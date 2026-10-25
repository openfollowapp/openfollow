# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Configuration model, TOML I/O, and runtime hot-reload for OpenFollow."""

from __future__ import annotations

import contextlib
import copy
import errno
import ipaddress
import logging
import math
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

import tomli_w
import tomllib

from openfollow.binding_conflicts import BindingMove, settle_duplicates
from openfollow.keyboard_layout import LAYOUT_RE as XKB_LAYOUT_RE
from openfollow.keyboard_layout import VARIANT_RE as XKB_VARIANT_RE
from openfollow.lens_model import C_BOUND, pull_back_to_valid
from openfollow.net_adapters import normalize_labels
from openfollow.station_fqdn import normalize_fqdn
from openfollow.text_hygiene import CONTROL_CHARS_RE
from openfollow.units import UnitSystem
from openfollow.uri_redaction import redact_uris_in_text

if TYPE_CHECKING:
    from openfollow.app import OpenFollowApp

logger = logging.getLogger(__name__)


# Serializes load→mutate→``save_config`` spans against the on-disk config.
# ``save_config`` overwrites the whole file, so two interleaving spans would
# have the later save clobber the earlier. Held by the off-GTK-thread writers
# that run concurrently (the threaded WSGI request handlers and the
# marker-catalog sync receiver) and by main-loop writers that write only their
# own change (``save_config_change``, ``save_config_fields``, the move-speed
# flush). The receiver's source pick does NOT take it.
config_write_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Configuration Models
# ---------------------------------------------------------------------------


# --- Input-normalisation helpers ----------------------------------------
# Dataclasses don't validate field types. Hand-edited config.toml and crafted
# web POSTs land in a dataclass with whatever type TOML decoded or the form
# parser produced. These helpers normalise once in ``__post_init__`` so
# downstream consumers can assume clean data.

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _coerce_float(
    value: Any,
    default: float,
    *,
    lo: float | None = None,
    hi: float | None = None,
) -> float:
    """Coerce ``value`` to float, clamping to [lo, hi]; ``default`` on bad input.

    Catches ``OverflowError`` (``float`` of an arbitrary-precision TOML int can
    raise) and rejects non-finite results (``inf``/``nan``) to ``default`` –
    the contract is a finite number, since ``int(inf)``/``int(nan)`` downstream
    raise even though ``float("inf")`` slips through unbounded clamping.
    """
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(out):
        return default
    if lo is not None:
        out = max(lo, out)
    if hi is not None:
        out = min(hi, out)
    return out


def _coerce_int(
    value: Any,
    default: int,
    *,
    lo: int | None = None,
    hi: int | None = None,
) -> int:
    """Coerce ``value`` to int, clamping to [lo, hi]; ``default`` on bad input.

    Rejects ``bool`` explicitly (it is an ``int`` subclass, so ``true`` would
    become ``1``) and catches ``OverflowError`` (``int(float('inf'))`` raises).
    """
    if isinstance(value, bool):
        return default
    try:
        out = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    # Every current caller passes ``lo``.
    if lo is not None:  # pragma: no branch
        out = max(lo, out)
    if hi is not None:
        out = min(hi, out)
    return out


def _not_bool(value: Any) -> Any:
    """``None`` for a bool, so a coercer falls back to its default; ``value`` otherwise."""
    return None if isinstance(value, bool) else value


def _coerce_hex_color(value: Any, default: str) -> str:
    """Return a lowercase ``#rrggbb`` string, or ``default`` on invalid input."""
    if isinstance(value, str) and _HEX_COLOR_RE.match(value):
        return value.lower()
    return default


def _field_default(instance: Any, name: str) -> Any:
    """Declared default of a dataclass field, for use as a coercion fallback.

    Lets ``__post_init__`` read the per-field defaults straight off the field
    declarations instead of a parallel table that could drift.
    """
    return instance.__dataclass_fields__[name].default


# Which binding repairs have been logged, so a config parsed on every request says each once per process.
_LOGGED_BINDINGS: set[tuple[str, str, Any]] = set()


def _warn_binding_once(section: str, field: str, value: Any, message: str, *args: Any) -> None:
    if (section, field, value) in _LOGGED_BINDINGS:
        return
    _LOGGED_BINDINGS.add((section, field, value))
    logger.warning("[%s] %s", section, message % args)


def _log_settled_binding(section: str, move: BindingMove[Any]) -> None:
    _warn_binding_once(
        section,
        move.field,
        move.value,
        "%s and %s are both bound to %r; keeping %s, unbinding %s",
        move.kept_by,
        move.field,
        move.value,
        move.kept_by,
        move.field,
    )


def _coerce_multicast_ipv4(value: Any, default: str = "") -> str:
    """Return a validated IPv4 multicast address, or ``default``.

    Empty string means off. Accepts only an IPv4 multicast address
    (``224.0.0.0``–``239.255.255.255``); anything else falls back to
    ``default``. Uses :mod:`ipaddress`, which rejects leading-zero octets
    and embedded whitespace.
    """
    if not isinstance(value, str):
        return default
    addr = value.strip()
    if not addr:
        return ""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return default
    if not isinstance(ip, ipaddress.IPv4Address) or not ip.is_multicast:
        return default
    return addr


def is_ipv4_literal(value: str) -> bool:
    """Whether *value* is an IPv4 address in the dotted form a socket reports a sender in."""
    try:
        ipaddress.IPv4Address(value)
    except ValueError:
        return False
    return True


def _coerce_optional_marker_id(value: Any) -> int | None:
    """Coerce an OSC transmitter row's ``marker_id`` field.

    Distinguishes "no default marker set" (``None``) from "explicit zero"
    (``0``): a ``None`` row using a default-marker placeholder skips with
    "no default marker configured", whereas ``0`` looks up marker 0.
    Empty/whitespace strings collapse to ``None``. ``bool`` is rejected (so
    ``True`` doesn't become marker 1); non-coercible junk also collapses to
    ``None`` so a skip surfaces rather than firing against the wrong marker.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        # Reject all floats: ``int(1.5)`` truncates to marker 1 instead of
        # collapsing to ``None``. The schema is ``int | None``. Also guards
        # ``inf``/``nan``, which would otherwise reach the truncation below.
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        out = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if out < 0:
        return None
    return out


# Marker-token grammar for an OSC transmitter row's ``markers`` field. Each
# token is one of: a non-negative integer marker id, a controller alias
# ``cN`` (1-based, no leading zero – mirrors the ``:cN`` reference grammar in
# ``osc/template.py``), or the keyword ``all`` (every id in
# ``controlled_marker_ids``). ``all`` and the aliases resolve dynamically, so
# they're stored verbatim and only mapped to concrete ids by the transmitter
# manager at render time.
MARKER_TOKEN_ALL = "all"
_CONTROLLER_ALIAS_RE = re.compile(r"^c[1-9][0-9]*$")


def _canonical_marker_token(raw: Any) -> str | None:
    """Canonicalise one ``markers`` entry, or ``None`` when invalid.

    Accepts an int (non-negative) or a string token (``"all"`` / ``"cN"`` /
    a decimal id). Invalid entries – floats, ``True``, negatives, ``c0`` /
    ``c01``, junk – return ``None`` so the caller drops them (the field
    ignores invalid ids by spec).
    """
    if isinstance(raw, str):
        s = raw.strip().lower()
        if not s:
            return None
        if s == MARKER_TOKEN_ALL:
            return MARKER_TOKEN_ALL
        if _CONTROLLER_ALIAS_RE.match(s):
            return s
        # Fall through to the numeric coercer (rejects bool / float / junk).
        mid = _coerce_optional_marker_id(s)
        return None if mid is None else str(mid)
    mid = _coerce_optional_marker_id(raw)
    return None if mid is None else str(mid)


def _marker_token_sort_key(token: str) -> tuple[int, int]:
    """Display order: numeric ids ascending, then controller aliases by
    index. ``all`` collapses the list so it never reaches here."""
    if _CONTROLLER_ALIAS_RE.match(token):
        return (1, int(token[1:]))
    return (0, int(token))


def _coerce_marker_tokens(value: Any) -> list[str]:
    """Normalise an OSC transmitter row's ``markers`` field to a sorted,
    de-duplicated list of canonical tokens.

    Accepts a list (TOML array), a comma-separated string (hand-edited TOML
    / web form), or a bare int (legacy single ``marker_id`` lift). ``all``
    subsumes every other entry, so its presence collapses the result to
    ``["all"]``. Numeric tokens sort ascending ahead of controller aliases.
    """
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, str):
        raw_items: list[Any] = value.split(",")
    elif isinstance(value, (list, tuple)):
        raw_items = list(value)
    else:
        # Bare int (legacy lift) or anything else the canonicaliser vets.
        raw_items = [value]
    canonical: list[str] = []
    seen: set[str] = set()
    has_all = False
    for item in raw_items:
        token = _canonical_marker_token(item)
        if token is None:
            continue
        if token == MARKER_TOKEN_ALL:
            has_all = True
            continue
        if token not in seen:
            seen.add(token)
            canonical.append(token)
    if has_all:
        return [MARKER_TOKEN_ALL]
    canonical.sort(key=_marker_token_sort_key)
    return canonical


def _coerce_choice(value: Any, choices: tuple[str, ...], default: str) -> str:
    """Return ``value`` when it matches one of ``choices``, else ``default``."""
    if isinstance(value, str) and value in choices:
        return value
    return default


def _coerce_optional_int(
    value: Any,
    *,
    lo: int,
    hi: int,
) -> int | None:
    """Coerce a wildcard-or-bounded-int field.

    Used by :class:`MidiMessageTrigger` for ``channel`` (``None`` = any;
    else 1-16), ``number`` and ``value`` (``None`` = any; else 0-127).
    ``None`` is the "any" sentinel and round-trips through TOML (the key is
    omitted on save). Empty/whitespace strings, out-of-range, ``bool``,
    ``float``, and non-coercible values all collapse to ``None``.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        # Reject floats: ``int(1.5)`` would silently truncate.
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        out = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not lo <= out <= hi:
        return None
    return out


def _coerce_str(value: Any, default: str) -> str:
    """Return ``value`` when it's a real string, else ``default``.

    Downstream consumers (templates, ``Path(value)``, ``.strip()``) assume
    ``str``; a non-string from hand-edited TOML (``model = 123``) would raise.
    """
    if isinstance(value, str):
        return value
    return default


_TRUTHY_STRINGS = frozenset({"1", "true", "yes", "on"})
_FALSY_STRINGS = frozenset({"0", "false", "no", "off"})


def _coerce_name(value: Any, pattern: re.Pattern[str]) -> str:
    """The stripped string when it is blank or matches ``pattern`` in full, else blank."""
    if not isinstance(value, str):
        return ""
    value = value.strip()
    return value if not value or pattern.fullmatch(value) else ""


def _coerce_bool(value: Any, default: bool) -> bool:
    """Accept real bools or recognised string forms; else ``default``.

    Stricter than ``bool(value)``: a TOML string ``"false"`` is truthy under
    ``bool()``, the opposite of intent. Unrecognised input falls to ``default``.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUTHY_STRINGS:
            return True
        if lowered in _FALSY_STRINGS:
            return False
    return default


# --- Config dataclasses --------------------------------------------------


CALIBRATION_CORNERS = ("DSL", "DSR", "USR", "USL")
CALIBRATION_MODES = ("simple", "advanced")
# The Setup Wizard's verdict on the lens it saved with a calibration.
CALIBRATION_LENS_RATINGS = ("excellent", "good", "fair", "poor", "user_input", "loaded")
CALIBRATION_SOURCE_MAX_LEN = 200
MARK_KINDS = ("known", "triangulated")
MAX_CALIBRATION_MARKS = 12
MAX_MARK_ID = 999
MAX_MARK_NAME = 60
MARK_REACH_M = 10_000.0  # a typed coordinate further out is a typing slip
MAX_MARK_DISTANCE_M = 200.0


def _coerce_measured(value: Any) -> float:
    """A measured number, or NaN for one that cannot be read; a boolean too (``float(True)`` is 1 m)."""
    return math.nan if isinstance(value, bool) else _coerce_float(value, math.nan)


def _coerce_mark_id(value: Any) -> int:
    """A whole number from 1 to MAX_MARK_ID, else 0, which no mark may carry."""
    if isinstance(value, bool):
        return 0
    number = _coerce_float(value, 0.0)
    return int(number) if number.is_integer() and 1 <= number <= MAX_MARK_ID else 0


@dataclass
class CalibrationMarkConfig:
    """A point of the Setup Wizard's camera solve: typed (known) or measured from two others (triangulated).

    ``pin`` is where it is on the snapshot, as fractions of its width and height; empty when not pinned.
    ``position`` is where the solve the camera pose came from put a triangulated point, kept with the pose.
    A value the kind does not read is stored as 0, so a kept mark holds finite numbers only.
    """

    # A missing coordinate or distance is no measurement: problem() refuses these defaults.
    id: int = 0
    name: str = ""
    kind: str = "known"
    x: float = math.nan  # known only
    y: float = math.nan
    z: float = math.nan
    from_a: int = 0  # triangulated only: the ids its two distances are measured from
    from_b: int = 0
    distance_a: float = math.nan  # m
    distance_b: float = math.nan
    pin: list[float] = field(default_factory=list)
    position: list[float] = field(default_factory=list)  # x, y, z in m; empty when no solve placed it

    def __post_init__(self) -> None:
        self.id = _coerce_mark_id(self.id)
        name = self.name.strip() if isinstance(self.name, str) else ""
        self.name = name[:MAX_MARK_NAME] or (f"P{self.id}" if self.id else "")
        self.kind = _coerce_choice(self.kind, MARK_KINDS, "")
        known = self.kind == "known"
        self.x = _coerce_measured(self.x) if known else 0.0
        self.y = _coerce_measured(self.y) if known else 0.0
        self.z = _coerce_measured(self.z)
        self.from_a = 0 if known else _coerce_mark_id(self.from_a)
        self.from_b = 0 if known else _coerce_mark_id(self.from_b)
        self.distance_a = 0.0 if known else _coerce_measured(self.distance_a)
        self.distance_b = 0.0 if known else _coerce_measured(self.distance_b)
        given = self.pin is not None and not (isinstance(self.pin, list | tuple) and not self.pin)
        self.pin = _coerce_fixed_list(self.pin, 2)
        self._unread_pin = given and not self.pin
        position = _coerce_fixed_list(self.position, 3)
        reached = all(abs(c) <= MARK_REACH_M for c in position)
        self.position = position if not known and reached else []

    def problem(self) -> str:
        """Why the mark cannot be used, or ``""``. A measurement is dropped, never clamped."""
        if not self.id:
            return f"needs an id between 1 and {MAX_MARK_ID}"
        if not self.kind:
            return "needs a kind of known or triangulated"
        if not -MARK_REACH_M <= self.z <= MARK_REACH_M:
            return "needs its Z"
        if self.kind == "known":
            if not (-MARK_REACH_M <= self.x <= MARK_REACH_M and -MARK_REACH_M <= self.y <= MARK_REACH_M):
                return "needs its X and Y"
        else:
            if not self.from_a or not self.from_b or len({self.from_a, self.from_b, self.id}) < 3:
                return "needs two other marks to be measured from"
            if not (0.0 < self.distance_a <= MAX_MARK_DISTANCE_M and 0.0 < self.distance_b <= MAX_MARK_DISTANCE_M):
                return f"needs both distances between 0 and {MAX_MARK_DISTANCE_M:g} m"
            if not self.pin:
                return "needs its pin: two distances allow two places"
        if self._unread_pin or (self.pin and not all(0.0 <= c <= 1.0 for c in self.pin)):
            return "needs its pin on the snapshot"
        return ""


def _coerce_fixed_list(value: Any, size: int) -> list[float]:
    """``size`` finite numbers, else empty."""
    numbers = [_coerce_measured(v) for v in value] if isinstance(value, list | tuple) else []
    return numbers if len(numbers) == size and all(math.isfinite(n) for n in numbers) else []


@dataclass
class CameraConfig:
    pos_x: float = 0.0
    pos_y: float = -11.0
    pos_z: float = 6.0
    pitch: float = -22.0
    yaw: float = 0.0
    roll: float = 0.0
    fov: float = 60.0  # horizontal FOV in degrees
    # The lens the HUD is bowed to match (openfollow.lens_model): the division
    # pair k1 / k2 (barrel negative) and the lens centre's offset from the frame
    # centre in half-diagonals (x right, y down). The video frame is never
    # warped. All 0 = pinhole.
    lens_k1: float = 0.0
    lens_k2: float = 0.0
    lens_cx: float = 0.0
    lens_cy: float = 0.0
    # The Setup Wizard's last calibration: Simple's four corners or Advanced's points, with their pins.
    calibration_mode: str = "simple"
    calibration_marks: list[CalibrationMarkConfig] = field(default_factory=list)
    calibration_height: float | None = None  # m: the camera's exact height the solve held; None when not entered
    calibration_lens_rating: str = ""  # one of CALIBRATION_LENS_RATINGS, "" when none was saved
    calibration_time: str = ""  # ISO 8601 with offset, by the clock of the browser that saved it; "" when none
    calibration_source: str = ""  # the video source the station showed when the calibration was saved

    def __post_init__(self) -> None:
        # These feed ``project_points`` via a numpy float array. Clamp fov out
        # of the degenerate zone (tan(fov/2) blows up at 180°; <= 0 degenerate).
        self.pos_x = _coerce_float(self.pos_x, 0.0)
        self.pos_y = _coerce_float(self.pos_y, -11.0)
        self.pos_z = _coerce_float(self.pos_z, 6.0)
        self.pitch = _coerce_float(self.pitch, -22.0)
        self.yaw = _coerce_float(self.yaw, 0.0)
        self.roll = _coerce_float(self.roll, 0.0)
        self.fov = _coerce_float(self.fov, 60.0, lo=1.0, hi=179.0)
        # The centre is clamped; the pair's one bound is that the overlay reaches every
        # frame corner, and a pair that does not is pulled back along its own direction.
        lens = (
            _coerce_float(self.lens_k1, 0.0),
            _coerce_float(self.lens_k2, 0.0),
            _coerce_float(self.lens_cx, 0.0, lo=-C_BOUND, hi=C_BOUND),
            _coerce_float(self.lens_cy, 0.0, lo=-C_BOUND, hi=C_BOUND),
        )
        self.lens_k1, self.lens_k2, self.lens_cx, self.lens_cy = pull_back_to_valid(*lens)
        if (self.lens_k1, self.lens_k2) != lens[:2]:
            _report_once(
                "camera.lens",
                f"lens_k1={lens[0]} / lens_k2={lens[1]} with lens centre {lens[2]:.6g} / {lens[3]:.6g} "
                f"cannot follow the picture out to its corners; using {self.lens_k1:.6g} / {self.lens_k2:.6g}",
            )
        self.calibration_mode = _coerce_choice(self.calibration_mode, CALIBRATION_MODES, "simple")
        self.calibration_marks = _coerce_calibration_marks(self.calibration_marks, self.calibration_mode)
        self.calibration_height = _coerce_calibration_height(self.calibration_height)
        self.calibration_lens_rating = _coerce_choice(self.calibration_lens_rating, CALIBRATION_LENS_RATINGS, "")
        self.calibration_time = _coerce_calibration_time(self.calibration_time)
        self.calibration_source = _coerce_calibration_source(self.calibration_source)


def _coerce_calibration_time(value: Any) -> str:
    """An ISO 8601 time with its UTC offset, to the second, else \"\". TOML reads one unquoted as a datetime."""
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.strip())
        except ValueError:
            return ""
    else:
        return ""
    return moment.isoformat(timespec="seconds") if moment.tzinfo is not None else ""


def _coerce_calibration_source(value: Any) -> str:
    """Display text without stream credentials or control characters, at most CALIBRATION_SOURCE_MAX_LEN."""
    if not isinstance(value, str):
        return ""
    return redact_uris_in_text(CONTROL_CHARS_RE.sub("", value)).strip()[:CALIBRATION_SOURCE_MAX_LEN].strip()


