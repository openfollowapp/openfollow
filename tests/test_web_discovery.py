# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for ``openfollow.web.discovery``.

Covers beacon packet encode/decode + validation, peer-table pruning and cap
handling, and the BeaconSender / BeaconReceiver socket loops (open / bind /
multicast-join failure recovery, self-filtering, and health properties).
"""

from __future__ import annotations

import json
import time

import pytest

import openfollow.web.discovery as discovery_module
from openfollow.web.discovery import (
    BEACON_NAME_MAX_LEN,
    BEACON_VERSION_MAX_LEN,
    MAX_PEERS,
    BeaconPacket,
    BeaconReceiver,
    PeerInfo,
)

pytestmark = pytest.mark.unit


def _beacon_bytes(**overrides: object) -> bytes:
    """Helper: build a beacon datagram with a base valid payload + overrides."""
    payload: dict[str, object] = {
        "type": "openfollow",
        "name": "Node",
        "web_port": 80,
        "version": "0.1.0",
    }
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


def test_beacon_packet_roundtrip_and_type_filtering() -> None:
    packet = BeaconPacket(name="Node A", web_port=9000, version="1.2.3")
    decoded = BeaconPacket.from_bytes(packet.to_bytes())

    assert decoded is not None
    assert decoded.name == "Node A"
    assert decoded.web_port == 9000
    assert decoded.version == "1.2.3"

    bad = BeaconPacket.from_bytes(b'{"type":"other","name":"x"}')
    assert bad is None


@pytest.mark.parametrize("port", [0, -1, 65536, 70000])
def test_beacon_packet_rejects_out_of_range_port(port: int) -> None:
    assert BeaconPacket.from_bytes(_beacon_bytes(web_port=port)) is None


@pytest.mark.parametrize("port", ["80", None, [], {}, 3.14])
def test_beacon_packet_rejects_non_int_port(port: object) -> None:
    assert BeaconPacket.from_bytes(_beacon_bytes(web_port=port)) is None


@pytest.mark.parametrize("port", [True, False])
def test_beacon_packet_rejects_bool_port(port: bool) -> None:
    # bool is an int subclass in Python; must be rejected explicitly so a
    # crafted {"web_port": true} can't land as port=1.
    assert BeaconPacket.from_bytes(_beacon_bytes(web_port=port)) is None


def test_beacon_packet_rejects_non_dict_json() -> None:
    assert BeaconPacket.from_bytes(b"[]") is None
    assert BeaconPacket.from_bytes(b'"openfollow"') is None
    assert BeaconPacket.from_bytes(b"null") is None


def test_beacon_packet_caps_name_length() -> None:
    oversized = "x" * (BEACON_NAME_MAX_LEN + 50)
    decoded = BeaconPacket.from_bytes(_beacon_bytes(name=oversized))
    assert decoded is not None
    assert len(decoded.name) == BEACON_NAME_MAX_LEN


def test_beacon_packet_caps_version_length() -> None:
    oversized = "v" * (BEACON_VERSION_MAX_LEN + 50)
    decoded = BeaconPacket.from_bytes(_beacon_bytes(version=oversized))
    assert decoded is not None
    assert len(decoded.version) == BEACON_VERSION_MAX_LEN


def test_beacon_packet_strips_control_chars_in_name() -> None:
    decoded = BeaconPacket.from_bytes(_beacon_bytes(name="hello\x00\x07\n\x1b[31mX"))
    assert decoded is not None
    # isprintable() drops NUL, BEL, LF, and the ESC that starts an ANSI
    # escape sequence; the trailing "[31mX" is printable and kept.
    assert decoded.name == "hello[31mX"


def test_beacon_packet_coerces_non_string_name_to_empty() -> None:
    decoded = BeaconPacket.from_bytes(_beacon_bytes(name=42))
    assert decoded is not None
    assert decoded.name == ""


def test_beacon_receiver_prunes_stale_peers() -> None:
    receiver = BeaconReceiver()
    now = time.time()
    receiver._peers = {
        "old:80": PeerInfo("Old", "10.0.0.10", 80, "0.1.0", now - 60.0),
        "new:80": PeerInfo("New", "10.0.0.11", 80, "0.1.0", now),
    }

    peers = receiver.get_peers()

    assert len(peers) == 1
    assert peers[0].name == "New"


def _fill_peers(receiver: BeaconReceiver, count: int, *, last_seen: float) -> None:
    receiver._peers = {
        f"10.0.{i // 256}.{i % 256}:80": PeerInfo(f"P{i}", f"10.0.{i // 256}.{i % 256}", 80, "0.1.0", last_seen)
        for i in range(count)
    }


def test_handle_packet_refuses_new_peer_when_table_full_of_live_peers() -> None:
    discovered: list[PeerInfo] = []
    receiver = BeaconReceiver(on_peer_discovered=discovered.append)
    _fill_peers(receiver, MAX_PEERS, last_seen=time.time())

    receiver._handle_packet(_beacon_bytes(web_port=9999), "192.168.1.1")

    # Live table at the cap: the new peer is dropped, not inserted, and no
    # discovery callback fires for it.
    assert len(receiver._peers) == MAX_PEERS
    assert "192.168.1.1:9999" not in receiver._peers
    assert discovered == []


def test_handle_packet_prunes_stale_then_inserts_when_table_full() -> None:
    receiver = BeaconReceiver()
    _fill_peers(receiver, MAX_PEERS, last_seen=time.time() - 100.0)  # all stale

    receiver._handle_packet(_beacon_bytes(web_port=9999), "192.168.1.1")

    # Stale entries are pruned at insert time, freeing room for the new peer.
    assert "192.168.1.1:9999" in receiver._peers
    assert len(receiver._peers) == 1


def test_handle_packet_cap_warning_is_throttled(caplog) -> None:
    receiver = BeaconReceiver()
    _fill_peers(receiver, MAX_PEERS, last_seen=time.time())

    with caplog.at_level("WARNING", logger="openfollow.web.discovery"):
        receiver._handle_packet(_beacon_bytes(web_port=1111), "192.168.1.1")
        receiver._handle_packet(_beacon_bytes(web_port=2222), "192.168.1.2")

    warnings = [r for r in caplog.records if "peer table full" in r.message]
    assert len(warnings) == 1  # second refusal within 30s is throttled


def test_handle_packet_cap_warning_fires_near_boot(caplog, monkeypatch) -> None:
    # time.monotonic() can be below the 30 s throttle window shortly after
    # boot; the first table-full warning must still fire (it was suppressed
    # when the throttle timestamp initialised to 0.0).
    monkeypatch.setattr(discovery_module.time, "monotonic", lambda: 5.0)
    receiver = BeaconReceiver()
    _fill_peers(receiver, MAX_PEERS, last_seen=time.time())

    with caplog.at_level("WARNING", logger="openfollow.web.discovery"):
        receiver._handle_packet(_beacon_bytes(web_port=1111), "192.168.1.1")

    warnings = [r for r in caplog.records if "peer table full" in r.message]
    assert len(warnings) == 1


def test_is_local_does_not_cache_timestamp_on_lookup_failure(monkeypatch) -> None:
    receiver = BeaconReceiver()
    receiver.set_local_port(80)

    def _boom() -> set[str]:
        raise OSError("no interfaces")

    monkeypatch.setattr(discovery_module, "get_local_ipv4_addresses", _boom)
    # Failed lookup must not advance the cache timestamp (else the empty set is
    # trusted for the whole TTL and this host self-lists as a peer).
    assert receiver._is_local("10.0.0.1", 80) is False
    assert receiver._local_ips_ts == 0.0

    # The next packet retries; a successful refresh caches and stamps it.
    monkeypatch.setattr(discovery_module, "get_local_ipv4_addresses", lambda: {"10.0.0.1"})
    monkeypatch.setattr(discovery_module.time, "time", lambda: 123.0)
    assert receiver._is_local("10.0.0.1", 80) is True
    assert receiver._local_ips_ts == 123.0


def test_beacon_receiver_detects_local_packets_with_iface_ip(monkeypatch) -> None:
    receiver = BeaconReceiver(iface_ip="10.0.0.9")
    receiver.set_local_port(80)

    monkeypatch.setattr(discovery_module, "get_local_ipv4_addresses", lambda: {"10.0.0.1"})
    monkeypatch.setattr(discovery_module.time, "time", lambda: 100.0)

    assert receiver._is_local("10.0.0.1", 80) is True
    assert receiver._is_local("10.0.0.9", 80) is True
    assert receiver._is_local("10.0.0.9", 9090) is False


# ---------------------------------------------------------------------------
# BeaconSender resilience across interface changes
# ---------------------------------------------------------------------------


def test_beacon_sender_survives_transient_send_error_and_rebuilds_socket(monkeypatch) -> None:
    import errno as _errno

    from openfollow.web.discovery import BeaconSender

    # Shrink the inter-beacon delay so the test rebuilds within one
    # polling window without trading off coverage.
    monkeypatch.setattr(discovery_module, "BEACON_INTERVAL", 0.05)

    sender = BeaconSender(name="Node", web_port=80, version="0.1.0", iface_ip="")

    # Two pretend sockets: the first fails once then is closed; the second
    # succeeds. _open_socket hands them out in order.
    first = _FakeSocket(fail_errno=_errno.ENETUNREACH, fail_times=1)
    second = _FakeSocket()
    sockets = iter([first, second])

    sender._open_socket = lambda: next(sockets)  # type: ignore[assignment]

    sender.start()
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if second.send_count >= 1:
                break
            time.sleep(0.01)
        assert second.send_count >= 1, "sender failed to recover"
        assert first.closed, "broken socket must be closed before rebuild"
        assert sender._thread is not None and sender._thread.is_alive()
    finally:
        sender.stop()


def test_beacon_sender_thread_survives_initial_open_socket_failure(monkeypatch) -> None:
    from openfollow.web.discovery import BeaconSender

    monkeypatch.setattr(discovery_module, "BEACON_INTERVAL", 0.05)

    sender = BeaconSender(name="Node", web_port=80, version="0.1.0", iface_ip="")

    # First call: blow up on socket creation. Second call: hand back a working
    # stub. _open_socket is invoked lazily whenever sock is None, so the
    # thread must be resilient across both code paths.
    attempts = {"n": 0}
    good = _FakeSocket()

    def _flaky_open():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise OSError("network unreachable")
        return good

    sender._open_socket = _flaky_open  # type: ignore[assignment]

    sender.start()
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if good.send_count >= 1:
                break
            time.sleep(0.01)
        assert attempts["n"] >= 2, "sender must retry after an open failure"
        assert good.send_count >= 1, "sender must recover and start sending"
        assert sender._thread is not None and sender._thread.is_alive()
    finally:
        sender.stop()


def test_beacon_sender_thread_survives_unexpected_exception(monkeypatch) -> None:
    from openfollow.web.discovery import BeaconSender

    monkeypatch.setattr(discovery_module, "BEACON_INTERVAL", 0.05)

    sender = BeaconSender(name="Node", web_port=80, version="0.1.0", iface_ip="")

    raised = {"count": 0}

    class _RaisingSocket:
        def __init__(self) -> None:
            self.closed = False
            self.send_count = 0

        def sendto(self, data: bytes, addr: tuple) -> None:
            if raised["count"] < 1:
                raised["count"] += 1
                raise RuntimeError("boom – unexpected bug")
            self.send_count += 1

        def close(self) -> None:
            self.closed = True

        def setsockopt(self, *a: object, **kw: object) -> None:
            pass

    sock = _RaisingSocket()
    sender._open_socket = lambda: sock  # type: ignore[assignment]

    sender.start()
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if sock.send_count >= 1:
                break
            time.sleep(0.01)
        assert raised["count"] == 1
        assert sock.send_count >= 1, "thread must survive unexpected exception"
        assert sender._thread is not None and sender._thread.is_alive()
    finally:
        sender.stop()


class _FakeSocket:
    """Minimal socket double used by the recovery tests above."""

    def __init__(self, fail_errno: int | None = None, fail_times: int = 0) -> None:
        self._fail_errno = fail_errno
        self._remaining_failures = fail_times
        self.closed = False
        self.send_count = 0

    def sendto(self, data: bytes, addr: tuple) -> None:
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise OSError(self._fail_errno, "simulated")
        self.send_count += 1

    def close(self) -> None:
        self.closed = True

    def setsockopt(self, *a: object, **kw: object) -> None:
        pass


# ---------------------------------------------------------------------------
# PeerInfo dataclass properties
# ---------------------------------------------------------------------------


def test_peer_info_address_is_ip_port_string() -> None:
    peer = PeerInfo(name="A", ip="10.1.2.3", web_port=8080, version="0.1.0", last_seen=0.0)
    assert peer.address == "10.1.2.3:8080"


def test_peer_info_is_online_true_when_last_seen_is_recent(monkeypatch) -> None:
    monkeypatch.setattr(discovery_module.time, "time", lambda: 100.0)
    peer = PeerInfo(name="A", ip="10.0.0.1", web_port=80, version="0.1.0", last_seen=99.0)
    assert peer.is_online is True


def test_peer_info_is_online_false_when_last_seen_is_old(monkeypatch) -> None:
    # PEER_TIMEOUT is 6.0s; fake a "now" well beyond that.
    monkeypatch.setattr(discovery_module.time, "time", lambda: 200.0)
    peer = PeerInfo(name="A", ip="10.0.0.1", web_port=80, version="0.1.0", last_seen=100.0)
    assert peer.is_online is False


# ---------------------------------------------------------------------------
# BeaconPacket.from_bytes – UTF-8 / JSON decode failure branches
# ---------------------------------------------------------------------------


def test_beacon_packet_rejects_non_utf8_bytes() -> None:
    assert BeaconPacket.from_bytes(b"\xff\xfe\xfd") is None


def test_beacon_packet_rejects_non_json_utf8() -> None:
    assert BeaconPacket.from_bytes(b"not json at all") is None


def test_beacon_packet_rejects_empty_bytes() -> None:
    assert BeaconPacket.from_bytes(b"") is None


# ---------------------------------------------------------------------------
# BeaconSender simple state transitions
# ---------------------------------------------------------------------------


def test_beacon_sender_update_name_mutates_packet() -> None:
    from openfollow.web.discovery import BeaconSender

    sender = BeaconSender(name="Old", web_port=80)
    sender.update_name("New")
    assert sender._packet.name == "New"


def test_beacon_sender_double_start_is_noop(monkeypatch) -> None:
    from openfollow.web.discovery import BeaconSender

    sender = BeaconSender(name="X", web_port=80)
    sender._run = lambda: None  # type: ignore[assignment]
    sender.start()
    first_thread = sender._thread
    sender.start()  # no-op
    assert sender._thread is first_thread
    sender.stop()


def test_beacon_sender_stop_without_start_is_noop() -> None:
    from openfollow.web.discovery import BeaconSender

    sender = BeaconSender(name="X", web_port=80)
    # No thread yet – must not raise or block on a missing thread join.
    sender.stop()
    assert sender._thread is None


# ---------------------------------------------------------------------------
# BeaconSender._open_socket – iface_ip branch and OSError fallback
# ---------------------------------------------------------------------------


def test_beacon_sender_open_socket_binds_to_configured_iface(monkeypatch) -> None:
    """With a configured iface_ip, _open_socket must setsockopt IP_MULTICAST_IF."""
    from openfollow.web.discovery import BeaconSender

    setsockopt_calls: list[tuple] = []

    class _FakeSock:
        def setsockopt(self, *a):
            setsockopt_calls.append(a)

    import socket as _sock_mod

    monkeypatch.setattr(
        discovery_module.socket,
        "socket",
        lambda *a, **kw: _FakeSock(),
    )

    sender = BeaconSender(name="X", web_port=80, iface_ip="10.0.0.5")
    sock = sender._open_socket()
    assert sock is not None
    # One call sets TTL, another sets IP_MULTICAST_IF with the inet_aton of 10.0.0.5.
    bound_iface_opts = [c for c in setsockopt_calls if len(c) == 3 and c[1] == _sock_mod.IP_MULTICAST_IF]
    assert bound_iface_opts, f"IP_MULTICAST_IF not set: {setsockopt_calls}"
    assert bound_iface_opts[0][2] == _sock_mod.inet_aton("10.0.0.5")


def test_beacon_sender_open_socket_falls_back_when_iface_unavailable(monkeypatch, caplog) -> None:
    import socket as _sock_mod

    from openfollow.web.discovery import BeaconSender

    class _FakeSock:
        def setsockopt(self, *a):
            # Fail only on IP_MULTICAST_IF; allow the TTL call.
            if len(a) == 3 and a[1] == _sock_mod.IP_MULTICAST_IF:
                raise OSError("iface vanished")

    monkeypatch.setattr(
        discovery_module.socket,
        "socket",
        lambda *a, **kw: _FakeSock(),
    )

    sender = BeaconSender(name="X", web_port=80, iface_ip="10.0.0.5")
    with caplog.at_level("WARNING", logger="openfollow.web.discovery"):
        sock = sender._open_socket()
    assert sock is not None
    assert any("not available" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# BeaconReceiver – start/stop thread lifecycle, _handle_packet, _is_local
# ---------------------------------------------------------------------------


def test_beacon_receiver_start_spawns_single_thread(monkeypatch) -> None:
    receiver = BeaconReceiver()
    receiver._run = lambda: None  # type: ignore[assignment]
    receiver.start()
    first = receiver._thread
    assert first is not None
    receiver.start()  # no-op on second call
    assert receiver._thread is first
    receiver.stop()
    assert receiver._thread is None


def test_beacon_receiver_stop_without_start_is_noop() -> None:
    receiver = BeaconReceiver()
    receiver.stop()
    assert receiver._thread is None


def test_beacon_receiver_handle_packet_adds_new_peer() -> None:
    added: list[PeerInfo] = []
    receiver = BeaconReceiver(on_peer_discovered=added.append)
    data = BeaconPacket(name="Other", web_port=9000, version="0.2.0").to_bytes()

    receiver._handle_packet(data, "10.0.0.42")

    peers = receiver.get_peers()
    assert len(peers) == 1
    assert peers[0].name == "Other"
    assert peers[0].ip == "10.0.0.42"
    assert peers[0].web_port == 9000
    assert added == peers  # callback fired once for the new peer


def test_beacon_receiver_handle_packet_ignores_malformed_data() -> None:
    """Invalid beacons must not mutate peer state or crash."""
    receiver = BeaconReceiver()
    receiver._handle_packet(b"garbage", "10.0.0.42")
    assert receiver.get_peers() == []


def test_beacon_receiver_handle_packet_skips_self_emitted_beacons(monkeypatch) -> None:
    """A beacon that resolves to our own IP and port must not self-enroll."""
    monkeypatch.setattr(
        discovery_module,
        "get_local_ipv4_addresses",
        lambda: {"10.0.0.42"},
    )
    receiver = BeaconReceiver()
    receiver.set_local_port(80)

    data = BeaconPacket(name="Self", web_port=80).to_bytes()
    receiver._handle_packet(data, "10.0.0.42")

    assert receiver.get_peers() == []


def test_beacon_receiver_handle_packet_suppresses_callback_for_existing_peer() -> None:
    """The on_peer_discovered callback fires only on the first beacon from a peer."""
    added: list[PeerInfo] = []
    receiver = BeaconReceiver(on_peer_discovered=added.append)
    data = BeaconPacket(name="P", web_port=8080).to_bytes()

    receiver._handle_packet(data, "10.0.0.5")
    receiver._handle_packet(data, "10.0.0.5")

    assert len(added) == 1  # still just the initial discovery


def test_beacon_receiver_is_local_returns_false_when_no_port_set() -> None:
    receiver = BeaconReceiver()
    # _local_port starts unset; every ip must be "not local" so no beacon is dropped.
    assert receiver._is_local("10.0.0.1", 80) is False


def test_beacon_receiver_is_local_survives_get_local_ipv4_raising(monkeypatch) -> None:

    def _raise() -> set[str]:
        raise OSError("netifaces failed")

    monkeypatch.setattr(discovery_module, "get_local_ipv4_addresses", _raise)
    receiver = BeaconReceiver(iface_ip="10.0.0.9")
    receiver.set_local_port(80)
    # Even though lookup failed, the iface_ip itself is still treated as local.
    assert receiver._is_local("10.0.0.9", 80) is True
    # Non-matching port must still be non-local regardless of lookup failure.
    assert receiver._is_local("10.0.0.9", 9999) is False


# ---------------------------------------------------------------------------
# BeaconReceiver._run – full socket loop with injected fake
# ---------------------------------------------------------------------------


class _FakeRecvSocket:
    """Socket double for BeaconReceiver._run tests.

    Yields a single valid beacon, then loops on timeout so the stop flag
    can break the loop.
    """

    def __init__(self, datagrams: list[tuple[bytes, tuple[str, int]]]) -> None:
        self._datagrams = list(datagrams)
        self.closed = False
        self.setsockopt_calls: list[tuple] = []
        self.bound_to: tuple[str, int] | None = None
        self.timeout_set: float | None = None
        self.membership_failures = 0

    def setsockopt(self, *a) -> None:
        self.setsockopt_calls.append(a)

    def bind(self, addr: tuple) -> None:
        self.bound_to = addr

    def settimeout(self, t: float) -> None:
        self.timeout_set = t

    def close(self) -> None:
        self.closed = True

    def recvfrom(self, bufsize: int) -> tuple[bytes, tuple[str, int]]:
        if self._datagrams:
            return self._datagrams.pop(0)
        raise discovery_module.socket.timeout("idle")


def test_beacon_receiver_run_delivers_datagrams_to_handle_packet(monkeypatch) -> None:
    """End-to-end _run: one valid datagram arrives, _handle_packet records it,
    the timeout branch then lets the stop flag terminate the loop.
    """
    discovered: list[PeerInfo] = []
    receiver = BeaconReceiver(on_peer_discovered=discovered.append)

    beacon = BeaconPacket(name="Far", web_port=8080).to_bytes()
    fake = _FakeRecvSocket([(beacon, ("10.0.0.77", 50505))])

    monkeypatch.setattr(
        discovery_module.socket,
        "socket",
        lambda *a, **kw: fake,
    )

    # Stop the loop after the first timeout branch.
    original_timeout_handler = fake.recvfrom

    def _recv_then_stop(bufsize: int):
        try:
            return original_timeout_handler(bufsize)
        finally:
            receiver._stop_event.set()

    fake.recvfrom = _recv_then_stop  # type: ignore[assignment]

    receiver._run()

    assert fake.closed is True
    assert fake.bound_to == ("", discovery_module.BEACON_PORT)
    assert len(discovered) == 1
    assert discovered[0].ip == "10.0.0.77"
    assert discovered[0].web_port == 8080


def test_beacon_receiver_run_falls_back_to_all_iface_on_bind_error(monkeypatch, caplog) -> None:
    receiver = BeaconReceiver(iface_ip="10.0.0.5")

    import socket as _sock_mod

    class _FlakyMembershipSocket(_FakeRecvSocket):
        def setsockopt(self, *a) -> None:
            # First IP_ADD_MEMBERSHIP call fails; second (fallback) succeeds.
            if len(a) == 3 and a[1] == _sock_mod.IP_ADD_MEMBERSHIP:
                self.membership_failures += 1
                if self.membership_failures == 1:
                    raise OSError("iface not available")
            super().setsockopt(*a)

    fake = _FlakyMembershipSocket([])

    def _stop_after_timeout(bufsize: int):
        receiver._stop_event.set()
        raise discovery_module.socket.timeout()

    fake.recvfrom = _stop_after_timeout  # type: ignore[assignment]

    monkeypatch.setattr(
        discovery_module.socket,
        "socket",
        lambda *a, **kw: fake,
    )

    with caplog.at_level("WARNING", logger="openfollow.web.discovery"):
        receiver._run()

    assert fake.membership_failures == 2  # first failed, second succeeded
    assert fake.closed is True
    assert any("not available" in rec.message for rec in caplog.records)


def test_beacon_receiver_run_retries_when_bind_fails(monkeypatch, caplog) -> None:
    """A held bind (another process owns the port) used to propagate out of
    _run and kill the daemon thread silently. It must now be logged and the
    socket setup retried on the next interval."""
    receiver = BeaconReceiver()
    monkeypatch.setattr(discovery_module, "BEACON_INTERVAL", 0.0)

    class _BindFailSocket(_FakeRecvSocket):
        def bind(self, addr: tuple) -> None:
            raise OSError("address already in use")

    fake = _BindFailSocket([])

    def _factory(*a, **kw):
        # Stop the retry loop after the first failed setup so the test
        # doesn't spin forever.
        receiver._stop_event.set()
        return fake

    monkeypatch.setattr(discovery_module.socket, "socket", _factory)

    with caplog.at_level("ERROR", logger="openfollow.web.discovery"):
        receiver._run()

    assert fake.closed is True  # setup failure still closes the half-built socket
    assert any("socket setup failed" in r.message for r in caplog.records)


def test_beacon_receiver_run_throttles_repeated_setup_failures(monkeypatch, caplog) -> None:
    """Under a persistent setup failure (here: both the primary and fallback
    multicast joins fail every attempt), the first 3 failures log, then the
    error log is throttled so a sustained outage can't spam the journal."""
    receiver = BeaconReceiver(iface_ip="10.0.0.5")
    monkeypatch.setattr(discovery_module, "BEACON_INTERVAL", 0.0)

    import socket as _sock_mod

    class _JoinFailSocket(_FakeRecvSocket):
        def setsockopt(self, *a) -> None:
            if len(a) == 3 and a[1] == _sock_mod.IP_ADD_MEMBERSHIP:
                raise OSError("no joinable iface")
            super().setsockopt(*a)

    attempts = {"n": 0}

    def _factory(*a, **kw):
        attempts["n"] += 1
        if attempts["n"] >= 5:
            receiver._stop_event.set()
        return _JoinFailSocket([])

    monkeypatch.setattr(discovery_module.socket, "socket", _factory)

    with caplog.at_level("ERROR", logger="openfollow.web.discovery"):
        receiver._run()

    errors = [r for r in caplog.records if "socket setup failed" in r.message]
    assert len(errors) == 3  # failures 1-3 logged; 4-5 throttled


