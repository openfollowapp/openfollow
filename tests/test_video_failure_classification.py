# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The failure taxonomy: what we observed maps to what the operator is told."""

from __future__ import annotations

import pytest

from openfollow.video.failure import (
    RESOURCE_BUSY,
    RESOURCE_DOMAIN,
    RESOURCE_NOT_AUTHORIZED,
    RESOURCE_NOT_FOUND,
    RESOURCE_OPEN_READ,
    RESOURCE_SETTINGS,
    STREAM_CODEC_NOT_FOUND,
    STREAM_DECODE,
    STREAM_DEMUX,
    STREAM_DOMAIN,
    STREAM_TYPE_NOT_FOUND,
    STREAM_WRONG_TYPE,
    ConnectionPhase,
    VideoFailure,
    classify_failure,
    failure_chip,
    failure_sentence,
)

pytestmark = pytest.mark.unit


class TestPhaseOrdering:
    def test_phases_run_from_nothing_to_pictures(self) -> None:
        """Classification asks "did we get at least this far", so the order is
        load-bearing, not decorative."""
        assert (
            ConnectionPhase.STARTING
            < ConnectionPhase.TRANSPORT_UP
            < ConnectionPhase.STREAM_DESCRIBED
            < ConnectionPhase.DATA_ARRIVING
            < ConnectionPhase.DECODING
        )


class TestClassification:
    @pytest.mark.parametrize(
        ("phase", "domain", "code", "message", "expected"),
        [
            # The three the operator most needs told apart, and the observation
            # that separates them: how far the connection got.
            (ConnectionPhase.STARTING, RESOURCE_DOMAIN, RESOURCE_OPEN_READ, "", VideoFailure.UNREACHABLE),
            (ConnectionPhase.STREAM_DESCRIBED, "", 0, "", VideoFailure.NO_DATA),
            (
                ConnectionPhase.DATA_ARRIVING,
                STREAM_DOMAIN,
                STREAM_DECODE,
                "",
                VideoFailure.DECODE_ERROR,
            ),
            # Credentials and paths are answers, not silence - the far end
            # replied, so neither is a reachability problem.
            (
                ConnectionPhase.TRANSPORT_UP,
                RESOURCE_DOMAIN,
                RESOURCE_NOT_AUTHORIZED,
                "",
                VideoFailure.UNAUTHORIZED,
            ),
            (
                ConnectionPhase.TRANSPORT_UP,
                RESOURCE_DOMAIN,
                RESOURCE_NOT_FOUND,
                "",
                VideoFailure.STREAM_NOT_FOUND,
            ),
            # A refusal is an open failure whose text carries the OS wording.
            (
                ConnectionPhase.STARTING,
                RESOURCE_DOMAIN,
                RESOURCE_OPEN_READ,
                "Could not open resource: Connection refused",
                VideoFailure.REFUSED,
            ),
            # Same domain and code as the unreachable case above; only the
            # phase differs, and it changes the verdict.
            (ConnectionPhase.DECODING, RESOURCE_DOMAIN, RESOURCE_OPEN_READ, "", VideoFailure.STALLED),
            (
                ConnectionPhase.DATA_ARRIVING,
                STREAM_DOMAIN,
                STREAM_CODEC_NOT_FOUND,
                "",
                VideoFailure.UNSUPPORTED_FORMAT,
            ),
            (
                ConnectionPhase.DATA_ARRIVING,
                STREAM_DOMAIN,
                STREAM_TYPE_NOT_FOUND,
                "",
                VideoFailure.UNSUPPORTED_FORMAT,
            ),
            (
                ConnectionPhase.DATA_ARRIVING,
                STREAM_DOMAIN,
                STREAM_WRONG_TYPE,
                "",
                VideoFailure.UNSUPPORTED_FORMAT,
            ),
            (ConnectionPhase.DATA_ARRIVING, STREAM_DOMAIN, STREAM_DEMUX, "", VideoFailure.DECODE_ERROR),
            # A local capture device held by another program.
            (ConnectionPhase.STARTING, RESOURCE_DOMAIN, RESOURCE_BUSY, "", VideoFailure.DEVICE_UNAVAILABLE),
            # Silence with no error at all: the phase is the whole story.
            (ConnectionPhase.STARTING, "", 0, "", VideoFailure.UNREACHABLE),
            (ConnectionPhase.TRANSPORT_UP, "", 0, "", VideoFailure.NO_DATA),
            (ConnectionPhase.DECODING, "", 0, "", VideoFailure.STALLED),
            # An error we have no rule for stays honest rather than guessing.
            (ConnectionPhase.STARTING, "some-other-quark", 42, "", VideoFailure.UNKNOWN),
            # A known domain can still carry a code we have no rule for (the
            # generic ``FAILED = 1`` both enums start with). With no evidence of
            # progress that says nothing, so the verdict stays UNKNOWN rather
            # than guessing a neighbouring bucket.
            (ConnectionPhase.STARTING, RESOURCE_DOMAIN, 1, "", VideoFailure.UNKNOWN),
            # Source bytes are not proof video flowed, so an unreadable code
            # alongside them still says nothing.
            (ConnectionPhase.DATA_ARRIVING, STREAM_DOMAIN, 1, "", VideoFailure.UNKNOWN),
            # But once video was demonstrably decoding, the unknown code cannot
            # undo that: ``GST_STREAM_ERROR_FAILED`` / "Internal data stream
            # error" is the commonest dropout there is, and reporting it as
            # "Failed" hides the one fact we are sure of.
            (ConnectionPhase.DECODING, STREAM_DOMAIN, 1, "", VideoFailure.STALLED),
            (ConnectionPhase.DECODING, STREAM_DOMAIN, 1, "", VideoFailure.STALLED),
        ],
    )
    def test_observation_maps_to_failure(
        self,
        phase: ConnectionPhase,
        domain: str,
        code: int,
        message: str,
        expected: VideoFailure,
    ) -> None:
        assert classify_failure(phase=phase, domain=domain, code=code, message=message) == expected

    def test_open_failure_before_and_after_data_differ(self) -> None:
        """The distinction the whole taxonomy exists for. Identical GStreamer
        error; the phase is what makes one a routing fault the operator should
        chase and the other a dropout on a link that was working."""
        args = {"domain": RESOURCE_DOMAIN, "code": RESOURCE_OPEN_READ, "message": ""}
        before = classify_failure(phase=ConnectionPhase.STARTING, **args)  # type: ignore[arg-type]
        after = classify_failure(phase=ConnectionPhase.DECODING, **args)  # type: ignore[arg-type]

        assert before == VideoFailure.UNREACHABLE
        assert after == VideoFailure.STALLED

    def test_a_connection_that_carried_video_never_reads_as_unreachable(self) -> None:
        """Telling an operator the camera cannot be reached, about a camera
        that was on screen a second ago, sends them to the wrong equipment."""
        for domain, code in (("", 0), (RESOURCE_DOMAIN, RESOURCE_OPEN_READ), ("some-quark", 1)):
            failure = classify_failure(phase=ConnectionPhase.DECODING, domain=domain, code=code)
            assert failure != VideoFailure.UNREACHABLE

    def test_refusal_is_detected_case_insensitively(self) -> None:
        """GStreamer's wording of the same errno is not stable in case."""
        for text in ("Connection refused", "connection refused", "ECONNREFUSED"):
            assert (
                classify_failure(
                    phase=ConnectionPhase.STARTING, domain=RESOURCE_DOMAIN, code=RESOURCE_OPEN_READ, message=text
                )
                == VideoFailure.REFUSED
            )


