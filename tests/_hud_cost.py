# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Counting what an Operator Screen picture costs, for tests that must not depend on timing.

``CountingContext`` is a real Cairo context on a real surface (the pixels are
real) that also records the groups it pushes with the clip in effect, whose
cost grows with the area they cover. ``count_calls`` counts calls to a module
function or method while a block runs.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import cairo


class CountingContext(cairo.Context):
    """A Cairo context that records each group it pushes with the clip extents in effect."""

    def __init__(self, target: cairo.Surface) -> None:
        super().__init__()
        self.groups: list[tuple[float, float, float, float]] = []

    def __new__(cls, target: cairo.Surface) -> CountingContext:
        return super().__new__(cls, target)

    def push_group(self) -> None:
        self.groups.append(self.clip_extents())
        super().push_group()


def area(extents: tuple[float, float, float, float]) -> float:
    x0, y0, x1, y1 = extents
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


@contextlib.contextmanager
def count_calls(owner: Any, name: str) -> Iterator[list[int]]:
    """Count calls to ``owner.name`` while the block runs; yields a one-item list holding the count."""
    original = owner.__dict__[name] if isinstance(owner, type) else getattr(owner, name)
    count = [0]
    func = original.__func__ if isinstance(original, staticmethod) else original

    def counted(*args: Any, **kwargs: Any) -> Any:
        count[0] += 1
        return func(*args, **kwargs)

    setattr(owner, name, staticmethod(counted) if isinstance(original, staticmethod) else counted)
    try:
        yield count
    finally:
        setattr(owner, name, original)
