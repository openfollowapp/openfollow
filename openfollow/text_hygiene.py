# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Characters no free-text config value may carry."""

from __future__ import annotations

import re

# Control characters and bidi-override codepoints. The bidi-override range
# (U+202A–U+202E) lets a string look one way in a code review and render another
# way in the browser; the control range (U+0000–U+001F, U+007F) includes NUL,
# BEL, etc. that have no business in a config field. U+200E / U+200F (LTR/RTL
# marks) are also stripped – same family of direction-spoofing tricks.
CONTROL_CHARS_RE = re.compile("[\x00-\x1f\x7f\u200e-\u200f\u202a-\u202e]")
