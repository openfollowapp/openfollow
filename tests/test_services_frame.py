# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for ``prepare_overlay_state_swap`` and ``update_video`` in
``runtime/services_frame`` – overlay-state pool swap and per-frame video
resolution / aspect-ratio / first-video logging."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from openfollow.runtime.overlay_state import OverlayState
from openfollow.runtime.services_frame import (
    prepare_overlay_state_swap,
    update_video,
)
from openfollow.runtime_metrics import OverlayStatePool

pytestmark = pytest.mark.unit


class TestPrepareOverlayStateSwap:
    def test_returns_new_state(self) -> None:
        pool = OverlayStatePool(pool_size=2)
        new_state = OverlayState()
        result = prepare_overlay_state_swap(pool, None, new_state)
        assert result is new_state

    def test_releases_old_state_to_pool(self) -> None:
        pool = OverlayStatePool(pool_size=2)
        # Drain pool
        s1 = pool.acquire()
        pool.acquire()
        assert len(pool._pool) == 0

        new_state = OverlayState()
        prepare_overlay_state_swap(pool, s1, new_state)
        # s1 should be back in the pool
        assert len(pool._pool) == 1

    def test_handles_none_old_state(self) -> None:
        pool = OverlayStatePool(pool_size=2)
        initial_pool_size = len(pool._pool)
        new_state = OverlayState()
        result = prepare_overlay_state_swap(pool, None, new_state)
        assert result is new_state
        assert len(pool._pool) == initial_pool_size  # unchanged


# --------------------------------------------------------------------------- #
# update_video – resolution + aspect-lock + first-video logging
# --------------------------------------------------------------------------- #


class _FakeCanvas:
    def __init__(self, has_set_aspect_ratio: bool = True) -> None:
        self.aspect_calls: list[tuple[int, int]] = []
        self._supports_set_aspect_ratio = has_set_aspect_ratio
        if has_set_aspect_ratio:
            # Dynamically add the method so ``hasattr`` returns True.
            self.set_aspect_ratio = self._set_aspect_ratio

    def _set_aspect_ratio(self, w: int, h: int) -> None:
        self.aspect_calls.append((w, h))


def _fake_app(
    *,
    resolution: tuple[int, int] = (1920, 1080),
    canvas: _FakeCanvas | None = None,
    video_logged: bool = False,
    raise_on_resolution: bool = False,
) -> SimpleNamespace:
    class _Receiver:
        def __init__(inner_self) -> None:  # noqa: N805
            inner_self.current = resolution

        @property
        def resolution(inner_self):  # noqa: N805
            if raise_on_resolution:
                raise RuntimeError("pipeline stalled")
            return inner_self.current

    return SimpleNamespace(
        _video_receiver=_Receiver(),
        _canvas=canvas if canvas is not None else _FakeCanvas(),
        _video_logged=video_logged,
        _video_aspect=None,
    )


