# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for RTTrPM packet encoding and RttrpmServer lifecycle."""

from __future__ import annotations

import socket
import struct
from unittest.mock import MagicMock, patch

import pytest

from openfollow.psn.marker import Marker
from openfollow.rttrpm.server import (
    _CENTROID_SIZE,
    _FLOAT_SIG,
    _HEADER_SIZE,
    _INT_SIG,
    _MAX_TRACKABLE_NAME_BYTES,
    _PKT_FORMAT_RAW,
    _PKT_TYPE_CENTROID,
    _PKT_TYPE_TRACKABLE,
    _VERSION,
    DEFAULT_PORT,
    RttrpmServer,
    _encode_trackable_name,
    encode_rttrpm_centroid_module,
    encode_rttrpm_packet,
    encode_rttrpm_trackable_module,
)

pytestmark = pytest.mark.unit


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


# ---------------------------------------------------------------------------
# Centroid Position module
# ---------------------------------------------------------------------------


class TestEncodeCentroidModule:
    def test_size_is_29(self) -> None:
        assert len(encode_rttrpm_centroid_module(0.0, 0.0, 0.0)) == 29

    def test_pktype_is_0x02(self) -> None:
        data = encode_rttrpm_centroid_module(0.0, 0.0, 0.0)
        assert data[0] == _PKT_TYPE_CENTROID

    def test_size_field(self) -> None:
        data = encode_rttrpm_centroid_module(0.0, 0.0, 0.0)
        (size,) = struct.unpack_from("!H", data, 1)
        assert size == _CENTROID_SIZE

    def test_latency_zero_by_default(self) -> None:
        data = encode_rttrpm_centroid_module(1.0, 2.0, 3.0)
        (latency,) = struct.unpack_from("!H", data, 3)
        assert latency == 0

    def test_custom_latency(self) -> None:
        data = encode_rttrpm_centroid_module(0.0, 0.0, 0.0, latency=42)
        (latency,) = struct.unpack_from("!H", data, 3)
        assert latency == 42

    def test_xyz_as_doubles(self) -> None:
        data = encode_rttrpm_centroid_module(1.5, -2.5, 3.75)
        x, y, z = struct.unpack_from("!ddd", data, 5)
        assert x == pytest.approx(1.5)
        assert y == pytest.approx(-2.5)
        assert z == pytest.approx(3.75)

    def test_negative_positions(self) -> None:
        data = encode_rttrpm_centroid_module(-10.0, -20.0, -30.0)
        x, y, z = struct.unpack_from("!ddd", data, 5)
        assert x == pytest.approx(-10.0)
        assert y == pytest.approx(-20.0)
        assert z == pytest.approx(-30.0)

    def test_struct_format_calcsize(self) -> None:
        assert struct.calcsize("!BHHddd") == 29


# ---------------------------------------------------------------------------
# Trackable module
# ---------------------------------------------------------------------------


class TestEncodeTrackableModule:
    def _centroid(self) -> bytes:
        return encode_rttrpm_centroid_module(0.0, 0.0, 0.0)

    def test_pktype_is_0x01(self) -> None:
        data = encode_rttrpm_trackable_module("T", self._centroid())
        assert data[0] == _PKT_TYPE_TRACKABLE

    def test_size_field_matches_actual_length(self) -> None:
        data = encode_rttrpm_trackable_module("T", self._centroid())
        (size,) = struct.unpack_from("!H", data, 1)
        assert size == len(data)

    def test_namelen_field(self) -> None:
        data = encode_rttrpm_trackable_module("AB", self._centroid())
        assert data[3] == 2

    def test_name_embedded_as_utf8(self) -> None:
        data = encode_rttrpm_trackable_module("Hi", self._centroid())
        assert data[4:6] == b"Hi"

    def test_num_mods_is_one(self) -> None:
        name = "T"
        data = encode_rttrpm_trackable_module(name, self._centroid())
        name_offset = 3 + len(name.encode("utf-8"))
        assert data[name_offset + 1] == 1

    def test_centroid_appended_after_header(self) -> None:
        centroid = self._centroid()
        data = encode_rttrpm_trackable_module("T", centroid)
        # body starts at offset 3: nameLen(1) + name(1) + numMods(1) + centroid
        assert data[6:] == centroid

    def test_multi_byte_name(self) -> None:
        name = "Marker 0"
        data = encode_rttrpm_trackable_module(name, self._centroid())
        assert data[3] == len(name)
        assert data[4 : 4 + len(name)] == name.encode("utf-8")

    def test_size_grows_with_name_length(self) -> None:
        c = self._centroid()
        short = encode_rttrpm_trackable_module("A", c)
        long_ = encode_rttrpm_trackable_module("AB", c)
        assert len(long_) == len(short) + 1


