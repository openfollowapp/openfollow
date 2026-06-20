# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""ANSI E1.59-2021 OTP (Object Transform Protocol) output server over UDP multicast."""

from __future__ import annotations

import contextlib
import errno
import logging
import math
import socket
import struct
import threading
import time
from uuid import uuid4

import multicast_expert

from openfollow.psn.marker import Marker

logger = logging.getLogger(__name__)

# Transient errno values (same as PsnServer, kept local to avoid coupling).
_TRANSIENT_SEND_ERRNOS: frozenset[int] = frozenset(
    {
        errno.EADDRNOTAVAIL,
        errno.ENETUNREACH,
        errno.ENETDOWN,
        errno.EHOSTDOWN,
        errno.EHOSTUNREACH,
    }
)

# E1.59 protocol constants (ANSI standard).
OTP_PACKET_IDENTIFIER = b"OTP-E1.59\x00\x00\x00"

# Vector types for OTP messages (2 octets per Table A-28).
VECTOR_OTP_TRANSFORM_MESSAGE = 0x0001
VECTOR_OTP_ADVERTISEMENT_MESSAGE = 0x0002
VECTOR_OTP_POINT = 0x0001
VECTOR_OTP_MODULE = 0x0001
VECTOR_OTP_ADVERTISEMENT_MODULE = 0x0001
VECTOR_OTP_ADVERTISEMENT_NAME = 0x0002
VECTOR_OTP_ADVERTISEMENT_SYSTEM = 0x0003
VECTOR_OTP_ADVERTISEMENT_MODULE_LIST = 0x0001
VECTOR_OTP_ADVERTISEMENT_NAME_LIST = 0x0001
VECTOR_OTP_ADVERTISEMENT_SYSTEM_LIST = 0x0001

# ESTA Manufacturer ID and position module constant.
ESTA_MANUFACTURER_ID = 0x0000
MODULE_NUMBER_POSITION = 0x0001

# Standard port and message limits.
OTP_PORT = 5568
COMPONENT_NAME_OCTETS = 32
POINT_NAME_OCTETS = 32
MAX_OTP_MESSAGE_OCTETS = 1472

# Timing: advertisements every 10s; transforms governed by fps (20-1000 Hz).
ADVERTISEMENT_INTERVAL_S = 10.0

# Meters to micrometers (Position Module transmission).
_M_TO_UM = 1_000_000

ADVERTISEMENT_MCAST_IP = "239.159.2.1"


def transform_mcast_ip(system_number: int) -> str:
    """Spec-mandated transform multicast address for a given System Number.

    Table 15-19: ``239.159.1.<System Number>``. The System Number itself
    must be 1–200 inclusive (Section 8.3); the caller (config layer)
    enforces that – we just substitute.
    """
    return f"239.159.1.{system_number}"


# Defaults preserved for backwards compatibility with code that imported
# them. The "right" transform address is derived from system_number; this
# default exists only so the OtpOutputConfig dataclass can field a value
# during migration of existing config.toml files that still carry
# ``mcast_ip = "239.159.1.0"``.
DEFAULT_MCAST_IP = "239.159.1.1"
DEFAULT_PORT = OTP_PORT

_MAX_SOCKET_RETRIES = 3
_SOCKET_RETRY_DELAY = 2.0  # seconds


# ---------------------------------------------------------------------------
# Encoding primitives
# ---------------------------------------------------------------------------


def _encode_fixed_name(name: str, octets: int) -> bytes:
    """Encode ``name`` as exactly ``octets`` bytes of UTF-8.

    Per Section 6.12 / 13.5.1: shorter names are null-padded; longer names
    are truncated on a UTF-8 rune boundary, then null-padded back up to the
    fixed width. The rune-boundary requirement matters because a naive
    ``encoded[:octets]`` could split a multi-byte sequence and produce
    invalid UTF-8 that a strict consumer would reject.
    """
    raw = name.encode("utf-8")
    if len(raw) <= octets:
        return raw + b"\x00" * (octets - len(raw))
    # Walk back from the cut until we land on a non-continuation byte
    # (continuation bytes are 0x80..0xBF in UTF-8).
    cut = octets
    while cut > 0 and (raw[cut] & 0xC0) == 0x80:
        cut -= 1
    return raw[:cut] + b"\x00" * (octets - cut)


