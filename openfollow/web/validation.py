# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""On-blur input validation registry and helpers for web forms."""

from __future__ import annotations

import ipaddress
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openfollow.configuration import (
    _DEFAULT_PSN_MCAST_IP,
    _DEFAULT_PSN_SYSTEM_NAME,
    MOUSE3D_AXES,
    MOUSE3D_AXIS_TARGETS,
    MOUSE3D_BUTTON_FIELDS,
    RESERVED_MOVEMENT_KEYS,
    VALID_BUTTON_NAMES,
    VALID_CURVES,
    VALID_KEY_NAMES,
    VALID_MARKER_FADER_STICKS,
    VALID_MOVE_LAYOUTS,
    VALID_OPERATOR_MESSAGE_SCALES,
    VALID_OSC_FRAMINGS,
    VALID_OSC_TRANSMITTER_PROTOCOLS,
    VALID_OSC_TRANSMITTER_RATES,
    VALID_STICKS,
    VALID_TRIGGER_EDGES,
    VALID_TRIGGER_KINDS,
    VALID_ZONE_EVAL_FPS,
    AppConfig,
    _canonical_marker_token,
)
from openfollow.web.routes import (
    _as_bool,
    _as_float,
    _as_float_or_zero,
    _as_int,
    _as_int_list,
    _as_ip_list,
    _as_optional_float,
    _as_optional_int,
    _as_positive_int,
    _as_str,
    _is_valid_service_name,
)

_FieldParser = Callable[[Any, Any], Any]
_CustomValidator = Callable[[str, "AppConfig | None"], "str | None"]


# --- Sanitiser --------------------------------------------------------------
# Strip control characters and bidi-override codepoints. The bidi-override
# range (U+202A–U+202E) lets a string look one way in a code review and
# render another way in the browser; the control range (U+0000–U+001F,
# U+007F) includes NUL, BEL, etc. that have no business in a config field.
# U+200E / U+200F (LTR/RTL marks) are also stripped – same family of
# direction-spoofing tricks.
_CONTROL_CHARS_RE = re.compile("[\x00-\x1f\x7f\u200e-\u200f\u202a-\u202e]")

# Subset of the above used to REJECT (not silently clean) input at validate
# time: NUL + non-whitespace C0 controls + DEL + bidi marks/overrides. Excludes
# \t\n\v\f\r (0x09-0x0d) which ``.strip()`` legitimately handles.
_DANGEROUS_TEXT_RE = re.compile("[\x00-\x08\x0e-\x1f\x7f\u200e-\u200f\u202a-\u202e]")


def _default_sanitiser(s: str) -> str:
    return _CONTROL_CHARS_RE.sub("", s)


# --- Type-error copy --------------------------------------------------------
# One line per parser kind. Falls through to a generic message if a future
# parser lands without an entry here – the parser-identity test catches
# the registry/parser drift before this fallback would matter at runtime.
_TYPE_ERRORS: dict[_FieldParser, str] = {
    _as_int: "Must be a whole number.",
    _as_optional_int: "Must be a whole number (or empty).",
    _as_positive_int: "Must be a whole number (1 or greater).",
    _as_float: "Must be a number.",
    _as_optional_float: "Must be a number (or empty).",
    _as_bool: "Must be one of: true, false, yes, no, on, off.",
    _as_int_list: "Must be a comma-separated list of whole numbers.",
    _as_ip_list: "Each entry must be a valid IPv4 or IPv6 address.",
}


def _default_type_error(parser: _FieldParser) -> str:
    return _TYPE_ERRORS.get(parser, "Invalid value.")


# --- FieldRule --------------------------------------------------------------
@dataclass(frozen=True)
class FieldRule:
    """Validation rule for a single web-form field.

    The contract: ``parser`` MUST be the same callable used at save-time
    (see ``routes._SECTION_FIELD_PARSERS``). ``lo`` / ``hi`` / ``choices``
    / ``pattern`` mirror the bounds enforced by the dataclass
    ``__post_init__``. ``max_len`` and the default sanitiser cover the
    string-hygiene cases that aren't in ``__post_init__`` today
    (control-char stripping, length caps).
    """

    parser: _FieldParser
    lo: float | int | None = None
    hi: float | int | None = None
    choices: tuple[str, ...] | None = None
    pattern: str | None = None  # regex applied to the raw (post-sanitise) string
    max_len: int | None = None
    sanitiser: Callable[[str], str] | None = None  # None → _default_sanitiser
    strip_whitespace: bool = True
    type_error: str | None = None  # None → derived from parser
    human_error: str = "Invalid value."
    custom: _CustomValidator | None = None  # extra validator after default checks


def _resolved_type_error(rule: FieldRule) -> str:
    return rule.type_error if rule.type_error is not None else _default_type_error(rule.parser)


# --- Validators -------------------------------------------------------------
_UNCOERCIBLE: Any = object()


def _normalize_input(rule: FieldRule, raw: Any) -> str:
    """Apply strip + sanitiser to produce the canonical input string."""
    if raw is None:
        return ""
    text = raw if isinstance(raw, str) else str(raw)
    if rule.strip_whitespace:
        text = text.strip()
    sanitiser = rule.sanitiser if rule.sanitiser is not None else _default_sanitiser
    return sanitiser(text)


