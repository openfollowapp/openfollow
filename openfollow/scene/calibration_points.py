# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Setup Wizard's camera solve from marks: known points with a typed position, and triangulated points
measured by their distances to two other marks plus their height.

One fit adjusts the 7 camera values and every triangulated point's x, y against the pins, the distances and
an entered camera height, each residual in its own expected error. Every pin counts the same, and no mark is
left out: one that disagrees shows in its error and in :func:`plausibility`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import numpy.typing as npt

from openfollow.configuration import CameraConfig
from openfollow.scene.solver import (
    apply_overlay_distortion,
    camera_params,
    compute_homography,
    decompose_homography,
    project_points,
    unproject_to_plane,
)

PIXEL_SIGMA = 1.0  # px: a careful pin on a mark, native snapshot pixels
DISTANCE_SIGMA = 0.04  # m: a hand-held laser or tape on a mark
HEIGHT_SIGMA = 0.01  # m: an entered camera height, measured to the lens
# Steeper than this, flat marks cannot tell the camera's height from its field of view.
STEEP_PITCH = -82.5
FLAG_PX = 5.0  # a pin further than this from where the camera sees its mark is named
FLAG_DISTANCE_M = 3.0 * DISTANCE_SIGMA
SEED_FOVS = (40.0, 90.0)  # tried beside the saved FOV from a camera position
# Looking at 0/0/0 steeper than this, a camera position 2 m off turns the bearing 20° or more from 10 m up, and
# straight down there is none: the seed at the saved FOV is tried a quarter turn apart as well.
STEEP_SEED_PITCH = -60.0
STEEP_TURNS = (0.0, 90.0, 180.0, 270.0)
# Without a camera position, and without four level marks seeding a camera less steep than STEEP_PITCH, the camera
# is also tried above the known marks looking almost straight down, where their homography decomposes to noise.
NADIR_PITCH = -89.0
# Every seed and mirror side gets this many iterations; only the best goes on to the full fit.
SCOUT_ITERATIONS = 25
FIT_ITERATIONS = 300
# Two measured circles may miss each other by this much and still be one point: the noise of two distances.
MEET_TOLERANCE = 3.0 * math.sqrt(2.0) * DISTANCE_SIGMA
FLOOR_GROUP_M = 0.05  # m: marks whose heights agree this closely suggest one floor
# Four pinned known marks within this of one height, every triangle of them at least this large, seed the solve.
_LEVEL_M = 0.01
_MIN_TRIANGLE_M2 = 0.05
# Over the picture, within reach of the marks, the fit may put the floor at most this many times as far, or as
# near, as the homography of four level marks alone.
FLOOR_REACH = 2.0
_FLOOR_PROBES = np.array([[x, y] for y in np.linspace(0.0, 1.0, 7) for x in np.linspace(0.0, 1.0, 9)])

REASON_HEIGHT = "A distance is shorter than the height difference between the points."
REASON_SAME_PLACE = "Its two points are at the same place."
REASON_DIFFER = "The difference between the two distances is larger than the distance between its two points."
REASON_SHORT = "The two distances together are shorter than the distance between its two points."
REASON_UNPLACED_FROM = "It is measured from a point that cannot be placed."
NO_FIT = "No camera fits these points. Check the pins and the measurements."
NO_FIT_AT_HEIGHT = "No camera fits at this camera height. Check the camera height and the measurements."

Kind = Literal["known", "triangulated"]


class SolveSuperseded(Exception):
    """A newer solve was asked for while this one ran: its answer would be dropped."""


@dataclass(frozen=True)
class Mark:
    """One mark as the solve reads it. ``pin`` is on the pinhole (undistorted) snapshot, None when not pinned."""

    id: int
    kind: Kind
    z: float
    position: tuple[float, float] | None = None  # known: x, y
    from_a: int = 0
    from_b: int = 0
    distance_a: float = 0.0
    distance_b: float = 0.0
    pin: tuple[float, float] | None = None


@dataclass(frozen=True)
class MarkFit:
    """What the solve made of one mark. ``seen`` is where the camera puts it on the pinhole snapshot."""

    id: int
    position: tuple[float, float, float] | None
    reason: str = ""
    seen: tuple[float, float] | None = None
    distance_error: float | None = None  # m, the larger of a triangulated point's two


