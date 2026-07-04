# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for ``AppRuntimeServices`` runtime-stats plumbing.

This covers:

* ``_default_runtime_stats_snapshot`` – the bootstrap payload before the
  first ``publish_runtime_stats`` tick.
* ``_resolve_detection_missing_deps`` – TTL + signature cache over
  ``check_detection_dependencies``.
* ``publish_runtime_stats`` – throttling, receiver-present / receiver-absent
  branches, and the controller + detection sub-sections.
* ``get_runtime_stats_snapshot`` – defensive deep copy.
* ``update_window_title`` – the canvas title delegate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

import openfollow.services as services_module
from openfollow.configuration import AppConfig
from openfollow.services import AppRuntimeServices

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _DummyApp:
    def __init__(self, *, config: AppConfig | None = None) -> None:
        self._config = config or AppConfig(psn_system_name="OpenFollow Test")
        self._canvas = None
        self._video_receiver = None
        self._input_manager = None


@dataclass
class _SystemStats:
    cpu_percent: float = 30.0
    ram_percent: float = 42.0
    temperature: float | None = 55.5
    ip_address: str = "10.0.0.7"


class _FakeSystemStatsCollector:
    def __init__(self, stats: _SystemStats | None = None) -> None:
        self._stats = stats or _SystemStats()

    @property
    def stats(self) -> _SystemStats:
        return self._stats


class _FakeOverlayRenderer:
    def __init__(self, fps: float = 59.75) -> None:
        self._fps = fps

    def measured_fps(self) -> float:
        return self._fps


@dataclass
class _FakeStatusMarker:
    status: SimpleNamespace = field(default_factory=lambda: SimpleNamespace(name="PLAYING"))
    is_connected: bool = True
    reconnect_attempt: int = 2
    error_message: str = "prev reset"


class _FakeReceiver:
    def __init__(self) -> None:
        self.status_marker = _FakeStatusMarker()
        self.resolution = (1920, 1080)
        self.source_name = "CAM1"
        self.source_selection_active = False
        self.source_framerate = 59.94


class _FakeDetector:
    performance_stats: dict[str, Any] = {
        "enabled": True,
        "available": True,
        "running": True,
        "model": "yolov8n.onnx",
        "interval_ms": 100,
        "inference_count": 12,
        "inference_hz": 10.0,
        "inference_avg_ms": 9.5,
        "inference_p95_ms": 12.0,
        "inference_max_ms": 18.0,
        "inference_last_ms": 9.0,
        "inference_errors": 0,
        "sample_timeouts": 0,
        "sample_failures": 0,
        "detections_last": 2,
        "detections_avg": 1.7,
        "tracked_people": 2,
        "pinned_track_id": 42,
        "last_inference_age_ms": 12.0,
    }


class _FakeInputManager:
    def __init__(self, items: list[dict] | None = None) -> None:
        self._items = items or []

    def get_controller_info(self) -> list[dict]:
        return list(self._items)


class _FakeCanvas:
    def __init__(self) -> None:
        self.title_history: list[str] = []

    def set_title(self, title: str) -> None:
        self.title_history.append(title)


# --------------------------------------------------------------------------- #
# Construction helper
# --------------------------------------------------------------------------- #


@pytest.fixture
def services(monkeypatch: pytest.MonkeyPatch) -> AppRuntimeServices:
    """Construct a bare ``AppRuntimeServices`` with every hardware
    check neutralised.  The individual tests populate the fields they
    need.
    """
    monkeypatch.setattr(services_module, "gst_runtime_available", lambda: True)
    monkeypatch.setattr(
        services_module.AppRuntimeServices,
        "_setup_gc_tuning",
        staticmethod(lambda: None),
    )
    monkeypatch.setattr(
        services_module.AppRuntimeServices,
        "_is_raspberry_pi",
        staticmethod(lambda: False),
    )
    return AppRuntimeServices(_DummyApp())


# --------------------------------------------------------------------------- #
# _default_runtime_stats_snapshot
# --------------------------------------------------------------------------- #