# ---------------------------------------------------------------------------
# Trackable name truncation
#
# The Trackable module's `nameLen` field is uint8, so names longer than
# 255 UTF-8 bytes can't be represented. Without truncation, struct.pack
# would raise and kill the send loop on the first oversized marker name.
# ---------------------------------------------------------------------------


class TestEncodeTrackableName:
    def test_max_constant_is_255(self) -> None:
        assert _MAX_TRACKABLE_NAME_BYTES == 255

    def test_short_ascii_passes_through(self) -> None:
        assert _encode_trackable_name("Spot 1") == b"Spot 1"

    def test_empty_name_returns_empty(self) -> None:
        # Wiki: "Zero-length names: Supported (Length field = 0x00 implies
        # no name bytes follow)".
        assert _encode_trackable_name("") == b""

    def test_exactly_255_bytes_passes_through(self) -> None:
        name = "A" * 255
        out = _encode_trackable_name(name)
        assert len(out) == 255
        assert out == name.encode("utf-8")

    def test_256_ascii_bytes_truncated_to_255(self) -> None:
        out = _encode_trackable_name("A" * 256)
        assert len(out) == 255
        assert out == b"A" * 255

    def test_truncation_lands_on_rune_boundary_4_byte(self) -> None:
        # 254 ASCII + one 4-byte rune (U+1F3A4 microphone) = 258 bytes
        # raw. Naive cut at 255 would split the rune and leave a 1-byte
        # partial sequence; we must drop the whole rune and stop at 254.
        name = "A" * 254 + "\U0001f3a4"
        out = _encode_trackable_name(name)
        assert len(out) == 254
        assert out == b"A" * 254

    def test_truncation_lands_on_rune_boundary_3_byte(self) -> None:
        # 254 ASCII + one 3-byte rune (U+2603 snowman) = 257 bytes.
        # Cut at 255 lands inside the rune; we back off to 254.
        name = "B" * 254 + "☃"
        out = _encode_trackable_name(name)
        assert len(out) == 254
        assert out == b"B" * 254

    def test_truncation_lands_on_rune_boundary_2_byte(self) -> None:
        # 254 ASCII + one 2-byte rune (U+00E9 é) = 256 bytes.
        # Cut at 255 lands inside the rune; we back off to 254.
        # Asserting the actual bytes (not just length) catches a
        # future regression where the truncator returns the right
        # length but wrong content – e.g. dropping an ASCII byte but
        # keeping the leading byte of the partial rune.
        name = "C" * 254 + "é"
        out = _encode_trackable_name(name)
        assert len(out) == 254
        assert out == b"C" * 254

    def test_truncation_keeps_complete_rune_when_boundary_aligns(self) -> None:
        # 253 ASCII + one 2-byte rune = 255 bytes – exactly fits.
        name = "D" * 253 + "é"
        out = _encode_trackable_name(name)
        assert len(out) == 255
        assert out.endswith("é".encode())


class TestTrackableModuleHandlesOversizeName:
    """Integration-level: ``encode_rttrpm_trackable_module`` must not raise
    on names that overflow the uint8 length cap. Before the truncation
    helper this would fail with ``struct.error: ubyte format requires
    0 <= number <= 255`` and crash the send thread."""

    def _centroid(self) -> bytes:
        return encode_rttrpm_centroid_module(0.0, 0.0, 0.0)

    def test_oversize_name_does_not_raise(self) -> None:
        encode_rttrpm_trackable_module("X" * 1000, self._centroid())

    def test_oversize_name_namelen_clamped_to_255(self) -> None:
        data = encode_rttrpm_trackable_module("X" * 1000, self._centroid())
        assert data[3] == 255

    def test_oversize_name_size_field_matches_actual_length(self) -> None:
        # Even after truncation, the module's outer `size` field must
        # equal the actual encoded length (the trackable size assertion
        # in TestEncodeTrackableModule covers the short-name case; this
        # one covers the truncated path).
        data = encode_rttrpm_trackable_module("Y" * 500, self._centroid())
        (size,) = struct.unpack_from("!H", data, 1)
        assert size == len(data)


