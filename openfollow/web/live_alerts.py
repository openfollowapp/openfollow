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

__all__ = ["PowerAlert", "StatisticsAlerts", "statistics_alerts"]

DETECTION_MISSING_STEP = "Install them from the Person Detection section, then restart."


def _read_out(text: str, action: str) -> str:
    """*text* and its next step as two sentences: a row may be written without a closing stop."""
    if not action:
        return text
    stop = "" if text.endswith((".", "!", "?", "…")) else "."
    return f"{text}{stop} {action}"


@dataclass(frozen=True)
class PowerAlert:
    # "error" while it lasts, "caution" once only the since-boot flag is left.
    level: str
    text: str
    step: str = ""


UNDER_VOLTAGE_NOW = PowerAlert(
    "error", "Power supply under voltage now.", "Use a 5 V 5 A supply or unplug bus-powered USB devices."
)
THROTTLED_NOW = PowerAlert("error", "CPU throttled now: the Pi is too hot.", "Check the fan and airflow.")
UNDER_VOLTAGE_SINCE_BOOT = PowerAlert("caution", "Under voltage reported since the station last started.")
THROTTLED_SINCE_BOOT = PowerAlert("caution", "CPU throttling reported since the station last started.")


@dataclass(frozen=True)
class StatisticsAlerts:
    # The failure sentence (or the element's own wording) and its action.
    video: tuple[str, str] | None
    # One (sentence, action) per output not sending, as the HUD's network rows.
    network: tuple[tuple[str, str], ...]
    # One line per missing controller.
    controllers: tuple[str, ...]
    # The missing-packages line.
    detection: str | None
    # Supply and throttling, errors first.
    power: tuple[PowerAlert, ...] = ()

    def spoken(self) -> list[str]:
        """Every box's text in page order, as the announcer reads it."""
        out = [_read_out(*self.video)] if self.video is not None else []
        out.extend(_read_out(*row) for row in self.network)
        out.extend(self.controllers)
        out.extend(" ".join(part for part in (alert.text, alert.step) if part) for alert in self.power)
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

    network = tuple(
        (str(row.get("text") or ""), str(row.get("action") or ""))
        for row in (stats.get("network") or {}).get("alerts") or []
        if row.get("text")
    )

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
    power = (stats.get("system") or {}).get("power") or {}
    return StatisticsAlerts(
        video=video_alert,
        network=network,
        controllers=tuple(controllers),
        detection=detection,
        power=_power_alerts(power),
    )


def _power_alerts(power: Mapping[str, Any]) -> tuple[PowerAlert, ...]:
    under_voltage = bool(power.get("under_voltage"))
    errors: list[PowerAlert] = []
    cautions: list[PowerAlert] = []
    if under_voltage:
        errors.append(UNDER_VOLTAGE_NOW)
    elif power.get("under_voltage_since_boot"):
        cautions.append(UNDER_VOLTAGE_SINCE_BOOT)
    # An under-voltage caps the CPU too; heat is named only without one.
    if power.get("throttled"):
        if not under_voltage:
            errors.append(THROTTLED_NOW)
    elif power.get("throttled_since_boot"):
        cautions.append(THROTTLED_SINCE_BOOT)
    return (*errors, *cautions)
