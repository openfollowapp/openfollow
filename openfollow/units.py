# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Display-unit conversion, formatting, and parsing.

Single source of truth for turning the internal **metric** model into
operator-facing strings and back. The data model, config storage, OSC,
PSN/RTTrPM/OTP, and scene math stay metric everywhere – only the web
UI and the on-device overlay route length / speed values through here.

Two unit systems, one global preference (``config.ui.unit_system``):

- ``metric``  – lengths in metres (3 dp), speed in m/s.
- ``imperial`` – lengths adaptive ft/in, speed in ft/s.

Imperial display is intentionally **lossy**: inches render to 2 dp
(≈0.01 in ≈ 0.25 mm of resolution), so ``parse(format(x))`` is only
guaranteed within ~0.0002 m of ``x``, not bit-exact. The web UI shows
a ``Stored: X.XXX m`` echo (``metric_echo`` of the *parsed* input) so
the operator always sees the exact value that will be persisted –
that's the contract, not a bit-exact round-trip.
"""

from __future__ import annotations

import math
import re
from enum import Enum


class UnitSystem(str, Enum):
    METRIC = "metric"
    IMPERIAL = "imperial"


# Exact SI conversion factors.
_M_PER_FT = 0.3048
_M_PER_IN = 0.0254
_FT_PER_M = 1.0 / _M_PER_FT
_IN_PER_FT = 12.0

# Unit token to metres conversion. Bare '' resolves to mode default at parse time.
_UNIT_TO_METERS: dict[str, float] = {
    "m": 1.0,
    "cm": 0.01,
    "mm": 0.001,
    "ft": _M_PER_FT,
    "'": _M_PER_FT,
    "in": _M_PER_IN,
    '"': _M_PER_IN,
}

# Parse length tokens; validate complete input.
_LEN_TOKEN_RE = re.compile(r"(\d*\.?\d+)\s*(mm|cm|ft|in|m|'|\")?", re.IGNORECASE)
_LEN_FULL_RE = re.compile(r"(?:\d*\.?\d+\s*(?:mm|cm|ft|in|m|'|\")?\s*)+", re.IGNORECASE)

_SPEED_RE = re.compile(r"([+-]?\d*\.?\d+)\s*(m/s|ft/s)?", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #


def format_length(meters: float, unit_system: UnitSystem) -> str:
    """Format metric length for display: ``"X.XXX m"`` or ``"X ft Y.YY in"``."""
    if unit_system is UnitSystem.METRIC:
        return f"{meters:.3f} m"
    sign = "-" if meters < 0 else ""
    a = abs(meters)
    total_in = a / _M_PER_IN
    # Inches-only form only when it won't round up to a full foot: a value
    # just under 1 ft (e.g. 11.998 in) would otherwise print "12.00 in", a
    # nonsensical imperial form – carry to "1 ft 0.00 in" instead.
    if a < _M_PER_FT and round(total_in, 2) < _IN_PER_FT:
        return f"{sign}{total_in:.2f} in"
    ft = int(total_in // _IN_PER_FT)
    rem_in = total_in - ft * _IN_PER_FT
    # Rounding can overflow to 12.00 in; carry to feet.
    if round(rem_in, 2) >= _IN_PER_FT:
        ft += 1
        rem_in = 0.0
    return f"{sign}{ft} ft {rem_in:.2f} in"


def format_length_compact(meters: float, unit_system: UnitSystem) -> str:
    """HUD compact length: fixed-width signed number, no unit suffix."""
    if unit_system is UnitSystem.METRIC:
        return f"{meters:+6.2f}"
    return f"{meters * _FT_PER_M:+6.2f}"


def format_speed(mps: float, unit_system: UnitSystem) -> str:
    """Format metric speed: ``"X.XX m/s"`` or ``"X.XX ft/s"``."""
    if unit_system is UnitSystem.METRIC:
        return f"{mps:.2f} m/s"
    return f"{mps * _FT_PER_M:.2f} ft/s"


def metric_echo(meters: float) -> str:
    """Return metric representation ``"X.XXX m"`` for UI echo of stored value."""
    return f"{meters:.3f} m"


def metric_echo_speed(mps: float) -> str:
    """Return metric speed representation ``"X.XXX m/s"`` for UI echo."""
    return f"{mps:.3f} m/s"


def unit_suffix_length(unit_system: UnitSystem) -> str:
    return "m" if unit_system is UnitSystem.METRIC else "ft / in"


def unit_suffix_speed(unit_system: UnitSystem) -> str:
    return "m/s" if unit_system is UnitSystem.METRIC else "ft/s"


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def parse_length(raw: str, unit_system: UnitSystem) -> float:
    """Parse operator input into metres: supports numbers, units (m/cm/mm/ft/in), and compound forms."""
    s = raw.strip()
    if not s:
        raise ValueError("empty length")
    sign = 1.0
    if s[0] in "+-":
        if s[0] == "-":
            sign = -1.0
        s = s[1:].strip()
    if not _LEN_FULL_RE.fullmatch(s):
        raise ValueError(f"unparseable length: {raw!r}")
    default_mult = _M_PER_FT if unit_system is UnitSystem.IMPERIAL else 1.0
    # ``_LEN_FULL_RE`` (one-or-more tokens) already matched, so finditer
    # yields at least one token.
    tokens = list(_LEN_TOKEN_RE.finditer(s))
    bare = [t for t in tokens if not t.group(2)]
    # Reject ambiguous input like "5 6 in" (bare number mixed with units).
    if bare and len(tokens) > 1:
        raise ValueError(f"ambiguous length (bare number with units): {raw!r}")
    total = 0.0
    for t in tokens:
        value = float(t.group(1))
        unit = (t.group(2) or "").lower()
        mult = _UNIT_TO_METERS[unit] if unit else default_mult
        total += value * mult
    result = sign * total
    if not math.isfinite(result):
        raise ValueError(f"length out of range: {raw!r}")
    return result


def parse_speed(raw: str, unit_system: UnitSystem) -> float:
    """Parse operator input into m/s; accepts m/s and ft/s explicitly, or bare number in mode default."""
    s = raw.strip()
    if not s:
        raise ValueError("empty speed")
    m = _SPEED_RE.fullmatch(s)
    if m is None:
        raise ValueError(f"unparseable speed: {raw!r}")
    value = float(m.group(1))
    if not math.isfinite(value):
        raise ValueError(f"speed out of range: {raw!r}")
    unit = (m.group(2) or "").lower()
    if unit == "m/s":
        return value
    if unit == "ft/s":
        return value * _M_PER_FT
    # Bare number → mode default.
    return value * (_M_PER_FT if unit_system is UnitSystem.IMPERIAL else 1.0)
