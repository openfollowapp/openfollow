# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for ANSI E1.59-2021 OTP encoder + OtpServer lifecycle.

Encoding tests validate the wire format byte-for-byte against the
normative tables in the standard (Sections 5–16, Appendix A) and against
a worked example derived from Appendix B Table B-31.

Lifecycle tests cover threading, socket retry/recover, stop-with-stuck-
thread warnings, and the live-apply ``restart()`` path.
"""

from __future__ import annotations

import struct
from unittest.mock import MagicMock, patch

import pytest

from openfollow.otp.server import (
    ADVERTISEMENT_INTERVAL_S,
    ADVERTISEMENT_MCAST_IP,
    COMPONENT_NAME_OCTETS,
    ESTA_MANUFACTURER_ID,
    MAX_OTP_MESSAGE_OCTETS,
    MODULE_NUMBER_POSITION,
    OTP_PACKET_IDENTIFIER,
    OTP_PORT,
    POINT_NAME_OCTETS,
    VECTOR_OTP_ADVERTISEMENT_MESSAGE,
    VECTOR_OTP_ADVERTISEMENT_MODULE,
    VECTOR_OTP_ADVERTISEMENT_MODULE_LIST,
    VECTOR_OTP_ADVERTISEMENT_NAME,
    VECTOR_OTP_ADVERTISEMENT_NAME_LIST,
    VECTOR_OTP_ADVERTISEMENT_SYSTEM,
    VECTOR_OTP_ADVERTISEMENT_SYSTEM_LIST,
    VECTOR_OTP_MODULE,
    VECTOR_OTP_POINT,
    VECTOR_OTP_TRANSFORM_MESSAGE,
    OtpServer,
    _build_otp_layer,
    _build_point_layer,
    _build_position_module,
    _build_transform_pdu,
    _encode_fixed_name,
    encode_otp_module_advertisement_packet,
    encode_otp_name_advertisement_packet,
    encode_otp_system_advertisement_packet,
    encode_otp_transform_packet,
    transform_mcast_ip,
)
from openfollow.psn.marker import Marker

pytestmark = pytest.mark.unit

_CID = b"\x11" * 16


def _marker(
    marker_id: int,
    x: float = 0.0,
    y: float = 0.0,
    z: float = 0.0,
    name: str = "T",
) -> Marker:
    t = Marker(marker_id, name)
    t.set_pos(x, y, z)
    return t


# ===========================================================================
# Constants – sanity-check the values match Appendix A of the standard.
# ===========================================================================


class TestSpecConstants:
    def test_packet_identifier_is_otp_e159_with_three_nulls(self) -> None:
        # Section 6.1 – exact 12-byte sequence; consumers discard packets
        # that don't match.
        assert OTP_PACKET_IDENTIFIER == b"OTP-E1.59\x00\x00\x00"
        assert len(OTP_PACKET_IDENTIFIER) == 12

    def test_otp_port_is_5568(self) -> None:
        # Table A-30
        assert OTP_PORT == 5568

    def test_vector_values_match_appendix_a(self) -> None:
        # Table A-28 – note that VECTOR_OTP_TRANSFORM_MESSAGE = 0x0001
        # and VECTOR_OTP_ADVERTISEMENT_MESSAGE = 0x0002. The previous
        # implementation had these swapped.
        assert VECTOR_OTP_TRANSFORM_MESSAGE == 0x0001
        assert VECTOR_OTP_ADVERTISEMENT_MESSAGE == 0x0002
        assert VECTOR_OTP_POINT == 0x0001
        assert VECTOR_OTP_MODULE == 0x0001
        assert VECTOR_OTP_ADVERTISEMENT_MODULE == 0x0001
        assert VECTOR_OTP_ADVERTISEMENT_NAME == 0x0002
        assert VECTOR_OTP_ADVERTISEMENT_SYSTEM == 0x0003
        assert VECTOR_OTP_ADVERTISEMENT_MODULE_LIST == 0x0001
        assert VECTOR_OTP_ADVERTISEMENT_NAME_LIST == 0x0001
        assert VECTOR_OTP_ADVERTISEMENT_SYSTEM_LIST == 0x0001

    def test_esta_manufacturer_id_is_zero(self) -> None:
        # Table A-30
        assert ESTA_MANUFACTURER_ID == 0x0000

    def test_position_module_number(self) -> None:
        # Table 16-21
        assert MODULE_NUMBER_POSITION == 0x0001

    def test_name_field_widths(self) -> None:
        # Section 6.12 / 13.5.1
        assert COMPONENT_NAME_OCTETS == 32
        assert POINT_NAME_OCTETS == 32

    def test_max_message_length(self) -> None:
        # Section 6.3.1
        assert MAX_OTP_MESSAGE_OCTETS == 1472

    def test_advertisement_interval_matches_spec(self) -> None:
        # Table A-29 – OTP_ADVERTISEMENT_TIMING = 10 seconds.
        assert ADVERTISEMENT_INTERVAL_S == 10.0


# ===========================================================================
# Multicast addressing (Section 15, Table 15-19)
# ===========================================================================


class TestMulticastAddressing:
    def test_transform_mcast_address_per_system(self) -> None:
        assert transform_mcast_ip(1) == "239.159.1.1"
        assert transform_mcast_ip(42) == "239.159.1.42"
        assert transform_mcast_ip(200) == "239.159.1.200"

    def test_advertisement_mcast_is_fixed(self) -> None:
        assert ADVERTISEMENT_MCAST_IP == "239.159.2.1"


# ===========================================================================
# UTF-8 fixed-length name encoding (Section 6.12 / 13.5.1)
# ===========================================================================


class TestEncodeFixedName:
    def test_short_name_is_null_padded(self) -> None:
        out = _encode_fixed_name("Spot", 32)
        assert len(out) == 32
        assert out[:4] == b"Spot"
        assert out[4:] == b"\x00" * 28

    def test_exact_length_no_padding_needed(self) -> None:
        name = "x" * 32
        out = _encode_fixed_name(name, 32)
        assert out == b"x" * 32

    def test_overlong_ascii_truncates(self) -> None:
        out = _encode_fixed_name("y" * 50, 32)
        assert out == b"y" * 32

    def test_truncation_on_utf8_rune_boundary(self) -> None:
        # 30 ASCII + one 4-byte emoji (U+1F3A4 microphone) = 34 bytes raw.
        # Naive truncate-then-pad would split the emoji; spec requires we
        # drop the partial rune entirely and null-pad back up to width.
        name = "A" * 30 + "\U0001f3a4"
        out = _encode_fixed_name(name, 32)
        assert len(out) == 32
        assert out == b"A" * 30 + b"\x00\x00"

    def test_truncation_at_3_byte_rune(self) -> None:
        # 31 ASCII + one 3-byte rune (U+2603 snowman) = 34 bytes. Cut at
        # 32 lands inside the rune.
        name = "B" * 31 + "☃"
        out = _encode_fixed_name(name, 32)
        assert out == b"B" * 31 + b"\x00"


# ===========================================================================
# Position Module (Section 16.1, Table 16-22)
# ===========================================================================


class TestPositionModule:
    def test_layout_options_first_then_xyz(self) -> None:
        # Table 16-22:
        #   Options(1) [bit 7 = scaling] + X(int32) + Y(int32) + Z(int32)
        # The previous implementation had Options at the END.
        out = _build_position_module(1_000_000, -2_000_000, 3_000_000)
        # Module Layer header: ManufID(2) + Length(2) + ModuleNumber(2) = 6
        # Module data: Options(1) + X(4) + Y(4) + Z(4) = 13
        # Length field counts ModuleNumber + data = 2 + 13 = 15.
        assert len(out) == 19
        manuf, length, module_num = struct.unpack("!HHH", out[:6])
        assert manuf == 0x0000
        assert length == 15
        assert module_num == MODULE_NUMBER_POSITION
        options = out[6]
        assert options == 0x00, "we always emit µm scaling"
        x, y, z = struct.unpack("!iii", out[7:19])
        assert (x, y, z) == (1_000_000, -2_000_000, 3_000_000)

    def test_negative_values_use_signed_encoding(self) -> None:
        out = _build_position_module(-1, -1, -1)
        x, y, z = struct.unpack("!iii", out[7:19])
        assert x == y == z == -1

    def test_max_int32_round_trips(self) -> None:
        out = _build_position_module(2_147_483_647, -2_147_483_648, 0)
        x, y, z = struct.unpack("!iii", out[7:19])
        assert x == 2_147_483_647
        assert y == -2_147_483_648
        assert z == 0


# ===========================================================================
# Position field clamping (non-finite / out-of-range guard)
# ===========================================================================


class TestMetresToUmClamp:
    def test_finite_value_converts(self) -> None:
        from openfollow.otp.server import _M_TO_UM, _metres_to_um_i32

        assert _metres_to_um_i32(1.5) == int(1.5 * _M_TO_UM)

    def test_non_finite_becomes_zero(self) -> None:
        from openfollow.otp.server import _metres_to_um_i32

        assert _metres_to_um_i32(float("nan")) == 0
        assert _metres_to_um_i32(float("inf")) == 0
        assert _metres_to_um_i32(float("-inf")) == 0

    def test_out_of_range_clamps_to_int32(self) -> None:
        from openfollow.otp.server import _INT32_MAX, _INT32_MIN, _metres_to_um_i32

        assert _metres_to_um_i32(1e30) == _INT32_MAX
        assert _metres_to_um_i32(-1e30) == _INT32_MIN

    def test_encode_transform_packet_with_non_finite_marker_does_not_raise(self) -> None:
        # A NaN/inf/huge marker position (bad calibration, tracking glitch) must
        # not crash the encoder – that would kill the transform thread.
        markers = [
            _marker(1, float("nan"), 0.0, 0.0),
            _marker(2, float("inf"), 2.0, -1e30),
        ]
        pkt = encode_otp_transform_packet(
            cid=b"\x00" * 16,
            component_name="Test",
            folio=1,
            system_number=1,
            timestamp_us=0,
            markers=markers,
            priority=100,
        )
        assert isinstance(pkt, bytes)
        assert len(pkt) > 0


# ===========================================================================
# Point Layer (Section 9, Table 9-12)
# ===========================================================================


class TestPointLayer:
    def test_header_and_field_layout(self) -> None:
        module = _build_position_module(1, 2, 3)
        out = _build_point_layer(
            priority=100,
            group=1000,
            point=42,
            sampled_timestamp_us=0xDEADBEEFCAFE,
            module_pdus=module,
        )
        vec, length = struct.unpack("!HH", out[:4])
        # Section 9.1 – Point Layer's Vector identifies the contained
        # Module PDUs. (VECTOR_OTP_MODULE and VECTOR_OTP_POINT happen to
        # share the byte value 0x0001 but are semantically different.)
        assert vec == VECTOR_OTP_MODULE
        # Body = Priority(1)+Group(2)+Point(4)+Timestamp(8)+Options(1)
        #        + Reserved(4) + Module(19) = 39
        assert length == 39
        priority = out[4]
        assert priority == 100
        group = struct.unpack("!H", out[5:7])[0]
        assert group == 1000
        point = struct.unpack("!I", out[7:11])[0]
        assert point == 42
        sampled = struct.unpack("!Q", out[11:19])[0]
        assert sampled == 0xDEADBEEFCAFE
        # Options + Reserved = 5 zero bytes
        assert out[19:24] == b"\x00" * 5
        assert out[24:] == module


# ===========================================================================
# Transform Layer (Section 8, Table 8-11)
# ===========================================================================


class TestTransformPdu:
    def test_header_and_full_point_set_bit(self) -> None:
        module = _build_position_module(1, 2, 3)
        point = _build_point_layer(
            priority=100,
            group=1,
            point=1,
            sampled_timestamp_us=0,
            module_pdus=module,
        )
        out = _build_transform_pdu(
            system_number=42,
            timestamp_us=0x1122334455667788,
            full_point_set=True,
            point_pdus=point,
        )
        vec, length = struct.unpack("!HH", out[:4])
        assert vec == VECTOR_OTP_POINT
        # Body = System(1) + Timestamp(8) + Options(1) + Reserved(4) + point
        assert length == 14 + len(point)
        system = out[4]
        assert system == 42
        ts = struct.unpack("!Q", out[5:13])[0]
        assert ts == 0x1122334455667788
        options = out[13]
        assert options == 0x80, "Full Point Set bit must be set"
        # Section 8.6 – Reserved is 4 octets, not 2
        assert out[14:18] == b"\x00\x00\x00\x00"

    def test_full_point_set_clear_when_false(self) -> None:
        out = _build_transform_pdu(
            system_number=1,
            timestamp_us=0,
            full_point_set=False,
            point_pdus=b"",
        )
        assert out[13] == 0x00


# ===========================================================================
# OTP Layer (Section 6, Table 6-3)
# ===========================================================================


class TestOtpLayer:
    def _wrap(self, **overrides) -> bytes:
        kwargs = {
            "vector": VECTOR_OTP_TRANSFORM_MESSAGE,
            "cid": _CID,
            "folio": 0,
            "page": 0,
            "last_page": 0,
            "component_name": "Test",
            "inner_pdu": b"",
        }
        kwargs.update(overrides)
        return _build_otp_layer(**kwargs)

    def test_packet_starts_with_otp_e159_identifier(self) -> None:
        out = self._wrap()
        assert out[:12] == OTP_PACKET_IDENTIFIER

    def test_vector_at_octets_12_13(self) -> None:
        out = self._wrap(vector=VECTOR_OTP_ADVERTISEMENT_MESSAGE)
        vec = struct.unpack("!H", out[12:14])[0]
        assert vec == VECTOR_OTP_ADVERTISEMENT_MESSAGE

    def test_length_counts_from_octet_16(self) -> None:
        # Section 6.3 – Length is octets-from-16 to end (excluding footer).
        out = self._wrap(inner_pdu=b"x" * 50)
        length = struct.unpack("!H", out[14:16])[0]
        assert length == len(out) - 16

    def test_footer_options_and_length_zero(self) -> None:
        out = self._wrap()
        assert out[16] == 0x00  # Footer Options
        assert out[17] == 0x00  # Footer Length

    def test_cid_at_offset_18(self) -> None:
        out = self._wrap(cid=b"\x42" * 16)
        assert out[18:34] == b"\x42" * 16

    def test_folio_page_lastpage_layout(self) -> None:
        out = self._wrap(folio=0xDEADBEEF, page=3, last_page=7)
        folio = struct.unpack("!I", out[34:38])[0]
        page = struct.unpack("!H", out[38:40])[0]
        last_page = struct.unpack("!H", out[40:42])[0]
        assert folio == 0xDEADBEEF
        assert page == 3
        assert last_page == 7

    def test_options_and_reserved_at_42_46(self) -> None:
        out = self._wrap()
        assert out[42] == 0x00
        assert out[43:47] == b"\x00\x00\x00\x00"

    def test_component_name_at_47_78_padded_to_32(self) -> None:
        out = self._wrap(component_name="OpenFollow")
        name_field = out[47:79]
        assert len(name_field) == 32
        assert name_field[:10] == b"OpenFollow"
        assert name_field[10:] == b"\x00" * 22

    def test_invalid_cid_length_raises(self) -> None:
        with pytest.raises(ValueError, match="16 octets"):
            self._wrap(cid=b"\x00" * 8)


# ===========================================================================
# Appendix B-style example: byte-exact reconstruction.
#
# Modeled on Table B-31 but adapted to what our encoder actually emits
# (Position Module only, µm scaling – Marker.pos is in meters and we
# convert to µm). Same outer OTP Layer fixture (CID, Folio, Component
# Name) as the published example to keep the byte layout recognisable
# next to the standard.
# ===========================================================================


class TestAppendixBLikeFixture:
    """Constructs the same packet two different ways and asserts equality.

    Side A: directly compose with the layer primitives, with each field
    written as the standard table specifies.
    Side B: call the public ``encode_otp_transform_packet`` with matching
    inputs.

    This catches any drift between the high-level encoder and the
    low-level builders, and serves as the regression target a future
    refactor should preserve.
    """

    APPENDIX_B_CID = bytes.fromhex(
        "4d6f76657320204039b020206f626a656374"[:32]
    )  # Padded ASCII per Table B-31 example; 16 octets

    def test_byte_exact_against_layered_construction(self) -> None:
        # Inputs mirroring the Table B-31 example with two markers.
        # Position values converted to µm (our default scaling).
        cid = self.APPENDIX_B_CID
        folio = 326
        system_number = 1
        timestamp_us = 3_600_000_000
        component_name = "Automation-Server-Primary"
        priority = 100

        t0 = _marker(1, 10.000, -1.500, -2.000, name="Spot 1")
        t1 = _marker(200, 0.100, 1.000_500, -0.010, name="Revolve A")

        # --- Side A: hand-rolled per the spec tables. ---
        # Module for marker 0 (10000mm, -1500mm, -2000mm in µm)
        mod0 = _build_position_module(
            int(10.000 * 1_000_000),
            int(-1.500 * 1_000_000),
            int(-2.000 * 1_000_000),
        )
        # Module for marker 1 (100000µm, 1000500µm, -10000µm)
        mod1 = _build_position_module(
            int(0.100 * 1_000_000),
            int(1.000_500 * 1_000_000),
            int(-0.010 * 1_000_000),
        )
        # Note: encode_otp_transform_packet uses the same generation
        # timestamp as the sampled timestamp when the latter is omitted
        # (Section 9.6 – sampled is when the Producer read the Point;
        # for a single-pass encoder there's no distinction).
        pt0 = _build_point_layer(
            priority=priority,
            group=1,
            point=1,
            sampled_timestamp_us=timestamp_us,
            module_pdus=mod0,
        )
        pt1 = _build_point_layer(
            priority=priority,
            group=1,
            point=200,
            sampled_timestamp_us=timestamp_us,
            module_pdus=mod1,
        )
        transform = _build_transform_pdu(
            system_number=system_number,
            timestamp_us=timestamp_us,
            full_point_set=True,
            point_pdus=pt0 + pt1,
        )
        expected = _build_otp_layer(
            vector=VECTOR_OTP_TRANSFORM_MESSAGE,
            cid=cid,
            folio=folio,
            page=0,
            last_page=0,
            component_name=component_name,
            inner_pdu=transform,
        )

        # --- Side B: public encoder with the same inputs. ---
        actual = encode_otp_transform_packet(
            cid=cid,
            component_name=component_name,
            folio=folio,
            system_number=system_number,
            timestamp_us=timestamp_us,
            markers=[t0, t1],
            priority=priority,
        )

        assert actual == expected, (
            f"Encoder drift detected.\n  expected (hex): {expected.hex()}\n  actual   (hex): {actual.hex()}"
        )

    def test_known_offsets_match_spec(self) -> None:
        """Spot-check named offsets from Table B-31 against the encoder output."""
        cid = self.APPENDIX_B_CID
        t0 = _marker(1, 10.000, -1.500, -2.000)
        pkt = encode_otp_transform_packet(
            cid=cid,
            component_name="Automation-Server-Primary",
            folio=326,
            system_number=1,
            timestamp_us=3_600_000_000,
            markers=[t0],
            priority=100,
        )
        # Offset 0 – packet identifier
        assert pkt[:12] == b"OTP-E1.59\x00\x00\x00"
        # Offset 12-13 – VECTOR_OTP_TRANSFORM_MESSAGE (0x0001)
        assert pkt[12:14] == b"\x00\x01"
        # Offset 18-33 – CID
        assert pkt[18:34] == cid
        # Offset 34-37 – Folio = 326 = 0x00000146
        assert pkt[34:38] == b"\x00\x00\x01\x46"
        # Offset 38-41 – Page=0, Last Page=0
        assert pkt[38:42] == b"\x00\x00\x00\x00"
        # Offset 47-78 – Component Name
        name = pkt[47:79]
        assert len(name) == 32
        assert name[:25] == b"Automation-Server-Primary"
        assert name[25:] == b"\x00" * 7
        # Offset 79-80 – Transform Layer Vector = VECTOR_OTP_POINT
        assert pkt[79:81] == b"\x00\x01"
        # Offset 83 – System Number
        assert pkt[83] == 1
        # Offset 84-91 – Timestamp = 3_600_000_000
        assert struct.unpack("!Q", pkt[84:92])[0] == 3_600_000_000
        # Offset 92 – Options bit 7 (Full Point Set)
        assert pkt[92] == 0x80
        # Offset 93-96 – Reserved (4 octets, not 2)
        assert pkt[93:97] == b"\x00\x00\x00\x00"


# ===========================================================================
# Public encoder happy paths
# ===========================================================================


class TestEncodeOtpTransformPacket:
    def test_minimum_packet_size_with_one_marker(self) -> None:
        # Section 6.3 / Table 6-4: minimum for a Transform Message is
        # "134 octets ... for a standard OTP Transform Message containing
        # a single Point and a single standard Reference Frame Module".
        # We don't include a Reference Frame, so our minimum is smaller –
        # but we must still respect the maximum.
        pkt = encode_otp_transform_packet(
            cid=_CID,
            component_name="X",
            folio=0,
            system_number=1,
            timestamp_us=0,
            markers=[_marker(1)],
            priority=100,
        )
        assert pkt[:12] == OTP_PACKET_IDENTIFIER
        assert len(pkt) <= MAX_OTP_MESSAGE_OCTETS

    def test_no_markers_still_produces_valid_layer(self) -> None:
        # An empty marker list still produces a syntactically valid OTP
        # Transform Message – the Transform Layer carries zero Point PDUs.
        # This is what _send_transform_packet's "no markers" guard avoids
        # actually transmitting, but the encoder shouldn't crash on it.
        pkt = encode_otp_transform_packet(
            cid=_CID,
            component_name="X",
            folio=0,
            system_number=1,
            timestamp_us=0,
            markers=[],
            priority=100,
        )
        assert pkt[:12] == OTP_PACKET_IDENTIFIER

    def test_position_in_meters_converted_to_micrometers(self) -> None:
        pkt = encode_otp_transform_packet(
            cid=_CID,
            component_name="X",
            folio=0,
            system_number=1,
            timestamp_us=0,
            markers=[_marker(1, 1.5, -2.5, 3.5)],
            priority=100,
        )
        # Find the Position Module by walking layers. The Module data
        # starts after: OTP Layer header(79) + Transform header(4) +
        # Transform body header(14) + Point header(4) + Point body header(20)
        # + Module Layer header(6) = 127.
        # Options(1) + X(4) + Y(4) + Z(4) at offset 127.
        assert pkt[127] == 0x00  # µm scaling
        x, y, z = struct.unpack("!iii", pkt[128:140])
        assert x == 1_500_000
        assert y == -2_500_000
        assert z == 3_500_000

    def test_oversize_payload_raises(self) -> None:
        # 70 markers * 43 bytes/Point ≈ 3010 bytes – well over 1472.
        many = [_marker(i, name=f"T{i}") for i in range(1, 71)]
        with pytest.raises(ValueError, match="exceeds spec maximum"):
            encode_otp_transform_packet(
                cid=_CID,
                component_name="X",
                folio=0,
                system_number=1,
                timestamp_us=0,
                markers=many,
                priority=100,
            )


class TestEncodeOtpAdvertisements:
    def test_module_advertisement_lists_position(self) -> None:
        pkt = encode_otp_module_advertisement_packet(
            cid=_CID,
            component_name="X",
            folio=0,
        )
        assert pkt[:12] == OTP_PACKET_IDENTIFIER
        # OTP Layer Vector = ADVERTISEMENT_MESSAGE
        assert struct.unpack("!H", pkt[12:14])[0] == VECTOR_OTP_ADVERTISEMENT_MESSAGE
        # The advertisement payload should end in the 4 octets identifying
        # the Position Module: ManufID 0x0000 + ModuleNumber 0x0001.
        assert pkt[-4:] == b"\x00\x00\x00\x01"

    def test_name_advertisement_includes_address_point_descriptions(self) -> None:
        markers = [_marker(1, name="Spot 1"), _marker(3, name="Spot 3")]
        pkt = encode_otp_name_advertisement_packet(
            cid=_CID,
            component_name="X",
            folio=0,
            system_number=1,
            markers=markers,
        )
        # Each APD is System(1) + Group(2) + Point(4) + Name(32) = 39 octets.
        # Two APDs = 78 octets at the end (not including the layer header).
        # The Name Advertisement Layer's Options byte should have bit 7
        # set (response, not request).
        assert pkt[:12] == OTP_PACKET_IDENTIFIER
        # Verify both names appear in the right Address Point Descriptions
        # (they're packed as fixed-width; search the tail of the packet).
        # Marker IDs become 1-based Point Numbers in OTP.
        last_apd = pkt[-39:]
        # System=1, Group=1, Point=3
        assert last_apd[0] == 1
        assert struct.unpack("!H", last_apd[1:3])[0] == 1
        assert struct.unpack("!I", last_apd[3:7])[0] == 3
        assert last_apd[7:13] == b"Spot 3"

    def test_system_advertisement_lists_system_number(self) -> None:
        pkt = encode_otp_system_advertisement_packet(
            cid=_CID,
            component_name="X",
            folio=0,
            system_number=42,
        )
        assert pkt[:12] == OTP_PACKET_IDENTIFIER
        # Last byte of the packet should be the single System Number 42.
        assert pkt[-1] == 42


# ===========================================================================
# OtpServer – marker management (preserved from previous suite).
# ===========================================================================


class TestOtpServerMarkerManagement:
    def test_register_and_retrieve(self) -> None:
        server = OtpServer()
        t = _marker(1)
        server.register_marker(t)
        assert server.get_marker(1) is t

    def test_unregister_removes_marker(self) -> None:
        server = OtpServer()
        server.register_marker(_marker(1))
        server.unregister_marker(1)
        assert server.get_marker(1) is None

    def test_unregister_nonexistent_is_noop(self) -> None:
        OtpServer().unregister_marker(99)

    def test_register_replaces_existing_id(self) -> None:
        server = OtpServer()
        old, new = _marker(1, name="Old"), _marker(1, name="New")
        server.register_marker(old)
        server.register_marker(new)
        assert server.get_marker(1) is new


# ===========================================================================
# OtpServer – send logic (no live threads).
# ===========================================================================


class TestOtpServerSend:
    def _server_with_socket(self, system_number: int = 1) -> tuple[OtpServer, MagicMock]:
        mock_sock = MagicMock()
        # No mcast_ip override → spec-derived destinations from system_number.
        server = OtpServer(system_number=system_number)
        server._socket = mock_sock
        return server, mock_sock

    def test_transform_skipped_when_no_markers(self) -> None:
        server, mock_sock = self._server_with_socket()
        server._send_transform_packet()
        mock_sock.sendto.assert_not_called()

    def test_transform_sends_to_spec_derived_address(self) -> None:
        server, mock_sock = self._server_with_socket(system_number=42)
        server.register_marker(_marker(1, 1.0, 2.0, 3.0))
        server._send_transform_packet()
        mock_sock.sendto.assert_called_once()
        data, addr = mock_sock.sendto.call_args[0]
        # Table 15-19: 239.159.1.<system>
        assert addr == ("239.159.1.42", 5568)
        assert data[:12] == OTP_PACKET_IDENTIFIER

    def test_advertisement_goes_to_239_159_2_1(self) -> None:
        server, mock_sock = self._server_with_socket(system_number=42)
        server.register_marker(_marker(1))
        server._send_advertisement_packets()
        # Module + Name + System = 3 advertisement packets, all to 239.159.2.1
        for call in mock_sock.sendto.call_args_list:
            _, addr = call[0]
            assert addr == ("239.159.2.1", 5568)

    def test_advertisement_sends_only_system_when_no_markers(self) -> None:
        server, mock_sock = self._server_with_socket()
        server._send_advertisement_packets()
        # No markers → only the System Advertisement goes out (Module
        # and Name are skipped because there's nothing to advertise).
        assert mock_sock.sendto.call_count == 1

    def test_advertisement_sends_three_packets_with_marker(self) -> None:
        server, mock_sock = self._server_with_socket()
        server.register_marker(_marker(1))
        server._send_advertisement_packets()
        assert mock_sock.sendto.call_count == 3

    def test_send_dropped_when_socket_is_none(self) -> None:
        server = OtpServer()
        server._socket = None
        server.register_marker(_marker(1))
        server._send_transform_packet()  # must not raise


# ===========================================================================
# Folio counters (Section 6.7) – independent per stream, wrap at 2^32.
# ===========================================================================


class TestFolioCounters:
    def test_independent_counters_per_stream(self) -> None:
        server = OtpServer()
        # Tick each one and confirm they're not shared.
        for _ in range(3):
            server._next_folio("transform")
        for _ in range(5):
            server._next_folio("module_adv")
        assert server._transform_folio == 3
        assert server._module_adv_folio == 5
        assert server._name_adv_folio == 0
        assert server._system_adv_folio == 0

    def test_rollover_at_max_uint32(self) -> None:
        server = OtpServer()
        server._transform_folio = 0xFFFFFFFF
        prev = server._next_folio("transform")
        # _next_folio returns the value BEFORE incrementing, then wraps.
        assert prev == 0xFFFFFFFF
        assert server._transform_folio == 0


# ===========================================================================
# OtpServer – lifecycle (preserved, signatures updated).
# ===========================================================================


class TestOtpServerLifecycle:
    def _patched(self) -> MagicMock:
        mock_instance = MagicMock()
        return MagicMock(return_value=mock_instance)

    def test_stop_before_start_is_safe(self) -> None:
        OtpServer().stop()

    def test_start_spawns_threads(self) -> None:
        with patch("openfollow.otp.server.multicast_expert.McastTxSocket", self._patched()):
            server = OtpServer()
            server.start()
            assert server._transform_thread is not None
            assert server._adv_thread is not None
            server.stop()

    def test_stop_joins_threads(self) -> None:
        with patch("openfollow.otp.server.multicast_expert.McastTxSocket", self._patched()):
            server = OtpServer()
            server.start()
            server.stop()
            assert server._transform_thread is None
            assert server._adv_thread is None

    def test_context_manager_starts_and_stops(self) -> None:
        with patch("openfollow.otp.server.multicast_expert.McastTxSocket", self._patched()):
            with OtpServer() as server:
                assert server._transform_thread is not None
            assert server._transform_thread is None


# ===========================================================================
# OtpServer – restart (live-apply,).
# ===========================================================================


class TestOtpServerRestart:
    def test_restart_updates_destinations_for_new_system_number(self) -> None:
        with patch("openfollow.otp.server.multicast_expert.McastTxSocket") as mcls:
            mcls.return_value = MagicMock()
            server = OtpServer(system_number=1)
            server.start()
            assert server._transform_dest == "239.159.1.1"
            server.restart(
                system_name="X",
                system_number=42,
                port=OTP_PORT,
                source_ip="",
                priority=100,
            )
            assert server._transform_dest == "239.159.1.42"
            assert server._advertisement_dest == "239.159.2.1"
            server.stop()

    def test_restart_signature_does_not_take_mcast_ip(self) -> None:
        # Defensive – services.py.apply_otp_output_change must NOT pass
        # mcast_ip after the rewrite. If somebody re-adds the kwarg,
        # this test breaks loudly.
        import inspect

        sig = inspect.signature(OtpServer.restart)
        assert "mcast_ip" not in sig.parameters


# ===========================================================================
# OtpServer – unicast/loopback debug branch (mcast_ip="").
# ===========================================================================


class TestOtpUnicastStart:
    def test_empty_mcast_ip_uses_plain_udp_socket(self) -> None:
        with patch("openfollow.otp.server.socket.socket") as mock_socket_cls:
            srv = OtpServer(mcast_ip="")
            srv.start()
            try:
                assert mock_socket_cls.call_count >= 1
                args, _ = mock_socket_cls.call_args
                import socket as _sk

                assert args == (_sk.AF_INET, _sk.SOCK_DGRAM)
            finally:
                srv.stop()

    def test_unicast_mode_sends_to_loopback(self) -> None:
        srv = OtpServer(mcast_ip="")
        mock_sock = MagicMock()
        srv._socket = mock_sock
        srv.register_marker(_marker(1, 1.0, 2.0, 3.0))
        srv._send_transform_packet()
        _, addr = mock_sock.sendto.call_args[0]
        assert addr == ("127.0.0.1", 5568)


# ===========================================================================
# OtpServer – stop with stuck threads (preserved).
# ===========================================================================


class TestOtpStopThreadTimeouts:
    def _stub_alive_thread(self):
        class _StubThread:
            def join(self, timeout=None):
                pass

            def is_alive(self):
                return True

        return _StubThread()

    def test_stop_warns_when_socket_retry_thread_does_not_join(self, caplog) -> None:
        import contextlib as _cl
        import logging as _logging

        srv = OtpServer()
        srv._socket_thread = self._stub_alive_thread()  # type: ignore[assignment]
        srv._exit_stack = _cl.ExitStack()

        with caplog.at_level(_logging.WARNING, logger="openfollow.otp.server"):
            srv.stop()
        assert any("socket-retry thread did not stop" in r.message for r in caplog.records)
        assert srv._socket_thread is None

    def test_stop_warns_when_transform_thread_does_not_join(self, caplog) -> None:
        import contextlib as _cl
        import logging as _logging

        srv = OtpServer()
        srv._transform_thread = self._stub_alive_thread()  # type: ignore[assignment]
        srv._exit_stack = _cl.ExitStack()

        with caplog.at_level(_logging.WARNING, logger="openfollow.otp.server"):
            srv.stop()
        assert any("transform thread did not stop" in r.message for r in caplog.records)
        assert srv._transform_thread is None

    def test_stop_warns_when_adv_thread_does_not_join(self, caplog) -> None:
        import contextlib as _cl
        import logging as _logging

        srv = OtpServer()
        srv._adv_thread = self._stub_alive_thread()  # type: ignore[assignment]
        srv._exit_stack = _cl.ExitStack()

        with caplog.at_level(_logging.WARNING, logger="openfollow.otp.server"):
            srv.stop()
        assert any("advertisement thread did not stop" in r.message for r in caplog.records)
        assert srv._adv_thread is None


# ===========================================================================
# OtpServer – multicast socket open with source_ip (preserved).
# ===========================================================================


class TestOtpTryOpenSocketWithSourceIp:
    def test_source_ip_passed_to_mcast_tx_socket(self) -> None:
        with patch("openfollow.otp.server.multicast_expert.McastTxSocket") as mcls:
            mcls.return_value = MagicMock()
            srv = OtpServer(system_number=1, source_ip="192.0.2.5")
            assert srv._try_open_multicast_socket_once(attempt=1) is True
            kwargs = mcls.call_args.kwargs
            assert kwargs.get("iface_ip") == "192.0.2.5"
            # Both transform and advertisement groups should be subscribed.
            assert "239.159.1.1" in kwargs["mcast_ips"]
            assert "239.159.2.1" in kwargs["mcast_ips"]

    def test_no_source_ip_omits_iface_ip(self) -> None:
        with patch("openfollow.otp.server.multicast_expert.McastTxSocket") as mcls:
            mcls.return_value = MagicMock()
            srv = OtpServer(system_number=1)
            assert srv._try_open_multicast_socket_once(attempt=1) is True
            assert "iface_ip" not in mcls.call_args.kwargs


# ===========================================================================
# OtpServer – handle_send_error transient errno path (preserved).
# ===========================================================================


class TestOtpOverrideMcastIp:
    """Test-only ``mcast_ip`` kwarg paths.

    Production callers leave ``mcast_ip=None`` and let the server derive
    addresses per Table 15-19. Tests that need to capture every packet
    on a single socket pass an explicit override; that path must keep
    working so the byte-fixture suite (``TestAppendixBLikeFixture``)
    isn't crippled by the refactor.
    """

    def test_explicit_override_routes_both_streams_to_same_address(self) -> None:
        srv = OtpServer(mcast_ip="192.0.2.99")
        assert srv._transform_dest == "192.0.2.99"
        assert srv._advertisement_dest == "192.0.2.99"

    def test_multicast_groups_in_override_mode(self) -> None:
        srv = OtpServer(mcast_ip="192.0.2.99")
        assert srv._multicast_groups() == ["192.0.2.99"]

    def test_multicast_groups_in_spec_mode(self) -> None:
        srv = OtpServer(system_number=7)
        groups = srv._multicast_groups()
        assert "239.159.1.7" in groups
        assert "239.159.2.1" in groups


class TestOtpUpdateSystemName:
    def test_updates_system_name_under_lock(self) -> None:
        srv = OtpServer(system_name="Old")
        srv.update_system_name("New")
        assert srv._system_name == "New"


class TestOtpStartFailsAndSpawnsRetryThread:
    """When the initial multicast socket open fails (``McastTxSocket``
    raises), ``start()`` spawns a daemon retry thread. Production: the
    operator's network interface comes up later and the retry succeeds.
    """

    def test_initial_failure_spawns_retry_thread(self) -> None:
        with patch("openfollow.otp.server.multicast_expert.McastTxSocket") as mcls:
            mcls.side_effect = OSError("no interface")
            srv = OtpServer(system_number=1)
            srv.start()
            try:
                # Initial open failed → no socket, retry thread alive.
                assert srv._socket is None
                assert srv._socket_thread is not None
            finally:
                srv._stop_event.set()
                srv.stop()


class TestOtpTryOpenSocketFailureLogs:
    def test_failure_returns_false_and_logs_warning(self, caplog) -> None:
        import logging as _logging

        with patch("openfollow.otp.server.multicast_expert.McastTxSocket") as mcls:
            mcls.side_effect = RuntimeError("boom")
            srv = OtpServer(system_number=1)
            with caplog.at_level(_logging.WARNING, logger="openfollow.otp.server"):
                ok = srv._try_open_multicast_socket_once(attempt=2)
            assert ok is False
            assert any("multicast socket failed" in r.message for r in caplog.records)


class TestOtpRetryMulticastBackground:
    def test_retry_succeeds_logs_info_and_returns(self, caplog) -> None:
        import logging as _logging

        srv = OtpServer(system_number=1)
        # First call fails, second succeeds – exactly within the bounded
        # retry budget (_MAX_SOCKET_RETRIES = 3).
        attempts: list[int] = []

        def fake_open(*, attempt: int) -> bool:
            attempts.append(attempt)
            return attempt == 2

        srv._try_open_multicast_socket_once = fake_open  # type: ignore[assignment]
        # Tight loop – pretend stop_event.wait returned immediately.
        srv._stop_event.wait = lambda timeout=None: False  # type: ignore[assignment]
        with caplog.at_level(_logging.INFO, logger="openfollow.otp.server"):
            srv._retry_multicast_socket_background()
        assert attempts == [2]
        assert any("connected on retry" in r.message for r in caplog.records)

    def test_retry_exhausts_logs_error(self, caplog) -> None:
        import logging as _logging

        srv = OtpServer(system_number=1)
        srv._try_open_multicast_socket_once = lambda **_kw: False  # type: ignore[assignment]
        srv._stop_event.wait = lambda timeout=None: False  # type: ignore[assignment]
        with caplog.at_level(_logging.ERROR, logger="openfollow.otp.server"):
            srv._retry_multicast_socket_background()
        assert any("failed after" in r.message for r in caplog.records)

    def test_retry_aborts_when_stop_event_set(self) -> None:
        srv = OtpServer(system_number=1)
        srv._stop_event.set()
        # If the body executes, it would call McastTxSocket; the early
        # exit means we never reach that, so no patch needed.
        srv._retry_multicast_socket_background()  # must return cleanly


class TestOtpRecoverMulticastBackground:
    def test_recovery_loops_until_success(self) -> None:
        srv = OtpServer(system_number=1)
        attempts: list[int] = []

        def fake_open(*, attempt: int) -> bool:
            attempts.append(attempt)
            return attempt == 3

        srv._try_open_multicast_socket_once = fake_open  # type: ignore[assignment]
        srv._stop_event.wait = lambda timeout=None: False  # type: ignore[assignment]
        srv._recover_multicast_socket_background()
        assert attempts == [1, 2, 3]

    def test_recovery_aborts_when_stop_set_between_iterations(self) -> None:
        srv = OtpServer(system_number=1)
        flips = iter([False, True])

        def fake_wait(timeout=None) -> bool:
            # Second call to wait() flips stop_event so the loop exits
            # mid-iteration via the check after wait().
            return next(flips, True)

        srv._stop_event.wait = fake_wait  # type: ignore[assignment]
        srv._try_open_multicast_socket_once = lambda **_kw: False  # type: ignore[assignment]
        # Manually set stop_event after the first wait so the post-wait
        # `is_set()` guard fires.
        original = srv._stop_event.is_set
        check_count = [0]

        def fake_is_set() -> bool:
            check_count[0] += 1
            # Let the outer ``while not stop_event.is_set()`` keep going
            # the first two times, then halt.
            return check_count[0] > 2

        srv._stop_event.is_set = fake_is_set  # type: ignore[assignment]
        try:
            srv._recover_multicast_socket_background()
        finally:
            srv._stop_event.is_set = original  # type: ignore[assignment]


class TestOtpHandleSendErrorRecoveryThreadGuard:
    """If a recovery thread is already running, ``_handle_send_error``
    must not spawn a second one – that would double-open the socket and
    leak file descriptors on every transient errno tick.
    """

    def test_no_rebuild_when_stopping(self) -> None:
        # #550: once stop_event is set, teardown owns the socket – a transient
        # send error must NOT spawn a recovery thread that could clobber the
        # socket across a restart().
        import errno as _e

        srv = OtpServer(system_number=1)
        sentinel_socket = MagicMock()
        srv._socket = sentinel_socket
        srv._stop_event.set()
        srv._handle_send_error(OSError(_e.ENETDOWN, "iface gone"))
        assert srv._socket_thread is None  # no recovery thread spawned
        assert srv._socket is sentinel_socket  # socket left for stop() to close

    def test_no_second_thread_when_one_already_running(self) -> None:
        import errno as _e

        srv = OtpServer(system_number=1)
        srv._socket = MagicMock()

        class _AliveStub:
            def is_alive(self) -> bool:
                return True

            def join(self, timeout=None) -> None:
                pass

        srv._socket_thread = _AliveStub()  # type: ignore[assignment]
        original_thread = srv._socket_thread
        srv._handle_send_error(OSError(_e.EHOSTUNREACH, "down"))
        assert srv._socket_thread is original_thread

    def test_old_stack_close_failure_logged(self, caplog) -> None:
        import errno as _e
        import logging as _logging

        srv = OtpServer(system_number=1)
        srv._socket = MagicMock()

        # Force the old ExitStack to raise on close; the helper must
        # log via ``logger.exception`` and not propagate.
        bad_stack = MagicMock()
        bad_stack.close.side_effect = RuntimeError("close failed")
        srv._exit_stack = bad_stack
        with caplog.at_level(_logging.ERROR, logger="openfollow.otp.server"):
            srv._handle_send_error(OSError(_e.ENETDOWN, "iface gone"))
        assert any("closing stale socket stack failed" in r.message for r in caplog.records)
        # Cleanup: stop the recovery thread.
        srv._stop_event.set()
        if srv._socket_thread is not None:
            srv._socket_thread.join(timeout=2.0)


class TestOtpRestartWithOverride:
    def test_restart_preserves_explicit_override(self) -> None:
        with patch("openfollow.otp.server.multicast_expert.McastTxSocket") as mcls:
            mcls.return_value = MagicMock()
            srv = OtpServer(system_number=1, mcast_ip="192.0.2.50")
            assert srv._mcast_ip == "192.0.2.50"
            srv.start()
            srv.restart(
                system_name="X",
                system_number=2,
                port=OTP_PORT,
                source_ip="",
                priority=100,
            )
            # System number changed, but mcast_ip stays at the override.
            assert srv._system_number == 2
            assert srv._mcast_ip == "192.0.2.50"
            assert srv._transform_dest == "192.0.2.50"
            srv.stop()


class TestOtpRestartFailureRaises:
    def test_restart_raises_when_socket_cannot_open(self) -> None:
        with patch("openfollow.otp.server.multicast_expert.McastTxSocket") as mcls:
            mcls.return_value = MagicMock()
            srv = OtpServer(system_number=1)
            srv.start()
            mcls.side_effect = OSError("dead interface")
            with pytest.raises(OSError, match="failed to open multicast socket"):
                srv.restart(
                    system_name="X",
                    system_number=2,
                    port=OTP_PORT,
                    source_ip="",
                    priority=100,
                )


class TestOtpSendOversizePacketSkipped:
    def _server_with_oversize_load(self) -> OtpServer:
        srv = OtpServer(system_number=1)
        srv._socket = MagicMock()
        # 70 markers blow the 1472-octet cap.
        for i in range(1, 71):
            srv.register_marker(_marker(i, name=f"T{i}"))
        return srv

    def test_oversize_payload_drops_packet_with_warning(self, caplog) -> None:
        import logging as _logging

        srv = self._server_with_oversize_load()
        with caplog.at_level(_logging.WARNING, logger="openfollow.otp.server"):
            srv._send_transform_packet()
        srv._socket.sendto.assert_not_called()
        assert any("transform packet skipped" in r.message for r in caplog.records)
        assert srv._oversize_drops == 1

    def test_oversize_log_throttles_after_5_drops(self, caplog) -> None:
        """Length-cap drops are throttled with the same first-5-then-
        every-100 pattern as ``_send``'s OSError counter, so a misconfigured
        install pushing too many markers doesn't flood the log at the
        transform fps."""
        import logging as _logging

        srv = self._server_with_oversize_load()
        with caplog.at_level(_logging.WARNING, logger="openfollow.otp.server"):
            for _ in range(20):
                srv._send_transform_packet()
        # 20 calls → first 5 log, then nothing until the 100th – so
        # exactly 5 warnings hit the buffer.
        warnings = [r for r in caplog.records if "transform packet skipped" in r.message]
        assert len(warnings) == 5
        assert srv._oversize_drops == 20

    def test_oversize_log_resumes_at_100th_drop(self, caplog) -> None:
        import logging as _logging

        srv = self._server_with_oversize_load()
        # Simulate having already passed the first-5 burst.
        srv._oversize_drops = 99
        with caplog.at_level(_logging.WARNING, logger="openfollow.otp.server"):
            srv._send_transform_packet()
        # Next drop is the 100th → must log.
        assert any("transform packet skipped" in r.message for r in caplog.records)
        assert srv._oversize_drops == 100


class TestOtpSendOsErrorPath:
    def test_send_oserror_invokes_handle_and_logs(self, caplog) -> None:
        import errno as _e
        import logging as _logging

        srv = OtpServer(system_number=1)
        mock_sock = MagicMock()
        mock_sock.sendto.side_effect = OSError(_e.ENETUNREACH, "unreachable")
        srv._socket = mock_sock
        with caplog.at_level(_logging.WARNING, logger="openfollow.otp.server"):
            srv._send(b"\x00" * 16, "239.159.1.1")
        assert srv._send_errors == 1
        assert any("OTP send failed" in r.message for r in caplog.records)
        # Cleanup: ENETUNREACH is transient → spawned recovery thread.
        srv._stop_event.set()
        if srv._socket_thread is not None:
            srv._socket_thread.join(timeout=2.0)

    def test_send_log_throttled_after_5_errors(self, caplog) -> None:
        import logging as _logging

        srv = OtpServer(system_number=1)
        mock_sock = MagicMock()
        mock_sock.sendto.side_effect = OSError(99999, "non-transient")
        srv._socket = mock_sock
        with caplog.at_level(_logging.WARNING, logger="openfollow.otp.server"):
            for _ in range(20):
                srv._send(b"\x00", "239.159.1.1")
        # First 5 errors logged, then every 100th – so 20 calls = 5 logs.
        warning_lines = [r for r in caplog.records if "OTP send failed" in r.message]
        assert len(warning_lines) == 5


class TestOtpHandleSendErrorEdges:
    def test_non_transient_errno_is_noop(self) -> None:
        srv = OtpServer()
        sentinel = object()
        srv._socket = sentinel  # type: ignore[assignment]
        srv._handle_send_error(OSError(99999, "weird"))
        assert srv._socket is sentinel

    def test_unicast_mode_does_not_spawn_recovery_thread(self) -> None:
        import errno as _e

        srv = OtpServer(mcast_ip="")
        sentinel = object()
        srv._socket = sentinel  # type: ignore[assignment]
        srv._handle_send_error(OSError(_e.EHOSTUNREACH, "host gone"))
        assert srv._socket is sentinel
        assert srv._socket_thread is None

    def test_transient_errno_in_multicast_mode_spawns_recovery(self) -> None:
        import errno as _e

        srv = OtpServer(system_number=1)
        srv._socket = MagicMock()
        srv._handle_send_error(OSError(_e.ENETUNREACH, "nope"))
        try:
            assert srv._socket is None  # cleared for recovery
            assert srv._socket_thread is not None
        finally:
            srv._stop_event.set()
            if srv._socket_thread is not None:
                srv._socket_thread.join(timeout=2.0)
