# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Hermetic edge-case tests for ``openfollow.psn.receiver`` and ``server``.

The existing PSN server tests use real loopback UDP and are marked
``integration``. These tests exercise paths that are hard to reach via
the network – speed derivation with out-of-range dt, empty marker
sends, stop without start, the info/data early-return, and ignore-id
updates at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

import openfollow.psn.receiver as receiver_module
from openfollow.psn.receiver import PsnReceiver
from openfollow.psn.server import PsnServer

pytestmark = pytest.mark.unit


@dataclass
class _Vec:
    x: float
    y: float
    z: float


@dataclass
class _PacketTracker:
    """Mirror of ``pypsn.PsnTracker`` for the fake-packet path –
    pypsn's wire-protocol field names are ``tracker_id`` /
    ``trackers``, even though our domain layer translates these
    into ``marker_id`` / ``markers`` once the values cross the
    seam in :class:`PsnReceiver`."""

    tracker_id: int
    pos: _Vec | None
    speed: _Vec | None


class _FakeDataPacket:
    def __init__(self, trackers: list[_PacketTracker]) -> None:
        self.trackers = trackers


# ---------------------------------------------------------------------------
# PsnReceiver edge cases
# ---------------------------------------------------------------------------


def test_receiver_ignores_marker_without_position(monkeypatch) -> None:
    """Packets with ``pos is None`` must be silently dropped – not crash
    the callback or store a malformed marker entry."""
    monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
    monkeypatch.setattr(receiver_module.time, "monotonic", lambda: 10.0)

    receiver = PsnReceiver()
    receiver._on_packet(
        _FakeDataPacket([_PacketTracker(5, pos=None, speed=None)]),
    )
    assert receiver.get_marker(5) is None


def test_receiver_drops_update_when_dt_too_small(monkeypatch) -> None:
    """Two packets within 1 ms would yield absurd velocities if naively
    divided – the helper rejects the derivation and leaves speed at its
    last known value (zero here)."""
    monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
    # Two timestamps 0.0005 s apart.
    times = iter([1000.0, 1000.0005])
    monkeypatch.setattr(receiver_module.time, "monotonic", lambda: next(times))

    receiver = PsnReceiver()
    receiver._on_packet(
        _FakeDataPacket(
            [
                _PacketTracker(
                    5,
                    _Vec(0.0, 0.0, 0.0),
                    _Vec(0.0, 0.0, 0.0),
                )
            ]
        )
    )
    receiver._on_packet(
        _FakeDataPacket(
            [
                _PacketTracker(
                    5,
                    _Vec(1.0, 0.0, 0.0),
                    _Vec(0.0, 0.0, 0.0),
                )
            ]
        )
    )
    marker = receiver.get_marker(5)
    assert marker is not None
    # Speed stays zero because dt was too small.
    assert marker.speed == (0.0, 0.0, 0.0)


def test_receiver_drops_update_when_dt_too_large(monkeypatch) -> None:
    """Two packets >1 s apart → treat as a re-connect, not a motion
    update. Otherwise a long gap would produce a tiny bogus velocity."""
    monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
    times = iter([1000.0, 1005.0])  # 5 s gap
    monkeypatch.setattr(receiver_module.time, "monotonic", lambda: next(times))

    receiver = PsnReceiver()
    receiver._on_packet(
        _FakeDataPacket(
            [
                _PacketTracker(
                    5,
                    _Vec(0.0, 0.0, 0.0),
                    _Vec(0.0, 0.0, 0.0),
                )
            ]
        )
    )
    receiver._on_packet(
        _FakeDataPacket(
            [
                _PacketTracker(
                    5,
                    _Vec(1.0, 0.0, 0.0),
                    _Vec(0.0, 0.0, 0.0),
                )
            ]
        )
    )
    marker = receiver.get_marker(5)
    assert marker is not None
    assert marker.speed == (0.0, 0.0, 0.0)


def test_receiver_skips_derivation_when_position_unchanged(monkeypatch) -> None:
    """A stationary marker with zero protocol speed stays at zero –
    the helper must not update speed when dx == dy == dz == 0."""
    monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
    times = iter([1.0, 1.5])
    monkeypatch.setattr(receiver_module.time, "monotonic", lambda: next(times))

    receiver = PsnReceiver()
    for _ in range(2):
        receiver._on_packet(
            _FakeDataPacket(
                [
                    _PacketTracker(
                        5,
                        _Vec(2.0, 2.0, 2.0),
                        _Vec(0.0, 0.0, 0.0),
                    )
                ]
            )
        )
    marker = receiver.get_marker(5)
    assert marker is not None
    assert marker.speed == (0.0, 0.0, 0.0)


def test_receiver_is_marker_online_false_for_unknown_id() -> None:
    receiver = PsnReceiver()
    assert receiver.is_marker_online(42) is False


