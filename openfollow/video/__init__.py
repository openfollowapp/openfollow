# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Video package: re-exports the receiver orchestrator and discovery entry points."""

from openfollow.video.receiver import (
    GstNativeSinkReceiver,
    discover_sources,
    gst_runtime_available,
)

__all__ = ["GstNativeSinkReceiver", "discover_sources", "gst_runtime_available"]
