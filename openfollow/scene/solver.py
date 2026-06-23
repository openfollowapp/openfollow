# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Camera solver for 4-point calibration via DLT homography decomposition.

Given 4 coplanar world points (grid corners in PSN coords) and their
corresponding 2D screen positions, compute the 3x3 homography via
Direct Linear Transform, then decompose it into camera parameters
(pos_x, pos_y, pos_z, pitch, yaw, roll, fov).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import numpy.typing as npt

from openfollow.configuration import CameraConfig
from openfollow.scene.camera import psn_to_pygfx_array

# fov bounds mirror CameraConfig.__post_init__ – outside this band tan(fov/2)
# is degenerate (0 → divide-by-zero, 180 → blows up).
_FOV_MIN_DEG = 1.0
_FOV_MAX_DEG = 179.0


def _require_view_inputs(fov: float, canvas_w: float, canvas_h: float) -> None:
    """Reject degenerate projection inputs before any division.

    Raises ``ValueError`` (not ``ZeroDivisionError``) so callers at a request
    boundary can map it to HTTP 400 rather than 500. ``CameraConfig`` already
    clamps fov, but the wizard endpoints feed a raw client-supplied parameter
    vector straight into the solver with no such clamp.
    """
    if not _FOV_MIN_DEG <= fov <= _FOV_MAX_DEG:
        raise ValueError(f"fov must be within [{_FOV_MIN_DEG}, {_FOV_MAX_DEG}] degrees, got {fov}")
    # Reject NaN/Inf too: ``NaN <= 0`` is False, so a non-finite canvas (which
    # json.loads and float("nan") both produce) would slip past a bare ``<= 0``
    # guard and yield NaN projections – or a non-standard-JSON "NaN" response.
    if not math.isfinite(canvas_w) or canvas_w <= 0.0:
        raise ValueError(f"canvas width must be a finite value > 0, got {canvas_w}")
    if not math.isfinite(canvas_h) or canvas_h <= 0.0:
        raise ValueError(f"canvas height must be a finite value > 0, got {canvas_h}")


def hfov_to_vfov(hfov_deg: float, aspect: float) -> float:
    """Convert horizontal FOV (degrees) to vertical FOV (degrees) for a given aspect ratio.

    Aspect is canvas_w / canvas_h. The camera's stored ``fov`` is horizontal
    (matches datasheet convention); the perspective-projection kernel needs
    vertical FOV, so conversion happens at solver boundaries.
    """
    hfov_rad = math.radians(hfov_deg)
    vfov_rad = 2.0 * math.atan(math.tan(hfov_rad / 2.0) / aspect)
    return math.degrees(vfov_rad)


def vfov_to_hfov(vfov_deg: float, aspect: float) -> float:
    """Inverse of :func:`hfov_to_vfov`."""
    vfov_rad = math.radians(vfov_deg)
    hfov_rad = 2.0 * math.atan(math.tan(vfov_rad / 2.0) * aspect)
    return math.degrees(hfov_rad)


def _rotation_matrix(pitch_rad: float, yaw_rad: float, roll_rad: float) -> npt.NDArray[np.float64]:
    """Build rotation matrix matching pygfx intrinsic XYZ euler convention.

    pygfx euler: (pitch, -yaw, roll) – we receive raw PSN angles and
    negate yaw internally, same as Camera._apply().
    """
    a = pitch_rad
    b = -yaw_rad  # negated, matching Camera._apply()
    c = roll_rad

    ca, sa = math.cos(a), math.sin(a)
    cb, sb = math.cos(b), math.sin(b)
    cc, sc = math.cos(c), math.sin(c)

    # Intrinsic XYZ: R = Rx(a) @ Ry(b) @ Rz(c)
    return np.array(
        [
            [cb * cc, -cb * sc, sb],
            [sa * sb * cc + ca * sc, -sa * sb * sc + ca * cc, -sa * cb],
            [-ca * sb * cc + sa * sc, ca * sb * sc + sa * cc, ca * cb],
        ],
        dtype=np.float64,
    )


