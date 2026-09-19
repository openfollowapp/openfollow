#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Regenerate the Settings-screen QR symbols in ``runtime/overlay_links.py``.

A workstation task: it needs OpenCV, which the runtime only has when the
detection extra is installed, and the symbols it prints are checked in so the
station never encodes anything itself. Prints the rows for every link; the
decode back to each URL is the check that matters.

    poetry run python scripts/gen_link_qr.py
"""

from __future__ import annotations

import sys

import cv2
import numpy as np

from openfollow.runtime.overlay_links import LINKS, QUIET_MODULES


def _symbol(url: str) -> np.ndarray:
    """The bare symbol for ``url``, the encoder's own quiet zone trimmed off."""
    matrix = (cv2.QRCodeEncoder_create().encode(url) == 0).astype(np.uint8)
    ys, xs = np.nonzero(matrix)
    return matrix[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]


def _decodes_back(symbol: np.ndarray, url: str) -> bool:
    scale = 8
    margin = QUIET_MODULES * scale
    dark = np.kron(1 - symbol, np.ones((scale, scale), np.uint8)) * 255
    canvas = np.full((dark.shape[0] + 2 * margin, dark.shape[1] + 2 * margin), 255, np.uint8)
    canvas[margin : margin + dark.shape[0], margin : margin + dark.shape[1]] = dark
    decoded, _, _ = cv2.QRCodeDetector().detectAndDecode(canvas)
    return bool(decoded == url)


def main() -> int:
    for code in LINKS:
        symbol = _symbol(code.url)
        if not _decodes_back(symbol, code.url):
            print(f"{code.url} did not decode back from its own symbol", file=sys.stderr)
            return 1
        print(f"\n# {code.url} ({symbol.shape[0]}x{symbol.shape[0]})")
        for row in symbol:
            print('        "' + "".join("#" if v else "." for v in row) + '",')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