class TestOperatorText:
    def test_every_failure_has_a_chip_and_a_sentence(self) -> None:
        """Five surfaces render these. A member added without text would reach
        an operator as a blank chip or a bare format placeholder."""
        for failure in VideoFailure:
            chip = failure_chip(failure)
            sentence = failure_sentence(failure, where="192.0.2.10:554")

            assert chip and chip == chip.strip()
            assert sentence.endswith(".")
            assert "{" not in sentence

    def test_the_endpoint_is_named_where_one_is_known(self) -> None:
        sentence = failure_sentence(VideoFailure.UNREACHABLE, where="192.0.2.10:554")
        assert "192.0.2.10:554" in sentence

    @pytest.mark.parametrize("where", ["", "   "])
    def test_a_missing_endpoint_still_reads_as_a_sentence(self, where: str) -> None:
        """The HUD renders this with no endpoint when the source dials nothing."""
        sentence = failure_sentence(VideoFailure.UNREACHABLE, where=where)

        assert "the video source" in sentence
        assert "  " not in sentence

    def test_sentences_carry_no_remedy(self) -> None:
        """Copy constraint: the sentence describes what was seen. Procedures
        live in the website docs, which an operator mid-show cannot open."""
        for failure in VideoFailure:
            sentence = failure_sentence(failure, where="192.0.2.10:554").lower()
            for instruction in ("please ", "try ", "check the", "make sure", "you should"):
                assert instruction not in sentence

    def test_chips_stay_short_enough_for_a_chip(self) -> None:
        """They sit in a fixed-width status chip on the Statistics panel."""
        for failure in VideoFailure:
            assert len(failure_chip(failure)) <= 16

    def test_enum_values_are_stable_wire_names(self) -> None:
        """``/api/stats`` publishes these for support tooling, so they are an
        interface: renaming a member silently breaks a consumer's matching."""
        assert {f.value for f in VideoFailure} == {
            "none",
            "not_configured",
            "unreachable",
            "refused",
            "unauthorized",
            "stream_not_found",
            "no_data",
            "unsupported_format",
            "decode_error",
            "stalled",
            "device_unavailable",
            "unknown",
        }


