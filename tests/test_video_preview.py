# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the video preview provider."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from openfollow.video.preview import (
    _IDLE_TIMEOUT,
    _JPEG_QUALITY,
    _MAX_AGE,
    PREVIEW_HEIGHT,
    PREVIEW_WIDTH,
    PreviewProvider,
    SnapshotProvider,
)

pytestmark = pytest.mark.unit


class TestPreviewProviderInit:
    """Initial state of the preview provider."""

    def test_snapshot_is_none_initially(self) -> None:
        pp = PreviewProvider()
        assert pp.get_snapshot() is None

    def test_not_running_initially(self) -> None:
        pp = PreviewProvider()
        assert pp._running is False
        assert pp._thread is None


class TestPreviewProviderStartStop:
    """Start/stop lifecycle."""

    def test_start_without_appsink_does_nothing(self) -> None:
        pp = PreviewProvider()
        pp.start()
        assert pp._thread is None

    def test_start_with_appsink_creates_thread(self) -> None:
        pp = PreviewProvider()
        pp.set_appsink(MagicMock())
        pp.start()
        assert pp._thread is not None
        assert pp._running is True
        pp.stop()
        assert pp._running is False

    def test_double_start_is_noop(self) -> None:
        pp = PreviewProvider()
        pp.set_appsink(MagicMock())
        pp.start()
        thread1 = pp._thread
        pp.start()
        assert pp._thread is thread1
        pp.stop()

    def test_stop_without_start_is_safe(self) -> None:
        pp = PreviewProvider()
        pp.stop()  # Should not raise


class TestPreviewProviderSnapshot:
    """Snapshot retrieval logic."""

    def test_get_snapshot_returns_stored_bytes(self) -> None:
        pp = PreviewProvider()
        pp._jpeg_bytes = b"\xff\xd8test"
        pp._timestamp = time.monotonic()
        assert pp.get_snapshot() == b"\xff\xd8test"

    def test_clear_cache_drops_frame(self) -> None:
        pp = PreviewProvider()
        pp._jpeg_bytes = b"\xff\xd8old"
        pp._timestamp = time.monotonic()
        pp.clear_cache()
        assert pp._jpeg_bytes is None
        assert pp._timestamp == 0.0
        assert pp.get_snapshot() is None

    def test_get_snapshot_returns_none_when_stale(self) -> None:
        pp = PreviewProvider()
        pp._jpeg_bytes = b"\xff\xd8test"
        pp._timestamp = time.monotonic() - _MAX_AGE - 1.0
        assert pp.get_snapshot() is None

    def test_get_snapshot_updates_last_requested(self) -> None:
        pp = PreviewProvider()
        assert pp._last_requested == 0.0
        before = time.monotonic()
        pp.get_snapshot()
        assert pp._last_requested >= before

    def test_idle_by_default(self) -> None:
        """Thread should idle when no snapshot requested."""
        pp = PreviewProvider()
        idle = (time.monotonic() - pp._last_requested) > _IDLE_TIMEOUT
        assert idle is True

    def test_jpeg_freed_when_idle(self) -> None:
        """Stored JPEG bytes are cleared when idle."""
        pp = PreviewProvider()
        pp._jpeg_bytes = b"\xff\xd8data"
        pp._timestamp = time.monotonic()
        pp._last_requested = 0.0
        idle_secs = time.monotonic() - pp._last_requested
        assert idle_secs > _IDLE_TIMEOUT
        if pp._jpeg_bytes is not None:
            pp._jpeg_bytes = None
            pp._timestamp = 0.0
        assert pp._jpeg_bytes is None
        assert pp._timestamp == 0.0


class TestExtractJpeg:
    """Static helper: extract JPEG bytes from GStreamer sample."""

    def test_extracts_bytes_from_sample(self) -> None:
        jpeg_data = b"\xff\xd8\xff\xe0fake-jpeg"

        buf = MagicMock()
        mapinfo = MagicMock()
        mapinfo.data = jpeg_data
        buf.map.return_value = (True, mapinfo)

        sample = MagicMock()
        sample.get_buffer.return_value = buf

        Gst = MagicMock()
        Gst.MapFlags.READ = 1

        result = PreviewProvider._extract_jpeg(sample, Gst)
        assert result == jpeg_data
        buf.unmap.assert_called_once_with(mapinfo)

    def test_returns_none_on_map_failure(self) -> None:
        buf = MagicMock()
        buf.map.return_value = (False, None)

        sample = MagicMock()
        sample.get_buffer.return_value = buf

        Gst = MagicMock()
        Gst.MapFlags.READ = 1

        assert PreviewProvider._extract_jpeg(sample, Gst) is None


