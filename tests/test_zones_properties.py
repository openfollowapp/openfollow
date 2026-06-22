# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Property-based tests for zone geometry + occupancy.

Point-in-polygon is the kernel of the trigger engine; these tests assert its
geometric invariants (degenerate input, bounding box, rigid-motion invariance)
and the containment that the hysteresis deadband rests on (the shrunken polygon
is inside the original for a convex zone), then the two direction-agnostic
occupancy guarantees that fall out of it: an entity solidly inside is always
detected, and one clearly outside is never a false occupant – regardless of its
prior state.

Conventions mirror ``test_zone_geometry.py`` / ``test_zone_engine.py``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from openfollow.zones.engine import ZoneEngine, _evaluate_zone
from openfollow.zones.geometry import point_in_polygon, shrink_polygon

pytestmark = pytest.mark.unit

_EID = ("marker", 1)


def _finite(lo: float, hi: float) -> st.SearchStrategy[float]:
    return st.floats(min_value=lo, max_value=hi, allow_nan=False, allow_infinity=False)


@st.composite
def _convex_polygon(draw: st.DrawFn) -> tuple[list[tuple[float, float]], float, float]:
    """A convex polygon inscribed in a circle (constant radius ⇒ convex).

    Vertices sit at evenly-spaced angles with bounded jitter, so they stay
    strictly ordered and well-separated (no coincident/spike vertices). Jitter
    is capped at ±0.2·gap so every arc (including the wrap-around) is at most
    1.4·(2π/n) < π for all n ≥ 3 – which keeps the circle centre inside.
    """
    n = draw(st.integers(min_value=3, max_value=8))
    cx = draw(_finite(-15.0, 15.0))
    cy = draw(_finite(-15.0, 15.0))
    radius = draw(_finite(8.0, 20.0))
    gap = 2.0 * math.pi / n
    verts = []
    for i in range(n):
        angle = i * gap + draw(_finite(-0.2 * gap, 0.2 * gap))
        verts.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return verts, cx, cy


def _min_edge_distance(px: float, py: float, verts: list[tuple[float, float]]) -> float:
    """Smallest distance from ``(px, py)`` to any polygon edge."""
    best = math.inf
    n = len(verts)
    for i in range(n):
        ax, ay = verts[i]
        bx, by = verts[(i + 1) % n]
        dx, dy = bx - ax, by - ay
        seg_len_sq = dx * dx + dy * dy
        if seg_len_sq == 0.0:
            t = 0.0
        else:
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))
        cxp, cyp = ax + t * dx, ay + t * dy
        best = min(best, math.hypot(px - cxp, py - cyp))
    return best


# --- point_in_polygon --------------------------------------------------------


@given(verts=st.lists(st.tuples(_finite(-50, 50), _finite(-50, 50)), max_size=2))
def test_point_in_polygon_degenerate_is_always_false(verts: list[tuple[float, float]]) -> None:
    """Fewer than 3 vertices can't bound an area → never inside."""
    assert point_in_polygon(0.0, 0.0, verts) is False


@given(poly=_convex_polygon(), px=_finite(-60, 60), py=_finite(-60, 60))
def test_point_outside_bounding_box_is_outside(
    poly: tuple[list[tuple[float, float]], float, float],
    px: float,
    py: float,
) -> None:
    verts, _, _ = poly
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    assume(px < min(xs) or px > max(xs) or py < min(ys) or py > max(ys))
    assert point_in_polygon(px, py, verts) is False


@given(poly=_convex_polygon())
def test_convex_polygon_contains_its_centre(
    poly: tuple[list[tuple[float, float]], float, float],
) -> None:
    verts, cx, cy = poly
    assert point_in_polygon(cx, cy, verts) is True


@given(
    poly=_convex_polygon(),
    px=_finite(-40, 40),
    py=_finite(-40, 40),
    dx=_finite(-50, 50),
    dy=_finite(-50, 50),
)
def test_membership_is_translation_invariant(
    poly: tuple[list[tuple[float, float]], float, float],
    px: float,
    py: float,
    dx: float,
    dy: float,
) -> None:
    verts, _, _ = poly
    # Skip points within rounding distance of an edge – the docstring does not
    # guarantee boundary classification, and a translation could flip it.
    assume(_min_edge_distance(px, py, verts) > 1e-3)
    moved = [(x + dx, y + dy) for x, y in verts]
    assert point_in_polygon(px + dx, py + dy, moved) == point_in_polygon(px, py, verts)