class TestRefusalArrivesInTheDebugString:
    """``rtspsrc`` and ``srtsrc`` put the OS wording in GStreamer's debug
    string, not the message, so searching only the message left REFUSED
    unreachable for every shipped plugin."""

    def test_a_refusal_in_the_debug_string_is_found(self) -> None:
        assert (
            classify_failure(
                phase=ConnectionPhase.STARTING,
                domain=RESOURCE_DOMAIN,
                code=RESOURCE_OPEN_READ,
                message="Could not open resource for reading and writing.",
                debug="gstrtspsrc.c(7469): gst_rtspsrc_send (): Connection refused",
            )
            == VideoFailure.REFUSED
        )

    def test_a_plain_timeout_in_the_debug_string_is_not_a_refusal(self) -> None:
        assert (
            classify_failure(
                phase=ConnectionPhase.STARTING,
                domain=RESOURCE_DOMAIN,
                code=RESOURCE_OPEN_READ,
                message="Could not open resource for reading and writing.",
                debug="gstrtspsrc.c(7469): gst_rtspsrc_send (): Connect timed out",
            )
            == VideoFailure.UNREACHABLE
        )


class TestDemonstratedFlowOutranksAnUnreadableError:
    """Evidence beats absence of a rule. A feed that was on screen a second ago
    must not be reported as a failure of unknown kind."""

    @pytest.mark.parametrize("domain", [RESOURCE_DOMAIN, STREAM_DOMAIN, "some-other-quark"])
    def test_an_unknown_code_after_video_flowed_is_a_stall(self, domain: str) -> None:
        assert classify_failure(phase=ConnectionPhase.DECODING, domain=domain, code=999) == VideoFailure.STALLED

    @pytest.mark.parametrize("domain", [RESOURCE_DOMAIN, STREAM_DOMAIN, "some-other-quark"])
    def test_an_unknown_code_with_no_progress_stays_unknown(self, domain: str) -> None:
        assert classify_failure(phase=ConnectionPhase.STARTING, domain=domain, code=999) == VideoFailure.UNKNOWN


class TestAnEmptySdpIsNotAGenericSettingsFailure:
    """Verified against a real camera: asking an RTSP server for a path it does
    not serve gets a session description listing no media, not a 404. It
    reaches us as ``RESOURCE_SETTINGS``, which on its own says nothing.
    """

    _MESSAGE = "Could not get/set settings from/on resource."
    _SDP_DEBUG = "gstrtspsrc.c(8357): gst_rtspsrc_setup_streams_start (): SDP contains no streams"

    def test_an_empty_sdp_names_the_path(self) -> None:
        assert (
            classify_failure(
                phase=ConnectionPhase.STARTING,
                domain=RESOURCE_DOMAIN,
                code=RESOURCE_SETTINGS,
                message=self._MESSAGE,
                debug=self._SDP_DEBUG,
            )
            == VideoFailure.STREAM_NOT_FOUND
        )

    def test_an_unrelated_settings_failure_is_not_claimed(self) -> None:
        """The code is generic; only the wording makes it a missing stream."""
        assert (
            classify_failure(
                phase=ConnectionPhase.STARTING,
                domain=RESOURCE_DOMAIN,
                code=RESOURCE_SETTINGS,
                message=self._MESSAGE,
                debug="could not set property on element",
            )
            == VideoFailure.UNKNOWN
        )

    def test_it_reads_the_path_not_a_dropped_feed(self) -> None:
        """A named cause outranks how far the feed once got."""
        assert (
            classify_failure(
                phase=ConnectionPhase.DECODING,
                domain=RESOURCE_DOMAIN,
                code=RESOURCE_SETTINGS,
                message=self._MESSAGE,
                debug=self._SDP_DEBUG,
            )
            == VideoFailure.STREAM_NOT_FOUND
        )


