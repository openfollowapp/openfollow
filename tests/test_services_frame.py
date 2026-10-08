# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for ``prepare_overlay_state_swap`` and ``update_video`` in
``runtime/services_frame`` – overlay-state pool swap and per-frame video
resolution / aspect-ratio / first-video logging."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

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
# update_video – first-video logging; never the window
# --------------------------------------------------------------------------- #


class _UntouchableCanvas:
    """A canvas ``update_video`` must not reach: any attribute read raises."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"update_video must not reach the window ({name})")


def _fake_app(
    *,
    resolution: tuple[int, int] = (1920, 1080),
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
        _canvas=_UntouchableCanvas(),
        _video_logged=video_logged,
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

    def test_never_touches_the_window_whatever_the_source_does(self, caplog) -> None:  # noqa: ANN001
        """The window's shape is the operator's and the OS's.

        A hard aspect hint froze the macOS app in fullscreen: Quartz refuses
        the constrained resize and GTK waits forever for the configure event.
        The HUD letterboxes itself instead, so no source change may reach the
        canvas, however the shape moves.
        """
        app = _fake_app(resolution=(1920, 1080))
        logger = logging.getLogger("test-update-video")
        with caplog.at_level(logging.INFO, logger="test-update-video"):
            for shape in ((1920, 1080), (0, 0), (1024, 768), (1280, 720)):
                app._video_receiver.current = shape
                update_video(app, logger)
        assert app._video_logged is True
        assert len([r for r in caplog.records if "Native sink" in r.message]) == 1
        assert not hasattr(app, "_video_aspect")

    def test_zero_resolution_skips_logging(self) -> None:
        app = _fake_app(resolution=(0, 0))
        logger = logging.getLogger("test-update-video")
        update_video(app, logger)
        assert app._video_logged is False

    def test_resolution_exception_is_caught_and_logged_at_debug(self, caplog) -> None:  # noqa: ANN001
        app = _fake_app(raise_on_resolution=True)
        logger = logging.getLogger("test-update-video")
        with caplog.at_level(logging.DEBUG, logger="test-update-video"):
            update_video(app, logger)
        assert app._video_logged is False
        assert any("Video update error" in r.message for r in caplog.records)
