# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann, Michel Honold, and Vinzenz Schultz
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""Human-friendly labels for raw enum tokens shown in the web UI."""

from __future__ import annotations

from typing import NamedTuple


class FeatureLabel(NamedTuple):
    """A Features table row's title and description, and what stops when the box is switched off."""

    title: str
    description: str
    when_off: str


FEATURE_LABELS: dict[str, FeatureLabel] = {
    "zones": FeatureLabel(
        "Trigger zones",
        "Fires OSC cues when a marker enters or leaves a zone on stage.",
        "zones stop firing and disappear from the Operator Screen.",
    ),
    "midi": FeatureLabel(
        "MIDI",
        "Opens MIDI devices for virtual faders and button bindings.",
        "MIDI devices are closed, so nothing played on them reaches this station.",
    ),
    "psn_input": FeatureLabel(
        "PSN input",
        "Shows the markers other stations send over PSN.",
        "other stations' markers disappear and no longer count for zones.",
    ),
    "lens_suggestions": FeatureLabel(
        "Lens line suggestions",
        "Suggests straight edges in the Setup Wizard's Lens step, to use or reject.",
        "the Lens step suggests no lines; lines are drawn by hand.",
    ),
    "detection": FeatureLabel(
        "Person detection",
        "Uses a local AI Vision model to detect people on the video stream. "
        "Markers can get magnetically pulled towards these people.",
        "detection stops, and markers are no longer pulled towards people.",
    ),
    "rttrpm": FeatureLabel(
        "RTTrPM output",
        "Sends marker positions as RTTrPM, next to PSN and OTP.",
        "RTTrPM output stops sending.",
    ),
    "paths": FeatureLabel(
        "Paths",
        "Locks a marker to a drawn path, so a stick moves it along the path.",
        "every marker on a path is let go.",
    ),
    "touch_remote": FeatureLabel(
        "Touch remote",
        "Drives a marker from a tablet or phone in the web UI.",
        "every touch remote lets go of its marker, and its launcher disappears.",
    ),
    "zone_heights": FeatureLabel(
        "Zone heights",
        "Draws a zone at a height above the stage, and can raise a marker's Z while it is inside.",
        "zones are drawn on the stage floor again and markers keep their own height.",
    ),
}

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
