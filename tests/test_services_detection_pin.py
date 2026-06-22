# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the detection-pinning helper.

In ``replace`` mode the helper snaps the single resolved marker onto a
visually-tracked detection with velocity prediction + EMA smoothing. In
``assist`` mode it refines *every* controlled marker toward the person
nearest that marker's operator-steered ghost anchor. Per-marker smoothing
state lives on ``app._detection_pin_states``; there is no ``pin_state``
kwarg.

These tests drive the helper through a ``SimpleNamespace`` app graph (no
GTK, no GStreamer) and a deterministic stub detector so we can assert on
the exact update rule without flakiness.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from openfollow.configuration import CameraConfig, DetectionConfig, GridConfig
from openfollow.runtime.services_detection_pin import (
    _NOMINAL_FRAME_DT,
    DetectionPinState,
    _advance_smoothing,
    _dt_steps,
    _ema_factor,
    _get_pin_state,
    _prune_manual_markers,
    _prune_pin_states,
    apply_detection_pin,
    assist_active,
    get_or_create_manual_marker,
    is_assist_controlled,
)

pytestmark = pytest.mark.unit


class _StubDetection:
    def __init__(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        track_id: int = -1,
    ) -> None:
        self.x1 = x1
        self.y1 = y1
        self.x2 = x2
        self.y2 = y2
        self.track_id = track_id


class _StubDetector:
    def __init__(self, detection) -> None:  # noqa: ANN001
        self.tracked_detection = detection


class _StubCamera:
    def __init__(self) -> None:
        # Camera looking straight down from 10m at origin – unproject_to_plane
        # becomes linear and predictable for the grid.
        self._cfg = CameraConfig(
            pos_x=0.0,
            pos_y=0.0,
            pos_z=10.0,
            pitch=-90.0,
            yaw=0.0,
            roll=0.0,
            fov=60.0,
        )

    def to_config(self) -> CameraConfig:
        return self._cfg


class _StubMarker:
    def __init__(self, marker_id: int = 0) -> None:
        self._pos = (0.0, 0.0, 0.0)
        self.marker_id = marker_id

    @property
    def pos(self) -> tuple[float, float, float]:
        return self._pos

    def set_pos(self, x: float, y: float, z: float) -> None:
        self._pos = (x, y, z)


class _StubServer:
    """Returns the same marker for any id – the single-marker default."""

    def __init__(self, marker) -> None:  # noqa: ANN001
        self._marker = marker

    def get_marker(self, _tid: int):  # noqa: ANN202
        return self._marker


class _MultiMarkerServer:
    """Returns a distinct marker per id (None for unknown ids)."""

    def __init__(self, by_id: dict[int, object]) -> None:
        self._by_id = by_id

    def get_marker(self, tid: int):  # noqa: ANN202
        return self._by_id.get(tid)


class _StubVideoReceiver:
    def __init__(self, resolution: tuple[int, int]) -> None:
        self.resolution = resolution


def _make_app(
    *,
    detection_cfg: DetectionConfig | None = None,
    resolution: tuple[int, int] = (1920, 1080),
    marker: _StubMarker | None = None,
    server: object | None = None,
    selected_id: int | None = 0,
    controlled_ids: list[int] | None = None,
) -> SimpleNamespace:
    detection_cfg = detection_cfg or DetectionConfig(
        enabled=True,
        pin_mode="replace",
        smoothing=0.5,
        prediction=0.0,
    )
    grid = GridConfig()
    if server is None:
        marker = marker or _StubMarker()
        server = _StubServer(marker)
    return SimpleNamespace(
        _config=SimpleNamespace(detection=detection_cfg, grid=grid),
        _video_receiver=_StubVideoReceiver(resolution),
        _camera=_StubCamera(),
        _server=server,
        _assist_manual={},
        _detection_pin_states={},
        _selected_id=selected_id,
        # ``pin_marker_id`` looks at controlled_ids when the operator picks a
        # fixed ID. Default to a single-marker list matching ``selected_id``
        # (or empty when there's no selection).
        _controlled_ids=(
            controlled_ids if controlled_ids is not None else ([selected_id] if selected_id is not None else [])
        ),
    )


def _buffers():  # noqa: ANN202
    return (
        np.zeros(7, dtype=np.float64),
        np.zeros((1, 2), dtype=np.float64),
    )


def _run(app, detector, monkeypatch=None, *, unproject=None, **buffers_kw):  # noqa: ANN001, ANN202
    """Call ``apply_detection_pin`` with fresh buffers (no ``pin_state`` kwarg)."""
    cam, pt = _buffers()
    if unproject is not None:
        import openfollow.runtime.services_detection_pin as module

        assert monkeypatch is not None
        monkeypatch.setattr(module, "unproject_to_plane", unproject)
    apply_detection_pin(
        app,
        person_detector=detector,
        unproject_cam_buffer=cam,
        screen_point_buffer=pt,
        **buffers_kw,
    )


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


def test_returns_early_when_detection_disabled() -> None:
    """``enabled=False`` is detection's only off switch: no marker writes, and
    every ghost + per-marker state is pruned so a later re-engage re-seeds."""
    cfg = DetectionConfig(enabled=False)
    app = _make_app(detection_cfg=cfg)
    marker = app._server.get_marker(0)
    before = marker.pos
    # Pre-existing leftover ghost + state must be cleared by the disabled path.
    from openfollow.psn import Marker

    app._assist_manual[1] = Marker(1, "")
    app._detection_pin_states[1] = DetectionPinState()

    _run(app, _StubDetector(_StubDetection(0.4, 0.4, 0.6, 0.6)))

    assert marker.pos == before
    assert app._assist_manual == {}
    assert app._detection_pin_states == {}


def test_noop_when_detector_is_none() -> None:
    cfg = DetectionConfig(enabled=True, pin_mode="replace", smoothing=0.5)
    app = _make_app(detection_cfg=cfg)
    marker = app._server.get_marker(0)
    before = marker.pos

    _run(app, None)

    assert marker.pos == before


