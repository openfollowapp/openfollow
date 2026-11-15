# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""The host name this station answers to over mDNS, before ``.local``.

avahi publishes the kernel host name. macOS's Bonjour publishes the
LocalHostName instead, which differs from it whenever DHCP named the Mac, so on
macOS it is read from ``scutil`` once, off the caller's thread and bounded. A
rename in System Settings shows after a restart.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import Any

_SCUTIL = "/usr/sbin/scutil"
_SCUTIL_TIMEOUT_S = 2.0
_LABEL_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


class LocalHostNameReader:
    """macOS's LocalHostName, read once by ``scutil`` on a worker thread.

    The first caller starts the read; until it finishes a caller waits at most
    its own *wait_s*.
    """

    def __init__(
        self,
        *,
        platform: str = sys.platform,
        run: Callable[..., Any] = subprocess.run,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
        timeout_s: float = _SCUTIL_TIMEOUT_S,
    ) -> None:
        self._platform = platform
        self._run = run
        self._thread_factory = thread_factory
        self._timeout_s = timeout_s
        self._lock = threading.Lock()
        self._started = False
        self._done = threading.Event()
        self._name = ""

    def get(self, wait_s: float = 0.0) -> str | None:
        """The LocalHostName, "" when there is none (always off macOS), or None while the read runs."""
        if self._platform != "darwin":
            return ""
        with self._lock:
            start, self._started = not self._started, True
        if start:
            try:
                self._thread_factory(target=self._read, name="mdns-name", daemon=True).start()
            except RuntimeError:
                self._done.set()
        self._done.wait(max(0.0, wait_s))
        return self._name if self._done.is_set() else None

    def _read(self) -> None:
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
            if _LABEL_RE.fullmatch(out):
                self._name = out
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        finally:
            self._done.set()


_reader = LocalHostNameReader()


def set_reader(new: LocalHostNameReader) -> LocalHostNameReader:
    """Swap the process-wide reader; returns the previous one. For tests."""
    global _reader
    previous, _reader = _reader, new
    return previous


def mdns_label(wait_s: float = 0.0) -> str:
    """The name mDNS publishes for this host, or "" while macOS's read runs.

    The kernel host name off macOS, and where Bonjour has no LocalHostName. *wait_s* bounds
    the wait for that read; the frame clock passes none.
    """
    name = _reader.get(wait_s)
    if name is None:
        return ""
    if name:
        return name
    from openfollow.privilege.device_repair import current_hostname

    return current_hostname().strip()
