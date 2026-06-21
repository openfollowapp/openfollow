# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Optional YOLO person detection via ONNX Runtime.

``PersonDetector`` runs inference in a background thread off a GStreamer
appsink using an ONNX Runtime backend. Produces ``DetectionBox`` results with
stable ``track_id``s from the ByteTrack tracker, and supports live config
reload via a single staged-config slot drained by the worker.
``check_detection_dependencies`` reports missing pip packages so the UI can
surface them.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

try:
    import cv2
except ImportError as _cv2_err:  # pragma: no cover - depends on runtime opencv-python presence
    cv2 = None  # type: ignore[assignment, unused-ignore]
    _CV2_IMPORT_ERROR: str | None = str(_cv2_err)
else:
    _CV2_IMPORT_ERROR = None

import numpy as np
import numpy.typing as npt

from openfollow.video.tracking import LOW_DETECTION_THRESHOLD, ByteTracker

if TYPE_CHECKING:
    from openfollow.configuration import DetectionConfig

logger = logging.getLogger(__name__)


_INFERENCE_HISTORY_SIZE = 120

# Cap on raw detections pulled from inference before tracking. ByteTrack needs
# to see the low-confidence band too, so the worker retrieves well past
# ``max_persons``; this bounds the per-frame association cost.
_RAW_DETECTION_CAP = 60

# Clamp for the per-frame Kalman time step (elapsed time ÷ nominal interval).
# Floors a back-to-back burst and caps a long stall so neither explodes the
# extrapolation / process noise when the real detection cadence jitters.
_MIN_DT_REL = 0.1
_MAX_DT_REL = 8.0

# Max normalised box-centre distance for re-acquiring the followed person after
# their track is lost/re-numbered. A performer who dimmed or was briefly occluded
# reappears near where they were, so a candidate within this radius is treated as
# the same person; beyond it the old target is taken to have left the frame.
_REACQUIRE_MAX_CENTER_DIST = 0.15

# Where detection storage lands when the operator leaves ``storage_path`` blank
# on a unit whose NVMe is mounted. The appliance mounts the drive here and the
# install/deploy layouts already use this directory tree.
_NVME_MOUNTPOINT = "/mnt/nvme"
_NVME_DETECTION_STORAGE = "/mnt/nvme/openfollow/yolo"

# Folder under the working directory used for detection storage when no NVMe is
# present. The service runs with WorkingDirectory=/var/lib/openfollow, so this
# resolves to /var/lib/openfollow/yolo on the appliance (the checkout dir on a
# dev machine).
_LOCAL_STORAGE_DIRNAME = "yolo"


def resolve_detection_storage_path(storage_path: str) -> str:
    """Resolve the effective detection storage root (always absolute when blank).

    An explicit ``storage_path`` always wins. A blank value resolves
    automatically so the operator never has to choose a location: the NVMe
    location when ``/mnt/nvme`` is a real mountpoint, otherwise a ``yolo`` folder
    under the current working directory. Idempotent: re-resolving an
    already-resolved path returns it unchanged.
    """
    explicit = storage_path.strip()
    if explicit:
        return explicit
    if os.path.ismount(_NVME_MOUNTPOINT):
        return _NVME_DETECTION_STORAGE
    return str(Path.cwd() / _LOCAL_STORAGE_DIRNAME)


def check_detection_dependencies(
    config: DetectionConfig | None = None,
) -> list[str]:
    """Return pip-install names of required deps that are missing.

    Person detection needs ``opencv-python`` and ``onnxruntime``; this reports
    whichever are absent. ``config`` is accepted but not required.
    """
    missing: list[str] = []
    if _CV2_IMPORT_ERROR is not None:
        missing.append("opencv-python")
    if importlib.util.find_spec("onnxruntime") is None:
        missing.append("onnxruntime")
    return missing


@dataclass
class DetectionBox:
    """A single person detection bounding box (normalised 0-1 coordinates)."""

    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float
    label: str = "person"
    track_id: int = -1


@dataclass
class _TrackedPerson:
    """Internal state for a tracked person across frames."""

    track_id: int
    box: DetectionBox
    last_seen: float  # time.monotonic() timestamp


class _InferenceBackend(Protocol):
    """Predictor backend interface used by the detector thread."""

    def predict(
        self,
        frame: npt.NDArray[Any],
        confidence: float,
        max_persons: int,
        inference_size: int,
    ) -> list[DetectionBox]: ...  # pragma: no cover - Protocol method body, never executed


def _nms(
    boxes_xyxy: npt.NDArray[Any],
    scores: npt.NDArray[Any],
    iou_threshold: float = 0.45,
) -> list[int]:
    """Run greedy NMS and return kept indices in descending score order."""
    if boxes_xyxy.size == 0 or scores.size == 0:
        return []

    x1 = boxes_xyxy[:, 0]
    y1 = boxes_xyxy[:, 1]
    x2 = boxes_xyxy[:, 2]
    y2 = boxes_xyxy[:, 3]
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)

    order = scores.argsort()[::-1]
    keep: list[int] = []

    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break

        rest = order[1:]
        xx1 = np.maximum(x1[i], x1[rest])
        yy1 = np.maximum(y1[i], y1[rest])
        xx2 = np.minimum(x2[i], x2[rest])
        yy2 = np.minimum(y2[i], y2[rest])

        inter_w = np.maximum(0.0, xx2 - xx1)
        inter_h = np.maximum(0.0, yy2 - yy1)
        inter = inter_w * inter_h

        union = areas[i] + areas[rest] - inter
        iou = np.zeros_like(inter)
        valid = union > 0
        iou[valid] = inter[valid] / union[valid]

        order = rest[iou <= iou_threshold]

    return keep


