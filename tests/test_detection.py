# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for person detection: NMS, ByteTrack track-ID stickiness, and box post-processing."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from openfollow.configuration import DetectionConfig

pytestmark = pytest.mark.unit


def _load_detection_module():
    pytest.importorskip("cv2")
    import openfollow.video.detection as detection_module

    return detection_module


def test_track_keeps_track_id_stable() -> None:
    detection_module = _load_detection_module()
    detector = detection_module.PersonDetector(DetectionConfig(enabled=False))

    first = detector._track([detection_module.DetectionBox(0.10, 0.10, 0.40, 0.60, 0.9)])
    second = detector._track([detection_module.DetectionBox(0.11, 0.11, 0.41, 0.61, 0.8)])

    assert len(first) == 1
    assert len(second) == 1
    assert second[0].track_id == first[0].track_id


def test_check_detection_dependencies_reports_cv2_when_import_failed(monkeypatch) -> None:
    import openfollow.video.detection as detection_module

    monkeypatch.setattr(detection_module, "_CV2_IMPORT_ERROR", "simulated cv2 gone")

    missing = detection_module.check_detection_dependencies(
        DetectionConfig(model="yolov8n.onnx"),
    )
    assert "opencv-python" in missing


def test_check_detection_dependencies_reports_missing_onnxruntime(monkeypatch) -> None:
    import openfollow.video.detection as detection_module

    # cv2 present, onnxruntime absent – its absence is reported.
    monkeypatch.setattr(detection_module, "_CV2_IMPORT_ERROR", None)

    def _fake_find_spec(name: str):
        return None if name == "onnxruntime" else object()

    monkeypatch.setattr(detection_module.importlib.util, "find_spec", _fake_find_spec)

    assert detection_module.check_detection_dependencies(
        DetectionConfig(model="yolov8n.onnx"),
    ) == ["onnxruntime"]


def test_check_detection_dependencies_empty_when_all_present(monkeypatch) -> None:
    import openfollow.video.detection as detection_module

    monkeypatch.setattr(detection_module, "_CV2_IMPORT_ERROR", None)
    monkeypatch.setattr(detection_module.importlib.util, "find_spec", lambda name: object())

    # Works with or without a config; the config does not change the result.
    assert detection_module.check_detection_dependencies() == []
    assert detection_module.check_detection_dependencies(DetectionConfig(model="x.onnx")) == []


def test_tracked_detection_obeys_grace_period_then_falls_back(monkeypatch) -> None:
    detection_module = _load_detection_module()
    cfg = DetectionConfig(enabled=False, grace_period_ms=500)
    detector = detection_module.PersonDetector(cfg)

    pinned_box = detection_module.DetectionBox(0.2, 0.2, 0.5, 0.8, 0.95, track_id=7)
    detector._tracked = [detection_module._TrackedPerson(track_id=7, box=pinned_box, last_seen=10.0)]
    detector._pinned_id = 7
    monkeypatch.setattr(detection_module.time, "monotonic", lambda: 10.2)

    assert detector.tracked_detection is pinned_box

    fallback_box = detection_module.DetectionBox(0.1, 0.1, 0.9, 0.9, 0.7, track_id=42)
    detector._results = [fallback_box]
    detector._tracked = [detection_module._TrackedPerson(track_id=7, box=pinned_box, last_seen=9.0)]
    monkeypatch.setattr(detection_module.time, "monotonic", lambda: 10.0)

    chosen = detector.tracked_detection
    assert chosen is fallback_box
    assert detector._pinned_id == 42


def test_nms_suppresses_strong_overlap_and_keeps_non_overlapping() -> None:
    import openfollow.video.detection as detection_module

    boxes = np.array(
        [
            [0.0, 0.0, 1.0, 1.0],  # high score, kept
            [0.05, 0.05, 1.05, 1.05],  # ~88% IoU with #0, suppressed
            [5.0, 5.0, 6.0, 6.0],  # disjoint, kept
        ],
        dtype=np.float32,
    )
    scores = np.array([0.9, 0.8, 0.7], dtype=np.float32)

    keep = detection_module._nms(boxes, scores, iou_threshold=0.45)
    assert keep == [0, 2]


