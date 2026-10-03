# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Characters no free-text config value may carry."""

from __future__ import annotations

import re

# Control characters and bidi codepoints. The embeddings and overrides
# (U+202A–U+202E) and the isolates (U+2066–U+2069) let a string look one way in a
# code review and render another way in the browser; the C0 and C1 control ranges
# (U+0000–U+001F, U+007F–U+009F) include NUL, BEL, etc. that have no business in
# a config field. U+200E / U+200F (LTR/RTL marks) and U+061C (Arabic letter mark)
# are also stripped – same family of direction-spoofing tricks.
CONTROL_CHARS_RE = re.compile("[\x00-\x1f\x7f-\x9f\u061c\u200e-\u200f\u202a-\u202e\u2066-\u2069]")