def _check_path_traversal(value: str, root: Path | None) -> str | None:
    """Reject ``..`` segments and absolute paths that escape ``root``.

    ``root=None`` only enforces the no-traversal rule; with a root, also
    refuse absolute paths whose resolved form leaves the root tree.
    """
    p = Path(value)
    parts = p.parts
    if any(seg == ".." for seg in parts):
        return "Path may not contain '..' segments."
    if root is not None and p.is_absolute():
        try:
            p.resolve().relative_to(root.resolve())
        except (ValueError, OSError):
            return f"Absolute path must stay inside {root}."
    return None


def _validate_service_name(value: str, _cfg: AppConfig | None) -> str | None:
    if not value:
        return None
    if not _is_valid_service_name(value):
        # ``_is_valid_service_name`` also rejects a leading ``-`` (the
        # option-injection guard against ``systemctl restart --foo``);
        # mention that explicitly so the rejection isn't a mystery.
        return "Service name may contain only letters, digits, '_', '.', '@', '-'; must not start with '-'."
    return None


def _validate_ip_list(value: str, _cfg: AppConfig | None) -> str | None:
    """Per-entry IP validation. Surfaces the first offending entry."""
    if not value:
        return None
    entries = [e.strip() for e in value.split(",") if e.strip()]
    for idx, entry in enumerate(entries, start=1):
        try:
            ipaddress.ip_address(entry)
        except ValueError:
            return f"Entry {idx} ({entry!r}) is not a valid IPv4 or IPv6 address."
    return None


def _validate_multicast_ip(value: str, _cfg: AppConfig | None) -> str | None:
    """IPv4 address inside the multicast range (224.0.0.0–239.255.255.255).

    ``ip_address("236.10.10.10").is_multicast`` is the canonical check.
    Empty is allowed because ``OtpOutputConfig`` treats it as a fallback
    sentinel (unicast / loopback branch); callers that want to forbid
    empty values should layer an additional check on top.

    PSN / OTP sockets are created with ``socket.AF_INET`` so an IPv6
    multicast address would pass blur but fail at ``socket.bind``.
    Reject non-IPv4 here so the typo surfaces in the form, not at runtime.
    """
    if not value:
        return None
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return "Must be a valid multicast IPv4 address (224.0.0.0–239.255.255.255)."
    if not isinstance(addr, ipaddress.IPv4Address) or not addr.is_multicast:
        return "Must be a multicast IPv4 address (224.0.0.0–239.255.255.255)."
    return None


# RFC 1123 hostname (relaxed): labels of 1–63 chars, alnum + hyphen,
# label cannot start or end with hyphen. Matches ``host.example.com``,
# ``localhost``, ``my-server-1``. Used as a permissive fallback when a
# field accepts either a hostname or an IP.
_HOSTNAME_RE = re.compile(
    r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)


def _validate_host(value: str, _cfg: AppConfig | None) -> str | None:
    """IPv4 / IPv6 address OR a syntactically valid hostname.

    Used for fields like ``rttrpm_output.host`` and
    ``osc_destination.host`` where the receiver socket can
    resolve a hostname. Rejects values with internal whitespace,
    schemes, or paths so the operator notices the typo before the
    UDP send silently goes nowhere.
    """
    if not value:
        return None
    try:
        ipaddress.ip_address(value)
        return None
    except ValueError:
        pass
    if len(value) > 253 or not _HOSTNAME_RE.match(value):
        return "Must be a valid hostname or IPv4 / IPv6 address."
    return None


def _validate_int_list(value: str, _cfg: AppConfig | None) -> str | None:
    if not value:
        return None
    entries = [e.strip() for e in value.split(",") if e.strip()]
    for idx, entry in enumerate(entries, start=1):
        try:
            int(entry)
        except ValueError:
            return f"Entry {idx} ({entry!r}) is not a whole number."
    return None


def _validate_markers(value: str, _cfg: AppConfig | None) -> str | None:
    """Per-entry syntax check for the OSC transmitter ``markers`` field.

    Each entry must be a non-negative marker id, a controller alias
    (``c1``, ``c2``, …), or ``all``. A syntactically-valid id this station
    doesn't currently control is NOT flagged – it's silently ignored at
    send time (the station may gain control of it later, and OSC routing
    never crosses machines). Only malformed tokens block Save."""
    if not value:
        return None
    entries = [e.strip() for e in value.split(",") if e.strip()]
    for idx, entry in enumerate(entries, start=1):
        if _canonical_marker_token(entry) is None:
            return f"Entry {idx} ({entry!r}) must be a marker id, a controller alias (c1, c2, …), or 'all'."
    return None


def _validate_model(value: str, _cfg: AppConfig | None) -> str | None:
    """Model field: free-form filename, but reject traversal."""
    if not value:
        return None
    return _check_path_traversal(value, root=None)


def _validate_keybinding(value: str, _cfg: AppConfig | None) -> str | None:
    """Keyboard binding: must be in VALID_KEY_NAMES and not collide with movement."""
    if not value:
        return None  # empty means unbound
    if value not in VALID_KEY_NAMES:
        return "Not a recognised key (use a single letter or one of Shift/Control/Alt/Space/Tab)."
    if value in RESERVED_MOVEMENT_KEYS:
        reserved = "/".join(sorted(k.upper() for k in RESERVED_MOVEMENT_KEYS))
        return f"Key {value!r} collides with movement layout ({reserved})."
    return None


