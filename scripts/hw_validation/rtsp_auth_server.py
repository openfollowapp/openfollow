#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Serve a test RTSP stream that demands a username and password.

Exists because the credential path is otherwise untestable: an IP camera that
*requires* RTSP authentication is the one thing a bench usually doesn't have,
and it is exactly the condition that produced the report behind the RTSP login
fields - a camera returning ``401 Unauthorized`` while the UI showed a working
1080p feed. Point a station at this and the real failure is reproducible on
demand::

    # default: rtsp://<this-host>:8554/test, operator / hunter2
    python3 scripts/hw_validation/rtsp_auth_server.py

    # a password full of the characters that break a URL
    python3 scripts/hw_validation/rtsp_auth_server.py --password 'p@ss:word/1'

    # no auth at all, as a control
    python3 scripts/hw_validation/rtsp_auth_server.py --no-auth

Then in the station's Video Source: URL ``rtsp://<this-host>:8554/test`` with
the matching Username / Password. Leaving them blank must fail with the
server's own ``401``; filling them in must connect.

Needs the ``gst-rtsp-server`` GObject bindings (``GstRtspServer-1.0.typelib``)
and an H.264 encoder::

    brew install gst-rtsp-server                                          # macOS
    sudo apt install gir1.2-gst-rtsp-server-1.0 gstreamer1.0-plugins-bad  # Debian

The encoder is the first of ``x264enc``, ``vtenc_h264`` and ``openh264enc``
that GStreamer can create. ``x264enc`` comes from ``gst-plugins-ugly``, which
the OpenFollow appliance deliberately does not ship (see
``THIRD_PARTY_NOTICES.md``), so on a station ``openh264enc`` from
``gst-plugins-bad`` is the one that actually runs. ``--encoder`` forces any
other installed H.264 encoder, and ``--list-encoders`` shows them all. Only the
settings the encoder has are applied; the startup banner names them, and any
it had to leave out. This is bench tooling, not part of the application.
"""

from __future__ import annotations

import argparse
import socket
import sys
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

# Auto-selection order; the first that outputs H.264 and can be created is used.
_ENCODER_PREFERENCE: tuple[str, ...] = ("x264enc", "vtenc_h264", "openh264enc")

# The keyframe interval under each encoder family's name; the first one the
# chosen encoder has is set.
_KEYFRAME_PROPERTIES: tuple[str, ...] = ("key-int-max", "max-keyframe-interval", "gop-size")

# Low-latency switches, set on any encoder that has them.
_LOW_LATENCY_OPTIONS: dict[str, str] = {"realtime": "true", "allow-frame-reordering": "false"}

# Settings whose values fit one element only. Bitrate is about 4 Mbit/s each:
# x264enc and vtenc take kbit/s, avenc and openh264enc bit/s.
_ENCODER_OPTIONS: dict[str, dict[str, str]] = {
    "x264enc": {"tune": "zerolatency", "speed-preset": "ultrafast", "bitrate": "4000"},
    "vtenc_h264": {"bitrate": "4000"},
    "vtenc_h264_hw": {"bitrate": "4000"},
    "avenc_h264_videotoolbox": {"bitrate": "4000000"},
    "openh264enc": {"bitrate": "4000000"},  # its default target is 128 kbit/s
}

DEFAULT_PORT = 8554
DEFAULT_PATH = "/test"
DEFAULT_USER = "operator"
DEFAULT_PASSWORD = "hunter2"  # nosec B105 - bench credential, not a secret


def _local_ip() -> str:
    """Best-effort outward-facing IPv4, for printing a reachable URL."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 1))  # TEST-NET-1: routed nowhere, never sends
        return str(s.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"listen port (default {DEFAULT_PORT})")
    p.add_argument("--path", default=DEFAULT_PATH, help=f"mount point (default {DEFAULT_PATH})")
    p.add_argument("--user", default=DEFAULT_USER, help=f"username (default {DEFAULT_USER})")
    p.add_argument("--password", default=DEFAULT_PASSWORD, help="password")
    p.add_argument("--no-auth", action="store_true", help="serve without authentication, as a control")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument(
        "--pattern",
        default="smpte",
        help="videotestsrc pattern name (smpte, ball, snow, …)",
    )
    p.add_argument("--encoder", help="force an installed H.264 encoder instead of auto-selecting")
    p.add_argument("--list-encoders", action="store_true", help="print the installed H.264 encoders and exit")
    return p