def test_nms_returns_empty_for_empty_inputs() -> None:
    import openfollow.video.detection as detection_module

    empty_boxes = np.empty((0, 4), dtype=np.float32)
    empty_scores = np.empty((0,), dtype=np.float32)
    assert detection_module._nms(empty_boxes, empty_scores) == []


def test_nms_orders_kept_indices_by_descending_score() -> None:
    import openfollow.video.detection as detection_module

    # Three disjoint boxes with deliberately-unsorted scores.
    boxes = np.array(
        [
            [0.0, 0.0, 1.0, 1.0],
            [2.0, 2.0, 3.0, 3.0],
            [4.0, 4.0, 5.0, 5.0],
        ],
        dtype=np.float32,
    )
    scores = np.array([0.3, 0.9, 0.6], dtype=np.float32)

    keep = detection_module._nms(boxes, scores, iou_threshold=0.45)
    assert keep == [1, 2, 0]


def test_prepare_predictions_converts_batch_channel_first_layout() -> None:
    import openfollow.video.detection as detection_module

    # Real YOLOv8 ONNX output is [1, 84, 8400]. _prepare_predictions should
    # drop the batch and transpose to [N, 84] so per-row access is sane.
    raw = np.zeros((1, 84, 1000), dtype=np.float32)
    out = detection_module._OnnxBackend._prepare_predictions(raw)
    assert out.shape == (1000, 84)


def test_prepare_predictions_transposes_channel_first_2d() -> None:
    import openfollow.video.detection as detection_module

    raw = np.zeros((84, 500), dtype=np.float32)
    out = detection_module._OnnxBackend._prepare_predictions(raw)
    assert out.shape == (500, 84)


def test_prepare_predictions_leaves_row_major_untouched() -> None:
    import openfollow.video.detection as detection_module

    raw = np.zeros((1000, 84), dtype=np.float32)
    out = detection_module._OnnxBackend._prepare_predictions(raw)
    assert out.shape == (1000, 84)


def test_prepare_predictions_returns_empty_for_unusable_shapes() -> None:
    import openfollow.video.detection as detection_module

    # 1D and 4D both miss the ``ndim == 2`` (after optional batch strip)
    # expectation and should collapse to an empty float32 array so the
    # caller's ``pred.size == 0`` guard trips safely.
    assert detection_module._OnnxBackend._prepare_predictions(np.zeros((84,))).size == 0
    assert detection_module._OnnxBackend._prepare_predictions(np.zeros((1, 1, 84, 100))).size == 0


def test_to_positive_int_accepts_only_positive_ints() -> None:
    import openfollow.video.detection as detection_module

    assert detection_module._OnnxBackend._to_positive_int(320) == 320
    # Dynamic ONNX dims are strings like "height"; treat as unknown.
    assert detection_module._OnnxBackend._to_positive_int("height") is None
    assert detection_module._OnnxBackend._to_positive_int(0) is None
    assert detection_module._OnnxBackend._to_positive_int(-5) is None


def test_extract_input_size_picks_max_of_h_w() -> None:
    import openfollow.video.detection as detection_module

    # NCHW shape with static dims returns max(h, w).
    assert detection_module._OnnxBackend._extract_input_size((1, 3, 480, 640)) == 640


def test_extract_input_size_returns_none_for_dynamic_or_short_shape() -> None:
    import openfollow.video.detection as detection_module

    assert detection_module._OnnxBackend._extract_input_size((1, 3)) is None
    assert detection_module._OnnxBackend._extract_input_size((1, 3, "height", "width")) is None