class TestDefaultRuntimeStatsSnapshot:
    def test_payload_shape(self, services: AppRuntimeServices) -> None:
        snap = services._default_runtime_stats_snapshot()
        assert set(snap) == {"timestamp", "system", "video", "controllers", "playback", "tracking"}
        assert snap["system"]["cpu_percent"] == 0.0
        assert snap["video"]["connected"] is False
        assert snap["controllers"]["items"] == []
        assert snap["tracking"]["enabled"] is False

    def test_reflects_video_source_type_from_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services_module, "gst_runtime_available", lambda: True)
        monkeypatch.setattr(
            services_module.AppRuntimeServices,
            "_setup_gc_tuning",
            staticmethod(lambda: None),
        )
        cfg = AppConfig(psn_system_name="X", video_source_type="rtsp")
        svc = AppRuntimeServices(_DummyApp(config=cfg))
        snap = svc._default_runtime_stats_snapshot()
        assert snap["video"]["source_type"] == "rtsp"


# --------------------------------------------------------------------------- #
# _resolve_detection_missing_deps – TTL cache
# --------------------------------------------------------------------------- #


class TestResolveDetectionMissingDeps:
    def _patch_check(self, monkeypatch: pytest.MonkeyPatch, values: list[list[str]]) -> list[Any]:
        calls: list[Any] = []

        def _spy(cfg: Any) -> list[str]:
            calls.append(cfg)
            return values[len(calls) - 1] if len(calls) <= len(values) else values[-1]

        # The function is re-imported inside the method via
        # ``from openfollow.video.detection import check_detection_dependencies``
        # so we patch it on that submodule.
        import openfollow.video.detection as detection_module

        monkeypatch.setattr(detection_module, "check_detection_dependencies", _spy)
        return calls

    def test_fresh_call_populates_cache(self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._patch_check(monkeypatch, [["numpy", "onnx"]])
        cfg = services._app._config.detection

        out = services._resolve_detection_missing_deps(cfg, now_monotonic=100.0)
        assert out == ["numpy", "onnx"]
        assert len(calls) == 1
        # Cached signature reflects the arg.
        assert services._detection_deps_cache == ["numpy", "onnx"]

    def test_cache_hit_skips_underlying_call(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._patch_check(monkeypatch, [["x"]])
        cfg = services._app._config.detection
        services._resolve_detection_missing_deps(cfg, now_monotonic=100.0)
        services._resolve_detection_missing_deps(cfg, now_monotonic=102.0)  # within TTL
        # Only the first call actually invokes the underlying probe.
        assert len(calls) == 1

    def test_cache_expires_after_ttl(self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._patch_check(monkeypatch, [["a"], ["b"]])
        cfg = services._app._config.detection
        services._resolve_detection_missing_deps(cfg, now_monotonic=100.0)
        services._resolve_detection_missing_deps(cfg, now_monotonic=200.0)  # > TTL
        assert len(calls) == 2

    def test_cache_invalidates_on_signature_change(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._patch_check(monkeypatch, [["a"], ["b"]])

        cfg_a = services._app._config.detection
        cfg_b = AppConfig(psn_system_name="x").detection
        # Force different repr by setting a different attribute on a shallow copy.
        from dataclasses import replace

        cfg_b = replace(cfg_b, model="yolov8s.onnx")

        services._resolve_detection_missing_deps(cfg_a, now_monotonic=100.0)
        services._resolve_detection_missing_deps(cfg_b, now_monotonic=100.0)
        assert len(calls) == 2

    def test_returns_list_is_defensively_copied(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_check(monkeypatch, [["a", "b"]])
        cfg = services._app._config.detection
        out = services._resolve_detection_missing_deps(cfg, now_monotonic=100.0)
        out.append("c")
        # Cache unaffected by caller mutation.
        assert services._detection_deps_cache == ["a", "b"]


# --------------------------------------------------------------------------- #
# publish_runtime_stats + get_runtime_stats_snapshot
# --------------------------------------------------------------------------- #


class TestPublishRuntimeStats:
    def _prime(
        self,
        services: AppRuntimeServices,
        *,
        receiver: _FakeReceiver | None = None,
        detector: _FakeDetector | None = None,
        input_manager: _FakeInputManager | None = None,
    ) -> None:
        services._system_stats = _FakeSystemStatsCollector()
        services._overlay_renderer = _FakeOverlayRenderer()
        services._person_detector = detector
        services._app._video_receiver = receiver
        services._app._input_manager = input_manager

    def test_publish_is_throttled_at_runtime_stats_interval(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._prime(services)
        monkeypatch.setattr(
            services_module,
            "check_detection_dependencies",
            lambda cfg: [],
            raising=False,
        )
        import openfollow.video.detection as det

        monkeypatch.setattr(det, "check_detection_dependencies", lambda cfg: [])

        monkeypatch.setattr(services_module.time, "monotonic", lambda: 100.0)
        services.publish_runtime_stats()
        # Second call immediately after – throttled, snapshot unchanged.
        before = services.get_runtime_stats_snapshot()
        monkeypatch.setattr(services_module.time, "monotonic", lambda: 100.1)
        services.publish_runtime_stats()
        after = services.get_runtime_stats_snapshot()
        assert before == after

    def test_force_bypasses_throttle(self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch) -> None:
        self._prime(services)
        import openfollow.video.detection as det

        monkeypatch.setattr(det, "check_detection_dependencies", lambda cfg: [])

        times = iter([100.0, 100.05, 100.10])
        monkeypatch.setattr(services_module.time, "monotonic", lambda: next(times))

        services.publish_runtime_stats()
        t1 = services._last_runtime_stats_publish
        services.publish_runtime_stats(force=True)
        t2 = services._last_runtime_stats_publish
        assert t2 > t1

    def test_receiver_present_populates_detailed_video_snapshot(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._prime(services, receiver=_FakeReceiver())
        import openfollow.video.detection as det

        monkeypatch.setattr(det, "check_detection_dependencies", lambda cfg: [])

        services.publish_runtime_stats(force=True)
        video = services.get_runtime_stats_snapshot()["video"]
        assert video["source_label"] == "CAM1"
        assert video["pipeline_state"] == "playing"
        assert video["connected"] is True
        assert video["resolution"] == {"width": 1920, "height": 1080}
        assert video["source_fps"] == pytest.approx(59.94)

    def test_receiver_absent_uses_default_video_shape(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._prime(services, receiver=None)
        import openfollow.video.detection as det

        monkeypatch.setattr(det, "check_detection_dependencies", lambda cfg: [])

        services.publish_runtime_stats(force=True)
        video = services.get_runtime_stats_snapshot()["video"]
        assert video["pipeline_state"] == "disconnected"
        assert video["connected"] is False
        assert video["resolution"] == {"width": 0, "height": 0}

    def test_controller_counts_aggregate_mapped_vs_connected(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mgr = _FakeInputManager(
            items=[
                {
                    "controller_index": 0,
                    "name": "Xbox",
                    "connected": True,
                    "marker_id": 1,
                    "effective_speed": 1.0,
                    "backend": "pygame",
                },
                {
                    "controller_index": 1,
                    "name": "PS4",
                    "connected": True,
                    "marker_id": None,
                    "effective_speed": 1.0,
                    "backend": "pygame",
                },
                {
                    "controller_index": 2,
                    "name": "8BitDo",
                    "connected": False,
                    "marker_id": 2,
                    "effective_speed": 1.0,
                    "backend": "pygame",
                },
            ]
        )
        self._prime(services, input_manager=mgr)
        import openfollow.video.detection as det

        monkeypatch.setattr(det, "check_detection_dependencies", lambda cfg: [])

        services.publish_runtime_stats(force=True)
        c = services.get_runtime_stats_snapshot()["controllers"]
        assert c["connected_count"] == 2
        assert c["mapped_count"] == 2
        assert [item["name"] for item in c["items"]] == ["Xbox", "PS4", "8BitDo"]

    def test_detector_present_delegates_to_performance_stats(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._prime(services, detector=_FakeDetector())
        import openfollow.video.detection as det

        monkeypatch.setattr(det, "check_detection_dependencies", lambda cfg: [])

        services.publish_runtime_stats(force=True)
        tracking = services.get_runtime_stats_snapshot()["tracking"]
        assert tracking["running"] is True
        assert tracking["pinned_track_id"] == 42
        assert tracking["missing_deps"] == []

    def test_missing_deps_surface_into_tracking_section(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._prime(services)
        import openfollow.video.detection as det

        monkeypatch.setattr(det, "check_detection_dependencies", lambda cfg: ["onnxruntime"])

        services.publish_runtime_stats(force=True)
        tracking = services.get_runtime_stats_snapshot()["tracking"]
        assert tracking["missing_deps"] == ["onnxruntime"]

    def test_snapshot_is_deep_copied_on_read(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._prime(services)
        import openfollow.video.detection as det

        monkeypatch.setattr(det, "check_detection_dependencies", lambda cfg: [])

        services.publish_runtime_stats(force=True)
        a = services.get_runtime_stats_snapshot()
        a["controllers"]["items"].append("mutated")
        b = services.get_runtime_stats_snapshot()
        assert "mutated" not in b["controllers"]["items"]

    def test_system_stats_absent_falls_back_to_defaults(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        services._system_stats = None
        services._overlay_renderer = None  # also exercise the no-overlay branch
        import openfollow.video.detection as det

        monkeypatch.setattr(det, "check_detection_dependencies", lambda cfg: [])

        services.publish_runtime_stats(force=True)
        snap = services.get_runtime_stats_snapshot()
        assert snap["system"]["cpu_percent"] == 0.0
        assert snap["system"]["ram_percent"] == 0.0
        assert snap["system"]["temperature_c"] is None
        assert snap["system"]["ip"] == "N/A"
        assert snap["video"]["fps"] == 0.0

    def test_system_stats_with_none_temperature(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stats = _SystemStats(temperature=None)
        services._system_stats = _FakeSystemStatsCollector(stats)
        services._overlay_renderer = _FakeOverlayRenderer()
        import openfollow.video.detection as det

        monkeypatch.setattr(det, "check_detection_dependencies", lambda cfg: [])

        services.publish_runtime_stats(force=True)
        snap = services.get_runtime_stats_snapshot()
        assert snap["system"]["temperature_c"] is None


# --------------------------------------------------------------------------- #
# update_window_title
# --------------------------------------------------------------------------- #


class TestUpdateWindowTitle:
    def test_sets_canvas_title(self, services: AppRuntimeServices) -> None:
        canvas = _FakeCanvas()
        services._app._canvas = canvas
        services.update_window_title("My Show")
        assert canvas.title_history == ["My Show"]

    def test_empty_title_falls_back_to_default(self, services: AppRuntimeServices) -> None:
        canvas = _FakeCanvas()
        services._app._canvas = canvas
        services.update_window_title("   ")
        assert canvas.title_history == ["OpenFollow"]

    def test_canvas_without_set_title_is_ignored(self, services: AppRuntimeServices) -> None:
        # Canvas without ``set_title`` (e.g. not yet wired) must not crash.
        services._app._canvas = SimpleNamespace()
        services.update_window_title("X")  # should not raise


# --------------------------------------------------------------------------- #
# _gamepad_runtime_snapshot – diagnostics provider
# --------------------------------------------------------------------------- #


class TestMouse3dLatestButton:
    def test_none_without_input_manager(self, services: AppRuntimeServices) -> None:
        services._app._input_manager = None
        assert services._mouse3d_latest_button() is None

    def test_delegates_to_handler(self, services: AppRuntimeServices) -> None:
        services._app._input_manager = SimpleNamespace(
            mouse3d_manager=SimpleNamespace(detect_pressed_button=lambda: 3),
        )
        assert services._mouse3d_latest_button() == 3


class TestGamepadRuntimeSnapshot:
    def test_empty_when_no_input_manager(self, services: AppRuntimeServices) -> None:
        services._app._input_manager = None
        assert services._gamepad_runtime_snapshot() == []

    def test_returns_handler_snapshot_as_dicts(self, services: AppRuntimeServices) -> None:
        from openfollow.input.gamepad import ControllerRuntimeInfo

        info = ControllerRuntimeInfo(
            index=0,
            backend="sdl2_controller",
            name="Xbox",
            guid="g",
            num_axes=6,
            num_buttons=11,
            num_hats=1,
            is_game_controller=True,
            matches_calibration=False,
            calibration_stored=True,
        )
        services._app._input_manager = SimpleNamespace(
            gamepad_handler=SimpleNamespace(runtime_snapshot=lambda: [info]),
        )
        out = services._gamepad_runtime_snapshot()
        assert out == [
            {
                "index": 0,
                "backend": "sdl2_controller",
                "name": "Xbox",
                "guid": "g",
                "num_axes": 6,
                "num_buttons": 11,
                "num_hats": 1,
                "is_game_controller": True,
                "matches_calibration": False,
                "calibration_stored": True,
            }
        ]