@dataclass(frozen=True)
class EncoderSettings:
    """The ``name=value`` settings applied to an encoder, and those it lacked."""

    applied: dict[str, str]
    skipped: tuple[str, ...] = ()

    def __str__(self) -> str:
        return " ".join(f"{name}={value}" for name, value in self.applied.items())


class EncoderError(Exception):
    """No encoder can serve the stream; the message says why."""


def encoder_settings(encoder: str, has_property: Callable[[str], bool], keyframe_interval: int) -> EncoderSettings:
    """Return the settings *encoder* has, and the requested ones it lacks.

    A property the element lacks fails the media pipeline, and every client is
    answered with a 503, so each setting goes only where the property exists.
    """
    requested = _ENCODER_OPTIONS.get(encoder, {})
    applied = {name: value for name, value in {**_LOW_LATENCY_OPTIONS, **requested}.items() if has_property(name)}
    skipped = [name for name in requested if name not in applied]
    keyframe = next((name for name in _KEYFRAME_PROPERTIES if has_property(name)), None)
    if keyframe is None:
        skipped.append("keyframe interval")
    else:
        applied[keyframe] = str(keyframe_interval)
    return EncoderSettings(applied, tuple(skipped))


def probe_encoder(gst: Any, encoder: str, keyframe_interval: int) -> EncoderSettings | None:
    """Return *encoder*'s settings, or ``None`` when GStreamer cannot create it.

    The element lives only for the probe.
    """
    element = gst.ElementFactory.make(encoder, None)
    if element is None:
        return None
    return encoder_settings(encoder, lambda name: element.find_property(name) is not None, keyframe_interval)


def h264_encoders(gst: Any) -> list[str]:
    """Name every installed video encoder that can output H.264."""
    encoders = gst.ElementFactory.list_get_elements(gst.ELEMENT_FACTORY_TYPE_VIDEO_ENCODER, gst.Rank.NONE)
    h264 = gst.ElementFactory.list_filter(encoders, gst.Caps.from_string("video/x-h264"), gst.PadDirection.SRC, False)
    return sorted(factory.get_name() for factory in h264)


def auto_encoder(
    h264: Collection[str], probe: Callable[[str], EncoderSettings | None]
) -> tuple[str, EncoderSettings] | None:
    """Return the first preferred encoder that outputs H.264 and can be created."""
    for name in _ENCODER_PREFERENCE:
        settings = probe(name) if name in h264 else None
        if settings is not None:
            return name, settings
    return None


def pick_encoder(
    forced: str | None,
    h264: Collection[str],
    installed: Callable[[str], bool],
    probe: Callable[[str], EncoderSettings | None],
) -> tuple[str, EncoderSettings]:
    """Return the encoder to serve with and its settings.

    A forced encoder is never substituted: that would hide the operator's
    mistake and change what the bench run tests. Raises :class:`EncoderError`
    naming why nothing is usable.
    """
    if forced is None:
        picked = auto_encoder(h264, probe)
        if picked is None:
            raise EncoderError(
                f"none of {' / '.join(_ENCODER_PREFERENCE)} can be used. Install gstreamer1.0-plugins-bad"
                " for openh264enc, or force another H.264 encoder with --encoder."
            )
        return picked
    if forced not in h264:
        if installed(forced):
            raise EncoderError(f"--encoder {forced}: not an H.264 video encoder (see --list-encoders).")
        raise EncoderError(f"--encoder {forced}: no element of that name is installed (see --list-encoders).")
    settings = probe(forced)
    if settings is None:
        raise EncoderError(f"--encoder {forced}: installed, but GStreamer could not create it.")
    return forced, settings


