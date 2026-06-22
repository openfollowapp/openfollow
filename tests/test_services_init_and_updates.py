# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for ``AppRuntimeServices`` init/update/shutdown seams.

This covers the thin delegators and constructor-wiring methods that sit
between ``OpenFollowApp.run()`` and the per-subsystem classes:

* ``init_camera`` / ``init_markers`` / ``init_psn`` / ``init_psn_receiver``
* ``init_otp`` / ``init_rttrpm`` – enabled + disabled paths, source-IP warning
* ``init_web_server`` / ``init_input_manager``
* ``update_video`` / ``apply_detection_pin`` / ``update_marker_visuals`` /
  ``update_controller_status`` – delegators to the ``runtime/`` helpers
* ``_init_overlay_state`` / ``_build_controller_status_text``
* ``shutdown`` – idempotent, per-subsystem start/stop coverage
* ``_setup_gc_tuning`` – GC threshold bump
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

import openfollow.services as services_module
from openfollow.configuration import (
    AppConfig,
    DetectionConfig,
    MarkerConfig,
    OtpOutputConfig,
    RttrpmOutputConfig,
)
from openfollow.psn.server import _UNCHANGED as _UNCHANGED_SENTINEL
from openfollow.runtime.services_marker_visuals import _resolve_marker_color
from openfollow.services import AppRuntimeServices

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _FakeMarker:
    def __init__(self, tid: int = 1) -> None:
        self.tid = tid
        self.pos_calls: list[tuple[float, float, float]] = []

    def set_pos(self, x: float, y: float, z: float) -> None:
        self.pos_calls.append((x, y, z))


class _FakePsnServer:
    def __init__(self, source_ip: str = "") -> None:
        self._markers: dict[int, _FakeMarker] = {}
        self.start_called = False
        self.stop_called = False
        # ``rebind_calls`` records every ``rebind`` invocation. Single calls
        # land as a bare string for backward compat; combined calls land as a
        # ``(source_ip, mcast_ip_str)`` tuple.
        self.rebind_calls: list = []
        self.rebind_mcast_ip_calls: list[str | None] = []
        self.system_name_updates: list[str] = []
        # Mirror ``PsnServer._source_ip`` so the orchestrator's
        # transactional-rebind capture has something to read.
        self._source_ip = source_ip
        # Mirror ``PsnServer._mcast_ip`` for the ``psn_mcast_ip``
        # transactional-rollback capture.
        self._mcast_ip: str | None = None

    def add_marker(self, tid: int, name: str) -> _FakeMarker:  # noqa: ARG002
        t = _FakeMarker(tid)
        self._markers[tid] = t
        return t

    def get_marker(self, tid: int) -> _FakeMarker | None:
        return self._markers.get(tid)

    def start(self) -> None:
        self.start_called = True

    def stop(self) -> None:
        self.stop_called = True

    def rebind(
        self,
        source_ip: str,
        *,
        mcast_ip: object = _UNCHANGED_SENTINEL,
    ) -> None:
        if mcast_ip is _UNCHANGED_SENTINEL:
            self.rebind_calls.append(source_ip)
        else:
            self.rebind_calls.append((source_ip, str(mcast_ip)))
            self._mcast_ip = mcast_ip  # type: ignore[assignment]
        self._source_ip = source_ip

    def rebind_mcast_ip(self, mcast_ip: str | None) -> None:
        self.rebind_mcast_ip_calls.append(mcast_ip)
        self._mcast_ip = mcast_ip

    def update_system_name(self, name: str) -> None:
        self.system_name_updates.append(name)


class _FakePsnServerFactory:
    """Captures constructor kwargs so we can assert on the wiring."""

    def __init__(self) -> None:
        self.instances: list[_FakePsnServer] = []
        self.last_kwargs: dict[str, Any] = {}

    def __call__(self, **kwargs: Any) -> _FakePsnServer:
        self.last_kwargs = kwargs
        inst = _FakePsnServer()
        self.instances.append(inst)
        return inst


class _FakeOtpServer:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.registered: list[_FakeMarker] = []
        self.started = False
        self.stopped = False
        self.restart_calls: list[dict[str, Any]] = []
        self.system_name_updates: list[str] = []
        # Mirror every ``OtpServer`` field the orchestrator inspects:
        # ``_socket`` (post-init bind guard), ``_transform_dest`` /
        # ``_advertisement_dest`` (used by the off→on bind-failure error
        # message), and the full kwargs set the transactional rollback
        # captures before retrying with the prior cfg. Defaults are
        # populated from kwargs so the prior cfg the orchestrator
        # captures matches the cfg the test constructed with.
        self._source_ip = kwargs.get("source_ip", "")
        self._system_name = kwargs.get("system_name", "OpenFollow")
        self._system_number = kwargs.get("system_number", 1)
        self._port = kwargs.get("port", 5568)
        self._priority = kwargs.get("priority", 100)
        self._transform_dest = f"239.159.1.{self._system_number}"
        self._advertisement_dest = "239.159.2.1"
        self._socket: object | None = object()

    def _is_multicast_mode(self) -> bool:
        # Mirrors ``OtpServer._is_multicast_mode``. Production callers
        # never pass ``mcast_ip`` (it's a test-only override); the fake
        # always behaves as the production multicast path.
        return True

    def register_marker(self, marker: _FakeMarker) -> None:
        self.registered.append(marker)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def restart(self, **kwargs: Any) -> None:
        self.restart_calls.append(kwargs)
        # Mirror the real ``OtpServer.restart`` field-update so a
        # subsequent rollback that calls ``restart`` again gets the
        # right "prior" snapshot.
        for name in (
            "system_name",
            "system_number",
            "port",
            "source_ip",
            "priority",
        ):
            if name in kwargs:
                setattr(self, f"_{name}", kwargs[name])

    def update_system_name(self, name: str) -> None:
        self.system_name_updates.append(name)
        self._system_name = name


class _FakeRttrpmServer:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.registered: list[_FakeMarker] = []
        self.started = False
        self.stopped = False
        self.restart_calls: list[dict[str, Any]] = []
        # Mirror ``RttrpmServer`` fields the orchestrator captures for
        # transactional rollback.
        self._host = kwargs.get("host", "127.0.0.1")
        self._port = kwargs.get("port", 24601)
        self._fps = float(kwargs.get("fps", 30))
        self._context = kwargs.get("context", 0)

    def register_marker(self, marker: _FakeMarker) -> None:
        self.registered.append(marker)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def restart(self, **kwargs: Any) -> None:
        self.restart_calls.append(kwargs)
        for name in ("host", "port", "fps", "context"):
            if name in kwargs:
                setattr(self, f"_{name}", kwargs[name])


class _FakePsnReceiver:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.rebind_calls: list[str] = []
        # Mirror the real ``PsnReceiver._source_ip`` so the
        # transactional-rebind orchestrator can read the prior IP for
        # rollback on partial failure.
        self._source_ip: str = kwargs.get("source_ip", "")

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def rebind(self, source_ip: str) -> None:
        self.rebind_calls.append(source_ip)
        self._source_ip = source_ip


class _FakeInputManager:
    def __init__(self, app: Any) -> None:
        from openfollow.input.events import InputEventBus

        self.app = app
        self.stopped = False
        # Mirror the real InputManager shape: event_bus is read by init_input_manager.
        self.event_bus = InputEventBus()

    def stop(self) -> None:
        self.stopped = True


class _FakeWebServer:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class _FakeVideoReceiver:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _FakeDetector:
    def __init__(self, *, available: bool = True) -> None:
        self.stopped = False
        self.reload_calls: list[Any] = []
        # ``swap_detector`` reads ``.available`` on the prior
        # detector to decide whether the existing pipeline has a
        # detection branch to drop on a rebuild.
        self.available = available

    def stop(self) -> None:
        self.stopped = True

    def reload_config(self, new_cfg: Any) -> None:
        self.reload_calls.append(new_cfg)


class _FakePool:
    def __init__(self) -> None:
        pass


class _FakeOverlayRenderer:
    def __init__(self) -> None:
        self.state = None


# --------------------------------------------------------------------------- #
# Construction helper
# --------------------------------------------------------------------------- #


def _dummy_app(cfg: AppConfig | None = None) -> SimpleNamespace:
    """Build a minimal app namespace for services tests."""
    cfg = cfg or AppConfig(psn_system_name="X")
    return SimpleNamespace(
        _config=cfg,
        _config_path="/tmp/openfollow-test.toml",
        _canvas=None,
        _camera=None,
        _server=None,
        _otp_server=None,
        _rttrpm_server=None,
        _psn_receiver=None,
        _web_server=None,
        _video_receiver=None,
        _input_manager=None,
        _controlled_ids=[],
        _viewer_ids=[],
        _selected_id=None,
        _web_commands=SimpleNamespace(),
        _log_ring=None,
    )


@pytest.fixture
def services(monkeypatch: pytest.MonkeyPatch) -> AppRuntimeServices:
    monkeypatch.setattr(services_module, "gst_runtime_available", lambda: True)
    monkeypatch.setattr(
        services_module.AppRuntimeServices,
        "_setup_gc_tuning",
        staticmethod(lambda: None),
    )
    monkeypatch.setattr(
        services_module.AppRuntimeServices,
        "_is_raspberry_pi",
        staticmethod(lambda: False),
    )
    return AppRuntimeServices(_dummy_app())


# --------------------------------------------------------------------------- #
# _init_overlay_state
# --------------------------------------------------------------------------- #