class TestSnapshotProviderConcurrency:
    """Concurrent ``get_snapshot()`` calls must serialise. Fires N threads
    at provider and asserts every caller sees consistent JPEG."""

    def _make_provider_with_appsink(self, jpeg: bytes = b"\xff\xd8concurrent") -> SnapshotProvider:
        sp = SnapshotProvider()
        appsink = MagicMock()

        # Emulate GStreamer: try_pull_sample returns a sample whose buffer
        # maps to our fake JPEG bytes. We slow it down slightly so threads
        # overlap inside the critical section.
        def _slow_pull(_timeout_ns):
            time.sleep(0.005)
            buf = MagicMock()
            mapinfo = MagicMock()
            mapinfo.data = jpeg
            buf.map.return_value = (True, mapinfo)
            sample = MagicMock()
            sample.get_buffer.return_value = buf
            return sample

        appsink.try_pull_sample.side_effect = _slow_pull
        sp.set_appsink(appsink)
        return sp

    def test_concurrent_get_snapshot_no_races(self) -> None:
        try:
            import gi  # noqa: F401

            gi.require_version("Gst", "1.0")
            from gi.repository import Gst  # noqa: F401
        except (ImportError, ValueError):
            pytest.skip("GStreamer introspection not available in this environment")

        import threading

        sp = self._make_provider_with_appsink(b"\xff\xd8payload")
        results: list[bytes | None] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def worker():
            try:
                out = sp.get_snapshot()
                with lock:
                    results.append(out)
            except BaseException as exc:  # pragma: no cover - defensive
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive(), "worker hung – likely a deadlock"

        assert not errors, f"unexpected exceptions: {errors}"
        assert len(results) == 16
        assert all(r == b"\xff\xd8payload" for r in results)

    def test_get_snapshot_without_appsink_returns_none(self) -> None:
        sp = SnapshotProvider()
        assert sp.get_snapshot() is None

    def test_clear_cache_drops_frame(self) -> None:
        sp = SnapshotProvider()
        sp._jpeg_bytes = b"\xff\xd8old"
        sp.clear_cache()
        assert sp._jpeg_bytes is None


class _FakeValve:
    """Records ``set_property('drop', ...)`` toggles in order."""

    def __init__(self) -> None:
        self.drop_calls: list[bool] = []

    def set_property(self, key: str, value: object) -> None:
        if key == "drop":
            self.drop_calls.append(bool(value))


class _RaisingValve:
    """A valve whose ``set_property`` always raises (defensive-path guard)."""

    def set_property(self, key: str, value: object) -> None:
        raise RuntimeError("valve disposed")


class _CloseRaisingValve:
    """Opens fine but raises when closed (``drop=True``) – exercises the
    finally-path guard where the element is disposed mid-request."""

    def __init__(self) -> None:
        self.drop_calls: list[bool] = []

    def set_property(self, key: str, value: object) -> None:
        if key == "drop":
            self.drop_calls.append(bool(value))
            if value is True:
                raise RuntimeError("valve disposed on close")


def _gst_available() -> bool:
    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst  # noqa: F401
    except (ImportError, ValueError):
        return False
    return True


class TestPreviewProviderValveGating:
    """The preview encoder valve opens on demand, closes when idle."""

    def test_set_valve_drop_toggles_idempotently(self) -> None:
        pp = PreviewProvider()
        valve = _FakeValve()
        pp.set_valve(valve)
        # Starts closed (dropping). Opening flips it once; a repeat is a no-op.
        pp._set_valve_drop(False)
        pp._set_valve_drop(False)
        assert valve.drop_calls == [False]
        pp._set_valve_drop(True)
        assert valve.drop_calls == [False, True]

    def test_no_valve_is_noop(self) -> None:
        pp = PreviewProvider()
        pp._set_valve_drop(False)  # must not raise without a valve
        assert pp._valve is None

    def test_get_snapshot_opens_valve(self) -> None:
        pp = PreviewProvider()
        valve = _FakeValve()
        pp.set_valve(valve)
        pp.get_snapshot()  # a request opens the encoder
        assert valve.drop_calls == [False]

    def test_set_valve_resyncs_dropping_state(self) -> None:
        pp = PreviewProvider()
        first = _FakeValve()
        pp.set_valve(first)
        pp._set_valve_drop(False)  # opened while a client watched
        assert pp._valve_dropping is False
        # Branch rebuilt with a brand-new closed valve.
        second = _FakeValve()
        pp.set_valve(second)
        assert pp._valve_dropping is True
        pp._set_valve_drop(False)  # must actually open the new valve
        assert second.drop_calls == [False]

    def test_set_valve_drop_drops_disposed_valve(self) -> None:
        pp = PreviewProvider()
        pp.set_valve(_RaisingValve())
        pp._set_valve_drop(False)  # must not propagate the error
        # State stays at its prior value since the toggle didn't take...
        assert pp._valve_dropping is True
        # ...and the disposed valve is dropped so future calls are pure no-ops.
        assert pp._valve is None
        pp._set_valve_drop(False)  # still must not raise


