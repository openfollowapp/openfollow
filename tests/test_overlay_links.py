# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for :mod:`openfollow.runtime.overlay_links`.

The symbols are checked-in data, not something the station encodes, so what
these tests defend is the data staying a well-formed QR and the drawing giving
it the white field and quiet zone a scanner needs. ``scripts/gen_link_qr.py``
is what proves a symbol still decodes to its URL; it needs OpenCV, which the
runtime has only with the detection extra.
"""

from __future__ import annotations

import pytest

from openfollow.runtime.overlay_links import DISCORD, DOCS, LINKS, QUIET_MODULES, draw_link_qr
from tests._fake_cairo import FakeCairo

pytestmark = pytest.mark.unit

_FINDER = (
    "#######",
    "#.....#",
    "#.###.#",
    "#.###.#",
    "#.###.#",
    "#.....#",
    "#######",
)


@pytest.mark.parametrize("code", LINKS, ids=lambda c: c.url)
class TestSymbolData:
    def test_is_a_square_grid_of_modules(self, code) -> None:
        size = len(code.symbol)
        assert all(len(row) == size for row in code.symbol)
        assert set("".join(code.symbol)) <= {"#", "."}

    def test_size_is_a_real_qr_version(self, code) -> None:
        """A QR side is ``17 + 4 * version`` modules; anything else is corrupt."""
        version, remainder = divmod(len(code.symbol) - 17, 4)
        assert remainder == 0
        assert 1 <= version <= 40

    @pytest.mark.parametrize("corner", ["top-left", "top-right", "bottom-left"])
    def test_carries_its_finder_patterns(self, code, corner: str) -> None:
        """The three position markers are what a scanner locks onto."""
        size = len(code.symbol)
        row0 = 0 if corner != "bottom-left" else size - 7
        col0 = 0 if corner != "top-right" else size - 7
        found = tuple(row[col0 : col0 + 7] for row in code.symbol[row0 : row0 + 7])
        assert found == _FINDER

    def test_caption_names_the_destination(self, code) -> None:
        host = code.url.split("//", 1)[1].split("/", 1)[0]
        assert host.split(".")[-2] in " ".join(code.lines).lower()


class TestPayloads:
    """Pinned: the printed captions and the addresses they promise."""

    def test_docs_url(self) -> None:
        assert DOCS.url == "https://openfollow.app/docs"

    def test_discord_url(self) -> None:
        assert DISCORD.url == "https://discord.gg/vbhtjP2mtT"


class TestDrawing:
    def test_draws_every_dark_module_and_nothing_else(self) -> None:
        cr = FakeCairo()
        draw_link_qr(cr, DOCS, 0.0, 0.0, 330.0)
        span = len(DOCS.symbol) + 2 * QUIET_MODULES
        module = 330.0 / span
        expected = sorted(
            (round(QUIET_MODULES * module + col * module, 6), round(QUIET_MODULES * module + row * module, 6))
            for row, bits in enumerate(DOCS.symbol)
            for col, bit in enumerate(bits)
            if bit == "#"
        )
        drawn = [r for r in cr.rects if r[2] == pytest.approx(module)]
        assert len(drawn) == sum(row.count("#") for row in DOCS.symbol)
        assert sorted((round(r[0], 6), round(r[1], 6)) for r in drawn) == expected

    def test_the_quiet_zone_is_inside_the_field(self) -> None:
        """Modules start four modules in on every side, so the margin a scanner
        needs is part of the drawn code rather than borrowed from the panel."""
        cr = FakeCairo()
        size = 330.0
        draw_link_qr(cr, DISCORD, 10.0, 20.0, size)
        module = size / (len(DISCORD.symbol) + 2 * QUIET_MODULES)
        modules = [r for r in cr.rects if r[2] == pytest.approx(module)]
        assert min(r[0] for r in modules) == pytest.approx(10.0 + QUIET_MODULES * module)
        assert min(r[1] for r in modules) == pytest.approx(20.0 + QUIET_MODULES * module)
        assert max(r[0] for r in modules) + module <= 10.0 + size - QUIET_MODULES * module + 1e-6
        assert max(r[1] for r in modules) + module <= 20.0 + size - QUIET_MODULES * module + 1e-6

    def test_the_field_is_painted_white_before_the_modules(self) -> None:
        """A code drawn straight onto the dark panel has no light modules."""
        cr = FakeCairo()
        draw_link_qr(cr, DOCS, 0.0, 0.0, 200.0)
        colours = [c for c in cr.calls if c[0] in {"rgb", "rgba"}]
        assert colours[0] == ("rgb", 1.0, 1.0, 1.0)
        assert ("rgb", 0.0, 0.0, 0.0) in colours

    @pytest.mark.parametrize("size", [80.0, 200.0])
    def test_the_corner_radius_never_eats_the_quiet_zone(self, size: float) -> None:
        """Rounding removes white at the corners. Past the quiet zone that is
        the scanner's margin going, which looks fine and does not scan."""
        cr = FakeCairo()
        draw_link_qr(cr, DOCS, 0.0, 0.0, size, radius=999.0)
        module = size / (len(DOCS.symbol) + 2 * QUIET_MODULES)
        assert max(a[2] for a in cr.arcs) <= QUIET_MODULES * module + 1e-6
