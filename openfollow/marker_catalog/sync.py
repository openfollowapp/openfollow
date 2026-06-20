# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""UDP multicast sync for :class:`~openfollow.marker_catalog.MarkerCatalog`.

Mirrors the structure of :mod:`openfollow.web.discovery` (two daemon
threads, transient-errno recovery, identical multicast group at a
different port). Each station emits a heartbeat every 5 s carrying
the full catalog plus its own selection (controlled / viewer ids);
on local catalog edit, a smaller "delta" beacon is queued and
flushed after a short debounce.

Selection is broadcast purely for UI display ("controlled by:
this station + bright-fox") – it's never applied as a remote write.
"""

from __future__ import annotations

import errno
import json
import logging
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from openfollow.marker_catalog.catalog import MarkerCatalog, MarkerEntry

logger = logging.getLogger(__name__)


CATALOG_MCAST_GROUP = "239.255.50.50"
CATALOG_PORT = 50506
HEARTBEAT_INTERVAL = 5.0
DELTA_DEBOUNCE = 0.3
PEER_TIMEOUT = 15.0  # 3 missed heartbeats

# Cap the untrusted-keyed peer-selection table so a flood of unique
# station_ids can't grow it without bound (mirrors web.discovery.MAX_PEERS).
MAX_PEER_SELECTIONS = 256
# Bound untrusted station_id / station_name length on intake; a real
# station_id is a UUID (~36 chars).
_STATION_ID_MAX_LEN = 128
_STATION_NAME_MAX_LEN = 128
_PEER_CAP_LOG_INTERVAL = 30.0

# Back-off prevents CPU spin on persistent send failures.
_SEND_ERROR_BACKOFF = HEARTBEAT_INTERVAL

# Reject packets larger than 60KB to prevent receive buffer exhaustion.
_MAX_RX_PACKET = 60 * 1024
_MAX_TX_PACKET = 1400

_TRANSIENT_SEND_ERRNOS: frozenset[int] = frozenset(
    {
        errno.EADDRNOTAVAIL,
        errno.ENETUNREACH,
        errno.ENETDOWN,
        errno.EHOSTDOWN,
        errno.EHOSTUNREACH,
    }
)


@dataclass
class PeerSelection:
    """A peer's broadcast selection – used purely for UI display."""

    station_id: str
    station_name: str
    controlled_ids: list[int] = field(default_factory=list)
    viewer_ids: list[int] = field(default_factory=list)
    last_seen: float = 0.0


def _entry_to_dict(entry: MarkerEntry) -> dict[str, object]:
    return {
        "id": entry.id,
        "name": entry.name,
        "color": entry.color,
        "updated_at": entry.updated_at,
        "tombstone": entry.tombstone,
    }


def _entry_from_dict(raw: object) -> MarkerEntry | None:
    if not isinstance(raw, dict):
        return None
    # Require real bool; bool("false") is True in Python.
    tombstone_raw = raw.get("tombstone", False)
    tombstone = tombstone_raw if isinstance(tombstone_raw, bool) else False
    # Require a real int id from peers (matches MarkerEntry.__post_init__ and
    # load_catalog's on-disk path). ``int("5")`` / ``int(5.9)`` / ``int(True)``
    # would all coerce and let a malformed peer payload collide with a valid
    # marker id under LWW; reject instead of coercing.
    raw_id = raw.get("id", 0)
    if not isinstance(raw_id, int) or isinstance(raw_id, bool):
        return None
    # Leave name as-is; MarkerEntry.__post_init__ normalises it.
    try:
        return MarkerEntry(
            id=raw_id,
            name=raw.get("name", ""),
            color=str(raw.get("color", "#ffffff")),
            updated_at=float(raw.get("updated_at", 0.0)),
            tombstone=tombstone,
        )
    except (TypeError, ValueError):
        return None


def _build_beacon(
    *,
    kind: str,
    station_id: str,
    station_name: str,
    controlled_ids: list[int],
    viewer_ids: list[int],
    entries: list[MarkerEntry],
) -> bytes:
    payload: dict[str, object] = {
        "type": "openfollow-markers",
        "kind": kind,
        "station_id": station_id,
        "station_name": station_name,
        "controlled_ids": list(controlled_ids),
        "viewer_ids": list(viewer_ids),
        "entries": [_entry_to_dict(e) for e in entries],
    }
    return json.dumps(payload).encode("utf-8")


def _parse_beacon(data: bytes) -> dict[str, object] | None:
    """Parse an untrusted datagram. Returns ``None`` on any malformed input."""
    if len(data) > _MAX_RX_PACKET:
        return None
    try:
        decoded = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    if decoded.get("type") != "openfollow-markers":
        return None
    return decoded


def _coerce_id_list(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    out: list[int] = []
    for v in value:
        if isinstance(v, bool) or not isinstance(v, int):
            continue
        if v < 1:
            continue
        out.append(v)
    return out


def _sanitize_text(value: object, max_len: int) -> str:
    """Coerce ``value`` to a printable, length-capped string.

    Mirrors :func:`web.discovery._sanitize_beacon_text`: non-string input
    becomes ``""``, control characters are dropped (so an untrusted station_id
    / station_name can't inject terminal escapes or newlines into the web UI),
    and the result is capped at ``max_len`` to bound per-entry memory.
    """
    if not isinstance(value, str):
        return ""
    cleaned = "".join(ch for ch in value if ch.isprintable())
    return cleaned[:max_len]


class MarkerCatalogSync:
    """Background multicast sync service for MarkerCatalog.

    Two daemon threads (sender + receiver). on_change callback runs
    on receiver thread; UI mutations need dispatch to main thread.
    """

    def __init__(
        self,
        catalog: MarkerCatalog,
        station_id: str,
        *,
        station_name_provider: Callable[[], str],
        selection_provider: Callable[[], tuple[list[int], list[int]]],
        on_change: Callable[[list[int]], None] | None = None,
        iface_ip: str = "",
    ) -> None:
        self._catalog = catalog
        self._station_id = station_id
        self._station_name_provider = station_name_provider
        self._selection_provider = selection_provider
        self._on_change = on_change
        self._iface_ip = iface_ip

        self._stop_event = threading.Event()
        self._send_thread: threading.Thread | None = None
        self._recv_thread: threading.Thread | None = None

        self._pending_delta_ids: set[int] = set()
        self._pending_lock = threading.Lock()
        self._delta_due_at: float | None = None

        self._peer_selections: dict[str, PeerSelection] = {}
        self._peer_lock = threading.Lock()
        # -inf so the first cap warning always fires: time.monotonic() can start
        # below _PEER_CAP_LOG_INTERVAL at boot, which 0.0 would suppress for ~30s
        # during a startup flood. Mirrors web.discovery.
        self._peer_cap_log_ts = float("-inf")

    # -- Public API ----------------------------------------------------------

    def start(self) -> None:
        if self._send_thread is not None:
            return
        self._stop_event.clear()
        self._send_thread = threading.Thread(
            target=self._send_loop,
            daemon=True,
            name="MarkerCatalogSync-Tx",
        )
        self._recv_thread = threading.Thread(
            target=self._recv_loop,
            daemon=True,
            name="MarkerCatalogSync-Rx",
        )
        self._send_thread.start()
        self._recv_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._send_thread is not None:
            self._send_thread.join(timeout=HEARTBEAT_INTERVAL + 1.0)
            self._send_thread = None
        if self._recv_thread is not None:
            # Recv socket has 1s timeout; allow margin for in-flight packets.
            self._recv_thread.join(timeout=2.0)
            self._recv_thread = None

    def request_delta(self, ids: list[int]) -> None:
        """Queue a delta beacon covering ``ids`` (flushed after debounce)."""
        if not ids:
            return
        now = time.monotonic()
        with self._pending_lock:
            self._pending_delta_ids.update(int(i) for i in ids if int(i) >= 1)
            self._delta_due_at = now + DELTA_DEBOUNCE

    def get_peer_selections(self) -> list[PeerSelection]:
        """Return non-stale peer selections (excluding self)."""
        now = time.time()
        with self._peer_lock:
            self._prune_stale_peers_locked(now)
            return list(self._peer_selections.values())

    def _prune_stale_peers_locked(self, now: float) -> None:
        """Drop peer selections not seen within ``PEER_TIMEOUT``. Caller holds ``_peer_lock``."""
        stale = [k for k, v in self._peer_selections.items() if (now - v.last_seen) > PEER_TIMEOUT]
        for k in stale:
            del self._peer_selections[k]

    def _note_peer_cap(self) -> None:
        """Log the peer-table-full condition, throttled to avoid flood spam."""
        now = time.monotonic()
        if (now - self._peer_cap_log_ts) >= _PEER_CAP_LOG_INTERVAL:
            logger.warning(
                "MarkerCatalogSync: peer-selection table full at %d entries; dropping new "
                "stations until existing ones age out (possible beacon flood).",
                MAX_PEER_SELECTIONS,
            )
            self._peer_cap_log_ts = now

    # -- Send path ------------------------------------------------------------

    def _open_tx_socket(self) -> socket.socket:
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
                logger.warning(
                    "MarkerCatalogSync: iface IP %s not available (%s); sending on all interfaces.",
                    self._iface_ip,
                    exc,
                )
        return sock

    def _send_loop(self) -> None:
        sock: socket.socket | None = None
        next_heartbeat = time.monotonic()
        while not self._stop_event.is_set():
            if sock is None:
                try:
                    sock = self._open_tx_socket()
                except Exception:
                    logger.exception("MarkerCatalogSync: TX socket open failed")
                    self._stop_event.wait(HEARTBEAT_INTERVAL)
                    continue

            now = time.monotonic()
            delta_due = False
            with self._pending_lock:
                if self._delta_due_at is not None and now >= self._delta_due_at:
                    delta_due = True

            if delta_due:
                ids = self._consume_pending_ids()
                if ids:
                    entries = [e for e in self._catalog.all_entries() if e.id in ids]
                    if entries:
                        try:
                            self._send_packet(sock, kind="delta", entries=entries)
                        except OSError as exc:
                            sock = self._handle_send_error(sock, exc)
                            # Pending ids already consumed; back off to avoid spin.
                            self._stop_event.wait(_SEND_ERROR_BACKOFF)
                            continue

            if now >= next_heartbeat:
                entries = self._catalog.all_entries()
                try:
                    self._send_packet(sock, kind="heartbeat", entries=entries)
                except OSError as exc:
                    sock = self._handle_send_error(sock, exc)
                    # Advance clock and back off to avoid busy loop.
                    next_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL
                    self._stop_event.wait(_SEND_ERROR_BACKOFF)
                    continue
                next_heartbeat = now + HEARTBEAT_INTERVAL

            # Wake on the earlier of (delta_due, next_heartbeat).
            wait_until = next_heartbeat
            with self._pending_lock:
                if self._delta_due_at is not None:
                    wait_until = min(wait_until, self._delta_due_at)
            wait = max(0.0, wait_until - time.monotonic())
            self._stop_event.wait(wait)

        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _consume_pending_ids(self) -> set[int]:
        with self._pending_lock:
            ids = self._pending_delta_ids
            self._pending_delta_ids = set()
            self._delta_due_at = None
        return ids

    def _send_packet(
        self,
        sock: socket.socket,
        *,
        kind: str,
        entries: list[MarkerEntry],
    ) -> None:
        try:
            station_name = self._station_name_provider()
        except Exception:
            station_name = ""
        try:
            controlled, viewer = self._selection_provider()
        except Exception:
            controlled, viewer = [], []
        data = _build_beacon(
            kind=kind,
            station_id=self._station_id,
            station_name=station_name,
            controlled_ids=list(controlled),
            viewer_ids=list(viewer),
            entries=entries,
        )
        if len(data) > _MAX_TX_PACKET:
            # Don't truncate JSON; send selection-only beacon instead.
            fallback = _build_beacon(
                kind=kind,
                station_id=self._station_id,
                station_name=station_name,
                controlled_ids=list(controlled),
                viewer_ids=list(viewer),
                entries=[],
            )
            logger.error(
                "MarkerCatalogSync: %s payload %d bytes exceeds %d-byte cap; "
                "sending selection-only beacon (chunked entry beacons deferred).",
                kind,
                len(data),
                _MAX_TX_PACKET,
            )
            if len(fallback) > _MAX_TX_PACKET:
                # Selection-only shell still too big; drop beacon.
                logger.error(
                    "MarkerCatalogSync: selection-only fallback also %d bytes; dropping beacon.",
                    len(fallback),
                )
                return
            data = fallback
        sock.sendto(data, (CATALOG_MCAST_GROUP, CATALOG_PORT))

    def _handle_send_error(
        self,
        sock: socket.socket,
        exc: OSError,
    ) -> socket.socket | None:
        if exc.errno in _TRANSIENT_SEND_ERRNOS:
            logger.warning(
                "MarkerCatalogSync: transient send error %s; rebuilding socket.",
                exc,
            )
            try:
                sock.close()
            except OSError:
                pass
            return None
        logger.warning("MarkerCatalogSync: send failed: %s", exc)
        return sock

    # -- Receive path ---------------------------------------------------------

    def _recv_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        try:
            sock.bind(("", CATALOG_PORT))
        except OSError as exc:
            logger.error("MarkerCatalogSync: bind failed: %s", exc)
            sock.close()
            return

        iface_addr = socket.inet_aton(self._iface_ip) if self._iface_ip else socket.inet_aton("0.0.0.0")
        mreq = socket.inet_aton(CATALOG_MCAST_GROUP) + iface_addr
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError as exc:
            logger.warning(
                "MarkerCatalogSync: iface IP %s not available (%s); joining on all interfaces.",
                self._iface_ip or "0.0.0.0",
                exc,
            )
            try:
                mreq = socket.inet_aton(CATALOG_MCAST_GROUP) + socket.inet_aton("0.0.0.0")
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            except OSError as exc2:
                logger.error("MarkerCatalogSync: fallback join failed: %s", exc2)
                sock.close()
                return
        sock.settimeout(1.0)
        try:
            while not self._stop_event.is_set():
                try:
                    data, _addr = sock.recvfrom(_MAX_RX_PACKET)
                except TimeoutError:
                    continue
                except OSError:
                    continue
                self._handle_packet(data)
        finally:
            sock.close()

    def _handle_packet(self, data: bytes) -> None:
        decoded = _parse_beacon(data)
        if decoded is None:
            return
        sender_id = decoded.get("station_id")
        if not isinstance(sender_id, str) or not sender_id:
            return
        sender_id = _sanitize_text(sender_id, _STATION_ID_MAX_LEN)
        if not sender_id:
            return  # became empty after dropping control characters
        if sender_id == self._station_id:
            return  # ignore our own beacons

        # Record peer selection for UI display (subject to MAX_PEER_SELECTIONS cap), regardless of catalog merge.
        station_name = _sanitize_text(decoded.get("station_name", ""), _STATION_NAME_MAX_LEN)
        now = time.time()
        peer = PeerSelection(
            station_id=sender_id,
            station_name=station_name,
            controlled_ids=_coerce_id_list(decoded.get("controlled_ids")),
            viewer_ids=_coerce_id_list(decoded.get("viewer_ids")),
            last_seen=now,
        )
        # Cap the table like discovery.MAX_PEERS: prune stale entries before
        # refusing a new station, so a flood of unique station_ids can't grow
        # _peer_selections without bound between read-side prunes.
        refused = False
        with self._peer_lock:
            is_new = sender_id not in self._peer_selections
            if is_new and len(self._peer_selections) >= MAX_PEER_SELECTIONS:
                self._prune_stale_peers_locked(now)
                if len(self._peer_selections) >= MAX_PEER_SELECTIONS:
                    refused = True
            if not refused:
                self._peer_selections[sender_id] = peer
        if refused:
            self._note_peer_cap()

        raw_entries = decoded.get("entries", [])
        if not isinstance(raw_entries, list):
            return
        changed: list[int] = []
        for raw in raw_entries:
            entry = _entry_from_dict(raw)
            if entry is None:
                continue
            if self._catalog.merge_entry(entry):
                changed.append(entry.id)
        # Rate ceiling: on_change fires synchronously on the recv thread once
        # per heartbeat that merges any change, so a peer toggling updated_at
        # every heartbeat couples inbound multicast to the callback's cost
        # (the app persists, fsync + atomic rename). Writes are serialized and
        # atomic; a persistence consumer that wants to decouple should debounce
        # on its side (the send path already debounces deltas).
        if changed and self._on_change is not None:
            try:
                self._on_change(changed)
            except Exception:
                logger.exception("MarkerCatalogSync: on_change callback failed")
