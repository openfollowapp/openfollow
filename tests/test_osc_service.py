# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the unified OSC service.

Combines two test styles:

- **Unit** (default ``pytestmark``): hermetic, monkeypatch
  ``SimpleUDPClient``, exercise cache + error paths + listener
  lifecycle without touching the network.
- **Integration**: real UDP socket round-trips through ``OscService``
  to prove the migrated send/receive paths still produce wire-identical
  bytes to the legacy ``OscOutputClient`` / ``OscInputHandler``.

The integration block catches any regression in OSC behaviour.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Any

import pytest

import openfollow.osc.service as service_module
from openfollow.osc.service import (
    _PYTHONOSC_AVAILABLE,
    ClientStats,
    OscService,
    find_free_udp_port,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Fakes for hermetic unit coverage
# ---------------------------------------------------------------------------


class _FakeSocket:
    def __init__(self) -> None:
        self.closed = False
        self.close_exc: Exception | None = None
        self.sockopts: list[tuple[int, int, int]] = []

    def setsockopt(self, level: int, optname: int, value: int) -> None:
        self.sockopts.append((level, optname, value))

    def close(self) -> None:
        if self.close_exc is not None:
            raise self.close_exc
        self.closed = True


class _FakeClient:
    """Stands in for ``pythonosc.udp_client.SimpleUDPClient``.

    Records every send into a class-level list so tests can assert on
    ``calls`` after the service routes through the cache. ``send_exc``
    forces a failure path.
    """

    instances: list[_FakeClient] = []

    def __init__(self, host: str, port: int, allow_broadcast: bool = False) -> None:
        self.host = host
        self.port = port
        self.allow_broadcast = allow_broadcast
        self.calls: list[tuple[str, list[Any]]] = []
        self._sock = _FakeSocket()
        self.send_exc: Exception | None = None
        _FakeClient.instances.append(self)

    def send_message(self, address: str, args: list[Any]) -> None:
        if self.send_exc is not None:
            raise self.send_exc
        self.calls.append((address, args))


class _ExplodingClient:
    def __init__(self, host: str, port: int, allow_broadcast: bool = False) -> None:
        raise OSError(f"DNS failure for {host}:{port}")


@pytest.fixture(autouse=True)
def _reset_fake_clients() -> None:
    _FakeClient.instances.clear()


@pytest.fixture
def patched_service(monkeypatch: pytest.MonkeyPatch) -> OscService:
    """OscService with ``SimpleUDPClient`` replaced by ``_FakeClient``.

    ``_resolve_host`` is stubbed to identity so the fake hostnames the
    tests use ("h", "h1", …) reach ``_FakeClient`` verbatim without a real
    DNS lookup – keeps the unit suite hermetic.
    """
    monkeypatch.setattr(service_module, "SimpleUDPClient", _FakeClient)
    monkeypatch.setattr(service_module, "_resolve_host", lambda host: host)
    return OscService()


# ---------------------------------------------------------------------------
# send() – happy path
# ---------------------------------------------------------------------------


def test_send_routes_to_fake_client(patched_service: OscService) -> None:
    patched_service.send("/cue/1/go", host="127.0.0.1", port=53000)
    assert len(_FakeClient.instances) == 1
    assert _FakeClient.instances[0].calls == [("/cue/1/go", [])]


def test_send_passes_args_through(patched_service: OscService) -> None:
    patched_service.send(
        "/marker/0",
        [1.5, -2.0, 0.25],
        host="10.0.0.5",
        port=9000,
    )
    assert _FakeClient.instances[0].calls == [
        ("/marker/0", [1.5, -2.0, 0.25]),
    ]


def test_send_caches_per_target(patched_service: OscService) -> None:
    patched_service.send("/a", host="h1", port=1)
    patched_service.send("/b", host="h1", port=1)
    patched_service.send("/c", host="h2", port=1)
    # Two cache entries – same host:port reuses, different host opens fresh.
    assert len(_FakeClient.instances) == 2


def test_send_updates_total_sent_counter(patched_service: OscService) -> None:
    patched_service.send("/a", host="h", port=1)
    patched_service.send("/b", host="h", port=1)
    stats = patched_service.stats_for("h", 1)
    assert stats.total_sent == 2
    assert stats.total_errors == 0


# ---------------------------------------------------------------------------
# send() – guard rails
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("address", ["", None])
def test_send_drops_empty_address(
    patched_service: OscService,
    address: Any,
) -> None:
    patched_service.send(address or "", host="h", port=1)
    assert _FakeClient.instances == []


@pytest.mark.parametrize(
    "host,port",
    [("", 53000), ("127.0.0.1", 0), ("127.0.0.1", -1)],
)
def test_send_drops_invalid_target(
    patched_service: OscService,
    host: str,
    port: int,
) -> None:
    patched_service.send("/foo", host=host, port=port)
    assert _FakeClient.instances == []


def test_send_with_unknown_protocol_warns_and_drops(
    patched_service: OscService,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``udp`` and ``tcp`` are the supported transports; anything else
    (a hand-edited ``protocol = "raw"`` in TOML, say) is rejected at
    send time with a warning and a drop. The ``OscTransmitterConfig``
    save-time coercion already pins ``protocol`` to the allowed set,
    so this is defence-in-depth for crafted configs."""
    with caplog.at_level(logging.WARNING):
        patched_service.send(
            "/x",
            host="h",
            port=1,
            protocol="raw",
        )
    assert _FakeClient.instances == []
    assert any("unknown protocol" in rec.message for rec in caplog.records)


def test_send_warns_once_when_pythonosc_missing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(service_module, "_PYTHONOSC_AVAILABLE", False)
    svc = OscService()
    with caplog.at_level(logging.WARNING):
        svc.send("/a", host="h", port=1)
        svc.send("/b", host="h", port=1)
    warnings = [r for r in caplog.records if "python-osc not installed" in r.message]
    assert len(warnings) == 1


# ---------------------------------------------------------------------------
# send() – error paths
# ---------------------------------------------------------------------------


def test_send_with_client_construction_error_returns_silently(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(service_module, "SimpleUDPClient", _ExplodingClient)
    monkeypatch.setattr(service_module, "_resolve_host", lambda host: host)
    svc = OscService()
    with caplog.at_level(logging.ERROR):
        svc.send("/a", host="bogus", port=1)
    assert any("Failed to create OSC client" in r.message for r in caplog.records)
    # No entry cached because client construction blew up.
    assert svc.stats_for("bogus", 1) == ClientStats()


def test_send_message_failure_increments_error_counter(
    patched_service: OscService,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Prime the cache, then make subsequent sends raise.
    patched_service.send("/ok", host="h", port=1)
    _FakeClient.instances[0].send_exc = OSError("net unreachable")
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            patched_service.send("/x", host="h", port=1)
    stats = patched_service.stats_for("h", 1)
    assert stats.total_errors == 3
    assert "net unreachable" in stats.last_error
    # First few errors logged at WARNING.
    warnings = [r for r in caplog.records if "OSC send" in r.message]
    assert len(warnings) >= 1


def test_send_error_throttle_quiet_after_burst(
    patched_service: OscService,
    caplog: pytest.LogCaptureFixture,
) -> None:
    patched_service.send("/ok", host="h", port=1)
    _FakeClient.instances[0].send_exc = ValueError("bad address")
    # 30 failures should produce ≤ 5 + 0 (no multiple-of-100 hit) log entries.
    with caplog.at_level(logging.WARNING):
        for _ in range(30):
            patched_service.send("/x", host="h", port=1)
    warnings = [r for r in caplog.records if "OSC send" in r.message]
    assert len(warnings) <= 5


# ---------------------------------------------------------------------------
# evict() / shutdown_clients()
# ---------------------------------------------------------------------------


def test_evict_closes_socket_and_drops_entry(
    patched_service: OscService,
) -> None:
    patched_service.send("/a", host="h", port=1)
    fake = _FakeClient.instances[0]
    patched_service.evict("h", 1)
    assert fake._sock.closed is True
    # Subsequent send creates a fresh client.
    patched_service.send("/b", host="h", port=1)
    assert len(_FakeClient.instances) == 2


def test_evict_missing_key_is_noop(patched_service: OscService) -> None:
    patched_service.evict("never-cached", 1)


def test_evict_tolerates_socket_close_error(
    patched_service: OscService,
) -> None:
    patched_service.send("/a", host="h", port=1)
    _FakeClient.instances[0]._sock.close_exc = OSError("already closed")
    patched_service.evict("h", 1)
    # No crash – and the entry is still dropped.
    assert patched_service.stats_for("h", 1).total_sent == 0


def test_evict_skips_close_when_client_has_no_sock_attribute(
    patched_service: OscService,
) -> None:
    patched_service.send("/a", host="h", port=1)
    _FakeClient.instances[0]._sock = None  # type: ignore[assignment]
    patched_service.evict("h", 1)


def test_shutdown_clients_skips_close_when_sock_attribute_missing(
    patched_service: OscService,
) -> None:
    patched_service.send("/a", host="h", port=1)
    _FakeClient.instances[0]._sock = None  # type: ignore[assignment]
    patched_service.shutdown_clients()


@pytest.mark.parametrize(
    "loser_sock_state",
    [
        "with_sock",  # close path
        "no_sock",  # skip close (line 275 False arm)
        "close_raises",  # close raises OSError (line 278-279)
    ],
)
def test_get_or_create_client_loses_construction_race(
    monkeypatch: pytest.MonkeyPatch,
    loser_sock_state: str,
) -> None:
    """Cover the race-resolution arm of ``_get_or_create_client``: when
    a concurrent caller seats a cache entry while we were still
    constructing ours, our newly-built client is discarded and the
    existing entry is returned. Three sub-cases for the loser's socket:
    normal close, no ``_sock`` attribute, and close raising OSError."""
    svc = OscService()
    winner_holder: dict[str, _FakeClient] = {}

    class _RaceClient(_FakeClient):
        def __init__(self, host: str, port: int, allow_broadcast: bool = False) -> None:
            super().__init__(host, port, allow_broadcast)
            # Configure this loser's socket per parametrised state.
            if loser_sock_state == "no_sock":
                self._sock = None  # type: ignore[assignment]
            elif loser_sock_state == "close_raises":
                self._sock.close_exc = OSError("loser close failed")
            # Seed the cache with a winner under the same key –
            # simulates 'another thread won the race'.
            if "winner" not in winner_holder:
                winner = _FakeClient("seeded", 0)
                winner_holder["winner"] = winner
                from openfollow.osc.service import _ClientEntry

                # Cache key is ``(host, port, protocol, framing)``;
                # UDP entries pin to ``"length_prefix"``.
                svc._cache[(host, port, "udp", "length_prefix")] = _ClientEntry(client=winner)

    monkeypatch.setattr(service_module, "SimpleUDPClient", _RaceClient)
    monkeypatch.setattr(service_module, "_resolve_host", lambda host: host)
    svc.send("/x", host="race-host", port=1)
    # The seeded 'winner' handled the send regardless of the loser's
    # socket state.
    assert winner_holder["winner"].calls == [("/x", [])]


def test_shutdown_clients_drains_cache(patched_service: OscService) -> None:
    patched_service.send("/a", host="h1", port=1)
    patched_service.send("/b", host="h2", port=2)
    patched_service.shutdown_clients()
    for fake in _FakeClient.instances:
        assert fake._sock.closed is True


def test_shutdown_clients_tolerates_close_errors(
    patched_service: OscService,
) -> None:
    patched_service.send("/a", host="h", port=1)
    _FakeClient.instances[0]._sock.close_exc = OSError("boom")
    patched_service.shutdown_clients()


def test_stats_for_unknown_target_returns_empty(
    patched_service: OscService,
) -> None:
    assert patched_service.stats_for("never", 0) == ClientStats()


# ---------------------------------------------------------------------------
# Framing in the cache key
# ---------------------------------------------------------------------------


class _FakeTcpSender:
    """Stands in for ``TcpOscSender`` so the framing-cache tests stay
    hermetic – no listener required, just a record of what framing each
    cached client was constructed with."""

    instances: list[_FakeTcpSender] = []

    def __init__(self, host: str, port: int, framing: str) -> None:
        self.host = host
        self.port = port
        self.framing = framing
        self.calls: list[tuple[str, list[Any]]] = []
        self.closed = False
        _FakeTcpSender.instances.append(self)

    def send_message(self, address: str, args: list[Any]) -> None:
        self.calls.append((address, args))

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def patched_tcp_service(monkeypatch: pytest.MonkeyPatch) -> OscService:
    """OscService whose TCP construction is redirected to ``_FakeTcpSender``
    so framing-dispatch can be asserted without opening sockets."""
    _FakeTcpSender.instances.clear()
    monkeypatch.setattr(service_module, "TcpOscSender", _FakeTcpSender)
    monkeypatch.setattr(service_module, "SimpleUDPClient", _FakeClient)
    monkeypatch.setattr(service_module, "_resolve_host", lambda host: host)
    return OscService()


def test_tcp_send_constructs_sender_with_chosen_framing(
    patched_tcp_service: OscService,
) -> None:
    patched_tcp_service.send(
        "/x",
        host="h",
        port=1,
        protocol="tcp",
        framing="slip",
    )
    patched_tcp_service.send(
        "/y",
        host="h",
        port=1,
        protocol="tcp",
        framing="length_prefix",
    )
    framings = [s.framing for s in _FakeTcpSender.instances]
    assert framings == ["slip", "length_prefix"]


def test_tcp_cache_separates_entries_by_framing(
    patched_tcp_service: OscService,
) -> None:
    patched_tcp_service.send(
        "/a",
        host="h",
        port=1,
        protocol="tcp",
        framing="slip",
    )
    patched_tcp_service.send(
        "/b",
        host="h",
        port=1,
        protocol="tcp",
        framing="length_prefix",
    )
    assert len(_FakeTcpSender.instances) == 2


def test_tcp_cache_reuses_entry_for_same_framing(
    patched_tcp_service: OscService,
) -> None:
    """Same host/port/protocol/framing → one cached sender across
    multiple sends."""
    for _ in range(3):
        patched_tcp_service.send(
            "/x",
            host="h",
            port=1,
            protocol="tcp",
            framing="slip",
        )
    assert len(_FakeTcpSender.instances) == 1
    assert len(_FakeTcpSender.instances[0].calls) == 3


def test_udp_framing_is_pinned_in_cache_key(
    patched_service: OscService,
) -> None:
    """UDP rows ignore framing in the cache key – two sends that differ
    only in their (meaningless-for-UDP) framing share one cached client.
    Otherwise every UDP row would double-cache by accident when the
    framing field round-trips through TOML."""
    patched_service.send(
        "/a",
        host="h",
        port=1,
        protocol="udp",
        framing="slip",
    )
    patched_service.send(
        "/b",
        host="h",
        port=1,
        protocol="udp",
        framing="length_prefix",
    )
    assert len(_FakeClient.instances) == 1


def test_stats_for_with_tcp_framing_is_isolated(
    patched_tcp_service: OscService,
) -> None:
    """Per-framing stats follow the cache-key separation."""
    patched_tcp_service.send(
        "/a",
        host="h",
        port=1,
        protocol="tcp",
        framing="slip",
    )
    slip_stats = patched_tcp_service.stats_for(
        "h",
        1,
        protocol="tcp",
        framing="slip",
    )
    other_stats = patched_tcp_service.stats_for(
        "h",
        1,
        protocol="tcp",
        framing="length_prefix",
    )
    assert slip_stats.total_sent == 1
    assert other_stats == ClientStats()


def test_evict_with_tcp_framing_only_drops_matching_entry(
    patched_tcp_service: OscService,
) -> None:
    patched_tcp_service.send(
        "/a",
        host="h",
        port=1,
        protocol="tcp",
        framing="slip",
    )
    patched_tcp_service.send(
        "/b",
        host="h",
        port=1,
        protocol="tcp",
        framing="length_prefix",
    )
    patched_tcp_service.evict("h", 1, protocol="tcp", framing="slip")
    # Only the SLIP sender was closed.
    closed = [s for s in _FakeTcpSender.instances if s.closed]
    assert len(closed) == 1
    assert closed[0].framing == "slip"


def test_send_with_unknown_framing_falls_back_to_slip(
    patched_tcp_service: OscService,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Bogus framing value (programmatic caller bypassing config-layer
    coercion) doesn't break send – it warns and uses the default."""
    with caplog.at_level(logging.WARNING):
        patched_tcp_service.send(
            "/x",
            host="h",
            port=1,
            protocol="tcp",
            framing="bogus",
        )
    assert _FakeTcpSender.instances[0].framing == "slip"
    assert any("unknown TCP framing" in rec.message for rec in caplog.records)


def test_send_udp_with_bogus_framing_does_not_warn(
    patched_tcp_service: OscService,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Framing is TCP-only. UDP bogus framing now stays silent since
    framing is always length_prefix for UDP anyway."""
    with caplog.at_level(logging.WARNING):
        patched_tcp_service.send(
            "/x",
            host="h",
            port=1,
            protocol="udp",
            framing="bogus",
        )
    assert not any("framing" in rec.message for rec in caplog.records)


def test_cache_framing_unknown_tcp_value_normalises_to_slip(
    patched_tcp_service: OscService,
) -> None:
    """``evict`` / ``stats_for`` accept a framing arg without re-running
    the validation that ``send`` does. ``_cache_framing`` is the
    defensive normaliser that prevents a stray TCP framing value from
    fragmenting the cache lookup. Send with ``"slip"`` so the entry
    seats under the canonical key, then look it up via
    ``stats_for(framing="bogus")`` – the normaliser maps it back to
    ``"slip"`` and the existing entry is found."""
    patched_tcp_service.send(
        "/x",
        host="h",
        port=1,
        protocol="tcp",
        framing="slip",
    )
    stats = patched_tcp_service.stats_for(
        "h",
        1,
        protocol="tcp",
        framing="bogus",
    )
    assert stats.total_sent == 1


# ---------------------------------------------------------------------------
# Subscribe / unsubscribe
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_subscribe_replaces_prior_handler() -> None:
    svc = OscService()
    seen: list[str] = []
    svc.subscribe("/foo", lambda *_: seen.append("first"))
    svc.subscribe("/foo", lambda *_: seen.append("second"))
    # Only the second handler should fire.
    handlers = svc._dispatcher.handlers_for_address("/foo")
    # pythonosc returns a list of Handler tuples (callable, args, ...);
    # asserting exactly one mapping is the contract here.
    assert len(list(handlers)) == 1


@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_unsubscribe_missing_pattern_is_noop() -> None:
    svc = OscService()
    svc.unsubscribe("/never-mapped")


@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_unsubscribe_present_pattern_drops_handler() -> None:
    svc = OscService()
    svc.subscribe("/foo", lambda *_: None)
    svc.unsubscribe("/foo")
    # Pattern is gone – a re-subscribe replaces a fresh handler, not
    # a stale one.
    assert "/foo" not in svc._subscriptions


# ---------------------------------------------------------------------------
# Dispatcher map-mutation vs dispatch iteration race
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_subscribe_during_dispatch_does_not_break_iteration() -> None:
    """A ``subscribe`` that adds a new pattern while the listener thread is
    mid-dispatch must not raise ``RuntimeError: dictionary changed size
    during iteration``.

    Reproduces the steady-state race: an inbound packet is being dispatched
    (``call_handlers_for_packet`` iterates the dispatcher map) at the exact
    instant a config toggle calls ``subscribe`` and seats a new map key. On a
    bare ``pythonosc.Dispatcher`` the live generator trips over the resized
    dict; the guarded dispatcher snapshots its match set first."""
    from pythonosc.osc_message_builder import OscMessageBuilder

    svc = OscService()
    fired: list[str] = []

    def _mutating_handler(address: str, *_args: Any) -> None:
        # Mid-dispatch subscribe → adds a new dict key in the dispatcher map.
        svc.subscribe("/late/pattern", lambda *_: None)
        fired.append(address)

    # Two matching patterns so the dispatch loop iterates the map more than
    # once after the mutation lands.
    svc.subscribe("/marker/0", _mutating_handler)
    svc.subscribe("/marker/1", lambda *_: None)

    datagram = OscMessageBuilder(address="/marker/0").build().dgram
    # Must complete without RuntimeError, and the handler must have run.
    svc._dispatcher.call_handlers_for_packet(datagram, ("127.0.0.1", 1))
    assert fired == ["/marker/0"]


@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_guarded_dispatcher_still_matches_patterns() -> None:
    """The lock guard must not change dispatch semantics: a subscribed
    glob pattern still routes a matching address to its handler."""
    from pythonosc.osc_message_builder import OscMessageBuilder

    svc = OscService()
    seen: list[tuple[str, tuple[Any, ...]]] = []
    svc.subscribe("/marker/*", lambda addr, *args: seen.append((addr, args)))
    datagram = OscMessageBuilder(address="/marker/2").build().dgram
    svc._dispatcher.call_handlers_for_packet(datagram, ("127.0.0.1", 1))
    assert seen == [("/marker/2", ())]


# ---------------------------------------------------------------------------
# TCP transport wiring (service-level)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_send_with_tcp_protocol_uses_tcp_sender() -> None:
    from openfollow.osc.transport import TcpOscSender

    svc = OscService()
    # Send to port 1 (refused). The send raises OSError internally,
    # which the service catches and counts as a stat error. The cache
    # should hold the TcpOscSender.
    svc.send("/x", host="127.0.0.1", port=1, protocol="tcp")
    cached = list(svc._cache.values())
    assert len(cached) == 1
    assert isinstance(cached[0].client, TcpOscSender)
    # ``shutdown_clients`` must dispatch to ``TcpOscSender.close()`` –
    # if it tried to close a ``_sock`` attribute the call would silently
    # leak the reader thread (there isn't one yet) and the test would
    # still pass; but the type-aware close path was the whole point.
    svc.shutdown_clients()
    assert svc._cache == {}


# ---------------------------------------------------------------------------
# Listener lifecycle (real UDP – fast, but counts as integration)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_listener_starts_and_subscribers_receive() -> None:
    """End-to-end: send via OscService.send → receive via subscribe.

    Proves the unified service preserves both sides of the existing OSC
    behaviour without a regression."""
    svc = OscService()
    port = find_free_udp_port()
    received: list[tuple[str, tuple[Any, ...]]] = []

    def _handler(address: str, *args: Any) -> None:
        received.append((address, args))

    svc.subscribe("/marker/*", _handler)
    svc.start_listener(port, allowed_ips=())

    try:
        svc.send("/marker/0", [1.0, 2.0, 3.0], host="127.0.0.1", port=port)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not received:
            time.sleep(0.01)
        assert received, "subscriber did not receive packet"
        addr, args = received[0]
        assert addr == "/marker/0"
        assert list(args) == [1.0, 2.0, 3.0]
    finally:
        svc.shutdown()


@pytest.mark.integration
@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_listener_idempotent_on_same_params() -> None:
    svc = OscService()
    port = find_free_udp_port()
    svc.start_listener(port, allowed_ips=())
    listener = svc._listener
    svc.start_listener(port, allowed_ips=())
    # Same listener instance – the second call short-circuited.
    assert svc._listener is listener
    svc.shutdown()


@pytest.mark.integration
@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_listener_restart_on_changed_params() -> None:
    svc = OscService()
    port_a = find_free_udp_port()
    svc.start_listener(port_a, allowed_ips=())
    first = svc._listener
    port_b = find_free_udp_port()
    svc.start_listener(port_b, allowed_ips=())
    assert svc._listener is not first
    assert svc.listener_port == port_b
    svc.shutdown()


@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_restart_listener_on_bind_failure_raises() -> None:
    """Hard-signal contract: bind failure raises OSError so the
    hot-reload dispatcher can revert the config."""
    svc = OscService()
    # Bind a foreign socket on a port, then ask the service to bind it too.
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(("127.0.0.1", 0))
    port = blocker.getsockname()[1]
    try:
        with pytest.raises(OSError):
            svc.restart_listener(port=port, allowed_ips=())
    finally:
        blocker.close()


@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_stop_listener_idempotent_when_not_running() -> None:
    svc = OscService()
    svc.stop_listener()  # no-op
    svc.stop_listener()  # still no-op


def test_listener_status_defaults_when_stopped() -> None:
    """Diagnostics accessor reports the idle state without a running listener."""
    svc = OscService()
    assert svc.listener_status() == {
        "port": None,
        "multicast_group": "",
        "multicast_joined": False,
        "allowed_sender_ips": [],
    }


@pytest.mark.integration
@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_listener_with_allowlist_logs_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    svc = OscService()
    port = find_free_udp_port()
    with caplog.at_level(logging.INFO):
        svc.start_listener(port, allowed_ips=["127.0.0.1"])
    info_records = [r for r in caplog.records if "accepting packets only" in r.message]
    assert len(info_records) == 1
    svc.shutdown()


@pytest.mark.integration
@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_listener_without_allowlist_warns_about_exposure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    svc = OscService()
    port = find_free_udp_port()
    with caplog.at_level(logging.WARNING):
        svc.start_listener(port, allowed_ips=())
    warnings = [r for r in caplog.records if "no sender allowlist" in r.message]
    assert len(warnings) == 1
    svc.shutdown()


# ---------------------------------------------------------------------------
# Allowlist – _FilteredOSCUDPServer.verify_request
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
class TestFilteredOSCUDPServerVerifyRequest:
    """Direct hermetic checks for the allowlist filter – start_listener
    integration above proves the wiring; these prove the matrix."""

    def _server(self, allowed: frozenset[str]) -> Any:
        svc = OscService()
        port = find_free_udp_port()
        svc.start_listener(port, allowed_ips=allowed)
        srv = svc._listener
        try:
            yield srv
        finally:
            svc.shutdown()

    def test_empty_allowlist_accepts_all(self) -> None:
        gen = self._server(frozenset())
        srv = next(gen)
        assert srv.verify_request(object(), ("10.0.0.1", 1234)) is True
        try:
            next(gen)
        except StopIteration:
            pass

    def test_populated_allowlist_blocks_others(self) -> None:
        gen = self._server(frozenset({"127.0.0.1"}))
        srv = next(gen)
        assert srv.verify_request(object(), ("10.0.0.1", 1)) is False
        assert srv.verify_request(object(), ("127.0.0.1", 1)) is True
        try:
            next(gen)
        except StopIteration:
            pass

    def test_str_address_fails_closed(self) -> None:
        """LSP-widening from socketserver's ``str | tuple`` address type
        forces us to handle the str case; we treat it as 'not in
        allowlist' so verification fails closed."""
        gen = self._server(frozenset({"127.0.0.1"}))
        srv = next(gen)
        assert srv.verify_request(object(), "/some/unix/socket") is False
        try:
            next(gen)
        except StopIteration:
            pass


# ---------------------------------------------------------------------------
# Context-manager + shutdown
# ---------------------------------------------------------------------------


def test_context_manager_calls_shutdown(
    patched_service: OscService,
) -> None:
    with patched_service as svc:
        svc.send("/x", host="h", port=1)
    # Cache drained at __exit__.
    fake = _FakeClient.instances[0]
    assert fake._sock.closed is True


# ---------------------------------------------------------------------------
# Integration: wire-format parity with legacy OscOutputClient
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_send_produces_real_udp_datagram_at_expected_address() -> None:
    """The unified service emits the same UDP datagram bytes the legacy
    ``OscOutputClient`` would. Bind a raw UDP socket as the receiver,
    send via ``OscService``, assert the datagram arrives addressed to
    the right OSC method."""
    received: list[bytes] = []
    stop = threading.Event()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.settimeout(0.1)

    def _serve() -> None:
        while not stop.is_set():
            try:
                data, _ = sock.recvfrom(4096)
            except TimeoutError:
                continue
            except OSError:
                return
            received.append(data)

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    svc = OscService()
    try:
        svc.send("/cue/1/go", host="127.0.0.1", port=port)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not received:
            time.sleep(0.01)
    finally:
        stop.set()
        svc.shutdown()
        thread.join(timeout=1.0)
        sock.close()
    assert received, "no datagram received"
    # OSC encodes the address as a 4-byte-aligned NUL-padded string at
    # the start of the message – the path appears verbatim.
    assert received[0].startswith(b"/cue/1/go")


# ---------------------------------------------------------------------------
# Multicast group join
# ---------------------------------------------------------------------------


class _RecordingSocket:
    """Minimal socket stand-in capturing ``setsockopt`` for the multicast
    join helper unit tests; ``raise_oserror`` forces the failure path."""

    def __init__(self, *, raise_oserror: bool = False) -> None:
        self.opts: list[tuple[int, int, bytes]] = []
        self._raise = raise_oserror

    def setsockopt(self, level: int, optname: int, value: bytes) -> None:
        if self._raise:
            raise OSError("join failed")
        self.opts.append((level, optname, value))


def test_join_multicast_group_success() -> None:
    sock = _RecordingSocket()
    assert service_module._join_multicast_group(sock, "239.1.2.3", 9000) is True
    assert len(sock.opts) == 1
    level, optname, value = sock.opts[0]
    assert level == socket.IPPROTO_IP
    assert optname == socket.IP_ADD_MEMBERSHIP
    assert value == socket.inet_aton("239.1.2.3") + socket.inet_aton("0.0.0.0")


def test_join_multicast_group_swallows_oserror(caplog: pytest.LogCaptureFixture) -> None:
    sock = _RecordingSocket(raise_oserror=True)
    with caplog.at_level(logging.WARNING):
        result = service_module._join_multicast_group(sock, "239.1.2.3", 9000)  # must not raise
    assert result is False
    assert any("could not join multicast group" in r.message for r in caplog.records)


@pytest.mark.integration
@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_restart_listener_joins_multicast_group(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``restart_listener`` joins the group on the bound socket and records
    it as listener state; ``stop_listener`` clears it. The join is stubbed."""
    svc = OscService()
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        service_module,
        "_join_multicast_group",
        lambda sock, group, port: calls.append((group, port)) or True,
    )
    port = find_free_udp_port()
    with caplog.at_level(logging.WARNING):
        svc.restart_listener(port=port, allowed_ips=(), multicast_group="239.1.2.3")
    try:
        assert calls == [("239.1.2.3", port)]
        assert svc._listener_multicast_group == "239.1.2.3"
        assert svc.listener_port == port
        assert any("joined multicast group 239.1.2.3" in r.message for r in caplog.records)
        # Diagnostics accessor reflects the live join.
        status = svc.listener_status()
        assert status["multicast_group"] == "239.1.2.3"
        assert status["multicast_joined"] is True
        assert status["port"] == port
    finally:
        svc.shutdown()
    # stop_listener (via shutdown) resets the recorded group + join flag.
    assert svc._listener_multicast_group == ""
    assert svc.listener_status()["multicast_joined"] is False


@pytest.mark.integration
@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_restart_listener_failed_join_omits_joined_log(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed multicast join must not produce a misleading 'joined' log."""
    svc = OscService()
    monkeypatch.setattr(service_module, "_join_multicast_group", lambda *a: False)
    port = find_free_udp_port()
    with caplog.at_level(logging.WARNING):
        svc.restart_listener(port=port, allowed_ips=(), multicast_group="239.1.2.3")
    try:
        assert not any("joined multicast group" in r.message for r in caplog.records)
        # Group requested but join failed → status distinguishes the two.
        status = svc.listener_status()
        assert status["multicast_group"] == "239.1.2.3"
        assert status["multicast_joined"] is False
    finally:
        svc.shutdown()


@pytest.mark.integration
@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_listener_with_allowlist_and_group_logs_info(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    svc = OscService()
    monkeypatch.setattr(service_module, "_join_multicast_group", lambda *a: True)
    port = find_free_udp_port()
    with caplog.at_level(logging.INFO):
        svc.start_listener(port, allowed_ips=["127.0.0.1"], multicast_group="239.4.5.6")
    try:
        assert any("joined multicast group 239.4.5.6" in r.message for r in caplog.records)
    finally:
        svc.shutdown()


@pytest.mark.integration
@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_start_listener_idempotent_with_same_group(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = OscService()
    monkeypatch.setattr(service_module, "_join_multicast_group", lambda *a: None)
    port = find_free_udp_port()
    svc.start_listener(port, allowed_ips=(), multicast_group="239.1.2.3")
    first = svc._listener
    svc.start_listener(port, allowed_ips=(), multicast_group="239.1.2.3")
    assert svc._listener is first  # same params → no rebind
    svc.shutdown()


@pytest.mark.integration
@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_start_listener_rebinds_on_group_change(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = OscService()
    monkeypatch.setattr(service_module, "_join_multicast_group", lambda *a: None)
    port = find_free_udp_port()
    svc.start_listener(port, allowed_ips=(), multicast_group="239.1.2.3")
    first = svc._listener
    svc.start_listener(port, allowed_ips=(), multicast_group="239.9.9.9")
    assert svc._listener is not first  # group change → rebind
    assert svc._listener_multicast_group == "239.9.9.9"
    svc.shutdown()


# --------------------------------------------------------------------------- #
# _udp_dest_class / _make_client – broadcast & multicast socket options
# --------------------------------------------------------------------------- #


def test_make_client_multicast_sets_ttl_and_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module, "SimpleUDPClient", _FakeClient)
    client = service_module._make_client("239.1.2.3", 9000, "udp", "slip")
    assert client.allow_broadcast is False
    assert (socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, service_module._MULTICAST_TTL) in client._sock.sockopts
    assert (socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1) in client._sock.sockopts


def test_make_client_limited_broadcast_enables_allow_broadcast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module, "SimpleUDPClient", _FakeClient)
    client = service_module._make_client("255.255.255.255", 9000, "udp", "slip")
    assert client.allow_broadcast is True
    assert client._sock.sockopts == []


def test_make_client_directed_broadcast_enables_allow_broadcast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module, "SimpleUDPClient", _FakeClient)
    client = service_module._make_client("192.168.1.255", 9000, "udp", "slip")
    assert client.allow_broadcast is True


def test_make_client_unicast_leaves_socket_plain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module, "SimpleUDPClient", _FakeClient)
    client = service_module._make_client("192.168.1.5", 9000, "udp", "slip")
    assert client.allow_broadcast is False
    assert client._sock.sockopts == []


def test_udp_dest_class_hostname_is_unicast() -> None:
    # A non-IPv4 literal (hostname) can't be classified – treat as unicast.
    assert service_module._udp_dest_class("osc.example.com") == "unicast"


# --------------------------------------------------------------------------- #
# _resolve_host – bounded hostname resolution off the scheduler hot path
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _clear_resolve_cache() -> None:
    service_module._resolve_failures.clear()


def test_resolve_host_passes_ipv4_literal_through_without_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An IPv4 literal must short-circuit – no DNS lookup at all, so the
    per-frame send loop never pays for resolution on the common path."""

    def _boom(_host: str) -> str:
        raise AssertionError("gethostbyname must not be called for an IP literal")

    monkeypatch.setattr(service_module.socket, "gethostbyname", _boom)
    assert service_module._resolve_host("192.168.1.5") == "192.168.1.5"


def test_resolve_host_resolves_hostname_to_literal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hostname is resolved to an IPv4 literal that is then handed to
    ``SimpleUDPClient`` (so pythonosc's own ``getaddrinfo`` resolves
    instantly instead of blocking on the scheduler thread)."""
    monkeypatch.setattr(service_module.socket, "gethostbyname", lambda host: "10.0.0.7")
    monkeypatch.setattr(service_module, "SimpleUDPClient", _FakeClient)
    client = service_module._make_client("osc.example.com", 9000, "udp", "slip")
    assert client.host == "10.0.0.7"


def test_resolve_host_bounds_slow_dns_and_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A slow/hung resolver must not stall the caller indefinitely: the
    lookup is bounded by ``_RESOLVE_TIMEOUT_S`` and surfaces as ``OSError``
    well before the (10×-longer) lookup would have returned."""
    monkeypatch.setattr(service_module, "_RESOLVE_TIMEOUT_S", 0.05)

    def _slow(_host: str) -> str:
        time.sleep(5.0)
        return "10.0.0.9"

    monkeypatch.setattr(service_module.socket, "gethostbyname", _slow)
    start = time.monotonic()
    # Sub-second deadline must render with its fraction, not round to "0s".
    with pytest.raises(OSError, match="timed out after 0.05s"):
        service_module._resolve_host("slow.example.com")
    # Bounded: returns near the deadline, not after the 5 s sleep.
    assert time.monotonic() - start < 1.0


def test_resolve_host_relays_lookup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing lookup surfaces as ``OSError`` so ``_get_or_create_client``
    logs it and caches nothing (no client for an unresolvable target)."""

    def _fail(_host: str) -> str:
        raise socket.gaierror("nodename nor servname provided")

    monkeypatch.setattr(service_module.socket, "gethostbyname", _fail)
    with pytest.raises(OSError, match="failed"):
        service_module._resolve_host("nope.invalid")


def test_resolve_host_caches_failure_to_avoid_respawning_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    """A repeated failing lookup hits the negative cache and raises without a
    second resolver thread, bounding thread churn under sustained failure."""
    calls: list[str] = []

    def _fail(host: str) -> str:
        calls.append(host)
        raise socket.gaierror("nodename nor servname provided")

    monkeypatch.setattr(service_module.socket, "gethostbyname", _fail)
    with pytest.raises(OSError, match="failed"):
        service_module._resolve_host("bad.invalid")
    with pytest.raises(OSError, match="recently failed"):
        service_module._resolve_host("bad.invalid")
    assert calls == ["bad.invalid"]  # second call short-circuited, no new lookup


def test_send_to_slow_hostname_does_not_hang_and_caches_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end on the send path: a target whose DNS lookup hangs is
    dropped (bounded) rather than wedging the scheduler – no client is
    cached, matching the existing construction-failure contract."""
    monkeypatch.setattr(service_module, "_RESOLVE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(service_module, "SimpleUDPClient", _FakeClient)
    monkeypatch.setattr(service_module.socket, "gethostbyname", lambda host: time.sleep(5.0))
    svc = OscService()
    start = time.monotonic()
    svc.send("/x", host="slow.example.com", port=9000)  # must not hang
    assert time.monotonic() - start < 1.0
    assert _FakeClient.instances == []
    assert svc.stats_for("slow.example.com", 9000) == ClientStats()


def test_send_swallows_non_oserror_build_failure(patched_service: OscService) -> None:
    """send() upholds its 'never raises' contract even when send_message raises a
    BuildError-like (non OSError/ValueError) exception on an un-encodable arg."""
    svc = patched_service
    svc.send("/x", [1], host="127.0.0.1", port=9000)
    _FakeClient.instances[0].send_exc = RuntimeError("BuildError: un-encodable arg")
    svc.send("/x", [2], host="127.0.0.1", port=9000)  # must not raise
    assert svc.stats_for("127.0.0.1", 9000).total_errors == 1


@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_restart_listener_resets_state_when_thread_start_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the serve thread can't start, the bound listener is torn down and the
    listener fields reset, so a later stop_listener has nothing half-installed."""
    svc = OscService()
    port = find_free_udp_port()

    class _BadThread:
        def __init__(self, *a: Any, **k: Any) -> None: ...

        def start(self) -> None:
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(service_module.threading, "Thread", _BadThread)
    with pytest.raises(RuntimeError, match="can't start new thread"):
        svc.restart_listener(port=port, allowed_ips=())
    assert svc._listener is None
    assert svc._listener_thread is None
    svc.stop_listener()  # clean no-op – nothing half-installed