def test_noop_when_no_tracked_detection() -> None:
    cfg = DetectionConfig(enabled=True, pin_mode="replace", smoothing=0.5)
    app = _make_app(detection_cfg=cfg)
    marker = app._server.get_marker(0)

    _run(app, _StubDetector(None))

    assert marker.pos == (0.0, 0.0, 0.0)
    # A driven marker still gets a state, but with no smoothing seeded.
    state = app._detection_pin_states.get(0)
    assert state is not None
    assert state.smooth_x is None and state.smooth_y is None


def test_noop_when_video_resolution_zero() -> None:
    cfg = DetectionConfig(enabled=True, pin_mode="replace", smoothing=0.5)
    app = _make_app(detection_cfg=cfg, resolution=(0, 0))

    _run(app, _StubDetector(_StubDetection(0.4, 0.4, 0.6, 0.6)))

    assert app._server.get_marker(0).pos == (0.0, 0.0, 0.0)


def test_noop_when_no_selected_marker() -> None:
    cfg = DetectionConfig(enabled=True, pin_mode="replace", smoothing=0.5)
    app = _make_app(detection_cfg=cfg, selected_id=None)

    _run(app, _StubDetector(_StubDetection(0.4, 0.4, 0.6, 0.6)))

    # No marker resolves -> nothing driven, no state created.
    assert app._detection_pin_states == {}


def test_noop_when_marker_missing() -> None:
    cfg = DetectionConfig(enabled=True, pin_mode="replace", smoothing=0.5)
    app = _make_app(detection_cfg=cfg, server=_MultiMarkerServer({}))

    _run(app, _StubDetector(_StubDetection(0.4, 0.4, 0.6, 0.6)))

    assert app._detection_pin_states == {}


# ---------------------------------------------------------------------------
# Replace mode – pin geometry
# ---------------------------------------------------------------------------


def _replace_cfg(**overrides) -> DetectionConfig:  # noqa: ANN003
    base = {"enabled": True, "pin_mode": "replace", "smoothing": 1.0, "prediction": 0.0}
    base.update(overrides)
    return DetectionConfig(**base)  # type: ignore[arg-type]


def test_pin_point_bottom_uses_box_y2(monkeypatch) -> None:
    app = _make_app(detection_cfg=_replace_cfg(pin_point="bottom"))
    detector = _StubDetector(_StubDetection(0.4, 0.2, 0.6, 0.8))
    marker = app._server.get_marker(0)
    recorded: dict[str, float] = {}

    def _stub_unproject(_params, screen_pt, _w, _h, plane_z):  # noqa: ANN001, ANN202
        recorded["plane_z"] = plane_z
        recorded["screen_y"] = float(screen_pt[0, 1])
        return np.array([[1.0, 2.0, 0.0]], dtype=np.float64)

    _run(app, detector, monkeypatch, unproject=_stub_unproject)

    # Screen Y for pin_point=bottom is ``y2 * h`` (0.8 * 1080 = 864).
    assert recorded["screen_y"] == pytest.approx(0.8 * 1080)
    # pin_point=bottom uses the grid plane (z_offset), not marker.pos[2].
    assert recorded["plane_z"] == pytest.approx(app._config.grid.z_offset)
    # marker.pos is PSN-absolute, matching the unprojected world point.
    assert marker.pos[0] == pytest.approx(1.0)
    assert marker.pos[1] == pytest.approx(2.0)


def test_pin_point_top_uses_box_y1(monkeypatch) -> None:
    app = _make_app(detection_cfg=_replace_cfg(pin_point="top"))
    detector = _StubDetector(_StubDetection(0.4, 0.2, 0.6, 0.8))
    marker = app._server.get_marker(0)
    marker.set_pos(0.0, 0.0, 1.5)
    recorded: dict[str, float] = {}

    def _stub_unproject(_params, screen_pt, _w, _h, plane_z):  # noqa: ANN001, ANN202
        recorded["plane_z"] = plane_z
        recorded["screen_y"] = float(screen_pt[0, 1])
        return np.array([[5.0, 6.0, 0.0]], dtype=np.float64)

    _run(app, detector, monkeypatch, unproject=_stub_unproject)

    # Screen Y for pin_point=top is ``y1 * h`` (0.2 * 1080 = 216).
    assert recorded["screen_y"] == pytest.approx(0.2 * 1080)
    # pin_point=top samples at the marker's current z.
    assert recorded["plane_z"] == pytest.approx(1.5)


def test_non_finite_world_point_is_dropped(monkeypatch) -> None:
    app = _make_app(detection_cfg=_replace_cfg())
    detector = _StubDetector(_StubDetection(0.4, 0.4, 0.6, 0.6))
    marker = app._server.get_marker(0)

    _run(app, detector, monkeypatch, unproject=lambda *_a, **_k: np.array([[np.inf, np.nan, 0.0]]))

    assert marker.pos == (0.0, 0.0, 0.0)
    state = app._detection_pin_states.get(0)
    assert state is not None and state.smooth_x is None


def test_first_frame_initialises_smoothed_position_without_lag(monkeypatch) -> None:
    app = _make_app(detection_cfg=_replace_cfg(smoothing=0.15))  # heavy smoothing
    detector = _StubDetector(_StubDetection(0.4, 0.4, 0.6, 0.6))
    marker = app._server.get_marker(0)

    _run(app, detector, monkeypatch, unproject=lambda *_a, **_k: np.array([[10.0, 20.0, 0.0]]))

    # Smoothed state and marker position equal the raw target on the first
    # frame – zero lag regardless of the smoothing coefficient.
    state = app._detection_pin_states[0]
    assert state.smooth_x == pytest.approx(10.0)
    assert state.smooth_y == pytest.approx(20.0)
    assert marker.pos[0] == pytest.approx(10.0)


