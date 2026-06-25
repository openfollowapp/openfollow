# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for ``AppRuntimeServices`` zone orchestration.

Covers:

* ``init_zone_engine`` – OSC client + ZoneEngine construction from config.
* ``update_zone_triggers`` – gating, ``eval_fps`` throttle, and marker +
  detection position collection wiring.
* ``_collect_marker_positions`` – controlled vs. viewer split + offline-
  marker filtering.
* ``_collect_detection_positions`` – detector + receiver + camera gates
  and the unprojection path.
* ``_get_zone_states_snapshot`` / ``_get_marker_positions_snapshot`` –
  web-UI read paths.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import SimpleNamespace

import numpy as np
import pytest

import openfollow.services as services_module
from openfollow.configuration import AppConfig, TriggerZonesConfig
from openfollow.services import AppRuntimeServices

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _FakeMarker:
    def __init__(self, x: float = 1.0, y: float = 2.0, z: float = 0.0) -> None:
        self.pos = (x, y, z)


class _FakePsnServer:
    def __init__(self, markers: dict[int, _FakeMarker] | None = None) -> None:
        self._markers = markers or {}

    def get_marker(self, tid: int) -> _FakeMarker | None:
        return self._markers.get(tid)


class _FakePsnReceiver:
    def __init__(
        self,
        markers: dict[int, _FakeMarker] | None = None,
        online: dict[int, bool] | None = None,
    ) -> None:
        self._markers = markers or {}
        self._online = online or {}

    def get_marker(self, tid: int) -> _FakeMarker | None:
        return self._markers.get(tid)

    def is_marker_online(self, tid: int) -> bool:
        return bool(self._online.get(tid, True))


@dataclass
class _FakeDet:
    x1: float
    y1: float
    x2: float
    y2: float
    track_id: int = -1


class _FakeDetector:
    def __init__(self, detections: list[_FakeDet] | None = None) -> None:
        self.detections = detections or []


class _FakeCamCfg:
    def __init__(
        self,
        pos_x: float = 0.0,
        pos_y: float = -6.0,
        pos_z: float = 2.5,
        pitch: float = -15.0,
        yaw: float = 0.0,
        roll: float = 0.0,
        fov: float = 60.0,
    ) -> None:
        self.pos_x = pos_x
        self.pos_y = pos_y
        self.pos_z = pos_z
        self.pitch = pitch
        self.yaw = yaw
        self.roll = roll
        self.fov = fov


class _FakeCamera:
    def __init__(self) -> None:
        self._cfg = _FakeCamCfg()

    def to_config(self) -> _FakeCamCfg:
        return self._cfg


class _FakeReceiver:
    def __init__(self, resolution: tuple[int, int] = (1920, 1080)) -> None:
        self.resolution = resolution


class _FakeZoneEngine:
    """Records calls to assert throttling + payload shape."""

    def __init__(self) -> None:
        self.updates: list[tuple[list, list]] = []
        self.zone_states = [(1, True, 3), (2, False, 0)]
        # Per-zone diagnostics keyed by zone index for test configuration.
        self.diagnostics: dict[int, dict] = {}

    def update(self, markers, detections) -> None:  # noqa: ANN001
        self.updates.append((list(markers), list(detections)))

    def get_zone_states(self) -> list[tuple[int, bool, int]]:
        return list(self.zone_states)

    def get_zone_diagnostics(self, index: int) -> dict | None:
        return self.diagnostics.get(index)


