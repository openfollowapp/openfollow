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


class _SteppingClock:
    """Microsecond clock that advances by ``step`` on every read."""

    def __init__(self, now: int, step: int = 0) -> None:
        self.now = now
        self.step = step

    def __call__(self) -> int:
        value = self.now
        self.now += self.step
        return value


class _RenamingMarker(Marker):
    """A marker whose name grows on every read, as a live rename would.

    ``set_name`` is called at runtime by the catalog sync and the web UI, and
    an RTTrPM trackable module is 34 octets plus the name - so a name read once
    to size a chunk and again to encode it is a datagram that can outgrow its
    budget.
    """

    def __init__(self, marker_id: int, base: str) -> None:
        super().__init__(marker_id, base)
        self._base = base
        self._reads = 0

    @property  # type: ignore[override]
    def name(self) -> str:
        self._reads += 1
        return self._base + "x" * (20 * self._reads)

    @name.setter
    def name(self, value: str) -> None:
        self._base = value


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


def _otp_sampled_timestamps(datagram: bytes) -> list[int]:
    """Each Point Layer's Section 9.6 sampled timestamp from a transform page."""
    inner = datagram[16:][63:]
    length = struct.unpack("!H", inner[2:4])[0]
    rest = inner[4 : 4 + length][14:]
    stamps = []
    while rest:
        point_length = struct.unpack("!H", rest[2:4])[0]
        body = rest[4 : 4 + point_length]
        stamps.append(struct.unpack("!Q", body[7:15])[0])  # Priority(1)+Group(2)+Point(4)
        rest = rest[4 + point_length :]
    return stamps


def _is_otp_name_advertisement(datagram: bytes) -> bool:
    return struct.unpack("!H", datagram[79:81])[0] == VECTOR_OTP_ADVERTISEMENT_NAME


def _otp_name_points(datagram: bytes) -> list[int]:
    """Point numbers from a name advertisement page's Address Point Descriptions."""
    inner = datagram[16:][63:]
    length = struct.unpack("!H", inner[2:4])[0]
    advertisement = inner[4 : 4 + length][4:]  # past the Advertisement Layer's Reserved(4)
    list_length = struct.unpack("!H", advertisement[2:4])[0]
    entries = advertisement[4 : 4 + list_length][5:]  # past Options(1) + Reserved(4)
    points = []
    while len(entries) >= 39:  # System(1) + Group(2) + Point(4) + Name(32)
        points.append(struct.unpack("!I", entries[3:7])[0])
        entries = entries[39:]
    return points


def _rttrpm_module_count(datagram: bytes) -> int:
    return datagram[17]  # numModules, the uint8 at the end of the 18-octet header


def _rttrpm_pkt_id(datagram: bytes) -> int:
    return struct.unpack("!I", datagram[6:10])[0]


# --- the invariant every output shares ---------------------------------------