# ---------------------------------------------------------------------------
# BeaconSender – remaining _open_socket / _run branch coverage
# ---------------------------------------------------------------------------


def test_beacon_sender_open_socket_without_iface_skips_multicast_if(monkeypatch) -> None:
    import socket as _sock_mod

    from openfollow.web.discovery import BeaconSender

    setsockopt_calls: list[tuple] = []

    class _FakeSock:
        def setsockopt(self, *a):
            setsockopt_calls.append(a)

    monkeypatch.setattr(
        discovery_module.socket,
        "socket",
        lambda *a, **kw: _FakeSock(),
    )

    sender = BeaconSender(name="X", web_port=80, iface_ip="")
    sock = sender._open_socket()
    assert sock is not None
    assert not any(c[1] == _sock_mod.IP_MULTICAST_IF for c in setsockopt_calls if len(c) == 3)


def test_beacon_sender_run_logs_initial_open_failures_with_rate_limit(monkeypatch) -> None:
    """After three straight open failures, the fourth must stay silent until
    30s elapse – i.e. the "consecutive_errors <= 3 or (now - last_error_log) >= 30"
    predicate flips to False (the skip-log branch).
    """
    from openfollow.web.discovery import BeaconSender

    calls = {"open": 0, "wait": 0, "warns": 0}

    def _always_fail() -> None:
        calls["open"] += 1
        raise OSError("boom")

    sender = BeaconSender(name="X", web_port=80)
    sender._open_socket = _always_fail  # type: ignore[assignment]

    # Freeze monotonic so (now - last_error_log) stays 0 and the rate-limit
    # branch short-circuits.
    monkeypatch.setattr(discovery_module.time, "monotonic", lambda: 1000.0)

    def _record_warning(msg, *args, **kwargs):
        calls["warns"] += 1

    # Patch the module-level logger instance only – replacing the class
    # method (``Logger.warning``) would leak into every logger for the
    # duration of the test.
    monkeypatch.setattr(discovery_module.logger, "warning", _record_warning)

    # Make wait() short-circuit and flip the stop flag after 5 iterations.
    def _fake_wait(timeout: float) -> bool:
        calls["wait"] += 1
        if calls["wait"] >= 5:
            sender._stop_event.set()
        return False

    sender._stop_event.wait = _fake_wait  # type: ignore[assignment]

    sender._run()

    # 5 open-failure iterations. First three warned (<=3), the next two are
    # suppressed because the monotonic clock is frozen so they fail the
    # "30s since last log" check too – the skip-log branch.
    assert calls["open"] == 5
    assert calls["warns"] == 3


