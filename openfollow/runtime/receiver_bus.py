# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Bus message helpers for ``GstNativeSinkReceiver``."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

MessageCallback = Callable[[Any, Any], None]
PipelineProvider = Callable[[], Any | None]
AsyncDoneHandler = Callable[[Any], None]
ErrorHandler = Callable[[str], None]
EosHandler = Callable[[], None]
BoolProvider = Callable[[], bool]
TextProvider = Callable[[], str]


class ReceiverBusHandler:
    """Owns bus watch setup and bus message dispatch."""

    def __init__(
        self,
        gst: Any,
        logger: logging.Logger,
        get_pipeline: PipelineProvider,
        on_async_done: AsyncDoneHandler,
        on_error: ErrorHandler,
        on_eos: EosHandler,
        is_placeholder_pipeline: BoolProvider,
        get_input_display_name: TextProvider,
    ) -> None:
        self._gst = gst
        self._logger = logger
        self._get_pipeline = get_pipeline
        self._on_async_done = on_async_done
        self._on_error = on_error
        self._on_eos = on_eos
        self._is_placeholder_pipeline = is_placeholder_pipeline
        self._get_input_display_name = get_input_display_name
        # Handler id from ``bus.connect("message", ...)`` so teardown can
        # disconnect it symmetrically.
        self._message_handler_id: int | None = None

    def setup_bus(self, pipeline: Any, on_message: MessageCallback) -> None:
        if pipeline is None:
            return
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        self._message_handler_id = bus.connect("message", on_message)

    def teardown_bus(self, pipeline: Any) -> None:
        """Undo :meth:`setup_bus` so the bus – and thus the pipeline and its
        sockets – can actually be freed.

        ``add_signal_watch`` installs a GSource on the main context that holds
        a reference to the bus, which transitively keeps the whole pipeline
        alive (including ``rtspsrc``'s UDP/TCP sockets). Tearing a pipeline
        down with only ``set_state(NULL)`` + dropping the Python reference is
        therefore NOT enough: without removing the watch, every reconnect
        leaks file descriptors. Call this on every pipeline disposal – reconnect, stop, swap.
        """
        if pipeline is None:
            return
        bus = pipeline.get_bus()
        if bus is None:
            return
        # Disconnect the "message" handler before removing the watch – keeps
        # attach/detach symmetric so a re-setup can't leak a GSource + closure.
        if self._message_handler_id is not None:
            try:
                bus.disconnect(self._message_handler_id)
            except Exception:
                self._logger.debug(
                    "Ignoring failure while disconnecting pipeline bus message handler",
                    exc_info=True,
                )
            self._message_handler_id = None
        try:
            bus.remove_signal_watch()
        except Exception:
            self._logger.debug(
                "Ignoring failure while removing pipeline bus watch",
                exc_info=True,
            )

    def handle_message(self, bus: Any, message: Any) -> None:
        msg_type = message.type
        if msg_type == self._gst.MessageType.ASYNC_DONE:
            pipeline = self._get_pipeline()
            if pipeline is not None:
                self._on_async_done(pipeline)
            return

        if msg_type == self._gst.MessageType.ERROR:
            err, debug = message.parse_error()
            error_msg = err.message if err else "Unknown error"
            self._logger.error("GStreamer error: %s (%s)", error_msg, debug)
            self._on_error(error_msg)
            return

        if msg_type == self._gst.MessageType.EOS:
            self._logger.info("GStreamer EOS received.")
            self._on_eos()
            return

        if msg_type == self._gst.MessageType.STATE_CHANGED:
            self._handle_state_changed(message)

    def _handle_state_changed(self, message: Any) -> None:
        if message.src.get_name() not in ("videosink", "shared_videosink"):
            return
        _, new_state, _ = message.parse_state_changed()
        if new_state != self._gst.State.PLAYING:
            return
        if self._is_placeholder_pipeline():
            self._logger.debug("Placeholder pipeline videosink reached PLAYING state")
            return
        self._logger.debug(
            "Videosink reached PLAYING for %s input; awaiting first frame.",
            self._get_input_display_name(),
        )