@dataclass(frozen=True)
class MarkSolve:
    camera: CameraConfig | None
    marks: tuple[MarkFit, ...]
    error: str = ""
    floor_off: bool = False  # :func:`_floor_holds` fails against the level marks' camera, neither one steep
    steep: bool = False  # pitch below STEEP_PITCH, where flat marks cannot tell the height from the FOV


def trilaterate(
    a: Sequence[float] | npt.NDArray[np.float64],
    b: Sequence[float] | npt.NDArray[np.float64],
    distance_a: float,
    distance_b: float,
    z: float,
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]] | str:
    """The two places at height ``z`` that lie at the measured distances from ``a`` and ``b``, mirror
    images across the line between them; or the reason there are none.

    Circles that miss each other by no more than :data:`MEET_TOLERANCE` meet at their closest approach.
    """
    ra2 = distance_a**2 - (z - float(a[2])) ** 2
    rb2 = distance_b**2 - (z - float(b[2])) ** 2
    if ra2 < 0.0 or rb2 < 0.0:
        return REASON_HEIGHT
    ra, rb = math.sqrt(ra2), math.sqrt(rb2)
    pa = np.array(a[:2], dtype=np.float64)
    pb = np.array(b[:2], dtype=np.float64)
    d = float(np.linalg.norm(pb - pa))
    if d < 0.05:
        return REASON_SAME_PLACE
    if abs(ra - rb) > d + MEET_TOLERANCE:
        return REASON_DIFFER
    if ra + rb < d - MEET_TOLERANCE:
        return REASON_SHORT
    if ra + rb < d:  # apart: midway between the facing edges
        along, across = (ra + d - rb) / 2.0, 0.0
    elif ra - rb > d:  # b inside a: midway between the far edges
        along, across = (ra + d + rb) / 2.0, 0.0
    elif rb - ra > d:  # a inside b
        along, across = (d - rb - ra) / 2.0, 0.0
    else:
        along = (ra**2 - rb**2 + d**2) / (2.0 * d)
        across = math.sqrt(max(ra**2 - along**2, 0.0))
    ex = (pb - pa) / d
    ey = np.array([-ex[1], ex[0]])
    first = pa + along * ex + across * ey
    second = pa + along * ex - across * ey
    return np.array([first[0], first[1], z]), np.array([second[0], second[1], z])


def look_at(position: Sequence[float], target: Sequence[float]) -> tuple[float, float]:
    """Pitch and yaw in degrees of a camera at ``position`` looking at ``target``, roll 0."""
    dx, dy, dz = (float(target[i]) - float(position[i]) for i in range(3))
    return math.degrees(math.atan2(dz, math.hypot(dx, dy))), math.degrees(math.atan2(dx, dy))


def _wrap_degrees(angle: float) -> float:
    """``angle`` in (-180, 180]: the fit moves freely across +/-180."""
    return 180.0 - (180.0 - angle) % 360.0


def _upright_angles(pitch: float, yaw: float, roll: float) -> tuple[float, float, float]:
    """The same view with pitch in [-90, 90] and yaw, roll in (-180, 180]: a pitch past 90 is the view turned
    half round in yaw and roll."""
    pitch = _wrap_degrees(pitch)
    if abs(pitch) > 90.0:
        pitch, yaw, roll = math.copysign(180.0, pitch) - pitch, yaw + 180.0, roll + 180.0
    return pitch, _wrap_degrees(yaw), _wrap_degrees(roll)


def _order(marks: Sequence[Mark]) -> list[Mark]:
    """Triangulated marks after the marks they are measured from; the caller has checked there is no loop."""
    by_id = {m.id: m for m in marks}
    done: dict[int, bool] = {}
    order: list[Mark] = []

    def visit(mark: Mark) -> None:
        if mark.id in done:
            return
        done[mark.id] = True
        for parent in (mark.from_a, mark.from_b) if mark.kind == "triangulated" else ():
            if parent in by_id:
                visit(by_id[parent])
        order.append(mark)

    for mark in marks:
        visit(mark)
    return order


def _level_quad(known: Sequence[Mark]) -> list[Mark] | None:
    """Four pinned known marks at one height, no three in a line: they seed the solve through a homography."""

    def area(p: Mark, q: Mark, r: Mark) -> float:
        assert p.position and q.position and r.position
        (ax, ay), (bx, by), (cx, cy) = p.position, q.position, r.position
        return abs((bx - ax) * (cy - ay) - (by - ay) * (cx - ax)) / 2.0

    n = len(known)
    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                for m in range(k + 1, n):
                    quad = [known[i], known[j], known[k], known[m]]
                    if any(abs(q.z - quad[0].z) >= _LEVEL_M for q in quad):
                        continue
                    triangles = ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3))
                    if all(area(quad[a], quad[b], quad[c]) > _MIN_TRIANGLE_M2 for a, b, c in triangles):
                        return quad
    return None


