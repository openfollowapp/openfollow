# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Web-triggered command handlers: restart, button detection, and dispatch
of the signed-``.deb`` update worker."""

from __future__ import annotations

import logging
import os
import sys
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openfollow.app import OpenFollowApp

logger = logging.getLogger(__name__)


def check_restart_request(app: OpenFollowApp) -> None:
    """Check if web UI requested app restart."""
    if app._update_worker is not None and app._update_worker.is_alive():
        return
    if app._web_commands.consume_restart_requested():
        logger.info("Restart requested from web UI.")
        app._restart_app()


def check_button_detection_request(app: OpenFollowApp) -> None:
    """Check if web UI requested (or cancelled) the button detection wizard."""
    if app._web_commands.consume_button_detection_requested():
        logger.info("Button detection requested from web UI.")
        app._enter_button_detection()
    # Web cancel drains on main loop; no-op if wizard not running.
    if app._web_commands.consume_button_detection_cancel_requested():
        logger.info("Button detection cancel requested from web UI.")
        app._exit_button_detection()


def check_controller_slot_actions(app: OpenFollowApp) -> None:
    """Run the Identify / Forget clicks queued from the web Controller Slots table."""
    input_manager = app._input_manager
    for action, index, ref in app._web_commands.consume_slot_actions():
        if input_manager is None:
            continue
        # The table the click came from can be a second old; act only on the slot it showed.
        if input_manager.slot_ref(index) != ref:
            logger.info("Controller slot C%d changed since it was clicked; %s not applied.", index + 1, action)
            continue
        try:
            if action == "identify":
                input_manager.identify_slot(index)
            elif action == "forget":
                input_manager.forget_slot(index)
        except Exception:
            # One failed action must not drop the others already taken off the queue.
            logger.exception("Controller slot action %s on C%d failed.", action, index + 1)


def check_camera_setup_requests(app: OpenFollowApp) -> None:
    """Stop, then rebuild, the Pi Camera pipeline around a live change from the web camera setup."""
    web_commands = app._web_commands
    receiver = app._video_receiver
    picam = receiver if receiver is not None and receiver._source_type == "picam" else None
    if web_commands.consume_camera_release_requested():
        try:
            if picam is not None:
                picam.release_source()
        except Exception:
            # Unconfirmed: the web side leaves the loaded camera alone.
            logger.exception("Stopping the Pi Camera pipeline failed.")
        else:
            web_commands.confirm_camera_released()
    if not web_commands.consume_video_rebuild_requested() or picam is None:
        return
    logger.info("Pi camera set up from the web UI; rebuilding the Pi Camera pipeline.")
    try:
        picam.swap_input(picam._source_type, dict(picam._input_config))
    except Exception:
        logger.exception("Rebuilding the Pi Camera pipeline failed.")


def check_update_request(app: OpenFollowApp) -> None:
    """Check if web UI requested an update operation."""
    # Reap a finished worker before evaluating the guard. A failed update
    # sets a terminal status before the daemon thread fully exits, so a
    # fast operator retry can land while ``is_alive()`` is still True. The
    # in-progress check MUST run before ``consume_update_requested`` –
    # consuming first would clear the just-queued request and then drop it
    # when the still-tearing-down worker reports alive, stranding the web
    # UI on a "running" status no worker will ever advance.
    worker = app._update_worker
    if worker is not None and not worker.is_alive():
        app._update_worker = None
        worker = None
    if worker is not None:
        # Leave the request queued (do not consume) so the next tick picks
        # it up once the worker has fully exited. Peek the pending flag –
        # never consume here – and surface "already running" if a retry
        # is waiting.
        if app._web_commands._update_requested.is_set():
            logger.info("Ignoring update request because another update is still running.")
            app._web_commands.set_update_status(
                "running",
                message="Update is already in progress.",
            )
        return

    request = app._web_commands.consume_update_requested()
    if request is None:
        return

    kind = request.get("kind")
    if kind == "deb-local":
        target = app._run_local_update
    elif kind == "deb":
        target = app._run_deb_update
    else:
        logger.warning("Ignoring update request with unknown kind: %r", kind)
        return
    app._update_worker = threading.Thread(
        target=target,
        args=(request,),
        daemon=True,
        name="WebUpdateWorker",
    )
    app._update_worker.start()


def restart_app(app: OpenFollowApp) -> None:
    """Restart the application.

    On a systemd-managed install (the .deb / Pi kiosk) the process is the leaf
    of a ``cage -> sh -> python`` chain. An in-process ``os.execv`` re-exec keeps
    the same PID and reconnects to the *existing* Cage Wayland session, which
    does not reliably bring the on-screen GUI back. When systemd manages us
    (``Restart=always``), exiting instead lets it respawn the whole unit – a
    fresh Cage session – so the display recovers cleanly. ``INVOCATION_ID`` is
    set by systemd for the service and inherited down the exec chain.

    Outside systemd (dev ``poetry run``) nothing would respawn us, so fall back
    to the re-exec.
    """
    app._runtime_services.shutdown()
    assert app._canvas is not None
    app._canvas.close()
    if os.environ.get("INVOCATION_ID"):
        sys.exit(0)
    os.execv(sys.executable, [sys.executable] + sys.argv)  # nosec B606
