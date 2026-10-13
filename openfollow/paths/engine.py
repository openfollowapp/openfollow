# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Markers locked onto the active paths, and where each movement source may put them.

Runs on the frame loop. Every movement source asks the engine before it writes a
locked marker and writes what it answers, so a locked marker is on its path in
every write and no output ever carries a position off it. A marker pressed onto
a path keeps moving under its input while the path pulls it in, harder and harder
until it is on it. Locks and recording live in memory only.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from openfollow.configuration import PathConfig, PathsConfig
from openfollow.paths.geometry import PathCurve, PathPoint, build_curve
from openfollow.paths.network import PathNetwork, Place

Point = tuple[float, float, float]

# Switching onto a path at another height eases the height over this long instead of jumping.
HEIGHT_BLEND_S = 0.5
# How far inside its path an end looks to see which way it points.
_END_LOOK_M = 0.5


class Press(Enum):
    """What a Pathfinder press did."""

    NONE = "none"
    LOCKED = "locked"
    RELEASED = "released"
    RECORD = "record"


@dataclass
class _Lock:
    name: str
    place: Place
    # While pulled in: where the marker would be without the path, and how long it has been pulled.
    free: Point | None = None
    slide_elapsed: float = 0.0
    # Moved by a reload, not by its marker: the next tick writes it.
    moved: bool = False
    # Height still to ease out after switching onto a path at another height, and how far into that.
    z_shift: float = 0.0
    z_elapsed: float = 0.0


@dataclass(frozen=True)
class PathEnd:
    """An end of an open path: where it is, a point just inside the path from it, and whether it releases."""

    at: Point
    inside: Point
    releases: bool


@dataclass(frozen=True)
class PathDrawing:
    """One path for the Operator Screen: its curve, its ends, and whether a marker is on it."""

    name: str
    curve: list[Point]
    ends: list[PathEnd]
    locked: bool = False


def _curve_of(path: PathConfig) -> PathCurve | None:
    return build_curve([PathPoint(p.x, p.y, p.z, p.radius) for p in path.points], path.closed)


def _ease(u: float) -> float:
    return u * u * (3.0 - 2.0 * u)