def _validate_osc_message(value: str, _cfg: AppConfig | None) -> str | None:
    """Combined ``osc_message`` field validator (quote-aware).

    The wire format is one freeform string parsed by
    :func:`openfollow.osc.parser.tokenize_osc_message`: the first
    token is the OSC address; the rest are args, with quoted strings
    (``"foo bar"``, ``'foo bar'``) preserved as single args. Args
    are not validated here; the renderer ignores unknown placeholder
    text and treats it as a literal, matching QLab's permissive
    behaviour.

    Two failure modes surface inline:

    - Unclosed quote (``/cue "unclosed``) → ``shlex.split`` raises
      ``ValueError``. Surface a clear inline error so the operator
      fixes it before Save.
    - Address doesn't start with ``/`` – same OSC 1.0 §2.1 rule.

    Empty / whitespace-only input is valid (no-op row).
    """
    if not value or not value.strip():
        return None
    from openfollow.osc.parser import tokenize_osc_message

    try:
        address, _args = tokenize_osc_message(value)
    except ValueError:
        # ``shlex.split``'s message is "No closing quotation". Reword
        # for an operator who's never heard of shlex. Suggest closing
        # the quote or switching styles so the advice works for both
        # quote shapes (double and single quotes).
        return (
            "Unclosed quote in OSC message. Close the string with the "
            "matching quote, or switch to the other quote style if the "
            "text contains the original quote character."
        )
    if not address.startswith("/"):
        return "OSC address must start with '/'."
    return None


# --- Helpers for common rules ----------------------------------------------
def _hex_color_rule(human_error: str = "Must be a #rrggbb hex colour like #ff8800.") -> FieldRule:
    return FieldRule(
        parser=_as_str,
        pattern=r"^#[0-9a-fA-F]{6}$",
        max_len=7,
        human_error=human_error,
    )


def _button_rule() -> FieldRule:
    """Controller button binding: VALID_BUTTON_NAMES (includes empty)."""
    return FieldRule(
        parser=_as_str,
        choices=tuple(sorted(VALID_BUTTON_NAMES)),
        max_len=16,
        human_error="Must be a controller button name (A, B, X, Y, BACK, START, LB, RB, LT, RT, DPAD_*, or empty).",
    )


def _key_rule() -> FieldRule:
    return FieldRule(
        parser=_as_str,
        max_len=16,
        custom=_validate_keybinding,
        human_error="Not a recognised key.",
    )


# --- Registry ---------------------------------------------------------------
# Order mirrors ``routes._SECTION_FIELD_PARSERS`` plus the special-section
# fields validated inline at routes.apply_section_data. Every entry MUST
# share its parser with the save-time path; the parser-identity test in
# tests/test_validation.py asserts ``rule.parser is _SECTION_FIELD_PARSERS[s][f]``
# for every overlapping (s, f).

# Marker movement-related max for grid offsets: the legacy partial used
# step="any" with no bound, so we leave lo/hi unset. Boolean fields are
# registered (for parser-identity) even though their type_error is the
# only failure mode they ever hit.