class TestInitOverlayState:
    def test_populates_overlay_renderer_state(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sentinel = object()
        monkeypatch.setattr(services_module, "build_initial_overlay_state", lambda cfg: sentinel)
        renderer = _FakeOverlayRenderer()
        services._init_overlay_state(renderer)
        assert renderer.state is sentinel


class TestSeedBundledDetectionModels:
    def test_seeds_models_into_resolved_storage(
        self, services: AppRuntimeServices, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # noqa: ANN001
        # When models are bundled, they are copied into ``<storage>/models``.
        source = tmp_path / "bundled"
        source.mkdir()
        (source / "yolo26n.onnx").write_bytes(b"nano")
        monkeypatch.setattr("openfollow.model_seed.bundled_models_dir", lambda: source)
        cfg = AppConfig()
        cfg.detection.storage_path = str(tmp_path / "store")

        services._seed_bundled_detection_models(cfg)

        assert (tmp_path / "store" / "models" / "yolo26n.onnx").read_bytes() == b"nano"


# --------------------------------------------------------------------------- #
# init_camera
# --------------------------------------------------------------------------- #


class TestInitCamera:
    def test_builds_camera_from_config(self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch) -> None:
        sentinel = object()
        monkeypatch.setattr(
            services_module.Camera,
            "from_config",
            classmethod(lambda cls, cfg: sentinel),
        )
        services.init_camera()
        assert services._app._camera is sentinel


# --------------------------------------------------------------------------- #
# init_markers
# --------------------------------------------------------------------------- #


class TestInitMarkers:
    def test_registers_controlled_markers_at_default_pos(self, services: AppRuntimeServices) -> None:
        cfg = AppConfig(psn_system_name="X")
        cfg = replace(
            cfg,
            controlled_marker_ids=[1, 2],
            viewer_marker_ids=[1, 2, 3],
            marker=MarkerConfig(
                default_pos_x=1.5,
                default_pos_y=2.5,
                default_pos_z=0.75,
            ),
        )
        services._app._config = cfg
        services._app._server = _FakePsnServer()

        services.init_markers()

        assert services._app._controlled_ids == [1, 2]
        assert services._app._viewer_ids == [1, 2, 3]
        assert services._app._selected_id == 1
        for tid in (1, 2):
            t = services._app._server.get_marker(tid)
            assert t is not None
            assert t.pos_calls == [(1.5, 2.5, 0.75)]

    def test_empty_controlled_list_leaves_selected_id_none(self, services: AppRuntimeServices) -> None:
        cfg = replace(services._app._config, controlled_marker_ids=[])
        services._app._config = cfg
        services._app._server = _FakePsnServer()
        services.init_markers()
        assert services._app._selected_id is None

    def test_filters_non_int_and_bool_marker_ids(self, services: AppRuntimeServices) -> None:
        """Two trap classes the ``init_markers`` defence guards:

        - ``bool`` is an ``int`` subclass so ``True >= 1`` passes a
          bare numeric guard, then crashes ``Marker.__init__``.
        - A non-int (string / float) raises ``TypeError`` on the
          ``>=`` comparison, crashing startup before any marker
          registers.

        Filter combines ``isinstance(tid, int)`` AND
        ``not isinstance(tid, bool)`` AND ``tid >= 1`` so a
        programmatically-constructed AppConfig (test fixture,
        in-place mutation that bypassed ``load_config``) can't smuggle
        either through to ``add_marker``."""
        cfg = replace(
            services._app._config,
            controlled_marker_ids=[True, "spot", 1.5, 1],  # type: ignore[list-item]
            viewer_marker_ids=[False, True, None, 2],  # type: ignore[list-item]
        )
        services._app._config = cfg
        services._app._server = _FakePsnServer()

        services.init_markers()

        # Bools / strings / floats / None all dropped; real ints kept.
        assert services._app._controlled_ids == [1]
        assert services._app._viewer_ids == [2]

    def test_dedupes_marker_ids(self, services: AppRuntimeServices) -> None:
        cfg = replace(
            services._app._config,
            controlled_marker_ids=[1, 2, 1, 3, 2],
            viewer_marker_ids=[2, 2, 4, 2],
        )
        services._app._config = cfg
        services._app._server = _FakePsnServer()

        services.init_markers()

        assert services._app._controlled_ids == [1, 2, 3]
        assert services._app._viewer_ids == [2, 4]


# --------------------------------------------------------------------------- #
# init_psn
# --------------------------------------------------------------------------- #


class TestInitPsn:
    def test_binds_to_resolved_iface_ip(self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch) -> None:
        """``init_psn`` binds to the IP held by the pinned ``psn_source_iface``,
        resolved through :func:`net_utils.resolve_source_ip`."""
        cfg = replace(
            services._app._config,
            psn_source_iface="eth0",
            psn_mcast_ip="236.10.10.10",
        )
        services._app._config = cfg

        import socket as _socket
        from types import SimpleNamespace

        from openfollow import net_utils

        monkeypatch.setattr(
            net_utils.psutil,
            "net_if_addrs",
            lambda: {
                "eth0": [SimpleNamespace(family=_socket.AF_INET, address="10.0.0.1")],
            },
        )

        factory = _FakePsnServerFactory()
        monkeypatch.setattr(services_module, "PsnServer", factory)

        services.init_psn()
        assert factory.instances[0].start_called is True
        assert factory.last_kwargs["source_ip"] == "10.0.0.1"

    def test_stale_iface_falls_back_to_primary(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the pinned iface is down/missing, init_psn falls back to the auto-detected primary."""
        cfg = replace(services._app._config, psn_source_iface="ghost0")
        services._app._config = cfg

        from openfollow import net_utils

        monkeypatch.setattr(
            net_utils.psutil,
            "net_if_addrs",
            lambda: {},
        )
        monkeypatch.setattr(
            net_utils,
            "get_primary_local_ipv4",
            lambda default="": "10.0.0.1",
        )

        factory = _FakePsnServerFactory()
        monkeypatch.setattr(services_module, "PsnServer", factory)

        services.init_psn()
        assert factory.last_kwargs["source_ip"] == "10.0.0.1"

    def _stub_primary_ip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import socket as _socket

        from openfollow import net_utils

        monkeypatch.setattr(
            net_utils.psutil,
            "net_if_addrs",
            lambda: {"eth0": [SimpleNamespace(family=_socket.AF_INET, address="10.0.0.1")]},
        )

    def test_start_failure_leaves_server_none_and_stops(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._stub_primary_ip(monkeypatch)
        stopped: list[bool] = []

        class _BadServer:
            def __init__(self, **kwargs: Any) -> None: ...

            def start(self) -> None:
                raise RuntimeError("bind failed")

            def stop(self) -> None:
                stopped.append(True)

        monkeypatch.setattr(services_module, "PsnServer", _BadServer)
        with pytest.raises(RuntimeError, match="bind failed"):
            services.init_psn()
        assert services._app._server is None
        assert stopped == [True]

    def test_start_failure_logs_when_stop_also_raises(
        self,
        services: AppRuntimeServices,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        self._stub_primary_ip(monkeypatch)

        class _BadServer:
            def __init__(self, **kwargs: Any) -> None: ...

            def start(self) -> None:
                raise RuntimeError("bind failed")

            def stop(self) -> None:
                raise RuntimeError("stop failed")

        monkeypatch.setattr(services_module, "PsnServer", _BadServer)
        with caplog.at_level("ERROR"), pytest.raises(RuntimeError, match="bind failed"):
            services.init_psn()
        assert services._app._server is None
        assert any("stop after failed start" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# init_otp
# --------------------------------------------------------------------------- #


class TestInitOtp:
    @pytest.fixture(autouse=True)
    def _stub_resolve_source_ip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Keep the OTP iface→IP resolution hermetic (no psutil / host IP).

        Default: empty pin → OS default. Tests that pin an interface override
        this with the status they need.
        """
        from openfollow import net_utils

        monkeypatch.setattr(
            net_utils,
            "resolve_source_ip",
            lambda iface, *, fallback=True: ("", "none"),
        )

    def test_disabled_is_no_op(self, services: AppRuntimeServices) -> None:
        # Default config has otp_output.enabled=False.
        services.init_otp()
        assert services._app._otp_server is None

    def test_enabled_constructs_server_and_registers_markers(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = replace(
            services._app._config,
            otp_output=OtpOutputConfig(
                enabled=True,
                system_number=1,
                port=5568,
                source_iface="",
                priority=100,
            ),
        )
        services._app._config = cfg
        server = _FakePsnServer()
        server.add_marker(5, "T5")
        services._app._server = server
        services._app._controlled_ids = [5]

        monkeypatch.setattr(services_module, "OtpServer", _FakeOtpServer)

        services.init_otp()
        otp = services._app._otp_server
        assert otp.started is True
        assert otp.registered == [server.get_marker(5)]

    def test_enabled_source_iface_available_binds_resolved_ip_no_warning(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        cfg = replace(
            services._app._config,
            otp_output=OtpOutputConfig(
                enabled=True,
                system_number=1,
                port=5568,
                source_iface="eth0",
                priority=100,
            ),
        )
        services._app._config = cfg
        services._app._server = _FakePsnServer()
        services._app._controlled_ids = []

        from openfollow import net_utils

        # Pinned iface is live → resolves to its own IP, status "iface".
        monkeypatch.setattr(net_utils, "resolve_source_ip", lambda iface, *, fallback=True: ("192.168.1.5", "iface"))
        monkeypatch.setattr(services_module, "OtpServer", _FakeOtpServer)

        with caplog.at_level("WARNING"):
            services.init_otp()
        # The resolved IP reaches the server, and a live pin warns about nothing.
        assert services._app._otp_server._source_ip == "192.168.1.5"
        assert not any("source_iface" in r.message for r in caplog.records)

    def test_enabled_with_unregistered_controlled_marker_skips(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The per-tid lookup falls through when a controlled marker isn't
        registered on the PSN server yet (defensive path)."""
        cfg = replace(
            services._app._config,
            otp_output=OtpOutputConfig(
                enabled=True,
                system_number=1,
                port=5568,
                source_iface="",
                priority=100,
            ),
        )
        services._app._config = cfg
        services._app._server = _FakePsnServer()
        # id=99 is not registered on the server → register_marker not called.
        services._app._controlled_ids = [99]

        monkeypatch.setattr(services_module, "OtpServer", _FakeOtpServer)
        services.init_otp()
        assert services._app._otp_server.registered == []

    def test_enabled_source_iface_unavailable_falls_back_and_warns(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        cfg = replace(
            services._app._config,
            otp_output=OtpOutputConfig(
                enabled=True,
                system_number=1,
                port=5568,
                source_iface="eth9_gone",
                priority=100,
            ),
        )
        services._app._config = cfg
        services._app._server = _FakePsnServer()
        services._app._controlled_ids = []

        from openfollow import net_utils

        # Pinned iface is down → falls back to the primary, status "primary".
        monkeypatch.setattr(net_utils, "resolve_source_ip", lambda iface, *, fallback=True: ("192.168.1.9", "primary"))
        monkeypatch.setattr(services_module, "OtpServer", _FakeOtpServer)

        with caplog.at_level("WARNING"):
            services.init_otp()
        # Output stays alive on the fallback IP, with a warning about the dead pin.
        assert services._app._otp_server._source_ip == "192.168.1.9"
        assert any("otp_output.source_iface" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# init_rttrpm
# --------------------------------------------------------------------------- #


class TestInitRttrpm:
    def test_disabled_is_no_op(self, services: AppRuntimeServices) -> None:
        services.init_rttrpm()
        assert services._app._rttrpm_server is None

    def test_enabled_constructs_server_and_registers_markers(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = replace(
            services._app._config,
            rttrpm_output=RttrpmOutputConfig(
                enabled=True,
                host="127.0.0.1",
                port=24601,
                fps=30,
                context=42,
            ),
        )
        services._app._config = cfg
        server = _FakePsnServer()
        server.add_marker(3, "T3")
        services._app._server = server
        services._app._controlled_ids = [3]

        monkeypatch.setattr(services_module, "RttrpmServer", _FakeRttrpmServer)
        services.init_rttrpm()
        rt = services._app._rttrpm_server
        assert rt.started is True
        assert rt.registered == [server.get_marker(3)]

    def test_enabled_unregistered_controlled_marker_skips(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = replace(
            services._app._config,
            rttrpm_output=RttrpmOutputConfig(
                enabled=True,
                host="127.0.0.1",
                port=24601,
                fps=30,
                context=42,
            ),
        )
        services._app._config = cfg
        services._app._server = _FakePsnServer()
        services._app._controlled_ids = [77]  # not registered

        monkeypatch.setattr(services_module, "RttrpmServer", _FakeRttrpmServer)
        services.init_rttrpm()
        assert services._app._rttrpm_server.registered == []


# --------------------------------------------------------------------------- #
# init_psn_receiver
# --------------------------------------------------------------------------- #


class TestInitPsnReceiver:
    def test_wires_ignore_ids_from_controlled(
        self,
        services: AppRuntimeServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The receiver binds to the same resolved IP the server uses, via
        ``_resolved_source_ip``. Mock psutil for a stable IPv4 address."""
        import socket as _socket
        from types import SimpleNamespace

        from openfollow import net_utils

        monkeypatch.setattr(
            net_utils.psutil,
            "net_if_addrs",
            lambda: {
                "eth0": [SimpleNamespace(family=_socket.AF_INET, address="10.0.0.1")],
            },
        )
        services._app._controlled_ids = [1, 2, 7]
        services._app._config = replace(
            services._app._config,
            psn_source_iface="eth0",
        )
        monkeypatch.setattr(services_module, "PsnReceiver", _FakePsnReceiver)
        services.init_psn_receiver()
        recv = services._app._psn_receiver
        assert recv.started is True
        assert recv.kwargs["ignore_ids"] == [1, 2, 7]
        assert recv.kwargs["source_ip"] == "10.0.0.1"


class TestResolveWebBind:
    def test_explicit_web_bind_wins(self, services: AppRuntimeServices) -> None:
        services._app._config = replace(services._app._config, web_bind="192.168.5.5", psn_source_iface="eth0")
        assert services._resolve_web_bind() == "192.168.5.5"

    def test_auto_pins_to_psn_iface_ip(self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch) -> None:
        import socket as _socket
        from types import SimpleNamespace

        from openfollow import net_utils

        monkeypatch.setattr(
            net_utils.psutil,
            "net_if_addrs",
            lambda: {"eth0": [SimpleNamespace(family=_socket.AF_INET, address="10.0.0.7")]},
        )
        services._app._config = replace(services._app._config, web_bind="", psn_source_iface="eth0")
        assert services._resolve_web_bind() == "10.0.0.7"

    def test_auto_falls_back_to_all_interfaces_without_iface(self, services: AppRuntimeServices) -> None:
        services._app._config = replace(services._app._config, web_bind="", psn_source_iface="")
        assert services._resolve_web_bind() == "0.0.0.0"

    def test_auto_falls_back_when_iface_has_no_ip(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openfollow import net_utils

        monkeypatch.setattr(net_utils.psutil, "net_if_addrs", lambda: {})  # iface absent
        services._app._config = replace(services._app._config, web_bind="", psn_source_iface="eth0")
        assert services._resolve_web_bind() == "0.0.0.0"


# --------------------------------------------------------------------------- #
# init_input_manager
# --------------------------------------------------------------------------- #


class TestInitInputManager:
    def test_instantiates_input_manager(self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services_module, "InputManager", _FakeInputManager)
        services.init_input_manager()
        assert isinstance(services._app._input_manager, _FakeInputManager)

    def test_attaches_event_bus_when_transmitter_manager_exists(
        self,
        services: AppRuntimeServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``init_input_manager`` wires the transmitter manager's Hotkey /
        ControllerButton dispatch to the InputManager's event bus."""
        monkeypatch.setattr(services_module, "InputManager", _FakeInputManager)
        attached: list[object] = []

        class _StubTransmitterManager:
            def attach_event_bus(self, bus: object) -> None:
                attached.append(bus)

        stub = _StubTransmitterManager()
        services._osc_transmitter_manager = stub  # type: ignore[assignment]
        services.init_input_manager()
        assert len(attached) == 1
        assert attached[0] is services._app._input_manager.event_bus

    def test_skips_attach_when_transmitter_manager_is_none(
        self,
        services: AppRuntimeServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If ``init_osc_transmitters`` was skipped or failed, the
        bus simply isn't wired – the rest of the input system still
        works. No exception."""
        monkeypatch.setattr(services_module, "InputManager", _FakeInputManager)
        services._osc_transmitter_manager = None
        services.init_input_manager()  # must not raise
        assert services._app._input_manager is not None


# --------------------------------------------------------------------------- #
# init_web_server
# --------------------------------------------------------------------------- #


class TestInitWebServer:
    def test_constructs_and_starts_web_server(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openfollow import web

        monkeypatch.setattr(web, "ConfigWebServer", _FakeWebServer)
        services._preview_provider = SimpleNamespace(get_snapshot=lambda: None)
        services._snapshot_provider = SimpleNamespace(get_snapshot=lambda: None)

        services.init_web_server()
        srv = services._app._web_server
        assert srv.started is True
        assert srv.kwargs["port"] == services._app._config.web_port
        # Snapshot provider hooks wired through.  Bound-method identity
        # isn't stable across attribute lookups, so compare by __func__.
        assert srv.kwargs["runtime_stats_provider"].__func__ is AppRuntimeServices.get_runtime_stats_snapshot

    def test_wires_osc_binding_diagnostics_providers(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openfollow import web

        monkeypatch.setattr(web, "ConfigWebServer", _FakeWebServer)
        services._preview_provider = SimpleNamespace(get_snapshot=lambda: None)
        services._snapshot_provider = SimpleNamespace(get_snapshot=lambda: None)

        services.init_web_server()
        kwargs = services._app._web_server.kwargs
        for name in (
            "osc_binding_status_provider",
            "osc_binding_preview_provider",
            "osc_binding_test_send",
            "osc_binding_conflicts_provider",
        ):
            assert name in kwargs, f"missing provider kwarg: {name}"

        # Round-trip the four hooks against a None manager to confirm
        # they degrade gracefully – the provider closures dereference
        # ``self._osc_transmitter_manager`` lazily, so a call before
        # ``init_osc_transmitters`` runs must return the
        # "manager-not-attached" sentinel rather than raise.
        services._osc_transmitter_manager = None
        assert kwargs["osc_binding_status_provider"]("any-id") is None
        assert kwargs["osc_binding_preview_provider"]("any-id") is None
        assert kwargs["osc_binding_test_send"]("any-id") == {}
        # Conflict probe stays functional even with no manager – it
        # reads the registry directly. The default registry has
        # ``system:movement`` claims for the reserved keys.
        from openfollow.configuration import RESERVED_MOVEMENT_KEYS

        any_movement_key = next(iter(RESERVED_MOVEMENT_KEYS))
        owners = kwargs["osc_binding_conflicts_provider"](
            "key",
            any_movement_key,
            "osc:hotkey",
        )
        assert "system:movement" in owners
        # Same probe under the same owner returns no conflict.
        assert (
            kwargs["osc_binding_conflicts_provider"](
                "key",
                any_movement_key,
                "system:movement",
            )
            == []
        )

    def test_wires_diagnostics_io_providers(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openfollow import web

        monkeypatch.setattr(web, "ConfigWebServer", _FakeWebServer)
        services._preview_provider = SimpleNamespace(get_snapshot=lambda: None)
        services._snapshot_provider = SimpleNamespace(get_snapshot=lambda: None)

        services.init_web_server()
        kwargs = services._app._web_server.kwargs
        for name in (
            "recent_osc_sends_provider",
            "recent_midi_events_provider",
            "midi_port_names_provider",
            "camera_names_provider",
        ):
            assert name in kwargs, f"missing provider kwarg: {name}"

        services._osc_transmitter_manager = None
        assert kwargs["recent_osc_sends_provider"]() == []
        assert kwargs["recent_midi_events_provider"]() == []
        assert kwargs["midi_port_names_provider"]() == []
        assert isinstance(kwargs["camera_names_provider"](), list)

    def test_status_provider_delegates_to_attached_manager(
        self,
        services: AppRuntimeServices,
    ) -> None:
        """When a manager is attached, the provider returns whatever
        ``manager.status_for`` does. Use a stand-in manager so the
        test doesn't depend on the live scheduler."""
        captured: list[str] = []

        class _StubManager:
            def status_for(self, row_id: str) -> dict:
                captured.append(row_id)
                return {"pps": 1.0}

            def preview_for(self, row_id: str) -> dict:
                return {"address": f"/r/{row_id}"}

            def test_send(self, row_id: str) -> dict:
                return {"sent": True, "address": f"/r/{row_id}"}

        services._osc_transmitter_manager = _StubManager()  # type: ignore[assignment]
        assert services._osc_binding_status_provider("row-x") == {"pps": 1.0}
        assert captured == ["row-x"]
        assert services._osc_binding_preview_provider("row-x") == {
            "address": "/r/row-x",
        }
        assert services._osc_binding_test_send("row-x") == {
            "sent": True,
            "address": "/r/row-x",
        }

    def test_test_send_collapses_none_to_empty_dict(
        self,
        services: AppRuntimeServices,
    ) -> None:
        """``test_send`` returns ``None`` when the manager doesn't know
        the row. The provider collapses that to ``{}`` so the route
        layer's ``if not result`` ``available=False`` path fires."""

        class _StubManager:
            def test_send(self, row_id: str) -> None:
                return None

        services._osc_transmitter_manager = _StubManager()  # type: ignore[assignment]
        assert services._osc_binding_test_send("row-x") == {}


# --------------------------------------------------------------------------- #
# Update delegators
# --------------------------------------------------------------------------- #


class TestUpdateDelegators:
    def test_update_video_forwards_to_helper(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple] = []
        monkeypatch.setattr(
            services_module,
            "update_video_helper",
            lambda app, logger: calls.append((app, logger)),
        )
        services.update_video()
        assert len(calls) == 1
        assert calls[0][0] is services._app

    def test_apply_detection_pin_forwards_to_helper(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple] = []
        monkeypatch.setattr(
            services_module,
            "apply_detection_pin_helper",
            lambda app, **kw: calls.append((app, kw)),
        )
        services.apply_detection_pin(dt=0.5)
        assert calls[0][0] is services._app
        kwargs = calls[0][1]
        # Per-marker state lives on the app now – the wrapper must NOT pass a
        # ``pin_state`` (the kwarg was removed when state moved to
        # ``app._detection_pin_states``).
        assert "pin_state" not in kwargs
        assert kwargs["person_detector"] is services._person_detector
        assert kwargs["unproject_cam_buffer"] is services._unproject_cam_buffer
        assert kwargs["screen_point_buffer"] is services._screen_point_buffer
        assert kwargs["dt"] == 0.5

    # Note: ``update_controller_status`` and ``services_controller_status``
    # were removed when the binding moved to the marker cards.

    def test_update_marker_visuals_swaps_overlay_state(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built = object()
        swapped_old: list[object] = []

        monkeypatch.setattr(
            services_module,
            "build_marker_visual_state",
            lambda app, **kw: built,
        )
        monkeypatch.setattr(
            services_module,
            "prepare_overlay_state_swap",
            lambda **kw: swapped_old.append(kw["new_overlay_state"]),
        )
        renderer = _FakeOverlayRenderer()
        services._overlay_renderer = renderer

        services.update_marker_visuals()
        assert renderer.state is built
        assert swapped_old[0] is built


# Note: Per-marker controller binding data flows through
# ``InputManager.get_controller_info`` into ``MarkerOverlayData``.

# --------------------------------------------------------------------------- #
# shutdown
# --------------------------------------------------------------------------- #


class _FakeTransmitterManager:
    """Stand-in for ``OscTransmitterManager`` in shutdown tests."""

    def __init__(self, stop_returns: bool = True) -> None:
        self.stopped = False
        self._stop_returns = stop_returns

    def stop(self) -> bool:
        self.stopped = True
        return self._stop_returns


class TestInitMidi:
    def test_calls_apply_config_with_configured_patches(
        self,
        services: AppRuntimeServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.configuration import MidiConfig, MidiPatch

        services._app._config = replace(
            services._app._config,
            midi=MidiConfig(
                patches=[
                    MidiPatch(id=1, alias="Workspace 1", port_name="MIDI Mix"),
                ]
            ),
        )
        captured: list[list[Any]] = []
        monkeypatch.setattr(
            services._midi,
            "apply_config",
            lambda patches: captured.append(list(patches)),
        )
        services.init_midi()
        assert len(captured) == 1
        assert captured[0][0].id == 1
        assert captured[0][0].alias == "Workspace 1"


class TestInitVirtualFaders:
    def test_calls_apply_config_with_persisted_fader_layout(
        self,
        services: AppRuntimeServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.configuration import (
            VirtualFaderConfig,
            VirtualFadersConfig,
        )

        new_faders = VirtualFadersConfig(
            faders=[
                VirtualFaderConfig(name="Master", default_value=0.5),
            ]
        )
        services._app._config = replace(
            services._app._config,
            virtual_faders=new_faders,
        )
        captured: list[VirtualFadersConfig] = []
        monkeypatch.setattr(
            services._virtual_faders,
            "apply_config",
            lambda cfg: captured.append(cfg),
        )
        services.init_virtual_faders()
        assert len(captured) == 1
        assert captured[0].faders[0].name == "Master"


class TestShutdown:
    def test_happy_path_stops_every_subsystem(self, services: AppRuntimeServices) -> None:
        app = services._app
        app._input_manager = _FakeInputManager(app)
        app._web_server = _FakeWebServer()
        app._video_receiver = _FakeVideoReceiver()
        app._otp_server = _FakeOtpServer()
        app._rttrpm_server = _FakeRttrpmServer()
        app._server = _FakePsnServer()
        app._psn_receiver = _FakePsnReceiver()
        services._person_detector = _FakeDetector()
        # Shutdown must stop the transmitter before draining the OSC service.
        transmitter = _FakeTransmitterManager()
        services._osc_transmitter_manager = transmitter  # type: ignore[assignment]

        services.shutdown()

        assert services._person_detector.stopped is True
        assert app._input_manager.stopped is True
        assert app._web_server.stopped is True
        assert app._video_receiver.stopped is True
        assert app._otp_server.stopped is True
        assert app._rttrpm_server.stopped is True
        assert app._server.stop_called is True
        assert app._psn_receiver.stopped is True
        assert transmitter.stopped is True
        assert services._osc_transmitter_manager is None

    def test_none_subsystems_are_skipped_cleanly(self, services: AppRuntimeServices) -> None:
        # All subsystems start as None – shutdown must not raise.
        services.shutdown()

    def test_is_idempotent(self, services: AppRuntimeServices) -> None:
        app = services._app
        app._server = _FakePsnServer()
        services.shutdown()
        first_stop = app._server.stop_called
        # Second call must not double-stop (idempotency via the flag).
        app._server.stop_called = False
        services.shutdown()
        assert first_stop is True
        assert app._server.stop_called is False

    def test_closes_midi_subsystem(
        self,
        services: AppRuntimeServices,
    ) -> None:
        """Shutdown drains the MIDI subsystem last to ensure all
        subsystems are stopped before returning."""
        closed: list[bool] = []

        def _record_close() -> None:
            closed.append(True)

        services._midi.shutdown = _record_close  # type: ignore[method-assign]
        services.shutdown()
        assert closed == [True]

    def test_stops_marker_catalog_sync_when_present(self, services: AppRuntimeServices) -> None:
        app = services._app

        class _FakeSync:
            def __init__(self) -> None:
                self.stopped = False

            def stop(self) -> None:
                self.stopped = True

        sync = _FakeSync()
        app._marker_catalog_sync = sync  # type: ignore[attr-defined]

        services.shutdown()

        assert sync.stopped is True

    def test_warns_and_keeps_reference_when_transmitter_stop_times_out(
        self,
        services: AppRuntimeServices,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        transmitter = _FakeTransmitterManager(stop_returns=False)
        services._osc_transmitter_manager = transmitter  # type: ignore[assignment]

        with caplog.at_level("WARNING", logger="openfollow.services"):
            services.shutdown()

        assert transmitter.stopped is True
        # Reference preserved – the wedged thread stays observable.
        assert services._osc_transmitter_manager is transmitter
        assert any("did not stop in time" in r.message for r in caplog.records)

    def test_one_failing_stop_does_not_strand_later_teardowns(
        self,
        services: AppRuntimeServices,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A raising ``stop()`` is logged and isolated so the later
        teardowns – the OSC-service drain and MIDI close – still run.
        Without per-subsystem isolation they'd be skipped, leaking
        sockets / multicast groups / rtmidi ports into an ``os.execv``
        re-exec."""
        app = services._app

        class _RaisingReceiver:
            def stop(self) -> None:
                raise RuntimeError("teardown boom")

        app._video_receiver = _RaisingReceiver()  # type: ignore[assignment]
        transmitter = _FakeTransmitterManager()
        services._osc_transmitter_manager = transmitter  # type: ignore[assignment]
        drained: list[str] = []
        services._osc_service.shutdown = lambda: drained.append("osc")  # type: ignore[method-assign]
        services._midi.shutdown = lambda: drained.append("midi")  # type: ignore[method-assign]

        with caplog.at_level("ERROR", logger="openfollow.services"):
            services.shutdown()

        # The OSC service drained and MIDI closed despite the earlier raise,
        # and the transmitter (sequenced after the failing receiver) stopped.
        assert drained == ["osc", "midi"]
        assert transmitter.stopped is True
        assert any("Error stopping video receiver" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# _is_raspberry_pi
# --------------------------------------------------------------------------- #


class TestIsRaspberryPi:
    def test_returns_true_when_device_tree_contains_pi(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from pathlib import Path

        def _fake_read_text(
            self: Path,
            encoding: str = "utf-8",
            errors: str = "strict",  # noqa: ARG001
        ) -> str:
            if str(self) == "/proc/device-tree/model":
                return "Raspberry Pi 4 Model B\x00"
            raise OSError("not this path")

        monkeypatch.setattr(Path, "read_text", _fake_read_text)
        assert services_module.AppRuntimeServices._is_raspberry_pi() is True

    def test_returns_false_when_no_probe_path_mentions_pi(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from pathlib import Path

        def _fake_read_text(
            self: Path,
            encoding: str = "utf-8",
            errors: str = "strict",  # noqa: ARG001
        ) -> str:
            raise OSError("not here")

        monkeypatch.setattr(Path, "read_text", _fake_read_text)
        assert services_module.AppRuntimeServices._is_raspberry_pi() is False

    def test_continues_when_readable_path_does_not_mention_pi(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cover branch 228->215: path is readable but not a Raspberry Pi.

        Exercises the False side of ``if "raspberry pi" in text`` so the
        loop continues to the next candidate path instead of returning
        immediately. Ensures non-Pi hardware with a populated
        device-tree model (e.g. BeagleBone) doesn't short-circuit to True.
        """
        from pathlib import Path

        def _fake_read_text(
            self: Path,
            encoding: str = "utf-8",
            errors: str = "strict",  # noqa: ARG001
        ) -> str:
            if str(self) == "/proc/device-tree/model":
                return "BeagleBone Black\x00"
            if str(self) == "/sys/firmware/devicetree/base/model":
                return "BeagleBone Black\x00"
            raise OSError("not this path")

        monkeypatch.setattr(Path, "read_text", _fake_read_text)
        assert services_module.AppRuntimeServices._is_raspberry_pi() is False

    def test_first_path_oserror_second_path_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cover the mixed path: first probe fails, later probe wins.

        The three probe paths (``/proc/device-tree/model``,
        ``/sys/firmware/devicetree/base/model``, ``/proc/cpuinfo``) are
        tried in order; a later path succeeding after an earlier
        ``OSError`` is the realistic case on older Pi kernels where the
        device-tree node is missing but ``/proc/cpuinfo`` carries the
        model string.
        """
        from pathlib import Path

        def _fake_read_text(
            self: Path,
            encoding: str = "utf-8",
            errors: str = "strict",  # noqa: ARG001
        ) -> str:
            if str(self) == "/proc/device-tree/model":
                raise OSError("device-tree node not present")
            if str(self) == "/sys/firmware/devicetree/base/model":
                return "Raspberry Pi 3 Model B Rev 1.2\x00"
            raise OSError("never reached")

        monkeypatch.setattr(Path, "read_text", _fake_read_text)
        assert services_module.AppRuntimeServices._is_raspberry_pi() is True


# --------------------------------------------------------------------------- #
# _setup_gc_tuning – unpatched to cover the real function
# --------------------------------------------------------------------------- #


class TestSetupGcTuning:
    def test_raises_no_exception_and_sets_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Record the forwarded args without touching real process-wide GC
        # thresholds – mutating them here would leak across the rest of the
        # test session and make ordering-sensitive flakes show up downstream.
        import gc

        calls: list[tuple] = []
        monkeypatch.setattr(gc, "set_threshold", lambda *args: calls.append(args) or None)

        services_module.AppRuntimeServices._setup_gc_tuning()
        assert calls[0] == (2000, 15, 15)


# --------------------------------------------------------------------------- #
# WebCommandQueue – detached update state
# --------------------------------------------------------------------------- #


class TestDetachedUpdateState:
    """The detached installer publishes progress to a status file that
    ``get_update_status`` surfaces over the frozen in-memory status."""

    def _point_at(self, monkeypatch: pytest.MonkeyPatch, path: Any) -> None:
        monkeypatch.setattr(services_module, "_DETACHED_UPDATE_STATE_FILE", str(path))

    def test_file_status_overrides_in_memory(self, monkeypatch, tmp_path) -> None:
        self._point_at(monkeypatch, tmp_path / "u.json")
        q = services_module.WebCommandQueue()
        q.set_update_status("restarting", message="frozen")
        (tmp_path / "u.json").write_text('{"state":"failed","message":"boom","error":"E: held"}')
        st = q.get_update_status()
        assert st["state"] == "failed"
        assert st["error"] == "E: held"

    def test_falls_back_to_memory_when_no_file(self, monkeypatch, tmp_path) -> None:
        self._point_at(monkeypatch, tmp_path / "missing.json")
        q = services_module.WebCommandQueue()
        q.set_update_status("running", message="installing")
        assert q.get_update_status()["state"] == "running"

    def test_restarting_without_file_uses_memory(self, monkeypatch, tmp_path) -> None:
        # In-memory ``restarting`` but no detached file yet → in-memory wins.
        self._point_at(monkeypatch, tmp_path / "missing.json")
        q = services_module.WebCommandQueue()
        q.set_update_status("restarting", message="installing")
        assert q.get_update_status()["state"] == "restarting"

    def test_stale_file_ignored_when_not_restarting(self, monkeypatch, tmp_path) -> None:
        # A leftover terminal file must not shadow a fresh non-restarting status.
        p = tmp_path / "u.json"
        self._point_at(monkeypatch, p)
        q = services_module.WebCommandQueue()
        p.write_text('{"state":"failed","message":"old","error":"stale"}')
        q.set_update_status("running", message="fresh")
        assert q.get_update_status()["state"] == "running"

    def test_read_ignores_invalid_or_shapeless(self, monkeypatch, tmp_path) -> None:
        p = tmp_path / "u.json"
        self._point_at(monkeypatch, p)
        p.write_text("not json")
        assert services_module._read_detached_update_state() is None
        p.write_text('["a","list"]')
        assert services_module._read_detached_update_state() is None
        p.write_text('{"message":"no state key"}')
        assert services_module._read_detached_update_state() is None

    def test_clear_removes_file_and_tolerates_absence(self, monkeypatch, tmp_path) -> None:
        p = tmp_path / "u.json"
        self._point_at(monkeypatch, p)
        p.write_text('{"state":"failed"}')
        services_module.clear_detached_update_state()
        assert not p.exists()
        services_module.clear_detached_update_state()  # no raise when already gone

    def test_queueing_an_update_clears_stale_file(self, monkeypatch, tmp_path) -> None:
        p = tmp_path / "u.json"
        self._point_at(monkeypatch, p)
        q = services_module.WebCommandQueue()
        p.write_text('{"state":"failed","message":"old"}')
        assert q.request_local_update("openfollow", deb_path="/tmp/openfollow-update-x.deb") is True
        assert not p.exists()

    def test_queued_update_unshadows_restarting_status(self, monkeypatch, tmp_path) -> None:
        # Clearing the stale detached file on queue means a live
        # ``restarting`` status is no longer shadowed by a prior run's
        # terminal result in ``get_update_status``.
        p = tmp_path / "u.json"
        self._point_at(monkeypatch, p)
        q = services_module.WebCommandQueue()
        p.write_text('{"state":"failed","message":"old deb run","error":"boom"}')
        assert q.request_deb_update("openfollow") is True
        assert not p.exists()
        q.set_update_status("restarting", message="deb restart")
        assert q.get_update_status() == {
            "state": "restarting",
            "message": "deb restart",
            "error": "",
        }


class TestConsumeUpdateRequestedEmpty:
    def test_flag_set_but_payload_missing_returns_none(self) -> None:
        """Defensive: the event flag being set while ``_update_request`` is
        None means the flag was cleared concurrently.  ``consume_update_requested``
        must not return an empty dict – it must return None so the caller
        treats it as "no pending request".
        """
        q = services_module.WebCommandQueue()
        q._update_requested.set()
        q._update_request = None
        assert q.consume_update_requested() is None


# --------------------------------------------------------------------------- #
# WebCommandQueue – .deb update request
# --------------------------------------------------------------------------- #


class TestRequestDebUpdate:
    """request_deb_update queues a deb-kind update and respects the
    in-progress guard."""

    def test_queues_and_sets_status(self) -> None:
        q = services_module.WebCommandQueue()
        accepted = q.request_deb_update(service_name="openfollow")
        assert accepted is True
        assert q.get_update_status()["state"] == "queued"

    def test_stored_request_has_deb_kind(self) -> None:
        q = services_module.WebCommandQueue()
        q.request_deb_update(service_name="openfollow")
        payload = q.consume_update_requested()
        assert payload is not None
        assert payload["kind"] == "deb"
        assert payload["service_name"] == "openfollow"

    def test_falls_back_to_openfollow_when_service_name_empty(self) -> None:
        q = services_module.WebCommandQueue()
        q.request_deb_update(service_name="")
        payload = q.consume_update_requested()
        assert payload is not None
        assert payload["service_name"] == "openfollow"

    def test_rejected_while_running(self) -> None:
        q = services_module.WebCommandQueue()
        q.set_update_status("running", message="...")
        assert q.request_deb_update(service_name="openfollow") is False

    def test_rejected_while_queued(self) -> None:
        q = services_module.WebCommandQueue()
        q.request_deb_update(service_name="openfollow")
        # Second request must be refused.
        assert q.request_deb_update(service_name="openfollow") is False

    def test_rejected_while_restarting(self) -> None:
        q = services_module.WebCommandQueue()
        q.set_update_status("restarting", message="...")
        assert q.request_deb_update(service_name="openfollow") is False

    def test_accepted_after_idle(self) -> None:
        q = services_module.WebCommandQueue()
        q.set_update_status("idle")
        assert q.request_deb_update(service_name="openfollow") is True


# --------------------------------------------------------------------------- #
# WebCommandQueue – detection install/uninstall job state
# --------------------------------------------------------------------------- #


class TestDetectionInstallStatus:
    """The detection install/uninstall workflow lives on the queue
    so the polling endpoint can read it without coupling the route
    to a route-local lock. These tests pin the contract the routes
    + template depend on."""

    def test_initial_state_is_idle(self) -> None:
        q = services_module.WebCommandQueue()
        snap = q.get_detection_install_status()
        assert snap == {
            "state": "idle",
            "extra": "",
            "action": "",
            "message": "",
            "tail": "",
        }

    def test_try_claim_transitions_to_running(self) -> None:
        q = services_module.WebCommandQueue()
        ok = q.try_claim_detection_install(
            action="install",
            extra="detection",
            message="Installing...",
        )
        assert ok is True
        snap = q.get_detection_install_status()
        assert snap["state"] == "running"
        assert snap["action"] == "install"
        assert snap["extra"] == "detection"
        assert snap["message"] == "Installing..."

    def test_concurrent_claim_returns_false(self) -> None:
        q = services_module.WebCommandQueue()
        assert q.try_claim_detection_install(action="install", extra="detection") is True
        assert q.try_claim_detection_install(action="install", extra="detection") is False

    def test_set_status_preserves_extra_and_action_when_unspecified(self) -> None:
        q = services_module.WebCommandQueue()
        q.try_claim_detection_install(action="install", extra="detection")
        q.set_detection_install_status(
            state="success",
            message="Installed `detection`.",
            tail="ok",
        )
        snap = q.get_detection_install_status()
        assert snap["state"] == "success"
        assert snap["extra"] == "detection"
        assert snap["action"] == "install"
        assert snap["tail"] == "ok"

    def test_set_status_idle_releases_slot(self) -> None:
        q = services_module.WebCommandQueue()
        q.try_claim_detection_install(action="install", extra="detection")
        q.set_detection_install_status(state="idle")
        assert q.try_claim_detection_install(action="uninstall", extra="detection") is True

    def test_set_status_idle_clears_extra_and_action(self) -> None:
        q = services_module.WebCommandQueue()
        q.try_claim_detection_install(action="install", extra="detection")
        q.set_detection_install_status(state="success", message="ok")
        # Sanity: the success snapshot inherits the running job's ids.
        snap = q.get_detection_install_status()
        assert snap["extra"] == "detection"
        assert snap["action"] == "install"

        # Polling-endpoint dismissal: state="idle" with no overrides.
        q.set_detection_install_status(state="idle", message="", tail="")
        snap = q.get_detection_install_status()
        assert snap["state"] == "idle"
        assert snap["extra"] == ""
        assert snap["action"] == ""

    def test_set_status_explicit_extra_action_overrides(self) -> None:
        """Callers that want to override the slot's extra/action
        (e.g. tests setting up a specific scenario) can do so
        explicitly. Without explicit values, the slot's recorded
        ones survive."""
        q = services_module.WebCommandQueue()
        q.try_claim_detection_install(action="install", extra="detection")
        q.set_detection_install_status(
            state="error",
            extra="other",
            action="uninstall",
            message="boom",
        )
        snap = q.get_detection_install_status()
        assert snap["extra"] == "other"
        assert snap["action"] == "uninstall"


# --------------------------------------------------------------------------- #
# Live config-apply orchestrators
# --------------------------------------------------------------------------- #


class TestApplyOtpOutputChange:
    """Four-state matrix: (currently_running × new_enabled)."""

    @pytest.fixture(autouse=True)
    def _stub_resolve_source_ip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Keep the forward-restart iface→IP resolution hermetic."""
        from openfollow import net_utils

        monkeypatch.setattr(
            net_utils,
            "resolve_source_ip",
            lambda iface, *, fallback=True: ("", "none"),
        )

    def _enabled_cfg(self, **overrides: Any) -> OtpOutputConfig:
        defaults = {
            "enabled": True,
            "system_number": 1,
            "port": 5568,
            "source_iface": "",
            "priority": 100,
        }
        defaults.update(overrides)
        return OtpOutputConfig(**defaults)

    def test_on_to_on_calls_restart_with_new_fields(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        services._app._otp_server = _FakeOtpServer()
        new_cfg = self._enabled_cfg(priority=200, port=5570)

        services.apply_otp_output_change(new_cfg)

        srv = services._app._otp_server
        assert isinstance(srv, _FakeOtpServer)
        assert len(srv.restart_calls) == 1
        kwargs = srv.restart_calls[0]
        assert kwargs["priority"] == 200
        assert kwargs["port"] == 5570
        assert kwargs["system_name"] == services._app._config.psn_system_name
        # Multicast addresses are derived from system_number, not passed explicitly.
        # Restart kwargs must NOT include mcast_ip.
        assert "mcast_ip" not in kwargs

    def test_on_to_off_stops_and_drops_reference(self, services: AppRuntimeServices) -> None:
        old = _FakeOtpServer()
        services._app._otp_server = old
        new_cfg = self._enabled_cfg(enabled=False)

        services.apply_otp_output_change(new_cfg)

        assert old.stopped is True
        assert services._app._otp_server is None

    def test_off_to_on_runs_full_init_otp(self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch) -> None:
        services._app._otp_server = None
        services._app._server = _FakePsnServer()
        services._app._controlled_ids = []
        services._app._config = replace(
            services._app._config,
            otp_output=self._enabled_cfg(),
        )
        monkeypatch.setattr(services_module, "OtpServer", _FakeOtpServer)

        services.apply_otp_output_change(self._enabled_cfg())

        assert services._app._otp_server is not None
        assert services._app._otp_server.started is True

    def test_off_to_on_raises_when_init_otp_silently_fails_to_bind(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        class _SilentlyFailingOtpServer(_FakeOtpServer):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(**kwargs)
                # Mirror the real failure mode: the multicast socket
                # never opens (start() spawned a retry thread instead).
                self._socket = None

        services._app._otp_server = None
        services._app._server = _FakePsnServer()
        services._app._controlled_ids = []
        services._app._config = replace(
            services._app._config,
            otp_output=self._enabled_cfg(),
        )
        monkeypatch.setattr(services_module, "OtpServer", _SilentlyFailingOtpServer)

        with pytest.raises(OSError, match="failed to open multicast socket"):
            services.apply_otp_output_change(self._enabled_cfg())

        # Orchestrator tore the half-initialised server back down so
        # the next reload starts fresh.
        assert services._app._otp_server is None

    def test_off_to_off_is_noop(self, services: AppRuntimeServices) -> None:
        services._app._otp_server = None
        new_cfg = OtpOutputConfig(enabled=False)

        services.apply_otp_output_change(new_cfg)

        assert services._app._otp_server is None

    def test_on_to_on_failure_rolls_back_to_prior_config(self, services: AppRuntimeServices) -> None:
        prior_kwargs = {
            "system_name": "OldName",
            "system_number": 7,
            "mcast_ip": "239.159.99.99",
            "port": 4000,
            "source_ip": "10.0.0.5",
            "priority": 50,
        }
        server = _FakeOtpServer(**prior_kwargs)

        # Make the first ``restart`` call (with new cfg) raise; let
        # subsequent calls succeed so the rollback can complete.
        first_raised: list[bool] = [False]
        original_restart = server.restart

        def _restart_raising_first(**kwargs: Any) -> None:
            if not first_raised[0]:
                first_raised[0] = True
                raise OSError("simulated bind failure on new cfg")
            original_restart(**kwargs)

        server.restart = _restart_raising_first  # type: ignore[method-assign]

        services._app._otp_server = server
        new_cfg = self._enabled_cfg(system_number=99, port=5999, priority=200)

        with pytest.raises(OSError):
            services.apply_otp_output_change(new_cfg)

        # Forward attempt happened (raised), then a rollback restart
        # with the captured prior kwargs landed and updated the
        # server back to its old otp_output fields.
        assert first_raised[0] is True
        assert server._system_number == 7
        assert server._port == 4000
        assert server._priority == 50
        # Rollback-success keeps the server alive on prior config, so the
        # orchestrator must not null the reference.
        assert services._app._otp_server is server
        # ``system_name`` is owned by ``psn_system_name``, not by ``otp_output``.
        # Rollback must preserve it to avoid undoing unrelated changes.
        assert server._system_name == services._app._config.psn_system_name
        assert server._system_name != "OldName"

    def test_on_to_on_rollback_failure_logs_reraises_and_clears_reference(
        self,
        services: AppRuntimeServices,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """If rollback restart also raises, re-raise the original exception
        and null the reference since the server is dead."""
        server = _FakeOtpServer(system_number=7)

        def _always_raise(**kwargs: Any) -> None:
            raise OSError(f"primary failure with {kwargs.get('system_number')!r}")

        server.restart = _always_raise  # type: ignore[method-assign]

        services._app._otp_server = server
        new_cfg = self._enabled_cfg(system_number=99)

        with caplog.at_level("ERROR", logger="openfollow.services"):
            with pytest.raises(OSError, match="primary failure with 99"):
                services.apply_otp_output_change(new_cfg)

        assert any("OTP server rollback" in r.message for r in caplog.records)
        assert services._app._otp_server is None


class TestApplyRttrpmOutputChange:
    """Four-state matrix mirrors OTP."""

    def _enabled_cfg(self, **overrides: Any) -> RttrpmOutputConfig:
        defaults: dict[str, Any] = {
            "enabled": True,
            "host": "127.0.0.1",
            "port": 24601,
            "fps": 30,
            "context": 0,
        }
        defaults.update(overrides)
        return RttrpmOutputConfig(**defaults)

    def test_on_to_on_calls_restart_with_new_fields(self, services: AppRuntimeServices) -> None:
        services._app._rttrpm_server = _FakeRttrpmServer()
        new_cfg = self._enabled_cfg(host="10.0.0.5", fps=60)

        services.apply_rttrpm_output_change(new_cfg)

        srv = services._app._rttrpm_server
        assert isinstance(srv, _FakeRttrpmServer)
        assert len(srv.restart_calls) == 1
        kwargs = srv.restart_calls[0]
        assert kwargs["host"] == "10.0.0.5"
        assert kwargs["fps"] == 60.0
        assert kwargs["context"] == 0

    def test_on_to_off_stops_and_drops_reference(self, services: AppRuntimeServices) -> None:
        old = _FakeRttrpmServer()
        services._app._rttrpm_server = old
        new_cfg = self._enabled_cfg(enabled=False)

        services.apply_rttrpm_output_change(new_cfg)

        assert old.stopped is True
        assert services._app._rttrpm_server is None

    def test_off_to_on_runs_full_init_rttrpm(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        services._app._rttrpm_server = None
        services._app._server = _FakePsnServer()
        services._app._controlled_ids = []
        services._app._config = replace(
            services._app._config,
            rttrpm_output=self._enabled_cfg(),
        )
        monkeypatch.setattr(services_module, "RttrpmServer", _FakeRttrpmServer)

        services.apply_rttrpm_output_change(self._enabled_cfg())

        assert services._app._rttrpm_server is not None
        assert services._app._rttrpm_server.started is True

    def test_off_to_off_is_noop(self, services: AppRuntimeServices) -> None:
        services._app._rttrpm_server = None
        new_cfg = RttrpmOutputConfig(enabled=False)

        services.apply_rttrpm_output_change(new_cfg)

        assert services._app._rttrpm_server is None

    def test_on_to_on_failure_rolls_back_to_prior_config(self, services: AppRuntimeServices) -> None:
        """Mirror of OTP transactional rollback for RTTrPM."""
        prior_kwargs = {"host": "10.0.0.5", "port": 24601, "fps": 30, "context": 7}
        server = _FakeRttrpmServer(**prior_kwargs)

        first_raised: list[bool] = [False]
        original_restart = server.restart

        def _restart_raising_first(**kwargs: Any) -> None:
            if not first_raised[0]:
                first_raised[0] = True
                raise OSError("simulated failure on new cfg")
            original_restart(**kwargs)

        server.restart = _restart_raising_first  # type: ignore[method-assign]

        services._app._rttrpm_server = server
        new_cfg = self._enabled_cfg(host="192.168.1.5", port=9999, fps=60, context=99)

        with pytest.raises(OSError):
            services.apply_rttrpm_output_change(new_cfg)

        assert first_raised[0] is True
        # Rollback restored prior fields.
        assert server._host == "10.0.0.5"
        assert server._port == 24601
        assert server._fps == 30.0
        assert server._context == 7
        # Rollback-success preserves the running server; orchestrator
        # must not null its reference.
        assert services._app._rttrpm_server is server

    def test_on_to_on_rollback_failure_logs_reraises_and_clears_reference(
        self,
        services: AppRuntimeServices,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """If rollback restart also raises, re-raise the original
        exception and null the reference since the server is dead."""
        server = _FakeRttrpmServer(host="10.0.0.5")

        def _always_raise(**kwargs: Any) -> None:
            raise OSError(f"primary failure with host={kwargs.get('host')!r}")

        server.restart = _always_raise  # type: ignore[method-assign]

        services._app._rttrpm_server = server
        new_cfg = self._enabled_cfg(host="192.168.1.5")

        with caplog.at_level("ERROR", logger="openfollow.services"):
            with pytest.raises(OSError, match="primary failure with host"):
                services.apply_rttrpm_output_change(new_cfg)

        assert any("RTTrPM server rollback" in r.message for r in caplog.records)
        assert services._app._rttrpm_server is None


# Eos OSC output (init_eos / apply_eos_output_change) was removed; the unified
# OscService + the configurable transmitter system supersede it. Tests for the
# new OSC service live in test_osc_service.py / test_osc_template.py.

# --------------------------------------------------------------------------- #
# init_osc_transmitters / apply_osc_transmitters_change
# --------------------------------------------------------------------------- #


class TestInitOscTransmitters:
    def test_creates_manager_and_starts_scheduler(
        self,
        services: AppRuntimeServices,
    ) -> None:
        """``init_osc_transmitters`` constructs the manager, hands it
        the runtime config, and starts the scheduler thread. After
        initialisation, the field is non-None and the manager is alive."""
        from openfollow.configuration import (
            OscTransmitterConfig,
            OscTransmittersConfig,
        )

        services._app._config = replace(
            services._app._config,
            osc_transmitters=OscTransmittersConfig(
                transmitters=[
                    OscTransmitterConfig(id="row-a", host="127.0.0.1", port=9001),
                ]
            ),
        )
        services.init_osc_transmitters()
        try:
            manager = services._osc_transmitter_manager
            assert manager is not None
            assert manager.row_ids() == ["row-a"]
            assert manager._thread is not None
            assert manager._thread.is_alive()
        finally:
            if services._osc_transmitter_manager is not None:
                services._osc_transmitter_manager.stop()

    def test_marker_provider_uses_psn_server(
        self,
        services: AppRuntimeServices,
    ) -> None:
        psn = _FakePsnServer()
        psn.add_marker(7, "T7")
        services._app._server = psn
        marker = services._marker_provider(7)
        assert marker is not None
        assert services._marker_provider(99) is None

    def test_psn_source_advisory_reads_app_degraded_state(
        self,
        services: AppRuntimeServices,
    ) -> None:
        """The web provider mirrors the app's degraded-state surface for
        rendering the PSN interface unavailability advisory."""
        services._app._psn_source_status = "primary"
        services._app._psn_source_banner = "iface gone – using 10.0.0.5"
        services._app._psn_source_resolved_ip = "10.0.0.5"
        assert services._psn_source_advisory() == {
            "status": "primary",
            "banner": "iface gone – using 10.0.0.5",
            "resolved_ip": "10.0.0.5",
        }

    def test_psn_source_advisory_empty_when_unset(
        self,
        services: AppRuntimeServices,
    ) -> None:
        """Nothing pinned / pin honoured → all-empty advisory (no banner),
        so the normal case renders no notice."""
        assert services._psn_source_advisory() == {
            "status": "",
            "banner": "",
            "resolved_ip": "",
        }

    def test_grid_provider_reads_live_config(
        self,
        services: AppRuntimeServices,
    ) -> None:
        from openfollow.configuration import GridConfig

        # grid_provider tuple includes (width, depth, max_height, z_offset).
        # Defaults are 0.0 for unset values.
        services._app._config = replace(
            services._app._config,
            grid=GridConfig(width=12.0, depth=4.0),
        )
        assert services._grid_provider() == (12.0, 4.0, 0.0, 0.0)

    def test_grid_provider_includes_max_height_and_z_offset(
        self,
        services: AppRuntimeServices,
    ) -> None:
        """Operator-set ``max_height`` and ``z_offset`` flow into the
        provider tuple for render context."""
        from openfollow.configuration import GridConfig

        services._app._config = replace(
            services._app._config,
            grid=GridConfig(width=10.0, depth=6.0, z_offset=0.5, max_height=4.0),
        )
        assert services._grid_provider() == (10.0, 6.0, 4.0, 0.5)

    def test_fader_provider_returns_value_for_valid_index(
        self,
        services: AppRuntimeServices,
    ) -> None:
        """Provider closure proxies through to :class:`VirtualFaderBus.value`
        for in-range indices."""
        for idx in range(1, 9):
            assert services._fader_provider(idx) == 0.0

    def test_fader_provider_returns_none_for_out_of_range_index(
        self,
        services: AppRuntimeServices,
    ) -> None:
        """Out-of-range indices (operator referenced ``[fader:9]`` on
        the eight-fader bus) return ``None``; the renderer turns that
        into a ring-buffer skip with the actionable message."""
        assert services._fader_provider(0) is None
        assert services._fader_provider(9) is None
        assert services._fader_provider(-1) is None

    def test_marker_fader_provider_proxies_bus(
        self,
        services: AppRuntimeServices,
    ) -> None:
        """The ``[markerfader]`` provider proxies
        :meth:`VirtualFaderBus.marker_fader_value`: an unprovisioned
        marker → ``None``; a provisioned one → its 0..1 value."""
        assert services._marker_fader_provider(1) is None
        services._virtual_faders.provision_marker_faders(
            [1],
            default_value=0.5,
        )
        assert services._marker_fader_provider(1) == 0.5
        assert services._marker_fader_provider(99) is None

    def test_controller_marker_provider_none_when_input_manager_absent(
        self,
        services: AppRuntimeServices,
    ) -> None:
        """The ``:cN`` provider tolerates ``_input_manager is None`` – the
        transmitter manager is built before the input manager, so a render
        before it exists must skip, not crash."""
        services._app._input_manager = None
        assert services._controller_marker_provider(0) is None

    def test_controller_marker_provider_delegates_to_input_manager(
        self,
        services: AppRuntimeServices,
    ) -> None:
        """With an input manager present, the provider delegates to
        :meth:`InputManager.controller_marker_id`."""
        calls: list[int] = []

        def _controller_marker_id(idx: int) -> int | None:
            calls.append(idx)
            return 9 if idx == 0 else None

        services._app._input_manager = SimpleNamespace(
            controller_marker_id=_controller_marker_id,
        )
        assert services._controller_marker_provider(0) == 9
        assert services._controller_marker_provider(1) is None
        assert calls == [0, 1]

    def test_marker_fader_values_provider_snapshot(
        self,
        services: AppRuntimeServices,
    ) -> None:
        """Marker Faders snapshot includes one entry per controlled marker with a provisioned fader.
        ``name`` falls back to "" when not in the catalog; ``value`` is the bus's current 0..1 reading."""
        services._app._controlled_ids = [3, 7]
        services._virtual_faders.provision_marker_faders([3, 7])
        services._virtual_faders.set_marker_fader_from_velocity_delta(
            3,
            0.4,
        )
        snapshot = services._marker_fader_values_provider()
        # ``color`` surfaces the marker's catalog colour for the read-only Marker Fader strip tinting.
        app = services._app
        assert snapshot == [
            {"marker_id": 3, "name": "", "value": pytest.approx(0.4), "color": _resolve_marker_color(app, 3)},
            {"marker_id": 7, "name": "", "value": 0.0, "color": _resolve_marker_color(app, 7)},
        ]

    def test_marker_fader_values_provider_skips_unprovisioned(
        self,
        services: AppRuntimeServices,
    ) -> None:
        """A controlled marker without a provisioned fader yet – the
        transient between a ``controlled_marker_ids`` edit and the
        re-provision – is skipped so the viz never shows a phantom row
        with a missing value."""
        services._app._controlled_ids = [3, 8]
        services._virtual_faders.provision_marker_faders([3])  # 8 absent
        snapshot = services._marker_fader_values_provider()
        assert [e["marker_id"] for e in snapshot] == [3]

    def test_midi_discovered_devices_provider_default_empty(
        self,
        services: AppRuntimeServices,
    ) -> None:
        """Without a real rtmidi backend, discovery returns an empty list."""
        assert services._midi_discovered_devices_provider() == []

    def test_midi_discovered_devices_provider_returns_dict_shape(
        self,
        services: AppRuntimeServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The provider bridges ``MidiSubsystem.discover()``'s typed
        :class:`DiscoveredDevice` shape to plain dicts the route /
        template layer consumes. The dict keys match the template's
        access pattern (``identifier`` / ``port_name`` / ``product``
        / ``serial``)."""
        from openfollow.input.midi import DiscoveredDevice

        monkeypatch.setattr(
            services._midi,
            "discover",
            lambda: [
                DiscoveredDevice(
                    serial=None,
                    port_name="MIDI Mix",
                    product="MIDI Mix",
                ),
                DiscoveredDevice(
                    serial="ABC123",
                    port_name="X-Touch",
                    product="X-Touch Mini",
                ),
            ],
        )
        out = services._midi_discovered_devices_provider()
        assert out == [
            {
                "identifier": "port:MIDI Mix|MIDI Mix",
                "port_name": "MIDI Mix",
                "product": "MIDI Mix",
                "serial": None,
            },
            {
                "identifier": "serial:ABC123",
                "port_name": "X-Touch",
                "product": "X-Touch Mini",
                "serial": "ABC123",
            },
        ]

    def test_midi_fader_values_provider_returns_full_snapshot(
        self,
        services: AppRuntimeServices,
    ) -> None:
        """Snapshot of eight MIDI faders with their index, value, and
        pickup state in the live-poll response format."""
        snapshot = services._midi_fader_values_provider()
        assert len(snapshot) == 8
        for idx, entry in enumerate(snapshot, start=1):
            assert entry["index"] == idx
            assert entry["value"] == 0.0
            assert entry["picked_up"] is True
            assert entry["show_on_display"] is False
            # ``name`` falls back to "Fader N" when the operator
            # hasn't set a custom name on a fresh config.
            assert entry["name"] == f"Fader {idx}"

    def test_init_syncs_hotkey_and_button_bindings_into_registry(
        self,
        services: AppRuntimeServices,
    ) -> None:
        """Hotkey and ControllerButton triggers claim their keys/buttons
        in the conflict registry for web UI warnings."""
        from openfollow.configuration import (
            ControllerButtonTrigger,
            HotkeyTrigger,
            OscTransmitterConfig,
            OscTransmittersConfig,
        )
        from openfollow.input.conflicts import InputBinding

        services._app._config = replace(
            services._app._config,
            osc_transmitters=OscTransmittersConfig(
                transmitters=[
                    OscTransmitterConfig(id="r1", trigger=HotkeyTrigger(key="r")),
                    OscTransmitterConfig(
                        id="r2",
                        trigger=ControllerButtonTrigger(button="A"),
                    ),
                    # Stream rows don't claim anything.
                    OscTransmitterConfig(id="r3"),
                ]
            ),
        )
        services.init_osc_transmitters()
        try:
            registry = services._conflict_registry
            assert InputBinding(
                kind="key",
                identifier="r",
            ) in registry.bindings_for("osc:hotkey")
            assert InputBinding(
                kind="controller_button",
                identifier="A",
            ) in registry.bindings_for("osc:controller_button")
        finally:
            if services._osc_transmitter_manager is not None:
                services._osc_transmitter_manager.stop()

    def test_init_skips_hotkey_with_empty_key(
        self,
        services: AppRuntimeServices,
    ) -> None:
        """A Hotkey trigger with no key set is a half-configured row –
        don't claim an empty-string identifier (the registry would
        treat it as a real binding and surface bogus conflicts)."""
        from openfollow.configuration import (
            HotkeyTrigger,
            OscTransmitterConfig,
            OscTransmittersConfig,
        )

        services._app._config = replace(
            services._app._config,
            osc_transmitters=OscTransmittersConfig(
                transmitters=[
                    OscTransmitterConfig(id="r1", trigger=HotkeyTrigger(key="")),
                ]
            ),
        )
        services.init_osc_transmitters()
        try:
            assert services._conflict_registry.bindings_for("osc:hotkey") == []
        finally:
            if services._osc_transmitter_manager is not None:
                services._osc_transmitter_manager.stop()

    def test_init_skips_controller_button_with_empty_button(
        self,
        services: AppRuntimeServices,
    ) -> None:
        from openfollow.configuration import (
            ControllerButtonTrigger,
            OscTransmitterConfig,
            OscTransmittersConfig,
        )

        services._app._config = replace(
            services._app._config,
            osc_transmitters=OscTransmittersConfig(
                transmitters=[
                    OscTransmitterConfig(
                        id="r1",
                        trigger=ControllerButtonTrigger(button=""),
                    ),
                ]
            ),
        )
        services.init_osc_transmitters()
        try:
            assert (
                services._conflict_registry.bindings_for(
                    "osc:controller_button",
                )
                == []
            )
        finally:
            if services._osc_transmitter_manager is not None:
                services._osc_transmitter_manager.stop()


class TestApplyOscTransmittersChange:
    def test_uninitialised_manager_runs_init(
        self,
        services: AppRuntimeServices,
    ) -> None:
        """Two-state matrix: when the manager hasn't been built yet, the
        apply path delegates to ``init_osc_transmitters``."""
        from openfollow.configuration import OscTransmittersConfig

        assert services._osc_transmitter_manager is None
        new_cfg = OscTransmittersConfig(transmitters=[])
        services.apply_osc_transmitters_change(new_cfg)
        try:
            assert services._osc_transmitter_manager is not None
        finally:
            if services._osc_transmitter_manager is not None:
                services._osc_transmitter_manager.stop()

    def test_reinit_reattaches_event_bus_when_input_manager_exists(
        self,
        services: AppRuntimeServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A hot-reload re-init (manager was None, rebuilt after the
        InputManager already ran) re-attaches the input event bus, so
        Hotkey / ControllerButton OSC rows keep dispatching rather than
        silently dying for the rest of the session."""
        from openfollow.configuration import OscTransmittersConfig
        from openfollow.osc import transmitter as transmitter_module

        monkeypatch.setattr(services_module, "InputManager", _FakeInputManager)
        services.init_input_manager()  # InputManager exists; manager still None
        assert services._osc_transmitter_manager is None

        attached: list[object] = []
        orig = transmitter_module.OscTransmitterManager.attach_event_bus

        def _spy(self: object, bus: object) -> None:
            attached.append(bus)
            orig(self, bus)  # type: ignore[arg-type]

        monkeypatch.setattr(transmitter_module.OscTransmitterManager, "attach_event_bus", _spy)

        services.apply_osc_transmitters_change(OscTransmittersConfig(transmitters=[]))
        try:
            assert attached, "event bus was not re-attached on re-init"
            assert attached[0] is services._app._input_manager.event_bus
        finally:
            if services._osc_transmitter_manager is not None:
                services._osc_transmitter_manager.stop()

    def test_existing_manager_restart_swaps_rows(
        self,
        services: AppRuntimeServices,
    ) -> None:
        """When the manager already exists, the apply path keeps the
        scheduler thread alive and just swaps in the new rows."""
        from openfollow.configuration import (
            OscTransmitterConfig,
            OscTransmittersConfig,
        )

        services.init_osc_transmitters()
        try:
            manager = services._osc_transmitter_manager
            assert manager is not None
            scheduler_thread = manager._thread

            new_cfg = OscTransmittersConfig(
                transmitters=[
                    OscTransmitterConfig(id="r1"),
                    OscTransmitterConfig(id="r2"),
                ]
            )
            services.apply_osc_transmitters_change(new_cfg)
            # Same manager instance, same scheduler thread, new rows.
            assert services._osc_transmitter_manager is manager
            assert manager._thread is scheduler_thread
            assert sorted(manager.row_ids()) == ["r1", "r2"]
        finally:
            if services._osc_transmitter_manager is not None:
                services._osc_transmitter_manager.stop()

    def test_apply_resyncs_conflict_registry(
        self,
        services: AppRuntimeServices,
    ) -> None:
        from openfollow.configuration import (
            HotkeyTrigger,
            OscTransmitterConfig,
            OscTransmittersConfig,
        )
        from openfollow.input.conflicts import InputBinding

        # First config: row claims "r".
        services._app._config = replace(
            services._app._config,
            osc_transmitters=OscTransmittersConfig(
                transmitters=[
                    OscTransmitterConfig(id="r1", trigger=HotkeyTrigger(key="r")),
                ]
            ),
        )
        services.init_osc_transmitters()
        try:
            registry = services._conflict_registry
            assert InputBinding(
                kind="key",
                identifier="r",
            ) in registry.bindings_for("osc:hotkey")

            # Hot-reload: row now claims "t" instead.
            services._app._config = replace(
                services._app._config,
                osc_transmitters=OscTransmittersConfig(
                    transmitters=[
                        OscTransmitterConfig(
                            id="r1",
                            trigger=HotkeyTrigger(key="t"),
                        ),
                    ]
                ),
            )
            services.apply_osc_transmitters_change(
                services._app._config.osc_transmitters,
            )
            bindings = registry.bindings_for("osc:hotkey")
            # Old claim gone, new claim in.
            assert InputBinding(kind="key", identifier="r") not in bindings
            assert InputBinding(kind="key", identifier="t") in bindings
        finally:
            if services._osc_transmitter_manager is not None:
                services._osc_transmitter_manager.stop()


class TestApplyPsnSourceIpChange:
    def test_rebinds_both_psn_input_and_output(self, services: AppRuntimeServices) -> None:
        recv = _FakePsnReceiver()
        server = _FakePsnServer()
        services._app._psn_receiver = recv
        services._app._server = server

        services.apply_psn_source_ip_change("192.168.1.5")

        assert recv.rebind_calls == ["192.168.1.5"]
        assert server.rebind_calls == ["192.168.1.5"]

    def test_no_op_when_neither_receiver_nor_server_present(self, services: AppRuntimeServices) -> None:
        services._app._psn_receiver = None
        services._app._server = None
        services.apply_psn_source_ip_change("192.168.1.5")
        # No error raised.

    def test_receiver_rebind_failure_does_not_touch_server(self, services: AppRuntimeServices) -> None:
        """If the receiver's ``rebind`` raises (bad new source_ip),
        the server is NOT attempted – the working output keeps
        emitting from the prior interface. The receiver itself gets
        a best-effort rollback to its prior IP so downstream
        ``_apply_with_fallback`` sees runtime state consistent with
        the dispatcher's reverted config (input back on old IP,
        output untouched).
        """

        class _RaisingReceiver:
            def __init__(self) -> None:
                self.rebind_calls: list[str] = []
                self._source_ip = "10.0.0.1"

            def rebind(self, source_ip: str) -> None:
                self.rebind_calls.append(source_ip)
                raise OSError(f"failed to bind to {source_ip!r}")

        recv = _RaisingReceiver()
        server = _FakePsnServer()
        services._app._psn_receiver = recv
        services._app._server = server

        with pytest.raises(OSError):
            services.apply_psn_source_ip_change("10.99.99.99")

        # Forward attempt + rollback attempt (rollback also raised
        # since the fake always raises – logged but doesn't propagate).
        assert recv.rebind_calls == ["10.99.99.99", "10.0.0.1"]
        # Server NOT touched because receiver raised before we got to it.
        assert server.rebind_calls == []

    def test_server_rebind_failure_rolls_receiver_back_to_old_source_ip(self, services: AppRuntimeServices) -> None:

        class _RaisingServer(_FakePsnServer):
            def rebind(self, source_ip: str) -> None:
                self.rebind_calls.append(source_ip)
                raise OSError(f"failed to bind to {source_ip!r}")

        recv = _FakePsnReceiver(source_ip="10.0.0.1")
        server = _RaisingServer()
        services._app._psn_receiver = recv
        services._app._server = server

        with pytest.raises(OSError):
            services.apply_psn_source_ip_change("192.168.1.5")

        # Receiver rebound forward, then back – calls reflect the
        # transaction-style rollback.
        assert recv.rebind_calls == ["192.168.1.5", "10.0.0.1"]
        # Server attempted forward (raised), then attempted rollback
        # (also raises since the fake always does – logged but not
        # propagated). Both attempts visible in calls.
        assert server.rebind_calls == ["192.168.1.5", ""]
        assert recv._source_ip == "10.0.0.1"

    def test_server_rebind_failure_with_no_receiver_still_rolls_server_back(self, services: AppRuntimeServices) -> None:

        class _RaisingServer(_FakePsnServer):
            def rebind(self, source_ip: str) -> None:
                self.rebind_calls.append(source_ip)
                raise OSError(f"failed to bind to {source_ip!r}")

        server = _RaisingServer(source_ip="10.0.0.1")
        services._app._psn_receiver = None
        services._app._server = server

        with pytest.raises(OSError):
            services.apply_psn_source_ip_change("192.168.1.5")

        # Server attempted forward then rolled back (rollback also
        # raised since the fake always raises – logged + suppressed).
        assert server.rebind_calls == ["192.168.1.5", "10.0.0.1"]

    def test_server_rebind_failure_logs_rollback_failure_and_reraises_original(
        self,
        services: AppRuntimeServices,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Defensive: if the rollback rebind ALSO fails, the receiver
        + server end up both stopped/inconsistent – but the operator
        still sees the original failure (the more informative one) and
        a logged note that rollback failed. Don't let the secondary
        exception eat the primary."""

        rollback_call_count: list[int] = []

        class _RecvFailsOnRollback(_FakePsnReceiver):
            def rebind(self, source_ip: str) -> None:
                self.rebind_calls.append(source_ip)
                self._source_ip = source_ip
                rollback_call_count.append(len(self.rebind_calls))
                if len(self.rebind_calls) > 1:
                    raise OSError(f"rollback to {source_ip!r} failed too")

        class _RaisingServer(_FakePsnServer):
            def rebind(self, source_ip: str) -> None:
                self.rebind_calls.append(source_ip)
                raise OSError(f"primary failure on {source_ip!r}")

        recv = _RecvFailsOnRollback(source_ip="10.0.0.1")
        server = _RaisingServer()
        services._app._psn_receiver = recv
        services._app._server = server

        with caplog.at_level("ERROR", logger="openfollow.services"):
            with pytest.raises(OSError, match="primary failure"):
                services.apply_psn_source_ip_change("192.168.1.5")

        # Rollback was attempted (calls = forward + rollback).
        assert recv.rebind_calls == ["192.168.1.5", "10.0.0.1"]
        # Rollback failure was logged but didn't mask the primary raise.
        assert any("PSN receiver rollback" in r.message for r in caplog.records)

    def test_combined_with_mcast_does_one_server_rebind(self, services: AppRuntimeServices) -> None:
        """Passing both ``new_mcast_ip`` and ``new_source_ip`` recycles
        the server socket once with both values applied."""
        recv = _FakePsnReceiver(source_ip="10.0.0.1")
        server = _FakePsnServer(source_ip="10.0.0.1")
        server._mcast_ip = "236.10.10.10"
        services._app._psn_receiver = recv
        services._app._server = server

        services.apply_psn_source_ip_change(
            "192.168.1.5",
            new_mcast_ip="239.0.0.1",
        )

        # Receiver: standard single-arg rebind.
        assert recv.rebind_calls == ["192.168.1.5"]
        # Server: ONE rebind call carrying both new values.
        assert server.rebind_calls == [("192.168.1.5", "239.0.0.1")]
        # Final state agrees with what was passed.
        assert server._source_ip == "192.168.1.5"
        assert server._mcast_ip == "239.0.0.1"

    def test_combined_failure_rolls_back_both_fields(self, services: AppRuntimeServices) -> None:
        """When the combined rebind raises, the server rolls back
        BOTH source_ip and mcast_ip to their prior values. Without
        the dual-field rollback, ``app._config`` reverts to old
        source_ip but the server stays bound to the new (broken)
        mcast_ip until the operator re-saves."""

        class _RaisingServer(_FakePsnServer):
            def rebind(
                self,
                source_ip: str,
                *,
                mcast_ip: object = _UNCHANGED_SENTINEL,
            ) -> None:
                self.rebind_calls.append((source_ip, str(mcast_ip)))
                if len(self.rebind_calls) == 1:
                    # First call (forward) raises to trigger rollback.
                    raise OSError("bind failed")
                # Second call (rollback) succeeds – restore fields.
                self._source_ip = source_ip
                if mcast_ip is not _UNCHANGED_SENTINEL:
                    self._mcast_ip = mcast_ip  # type: ignore[assignment]

        server = _RaisingServer(source_ip="10.0.0.1")
        server._mcast_ip = "236.10.10.10"
        services._app._psn_receiver = None
        services._app._server = server

        with pytest.raises(OSError):
            services.apply_psn_source_ip_change(
                "192.168.1.5",
                new_mcast_ip="239.0.0.1",
            )

        # Forward + rollback, both carrying both values.
        assert server.rebind_calls == [
            ("192.168.1.5", "239.0.0.1"),
            ("10.0.0.1", "236.10.10.10"),
        ]
        assert server._source_ip == "10.0.0.1"
        assert server._mcast_ip == "236.10.10.10"


class TestApplyPsnMcastIpChange:
    """Live-apply for ``psn_mcast_ip`` recycles the PSN server's multicast
    socket with transactional rollback on failure."""

    def test_rebinds_psn_server_on_new_mcast_ip(self, services: AppRuntimeServices) -> None:
        server = _FakePsnServer()
        server._mcast_ip = "236.10.10.10"
        services._app._server = server

        services.apply_psn_mcast_ip_change("239.0.0.1")

        assert server.rebind_mcast_ip_calls == ["239.0.0.1"]

    def test_no_op_when_server_absent(self, services: AppRuntimeServices) -> None:
        services._app._server = None
        services.apply_psn_mcast_ip_change("239.0.0.1")

    def test_rebind_failure_rolls_server_back_to_old_mcast_ip(self, services: AppRuntimeServices) -> None:

        class _RaisingServer(_FakePsnServer):
            def rebind_mcast_ip(self, mcast_ip: str | None) -> None:
                self.rebind_mcast_ip_calls.append(mcast_ip)
                raise OSError(f"failed to bind to {mcast_ip!r}")

        server = _RaisingServer()
        server._mcast_ip = "236.10.10.10"
        services._app._server = server

        with pytest.raises(OSError):
            services.apply_psn_mcast_ip_change("239.0.0.1")

        # Forward attempt + rollback attempt (rollback also raises since
        # the fake always raises – logged but not propagated).
        assert server.rebind_mcast_ip_calls == ["239.0.0.1", "236.10.10.10"]

    def test_rollback_failure_logs_and_reraises_primary(
        self,
        services: AppRuntimeServices,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """If rollback also fails, the operator still sees the original
        failure (the more informative one) and a logged note that
        rollback failed."""

        class _RaisingServer(_FakePsnServer):
            def rebind_mcast_ip(self, mcast_ip: str | None) -> None:
                self.rebind_mcast_ip_calls.append(mcast_ip)
                raise OSError(f"primary failure on {mcast_ip!r}")

        server = _RaisingServer()
        server._mcast_ip = "236.10.10.10"
        services._app._server = server

        with caplog.at_level("ERROR", logger="openfollow.services"):
            with pytest.raises(OSError, match="primary failure"):
                services.apply_psn_mcast_ip_change("239.0.0.1")

        assert any("PSN server rollback to mcast_ip" in r.message for r in caplog.records)


class TestApplyPsnSystemNameChange:
    """``psn_system_name`` propagates to all running services without
    recycling sockets."""

    @pytest.fixture(autouse=True)
    def hostname_sync_calls(self, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        """Record + stub the hostname sync so these propagation tests stay
        hermetic (the real one probes ``sudo`` on a Linux host). The list is
        the canonical names passed through to the OS-hostname updater."""
        calls: list[str] = []

        def _fake(broker: object, name: str) -> bool:
            calls.append(name)
            return False

        monkeypatch.setattr(
            "openfollow.privilege.device_repair.sync_station_hostname",
            _fake,
        )
        return calls

    def test_renaming_propagates_to_system_hostname(
        self,
        services: AppRuntimeServices,
        hostname_sync_calls: list[str],
    ) -> None:
        """A web-UI rename must also reach the OS hostname updater with the
        same canonical (stripped) name, so the unit's ``<slug>.local`` mDNS
        name follows the station name."""
        services._app._canvas = SimpleNamespace(set_title=lambda title: None)
        services._app._server = None
        services._app._otp_server = None
        services._app._web_server = None

        services.apply_psn_system_name_change("  My Show  ")

        assert hostname_sync_calls == ["My Show"]

    def test_propagates_to_psn_server_otp_server_and_web(
        self,
        services: AppRuntimeServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Define recorder lists before lambdas to avoid static analysis warnings.
        title_calls: list[str] = []
        web_calls: list[str] = []
        services._app._canvas = SimpleNamespace(
            set_title=lambda title: title_calls.append(title),
        )
        psn_server = _FakePsnServer()
        otp_server = _FakeOtpServer()
        web_server = SimpleNamespace(
            update_system_name=lambda name: web_calls.append(name),
        )
        services._app._server = psn_server
        services._app._otp_server = otp_server
        services._app._web_server = web_server

        services.apply_psn_system_name_change("New Name")

        assert psn_server.system_name_updates == ["New Name"]
        assert otp_server.system_name_updates == ["New Name"]
        assert web_calls == ["New Name"]
        assert title_calls == ["New Name"]

    def test_no_op_when_servers_absent(
        self,
        services: AppRuntimeServices,
    ) -> None:
        services._app._canvas = SimpleNamespace(set_title=lambda title: None)
        services._app._server = None
        services._app._otp_server = None
        services._app._web_server = None

        services.apply_psn_system_name_change("New Name")
        # No error raised.

    def test_canonical_name_is_consistent_across_services(
        self,
        services: AppRuntimeServices,
    ) -> None:
        """The orchestrator normalizes the name once and applies the same
        value to all downstream services."""
        title_calls: list[str] = []
        services._app._canvas = SimpleNamespace(
            set_title=lambda title: title_calls.append(title),
        )
        psn_server = _FakePsnServer()
        otp_server = _FakeOtpServer()
        web_calls: list[str] = []
        web_server = SimpleNamespace(
            update_system_name=lambda name: web_calls.append(name),
        )
        services._app._server = psn_server
        services._app._otp_server = otp_server
        services._app._web_server = web_server

        services.apply_psn_system_name_change("  My Show  ")

        # Strip applied uniformly across every consumer.
        assert title_calls == ["My Show"]
        assert psn_server.system_name_updates == ["My Show"]
        assert otp_server.system_name_updates == ["My Show"]
        assert web_calls == ["My Show"]

    def test_empty_name_falls_back_to_openfollow_everywhere(
        self,
        services: AppRuntimeServices,
    ) -> None:
        """An empty or whitespace-only name must produce the same
        ``"OpenFollow"`` fallback everywhere – not just on the GTK
        title."""
        title_calls: list[str] = []
        services._app._canvas = SimpleNamespace(
            set_title=lambda title: title_calls.append(title),
        )
        psn_server = _FakePsnServer()
        otp_server = _FakeOtpServer()
        web_calls: list[str] = []
        web_server = SimpleNamespace(
            update_system_name=lambda name: web_calls.append(name),
        )
        services._app._server = psn_server
        services._app._otp_server = otp_server
        services._app._web_server = web_server

        services.apply_psn_system_name_change("   ")

        assert title_calls == ["OpenFollow"]
        assert psn_server.system_name_updates == ["OpenFollow"]
        assert otp_server.system_name_updates == ["OpenFollow"]
        assert web_calls == ["OpenFollow"]


class TestApplyDetectionChange:
    """Three-state matrix for ``apply_detection_change`` (the off→on
    transition is handled by the dispatcher, not the orchestrator –
    see
    ``test_configuration.test_apply_runtime_detection_off_to_on_still_requests_restart``).

    The detector reference lives on ``AppRuntimeServices`` itself
    (set in ``init_video``), NOT on ``OpenFollowApp``. These tests
    therefore set/assert ``services._person_detector`` so the wiring
    matches production rather than passing-by-coincidence on a
    ``SimpleNamespace`` that accepts arbitrary attributes."""

    def test_on_to_on_stages_reload_on_running_detector(self, services: AppRuntimeServices) -> None:
        detector = _FakeDetector()
        services._person_detector = detector
        new_cfg = DetectionConfig(enabled=True, confidence=0.85)

        services.apply_detection_change(new_cfg)

        assert detector.reload_calls == [new_cfg]
        assert services._person_detector is detector

    def test_on_to_off_stops_and_drops_reference(self, services: AppRuntimeServices) -> None:
        detector = _FakeDetector()
        services._person_detector = detector
        new_cfg = DetectionConfig(enabled=False)

        services.apply_detection_change(new_cfg)

        assert detector.stopped is True
        assert services._person_detector is None

    def test_off_to_off_is_noop(self, services: AppRuntimeServices) -> None:
        services._person_detector = None

        services.apply_detection_change(DetectionConfig(enabled=False))

        assert services._person_detector is None

    def test_off_to_on_is_dispatcher_responsibility(self, services: AppRuntimeServices) -> None:
        """The orchestrator is a no-op for ``off→on`` because wiring the
        appsink into a fresh detector requires receiver-side plumbing
        only ``init_video`` does today. The dispatcher catches this case
        and falls back to ``request_restart()`` instead."""
        services._person_detector = None

        services.apply_detection_change(DetectionConfig(enabled=True))

        assert services._person_detector is None


# --------------------------------------------------------------------------- #
# apply_window_size_change
# --------------------------------------------------------------------------- #


class _FakeCanvas:
    """Minimal canvas stand-in that records ``apply_window_size`` calls."""

    def __init__(self) -> None:
        self.size_calls: list[tuple[int, int]] = []

    def apply_window_size(self, width: int, height: int) -> None:
        self.size_calls.append((width, height))


class TestApplyWindowSizeChange:
    def test_forwards_dimensions_to_canvas(self, services: AppRuntimeServices) -> None:
        canvas = _FakeCanvas()
        services._app._canvas = canvas

        services.apply_window_size_change(1920, 1080)

        assert canvas.size_calls == [(1920, 1080)]

    def test_clamps_zero_or_negative_dimensions_to_one(self, services: AppRuntimeServices) -> None:
        """Hand-edited TOML can produce non-positive integers; mirror
        ``init_canvas``'s ``max(1, int(...))`` clamp so a bad value
        doesn't propagate into a Gdk geometry call."""
        canvas = _FakeCanvas()
        services._app._canvas = canvas

        services.apply_window_size_change(0, -100)

        assert canvas.size_calls == [(1, 1)]

    def test_coerces_float_dimensions_to_int(self, services: AppRuntimeServices) -> None:
        """The clamp also runs ``int(...)``; mirror ``init_canvas``'s
        coercion so an unexpected non-int from a custom config
        loader doesn't raise inside the Gdk call site."""
        canvas = _FakeCanvas()
        services._app._canvas = canvas

        services.apply_window_size_change(1920.7, 1080.4)

        assert canvas.size_calls == [(1920, 1080)]


# --------------------------------------------------------------------------- #
# Diagnostics I/O providers
# --------------------------------------------------------------------------- #


class _RingEntry:
    """Minimal stand-in for ``osc.transmitter.RingBufferEntry`` – the
    provider only reads ``ts`` / ``status`` / ``address`` / ``args`` /
    ``error``."""

    def __init__(
        self,
        *,
        ts: float,
        status: str,
        address: str = "",
        args: tuple = (),
        error: str = "",
    ) -> None:
        self.ts = ts
        self.status = status
        self.address = address
        self.args = args
        self.error = error


class _FakeOscManager:
    """Stand-in exposing the two methods ``_recent_osc_sends_provider``
    reaches: ``row_ids()`` and ``ring_buffer_for(row_id)``."""

    def __init__(self, rings: dict[str, list]) -> None:
        self._rings = rings

    def row_ids(self) -> list[str]:
        return list(self._rings)

    def ring_buffer_for(self, row_id: str) -> list | None:
        return self._rings.get(row_id)


class TestDiagnosticsIoProviders:
    # -- recent_osc_sends ---------------------------------------------------

    def test_recent_osc_sends_none_manager(self, services: AppRuntimeServices) -> None:
        services._osc_transmitter_manager = None
        assert services._recent_osc_sends_provider() == []

    def test_recent_osc_sends_flattens_sorted(
        self,
        services: AppRuntimeServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import time as _time

        now = _time.monotonic()
        services._osc_transmitter_manager = _FakeOscManager(  # type: ignore[assignment]
            {
                "rowA": [
                    _RingEntry(ts=now - 5.0, status="sent", address="/a", args=(1,)),
                ],
                "rowB": [
                    _RingEntry(ts=now - 1.0, status="sent", address="/b", args=(2,)),
                ],
                "rowEmpty": [],  # quiet row – exercises the skip-empty path
            }
        )
        out = services._recent_osc_sends_provider()
        # Most-recent-first across rows, in the renderer's dict shape.
        assert [e["address"] for e in out] == ["/b", "/a"]
        assert out[0]["args"] == (2,)
        assert all(e["age_s"] >= 0.0 for e in out)
        assert {"age_s", "status", "address", "args"} == set(out[0])

    def test_recent_osc_sends_skip_folds_error_into_address(
        self,
        services: AppRuntimeServices,
    ) -> None:
        import time as _time

        services._osc_transmitter_manager = _FakeOscManager(  # type: ignore[assignment]
            {
                "row": [
                    _RingEntry(
                        ts=_time.monotonic(),
                        status="skipped",
                        address="",
                        error="no default marker configured",
                    ),
                ],
            }
        )
        out = services._recent_osc_sends_provider()
        assert out[0]["status"] == "skipped"
        assert out[0]["address"] == "no default marker configured"

    def test_recent_osc_sends_truncates(
        self,
        services: AppRuntimeServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import time as _time

        monkeypatch.setattr(services_module, "_RECENT_OSC_SENDS_LIMIT", 3)
        now = _time.monotonic()
        ring = [_RingEntry(ts=now - i, status="sent", address=f"/{i}") for i in range(10)]
        services._osc_transmitter_manager = _FakeOscManager({"row": ring})  # type: ignore[assignment]
        assert len(services._recent_osc_sends_provider()) == 3

    # -- osc_listener_status ------------------------------------------------

    def test_osc_listener_status_reads_service(self, services: AppRuntimeServices) -> None:
        """Provider passes the live ``OscService`` listener status straight
        through; idle service → the default (stopped) shape."""
        assert services._osc_listener_status_provider() == {
            "port": None,
            "multicast_group": "",
            "multicast_joined": False,
            "allowed_sender_ips": [],
        }

    # -- recent_midi_events -------------------------------------------------

    def test_recent_midi_events_shape(
        self,
        services: AppRuntimeServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import time as _time

        from openfollow.input.midi import MidiEvent

        # Pin monotonic so age_s is deterministic (the prior assertion
        # compared age_s to itself – a tautology that could never fail).
        monkeypatch.setattr(_time, "monotonic", lambda: 1000.0)
        ev = MidiEvent(
            type="control_change",
            channel=3,
            number=7,
            value=64,
            patch_id=2,
            timestamp=999.5,
        )
        monkeypatch.setattr(services._midi, "recent_events", lambda: [ev])
        out = services._recent_midi_events_provider()
        assert out == [
            {
                "age_s": pytest.approx(0.5),
                "patch_id": 2,
                "type": "control_change",
                "channel": 3,
                "number": 7,
                "value": 64,
            }
        ]

    def test_recent_midi_events_empty_by_default(
        self,
        services: AppRuntimeServices,
    ) -> None:
        # Real subsystem, no events pumped → empty list (no rtmidi in tests).
        assert services._recent_midi_events_provider() == []

    # -- midi_port_names ----------------------------------------------------

    def test_midi_port_names(self, services: AppRuntimeServices) -> None:
        # Provider reads the hotplug cache (not a live discover): two raw names
        # with volatile ALSA suffixes normalize + dedup to one; "" drops out.
        services._midi._last_input_names = frozenset({"nanoKONTROL2 24:0", "nanoKONTROL2 24:1", ""})
        assert services._midi_port_names_provider() == ["nanoKONTROL2"]

    def test_midi_port_names_default_empty(self, services: AppRuntimeServices) -> None:
        assert services._midi_port_names_provider() == []

    # -- camera_names -------------------------------------------------------

    def test_camera_names_bridges_registry(
        self,
        services: AppRuntimeServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import openfollow.video.inputs as inputs_mod

        monkeypatch.setattr(inputs_mod, "usb_camera_names", lambda: ["USB Capture HDMI"])
        assert services._camera_names_provider() == ["USB Capture HDMI"]


# --------------------------------------------------------------------------- #
# swap_video – live video pipeline rebuild
# --------------------------------------------------------------------------- #


class _FakeReceiver:
    """Recording stand-in for ``GstNativeSinkReceiver`` exposing the
    surface ``swap_video`` reaches: ``_source_type``, ``_input_config``,
    and ``swap_input(source_type, input_config)``."""

    def __init__(
        self,
        *,
        source_type: str = "rtsp",
        input_config: dict[str, Any] | None = None,
        swap_raises: list[Exception] | None = None,
    ) -> None:
        self._source_type = source_type
        self._input_config = dict(input_config or {})
        self.swap_calls: list[tuple[str, dict[str, Any]]] = []
        # Pop one exception per ``swap_input`` call. Empty list = no
        # raises. ``[exc1, None]`` = first call raises, second succeeds.
        self._swap_raises = list(swap_raises or [])

    def swap_input(self, source_type: str, input_config: dict[str, Any]) -> None:
        self.swap_calls.append((source_type, dict(input_config)))
        if self._swap_raises:
            exc = self._swap_raises.pop(0)
            if exc is not None:
                raise exc
        # Mirror the real receiver's contract: on success the receiver
        # picks up the new plugin/config.
        self._source_type = source_type
        self._input_config = dict(input_config)


class TestSwapVideo:
    def test_unknown_video_source_type_raises_and_does_not_touch_receiver(
        self,
        services: AppRuntimeServices,
    ) -> None:
        """Validation lives at the orchestrator boundary – the
        receiver never sees a bad type. Mirrors the
        ``swap_input``-level guard so the dispatcher can revert
        config without leaving the receiver mid-swap."""
        from openfollow.configuration import AppConfig

        receiver = _FakeReceiver(source_type="rtsp")
        services._app._video_receiver = receiver  # type: ignore[assignment]
        new_cfg = AppConfig(video_source_type="not-a-real-plugin")

        with pytest.raises(ValueError, match="Unknown video input type"):
            services.swap_video(new_cfg)

        assert receiver.swap_calls == []
        assert receiver._source_type == "rtsp"

    def test_forwards_new_plugin_and_config_to_receiver(
        self,
        services: AppRuntimeServices,
    ) -> None:
        """Happy path: extract plugin field values from the new
        ``AppConfig`` via ``Plugin.get_config_field_values`` and hand
        them to ``swap_input`` alongside the new source type."""
        from openfollow.configuration import AppConfig

        receiver = _FakeReceiver(
            source_type="rtsp",
            input_config={"rtsp_url": "rtsp://old/x"},
        )
        services._app._video_receiver = receiver  # type: ignore[assignment]
        new_cfg = AppConfig(
            video_source_type="rtsp",
            rtsp_url="rtsp://new/y",
        )

        services.swap_video(new_cfg)

        assert len(receiver.swap_calls) == 1
        called_type, called_config = receiver.swap_calls[0]
        assert called_type == "rtsp"
        assert called_config == {"rtsp_url": "rtsp://new/y"}

    def test_failure_attempts_rollback_to_prior_plugin_and_config(
        self,
        services: AppRuntimeServices,
    ) -> None:
        """On forward swap failure, orchestrator restores the prior
        plugin/config and re-raises to revert app config."""
        from openfollow.configuration import AppConfig

        # Forward swap raises; rollback succeeds.
        receiver = _FakeReceiver(
            source_type="rtsp",
            input_config={"rtsp_url": "rtsp://orig/x"},
            swap_raises=[RuntimeError("new pipeline failed"), None],
        )
        services._app._video_receiver = receiver  # type: ignore[assignment]
        new_cfg = AppConfig(
            video_source_type="srt",
            srt_host="srt://10.0.0.5:5000",
        )

        with pytest.raises(RuntimeError, match="new pipeline failed"):
            services.swap_video(new_cfg)

        # First call: forward swap with new cfg.
        assert receiver.swap_calls[0] == ("srt", {"srt_host": "srt://10.0.0.5:5000"})
        # Second call: rollback to prior plugin/config.
        assert receiver.swap_calls[1] == (
            "rtsp",
            {"rtsp_url": "rtsp://orig/x"},
        )
        # Receiver reference preserved on rollback-success – the
        # runtime is alive on the prior cfg.
        assert services._app._video_receiver is receiver

    def test_rollback_failure_keeps_receiver_attached(
        self,
        services: AppRuntimeServices,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        from openfollow.configuration import AppConfig

        receiver = _FakeReceiver(
            source_type="rtsp",
            input_config={"rtsp_url": "rtsp://orig/x"},
            swap_raises=[
                RuntimeError("primary failure"),
                RuntimeError("rollback also broke"),
            ],
        )
        services._app._video_receiver = receiver  # type: ignore[assignment]
        new_cfg = AppConfig(
            video_source_type="srt",
            srt_host="srt://10.0.0.5:5000",
        )

        with caplog.at_level("ERROR", logger="openfollow.services"):
            with pytest.raises(RuntimeError, match="primary failure"):
                services.swap_video(new_cfg)

        # Receiver stays attached so runtime code that reads
        # ``app._video_receiver.<x>`` on the next tick doesn't
        # AttributeError. The error is surfaced via the log instead.
        assert services._app._video_receiver is receiver
        assert any("rollback to prior cfg failed" in r.message for r in caplog.records)

    def test_pipeline_stuck_error_skips_rollback_and_keeps_receiver(
        self,
        services: AppRuntimeServices,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """``PipelineStuckError`` skips rollback (unsafe) and keeps the
        receiver reference since ``swap_input`` only updates after success."""
        from openfollow.configuration import AppConfig
        from openfollow.video.receiver import PipelineStuckError

        receiver = _FakeReceiver(
            source_type="rtsp",
            input_config={"rtsp_url": "rtsp://orig/x"},
            swap_raises=[
                PipelineStuckError("prior pipeline stuck"),
                # If the orchestrator wrongly attempted a rollback,
                # the second swap_input call would land here. The
                # test asserts only ONE swap_input was made.
                None,
            ],
        )
        services._app._video_receiver = receiver  # type: ignore[assignment]
        new_cfg = AppConfig(
            video_source_type="srt",
            srt_host="srt://10.0.0.5:5000",
        )

        with caplog.at_level("ERROR", logger="openfollow.services"):
            with pytest.raises(PipelineStuckError, match="prior pipeline stuck"):
                services.swap_video(new_cfg)

        # Forward swap attempted exactly once – no rollback call.
        assert len(receiver.swap_calls) == 1
        # Receiver stays attached so the next tick doesn't crash on
        # a None reference.
        assert services._app._video_receiver is receiver
        assert any("blocked by stuck prior pipeline" in r.message for r in caplog.records)

    def test_raises_when_video_receiver_is_none(
        self,
        services: AppRuntimeServices,
    ) -> None:
        """Raise if ``_video_receiver`` is None to prevent config from
        diverging when no runtime swap occurs."""
        from openfollow.configuration import AppConfig

        services._app._video_receiver = None
        new_cfg = AppConfig(video_source_type="srt")

        with pytest.raises(RuntimeError, match="not initialised"):
            services.swap_video(new_cfg)


# --------------------------------------------------------------------------- #
# swap_detector – (live detection pipeline rebuild)
# --------------------------------------------------------------------------- #


class _RecordingReceiver:
    """Recording stand-in for ``GstNativeSinkReceiver`` exposing the
    surface ``swap_detector`` reaches: ``swap_detection_branch``."""

    def __init__(
        self,
        *,
        swap_raises: list[Any] | None = None,
        initial_detector: Any = None,
    ) -> None:
        # Each entry is either an Exception (raise) or None (succeed).
        self._swap_raises = list(swap_raises or [])
        self.swap_detector_calls: list[Any] = []
        # Mirror _detector for skip-rebuild checks. Assign before calling
        # so failures leave the new reference attached.
        self._detector: Any = initial_detector

    def swap_detection_branch(self, new_detector: Any) -> None:
        self.swap_detector_calls.append(new_detector)
        self._detector = new_detector
        if self._swap_raises:
            exc = self._swap_raises.pop(0)
            if exc is not None:
                raise exc


class _FakePersonDetector:
    """Recording PersonDetector double – captures the cfg passed to
    its constructor and exposes ``available`` / ``stop`` so tests can
    drive both the available and unavailable sub-branches.

    Patched in via ``monkeypatch.setattr`` on the import line inside
    ``swap_detector`` so the real ``PersonDetector`` constructor (which
    pokes at cv2 / model files) never runs."""

    instances: list[_FakePersonDetector] = []
    available_default: bool = True

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self.available = type(self).available_default
        self.stop_calls = 0
        type(self).instances.append(self)

    def stop(self) -> None:
        self.stop_calls += 1


class TestSwapDetector:
    def _setup(
        self,
        services: AppRuntimeServices,
        monkeypatch: pytest.MonkeyPatch,
        *,
        receiver: _RecordingReceiver | None = None,
        existing_detector: Any = None,
        available: bool = True,
    ) -> tuple[_RecordingReceiver, type[_FakePersonDetector]]:
        receiver = receiver or _RecordingReceiver()
        services._app._video_receiver = receiver  # type: ignore[assignment]
        services._person_detector = existing_detector
        from openfollow.video import detection as detection_mod

        _FakePersonDetector.instances = []
        _FakePersonDetector.available_default = available
        monkeypatch.setattr(detection_mod, "PersonDetector", _FakePersonDetector)
        return receiver, _FakePersonDetector

    def test_off_to_on_constructs_and_swaps(
        self,
        services: AppRuntimeServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Off→on case: no prior detector. Construct a fresh
        ``PersonDetector`` from the new cfg, hand it to the receiver
        for a pipeline rebuild, and update ``_person_detector``."""
        receiver, detector_cls = self._setup(services, monkeypatch)
        new_cfg = DetectionConfig(enabled=True, confidence=0.85)

        services.swap_detector(new_cfg)

        # One PersonDetector constructed with the new cfg.
        assert len(detector_cls.instances) == 1
        new_detector = detector_cls.instances[0]
        assert new_detector.cfg is new_cfg
        # Receiver got the fresh detector.
        assert receiver.swap_detector_calls == [new_detector]
        # Orchestrator now references the new detector.
        assert services._person_detector is new_detector

    def test_off_to_on_when_new_backend_unavailable_just_replaces_reference(
        self,
        services: AppRuntimeServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Off→on where the freshly-constructed detector has
        ``available=False`` (e.g. ``onnxruntime`` not installed).
        No pipeline change is meaningful – replacing the reference
        is enough to surface the latest cfg via runtime stats."""
        receiver, detector_cls = self._setup(
            services,
            monkeypatch,
            available=False,
        )
        new_cfg = DetectionConfig(enabled=True, confidence=0.9)

        services.swap_detector(new_cfg)

        new_detector = detector_cls.instances[0]
        assert receiver.swap_detector_calls == []
        assert services._person_detector is new_detector

    def test_unavailable_to_unavailable_replaces_reference_and_stops_old(
        self,
        services: AppRuntimeServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Re-probe sub-branch where the new backend ALSO fails to
        load. Replace the reference (so subsequent reload passes
        carry the latest cfg + ``missing_deps``) but skip the
        pipeline rebuild – the prior pipeline never wired an
        appsink for an unavailable detector either. The OLD detector
        is stopped so its worker (if any) doesn't keep running on
        stale config."""
        old_detector = _FakeDetector(available=False)
        receiver, detector_cls = self._setup(
            services,
            monkeypatch,
            existing_detector=old_detector,
            available=False,
        )
        new_cfg = DetectionConfig(enabled=True, confidence=0.9)

        services.swap_detector(new_cfg)

        new_detector = detector_cls.instances[0]
        # No pipeline rebuild attempted – both ends are unavailable
        # so the pipeline has no detection branch to drop or wire.
        assert receiver.swap_detector_calls == []
        # Reference replaced.
        assert services._person_detector is new_detector
        # Old detector stopped to drop any stale worker.
        assert old_detector.stopped is True

    def test_unavailable_to_available_swaps_pipeline(
        self,
        services: AppRuntimeServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Re-probe sub-branch where the new backend DOES load. Same
        path as off→on – the pipeline is rebuilt with the new
        detector wired into the appsink."""
        old_detector = _FakeDetector(available=False)
        receiver, detector_cls = self._setup(
            services,
            monkeypatch,
            existing_detector=old_detector,
            available=True,
        )
        new_cfg = DetectionConfig(enabled=True, confidence=0.9)

        services.swap_detector(new_cfg)

        new_detector = detector_cls.instances[0]
        assert receiver.swap_detector_calls == [new_detector]
        assert services._person_detector is new_detector

    def test_available_to_unavailable_rebuilds_pipeline_to_drop_branch(
        self,
        services: AppRuntimeServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When the prior detector was running (``available=True``)
        but the new one fails to load, the pipeline still has a
        detection branch wired into the OLD appsink. Rebuild via
        ``swap_detection_branch`` to drop the now-orphaned branch –
        otherwise the pipeline keeps feeding frames into a queue
        that nothing reads."""
        old_detector = _FakeDetector(available=True)
        receiver, detector_cls = self._setup(
            services,
            monkeypatch,
            existing_detector=old_detector,
            available=False,
        )
        new_cfg = DetectionConfig(enabled=True, confidence=0.9)

        services.swap_detector(new_cfg)

        new_detector = detector_cls.instances[0]
        # Pipeline rebuilt – the new (unavailable) detector takes
        # ownership of the receiver's detector slot, and the
        # rebuilt pipeline omits the detection branch because
        # ``need_detection`` is False for an unavailable detector.
        assert receiver.swap_detector_calls == [new_detector]
        assert services._person_detector is new_detector

    def test_inference_size_change_swaps_pipeline_against_fresh_detector(
        self,
        services: AppRuntimeServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``inference_size`` change with a running detector: rebuild
        the pipeline so the appsink caps match the new detector's
        ``input_resolution``."""
        old_detector = _FakeDetector()
        receiver, detector_cls = self._setup(
            services,
            monkeypatch,
            existing_detector=old_detector,
        )
        new_cfg = DetectionConfig(enabled=True, inference_size=640)

        services.swap_detector(new_cfg)

        new_detector = detector_cls.instances[0]
        assert receiver.swap_detector_calls == [new_detector]
        assert services._person_detector is new_detector

    def test_failure_attempts_rollback_to_prior_detector(
        self,
        services: AppRuntimeServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """On forward swap failure, orchestrator rebuilds against prior
        detector and re-raises to revert config."""
        old_detector = _FakeDetector()
        # Forward swap raises; rollback succeeds.
        receiver = _RecordingReceiver(
            swap_raises=[RuntimeError("new pipeline failed"), None],
        )
        _, detector_cls = self._setup(
            services,
            monkeypatch,
            receiver=receiver,
            existing_detector=old_detector,
        )
        new_cfg = DetectionConfig(enabled=True, confidence=0.95)

        with pytest.raises(RuntimeError, match="new pipeline failed"):
            services.swap_detector(new_cfg)

        new_detector = detector_cls.instances[0]
        # First call: forward swap with the new detector.
        assert receiver.swap_detector_calls[0] is new_detector
        # Second call: rollback to prior detector.
        assert receiver.swap_detector_calls[1] is old_detector
        # Reference restored to the old detector after rollback-success.
        assert services._person_detector is old_detector

    def test_rollback_failure_clears_detector_reference(
        self,
        services: AppRuntimeServices,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When BOTH the forward swap AND the rollback raise, the
        receiver is in a known-bad state. Drop the detector
        reference so subsequent reload passes route through the
        off→on branch again instead of trying to call
        ``reload_config`` on a half-applied detector."""
        old_detector = _FakeDetector()
        receiver = _RecordingReceiver(
            swap_raises=[
                RuntimeError("primary failure"),
                RuntimeError("rollback also broke"),
            ],
        )
        _, _ = self._setup(
            services,
            monkeypatch,
            receiver=receiver,
            existing_detector=old_detector,
        )
        new_cfg = DetectionConfig(enabled=True, confidence=0.95)

        with caplog.at_level("ERROR", logger="openfollow.services"):
            with pytest.raises(RuntimeError, match="primary failure"):
                services.swap_detector(new_cfg)

        # Rollback was attempted – both swap calls happened.
        assert len(receiver.swap_detector_calls) == 2
        # Detector reference cleared so subsequent reload pass starts
        # fresh through the off→on branch.
        assert services._person_detector is None
        assert any("rollback to prior detector failed" in r.message for r in caplog.records)

    def test_skip_rebuild_blocked_when_receiver_still_has_available_branch(
        self,
        services: AppRuntimeServices,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        old_detector = _FakeDetector(available=True)
        receiver = _RecordingReceiver(
            swap_raises=[
                RuntimeError("primary failure"),
                RuntimeError("rollback also broke"),
            ],
        )
        _, detector_cls = self._setup(
            services,
            monkeypatch,
            receiver=receiver,
            existing_detector=old_detector,
        )

        # Step 1: drive the rollback-failure path so the orchestrator
        # clears ``self._person_detector`` while the receiver retains
        # an available detector reference (assignment happens inside
        # ``swap_detection_branch`` before the raise).
        with caplog.at_level("ERROR", logger="openfollow.services"):
            with pytest.raises(RuntimeError, match="primary failure"):
                services.swap_detector(
                    DetectionConfig(enabled=True, confidence=0.9),
                )

        assert services._person_detector is None
        assert receiver._detector is not None
        assert receiver._detector.available is True

        # Step 2: a follow-up swap to a fresh unavailable detector.
        # Without the cross-check fix, ``old_was_available`` would be
        # False (services view) and the fast path would skip the
        # rebuild, leaving the orphan branch wired.
        _FakePersonDetector.available_default = False
        services.swap_detector(
            DetectionConfig(enabled=True, confidence=0.5),
        )

        new_detector = detector_cls.instances[-1]
        # Three swap calls total: forward (raised), rollback (raised),
        # and the rebuild from the follow-up call. Without the fix the
        # third call would never reach ``swap_detection_branch``.
        assert len(receiver.swap_detector_calls) == 3
        assert receiver.swap_detector_calls[-1] is new_detector
        assert services._person_detector is new_detector

    def test_pipeline_stuck_error_skips_rollback(
        self,
        services: AppRuntimeServices,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """``PipelineStuckError`` is the one failure mode where
        rollback is unsafe – the rollback would re-attach the shared
        sink to a new pipeline while the old one is still alive.
        Mirrors ``swap_video``: re-raise WITHOUT a rollback attempt."""
        from openfollow.video.receiver import PipelineStuckError

        old_detector = _FakeDetector()
        receiver = _RecordingReceiver(
            swap_raises=[PipelineStuckError("prior pipeline stuck"), None],
        )
        _, _ = self._setup(
            services,
            monkeypatch,
            receiver=receiver,
            existing_detector=old_detector,
        )
        new_cfg = DetectionConfig(enabled=True, confidence=0.95)

        with caplog.at_level("ERROR", logger="openfollow.services"):
            with pytest.raises(PipelineStuckError, match="prior pipeline stuck"):
                services.swap_detector(new_cfg)

        # Forward swap attempted exactly once – no rollback call.
        assert len(receiver.swap_detector_calls) == 1
        # Detector reference NOT mutated – the swap never reached the
        # update line, and skipping a rollback means the prior detector
        # is still attached. The dispatcher reverts stored config to
        # match.
        assert services._person_detector is old_detector
        assert any("blocked by stuck prior pipeline" in r.message for r in caplog.records)

    def test_raises_when_video_receiver_is_none(
        self,
        services: AppRuntimeServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If ``_video_receiver`` is None when the dispatcher routes
        a detection swap here, the swap can't apply. Silently
        returning would let ``_apply_with_fallback`` treat it as a
        success and commit ``app._config.detection`` to the new cfg
        even though no runtime change happened. Raise so the
        dispatcher reverts. Mirrors ``swap_video``'s same guard."""
        services._app._video_receiver = None
        new_cfg = DetectionConfig(enabled=True, confidence=0.9)

        with pytest.raises(RuntimeError, match="not initialised"):
            services.swap_detector(new_cfg)