def test_receiver_set_ignore_ids_takes_effect_for_next_packet(monkeypatch) -> None:
    monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
    monkeypatch.setattr(receiver_module.time, "monotonic", lambda: 10.0)

    receiver = PsnReceiver()
    receiver.set_ignore_ids({7})
    receiver._on_packet(
        _FakeDataPacket(
            [
                _PacketTracker(7, _Vec(0.0, 0.0, 0.0), None),
                _PacketTracker(8, _Vec(0.0, 0.0, 0.0), None),
            ]
        )
    )
    assert receiver.get_marker(7) is None
    assert receiver.get_marker(8) is not None


def test_receiver_on_packet_rejects_non_data_packet() -> None:
    """The dispatch must drop anything that isn't a PsnDataPacket instance
    (info packets, stray bytes) without ever reaching the marker loop."""
    receiver = PsnReceiver()
    # A bare object is not a PsnDataPacket, so the isinstance check bails
    # before the attribute access that would crash on a non-packet.
    receiver._on_packet(object())
    receiver._on_packet(None)  # type: ignore[arg-type]
    assert receiver._markers == {}


def test_receiver_on_packet_swallows_unexpected_errors(monkeypatch) -> None:
    """Any exception inside the callback must be logged and swallowed so
    the receiver thread doesn't die."""
    monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)

    class _Evil:
        # isinstance()-compatible; iterating ``trackers`` explodes –
        # ``trackers`` matches pypsn's wire-attribute name (the
        # receiver reads ``data.trackers``).
        trackers = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))

    evil = _Evil()
    # Register the evil subclass as matching PsnDataPacket so isinstance passes.
    monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _Evil)
    receiver = PsnReceiver()
    receiver._on_packet(evil)  # Must not raise.


def test_receiver_stop_without_start_is_silent() -> None:
    receiver = PsnReceiver()
    receiver.stop()  # Must not raise even though _receiver is None.


# ---------------------------------------------------------------------------
# PsnServer edge cases
# ---------------------------------------------------------------------------


def test_server_send_is_noop_when_socket_none() -> None:
    """Without an open socket, _send must not touch the network and must
    not raise. This is the state between stop() and a subsequent start()."""
    server = PsnServer(mcast_ip=None)
    assert server._socket is None
    server._send(b"any")  # Must not raise.
    # send counters stay at zero.
    assert server._send_total == 0


def test_server_send_data_packet_is_noop_with_no_markers(monkeypatch) -> None:
    """An empty marker list must short-circuit *before* the prepare call
    – otherwise we'd emit a valid but empty packet every tick."""
    server = PsnServer(mcast_ip=None)
    prepared: list[bytes] = []

    def _fail_prepare(_packet):  # noqa: ANN202
        prepared.append(b"x")
        return b"x"

    import openfollow.psn.server as server_module

    monkeypatch.setattr(server_module.pypsn, "prepare_psn_data_packet_bytes", _fail_prepare)
    server._send_data_packet()
    assert prepared == []


def test_server_send_info_packet_is_noop_with_no_markers(monkeypatch) -> None:
    server = PsnServer(mcast_ip=None)
    prepared: list[bytes] = []
    import openfollow.psn.server as server_module

    monkeypatch.setattr(
        server_module.pypsn,
        "prepare_psn_info_packet_bytes",
        lambda _p: prepared.append(b"x") or b"x",
    )
    server._send_info_packet()
    assert prepared == []


def test_server_make_psn_info_advances_frame_id() -> None:
    server = PsnServer(mcast_ip=None)
    info1 = server._make_psn_info()
    info2 = server._make_psn_info()
    # The info object snapshots the *previous* frame_id before incrementing.
    assert info1.frame_id != info2.frame_id
    assert server._frame_id == 2


def test_server_send_counts_errors_and_suppresses_spam(monkeypatch) -> None:
    """First 5 errors log, subsequent ones are aggregated (every 100th)."""
    import errno

    server = PsnServer(mcast_ip=None, target_ip="127.0.0.1")

    class _BadSocket:
        def sendto(self, _d, _addr):  # noqa: ANN001, ANN201
            raise OSError(errno.EACCES, "denied")

    server._socket = _BadSocket()
    for _ in range(3):
        server._send(b"ping")
    assert server._send_total == 3
    assert server._send_errors == 3


def test_server_stop_without_start_is_silent() -> None:
    server = PsnServer(mcast_ip=None)
    server.stop()  # Must not raise – all threads/sockets are None.


def test_server_snapshot_markers_returns_new_list_each_call() -> None:
    """The snapshot must be a fresh list so callers can iterate safely
    even if another thread adds a marker mid-iteration."""
    server = PsnServer(mcast_ip=None)
    server.add_marker(1, "a")
    server.add_marker(2, "b")
    snap1 = server._snapshot_markers()
    snap2 = server._snapshot_markers()
    assert snap1 is not snap2
    assert {t.marker_id for t in snap1} == {1, 2}


