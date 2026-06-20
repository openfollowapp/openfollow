# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Render help Markdown (``openfollow/web/help/*.md``) to HTML fragments.

``escape=True`` escapes any raw inline/block HTML in the source, keeping the
rendered fragment inert (no live ``<script>``). The ``table`` plugin enables
the only non-core Markdown construct the help docs use.
"""

from __future__ import annotations

from typing import cast

import mistune

# Stateless renderer; reused across requests.
_render = mistune.create_markdown(escape=True, plugins=["table"])


def render_help_markdown(text: str) -> str:
    # mistune's ``__call__`` is typed ``str | list`` (it can emit an AST), but
    # the default HTML renderer always returns a string.
    return cast(str, _render(text or ""))
