# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Calibration points: far correspondences measured with a laser from two grid corners.

A point's place in the stage frame is fixed by its distances from two grid corners and its height
above stage level, and the operator pins it on the snapshot. The camera is fitted to the corner pins,
the point pins and the distances together: each point's x, y is an unknown of the fit, every
observation is weighted by its own expected error. A laser fixes a far point's depth well and its
width poorly (the width comes from the difference of two long distances), the pin the other way
round, so neither is reduced to a position before the fit.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt

from openfollow.configuration import CameraConfig
from openfollow.scene.solver import project_points, solve_camera_dlt, unproject_to_plane

PIXEL_SIGMA = 1.0  # px: a careful pin on a calibration point's mark, native snapshot pixels
CORNER_SIGMA = 3.0  # px: a pin on a grid corner, whose mark on the stage floor is seldom that sharp
DISTANCE_SIGMA = 0.04  # m: a hand-held laser on the corner mark, a plumb stand
FLAG_SIGMA = 3.0  # a normalised residual past this flags the point it belongs to
# Over the picture, a fit with points may put the floor at most this many times as far, or as near,
# as the corners alone put it; a point that bends the camera further is left out.
FLOOR_REACH = 2.0
_FLOOR_PROBES = np.array([[x, y] for y in np.linspace(0.0, 1.0, 7) for x in np.linspace(0.0, 1.0, 9)])

# Two measured circles may miss each other by this much and still be one point: the noise of
# two distances. A point straight off the end of its baseline lies on that edge.
_MEET_TOLERANCE = 3.0 * math.sqrt(2.0) * DISTANCE_SIGMA

REASON_DIFFER = "The two distances differ by more than the corners are apart."
REASON_SHORT = "The two distances add up to less than the corners are apart."
REASON_HEIGHT = "A distance is shorter than the point's height."
REASON_BEHIND = "Neither place the distances allow is in front of the camera."
REASON_DISAGREES = "It disagrees with the corners and the other points."
REASON_DISAGREES_CORNERS = "It disagrees with the corners."
REASON_PAIR = "The two points disagree, and two cannot tell which one is wrong."
REASON_FLOOR = "It moves the far floor too far from where the corners put it."
REASON_SAME_PLACE = "Its two corners are at the same place."

Status = Literal["ok", "unverified", "flagged"]


@dataclass(frozen=True)
class CalibrationPoint:
    """One point as measured; the corners in world coordinates, the pin on the pinhole snapshot."""

    corner_a: tuple[float, float, float]
    corner_b: tuple[float, float, float]
    distance_a: float
    distance_b: float
    height: float
    pin: tuple[float, float]


@dataclass(frozen=True)
class PointFit:
    """What the solve made of one point. ``position`` is None when the distances place it nowhere."""

    position: tuple[float, float, float] | None
    status: Status
    reason: str = ""
    residual: float = math.nan  # largest normalised residual of its observations, in sigma


@dataclass(frozen=True)
class CalibrationSolve:
    camera: CameraConfig
    points: tuple[PointFit, ...]
    corner_residual: float  # largest normalised residual of the corner pins, in sigma


def trilaterate(
    a: Sequence[float], b: Sequence[float], distance_a: float, distance_b: float, height: float
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]] | str:
    """The two places at ``height`` above corner level that lie at the measured distances from ``a``
    and ``b``, mirror images across the line between them; or the reason there are none.

    Circles that miss each other by no more than the noise of two distances meet at their closest
    approach, so a point on the line through both corners still has a place.
    """
    za = float(a[2])
    if distance_a < abs(height) or distance_b < abs(height + za - float(b[2])):
        return REASON_HEIGHT
    ra = math.sqrt(distance_a**2 - height**2)
    rb = math.sqrt(distance_b**2 - (height + za - float(b[2])) ** 2)
    pa = np.array(a[:2], dtype=np.float64)
    pb = np.array(b[:2], dtype=np.float64)
    d = float(np.linalg.norm(pb - pa))
    if d == 0.0:
        return REASON_SAME_PLACE
    if abs(ra - rb) > d + _MEET_TOLERANCE:
        return REASON_DIFFER
    if ra + rb < d - _MEET_TOLERANCE:
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
    z = za + height
    first = pa + along * ex + across * ey
    second = pa + along * ex - across * ey
    return np.array([first[0], first[1], z]), np.array([second[0], second[1], z])


def _wrap_degrees(angle: float) -> float:
    """``angle`` in (-180, 180], the corner solve's range: the fit moves freely across +/-180."""
    return 180.0 - (180.0 - angle) % 360.0


def _camera_params(cam: CameraConfig) -> npt.NDArray[np.float64]:
    return np.array([cam.pos_x, cam.pos_y, cam.pos_z, cam.pitch, cam.yaw, cam.roll, cam.fov], dtype=np.float64)


