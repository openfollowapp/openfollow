# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the detection-pinning helper.

The helper snaps the selected marker onto a visually-tracked detection,
with velocity prediction + EMA smoothing applied each frame.  These
tests drive it through a ``SimpleNamespace`` app graph (no GTK, no
GStreamer) and a deterministic stub detector so we can assert on the
exact update rule without flakiness.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from openfollow.configuration import CameraConfig, DetectionConfig, GridConfig
from openfollow.runtime.services_detection_pin import (
    DetectionPinState,
    apply_detection_pin,
    assist_pinned_marker_id,
    get_or_create_manual_marker,
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
    def __init__(self, marker) -> None:  # noqa: ANN001
        self._marker = marker

    def get_marker(self, _tid: int):  # noqa: ANN202
        return self._marker


class _StubVideoReceiver:
    def __init__(self, resolution: tuple[int, int]) -> None:
        self.resolution = resolution


def _make_app(
    *,
    detection_cfg: DetectionConfig | None = None,
    resolution: tuple[int, int] = (1920, 1080),
    marker: _StubMarker | None = None,
    selected_id: int | None = 0,
    controlled_ids: list[int] | None = None,
) -> SimpleNamespace:
    detection_cfg = detection_cfg or DetectionConfig(
        enabled=True,
        pin_marker=True,
        pin_mode="replace",
        smoothing=0.5,
        prediction=0.0,
    )
    grid = GridConfig()
    marker = marker or _StubMarker()
    return SimpleNamespace(
        _config=SimpleNamespace(detection=detection_cfg, grid=grid, camera=CameraConfig()),
        _video_receiver=_StubVideoReceiver(resolution),
        _camera=_StubCamera(),
        _server=_StubServer(marker),
        _assist_manual={},
        _selected_id=selected_id,
        # ``pin_marker_id`` looks at controlled_ids when the
        # operator picks a fixed ID. Default to a single-marker
        # list matching ``selected_id`` (or empty when there's no
        # Legacy tests still find a marker, and non-default selected_id doesn't fall back to [0].
        _controlled_ids=(
            controlled_ids if controlled_ids is not None else ([selected_id] if selected_id is not None else [])
        ),
    )


def _buffers():  # noqa: ANN202
    return (
        np.zeros(7, dtype=np.float64),
        np.zeros((1, 2), dtype=np.float64),
    )


def test_returns_early_when_pin_marker_disabled() -> None:
    cfg = DetectionConfig(enabled=True, pin_marker=False)
    app = _make_app(detection_cfg=cfg)
    detector = _StubDetector(_StubDetection(0.4, 0.4, 0.6, 0.6))
    cam, pt = _buffers()
    state = DetectionPinState()
    marker = app._server.get_marker(0)
    before = marker.pos

    apply_detection_pin(
        app,
        person_detector=detector,
        unproject_cam_buffer=cam,
        screen_point_buffer=pt,
        pin_state=state,
    )

    assert marker.pos == before
    assert state.smooth_x is None and state.smooth_y is None


def test_noop_when_detector_is_none() -> None:
    app = _make_app()
    cam, pt = _buffers()
    state = DetectionPinState()
    marker = app._server.get_marker(0)
    before = marker.pos

    apply_detection_pin(
        app,
        person_detector=None,
        unproject_cam_buffer=cam,
        screen_point_buffer=pt,
        pin_state=state,
    )

    assert marker.pos == before


def test_noop_when_no_tracked_detection() -> None:
    app = _make_app()
    detector = _StubDetector(None)
    cam, pt = _buffers()
    state = DetectionPinState()
    marker = app._server.get_marker(0)

    apply_detection_pin(
        app,
        person_detector=detector,
        unproject_cam_buffer=cam,
        screen_point_buffer=pt,
        pin_state=state,
    )
    assert marker.pos == (0.0, 0.0, 0.0)


def test_noop_when_video_resolution_zero() -> None:
    app = _make_app(resolution=(0, 0))
    detector = _StubDetector(_StubDetection(0.4, 0.4, 0.6, 0.6))
    cam, pt = _buffers()
    state = DetectionPinState()
    marker = app._server.get_marker(0)

    apply_detection_pin(
        app,
        person_detector=detector,
        unproject_cam_buffer=cam,
        screen_point_buffer=pt,
        pin_state=state,
    )
    assert marker.pos == (0.0, 0.0, 0.0)


def test_noop_when_no_selected_marker() -> None:
    app = _make_app(selected_id=None)
    detector = _StubDetector(_StubDetection(0.4, 0.4, 0.6, 0.6))
    cam, pt = _buffers()
    state = DetectionPinState()

    apply_detection_pin(
        app,
        person_detector=detector,
        unproject_cam_buffer=cam,
        screen_point_buffer=pt,
        pin_state=state,
    )
    # Nothing to assert except that the call returned without mutating state.
    assert state.smooth_x is None


def test_noop_when_marker_missing() -> None:
    class _NoneServer:
        def get_marker(self, _tid: int):  # noqa: ANN202
            return None

    app = _make_app()
    app._server = _NoneServer()
    detector = _StubDetector(_StubDetection(0.4, 0.4, 0.6, 0.6))
    cam, pt = _buffers()
    state = DetectionPinState()

    apply_detection_pin(
        app,
        person_detector=detector,
        unproject_cam_buffer=cam,
        screen_point_buffer=pt,
        pin_state=state,
    )
    assert state.smooth_x is None


def test_pin_point_bottom_uses_box_y2(monkeypatch) -> None:
    cfg = DetectionConfig(
        enabled=True,
        pin_marker=True,
        pin_mode="replace",
        pin_point="bottom",
        smoothing=1.0,
    )
    app = _make_app(detection_cfg=cfg)
    detector = _StubDetector(_StubDetection(0.4, 0.2, 0.6, 0.8))
    cam, pt = _buffers()
    state = DetectionPinState()

    recorded: dict[str, float] = {}

    import openfollow.runtime.services_detection_pin as module

    def _stub_unproject(_params, screen_pt, _w, _h, plane_z):  # noqa: ANN001, ANN202
        recorded["plane_z"] = plane_z
        recorded["screen_y"] = float(screen_pt[0, 1])
        return np.array([[1.0, 2.0, 0.0]], dtype=np.float64)

    monkeypatch.setattr(module, "unproject_to_plane", _stub_unproject)
    marker = app._server.get_marker(0)

    apply_detection_pin(
        app,
        person_detector=detector,
        unproject_cam_buffer=cam,
        screen_point_buffer=pt,
        pin_state=state,
    )

    # Screen Y for pin_point=bottom is ``y2 * h`` (0.8 * 1080 = 864).
    assert recorded["screen_y"] == pytest.approx(0.8 * 1080)
    # pin_point=bottom uses the grid plane (z_offset), not marker.pos[2].
    assert recorded["plane_z"] == pytest.approx(app._config.grid.z_offset)
    # marker.pos is PSN-absolute, matching the unprojected world point –
    # no grid-offset translation.
    assert marker.pos[0] == pytest.approx(1.0)
    assert marker.pos[1] == pytest.approx(2.0)


def test_pin_point_top_uses_box_y1(monkeypatch) -> None:
    cfg = DetectionConfig(
        enabled=True,
        pin_marker=True,
        pin_mode="replace",
        pin_point="top",
        smoothing=1.0,
    )
    app = _make_app(detection_cfg=cfg)
    detector = _StubDetector(_StubDetection(0.4, 0.2, 0.6, 0.8))
    cam, pt = _buffers()
    state = DetectionPinState()
    marker = app._server.get_marker(0)
    marker.set_pos(0.0, 0.0, 1.5)

    recorded: dict[str, float] = {}

    import openfollow.runtime.services_detection_pin as module

    def _stub_unproject(_params, screen_pt, _w, _h, plane_z):  # noqa: ANN001, ANN202
        recorded["plane_z"] = plane_z
        recorded["screen_y"] = float(screen_pt[0, 1])
        return np.array([[5.0, 6.0, 0.0]], dtype=np.float64)

    monkeypatch.setattr(module, "unproject_to_plane", _stub_unproject)

    apply_detection_pin(
        app,
        person_detector=detector,
        unproject_cam_buffer=cam,
        screen_point_buffer=pt,
        pin_state=state,
    )

    # Screen Y for pin_point=top is ``y1 * h`` (0.2 * 1080 = 216).
    assert recorded["screen_y"] == pytest.approx(0.2 * 1080)
    # pin_point=top samples at the marker's current z.
    assert recorded["plane_z"] == pytest.approx(1.5)


def test_non_finite_world_point_is_dropped(monkeypatch) -> None:
    app = _make_app()
    detector = _StubDetector(_StubDetection(0.4, 0.4, 0.6, 0.6))
    cam, pt = _buffers()
    state = DetectionPinState()

    import openfollow.runtime.services_detection_pin as module

    monkeypatch.setattr(
        module,
        "unproject_to_plane",
        lambda *_a, **_k: np.array([[np.inf, np.nan, 0.0]]),
    )
    marker = app._server.get_marker(0)

    apply_detection_pin(
        app,
        person_detector=detector,
        unproject_cam_buffer=cam,
        screen_point_buffer=pt,
        pin_state=state,
    )
    assert marker.pos == (0.0, 0.0, 0.0)
    assert state.smooth_x is None


def test_first_frame_initialises_smoothed_position_without_lag(monkeypatch) -> None:
    cfg = DetectionConfig(
        enabled=True,
        pin_marker=True,
        pin_mode="replace",
        smoothing=0.15,  # heavy smoothing
    )
    app = _make_app(detection_cfg=cfg)
    detector = _StubDetector(_StubDetection(0.4, 0.4, 0.6, 0.6))
    cam, pt = _buffers()
    state = DetectionPinState()

    import openfollow.runtime.services_detection_pin as module

    monkeypatch.setattr(
        module,
        "unproject_to_plane",
        lambda *_a, **_k: np.array([[10.0, 20.0, 0.0]]),
    )
    marker = app._server.get_marker(0)

    apply_detection_pin(
        app,
        person_detector=detector,
        unproject_cam_buffer=cam,
        screen_point_buffer=pt,
        pin_state=state,
    )

    # Smoothed state and marker position should equal the raw target on
    # the first frame – zero lag regardless of the smoothing coefficient.
    # Target is PSN-absolute: equal to the unprojected world point.
    assert state.smooth_x == pytest.approx(10.0)
    assert state.smooth_y == pytest.approx(20.0)
    assert marker.pos[0] == pytest.approx(10.0)


def test_second_frame_applies_ema_interpolation(monkeypatch) -> None:
    cfg = DetectionConfig(
        enabled=True,
        pin_marker=True,
        pin_mode="replace",
        smoothing=0.5,
        prediction=0.0,
    )
    app = _make_app(detection_cfg=cfg)
    detector = _StubDetector(_StubDetection(0.4, 0.4, 0.6, 0.6))
    cam, pt = _buffers()
    state = DetectionPinState()

    world_points = iter(
        [
            np.array([[10.0, 20.0, 0.0]]),
            np.array([[20.0, 40.0, 0.0]]),
        ]
    )

    import openfollow.runtime.services_detection_pin as module

    monkeypatch.setattr(
        module,
        "unproject_to_plane",
        lambda *_a, **_k: next(world_points),
    )
    marker = app._server.get_marker(0)

    apply_detection_pin(
        app,
        person_detector=detector,
        unproject_cam_buffer=cam,
        screen_point_buffer=pt,
        pin_state=state,
    )
    assert state.smooth_x == pytest.approx(10.0)

    apply_detection_pin(
        app,
        person_detector=detector,
        unproject_cam_buffer=cam,
        screen_point_buffer=pt,
        pin_state=state,
    )
    # alpha=0.5 → smooth_x = prev + 0.5*(new - prev) = 15.0 (PSN-absolute).
    assert state.smooth_x == pytest.approx(15.0)
    assert state.smooth_y == pytest.approx(30.0)
    assert marker.pos[0] == pytest.approx(15.0)


def test_velocity_prediction_extrapolates_target(monkeypatch) -> None:
    cfg = DetectionConfig(
        enabled=True,
        pin_marker=True,
        pin_mode="replace",
        smoothing=1.0,
        prediction=1.0,
    )
    app = _make_app(detection_cfg=cfg)
    detector = _StubDetector(_StubDetection(0.4, 0.4, 0.6, 0.6))
    cam, pt = _buffers()
    state = DetectionPinState()

    world_points = iter(
        [
            np.array([[0.0, 0.0, 0.0]]),
            np.array([[1.0, 0.0, 0.0]]),
            np.array([[2.0, 0.0, 0.0]]),
        ]
    )

    import openfollow.runtime.services_detection_pin as module

    monkeypatch.setattr(
        module,
        "unproject_to_plane",
        lambda *_a, **_k: next(world_points),
    )
    marker = app._server.get_marker(0)

    # Frame 1 (seeds).
    apply_detection_pin(
        app,
        person_detector=detector,
        unproject_cam_buffer=cam,
        screen_point_buffer=pt,
        pin_state=state,
    )
    # Frame 2: velocity EMA sees first nonzero delta.
    apply_detection_pin(
        app,
        person_detector=detector,
        unproject_cam_buffer=cam,
        screen_point_buffer=pt,
        pin_state=state,
    )
    vx_after_f2 = state.vel_x
    # vel_alpha = 0.3 on first nonzero delta of 1.0
    assert vx_after_f2 == pytest.approx(0.3)

    # Frame 3: prediction=1.0 means marker lands at target + vel*1.
    apply_detection_pin(
        app,
        person_detector=detector,
        unproject_cam_buffer=cam,
        screen_point_buffer=pt,
        pin_state=state,
    )
    # Target for frame 3 is 2.0 (PSN-absolute), and vel_x EMA advanced again:
    # vel_x <- 0.3 + 0.3 * (1.0 - 0.3) = 0.51
    assert state.vel_x == pytest.approx(0.51)
    # predicted_x = target_x + vel_x * 1.0; smoothing=1.0 snaps exactly.
    assert marker.pos[0] == pytest.approx(2.0 + 0.51)


# ---------------------------------------------------------------------------
# pin_marker_id – explicit-ID setting
# ---------------------------------------------------------------------------


class _RecordingServer:
    """Records every ``get_marker(tid)`` call so tests can verify
    which marker the pin path queried – necessary because the
    ``_StubServer`` returns the same marker regardless of ID."""

    def __init__(self, by_id: dict[int, _StubMarker]) -> None:
        self._by_id = by_id
        self.lookups: list[int] = []

    def get_marker(self, tid: int) -> _StubMarker | None:
        self.lookups.append(tid)
        return self._by_id.get(tid)


def test_pin_marker_id_default_minus_one_follows_selected(monkeypatch) -> None:
    cfg = DetectionConfig(
        enabled=True,
        pin_marker=True,
        pin_mode="replace",
        smoothing=1.0,
        pin_marker_id=-1,
    )
    marker_3 = _StubMarker()
    server = _RecordingServer({3: marker_3})
    app = _make_app(
        detection_cfg=cfg,
        controlled_ids=[3],
        selected_id=3,
    )
    app._server = server
    detector = _StubDetector(_StubDetection(0.4, 0.4, 0.6, 0.6))
    cam, pt = _buffers()
    state = DetectionPinState()

    import openfollow.runtime.services_detection_pin as module

    monkeypatch.setattr(
        module,
        "unproject_to_plane",
        lambda *_a, **_k: np.array([[1.0, 2.0, 0.0]], dtype=np.float64),
    )

    apply_detection_pin(
        app,
        person_detector=detector,
        unproject_cam_buffer=cam,
        screen_point_buffer=pt,
        pin_state=state,
    )

    # Server was queried for the controller-selected marker (id 3).
    assert server.lookups == [3]
    assert marker_3.pos[0] == pytest.approx(1.0)


def test_pin_marker_id_explicit_id_overrides_selected(monkeypatch) -> None:
    """A non-negative ``pin_marker_id`` pins to that exact marker
    regardless of which one the controller has selected. This is the
    workflow where the operator wants 'detection always drives
    marker 1' even if they're flipping between markers in the
    handheld."""
    cfg = DetectionConfig(
        enabled=True,
        pin_marker=True,
        pin_mode="replace",
        smoothing=1.0,
        pin_marker_id=5,
    )
    marker_5 = _StubMarker()
    marker_3 = _StubMarker()
    server = _RecordingServer({3: marker_3, 5: marker_5})
    app = _make_app(
        detection_cfg=cfg,
        controlled_ids=[3, 5],
        selected_id=3,
    )
    app._server = server
    detector = _StubDetector(_StubDetection(0.4, 0.4, 0.6, 0.6))
    cam, pt = _buffers()
    state = DetectionPinState()

    import openfollow.runtime.services_detection_pin as module

    monkeypatch.setattr(
        module,
        "unproject_to_plane",
        lambda *_a, **_k: np.array([[1.0, 2.0, 0.0]], dtype=np.float64),
    )

    apply_detection_pin(
        app,
        person_detector=detector,
        unproject_cam_buffer=cam,
        screen_point_buffer=pt,
        pin_state=state,
    )

    # Server was queried for the configured ID (5), not the
    # controller's selection (3).
    assert server.lookups == [5]
    # Marker 5 moved; marker 3 stays at origin.
    assert marker_5.pos[0] == pytest.approx(1.0)
    assert marker_3.pos == (0.0, 0.0, 0.0)


def test_pin_marker_id_skips_when_id_not_in_controlled_ids() -> None:
    """When ``pin_marker_id`` points at an ID that's NOT in
    ``controlled_marker_ids`` (e.g. operator removed the marker
    from ``[markers]`` but left it in ``[detection]``), the runtime
    skips the pin entirely. Falling back to the controller-selected
    marker would silently re-target which is worse than no pin –
    the runtime stats already surface ``pinned_track_id`` for the
    operator to spot the misconfiguration."""
    cfg = DetectionConfig(
        enabled=True,
        pin_marker=True,
        pin_mode="replace",
        smoothing=1.0,
        pin_marker_id=99,
    )
    marker_3 = _StubMarker()
    server = _RecordingServer({3: marker_3})
    app = _make_app(
        detection_cfg=cfg,
        controlled_ids=[3],
        selected_id=3,
    )
    app._server = server
    detector = _StubDetector(_StubDetection(0.4, 0.4, 0.6, 0.6))
    cam, pt = _buffers()
    state = DetectionPinState()

    apply_detection_pin(
        app,
        person_detector=detector,
        unproject_cam_buffer=cam,
        screen_point_buffer=pt,
        pin_state=state,
    )

    # No marker lookup happened – the route bailed out at the
    # controlled-ids guard.
    assert server.lookups == []
    assert marker_3.pos == (0.0, 0.0, 0.0)
    assert state.smooth_x is None


# ---------------------------------------------------------------------------
# Assist (hybrid) mode – operator drives, detection refines toward the nearest
# person within the gate radius. Uses a linear unproject stub so the world
# point of a box is just its centre fraction × 10 (along the x axis when y1=0).
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


def _patch_linear_unproject(monkeypatch) -> None:  # noqa: ANN001
    import openfollow.runtime.services_detection_pin as module

    monkeypatch.setattr(module, "unproject_to_plane", _linear_unproject)


def _assist_cfg(**overrides) -> DetectionConfig:  # noqa: ANN003
    base = {
        "enabled": True,
        "pin_marker": True,
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


def _seed_anchor(app: SimpleNamespace, x: float, y: float, z: float) -> object:
    """Create + position the operator-steered manual ghost for the pinned id.

    Mirrors what the input resolver does the first time the operator nudges the
    marker in assist mode: lazily creates the ghost (seeded from the registered
    marker), then moves it. Returns the ghost so tests can assert the pin never
    writes it.
    """
    anchor = get_or_create_manual_marker(app, _PID)
    anchor.set_pos(x, y, z)
    return anchor


def _run_assist(app, detector, state, monkeypatch):  # noqa: ANN001, ANN202
    cam, pt = _buffers()
    _patch_linear_unproject(monkeypatch)
    apply_detection_pin(
        app,
        person_detector=detector,
        unproject_cam_buffer=cam,
        screen_point_buffer=pt,
        pin_state=state,
    )


def test_assist_clips_output_to_nearest_person_within_radius(monkeypatch) -> None:
    app = _assist_app(_assist_cfg())  # smoothing 1.0, strength 1.0, radius 2.0
    out = app._server.get_marker(_PID)
    anchor = _seed_anchor(app, 4.0, 0.0, 0.0)  # operator parks the anchor at (4,0)
    # A → world (5,0) dist 1 (in radius 2); B → world (9,0) dist 5 (out).
    detector = _AssistDetector([_det(0.5, 1), _det(0.9, 2)])
    state = DetectionPinState()

    _run_assist(app, detector, state, monkeypatch)

    # Full glide (smoothing 1) lands the output exactly on the nearest in-range
    # person; proximity to the anchor – not box size / order – chooses who.
    assert out.pos[0] == pytest.approx(5.0)
    assert out.pos[1] == pytest.approx(0.0)
    assert state.locked_track_id == 1
    # The pin must never write the operator's anchor.
    assert anchor.pos == (4.0, 0.0, 0.0)


def test_assist_clip_strength_blends_output_toward_anchor(monkeypatch) -> None:
    cfg = _assist_cfg(assist_radius_m=50.0, assist_strength=0.5)
    app = _assist_app(cfg)
    out = app._server.get_marker(_PID)
    anchor = _seed_anchor(app, 0.0, 0.0, 0.0)
    detector = _AssistDetector([_det(1.0, 1)])  # world (10,0)
    state = DetectionPinState()

    _run_assist(app, detector, state, monkeypatch)

    # In-range target = anchor + strength·(person − anchor) = 0 + 0.5·(10 − 0)
    # = 5; full glide (smoothing 1) lands the output there this frame.
    assert out.pos[0] == pytest.approx(5.0)
    assert state.locked_track_id == 1
    assert anchor.pos == (0.0, 0.0, 0.0)


def test_assist_output_glides_onto_person_and_never_snaps(monkeypatch) -> None:
    """Strength 1 clips to the person, but the OUTPUT still glides over frames
    (never snaps), and the operator's anchor stays put – they keep full freedom.
    """
    cfg = _assist_cfg(assist_radius_m=50.0, smoothing=0.5)
    app = _assist_app(cfg)
    out = app._server.get_marker(_PID)
    anchor = _seed_anchor(app, 0.0, 0.0, 0.0)
    detector = _AssistDetector([_det(1.0, 1)])  # world (10,0), static
    state = DetectionPinState()

    def _step() -> float:
        _run_assist(app, detector, state, monkeypatch)
        return out.pos[0]

    # Geometric glide toward the person: 5 → 7.5 → 8.75 (each frame halves the gap).
    assert _step() == pytest.approx(5.0)
    assert _step() == pytest.approx(7.5)
    assert _step() == pytest.approx(8.75)
    for _ in range(40):
        _step()
    assert out.pos[0] == pytest.approx(10.0, abs=1e-3)  # settles onto the person
    # The anchor never moved – the operator was hands-off but free to steer.
    assert anchor.pos == (0.0, 0.0, 0.0)


def test_assist_glides_home_when_no_detection_in_radius(monkeypatch) -> None:
    cfg = _assist_cfg(smoothing=0.5)  # radius 2.0
    app = _assist_app(cfg)
    out = app._server.get_marker(_PID)
    anchor = _seed_anchor(app, 0.0, 0.0, 0.0)  # anchor home at the origin
    out.set_pos(5.0, 0.0, 0.0)  # output currently parked away from the anchor
    # Both detections (world 5,0 and 9,0) lie outside the 2 m gate from (0,0).
    detector = _AssistDetector([_det(0.5, 1), _det(0.9, 2)])
    state = DetectionPinState()

    _run_assist(app, detector, state, monkeypatch)

    # Nothing in range → the output glides *toward* the anchor, not snaps:
    # 5 → 5 + 0.5·(0 − 5) = 2.5.
    assert out.pos[0] == pytest.approx(2.5)
    assert state.locked_track_id is None
    assert anchor.pos == (0.0, 0.0, 0.0)


def test_assist_glides_home_when_no_detections_at_all(monkeypatch) -> None:
    cfg = _assist_cfg(smoothing=0.5)
    app = _assist_app(cfg)
    out = app._server.get_marker(_PID)
    anchor = _seed_anchor(app, 2.0, 3.0, 1.0)
    out.set_pos(6.0, 3.0, 9.0)  # away from the anchor, with a stale Z
    state = DetectionPinState()

    _run_assist(app, _AssistDetector([]), state, monkeypatch)

    # x glides 6 → 4; y already equals the anchor; z snaps to the anchor's Z.
    assert out.pos[0] == pytest.approx(4.0)
    assert out.pos[1] == pytest.approx(3.0)
    assert out.pos[2] == pytest.approx(1.0)
    assert state.locked_track_id is None
    assert anchor.pos == (2.0, 3.0, 1.0)


def test_assist_output_z_follows_anchor(monkeypatch) -> None:
    cfg = _assist_cfg(assist_radius_m=50.0)  # smoothing 1, strength 1
    app = _assist_app(cfg)
    out = app._server.get_marker(_PID)
    out.set_pos(0.0, 0.0, 9.9)  # stale output Z
    anchor = _seed_anchor(app, 0.0, 0.0, 1.7)
    detector = _AssistDetector([_det(0.5, 1)])  # world (5,0)
    state = DetectionPinState()

    _run_assist(app, detector, state, monkeypatch)

    # Output X/Y track the person; output Z is always the anchor's Z.
    assert out.pos[2] == pytest.approx(1.7)
    assert anchor.pos == (0.0, 0.0, 1.7)


def test_assist_move_anchor_away_releases_lock_and_glides_home(monkeypatch) -> None:
    """The headline regression: the operator can drag the anchor off a locked
    person and the output follows the anchor instead of staying trapped."""
    cfg = _assist_cfg(assist_radius_m=2.0, smoothing=0.5)
    app = _assist_app(cfg)
    out = app._server.get_marker(_PID)
    anchor = _seed_anchor(app, 0.0, 0.0, 0.0)
    detector = _AssistDetector([_det(0.0, 1)])  # world (0,0), static
    state = DetectionPinState()

    # Frame 1: person at (0,0) within the 2 m gate → lock, output sits on them.
    _run_assist(app, detector, state, monkeypatch)
    assert state.locked_track_id == 1

    # Operator drags the anchor 20 m away (the move the old design forbade).
    anchor.set_pos(20.0, 0.0, 0.0)

    # Frame 2: the person is now far outside the gate → lock released, output
    # glides toward the anchor (never snapping, never stuck on the performer).
    _run_assist(app, detector, state, monkeypatch)
    assert state.locked_track_id is None
    assert 0.0 < out.pos[0] < 20.0
    assert anchor.pos == (20.0, 0.0, 0.0)


def test_assist_glides_to_anchor_when_detector_missing(monkeypatch) -> None:
    """Detector absent (e.g. detection deps missing): input is still redirected
    to the ghost, so the output must glide to the anchor – never freeze."""
    cfg = _assist_cfg(smoothing=0.5)
    app = _assist_app(cfg)
    out = app._server.get_marker(_PID)
    anchor = _seed_anchor(app, 0.0, 0.0, 0.0)
    out.set_pos(4.0, 0.0, 0.0)
    state = DetectionPinState()

    _run_assist(app, None, state, monkeypatch)  # no live detector

    assert out.pos[0] == pytest.approx(2.0)  # 4 → 4 + 0.5·(0 − 4)
    assert state.locked_track_id is None
    assert anchor.pos == (0.0, 0.0, 0.0)


def test_assist_reseeds_glide_when_switching_markers(monkeypatch) -> None:
    """Switching the assist-controlled marker must not sweep the new marker from
    the old one's position: the never-reset outer glide re-seeds from the new
    marker so the output starts on it."""
    from openfollow.psn import Marker

    m1, m2 = Marker(1, ""), Marker(2, "")
    m1.set_pos(0.0, 0.0, 0.0)
    m2.set_pos(10.0, 0.0, 0.0)
    markers = {1: m1, 2: m2}

    class _MultiServer:
        def get_marker(self, tid):  # noqa: ANN001, ANN202
            return markers.get(tid)

    app = _make_app(
        detection_cfg=_assist_cfg(smoothing=0.5),
        selected_id=1,
        controlled_ids=[1, 2],
        resolution=(1000, 1000),
    )
    app._server = _MultiServer()
    state = DetectionPinState()

    # Drive marker 1 with nobody in range → output glides home, stays at x=0.
    _run_assist(app, _AssistDetector([]), state, monkeypatch)
    assert state.glide_marker_id == 1
    assert m1.pos[0] == pytest.approx(0.0)

    # Operator selects marker 2 (at x=10). The output must START on it – not
    # glide from marker 1's x=0, which would land mid-way at x=5.
    app._selected_id = 2
    _run_assist(app, _AssistDetector([]), state, monkeypatch)
    assert state.glide_marker_id == 2
    assert m2.pos[0] == pytest.approx(10.0)
    assert m1.pos[0] == pytest.approx(0.0)  # the deselected marker is left alone


def test_assist_pin_does_not_write_the_manual_anchor(monkeypatch) -> None:
    cfg = _assist_cfg(assist_radius_m=50.0)
    app = _assist_app(cfg)
    anchor = _seed_anchor(app, 1.0, 2.0, 0.5)
    detector = _AssistDetector([_det(0.5, 1)])  # world (5,0), in range
    state = DetectionPinState()

    _run_assist(app, detector, state, monkeypatch)

    # The operator owns the anchor; the pin writes only the registered output.
    assert anchor.pos == (1.0, 2.0, 0.5)


def test_assist_follows_the_closest_track_each_frame(monkeypatch) -> None:
    # strength=0 targets the anchor so the proximity selection is isolated from
    # output glide; we assert only the lock here. The selection is purely
    # distance-to-anchor – a previously-chosen (or bigger) box never wins over a
    # closer one, so moving the scene re-targets the AI to whoever is nearest.
    cfg = _assist_cfg(assist_radius_m=50.0, assist_strength=0.0)
    app = _assist_app(cfg)
    _seed_anchor(app, 0.0, 0.0, 0.0)
    state = DetectionPinState()

    # Frame 1: A at world (0,0) is nearest → lock track 1.
    _run_assist(app, _AssistDetector([_det(0.0, 1), _det(0.5, 2)]), state, monkeypatch)
    assert state.locked_track_id == 1

    # Frame 2: track 1 drifts to world (8,0); track 2 sits at (5,0) – now closer.
    # Both stay inside the radius, but the lock hands over to the nearer track 2.
    _run_assist(app, _AssistDetector([_det(0.8, 1), _det(0.5, 2)]), state, monkeypatch)
    assert state.locked_track_id == 2


def test_assist_reselects_when_locked_track_leaves_radius(monkeypatch) -> None:
    cfg = _assist_cfg(assist_radius_m=3.0, assist_strength=0.0)
    app = _assist_app(cfg)
    _seed_anchor(app, 0.0, 0.0, 0.0)
    state = DetectionPinState()

    # Frame 1: only track 1 at world (0,0) → lock 1.
    _run_assist(app, _AssistDetector([_det(0.0, 1)]), state, monkeypatch)
    assert state.locked_track_id == 1

    # Frame 2: track 1 moves to world (8,0) – outside the 3 m gate; track 2
    # at world (1,0) is inside. The lock hands over to track 2.
    _run_assist(app, _AssistDetector([_det(0.8, 1), _det(0.1, 2)]), state, monkeypatch)
    assert state.locked_track_id == 2


def test_assist_prunes_ghost_when_disengaged(monkeypatch) -> None:
    app = _assist_app(_assist_cfg(assist_radius_m=50.0))
    _seed_anchor(app, 1.0, 0.0, 0.0)
    assert app._assist_manual  # ghost exists while assist is active

    # Operator switches back to replace mode – the next pin call drops the ghost
    # so a later re-engage re-seeds from the live marker, not a stale anchor.
    app._config.detection.pin_mode = "replace"
    _run_assist(app, _StubDetector(_det(0.5, 1)), DetectionPinState(), monkeypatch)
    assert app._assist_manual == {}


def test_assist_prunes_stale_ghost_when_pinned_id_changes(monkeypatch) -> None:
    from openfollow.psn import Marker

    app = _assist_app(_assist_cfg(assist_radius_m=50.0))
    app._assist_manual[5] = Marker(5, "")  # leftover from a previously-pinned id
    _seed_anchor(app, 0.0, 0.0, 0.0)  # current pinned id is _PID
    state = DetectionPinState()

    _run_assist(app, _AssistDetector([_det(0.5, 1)]), state, monkeypatch)

    # The stale ghost is dropped; only the current anchor survives.
    assert set(app._assist_manual) == {_PID}


def test_assist_pinned_marker_id_resolves_only_in_assist_mode() -> None:
    app = _assist_app(_assist_cfg(assist_radius_m=50.0))
    assert assist_pinned_marker_id(app) == _PID
    # Detection off, replace mode, pin off, or no selection all yield None
    # (no input redirect, no ghost, no glide) – assist stays fully dormant.
    app._config.detection.enabled = False
    assert assist_pinned_marker_id(app) is None
    app._config.detection.enabled = True
    app._config.detection.pin_mode = "replace"
    assert assist_pinned_marker_id(app) is None
    app._config.detection.pin_mode = "assist"
    app._config.detection.pin_marker = False
    assert assist_pinned_marker_id(app) is None
    app._config.detection.pin_marker = True
    app._selected_id = None
    assert assist_pinned_marker_id(app) is None


def test_replace_mode_does_not_read_detections(monkeypatch) -> None:
    """Default ``pin_mode=replace`` must not touch the assist path – proven by
    a detector whose ``detections`` property raises if read."""
    cfg = DetectionConfig(enabled=True, pin_marker=True, pin_mode="replace", smoothing=1.0)
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
    cam, pt = _buffers()
    state = DetectionPinState()
    _patch_linear_unproject(monkeypatch)

    apply_detection_pin(
        app,
        person_detector=detector,
        unproject_cam_buffer=cam,
        screen_point_buffer=pt,
        pin_state=state,
    )
    # Replace path drove the marker from tracked_detection without error.
    assert marker.pos[0] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Attached-box highlight: the pin records which detection track drives which
# marker so the overlay can paint that one box in the marker's colour.
# ---------------------------------------------------------------------------


def test_assist_records_attached_track_and_marker(monkeypatch) -> None:
    app = _assist_app(_assist_cfg())
    _seed_anchor(app, 5.0, 0.0, 0.0)  # anchor by the in-range person at world (5,0)
    state = DetectionPinState()

    _run_assist(app, _AssistDetector([_det(0.5, 7), _det(0.9, 2)]), state, monkeypatch)

    assert state.locked_track_id == 7
    assert state.attached_track_id == 7
    assert state.attached_marker_id == _PID


def test_assist_clears_attached_track_when_gliding_home(monkeypatch) -> None:
    app = _assist_app(_assist_cfg(assist_radius_m=1.0))
    _seed_anchor(app, 0.0, 0.0, 0.0)  # nobody within 1m of the anchor
    state = DetectionPinState()

    _run_assist(app, _AssistDetector([_det(0.9, 7)]), state, monkeypatch)  # world (9,0)

    assert state.locked_track_id is None
    assert state.attached_track_id is None
    assert state.attached_marker_id == _PID


def test_replace_records_attached_track_and_marker(monkeypatch) -> None:
    cfg = DetectionConfig(enabled=True, pin_marker=True, pin_mode="replace", smoothing=1.0)
    app = _make_app(detection_cfg=cfg, resolution=(1000, 1000))
    state = DetectionPinState()
    _patch_linear_unproject(monkeypatch)
    cam, pt = _buffers()

    apply_detection_pin(
        app,
        person_detector=_StubDetector(_det(0.5, 4)),
        unproject_cam_buffer=cam,
        screen_point_buffer=pt,
        pin_state=state,
    )

    assert state.attached_track_id == 4
    assert state.attached_marker_id == 0  # _StubMarker default id


def test_pin_clears_attached_track_when_pin_marker_off(monkeypatch) -> None:
    cfg = DetectionConfig(enabled=True, pin_marker=True, pin_mode="replace", smoothing=1.0)
    app = _make_app(detection_cfg=cfg, resolution=(1000, 1000))
    state = DetectionPinState()
    _patch_linear_unproject(monkeypatch)
    cam, pt = _buffers()
    _run = lambda: apply_detection_pin(  # noqa: E731
        app,
        person_detector=_StubDetector(_det(0.5, 4)),
        unproject_cam_buffer=cam,
        screen_point_buffer=pt,
        pin_state=state,
    )
    _run()
    assert state.attached_track_id == 4

    # Operator disables Pin Marker – the attachment clears so no box stays lit.
    app._config.detection.pin_marker = False
    _run()
    assert state.attached_track_id is None
    assert state.attached_marker_id is None


# ---------------------------------------------------------------------------
# Coverage: assist id resolution + glide edge paths
# ---------------------------------------------------------------------------


def test_assist_pinned_marker_id_explicit_controlled_id() -> None:
    app = _make_app(detection_cfg=_assist_cfg(pin_marker_id=5), selected_id=1, controlled_ids=[5])
    assert assist_pinned_marker_id(app) == 5


def test_assist_pinned_marker_id_explicit_uncontrolled_id_is_none() -> None:
    app = _make_app(detection_cfg=_assist_cfg(pin_marker_id=7), selected_id=1, controlled_ids=[5])
    assert assist_pinned_marker_id(app) is None


def test_get_or_create_manual_marker_unseeded_when_registered_missing() -> None:
    app = _make_app(detection_cfg=_assist_cfg())
    app._server = SimpleNamespace(get_marker=lambda _id: None)
    ghost = get_or_create_manual_marker(app, _PID)
    # No registered marker to seed from -> ghost stays at the origin default.
    assert ghost.pos == (0.0, 0.0, 0.0)
    assert app._assist_manual[_PID] is ghost


def test_assist_noop_when_no_selection(monkeypatch) -> None:
    from openfollow.psn import Marker

    app = _make_app(detection_cfg=_assist_cfg(), selected_id=None, controlled_ids=[_PID])
    app._assist_manual[_PID] = Marker(_PID, "")
    state = DetectionPinState()
    state.ai_smooth_x = 1.0  # would be cleared by reset()
    _run_assist(app, _AssistDetector([_det(0.5, 1)]), state, monkeypatch)
    # No assist id resolves -> ghost pruned + state reset, nothing driven.
    assert app._assist_manual == {}
    assert state.ai_smooth_x is None


def test_assist_noop_when_out_marker_missing(monkeypatch) -> None:
    app = _assist_app(_assist_cfg())
    app._server = SimpleNamespace(get_marker=lambda _id: None)
    state = DetectionPinState()
    _run_assist(app, _AssistDetector([_det(0.5, 1)]), state, monkeypatch)
    # Output marker can't be resolved -> bail without touching the glide.
    assert state.ai_smooth_x is None


def test_assist_noop_when_resolution_zero(monkeypatch) -> None:
    app = _make_app(detection_cfg=_assist_cfg(), selected_id=_PID, controlled_ids=[_PID], resolution=(0, 0))
    _seed_anchor(app, 1.0, 0.0, 0.0)
    state = DetectionPinState()
    _run_assist(app, _AssistDetector([_det(0.5, 1)]), state, monkeypatch)
    assert state.ai_smooth_x is None


def test_assist_skips_non_finite_detection_in_nearest_search(monkeypatch) -> None:
    import openfollow.runtime.services_detection_pin as module

    app = _assist_app(_assist_cfg(assist_radius_m=50.0))
    out = app._server.get_marker(_PID)
    _seed_anchor(app, 0.0, 0.0, 0.0)
    state = DetectionPinState()

    cam, pt = _buffers()
    monkeypatch.setattr(module, "unproject_to_plane", lambda *_a, **_k: np.array([[np.nan, np.nan, 0.0]]))
    apply_detection_pin(
        app,
        person_detector=_AssistDetector([_det(0.5, 1)]),
        unproject_cam_buffer=cam,
        screen_point_buffer=pt,
        pin_state=state,
    )
    # The only detection unprojects to a non-finite point -> dropped; the
    # output glides home to the anchor (no lock).
    assert state.locked_track_id is None
    assert out.pos[0] == pytest.approx(0.0)


def test_assist_locked_track_absent_from_detections_falls_through(monkeypatch) -> None:
    app = _assist_app(_assist_cfg(assist_radius_m=50.0))
    _seed_anchor(app, 5.0, 0.0, 0.0)
    state = DetectionPinState()
    state.locked_track_id = 99  # a track that isn't in this frame's detections

    _run_assist(app, _AssistDetector([_det(0.5, 1)]), state, monkeypatch)

    # The stale lock isn't found, so the nearest in-range detection is chosen.
    assert state.locked_track_id == 1


def _capture_unproject_screen(monkeypatch) -> dict:  # noqa: ANN001
    """Patch unproject_to_plane to record the screen point it receives."""
    import openfollow.runtime.services_detection_pin as module

    recorded: dict[str, np.ndarray] = {}

    def _spy(_params, screen_pt, _w, _h, _plane_z):  # noqa: ANN001, ANN202
        recorded["screen"] = np.array(screen_pt, dtype=float).copy()
        return np.array([[1.0, 2.0, 0.0]], dtype=np.float64)

    monkeypatch.setattr(module, "unproject_to_plane", _spy)
    return recorded


def test_replace_pin_passes_raw_screen_when_lens_zero(monkeypatch) -> None:
    cfg = DetectionConfig(enabled=True, pin_marker=True, pin_mode="replace", smoothing=1.0)
    app = _make_app(detection_cfg=cfg)  # lens_k1 == lens_k2 == 0
    detector = _StubDetector(_StubDetection(0.6, 0.6, 0.8, 0.8))
    cam, pt = _buffers()
    recorded = _capture_unproject_screen(monkeypatch)

    apply_detection_pin(
        app, person_detector=detector, unproject_cam_buffer=cam, screen_point_buffer=pt, pin_state=DetectionPinState()
    )

    # Pin point reaches unproject unchanged: centre_x = 0.7*1920, top = 0.6*1080.
    assert recorded["screen"][0, 0] == pytest.approx(0.7 * 1920)
    assert recorded["screen"][0, 1] == pytest.approx(0.6 * 1080)


def test_replace_pin_undistorts_screen_when_lens_set(monkeypatch) -> None:
    cfg = DetectionConfig(enabled=True, pin_marker=True, pin_mode="replace", smoothing=1.0)
    app = _make_app(detection_cfg=cfg)
    app._config.camera.lens_k1 = -0.2  # barrel
    detector = _StubDetector(_StubDetection(0.6, 0.6, 0.8, 0.8))
    cam, pt = _buffers()
    recorded = _capture_unproject_screen(monkeypatch)

    apply_detection_pin(
        app, person_detector=detector, unproject_cam_buffer=cam, screen_point_buffer=pt, pin_state=DetectionPinState()
    )

    cx, cy = 960.0, 540.0  # 1920x1080 centre
    raw = np.hypot(0.7 * 1920 - cx, 0.6 * 1080 - cy)
    undist = np.hypot(recorded["screen"][0, 0] - cx, recorded["screen"][0, 1] - cy)
    # Undistorting a barrel-distorted detection pin pushes it outward.
    assert undist > raw