FIELD_RULES: dict[str, dict[str, FieldRule]] = {
    "camera": {
        "pos_x": FieldRule(_as_float, human_error="Must be a number."),
        "pos_y": FieldRule(_as_float, human_error="Must be a number."),
        "pos_z": FieldRule(_as_float, human_error="Must be a number."),
        "pitch": FieldRule(_as_float, lo=-180.0, hi=180.0, human_error="Pitch must be between -180° and 180°."),
        "yaw": FieldRule(_as_float, lo=-180.0, hi=180.0, human_error="Yaw must be between -180° and 180°."),
        "roll": FieldRule(_as_float, lo=-180.0, hi=180.0, human_error="Roll must be between -180° and 180°."),
        "fov": FieldRule(_as_float, lo=1.0, hi=179.0, human_error="FOV must be between 1° and 179°."),
        "sensor_width_mm": FieldRule(
            _as_optional_float, lo=0.0, human_error="Sensor width must be a positive number (or empty)."
        ),
        "focal_length_mm": FieldRule(
            _as_optional_float, lo=0.0, human_error="Focal length must be a positive number (or empty)."
        ),
        "lens_k1": FieldRule(_as_float, lo=-0.4, hi=0.4, human_error="Must be between -0.4 and 0.4."),
        "lens_k2": FieldRule(_as_float, lo=-0.2, hi=0.2, human_error="Must be between -0.2 and 0.2."),
    },
    "grid": {
        "visible": FieldRule(_as_bool),
        "width": FieldRule(_as_float, lo=0.1, human_error="Width must be at least 0.1 m."),
        "depth": FieldRule(_as_float, lo=0.1, human_error="Depth must be at least 0.1 m."),
        "spacing": FieldRule(_as_float, lo=0.1, human_error="Spacing must be at least 0.1 m."),
        "x_offset": FieldRule(_as_float, human_error="Must be a number."),
        "y_offset": FieldRule(_as_float, human_error="Must be a number."),
        "z_offset": FieldRule(_as_float, human_error="Must be a number."),
        # Vertical extent of the volume above the grid plane. No bounds
        # enforced (anything can happen in art). ``GridConfig.__post_init__``
        # collapses negative inputs to ``0`` (= unset, which is also the
        # default). Blur uses the same ``_as_float_or_zero`` parser as the
        # save path so a non-finite (``1e5000`` → ``inf``) or malformed
        # (``"tall"``) value surfaces a type error instead of slipping
        # through and being silently zeroed on save.
        "max_height": FieldRule(
            _as_float_or_zero,
            type_error="Must be a finite number.",
            human_error="Must be a finite number.",
        ),
        "color": _hex_color_rule(),
        "thickness": FieldRule(_as_positive_int, lo=1, hi=20, human_error="Thickness must be between 1 and 20 px."),
        "transparency": FieldRule(_as_float, lo=0.0, hi=1.0, human_error="Opacity must be between 0 and 1."),
        "origin_visible": FieldRule(_as_bool),
        "origin_length": FieldRule(_as_float, lo=0.1, human_error="Origin length must be at least 0.1 m."),
        "origin_thickness": FieldRule(
            _as_int, lo=1, hi=20, human_error="Origin thickness must be between 1 and 20 px."
        ),
    },
    "movement": {
        "min_speed": FieldRule(_as_float, lo=0.0, human_error="Min speed must be ≥ 0."),
        "max_speed": FieldRule(_as_float, lo=0.0, human_error="Max speed must be ≥ 0."),
        "move_speed": FieldRule(_as_float, lo=0.0, human_error="Move speed must be ≥ 0."),
        "default_pos_x": FieldRule(_as_float, human_error="Must be a number."),
        "default_pos_y": FieldRule(_as_float, human_error="Must be a number."),
        "default_pos_z": FieldRule(_as_float, human_error="Must be a number."),
    },
    "marker": {
        "ball_visible": FieldRule(_as_bool),
        "ball_size": FieldRule(_as_float, lo=0.0, human_error="Ball size must be ≥ 0."),
        "transparency": FieldRule(_as_float, lo=0.0, hi=1.0, human_error="Opacity must be between 0 and 1."),
        "crosshair_visible": FieldRule(_as_bool),
        "crosshair_size": FieldRule(_as_float, lo=0.0, human_error="Crosshair size must be ≥ 0."),
        "crosshair_color": _hex_color_rule(),
        "crosshair_thickness": FieldRule(
            _as_int, lo=1, hi=10, human_error="Crosshair thickness must be between 1 and 10 px."
        ),
        "drop_line": FieldRule(_as_bool),
        "drop_line_thickness": FieldRule(
            _as_int, lo=1, hi=20, human_error="Drop-line thickness must be between 1 and 20 px."
        ),
        "ground_circle": FieldRule(_as_bool),
        "ground_circle_size": FieldRule(_as_float, lo=0.0, human_error="Ground circle size must be ≥ 0."),
        "ground_circle_filled": FieldRule(_as_bool),
        "z_display_from_stage": FieldRule(_as_bool),
    },
    "controller": {
        "enabled": FieldRule(_as_bool),
        "keyboard_enabled": FieldRule(_as_bool),
        "mouse_enabled": FieldRule(_as_bool),
        "mouse_hysteresis_px": FieldRule(
            _as_int, lo=0, hi=200, human_error="Mouse hysteresis must be a whole number of pixels between 0 and 200."
        ),
        "mouse_smoothing": FieldRule(_as_float, lo=0.0, hi=1.0, human_error="Mouse smoothing must be between 0 and 1."),
        "mouse_max_y": FieldRule(
            _as_float, lo=0.0, hi=10000.0, human_error="Maximum Y+ must be between 0 and 10000 m."
        ),
        "mouse_wheel_z_enabled": FieldRule(_as_bool),
        "mouse_wheel_invert": FieldRule(_as_bool),
        "mouse_wheel_z_step": FieldRule(
            _as_float, lo=0.0, hi=10.0, human_error="Wheel Z step must be between 0 and 10 m."
        ),
        "mouse_double_click_reset": FieldRule(_as_bool),
        "deadzone": FieldRule(_as_float, lo=0.0, hi=1.0, human_error="Deadzone must be between 0 and 1."),
        "invert_y": FieldRule(_as_bool),
        "curve": FieldRule(
            _as_str, choices=VALID_CURVES, human_error=f"Curve must be one of: {', '.join(VALID_CURVES)}."
        ),
        "btn_reset": _button_rule(),
        "btn_source_select": _button_rule(),
        "btn_toggle_help": _button_rule(),
        "btn_speed_down": _button_rule(),
        "btn_speed_up": _button_rule(),
        "btn_move_z_down": _button_rule(),
        "btn_move_z_up": _button_rule(),
        "btn_toggle_zones": _button_rule(),
        "btn_next_marker": _button_rule(),
        "btn_prev_marker": _button_rule(),
        "btn_settings": _button_rule(),
        "btn_menu_confirm": _button_rule(),
        "btn_menu_cancel": _button_rule(),
        "btn_clear_messages": _button_rule(),
        "move_xy_stick": FieldRule(
            _as_str, choices=VALID_STICKS, human_error=f"Stick must be one of: {', '.join(VALID_STICKS)}."
        ),
        # Marker-fader integrator (gamepad stick → the controlled
        # marker's fader). Empty string is the dropdown's "(unused)"
        # option; the two non-empty choices match the typed enum on
        # ``ControllerConfig.marker_fader_stick``.
        "marker_fader_stick": FieldRule(
            _as_str,
            choices=VALID_MARKER_FADER_STICKS,
            human_error=("Marker fader stick must be empty (unused), 'left_y', or 'right_y'."),
        ),
        "marker_fader_max_speed_s": FieldRule(
            _as_float,
            lo=0.05,
            hi=60.0,
            human_error=("Marker fader max speed must be between 0.05 and 60 seconds."),
        ),
        "map_a": _button_rule(),
        "map_b": _button_rule(),
        "map_x": _button_rule(),
        "map_y": _button_rule(),
        "map_back": _button_rule(),
        "map_start": _button_rule(),
        "map_lb": _button_rule(),
        "map_rb": _button_rule(),
        "map_dpad_up": _button_rule(),
        "map_dpad_down": _button_rule(),
        "map_dpad_left": _button_rule(),
        "map_dpad_right": _button_rule(),
        "key_move_layout": FieldRule(
            _as_str, choices=VALID_MOVE_LAYOUTS, human_error=f"Layout must be one of: {', '.join(VALID_MOVE_LAYOUTS)}."
        ),
        "key_move_z_up": _key_rule(),
        "key_move_z_down": _key_rule(),
        "key_reset": _key_rule(),
        "key_toggle_help": _key_rule(),
        "key_toggle_zones": _key_rule(),
        "key_speed_down": _key_rule(),
        "key_speed_up": _key_rule(),
        "key_next_marker": _key_rule(),
        "key_prev_marker": _key_rule(),
        "key_settings": _key_rule(),
        "key_clear_messages": _key_rule(),
    },
    "osc": {
        "enabled": FieldRule(_as_bool),
        "port": FieldRule(_as_int, lo=1, hi=65535, human_error="Port must be between 1 and 65535."),
        "allowed_sender_ips": FieldRule(
            _as_ip_list, custom=_validate_ip_list, human_error="Comma-separated IPv4 / IPv6 addresses."
        ),
        # Empty = off (unicast/broadcast only); else a multicast group to join.
        "multicast_group": FieldRule(
            _as_str,
            max_len=64,
            custom=_validate_multicast_ip,
            human_error="Multicast IPv4 address (224.0.0.0–239.255.255.255) or blank.",
        ),
    },
    # OSC operator / next-cue overlay.
    "operator_messages": {
        "enabled": FieldRule(_as_bool),
        "position": FieldRule(_as_str, choices=("bottom", "top"), human_error="Position must be 'bottom' or 'top'."),
        "max_visible": FieldRule(_as_int, lo=1, hi=20, human_error="Max visible must be between 1 and 20."),
        "route_by_marker": FieldRule(_as_bool),
        "scale": FieldRule(
            _as_float,
            choices=tuple(str(v) for v in VALID_OPERATOR_MESSAGE_SCALES),
            human_error=(f"Scale must be one of: {', '.join(str(v) for v in VALID_OPERATOR_MESSAGE_SCALES)}."),
        ),
    },
    "otp_output": {
        "enabled": FieldRule(_as_bool),
        # Multicast addresses are derived from system_number per E1.59
        # Table 15-19 – there's no `mcast_ip` field to validate. The web UI
        # displays the computed addresses read-only.
        "port": FieldRule(_as_int, lo=1, hi=65535, human_error="Port must be between 1 and 65535."),
        # ``source_iface`` is an interface picker (a closed dropdown of valid
        # NICs), like ``psn_source_iface`` – no free-text FieldRule needed.
        "system_number": FieldRule(_as_int, lo=1, hi=200, human_error="System number must be between 1 and 200."),
        "priority": FieldRule(_as_int, lo=0, hi=200, human_error="Priority must be between 0 and 200."),
    },
    "rttrpm_output": {
        "enabled": FieldRule(_as_bool),
        "host": FieldRule(_as_str, max_len=255, custom=_validate_host, human_error="Host must be a hostname or IP."),
        "port": FieldRule(_as_int, lo=1, hi=65535, human_error="Port must be between 1 and 65535."),
        "fps": FieldRule(_as_positive_int, lo=1, hi=240, human_error="FPS must be between 1 and 240."),
        "context": FieldRule(_as_int, lo=0, hi=4294967295, human_error="Context must be between 0 and 4294967295."),
    },
    "detection": {
        "enabled": FieldRule(_as_bool),
        "model": FieldRule(_as_str, max_len=255, custom=_validate_model, human_error="Model filename."),
        # ``inference_size`` is processed inline at save-time (max(160, ...))
        # and snapped to multiples of 32 by ``DetectionConfig.__post_init__``.
        # Surface the snap as an advisory note (``note()``), not an error.
        "inference_size": FieldRule(
            _as_int, lo=160, hi=1280, human_error="Inference size must be between 160 and 1280."
        ),
        "pin_point": FieldRule(_as_str, choices=("top", "bottom"), human_error="Pin point must be 'top' or 'bottom'."),
        "preprocess_clahe": FieldRule(_as_bool),
        "confidence": FieldRule(_as_float, lo=0.0, hi=1.0, human_error="Confidence must be between 0 and 1."),
        "interval_ms": FieldRule(_as_int, lo=1, hi=10000, human_error="Interval must be between 1 and 10000 ms."),
        "show_boxes": FieldRule(_as_bool),
        "show_labels": FieldRule(_as_bool),
        "box_color": _hex_color_rule(),
        "box_thickness": FieldRule(_as_int, lo=1, hi=10, human_error="Box thickness must be between 1 and 10 px."),
        "max_persons": FieldRule(_as_int, lo=1, hi=50, human_error="Max persons must be between 1 and 50."),
        "pin_marker": FieldRule(_as_bool),
        "pin_marker_id": FieldRule(_as_int, lo=-1, human_error="Marker ID must be -1 (selected) or ≥ 0."),
        "smoothing": FieldRule(_as_float, lo=0.0, hi=1.0, human_error="Smoothing must be between 0 and 1."),
        "prediction": FieldRule(_as_float, lo=0.0, hi=20.0, human_error="Prediction must be between 0 and 20."),
        "grace_period_ms": FieldRule(
            _as_int, lo=0, hi=10000, human_error="Grace period must be between 0 and 10000 ms."
        ),
        "pin_mode": FieldRule(
            _as_str,
            choices=("replace", "assist"),
            human_error="Pin mode must be 'replace' or 'assist'.",
        ),
        "assist_radius_m": FieldRule(
            _as_float, lo=0.1, hi=50.0, human_error="Assist radius must be between 0.1 and 50 m."
        ),
        "assist_strength": FieldRule(_as_float, lo=0.0, hi=1.0, human_error="Assist strength must be between 0 and 1."),
    },
    "video_source": {
        # ``video_source_type`` is the only field common to every plugin.
        # Per-plugin fields (e.g. ``ndi_source_name``, ``srt_host``) are
        # not registered here – they're plugin-owned and free-form. The
        # consistency test skips inputs whose name isn't in this map, so
        # plugin templates aren't forced to add validation markup until
        # a future change widens the registry.
        "video_source_type": FieldRule(_as_str, max_len=32, human_error="Pick an installed video input plugin."),
    },
    "trigger_zones": {
        "enabled": FieldRule(_as_bool),
        "show_overlay": FieldRule(_as_bool),
        "eval_fps": FieldRule(
            _as_int,
            choices=tuple(str(v) for v in VALID_ZONE_EVAL_FPS),
            human_error=(f"Eval FPS must be one of: {', '.join(str(v) for v in VALID_ZONE_EVAL_FPS)}."),
        ),
        "debounce_ms": FieldRule(_as_int, lo=0, hi=60000, human_error="Debounce must be between 0 and 60000 ms."),
        "hysteresis": FieldRule(_as_float, lo=0.0, hi=10.0, human_error="Hysteresis must be between 0 and 10 m."),
    },
    # Shared OSC destination profiles (name + connection).
    "osc_destination": {
        "name": FieldRule(_as_str, max_len=64, human_error="Name must be 0–64 characters."),
        "host": FieldRule(_as_str, max_len=255, custom=_validate_host, human_error="Host must be a hostname or IP."),
        "port": FieldRule(_as_int, lo=1, hi=65535, human_error="Port must be between 1 and 65535."),
        "protocol": FieldRule(
            _as_str,
            choices=VALID_OSC_TRANSMITTER_PROTOCOLS,
            human_error=(f"Protocol must be one of: {', '.join(VALID_OSC_TRANSMITTER_PROTOCOLS)}."),
        ),
        "framing": FieldRule(
            _as_str,
            choices=VALID_OSC_FRAMINGS,
            human_error=(f"Framing must be one of: {', '.join(VALID_OSC_FRAMINGS)}."),
        ),
    },
    # Per-row OSC binding fields; flat section to avoid routing complexity.
    "osc_binding": {
        "enabled": FieldRule(_as_bool),
        "name": FieldRule(_as_str, max_len=64, human_error="Name must be 0–64 characters."),
        # Connection chosen via a shared OSC destination; empty allowed –
        # an unselected enabled row is a soft "no send", not a blur error.
        "destination_id": FieldRule(_as_str, max_len=64),
        # ``markers``: comma-separated marker ids / controller aliases (cN) /
        # ``all``; empty = no default marker. Malformed tokens block; a valid
        # but non-controlled id is ignored at send time, not flagged here.
        "markers": FieldRule(
            _as_str,
            max_len=256,
            custom=_validate_markers,
            human_error="Comma-separated marker ids, controller aliases (c1, c2, …), or 'all'.",
        ),
        # address + args input; template picker handles choice at row creation time.
        "osc_message": FieldRule(
            _as_str,
            max_len=2048,
            custom=_validate_osc_message,
            human_error=("OSC message must start with '/' and be ≤ 2048 characters."),
        ),
        # Accepts all trigger kinds to preserve config round-trip.
        "trigger.type": FieldRule(
            _as_str,
            choices=VALID_TRIGGER_KINDS,
            human_error=(f"Trigger type must be one of: {', '.join(VALID_TRIGGER_KINDS)}."),
        ),
        # Stream trigger.
        "trigger.rate_hz": FieldRule(
            _as_int,
            choices=tuple(str(v) for v in VALID_OSC_TRANSMITTER_RATES),
            human_error=(f"Rate must be one of: {', '.join(str(v) for v in VALID_OSC_TRANSMITTER_RATES)} Hz."),
        ),
        # Hotkey trigger. ``trigger.modifiers`` is a checkbox group;
        # we don't validate the multi-value here – the dataclass
        # post_init filters against ``VALID_TRIGGER_MODIFIERS``.
        "trigger.key": FieldRule(
            _as_str,
            choices=tuple(sorted(VALID_KEY_NAMES)),
            max_len=16,
            human_error="Pick a key from the list.",
        ),
        # Controller-button trigger.
        "trigger.button": FieldRule(
            _as_str,
            choices=tuple(sorted(VALID_BUTTON_NAMES)),
            max_len=16,
            human_error="Pick a controller button from the list.",
        ),
        # Edge applies to both Hotkey and ControllerButton.
        "trigger.edge": FieldRule(
            _as_str,
            choices=VALID_TRIGGER_EDGES,
            human_error=(f"Edge must be one of: {', '.join(VALID_TRIGGER_EDGES)}."),
        ),
    },
    # Per-zone OSC address fields; section name is zone (singular), routes through /api/zones.
    "zone": {
        # Per-marker filter; empty=any marker; uses same parser as general.controlled_marker_ids.
        "triggered_by": FieldRule(
            _as_int_list,
            max_len=512,
            custom=_validate_int_list,
            human_error="Comma-separated marker IDs (e.g. 0, 1, 5).",
        ),
        "osc_address_first_entry": FieldRule(
            _as_str,
            max_len=2048,
            custom=_validate_osc_message,
            human_error=("OSC message must start with '/' and be ≤ 2048 characters."),
        ),
        "osc_address_additional_entry": FieldRule(
            _as_str,
            max_len=2048,
            custom=_validate_osc_message,
            human_error=("OSC message must start with '/' and be ≤ 2048 characters."),
        ),
        "osc_address_partial_exit": FieldRule(
            _as_str,
            max_len=2048,
            custom=_validate_osc_message,
            human_error=("OSC message must start with '/' and be ≤ 2048 characters."),
        ),
        "osc_address_final_exit": FieldRule(
            _as_str,
            max_len=2048,
            custom=_validate_osc_message,
            human_error=("OSC message must start with '/' and be ≤ 2048 characters."),
        ),
    },
    # Special sections handled inline in apply_section_data.
    "psn": {
        "psn_system_name": FieldRule(_as_str, max_len=64, human_error="System name must be 1–64 characters."),
        "psn_mcast_ip": FieldRule(_as_str, max_len=64, custom=_validate_multicast_ip, human_error="PSN multicast IP."),
    },
    "general": {
        "psn_system_name": FieldRule(_as_str, max_len=64, human_error="System name must be 1–64 characters."),
        "psn_mcast_ip": FieldRule(_as_str, max_len=64, custom=_validate_multicast_ip, human_error="PSN multicast IP."),
        "web_port": FieldRule(_as_int, lo=1, hi=65535, human_error="Port must be between 1 and 65535."),
        "web_pin": FieldRule(
            _as_str, pattern=r"^[0-9]{1,32}$", max_len=32, human_error="PIN must be 1–32 digits (or empty)."
        ),
        "update_service_name": FieldRule(
            _as_str, max_len=128, custom=_validate_service_name, human_error="Service name."
        ),
        "controlled_marker_ids": FieldRule(
            _as_int_list, custom=_validate_int_list, human_error="Comma-separated marker IDs."
        ),
        "viewer_marker_ids": FieldRule(
            _as_int_list, custom=_validate_int_list, human_error="Comma-separated marker IDs."
        ),
    },
}