@given(
    poly=_convex_polygon(),
    px=_finite(-40, 40),
    py=_finite(-40, 40),
    theta=_finite(0.0, 2.0 * math.pi),
)
def test_membership_is_rotation_invariant(
    poly: tuple[list[tuple[float, float]], float, float],
    px: float,
    py: float,
    theta: float,
) -> None:
    verts, cx, cy = poly
    assume(_min_edge_distance(px, py, verts) > 1e-3)
    cos_t, sin_t = math.cos(theta), math.sin(theta)

    def rot(x: float, y: float) -> tuple[float, float]:
        ox, oy = x - cx, y - cy
        return (cx + ox * cos_t - oy * sin_t, cy + ox * sin_t + oy * cos_t)

    rverts = [rot(x, y) for x, y in verts]
    rpx, rpy = rot(px, py)
    assert point_in_polygon(rpx, rpy, rverts) == point_in_polygon(px, py, verts)


# --- shrink_polygon ----------------------------------------------------------


@given(
    verts=st.lists(st.tuples(_finite(-50, 50), _finite(-50, 50)), max_size=8),
    amount=_finite(-5.0, 5.0),
)
def test_shrink_polygon_is_noop_when_disabled_or_degenerate(
    verts: list[tuple[float, float]],
    amount: float,
) -> None:
    result = shrink_polygon(verts, amount)
    if amount <= 0.0 or len(verts) < 3:
        assert result == [(float(x), float(y)) for x, y in verts]
    else:
        assert len(result) == len(verts)


@given(poly=_convex_polygon(), px=_finite(-40, 40), py=_finite(-40, 40), amount=_finite(0.2, 1.0))
def test_shrunken_polygon_is_contained_in_original(
    poly: tuple[list[tuple[float, float]], float, float],
    px: float,
    py: float,
    amount: float,
) -> None:
    """The hysteresis deadband rests on this: a point inside the inset polygon
    is inside the original (radius ≥ 8, amount ≤ 1 ⇒ the miter inset never
    collapses or inverts the convex zone)."""
    verts, _, _ = poly
    shrunken = shrink_polygon(verts, amount)
    if point_in_polygon(px, py, shrunken):
        assert point_in_polygon(px, py, verts)


# --- _evaluate_zone (occupancy) ----------------------------------------------


@given(
    poly=_convex_polygon(),
    px=_finite(-40, 40),
    py=_finite(-40, 40),
    amount=_finite(0.2, 1.0),
    was_occupant=st.booleans(),
)
def test_entity_solidly_inside_is_always_detected(
    poly: tuple[list[tuple[float, float]], float, float],
    px: float,
    py: float,
    amount: float,
    was_occupant: bool,
) -> None:
    """Inside the shrunken polygon ⇒ counted, whether or not it was an occupant
    (a non-occupant clears the larger original; an occupant clears the inset)."""
    verts, _, _ = poly
    shrunken = shrink_polygon(verts, amount)
    assume(point_in_polygon(px, py, shrunken))
    prev = {_EID} if was_occupant else set()
    assert _EID in _evaluate_zone([(_EID, px, py)], verts, shrunken, prev)


@given(
    poly=_convex_polygon(),
    px=_finite(-40, 40),
    py=_finite(-40, 40),
    amount=_finite(0.2, 1.0),
    was_occupant=st.booleans(),
)
def test_entity_clearly_outside_is_never_detected(
    poly: tuple[list[tuple[float, float]], float, float],
    px: float,
    py: float,
    amount: float,
    was_occupant: bool,
) -> None:
    """Outside the original polygon ⇒ never counted, in any prior state
    (outside original ⇒ outside the inset too)."""
    verts, _, _ = poly
    shrunken = shrink_polygon(verts, amount)
    assume(not point_in_polygon(px, py, verts))
    prev = {_EID} if was_occupant else set()
    assert _EID not in _evaluate_zone([(_EID, px, py)], verts, shrunken, prev)


# --- stateful ZoneEngine (transitions across frames) -------------------------
#
# The properties above test the single-frame kernel; these drive the real
# ``ZoneEngine.update()`` over a sequence and assert the transition state
# machine (the OSC ``/first`` ... ``/final`` emissions). Minimal config doubles
# match the API surface the engine reads, mirroring ``test_zone_engine.py``.
# ``debounce_ms=0`` so every genuine transition emits.


