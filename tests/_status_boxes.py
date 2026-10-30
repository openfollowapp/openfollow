# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""Rendering Status & Diagnostics' live boxes, for tests about what they show."""

from __future__ import annotations

from typing import Any

from bottle import template

# A healthy station on its configured port: what ``_build_diagnostics_cards`` returns.
CARDS: dict[str, Any] = {
    "web_port_configured": 80,
    "web_port_display": 80,
    "web_port_match": True,
    "uptime_human": "1h 53m",
    "network_chip": ("ok", "Running"),
    "sender_last_send": "0.8s",
    "sender_errors": 0,
    "sender_send_count": 3407,
    "receiver_last_recv": "0.8s",
    "receiver_packet_count": 6812,
    "peer_count": 1,
}


def render_boxes(stats: dict[str, Any], **cards: Any) -> str:
    """The boxes for ``stats``, with ``cards`` overriding the healthy figures."""
    return template("partials/diagnostics_cards", stats=stats, cards={**CARDS, **cards})


def render_alerts(stats: dict[str, Any]) -> str:
    return template("partials/diagnostics_alerts", stats=stats)