# Gamepad / keyboard / mouse share the controller rule map (a slice of the
# full ControllerConfig fields). Aliasing matches _SECTION_FIELD_PARSERS.
FIELD_RULES["gamepad"] = FIELD_RULES["controller"]
FIELD_RULES["keyboard"] = FIELD_RULES["controller"]
FIELD_RULES["mouse"] = FIELD_RULES["controller"]

# 3D Mouse: built from the same axis / button constants as
# routes._SECTION_FIELD_PARSERS, with the identical imported parser callables,
# so the parser-identity and template-consistency tests both hold.
_mouse3d_rules: dict[str, FieldRule] = {
    "enabled": FieldRule(_as_bool),
    "deadzone": FieldRule(_as_float, lo=0.0, hi=1.0, human_error="Deadzone must be between 0 and 1."),
    "curve": FieldRule(_as_str, choices=VALID_CURVES, human_error=f"Curve must be one of: {', '.join(VALID_CURVES)}."),
}
for _axis in MOUSE3D_AXES:
    _mouse3d_rules[f"map_{_axis}"] = FieldRule(
        _as_str,
        choices=MOUSE3D_AXIS_TARGETS,
        human_error=f"Axis target must be one of: {', '.join(MOUSE3D_AXIS_TARGETS)}.",
    )
    _mouse3d_rules[f"sens_{_axis}"] = FieldRule(
        _as_float, lo=0.0, hi=10.0, human_error="Sensitivity must be between 0 and 10."
    )
    _mouse3d_rules[f"invert_{_axis}"] = FieldRule(_as_bool)