def test_second_frame_applies_ema_interpolation(monkeypatch) -> None:
    app = _make_app(detection_cfg=_replace_cfg(smoothing=0.5))
    detector = _StubDetector(_StubDetection(0.4, 0.4, 0.6, 0.6))
    marker = app._server.get_marker(0)
    world_points = iter([np.array([[10.0, 20.0, 0.0]]), np.array([[20.0, 40.0, 0.0]])])

    import openfollow.runtime.services_detection_pin as module

    monkeypatch.setattr(module, "unproject_to_plane", lambda *_a, **_k: next(world_points))

    _run(app, detector)
    assert app._detection_pin_states[0].smooth_x == pytest.approx(10.0)

    _run(app, detector)
    # alpha=0.5 → smooth_x = prev + 0.5*(new - prev) = 15.0 (PSN-absolute).
    state = app._detection_pin_states[0]
    assert state.smooth_x == pytest.approx(15.0)
    assert state.smooth_y == pytest.approx(30.0)
    assert marker.pos[0] == pytest.approx(15.0)


def test_velocity_prediction_extrapolates_target(monkeypatch) -> None:
    app = _make_app(detection_cfg=_replace_cfg(smoothing=1.0, prediction=1.0))
    detector = _StubDetector(_StubDetection(0.4, 0.4, 0.6, 0.6))
    marker = app._server.get_marker(0)
    world_points = iter(
        [
            np.array([[0.0, 0.0, 0.0]]),
            np.array([[1.0, 0.0, 0.0]]),
            np.array([[2.0, 0.0, 0.0]]),
        ]
    )

    import openfollow.runtime.services_detection_pin as module

    monkeypatch.setattr(module, "unproject_to_plane", lambda *_a, **_k: next(world_points))

    _run(app, detector)  # frame 1 seeds
    _run(app, detector)  # frame 2: velocity EMA sees first nonzero delta
    state = app._detection_pin_states[0]
    # vel_alpha = 0.3 on first nonzero delta of 1.0
    assert state.vel_x == pytest.approx(0.3)

    _run(app, detector)  # frame 3: prediction=1.0 lands marker at target + vel*1
    # vel_x <- 0.3 + 0.3 * (1.0 - 0.3) = 0.51
    assert state.vel_x == pytest.approx(0.51)
    # predicted_x = target_x + vel_x * 1.0; smoothing=1.0 snaps exactly.
    assert marker.pos[0] == pytest.approx(2.0 + 0.51)


# ---------------------------------------------------------------------------
# pin_marker_id – explicit-ID setting (replace mode)
# ---------------------------------------------------------------------------


class _RecordingServer:
    """Records every ``get_marker(tid)`` call so tests can verify which marker
    the pin path queried."""

    def __init__(self, by_id: dict[int, _StubMarker]) -> None:
        self._by_id = by_id
        self.lookups: list[int] = []

    def get_marker(self, tid: int) -> _StubMarker | None:
        self.lookups.append(tid)
        return self._by_id.get(tid)


def test_pin_marker_id_default_minus_one_follows_selected(monkeypatch) -> None:
    cfg = _replace_cfg(pin_marker_id=-1)
    marker_3 = _StubMarker(3)
    server = _RecordingServer({3: marker_3})
    app = _make_app(detection_cfg=cfg, server=server, controlled_ids=[3], selected_id=3)
    detector = _StubDetector(_StubDetection(0.4, 0.4, 0.6, 0.6))

    _run(app, detector, monkeypatch, unproject=lambda *_a, **_k: np.array([[1.0, 2.0, 0.0]]))

    # Server queried for the controller-selected marker (id 3).
    assert server.lookups == [3]
    assert marker_3.pos[0] == pytest.approx(1.0)
    assert 3 in app._detection_pin_states


def test_pin_marker_id_explicit_id_overrides_selected(monkeypatch) -> None:
    """A non-negative ``pin_marker_id`` pins to that exact marker regardless of
    which one the controller has selected."""
    cfg = _replace_cfg(pin_marker_id=5)
    marker_5 = _StubMarker(5)
    marker_3 = _StubMarker(3)
    server = _RecordingServer({3: marker_3, 5: marker_5})
    app = _make_app(detection_cfg=cfg, server=server, controlled_ids=[3, 5], selected_id=3)
    detector = _StubDetector(_StubDetection(0.4, 0.4, 0.6, 0.6))

    _run(app, detector, monkeypatch, unproject=lambda *_a, **_k: np.array([[1.0, 2.0, 0.0]]))

    # Server queried for the configured ID (5), not the selection (3).
    assert server.lookups == [5]
    assert marker_5.pos[0] == pytest.approx(1.0)
    assert marker_3.pos == (0.0, 0.0, 0.0)


def test_pin_marker_id_skips_when_id_not_in_controlled_ids() -> None:
    """When ``pin_marker_id`` points at an ID not in ``controlled_marker_ids``,
    the runtime skips the pin entirely rather than silently re-targeting."""
    cfg = _replace_cfg(pin_marker_id=99)
    marker_3 = _StubMarker(3)
    server = _RecordingServer({3: marker_3})
    app = _make_app(detection_cfg=cfg, server=server, controlled_ids=[3], selected_id=3)

    _run(app, _StubDetector(_StubDetection(0.4, 0.4, 0.6, 0.6)))

    # No marker lookup happened – the route bailed at the controlled-ids guard.
    assert server.lookups == []
    assert marker_3.pos == (0.0, 0.0, 0.0)
    assert app._detection_pin_states == {}


# ---------------------------------------------------------------------------
# Assist (hybrid) mode – operator drives, detection refines toward the nearest
# person within the gate radius. Uses a linear unproject stub so the world
# point of a box is its centre fraction × 10 (along the x axis when y1=0).
# ---------------------------------------------------------------------------


class _AssistDetector:
    """Exposes ``detections`` (the assist path's input); never tracked."""

    def __init__(self, detections: list[_StubDetection]) -> None:
        self.detections = list(detections)
        self.tracked_detection = None


def _det(center_x: float, track_id: int, *, y1: float = 0.0) -> _StubDetection:
    """A box centred at ``center_x`` (fraction) carrying ``track_id``."""
    return _StubDetection(center_x - 0.05, y1, center_x + 0.05, y1 + 0.1, track_id=track_id)