# ---------------------------------------------------------------------------
# Full packet
# ---------------------------------------------------------------------------


class TestEncodeRttrpmPacket:
    def _one_marker(self) -> Marker:
        return _marker(1, 1.0, 2.0, 3.0, name="T")

    def test_header_size(self) -> None:
        assert struct.calcsize("!HHHIBHIB") == _HEADER_SIZE == 18

    def test_int_sig(self) -> None:
        pkt = encode_rttrpm_packet(0, [])
        (val,) = struct.unpack_from("!H", pkt, 0)
        assert val == _INT_SIG

    def test_float_sig(self) -> None:
        pkt = encode_rttrpm_packet(0, [])
        (val,) = struct.unpack_from("!H", pkt, 2)
        assert val == _FLOAT_SIG

    def test_version(self) -> None:
        pkt = encode_rttrpm_packet(0, [])
        (val,) = struct.unpack_from("!H", pkt, 4)
        assert val == _VERSION

    def test_pkt_format_is_zero(self) -> None:
        pkt = encode_rttrpm_packet(0, [])
        assert pkt[10] == _PKT_FORMAT_RAW

    def test_size_field_matches_packet_length(self) -> None:
        pkt = encode_rttrpm_packet(0, [self._one_marker()])
        (size,) = struct.unpack_from("!H", pkt, 11)
        assert size == len(pkt)

    def test_context_encoded(self) -> None:
        pkt = encode_rttrpm_packet(0, [], context=0xDEADBEEF)
        (ctx,) = struct.unpack_from("!I", pkt, 13)
        assert ctx == 0xDEADBEEF

    def test_context_defaults_to_zero(self) -> None:
        pkt = encode_rttrpm_packet(0, [])
        (ctx,) = struct.unpack_from("!I", pkt, 13)
        assert ctx == 0

    def test_num_modules_zero_markers(self) -> None:
        pkt = encode_rttrpm_packet(0, [])
        assert pkt[17] == 0

    def test_num_modules_one_marker(self) -> None:
        pkt = encode_rttrpm_packet(0, [self._one_marker()])
        assert pkt[17] == 1

    def test_num_modules_two_markers(self) -> None:
        # Distinct ids so an ID-indexing bug in the encoder doesn't
        # silently collapse to the same module twice.
        markers = [_marker(1, name="A"), _marker(2, name="B")]
        pkt = encode_rttrpm_packet(0, markers)
        assert pkt[17] == 2

    def test_pkt_id_encoded(self) -> None:
        pkt = encode_rttrpm_packet(42, [])
        (pkt_id,) = struct.unpack_from("!I", pkt, 6)
        assert pkt_id == 42

    def test_pkt_id_wraps_at_32_bits(self) -> None:
        pkt = encode_rttrpm_packet(0xFFFFFFFF + 1, [])
        (pkt_id,) = struct.unpack_from("!I", pkt, 6)
        assert pkt_id == 0

    def test_empty_marker_list_produces_header_only(self) -> None:
        pkt = encode_rttrpm_packet(0, [])
        assert len(pkt) == _HEADER_SIZE

    def test_positions_in_metres_not_micrometres(self) -> None:
        # Positions must be stored as metres (doubles), not multiplied by 1e6.
        t = _marker(1, 1.0, 2.0, 3.0, name="T")
        pkt = encode_rttrpm_packet(0, [t])
        # centroid x is at offset 18 (trackable header) + 3 (nameLen+name+numMods) + 3 (centroid BHH) = ...
        # trackable header: pkType(1) + size(2) + nameLen(1) + name(1) + numMods(1) = 6
        # centroid: pkType(1) + size(2) + latency(2) = 5 bytes before x
        centroid_offset = _HEADER_SIZE + 6 + 5
        (x,) = struct.unpack_from("!d", pkt, centroid_offset)
        assert x == pytest.approx(1.0)
        assert x != pytest.approx(1_000_000.0)

    def test_marker_name_in_packet(self) -> None:
        t = _marker(1, name="Stage")
        pkt = encode_rttrpm_packet(0, [t])
        assert b"Stage" in pkt


