# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The web UI's touch remote: browser sessions that hold and drive controlled markers.

Web request threads write sessions here; the frame loop takes one :class:`RemoteFrame` per held
marker each frame (``InputManager.update_touch_remote``) and applies it. While a session holds a
marker, every other input source leaves that marker alone (:func:`holds`).
"""

from __future__ import annotations

import math
import secrets
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

# A held marker stops when no drive message arrived for this long.
DRIVE_STALE_S = 0.25
# A session that sent nothing for this long lets go of its marker and is forgotten.
HOLD_STALE_S = 10.0
# Open sessions kept at most; opening one more forgets the one heard from longest ago.
MAX_SESSIONS = 16
# Queued presses and glide targets per session between two frames.
MAX_PENDING_ACTIONS = 8

OK = "ok"
LOST = "lost"
UNKNOWN = "unknown"

RESET = "reset"
PATHFINDER = "pathfinder"
GLIDE = "glide"
ACTIONS = (RESET, PATHFINDER, GLIDE)


@dataclass(frozen=True)
class RemoteAction:
    """A press from the remote: Reset or Pathfinder, or a glide to ``target`` (X/Y/Z in metres)."""

    kind: str
    target: tuple[float, float, float] | None = None


@dataclass(frozen=True)
class RemoteFrame:
    """What one held marker's remote asks of this frame."""

    marker_id: int
    stick: tuple[float, float]
    z_notches: int
    actions: tuple[RemoteAction, ...]
    # A drive message arrived within ``DRIVE_STALE_S``: the remote is still there.
    live: bool = True


@dataclass
class _Session:
    seen_at: float
    marker_id: int | None = None
    lost: bool = False
    stick: tuple[float, float] = (0.0, 0.0)
    driven_at: float = -math.inf
    z_notches: int = 0
    actions: list[RemoteAction] = field(default_factory=list)