def _linear_unproject(_params, screen_pt, w, h, _plane_z):  # noqa: ANN001, ANN202
    """world_x = (screen_x / w) × 10; world_y = (screen_y / h) × 10."""
    sx = float(screen_pt[0, 0]) / float(w)
    sy = float(screen_pt[0, 1]) / float(h)
    return np.array([[sx * 10.0, sy * 10.0, 0.0]], dtype=np.float64)


def _assist_cfg(**overrides) -> DetectionConfig:  # noqa: ANN003
    base = {
        "enabled": True,
        "pin_mode": "assist",
        "assist_radius_m": 2.0,
        "assist_strength": 1.0,
        "smoothing": 1.0,
        "prediction": 0.0,
    }
    base.update(overrides)
    return DetectionConfig(**base)  # type: ignore[arg-type]


# Assist ghosts are real ``Marker`` objects (Marker requires id >= 1), so the
# assist suite pins marker id 1 rather than the id-0 default the replace suite
# uses with its lightweight ``_StubMarker``.
_PID = 1


def _assist_app(detection_cfg: DetectionConfig, **kw) -> SimpleNamespace:  # noqa: ANN003
    return _make_app(
        detection_cfg=detection_cfg,
        selected_id=_PID,
        controlled_ids=[_PID],
        resolution=(1000, 1000),
        **kw,
    )


def _seed_anchor(app: SimpleNamespace, x: float, y: float, z: float, mid: int = _PID) -> object:
    """Create + position the operator-steered manual ghost for a pinned id.

    Mirrors what the input resolver does the first time the operator nudges the
    marker in assist mode: lazily creates the ghost (seeded from the registered
    marker), then moves it. Returns the ghost so tests can assert the pin never
    writes it.
    """
    anchor = get_or_create_manual_marker(app, mid)
    anchor.set_pos(x, y, z)
    return anchor


def _run_assist(app, detector, monkeypatch) -> None:  # noqa: ANN001
    _run(app, detector, monkeypatch, unproject=_linear_unproject)


def test_assist_clips_output_to_nearest_person_within_radius(monkeypatch) -> None:
    app = _assist_app(_assist_cfg())  # smoothing 1.0, strength 1.0, radius 2.0
    out = app._server.get_marker(_PID)
    anchor = _seed_anchor(app, 4.0, 0.0, 0.0)  # operator parks the anchor at (4,0)
    # A → world (5,0) dist 1 (in radius 2); B → world (9,0) dist 5 (out).
    detector = _AssistDetector([_det(0.5, 1), _det(0.9, 2)])

    _run_assist(app, detector, monkeypatch)

    state = app._detection_pin_states[_PID]
    # Full glide lands the output exactly on the nearest in-range person.
    assert out.pos[0] == pytest.approx(5.0)
    assert out.pos[1] == pytest.approx(0.0)
    assert state.locked_track_id == 1
    # The pin must never write the operator's anchor.
    assert anchor.pos == (4.0, 0.0, 0.0)


def test_assist_clip_strength_blends_output_toward_anchor(monkeypatch) -> None:
    app = _assist_app(_assist_cfg(assist_radius_m=50.0, assist_strength=0.5))
    out = app._server.get_marker(_PID)
    anchor = _seed_anchor(app, 0.0, 0.0, 0.0)
    detector = _AssistDetector([_det(1.0, 1)])  # world (10,0)

    _run_assist(app, detector, monkeypatch)

    # In-range target = anchor + strength·(person − anchor) = 0 + 0.5·10 = 5;
    # full glide lands there this frame.
    assert out.pos[0] == pytest.approx(5.0)
    assert app._detection_pin_states[_PID].locked_track_id == 1
    assert anchor.pos == (0.0, 0.0, 0.0)


def test_assist_output_glides_onto_person_and_never_snaps(monkeypatch) -> None:
    """Strength 1 clips to the person, but the OUTPUT still glides over frames
    (never snaps), and the operator's anchor stays put."""
    app = _assist_app(_assist_cfg(assist_radius_m=50.0, smoothing=0.5))
    out = app._server.get_marker(_PID)
    anchor = _seed_anchor(app, 0.0, 0.0, 0.0)
    detector = _AssistDetector([_det(1.0, 1)])  # world (10,0), static

    def _step() -> float:
        _run_assist(app, detector, monkeypatch)
        return out.pos[0]

    # Geometric glide toward the person: 5 → 7.5 → 8.75 (each frame halves the gap).
    assert _step() == pytest.approx(5.0)
    assert _step() == pytest.approx(7.5)
    assert _step() == pytest.approx(8.75)
    for _ in range(40):
        _step()
    assert out.pos[0] == pytest.approx(10.0, abs=1e-3)  # settles onto the person
    assert anchor.pos == (0.0, 0.0, 0.0)


def test_assist_glides_home_when_no_detection_in_radius(monkeypatch) -> None:
    app = _assist_app(_assist_cfg(smoothing=0.5))  # radius 2.0
    out = app._server.get_marker(_PID)
    anchor = _seed_anchor(app, 0.0, 0.0, 0.0)  # anchor home at the origin
    out.set_pos(5.0, 0.0, 0.0)  # output currently parked away from the anchor
    # Both detections (world 5,0 and 9,0) lie outside the 2 m gate from (0,0).
    detector = _AssistDetector([_det(0.5, 1), _det(0.9, 2)])

    _run_assist(app, detector, monkeypatch)

    # Nothing in range → the output glides *toward* the anchor, not snaps:
    # 5 → 5 + 0.5·(0 − 5) = 2.5.
    assert out.pos[0] == pytest.approx(2.5)
    assert app._detection_pin_states[_PID].locked_track_id is None
    assert anchor.pos == (0.0, 0.0, 0.0)


