# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unified OSC service: sends/receives via OscService, templates & transmitters."""

from openfollow.osc.input import OscMarkerAdapter
from openfollow.osc.service import (
    _PYTHONOSC_AVAILABLE,
    ClientStats,
    OscService,
    find_free_udp_port,
)
from openfollow.osc.template import (
    BUILTIN_TEMPLATES,
    PLACEHOLDERS,
    BuiltinTemplate,
    RenderContext,
    RenderError,
    builtin_by_id,
    compile_template,
    osc_arg_for,
    render,
)
from openfollow.osc.transmitter import (
    BindingRingBuffer,
    OscTransmitter,
    OscTransmitterManager,
    RingBufferEntry,
)
from openfollow.osc.transport import TcpOscSender

__all__ = [
    "BUILTIN_TEMPLATES",
    "BindingRingBuffer",
    "BuiltinTemplate",
    "ClientStats",
    "OscService",
    "OscMarkerAdapter",
    "OscTransmitter",
    "OscTransmitterManager",
    "PLACEHOLDERS",
    "RenderContext",
    "RenderError",
    "RingBufferEntry",
    "TcpOscSender",
    "_PYTHONOSC_AVAILABLE",
    "builtin_by_id",
    "compile_template",
    "find_free_udp_port",
    "osc_arg_for",
    "render",
]