def _build_otp_layer(
    *,
    vector: int,
    cid: bytes,
    folio: int,
    page: int,
    last_page: int,
    component_name: str,
    inner_pdu: bytes,
) -> bytes:
    """Encode the OTP Layer (Table 6-3) wrapping ``inner_pdu``.

    The Length field per Section 6.3 covers everything from octet 16
    (Footer Options) through the end of the inner PDU – i.e. the whole
    body excluding the Packet Identifier(12) + Vector(2) + Length(2) prefix.
    """
    if len(cid) != 16:
        raise ValueError(f"CID must be 16 octets, got {len(cid)}")
    body = (
        b"\x00"  # Footer Options (Section 6.4)
        b"\x00"  # Footer Length (Section 6.5) – no footer
        + cid  # CID (Section 6.6)
        + struct.pack("!I", folio & 0xFFFFFFFF)  # Folio Number (Section 6.7)
        + struct.pack("!HH", page, last_page)  # Page / Last Page (Sections 6.8, 6.9)
        + b"\x00"  # Options (Section 6.10) – reserved
        + b"\x00\x00\x00\x00"  # Reserved (Section 6.11)
        + _encode_fixed_name(component_name, COMPONENT_NAME_OCTETS)
        + inner_pdu
    )
    return OTP_PACKET_IDENTIFIER + struct.pack("!HH", vector, len(body)) + body


_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1


def _metres_to_um_i32(value: float) -> int:
    """Metres → micrometres clamped to signed int32 (the OTP position field).

    A non-finite (NaN/inf) or out-of-range position – bad calibration, a large
    ``max_height``, or a tracking glitch – would otherwise raise
    ``OverflowError`` / ``struct.error`` from ``int()`` / ``struct.pack`` and
    silently kill the transform thread. Clamp instead so one bad value can't
    take OTP output down."""
    if not math.isfinite(value):
        return 0
    return max(_INT32_MIN, min(_INT32_MAX, int(value * _M_TO_UM)))


def _build_position_module(x_um: int, y_um: int, z_um: int) -> bytes:
    """Encode a Position Module PDU (Table 16-22).

    Module Layer header (Table 10-13): Manufacturer ID(2) + Length(2) +
    Module Number(2) + module-specific data. The Length field per Section
    10.2 covers Module Number + data, i.e. everything after the Length
    field itself.

    Position Module data (Table 16-22): Options(1, bit 7 = mm scaling) +
    X(int32) + Y(int32) + Z(int32). We always transmit µm (Options = 0).
    """
    options = 0x00  # bit 7 = 0 → values are µm; bit 7 = 1 would mean mm
    data = struct.pack("!Bi i i", options, x_um, y_um, z_um)
    after_length = struct.pack("!H", MODULE_NUMBER_POSITION) + data
    return struct.pack("!HH", ESTA_MANUFACTURER_ID, len(after_length)) + after_length


def _build_point_layer(
    *,
    priority: int,
    group: int,
    point: int,
    sampled_timestamp_us: int,
    module_pdus: bytes,
) -> bytes:
    """Encode a Point Layer (Table 9-12) wrapping one or more module PDUs.

    Length per Section 9.2 excludes the Vector and Length fields themselves.
    """
    body = (
        struct.pack("!B", priority & 0xFF)  # Priority (Section 9.3) – 0–200
        + struct.pack("!H", group & 0xFFFF)  # Group Number (Section 9.4) – 1–60_000
        + struct.pack("!I", point & 0xFFFFFFFF)  # Point Number (Section 9.5) – 1–4_000_000_000
        + struct.pack("!Q", sampled_timestamp_us & 0xFFFFFFFFFFFFFFFF)
        + b"\x00"  # Options (Section 9.7) – reserved
        + b"\x00\x00\x00\x00"  # Reserved (Section 9.8)
        + module_pdus
    )
    # Point Layer's own Vector identifies the contained Module PDUs
    # (Table 9-12). VECTOR_OTP_POINT and VECTOR_OTP_MODULE share the
    # value 0x0001 but mean different things at different layers.
    return struct.pack("!HH", VECTOR_OTP_MODULE, len(body)) + body