def _level_camera(
    marks: Sequence[Mark], canvas_w: float, canvas_h: float
) -> tuple[npt.NDArray[np.float64], list[Mark]] | None:
    """The camera of four pinned known marks at one height alone, through their homography, and those marks."""
    quad = _level_quad([m for m in marks if m.kind == "known" and m.pin is not None])
    if quad is None:
        return None
    plane = np.array([q.position for q in quad if q.position], dtype=np.float64)
    screen = np.array([q.pin for q in quad if q.pin], dtype=np.float64)
    seeded = decompose_homography(compute_homography(plane, screen), canvas_w, canvas_h, quad[0].z)
    return None if seeded is None else (camera_params(seeded), quad)


def _above_marks(
    marks: Sequence[Mark], canvas_w: float, saved_fov: float, height: float | None
) -> tuple[float, float, float] | None:
    """Over the pinned known marks' centre: at ``height``, else as high as their spread in the pins says at the
    saved FOV looking straight down. None without two pinned known marks apart on the snapshot."""
    pinned = [m for m in marks if m.kind == "known" and m.pin is not None and m.position is not None]
    world = np.array([[*m.position, m.z] for m in pinned if m.position], dtype=np.float64).reshape(-1, 3)
    pins = np.array([m.pin for m in pinned], dtype=np.float64).reshape(-1, 2)
    i, j = np.triu_indices(len(pinned), 1)
    apart_px = np.linalg.norm(pins[i] - pins[j], axis=1)
    if not (apart_px > 1.0).any():
        return None
    if height is None:
        focal_px = canvas_w / 2.0 / math.tan(math.radians(saved_fov) / 2.0)
        apart_m = np.linalg.norm(world[i, :2] - world[j, :2], axis=1)
        height = float(world[:, 2].mean() + focal_px * apart_m.sum() / apart_px.sum())
    return float(world[:, 0].mean()), float(world[:, 1].mean()), height


def _seeds(
    hint: Sequence[float] | None,
    saved_fov: float,
    level: npt.NDArray[np.float64] | None,
    height: float | None = None,
    above: tuple[float, float, float] | None = None,
) -> list[npt.NDArray[np.float64]]:
    """Every seed at ``height`` when one is entered; ``above`` is :func:`_above_marks`."""
    seeds = []
    if hint is not None:
        at = (hint[0], hint[1], hint[2] if height is None else height)
        pitch, yaw = look_at(at, (0.0, 0.0, 0.0))
        turns = STEEP_TURNS if pitch < STEEP_SEED_PITCH else (0.0,)
        for fov in (saved_fov, *SEED_FOVS):
            for turn in turns if fov == saved_fov else (0.0,):
                seeds.append(np.array([*at, pitch, yaw + turn, 0.0, fov], dtype=np.float64))
    if level is not None:
        seeds.append(level if height is None else np.concatenate([level[:2], [height], level[3:]]))
    if above is not None and hint is None and (level is None or float(level[3]) < STEEP_PITCH):
        seeds += [np.array([*above, NADIR_PITCH, turn, 0.0, saved_fov], dtype=np.float64) for turn in STEEP_TURNS]
    return seeds


def _floor_holds(
    reference: npt.NDArray[np.float64],
    camera: npt.NDArray[np.float64],
    plane_z: float,
    places: npt.NDArray[np.float64],
    canvas_w: float,
    canvas_h: float,
) -> bool:
    """Whether ``camera`` puts the floor where ``reference`` does, within FLOOR_REACH.

    Only the picture the reference puts on the floor within reach of the marks is asked about, so a horizon in
    the picture, or floor far beyond the marks, decides nothing.
    """
    probes = _FLOOR_PROBES * np.array([canvas_w, canvas_h])
    ref = unproject_to_plane(reference, probes, canvas_w, canvas_h, plane_z)
    got = unproject_to_plane(camera, probes, canvas_w, canvas_h, plane_z)
    ref_far = np.hypot(ref[:, 0] - reference[0], ref[:, 1] - reference[1])
    got_far = np.hypot(got[:, 0] - camera[0], got[:, 1] - camera[1])
    reach = FLOOR_REACH * float(np.hypot(places[:, 0] - reference[0], places[:, 1] - reference[1]).max())
    asked = np.isfinite(ref_far) & (ref_far <= reach)
    ratio = got_far[asked] / ref_far[asked]  # NaN where the camera does not put that pixel on the floor
    return bool(np.all((ratio <= FLOOR_REACH) & (ratio >= 1.0 / FLOOR_REACH)))