def test_beacon_sender_run_non_transient_oserror_keeps_socket(monkeypatch) -> None:
    from openfollow.web.discovery import BeaconSender

    class _StableFailingSock:
        def __init__(self) -> None:
            self.send_attempts = 0
            self.closed = 0

        def setsockopt(self, *a) -> None:
            pass

        def sendto(self, data: bytes, addr) -> None:
            self.send_attempts += 1
            exc = OSError("permission denied")
            exc.errno = 1  # EPERM: not in the transient set
            raise exc

        def close(self) -> None:
            self.closed += 1

    built: list[_StableFailingSock] = []

    def _build() -> _StableFailingSock:
        s = _StableFailingSock()
        built.append(s)
        return s

    sender = BeaconSender(name="X", web_port=80)
    sender._open_socket = _build  # type: ignore[assignment]
    monkeypatch.setattr(discovery_module.time, "monotonic", lambda: 1000.0)

    waits = {"n": 0}

    def _fake_wait(timeout: float) -> bool:
        waits["n"] += 1
        if waits["n"] >= 3:
            sender._stop_event.set()
        return False

    sender._stop_event.wait = _fake_wait  # type: ignore[assignment]

    sender._run()

    # Only one socket ever built – the non-transient errno kept reusing it.
    assert len(built) == 1
    # sendto kept being called on the same socket across iterations.
    assert built[0].send_attempts >= 2
    # finally: close() fired exactly once at loop exit.
    assert built[0].closed == 1


