# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Configuration model, TOML I/O, and runtime hot-reload for OpenFollow."""

from __future__ import annotations

import ipaddress
import logging
import math
import os
import re
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    import tomllib
except ImportError:
    # Python 3.10 has no stdlib ``tomllib``; use the ``tomli`` backport.
    import tomli as tomllib  # type: ignore[no-redef]

import tomli_w

from openfollow.units import UnitSystem

if TYPE_CHECKING:
    from openfollow.app import OpenFollowApp

logger = logging.getLogger(__name__)


# Serializes load→mutate→``save_config`` spans against the on-disk config.
# ``save_config`` overwrites the whole file, so two interleaving spans would
# have the later save clobber the earlier. Held by the off-GTK-thread writers
# that run concurrently: the threaded WSGI request handlers and the
# marker-catalog sync receiver. GTK-main-loop writers (on-screen menu,
# receiver self-heal) do NOT take this lock and can still interleave.
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
    # pragma: no branch – every current caller passes ``lo=…``; the
    # signature keeps ``lo`` optional for symmetry with
    # ``_coerce_optional_float`` (which has callers that don't pass
    # bounds), so the False arm is structurally reachable but unused
    # at every present call site.
    if lo is not None:  # pragma: no branch
        out = max(lo, out)
    if hi is not None:
        out = min(hi, out)
    return out


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


def _coerce_optional_float(
    value: Any,
    default: float | None,
    *,
    lo: float | None = None,
    hi: float | None = None,
) -> float | None:
    """Like ``_coerce_float`` but preserves ``None`` for optional fields.

    When ``default`` is ``None``, invalid input collapses to ``None`` –
    substituting ``0.0`` would turn "not set" into "explicitly zero" for
    optional lens hints (``sensor_width_mm`` / ``focal_length_mm``).
    Catches ``OverflowError`` and rejects non-finite results as in
    ``_coerce_float`` (the lens helper divides by these).
    """
    if value is None:
        return None
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


@dataclass
class CameraConfig:
    pos_x: float = 0.0
    pos_y: float = -11.0
    pos_z: float = 6.0
    pitch: float = -22.0
    yaw: float = 0.0
    roll: float = 0.0
    fov: float = 60.0  # horizontal FOV in degrees
    # UI hints to derive FOV from sensor width + focal length (mm). Not read by
    # the projection math; ``fov`` remains the source of truth.
    sensor_width_mm: float | None = None
    focal_length_mm: float | None = None
    # Radial lens-distortion coefficients for the overlay-curvature correction.
    # Bow the rendered HUD to match a fisheye / wide-angle lens via
    # f = 1 + k1*r^2 + k2*r^4 (r normalised to the image half-diagonal). The
    # video frame is never warped. 0/0 = pinhole (no curvature).
    lens_k1: float = 0.0
    lens_k2: float = 0.0

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
        self.sensor_width_mm = _coerce_optional_float(self.sensor_width_mm, None, lo=0.0)
        self.focal_length_mm = _coerce_optional_float(self.focal_length_mm, None, lo=0.0)
        # Bounds cover strong wide-angle / fisheye lenses. The radial map
        # f = 1 + k1*r^2 + k2*r^4 stays positive and monotonic across the bulk
        # of the frame; near the extreme corner a strong barrel setting can
        # compress past monotonic, but the floored inverse (input path) stays
        # bounded there, so control never diverges.
        self.lens_k1 = _coerce_float(self.lens_k1, 0.0, lo=-0.4, hi=0.4)
        self.lens_k2 = _coerce_float(self.lens_k2, 0.0, lo=-0.2, hi=0.2)


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