def encoder_report(h264: Collection[str], probe: Callable[[str], EncoderSettings | None]) -> list[str]:
    """Return one line per installed H.264 encoder and per preferred one."""
    results = {name: probe(name) for name in h264}
    picked = auto_encoder(h264, results.__getitem__)
    names = [*_ENCODER_PREFERENCE, *sorted(set(h264).difference(_ENCODER_PREFERENCE))]
    width = max(len(name) for name in names)
    lines = []
    for name in names:
        notes = ["preferred"] if name in _ENCODER_PREFERENCE else []
        if name not in results:
            notes.append("not installed")
        else:
            notes.append("usable" if results[name] is not None else "cannot be created")
        if picked is not None and name == picked[0]:
            notes.append("auto-selected")
        lines.append(f"  {name:<{width}}  {', '.join(notes)}")
    return lines


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    import gi

    gi.require_version("Gst", "1.0")
    gi.require_version("GstRtspServer", "1.0")
    from gi.repository import GLib, Gst, GstRtspServer

    Gst.init(None)
    h264 = h264_encoders(Gst)

    def probe(name: str) -> EncoderSettings | None:
        return probe_encoder(Gst, name, args.fps)

    if args.list_encoders:
        print("\n".join(encoder_report(h264, probe)))
        return 0

    try:
        encoder, settings = pick_encoder(
            args.encoder, h264, lambda name: Gst.ElementFactory.find(name) is not None, probe
        )
    except EncoderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # ``is-live`` keeps the pattern advancing on wall-clock time, so a station
    # that connects late still sees motion rather than a frame from t=0.
    launch = (
        f"( videotestsrc is-live=true pattern={args.pattern} "
        f"! video/x-raw,width={args.width},height={args.height},framerate={args.fps}/1 "
        f"! videoconvert ! {encoder} {settings} "
        f"! rtph264pay name=pay0 pt=96 )"
    )

    server = GstRtspServer.RTSPServer()
    server.set_service(str(args.port))

    factory = GstRtspServer.RTSPMediaFactory()
    factory.set_launch(launch)
    factory.set_shared(True)  # one encoder feeds every client

    if not args.no_auth:
        auth = GstRtspServer.RTSPAuth()
        token = GstRtspServer.RTSPToken()
        token.set_string("media.factory.role", args.user)
        auth.add_basic(GstRtspServer.RTSPAuth.make_basic(args.user, args.password), token)
        server.set_auth(auth)

        # Without an explicit grant the factory is readable by anyone, so the
        # server would accept an unauthenticated client and the test would
        # silently prove nothing.
        permissions = GstRtspServer.RTSPPermissions()
        permissions.add_permission_for_role(args.user, "media.factory.access", True)
        permissions.add_permission_for_role(args.user, "media.factory.construct", True)
        factory.set_permissions(permissions)

    server.get_mount_points().add_factory(args.path, factory)
    if server.attach(None) == 0:
        print(f"error: could not listen on port {args.port} (already in use?)", file=sys.stderr)
        return 1

    url = f"rtsp://{_local_ip()}:{args.port}{args.path}"
    print(f"serving {url}  ({args.width}x{args.height} @ {args.fps}, {args.pattern})")
    print(f"  encoder:  {encoder} {settings}".rstrip())
    if settings.skipped:
        print(f"  not set:  {', '.join(settings.skipped)} (no such property on {encoder})")
    if args.no_auth:
        print("  auth:     disabled (control run)")
    else:
        print(f"  username: {args.user}")
        print(f"  password: {args.password}")
        print("  a client with no credentials must be refused 401 Unauthorized")
    print("Ctrl-C to stop.")

    loop = GLib.MainLoop()
    try:
        loop.run()
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
