# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Shared marker catalog (id, name, color) with multicast sync.

The catalog is the project-wide source of truth for marker identity:
names and colors. Per-station selection (which markers a station
controls or views) stays in ``config.toml``; the catalog flows both
ways across the station group via UDP multicast.
"""

from openfollow.marker_catalog.catalog import (
    MarkerCatalog,
    MarkerEntry,
    load_catalog,
    save_catalog,
)
from openfollow.marker_catalog.station_name import derive_station_name

__all__ = [
    "MarkerCatalog",
    "MarkerEntry",
    "load_catalog",
    "save_catalog",
    "derive_station_name",
]
