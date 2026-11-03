# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""The wlr-output-management reader, against a scripted compositor on a socket pair."""

from __future__ import annotations

import socket
import struct
import tempfile
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from openfollow import wayland_outputs
from openfollow.wayland_outputs import OutputHead, OutputMode, WaylandError

pytestmark = pytest.mark.unit

_MANAGER = "zwlr_output_manager_v1"
_HEAD_BASE = 0xFF000000


def _msg(object_id: int, opcode: int, payload: bytes = b"") -> bytes:
    return struct.pack("=II", object_id, (8 + len(payload)) << 16 | opcode) + payload


def _str(text: str) -> bytes:
    raw = text.encode() + b"\0"
    return struct.pack("=I", len(raw)) + raw + b"\0" * (-len(raw) % 4)


def _u(*values: int) -> bytes:
    return struct.pack(f"={len(values)}I", *values)


def _i(*values: int) -> bytes:
    return struct.pack(f"={len(values)}i", *values)


def _globals(*interfaces: tuple[str, int]) -> bytes:
    out = b""
    for name, (interface, version) in enumerate(interfaces, start=1):
        out += _msg(2, 0, _u(name) + _str(interface) + _u(version))
    return out + _msg(3, 0, _u(0))


def _head(
    head_id: int,
    name: str,
    modes: list[tuple[int, int, int, bool]],
    *,
    enabled: bool = True,
    current: int | None = 0,
    position: tuple[int, int] = (0, 0),
    scale: float = 1.0,
    transform: int = 0,
    make: str = "",
    model: str = "",
    description: str = "",
) -> bytes:
    out = _msg(4, 0, _u(head_id)) + _msg(head_id, 0, _str(name)) + _msg(head_id, 1, _str(description))
    out += _msg(head_id, 2, _i(600, 340))
    mode_ids = []
    for index, (width, height, refresh, preferred) in enumerate(modes):
        mode_id = head_id + 0x100 * (index + 1)
        mode_ids.append(mode_id)
        out += _msg(head_id, 3, _u(mode_id)) + _msg(mode_id, 0, _i(width, height)) + _msg(mode_id, 1, _i(refresh))
        if preferred:
            out += _msg(mode_id, 2)
    out += _msg(head_id, 4, _i(int(enabled)))
    if enabled and current is not None:
        out += _msg(head_id, 5, _u(mode_ids[current]))
        out += (
            _msg(head_id, 6, _i(*position)) + _msg(head_id, 7, _i(transform)) + _msg(head_id, 8, _i(int(scale * 256)))
        )
    if make:
        out += _msg(head_id, 10, _str(make))
    if model:
        out += _msg(head_id, 11, _str(model))
    return out


class _Compositor:
    """Answers the registry round trip with ``globals_reply`` and a bind with ``bind_reply``."""

    def __init__(self, globals_reply: bytes, bind_reply: bytes = b"", *, chunk: int = 0) -> None:
        self.globals_reply = globals_reply
        self.bind_reply = bind_reply
        self.chunk = chunk
        self.requests: list[tuple[int, int, bytes]] = []
        self.client, self._server = socket.socketpair()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _send(self, data: bytes) -> None:
        step = self.chunk or len(data) or 1
        try:
            for start in range(0, len(data), step):
                self._server.sendall(data[start : start + step])
        except OSError:
            pass  # the client stopped reading

    def _serve(self) -> None:
        buffer = b""
        while True:
            try:
                data = self._server.recv(4096)
            except OSError:
                return
            if not data:
                return
            buffer += data
            while len(buffer) >= 8:
                object_id, word = struct.unpack_from("=II", buffer)
                size = word >> 16
                if len(buffer) < size:
                    break
                payload, buffer = buffer[8:size], buffer[size:]
                self.requests.append((object_id, word & 0xFFFF, payload))
                if (object_id, word & 0xFFFF) == (1, 0):
                    self._send(self.globals_reply)
                elif (object_id, word & 0xFFFF) == (2, 0):
                    self._send(self.bind_reply)

    def close(self) -> None:
        self._server.close()
        self.client.close()


@pytest.fixture
def compositor() -> Iterator[Callable[..., _Compositor]]:
    made: list[_Compositor] = []

    def make(*args: object, **kwargs: object) -> _Compositor:
        made.append(_Compositor(*args, **kwargs))  # type: ignore[arg-type]
        return made[-1]

    yield make
    for comp in made:
        comp.close()