class TouchRemote:
    """Thread-safe registry of remote sessions and the markers they hold. The newest hold wins."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, _Session] = {}
        self._holders: dict[int, str] = {}

    def open(self) -> str:
        """Start a session and return its token."""
        token = secrets.token_urlsafe(16)
        with self._lock:
            while len(self._sessions) >= MAX_SESSIONS:
                oldest = min(self._sessions, key=lambda t: self._sessions[t].seen_at)
                self._forget(oldest)
            self._sessions[token] = _Session(seen_at=self._clock())
        return token

    def close(self, token: str) -> None:
        """End a session, letting go of its marker."""
        with self._lock:
            self._forget(token)

    def hold(self, token: str, marker_id: int) -> str:
        """Take ``marker_id`` for the session, from whoever held it; let go of the one held before."""
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return UNKNOWN
            session.seen_at = self._clock()
            if session.marker_id == marker_id:
                return OK
            self._let_go(session)
            previous = self._holders.get(marker_id)
            if previous is not None:
                taken = self._sessions[previous]
                self._let_go(taken)
                taken.lost = True
            self._holders[marker_id] = token
            session.marker_id = marker_id
            session.lost = False
            return OK

    def drive(self, token: str, marker_id: int, stick: tuple[float, float], z_notches: int) -> str:
        """The stick deflection (each axis -1..1, X stage left, Y upstage) and the Z notches turned since."""
        with self._lock:
            session = self._holding(token, marker_id)
            if not isinstance(session, _Session):
                return session
            sx, sy = stick
            length = math.hypot(sx, sy)
            if length > 1.0:
                sx, sy = sx / length, sy / length
            session.stick = (sx, sy)
            session.driven_at = session.seen_at
            session.z_notches += z_notches
            return OK

    def act(self, token: str, marker_id: int, action: RemoteAction) -> str:
        """Queue a press for the next frame."""
        with self._lock:
            session = self._holding(token, marker_id)
            if not isinstance(session, _Session):
                return session
            if len(session.actions) < MAX_PENDING_ACTIONS:
                session.actions.append(action)
            return OK

    def status(self, token: str) -> dict[str, Any] | None:
        """The marker the session holds and whether another remote took it; ``None`` for an unknown token."""
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            session.seen_at = self._clock()
            return {"held": session.marker_id, "lost": session.lost}

    def held_ids(self) -> frozenset[int]:
        with self._lock:
            return frozenset(self._holders)

    def take(self) -> list[RemoteFrame]:
        """One frame per held marker, draining what its session queued; forgets sessions gone quiet."""
        now = self._clock()
        frames: list[RemoteFrame] = []
        with self._lock:
            for token in [t for t, s in self._sessions.items() if now - s.seen_at > HOLD_STALE_S]:
                self._forget(token)
            for marker_id, token in self._holders.items():
                session = self._sessions[token]
                live = now - session.driven_at <= DRIVE_STALE_S
                stick = session.stick if live else (0.0, 0.0)
                frames.append(RemoteFrame(marker_id, stick, session.z_notches, tuple(session.actions), live))
                session.z_notches = 0
                session.actions.clear()
        return frames

    def release_uncontrolled(self, controlled: Iterable[int]) -> None:
        """Let go of every held marker this station no longer controls."""
        keep = set(controlled)
        with self._lock:
            for marker_id in [m for m in self._holders if m not in keep]:
                self._let_go(self._sessions[self._holders[marker_id]])

    def release_all(self) -> None:
        with self._lock:
            for token in list(self._holders.values()):
                self._let_go(self._sessions[token])

    def _holding(self, token: str, marker_id: int) -> _Session | str:
        session = self._sessions.get(token)
        if session is None:
            return UNKNOWN
        session.seen_at = self._clock()
        if session.marker_id != marker_id:
            return LOST
        return session

    # A session's ``marker_id`` and ``_holders`` always name each other; both change together.
    def _let_go(self, session: _Session) -> None:
        if session.marker_id is not None:
            del self._holders[session.marker_id]
        session.marker_id = None
        session.stick = (0.0, 0.0)
        session.z_notches = 0
        session.actions.clear()

    def _forget(self, token: str) -> None:
        session = self._sessions.pop(token, None)
        if session is not None:
            self._let_go(session)


def holds(app: Any, marker_id: int | None) -> bool:
    """Whether a remote holds ``marker_id``, so other input must leave it alone."""
    remote = getattr(app, "_touch_remote", None)
    return remote is not None and marker_id is not None and marker_id in remote.held_ids()


# Key of the Operator Screen status row shown while the remote holds a marker.
STATUS_FLAG = "touch_remote"


def status_row(app: Any) -> tuple[str, str] | None:
    """The caution row naming the markers the remote holds, else ``None``."""
    remote = getattr(app, "_touch_remote", None)
    held = sorted(remote.held_ids()) if remote is not None else []
    if not held:
        return None
    noun = "marker" if len(held) == 1 else "markers"
    return "caution", f"Web remote holds {noun} {', '.join(str(m) for m in held)}"


def stick_velocity(stick: tuple[float, float], curve: float, dead_zone: float, max_speed: float) -> tuple[float, float]:
    """X/Y velocity in m/s: deflection past the dead zone, raised to ``curve``, up to ``max_speed``."""
    sx, sy = stick
    length = math.hypot(sx, sy)
    if length <= dead_zone:
        return 0.0, 0.0
    shaped = ((min(length, 1.0) - dead_zone) / (1.0 - dead_zone)) ** curve * max_speed
    return sx / length * shaped, sy / length * shaped


def snap_z(z: float, notches: int, step: float) -> float:
    """``z`` moved ``notches`` lines along the step grid; from between two lines the first notch reaches the next."""
    lines = z / step
    # Within a hair of a line counts as on it, so float error never costs a notch.
    start = math.floor(lines + 1e-9) if notches > 0 else math.ceil(lines - 1e-9)
    return (start + notches) * step