def _build_transform_pdu(
    *,
    system_number: int,
    timestamp_us: int,
    full_point_set: bool,
    point_pdus: bytes,
) -> bytes:
    """Transform Layer (Table 8-11) wrapping point PDUs.

    Section 8.5 – bit 7 of Options is the Full Point Set flag. Each Point
    PDU in ``point_pdus`` is itself prefixed with its own Vector
    (``VECTOR_OTP_POINT``) by ``_build_point_layer``; the Transform
    Layer's own Vector is set by the OTP Layer above (which uses
    ``VECTOR_OTP_TRANSFORM_MESSAGE`` to identify the Transform PDU it
    carries).
    """
    options = 0x80 if full_point_set else 0x00
    body = (
        struct.pack("!B", system_number & 0xFF)
        + struct.pack("!Q", timestamp_us & 0xFFFFFFFFFFFFFFFF)
        + struct.pack("!B", options)
        + b"\x00\x00\x00\x00"  # Reserved (Section 8.6)
        + point_pdus
    )
    return struct.pack("!HH", VECTOR_OTP_POINT, len(body)) + body


def _build_advertisement_pdu(
    *,
    advertisement_vector: int,
    inner_pdu: bytes,
) -> bytes:
    """OTP Advertisement Layer (Table 11-14).

    Carries one of: Module / Name / System Advertisement Layer, identified
    by ``advertisement_vector`` (one of VECTOR_OTP_ADVERTISEMENT_*).
    Reserved is 4 octets per Section 11.3.
    """
    body = b"\x00\x00\x00\x00" + inner_pdu  # Reserved(4) + inner
    return struct.pack("!HH", advertisement_vector, len(body)) + body


def _build_module_advertisement_layer(module_identifiers: list[tuple[int, int]]) -> bytes:
    """OTP Module Advertisement Layer (Table 12-15).

    Vector + Length + Reserved(4) + List<{ManufId(2), ModuleNumber(2)}>.

    Section 12.4 specifies a flat, sorted list – there's no per-point
    nesting and no timestamps. The list is sorted by ``(ManufId,
    ModuleNumber)`` ascending.
    """
    sorted_ids = sorted(module_identifiers)
    list_bytes = b"".join(struct.pack("!HH", m, n) for m, n in sorted_ids)
    body = b"\x00\x00\x00\x00" + list_bytes
    return struct.pack("!HH", VECTOR_OTP_ADVERTISEMENT_MODULE_LIST, len(body)) + body


def _build_name_advertisement_layer(
    *,
    response: bool,
    address_point_descriptions: list[tuple[int, int, int, str]],
) -> bytes:
    """OTP Name Advertisement Layer (Table 13-16, Figure 13-14).

    Vector + Length + Options(1) + Reserved(4) + List<APD>.

    Address Point Description (Figure 13-14): System(1) + Group(2) +
    Point(4) + Name(32 octets UTF-8). The list is sorted by
    ``(System, Group, Point)`` ascending per Section 13.5.

    Section 13.3 – Options bit 7: 0 = request from a Consumer, 1 =
    response from a Producer. We always send as ``response = True``
    because we don't implement the request side.
    """
    sorted_apds = sorted(address_point_descriptions, key=lambda t: (t[0], t[1], t[2]))
    list_bytes = b"".join(
        struct.pack("!BHI", sys_n & 0xFF, grp & 0xFFFF, pt & 0xFFFFFFFF) + _encode_fixed_name(name, POINT_NAME_OCTETS)
        for sys_n, grp, pt, name in sorted_apds
    )
    options = 0x80 if response else 0x00
    body = struct.pack("!B", options) + b"\x00\x00\x00\x00" + list_bytes
    return struct.pack("!HH", VECTOR_OTP_ADVERTISEMENT_NAME_LIST, len(body)) + body


def _build_system_advertisement_layer(
    *,
    response: bool,
    system_numbers: list[int],
) -> bytes:
    """OTP System Advertisement Layer (Table 14-17).

    Vector + Length + Options(1) + Reserved(4) + List<System Number(1)>.

    The list is sorted ascending per Section 14.5. Same Options
    request/response convention as the Name layer.
    """
    sorted_systems = sorted(system_numbers)
    list_bytes = bytes(s & 0xFF for s in sorted_systems)
    options = 0x80 if response else 0x00
    body = struct.pack("!B", options) + b"\x00\x00\x00\x00" + list_bytes
    return struct.pack("!HH", VECTOR_OTP_ADVERTISEMENT_SYSTEM_LIST, len(body)) + body


