# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The alert boxes Live Statistics shows, for the panel and its announcer.

The panel is swapped every second, so a ``role="alert"`` inside it would be
inserted, and announced, every second. The boxes carry no role; one announcer
outside the swap speaks their text, and its poll answers 204 while that text
is unchanged. Both read this module, so they cannot say different things.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["StatisticsAlerts", "statistics_alerts"]

DETECTION_MISSING_STEP = "Install them from the Person Detection section, then restart."


@dataclass(frozen=True)
class StatisticsAlerts:
    # The failure sentence (or the element's own wording) and its action.
    video: tuple[str, str] | None
    # One line per missing controller.
    controllers: tuple[str, ...]
    # The missing-packages line.
    detection: str | None

    def spoken(self) -> list[str]:
        """Every box's text in page order, as the announcer reads it."""
        out = [" ".join(part for part in self.video if part)] if self.video is not None else []
        out.extend(self.controllers)
        if self.detection is not None:
            out.append(f"{self.detection} {DETECTION_MISSING_STEP}")
        return out

    def key(self) -> str:
        return hashlib.sha256("\x00".join(self.spoken()).encode("utf-8")).hexdigest()[:12]


def statistics_alerts(stats: Mapping[str, Any]) -> StatisticsAlerts:
    video = stats.get("video") or {}
    failure = str(video.get("failure") or "none")
    # "unknown" contributes no sentence: it would contradict the element's own wording.
    failure_text = str(video.get("failure_text") or "") if failure not in ("none", "unknown") else ""
    shown = failure_text or str(video.get("error_message") or "")
    video_alert = None
    if shown and not video.get("connected"):
        video_alert = (shown, str(video.get("failure_action") or ""))

    controllers = []
    for c in (stats.get("controllers") or {}).get("items") or []:
        if c.get("state") != "missing":
            continue
        line = f"C{int(c.get('controller_index', 0)) + 1} missing"
        if c.get("marker_id") is not None:
            line += f" · marker {c['marker_id']}"
        if c.get("name"):
            line += f" · {c['name']}"
        if c.get("port_label"):
            line += f" ({c['port_label']})"
        controllers.append(line)

    tracking = stats.get("tracking") or {}
    missing = tracking.get("missing_deps") or []
    detection = f"Missing packages: {', '.join(missing)}." if missing and tracking.get("enabled") else None
    return StatisticsAlerts(video=video_alert, controllers=tuple(controllers), detection=detection)
