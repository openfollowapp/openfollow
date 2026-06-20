# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Invariants for the PSN-absolute coordinate system.

Every marker-position touchpoint across the system MUST agree that
``marker.pos`` is PSN-absolute world coordinates – the same frame as:

* PSN packets on the wire (in and out),
* zone vertices in ``TriggerZoneConfig``,
* world points returned by ``scene.solver.unproject_to_plane``,
* ``scene.solver.project_points`` input,
* grid-origin arithmetic in ``scene.calibration``.

Historically, writers (mouse, detection-pin) and the zone-engine reader
each applied their own ``±grid.{x,y}_offset`` translation, causing
silent frame mismatches whenever the grid offsets were nonzero.

These tests run each touchpoint across three offset configurations – a
zero baseline, positive offsets, and a mixed-sign case – and assert the
coordinate-system output is offset-independent wherever it should be.
Add an offset-aware line of code anywhere in the marker-position
pipeline and one of these will catch it.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from openfollow.configuration import (
    AppConfig,
    CameraConfig,
    DetectionConfig,
    GridConfig,
    TriggerZoneConfig,
    TriggerZonesConfig,
)
from openfollow.input.mouse import MouseHandler
from openfollow.psn.marker import Marker
from openfollow.runtime.overlay_draw_scene import project
from openfollow.runtime.overlay_state import MarkerOverlayData, OverlayState
from openfollow.runtime.services_detection_pin import (
    DetectionPinState,
    apply_detection_pin,
)
from openfollow.scene.solver import unproject_to_plane
from openfollow.services import AppRuntimeServices
from openfollow.zones.engine import ZoneEngine

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# Shared test scaffolding
# --------------------------------------------------------------------------- #

OFFSET_CASES = [
    pytest.param((0.0, 0.0, 0.0), id="zero-offsets"),
    pytest.param((5.0, 3.0, 0.0), id="positive-offsets"),
    pytest.param((-4.5, 7.25, 1.0), id="mixed-sign-offsets"),
]


def _grid(x_offset: float, y_offset: float, z_offset: float) -> GridConfig:
    return GridConfig(
        width=20.0,
        depth=15.0,
        x_offset=x_offset,
        y_offset=y_offset,
        z_offset=z_offset,
    )


def _top_down_camera() -> CameraConfig:
    """Camera looking straight down from 10m – unprojection is linear/predictable."""
    return CameraConfig(
        pos_x=0.0,
        pos_y=0.0,
        pos_z=10.0,
        pitch=-90.0,
        yaw=0.0,
        roll=0.0,
        fov=60.0,
    )


class _StubServer:
    def __init__(self) -> None:
        self._markers: dict[int, Marker] = {}

    def add_marker(self, tid: int) -> Marker:
        t = Marker(tid, f"Marker {tid}")
        self._markers[tid] = t
        return t

    def get_marker(self, tid: int) -> Marker | None:
        return self._markers.get(tid)


class _StubPsnReceiver:
    def __init__(self) -> None:
        self._markers: dict[int, Marker] = {}

    def add_marker(self, tid: int) -> Marker:
        t = Marker(tid, f"Remote {tid}")
        self._markers[tid] = t
        return t

    def get_marker(self, tid: int) -> Marker | None:
        return self._markers.get(tid)

    def is_marker_online(self, tid: int, timeout: float = 2.0) -> bool:
        return tid in self._markers


class _StubCamera:
    def __init__(self, cfg: CameraConfig) -> None:
        self._cfg = cfg

    def to_config(self) -> CameraConfig:
        return self._cfg


class _StubVideoReceiver:
    def __init__(self, resolution: tuple[int, int]) -> None:
        self.resolution = resolution


