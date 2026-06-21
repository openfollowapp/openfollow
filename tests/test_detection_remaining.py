# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Residual coverage for :mod:`openfollow.video.detection`.

The existing suites cover:

* :mod:`tests.test_detection` – pure helpers (``_nms``, ``_track`` track-id
  stickiness, ``check_detection_dependencies``).
* :mod:`tests.test_tracking` – the ByteTrack tracker (Kalman filter,
  two-stage association, lifecycle).
* :mod:`tests.test_detection_edge_cases` – ``_load_backend`` ONNX session
  construction (success, replaced-backend close, import failure, session
  failure), ``_prepare_model_path`` storage-path policy, ``_sample_to_numpy``
  buffer map paths, ``_preprocess`` no-op, ``start`` no-op guards,
  ``performance_stats``
  counter read-through.

This file fills in:

* ``_OnnxBackend`` – constructor + ``predict`` matrix (input-size probe
  from ONNX graph, empty predictions, score-filter reject,
  box-coords unprojection, ``max_persons`` cap, degenerate-box skip).
* ``_OnnxBackend._prepare_predictions`` shape-matrix branches.
* ``_nms`` early-exit on single remaining index (``order.size == 1``).
* ``_run`` outer try/except guard (inner raise → ``logger.exception``).
* ``_run_inner`` loop matrix: no sample → sample-timeout counter,
  ``_sample_to_numpy`` fail → sample-failure counter, inference error
  → error counter, happy path updates counters + results.
* ``start`` happy path (backend + appsink present, counters reset, thread
  armed).
* ``stop`` with a live thread reference.
* ``set_appsink`` assignment.
* ``_preprocess`` CLAHE branch (requires cv2 + a CLAHE stand-in).
* ``tracked_detection`` pinned-but-missing → release fallback.
* ``_track`` greedy-matching continue-on-matched branch.
* ``_prepare_model_path`` probe-write OSError.

All backend + GStreamer interactions are monkeypatched at the lowest
possible import seam. No real ``onnxruntime`` / ``gi.repository`` /
appsink calls.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
import types
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

import openfollow.video.detection as detection_module
from openfollow.configuration import DetectionConfig
from openfollow.video.detection import (
    DetectionBox,
    PersonDetector,
    _nms,
    _OnnxBackend,
    _TrackedPerson,
)

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _cfg(**overrides: Any) -> DetectionConfig:
    """Build a ``DetectionConfig`` with deterministic defaults."""
    base = DetectionConfig(
        enabled=False,
        model="yolov8n.onnx",
        storage_path="",
        inference_size=320,
        confidence=0.4,
        interval_ms=100,
        max_persons=10,
        grace_period_ms=500,
    )
    if overrides:
        base = replace(base, **overrides)
    return base


def _detector_without_backend(**overrides: Any) -> PersonDetector:
    """Build a ``PersonDetector`` where backend initialisation is skipped.

    ``enabled=False`` makes ``__init__`` return before the
    ``_prepare_model_path`` / ``_load_backend`` chain – perfect for
    tests that drive the detector's properties + ``_run_inner`` /
    ``_track`` directly without needing a real model file.
    """
    cfg = _cfg(enabled=False, **overrides)
    return PersonDetector(cfg)


# --------------------------------------------------------------------------- #
# _nms – single-remaining-index early exit
# --------------------------------------------------------------------------- #


class TestNmsSingleIndexExit:
    def test_single_box_returns_its_index(self) -> None:
        """Branch 163-164 early break – ``order.size == 1`` after the
        top box has been kept, so the inner IoU expansion loop isn't
        entered.  Regression guard against an off-by-one that would
        try to index ``order[0]`` when empty.
        """
        boxes = np.array([[0.0, 0.0, 1.0, 1.0]], dtype=np.float32)
        scores = np.array([0.9], dtype=np.float32)
        assert _nms(boxes, scores) == [0]

    def test_empty_inputs_return_empty_keep(self) -> None:
        assert _nms(np.empty((0, 4), dtype=np.float32), np.array([])) == []

    def test_two_non_overlapping_boxes_keeps_both_sorted_by_score(self) -> None:
        boxes = np.array([[0.0, 0.0, 0.4, 0.4], [0.6, 0.6, 1.0, 1.0]], dtype=np.float32)
        scores = np.array([0.7, 0.9], dtype=np.float32)
        assert _nms(boxes, scores) == [1, 0]

    def test_highly_overlapping_boxes_naturally_exit_while_loop(self) -> None:
        """Covers branch 160->183: the ``while order.size > 0:`` False
        side reached via the IoU filter, not the ``order.size == 1``
        break.  Two near-identical boxes – the top-scored is kept; the
        other fails the IoU threshold and gets filtered out, leaving
        ``order`` empty so the loop exits naturally.
        """
        boxes = np.array([[0.0, 0.0, 1.0, 1.0], [0.01, 0.01, 0.99, 0.99]], dtype=np.float32)
        scores = np.array([0.9, 0.85], dtype=np.float32)
        # Highest-score kept; near-identical sibling filtered (IoU ≈ 0.96 > 0.45).
        assert _nms(boxes, scores) == [0]


# --------------------------------------------------------------------------- #
# _OnnxBackend._prepare_predictions – shape matrix
# --------------------------------------------------------------------------- #


