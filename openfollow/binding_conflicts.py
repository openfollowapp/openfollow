# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""One input, one action: settle bindings that share an input within a group.

Stdlib-only so :mod:`openfollow.configuration` can import it at load time.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

V = TypeVar("V", bound=Hashable)


@dataclass(frozen=True)
class BindingMove(Generic[V]):
    """``field`` gave ``value`` up to ``kept_by``."""

    field: str
    kept_by: str
    value: V


def settle_duplicates(
    values: Mapping[str, V],
    order: Sequence[str],
    *,
    unbound: V,
    prefer: Iterable[str] = (),
) -> list[BindingMove[V]]:
    """Return the moves that leave each bound value on exactly one field of ``order``.

    The first holder keeps a value: the fields in ``prefer`` rank ahead of the
    rest, and ``order`` (the form order) ranks within each. Does not mutate.
    """
    preferred = set(prefer)
    ranked = [f for f in order if f in preferred] + [f for f in order if f not in preferred]
    holder: dict[V, str] = {}
    moves: list[BindingMove[V]] = []
    for name in ranked:
        value = values[name]
        if value == unbound:
            continue
        if value in holder:
            moves.append(BindingMove(name, holder[value], value))
        else:
            holder[value] = name
    return moves
