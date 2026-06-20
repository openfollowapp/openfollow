#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# OpenFollow boot splash launcher.
#
# Mirrors /usr/share/openfollow/splash.png to *every* connected HDMI output. A
# single unpinned kmssink only drives one connector, and spawning one kmssink
# process per connector does NOT work: DRM master is exclusive per device, so
# only the first process becomes master and lights its screen while the rest
# fail with "drmModeSetPlane: Permission denied". Instead a single process takes
# DRM master once and shares that one fd across one kmssink per connector (fanned
# off a tee), so every sink can modeset its own CRTC.
#
# Falls back to a single unpinned kmssink when connector enumeration is
# unavailable (modetest missing, or nothing connected) so a single-screen Pi
# still gets a splash. The helper holds until systemd stops the unit; on SIGTERM
# it exits hard (KillMode=control-group reaps the in-process kmssink), skipping
# GStreamer's NULL-state teardown which can deadlock on kmssink.
set -u
PNG="/usr/share/openfollow/splash.png"
exec python3 - "$PNG" <<'PYEOF'
import ctypes
import os
import signal
import subprocess
import sys

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst

PNG = sys.argv[1]
DRM_IOCTL_SET_MASTER = 0x641E  # _IO('d', 0x1e)


def find_vc4_card():
    """Path of the KMS card carrying the HDMI connectors (vc4), or None."""
    for n in range(8):
        dev = "/dev/dri/card%d" % n
        if os.path.exists(dev) and any(
            os.path.exists("/sys/class/drm/card%d-HDMI-A-%d/status" % (n, i))
            for i in (1, 2)
        ):
            return dev
    return None


def connected_connector_ids():
    """DRM connector ids reported 'connected' by modetest (col1=id, col3=status)."""
    try:
        out = subprocess.run(
            ["modetest", "-M", "vc4", "-c"],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    ids = []
    for line in out.splitlines():
        f = line.split()
        if len(f) >= 3 and f[0].isdigit() and f[2] == "connected":
            ids.append(f[0])
    return ids


def pipeline_desc():
    card = find_vc4_card()
    cids = connected_connector_ids() if card else []
    if card and cids:
        # One DRM master fd shared by every sink – the whole point.
        fd = os.open(card, os.O_RDWR | os.O_CLOEXEC)
        ctypes.CDLL("libc.so.6", use_errno=True).ioctl(fd, DRM_IOCTL_SET_MASTER, 0)
        branches = " ".join(
            "t. ! queue ! kmssink fd=%d connector-id=%s "
            "force-modesetting=true sync=false" % (fd, cid)
            for cid in cids
        )
        return ("filesrc location=%s ! pngdec ! imagefreeze ! videoconvert ! "
                "tee name=t %s" % (PNG, branches))
    return ("filesrc location=%s ! pngdec ! imagefreeze ! videoconvert ! "
            "kmssink driver-name=vc4 force-modesetting=true sync=false" % PNG)


Gst.init(None)
pipe = Gst.parse_launch(pipeline_desc())
pipe.set_state(Gst.State.PLAYING)
signal.signal(signal.SIGTERM, lambda *_a: os._exit(0))
GLib.MainLoop().run()
PYEOF
