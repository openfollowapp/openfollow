# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""What a settings restore holds until its restart.

A restore writes the config, the marker catalog, the saved templates and the
lens calibrations, then the station restarts on them. Every writer of those
parts writes inside :func:`unless_held`, and the restore takes :data:`GATE`
across its config save, its :func:`hold` and its renames, so a write lands
before the restore or not at all.
"""

from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import Iterator

__all__ = ["GATE", "HeldForRestartError", "hold", "is_held", "release_all", "unless_held"]

GATE = threading.RLock()
_HELD: set[str] = set()


class HeldForRestartError(OSError):
    """A write to a part a restore wrote, before the restart it asked for."""


def hold(*paths: str | os.PathLike[str]) -> None:
    """Refuse every later write to *paths*, and to anything inside one, in this process."""
    with GATE:
        _HELD.update(os.path.realpath(path) for path in paths)


def release_all() -> None:
    """Hold nothing: a new app instance starts on what the restore wrote, the restart it was held for."""
    with GATE:
        _HELD.clear()


def is_held(path: str | os.PathLike[str]) -> bool:
    """Whether *path*, or a folder above it, is held."""
    real = os.path.realpath(path)
    with GATE:
        return any(real == held or real.startswith(held + os.sep) for held in _HELD)


@contextlib.contextmanager
def unless_held(path: str | os.PathLike[str]) -> Iterator[None]:
    """Write *path* inside this; raises :class:`HeldForRestartError` when a restore holds it."""
    with GATE:
        if is_held(path):
            raise HeldForRestartError(f"{path} is held for the restart a restore asked for.")
        yield
