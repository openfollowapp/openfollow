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

from openfollow.web.labels import pretty_label, video_error_token, video_signal_label

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


class TestVideoSignalLabel:
    """The Video panel's signal state. "Disconnected" is true of every failure
    and useful for none of them - it does not say which box to go and look at.
    """

    def test_a_connected_feed_reads_as_connected(self) -> None:
        assert video_signal_label(True, "unreachable") == "Connected"

    @pytest.mark.parametrize(
        ("failure", "expected"),
        [
            ("unreachable", "Unreachable"),
            ("refused", "Refused"),
            ("unauthorized", "Login rejected"),
            ("no_data", "No video"),
            ("stalled", "Stalled"),
        ],
    )
    def test_a_classified_failure_names_itself(self, failure: str, expected: str) -> None:
        assert video_signal_label(False, failure) == expected

    @pytest.mark.parametrize("failure", ["none", "unknown"])
    def test_an_unclassified_failure_falls_back_to_the_plain_state(self, failure: str) -> None:
        assert video_signal_label(False, failure) == "Disconnected"

    @pytest.mark.parametrize("failure", ["", "teleported_away", "UNREACHABLE"])
    def test_an_unrecognised_token_degrades_instead_of_raising(self, failure: str) -> None:
        """This renders a live provider's dict. A payload from a newer build
        must not take the Statistics page down."""
        assert video_signal_label(False, failure) == "Disconnected"


class TestVideoErrorToken:
    """``hx-preserve`` keys the failure box on this id.

    Both hosts re-swap their partial on a timer, and the node survives only
    while the id does. It therefore has to track exactly what the box shows.
    """

    def test_it_ignores_wording_the_box_hides(self) -> None:
        """The pipeline's own text is displayed only when there is no
        classification; hashing it otherwise re-announces the alert on a change
        no one can see."""
        a = video_error_token("Nothing answered at X.", "Could not open resource.", "Check the camera.")
        b = video_error_token("Nothing answered at X.", "A totally different GStreamer message.", "Check the camera.")
        assert a == b

    def test_it_tracks_the_action(self) -> None:
        """Advice can change while the observation does not; omitting it left
        the old next step on screen."""
        a = video_error_token("Nothing answered at X.", "", "Check the camera is powered.")
        b = video_error_token("Nothing answered at X.", "", "Check the sender is transmitting.")
        assert a != b

    def test_it_tracks_the_observation(self) -> None:
        assert video_error_token("Nothing answered at X.", "", "Check.") != video_error_token(
            "X was not found.", "", "Check."
        )

    def test_it_tracks_the_wording_that_stands_in_for_a_missing_sentence(self) -> None:
        """With no classification the raw text *is* the message, so a change to
        it must re-announce."""
        assert video_error_token("", "v4l2src is Linux-only", "Check.") != video_error_token(
            "", "libcamera is Pi-only", "Check."
        )