class PathEngine:
    """The network of active paths and each locked marker's place on it."""

    def __init__(self) -> None:
        self.enabled = False
        self._network: PathNetwork | None = None
        self._names: list[str] = []
        self._slide_time = 0.5
        self._drawn: list[tuple[list[Point], list[PathEnd]]] = []
        self._locks: dict[int, _Lock] = {}
        self._recording: str | None = None
        self._recorded: tuple[tuple[object, ...], PathDrawing] | None = None
        # Written by the web thread: renames staged before they are saved, and the recording.
        self._renames: dict[str, str] = {}
        self._web_lock = threading.Lock()

    # -- configuration -------------------------------------------------

    def reload(self, cfg: PathsConfig, *, enabled: bool) -> None:
        """Rebuild from ``cfg``; with ``enabled`` False nothing is active and nobody stays locked."""
        self._carry_renames(cfg)
        # Where each locked marker is, or is sliding to, on the paths as they were.
        old = self._network
        spots = (
            {m: lock.free if lock.free is not None else old.point(lock.place) for m, lock in self._locks.items()}
            if old is not None
            else {}
        )
        self.enabled = enabled
        self._slide_time = cfg.slide_time
        curves: list[PathCurve] = []
        releases: list[tuple[bool, bool]] = []
        built: list[PathConfig] = []
        for path in cfg.paths if enabled else []:
            curve = _curve_of(path) if path.active else None
            if curve is not None:
                curves.append(curve)
                releases.append((path.release_at_start, path.release_at_end))
                built.append(path)
        self._network = PathNetwork(curves, releases) if curves else None
        self._names = [path.name for path in built]
        # Drawn from the network's curves: an end carried on to a corner is part of its path.
        network_curves = self._network.curves if self._network is not None else []
        self._drawn = [
            (_line(curve), path_ends(path, curve)) for path, curve in zip(built, network_curves, strict=True)
        ]
        recording = self._recording
        if recording is not None and (not enabled or cfg.find(recording) is None):
            self._replace_recording(recording, None)
        for marker_id, lock in list(self._locks.items()):
            index = self._index(lock.name)
            if index is None or self._network is None or marker_id not in spots:
                del self._locks[marker_id]
                continue
            # The path may have changed under the marker: put it back on the edited curve.
            x, y, _ = spots[marker_id]
            lock.place = Place(index, self._network.curves[index].nearest(x, y)[0])
            lock.moved = True

    def _index(self, name: str) -> int | None:
        wanted = name.lower()
        return next((i for i, n in enumerate(self._names) if n.lower() == wanted), None)

    # -- recording -----------------------------------------------------

    @property
    def recording(self) -> str | None:
        return self._recording

    def record(self, name: str | None, cfg: PathsConfig) -> bool:
        """Record into the path called ``name`` (``None`` stops); False when there is no such path."""
        path = cfg.find(name) if name is not None else None
        if name is not None and (path is None or not self.enabled):
            return False
        with self._web_lock:
            self._recording = None if path is None else path.name
        return True

    def _replace_recording(self, was: str, now: str | None) -> None:
        """Change the recording the frame loop read as ``was``, unless the web thread changed it since."""
        with self._web_lock:
            if self._recording == was:
                self._recording = now

    # -- locks ---------------------------------------------------------

    def is_locked(self, marker_id: int) -> bool:
        return marker_id in self._locks

    def locked_path(self, marker_id: int) -> str | None:
        lock = self._locks.get(marker_id)
        return None if lock is None else lock.name

    def press(self, marker_id: int, pos: Point) -> Press:
        """A Pathfinder press for ``marker_id``, standing at ``pos``."""
        if not self.enabled:
            return Press.NONE
        if self._recording is not None:
            return Press.RECORD
        if self._locks.pop(marker_id, None) is not None:
            return Press.RELEASED
        if self._network is None:
            return Press.NONE
        place = self._network.nearest(pos[0], pos[1])
        if place is None:  # pragma: no cover - a network always holds a curve
            return Press.NONE
        self._start(marker_id, place, pos)
        return Press.LOCKED

    def lock_onto(self, marker_id: int, name: str, pos: Point) -> bool:
        """Slide ``marker_id`` onto the active path called ``name``; False when there is none."""
        index = self._index(name)
        if index is None or self._network is None:
            return False
        place = self._network.nearest(pos[0], pos[1], among=[index])
        if place is None:  # pragma: no cover - one index always answers
            return False
        self._start(marker_id, place, pos)
        return True

    def _start(self, marker_id: int, place: Place, pos: Point) -> None:
        self._locks[marker_id] = _Lock(self._names[place.path], place, free=pos)

    def release(self, marker_id: int) -> None:
        self._locks.pop(marker_id, None)

    def release_all(self, keep: Sequence[int] = ()) -> None:
        """Release every marker but those in ``keep``."""
        for marker_id in [m for m in self._locks if m not in keep]:
            del self._locks[marker_id]

    # -- movement ------------------------------------------------------

    def tick(self, dt: float) -> dict[int, Point]:
        """Advance every slide; the positions of markers the engine itself moved this frame."""
        out: dict[int, Point] = {}
        for marker_id, lock in self._locks.items():
            # A lock only ever lives on a network: a reload without one releases everybody.
            assert self._network is not None
            if lock.free is not None:
                lock.slide_elapsed += dt
                out[marker_id] = self._pulled(lock)
                if lock.slide_elapsed >= self._slide_time:
                    lock.free = None
            elif lock.z_shift:
                lock.z_elapsed += dt
                out[marker_id] = self._at(lock)
                if lock.z_elapsed >= HEIGHT_BLEND_S:
                    lock.z_shift = 0.0
            elif lock.moved:
                out[marker_id] = self._at(lock)
            lock.moved = False
        return out

    def _at(self, lock: _Lock) -> Point:
        """Where a landed lock puts its marker: its place, with what is left of a height switch."""
        assert self._network is not None
        x, y, z = self._network.point(lock.place)
        return x, y, z + self._height_left(lock)

    @staticmethod
    def _height_left(lock: _Lock) -> float:
        return lock.z_shift * (1.0 - _ease(min(lock.z_elapsed / HEIGHT_BLEND_S, 1.0)))

    def _pulled(self, lock: _Lock) -> Point:
        """Between where the marker would be and the nearest point of its path, closer the longer it is pulled."""
        assert self._network is not None and lock.free is not None
        return self._pull(lock, lock.free, self._network.point(lock.place))

    def _pull(self, lock: _Lock, start: Point, target: Point) -> Point:
        """``start`` moved towards ``target`` as far as ``lock``'s pull has got."""
        u = 1.0 if self._slide_time <= 0.0 else min(lock.slide_elapsed / self._slide_time, 1.0)
        k = _ease(u)
        x0, y0, z0 = start
        return x0 + (target[0] - x0) * k, y0 + (target[1] - y0) * k, z0 + (target[2] - z0) * k

    def _move_free(self, lock: _Lock, free: Point) -> Point:
        """Carry a pulled marker's own movement on, and aim the pull at the point of the path nearest it."""
        assert self._network is not None
        lock.free = free
        s, _ = self._network.curves[lock.place.path].nearest(free[0], free[1])
        lock.place = Place(lock.place.path, s)
        return self._pulled(lock)

    def move(self, marker_id: int, vx: float, vy: float, dt: float, vz: float = 0.0) -> Point | None:
        """Where a push of ``(vx, vy, vz)`` m/s for ``dt`` puts a locked marker; ``None`` when it is not locked.

        Once on the path only the push along it counts and the height is the path's.
        """
        lock = self._locks.get(marker_id)
        if lock is None or self._network is None:
            return None
        if lock.free is not None:
            x, y, z = lock.free
            return self._move_free(lock, (x + vx * dt, y + vy * dt, z + vz * dt))
        step = self._network.walk(lock.place, vx, vy, dt)
        self._set_place(lock, step.place)
        if step.released:
            del self._locks[marker_id]
        return self._at(lock)

    def project(self, marker_id: int, x: float, y: float) -> Point | None:
        """Where an absolute position lands for a locked marker: the nearest point it can reach."""
        lock = self._locks.get(marker_id)
        if lock is None or self._network is None:
            return None
        if lock.free is not None:
            return self._move_free(lock, (x, y, lock.free[2]))
        place = self._network.nearest(x, y, among=self._network.connected(lock.place.path))
        assert place is not None
        self._set_place(lock, place)
        return self._at(lock)

    def constrain(self, marker_id: int, x: float, y: float) -> Point | None:
        """Where ``project`` would put ``(x, y)`` for a locked marker, leaving its lock where it is.

        For a second position that follows the marker, such as the AI Assisted output beside its anchor.
        """
        lock = self._locks.get(marker_id)
        if lock is None or self._network is None:
            return None
        if lock.free is not None:
            curve = self._network.curves[lock.place.path]
            return self._pull(lock, (x, y, lock.free[2]), curve.point(curve.nearest(x, y)[0]))
        place = self._network.nearest(x, y, among=self._network.connected(lock.place.path))
        assert place is not None
        px, py, pz = self._network.point(place)
        return px, py, pz + self._height_left(lock)

    def _set_place(self, lock: _Lock, place: Place) -> None:
        if place.path != lock.place.path:
            # Onto another path: carry the height across and let it ease to the new path's.
            assert self._network is not None
            lock.z_shift, lock.z_elapsed = self._at(lock)[2] - self._network.point(place)[2], 0.0
        lock.place = place
        lock.name = self._names[place.path]

    # -- drawing -------------------------------------------------------

    def drawings(self, cfg: PathsConfig) -> list[PathDrawing]:
        """The paths the Operator Screen draws: every active one, and the one being recorded."""
        if not self.enabled:
            return []
        locked = {lock.name.lower() for lock in self._locks.values()}
        out = [
            PathDrawing(name, line, ends, name.lower() in locked)
            for name, (line, ends) in zip(self._names, self._drawn, strict=True)
        ]
        recording = self._recording
        recorded = cfg.find(recording) if recording is not None else None
        if recorded is not None and self._index(recorded.name) is None:
            out.append(self._recorded_drawing(recorded))
        return out

    def _recorded_drawing(self, path: PathConfig) -> PathDrawing:
        # Recording an inactive path changes it point by point; build it only when it changed.
        key = (path.name, path.closed, tuple((p.x, p.y, p.z, p.radius) for p in path.points))
        if self._recorded is None or self._recorded[0] != key:
            curve = _curve_of(path)
            line = [(p.x, p.y, p.z) for p in path.points] if curve is None else _line(curve)
            self._recorded = (key, PathDrawing(path.name, line, path_ends(path, curve)))
        return self._recorded[1]

    # -- renames -------------------------------------------------------

    def stage_rename(self, old: str, new: str) -> None:
        """Move locks and recording from path ``old`` to ``new`` at the first reload whose paths hold ``new``.

        Safe from another thread. Staged before the rename is saved, so the reload that reads it always finds it.
        A rename is kept against the name the path had at the last reload, so names swapped or reused before
        the reload still follow their paths.
        """
        wanted = old.lower()
        with self._web_lock:
            key = next((k for k, now in self._renames.items() if now.lower() == wanted), None)
            if key is None:
                if wanted in self._renames:
                    return  # made since the last reload under a name another path gave up: nothing follows it
                key = wanted
            self._renames[key] = new

    def _carry_renames(self, cfg: PathsConfig) -> None:
        with self._web_lock:
            landed = {key: now for key, now in self._renames.items() if cfg.find(now) is not None}
            for key in landed:
                del self._renames[key]
        taken = {now.lower() for now in landed.values()}
        # An old name still in the paths is another path's new name, or a rename whose save was lost.
        moved = {key: now for key, now in landed.items() if key in taken or cfg.find(key) is None}
        for lock in self._locks.values():
            lock.name = moved.get(lock.name.lower(), lock.name)
        recording = self._recording
        if recording is not None and recording.lower() in moved:
            self._replace_recording(recording, moved[recording.lower()])

    def web_state(self) -> tuple[str | None, dict[int, str]]:
        """The recording and each locked marker's path, named as the paths file has them; safe from another thread."""
        with self._web_lock:
            renames = dict(self._renames)
            recording = self._recording

        def named(name: str) -> str:
            return renames.get(name.lower(), name)

        locks = {marker_id: named(lock.name) for marker_id, lock in tuple(self._locks.items())}
        return (None if recording is None else named(recording)), locks


def path_ends(path: PathConfig, curve: PathCurve | None) -> list[PathEnd]:
    """An open path's two ends; a closed one has none, and a corner shows as its curve.

    A path of one point (being recorded) is that point, drawn as an end that holds.
    """
    if path.closed or not path.points:
        return []
    if curve is None:
        first = path.points[0]
        at = (first.x, first.y, first.z)
        return [PathEnd(at, at, False)]
    look = min(_END_LOOK_M, curve.length / 2.0)
    return [
        PathEnd(curve.point(0.0), curve.point(look), path.release_at_start),
        PathEnd(curve.point(curve.length), curve.point(curve.length - look), path.release_at_end),
    ]


def _line(curve: PathCurve) -> list[Point]:
    return curve.drawn()
