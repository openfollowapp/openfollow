# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Seed pre-shipped detection models into the per-device storage folder.

The Pi image, the ``.deb``, and the macOS app bundle a set of YOLO quality-tier
models. On startup they are copied into the resolved detection ``models/``
folder so detection works offline out of the box. Existing files are never
overwritten, so an operator's downloads / edits survive across launches.

Stdlib-only so the macOS bundle launcher can call it without importing the
GStreamer-heavy ``openfollow.video`` package.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Where the ``.deb`` / image install lay down the bundled tier models.
_PACKAGED_MODELS_DIR = Path("/usr/share/openfollow/models")


def bundled_models_dir() -> Path | None:
    """Resolve this install's bundled-models directory, or ``None``.

    Returns the documented packaged location when present (the ``.deb`` /
    Pi-image layout); ``None`` for a plain source checkout, which ships no
    bundled models. The macOS bundle resolves its own path from ``sys._MEIPASS``
    in the launcher and does not use this.
    """
    if _PACKAGED_MODELS_DIR.is_dir():
        return _PACKAGED_MODELS_DIR
    return None


def seed_bundled_models(source_dir: Path, storage_models_dir: Path) -> list[str]:
    """Copy any missing ``.onnx`` from ``source_dir`` into ``storage_models_dir``.

    Returns the filenames newly copied. A missing ``source_dir`` (a source
    checkout with no bundled models) is a no-op. Existing destination files are
    left untouched so operator downloads / edits survive.
    """
    if not source_dir.is_dir():
        return []
    seeded: list[str] = []
    try:
        storage_models_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning("Could not create detection models folder %s", storage_models_dir, exc_info=True)
        return []
    for src in sorted(source_dir.glob("*.onnx")):
        dst = storage_models_dir / src.name
        if dst.exists():
            continue
        try:
            shutil.copyfile(src, dst)
        except OSError:
            logger.warning("Failed to seed bundled model %s", src.name, exc_info=True)
            continue
        seeded.append(src.name)
    if seeded:
        logger.info("Seeded %d bundled detection model(s): %s", len(seeded), ", ".join(seeded))
    return seeded