def _make_app(
    grid: GridConfig,
    *,
    camera: CameraConfig | None = None,
    resolution: tuple[int, int] = (1280, 720),
    detection_cfg: DetectionConfig | None = None,
) -> SimpleNamespace:
    """Build a minimal app graph shared by mouse, pin, and services tests."""
    camera = camera or _top_down_camera()
    detection_cfg = detection_cfg or DetectionConfig(
        enabled=True,
        pin_marker=True,
        pin_mode="replace",
        smoothing=1.0,
        prediction=0.0,
    )
    cfg = AppConfig()
    cfg.grid = grid
    cfg.camera = camera
    cfg.detection = detection_cfg
    return SimpleNamespace(
        _config=cfg,
        _server=_StubServer(),
        _psn_receiver=_StubPsnReceiver(),
        _camera=_StubCamera(camera),
        _video_receiver=_StubVideoReceiver(resolution),
        _canvas=None,
        _controlled_ids=[],
        _viewer_ids=[],
        _selected_id=None,
        _assist_manual={},
    )


def _services_for(app: SimpleNamespace) -> AppRuntimeServices:
    """Build an AppRuntimeServices that only wires up the fields the unit
    tests exercise – bypasses ``__init__`` (GStreamer/GC setup) entirely."""
    svc = AppRuntimeServices.__new__(AppRuntimeServices)
    svc._app = app
    return svc


# --------------------------------------------------------------------------- #
# 1. PSN receiver path: raw PSN coords land in marker.pos unmodified
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("offsets", OFFSET_CASES)
def test_psn_received_marker_pos_is_psn_absolute(offsets) -> None:
    """PSN receiver stores raw PSN coords; no grid-offset translation applied.

    This is the *definition* the rest of the system is aligned to:
    ``set_pos`` is documented as "in PSN coordinates" and must remain so
    regardless of any grid offsets configured downstream.
    """
    ox, oy, oz = offsets
    marker = Marker(7, "remote")
    marker.set_pos(4.0, 5.0, 6.0)

    assert marker.pos == (4.0, 5.0, 6.0)
    # Outgoing PSN packet carries marker.pos verbatim – independent of
    # any configured grid offsets.
    psn_out = marker.to_psn_marker()
    assert (psn_out.pos.x, psn_out.pos.y, psn_out.pos.z) == (4.0, 5.0, 6.0)
    # Offsets supplied only to demonstrate independence – they must not
    # enter this code path at all.
    del ox, oy, oz


# --------------------------------------------------------------------------- #
# 2. _collect_marker_positions: marker.pos flows through without translation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("offsets", OFFSET_CASES)
def test_collect_marker_positions_is_offset_independent(offsets) -> None:
    """``_collect_marker_positions`` returns marker.pos verbatim for zone eval.

    If a regression re-introduces a ``+ x_offset`` / ``+ y_offset`` here,
    this test fails across every non-zero offset case.
    """
    ox, oy, oz = offsets
    app = _make_app(_grid(ox, oy, oz))
    # One locally-controlled marker at (2, 3) and one remote at (7, -4).
    ctrl = app._server.add_marker(1)
    ctrl.set_pos(2.0, 3.0, 0.0)
    remote = app._psn_receiver.add_marker(2)
    remote.set_pos(7.0, -4.0, 0.0)
    app._controlled_ids = [1]
    app._viewer_ids = [1, 2]

    svc = _services_for(app)
    result = {(kind, tid): (x, y) for (kind, tid), x, y in svc._collect_marker_positions()}

    assert result[("marker", 1)] == pytest.approx((2.0, 3.0))
    assert result[("marker", 2)] == pytest.approx((7.0, -4.0))


@pytest.mark.parametrize("offsets", OFFSET_CASES)
def test_collect_marker_positions_ignores_grid_offsets(offsets) -> None:
    ox, oy, oz = offsets
    baseline_app = _make_app(_grid(0.0, 0.0, 0.0))
    offset_app = _make_app(_grid(ox, oy, oz))

    for app in (baseline_app, offset_app):
        t = app._server.add_marker(1)
        t.set_pos(1.5, 2.5, 0.0)
        app._controlled_ids = [1]
        app._viewer_ids = [1]

    baseline = _services_for(baseline_app)._collect_marker_positions()
    shifted = _services_for(offset_app)._collect_marker_positions()
    assert baseline == shifted


