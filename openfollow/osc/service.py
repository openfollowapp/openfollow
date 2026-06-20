# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unified OSC service – single entry point for all OSC traffic.

Outbound: a shared client cache keyed on ``(host, port, protocol, framing)``.
Inbound: a single UDP listener with a filtered allowlist and a ``pythonosc``
dispatcher that subscribers attach patterns to.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from openfollow.configuration import VALID_OSC_FRAMINGS as _VALID_OSC_FRAMINGS_TUPLE
from openfollow.osc.transport import TcpOscSender

logger = logging.getLogger(__name__)

try:
    from pythonosc.dispatcher import Dispatcher
    from pythonosc.osc_server import BlockingOSCUDPServer
    from pythonosc.udp_client import SimpleUDPClient

    _PYTHONOSC_AVAILABLE = True
# pragma: no cover – python-osc is a hard pyproject dependency; the
# import-failure arm only fires for installs that strip it out.
except ImportError:  # pragma: no cover
    _PYTHONOSC_AVAILABLE = False


_VALID_PROTOCOLS: frozenset[str] = frozenset({"udp", "tcp"})
# Frozenset wrapper for O(1) membership on the hot ``send`` path; the
# configuration tuple is the canonical form the validation registry iterates.
_VALID_FRAMINGS: frozenset[str] = frozenset(_VALID_OSC_FRAMINGS_TUPLE)
# UDP rows pin to this framing in the cache key so the key shape stays a
# uniform 4-tuple across transports (framing is a TCP-only concern).
_UDP_FRAMING_PIN = "length_prefix"


def _cache_framing(protocol: str, framing: str) -> str:
    """Normalise the framing component of the cache key.

    UDP rows pin to ``_UDP_FRAMING_PIN`` so UDP rows differing only in
    framing share one cached client. TCP rows pass through unchanged.
    Unknown framings fall back to ``"slip"`` to keep the cache key in sync
    with the sender's actual framing.
    """
    if protocol != "tcp":
        return _UDP_FRAMING_PIN
    if framing not in _VALID_FRAMINGS:
        return "slip"
    return framing


# Multicast send TTL – 1 keeps datagrams on the local segment (the show
# network); the deployment would raise it only to route OSC across subnets.
_MULTICAST_TTL = 1


def _udp_dest_class(host: str) -> str:
    """Classify a UDP destination as multicast / broadcast / unicast by IP."""
    try:
        packed = socket.inet_aton(host)
    except OSError:
        return "unicast"  # hostname or non-IPv4 literal
    if 224 <= packed[0] <= 239:
        return "multicast"
    if host == "255.255.255.255" or packed[3] == 255:
        return "broadcast"
    return "unicast"


# Hard cap on the one-time DNS lookup for a hostname target. The 60 Hz
# scheduler thread is the typical caller of ``_make_client``; an
# unbounded ``getaddrinfo`` on a slow/unreachable resolver would stall
# every other transmitter row dispatched on that thread until it returns.
_RESOLVE_TIMEOUT_S = 1.0
# Remember a failed/timed-out lookup so a misconfigured host doesn't respawn a
# resolver thread on every 60 Hz send. Keyed by host (bounded by config rows).
_RESOLVE_NEG_TTL_S = 30.0
_resolve_failures: dict[str, float] = {}
_resolve_lock = threading.Lock()


def _resolve_host(host: str) -> str:
    """Resolve ``host`` to an IPv4 literal, bounding the DNS lookup.

    Literals pass through. A hostname resolves on a daemon thread capped at
    ``_RESOLVE_TIMEOUT_S``; a timeout/failure raises ``OSError`` and is cached
    for ``_RESOLVE_NEG_TTL_S`` so repeats don't respawn threads.
    """
    try:
        socket.inet_aton(host)
        return host  # already an IPv4 literal – no lookup needed
    except OSError:
        pass
    now = time.monotonic()
    with _resolve_lock:
        until = _resolve_failures.get(host)
        if until is not None and now < until:
            raise OSError(f"DNS lookup for {host!r} recently failed")
    result: list[str] = []
    error: list[BaseException] = []

    def _lookup() -> None:
        try:
            result.append(socket.gethostbyname(host))
        except BaseException as exc:  # noqa: BLE001 – relayed to the caller below
            error.append(exc)

    worker = threading.Thread(target=_lookup, name="OscDns", daemon=True)
    worker.start()
    worker.join(_RESOLVE_TIMEOUT_S)
    if worker.is_alive():
        with _resolve_lock:
            _resolve_failures[host] = now + _RESOLVE_NEG_TTL_S
        raise OSError(f"DNS lookup for {host!r} timed out after {_RESOLVE_TIMEOUT_S:g}s")
    if error:
        with _resolve_lock:
            _resolve_failures[host] = now + _RESOLVE_NEG_TTL_S
        raise OSError(f"DNS lookup for {host!r} failed: {error[0]}")
    with _resolve_lock:
        _resolve_failures.pop(host, None)
    return result[0]