def test_letterbox_preserves_aspect_and_centers_padding() -> None:
    detection_module = _load_detection_module()

    # 200x100 image (landscape) at target 320 – scale is 320/200=1.6, so
    # final inner size is 320x160 with 80px of padding on top and bottom.
    src = np.full((100, 200, 3), 255, dtype=np.uint8)
    padded, scale, (pad_x, pad_y) = detection_module._letterbox(src, 320)

    assert padded.shape == (320, 320, 3)
    assert scale == pytest.approx(1.6)
    assert (pad_x, pad_y) == (0, 80)
    # Padding band must be the neutral 114 used by YOLO preprocessing.
    assert padded[0, 0, 0] == 114
    # Interior must be the resized content (white), not padding.
    assert padded[160, 160, 0] == 255


def test_letterbox_handles_zero_sized_input() -> None:
    detection_module = _load_detection_module()

    src = np.zeros((0, 0, 3), dtype=np.uint8)
    padded, scale, offsets = detection_module._letterbox(src, 320)
    assert padded.shape == (320, 320, 3)
    assert scale == 1.0
    assert offsets == (0, 0)


def test_percentile_returns_zero_for_empty() -> None:
    import openfollow.video.detection as detection_module

    assert detection_module.PersonDetector._percentile([], 95.0) == 0.0


def test_percentile_returns_expected_sample() -> None:
    import openfollow.video.detection as detection_module

    # nearest-rank at p95 over 0..99 is idx=round(0.95*99)=94 → value 94.
    values = [float(v) for v in range(100)]
    assert detection_module.PersonDetector._percentile(values, 95.0) == 94.0


def test_normalize_inference_size_snaps_to_multiple_of_32() -> None:
    import openfollow.video.detection as detection_module

    assert detection_module.PersonDetector._normalize_inference_size(500) == 480
    assert detection_module.PersonDetector._normalize_inference_size(320) == 320


def test_normalize_inference_size_clamps_to_bounds() -> None:
    import openfollow.video.detection as detection_module

    # Below 160 is clamped up; above 1280 is clamped down to a multiple of 32.
    assert detection_module.PersonDetector._normalize_inference_size(10) == 160
    assert detection_module.PersonDetector._normalize_inference_size(5000) == 1280


def test_performance_stats_shape_matches_services_template() -> None:
    """The detector's dict shape is the contract consumed by services.py's
    runtime-stats fallback; if a key is renamed here without updating the
    fallback, the web UI silently renders 0/None instead of live values."""
    import openfollow.video.detection as detection_module

    detector = detection_module.PersonDetector(DetectionConfig(enabled=False))
    stats = detector.performance_stats

    expected_keys = {
        "enabled",
        "available",
        "running",
        "model",
        "interval_ms",
        "inference_count",
        "inference_hz",
        "inference_avg_ms",
        "inference_p95_ms",
        "inference_max_ms",
        "inference_last_ms",
        "inference_errors",
        "sample_timeouts",
        "sample_failures",
        "detections_last",
        "detections_avg",
        "tracked_people",
        "pinned_track_id",
        "last_inference_age_ms",
    }
    assert expected_keys.issubset(stats.keys())
    assert stats["enabled"] is False
    assert stats["available"] is False
    assert stats["running"] is False
    assert stats["pinned_track_id"] is None
    assert stats["last_inference_age_ms"] is None


def test_track_assigns_fresh_ids_to_new_detections() -> None:
    detection_module = _load_detection_module()
    detector = detection_module.PersonDetector(DetectionConfig(enabled=False))

    boxes = [
        detection_module.DetectionBox(0.1, 0.1, 0.2, 0.3, 0.9),
        detection_module.DetectionBox(0.6, 0.6, 0.7, 0.8, 0.8),
    ]
    tracked = detector._track(boxes)

    assert len(tracked) == 2
    ids = {t.track_id for t in tracked}
    assert len(ids) == 2  # two distinct ids
    assert -1 not in ids  # sentinel replaced by real id


