# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for ConfigWebServer / _ThreadingWSGIServer / _QuietHandler.

All tests in this module are hermetic – no real HTTP sockets, no live Bottle
server.  Beacon I/O is neutralised via monkeypatch so the tests can exercise
lifecycle guards, provider wiring, and error paths without touching the
network.  Integration-style tests (against a live server) live in
``tests/test_web_server.py`` under the ``integration`` marker.
"""

from __future__ import annotations

import threading
import time

import pytest

import openfollow
import openfollow.web.discovery as discovery_module
from openfollow.web.server import ConfigWebServer

pytestmark = pytest.mark.unit

# A fixed port number used in place of a real free-port lookup. Unit tests
# never invoke ``ConfigWebServer.start()``, so the port is never bound – it
# only needs to be present on the instance for identity / introspection.
_UNIT_PLACEHOLDER_PORT = 18080

# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------


def _make_quiet_server(
    tmp_path,
    monkeypatch,
    *,
    port: int = _UNIT_PLACEHOLDER_PORT,
    local_ip: str = "",
    host: str = "127.0.0.1",
    **kwargs,
) -> ConfigWebServer:
    """Build a ConfigWebServer with beacon I/O neutralised so unit tests can
    exercise lifecycle and provider methods without touching the network."""
    monkeypatch.setattr(discovery_module.BeaconSender, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconSender, "stop", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "stop", lambda self: None)

    return ConfigWebServer(
        config_path=str(tmp_path / "config.toml"),
        host=host,
        port=port,
        system_name=kwargs.pop("system_name", "UnitSystem"),
        local_ip=local_ip,
        **kwargs,
    )


class _FakeSocket:
    """Minimal stand-in for ``socket.socket`` used by ``_can_bind`` tests.

    Implements just enough of the socket protocol (context-manager,
    ``setsockopt``, ``bind``) to exercise the production code path without
    touching the OS network stack. Pass ``bind_error`` to simulate an
    ``EADDRINUSE``-style failure.
    """

    def __init__(self, bind_error: OSError | None = None) -> None:
        self._bind_error = bind_error
        self.binds: list[tuple[str, int]] = []

    def setsockopt(self, *_args, **_kwargs) -> None:
        return None

    def bind(self, addr: tuple[str, int]) -> None:
        self.binds.append(addr)
        if self._bind_error is not None:
            raise self._bind_error

    def __enter__(self) -> _FakeSocket:
        return self

    def __exit__(self, *_exc) -> bool:
        return False


# ---------------------------------------------------------------------------
# ConfigWebServer – lifecycle guards and provider paths
# ---------------------------------------------------------------------------


def test_configwebserver_local_ip_fallback_when_not_on_interface(
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    monkeypatch.setattr(
        "openfollow.web.server.get_local_ipv4_addresses",
        lambda: {"10.99.99.99"},
    )
    monkeypatch.setattr(
        "openfollow.web.server.get_primary_local_ipv4",
        lambda default="127.0.0.1": "10.99.99.99",
    )

    with caplog.at_level("WARNING", logger="openfollow.web.server"):
        srv = _make_quiet_server(tmp_path, monkeypatch, local_ip="203.0.113.5")

    assert srv.local_ip == "10.99.99.99"
    assert any("not a local interface" in r.message for r in caplog.records)


def test_configwebserver_local_ip_preserved_when_matches_interface(
    tmp_path,
    monkeypatch,
) -> None:
    """When the configured local_ip matches an interface, it's used verbatim
    (line 116 branch – does NOT enter the fallback warning)."""
    monkeypatch.setattr(
        "openfollow.web.server.get_local_ipv4_addresses",
        lambda: {"10.0.0.55", "127.0.0.1"},
    )

    srv = _make_quiet_server(tmp_path, monkeypatch, local_ip="10.0.0.55")

    assert srv.local_ip == "10.0.0.55"


def test_configwebserver_log_ring_property_round_trip(tmp_path, monkeypatch) -> None:
    from openfollow.logging_setup import RingBufferLogHandler

    ring = RingBufferLogHandler(capacity=4)
    srv = _make_quiet_server(tmp_path, monkeypatch, log_ring=ring)
    assert srv.log_ring is ring
    # Default-None construction (the path the existing test fleet
    # has used for every other test).
    bare = _make_quiet_server(tmp_path, monkeypatch)
    assert bare.log_ring is None


def test_configwebserver_update_system_name_syncs_beacon(tmp_path, monkeypatch) -> None:
    srv = _make_quiet_server(tmp_path, monkeypatch)
    srv.update_system_name("Renamed")

    assert srv.system_name == "Renamed"
    assert srv._beacon_sender._packet.name == "Renamed"


def test_configwebserver_command_queue_proxies(tmp_path, monkeypatch) -> None:
    srv = _make_quiet_server(tmp_path, monkeypatch)

    # Button detection wizard.
    assert srv.is_button_detection_active() is False
    srv.request_button_detection()
    srv.set_button_detection_active(True)
    assert srv.is_button_detection_active() is True
    srv.set_button_detection_active(False)
    assert srv.is_button_detection_active() is False

    # Cancel forwards to the queue's cancel edge.
    srv.cancel_button_detection()
    assert srv._command_queue.consume_button_detection_cancel_requested() is True

    # Update workflow – queue a deb-release update; it occupies the
    # single in-flight slot.
    ok = srv.request_deb_update("svc")
    assert ok is True
    srv.set_update_status("running", message="installing", error="")
    status = srv.get_update_status()
    assert status["state"] == "running"
    assert status["message"] == "installing"

    # A second update request is rejected because the update started
    # above is still in flight – for both the deb-release and the
    # offline-upload path.
    assert srv.request_deb_update("svc") is False
    assert srv.request_local_update("svc", deb_path="/tmp/openfollow-update-x.deb") is False


def test_configwebserver_request_local_update_forwards_payload(tmp_path, monkeypatch) -> None:
    """request_local_update must forward deb_path to the queue and queue an
    offline install when idle."""
    srv = _make_quiet_server(tmp_path, monkeypatch)

    assert srv.request_local_update("svc", deb_path="/tmp/openfollow-update-x.deb") is True
    payload = srv._command_queue.consume_update_requested()
    assert payload is not None
    assert payload["kind"] == "deb-local"
    assert payload["service_name"] == "svc"
    assert payload["deb_path"] == "/tmp/openfollow-update-x.deb"


def test_configwebserver_providers_return_empty_when_unset(tmp_path, monkeypatch) -> None:
    srv = _make_quiet_server(tmp_path, monkeypatch)

    assert srv.get_runtime_stats() == {}
    assert srv.get_preview_snapshot() is None
    assert srv.get_zone_states() == []
    assert srv.get_marker_positions() == []
    assert srv.get_full_snapshot() is None
    assert srv.get_network_state() is None  #


def test_configwebserver_get_network_state_invokes_provider(
    tmp_path,
    monkeypatch,
) -> None:
    expected = {
        "interfaces": ["eth0"],
        "active_interface": "eth0",
        "method": "DHCP",
        "address": "10.0.0.5",
    }
    srv = _make_quiet_server(
        tmp_path,
        monkeypatch,
        network_state_provider=lambda: expected,
    )
    assert srv.get_network_state() == expected


def test_configwebserver_get_network_state_swallows_provider_exception(
    tmp_path,
    monkeypatch,
) -> None:

    def boom():
        raise RuntimeError("adapter offline")

    srv = _make_quiet_server(
        tmp_path,
        monkeypatch,
        network_state_provider=boom,
    )
    assert srv.get_network_state() is None


def test_configwebserver_providers_are_invoked_when_set(tmp_path, monkeypatch) -> None:
    """Provider callables must actually be called (lines 220, 226, 232, 238, 244)."""
    stats = {"fps": 30}
    snap = b"jpg-bytes"
    zones = [(0, True, 2), (1, False, 0)]
    markers = [(1, 12.5, -7.0)]
    full = b"full-jpg"

    srv = _make_quiet_server(
        tmp_path,
        monkeypatch,
        runtime_stats_provider=lambda: stats,
        preview_snapshot_provider=lambda: snap,
        zone_state_provider=lambda: zones,
        marker_positions_provider=lambda: markers,
        full_snapshot_provider=lambda: full,
    )

    assert srv.get_runtime_stats() == stats
    assert srv.get_preview_snapshot() == snap
    assert srv.get_zone_states() == zones
    assert srv.get_marker_positions() == markers
    assert srv.get_full_snapshot() == full


def test_configwebserver_get_local_peer_info_reflects_identity(
    tmp_path,
    monkeypatch,
) -> None:
    srv = _make_quiet_server(tmp_path, monkeypatch, system_name="Local")
    peer = srv.get_local_peer_info()

    assert peer.name == "Local"
    assert peer.web_port == srv.port
    assert peer.ip == srv.local_ip
    # Version must reflect the real package version, not the "0.1.0" placeholder.
    assert peer.version == openfollow.__version__
    # last_seen is time.time() – sanity-check monotonic direction rather than
    # an exact value, which would be flaky.
    assert peer.last_seen > 0.0


def test_configwebserver_beacon_advertises_real_version(tmp_path, monkeypatch) -> None:
    """The emitted discovery beacon must carry the real package version so
    peers, the Overview list, and the diagnostics bundle don't see "0.1.0"."""
    srv = _make_quiet_server(tmp_path, monkeypatch)

    # Round-trip the packet the sender serialises onto the wire.
    emitted = discovery_module.BeaconPacket.from_bytes(srv._beacon_sender._packet.to_bytes())

    assert emitted is not None
    assert emitted.version == openfollow.__version__


