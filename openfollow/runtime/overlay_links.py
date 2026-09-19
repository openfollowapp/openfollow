# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The links shown on the Settings screen, each with its QR symbol.

The symbols are stored rather than generated: encoding one needs a library the
runtime does not otherwise want, and the payloads rarely change. Regenerate
with ``scripts/gen_link_qr.py`` after editing a URL.

Rows are the bare symbol, no quiet zone - ``draw_link_qr`` adds the four
modules the spec requires, so the margin cannot be lost by a caller drawing the
code against a dark panel.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openfollow.runtime.overlay_draw_style import draw_rounded_rect

QUIET_MODULES = 4


@dataclass(frozen=True)
class LinkCode:
    """A URL an operator cannot type at the station, and the lines naming it.

    The caption is broken where it reads best rather than wherever a greedy
    wrap happens to land, so it is authored a line at a time.
    """

    url: str
    lines: tuple[str, ...]
    symbol: tuple[str, ...]


DOCS = LinkCode(
    url="https://openfollow.app/docs",
    lines=(
        "Visit openfollow.app/docs for full documentation,",
        "manuals and troubleshooting",
    ),
    symbol=(
        "#######.#..###....#######",
        "#.....#..##.....#.#.....#",
        "#.###.#...#.###...#.###.#",
        "#.###.#.....#####.#.###.#",
        "#.###.#..#.#.##.#.#.###.#",
        "#.....#...#.###.#.#.....#",
        "#######.#.#.#.#.#.#######",
        "........#.##.##.#........",
        "##.##.#..####.#.#.#.....#",
        "##.#.....#..#.##.#.#####.",
        "..#...##..##..##.###.#..#",
        "#........##.#.#..########",
        ".###.#####.#....#.##....#",
        "###.#......#.#.##...#..#.",
        "####.######..#.#.##.#####",
        "#.##...#...##.##.###.##.#",
        "##.##.########..#####.##.",
        "........#.#...#.#...#.##.",
        "#######..#.#.##.#.#.#...#",
        "#.....#..#.#.####...#..#.",
        "#.###.#.#..##..######....",
        "#.###.#.####.##..##....##",
        "#.###.#..#.###...#..#####",
        "#.....#.#..#..#....##.###",
        "#######.#..##...##...#..#",
    ),
)

DISCORD = LinkCode(
    url="https://discord.gg/vbhtjP2mtT",
    lines=(
        "Join the OpenFollow Discord for",
        "community discussions and support",
    ),
    symbol=(
        "#######..##.#.##..#######",
        "#.....#.#..#.###..#.....#",
        "#.###.#.####..##..#.###.#",
        "#.###.#..####..#..#.###.#",
        "#.###.#.#.....###.#.###.#",
        "#.....#.##.....#..#.....#",
        "#######.#.#.#.#.#.#######",
        "........##.##.#..........",
        "##.#..##..##.####.###.##.",
        "#.##.#.#..#.###..##.....#",
        ".#.##.#..##........##..##",
        "...###..#...######..#....",
        ".##...##...###..###..#.##",
        "..#.##...##.#.....##.##.#",
        "#....###..####..#####.#.#",
        ".####..#.##..#....#.#..#.",
        "###.#.#..##.....#######..",
        "........##.#..#.#...##..#",
        "#######.#...#.###.#.##.##",
        "#.....#...##....#...###..",
        "#.###.#....#.##.######...",
        "#.###.#.#..####.#..####..",
        "#.###.#...#.##.##..##.#.#",
        "#.....#.####.####....#...",
        "#######.##.###.###.#...##",
    ),
)

LINKS: tuple[LinkCode, ...] = (DOCS, DISCORD)


def draw_link_qr(cr: Any, code: LinkCode, x: float, y: float, size: float, *, radius: float = 10.0) -> None:
    """Draw ``code`` filling ``size`` square at ``x``/``y``, quiet zone included.

    The light modules are painted as a solid field first: a QR read against a
    dark HUD needs its own white ground, not the panel showing through. The
    corner radius is clamped to the quiet zone, so rounding the field to match
    the panels around it can never cut into the margin the symbol needs.
    """
    span = len(code.symbol) + 2 * QUIET_MODULES
    module = size / span
    draw_rounded_rect(cr, x, y, size, size, min(radius, QUIET_MODULES * module))
    cr.set_source_rgb(1.0, 1.0, 1.0)
    cr.fill()
    cr.set_source_rgb(0.0, 0.0, 0.0)
    origin = QUIET_MODULES * module
    for row, bits in enumerate(code.symbol):
        for col, bit in enumerate(bits):
            if bit == "#":
                cr.rectangle(x + origin + col * module, y + origin + row * module, module, module)
    cr.fill()
