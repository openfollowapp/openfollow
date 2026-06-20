# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""GUI entry point for OpenFollow with GStreamer video overlay."""

from __future__ import annotations

import logging
import sys
import time

from openfollow import OpenFollowApp
from openfollow.logging_setup import setup_logging

logger = logging.getLogger(__name__)

# Circuit breaker: max 5 restarts within 5 minutes
_MAX_RESTARTS = 5
_RESTART_WINDOW = 300  # seconds


def main() -> None:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.toml"

    # Configure logging with in-memory ring fallback for diagnostics.
    log_ring = setup_logging(level=logging.INFO)

    restart_times: list[float] = []

    while True:
        # Monotonic clock: a wall-clock jump (late NTP sync, manual set) must
        # not widen or reset the breaker window on offline show LANs.
        current_time = time.monotonic()

        # Prune old restart times outside the window.
        restart_times = [t for t in restart_times if current_time - t < _RESTART_WINDOW]

        # Check if too many restarts in the window.
        if len(restart_times) >= _MAX_RESTARTS:
            logger.critical(
                "Circuit breaker triggered: %d restarts in %d seconds. Exiting.",
                len(restart_times),
                _RESTART_WINDOW,
            )
            sys.exit(1)

        app: OpenFollowApp | None = None
        try:
            app = OpenFollowApp(config_path=config_path, log_ring=log_ring)
            app.run()
            break  # Clean exit
        except KeyboardInterrupt:
            logger.info("Interrupted by user.")
            break
        except SystemExit:
            raise
        except Exception:
            restart_times.append(time.monotonic())
            logger.exception(
                "Application crashed. Restarting in 2s... (%d/%d recent restarts)",
                len(restart_times),
                _MAX_RESTARTS,
            )
            time.sleep(2)
        finally:
            # Tear down the just-exited instance's started subsystems (web
            # serve + beacon threads/sockets, PSN/OTP/RTTrPM servers, video
            # receiver, MIDI) before the next generation. The native loop's own
            # finally only fires once Gtk.main() is entered, so a crash during
            # post-init setup (or before the loop) would otherwise leak them
            # into the respawn (port-bind fallback, multicast collisions).
            # shutdown() is idempotent, so a clean exit is a no-op; best-effort
            # so a teardown error can't mask the crash.
            if app is not None:
                try:
                    app._runtime_services.shutdown()
                except Exception:
                    logger.exception("Error tearing down the exited instance.")


if __name__ == "__main__":
    main()
