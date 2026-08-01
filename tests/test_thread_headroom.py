# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Regression guard: the suite must not oversubscribe OpenCV's thread pool.

Each pytest-xdist worker imports OpenCV, whose parallel pool otherwise grows to
the full core count on first use; N workers running cv2 ops at once fan out to
N x cores threads and freeze an interactive desktop. tests/conftest.py caps it
with cv2.setNumThreads(1). These tests pin that so dropping the cap fails loudly.
"""

import subprocess
import sys
from pathlib import Path

import pytest

_CONFTEST = Path(__file__).resolve().with_name("conftest.py")

# Load conftest.py by *path*, never as the module ``tests.conftest``. This
# repo's ``tests/`` has no ``__init__.py``, so it is only a PEP 420 namespace
# portion – and a regular package anywhere on ``sys.path`` beats a namespace
# portion outright, whatever the path order. ``ultralytics`` (this project's own
# ``export`` extra) installs a top-level ``tests`` package with its own
# ``conftest.py``, so importing by name silently loads *that* file in any
# environment where the extra is present, and this guard passes while testing
# nothing.
_LOAD_CONFTEST = f"import runpy; runpy.run_path({str(_CONFTEST)!r})"

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


def test_conftest_applies_the_opencv_cap() -> None:
    """Importing conftest.py must actually collapse the pool, not just intend to.

    ``test_setnumthreads_caps_the_opencv_pool`` proves the lever works; this
    proves conftest.py pulls it. Importing the real conftest into a fresh probe
    process keeps the two halves independent – deleting the ``setNumThreads``
    call fails here while the lever test stays green.

    Measured in a subprocess, not in this worker. A worker's own thread count
    stops being a signal once the suite is running: threads left alive by
    earlier tests (web listeners, OSC servers, discovery beacons) inflate it
    until it no longer reflects OpenCV at all. ``cv2.getNumThreads()`` is no
    substitute either – under the GCD parallel framework macOS builds use, it
    keeps reporting the core count after a successful cap.

    The comparison is against a live uncapped probe rather than a fixed
    threshold: on a small-core runner the pool barely fans out, so ``capped <=
    2`` would hold even with the cap deleted and the guard would report green on
    a real regression. When the uncapped pool is too small to measure at all,
    skip rather than assert on noise.
    """
    uncapped = _probe_threads(cap="")
    if uncapped < 3:
        pytest.skip(f"OpenCV pool too small to measure (uncapped process held {uncapped} threads)")
    capped = _probe_threads(cap=_LOAD_CONFTEST)
    assert capped < uncapped, (
        f"conftest.py left {capped} threads vs {uncapped} uncapped - the OpenCV cap is not being applied"
    )