_LAPTOP = (
    _head(
        _HEAD_BASE,
        "HDMI-A-1",
        [(1920, 1080, 60000, True), (1280, 720, 60000, False)],
        position=(0, 0),
        scale=1.5,
        transform=1,
        make="Acme",
        model="Show 27",
    )
    + _head(_HEAD_BASE + 1, "eDP-1", [(2560, 1600, 165000, True)], enabled=False, description="Built-in panel")
    + _msg(4, 1, _u(7))
)


def test_reads_every_head_enabled_or_not(compositor: Callable[..., _Compositor]) -> None:
    comp = compositor(_globals(("wl_output", 4), (_MANAGER, 4)), _LAPTOP)
    hdmi, edp = wayland_outputs.read_heads_from(comp.client, 2.0)
    assert hdmi == OutputHead(
        name="HDMI-A-1",
        description="",
        make="Acme",
        model="Show 27",
        enabled=True,
        current_mode=OutputMode(1920, 1080, 60000, True),
        position=(0, 0),
        transform=1,
        scale=1.5,
        modes=(OutputMode(1920, 1080, 60000, True), OutputMode(1280, 720, 60000, False)),
    )
    assert edp.name == "eDP-1"
    assert edp.enabled is False
    assert edp.current_mode is None
    assert edp.description == "Built-in panel"
    assert edp.modes == (OutputMode(2560, 1600, 165000, True),)


def test_binds_version_two_and_never_configures(compositor: Callable[..., _Compositor]) -> None:
    comp = compositor(_globals((_MANAGER, 4)), _LAPTOP)
    wayland_outputs.read_heads_from(comp.client, 2.0)
    binds = [payload for object_id, opcode, payload in comp.requests if (object_id, opcode) == (2, 0)]
    (bind,) = binds
    assert bind.endswith(_u(2, 4))
    assert not any(object_id == 4 for object_id, _op, _payload in comp.requests)


def test_an_older_manager_binds_at_its_own_version(compositor: Callable[..., _Compositor]) -> None:
    comp = compositor(_globals((_MANAGER, 1)), _msg(4, 1, _u(1)))
    assert wayland_outputs.read_heads_from(comp.client, 2.0) == []
    (bind,) = [payload for object_id, opcode, payload in comp.requests if (object_id, opcode) == (2, 0)]
    assert bind.endswith(_u(1, 4))


def test_messages_split_across_reads_still_decode(compositor: Callable[..., _Compositor]) -> None:
    comp = compositor(_globals((_MANAGER, 2)), _LAPTOP, chunk=3)
    assert [head.name for head in wayland_outputs.read_heads_from(comp.client, 2.0)] == ["HDMI-A-1", "eDP-1"]


def test_a_head_or_mode_withdrawn_before_done_is_left_out(compositor: Callable[..., _Compositor]) -> None:
    reply = (
        _head(_HEAD_BASE, "DP-1", [(1920, 1080, 60000, True), (1024, 768, 60000, False)])
        + _msg(_HEAD_BASE + 0x200, 3)
        + _head(_HEAD_BASE + 1, "DP-2", [(1920, 1080, 60000, True)])
        + _msg(_HEAD_BASE + 1, 9)
        + _msg(4, 1, _u(1))
    )
    comp = compositor(_globals((_MANAGER, 2)), reply)
    (head,) = wayland_outputs.read_heads_from(comp.client, 2.0)
    assert head.name == "DP-1"
    assert head.modes == (OutputMode(1920, 1080, 60000, True),)


def test_a_head_disabled_after_it_was_enabled_has_no_current_mode(compositor: Callable[..., _Compositor]) -> None:
    reply = _head(_HEAD_BASE, "DP-1", [(1920, 1080, 60000, True)]) + _msg(_HEAD_BASE, 4, _i(0)) + _msg(4, 1, _u(2))
    comp = compositor(_globals((_MANAGER, 2)), reply)
    (head,) = wayland_outputs.read_heads_from(comp.client, 2.0)
    assert head.enabled is False
    assert head.current_mode is None


