# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Concurrency + lifecycle coverage for :mod:`openfollow.video.preview`.

Complements :mod:`tests.test_video_preview` (init state, start/stop
lifecycle, ``_extract_jpeg`` happy path, one concurrent-pull
invariant).  This file drives the remaining edge paths:

* ``PreviewProvider._run`` background loop:
  - GStreamer ``gi`` / ``gi.repository.Gst`` import failure → early
    return + WARNING log.
  - ``appsink is None`` on loop entry → ``time.sleep(0.5); continue``.
  - Idle timeout: ``_jpeg_bytes`` dropped + timestamp zeroed so a
    stale buffer doesn't sit in memory while nobody is viewing.
  - Active path: ``try_pull_sample`` → ``_extract_jpeg`` → store;
    with both ``sample is None`` and ``jpeg is None`` skip branches.

* ``SnapshotProvider.get_snapshot``:
  - ``gi`` / ``gi.repository.Gst`` import failure → return ``None``.
  - ``try_pull_sample`` returning ``None`` → cached JPEG returned
    (cache-hit on transient appsink stalls).
  - ``_extract_jpeg`` returning ``None`` → cache untouched, ``None``
    returned for this call (failed map, not a cache invalidation).
  - ``self._lock`` held across the entire
    ``try_pull_sample → extract → cache`` sequence – regression
    guard for the serialised snapshot pull.
"""

from __future__ import annotations

import sys
import threading
import time
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

from openfollow.video import preview as preview_module
from openfollow.video.preview import PreviewProvider, SnapshotProvider

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _install_stub_gst(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    """Swap ``sys.modules['gi']`` + ``sys.modules['gi.repository']`` for
    fakes so ``from gi.repository import Gst`` inside ``_run`` /
    ``get_snapshot`` resolves to a deterministic stub.

    Avoids touching the *real* ``gi`` module – patching attributes on
    the live module leaks partial-import state into ``gi.importer``'s
    caches that survives monkeypatch teardown and poisons subsequent
    tests that try to do ``from gi.repository import Gst`` for real.
    ``sys.modules`` substitution restores cleanly.
    """
    gst_stub = types.SimpleNamespace(
        MapFlags=types.SimpleNamespace(READ=1),
    )
    fake_gi = types.ModuleType("gi")
    fake_gi.require_version = lambda *_a, **_kw: None  # type: ignore[attr-defined]
    fake_repo = types.ModuleType("gi.repository")
    fake_repo.Gst = gst_stub  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gi", fake_gi)
    monkeypatch.setitem(sys.modules, "gi.repository", fake_repo)
    return gst_stub


def _install_blocked_gst(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``gi.require_version("Gst", "1.0")`` to raise ``ValueError``
    so the ``except (ImportError, ValueError)`` guard in ``_run`` /
    ``get_snapshot`` takes the fallback branch.

    Same isolation rule as ``_install_stub_gst``: we swap the module
    objects in ``sys.modules`` rather than mutate the real ``gi``.
    """
    fake_gi = types.ModuleType("gi")

    def _fail(ns: str, _ver: str) -> None:
        raise ValueError(f"{ns} typelib blocked for test")

    fake_gi.require_version = _fail  # type: ignore[attr-defined]
    fake_repo = types.ModuleType("gi.repository")
    monkeypatch.setitem(sys.modules, "gi", fake_gi)
    monkeypatch.setitem(sys.modules, "gi.repository", fake_repo)


def _make_sample(jpeg: bytes, *, map_success: bool = True) -> MagicMock:
    """Build a Gst sample stand-in whose buffer maps to ``jpeg``."""
    mapinfo = MagicMock()
    mapinfo.data = jpeg
    buf = MagicMock()
    buf.map.return_value = (map_success, mapinfo if map_success else None)
    sample = MagicMock()
    sample.get_buffer.return_value = buf
    return sample


# --------------------------------------------------------------------------- #
# PreviewProvider._run – gi / Gst import failure
# --------------------------------------------------------------------------- #