# ---------------------------------------------------------------------------
# Top-level packet builders
# ---------------------------------------------------------------------------


def encode_otp_transform_packet(
    *,
    cid: bytes,
    component_name: str,
    folio: int,
    system_number: int,
    timestamp_us: int,
    markers: list[Marker],
    priority: int,
    group: int = 1,
    sampled_timestamp_us: int | None = None,
    full_point_set: bool = True,
) -> bytes:
    """Build a complete OTP Transform Message UDP payload.

    ``sampled_timestamp_us`` defaults to the same value as ``timestamp_us``
    – Section 9.6 says the sampled timestamp is the moment the Producer
    read the Point's transform; for a single-pass encoder there's no
    distinction.
    """
    sampled = timestamp_us if sampled_timestamp_us is None else sampled_timestamp_us
    # Collect into a list and ``b"".join(...)`` instead of ``+=`` on
    # immutable bytes – at 60 Hz with N markers, repeated ``+=``
    # allocates and copies in O(N²); join is O(N). Same pattern as
    # ``openfollow/rttrpm/server.py``.
    point_pdu_parts: list[bytes] = []
    for marker in markers:
        x, y, z = marker.pos
        x_um = _metres_to_um_i32(x)
        y_um = _metres_to_um_i32(y)
        z_um = _metres_to_um_i32(z)
        # OTP point numbers start at 1 (Section 9.5). Project convention
        # reserves marker_id 0 as "ignored", so marker_id 1 maps directly
        # to point 1 – no +1 offset needed any more.
        point_pdu_parts.append(
            _build_point_layer(
                priority=priority,
                group=group,
                point=marker.marker_id,
                sampled_timestamp_us=sampled,
                module_pdus=_build_position_module(x_um, y_um, z_um),
            )
        )
    transform = _build_transform_pdu(
        system_number=system_number,
        timestamp_us=timestamp_us,
        full_point_set=full_point_set,
        point_pdus=b"".join(point_pdu_parts),
    )
    packet = _build_otp_layer(
        vector=VECTOR_OTP_TRANSFORM_MESSAGE,
        cid=cid,
        folio=folio,
        page=0,
        last_page=0,
        component_name=component_name,
        inner_pdu=transform,
    )
    if len(packet) > MAX_OTP_MESSAGE_OCTETS:
        # Section 6.3.1 – hard cap. Page splitting is intentionally out of
        # scope for the pragmatic compliance level; fail loud
        # so we know to revisit if a real installation ever needs it.
        raise ValueError(
            f"OTP transform packet of {len(packet)} octets exceeds spec maximum "
            f"of {MAX_OTP_MESSAGE_OCTETS}; reduce marker count or implement "
            f"Page splitting (Section 6.8).",
        )
    return packet


def encode_otp_module_advertisement_packet(
    *,
    cid: bytes,
    component_name: str,
    folio: int,
) -> bytes:
    """Build a complete OTP Module Advertisement Message UDP payload.

    Per Section 7.4.2 this layer is normally sent by *Consumers* declaring
    which modules they want to receive. We emit it as a producer for
    interoperability and discoverability – compliant consumers tolerate it
    and use it for status/diagnostics. We advertise the single module we
    populate (Standard Position).
    """
    module_layer = _build_module_advertisement_layer(
        module_identifiers=[(ESTA_MANUFACTURER_ID, MODULE_NUMBER_POSITION)],
    )
    advertisement = _build_advertisement_pdu(
        advertisement_vector=VECTOR_OTP_ADVERTISEMENT_MODULE,
        inner_pdu=module_layer,
    )
    return _build_otp_layer(
        vector=VECTOR_OTP_ADVERTISEMENT_MESSAGE,
        cid=cid,
        folio=folio,
        page=0,
        last_page=0,
        component_name=component_name,
        inner_pdu=advertisement,
    )


