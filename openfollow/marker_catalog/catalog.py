# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Marker catalog: per-marker id/name/color, persisted in ``markers.toml``.

The catalog is the shared source of truth for marker identity. Per-
station selection (which catalog entries this station controls/views)
lives in ``config.toml`` and is intentionally NOT covered here.

Conflict model: per-entry last-write-wins on ``updated_at``. Deletes
are tombstones so a late-arriving peer can't reincarnate them.
"""

from __future__ import annotations

import logging
import math
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import tomli_w

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - python 3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]

from openfollow.configuration import _coerce_hex_color

logger = logging.getLogger(__name__)


_DEFAULT_COLOR = "#ffffff"


@dataclass
class MarkerEntry:
    """A single catalog entry. ``tombstone=True`` marks a deletion."""

    id: int
    name: str
    color: str
    updated_at: float
    tombstone: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.id, int) or isinstance(self.id, bool):
            raise ValueError("MarkerEntry.id must be int")
        if self.id < 1:
            raise ValueError("MarkerEntry.id must be >= 1")
        if not isinstance(self.name, str):
            self.name = ""
        self.color = _coerce_hex_color(self.color, _DEFAULT_COLOR)
        try:
            self.updated_at = float(self.updated_at)
        except (TypeError, ValueError):
            self.updated_at = 0.0
        # NaN/inf in timestamps breaks LWW merge; normalize to 0.0.
        if not math.isfinite(self.updated_at):
            self.updated_at = 0.0
        # Defence-in-depth: bool("false") is True; require real bool.
        if not isinstance(self.tombstone, bool):
            self.tombstone = False


class MarkerCatalog:
    """Thread-safe collection of :class:`MarkerEntry` with LWW merge.

    Tombstones are retained to prevent reincarnation of deleted entries.
    """

    def __init__(self) -> None:
        self._entries: dict[int, MarkerEntry] = {}
        self._lock = threading.Lock()
        # Serialises save_catalog calls to prevent writer interleaving.
        self._save_lock = threading.Lock()

    def get(self, marker_id: int) -> MarkerEntry | None:
        """Return the live (non-tombstoned) entry for ``marker_id``, or None."""
        with self._lock:
            entry = self._entries.get(marker_id)
        if entry is None or entry.tombstone:
            return None
        return entry

    def get_any(self, marker_id: int) -> MarkerEntry | None:
        """Return the entry for ``marker_id`` including tombstones."""
        with self._lock:
            return self._entries.get(marker_id)

    def live_entries(self) -> list[MarkerEntry]:
        """Return id-sorted, non-tombstoned entries."""
        with self._lock:
            entries = [e for e in self._entries.values() if not e.tombstone]
        entries.sort(key=lambda e: e.id)
        return entries

    def all_entries(self) -> list[MarkerEntry]:
        """Return id-sorted entries including tombstones – for serialisation."""
        with self._lock:
            entries = list(self._entries.values())
        entries.sort(key=lambda e: e.id)
        return entries

    def upsert(
        self,
        marker_id: int,
        name: str,
        color: str,
        *,
        updated_at: float | None = None,
    ) -> MarkerEntry:
        """Create or update an entry. Stamps ``updated_at`` to wall time.

        Clears any tombstone (re-adding an id resurrects it locally –
        peers will see this as a newer LWW write and resurrect too).
        """
        if marker_id < 1:
            raise ValueError("marker id must be >= 1")
        ts = updated_at if updated_at is not None else time.time()
        entry = MarkerEntry(
            id=marker_id,
            name=name,
            color=color,
            updated_at=ts,
            tombstone=False,
        )
        with self._lock:
            self._entries[marker_id] = entry
        return entry

    def restore_entry(
        self,
        marker_id: int,
        entry: MarkerEntry | None,
    ) -> None:
        """Atomically reinstall or remove an entry to roll back a failed save.

        ``entry=None`` means "nothing was here before" and pops any current
        value at ``marker_id``.
        """
        with self._lock:
            if entry is None:
                self._entries.pop(marker_id, None)
            else:
                self._entries[marker_id] = entry

    def delete(self, marker_id: int) -> MarkerEntry | None:
        """Tombstone an entry. Returns the new tombstone, or None if unknown."""
        ts = time.time()
        with self._lock:
            existing = self._entries.get(marker_id)
            if existing is None:
                # Don't materialise a tombstone for an id we've never
                # heard of – that would let any peer flood us with
                # deletes for arbitrary ids and grow memory without
                # bound. A peer's later upsert of that id will still
                # win normally.
                return None
            tomb = MarkerEntry(
                id=marker_id,
                name=existing.name,
                color=existing.color,
                updated_at=ts,
                tombstone=True,
            )
            self._entries[marker_id] = tomb
        return tomb

    def merge_entry(self, remote: MarkerEntry) -> bool:
        """LWW merge a remote entry into the local catalog.

        Returns ``True`` if the merge changed local state (entry added,
        replaced, or tombstoned).
        """
        with self._lock:
            existing = self._entries.get(remote.id)
            if existing is None:
                if remote.tombstone:
                    # Don't materialise a tombstone for an id we've never
                    # heard of – mirrors ``delete()``. Otherwise a peer can
                    # flood us with tombstones for arbitrary ids (each kept
                    # permanently, re-saved to markers.toml and re-broadcast
                    # every heartbeat) and grow ``_entries`` without bound.
                    # A tombstone only matters when there's a prior live
                    # entry to suppress; a later live upsert of that id still
                    # wins under LWW.
                    return False
                self._entries[remote.id] = remote
                return True
            if remote.updated_at <= existing.updated_at:
                return False
            self._entries[remote.id] = remote
            return True

    def next_free_id(self) -> int:
        """Lowest int >= 1 not in _entries (including tombstones)."""
        with self._lock:
            used = set(self._entries.keys())
        i = 1
        while i in used:
            i += 1
        return i

    def __len__(self) -> int:
        with self._lock:
            return sum(1 for e in self._entries.values() if not e.tombstone)


def load_catalog(path: str) -> MarkerCatalog:
    """Load catalog from markers.toml; returns empty on file-not-found."""
    catalog = MarkerCatalog()
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        return catalog
    except Exception:
        logger.exception("Failed to parse marker catalog %s – using empty.", path)
        return catalog

    raw_entries = data.get("marker", [])
    if not isinstance(raw_entries, list):
        logger.warning("markers.toml: 'marker' must be array-of-tables, got %r", type(raw_entries))
        return catalog

    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        raw_id = raw.get("id", 0)
        # Reject bool explicitly; int(True) == 1 would overwrite marker 1.
        if isinstance(raw_id, bool):
            logger.warning("markers.toml: dropping entry with bool id=%r", raw_id)
            continue
        try:
            mid = int(raw_id)
        except (TypeError, ValueError):
            continue
        if mid < 1:
            logger.warning("markers.toml: dropping entry with id=%r (must be >= 1)", raw_id)
            continue
        # Require real bool; bool("false") is True in Python.
        tombstone_raw = raw.get("tombstone", False)
        tombstone = tombstone_raw if isinstance(tombstone_raw, bool) else False
        # Leave name as-is; MarkerEntry.__post_init__ normalises it.
        try:
            entry = MarkerEntry(
                id=mid,
                name=raw.get("name", ""),
                color=str(raw.get("color", _DEFAULT_COLOR)),
                updated_at=float(raw.get("updated_at", 0.0)),
                tombstone=tombstone,
            )
        except ValueError:
            logger.warning("markers.toml: dropping invalid entry %r", raw)
            continue
        catalog._entries[entry.id] = entry
    return catalog


def save_catalog(catalog: MarkerCatalog, path: str) -> None:
    """Atomically write catalog (including tombstones) via tempfile+rename."""
    with catalog._save_lock:
        entries = catalog.all_entries()
        data: dict[str, Any] = {
            "marker": [asdict(e) for e in entries],
        }
        target = Path(path)
        directory = target.parent if str(target.parent) else Path(".")
        fd, tmp_path = tempfile.mkstemp(
            prefix=target.name + ".",
            suffix=".tmp",
            dir=str(directory),
        )
        try:
            with os.fdopen(fd, "wb") as f:
                tomli_w.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