class TestPreviewRunGstUnavailable:
    def test_gi_require_value_error_exits_run_with_warning(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``gi.require_version("Gst", "1.0")`` raising ``ValueError``
        (Gst typelib not installed) must stop ``_run`` cleanly – no
        uncaught exception escapes the thread and the operator sees a
        WARNING about the disabled preview.
        """
        _install_blocked_gst(monkeypatch)

        pp = PreviewProvider()
        pp._appsink = MagicMock()  # not None, otherwise _run short-circuits
        pp._running = True
        with caplog.at_level("WARNING", logger="openfollow.video.preview"):
            pp._run()  # must return within milliseconds, not raise
        assert any("preview disabled" in r.message for r in caplog.records if r.levelname == "WARNING")

    def test_gi_import_error_takes_same_fallback_branch(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A stub ``gi`` module whose ``require_version("Gst", "1.0")``
        raises ``ImportError`` hits the same ``except`` as the
        ``ValueError`` path.  Covers the failure mode where ``gi`` is
        importable but the Gst typelib registration itself raises
        ``ImportError`` (distinct from the ``ValueError`` case where
        only the typelib lookup failed).
        """
        # Install a stub ``gi`` module so ``import gi`` succeeds, then
        # make ``gi.require_version(...)`` raise ``ImportError``.
        blocker = types.ModuleType("gi")

        def _always_raise(*_a: Any, **_kw: Any) -> None:
            raise ImportError("gi not available")

        blocker.require_version = _always_raise  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "gi", blocker)

        pp = PreviewProvider()
        pp._appsink = MagicMock()
        pp._running = True
        with caplog.at_level("WARNING", logger="openfollow.video.preview"):
            pp._run()
        assert any("preview disabled" in r.message for r in caplog.records if r.levelname == "WARNING")


# --------------------------------------------------------------------------- #
# PreviewProvider._run – background loop
# --------------------------------------------------------------------------- #