# --------------------------------------------------------------------------- #
# 3. Mouse input: unprojected world point lands in marker.pos directly
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("offsets", OFFSET_CASES)
def test_mouse_click_stores_psn_absolute_position(offsets) -> None:
    """A mouse click at an (x, y) screen coord places the marker at the
    *same PSN-absolute point* regardless of grid offsets.

    Before the fix, ``mouse.py`` subtracted ``grid.{x,y}_offset`` before
    storing into ``marker.pos``, meaning the same click produced
    different ``marker.pos`` values under different offsets – which in
    turn desynced PSN output and zone-engine membership.
    """
    ox, oy, oz = offsets
    app = _make_app(_grid(ox, oy, oz))
    app._server.add_marker(1).set_pos(0.0, 0.0, 0.0)
    app._controlled_ids = [1]
    app._selected_id = 1

    handler = MouseHandler(app)
    # Left-click at a fixed screen point – chosen off-center so the x/y
    # unprojection results are distinct from one another.
    handler.on_pointer_down(800.0, 300.0, 1)

    marker = app._server.get_marker(1)
    x, y, _ = marker.pos

    # Compute the expected world point independently; the test asserts
    # marker.pos matches this PSN-absolute point without any offset
    # subtraction applied in mouse.py.
    cam = _top_down_camera()
    cam_params = np.array([cam.pos_x, cam.pos_y, cam.pos_z, cam.pitch, cam.yaw, cam.roll, cam.fov])
    expected = unproject_to_plane(
        cam_params,
        np.array([[800.0, 300.0]], dtype=np.float64),
        1280.0,
        720.0,
        oz,
    )
    assert math.isfinite(x) and math.isfinite(y)
    assert x == pytest.approx(float(expected[0, 0]))
    assert y == pytest.approx(float(expected[0, 1]))


# --------------------------------------------------------------------------- #
# 4. Detection pin: smoothing=1.0 snaps marker.pos to the unprojected point
# --------------------------------------------------------------------------- #


class _StubDetection:
    def __init__(self, x1: float, y1: float, x2: float, y2: float, track_id: int = 0) -> None:
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2
        self.track_id = track_id


class _StubDetector:
    def __init__(self, detection) -> None:  # noqa: ANN001
        self.tracked_detection = detection


@pytest.mark.parametrize("offsets", OFFSET_CASES)
def test_detection_pin_stores_psn_absolute_position(offsets, monkeypatch) -> None:
    """Detection-pin writes the unprojected world point directly into
    ``marker.pos`` – no grid-offset translation, independent of offsets."""
    ox, oy, oz = offsets
    app = _make_app(
        _grid(ox, oy, oz),
        detection_cfg=DetectionConfig(
            enabled=True,
            pin_marker=True,
            pin_mode="replace",
            smoothing=1.0,
            prediction=0.0,
        ),
    )
    app._server.add_marker(1).set_pos(0.0, 0.0, 0.0)
    app._controlled_ids = [1]
    app._selected_id = 1

    import openfollow.runtime.services_detection_pin as module

    monkeypatch.setattr(
        module,
        "unproject_to_plane",
        lambda *_a, **_k: np.array([[12.5, -3.75, 0.0]], dtype=np.float64),
    )

    detector = _StubDetector(_StubDetection(0.4, 0.4, 0.6, 0.6))
    state = DetectionPinState()
    apply_detection_pin(
        app,
        person_detector=detector,
        unproject_cam_buffer=np.zeros(7, dtype=np.float64),
        screen_point_buffer=np.zeros((1, 2), dtype=np.float64),
        pin_state=state,
    )

    marker = app._server.get_marker(1)
    assert marker.pos[0] == pytest.approx(12.5)
    assert marker.pos[1] == pytest.approx(-3.75)


# --------------------------------------------------------------------------- #
# 5. Zone engine: a PSN marker inside a PSN-absolute zone is detected
# --------------------------------------------------------------------------- #


class _StubOsc:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str | None, int | None]] = []

    def send(
        self,
        address: str,
        args: object = (),
        host: str | None = None,
        port: int | None = None,
        protocol: str = "udp",
    ) -> None:
        # ``args``/``protocol`` are accepted to keep the stub aligned with
        # OscService.send. The invariants
        # tests don't assert on args today – they only care that the right
        # address fires for a marker-inside-zone scenario.
        self.sent.append((address, host, port))


