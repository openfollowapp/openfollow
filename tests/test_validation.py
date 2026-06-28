# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for :mod:`openfollow.web.validation`.

Covers:
- Parser-identity contract: every shared (section, field) pair uses the
  same parser as ``routes._SECTION_FIELD_PARSERS`` so blur validation can
  never disagree with what the server does on Save.
- Parser-driven type errors (sentinel default → ``type_error``).
- Range / choices / pattern / max_len / custom checks.
- ``note()`` advisory output for the cross-field auto-corrections
  (max_speed clamp, inference_size snap, psn empty-fallback).
- Whitespace + control-char sanitisation: validate-time output mirrors
  what ``__post_init__`` would persist.
"""

from __future__ import annotations

import pytest

from openfollow.configuration import AppConfig
from openfollow.web import validation
from openfollow.web.routes import (
    _SECTION_FIELD_PARSERS,
    _as_float,
    _as_int,
    _as_str,
)
from openfollow.web.validation import (
    _CONTROL_CHARS_RE,
    FIELD_RULES,
    FieldRule,
    _check_path_traversal,
    _default_sanitiser,
    _default_type_error,
    _normalize_input,
    _resolved_type_error,
    _validate_host,
    _validate_int_list,
    _validate_ip_list,
    _validate_keybinding,
    _validate_markers,
    _validate_model,
    _validate_multicast_ip,
    _validate_service_name,
    note,
    validate,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Parser identity: registry mirrors _SECTION_FIELD_PARSERS field-for-field.
# ---------------------------------------------------------------------------

_OVERLAPPING_FIELDS = [
    (section, field)
    for section, parsers in _SECTION_FIELD_PARSERS.items()
    for field in parsers
    if section in FIELD_RULES and field in FIELD_RULES[section]
]


@pytest.mark.parametrize("section,field", _OVERLAPPING_FIELDS)
def test_field_rule_parser_matches_save_path(section: str, field: str) -> None:
    """Every shared (section, field) pair uses the save-time parser."""
    rule = FIELD_RULES[section][field]
    assert rule.parser is _SECTION_FIELD_PARSERS[section][field], (
        f"{section}.{field}: blur parser must match save-time parser"
    )


def test_section_aliases_share_controller_map() -> None:
    """gamepad / keyboard / mouse alias controller – same identity."""
    assert FIELD_RULES["gamepad"] is FIELD_RULES["controller"]
    assert FIELD_RULES["keyboard"] is FIELD_RULES["controller"]
    assert FIELD_RULES["mouse"] is FIELD_RULES["controller"]


# ---------------------------------------------------------------------------
# validate(): unknown / empty input handling.
# ---------------------------------------------------------------------------


def test_validate_unknown_section_returns_none() -> None:
    assert validate("nope", "fov", "200") is None


def test_validate_unknown_field_returns_none() -> None:
    assert validate("camera", "nope", "200") is None


@pytest.mark.parametrize("raw", ["", "   ", None, "\t\n  "])
def test_validate_empty_input_returns_none(raw: object) -> None:
    """Empty / whitespace-only input is never an error – let the user clear a field."""
    assert validate("camera", "fov", raw) is None


# ---------------------------------------------------------------------------
# validate(): type-error path (parser returns sentinel).
# ---------------------------------------------------------------------------


def test_validate_float_type_error() -> None:
    assert validate("camera", "fov", "wide") == _default_type_error(_as_float)


def test_validate_int_type_error() -> None:
    assert validate("osc", "port", "abc") == _default_type_error(_as_int)


def test_validate_bool_type_error() -> None:
    err = validate("controller", "enabled", "maybe")
    assert err is not None
    assert "true" in err.lower()


def test_validate_int_list_type_error_via_parser() -> None:
    # _as_int_list returns the sentinel on a malformed CSV (ValueError).
    err = validate("general", "controlled_marker_ids", "1, abc, 3")
    assert err is not None
    assert "Entry" in err  # custom validator surfaces specific entry


def test_validate_ip_list_per_entry_error() -> None:
    err = validate("osc", "allowed_sender_ips", "192.168.1.1, 999.0.0.1")
    assert err is not None
    assert "Entry 2" in err
    assert "999.0.0.1" in err


def test_validate_float_within_range() -> None:
    assert validate("camera", "fov", "60") is None


def test_validate_rejects_non_finite_float() -> None:
    # NaN/inf parse fine but save resets them to default – blur must reject.
    assert validate("camera", "pos_x", "nan") is not None
    assert validate("camera", "pos_x", "inf") is not None
    assert validate("camera", "pos_x", "infinity") is not None
    assert validate("camera", "fov", "nan") is not None  # bounded field too
    assert validate("grid", "transparency", "nan") is not None


def test_validate_rejects_control_and_bidi_chars() -> None:
    # Save path doesn't sanitise, so the validator rejects rather than clean.
    assert validate("osc_binding", "name", "My\x00Name") is not None
    assert validate("osc_binding", "name", "My‮Name") is not None  # RTL override
    assert validate("general", "psn_system_name", "Bad\x07Name") is not None
    # A clean value still validates.
    assert validate("osc_binding", "name", "Cue 1") is None


# ---------------------------------------------------------------------------
# validate(): range / pattern / choices / max_len / custom branches.
# ---------------------------------------------------------------------------


def test_validate_range_low() -> None:
    err = validate("camera", "fov", "0.5")
    assert err is not None
    assert "FOV" in err


def test_validate_range_high() -> None:
    err = validate("camera", "fov", "200")
    assert err is not None
    assert "FOV" in err


@pytest.mark.parametrize(
    "field,raw",
    [("lens_k1", "0.5"), ("lens_k1", "-0.5"), ("lens_k2", "0.5"), ("lens_k2", "-0.5")],
)
def test_validate_lens_distortion_out_of_range(field: str, raw: str) -> None:
    err = validate("camera", field, raw)
    assert err is not None
    assert "between" in err.lower()


@pytest.mark.parametrize("field,raw", [("lens_k1", "0.1"), ("lens_k2", "-0.03")])
def test_validate_lens_distortion_in_range_ok(field: str, raw: str) -> None:
    assert validate("camera", field, raw) is None


@pytest.mark.parametrize("section", ["grid", "marker"])
def test_validate_opacity_out_of_range_uses_opacity_wording(section: str) -> None:
    """The 0-1 alpha control is opacity (1 = fully opaque, 0 = invisible), so
    its out-of-range message reads "Opacity", not the inverted "Transparency"."""
    err = validate(section, "transparency", "2")
    assert err is not None
    assert "Opacity" in err
    assert "Transparency" not in err


def test_validate_pattern_failure() -> None:
    err = validate("grid", "color", "#zzz")
    assert err is not None
    assert "hex" in err.lower()


def test_validate_pattern_success() -> None:
    assert validate("grid", "color", "#ff8800") is None


# grid.max_height accepts any number; reject non-numeric.


@pytest.mark.parametrize("value", ["0", "0.0", "4", "4.5", "100", "-1", "-3.14", "5000"])
def test_validate_grid_max_height_accepts_any_number(value: str) -> None:
    assert validate("grid", "max_height", value) is None


def test_validate_grid_max_height_rejects_non_numeric() -> None:
    err = validate("grid", "max_height", "tall")
    assert err is not None


@pytest.mark.parametrize("value", ["1e5000", "-1e5000", "inf", "-inf", "nan"])
def test_validate_grid_max_height_rejects_non_finite(value: str) -> None:
    """Non-finite strings (inf, nan) must be rejected before GridConfig.__post_init__ silently zeros them."""
    err = validate("grid", "max_height", value)
    assert err is not None


def test_validate_choices_failure() -> None:
    err = validate("controller", "curve", "exponential")
    assert err is not None
    assert "Curve" in err


def test_validate_choices_success() -> None:
    assert validate("controller", "curve", "linear") is None


def test_validate_numeric_choices_canonical_match() -> None:
    # eval_fps with "010" parses to 10; blur must treat it as canonical "10".
    assert validate("trigger_zones", "eval_fps", "010") is None
    assert validate("trigger_zones", "eval_fps", "10") is None


def test_validate_numeric_choices_unknown_value_still_rejected() -> None:
    # 7 is not a valid eval_fps (save-time snaps it to 5 with a warning,
    # but the UI uses a hardcoded ``<select>`` so direct API hits with
    # "7" should still surface as an error).
    err = validate("trigger_zones", "eval_fps", "7")
    assert err is not None


def test_validate_max_len_failure() -> None:
    long = "x" * 600
    err = validate("detection", "model", long)
    assert err is not None
    assert "characters" in err


def test_validate_service_name() -> None:
    assert validate("general", "update_service_name", "openfollow") is None
    err = validate("general", "update_service_name", "open follow")
    assert err is not None


def test_validate_model_traversal_rejected() -> None:
    err = validate("detection", "model", "../../yolov8n.onnx")
    assert err is not None


def test_validate_keybinding_invalid_key() -> None:
    err = validate("controller", "key_reset", "F1")
    assert err is not None


def test_validate_keybinding_movement_collision() -> None:
    err = validate("controller", "key_reset", "w")
    assert err is not None
    assert "movement" in err


def test_validate_keybinding_valid() -> None:
    assert validate("controller", "key_reset", "x") is None


def test_validate_keybinding_allows_e() -> None:
    # "e" is not a movement key, so it is accepted as the lower-Z binding.
    assert validate("controller", "key_move_z_down", "e") is None


def test_validate_button_choice_failure() -> None:
    err = validate("controller", "btn_reset", "FOO")
    assert err is not None


def test_validate_button_choice_empty_allowed() -> None:
    # Empty string is in VALID_BUTTON_NAMES but bypassed by validate's
    # empty-input early return; cover that path explicitly.
    assert validate("controller", "btn_reset", "") is None


def test_validate_inference_size_within_range() -> None:
    assert validate("detection", "inference_size", "320") is None


def test_validate_inference_size_below_min() -> None:
    err = validate("detection", "inference_size", "100")
    assert err is not None


def test_validate_optional_float_uncoercible() -> None:
    err = validate("camera", "sensor_width_mm", "abc")
    assert err is not None


def test_validate_optional_float_valid() -> None:
    assert validate("camera", "sensor_width_mm", "10") is None


# ---------------------------------------------------------------------------
# note(): cross-field advisories.
# ---------------------------------------------------------------------------


def test_note_unknown_returns_none() -> None:
    assert note("nope", "field", "value") is None


def test_note_max_speed_below_min() -> None:
    msg = note("movement", "max_speed", "0.5", context={"min_speed": "1.0"})
    assert msg is not None
    assert "Min Speed" in msg


def test_note_max_speed_within_min_no_message() -> None:
    assert note("movement", "max_speed", "5.0", context={"min_speed": "1.0"}) is None


def test_note_max_speed_no_context_returns_none() -> None:
    assert note("movement", "max_speed", "0.5") is None


def test_note_max_speed_empty_min_returns_none() -> None:
    assert note("movement", "max_speed", "0.5", context={}) is None


def test_note_max_speed_uncoercible_value_returns_none() -> None:
    assert (
        note(
            "movement",
            "max_speed",
            "abc",
            context={"min_speed": "1.0"},
        )
        is None
    )


def test_note_max_speed_uncoercible_min_returns_none() -> None:
    assert (
        note(
            "movement",
            "max_speed",
            "0.5",
            context={"min_speed": "abc"},
        )
        is None
    )


def test_note_inference_size_snap_advice() -> None:
    msg = note("detection", "inference_size", "200")
    assert msg is not None
    assert "192" in msg


def test_note_inference_size_below_minimum() -> None:
    msg = note("detection", "inference_size", "100")
    assert msg is not None
    assert "160" in msg


def test_note_inference_size_already_snapped_no_message() -> None:
    assert note("detection", "inference_size", "320") is None


def test_note_inference_size_uncoercible_returns_none() -> None:
    assert note("detection", "inference_size", "wide") is None


def test_note_psn_system_name_empty_fallback_psn_section() -> None:
    msg = note("psn", "psn_system_name", "")
    assert msg is not None
    assert "OpenFollow" in msg


def test_note_psn_system_name_empty_fallback_general_section() -> None:
    msg = note("general", "psn_system_name", "")
    assert msg is not None


def test_note_psn_mcast_ip_empty_fallback() -> None:
    msg = note("psn", "psn_mcast_ip", "")
    assert msg is not None
    assert "236.10.10.10" in msg


def test_note_psn_mcast_ip_general_empty_fallback() -> None:
    msg = note("general", "psn_mcast_ip", "")
    assert msg is not None


def test_note_other_field_empty_returns_none() -> None:
    assert note("camera", "fov", "") is None


def test_note_psn_system_name_with_value_returns_none() -> None:
    assert note("psn", "psn_system_name", "MyShow") is None


# ---------------------------------------------------------------------------
# Whitespace / sanitiser.
# ---------------------------------------------------------------------------


def test_validate_strips_leading_trailing_whitespace() -> None:
    # Both forms must produce the same result.
    assert validate("camera", "fov", "  60  ") is None
    assert validate("camera", "fov", "60") is None


@pytest.mark.parametrize(
    ("field", "good", "bad"),
    [
        ("mouse_hysteresis_px", "12", "300"),
        ("mouse_smoothing", "0.4", "5"),
        ("mouse_max_y", "25", "99999"),
        ("mouse_wheel_z_step", "0.25", "50"),
    ],
)
def test_validate_mouse_numeric_bounds(field: str, good: str, bad: str) -> None:
    # The mouse partial validates against the controller rules (aliased).
    assert validate("mouse", field, good) is None
    assert validate("mouse", field, bad) is not None


def test_validate_mouse_hysteresis_rejects_decimals() -> None:
    # Hysteresis is a whole number of pixels; a decimal must be rejected.
    assert validate("mouse", "mouse_hysteresis_px", "3.5") is not None
    assert validate("mouse", "mouse_hysteresis_px", "5") is None


def test_validate_mouse_smoothing_accepts_zero() -> None:
    # 0 = instant (no smoothing) is a valid value now; only > 1 is out of range.
    assert validate("mouse", "mouse_smoothing", "0") is None
    assert validate("mouse", "mouse_smoothing", "1") is None
    assert validate("mouse", "mouse_smoothing", "1.5") is not None


def test_validate_rejects_dangerous_control_chars_but_strips_whitespace() -> None:
    # NUL is rejected (not silently cleaned) so blur matches the unsanitised
    # save path; a leading tab is whitespace → stripped → the hex still passes.
    assert validate("grid", "color", "\x00#ff8800") is not None
    assert validate("grid", "color", "\t#ff8800") is None


def test_default_sanitiser_strips_bidi_marks() -> None:
    s = "abc‮def‎"
    assert _default_sanitiser(s) == "abcdef"


def test_default_sanitiser_passes_clean_string() -> None:
    assert _default_sanitiser("hello world") == "hello world"


def test_normalize_input_handles_none() -> None:
    rule = FIELD_RULES["camera"]["fov"]
    assert _normalize_input(rule, None) == ""


def test_normalize_input_handles_non_string() -> None:
    rule = FIELD_RULES["camera"]["fov"]
    assert _normalize_input(rule, 60) == "60"


def test_normalize_input_strip_whitespace_disabled() -> None:
    rule = FieldRule(_as_str, strip_whitespace=False)
    assert _normalize_input(rule, "  hello  ") == "  hello  "


def test_normalize_input_custom_sanitiser() -> None:
    rule = FieldRule(_as_str, sanitiser=str.upper)
    assert _normalize_input(rule, "hello") == "HELLO"


def test_default_type_error_unknown_parser_falls_back() -> None:
    # A parser that isn't in _TYPE_ERRORS uses the generic message.
    def fake(_v: object, default: object) -> object:
        return default

    assert _default_type_error(fake) == "Invalid value."


def test_resolved_type_error_uses_explicit_when_set() -> None:
    rule = FieldRule(_as_int, type_error="custom message")
    assert _resolved_type_error(rule) == "custom message"


def test_resolved_type_error_falls_back_to_parser_default() -> None:
    rule = FIELD_RULES["camera"]["fov"]
    assert _resolved_type_error(rule) == _default_type_error(_as_float)


# ---------------------------------------------------------------------------
# _check_path_traversal direct coverage.
# ---------------------------------------------------------------------------


def test_check_path_traversal_dotdot_rejected() -> None:
    assert _check_path_traversal("../foo", root=None) is not None


def test_check_path_traversal_relative_clean() -> None:
    assert _check_path_traversal("foo/bar", root=None) is None


def test_check_path_traversal_absolute_no_root_ok() -> None:
    # path_root=None – absolute paths only fail the .. check, not the
    # escape check (since there is no root to compare against).
    assert _check_path_traversal("/etc/hosts", root=None) is None


def test_check_path_traversal_absolute_inside_root() -> None:
    from pathlib import Path

    assert _check_path_traversal("/tmp/foo", root=Path("/tmp")) is None


def test_check_path_traversal_absolute_outside_root() -> None:
    from pathlib import Path

    err = _check_path_traversal("/etc/passwd", root=Path("/tmp"))
    assert err is not None
    assert "/tmp" in err


# ---------------------------------------------------------------------------
# Sanity: every registered string field passes validate() on its current
# config default. (Round-trip: defaults load → validate → no error.)
# ---------------------------------------------------------------------------


def test_default_config_passes_every_registered_rule() -> None:
    """Round-trip: default config values pass blur validation.

    Lists are converted to their CSV form (the wire shape submitted by the
    web form) before validating; bools / numbers are stringified the same
    way the browser would.
    """
    cfg = AppConfig()
    for section, rules in FIELD_RULES.items():
        for field, _rule in rules.items():
            value = _value_for(cfg, section, field)
            if value is None:
                continue
            wire = _to_wire(value)
            err = validate(section, field, wire, cfg=cfg)
            assert err is None, f"{section}.{field}: default {value!r} (wire={wire!r}) failed validation: {err}"


def _to_wire(value: object) -> object:
    """Convert a Python config value to the string form a form field submits."""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _value_for(cfg: AppConfig, section: str, field: str) -> object:
    """Return the on-disk value for (section, field), or None if not derivable."""
    # Section aliases.
    section_attr = {
        "movement": "marker",
        "gamepad": "controller",
        "keyboard": "controller",
        "mouse": "controller",
    }.get(section, section)
    # Special-section fields living on AppConfig directly.
    if section in ("psn", "general", "video_source") and hasattr(cfg, field):
        return getattr(cfg, field)
    sub = getattr(cfg, section_attr, None)
    if sub is None:
        return None
    return getattr(sub, field, None)


# ---------------------------------------------------------------------------
# CONTROL_CHARS_RE: covers the bidi range explicitly.
# ---------------------------------------------------------------------------


def test_control_chars_re_matches_nul() -> None:
    assert _CONTROL_CHARS_RE.search("a\x00b") is not None


def test_control_chars_re_matches_del() -> None:
    assert _CONTROL_CHARS_RE.search("a\x7fb") is not None


def test_control_chars_re_matches_bidi_override() -> None:
    assert _CONTROL_CHARS_RE.search("a‮b") is not None


def test_control_chars_re_passes_normal_string() -> None:
    assert _CONTROL_CHARS_RE.search("abcDEF 123 – em-dash") is None


# ---------------------------------------------------------------------------
# Direct coverage on custom validator helpers (covers the empty-input
# early returns and the all-valid normal-path returns that ``validate()``
# bypasses or short-circuits).
# ---------------------------------------------------------------------------


def test_validate_service_name_empty() -> None:
    assert _validate_service_name("", None) is None


def test_validate_service_name_valid() -> None:
    assert _validate_service_name("openfollow", None) is None


def test_validate_ip_list_empty() -> None:
    assert _validate_ip_list("", None) is None


def test_validate_ip_list_all_valid() -> None:
    assert _validate_ip_list("192.168.1.1, 10.0.0.1", None) is None


def test_validate_int_list_empty() -> None:
    assert _validate_int_list("", None) is None


def test_validate_int_list_all_valid() -> None:
    assert _validate_int_list("0, 1, 2", None) is None


def test_validate_markers_empty() -> None:
    assert _validate_markers("", None) is None


def test_validate_markers_all_valid_token_kinds() -> None:
    assert _validate_markers("1, c2, all", None) is None


def test_validate_markers_flags_offending_entry() -> None:
    err = _validate_markers("1, c0", None)
    assert err is not None and "c0" in err


def test_validate_model_empty() -> None:
    assert _validate_model("", None) is None


def test_validate_keybinding_empty() -> None:
    assert _validate_keybinding("", None) is None


# ---------------------------------------------------------------------------
# IP address fields – direct validators + endpoint-via-validate roundtrips.
# ---------------------------------------------------------------------------


def test_validate_multicast_ip_empty_allowed() -> None:
    assert _validate_multicast_ip("", None) is None


def test_validate_multicast_ip_valid() -> None:
    # 236.10.10.10 is the PSN default – known multicast.
    assert _validate_multicast_ip("236.10.10.10", None) is None
    # OTP default – also multicast.
    assert _validate_multicast_ip("239.159.1.0", None) is None


def test_validate_multicast_ip_unicast_rejected() -> None:
    err = _validate_multicast_ip("192.168.1.1", None)
    assert err is not None
    assert "multicast" in err.lower()


def test_validate_multicast_ip_garbage_rejected() -> None:
    err = _validate_multicast_ip("not-an-ip", None)
    assert err is not None


def test_validate_multicast_ip_ipv6_rejected() -> None:
    # PSN / OTP sockets are AF_INET; an IPv6 multicast address parses
    # via ``ipaddress`` and reports ``is_multicast=True`` but would
    # blow up at ``socket.bind``. Blur must reject it up front.
    err = _validate_multicast_ip("ff02::1", None)
    assert err is not None
    assert "ipv4" in err.lower()


def test_validate_host_empty_allowed() -> None:
    assert _validate_host("", None) is None


def test_validate_host_ipv4() -> None:
    assert _validate_host("127.0.0.1", None) is None


def test_validate_host_ipv6() -> None:
    assert _validate_host("::1", None) is None


def test_validate_host_simple_hostname() -> None:
    assert _validate_host("localhost", None) is None


def test_validate_host_dotted_hostname() -> None:
    assert _validate_host("my-server.local", None) is None


def test_validate_host_with_internal_whitespace_rejected() -> None:
    err = _validate_host("192.168 .1.1", None)
    assert err is not None


def test_validate_host_with_scheme_rejected() -> None:
    err = _validate_host("http://example.com", None)
    assert err is not None


def test_validate_host_too_long_rejected() -> None:
    err = _validate_host("a" * 254, None)
    assert err is not None


# Routing through the public validate() – exercises the full pipeline
# (strip / sanitise / parser / range / pattern / max_len / custom).


@pytest.mark.parametrize(
    "section,field",
    [
        ("psn", "psn_mcast_ip"),
        ("general", "psn_mcast_ip"),
    ],
)
def test_validate_multicast_field_rejects_unicast(section: str, field: str) -> None:
    err = validate(section, field, "192.168.1.1")
    assert err is not None
    assert "multicast" in err.lower()


@pytest.mark.parametrize(
    "section,field",
    [
        ("psn", "psn_mcast_ip"),
        ("general", "psn_mcast_ip"),
    ],
)
def test_validate_psn_mcast_accepts_default(section: str, field: str) -> None:
    assert validate(section, field, "236.10.10.10") is None


def test_validate_otp_mcast_field_does_not_exist() -> None:
    from openfollow.web.validation import FIELD_RULES

    assert "mcast_ip" not in FIELD_RULES["otp_output"]


def test_otp_source_iface_has_no_field_rule() -> None:
    """The OTP source interface is an interface picker (closed dropdown of
    valid NICs), like ``psn_source_iface`` – it carries no free-text
    FieldRule, and the old IP-typed ``source_ip`` rule is gone."""
    from openfollow.web.validation import FIELD_RULES

    assert "source_ip" not in FIELD_RULES["otp_output"]
    assert "source_iface" not in FIELD_RULES["otp_output"]


@pytest.mark.parametrize(
    "section,field",
    [
        ("rttrpm_output", "host"),
        ("osc_destination", "host"),
    ],
)
def test_validate_host_field_accepts_ip(section: str, field: str) -> None:
    assert validate(section, field, "127.0.0.1") is None


@pytest.mark.parametrize(
    "section,field",
    [
        ("rttrpm_output", "host"),
        ("osc_destination", "host"),
    ],
)
def test_validate_host_field_accepts_hostname(section: str, field: str) -> None:
    assert validate(section, field, "stage-controller.local") is None


@pytest.mark.parametrize(
    "section,field",
    [
        ("rttrpm_output", "host"),
        ("osc_destination", "host"),
    ],
)
def test_validate_host_field_rejects_internal_whitespace(section: str, field: str) -> None:
    # "192.168 .1.1" is neither a parseable IP nor a syntactically valid
    # hostname (whitespace breaks the label regex). All-digit DNS labels
    # like "192.168.1.999.500" are intentionally allowed – RFC 1123
    # hostnames may start with a digit, and the runtime resolver will
    # fail loudly on lookup if no host exists.
    err = validate(section, field, "192.168 .1.1")
    assert err is not None


# ---------------------------------------------------------------------------
# Module exports.
# ---------------------------------------------------------------------------


def test_module_exports_public_symbols() -> None:
    assert "validate" in validation.__all__
    assert "note" in validation.__all__
    assert "FIELD_RULES" in validation.__all__
    assert "FieldRule" in validation.__all__


# ---------------------------------------------------------------------------
# Combined osc_message field validation
# Address travels as the first whitespace-delimited token; the
# validator only checks the address-prefix rule, leaving args
# permissive (matches QLab's editor behaviour). The bare
# ``_validate_osc_address`` helper this replaced was removed because
# the combined validator is the single source of truth for the
# ``osc_binding`` form.


def test_validate_osc_message_empty_is_ok() -> None:
    from openfollow.web.validation import _validate_osc_message

    assert _validate_osc_message("", None) is None


def test_validate_osc_message_whitespace_only_is_ok() -> None:
    from openfollow.web.validation import _validate_osc_message

    assert _validate_osc_message("   ", None) is None


def test_validate_osc_message_with_leading_slash_is_ok() -> None:
    from openfollow.web.validation import _validate_osc_message

    assert _validate_osc_message("/foo/[markerId] [x] [y]", None) is None


def test_validate_osc_message_address_without_slash_rejected() -> None:
    from openfollow.web.validation import _validate_osc_message

    err = _validate_osc_message("foo [x] [y]", None)
    assert err is not None
    assert "must start with '/'" in err


def test_validate_osc_message_with_quoted_arg_is_ok() -> None:
    """Quoted strings with whitespace are valid in OSC message arguments."""
    from openfollow.web.validation import _validate_osc_message

    assert _validate_osc_message('/cmd "Go Cue 1 Fade 2"', None) is None
    assert _validate_osc_message("/cue/go 'My Cue' 1.5", None) is None


@pytest.mark.parametrize(
    "raw",
    [
        '/cue "unclosed',
        "/cue 'unclosed",
        '/say "she said \\"hi" "trailing',
    ],
)
def test_validate_osc_message_rejects_unclosed_quote(raw: str) -> None:
    """Unclosed quotes raise ValueError; validator translates to human-readable error."""
    from openfollow.web.validation import _validate_osc_message

    err = _validate_osc_message(raw, None)
    assert err is not None
    assert "Unclosed quote" in err


@pytest.mark.parametrize(
    "field,value,expect_error",
    [
        ("name", "Stage 1", False),
        ("name", "x" * 65, True),  # max_len 64
        # Connection is chosen via a destination; empty is allowed (no send).
        ("destination_id", "", False),
        ("destination_id", "dest-1", False),
        ("markers", "0", False),
        ("markers", "1, 3, c1", False),
        ("markers", "all", False),
        # A valid-but-non-controlled id is ignored at runtime, not flagged.
        ("markers", "5", False),
        # markers can be empty (no default marker).
        ("markers", "", False),
        ("markers", "   ", False),
        # Malformed tokens (negative, junk, bad alias) block.
        ("markers", "-1", True),
        ("markers", "bogus", True),
        ("markers", "1, c0", True),
        # Combined ``osc_message`` field – address as first token.
        ("osc_message", "/foo/[markerId] [x] [y]", False),
        ("osc_message", "", False),
        ("osc_message", "no-slash [x]", True),
        ("trigger.type", "stream", False),
        ("trigger.type", "hotkey", False),
        ("trigger.type", "controller_button", False),
        ("trigger.type", "midi_message", False),
        ("trigger.type", "fake-kind", True),
        ("trigger.rate_hz", "30", False),
        ("trigger.rate_hz", "999", True),
        ("trigger.edge", "press", False),
        ("trigger.edge", "release", False),
        ("trigger.edge", "tap", True),
        ("trigger.key", "r", False),
        ("trigger.key", "@@@", True),
        ("trigger.button", "A", False),
        ("trigger.button", "ZZZ", True),
    ],
)
def test_osc_binding_field_rules(field: str, value: str, expect_error: bool) -> None:
    err = validate("osc_binding", field, value)
    if expect_error:
        assert err is not None, f"osc_binding.{field}={value!r} should have failed validation"
    else:
        assert err is None, f"osc_binding.{field}={value!r} unexpectedly failed: {err}"


@pytest.mark.parametrize(
    "field,value,expect_error",
    [
        ("name", "Console", False),
        ("name", "x" * 65, True),  # max_len 64
        ("host", "10.0.0.1", False),
        ("host", "192.168 .1.1", True),
        ("port", "9000", False),
        ("port", "0", True),
        ("port", "65536", True),
        ("protocol", "udp", False),
        ("protocol", "tcp", False),
        ("protocol", "carrier-pigeon", True),
        ("framing", "slip", False),
        ("framing", "length_prefix", False),
        ("framing", "carrier-pigeon", True),
        ("framing", "SLIP", True),  # canonical lower-case only
    ],
)
def test_osc_destination_field_rules(field: str, value: str, expect_error: bool) -> None:
    err = validate("osc_destination", field, value)
    if expect_error:
        assert err is not None, f"osc_destination.{field}={value!r} should have failed validation"
    else:
        assert err is None, f"osc_destination.{field}={value!r} unexpectedly failed: {err}"


# ---------------------------------------------------------------------------
# Per-zone OSC address fields share the OSC Transmitters blur validator
# ---------------------------------------------------------------------------

# triggered_by blur validation: empty/valid lists pass, invalid entries reject.


class TestZoneTriggeredByRule:
    def test_empty_is_valid(self) -> None:
        """Empty input = no filter = legacy "any marker" behaviour."""
        assert validate("zone", "triggered_by", "") is None
        assert validate("zone", "triggered_by", "   ") is None

    def test_single_id_is_valid(self) -> None:
        assert validate("zone", "triggered_by", "0") is None

    def test_comma_separated_ids_are_valid(self) -> None:
        assert validate("zone", "triggered_by", "0, 1, 5") is None

    def test_non_numeric_entry_rejected(self) -> None:
        err = validate("zone", "triggered_by", "0, abc, 5")
        assert err is not None
        assert "Entry 2" in err
        assert "abc" in err

    def test_over_max_len_rejected(self) -> None:
        err = validate("zone", "triggered_by", "1," * 300)
        assert err is not None
        assert "512" in err


@pytest.mark.parametrize(
    "field",
    [
        "osc_address_first_entry",
        "osc_address_additional_entry",
        "osc_address_partial_exit",
        "osc_address_final_exit",
    ],
)
class TestZoneOscAddressRules:
    def test_empty_field_is_valid(self, field: str) -> None:
        """An unset zone OSC field is a valid "skip this transition"
        – same lenient contract as OSC Transmitters's empty osc_message."""
        assert validate("zone", field, "") is None
        assert validate("zone", field, "   ") is None

    def test_address_only_is_valid(self, field: str) -> None:
        """The legacy shape (just an OSC address, no args) keeps
        passing – every existing config tokenises identically."""
        assert validate("zone", field, "/zone/enter") is None

    def test_address_plus_quoted_arg_is_valid(self, field: str) -> None:
        """Quoted args alongside address allowed in zone fields like OSC Transmitters."""
        assert validate("zone", field, '/cmd "Go Cue 1" 1.5') is None

    def test_address_without_leading_slash_rejected(self, field: str) -> None:
        err = validate("zone", field, "foo bar")
        assert err is not None
        assert "must start with '/'" in err

    def test_unclosed_quote_rejected(self, field: str) -> None:
        err = validate("zone", field, '/cue "unclosed')
        assert err is not None
        assert "Unclosed quote" in err

    def test_over_max_len_rejected(self, field: str) -> None:
        """``max_len=2048`` mirrors ``osc_binding.osc_message`` so
        operators can't smuggle multi-kilobyte strings through the
        per-zone JSON ``/api/zones`` endpoint."""
        err = validate("zone", field, "/" + "a" * 2048)
        assert err is not None
        assert "2048" in err


@pytest.mark.parametrize(
    "raw,expect_error",
    [("", False), ("0", False), ("3", False), ("abc", True)],
)
def test_mouse3d_button_validation(raw: str, expect_error: bool) -> None:
    # Blank = unbound (no error); a 0+ index is valid; junk is a type error.
    err = validate("mouse3d", "btn_reset", raw)
    assert (err is not None) is expect_error


def test_needs_cfg_false_for_every_rule() -> None:
    """No registered ``FieldRule`` currently reads ``AppConfig``, so
    ``needs_cfg`` returns ``False`` for all of them – the ``/api/validate``
    fast-path skips the per-keystroke TOML parse for every field."""
    from openfollow.web.validation import needs_cfg

    for section, rules in FIELD_RULES.items():
        for field_name, rule in rules.items():
            assert needs_cfg(rule) is False, f"{section}.{field_name}"