def test_configwebserver_can_bind_true_when_bind_succeeds(monkeypatch) -> None:
    from openfollow.web import server as server_mod

    created: list[_FakeSocket] = []

    def _factory(*_a, **_kw) -> _FakeSocket:
        sock = _FakeSocket()
        created.append(sock)
        return sock

    monkeypatch.setattr(server_mod.socket, "socket", _factory)

    assert ConfigWebServer._can_bind("127.0.0.1", 12345) is True
    assert created[-1].binds == [("127.0.0.1", 12345)]

    assert ConfigWebServer._can_bind("0.0.0.0", 12345) is True
    # Wildcard host must be rewritten to "" before the bind call.
    assert created[-1].binds == [("", 12345)]


def test_configwebserver_can_bind_false_when_bind_raises_oserror(monkeypatch) -> None:
    from openfollow.web import server as server_mod

    monkeypatch.setattr(
        server_mod.socket,
        "socket",
        lambda *_a, **_kw: _FakeSocket(bind_error=OSError("address already in use")),
    )

    assert ConfigWebServer._can_bind("127.0.0.1", 12345) is False


def test_configwebserver_start_short_circuits_when_thread_exists(
    tmp_path,
    monkeypatch,
) -> None:
    srv = _make_quiet_server(tmp_path, monkeypatch)

    sentinel_thread = object()
    srv._thread = sentinel_thread  # type: ignore[assignment]

    # Must not even attempt a bind check – verify by monkeypatching _can_bind
    # to blow up if called.
    def _boom(*a, **kw):
        raise AssertionError("_can_bind must not be called on second start")

    monkeypatch.setattr(srv, "_can_bind", _boom)

    srv.start()  # must early-return
    assert srv._thread is sentinel_thread


