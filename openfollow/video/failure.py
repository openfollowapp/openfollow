# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""How far a video connection got, and what stopped it.

One module owns the enum and both text maps, so the five surfaces that render
them - HUD, Statistics panel, Video Source section, ``/api/stats``, diagnostics
bundle - cannot describe the same failure differently.
"""

from __future__ import annotations

from enum import Enum, IntEnum

__all__ = [
    "ConnectionPhase",
    "SourceKind",
    "VideoFailure",
    "classify_failure",
    "failure_action",
    "failure_chip",
    "failure_sentence",
]


class ConnectionPhase(IntEnum):
    """How far a connection attempt got, ordered so "furthest reached" compares.

    ``DATA_ARRIVING`` is load-bearing: bytes out of the source element, which
    every protocol has, separating "never reached it" from "it sent nothing
    usable".
    """

    STARTING = 0
    TRANSPORT_UP = 1
    STREAM_DESCRIBED = 2
    DATA_ARRIVING = 3
    DECODING = 4


class SourceKind(Enum):
    """What kind of thing an input gets its video from.

    Picks the wording for the failures whose advice would otherwise name
    something the operator cannot find: an NDI source has no address or port, a
    USB camera has no sender, and a listener has no camera to power on.
    """

    REMOTE = "remote"  # dials a host: rtsp, srt
    NAMED = "named"  # finds a source by name on the network: ndi
    LISTENER = "listener"  # waits for packets at an address: rtp
    LOCAL = "local"  # hardware or a file on this box: v4l2, avf, picam, media


class VideoFailure(Enum):
    """Why there is no picture, in terms an operator can act on."""

    NONE = "none"
    NOT_CONFIGURED = "not_configured"
    UNREACHABLE = "unreachable"
    REFUSED = "refused"
    UNAUTHORIZED = "unauthorized"
    STREAM_NOT_FOUND = "stream_not_found"
    NO_DATA = "no_data"
    UNSUPPORTED_FORMAT = "unsupported_format"
    DECODE_ERROR = "decode_error"
    STALLED = "stalled"
    DEVICE_UNAVAILABLE = "device_unavailable"
    UNKNOWN = "unknown"


# GLib error domains, by the quark name GStreamer reports them under.
RESOURCE_DOMAIN = "gst-resource-error-quark"
STREAM_DOMAIN = "gst-stream-error-quark"

# ``GstResourceError``; named because a bare 15 in a branch says nothing.
RESOURCE_NOT_FOUND = 3
RESOURCE_BUSY = 4
RESOURCE_OPEN_READ = 5
RESOURCE_OPEN_READ_WRITE = 7
RESOURCE_READ = 9
RESOURCE_SETTINGS = 13
RESOURCE_NOT_AUTHORIZED = 15

# ``GstStreamError``.
STREAM_TYPE_NOT_FOUND = 4
STREAM_WRONG_TYPE = 5
STREAM_CODEC_NOT_FOUND = 6
STREAM_DECODE = 7
STREAM_DEMUX = 9

_OPEN_CODES = frozenset({RESOURCE_OPEN_READ, RESOURCE_OPEN_READ_WRITE, RESOURCE_READ})
_FORMAT_CODES = frozenset({STREAM_TYPE_NOT_FOUND, STREAM_WRONG_TYPE, STREAM_CODEC_NOT_FOUND})
_DECODE_CODES = frozenset({STREAM_DECODE, STREAM_DEMUX})

# A refusal has no distinct code - it is an open failure carrying the OS
# wording, and matching that text is the only way to separate "nothing
# answered" from "something answered no".
_REFUSED_MARKERS = ("connection refused", "econnrefused")

# An RTSP server that has nothing at the requested path answers with a session
# description listing no media rather than a 404, which reaches us as a generic
# settings failure. The wording is gstrtspsrc's own, not a camera's.
_EMPTY_SDP_MARKERS = ("sdp contains no streams", "no streams in sdp")

_CHIPS: dict[VideoFailure, str] = {
    VideoFailure.NONE: "OK",
    VideoFailure.NOT_CONFIGURED: "Not configured",
    VideoFailure.UNREACHABLE: "Unreachable",
    VideoFailure.REFUSED: "Refused",
    VideoFailure.UNAUTHORIZED: "Login rejected",
    VideoFailure.STREAM_NOT_FOUND: "Not found",
    VideoFailure.NO_DATA: "No video",
    VideoFailure.UNSUPPORTED_FORMAT: "Unsupported",
    VideoFailure.DECODE_ERROR: "Decode error",
    VideoFailure.STALLED: "Stalled",
    VideoFailure.DEVICE_UNAVAILABLE: "Device busy",
    VideoFailure.UNKNOWN: "Failed",
}

# Each sentence describes the observation and stops; remedies belong in the
# website docs, which an operator mid-show cannot open.
_SENTENCES: dict[VideoFailure, str] = {
    VideoFailure.NONE: "Video is arriving.",
    VideoFailure.NOT_CONFIGURED: "No video source is configured.",
    VideoFailure.UNREACHABLE: "Nothing answered at {where}.",
    VideoFailure.REFUSED: "{where} refused the connection.",
    VideoFailure.UNAUTHORIZED: "{where} rejected the login.",
    # Not "answered, but ...": the same code comes from a v4l2 / libcamera /
    # AVFoundation device that is absent and from a Media Gallery file that was
    # deleted, neither of which answered anything.
    VideoFailure.STREAM_NOT_FOUND: "{where} was not found.",
    # Not "sent no video": what the far end did is not observable from here.
    # The camera may be sending into a blocked media path, which is half of
    # what the accompanying action asks the operator to check.
    VideoFailure.NO_DATA: "{where} answered, but this station did not receive video from it.",
    VideoFailure.UNSUPPORTED_FORMAT: "Video is arriving from {where} in a format this station cannot decode.",
    VideoFailure.DECODE_ERROR: "Video is arriving from {where}, but it cannot be decoded.",
    VideoFailure.STALLED: "Video from {where} stopped arriving.",
    VideoFailure.DEVICE_UNAVAILABLE: "{where} is not available - another program may be holding it.",
    VideoFailure.UNKNOWN: "Video from {where} failed for a reason this station does not recognise.",
}

# An input that dials nothing - a listener waiting for packets, a local capture
# device, a discovery-by-name protocol - was never in a position to be answered,
# so the two sentences that presume a request get a wording that does not.
_NO_DIAL_SENTENCES: dict[VideoFailure, str] = {
    VideoFailure.UNREACHABLE: "No video has arrived from {where}.",
    VideoFailure.NO_DATA: "Data is arriving from {where}, but no video could be decoded from it.",
}

# One short next step per failure, kept separate from the sentence so the two
# never blur: the sentence is what was observed, this is what to do about it.
# One line, never a procedure - anything longer belongs in the website docs,
# which an operator mid-show cannot open.
_ACTIONS: dict[VideoFailure, str] = {
    VideoFailure.NONE: "",
    VideoFailure.NOT_CONFIGURED: "Choose a source under Video Source.",
    VideoFailure.UNREACHABLE: (
        "Check the camera is powered and on this network, or correct the address under Video Source."
    ),
    VideoFailure.REFUSED: "Check the port, and that the camera's stream is switched on.",
    VideoFailure.UNAUTHORIZED: "Check the username and password under Video Source.",
    VideoFailure.STREAM_NOT_FOUND: "Check the stream path matches the camera's settings.",
    VideoFailure.NO_DATA: "Check the camera is sending, and that nothing blocks its media path.",
    VideoFailure.UNSUPPORTED_FORMAT: "Set the camera to a codec this station can decode, such as H.264.",
    VideoFailure.DECODE_ERROR: "Check the camera's encoder settings, or lower its bitrate.",
    VideoFailure.STALLED: "Check the camera and the network link between it and this station.",
    VideoFailure.DEVICE_UNAVAILABLE: "Close any other program using this device.",
    VideoFailure.UNKNOWN: "Check the settings under Video Source, or download a diagnostics bundle.",
}

# Per kind, for the two failures whose default advice presumes a host we dialled.
# Anything not listed falls back to ``_ACTIONS``, which holds for every kind.
_KIND_ACTIONS: dict[SourceKind, dict[VideoFailure, str]] = {
    SourceKind.NAMED: {
        VideoFailure.UNREACHABLE: "Check the source is running and visible to this station on the network.",
        VideoFailure.NO_DATA: "Check the source is sending video, not audio only.",
    },
    SourceKind.LISTENER: {
        VideoFailure.UNREACHABLE: "Check the sender is transmitting to this address and port.",
        VideoFailure.NO_DATA: "Check the sender is transmitting the encoding configured on this station.",
    },
    SourceKind.LOCAL: {
        VideoFailure.UNREACHABLE: "Check the device is connected, and not held by another program.",
        VideoFailure.NO_DATA: "Check the device is producing a signal in the format configured for it.",
    },
}

# Keeps every sentence readable where the caller has no endpoint to name.
_ANONYMOUS_SOURCE = "the video source"


def failure_chip(failure: VideoFailure) -> str:
    """Two or three words for a status chip."""
    return _CHIPS.get(failure, _CHIPS[VideoFailure.UNKNOWN])


def failure_sentence(failure: VideoFailure, *, where: str = "", kind: SourceKind = SourceKind.REMOTE) -> str:
    """One sentence naming what was observed, for an operator.

    ``where`` MUST already be redacted: it reaches the HUD and the PIN-exempt
    stats route. Anything but ``REMOTE`` connects nowhere, so the wording does
    not report that nothing "answered" a request never made.
    """
    template = _SENTENCES.get(failure, _SENTENCES[VideoFailure.UNKNOWN])
    if kind is not SourceKind.REMOTE:
        template = _NO_DIAL_SENTENCES.get(failure, template)
    return template.format(where=where.strip() or _ANONYMOUS_SOURCE)


def _silence_verdict(phase: ConnectionPhase, saw_video: bool) -> VideoFailure:
    """How far it got, when nothing readable says why it stopped."""
    if saw_video:
        return VideoFailure.STALLED
    if phase >= ConnectionPhase.TRANSPORT_UP:
        return VideoFailure.NO_DATA
    return VideoFailure.UNREACHABLE


def failure_action(failure: VideoFailure, *, kind: SourceKind = SourceKind.REMOTE) -> str:
    """The one thing to try next, or ``""`` when there is nothing to suggest.

    Deliberately not folded into :func:`failure_sentence`: an operator reading a
    projected screen needs to separate what the station saw from what they are
    being asked to do, and a reader quoting the observation into a support
    thread should not carry our advice with it.

    ``kind`` keeps the advice inside the operator's own vocabulary. Telling them
    to check an address and port for an NDI source, which has neither, sends
    them looking for a setting that does not exist.
    """
    per_kind = _KIND_ACTIONS.get(kind, {})
    if failure in per_kind:
        return per_kind[failure]
    return _ACTIONS.get(failure, _ACTIONS[VideoFailure.UNKNOWN])


def classify_failure(
    *,
    phase: ConnectionPhase,
    domain: str = "",
    code: int = 0,
    message: str = "",
    debug: str = "",
) -> VideoFailure:
    """Name the failure from how far we got and what GStreamer said.

    *phase* is how far this **feed** has ever reached, not the attempt that
    just failed: the attempt is torn down and retried, and a retry knows
    nothing. The same "could not open resource" is a routing fault on a feed
    that never produced a byte and a dropout on one that was decoding.

    Only ``DECODING`` is evidence that video *arrived* - nothing but a decoded
    frame reaching the sink sets it. ``DATA_ARRIVING`` is bytes out of the
    source element, which a feed carrying an undecodable payload produces just
    as readily, so it means "it answered", not "it worked".
    """
    # The OS-level wording ("Connection refused") reaches us in the debug
    # string, not the message, so both are searched.
    text = f"{message}\n{debug}".lower()
    saw_video = phase >= ConnectionPhase.DECODING

    if domain == RESOURCE_DOMAIN:
        if code == RESOURCE_NOT_AUTHORIZED:
            return VideoFailure.UNAUTHORIZED
        if code == RESOURCE_NOT_FOUND:
            return VideoFailure.STREAM_NOT_FOUND
        if code == RESOURCE_BUSY:
            return VideoFailure.DEVICE_UNAVAILABLE
        if code == RESOURCE_SETTINGS and any(marker in text for marker in _EMPTY_SDP_MARKERS):
            return VideoFailure.STREAM_NOT_FOUND
        if code in _OPEN_CODES:
            if any(marker in text for marker in _REFUSED_MARKERS):
                return VideoFailure.REFUSED
            return _silence_verdict(phase, saw_video)

    if domain == STREAM_DOMAIN:
        if code in _FORMAT_CODES:
            return VideoFailure.UNSUPPORTED_FORMAT
        if code in _DECODE_CODES:
            return VideoFailure.DECODE_ERROR

    if domain or code:
        # An error with no rule cannot undo what was watched happening: a
        # connection that was carrying video and stopped is a stall, whatever
        # code GStreamer attached to it (``GST_STREAM_ERROR_FAILED`` /
        # "Internal data stream error" is the commonest dropout there is).
        # Without that evidence the error really does say nothing.
        return VideoFailure.STALLED if saw_video else VideoFailure.UNKNOWN

    return _silence_verdict(phase, saw_video)