def test_assist_glides_home_when_no_detections_at_all(monkeypatch) -> None:
    app = _assist_app(_assist_cfg(smoothing=0.5))
    out = app._server.get_marker(_PID)
    anchor = _seed_anchor(app, 2.0, 3.0, 1.0)
    out.set_pos(6.0, 3.0, 9.0)  # away from the anchor, with a stale Z

    _run_assist(app, _AssistDetector([]), monkeypatch)

    # x glides 6 → 4; y already equals the anchor; z snaps to the anchor's Z.
    assert out.pos[0] == pytest.approx(4.0)
    assert out.pos[1] == pytest.approx(3.0)
    assert out.pos[2] == pytest.approx(1.0)
    assert app._detection_pin_states[_PID].locked_track_id is None
    assert anchor.pos == (2.0, 3.0, 1.0)


def test_assist_is_a_noop_when_no_controlled_markers(monkeypatch) -> None:
    # With no controlled markers there is nothing to refine: assist must early
    # return without touching state (and without unprojecting anything).
    app = _make_app(detection_cfg=_assist_cfg(), selected_id=None, controlled_ids=[], resolution=(1000, 1000))
    _run_assist(app, _AssistDetector([_det(0.5, 1)]), monkeypatch)
    assert app._detection_pin_states == {}
    assert app._assist_manual == {}


def test_assist_output_z_follows_anchor(monkeypatch) -> None:
    app = _assist_app(_assist_cfg(assist_radius_m=50.0))  # smoothing 1, strength 1
    out = app._server.get_marker(_PID)
    out.set_pos(0.0, 0.0, 9.9)  # stale output Z
    anchor = _seed_anchor(app, 0.0, 0.0, 1.7)
    detector = _AssistDetector([_det(0.5, 1)])  # world (5,0)

    _run_assist(app, detector, monkeypatch)

    # Output X/Y track the person; output Z is always the anchor's Z.
    assert out.pos[2] == pytest.approx(1.7)
    assert anchor.pos == (0.0, 0.0, 1.7)


def test_assist_move_anchor_away_releases_lock_and_glides_home(monkeypatch) -> None:
    """The operator can drag the anchor off a locked person and the output
    follows the anchor instead of staying trapped."""
    app = _assist_app(_assist_cfg(assist_radius_m=2.0, smoothing=0.5))
    out = app._server.get_marker(_PID)
    anchor = _seed_anchor(app, 0.0, 0.0, 0.0)
    detector = _AssistDetector([_det(0.0, 1)])  # world (0,0), static

    # Frame 1: person at (0,0) within the 2 m gate → lock, output sits on them.
    _run_assist(app, detector, monkeypatch)
    assert app._detection_pin_states[_PID].locked_track_id == 1

    # Operator drags the anchor 20 m away.
    anchor.set_pos(20.0, 0.0, 0.0)

    # Frame 2: person now far outside the gate → lock released, output glides
    # toward the anchor.
    _run_assist(app, detector, monkeypatch)
    assert app._detection_pin_states[_PID].locked_track_id is None
    assert 0.0 < out.pos[0] < 20.0
    assert anchor.pos == (20.0, 0.0, 0.0)


def test_assist_glides_to_anchor_when_detector_missing(monkeypatch) -> None:
    """Detector absent (deps missing): input is still redirected to the ghost,
    so the output must glide to the anchor – never freeze."""
    app = _assist_app(_assist_cfg(smoothing=0.5))
    out = app._server.get_marker(_PID)
    anchor = _seed_anchor(app, 0.0, 0.0, 0.0)
    out.set_pos(4.0, 0.0, 0.0)

    _run_assist(app, None, monkeypatch)  # no live detector

    assert out.pos[0] == pytest.approx(2.0)  # 4 → 4 + 0.5·(0 − 4)
    assert app._detection_pin_states[_PID].locked_track_id is None
    assert anchor.pos == (0.0, 0.0, 0.0)


def test_assist_pin_does_not_write_the_manual_anchor(monkeypatch) -> None:
    app = _assist_app(_assist_cfg(assist_radius_m=50.0))
    anchor = _seed_anchor(app, 1.0, 2.0, 0.5)
    detector = _AssistDetector([_det(0.5, 1)])  # world (5,0), in range

    _run_assist(app, detector, monkeypatch)

    # The operator owns the anchor; the pin writes only the registered output.
    assert anchor.pos == (1.0, 2.0, 0.5)


def test_assist_follows_the_closest_track_each_frame(monkeypatch) -> None:
    # strength=0 targets the anchor so proximity selection is isolated from
    # output glide; we assert only the lock. Selection is purely
    # distance-to-anchor – a previously-chosen box never wins over a closer one.
    app = _assist_app(_assist_cfg(assist_radius_m=50.0, assist_strength=0.0))
    _seed_anchor(app, 0.0, 0.0, 0.0)

    # Frame 1: A at world (0,0) is nearest → lock track 1.
    _run_assist(app, _AssistDetector([_det(0.0, 1), _det(0.5, 2)]), monkeypatch)
    assert app._detection_pin_states[_PID].locked_track_id == 1

    # Frame 2: track 1 drifts to (8,0); track 2 sits at (5,0) – now closer.
    _run_assist(app, _AssistDetector([_det(0.8, 1), _det(0.5, 2)]), monkeypatch)
    assert app._detection_pin_states[_PID].locked_track_id == 2


def test_assist_reselects_when_locked_track_leaves_radius(monkeypatch) -> None:
    app = _assist_app(_assist_cfg(assist_radius_m=3.0, assist_strength=0.0))
    _seed_anchor(app, 0.0, 0.0, 0.0)

    # Frame 1: only track 1 at world (0,0) → lock 1.
    _run_assist(app, _AssistDetector([_det(0.0, 1)]), monkeypatch)
    assert app._detection_pin_states[_PID].locked_track_id == 1

    # Frame 2: track 1 → (8,0) outside the 3 m gate; track 2 at (1,0) is inside.
    _run_assist(app, _AssistDetector([_det(0.8, 1), _det(0.1, 2)]), monkeypatch)
    assert app._detection_pin_states[_PID].locked_track_id == 2


