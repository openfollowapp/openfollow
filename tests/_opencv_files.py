# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""OpenCV ``FileStorage`` calibration files, written the way calibration tools write them."""

from __future__ import annotations


def opencv_matrix(name: str, rows: int, cols: int, values: list[float], *, kind: str = "opencv-matrix") -> str:
    data = " ".join(repr(float(v)) for v in values)
    return f'<{name} type_id="{kind}"><rows>{rows}</rows><cols>{cols}</cols><dt>d</dt><data>{data}</data></{name}>'


def opencv_xml(
    f: float = 1500.0,
    c: tuple[float, float] = (959.5, 539.5),
    dist: list[float] | None = None,
    *,
    fy: float | None = None,
    fisheye: str | None = None,
    size: tuple[int, int] | None = (1920, 1080),
    size_keys: str = "resolution",
    offset: list[float] | None = None,
    matrix_key: str = "cameraMatrix",
    dist_key: str = "dist_coeffs",
    extra: str = "",
) -> str:
    """An OpenCV ``FileStorage`` file as calibration tools write it."""
    parts = [
        '<?xml version="1.0"?>',
        "<opencv_storage>",
        '<calibrationDate>"Mon Jan  5 10:00:00 2026"</calibrationDate>',
    ]
    if fisheye is not None:
        parts.append(f"<isFisheye>{fisheye}</isFisheye>")
    if size and size_keys == "resolution":
        parts.append(f"<cameraResolution>\n  {size[0]} {size[1]}</cameraResolution>")
    elif size:
        parts.append(f"<image_width>{size[0]}</image_width><image_height>{size[1]}</image_height>")
    parts.append(opencv_matrix(matrix_key, 3, 3, [f, 0, c[0], 0, fy if fy is not None else f, c[1], 0, 0, 1]))
    values = [-0.1, 0.02, 0.0, 0.0, 0.0] if dist is None else dist
    parts.append(opencv_matrix(dist_key, 1, len(values), values))
    if offset is not None:
        parts.append(opencv_matrix("cxcy_offset", 1, 2, offset))
    parts.append(extra)
    parts.append("<avg_reprojection_error>0.31</avg_reprojection_error></opencv_storage>")
    return "\n".join(parts)