@dataclass
class _ZoneCfg:
    name: str = ""
    vertices: list[list[float]] = field(default_factory=list)
    color: str = "#ff8000"
    trigger_source: str = "markers"
    triggered_by: list[int] = field(default_factory=list)
    osc_address_first_entry: str = "/first"
    osc_address_additional_entry: str = "/additional"
    osc_address_partial_exit: str = "/partial"
    osc_address_final_exit: str = "/final"
    destination_id: str = "d"
    enabled: bool = True


@dataclass
class _ZonesCfg:
    enabled: bool = True
    show_overlay: bool = True
    eval_fps: int = 10
    debounce_ms: int = 0
    hysteresis: float = 0.0
    zones: list[_ZoneCfg] = field(default_factory=list)


@dataclass
class _DestCfg:
    id: str = "d"
    host: str = "127.0.0.1"
    port: int = 53000
    protocol: str = "udp"
    framing: str = "slip"


@dataclass
class _DestsCfg:
    destinations: list[_DestCfg] = field(default_factory=lambda: [_DestCfg()])

    def get(self, destination_id: str) -> _DestCfg | None:
        for d in self.destinations:
            if d.id == destination_id:
                return d
        return None

    def by_id(self) -> dict[str, _DestCfg]:
        return {d.id: d for d in self.destinations}


class _RecordingOsc:
    def __init__(self) -> None:
        self.addresses: list[str] = []

    def send(
        self,
        address: str,
        args: tuple = (),
        *,
        host: str,
        port: int,
        protocol: str = "udp",
        framing: str = "slip",
    ) -> None:
        self.addresses.append(address)


def _engine(
    verts: list[tuple[float, float]],
    hysteresis: float,
) -> tuple[ZoneEngine, _RecordingOsc]:
    zone = _ZoneCfg(vertices=[[float(x), float(y)] for x, y in verts])
    osc = _RecordingOsc()
    cfg = _ZonesCfg(zones=[zone], hysteresis=hysteresis)
    return ZoneEngine(cfg, osc, _DestsCfg()), osc  # type: ignore[arg-type]


def _marker(x: float, y: float) -> tuple[tuple[str, int], float, float]:
    return (("marker", 1), x, y)


@given(poly=_convex_polygon(), hysteresis=_finite(0.2, 1.0), frames=st.integers(2, 6))
def test_stationary_inside_marker_fires_first_exactly_once(
    poly: tuple[list[tuple[float, float]], float, float],
    hysteresis: float,
    frames: int,
) -> None:
    """A marker held at the zone centre over many frames enters once and never
    re-triggers – occupancy is stable, so no repeated ``/first`` or any exit."""
    verts, cx, cy = poly
    # Centre is inside the inset too, so the occupant never drops out.
    assume(point_in_polygon(cx, cy, shrink_polygon(verts, hysteresis)))
    engine, osc = _engine(verts, hysteresis)
    for _ in range(frames):
        engine.update([_marker(cx, cy)], [])
    assert osc.addresses == ["/first"]


@given(poly=_convex_polygon(), hysteresis=_finite(0.2, 1.0), frames=st.integers(1, 6))
def test_stationary_outside_marker_never_fires(
    poly: tuple[list[tuple[float, float]], float, float],
    hysteresis: float,
    frames: int,
) -> None:
    """A marker far outside the zone never becomes an occupant – no emissions."""
    verts, cx, cy = poly
    engine, osc = _engine(verts, hysteresis)
    for _ in range(frames):
        engine.update([_marker(cx + 1000.0, cy)], [])  # radius <= 20 ⇒ clearly outside
    assert osc.addresses == []


@given(poly=_convex_polygon(), hysteresis=_finite(0.2, 1.0))
def test_marker_entering_then_leaving_fires_first_then_final(
    poly: tuple[list[tuple[float, float]], float, float],
    hysteresis: float,
) -> None:
    """Enter (centre) then leave (clearly outside) emits exactly ``/first`` then
    ``/final`` – the basic single-occupant transition cycle."""
    verts, cx, cy = poly
    assume(point_in_polygon(cx, cy, shrink_polygon(verts, hysteresis)))
    engine, osc = _engine(verts, hysteresis)
    engine.update([_marker(cx, cy)], [])
    engine.update([_marker(cx + 1000.0, cy)], [])
    assert osc.addresses == ["/first", "/final"]