class TestPrepareOnnxPredictions:
    def test_batched_3d_output_is_squeezed(self) -> None:
        raw = np.zeros((1, 6, 3), dtype=np.float32)
        raw[0, :, 0] = [0.5, 0.5, 1.0, 1.0, 0.9, 0.1]  # one box, class_0 high
        pred = _OnnxBackend._prepare_predictions(raw)
        assert pred.ndim == 2
        # Transposed so rows = detections, cols = [cx, cy, w, h, class...]
        assert pred.shape[0] >= 1

    def test_non_2d_output_returns_empty(self) -> None:
        """A 1-D output slipped through here would later trigger an
        IndexError in ``predict``.  Short-circuit at the guard.
        """
        raw = np.zeros((5,), dtype=np.float32)
        assert _OnnxBackend._prepare_predictions(raw).size == 0

    def test_transpose_when_first_dim_is_short(self) -> None:
        """Many YOLO exports emit ``[84, N]`` rather than ``[N, 84]`` –
        the helper's heuristic transposes so predict sees rows = detections.
        """
        raw = np.zeros((6, 100), dtype=np.float32)
        out = _OnnxBackend._prepare_predictions(raw)
        assert out.shape == (100, 6)


class TestOnnxExtractInputSize:
    def test_non_4d_shape_returns_none(self) -> None:
        assert _OnnxBackend._extract_input_size((3, 224, 224)) is None

    def test_negative_dim_returns_none(self) -> None:
        assert _OnnxBackend._extract_input_size((1, 3, -1, 320)) is None

    def test_non_int_dim_returns_none(self) -> None:
        assert _OnnxBackend._extract_input_size((1, 3, "height", 320)) is None

    def test_valid_shape_returns_max_dim(self) -> None:
        assert _OnnxBackend._extract_input_size((1, 3, 224, 320)) == 320


# --------------------------------------------------------------------------- #
# _OnnxBackend construction + predict
# --------------------------------------------------------------------------- #


class _FakeOnnxSession:
    """Stand-in for ``onnxruntime.InferenceSession``.

    Scripts a single output tensor that ``predict`` will feed through
    ``_prepare_predictions`` + NMS.  The default output shape is
    ``(1, N, 5)`` – one box per row with centre/size + score, matching
    the YOLOv8 ``[cx, cy, w, h, score]`` layout ``_OnnxBackend.predict``
    interprets.
    """

    def __init__(self, output: np.ndarray | None = None) -> None:
        self.output = output
        self.run_calls: list[tuple[Any, dict[str, Any]]] = []

    def get_inputs(self) -> list[types.SimpleNamespace]:
        return [types.SimpleNamespace(name="images", shape=(1, 3, 320, 320))]

    def get_outputs(self) -> list[types.SimpleNamespace]:
        return [types.SimpleNamespace(name="output0")]

    def get_providers(self) -> list[str]:
        return ["CPUExecutionProvider"]

    def run(self, out_names: list[str], feeds: dict[str, Any]) -> list[np.ndarray]:
        self.run_calls.append((out_names, feeds))
        if self.output is not None:
            return [self.output]
        # Empty prediction by default.
        return [np.zeros((1, 0, 5), dtype=np.float32)]


def _install_fake_onnxruntime(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: Any | None = None,
    available_providers: list[str] | None = None,
) -> types.ModuleType:
    """Install a fake ``onnxruntime`` module.

    ``session_factory`` is called with ``(path, sess_options, providers)``
    and must return a session-like object (typically ``_FakeOnnxSession``).
    ``available_providers`` is what ``get_available_providers()`` reports;
    defaults to a CPU-only host.
    """
    fake = types.ModuleType("onnxruntime")

    class _SessionOptions:
        intra_op_num_threads: int = 0

    fake.SessionOptions = _SessionOptions  # type: ignore[attr-defined]
    fake.get_available_providers = lambda: list(  # type: ignore[attr-defined]
        available_providers if available_providers is not None else ["CPUExecutionProvider"]
    )

    if session_factory is None:

        def session_factory(*a: Any, **kw: Any) -> _FakeOnnxSession:
            return _FakeOnnxSession()

    fake.InferenceSession = session_factory  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "onnxruntime", fake)
    return fake


class TestSelectProviders:
    """``_OnnxBackend._select_providers`` orders accelerators ahead of CPU."""

    def test_coreml_preferred_when_available(self) -> None:
        chosen = _OnnxBackend._select_providers(["CoreMLExecutionProvider", "CPUExecutionProvider"])
        assert chosen == ["CoreMLExecutionProvider", "CPUExecutionProvider"]

    def test_cpu_only_when_no_accelerator(self) -> None:
        # The Pi (Linux aarch64) reports no CoreML – must stay CPU-only.
        assert _OnnxBackend._select_providers(["CPUExecutionProvider"]) == ["CPUExecutionProvider"]

    def test_cpu_appended_even_when_runtime_omits_it(self) -> None:
        # CPU is always the trailing fallback for ops the accelerator drops.
        chosen = _OnnxBackend._select_providers(["CoreMLExecutionProvider"])
        assert chosen == ["CoreMLExecutionProvider", "CPUExecutionProvider"]
        assert chosen[-1] == "CPUExecutionProvider"


