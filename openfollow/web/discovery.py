# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""UDP multicast beacon for peer discovery.

``BeaconSender`` / ``BeaconReceiver`` run daemon threads that emit and ingest
JSON ``BeaconPacket`` datagrams on a fixed multicast group; ``BeaconReceiver``
tracks live ``PeerInfo`` entries with self-filtering, a peer-table cap, and
stale-entry pruning. Incoming datagrams are untrusted: ``from_bytes`` validates
``web_port`` range and caps / sanitises string fields.
"""

from __future__ import annotations

import errno
import json
import logging
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass

from openfollow.net_utils import get_local_ipv4_addresses

logger = logging.getLogger(__name__)

BEACON_MCAST_GROUP = "239.255.50.50"
BEACON_PORT = 50505
BEACON_INTERVAL = 2.0  # seconds
PEER_TIMEOUT = 6.0  # seconds (3 missed beacons)
# Hard cap on the peer table. A real LAN fleet is <100 devices; the cap stops
# an attacker-controlled beacon flood (web_port spans [1, 65535], spoofable
# source IPs) from growing the table without bound between read-side prunes.
MAX_PEERS = 256

# Errno values from transient interface changes (reconnect on next iteration)
_TRANSIENT_SEND_ERRNOS: frozenset[int] = frozenset(
    {
        errno.EADDRNOTAVAIL,
        errno.ENETUNREACH,
        errno.ENETDOWN,
        errno.EHOSTDOWN,
        errno.EHOSTUNREACH,
    }
)

BEACON_NAME_MAX_LEN = 128  # cap untrusted field length to prevent overflow
BEACON_VERSION_MAX_LEN = 32


@dataclass
class PeerInfo:
    """Information about a discovered peer."""

    name: str
    ip: str
    web_port: int
    version: str
    last_seen: float

    @property
    def address(self) -> str:
        return f"{self.ip}:{self.web_port}"

    @property
    def is_online(self) -> bool:
        return (time.time() - self.last_seen) < PEER_TIMEOUT


@dataclass
class BeaconPacket:
    """Beacon packet structure."""

    type: str = "openfollow"
    name: str = ""
    web_port: int = 80
    version: str = "0.1.0"

    def to_bytes(self) -> bytes:
        return json.dumps(asdict(self)).encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> BeaconPacket | None:
        """Parse an untrusted UDP datagram into a beacon packet.

        Returns ``None`` on any malformed input. In particular, ``web_port``
        must be an ``int`` in ``[1, 65535]`` (bool is rejected because
        ``bool`` is a subclass of ``int`` in Python); strings are capped
        and non-printable characters are stripped from ``name`` /
        ``version``.
        """
        try:
            d = json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(d, dict) or d.get("type") != "openfollow":
            return None

        web_port = d.get("web_port", 80)
        if isinstance(web_port, bool) or not isinstance(web_port, int):
            return None
        if not (1 <= web_port <= 65535):
            return None

        name = _sanitize_beacon_text(d.get("name", ""), BEACON_NAME_MAX_LEN)
        version = _sanitize_beacon_text(d.get("version", "0.1.0"), BEACON_VERSION_MAX_LEN)

        return cls(name=name, web_port=web_port, version=version)


def _sanitize_beacon_text(value: object, max_len: int) -> str:
    """Coerce ``value`` to a printable, length-capped string.

    Non-string input becomes ``""``. Control characters (anything where
    ``str.isprintable()`` returns False) are dropped so a crafted beacon
    can't inject terminal escapes or newlines into the web UI.
    """
    if not isinstance(value, str):
        return ""
    cleaned = "".join(ch for ch in value if ch.isprintable())
    return cleaned[:max_len]


class BeaconSender:
    """Sends periodic beacon packets via UDP multicast."""

    def __init__(self, name: str, web_port: int, version: str = "0.1.0", iface_ip: str = "") -> None:
        self._packet = BeaconPacket(name=name, web_port=web_port, version=version)
        self._iface_ip = iface_ip
        self._stop_event = threading.Event()
        # Set by ``update_iface_ip`` to make the send loop drop and rebuild its
        # socket so beacons egress from the new source interface.
        self._reopen = threading.Event()
        self._thread: threading.Thread | None = None
        # Health metrics for diagnostics bundle.
        # Reads of monotonic floats / int counters are atomic under
        # CPython's GIL so the diagnostics-thread reads don't need
        # the discovery thread's own lock; the values are at-most a
        # tick stale, which is fine for a snapshot.
        self._consecutive_errors = 0
        self._last_send_ts = 0.0  # monotonic, 0 = never sent successfully
        self._send_count = 0

    def update_name(self, name: str) -> None:
        """Update the beacon name (e.g., after config change)."""
        self._packet.name = name

    def update_iface_ip(self, iface_ip: str) -> None:
        """Repoint the multicast send interface after a host IP change.

        The send loop rebuilds its socket on the next iteration so beacons
        egress from the new source address. A no-op when the IP is unchanged.
        """
        if iface_ip == self._iface_ip:
            return
        self._iface_ip = iface_ip
        self._reopen.set()

    def _drain_reopen(self, sock: socket.socket | None) -> socket.socket | None:
        """Drop the socket on a pending repoint so the loop rebuilds it.

        Returns the socket to keep, or None to force a rebuild.
        """
        if not self._reopen.is_set():
            return sock
        self._reopen.clear()
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        return None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="BeaconSender")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=BEACON_INTERVAL + 1.0)
            self._thread = None

    def _open_socket(self) -> socket.socket:
        """Create a fresh multicast TX socket bound to the configured interface."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        if self._iface_ip:
            try:
                sock.setsockopt(
                    socket.IPPROTO_IP,
                    socket.IP_MULTICAST_IF,
                    socket.inet_aton(self._iface_ip),
                )
            except OSError as exc:
                # Preferred iface is gone. Fall back to all-interfaces –
                # beacons still go out, they just aren't bound to a
                # specific source IP until the interface returns.
                logger.warning(
                    "BeaconSender: interface IP %s not available (%s), sending on all interfaces.",
                    self._iface_ip,
                    exc,
                )
        return sock

    def _run(self) -> None:
        # Recovery state: we rebuild the socket on transient send errors
        # (interface change, VPN toggle). Unknown exceptions are caught
        # here too so the daemon thread can never die silently. Both the
        # initial open and any rebuild go through the same sock-is-None
        # branch so an open failure can never kill the thread.
        sock: socket.socket | None = None
        last_error_log = 0.0
        try:
            while not self._stop_event.is_set():
                sock = self._drain_reopen(sock)
                if sock is None:
                    try:
                        sock = self._open_socket()
                    except Exception as exc:
                        self._consecutive_errors += 1
                        now = time.monotonic()
                        if self._consecutive_errors <= 3 or (now - last_error_log) >= 30.0:
                            logger.warning(
                                "BeaconSender: socket open failed (%d consecutive): %s",
                                self._consecutive_errors,
                                exc,
                            )
                            last_error_log = now
                        self._stop_event.wait(BEACON_INTERVAL)
                        continue

                data = self._packet.to_bytes()
                try:
                    sock.sendto(data, (BEACON_MCAST_GROUP, BEACON_PORT))
                    if self._consecutive_errors:
                        logger.info(
                            "BeaconSender: send recovered after %d errors.",
                            self._consecutive_errors,
                        )
                        self._consecutive_errors = 0
                    self._last_send_ts = time.monotonic()
                    self._send_count += 1
                except OSError as exc:
                    self._consecutive_errors += 1
                    now = time.monotonic()
                    # Log first 3 errors, then at most every 30s during an outage.
                    if self._consecutive_errors <= 3 or (now - last_error_log) >= 30.0:
                        logger.warning(
                            "BeaconSender: sendto failed (%d consecutive): %s",
                            self._consecutive_errors,
                            exc,
                        )
                        last_error_log = now
                    if exc.errno in _TRANSIENT_SEND_ERRNOS:
                        # Interface changed – drop the socket; the next loop
                        # iteration will rebuild it via the branch above.
                        try:
                            sock.close()
                        except OSError:
                            pass
                        sock = None
                except Exception:
                    # Defence-in-depth: never let this daemon thread die on
                    # an unexpected error. Log once and continue the loop.
                    logger.exception("BeaconSender: unexpected error in send loop")
                self._stop_event.wait(BEACON_INTERVAL)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    # -- Diagnostics surface ------------------------------------------------
    # All four read-only; the writes come from ``_run`` on the daemon
    # thread. CPython's GIL makes the int / float reads atomic so a
    # stale snapshot (off by at most one tick) is the worst the
    # diagnostics collector ever sees.

    @property
    def is_alive(self) -> bool:
        """``True`` once :meth:`start` has been called and the daemon
        thread is still running. Catches the "service started but the
        sender thread died" case the bundle wants to surface."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def consecutive_errors(self) -> int:
        """Send failures since the last successful send. Resets to 0
        on recovery; non-zero in the bundle is the strongest signal
        that beacons aren't going out right now."""
        return self._consecutive_errors

    @property
    def last_send_ts(self) -> float:
        """``time.monotonic()`` at the last successful send, or 0.0
        if no send has succeeded yet. Diagnostics renders ``time.
        monotonic() - last_send_ts`` to give an absolute "last sent
        N seconds ago" reading."""
        return self._last_send_ts

    @property
    def send_count(self) -> int:
        """Cumulative successful sends since process start.
        A frozen counter alongside a thriving thread = silently
        broken multicast (interface up, sock open, no packets)."""
        return self._send_count


