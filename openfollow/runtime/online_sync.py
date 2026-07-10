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

import ipaddress
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
# Daily re-sync once we've reached the network. The clock and the release check
# don't need to be chattier than that; startup and IP-change triggers cover the
# time-critical cases, so this is just the long-running-session backstop. Kept
# deliberately gentle on the external NTP / GitHub servers.
_PERIODIC_INTERVAL_S = 24 * 3600.0
# Retry cadence while we've NOT yet reached the network. An uplink can appear
# after boot without the device's own IP changing (static / DHCP-reserved show
# Pi), so retry every few minutes until a cycle succeeds, then relax to daily.
_RETRY_INTERVAL_S = 300.0
# Floor on the gap between IP-change-triggered cycles, so a flapping IP can't
# fire an NTP + GitHub cycle on every poll.
_MIN_CYCLE_INTERVAL_S = 60.0
_INITIAL_DELAY_S = 5.0
_NTP_TIMEOUT_S = 5.0


def _is_real_ip(ip: str) -> bool:
    """True for a routable-on-LAN address.

    Rejects empty / non-address sentinels, loopback, the unspecified address,
    and link-local (169.254/16, fe80::/10) – the last is what a host
    self-assigns when DHCP fails, so it is not a real-uplink signal.
    """
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return False
    return not (addr.is_loopback or addr.is_link_local or addr.is_unspecified)


class OnlineSyncWorker:
    """Daemon thread driving opportunistic time-sync + update-check.

    Triggers a cycle on startup and whenever the device's primary IP changes to
    a usable address (rate-limited). Between those, it retries every few minutes
    until a cycle reaches the network, then relaxes to a daily backstop – so an
    uplink appearing after boot (without the device's IP changing) is picked up
    within minutes, while a long-running online session stays gentle on the
    external NTP / GitHub servers.
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
        retry_interval: float = _RETRY_INTERVAL_S,
        min_cycle_interval: float = _MIN_CYCLE_INTERVAL_S,
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
        self._retry_interval = retry_interval
        self._min_cycle_interval = min_cycle_interval
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
        # Whether the last cycle reached the network. Drives the retry-vs-daily
        # cadence: retry every few minutes until online, then relax to daily.
        self._online = False

    # ----- lifecycle -----------------------------------------------------

    def start(self) -> None:
        thread = self._thread
        # Key on liveness, not mere presence: a prior stop() whose join timed out
        # on an in-flight cycle leaves a still-set reference to a soon-dead
        # thread. If it's alive, don't spawn a second worker; if it has since
        # died (or was never started), (re)start fresh.
        if thread is not None and thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="OnlineSyncWorker")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            # A mid-cycle network call (NTP/GitHub) outlives this join; the
            # daemon thread is reaped at process exit. Don't block shutdown on it.
            thread.join(timeout=2.0)
            # Only drop the reference once the thread has actually exited. If the
            # join timed out on an in-flight network call, keep it so a later
            # start() sees a live worker and won't clear _stop_event out from
            # under it, which would let the old thread keep running as a second
            # worker alongside the new one.
            if not thread.is_alive():
                self._thread = None

    # ----- loop ----------------------------------------------------------

    def _run(self) -> None:
        if self._stop_event.wait(self._initial_delay):
            return
        # Guard the startup cycle like every poll iteration: an exception here
        # (a raising ip provider, a malformed config field) must not kill the
        # daemon thread before it reaches the resilient poll loop below.
        try:
            self._last_ip = self._ip_provider()
            self._run_cycle("startup")
        except Exception:
            logger.exception("online-sync startup cycle failed")
        while not self._stop_event.wait(self._poll_interval):
            try:
                self._maybe_cycle()
            except Exception:
                logger.exception("online-sync poll iteration failed")

    def _maybe_cycle(self) -> None:
        cfg = self._config_provider()
        # Nothing enabled -> don't even resolve the IP (interface enumeration).
        if not (cfg.auto_time_sync or cfg.auto_update_check):
            return
        ip = self._ip_provider()
        changed = ip != self._last_ip
        self._last_ip = ip
        last = self._last_cycle_monotonic
        elapsed = None if last is None else (self._now() - last)
        # Retry sooner until a cycle reaches the network; relax to the daily
        # backstop once online. This picks up an uplink that appears after boot
        # without the device's IP changing, within the retry window not a day.
        periodic_interval = self._periodic_interval if self._online else self._retry_interval
        due_periodic = elapsed is None or elapsed >= periodic_interval
        # An IP change is a strong "connectivity may have appeared" signal, but
        # rate-limit it so a flapping IP can't fire a cycle on every poll.
        due_ip = changed and _is_real_ip(ip) and (elapsed is None or elapsed >= self._min_cycle_interval)
        if due_ip:
            self._run_cycle("ip-change")
        elif due_periodic:
            self._run_cycle("periodic" if self._online else "retry")

    def _run_cycle(self, reason: str) -> None:
        self._last_cycle_monotonic = self._now()
        cfg = self._config_provider()
        logger.debug("online-sync cycle (%s)", reason)
        reached = False
        # NTP first so the clock is correct before the HTTPS update check.
        if cfg.auto_time_sync:
            reached = self._sync_time(cfg) or reached
        if cfg.auto_update_check:
            reached = self._check_update(cfg) or reached
        # Only a cycle that actually attempted network work updates the online
        # verdict; a both-flags-off cycle leaves the retry cadence alone.
        if cfg.auto_time_sync or cfg.auto_update_check:
            self._online = reached

    # ----- actions -------------------------------------------------------

    def _sync_time(self, cfg: AppConfig) -> bool:
        """Sync the clock from NTP. Returns True if the NTP server was reached
        (whether or not the clock needed setting), False if it couldn't be
        reached or clock-set isn't available on this host."""
        if not self._can_set_clock or self._broker is None:
            return False
        try:
            epoch = self._ntp_query(cfg.time_sync_server, self._ntp_timeout)
        except Exception as exc:
            logger.debug("NTP query failed: %s", exc)
            return False
        if not is_plausible_epoch(epoch):
            logger.debug("NTP returned an implausible epoch (%s); ignoring.", epoch)
            return True
        drift = abs(epoch - self._wall_now())
        if drift < DRIFT_THRESHOLD_S:
            logger.debug("Clock within %.1fs of NTP (drift %.2fs); not setting.", DRIFT_THRESHOLD_S, drift)
            return True
        self._set_clock(self._broker, round(epoch))
        return True

    def _check_update(self, cfg: AppConfig) -> bool:
        """Check GitHub for a newer release. Returns True if GitHub was reached,
        False on offline / API error / bad config."""
        try:
            # Inside the try so a malformed (non-string) update_github_repo can't
            # raise past here and abort the cycle uncaught.
            repo = cfg.update_github_repo.strip()
            if not repo:
                return False
            info = self._update_check(
                repo,
                self._version,
                include_prereleases=cfg.update_include_prereleases,
            )
        except Exception as exc:
            # Offline / API error / bad config – leave any prior known state untouched.
            logger.debug("Update check failed: %s", exc)
            return False
        if info.get("available"):
            self._web_commands.set_update_available(str(info.get("latest", "")))
        else:
            self._web_commands.set_update_available("")
        return True
