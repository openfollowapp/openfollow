# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""What the Video Source page's Camera Latency row and its Measure dialog show."""

from __future__ import annotations

import json
import time
import zlib
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


def measured_when(finished_at: float, now: float) -> str:
    """When a run finished, in the station's local time: today, or on its date."""
    finished, today = time.localtime(finished_at), time.localtime(now)
    clock = time.strftime("%H:%M", finished)
    if finished[:3] == today[:3]:
        return f"Measured today at {clock}"
    return f"Measured on {time.strftime('%Y-%m-%d', finished)} at {clock}"


def latency_row_view(stats: Mapping[str, Any], *, gallery: bool, now: float) -> dict[str, Any]:
    """The row: the last successful run and when it finished, or the reason a run cannot start now."""
    refusal = latency_unavailable(stats)
    last = stats.get("latency")
    view: dict[str, Any] = {
        "gallery": gallery,
        "unavailable": LatencyFailure(refusal).to_dict() if refusal is not None else None,
        "last": None,
    }
    if isinstance(last, dict):
        view["last"] = {
            "median_ms": float(last.get("median_ms", 0.0)),
            "min_ms": float(last.get("min_ms", 0.0)),
            "max_ms": float(last.get("max_ms", 0.0)),
            "when": measured_when(float(last.get("finished_at", 0.0)), now),
        }
    # What the row shows, so a page polling the source status swaps it only when that changes.
    view["key"] = str(zlib.crc32(json.dumps(view, sort_keys=True).encode()))
    return view


def latency_dialog_view(status: Mapping[str, Any], *, before_start: bool) -> dict[str, Any]:
    """The dialog: ``intro`` before a start, ``running`` with its progress, else the run's ``done`` or ``failed``."""
    state = str(status.get("state") or "idle")
    if state != "running" and (before_start or state not in ("done", "failed")):
        state = "intro"
    result = status.get("result") if state == "done" else None
    return {
        "state": state,
        "measured": int(status.get("measured") or 0),
        "total": SAMPLES,
        "result": result,
        "result_class": RESULT_CLASS.get(str(result.get("level")), "error") if result else "",
        "failure": status.get("failure") if state == "failed" else None,
    }
