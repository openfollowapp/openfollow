"""Digit-grid model for editing an IPv4 value on a gamepad.

The on-screen network field editor is reachable without a keyboard, so the
static-address fields need an entry method a d-pad can drive. Gamepad input in
the Settings overlay is edge-triggered throughout, so a per-octet increment
would need held-state key repeat: timers, and tests that depend on wall-clock.

This models the value as a fixed grid of 4 octets x 3 digits instead. Up/down
cycles one digit 0-9 wrapping, so any digit is at most five presses away, and
left/right walks the cursor. Every function here is pure, so the widget needs no
clock at all.

Values carry their zero padding while the grid is being edited (``192.168.001.005``)
because that is what keeps the cursor position and the character it points at in
fixed correspondence. :func:`strip_padding` removes it on commit.
"""

from __future__ import annotations

OCTETS = 4
DIGITS_PER_OCTET = 3
DIGIT_SLOTS = OCTETS * DIGITS_PER_OCTET


def to_grid(value: str) -> str:
    """Return *value* as ``DIGIT_SLOTS`` digits, ignoring its dots.

    Anything unparseable degrades to zeros rather than raising: the editor is
    pre-filled from live config that may be blank, and a half-typed value has
    to survive the operator reaching for the d-pad mid-edit.
    """
    parts = value.split(".")[:OCTETS]
    digits = ""
    for i in range(OCTETS):
        part = parts[i] if i < len(parts) else ""
        part = "".join(c for c in part if c.isdigit())[:DIGITS_PER_OCTET]
        digits += part.rjust(DIGITS_PER_OCTET, "0")
    return digits


def from_grid(digits: str) -> str:
    """Render *digits* as a zero-padded dotted quad for display and editing."""
    digits = digits.rjust(DIGIT_SLOTS, "0")[:DIGIT_SLOTS]
    return ".".join(digits[i : i + DIGITS_PER_OCTET] for i in range(0, DIGIT_SLOTS, DIGITS_PER_OCTET))


def strip_padding(value: str) -> str:
    """Drop each octet's leading zeros, leaving a value the parsers accept.

    ``ipaddress`` rejects leading zeros outright, so a grid-edited value has to
    lose them before it reaches validation. Only a four-part all-digit quad is
    touched: the subnet field also accepts a bare prefix length, which must pass
    through unchanged.
    """
    parts = value.split(".")
    if len(parts) != OCTETS or not all(p.isdigit() for p in parts):
        return value
    return ".".join(str(int(p)) for p in parts)


def move_cursor(index: int, delta: int) -> int:
    """Step the cursor across the digit slots, stopping at either end.

    Clamped rather than wrapping: running off the last digit and landing back on
    the first reads as the value having changed when it has not.
    """
    return max(0, min(DIGIT_SLOTS - 1, index + delta))


def bump_digit(digits: str, index: int, delta: int) -> str:
    """Cycle the digit at *index* by *delta*, wrapping 0-9.

    Wrapping is what keeps 255 one press from 0; clamping would put the common
    high octets nine presses away.
    """
    digits = digits.rjust(DIGIT_SLOTS, "0")[:DIGIT_SLOTS]
    if not 0 <= index < DIGIT_SLOTS:
        return digits
    current = int(digits[index]) if digits[index].isdigit() else 0
    return digits[:index] + str((current + delta) % 10) + digits[index + 1 :]