class TestEncodePacketCaps:
    """numModules (uint8) / size (uint16) overflow must cap, not crash."""

    def test_caps_module_count_at_255(self) -> None:
        markers = [_marker(i, name="T") for i in range(1, 300)]  # 299 markers
        pkt = encode_rttrpm_packet(0, markers)  # must not raise
        assert pkt[17] == 255  # numModules capped to the uint8 limit

    def test_caps_on_packet_size_before_count(self) -> None:
        # 255 markers with max-length names blow the uint16 size before the count.
        markers = [_marker(i, name="X" * 255) for i in range(1, 256)]
        pkt = encode_rttrpm_packet(0, markers)
        assert pkt[17] < 255  # capped by size, not count
        assert len(pkt) <= 65507


# ---------------------------------------------------------------------------
# Marker management
# ---------------------------------------------------------------------------


class TestRttrpmServerMarkerManagement:
    def _server(self) -> RttrpmServer:
        return RttrpmServer(host="127.0.0.1", port=DEFAULT_PORT)

    def test_register_and_retrieve(self) -> None:
        srv = self._server()
        t = _marker(1)
        srv.register_marker(t)
        assert srv.get_marker(1) is t

    def test_unregister_removes_marker(self) -> None:
        srv = self._server()
        t = _marker(1)
        srv.register_marker(t)
        srv.unregister_marker(1)
        assert srv.get_marker(1) is None

    def test_unregister_nonexistent_is_noop(self) -> None:
        srv = self._server()
        srv.unregister_marker(99)  # must not raise

    def test_multiple_markers_independent(self) -> None:
        srv = self._server()
        t0 = _marker(1, name="A")
        t1 = _marker(2, name="B")
        srv.register_marker(t0)
        srv.register_marker(t1)
        assert srv.get_marker(1) is t0
        assert srv.get_marker(2) is t1

    def test_register_replaces_existing_id(self) -> None:
        srv = self._server()
        t_old = _marker(1, name="Old")
        t_new = _marker(1, name="New")
        srv.register_marker(t_old)
        srv.register_marker(t_new)
        assert srv.get_marker(1) is t_new

    def test_unknown_id_returns_none(self) -> None:
        srv = self._server()
        assert srv.get_marker(42) is None


# ---------------------------------------------------------------------------
# Send behaviour
# ---------------------------------------------------------------------------


