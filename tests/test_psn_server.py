# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""System tests for PsnServer – verifies real UDP packet emission (unicast).

Uses a bound sink socket on localhost so no multicast group is required.
The _recv_typed helper drains packets until the expected type arrives or
the 2-second wall-clock budget expires.
"""

from __future__ import annotations

import socket
import time

import pypsn
import pytest

from openfollow.psn.server import PsnServer

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_free_udp_port() -> int:
    """Return a currently-free UDP port number (best-effort)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _recv_typed(sock: socket.socket, expected_type: type, timeout: float = 2.0) -> object | None:
    """Receive packets until one of *expected_type* arrives, or the budget expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        sock.settimeout(max(0.05, remaining))
        try:
            data, _ = sock.recvfrom(4096)
        except TimeoutError:
            break
        try:
            parsed = pypsn.parse_psn_packet(data)
        except Exception:
            continue
        if isinstance(parsed, expected_type):
            return parsed
    return None


@pytest.fixture()
def udp_sink() -> socket.socket:
    """A bound UDP socket that acts as the unicast packet sink for PsnServer."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)
    sock.bind(("", 0))
    yield sock
    sock.close()


# ---------------------------------------------------------------------------
# Data-packet tests
# ---------------------------------------------------------------------------


def test_psn_server_data_packet_contains_marker_position(udp_sink: socket.socket) -> None:
    """Emitted data packet carries the exact position set on the marker."""
    port = udp_sink.getsockname()[1]
    with PsnServer(target_ip="127.0.0.1", port=port, mcast_ip=None) as server:
        server.add_marker(1, "T1").set_pos(1.0, 2.0, 3.0)
        packet = _recv_typed(udp_sink, pypsn.PsnDataPacket)

    assert packet is not None, "No PsnDataPacket received within 2 s"
    assert len(packet.trackers) == 1
    t = packet.trackers[0]
    assert t.tracker_id == 1
    assert t.pos.x == pytest.approx(1.0)
    assert t.pos.y == pytest.approx(2.0)
    assert t.pos.z == pytest.approx(3.0)


def test_psn_server_multiple_markers_in_data_packet(udp_sink: socket.socket) -> None:
    """All registered markers appear together in a single data packet."""
    port = udp_sink.getsockname()[1]
    with PsnServer(target_ip="127.0.0.1", port=port, mcast_ip=None) as server:
        server.add_marker(1, "T1").set_pos(1.0, 0.0, 0.0)
        server.add_marker(2, "T2").set_pos(0.0, 5.0, 0.0)
        packet = _recv_typed(udp_sink, pypsn.PsnDataPacket)

    assert packet is not None
    by_id = {t.tracker_id: t for t in packet.trackers}
    assert set(by_id) == {1, 2}
    assert by_id[1].pos.x == pytest.approx(1.0)
    assert by_id[2].pos.y == pytest.approx(5.0)


def test_psn_server_removed_marker_absent_from_data_packet(udp_sink: socket.socket) -> None:
    """A marker removed before start is not present in the emitted data packet."""
    port = udp_sink.getsockname()[1]
    server = PsnServer(target_ip="127.0.0.1", port=port, mcast_ip=None)
    server.add_marker(1, "T1").set_pos(1.0, 0.0, 0.0)
    server.add_marker(2, "T2").set_pos(0.0, 1.0, 0.0)
    server.remove_marker(2)

    with server:
        packet = _recv_typed(udp_sink, pypsn.PsnDataPacket)

    assert packet is not None
    ids = {t.tracker_id for t in packet.trackers}
    assert 1 in ids
    assert 2 not in ids


# ---------------------------------------------------------------------------
# Info-packet tests
# ---------------------------------------------------------------------------


def test_psn_server_info_packet_contains_system_and_marker_names(udp_sink: socket.socket) -> None:
    """Info packet carries the configured system name and registered marker name."""
    port = udp_sink.getsockname()[1]
    with PsnServer(
        system_name="TestRig",
        target_ip="127.0.0.1",
        port=port,
        mcast_ip=None,
        info_fps=60.0,  # high rate so an info packet arrives before the 2 s budget
    ) as server:
        server.add_marker(7, "Spot7")
        packet = _recv_typed(udp_sink, pypsn.PsnInfoPacket)

    assert packet is not None, "No PsnInfoPacket received within 2 s"
    # pypsn returns decoded strings or raw bytes depending on version
    name = packet.name.decode() if isinstance(packet.name, bytes) else packet.name
    assert name == "TestRig"
    assert len(packet.trackers) == 1
    marker_name = packet.trackers[0].tracker_name
    if isinstance(marker_name, bytes):
        marker_name = marker_name.decode()
    assert marker_name == "Spot7"


# ---------------------------------------------------------------------------
# Lifecycle / API tests
# ---------------------------------------------------------------------------


def test_psn_server_context_manager_starts_and_stops(udp_sink: socket.socket) -> None:
    """Context-manager usage starts the server and stops it on exit."""
    port = udp_sink.getsockname()[1]
    with PsnServer(target_ip="127.0.0.1", port=port, mcast_ip=None) as server:
        server.add_marker(1, "T1").set_pos(0.0, 0.0, 0.0)
        packet = _recv_typed(udp_sink, pypsn.PsnDataPacket)

    assert packet is not None
    # After __exit__ both threads should have stopped
    assert server._data_thread is None
    assert server._info_thread is None


def test_psn_server_frame_id_increments_and_wraps() -> None:
    """frame_id advances on each info build and rolls over at 256."""
    server = PsnServer(mcast_ip=None)
    server._frame_id = 254

    server._make_psn_info()  # consumes 254 → internal becomes 255
    server._make_psn_info()  # consumes 255 → internal becomes 0
    server._make_psn_info()  # consumes 0   → internal becomes 1

    assert server._frame_id == 1