def test_events_for_unknown_objects_and_unused_opcodes_are_ignored(compositor: Callable[..., _Compositor]) -> None:
    noise = _msg(2, 1, _u(9)) + _msg(1, 1, _u(5)) + _msg(0xFF00FFFF, 0, _u(1))
    head = _head(_HEAD_BASE, "DP-1", [(800, 600, 60000, False)])
    reply = noise + _msg(4, 9) + head + _msg(_HEAD_BASE + 0x100, 9) + _msg(4, 1, _u(1))
    comp = compositor(_globals((_MANAGER, 2)), reply)
    (head,) = wayland_outputs.read_heads_from(comp.client, 2.0)
    assert head.current_mode == OutputMode(800, 600, 60000, False)


def test_a_compositor_without_the_protocol_says_so(compositor: Callable[..., _Compositor]) -> None:
    comp = compositor(_globals(("wl_compositor", 6), ("wl_output", 4)))
    with pytest.raises(WaylandError, match="does not offer wlr-output-management"):
        wayland_outputs.read_heads_from(comp.client, 2.0)


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (_msg(1, 0, _u(2, 1) + _str("invalid version")), "reported error 1: invalid version"),
        (_msg(4, 2), "withdrew wlr-output-management"),
        (struct.pack("=II", 4, 4 << 16), r"malformed message from the compositor \(size 4\)"),
    ],
    ids=["display-error", "manager-finished", "malformed"],
)
def test_a_protocol_failure_is_one_clause(compositor: Callable[..., _Compositor], reply: bytes, message: str) -> None:
    comp = compositor(_globals((_MANAGER, 2)), reply)
    with pytest.raises(WaylandError, match=message):
        wayland_outputs.read_heads_from(comp.client, 2.0)


def test_a_silent_compositor_times_out(compositor: Callable[..., _Compositor]) -> None:
    comp = compositor(_globals((_MANAGER, 2)))
    with pytest.raises(WaylandError, match="did not answer in time"):
        wayland_outputs.read_heads_from(comp.client, 0.2)


def test_an_exhausted_budget_never_waits(compositor: Callable[..., _Compositor]) -> None:
    comp = compositor(_globals((_MANAGER, 2)))
    with pytest.raises(WaylandError, match="did not answer in time"):
        wayland_outputs.read_heads_from(comp.client, 0.0)


def test_a_closed_connection_says_so(compositor: Callable[..., _Compositor]) -> None:
    comp = compositor(b"")
    comp._server.shutdown(socket.SHUT_WR)
    with pytest.raises(WaylandError, match="closed the connection"):
        wayland_outputs.read_heads_from(comp.client, 2.0)


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, None),
        ({"WAYLAND_DISPLAY": "wayland-0"}, None),
        ({"WAYLAND_DISPLAY": "wayland-0", "XDG_RUNTIME_DIR": "/run/user/1000"}, Path("/run/user/1000/wayland-0")),
        ({"WAYLAND_DISPLAY": "/tmp/wl-sock", "XDG_RUNTIME_DIR": "/run/user/1000"}, Path("/tmp/wl-sock")),
    ],
    ids=["unset", "no-runtime-dir", "relative", "absolute"],
)
def test_socket_path_resolves_like_libwayland(environ: dict[str, str], expected: Path | None) -> None:
    assert wayland_outputs.socket_path(environ) == expected


def test_read_heads_reports_a_socket_it_cannot_reach(tmp_path: Path) -> None:
    with pytest.raises(WaylandError, match="cannot connect to"):
        wayland_outputs.read_heads(tmp_path / "absent", 1.0)


def test_read_heads_reads_through_the_socket_path() -> None:
    # A short directory: AF_UNIX paths are capped near 104 bytes on macOS.
    with tempfile.TemporaryDirectory(prefix="wl") as directory:
        path = Path(directory) / "s"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        listener.listen(1)
        holder: list[_Compositor] = []

        def accept() -> None:
            conn, _ = listener.accept()
            comp = _Compositor.__new__(_Compositor)
            comp.globals_reply, comp.bind_reply, comp.chunk, comp.requests = (
                _globals((_MANAGER, 2)),
                _msg(4, 1, _u(1)),
                0,
                [],
            )
            comp._server = conn
            holder.append(comp)
            comp._serve()

        thread = threading.Thread(target=accept, daemon=True)
        thread.start()
        try:
            assert wayland_outputs.read_heads(path, 2.0) == []
        finally:
            thread.join(2.0)
            listener.close()
            for comp in holder:
                comp._server.close()