class TestRttrpmServerSend:
    def test_send_skipped_when_no_markers(self) -> None:
        srv = RttrpmServer(host="127.0.0.1")
        mock_sock = MagicMock()
        srv._socket = mock_sock
        srv._send_packet()
        mock_sock.sendto.assert_not_called()

    def test_send_targets_configured_host_and_port(self) -> None:
        srv = RttrpmServer(host="192.168.1.50", port=12345)
        mock_sock = MagicMock()
        srv._socket = mock_sock
        srv.register_marker(_marker(1))
        srv._send_packet()
        assert mock_sock.sendto.call_count == 1
        _, dest = mock_sock.sendto.call_args[0]
        assert dest == ("192.168.1.50", 12345)

    def test_send_dropped_when_socket_is_none(self) -> None:
        srv = RttrpmServer(host="127.0.0.1")
        srv._socket = None
        srv.register_marker(_marker(1))
        srv._send_packet()  # must not raise

    def test_send_skips_encoding_when_rebuild_leaves_socket_down(self) -> None:
        # A throttled/failed rebuild leaves _socket None; _send_packet must
        # return early instead of encoding a packet _send() would only drop.
        srv = RttrpmServer(host="127.0.0.1")
        srv._socket = None
        srv.register_marker(_marker(1))
        with (
            patch.object(srv, "_maybe_rebuild_socket_after_error") as rebuild,
            patch.object(srv, "_send") as send,
        ):
            srv._send_packet()
        rebuild.assert_called_once()
        send.assert_not_called()

    def test_send_is_noop_when_socket_is_none(self) -> None:
        # _send's own None guard (defence-in-depth behind _send_packet).
        srv = RttrpmServer(host="127.0.0.1")
        srv._socket = None
        srv._send(b"\x00\x01")  # must not raise


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestRttrpmServerLifecycle:
    def test_stop_before_start_is_safe(self) -> None:
        srv = RttrpmServer(host="127.0.0.1")
        srv.stop()  # must not raise

    def test_start_spawns_exactly_one_thread(self) -> None:
        with patch("openfollow.rttrpm.server.socket.socket"):
            srv = RttrpmServer(host="127.0.0.1")
            srv.start()
            assert srv._send_thread is not None
            assert not hasattr(srv, "_adv_thread"), "RTTrPM must not spawn an advertisement thread"
            srv.stop()

    def test_stop_joins_thread(self) -> None:
        with patch("openfollow.rttrpm.server.socket.socket"):
            srv = RttrpmServer(host="127.0.0.1")
            srv.start()
            srv.stop()
            assert srv._send_thread is None
            assert srv._socket is None

    def test_context_manager_starts_and_stops(self) -> None:
        with patch("openfollow.rttrpm.server.socket.socket"):
            srv = RttrpmServer(host="127.0.0.1")
            with srv:
                assert srv._send_thread is not None
            assert srv._send_thread is None

    def test_socket_is_plain_udp_not_multicast(self) -> None:
        with patch("openfollow.rttrpm.server.socket.socket") as mock_socket_cls:
            srv = RttrpmServer(host="127.0.0.1")
            srv.start()
            mock_socket_cls.assert_called_once_with(socket.AF_INET, socket.SOCK_DGRAM)
            srv.stop()

    def test_send_loop_survives_fps_zero(self) -> None:
        # Defence-in-depth guard in _send_loop: even if a caller
        # constructs the server with fps=0 (bypassing the TOML and web
        # parser clamps), the thread must not crash with
        # ZeroDivisionError.
        with patch("openfollow.rttrpm.server.socket.socket"):
            srv = RttrpmServer(host="127.0.0.1", fps=0)
            srv.start()
            assert srv._send_thread is not None
            assert srv._send_thread.is_alive()
            srv.stop()
            assert srv._send_thread is None

    def test_restart_swaps_socket_and_applies_new_config(self) -> None:
        """``restart`` recycles the UDP socket and send thread under new
        config; old socket closes, new one opens with updated fields."""
        with patch("openfollow.rttrpm.server.socket.socket") as mock_socket_cls:
            srv = RttrpmServer(host="10.0.0.1", port=9000, fps=30.0, context=0)
            srv.start()
            first_call_count = mock_socket_cls.call_count
            assert first_call_count == 1
            first_thread = srv._send_thread

            srv.restart(host="10.0.0.2", port=9001, fps=60.0, context=42)

            # New socket constructed; new send thread spawned.
            assert mock_socket_cls.call_count == first_call_count + 1
            assert srv._send_thread is not None
            assert srv._send_thread is not first_thread
            # Config fields reflect the new values.
            assert srv._host == "10.0.0.2"
            assert srv._port == 9001
            assert srv._fps == 60.0
            assert srv._context == 42
            srv.stop()

    def test_restart_preserves_marker_registrations(self) -> None:
        """Marker registrations live on ``self._markers`` and are not
        touched by ``stop()``/``start()`` – bound markers survive a
        config-only restart."""
        from openfollow.psn.marker import Marker

        with patch("openfollow.rttrpm.server.socket.socket"):
            srv = RttrpmServer(host="127.0.0.1")
            srv.start()
            srv.register_marker(Marker(1, "T1"))
            srv.register_marker(Marker(2, "T2"))

            srv.restart(host="127.0.0.2", port=9999, fps=60.0, context=0)

            assert srv.get_marker(1) is not None
            assert srv.get_marker(2) is not None
            srv.stop()


# ---------------------------------------------------------------------------
# Interface-change recovery
# ---------------------------------------------------------------------------