def test_configwebserver_start_refuses_when_no_port_can_bind(
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    srv = _make_quiet_server(tmp_path, monkeypatch)

    monkeypatch.setattr(srv, "_can_bind", lambda h, p: False)

    beacon_started = {"sender": False, "receiver": False}
    monkeypatch.setattr(
        srv._beacon_sender,
        "start",
        lambda: beacon_started.__setitem__("sender", True),
    )
    monkeypatch.setattr(
        srv._beacon_receiver,
        "start",
        lambda: beacon_started.__setitem__("receiver", True),
    )

    with caplog.at_level("ERROR", logger="openfollow.web.server"):
        srv.start()

    assert srv._thread is None
    assert srv._fallback_thread is None
    assert beacon_started == {"sender": False, "receiver": False}
    assert any("no fallback port" in r.message for r in caplog.records)


def test_configwebserver_hard_error_log_excludes_primary_from_fallback_list(
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    """Regression: when the operator configures the primary port to one of
    the fallback entries (e.g. 8080) and nothing binds, the hard-error log
    must not list that port as part of the "fallback port set" – the
    candidate loop explicitly skipped it, so listing it would be
    misleading."""
    from openfollow.web import server as server_mod

    primary = server_mod._FALLBACK_PORTS[0]
    srv = _make_quiet_server(tmp_path, monkeypatch, port=primary)
    monkeypatch.setattr(srv, "_can_bind", lambda h, p: False)

    with caplog.at_level("ERROR", logger="openfollow.web.server"):
        srv.start()

    [record] = [r for r in caplog.records if "no fallback port" in r.message]
    # The fallback list in the message must omit the primary port – the
    # remaining chain entries are what was actually tried.
    expected_chain = [p for p in server_mod._FALLBACK_PORTS if p != primary]
    expected_fragment = f"({', '.join(str(p) for p in expected_chain)})"
    assert expected_fragment in record.message
    assert f"({primary}, " not in record.message
    assert f", {primary})" not in record.message


def test_configwebserver_run_logs_oserror_and_clears_state(
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    srv = _make_quiet_server(tmp_path, monkeypatch)

    # Simulate a leftover thread handle so the finally-block cleanup is observable.
    srv._thread = "sentinel"  # type: ignore[assignment]
    srv._http_server = "alive"

    def _boom(*a, **kw):
        raise OSError("port 80 requires root")

    import wsgiref.simple_server as wsgi_mod

    monkeypatch.setattr(wsgi_mod, "make_server", _boom)

    with caplog.at_level("ERROR", logger="openfollow.web.server"):
        srv._run(srv._host, srv.port, "primary")

    assert srv._thread is None
    assert srv._http_server is None
    assert any("failed on" in r.message for r in caplog.records)


def test_configwebserver_run_logs_unexpected_exception(
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    srv = _make_quiet_server(tmp_path, monkeypatch)
    srv._thread = "sentinel"  # type: ignore[assignment]

    def _boom(*a, **kw):
        raise RuntimeError("unexpected")

    import wsgiref.simple_server as wsgi_mod

    monkeypatch.setattr(wsgi_mod, "make_server", _boom)

    with caplog.at_level("ERROR", logger="openfollow.web.server"):
        srv._run(srv._host, srv.port, "primary")

    assert srv._thread is None
    assert srv._http_server is None
    assert any("crashed" in r.message for r in caplog.records)


def test_configwebserver_start_spawns_primary_and_first_fallback(
    tmp_path,
    monkeypatch,
) -> None:
    from openfollow.web import server as server_mod

    srv = _make_quiet_server(tmp_path, monkeypatch, port=9999)
    monkeypatch.setattr(srv, "_can_bind", lambda h, p: True)
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)

    srv.start()

    assert srv._thread is not None
    assert srv._fallback_thread is not None
    # First fallback in the chain wins because it bound.
    assert srv._fallback_port == server_mod._FALLBACK_PORTS[0]
    assert srv._fallback_thread._args == (srv._host, server_mod._FALLBACK_PORTS[0], "fallback")


def test_configwebserver_start_falls_through_to_second_fallback_when_first_busy(
    tmp_path,
    monkeypatch,
) -> None:
    from openfollow.web import server as server_mod

    srv = _make_quiet_server(tmp_path, monkeypatch, port=9999)

    busy = {server_mod._FALLBACK_PORTS[0]}

    def _selective_bind(host: str, port: int) -> bool:
        return port not in busy

    monkeypatch.setattr(srv, "_can_bind", _selective_bind)
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)

    srv.start()

    assert srv._fallback_port == server_mod._FALLBACK_PORTS[1]
    assert srv._fallback_thread is not None


def test_configwebserver_start_skips_fallback_entry_matching_primary(
    tmp_path,
    monkeypatch,
) -> None:
    """If the operator configured the primary port to one of the fallback
    entries, that entry is skipped (no point binding the same address
    twice) and the next chain entry is used."""
    from openfollow.web import server as server_mod

    primary = server_mod._FALLBACK_PORTS[0]
    srv = _make_quiet_server(tmp_path, monkeypatch, port=primary)

    monkeypatch.setattr(srv, "_can_bind", lambda h, p: True)
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)

    srv.start()

    # 8080 is the primary; the fallback walker skips it and picks 2010.
    assert srv._fallback_port == server_mod._FALLBACK_PORTS[1]


def test_configwebserver_start_skips_all_fallbacks_when_busy(
    tmp_path,
    monkeypatch,
) -> None:
    """When every fallback port is taken but the primary still binds, the
    server runs on primary only and ``_fallback_port`` stays None."""
    from openfollow.web import server as server_mod

    srv = _make_quiet_server(tmp_path, monkeypatch, port=9999)

    busy = set(server_mod._FALLBACK_PORTS)

    def _selective_bind(host: str, port: int) -> bool:
        return port not in busy

    monkeypatch.setattr(srv, "_can_bind", _selective_bind)
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)

    srv.start()

    assert srv._thread is not None
    assert srv._fallback_thread is None
    assert srv._fallback_port is None


