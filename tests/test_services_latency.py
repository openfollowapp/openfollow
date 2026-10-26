# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""A camera latency run through ``AppRuntimeServices``: what starts it, what the overlay shows,
when the receiver hands frames over, and what ends it."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

import openfollow.services as services_module
import openfollow.video.frame_luma as frame_luma_module
from openfollow.configuration import AppConfig
from openfollow.runtime import latency_probe as lp
from openfollow.runtime.overlay_state import OverlayState
from openfollow.services import AppRuntimeServices

pytestmark = pytest.mark.unit


@dataclass
class _Receiver:
    connected: bool = True
    pipeline_builds: int = 1
    listeners: list[Any] = field(default_factory=list)

    @property
    def status_marker(self) -> Any:
        return SimpleNamespace(snapshot=lambda: SimpleNamespace(is_connected=self.connected))

    def set_frame_listener(self, listener: Any) -> None:
        self.listeners.append(listener)

    @property
    def listener(self) -> Any:
        return self.listeners[-1] if self.listeners else None


@pytest.fixture
def clock() -> list[int]:
    return [10_000_000]


@pytest.fixture
def services(monkeypatch: pytest.MonkeyPatch, clock: list[int]) -> AppRuntimeServices:
    monkeypatch.setattr(services_module.AppRuntimeServices, "_setup_gc_tuning", staticmethod(lambda: None))
    monkeypatch.setattr(services_module.AppRuntimeServices, "_is_raspberry_pi", staticmethod(lambda: False))
    app = SimpleNamespace(
        _config=AppConfig(video_source_type="rtsp"),
        _canvas=None,
        _video_receiver=_Receiver(),
        _input_manager=None,
        _controlled_ids=[],
        _detection_pin_states={},
        _last_frame_completed=None,
        _frame_stalled=False,
    )
    svc = AppRuntimeServices(app)
    svc._monotonic_us = lambda: clock[0]
    # A screen is attached.
    svc._hud_render = SimpleNamespace(stats=lambda: None)
    svc._overlay_renderer = SimpleNamespace(state=None, measured_fps=lambda: 0.0)
    flashes: list[float | None] = []

    def build(_app: Any, **kwargs: Any) -> OverlayState:
        flashes.append(kwargs["latency_flash"])
        return OverlayState(latency_flash=kwargs["latency_flash"])

    monkeypatch.setattr(services_module, "build_marker_visual_state", build)
    # Frames carry their luma as the buffer.
    monkeypatch.setattr(frame_luma_module, "frame_mean_luma", lambda buffer, _caps: buffer)
    svc.flashes = flashes  # type: ignore[attr-defined]
    return svc


def _frame(services: AppRuntimeServices, clock: list[int], step_us: int = 16_000) -> None:
    clock[0] += step_us
    services.update_marker_visuals(step_us / 1e6)


def test_a_run_hands_its_level_to_the_overlay_and_frames_to_the_probe(
    services: AppRuntimeServices, clock: list[int]
) -> None:
    receiver = services._app._video_receiver
    services.start_latency_run()
    assert receiver.listener is not None
    _frame(services, clock)
    assert services._overlay_renderer.state.latency_flash == lp.BLACK
    assert services.latency_status()["state"] == "running"