for _btn in MOUSE3D_BUTTON_FIELDS:
    _mouse3d_rules[_btn] = FieldRule(
        _as_int, lo=-1, human_error="Button index must be -1 (unbound) or a device button number."
    )
FIELD_RULES["mouse3d"] = _mouse3d_rules


# --- Public API -------------------------------------------------------------
def validate(
    section: str,
    field: str,
    raw: Any,
    *,
    cfg: AppConfig | None = None,
) -> str | None:
    """Return an error string if ``raw`` is invalid for the given field, else None.

    Empty input returns ``None`` so an operator who clears a field doesn't
    see a stale "required" complaint on every keystroke. Use ``note()`` to
    surface advisory text for empty-with-fallback fields.
    """
    rule = FIELD_RULES.get(section, {}).get(field)
    if rule is None:
        return None  # unknown field – endpoint converts this to 404.
    value = _normalize_input(rule, raw)
    # Reject control / bidi-override codepoints rather than silently cleaning
    # them: the save path doesn't sanitise, so cleaning here would report
    # "valid" while the raw value (with the dangerous codepoints) is stored.
    if isinstance(raw, str) and _DANGEROUS_TEXT_RE.search(raw):
        return "Remove control or text-direction characters."
    if not value:
        return None
    parsed = rule.parser(value, _UNCOERCIBLE)
    if parsed is _UNCOERCIBLE:
        # List parsers (``_as_int_list`` etc.) collapse a single bad entry
        # to the sentinel default; in that case the custom validator's
        # per-entry message ("Entry 2 ('abc') is not a whole number.")
        # is more useful than the generic type error. The False-arm
        # (``custom is None`` when sentinel triggered) is reachable for
        # parser-only fields without a custom validator.
        if rule.custom is not None:
            custom_err = rule.custom(value, cfg)
            # pragma: no branch – every list parser registered here
            # surfaces a per-entry error when its parser would return
            # the sentinel; the False arm of ``custom_err is not None``
            # would only execute if a future custom validator failed
            # to detect the same syntactic mismatch the parser tripped
            # on (defensive fallthrough).
            if custom_err is not None:  # pragma: no branch
                return custom_err
        return _resolved_type_error(rule)
    if rule.max_len is not None and len(value) > rule.max_len:
        return f"Must be at most {rule.max_len} characters."
    if rule.pattern is not None and not re.fullmatch(rule.pattern, value):
        return rule.human_error
    if rule.choices is not None:
        # For numeric parsers, compare the canonicalised parsed value
        # against ``choices`` – save-time runs the parser too, so e.g.
        # ``"010"`` (parsed → 10) must match ``"10"`` in choices to keep
        # blur and save in lockstep.
        canonical = str(parsed) if isinstance(parsed, (int, float)) and not isinstance(parsed, bool) else value
        if canonical not in rule.choices:
            return rule.human_error
    if isinstance(parsed, (int, float)) and not isinstance(parsed, bool):
        # NaN / inf parse fine but ``__post_init__``'s _coerce_float rejects
        # them to the field default; surface that here so blur matches save
        # instead of reporting a non-finite value as "valid".
        if isinstance(parsed, float) and not math.isfinite(parsed):
            return _resolved_type_error(rule)
        # ``_as_positive_int`` silently clamps inputs < 1 to 1 to match
        # save-time behaviour. Without re-parsing through the unclamped
        # ``_as_int``, a typed ``0`` would pass the blur check (1 >= lo=1)
        # but visibly jump to ``1`` on the next render – confusing for
        # an operator who expected the form to flag the typo. Compare
        # bounds against the unclamped value so blur surfaces the same
        # error the save-time clamp silently corrects.
        bound_value: float = parsed
        if rule.parser is _as_positive_int:
            unclamped = _as_int(value, _UNCOERCIBLE)
            # pragma: no branch – ``_as_positive_int`` and ``_as_int``
            # share the same int coercion path. If ``parsed`` made it
            # past the earlier ``parsed is _UNCOERCIBLE`` guard, the
            # value is int-coercible and ``_as_int`` cannot return the
            # sentinel here. The False arm is defensive only.
            if unclamped is not _UNCOERCIBLE:  # pragma: no branch
                bound_value = unclamped
        if rule.lo is not None and bound_value < rule.lo:
            return rule.human_error
        if rule.hi is not None and bound_value > rule.hi:
            return rule.human_error
    if rule.custom is not None:
        custom_err = rule.custom(value, cfg)
        if custom_err is not None:
            return custom_err
    return None