def test_assist_locked_track_absent_from_detections_falls_through(monkeypatch) -> None:
    app = _assist_app(_assist_cfg(assist_radius_m=50.0))
    _seed_anchor(app, 5.0, 0.0, 0.0)
    # Pre-seed a stale lock so the call must re-select the in-range detection.
    state = _get_pin_state(app, _PID)
    state.locked_track_id = 99

    _run_assist(app, _AssistDetector([_det(0.5, 1)]), monkeypatch)

    # The stale lock isn't found, so the nearest in-range detection is chosen.
    assert app._detection_pin_states[_PID].locked_track_id == 1


# ---------------------------------------------------------------------------
# Assist drives every controlled marker simultaneously
# ---------------------------------------------------------------------------


def test_assist_drives_two_controlled_markers_independently(monkeypatch) -> None:
    """Assist refines *every* controlled marker. Two markers, two anchors, each
    glides onto the detection nearest its own anchor, and each keeps its own
    per-marker pin state + ghost."""
    from openfollow.psn import Marker

    m1, m2 = Marker(1, ""), Marker(2, "")
    m1.set_pos(0.0, 0.0, 0.0)
    m2.set_pos(8.0, 0.0, 0.0)
    server = _MultiMarkerServer({1: m1, 2: m2})
    app = _make_app(
        detection_cfg=_assist_cfg(assist_radius_m=2.0),  # smoothing 1, strength 1
        server=server,
        selected_id=1,
        controlled_ids=[1, 2],
        resolution=(1000, 1000),
    )
    # Anchor 1 at (1,0), anchor 2 at (8,0).
    _seed_anchor(app, 1.0, 0.0, 0.0, mid=1)
    _seed_anchor(app, 8.0, 0.0, 0.0, mid=2)
    # Detection A → world (1,0) [near anchor 1]; B → world (8,0) [near anchor 2].
    detector = _AssistDetector([_det(0.1, 11), _det(0.8, 22)])

    _run_assist(app, detector, monkeypatch)

    # Each registered marker glides onto its own nearest detection.
    assert m1.pos[0] == pytest.approx(1.0)
    assert m2.pos[0] == pytest.approx(8.0)
    # Each marker owns its own state + ghost.
    assert set(app._detection_pin_states) == {1, 2}
    assert set(app._assist_manual) == {1, 2}
    s1, s2 = app._detection_pin_states[1], app._detection_pin_states[2]
    assert s1 is not s2
    assert s1.locked_track_id == 11
    assert s2.locked_track_id == 22
    assert s1.attached_marker_id == 1
    assert s2.attached_marker_id == 2


def test_assist_two_markers_glide_home_without_detector(monkeypatch) -> None:
    """No detector / no detections: every controlled marker glides toward its
    own anchor (operator keeps control), never snaps, never freezes."""
    from openfollow.psn import Marker

    m1, m2 = Marker(1, ""), Marker(2, "")
    server = _MultiMarkerServer({1: m1, 2: m2})
    app = _make_app(
        detection_cfg=_assist_cfg(smoothing=0.5),
        server=server,
        selected_id=1,
        controlled_ids=[1, 2],
        resolution=(1000, 1000),
    )
    _seed_anchor(app, 0.0, 0.0, 0.0, mid=1)
    _seed_anchor(app, 0.0, 0.0, 0.0, mid=2)
    m1.set_pos(4.0, 0.0, 0.0)  # parked away from anchor 1
    m2.set_pos(10.0, 0.0, 0.0)  # parked away from anchor 2

    _run_assist(app, None, monkeypatch)  # no detector

    # Each output glides toward its own anchor (origin), not snaps.
    assert m1.pos[0] == pytest.approx(2.0)  # 4 → 4 + 0.5·(0 − 4)
    assert m2.pos[0] == pytest.approx(5.0)  # 10 → 10 + 0.5·(0 − 10)
    assert app._detection_pin_states[1].locked_track_id is None
    assert app._detection_pin_states[2].locked_track_id is None


# ---------------------------------------------------------------------------
# Pruning – a marker leaving the driven set sheds its ghost + state
# ---------------------------------------------------------------------------


def test_assist_prunes_ghost_and_state_when_marker_leaves_controlled_ids(monkeypatch) -> None:
    """A marker dropped from ``controlled_marker_ids`` loses its ghost + pin
    state on the next assist call, so a later re-add re-seeds fresh."""
    from openfollow.psn import Marker

    m1, m2 = Marker(1, ""), Marker(2, "")
    server = _MultiMarkerServer({1: m1, 2: m2})
    app = _make_app(
        detection_cfg=_assist_cfg(assist_radius_m=50.0),
        server=server,
        selected_id=1,
        controlled_ids=[1, 2],
        resolution=(1000, 1000),
    )
    _seed_anchor(app, 0.0, 0.0, 0.0, mid=1)
    _seed_anchor(app, 0.0, 0.0, 0.0, mid=2)

    _run_assist(app, _AssistDetector([]), monkeypatch)
    assert set(app._assist_manual) == {1, 2}
    assert set(app._detection_pin_states) == {1, 2}

    # Operator removes marker 2 from the controlled set.
    app._controlled_ids = [1]
    _run_assist(app, _AssistDetector([]), monkeypatch)

    assert set(app._assist_manual) == {1}
    assert set(app._detection_pin_states) == {1}


def test_assist_prunes_ghost_when_disengaged(monkeypatch) -> None:
    app = _assist_app(_assist_cfg(assist_radius_m=50.0))
    _seed_anchor(app, 1.0, 0.0, 0.0)
    assert app._assist_manual  # ghost exists while assist is active

    # Operator switches to replace mode – the next pin call drops the ghost so
    # a later re-engage re-seeds from the live marker, not a stale anchor.
    app._config.detection.pin_mode = "replace"
    _run_assist(app, _StubDetector(_det(0.5, 1)), monkeypatch)
    assert app._assist_manual == {}