def test_configwebserver_start_uses_fallback_when_primary_fails(
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    from openfollow.web import server as server_mod

    srv = _make_quiet_server(tmp_path, monkeypatch, port=80)

    def _selective_bind(host: str, port: int) -> bool:
        return port != 80

    monkeypatch.setattr(srv, "_can_bind", _selective_bind)
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)

    with caplog.at_level("WARNING", logger="openfollow.web.server"):
        srv.start()

    assert srv._thread is None
    assert srv._fallback_thread is not None
    assert srv._fallback_port == server_mod._FALLBACK_PORTS[0]
    assert any("Primary web UI port 80 unavailable" in r.message for r in caplog.records)


def test_configwebserver_display_port_returns_primary_when_slot_live(
    tmp_path,
    monkeypatch,
) -> None:
    """``display_port`` reads the live HTTP server slots, not a flag set
    pre-bind. Primary slot non-None → return the configured port."""
    srv = _make_quiet_server(tmp_path, monkeypatch, port=9000)
    srv._http_server = object()
    srv._fallback_http_server = object()
    srv._fallback_port = 8080

    # Primary wins when both are live – that's the URL the operator
    # originally configured.
    assert srv.display_port == 9000


def test_configwebserver_display_port_returns_fallback_when_primary_slot_empty(
    tmp_path,
    monkeypatch,
) -> None:
    """Primary slot empty (failed to bind, or crashed mid-run) but fallback
    live → return the fallback port. This is the case the HUD needs to
    surface so the operator types a URL that actually works."""
    srv = _make_quiet_server(tmp_path, monkeypatch, port=80)
    srv._http_server = None
    srv._fallback_http_server = object()
    srv._fallback_port = 8080

    assert srv.display_port == 8080


def test_configwebserver_display_port_falls_back_to_primary_when_nothing_live(
    tmp_path,
    monkeypatch,
) -> None:
    """Both slots empty (never started, or fully stopped) → return the
    configured port. The URL won't work, but a blank HUD value is worse."""
    srv = _make_quiet_server(tmp_path, monkeypatch, port=80)
    srv._http_server = None
    srv._fallback_http_server = None
    srv._fallback_port = None

    assert srv.display_port == 80


def test_configwebserver_display_port_ignores_stale_primary_bound_flag(
    tmp_path,
    monkeypatch,
) -> None:
    """Regression: with the old ``_primary_bound`` flag, a primary thread
    that crashed in ``_run`` would leave the flag stuck at True and the
    HUD would lie about reachability. Deriving from the live slot fixes
    this – even if some other state suggests primary "should" be up, the
    slot being None is authoritative."""
    srv = _make_quiet_server(tmp_path, monkeypatch, port=80)
    srv._http_server = None  # primary thread crashed and cleared its slot
    srv._fallback_http_server = object()
    srv._fallback_port = 8080

    assert srv.display_port == 8080