class TestPreviewRunLoop:
    """Drive ``_run`` under a fake appsink.

    Each test controls the loop with the provider's ``_running``
    boolean flag: seed it to ``True`` before entering ``_run()``, then
    flip it to ``False`` from inside the fake appsink / monkeypatched
    ``time.monotonic`` once the assertion has exercised the intended
    iteration path.
    """

    def test_appsink_none_sleeps_and_continues(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Covers the ``if self._appsink is None: time.sleep(...); continue`` arm.

        The loop is supposed to idle gracefully when the pipeline
        builder hasn't wired the appsink yet (common during startup
        while GStreamer state transitions are in flight).
        """
        _install_stub_gst(monkeypatch)

        pp = PreviewProvider()
        pp._appsink = None
        pp._running = True

        sleep_calls: list[float] = []
        iterations = {"n": 0}

        def _tracking_sleep(dt: float) -> None:
            sleep_calls.append(dt)
            iterations["n"] += 1
            # Stop after a couple of sleeps so the loop terminates.
            if iterations["n"] >= 2:
                pp._running = False

        monkeypatch.setattr(preview_module.time, "sleep", _tracking_sleep)
        pp._run()

        # At least one 0.5s nap from the ``appsink is None`` branch.
        assert 0.5 in sleep_calls

    def test_idle_timeout_frees_stored_jpeg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``_jpeg_bytes`` cleared when no caller has requested a snapshot
        for longer than ``_IDLE_TIMEOUT``.

        This is the memory-hygiene branch – without it, the last-seen
        JPEG stays in RAM forever even after the web UI is closed.
        """
        _install_stub_gst(monkeypatch)

        pp = PreviewProvider()
        pp._appsink = MagicMock()
        pp._jpeg_bytes = b"\xff\xd8stale"
        pp._timestamp = time.monotonic()
        # Last request long enough ago that ``idle_secs > _IDLE_TIMEOUT``.
        pp._last_requested = time.monotonic() - preview_module._IDLE_TIMEOUT - 5.0
        pp._running = True

        def _stop_after_one_iter(dt: float) -> None:
            pp._running = False  # break out on the first 1.0s idle sleep

        monkeypatch.setattr(preview_module.time, "sleep", _stop_after_one_iter)
        pp._run()

        assert pp._jpeg_bytes is None
        assert pp._timestamp == 0.0
        # ``try_pull_sample`` must NOT have been called on the idle path.
        pp._appsink.try_pull_sample.assert_not_called()

    def test_idle_timeout_with_no_stored_jpeg_skips_clear(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Idle, but ``_jpeg_bytes`` is already ``None`` so there's nothing
        to free – the loop skips the clear and falls straight to the idle
        sleep.

        Deterministic counterpart to ``test_idle_timeout_frees_stored_jpeg``.
        Driving ``_run`` synchronously pins down the coverage gate.
        """
        _install_stub_gst(monkeypatch)

        pp = PreviewProvider()
        pp._appsink = MagicMock()
        pp._jpeg_bytes = None
        pp._timestamp = 0.0
        pp._last_requested = time.monotonic() - preview_module._IDLE_TIMEOUT - 5.0
        pp._running = True

        def _stop_after_one_iter(dt: float) -> None:
            pp._running = False  # break out on the first 1.0s idle sleep

        monkeypatch.setattr(preview_module.time, "sleep", _stop_after_one_iter)
        pp._run()

        assert pp._jpeg_bytes is None
        assert pp._timestamp == 0.0
        # Idle path must not pull samples.
        pp._appsink.try_pull_sample.assert_not_called()

    def test_active_path_stores_pulled_jpeg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Active-caller path pulls a sample, extracts JPEG bytes, stores
        them under the lock, stamps the timestamp.  Two iterations – first
        succeeds, second raises ``_running = False`` to exit.
        """
        _install_stub_gst(monkeypatch)

        pp = PreviewProvider()
        appsink = MagicMock()
        jpeg_payload = b"\xff\xd8active-path"
        appsink.try_pull_sample.return_value = _make_sample(jpeg_payload)
        pp._appsink = appsink
        pp._last_requested = time.monotonic()  # within window → active
        pp._running = True

        iterations = {"n": 0}
        real_monotonic = preview_module.time.monotonic

        def _bounded_monotonic() -> float:
            iterations["n"] += 1
            if iterations["n"] > 10:
                pp._running = False
            return real_monotonic()

        monkeypatch.setattr(preview_module.time, "monotonic", _bounded_monotonic)
        pp._run()

        assert pp._jpeg_bytes == jpeg_payload
        assert pp._timestamp > 0.0

    def test_extract_jpeg_none_preserves_cache_in_active_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_stub_gst(monkeypatch)

        pp = PreviewProvider()
        appsink = MagicMock()
        # Sample present but its buffer.map fails → _extract_jpeg → None.
        appsink.try_pull_sample.return_value = _make_sample(b"\xff\xd8ignored", map_success=False)
        pp._appsink = appsink
        pp._jpeg_bytes = b"\xff\xd8cached"
        pp._timestamp = time.monotonic()
        pp._last_requested = time.monotonic()
        pp._running = True

        iterations = {"n": 0}
        real_monotonic = preview_module.time.monotonic

        def _bounded_monotonic() -> float:
            iterations["n"] += 1
            if iterations["n"] > 6:
                pp._running = False
            return real_monotonic()

        monkeypatch.setattr(preview_module.time, "monotonic", _bounded_monotonic)
        pp._run()

        assert pp._jpeg_bytes == b"\xff\xd8cached"  # preserved across failed extract

    def test_sample_none_skips_store(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_stub_gst(monkeypatch)

        pp = PreviewProvider()
        appsink = MagicMock()
        appsink.try_pull_sample.return_value = None
        pp._appsink = appsink
        pp._jpeg_bytes = b"\xff\xd8previous"
        pp._timestamp = time.monotonic()
        pp._last_requested = time.monotonic()
        pp._running = True

        iterations = {"n": 0}
        real_monotonic = preview_module.time.monotonic

        def _bounded_monotonic() -> float:
            iterations["n"] += 1
            if iterations["n"] > 6:
                pp._running = False
            return real_monotonic()

        monkeypatch.setattr(preview_module.time, "monotonic", _bounded_monotonic)
        pp._run()

        assert pp._jpeg_bytes == b"\xff\xd8previous"  # preserved


# --------------------------------------------------------------------------- #
# SnapshotProvider.get_snapshot edge paths
# --------------------------------------------------------------------------- #


class TestSnapshotProviderEdgePaths:
    def test_gi_import_error_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_blocked_gst(monkeypatch)

        sp = SnapshotProvider()
        sp.set_appsink(MagicMock())
        assert sp.get_snapshot() is None

    def test_try_pull_sample_none_returns_cached_jpeg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Transient appsink stalls return the last cached frame.

        Transient appsink stalls (empty-queue, warm-up) return ``None``
        from ``try_pull_sample``.  The wizard snapshot endpoint should
        still respond with the last known good frame so the UI doesn't
        flicker to a "no signal" placeholder on every glitch.
        """
        _install_stub_gst(monkeypatch)

        sp = SnapshotProvider()
        appsink = MagicMock()
        appsink.try_pull_sample.return_value = None
        sp.set_appsink(appsink)
        cached = b"\xff\xd8cached-frame"
        sp._jpeg_bytes = cached

        assert sp.get_snapshot() == cached

    def test_extract_jpeg_none_returns_none_preserves_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_stub_gst(monkeypatch)

        sp = SnapshotProvider()
        appsink = MagicMock()
        # Sample present but its buffer.map fails.
        appsink.try_pull_sample.return_value = _make_sample(b"ignored", map_success=False)
        sp.set_appsink(appsink)
        sp._jpeg_bytes = b"\xff\xd8cached"  # prior successful pull

        result = sp.get_snapshot()
        assert result is None
        # Cache survived the failed map.
        assert sp._jpeg_bytes == b"\xff\xd8cached"

    def test_appsink_none_returns_none_without_gst_import(self) -> None:
        sp = SnapshotProvider()
        assert sp._appsink is None
        assert sp.get_snapshot() is None


# --------------------------------------------------------------------------- #
# Lock-invariant regression guard for the serialised snapshot pull
# --------------------------------------------------------------------------- #


class TestSnapshotProviderLockInvariant:
    """The blocking ``try_pull_sample`` stays serialised under ``_pull_lock``
    so concurrent HTTP requests can't race the single appsink – while
    ``set_valve`` uses a separate short ``_state_lock`` and never blocks on
    an in-flight pull.

    ``tests.test_video_preview.TestSnapshotProviderConcurrency`` fires
    N threads at the same provider and asserts every caller sees a
    consistent JPEG.  This test instruments the lock directly so the
    invariant fails loudly on a future refactor that moves
    ``try_pull_sample`` outside the serialisation lock – even if the
    concurrent test happens to pass by luck.
    """

    def test_appsink_is_only_called_while_lock_is_held(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_stub_gst(monkeypatch)

        sp = SnapshotProvider()

        # Instrument the pull-serialisation lock to flip a bool while held.
        real_lock = sp._pull_lock
        held = {"yes": False}

        class _TrackingLock:
            def __enter__(self) -> Any:
                real_lock.acquire()
                held["yes"] = True
                return self

            def __exit__(self, *exc: object) -> None:
                held["yes"] = False
                real_lock.release()

            # threading.Lock fallbacks some callers might reach for.
            def acquire(self, *a: Any, **kw: Any) -> bool:
                result = real_lock.acquire(*a, **kw)
                if result:
                    held["yes"] = True
                return result

            def release(self) -> None:
                held["yes"] = False
                real_lock.release()

        sp._pull_lock = _TrackingLock()  # type: ignore[assignment]

        appsink = MagicMock()
        pull_observations: list[bool] = []

        def _check_lock_during_pull(_timeout: int) -> MagicMock:
            pull_observations.append(held["yes"])
            return _make_sample(b"\xff\xd8locked")

        appsink.try_pull_sample.side_effect = _check_lock_during_pull
        sp.set_appsink(appsink)

        sp.get_snapshot()

        assert pull_observations == [True], "try_pull_sample was called outside the lock – race hazard"

    def test_set_valve_does_not_block_on_in_flight_pull(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The HIGH-finding regression: a pipeline rebuild's ``set_valve`` must
        not stall on the ~500 ms snapshot pull (the old single-lock design
        blocked it)."""
        _install_stub_gst(monkeypatch)

        sp = SnapshotProvider()
        in_pull = threading.Event()
        release = threading.Event()

        def _gated_pull(_timeout: int) -> MagicMock:
            in_pull.set()
            # Backstop far beyond the 2 s assertion window below so the pull
            # cannot finish (and free _pull_lock) before set_valve is observed –
            # otherwise the contention this guards against wouldn't be exercised.
            # The finally-block release.set() ends it well before this fires.
            release.wait(timeout=30.0)
            return _make_sample(b"\xff\xd8x")

        appsink = MagicMock()
        appsink.try_pull_sample.side_effect = _gated_pull
        sp.set_appsink(appsink)

        puller = threading.Thread(target=sp.get_snapshot, daemon=True)
        puller.start()
        assert in_pull.wait(timeout=2.0)  # pull is in flight, _pull_lock held

        done = threading.Event()

        def _swap() -> None:
            sp.set_valve(MagicMock())
            done.set()

        swapper = threading.Thread(target=_swap, daemon=True)
        swapper.start()
        try:
            assert done.wait(timeout=2.0), "set_valve blocked on an in-flight snapshot pull"
        finally:
            release.set()
        puller.join(timeout=2.0)

    def test_concurrent_callers_serialise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Supplements ``test_concurrent_get_snapshot_no_races`` with an
        explicit counter: if ``try_pull_sample`` ever observes a second
        caller already inside the critical section, the invariant is
        broken.
        """
        _install_stub_gst(monkeypatch)

        sp = SnapshotProvider()
        in_flight = {"n": 0, "max": 0}
        lock_for_counter = threading.Lock()
        # Block the first caller inside the critical section until every
        # worker thread has had a chance to queue on the provider lock.
        # ``release_gate`` is set by the test driver once all 6 threads
        # have started and (per SnapshotProvider's serialisation) are
        # stacked up on ``self._lock`` – strictly event-driven, no
        # wall-clock ``time.sleep``.
        release_gate = threading.Event()
        pulls_started = threading.Semaphore(0)

        def _gated_pull(_timeout: int) -> MagicMock:
            with lock_for_counter:
                in_flight["n"] += 1
                in_flight["max"] = max(in_flight["max"], in_flight["n"])
            pulls_started.release()  # signal this caller has entered the cs
            release_gate.wait(timeout=2.0)
            with lock_for_counter:
                in_flight["n"] -= 1
            return _make_sample(b"\xff\xd8mp")

        appsink = MagicMock()
        appsink.try_pull_sample.side_effect = _gated_pull
        sp.set_appsink(appsink)

        # Daemon threads so a future regression that actually deadlocks
        # ``get_snapshot()`` can't keep the pytest process alive past
        # the assertion.  Without ``daemon=True``, the interpreter waits
        # for every non-daemon thread to exit before shutting down – a
        # failed test would turn into a CI-timeout hang instead of a
        # clean failure.
        threads = [threading.Thread(target=sp.get_snapshot, daemon=True) for _ in range(6)]
        try:
            for t in threads:
                t.start()

            # Wait for the first caller to enter the critical section.
            # Any future caller that squeezed past the lock would
            # already have bumped ``in_flight["max"]`` above 1 before
            # blocking on the gate – which is exactly what we assert
            # against.
            pulls_started.acquire(timeout=2.0)
        finally:
            # Always release the gate so ``_gated_pull`` can return
            # even if we raised above – prevents a suite-wide hang on
            # setup failures.
            release_gate.set()

        for t in threads:
            t.join(timeout=2.0)

        # If ``get_snapshot()`` ever deadlocks the timeout-bounded join
        # would pass silently and leak threads – assert termination
        # directly so a regression fails loudly instead of flaking.
        alive_threads = [t for t in threads if t.is_alive()]
        assert not alive_threads, (
            f"{len(alive_threads)} worker thread(s) did not terminate after "
            "join(timeout=2.0); get_snapshot() may be deadlocked"
        )

        assert in_flight["max"] == 1, (
            f"Observed {in_flight['max']} concurrent try_pull_sample calls – "
            "get_snapshot() must serialise the critical section"
        )