def _letterbox(
    img: npt.NDArray[Any],
    target_size: int,
) -> tuple[npt.NDArray[Any], float, tuple[int, int]]:
    """Resize with aspect ratio preserved, pad with neutral gray (114)."""
    h, w = img.shape[:2]
    if h <= 0 or w <= 0:
        padded = np.full((target_size, target_size, 3), 114, dtype=np.uint8)
        return padded, 1.0, (0, 0)

    scale = min(target_size / w, target_size / h)
    resized_w = max(1, int(round(w * scale)))
    resized_h = max(1, int(round(h * scale)))

    resized = cv2.resize(img, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR)
    padded = np.full((target_size, target_size, 3), 114, dtype=resized.dtype)

    pad_x = (target_size - resized_w) // 2
    pad_y = (target_size - resized_h) // 2
    padded[pad_y : pad_y + resized_h, pad_x : pad_x + resized_w] = resized
    return padded, scale, (pad_x, pad_y)


class _OnnxBackend:
    """YOLO ONNX Runtime backend.

    Runs on hardware acceleration when the platform offers it (CoreML on
    Apple Silicon) and falls back to CPU otherwise. For a medium/large
    model on Apple Silicon, CoreML is roughly an order of magnitude faster
    than the CPU provider.
    """

    @staticmethod
    def _select_providers(available: list[str]) -> list[str]:
        """Order the execution providers, fastest viable first.

        CoreML (Apple Silicon GPU / Neural Engine) is preferred when present;
        it only shows up in ``available`` on macOS onnxruntime builds, so a
        Linux host (the Pi) lands on CPU with no OS sniffing. CPU is always
        kept as the trailing fallback for ops the accelerator can't run.
        """
        providers: list[str] = []
        if "CoreMLExecutionProvider" in available:
            providers.append("CoreMLExecutionProvider")
        providers.append("CPUExecutionProvider")
        return providers

    def __init__(self, model_path: str) -> None:
        import onnxruntime as ort

        session_options = ort.SessionOptions()
        # CPU inference of YOLO conv layers stops scaling past ~4 threads on
        # the target hardware (more threads regress on Apple Silicon's mixed
        # perf/efficiency cores), and CoreML offloads the bulk off-CPU anyway.
        session_options.intra_op_num_threads = 4

        providers = self._select_providers(ort.get_available_providers())

        self._session: Any = ort.InferenceSession(
            model_path,
            sess_options=session_options,
            providers=providers,
        )
        logger.info("ONNX detection providers: %s", ", ".join(self._session.get_providers()))
        input_meta = self._session.get_inputs()[0]
        output_meta = self._session.get_outputs()[0]

        self._input_name = input_meta.name
        self._output_name = output_meta.name
        self._input_shape = tuple(input_meta.shape)
        self._model_input_size = self._extract_input_size(self._input_shape)

    @property
    def model_input_size(self) -> int | None:
        """The model's fixed square input size, or ``None`` for a dynamic axis."""
        return self._model_input_size

    @staticmethod
    def _extract_input_size(shape: tuple[object, ...]) -> int | None:
        if len(shape) < 4:
            return None
        h = _OnnxBackend._to_positive_int(shape[2])
        w = _OnnxBackend._to_positive_int(shape[3])
        if h is None or w is None:
            return None
        return max(h, w)

    @staticmethod
    def _to_positive_int(value: object) -> int | None:
        return int(value) if isinstance(value, int) and value > 0 else None

    @staticmethod
    def _prepare_predictions(raw: npt.NDArray[Any]) -> npt.NDArray[Any]:
        pred = np.asarray(raw)
        if pred.ndim == 3:
            pred = pred[0]
        if pred.ndim != 2:
            return np.empty((0, 0), dtype=np.float32)

        # YOLOv8 ONNX is often [84, N]; convert to [N, 84].
        if (pred.shape[0] < pred.shape[1] and pred.shape[0] >= 6) or (pred.shape[1] < 6 and pred.shape[0] >= 6):
            pred = pred.T

        return pred.astype(np.float32, copy=False)

    def predict(
        self,
        frame: npt.NDArray[Any],
        confidence: float,
        max_persons: int,
        inference_size: int,
    ) -> list[DetectionBox]:
        target_size = self._model_input_size or int(inference_size)
        target_size = max(160, target_size)

        letterboxed, scale, (pad_x, pad_y) = _letterbox(frame, target_size)
        tensor = letterboxed.astype(np.float32) / 255.0
        tensor = np.transpose(tensor, (2, 0, 1))
        tensor = np.expand_dims(tensor, axis=0)

        outputs = self._session.run([self._output_name], {self._input_name: tensor})
        pred = self._prepare_predictions(outputs[0])
        if pred.size == 0 or pred.shape[1] <= 4:
            return []

        if pred.shape[1] == 6:
            # NMS-free end-to-end head (YOLO26 / YOLOv10): each row is
            # ``[x1, y1, x2, y2, conf, class]`` already in input-pixel xyxy
            # space, de-duplicated and ranked by score. Keep person (class 0)
            # only. The head already ran NMS, so don't run it again here – that
            # would suppress legitimately overlapping people it kept on purpose.
            scores = pred[:, 4]
            keep_mask = (scores >= float(confidence)) & (np.rint(pred[:, 5]) == 0)
            if not np.any(keep_mask):
                return []
            boxes_xyxy = pred[keep_mask, :4].astype(np.float32, copy=True)
            scores = scores[keep_mask]
            keep_indices = list(range(len(scores)))
        else:
            # YOLOv8 / YOLO11 head: ``[cx, cy, w, h, <class scores>]``; class 0
            # is "person". Convert cx,cy,w,h -> x1,y1,x2,y2 then run NMS.
            scores = pred[:, 4]
            keep_mask = scores >= float(confidence)
            if not np.any(keep_mask):
                return []
            boxes = pred[keep_mask, :4]
            scores = scores[keep_mask]
            boxes_xyxy = np.empty_like(boxes, dtype=np.float32)
            boxes_xyxy[:, 0] = boxes[:, 0] - boxes[:, 2] / 2.0
            boxes_xyxy[:, 1] = boxes[:, 1] - boxes[:, 3] / 2.0
            boxes_xyxy[:, 2] = boxes[:, 0] + boxes[:, 2] / 2.0
            boxes_xyxy[:, 3] = boxes[:, 1] + boxes[:, 3] / 2.0
            keep_indices = _nms(boxes_xyxy, scores, iou_threshold=0.45)
            if not keep_indices:  # pragma: no cover - unreachable: _nms always keeps >=1 on non-empty input
                return []

        frame_h, frame_w = frame.shape[:2]
        scale = max(scale, 1e-6)
        safe_w = max(frame_w, 1)
        safe_h = max(frame_h, 1)

        detections: list[DetectionBox] = []
        for idx in keep_indices:
            x1, y1, x2, y2 = boxes_xyxy[idx]
            x1 = (x1 - pad_x) / scale
            y1 = (y1 - pad_y) / scale
            x2 = (x2 - pad_x) / scale
            y2 = (y2 - pad_y) / scale

            x1 = float(np.clip(x1, 0.0, frame_w))
            y1 = float(np.clip(y1, 0.0, frame_h))
            x2 = float(np.clip(x2, 0.0, frame_w))
            y2 = float(np.clip(y2, 0.0, frame_h))
            if x2 <= x1 or y2 <= y1:
                continue

            detections.append(
                DetectionBox(
                    x1=x1 / safe_w,
                    y1=y1 / safe_h,
                    x2=x2 / safe_w,
                    y2=y2 / safe_h,
                    confidence=float(scores[idx]),
                )
            )
            if len(detections) >= max_persons:
                break

        return detections

    def close(self) -> None:
        """Release the InferenceSession (and its native intra-op thread
        pool) deterministically instead of waiting for GC."""
        self._session = None


