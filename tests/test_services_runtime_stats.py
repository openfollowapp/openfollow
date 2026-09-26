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
* ``get_runtime_stats_snapshot`` – defensive deep copy + the frame-clock
  liveness overlay.
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
from openfollow.video.failure import ConnectionPhase, SourceKind, VideoFailure

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
        self._last_animate_time: float | None = None
        self._last_frame_completed: float | None = None
        self._frame_stalled = False


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
    """Models the real marker, including that readers take one snapshot.

    ``NdiStatusMarker`` publishes its fields as an immutable unit and
    requires multi-field readers to go through ``snapshot()``; a fake that also
    answered bare property reads would let that contract be broken silently.
    """

    status: SimpleNamespace = field(default_factory=lambda: SimpleNamespace(name="PLAYING"))
    is_connected: bool = True
    reconnect_attempt: int = 2
    error_message: str = "prev reset"
    failure: VideoFailure = VideoFailure.UNAUTHORIZED
    phase: ConnectionPhase = ConnectionPhase.DECODING
    source_name: str = "CAM1"

    def snapshot(self) -> _FakeStatusMarker:
        return self


class _FakeReceiver:
    def __init__(self) -> None:
        self.status_marker = _FakeStatusMarker()
        self.resolution = (1920, 1080)
        self.source_name = "CAM1"
        self.source_selection_active = False
        self.source_framerate = 59.94
        self.source_kind = SourceKind.REMOTE


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
        # Machine-readable for support tooling, plus the sentence the panel and
        # the HUD both render, so the two cannot describe the same failure
        # differently.
        assert video["failure"] == "unauthorized"
        assert video["failure_text"] == "CAM1 rejected the login."
        assert video["phase"] == "decoding"

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
        # Same keys either way: a consumer must not have to branch on whether a
        # receiver happened to exist when the snapshot was taken.
        assert video["failure"] == "none"
        assert video["failure_text"] == ""
        assert video["phase"] == "starting"

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

    def test_frame_liveness_is_read_live_not_taken_from_the_snapshot(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stalled frame loop freezes the published snapshot along with
        everything else, so the age has to be measured when the caller reads
        rather than when the loop last published. This is the one figure that
        can tell a reader the rest of the payload is stale."""
        self._prime(services)
        import openfollow.video.detection as det

        monkeypatch.setattr(det, "check_detection_dependencies", lambda cfg: [])

        services._app._last_frame_completed = 500.0
        monkeypatch.setattr(services_module.time, "perf_counter", lambda: 500.0)
        services.publish_runtime_stats(force=True)

        # The loop is now wedged: no further publish happens, only wall time moves.
        monkeypatch.setattr(services_module.time, "perf_counter", lambda: 512.0)
        assert services.get_runtime_stats_snapshot()["playback"]["seconds_since_last_frame"] == 12.0
        monkeypatch.setattr(services_module.time, "perf_counter", lambda: 530.0)
        assert services.get_runtime_stats_snapshot()["playback"]["seconds_since_last_frame"] == 30.0

    def test_stall_flag_mirrors_the_watchdog_verdict(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._prime(services)
        import openfollow.video.detection as det

        monkeypatch.setattr(det, "check_detection_dependencies", lambda cfg: [])
        services.publish_runtime_stats(force=True)

        assert services.get_runtime_stats_snapshot()["playback"]["stalled"] is False
        services._app._frame_stalled = True
        assert services.get_runtime_stats_snapshot()["playback"]["stalled"] is True

    def test_the_stale_threshold_is_published_for_the_ui(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The UI compares the age against this rather than a literal, so the
        chip and the four output protocols cannot drift apart on the threshold."""
        from openfollow.psn import MARKER_STALE_AFTER_S

        self._prime(services)
        import openfollow.video.detection as det

        monkeypatch.setattr(det, "check_detection_dependencies", lambda cfg: [])
        services.publish_runtime_stats(force=True)
        assert services.get_runtime_stats_snapshot()["playback"]["stale_after_s"] == MARKER_STALE_AFTER_S

    def test_frame_age_is_none_before_the_first_frame(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._prime(services)
        import openfollow.video.detection as det

        monkeypatch.setattr(det, "check_detection_dependencies", lambda cfg: [])
        services.publish_runtime_stats(force=True)

        services._app._last_frame_completed = None
        assert services.get_runtime_stats_snapshot()["playback"]["seconds_since_last_frame"] is None

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
        assert snap["system"]["hud_fps"] == 0.0
        # No overlay renderer ⇒ nothing is drawing ⇒ no canvas to report.
        assert snap["system"]["output_resolution"] is None

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

    def test_delegates_to_handler_with_short_budget(self, services: AppRuntimeServices) -> None:
        captured: dict[str, float] = {}

        def _detect(*, timeout: float) -> int:
            captured["timeout"] = timeout
            return 3

        services._app._input_manager = SimpleNamespace(
            mouse3d_manager=SimpleNamespace(detect_pressed_button=_detect),
        )
        assert services._mouse3d_latest_button() == 3
        # Bounded so the web request thread returns promptly (client re-polls).
        assert captured["timeout"] <= 0.5


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
            port_key="usb:h:1",
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
                "port_key": "usb:h:1",
            }
        ]


