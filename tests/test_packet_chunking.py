# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The byte-budget split shared by the PSN, OTP and RTTrPM send paths.

Sizes come from the caller's own encoder, so the properties that matter are
about the split itself: it loses nothing, it keeps order, it stays inside the
budget wherever that is possible at all, and it terminates on an item that never
fits. The protocol wiring is covered in ``test_output_packet_splitting``.
"""

from __future__ import annotations

import pytest

from openfollow.packet_chunking import MAX_DATAGRAM_BYTES, chunk_to_datagrams

pytestmark = pytest.mark.unit

_OVERHEAD = 10


def _sizer(per_item: dict[str, int]):
    """Encoder stand-in: a fixed overhead plus each item's declared cost."""

    def encoded_size(items) -> int:
        return _OVERHEAD + sum(per_item[item] for item in items)

    return encoded_size


def _uniform(count: int, size: int) -> tuple[list[str], dict[str, int]]:
    items = [f"i{index}" for index in range(count)]
    return items, dict.fromkeys(items, size)


def test_an_empty_list_produces_no_datagrams() -> None:
    assert chunk_to_datagrams([], _sizer({})) == []


def test_a_set_that_fits_stays_in_one_datagram() -> None:
    items, sizes = _uniform(5, 20)
    assert [list(chunk) for chunk in chunk_to_datagrams(items, _sizer(sizes), 200)] == [items]


def test_a_set_at_the_exact_budget_stays_in_one_datagram() -> None:
    """Off-by-one here silently halves the payload of every full datagram."""
    items, sizes = _uniform(4, 25)  # 10 + 4*25 == 110
    assert len(chunk_to_datagrams(items, _sizer(sizes), 110)) == 1
    assert len(chunk_to_datagrams(items, _sizer(sizes), 109)) == 2


@pytest.mark.parametrize("count", [2, 3, 7, 33, 100])
def test_every_item_is_carried_exactly_once_and_in_order(count: int) -> None:
    items, sizes = _uniform(count, 40)
    chunks = chunk_to_datagrams(items, _sizer(sizes), 130)  # 3 items per chunk
    assert [item for chunk in chunks for item in chunk] == items


@pytest.mark.parametrize("budget", [50, 90, 130, 400])
def test_no_chunk_exceeds_the_budget(budget: int) -> None:
    items, sizes = _uniform(40, 37)
    encoded_size = _sizer(sizes)
    for chunk in chunk_to_datagrams(items, encoded_size, budget):
        assert encoded_size(chunk) <= budget


def test_items_of_differing_size_are_packed_to_the_budget() -> None:
    items = ["small", "large", "small2", "large2"]
    sizes = {"small": 10, "large": 80, "small2": 10, "large2": 80}
    chunks = [list(chunk) for chunk in chunk_to_datagrams(items, _sizer(sizes), 100)]
    assert chunks == [["small", "large"], ["small2", "large2"]]


def test_an_item_too_large_for_any_datagram_is_emitted_alone() -> None:
    """The alternative is dropping it or spinning forever; the protocol layer
    decides what an unsendable item means, so it has to arrive there."""
    items = ["huge", "a", "b"]
    sizes = {"huge": 5_000, "a": 10, "b": 10}
    chunks = [list(chunk) for chunk in chunk_to_datagrams(items, _sizer(sizes), 100)]
    assert chunks == [["huge"], ["a", "b"]]


def test_consecutive_oversize_items_each_get_their_own_datagram() -> None:
    items, sizes = _uniform(3, 5_000)
    assert [list(chunk) for chunk in chunk_to_datagrams(items, _sizer(sizes), 100)] == [[i] for i in items]


def test_the_encoder_is_called_once_per_item_plus_once() -> None:
    """Sizing by re-encoding each candidate chunk would be quadratic, which at
    the transform fps is the difference between free and not."""
    items, sizes = _uniform(50, 40)
    calls = 0
    inner = _sizer(sizes)

    def counting(chunk) -> int:
        nonlocal calls
        calls += 1
        return inner(chunk)

    chunk_to_datagrams(items, counting, 130)
    assert calls == len(items) + 1


def test_the_default_budget_is_the_udp_payload_of_an_ethernet_mtu() -> None:
    """1500 less the 20-octet IPv4 and 8-octet UDP headers. Fragmentation
    starts here, below every one of these protocols' own packet limits."""
    assert MAX_DATAGRAM_BYTES == 1472
    items, sizes = _uniform(2, 1_000)
    assert len(chunk_to_datagrams(items, _sizer(sizes))) == 2