class _Problem:
    """The joint adjustment: camera (7) and each live triangulated mark's x, y; residuals in their sigma."""

    def __init__(
        self,
        known: dict[int, npt.NDArray[np.float64]],
        live: Sequence[Mark],
        pinned: Sequence[Mark],
        canvas: tuple[float, float],
        height: float | None = None,
    ) -> None:
        self.known = known
        self.live = list(live)
        self.pinned = list(pinned)
        self.canvas = canvas
        self.height = height
        self.pins = np.array([m.pin for m in self.pinned], dtype=np.float64).reshape(len(self.pinned), 2)

    def places(self, x: npt.NDArray[np.float64]) -> dict[int, npt.NDArray[np.float64]]:
        placed = dict(self.known)
        for i, mark in enumerate(self.live):
            placed[mark.id] = np.array([x[7 + 2 * i], x[8 + 2 * i], mark.z])
        return placed

    def jacobian(self, x: npt.NDArray[np.float64], rows: int) -> npt.NDArray[np.float64] | None:
        """Camera columns by central differences, the height row's too; each mark's x, y in closed form beside one
        projection call. ``x`` is one :meth:`residuals` answered, with ``rows`` residuals."""
        jac = np.zeros((rows, x.size))
        for k in range(7):
            step = 1e-6 * (1.0 + abs(x[k]))
            up, down = x.copy(), x.copy()
            up[k] += step
            down[k] -= step
            r_up, r_down = self.residuals(up), self.residuals(down)
            if r_up is None or r_down is None:
                return None
            jac[:, k] = (r_up - r_down) / (2.0 * step)
        if not self.live:
            return jac
        placed = self.places(x)
        column = {m.id: 7 + 2 * i for i, m in enumerate(self.live)}
        steps, probes = [], []
        for mark in self.live:
            at = placed[mark.id]
            hx, hy = 1e-6 * (1.0 + abs(at[0])), 1e-6 * (1.0 + abs(at[1]))
            steps.append((hx, hy))
            probes += [at + (hx, 0, 0), at - (hx, 0, 0), at + (0, hy, 0), at - (0, hy, 0)]
        seen = project_points(x[:7], np.array(probes), *self.canvas)
        pin_row = {m.id: 2 * i for i, m in enumerate(self.pinned)}
        for i, mark in enumerate(self.live):
            if mark.id in pin_row:
                row, (hx, hy), d = pin_row[mark.id], steps[i], seen[4 * i : 4 * i + 4]
                jac[row : row + 2, column[mark.id]] = (d[0] - d[1]) / (2.0 * hx * PIXEL_SIGMA)
                jac[row : row + 2, column[mark.id] + 1] = (d[2] - d[3]) / (2.0 * hy * PIXEL_SIGMA)
        base = 2 * len(self.pinned)
        for i, mark in enumerate(self.live):
            for k, parent in enumerate((mark.from_a, mark.from_b)):
                diff = placed[mark.id] - placed[parent]
                length = float(np.linalg.norm(diff))
                if length == 0.0:
                    continue
                grad = diff[:2] / (length * DISTANCE_SIGMA)
                row = base + 2 * i + k
                jac[row, column[mark.id] : column[mark.id] + 2] += grad
                if parent in column:
                    jac[row, column[parent] : column[parent] + 2] -= grad
        return jac if bool(np.isfinite(jac).all()) else None

    def residuals(self, x: npt.NDArray[np.float64]) -> npt.NDArray[np.float64] | None:
        """Per pin (2), then per triangulated mark its two distances, then the entered height; None off the valid
        range."""
        placed = self.places(x)
        world = np.array([placed[m.id] for m in self.pinned])
        try:
            seen = project_points(x[:7], world, *self.canvas)
        except ValueError:
            return None
        pins = ((seen - self.pins) / PIXEL_SIGMA).ravel()
        lengths = [
            (float(np.linalg.norm(placed[m.id] - placed[parent])) - measured) / DISTANCE_SIGMA
            for m in self.live
            for parent, measured in ((m.from_a, m.distance_a), (m.from_b, m.distance_b))
        ]
        if self.height is not None:
            lengths.append((float(x[2]) - self.height) / HEIGHT_SIGMA)
        r = np.concatenate([pins, np.array(lengths, dtype=np.float64)])
        return r if bool(np.isfinite(r).all()) else None