def test_server_rebind_swaps_socket_and_preserves_markers(monkeypatch) -> None:
    """``PsnServer.rebind`` recycles the multicast TX socket bound to a
    new interface IP. Marker registrations live on ``self._markers``
    and are not touched by ``stop()``/``start()``, so they survive the
    cycle. Companion to ``PsnReceiver.rebind``; PSN input + output must
    stay bound to the same interface."""
    from unittest.mock import MagicMock

    instances: list[MagicMock] = []

    def _fake_mcast_socket(*_a, **_kw) -> MagicMock:
        sock = MagicMock(name=f"McastTxSocket-{len(instances)}")
        instances.append(sock)
        return sock

    import openfollow.psn.server as server_module

    monkeypatch.setattr(
        server_module.multicast_expert,
        "McastTxSocket",
        _fake_mcast_socket,
    )

    server = PsnServer(mcast_ip="236.10.10.10", source_ip="10.0.0.1")
    server.add_marker(1, "T1")
    server.add_marker(2, "T2")
    server.start()
    first_count = len(instances)

    server.rebind("192.168.1.5")

    assert len(instances) == first_count + 1
    assert server._source_ip == "192.168.1.5"
    # Marker registrations survive the cycle.
    assert server.get_marker(1) is not None
    assert server.get_marker(2) is not None
    server.stop()


def test_receiver_rebind_raises_when_start_silently_disables_input(monkeypatch) -> None:
    """``PsnReceiver.start()`` swallows ``OSError`` and leaves
    ``_receiver`` as ``None`` so a bad ``source_ip`` at startup doesn't
    take down the rest of the app. ``rebind()`` must NOT inherit that
    silence – it has to surface the failure to the live-apply caller
    so ``_apply_with_fallback`` can degrade visibly. Otherwise PSN
    input gets disabled and the operator sees no signal."""
    monkeypatch.setattr(
        receiver_module,
        "_RobustReceiver",
        lambda **kw: (_ for _ in ()).throw(OSError(99, "address not assignable")),
    )

    receiver = PsnReceiver(source_ip="10.0.0.1")

    with pytest.raises(OSError, match="failed to bind"):
        receiver.rebind("192.168.99.99")


def test_receiver_init_strips_whitespace_from_source_ip() -> None:
    """``AppConfig.psn_source_ip`` is not stripped during TOML load,
    so a hand-edited ``"192.168.1.5 "`` would otherwise be treated as
    a real interface IP and break the bind. The receiver normalises
    its own copy so downstream socket code never sees the whitespace."""
    receiver = PsnReceiver(source_ip="  192.168.1.5  ")
    assert receiver._source_ip == "192.168.1.5"


def test_receiver_rebind_strips_whitespace_from_source_ip(monkeypatch) -> None:
    """Same whitespace contract applies to live ``rebind`` – a
    web-form / TOML round-trip that passes whitespace must not break
    the bind nor mis-compare against the normalised stored value on
    the next reload pass."""

    captured: list[str] = []

    class _StubRecvThread:
        def __init__(self, *, callback, ip_addr: str, mcast_port: int) -> None:
            captured.append(ip_addr)
            self.daemon = False

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

    monkeypatch.setattr(receiver_module, "_RobustReceiver", _StubRecvThread)

    receiver = PsnReceiver(source_ip="10.0.0.1")
    receiver.rebind("  192.168.1.5  ")

    assert receiver._source_ip == "192.168.1.5"
    # The constructor for the new receiver thread saw the stripped IP.
    assert captured[-1] == "192.168.1.5"


def test_server_rebind_raises_when_multicast_bind_fails(monkeypatch) -> None:
    """Mirror of ``OtpServer.restart`` fail-fast: ``PsnServer.start()``
    swallows multicast-bind failures and spawns a daemon retry
    thread. ``rebind`` must observe the post-start ``_socket is None``
    and raise so the dispatcher's ``_apply_with_fallback`` can revert
    config and surface the failure. Without this, a bad live
    ``psn_source_ip`` silently disables PSN output."""
    import openfollow.psn.server as server_module

    def _failing_mcast(*_a, **_kw):
        raise OSError("network unreachable")

    monkeypatch.setattr(
        server_module.multicast_expert,
        "McastTxSocket",
        _failing_mcast,
    )

    server = PsnServer(mcast_ip="236.10.10.10", source_ip="10.0.0.1")

    with pytest.raises(OSError, match="failed to open multicast socket"):
        server.rebind("10.99.99.99")

    # Retry thread / worker threads were torn back down before the raise.
    assert server._data_thread is None
    assert server._info_thread is None
