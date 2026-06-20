# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Single canonical color palette for markers and zones.

One source of truth read by both the Python auto-seeding paths and the
web UI colour picker.

Two orderings over the same hex data:

- ``HUE_COLUMNS`` – display order. Five hue families × four
  brightness tiers; within each column rows are sorted dark → bright
  so the picker grid reads top-to-bottom by lightness.
- ``AUTO_PICK_ORDER`` – auto-seed order. 20-element permutation of the
  HUE_COLUMNS hexes; the first five slots span every hue family in the
  mid-dark tier for maximum cross-hue contrast on rigs with few
  markers, then subsequent five-slot bands fill the remaining
  brightness tiers. Greys are excluded.

``GREYS`` is a separate 5-stop row (white → black, perceptually
even sRGB) used by the picker's greys-only variant for crosshair
and grid line colour fields. Excluded from auto-pick.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

# Display: hue families (columns) × brightness tiers (rows, dark to bright).
HUE_COLUMNS: list[list[tuple[str, str]]] = [
    # Warm
    [
        ("#EA2027", "Cardinal Red"),
        ("#EE5A24", "Atomic Orange"),
        ("#F79F1F", "Amber"),
        ("#FFC312", "Sunflower"),
    ],
    # Green
    [
        ("#006266", "Pine"),
        ("#009432", "Emerald"),
        ("#A3CB38", "Apple Green"),
        ("#C4E538", "Electric Lime"),
    ],
    # Blue
    [
        ("#1B1464", "Ultramarine"),
        ("#0652DD", "Merchant Blue"),
        ("#1289A7", "Teal"),
        ("#12CBC4", "Lagoon"),
    ],
    # Purple
    [
        ("#5758BB", "Indigo"),
        ("#9980FA", "Lavender"),
        ("#D980FA", "Orchid"),
        ("#FDA7DF", "Cotton Candy"),
    ],
    # Pink
    [
        ("#6F1E51", "Plum"),
        ("#833471", "Mulberry"),
        ("#B53471", "Raspberry"),
        ("#ED4C67", "Coral"),
    ],
]

# Grey scale (excluded from auto-pick, greys-only picker variant).
GREYS: list[tuple[str, str]] = [
    ("#FFFFFF", "White"),
    ("#C8C8C8", "Light Grey"),
    ("#939393", "Mid Grey"),
    ("#5E5E5E", "Dark Grey"),
    ("#000000", "Black"),
]

# Auto-seed order: first 5 span hue families in mid-dark, then light, dark, bright.
AUTO_PICK_ORDER: list[str] = [
    # Slots 0–4: row 1, all hue columns
    "#EE5A24",
    "#009432",
    "#0652DD",
    "#9980FA",
    "#833471",
    # Slots 5–9 – row 2
    "#F79F1F",
    "#A3CB38",
    "#1289A7",
    "#D980FA",
    "#B53471",
    # Slots 10–14 – row 0
    "#EA2027",
    "#006266",
    "#1B1464",
    "#5758BB",
    "#6F1E51",
    # Slots 15–19 – row 3
    "#FFC312",
    "#C4E538",
    "#12CBC4",
    "#FDA7DF",
    "#ED4C67",
]

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _norm(hex_value: str) -> str:
    """Uppercase 7-char form for case-insensitive comparison."""
    return hex_value.strip().upper()


# Fixed palette dimensions – a contract the web layer depends on (picker
# grid CSS hardcodes 5 hue columns, JS iterates 4 brightness rows, greys
# ramp is a fixed 5-stop scale). The validator enforces them at import.
_HUE_COLUMN_COUNT = 5
_HUE_ROW_COUNT = 4
_GREY_COUNT = 5