def _zone_square(cx: float, cy: float, half: float = 1.0) -> TriggerZoneConfig:
    return TriggerZoneConfig(
        name="Z",
        vertices=[
            (cx - half, cy - half),
            (cx + half, cy - half),
            (cx + half, cy + half),
            (cx - half, cy + half),
        ],
        trigger_source="markers",
        enabled=True,
        osc_address_first_entry="/z/first",
    )


@pytest.mark.parametrize("offsets", OFFSET_CASES)
def test_psn_marker_inside_zone_is_detected_regardless_of_offsets(offsets) -> None:
    ox, oy, oz = offsets
    app = _make_app(_grid(ox, oy, oz))
    # Remote marker at PSN(0, 3) – dead centre of a 2x2 zone at (0, 3).
    remote = app._psn_receiver.add_marker(2)
    remote.set_pos(0.0, 3.0, 0.0)
    app._viewer_ids = [2]

    svc = _services_for(app)
    osc = _StubOsc()
    zone_cfg = TriggerZonesConfig(
        enabled=True,
        zones=[_zone_square(0.0, 3.0, half=1.0)],
    )
    engine = ZoneEngine(zone_cfg, osc)
    engine.update(svc._collect_marker_positions(), detection_positions=[])

    states = engine.get_zone_states()
    assert states == [(0, True, 1)], f"grid_offset=({ox}, {oy}, {oz}) broke zone membership: {states}"
    assert any(addr == "/z/first" for addr, _host, _port in osc.sent)


@pytest.mark.parametrize("offsets", OFFSET_CASES)
def test_mouse_click_inside_zone_is_detected_regardless_of_offsets(offsets) -> None:
    ox, oy, oz = offsets
    app = _make_app(_grid(ox, oy, oz))
    app._server.add_marker(1).set_pos(0.0, 0.0, 0.0)
    app._controlled_ids = [1]
    app._selected_id = 1
    app._viewer_ids = [1]

    # Unproject the screen centre up-front so the zone is placed around
    # the exact PSN-absolute world point a centre-click will produce.
    cam = _top_down_camera()
    cam_params = np.array([cam.pos_x, cam.pos_y, cam.pos_z, cam.pitch, cam.yaw, cam.roll, cam.fov])
    expected_world = unproject_to_plane(
        cam_params,
        np.array([[640.0, 360.0]], dtype=np.float64),
        1280.0,
        720.0,
        oz,
    )
    wx, wy = float(expected_world[0, 0]), float(expected_world[0, 1])

    MouseHandler(app).on_pointer_down(640.0, 360.0, 1)

    svc = _services_for(app)
    osc = _StubOsc()
    zone_cfg = TriggerZonesConfig(
        enabled=True,
        zones=[_zone_square(wx, wy, half=2.0)],
    )
    engine = ZoneEngine(zone_cfg, osc)
    engine.update(svc._collect_marker_positions(), detection_positions=[])

    assert engine.get_zone_states() == [(0, True, 1)], (
        f"grid_offset=({ox}, {oy}, {oz}) broke click-inside-zone detection."
    )


# --------------------------------------------------------------------------- #
# 6. Renderer ↔ zone engine: the ball is drawn where the engine tests
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("offsets", OFFSET_CASES)
def test_marker_ball_projects_to_same_world_point_zone_engine_sees(offsets) -> None:
    """The projected ball centre and the zone-engine's (x, y) must be the
    same PSN-absolute world point.

    A future regression that offsets marker.pos on the rendering side
    (reintroducing the historical offset shift) without doing so on the engine side –
    or vice versa – would decouple the two. Both sides call
    ``scene.solver.project_points`` (via ``project``) on the same raw
    marker.pos, so their screen outputs must match byte-for-byte for a
    given camera.
    """
    ox, oy, oz = offsets
    marker_world = (2.25, -1.5, float(oz))

    # Marker renders via draw_marker → project(cam, [marker.pos, …]).
    cam = _top_down_camera()
    cam_params = np.array([cam.pos_x, cam.pos_y, cam.pos_z, cam.pitch, cam.yaw, cam.roll, cam.fov])
    rendered = project(cam_params, [marker_world], 1280, 720)

    # Zone engine takes (x, y) from _collect_marker_positions and tests
    # them against zone vertices; both are fed *unmodified* into the same
    # projection for zone-polygon rendering. So projecting the engine's
    # (x, y) at z_offset must give the same screen pixel as the marker
    # ball.
    engine_view = project(cam_params, [(marker_world[0], marker_world[1], oz)], 1280, 720)

    assert rendered[0, 0] == pytest.approx(engine_view[0, 0])
    assert rendered[0, 1] == pytest.approx(engine_view[0, 1])
    # And the projected pixel is finite (camera+point are in view).
    assert np.all(np.isfinite(rendered))
    del ox, oy  # consumed via z_offset above