def test_track_caps_results_to_highest_confidence() -> None:
    detection_module = _load_detection_module()
    detector = detection_module.PersonDetector(DetectionConfig(enabled=False, max_persons=2))

    boxes = [
        detection_module.DetectionBox(0.00, 0.0, 0.10, 0.30, 0.30),  # lowest conf
        detection_module.DetectionBox(0.30, 0.0, 0.45, 0.40, 0.90),  # highest conf
        detection_module.DetectionBox(0.70, 0.0, 0.85, 0.50, 0.50),  # mid conf
    ]
    tracked = detector._track(boxes)

    # Capped to max_persons, keeping the highest-confidence detections (the
    # documented "Max Persons ... (highest-confidence first)" contract).
    assert len(tracked) == 2
    assert sorted(round(b.confidence, 2) for b in tracked) == [0.50, 0.90]


def test_track_carries_lost_track_within_grace_period(monkeypatch) -> None:
    detection_module = _load_detection_module()
    cfg = DetectionConfig(enabled=False, grace_period_ms=500)
    detector = detection_module.PersonDetector(cfg)

    monkeypatch.setattr(detection_module.time, "monotonic", lambda: 100.0)
    first = detector._track([detection_module.DetectionBox(0.1, 0.1, 0.4, 0.6, 0.9)])
    assert len(first) == 1
    first_id = first[0].track_id

    # 200ms later (inside grace) with no detections – the track must still
    # exist internally even though it's not returned as a "fresh" match.
    monkeypatch.setattr(detection_module.time, "monotonic", lambda: 100.2)
    empty = detector._track([])
    assert empty == []
    assert any(t.track_id == first_id for t in detector._tracked)

    # Past grace period – the carry-over drops.
    monkeypatch.setattr(detection_module.time, "monotonic", lambda: 101.0)
    gone = detector._track([])
    assert gone == []
    assert all(t.track_id != first_id for t in detector._tracked)


# ---------------------------------------------------------------------------
# Live config-apply
# ---------------------------------------------------------------------------


def test_reload_config_stages_pending_for_worker_drain() -> None:
    """``reload_config`` runs on the GTK thread; it stages the new
    config under ``_config_lock`` for the worker to pick up between
    frames. The single-slot semantics mean two consecutive updates
    collapse to the latest one."""
    detection_module = _load_detection_module()
    detector = detection_module.PersonDetector(DetectionConfig(enabled=False))

    new_a = DetectionConfig(enabled=False, confidence=0.42)
    detector.reload_config(new_a)
    assert detector._pending_config is new_a

    new_b = DetectionConfig(enabled=False, confidence=0.66)
    detector.reload_config(new_b)
    assert detector._pending_config is new_b


def test_drain_swaps_hot_path_fields_without_rebuild() -> None:
    """Hot-path fields (confidence, max_persons, interval_ms,
    grace_period_ms) are read inside the worker loop on every frame;
    the drain swaps them in place with no backend rebuild."""
    detection_module = _load_detection_module()
    detector = detection_module.PersonDetector(DetectionConfig(enabled=False))
    new_cfg = DetectionConfig(
        enabled=False,
        confidence=0.85,
        max_persons=3,
        interval_ms=50,
        grace_period_ms=1000,
    )
    detector._pending_config = new_cfg

    rebuild_calls: list[str | None] = []

    def _spy_load(base_model_path: str | None = None) -> None:
        rebuild_calls.append(base_model_path)

    detector._load_backend = _spy_load  # type: ignore[method-assign]

    detector._drain_pending_config()

    assert detector._config is new_cfg
    assert detector._pending_config is None
    assert rebuild_calls == []