def project_points(
    params: npt.NDArray[Any],
    world_pts_psn: npt.NDArray[Any],
    canvas_w: float,
    canvas_h: float,
) -> npt.NDArray[np.float64]:
    """Project world points to screen coords using camera parameters.

    Parameters
    ----------
    params : array of 7 floats
        [pos_x, pos_y, pos_z, pitch, yaw, roll, fov] in PSN convention,
        angles in degrees. ``fov`` is **horizontal** FOV (datasheet convention).
    world_pts_psn : (N, 3) array
        World points in PSN coordinates.
    canvas_w, canvas_h : float
        Canvas size in logical pixels.

    Returns
    -------
    (N, 2) array of screen coordinates (origin top-left, Y down).
    """
    pos_x, pos_y, pos_z, pitch, yaw, roll, fov = params
    _require_view_inputs(float(fov), canvas_w, canvas_h)

    # Camera position in pygfx coords
    cam_pos = np.array([pos_x, pos_z, -pos_y], dtype=np.float64)

    # World points in pygfx coords
    pts = psn_to_pygfx_array(world_pts_psn)

    # View transform: translate then rotate by inverse camera rotation
    R = _rotation_matrix(math.radians(pitch), math.radians(yaw), math.radians(roll))
    translated = pts - cam_pos  # (N, 3)
    view = (R.T @ translated.T).T  # (N, 3) – points in camera space

    # Perspective projection (camera looks along -Z). The stored fov is
    # horizontal; convert to vertical for the focal-length calculation that
    # the NDC equations expect.
    aspect = canvas_w / canvas_h
    vfov_rad = math.radians(hfov_to_vfov(fov, aspect))
    f = 1.0 / math.tan(vfov_rad / 2.0)

    # Camera looks along -Z, so a visible point has view_z < 0. A point at or
    # behind the camera plane (view_z >= -eps) would otherwise divide to a
    # finite, mirrored screen coord that the downstream isfinite filters can't
    # discard – replace its depth with NaN so the projection comes out NaN.
    view_z = view[:, 2]
    safe_z = np.where(view_z >= -1e-9, np.nan, view_z)

    # NDC
    ndc_x = (f / aspect) * (view[:, 0] / -safe_z)
    ndc_y = f * (view[:, 1] / -safe_z)

    # Screen coords (top-left origin, Y down)
    sx = (ndc_x + 1.0) / 2.0 * canvas_w
    sy = (1.0 - ndc_y) / 2.0 * canvas_h

    return np.column_stack([sx, sy])


def ground_circle_world_ring(
    cx: float,
    cy: float,
    z: float,
    radius: float,
    segments: int = 24,
) -> list[tuple[float, float, float]]:
    """Build a horizontal ring of world points around ``(cx, cy)`` at height ``z``.

    Single source of truth for the marker ground-circle geometry: the overlay
    draws this ring and the mouse hit-test projects the same points, so the
    clickable region can't drift from the rendered circle.
    """
    return [
        (
            cx + radius * math.cos(2 * math.pi * i / segments),
            cy + radius * math.sin(2 * math.pi * i / segments),
            z,
        )
        for i in range(segments)
    ]


