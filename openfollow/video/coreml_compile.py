# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Compile a detection model for CoreML in a child process.

Building an ONNX Runtime session holds the GIL for the whole build, and the
CoreML provider compiles a model on its first build, which takes seconds. Run
in the app, that compile stops every Python thread, the output senders
included. ``precompile`` runs it in a short-lived child that fills the
compiled-model cache instead, so the app's own build only loads the cached copy.
Waiting on the child releases the GIL. A host without the CoreML provider (the
Pi) never starts a child.

Entry points of the child: ``python -m openfollow.video.coreml_compile`` from a
source checkout, the bundle launcher's ``--coreml-compile`` in the macOS app.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openfollow.configuration import DetectionConfig

logger = logging.getLogger(__name__)

COMPILE_FLAG = "--coreml-compile"
_POLL_S = 0.1
# Generous: the largest tier takes about half a minute on a busy machine.
_COMPILE_TIMEOUT_S = 600.0
_STDERR_TAIL_LINES = 5
# One compile at a time, so two builds never write into the same cache directory.
_compile_lock = threading.Lock()


def coreml_available() -> bool:
    """Whether this host's onnxruntime offers the CoreML provider."""
    try:
        import onnxruntime as ort
    except ImportError:
        return False
    from openfollow.video.detection import _COREML_PROVIDER

    return _COREML_PROVIDER in ort.get_available_providers()


def compile_needed(model_path: str, cache_root: Path) -> bool:
    """Whether loading ``model_path`` would compile it for CoreML in-process."""
    if not coreml_available() or not Path(model_path).is_file():
        return False  # a missing model is reported by the build
    import onnxruntime as ort

    from openfollow.video.detection import coreml_compile_cached

    return not coreml_compile_cached(cache_root, model_path, ort.__version__)


def child_argv(model_path: str, cache_root: Path) -> list[str]:
    """The command that compiles ``model_path`` into ``cache_root``."""
    args = [model_path, str(cache_root)]
    if getattr(sys, "frozen", False):
        return [sys.executable, COMPILE_FLAG, *args]
    return [sys.executable, "-m", "openfollow.video.coreml_compile", *args]


def precompile(model_path: str, cache_root: Path, cancelled: Callable[[], bool]) -> bool:
    """Fill the CoreML cache for ``model_path`` from a child process.

    Blocks the calling thread, never the GIL, until the compile is done,
    ``cancelled()`` turns true or the timeout passes; a cut-short child is
    killed. True when the cache holds the compile (or none is needed).
    """
    while not _compile_lock.acquire(timeout=_POLL_S):
        if cancelled():
            return False
    try:
        if not compile_needed(model_path, cache_root):
            return True
        return _run_child(model_path, cache_root, cancelled)
    finally:
        _compile_lock.release()


def _detection_target(cfg: DetectionConfig) -> tuple[str, Path] | None:
    """The model file and cache root ``cfg`` builds from, or ``None`` when the
    storage is unusable (the detector build reports it)."""
    from openfollow.video.detection import PersonDetector, detection_cache_dir

    try:
        return PersonDetector.resolve_model_file(cfg), detection_cache_dir(cfg.storage_path)
    except (OSError, ValueError):
        return None


def detection_compile_needed(cfg: DetectionConfig) -> bool:
    """Whether building a detector for ``cfg`` would compile its model in-process."""
    if not coreml_available():
        return False
    target = _detection_target(cfg)
    return target is not None and compile_needed(*target)


def precompile_detection(cfg: DetectionConfig, cancelled: Callable[[], bool]) -> bool:
    """``precompile`` for the model a detector built from ``cfg`` loads."""
    if not coreml_available():
        return True
    target = _detection_target(cfg)
    return target is None or precompile(*target, cancelled)


def wait_idle(timeout_s: float) -> bool:
    """Wait for a running compile to end; a cancelled one kills its child within a poll."""
    if not _compile_lock.acquire(timeout=timeout_s):
        return False
    _compile_lock.release()
    return True


def _run_child(model_path: str, cache_root: Path, cancelled: Callable[[], bool]) -> bool:
    name = Path(model_path).name
    logger.info("Compiling %s for CoreML in a separate process", name)
    started = time.monotonic()
    try:
        proc = subprocess.Popen(  # nosec B603 - fixed argv, no shell
            child_argv(model_path, cache_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        logger.warning("Could not start the CoreML compile of %s: %s", name, exc)
        return False
    stderr = ""
    while True:
        try:
            _, stderr = proc.communicate(timeout=_POLL_S)
            break
        except subprocess.TimeoutExpired:
            pass
        reason = "cancelled" if cancelled() else None
        if reason is None and time.monotonic() - started > _COMPILE_TIMEOUT_S:
            reason = f"timed out after {_COMPILE_TIMEOUT_S:.0f}s"
        if reason is not None:
            proc.kill()
            proc.communicate()
            logger.info("CoreML compile of %s %s", name, reason)
            return False
    if proc.returncode != 0 or compile_needed(model_path, cache_root):
        tail = " | ".join((stderr or "").strip().splitlines()[-_STDERR_TAIL_LINES:])
        logger.warning("CoreML compile of %s failed (exit %s): %s", name, proc.returncode, tail or "no output")
        return False
    logger.info("Compiled %s for CoreML in %.1fs", name, time.monotonic() - started)
    return True


def main(argv: list[str] | None = None) -> int:
    """Child entry: build one session through the cache, which compiles into it."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print(f"usage: {COMPILE_FLAG} <model.onnx> <cache-root>", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(message)s")
    from openfollow.video.detection import _OnnxBackend

    try:
        _OnnxBackend(args[0], Path(args[1]))
    except Exception as exc:
        logger.error("CoreML compile of %s failed: %s", args[0], exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