def note(
    section: str,
    field: str,
    raw: Any,
    *,
    context: Mapping[str, Any] | None = None,
) -> str | None:
    """Return advisory text for cross-field auto-corrections, or None.

    Notes are advisory: they DO NOT set ``aria-invalid`` or gate Save.
    The server-side ``__post_init__`` chain still repairs the value on
    save. The web UI renders notes in a sibling ``<span class="field-note">``.
    """
    rule = FIELD_RULES.get(section, {}).get(field)
    if rule is None:
        return None
    value = _normalize_input(rule, raw)

    # Empty-with-fallback: psn identity defaults snap back if the operator
    # clears them. Surface as advisory so they understand what will save.
    if not value:
        if section in ("psn", "general") and field == "psn_system_name":
            return f"Empty value will be replaced with {_DEFAULT_PSN_SYSTEM_NAME!r}."
        if section in ("psn", "general") and field == "psn_mcast_ip":
            return f"Empty value will be replaced with {_DEFAULT_PSN_MCAST_IP!r}."
        return None

    if section == "movement" and field == "max_speed" and context is not None:
        max_val = _as_float(value, _UNCOERCIBLE)
        if max_val is _UNCOERCIBLE:
            return None
        min_raw = context.get("min_speed")
        if min_raw is None:
            return None
        min_val = _as_float(min_raw, _UNCOERCIBLE)
        if min_val is _UNCOERCIBLE:
            return None
        if max_val < min_val:
            return f"Will be raised to match Min Speed ({min_val})."
        return None

    if section == "detection" and field == "inference_size":
        size = _as_int(value, _UNCOERCIBLE)
        if size is _UNCOERCIBLE:
            return None
        if size < 160:
            return "Will be raised to 160 (minimum)."
        if size % 32 != 0:
            rounded = max(160, (size // 32) * 32)
            return f"Will be rounded down to {rounded} (multiple of 32)."
        return None

    return None


# Custom validators that read from ``AppConfig`` (e.g. allow-list checks).
# The ``/api/validate`` endpoint uses this set to decide whether to load
# the TOML config for a given blur – most validators ignore ``cfg``, and
# loading it on every keystroke (with ``hx-trigger="blur changed delay:200ms"``)
# turned the endpoint into per-keystroke disk I/O. Currently empty (no
# validator consults ``cfg``); kept as the seam for future cfg-aware rules.
_CFG_USING_VALIDATORS: frozenset[_CustomValidator] = frozenset()


def needs_cfg(rule: FieldRule) -> bool:
    """True iff ``validate`` would actually consult ``cfg`` for this rule."""
    return rule.custom in _CFG_USING_VALIDATORS


__all__ = [
    "FIELD_RULES",
    "FieldRule",
    "needs_cfg",
    "note",
    "validate",
]
