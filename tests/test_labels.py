# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the web UI label helper.

``pretty_label`` turns raw enum tokens (snake_case / ALL_CAPS) into
human-friendly display text without mangling acronyms or the gamepad
shoulder / trigger button names. The posted/stored value stays raw; only
the visible label is prettified.
"""

from __future__ import annotations

import pytest

from openfollow.web.labels import pretty_label

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Acronyms keep their canonical casing instead of Title-casing to
        # "Midi" / "Osc" / "Cc".
        ("midi_message", "MIDI Message"),
        ("osc", "OSC"),
        ("psn", "PSN"),
        ("cc", "CC"),
        # Gamepad shoulder / trigger buttons spelled out in full.
        ("LB", "Left Bumper"),
        ("RB", "Right Bumper"),
        ("LT", "Left Trigger"),
        ("RT", "Right Trigger"),
        # D-Pad tokens: the "dpad" word rewrite plus a capitalised direction.
        ("DPAD_UP", "D-Pad Up"),
        ("dpad_left", "D-Pad Left"),
        # Plain enum tokens fall back to Title Case.
        ("control_change", "Control Change"),
        ("note_on", "Note On"),
        ("fader_on_change", "Fader On Change"),
        ("press", "Press"),
        ("BACK", "Back"),
        # Single letters pass through.
        ("A", "A"),
        # Empty string and None both render as empty (the override path).
        ("", ""),
        (None, ""),
        # Non-string input is coerced via str().
        (7, "7"),
    ],
)
def test_pretty_label(raw: object, expected: str) -> None:
    assert pretty_label(raw) == expected
