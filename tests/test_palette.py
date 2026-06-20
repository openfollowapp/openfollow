# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Canonical palette invariants + auto-pick semantics.

The palette is the single source of truth for marker / zone colours
across the Python seeding paths and the web picker. These
tests pin the data shape, the import-time self-check's effective
output, and the case-insensitive auto-pick walk so a typo in
``palette.py`` can't ship colours that diverge from what the UI
displays or rotate the auto-pick order under callers.
"""

from __future__ import annotations

import copy
import re

import pytest

from openfollow import palette

pytestmark = pytest.mark.unit


class TestPaletteShape:
    """The static palette has a fixed shape – 5×4 hue grid plus a
    5-stop grey row, and the auto-pick sequence is a permutation of
    the hue grid's 20 hexes."""

    def test_hue_columns_are_5_by_4(self) -> None:
        assert len(palette.HUE_COLUMNS) == 5
        for column in palette.HUE_COLUMNS:
            assert len(column) == 4

    def test_greys_has_5_stops(self) -> None:
        assert len(palette.GREYS) == 5

    def test_auto_pick_order_has_20_entries(self) -> None:
        assert len(palette.AUTO_PICK_ORDER) == 20

    def test_all_hexes_are_7_char_hash_form(self) -> None:
        hex_re = re.compile(r"^#[0-9A-Fa-f]{6}$")
        for column in palette.HUE_COLUMNS:
            for hex_value, name in column:
                assert hex_re.match(hex_value), hex_value
                assert name, hex_value
        for hex_value, name in palette.GREYS:
            assert hex_re.match(hex_value), hex_value
            assert name, hex_value
        for hex_value in palette.AUTO_PICK_ORDER:
            assert hex_re.match(hex_value), hex_value

    def test_auto_pick_is_permutation_of_hue_columns(self) -> None:
        """Pin the import-time validator's effective output so a
        future edit can't quietly leave a hue out of the auto-pick
        order (or vice versa). Comparison is uppercase since the
        validator normalizes case."""
        column_hexes = {h.upper() for column in palette.HUE_COLUMNS for h, _name in column}
        auto_hexes = {h.upper() for h in palette.AUTO_PICK_ORDER}
        assert column_hexes == auto_hexes

    def test_greys_excluded_from_auto_pick(self) -> None:
        """Greys never auto-seed – they're picker-only for crosshair
        and grid line colours."""
        grey_hexes = {h.upper() for h, _name in palette.GREYS}
        auto_hexes = {h.upper() for h in palette.AUTO_PICK_ORDER}
        assert grey_hexes.isdisjoint(auto_hexes)


class TestAutoPickSlotOrder:
    """The first five auto-pick slots span all five hue columns at
    row 1 (mid-dark). This guarantees that two markers always sit on
    different hues – the whole reason the order is hand-tuned. Pin
    the canonical hexes for the first band so a re-tune can't quietly
    collapse contrast on rigs with few markers."""

    def test_slots_0_through_4_cover_row_1_across_all_columns(self) -> None:
        expected = [column[1][0] for column in palette.HUE_COLUMNS]
        assert palette.AUTO_PICK_ORDER[:5] == expected


class TestNameLookup:
    def test_known_hex_returns_palette_name(self) -> None:
        assert palette.name_for("#EE5A24") == "Atomic Orange"
        assert palette.name_for("#FFFFFF") == "White"

    def test_case_insensitive(self) -> None:
        assert palette.name_for("#ee5a24") == "Atomic Orange"
        assert palette.name_for("#fff") is None  # 3-char form rejected
        assert palette.name_for("#EE5A24 ") == "Atomic Orange"  # trim

    def test_unknown_hex_returns_none(self) -> None:
        assert palette.name_for("#123456") is None
        assert palette.name_for("") is None