def test_configwebserver_start_is_idempotent_in_fallback_only_mode(
    tmp_path,
    monkeypatch,
) -> None:
    """Regression: the old guard checked only ``_thread``. When the primary
    failed and only the fallback was running, a second ``start()`` call
    would re-probe ``_can_bind`` and could log a spurious hard error.
    The guard must trip on either thread."""
    srv = _make_quiet_server(tmp_path, monkeypatch)

    sentinel_thread = object()
    srv._fallback_thread = sentinel_thread  # type: ignore[assignment]
    # Primary thread slot stays None – the only liveness signal is fallback.

    def _boom(*a, **kw):
        raise AssertionError("_can_bind must not be called when fallback is alive")

    monkeypatch.setattr(srv, "_can_bind", _boom)

    srv.start()  # must early-return without re-probing
    assert srv._fallback_thread is sentinel_thread


def test_configwebserver_run_clears_fallback_port_on_finally(
    tmp_path,
    monkeypatch,
) -> None:
    srv = _make_quiet_server(tmp_path, monkeypatch)
    srv._fallback_port = 8080
    srv._fallback_thread = "sentinel"  # type: ignore[assignment]

    class _FakeSrv:
        def serve_forever(self) -> None:
            return None  # immediate return triggers finally

    import wsgiref.simple_server as wsgi_mod

    monkeypatch.setattr(wsgi_mod, "make_server", lambda *a, **kw: _FakeSrv())

    srv._run(srv._host, 8080, "fallback")

    assert srv._fallback_port is None
    assert srv._fallback_http_server is None
    assert srv._fallback_thread is None


def test_configwebserver_run_clears_fallback_state_on_oserror(
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    srv = _make_quiet_server(tmp_path, monkeypatch)
    srv._fallback_thread = "sentinel"  # type: ignore[assignment]
    srv._fallback_http_server = "alive"

    def _boom(*a, **kw):
        raise OSError("port unavailable")

    import wsgiref.simple_server as wsgi_mod

    monkeypatch.setattr(wsgi_mod, "make_server", _boom)

    with caplog.at_level("ERROR", logger="openfollow.web.server"):
        srv._run(srv._host, 8080, "fallback")

    assert srv._fallback_thread is None
    assert srv._fallback_http_server is None


def test_configwebserver_run_assigns_to_fallback_slot(tmp_path, monkeypatch) -> None:
    srv = _make_quiet_server(tmp_path, monkeypatch)

    captured: dict[str, object] = {}

    class _FakeSrv:
        def serve_forever(self) -> None:
            captured["primary"] = srv._http_server
            captured["fallback"] = srv._fallback_http_server

    import wsgiref.simple_server as wsgi_mod

    monkeypatch.setattr(wsgi_mod, "make_server", lambda *a, **kw: _FakeSrv())

    srv._run(srv._host, 8080, "fallback")

    assert captured["primary"] is None
    assert isinstance(captured["fallback"], _FakeSrv)
    # The finally block clears the slot after serve_forever returns.
    assert srv._fallback_http_server is None


def test_needs_loopback_listener_only_for_pinned_non_loopback_host(tmp_path, monkeypatch) -> None:
    cases = [
        ("0.0.0.0", False),
        ("", False),
        ("127.0.0.1", False),
        ("127.0.0.5", False),
        ("::1", False),
        ("10.0.0.5", True),
        ("192.168.1.20", True),
    ]
    for host, expected in cases:
        srv = _make_quiet_server(tmp_path, monkeypatch, host=host)
        assert srv._needs_loopback_listener() is expected, host


def test_start_spawns_loopback_listener_for_pinned_host(tmp_path, monkeypatch) -> None:
    srv = _make_quiet_server(tmp_path, monkeypatch, port=9999, host="10.0.0.5")
    monkeypatch.setattr(srv, "_can_bind", lambda h, p: True)
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)

    srv.start()

    assert srv._loopback_thread is not None
    # Loopback listener serves 127.0.0.1 on the live (primary) port.
    assert srv._loopback_thread._args == ("127.0.0.1", 9999, "loopback")


def test_run_assigns_to_loopback_slot(tmp_path, monkeypatch) -> None:
    srv = _make_quiet_server(tmp_path, monkeypatch, host="10.0.0.5")

    captured: dict[str, object] = {}

    class _FakeSrv:
        def serve_forever(self) -> None:
            captured["loopback"] = srv._loopback_http_server

    import wsgiref.simple_server as wsgi_mod

    monkeypatch.setattr(wsgi_mod, "make_server", lambda *a, **kw: _FakeSrv())

    srv._run("127.0.0.1", 9999, "loopback")

    assert isinstance(captured["loopback"], _FakeSrv)
    assert srv._loopback_http_server is None  # finally clears the slot


