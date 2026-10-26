# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""What the Video Source page's Measure latency row shows, from the runtime stats and the run's status."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from openfollow.runtime.latency_probe import MEDIA_GALLERY, SAMPLES, FailureReason, LatencyFailure

# The notice class of each result level (docs/STATUS_LANGUAGE.md).
RESULT_CLASS = {"success": "success", "caution": "warning", "error": "error"}


def latency_unavailable(stats: Mapping[str, Any]) -> FailureReason | None:
    """Why a run cannot start now, None when it can: a screen shows the HUD and a camera's video is decoded."""
    video = stats.get("video") or {}
    if video.get("source_type") == MEDIA_GALLERY:
        return FailureReason.MEDIA_GALLERY
    if stats.get("hud") is None:
        return FailureReason.NO_DISPLAY
    # The published phase is the furthest the feed reached, so a feed that stopped still reads decoding.
    if not (video.get("connected") and video.get("phase") == "decoding"):
        return FailureReason.NO_VIDEO
    return None


def latency_view(stats: Mapping[str, Any], status: Mapping[str, Any], *, gallery: bool) -> dict[str, Any]:
    """The row's state: ``running`` with its progress, else the reason a run cannot start, else the last outcome."""
    state = str(status.get("state") or "idle")
    refusal = latency_unavailable(stats)
    result = status.get("result") if state == "done" else None
    return {
        "gallery": gallery,
        "state": state,
        "measured": int(status.get("measured") or 0),
        "total": SAMPLES,
        "unavailable": LatencyFailure(refusal).to_dict() if refusal is not None else None,
        "result": result,
        "result_class": RESULT_CLASS.get(str(result.get("level")), "error") if result else "",
        "failure": status.get("failure") if state == "failed" else None,
    }