def _fit(
    problem: _Problem, x0: npt.NDArray[np.float64], stale: Callable[[], bool] | None, iterations: int
) -> tuple[npt.NDArray[np.float64], float] | None:
    """Levenberg-Marquardt from ``x0``. None when the start or its first derivatives are off the valid range;
    once a step was taken, a later failed derivative ends the fit where it is.
    ``stale`` is asked before every iteration and raises :class:`SolveSuperseded` once it answers True."""
    x = x0.copy()
    r = problem.residuals(x)
    if r is None:
        return None
    damping = 1e-3
    for iteration in range(iterations):
        if stale is not None and stale():
            raise SolveSuperseded
        jac = problem.jacobian(x, r.size)
        if jac is None:
            return None if iteration == 0 else (x, float(r @ r))
        normal = jac.T @ jac
        gradient = jac.T @ r
        cost = float(r @ r)
        while damping < 1e10:
            # Positive definite for any damping above zero, so the solve cannot fail.
            delta = np.linalg.solve(normal + damping * np.diag(np.diag(normal) + 1e-12), -gradient)
            trial = problem.residuals(x + delta)
            if trial is not None and float(trial @ trial) < cost:
                break
            damping *= 4.0
        else:
            return x, cost
        x, r = x + delta, trial
        damping = max(damping / 3.0, 1e-12)
        if float(np.max(np.abs(delta) / (1.0 + np.abs(x)))) < 1e-12:
            break
    return x, float(r @ r)


