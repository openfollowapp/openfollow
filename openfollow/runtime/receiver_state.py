# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""State machine helpers for GstNativeSinkReceiver connection lifecycle."""

from __future__ import annotations

from dataclasses import dataclass

from openfollow.video.inputs._base import ReconnectPolicy


@dataclass(frozen=True)
class ReconnectSchedule:
    """Computed reconnect timer schedule."""

    attempt: int
    delay_ms: int


class ReceiverStateMachine:
    """Owns receiver connection/reconnect/state transitions."""

    def __init__(self, reconnect_delay: float) -> None:
        self._base_reconnect_delay = max(0.1, reconnect_delay)
        self.current_reconnect_delay = self._base_reconnect_delay
        self.reconnect_attempt = 0
        self.reconnect_source_id: int | None = None
        self.connection_timeout_id: int | None = None

        self.connected = False
        self.video_flow_detected = False
        self.resolution: tuple[int, int] = (0, 0)
        self.source_framerate: float = 0.0
        self.is_placeholder_pipeline = False
        self.source_selection_active = False
        self.was_connected_before_selection = False

    def reset_video_flow(self) -> None:
        self.connected = False
        self.video_flow_detected = False
        self.resolution = (0, 0)
        self.source_framerate = 0.0

    def set_resolution(self, width: int, height: int) -> bool:
        previous = self.resolution
        self.resolution = (width, height)
        return previous != self.resolution

    def set_source_framerate(self, fps: float) -> None:
        self.source_framerate = float(fps)

    def mark_frame_received(self) -> bool:
        """Record that a real decoded frame reached the sink.

        Returns ``True`` only on the disconnected→connected transition (the
        first real frame from a non-placeholder pipeline). Connection is
        driven by actual frame flow, NOT by CAPS events: CAPS is a *sticky*
        event that GStreamer re-delivers to the shared-sink probe whenever a
        new pipeline starts on that sink, so keying "connected" off CAPS made
        reconnect flash the HUD green before actual frames arrived.

        Placeholder frames are ignored entirely: they must neither connect
        nor set ``video_flow_detected`` – a stray placeholder buffer crossing
        the shared sink after ``is_placeholder_pipeline`` flips False would
        otherwise also suppress the real pipeline's connection timeout
        (which gates on ``video_flow_detected``). Only real frames count.
        """
        if self.is_placeholder_pipeline:
            return False
        self.video_flow_detected = True
        if not self.connected:
            self.connected = True
            return True
        return False

    def mark_disconnected(self) -> None:
        self.connected = False

    def set_placeholder_pipeline(self, is_placeholder: bool) -> None:
        self.is_placeholder_pipeline = is_placeholder

    def activate_source_selection(self) -> None:
        self.source_selection_active = True

    def deactivate_source_selection(self) -> None:
        self.source_selection_active = False

    def enter_source_selection(self) -> None:
        self.was_connected_before_selection = self.connected
        self.source_selection_active = True

    def restore_connection_after_selection(self) -> bool:
        if not self.was_connected_before_selection:
            return False
        self.connected = True
        self.was_connected_before_selection = False
        return True

    def reset_reconnect_backoff(self) -> None:
        self.reconnect_attempt = 0
        self.current_reconnect_delay = self._base_reconnect_delay

    def build_reconnect_schedule(self, policy: ReconnectPolicy) -> ReconnectSchedule:
        self.reconnect_attempt += 1
        delay_ms = int(self.current_reconnect_delay * 1000)
        self.current_reconnect_delay = min(
            self.current_reconnect_delay * policy.backoff_multiplier,
            policy.max_delay,
        )
        return ReconnectSchedule(attempt=self.reconnect_attempt, delay_ms=delay_ms)

    def should_fallback_to_placeholder(self, policy: ReconnectPolicy) -> bool:
        max_attempts = policy.max_attempts
        return max_attempts > 0 and self.reconnect_attempt >= max_attempts and policy.fallback_to_selection

    def clear_reconnect_source(self) -> None:
        self.reconnect_source_id = None

    def clear_connection_timeout(self) -> None:
        self.connection_timeout_id = None