_MARKER_COUNTS = [1, 13, 14, 15, 31, 32, 34, 35, 36, 64, 100]


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

    One datagram holds 13 PSN trackers, 31 OTP points, 34 RTTrPM trackables and
    35 OTP names at these label lengths, so the counts below straddle every one
    of those boundaries.
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
        """It carries a fixed 32-octet name per point, so it needs a second page
        from 36 markers - a different boundary from the transform's."""
        _, advertisement = _otp_datagrams(count)
        name_pages = [datagram for datagram in advertisement if _is_otp_name_advertisement(datagram)]
        assert len(name_pages) > 1
        headers = [_otp_folio_and_pages(datagram) for datagram in name_pages]
        assert len({folio for folio, _, _ in headers}) == 1
        assert [page for _, page, _ in headers] == list(range(len(name_pages)))
        assert {last for _, _, last in headers} == {len(name_pages) - 1}

    def test_the_name_advertisement_stays_ascending_across_its_pages(self) -> None:
        """Section 13.5 wants one ascending list per folio, and a page can only
        sort what it is handed - so a marker set registered in the operator's
        order, not id order, is the case that catches a per-page sort."""
        server = OtpServer(system_number=1)
        sent: list[bytes] = []
        server._send = lambda data, dest_ip, stop_event=None: sent.append(data)  # type: ignore[method-assign]
        for marker in reversed(_markers(60)):  # registration order 60..1
            server.register_marker(marker)

        server._send_advertisement_packets()
        name_pages = [datagram for datagram in sent if _is_otp_name_advertisement(datagram)]
        assert len(name_pages) > 1
        points = [point for datagram in name_pages for point in _otp_name_points(datagram)]
        assert points == sorted(points)
        assert points == list(range(1, 61))

    def test_a_folio_whose_pages_cannot_be_encoded_is_dropped_not_fatal(self, caplog) -> None:
        """The transform loop calls this bare, so an escaping encode error ends
        OTP output for the life of the process rather than one frame of it."""
        server = OtpServer(system_number=1)
        sent: list[bytes] = []
        server._send = lambda data, dest_ip, stop_event=None: sent.append(data)  # type: ignore[method-assign]
        for marker in _markers(4):
            server.register_marker(marker)
        server._cid = b"too short"  # rejected by the OTP Layer on every page

        with caplog.at_level("ERROR", logger="openfollow.otp.server"):
            server._send_transform_packet()

        assert sent == []
        assert server._oversize_drops == 1
        assert any("folio dropped" in record.message for record in caplog.records)

    def test_repeated_folio_drops_are_throttled_but_not_silenced(self, caplog) -> None:
        """The condition needs operator action and recurs at the transform fps,
        so it is logged like the send-error counter: the opening burst, then a
        periodic reminder rather than silence for the rest of the process."""
        server = OtpServer(system_number=1)
        server._send = lambda data, dest_ip, stop_event=None: None  # type: ignore[method-assign]
        for marker in _markers(4):
            server.register_marker(marker)
        server._cid = b"too short"

        with caplog.at_level("ERROR", logger="openfollow.otp.server"):
            for _ in range(20):
                server._send_transform_packet()
            burst = len([r for r in caplog.records if "folio dropped" in r.message])
            server._oversize_drops = 99  # already past the opening burst
            server._send_transform_packet()

        assert burst == 5
        assert server._oversize_drops == 100
        assert len([r for r in caplog.records if "folio dropped" in r.message]) == 6

    def test_every_point_in_a_folio_ages_against_one_instant(self) -> None:
        """Section 9.6's sampled timestamp is read against the folio's own
        Transform Layer timestamp, which the pages share. Ageing each page
        against its own clock read reports identically-fresh points on a later
        page as older, so the clock is driven here rather than left to run at
        whatever speed the host happens to encode at.
        """
        clock = _SteppingClock(1_000_000)
        markers = [Marker(index, f"Marker {index}", clock=clock) for index in range(1, 65)]
        for marker in markers:
            marker.set_pos(1.0, 2.0, 3.0)  # every marker stamped at the same instant
        clock.step = 50_000  # 50 ms of apparent time per read from here on

        server = OtpServer(system_number=1)
        # A Transform Layer timestamp of a freshly-built server is a few
        # microseconds, and the sampled stamp is clamped at zero - so every age
        # would floor to the same 0 and hide the difference. Ten seconds of
        # apparent uptime puts the arithmetic in the range a running one uses.
        server._start_time_us -= 10_000_000
        sent: list[bytes] = []
        server._send = lambda data, dest_ip, stop_event=None: sent.append(data)  # type: ignore[method-assign]
        for marker in markers:
            server.register_marker(marker)
        server._send_transform_packet()

        assert len(sent) > 1
        sampled = {stamp for datagram in sent for stamp in _otp_sampled_timestamps(datagram)}
        assert sampled != {0}, "clamped flat - the test would pass either way"
        assert len(sampled) == 1

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

    def test_a_rename_between_sizing_and_encoding_cannot_burst_the_mtu(self) -> None:
        """The split budgets a chunk from one reading of each name and the
        datagram is built from another; a rename landing between the two is
        what turns a sized-to-fit chunk into the fragmenting datagram this
        exists to prevent."""
        server = RttrpmServer(host="127.0.0.1")
        server._socket = MagicMock()
        sent: list[bytes] = []
        server._send = lambda data, stop_event=None: sent.append(data)  # type: ignore[method-assign]
        for index in range(1, 41):
            marker = _RenamingMarker(index, f"Marker {index}")
            marker.set_pos(1.5, -2.5, 3.5)
            server.register_marker(marker)

        server._send_packet()

        assert sent
        assert max(len(datagram) for datagram in sent) <= MAX_DATAGRAM_BYTES

    def test_a_set_larger_than_the_module_count_field_still_ships_whole(self) -> None:
        """299 trackables exceed the uint8 ``numModules`` a single packet can
        count, so they can only ship at all by riding across several."""
        datagrams = _rttrpm_datagrams(299)
        assert sum(_rttrpm_module_count(datagram) for datagram in datagrams) == 299