def solve_marks(
    marks: Sequence[Mark],
    canvas_w: float,
    canvas_h: float,
    hint: Sequence[float] | None = None,
    saved_fov: float = 60.0,
    stale: Callable[[], bool] | None = None,
    height: float | None = None,
) -> MarkSolve:
    """The camera from the marks; ``hint`` is the camera position the operator estimated, or None; ``height``
    is the camera's exact height in m, or None: every seed starts at it, and the fit holds it.

    Every seed (the hint at the saved FOV, 40° and 90°, looking at 0/0/0, at the saved FOV also turned a
    quarter at a time when that look is steep; the homography of four pinned known marks at one height; without
    the hint and with no such homography or a steep one, above the known marks looking down at
    :data:`NADIR_PITCH`, a quarter turn apart) is fitted briefly twice: with each triangulated mark on the side
    its pin picks as the seed sees it, and with all of them on the other side. An upright camera (at or above the
    lowest mark, not looking up) beats one that is not, then the one that placed more marks, then the lower cost.
    The fitted camera of the winner places again what its seed could not, and only that one is fitted to the end;
    when that fit fails or ranks lower, the winner's brief fit answers.
    With ``height`` given, no fit, or a height not above the lowest pinned mark, answers :data:`NO_FIT_AT_HEIGHT`.
    ``stale`` is asked before every iteration; once it answers True the solve raises :class:`SolveSuperseded`.
    """
    canvas = (canvas_w, canvas_h)
    ordered = _order(marks)
    if height is not None and height <= min((m.z for m in ordered if m.pin is not None), default=-math.inf):
        return MarkSolve(None, tuple(MarkFit(m.id, None) for m in marks), NO_FIT_AT_HEIGHT)
    triangulated = [m for m in ordered if m.kind == "triangulated"]
    known = {m.id: np.array([m.position[0], m.position[1], m.z]) for m in ordered if m.position is not None}
    level = _level_camera(ordered, canvas_w, canvas_h)
    above = _above_marks(ordered, canvas_w, saved_fov, height)
    seeds = _seeds(hint, saved_fov, None if level is None else level[0], height, above)

    def place_marks(
        placed: dict[int, npt.NDArray[np.float64]], params: npt.NDArray[np.float64], flip: bool
    ) -> dict[int, str]:
        """Trilaterates into ``placed`` each triangulated mark not in it yet; answers the reasons of the rest."""
        failed: dict[int, str] = {}
        for mark in triangulated:
            if mark.id in placed:
                continue
            if mark.from_a not in placed or mark.from_b not in placed:
                failed[mark.id] = REASON_UNPLACED_FROM
                continue
            found = trilaterate(placed[mark.from_a], placed[mark.from_b], mark.distance_a, mark.distance_b, mark.z)
            if isinstance(found, str):
                failed[mark.id] = found
                continue
            seen = project_points(params, np.array(found), canvas_w, canvas_h)
            gaps = np.linalg.norm(seen - np.array(mark.pin, dtype=np.float64), axis=1)
            nearer = 1 if np.isfinite(gaps).any() and np.nanargmin(gaps) == 1 else 0
            placed[mark.id] = found[1 - nearer if flip else nearer]
        return failed

    def fitted(
        camera: npt.NDArray[np.float64],
        placed: dict[int, npt.NDArray[np.float64]],
        failed: dict[int, str],
        iterations: int,
    ) -> dict[str, Any] | None:
        live = [m for m in triangulated if m.id in placed]
        pinned = [m for m in ordered if m.pin is not None and m.id in placed]
        if len(pinned) < 4:
            return None
        problem = _Problem(known, live, pinned, canvas, height)
        result = _fit(problem, np.concatenate([camera[:7], *[placed[m.id][:2] for m in live]]), stale, iterations)
        if result is None or not 10.0 < float(result[0][6]) < 170.0:
            return None
        x, cost = result
        places = problem.places(x)
        floor = min(float(p[2]) for p in places.values())
        upright = bool(x[2] >= floor and _upright_angles(x[3], x[4], x[5])[0] <= 0.0)
        return {"x": x, "cost": cost, "upright": upright, "places": places, "failed": failed}

    def rank(c: dict[str, Any]) -> tuple[bool, int, float]:
        return c["upright"], -len(c["failed"]), -c["cost"]

    best: dict[str, Any] | None = None
    reasons: dict[int, str] = {}
    for seed in seeds:
        for flip in (False, True) if triangulated else (False,):
            placed = dict(known)
            failed = place_marks(placed, seed, flip)
            for mark_id, reason in failed.items():
                reasons.setdefault(mark_id, reason)
            scout = fitted(seed, placed, failed, SCOUT_ITERATIONS)
            if scout is not None and (best is None or rank(scout) > rank(best)):
                best = scout

    if best is not None:
        # Chained from the seed a child inherits its parent's error; from the fitted places it may meet.
        placed = dict(best["places"])
        refined = fitted(best["x"], placed, place_marks(placed, best["x"][:7], False), FIT_ITERATIONS)
        if refined is not None and rank(refined) >= rank(best):
            best = refined
    if best is None:
        error = NO_FIT if height is None else NO_FIT_AT_HEIGHT
        return MarkSolve(None, tuple(MarkFit(m.id, None, reasons.get(m.id, "")) for m in marks), error)
    x = best["x"]
    pitch, yaw, roll = _upright_angles(float(x[3]), float(x[4]), float(x[5]))
    camera = CameraConfig(
        pos_x=float(x[0]), pos_y=float(x[1]), pos_z=float(x[2]), pitch=pitch, yaw=yaw, roll=roll, fov=float(x[6])
    )
    params = camera_params(camera)
    places = best["places"]
    floor_off = False
    # Steeper, the level marks' camera is noise: the entered height holds the fit instead.
    compared = level is not None and min(pitch, float(level[0][3])) >= STEEP_PITCH
    if compared and level is not None and any(mark_id not in {m.id for m in level[1]} for mark_id in places):
        plane_z = float(np.mean([m.z for m in level[1]]))
        floor_off = not _floor_holds(level[0], params, plane_z, np.array(list(places.values())), *canvas)
    fits = []
    for mark in marks:
        if mark.id not in places:
            fits.append(MarkFit(mark.id, None, best["failed"].get(mark.id, "")))
            continue
        place = places[mark.id]
        seen = project_points(params, place.reshape(1, 3), canvas_w, canvas_h)[0]
        distance_error = None
        if mark.kind == "triangulated":
            distance_error = max(
                abs(float(np.linalg.norm(place - places[parent])) - measured)
                for parent, measured in ((mark.from_a, mark.distance_a), (mark.from_b, mark.distance_b))
            )
        fits.append(
            MarkFit(
                mark.id,
                (float(place[0]), float(place[1]), float(place[2])),
                "",
                (float(seen[0]), float(seen[1])) if np.isfinite(seen).all() else None,
                distance_error,
            )
        )
    return MarkSolve(camera, tuple(fits), floor_off=floor_off, steep=pitch < STEEP_PITCH)


