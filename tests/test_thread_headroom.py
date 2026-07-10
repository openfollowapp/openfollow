# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Regression guard: the suite must not oversubscribe OpenCV's thread pool.

Each pytest-xdist worker imports OpenCV, whose parallel pool otherwise grows to
the full core count on first use; N workers running cv2 ops at once fan out to
N x cores threads and freeze an interactive desktop. tests/conftest.py caps it
with cv2.setNumThreads(1). These tests pin that so dropping the cap fails loudly.
"""

import os
import subprocess
import sys

import numpy as np
import psutil
import pytest

pytestmark = pytest.mark.unit

cv2 = pytest.importorskip("cv2")

# Absolute OS thread count of a fresh process after a burst of cv2 parallel ops.
# ``cap`` is Python injected right after the cv2 import (empty = leave default).
_PROBE = (
    "import numpy as np, cv2, psutil\n"
    "{cap}\n"
    "img = (np.random.rand(480, 640) * 255).astype('uint8')\n"
    "clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))\n"
    "for _ in range(20):\n"
    "    clahe.apply(img)\n"
    "print(psutil.Process().num_threads())\n"
)


def _probe_threads(cap: str) -> int:
    out = subprocess.run(
        [sys.executable, "-c", _PROBE.format(cap=cap)],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(out.stdout.strip().splitlines()[-1])


def test_setnumthreads_caps_the_opencv_pool() -> None:
    """setNumThreads(1) collapses the pool – the lever conftest.py relies on.

    Measured in isolated subprocesses so nothing in the test worker masks it.
    """
    uncapped = _probe_threads(cap="")
    capped = _probe_threads(cap="cv2.setNumThreads(1)")
    assert uncapped >= 3, f"expected OpenCV to fan out uncapped, saw {uncapped} threads"
    assert capped < uncapped
    assert capped <= 2, f"setNumThreads(1) left {capped} threads"


def test_conftest_caps_opencv_in_the_test_process() -> None:
    """conftest.py must cap OpenCV in the worker itself, not just a subprocess.

    Warm cv2 here, then compare this (capped) worker's thread count against a
    freshly spawned uncapped process: capped stays far below the full pool. The
    pool only registers on >=4 cores, so below that the signal is unmeasurable.
    """
    if (os.cpu_count() or 1) < 4:
        pytest.skip("OpenCV pool too small to measure below 4 cores")
    img = (np.random.rand(480, 640) * 255).astype("uint8")
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    for _ in range(20):
        clahe.apply(img)
    in_process = psutil.Process().num_threads()
    uncapped = _probe_threads(cap="")
    assert in_process < uncapped, (
        f"this worker holds {in_process} threads vs {uncapped} for an uncapped process - conftest.py did not cap OpenCV"
    )