class TestNextUnusedColor:
    def test_empty_used_returns_first_slot(self) -> None:
        assert palette.next_unused_color([]) == palette.AUTO_PICK_ORDER[0]

    def test_skips_used_returns_next_slot(self) -> None:
        used = [palette.AUTO_PICK_ORDER[0]]
        assert palette.next_unused_color(used) == palette.AUTO_PICK_ORDER[1]

    def test_case_insensitive_match(self) -> None:
        used = [palette.AUTO_PICK_ORDER[0].lower()]
        assert palette.next_unused_color(used) == palette.AUTO_PICK_ORDER[1]

    def test_skips_first_few_used(self) -> None:
        used = palette.AUTO_PICK_ORDER[:3]
        assert palette.next_unused_color(used) == palette.AUTO_PICK_ORDER[3]

    def test_all_palette_used_falls_back_to_positional_pick(self) -> None:
        """Every auto-pick slot is taken → positional fallback so the
        caller still gets a usable hex (we prefer a duplicate over an
        empty / None return that would have to be guarded everywhere)."""
        used = list(palette.AUTO_PICK_ORDER)
        result = palette.next_unused_color(used)
        assert result in palette.AUTO_PICK_ORDER

    def test_ignores_empty_strings_in_used(self) -> None:
        """A catalog row whose colour is ``""`` (legacy migrations
        ship that occasionally) must not consume the first slot."""
        assert palette.next_unused_color(["", None]) == palette.AUTO_PICK_ORDER[0]

    def test_whitespace_only_does_not_skew_fallback(self) -> None:
        """A whitespace-only entry normalizes to ``""`` and must be
        dropped from ``used_set`` – otherwise it inflates the count and
        shifts the positional fallback when the palette is exhausted.
        With every real colour used plus a junk ``"   "``, the fallback
        index must still be ``len(palette) % len`` (== 0), not off-by-one.
        """
        used = list(palette.AUTO_PICK_ORDER) + ["   "]
        result = palette.next_unused_color(used)
        assert result == palette.AUTO_PICK_ORDER[len(palette.AUTO_PICK_ORDER) % len(palette.AUTO_PICK_ORDER)]


class TestValidatorRaises:
    """``_validate`` is the import-time guard against malformed palette
    data. The production constants are valid so its raise branches
    never fire at runtime – we exercise them directly here so the
    contract is locked: a future palette edit that breaks the grid
    shape, ships a malformed hex, or skews AUTO_PICK_ORDER must
    trigger a load-time failure rather than silently corrupting
    downstream callers.

    Each negative case starts from a deep copy of the *real* palette
    (shape-valid: 5×4 hues, 5 greys, 20 auto) and corrupts exactly one
    thing, so the test isolates the targeted invariant instead of
    tripping the shape guards on a too-small fixture.
    """

    def _valid_args(
        self,
    ) -> tuple[list[list[tuple[str, str]]], list[tuple[str, str]], list[str]]:
        return (
            copy.deepcopy(palette.HUE_COLUMNS),
            copy.deepcopy(palette.GREYS),
            list(palette.AUTO_PICK_ORDER),
        )

    def test_wrong_hue_column_count_raises(self) -> None:
        hue, greys, auto = self._valid_args()
        hue.pop()  # 4 columns
        with pytest.raises(ValueError, match="5 hue columns"):
            palette._validate(hue, greys, auto)

    def test_wrong_hue_row_count_raises(self) -> None:
        hue, greys, auto = self._valid_args()
        hue[0].pop()  # 3 rows in column 0
        with pytest.raises(ValueError, match="4 rows"):
            palette._validate(hue, greys, auto)

    def test_wrong_grey_count_raises(self) -> None:
        hue, greys, auto = self._valid_args()
        greys.pop()  # 4 greys
        with pytest.raises(ValueError, match="5 stops"):
            palette._validate(hue, greys, auto)

    def test_invalid_hue_hex_raises(self) -> None:
        hue, greys, auto = self._valid_args()
        hue[0][0] = ("not-a-hex", "Red")
        with pytest.raises(ValueError, match="invalid hex"):
            palette._validate(hue, greys, auto)

    def test_empty_hue_name_raises(self) -> None:
        hue, greys, auto = self._valid_args()
        hue[0][0] = (hue[0][0][0], "")
        with pytest.raises(ValueError, match="empty name"):
            palette._validate(hue, greys, auto)

    def test_invalid_grey_hex_raises(self) -> None:
        hue, greys, auto = self._valid_args()
        greys[0] = ("not-a-hex", "Grey")
        with pytest.raises(ValueError, match="invalid grey hex"):
            palette._validate(hue, greys, auto)

    def test_empty_grey_name_raises(self) -> None:
        hue, greys, auto = self._valid_args()
        greys[0] = (greys[0][0], "")
        with pytest.raises(ValueError, match="empty grey name"):
            palette._validate(hue, greys, auto)

    def test_duplicate_hue_hex_raises(self) -> None:
        hue, greys, auto = self._valid_args()
        # Make column 0 row 1 collide with column 0 row 0.
        hue[0][1] = (hue[0][0][0], "AlsoRed")
        with pytest.raises(ValueError, match="duplicate hex across HUE_COLUMNS"):
            palette._validate(hue, greys, auto)

    def test_duplicate_grey_hex_raises(self) -> None:
        """A repeated grey would silently overwrite ``_NAME_BY_HEX``."""
        hue, greys, auto = self._valid_args()
        greys[1] = (greys[0][0], "AlsoWhite")
        with pytest.raises(ValueError, match="duplicate hex within GREYS"):
            palette._validate(hue, greys, auto)

    def test_grey_colliding_with_hue_raises(self) -> None:
        """A grey sharing a hue hex makes ``name_for`` ambiguous –
        whichever was indexed into ``_NAME_BY_HEX`` last wins."""
        hue, greys, auto = self._valid_args()
        greys[0] = (hue[0][0][0], "NotReallyGrey")
        with pytest.raises(ValueError, match="collides with a HUE_COLUMNS hex"):
            palette._validate(hue, greys, auto)

    def test_invalid_auto_pick_hex_raises(self) -> None:
        hue, greys, auto = self._valid_args()
        auto[0] = "not-a-hex"
        with pytest.raises(ValueError, match="invalid auto-pick hex"):
            palette._validate(hue, greys, auto)

    def test_duplicate_auto_pick_hex_raises(self) -> None:
        hue, greys, auto = self._valid_args()
        auto.append(auto[0])  # 21 entries, one duplicated
        with pytest.raises(ValueError, match="duplicate hex within AUTO_PICK_ORDER"):
            palette._validate(hue, greys, auto)

    def test_auto_pick_not_a_permutation_raises(self) -> None:
        hue, greys, auto = self._valid_args()
        auto[0] = "#123456"  # valid hex, not a hue
        with pytest.raises(ValueError, match="not a permutation"):
            palette._validate(hue, greys, auto)