class TestRttrpmSocketRecovery:
    def test_send_rebuilds_socket_on_enetunreach(self) -> None:
        # Simulate a transient interface change: the first sendto raises
        # ENETUNREACH; _send_rebuild_after_error should replace _socket
        # with a fresh one so the next send has a chance to succeed.
        import errno as _e

        srv = RttrpmServer(host="127.0.0.1")
        first_sock = MagicMock()
        first_sock.sendto.side_effect = OSError(_e.ENETUNREACH, "unreach")
        srv._socket = first_sock

        srv._send(b"x")

        assert srv._socket is not None, "socket must be rebuilt after transient error"
        assert srv._socket is not first_sock, "a new socket object must replace the broken one"
        first_sock.close.assert_called_once()

    def test_send_does_not_rebuild_on_non_transient_error(self) -> None:
        # A permission error (EACCES) is not an interface-change signal;
        # we log and continue, but the socket must not be rebuilt.
        import errno as _e

        srv = RttrpmServer(host="127.0.0.1")
        sock = MagicMock()
        sock.sendto.side_effect = OSError(_e.EACCES, "denied")
        srv._socket = sock

        srv._send(b"x")

        assert srv._socket is sock, "non-transient error must not trigger rebuild"

    def test_send_rebuild_is_throttled_while_errors_persist(self) -> None:
        import errno as _e

        srv = RttrpmServer(host="127.0.0.1")
        sock = MagicMock()
        sock.sendto.side_effect = OSError(_e.ENETUNREACH, "unreach")
        srv._socket = sock

        with (
            patch.object(srv, "_rebuild_socket_after_error") as rebuild,
            patch(
                "openfollow.rttrpm.server.time.monotonic",
                side_effect=[100.0, 100.2],
            ),
        ):
            srv._send(b"x")
            srv._send(b"x")

        assert rebuild.call_count == 1

    def test_send_rebuild_retries_after_throttle_interval(self) -> None:
        import errno as _e

        srv = RttrpmServer(host="127.0.0.1")
        sock = MagicMock()
        sock.sendto.side_effect = OSError(_e.ENETUNREACH, "unreach")
        srv._socket = sock

        with (
            patch.object(srv, "_rebuild_socket_after_error") as rebuild,
            patch(
                "openfollow.rttrpm.server.time.monotonic",
                side_effect=[100.0, 101.1],
            ),
        ):
            srv._send(b"x")
            srv._send(b"x")

        assert rebuild.call_count == 2


# ---------------------------------------------------------------------------
# Stop-thread-timeout warning + rebuild error swallowing
class TestRttrpmStopTimeout:
    def test_stop_warns_when_send_thread_does_not_join(self, caplog) -> None:
        import logging as _logging

        srv = RttrpmServer(host="127.0.0.1")

        class _StubThread:
            def join(self, timeout=None):
                pass  # don't actually wait – just simulate timeout

            def is_alive(self):
                return True

        srv._send_thread = _StubThread()  # type: ignore[assignment]
        srv._socket = None

        with caplog.at_level(_logging.WARNING, logger="openfollow.rttrpm.server"):
            srv.stop()

        assert any("send thread did not stop within timeout" in rec.message for rec in caplog.records)
        assert srv._send_thread is None


class TestRttrpmSendErrorSuppression:
    def test_send_log_is_rate_limited_after_first_5_errors(self, caplog) -> None:
        import errno as _e
        import logging as _logging

        srv = RttrpmServer(host="127.0.0.1")
        sock = MagicMock()
        sock.sendto.side_effect = OSError(_e.EACCES, "denied")  # non-transient
        srv._socket = sock

        with caplog.at_level(_logging.WARNING, logger="openfollow.rttrpm.server"):
            for _ in range(20):
                srv._send(b"x")

        # First 5 logged, the next 15 suppressed (none of 6..20 hit %100).
        assert len(caplog.records) == 5


class TestRttrpmRebuildSocketFailure:
    def test_rebuild_when_socket_constructor_raises_clears_state_and_warns(
        self,
        caplog,
    ) -> None:
        import logging as _logging

        srv = RttrpmServer(host="127.0.0.1")
        old_sock = MagicMock()
        srv._socket = old_sock

        with patch(
            "openfollow.rttrpm.server.socket.socket",
            side_effect=OSError("no fds"),
        ):
            with caplog.at_level(_logging.WARNING, logger="openfollow.rttrpm.server"):
                srv._rebuild_socket_after_error()

        assert srv._socket is None
        assert any("socket rebuild failed" in rec.message for rec in caplog.records)
        # Old socket still got closed since rebuild attempt happened.
        old_sock.close.assert_called_once()

    def test_rebuild_when_old_socket_was_none_skips_close(self) -> None:
        srv = RttrpmServer(host="127.0.0.1")
        srv._socket = None

        with patch("openfollow.rttrpm.server.socket.socket"):
            srv._rebuild_socket_after_error()

        assert srv._socket is not None  # fresh socket installed

    def test_rebuild_swallows_oserror_on_old_socket_close(self) -> None:
        srv = RttrpmServer(host="127.0.0.1")
        old_sock = MagicMock()
        old_sock.close.side_effect = OSError("already closed")
        srv._socket = old_sock

        with patch("openfollow.rttrpm.server.socket.socket"):
            srv._rebuild_socket_after_error()  # must not raise

        assert srv._socket is not None
        assert srv._socket is not old_sock