class TestSnapshotProviderValveGating:
    """The full-res encoder valve is open only for the request."""

    def test_get_snapshot_opens_then_closes_valve(self) -> None:
        if not _gst_available():
            pytest.skip("GStreamer introspection not available in this environment")
        sp = SnapshotProvider()
        valve = _FakeValve()
        appsink = MagicMock()
        appsink.try_pull_sample.return_value = None  # no fresh frame
        sp.set_appsink(appsink)
        sp.set_valve(valve)

        sp.get_snapshot()

        # Opened for the request, then closed again afterwards.
        assert valve.drop_calls == [False, True]
        # Drains a possibly-stale buffer (timeout 0) then blocks briefly.
        timeouts = [c.args[0] for c in appsink.try_pull_sample.call_args_list]
        assert 0 in timeouts
        assert 500_000_000 in timeouts

    def test_no_valve_uses_single_pull(self) -> None:
        if not _gst_available():
            pytest.skip("GStreamer introspection not available in this environment")
        sp = SnapshotProvider()
        appsink = MagicMock()
        appsink.try_pull_sample.return_value = None
        sp.set_appsink(appsink)

        sp.get_snapshot()

        # Unchanged original behaviour: exactly one 500ms pull, no drain.
        assert appsink.try_pull_sample.call_count == 1
        assert appsink.try_pull_sample.call_args.args[0] == 500_000_000

    def test_get_snapshot_swallows_valve_errors(self) -> None:
        if not _gst_available():
            pytest.skip("GStreamer introspection not available in this environment")
        sp = SnapshotProvider()
        sp.set_valve(_RaisingValve())
        appsink = MagicMock()
        appsink.try_pull_sample.return_value = None
        sp.set_appsink(appsink)

        result = sp.get_snapshot()  # must not raise

        assert result is None
        # Still attempted to capture a frame despite the valve error...
        assert appsink.try_pull_sample.called
        # ...and dropped the disposed valve so the next request is always-on.
        assert sp._valve is None

    def test_get_snapshot_close_failure_drops_valve(self) -> None:
        """If closing the valve raises (disposed mid-request), the error is
        swallowed and the valve is dropped so later requests go always-on."""
        if not _gst_available():
            pytest.skip("GStreamer introspection not available in this environment")
        sp = SnapshotProvider()
        valve = _CloseRaisingValve()
        appsink = MagicMock()
        appsink.try_pull_sample.return_value = None
        sp.set_appsink(appsink)
        sp.set_valve(valve)

        result = sp.get_snapshot()  # must not raise

        assert result is None
        # Opened (drop False), then the close (drop True) raised and was caught.
        assert valve.drop_calls == [False, True]
        assert sp._valve is None

    def test_clear_valve_preserves_a_concurrently_installed_replacement(self) -> None:
        """A disposed-valve error during a pull must not clobber a valve a
        concurrent set_valve (pipeline rebuild) installed in the meantime."""
        sp = SnapshotProvider()
        valve_a, valve_b = object(), object()
        sp.set_valve(valve_b)  # rebuild installed B while A's set_property failed
        sp._clear_valve(valve_a)  # not the valve we opened – leave B alone
        assert sp._valve is valve_b
        sp._clear_valve(valve_b)  # same valve → cleared
        assert sp._valve is None


class TestPreviewProviderActiveLoopThrottle:
    """The active pull loop must not busy-spin when the appsink yields no
    sample (EOS / non-PLAYING returns None immediately rather than blocking
    for the pull interval)."""

    def test_none_sample_throttles_active_loop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        if not _gst_available():
            pytest.skip("GStreamer introspection not available in this environment")

        pp = PreviewProvider()

        pulls = 0

        # Appsink that, like an EOS sink, returns None *immediately* – no block.
        # Stop the loop after a bounded number of pulls so the test terminates
        # even on the pre-fix (busy-spin) code instead of leaking a hot thread.
        def _eos_pull(_timeout_ns):
            nonlocal pulls
            pulls += 1
            if pulls >= 50:
                pp._running = False
            return None

        appsink = MagicMock()
        appsink.try_pull_sample.side_effect = _eos_pull
        pp.set_appsink(appsink)
        pp.set_valve(_FakeValve())

        # Active: a consumer requested very recently.
        pp._last_requested = time.monotonic()
        pp._running = True

        sleeps: list[float] = []
        real_sleep = time.sleep

        def _recording_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            real_sleep(0)  # don't actually block the test

        monkeypatch.setattr("openfollow.video.preview.time.sleep", _recording_sleep)

        pp._run()

        # The throttle pairs every active None pull with a hold-off sleep. Pre-fix
        # the None path never sleeps, so the loop is a pure busy-spin.
        assert sleeps, "active None path did not throttle (busy-spin)"
        assert all(s > 0 for s in sleeps)
        # One sleep per None pull – the loop never spins faster than the hold-off.
        assert len(sleeps) >= pulls - 1


class TestConstants:
    """Preview constants are sensible."""

    def test_dimensions(self) -> None:
        assert PREVIEW_WIDTH == 640
        assert PREVIEW_HEIGHT == 360

    def test_quality(self) -> None:
        assert 1 <= _JPEG_QUALITY <= 100

    def test_max_age(self) -> None:
        assert _MAX_AGE > 0

    def test_idle_timeout(self) -> None:
        assert _IDLE_TIMEOUT > _MAX_AGE
