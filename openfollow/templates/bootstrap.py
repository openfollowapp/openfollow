# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Seed on-disk system templates from bundled defaults on server start."""

from __future__ import annotations

import importlib.resources as resources
import logging
from pathlib import Path

from openfollow.templates.schema import TEMPLATE_FILE_SUFFIX

logger = logging.getLogger(__name__)


def seed_system_templates(templates_root: Path) -> int:
    """Mirror bundled system templates to <templates_root>/system/. Idempotent and prunes stale files."""
    target_dir = templates_root / "system"
    target_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    bundled_names: set[str] = set()
    package_files = resources.files("openfollow.templates.system")
    for entry in package_files.iterdir():
        # Filter to the template suffix so package metadata
        # (e.g. __init__.py) isn't written into the operator's folder.
        if not entry.name.endswith(TEMPLATE_FILE_SUFFIX):
            continue
        bundled_names.add(entry.name)
        # ``Traversable.read_text`` is the portable read API across
        # zipped wheels, regular installs, and source checkouts; keeps
        # working when the package is loaded from a zip.
        try:
            payload = entry.read_text(encoding="utf-8")
        # Bundled files are committed UTF-8 – a read failure means the
        # wheel install is corrupted.
        except (OSError, UnicodeDecodeError) as exc:  # pragma: no cover
            logger.warning(
                "could not read bundled system template %s: %s",
                entry.name,
                exc,
            )
            continue
        target = target_dir / entry.name
        try:
            target.write_text(payload, encoding="utf-8")
        # Write failures need a read-only mount / perm error at the call site.
        except OSError as exc:  # pragma: no cover
            logger.warning(
                "could not write system template %s: %s",
                target,
                exc.strerror or exc,
            )
            continue
        written += 1
    # Prune stale templates: on-disk system files no longer in the
    # bundled set. Only ``.openfollowtemplate`` files are touched;
    # foreign files an operator placed in ``system/`` are left alone.
    pruned = 0
    for path in target_dir.glob(f"*{TEMPLATE_FILE_SUFFIX}"):
        if path.name in bundled_names:
            continue
        try:
            path.unlink()
        # Same OS-level error class as the write path (read-only mount / perm).
        except OSError as exc:  # pragma: no cover
            logger.warning(
                "could not prune stale system template %s: %s",
                path,
                exc.strerror or exc,
            )
            continue
        pruned += 1
    logger.info(
        "seeded %d bundled system template(s) into %s (pruned %d stale)",
        written,
        target_dir,
        pruned,
    )
    return written