# --------------------------------------------------------------------------- #
# Device figures – overlay redraw rate + output resolution
# --------------------------------------------------------------------------- #


class _FakeSizedCanvas:
    def __init__(self, size: tuple[int, int] = (1920, 1200)) -> None:
        self._size = size

    def get_canvas_size(self) -> tuple[int, int]:
        return self._size


class _TearingStatusMarker:
    """A marker that changes generation between bare property reads.

    The real ``NdiStatusMarker`` publishes its four fields as one immutable
    unit precisely because a writer on the GStreamer bus thread can land
    between a reader's property accesses. A fake that answers every read from
    the same object cannot tell the two reading styles apart, so this one
    advances on each bare read and freezes on ``snapshot()``.
    """

    _GENERATIONS = (
        SimpleNamespace(
            status=SimpleNamespace(name="CONNECTED"),
            is_connected=True,
            reconnect_attempt=0,
            error_message="",
            failure=VideoFailure.NONE,
            phase=ConnectionPhase.DECODING,
        ),
        SimpleNamespace(
            status=SimpleNamespace(name="DISCONNECTED"),
            is_connected=False,
            reconnect_attempt=3,
            error_message="Unauthorized",
            failure=VideoFailure.UNAUTHORIZED,
            phase=ConnectionPhase.STREAM_DESCRIBED,
        ),
    )

    def __init__(self) -> None:
        self._n = 0

    def _current(self) -> SimpleNamespace:
        return self._GENERATIONS[min(self._n, len(self._GENERATIONS) - 1)]

    def _advance(self) -> SimpleNamespace:
        current = self._current()
        self._n += 1
        return current

    @property
    def status(self) -> SimpleNamespace:
        return self._advance().status

    @property
    def is_connected(self) -> bool:
        return bool(self._advance().is_connected)

    @property
    def reconnect_attempt(self) -> int:
        return int(self._advance().reconnect_attempt)

    @property
    def error_message(self) -> str:
        return str(self._advance().error_message)

    def snapshot(self) -> SimpleNamespace:
        return self._current()