def test_beacon_sender_run_transient_oserror_rebuilds_even_if_close_raises(
    monkeypatch,
) -> None:
    import errno

    from openfollow.web.discovery import BeaconSender

    class _TransientSock:
        def __init__(self) -> None:
            self.sent = 0
            self.closed_attempts = 0

        def setsockopt(self, *a) -> None:
            pass

        def sendto(self, data: bytes, addr) -> None:
            self.sent += 1
            exc = OSError("network went away")
            exc.errno = errno.ENETUNREACH
            raise exc

        def close(self) -> None:
            self.closed_attempts += 1
            raise OSError("close also failed")

    built: list[_TransientSock] = []

    def _build() -> _TransientSock:
        s = _TransientSock()
        built.append(s)
        return s

    sender = BeaconSender(name="X", web_port=80)
    sender._open_socket = _build  # type: ignore[assignment]
    monkeypatch.setattr(discovery_module.time, "monotonic", lambda: 1000.0)

    waits = {"n": 0}

    def _fake_wait(timeout: float) -> bool:
        waits["n"] += 1
        if waits["n"] >= 4:
            sender._stop_event.set()
        return False

    sender._stop_event.wait = _fake_wait  # type: ignore[assignment]

    sender._run()

    # Each iteration tore down the socket and rebuilt. We expect >= 2 rebuilds
    # which proves the close-raised-OSError branch didn't wedge the loop.
    assert len(built) >= 2


