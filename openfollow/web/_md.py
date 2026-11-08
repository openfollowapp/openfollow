# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann, Michel Honold, and Vinzenz Schultz
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""Render help Markdown (``openfollow/web/help/*.md``) to HTML fragments.

``escape=True`` escapes any raw inline/block HTML in the source, keeping the
rendered fragment inert (no live ``<script>``). The ``table`` plugin enables
the only non-core Markdown construct the help docs use. An image whose URL is
``button:<symbol>`` is a gamepad button symbol, drawn inline in the text colour.

A ``### Heading`` opens a section the drawer shows closed, up to the next
heading of level 1–3: the heading is its title and a first paragraph, if the
section starts with one, its summary line.
"""

from __future__ import annotations

import html
import re
from typing import cast

import mistune

from openfollow.button_names import SYMBOL_SHAPES
from openfollow.web.button_icons import symbol_html


class _Renderer(mistune.HTMLRenderer):
    def link(self, text: str, url: str, title: str | None = None) -> str:
        html = super().link(text, url, title)
        # Followed in place, an external link would take the station's own page away.
        if url.startswith(("https://", "http://")):
            return html.replace("<a ", '<a target="_blank" rel="noopener noreferrer" ', 1)
        return html

    def image(self, text: str, url: str, title: str | None = None) -> str:
        symbol = url.removeprefix("button:")
        if url.startswith("button:") and symbol in SYMBOL_SHAPES:
            # mistune hands the alt text over escaped; symbol_html escapes it itself.
            return symbol_html(symbol, html.unescape(text))
        return super().image(text, url, title)


# Stateless renderer; reused across requests.
_render = mistune.create_markdown(renderer=_Renderer(escape=True), plugins=["table"])


_HEADING = re.compile(r"#{1,3} ")
_FENCE = re.compile(r"\s*(```|~~~)")
_ONE_PARAGRAPH = re.compile(r"<p>((?:(?!<p>|</p>).)*)</p>", re.S)


def _md(text: str) -> str:
    # mistune's ``__call__`` is typed ``str | list`` (it can emit an AST), but
    # the default HTML renderer always returns a string.
    return cast(str, _render(text))


def _paragraph(text: str) -> str | None:
    """``text`` rendered inline when it is exactly one paragraph, else ``None``."""
    match = _ONE_PARAGRAPH.fullmatch(_md(text).strip())
    return match.group(1) if match else None


def _section(title: str, lines: list[str]) -> str:
    body = "\n".join(lines).strip("\n")
    first, _, rest = body.partition("\n\n")
    summary = ""
    if first and (line := _paragraph(first)) is not None:
        summary, body = f"<span>{line}</span>", rest
    heading = _paragraph(title)
    if heading is None:
        heading = html.escape(title)
    return (
        f'<details class="help-more"><summary><b>{heading}</b>{summary}</summary>'
        f'<div class="help-more-content">{_md(body)}</div></details>\n'
    )


def render_help_markdown(text: str) -> str:
    out: list[str] = []
    plain: list[str] = []
    title: str | None = None
    section: list[str] = []
    in_fence = False
    for line in (text or "").splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        if not in_fence and _HEADING.match(line):
            if title is not None:
                out.append(_section(title, section))
                title = None
            elif plain:
                out.append(_md("\n".join(plain)))
                plain = []
            if line.startswith("### "):
                title, section = line[4:].strip(), []
                continue
        (section if title is not None else plain).append(line)
    if title is not None:
        out.append(_section(title, section))
    else:
        out.append(_md("\n".join(plain)))
    return "".join(out)