@dataclass
class MarkerConfig:
    min_speed: float = 0.1
    max_speed: float = 3.0
    move_speed: float = 2.0
    default_pos_x: float = 0.0
    default_pos_y: float = 0.0
    default_pos_z: float = 1.6
    ball_visible: bool = True
    ball_size: float = 0.15
    transparency: float = 0.3
    crosshair_visible: bool = True
    crosshair_size: float = 0.3
    crosshair_color: str = "#ffffff"
    crosshair_thickness: int = 2
    drop_line: bool = True
    drop_line_thickness: int = 2
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
        self.ball_visible = _coerce_bool(self.ball_visible, True)
        self.crosshair_visible = _coerce_bool(self.crosshair_visible, True)
        self.drop_line = _coerce_bool(self.drop_line, True)
        self.ground_circle = _coerce_bool(self.ground_circle, True)
        self.ground_circle_filled = _coerce_bool(self.ground_circle_filled, False)
        self.z_display_from_stage = _coerce_bool(self.z_display_from_stage, True)
        self.ball_size = _coerce_float(self.ball_size, 0.15, lo=0.0)
        self.transparency = _coerce_float(self.transparency, 0.3, lo=0.0, hi=1.0)
        self.crosshair_size = _coerce_float(self.crosshair_size, 0.3, lo=0.0)
        self.crosshair_color = _coerce_hex_color(self.crosshair_color, "#ffffff")
        # Upper bounds mirror ``openfollow/web/validation.py``.
        self.crosshair_thickness = _coerce_int(self.crosshair_thickness, 2, lo=1, hi=10)
        self.drop_line_thickness = _coerce_int(self.drop_line_thickness, 2, lo=1, hi=20)
        self.ground_circle_size = _coerce_float(self.ground_circle_size, 0.3, lo=0.0)


_DETECTION_PIN_POINTS = ("top", "bottom")
# How the detection pin writes to the marker. ``replace`` overwrites the marker
# with the tracked (largest-box) detection – the original auto-pin. ``assist``
# is the two-marker hybrid: the operator steers a manual anchor freely while the
# AI-corrected output marker glides toward the *nearest* detection within
# ``assist_radius_m`` (or back to the anchor when none is in range).
_DETECTION_PIN_MODES = ("replace", "assist")


@dataclass
class DetectionConfig:
    enabled: bool = False
    model: str = "yolov8n.onnx"
    storage_path: str = ""
    inference_size: int = 640
    preprocess_clahe: bool = True
    confidence: float = 0.2
    interval_ms: int = 67
    show_boxes: bool = True
    show_labels: bool = True
    box_color: str = "#808080"
    box_thickness: int = 2
    max_persons: int = 10
    pin_marker: bool = False
    # Which marker the detection pin writes to when ``pin_marker`` is enabled.
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

    def __post_init__(self) -> None:
        # ``model`` / ``storage_path`` are rendered into templates and consumed
        # via ``Path(...)`` / ``.strip()``; both assume ``str``.
        self.model = _coerce_str(self.model, "yolov8n.onnx")
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


