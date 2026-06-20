# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for the operator-message store and config.

Covers :class:`OperatorMessageStore` (replace-by-marker, broadcast stack,
expiry, hard-cap eviction, clear-all / clear-marker, concurrency), the
:class:`OperatorMessage` countdown helpers, and the config surfaces
(``OperatorMessagesConfig``, ``OscConfig.multicast_group``,
``_coerce_multicast_ipv4``).
"""

from __future__ import annotations

import threading

import pytest

from openfollow.configuration import (
    OperatorMessagesConfig,
    OscConfig,
    _coerce_multicast_ipv4,
    load_config,
)
from openfollow.operator_messages import (
    _HARD_CAP,
    OperatorMessage,
    OperatorMessageStore,
    _as_float,
    _as_int,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# OperatorMessage countdown helpers
# --------------------------------------------------------------------------- #


def _msg(duration_s: float, created: float = 100.0, marker_id: int = 0) -> OperatorMessage:
    return OperatorMessage(
        message="m",
        info="",
        marker_id=marker_id,
        created_monotonic=created,
        duration_s=duration_s,
        seq=1,
    )


def test_forever_message_never_expires() -> None:
    m = _msg(0.0)
    assert m.is_expired(1e9) is False
    assert m.remaining_s(1e9) == 0.0
    assert m.remaining_fraction(1e9) == 1.0


def test_timed_message_expiry_and_remaining() -> None:
    m = _msg(10.0, created=100.0)
    assert m.is_expired(105.0) is False
    assert m.remaining_s(105.0) == pytest.approx(5.0)
    assert m.remaining_fraction(105.0) == pytest.approx(0.5)
    # At/after expiry.
    assert m.is_expired(110.0) is True
    assert m.remaining_s(120.0) == 0.0
    assert m.remaining_fraction(120.0) == 0.0


def test_remaining_fraction_clamps_above_one() -> None:
    # A clock reading before creation clamps to 1.0.
    m = _msg(10.0, created=100.0)
    assert m.remaining_fraction(90.0) == 1.0


# --------------------------------------------------------------------------- #
# Store – add / replace / stack
# --------------------------------------------------------------------------- #


def _store(start: float = 100.0):  # noqa: ANN202
    clock = {"t": start}
    store = OperatorMessageStore(clock=lambda: clock["t"])
    return store, clock


def test_broadcast_messages_stack() -> None:
    store, _ = _store()
    store.add("A", marker_id=0)
    store.add("B", marker_id=0)
    snap = store.snapshot()
    assert [m.message for m in snap] == ["B", "A"]  # newest-first


def test_keyed_messages_replace_per_marker() -> None:
    store, _ = _store()
    store.add("first", marker_id=3)
    store.add("second", marker_id=3)
    snap = store.snapshot()
    assert [m.message for m in snap] == ["second"]
    assert len(snap) == 1


def test_keyed_replace_only_affects_same_marker() -> None:
    store, _ = _store()
    store.add("m3", marker_id=3)
    store.add("m4", marker_id=4)
    store.add("m3-new", marker_id=3)
    snap = {m.marker_id: m.message for m in store.snapshot()}
    assert snap == {3: "m3-new", 4: "m4"}


def test_snapshot_drops_expired_and_orders_newest_first() -> None:
    store, clock = _store(100.0)
    store.add("forever", marker_id=0, duration_s=0.0)
    store.add("short", marker_id=0, duration_s=5.0)
    store.add("m7", marker_id=7, duration_s=0.0)
    clock["t"] = 106.0  # "short" expired
    snap = [m.message for m in store.snapshot()]
    assert snap == ["m7", "forever"]


def test_snapshot_accepts_explicit_now() -> None:
    store, _ = _store(100.0)
    store.add("short", marker_id=0, duration_s=5.0)
    assert store.snapshot(now=104.0)  # not yet expired
    assert store.snapshot(now=110.0) == []  # expired at caller's frame clock


def test_clear_all_empties_store() -> None:
    store, _ = _store()
    store.add("a", marker_id=0)
    store.add("b", marker_id=3)
    store.clear_all()
    assert store.snapshot() == []


def test_clear_marker_removes_only_that_marker() -> None:
    store, _ = _store()
    store.add("bcast", marker_id=0)
    store.add("m3", marker_id=3)
    store.add("m4", marker_id=4)
    store.clear_marker(3)
    assert {m.marker_id for m in store.snapshot()} == {0, 4}


def test_clear_marker_zero_clears_broadcasts() -> None:
    store, _ = _store()
    store.add("bcast1", marker_id=0)
    store.add("bcast2", marker_id=0)
    store.add("m3", marker_id=3)
    store.clear_marker(0)
    assert [m.marker_id for m in store.snapshot()] == [3]


def test_hard_cap_evicts_oldest_broadcasts() -> None:
    store, _ = _store()
    for i in range(_HARD_CAP + 10):
        store.add(f"msg{i}", marker_id=0, duration_s=0.0)
    snap = store.snapshot()
    assert len(snap) == _HARD_CAP
    # Newest kept (msg59 down to msg10); oldest (msg0..msg9) evicted.
    assert snap[0].message == f"msg{_HARD_CAP + 9}"
    assert all(m.message != "msg0" for m in snap)


def test_add_truncates_overlong_text() -> None:
    # message/info are truncated at ingest.
    from openfollow.operator_messages import _MAX_TEXT_LEN

    store, _ = _store()
    store.add("m" * 5000, "i" * 5000, marker_id=0, duration_s=0.0)
    m = store.snapshot()[0]
    assert len(m.message) == _MAX_TEXT_LEN
    assert len(m.info) == _MAX_TEXT_LEN


def test_add_coerces_odd_types_defensively() -> None:
    store, _ = _store()
    # bool marker_id / duration are rejected → broadcast forever.
    store.add("x", marker_id=True, duration_s=True)  # type: ignore[arg-type]
    m = store.snapshot()[0]
    assert m.marker_id == 0
    assert m.duration_s == 0.0
    # numeric-string-ish floats coerce.
    store.clear_all()
    store.add("y", marker_id=3.0, duration_s=5.0)  # type: ignore[arg-type]
    m = store.snapshot()[0]
    assert m.marker_id == 3
    assert m.duration_s == pytest.approx(5.0)


def test_store_is_thread_safe_under_concurrent_writes() -> None:
    store, _ = _store()

    def worker(base: int) -> None:
        for i in range(50):
            store.add(f"m{base}-{i}", marker_id=0)

    threads = [threading.Thread(target=worker, args=(b,)) for b in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # No crash / lost-update corruption; capped at the hard limit.
    assert len(store.snapshot()) == _HARD_CAP


# --------------------------------------------------------------------------- #
# Coercion helpers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value,expected",
    [(3, 3), (3.9, 3), ("4", 4), ("4.0", 0), (True, 0), (object(), 0), ("x", 0), (None, 0), (float("inf"), 0)],
)
def test_as_int(value: object, expected: int) -> None:
    assert _as_int(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (3, 3.0),
        (3.5, 3.5),
        ("4.5", 4.5),
        (True, 0.0),
        (object(), 0.0),
        ("x", 0.0),
        (10**400, 0.0),
        # Non-finite floats coerce to 0.0 (forever) – never pin a NaN card.
        (float("nan"), 0.0),
        (float("inf"), 0.0),
        (float("-inf"), 0.0),
        ("nan", 0.0),
        ("inf", 0.0),
    ],
)
def test_as_float(value: object, expected: float) -> None:
    assert _as_float(value) == pytest.approx(expected)


def test_add_rejects_nan_duration_at_the_store_boundary() -> None:
    # A NaN duration must not pin the card forever or yield a NaN-width bar:
    # the store is the documented coercion boundary, so it falls back to 0.0.
    store, clock = _store(100.0)
    store.add("x", marker_id=0, duration_s=float("nan"))
    m = store.snapshot()[0]
    # Treated as forever (duration 0.0), not an un-expiring NaN window.
    assert m.duration_s == 0.0
    clock["t"] = 1e9
    assert m.is_expired(clock["t"]) is False
    # Countdown fraction stays finite for the Cairo bar.
    assert m.remaining_fraction(clock["t"]) == 1.0


# --------------------------------------------------------------------------- #
# Config – OperatorMessagesConfig + OscConfig.multicast_group
# --------------------------------------------------------------------------- #


def test_operator_messages_config_defaults() -> None:
    cfg = OperatorMessagesConfig()
    # Default off.
    assert cfg.enabled is False
    assert cfg.position == "bottom"
    assert cfg.max_visible == 5
    assert cfg.route_by_marker is True
    assert cfg.scale == 1.0


def test_operator_messages_config_validates() -> None:
    cfg = OperatorMessagesConfig(enabled="nope", position="weird", max_visible=999)  # type: ignore[arg-type]
    assert cfg.enabled is False  # _coerce_bool default (off)
    assert cfg.position == "bottom"  # invalid → fallback
    assert cfg.max_visible == 20  # clamped to hi
    assert OperatorMessagesConfig(max_visible=0).max_visible == 1  # clamped to lo
    assert OperatorMessagesConfig(position="top").position == "top"


@pytest.mark.parametrize(
    "raw,expected",
    [
        (False, False),
        (True, True),
        ("off", False),  # recognised string form
        ("nope", True),  # unrecognised → default (on)
        (None, True),  # wrong type → default
    ],
)
def test_operator_messages_config_route_by_marker_coerces(raw: object, expected: bool) -> None:
    assert OperatorMessagesConfig(route_by_marker=raw).route_by_marker is expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "raw,expected",
    [
        (1.0, 1.0),  # exact valid
        (2.0, 2.0),  # exact valid
        (1.25, 1.25),
        (1.4, 1.5),  # snaps to nearest valid
        (1.1, 1.0),  # snaps down to nearest
        (0.5, 1.0),  # below range → clamped to lo then exact
        (5.0, 2.0),  # above range → clamped to hi then exact
        ("1.75", 1.75),  # numeric string coerces
        ("wide", 1.0),  # non-numeric → default
        (None, 1.0),  # wrong type → default
        (True, 1.0),  # bool → default (1.0, snapped)
    ],
)
def test_operator_messages_config_scale_snaps(raw: object, expected: float) -> None:
    assert OperatorMessagesConfig(scale=raw).scale == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("239.1.2.3", "239.1.2.3"),
        ("224.0.0.1", "224.0.0.1"),
        ("239.255.255.255", "239.255.255.255"),
        ("  239.1.2.3  ", "239.1.2.3"),
        ("", ""),
        ("192.168.1.10", ""),  # not multicast
        ("240.0.0.1", ""),  # above multicast range
        ("223.255.255.255", ""),  # below multicast range
        ("239.1.2", ""),  # too few octets
        ("239.1.2.300", ""),  # octet out of range
        ("224.01.1.1", ""),  # leading-zero octet – ipaddress rejects
        ("239. 1.2.3", ""),  # embedded space – ipaddress rejects
        ("not.an.ip.addr", ""),
        (12345, ""),  # non-string
    ],
)
def test_coerce_multicast_ipv4(raw: object, expected: str) -> None:
    assert _coerce_multicast_ipv4(raw) == expected


def test_osc_config_multicast_field() -> None:
    assert OscConfig(multicast_group="239.10.10.10").multicast_group == "239.10.10.10"
    assert OscConfig(multicast_group="10.0.0.1").multicast_group == ""  # invalid → off
    assert OscConfig().multicast_group == "239.20.20.20"  # default group


def test_config_round_trips_new_sections(tmp_path) -> None:  # noqa: ANN001
    """``[operator_messages]`` + ``[osc] multicast_group`` survive a TOML
    load via ``_SUB_CONFIG_MAP`` / the OscConfig field."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        "\n".join(
            [
                "[osc]",
                'multicast_group = "239.5.6.7"',
                "[operator_messages]",
                "enabled = false",
                'position = "top"',
                "max_visible = 3",
                "route_by_marker = false",
                "scale = 1.5",
            ]
        ),
        encoding="utf-8",
    )
    cfg = load_config(str(cfg_path))
    assert cfg.osc.multicast_group == "239.5.6.7"
    assert cfg.operator_messages.enabled is False
    assert cfg.operator_messages.position == "top"
    assert cfg.operator_messages.max_visible == 3
    assert cfg.operator_messages.route_by_marker is False
    assert cfg.operator_messages.scale == 1.5