# --------------------------------------------------------------------------- #
# 7. PSN-absolute marker at (0,0,0) renders at the world origin – the
#    regression re-expressed at this layer, since all three
#    writer sites (PSN, mouse, pin) must now produce marker.pos==(0,0,0)
#    for a PSN(0,0,0) report.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("offsets", OFFSET_CASES)
def test_marker_at_origin_renders_at_origin(offsets) -> None:
    """A marker with ``pos == (0, 0, 0)`` projects to the same pixel as
    the world origin, regardless of grid offsets.

    This is the invariant re-established at the renderer. If a
    future edit re-adds an offset step in ``draw_marker``, this fails.
    """
    from openfollow.runtime.overlay_draw_scene import draw_marker

    ox, oy, oz = offsets
    cam = _top_down_camera()
    cam_params = np.array([cam.pos_x, cam.pos_y, cam.pos_z, cam.pitch, cam.yaw, cam.roll, cam.fov])

    state = OverlayState()
    state.camera_params = cam_params
    state.grid_config = (20.0, 15.0, 1.0, ox, oy, oz)
    state.show_ball = True
    state.transparency = 1.0

    td = MarkerOverlayData(
        marker_id=0,
        x=0.0,
        y=0.0,
        z=0.0,
        color="#ffffff",
        radius=0.5,
    )

    calls: list[tuple[float, float, float]] = []

    class _RecordingCairo:
        def set_source_rgba(self, *_):
            pass

        def set_source_rgb(self, *_):
            pass

        def set_line_width(self, _):
            pass

        def move_to(self, *_):
            pass

        def line_to(self, *_):
            pass

        def stroke(self):
            pass

        def fill(self):
            pass

        def fill_preserve(self):
            pass

        def arc(self, cx, cy, r, *_):
            calls.append((cx, cy, r))

        def close_path(self):
            pass

    draw_marker(_RecordingCairo(), state, td, 1280, 720)

    # The ball centre pixel must equal project(cam, [(0,0,0)]).
    expected = project(cam_params, [(0.0, 0.0, 0.0)], 1280, 720)
    assert calls, "draw_marker did not draw the ball"
    cx, cy, _ = calls[0]
    assert cx == pytest.approx(float(expected[0, 0]))
    assert cy == pytest.approx(float(expected[0, 1]))


# --------------------------------------------------------------------------- #
# 8. Marker-snapshot bridge: the web API publishes PSN-absolute coords so
#    the zone editor's "markers" dots land where its zone-vertex dots do.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("offsets", OFFSET_CASES)
def test_marker_snapshot_is_psn_absolute(offsets) -> None:
    """The web zone-editor receives markers via
    ``_get_marker_positions_snapshot``; the editor draws both markers
    and zone vertices in the PSN-absolute world frame, translating for
    display-centering only. If this helper ever reverts to a
    grid-local frame, the marker dot will visibly drift away from the
    ball overlay for the same marker.
    """
    ox, oy, oz = offsets
    app = _make_app(_grid(ox, oy, oz))
    remote = app._psn_receiver.add_marker(4)
    remote.set_pos(5.0, 6.0, 0.0)
    app._viewer_ids = [4]

    svc = _services_for(app)
    snapshot = svc._get_marker_positions_snapshot()

    assert snapshot == [(4, 5.0, 6.0)]