class TestStatusIsReadAsOneUnit:
    def test_a_mid_read_transition_cannot_publish_a_mixed_state(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Four separate reads can each land in a different generation.

        The Video panel decides whether to raise the failure banner from
        ``connected`` and ``error_message`` together, so a torn read can report
        a connected pipeline that is not connected - and swallow the banner for
        that poll.
        """
        import openfollow.video.detection as det

        monkeypatch.setattr(det, "check_detection_dependencies", lambda cfg: [])
        receiver = _FakeReceiver()
        receiver.status_marker = _TearingStatusMarker()  # type: ignore[assignment]
        services._system_stats = _FakeSystemStatsCollector()
        services._overlay_renderer = _FakeOverlayRenderer()
        services._app._video_receiver = receiver

        services.publish_runtime_stats(force=True)
        video = services.get_runtime_stats_snapshot()["video"]

        assert (video["pipeline_state"] == "connected") is video["connected"]
        assert bool(video["error_message"]) is not video["connected"]


class TestDeviceFigures:
    """The redraw rate and the canvas size describe the station, not the feed.

    An operator reading a healthy redraw rate next to a resolution concluded
    video was flowing when nothing had ever connected, so these two live under
    Device and the video section carries neither.
    """

    def _prime(
        self,
        services: AppRuntimeServices,
        *,
        hud_fps: float,
        canvas: Any,
        receiver: _FakeReceiver | None = None,
    ) -> None:
        services._system_stats = _FakeSystemStatsCollector()
        services._overlay_renderer = _FakeOverlayRenderer(fps=hud_fps)
        services._app._canvas = canvas
        services._app._video_receiver = receiver

    def _publish(self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        import openfollow.video.detection as det

        monkeypatch.setattr(det, "check_detection_dependencies", lambda cfg: [])
        services.publish_runtime_stats(force=True)
        return services.get_runtime_stats_snapshot()

    def test_publishes_redraw_rate_and_live_canvas_under_device(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._prime(services, hud_fps=59.75, canvas=_FakeSizedCanvas((1920, 1200)))
        snap = self._publish(services, monkeypatch)
        assert snap["system"]["hud_fps"] == 59.75
        assert snap["system"]["output_resolution"] == {"width": 1920, "height": 1200}

    @pytest.mark.parametrize("receiver", [None, _FakeReceiver()], ids=["no-receiver", "receiver"])
    def test_video_section_carries_no_device_figure(
        self,
        services: AppRuntimeServices,
        monkeypatch: pytest.MonkeyPatch,
        receiver: _FakeReceiver | None,
    ) -> None:
        """The redraw rate is not a third measurement of the feed, so it is not
        reported beside the ones that are. Both branches of the video snapshot
        are checked: the live one is the only one production takes.
        """
        self._prime(services, hud_fps=59.75, canvas=_FakeSizedCanvas(), receiver=receiver)
        snap = self._publish(services, monkeypatch)
        assert "fps" not in snap["video"]
        assert snap["system"]["hud_fps"] == 59.75

    def test_headless_station_reports_no_canvas(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With no compositor frame clock nothing draws, and the window's
        allocation falls back to the *requested* size - which under fullscreen
        is routinely not the real canvas. Report nothing rather than that.
        """
        self._prime(services, hud_fps=0.0, canvas=_FakeSizedCanvas((1280, 720)))
        snap = self._publish(services, monkeypatch)
        assert snap["system"]["hud_fps"] == 0.0
        assert snap["system"]["output_resolution"] is None

    @pytest.mark.parametrize("canvas", [None, SimpleNamespace(), _FakeSizedCanvas((0, 0))])
    def test_unusable_canvas_reports_none(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch, canvas: Any
    ) -> None:
        self._prime(services, hud_fps=59.75, canvas=canvas)
        snap = self._publish(services, monkeypatch)
        assert snap["system"]["output_resolution"] is None

    def test_a_raising_canvas_read_cannot_stall_the_frame_loop(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``publish_runtime_stats`` runs on the frame loop *ahead* of the
        liveness stamp. A raising GTK read here would stop
        ``_last_frame_completed`` advancing, so the watchdog would report a
        permanent stall - and freeze every other figure on the page - for a
        loop that is in fact running. One unreadable row is the cheap loss.
        """

        class _RaisingCanvas:
            def get_canvas_size(self) -> tuple[int, int]:
                raise RuntimeError("window is being destroyed")

        self._prime(services, hud_fps=59.75, canvas=_RaisingCanvas())
        snap = self._publish(services, monkeypatch)
        assert snap["system"]["output_resolution"] is None
        # The rest of the snapshot still published.
        assert snap["system"]["hud_fps"] == 59.75
        assert snap["system"]["ip"] == "10.0.0.7"

    def test_a_canvas_returning_the_wrong_shape_reports_none(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same contract for the unpack: a non-pair raises ``ValueError``."""

        class _WrongShapeCanvas:
            def get_canvas_size(self) -> Any:
                return (1920, 1200, 60)

        self._prime(services, hud_fps=59.75, canvas=_WrongShapeCanvas())
        snap = self._publish(services, monkeypatch)
        assert snap["system"]["output_resolution"] is None


class TestTheSentenceNeverContradictsTheState:
    """``failure_text`` is published for support tooling to read literally."""

    def _video(self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch, failure: VideoFailure) -> dict:
        receiver = _FakeReceiver()
        receiver.status_marker.failure = failure
        receiver.status_marker.is_connected = failure is VideoFailure.NONE
        TestPublishRuntimeStats._prime(self, services, receiver=receiver)
        import openfollow.video.detection as det

        monkeypatch.setattr(det, "check_detection_dependencies", lambda cfg: [])
        services.publish_runtime_stats(force=True)
        return services.get_runtime_stats_snapshot()["video"]

    def test_a_connect_attempt_does_not_claim_video_is_arriving(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Seen live: mid-swap the marker reads NONE while the feed is down,
        and the payload asserted "Video is arriving." beside connected=false."""
        video = self._video(services, monkeypatch, VideoFailure.NONE)
        assert video["failure_text"] == ""

    def test_an_unreadable_failure_publishes_no_sentence(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        video = self._video(services, monkeypatch, VideoFailure.UNKNOWN)
        assert video["failure_text"] == ""
        assert video["failure"] == "unknown"

    def test_a_classified_failure_still_publishes_one(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        video = self._video(services, monkeypatch, VideoFailure.UNREACHABLE)
        assert video["failure_text"] == "Nothing answered at CAM1."


class TestTheSentenceNamesTheSourceThatFailed:
    """Seen on a real NDI camera: the box read "Video from the video source
    stopped arriving" about a named source that had been on screen.

    Falling back to the picker clears the selection from the input's config, so
    a live read loses the name at the moment it matters most. The snapshot kept
    what it had when it connected.
    """

    def test_it_uses_the_name_the_verdict_was_published_with(
        self, services: AppRuntimeServices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        receiver = _FakeReceiver()
        receiver.status_marker.failure = VideoFailure.STALLED
        receiver.status_marker.is_connected = False
        receiver.status_marker.source_name = "AIDA NDI POV (HX-Stream)"
        receiver.source_name = ""  # what the picker fallback leaves behind
        TestPublishRuntimeStats._prime(self, services, receiver=receiver)
        import openfollow.video.detection as det

        monkeypatch.setattr(det, "check_detection_dependencies", lambda cfg: [])

        services.publish_runtime_stats(force=True)
        video = services.get_runtime_stats_snapshot()["video"]

        assert "AIDA NDI POV (HX-Stream)" in video["failure_text"]
        assert "the video source" not in video["failure_text"]