def _close_backend(backend: _InferenceBackend | None) -> None:
    """Release a backend's native resources (the ONNX session's intra-op thread
    pool) deterministically. Swallows errors so a teardown failure can't abort
    an in-progress rebuild or shutdown."""
    if backend is None:
        return
    close = getattr(backend, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:
        logger.debug("Error closing detection backend", exc_info=True)


class PersonDetector:
    """Background person detector with GStreamer appsink integration."""

    def __init__(self, config: DetectionConfig) -> None:
        self._config = config
        self._backend: _InferenceBackend | None = None
        self._backend_name: str | None = None
        self._model_path = config.model
        self._inference_size = self._normalize_inference_size(config.inference_size)
        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)) if config.preprocess_clahe else None
        self._results: list[DetectionBox] = []
        self._running = False
        self._thread: threading.Thread | None = None
        self._appsink = None  # Set by pipeline builder

        # Live config-apply staging. The GTK thread stages a new config
        # under ``_config_lock``; the worker drains it between frames.
        # A single-element slot is enough – if two updates arrive before
        # the worker drains, the latter wins, which is what we want.
        self._config_lock = threading.Lock()
        self._pending_config: DetectionConfig | None = None

        # ByteTrack tracker + the track snapshot it produces each frame.
        # ``_tracked`` carries every live tracklet (tracked + lost-within-grace)
        # so ``tracked_detection`` can hold a pinned person through occlusion.
        self._tracker = ByteTracker()
        self._tracked: list[_TrackedPerson] = []
        # Monotonic timestamp of the previous track step, for the Kalman dt.
        self._last_track_t: float | None = None
        self._pinned_id: int | None = None  # currently followed person
        # Normalised centre of the box last returned for the pinned person. Lets
        # re-acquisition re-lock onto the same person (by position) when their
        # track is lost/re-numbered, instead of jumping to the largest box.
        self._last_pinned_center: tuple[float, float] | None = None
        self._track_lock = threading.Lock()

        # Guards the backend triple (_backend / _backend_name / _model_path):
        # the worker thread rewrites them on a live rebuild while the GTK /
        # web threads read them via ``available`` and ``performance_stats``.
        self._backend_lock = threading.Lock()

        # Runtime performance metrics (read from web thread, written from detector thread)
        self._perf_lock = threading.Lock()
        self._started_at_monotonic: float | None = None
        self._last_inference_monotonic: float | None = None
        self._inference_count = 0
        self._inference_total_ms = 0.0
        self._inference_last_ms = 0.0
        self._inference_max_ms = 0.0
        self._inference_errors = 0
        self._sample_timeouts = 0
        self._sample_failures = 0
        self._detections_last = 0
        self._detections_total = 0
        self._tracked_count = 0
        self._inference_history_ms: deque[float] = deque(maxlen=_INFERENCE_HISTORY_SIZE)

        # Snapshot the storage-redirect env vars BEFORE
        # ``_prepare_model_path`` mutates them so the operator's
        # original values can be restored on a live ``storage_path →
        # ""`` transition (which otherwise leaves the redirection in
        # place because ``_prepare_model_path`` returns early when
        # ``storage_path`` is empty). Captured even when detection is
        # disabled so a later off→on followed by on→empty cycle still
        # has a baseline to restore to.
        self._initial_storage_env = self._capture_storage_env()

        if not config.enabled:
            return

        try:
            self._model_path = self._prepare_model_path(config)
        except (OSError, ValueError) as exc:
            logger.error("Failed to initialize detection storage: %s", exc)
            return

        self._load_backend()

    def _load_backend(self, base_model_path: str | None = None) -> None:
        # ``base_model_path`` lets the live-rebuild path pass the candidate
        # model WITHOUT pre-committing it to ``self._model_path`` – the backend
        # triple (_backend / _backend_name / _model_path) is published together
        # only on a successful load below, so a failed/slow rebuild never
        # exposes a half-applied new-model/old-backend state. Defaults to the
        # current path for the construction-time load.
        model_path = base_model_path if base_model_path is not None else self._model_path

        try:
            backend: _InferenceBackend = _OnnxBackend(model_path)
        except ImportError as exc:
            logger.warning(
                "onnxruntime not installed – person detection disabled. "
                "Install with: pip install openfollow[detection] (%s)",
                exc,
            )
            return
        except Exception as exc:
            logger.error(
                "Failed to initialize ONNX detection backend with '%s': %s",
                model_path,
                exc,
            )
            return

        with self._backend_lock:
            old_backend = self._backend
            self._backend = backend
            self._backend_name = "onnx"
            self._model_path = model_path
        # Release the replaced backend off the lock. stop() only closes on
        # terminal shutdown, so without this a live model/storage_path change
        # orphans the prior ONNX session (4-thread native pool), leaking native
        # threads + memory across a show. Identity-guarded so a factory that
        # ever returns the live instance can't close it out from under itself.
        if old_backend is not backend:
            _close_backend(old_backend)
        # Auto-size the detector to the model's own fixed input resolution so the
        # operator never has to match ``inference_size`` to the export. This sets
        # the appsink caps (via ``input_resolution``) at construction time, ahead
        # of pipeline build. A dynamic-axis model reports ``None`` – keep the
        # configured ``inference_size`` as the fallback then.
        model_size = getattr(backend, "model_input_size", None)
        if model_size is not None:
            self._inference_size = self._normalize_inference_size(int(model_size))
        logger.info(
            "Detection backend loaded: onnx (model=%s, imgsz=%d, clahe=%s)",
            self._model_path,
            self._inference_size,
            "on" if self._clahe is not None else "off",
        )

    @staticmethod
    def _prepare_model_path(config: DetectionConfig) -> str:
        model_name = config.model.strip()
        if not model_name:
            raise ValueError("detection.model must not be empty")

        storage_path = resolve_detection_storage_path(config.storage_path)
        storage_root = Path(storage_path).expanduser()
        if not storage_root.is_absolute():
            raise ValueError("detection.storage_path must be an absolute path")

        models_dir = storage_root / "models"
        cache_dir = storage_root / "cache"

        for directory in (storage_root, models_dir, cache_dir):
            directory.mkdir(parents=True, exist_ok=True)

        probe_path = storage_root / ".openfollow-write-test"
        probe_path.write_text("ok", encoding="utf-8")
        probe_path.unlink()

        os.environ["XDG_CACHE_HOME"] = str(cache_dir)

        model_path = Path(model_name).expanduser()
        if model_path.is_absolute() or model_path.parent != Path("."):
            resolved_model = model_path
        else:
            resolved_model = models_dir / model_path.name

        logger.info("Detection runtime storage redirected to %s", storage_root)
        return str(resolved_model)

    # Env var ``_prepare_model_path`` mutates when ``storage_path`` redirects
    # the ONNX runtime cache off the default user dir. Captured + restored
    # across a failed live-apply rebuild so the process never ends up pointing
    # at the new cache dir while inference still runs on the old session.
    _STORAGE_ENV_VARS = ("XDG_CACHE_HOME",)

    @classmethod
    def _capture_storage_env(cls) -> dict[str, str | None]:
        """Snapshot the storage-redirect env vars for rollback."""
        return {name: os.environ.get(name) for name in cls._STORAGE_ENV_VARS}

    @classmethod
    def _restore_storage_env(cls, snapshot: dict[str, str | None]) -> None:
        """Restore env vars from a snapshot, removing any that were unset."""
        for name, value in snapshot.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    @staticmethod
    def _normalize_inference_size(size: int) -> int:
        bounded = max(160, min(1280, int(size)))
        return max(160, (bounded // 32) * 32)

    @property
    def available(self) -> bool:
        """True if a detection backend loaded successfully."""
        with self._backend_lock:
            return self._backend is not None

    @property
    def input_resolution(self) -> tuple[int, int]:
        """Preferred detector input size for the appsink branch (4:3)."""
        s = self._inference_size
        return (s, int(s * 3 / 4))

    @property
    def detections(self) -> list[DetectionBox]:
        """Thread-safe read of latest detections."""
        with self._track_lock:
            return self._results

    @staticmethod
    def _box_center(box: DetectionBox) -> tuple[float, float]:
        """Normalised centre ``(cx, cy)`` of a detection box."""
        return ((box.x1 + box.x2) / 2.0, (box.y1 + box.y2) / 2.0)

    @property
    def tracked_detection(self) -> DetectionBox | None:
        """Return the detection for the currently pinned person, or *None*.

        While the pinned person's track is alive (seen within the grace period)
        their box is returned even if briefly unseen. Once the track is lost or
        re-numbered, re-acquisition re-locks onto the detection nearest the
        last-followed box centre – the same person at the same place – so a
        re-numbered performer keeps being followed. It falls back to the largest
        visible detection only on a true cold start or when no candidate lies
        within the re-acquire gate (the old target has left the frame).
        """
        now = time.monotonic()
        grace_s = self._config.grace_period_ms / 1000.0

        # Snapshot shared state under lock to avoid races with detector thread
        with self._track_lock:
            pinned_id = self._pinned_id
            tracked = self._tracked
            results = self._results
            last_center = self._last_pinned_center

        # Sticky-by-track_id while the pinned person's track is still alive.
        if pinned_id is not None:
            for tp in tracked:
                if tp.track_id == pinned_id:
                    if now - tp.last_seen <= grace_s:
                        with self._track_lock:
                            self._last_pinned_center = self._box_center(tp.box)
                        return tp.box
                    # Grace period expired – release pin
                    break
            with self._track_lock:
                self._pinned_id = None

        if not results:
            return None

        # Re-acquire: prefer the detection nearest the last-followed centre so a
        # re-numbered track stays followed rather than jumping to the largest box.
        best: DetectionBox | None = None
        if last_center is not None:
            cx0, cy0 = last_center

            def _dist_sq(d: DetectionBox) -> float:
                dcx, dcy = self._box_center(d)
                return (dcx - cx0) ** 2 + (dcy - cy0) ** 2

            nearest = min(results, key=_dist_sq)
            if _dist_sq(nearest) <= _REACQUIRE_MAX_CENTER_DIST**2:
                best = nearest

        # Cold start, or the old target left the frame: pin the largest detection.
        if best is None:
            best = max(results, key=lambda d: (d.x2 - d.x1) * (d.y2 - d.y1))

        with self._track_lock:
            self._pinned_id = best.track_id
            self._last_pinned_center = self._box_center(best)
        return best

    @property
    def performance_stats(self) -> dict[str, bool | int | float | str | None]:
        """Return a lightweight snapshot of detector runtime metrics."""
        now = time.monotonic()
        with self._track_lock:
            pinned_id = self._pinned_id
        with self._backend_lock:
            backend_available = self._backend is not None
            backend_name = self._backend_name
            model_path = self._model_path
        with self._perf_lock:
            elapsed = now - self._started_at_monotonic if self._started_at_monotonic is not None else 0.0
            avg_ms = self._inference_total_ms / self._inference_count if self._inference_count > 0 else 0.0
            detections_avg = self._detections_total / self._inference_count if self._inference_count > 0 else 0.0
            inference_hz = self._inference_count / elapsed if elapsed > 0.0 else 0.0
            p95_ms = self._percentile(list(self._inference_history_ms), 95.0)
            last_age_ms = (
                (now - self._last_inference_monotonic) * 1000.0 if self._last_inference_monotonic is not None else None
            )
            return {
                "enabled": bool(self._config.enabled),
                "available": backend_available,
                "running": self._running,
                "backend": backend_name or "none",
                "model": model_path,
                "interval_ms": int(self._config.interval_ms),
                "inference_count": int(self._inference_count),
                "inference_hz": float(inference_hz),
                "inference_avg_ms": float(avg_ms),
                "inference_p95_ms": float(p95_ms),
                "inference_max_ms": float(self._inference_max_ms),
                "inference_last_ms": float(self._inference_last_ms),
                "inference_errors": int(self._inference_errors),
                "sample_timeouts": int(self._sample_timeouts),
                "sample_failures": int(self._sample_failures),
                "detections_last": int(self._detections_last),
                "detections_avg": float(detections_avg),
                "tracked_people": int(self._tracked_count),
                "pinned_track_id": int(pinned_id) if pinned_id is not None else None,
                "last_inference_age_ms": float(last_age_ms) if last_age_ms is not None else None,
            }

    def set_appsink(self, appsink: Any) -> None:
        """Called by the pipeline builder to provide the GStreamer appsink."""
        self._appsink = appsink

    def start(self) -> None:
        with self._backend_lock:
            backend_ready = self._backend is not None
        if not backend_ready or self._appsink is None:
            return
        # Idempotent: a second start() without an intervening stop() would
        # orphan the prior daemon thread (it keeps running, races on the
        # appsink/results, and can never be joined). Bail if one is alive.
        if self._running or (self._thread is not None and self._thread.is_alive()):
            return
        self._running = True
        with self._perf_lock:
            self._started_at_monotonic = time.monotonic()
            self._last_inference_monotonic = None
            self._inference_count = 0
            self._inference_total_ms = 0.0
            self._inference_last_ms = 0.0
            self._inference_max_ms = 0.0
            self._inference_errors = 0
            self._sample_timeouts = 0
            self._sample_failures = 0
            self._detections_last = 0
            self._detections_total = 0
            self._tracked_count = 0
            self._inference_history_ms.clear()
        # Fresh session: drop any tracklets/ids carried from a prior run so
        # track_ids restart at 0 and no stale lost track lingers.
        with self._track_lock:
            self._tracker.reset()
            self._tracked = []
            self._last_track_t = None
            self._pinned_id = None
            self._last_pinned_center = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="PersonDetector")
        self._thread.start()
        logger.info(
            "Person detector started (backend=%s, interval=%dms)",
            self._backend_name or "unknown",
            self._config.interval_ms,
        )

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        # Release native backend resources deterministically rather than at
        # GC time (the ONNX session owns a native thread pool). stop() is
        # terminal for this detector instance – swap/re-enable builds a fresh
        # PersonDetector.
        with self._backend_lock:
            backend = self._backend
            self._backend = None
            # Clear the name too so performance_stats can't report
            # available=False while still showing the prior backend name.
            self._backend_name = None
        _close_backend(backend)
        logger.info("Person detector stopped")

    def reload_config(self, new_cfg: DetectionConfig) -> None:
        """Stage a config update for the worker to pick up between frames.

        Called from the GTK thread. The worker drains the staged config
        at the top of its next loop iteration; the stage is a single
        slot, so consecutive updates collapse to the latest one (which
        is what we want).

        Heavy operations – backend session rebuild on a model / storage_path
        change – happen inside the worker, not on the GTK thread. See
        ``_drain_pending_config``.
        """
        with self._config_lock:
            self._pending_config = new_cfg

    def _drain_pending_config(self) -> None:
        """Apply a staged config update from inside the worker thread.

        Compares the new config against the live one and:
        - Swaps cheap hot-path fields (confidence, max_persons,
          interval_ms, grace_period_ms) with no rebuild.
        - Recomputes live-applied derived state (e.g. ``_clahe``) when
          its source field changes. ``inference_size`` is intentionally
          NOT live-applied here – the GStreamer appsink caps are
          pinned at pipeline build time and live-restamping
          ``_inference_size`` would silently disagree with the
          appsink resolution. The dispatcher treats it as
          restart-required so the receiver pipeline rebuilds with
          matching caps.
        - Rebuilds the inference backend when ``model`` or ``storage_path``
          changes – this is the heavy step, and it stays on the worker
          thread so the GTK thread isn't blocked by ONNX session loads.

        Failure mode: if the backend rebuild raises *or* silently
        fails to load (``_load_backend`` logs a warning and returns
        without raising when the backend is unavailable), the
        swap is aborted and the worker keeps running on the prior
        config + backend + model_path + backend_name. The operator sees
        a ``logger.exception`` / ``logger.error``; the next config edit
        gets a fresh attempt.
        """
        with self._config_lock:
            pending = self._pending_config
            self._pending_config = None
        if pending is None:
            return
        old = self._config

        needs_backend_rebuild = pending.model != old.model or pending.storage_path != old.storage_path
        if needs_backend_rebuild:
            # ``_load_backend`` publishes the backend triple
            # (_backend / _backend_name / _model_path) atomically and only on a
            # successful load, so on failure NONE of them change – no rollback
            # needed, and a slow rebuild can't expose a half-applied
            # new-model/old-backend state to ``performance_stats``. We still
            # snapshot the prior backend identity (to detect "loaded nothing
            # new") and the env (``_prepare_model_path`` mutates process-wide
            # cache dirs and must be restored if the load then fails).
            old_backend = self._backend
            old_env = self._capture_storage_env()
            try:
                new_model_path = self._prepare_model_path(pending)
            except (OSError, ValueError):
                logger.exception(
                    "detection: model-path resolution failed; keeping prior config",
                )
                # _prepare_model_path validates before touching env vars, so
                # env is pristine here; restore defensively regardless.
                self._restore_storage_env(old_env)
                return
            try:
                self._load_backend(new_model_path)
            except Exception:
                logger.exception(
                    "detection: backend rebuild failed; keeping prior config",
                )
                # Triple is untouched (published only on success); undo env only.
                self._restore_storage_env(old_env)
                return
            # ``_load_backend`` swallows ImportError / load failures and only
            # publishes the triple on a successful load. If the load failed it
            # returns without raising – detect that by identity-comparing the
            # current backend against the one captured before.
            if self._backend is old_backend:
                logger.error(
                    "detection: backend rebuild loaded no new backend; keeping prior config",
                )
                self._restore_storage_env(old_env)
                return

            # On the success path, restore the operator's pre-app
            # baseline if ``storage_path`` was cleared (non-empty →
            # empty). ``_prepare_model_path`` returns early without
            # touching env vars when ``storage_path`` is empty, so the
            # process would otherwise keep using the prior redirected
            # cache dirs even though the operator just told us "no
            # cache redirection".
            if not pending.storage_path and old.storage_path:
                self._restore_storage_env(self._initial_storage_env)

        if pending.preprocess_clahe != old.preprocess_clahe:
            self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)) if pending.preprocess_clahe else None
        # ``inference_size`` is intentionally NOT live-applied here.
        # The GStreamer appsink caps were chosen at pipeline build time
        # from ``PersonDetector.input_resolution`` and the receiver
        # owns the capsfilter – re-snapping ``_inference_size`` in the
        # worker would cause the backend to internally rescale frames
        # that arrive at the OLD caps resolution, defeating the
        # accuracy/perf change the operator intended. The dispatcher
        # treats ``inference_size`` changes as cold-path
        # (request_restart) so the receiver pipeline rebuilds with
        # caps matching the new resolution.

        self._config = pending
        logger.info(
            "Person detector config reloaded (backend=%s, interval=%dms, confidence=%.2f, max_persons=%d)",
            self._backend_name or "unknown",
            pending.interval_ms,
            pending.confidence,
            pending.max_persons,
        )

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float:
        """Return percentile from sorted values; 0 when empty."""
        if not values:
            return 0.0
        values.sort()
        idx = int(round((percentile / 100.0) * (len(values) - 1)))
        idx = max(0, min(idx, len(values) - 1))
        return float(values[idx])

    # ------------------------------------------------------------------
    # ByteTrack association
    # ------------------------------------------------------------------

    def _track(self, raw: list[DetectionBox]) -> list[DetectionBox]:
        """Run ByteTrack over the raw detections and publish the track snapshot.

        ``raw`` holds detections down to the ByteTrack low floor. They are split
        into the high band (>= configured ``confidence``) and the low recovery
        band, fed to the tracker, and mapped back to ``DetectionBox``es carrying
        a stable ``track_id``. Returns the boxes for tracks matched this frame
        (highest-confidence first, capped at ``max_persons`` per the documented
        contract); ``self._tracked`` keeps the full live set – including
        lost-within-grace tracks – for the pin grace logic in
        ``tracked_detection``.
        """
        now = time.monotonic()
        conf = self._config.confidence
        high = [box for box in raw if box.confidence >= conf]
        low = [box for box in raw if box.confidence < conf]
        max_lost_s = self._config.grace_period_ms / 1000.0

        # Real elapsed time since the last step, in units of the nominal interval,
        # so the Kalman extrapolation stays correct when frames drop or the
        # cadence jitters (1.0 = on-time, ~2.0 = one dropped frame). This is the
        # detection-cadence clock; the pin filter runs on a separate animate-cadence
        # clock (``_NOMINAL_FRAME_DT`` in ``runtime/services_detection_pin``).
        nominal_s = max(self._config.interval_ms / 1000.0, 1e-3)
        if self._last_track_t is None:
            dt_rel = 1.0
        else:
            dt_rel = min(max((now - self._last_track_t) / nominal_s, _MIN_DT_REL), _MAX_DT_REL)
        self._last_track_t = now

        tracks = self._tracker.update(high, low, now, max_lost_s, dt=dt_rel)

        full: list[_TrackedPerson] = []
        matched: list[DetectionBox] = []
        for track in tracks:
            x1, y1, x2, y2 = track.tlbr
            box = DetectionBox(x1=x1, y1=y1, x2=x2, y2=y2, confidence=track.score, track_id=track.track_id)
            full.append(_TrackedPerson(track_id=track.track_id, box=box, last_seen=track.last_seen))
            if track.state == "tracked":
                matched.append(box)

        # Keep the highest-confidence detections when over the cap, matching the
        # documented "Max Persons ... (highest-confidence first)" contract.
        matched.sort(key=lambda b: b.confidence, reverse=True)
        matched = matched[: self._config.max_persons]

        with self._track_lock:
            self._tracked = full
        with self._perf_lock:
            self._tracked_count = len(full)
        return matched

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Background inference loop."""
        try:
            self._run_inner()
        except Exception:
            logger.exception("PersonDetector thread crashed")
        finally:
            # The worker has exited – on a clean stop() OR an unexpected
            # crash. Clear _running so performance_stats stops advertising a
            # live worker; otherwise diagnostics report running=True forever
            # while no detections are produced.
            self._running = False

    def _run_inner(self) -> None:
        from gi.repository import Gst

        # ``_run_inner`` only runs in the background thread spawned by
        # ``start()``, which already guards ``_backend`` against None.
        # Strict typing can't propagate that across the thread
        # boundary, so we re-check here.
        if self._backend is None:
            return

        while self._running:
            # Apply any staged config update before reading per-iteration
            # values – the new ``interval_ms`` / ``confidence`` /
            # ``max_persons`` should affect this frame, not the next one.
            self._drain_pending_config()

            interval_ns = int(self._config.interval_ms * 1_000_000)
            backend = self._backend
            # pragma: no cover – defence-in-depth. ``_load_backend``
            # only assigns ``self._backend`` on a successful attempt and
            # ``_drain_pending_config`` restores the prior backend on
            # rebuild failure, so this guard only fires if a caller
            # nulls out ``_backend`` directly mid-loop.
            if backend is None:  # pragma: no cover
                return

            # Re-read ``_appsink`` each iteration: live pipeline rebuilds
            # (``GstNativeSinkReceiver.swap_input`` →
            # ``ReceiverPipelineAssembler._add_detection_branch``) call
            # ``set_appsink`` with the new sink, and we must observe
            # that update or we'd keep pulling from a disposed
            # element. The ``is None`` arm is defensive: a transient
            # gap between pipelines briefly skips a tick rather than
            # raising ``AttributeError``.
            appsink = self._appsink
            if appsink is None:  # pragma: no cover – narrow gap during a swap
                continue

            sample = appsink.try_pull_sample(interval_ns)
            if sample is None:
                with self._perf_lock:
                    self._sample_timeouts += 1
                continue

            frame = self._sample_to_numpy(sample, Gst)
            if frame is None:
                with self._perf_lock:
                    self._sample_failures += 1
                continue

            frame = self._preprocess(frame)

            # Pull detections down to the ByteTrack low floor (the tracker's
            # second association needs the dim boxes); split high/low in
            # ``_track``. A generous cap lets the recovery band through while
            # bounding association cost.
            score_floor = min(LOW_DETECTION_THRESHOLD, self._config.confidence)
            raw_cap = max(self._config.max_persons * 3, _RAW_DETECTION_CAP)

            infer_started = time.perf_counter()
            try:
                boxes = backend.predict(
                    frame=frame,
                    confidence=score_floor,
                    max_persons=raw_cap,
                    inference_size=self._inference_size,
                )
            except Exception as exc:
                logger.debug("Detection inference error (%s): %s", self._backend_name, exc)
                with self._perf_lock:
                    self._inference_errors += 1
                continue
            inference_ms = (time.perf_counter() - infer_started) * 1000.0

            # Associate detections to tracklets (two-stage ByteTrack). Guarded
            # like inference: a numerical edge in the tracker (e.g. a singular
            # Kalman matrix raising LinAlgError) must drop this frame, not kill
            # the worker for the rest of the session. The tracker self-heals on
            # the next frame's predict/associate pass.
            try:
                boxes = self._track(boxes)
            except Exception as exc:
                logger.debug("Detection tracking error: %s", exc)
                with self._perf_lock:
                    self._inference_errors += 1
                continue

            with self._track_lock:
                self._results = boxes
            with self._perf_lock:
                self._inference_count += 1
                self._inference_total_ms += inference_ms
                self._inference_last_ms = inference_ms
                self._inference_max_ms = max(self._inference_max_ms, inference_ms)
                self._inference_history_ms.append(inference_ms)
                self._detections_last = len(boxes)
                self._detections_total += len(boxes)
                self._last_inference_monotonic = time.monotonic()

    def _preprocess(self, frame: npt.NDArray[Any]) -> npt.NDArray[Any]:
        """Apply CLAHE to normalise harsh stage lighting before inference."""
        if self._clahe is None:
            return frame
        lab = cv2.cvtColor(frame, cv2.COLOR_RGB2LAB)
        lab[:, :, 0] = self._clahe.apply(lab[:, :, 0])
        return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)  # type: ignore[no-any-return, unused-ignore]

    @staticmethod
    def _sample_to_numpy(sample: Any, Gst: Any) -> npt.NDArray[Any] | None:  # noqa: N803
        """Convert a GStreamer sample to a NumPy RGB array."""
        buf = sample.get_buffer()
        caps = sample.get_caps()
        structure = caps.get_structure(0)
        w = structure.get_value("width")
        h = structure.get_value("height")

        success, mapinfo = buf.map(Gst.MapFlags.READ)
        if not success:
            return None
        try:
            if w <= 0 or h <= 0:
                return None
            data = mapinfo.data
            row_bytes = w * 3
            # GStreamer pads each row up to a 4-byte boundary, so for a width
            # where w*3 isn't a multiple of 4 the buffer is larger than
            # h*w*3 (rowstride > w*3). Derive the real stride and slice each
            # row back to the packed width rather than assuming a tight
            # h*w*3 layout – which would raise on every frame for such widths.
            # A short/truncated buffer makes the final reshape raise
            # ValueError, which is caught below and counted as a failure.
            stride = len(data) // h
            arr = (
                np.frombuffer(data, dtype=np.uint8)[: stride * h]
                .reshape(h, stride)[:, :row_bytes]
                .reshape(h, w, 3)
                .copy()
            )
        except ValueError:
            return None
        finally:
            buf.unmap(mapinfo)
        return arr
