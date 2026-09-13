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
and any H.264 encoder::

    brew install gst-rtsp-server                                   # macOS
    sudo apt install gir1.2-gst-rtsp-server-1.0 gstreamer1.0-libav  # Debian

The encoder is picked from whatever is installed (``--list-encoders`` shows the
choice). ``x264enc`` is preferred where present but comes from
``gst-plugins-ugly``, which the OpenFollow appliance deliberately does not ship
(see ``THIRD_PARTY_NOTICES.md``), so on a station the fallback to ``gst-libav``
or ``gst-plugins-bad`` is the path that actually runs. This is bench tooling,
not part of the application.
"""

from __future__ import annotations

import argparse
import socket
import sys
from collections.abc import Callable

# Preference order, best-quality-per-CPU first. ``x264enc`` is ``gst-plugins-ugly``
# and absent from the appliance image; ``avenc_h264`` (gst-libav) and
# ``openh264enc`` (gst-plugins-bad) are the sets that ship, and ``vtenc_h264`` is
# VideoToolbox on macOS. Any of them is fine for a bench pattern.
_ENCODER_PREFERENCE: tuple[str, ...] = ("x264enc", "vtenc_h264", "avenc_h264", "openh264enc")

# Per-encoder flags for low-latency output; absent entries just take defaults.
_ENCODER_OPTIONS: dict[str, str] = {
    "x264enc": "tune=zerolatency speed-preset=ultrafast",
    "vtenc_h264": "realtime=true allow-frame-reordering=false",
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
    p.add_argument("--encoder", help="force an H.264 encoder instead of auto-selecting")
    p.add_argument("--list-encoders", action="store_true", help="print the available encoders and exit")
    return p


def pick_encoder(available: Callable[[str], bool], preferred: str | None = None) -> str | None:
    """Return the encoder to use, or ``None`` when nothing usable is installed.

    Separated from the server so the selection can be tested without a
    GStreamer registry.
    """
    if preferred is not None:
        return preferred if available(preferred) else None
    return next((name for name in _ENCODER_PREFERENCE if available(name)), None)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    import gi

    gi.require_version("Gst", "1.0")
    gi.require_version("GstRtspServer", "1.0")
    from gi.repository import GLib, Gst, GstRtspServer

    Gst.init(None)

    def available(name: str) -> bool:
        return Gst.ElementFactory.find(name) is not None

    if args.list_encoders:
        for name in _ENCODER_PREFERENCE:
            print(f"  {name:<14} {'available' if available(name) else 'not installed'}")
        return 0

    encoder = pick_encoder(available, args.encoder)
    if encoder is None:
        wanted = args.encoder or " / ".join(_ENCODER_PREFERENCE)
        print(f"error: no usable H.264 encoder ({wanted}). Install gstreamer1.0-libav.", file=sys.stderr)
        return 1
    options = _ENCODER_OPTIONS.get(encoder, "")

    # ``is-live`` keeps the pattern advancing on wall-clock time, so a station
    # that connects late still sees motion rather than a frame from t=0.
    launch = (
        f"( videotestsrc is-live=true pattern={args.pattern} "
        f"! video/x-raw,width={args.width},height={args.height},framerate={args.fps}/1 "
        f"! videoconvert ! {encoder} {options} key-int-max={args.fps} "
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
    print(f"serving {url}  ({args.width}x{args.height} @ {args.fps}, {args.pattern}, {encoder})")
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