def test_drain_does_not_live_swap_inference_size() -> None:
    detection_module = _load_detection_module()
    detector = detection_module.PersonDetector(
        DetectionConfig(enabled=False, inference_size=320),
    )
    assert detector._inference_size == 320

    # Pending config has a different inference_size but no other
    # cold-path field changed (so the rebuild branch doesn't fire).
    detector._pending_config = DetectionConfig(enabled=False, inference_size=640)
    detector._drain_pending_config()

    # Worker kept the prior derived value – the pipeline caps haven't
    # changed, so neither does the backend's effective input size.
    assert detector._inference_size == 320


def test_drain_recomputes_clahe_when_toggled() -> None:
    detection_module = _load_detection_module()
    detector = detection_module.PersonDetector(
        DetectionConfig(enabled=False, preprocess_clahe=False),
    )
    assert detector._clahe is None

    detector._pending_config = DetectionConfig(enabled=False, preprocess_clahe=True)
    detector._drain_pending_config()
    assert detector._clahe is not None

    detector._pending_config = DetectionConfig(enabled=False, preprocess_clahe=False)
    detector._drain_pending_config()
    assert detector._clahe is None


def test_drain_rebuilds_backend_when_model_changes() -> None:
    """Cold-path fields (model, storage_path) require a fresh inference
    session. The rebuild happens on the worker thread so the GTK thread
    isn't blocked by an ONNX session load. The drain detects that
    ``_load_backend`` actually loaded a new backend by identity-comparing
    against the captured prior one."""
    detection_module = _load_detection_module()
    detector = detection_module.PersonDetector(
        DetectionConfig(enabled=False, model="yolov8n.onnx"),
    )

    rebuild_calls: list[str | None] = []
    new_backend = object()

    def _spy_load(base_model_path: str | None = None) -> None:
        rebuild_calls.append(base_model_path)
        # Mirror the real ``_load_backend`` contract: assign
        # ``self._backend`` on a successful load. The drain checks
        # this identity to detect silent-no-load failures.
        detector._backend = new_backend  # type: ignore[assignment]

    detector._load_backend = _spy_load  # type: ignore[method-assign]
    detector._prepare_model_path = lambda cfg: cfg.model  # type: ignore[method-assign]

    new_cfg = DetectionConfig(enabled=False, model="yolov8s.onnx")
    detector._pending_config = new_cfg
    detector._drain_pending_config()

    assert rebuild_calls == ["yolov8s.onnx"]
    assert detector._config is new_cfg
    assert detector._backend is new_backend


def test_drain_keeps_prior_config_when_backend_rebuild_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    detection_module = _load_detection_module()
    old_cfg = DetectionConfig(enabled=False, model="yolov8n.onnx")
    detector = detection_module.PersonDetector(old_cfg)
    sentinel_backend = object()
    detector._backend = sentinel_backend  # type: ignore[assignment]
    detector._backend_name = "onnx-old"
    detector._model_path = "/old/path/yolov8n.onnx"

    def _raising_load(_base_model_path: str | None = None) -> None:
        raise OSError("simulated session load failure")

    detector._load_backend = _raising_load  # type: ignore[method-assign]
    detector._prepare_model_path = lambda cfg: f"/new/{cfg.model}"  # type: ignore[method-assign]

    detector._pending_config = DetectionConfig(enabled=False, model="yolov8s.onnx")
    with caplog.at_level("ERROR", logger="openfollow.video.detection"):
        detector._drain_pending_config()

    assert detector._config is old_cfg
    assert detector._backend is sentinel_backend
    # ``_backend_name`` and ``_model_path`` must roll back too – they
    # may have been mutated mid-rebuild before the raise.
    assert detector._backend_name == "onnx-old"
    assert detector._model_path == "/old/path/yolov8n.onnx"
    assert any("backend rebuild failed" in r.message for r in caplog.records)


