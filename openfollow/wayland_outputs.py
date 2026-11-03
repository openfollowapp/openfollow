# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""The outputs a wlroots compositor holds, read through wlr-output-management.

A read-only Wayland client on the standard library: it binds
``zwlr_output_manager_v1`` at version 2, collects every head (enabled or not)
with its modes until the manager's ``done``, and disconnects without ever
creating a configuration, so it cannot change the layout.
"""

from __future__ import annotations

import os
import socket
import struct
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

_MANAGER_INTERFACE = "zwlr_output_manager_v1"
# Version 2 adds make / model; later versions add nothing this reader shows.
_MANAGER_VERSION = 2

_DISPLAY_ID = 1
_REGISTRY_ID = 2
_CALLBACK_ID = 3
_MANAGER_ID = 4

_HEADER = struct.Struct("=II")
_UINT = struct.Struct("=I")
_INT = struct.Struct("=i")
_INT2 = struct.Struct("=ii")


class WaylandError(Exception):
    """The layout could not be read; the message says why, in one clause."""


@dataclass(frozen=True)
class OutputMode:
    width: int
    height: int
    refresh_mhz: int
    preferred: bool


@dataclass(frozen=True)
class OutputHead:
    name: str
    description: str
    make: str
    model: str
    enabled: bool
    current_mode: OutputMode | None
    position: tuple[int, int]
    transform: int
    scale: float
    modes: tuple[OutputMode, ...]


@dataclass
class _Mode:
    width: int = 0
    height: int = 0
    refresh_mhz: int = 0
    preferred: bool = False

    def freeze(self) -> OutputMode:
        return OutputMode(self.width, self.height, self.refresh_mhz, self.preferred)


@dataclass
class _Head:
    name: str = ""
    description: str = ""
    make: str = ""
    model: str = ""
    enabled: bool = False
    current_mode_id: int | None = None
    position: tuple[int, int] = (0, 0)
    transform: int = 0
    scale: float = 1.0
    mode_ids: list[int] = field(default_factory=list)


def socket_path(environ: Mapping[str, str] = os.environ) -> Path | None:
    """The session's Wayland socket, resolved as libwayland does; ``None`` outside a session."""
    display = environ.get("WAYLAND_DISPLAY", "")
    if not display:
        return None
    if display.startswith("/"):
        return Path(display)
    runtime = environ.get("XDG_RUNTIME_DIR", "")
    return Path(runtime) / display if runtime else None


def _string(payload: bytes, offset: int) -> tuple[str, int]:
    (length,) = _UINT.unpack_from(payload, offset)
    start = offset + 4
    text = payload[start : start + max(length - 1, 0)].decode("utf-8", "replace")
    return text, start + (length + 3) // 4 * 4


def _encode_string(text: str) -> bytes:
    raw = text.encode() + b"\0"
    return _UINT.pack(len(raw)) + raw + b"\0" * (-len(raw) % 4)


def _request(object_id: int, opcode: int, payload: bytes = b"") -> bytes:
    return _HEADER.pack(object_id, (8 + len(payload)) << 16 | opcode) + payload