def test_beacon_sender_run_finally_with_no_socket_built(monkeypatch) -> None:
    from openfollow.web.discovery import BeaconSender

    def _always_fail() -> None:
        raise OSError("boom")

    sender = BeaconSender(name="X", web_port=80)
    sender._open_socket = _always_fail  # type: ignore[assignment]
    monkeypatch.setattr(discovery_module.time, "monotonic", lambda: 1000.0)

    sender._stop_event.set()  # stop before the first iteration begins
    # Must not raise.
    sender._run()


def test_beacon_sender_run_finally_swallows_close_oserror(monkeypatch) -> None:
    from openfollow.web.discovery import BeaconSender

    class _CloseFailsSock:
        def __init__(self) -> None:
            self.send_ok = 0

        def setsockopt(self, *a) -> None:
            pass

        def sendto(self, data: bytes, addr) -> None:
            self.send_ok += 1

        def close(self) -> None:
            raise OSError("close blew up")

    sock = _CloseFailsSock()
    sender = BeaconSender(name="X", web_port=80)
    sender._open_socket = lambda: sock  # type: ignore[assignment]

    # One successful iteration, then stop.
    calls = {"n": 0}

    def _fake_wait(timeout: float) -> bool:
        calls["n"] += 1
        sender._stop_event.set()
        return False

    sender._stop_event.wait = _fake_wait  # type: ignore[assignment]

    # Must complete without raising even though close() raises.
    sender._run()
    assert sock.send_ok == 1