def test_drain_keeps_prior_config_when_load_backend_silently_loads_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``_load_backend`` doesn't raise when every attempt fails – it
    logs a warning and returns with ``self._backend`` unchanged. The
    drain must detect that case (by identity-comparing the post-call
    ``_backend`` against the captured prior one) and roll back, or
    we end up with a config saying "new model" while the backend still
    serves the old one."""
    detection_module = _load_detection_module()
    old_cfg = DetectionConfig(enabled=False, model="yolov8n.onnx")
    detector = detection_module.PersonDetector(old_cfg)
    sentinel_backend = object()
    detector._backend = sentinel_backend  # type: ignore[assignment]
    detector._backend_name = "onnx-old"
    detector._model_path = "/old/path/yolov8n.onnx"

    def _silent_no_load(_base_model_path: str | None = None) -> None:
        # Mirrors real ``_load_backend`` when every attempt's import /
        # session-load fails: log + return without raising and without
        # assigning ``self._backend``.
        return None

    detector._load_backend = _silent_no_load  # type: ignore[method-assign]
    detector._prepare_model_path = lambda cfg: f"/new/{cfg.model}"  # type: ignore[method-assign]

    detector._pending_config = DetectionConfig(enabled=False, model="yolov8s.onnx")
    with caplog.at_level("ERROR", logger="openfollow.video.detection"):
        detector._drain_pending_config()

    assert detector._config is old_cfg
    assert detector._backend is sentinel_backend
    assert detector._backend_name == "onnx-old"
    assert detector._model_path == "/old/path/yolov8n.onnx"
    assert any("loaded no new backend" in r.message for r in caplog.records)


def test_drain_keeps_prior_config_when_model_path_resolution_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``_prepare_model_path`` runs before the backend rebuild; if it
    raises (storage permission denied, missing file, etc.) the swap
    is aborted before any side effect."""
    detection_module = _load_detection_module()
    old_cfg = DetectionConfig(enabled=False, model="yolov8n.onnx")
    detector = detection_module.PersonDetector(old_cfg)

    def _raising_prepare(_cfg: DetectionConfig) -> str:
        raise OSError("simulated storage failure")

    detector._prepare_model_path = _raising_prepare  # type: ignore[method-assign]

    detector._pending_config = DetectionConfig(enabled=False, model="yolov8s.onnx")
    with caplog.at_level("ERROR", logger="openfollow.video.detection"):
        detector._drain_pending_config()

    assert detector._config is old_cfg
    assert any("model-path resolution failed" in r.message for r in caplog.records)


def test_drain_with_no_pending_is_noop() -> None:
    """No pending config → drain returns without touching any state."""
    detection_module = _load_detection_module()
    detector = detection_module.PersonDetector(DetectionConfig(enabled=False))
    sentinel = detector._config

    detector._drain_pending_config()

    assert detector._config is sentinel
    assert detector._pending_config is None


