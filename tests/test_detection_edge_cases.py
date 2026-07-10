# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Edge-case tests for ``openfollow.video.detection``.

Covers paths not exercised by ``test_detection.py``:

- ``PersonDetector._load_backend`` ONNX session construction with a
  monkeypatched ``_OnnxBackend`` – success, replaced-backend close,
  import failure, session failure.
- ``_prepare_model_path``: validation, env-var wiring, absolute-model
  passthrough.
- ``_sample_to_numpy`` success + the two failure paths (``buf.map``
  returns False, reshape ``ValueError``).
- ``_preprocess`` short-circuit when CLAHE is disabled.
- ``start``/``stop`` guards and basic property accessors.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from openfollow.configuration import DetectionConfig

pytestmark = pytest.mark.unit


def _load_detection_module():
    """Import ``openfollow.video.detection`` without requiring cv2.

    The module catches ``ImportError`` and leaves ``cv2 = None``, and none
    of the code paths exercised in this file actually hit a cv2 call
    (``_preprocess`` short-circuits when CLAHE is off, ``_sample_to_numpy``
    uses numpy only).  A blanket ``importorskip("cv2")`` would silently
    skip every test here in environments without the ``detection`` extra.
    """
    import openfollow.video.detection as detection_module

    return detection_module


# ---------------------------------------------------------------------------
# _load_backend – ONNX session construction
# ---------------------------------------------------------------------------


def _install_fake_onnx_backend(
    monkeypatch,
    detection_module,
    record: list[str],
    results,
) -> None:
    """Replace ``_OnnxBackend`` so we observe the model path it's built with.

    ``results`` is an iterable of either backend-object returns or
    exceptions; the Nth construction consumes the Nth item in order.
    """
    iterator = iter(results)

    def _fake(model_path):  # noqa: ANN001, ANN202
        record.append(model_path)
        result = next(iterator)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(detection_module, "_OnnxBackend", _fake)


def test_load_backend_builds_onnx_session(monkeypatch) -> None:
    detection_module = _load_detection_module()
    detector = detection_module.PersonDetector(DetectionConfig(enabled=False))
    detector._model_path = "/tmp/weights.onnx"

    record: list[str] = []
    fake_backend = object()
    _install_fake_onnx_backend(monkeypatch, detection_module, record, [fake_backend])
    detector._load_backend()
    assert record == ["/tmp/weights.onnx"]
    assert detector._backend is fake_backend
    assert detector._backend_name == "onnx"