# ---------------------------------------------------------------------------
# BeaconReceiver._run – SO_REUSEPORT AttributeError fallback
# ---------------------------------------------------------------------------


def test_beacon_receiver_run_tolerates_missing_so_reuseport(monkeypatch) -> None:
    receiver = BeaconReceiver()

    fake = _FakeRecvSocket([])

    def _stop_after_timeout(bufsize: int):
        receiver._stop_event.set()
        raise discovery_module.socket.timeout()

    fake.recvfrom = _stop_after_timeout  # type: ignore[assignment]

    monkeypatch.setattr(
        discovery_module.socket,
        "socket",
        lambda *a, **kw: fake,
    )
    # Simulate platform missing SO_REUSEPORT entirely. The lookup in _run
    # (`socket.SO_REUSEPORT`) must raise AttributeError and be caught.
    monkeypatch.delattr(discovery_module.socket, "SO_REUSEPORT", raising=False)

    receiver._run()

    assert fake.closed is True
    assert fake.bound_to == ("", discovery_module.BEACON_PORT)


# ---------------------------------------------------------------------------
# Beacon health properties for the diagnostics bundle
# ---------------------------------------------------------------------------


def test_beacon_sender_health_defaults_before_start() -> None:
    """A freshly-constructed sender reports zeros and ``not alive``
    so the diagnostics bundle can render meaningful state even
    when collected during boot, before the thread has started."""
    from openfollow.web.discovery import BeaconSender

    s = BeaconSender(name="X", web_port=80)
    assert s.is_alive is False
    assert s.consecutive_errors == 0
    assert s.last_send_ts == 0.0
    assert s.send_count == 0


