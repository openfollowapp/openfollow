# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""Lens calibrations saved on this station by the Setup Wizard, one small JSON file each.

They live beside ``config.toml``, not in it, so an export, an import or a push never carries them. Each
file records the OpenFollow version that wrote it. Saving a name that is already there, ignoring case,
rewrites that file.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import math
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openfollow.lens_model import lens_warp_is_valid
from openfollow.restore_hold import unless_held

logger = logging.getLogger(__name__)

DIR_NAME = "lens-calibrations"
MAX_NAME = 60
# The widest snapshot side the wizard takes: an 8K frame, twice over.
MAX_SIDE = 16384
_SUFFIX = ".json"

# The files last reported unreadable, so a listing says each once.
_reported: set[str] = set()


@dataclass(frozen=True)
class LensCalibration:
    name: str
    k1: float
    k2: float
    cx: float  # the lens centre, half-diagonals from the frame centre (openfollow.lens_model)
    cy: float
    saved: str  # ISO date
    openfollow_version: str
    width: int | None = None  # the snapshot it was measured on; None when unknown
    height: int | None = None


def calibrations_dir(config_path: str | os.PathLike[str]) -> Path:
    return Path(config_path).parent / DIR_NAME


def name_problem(name: Any) -> str:
    """Why ``name`` cannot name a calibration, or ``""``."""
    if not isinstance(name, str) or not name.strip():
        return "Enter a name."
    if len(name.strip()) > MAX_NAME:
        return f"A name has at most {MAX_NAME} characters."
    if any(ord(c) < 0x20 or c == "\x7f" for c in name):
        return "A name cannot hold control characters."
    if any(0xD800 <= ord(c) <= 0xDFFF for c in name):  # a lone surrogate from a JSON escape: no UTF-8 for it
        return "A name cannot hold unpaired surrogates."
    return ""


def lens_problem(k1: Any, k2: Any, cx: Any, cy: Any) -> str:
    """Why ``k1`` / ``k2`` / ``cx`` / ``cy`` are no lens to save, or ``""``."""
    if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in (k1, k2, cx, cy)):
        return "The lens values must be numbers."
    try:
        lens = (float(k1), float(k2), float(cx), float(cy))
    except OverflowError:  # a JSON integer of hundreds of digits
        return "The lens values must be numbers."
    if not all(math.isfinite(v) for v in lens):
        return "The lens values must be numbers."
    # The centre's bound is part of the rule.
    if not lens_warp_is_valid(*lens):
        return "These lens values cannot follow the picture out to its corners."
    return ""


def size_problem(width: Any, height: Any) -> str:
    """Why ``width`` / ``height`` are no snapshot size, or ``""``."""
    if not all(isinstance(v, int) and not isinstance(v, bool) and 0 < v <= MAX_SIDE for v in (width, height)):
        return f"The picture size must be two whole numbers from 1 to {MAX_SIDE}."
    return ""


def _file_name(name: str) -> str:
    """A safe file name for ``name``: a readable slug plus a digest of the name ignoring case."""
    key = name.strip().casefold()
    slug = re.sub(r"[^a-z0-9]+", "-", key).strip("-")[:40] or "lens"
    return f"{slug}-{hashlib.sha256(key.encode()).hexdigest()[:10]}{_SUFFIX}"


def _read(path: Path) -> LensCalibration | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        texts = [data["name"], data.get("saved", ""), data.get("openfollow_version", "")]
        if not all(isinstance(t, str) for t in texts):
            raise TypeError("a text field is not text")
        calibration = LensCalibration(texts[0].strip(), data["k1"], data["k2"], data["cx"], data["cy"], *texts[1:])
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        calibration = None
    if (
        calibration is None
        or name_problem(calibration.name)
        or lens_problem(calibration.k1, calibration.k2, calibration.cx, calibration.cy)
    ):
        if path.name not in _reported:
            _reported.add(path.name)
            logger.warning("lens calibration %s does not read as one; left out", path.name)
        return None
    # A missing or broken size only drops the size, never the calibration.
    width, height = data.get("width"), data.get("height")
    if size_problem(width, height):
        width = height = None
    return LensCalibration(
        calibration.name,
        float(calibration.k1),
        float(calibration.k2),
        float(calibration.cx),
        float(calibration.cy),
        calibration.saved,
        calibration.openfollow_version,
        width,
        height,
    )


def list_calibrations(directory: Path) -> list[LensCalibration]:
    """Every readable calibration, newest saved first, then by name; a file that does not read is left out."""
    if not directory.is_dir():
        return []
    found = [c for c in (_read(p) for p in sorted(directory.glob(f"*{_SUFFIX}"))) if c is not None]
    found.sort(key=lambda c: c.name.casefold())
    found.sort(key=lambda c: c.saved, reverse=True)
    return found


def save_calibration(
    directory: Path,
    name: str,
    k1: float,
    k2: float,
    cx: float,
    cy: float,
    version: str,
    today: datetime.date,
    *,
    width: int | None = None,
    height: int | None = None,
) -> LensCalibration:
    """Write ``name`` with the lens, the version and the snapshot size, if known; one already saved under that
    name, ignoring case, is replaced.

    The caller checks :func:`name_problem`, :func:`lens_problem` and :func:`size_problem` first; a bad one
    raises ValueError here. A size is both sides or neither.
    """
    sized = width is not None or height is not None
    problem = name_problem(name) or lens_problem(k1, k2, cx, cy) or (size_problem(width, height) if sized else "")
    if problem:
        raise ValueError(problem)
    name = name.strip()
    lens = (float(k1), float(k2), float(cx), float(cy))
    calibration = LensCalibration(name, *lens, today.isoformat(), version, width, height)
    directory.mkdir(parents=True, exist_ok=True)
    same = [
        p for p in directory.glob(f"*{_SUFFIX}") if (c := _read(p)) is not None and c.name.casefold() == name.casefold()
    ]
    target = directory / _file_name(name)
    body = json.dumps(
        {
            "name": calibration.name,
            "k1": calibration.k1,
            "k2": calibration.k2,
            "cx": calibration.cx,
            "cy": calibration.cy,
            "saved": calibration.saved,
            "width": calibration.width,
            "height": calibration.height,
            "openfollow_version": calibration.openfollow_version,
        },
        indent=2,
    )
    with unless_held(directory):
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".lens-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(body + "\n")
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        for old in same:
            if old != target:
                old.unlink(missing_ok=True)
    return calibration
