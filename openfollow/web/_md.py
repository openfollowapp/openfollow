# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Render help Markdown (``openfollow/web/help/*.md``) to HTML fragments.

``escape=True`` escapes any raw inline/block HTML in the source, keeping the
rendered fragment inert (no live ``<script>``). The ``table`` plugin enables
the only non-core Markdown construct the help docs use. An image whose URL is
``button:<symbol>`` is a gamepad button symbol, drawn inline in the text colour.
"""

from __future__ import annotations

import html
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


def render_help_markdown(text: str) -> str:
    # mistune's ``__call__`` is typed ``str | list`` (it can emit an AST), but
    # the default HTML renderer always returns a string.
    return cast(str, _render(text or ""))