def test_assist_prunes_stale_ghost_when_pinned_id_changes(monkeypatch) -> None:
    from openfollow.psn import Marker

    app = _assist_app(_assist_cfg(assist_radius_m=50.0))
    app._assist_manual[5] = Marker(5, "")  # leftover from a previously-pinned id
    app._detection_pin_states[5] = DetectionPinState()  # and its stale state
    _seed_anchor(app, 0.0, 0.0, 0.0)  # current pinned id is _PID

    _run_assist(app, _AssistDetector([_det(0.5, 1)]), monkeypatch)

    # The stale ghost + state are dropped; only the current id survives.
    assert set(app._assist_manual) == {_PID}
    assert set(app._detection_pin_states) == {_PID}


# ---------------------------------------------------------------------------
# assist_active / is_assist_controlled predicate truth table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("enabled", "pin_mode", "expected"),
    [
        (True, "assist", True),
        (True, "replace", False),
        (False, "assist", False),
        (False, "replace", False),
    ],
)
def test_assist_active_truth_table(enabled: bool, pin_mode: str, expected: bool) -> None:
    cfg = DetectionConfig(enabled=enabled, pin_mode=pin_mode)
    app = _make_app(detection_cfg=cfg, controlled_ids=[_PID])
    assert assist_active(app) is expected


@pytest.mark.parametrize(
    ("enabled", "pin_mode", "marker_id", "expected"),
    [
        (True, "assist", _PID, True),  # active + controlled
        (True, "assist", 99, False),  # active but uncontrolled
        (True, "assist", None, False),  # no selection
        (True, "replace", _PID, False),  # wrong mode
        (False, "assist", _PID, False),  # detection off
    ],
)
def test_is_assist_controlled_truth_table(enabled: bool, pin_mode: str, marker_id: int | None, expected: bool) -> None:
    cfg = DetectionConfig(enabled=enabled, pin_mode=pin_mode)
    app = _make_app(detection_cfg=cfg, controlled_ids=[_PID])
    assert is_assist_controlled(app, marker_id) is expected


# ---------------------------------------------------------------------------
# Replace mode does not consult the assist path
# ---------------------------------------------------------------------------


def test_replace_mode_does_not_read_detections(monkeypatch) -> None:
    """``pin_mode=replace`` must not touch the assist path – proven by a
    detector whose ``detections`` property raises if read."""
    cfg = DetectionConfig(enabled=True, pin_mode="replace", smoothing=1.0)
    assert cfg.pin_mode == "replace"
    app = _make_app(detection_cfg=cfg, resolution=(1000, 1000))
    marker = app._server.get_marker(0)

    class _ReplaceOnlyDetector:
        def __init__(self, tracked: _StubDetection) -> None:
            self.tracked_detection = tracked

        @property
        def detections(self):  # noqa: ANN202
            raise AssertionError("replace mode must not consult .detections")

    detector = _ReplaceOnlyDetector(_det(0.5, 7))  # world (5,0)

    _run(app, detector, monkeypatch, unproject=_linear_unproject)

    # Replace path drove the marker from tracked_detection without error.
    assert marker.pos[0] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Attached-box highlight: the pin records which detection track drives which
# marker so the overlay can paint that one box in the marker's colour.
# ---------------------------------------------------------------------------


def test_assist_records_attached_track_and_marker(monkeypatch) -> None:
    app = _assist_app(_assist_cfg())
    _seed_anchor(app, 5.0, 0.0, 0.0)  # anchor by the in-range person at world (5,0)

    _run_assist(app, _AssistDetector([_det(0.5, 7), _det(0.9, 2)]), monkeypatch)

    state = app._detection_pin_states[_PID]
    assert state.locked_track_id == 7
    assert state.attached_track_id == 7
    assert state.attached_marker_id == _PID


def test_assist_clears_attached_track_when_gliding_home(monkeypatch) -> None:
    app = _assist_app(_assist_cfg(assist_radius_m=1.0))
    _seed_anchor(app, 0.0, 0.0, 0.0)  # nobody within 1m of the anchor

    _run_assist(app, _AssistDetector([_det(0.9, 7)]), monkeypatch)  # world (9,0)

    state = app._detection_pin_states[_PID]
    assert state.locked_track_id is None
    assert state.attached_track_id is None
    assert state.attached_marker_id == _PID


def test_replace_records_attached_track_and_marker(monkeypatch) -> None:
    cfg = DetectionConfig(enabled=True, pin_mode="replace", smoothing=1.0)
    app = _make_app(detection_cfg=cfg, resolution=(1000, 1000))

    _run(app, _StubDetector(_det(0.5, 4)), monkeypatch, unproject=_linear_unproject)

    state = app._detection_pin_states[0]
    assert state.attached_track_id == 4
    assert state.attached_marker_id == 0  # _StubMarker default id


def test_replace_clears_attached_track_when_detection_lost(monkeypatch) -> None:
    cfg = DetectionConfig(enabled=True, pin_mode="replace", smoothing=1.0)
    app = _make_app(detection_cfg=cfg, resolution=(1000, 1000))

    _run(app, _StubDetector(_det(0.5, 4)), monkeypatch, unproject=_linear_unproject)
    assert app._detection_pin_states[0].attached_track_id == 4

    # Detection lost – the attachment clears so no box stays lit.
    _run(app, _StubDetector(None))
    state = app._detection_pin_states[0]
    assert state.attached_track_id is None
    assert state.attached_marker_id is None


# ---------------------------------------------------------------------------
# Assist – edge paths
# ---------------------------------------------------------------------------


def test_get_or_create_manual_marker_unseeded_when_registered_missing() -> None:
    app = _make_app(detection_cfg=_assist_cfg(), server=_MultiMarkerServer({}))
    ghost = get_or_create_manual_marker(app, _PID)
    # No registered marker to seed from -> ghost stays at the origin default.
    assert ghost.pos == (0.0, 0.0, 0.0)
    assert app._assist_manual[_PID] is ghost