def test_stop_joins_loopback_thread(tmp_path, monkeypatch) -> None:
    srv = _make_quiet_server(tmp_path, monkeypatch, host="10.0.0.5")

    shutdown_count = {"n": 0}
    join_count = {"n": 0}

    class _FakeHttp:
        def shutdown(self) -> None:
            shutdown_count["n"] += 1

    class _FakeThread:
        def join(self, timeout: float) -> None:
            join_count["n"] += 1

    srv._loopback_http_server = _FakeHttp()
    srv._loopback_thread = _FakeThread()
    srv.stop()

    assert shutdown_count["n"] == 1
    assert join_count["n"] == 1
    assert srv._loopback_thread is None


def test_configwebserver_stop_joins_fallback_thread(tmp_path, monkeypatch) -> None:
    """stop() must shut down the fallback HTTP server and join its thread."""
    srv = _make_quiet_server(tmp_path, monkeypatch)

    shutdown_count = {"n": 0}
    join_count = {"n": 0}

    class _FakeHttp:
        def shutdown(self) -> None:
            shutdown_count["n"] += 1

    class _FakeThread:
        def join(self, timeout: float) -> None:
            join_count["n"] += 1

    srv._fallback_http_server = _FakeHttp()
    srv._fallback_thread = _FakeThread()  # type: ignore[assignment]

    srv.stop()

    assert shutdown_count["n"] == 1
    assert join_count["n"] == 1
    assert srv._fallback_thread is None


def test_configwebserver_stop_joins_existing_thread(tmp_path, monkeypatch) -> None:
    srv = _make_quiet_server(tmp_path, monkeypatch)

    shutdown_called = {"n": 0}
    joined = {"n": 0}

    class _FakeHttp:
        def shutdown(self) -> None:
            shutdown_called["n"] += 1

    class _FakeThread:
        def __init__(self) -> None:
            self.is_alive_calls = 0

        def join(self, timeout: float) -> None:
            joined["n"] += 1

    srv._http_server = _FakeHttp()
    srv._thread = _FakeThread()  # type: ignore[assignment]

    srv.stop()

    assert shutdown_called["n"] == 1
    assert joined["n"] == 1
    assert srv._thread is None


def test_configwebserver_stop_without_http_server_still_stops_beacons(
    tmp_path,
    monkeypatch,
) -> None:
    srv = _make_quiet_server(tmp_path, monkeypatch)

    sender_stopped = {"n": 0}
    receiver_stopped = {"n": 0}
    monkeypatch.setattr(
        srv._beacon_sender,
        "stop",
        lambda: sender_stopped.__setitem__("n", sender_stopped["n"] + 1),
    )
    monkeypatch.setattr(
        srv._beacon_receiver,
        "stop",
        lambda: receiver_stopped.__setitem__("n", receiver_stopped["n"] + 1),
    )

    srv.stop()  # nothing to join, nothing to shut down

    assert sender_stopped["n"] == 1
    assert receiver_stopped["n"] == 1