# Self-check at import: fail loud on malformed palette data.
def _validate(
    hue_columns: list[list[tuple[str, str]]],
    greys: list[tuple[str, str]],
    auto_pick_order: list[str],
) -> None:
    # Check grid shape first.
    if len(hue_columns) != _HUE_COLUMN_COUNT:
        raise ValueError(f"palette: HUE_COLUMNS must have {_HUE_COLUMN_COUNT} hue columns")
    for column in hue_columns:
        if len(column) != _HUE_ROW_COUNT:
            raise ValueError(f"palette: each hue column must have {_HUE_ROW_COUNT} rows")
    if len(greys) != _GREY_COUNT:
        raise ValueError(f"palette: GREYS must have {_GREY_COUNT} stops")

    column_hexes: list[str] = []
    for column in hue_columns:
        for hex_value, name in column:
            if not _HEX_RE.match(hex_value):
                raise ValueError(f"palette: invalid hex {hex_value!r}")
            if not name:
                raise ValueError(f"palette: empty name for {hex_value!r}")
            column_hexes.append(_norm(hex_value))
    grey_hexes: list[str] = []
    for hex_value, name in greys:
        if not _HEX_RE.match(hex_value):
            raise ValueError(f"palette: invalid grey hex {hex_value!r}")
        if not name:
            raise ValueError(f"palette: empty grey name for {hex_value!r}")
        grey_hexes.append(_norm(hex_value))
    column_set = set(column_hexes)
    if len(column_set) != len(column_hexes):
        raise ValueError("palette: duplicate hex across HUE_COLUMNS")
    # GREYS must be internally unique AND disjoint from the hue hexes –
    # otherwise the shared ``_NAME_BY_HEX`` index silently overwrites an
    # entry and ``name_for`` returns whichever name was indexed last.
    grey_set = set(grey_hexes)
    if len(grey_set) != len(grey_hexes):
        raise ValueError("palette: duplicate hex within GREYS")
    if grey_set & column_set:
        raise ValueError("palette: GREYS hex collides with a HUE_COLUMNS hex")
    # AUTO_PICK_ORDER must be a true permutation (valid format, no dups, exact set).
    auto_norm: list[str] = []
    for hex_value in auto_pick_order:
        if not _HEX_RE.match(hex_value):
            raise ValueError(f"palette: invalid auto-pick hex {hex_value!r}")
        auto_norm.append(_norm(hex_value))
    auto_set = set(auto_norm)
    if len(auto_set) != len(auto_norm):
        raise ValueError("palette: duplicate hex within AUTO_PICK_ORDER")
    if auto_set != column_set:
        raise ValueError("palette: AUTO_PICK_ORDER is not a permutation of HUE_COLUMNS hexes")


# Validate at import to catch malformed data early.
_validate(HUE_COLUMNS, GREYS, AUTO_PICK_ORDER)


# Build hex→name index for both hue and grey palettes.
_NAME_BY_HEX: dict[str, str] = {}
for _column in HUE_COLUMNS:
    for _hex, _name in _column:
        _NAME_BY_HEX[_norm(_hex)] = _name
for _hex, _name in GREYS:
    _NAME_BY_HEX[_norm(_hex)] = _name

# Precomputed normalized auto-pick order.
_AUTO_PICK_NORM: list[str] = [_norm(h) for h in AUTO_PICK_ORDER]


def name_for(hex_value: str) -> str | None:
    """Return palette name for hex_value, or None if not a member."""
    return _NAME_BY_HEX.get(_norm(hex_value))


def next_unused_color(used: Iterable[str | None]) -> str:
    """Return first AUTO_PICK_ORDER entry not in used. Case-insensitive."""
    used_set = {norm for norm in (_norm(c) for c in used if c) if norm}
    for index, norm in enumerate(_AUTO_PICK_NORM):
        if norm not in used_set:
            return AUTO_PICK_ORDER[index]
    return AUTO_PICK_ORDER[len(used_set) % len(AUTO_PICK_ORDER)]


def web_palette_dict() -> dict[str, Any]:
    """Return palette as plain dict for web layer template embedding."""
    return {
        "hue_columns": [[{"hex": h, "name": n} for h, n in column] for column in HUE_COLUMNS],
        "greys": [{"hex": h, "name": n} for h, n in GREYS],
        "auto_pick_order": list(AUTO_PICK_ORDER),
    }


def web_palette_json() -> str:
    """Return palette as JSON string for template embedding."""
    return json.dumps(web_palette_dict(), separators=(",", ":"))