class TestOnnxBackendConstruction:
    def test_session_options_threads_set_to_four(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def _factory(path: str, *, sess_options: Any, providers: list[str]) -> _FakeOnnxSession:
            captured["path"] = path
            captured["threads"] = sess_options.intra_op_num_threads
            captured["providers"] = providers
            return _FakeOnnxSession()

        _install_fake_onnxruntime(monkeypatch, session_factory=_factory)

        backend = _OnnxBackend("models/yolov8n.onnx")
        assert captured["path"] == "models/yolov8n.onnx"
        assert captured["threads"] == 4
        assert captured["providers"] == ["CPUExecutionProvider"]
        assert backend._model_input_size == 320

    def test_coreml_requested_when_runtime_offers_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def _factory(path: str, *, sess_options: Any, providers: list[str]) -> _FakeOnnxSession:
            captured["providers"] = providers
            return _FakeOnnxSession()

        _install_fake_onnxruntime(
            monkeypatch,
            session_factory=_factory,
            available_providers=["CoreMLExecutionProvider", "CPUExecutionProvider"],
        )

        _OnnxBackend("models/yolov8n.onnx")
        assert captured["providers"] == ["CoreMLExecutionProvider", "CPUExecutionProvider"]


class _FakeCv2:
    """Minimal ``cv2`` stand-in used by ``TestOnnxBackendPredict``.

    ``_OnnxBackend.predict`` calls ``_letterbox`` which calls
    ``cv2.resize`` – on Linux CI ``opencv-python`` isn't installed, so
    ``detection_module.cv2`` is ``None`` and the real call would
    AttributeError.  Monkeypatching a fake ``cv2`` instead of
    ``skipif``-ing keeps the predict-matrix exercised on every
    platform / CI image, which is where the coverage ratchet is
    actually enforced.

    Only the subset ``_letterbox`` touches is implemented: ``resize``
    returns a correctly-shaped uint8 array and ``INTER_LINEAR`` is a
    sentinel integer.
    """

    INTER_LINEAR = 1

    @staticmethod
    def resize(
        img: np.ndarray,
        dsize: tuple[int, int],
        interpolation: int = 1,  # noqa: ARG004
    ) -> np.ndarray:
        w, h = dsize
        # Match OpenCV: the output array is uint8 with the same channel
        # count as the input, filled deterministically so downstream
        # ONNX tensor math is reproducible across test runs.
        channels = img.shape[2] if img.ndim == 3 else 1
        shape = (h, w, channels) if img.ndim == 3 else (h, w)
        return np.zeros(shape, dtype=np.uint8)


class TestOnnxBackendPredict:
    """``_OnnxBackend.predict`` drives ``_letterbox`` which calls
    ``cv2.resize``.  Rather than skip these tests on environments
    without ``opencv-python`` (Linux CI), monkeypatch
    ``detection_module.cv2`` with :class:`_FakeCv2` so the predict
    matrix runs everywhere – the coverage ratchet is enforced on the
    very CI image that used to omit opencv-python, and the intended
    ONNX-logic coverage actually lands there.
    """

    def _run_predict(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        output: np.ndarray,
        frame: np.ndarray | None = None,
        confidence: float = 0.4,
        max_persons: int = 10,
    ) -> list[DetectionBox]:
        _install_fake_onnxruntime(
            monkeypatch,
            session_factory=lambda *a, **kw: _FakeOnnxSession(output=output),
        )
        monkeypatch.setattr(detection_module, "cv2", _FakeCv2)
        backend = _OnnxBackend("fake.onnx")
        if frame is None:
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
        return backend.predict(
            frame=frame,
            confidence=confidence,
            max_persons=max_persons,
            inference_size=320,
        )

    def test_empty_prediction_returns_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        out = np.zeros((1, 0, 5), dtype=np.float32)
        assert self._run_predict(monkeypatch, output=out) == []

    def test_predictions_with_class_count_under_five_return_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``pred.shape[1] <= 4`` means no class scores survived the
        transpose – return empty rather than reading a missing column.
        """
        # 3 box coords, no score column
        out = np.array([[[0.5, 0.5, 1.0, 1.0]]], dtype=np.float32)
        assert self._run_predict(monkeypatch, output=out) == []

    def test_all_scores_below_threshold_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        out = np.array([[[160.0, 160.0, 80.0, 200.0, 0.05]]], dtype=np.float32)
        assert self._run_predict(monkeypatch, output=out, confidence=0.9) == []

    def test_returns_normalised_box_for_above_threshold_detection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        out = np.array([[[160.0, 160.0, 80.0, 200.0, 0.85]]], dtype=np.float32)
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        boxes = self._run_predict(monkeypatch, output=out, frame=frame)
        assert len(boxes) == 1
        # Normalised to frame W/H.
        assert 0.0 <= boxes[0].x1 <= 1.0
        assert 0.0 <= boxes[0].y1 <= 1.0
        assert 0.0 <= boxes[0].x2 <= 1.0
        assert 0.0 <= boxes[0].y2 <= 1.0
        assert boxes[0].confidence == pytest.approx(0.85)

    def test_max_persons_caps_returned_boxes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Three non-overlapping above-threshold detections; cap to 2.
        out = np.array(
            [
                [
                    [40.0, 40.0, 60.0, 100.0, 0.80],
                    [160.0, 160.0, 60.0, 100.0, 0.85],
                    [280.0, 280.0, 60.0, 100.0, 0.90],
                ]
            ],
            dtype=np.float32,
        )
        boxes = self._run_predict(monkeypatch, output=out, max_persons=2)
        assert len(boxes) == 2

    def test_degenerate_post_clip_box_is_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Box sitting entirely outside the frame post-letterbox – all
        # corners clip to the same boundary value.
        out = np.array([[[-500.0, -500.0, 10.0, 10.0, 0.9]]], dtype=np.float32)
        boxes = self._run_predict(monkeypatch, output=out)
        assert boxes == []

    def test_end_to_end_head_parses_box_as_xyxy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A 6-wide row is the NMS-free head ``[x1, y1, x2, y2, conf, class]``.

        Regression for the YOLO26 giant-box bug: the old code treated these
        columns as ``cx, cy, w, h`` and ran a cx,cy,w,h->xyxy conversion,
        producing boxes spanning the whole frame. Asserting the exact xyxy
        un-letterbox (scale 0.5, pad_y 40 for a 640x480 frame -> 320 input)
        proves the box is read as xyxy, not converted.
        """
        out = np.array([[[80.0, 90.0, 160.0, 250.0, 0.85, 0.0]]], dtype=np.float32)
        boxes = self._run_predict(monkeypatch, output=out)
        assert len(boxes) == 1
        assert boxes[0].x1 == pytest.approx(0.25)
        assert boxes[0].y1 == pytest.approx(100.0 / 480.0)
        assert boxes[0].x2 == pytest.approx(0.5)
        assert boxes[0].y2 == pytest.approx(0.875)
        assert boxes[0].confidence == pytest.approx(0.85)

    def test_end_to_end_head_filters_non_person_class(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # class 2 (not person) above confidence must be dropped.
        out = np.array([[[80.0, 90.0, 160.0, 250.0, 0.95, 2.0]]], dtype=np.float32)
        assert self._run_predict(monkeypatch, output=out) == []

    def test_end_to_end_head_respects_confidence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        out = np.array([[[80.0, 90.0, 160.0, 250.0, 0.10, 0.0]]], dtype=np.float32)
        assert self._run_predict(monkeypatch, output=out, confidence=0.4) == []

    def test_end_to_end_head_keeps_overlapping_people_without_nms(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Two heavily-overlapping person boxes: the end-to-end head already
        # de-duplicated, so predict must NOT re-run NMS and collapse them.
        out = np.array(
            [
                [
                    [80.0, 90.0, 160.0, 250.0, 0.90, 0.0],
                    [85.0, 95.0, 165.0, 255.0, 0.80, 0.0],
                ]
            ],
            dtype=np.float32,
        )
        boxes = self._run_predict(monkeypatch, output=out)
        assert len(boxes) == 2


class TestAutoSizeFromModel:
    """The detector adopts the model's own fixed input size so the operator
    never has to match ``inference_size`` to the export."""

    def test_inference_size_adopts_model_input_size(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Fake session reports a 320x320 input; the config asks for 640.
        _install_fake_onnxruntime(monkeypatch)
        det = PersonDetector(_cfg(enabled=True, model="x.onnx", inference_size=640))
        assert det.available is True
        assert det._inference_size == 320  # model wins over the configured 640
        assert det.input_resolution == (320, 240)

    def test_inference_size_kept_when_model_axis_dynamic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A dynamic input axis reports a non-int dim -> model_input_size is None,
        # so the configured size stands.
        class _DynSession(_FakeOnnxSession):
            def get_inputs(self) -> list[types.SimpleNamespace]:
                return [types.SimpleNamespace(name="images", shape=(1, 3, "height", "width"))]

        _install_fake_onnxruntime(monkeypatch, session_factory=lambda *a, **kw: _DynSession())
        det = PersonDetector(_cfg(enabled=True, model="x.onnx", inference_size=512))
        assert det.available is True
        assert det._inference_size == 512  # configured fallback


# --------------------------------------------------------------------------- #
# PersonDetector lifecycle + properties
# --------------------------------------------------------------------------- #


class TestSetAppsinkAndStart:
    def test_set_appsink_stores_reference(self) -> None:
        det = _detector_without_backend()
        sentinel = object()
        det.set_appsink(sentinel)
        assert det._appsink is sentinel

    def test_start_happy_path_arms_thread_and_resets_counters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        det = _detector_without_backend()
        det._backend = MagicMock()  # pretend a backend loaded
        det._appsink = MagicMock()
        # Pre-seed some stats to confirm they are reset.
        det._inference_count = 42
        det._inference_errors = 3
        det._sample_timeouts = 99

        started_threads: list[threading.Thread] = []

        class _RecordingThread(threading.Thread):
            def start(self: threading.Thread) -> None:  # type: ignore[override]
                started_threads.append(self)

        monkeypatch.setattr(detection_module.threading, "Thread", _RecordingThread)

        det.start()
        assert det._running is True
        assert det._thread is not None
        assert started_threads == [det._thread]
        assert det._inference_count == 0
        assert det._inference_errors == 0
        assert det._sample_timeouts == 0
        assert det._started_at_monotonic is not None

    def test_stop_joins_thread_and_clears_reference(self) -> None:
        det = _detector_without_backend()
        joined = {"count": 0}

        class _DummyThread:
            def join(self, timeout: float = 0.0) -> None:
                joined["count"] += 1

        det._running = True
        det._thread = _DummyThread()  # type: ignore[assignment]
        det.stop()
        assert det._running is False
        assert det._thread is None
        assert joined["count"] == 1

    def test_start_is_idempotent_while_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A second start() without an intervening stop() must not spawn a
        second worker (which would orphan the first and race the appsink)."""
        det = _detector_without_backend()
        det._backend = MagicMock()
        det._appsink = MagicMock()

        created: list[threading.Thread] = []

        class _RecordingThread(threading.Thread):
            def start(self: threading.Thread) -> None:  # type: ignore[override]
                created.append(self)

        monkeypatch.setattr(detection_module.threading, "Thread", _RecordingThread)

        det.start()
        first_thread = det._thread
        assert len(created) == 1

        det.start()  # already running → must be a no-op
        assert det._thread is first_thread
        assert len(created) == 1

    def test_stop_closes_and_releases_backend(self) -> None:
        """stop() releases native backend resources deterministically."""
        det = _detector_without_backend()
        closed = {"n": 0}

        class _ClosableBackend:
            def close(self) -> None:
                closed["n"] += 1

        det._backend = _ClosableBackend()  # type: ignore[assignment]
        det._backend_name = "onnx"
        det.stop()
        assert closed["n"] == 1
        assert det._backend is None
        # Name cleared too, so performance_stats can't report a stale backend.
        assert det._backend_name is None
        assert det.available is False
        assert det.performance_stats["backend"] == "none"

    def test_stop_tolerates_backend_without_close(self) -> None:
        det = _detector_without_backend()
        det._backend = object()  # type: ignore[assignment]  # no close()
        det.stop()  # must not raise
        assert det._backend is None

    def test_available_and_stats_reflect_backend_state(self) -> None:
        """``available`` and ``performance_stats`` read the backend triple
        consistently (under ``_backend_lock``)."""
        det = _detector_without_backend()
        assert det.available is False
        assert det.performance_stats["available"] is False

        det._backend = MagicMock()
        det._backend_name = "onnx"
        det._model_path = "/models/yolov8n.onnx"

        assert det.available is True
        stats = det.performance_stats
        assert stats["available"] is True
        assert stats["backend"] == "onnx"
        assert stats["model"] == "/models/yolov8n.onnx"


# --------------------------------------------------------------------------- #
# _preprocess CLAHE path
# --------------------------------------------------------------------------- #


class _FakeClahe:
    def __init__(self) -> None:
        self.apply_calls: list[np.ndarray] = []

    def apply(self, channel: np.ndarray) -> np.ndarray:
        self.apply_calls.append(channel)
        return channel  # no-op transform


class TestPreprocessClahePath:
    def test_clahe_applied_to_l_channel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CLAHE is unconditional: ``_preprocess`` always equalises the LAB
        L channel. Substitute ``cv2.cvtColor`` + ``_clahe.apply`` with fakes
        so it runs without a real OpenCV install and assert the L channel was
        passed through ``apply`` exactly once.
        """
        det = _detector_without_backend()
        fake_clahe = _FakeClahe()
        det._clahe = fake_clahe

        calls: list[tuple[str, int]] = []
        fake_cv2 = types.SimpleNamespace(
            COLOR_RGB2LAB=101,
            COLOR_LAB2RGB=102,
            cvtColor=lambda frame, flag: calls.append(("cvt", flag)) or frame.copy(),
        )
        monkeypatch.setattr(detection_module, "cv2", fake_cv2)

        frame = np.zeros((4, 4, 3), dtype=np.uint8)
        out = det._preprocess(frame)

        assert len(fake_clahe.apply_calls) == 1
        assert any(flag == 101 for _, flag in calls)
        assert any(flag == 102 for _, flag in calls)
        assert out.shape == frame.shape


# --------------------------------------------------------------------------- #
# tracked_detection – pinned-but-missing release branch
# --------------------------------------------------------------------------- #


class TestTrackedDetectionRelease:
    def test_pinned_missing_from_tracked_falls_back_to_largest(self) -> None:
        det = _detector_without_backend()
        det._pinned_id = 42  # stale pin
        det._tracked = []  # no longer tracked
        visible = [
            DetectionBox(0.0, 0.0, 0.2, 0.2, confidence=0.8, track_id=10),
            DetectionBox(0.1, 0.1, 0.9, 0.9, confidence=0.7, track_id=11),
        ]
        det._results = visible

        picked = det.tracked_detection
        assert picked is not None
        assert picked.track_id == 11  # largest by area
        # Pin re-targeted to the new largest.
        assert det._pinned_id == 11

    def test_pinned_still_valid_returns_its_box(self) -> None:
        det = _detector_without_backend(grace_period_ms=1000)
        stale_box = DetectionBox(0.2, 0.2, 0.4, 0.4, confidence=0.9, track_id=7)
        det._pinned_id = 7
        det._tracked = [
            _TrackedPerson(track_id=7, box=stale_box, last_seen=time.monotonic()),
        ]
        det._results = []  # not visible this frame

        assert det.tracked_detection is stale_box

    def test_pinned_found_after_iterating_past_non_matches(self) -> None:
        det = _detector_without_backend(grace_period_ms=1000)
        now = time.monotonic()
        pinned_box = DetectionBox(0.4, 0.4, 0.6, 0.6, confidence=0.95, track_id=99)
        other_box = DetectionBox(0.0, 0.0, 0.2, 0.2, confidence=0.8, track_id=1)
        det._pinned_id = 99
        # Pinned id sits at index 1 – the loop must keep iterating
        # past the index-0 non-match to find it.
        det._tracked = [
            _TrackedPerson(track_id=1, box=other_box, last_seen=now),
            _TrackedPerson(track_id=99, box=pinned_box, last_seen=now),
        ]

        assert det.tracked_detection is pinned_box

    def test_reacquires_nearest_after_id_switch_not_largest(self) -> None:
        """The followed person's track was re-numbered: a *smaller* box reappears
        where they were while a *larger* unrelated person stands elsewhere. The
        pin must re-lock onto the nearest box (the same person), not the largest –
        this is the auto-follow-loses-a-still-visible-person regression."""
        det = _detector_without_backend()
        det._pinned_id = 5  # old id, now gone
        det._tracked = []  # track expired -> pin releases
        det._last_pinned_center = (0.35, 0.5)  # where we last followed them
        near_small = DetectionBox(0.30, 0.45, 0.40, 0.70, confidence=0.7, track_id=12)
        far_large = DetectionBox(0.60, 0.10, 0.95, 0.95, confidence=0.8, track_id=13)
        det._results = [far_large, near_small]

        picked = det.tracked_detection
        assert picked is near_small
        assert picked.track_id == 12
        assert det._pinned_id == 12

    def test_reacquire_falls_back_to_largest_when_old_target_gone(self) -> None:
        """Every visible detection is outside the re-acquire gate (the followed
        person has left the frame), so the pin falls back to the largest box."""
        det = _detector_without_backend()
        det._pinned_id = 5
        det._tracked = []
        det._last_pinned_center = (0.1, 0.1)  # old corner; nobody near it now
        small = DetectionBox(0.50, 0.50, 0.60, 0.60, confidence=0.7, track_id=20)
        big = DetectionBox(0.55, 0.10, 0.95, 0.95, confidence=0.6, track_id=21)
        det._results = [small, big]

        picked = det.tracked_detection
        assert picked is big
        assert det._pinned_id == 21

    def test_cold_start_seeds_centre_then_holds_same_person(self) -> None:
        """A cold-start pick (largest) seeds the last-followed centre, so the next
        call holds that same person (now re-numbered) over a larger newcomer –
        proving the centre is maintained through the public property."""
        det = _detector_without_backend()
        first = DetectionBox(0.30, 0.30, 0.50, 0.80, confidence=0.9, track_id=1)
        det._results = [first]
        det._tracked = []
        assert det.tracked_detection is first  # cold start: only box, seeds centre

        # Their track is re-numbered (1 -> 2) and a larger newcomer appears.
        moved = DetectionBox(0.31, 0.31, 0.49, 0.79, confidence=0.8, track_id=2)
        newcomer = DetectionBox(0.60, 0.05, 0.98, 0.98, confidence=0.85, track_id=3)
        det._results = [newcomer, moved]
        det._tracked = []  # old id 1 no longer present -> pin releases

        picked = det.tracked_detection
        assert picked is moved
        assert det._pinned_id == 2


# --------------------------------------------------------------------------- #
# _run_inner – loop matrix
# --------------------------------------------------------------------------- #


class _ScriptedAppsink:
    """Scripted appsink for driving ``_run_inner``.

    ``samples`` is a list: each entry is either a sample object (yielded
    by ``try_pull_sample``) or ``None`` (simulates a pull timeout).
    After the list is exhausted, ``try_pull_sample`` returns ``None``
    forever.  Callers should stop the loop via ``detector._running =
    False`` once the list is consumed.
    """

    def __init__(self, samples: list[Any]) -> None:
        self._samples = list(samples)
        self.pull_calls: list[int] = []

    def try_pull_sample(self, timeout_ns: int) -> Any:
        self.pull_calls.append(timeout_ns)
        if not self._samples:
            return None
        return self._samples.pop(0)


class _FakeBackendWithScriptedBoxes:
    def __init__(self, scripts: list[list[DetectionBox] | Exception]) -> None:
        self._scripts = list(scripts)
        self.predict_calls = 0

    def predict(
        self,
        *,
        frame: np.ndarray,
        confidence: float,
        max_persons: int,
        inference_size: int,
    ) -> list[DetectionBox]:
        self.predict_calls += 1
        if not self._scripts:
            return []
        script = self._scripts.pop(0)
        if isinstance(script, Exception):
            raise script
        return script


def _install_gi_repository_gst(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Make ``from gi.repository import Gst`` inside ``_run_inner`` resolve
    to a deterministic stub.  Isolated via ``sys.modules`` so the
    real ``gi`` never sees any mutation.
    """
    fake_gi = types.ModuleType("gi")
    fake_gi.require_version = lambda *_a, **_kw: None  # type: ignore[attr-defined]
    fake_repo = types.ModuleType("gi.repository")
    fake_repo.Gst = types.SimpleNamespace(
        MapFlags=types.SimpleNamespace(READ=1),
    )
    monkeypatch.setitem(sys.modules, "gi", fake_gi)
    monkeypatch.setitem(sys.modules, "gi.repository", fake_repo)
    return fake_repo.Gst


class TestRunInnerLoop:
    def _make_detector_for_run(
        self,
        *,
        backend: Any,
        appsink: _ScriptedAppsink,
        monkeypatch: pytest.MonkeyPatch,
    ) -> PersonDetector:
        """Build a detector wired for ``_run_inner`` with a deterministic
        perf-counter surface and our sample-to-numpy stub.
        """
        det = _detector_without_backend()
        det._backend = backend
        det._appsink = appsink
        det._running = True
        # Replace the buffer-map helper with a deterministic stub that
        # returns a 4x4 RGB frame regardless of sample shape.
        stub_frame = np.zeros((4, 4, 3), dtype=np.uint8)
        monkeypatch.setattr(
            PersonDetector,
            "_sample_to_numpy",
            staticmethod(lambda sample, _Gst: None if sample == "bad" else stub_frame.copy()),
        )
        return det

    def test_sample_none_increments_timeout_counter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_gi_repository_gst(monkeypatch)
        backend = _FakeBackendWithScriptedBoxes([])
        appsink = _ScriptedAppsink([None, None])
        det = self._make_detector_for_run(backend=backend, appsink=appsink, monkeypatch=monkeypatch)

        # Break out after the scripted pulls exhaust.
        pull_count = {"n": 0}
        real_pull = appsink.try_pull_sample

        def _counting_pull(timeout: int) -> Any:
            pull_count["n"] += 1
            if pull_count["n"] >= 2:
                det._running = False
            return real_pull(timeout)

        appsink.try_pull_sample = _counting_pull  # type: ignore[assignment]

        det._run_inner()
        assert det._sample_timeouts == 2
        assert backend.predict_calls == 0

    def test_sample_to_numpy_failure_increments_failure_counter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_gi_repository_gst(monkeypatch)
        backend = _FakeBackendWithScriptedBoxes([])
        appsink = _ScriptedAppsink(["bad", "bad"])
        det = self._make_detector_for_run(backend=backend, appsink=appsink, monkeypatch=monkeypatch)

        pull_count = {"n": 0}
        real_pull = appsink.try_pull_sample

        def _counting_pull(timeout: int) -> Any:
            pull_count["n"] += 1
            if pull_count["n"] >= 2:
                det._running = False
            return real_pull(timeout)

        appsink.try_pull_sample = _counting_pull  # type: ignore[assignment]

        det._run_inner()
        assert det._sample_failures == 2
        assert backend.predict_calls == 0

    def test_inference_error_increments_error_counter_and_continues(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        _install_gi_repository_gst(monkeypatch)
        backend = _FakeBackendWithScriptedBoxes(
            [
                RuntimeError("backend exploded"),
                [DetectionBox(0.0, 0.0, 0.5, 0.5, confidence=0.9)],
            ]
        )
        appsink = _ScriptedAppsink(["sample1", "sample2"])
        det = self._make_detector_for_run(backend=backend, appsink=appsink, monkeypatch=monkeypatch)

        pull_count = {"n": 0}
        real_pull = appsink.try_pull_sample

        def _counting_pull(timeout: int) -> Any:
            pull_count["n"] += 1
            if pull_count["n"] >= 2:
                det._running = False
            return real_pull(timeout)

        appsink.try_pull_sample = _counting_pull  # type: ignore[assignment]

        with caplog.at_level(logging.DEBUG, logger="openfollow.video.detection"):
            det._run_inner()

        assert det._inference_errors == 1
        assert det._inference_count == 1  # second call succeeded
        assert len(det._results) == 1

    def test_tracking_error_is_caught_and_loop_continues(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A tracker exception (e.g. a singular Kalman matrix → LinAlgError)
        must drop the frame and bump the error counter, not propagate out of
        the worker and kill it for the session."""
        _install_gi_repository_gst(monkeypatch)
        backend = _FakeBackendWithScriptedBoxes(
            [
                [DetectionBox(0.0, 0.0, 0.5, 0.5, confidence=0.9)],
                [DetectionBox(0.0, 0.0, 0.5, 0.5, confidence=0.9)],
            ]
        )
        appsink = _ScriptedAppsink(["s1", "s2"])
        det = self._make_detector_for_run(backend=backend, appsink=appsink, monkeypatch=monkeypatch)

        calls = {"n": 0}
        real_track = det._track

        def _flaky_track(boxes: list[DetectionBox]) -> list[DetectionBox]:
            calls["n"] += 1
            if calls["n"] == 1:
                raise np.linalg.LinAlgError("singular matrix")
            det._running = False  # stop after the recovering frame
            return real_track(boxes)

        monkeypatch.setattr(det, "_track", _flaky_track)

        det._run_inner()  # must NOT propagate the LinAlgError

        assert calls["n"] == 2  # loop continued past the tracker error
        assert det._inference_errors == 1
        assert det._inference_count == 1  # the recovering frame succeeded

    def test_happy_path_updates_all_counters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_gi_repository_gst(monkeypatch)
        boxes = [DetectionBox(0.1, 0.1, 0.4, 0.4, confidence=0.8)]
        backend = _FakeBackendWithScriptedBoxes([boxes])
        appsink = _ScriptedAppsink(["s1"])
        det = self._make_detector_for_run(backend=backend, appsink=appsink, monkeypatch=monkeypatch)

        # Terminate after the one scripted sample.
        real_pull = appsink.try_pull_sample

        def _one_then_stop(timeout: int) -> Any:
            out = real_pull(timeout)
            det._running = False  # exit after this iteration
            return out

        appsink.try_pull_sample = _one_then_stop  # type: ignore[assignment]

        det._run_inner()
        assert det._inference_count == 1
        assert det._inference_last_ms >= 0.0
        assert det._detections_last == 1
        assert det._detections_total == 1
        assert det._last_inference_monotonic is not None
        assert len(det._results) == 1

    def test_backend_none_returns_immediately(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_gi_repository_gst(monkeypatch)
        det = _detector_without_backend()
        det._backend = None
        det._running = True
        appsink = _ScriptedAppsink([])
        det._appsink = appsink
        det._run_inner()  # must return without touching appsink
        assert appsink.pull_calls == []


class TestRunOuterGuard:
    def test_run_logs_exception_when_inner_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        det = _detector_without_backend()

        def _boom() -> None:
            raise RuntimeError("run_inner exploded")

        monkeypatch.setattr(det, "_run_inner", _boom)

        with caplog.at_level(logging.ERROR, logger="openfollow.video.detection"):
            det._run()

        assert any("PersonDetector thread crashed" in r.message for r in caplog.records)

    def test_run_clears_running_after_inner_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A crashed worker must stop advertising ``running: True`` –
        otherwise performance_stats reports a live worker forever while no
        detections are produced."""
        det = _detector_without_backend()
        det._running = True

        def _boom() -> None:
            raise RuntimeError("run_inner exploded")

        monkeypatch.setattr(det, "_run_inner", _boom)
        det._run()

        assert det._running is False
        assert det.performance_stats["running"] is False

    def test_run_clears_running_on_clean_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        det = _detector_without_backend()
        det._running = True
        monkeypatch.setattr(det, "_run_inner", lambda: None)
        det._run()
        assert det._running is False


# --------------------------------------------------------------------------- #
# _track – greedy matching continue-on-matched branch
# --------------------------------------------------------------------------- #


class TestTrackGreedyMatching:
    def test_second_overlapping_detection_spawns_new_track(self) -> None:
        det = _detector_without_backend()  # confidence=0.4
        # Frame 1: establish one track (id 0).
        first = det._track([DetectionBox(0.0, 0.0, 0.5, 0.5, confidence=0.9)])
        established_id = first[0].track_id

        # Frame 2: two detections both overlap the existing track – the greedy
        # matcher binds the higher-IoU one to it (continue skips the loser), and
        # the unmatched high detection must spawn a NEW track.
        matched = det._track(
            [
                DetectionBox(0.02, 0.02, 0.52, 0.52, confidence=0.9),  # higher IoU -> existing
                DetectionBox(0.10, 0.10, 0.60, 0.60, confidence=0.85),  # overlaps too, loses -> new
            ]
        )
        track_ids = sorted(m.track_id for m in matched)
        assert established_id in track_ids
        assert len(matched) == 2
        assert len(set(track_ids)) == 2  # one kept, one fresh


# --------------------------------------------------------------------------- #
# _prepare_model_path – probe-write OSError
# --------------------------------------------------------------------------- #


class TestPrepareModelPathProbeFailure:
    def test_probe_write_oserror_propagates(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """``storage_path`` refers to a writable-looking directory but
        the probe-write step raises – the error surfaces to the caller
        so ``PersonDetector.__init__`` can log + bail out instead of
        silently proceeding into a detector that can't cache models.
        """
        from pathlib import Path as _P

        storage = tmp_path / "storage"

        def _failing_write(self: _P, *a: Any, **kw: Any) -> None:
            if self.name.startswith(".openfollow-write-test"):
                raise OSError("disk full")
            # Real write for non-probe files (mkdir touches).
            return None

        monkeypatch.setattr(_P, "write_text", _failing_write)
        cfg = _cfg(
            enabled=True,
            model="yolov8n.onnx",
            storage_path=str(storage),
        )
        with pytest.raises(OSError):
            PersonDetector._prepare_model_path(cfg)


# --------------------------------------------------------------------------- #
# __init__ storage-failure branch (covers lines 423-429)
# --------------------------------------------------------------------------- #


class TestInitStorageFailure:
    def test_storage_failure_logs_error_and_leaves_backend_none(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:

        def _boom(_cfg: DetectionConfig) -> str:
            raise OSError("read-only fs")

        monkeypatch.setattr(PersonDetector, "_prepare_model_path", staticmethod(_boom))

        cfg = _cfg(enabled=True, storage_path="/nonexistent/ro")
        with caplog.at_level("ERROR", logger="openfollow.video.detection"):
            det = PersonDetector(cfg)

        assert det.available is False
        assert any(
            "Failed to initialize detection storage" in r.message for r in caplog.records if r.levelname == "ERROR"
        )

    def test_value_error_from_prepare_model_path_is_caught(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = _cfg(enabled=True, model="")
        det = PersonDetector(cfg)
        assert det.available is False


class TestBackendClose:
    """close() drops the native handle so stop() can release it eagerly."""

    def test_onnx_backend_close_releases_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_onnxruntime(monkeypatch)
        backend = _OnnxBackend("models/yolov8n.onnx")
        assert backend._session is not None
        backend.close()
        assert backend._session is None
