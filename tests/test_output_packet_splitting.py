# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Every output protocol keeps its datagrams inside the MTU as markers scale.

A datagram past the UDP payload of a 1500-octet Ethernet MTU is fragmented by
IP, which a quiet bench LAN reassembles and a loaded show network drops - so the
failure appears only where it cannot be debugged. Each protocol spreads a large
marker set differently: PSN over the packets of one frame, OTP over the pages of
one folio, RTTrPM over independent packets. What none of them may do is emit an
oversize datagram or lose a marker in the split.

Datagrams are captured at each server's send seam and read back as bytes,
because the bytes are what a receiver has to make sense of.
"""

from __future__ import annotations

import struct
from unittest.mock import MagicMock

import pypsn
import pytest

from openfollow.otp.server import (
    OTP_PACKET_IDENTIFIER,
    VECTOR_OTP_ADVERTISEMENT_NAME,
    OtpServer,
)
from openfollow.packet_chunking import MAX_DATAGRAM_BYTES
from openfollow.psn.marker import Marker
from openfollow.psn.receiver import PsnReceiver
from openfollow.psn.server import PsnServer
from openfollow.rttrpm.server import RttrpmServer

pytestmark = pytest.mark.unit

# Long enough to be an ordinary operator label, which is what pushes the
# name-carrying packets over the line well before the position-carrying ones.
_LONG_NAME = "Followspot Operator Position {:02d}"


def _markers(count: int, name_template: str = "Marker {}") -> list[Marker]:
    markers = []
    for index in range(1, count + 1):
        marker = Marker(index, name_template.format(index))
        marker.set_pos(1.5, -2.5, 3.5)
        markers.append(marker)
    return markers


# --- capture helpers: drive the real send paths, keep the datagrams ----------


def _psn_datagrams(count: int, name_template: str = "Marker {}") -> tuple[list[bytes], list[bytes]]:
    """Return the (data, info) datagrams one frame of each stream puts on the wire."""
    server = PsnServer(mcast_ip=None)
    sent: list[bytes] = []
    server._send = lambda data, stop_event=None: sent.append(data)  # type: ignore[method-assign]
    for marker in _markers(count, name_template):
        server._markers[marker.marker_id] = marker
    server._send_data_packet()
    data = list(sent)
    sent.clear()
    server._send_info_packet()
    return data, list(sent)


def _otp_datagrams(count: int, name_template: str = "Marker {}") -> tuple[list[bytes], list[bytes]]:
    """Return the (transform, advertisement) datagrams the OTP server emits."""
    server = OtpServer(system_number=1)
    sent: list[bytes] = []
    server._send = lambda data, dest_ip, stop_event=None: sent.append(data)  # type: ignore[method-assign]
    for marker in _markers(count, name_template):
        server.register_marker(marker)
    server._send_transform_packet()
    transform = list(sent)
    sent.clear()
    server._send_advertisement_packets()
    return transform, list(sent)


def _rttrpm_datagrams(count: int, name_template: str = "Marker {}") -> list[bytes]:
    server = RttrpmServer(host="127.0.0.1")
    server._socket = MagicMock()
    sent: list[bytes] = []
    server._send = lambda data, stop_event=None: sent.append(data)  # type: ignore[method-assign]
    for marker in _markers(count, name_template):
        server.register_marker(marker)
    server._send_packet()
    return sent


# --- byte readers ------------------------------------------------------------


def _psn_packets(datagrams: list[bytes]) -> list[object]:
    parsed = [pypsn.parse_psn_packet(datagram) for datagram in datagrams]
    # parse_psn_packet returns None on an unrecognised chunk id, which would
    # otherwise read as "no trackers" rather than as a serialisation regression.
    assert None not in parsed
    return parsed


def _otp_folio_and_pages(datagram: bytes) -> tuple[int, int, int]:
    """(folio, page, last_page) from the OTP Layer (Sections 6.7-6.9)."""
    assert datagram.startswith(OTP_PACKET_IDENTIFIER)
    folio = struct.unpack("!I", datagram[34:38])[0]
    page, last_page = struct.unpack("!HH", datagram[38:42])
    return folio, page, last_page


def _otp_transform_body(datagram: bytes) -> tuple[int, list[int]]:
    """(Transform Layer options, point numbers) from a transform datagram."""
    inner = datagram[16:][63:]  # past the OTP Layer's fixed fields + component name
    length = struct.unpack("!H", inner[2:4])[0]
    body = inner[4 : 4 + length]
    options = body[9]  # System Number(1) + Timestamp(8), then Options
    rest = body[14:]  # ... then Reserved(4), then the Point PDUs
    points = []
    while rest:
        point_length = struct.unpack("!H", rest[2:4])[0]
        point_body = rest[4 : 4 + point_length]
        points.append(struct.unpack("!I", point_body[3:7])[0])  # Priority(1) + Group(2)
        rest = rest[4 + point_length :]
    return options, points


def _is_otp_name_advertisement(datagram: bytes) -> bool:
    return struct.unpack("!H", datagram[79:81])[0] == VECTOR_OTP_ADVERTISEMENT_NAME


def _rttrpm_module_count(datagram: bytes) -> int:
    return datagram[17]  # numModules, the uint8 at the end of the 18-octet header


def _rttrpm_pkt_id(datagram: bytes) -> int:
    return struct.unpack("!I", datagram[6:10])[0]


# --- the invariant every output shares ---------------------------------------

_MARKER_COUNTS = [1, 13, 14, 15, 31, 32, 34, 36, 64, 100]


def _all_datagrams(count: int, name_template: str) -> dict[str, list[bytes]]:
    psn_data, psn_info = _psn_datagrams(count, name_template)
    otp_transform, otp_advertisement = _otp_datagrams(count, name_template)
    return {
        "psn data": psn_data,
        "psn info": psn_info,
        "otp transform": otp_transform,
        "otp advertisement": otp_advertisement,
        "rttrpm": _rttrpm_datagrams(count, name_template),
    }


@pytest.mark.parametrize("count", _MARKER_COUNTS)
@pytest.mark.parametrize("name_template", ["Marker {}", _LONG_NAME])
def test_no_output_protocol_emits_a_datagram_past_the_mtu(count: int, name_template: str) -> None:
    """The whole point of the exercise, in one assertion per stream.

    Before the split these crossed at 14 (PSN data), 32 (OTP transform), 34
    (RTTrPM), 36 (OTP name advertisement) and 85 (PSN info) markers.
    """
    for stream, datagrams in _all_datagrams(count, name_template).items():
        assert datagrams, f"{stream} sent nothing"
        oversize = [len(datagram) for datagram in datagrams if len(datagram) > MAX_DATAGRAM_BYTES]
        assert not oversize, f"{stream} emitted {oversize} octet datagram(s) at {count} markers"


@pytest.mark.parametrize("count", _MARKER_COUNTS)
def test_a_single_marker_set_needs_no_more_datagrams_than_the_payload_requires(count: int) -> None:
    """Guards the other direction: a split that emitted one datagram per marker
    would satisfy the size invariant and destroy the wire rate."""
    psn_data, _ = _psn_datagrams(count)
    assert len(psn_data) == -(-count // 13)  # 13 trackers is a full PSN data packet


# --- PSN: the packets of one frame -------------------------------------------


class TestPsnFrameSplitting:
    @pytest.mark.parametrize("count", _MARKER_COUNTS)
    def test_every_tracker_ships_exactly_once_across_the_frame(self, count: int) -> None:
        data, info = _psn_datagrams(count)
        for datagrams in (data, info):
            shipped = [t.tracker_id for packet in _psn_packets(datagrams) for t in packet.trackers]
            assert sorted(shipped) == list(range(1, count + 1))
            assert len(shipped) == len(set(shipped))

    @pytest.mark.parametrize("count", [14, 32, 64, 100])
    def test_a_split_frame_shares_one_id_and_declares_its_packet_count(self, count: int) -> None:
        """A receiver buffers a frame's trackers until it has ``packet_count``
        packets carrying that ``frame_id``; a per-packet id reassembles nothing."""
        data, _ = _psn_datagrams(count)
        assert len(data) > 1
        packets = _psn_packets(data)
        assert len({packet.info.frame_id for packet in packets}) == 1
        assert {packet.info.packet_count for packet in packets} == {len(data)}

    @pytest.mark.parametrize("count", [14, 64])
    def test_every_packet_of_a_frame_carries_one_timestamp(self, count: int) -> None:
        """The frame was sampled once; a per-packet clock read would let a
        receiver age two trackers of one frame differently."""
        data, _ = _psn_datagrams(count)
        assert len({packet.info.timestamp for packet in _psn_packets(data)}) == 1

    def test_frame_ids_advance_once_per_frame_not_once_per_packet(self) -> None:
        """Splitting must not reintroduce the gap that made a receiver read a
        dropped frame - the id belongs to the frame, not to the datagram."""
        server = PsnServer(mcast_ip=None)
        sent: list[bytes] = []
        server._send = lambda data, stop_event=None: sent.append(data)  # type: ignore[method-assign]
        for marker in _markers(64):
            server._markers[marker.marker_id] = marker

        frame_ids = []
        for _ in range(4):
            sent.clear()
            server._send_data_packet()
            ids = {packet.info.frame_id for packet in _psn_packets(sent)}
            assert len(ids) == 1
            frame_ids.append(ids.pop())
        assert frame_ids == [0, 1, 2, 3]

    def test_a_split_frame_reassembles_through_the_receiver(self) -> None:
        """Our own receiver accumulates a frame's trackers packet by packet, so
        a station receiving a peer's split frame needs no reassembly buffer."""
        data, _ = _psn_datagrams(64)
        assert len(data) > 1
        receiver = PsnReceiver()
        for datagram in data:
            receiver._on_packet(pypsn.parse_psn_packet(datagram))
        assert sorted(receiver._markers) == list(range(1, 65))

    def test_a_stale_marker_still_declares_itself_stale_inside_a_chunk(self) -> None:
        """Splitting reshuffles which packet a tracker rides in; it must not
        disturb the validity flag that says the position stopped being live."""
        server = PsnServer(mcast_ip=None)
        sent: list[bytes] = []
        server._send = lambda data, stop_event=None: sent.append(data)  # type: ignore[method-assign]
        fresh = _markers(64)
        for marker in fresh:
            server._markers[marker.marker_id] = marker
        stale = Marker(99, "Stale")  # registered, never written → unaged → stale
        server._markers[99] = stale

        server._send_data_packet()
        by_id = {t.tracker_id: t for packet in _psn_packets(sent) for t in packet.trackers}
        assert by_id[99].status == pytest.approx(0.0)
        assert by_id[1].status == pytest.approx(1.0)

    def test_a_frame_past_the_packet_count_ceiling_drops_its_tail(
        self, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        """``frame_packet_count`` is a uint8, so a frame needing more packets
        than that cannot describe itself. Thousands of markers away in practice;
        the cap is lowered to reach the path rather than registering them."""
        monkeypatch.setattr("openfollow.psn.server._MAX_FRAME_PACKETS", 2)
        server = PsnServer(mcast_ip=None)
        sent: list[bytes] = []
        server._send = lambda data, stop_event=None: sent.append(data)  # type: ignore[method-assign]
        for marker in _markers(64):
            server._markers[marker.marker_id] = marker

        with caplog.at_level("WARNING", logger="openfollow.psn.server"):
            server._send_data_packet()

        assert len(sent) == 2
        assert {packet.info.packet_count for packet in _psn_packets(sent)} == {2}
        assert any("capped at 2" in record.message for record in caplog.records)
        assert server._oversize_drops == 1

    def _capped_server(self, monkeypatch: pytest.MonkeyPatch) -> PsnServer:
        monkeypatch.setattr("openfollow.psn.server._MAX_FRAME_PACKETS", 2)
        server = PsnServer(mcast_ip=None)
        server._send = lambda data, stop_event=None: None  # type: ignore[method-assign]
        for marker in _markers(64):
            server._markers[marker.marker_id] = marker
        return server

    def test_the_cap_warning_throttles_after_five_episodes(self, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
        """Same first-5-then-every-100 shape as the send-error counter: the
        condition needs operator action, so at 60 fps it would flood the log."""
        server = self._capped_server(monkeypatch)
        with caplog.at_level("WARNING", logger="openfollow.psn.server"):
            for _ in range(20):
                server._send_data_packet()
        assert len([r for r in caplog.records if "capped at 2" in r.message]) == 5
        assert server._oversize_drops == 20

    def test_the_cap_warning_resumes_at_the_hundredth_episode(self, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
        server = self._capped_server(monkeypatch)
        server._oversize_drops = 99  # already past the opening burst
        with caplog.at_level("WARNING", logger="openfollow.psn.server"):
            server._send_data_packet()
        assert any("capped at 2" in r.message for r in caplog.records)
        assert server._oversize_drops == 100


# --- OTP: the pages of one folio ---------------------------------------------


class TestOtpFolioPaging:
    @pytest.mark.parametrize("count", _MARKER_COUNTS)
    def test_every_point_ships_exactly_once_across_the_folio(self, count: int) -> None:
        transform, _ = _otp_datagrams(count)
        points = [point for datagram in transform for point in _otp_transform_body(datagram)[1]]
        assert points == list(range(1, count + 1))

    @pytest.mark.parametrize("count", [32, 64, 120])
    def test_pages_share_a_folio_and_number_themselves_in_order(self, count: int) -> None:
        """Sections 6.7-6.9: a Consumer collects pages 0..last_page of one
        Folio Number, so a page that renumbered the folio joins nothing."""
        transform, _ = _otp_datagrams(count)
        assert len(transform) > 1
        headers = [_otp_folio_and_pages(datagram) for datagram in transform]
        folios = {folio for folio, _, _ in headers}
        assert len(folios) == 1
        assert [page for _, page, _ in headers] == list(range(len(transform)))
        assert {last for _, _, last in headers} == {len(transform) - 1}

    @pytest.mark.parametrize("count", [32, 120])
    def test_full_point_set_is_declared_identically_on_every_page(self, count: int) -> None:
        """Section 8.5's flag describes the folio, not the datagram: a page
        claiming a different point set than its siblings describes none."""
        transform, _ = _otp_datagrams(count)
        assert len({_otp_transform_body(datagram)[0] for datagram in transform}) == 1
        assert _otp_transform_body(transform[0])[0] & 0x80

    def test_a_single_page_folio_still_names_itself_page_zero_of_zero(self) -> None:
        transform, _ = _otp_datagrams(4)
        assert [_otp_folio_and_pages(datagram)[1:] for datagram in transform] == [(0, 0)]

    @pytest.mark.parametrize("count", [36, 64, 100])
    def test_the_name_advertisement_pages_too(self, count: int) -> None:
        """It carries a 32-octet name per point and had no length check at all,
        so it crossed the MTU silently at 36 markers."""
        _, advertisement = _otp_datagrams(count)
        name_pages = [datagram for datagram in advertisement if _is_otp_name_advertisement(datagram)]
        assert len(name_pages) > 1
        headers = [_otp_folio_and_pages(datagram) for datagram in name_pages]
        assert len({folio for folio, _, _ in headers}) == 1
        assert [page for _, page, _ in headers] == list(range(len(name_pages)))
        assert {last for _, _, last in headers} == {len(name_pages) - 1}

    def test_a_transform_folio_advances_once_per_folio_not_once_per_page(self) -> None:
        server = OtpServer(system_number=1)
        sent: list[bytes] = []
        server._send = lambda data, dest_ip, stop_event=None: sent.append(data)  # type: ignore[method-assign]
        for marker in _markers(120):
            server.register_marker(marker)

        folios = []
        for _ in range(3):
            sent.clear()
            server._send_transform_packet()
            page_folios = {_otp_folio_and_pages(datagram)[0] for datagram in sent}
            assert len(page_folios) == 1
            folios.append(page_folios.pop())
        assert folios == [0, 1, 2]


# --- RTTrPM: independent packets ---------------------------------------------


class TestRttrpmPacketSplitting:
    @pytest.mark.parametrize("count", _MARKER_COUNTS)
    def test_every_trackable_ships_exactly_once(self, count: int) -> None:
        datagrams = _rttrpm_datagrams(count)
        assert sum(_rttrpm_module_count(datagram) for datagram in datagrams) == count

    @pytest.mark.parametrize("count", [35, 64, 100])
    def test_each_packet_carries_its_own_sequence_number(self, count: int) -> None:
        """RTTrPM groups nothing across packets, so a split is several ordinary
        packets - sharing one sequence number would look like a retransmit."""
        datagrams = _rttrpm_datagrams(count)
        assert len(datagrams) > 1
        assert [_rttrpm_pkt_id(datagram) for datagram in datagrams] == list(range(len(datagrams)))

    @pytest.mark.parametrize("count", [35, 100])
    def test_the_declared_size_matches_each_packet(self, count: int) -> None:
        """``size`` covers the whole packet including the header; a split that
        left the original total in place would desynchronise every reader."""
        for datagram in _rttrpm_datagrams(count):
            assert struct.unpack("!H", datagram[11:13])[0] == len(datagram)

    def test_a_large_set_is_no_longer_truncated(self) -> None:
        """299 trackables used to hit the uint8 module cap and lose the tail
        silently; they now ride across as many packets as they need."""
        datagrams = _rttrpm_datagrams(299)
        assert sum(_rttrpm_module_count(datagram) for datagram in datagrams) == 299