def test_configwebserver_on_peer_discovered_logs(tmp_path, monkeypatch, caplog) -> None:
    srv = _make_quiet_server(tmp_path, monkeypatch)

    from openfollow.web.discovery import PeerInfo

    peer = PeerInfo(
        name="Remote",
        ip="10.1.2.3",
        web_port=9000,
        version="0.1.0",
        last_seen=time.time(),
    )

    with caplog.at_level("INFO", logger="openfollow.web.server"):
        srv._on_peer_discovered(peer)

    assert any("Remote" in r.message and "10.1.2.3:9000" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _ThreadingWSGIServer – concurrency overflow path
# ---------------------------------------------------------------------------


def test_threading_wsgi_server_returns_503_when_semaphore_full(monkeypatch) -> None:
    from openfollow.web import server as server_mod

    # Swap in a pre-exhausted semaphore so the first acquire attempt fails.
    exhausted = threading.BoundedSemaphore(1)
    exhausted.acquire()  # leaves zero permits
    monkeypatch.setattr(server_mod, "_request_semaphore", exhausted)

    sent: list[bytes] = []
    shutdowns: list[object] = []

    class _FakeRequest:
        def sendall(self, data: bytes) -> None:
            sent.append(data)

    srv = server_mod._ThreadingWSGIServer.__new__(server_mod._ThreadingWSGIServer)
    srv.shutdown_request = lambda req: shutdowns.append(req)  # type: ignore[assignment]

    srv.process_request_thread(_FakeRequest(), ("127.0.0.1", 40000))

    assert len(sent) == 1
    assert sent[0].startswith(b"HTTP/1.1 503")
    assert len(shutdowns) == 1


def test_threading_wsgi_server_swallows_sendall_oserror(monkeypatch) -> None:
    from openfollow.web import server as server_mod

    exhausted = threading.BoundedSemaphore(1)
    exhausted.acquire()
    monkeypatch.setattr(server_mod, "_request_semaphore", exhausted)

    class _FlakyRequest:
        def sendall(self, data: bytes) -> None:
            raise OSError("broken pipe")

    shutdowns: list[object] = []
    srv = server_mod._ThreadingWSGIServer.__new__(server_mod._ThreadingWSGIServer)
    srv.shutdown_request = lambda req: shutdowns.append(req)  # type: ignore[assignment]

    # Must not propagate the OSError.
    srv.process_request_thread(_FlakyRequest(), ("127.0.0.1", 40001))

    assert len(shutdowns) == 1


def test_threading_wsgi_server_swallows_shutdown_exception(monkeypatch) -> None:
    from openfollow.web import server as server_mod

    exhausted = threading.BoundedSemaphore(1)
    exhausted.acquire()
    monkeypatch.setattr(server_mod, "_request_semaphore", exhausted)

    class _FakeRequest:
        def sendall(self, data: bytes) -> None:
            pass

    srv = server_mod._ThreadingWSGIServer.__new__(server_mod._ThreadingWSGIServer)

    def _boom(req):
        raise RuntimeError("transport went away")

    srv.shutdown_request = _boom  # type: ignore[assignment]

    # Must not raise – exception is swallowed at lines 80-81.
    srv.process_request_thread(_FakeRequest(), ("127.0.0.1", 40002))


def test_threading_wsgi_server_counts_rejection(monkeypatch) -> None:
    """A 503 rejection must bump the module-level rejection counter so the
    diagnostics bundle reports the cap being hit instead of a constant 0."""
    from openfollow.web import server as server_mod

    exhausted = threading.BoundedSemaphore(1)
    exhausted.acquire()  # leaves zero permits
    monkeypatch.setattr(server_mod, "_request_semaphore", exhausted)
    # Reset the cumulative counter so the assertion is order-independent.
    monkeypatch.setattr(server_mod, "_request_semaphore_rejections", 0)

    class _FakeRequest:
        def sendall(self, data: bytes) -> None:
            pass

    srv = server_mod._ThreadingWSGIServer.__new__(server_mod._ThreadingWSGIServer)
    srv.shutdown_request = lambda req: None  # type: ignore[assignment]

    srv.process_request_thread(_FakeRequest(), ("127.0.0.1", 40010))
    srv.process_request_thread(_FakeRequest(), ("127.0.0.1", 40011))

    assert server_mod._request_semaphore_rejections == 2


def test_configwebserver_rejection_count_surfaces_via_property(tmp_path, monkeypatch) -> None:
    """The ``request_semaphore_rejections`` property must report the live
    counter, not a placeholder zero, so diagnostics reflect rejections."""
    from openfollow.web import server as server_mod

    monkeypatch.setattr(server_mod, "_request_semaphore_rejections", 0)
    srv = _make_quiet_server(tmp_path, monkeypatch)
    assert srv.request_semaphore_rejections == 0

    exhausted = threading.BoundedSemaphore(1)
    exhausted.acquire()
    monkeypatch.setattr(server_mod, "_request_semaphore", exhausted)

    class _FakeRequest:
        def sendall(self, data: bytes) -> None:
            pass

    wsgi = server_mod._ThreadingWSGIServer.__new__(server_mod._ThreadingWSGIServer)
    wsgi.shutdown_request = lambda req: None  # type: ignore[assignment]
    wsgi.process_request_thread(_FakeRequest(), ("127.0.0.1", 40012))

    assert srv.request_semaphore_rejections == 1


def test_configwebserver_run_shuts_down_server_bound_after_stop(tmp_path, monkeypatch) -> None:
    """Regression: if ``stop()`` runs in the window before ``_run`` publishes
    its HTTP-server slot, the freshly bound server must still be released
    rather than left squatting the port. With ``_stopping`` already set,
    ``_run`` must close the server and never enter ``serve_forever``."""
    srv = _make_quiet_server(tmp_path, monkeypatch)
    srv._thread = "sentinel"  # type: ignore[assignment]
    srv._stopping = True  # stop() already ran and missed the slot

    closed = {"n": 0}
    served = {"n": 0}

    class _FakeSrv:
        def serve_forever(self) -> None:
            served["n"] += 1

        def server_close(self) -> None:
            closed["n"] += 1

    import wsgiref.simple_server as wsgi_mod

    monkeypatch.setattr(wsgi_mod, "make_server", lambda *a, **kw: _FakeSrv())

    srv._run(srv._host, srv.port, "primary")

    assert served["n"] == 0  # must NOT start serving
    assert closed["n"] == 1  # port released directly
    assert srv._http_server is None  # finally cleared the slot
    assert srv._thread is None


def test_configwebserver_stop_flags_stopping_before_snapshot(tmp_path, monkeypatch) -> None:
    """stop() must set ``_stopping`` so a worker that publishes its slot after
    stop() began still self-closes instead of holding the port."""
    srv = _make_quiet_server(tmp_path, monkeypatch)

    assert srv._stopping is False
    srv.stop()
    assert srv._stopping is True


def test_configwebserver_start_clears_stopping_flag(tmp_path, monkeypatch) -> None:
    """A stop()+start() cycle must clear ``_stopping`` so the new workers
    don't immediately self-shutdown."""
    srv = _make_quiet_server(tmp_path, monkeypatch, port=9999)
    srv._stopping = True

    monkeypatch.setattr(srv, "_can_bind", lambda h, p: True)
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)

    srv.start()

    assert srv._stopping is False