def encode_otp_name_advertisement_packet(
    *,
    cid: bytes,
    component_name: str,
    folio: int,
    system_number: int,
    markers: list[Marker],
    group: int = 1,
) -> bytes:
    """Build a complete OTP Name Advertisement Message UDP payload."""
    apds = [(system_number, group, marker.marker_id, marker.name) for marker in markers]
    name_layer = _build_name_advertisement_layer(
        response=True,
        address_point_descriptions=apds,
    )
    advertisement = _build_advertisement_pdu(
        advertisement_vector=VECTOR_OTP_ADVERTISEMENT_NAME,
        inner_pdu=name_layer,
    )
    return _build_otp_layer(
        vector=VECTOR_OTP_ADVERTISEMENT_MESSAGE,
        cid=cid,
        folio=folio,
        page=0,
        last_page=0,
        component_name=component_name,
        inner_pdu=advertisement,
    )


def encode_otp_system_advertisement_packet(
    *,
    cid: bytes,
    component_name: str,
    folio: int,
    system_number: int,
) -> bytes:
    """Build a complete OTP System Advertisement Message UDP payload."""
    system_layer = _build_system_advertisement_layer(
        response=True,
        system_numbers=[system_number],
    )
    advertisement = _build_advertisement_pdu(
        advertisement_vector=VECTOR_OTP_ADVERTISEMENT_SYSTEM,
        inner_pdu=system_layer,
    )
    return _build_otp_layer(
        vector=VECTOR_OTP_ADVERTISEMENT_MESSAGE,
        cid=cid,
        folio=folio,
        page=0,
        last_page=0,
        component_name=component_name,
        inner_pdu=advertisement,
    )


# ---------------------------------------------------------------------------
# OtpServer
# ---------------------------------------------------------------------------