def _coerce_calibration_height(value: Any) -> float | None:
    """A measured height within MARK_REACH_M, else None; an unreadable one is dropped, never clamped."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    height = _coerce_measured(value)
    if abs(height) <= MARK_REACH_M:
        return height
    _report_drops("camera.calibration_height", f"{value!r} is not a height within {MARK_REACH_M:g} m")
    return None


# The warnings last reported, by field: every load_config builds the camera again, so a file says it once.
_reported_drops: dict[str, str] = {}


def _report_drops(name: str, said: str) -> None:
    if _reported_drops.get(name) != said:
        _reported_drops[name] = said
        logger.warning("%s: %s; dropped", name, said)


def _report_once(name: str, said: str) -> None:
    if _reported_drops.get(name) != said:
        _reported_drops[name] = said
        logger.warning("%s: %s", name, said)


def calibration_mark_drops(marks: list[CalibrationMarkConfig]) -> tuple[list[CalibrationMarkConfig], list[str]]:
    """The marks a solve can read: a triangulated one needs both marks it is measured from, without a loop."""
    dropped: list[str] = []
    while True:
        by_id = {m.id: m for m in marks}
        orphans = [m for m in marks if m.kind == "triangulated" and not (m.from_a in by_id and m.from_b in by_id)]
        if not orphans:
            break
        dropped += [f"mark id {m.id} is measured from a mark that is not kept" for m in orphans]
        marks = [m for m in marks if m not in orphans]
    by_id = {m.id: m for m in marks}
    looping: set[int] = set()
    for mark in marks:
        seen, todo = set(), [mark.id]
        while todo:
            current = by_id[todo.pop()]
            if current.kind != "triangulated":
                continue
            for parent in (current.from_a, current.from_b):
                if parent == mark.id:
                    looping.add(mark.id)
                elif parent not in seen:
                    seen.add(parent)
                    todo.append(parent)
    dropped += [f"mark id {i} is measured from itself through others" for i in sorted(looping)]
    if not looping:
        return marks, dropped
    kept, more = calibration_mark_drops([m for m in marks if m.id not in looping])
    return kept, dropped + more


def _coerce_calibration_marks(value: Any, mode: str = "advanced") -> list[CalibrationMarkConfig]:
    """Entries as dataclasses; one that cannot be used is dropped with one warning, past the cap too.

    Simple's marks are the rectangle's four corners: known marks with ids 1 to 4, in CALIBRATION_CORNERS order.
    """
    marks: list[CalibrationMarkConfig] = []
    entries = value if isinstance(value, list) else []
    dropped: list[str] = [] if isinstance(value, list) else ["is not a list of tables"]
    for i, entry in enumerate(entries, start=1):
        if len(marks) == MAX_CALIBRATION_MARKS:
            dropped.append(f"entries from {i} on, past the first {MAX_CALIBRATION_MARKS} usable")
            break
        if isinstance(entry, dict):
            entry = CalibrationMarkConfig(**_filter_known(CalibrationMarkConfig, entry))
        if not isinstance(entry, CalibrationMarkConfig):
            dropped.append(f"entry {i} is not a table")
            continue
        problem = entry.problem()
        if not problem and any(m.id == entry.id for m in marks):
            problem = f"repeats id {entry.id}"
        if not problem and mode == "simple" and (entry.kind != "known" or entry.id > len(CALIBRATION_CORNERS)):
            problem = "is not one of Simple's four corners"
        if problem:
            dropped.append(f"entry {i} {problem}")
            continue
        marks.append(entry)
    marks, unreadable = calibration_mark_drops(marks)
    dropped += unreadable
    if dropped:
        _report_drops(
            "camera.calibration_marks",
            "; ".join(dropped[:3]) + (f"; {len(dropped) - 3} more" if len(dropped) > 3 else ""),
        )
    return marks


def grid_corner_positions(width: float, depth: float, x: float, y: float, z: float) -> list[list[float]]:
    """DSL, DSR, USR, USL of a grid whose centre is at ``x``, ``y`` (stage left +X, upstage +Y), at ``z``."""
    hw, hd = width / 2.0, depth / 2.0
    return [[x + hw, y - hd, z], [x - hw, y - hd, z], [x - hw, y + hd, z], [x + hw, y + hd, z]]


def reconcile_simple_corners(cfg: Any) -> None:
    """Simple's corners are the grid's: a corner the grid has moved goes with it and loses its pin."""
    if cfg.camera.calibration_mode != "simple":
        return
    corners = grid_corner_positions(*(getattr(cfg.grid, k) for k in _GRID_PLACEMENT))
    for mark in cfg.camera.calibration_marks:
        place = corners[mark.id - 1]
        if any(abs(a - b) > 1e-3 for a, b in zip((mark.x, mark.y, mark.z), place, strict=True)):
            mark.x, mark.y, mark.z = place
            mark.pin = []


_GRID_PLACEMENT = ("width", "depth", "x_offset", "y_offset", "z_offset")


_GRID_COLOR_DEFAULT = "#545454"
_GRID_THICKNESS_DEFAULT = 1
_GRID_TRANSPARENCY_DEFAULT = 0.6


@dataclass
class GridConfig:
    # Master show/hide for the grid overlay. Default on so existing configs
    # keep drawing the grid.
    visible: bool = True
    width: float = 10.0
    depth: float = 6.0
    spacing: float = 1.0
    x_offset: float = 0.0
    y_offset: float = 3.0
    z_offset: float = 0.0
    # Vertical extent of the volume above the grid plane, in metres. ``0.0``
    # (or non-positive) means "unset" – the template engine refuses to render
    # ``[fz]`` / ``[ifz]`` until set (those placeholders need a denominator).
    # No validation bounds, by design.
    max_height: float = 0.0
    color: str = _GRID_COLOR_DEFAULT
    thickness: int = _GRID_THICKNESS_DEFAULT
    transparency: float = _GRID_TRANSPARENCY_DEFAULT
    origin_visible: bool = False
    origin_length: float = 1.0
    origin_thickness: int = 3

    def __post_init__(self) -> None:
        # Dimensions/offsets reach numpy float buffers in draw_grid; appearance
        # fields reach Cairo.
        self.visible = _coerce_bool(self.visible, True)
        self.width = _coerce_float(self.width, 10.0, lo=0.1)
        self.depth = _coerce_float(self.depth, 6.0, lo=0.1)
        self.spacing = _coerce_float(self.spacing, 1.0, lo=0.1)
        self.x_offset = _coerce_float(self.x_offset, 0.0)
        self.y_offset = _coerce_float(self.y_offset, 3.0)
        self.z_offset = _coerce_float(self.z_offset, 0.0)
        # Negative ``max_height`` collapses to ``0.0`` (the unset state).
        max_h = _coerce_float(self.max_height, 0.0)
        self.max_height = max_h if max_h > 0.0 else 0.0
        self.color = _coerce_hex_color(self.color, _GRID_COLOR_DEFAULT)
        # Upper bounds mirror the rules in ``openfollow/web/validation.py``.
        self.thickness = _coerce_int(self.thickness, _GRID_THICKNESS_DEFAULT, lo=1, hi=20)
        self.transparency = _coerce_float(
            self.transparency,
            _GRID_TRANSPARENCY_DEFAULT,
            lo=0.0,
            hi=1.0,
        )
        self.origin_visible = _coerce_bool(self.origin_visible, False)
        self.origin_length = _coerce_float(self.origin_length, 1.0, lo=0.1)
        self.origin_thickness = _coerce_int(self.origin_thickness, 3, lo=1, hi=20)


def _coerce_xy_vertices(value: Any, *, max_count: int | None = None) -> list[list[float]]:
    """Keep finite ``[x, y]`` pairs from *value*; anything else is dropped.

    Non-finite coordinates are dropped too: they make every point-in-polygon
    comparison False and silently corrupt the containment test.
    """
    if not isinstance(value, (list, tuple)):
        return []
    cleaned: list[list[float]] = []
    for v in value:
        if not isinstance(v, (list, tuple)) or len(v) < 2 or isinstance(v[0], bool) or isinstance(v[1], bool):
            continue
        try:
            x, y = float(v[0]), float(v[1])
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(x) and math.isfinite(y):
            cleaned.append([x, y])
    return cleaned[:max_count] if max_count is not None else cleaned


# Upper bound on ground-grid polygon corners.
GROUND_GRID_MAX_VERTICES = 64


@dataclass
class GroundGridConfig:
    """A second floor: one polygon at its own absolute Z (experimental).

    Active only when ``enabled`` and the polygon has at least three corners; a
    marker inside it in XY gets its floor elements drawn at ``z_offset``.
    """

    enabled: bool = False
    visible: bool = True
    z_offset: float = 0.0
    vertices: list[list[float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.enabled = _coerce_bool(self.enabled, False)
        self.visible = _coerce_bool(self.visible, True)
        self.z_offset = 0.0 if isinstance(self.z_offset, bool) else _coerce_float(self.z_offset, 0.0)
        self.vertices = _coerce_xy_vertices(self.vertices, max_count=GROUND_GRID_MAX_VERTICES)

    @property
    def active(self) -> bool:
        return self.enabled and len(self.vertices) >= 3


# Operator Screen marker glyph; each style reads only its own fields.
_MARKER_STYLES = ("crosshair", "cone")
CONTROL_FRAMES = ("stage", "heading", "camera", "screen")


@dataclass
class MarkerConfig:
    min_speed: float = 0.1
    max_speed: float = 3.0
    move_speed: float = 2.0
    default_pos_x: float = 0.0
    default_pos_y: float = 0.0
    default_pos_z: float = 1.6
    # Which stage directions relative input (keyboard, gamepad, 3D mouse) steers in.
    control_frame: str = "stage"
    control_heading: float = 0.0
    marker_style: str = "crosshair"
    cone_base_diameter: float = 0.6
    cone_top_diameter: float = 0.3
    cone_thickness: int = 2
    cone_filled: bool = True
    cone_opacity: float = 0.4
    cone_shaded: bool = True
    ball_visible: bool = True
    ball_size: float = 0.15
    transparency: float = 0.3
    crosshair_visible: bool = True
    crosshair_size: float = 0.3
    crosshair_color: str = "#ffffff"
    crosshair_thickness: int = 2
    z_line: bool = True
    z_line_thickness: int = 2
    ground_circle: bool = True
    ground_circle_size: float = 0.3
    ground_circle_filled: bool = False
    z_display_from_stage: bool = True

    def __post_init__(self) -> None:
        # Speeds feed the motion integrator (bad types would crash the input
        # thread); sizes and thicknesses feed Cairo draw calls.
        self.min_speed = _coerce_float(self.min_speed, 0.1, lo=0.0)
        self.max_speed = _coerce_float(self.max_speed, 3.0, lo=0.0)
        self.move_speed = _coerce_float(self.move_speed, 2.0, lo=0.0)
        if self.max_speed < self.min_speed:
            self.max_speed = self.min_speed
        self.default_pos_x = _coerce_float(self.default_pos_x, 0.0)
        self.default_pos_y = _coerce_float(self.default_pos_y, 0.0)
        self.default_pos_z = _coerce_float(self.default_pos_z, 1.6)
        self.control_frame = _coerce_choice(self.control_frame, CONTROL_FRAMES, "stage")
        heading = 0.0 if isinstance(self.control_heading, bool) else _coerce_float(self.control_heading, 0.0)
        wrapped = heading % 360.0
        # A tiny negative wraps to exactly 360.0 in float arithmetic.
        self.control_heading = 0.0 if wrapped == 360.0 else wrapped
        self.marker_style = _coerce_choice(self.marker_style, _MARKER_STYLES, "crosshair")
        self.cone_base_diameter = _coerce_float(self.cone_base_diameter, 0.6, lo=0.0)
        self.cone_top_diameter = _coerce_float(self.cone_top_diameter, 0.3, lo=0.0)
        self.cone_thickness = _coerce_int(self.cone_thickness, 2, lo=1, hi=10)
        self.cone_filled = _coerce_bool(self.cone_filled, True)
        self.cone_opacity = _coerce_float(self.cone_opacity, 0.4, lo=0.0, hi=1.0)
        self.cone_shaded = _coerce_bool(self.cone_shaded, True)
        self.ball_visible = _coerce_bool(self.ball_visible, True)
        self.crosshair_visible = _coerce_bool(self.crosshair_visible, True)
        self.z_line = _coerce_bool(self.z_line, True)
        self.ground_circle = _coerce_bool(self.ground_circle, True)
        self.ground_circle_filled = _coerce_bool(self.ground_circle_filled, False)
        self.z_display_from_stage = _coerce_bool(self.z_display_from_stage, True)
        self.ball_size = _coerce_float(self.ball_size, 0.15, lo=0.0)
        self.transparency = _coerce_float(self.transparency, 0.3, lo=0.0, hi=1.0)
        self.crosshair_size = _coerce_float(self.crosshair_size, 0.3, lo=0.0)
        self.crosshair_color = _coerce_hex_color(self.crosshair_color, "#ffffff")
        # Upper bounds mirror ``openfollow/web/validation.py``.
        self.crosshair_thickness = _coerce_int(self.crosshair_thickness, 2, lo=1, hi=10)
        self.z_line_thickness = _coerce_int(self.z_line_thickness, 2, lo=1, hi=20)
        self.ground_circle_size = _coerce_float(self.ground_circle_size, 0.3, lo=0.0)


_DETECTION_PIN_POINTS = ("top", "bottom")
# How the detection pin writes to the marker. ``replace`` overwrites the marker
# with the tracked (largest-box) detection – the original auto-pin. ``assist``
# is the two-marker hybrid: the operator steers a manual anchor freely while the
# AI-corrected output marker glides toward the *nearest* detection within
# ``assist_radius_m`` (or back to the anchor when none is in range).
_DETECTION_PIN_MODES = ("replace", "assist")


@dataclass
class DetectionMaskConfig:
    """A single polygonal detection mask in normalised 0-1 frame coordinates.

    Detection is confined to the union of all enabled masks: a person whose
    ground point falls outside every mask is discarded before tracking. An empty
    mask list leaves detection unrestricted.
    """

    name: str = ""
    vertices: list[list[float]] = field(default_factory=list)  # [[x, y], ...] in 0-1 frame space
    enabled: bool = True

    def __post_init__(self) -> None:
        self.name = _coerce_str(self.name, "")
        self.enabled = _coerce_bool(self.enabled, True)
        # Normalize vertices to [float, float] pairs. Drop non-finite coords:
        # ``float("nan")`` / ``float("inf")`` survive coercion but make every
        # point-in-polygon comparison False, silently corrupting the mask.
        cleaned: list[list[float]] = []
        for v in self.vertices:
            if isinstance(v, (list, tuple)) and len(v) >= 2:
                try:
                    x, y = float(v[0]), float(v[1])
                except (TypeError, ValueError):
                    continue
                if not (math.isfinite(x) and math.isfinite(y)):
                    continue
                cleaned.append([x, y])
        self.vertices = cleaned


@dataclass
class DetectionConfig:
    enabled: bool = False
    model: str = "yolo26n.onnx"
    storage_path: str = ""
    inference_size: int = 640
    confidence: float = 0.2
    interval_ms: int = 67
    show_boxes: bool = True
    show_labels: bool = True
    box_color: str = "#808080"
    box_thickness: int = 2
    max_persons: int = 10
    # Which marker the detection pin writes to in Fully Automatic (replace) mode.
    # ``-1`` (sentinel) follows the controller's selected marker
    # (``app._selected_id``). Any non-negative value pins to that exact id; if
    # it isn't in ``controlled_marker_ids`` the runtime skips the pin.
    pin_marker_id: int = -1
    pin_point: str = "top"
    smoothing: float = 0.15
    prediction: float = 8.0
    grace_period_ms: int = 500
    # Hybrid tracking. ``replace`` = original auto-pin (overwrite with the
    # largest detection). ``assist`` = two markers: the operator steers a manual
    # anchor (ghost) freely, and the AI-corrected output marker glides toward the
    # detection nearest that anchor within ``assist_radius_m`` (or back to the
    # anchor when none is in range). The glide reuses ``smoothing`` and never
    # snaps. ``assist_strength`` is the in-range clip ratio: 1.0 sits exactly on
    # the person, lower blends the output toward the manual anchor.
    pin_mode: str = "assist"
    assist_radius_m: float = 1.0
    assist_strength: float = 0.5
    # Master on/off for region-of-interest masking. When False (default) the
    # masks below are inactive and detection runs over the whole frame even if
    # masks are drawn; True confines detection to the union of enabled masks.
    masks_enabled: bool = False
    # Region-of-interest polygons in normalised 0-1 frame coords. Detection is
    # confined to the union of enabled masks; empty = unrestricted.
    masks: list[DetectionMaskConfig] = field(default_factory=list)

    def __post_init__(self) -> None:
        # ``model`` / ``storage_path`` are rendered into templates and consumed
        # via ``Path(...)`` / ``.strip()``; both assume ``str``.
        self.model = _coerce_str(self.model, "yolo26n.onnx")
        self.storage_path = _coerce_str(self.storage_path, "")
        self.pin_point = _coerce_choice(self.pin_point, _DETECTION_PIN_POINTS, "top")
        # inference_size: letterbox math requires >= 160; YOLO heads prefer
        # multiples of 32. Clamp here so the detector thread can't divide by
        # a bogus shape.
        size = _coerce_int(self.inference_size, 640, lo=160, hi=1280)
        self.inference_size = max(160, (size // 32) * 32)
        self.confidence = _coerce_float(self.confidence, 0.2, lo=0.0, hi=1.0)
        # Upper bounds mirror ``openfollow/web/validation.py``.
        self.interval_ms = _coerce_int(self.interval_ms, 67, lo=1, hi=10000)
        self.box_color = _coerce_hex_color(self.box_color, "#808080")
        self.box_thickness = _coerce_int(self.box_thickness, 2, lo=1, hi=10)
        self.max_persons = _coerce_int(self.max_persons, 10, lo=1, hi=50)
        # ``lo=-1`` admits the "follow selected" sentinel but no other negative.
        self.pin_marker_id = _coerce_int(self.pin_marker_id, -1, lo=-1)
        self.smoothing = _coerce_float(self.smoothing, 0.15, lo=0.0, hi=1.0)
        # Upper bounds mirror ``openfollow/web/validation.py``.
        self.prediction = _coerce_float(self.prediction, 8.0, lo=0.0, hi=20.0)
        self.grace_period_ms = _coerce_int(self.grace_period_ms, 500, lo=0, hi=10000)
        self.pin_mode = _coerce_choice(self.pin_mode, _DETECTION_PIN_MODES, "assist")
        # Gate radius for assist mode. Upper bound mirrors web/validation.py.
        self.assist_radius_m = _coerce_float(self.assist_radius_m, 1.0, lo=0.1, hi=50.0)
        self.assist_strength = _coerce_float(self.assist_strength, 0.5, lo=0.0, hi=1.0)
        self.masks_enabled = _coerce_bool(self.masks_enabled, False)
        # Convert dict entries (hand-edited TOML / imported config) to
        # dataclasses and drop non-object entries, so the detector thread can
        # dereference ``mask.vertices`` without an AttributeError.
        self.masks = [
            DetectionMaskConfig(**_filter_known(DetectionMaskConfig, m)) if isinstance(m, dict) else m
            for m in (self.masks if isinstance(self.masks, list) else [])
            if isinstance(m, (dict, DetectionMaskConfig))
        ]


@dataclass
class OscConfig:
    enabled: bool = True
    port: int = 8765
    # Source-IP allowlist for the ``/marker/{id} x y z`` receiver. Empty =
    # allow-all (the handler logs a startup warning). Each entry is a literal
    # IPv4 address, the listener's only family; CIDR blocks are not supported.
    allowed_sender_ips: list[str] = field(default_factory=list)
    # IPv4 multicast group the listener joins; empty = off. Validated to
    # 224.0.0.0–239.255.255.255, else coerced to "".
    multicast_group: str = "239.20.20.20"
    # Interface, by name, that takes the multicast membership; empty follows the
    # station interface, else the routing table picks one. The socket always binds
    # every interface, so unicast and broadcast arrive regardless.
    listen_iface: str = ""

    def __post_init__(self) -> None:
        """Normalize ``allowed_sender_ips`` (or a bare string) into ``list[str]``."""
        self.port = _coerce_int(self.port, 8765, lo=1, hi=65535)
        if not isinstance(self.listen_iface, str):
            self.listen_iface = ""
        self.listen_iface = self.listen_iface.strip()
        raw = self.allowed_sender_ips
        if isinstance(raw, str):
            # A bare string becomes a single-entry list.
            candidates: list[object] = [raw]
        elif isinstance(raw, (list, tuple, set)):
            candidates = list(raw)
        else:
            candidates = []
        entries = [entry.strip() for entry in candidates if isinstance(entry, str) and entry.strip()]
        # Only an IPv4 entry can match a sender. A list with none stays as written,
        # so it admits nobody rather than reading as empty, which admits everybody.
        self.allowed_sender_ips = [entry for entry in entries if is_ipv4_literal(entry)] or entries
        self.multicast_group = _coerce_multicast_ipv4(self.multicast_group)


# Clamp bounds for the on-screen operator-message card cap.
_OPERATOR_MESSAGES_MAX_VISIBLE_LO = 1
_OPERATOR_MESSAGES_MAX_VISIBLE_HI = 20

# Discrete overlay scale factors offered by the web-UI dropdown. A hand-edited
# TOML value is clamped to [1, 2] then snapped to the nearest entry.
VALID_OPERATOR_MESSAGE_SCALES = (1.0, 1.25, 1.5, 1.75, 2.0)


@dataclass
class OperatorMessagesConfig:
    """`[operator_messages]` – OSC-driven overlay config.

    ``enabled`` (default off) gates the OSC ingest adapter and the overlay
    draw pass. ``max_visible`` caps concurrent cards; overflow shows a
    "+N more" hint. ``position`` selects top/bottom placement. ``scale``
    uniformly scales the card window and text. ``route_by_marker`` (default
    on) keeps a marker-keyed message on the station controlling that marker;
    off shows every message on every station.
    """

    enabled: bool = False
    position: str = "bottom"  # "bottom" | "top"
    max_visible: int = 5
    scale: float = 1.0
    route_by_marker: bool = True

    def __post_init__(self) -> None:
        self.enabled = _coerce_bool(self.enabled, False)
        self.position = self.position if self.position in ("bottom", "top") else "bottom"
        self.max_visible = _coerce_int(
            self.max_visible,
            5,
            lo=_OPERATOR_MESSAGES_MAX_VISIBLE_LO,
            hi=_OPERATOR_MESSAGES_MAX_VISIBLE_HI,
        )
        scale = _coerce_float(self.scale, 1.0, lo=1.0, hi=2.0)
        if scale not in VALID_OPERATOR_MESSAGE_SCALES:
            scale = min(VALID_OPERATOR_MESSAGE_SCALES, key=lambda v: abs(v - scale))
        self.scale = scale
        self.route_by_marker = _coerce_bool(self.route_by_marker, True)


@dataclass
class TouchRemoteConfig:
    """`[touch_remote]` – the web UI's touch remote, a setup tool that drives a controlled marker.

    ``enabled`` (device-local, default off) shows the launcher and opens the remote routes. The
    rest shape the controls: the stick response curve (exponent) and dead zone (fraction of full
    deflection), and the Z wheel's step per notch and finger travel per notch.
    """

    enabled: bool = False
    stick_curve: float = 2.0
    stick_dead_zone: float = 0.08
    z_step_m: float = 0.05
    z_notch_px: int = 20

    def __post_init__(self) -> None:
        self.enabled = _coerce_bool(self.enabled, False)
        # A TOML ``true`` is not a number here, though ``float(True)`` is 1.0.
        self.stick_curve = _coerce_float(_not_bool(self.stick_curve), 2.0, lo=1.0, hi=3.0)
        self.stick_dead_zone = _coerce_float(_not_bool(self.stick_dead_zone), 0.08, lo=0.0, hi=0.5)
        self.z_step_m = _coerce_float(_not_bool(self.z_step_m), 0.05, lo=0.001, hi=1.0)
        self.z_notch_px = _coerce_int(self.z_notch_px, 20, lo=8, hi=80)


VALID_TRIGGER_SOURCES = ("markers", "detection", "both")
VALID_ZONE_EVAL_FPS = (1, 5, 10, 15, 30, 60)


@dataclass
class TriggerZoneConfig:
    """A single polygonal trigger zone in world coordinates."""

    name: str = ""
    vertices: list[list[float]] = field(default_factory=list)  # [[x, y], ...]
    color: str = "#ff8000"
    trigger_source: str = "markers"  # "markers" | "detection" | "both"
    # Per-marker filter. Empty ⇒ any marker triggers this zone. Non-empty ⇒
    # only listed marker_ids trigger. Detection IDs are NOT filtered, so
    # ``trigger_source=both`` allows the listed markers plus every detection.
    triggered_by: list[int] = field(default_factory=list)
    osc_address_first_entry: str = ""
    osc_address_additional_entry: str = ""
    osc_address_partial_exit: str = ""
    osc_address_final_exit: str = ""
    # Reference to an :class:`OscDestinationConfig`. Empty = no destination
    # selected → the zone emits nothing.
    destination_id: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.trigger_source not in VALID_TRIGGER_SOURCES:
            logger.warning(
                "Invalid zone trigger_source %r, falling back to 'markers'",
                self.trigger_source,
            )
            self.trigger_source = "markers"
        self.vertices = _coerce_xy_vertices(self.vertices)
        # Coerce ``triggered_by`` items to ``int``; drop anything that
        # can't be coerced (a hand-edited TOML with ``["a", 1]`` won't
        # crash the engine – it'll filter on ``[1]`` and log nothing,
        # since the operator-visible blur validator catches this on
        # the form path before it ever reaches disk).
        cleaned_ids: list[int] = []
        for raw_id in self.triggered_by:
            try:
                cleaned_ids.append(int(raw_id))
            except (TypeError, ValueError):
                continue
        self.triggered_by = cleaned_ids
        if not isinstance(self.destination_id, str):
            self.destination_id = ""
        self.destination_id = self.destination_id.strip()


@dataclass
class TriggerZonesConfig:
    """Global settings for the trigger-zone subsystem."""

    enabled: bool = False
    show_overlay: bool = True
    eval_fps: int = 10
    debounce_ms: int = 200
    hysteresis: float = 0.05  # metres – inward polygon offset for exit threshold
    zones: list[TriggerZoneConfig] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.eval_fps not in VALID_ZONE_EVAL_FPS:
            snapped = min(VALID_ZONE_EVAL_FPS, key=lambda v: abs(v - self.eval_fps))
            logger.warning(
                "Invalid trigger_zones.eval_fps %s, snapping to %s",
                self.eval_fps,
                snapped,
            )
            self.eval_fps = snapped
        # Use the coerce helpers, not bare ``<``/``>`` clamps: ``nan < 0`` and
        # ``nan > 10`` are both False, so a non-finite value would otherwise
        # survive. Upper bounds mirror ``openfollow/web/validation.py``.
        self.debounce_ms = _coerce_int(self.debounce_ms, 200, lo=0, hi=60000)
        self.hysteresis = _coerce_float(self.hysteresis, 0.05, lo=0.0, hi=10.0)
        # Drop non-object zone entries instead of keeping them verbatim: a
        # hand-edited inline TOML array (``zones = ["evil"]``) or a crafted
        # payload would otherwise persist a bare str/int that the zone engine
        # dereferences (``zone.vertices``) → AttributeError on the eval
        # thread. Already-typed ``TriggerZoneConfig`` entries pass through.
        self.zones = [
            TriggerZoneConfig(**_filter_known(TriggerZoneConfig, z)) if isinstance(z, dict) else z
            for z in self.zones
            if isinstance(z, (dict, TriggerZoneConfig))
        ]


# A path is addressed by name over OSC, so names keep to what an OSC address allows.
PATH_NAME_MAX_LEN = 32
PATH_NAME_PATTERN = re.compile(r"[A-Za-z0-9._-]+")
MAX_PATHS = 32
MAX_PATH_POINTS = 100
# Stage coordinates in metres; far beyond any venue.
_PATH_COORD_LIMIT = 1000.0
DEFAULT_PATH_RADIUS = 0.5


@dataclass
class PathPointConfig:
    """One point of a path: a stage position and the radius its corner is rounded with."""

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    radius: float = DEFAULT_PATH_RADIUS  # metres

    def __post_init__(self) -> None:
        self.x = _coerce_float(self.x, 0.0, lo=-_PATH_COORD_LIMIT, hi=_PATH_COORD_LIMIT)
        self.y = _coerce_float(self.y, 0.0, lo=-_PATH_COORD_LIMIT, hi=_PATH_COORD_LIMIT)
        self.z = _coerce_float(self.z, 0.0, lo=-_PATH_COORD_LIMIT, hi=_PATH_COORD_LIMIT)
        self.radius = _coerce_float(self.radius, DEFAULT_PATH_RADIUS, lo=0.0, hi=100.0)


def path_name_from(value: Any) -> str:
    """``value`` reduced to the characters a path name allows, at most 32 of them; dots alone make no name."""
    text = value if isinstance(value, str) else ""
    name = "".join(PATH_NAME_PATTERN.findall(text))[:PATH_NAME_MAX_LEN]
    # A URL resolves dot segments away, even written as %2E, so such a name could never be addressed.
    return name if name.strip(".") else ""


@dataclass
class PathConfig:
    """A path a marker can be locked onto, in PSN-absolute world coordinates."""

    name: str = ""
    # Per station: a push or an import keeps this station's flag (see PathsConfig.replace_paths).
    active: bool = False
    closed: bool = False
    # Ends of an open path: pushing past one releases the marker instead of stopping it there.
    release_at_start: bool = False
    release_at_end: bool = False
    points: list[PathPointConfig] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.name = path_name_from(self.name)
        self.active = _coerce_bool(self.active, False)
        self.closed = _coerce_bool(self.closed, False)
        self.release_at_start = _coerce_bool(self.release_at_start, False)
        self.release_at_end = _coerce_bool(self.release_at_end, False)
        raw = self.points if isinstance(self.points, list) else []
        points = [
            PathPointConfig(**_filter_known(PathPointConfig, p)) if isinstance(p, dict) else p
            for p in raw
            if isinstance(p, (dict, PathPointConfig))
        ]
        if len(points) > MAX_PATH_POINTS:
            logger.warning("Path %r has %d points, keeping the first %d", self.name, len(points), MAX_PATH_POINTS)
        self.points = points[:MAX_PATH_POINTS]


@dataclass
class PathsConfig:
    """Paths, and how a marker slides onto one."""

    show_paths: bool = True
    slide_time: float = 1.0  # seconds
    paths: list[PathConfig] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.show_paths = _coerce_bool(self.show_paths, True)
        self.slide_time = _coerce_float(self.slide_time, 1.0, lo=0.0, hi=10.0)
        raw = self.paths if isinstance(self.paths, list) else []
        paths = [
            PathConfig(**_filter_known(PathConfig, p)) if isinstance(p, dict) else p
            for p in raw
            if isinstance(p, (dict, PathConfig))
        ]
        if len(paths) > MAX_PATHS:
            logger.warning("%d paths configured, keeping the first %d", len(paths), MAX_PATHS)
        self.paths = paths[:MAX_PATHS]
        self._give_unique_names()

    def _give_unique_names(self) -> None:
        # OSC finds a path by name ignoring case, so two must never match.
        explicit = {path.name.lower() for path in self.paths if path.name}
        taken: set[str] = set()
        for index, path in enumerate(self.paths):
            base = path.name or f"path-{index + 1}"
            name, n = base, 1
            # A name made up here, or a clash's new one, never takes a name another path was given.
            while name.lower() in taken or (name != path.name and name.lower() in explicit):
                n += 1
                suffix = f"-{n}"
                name = base[: PATH_NAME_MAX_LEN - len(suffix)] + suffix
            if name != path.name:
                if path.name:
                    logger.warning("Path name %r is already taken, renamed to %r", path.name, name)
                path.name = name
            taken.add(name.lower())

    def find(self, name: str) -> PathConfig | None:
        """The path called ``name``, ignoring case."""
        wanted = name.lower()
        return next((path for path in self.paths if path.name.lower() == wanted), None)

    def replace_paths(self, raw: Any) -> None:
        """Take ``raw``'s paths, keeping this station's Active flag for each name it already has.

        A new name arrives inactive: what is active is decided per station, by its
        own web UI and desk.
        """
        active = {path.name.lower(): path.active for path in self.paths}
        self.paths = raw if isinstance(raw, list) else []
        self.__post_init__()
        for path in self.paths:
            path.active = active.get(path.name.lower(), False)


@dataclass
class MidiPatch:
    """A stable, integer-ID'd MIDI slot the operator assigns a device to.

    ``id`` is the foreign key every binding / virtual fader references – never
    the port name or alias. IDs are sequential integers from 1; ``0`` is the
    "any patch" wildcard, so a real patch always has ``id >= 1``.

    Device identity falls through ``serial`` first (the only way to
    distinguish two of the same model connected at once), then the
    (``port_name``, ``product``) composite.
    """

    id: int = 0
    alias: str = ""
    serial: str = ""
    port_name: str = ""
    product: str = ""

    def __post_init__(self) -> None:
        self.id = _coerce_int(self.id, 0, lo=0)
        self.alias = _coerce_str(self.alias, "").strip()
        self.serial = _coerce_str(self.serial, "").strip()
        self.port_name = _coerce_str(self.port_name, "").strip()
        self.product = _coerce_str(self.product, "").strip()
        # Discovery populates ``product`` from the same string as ``port_name``
        # (mido exposes only a port-name). Mirror that for a patch carrying
        # ``port_name`` alone so ``identifier`` matches a discovered device's
        # key; otherwise the device opens but the UI shows "(not connected)".
        if self.port_name and not self.product:
            self.product = self.port_name

    @property
    def label(self) -> str:
        """Dropdown label: ``"<id> – <alias or port name>"``."""
        name = self.alias or self.port_name or f"Patch {self.id}"
        return f"{self.id} – {name}"

    @property
    def identifier(self) -> str:
        """Stable device key matching ``_DiscoveredDevice.identifier`` in
        the MIDI subsystem – ``serial:<serial>`` when a serial is known,
        else ``port:<port_name>|<product>``. Empty when no device is bound
        (a fresh patch). Used by the Device dropdown to mark the selected
        option and to preserve a binding whose device is currently
        disconnected (the row re-submits this same key)."""
        if not (self.serial or self.port_name or self.product):
            return ""
        if self.serial:
            return f"serial:{self.serial}"
        return f"port:{self.port_name}|{self.product}"


@dataclass
class MidiConfig:
    """Top-level ``[midi]`` config: the operator's MIDI device patches.

    ``patches`` load as raw dicts (TOML arrays of tables) and are coerced to
    typed instances in ``__post_init__``.
    """

    patches: list[MidiPatch] = field(default_factory=list)

    def __post_init__(self) -> None:
        patches = self.patches if isinstance(self.patches, list) else []
        self.patches = [
            MidiPatch(**_filter_known(MidiPatch, p)) if isinstance(p, dict) else p
            for p in patches
            if isinstance(p, (dict, MidiPatch))
        ]
        # Guarantee every patch has a unique id >= 1 (id 0 / duplicates get the
        # next free sequential id) so binding foreign keys always resolve.
        seen: set[int] = set()
        for patch in self.patches:
            if patch.id < 1 or patch.id in seen:
                patch.id = self.next_patch_id()
            seen.add(patch.id)

    def next_patch_id(self) -> int:
        """Smallest unused positive patch id (reuses freed numbers)."""
        used = {p.id for p in self.patches if p.id >= 1}
        candidate = 1
        while candidate in used:
            candidate += 1
        return candidate

    def patch_by_id(self, patch_id: int) -> MidiPatch | None:
        """Look up a patch by id, or ``None`` (e.g. a deleted/stale ref)."""
        for patch in self.patches:
            if patch.id == patch_id:
                return patch
        return None


# Virtual fader count (eight normalized 0-1 faders). Lives here so
# :class:`VirtualFadersConfig` can pad/trim at load time without
# circular-importing the bus module.
VIRTUAL_FADER_COUNT: int = 8

# Allowed ``VirtualFaderConfig.source_kind`` values:
#   ``""``     – unmapped, fader sits at its default value.
#   ``"midi"`` – driven by an incoming MIDI message.
# The gamepad now drives per-controlled-marker faders (keyed by marker id),
# not a fixed index, so the indexed faders are uniformly MIDI / unmapped.
VALID_FADER_SOURCE_KINDS: tuple[str, ...] = ("", "midi")

# MIDI message types that can drive a virtual fader.
# Pitch-bend / sysex are not modelled.
VALID_FADER_MIDI_TYPES: tuple[str, ...] = (
    "control_change",
    "key_pressure",
    "channel_pressure",
)


@dataclass
class VirtualFaderConfig:
    """One virtual fader's persisted configuration.

    Index is implicit (position in :attr:`VirtualFadersConfig.faders`).
    Blank :attr:`name` renders as ``"Fader N"`` at render time (fallback in
    the bus). ``source_*`` fields identify the fader's runtime value source;
    ``source_midi_channel`` of ``0`` matches any channel, non-zero matches
    channels 1-16 exactly.
    """

    name: str = ""
    default_value: float = 0.0
    show_on_display: bool = False
    source_kind: str = ""
    source_patch: int = 0  # MIDI patch id; 0 = any
    source_midi_type: str = "control_change"
    source_midi_channel: int = 0  # 0 = any
    source_midi_number: int = 0
    # Operator-assignable display colour (hex); drives the fader strip tint.
    color: str = "#000000"

    def __post_init__(self) -> None:
        self.name = _coerce_str(self.name, "").strip()
        self.default_value = _coerce_float(
            self.default_value,
            0.0,
            lo=0.0,
            hi=1.0,
        )
        self.show_on_display = _coerce_bool(self.show_on_display, False)
        self.source_kind = _coerce_choice(
            self.source_kind,
            VALID_FADER_SOURCE_KINDS,
            "",
        )
        self.source_patch = _coerce_int(self.source_patch, 0, lo=0)
        self.source_midi_type = _coerce_choice(
            self.source_midi_type,
            VALID_FADER_MIDI_TYPES,
            "control_change",
        )
        self.source_midi_channel = _coerce_int(
            self.source_midi_channel,
            0,
            lo=0,
            hi=16,
        )
        self.source_midi_number = _coerce_int(
            self.source_midi_number,
            0,
            lo=0,
            hi=127,
        )
        self.color = _coerce_hex_color(self.color, "#000000")


@dataclass
class VirtualFadersConfig:
    """Virtual fader entries persisted in ``[virtual_faders]``.

    Pads to :data:`VIRTUAL_FADER_COUNT` entries on load (so an omitted or
    partial section still yields a full list) and trims on overflow.
    """

    faders: list[VirtualFaderConfig] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Coerce dicts to typed entries before padding so pad/trim sees
        # uniform values.
        faders = self.faders if isinstance(self.faders, list) else []
        self.faders = [
            VirtualFaderConfig(**_filter_known(VirtualFaderConfig, f)) if isinstance(f, dict) else f
            for f in faders
            if isinstance(f, (dict, VirtualFaderConfig))
        ]
        while len(self.faders) < VIRTUAL_FADER_COUNT:
            self.faders.append(VirtualFaderConfig())
        if len(self.faders) > VIRTUAL_FADER_COUNT:
            self.faders = self.faders[:VIRTUAL_FADER_COUNT]


@dataclass
class OtpOutputConfig:
    enabled: bool = False
    port: int = 5568
    # Pin the source interface by name (eth0/wlan0), like ``psn_source_iface``;
    # resolved to a concrete bind IP at runtime. Empty = auto-detect primary.
    source_iface: str = ""
    system_number: int = 1
    priority: int = 100

    def __post_init__(self) -> None:
        self.port = _coerce_int(self.port, 5568, lo=1, hi=65535)
        # E1.59 §8.3: 1-200. Also drives the multicast destination
        # (transform_mcast_ip), so the clamp matters for routing.
        self.system_number = _coerce_int(self.system_number, 1, lo=1, hi=200)
        # E1.59 §9.3: 0-200.
        self.priority = _coerce_int(self.priority, 100, lo=0, hi=200)
        if not isinstance(self.source_iface, str):
            self.source_iface = ""
        self.source_iface = self.source_iface.strip()

    @property
    def transform_mcast_ip(self) -> str:
        """Transform multicast address (E1.59 Table 15-19), derived from ``system_number``."""
        return f"239.159.1.{self.system_number}"

    @property
    def advertisement_mcast_ip(self) -> str:
        """Advertisement multicast address (E1.59 Table 15-19)."""
        return "239.159.2.1"


@dataclass
class RttrpmOutputConfig:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 36700
    fps: int = 60
    context: int = 0
    # Interface to send from, by name; blank follows ``psn_source_iface``.
    source_iface: str = ""

    def __post_init__(self) -> None:
        # fps drives ``1.0 / fps`` in the send loop – clamp to >= 1 to avoid
        # ZeroDivisionError. Upper bound mirrors ``openfollow/web/validation.py``.
        if not isinstance(self.fps, int) or isinstance(self.fps, bool) or self.fps < 1:
            self.fps = 1
        elif self.fps > 240:
            self.fps = 240
        self.port = _coerce_int(self.port, 36700, lo=1, hi=65535)
        self.context = _coerce_int(self.context, 0, lo=0, hi=4294967295)
        # Type-guard ``host`` before stripping so a non-string or trailing
        # whitespace doesn't break name resolution or trigger spurious
        # live-restarts.
        if not isinstance(self.host, str):
            self.host = "127.0.0.1"
        self.host = self.host.strip()
        if not isinstance(self.source_iface, str):
            self.source_iface = ""
        self.source_iface = self.source_iface.strip()


# ---------------------------------------------------------------------------
# Flexible OSC transmitter system
# ---------------------------------------------------------------------------

# Allowed transmitter rates. Every value divides 60 evenly, so the 60 Hz
# scheduler thread sends on whole-tick boundaries with zero jitter.
VALID_OSC_TRANSMITTER_RATES = (1, 5, 10, 20, 30, 60)
VALID_OSC_TRANSMITTER_PROTOCOLS = ("udp", "tcp")
# Per-binding TCP framing. SLIP (RFC 1055) is OSC 1.1's required framing;
# length-prefix is the OSC 1.0 style. Inert for UDP rows but round-trips
# through TOML so the dropdown swap stays symmetric.
VALID_OSC_FRAMINGS = ("slip", "length_prefix")

# MIDI trigger message types. Mirrors
# :data:`openfollow.input.midi._MIDI_EVENT_TYPES`; defined separately here
# because configuration.py precedes input/midi.py in the import graph
# (input/midi.py imports MidiPatch from here). Pitch-bend / sysex / clock are
# not modelled as triggers and the conversion layer drops them anyway.
VALID_MIDI_MESSAGE_TYPES: tuple[str, ...] = (
    "note_on",
    "note_off",
    "control_change",
    "program_change",
    "key_pressure",
    "channel_pressure",
)


def _snap_to_valid_rate(value: Any, default: int = 30) -> int:
    """Snap an integer-ish value to the nearest valid transmitter rate.

    - Non-coercible values → ``default``.
    - Out-of-range ints are clamped to ``[1, 240]`` first, THEN snapped to
      the nearest valid rate (NOT to ``default``) – e.g. 1000 → 240 → 60.
    - In-range ints not already valid → snapped to nearest valid.
    """
    rate_int = _coerce_int(value, default, lo=1, hi=240)
    if rate_int not in VALID_OSC_TRANSMITTER_RATES:
        rate_int = min(
            VALID_OSC_TRANSMITTER_RATES,
            key=lambda v: abs(v - rate_int),
        )
    return rate_int


def _new_uuid_hex() -> str:
    """Mint a fresh uuid4 hex. Wrapped so test code can monkeypatch
    `_new_uuid_hex` for deterministic ids without touching the stdlib."""
    import uuid

    return uuid.uuid4().hex


# --------------------------------------------------------------------------- #
# Trigger types
# --------------------------------------------------------------------------- #
#
# Every transmitter row carries a ``trigger`` describing *when* a send fires.
#
# TOML representation: a sub-table with a ``kind`` discriminator, e.g.
# ``[osc_transmitters.transmitters.trigger] kind = "stream", rate_hz = 30``.
# Each trigger dataclass carries ``kind: str = field(init=False)`` defaulting
# to the discriminator string, so plain ``asdict`` emits the right TOML shape
# on save. ``_trigger_from_dict`` is the load-time factory.

VALID_TRIGGER_KINDS = (
    "stream",
    "hotkey",
    "controller_button",
    # encoder-on-change is not a selectable kind (no relative-encoder
    # hardware); EncoderOnChangeTrigger is retained only for config
    # round-trip and load-time degradation to Stream.
    "midi_message",
    "fader_on_change",
)
VALID_TRIGGER_EDGES = ("press", "release")
VALID_TRIGGER_MODIFIERS = ("ctrl", "shift", "alt", "cmd")


VALID_OSC_STREAM_MODES = ("always", "on_change")


@dataclass(frozen=True)
class StreamTrigger:
    """Continuous send at the configured rate via the 60 Hz scheduler's
    per-row down-sample counter.

    ``kind`` is set automatically (``init=False``) and serialises as the TOML
    discriminator :func:`_trigger_from_dict` reads on load.

    ``mode="on_change"`` fires only when the row's default marker has moved
    >= ``min_change_m`` (metres) along any axis (``max(|dx|, |dy|, |dz|)``,
    not Euclidean). The gate watches ONLY the default marker, independent of
    the message's placeholders; rows with no default marker fire every tick.
    ``rate_hz`` sets the gate-evaluation cadence; ``mode`` decides whether an
    evaluated tick sends.
    """

    rate_hz: int = 30
    mode: str = "always"
    min_change_m: float = 0.05
    kind: str = field(default="stream", init=False)

    def __post_init__(self) -> None:
        # Frozen dataclass: normalise fields via object.__setattr__.
        object.__setattr__(self, "rate_hz", _snap_to_valid_rate(self.rate_hz))
        mode = self.mode if isinstance(self.mode, str) else "always"
        if mode not in VALID_OSC_STREAM_MODES:
            mode = "always"
        object.__setattr__(self, "mode", mode)
        # ``min_change_m`` is metres; clamp non-negative (no upper clamp) so a
        # negative threshold can't invert the gate.
        try:
            change = float(self.min_change_m)
        except (TypeError, ValueError):
            change = 0.05
        if change < 0.0 or change != change:  # NaN check via self-compare
            change = 0.05
        object.__setattr__(self, "min_change_m", change)


@dataclass(frozen=True)
class HotkeyTrigger:
    """Fire on a key + modifier combination. Modifier list is normalised
    to a sorted lower-case tuple so two configs that differ only in
    modifier order compare equal."""

    key: str = ""
    modifiers: tuple[str, ...] = ()
    edge: str = "press"
    kind: str = field(default="hotkey", init=False)

    def __post_init__(self) -> None:
        key = self.key.strip() if isinstance(self.key, str) else ""
        if isinstance(self.modifiers, (list, tuple, set, frozenset)):
            mods = tuple(
                sorted(
                    {
                        str(m).strip().lower()
                        for m in self.modifiers
                        if str(m).strip().lower() in VALID_TRIGGER_MODIFIERS
                    }
                )
            )
        else:
            mods = ()
        edge = _coerce_choice(self.edge, VALID_TRIGGER_EDGES, "press")
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "modifiers", mods)
        object.__setattr__(self, "edge", edge)


@dataclass(frozen=True)
class ControllerButtonTrigger:
    """Fire on a controller button edge. Press- and release-edge bindings
    sharing the same physical button are independent rows."""

    button: str = ""
    edge: str = "press"
    kind: str = field(default="controller_button", init=False)

    def __post_init__(self) -> None:
        button = self.button.strip() if isinstance(self.button, str) else ""
        edge = _coerce_choice(self.edge, VALID_TRIGGER_EDGES, "press")
        object.__setattr__(self, "button", button)
        object.__setattr__(self, "edge", edge)


# MIDI trigger wildcard semantics: empty / ``None`` means "any", so a row can
# constrain only the fields that matter. The dispatcher walks every row against
# every incoming :class:`MidiEvent` and fires every match – overlapping
# wildcards firing multiple bindings is intentional.


@dataclass(frozen=True)
class MidiMessageTrigger:
    """Fire on a matching MIDI message.

    Wildcard-by-default: ``patch_id=0`` matches any patch, ``channel=None``
    any channel (else 1-16), ``number=None`` any note/CC, ``value=None`` any
    value. A ``channel=None`` row encodes its conflict-registry identifier
    with channel ``0`` (the "any channel" sentinel), so the registry treats
    semantically-overlapping rows as distinct; the dispatcher resolves the
    actual overlap by matching against every row.
    """

    patch_id: int = 0
    type: str = "note_on"
    channel: int | None = None
    number: int | None = None
    value: int | None = None
    kind: str = field(default="midi_message", init=False)

    def __post_init__(self) -> None:
        patch_id = _coerce_int(self.patch_id, 0, lo=0)
        type_ = _coerce_choice(
            self.type,
            VALID_MIDI_MESSAGE_TYPES,
            "note_on",
        )
        channel = _coerce_optional_int(self.channel, lo=1, hi=16)
        number = _coerce_optional_int(self.number, lo=0, hi=127)
        value = _coerce_optional_int(self.value, lo=0, hi=127)
        # ``program_change`` / ``channel_pressure`` carry no ``number`` on the
        # wire (``MidiEvent.number = None``); normalise to ``None`` so the row
        # can match and its registry identifier stays canonical.
        if type_ in ("program_change", "channel_pressure"):
            number = None
        object.__setattr__(self, "patch_id", patch_id)
        object.__setattr__(self, "type", type_)
        object.__setattr__(self, "channel", channel)
        object.__setattr__(self, "number", number)
        object.__setattr__(self, "value", value)


@dataclass(frozen=True)
class FaderOnChangeTrigger:
    """Fire when a virtual fader value changes.

    ``marker_id`` picks the source:
    * ``0`` (default): indexed virtual fader; ``fader`` is the 1-based index
      (1..:data:`VIRTUAL_FADER_COUNT`), MIDI-driven.
    * ``>= 1``: per-controlled-marker gamepad fader keyed by marker id;
      ``fader`` is ignored (kept valid for round-trip).

    ``rate_hz`` throttles: changes faster than ``1/rate_hz`` apart coalesce
    via the 60 Hz scheduler-tick down-sample counter.
    """

    fader: int = 1
    rate_hz: int = 30
    marker_id: int = 0
    kind: str = field(default="fader_on_change", init=False)

    def __post_init__(self) -> None:
        fader = _coerce_int(self.fader, 1, lo=1, hi=VIRTUAL_FADER_COUNT)
        object.__setattr__(self, "fader", fader)
        object.__setattr__(self, "rate_hz", _snap_to_valid_rate(self.rate_hz))
        # 0 = indexed source (use ``fader``); >= 1 = marker-fader source.
        # Marker id 0 is the PSN "ignored" sentinel, so it can never be a
        # real marker – it doubles cleanly as the "indexed, not a marker"
        # discriminator here.
        marker_id = _coerce_int(self.marker_id, 0, lo=0)
        object.__setattr__(self, "marker_id", marker_id)


@dataclass(frozen=True)
class EncoderOnChangeTrigger:
    """Encoder-on-change trigger – retained as a known ``kind`` for config
    round-trip only. Encoder support is unimplemented (no relative-encoder
    hardware), so :func:`_trigger_from_dict` maps this kind to a Stream
    trigger. Kept to keep the :data:`Trigger` union stable.
    """

    kind: str = field(default="encoder_on_change", init=False)


Trigger = (
    StreamTrigger
    | HotkeyTrigger
    | ControllerButtonTrigger
    | MidiMessageTrigger
    | FaderOnChangeTrigger
    | EncoderOnChangeTrigger
)


def _trigger_from_dict(data: Any) -> Trigger:
    """Factory: TOML sub-table → typed :data:`Trigger`.

    Robust to malformed input – unknown ``kind``, missing fields, and non-dict
    payloads all fall back to a default ``StreamTrigger`` so one bad row can't
    fail the whole config load (also forward-compat with future kinds).
    """
    if not isinstance(data, dict):
        return StreamTrigger()
    kind = data.get("kind", "stream")
    if kind == "stream":
        return StreamTrigger(
            rate_hz=data.get("rate_hz", 30),
            mode=data.get("mode", "always"),
            min_change_m=data.get("min_change_m", 0.05),
        )
    if kind == "hotkey":
        # Pass ``modifiers`` verbatim – ``HotkeyTrigger.__post_init__`` collapses
        # non-iterables (e.g. a null) to (); an eager ``tuple(...)`` here would
        # raise on malformed input.
        return HotkeyTrigger(
            key=data.get("key", ""),
            modifiers=data.get("modifiers", ()),
            edge=data.get("edge", "press"),
        )
    if kind == "controller_button":
        return ControllerButtonTrigger(
            button=data.get("button", ""),
            edge=data.get("edge", "press"),
        )
    if kind == "midi_message":
        return MidiMessageTrigger(
            patch_id=data.get("patch_id", 0),
            type=data.get("type", "note_on"),
            channel=data.get("channel"),
            number=data.get("number"),
            value=data.get("value"),
        )
    if kind == "fader_on_change":
        return FaderOnChangeTrigger(
            fader=data.get("fader", 1),
            rate_hz=data.get("rate_hz", 30),
            marker_id=data.get("marker_id", 0),
        )
    # Unknown kind – safe default so the load still succeeds.
    return StreamTrigger()


@dataclass
class OscTransmitterConfig:
    """A single OSC transmitter row.

    The address and each entry of ``args`` are template strings parsed by
    :func:`openfollow.osc.template.compile_template` at runtime.

    ``id`` is a stable uuid4 hex used as the web form-field key, the
    :class:`OscTransmitterManager` lookup key, and the persistence id across
    reloads. A row without an id gets one minted on the next load.
    """

    # Defaults to disabled (matching the other network-output sections): a
    # row is inert until explicitly enabled.
    id: str = ""
    enabled: bool = False
    name: str = ""
    # Reference to an :class:`OscDestinationConfig` (host/port/protocol/framing
    # live there). Empty = no destination selected → the row skips sending.
    destination_id: str = ""
    # Default markers driving ``[x]`` / ``[markerid]`` / ``[markerfader]``.
    # Each token is a marker id, a controller alias ``cN``, or ``all`` (every
    # controlled marker). Multiple tokens fan the row out into one
    # independent send per resolved marker. Empty list = no default marker:
    # rows using only literals or explicit refs (``[x:markerN]``) still
    # dispatch; rows using a default-marker placeholder skip with "no default
    # marker configured" until a token is set.
    markers: list[str] = field(default_factory=list)
    # Optional default virtual fader. Bare placeholders (``[fader]``,
    # ``[float]``, ``[int:min-max]``) resolve through this; explicit refs
    # (``[fader:3]``) ignore it. ``None`` = unset; ``0`` is rejected at
    # coercion (faders are 1-indexed, 1..VIRTUAL_FADER_COUNT).
    default_fader: int | None = None
    template_id: str = ""  # builtin template id, or "" for free-form
    address: str = ""
    args: list[str] = field(default_factory=list)
    rate_hz: int = 30
    # Accepts a typed :data:`Trigger` or a dict (TOML sub-table). Default
    # ``None`` so ``__post_init__`` can distinguish "no trigger set" (lift
    # ``rate_hz`` into a Stream trigger) from "set explicitly" – a
    # ``StreamTrigger(rate_hz=30)`` default would silently overwrite a
    # hand-set ``rate_hz``.
    trigger: Any = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            self.id = _new_uuid_hex()
        else:
            self.id = self.id.strip()
        self.enabled = _coerce_bool(self.enabled, False)
        if not isinstance(self.name, str):
            self.name = ""
        self.name = self.name.strip()
        if not isinstance(self.destination_id, str):
            self.destination_id = ""
        self.destination_id = self.destination_id.strip()
        self.markers = _coerce_marker_tokens(self.markers)
        # ``None`` for unset, else 1..VIRTUAL_FADER_COUNT; out-of-range/junk
        # collapses to ``None`` rather than routing to a valid-but-wrong fader.
        self.default_fader = _coerce_optional_int(
            self.default_fader,
            lo=1,
            hi=VIRTUAL_FADER_COUNT,
        )
        if not isinstance(self.template_id, str):
            self.template_id = ""
        self.template_id = self.template_id.strip()
        if not isinstance(self.address, str):
            self.address = ""
        self.address = self.address.strip()
        # OSC addresses must start with "/"; normalise a non-empty hand-edited
        # value so load/save matches the blur validator (which requires it).
        if self.address and not self.address.startswith("/"):
            self.address = "/" + self.address
        if not isinstance(self.args, list):
            self.args = []
        else:
            self.args = [str(a) for a in self.args]
        # Snap to nearest valid rate so the runtime never sees an off-set value.
        self.rate_hz = _snap_to_valid_rate(self.rate_hz)
        # Trigger normalisation, in order:
        #   1. dict → typed via :func:`_trigger_from_dict`.
        #   2. None or junk → StreamTrigger lifted from ``rate_hz``.
        # Then mirror a Stream trigger's rate back to ``rate_hz`` so the legacy
        # field stays authoritative.
        if isinstance(self.trigger, dict):
            self.trigger = _trigger_from_dict(self.trigger)
        elif self.trigger is None or not isinstance(
            self.trigger,
            (
                StreamTrigger,
                HotkeyTrigger,
                ControllerButtonTrigger,
                MidiMessageTrigger,
                FaderOnChangeTrigger,
                EncoderOnChangeTrigger,
            ),
        ):
            self.trigger = StreamTrigger(rate_hz=self.rate_hz)
        if isinstance(self.trigger, StreamTrigger):
            self.rate_hz = self.trigger.rate_hz


@dataclass
class OscTransmittersConfig:
    """Container for the OSC-transmitters section (the ``transmitters`` rows).

    Operator-saved templates live as ``.oftemplate`` JSON files under
    ``<config-dir>/templates/user/``, not in this config.
    """

    transmitters: list[OscTransmitterConfig] = field(default_factory=list)

    def __post_init__(self) -> None:
        coerced_transmitters: list[OscTransmitterConfig] = []
        for t in self.transmitters:
            if isinstance(t, dict):
                # Legacy lift: a row with ``rate_hz`` but no ``trigger`` gets a
                # Stream trigger minted from the rate, preserving its cadence.
                row_data: dict[str, Any] = dict(t)
                if "trigger" not in row_data and "rate_hz" in row_data:
                    row_data["trigger"] = {
                        "kind": "stream",
                        "rate_hz": row_data["rate_hz"],
                    }
                # Legacy lift: a single ``marker_id`` becomes the one-token
                # ``markers`` list (``_coerce_marker_tokens`` handles the
                # scalar). ``_filter_known`` would otherwise drop the unknown
                # key and lose the operator's default marker.
                if "markers" not in row_data and "marker_id" in row_data:
                    row_data["markers"] = row_data["marker_id"]
                coerced_transmitters.append(
                    OscTransmitterConfig(
                        **_filter_known(OscTransmitterConfig, row_data),
                    ),
                )
            else:
                coerced_transmitters.append(t)
        self.transmitters = coerced_transmitters


@dataclass
class OscDestinationConfig:
    """A named, reusable OSC connection profile.

    Transmitters and trigger zones reference a destination by ``id`` instead
    of carrying host/port/protocol/framing inline, so re-pointing a single
    destination repoints every consumer at once.
    """

    id: str = ""
    name: str = ""
    host: str = "127.0.0.1"
    port: int = 8000
    protocol: str = "udp"
    # TCP framing selector. Inert for UDP but round-trippable so the UI swap
    # doesn't hide it on protocol toggle.
    framing: str = "slip"
    # Interface to send from, by name; blank follows ``psn_source_iface``.
    source_iface: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            self.id = _new_uuid_hex()
        else:
            self.id = self.id.strip()
        if not isinstance(self.name, str):
            self.name = ""
        self.name = self.name.strip()
        if not isinstance(self.host, str):
            self.host = "127.0.0.1"
        self.host = self.host.strip() or "127.0.0.1"
        self.port = _coerce_int(self.port, 8000, lo=1, hi=65535)
        self.protocol = _coerce_choice(
            self.protocol,
            VALID_OSC_TRANSMITTER_PROTOCOLS,
            "udp",
        )
        self.framing = _coerce_choice(
            self.framing,
            VALID_OSC_FRAMINGS,
            "slip",
        )
        if not isinstance(self.source_iface, str):
            self.source_iface = ""
        self.source_iface = self.source_iface.strip()


def _default_osc_destinations() -> list[OscDestinationConfig]:
    """Seed one pickable destination so fresh installs have a non-empty list.

    The id is fixed (not minted) so two default configs compare equal – the
    hot-reload diff and a pile of ``AppConfig() == AppConfig()`` tests rely on
    default-config equality.
    """
    return [OscDestinationConfig(id="default", name="Default")]


@dataclass
class OscDestinationsConfig:
    """Container for the shared OSC destination profiles."""

    destinations: list[OscDestinationConfig] = field(
        default_factory=_default_osc_destinations,
    )

    def __post_init__(self) -> None:
        # Drop non-object destination entries instead of keeping them verbatim:
        # a hand-edited inline TOML array (``destinations = ["evil"]``) or a
        # crafted import would otherwise persist a bare str that ``get()`` and
        # the template dereference (``d.id`` / ``d.host``) → AttributeError.
        # Already-typed ``OscDestinationConfig`` entries pass through.
        self.destinations = [
            OscDestinationConfig(**_filter_known(OscDestinationConfig, d)) if isinstance(d, dict) else d
            for d in self.destinations
            if isinstance(d, (dict, OscDestinationConfig))
        ]
        # The id is the key transmitters/zones reference, so it must be unique:
        # a hand-edited TOML or crafted import with two entries sharing an id
        # would make ``get()`` resolve ambiguously (first match wins) and hide
        # the duplicate from editing. Keep the first occurrence's id stable so
        # existing references stay valid; re-mint a fresh uuid for each later
        # collision rather than dropping the entry.
        seen_ids: set[str] = set()
        for dest in self.destinations:
            if dest.id in seen_ids:
                dest.id = _new_uuid_hex()
            seen_ids.add(dest.id)

    def get(self, destination_id: str) -> OscDestinationConfig | None:
        """Resolve a destination id to its profile, or ``None`` if unknown.

        Linear scan – fine for one-off lookups (web routes, wizard). Hot
        per-tick consumers stage an :meth:`by_id` index instead.
        """
        if not destination_id:
            return None
        for d in self.destinations:
            if d.id == destination_id:
                return d
        return None

    def by_id(self) -> dict[str, OscDestinationConfig]:
        """Index destinations by id for O(1) resolution on the hot path.

        Built once when the set is staged into a consumer (the transmitter
        manager / zone engine) so per-row, per-tick resolution is a dict
        lookup rather than a linear scan under the lock. Ids are unique
        (see ``__post_init__``), so the comprehension is unambiguous.
        """
        return {d.id: d for d in self.destinations}


VALID_CURVES = ("linear", "logarithmic", "quadratic", "s-law")
VALID_STICKS = ("left", "right")
# Gamepad stick selector for the marker-fader integrator. ``""`` = not
# stick-driven; the non-empty options pick the Y-axis of the named stick
# (X-axes are not modelled).
VALID_MARKER_FADER_STICKS = ("", "left_y", "right_y")

VALID_BUTTON_NAMES = frozenset(
    {
        "",
        "A",
        "B",
        "X",
        "Y",
        "BACK",
        "START",
        "LB",
        "RB",
        "LT",
        "RT",
        "DPAD_UP",
        "DPAD_DOWN",
        "DPAD_LEFT",
        "DPAD_RIGHT",
    }
)

VALID_KEY_NAMES = frozenset(
    {
        "",
        "a",
        "b",
        "c",
        "d",
        "e",
        "f",
        "g",
        "h",
        "i",
        "j",
        "k",
        "l",
        "m",
        "n",
        "o",
        "p",
        "q",
        "r",
        "s",
        "t",
        "u",
        "v",
        "w",
        "x",
        "y",
        "z",
        "Shift",
        "Control",
        "Alt",
        "Space",
        "Tab",
    }
)

# Letters reserved for the WASD/IJKL movement layouts; action keys
# must not collide with these or movement input would steal the
# keypress.  Numpad keys are not letters so no collision is possible.
RESERVED_MOVEMENT_KEYS = frozenset({"w", "a", "s", "d", "i", "j", "k", "l"})

VALID_MOVE_LAYOUTS = ("wasd", "ijkl", "numpad")

_BUTTON_MAPPING_FIELDS = (
    "btn_reset",
    "btn_toggle_help",
    "btn_toggle_zones",
    "btn_speed_down",
    "btn_speed_up",
    "btn_move_z_down",
    "btn_move_z_up",
    "btn_next_marker",
    "btn_prev_marker",
    "btn_settings",
    "btn_menu_confirm",
    "btn_menu_cancel",
    "btn_clear_messages",  # clear operator-message cards
    "btn_pathfinder",
    "btn_toggle_paths",
    # Deprecated – superseded by btn_menu_confirm / btn_menu_cancel. Kept
    # in the mapping so legacy configs still validate and fall back to
    # defaults on invalid values; no longer used for dispatch.
    "btn_settings_confirm",
    "btn_settings_cancel",
    "src_btn_confirm",
    "src_btn_cancel",
)

_BUTTON_DETECT_MAP_FIELDS = (
    "map_a",
    "map_b",
    "map_x",
    "map_y",
    "map_back",
    "map_start",
    "map_lb",
    "map_rb",
    "map_dpad_up",
    "map_dpad_down",
    "map_dpad_left",
    "map_dpad_right",
)

_KEYBOARD_ACTION_FIELDS = (
    "key_move_z_up",
    "key_move_z_down",
    "key_reset",
    "key_toggle_help",
    "key_toggle_zones",
    "key_speed_down",
    "key_speed_up",
    "key_next_marker",
    "key_prev_marker",
    "key_settings",
    "key_clear_messages",  # clear operator-message cards
    "key_pathfinder",
    "key_toggle_paths",
)

# Binding groups in form order: the fields of one group may not share an input,
# and when a file or import holds a duplicate the upper field keeps it. Menu
# buttons are only read while a menu is open, so they form their own group.
GAMEPAD_ACTION_BUTTON_FORM_LABELS = (
    ("btn_reset", "Reset Marker"),
    ("btn_toggle_help", "Toggle Help"),
    ("btn_settings", "Settings Menu"),
    ("btn_speed_down", "Speed -"),
    ("btn_speed_up", "Speed +"),
    ("btn_move_z_down", "Move Z-"),
    ("btn_move_z_up", "Move Z+"),
    ("btn_next_marker", "Next Marker"),
    ("btn_prev_marker", "Prev Marker"),
    ("btn_clear_messages", "Clear Messages"),
    ("btn_toggle_zones", "Toggle Zone Overlay"),
    ("btn_toggle_paths", "Toggle Paths"),
    ("btn_pathfinder", "Pathfinder"),
)
GAMEPAD_MENU_BUTTON_FORM_LABELS = (
    ("btn_menu_confirm", "Confirm"),
    ("btn_menu_cancel", "Cancel"),
)
# Menu navigation moves the highlight with these itself, so a confirm or cancel
# bound to one could never fire.
MENU_RESERVED_BUTTONS = frozenset({"DPAD_UP", "DPAD_DOWN"})
KEYBOARD_ACTION_FORM_LABELS = (
    ("key_move_z_up", "Z+"),
    ("key_move_z_down", "Z-"),
    ("key_reset", "Reset Marker"),
    ("key_toggle_help", "Toggle Help"),
    ("key_toggle_zones", "Toggle Zone Overlay"),
    ("key_speed_down", "Speed -"),
    ("key_speed_up", "Speed +"),
    ("key_settings", "Settings Menu"),
    ("key_clear_messages", "Clear Messages"),
    ("key_next_marker", "Next Marker"),
    ("key_prev_marker", "Prev Marker"),
    ("key_pathfinder", "Pathfinder"),
    ("key_toggle_paths", "Toggle Paths"),
)
CONTROLLER_BINDING_GROUPS = tuple(
    tuple(name for name, _ in labels)
    for labels in (GAMEPAD_ACTION_BUTTON_FORM_LABELS, GAMEPAD_MENU_BUTTON_FORM_LABELS, KEYBOARD_ACTION_FORM_LABELS)
)
CONTROLLER_BINDING_FIELDS = (
    *(name for group in CONTROLLER_BINDING_GROUPS for name in group),
    "move_xy_stick",
    "marker_fader_stick",
)


@dataclass
class ControllerConfig:
    enabled: bool = True
    keyboard_enabled: bool = True
    # Default matches config.example.toml; an explicit ``true`` is honoured.
    mouse_enabled: bool = False
    # Mouse steering refinements (see input/mouse.py).
    # Cursor deadband in whole screen pixels; 0 = off (apply every move).
    mouse_hysteresis_px: int = 0
    # Glide toward the cursor target; 0 = instant (no smoothing), higher = smoother/laggier.
    mouse_smoothing: float = 0.0
    # Cap the marker's upstage (Y+) position when steering by mouse; 0 = no
    # limit. Near the camera horizon the unprojected Y runs away, so a move
    # beyond this holds the marker rather than placing it far upstage.
    mouse_max_y: float = 0.0
    # Scroll wheel adjusts marker Z height.
    mouse_wheel_z_enabled: bool = True
    mouse_wheel_invert: bool = False
    # Height change per wheel tick (m).
    mouse_wheel_z_step: float = 0.1
    # Double right-click resets the controlled marker to the default position.
    mouse_double_click_reset: bool = True
    deadzone: float = 0.15
    invert_y: bool = False
    curve: str = "logarithmic"
    # Normal mode button mappings
    btn_reset: str = "X"
    btn_toggle_help: str = "Y"
    btn_speed_down: str = "LB"
    btn_speed_up: str = "RB"
    btn_move_z_down: str = "LT"
    btn_move_z_up: str = "RT"
    # Cycle bindings (marker selection only; calibration is web-UI-only).
    btn_next_marker: str = "DPAD_RIGHT"
    btn_prev_marker: str = "DPAD_LEFT"
    # Settings menu bindings.
    btn_settings: str = "BACK"
    # Unified menu confirm/cancel, shared by source / interface
    # selection and the Settings menu.
    btn_menu_confirm: str = "A"
    btn_menu_cancel: str = "B"
    # Clear all operator-message cards. Default unbound; edge-triggered.
    btn_clear_messages: str = ""
    btn_toggle_zones: str = ""
    btn_toggle_paths: str = ""
    btn_pathfinder: str = ""
    move_xy_stick: str = "left"
    # Marker-fader integrator: which stick Y-axis (if any) drives the fader of
    # the currently-controlled marker. The bus integrates
    # ``deflection × dt × (1 / marker_fader_max_speed_s)`` per tick; the
    # deadzone + response curve apply. ``marker_fader_max_speed_s`` is the
    # seconds for full 0→1 fader travel at full deflection.
    marker_fader_stick: str = ""
    marker_fader_max_speed_s: float = 1.0
    # Deprecated confirm/cancel fields. Retained so legacy configs load;
    # validated but no longer drive dispatch.
    btn_settings_confirm: str = "A"
    btn_settings_cancel: str = "B"
    src_btn_confirm: str = "A"
    src_btn_cancel: str = "B"
    # Button detection map – corrects mislabeled hardware.
    # Each field means: "physical <button> is detected as <value>".
    map_a: str = "A"
    map_b: str = "B"
    map_x: str = "X"
    map_y: str = "Y"
    map_back: str = "BACK"
    map_start: str = "START"
    map_lb: str = "LB"
    map_rb: str = "RB"
    map_dpad_up: str = "DPAD_UP"
    map_dpad_down: str = "DPAD_DOWN"
    map_dpad_left: str = "DPAD_LEFT"
    map_dpad_right: str = "DPAD_RIGHT"
    # Raw joystick button index map – maps button label to the raw
    # hardware index detected by the wizard.  Used to build the remap
    # for raw joysticks where SDL2 IDs don't match the actual layout.
    # Empty dict means no wizard has been run (use SDL2 defaults).
    button_raw_indices: dict[str, int] = field(default_factory=dict)
    # Swap LT/RT analog trigger axes (for controllers that report them reversed)
    swap_triggers: bool = False
    # Name of the controller the wizard was last run against; empty if unset.
    mapped_controller_name: str = ""
    # SDL GUID of the controller the wizard was last run against; empty if
    # unset. More reliable than the name for detecting that the connected pad
    # differs from the one calibrated (a different model, or the same pad in a
    # non-X-input hardware mode, which enumerates with a different GUID). Two
    # pads of one model share a GUID.
    mapped_controller_guid: str = ""
    # Keyboard mappings – XY movement layout (chooses W/A/S/D vs E/S/D/F)
    key_move_layout: str = "wasd"
    # Keyboard mappings – Z movement
    key_move_z_up: str = "q"
    key_move_z_down: str = "e"
    # Keyboard mappings – discrete actions (Normal mode)
    key_reset: str = "x"
    key_toggle_help: str = "h"
    key_toggle_zones: str = "z"
    key_speed_down: str = "r"
    key_speed_up: str = "t"
    # Cycle bindings (marker selection only). Prev defaults to unbound so
    # nothing is stolen from user keymaps.
    key_next_marker: str = "Tab"
    key_prev_marker: str = ""
    # Settings menu binding. "M" avoids the WASD/IJKL clusters.
    key_settings: str = "m"
    # Clear all operator-message cards. Default unbound.
    key_clear_messages: str = ""
    key_pathfinder: str = ""
    key_toggle_paths: str = ""
    # xkb layout / variant the kiosk session starts with; blank = the system's.
    keyboard_layout: str = ""
    keyboard_variant: str = ""

    def __post_init__(self) -> None:
        """Validate configuration values."""
        self.keyboard_layout = _coerce_name(self.keyboard_layout, XKB_LAYOUT_RE)
        self.keyboard_variant = _coerce_name(self.keyboard_variant, XKB_VARIANT_RE) if self.keyboard_layout else ""
        # Mouse steering refinements – coerce so a hand-edited / imported TOML
        # can't feed a string or out-of-range value into the input loop.
        self.mouse_hysteresis_px = _coerce_int(self.mouse_hysteresis_px, 0, lo=0, hi=200)
        self.mouse_smoothing = _coerce_float(self.mouse_smoothing, 0.0, lo=0.0, hi=1.0)
        self.mouse_max_y = _coerce_float(self.mouse_max_y, 0.0, lo=0.0, hi=10000.0)
        self.mouse_wheel_z_step = _coerce_float(self.mouse_wheel_z_step, 0.1, lo=0.0, hi=10.0)
        self.mouse_wheel_z_enabled = _coerce_bool(self.mouse_wheel_z_enabled, True)
        self.mouse_wheel_invert = _coerce_bool(self.mouse_wheel_invert, False)
        self.mouse_double_click_reset = _coerce_bool(self.mouse_double_click_reset, True)
        if not 0.0 <= self.deadzone <= 1.0:
            logger.warning(
                "Invalid controller deadzone %s, clamping to [0.0, 1.0]",
                self.deadzone,
            )
            self.deadzone = max(0.0, min(1.0, self.deadzone))
        if self.curve not in VALID_CURVES:
            logger.warning(
                "Invalid controller curve %r, falling back to 'logarithmic'",
                self.curve,
            )
            self.curve = "logarithmic"
        if self.move_xy_stick not in VALID_STICKS:
            logger.warning(
                "Invalid move_xy_stick %r, falling back to 'left'",
                self.move_xy_stick,
            )
            self.move_xy_stick = "left"
        # Snap an unknown stick choice to "" (disable) rather than a working
        # default, so a typo can't route to a stick the operator didn't pick.
        self.marker_fader_stick = _coerce_choice(
            self.marker_fader_stick,
            VALID_MARKER_FADER_STICKS,
            "",
        )
        # Lower bound 0.05 s: a 0 would divide-by-zero in the integrator.
        self.marker_fader_max_speed_s = _coerce_float(
            self.marker_fader_max_speed_s,
            1.0,
            lo=0.05,
            hi=60.0,
        )
        btn_fields = (
            *_BUTTON_MAPPING_FIELDS,
            *_BUTTON_DETECT_MAP_FIELDS,
        )
        for fname in btn_fields:
            val = getattr(self, fname)
            if val not in VALID_BUTTON_NAMES:
                default = ControllerConfig.__dataclass_fields__[fname].default
                logger.warning(
                    "Invalid button name %r for %s, falling back to %r",
                    val,
                    fname,
                    default,
                )
                setattr(self, fname, default)
        for fname in ("btn_menu_confirm", "btn_menu_cancel"):
            if getattr(self, fname) in MENU_RESERVED_BUTTONS:
                default = ControllerConfig.__dataclass_fields__[fname].default
                _warn_binding_once(
                    "controller",
                    fname,
                    getattr(self, fname),
                    "Button %r for %s moves the menu highlight, falling back to %r",
                    getattr(self, fname),
                    fname,
                    default,
                )
                setattr(self, fname, default)
        if self.key_move_layout not in VALID_MOVE_LAYOUTS:
            logger.warning(
                "Invalid key_move_layout %r, falling back to 'wasd'",
                self.key_move_layout,
            )
            self.key_move_layout = "wasd"
        for fname in _KEYBOARD_ACTION_FIELDS:
            val = getattr(self, fname)
            default = ControllerConfig.__dataclass_fields__[fname].default
            if val not in VALID_KEY_NAMES:
                logger.warning(
                    "Invalid key name %r for %s, falling back to %r",
                    val,
                    fname,
                    default,
                )
                setattr(self, fname, default)
            elif val in RESERVED_MOVEMENT_KEYS:
                logger.warning(
                    "Key %r for %s collides with movement (%s); falling back to %r",
                    val,
                    fname,
                    "/".join(sorted(k.upper() for k in RESERVED_MOVEMENT_KEYS)),
                    default,
                )
                setattr(self, fname, default)
        # button_raw_indices: drop entries whose value isn't a usable int – a
        # hand-edited/imported TOML can hold a str/float that crashes button reads.
        raw_indices = self.button_raw_indices if isinstance(self.button_raw_indices, dict) else {}
        coerced_indices: dict[str, int] = {}
        for raw_name, raw_idx in raw_indices.items():
            if isinstance(raw_idx, bool):
                continue
            try:
                idx = int(raw_idx)
            except (TypeError, ValueError, OverflowError):
                continue  # OverflowError: int(inf); ValueError: int("x")/int(nan)
            # Drop a non-integral float (int() would truncate, e.g. 4.9 -> 4).
            if isinstance(raw_idx, float) and idx != raw_idx:
                continue
            coerced_indices[str(raw_name)] = idx
        self.button_raw_indices = coerced_indices
        self.swap_triggers = _coerce_bool(self.swap_triggers, False)
        self.mapped_controller_name = _coerce_str(self.mapped_controller_name, "")
        self.mapped_controller_guid = _coerce_str(self.mapped_controller_guid, "")
        for move in self.settle_bindings():
            _log_settled_binding("controller", move)

    def settle_bindings(self, prefer: Iterable[str] = ()) -> list[BindingMove[str]]:
        """Unbind every action sharing its input with an upper one in its group.

        ``prefer`` ranks ahead of form order, so a write keeps what it changed.
        Move X/Y has no unbound value: it always keeps its stick.
        """
        prefer = tuple(prefer)
        moves: list[BindingMove[str]] = []
        for group in CONTROLLER_BINDING_GROUPS:
            found = settle_duplicates({name: getattr(self, name) for name in group}, group, unbound="", prefer=prefer)
            for move in found:
                setattr(self, move.field, "")
            moves.extend(found)
        if self.marker_fader_stick and self.marker_fader_stick == f"{self.move_xy_stick}_y":
            moves.append(BindingMove("marker_fader_stick", "move_xy_stick", self.marker_fader_stick))
            self.marker_fader_stick = ""
        return moves


# 3D Mouse (6DOF) input. The six source axes are the device deflections; each
# resolves to a marker target with its own sensitivity and invert. Buttons bind
# to actions by device button index. These constants are the single source of
# truth shared by the config, the handler, the web parsers, and the validation
# rules.
MOUSE3D_AXES = ("pan_x", "pan_y", "lift", "pitch", "yaw", "roll")
MOUSE3D_AXIS_TARGETS = ("none", "x", "y", "z", "speed", "fader")
MOUSE3D_BUTTON_FIELDS = (
    "btn_reset",
    "btn_next_marker",
    "btn_prev_marker",
    "btn_speed_up",
    "btn_speed_down",
    "btn_toggle_help",
    "btn_toggle_zones",
    "btn_pathfinder",
    "btn_toggle_paths",
)
# Web-form row labels (gesture-first, config key in parens) in display order.
# Single source for the form's axis/button loops; the consistency test asserts
# they cover exactly MOUSE3D_AXES / MOUSE3D_BUTTON_FIELDS so neither can drift.
MOUSE3D_AXIS_FORM_LABELS = (
    ("pan_x", "Push left / right (pan_x)"),
    ("pan_y", "Push forward / back (pan_y)"),
    ("lift", "Pull up / push down (lift)"),
    ("pitch", "Tilt forward / back (pitch)"),
    ("yaw", "Twist / spin (yaw)"),
    ("roll", "Tilt left / right (roll)"),
)
MOUSE3D_BUTTON_FORM_LABELS = (
    ("btn_reset", "Reset marker"),
    ("btn_next_marker", "Next marker"),
    ("btn_prev_marker", "Previous marker"),
    ("btn_speed_up", "Speed up"),
    ("btn_speed_down", "Speed down"),
    ("btn_toggle_help", "Toggle help"),
    ("btn_toggle_zones", "Toggle zones"),
    ("btn_pathfinder", "Pathfinder"),
    ("btn_toggle_paths", "Toggle paths"),
)


@dataclass
class Mouse3DConfig:
    """Optional ``[mouse3d]`` section – 3D Mouse (3Dconnexion 6DOF) input.

    Disabled by default. Each of the six source axes resolves to a marker
    target (``none``/``x``/``y``/``z``/``speed``/``fader``) with its own
    sensitivity, deadzone and invert; the shared ``curve`` reuses the gamepad
    shaping.
    Buttons bind to actions by device button index (``-1`` = unbound).
    """

    enabled: bool = False
    curve: str = "logarithmic"

    map_pan_x: str = "x"
    sens_pan_x: float = 1.0
    deadzone_pan_x: float = 0.05
    invert_pan_x: bool = False
    map_pan_y: str = "y"
    sens_pan_y: float = 1.0
    deadzone_pan_y: float = 0.05
    invert_pan_y: bool = False
    map_lift: str = "z"
    sens_lift: float = 0.3
    deadzone_lift: float = 0.3
    invert_lift: bool = False
    map_pitch: str = "none"
    sens_pitch: float = 1.0
    deadzone_pitch: float = 0.1
    invert_pitch: bool = False
    map_yaw: str = "speed"
    sens_yaw: float = 1.0
    deadzone_yaw: float = 0.3
    invert_yaw: bool = False
    map_roll: str = "none"
    sens_roll: float = 1.0
    deadzone_roll: float = 0.1
    invert_roll: bool = False

    btn_reset: int = -1
    btn_next_marker: int = 0
    btn_prev_marker: int = 1
    btn_speed_up: int = -1
    btn_speed_down: int = -1
    btn_toggle_help: int = -1
    btn_toggle_zones: int = -1
    btn_pathfinder: int = -1
    btn_toggle_paths: int = -1

    def __post_init__(self) -> None:
        self.enabled = _coerce_bool(self.enabled, False)
        self.curve = _coerce_choice(self.curve, VALID_CURVES, "logarithmic")
        # The field declarations are the single source of per-axis defaults; the
        # coercion fallback reads them back so an absent-key default and an
        # invalid-value fallback can't drift apart.
        for axis in MOUSE3D_AXES:
            setattr(
                self,
                f"map_{axis}",
                _coerce_choice(
                    getattr(self, f"map_{axis}"),
                    MOUSE3D_AXIS_TARGETS,
                    _field_default(self, f"map_{axis}"),
                ),
            )
            setattr(
                self,
                f"sens_{axis}",
                _coerce_float(getattr(self, f"sens_{axis}"), _field_default(self, f"sens_{axis}"), lo=0.0, hi=10.0),
            )
            setattr(
                self,
                f"deadzone_{axis}",
                _coerce_float(
                    getattr(self, f"deadzone_{axis}"), _field_default(self, f"deadzone_{axis}"), lo=0.0, hi=1.0
                ),
            )
            setattr(
                self,
                f"invert_{axis}",
                _coerce_bool(getattr(self, f"invert_{axis}"), _field_default(self, f"invert_{axis}")),
            )
        # Invalid / out-of-range button index falls back to unbound (-1) rather
        # than rebinding to a default index the operator didn't choose.
        for btn in MOUSE3D_BUTTON_FIELDS:
            setattr(self, btn, _coerce_int(getattr(self, btn), -1, lo=-1))
        for move in self.settle_bindings():
            _log_settled_binding("mouse3d", move)

    def settle_bindings(self, prefer: Iterable[str] = ()) -> list[BindingMove[int]]:
        """Unbind every action sharing its button with an upper one; ``prefer`` ranks first."""
        values = {name: getattr(self, name) for name in MOUSE3D_BUTTON_FIELDS}
        moves = settle_duplicates(values, MOUSE3D_BUTTON_FIELDS, unbound=-1, prefer=prefer)
        for move in moves:
            setattr(self, move.field, -1)
        return moves


# The systemd unit restarted after a successful update. Also the recovery
# fallback when a box's persisted ``update_service_name`` is invalid.
DEFAULT_UPDATE_SERVICE_NAME = "openfollow"


# Single source of truth for the PSN identity defaults so the field defaults
# and the ``__post_init__`` non-string fallbacks can't drift apart.
_DEFAULT_PSN_SYSTEM_NAME = "OpenFollow"
_DEFAULT_PSN_MCAST_IP = "236.10.10.10"
# Mirrors ``media_store.DEFAULT_SELECTED_MEDIA`` (the Stage default). Kept as a
# literal here – not imported – so this early-loaded module doesn't pull in the
# video package; a test pins the two together. The constant dedupes the field
# default and the ``__post_init__`` fallback.
_DEFAULT_SELECTED_MEDIA = "default:stage"

# A camera's name as macOS reports it; anything longer is not one.
AVF_DEVICE_NAME_MAX_LEN = 128


def clean_avf_device_name(raw: object) -> str:
    """A stored camera name: text only, no control or direction characters, at most ``AVF_DEVICE_NAME_MAX_LEN``."""
    if not isinstance(raw, str):
        return ""
    return CONTROL_CHARS_RE.sub("", raw).strip()[:AVF_DEVICE_NAME_MAX_LEN].strip()


def clean_avf_unique_id(raw: object) -> str:
    """A stored camera ID: text only, trimmed, no control or direction characters."""
    if not isinstance(raw, str):
        return ""
    return CONTROL_CHARS_RE.sub("", raw).strip()


@dataclass
class NetworkConfig:
    """Optional ``[network]`` section.

    ``backend`` forces the adapter chosen by :mod:`openfollow.network.detect`.
    Useful in CI or on dev hosts that have both NM and dhcpcd installed but
    want a deterministic choice. ``"auto"`` (the default) probes at startup.
    """

    backend: str = "auto"  # "auto" | "nm" | "dhcpcd" | "psutil"

    def __post_init__(self) -> None:
        if not isinstance(self.backend, str):
            self.backend = "auto"
        choice = self.backend.strip().lower()
        if choice not in {"auto", "nm", "dhcpcd", "psutil"}:
            choice = "auto"
        self.backend = choice


OPERATOR_SCREENS: tuple[str, ...] = ("auto", "cairo")


@dataclass
class DisplayConfig:
    """Optional ``[display]`` section, set in ``config.toml`` only.

    ``operator_screen`` "auto" runs the GL Operator Screen where the platform supports it and Cairo
    elsewhere; "cairo" keeps Cairo on this station. Read at startup.
    """

    operator_screen: str = "auto"

    def __post_init__(self) -> None:
        choice = self.operator_screen.strip().lower() if isinstance(self.operator_screen, str) else ""
        self.operator_screen = _coerce_choice(choice, OPERATOR_SCREENS, "auto")


# The General tab's Features list, in display order. A feature that is off is
# hidden in the web UI and does not run; its own settings are kept.
FEATURE_KEYS: tuple[str, ...] = (
    "zones",
    "midi",
    "psn_input",
    "lens_suggestions",
    "detection",
    "rttrpm",
    "paths",
    "ground_grid",
    "touch_remote",
)
EXPERIMENTAL_FEATURES: frozenset[str] = frozenset(
    {"lens_suggestions", "detection", "rttrpm", "paths", "ground_grid", "touch_remote"}
)
DEFAULT_SHOWN_FEATURES: tuple[str, ...] = tuple(key for key in FEATURE_KEYS if key not in EXPERIMENTAL_FEATURES)
# Gated like any feature but not offered: no Features row, no web route turns one on.
# Only ``shown_features`` in config.toml (or an import or push carrying it) does.
CONFIG_ONLY_FEATURES: frozenset[str] = frozenset({"paths"})
OFFERED_FEATURES: tuple[str, ...] = tuple(key for key in FEATURE_KEYS if key not in CONFIG_ONLY_FEATURES)


def _coerce_feature_list(value: Any) -> list[str]:
    """Known feature keys from ``value`` in ``FEATURE_KEYS`` order; anything but a list is the default."""
    if not isinstance(value, (list, tuple)):
        return list(DEFAULT_SHOWN_FEATURES)
    named = {entry.strip().lower() for entry in value if isinstance(entry, str)}
    return [key for key in FEATURE_KEYS if key in named]


def migrate_ui_keys(ui_data: dict[str, Any]) -> None:
    """Map ``show_experimental_features`` onto ``shown_features`` when that is absent; drop the old key."""
    if "show_experimental_features" not in ui_data:
        return
    shown_all = _coerce_bool(ui_data.pop("show_experimental_features"), False)
    if shown_all and "shown_features" not in ui_data:
        ui_data["shown_features"] = list(OFFERED_FEATURES)


def feature_on(cfg: Any, key: str) -> bool:
    """Whether ``cfg`` has feature ``key`` on; a config without ``[ui]`` reads the defaults."""
    shown = getattr(getattr(cfg, "ui", None), "shown_features", DEFAULT_SHOWN_FEATURES)
    return key in shown


class _Switched(Protocol):
    enabled: bool


_SwitchedT = TypeVar("_SwitchedT", bound=_Switched)


def feature_gated(section: _SwitchedT, cfg: Any, key: str) -> _SwitchedT:
    """``section`` as its subsystem runs it: switched off (``enabled``) while feature ``key`` is off."""
    if not section.enabled or feature_on(cfg, key):
        return section
    gated = copy.copy(section)
    gated.enabled = False
    return gated


def effective_lens(cfg: Any) -> tuple[float, float, float, float]:
    """The lens everything draws and maps with, ``(k1, k2, cx, cy)``."""
    cam = cfg.camera
    return cam.lens_k1, cam.lens_k2, cam.lens_cx, cam.lens_cy


@dataclass
class UiConfig:
    """Optional ``[ui]`` section – operator display preferences.

    ``unit_system`` drives length/speed rendering in the web UI and overlay.
    Internal storage, OSC, PSN/RTTrPM/OTP and template files are always
    metric – only display formatting and form-input parsing change. Stored as
    a plain string so TOML round-trips; consumers wrap it in ``UnitSystem``.
    """

    unit_system: str = "metric"  # "metric" | "imperial"

    # Features that are on; every subsystem gated by one asks ``feature_on``.
    shown_features: list[str] = field(default_factory=lambda: list(DEFAULT_SHOWN_FEATURES))
    # Config-only: shows the developer's views in the web UI (the Lens step's edge view).
    developer_mode: bool = False

    def __post_init__(self) -> None:
        valid = {m.value for m in UnitSystem}
        if not isinstance(self.unit_system, str):
            self.unit_system = "metric"
        choice = self.unit_system.strip().lower()
        self.unit_system = choice if choice in valid else "metric"
        self.shown_features = _coerce_feature_list(self.shown_features)
        self.developer_mode = _coerce_bool(self.developer_mode, False)

    def feature_on(self, key: str) -> bool:
        """Whether feature ``key`` is on."""
        return key in self.shown_features


NETWORK_MODE_SIMPLE = "simple"
NETWORK_MODE_ADVANCED = "advanced"
NETWORK_MODES = (NETWORK_MODE_SIMPLE, NETWORK_MODE_ADVANCED)


# Every per-plane interface pin, plus the web UI's: (form key, owning section or
# None for top level, field, the Assignment panel's name for it). Each OSC
# destination's ``source_iface`` is one more, keyed by its id.
PLANE_PIN_FIELDS: tuple[tuple[str, str | None, str, str], ...] = (
    ("otp_output.source_iface", "otp_output", "source_iface", "OTP output"),
    ("rttrpm_output.source_iface", "rttrpm_output", "source_iface", "RTTrPM output"),
    ("osc.listen_iface", "osc", "listen_iface", "OSC input"),
    ("video_input_iface", None, "video_input_iface", "Video input"),
    ("web_bind_iface", None, "web_bind_iface", "Web UI"),
)


def plane_pin_key(dest_id: str) -> str:
    """The form key of an OSC destination's pin."""
    return f"osc_destinations.{dest_id}.source_iface"