def test_beacon_sender_records_successful_send() -> None:
    """One successful send bumps ``send_count`` + ``last_send_ts``
    and keeps ``consecutive_errors`` at zero."""
    from openfollow.web.discovery import BeaconSender

    class _OkSocket:
        def __init__(self) -> None:
            self.sent = 0

        def sendto(self, data: bytes, addr: tuple) -> int:  # noqa: ARG002
            self.sent += 1
            return len(data)

        def close(self) -> None:
            pass

    sock = _OkSocket()
    sender = BeaconSender(name="X", web_port=80)
    sender._open_socket = lambda: sock  # type: ignore[assignment]

    def _stop_after_one(_t: float) -> bool:
        sender._stop_event.set()
        return False

    sender._stop_event.wait = _stop_after_one  # type: ignore[assignment]

    sender._run()
    assert sender.send_count == 1
    assert sender.last_send_ts > 0.0
    assert sender.consecutive_errors == 0


def test_beacon_sender_resets_consecutive_errors_on_recovery(monkeypatch) -> None:
    """Errors bump the counter; a later successful send resets it.
    The reset is what tells the diagnostics bundle the recent
    failure has cleared."""
    from openfollow.web.discovery import BeaconSender

    class _FlakySocket:
        def __init__(self) -> None:
            self.calls = 0

        def sendto(self, data: bytes, addr: tuple) -> int:  # noqa: ARG002
            self.calls += 1
            if self.calls <= 2:
                raise OSError(99, "transient")
            return len(data)

        def close(self) -> None:
            pass

    sock = _FlakySocket()
    sender = BeaconSender(name="X", web_port=80)
    sender._open_socket = lambda: sock  # type: ignore[assignment]
    iterations = {"n": 0}

    def _stop_after_n(_t: float) -> bool:
        iterations["n"] += 1
        if iterations["n"] >= 3:
            sender._stop_event.set()
        return False

    sender._stop_event.wait = _stop_after_n  # type: ignore[assignment]
    monkeypatch.setattr(
        discovery_module,
        "_TRANSIENT_SEND_ERRNOS",
        frozenset(),
    )

    sender._run()
    assert sender.consecutive_errors == 0
    assert sender.send_count == 1


def test_beacon_receiver_health_defaults_before_start() -> None:
    receiver = BeaconReceiver()
    assert receiver.is_alive is False
    assert receiver.packets_received == 0
    assert receiver.last_recv_ts == 0.0


