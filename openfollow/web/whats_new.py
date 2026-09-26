# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The What's new step the updater ends in: this release's bundled notes.

``whatsnew/whatsnew.md`` opens with the version it describes (``v0.4.4``) and
is shown only when that matches the installed release, so a package that
still carries an older release's file falls back to a generic confirmation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from openfollow.web._md import render_help_markdown

WHATS_NEW_FILE = Path(__file__).resolve().parent / "whatsnew" / "whatsnew.md"

_VERSION_LINE_RE = re.compile(r"v?(\d+(?:\.\d+)*)\S*")
# Release segment only: a candidate build (``0.4.4rc1``) shows its release's notes.
_RELEASE_RE = re.compile(r"v?(\d+(?:\.\d+)*)")


@dataclass(frozen=True)
class WhatsNew:
    """``html`` is the rendered notes when they describe ``version``, else empty."""

    version: str
    matches: bool
    html: str


def _release(version: str) -> str | None:
    match = _RELEASE_RE.match(version.strip())
    return match.group(1) if match else None


def load_whats_new(installed_version: str, path: Path | None = None) -> WhatsNew:
    """The notes for ``installed_version``; ``matches`` is False when there are none."""
    none = WhatsNew(version=installed_version, matches=False, html="")
    try:
        text = (path or WHATS_NEW_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return none
    first_line, _sep, body = text.partition("\n")
    version_line = _VERSION_LINE_RE.fullmatch(first_line.strip())
    if version_line is None or not body.strip():
        return none
    installed = _release(installed_version)
    if installed is None or version_line.group(1) != installed:
        return none
    return WhatsNew(version=installed_version, matches=True, html=render_help_markdown(body))
