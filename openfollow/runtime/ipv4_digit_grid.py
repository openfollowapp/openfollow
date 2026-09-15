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


MAX_OCTET = 255


def _octet_bounds(index: int) -> tuple[int, int]:
    """Slice bounds of the octet containing digit slot *index*."""
    start = (index // DIGITS_PER_OCTET) * DIGITS_PER_OCTET
    return start, start + DIGITS_PER_OCTET


def bump_digit(digits: str, index: int, delta: int) -> str:
    """Cycle the digit at *index* by *delta*, wrapping 0-9.

    Wrapping is what keeps 255 one press from 0; clamping would put the common
    high octets nine presses away.

    Values that would take the octet past 255 are skipped rather than offered,
    so a dotted quad the grid produced is always a real address. The
    alternative - let the operator dial ``648`` and reject it at save - spends
    the error on a banner several presses after the mistake, and leaves them to
    work out which octet it meant.
    """
    digits = digits.rjust(DIGIT_SLOTS, "0")[:DIGIT_SLOTS]
    if not 0 <= index < DIGIT_SLOTS:
        return digits
    if delta == 0:
        return digits
    current = int(digits[index]) if digits[index].isdigit() else 0
    step = 1 if delta > 0 else -1
    start, end = _octet_bounds(index)
    for hop in range(1, 11):
        candidate = digits[:index] + str((current + step * hop) % 10) + digits[index + 1 :]
        octet = candidate[start:end]
        if octet.isdigit() and int(octet) <= MAX_OCTET:
            return candidate
    # Every value of this digit leaves the octet out of range, which only a
    # buffer that was already invalid can produce. Leave it be rather than
    # rewrite digits the operator did not touch.
    return digits


def is_grid_form(value: str) -> bool:
    """True when *value* is a fully padded grid, i.e. the d-pad has edited it.

    While that holds, a digit slot and a character position are in fixed
    correspondence, so a caret can be drawn under the digit the cursor names.
    A freely typed value has no such correspondence.
    """
    parts = value.split(".")
    return len(parts) == OCTETS and all(len(p) == DIGITS_PER_OCTET and p.isdigit() for p in parts)


def caret_offset(index: int) -> int:
    """Character offset of digit slot *index* within the dotted grid form.

    Steps over the dot separating each octet, so the caret lands on the digit
    the cursor names rather than drifting one place left per octet crossed.
    """
    index = max(0, min(DIGIT_SLOTS - 1, index))
    return index + index // DIGITS_PER_OCTET
