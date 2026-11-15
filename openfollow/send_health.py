# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""Whether macOS is refusing a sender's datagrams, judged by the results of its sends.

The network observer watches interfaces, so it cannot see a send the OS refuses
on an interface that is up. macOS does exactly that while OpenFollow's Local
Network permission is off: every LAN send fails with ``EHOSTUNREACH`` while the
address, the route and the socket all look healthy. Each multicast sender
records its sends here, and refusals that keep arriving become one status row.

*A streak is refused once failures keep arriving*: a failure landing
``FAILING_AFTER_S`` after the streak's first. *Recovery needs sends that keep
succeeding*: with Local Network denied, the first send on a fresh socket returns
success and nothing leaves, so a streak ends only on a success at least
``RECOVERY_HOLD_S`` after the first success that followed its last failure.
"""

from __future__ import annotations

import errno
import logging
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from openfollow.net_egress import is_multicast_host

logger = logging.getLogger(__name__)

FAILING_AFTER_S = 1.0
RECOVERY_HOLD_S = 1.0
# The shortest gap between two logged streak starts of one sender.
_LOG_EVERY_S = 60.0

LOCAL_NETWORK_TEXT = "macOS refuses this station's network sends"
# The Operator Screen shows no next step, and its operator is at this station.
LOCAL_NETWORK_HUD_TEXT = "macOS refuses network sends – check Local Network"
LOCAL_NETWORK_ACTION = "Turn on OpenFollow under System Settings → Privacy & Security → Local Network."


def looks_like_local_network(err: int | None, multicast: bool, platform: str = sys.platform) -> bool:
    """Whether a refused send reads as macOS's Local Network permission.

    Only multicast counts: a multicast send leaves through the interface the
    socket names, so ``EHOSTUNREACH`` there is not a routing gap, which a
    unicast send to a host off every subnet would also report.
    """
    return platform == "darwin" and err == errno.EHOSTUNREACH and multicast


@dataclass(frozen=True)
class SendAlert:
    """A status row: what the station observed, and one next step for the web.

    ``hud`` stands in for ``text`` on the Operator Screen, for a row whose next step it must not lose.
    """

    text: str
    action: str = ""
    hud: str = ""

    @property
    def hud_text(self) -> str:
        return self.hud or self.text


LOCAL_NETWORK_ALERT = SendAlert(LOCAL_NETWORK_TEXT, LOCAL_NETWORK_ACTION, LOCAL_NETWORK_HUD_TEXT)


class SendHealth:
    """The outcome of one sender's sends. Thread-safe; every method is cheap."""

    def __init__(
        self,
        label: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        platform: str = sys.platform,
    ) -> None:
        self.label = label
        self._clock = clock
        self._platform = platform
        self._lock = threading.Lock()
        self._streak_start: float | None = None
        self._failures = 0
        self._failing = False
        self._first_ok: float | None = None
        self._local_network = False
        self._quiet_until = 0.0

    @property
    def failed(self) -> int:
        """Failures in the current streak; 0 outside one."""
        with self._lock:
            return self._failures

    @property
    def failing(self) -> bool:
        """Whether the current streak has kept failing for ``FAILING_AFTER_S``."""
        with self._lock:
            return self._failing

    @property
    def local_network(self) -> bool:
        """Whether sends keep failing the way macOS refuses them with Local Network access off."""
        with self._lock:
            return self._failing and self._local_network

    def record_success(self) -> None:
        with self._lock:
            if self._streak_start is None:
                return
            now = self._clock()
            if self._first_ok is None:
                self._first_ok = now
                return
            if now - self._first_ok < RECOVERY_HOLD_S:
                return
            failing, lasted, failed = self._failing, now - self._streak_start, self._failures
            self._clear()
        if failing:
            logger.info("%s: sending again after %.0f s (%d sends failed).", self.label, lasted, failed)

    def record_failure(self, exc: OSError, dest: str) -> None:
        """Count a send to *dest* that raised *exc*. Logs a streak's start (rate-limited) and when it keeps failing."""
        local = looks_like_local_network(exc.errno, is_multicast_host(dest), self._platform)
        message: tuple[int, str] | None = None
        with self._lock:
            now = self._clock()
            self._failures += 1
            self._first_ok = None
            self._local_network = local
            if self._streak_start is None:
                self._streak_start = now
                if now >= self._quiet_until:
                    self._quiet_until = now + _LOG_EVERY_S
                    message = (logging.WARNING, f"{self.label}: send failed: {exc}")
            elif not self._failing and now - self._streak_start >= FAILING_AFTER_S:
                self._failing = True
                text = f"{self.label}: sends keep failing: {exc}"
                if local:
                    text += (
                        " – macOS is refusing them, which is what Local Network access being off for"
                        " OpenFollow looks like (System Settings → Privacy & Security → Local Network)"
                    )
                message = (logging.ERROR, text)
        if message is not None:
            logger.log(*message)

    def reset(self) -> None:
        """Forget the current streak: the sender was stopped, restarted or repointed."""
        with self._lock:
            self._clear()

    def _clear(self) -> None:
        self._streak_start = None
        self._failures = 0
        self._failing = False
        self._first_ok = None
        self._local_network = False


def send_alerts(healths: Iterable[SendHealth]) -> list[SendAlert]:
    """One row when any sender reads as refused by macOS: Local Network access is one switch for the whole app."""
    return [LOCAL_NETWORK_ALERT] if any(health.local_network for health in healths) else []
