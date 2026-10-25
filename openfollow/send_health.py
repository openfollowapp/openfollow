# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Whether a sender's datagrams are leaving, judged by the results of its sends.

The network observer watches interfaces, so it cannot see a send the OS refuses
on an interface that is up. macOS does exactly that while OpenFollow's Local
Network permission is off: every LAN send fails with ``EHOSTUNREACH`` while the
address, the route and the socket all look healthy. Each sender records its
sends here, and failures that keep arriving become a status row.

Two rules carry the weight:

*A streak is failing once failures keep arriving*: a failure landing
``FAILING_AFTER_S`` after the streak's first. A burst at one instant never
becomes a row by itself. A send skipped for want of a socket is a failure too,
named by the error that kept the socket from opening when no streak is open, so
a socket rebuild that takes that long, or a socket that never opens, does.

*Recovery needs sends that keep succeeding.* With Local Network denied, the
first send on a fresh socket returns success and nothing leaves, so every
rebuild looks like a recovery. A streak ends only on a success at least
``RECOVERY_HOLD_S`` after the first success that followed its last failure.

A streak's first failure is logged at most once a minute per sender, and the
next such line counts the brief streaks held back. A streak a skipped send
starts is logged as the socket not being open, outside that limit. A streak
that reaches failing is always logged when it does and when it ends, or is reset.
"""

from __future__ import annotations

import errno
import logging
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

from openfollow.net_egress import is_multicast_host

logger = logging.getLogger(__name__)

FAILING_AFTER_S = 1.0
RECOVERY_HOLD_S = 1.0
# How often a failing streak repeats itself in the log, and the shortest gap
# between two logged streak starts.
_REMIND_EVERY_S = 60.0

LOCAL_NETWORK_TEXT = "macOS refuses this station's network sends"
# The Operator Screen shows no next step, and its operator is at this station.
LOCAL_NETWORK_HUD_TEXT = "macOS refuses network sends – check Local Network"
LOCAL_NETWORK_ACTION = "Turn on OpenFollow under System Settings → Privacy & Security → Local Network."
SEND_FAILING_ACTION = "Check this station's network settings and firewall."
OSC_SEND_FAILING_ACTION = "Check the destination's address under OSC Destinations, and this station's firewall."


def looks_like_local_network(err: int | None, multicast: bool, platform: str = sys.platform) -> bool:
    """Whether a refused send reads as macOS's Local Network permission.

    Only multicast counts: a multicast send leaves through the interface the
    socket names, so ``EHOSTUNREACH`` there is not a routing gap, which a
    unicast send to a host off every subnet would also report.
    """
    return platform == "darwin" and err == errno.EHOSTUNREACH and multicast


@dataclass(frozen=True)
class SendHealthStatus:
    """One sender's record as a reader sees it. ``state`` is ``ok``, ``retrying`` or ``failing``."""

    label: str
    state: str
    # Seconds since the streak's first failure; None outside a streak.
    failing_for_s: float | None
    # Failures in the current streak.
    failed: int
    # The last failure's OS wording ("No route to host"), "" outside a streak.
    error: str
    errno: int | None
    local_network: bool
    sent_total: int
    failed_total: int

    @property
    def failing(self) -> bool:
        return self.state == "failing"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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