def plane_pins(cfg: AppConfig) -> list[tuple[str, str]]:
    """Every per-plane interface pin that is set, plus ``web_bind_iface``, as ``(key, iface)``.

    ``psn_source_iface`` is the station interface, not a per-plane pin, and
    ``web_bind`` is config-only.
    """
    pins = [(key, getattr(getattr(cfg, owner) if owner else cfg, name)) for key, owner, name, _ in PLANE_PIN_FIELDS]
    # In the Assignment panel's order: the destinations follow OSC input.
    after_osc = [key for key, *_ in PLANE_PIN_FIELDS].index("osc.listen_iface") + 1
    pins[after_osc:after_osc] = [
        (plane_pin_key(dest.id), dest.source_iface) for dest in cfg.osc_destinations.destinations
    ]
    return [(key, value) for key, value in pins if isinstance(value, str) and value.strip()]


def enforce_network_mode(cfg: AppConfig) -> None:
    """Clear every per-plane pin and ``web_bind_iface`` in Simple: Simple never holds a hidden pin."""
    if cfg.network_mode != NETWORK_MODE_SIMPLE:
        return
    for _key, owner, name, _label in PLANE_PIN_FIELDS:
        setattr(getattr(cfg, owner) if owner else cfg, name, "")
    for dest in cfg.osc_destinations.destinations:
        dest.source_iface = ""