class TestWebPaletteSerialization:
    """web_palette_dict / web_palette_json seed window.OPENFOLLOW_PALETTE.
    Ensures picker contract remains stable across palette reworks."""

    def test_dict_has_three_top_level_keys(self) -> None:
        d = palette.web_palette_dict()
        assert set(d.keys()) == {"hue_columns", "greys", "auto_pick_order"}

    def test_hue_columns_shape_matches_python_layout(self) -> None:
        d = palette.web_palette_dict()
        assert len(d["hue_columns"]) == 5
        for column in d["hue_columns"]:
            assert len(column) == 4
            for entry in column:
                assert set(entry.keys()) == {"hex", "name"}
                assert entry["hex"].startswith("#")
                assert entry["name"]

    def test_greys_shape(self) -> None:
        d = palette.web_palette_dict()
        assert len(d["greys"]) == 5
        for entry in d["greys"]:
            assert set(entry.keys()) == {"hex", "name"}

    def test_auto_pick_order_is_plain_string_list(self) -> None:
        d = palette.web_palette_dict()
        assert len(d["auto_pick_order"]) == 20
        for hex_value in d["auto_pick_order"]:
            assert isinstance(hex_value, str)
            assert hex_value.startswith("#")

    def test_json_round_trips(self) -> None:
        """``web_palette_json`` returns a valid JSON string that decodes
        back to the same dict the Python helper produces. Pin so a
        future serialization tweak (sort_keys, separators) can't
        silently change wire shape."""
        import json

        decoded = json.loads(palette.web_palette_json())
        assert decoded == palette.web_palette_dict()

    def test_json_is_compact_no_indentation(self) -> None:
        """Embedded inline in base.tpl, so we want compact JSON
        (no pretty-print whitespace) – keeps the rendered HTML small
        and makes the diff readable."""
        s = palette.web_palette_json()
        assert "\n" not in s
        assert ": " not in s  # compact separators