@dataclass
class OscConfig:
    enabled: bool = True
    port: int = 8765
    # Source-IP allowlist for the ``/marker/{id} x y z`` receiver. Empty =
    # allow-all (the handler logs a startup warning). Each entry is a literal
    # IPv4/IPv6 address; CIDR blocks are not supported.
    allowed_sender_ips: list[str] = field(default_factory=list)
    # IPv4 multicast group the listener joins; empty = off. Validated to
    # 224.0.0.0–239.255.255.255, else coerced to "".
    multicast_group: str = "239.20.20.20"

    def __post_init__(self) -> None:
        """Normalize ``allowed_sender_ips`` (or a bare string) into ``list[str]``."""
        self.port = _coerce_int(self.port, 8765, lo=1, hi=65535)
        raw = self.allowed_sender_ips
        if isinstance(raw, str):
            # A bare string becomes a single-entry list.
            candidates: list[object] = [raw]
        elif isinstance(raw, (list, tuple, set)):
            candidates = list(raw)
        else:
            candidates = []
        self.allowed_sender_ips = [entry.strip() for entry in candidates if isinstance(entry, str) and entry.strip()]
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
        # Normalize vertices to [float, float] pairs
        cleaned: list[list[float]] = []
        for v in self.vertices:
            if isinstance(v, (list, tuple)) and len(v) >= 2:
                try:
                    x, y = float(v[0]), float(v[1])
                except (TypeError, ValueError):
                    continue
                # Drop non-finite coords: ``float("nan")`` / ``float("inf")``
                # survive coercion but make every point-in-polygon comparison
                # False, silently corrupting membership tests for the zone.
                if not (math.isfinite(x) and math.isfinite(y)):
                    continue
                cleaned.append([x, y])
        self.vertices = cleaned
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
        self.patches = [MidiPatch(**_filter_known(MidiPatch, p)) if isinstance(p, dict) else p for p in self.patches]
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
        self.faders = [
            VirtualFaderConfig(**_filter_known(VirtualFaderConfig, f)) if isinstance(f, dict) else f
            for f in self.faders
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

    Operator-saved templates live as ``.openfollowtemplate`` JSON files under
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
    "btn_source_select",
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
    btn_source_select: str = "BACK"
    btn_toggle_help: str = "Y"
    btn_speed_down: str = "LB"
    btn_speed_up: str = "RB"
    btn_move_z_down: str = "LT"
    btn_move_z_up: str = "RT"
    btn_toggle_zones: str = "B"
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
    # differs from the one calibrated (e.g. a different unit, or the same pad
    # in a non-X-input hardware mode, which enumerates with a different GUID).
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

    def __post_init__(self) -> None:
        """Validate configuration values."""
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
    "btn_settings",
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
    btn_settings: int = -1

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


# The systemd unit restarted after a successful update. Also the recovery
# fallback when a box's persisted ``update_service_name`` is invalid.
DEFAULT_UPDATE_SERVICE_NAME = "openfollow"


# Single source of truth for the PSN identity defaults so the field defaults
# and the ``__post_init__`` non-string fallbacks can't drift apart.
_DEFAULT_PSN_SYSTEM_NAME = "OpenFollow"
_DEFAULT_PSN_MCAST_IP = "236.10.10.10"


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


@dataclass
class UiConfig:
    """Optional ``[ui]`` section – operator display preferences.

    ``unit_system`` drives length/speed rendering in the web UI and overlay.
    Internal storage, OSC, PSN/RTTrPM/OTP and template files are always
    metric – only display formatting and form-input parsing change. Stored as
    a plain string so TOML round-trips; consumers wrap it in ``UnitSystem``.
    """

    unit_system: str = "metric"  # "metric" | "imperial"

    # UI-visibility gate for experimental sections; does not start/stop any
    # subsystem (the save route handles the mouse + detection cascade).
    show_experimental_features: bool = False

    def __post_init__(self) -> None:
        valid = {m.value for m in UnitSystem}
        if not isinstance(self.unit_system, str):
            self.unit_system = "metric"
        choice = self.unit_system.strip().lower()
        self.unit_system = choice if choice in valid else "metric"
        self.show_experimental_features = _coerce_bool(self.show_experimental_features, False)


@dataclass
class AppConfig:
    # Video source
    video_source_type: str = "testpattern"  # "testpattern" | "ndi" | "srt" | "rtp" | "rtsp" | ...
    ndi_source_name: str = ""
    srt_host: str = "srt://0.0.0.0:5000"
    rtp_url: str = "rtp://0.0.0.0:5004"
    rtp_encoding: str = "H264"
    rtsp_url: str = "rtsp://0.0.0.0:554/stream"
    picam_camera_name: str = ""
    picam_width: int = 1920
    picam_height: int = 1080
    picam_framerate: int = 30
    v4l2_device: str = "/dev/video0"
    v4l2_render_resolution: str = "1080p"
    v4l2_framerate: int = 30
    avf_unique_id: str = ""
    avf_device_index: int = -1
    avf_width: int = 1920
    avf_height: int = 1080
    avf_framerate: int = 30
    testpattern_resolution: str = "1080p"
    testpattern_pattern: str = "stage"

    # Video recovery (network inputs: RTSP/SRT/RTP). ``stall_timeout`` is the
    # silent-stall watchdog – seconds an established stream may deliver no
    # frames before it's torn down and reconnected (0 = off). ``heal_interval``
    # is how often a feed parked on the no-signal placeholder re-probes its URL
    # so it recovers on its own after the source/network returns (0 = off).
    stall_timeout: float = 3.0
    heal_interval: float = 5.0

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

    # Web-triggered update settings (signed-.deb GitHub-release installer)
    update_github_repo: str = "openfollowapp/openfollow"
    update_service_name: str = DEFAULT_UPDATE_SERVICE_NAME

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
    marker: MarkerConfig = field(default_factory=MarkerConfig)
    controller: ControllerConfig = field(default_factory=ControllerConfig)
    mouse3d: Mouse3DConfig = field(default_factory=Mouse3DConfig)
    osc: OscConfig = field(default_factory=OscConfig)
    operator_messages: OperatorMessagesConfig = field(default_factory=OperatorMessagesConfig)
    otp_output: OtpOutputConfig = field(default_factory=OtpOutputConfig)
    rttrpm_output: RttrpmOutputConfig = field(default_factory=RttrpmOutputConfig)
    osc_transmitters: OscTransmittersConfig = field(default_factory=OscTransmittersConfig)
    osc_destinations: OscDestinationsConfig = field(default_factory=OscDestinationsConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    trigger_zones: TriggerZonesConfig = field(default_factory=TriggerZonesConfig)
    midi: MidiConfig = field(default_factory=MidiConfig)
    virtual_faders: VirtualFadersConfig = field(
        default_factory=VirtualFadersConfig,
    )
    network: NetworkConfig = field(default_factory=NetworkConfig)
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
        self.psn_system_name = self.psn_system_name.strip() or _DEFAULT_PSN_SYSTEM_NAME
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


# ---------------------------------------------------------------------------
# TOML I/O
# ---------------------------------------------------------------------------


def _filter_known(cls: type, data: dict[str, Any]) -> dict[str, Any]:
    """Strip keys that don't match any field on *cls* (stale TOML entries)."""
    known = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in known}


_SUB_CONFIG_MAP: dict[str, type] = {
    "camera": CameraConfig,
    "grid": GridConfig,
    "marker": MarkerConfig,
    "controller": ControllerConfig,
    "mouse3d": Mouse3DConfig,
    "osc": OscConfig,
    "operator_messages": OperatorMessagesConfig,
    "otp_output": OtpOutputConfig,
    "rttrpm_output": RttrpmOutputConfig,
    "osc_transmitters": OscTransmittersConfig,
    "osc_destinations": OscDestinationsConfig,
    "detection": DetectionConfig,
    "trigger_zones": TriggerZonesConfig,
    "midi": MidiConfig,
    "virtual_faders": VirtualFadersConfig,
    "network": NetworkConfig,
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


def _load_backup_data(path: str) -> dict[str, Any] | None:
    """Parse the ``<path>.bak`` snapshot ``save_config`` writes, or ``None``.

    Returns ``None`` when the backup is absent, unreadable, or unparseable so
    recovery degrades to defaults rather than raising.
    """
    try:
        with open(path + ".bak", "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None


def load_config(path: str = "config.toml", *, strict: bool = False) -> AppConfig:
    """Load config from a TOML file.

    Args:
        path: Path to the TOML file.
        strict: When True, re-raise parse/read errors instead of logging and
            returning defaults; missing files still return defaults. Used by
            hot-reload, which must not replace the running config with defaults.
            Strict also skips the ``.bak`` recovery.

    On the non-strict path, a missing or unparseable primary file is recovered
    from the ``config.toml.bak`` snapshot, degrading to defaults when the
    backup is also absent or unparseable.

    Returns:
        Populated ``AppConfig``; defaults for any missing fields.
    """
    try:
        with open(path, "rb") as f:
            data: dict[str, Any] = tomllib.load(f)
    except FileNotFoundError:
        # Strict mode (hot-reload): a missing file is not an error and must not
        # pull in a stale backup. Otherwise recover from the ``.bak``.
        if not strict:
            backup = _load_backup_data(path)
            if backup is not None:
                logger.warning("Config file %s is missing – recovered from %s.bak.", path, path)
                data = backup
            else:
                return AppConfig()
        else:
            return AppConfig()
    except Exception:
        if strict:
            raise
        # Catches any read/parse failure (TOML decode, permissions, …);
        # recover from the ``.bak`` snapshot.
        backup = _load_backup_data(path)
        if backup is not None:
            logger.warning(
                "Config file %s could not be read/parsed – recovered from %s.bak.",
                path,
                path,
                exc_info=True,
            )
            data = backup
        else:
            logger.exception("Failed to read/parse config file %s – using defaults.", path)
            return AppConfig()

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

    # Marker id 0 is reserved as "ignored" on the PSN wire; strip it from the
    # selection lists so a hand-edited config can't smuggle a 0 past
    # ``Marker.__init__``. A non-list value coerces to ``[]`` (``init_markers``
    # expects an iterable of ints). The backcompat block above guarantees both
    # keys are present, so there's no missing-key case to guard.
    for _field_name in ("controlled_marker_ids", "viewer_marker_ids"):
        _raw = data[_field_name]
        if not isinstance(_raw, list):
            logger.warning(
                "%s: %s is %s, not a list – coercing to [] so selection-list iteration can't crash startup",
                path,
                _field_name,
                type(_raw).__name__,
            )
            data[_field_name] = []
            continue
        # Dedup, preserving first-seen order: a duplicate id would render two
        # marker cards and double-register the marker with PSN / RTTrPM on hot
        # reload.
        _seen: set[int] = set()
        _filtered: list[int] = []
        for _v in _raw:
            if isinstance(_v, bool) or not isinstance(_v, int):
                continue
            if _v < 1:
                logger.warning(
                    "%s: dropping reserved marker id %r from %s",
                    path,
                    _v,
                    _field_name,
                )
                continue
            if _v in _seen:
                continue
            _seen.add(_v)
            _filtered.append(_v)
        data[_field_name] = _filtered

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


_DEPRECATED_WARNED: set[str] = set()


def _warn_deprecated_controller_bindings(controller: ControllerConfig) -> None:
    """Emit a one-shot warning for deprecated controller bindings.

    - The ``btn_source_select`` direct-entry shortcut was superseded by
      the Settings menu (``btn_settings``).
    - Mode-specific confirm/cancel pairs were consolidated into a single
      ``btn_menu_confirm`` / ``btn_menu_cancel`` used by every menu.

    ``load_config`` is invoked on every hot-reload, so a module-level set
    tracks which fields already warned to keep logs from flooding across
    reloads.
    """
    defaults = ControllerConfig()
    direct_entry_fields = ("btn_source_select",)
    for field_name in direct_entry_fields:
        if field_name in _DEPRECATED_WARNED:
            continue
        current = getattr(controller, field_name)
        if current != getattr(defaults, field_name):
            logger.warning(
                "Config field controller.%s=%r is deprecated (issue #71): direct shortcut "
                "removed – use the Settings menu (btn_settings, default BACK) instead.",
                field_name,
                current,
            )
            _DEPRECATED_WARNED.add(field_name)
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
                "Config field controller.%s=%r is deprecated (issue #71): per-menu "
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


def save_config(config: AppConfig, path: str = "config.toml") -> None:
    """Serialize the full config to a TOML file, overwriting existing content.

    Durable, atomic write: content is streamed to a temp file in the same
    directory, ``fsync``-ed, ``os.replace``-d into place, then the parent
    directory is ``fsync``-ed so the rename survives power loss. A crash leaves
    either the old or new file intact, never a truncated one. The previous
    config is snapshotted to ``config.toml.bak`` best-effort (a copy failure is
    logged and swallowed); ``load_config`` recovers from it when the primary is
    missing or unreadable.
    """
    data = config_to_toml_dict(config)
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
        # Snapshot the current good config to ``<name>.bak`` before the swap
        # so an unparseable primary can be recovered. Written atomically (temp
        # + ``os.replace``) so a partial snapshot can't truncate the last
        # recovery point. A backup failure is logged and swallowed.
        if target.exists():
            backup_path = target.with_name(target.name + ".bak")
            try:
                bak_fd, bak_tmp = tempfile.mkstemp(
                    prefix=backup_path.name + ".",
                    suffix=".tmp",
                    dir=str(directory),
                )
                os.close(bak_fd)
                try:
                    shutil.copy2(target, bak_tmp)
                    # copy2 copies the source's mode onto bak_tmp, so a
                    # world-readable primary would yield a world-readable .bak
                    # despite mkstemp's 0o600. Re-tighten – the .bak holds the
                    # same web_pin secret as the primary.
                    os.chmod(bak_tmp, 0o600)
                    # fsync the snapshot's contents before the rename so power
                    # loss can't leave an empty/truncated ``.bak``.
                    tmp_fd = os.open(bak_tmp, os.O_RDONLY)
                    try:
                        os.fsync(tmp_fd)
                    finally:
                        os.close(tmp_fd)
                    os.replace(bak_tmp, backup_path)
                    _fsync_dir(directory)
                except OSError:
                    # Drop the partial temp so a failed backup doesn't litter
                    # ``<name>.bak.*.tmp`` next to the config, then re-raise to
                    # the best-effort handler below.
                    try:
                        os.unlink(bak_tmp)
                    except OSError:  # pragma: no cover - unlink of our own temp
                        pass
                    raise
            except OSError:
                # Swallowed (the save itself is fine) but log with the full
                # path + traceback – the cause (permissions, disk full, …) is
                # exactly what's needed to diagnose a missing .bak later.
                logger.warning(
                    "Could not write config backup %s; continuing",
                    backup_path,
                    exc_info=True,
                )
        os.replace(tmp_path, path)
        _fsync_dir(directory)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Runtime Hot-Reload
# ---------------------------------------------------------------------------


def _apply_with_fallback(
    name: str,
    apply_fn: Callable[[], None],
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
        apply_fn: Callable[[], None],
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
    new_psn_mcast_ip = new_config.psn_mcast_ip.strip()
    new_psn_source_iface = new_config.psn_source_iface.strip()
    psn_iface_changed = new_psn_source_iface != app._config.psn_source_iface
    psn_mcast_changed = new_psn_mcast_ip != app._config.psn_mcast_ip
    if psn_iface_changed:
        # Resolve the bind IP up-front so receiver/server rebind to the same
        # value ``init_psn`` uses. Fallback matches startup so clearing the pin
        # still binds to a real interface instead of going dead.
        from openfollow.net_utils import resolve_source_ip

        new_resolved_source_ip, _new_resolve_status = resolve_source_ip(
            new_psn_source_iface,
        )
    else:
        new_resolved_source_ip = ""

    if psn_mcast_changed and psn_iface_changed:
        old_psn_mcast_ip = app._config.psn_mcast_ip
        old_psn_source_iface = app._config.psn_source_iface
        app._config.psn_mcast_ip = new_psn_mcast_ip
        app._config.psn_source_iface = new_psn_source_iface
        if not _apply(
            "psn_network",
            lambda: app._runtime_services.apply_psn_source_ip_change(
                new_resolved_source_ip,
                new_mcast_ip=new_psn_mcast_ip,
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

    # PSN iface single-field path: only fires when iface changed alone (the
    # combined block above handles the iface+mcast case). The bind IP comes
    # from the resolver, so changing the pin rebinds to its current IPv4.
    if psn_iface_changed and not psn_mcast_changed:
        old_psn_source_iface = app._config.psn_source_iface
        app._config.psn_source_iface = new_psn_source_iface
        if not _apply(
            "psn_source_iface",
            lambda: app._runtime_services.apply_psn_source_ip_change(
                new_resolved_source_ip,
            ),
        ):
            app._config.psn_source_iface = old_psn_source_iface
        # Re-sync the stale-iface advisory so the PSN web partial tracks the
        # now-active pin (cleared on a honoured pin, restored on rollback).
        app._refresh_psn_source_advisory()

    # Video pipeline live-swap. Change detection is plugin-driven via the
    # active source's ``config_changed(old, new)`` – covers ``video_source_type``
    # and per-plugin fields (rtsp_url, srt_host, …). Distinct from the
    # ``detection`` block below.
    from openfollow.video.inputs import get_input_class

    video_changed = new_config.video_source_type != app._config.video_source_type
    if not video_changed:
        input_cls = get_input_class(new_config.video_source_type)
        # pragma: no branch – every video_source_type written via the
        # web UI passes through the registry, so input_cls is always
        # non-None at this point. The False arm guards against a
        # hand-edited config.toml with a removed plugin.
        if input_cls is not None:  # pragma: no branch
            video_changed = input_cls.config_changed(app._config, new_config)
    if video_changed:
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

        if not _apply(
            "video_source",
            lambda: app._runtime_services.swap_video(new_config),
        ):
            app._config.video_source_type = old_video_source_type
            for name, value in old_field_values.items():
                setattr(app._config, name, value)

    # Grid and camera updates (no renderer needed - Cairo overlay reads from config)
    if new_config.grid != app._config.grid:
        app._config.grid = new_config.grid

    if new_config.camera != app._config.camera:
        app._camera = Camera.from_config(new_config.camera)
        app._config.camera = new_config.camera

    if new_config.marker != app._config.marker:
        app._config.marker = new_config.marker

    if new_config.marker_move_speeds != app._config.marker_move_speeds:
        # Readers go through ``get_marker_move_speed``, so a direct swap
        # suffices – no service restart.
        app._config.marker_move_speeds = new_config.marker_move_speeds

    # ``unit_system`` is read live every frame by ``sync_ui_config`` and
    # ``show_experimental_features`` gates the web UI off ``app._config``; a
    # direct swap is enough (the save routes own the mouse/detection cascade).
    if new_config.ui != app._config.ui:
        app._config.ui = new_config.ui

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
            tid
            for tid in new_config.controlled_marker_ids
            if isinstance(tid, int) and not isinstance(tid, bool) and tid >= 1
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
            old_names = {tid: _marker_name_for_runtime(app, tid) for tid in to_remove}

            def _apply_controlled() -> None:
                for tid in to_remove:
                    server.remove_marker(tid)
                for tid in to_add:
                    server.add_marker(tid, _marker_name_for_runtime(app, tid))

                # Mirror marker changes to OTP output if active
                if app._otp_server is not None:
                    for tid in to_remove:
                        app._otp_server.unregister_marker(tid)
                    for tid in to_add:
                        marker = server.get_marker(tid)
                        # pragma: no branch – tid was just added to the
                        # server above, so ``get_marker`` always returns it.
                        if marker is not None:  # pragma: no branch
                            app._otp_server.register_marker(marker)

                # Mirror marker changes to RTTrPM output if active
                if app._rttrpm_server is not None:
                    for tid in to_remove:
                        app._rttrpm_server.unregister_marker(tid)
                    for tid in to_add:
                        marker = server.get_marker(tid)
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
                for tid in to_add:
                    server.remove_marker(tid)
                for tid in to_remove:
                    server.add_marker(tid, old_names[tid])

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
            tid
            for tid in new_config.viewer_marker_ids
            if isinstance(tid, int) and not isinstance(tid, bool) and tid >= 1
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
            )

    # ``enabled`` toggles the OSC ingest adapter; ``max_visible`` /
    # ``position`` / ``scale`` are read live by the overlay builder.
    if new_config.operator_messages != app._config.operator_messages:
        app._config.operator_messages = new_config.operator_messages
        if app._input_manager is not None:  # pragma: no branch
            app._input_manager.restart_operator_messages()

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

    if new_config.rttrpm_output != app._config.rttrpm_output:
        old_rttrpm_output = app._config.rttrpm_output
        app._config.rttrpm_output = new_config.rttrpm_output
        # Same lifecycle ownership as OTP: orchestrator nulls the
        # server reference iff rollback also fails.
        if not _apply(
            "rttrpm_output",
            lambda: app._runtime_services.apply_rttrpm_output_change(
                new_config.rttrpm_output,
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
    trigger_zones_changed = new_config.trigger_zones != app._config.trigger_zones
    if osc_transmitters_changed or osc_destinations_changed:
        old_osc_transmitters = app._config.osc_transmitters
        old_osc_destinations = app._config.osc_destinations
        app._config.osc_transmitters = new_config.osc_transmitters
        app._config.osc_destinations = new_config.osc_destinations

        def _reload_zone_destinations(destinations: OscDestinationsConfig) -> None:
            zone_engine = getattr(app._runtime_services, "_zone_engine", None)
            if zone_engine is not None:
                zone_engine.reload_config(app._config.trigger_zones, destinations)

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

    if new_config.detection != app._config.detection:
        old_detection = app._config.detection
        new_enabled = new_config.detection.enabled
        # Two live-apply paths:
        #   - ``apply_detection_change`` for on→on reload, on→off, off→off –
        #     served by the in-process worker's staged-config drain.
        #   - ``swap_detector`` for transitions needing a pipeline rebuild
        #     (the detection appsink caps are pinned at build time from
        #     ``PersonDetector.input_resolution``): off→on, unavailable→on
        #     (re-probe a backend that failed to load), and ``inference_size``
        #     change while enabled.
        detector_ref = getattr(app._runtime_services, "_person_detector", None)
        detector_runnable = detector_ref is not None and getattr(detector_ref, "available", True)
        inference_size_changed = old_detection.inference_size != new_config.detection.inference_size
        needs_swap = new_enabled and (not detector_runnable or inference_size_changed)
        app._config.detection = new_config.detection
        if needs_swap:
            # Rebuild path. ``swap_detector`` owns its own rollback –
            # on failure it restores the prior detector against a
            # rebuilt pipeline (or clears the reference if rollback
            # also fails). The dispatcher only reverts stored config;
            # an ``on_failure`` clear here would erase a successful
            # orchestrator-side rollback.
            if not _apply(
                "detection_swap",
                lambda: app._runtime_services.swap_detector(
                    new_config.detection,
                ),
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
                lambda: app._runtime_services.apply_detection_change(
                    new_config.detection,
                ),
                on_failure=lambda: setattr(
                    app._runtime_services,
                    "_person_detector",
                    None,
                ),
            ):
                app._config.detection = old_detection

    if new_config.trigger_zones != app._config.trigger_zones:
        app._config.trigger_zones = new_config.trigger_zones
        zone_engine = getattr(app._runtime_services, "_zone_engine", None)
        if zone_engine is not None:
            # Use the committed destinations (``app._config``) so a zones edit
            # stays coherent with an OSC-routing apply that was reverted above.
            zone_engine.reload_config(
                new_config.trigger_zones,
                app._config.osc_destinations,
            )
        # The endpoint is resolved from ``osc_destinations`` at send time.
        # Stale per-target sockets in the shared ``OscService`` cache are left
        # in place – they're reused if a future row targets the same
        # destination and can't be safely evicted without per-caller
        # ownership tracking.

    # The MIDI ``apply_config`` is idempotent (same device on same patch is a
    # no-op), so re-applying on every reload pass is safe.
    if new_config.midi != app._config.midi:
        app._config.midi = new_config.midi
        midi = getattr(app._runtime_services, "_midi", None)
        if midi is not None:
            midi.apply_config(new_config.midi.patches)

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