@dataclass(frozen=True)
class FailureCause:
    """What a failed call's exception says, kept without the exception and the frames its traceback holds."""

    # The full text the log quotes ("[Errno 65] No route to host").
    reason: str
    # The OS wording a row shows ("No route to host"), else the full text.
    error: str
    errno: int | None

    @classmethod
    def from_exception(cls, exc: BaseException) -> FailureCause:
        reason = str(exc) or exc.__class__.__name__
        if isinstance(exc, OSError):
            return cls(reason, exc.strerror or reason, exc.errno)
        return cls(reason, reason, None)


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
        self._sent_total = 0
        self._failed_total = 0
        self._streak_start: float | None = None
        self._streak_failures = 0
        self._failing = False
        self._first_ok: float | None = None
        self._last_log = 0.0
        # Streak starts are logged no earlier than this; the ones held back are counted.
        self._quiet_until = 0.0
        self._quiet_starts = 0
        self._start_logged = False
        self._error = ""
        # The last failure's full text, which the log quotes.
        self._reason = ""
        self._errno: int | None = None
        self._multicast = False

    def record_success(self) -> None:
        with self._lock:
            self._sent_total += 1
            if self._streak_start is None:
                return
            now = self._clock()
            if self._first_ok is None:
                self._first_ok = now
                return
            if now - self._first_ok < RECOVERY_HOLD_S:
                return
            lasted = now - self._streak_start
            failed = self._streak_failures
            # A streak that never failed put nothing on screen.
            level = logging.INFO if self._failing else logging.DEBUG
            self._clear_streak()
        logger.log(level, "%s: sending again after %.0f s (%d sends failed).", self.label, lasted, failed)

    def record_failure(self, exc: OSError, dest: str) -> None:
        """Count a send to *dest* that raised *exc*. Logs once per streak, then as a reminder."""
        cause = FailureCause.from_exception(exc)
        with self._lock:
            self._note_error(cause, dest)
            entry = self._count_failure()
        if entry is not None:
            logger.log(entry[0], "%s", entry[1])

    def record_skipped(self, cause: FailureCause | None, dest: str) -> None:
        """Count a send to *dest* skipped for want of a socket; *cause* kept the socket from opening.

        Inside a streak it is one more failure of that streak's error, so a
        rebuild that keeps failing still becomes a row. Outside one *cause*
        starts a streak, so a socket that never opened, or stayed down past a
        reset, becomes a row too. With no *cause* it records nothing.
        """
        entry: tuple[int, str] | None
        with self._lock:
            if self._streak_start is None:
                if cause is None:
                    return
                self._note_error(cause, dest)
                entry = self._start_skipped_streak()
            else:
                entry = self._count_failure()
        if entry is not None:
            logger.log(entry[0], "%s", entry[1])

    def _note_error(self, cause: FailureCause, dest: str) -> None:
        """Make *cause*, met sending to *dest*, the error the next failure counts. Caller holds the lock."""
        self._error = cause.error
        self._errno = cause.errno
        self._multicast = is_multicast_host(dest)
        self._reason = cause.reason

    def _start_skipped_streak(self) -> tuple[int, str]:
        """Open a streak on a skipped send. Caller holds the lock.

        Always logged, and outside the once-a-minute slot: no send failed, so it
        is no brief send failure to hold back, and it must not hold one back.
        """
        self._failed_total += 1
        self._open_streak(self._clock())
        self._start_logged = True
        return logging.WARNING, f"{self.label}: socket not open: {self._reason}"

    def _open_streak(self, now: float) -> None:
        self._streak_start = now
        self._streak_failures = 1
        self._last_log = now

    def _count_failure(self) -> tuple[int, str] | None:
        """Count one failure of the last recorded error; the log line it earns, if any. Caller holds the lock."""
        now = self._clock()
        reason = self._reason
        self._failed_total += 1
        self._first_ok = None
        if self._streak_start is None:
            self._open_streak(now)
            message = f"{self.label}: send failed: {reason}"
            self._start_logged = now >= self._quiet_until
            if self._start_logged:
                self._quiet_until = now + _REMIND_EVERY_S
                if self._quiet_starts:
                    plural = "s" if self._quiet_starts != 1 else ""
                    message += f" ({self._quiet_starts} brief failure{plural} since the last report)"
                self._quiet_starts = 0
                level = logging.WARNING
            else:
                self._quiet_starts += 1
                level = logging.DEBUG
        else:
            self._streak_failures += 1
            lasted = now - self._streak_start
            if not self._failing and lasted >= FAILING_AFTER_S:
                self._failing = True
                self._last_log = now
                if not self._start_logged:
                    # Reported here, so the next summary leaves it out.
                    self._quiet_starts -= 1
                message = f"{self.label}: sends keep failing: {reason}"
                if looks_like_local_network(self._errno, self._multicast, self._platform):
                    message += (
                        " – macOS is refusing them, which is what Local Network access being off for"
                        " OpenFollow looks like (System Settings → Privacy & Security → Local Network)"
                    )
                level = logging.ERROR
            elif self._failing and now - self._last_log >= _REMIND_EVERY_S:
                self._last_log = now
                failed = self._streak_failures
                message = f"{self.label}: sends still failing after {lasted:.0f} s ({failed} failed): {reason}"
                level = logging.WARNING
            else:
                return None
        return level, message

    def reset(self) -> None:
        """Forget the current streak: the sender was stopped, restarted or repointed."""
        with self._lock:
            failing = self._failing
            lasted = self._clock() - self._streak_start if self._streak_start is not None else 0.0
            failed = self._streak_failures
            self._clear_streak()
        if failing:
            logger.info("%s: send record reset after %.0f s of failing sends (%d failed).", self.label, lasted, failed)

    def status(self) -> SendHealthStatus:
        with self._lock:
            in_streak = self._streak_start is not None
            if self._failing:
                state = "failing"
            elif in_streak:
                state = "retrying"
            else:
                state = "ok"
            return SendHealthStatus(
                label=self.label,
                state=state,
                failing_for_s=self._clock() - self._streak_start if self._streak_start is not None else None,
                failed=self._streak_failures,
                error=self._error if in_streak else "",
                errno=self._errno if in_streak else None,
                local_network=in_streak and looks_like_local_network(self._errno, self._multicast, self._platform),
                sent_total=self._sent_total,
                failed_total=self._failed_total,
            )

    def _clear_streak(self) -> None:
        self._streak_start = None
        self._streak_failures = 0
        self._failing = False
        self._first_ok = None


def send_alerts(statuses: Iterable[SendHealthStatus], actions: Mapping[str, str] | None = None) -> list[SendAlert]:
    """One row per failing sender, with every sender macOS refuses folded into one.

    Local Network access is one switch for the whole app, so a station showing
    its signature anywhere gets a single row naming it in place of one per
    output, and a unicast sender refused the same way rides along. *actions*
    gives the senders it names by label their own next step.
    """
    actions = actions or {}
    failing = [status for status in statuses if status.failing]
    blocked = any(status.local_network for status in failing)
    alerts: list[SendAlert] = []
    for status in failing:
        if blocked and status.errno == errno.EHOSTUNREACH:
            continue
        action = actions.get(status.label, SEND_FAILING_ACTION)
        alerts.append(SendAlert(f"{status.label}: sends keep failing ({status.error})", action))
    if blocked:
        alerts.insert(0, SendAlert(LOCAL_NETWORK_TEXT, LOCAL_NETWORK_ACTION, LOCAL_NETWORK_HUD_TEXT))
    return alerts