_LOCAL_IP_CACHE_TTL = 30.0  # seconds between local-IP refreshes


class BeaconReceiver:
    """Receives beacon packets and tracks discovered peers."""

    def __init__(
        self,
        on_peer_discovered: Callable[[PeerInfo], None] | None = None,
        iface_ip: str = "",
    ) -> None:
        self._peers: dict[str, PeerInfo] = {}  # ip:port -> PeerInfo
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._on_peer_discovered = on_peer_discovered
        self._iface_ip = iface_ip
        # Set by ``update_iface_ip`` to make the receive loop rebuild its
        # socket so multicast membership rejoins on the new interface.
        self._reopen = threading.Event()
        self._local_port: int | None = None  # To filter out self
        self._local_ips: set[str] = set()
        self._local_ips_ts: float = 0.0  # timestamp of last refresh
        # Counters for diagnostics bundle.
        # ``packets_received`` includes self-filtered + ignored
        # packets so a "no packets at all" host can still be
        # distinguished from a "packets arriving but all from
        # myself" host. The discriminator matters: the second case
        # means multicast is fine and the operator's discovery bug
        # is in the self-filter / port logic, not the network.
        self._packets_received = 0
        self._last_recv_ts = 0.0
        # Monotonic ts of the last table-full warning; -inf so the FIRST
        # warning always fires. (0.0 would suppress it for ~30 s after boot,
        # since time.monotonic() can start below 30 – exactly when a flood at
        # startup most needs surfacing.)
        self._peer_cap_log_ts = float("-inf")

    def set_local_port(self, port: int) -> None:
        """Set local web port to filter out self-discovery."""
        self._local_port = port

    def update_iface_ip(self, iface_ip: str) -> None:
        """Rejoin the multicast group on a new interface after a host IP change.

        The receive loop rebuilds its socket on the next iteration so membership
        moves to the new interface. A no-op when the IP is unchanged.
        """
        if iface_ip == self._iface_ip:
            return
        self._iface_ip = iface_ip
        # Force the self-filter cache to re-resolve so the new IP is recognised
        # as local immediately; otherwise our own looped-back beacon passes the
        # filter and we self-list as a peer until the next TTL refresh.
        self._local_ips_ts = 0.0
        self._reopen.set()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="BeaconReceiver")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)  # socket has 1s timeout
            self._thread = None

    def get_peers(self) -> list[PeerInfo]:
        """Return list of discovered peers, removing stale ones."""
        now = time.time()
        with self._lock:
            self._prune_stale_locked(now)
            return list(self._peers.values())

    def _prune_stale_locked(self, now: float) -> None:
        """Drop peers not seen within ``PEER_TIMEOUT``. Caller holds ``self._lock``."""
        stale = [k for k, v in self._peers.items() if (now - v.last_seen) > PEER_TIMEOUT]
        for k in stale:
            del self._peers[k]

    def _setup_socket(self) -> socket.socket:
        """Create, bind, and join the multicast group.

        Raises ``OSError`` on an unrecoverable setup failure (bind held by
        another process, malformed ``iface_ip``, no joinable interface) so the
        caller can log + retry instead of letting the daemon thread die.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass  # SO_REUSEPORT not available on all platforms
        try:
            sock.bind(("", BEACON_PORT))
            # Join multicast group on the configured interface (or all interfaces)
            iface_addr = socket.inet_aton(self._iface_ip) if self._iface_ip else socket.inet_aton("0.0.0.0")
            mreq = socket.inet_aton(BEACON_MCAST_GROUP) + iface_addr
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            except OSError as exc:
                logger.warning(
                    "BeaconReceiver: interface IP %s not available (%s), joining on all interfaces.",
                    self._iface_ip or "0.0.0.0",
                    exc,
                )
                mreq = socket.inet_aton(BEACON_MCAST_GROUP) + socket.inet_aton("0.0.0.0")
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            sock.settimeout(1.0)
        except OSError:
            sock.close()
            raise
        return sock

    def _run(self) -> None:
        # Rebuild the socket on setup failure rather than letting the daemon
        # thread die silently: a bind held by another process or a malformed
        # iface_ip would otherwise flip is_alive to False with no recovery.
        consecutive = 0
        last_error_log = 0.0
        while not self._stop_event.is_set():
            try:
                sock = self._setup_socket()
            except OSError as exc:
                consecutive += 1
                now = time.monotonic()
                # Log first 3 failures, then at most every 30s during an outage.
                if consecutive <= 3 or (now - last_error_log) >= 30.0:
                    logger.error(
                        "BeaconReceiver: socket setup failed (%d consecutive): %s; retrying.",
                        consecutive,
                        exc,
                    )
                    last_error_log = now
                self._stop_event.wait(BEACON_INTERVAL)
                continue
            consecutive = 0
            try:
                self._recv_loop(sock)
            finally:
                sock.close()

    def _recv_loop(self, sock: socket.socket) -> None:
        """Receive + dispatch packets until stop, or a repoint is requested.

        Returns on a pending repoint so the caller rebuilds the socket and
        rejoins multicast on the new interface.
        """
        while not self._stop_event.is_set():
            if self._reopen.is_set():
                self._reopen.clear()
                return
            try:
                data, addr = sock.recvfrom(1024)
                self._handle_packet(data, addr[0])
            except TimeoutError:
                continue

    def _handle_packet(self, data: bytes, sender_ip: str) -> None:
        packet = BeaconPacket.from_bytes(data)
        if packet is None:
            return

        # Count every well-formed beacon packet for diagnostics.
        self._packets_received += 1
        self._last_recv_ts = time.monotonic()

        # Check if this is from ourselves
        if self._is_local(sender_ip, packet.web_port):
            return

        key = f"{sender_ip}:{packet.web_port}"
        now = time.time()

        with self._lock:
            is_new = key not in self._peers
            if is_new and len(self._peers) >= MAX_PEERS:
                # At the cap: prune stale entries first, and only refuse the
                # new peer if the table is still full of live ones. Bounds the
                # table under a beacon flood instead of relying solely on the
                # read-side prune in get_peers (which may have no caller).
                self._prune_stale_locked(now)
                if len(self._peers) >= MAX_PEERS:
                    self._note_peer_cap()
                    return
            self._peers[key] = PeerInfo(
                name=packet.name,
                ip=sender_ip,
                web_port=packet.web_port,
                version=packet.version,
                last_seen=now,
            )
            peer = self._peers[key]

        if is_new and self._on_peer_discovered:
            self._on_peer_discovered(peer)

    def _note_peer_cap(self) -> None:
        """Log the peer-table-full condition, throttled to avoid flood spam."""
        now = time.monotonic()
        if (now - self._peer_cap_log_ts) >= 30.0:
            logger.warning(
                "BeaconReceiver: peer table full at %d entries; dropping new peers "
                "until existing ones age out (possible beacon flood).",
                MAX_PEERS,
            )
            self._peer_cap_log_ts = now

    def _is_local(self, ip: str, port: int) -> bool:
        """Check if this beacon is from ourselves.

        Local IPs are cached for ``_LOCAL_IP_CACHE_TTL`` seconds to avoid
        repeated DNS lookups on every received packet.
        """
        if self._local_port is None:
            return False
        if port != self._local_port:
            return False
        now = time.time()
        if now - self._local_ips_ts > _LOCAL_IP_CACHE_TTL:
            try:
                self._local_ips = get_local_ipv4_addresses()
            except OSError:
                # Don't advance the cache timestamp on a failed lookup: the
                # prior (possibly empty) set would otherwise be trusted for the
                # full TTL, during which this host fails its own self-filter and
                # adds itself to the peer list. Retry on the next packet instead.
                pass
            else:
                self._local_ips_ts = now
            if self._iface_ip:
                self._local_ips.add(self._iface_ip)
        return ip in self._local_ips

    # -- Diagnostics surface ------------------------------------------------

    @property
    def is_alive(self) -> bool:
        """``True`` once :meth:`start` has been called and the
        receive thread is still running."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def packets_received(self) -> int:
        """Cumulative count of well-formed beacon packets seen by the
        socket, including self-filtered ones. The bundle compares
        this to ``len(get_peers())`` to distinguish "no multicast"
        from "multicast works but every packet is from me"."""
        return self._packets_received

    @property
    def last_recv_ts(self) -> float:
        """``time.monotonic()`` of the most recent received beacon
        packet (any source), or 0.0 if none received yet."""
        return self._last_recv_ts
