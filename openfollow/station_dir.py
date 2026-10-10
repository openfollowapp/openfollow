# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The directory a station keeps its device-local files in.

It is the directory of the config file the station runs from, never the working
directory: an app opened from the Finder starts in the read-only ``/``.
"""

from __future__ import annotations

import os
from pathlib import Path

_config_dir: Path | None = None


def set_config_path(config_path: str) -> None:
    """Anchor the device-local stores to the directory holding *config_path*."""
    global _config_dir
    _config_dir = Path(os.path.abspath(config_path)).parent


def station_dir() -> Path:
    """The config file's directory; until one is set, the default ``config.toml``'s (the working directory)."""
    return _config_dir if _config_dir is not None else Path.cwd()