def unproject_to_plane(
    params: npt.NDArray[Any],
    screen_pts: npt.NDArray[Any],
    canvas_w: float,
    canvas_h: float,
    plane_z_psn: float = 0.0,
) -> npt.NDArray[np.float64]:
    """Unproject 2D screen points onto a horizontal PSN plane at *plane_z_psn*.

    Parameters
    ----------
    params : array of 7 floats
        [pos_x, pos_y, pos_z, pitch, yaw, roll, fov] – PSN convention, degrees.
        ``fov`` is **horizontal** FOV (datasheet convention).
    screen_pts : (N, 2) array of screen coordinates (origin top-left, Y down).
    canvas_w, canvas_h : float
        Canvas size in logical pixels.
    plane_z_psn : float
        PSN Z height of the target plane.

    Returns
    -------
    (N, 3) array of PSN world coordinates on the plane, or NaN rows for
    rays that don't intersect.
    """
    pos_x, pos_y, pos_z, pitch, yaw, roll, fov = params
    _require_view_inputs(float(fov), canvas_w, canvas_h)

    cam_pos = np.array([pos_x, pos_z, -pos_y], dtype=np.float64)

    R = _rotation_matrix(math.radians(pitch), math.radians(yaw), math.radians(roll))
    aspect = canvas_w / canvas_h
    # fov is horizontal – convert to vertical for the focal-length calculation.
    vfov_rad = math.radians(hfov_to_vfov(fov, aspect))
    f = 1.0 / math.tan(vfov_rad / 2.0)

    pts = np.atleast_2d(screen_pts).astype(np.float64)
    # Screen → NDC
    ndc_x = (pts[:, 0] / canvas_w) * 2.0 - 1.0
    ndc_y = 1.0 - (pts[:, 1] / canvas_h) * 2.0

    # Camera-space ray direction (camera looks along -Z)
    dx = ndc_x * aspect / f
    dy = ndc_y / f
    dz = np.full_like(dx, -1.0)
    dirs_cam = np.column_stack([dx, dy, dz])  # (N, 3)

    # Rotate to world (pygfx) space
    dirs_world = (R @ dirs_cam.T).T  # (N, 3)

    # Intersect with pygfx plane y = plane_z_psn  (pygfx y = PSN z)
    plane_y = plane_z_psn
    result = np.full((len(pts), 3), np.nan, dtype=np.float64)
    for i in range(len(pts)):
        dy_w = dirs_world[i, 1]
        # Numerical-tolerance guard: a ray exactly parallel to the
        # plane (|dy_w| < 1e-12) requires a degenerate camera attitude
        # and hand-crafted screen coordinates, never observed in practice.
        if abs(dy_w) < 1e-12:  # pragma: no cover
            continue
        t = (plane_y - cam_pos[1]) / dy_w
        if t <= 0:
            continue  # intersection behind camera or at the camera origin
        hit = cam_pos + t * dirs_world[i]
        # pygfx (x, y, z) → PSN (x, -z, y)
        result[i] = [hit[0], -hit[2], hit[1]]

    return result


# ---------------------------------------------------------------------------
# DLT Homography + Camera Decomposition
# ---------------------------------------------------------------------------