class OtpServer:
    """Sends ANSI E1.59 OTP marker position data via multicast UDP.

    Multicast destinations are derived from the configured System Number
    per Table 15-19:
        Transform packets:     ``239.159.1.<system_number>``
        Advertisement packets: ``239.159.2.1``

    Accepts references to existing ``Marker`` objects (from
    ``openfollow.psn.marker``) so PSN and OTP share position state
    without duplication.

    Two background threads:
    - Transform thread (``fps`` Hz, default 60): position data.
    - Advertisement thread (every ``ADVERTISEMENT_INTERVAL_S`` = 10s):
      Module + Name + System advertisement PDUs.

    The ``mcast_ip`` constructor argument is **test-only**. Production
    callers should never pass it – when it's left as the default
    ``None``, addresses are derived per spec from ``system_number``. An
    empty string selects the unicast/loopback debug branch (used by
    ``tests/test_otp.py::TestOtpUnicastStart``); any other string forces
    every packet (transform and advertisement) to that address, which is
    only useful when capturing into a single recv-buffer for a test.
    """

    def __init__(
        self,
        system_name: str = "OpenFollow",
        system_number: int = 1,
        port: int = OTP_PORT,
        source_ip: str = "",
        fps: float = 60.0,
        priority: int = 100,
        *,
        mcast_ip: str | None = None,
    ) -> None:
        self._system_name = system_name
        self._system_number = system_number
        self._port = port
        self._source_ip = source_ip.strip()
        self._fps = fps
        self._priority = priority

        self._mcast_ip_override = mcast_ip
        self._transform_dest, self._advertisement_dest = self._resolve_destinations()
        # Kept for backwards-compat with services.py snapshot/rollback path
        # and existing tests that read `_mcast_ip`. None is the spec-derived
        # mode; "" is unicast; otherwise it's an explicit override.
        self._mcast_ip: str = "" if mcast_ip == "" else self._transform_dest

        # Unique component identifier per Section 6.6 (UUID, RFC 4122).
        self._cid: bytes = uuid4().bytes

        # Shared marker references (keyed by marker_id)
        self._markers: dict[int, Marker] = {}
        self._lock = threading.Lock()

        # Section 6.7 – separate Folio counters per advertisement type and
        # per system for transforms. We only emit one system, so a single
        # transform counter suffices.
        self._transform_folio: int = 0
        self._module_adv_folio: int = 0
        self._name_adv_folio: int = 0
        self._system_adv_folio: int = 0

        # Relative timestamp origin (µs, from time.monotonic).
        self._start_time_us: int = int(time.monotonic() * 1_000_000)

        self._stop_event = threading.Event()
        self._socket: multicast_expert.McastTxSocket | socket.socket | None = None
        self._exit_stack: contextlib.ExitStack = contextlib.ExitStack()
        self._transform_thread: threading.Thread | None = None
        self._adv_thread: threading.Thread | None = None
        self._socket_thread: threading.Thread | None = None
        self._send_errors: int = 0
        self._send_total: int = 0
        # Counter for oversized-packet drops. The length-cap (Section
        # 6.3.1) only fires on misconfigured installs (~70+ markers in
        # one folio); without throttling we'd flood logs at the
        # transform fps. Same first-5-then-every-100 pattern ``_send``
        # uses for OSError logging.
        self._oversize_drops: int = 0

    def _resolve_destinations(self) -> tuple[str, str]:
        """Compute (transform_dest, advertisement_dest) for current config."""
        override = self._mcast_ip_override
        if override is None:
            return transform_mcast_ip(self._system_number), ADVERTISEMENT_MCAST_IP
        if override == "":
            # Unicast/loopback mode – both streams go to localhost. Tests
            # that exercise the non-multicast socket path rely on this.
            return "127.0.0.1", "127.0.0.1"
        return override, override

    # -- Marker management ---------------------------------------------------

    def register_marker(self, marker: Marker) -> None:
        """Register an existing Marker reference for OTP output."""
        with self._lock:
            self._markers[marker.marker_id] = marker

    def unregister_marker(self, marker_id: int) -> None:
        """Remove a marker from OTP output (no-op if absent)."""
        with self._lock:
            self._markers.pop(marker_id, None)

    def get_marker(self, marker_id: int) -> Marker | None:
        """Return a registered marker by ID, or ``None``."""
        with self._lock:
            return self._markers.get(marker_id)

    # -- Lifecycle ------------------------------------------------------------

    def start(self) -> None:
        """Open the network socket and start transform + advertisement threads."""
        self._stop_event.clear()
        self._exit_stack = contextlib.ExitStack()
        if self._is_multicast_mode():
            if not self._try_open_multicast_socket_once(attempt=1):
                self._socket_thread = threading.Thread(
                    target=self._retry_multicast_socket_background,
                    daemon=True,
                    name="OTP-SocketRetry",
                )
                self._socket_thread.start()
        else:
            self._socket = self._exit_stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
        self._adv_thread = threading.Thread(target=self._advertisement_loop, daemon=True, name="OTP-Advertisement")
        self._transform_thread = threading.Thread(target=self._transform_loop, daemon=True, name="OTP-Transform")
        # Start advertisement first so receivers see module info before data.
        self._adv_thread.start()
        self._transform_thread.start()

    def _is_multicast_mode(self) -> bool:
        """True unless the test-only unicast/loopback branch is active."""
        return self._mcast_ip_override != ""

    def stop(self) -> None:
        """Signal threads to stop, wait for them, then close the socket."""
        self._stop_event.set()
        if self._socket_thread is not None:
            self._socket_thread.join(timeout=_SOCKET_RETRY_DELAY + 1.0)
            if self._socket_thread.is_alive():
                logger.warning("OTP socket-retry thread did not stop within timeout")
            self._socket_thread = None
        if self._transform_thread is not None:
            self._transform_thread.join(timeout=1.0)
            if self._transform_thread.is_alive():
                logger.warning("OTP transform thread did not stop within timeout")
            self._transform_thread = None
        if self._adv_thread is not None:
            self._adv_thread.join(timeout=2.0)
            if self._adv_thread.is_alive():
                logger.warning("OTP advertisement thread did not stop within timeout")
            self._adv_thread = None
        self._socket = None
        self._exit_stack.close()

    def restart(
        self,
        *,
        system_name: str,
        system_number: int,
        port: int,
        source_ip: str,
        priority: int,
    ) -> None:
        """Stop, reconfigure, and restart in place.

        Marker registrations survive – only the UDP socket and worker
        threads recycle. Used by ``apply_otp_output_change`` to apply ``otp_output.*`` edits without a process restart.

        Raises ``OSError`` when the multicast socket can't be opened
        synchronously on the new interface – ``start()`` itself spawns a
        daemon retry thread instead, which is the right behaviour at boot
        but the wrong one for live-apply, where the dispatcher needs a
        hard signal to revert ``app._config.otp_output``. We tear back
        down before raising so the retry thread doesn't keep poking at a
        stale config.
        """
        self.stop()
        self._system_name = system_name
        self._system_number = system_number
        self._port = port
        self._source_ip = source_ip.strip()
        self._priority = priority
        # Recompute destinations for the new system_number.
        self._transform_dest, self._advertisement_dest = self._resolve_destinations()
        if self._mcast_ip_override is None:
            self._mcast_ip = self._transform_dest
        self.start()
        if self._is_multicast_mode() and self._socket is None:
            self.stop()
            raise OSError(
                f"OTP failed to open multicast socket for system_number="
                f"{self._system_number} (transform={self._transform_dest!r}, "
                f"advertisement={self._advertisement_dest!r}, "
                f"source_ip={self._source_ip!r})",
            )

    def update_system_name(self, name: str) -> None:
        """Update the human-readable system name carried in OTP Layer's
        Component Name field (Section 6.12).

        Live-applied without a socket restart, mirroring ``PsnServer``.
        """
        with self._lock:
            self._system_name = name

    def __enter__(self) -> OtpServer:
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()

    # -- Socket helpers -------------------------------------------------------

    def _multicast_groups(self) -> list[str]:
        """Multicast groups this socket needs to send to.

        Production: transform group + advertisement group. Test override:
        a single forced address. ``McastTxSocket`` sets the right outgoing
        interface for each group on each ``sendto``.
        """
        override = self._mcast_ip_override
        # Mypy can't narrow ``str | None not in (None, "")`` automatically,
        # so check both predicates explicitly to keep the str-only branch.
        if override is not None and override != "":
            return [override]
        return [self._transform_dest, self._advertisement_dest]

    def _try_open_multicast_socket_once(self, attempt: int) -> bool:
        """Attempt to create the multicast TX socket once. Returns True on success."""
        try:
            groups = self._multicast_groups()
            if self._source_ip:
                sock = multicast_expert.McastTxSocket(
                    socket.AF_INET,
                    mcast_ips=groups,
                    iface_ip=self._source_ip,
                    enable_external_loopback=True,
                )
            else:
                sock = multicast_expert.McastTxSocket(
                    socket.AF_INET,
                    mcast_ips=groups,
                    enable_external_loopback=True,
                )
            self._socket = self._exit_stack.enter_context(sock)
            return True
        except Exception as exc:
            logger.warning(
                "OTP multicast socket failed (attempt %d/%d): %s",
                attempt,
                _MAX_SOCKET_RETRIES,
                exc,
            )
            return False

    def _retry_multicast_socket_background(self) -> None:
        """Retry multicast socket creation in the background (bounded)."""
        for attempt in range(2, _MAX_SOCKET_RETRIES + 1):
            self._stop_event.wait(_SOCKET_RETRY_DELAY)
            if self._stop_event.is_set():
                return
            if self._try_open_multicast_socket_once(attempt=attempt):
                logger.info(
                    "OTP multicast socket connected on retry %d/%d.",
                    attempt,
                    _MAX_SOCKET_RETRIES,
                )
                return
        logger.error(
            "OTP multicast socket failed after %d attempts. OTP output disabled – check your network interface IP.",
            _MAX_SOCKET_RETRIES,
        )

    def _recover_multicast_socket_background(self) -> None:
        """Re-open the multicast socket indefinitely after a transient
        send failure. See ``PsnServer._recover_multicast_socket_background``
        for the same rationale.
        """
        attempt = 0
        while not self._stop_event.is_set():
            attempt += 1
            self._stop_event.wait(_SOCKET_RETRY_DELAY)
            if self._stop_event.is_set():
                return
            if self._try_open_multicast_socket_once(attempt=attempt):
                logger.info("OTP multicast socket recovered on attempt %d.", attempt)
                return

    def _handle_send_error(self, exc: OSError) -> None:
        """On a transient interface-change error, rebuild the socket in the background."""
        # Once stopping, teardown owns the socket/exit-stack lifecycle. A recovery
        # thread spawned here is orphaned (stop() may already have passed the
        # socket-thread join) and, after restart()'s start() clears the stop
        # event, could open a SECOND multicast socket racing the fresh server –
        # clobbering self._socket and leaking the FD. Mirrors PsnServer.
        if self._stop_event.is_set():
            return
        if exc.errno not in _TRANSIENT_SEND_ERRNOS:
            return
        # Unicast mode has no multicast socket to rebuild – the recovery
        # helper would call _try_open_multicast_socket_once with no
        # multicast groups and permanently disable output. Unicast UDP is
        # connectionless, so the next send will simply try again.
        if not self._is_multicast_mode():
            return
        with self._lock:
            if self._socket_thread is not None and self._socket_thread.is_alive():
                return
            old_stack = self._exit_stack
            self._socket = None
            self._exit_stack = contextlib.ExitStack()
            self._socket_thread = threading.Thread(
                target=self._recover_multicast_socket_background,
                daemon=True,
                name="OTP-SocketRecover",
            )
            self._socket_thread.start()
        try:
            old_stack.close()
        except Exception:
            logger.exception("OTP: closing stale socket stack failed")

    # -- Send loops -----------------------------------------------------------

    def _transform_loop(self) -> None:
        interval = 1.0 / self._fps
        while not self._stop_event.is_set():
            self._send_transform_packet()
            self._stop_event.wait(interval)

    def _advertisement_loop(self) -> None:
        while not self._stop_event.is_set():
            self._send_advertisement_packets()
            self._stop_event.wait(ADVERTISEMENT_INTERVAL_S)

    def _snapshot_markers(self) -> list[Marker]:
        with self._lock:
            return list(self._markers.values())

    def _next_folio(self, name: str) -> int:
        attr = f"_{name}_folio"
        # ``getattr`` returns Any for dynamic lookup; cast through ``int()``
        # so mypy can keep the declared return type. All four counters
        # are typed ``int`` at __init__ time.
        value = int(getattr(self, attr))
        setattr(self, attr, (value + 1) & 0xFFFFFFFF)
        return value

    def _current_timestamp(self) -> int:
        return int(time.monotonic() * 1_000_000) - self._start_time_us

    def _send_transform_packet(self) -> None:
        markers = self._snapshot_markers()
        if not markers:
            return
        try:
            payload = encode_otp_transform_packet(
                cid=self._cid,
                component_name=self._system_name,
                folio=self._next_folio("transform"),
                system_number=self._system_number,
                timestamp_us=self._current_timestamp(),
                markers=markers,
                priority=self._priority,
            )
        except ValueError as exc:
            # Length-cap blew (Section 6.3.1, 1472-octet hard cap).
            # This is a config-error path: oversize means too many
            # markers in one folio – the condition won't fix itself
            # without operator action, so we throttle to first-5-then-
            # every-100th occurrence. At 60 fps that's ~1.7s between
            # log lines after the initial burst, which is enough to
            # diagnose without flooding. Drop the packet rather than
            # killing the loop.
            with self._lock:
                self._oversize_drops += 1
                drops = self._oversize_drops
            if drops <= 5 or drops % 100 == 0:
                logger.warning(
                    "OTP transform packet skipped (%d drops): %s",
                    drops,
                    exc,
                )
            return
        self._send(payload, self._transform_dest)

    def _send_advertisement_packets(self) -> None:
        """Send Module, Name, and System advertisement packets in sequence."""
        markers = self._snapshot_markers()

        if markers:
            self._send(
                encode_otp_module_advertisement_packet(
                    cid=self._cid,
                    component_name=self._system_name,
                    folio=self._next_folio("module_adv"),
                ),
                self._advertisement_dest,
            )
            self._send(
                encode_otp_name_advertisement_packet(
                    cid=self._cid,
                    component_name=self._system_name,
                    folio=self._next_folio("name_adv"),
                    system_number=self._system_number,
                    markers=markers,
                ),
                self._advertisement_dest,
            )

        self._send(
            encode_otp_system_advertisement_packet(
                cid=self._cid,
                component_name=self._system_name,
                folio=self._next_folio("system_adv"),
                system_number=self._system_number,
            ),
            self._advertisement_dest,
        )

    def _send(self, data: bytes, dest_ip: str) -> None:
        sock = self._socket
        if sock is None:
            return
        with self._lock:
            self._send_total += 1
        try:
            sock.sendto(data, (dest_ip, self._port))
        except OSError as exc:
            with self._lock:
                self._send_errors += 1
                errors = self._send_errors
                total = self._send_total
            if errors <= 5 or errors % 100 == 0:
                logger.warning(
                    "OTP send failed (%d/%d errors): %s",
                    errors,
                    total,
                    exc,
                )
            self._handle_send_error(exc)
