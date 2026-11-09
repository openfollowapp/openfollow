# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""The settings Restore defaults returns to: the macOS app's first-run seed, else the defaults."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from openfollow.configuration import AppConfig, ConfigFileUnreadableError, read_config_whole

logger = logging.getLogger(__name__)

# The macOS bundle's launcher seeds a first run's config.toml from this file.
SEED_CONFIG_NAME = "config.seed.toml"


def bundled_seed() -> Path | None:
    """The seed PyInstaller unpacked beside the frozen app, ``None`` outside the bundle."""
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    seed = Path(base) / SEED_CONFIG_NAME
    return seed if seed.is_file() else None


def first_run_config() -> AppConfig:
    """A new config holding the macOS app's first-run seed, or ``AppConfig()`` outside the bundle."""
    seed = bundled_seed()
    if seed is None:
        return AppConfig()
    try:
        seeded = read_config_whole(str(seed))
    except ConfigFileUnreadableError as exc:
        logger.warning("First-run settings unreadable (%s); using the built-in defaults.", exc)
        return AppConfig()
    return seeded if seeded is not None else AppConfig()