def test_psn_server_get_marker_returns_registered_and_none_for_unknown() -> None:
    """get_marker returns the Marker object by ID or None when absent."""
    server = PsnServer(mcast_ip=None)
    server.add_marker(3, "T3")

    marker = server.get_marker(3)
    assert marker is not None
    assert marker.name == "T3"
    assert server.get_marker(99) is None


def test_psn_server_update_marker_name_renames_existing() -> None:
    """Live-rename path used by the marker catalog sync's ``_on_change``
    callback – a registered marker picks up the new name on the next PSN
    info packet."""
    server = PsnServer(mcast_ip=None)
    server.add_marker(7, "Old")
    assert server.update_marker_name(7, "New") is True
    assert server.get_marker(7).name == "New"


def test_psn_server_update_marker_name_returns_false_when_absent() -> None:
    """If the catalog learns about a marker the local station doesn't
    control (i.e. not in ``app._controlled_ids`` → not registered), the
    rename is a no-op."""
    server = PsnServer(mcast_ip=None)
    assert server.update_marker_name(99, "ghost") is False


# ---------------------------------------------------------------------------
# Interface-change recovery
# ---------------------------------------------------------------------------


def test_handle_send_error_starts_recovery_thread_on_transient_errno() -> None:
    """A transient OSError in _send must trigger a background recovery thread.

    We set up a minimal server instance, invoke ``_handle_send_error``
    directly with ENETUNREACH, and assert the side effects: broken
    exit_stack replaced, socket cleared, recovery thread started. We
    don't run the thread to completion – that would require a real
    multicast interface – we just verify the recovery state machine.
    """
    import contextlib
    import errno as _e

    from openfollow.psn.server import PsnServer

    server = PsnServer(mcast_ip="236.10.10.10")
    # Pretend we're mid-session with an old exit stack and a live socket.
    old_stack = contextlib.ExitStack()
    server._exit_stack = old_stack
    server._socket = object()  # type: ignore[assignment]

    # Block the recovery thread's actual work – we only care about the
    # bookkeeping here, not the multicast socket open.
    server._try_open_multicast_socket_once = lambda attempt: False  # type: ignore[assignment]

    server._handle_send_error(OSError(_e.ENETUNREACH, "transient"))

    try:
        assert server._socket is None, "broken socket must be cleared"
        assert server._exit_stack is not old_stack, "a fresh exit stack must be installed"
        assert server._socket_thread is not None
        assert server._socket_thread.is_alive()
    finally:
        server._stop_event.set()
        if server._socket_thread is not None:
            server._socket_thread.join(timeout=3.0)


def test_handle_send_error_rechecks_stop_event_under_lock(monkeypatch) -> None:
    """#541: if stop() sets the event after the pre-lock check but before the
    locked spawn block, the under-lock re-check must prevent spawning a recovery
    thread, leaving teardown to stop()."""
    import errno as _e

    from openfollow.psn.server import PsnServer

    server = PsnServer(mcast_ip="236.10.10.10")
    sentinel_socket = object()
    server._socket = sentinel_socket  # type: ignore[assignment]

    # is_set() returns False for the pre-lock check, then True for the under-lock
    # re-check – simulating stop() winning the race in between.
    calls = {"n": 0}

    def fake_is_set() -> bool:
        calls["n"] += 1
        return calls["n"] >= 2

    monkeypatch.setattr(server._stop_event, "is_set", fake_is_set)
    server._handle_send_error(OSError(_e.ENETUNREACH, "transient"))

    assert server._socket_thread is None  # no recovery thread spawned
    assert server._socket is sentinel_socket  # teardown left to stop()


def test_handle_send_error_ignores_non_transient_errno() -> None:
    """Non-interface errors (e.g. EACCES) must not trigger recovery."""
    import errno as _e

    from openfollow.psn.server import PsnServer

    server = PsnServer(mcast_ip="236.10.10.10")
    sentinel_socket = object()
    server._socket = sentinel_socket  # type: ignore[assignment]

    server._handle_send_error(OSError(_e.EACCES, "denied"))

    assert server._socket is sentinel_socket
    assert server._socket_thread is None


def test_handle_send_error_noop_in_unicast_mode() -> None:
    """Unicast mode (mcast_ip=None) must not spawn a multicast recovery thread.

    The recovery helper would otherwise call _try_open_multicast_socket_once
    with a falsy mcast_ip and permanently disable output.
    """
    import errno as _e

    from openfollow.psn.server import PsnServer

    server = PsnServer(mcast_ip=None, target_ip="127.0.0.1")
    sentinel_socket = object()
    server._socket = sentinel_socket  # type: ignore[assignment]

    server._handle_send_error(OSError(_e.EADDRNOTAVAIL, "iface gone"))

    assert server._socket is sentinel_socket, "unicast socket must not be cleared"
    assert server._socket_thread is None


def test_handle_send_error_noop_when_stopping() -> None:
    """Once stop_event is set, teardown owns the socket/exit-stack lifecycle, so
    a send failure must not spawn an orphaned recovery thread or swap the stack."""
    import errno as _e

    from openfollow.psn.server import PsnServer

    server = PsnServer(mcast_ip="236.10.10.10")
    sentinel_socket = object()
    server._socket = sentinel_socket  # type: ignore[assignment]
    server._stop_event.set()

    server._handle_send_error(OSError(_e.ENETUNREACH, "transient"))

    assert server._socket is sentinel_socket  # untouched
    assert server._socket_thread is None  # no recovery thread spawned
