# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""UTF-8 monkey-patch for Bottle's URL form decoding to prevent mojibake in form submissions."""

from __future__ import annotations

import functools
import urllib.parse

import bottle

# Monkey-patch: idempotent and safe for multiple imports
bottle.urlunquote = functools.partial(urllib.parse.unquote, encoding="utf-8")