def _coerce_network_mode(cfg: AppConfig) -> str:
    """``network_mode`` read case- and space-blind; anything else is the mode the pins imply.

    That is also how a config without the field starts: Advanced when it pins a
    plane or the web UI (VLANs and labels do not count), else Simple. A typo
    must never read as Simple and erase every pin.
    """
    raw = cfg.network_mode.strip().lower() if isinstance(cfg.network_mode, str) else None
    if raw in NETWORK_MODES:
        return str(raw)
    return NETWORK_MODE_ADVANCED if plane_pins(cfg) else NETWORK_MODE_SIMPLE


def network_mode_unsaved(path: str) -> bool:
    """Whether the config file at ``path`` reads but records no ``network_mode``.

    A file that does not read says nothing: the config may have come from the
    backup, and saving over the original would discard what the operator wrote.
    """
    try:
        with open(path, "rb") as f:
            raw = f.read()
        return bool(raw.strip()) and "network_mode" not in tomllib.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False


def is_simple_mode(cfg: Any) -> bool:
    """Whether ``cfg`` is in Simple network mode; anything without the field reads as Advanced."""
    return getattr(cfg, "network_mode", NETWORK_MODE_ADVANCED) == NETWORK_MODE_SIMPLE


@dataclass
class AppConfig:
    # Video source
    video_source_type: str = "testpattern"  # "testpattern" | "ndi" | "srt" | "rtp" | "rtsp" | ...
    ndi_source_name: str = ""
    srt_host: str = "srt://0.0.0.0:5000"
    srt_passphrase: str = ""
    rtp_url: str = "rtp://0.0.0.0:5004"
    rtp_encoding: str = "H264"
    rtsp_url: str = "rtsp://0.0.0.0:554/stream"
    rtsp_user: str = ""
    rtsp_password: str = ""
    picam_camera_name: str = ""
    picam_width: int = 1920
    picam_height: int = 1080
    picam_framerate: int = 30
    v4l2_device: str = "/dev/video0"
    v4l2_render_resolution: str = "1080p"
    v4l2_framerate: int = 30
    avf_unique_id: str = ""
    # That camera's name when it was chosen; names it while it is not connected.
    avf_device_name: str = ""
    avf_device_index: int = -1
    avf_width: int = 1920
    avf_height: int = 1080
    avf_framerate: int = 30
    # Media Gallery selection; see ``_DEFAULT_SELECTED_MEDIA`` above.
    testpattern_selected_media: str = _DEFAULT_SELECTED_MEDIA

    # Video recovery. ``stall_timeout`` is the silent-stall watchdog of every
    # input – seconds an established stream may deliver no frames before it's
    # torn down and reconnected (0 = off). ``heal_interval`` is how often an
    # RTSP/SRT/RTP feed parked on the no-signal placeholder re-probes its URL
    # so it recovers on its own after the source/network returns (0 = off).
    stall_timeout: float = 3.0
    heal_interval: float = 5.0
    # Interface the network video inputs (SRT, RTSP, RTP) use, by name. Blank
    # follows ``psn_source_iface``, like the outputs.
    video_input_iface: str = ""

    # Window
    window_width: int = 1280
    window_height: int = 720

    # PSN network
    psn_system_name: str = _DEFAULT_PSN_SYSTEM_NAME
    psn_mcast_ip: str = _DEFAULT_PSN_MCAST_IP
    # Pin the interface (eth0/wlan0), not an IP: the iface name is stable
    # across DHCP renewals and venue changes. Empty = auto-detect the primary
    # outbound IPv4 at startup.
    psn_source_iface: str = ""

    # Web config UI
    web_port: int = 80
    web_pin: str = ""
    # Listen address for the web UI. "" = auto (pin to the PSN source
    # interface's IP when one is set, else all interfaces); an explicit IP
    # pins the bind; "0.0.0.0" forces all interfaces. The server always also
    # serves loopback so the on-screen browser keeps working.
    web_bind: str = ""
    # Interface the web UI listens on. Blank = every interface. An explicit
    # ``web_bind`` address outranks it. Unlike every other plane this one
    # falls back to the wildcard bind when the pin cannot be resolved: a
    # silent output is diagnosable, an unreachable config UI is not.
    web_bind_iface: str = ""
    # Operator labels for this station's interfaces, by interface name
    # ("enx9c69d3ac16ab" -> "Lighting"). Device-local: they name this box's adapters.
    interface_labels: dict[str, str] = field(default_factory=dict)
    # This station's name on a venue's DNS. Blank = only its ``<host>.local`` name.
    # Device-local: it names this box.
    station_fqdn: str = ""
    # "simple": the station interface carries every plane; "advanced": per-plane
    # pins. Device-local. A config without it starts in the mode its pins imply.
    network_mode: str = NETWORK_MODE_SIMPLE

    # Web-triggered update settings (signed-.deb GitHub-release installer)
    update_github_repo: str = "openfollowapp/openfollow"
    update_service_name: str = DEFAULT_UPDATE_SERVICE_NAME
    # Offer pre-release builds, not just stable releases. Hidden (config-only),
    # off by default – set ``update_include_prereleases = true`` in config.toml.
    update_include_prereleases: bool = False

    # On startup and whenever the device IP changes, if the LAN has internet:
    # auto-check GitHub for a newer release (drives the Software Update box) and
    # sync the system clock from a trusted NTP server (Pis have no RTC and
    # drift). Both are gated, fail silently offline, and default on.
    auto_update_check: bool = True
    auto_time_sync: bool = True
    time_sync_server: str = "ptbtime1.ptb.de"

    # Markers – id 0 is reserved as "ignored" on the PSN wire, so
    # the default selection is empty (operator picks via the catalog UI).
    controlled_marker_ids: list[int] = field(default_factory=list)
    viewer_marker_ids: list[int] = field(default_factory=list)
    # Per-marker move-speed overrides; ``MarkerConfig.move_speed`` is the
    # fallback for a marker with no entry.
    marker_move_speeds: dict[int, float] = field(default_factory=dict)

    # Shared marker catalog persisted in a separate file so the
    # multicast sync layer can rewrite it without touching this
    # station's per-device config. Default lives next to config.toml.
    markers_catalog_path: str = "markers.toml"

    # Stable per-station identifier (UUID hex). Bootstrapped on first
    # run when blank – used by the catalog sync to ignore our own
    # beacons and by ``derive_station_name`` to seed
    # ``psn_system_name`` with a memorable default.
    station_id: str = ""

    # Sub-configs
    camera: CameraConfig = field(default_factory=CameraConfig)
    grid: GridConfig = field(default_factory=GridConfig)
    ground_grid: GroundGridConfig = field(default_factory=GroundGridConfig)
    marker: MarkerConfig = field(default_factory=MarkerConfig)
    controller: ControllerConfig = field(default_factory=ControllerConfig)
    mouse3d: Mouse3DConfig = field(default_factory=Mouse3DConfig)
    osc: OscConfig = field(default_factory=OscConfig)
    operator_messages: OperatorMessagesConfig = field(default_factory=OperatorMessagesConfig)
    touch_remote: TouchRemoteConfig = field(default_factory=TouchRemoteConfig)
    otp_output: OtpOutputConfig = field(default_factory=OtpOutputConfig)
    rttrpm_output: RttrpmOutputConfig = field(default_factory=RttrpmOutputConfig)
    osc_transmitters: OscTransmittersConfig = field(default_factory=OscTransmittersConfig)
    osc_destinations: OscDestinationsConfig = field(default_factory=OscDestinationsConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    trigger_zones: TriggerZonesConfig = field(default_factory=TriggerZonesConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    midi: MidiConfig = field(default_factory=MidiConfig)
    virtual_faders: VirtualFadersConfig = field(
        default_factory=VirtualFadersConfig,
    )
    network: NetworkConfig = field(default_factory=NetworkConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    ui: UiConfig = field(default_factory=UiConfig)

    def __post_init__(self) -> None:
        # Top-level scalars aren't covered by sub-dataclass ``__post_init__``;
        # normalise here so a hand-edited bad type can't wedge hot-reload.
        self.window_width = _coerce_int(self.window_width, 1280, lo=1)
        self.window_height = _coerce_int(self.window_height, 720, lo=1)
        # Video recovery timers: non-negative seconds, 0 disables.
        self.stall_timeout = _coerce_float(self.stall_timeout, 3.0, lo=0.0)
        self.heal_interval = _coerce_float(self.heal_interval, 5.0, lo=0.0)
        self.web_port = _coerce_int(self.web_port, 80, lo=1, hi=65535)
        # Normalise web_pin on load so a hand-edited TOML matches the web-save
        # path; the digit/length contract is enforced where it's set.
        if not isinstance(self.web_pin, str):
            self.web_pin = ""
        self.web_pin = self.web_pin.strip()
        if not isinstance(self.web_bind, str):
            self.web_bind = ""
        self.web_bind = self.web_bind.strip()
        if not isinstance(self.web_bind_iface, str):
            self.web_bind_iface = ""
        self.web_bind_iface = self.web_bind_iface.strip()
        if not isinstance(self.video_input_iface, str):
            self.video_input_iface = ""
        self.video_input_iface = self.video_input_iface.strip()
        self.interface_labels = normalize_labels(self.interface_labels)
        self.station_fqdn = normalize_fqdn(self.station_fqdn)
        self.network_mode = _coerce_network_mode(self)
        enforce_network_mode(self)
        reconcile_simple_corners(self)
        # Strip ``psn_source_iface`` so whitespace doesn't look like a value
        # change each load and trigger a needless rebind cycle.
        if not isinstance(self.psn_source_iface, str):
            self.psn_source_iface = ""
        self.psn_source_iface = self.psn_source_iface.strip()
        # Strip ``psn_mcast_ip``: trailing whitespace both spuriously rebinds
        # each load and breaks the multicast bind (whitespace-tainted IP).
        if not isinstance(self.psn_mcast_ip, str):
            self.psn_mcast_ip = _DEFAULT_PSN_MCAST_IP
        self.psn_mcast_ip = self.psn_mcast_ip.strip()
        # Canonicalise ``psn_system_name`` here too (not just in live-apply) so
        # startup ``init_psn`` / ``init_otp`` broadcast the same value.
        if not isinstance(self.psn_system_name, str):
            self.psn_system_name = _DEFAULT_PSN_SYSTEM_NAME
        # Cap to match the ``psn_system_name`` FieldRule: a hand-edited TOML
        # must not bypass the bound the web form enforces, and the value sizes
        # the marker-catalog beacon envelope.
        self.psn_system_name = self.psn_system_name.strip()[:64] or _DEFAULT_PSN_SYSTEM_NAME
        # Normalise the Media Gallery selection: a hand-edited non-string (or a
        # blank) must never reach ``media_store.resolve`` / ``re.match`` – which
        # raise ``TypeError`` on a non-string and would crash pipeline build and
        # ``get_source_label`` instead of degrading to Stage. A well-typed but
        # unknown id is left as-is and falls back to Stage at resolve time.
        if not isinstance(self.testpattern_selected_media, str):
            self.testpattern_selected_media = _DEFAULT_SELECTED_MEDIA
        self.testpattern_selected_media = self.testpattern_selected_media.strip() or _DEFAULT_SELECTED_MEDIA
        # Stream credentials. A hand-edited non-string would reach the element's
        # ``set_property`` as-is, and - worse - a bare ``0`` reads as falsy, so
        # the login would silently drop back to whatever is in the URL. The
        # username is stripped like any other identifier; the two secrets keep
        # their edge whitespace, which can be part of the value and is invisible
        # in a password field (``ConfigField(strip=False)`` makes the same
        # promise on the web-save path).
        if not isinstance(self.rtsp_user, str):
            self.rtsp_user = ""
        self.rtsp_user = self.rtsp_user.strip()
        if not isinstance(self.rtsp_password, str):
            self.rtsp_password = ""
        if not isinstance(self.srt_passphrase, str):
            self.srt_passphrase = ""
        # The build refuses an ID no listed camera matches, and an unnamed camera shows its ID on
        # the HUD; the USB camera's web-save path cleans both fields the same way.
        self.avf_unique_id = clean_avf_unique_id(self.avf_unique_id)
        self.avf_device_name = clean_avf_device_name(self.avf_device_name)
        self.avf_device_index = _coerce_int(self.avf_device_index, -1, lo=-1)
        self.normalise_startup_settings()
        # TOML produces ``dict[str, Any]``; the runtime contract is
        # ``dict[int, float]`` keyed by marker_id. Drop pairs with a non-int
        # key or non-finite/negative value.
        # pragma: no branch – TOML's table parser always materialises
        # ``[marker_move_speeds]`` as a dict; the guard is defensive
        # against direct ``AppConfig(marker_move_speeds=<bad-type>)``
        # construction in tests or future hand-built kwargs.
        if not isinstance(self.marker_move_speeds, dict):  # pragma: no branch
            self.marker_move_speeds = {}  # pragma: no cover
        coerced: dict[int, float] = {}
        for raw_key, raw_value in self.marker_move_speeds.items():
            # Reject ``bool`` keys: ``int(True)`` is ``1`` and would clobber
            # marker 1. TOML can't express bool keys but JSON payloads can.
            if isinstance(raw_key, bool):
                continue
            try:
                key = int(raw_key)
            except (TypeError, ValueError):
                continue
            # Marker id 0 is the reserved "ignored" wire sentinel and never
            # appears in the selection lists; a stored speed for it (or a
            # negative id) is dead state.
            if key < 1:
                continue
            # Inline float coercion so an unparseable value drops the pair
            # entirely rather than substituting a fallback that masks typos.
            try:
                value = float(raw_value)
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(value) or value < 0.0:
                continue
            coerced[key] = value
        self.marker_move_speeds = coerced

    def normalise_startup_settings(self) -> None:
        """Coerce the update and time-sync settings read at startup, so a hand-edited TOML or an
        import can't feed a non-bool / non-string into the updater or the online-sync worker."""
        self.update_include_prereleases = _coerce_bool(self.update_include_prereleases, False)
        self.auto_update_check = _coerce_bool(self.auto_update_check, True)
        self.auto_time_sync = _coerce_bool(self.auto_time_sync, True)
        self.time_sync_server = _coerce_str(self.time_sync_server, "ptbtime1.ptb.de").strip() or "ptbtime1.ptb.de"


# ---------------------------------------------------------------------------
# TOML I/O
# ---------------------------------------------------------------------------


def _filter_known(cls: type, data: dict[str, Any]) -> dict[str, Any]:
    """Strip keys that don't match any field on *cls* (stale TOML entries)."""
    known = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in known}


# Former camera settings a Camera & Grid template saved by an earlier build may carry.
_FORMER_CAMERA_KEYS = frozenset({"sensor_width_mm", "focal_length_mm"})


def without_former_camera_keys(camera: dict[str, Any]) -> dict[str, Any]:
    """*camera* without its former settings; any other unknown key still fails the dataclass."""
    return {k: v for k, v in camera.items() if k not in _FORMER_CAMERA_KEYS}


_SUB_CONFIG_MAP: dict[str, type] = {
    "camera": CameraConfig,
    "grid": GridConfig,
    "ground_grid": GroundGridConfig,
    "marker": MarkerConfig,
    "controller": ControllerConfig,
    "mouse3d": Mouse3DConfig,
    "osc": OscConfig,
    "operator_messages": OperatorMessagesConfig,
    "touch_remote": TouchRemoteConfig,
    "otp_output": OtpOutputConfig,
    "rttrpm_output": RttrpmOutputConfig,
    "osc_transmitters": OscTransmittersConfig,
    "osc_destinations": OscDestinationsConfig,
    "detection": DetectionConfig,
    "trigger_zones": TriggerZonesConfig,
    "paths": PathsConfig,
    "midi": MidiConfig,
    "virtual_faders": VirtualFadersConfig,
    "network": NetworkConfig,
    "display": DisplayConfig,
    "ui": UiConfig,
}


# Name of the shipped, git-tracked defaults file. Sits next to the live
# ``config.toml`` (which is gitignored). On first run we copy this to the
# live path so the operator has a real file to inspect/edit, and so future
# updates never have a "your local changes would be overwritten" conflict.
_CONFIG_EXAMPLE_FILENAME = "config.example.toml"

# Upper bound for the legacy ``num_markers`` → ``controlled_marker_ids`` expansion.
# Far beyond any real show; caps ``list(range(num_markers))`` so a hand-edited
# huge value can't OOM the boot path.
_MAX_BOOTSTRAP_MARKERS = 1024


def bootstrap_config_if_missing(config_path: str) -> bool:
    """Copy ``config.example.toml`` into place when the live config is absent.

    Returns ``True`` if a copy was made, ``False`` otherwise (file already
    exists, or no example shipped alongside it). Safe to call repeatedly.

    The example is looked up in the same directory as ``config_path`` –
    i.e. the operator's working directory, which on a Pi deploy is the
    repo root. Tests pointing at ``tmp_path`` get a no-op, which keeps
    the missing-file path through ``load_config`` exercised.
    """
    if os.path.exists(config_path):
        return False
    target_dir = os.path.dirname(config_path) or "."
    example = os.path.join(target_dir, _CONFIG_EXAMPLE_FILENAME)
    if not os.path.exists(example):
        return False
    with open(example, "rb") as src:
        data = src.read()
    # config.toml carries the web_pin secret. Create it O_EXCL with mode 0o600
    # from the start, then write the example bytes – a plain copyfile creates
    # the file under the process umask (commonly 0o644 = world-readable),
    # leaving the PIN exposed in the window before the first save_config.
    # O_EXCL also closes the check-then-create race with a concurrent writer.
    try:
        fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Lost the race to another bootstrapper; its file stands.
        return False
    with os.fdopen(fd, "wb") as dst:
        dst.write(data)
    logger.info("Bootstrapped %s from %s.", config_path, example)
    return True


def restore_empty_config(path: str) -> None:
    """At startup, put the backup in place of an empty config file, or remove it for a first run.

    An empty file holds nothing to keep, unlike an unreadable one, which may
    hold hand edits and is left alone. The backup is copied byte for byte and
    stays: ``save_config`` would snapshot the empty file over it.
    """
    with config_write_lock:
        try:
            with open(path, "rb") as f:
                if f.read().strip():
                    return
        except OSError:
            return
        backup = _readable_backup(path)
        try:
            if backup is None:
                os.unlink(path)
            else:
                _replace_file(Path(path), backup[0])
        except OSError:
            logger.exception("Config file %s is empty and could not be restored.", path)
            return
    if backup is None:
        logger.warning("Config file %s is empty and has no readable backup – starting as a first run.", path)
    else:
        logger.warning("Config file %s is empty – restored it from %s.bak.", path, path)


def _readable_backup(path: str) -> tuple[bytes, AppConfig] | None:
    """The ``<path>.bak`` snapshot ``save_config`` writes, when it reads whole."""
    try:
        return _read_whole(path + ".bak")
    except ConfigFileUnreadableError:
        return None


def _replace_file(target: Path, data: bytes) -> None:
    """Write *data* to a 0o600 temp file beside *target*, fsync it and rename it into place."""
    directory = target.parent if str(target.parent) else Path(".")
    fd, tmp_path = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(directory))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise
    _fsync_dir(directory)


# Which (config path, marker ids) have been logged as newly viewed, so a config reloaded on every
# request says it once per process.
_LOGGED_UNVIEWED: set[tuple[str, tuple[int, ...]]] = set()


def marker_id_list(raw: Any, *, path: str, field_name: str) -> list[int]:
    """A selection list as it is kept: positive int marker ids in first-seen order.

    Marker id 0 is reserved as "ignored" on the PSN wire, so it never gets past ``Marker.__init__``. A
    duplicate would render two marker cards and register the marker twice with PSN / RTTrPM. Anything
    but a list reads as ``[]``: ``init_markers`` iterates it. *path* names the source in the log.
    """
    if not isinstance(raw, list):
        logger.warning("%s: %s is %s, not a list – coercing to []", path, field_name, type(raw).__name__)
        return []
    kept: list[int] = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        if value < 1:
            logger.warning("%s: dropping reserved marker id %r from %s", path, value, field_name)
            continue
        if value not in kept:
            kept.append(value)
    return kept


def viewed_with_controlled(controlled: list[int], viewer: list[int]) -> list[int]:
    """The viewed markers with every controlled one added: a marker this station controls is always
    one it views, so it keeps its HUD card and counts in the trigger zones."""
    return list(viewer) + [mid for mid in controlled if mid not in viewer]


class ConfigFileUnreadableError(OSError):
    """The config file exists but is empty or does not read: nothing may be written over it."""

    def __init__(self, message: str, path: str) -> None:
        super().__init__(message)
        self.path = path


class ConfigFileEmptyError(ConfigFileUnreadableError):
    """The config file holds only whitespace, which would load as every default."""


# The state of each config file whose recovery was last logged; a web request reads it every time.
_RECOVERY_LOGGED: dict[str, tuple[int, ...]] = {}


def _file_state(path: str) -> tuple[int, ...]:
    try:
        st = os.stat(path)
    except OSError:
        return ()
    return (st.st_ino, st.st_mtime_ns, st.st_size)


def _log_recovery(path: str, level: int, message: str, *args: object, exc_info: BaseException | None = None) -> None:
    """Log how a file that did not read was recovered, once until the file changes."""
    state = _file_state(path)
    if _RECOVERY_LOGGED.get(path) == state:
        return
    _RECOVERY_LOGGED[path] = state
    logger.log(level, message, path, *args, exc_info=exc_info)


def load_config(path: str = "config.toml", *, strict: bool = False, missing_ok: bool = True) -> AppConfig:
    """Load config from a TOML file.

    Args:
        path: Path to the TOML file.
        strict: When True, a file that does not read whole raises
            ``ConfigFileUnreadableError`` (``ConfigFileEmptyError`` for an empty
            one) instead of being recovered; missing files still return
            defaults. Used by hot-reload, which must not replace the running
            config with defaults.
        missing_ok: With *strict*, False raises ``FileNotFoundError`` for a
            missing file instead of returning defaults.

    On the non-strict path, a missing file or one that does not read whole (see
    ``_read_whole``) is recovered from the ``config.toml.bak`` snapshot,
    degrading to defaults when the backup does not read whole either; it never
    raises. A caller that writes the config back reads it with
    :func:`load_config_for_save` instead.

    Returns:
        Populated ``AppConfig``; defaults for any missing fields.
    """
    try:
        whole = _read_whole(path)
    except ConfigFileUnreadableError as error:
        if strict:
            raise
        return _recovered(path, error)
    if whole is None:
        if not strict:
            return _recovered(path, None)
        if not missing_ok:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path)
        return AppConfig()
    _RECOVERY_LOGGED.pop(path, None)
    return whole[1]


