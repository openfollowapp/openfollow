# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Setup Wizard's camera solve from marks: known points with a typed position, and triangulated points
measured by their distances to two other marks plus their height.

One fit adjusts the 7 camera values and every triangulated point's x, y against the pins and the
distances, each residual in its own expected error. Every pin counts the same, and no mark is left out:
one that disagrees shows in its error and in :func:`plausibility`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import numpy.typing as npt

from openfollow.configuration import CameraConfig
from openfollow.scene.solver import camera_params, project_points, solve_camera_dlt

PIXEL_SIGMA = 1.0  # px: a careful pin on a mark, native snapshot pixels
DISTANCE_SIGMA = 0.04  # m: a hand-held laser or tape on a mark
FLAG_PX = 5.0  # a pin further than this from where the camera sees its mark is named
FLAG_DISTANCE_M = 3.0 * DISTANCE_SIGMA
SEED_FOVS = (40.0, 90.0)  # tried beside the saved FOV from a camera position
# Two measured circles may miss each other by this much and still be one point: the noise of two distances.
MEET_TOLERANCE = 3.0 * math.sqrt(2.0) * DISTANCE_SIGMA
# Four pinned known marks within this of one height, every triangle of them at least this large, seed the solve.
_LEVEL_M = 0.01
_MIN_TRIANGLE_M2 = 0.05

REASON_HEIGHT = "A distance is shorter than the height between the two points."
REASON_SAME_PLACE = "Its two points are at the same place."
REASON_DIFFER = "The two distances differ by more than their points are apart."
REASON_SHORT = "The two distances add up to less than their points are apart."
REASON_UNPLACED_FROM = "It is measured from a point that cannot be placed."
NO_FIT = "No camera fits these points. Check the pins and the measurements."

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


