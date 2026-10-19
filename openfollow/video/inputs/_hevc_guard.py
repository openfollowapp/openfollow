# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Keep HEVC off the Pi 5 hardware decoder on kernels where stopping it can hang the station.

Before Raspberry Pi kernel 6.18.48, ``rpi_hevc_dec`` blocks ``VIDIOC_STREAMOFF`` forever
once a decode request stalls (a missing reference picture after packet loss, a starved
buffer): the process sits in uninterruptible sleep and the decoder is gone until a reboot.
Such kernels decode HEVC in software instead.
"""

from __future__ import annotations

import logging
import platform
import re
from typing import Any

logger = logging.getLogger(__name__)

HEVC_HARDWARE_DECODER = "v4l2slh265dec"
# The first rpi-6.18.y kernel whose HEVC decoder fails a stalled request instead of hanging.
FIXED_KERNEL = (6, 18, 48)

_logged = False


def kernel_version(release: str) -> tuple[int, int, int] | None:
    """``platform.release()`` such as ``6.18.39+rpt-rpi-2712`` as (major, minor, patch); None when unreadable."""
    match = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?", release)
    if match is None:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch or 0)


def hevc_hardware_decoder_safe(release: str) -> bool:
    """Whether this kernel stops its HEVC hardware decoder safely; an unreadable version counts as not."""
    version = kernel_version(release)
    return version is not None and version >= FIXED_KERNEL


def guard_hevc_decoder(gst: Any, release: str | None = None) -> None:
    """Rank the HEVC hardware decoder out on a kernel that can hang stopping it, so decodebin picks software."""
    global _logged
    release = platform.release() if release is None else release
    if hevc_hardware_decoder_safe(release):
        return
    factory = gst.ElementFactory.find(HEVC_HARDWARE_DECODER)
    if not factory:
        return
    factory.set_rank(gst.Rank.NONE)
    if not _logged:
        _logged = True
        logger.warning(
            "HEVC decodes in software: kernel %s can hang stopping the Pi 5 HEVC decoder"
            " (fixed in %d.%d.%d; update the system to use the hardware decoder)",
            release,
            *FIXED_KERNEL,
        )
