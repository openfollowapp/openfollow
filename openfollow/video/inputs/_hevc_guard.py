# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Keep HEVC off the Raspberry Pi hardware decoder.

``v4l2slh265dec`` over ``rpi_hevc_dec`` cannot yet lose a reference picture safely: after
packet loss, or when a stream is joined between keyframes, the driver rejects the slice
parameters and stopping the pipeline afterwards crashes the process. Kernels before 6.18.49
can instead hang that stop in uninterruptible sleep until a reboot. On a Raspberry Pi HEVC
therefore decodes in software.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from openfollow.privilege.camera_config import MODEL_PATH, is_raspberry_pi

logger = logging.getLogger(__name__)

HEVC_HARDWARE_DECODER = "v4l2slh265dec"

_logged = False


def guard_hevc_decoder(gst: Any, model_path: Path = MODEL_PATH) -> None:
    """Rank the HEVC hardware decoder out on a Raspberry Pi, so decodebin picks software."""
    global _logged
    if not is_raspberry_pi(model_path):
        return
    factory = gst.ElementFactory.find(HEVC_HARDWARE_DECODER)
    if not factory:
        return
    factory.set_rank(gst.Rank.NONE)
    if not _logged:
        _logged = True
        logger.warning(
            "HEVC decodes in software: the Raspberry Pi HEVC hardware decoder can take the station down"
            " after a lost reference picture"
        )