def trilaterate(
    a: Sequence[float], b: Sequence[float], distance_a: float, distance_b: float, z: float
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


def _seeds(
    marks: Sequence[Mark], hint: Sequence[float] | None, saved_fov: float, canvas_w: float, canvas_h: float
) -> list[npt.NDArray[np.float64]]:
    seeds = []
    if hint is not None:
        pitch, yaw = look_at(hint, (0.0, 0.0, 0.0))
        for fov in (saved_fov, *SEED_FOVS):
            seeds.append(np.array([hint[0], hint[1], hint[2], pitch, yaw, 0.0, fov], dtype=np.float64))
    quad = _level_quad([m for m in marks if m.kind == "known" and m.pin is not None])
    if quad is not None:
        world = [(q.position[0], q.position[1], q.z) for q in quad if q.position]
        screen = [q.pin for q in quad if q.pin]
        seeded = solve_camera_dlt(world, screen, canvas_w, canvas_h)
        if seeded is not None:
            seeds.append(camera_params(seeded))
    return seeds


class _Problem:
    """The joint adjustment: camera (7) and each live triangulated mark's x, y; residuals in their sigma."""

    def __init__(
        self,
        known: dict[int, npt.NDArray[np.float64]],
        live: Sequence[Mark],
        pinned: Sequence[Mark],
        canvas: tuple[float, float],
    ) -> None:
        self.known = known
        self.live = list(live)
        self.pinned = list(pinned)
        self.canvas = canvas
        self.pins = np.array([m.pin for m in self.pinned], dtype=np.float64).reshape(len(self.pinned), 2)

    def places(self, x: npt.NDArray[np.float64]) -> dict[int, npt.NDArray[np.float64]]:
        placed = dict(self.known)
        for i, mark in enumerate(self.live):
            placed[mark.id] = np.array([x[7 + 2 * i], x[8 + 2 * i], mark.z])
        return placed

    def residuals(self, x: npt.NDArray[np.float64]) -> npt.NDArray[np.float64] | None:
        """Per pin (2), then per triangulated mark its two distances; None off the valid range."""
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
        r = np.concatenate([pins, np.array(lengths, dtype=np.float64)])
        return r if bool(np.isfinite(r).all()) else None


def _fit(
    problem: _Problem, x0: npt.NDArray[np.float64], stale: Callable[[], bool] | None, iterations: int = 300
) -> tuple[npt.NDArray[np.float64], float] | None:
    """Levenberg-Marquardt from ``x0``; None when the start or a derivative probe is off the valid range.
    ``stale`` is asked before every iteration and raises :class:`SolveSuperseded` once it answers True."""
    x = x0.copy()
    r = problem.residuals(x)
    if r is None:
        return None
    damping = 1e-3
    for _ in range(iterations):
        if stale is not None and stale():
            raise SolveSuperseded
        jac = np.empty((r.size, x.size))
        for k in range(x.size):
            step = 1e-6 * (1.0 + abs(x[k]))
            up, down = x.copy(), x.copy()
            up[k] += step
            down[k] -= step
            r_up, r_down = problem.residuals(up), problem.residuals(down)
            if r_up is None or r_down is None:
                return x, float(r @ r)
            jac[:, k] = (r_up - r_down) / (2.0 * step)
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
) -> MarkSolve:
    """The camera from the marks; ``hint`` is the camera position the operator estimated, or None.

    Every seed (the hint at the saved FOV, 40° and 90°, looking at 0/0/0; the homography of four pinned known
    marks at one height) is fitted twice: with each triangulated mark on the side its pin picks as the seed
    sees it, and with all of them on the other side. An upright camera (at or above the lowest mark, not
    looking up) beats one that is not, then the lower cost wins. ``stale`` is asked before every iteration;
    once it answers True the solve raises :class:`SolveSuperseded`.
    """
    canvas = (canvas_w, canvas_h)
    ordered = _order(marks)
    triangulated = [m for m in ordered if m.kind == "triangulated"]
    known = {m.id: np.array([m.position[0], m.position[1], m.z]) for m in ordered if m.position is not None}
    seeds = _seeds(ordered, hint, saved_fov, canvas_w, canvas_h)
    if not seeds:
        return MarkSolve(None, (), "the camera position")

    best: dict[str, Any] | None = None
    reasons: dict[int, str] = {}
    for seed in seeds:
        for flip in (False, True) if triangulated else (False,):
            placed = dict(known)
            failed: dict[int, str] = {}
            for mark in triangulated:
                if mark.from_a not in placed or mark.from_b not in placed:
                    failed[mark.id] = REASON_UNPLACED_FROM
                    continue
                found = trilaterate(placed[mark.from_a], placed[mark.from_b], mark.distance_a, mark.distance_b, mark.z)
                if isinstance(found, str):
                    failed[mark.id] = found
                    continue
                seen = project_points(seed, np.array(found), canvas_w, canvas_h)
                gaps = np.linalg.norm(seen - np.array(mark.pin, dtype=np.float64), axis=1)
                nearer = 1 if np.isfinite(gaps).any() and np.nanargmin(gaps) == 1 else 0
                placed[mark.id] = found[1 - nearer if flip else nearer]
            reasons = reasons or failed
            live = [m for m in triangulated if m.id not in failed]
            pinned = [m for m in ordered if m.pin is not None and m.id not in failed]
            if len(pinned) < 4:
                continue
            problem = _Problem(known, live, pinned, canvas)
            x0 = np.concatenate([seed, *[placed[m.id][:2] for m in live]])
            fitted = _fit(problem, x0, stale)
            if fitted is None:
                continue
            x, cost = fitted
            places = problem.places(x)
            floor = min(float(p[2]) for p in places.values())
            upright = bool(x[2] >= floor and x[3] <= 0.0)
            if best is None or (upright, -cost) > (best["upright"], -best["cost"]):
                best = {"x": x, "cost": cost, "upright": upright, "places": places, "failed": failed, "live": live}

    if best is None:
        fits = tuple(MarkFit(m.id, None, reasons.get(m.id, "")) for m in marks)
        return MarkSolve(None, fits, NO_FIT)
    x = best["x"]
    if not 10.0 < float(x[6]) < 170.0:
        return MarkSolve(None, tuple(MarkFit(m.id, None, best["failed"].get(m.id, "")) for m in marks), NO_FIT)
    camera = CameraConfig(
        pos_x=float(x[0]),
        pos_y=float(x[1]),
        pos_z=float(x[2]),
        pitch=float(x[3]),
        yaw=_wrap_degrees(float(x[4])),
        roll=_wrap_degrees(float(x[5])),
        fov=float(x[6]),
    )
    params = camera_params(camera)
    places = best["places"]
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
    return MarkSolve(camera, tuple(fits))


def plausibility(
    camera: CameraConfig,
    fits: Sequence[MarkFit],
    errors: dict[int, float],
    hint: Sequence[float] | None,
    per_axis: bool,
) -> list[dict[str, Any]]:
    """What looks implausible about a solve, as data for the page to word in its units.

    ``errors`` is each pinned mark's distance from where the camera sees it, on the snapshot as shown.
    ``per_axis`` is Advanced's check of the camera position: any axis off by more than max(2.5 m, 10 % of the
    camera's distance to the marks); Simple checks the whole gap against max(1 m, 10 %).
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
    for fit in fits:
        if errors.get(fit.id, 0.0) > FLAG_PX:
            out.append({"kind": "pixel", "id": fit.id, "px": errors[fit.id]})
        if fit.distance_error is not None and fit.distance_error > FLAG_DISTANCE_M:
            out.append({"kind": "distance", "id": fit.id, "m": fit.distance_error})
    return out