def test_load_backend_closes_replaced_backend_on_rebuild(monkeypatch) -> None:
    """A live rebuild closes the replaced backend's native resources so the
    prior ONNX session isn't orphaned across a show."""
    detection_module = _load_detection_module()
    detector = detection_module.PersonDetector(DetectionConfig(enabled=False))
    detector._model_path = "/tmp/weights.onnx"

    closed: list[bool] = []

    class _OldBackend:
        def predict(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN202
            return []

        def close(self) -> None:
            closed.append(True)

    detector._backend = _OldBackend()
    new_backend = object()
    _install_fake_onnx_backend(monkeypatch, detection_module, [], [new_backend])

    detector._load_backend()
    assert detector._backend is new_backend
    assert closed == [True]


def test_load_backend_swallows_replaced_backend_close_error(monkeypatch) -> None:
    """A failing close() on the replaced backend must not abort the rebuild."""
    detection_module = _load_detection_module()
    detector = detection_module.PersonDetector(DetectionConfig(enabled=False))
    detector._model_path = "/tmp/weights.onnx"

    class _BadCloseBackend:
        def predict(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN202
            return []

        def close(self) -> None:
            raise RuntimeError("close blew up")

    detector._backend = _BadCloseBackend()
    new_backend = object()
    _install_fake_onnx_backend(monkeypatch, detection_module, [], [new_backend])

    detector._load_backend()  # must not raise
    assert detector._backend is new_backend


def test_load_backend_handles_replaced_backend_without_close(monkeypatch) -> None:
    """A replaced backend with no close() is a no-op, not an error."""
    detection_module = _load_detection_module()
    detector = detection_module.PersonDetector(DetectionConfig(enabled=False))
    detector._model_path = "/tmp/weights.onnx"
    detector._backend = object()  # no close()
    new_backend = object()
    _install_fake_onnx_backend(monkeypatch, detection_module, [], [new_backend])

    detector._load_backend()
    assert detector._backend is new_backend


def test_load_backend_does_not_close_when_factory_returns_same_instance(monkeypatch) -> None:
    """Identity guard: if the factory ever returns the live instance,
    _load_backend must not close it out from under itself."""
    detection_module = _load_detection_module()
    detector = detection_module.PersonDetector(DetectionConfig(enabled=False))
    detector._model_path = "/tmp/weights.onnx"

    closed: list[bool] = []

    class _Backend:
        def predict(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN202
            return []

        def close(self) -> None:
            closed.append(True)

    same = _Backend()
    detector._backend = same
    _install_fake_onnx_backend(monkeypatch, detection_module, [], [same])

    detector._load_backend()
    assert detector._backend is same
    assert closed == []  # identity guard: not closed


def test_load_backend_uses_explicit_base_model_path(monkeypatch) -> None:
    """When given an explicit ``base_model_path`` (the live-rebuild path),
    the session resolves from that candidate – not the committed
    ``_model_path`` – and the triple is published to the candidate on success."""
    detection_module = _load_detection_module()
    detector = detection_module.PersonDetector(DetectionConfig(enabled=False))
    detector._model_path = "/old/committed.onnx"

    record: list[str] = []
    fake_backend = object()
    _install_fake_onnx_backend(monkeypatch, detection_module, record, [fake_backend])
    detector._load_backend("/candidate/new.onnx")

    assert record == ["/candidate/new.onnx"]  # candidate, not the old committed path
    assert detector._backend is fake_backend
    assert detector._model_path == "/candidate/new.onnx"  # committed on success


def test_load_backend_import_error_leaves_backend_none_and_warns(monkeypatch, caplog) -> None:
    """A missing onnxruntime surfaces the install warning and leaves the
    backend unset rather than raising."""
    import logging as _logging

    detection_module = _load_detection_module()
    detector = detection_module.PersonDetector(DetectionConfig(enabled=False))
    detector._model_path = "/tmp/missing.onnx"

    _install_fake_onnx_backend(monkeypatch, detection_module, [], [ImportError("no onnxruntime")])
    with caplog.at_level(_logging.WARNING, logger="openfollow.video.detection"):
        detector._load_backend()
    assert detector._backend is None
    assert any("onnxruntime not installed" in rec.message for rec in caplog.records)


def test_load_backend_session_failure_leaves_backend_none_and_logs(monkeypatch, caplog) -> None:
    """A non-ImportError failure (corrupt / missing model file) logs an error
    and leaves the backend unset rather than raising."""
    import logging as _logging

    detection_module = _load_detection_module()
    detector = detection_module.PersonDetector(DetectionConfig(enabled=False))
    detector._model_path = "/tmp/corrupt.onnx"

    _install_fake_onnx_backend(monkeypatch, detection_module, [], [RuntimeError("bad model")])
    with caplog.at_level(_logging.ERROR, logger="openfollow.video.detection"):
        detector._load_backend()
    assert detector._backend is None
    assert any("Failed to initialize ONNX detection backend" in rec.message for rec in caplog.records)


def test_person_detector_constructor_runs_load_backend_when_enabled(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """When ``DetectionConfig.enabled=True``, the constructor flows past the
    early-return into ``_load_backend`` and builds the ONNX session. Most
    other tests construct with ``enabled=False`` and call ``_load_backend``
    directly, leaving the constructor's call site uncovered."""
    detection_module = _load_detection_module()
    onnx = tmp_path / "w.onnx"
    onnx.write_bytes(b"o")

    record: list[str] = []
    _install_fake_onnx_backend(monkeypatch, detection_module, record, [object()])
    detector = detection_module.PersonDetector(
        DetectionConfig(enabled=True, model=str(onnx), storage_path=""),
    )
    # The constructor reached _load_backend and a backend was created.
    assert record == [str(onnx)]
    assert detector._backend is not None


# ---------------------------------------------------------------------------
# _prepare_model_path
# ---------------------------------------------------------------------------


def _snapshot_detection_env(monkeypatch) -> None:
    """Tell monkeypatch about the env vars that ``_prepare_model_path``
    mutates so they're restored on teardown regardless of what production
    code writes to them via direct ``os.environ[...]`` assignment."""
    monkeypatch.setenv("XDG_CACHE_HOME", os.environ.get("XDG_CACHE_HOME", ""))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)


def test_prepare_model_path_rejects_empty_model() -> None:
    detection_module = _load_detection_module()
    with pytest.raises(ValueError, match="model must not be empty"):
        detection_module.PersonDetector._prepare_model_path(
            DetectionConfig(enabled=False, model="   "),
        )


def test_prepare_model_path_blank_uses_cwd_yolo_when_unmounted(tmp_path: Path, monkeypatch) -> None:
    detection_module = _load_detection_module()
    _snapshot_detection_env(monkeypatch)
    # No NVMe mounted: a blank storage_path resolves models under <cwd>/yolo so
    # storage is always a concrete absolute location (no bare cwd model name).
    monkeypatch.setattr(os.path, "ismount", lambda _p: False)
    monkeypatch.chdir(tmp_path)
    out = detection_module.PersonDetector._prepare_model_path(
        DetectionConfig(enabled=False, model="yolov8n.onnx", storage_path=""),
    )
    storage = tmp_path / "yolo"
    assert out == str(storage / "models" / "yolov8n.onnx")
    assert (storage / "models").is_dir()
    assert os.environ["XDG_CACHE_HOME"] == str(storage / "cache")


def test_resolve_storage_explicit_path_wins(monkeypatch) -> None:
    detection_module = _load_detection_module()
    # Even with a mounted NVMe, an explicit value is returned verbatim (stripped).
    monkeypatch.setattr(os.path, "ismount", lambda _p: True)
    assert detection_module.resolve_detection_storage_path("  /data/models  ") == "/data/models"


def test_resolve_storage_blank_uses_nvme_when_mounted(monkeypatch) -> None:
    detection_module = _load_detection_module()
    monkeypatch.setattr(os.path, "ismount", lambda p: p == detection_module._NVME_MOUNTPOINT)
    assert detection_module.resolve_detection_storage_path("") == detection_module._NVME_DETECTION_STORAGE


def test_resolve_storage_blank_falls_back_to_cwd_yolo_when_unmounted(monkeypatch, tmp_path) -> None:
    detection_module = _load_detection_module()
    monkeypatch.setattr(os.path, "ismount", lambda _p: False)
    monkeypatch.chdir(tmp_path)
    # No NVMe -> an absolute <cwd>/yolo path so storage is always resolvable.
    assert detection_module.resolve_detection_storage_path("") == str(tmp_path / "yolo")


def test_resolve_storage_whitespace_only_treated_as_blank(monkeypatch, tmp_path) -> None:
    detection_module = _load_detection_module()
    monkeypatch.setattr(os.path, "ismount", lambda _p: False)
    monkeypatch.chdir(tmp_path)
    assert detection_module.resolve_detection_storage_path("   ") == str(tmp_path / "yolo")


def test_prepare_model_path_blank_redirects_to_mounted_nvme(tmp_path: Path, monkeypatch) -> None:
    """On a unit with the NVMe mounted, a blank storage_path resolves models
    under the NVMe location instead of the bare cwd model name."""
    detection_module = _load_detection_module()
    _snapshot_detection_env(monkeypatch)

    nvme_root = tmp_path / "nvme"
    storage = nvme_root / "openfollow" / "yolo"
    monkeypatch.setattr(detection_module, "_NVME_MOUNTPOINT", str(nvme_root))
    monkeypatch.setattr(detection_module, "_NVME_DETECTION_STORAGE", str(storage))
    monkeypatch.setattr(os.path, "ismount", lambda p: p == str(nvme_root))

    out = detection_module.PersonDetector._prepare_model_path(
        DetectionConfig(enabled=False, model="yolov8n.onnx", storage_path=""),
    )
    assert out == str(storage / "models" / "yolov8n.onnx")
    assert (storage / "models").is_dir()
    assert (storage / "cache").is_dir()
    assert os.environ["XDG_CACHE_HOME"] == str(storage / "cache")


def test_prepare_model_path_rejects_relative_storage_path() -> None:
    detection_module = _load_detection_module()
    with pytest.raises(ValueError, match="absolute path"):
        detection_module.PersonDetector._prepare_model_path(
            DetectionConfig(
                enabled=False,
                model="yolov8n.onnx",
                storage_path="relative/dir",
            ),
        )


def test_prepare_model_path_creates_models_and_cache_dirs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A storage_path redirect creates the models + cache dirs and points
    ``XDG_CACHE_HOME`` at the cache dir."""
    detection_module = _load_detection_module()
    _snapshot_detection_env(monkeypatch)

    out = detection_module.PersonDetector._prepare_model_path(
        DetectionConfig(
            enabled=False,
            model="yolov8n.onnx",
            storage_path=str(tmp_path),
        ),
    )
    assert out == str(tmp_path / "models" / "yolov8n.onnx")
    assert (tmp_path / "models").is_dir()
    assert (tmp_path / "cache").is_dir()
    assert os.environ["XDG_CACHE_HOME"] == str(tmp_path / "cache")


def test_prepare_model_path_keeps_absolute_model_path(tmp_path: Path, monkeypatch) -> None:
    detection_module = _load_detection_module()
    _snapshot_detection_env(monkeypatch)

    custom = tmp_path / "custom" / "weights.onnx"
    custom.parent.mkdir()
    custom.write_bytes(b"stub")
    out = detection_module.PersonDetector._prepare_model_path(
        DetectionConfig(
            enabled=False,
            model=str(custom),
            storage_path=str(tmp_path),
        ),
    )
    assert out == str(custom)


# ---------------------------------------------------------------------------
# _preprocess / _sample_to_numpy
# ---------------------------------------------------------------------------


def test_preprocess_always_applies_clahe() -> None:
    detection_module = _load_detection_module()
    detector = detection_module.PersonDetector(DetectionConfig(enabled=False))
    # CLAHE is unconditional now – the equaliser is always constructed.
    assert detector._clahe is not None

    # A low-contrast gradient ramps the luminance; CLAHE redistributes it, so
    # the output is a fresh array that differs from the input (not pass-through).
    frame = np.tile(np.linspace(40, 90, 16, dtype=np.uint8), (16, 1))
    frame = np.repeat(frame[:, :, None], 3, axis=2)
    out = detector._preprocess(frame)

    assert out is not frame  # never returns the input unchanged
    assert out.shape == frame.shape
    assert out.dtype == frame.dtype
    assert not np.array_equal(out, frame)  # contrast was actually equalised


class _FakeStructure:
    def __init__(self, w: int, h: int) -> None:
        self._values = {"width": w, "height": h}

    def get_value(self, key: str) -> int:
        return self._values[key]


class _FakeCaps:
    def __init__(self, structure: _FakeStructure) -> None:
        self._structure = structure

    def get_structure(self, _idx: int) -> _FakeStructure:
        return self._structure


class _FakeMapInfo:
    def __init__(self, data: bytes) -> None:
        self.data = data


class _FakeBuffer:
    def __init__(self, data: bytes, map_ok: bool = True) -> None:
        self._data = data
        self._map_ok = map_ok
        self.unmapped = False

    def map(self, _flags):  # noqa: ANN001, ANN201
        if not self._map_ok:
            return False, _FakeMapInfo(b"")
        return True, _FakeMapInfo(self._data)

    def unmap(self, _mapinfo) -> None:  # noqa: ANN001
        self.unmapped = True


class _FakeSample:
    def __init__(self, buffer: _FakeBuffer, caps: _FakeCaps) -> None:
        self._buffer = buffer
        self._caps = caps

    def get_buffer(self) -> _FakeBuffer:
        return self._buffer

    def get_caps(self) -> _FakeCaps:
        return self._caps


class _FakeGst:
    class MapFlags:
        READ = 1


def test_sample_to_numpy_returns_copy_of_rgb_frame() -> None:
    detection_module = _load_detection_module()
    data = np.full((4, 4, 3), 7, dtype=np.uint8).tobytes()
    buffer = _FakeBuffer(data)
    sample = _FakeSample(
        buffer=buffer,
        caps=_FakeCaps(_FakeStructure(w=4, h=4)),
    )
    out = detection_module.PersonDetector._sample_to_numpy(sample, _FakeGst)
    assert out is not None
    assert out.shape == (4, 4, 3)
    assert int(out[0, 0, 0]) == 7
    # unmap must always run so GStreamer doesn't leak the buffer.
    assert buffer.unmapped is True


def test_sample_to_numpy_returns_none_when_map_fails() -> None:
    detection_module = _load_detection_module()
    sample = _FakeSample(
        buffer=_FakeBuffer(b"", map_ok=False),
        caps=_FakeCaps(_FakeStructure(w=4, h=4)),
    )
    out = detection_module.PersonDetector._sample_to_numpy(sample, _FakeGst)
    assert out is None


def test_sample_to_numpy_returns_none_on_reshape_mismatch() -> None:
    detection_module = _load_detection_module()
    # Buffer is 10 bytes; caps claim 4×4×3 = 48 → reshape ValueError.
    buffer = _FakeBuffer(b"\x00" * 10)
    sample = _FakeSample(
        buffer=buffer,
        caps=_FakeCaps(_FakeStructure(w=4, h=4)),
    )
    out = detection_module.PersonDetector._sample_to_numpy(sample, _FakeGst)
    assert out is None
    assert buffer.unmapped is True


def test_sample_to_numpy_handles_row_padding() -> None:
    """GStreamer may pad each row up to a 4-byte boundary; the converter
    must slice per-row by the real stride, not assume a tight h*w*3 buffer
    (which would raise ValueError every frame for such widths)."""
    detection_module = _load_detection_module()
    w, h = 2, 2
    row_bytes = w * 3  # 6
    stride = 8  # padded up from 6
    rows = []
    for r in range(h):
        real = bytes((r * 10 + i) for i in range(row_bytes))  # distinct, < 255
        rows.append(real + b"\xff" * (stride - row_bytes))  # pad with 0xff
    buffer = _FakeBuffer(b"".join(rows))
    sample = _FakeSample(buffer=buffer, caps=_FakeCaps(_FakeStructure(w=w, h=h)))

    out = detection_module.PersonDetector._sample_to_numpy(sample, _FakeGst)

    assert out is not None
    assert out.shape == (h, w, 3)
    # Real pixels survive; the 0xff padding bytes must NOT leak in.
    assert out[0].flatten().tolist() == [0, 1, 2, 3, 4, 5]
    assert out[1].flatten().tolist() == [10, 11, 12, 13, 14, 15]
    assert 255 not in out.flatten().tolist()
    assert buffer.unmapped is True


def test_sample_to_numpy_returns_none_on_zero_width() -> None:
    detection_module = _load_detection_module()
    sample = _FakeSample(_FakeBuffer(b"\x00" * 12), _FakeCaps(_FakeStructure(w=0, h=2)))
    assert detection_module.PersonDetector._sample_to_numpy(sample, _FakeGst) is None


def test_sample_to_numpy_returns_none_on_zero_height() -> None:
    detection_module = _load_detection_module()
    sample = _FakeSample(_FakeBuffer(b"\x00" * 12), _FakeCaps(_FakeStructure(w=2, h=0)))
    assert detection_module.PersonDetector._sample_to_numpy(sample, _FakeGst) is None


# ---------------------------------------------------------------------------
# start / stop / properties
# ---------------------------------------------------------------------------


def test_start_is_noop_without_backend() -> None:
    detection_module = _load_detection_module()
    detector = detection_module.PersonDetector(DetectionConfig(enabled=False))
    detector.start()
    assert detector._running is False
    assert detector._thread is None


def test_start_is_noop_when_appsink_missing() -> None:
    """Backend present but appsink not yet attached (pre-pipeline build)
    → still a no-op; otherwise the thread would spin on a None appsink."""
    detection_module = _load_detection_module()
    detector = detection_module.PersonDetector(DetectionConfig(enabled=False))
    detector._backend = object()
    detector.start()
    assert detector._running is False


def test_stop_without_thread_is_silent() -> None:
    detection_module = _load_detection_module()
    detector = detection_module.PersonDetector(DetectionConfig(enabled=False))
    detector.stop()  # no thread, no backend – must not raise
    assert detector._running is False


def test_input_resolution_returns_4_3_from_inference_size() -> None:
    """The detector exposes a 4:3 resolution to the pipeline builder so
    the appsink branch matches what the model was trained on."""
    detection_module = _load_detection_module()
    detector = detection_module.PersonDetector(
        DetectionConfig(enabled=False, inference_size=320),
    )
    assert detector.input_resolution == (320, 240)


def test_detections_property_returns_internal_results_list() -> None:
    detection_module = _load_detection_module()
    detector = detection_module.PersonDetector(DetectionConfig(enabled=False))
    box = detection_module.DetectionBox(0.1, 0.1, 0.2, 0.2, 0.9, track_id=1)
    detector._results = [box]
    assert detector.detections == [box]


def test_tracked_detection_returns_none_when_no_results() -> None:
    detection_module = _load_detection_module()
    detector = detection_module.PersonDetector(
        DetectionConfig(enabled=False, grace_period_ms=500),
    )
    assert detector.tracked_detection is None


def test_tracked_detection_pins_largest_box_when_no_pin_active() -> None:
    detection_module = _load_detection_module()
    detector = detection_module.PersonDetector(DetectionConfig(enabled=False))
    small = detection_module.DetectionBox(0.1, 0.1, 0.2, 0.2, 0.9, track_id=1)
    big = detection_module.DetectionBox(0.1, 0.1, 0.9, 0.9, 0.8, track_id=2)
    detector._results = [small, big]
    chosen = detector.tracked_detection
    assert chosen is big
    assert detector._pinned_id == 2


def test_performance_stats_reflects_written_counters() -> None:
    detection_module = _load_detection_module()
    detector = detection_module.PersonDetector(DetectionConfig(enabled=False))
    # Simulate what _run_inner would write.
    detector._inference_count = 10
    detector._inference_total_ms = 250.0
    detector._inference_max_ms = 42.0
    detector._inference_last_ms = 25.0
    detector._inference_errors = 1
    detector._detections_last = 3
    detector._detections_total = 30

    stats = detector.performance_stats
    assert stats["inference_count"] == 10
    assert stats["inference_avg_ms"] == pytest.approx(25.0)
    assert stats["inference_max_ms"] == 42.0
    assert stats["inference_last_ms"] == 25.0
    assert stats["inference_errors"] == 1
    assert stats["detections_last"] == 3
    assert stats["detections_avg"] == pytest.approx(3.0)
