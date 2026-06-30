# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Background worker: on startup and on IP change, if the LAN has internet,
sync the system clock (NTP) and check GitHub for a newer release.

The show LAN normally has no uplink. When it briefly does (e.g. during setup),
this opportunistically corrects the clock and surfaces an "update available"
banner. Every network step fails silently offline – nothing here is on the
data path, and the clock-set never prompts for a password (it runs only when
the sudoers grant is already in place).

Ordering inside a cycle is deliberate: NTP time-sync runs BEFORE the GitHub
HTTPS check, so a stale clock (no RTC) is corrected first and TLS certificate
validation on the release check doesn't fail from clock skew.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from openfollow.runtime.deb_update import check_for_update
from openfollow.runtime.time_sync import (
    DRIFT_THRESHOLD_S,
    is_plausible_epoch,
    query_ntp,
    set_system_clock,
)

if TYPE_CHECKING:
    from openfollow.configuration import AppConfig
    from openfollow.privilege.broker import PrivilegeBroker

logger = logging.getLogger(__name__)

_POLL_INTERVAL_S = 15.0
# Daily re-sync. The clock and the release check don't need to be chattier than
# that; startup and IP-change triggers cover the time-critical cases, so this is
# just the long-running-session backstop. Kept deliberately gentle on the
# external NTP / GitHub servers.
_PERIODIC_INTERVAL_S = 24 * 3600.0
_INITIAL_DELAY_S = 5.0
_NTP_TIMEOUT_S = 5.0


def _is_real_ip(ip: str) -> bool:
    """True for a usable LAN address (not empty / loopback / unset)."""
    if not ip or ip in ("N/A", "0.0.0.0"):  # nosec B104 - comparison, not a bind
        return False
    return not ip.startswith("127.")


class OnlineSyncWorker:
    """Daemon thread driving opportunistic time-sync + update-check.

    Triggers a cycle on startup, whenever the device's primary IP changes to a
    usable address, and once a day so a long-running session still notices a
    release published mid-run and keeps the clock from slowly drifting.
    """

    def __init__(
        self,
        *,
        config_provider: Callable[[], AppConfig],
        ip_provider: Callable[[], str],
        broker: PrivilegeBroker | None,
        web_commands: Any,
        version: str,
        poll_interval: float = _POLL_INTERVAL_S,
        periodic_interval: float = _PERIODIC_INTERVAL_S,
        initial_delay: float = _INITIAL_DELAY_S,
        ntp_timeout: float = _NTP_TIMEOUT_S,
        # Injectable for hermetic tests; default to the real implementations.
        ntp_query: Callable[[str, float], float] = query_ntp,
        set_clock: Callable[[Any, int], bool] = set_system_clock,
        update_check: Callable[..., dict[str, Any]] = check_for_update,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        platform_can_set_clock: bool | None = None,
    ) -> None:
        self._config_provider = config_provider
        self._ip_provider = ip_provider
        self._broker = broker
        self._web_commands = web_commands
        self._version = version
        self._poll_interval = poll_interval
        self._periodic_interval = periodic_interval
        self._initial_delay = initial_delay
        self._ntp_timeout = ntp_timeout
        self._ntp_query = ntp_query
        self._set_clock = set_clock
        self._update_check = update_check
        self._now = monotonic
        self._wall_now = wall_clock
        if platform_can_set_clock is None:
            import sys

            platform_can_set_clock = sys.platform.startswith("linux")
        self._can_set_clock = platform_can_set_clock

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_ip: str | None = None
        self._last_cycle_monotonic: float | None = None

    # ----- lifecycle -----------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="OnlineSyncWorker")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            # A mid-cycle network call (NTP/GitHub) outlives this join; the
            # daemon thread is reaped at process exit. Don't block shutdown on it.
            self._thread.join(timeout=2.0)
            self._thread = None

    # ----- loop ----------------------------------------------------------

    def _run(self) -> None:
        if self._stop_event.wait(self._initial_delay):
            return
        self._last_ip = self._ip_provider()
        self._run_cycle("startup")
        while not self._stop_event.wait(self._poll_interval):
            try:
                self._maybe_cycle()
            except Exception:
                logger.exception("online-sync poll iteration failed")

    def _maybe_cycle(self) -> None:
        ip = self._ip_provider()
        changed = ip != self._last_ip
        self._last_ip = ip
        last = self._last_cycle_monotonic
        due_periodic = last is None or (self._now() - last) >= self._periodic_interval
        if changed and _is_real_ip(ip):
            self._run_cycle("ip-change")
        elif due_periodic:
            self._run_cycle("periodic")

    def _run_cycle(self, reason: str) -> None:
        self._last_cycle_monotonic = self._now()
        cfg = self._config_provider()
        logger.debug("online-sync cycle (%s)", reason)
        # NTP first so the clock is correct before the HTTPS update check.
        if cfg.auto_time_sync:
            self._sync_time(cfg)
        if cfg.auto_update_check:
            self._check_update(cfg)

    # ----- actions -------------------------------------------------------

    def _sync_time(self, cfg: AppConfig) -> None:
        if not self._can_set_clock or self._broker is None:
            return
        try:
            epoch = self._ntp_query(cfg.time_sync_server, self._ntp_timeout)
        except Exception as exc:
            logger.debug("NTP query failed: %s", exc)
            return
        if not is_plausible_epoch(epoch):
            logger.debug("NTP returned an implausible epoch (%s); ignoring.", epoch)
            return
        drift = abs(epoch - self._wall_now())
        if drift < DRIFT_THRESHOLD_S:
            logger.debug("Clock within %.1fs of NTP (drift %.2fs); not setting.", DRIFT_THRESHOLD_S, drift)
            return
        self._set_clock(self._broker, round(epoch))

    def _check_update(self, cfg: AppConfig) -> None:
        repo = cfg.update_github_repo.strip()
        if not repo:
            return
        try:
            info = self._update_check(
                repo,
                self._version,
                include_prereleases=cfg.update_include_prereleases,
            )
        except Exception as exc:
            # Offline / API error – leave any prior known state untouched.
            logger.debug("Update check failed: %s", exc)
            return
        if info.get("available"):
            self._web_commands.set_update_available(str(info.get("latest", "")))
        else:
            self._web_commands.set_update_available("")
