# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""Real GStreamer for the tests that run elements and pipelines rather than the fakes."""

from __future__ import annotations

from typing import Any

import pytest


def real_gst() -> Any:
    """The initialised ``Gst`` module; skips the test where PyGObject / GStreamer is not importable."""
    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
    except (ImportError, ValueError):
        pytest.skip("PyGObject / GStreamer is not importable")
    Gst.init(None)
    return Gst
