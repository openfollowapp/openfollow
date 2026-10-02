# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Local IPv4 enumeration and source-interface resolution helpers.

Wraps ``psutil.net_if_addrs()`` to list/select interface IPv4 addresses and
resolves a configured (or pinned) interface to a concrete bind IP, with
auto-detect fallback. ``resolve_source_ip`` returns ``(ip, ResolveStatus)``.
"""

from __future__ import annotations

import ipaddress
import socket
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, NamedTuple

import psutil

# Status from resolve_source_ip / resolve_plane_source_ip: "iface" (pinned),
# "station" (inherited the station-wide pin), "primary" (auto-detected),
# "down" (an interface *is* configured but currently has no address – an error
# state, never a reason to bind elsewhere), "none" (nothing configured and
# nothing to auto-detect).
ResolveStatus = Literal["iface", "station", "primary", "down", "none"]


def get_primary_local_ipv4(default: str = "N/A") -> str:
    """Return the primary local IPv4 address, or *default* on failure."""
    # Try outbound interface via dummy connection; fall back to first non-loopback address.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = str(sock.getsockname()[0])
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass

    # Ordered, not set-iteration order: on an offline show LAN the probe above
    # always fails, so this branch decides the station's address. Unordered
    # iteration makes that pick flip when an unrelated address appears (a VPN,
    # docker0, a second lease), which reads downstream as a genuine IP change
    # and repoints the data planes onto a network nobody chose.
    #
    # Link-local sorts last rather than first: a station that took a 169.254
    # address while DHCP was failing keeps advertising it after a real lease
    # arrives, because "169." precedes "192." lexicographically. That address
    # is the station's identity - what peers and consoles reach it at - so a
    # real lease has to win.
    for ip in sorted(get_local_ipv4_addresses(), key=_address_preference):
        if not ip.startswith("127."):
            return ip

    return default


def _address_preference(ip: str) -> tuple[int, tuple[int, ...]]:
    """Sort key preferring a routable address, then ordering numerically.

    Numeric rather than lexicographic so ``10.0.0.9`` precedes ``10.0.0.10``;
    the point is a stable pick, and digit-string order is stable but arbitrary.
    """
    try:
        octets = tuple(int(part) for part in ip.split("."))
    except ValueError:
        octets = ()
    return (1 if ip.startswith("169.254.") else 0, octets)


def get_local_ipv4_addresses() -> set[str]:
    """Return all local IPv4 addresses across every network interface."""
    ips: set[str] = set()
    for addrs in psutil.net_if_addrs().values():
        for addr in addrs:
            if addr.family == socket.AF_INET:
                ips.add(addr.address)
    return ips


def get_iface_ipv4(iface_name: str) -> str:
    """Return first non-loopback IPv4 on iface, or empty string if unavailable."""
    if not iface_name:
        return ""
    addrs = psutil.net_if_addrs().get(iface_name, [])
    for addr in addrs:
        if addr.family == socket.AF_INET and not addr.address.startswith("127."):
            return str(addr.address)
    return ""


def get_iface_for_ip(ip: str) -> str:
    """Return the interface holding IP (either family), or empty string."""
    if not ip or ip.startswith("127.") or ip == "::1":
        return ""
    for iface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family in (socket.AF_INET, socket.AF_INET6) and str(addr.address).split("%")[0] == ip:
                return str(iface)
    return ""


def list_iface_ipv4() -> list[tuple[str, str]]:
    """Return [(iface_name, ipv4)] for every non-loopback IPv4, sorted by name."""
    out: list[tuple[str, str]] = []
    for iface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family != socket.AF_INET:
                continue
            if addr.address.startswith("127."):
                continue
            out.append((str(iface), str(addr.address)))
            break  # first non-loopback IPv4 per iface is enough
    out.sort(key=lambda item: item[0])
    return out


def resolve_source_ip(
    iface: str,
    *,
    fallback: bool = True,
) -> tuple[str, ResolveStatus]:
    """Resolve pinned interface to concrete bind IP + status.

    Status: "iface" (pinned live), "primary" (auto-detect), "none" (unavailable).
    """
    if iface:
        ip_for_iface = get_iface_ipv4(iface)
        if ip_for_iface:
            return ip_for_iface, "iface"
        # Pinned iface is down / missing – fall through to fallback
        # rather than failing closed.

    if not fallback:
        return "", "none"

    primary = get_primary_local_ipv4(default="")
    if primary and not primary.startswith("127."):
        return primary, "primary"
    return "", "none"


def route_source(address: str, port: int = 0) -> str | None:
    """This station's source address towards *address*, or None when nothing routes there.

    No packet is sent: connecting a UDP socket only consults the routing table.
    """
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    probe = socket.socket(family, socket.SOCK_DGRAM)
    try:
        if family == socket.AF_INET:
            # Without it a broadcast destination is refused, though nothing is sent.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        probe.connect((address, port or 9))
        return str(probe.getsockname()[0]).split("%")[0]
    except OSError:
        return None
    finally:
        probe.close()


def plane_source_iface(pin: str, station_iface: str = "") -> str:
    """Return the interface a plane is configured to use, or "" for auto-detect.

    A blank *pin* means "follow the station interface"; a blank station
    interface in turn means "let the OS choose". This resolves *configuration*
    only – it never looks at whether the interface currently has an address.
    """
    return pin or station_iface


def resolve_plane_source_ip(
    pin: str,
    station_iface: str = "",
) -> tuple[str, ResolveStatus]:
    """Resolve one network plane's configured interface to a concrete bind IP.

    Inheritance happens for an **unset** value only: blank pin follows
    *station_iface*, blank station interface auto-detects. Once an interface is
    configured, it is the only one this plane may use – if it currently has no
    address the result is ``("", "down")`` and the caller must bind nothing,
    surface the error and retry.

    It deliberately does **not** fall through to another interface. Silently
    moving PSN onto the office LAN because a lighting VLAN went dark is worse
    than PSN stopping: a dead output is diagnosable, a misrouted one is not.

    The *address* is free to change – callers re-resolve on every retry so a new
    DHCP lease on the same interface is picked up automatically. Only the
    interface is fixed.
    """
    configured = plane_source_iface(pin, station_iface)
    if configured:
        resolved = get_iface_ipv4(configured)
        if resolved:
            return resolved, "iface" if pin else "station"
        return "", "down"

    primary = get_primary_local_ipv4(default="")
    if primary and not primary.startswith("127."):
        return primary, "primary"
    return "", "none"


def resolve_multicast_iface(pin: str, station_iface: str = "") -> tuple[str, ResolveStatus]:
    """Resolve the interface a receiver takes its multicast membership on.

    **Not a bind address.** A listener that has to receive multicast binds the
    wildcard and nothing else: the kernel matches a datagram's destination
    against the bound address, so a socket bound to one interface's address
    receives neither the group nor subnet broadcast, while
    ``IP_ADD_MEMBERSHIP`` still reports success. What this resolves is the
    ``imr_interface`` of that membership, and callers must not pass it to
    ``bind()``.

    The receive-side counterpart of :func:`resolve_plane_source_ip`, differing
    in one arm: with nothing configured the result is ``("", "none")``, meaning
    the routing table picks the interface, rather than the auto-detected
    primary. A sender with no pin has to choose one address; a membership does
    not have to be pinned at all.

    A configured interface with no address yields ``("", "down")``: the caller
    holds **no membership** rather than taking one on an interface the operator
    excluded. The listener itself keeps running, so unicast and broadcast go on
    arriving while the group is unavailable.
    """
    configured = plane_source_iface(pin, station_iface)
    if not configured:
        return "", "none"
    resolved = get_iface_ipv4(configured)
    if resolved:
        return resolved, "iface" if pin else "station"
    return "", "down"


WEB_BIND_ALL = "0.0.0.0"


def resolve_web_bind(web_bind: str, web_bind_iface: str) -> tuple[str, ResolveStatus]:
    """Resolve the web UI's listen address, failing **open**.

    An explicit ``web_bind`` address outranks the interface pin and is
    returned verbatim (status ``"iface"``); a pin resolves to that
    interface's current IPv4; neither gives the wildcard bind.

    This is the one plane that does not fail closed. Every other plane going
    silent is diagnosable from another station, whereas an unreachable config
    UI leaves nobody able to correct the pin that caused it – so a pin naming
    an interface with no address yields ``(WEB_BIND_ALL, "down")`` and the
    caller serves everywhere while surfacing the substitution.
    """
    if web_bind:
        return web_bind, "iface"
    if not web_bind_iface:
        return WEB_BIND_ALL, "none"
    resolved = get_iface_ipv4(web_bind_iface)
    if resolved:
        return resolved, "iface"
    return WEB_BIND_ALL, "down"


def resolve_iface_ip(configured: str) -> str:
    """Return configured IP, or auto-detect primary for multicast binding."""
    if configured:
        return configured
    primary = get_primary_local_ipv4(default="")
    if primary and not primary.startswith("127."):
        return primary
    return ""


def wait_for_source_ip(
    iface: str = "",
    timeout_s: float = 30.0,
    interval_s: float = 1.0,
) -> str:
    """Block until pinned source IP is live (returns loopback on timeout)."""
    # When pinned, use fallback=False to wait for target; otherwise accept primary.
    pinned = bool(iface)
    deadline = time.monotonic() + timeout_s
    while True:
        resolved, status = resolve_source_ip(iface, fallback=not pinned)
        if status != "none":
            return resolved
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # On timeout, fall back to primary or loopback.
            primary = get_primary_local_ipv4(default="")
            if primary and not primary.startswith("127."):
                return primary
            return "127.0.0.1"
        # Don't sleep past deadline.
        time.sleep(min(interval_s, remaining))


class InterfaceUnavailable(OSError):
    """A pinned interface has no usable address, so the plane must stay silent.

    Subclasses :class:`OSError` so the socket-error handling each sender
    already has keeps working unchanged.
    """


def bind_multicast_send_iface(sock: socket.socket, iface_ip: str | None) -> None:
    """Pin a multicast TX socket to *iface_ip*, raising rather than roaming.

    An unbound multicast socket does not send "on all interfaces" - it sends on
    whichever single interface the routing table picks, which is why a plane
    that quietly fell back looked contained on the interface anyone thought to
    capture. A pin that cannot be honoured is an error state, never a reason to
    transmit somewhere the operator did not choose.

    Three states, matching :func:`resolve_plane_source_ip`: an address pins the
    socket, ``""`` means nothing is configured and is left to the OS, and
    ``None`` means an interface *is* configured but currently has no address -
    which must stop the plane rather than move it.
    """
    if iface_ip is None:
        raise InterfaceUnavailable(
            "the configured interface has no address; staying silent until it returns "
            "rather than sending on another interface"
        )
    if not iface_ip:
        return
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(iface_ip))
    except OSError as exc:
        raise InterfaceUnavailable(
            f"interface address {iface_ip} is unavailable ({exc}); staying silent until it "
            f"returns rather than sending on another interface"
        ) from exc


def join_multicast_group_on_iface(sock: socket.socket, group: str, iface_ip: str | None) -> None:
    """Join *group* on *iface_ip* only, raising rather than joining everywhere.

    The receive side of :func:`bind_multicast_send_iface`, with the same three
    states: joining on ``0.0.0.0`` subscribes on an interface the operator
    excluded, so peers from that network reach the station's own peer list.
    """
    if iface_ip is None:
        raise InterfaceUnavailable(
            "the configured interface has no address; staying unsubscribed until it returns "
            "rather than joining on every interface"
        )
    mreq = socket.inet_aton(group) + socket.inet_aton(iface_ip or "0.0.0.0")
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    except OSError as exc:
        if not iface_ip:
            raise
        raise InterfaceUnavailable(
            f"interface address {iface_ip} is unavailable ({exc}); staying unsubscribed "
            f"until it returns rather than joining on every interface"
        ) from exc


# The kernel's IPv4 route table. Read directly rather than asked of the
# network backend: the backend reports what is *configured*, and "where does a
# packet to that address go" is a question about what the kernel will do.
_PROC_NET_ROUTE = Path("/proc/net/route")


class Ipv4Route(NamedTuple):
    """One route of the main IPv4 table. A gateway of ``0.0.0.0`` is directly connected.

    The table carries no up/down state: the kernel sets ``RTF_UP`` on every row
    it prints, and keeps an interface's routes through a carrier loss that
    leaves its address in place.
    """

    iface: str
    network: ipaddress.IPv4Network
    gateway: str
    metric: int


_RTF_REJECT = 0x0200


def _route_word(field: str) -> str:
    # The kernel prints each network-order word as a native integer.
    return socket.inet_ntoa(int(field, 16).to_bytes(4, sys.byteorder))


def read_ipv4_routes(path: Path | None = None) -> list[Ipv4Route] | None:
    """Every route in the kernel's main IPv4 table that can carry a packet.

    ``None`` means the table could not be read (no ``/proc`` on macOS), which a
    caller must report as unknown - distinct from ``[]``, a host that really has
    nowhere to send a packet. The path is resolved here rather than as a default
    argument, so a test pointing the module at another table is honoured.
    """
    try:
        text = (path or _PROC_NET_ROUTE).read_text()
    except (OSError, ValueError):
        return None
    routes: list[Ipv4Route] = []
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 8:
            continue
        try:
            # Unreachable and prohibit routes carry RTF_REJECT, a blackhole no
            # device: none of them sends anything anywhere.
            if fields[0] == "*" or int(fields[3], 16) & _RTF_REJECT:
                continue
            network = ipaddress.IPv4Network(f"{_route_word(fields[1])}/{_route_word(fields[7])}", strict=False)
            route = Ipv4Route(
                iface=fields[0],
                network=network,
                gateway=_route_word(fields[2]),
                metric=int(fields[6]),
            )
        except (ValueError, OverflowError):
            continue
        routes.append(route)
    return routes


@dataclass(frozen=True)
class HostLookup:
    """What a bounded lookup found: every address, or why there is none."""

    # "resolved"; "literal" (an address, not a name; *error* says when the
    # resolver's family excludes it); "pending" (still running past the wait);
    # "failed"; or "skipped" (not started: too many running, or no thread to run it).
    outcome: Literal["resolved", "literal", "pending", "failed", "skipped"]
    addresses: tuple[str, ...] = ()
    error: str = ""


@dataclass(eq=False)
class _Lookup:
    """One lookup in flight; every caller waiting on it reads its answer here."""

    host: str
    generation: int
    started: float
    done: threading.Event = field(default_factory=threading.Event)
    # (when, addresses, error), set once by the worker.
    answer: tuple[float, tuple[str, ...], str] | None = None
    # Why no thread could run it; every caller waiting on it is told so.
    not_started: str | None = None


class BoundedResolver:
    """Host name lookups a caller waits for only so long.

    ``getaddrinfo`` takes no timeout, so on a LAN with no resolver a bare call
    blocks for as long as the OS keeps retrying. Each lookup runs on a daemon
    thread instead, and one name never has two at once. A caller waits at most
    its own budget counted from when the lookup started, so one older than that
    answers "pending" at once, and a short wait elsewhere shortens no one
    else's. A lookup keeps running past every wait, and its answer serves the
    next caller. ``max_in_flight`` caps how many lookup threads may be alive.

    An answer is remembered for ``ttl_s`` and a failure for ``failure_ttl_s``,
    so a broken name does not respawn a thread per call.
    """

    def __init__(
        self,
        *,
        family: int = socket.AF_UNSPEC,
        ttl_s: float = 0.0,
        failure_ttl_s: float = 0.0,
        max_in_flight: int = 0,
        thread_name: str = "dns",
        clock: Callable[[], float] = time.monotonic,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
    ) -> None:
        self._family = family
        self._ttl_s = ttl_s
        self._failure_ttl_s = failure_ttl_s
        self._max_in_flight = max_in_flight
        self._thread_name = thread_name
        self._clock = clock
        self._thread_factory = thread_factory
        self._lock = threading.Lock()
        # host -> (when, addresses, error) from the last lookup that finished.
        self._answers: dict[str, tuple[float, tuple[str, ...], str]] = {}
        self._pending: dict[str, _Lookup] = {}
        # Lookup threads still running, including any ``clear()`` forgot.
        self._alive = 0
        # Bumped by ``clear()``; a lookup started before it stores nothing.
        self._generation = 0

    def __len__(self) -> int:
        """How many names have a remembered answer."""
        with self._lock:
            return len(self._answers)

    def lookup(self, host: str, wait_s: float) -> HostLookup:
        """*host*'s addresses, waiting at most *wait_s* from when its lookup started."""
        found = self._begin(host)
        if isinstance(found, HostLookup):
            return found
        found.done.wait(max(0.0, found.started + wait_s - self._clock()))
        with self._lock:
            cleared = found.generation != self._generation
        if found.not_started is not None:
            return HostLookup("skipped", error=found.not_started)
        if found.answer is None:
            return HostLookup("pending")
        if cleared:
            return HostLookup("failed", error="no answer")
        return self._as_lookup(found.answer)

    def prefetch(self, host: str) -> None:
        """Start *host*'s lookup without waiting, so several can run at once."""
        self._begin(host)

    def cached(self, host: str) -> tuple[str, ...]:
        """What *host* is known to resolve to, without looking it up."""
        literal = self._literal(host)
        if literal is not None:
            return literal.addresses
        with self._lock:
            known = self._known(host)
        return known.addresses if known is not None else ()

    def clear(self) -> None:
        """Forget every answer and every running lookup; one still running stores nothing."""
        with self._lock:
            self._answers.clear()
            self._pending.clear()
            self._generation += 1

    def _begin(self, host: str) -> HostLookup | _Lookup:
        """A known answer, or the lookup to wait on, started here if none is running."""
        literal = self._literal(host)
        if literal is not None:
            return literal
        with self._lock:
            known = self._known(host)
            if known is not None:
                return known
            running = self._pending.get(host)
            if running is not None:
                return running
            if self._max_in_flight and self._alive >= self._max_in_flight:
                return HostLookup("skipped", error="too many lookups still running")
            running = _Lookup(host, self._generation, self._clock())
            self._pending[host] = running
            self._alive += 1
        # Started outside the lock, which the worker takes to store its answer.
        try:
            self._thread_factory(target=self._resolve, args=(running,), name=self._thread_name, daemon=True).start()
        except RuntimeError as exc:
            with self._lock:
                self._alive -= 1
                if self._pending.get(host) is running:
                    del self._pending[host]
                running.not_started = str(exc)
            running.done.set()
            return HostLookup("skipped", error=str(exc))
        return running

    def _literal(self, host: str) -> HostLookup | None:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return None
        if self._family == socket.AF_INET and address.version != 4:
            return HostLookup("literal", error="not an IPv4 address")
        return HostLookup("literal", (str(address),))

    def _expired(self, answer: tuple[float, tuple[str, ...], str], now: float) -> bool:
        when, addresses, _error = answer
        return now - when >= (self._ttl_s if addresses else self._failure_ttl_s)

    def _known(self, host: str) -> HostLookup | None:
        """A remembered answer still within its lifetime. Call with the lock held."""
        answer = self._answers.get(host)
        if answer is None or self._expired(answer, self._clock()):
            return None
        return self._as_lookup(answer)

    @staticmethod
    def _as_lookup(answer: tuple[float, tuple[str, ...], str]) -> HostLookup:
        _when, addresses, error = answer
        return HostLookup("resolved", addresses) if addresses else HostLookup("failed", error=error)

    def _resolve(self, running: _Lookup) -> None:
        addresses: tuple[str, ...] = ()
        error = ""
        try:
            infos = socket.getaddrinfo(running.host, None, self._family, socket.SOCK_DGRAM)
            addresses = tuple(dict.fromkeys(str(info[4][0]) for info in infos))
            if not addresses:
                error = "no address"
        except Exception as exc:  # noqa: BLE001 - any failure is this name's answer
            # Not only OSError: IDNA encoding rejects an empty or over-long label
            # with a ValueError, and a dead worker would block the name for good.
            error = str(exc) or exc.__class__.__name__
        finally:
            with self._lock:
                now = self._clock()
                running.answer = (now, addresses, error)
                self._alive -= 1
                if running.generation == self._generation:
                    del self._pending[running.host]
                    # Expired answers serve no one; dropped here so the map stays bounded.
                    self._answers = {h: a for h, a in self._answers.items() if not self._expired(a, now)}
                    self._answers[running.host] = running.answer
            running.done.set()


# Names each shared resolver may have resolving at once: a LAN with no resolver
# must not collect a hung thread per name the operator ever typed.
MAX_LOOKUPS_IN_FLIGHT = 16
# Resolves a destination once per name for every output and panel row that
# asks; a failure is retried sooner, so a camera switched on is found quickly.
HOST_RESOLVER = BoundedResolver(
    ttl_s=30.0, failure_ttl_s=10.0, max_in_flight=MAX_LOOKUPS_IN_FLIGHT, thread_name="host-dns"
)
# OSC output and RTTrPM send from IPv4 sockets, so a dual-stack name means its A record.
IPV4_RESOLVER = BoundedResolver(
    family=socket.AF_INET,
    ttl_s=30.0,
    failure_ttl_s=30.0,
    max_in_flight=MAX_LOOKUPS_IN_FLIGHT,
    thread_name="ipv4-dns",
)