class TestRttrpmRecoversFromFailedRebuild:
    """A rebuild that failed (socket left None) must not permanently disable
    output – a later send attempt has to retry and can succeed once the
    transient condition (e.g. FD exhaustion) clears."""

    def test_send_packet_retries_rebuild_when_socket_is_none(self) -> None:
        # First rebuild fails (no fds) leaving _socket None; the next
        # _send_packet must attempt another rebuild rather than spinning
        # forever on a dead None socket.
        srv = RttrpmServer(host="127.0.0.1")
        srv.register_marker(_marker(1))
        srv._socket = None

        with patch.object(srv, "_maybe_rebuild_socket_after_error") as rebuild:
            srv._send_packet()

        rebuild.assert_called_once()

    def test_none_socket_transitions_back_to_working_socket(self) -> None:
        # End-to-end: from a None-socket state, a send attempt rebuilds the
        # socket and the packet then actually goes out.
        srv = RttrpmServer(host="127.0.0.1", port=4242)
        srv.register_marker(_marker(1))
        srv._socket = None
        srv._next_rebuild_at = 0.0

        new_sock = MagicMock()
        with patch("openfollow.rttrpm.server.socket.socket", return_value=new_sock):
            srv._send_packet()

        assert srv._socket is new_sock
        new_sock.sendto.assert_called_once()

    def test_send_packet_does_not_rebuild_when_socket_present(self) -> None:
        srv = RttrpmServer(host="127.0.0.1")
        srv.register_marker(_marker(1))
        srv._socket = MagicMock()

        with patch.object(srv, "_maybe_rebuild_socket_after_error") as rebuild:
            srv._send_packet()

        rebuild.assert_not_called()


class TestRttrpmCapWarning:
    """Cap warning must surface a recurring/newly-introduced cap condition,
    not be suppressed once-ever for the whole process lifetime."""

    def _capping_markers(self) -> list[Marker]:
        return [_marker(i, name="T") for i in range(1, 300)]  # 299 > 255

    def test_warns_once_within_a_cap_episode(self, caplog) -> None:
        srv = RttrpmServer(host="127.0.0.1")
        srv._socket = MagicMock()
        for t in self._capping_markers():
            srv.register_marker(t)

        with caplog.at_level("WARNING", logger="openfollow.rttrpm.server"):
            srv._send_packet()
            srv._send_packet()

        warns = [r for r in caplog.records if "RTTrPM packet capped" in r.message]
        assert len(warns) == 1  # one log line per episode, not every frame

    def test_rearms_after_cap_clears_and_returns(self, caplog) -> None:
        # Cap, then drop back under the limit (re-arm), then cap again: the
        # second cap episode must log again rather than stay silent forever.
        srv = RttrpmServer(host="127.0.0.1")
        srv._socket = MagicMock()
        capping = self._capping_markers()
        for t in capping:
            srv.register_marker(t)

        with caplog.at_level("WARNING", logger="openfollow.rttrpm.server"):
            srv._send_packet()  # episode 1 → warns
            for t in capping:
                srv.unregister_marker(t.marker_id)
            srv.register_marker(_marker(1))
            srv._send_packet()  # under limit → re-arms
            for t in capping:
                srv.register_marker(t)
            srv._send_packet()  # episode 2 → warns again

        warns = [r for r in caplog.records if "RTTrPM packet capped" in r.message]
        assert len(warns) == 2

    def test_uncapped_packet_never_warns(self, caplog) -> None:
        srv = RttrpmServer(host="127.0.0.1")
        srv._socket = MagicMock()
        srv.register_marker(_marker(1))

        with caplog.at_level("WARNING", logger="openfollow.rttrpm.server"):
            srv._send_packet()

        assert not [r for r in caplog.records if "RTTrPM packet capped" in r.message]

    def test_start_rearms_cap_warning(self) -> None:
        # The warn-once guard is per-instance and reset by start() (hence
        # restart() too) so a fresh misconfiguration after a config change
        # surfaces again rather than staying silent for the process lifetime.
        srv = RttrpmServer(host="127.0.0.1")
        srv._cap_warned = True  # left set by a prior cap episode

        with patch("openfollow.rttrpm.server.socket.socket"):
            srv.start()
            try:
                assert srv._cap_warned is False
            finally:
                srv.stop()