def _make_client(
    host: str,
    port: int,
    protocol: str,
    framing: str,
) -> Any:
    """Construct the cached client for ``protocol``.

    UDP: ``SimpleUDPClient``, socket opened eagerly with the broadcast/multicast
    options the destination needs (else broadcast sends raise ``EACCES`` and
    multicast goes out with implicit options). TCP: ``TcpOscSender`` wired with
    ``framing``, socket opened lazily on first ``send_message``.

    A hostname target is resolved to an IPv4 literal first via
    ``_resolve_host`` so the per-frame send loop never blocks on an unbounded
    DNS lookup.
    """
    if protocol == "tcp":
        return TcpOscSender(host, port, framing)
    # pragma: no branch – protocol is validated against ``_VALID_PROTOCOLS``
    # before reaching here, so the only remaining value is ``"udp"``.
    dest = _udp_dest_class(host)
    resolved = _resolve_host(host)
    client = SimpleUDPClient(resolved, port, allow_broadcast=dest == "broadcast")
    if dest == "multicast":
        sock = client._sock
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, _MULTICAST_TTL)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    return client


def _close_client(client: Any) -> None:
    """Close a cached client of either transport. UDP exposes only
    ``_sock``; ``TcpOscSender.close()`` also joins the reader thread."""
    if isinstance(client, TcpOscSender):
        client.close()
        return
    sock = getattr(client, "_sock", None)
    if sock is not None:
        try:
            sock.close()
        except OSError:
            pass


# Subscriber callbacks receive ``(address, *args)`` per pythonosc's
# Dispatcher contract; args type unconstrained (handlers discriminate per-pattern).
OscHandler = Callable[..., None]


@dataclass
class ClientStats:
    """Per-target outbound stats."""

    total_sent: int = 0
    total_errors: int = 0
    last_error: str = ""


@dataclass
class _ClientEntry:
    """Cached OSC client for a single ``(host, port, protocol, framing)`` target.

    ``framing`` is meaningful for TCP only (``"slip"`` / ``"length_prefix"``);
    UDP entries pin to ``"length_prefix"`` to keep the key shape uniform.
    """

    client: Any
    stats: ClientStats = field(default_factory=ClientStats)


# pragma: no branch – pythonosc is a pyproject-pinned dependency, so the
# False arm of the availability check would only trigger in a stripped
# install (same reasoning as the import guard above).
if _PYTHONOSC_AVAILABLE:  # pragma: no branch

    class _GuardedDispatcher(Dispatcher):
        """Dispatcher whose map mutation and dispatch iteration share one lock.

        ``pythonosc.Dispatcher`` iterates ``self._map.items()`` in
        ``handlers_for_address`` without any lock, while ``map``/``unmap``
        mutate that ``defaultdict``. A ``subscribe`` adding a new pattern
        (new dict key) concurrent with an in-flight dispatch raises
        ``RuntimeError: dictionary changed size during iteration``.

        Mutations hold ``_map_lock``; ``handlers_for_address`` snapshots the
        matching handlers under the same lock and yields the snapshot
        afterwards, so the lock is never held across handler invocation.
        """

        def __init__(self) -> None:
            super().__init__()
            self._map_lock = threading.Lock()

        def map(self, address: str, handler: Any, *args: Any, **kwargs: Any) -> Any:
            with self._map_lock:
                return super().map(address, handler, *args, **kwargs)

        def unmap(self, address: str, handler: Any, *args: Any, **kwargs: Any) -> None:
            with self._map_lock:
                super().unmap(address, handler, *args, **kwargs)

        def handlers_for_address(self, address_pattern: str) -> Any:
            with self._map_lock:
                handlers = list(super().handlers_for_address(address_pattern))
            return iter(handlers)

    class _FilteredOSCUDPServer(BlockingOSCUDPServer):
        """OSC UDP server with an optional source-IP allowlist.

        ``verify_request`` runs before dispatch; returning False drops the
        packet with no handler invoked. An empty allowlist accepts everything.
        """

        def __init__(
            self,
            server_address: tuple[str, int],
            dispatcher: Dispatcher,
            allowed_ips: frozenset[str],
        ) -> None:
            super().__init__(server_address, dispatcher)
            self._allowed_ips = allowed_ips

        def verify_request(
            self,
            request: Any,
            client_address: tuple[str, int] | str,
        ) -> bool:
            if not self._allowed_ips:
                return True
            # UDP always yields a (host, port) tuple; the supertype allows a
            # ``str`` (unix-socket) address – treat that as not-in-allowlist
            # so verification fails closed.
            if isinstance(client_address, tuple) and client_address[0] in self._allowed_ips:
                return True
            host = client_address[0] if isinstance(client_address, tuple) else client_address
            logger.debug(
                "Dropped OSC packet from %s – not in allowed_sender_ips.",
                host,
            )
            return False