def _pick_by_pin(
    places: tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]],
    pin: tuple[float, float],
    params: npt.NDArray[np.float64],
    canvas_w: float,
    canvas_h: float,
) -> npt.NDArray[np.float64] | None:
    """The mirror image that projects nearer the pin; None when neither is in front of the camera."""
    seen = project_points(params, np.array(places), canvas_w, canvas_h)
    gaps = np.linalg.norm(seen - np.array(pin, dtype=np.float64), axis=1)
    if not np.isfinite(gaps).any():
        return None
    return places[int(np.nanargmin(gaps))]


def _floor_holds(
    reference: npt.NDArray[np.float64],
    camera: npt.NDArray[np.float64],
    plane_z: float,
    places: npt.NDArray[np.float64],
    canvas_w: float,
    canvas_h: float,
) -> bool:
    """Whether ``camera`` puts the floor where ``reference`` (the corners alone) does, within FLOOR_REACH.

    Only the picture the reference puts on the floor within reach of the points is asked about, so a
    horizon in the picture, or floor far beyond the points, decides nothing.
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
    """The joint adjustment: camera (7) and each point's x, y; residuals normalised by their sigma."""

    def __init__(
        self,
        corner_world: npt.NDArray[np.float64],
        corner_screen: npt.NDArray[np.float64],
        points: Sequence[CalibrationPoint],
        canvas_w: float,
        canvas_h: float,
    ) -> None:
        self.corner_world = corner_world
        self.corner_screen = corner_screen
        self.n = len(points)
        self.z = np.array([p.corner_a[2] + p.height for p in points], dtype=np.float64)
        self.a = np.array([p.corner_a for p in points], dtype=np.float64).reshape(self.n, 3)
        self.b = np.array([p.corner_b for p in points], dtype=np.float64).reshape(self.n, 3)
        self.da = np.array([p.distance_a for p in points], dtype=np.float64)
        self.db = np.array([p.distance_b for p in points], dtype=np.float64)
        self.pins = np.array([p.pin for p in points], dtype=np.float64).reshape(self.n, 2)
        self.canvas = (canvas_w, canvas_h)

    def residuals(self, x: npt.NDArray[np.float64]) -> npt.NDArray[np.float64] | None:
        """Corner pins (8), then per point its pin (2) and its distances (2); None off the valid range."""
        cam = x[:7]
        places = np.column_stack([x[7:].reshape(self.n, 2), self.z])
        try:
            corners = (project_points(cam, self.corner_world, *self.canvas) - self.corner_screen) / CORNER_SIGMA
            pins = (project_points(cam, places, *self.canvas) - self.pins) / PIXEL_SIGMA
        except ValueError:
            return None
        to_a = (np.linalg.norm(places - self.a, axis=1) - self.da) / DISTANCE_SIGMA
        to_b = (np.linalg.norm(places - self.b, axis=1) - self.db) / DISTANCE_SIGMA
        r = np.concatenate([corners.ravel(), np.column_stack([pins, to_a, to_b]).ravel()])
        return r if bool(np.isfinite(r).all()) else None

    def per_point(self, r: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
        return np.abs(r[8:]).reshape(self.n, 4).max(axis=1) if self.n else np.zeros(0)


def _fit(
    problem: _Problem, x0: npt.NDArray[np.float64], iterations: int = 100
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]] | None:
    """Levenberg-Marquardt from ``x0``; None when the start or a derivative probe is off the valid range."""
    x = x0.copy()
    r = problem.residuals(x)
    if r is None:
        return None
    damping = 1e-3
    for _ in range(iterations):
        jac = np.empty((r.size, x.size))
        for k in range(x.size):
            step = 1e-6 * (1.0 + abs(x[k]))
            up, down = x.copy(), x.copy()
            up[k] += step
            down[k] -= step
            r_up, r_down = problem.residuals(up), problem.residuals(down)
            if r_up is None or r_down is None:
                return None
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
            return x, r
        x, r = x + delta, trial
        damping = max(damping / 3.0, 1e-12)
        if float(np.max(np.abs(delta) / (1.0 + np.abs(x)))) < 1e-12:
            break
    return x, r