def _recovered(path: str, error: ConfigFileUnreadableError | None) -> AppConfig:
    """The config for a file that is missing (*error* ``None``) or does not read: the backup, else defaults."""
    backup = _readable_backup(path)
    if error is None:
        if backup is not None:
            _log_recovery(path, logging.WARNING, "Config file %s is missing – recovered from %s.bak.", path)
            return backup[1]
        return AppConfig()
    if isinstance(error, ConfigFileEmptyError):
        if backup is None:
            _log_recovery(path, logging.WARNING, "Config file %s is empty – using defaults.")
            return AppConfig()
        _log_recovery(path, logging.WARNING, "Config file %s is empty – recovered from %s.bak.", path)
        return backup[1]
    if backup is None:
        _log_recovery(path, logging.ERROR, "Failed to read/parse config file %s – using defaults.", exc_info=error)
        return AppConfig()
    _log_recovery(
        path, logging.WARNING, "Config file %s could not be read/parsed – recovered from %s.bak.", path, exc_info=error
    )
    return backup[1]


def _config_from_data(path: str, data: dict[str, Any]) -> AppConfig:
    """Build the config a parsed config file holds; *path* only names it in what is logged."""
    # Backward compatibility: fall back to num_markers if new fields absent.
    # Coerce the raw TOML types – this is the legacy-upgrade path, so a
    # hand-edited / old config can carry a non-int ``num_markers`` (range()
    # TypeError), a huge int (massive list allocation), or a non-list
    # ``controlled_marker_ids`` (list() TypeError). The block runs outside the
    # parse try/except, so any raise here crashes boot on the non-strict path.
    if "controlled_marker_ids" not in data:
        num_markers = _coerce_int(data.get("num_markers", 1), 1, lo=0, hi=_MAX_BOOTSTRAP_MARKERS)
        data["controlled_marker_ids"] = list(range(num_markers))
    if "viewer_marker_ids" not in data:
        raw_controlled = data["controlled_marker_ids"]
        data["viewer_marker_ids"] = list(raw_controlled) if isinstance(raw_controlled, list) else []

    # The backcompat block above guarantees both keys are present.
    for _field_name in ("controlled_marker_ids", "viewer_marker_ids"):
        data[_field_name] = marker_id_list(data[_field_name], path=path, field_name=_field_name)
    _unviewed = [mid for mid in data["controlled_marker_ids"] if mid not in data["viewer_marker_ids"]]
    if _unviewed and (path, tuple(_unviewed)) not in _LOGGED_UNVIEWED:
        _LOGGED_UNVIEWED.add((path, tuple(_unviewed)))
        logger.info("%s: controlled markers %s are now viewed too; a controlled marker always is", path, _unviewed)
    data["viewer_marker_ids"] = viewed_with_controlled(data["controlled_marker_ids"], data["viewer_marker_ids"])

    # Back-compat: map the old ``vf1_*`` keys onto their ``marker_fader_*``
    # successors (only when the new key is absent) BEFORE ``_filter_known``
    # drops them, so an existing config keeps its stick assignment.
    _ctrl = data.get("controller")
    if isinstance(_ctrl, dict):
        for _old, _new in (
            ("vf1_stick", "marker_fader_stick"),
            ("vf1_max_speed_s", "marker_fader_max_speed_s"),
        ):
            if _old in _ctrl and _new not in _ctrl:
                _ctrl[_new] = _ctrl[_old]

    # Back-compat: read the marker's Z-line settings under their former names
    # BEFORE ``_filter_known`` drops them, so an existing config keeps them.
    _marker = data.get("marker")
    if isinstance(_marker, dict):
        apply_renamed_marker_keys(_marker)

    _ui = data.get("ui")
    if isinstance(_ui, dict):
        migrate_ui_keys(_ui)

    # Back-compat: OTP output used to pin a raw ``source_ip``; it now pins the
    # interface by name (``source_iface``), like PSN. Convert an existing IP to
    # its current iface name before ``_filter_known`` drops the old key, so a
    # pin survives the upgrade. An IP no longer present on any NIC resolves to
    # "" (auto-detect), which is the safe fallback.
    _otp = data.get("otp_output")
    if isinstance(_otp, dict) and "source_iface" not in _otp:
        _legacy_otp_ip = _otp.get("source_ip")
        if isinstance(_legacy_otp_ip, str) and _legacy_otp_ip.strip():
            from openfollow.net_utils import get_iface_for_ip

            _otp["source_iface"] = get_iface_for_ip(_legacy_otp_ip.strip())

    # Missing, it is decided from the pins (``_coerce_network_mode``).
    data.setdefault("network_mode", None)

    # Build sub-config instances from their TOML sections
    sub_kwargs: dict[str, Any] = {}
    for name, cls in _SUB_CONFIG_MAP.items():
        sub_kwargs[name] = cls(**_filter_known(cls, data.get(name, {})))

    # Build top-level kwargs: keep only fields known to AppConfig,
    # exclude sub-config sections and legacy keys
    top_kwargs = _filter_known(AppConfig, data)
    for name in _SUB_CONFIG_MAP:
        top_kwargs.pop(name, None)
    top_kwargs.pop("num_markers", None)

    config = AppConfig(**top_kwargs, **sub_kwargs)
    _warn_deprecated_controller_bindings(config.controller)
    return config


