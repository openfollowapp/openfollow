# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The host name this station answers to over mDNS, before ``.local``.

avahi publishes the kernel host name. macOS's Bonjour publishes the
LocalHostName instead, which differs from it whenever DHCP named the Mac, so on
macOS it is read from ``scutil``: off the caller's thread, bounded, and again
every few seconds so a rename in System Settings shows.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

_SCUTIL = "/usr/sbin/scutil"
_SCUTIL_TIMEOUT_S = 2.0
# How old a read may be before the next caller starts another.
_REFRESH_S = 10.0
_LABEL_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


class LocalHostNameReader:
    """macOS's LocalHostName, read by ``scutil`` on a worker thread.

    Once a name is known no caller waits: a read older than ``refresh_s`` is
    answered from memory while the next one runs. Before the first answer a
    caller waits at most its own *wait_s*. A read that gets no answer keeps the
    last name; only an answer replaces it.
    """

    def __init__(
        self,
        *,
        platform: str = sys.platform,
        run: Callable[..., Any] = subprocess.run,
        clock: Callable[[], float] = time.monotonic,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
        refresh_s: float = _REFRESH_S,
        timeout_s: float = _SCUTIL_TIMEOUT_S,
    ) -> None:
        self._platform = platform
        self._run = run
        self._clock = clock
        self._thread_factory = thread_factory
        self._refresh_s = refresh_s
        self._timeout_s = timeout_s
        # Reentrant: a read that cannot get a thread stores its answer from inside ``get``.
        self._lock = threading.RLock()
        self._name = ""
        self._read_at: float | None = None
        self._reading: threading.Event | None = None

    def get(self, wait_s: float = 0.0) -> str | None:
        """The LocalHostName, "" when there is none (always off macOS), or None while the first read runs."""
        if self._platform != "darwin":
            return ""
        with self._lock:
            read_at = self._read_at
            if self._reading is None and (read_at is None or self._clock() - read_at >= self._refresh_s):
                reading = threading.Event()
                self._reading = reading
                self._start(reading)
            in_flight = self._reading
        if read_at is None and in_flight is not None:
            in_flight.wait(max(0.0, wait_s))
        with self._lock:
            return self._name if self._read_at is not None else None

    def _start(self, reading: threading.Event) -> None:
        try:
            self._thread_factory(target=self._read, args=(reading,), name="mdns-name", daemon=True).start()
        except RuntimeError:
            self._store(None, reading)

    def _read(self, reading: threading.Event) -> None:
        name: str | None = None
        try:
            done = self._run(
                [_SCUTIL, "--get", "LocalHostName"],
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                check=False,
            )
            # A non-zero exit is scutil saying the name is not set.
            out = str(done.stdout or "").strip() if done.returncode == 0 else ""
            name = out if _LABEL_RE.fullmatch(out) else ""
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        finally:
            self._store(name, reading)

    def _store(self, name: str | None, reading: threading.Event) -> None:
        """Finish a read; *name* None (no answer) keeps the last one."""
        with self._lock:
            if name is not None:
                self._name = name
            self._read_at, self._reading = self._clock(), None
        reading.set()


_reader = LocalHostNameReader()


def set_reader(new: LocalHostNameReader) -> LocalHostNameReader:
    """Swap the process-wide reader; returns the previous one. For tests."""
    global _reader
    previous, _reader = _reader, new
    return previous


def mdns_label(wait_s: float = 0.0) -> str:
    """The name mDNS publishes for this host, or "" while macOS's first read runs.

    The kernel host name off macOS, and where Bonjour has no LocalHostName. *wait_s* bounds
    the wait for that first read; the frame clock passes none.
    """
    name = _reader.get(wait_s)
    if name is None:
        return ""
    if name:
        return name
    from openfollow.privilege.device_repair import current_hostname

    return current_hostname().strip()