class _Reader:
    """Decodes the event stream and keeps the state of every object it created or was handed."""

    def __init__(self, sock: socket.socket, deadline: float) -> None:
        self._sock = sock
        self._deadline = deadline
        self._buffer = b""
        self.manager_global: tuple[int, int] | None = None
        self.synced = False
        self.done = False
        self.heads: dict[int, _Head] = {}
        self.modes: dict[int, _Mode] = {}

    def pump(self) -> None:
        """Read until at least one more complete message has been handled."""
        while True:
            if len(self._buffer) >= 8:
                object_id, word = _HEADER.unpack_from(self._buffer)
                size = word >> 16
                if size < 8:
                    raise WaylandError(f"malformed message from the compositor (size {size})")
                if len(self._buffer) >= size:
                    payload = self._buffer[8:size]
                    self._buffer = self._buffer[size:]
                    self._handle(object_id, word & 0xFFFF, payload)
                    return
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                raise WaylandError("the compositor did not answer in time")
            self._sock.settimeout(remaining)
            try:
                chunk = self._sock.recv(4096)
            except TimeoutError:
                raise WaylandError("the compositor did not answer in time") from None
            if not chunk:
                raise WaylandError("the compositor closed the connection")
            self._buffer += chunk

    def _handle(self, object_id: int, opcode: int, payload: bytes) -> None:
        if object_id == _DISPLAY_ID and opcode == 0:
            _object, code = struct.unpack_from("=II", payload)
            message, _ = _string(payload, 8)
            raise WaylandError(f"the compositor reported error {code}: {message}")
        if object_id == _REGISTRY_ID and opcode == 0:
            (name,) = _UINT.unpack_from(payload)
            interface, offset = _string(payload, 4)
            (version,) = _UINT.unpack_from(payload, offset)
            if interface == _MANAGER_INTERFACE:
                self.manager_global = (name, version)
        elif object_id == _CALLBACK_ID and opcode == 0:
            self.synced = True
        elif object_id == _MANAGER_ID:
            self._handle_manager(opcode, payload)
        elif object_id in self.heads:
            self._handle_head(self.heads[object_id], object_id, opcode, payload)
        elif object_id in self.modes:
            self._handle_mode(self.modes[object_id], object_id, opcode, payload)

    def _handle_manager(self, opcode: int, payload: bytes) -> None:
        if opcode == 0:
            (head_id,) = _UINT.unpack_from(payload)
            self.heads[head_id] = _Head()
        elif opcode == 1:
            self.done = True
        elif opcode == 2:
            raise WaylandError("the compositor withdrew wlr-output-management")

    def _handle_head(self, head: _Head, head_id: int, opcode: int, payload: bytes) -> None:
        if opcode in (0, 1, 10, 11):
            text, _ = _string(payload, 0)
            attr = {0: "name", 1: "description", 10: "make", 11: "model"}[opcode]
            setattr(head, attr, text)
        elif opcode == 3:
            (mode_id,) = _UINT.unpack_from(payload)
            self.modes[mode_id] = _Mode()
            head.mode_ids.append(mode_id)
        elif opcode == 4:
            head.enabled = _INT.unpack_from(payload)[0] != 0
        elif opcode == 5:
            head.current_mode_id = _UINT.unpack_from(payload)[0]
        elif opcode == 6:
            head.position = _INT2.unpack_from(payload)
        elif opcode == 7:
            head.transform = _INT.unpack_from(payload)[0]
        elif opcode == 8:
            head.scale = _INT.unpack_from(payload)[0] / 256
        elif opcode == 9:
            del self.heads[head_id]

    def _handle_mode(self, mode: _Mode, mode_id: int, opcode: int, payload: bytes) -> None:
        if opcode == 0:
            mode.width, mode.height = _INT2.unpack_from(payload)
        elif opcode == 1:
            mode.refresh_mhz = _INT.unpack_from(payload)[0]
        elif opcode == 2:
            mode.preferred = True
        elif opcode == 3:
            del self.modes[mode_id]

    def heads_snapshot(self) -> list[OutputHead]:
        out: list[OutputHead] = []
        for head in self.heads.values():
            modes = {mid: self.modes[mid].freeze() for mid in head.mode_ids if mid in self.modes}
            current = modes.get(head.current_mode_id) if head.enabled and head.current_mode_id is not None else None
            out.append(
                OutputHead(
                    name=head.name,
                    description=head.description,
                    make=head.make,
                    model=head.model,
                    enabled=head.enabled,
                    current_mode=current,
                    position=head.position,
                    transform=head.transform,
                    scale=head.scale,
                    modes=tuple(modes.values()),
                )
            )
        return out


def read_heads_from(sock: socket.socket, timeout_s: float) -> list[OutputHead]:
    """Every head the compositor on ``sock`` announces, in its order."""
    reader = _Reader(sock, time.monotonic() + timeout_s)
    sock.sendall(
        _request(_DISPLAY_ID, 1, _UINT.pack(_REGISTRY_ID)) + _request(_DISPLAY_ID, 0, _UINT.pack(_CALLBACK_ID))
    )
    while not reader.synced:
        reader.pump()
    if reader.manager_global is None:
        raise WaylandError("the compositor does not offer wlr-output-management")
    name, version = reader.manager_global
    bind = _UINT.pack(name) + _encode_string(_MANAGER_INTERFACE)
    bind += _UINT.pack(min(version, _MANAGER_VERSION)) + _UINT.pack(_MANAGER_ID)
    sock.sendall(_request(_REGISTRY_ID, 0, bind))
    while not reader.done:
        reader.pump()
    return reader.heads_snapshot()


def read_heads(path: Path, timeout_s: float) -> list[OutputHead]:
    """Connect to the Wayland socket at ``path`` and read its heads; raises :class:`WaylandError`."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout_s)
        try:
            sock.connect(str(path))
        except OSError as exc:
            raise WaylandError(f"cannot connect to {path}: {exc.strerror or exc}") from None
        return read_heads_from(sock, timeout_s)
