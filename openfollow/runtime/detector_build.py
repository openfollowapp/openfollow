# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""A detector build that compiles its model for CoreML before it can be wired.

``AppRuntimeServices`` starts one when a detection change would compile a model
in-process (see ``video/coreml_compile.py``). The build thread waits for the
compile child; housekeeping polls ``ready()`` and wires the detector on the GTK
thread, where the build then only loads the compiled copy. ``update`` replaces
the request (latest wins) and ``cancel`` drops it and kills the child.
"""

from __future__ import annotations

import functools
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from openfollow.video import coreml_compile

if TYPE_CHECKING:
    from openfollow.configuration import DetectionConfig

logger = logging.getLogger(__name__)


def _model_target(cfg: DetectionConfig) -> tuple[str, Path] | None:
    """The model file and cache root ``cfg`` builds from, or ``None`` when the
    storage is unusable (the detector build reports it)."""
    from openfollow.video.detection import PersonDetector, detection_cache_dir

    try:
        return PersonDetector.resolve_model_file(cfg), detection_cache_dir(cfg.storage_path)
    except (OSError, ValueError):
        return None


def needs_compile(cfg: DetectionConfig) -> bool:
    """Whether building a detector for ``cfg`` would compile its model in-process."""
    if not coreml_compile.coreml_available():
        return False
    target = _model_target(cfg)
    if target is None:
        return False
    model_path, cache_root = target
    return coreml_compile.compile_needed(model_path, cache_root)


class DetectorBuild:
    """Compiles the latest requested detection config's model on a worker thread."""

    def __init__(
        self,
        cfg: DetectionConfig,
        compile_model: Callable[[str, Path, Callable[[], bool]], bool] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._cfg = cfg
        self._finished = False
        self._cancelled = False
        self._compile_model = compile_model or coreml_compile.precompile
        self._thread = threading.Thread(target=self._run, daemon=True, name="DetectorBuild")
        self._thread.start()

    def update(self, cfg: DetectionConfig) -> None:
        """Replace the request. A compile for another model or storage is cut
        short; one for the same model finishes and serves this request too."""
        with self._lock:
            self._cfg = cfg

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True

    def ready(self) -> DetectionConfig | None:
        """The config to build now that its compile is done, else ``None``."""
        with self._lock:
            return self._cfg if self._finished and not self._cancelled else None

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def _superseded(self, cfg: DetectionConfig) -> bool:
        with self._lock:
            latest = self._cfg
            return self._cancelled or (latest.model, latest.storage_path) != (cfg.model, cfg.storage_path)

    def _run(self) -> None:
        while True:
            with self._lock:
                cfg = self._cfg
            target = _model_target(cfg)
            if target is not None:
                try:
                    self._compile_model(target[0], target[1], functools.partial(self._superseded, cfg))
                except Exception:
                    logger.exception("CoreML compile of the detection model raised")
            with self._lock:
                if self._cancelled:
                    return
                if self._cfg is cfg:
                    self._finished = True
                    return