def _normalize_points(pts: npt.NDArray[Any]) -> tuple[npt.NDArray[Any], npt.NDArray[np.float64]]:
    """Normalize 2D points for numerically stable DLT.

    Translates centroid to origin and scales so average distance = sqrt(2).
    Returns (normalized_pts, 3x3 transform matrix).
    """
    centroid = pts.mean(axis=0)
    shifted = pts - centroid
    avg_dist = float(np.mean(np.linalg.norm(shifted, axis=1)))
    if avg_dist < 1e-12:
        return pts.copy(), np.eye(3, dtype=np.float64)
    scale = math.sqrt(2.0) / avg_dist
    T = np.array(
        [
            [scale, 0.0, -scale * centroid[0]],
            [0.0, scale, -scale * centroid[1]],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    homo = np.column_stack([pts, np.ones(len(pts))])
    normalized = (T @ homo.T).T[:, :2]
    return normalized, T


def compute_homography(
    plane_pts: npt.NDArray[Any],
    screen_pts: npt.NDArray[Any],
) -> npt.NDArray[np.float64]:
    """Compute 3x3 homography via normalized DLT from 4 point correspondences.

    Maps plane coordinates to screen coordinates:
        s * [sx, sy, 1]^T = H @ [px, py, 1]^T

    Parameters
    ----------
    plane_pts : (4, 2) array of 2D coordinates on the grid plane.
    screen_pts : (4, 2) array of 2D screen pixel coordinates.

    Returns
    -------
    3x3 homography matrix.
    """
    # Normalize for numerical stability
    src_norm, T_src = _normalize_points(plane_pts)
    dst_norm, T_dst = _normalize_points(screen_pts)

    # Build 8x9 DLT matrix
    A = np.zeros((8, 9), dtype=np.float64)
    for i in range(4):
        px, py = src_norm[i]
        qx, qy = dst_norm[i]
        A[2 * i] = [px, py, 1, 0, 0, 0, -qx * px, -qx * py, -qx]
        A[2 * i + 1] = [0, 0, 0, px, py, 1, -qy * px, -qy * py, -qy]

    _, _, Vt = np.linalg.svd(A)
    H_norm = Vt[-1].reshape(3, 3)

    # Denormalize: H = T_dst^{-1} @ H_norm @ T_src
    H: npt.NDArray[np.float64] = np.asarray(
        np.linalg.solve(T_dst, H_norm) @ T_src,
        dtype=np.float64,
    )

    # Normalize so H[2,2] = 1 (standard convention).
    # The False arm (|H[2,2]| ≤ 1e-12) requires the homography to
    # collapse the world plane through the camera origin – impossible
    # for the four-corner inputs the calibration overlay produces.
    if abs(H[2, 2]) > 1e-12:  # pragma: no branch
        H /= H[2, 2]

    return H


def decompose_homography(
    H: npt.NDArray[Any],
    canvas_w: float,
    canvas_h: float,
    z_offset: float,
) -> CameraConfig | None:
    """Extract camera parameters from a plane-to-screen homography.

    The homography maps PSN plane coords (psn_x, psn_y) at z=z_offset
    to screen pixels (top-left origin, Y down).

    Uses the known intrinsic structure (square pixels, principal point
    at canvas center) to solve for focal length, then decomposes the
    rotation and translation.

    Returns CameraConfig or None if decomposition yields an invalid camera.
    """
    h1, h2, h3 = H[:, 0], H[:, 1], H[:, 2]

    cw2 = canvas_w / 2.0
    ch2 = canvas_h / 2.0

    # Center the homography columns (subtract principal point contribution)
    # K^{-1} @ h = [(h[0] - cw2*h[2])/fp, (-h[1] + ch2*h[2])/fp, h[2]]
    a = h1[0] - cw2 * h1[2]
    b = h1[1] - ch2 * h1[2]
    d = h2[0] - cw2 * h2[2]
    e = h2[1] - ch2 * h2[2]

    # Solve for focal_px^2 using orthonormality constraints on rotation columns.
    #
    # Orthogonality: denom_orth * fp^2 = numer_orth
    # Equal norms:   denom_norm * fp^2 = numer_norm
    #
    # Combined via least-squares: fp^2 = (A . b) / (A . A)
    denom_orth = h1[2] * h2[2]
    numer_orth = -(a * d + b * e)

    denom_norm = h2[2] ** 2 - h1[2] ** 2
    numer_norm = a**2 + b**2 - d**2 - e**2

    ls_num = denom_orth * numer_orth + denom_norm * numer_norm
    ls_den = denom_orth**2 + denom_norm**2

    if ls_den > 1e-20 and ls_num / ls_den > 0:
        fp2 = ls_num / ls_den
    else:
        # Near-affine case: both constraints degenerate (camera nearly
        # top-down). Estimate fp from average K^{-1} column norm with fp=1,
        # then scale so the column norms equal 1.
        n1 = math.sqrt(a**2 + b**2 + h1[2] ** 2)
        n2 = math.sqrt(d**2 + e**2 + h2[2] ** 2)
        avg_n = (n1 + n2) / 2.0
        # pragma: no cover – both columns of K⁻¹·H zero requires a
        # degenerate top-down camera; documented Phase 11 numerical
        # survivor.
        if avg_n < 1e-12:  # pragma: no cover
            return None
        fp2 = avg_n**2  # fp that makes avg ||K^{-1} h_i|| / fp ≈ 1

    fp = math.sqrt(fp2)

    # Focal length in pixels is defined against canvas_h, so this yields
    # vertical FOV. Convert to horizontal FOV before returning so the stored
    # value matches datasheet convention.
    vfov_deg = math.degrees(2.0 * math.atan(canvas_h / (2.0 * fp)))
    aspect = canvas_w / canvas_h
    fov_deg = vfov_to_hfov(vfov_deg, aspect)
    if fov_deg < 10.0 or fov_deg > 170.0:
        return None

    # Compute K^{-1} @ H columns
    def _kinv_col(col: npt.NDArray[Any]) -> npt.NDArray[np.float64]:
        return np.array(
            [
                (col[0] - cw2 * col[2]) / fp,
                -(col[1] - ch2 * col[2]) / fp,
                col[2],
            ]
        )

    v1 = _kinv_col(h1)
    v2 = _kinv_col(h2)
    v3 = _kinv_col(h3)

    # Scale factor (should make rotation columns unit-length)
    lam = float(np.linalg.norm(v1))
    # pragma: no cover – ‖K⁻¹·h₁‖ < 1e-12 requires an essentially-zero
    # first homography column, blocked upstream by the pre-decomposition
    # well-posedness checks. Phase 11 numerical-survivor.
    if lam < 1e-12:  # pragma: no cover
        return None

    # Choose sign so plane origin is in front of camera.
    # b3[2] = v3[2]/lam must be > 0 (view_z of origin = -b3[2] < 0).
    # pragma: no branch – for a well-formed perspective from the
    # calibration overlay, v3[2]/lam is always positive (origin in
    # front of camera). The sign-flip arm is a safety net for
    # malformed homographies upstream code already filters out.
    if v3[2] / lam < 0:  # pragma: no branch
        lam = -lam  # pragma: no cover

    b1 = v1 / lam
    b2 = v2 / lam
    b3 = v3 / lam

    # Recover rotation rows.
    #
    # Plane basis in pygfx: e1=(1,0,0), e2=(0,0,-1), origin O=(0,z_off,0)
    # F = diag(1,1,-1) @ R^T
    # b1 = F @ e1 = (R[0,0], R[0,1], -R[0,2])
    # b2 = F @ e2 = (-R[2,0], -R[2,1], R[2,2])
    r0 = np.array([b1[0], b1[1], -b1[2]])  # R row 0
    r2 = np.array([-b2[0], -b2[1], b2[2]])  # R row 2

    # Orthogonalize and normalize
    n0 = np.linalg.norm(r0)
    # pragma: no cover – ‖r0‖ < 1e-12 only for a homography whose
    # first column collapses to zero, blocked by upstream
    # well-posedness checks. Phase 11 numerical-survivor.
    if n0 < 1e-12:  # pragma: no cover
        return None
    r0 = r0 / n0
    r2 = r2 - np.dot(r2, r0) * r0
    n2 = float(np.linalg.norm(r2))
    # pragma: no cover – same reasoning as n0 above; ‖r2 − (r2·r0)r0‖
    # < 1e-12 requires r2 ≈ r0, only possible for a degenerate
    # homography upstream code already rejects.
    if n2 < 1e-12:  # pragma: no cover
        return None
    r2 = r2 / n2
    r1 = np.cross(r2, r0)

    R = np.array([r0, r1, r2])

    # Ensure proper rotation (det = +1) via closest rotation matrix
    U, _, Vt = np.linalg.svd(R)
    det_sign = np.linalg.det(U @ Vt)
    R = U @ np.diag([1.0, 1.0, det_sign]) @ Vt

    # Camera position in pygfx coords.
    # From F @ (O - cam) = b3 and F = diag(1,1,-1) @ R^T:
    #   R^T @ (O - cam) = (b3[0], b3[1], -b3[2])
    #   cam = O - R @ (b3[0], b3[1], -b3[2])
    t_view = np.array([b3[0], b3[1], -b3[2]])
    origin = np.array([0.0, z_offset, 0.0])
    cam_pygfx = origin - R @ t_view

    # pygfx (x, y, z) -> PSN (x, -z, y)
    pos_x = float(cam_pygfx[0])
    pos_y = float(-cam_pygfx[2])
    pos_z = float(cam_pygfx[1])

    # Extract Euler angles from R = Rx(pitch) @ Ry(-yaw) @ Rz(roll).
    # R[0,2] = sin(-yaw)
    sb = float(np.clip(R[0, 2], -1.0, 1.0))
    neg_yaw = math.asin(sb)
    cb = math.cos(neg_yaw)

    if abs(cb) > 1e-6:
        pitch_rad = math.atan2(-R[1, 2], R[2, 2])
        roll_rad = math.atan2(-R[0, 1], R[0, 0])
    else:
        # Gimbal lock (yaw ≈ ±90°): set roll = 0.
        # pragma: no cover – physically requires a side-on camera
        # (yaw ≈ 90°), incompatible with the calibration overlay's
        # forward-facing assumption. Kept as a safety net.
        roll_rad = 0.0  # pragma: no cover
        pitch_rad = math.atan2(R[1, 0], R[1, 1])  # pragma: no cover

    yaw_deg = -math.degrees(neg_yaw)
    pitch_deg = math.degrees(pitch_rad)
    roll_deg = math.degrees(roll_rad)

    # Clamp to valid ranges
    pitch_deg = max(-89.0, min(89.0, pitch_deg))
    fov_deg = max(10.0, min(170.0, fov_deg))

    return CameraConfig(
        pos_x=round(pos_x, 4),
        pos_y=round(pos_y, 4),
        pos_z=round(pos_z, 4),
        pitch=round(pitch_deg, 2),
        yaw=round(yaw_deg, 2),
        roll=round(roll_deg, 2),
        fov=round(fov_deg, 2),
    )


def solve_camera_dlt(
    world_pts_psn: list[tuple[float, float, float]],
    screen_pts: list[tuple[float, float]],
    canvas_w: float,
    canvas_h: float,
) -> CameraConfig | None:
    """One-shot camera solve from 4 coplanar point correspondences.

    Computes the homography mapping grid-plane coordinates to screen
    pixels, then decomposes it into camera parameters. Always produces
    a result for non-degenerate inputs (no iterative solving).

    Parameters
    ----------
    world_pts_psn : list of 4 (x, y, z) tuples in PSN coords (all at same z).
    screen_pts : list of 4 (x, y) tuples in screen pixels (top-left origin).
    canvas_w, canvas_h : canvas size in logical pixels.

    Returns
    -------
    CameraConfig on success, None if decomposition yields invalid camera.
    """
    world = np.array(world_pts_psn, dtype=np.float64)
    screen = np.array(screen_pts, dtype=np.float64)

    z_offset = float(world[0, 2])
    plane_pts = world[:, :2]  # (psn_x, psn_y)

    H = compute_homography(plane_pts, screen)
    result = decompose_homography(H, canvas_w, canvas_h, z_offset)
    if result is None:
        return None

    # Post-decomposition reprojection check: reject if the solved camera
    # doesn't reproduce the input screen points within tolerance.
    params = np.array(
        [
            result.pos_x,
            result.pos_y,
            result.pos_z,
            result.pitch,
            result.yaw,
            result.roll,
            result.fov,
        ]
    )
    reproj = project_points(params, world, canvas_w, canvas_h)
    # Reject the mirror branch of the homography sign ambiguity: it yields a
    # finite camera that puts the world points behind the lens, so they
    # reproject to NaN. ``NaN > 20.0`` is False, so the residual must be
    # tested for finiteness before the magnitude threshold.
    residual = float(np.max(np.abs(reproj - screen)))
    if not math.isfinite(residual) or residual > 20.0:
        return None

    return result