def test_beacon_receiver_counts_self_filtered_packets() -> None:
    """The diagnostics bundle distinguishes "no multicast at all"
    from "multicast works but every packet is mine" by comparing
    ``packets_received`` against ``len(get_peers())``. Self-
    filtered packets must still bump the counter."""
    receiver = BeaconReceiver()
    receiver._local_port = 80
    receiver._local_ips = {"127.0.0.1"}
    receiver._local_ips_ts = time.time() + 1000  # skip refresh

    receiver._handle_packet(
        _beacon_bytes(name="self", web_port=80),
        "127.0.0.1",
    )
    assert receiver.packets_received == 1
    assert receiver.last_recv_ts > 0.0
    assert receiver.get_peers() == []  # self-filtered, no peer entry


def test_beacon_receiver_counts_remote_packets() -> None:
    receiver = BeaconReceiver()
    receiver._local_port = 99
    receiver._local_ips = set()
    receiver._local_ips_ts = time.time() + 1000

    receiver._handle_packet(
        _beacon_bytes(name="remote", web_port=80),
        "10.0.0.5",
    )
    assert receiver.packets_received == 1
    peers = receiver.get_peers()
    assert len(peers) == 1
    assert peers[0].name == "remote"


# ---------------------------------------------------------------------------
# update_iface_ip – repoint the beacon interface after a host IP change
# ---------------------------------------------------------------------------


def test_beacon_sender_update_iface_ip_flags_reopen_on_change() -> None:
    from openfollow.web.discovery import BeaconSender

    sender = BeaconSender(name="N", web_port=80, iface_ip="10.0.0.1")
    sender.update_iface_ip("10.0.0.2")

    assert sender._iface_ip == "10.0.0.2"
    assert sender._reopen.is_set()


def test_beacon_sender_update_iface_ip_noop_when_unchanged() -> None:
    from openfollow.web.discovery import BeaconSender

    sender = BeaconSender(name="N", web_port=80, iface_ip="10.0.0.1")
    sender.update_iface_ip("10.0.0.1")

    assert sender._iface_ip == "10.0.0.1"
    assert not sender._reopen.is_set()


def test_beacon_sender_rebuilds_socket_on_iface_change(monkeypatch) -> None:
    """A live iface change drops the open socket and reopens bound to the new IP."""
    from openfollow.web.discovery import BeaconSender

    monkeypatch.setattr(discovery_module, "BEACON_INTERVAL", 0.02)

    sender = BeaconSender(name="N", web_port=80, iface_ip="10.0.0.1")
    opened_with: list[str] = []
    sockets: list[_FakeSocket] = []

    def _open() -> _FakeSocket:
        opened_with.append(sender._iface_ip)
        sock = _FakeSocket()
        sockets.append(sock)
        return sock

    sender._open_socket = _open  # type: ignore[assignment]

    sender.start()
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not (sockets and sockets[0].send_count >= 1):
            time.sleep(0.01)
        assert sockets and sockets[0].send_count >= 1, "first socket never sent"

        sender.update_iface_ip("10.0.0.2")

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not (len(opened_with) >= 2 and opened_with[-1] == "10.0.0.2"):
            time.sleep(0.01)
        assert opened_with[-1] == "10.0.0.2", "socket not reopened on the new interface"
        assert sockets[0].closed, "old socket must close before the rebuild"
    finally:
        sender.stop()


def test_beacon_receiver_update_iface_ip_flags_reopen_on_change() -> None:
    receiver = BeaconReceiver(iface_ip="10.0.0.1")
    receiver.update_iface_ip("10.0.0.2")

    assert receiver._iface_ip == "10.0.0.2"
    assert receiver._reopen.is_set()


def test_beacon_receiver_update_iface_ip_noop_when_unchanged() -> None:
    receiver = BeaconReceiver(iface_ip="10.0.0.1")
    receiver.update_iface_ip("10.0.0.1")

    assert receiver._iface_ip == "10.0.0.1"
    assert not receiver._reopen.is_set()


class _IdleRecvSocket:
    """Receive-socket double: ``recvfrom`` always times out so the loop idles."""

    def __init__(self) -> None:
        self.closed = False

    def recvfrom(self, _n: int) -> tuple[bytes, tuple[str, int]]:
        time.sleep(0.005)
        raise TimeoutError

    def settimeout(self, _t: float) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def test_beacon_receiver_rebuilds_socket_on_iface_change(monkeypatch) -> None:
    """A live iface change breaks the recv loop so it rejoins on the new IP."""
    monkeypatch.setattr(discovery_module, "BEACON_INTERVAL", 0.02)

    receiver = BeaconReceiver(iface_ip="10.0.0.1")
    opened_with: list[str] = []
    sockets: list[_IdleRecvSocket] = []

    def _setup() -> _IdleRecvSocket:
        opened_with.append(receiver._iface_ip)
        sock = _IdleRecvSocket()
        sockets.append(sock)
        return sock

    receiver._setup_socket = _setup  # type: ignore[assignment]

    receiver.start()
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not opened_with:
            time.sleep(0.01)
        assert opened_with, "receiver never opened its socket"
        first = sockets[0]

        receiver.update_iface_ip("10.0.0.2")

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not (len(opened_with) >= 2 and opened_with[-1] == "10.0.0.2"):
            time.sleep(0.01)
        assert opened_with[-1] == "10.0.0.2", "socket not rejoined on the new interface"
        assert first.closed, "old socket must close before the rebuild"
    finally:
        receiver.stop()
