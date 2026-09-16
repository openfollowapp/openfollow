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
    "VideoFailure",
    "classify_failure",
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
    VideoFailure.STREAM_NOT_FOUND: "{where} answered, but has no stream at that path.",
    VideoFailure.NO_DATA: "{where} answered, but sent no video.",
    VideoFailure.UNSUPPORTED_FORMAT: "Video is arriving from {where} in a format this station cannot decode.",
    VideoFailure.DECODE_ERROR: "Video is arriving from {where}, but it cannot be decoded.",
    VideoFailure.STALLED: "Video from {where} stopped arriving.",
    VideoFailure.DEVICE_UNAVAILABLE: "{where} is not available - another program may be holding it.",
    VideoFailure.UNKNOWN: "Video from {where} failed for a reason this station does not recognise.",
}

# Keeps every sentence readable where the caller has no endpoint to name.
_ANONYMOUS_SOURCE = "the video source"


def failure_chip(failure: VideoFailure) -> str:
    """Two or three words for a status chip."""
    return _CHIPS.get(failure, _CHIPS[VideoFailure.UNKNOWN])


def failure_sentence(failure: VideoFailure, *, where: str = "") -> str:
    """One sentence naming what was observed, for an operator.

    ``where`` MUST already be redacted: it reaches the HUD and the PIN-exempt
    stats route.
    """
    template = _SENTENCES.get(failure, _SENTENCES[VideoFailure.UNKNOWN])
    return template.format(where=where.strip() or _ANONYMOUS_SOURCE)


def classify_failure(
    *,
    phase: ConnectionPhase,
    domain: str = "",
    code: int = 0,
    message: str = "",
    debug: str = "",
    was_connected: bool = False,
) -> VideoFailure:
    """Name the failure from how far we got and what GStreamer said.

    The phase decides an ambiguous error: the same "could not open resource" is
    a routing fault before any bytes arrive and a dropout after them.
    """
    # The OS-level wording ("Connection refused") reaches us in the debug
    # string, not the message, so both are searched.
    text = f"{message}\n{debug}".lower()
    # "Did video ever flow on this feed" is the one discriminator, and it is
    # deliberately not the phase alone: the phase describes the current attempt
    # and is reset before each retry, so a dropped feed retrying would otherwise
    # be redescribed as a source that was never reachable.
    flowing = was_connected or phase >= ConnectionPhase.DATA_ARRIVING

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
            return VideoFailure.STALLED if flowing else VideoFailure.UNREACHABLE

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
        return VideoFailure.STALLED if flowing else VideoFailure.UNKNOWN

    # No error to read, so the phase is the whole story.
    if flowing:
        return VideoFailure.STALLED
    if phase >= ConnectionPhase.TRANSPORT_UP:
        return VideoFailure.NO_DATA
    return VideoFailure.UNREACHABLE