class TestUpdateVideo:
    def test_logs_once_on_first_valid_resolution(self, caplog) -> None:  # noqa: ANN001
        app = _fake_app(resolution=(1600, 900))
        logger = logging.getLogger("test-update-video")
        with caplog.at_level(logging.INFO, logger="test-update-video"):
            update_video(app, logger)
        assert app._video_logged is True
        assert any("1600x900" in r.message for r in caplog.records)

    def test_does_not_log_twice(self, caplog) -> None:  # noqa: ANN001
        app = _fake_app(resolution=(1600, 900), video_logged=True)
        logger = logging.getLogger("test-update-video")
        with caplog.at_level(logging.INFO, logger="test-update-video"):
            update_video(app, logger)
        # `video_logged=True` must gate out the first-resolution log line –
        # no "Native sink:" records should have been emitted this tick.
        matching = [r for r in caplog.records if "Native sink" in r.message]
        assert matching == []

    def test_unchanged_resolution_does_not_reapply_the_hint(self) -> None:
        canvas = _FakeCanvas(has_set_aspect_ratio=True)
        app = _fake_app(resolution=(1920, 1080), canvas=canvas)
        logger = logging.getLogger("test-update-video")
        update_video(app, logger)
        update_video(app, logger)
        assert canvas.aspect_calls == [(1920, 1080)]
        assert app._video_logged is True

    def test_hint_follows_the_source_across_a_placeholder_fallback(self) -> None:
        """Connect 16:9, drop to the placeholder, reconnect 4:3.

        The window must end up shaped like the source that is actually
        playing: the HUD is projected across the canvas while calibration is
        solved against the input, so a hint left at the first figure of the
        session slides the overlay off the video for the rest of it.
        """
        canvas = _FakeCanvas(has_set_aspect_ratio=True)
        app = _fake_app(resolution=(1920, 1080), canvas=canvas)
        logger = logging.getLogger("test-update-video")
        update_video(app, logger)

        # Placeholder up: no source geometry is published at all.
        app._video_receiver.current = (0, 0)
        update_video(app, logger)

        app._video_receiver.current = (1024, 768)
        update_video(app, logger)

        assert canvas.aspect_calls == [(1920, 1080), (1024, 768)]

    def test_an_aspect_preserving_resolution_change_does_not_re_hint(self) -> None:
        """1920x1080 -> 1280x720 is the same shape, so the window is already
        constrained correctly and there is nothing to re-apply."""
        canvas = _FakeCanvas(has_set_aspect_ratio=True)
        app = _fake_app(resolution=(1920, 1080), canvas=canvas)
        logger = logging.getLogger("test-update-video")
        update_video(app, logger)
        app._video_receiver.current = (1280, 720)
        update_video(app, logger)
        assert canvas.aspect_calls == [(1920, 1080)]

    def test_a_raising_hint_is_attempted_once_not_every_frame(self) -> None:
        """The latch this replaced tried once. Recording the shape only on
        success would retry a raising GTK call 60 times a second for the rest
        of the session."""

        class _RaisingCanvas:
            def __init__(self) -> None:
                self.calls = 0

            def set_aspect_ratio(self, w: int, h: int) -> None:
                self.calls += 1
                raise RuntimeError("window is being destroyed")

        canvas = _RaisingCanvas()
        app = _fake_app(resolution=(1920, 1080), canvas=canvas)
        logger = logging.getLogger("test-update-video")
        for _ in range(5):
            update_video(app, logger)
        assert canvas.calls == 1

    def test_first_logged_resolution_does_not_gate_later_hints(self) -> None:
        """The one-shot log line and the aspect hint are separate concerns."""
        canvas = _FakeCanvas(has_set_aspect_ratio=True)
        app = _fake_app(resolution=(1280, 720), canvas=canvas, video_logged=True)
        logger = logging.getLogger("test-update-video")
        update_video(app, logger)
        assert canvas.aspect_calls == [(1280, 720)]

    def test_zero_resolution_skips_logging_and_aspect_ratio(self) -> None:
        canvas = _FakeCanvas(has_set_aspect_ratio=True)
        app = _fake_app(resolution=(0, 0), canvas=canvas)
        logger = logging.getLogger("test-update-video")
        update_video(app, logger)
        assert app._video_logged is False
        assert canvas.aspect_calls == []

    def test_canvas_without_aspect_ratio_method_still_logs(self) -> None:
        # Canvas has no `set_aspect_ratio` – the branch falls through but the
        # log still fires and the once-only gate still flips.
        canvas = _FakeCanvas(has_set_aspect_ratio=False)
        app = _fake_app(resolution=(1280, 720), canvas=canvas)
        logger = logging.getLogger("test-update-video")
        update_video(app, logger)
        assert canvas.aspect_calls == []
        assert app._video_logged is True

    def test_resolution_exception_is_caught_and_logged_at_debug(
        self,
        caplog,  # noqa: ANN001
    ) -> None:
        app = _fake_app(raise_on_resolution=True)
        logger = logging.getLogger("test-update-video")
        with caplog.at_level(logging.DEBUG, logger="test-update-video"):
            update_video(app, logger)  # must not raise
        assert any(r.levelname == "DEBUG" and "Video update error" in r.message for r in caplog.records)
