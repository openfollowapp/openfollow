# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for :class:`OscMarkerAdapter`.

The adapter is the surface that ``InputManager`` calls every frame:
``flush_updates()`` returns a sparse ``dict[int, dict[str, float]]`` of
pending axis writes per marker. The consumer fills missing axes from the
marker's current position before applying. Covers triple/per-axis dispatch,
start/stop lifecycle, and the real-UDP round-trip.
"""

from __future__ import annotations

import socket
import time

import pytest

from openfollow.osc.input import OscMarkerAdapter
from openfollow.osc.service import _PYTHONOSC_AVAILABLE, OscService, find_free_udp_port

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Hermetic dispatch – invoke the handlers directly, no socket
# ---------------------------------------------------------------------------


def _adapter() -> OscMarkerAdapter:
    return OscMarkerAdapter(OscService(), port=12345)


def test_handle_routes_three_floats_into_pending() -> None:
    a = _adapter()
    a._handle_triple("/marker/0", 1.0, 2.0, 3.0)
    assert a.flush_updates() == {0: {"x": 1.0, "y": 2.0, "z": 3.0}}


def test_handle_handles_multiple_marker_ids() -> None:
    a = _adapter()
    a._handle_triple("/marker/0", 1.0, 0.0, 0.0)
    a._handle_triple("/marker/2", 0.0, 1.0, 0.0)
    a._handle_triple("/marker/3", 0.0, 0.0, 1.0)
    assert a.flush_updates() == {
        0: {"x": 1.0, "y": 0.0, "z": 0.0},
        2: {"x": 0.0, "y": 1.0, "z": 0.0},
        3: {"x": 0.0, "y": 0.0, "z": 1.0},
    }


def test_handle_later_triple_overwrites_earlier() -> None:
    a = _adapter()
    a._handle_triple("/marker/0", 1.0, 1.0, 1.0)
    a._handle_triple("/marker/0", 9.0, 9.0, 9.0)
    assert a.flush_updates() == {0: {"x": 9.0, "y": 9.0, "z": 9.0}}


def test_handle_drops_address_with_wrong_part_count() -> None:
    a = _adapter()
    a._handle_triple("/marker", 1.0, 2.0, 3.0)
    a._handle_triple("/marker/0/extra", 1.0, 2.0, 3.0)
    assert a.flush_updates() == {}


def test_handle_drops_non_finite_values() -> None:
    # NaN/inf must not reach a marker position (they'd serialise into PSN).
    a = _adapter()
    a._handle_triple("/marker/0", float("nan"), 1.0, 2.0)
    a._handle_triple("/marker/1", float("inf"), 1.0, 2.0)
    a._handle_axis("x", "/marker/2/x", float("-inf"))
    assert a.flush_updates() == {}


def test_handle_drops_non_integer_marker_id() -> None:
    a = _adapter()
    a._handle_triple("/marker/foo", 1.0, 2.0, 3.0)
    assert a.flush_updates() == {}


def test_handle_drops_message_with_too_few_args() -> None:
    a = _adapter()
    a._handle_triple("/marker/0", 1.0, 2.0)
    a._handle_triple("/marker/1")
    assert a.flush_updates() == {}


def test_handle_drops_message_with_non_numeric_args() -> None:
    a = _adapter()
    a._handle_triple("/marker/0", "hello", 2.0, 3.0)
    a._handle_triple("/marker/1", None, None, None)
    assert a.flush_updates() == {}


def test_flush_updates_clears_queue() -> None:
    a = _adapter()
    a._handle_triple("/marker/0", 1.0, 2.0, 3.0)
    assert a.flush_updates() == {0: {"x": 1.0, "y": 2.0, "z": 3.0}}
    assert a.flush_updates() == {}


def test_port_property_exposes_configured_port() -> None:
    a = OscMarkerAdapter(OscService(), port=9999)
    assert a.port == 9999


# ---------------------------------------------------------------------------
# Per-axis dispatch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("axis", ["x", "y", "z"])
def test_handle_axis_writes_single_axis(axis: str) -> None:
    a = _adapter()
    a._handle_axis(axis, f"/marker/4/{axis}", 7.5)
    assert a.flush_updates() == {4: {axis: 7.5}}


def test_handle_axis_merges_multiple_axes_for_same_marker() -> None:
    a = _adapter()
    a._handle_axis("x", "/marker/1/x", 1.0)
    a._handle_axis("y", "/marker/1/y", 2.0)
    a._handle_axis("z", "/marker/1/z", 3.0)
    assert a.flush_updates() == {1: {"x": 1.0, "y": 2.0, "z": 3.0}}


def test_handle_axis_later_overwrites_earlier_same_axis() -> None:
    a = _adapter()
    a._handle_axis("x", "/marker/0/x", 1.0)
    a._handle_axis("x", "/marker/0/x", 9.0)
    assert a.flush_updates() == {0: {"x": 9.0}}


def test_handle_axis_drops_wrong_path_depth() -> None:
    a = _adapter()
    # Two segments – looks like a triple address, not a per-axis one.
    a._handle_axis("x", "/marker/0", 1.0)
    # Four segments – extra trailing token.
    a._handle_axis("x", "/marker/0/x/extra", 1.0)
    assert a.flush_updates() == {}


def test_handle_axis_drops_when_axis_param_disagrees_with_address() -> None:
    a = _adapter()
    a._handle_axis("x", "/marker/0/y", 1.0)  # bound axis ≠ address
    assert a.flush_updates() == {}


def test_handle_axis_drops_unknown_axis_name() -> None:
    a = _adapter()
    a._handle_axis("w", "/marker/0/w", 1.0)
    assert a.flush_updates() == {}


def test_handle_axis_drops_non_integer_marker_id() -> None:
    a = _adapter()
    a._handle_axis("x", "/marker/foo/x", 1.0)
    assert a.flush_updates() == {}


def test_handle_axis_drops_wrong_arg_count() -> None:
    a = _adapter()
    a._handle_axis("x", "/marker/0/x")  # zero args
    a._handle_axis("y", "/marker/0/y", 1.0, 2.0)  # too many
    assert a.flush_updates() == {}


def test_handle_axis_drops_non_numeric_arg() -> None:
    a = _adapter()
    a._handle_axis("x", "/marker/0/x", "nope")
    a._handle_axis("y", "/marker/0/y", None)
    assert a.flush_updates() == {}


def test_per_axis_after_triple_overwrites_only_that_axis() -> None:
    """Triple seeds all three axes; a per-axis write that arrives later
    in the same window updates one key without disturbing the others."""
    a = _adapter()
    a._handle_triple("/marker/0", 1.0, 2.0, 3.0)
    a._handle_axis("x", "/marker/0/x", 9.0)
    assert a.flush_updates() == {0: {"x": 9.0, "y": 2.0, "z": 3.0}}


def test_triple_after_per_axis_clears_partial_state() -> None:
    """A triple is the operator's "use exactly these three values"
    signal – any per-axis writes queued earlier in the window are
    discarded so the resulting state is unambiguous."""
    a = _adapter()
    a._handle_axis("x", "/marker/0/x", 100.0)
    a._handle_axis("y", "/marker/0/y", 200.0)
    a._handle_triple("/marker/0", 1.0, 2.0, 3.0)
    assert a.flush_updates() == {0: {"x": 1.0, "y": 2.0, "z": 3.0}}


# ---------------------------------------------------------------------------
# Lifecycle – start/stop idempotence
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_start_idempotent_on_second_call() -> None:
    svc = OscService()
    port = find_free_udp_port()
    a = OscMarkerAdapter(svc, port=port)
    a.start()
    listener_first = svc._listener
    a.start()
    assert svc._listener is listener_first
    a.stop()


@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_stop_idempotent_when_not_started() -> None:
    svc = OscService()
    a = OscMarkerAdapter(svc, port=12345)
    a.stop()


@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_start_subscribes_triple_and_per_axis_patterns() -> None:
    svc = OscService()
    port = find_free_udp_port()
    a = OscMarkerAdapter(svc, port=port)
    a.start()
    try:
        assert "/marker/*" in svc._subscriptions
        assert "/marker/*/x" in svc._subscriptions
        assert "/marker/*/y" in svc._subscriptions
        assert "/marker/*/z" in svc._subscriptions
    finally:
        a.stop()
    # Stop must remove every pattern – no stale mappings left on the
    # shared service for other callers to inherit.
    assert "/marker/*" not in svc._subscriptions
    assert "/marker/*/x" not in svc._subscriptions
    assert "/marker/*/y" not in svc._subscriptions
    assert "/marker/*/z" not in svc._subscriptions


@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_start_unsubscribes_on_bind_failure() -> None:
    svc = OscService()
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(("127.0.0.1", 0))
    port = blocker.getsockname()[1]
    a = OscMarkerAdapter(svc, port=port)
    try:
        with pytest.raises(OSError):
            a.start()
        for pattern in (
            "/marker/*",
            "/marker/*/x",
            "/marker/*/y",
            "/marker/*/z",
        ):
            assert pattern not in svc._subscriptions
    finally:
        blocker.close()


# ---------------------------------------------------------------------------
# Integration – real UDP round-trip
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_real_udp_send_round_trips_through_adapter() -> None:
    """Wire-level proof: send via ``OscService.send`` to the adapter's
    own listener port, observe the dispatch via ``flush_updates``."""
    svc = OscService()
    port = find_free_udp_port()
    adapter = OscMarkerAdapter(svc, port=port)
    adapter.start()
    try:
        svc.send(
            "/marker/2",
            [3.5, -1.25, 0.0],
            host="127.0.0.1",
            port=port,
        )
        deadline = time.monotonic() + 1.0
        updates: dict[int, dict[str, float]] = {}
        while time.monotonic() < deadline and not updates:
            updates = adapter.flush_updates()
            if not updates:
                time.sleep(0.01)
        assert updates == {2: {"x": 3.5, "y": -1.25, "z": 0.0}}
    finally:
        adapter.stop()
        svc.shutdown()


@pytest.mark.integration
@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_real_udp_per_axis_round_trip() -> None:
    svc = OscService()
    port = find_free_udp_port()
    adapter = OscMarkerAdapter(svc, port=port)
    adapter.start()
    try:
        svc.send("/marker/2/y", [1.5], host="127.0.0.1", port=port)
        deadline = time.monotonic() + 1.0
        updates: dict[int, dict[str, float]] = {}
        while time.monotonic() < deadline and not updates:
            updates = adapter.flush_updates()
            if not updates:
                time.sleep(0.01)
        assert updates == {2: {"y": 1.5}}
    finally:
        adapter.stop()
        svc.shutdown()


@pytest.mark.integration
@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_real_udp_dispatch_respects_allowlist() -> None:
    """The adapter inherits the listener's allowlist filter – packets
    from a non-allowed sender are dropped before reaching ``flush``."""
    svc = OscService()
    port = find_free_udp_port()
    # Bind to a specific source address so we can demonstrate filtering;
    # localhost is in the allowlist, "192.0.2.1" wouldn't be (TEST-NET-1).
    adapter = OscMarkerAdapter(
        svc,
        port=port,
        allowed_sender_ips=["192.0.2.1"],
    )
    adapter.start()
    try:
        svc.send(
            "/marker/0",
            [1.0, 2.0, 3.0],
            host="127.0.0.1",
            port=port,
        )
        # Wait long enough that the packet would have arrived if accepted.
        time.sleep(0.2)
        assert adapter.flush_updates() == {}
    finally:
        adapter.stop()
        svc.shutdown()
