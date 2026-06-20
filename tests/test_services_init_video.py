# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Hermetic coverage of :meth:`AppRuntimeServices.init_video`.

``init_video`` is the lifecycle seam wiring overlay renderer, stats
collector, optional detector, preview/snapshot providers, video-input
selection, GstNativeSinkReceiver construction, pipeline bring-up, and
receiver/detector start calls. Uses shared FakeCanvas and monkeypatched
subsystem factories on ``services_module``.

Each test exercises exactly one branch of ``init_video`` to keep the
assertions sharp:

* detection disabled vs enabled
* detection enabled but dependencies missing
* input plugin registered vs absent
* ``create_pipeline`` success vs exception
* ``get_sink_widget`` returns widget vs ``None``
* canvas with / without ``attach_hud`` support
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

import openfollow.services as services_module
from openfollow.configuration import AppConfig, DetectionConfig
from openfollow.services import AppRuntimeServices
from tests._fake_gtk import FakeCanvas

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _FakeOverlayRenderer:
    def __init__(self) -> None:
        self.state: Any = None

    def draw(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _FakeSystemStats:
    instances: list[_FakeSystemStats] = []

    def __init__(self, *, update_interval: float, preferred_ip: str) -> None:
        self.update_interval = update_interval
        self.preferred_ip = preferred_ip
        _FakeSystemStats.instances.append(self)


class _FakePreviewProvider:
    instances: list[_FakePreviewProvider] = []

    def __init__(self) -> None:
        _FakePreviewProvider.instances.append(self)


class _FakeSnapshotProvider:
    instances: list[_FakeSnapshotProvider] = []

    def __init__(self) -> None:
        _FakeSnapshotProvider.instances.append(self)


class _FakePersonDetector:
    instances: list[_FakePersonDetector] = []

    def __init__(self, cfg: DetectionConfig) -> None:
        self.cfg = cfg
        self.started = False
        _FakePersonDetector.instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False


class _FakeReceiver:
    """Stand-in for :class:`GstNativeSinkReceiver`.

    Configurable per-test to exercise the ``create_pipeline`` success /
    failure branches and the widget-present / widget-absent paths.
    """

    def __init__(
        self,
        *,
        source_type: str,
        input_config: dict[str, Any],
        overlay_renderer: Any,
        on_widget_changed: Any,
        config_path: str,
        detector: Any,
        preview_provider: Any,
        snapshot_provider: Any,
        stall_timeout: Any = None,
        heal_interval: Any = None,
    ) -> None:
        self.source_type = source_type
        self.input_config = input_config
        self.overlay_renderer = overlay_renderer
        self.on_widget_changed = on_widget_changed
        self.config_path = config_path
        self.detector = detector
        self.preview_provider = preview_provider
        self.snapshot_provider = snapshot_provider
        self.stall_timeout = stall_timeout
        self.heal_interval = heal_interval
        self.create_called = False
        self.started = False
        self.widget: Any = object()
        self._raise_on_create: Exception | None = None

    def set_pipeline_failure(self, exc: Exception) -> None:
        self._raise_on_create = exc

    def set_widget(self, widget: Any) -> None:
        self.widget = widget

    def create_pipeline(self) -> None:
        self.create_called = True
        if self._raise_on_create is not None:
            raise self._raise_on_create

    def get_sink_widget(self) -> Any:
        return self.widget

    def start(self) -> None:
        self.started = True


class _RecordingReceiverFactory:
    """Captures the most recent ``_FakeReceiver`` for post-call inspection."""

    def __init__(self) -> None:
        self.last: _FakeReceiver | None = None
        self._pending_exc: Exception | None = None
        self._pending_widget: Any = object()

    def plant_pipeline_failure(self, exc: Exception) -> None:
        self._pending_exc = exc

    def plant_widget(self, widget: Any) -> None:
        self._pending_widget = widget

    def __call__(self, **kwargs: Any) -> _FakeReceiver:
        recv = _FakeReceiver(**kwargs)
        if self._pending_exc is not None:
            recv.set_pipeline_failure(self._pending_exc)
        recv.set_widget(self._pending_widget)
        self.last = recv
        return recv


class _FakeInputCls:
    """Minimal stand-in for a :class:`VideoInputBase` subclass."""

    @classmethod
    def get_config_field_values(cls, cfg: Any) -> dict[str, Any]:  # noqa: ARG003
        return {"kind": "fake"}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _dummy_app(cfg: AppConfig | None = None) -> SimpleNamespace:
    cfg = cfg or AppConfig(psn_system_name="Phase8", psn_source_iface="eth0")
    return SimpleNamespace(
        _config=cfg,
        _config_path="/tmp/openfollow-phase8.toml",
        _canvas=FakeCanvas(),
        _camera=None,
        _video_receiver=None,
        _video_logged=True,
    )


@pytest.fixture
def services(monkeypatch: pytest.MonkeyPatch) -> AppRuntimeServices:
    """Build an :class:`AppRuntimeServices` wired to per-test fakes."""
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
    monkeypatch.setattr(services_module, "CairoOverlayRenderer", _FakeOverlayRenderer)
    monkeypatch.setattr(services_module, "SystemStatsCollector", _FakeSystemStats)
    # Mock psutil so the dummy config's psn_source_iface resolves to stable IPv4 for HUD display.
    import socket as _socket
    from types import SimpleNamespace

    from openfollow import net_utils

    monkeypatch.setattr(
        net_utils.psutil,
        "net_if_addrs",
        lambda: {
            "eth0": [SimpleNamespace(family=_socket.AF_INET, address="10.0.0.5")],
        },
    )
    return AppRuntimeServices(_dummy_app())


@pytest.fixture
def recv_factory(monkeypatch: pytest.MonkeyPatch) -> _RecordingReceiverFactory:
    factory = _RecordingReceiverFactory()
    monkeypatch.setattr(services_module, "GstNativeSinkReceiver", factory)
    return factory


@pytest.fixture(autouse=True)
def _reset_fake_instances() -> None:
    _FakeSystemStats.instances = []
    _FakePreviewProvider.instances = []
    _FakeSnapshotProvider.instances = []
    _FakePersonDetector.instances = []


@pytest.fixture
def patched_subsystems(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch detection + preview + input-plugin imports used inside
    ``init_video`` so tests don't pull in real GStreamer / detector deps.
    """
    import openfollow.video.detection as detection_module
    import openfollow.video.inputs as inputs_module
    import openfollow.video.preview as preview_module

    monkeypatch.setattr(detection_module, "PersonDetector", _FakePersonDetector)
    monkeypatch.setattr(detection_module, "check_detection_dependencies", lambda _cfg: [])
    monkeypatch.setattr(preview_module, "PreviewProvider", _FakePreviewProvider)
    monkeypatch.setattr(preview_module, "SnapshotProvider", _FakeSnapshotProvider)
    monkeypatch.setattr(inputs_module, "get_input_class", lambda _kind: _FakeInputCls)


# --------------------------------------------------------------------------- #
# init_video – detection disabled (happy path)
# --------------------------------------------------------------------------- #


class TestInitVideoDetectionDisabled:
    def test_wires_renderer_stats_preview_receiver_and_starts(
        self,
        services: AppRuntimeServices,
        recv_factory: _RecordingReceiverFactory,
        patched_subsystems: None,
    ) -> None:
        services.init_video()

        assert isinstance(services._overlay_renderer, _FakeOverlayRenderer)
        # System stats use a lazy callable for the preferred IP so it reflects the current bind.
        # A literal capture would lag since init_video runs before wait_for_source_ip.
        assert len(_FakeSystemStats.instances) == 1
        provider = _FakeSystemStats.instances[0].preferred_ip
        assert callable(provider)
        assert provider() == "10.0.0.5"
        assert _FakeSystemStats.instances[0].update_interval == 1.0
        # detection disabled – no detector instantiated
        assert services._person_detector is None
        assert _FakePersonDetector.instances == []
        # preview providers constructed
        assert len(_FakePreviewProvider.instances) == 1
        assert len(_FakeSnapshotProvider.instances) == 1

        recv = recv_factory.last
        assert recv is not None
        assert recv.source_type == services._app._config.video_source_type
        assert recv.input_config == {"kind": "fake"}
        assert recv.overlay_renderer is services._overlay_renderer
        assert recv.detector is None
        assert recv.preview_provider is _FakePreviewProvider.instances[0]
        assert recv.snapshot_provider is _FakeSnapshotProvider.instances[0]
        assert recv.config_path == "/tmp/openfollow-phase8.toml"
        assert recv.on_widget_changed == services._app._canvas.embed_widget

        # pipeline brought up + widget embedded + HUD attached + receiver started
        assert recv.create_called is True
        assert recv.started is True
        assert services._app._video_receiver is recv
        assert services._app._video_logged is False
        canvas = services._app._canvas
        assert canvas.hud_draw_fn == services._overlay_renderer.draw
        assert canvas.embed_widget_calls == [recv.widget]

    def test_widget_none_skips_embed_but_keeps_attach_hud(
        self,
        services: AppRuntimeServices,
        recv_factory: _RecordingReceiverFactory,
        patched_subsystems: None,
    ) -> None:
        """``get_sink_widget`` returning ``None`` – no embed call, HUD still attached."""
        recv_factory.plant_widget(None)
        services.init_video()
        canvas = services._app._canvas
        assert canvas.embed_widget_calls == []
        assert canvas.hud_draw_fn == services._overlay_renderer.draw


# --------------------------------------------------------------------------- #
# init_video – detection enabled
# --------------------------------------------------------------------------- #


class TestInitVideoDetectionEnabled:
    def _enabled_services(self, monkeypatch: pytest.MonkeyPatch) -> AppRuntimeServices:
        cfg = AppConfig(psn_system_name="Phase8")
        cfg = replace(cfg, detection=replace(cfg.detection, enabled=True))
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
        monkeypatch.setattr(services_module, "CairoOverlayRenderer", _FakeOverlayRenderer)
        monkeypatch.setattr(services_module, "SystemStatsCollector", _FakeSystemStats)
        return AppRuntimeServices(_dummy_app(cfg))

    def test_detector_constructed_and_started_when_deps_present(
        self,
        monkeypatch: pytest.MonkeyPatch,
        recv_factory: _RecordingReceiverFactory,
        patched_subsystems: None,
    ) -> None:
        svc = self._enabled_services(monkeypatch)
        svc.init_video()
        assert isinstance(svc._person_detector, _FakePersonDetector)
        assert svc._person_detector.started is True
        assert recv_factory.last is not None
        assert recv_factory.last.detector is svc._person_detector

    def test_detector_not_adopted_when_receiver_start_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        recv_factory: _RecordingReceiverFactory,
        patched_subsystems: None,
    ) -> None:
        """If ``receiver.start()`` raises, ``init_video`` aborts before the
        detector is adopted, so the orchestrator isn't left holding a
        detector whose appsink was never wired and whose worker never
        started (the assignment is deferred until the receiver is up)."""

        def _boom(self: _FakeReceiver) -> None:
            raise RuntimeError("start boom")

        monkeypatch.setattr(_FakeReceiver, "start", _boom)
        svc = self._enabled_services(monkeypatch)
        with pytest.raises(RuntimeError, match="start boom"):
            svc.init_video()
        # Detector was constructed and handed to the receiver, but the
        # orchestrator never adopted it and never started its worker.
        assert svc._person_detector is None
        assert recv_factory.last is not None
        assert recv_factory.last.detector is not None
        assert recv_factory.last.detector.started is False

    def test_missing_deps_warn_and_skip_detector(
        self,
        monkeypatch: pytest.MonkeyPatch,
        recv_factory: _RecordingReceiverFactory,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Enabled + missing deps: warning logged, detector stays None."""
        import openfollow.video.detection as detection_module
        import openfollow.video.inputs as inputs_module
        import openfollow.video.preview as preview_module

        monkeypatch.setattr(detection_module, "PersonDetector", _FakePersonDetector)
        monkeypatch.setattr(
            detection_module,
            "check_detection_dependencies",
            lambda _cfg: ["onnxruntime", "opencv-python"],
        )
        monkeypatch.setattr(preview_module, "PreviewProvider", _FakePreviewProvider)
        monkeypatch.setattr(preview_module, "SnapshotProvider", _FakeSnapshotProvider)
        monkeypatch.setattr(inputs_module, "get_input_class", lambda _kind: _FakeInputCls)

        svc = self._enabled_services(monkeypatch)
        with caplog.at_level("WARNING", logger="openfollow.services"):
            svc.init_video()

        assert svc._person_detector is None
        assert _FakePersonDetector.instances == []
        assert recv_factory.last is not None
        assert recv_factory.last.detector is None
        # warning mentions the missing package list
        assert any(
            "onnxruntime" in rec.message and "opencv-python" in rec.message
            for rec in caplog.records
            if rec.levelname == "WARNING"
        )


# --------------------------------------------------------------------------- #
# init_video – plugin / pipeline / canvas capability branches
# --------------------------------------------------------------------------- #


class TestInitVideoInputPluginAbsent:
    def test_missing_plugin_feeds_empty_input_config(
        self,
        services: AppRuntimeServices,
        recv_factory: _RecordingReceiverFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import openfollow.video.detection as detection_module
        import openfollow.video.inputs as inputs_module
        import openfollow.video.preview as preview_module

        monkeypatch.setattr(detection_module, "PersonDetector", _FakePersonDetector)
        monkeypatch.setattr(detection_module, "check_detection_dependencies", lambda _cfg: [])
        monkeypatch.setattr(preview_module, "PreviewProvider", _FakePreviewProvider)
        monkeypatch.setattr(preview_module, "SnapshotProvider", _FakeSnapshotProvider)
        monkeypatch.setattr(inputs_module, "get_input_class", lambda _kind: None)

        services.init_video()
        assert recv_factory.last is not None
        assert recv_factory.last.input_config == {}


class TestInitVideoPipelineFailure:
    def test_create_pipeline_exception_is_logged_and_swallowed(
        self,
        services: AppRuntimeServices,
        recv_factory: _RecordingReceiverFactory,
        patched_subsystems: None,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        recv_factory.plant_pipeline_failure(RuntimeError("no source"))

        with caplog.at_level("WARNING", logger="openfollow.services"):
            services.init_video()

        assert recv_factory.last is not None
        assert recv_factory.last.create_called is True
        # receiver still started even though pipeline creation raised
        assert recv_factory.last.started is True
        assert services._app._video_receiver is recv_factory.last
        # canvas HUD still attached (the except clause does not short-circuit)
        assert services._app._canvas.hud_draw_fn == services._overlay_renderer.draw
        assert any(
            "Failed to create video pipeline" in rec.message and "no source" in rec.message
            for rec in caplog.records
            if rec.levelname == "WARNING"
        )


class TestInitVideoCanvasWithoutAttachHud:
    def test_canvas_missing_attach_hud_is_silently_tolerated(
        self,
        services: AppRuntimeServices,
        recv_factory: _RecordingReceiverFactory,
        patched_subsystems: None,
    ) -> None:

        class _BareCanvas:
            def __init__(self) -> None:
                self.embed_widget_calls: list[Any] = []

            def embed_widget(self, widget: Any) -> None:
                self.embed_widget_calls.append(widget)

        bare = _BareCanvas()
        services._app._canvas = bare
        services.init_video()
        assert recv_factory.last is not None
        assert recv_factory.last.started is True
        # Widget embedded but no HUD hook – and no AttributeError
        assert bare.embed_widget_calls == [recv_factory.last.widget]