def test_assist_noop_when_out_marker_missing(monkeypatch) -> None:
    app = _make_app(
        detection_cfg=_assist_cfg(),
        server=_MultiMarkerServer({}),
        selected_id=_PID,
        controlled_ids=[_PID],
        resolution=(1000, 1000),
    )

    _run_assist(app, _AssistDetector([_det(0.5, 1)]), monkeypatch)

    # Output marker can't be resolved -> the loop skips that id before creating
    # any pin state, so nothing is driven and no glide is seeded.
    assert _PID not in app._detection_pin_states


def test_assist_noop_when_resolution_zero(monkeypatch) -> None:
    app = _make_app(
        detection_cfg=_assist_cfg(),
        selected_id=_PID,
        controlled_ids=[_PID],
        resolution=(0, 0),
    )
    _seed_anchor(app, 1.0, 0.0, 0.0)

    _run_assist(app, _AssistDetector([_det(0.5, 1)]), monkeypatch)

    state = app._detection_pin_states.get(_PID)
    assert state is None or state.ai_smooth_x is None


def test_assist_skips_non_finite_detection_in_nearest_search(monkeypatch) -> None:
    app = _assist_app(_assist_cfg(assist_radius_m=50.0))
    out = app._server.get_marker(_PID)
    _seed_anchor(app, 0.0, 0.0, 0.0)

    _run(
        app,
        _AssistDetector([_det(0.5, 1)]),
        monkeypatch,
        unproject=lambda *_a, **_k: np.array([[np.nan, np.nan, 0.0]]),
    )

    # The only detection unprojects to a non-finite point -> dropped; the
    # output glides home to the anchor (no lock).
    state = app._detection_pin_states[_PID]
    assert state.locked_track_id is None
    assert out.pos[0] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Pruning helpers (direct unit cover of the set-keyed signature)
# ---------------------------------------------------------------------------


def test_prune_manual_markers_keeps_set_drops_rest() -> None:
    from openfollow.psn import Marker

    app = _make_app(detection_cfg=_assist_cfg())
    app._assist_manual = {1: Marker(1, ""), 2: Marker(2, ""), 3: Marker(3, "")}
    _prune_manual_markers(app, keep={1, 3})
    assert set(app._assist_manual) == {1, 3}


def test_prune_pin_states_keeps_set_drops_rest() -> None:
    app = _make_app(detection_cfg=_assist_cfg())
    app._detection_pin_states = {1: DetectionPinState(), 2: DetectionPinState(), 3: DetectionPinState()}
    _prune_pin_states(app, keep={2})
    assert set(app._detection_pin_states) == {2}


def test_get_pin_state_is_per_marker_and_persistent() -> None:
    app = _make_app(detection_cfg=_assist_cfg())
    s1a = _get_pin_state(app, 1)
    s1b = _get_pin_state(app, 1)
    s2 = _get_pin_state(app, 2)
    assert s1a is s1b  # same id reuses the same state
    assert s1a is not s2  # different ids are independent


# --------------------------------------------------------------------------- #
# Frame-rate-independent smoothing / prediction (time-aware filter)
# --------------------------------------------------------------------------- #


def test_ema_factor_is_exact_at_nominal_and_compounds() -> None:
    # At one nominal frame the alpha is returned unchanged (no float drift).
    assert _ema_factor(0.3, 1.0) == 0.3
    assert _ema_factor(0.15, 1.0) == 0.15
    # Two frames compound the retention: 1 - (1-a)^2.
    assert _ema_factor(0.2, 2.0) == pytest.approx(1.0 - 0.8**2)
    # A full-snap alpha stays full at any step count.
    assert _ema_factor(1.0, 3.0) == 1.0


def test_dt_steps_clamps_and_normalises() -> None:
    assert _dt_steps(_NOMINAL_FRAME_DT) == pytest.approx(1.0)
    assert _dt_steps(2 * _NOMINAL_FRAME_DT) == pytest.approx(2.0)
    # A huge stall is clamped so the filter can't take an unbounded step.
    assert _dt_steps(100.0) == _dt_steps(10.0)


def test_advance_smoothing_nominal_matches_legacy_formula() -> None:
    cfg = SimpleNamespace(detection=DetectionConfig(enabled=False, prediction=2.0, smoothing=0.5))
    st = DetectionPinState()

    # First call seeds (velocity 0 -> predicted == target).
    assert _advance_smoothing(st, 1.0, 2.0, cfg, _NOMINAL_FRAME_DT) == (1.0, 2.0)

    # Second call: vel = 0.3*delta, predicted = target + vel*prediction,
    # smooth += smoothing*(predicted - smooth).
    x2, y2 = _advance_smoothing(st, 2.0, 2.0, cfg, _NOMINAL_FRAME_DT)
    vel = 0.3 * (2.0 - 1.0)
    predicted = 2.0 + vel * 2.0
    assert x2 == pytest.approx(1.0 + 0.5 * (predicted - 1.0))
    assert y2 == pytest.approx(2.0)


def test_pin_velocity_is_frame_rate_independent() -> None:
    """A target moving at a constant world speed converges to the same
    per-nominal-frame velocity whether sampled at 60fps or 30fps."""
    cfg = SimpleNamespace(detection=DetectionConfig(enabled=False, prediction=8.0, smoothing=0.5))
    speed = 2.0  # world units per second
    nominal = _NOMINAL_FRAME_DT

    s60 = DetectionPinState()
    x = 0.0
    for _ in range(300):
        x += speed * nominal  # one 60fps frame of travel
        _advance_smoothing(s60, x, 0.0, cfg, nominal)

    s30 = DetectionPinState()
    x = 0.0
    for _ in range(300):
        x += speed * (2 * nominal)  # one 30fps frame of travel
        _advance_smoothing(s30, x, 0.0, cfg, 2 * nominal)

    expected = speed * nominal  # per-nominal-frame velocity
    assert s60.vel_x == pytest.approx(expected, rel=1e-3)
    assert s30.vel_x == pytest.approx(expected, rel=1e-3)