def load_config_for_save(path: str, *, missing: Callable[[], AppConfig] | None = None) -> AppConfig:
    """The config file as it is on disk, for a caller that writes it back or hands it on.

    An empty or unreadable file raises ``ConfigFileUnreadableError``: read as
    the backup or the defaults, it would be written over with them. A missing
    one reads as *missing* says, else as ``load_config`` reads it.
    """
    on_disk = read_config_whole(path)
    if on_disk is not None:
        return on_disk
    return missing() if missing is not None else load_config(path)


_DEPRECATED_WARNED: set[str] = set()


_RENAMED_MARKER_KEYS: tuple[tuple[str, str], ...] = (
    ("drop_line", "z_line"),
    ("drop_line_thickness", "z_line_thickness"),
)


def apply_renamed_marker_keys(marker_data: dict[str, Any]) -> None:
    """Read the marker's settings under their former names, in place.

    Every route into a marker section goes through here - config file, JSON
    config API, peer broadcast - so a station still sending the former names
    during a rolling upgrade is understood rather than accepted and silently
    discarded. The current name wins wherever both appear.
    """
    for old, new in _RENAMED_MARKER_KEYS:
        if old in marker_data and new not in marker_data:
            marker_data[new] = marker_data[old]
            _warn_renamed_marker_key(old, new)
    # invert_control_direction is stage axes, or when true the heading of a camera looking downstage.
    if "invert_control_direction" in marker_data:
        inverted = _coerce_bool(marker_data.pop("invert_control_direction"), False)
        if "control_frame" not in marker_data:
            marker_data["control_frame"] = "heading" if inverted else "stage"
            if inverted:
                marker_data.setdefault("control_heading", 180.0)
            _warn_renamed_marker_key("invert_control_direction", "control_frame")