class TestSourceBytesAreNotDecodedVideo:
    """``DATA_ARRIVING`` is bytes out of the source element. A feed carrying a
    payload this station cannot decode produces them just as readily as a
    working one, so it is not evidence that video ever arrived.
    """

    def test_bytes_without_a_frame_never_reads_as_a_stall(self) -> None:
        """Otherwise a feed that never showed a picture is reported as one that
        "stopped arriving"."""
        assert classify_failure(phase=ConnectionPhase.DATA_ARRIVING) == VideoFailure.NO_DATA

    def test_a_decoded_frame_does(self) -> None:
        assert classify_failure(phase=ConnectionPhase.DECODING) == VideoFailure.STALLED

    def test_the_phase_is_the_feeds_history_not_the_attempts(self) -> None:
        """A retry knows nothing, so the caller passes how far the *feed* ever
        got; ``STARTING`` therefore means this feed never produced a byte."""
        assert classify_failure(phase=ConnectionPhase.STARTING) == VideoFailure.UNREACHABLE

    @pytest.mark.parametrize(
        ("phase", "expected"),
        [
            (ConnectionPhase.STARTING, VideoFailure.UNREACHABLE),
            (ConnectionPhase.TRANSPORT_UP, VideoFailure.NO_DATA),
            (ConnectionPhase.STREAM_DESCRIBED, VideoFailure.NO_DATA),
            (ConnectionPhase.DATA_ARRIVING, VideoFailure.NO_DATA),
            (ConnectionPhase.DECODING, VideoFailure.STALLED),
        ],
    )
    def test_the_open_failure_ladder_matches_the_silent_one(
        self, phase: ConnectionPhase, expected: VideoFailure
    ) -> None:
        """A read/open failure and plain silence describe the same progress, so
        they must not disagree about what that progress means."""
        assert classify_failure(phase=phase, domain=RESOURCE_DOMAIN, code=RESOURCE_OPEN_READ) == expected
        assert classify_failure(phase=phase) == expected

    def test_no_data_is_reachable_without_the_rtsp_sdp_hook(self) -> None:
        """Only RTSP reports TRANSPORT_UP / STREAM_DESCRIBED. Every other input
        jumps STARTING to DATA_ARRIVING, so NO_DATA has to be reachable from
        there or it is unreachable for SRT, RTP, NDI and every local device."""
        assert classify_failure(phase=ConnectionPhase.DATA_ARRIVING) == VideoFailure.NO_DATA


class TestNotFoundDoesNotClaimSomethingAnswered:
    """``RESOURCE_NOT_FOUND`` comes from an absent v4l2 / libcamera /
    AVFoundation device and a deleted Media Gallery file as readily as from an
    RTSP path, and neither of those answered anything.
    """

    @pytest.mark.parametrize("where", ["rtsp://192.0.2.10:554/no/such/stream", "/dev/video0", "Stage.webm"])
    def test_the_sentence_holds_for_every_producer(self, where: str) -> None:
        sentence = failure_sentence(VideoFailure.STREAM_NOT_FOUND, where=where)
        assert where in sentence
        assert "answered" not in sentence


class TestAnInputThatDialsNothingIsNotDescribedAsUnanswered:
    """Seen on hardware: an RTP listener with no sender reported "Nothing
    answered at RTP 0.0.0.0:5004", naming this station's own listening port as
    a host that failed to reply. A listener, a local capture device and a
    discovery-by-name source all connect nowhere.
    """

    @pytest.mark.parametrize("failure", [VideoFailure.UNREACHABLE, VideoFailure.NO_DATA])
    def test_the_wording_presumes_no_request(self, failure: VideoFailure) -> None:
        sentence = failure_sentence(failure, where="RTP 0.0.0.0:5004", dials_out=False)
        assert "answered" not in sentence
        assert "RTP 0.0.0.0:5004" in sentence

    @pytest.mark.parametrize("failure", [VideoFailure.UNREACHABLE, VideoFailure.NO_DATA])
    def test_a_dialling_input_keeps_the_stronger_wording(self, failure: VideoFailure) -> None:
        """ "Nothing answered" is the useful fact when we did dial somewhere."""
        assert "answered" in failure_sentence(failure, where="rtsp://192.0.2.10:554/s")

    def test_only_the_request_shaped_sentences_change(self) -> None:
        """A stall and a decode error describe what was observed either way."""
        for failure in (VideoFailure.STALLED, VideoFailure.DECODE_ERROR, VideoFailure.STREAM_NOT_FOUND):
            assert failure_sentence(failure, where="X") == failure_sentence(failure, where="X", dials_out=False)

    def test_every_failure_still_reads_as_a_sentence_without_a_dial(self) -> None:
        for failure in VideoFailure:
            sentence = failure_sentence(failure, where="X", dials_out=False)
            assert sentence.endswith(".")
            assert "{" not in sentence