def solve_with_points(
    corner_world: Sequence[Sequence[float]],
    corner_screen: Sequence[Sequence[float]],
    points: Sequence[CalibrationPoint],
    canvas_w: float,
    canvas_h: float,
) -> CalibrationSolve | None:
    """The camera from the four corners and the calibration points; None when the corners give none.
    When no point is used, the camera is exactly the corners' own solve.

    A point the distances place nowhere is flagged and left out. When a point's residual passes
    :data:`FLAG_SIGMA`, three or more points drop the one whose removal leaves the others the best fit,
    and fit again; two points that disagree are both left out, since from a narrow stage strip neither
    can show the other wrong; a lone point that disagrees with the corners, a corner pin's residual
    included, is left out. A fit that passes is still left out the same way when it moves the floor
    across the picture past :data:`FLOOR_REACH` from where the corners alone put it. A point that is the
    only one in the fit cannot be cross-checked: ``unverified``.
    """
    world = np.array(corner_world, dtype=np.float64)
    screen = np.array(corner_screen, dtype=np.float64)
    seed = solve_camera_dlt([tuple(p) for p in world], [(float(p[0]), float(p[1])) for p in screen], canvas_w, canvas_h)
    if seed is None:
        return None
    seed_params = _camera_params(seed)

    fits: list[PointFit | None] = [None] * len(points)
    places: dict[int, npt.NDArray[np.float64]] = {}
    for i, point in enumerate(points):
        found = trilaterate(point.corner_a, point.corner_b, point.distance_a, point.distance_b, point.height)
        if isinstance(found, str):
            fits[i] = PointFit(None, "flagged", found)
            continue
        place = _pick_by_pin(found, point.pin, seed_params, canvas_w, canvas_h)
        if place is None:
            fits[i] = PointFit(None, "flagged", REASON_BEHIND)
            continue
        places[i] = place

    def fit(ids: list[int]) -> tuple[_Problem, npt.NDArray[np.float64], npt.NDArray[np.float64]] | None:
        problem = _Problem(world, screen, [points[i] for i in ids], canvas_w, canvas_h)
        x0 = np.concatenate([seed_params, np.array([places[i][:2] for i in ids]).ravel()])
        fitted = _fit(problem, x0)
        return None if fitted is None else (problem, *fitted)

    def leave_out(ids: list[int], reason: str, residuals: npt.NDArray[np.float64]) -> None:
        for i, residual in zip(ids, residuals, strict=True):
            seen = places[i]
            fits[i] = PointFit((float(seen[0]), float(seen[1]), float(seen[2])), "flagged", reason, float(residual))
            active.remove(i)

    active = sorted(places)
    solved = None
    while active:
        result = fit(active)
        if result is None:
            # No camera fits these points with the corners: they are left out, not the solve.
            leave_out(active[:], REASON_DISAGREES_CORNERS, np.full(len(active), math.nan))
            continue
        problem, x, r = result
        worst = problem.per_point(r)
        # A lone point can bend the camera off the corners while fitting itself, so they count too.
        # With more, the points check each other: corners a few px off would throw good points away.
        alone = len(active) == 1 and float(np.abs(r[:8]).max()) > FLAG_SIGMA
        reason = ""
        if float(worst.max()) <= FLAG_SIGMA and not alone:
            fitted = np.column_stack([x[7:].reshape(len(active), 2), problem.z])
            if _floor_holds(seed_params, x[:7], float(world[:, 2].mean()), fitted, canvas_w, canvas_h):
                solved = result
                break
            reason = REASON_FLOOR
        if len(active) == 1:
            leave_out(active[:], reason or REASON_DISAGREES_CORNERS, worst)
        elif len(active) == 2:
            leave_out(active[:], reason or REASON_PAIR, worst)
        else:
            costs = {}
            for i in active:
                rest = fit([j for j in active if j != i])
                costs[i] = math.inf if rest is None else float(rest[2] @ rest[2])
            out = min(costs, key=lambda i: costs[i])
            leave_out([out], reason or REASON_DISAGREES, worst[[active.index(out)]])

    if solved is None:
        # No point is used, so none may move the pose: it is the corners' own solve.
        corners = (project_points(seed_params, world, canvas_w, canvas_h) - screen) / CORNER_SIGMA
        return CalibrationSolve(seed, tuple(f for f in fits if f is not None), float(np.abs(corners).max()))

    problem, x, r = solved
    status: Status = "ok" if len(active) > 1 else "unverified"
    xy = x[7:].reshape(len(active), 2)
    for j, i in enumerate(active):
        fits[i] = PointFit((float(xy[j, 0]), float(xy[j, 1]), float(problem.z[j])), status, "", float(worst[j]))

    cam = x[:7]
    camera = CameraConfig(
        pos_x=float(cam[0]),
        pos_y=float(cam[1]),
        pos_z=float(cam[2]),
        pitch=float(cam[3]),
        yaw=_wrap_degrees(float(cam[4])),
        roll=_wrap_degrees(float(cam[5])),
        fov=float(cam[6]),
    )
    corner_residual = float(np.abs(r[:8]).max())
    return CalibrationSolve(camera, tuple(f for f in fits if f is not None), corner_residual)
