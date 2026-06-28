# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""One real end-to-end smoke test.

The rest of the suite is high-fidelity but synthetic: HTTP-against-Bottle and the
FakeGst harness against pipeline *builders*. Nothing runs a **real GStreamer
pipeline** or pushes a **real PSN packet through the real wire format out to a
real output socket**. That leaves one bug class invisible – a pipeline wired
backwards / a bad link, a PSN encode-decode regression, or the OTP micrometre
conversion silently breaking. This single test closes it.

Deliberately ONE test (opt-in `smoke_e2e`, not in the coverage gate): every
extra end-to-end test just doubles the flakiness budget. It skips cleanly where
GStreamer isn't installed and runs on the Ubuntu CI job on main pushes.
"""

from __future__ import annotations

import socket
import struct

import pypsn
import pytest

from openfollow.otp.server import OtpServer
from openfollow.psn.receiver import PsnReceiver
from openfollow.rttrpm.server import RttrpmServer, encode_rttrpm_centroid_module
from openfollow.video.receiver import gst_runtime_available

pytestmark = [
    pytest.mark.smoke_e2e,
    pytest.mark.skipif(not gst_runtime_available(), reason="GStreamer runtime not available"),
]

# A position whose components are exactly representable in float32 (the PSN wire
# type), so the round-trip is exact. OTP emits micrometres (×1e6); RTTrPM emits
# metres. No grid/unit/fader transform applies on the output path.
_TID = 7
_POS = (1.5, -2.5, 3.5)
_M_TO_UM = 1_000_000


def _psn_data_packet_bytes(tid: int, x: float, y: float, z: float) -> bytes:
    """Encode a real PSN data packet (all vectors populated – the encoder
    dereferences ``tracker.speed.x`` etc., so ``None`` is not allowed)."""
    zero = pypsn.PsnVector3(0.0, 0.0, 0.0)
    info = pypsn.PsnInfo(timestamp=0, version_high=2, version_low=0, frame_id=0, packet_count=1)
    tracker = pypsn.PsnTracker(
        tracker_id=tid,
        pos=pypsn.PsnVector3(x, y, z),
        speed=zero,
        ori=zero,
        accel=zero,
        trgtpos=zero,
        status=0,
        timestamp=0,
    )
    packet = pypsn.PsnDataPacket(info=info, trackers=[tracker])
    return pypsn.prepare_psn_data_packet_bytes(packet)


def _udp_capture_socket() -> socket.socket:
    """A UDP socket bound to an ephemeral loopback port, ready to recv output."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(2.0)
    return sock


def _drive_real_testpattern_pipeline() -> None:
    """Build the REAL testpattern pipeline via the app's own builder, drive it to
    PLAYING with a headless ``fakesink``, assert no bus error, tear it down.

    Uses ``MediaGalleryInput.create_pipeline`` (not a hand-rolled pipeline) so the
    actual element construction + linking is exercised. The HUD is a separate
    ``Gtk.DrawingArea``, so a head→sink overlay tail is faithful.
    """
    from gi.repository import Gst

    from openfollow.video.inputs.testpattern import MediaGalleryInput

    Gst.init(None)

    def prepare_sink() -> object:
        sink = Gst.ElementFactory.make("fakesink", "e2e-sink")
        sink.set_property("sync", False)  # don't block on the clock for a smoke run
        return sink

    def build_overlay_tail(pipeline: object, head: object, sink: object) -> None:
        if not head.link(sink):
            raise AssertionError("testpattern head failed to link to sink")

    pipeline = MediaGalleryInput().create_pipeline(
        {"testpattern_selected_media": "default:grey"},
        None,
        build_overlay_tail,
        prepare_sink,
    )
    try:
        assert pipeline.set_state(Gst.State.PLAYING) != Gst.StateChangeReturn.FAILURE
        # ``is-live`` source ⇒ ASYNC transition; wait for it to settle.
        _result, state, _pending = pipeline.get_state(3 * Gst.SECOND)
        err = pipeline.get_bus().poll(Gst.MessageType.ERROR, 0)
        assert err is None, f"pipeline error: {err.parse_error() if err else ''}"
        assert state == Gst.State.PLAYING
    finally:
        pipeline.set_state(Gst.State.NULL)


def test_e2e_real_pipeline_and_psn_to_outputs() -> None:
    """Real pipeline + PSN-wire → marker → OTP/RTTrPM output, position intact."""
    # 1) A real GStreamer pipeline (the FakeGst harness can't catch wiring bugs).
    _drive_real_testpattern_pipeline()

    # 2) Real PSN wire round-trip → live receiver → marker position.
    receiver = PsnReceiver()  # not start()ed: no socket bound, _on_packet drives it
    decoded = pypsn.parse_psn_packet(_psn_data_packet_bytes(_TID, *_POS))
    receiver._on_packet(decoded)
    marker = receiver.get_marker(_TID)
    assert marker is not None
    assert marker.pos == _POS

    # 3a) OTP output (loopback unicast, micrometres) carries the exact position.
    otp_cap = _udp_capture_socket()
    try:
        otp = OtpServer(mcast_ip="", port=otp_cap.getsockname()[1], system_number=1)
        otp._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            otp.register_marker(marker)
            otp._send_transform_packet()
            otp_pkt, _ = otp_cap.recvfrom(2048)
        finally:
            otp._socket.close()
    finally:
        otp_cap.close()
    assert otp_pkt[0:12] == b"OTP-E1.59\x00\x00\x00"
    # Position module sits at a fixed offset in a single-marker transform packet
    # (OTP/Transform/Point/Module layer headers + 1 options byte = 128).
    expected_um = tuple(int(c * _M_TO_UM) for c in _POS)
    assert struct.unpack("!iii", otp_pkt[128:140]) == expected_um

    # 3b) RTTrPM output (unicast, metres) carries the exact position – assert the
    # real encoder's centroid module bytes appear verbatim in the emitted packet.
    rt_cap = _udp_capture_socket()
    try:
        rttrpm = RttrpmServer(host="127.0.0.1", port=rt_cap.getsockname()[1])
        rttrpm._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            rttrpm.register_marker(marker)
            rttrpm._send_packet()
            rt_pkt, _ = rt_cap.recvfrom(4096)
        finally:
            rttrpm._socket.close()
    finally:
        rt_cap.close()
    assert rt_pkt[0:2] == b"\x41\x54"  # RTTrP intSig
    assert encode_rttrpm_centroid_module(*_POS) in rt_pkt