def test_a_run_that_times_out_lets_go_of_the_frames_by_itself(services: AppRuntimeServices, clock: list[int]) -> None:
    receiver = services._app._video_receiver
    services.start_latency_run()
    for _ in range((lp.SETTLE_US + lp.TIMEOUT_US) // 16_000 + 2):
        _frame(services, clock)
    assert services.latency_status()["failure"]["reason"] == "no_rise"
    assert receiver.listener is None
    assert services.flashes[-1] is None


@pytest.mark.parametrize("change", ["rebuild", "lost"])
def test_a_rebuilt_pipeline_or_a_lost_feed_ends_the_run(
    services: AppRuntimeServices, clock: list[int], change: str
) -> None:
    receiver = services._app._video_receiver
    services.start_latency_run()
    _frame(services, clock)
    if change == "rebuild":
        receiver.pipeline_builds += 1
    else:
        receiver.connected = False
    _frame(services, clock)
    assert services.latency_status()["failure"]["reason"] == "source_changed"
    assert receiver.listener is None


def test_a_source_type_swap_ends_the_run(services: AppRuntimeServices, clock: list[int]) -> None:
    services.start_latency_run()
    services._app._config.video_source_type = "srt"
    _frame(services, clock)
    assert services.latency_status()["failure"]["reason"] == "source_changed"


@pytest.mark.parametrize(
    ("setup", "reason"),
    [("no-display", "no_display"), ("gallery", "media_gallery"), ("no-video", "no_video")],
)
def test_a_run_that_cannot_start_says_why_and_takes_no_frames(
    services: AppRuntimeServices, setup: str, reason: str
) -> None:
    receiver = services._app._video_receiver
    if setup == "no-display":
        services._hud_render = None
    elif setup == "gallery":
        services._app._config.video_source_type = lp.MEDIA_GALLERY
    else:
        receiver.connected = False
    services.start_latency_run()
    assert services.latency_status()["state"] == "failed"
    assert services.latency_status()["failure"]["reason"] == reason
    assert receiver.listeners == []


def test_idle_the_receiver_is_never_handed_a_listener_and_the_overlay_no_flash(
    services: AppRuntimeServices, clock: list[int]
) -> None:
    for _ in range(5):
        _frame(services, clock)
    assert services._app._video_receiver.listeners == []
    assert services.flashes == [None] * 5


def test_a_start_during_a_run_lets_it_carry_on(services: AppRuntimeServices, clock: list[int]) -> None:
    receiver = services._app._video_receiver
    services.start_latency_run()
    for _ in range(lp.SETTLE_US // 16_000 + 2):
        _frame(services, clock)
    assert services._overlay_renderer.state.latency_flash == lp.WHITE
    services.start_latency_run()
    _frame(services, clock)
    # Not back to the settle's black, and the receiver was handed the probe once.
    assert services._overlay_renderer.state.latency_flash == lp.WHITE
    assert len(receiver.listeners) == 1


def test_the_last_success_is_published_with_its_age(services: AppRuntimeServices, clock: list[int]) -> None:
    assert services.latency_last_success() is None
    services._latency_probe.last_success = lp.LatencyResult(
        median_ms=84.0,
        min_ms=70.0,
        max_ms=99.0,
        samples=10,
        finished_at=1_700_000_000.0,
        finished_us=clock[0] - 30_000_000,
        source_type="rtsp",
    )
    published = services.latency_last_success()
    assert published is not None
    assert published["median_ms"] == 84.0 and published["age_s"] == pytest.approx(30.0)
    services._app._video_receiver = None
    services.publish_runtime_stats(force=True)
    assert services.get_runtime_stats_snapshot()["latency"]["median_ms"] == 84.0


def test_a_complete_run_is_kept_as_the_last_success_and_logged(
    services: AppRuntimeServices, clock: list[int], caplog: pytest.LogCaptureFixture
) -> None:
    receiver = services._app._video_receiver
    probe = services._latency_probe
    services.start_latency_run()
    # The screen shows each level at the next refresh; the camera sees it 3 frames later.
    seen: list[tuple[int, float]] = []
    with caplog.at_level("INFO", logger=services_module.__name__):
        while probe.active:
            level = probe.screen_level()
            if level is not None:
                probe.presented(level, clock[0])
                if not seen or seen[-1][1] != level:
                    seen.append((clock[0], level))
            visible = [lv for at, lv in seen if clock[0] - at >= 48_000]
            receiver.listener(0.1 + 0.7 * (visible[-1] if visible else 0.0), None)
            _frame(services, clock)
    assert services.latency_status()["state"] == "done"
    assert services.latency_last_success()["samples"] == lp.SAMPLES
    assert receiver.listener is None
    assert "Camera latency: median" in caplog.text


def test_a_frame_that_cannot_be_read_ends_the_run_and_still_reaches_the_screen(
    services: AppRuntimeServices, clock: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_buffer: Any, _caps: Any) -> float:
        raise TypeError("not a video buffer")

    monkeypatch.setattr(frame_luma_module, "frame_mean_luma", broken)
    receiver = services._app._video_receiver
    services.start_latency_run()
    assert receiver.listener("buffer", "caps") is None
    _frame(services, clock)
    assert services.latency_status()["failure"]["reason"] == "unsupported_format"


def test_a_run_ends_cleanly_when_the_receiver_is_gone(services: AppRuntimeServices, clock: list[int]) -> None:
    services.start_latency_run()
    services._app._video_receiver = None
    _frame(services, clock)
    assert services.latency_status()["failure"]["reason"] == "source_changed"
