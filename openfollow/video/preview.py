# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Video preview provider for the web UI with JPEG encoding and snapshots."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

PREVIEW_WIDTH = 640  # maximum preview resolution
PREVIEW_HEIGHT = 360
_JPEG_QUALITY = 70  # JPEG encoding quality (0-100)
_MAX_AGE = 5.0  # snapshot staleness threshold

# Stop pulling frames after this long without a request (seconds)
_IDLE_TIMEOUT = 10.0


class PreviewProvider:
    """Thread-safe provider of JPEG snapshots from a GStreamer
    appsink.

    Call :meth:`set_appsink` once the pipeline is built.  A
    background thread pulls samples and stores the latest JPEG
    bytes.  :meth:`get_snapshot` returns the most recent JPEG
    (or *None* if no frame has been captured yet).
    """

    def __init__(self) -> None:
        self._appsink = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()
        self._jpeg_bytes: bytes | None = None
        self._timestamp: float = 0.0
        self._last_requested: float = 0.0
        # Branch valve gating the videoscale/videoconvert/jpegenc.
        # The encoder only runs while open; starts closed so unused preview costs nothing.
        self._valve: Any = None
        self._valve_dropping = True

    def set_appsink(self, appsink: Any) -> None:
        """Set the GStreamer appsink (called by pipeline builder)."""
        self._appsink = appsink

    def set_valve(self, valve: Any) -> None:
        """Set branch valve gating the JPEG encoder; drop frames when idle."""
        with self._lock:
            self._valve = valve
            self._valve_dropping = True

    def _set_valve_drop(self, drop: bool) -> None:
        """Open (``drop=False``) / close (``drop=True``) the encoder valve.

        Idempotent: a no-op when the state is unchanged or no valve is
        wired, so it's cheap to call every loop tick / request. Guarded by
        ``self._lock`` because it runs on both the background ``_run`` loop and
        web request threads (via :meth:`get_snapshot`), which would otherwise
        race on the cached ``_valve_dropping`` flag and leave it out of step
        with the element's actual state.
        """
        with self._lock:
            if self._valve is None or drop == self._valve_dropping:
                return
            try:
                self._valve.set_property("drop", drop)
            except Exception:
                # Element is gone (disposed pipeline). Drop the reference so we
                # degrade to always-on encode instead of re-raising on every
                # tick/request; a branch rebuild re-arms it via set_valve().
                self._valve = None
                return
            self._valve_dropping = drop

    def start(self) -> None:
        """Start the background frame-pulling thread."""
        if self._thread is not None or self._appsink is None:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="PreviewProvider",
        )
        self._thread.start()
        logger.info("Preview provider started")

    def stop(self) -> None:
        """Stop the background thread."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def get_snapshot(self) -> bytes | None:
        """Return the latest JPEG snapshot, or None.

        Also signals the background thread to start pulling
        frames (it idles when no one is requesting snapshots).
        """
        # A request means a consumer is watching – open the encoder valve
        # immediately so the background loop has fresh frames to pull.
        self._set_valve_drop(False)
        with self._lock:
            self._last_requested = time.monotonic()
            if self._jpeg_bytes is None:
                return None
            age = time.monotonic() - self._timestamp
            if age > _MAX_AGE:
                return None
            return self._jpeg_bytes

    def _run(self) -> None:
        """Background loop: pull JPEG samples from appsink."""
        try:
            import gi

            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
        except (ImportError, ValueError):
            logger.warning("GStreamer not available – preview disabled")
            return

        # Pull at ~2 FPS – enough for a web preview
        interval_ns = 500_000_000  # 500ms

        while self._running:
            if self._appsink is None:
                time.sleep(0.5)
                continue

            # Only pull frames when someone is actively
            # requesting snapshots; idle otherwise.
            with self._lock:
                idle_secs = time.monotonic() - self._last_requested
            if idle_secs > _IDLE_TIMEOUT:
                # Nobody watching – close the encoder valve so the branch
                # stops doing scale/convert/jpegenc work and frees the stored JPEG.
                self._set_valve_drop(True)
                if self._jpeg_bytes is not None:
                    with self._lock:
                        self._jpeg_bytes = None
                        self._timestamp = 0.0
                time.sleep(1.0)
                continue

            # Active: make sure the encoder is running before pulling.
            self._set_valve_drop(False)
            sample = self._appsink.try_pull_sample(
                interval_ns,
            )
            if sample is None:
                # On EOS/non-PLAYING, try_pull_sample returns None immediately
                # (not after interval_ns), so hold off to avoid a busy-spin.
                time.sleep(interval_ns / 1_000_000_000)
                continue

            jpeg = self._extract_jpeg(sample, Gst)
            if jpeg is not None:
                with self._lock:
                    self._jpeg_bytes = jpeg
                    self._timestamp = time.monotonic()

    @staticmethod
    def _extract_jpeg(sample: Any, Gst: Any) -> bytes | None:  # noqa: N803
        """Extract raw JPEG bytes from a GStreamer sample."""
        buf = sample.get_buffer()
        success, mapinfo = buf.map(Gst.MapFlags.READ)
        if not success:
            return None
        try:
            data = bytes(mapinfo.data)
        finally:
            buf.unmap(mapinfo)
        return data


# JPEG quality for full-resolution wizard snapshots
_SNAPSHOT_JPEG_QUALITY = 92


class SnapshotProvider:
    """On-demand provider of full-resolution JPEG snapshots.

    Unlike :class:`PreviewProvider`, this does not run a background
    polling loop.  A single frame is pulled from the appsink when
    :meth:`get_snapshot` is called and then cached until the next
    request.
    """

    def __init__(self) -> None:
        self._appsink = None
        # Two locks, deliberately:
        #  * _pull_lock serialises the blocking try_pull_sample so concurrent
        #    HTTP snapshot requests can't race on the single appsink.
        #  * _state_lock is short-lived and guards only the _valve / _jpeg_bytes
        #    references – it is NOT held across the ~500 ms pull, so a pipeline
        #    rebuild's set_valve never blocks on an in-flight encode.
        self._pull_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._jpeg_bytes: bytes | None = None
        # Branch valve gating the full-res videoconvert/jpegenc.
        # Opened only for request duration so encoder is idle between snapshots.
        self._valve: Any = None

    def set_appsink(self, appsink: Any) -> None:
        """Set the GStreamer appsink (called by pipeline builder)."""
        self._appsink = appsink

    def set_valve(self, valve: Any) -> None:
        """Set the branch valve gating the full-res JPEG encoder."""
        with self._state_lock:
            self._valve = valve

    def _clear_valve(self, valve: Any) -> None:
        """Drop a disposed valve reference, but only if it hasn't been replaced
        by a concurrent ``set_valve`` (pipeline rebuild) in the meantime."""
        with self._state_lock:
            if self._valve is valve:
                self._valve = None

    def get_snapshot(self) -> bytes | None:
        """Pull a single full-res JPEG frame (serialised; cached between calls)."""
        if self._appsink is None:
            return None
        try:
            import gi

            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
        except (ImportError, ValueError):
            return None

        # Serialise the pull (one in-flight encode at a time) but do NOT hold
        # _state_lock across it – that's what would otherwise block set_valve.
        with self._pull_lock:
            with self._state_lock:
                valve = self._valve
            opened = False
            if valve is not None:
                try:
                    valve.set_property("drop", False)
                    opened = True
                except Exception:
                    self._clear_valve(valve)
                    valve = None
            try:
                if opened:
                    self._appsink.try_pull_sample(0)  # discard ≤1 stale buffer
                sample = self._appsink.try_pull_sample(500_000_000)
            finally:
                if opened:
                    try:
                        valve.set_property("drop", True)
                    except Exception:
                        self._clear_valve(valve)

            if sample is None:
                with self._state_lock:
                    return self._jpeg_bytes  # return last cached frame

            jpeg = PreviewProvider._extract_jpeg(sample, Gst)
            with self._state_lock:
                if jpeg is not None:
                    self._jpeg_bytes = jpeg
                return jpeg