def test_quiet_handler_log_request_is_noop() -> None:
    from openfollow.web.server import _QuietHandler

    handler = _QuietHandler.__new__(_QuietHandler)
    # Must not raise and must return None regardless of arguments.
    assert handler.log_request() is None
    assert handler.log_request(200, "100") is None
    assert handler.log_request(code=500, size="0") is None


# ---------------------------------------------------------------------------
# get_local_peer_info – live local-IP refresh after a runtime IP change
# ---------------------------------------------------------------------------


def test_get_local_peer_info_adopts_live_ip_change(tmp_path, monkeypatch) -> None:
    """A static→DHCP switch (new address, same interface) is picked up without
    a restart: the self-row IP and both beacon interfaces follow the provider."""
    monkeypatch.setattr(
        "openfollow.web.server.get_local_ipv4_addresses",
        lambda: {"10.0.0.1", "10.0.0.2"},
    )
    current = {"ip": "10.0.0.1"}
    srv = _make_quiet_server(
        tmp_path,
        monkeypatch,
        local_ip="10.0.0.1",
        local_ip_provider=lambda: current["ip"],
    )

    assert srv.get_local_peer_info().ip == "10.0.0.1"

    current["ip"] = "10.0.0.2"
    srv._local_ip_refresh_ts -= 1000.0  # elapse the refresh throttle window
    assert srv.get_local_peer_info().ip == "10.0.0.2"
    assert srv.local_ip == "10.0.0.2"
    assert srv._beacon_sender._iface_ip == "10.0.0.2"
    assert srv._beacon_receiver._iface_ip == "10.0.0.2"


@pytest.mark.parametrize("unresolved", ["", "127.0.0.1"])
def test_get_local_peer_info_keeps_ip_when_provider_unresolved(
    tmp_path,
    monkeypatch,
    unresolved: str,
) -> None:
    """An offline / loopback resolution must not downgrade the last known IP."""
    monkeypatch.setattr(
        "openfollow.web.server.get_local_ipv4_addresses",
        lambda: {"10.0.0.1"},
    )
    srv = _make_quiet_server(
        tmp_path,
        monkeypatch,
        local_ip="10.0.0.1",
        local_ip_provider=lambda: unresolved,
    )

    assert srv.get_local_peer_info().ip == "10.0.0.1"
    assert srv._beacon_sender._iface_ip == "10.0.0.1"


def test_get_local_peer_info_without_provider_uses_startup_ip(tmp_path, monkeypatch) -> None:
    """No provider wired (e.g. test harness) keeps the boot-time resolution."""
    monkeypatch.setattr(
        "openfollow.web.server.get_local_ipv4_addresses",
        lambda: {"10.0.0.1"},
    )
    srv = _make_quiet_server(tmp_path, monkeypatch, local_ip="10.0.0.1")

    assert srv.get_local_peer_info().ip == "10.0.0.1"


def test_get_local_peer_info_survives_provider_exception(tmp_path, monkeypatch, caplog) -> None:
    """A raising provider is logged and ignored – the self-row stays usable."""
    monkeypatch.setattr(
        "openfollow.web.server.get_local_ipv4_addresses",
        lambda: {"10.0.0.1"},
    )

    def _boom() -> str:
        raise RuntimeError("nic enumeration failed")

    srv = _make_quiet_server(
        tmp_path,
        monkeypatch,
        local_ip="10.0.0.1",
        local_ip_provider=_boom,
    )

    with caplog.at_level("ERROR", logger="openfollow.web.server"):
        peer = srv.get_local_peer_info()

    assert peer.ip == "10.0.0.1"
    assert any("local_ip provider raised" in r.message for r in caplog.records)


def test_get_local_peer_info_throttles_ip_refresh(tmp_path, monkeypatch) -> None:
    """The live IP refresh runs on request paths; it must re-resolve at most
    once per TTL, not on every call (the provider enumerates interfaces)."""
    monkeypatch.setattr(
        "openfollow.web.server.get_local_ipv4_addresses",
        lambda: {"10.0.0.1", "10.0.0.2"},
    )
    calls = {"n": 0}

    def _provider() -> str:
        calls["n"] += 1
        return "10.0.0.1"

    srv = _make_quiet_server(tmp_path, monkeypatch, local_ip="10.0.0.1", local_ip_provider=_provider)

    srv.get_local_peer_info()
    assert calls["n"] == 1  # first call resolves
    srv.get_local_peer_info()
    assert calls["n"] == 1  # within TTL -> throttled, provider not called again

    # Simulate the TTL elapsing -> the next call re-resolves.
    srv._local_ip_refresh_ts -= 1000.0
    srv.get_local_peer_info()
    assert calls["n"] == 2