def _warn_renamed_marker_key(old: str, new: str) -> None:
    """Tell the operator a marker key was read under its former name.

    The value still applies, so this is not a failure - but the old name will
    stop being read, and a config left on it would silently revert to the
    default. ``load_config`` runs on every hot-reload, so the shared set keeps
    one warning per key rather than one per reload.
    """
    if old in _DEPRECATED_WARNED:
        return
    _DEPRECATED_WARNED.add(old)
    logger.warning(
        "[marker] %s has been renamed to %s. The value was applied. Update the sender "
        "(config.toml, a config API client, or an older peer) before the old name stops being read.",
        old,
        new,
    )


def _warn_deprecated_controller_bindings(controller: ControllerConfig) -> None:
    """Emit a one-shot warning for deprecated controller bindings.

    Mode-specific confirm/cancel pairs were consolidated into a single
    ``btn_menu_confirm`` / ``btn_menu_cancel`` used by every menu.

    ``load_config`` is invoked on every hot-reload, so a module-level set
    tracks which fields already warned to keep logs from flooding across
    reloads.
    """
    defaults = ControllerConfig()
    confirm_cancel_fields = (
        "btn_settings_confirm",
        "btn_settings_cancel",
        "src_btn_confirm",
        "src_btn_cancel",
    )
    for field_name in confirm_cancel_fields:
        if field_name in _DEPRECATED_WARNED:
            continue
        current = getattr(controller, field_name)
        if current != getattr(defaults, field_name):
            logger.warning(
                "Config field controller.%s=%r is deprecated: per-menu "
                "confirm/cancel bindings were consolidated – use btn_menu_confirm / "
                "btn_menu_cancel instead.",
                field_name,
                current,
            )
            _DEPRECATED_WARNED.add(field_name)


def _strip_none(obj: Any) -> Any:
    """Recursively drop keys whose value is None – TOML has no null."""
    if isinstance(obj, dict):
        return {k: _strip_none(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip_none(v) for v in obj]
    return obj


def _fsync_dir(directory: Path) -> None:
    """``fsync`` a directory entry so a rename into it is itself durable.

    ``os.fsync`` on the file flushes its contents, but the rename (a directory
    metadata change) can be lost on power loss until the parent dir is synced.
    Best-effort: directory fsync is unsupported on some platforms (Windows) and
    the file is already in place, so ``OSError`` is swallowed.
    """
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY)
    except OSError:  # pragma: no cover - platform-dependent (e.g. Windows)
        return
    try:
        os.fsync(dir_fd)
    except OSError:  # pragma: no cover - rare; the file is already renamed
        pass
    finally:
        os.close(dir_fd)


def config_to_toml_dict(config: AppConfig) -> dict[str, Any]:
    """Return ``config`` as a TOML-serialisable dict.

    Single source of truth shared by :func:`save_config` and the
    diagnostics-bundle "effective config" dump so the two can't drift.

    Normalisations:
    - ``None`` values dropped (TOML has no null).
    - ``marker_move_speeds``: int keys stringified (TOML requires string keys);
      entries whose marker is no longer in ``controlled_marker_ids`` are pruned
      from this serialised view only (in-memory entries survive a live
      remove-and-re-add).
    """
    data: dict[str, Any] = _strip_none(asdict(config))
    raw_speeds = data.get("marker_move_speeds", {})
    # pragma: no branch – ``asdict(config)`` always materialises the
    # ``marker_move_speeds`` field as a dict (it's declared as
    # ``dict[int, float]`` with ``default_factory=dict``). The
    # ``isinstance`` guard is defensive against future schema changes.
    if isinstance(raw_speeds, dict):  # pragma: no branch
        controlled = set(config.controlled_marker_ids)
        data["marker_move_speeds"] = {str(k): v for k, v in raw_speeds.items() if int(k) in controlled}
    return data


def _read_whole(path: str) -> tuple[bytes, AppConfig] | None:
    """The bytes of the config file at *path* and the config they hold; ``None`` when there is no file.

    This is the one test of a file that reads whole: its bytes build a config.
    An empty file raises ``ConfigFileEmptyError``, one that cannot be read,
    does not parse or does not build ``ConfigFileUnreadableError``.
    """
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ConfigFileUnreadableError(f"Config file {path} could not be read: {error}", path) from error
    # Blank loads as every default; an editor that truncates before it writes leaves one for a moment.
    if not raw.strip():
        raise ConfigFileEmptyError(f"Config file {path} is empty.", path)
    try:
        return raw, _config_from_data(path, tomllib.loads(raw.decode("utf-8")))
    except Exception as error:
        raise ConfigFileUnreadableError(f"Config file {path} could not be read: {error}", path) from error


def read_config_whole(path: str) -> AppConfig | None:
    """The config file at *path* as it reads whole, ``None`` when there is none; an empty or unreadable one raises."""
    whole = _read_whole(path)
    return None if whole is None else whole[1]


def config_file_problem(path: str) -> ConfigFileUnreadableError | None:
    """What keeps the config file at *path* from taking a save, or ``None``."""
    try:
        _read_whole(path)
    except ConfigFileUnreadableError as error:
        return error
    return None


def config_problem_kind(error: ConfigFileUnreadableError) -> str:
    """``empty-restorable`` (a restart restores the backup), ``empty`` (a restart starts afresh) or ``unreadable``."""
    if not isinstance(error, ConfigFileEmptyError):
        return "unreadable"
    return "empty" if _readable_backup(error.path) is None else "empty-restorable"


_PROBLEM_WORDS = {
    "empty-restorable": ("is empty", "Restart OpenFollow to restore the settings from the latest backup copy"),
    "empty": ("is empty", "Restart OpenFollow to start again with default settings"),
    "unreadable": ("could not be read", "Import a settings file to replace it"),
}


def config_problem_words(error: ConfigFileUnreadableError) -> tuple[str, str]:
    """An operator's words for *error*: what is wrong with the settings file, and the one step that mends it."""
    return _PROBLEM_WORDS[config_problem_kind(error)]


def config_file_health(path: str) -> str:
    """How the config file at *path* and its backup read, naming nothing they hold."""
    problem = config_file_problem(path)
    if problem is not None:
        state = config_problem_words(problem)[0]
    else:
        state = "reads whole" if os.path.exists(path) else "missing"
    backup = "backup reads whole" if _readable_backup(path) is not None else "no readable backup"
    return f"{state}; {backup}"


def save_config(config: AppConfig, path: str = "config.toml", *, replace_unreadable: bool = False) -> None:
    """Serialize the full config to a TOML file, overwriting existing content.

    A file that is there but empty or unreadable is left alone and raises
    ``ConfigFileUnreadableError``, unless *replace_unreadable*: the config
    written was not read from it. Replacing one takes no snapshot, so the last
    good ``.bak`` stays.

    Durable, atomic write: content is streamed to a temp file in the same
    directory, ``fsync``-ed, ``os.replace``-d into place, then the parent
    directory is ``fsync``-ed so the rename survives power loss. A crash leaves
    either the old or new file intact, never a truncated one. The bytes the
    check read are snapshotted to ``config.toml.bak`` the same way, best-effort
    (a failure is logged and swallowed); ``load_config`` recovers from it when
    the primary is missing or unreadable.
    """
    try:
        whole = _read_whole(path)
    except ConfigFileUnreadableError:
        if not replace_unreadable:
            raise
        whole = None
    current = None if whole is None else whole[0]
    data = tomli_w.dumps(config_to_toml_dict(config)).encode("utf-8")
    target = Path(path)
    if current is not None:
        backup_path = target.with_name(target.name + ".bak")
        try:
            _replace_file(backup_path, current)
        except OSError:
            # The save itself is fine; the cause is what a missing .bak is later diagnosed from.
            logger.warning("Could not write config backup %s; continuing", backup_path, exc_info=True)
    _replace_file(target, data)


def save_config_change(path: str, running: AppConfig, change: Callable[[AppConfig], None]) -> None:
    """Make ``change`` in ``running`` and in the config file as it is on disk now.

    For a writer whose running config may lag the file: only what ``change`` sets is written, under
    ``config_write_lock``, so a save another thread made in the meantime survives. With no file yet,
    ``running`` is written whole.
    """
    change(running)
    with config_write_lock:
        current = read_config_whole(path)
        if current is None:
            save_config(running, path)
            return
        change(current)
        save_config(current, path)


def save_config_fields(
    config: AppConfig,
    path: str,
    names: Iterable[str],
    *,
    replace_unreadable: bool = False,
) -> None:
    """Write the named fields of *config* onto the file at *path*; every other field keeps the file's value.

    One dot addresses a sub-config field (``trigger_zones.show_overlay``). A
    missing file is written from *config* whole. An empty or unreadable file
    raises and is left alone, or is replaced by *config* whole with
    *replace_unreadable*.
    """
    with config_write_lock:
        try:
            on_disk = read_config_whole(path)
        except ConfigFileUnreadableError:
            if not replace_unreadable:
                raise
            logger.warning("Config file %s could not be read - replacing it with the running config.", path)
            save_config(config, path, replace_unreadable=True)
            return
        if on_disk is None:
            save_config(config, path)
            return
        for name in names:
            parent, _, attr = name.rpartition(".")
            source = getattr(config, parent) if parent else config
            target = getattr(on_disk, parent) if parent else on_disk
            setattr(target, attr, getattr(source, attr))
        save_config(on_disk, path)


# ---------------------------------------------------------------------------
# Runtime Hot-Reload
# ---------------------------------------------------------------------------


def _apply_with_fallback(
    name: str,
    apply_fn: Callable[[], object],
    *,
    on_failure: Callable[[], None] | None = None,
) -> bool:
    """Run a live-config-apply function with logging + degrade-on-fail.

    Returns ``True`` on success. On exception: logs, runs ``on_failure``
    (typically clears a half-initialised reference), and returns ``False``
    without raising. The bool lets the caller revert ``app._config.<key>`` so
    a later pass retries – otherwise stored config matches ``new_config`` and
    subsequent passes silently no-op while the service stays disabled. Not
    raising keeps later settings applying.
    """
    start = time.monotonic()
    try:
        apply_fn()
    except Exception:
        logger.exception("live-apply '%s' failed; degrading", name)
        if on_failure is not None:
            try:
                on_failure()
            except Exception:
                logger.exception("on_failure for '%s' also raised", name)
        return False
    duration_ms = (time.monotonic() - start) * 1000.0
    logger.info("live-apply '%s' completed in %.1f ms", name, duration_ms)
    return True


def _marker_name_for_runtime(app: OpenFollowApp, marker_id: int) -> str:
    """Pick a name for ``add_marker`` from the catalog, with a fallback.

    Mirrors :func:`openfollow.services.init_markers`. Kept here so the
    live-reload diff path doesn't need to import services to format
    the label.
    """
    catalog = getattr(app, "_marker_catalog", None)
    if catalog is not None:
        entry = catalog.get(marker_id)
        if entry is not None and entry.name:
            return str(entry.name)
    return f"Marker {marker_id}"


def _video_config_changed(old_config: AppConfig, new_config: AppConfig) -> bool:
    """Whether the video input must be rebuilt: plugin-driven via the active
    source's ``config_changed(old, new)``, which covers ``video_source_type``,
    per-plugin fields (rtsp_url, srt_host, …) and the station interface a blank
    pin follows."""
    from openfollow.video.inputs import get_input_class

    if new_config.video_source_type != old_config.video_source_type:
        return True
    input_cls = get_input_class(new_config.video_source_type)
    # None only for a hand-edited config.toml naming a removed plugin.
    return input_cls is not None and input_cls.config_changed(old_config, new_config)


def _inactive_video_field_names(running_type: str) -> set[str]:
    """Stored video input fields that the input *running_type* does not declare."""
    from openfollow.video.inputs import get_input_class, get_registry

    stored = {f.name for f in fields(AppConfig)}
    names = {f.name for cls in get_registry().values() for f in cls.config_fields()} & stored
    cls = get_input_class(running_type)
    if cls is not None:
        names -= {f.name for f in cls.config_fields()}
    return names


