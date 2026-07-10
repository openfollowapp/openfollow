# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Main GTK loop, per-frame animate tick, display-independent housekeeping
timer, and config-file hot-reload polling for ``OpenFollowApp``."""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

from openfollow.configuration import (
    apply_runtime_config_changes,
    config_write_lock,
    load_config,
    save_config,
)

if TYPE_CHECKING:
    from openfollow.app import OpenFollowApp

logger = logging.getLogger(__name__)

# Web-driven housekeeping (update, config reload, restart, button-detection) polls
# at 10 Hz on its own GLib timeout. It MUST NOT live on the frame tick: the tick is
# the display vsync (``add_tick_callback``), so it stalls when no display is
# attached, which would otherwise freeze update/restart/config-save handling on a
# headless device.
_HOUSEKEEPING_INTERVAL_MS = 100

# Frame integration step. ``animate`` runs on the display vsync tick
# (``add_tick_callback``, 60–120 Hz), so velocity must integrate against real
# elapsed time, not a fixed 1/60 – otherwise a WASD/gamepad nudge moves the
# marker at the wrong speed on non-60Hz displays (≈2× on a 120 Hz panel).
_DEFAULT_FRAME_DT = 1.0 / 60.0  # first-tick fallback before a real delta exists
_MAX_FRAME_DT = 0.1  # clamp the step after a stall so a marker can't teleport

# Quiet window after the last per-marker speed edit before it's flushed to disk,
# so a tap-streak / held bumper coalesces into a single write.
_SPEED_PERSIST_SETTLE_S = 2.5


def run_native_loop(app: OpenFollowApp) -> None:
    """Event loop for native_sink mode – GTK main loop, no RenderCanvas."""
    import signal as _signal

    from gi.repository import GLib, Gtk

    # Handle SIGTERM as well as SIGINT: in the kiosk deploy the app runs under
    # systemd (Type=simple, default KillSignal=SIGTERM), so `systemctl
    # stop`/`restart` sends SIGTERM. Without this, the interpreter's default
    # disposition kills the process immediately, Gtk.main() never returns, and
    # the ordered graceful teardown in the finally below is skipped on every
    # stop/restart. SIGINT only ever fires on a dev terminal Ctrl-C.
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, _signal.SIGINT, Gtk.main_quit)
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, _signal.SIGTERM, Gtk.main_quit)

    assert app._canvas is not None
    # start_tick_animation / timeout_add are inside the try so a failure there
    # still runs shutdown() – the subsystems were already started by app.run()
    # before this loop is entered.
    try:
        app._canvas.start_tick_animation(app._animate)
        GLib.timeout_add(_HOUSEKEEPING_INTERVAL_MS, app._run_housekeeping)
        Gtk.main()
    finally:
        app._runtime_services.shutdown()


def housekeeping(app: OpenFollowApp) -> bool:
    """Display-independent polled checks (GLib timeout, not the vsync tick).

    Returns True so the timeout re-arms. Kept separate from :func:`animate` so a
    headless device (no display tick) still services web-triggered update, config
    hot-reload, restart, and button-detection requests.
    """
    # Guard EACH check independently so one persistently-raising handler can't
    # tear down the GLib source (PyGObject does not reliably keep a source whose
    # callback raised) AND can't starve the checks after it – a broken restart
    # check must not block Pi-network draining / button-detection forever.
    # Mirrors window.py ``_on_tick``.
    for check in (
        app._check_config_reload,
        app._check_update_request,
        app._check_restart_request,
        app._check_pi_network_worker,
        app._check_button_detection_request,
        app._check_marker_speeds_persist,
    ):
        try:
            check()
        except Exception:
            logger.exception("Housekeeping check %s raised; continuing.", getattr(check, "__name__", check))
    return True


def animate(app: OpenFollowApp) -> None:
    frame_start = time.perf_counter()
    # Integrate against real elapsed time since the previous tick. Clamp the
    # step so a stall (hidden/paused window, dropped frames) can't teleport the
    # marker on the catch-up frame.
    last = app._last_animate_time
    dt = (frame_start - last) if last is not None else _DEFAULT_FRAME_DT
    dt = min(dt, _MAX_FRAME_DT)
    app._last_animate_time = frame_start

    if app._input_manager is not None:
        app._input_manager.keyboard_handler.poll_discrete_keys()
        key_presses = app._input_manager.keyboard_handler.consume_key_presses()
    else:
        key_presses = []

    for key in key_presses:
        app._handle_key_press(key)

    # Update / config-reload / restart / button-detection run on the
    # display-independent housekeeping timeout (see ``housekeeping``), not here –
    # the frame tick stalls when no display is attached.
    app._check_video_disconnect_banner()
    app._process_input(dt)

    if app._iface_selection_active:
        now = time.monotonic()
        if now - app._last_iface_refresh >= 1.0:
            app._last_iface_refresh = now
            app._refresh_iface_list()

    svc = app._runtime_services

    svc.update_video()
    svc.apply_detection_pin(dt)
    svc.update_zone_triggers()
    svc.update_marker_visuals()

    frame_time = time.perf_counter() - frame_start
    svc._frame_metrics.add_frame(frame_time)
    svc.publish_runtime_stats()

    assert app._canvas is not None
    app._canvas.request_draw(app._animate)


def get_config_mtime(app: OpenFollowApp) -> float:
    try:
        return os.path.getmtime(app._config_path)
    except OSError:
        return 0.0


def check_config_reload(app: OpenFollowApp) -> None:
    now = time.monotonic()
    if now - app._last_config_check < 1.0:
        return
    app._last_config_check = now

    mtime = app._get_config_mtime()
    if mtime <= app._config_mtime:
        return

    try:
        new_config = load_config(app._config_path, strict=True)
    except Exception as error:
        # Parse error; don't advance mtime so the next poll retries (mtime
        # stays > _config_mtime regardless of further file changes).
        logger.warning("Config reload error: %s", error)
        return

    try:
        applied = apply_runtime_config_changes(app, new_config)
    except Exception as error:
        # Apply error; don't advance mtime so the next poll retries (mtime
        # stays > _config_mtime regardless of further file changes).
        logger.warning("Config apply error: %s", error)
        return

    if not applied:
        # A live-apply section degraded and was reverted. Withhold the mtime
        # advance so the next poll retries it – matching the raise-based
        # sections, which already keep the mtime by propagating.
        logger.warning("Config partially applied; will retry on next poll.")
        return

    app._config_mtime = mtime
    logger.info("Config reloaded.")


def check_marker_speeds_persist(app: OpenFollowApp) -> None:
    """Flush the runtime-authoritative per-marker move speeds ~2.5s after the
    last R/T / gamepad-bumper edit, coalescing a tap-streak into one write.

    The flush reads the config fresh from disk under ``config_write_lock`` and
    injects the live ``marker_move_speeds`` before saving, so a concurrent web
    section save (which holds the same lock) can never be clobbered by writing
    the whole in-memory config wholesale. The load is ``strict=True`` (mirrors
    ``check_config_reload``): this flush fires automatically with no operator
    intent, so on a malformed/unparseable ``config.toml`` it must raise and retry
    rather than silently heal the file to its ``.bak`` snapshot or to defaults.
    The mtime is deliberately left alone: the benign reload that follows is a
    no-op for speeds (they're not reloaded) and correctly picks up whatever else
    the disk holds.
    """
    if not app._marker_speeds_dirty:
        return
    if time.monotonic() - app._marker_speeds_dirty_since < _SPEED_PERSIST_SETTLE_S:
        return

    try:
        with config_write_lock:
            cfg = load_config(app._config_path, strict=True)
            cfg.marker_move_speeds = dict(app._config.marker_move_speeds)
            save_config(cfg, app._config_path)
    except Exception:
        logger.exception("Failed to persist per-marker move speeds.")
        # Back off to the settle cadence so a persistent failure (e.g. a full
        # disk, or a transiently-malformed file) doesn't retry + log on every
        # housekeeping tick. Stays dirty, so it still retries after the window.
        app._marker_speeds_dirty_since = time.monotonic()
        return
    app._marker_speeds_dirty = False
