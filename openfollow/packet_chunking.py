# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Splits an output protocol's payload list across datagrams that fit the MTU.

Every marker-carrying output (PSN, OTP, RTTrPM) grows its datagram with the
marker count, and each has its own on-the-wire mechanism for spreading one
logical frame over several datagrams. What they share is the question of where
to cut, which is what this module answers: pack items greedily into chunks whose
encoded size stays inside ``MAX_DATAGRAM_BYTES`` wherever that is possible at
all. An item too large to fit even alone is handed back in a chunk of its own,
for the protocol layer to decide what an unsendable item means.

Sizes are measured through the caller's real encoder rather than derived from a
per-item constant, so a protocol field added later moves the split instead of
silently pushing the datagram over the limit.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

T = TypeVar("T")

# A 1500-octet Ethernet MTU less the 20-octet IPv4 and 8-octet UDP headers.
# This is the fragmentation threshold, which bites before any protocol's own
# packet-size limit: an over-MTU datagram is split by IP and usually still
# arrives on a quiet LAN, so the failure surfaces only under show-network load.
MAX_DATAGRAM_BYTES = 1472


def chunk_to_datagrams(
    items: Sequence[T],
    encoded_size: Callable[[Sequence[T]], int],
    budget: int = MAX_DATAGRAM_BYTES,
) -> list[Sequence[T]]:
    """Split *items* into chunks that each encode to at most *budget* bytes.

    ``encoded_size`` must return the datagram size for a given item subset. It
    is called once per item plus once for the empty list, and the split assumes
    per-item cost is additive on top of that fixed overhead – true of every
    length-prefixed protocol here, where a packet is a header followed by
    concatenated per-item records.

    An item too large to ever fit is emitted alone in an oversize chunk rather
    than dropped or spun on: the caller's protocol layer decides what an
    unsendable single item means.
    """
    if not items:
        return []
    overhead = encoded_size(())
    sizes = [encoded_size((item,)) - overhead for item in items]
    chunks: list[Sequence[T]] = []
    start = 0
    used = overhead
    for index, size in enumerate(sizes):
        if index > start and used + size > budget:
            chunks.append(items[start:index])
            start = index
            used = overhead
        used += size
    chunks.append(items[start:])
    return chunks
