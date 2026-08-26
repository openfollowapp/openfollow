# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Main GTK loop, the display-independent frame clock, housekeeping timer, and
config-file hot-reload polling for ``OpenFollowApp``."""

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
# at 10 Hz on its own GLib timeout.
_HOUSEKEEPING_INTERVAL_MS = 100

# The frame clock. Marker state, input, detection pinning, zone evaluation, and
# every output are driven from this GLib timeout and NOT from the display's
# ``add_tick_callback``: that tick is the compositor's frame clock, so it never
# fires on a unit with no display attached. A station in that state would keep
# transmitting a frozen position at full rate on PSN, OTP, RTTrPM, and OSC, which
# is indistinguishable from a healthy stream. The display tick drives the HUD
# redraw alone (``GtkNativeSinkWindow.start_hud_tick``).
_FRAME_INTERVAL_MS = 16

# Frame integration step. The clock above is a timeout, not a vsync tick, so its
# cadence drifts with main-loop load; velocity integrates against real elapsed
# time rather than a fixed 1/60 so a WASD/gamepad nudge moves the marker at the
# configured speed regardless.
_DEFAULT_FRAME_DT = 1.0 / 60.0  # first-frame fallback before a real delta exists
_MAX_FRAME_DT = 0.1  # clamp the step after a stall so a marker can't teleport

# A frame clock that has not run for this long is stalled, not merely slow -
# roughly 60 missed frames at the interval above. Wide enough that a GC pause, a
# config reload, or a detection hiccup never trips it.
_FRAME_STALL_AFTER_S = 1.0

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
    # start_hud_tick / timeout_add are inside the try so a failure there still
    # runs shutdown() – the subsystems were already started by app.run() before
    # this loop is entered.
    try:
        app._canvas.start_hud_tick()
        GLib.timeout_add(_FRAME_INTERVAL_MS, app._run_frame)
        GLib.timeout_add(_HOUSEKEEPING_INTERVAL_MS, app._run_housekeeping)
        Gtk.main()
    finally:
        app._runtime_services.shutdown()


def run_frame(app: OpenFollowApp) -> bool:
    """Frame-clock timeout callback. Returns True so the timeout re-arms.

    Guarded for the same reason as :func:`housekeeping`: PyGObject does not
    reliably keep a source whose callback raised, so an unguarded exception here
    would silently kill the frame loop and freeze marker state exactly as a
    missing display tick does. The throttle keeps a persistently-raising frame
    from flooding the journal at 60 Hz.
    """
    canvas = app._canvas
    if canvas is None or canvas.is_closing:
        return False  # tearing down; drop the source rather than run against it
    try:
        animate(app)
    except Exception:
        app._frame_err_log.log()
    return True


def housekeeping(app: OpenFollowApp) -> bool:
    """Slow polled checks on their own 10 Hz timeout.

    Returns True so the timeout re-arms. Kept off the frame clock so web-driven
    update, config hot-reload, restart, and button-detection handling run at
    their own cadence rather than 60 times a second.
    """
    # Guard EACH check independently so one persistently-raising handler can't
    # tear down the GLib source (PyGObject does not reliably keep a source whose
    # callback raised) AND can't starve the checks after it – a broken restart
    # check must not block Pi-network draining / button-detection forever.
    for check in (
        app._check_config_reload,
        app._check_update_request,
        app._check_restart_request,
        app._check_pi_network_worker,
        app._check_button_detection_request,
        app._check_marker_speeds_persist,
        app._check_frame_loop_stall,
    ):
        try:
            check()
        except Exception:
            logger.exception("Housekeeping check %s raised; continuing.", getattr(check, "__name__", check))
    return True


def check_frame_loop_stall(app: OpenFollowApp) -> None:
    """Watch the frame clock from the housekeeping timer and log both edges.

    Polled here rather than from the frame loop for the obvious reason: a
    stalled loop cannot report itself, and ``publish_runtime_stats`` is called
    from ``animate`` so every telemetry figure freezes with it. The stamp is
    taken at the *top* of ``animate``, so a frame that hangs part-way through
    counts as a stall too.

    Edge-triggered: one line per episode, never one per poll.
    """
    last = app._last_animate_time
    if last is None:
        return  # first frame hasn't landed yet; nothing to compare against
    now = time.perf_counter()
    stalled = (now - last) >= _FRAME_STALL_AFTER_S
    if stalled == app._frame_stalled:
        return
    app._frame_stalled = stalled
    if stalled:
        app._frame_stall_since = now
        logger.warning(
            "Frame clock stalled %.1fs ago. Marker positions, input, and detection are frozen; "
            "PSN, OTP, RTTrPM, and OSC outputs are transmitting the last known state.",
            now - last,
        )
        return
    # Only reachable after the branch above ran, so the stall start is set.
    logger.info("Frame clock resumed after %.1fs.", now - app._frame_stall_since)


def animate(app: OpenFollowApp) -> None:
    frame_start = time.perf_counter()
    # Integrate against real elapsed time since the previous frame. Clamp the
    # step so a stall (blocked main loop, dropped frames) can't teleport the
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
    # housekeeping timeout (see ``housekeeping``) so they keep their own cadence
    # rather than the frame rate.
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