def plausibility(
    camera: CameraConfig,
    fits: Sequence[MarkFit],
    errors: dict[int, float],
    hint: Sequence[float] | None,
    per_axis: bool,
    *,
    floor_off: bool = False,
) -> list[dict[str, Any]]:
    """What looks implausible about a solve, as data for the page to word in its units.

    ``errors`` is each pinned mark's distance from where the camera sees it, on the snapshot as shown.
    ``per_axis`` is Advanced's check of the camera position: any axis off by more than max(2.5 m, 10 % of the
    camera's distance to the marks); Simple checks the whole gap against max(1 m, 10 %).
    ``floor_off`` is :attr:`MarkSolve.floor_off`.
    """
    placed = [f.position for f in fits if f.position is not None]
    cam = np.array([camera.pos_x, camera.pos_y, camera.pos_z])
    out: list[dict[str, Any]] = []
    if hint is not None and placed:
        delta = cam - np.array(hint, dtype=np.float64)
        off = float(np.linalg.norm(delta))
        span = float(np.linalg.norm(cam - np.mean(np.array(placed), axis=0)))
        allowed = max(2.5 if per_axis else 1.0, 0.1 * span)
        if (float(np.abs(delta).max()) if per_axis else off) > allowed:
            out.append(
                {
                    "kind": "offset",
                    "delta": [float(v) for v in delta],
                    "off": off,
                    "allowed": allowed,
                    "per_axis": per_axis,
                }
            )
    if camera.fov < 20.0 or camera.fov > 120.0:
        out.append({"kind": "fov", "fov": camera.fov})
    if placed and camera.pos_z < min(p[2] for p in placed):
        out.append({"kind": "below"})
    if camera.pitch > 0.0:
        out.append({"kind": "up"})
    if floor_off:
        out.append({"kind": "reach"})
    for fit in fits:
        if errors.get(fit.id, 0.0) > FLAG_PX:
            out.append({"kind": "pixel", "id": fit.id, "px": errors[fit.id]})
        if fit.distance_error is not None and fit.distance_error > FLAG_DISTANCE_M:
            out.append({"kind": "distance", "id": fit.id, "m": fit.distance_error})
    return out


def suggested_floor(marks: Sequence[Mark]) -> tuple[float, tuple[int, ...]] | None:
    """The floor the marks suggest: the mean height of the largest group whose heights lie within
    FLOOR_GROUP_M of each other, and the group's ids. Overlapping groups of that size are one floor
    measured loosely: the tightest, then the lowest, stands for it. None when no two agree or two
    separate groups are the same size."""
    ordered = sorted(marks, key=lambda m: m.z)
    groups = [[m for m in ordered[i:] if m.z - low.z <= FLOOR_GROUP_M + 1e-9] for i, low in enumerate(ordered)]
    size = max((len(g) for g in groups), default=0)
    largest = [g for g in groups if len(g) == size]
    if size < 2 or any(not {m.id for m in a} & {m.id for m in b} for a in largest for b in largest):
        return None
    best = min(largest, key=lambda g: (g[-1].z - g[0].z, g[0].z))
    return round(sum(m.z for m in best) / size, 3), tuple(m.id for m in best)


def pin_errors(cam: CameraConfig, width: float, height: float) -> dict[int, float]:
    """Each pinned mark's distance in px from where *cam*, lens included, sees it in a *width* x *height* picture.

    A triangulated point counts once its solve placed it; a mark behind the camera has none.
    """
    pinned = [(m, [m.x, m.y, m.z] if m.kind == "known" else m.position) for m in cam.calibration_marks if m.pin]
    pinned = [(m, at) for m, at in pinned if at]
    if not pinned:
        return {}
    screen = project_points(camera_params(cam), np.array([at for _, at in pinned], dtype=np.float64), width, height)
    seen = apply_overlay_distortion(screen, width, height, cam.lens_k1, cam.lens_k2, cam.lens_cx, cam.lens_cy)
    return {
        m.id: math.dist(xy, (m.pin[0] * width, m.pin[1] * height))
        for (m, _), xy in zip(pinned, seen, strict=True)
        if np.isfinite(xy).all()
    }
