# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Counting what an Operator Screen picture costs, for tests that must not depend on timing.

``count_calls`` counts calls to a module function or method while a block runs.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any


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
