# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Lifecycle helpers for the per-marker state the frame loop carries.

Detection pin states, assist anchors and broadcast velocity estimates all live
in a ``dict`` keyed by marker id: created the first frame a marker needs one,
dropped when it leaves the driven set. Both halves of that lifecycle are here so
the three call sites share one implementation.
"""

from __future__ import annotations

from collections.abc import Callable, Container, Sequence
from typing import TypeVar

K = TypeVar("K")
V = TypeVar("V")


def get_or_create(mapping: dict[K, V], key: K, factory: Callable[[], V]) -> V:
    """Return ``mapping[key]``, creating it from *factory* on first use."""
    try:
        return mapping[key]
    except KeyError:
        value = mapping[key] = factory()
        return value


def prune_to_keep(mapping: dict[K, V], keep: Container[K]) -> Sequence[tuple[K, V]]:
    """Drop every entry whose key is not in *keep*, in place.

    Returns the dropped ``(key, value)`` pairs so a caller can release whatever
    that state still holds elsewhere. Called once per frame per map, so the
    doomed entries are collected only when there are any: the steady state
    (nothing to drop) allocates nothing.
    """
    stale: list[tuple[K, V]] | None = None
    for key, value in mapping.items():
        if key not in keep:
            if stale is None:
                stale = []
            stale.append((key, value))
    if stale is None:
        return ()
    for key, _ in stale:
        del mapping[key]
    return stale
