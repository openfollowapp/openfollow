# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Human-friendly labels for raw enum tokens shown in the web UI."""

from __future__ import annotations

# Tokens that must keep their canonical casing rather than Title-case.
_ACRONYMS: dict[str, str] = {
    "midi": "MIDI",
    "osc": "OSC",
    "psn": "PSN",
    "cc": "CC",
    "rtsp": "RTSP",
    "ip": "IP",
    "url": "URL",
    "id": "ID",
    "udp": "UDP",
    "tcp": "TCP",
    "slip": "SLIP",
}

# Whole-token rewrites that aren't just a casing change
_WORDS: dict[str, str] = {
    "dpad": "D-Pad",
    "hud": "Operator Screen",  # on-device overlay label
    "lb": "Left Bumper",  # gamepad button
    "rb": "Right Bumper",
    "lt": "Left Trigger",
    "rt": "Right Trigger",
}

# Whole-value overrides, checked before the per-word pass.
_OVERRIDES: dict[str, str] = {
    "": "",
}


def pretty_label(value: object) -> str:
    """Return a human-friendly label for a raw enum token."""
    text = "" if value is None else str(value)
    if text in _OVERRIDES:
        return _OVERRIDES[text]
    words = text.replace("_", " ").replace("-", " ").split()
    out: list[str] = []
    for word in words:
        low = word.lower()
        if low in _ACRONYMS:
            out.append(_ACRONYMS[low])
        elif low in _WORDS:
            out.append(_WORDS[low])
        else:
            out.append(word[:1].upper() + word[1:].lower())
    return " ".join(out)


def video_signal_label(connected: bool, failure: str) -> str:
    """Label the Video panel's signal state by what went wrong.

    An unrecognised token degrades to the plain state rather than raising: a
    stats payload from a newer build must not take the panel down.
    """
    from openfollow.video.failure import VideoFailure, failure_chip

    if connected:
        return "Connected"
    try:
        classified = VideoFailure(failure)
    except ValueError:
        return "Disconnected"
    if classified in (VideoFailure.NONE, VideoFailure.UNKNOWN):
        return "Disconnected"
    return failure_chip(classified)


def video_error_token(failure_text: str, error_message: str, action: str) -> str:
    """Identify the failure box by what it actually displays.

    ``hx-preserve`` keys on this id. Hashing text the box hides (the pipeline's
    own wording, shown only when there is no classification) re-inserts the node
    on changes nobody sees, re-announcing the alert; omitting the action leaves
    stale text when only the advice changes.
    """
    import hashlib

    shown = (failure_text or error_message) + "\x00" + action
    return hashlib.sha256(shown.encode("utf-8")).hexdigest()[:12]