def apply_runtime_config_changes(app: OpenFollowApp, new_config: AppConfig) -> bool:
    """Apply hot-reload config changes to runtime objects.

    Returns ``True`` when every section applied, ``False`` when at least one
    ``_apply_with_fallback`` section failed (its config was reverted). The
    caller withholds the mtime advance on ``False`` so the failed section
    retries on the next poll – matching the raise-based sections, which already
    keep the mtime by propagating. Direct-call sections still raise on failure.
    """
    from openfollow.scene.camera import Camera

    # Collects the names of ``_apply_with_fallback`` sections that degraded so
    # the orchestrator can withhold the mtime advance and retry them. Wrapping
    # the helper here (rather than threading a flag through ~10 call sites)
    # keeps each block's revert logic untouched.
    _failures: list[str] = []

    def _apply(
        name: str,
        apply_fn: Callable[[], object],
        *,
        on_failure: Callable[[], None] | None = None,
    ) -> bool:
        ok = _apply_with_fallback(name, apply_fn, on_failure=on_failure)
        if not ok:
            _failures.append(name)
        return ok

    if new_config.psn_system_name != app._config.psn_system_name:
        # Propagate the new name into every service holding a copy (PSN info
        # packets, OTP server, web beacon, window title). Revert on failure so
        # a later pass retries.
        old_psn_system_name = app._config.psn_system_name
        app._config.psn_system_name = new_config.psn_system_name
        if not _apply(
            "psn_system_name",
            lambda: app._runtime_services.apply_psn_system_name_change(
                new_config.psn_system_name,
            ),
        ):
            app._config.psn_system_name = old_psn_system_name

    # PSN network-binding. Both ``psn_mcast_ip`` and ``psn_source_iface`` reach
    # the PSN server's UDP socket via stop+reassign+start; when both change in
    # one pass, route through a single combined apply so the socket recycles
    # once (else the first rebind would briefly mix one new value with the
    # other's old value). The single-field blocks below handle one-at-a-time.
    #
    # Strip whitespace before compare/store/apply: the web apply path bypasses
    # ``AppConfig.__post_init__``, so a value like ``"236.10.10.10 "`` would
    # otherwise spuriously rebind each load and break the multicast bind.
    # A blank video pin follows the station interface; the video block below
    # compares against the one that was in force here.
    old_station_iface = app._config.psn_source_iface

    new_psn_mcast_ip = new_config.psn_mcast_ip.strip()
    new_psn_source_iface = new_config.psn_source_iface.strip()
    psn_iface_changed = new_psn_source_iface != app._config.psn_source_iface
    psn_mcast_changed = new_psn_mcast_ip != app._config.psn_mcast_ip
    if psn_iface_changed:
        # Resolve the bind IP up-front so receiver/server rebind to the same
        # value ``init_psn`` uses – through the fail-closed resolver, because a
        # configured interface with no address must stop PSN rather than move
        # it. Falling through here would make a live Save behave differently
        # from a reboot on the same config, and would put stage data on the
        # network the operator just deselected.
        from openfollow.net_utils import resolve_plane_source_ip

        new_resolved_source_ip, new_resolve_status = resolve_plane_source_ip(
            "",
            new_psn_source_iface,
        )
    else:
        new_resolved_source_ip = ""
        new_resolve_status = "none"
    psn_iface_down = psn_iface_changed and new_resolve_status == "down"

    if psn_mcast_changed and psn_iface_changed:
        old_psn_mcast_ip = app._config.psn_mcast_ip
        old_psn_source_iface = app._config.psn_source_iface
        app._config.psn_mcast_ip = new_psn_mcast_ip
        app._config.psn_source_iface = new_psn_source_iface
        if not _apply(
            "psn_network",
            (
                app._runtime_services.suspend_psn_planes
                if psn_iface_down
                else lambda: app._runtime_services.apply_psn_source_ip_change(
                    new_resolved_source_ip,
                    new_mcast_ip=new_psn_mcast_ip,
                )
            ),
        ):
            app._config.psn_mcast_ip = old_psn_mcast_ip
            app._config.psn_source_iface = old_psn_source_iface
        # Re-sync the stale-iface advisory so the PSN web partial tracks the
        # now-active pin (cleared on a honoured pin, restored on rollback).
        app._refresh_psn_source_advisory()
    elif psn_mcast_changed:
        old_psn_mcast_ip = app._config.psn_mcast_ip
        app._config.psn_mcast_ip = new_psn_mcast_ip
        if not _apply(
            "psn_mcast_ip",
            lambda: app._runtime_services.apply_psn_mcast_ip_change(
                new_psn_mcast_ip,
            ),
        ):
            app._config.psn_mcast_ip = old_psn_mcast_ip

    # Window dimensions live-resize the GTK window. Normalise
    # (``max(1, int(...))``) before comparing/storing so a bad TOML value
    # doesn't desync ``app._config`` from the runtime or re-fire each reload.
    new_window_width = max(1, int(new_config.window_width))
    new_window_height = max(1, int(new_config.window_height))
    if new_window_width != app._config.window_width or new_window_height != app._config.window_height:
        app._config.window_width = new_window_width
        app._config.window_height = new_window_height
        app._runtime_services.apply_window_size_change(
            new_window_width,
            new_window_height,
        )

    # Video recovery timers (``stall_timeout`` / ``heal_interval``) apply
    # live: the receiver reads them off its mutable ``ReconnectPolicy`` on
    # the next watchdog / heal tick, so no pipeline rebuild is needed. Push
    # them onto the running receiver and mirror into ``app._config`` so the
    # diff doesn't re-fire each pass.
    if new_config.stall_timeout != app._config.stall_timeout or new_config.heal_interval != app._config.heal_interval:
        app._config.stall_timeout = new_config.stall_timeout
        app._config.heal_interval = new_config.heal_interval
        receiver = getattr(app, "_video_receiver", None)
        if receiver is not None and hasattr(receiver, "set_recovery_timers"):
            receiver.set_recovery_timers(
                stall_timeout=new_config.stall_timeout,
                heal_interval=new_config.heal_interval,
            )
        logger.info(
            "Applied video recovery timers: stall_timeout=%.1fs, heal_interval=%.1fs",
            new_config.stall_timeout,
            new_config.heal_interval,
        )

    # ``web_pin`` has no in-process service to notify (auth reads it from disk
    # each request); just mirror it into ``app._config`` so the diff stops
    # looping on subsequent passes.
    if new_config.web_pin != app._config.web_pin:
        app._config.web_pin = new_config.web_pin
    # Read live by the Network screen, the labels and the video wording; the
    # pins it clears arrive through their own sections above.
    if new_config.network_mode != app._config.network_mode:
        app._config.network_mode = new_config.network_mode
    # The listener moves only on a restart, but a whole-config save must not
    # write a cleared pin back, and the Network screen reads the saved one.
    if new_config.web_bind_iface != app._config.web_bind_iface:
        app._config.web_bind_iface = new_config.web_bind_iface
    # Read live by the HUD's outage rows and the Network screen; nothing to rebind.
    if new_config.interface_labels != app._config.interface_labels:
        app._config.interface_labels = dict(new_config.interface_labels)
    # Read live by the HUD's web address rows; the web server reads it from disk.
    if new_config.station_fqdn != app._config.station_fqdn:
        old_station_fqdn = app._config.station_fqdn
        app._config.station_fqdn = new_config.station_fqdn
        if not _apply(
            "station_fqdn",
            lambda: app._runtime_services.apply_station_fqdn_change(new_config.station_fqdn),
        ):
            app._config.station_fqdn = old_station_fqdn

    # PSN iface single-field path: only fires when iface changed alone (the
    # combined block above handles the iface+mcast case). The bind IP comes
    # from the resolver, so changing the pin rebinds to its current IPv4.
    if psn_iface_changed and not psn_mcast_changed:
        old_psn_source_iface = app._config.psn_source_iface
        app._config.psn_source_iface = new_psn_source_iface
        if not _apply(
            "psn_source_iface",
            (
                app._runtime_services.suspend_psn_planes
                if psn_iface_down
                else lambda: app._runtime_services.apply_psn_source_ip_change(
                    new_resolved_source_ip,
                )
            ),
        ):
            app._config.psn_source_iface = old_psn_source_iface
        # Re-sync the stale-iface advisory so the PSN web partial tracks the
        # now-active pin (cleared on a honoured pin, restored on rollback).
        app._refresh_psn_source_advisory()

    # Planes with a blank pin follow the station interface, but each is gated
    # on its own dataclass differing – which it doesn't when only the Station
    # default row moved. Covers both iface paths above; the equality check
    # skips it when either rolled back. Runs on the same pass, so the panel's
    # address column never advertises an interface a plane isn't on yet.
    if psn_iface_changed and app._config.psn_source_iface == new_psn_source_iface:
        _apply(
            "station_iface_followers",
            app._runtime_services.apply_station_iface_change,
        )

    # Video pipeline live-swap, detected above. Distinct from the
    # ``detection`` block below.
    from openfollow.video.inputs import get_input_class

    # Built from the station interface actually committed above: a rejected
    # station change must not pin the video to the interface it named.
    video_config = copy.copy(new_config)
    video_config.psn_source_iface = app._config.psn_source_iface
    video_before = copy.copy(app._config)
    video_before.psn_source_iface = old_station_iface
    # A failed swap is owed until one succeeds: when only the station interface it
    # follows changed, that change is committed and the configs alone no longer differ.
    if _video_config_changed(video_before, video_config) or getattr(app, "_video_swap_owed", False):
        # Snapshot the new plugin's CURRENT (pre-commit) field values so the
        # failure path can restore them. The old plugin's fields aren't
        # snapshotted – the commit loop only writes new-plugin fields.
        old_video_source_type = app._config.video_source_type
        new_input_cls = get_input_class(new_config.video_source_type)
        old_field_values: dict[str, Any] = {}
        if new_input_cls is not None:  # pragma: no branch
            for f in new_input_cls.config_fields():
                old_field_values[f.name] = getattr(
                    app._config,
                    f.name,
                    f.default,
                )

        # Commit new values to ``app._config`` BEFORE the orchestrator
        # call so a successful swap leaves config + runtime in sync.
        # On failure ``_apply_with_fallback`` returns False and we
        # walk the snapshot back into place.
        app._config.video_source_type = new_config.video_source_type
        if new_input_cls is not None:  # pragma: no branch
            for f in new_input_cls.config_fields():
                setattr(
                    app._config,
                    f.name,
                    getattr(new_config, f.name, f.default),
                )

        swapped = _apply(
            "video_source",
            lambda: app._runtime_services.swap_video(video_config),
        )
        app._video_swap_owed = not swapped
        if not swapped:
            app._config.video_source_type = old_video_source_type
            for name, value in old_field_values.items():
                setattr(app._config, name, value)
    else:
        # A field that does not rebuild the input is committed without a swap.
        input_cls = get_input_class(app._config.video_source_type)
        quiet = {
            f.name: getattr(new_config, f.name, f.default)
            for f in (input_cls.config_fields() if input_cls is not None else [])
            if not f.rebuilds and getattr(app._config, f.name, f.default) != getattr(new_config, f.name, f.default)
        }
        if quiet:
            for name, value in quiet.items():
                setattr(app._config, name, value)
            receiver = getattr(app, "_video_receiver", None)
            if receiver is not None:
                receiver.update_input_fields(quiet)
    # The swap commits the video pin only for an input it rebuilds; a pin Simple cleared may need
    # no rebuild, and must not stay in the running config for a whole-config save to write back.
    if not getattr(app, "_video_swap_owed", False) and new_config.video_input_iface != app._config.video_input_iface:
        app._config.video_input_iface = new_config.video_input_iface

    # The other inputs' fields drive nothing until their input runs, so they are
    # mirrored as they are: a later switch to one is built from the file's values.
    for name in _inactive_video_field_names(app._config.video_source_type):
        setattr(app._config, name, getattr(new_config, name))

    # Grid and camera updates (no renderer needed - Cairo overlay reads from config)
    if new_config.grid != app._config.grid:
        app._config.grid = new_config.grid

    if new_config.ground_grid != app._config.ground_grid:
        app._config.ground_grid = new_config.ground_grid

    if new_config.camera != app._config.camera:
        app._camera = Camera.from_config(new_config.camera)
        app._config.camera = new_config.camera

    if new_config.marker != app._config.marker:
        app._config.marker = new_config.marker

    # ``marker_move_speeds`` is deliberately NOT reloaded here. It is device-local
    # and runtime-authoritative – written live by the R/T keys + gamepad bumpers, so
    # the in-memory dict is the source of truth. A hot-reload from any config write (a
    # web section save, the debounced self-persist, an import) must never overwrite it.
    # Startup ``load_config`` seeds the initial value from disk; it is not reloaded after.

    # ``unit_system`` is read live every frame by ``sync_ui_config``; the
    # Features list is read live too, and the services it gates are re-applied
    # by their blocks below.
    old_ui = app._config.ui
    if new_config.ui != app._config.ui:
        app._config.ui = new_config.ui

    def _flipped(key: str) -> bool:
        return old_ui.feature_on(key) != app._config.ui.feature_on(key)

    if _flipped("psn_input"):
        _apply("psn_input", app._runtime_services.apply_psn_input_change)

    # Network backend selection happens once at startup, so a change only
    # takes effect on restart. Mirror it in-memory so the web UI re-render and
    # any other ``app._config.network`` reader stay consistent until then.
    if new_config.network != app._config.network:
        app._config.network = new_config.network

    if new_config.controlled_marker_ids != app._config.controlled_marker_ids:
        # Defense-in-depth: filter ``< 1`` ids again (this path can bypass
        # ``load_config``'s filter via programmatic apply). ``Marker.__init__``
        # raises for id < 1 and id 0 is the reserved "ignored" wire value.
        # ``bool`` is excluded explicitly (``True >= 1`` since ``bool`` is an
        # ``int`` subclass).
        filtered_controlled = [
            marker_id
            for marker_id in new_config.controlled_marker_ids
            if isinstance(marker_id, int) and not isinstance(marker_id, bool) and marker_id >= 1
        ]
        if app._server is not None and app._psn_receiver is not None:
            # Bind narrowed non-None locals so the closures below keep the
            # type (mypy doesn't carry outer narrowing into nested functions).
            server = app._server
            receiver = app._psn_receiver
            old_ids = set(app._controlled_ids)
            new_ids = set(filtered_controlled)
            to_remove = sorted(old_ids - new_ids)
            to_add = sorted(new_ids - old_ids)
            # Capture old names up front so a rollback re-registers removed
            # markers with their original label.
            old_names = {marker_id: _marker_name_for_runtime(app, marker_id) for marker_id in to_remove}
            # Seed the same default position startup registration uses: an
            # unseeded marker broadcasts (0, 0, 0) and, having never taken a
            # data write, reports itself invalid on the PSN wire.
            _mk = app._config.marker
            default_pos = (_mk.default_pos_x, _mk.default_pos_y, _mk.default_pos_z)

            def _apply_controlled() -> None:
                for marker_id in to_remove:
                    server.remove_marker(marker_id)
                for marker_id in to_add:
                    server.add_marker(marker_id, _marker_name_for_runtime(app, marker_id)).set_pos(*default_pos)

                # Mirror marker changes to OTP output if active
                if app._otp_server is not None:
                    for marker_id in to_remove:
                        app._otp_server.unregister_marker(marker_id)
                    for marker_id in to_add:
                        marker = server.get_marker(marker_id)
                        # pragma: no branch – marker_id was just added to the
                        # server above, so ``get_marker`` always returns it.
                        if marker is not None:  # pragma: no branch
                            app._otp_server.register_marker(marker)

                # Mirror marker changes to RTTrPM output if active
                if app._rttrpm_server is not None:
                    for marker_id in to_remove:
                        app._rttrpm_server.unregister_marker(marker_id)
                    for marker_id in to_add:
                        marker = server.get_marker(marker_id)
                        # pragma: no branch – same reasoning as the OTP mirror.
                        if marker is not None:  # pragma: no branch
                            app._rttrpm_server.register_marker(marker)

                # Commit app-level state only after every server mutation
                # succeeded: a mid-loop failure must leave controlled ids /
                # config untouched so the next pass recomputes the same diff
                # against a server we've rolled back – never against a
                # half-mutated one.
                app._controlled_ids = list(filtered_controlled)
                # pragma: no branch – _selected_id is set from the previous
                # controlled_ids list and is only updated to a new value
                # when removed; integration tests cover the True arm.
                if app._selected_id not in new_ids:  # pragma: no branch
                    app._selected_id = app._controlled_ids[0] if app._controlled_ids else None
                receiver.set_ignore_ids(new_ids)
                app._config.controlled_marker_ids = filtered_controlled

            def _restore_controlled() -> None:
                # Reconcile the server back to old_ids so a partial apply
                # doesn't leave registrations ahead of the un-committed
                # controlled ids / config; the next pass recomputes the same
                # diff (and re-mirrors OTP/RTTrPM). Best-effort –
                # ``_apply_with_fallback`` logs if this itself raises.
                for marker_id in to_add:
                    server.remove_marker(marker_id)
                for marker_id in to_remove:
                    server.add_marker(marker_id, old_names[marker_id]).set_pos(*default_pos)

            _apply("controlled_marker_ids", _apply_controlled, on_failure=_restore_controlled)
        else:
            app._config.controlled_marker_ids = filtered_controlled

        # Re-provision per-controlled-marker faders for the *committed* set
        # (new on success; unchanged-old when the transactional apply rolled
        # back). Runs outside the PSN guard; tolerates an absent bus.
        bus = getattr(app._runtime_services, "_virtual_faders", None)
        if bus is not None:
            bus.provision_marker_faders(list(app._config.controlled_marker_ids))

    if new_config.viewer_marker_ids != app._config.viewer_marker_ids:
        # Same defense-in-depth filter as controlled_marker_ids above
        # (including the explicit ``bool`` rejection – ``True >= 1``
        # would otherwise sneak past).
        filtered_viewer = [
            marker_id
            for marker_id in new_config.viewer_marker_ids
            if isinstance(marker_id, int) and not isinstance(marker_id, bool) and marker_id >= 1
        ]
        app._viewer_ids = list(filtered_viewer)
        app._config.viewer_marker_ids = filtered_viewer

    if new_config.controller != app._config.controller:
        mouse_was_enabled = app._config.controller.mouse_enabled
        app._config.controller = new_config.controller
        # pragma: no branch – controller live-reload only fires while
        # the app is running, so ``_input_manager`` is always
        # initialised at this point. The False arm guards against a
        # config-only restart path that doesn't have a running input
        # subsystem.
        if app._input_manager is not None:  # pragma: no branch
            app._input_manager.gamepad_handler.apply_config()
            # Turning mouse control off mid-session must disarm the handler too,
            # else a stale ``_active`` would snap the marker to the cursor on
            # the first pointer event after it's re-enabled.
            if mouse_was_enabled and not new_config.controller.mouse_enabled:
                app._input_manager.mouse_handler.deactivate()
        # Reflect a mouse_enabled toggle in the live pointer visibility:
        # turning mouse input off hides the cursor immediately, turning it
        # on brings the arrow back. Idempotent on the window side, so an
        # unrelated controller edit (button remap, deadzone) is a no-op.
        canvas = getattr(app, "_canvas", None)
        if canvas is not None and hasattr(canvas, "set_pointer_base_visible"):
            canvas.set_pointer_base_visible(new_config.controller.mouse_enabled)

    if new_config.mouse3d != app._config.mouse3d:
        app._config.mouse3d = new_config.mouse3d
        # Live-apply: the read thread runs for the handler's lifetime, so this
        # only swaps the mapping config (the ``enabled`` gate is read live in
        # ``InputManager.update``). pragma: no branch – same reasoning as the
        # controller block; the input subsystem is always up during live reload.
        if app._input_manager is not None:  # pragma: no branch
            app._input_manager.restart_mouse3d(new_config.mouse3d)

    if new_config.osc != app._config.osc:
        app._config.osc = new_config.osc
        # pragma: no branch – same reasoning as the controller block.
        if app._input_manager is not None:  # pragma: no branch
            app._input_manager.restart_osc(
                new_config.osc.enabled,
                new_config.osc.port,
                allowed_sender_ips=list(new_config.osc.allowed_sender_ips),
                multicast_group=new_config.osc.multicast_group,
                listen_iface=new_config.osc.listen_iface,
            )

    # ``enabled`` toggles the OSC ingest adapter; ``max_visible`` /
    # ``position`` / ``scale`` are read live by the overlay builder.
    if new_config.operator_messages != app._config.operator_messages:
        app._config.operator_messages = new_config.operator_messages
        if app._input_manager is not None:  # pragma: no branch
            app._input_manager.restart_operator_messages()

    # Read live by the remote routes and the frame loop, which lets go of every hold while it is off.
    if new_config.touch_remote != app._config.touch_remote:
        app._config.touch_remote = new_config.touch_remote

    if new_config.otp_output != app._config.otp_output:
        old_otp_output = app._config.otp_output
        app._config.otp_output = new_config.otp_output
        # No ``on_failure`` clear here: the orchestrator owns ``_otp_server``'s
        # lifecycle and nulls it only when the server is genuinely dead. An
        # unconditional clear would orphan a server that the orchestrator's
        # rollback kept running.
        if not _apply(
            "otp_output",
            lambda: app._runtime_services.apply_otp_output_change(
                new_config.otp_output,
            ),
        ):
            app._config.otp_output = old_otp_output

    if new_config.rttrpm_output != app._config.rttrpm_output or _flipped("rttrpm"):
        old_rttrpm_output = app._config.rttrpm_output
        app._config.rttrpm_output = new_config.rttrpm_output
        # Same lifecycle ownership as OTP: orchestrator nulls the
        # server reference iff rollback also fails.
        if not _apply(
            "rttrpm_output",
            lambda: app._runtime_services.apply_rttrpm_output_change(
                feature_gated(new_config.rttrpm_output, app._config, "rttrpm"),
            ),
        ):
            app._config.rttrpm_output = old_rttrpm_output

    # OSC routing: transmitter rows reference a shared destination set, and the
    # zone engine resolves zone sends against the same set. A transmitter or
    # destination edit (e.g. an IP change) re-resolves at both consumers so
    # every referencing row and zone repoints live, no restart. Apply both as
    # one unit – the manager and the zone engine must agree on the same
    # destination set, and a partial apply would split routing across the two
    # consumers (manager on the new endpoints while config + zone engine hold
    # the old ones). Manager always exists once initialised (no on→off teardown).
    osc_transmitters_changed = new_config.osc_transmitters != app._config.osc_transmitters
    osc_destinations_changed = new_config.osc_destinations != app._config.osc_destinations
    # The trigger_zones block below reloads the zone engine too; when zones also
    # changed, let that block own the single reload so a combined edit doesn't
    # reload the engine twice in one pass.
    trigger_zones_changed = new_config.trigger_zones != app._config.trigger_zones or _flipped("zones")
    if osc_transmitters_changed or osc_destinations_changed:
        old_osc_transmitters = app._config.osc_transmitters
        old_osc_destinations = app._config.osc_destinations
        app._config.osc_transmitters = new_config.osc_transmitters
        app._config.osc_destinations = new_config.osc_destinations

        def _reload_zone_destinations(destinations: OscDestinationsConfig) -> None:
            zone_engine = getattr(app._runtime_services, "_zone_engine", None)
            if zone_engine is not None:
                zone_engine.reload_config(feature_gated(app._config.trigger_zones, app._config, "zones"), destinations)

        def _revert_osc_routing() -> None:
            # Restore config first, then re-stage the old routing into the
            # manager + zone engine so a failed apply can't leave the manager
            # on the new endpoints while config and the zone engine hold the
            # old ones. A re-raise here is caught and logged by the helper.
            app._config.osc_transmitters = old_osc_transmitters
            app._config.osc_destinations = old_osc_destinations
            app._runtime_services.apply_osc_transmitters_change(
                old_osc_transmitters,
                old_osc_destinations,
            )
            _reload_zone_destinations(old_osc_destinations)

        if _apply(
            "osc_routing",
            lambda: app._runtime_services.apply_osc_transmitters_change(
                new_config.osc_transmitters,
                new_config.osc_destinations,
            ),
            on_failure=_revert_osc_routing,
        ):
            # Skip when the trigger_zones block will reload the engine anyway
            # (with the same committed destinations) – avoids a double reload.
            if not trigger_zones_changed:
                _reload_zone_destinations(new_config.osc_destinations)

    if new_config.detection != app._config.detection or _flipped("detection"):
        old_detection = app._config.detection
        # What the detector runs: off while the Person detection feature is off.
        new_detection = feature_gated(new_config.detection, app._config, "detection")
        new_enabled = new_detection.enabled
        # Two live-apply paths:
        #   - ``apply_detection_change`` for on→on reload, on→off, off→off –
        #     served by the in-process worker's staged-config drain.
        #   - ``swap_detector`` for transitions needing a pipeline rebuild
        #     (the detection appsink caps are pinned at build time from
        #     ``PersonDetector.input_resolution``): off→on, unavailable→on
        #     (re-probe a backend that failed to load), and ``inference_size``
        #     change while enabled. An edit while a detector is still being
        #     built goes to a new build: the running detector is about to go.
        detector_ref = getattr(app._runtime_services, "_person_detector", None)
        detector_runnable = detector_ref is not None and getattr(detector_ref, "available", True)
        inference_size_changed = old_detection.inference_size != new_config.detection.inference_size
        needs_swap = new_enabled and (
            not detector_runnable or inference_size_changed or app._runtime_services.detector_build_pending
        )
        app._config.detection = new_config.detection
        if needs_swap:
            # Rebuild path. ``swap_detector`` owns its own rollback –
            # on failure it restores the prior detector against a
            # rebuilt pipeline (or clears the reference if rollback
            # also fails), so the dispatcher only reverts stored config.
            # The detector is built off the frame loop; a failure after this
            # returns reverts and is retried on the next poll, as a raise is.
            retry_from = app._config_mtime

            def _swap_failed() -> None:
                app._config.detection = old_detection
                app._config_mtime = min(app._config_mtime, retry_from)

            if not _apply(
                "detection_swap",
                lambda: app._runtime_services.swap_detector(new_detection, on_failure=_swap_failed),
            ):
                app._config.detection = old_detection
        else:
            # In-process reload path. The detector reference lives on
            # ``AppRuntimeServices`` (set in ``init_video``), NOT on
            # ``OpenFollowApp``. Clear the right attribute on failure
            # so the next reload starts from a clean slate; a setattr
            # on ``app`` would silently mint a new attribute that
            # nothing else reads.
            if not _apply(
                "detection",
                lambda: app._runtime_services.apply_detection_change(new_detection),
                on_failure=lambda: setattr(
                    app._runtime_services,
                    "_person_detector",
                    None,
                ),
            ):
                app._config.detection = old_detection

    if trigger_zones_changed:
        app._config.trigger_zones = new_config.trigger_zones
        zone_engine = getattr(app._runtime_services, "_zone_engine", None)
        if zone_engine is not None:
            # Use the committed destinations (``app._config``) so a zones edit
            # stays coherent with an OSC-routing apply that was reverted above.
            zone_engine.reload_config(
                feature_gated(new_config.trigger_zones, app._config, "zones"),
                app._config.osc_destinations,
            )
        # The endpoint is resolved from ``osc_destinations`` at send time.
        # Stale per-target sockets in the shared ``OscService`` cache are left
        # in place – they're reused if a future row targets the same
        # destination and can't be safely evicted without per-caller
        # ownership tracking.

    if osc_transmitters_changed or osc_destinations_changed or trigger_zones_changed:
        # Against the committed config, so a reverted routing apply keeps its records.
        app._runtime_services.forget_idle_osc_send_records()

    paths_changed = new_config.paths != app._config.paths
    if paths_changed:
        app._config.paths = new_config.paths
    engine = getattr(app, "_paths", None)
    paths_on = app._config.ui.feature_on("paths")
    if engine is not None and (paths_changed or engine.enabled != paths_on):
        engine.reload(app._config.paths, enabled=paths_on)

    # The MIDI ``apply_config`` is idempotent (same device on same patch is a
    # no-op), so re-applying on every reload pass is safe.
    if new_config.midi != app._config.midi or _flipped("midi"):
        app._config.midi = new_config.midi
        midi = getattr(app._runtime_services, "_midi", None)
        if midi is not None:
            midi.apply_config(new_config.midi.patches if app._config.ui.feature_on("midi") else [])

    # The bus's ``apply_config`` preserves current runtime values (only startup
    # resets to defaults) and resets pickup state for any fader whose source
    # mapping changed.
    if new_config.virtual_faders != app._config.virtual_faders:
        app._config.virtual_faders = new_config.virtual_faders
        bus = getattr(app._runtime_services, "_virtual_faders", None)
        if bus is not None:
            bus.apply_config(new_config.virtual_faders)

    # False when any ``_apply_with_fallback`` section degraded; the orchestrator
    # withholds the mtime advance so the reverted section retries next poll.
    return not _failures