class _FakeOscService:
    """Stand-in for the unified ``OscService`` used by the zone engine.

    Records every send so tests can assert on the (address, host, port)
    triples the engine produced. Keeps the same surface the production
    service exposes so the engine code under test sees no difference."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list, str, int]] = []

    def send(
        self,
        address: str,
        args=(),  # noqa: ANN001
        *,
        host: str,
        port: int,
        protocol: str = "udp",
        framing: str = "slip",
    ) -> None:
        self.calls.append((address, list(args), host, port))


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _make_app(
    *,
    controlled: list[int] | None = None,
    viewer: list[int] | None = None,
    psn_markers: dict[int, _FakeMarker] | None = None,
    recv_markers: dict[int, _FakeMarker] | None = None,
    online: dict[int, bool] | None = None,
    zones_enabled: bool = True,
    eval_fps: int = 10,
) -> SimpleNamespace:
    cfg = AppConfig(psn_system_name="ZoneTest")
    cfg = replace(
        cfg,
        trigger_zones=TriggerZonesConfig(
            enabled=zones_enabled,
            eval_fps=eval_fps,
        ),
    )
    app = SimpleNamespace(
        _config=cfg,
        _controlled_ids=list(controlled or []),
        _viewer_ids=list(viewer or []),
        _server=_FakePsnServer(psn_markers or {}),
        _psn_receiver=_FakePsnReceiver(recv_markers or {}, online or {}),
        _video_receiver=None,
        _camera=_FakeCamera(),
        _input_manager=None,
        _canvas=None,
    )
    return app


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
    # Replace the ``ZoneEngine`` factory used by ``init_zone_engine`` so
    # we exercise the wiring without spinning up the real engine. The
    # OSC side is now the unified ``OscService``, owned by the runtime
    # services itself; tests swap it for a fake after construction.
    import openfollow.zones as zones_pkg

    monkeypatch.setattr(zones_pkg, "ZoneEngine", lambda cfg, osc, dests=None: _FakeZoneEngine())

    svc = AppRuntimeServices(_make_app())
    svc._osc_service = _FakeOscService()  # type: ignore[assignment]
    return svc


# --------------------------------------------------------------------------- #
# init_zone_engine
# --------------------------------------------------------------------------- #


class TestInitZoneEngine:
    def test_creates_zone_engine(self, services: AppRuntimeServices) -> None:
        services.init_zone_engine()
        assert isinstance(services._zone_engine, _FakeZoneEngine)

    def test_uses_shared_osc_service(self, services: AppRuntimeServices) -> None:
        """The engine receives the runtime services' shared OSC service
        – not a fresh per-zone client. Same instance across init steps
        so zones, transmitters, and the input listener share one cache."""
        services.init_zone_engine()
        assert isinstance(services._osc_service, _FakeOscService)


# --------------------------------------------------------------------------- #
# update_zone_triggers
# --------------------------------------------------------------------------- #


class TestUpdateZoneTriggers:
    def test_no_engine_is_no_op(self, services: AppRuntimeServices) -> None:
        services._zone_engine = None
        services.update_zone_triggers()  # must not raise

    def test_disabled_in_config_is_no_op(self, services: AppRuntimeServices) -> None:
        services._app = _make_app(zones_enabled=False)
        services.init_zone_engine()
        fake = services._zone_engine
        services.update_zone_triggers()
        assert isinstance(fake, _FakeZoneEngine)
        assert fake.updates == []

    def test_respects_eval_fps_throttle(self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch) -> None:
        services._app = _make_app(eval_fps=10)  # 0.1s interval
        services.init_zone_engine()
        fake = services._zone_engine

        times = iter([100.0, 100.05, 100.2])
        monkeypatch.setattr(services_module.time, "monotonic", lambda: next(times))
        services.update_zone_triggers()  # t=100.0 → first eval
        services.update_zone_triggers()  # t=100.05 → throttled
        services.update_zone_triggers()  # t=100.2  → eval again
        assert len(fake.updates) == 2

    def test_forwards_markers_and_detections(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        services._app = _make_app(
            controlled=[1],
            viewer=[1],
            psn_markers={1: _FakeMarker(3.0, 4.0, 0.0)},
        )
        services.init_zone_engine()
        fake = services._zone_engine
        monkeypatch.setattr(services_module.time, "monotonic", lambda: 100.0)
        services.update_zone_triggers()

        markers, detections = fake.updates[0]
        assert markers == [(("marker", 1), 3.0, 4.0)]
        assert detections == []


# --------------------------------------------------------------------------- #
# _collect_marker_positions
# --------------------------------------------------------------------------- #


class TestCollectMarkerPositions:
    def test_controlled_markers_come_from_psn_server(self, services: AppRuntimeServices) -> None:
        services._app = _make_app(
            controlled=[1],
            viewer=[1],
            psn_markers={1: _FakeMarker(1.0, 2.0, 3.0)},
        )
        assert services._collect_marker_positions() == [
            (("marker", 1), 1.0, 2.0),
        ]

    def test_viewer_only_markers_come_from_receiver(self, services: AppRuntimeServices) -> None:
        services._app = _make_app(
            controlled=[1],
            viewer=[1, 2],
            psn_markers={1: _FakeMarker(1.0, 2.0)},
            recv_markers={2: _FakeMarker(5.0, 6.0)},
            online={2: True},
        )
        out = services._collect_marker_positions()
        # Order preserved from viewer_ids: 1 then 2.
        assert out == [
            (("marker", 1), 1.0, 2.0),
            (("marker", 2), 5.0, 6.0),
        ]

    def test_offline_viewer_marker_is_dropped(self, services: AppRuntimeServices) -> None:
        services._app = _make_app(
            viewer=[5],
            recv_markers={5: _FakeMarker(9.0, 9.0)},
            online={5: False},
        )
        assert services._collect_marker_positions() == []

    def test_missing_marker_is_silently_skipped(self, services: AppRuntimeServices) -> None:
        services._app = _make_app(viewer=[42])  # no marker registered
        assert services._collect_marker_positions() == []


# --------------------------------------------------------------------------- #
# _collect_detection_positions
# --------------------------------------------------------------------------- #


class TestCollectDetectionPositions:
    def test_no_detector_returns_empty(self, services: AppRuntimeServices) -> None:
        services._person_detector = None
        assert services._collect_detection_positions() == []

    def test_no_detections_returns_empty(self, services: AppRuntimeServices) -> None:
        services._person_detector = _FakeDetector(detections=[])
        assert services._collect_detection_positions() == []

    def test_no_receiver_returns_empty(self, services: AppRuntimeServices) -> None:
        services._person_detector = _FakeDetector([_FakeDet(0, 0, 1, 1)])
        services._app._video_receiver = None
        assert services._collect_detection_positions() == []

    def test_zero_resolution_returns_empty(self, services: AppRuntimeServices) -> None:
        services._person_detector = _FakeDetector([_FakeDet(0, 0, 1, 1)])
        services._app._video_receiver = _FakeReceiver(resolution=(0, 0))
        assert services._collect_detection_positions() == []

    def test_no_camera_returns_empty(self, services: AppRuntimeServices) -> None:
        services._person_detector = _FakeDetector([_FakeDet(0, 0, 1, 1)])
        services._app._video_receiver = _FakeReceiver()
        services._app._camera = None
        assert services._collect_detection_positions() == []

    def test_unprojection_nonfinite_box_is_dropped(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        services._person_detector = _FakeDetector([_FakeDet(0, 0, 1, 1, track_id=7)])
        services._app._video_receiver = _FakeReceiver()

        from openfollow.scene import solver as solver_mod

        monkeypatch.setattr(
            solver_mod,
            "unproject_to_plane",
            lambda *a, **kw: np.array([[np.nan, np.nan]], dtype=np.float64),
        )
        assert services._collect_detection_positions() == []

    def test_tracked_detection_yields_world_xy(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        services._person_detector = _FakeDetector([_FakeDet(0.4, 0.5, 0.6, 0.7, track_id=3)])
        services._app._video_receiver = _FakeReceiver()

        from openfollow.scene import solver as solver_mod

        monkeypatch.setattr(
            solver_mod,
            "unproject_to_plane",
            lambda *a, **kw: np.array([[2.0, 3.0]], dtype=np.float64),
        )
        out = services._collect_detection_positions()
        assert out == [(("detection", 3), 2.0, 3.0)]

    def test_untracked_detection_collapses_to_neg_one(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        services._person_detector = _FakeDetector(
            [
                _FakeDet(0.0, 0.0, 0.1, 0.1, track_id=-1),
                _FakeDet(0.5, 0.5, 0.6, 0.6, track_id=-1),
            ]
        )
        services._app._video_receiver = _FakeReceiver()

        from openfollow.scene import solver as solver_mod

        monkeypatch.setattr(
            solver_mod,
            "unproject_to_plane",
            lambda *a, **kw: np.array([[1.0, 1.0]], dtype=np.float64),
        )
        # Two boxes → two entries, each with ("detection", -1).
        out = services._collect_detection_positions()
        assert len(out) == 2
        assert all(k == ("detection", -1) for k, _x, _y in out)


def _spy_detection_unproject(monkeypatch: pytest.MonkeyPatch) -> dict[str, np.ndarray]:
    """Patch unproject_to_plane to record the screen point it receives."""
    from openfollow.scene import solver as solver_mod

    recorded: dict[str, np.ndarray] = {}

    def _spy(_params, screen_pt, _w, _h, _plane_z):  # noqa: ANN001, ANN202
        recorded["screen"] = np.array(screen_pt, dtype=float).copy()
        return np.array([[2.0, 3.0]], dtype=np.float64)

    monkeypatch.setattr(solver_mod, "unproject_to_plane", _spy)
    return recorded


class TestCollectDetectionPositionsLensDistortion:
    """A detection foot point sits on the lens-distorted video, so it is
    undistorted back to the pinhole frame before unprojection – the same
    treatment the detection-pin path gets (identity when no lens is set)."""

    def test_passes_raw_foot_point_when_lens_zero(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        services._person_detector = _FakeDetector([_FakeDet(0.6, 0.5, 0.8, 0.7, track_id=3)])
        services._app._video_receiver = _FakeReceiver()  # lens_k1 == lens_k2 == 0 by default
        recorded = _spy_detection_unproject(monkeypatch)

        out = services._collect_detection_positions()
        assert out == [(("detection", 3), 2.0, 3.0)]
        # No distortion -> the raw foot point reaches unproject unchanged:
        # centre_x = 0.7*1920, foot_y = 0.7*1080.
        assert recorded["screen"][0, 0] == pytest.approx(0.7 * 1920)
        assert recorded["screen"][0, 1] == pytest.approx(0.7 * 1080)

    def test_undistorts_foot_point_when_lens_set(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        services._person_detector = _FakeDetector([_FakeDet(0.6, 0.5, 0.8, 0.7, track_id=3)])
        services._app._video_receiver = _FakeReceiver()
        services._app._config.camera.lens_k1 = -0.2  # barrel
        recorded = _spy_detection_unproject(monkeypatch)

        services._collect_detection_positions()
        cx, cy = 960.0, 540.0  # 1920x1080 centre
        raw = np.hypot(0.7 * 1920 - cx, 0.7 * 1080 - cy)
        undist = np.hypot(recorded["screen"][0, 0] - cx, recorded["screen"][0, 1] - cy)
        # Undistorting a barrel-distorted foot point pushes it outward, matching
        # the detection-pin path (and the bowed zone overlay).
        assert undist > raw


# --------------------------------------------------------------------------- #
# _get_zone_states_snapshot + _get_marker_positions_snapshot
# --------------------------------------------------------------------------- #


class TestSnapshotProviders:
    def test_zone_states_returns_engine_snapshot(self, services: AppRuntimeServices) -> None:
        services.init_zone_engine()
        assert services._get_zone_states_snapshot() == [(1, True, 3), (2, False, 0)]

    def test_zone_states_without_engine_returns_empty(self, services: AppRuntimeServices) -> None:
        services._zone_engine = None
        assert services._get_zone_states_snapshot() == []

    def test_marker_positions_snapshot_omits_kind_label(self, services: AppRuntimeServices) -> None:
        services._app = _make_app(
            controlled=[7],
            viewer=[7],
            psn_markers={7: _FakeMarker(1.5, 2.5)},
        )
        assert services._get_marker_positions_snapshot() == [(7, 1.5, 2.5)]

    def test_marker_positions_snapshot_swallows_exceptions(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(self: AppRuntimeServices) -> list:
            raise RuntimeError("receiver blew up")

        monkeypatch.setattr(AppRuntimeServices, "_collect_marker_positions", _boom)
        # Must not propagate: web thread must never crash.
        assert services._get_marker_positions_snapshot() == []


# --------------------------------------------------------------------------- #
# Zone Diagnostics tab providers – _get_zone_diagnostics_snapshot
# + _zone_test_send. The web-server fixture above doesn't reach these; we
# need to drive them through the live ``AppRuntimeServices`` fake.
# --------------------------------------------------------------------------- #


class TestZoneDiagnosticsProviders:
    def test_diagnostics_snapshot_returns_engine_dict(self, services: AppRuntimeServices) -> None:
        services.init_zone_engine()
        fake = services._zone_engine
        assert isinstance(fake, _FakeZoneEngine)
        fake.diagnostics[0] = {
            "is_occupied": True,
            "count": 1,
            "occupants": [{"kind": "marker", "id": 0}],
            "last_event_time": 12.5,
            "last_event_address": "/zone/enter",
        }
        out = services._get_zone_diagnostics_snapshot(0)
        assert out is not None
        assert out["last_event_address"] == "/zone/enter"

    def test_diagnostics_snapshot_returns_none_without_engine(self, services: AppRuntimeServices) -> None:
        services._zone_engine = None
        assert services._get_zone_diagnostics_snapshot(0) is None


def _seed_zone(services: AppRuntimeServices) -> None:
    """Insert one ``TriggerZoneConfig`` into the test app's config so
    the test-send tests have an index to operate on. ``_make_app``'s
    default zones list is empty by design (most tests don't need one)."""
    from openfollow.configuration import TriggerZoneConfig

    services._app._config.trigger_zones.zones.append(TriggerZoneConfig(name="Z"))


class TestZoneTestSend:
    def test_test_send_dispatches_through_osc_service(self, services: AppRuntimeServices) -> None:
        """Happy path: an empty zone gets a populated ``first_entry``
        address; ``_zone_test_send`` tokenises + coerces + sends."""
        from openfollow.configuration import OscDestinationConfig

        _seed_zone(services)
        services._app._config.osc_destinations.destinations.append(
            OscDestinationConfig(id="d1", host="10.1.2.3", port=9000),
        )
        zone_cfg = services._app._config.trigger_zones.zones[0]
        zone_cfg.osc_address_first_entry = "/zone/enter 1.5"
        zone_cfg.destination_id = "d1"
        result = services._zone_test_send(0, "first")
        assert result["success"] is True
        assert result["address"] == "/zone/enter"
        assert result["args"] == [1.5]
        assert result["host"] == "10.1.2.3"
        assert result["port"] == 9000
        # The OSC service recorded one matching send.
        fake_osc = services._osc_service
        assert isinstance(fake_osc, _FakeOscService)
        assert fake_osc.calls == [("/zone/enter", [1.5], "10.1.2.3", 9000)]

    def test_test_send_skipped_when_no_destination_selected(self, services: AppRuntimeServices) -> None:
        _seed_zone(services)
        zone_cfg = services._app._config.trigger_zones.zones[0]
        zone_cfg.osc_address_additional_entry = "/zone/extra"
        zone_cfg.destination_id = ""
        result = services._zone_test_send(0, "additional")
        assert result.get("skipped") is True
        assert result["reason"] == "no destination selected"

    def test_test_send_unknown_which_returns_error(self, services: AppRuntimeServices) -> None:
        _seed_zone(services)
        result = services._zone_test_send(0, "bogus")
        assert "error" in result

    def test_test_send_out_of_range_index_returns_error(self, services: AppRuntimeServices) -> None:
        result = services._zone_test_send(99, "first")
        assert "error" in result
        result_neg = services._zone_test_send(-1, "first")
        assert "error" in result_neg

    def test_test_send_skipped_for_empty_field(self, services: AppRuntimeServices) -> None:
        """Lenient contract: an unconfigured field returns
        ``{"skipped": True, ...}`` instead of an error so the editor can
        say "nothing to send" without forcing the operator to fill the
        field first."""
        _seed_zone(services)
        zone_cfg = services._app._config.trigger_zones.zones[0]
        zone_cfg.osc_address_first_entry = ""
        result = services._zone_test_send(0, "first")
        assert result.get("skipped") is True

    def test_test_send_skipped_for_whitespace_only_field(self, services: AppRuntimeServices) -> None:
        """A whitespace-only field passes the empty-string truthiness
        guard but tokenises to ``("", [])``. Distinct from the empty-
        string case so the editor can label "field has no address" if
        the operator entered only whitespace."""
        _seed_zone(services)
        zone_cfg = services._app._config.trigger_zones.zones[0]
        zone_cfg.osc_address_first_entry = "   "
        result = services._zone_test_send(0, "first")
        assert result.get("skipped") is True
        assert result.get("reason") == "field has no address"

    def test_test_send_unclosed_quote_returns_error(self, services: AppRuntimeServices) -> None:
        _seed_zone(services)
        zone_cfg = services._app._config.trigger_zones.zones[0]
        zone_cfg.osc_address_first_entry = '/cmd "unclosed'
        result = services._zone_test_send(0, "first")
        assert "error" in result
        assert "unclosed" in result["error"].lower()