class OscService:
    """Process-wide OSC service. The constructor opens no sockets.

    Send-side: caches one client per ``(host, port, protocol, framing)``
    target. ``send()`` never raises – failures are caught, rate-limited in
    the log, and counted in per-target ``ClientStats``.

    Receive-side: a single ``BlockingOSCUDPServer`` (when started) hosts one
    ``Dispatcher``. ``subscribe``/``unsubscribe`` mutate the dispatcher
    mapping; mappings survive listener restarts, so callers can subscribe
    before the listener starts and stay subscribed across restarts.
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, int, str, str], _ClientEntry] = {}
        self._cache_lock = threading.Lock()

        self._dispatcher: Any = _GuardedDispatcher() if _PYTHONOSC_AVAILABLE else None
        self._subscriptions: dict[str, OscHandler] = {}
        self._listener: Any = None
        self._listener_thread: threading.Thread | None = None
        self._listener_port: int | None = None
        self._listener_allowed_ips: frozenset[str] = frozenset()
        # Optional IPv4 multicast group the listener joins; empty = off.
        # Part of the idempotency key so a group change triggers a rebind.
        self._listener_multicast_group: str = ""
        # Whether the live socket actually joined that group – a failed
        # IP_ADD_MEMBERSHIP is non-fatal, so "requested" can differ from "joined".
        self._listener_multicast_joined: bool = False
        self._listener_lock = threading.Lock()

        self._missing_dep_warned = False

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------

    def send(
        self,
        address: str,
        args: Sequence[Any] = (),
        *,
        host: str,
        port: int,
        protocol: str = "udp",
        framing: str = "slip",
    ) -> None:
        """Send a single OSC message.

        Empty ``address`` is silently dropped. ``host``/``port`` must be
        valid (non-empty / >0); callers resolve any default fallback first.
        ``framing`` selects the TCP wire framing (ignored for UDP); invalid
        values fall back to ``"slip"`` with a warning.
        """
        if not address:
            return
        if not host or port <= 0:
            return
        if protocol not in _VALID_PROTOCOLS:
            logger.warning(
                "OSC send: unknown protocol %r (expected 'udp' or 'tcp')",
                protocol,
            )
            return
        # Framing matters only for TCP; restrict the validate-and-warn to
        # ``protocol == "tcp"`` so a UDP row with a stray framing value stays
        # silent (``_get_or_create_client`` normalises the cache key anyway).
        if protocol == "tcp" and framing not in _VALID_FRAMINGS:
            logger.warning(
                "OSC send: unknown TCP framing %r (expected 'slip' or 'length_prefix') – defaulting to 'slip'",
                framing,
            )
            framing = "slip"
        if not _PYTHONOSC_AVAILABLE:
            if not self._missing_dep_warned:
                logger.warning("python-osc not installed – OSC output disabled. Run: pip install python-osc")
                self._missing_dep_warned = True
            return

        entry = self._get_or_create_client(host, port, protocol, framing)
        if entry is None:
            return
        # Broad catch upholds the documented "never raises" contract: pythonosc
        # raises BuildError (not OSError/ValueError) on un-encodable args.
        try:
            entry.client.send_message(address, list(args))
        except Exception as exc:
            with self._cache_lock:
                entry.stats.total_errors += 1
                entry.stats.last_error = str(exc)
                errors = entry.stats.total_errors
            # Throttle: log the first few, then every 100th, so a
            # permanently-down receiver doesn't flood the log.
            if errors <= 5 or errors % 100 == 0:
                logger.warning(
                    "OSC send to %s://%s:%d failed (%d errors): %s",
                    protocol,
                    host,
                    port,
                    errors,
                    exc,
                )
            return
        with self._cache_lock:
            entry.stats.total_sent += 1

    def evict(
        self,
        host: str,
        port: int,
        protocol: str = "udp",
        framing: str = "slip",
    ) -> None:
        """Close and drop the cached client for a target, if any."""
        key = (host, int(port), protocol, _cache_framing(protocol, framing))
        with self._cache_lock:
            entry = self._cache.pop(key, None)
        if entry is None:
            return
        _close_client(entry.client)

    def shutdown_clients(self) -> None:
        """Drain the entire client cache. Called at app shutdown."""
        with self._cache_lock:
            entries = list(self._cache.values())
            self._cache.clear()
        for entry in entries:
            _close_client(entry.client)

    def stats_for(
        self,
        host: str,
        port: int,
        protocol: str = "udp",
        framing: str = "slip",
    ) -> ClientStats:
        """Return a snapshot of per-target stats; empty stats for an
        unknown target."""
        key = (host, int(port), protocol, _cache_framing(protocol, framing))
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is None:
                return ClientStats()
            return ClientStats(
                total_sent=entry.stats.total_sent,
                total_errors=entry.stats.total_errors,
                last_error=entry.stats.last_error,
            )

    def _get_or_create_client(
        self,
        host: str,
        port: int,
        protocol: str,
        framing: str,
    ) -> _ClientEntry | None:
        cache_framing = _cache_framing(protocol, framing)
        key = (host, int(port), protocol, cache_framing)
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is not None:
                return entry
        try:
            client = _make_client(host, port, protocol, cache_framing)
        except (OSError, ValueError) as exc:
            # OSError: socket/DNS failure. ValueError: malformed host string.
            logger.error(
                "Failed to create OSC client for %s://%s:%d – %s",
                protocol,
                host,
                port,
                exc,
            )
            return None
        new_entry = _ClientEntry(client=client)
        with self._cache_lock:
            # Re-check under lock to resolve a duplicate-creation race.
            existing = self._cache.get(key)
            if existing is not None:
                # Discard ours; close the socket we just opened.
                _close_client(client)
                return existing
            self._cache[key] = new_entry
            return new_entry

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------

    def subscribe(self, pattern: str, handler: OscHandler) -> None:
        """Register an OSC dispatcher mapping.

        Patterns follow pythonosc matching (``/marker/*`` matches
        ``/marker/0`` … ``/marker/3``). Re-subscribing a pattern replaces
        the prior handler rather than appending.
        """
        if not _PYTHONOSC_AVAILABLE:  # pragma: no cover
            return
        with self._listener_lock:
            previous = self._subscriptions.get(pattern)
            if previous is not None:
                self._dispatcher.unmap(pattern, previous)
            self._subscriptions[pattern] = handler
            self._dispatcher.map(pattern, handler)

    def unsubscribe(self, pattern: str) -> None:
        """Remove a previously-registered mapping. No-op if absent."""
        if not _PYTHONOSC_AVAILABLE:  # pragma: no cover
            return
        with self._listener_lock:
            handler = self._subscriptions.pop(pattern, None)
            if handler is not None:
                self._dispatcher.unmap(pattern, handler)

    def start_listener(
        self,
        port: int,
        *,
        allowed_ips: Iterable[str] = (),
        multicast_group: str = "",
    ) -> None:
        """Start the inbound UDP listener.

        Idempotent: a second call with the same parameters is a no-op.
        A call with different parameters (port / allowlist / multicast
        group) is treated as ``restart_listener``.
        """
        normalised_ips = frozenset(ip.strip() for ip in allowed_ips if isinstance(ip, str) and ip.strip())
        group = multicast_group.strip()
        with self._listener_lock:
            if self._listener is not None:
                if (
                    self._listener_port == port
                    and self._listener_allowed_ips == normalised_ips
                    and self._listener_multicast_group == group
                ):
                    return
        self.restart_listener(port=port, allowed_ips=normalised_ips, multicast_group=group)

    def stop_listener(self) -> None:
        """Stop the inbound listener. No-op if not running."""
        with self._listener_lock:
            listener = self._listener
            thread = self._listener_thread
            self._listener = None
            self._listener_thread = None
            self._listener_port = None
            self._listener_allowed_ips = frozenset()
            self._listener_multicast_group = ""
            self._listener_multicast_joined = False
        if listener is not None:
            listener.shutdown()
            listener.server_close()
        if thread is not None:
            thread.join(timeout=1.0)

    def restart_listener(
        self,
        *,
        port: int,
        allowed_ips: Iterable[str],
        multicast_group: str = "",
    ) -> None:
        """Stop, rebind, and restart the inbound listener atomically.

        Raises ``OSError`` on bind failure so the caller can revert the
        config (transactional hot-reload contract).

        When ``multicast_group`` is set, the bound socket also joins that
        IPv4 multicast group (``IP_ADD_MEMBERSHIP`` on ``INADDR_ANY``). A
        failed join is non-fatal (logged); only a failed bind raises.
        """
        if not _PYTHONOSC_AVAILABLE:  # pragma: no cover
            logger.warning("python-osc not installed – OSC input disabled. Run: pip install python-osc")
            return
        normalised_ips = frozenset(ip.strip() for ip in allowed_ips if isinstance(ip, str) and ip.strip())
        group = multicast_group.strip()
        self.stop_listener()
        try:
            listener = _FilteredOSCUDPServer(
                ("", port),
                self._dispatcher,
                normalised_ips,
            )
        except OSError:
            logger.error(
                "Cannot start OSC listener on port %d – port likely in use.",
                port,
            )
            raise
        joined_group = _join_multicast_group(listener.socket, group, port) if group else False
        thread = threading.Thread(
            target=listener.serve_forever,
            daemon=True,
            name="OscService-Listener",
        )
        # Start the thread inside the lock so a concurrent ``stop_listener``
        # can't see the listener before its serve loop runs: ``shutdown()``
        # before ``serve_forever()`` enters its loop deadlocks, and
        # ``join()`` on an unstarted thread raises ``RuntimeError``.
        # ``Thread.start()`` only blocks until the thread is scheduled.
        with self._listener_lock:
            self._listener = listener
            self._listener_thread = thread
            self._listener_port = port
            self._listener_allowed_ips = normalised_ips
            self._listener_multicast_group = group
            self._listener_multicast_joined = joined_group
            try:
                thread.start()
            except Exception:
                # Thread launch failed (e.g. thread exhaustion): tear the bound
                # listener back down so no half-installed socket/unstarted
                # thread is left for stop_listener to trip over.
                self._listener = None
                self._listener_thread = None
                self._listener_port = None
                self._listener_allowed_ips = frozenset()
                self._listener_multicast_group = ""
                self._listener_multicast_joined = False
                listener.server_close()
                raise
        group_note = f"; joined multicast group {group}" if joined_group else ""
        if normalised_ips:
            logger.info(
                "OSC input listening on UDP port %d; accepting packets only from %s%s",
                port,
                ", ".join(sorted(normalised_ips)),
                group_note,
            )
        else:
            logger.warning(
                "OSC input listening on UDP port %d with no sender allowlist "
                "– any LAN device can send OSC. Set osc.allowed_sender_ips "
                "in config.toml to restrict.%s",
                port,
                group_note,
            )

    @property
    def listener_port(self) -> int | None:
        """Currently-bound listener port, or None if stopped."""
        return self._listener_port

    def listener_status(self) -> dict[str, Any]:
        """Live inbound-listener state for diagnostics: bound port, the
        multicast group the socket joined (and whether the join actually
        succeeded), and the sender allowlist (empty = open to any LAN device)."""
        with self._listener_lock:
            return {
                "port": self._listener_port,
                "multicast_group": self._listener_multicast_group,
                "multicast_joined": self._listener_multicast_joined,
                "allowed_sender_ips": sorted(self._listener_allowed_ips),
            }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Stop the listener and drain all outbound clients."""
        self.stop_listener()
        self.shutdown_clients()

    def __enter__(self) -> OscService:
        return self

    def __exit__(self, *_args: object) -> None:
        self.shutdown()


# ---------------------------------------------------------------------------
# Free helpers
# ---------------------------------------------------------------------------


def _join_multicast_group(sock: Any, group: str, port: int) -> bool:
    """Join ``group`` on ``sock`` (best-effort, INADDR_ANY interface).

    Returns True on success, False if the join failed (logged + swallowed).
    ``group`` is assumed already validated as an IPv4 multicast address;
    ``inet_aton`` is the only runtime guard.
    """
    try:
        mreq = socket.inet_aton(group) + socket.inet_aton("0.0.0.0")
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    except OSError as exc:
        logger.warning(
            "OSC listener on port %d could not join multicast group %s: %s – unicast/broadcast still active.",
            port,
            group,
            exc,
        )
        return False
    return True


def find_free_udp_port() -> int:  # pragma: no cover - test helper
    """Bind a transient socket to ``(*, 0)`` and return the kernel-assigned port."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("", 0))
        return int(s.getsockname()[1])