def test_drain_restores_env_vars_when_backend_rebuild_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``_prepare_model_path`` mutates ``XDG_CACHE_HOME`` when
    ``storage_path`` is set so the ONNX runtime cache lands in the
    operator's chosen directory. If ``_load_backend`` then raises, the
    rollback restores ``_backend`` / ``_backend_name`` / ``_model_path`` –
    but the env var persists process-wide. Without restoring it too, the
    process ends up pointing at the new cache dir while inference still
    runs on the old session."""
    import os as _os

    detection_module = _load_detection_module()
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)

    old_cfg = DetectionConfig(enabled=False, model="yolov8n.onnx")
    detector = detection_module.PersonDetector(old_cfg)
    sentinel_backend = object()
    detector._backend = sentinel_backend  # type: ignore[assignment]

    def _raising_load(_base_model_path: str | None = None) -> None:
        raise OSError("simulated session load failure")

    detector._load_backend = _raising_load  # type: ignore[method-assign]

    detector._pending_config = DetectionConfig(
        enabled=False,
        model="yolov8s.onnx",
        storage_path=str(tmp_path),
    )
    detector._drain_pending_config()

    # Env vars rolled back to the unset state from before the swap.
    assert "XDG_CACHE_HOME" not in _os.environ
    assert detector._config is old_cfg


def test_drain_restores_env_vars_when_load_backend_silently_loads_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import os as _os

    detection_module = _load_detection_module()
    monkeypatch.setenv("XDG_CACHE_HOME", "/old/cache")

    old_cfg = DetectionConfig(enabled=False, model="yolov8n.onnx")
    detector = detection_module.PersonDetector(old_cfg)
    sentinel_backend = object()
    detector._backend = sentinel_backend  # type: ignore[assignment]

    def _silent_no_load(_base_model_path: str | None = None) -> None:
        return None

    detector._load_backend = _silent_no_load  # type: ignore[method-assign]

    detector._pending_config = DetectionConfig(
        enabled=False,
        model="yolov8s.onnx",
        storage_path=str(tmp_path),
    )
    detector._drain_pending_config()

    # Pre-existing env value restored, NOT left at the new cache dir.
    assert _os.environ["XDG_CACHE_HOME"] == "/old/cache"
    assert detector._config is old_cfg


def test_drain_restores_env_vars_when_pre_existing_values_present(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import os as _os

    detection_module = _load_detection_module()
    monkeypatch.setenv("XDG_CACHE_HOME", "/operator/cache")

    old_cfg = DetectionConfig(enabled=False, model="yolov8n.onnx")
    detector = detection_module.PersonDetector(old_cfg)
    sentinel_backend = object()
    detector._backend = sentinel_backend  # type: ignore[assignment]

    def _raising_load(_base_model_path: str | None = None) -> None:
        raise OSError("simulated")

    detector._load_backend = _raising_load  # type: ignore[method-assign]

    detector._pending_config = DetectionConfig(
        enabled=False,
        model="yolov8s.onnx",
        storage_path=str(tmp_path),
    )
    detector._drain_pending_config()

    assert _os.environ["XDG_CACHE_HOME"] == "/operator/cache"


def test_drain_restores_env_baseline_on_storage_path_cleared(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Transition ``storage_path: "/foo" → ""``: the worker rebuilds
    the backend (model path computed without redirection) and the
    operator's intent is "no cache redirection any more". But
    ``_prepare_model_path`` returns early without mutating env vars
    when ``storage_path`` is empty. Drain must restore init-time baseline
    on success path of empty-transition swap.
    """
    import os as _os

    detection_module = _load_detection_module()
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)

    # Init with storage_path=/foo so init-time mutates env to redirect.
    initial_storage = tmp_path / "initial"
    initial_storage.mkdir()
    old_cfg = DetectionConfig(
        enabled=False,
        model="yolov8n.onnx",
        storage_path=str(initial_storage),
    )
    detector = detection_module.PersonDetector(old_cfg)
    # Sanity: detector __init__ captured the (empty) baseline BEFORE
    # any redirect mutation.
    assert detector._initial_storage_env["XDG_CACHE_HOME"] is None

    # Stub out backend wiring so the rebuild "succeeds" without needing
    # a real ONNX session.
    new_backend = object()

    def _spy_load(_base_model_path: str | None = None) -> None:
        detector._backend = new_backend  # type: ignore[assignment]

    detector._load_backend = _spy_load  # type: ignore[method-assign]

    # Pretend init-time redirection actually mutated env (which the
    # real ``_prepare_model_path`` would have done before this test
    # if it had a writeable storage dir).
    monkeypatch.setenv("XDG_CACHE_HOME", str(initial_storage / "cache"))

    # Now transition to ``storage_path=""`` – operator clears the
    # redirection. Drain should detect old.storage_path was non-empty
    # AND new is empty, and restore the baseline.
    detector._pending_config = DetectionConfig(
        enabled=False,
        model="yolov8n.onnx",
        storage_path="",
    )
    detector._drain_pending_config()

    # Env restored to the operator's pre-app baseline (unset).
    assert "XDG_CACHE_HOME" not in _os.environ
    assert detector._config.storage_path == ""
