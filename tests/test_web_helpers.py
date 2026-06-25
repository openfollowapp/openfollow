# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for web route helpers: config section apply/serialise, web-PIN
and update-URL validators, peer SSRF / port guards, zone + wizard parsers, and
the detection-extras probes."""

from __future__ import annotations

import os
from dataclasses import asdict

import pytest

import openfollow.video.inputs as inputs_module
from openfollow.configuration import AppConfig, OscDestinationConfig, OscDestinationsConfig
from openfollow.web.routes import (
    _as_int_list,
    _as_ip_list,
    _config_dict_redacted,
    _is_private_peer_ip,
    _is_valid_service_name,
    _is_valid_web_pin,
    apply_section_data,
    get_section_data,
    osc_destinations_client_list,
    osc_destinations_script_json,
    strip_device_local_fields,
)

pytestmark = pytest.mark.unit


def test_is_valid_web_pin_accepts_empty_and_ascii_digits() -> None:
    assert _is_valid_web_pin("") is True  # empty disables auth
    assert _is_valid_web_pin("1234") is True
    assert _is_valid_web_pin("9" * 32) is True


def test_is_valid_web_pin_rejects_nondigit_overlong_nonascii() -> None:
    assert _is_valid_web_pin("abcd") is False
    assert _is_valid_web_pin("12 34") is False
    assert _is_valid_web_pin("9" * 33) is False
    assert _is_valid_web_pin("١٢٣٤") is False  # Arabic-Indic digits (not ASCII)


def test_config_dict_redacted_drops_device_local_fields() -> None:
    cfg = AppConfig(web_pin="1234", web_port=8080)
    cfg.detection.storage_path = "/mnt/nvme/openfollow/yolo"
    d = _config_dict_redacted(cfg)
    assert "web_pin" not in d  # login credential never exported
    # storage_path is an absolute path on the exporting host – stripped so it
    # can't land on (and break) another machine.
    assert "storage_path" not in d["detection"]
    assert d["web_port"] == 8080  # non-secret fields preserved


def test_strip_device_local_fields_drops_detection_storage_path() -> None:
    scrubbed = strip_device_local_fields(
        "detection",
        {"storage_path": "/Users/dev/checkout/.openfollow-storage", "model": "yolov8n.onnx"},
    )
    assert "storage_path" not in scrubbed
    assert scrubbed["model"] == "yolov8n.onnx"  # non-local fields kept


def test_general_section_rejects_invalid_web_pin_and_port() -> None:
    cfg = AppConfig(web_pin="1234", web_port=80)
    apply_section_data(cfg, "general", {"web_pin": "abcd", "web_port": 99999})
    assert cfg.web_pin == "1234"  # invalid PIN rejected, current kept
    assert cfg.web_port == 80  # out-of-range port rejected


def test_general_section_accepts_valid_web_pin_and_port() -> None:
    cfg = AppConfig(web_pin="1234", web_port=80)
    apply_section_data(cfg, "general", {"web_pin": "5678", "web_port": 8080})
    assert cfg.web_pin == "5678"
    assert cfg.web_port == 8080


def test_general_strip_covers_pin_and_port() -> None:
    scrubbed = strip_device_local_fields("general", {"web_pin": "1", "web_port": 80, "psn_system_name": "x"})
    assert "web_pin" not in scrubbed
    assert "web_port" not in scrubbed
    assert scrubbed["psn_system_name"] == "x"


def test_as_int_list_parses_csv_and_falls_back() -> None:
    assert _as_int_list("1, 2,3", [9]) == [1, 2, 3]
    assert _as_int_list("x, y", [9]) == [9]
    assert _as_int_list(None, [9]) == [9]


# --- JSON-config API path tolerates non-numeric / out-of-range input -----
# JSON decodes ``1e400`` to ``float('inf')`` and arbitrary-precision integers
# to Python ``int``. ``float(10**5000)`` and ``int(float('inf'))`` both raise
# ``OverflowError``; ``float('inf')`` succeeds but would crash ``draw_grid``
# downstream. The ``/api/config/<section>`` endpoint must fall back to the
# GridConfig defaults rather than 500 or persist a non-finite value. These
# exercise the public boundary (``apply_section_data``) rather than the
# private ``_as_float`` / ``_as_int`` helpers, so a refactor of the
# helpers shouldn't break these.


def test_apply_section_data_camera_lens_distortion_roundtrips() -> None:
    config = AppConfig()
    ok = apply_section_data(config, "camera", {"lens_k1": "-0.15", "lens_k2": "0.03"})
    assert ok is True
    assert config.camera.lens_k1 == pytest.approx(-0.15)
    assert config.camera.lens_k2 == pytest.approx(0.03)


def test_apply_section_data_camera_lens_distortion_clamps_out_of_range() -> None:
    config = AppConfig()
    ok = apply_section_data(config, "camera", {"lens_k1": "5", "lens_k2": "-5"})
    assert ok is True
    # __post_init__ re-runs after the web save, clamping to the configured band.
    assert config.camera.lens_k1 == 0.4
    assert config.camera.lens_k2 == -0.2


def test_apply_section_data_grid_tolerates_huge_int_width() -> None:
    config = AppConfig()
    ok = apply_section_data(config, "grid", {"width": 10**5000})
    assert ok is True
    # ``float(10**5000)`` raises OverflowError → parser defaults → post_init
    # keeps the declared default.
    assert config.grid.width == 10.0


def test_apply_section_data_grid_tolerates_inf_thickness() -> None:
    config = AppConfig()
    ok = apply_section_data(config, "grid", {"thickness": float("inf")})
    assert ok is True
    # ``int(float('inf'))`` raises OverflowError.
    assert config.grid.thickness == 1


def test_apply_section_data_grid_rejects_inf_width_to_default() -> None:
    config = AppConfig()
    ok = apply_section_data(config, "grid", {"width": float("inf")})
    assert ok is True
    # ``float('inf')`` would slip through the parser's OverflowError catch,
    # but ``__post_init__``'s non-finite guard rejects it at the dataclass
    # boundary.
    assert config.grid.width == 10.0


def test_apply_section_data_camera_rejects_nan_fov_to_default() -> None:
    config = AppConfig()
    ok = apply_section_data(config, "camera", {"fov": float("nan")})
    assert ok is True
    # ``nan`` propagates through clamping unchanged; the non-finite guard
    # in ``_coerce_float`` drops it to the declared default.
    assert config.camera.fov == 60.0


def test_apply_section_data_grid_rejects_non_bool_origin_visible_to_default() -> None:
    """Non-bool values for origin_visible are rejected and revert to default."""
    config = AppConfig()
    ok = apply_section_data(config, "grid", {"origin_visible": 42})
    assert ok is True
    assert config.grid.origin_visible is False

    # Symmetry: a list / dict / None payload also falls back, regardless
    # of Python truthiness.
    for bad in ([1, 2, 3], {"k": 1}, None):
        config = AppConfig()
        ok = apply_section_data(config, "grid", {"origin_visible": bad})
        assert ok is True
        assert config.grid.origin_visible is False, f"non-bool origin_visible={bad!r} must fall back to default False"


def test_apply_section_data_validates_detection_fields() -> None:
    config = AppConfig()

    ok = apply_section_data(
        config,
        "detection",
        {
            "enabled": "true",
            "pin_point": "left",
            "inference_size": "100",
        },
    )

    assert ok is True
    assert config.detection.enabled is True
    assert config.detection.pin_point == "top"
    assert config.detection.inference_size == 160


def test_apply_section_data_general_updates_known_values(monkeypatch) -> None:
    monkeypatch.setattr(inputs_module, "get_available_input_ids", lambda: ["rtsp", "srt"])
    monkeypatch.setattr(inputs_module, "get_input_class", lambda _input_id: None)

    config = AppConfig(video_source_type="rtsp")
    ok = apply_section_data(
        config,
        "general",
        {
            "psn_system_name": "Updated Name",
        },
    )

    assert ok is True
    assert config.psn_system_name == "Updated Name"


def test_apply_section_data_general_ignores_marker_ids(monkeypatch) -> None:
    monkeypatch.setattr(inputs_module, "get_available_input_ids", lambda: ["rtsp"])
    monkeypatch.setattr(inputs_module, "get_input_class", lambda _input_id: None)

    config = AppConfig(video_source_type="rtsp")
    original_controlled = list(config.controlled_marker_ids)
    original_viewer = list(config.viewer_marker_ids)
    ok = apply_section_data(
        config,
        "general",
        {
            "controlled_marker_ids": "99",
            "viewer_marker_ids": "98",
        },
    )
    assert ok is True
    assert config.controlled_marker_ids == original_controlled
    assert config.viewer_marker_ids == original_viewer


def test_apply_section_data_marker_ignores_marker_id_fields() -> None:
    config = AppConfig(
        controlled_marker_ids=[7],
        viewer_marker_ids=[7],
    )
    ok = apply_section_data(
        config,
        "marker",
        {
            "controlled_marker_ids": "1,2,3",
            "viewer_marker_ids": [4, 5],
        },
    )
    assert ok is True
    assert config.controlled_marker_ids == [7]
    assert config.viewer_marker_ids == [7]


def test_apply_section_data_video_source(monkeypatch) -> None:
    monkeypatch.setattr(inputs_module, "get_available_input_ids", lambda: ["rtsp", "srt"])
    monkeypatch.setattr(inputs_module, "get_input_class", lambda _input_id: None)

    config = AppConfig(video_source_type="rtsp")
    ok = apply_section_data(
        config,
        "video_source",
        {"video_source_type": "srt"},
    )

    assert ok is True
    assert config.video_source_type == "srt"


def test_get_section_data_returns_none_for_unknown_section() -> None:
    assert get_section_data(AppConfig(), "unknown-section") is None


def test_get_section_data_serializes_simple_sections() -> None:
    config = AppConfig()

    assert get_section_data(config, "camera") == asdict(config.camera)
    assert get_section_data(config, "detection") == asdict(config.detection)


def test_get_section_data_marker_excludes_selection_fields() -> None:
    """Per-marker control/view selection is owned by the inline
    catalog UI in the Markers & Zones tab and is persisted via
    ``/api/markers/selection`` – the marker section payload now
    carries visuals only."""
    config = AppConfig()
    payload = get_section_data(config, "marker")
    assert payload is not None
    # All of MarkerConfig's fields are still present.
    for key, value in asdict(config.marker).items():
        assert payload[key] == value
    # Selection fields are NOT in the marker payload any more.
    assert "controlled_marker_ids" not in payload
    assert "viewer_marker_ids" not in payload


def test_apply_section_data_marker_parses_boolean_and_float() -> None:
    config = AppConfig()

    ok = apply_section_data(
        config,
        "marker",
        {
            "ball_size": "0.25",
            "ball_visible": "false",
        },
    )

    assert ok is True
    assert config.marker.ball_size == 0.25
    assert config.marker.ball_visible is False


def test_apply_section_data_movement_parses_speed_and_position() -> None:
    config = AppConfig()

    ok = apply_section_data(
        config,
        "movement",
        {
            "min_speed": "0.5",
            "move_speed": "3.5",
            "max_speed": "10.0",
            "default_pos_x": "1.0",
        },
    )

    assert ok is True
    assert config.marker.min_speed == 0.5
    assert config.marker.move_speed == 3.5
    assert config.marker.max_speed == 10.0
    assert config.marker.default_pos_x == 1.0


def test_apply_section_data_controller_revalidates_deadzone() -> None:
    config = AppConfig()

    ok = apply_section_data(config, "controller", {"deadzone": "5.0"})

    assert ok is True
    assert config.controller.deadzone == 1.0


# --- __post_init__ re-run after web-form saves ---------------------------
# Every dataclass with validation in ``__post_init__`` must also have it
# re-run after a web-form save, otherwise a crafted POST that slips past
# the field parser (or mutates a field the parser leaves alone) persists
# an invalid value to config.toml. See "Validation contract" in
# CLAUDE.md.


def test_apply_section_data_grid_clamps_appearance_via_post_init() -> None:
    config = AppConfig()

    ok = apply_section_data(
        config,
        "grid",
        {"color": "not-a-color", "thickness": "0", "transparency": "9.0"},
    )

    assert ok is True
    # color falls back to default, thickness floor-clamped to 1,
    # transparency top-clamped to 1.0 – all via GridConfig.__post_init__.
    assert config.grid.color == "#545454"
    assert config.grid.thickness == 1
    assert config.grid.transparency == 1.0


def test_apply_section_data_camera_clamps_fov_via_post_init() -> None:
    config = AppConfig()

    ok = apply_section_data(config, "camera", {"fov": "500"})

    assert ok is True
    assert config.camera.fov == 179.0


def test_apply_section_data_movement_enforces_max_above_min_via_post_init() -> None:
    # The "movement" section carries speed limits; "marker" holds appearance.
    # Both share the same ``cfg.marker`` dataclass, so both re-run its post_init.
    config = AppConfig()

    ok = apply_section_data(
        config,
        "movement",
        {"min_speed": "5.0", "max_speed": "1.0"},
    )

    assert ok is True
    assert config.marker.min_speed == 5.0
    assert config.marker.max_speed == 5.0


def test_apply_section_data_otp_output_clamps_port_via_post_init() -> None:
    config = AppConfig()

    ok = apply_section_data(config, "otp_output", {"port": "70000"})

    assert ok is True
    assert config.otp_output.port == 65535


def test_apply_section_data_rttrpm_output_clamps_fps_via_post_init() -> None:
    config = AppConfig()

    ok = apply_section_data(config, "rttrpm_output", {"fps": "0"})

    assert ok is True
    assert config.rttrpm_output.fps == 1


def test_apply_section_data_detection_snaps_inference_size_via_post_init() -> None:
    config = AppConfig()

    ok = apply_section_data(
        config,
        "detection",
        {"inference_size": "2000"},
    )

    assert ok is True
    # post_init clamps to <=1280 and snaps to a multiple of 32.
    assert config.detection.inference_size == 1280


@pytest.mark.parametrize(
    "value",
    ["openfollow", "openfollow.service", "multi-user.target", "my_app@2", "a.b-c_d@e"],
)
def test_is_valid_service_name_accepts_systemd_unit_names(value: str) -> None:
    assert _is_valid_service_name(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "",
        "  ",
        "-openfollow",  # leading dash: reads as systemctl option
        "--version",  # classic argument injection
        "bad service",
        "bad;rm",
    ],
)
def test_is_valid_service_name_rejects_unsafe_inputs(value: str) -> None:
    assert _is_valid_service_name(value) is False


def test_as_ip_list_accepts_comma_separated_string() -> None:
    assert _as_ip_list("127.0.0.1, 10.0.0.5 ,", []) == ["127.0.0.1", "10.0.0.5"]


def test_as_ip_list_accepts_json_list() -> None:
    assert _as_ip_list(["192.168.1.1", "fe80::1"], []) == ["192.168.1.1", "fe80::1"]


def test_as_ip_list_drops_invalid_entries_silently() -> None:
    # Mix of valid and invalid – only valid ones survive.
    assert _as_ip_list(
        "127.0.0.1, not-an-ip, 10.0.0.5",
        [],
    ) == ["127.0.0.1", "10.0.0.5"]


def test_as_ip_list_all_invalid_preserves_default() -> None:
    """Fail closed on typos.

    Empty allowlist means "allow all" in OscInputHandler, so a
    submission of ``"192.168.1.999"`` (all invalid) must NOT silently
    become ``[]`` and disable the filter. Keep the existing default.
    """
    assert _as_ip_list("garbage", ["1.2.3.4"]) == ["1.2.3.4"]
    assert _as_ip_list("192.168.1.999", ["10.0.0.1"]) == ["10.0.0.1"]


def test_as_ip_list_empty_string_returns_empty_list() -> None:
    # Meaningful semantically: "clear the allowlist" is distinct from
    # "leave unchanged". The caller distinguishes via key presence.
    assert _as_ip_list("", ["keep me"]) == []


def test_as_ip_list_whitespace_only_csv_treated_as_explicit_empty() -> None:
    # Operator tried to clear the list, result has no characters after
    # strip – treat as explicit empty, not as a typo.
    assert _as_ip_list("   ,  ,", ["keep me"]) == []


def test_as_ip_list_empty_json_list_returns_empty() -> None:
    assert _as_ip_list([], ["keep me"]) == []


def test_as_ip_list_non_string_non_list_returns_default() -> None:
    assert _as_ip_list(None, ["keep me"]) == ["keep me"]
    assert _as_ip_list(42, ["keep me"]) == ["keep me"]


def test_apply_section_data_osc_parses_allowed_sender_ips() -> None:
    config = AppConfig()

    ok = apply_section_data(
        config,
        "osc",
        {"enabled": "true", "port": "9001", "allowed_sender_ips": "127.0.0.1, 192.168.1.10"},
    )

    assert ok is True
    assert config.osc.enabled is True
    assert config.osc.port == 9001
    assert config.osc.allowed_sender_ips == ["127.0.0.1", "192.168.1.10"]


def test_apply_section_data_rttrpm_output_parses_fields() -> None:
    config = AppConfig()

    ok = apply_section_data(
        config,
        "rttrpm_output",
        {"enabled": "true", "host": "192.168.1.50", "port": "36700", "fps": "30", "context": "42"},
    )

    assert ok is True
    assert config.rttrpm_output.enabled is True
    assert config.rttrpm_output.host == "192.168.1.50"
    assert config.rttrpm_output.port == 36700
    assert config.rttrpm_output.fps == 30
    assert config.rttrpm_output.context == 42


def test_apply_section_data_rttrpm_output_disabled_when_checkbox_absent() -> None:
    config = AppConfig()
    config.rttrpm_output.enabled = True

    ok = apply_section_data(config, "rttrpm_output", {"host": "10.0.0.1"})

    assert ok is True
    # enabled key absent → not updated by apply_section_data (checkbox handled by _save_section_from_form)
    assert config.rttrpm_output.host == "10.0.0.1"


def test_get_section_data_rttrpm_output() -> None:
    config = AppConfig()
    assert get_section_data(config, "rttrpm_output") == asdict(config.rttrpm_output)


# EosOutputConfig save-time tests removed – eos_output was deleted along with legacy OSC surfaces.
# The replacement osc_transmitters section has its own coverage.

# ---------------------------------------------------------------------------
# service name validation helper (update service restart target)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("svc", ["openfollow", "my-service", "systemd.slice", "a_b"])
def test_is_valid_service_name_accepts_normal_names(svc: str) -> None:
    assert _is_valid_service_name(svc) is True


@pytest.mark.parametrize(
    "svc",
    [
        "",
        "   ",
        "-payload",  # option-masquerade
        "--no-block",  # option-masquerade
        "service name",  # space
        "service;rm",  # shell metachar
        "service\x00stop",  # null byte
    ],
)
def test_is_valid_service_name_rejects_dangerous_inputs(svc: str) -> None:
    assert _is_valid_service_name(svc) is False


# ---------------------------------------------------------------------------
# Zone parsing helpers
# ---------------------------------------------------------------------------


def test_parse_vertices_accepts_list_of_pairs() -> None:
    from openfollow.web.routes import _parse_vertices

    result = _parse_vertices([[1.0, 2.0], (3, 4), [5.5, 6.5, 99]], current=[])
    # Each pair coerced to [float, float]; extra coords in third tuple ignored.
    assert result == [[1.0, 2.0], [3.0, 4.0], [5.5, 6.5]]


def test_parse_vertices_non_list_preserves_current() -> None:
    from openfollow.web.routes import _parse_vertices

    current = [[1.0, 2.0]]
    # null, str, dict – all non-list: must return current, not blank the polygon.
    assert _parse_vertices(None, current) == current
    assert _parse_vertices("garbage", current) == current
    assert _parse_vertices({"x": 1}, current) == current
    assert current == [[1.0, 2.0]]


def test_parse_vertices_drops_malformed_entries() -> None:
    from openfollow.web.routes import _parse_vertices

    result = _parse_vertices(
        [
            [1.0, 2.0],  # valid
            "not a pair",  # scalar string -> skipped
            [3.0],  # too short -> skipped
            ["x", "y"],  # non-numeric -> skipped via ValueError
            [None, 4.0],  # non-numeric -> skipped via TypeError
            [9.0, 10.0],  # valid
        ],
        current=[[99.0, 99.0]],
    )
    assert result == [[1.0, 2.0], [9.0, 10.0]]


def test_apply_zone_fields_parses_types_and_defaults_vertices() -> None:
    from openfollow.configuration import TriggerZoneConfig
    from openfollow.web.routes import _apply_zone_fields

    zone = TriggerZoneConfig()
    _apply_zone_fields(
        zone,
        {
            "name": "Stage Left",
            "color": "#FF0000",
            "enabled": "true",
            "destination_id": "d1",
            "vertices": [[0, 0], [10, 0], [10, 10]],
            "ignored_unknown_field": "dropped-silently",
        },
    )

    assert zone.name == "Stage Left"
    assert zone.color == "#FF0000"
    assert zone.enabled is True
    assert zone.destination_id == "d1"
    assert zone.vertices == [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0]]
    # Unknown keys must not create attributes on the dataclass.
    assert not hasattr(zone, "ignored_unknown_field")


def test_apply_zone_fields_preserves_vertices_when_key_absent() -> None:
    from openfollow.configuration import TriggerZoneConfig
    from openfollow.web.routes import _apply_zone_fields

    zone = TriggerZoneConfig()
    zone.vertices = [[0.0, 0.0], [1.0, 1.0]]
    _apply_zone_fields(zone, {"name": "New"})  # no vertices key at all
    assert zone.vertices == [[0.0, 0.0], [1.0, 1.0]]


# ---------------------------------------------------------------------------
# _wizard_camera_params
# ---------------------------------------------------------------------------


def test_wizard_camera_params_builds_vector_in_field_order() -> None:
    from openfollow.web.routes import _WIZARD_CAMERA_FIELDS, _wizard_camera_params

    cam = {f: i + 1 for i, f in enumerate(_WIZARD_CAMERA_FIELDS)}
    params = _wizard_camera_params(cam)
    assert params.shape == (len(_WIZARD_CAMERA_FIELDS),)
    # Order must match _WIZARD_CAMERA_FIELDS verbatim – downstream
    # project_points/solver reads positional slots, not a dict.
    for i, _ in enumerate(_WIZARD_CAMERA_FIELDS):
        assert params[i] == float(i + 1)


def test_wizard_camera_params_rejects_non_dict() -> None:
    from openfollow.web.routes import _wizard_camera_params

    with pytest.raises(TypeError):
        _wizard_camera_params([1, 2, 3])


def test_wizard_camera_params_raises_keyerror_for_missing_field() -> None:
    from openfollow.web.routes import _WIZARD_CAMERA_FIELDS, _wizard_camera_params

    cam = dict.fromkeys(_WIZARD_CAMERA_FIELDS, 0.0)
    cam.pop("fov")  # omit one required field
    with pytest.raises(KeyError):
        _wizard_camera_params(cam)


def test_wizard_camera_params_raises_valueerror_on_non_numeric() -> None:
    from openfollow.web.routes import _WIZARD_CAMERA_FIELDS, _wizard_camera_params

    cam = dict.fromkeys(_WIZARD_CAMERA_FIELDS, 0.0)
    cam["yaw"] = "not a number"
    with pytest.raises(ValueError):
        _wizard_camera_params(cam)


# ---------------------------------------------------------------------------
# _apply_import_data / _import_needs_restart
# ---------------------------------------------------------------------------


def test_apply_import_data_preserves_psn_source_iface() -> None:
    from openfollow.web.routes import _apply_import_data

    current = AppConfig()
    current.psn_source_iface = "eth0"  # device-specific

    imported = {
        "psn_source_iface": "wlan0",  # attacker/other-device value
        "psn_system_name": "Imported",
    }
    new = _apply_import_data(current, imported)

    assert new.psn_source_iface == "eth0"  # device iface preserved
    assert new.psn_system_name == "Imported"


def test_apply_import_data_preserves_detection_storage_path() -> None:
    from openfollow.web.routes import _apply_import_data

    current = AppConfig()
    current.detection.storage_path = "/mnt/nvme/openfollow/yolo"  # this device's path

    imported = {
        # A path from the exporting machine that would be unwritable here.
        "detection": {"storage_path": "/Users/dev/checkout/.openfollow-storage", "confidence": 0.5},
    }
    new = _apply_import_data(current, imported)

    assert new.detection.storage_path == "/mnt/nvme/openfollow/yolo"  # device path kept
    assert new.detection.confidence == 0.5  # other detection fields still import


def test_apply_import_data_skip_restart_sections_applies_every_section(
    monkeypatch,
) -> None:
    """All sections apply live; skip_restart flag is preserved for backwards compatibility."""
    from openfollow.web.routes import _apply_import_data

    monkeypatch.setattr(inputs_module, "get_available_input_ids", lambda: ["rtsp", "srt"])
    monkeypatch.setattr(inputs_module, "get_input_class", lambda _id: None)

    current = AppConfig(video_source_type="rtsp")
    current.otp_output.enabled = False
    current.detection.enabled = False

    imported = {
        "video_source_type": "srt",
        "otp_output": {"enabled": True},
        "detection": {"enabled": True},  # – off→on now live
        "camera": {"pos_x": 42.0},
    }
    new = _apply_import_data(current, imported, skip_restart_sections=True)

    assert new.video_source_type == "srt"  # applied
    assert new.detection.enabled is True  # applied
    assert new.otp_output.enabled is True  # applied
    assert new.camera.pos_x == pytest.approx(42.0)  # applied


def test_apply_import_data_replaces_trigger_zones_list() -> None:
    from openfollow.configuration import TriggerZoneConfig
    from openfollow.web.routes import _apply_import_data

    current = AppConfig()
    current.trigger_zones.zones = [
        TriggerZoneConfig(name="Old A"),
        TriggerZoneConfig(name="Old B"),
    ]
    imported = {
        "trigger_zones": {
            "enabled": True,
            "zones": [
                {"name": "New 1", "vertices": [[0, 0], [1, 1]]},
                {"name": "New 2"},
                "not-a-dict",  # must be skipped silently
            ],
        },
    }
    new = _apply_import_data(current, imported)

    assert new.trigger_zones.enabled is True
    assert [z.name for z in new.trigger_zones.zones] == ["New 1", "New 2"]
    assert new.trigger_zones.zones[0].vertices == [[0.0, 0.0], [1.0, 1.0]]


def test_apply_import_data_round_trips_osc_transmitters_and_destinations() -> None:
    """Destinations + transmitters + zones travel through the config file so a
    ``destination_id`` and its target arrive together with references intact."""
    from openfollow.web.routes import _apply_import_data

    current = AppConfig()
    imported = {
        "osc_destinations": {
            "destinations": [
                {"id": "console", "name": "Console", "host": "10.0.0.9", "port": 8000},
            ],
        },
        "osc_transmitters": {
            "transmitters": [
                {"id": "row1", "name": "Cue", "destination_id": "console"},
            ],
        },
        "trigger_zones": {
            "zones": [{"name": "Z", "destination_id": "console"}],
        },
    }
    new = _apply_import_data(current, imported)

    dests = new.osc_destinations.destinations
    assert any(d.id == "console" and d.host == "10.0.0.9" for d in dests)
    rows = new.osc_transmitters.transmitters
    assert rows[0].destination_id == "console"
    assert new.trigger_zones.zones[0].destination_id == "console"
    # The reference resolves on the importing device.
    assert new.osc_destinations.get(rows[0].destination_id) is not None


def test_strip_broadcast_excluded_drops_routing_sections() -> None:
    """OSC destinations / transmitters / zones export via file but are never
    real-time shared – the broadcast body omits them."""
    from openfollow.web.routes import _config_dict_redacted, _strip_broadcast_excluded

    full = _config_dict_redacted(AppConfig())
    # Sanity: the full export dict carries them.
    assert "osc_destinations" in full
    assert "osc_transmitters" in full
    assert "trigger_zones" in full

    broadcast = _strip_broadcast_excluded(full)
    assert "osc_destinations" not in broadcast
    assert "osc_transmitters" not in broadcast
    assert "trigger_zones" not in broadcast
    # A non-excluded section still travels.
    assert "camera" in broadcast


def test_apply_osc_destination_fields_coerces_and_normalises() -> None:
    from openfollow.configuration import OscDestinationConfig
    from openfollow.web.routes import _apply_osc_destination_fields

    dest = OscDestinationConfig(id="d1")
    _apply_osc_destination_fields(
        dest,
        {
            "name": "  Console  ",
            "host": "  10.0.0.5  ",
            "port": "70000",  # clamps to 65535
            "protocol": "tcp",
            "framing": "carrier-pigeon",  # snaps back to slip
        },
    )
    assert dest.name == "Console"
    assert dest.host == "10.0.0.5"
    assert dest.port == 65535
    assert dest.protocol == "tcp"
    assert dest.framing == "slip"
    # id is preserved (not minted).
    assert dest.id == "d1"


def test_apply_import_data_window_dims_and_pin_pass_through() -> None:
    """window_width/window_height are top-level fields not covered by any
    section parser – the importer has its own branch for them. web_pin is
    device-local and preserved across import (not overwritten)."""
    from openfollow.web.routes import _apply_import_data

    current = AppConfig(web_pin="4242")
    imported = {
        "window_width": "1920",
        "window_height": "1080",
        "web_pin": "9999",
    }
    new = _apply_import_data(current, imported)

    assert new.window_width == 1920
    assert new.window_height == 1080
    # Imported web_pin is ignored; this station keeps its own PIN.
    assert new.web_pin == "4242"


def test_apply_import_data_applies_ui_section() -> None:
    """The exported ``[ui]`` block (unit_system + show_experimental_features)
    must round-trip on import. Regression: the importer skipped the ``ui``
    section, so an exported "experimental on" / imperial config silently
    reverted to the importing device's values."""
    from openfollow.web.routes import _apply_import_data

    current = AppConfig()
    assert current.ui.show_experimental_features is False
    assert current.ui.unit_system == "metric"

    imported = {
        "ui": {
            "show_experimental_features": True,
            "unit_system": "imperial",
        },
    }
    new = _apply_import_data(current, imported)

    assert new.ui.show_experimental_features is True
    assert new.ui.unit_system == "imperial"


def test_apply_import_data_ui_round_trips_through_export_dict() -> None:
    """End-to-end: the export payload of a configured device imports cleanly
    on a default device, carrying the ``ui`` preferences across."""
    from openfollow.web.routes import _apply_import_data

    source = AppConfig()
    source.ui.show_experimental_features = True
    source.ui.unit_system = "imperial"

    new = _apply_import_data(AppConfig(), _config_dict_redacted(source))

    assert new.ui.show_experimental_features is True
    assert new.ui.unit_system == "imperial"


@pytest.mark.parametrize(
    "ui_payload",
    [
        {"show_experimental_features": "garbage", "unit_system": "furlongs"},
        {"show_experimental_features": None, "unit_system": None},
        {"show_experimental_features": 1234, "unit_system": 5678},
    ],
)
def test_apply_import_data_ui_section_bad_values_reset_to_defaults(
    ui_payload,  # noqa: ANN001
) -> None:
    """A present-but-invalid ``ui`` field coerces to the *declared* defaults
    (metric / False) via __post_init__ – exactly like a TOML load. Starting
    from non-default ui values proves the importer does NOT silently preserve
    the current setting when the imported value is bad."""
    from openfollow.web.routes import _apply_import_data

    current = AppConfig()
    current.ui.unit_system = "imperial"
    current.ui.show_experimental_features = True

    new = _apply_import_data(current, {"ui": ui_payload})

    assert new.ui.show_experimental_features is False
    assert new.ui.unit_system == "metric"


def test_apply_import_data_ui_section_absent_field_preserves_current() -> None:
    """A field omitted from the ``ui`` payload preserves the device's current
    value; only fields actually present are applied. Covers both single-field
    payloads (unit_system-only and experimental-only)."""
    from openfollow.web.routes import _apply_import_data

    def _current() -> AppConfig:
        cfg = AppConfig()
        cfg.ui.unit_system = "imperial"
        cfg.ui.show_experimental_features = True
        return cfg

    # Only unit_system present (and valid); the experimental flag is kept.
    new = _apply_import_data(_current(), {"ui": {"unit_system": "metric"}})
    assert new.ui.unit_system == "metric"
    assert new.ui.show_experimental_features is True

    # Inverse: only the experimental flag present; unit_system is kept.
    new = _apply_import_data(_current(), {"ui": {"show_experimental_features": False}})
    assert new.ui.show_experimental_features is False
    assert new.ui.unit_system == "imperial"


def test_apply_import_data_non_dict_ui_section_preserves_current() -> None:
    """A malformed non-dict ``ui`` block is skipped entirely, leaving the
    device's current ui preferences untouched (not reset to defaults)."""
    from openfollow.web.routes import _apply_import_data

    current = AppConfig()
    current.ui.unit_system = "imperial"
    current.ui.show_experimental_features = True

    new = _apply_import_data(current, {"ui": "not-a-dict"})

    assert new.ui.unit_system == "imperial"
    assert new.ui.show_experimental_features is True


def test_import_needs_restart_detects_each_restart_reason() -> None:
    """All config changes apply live; nothing is flagged as requiring restart."""
    from openfollow.web.routes import _import_needs_restart

    base = AppConfig()

    video_changed = AppConfig()
    video_changed.video_source_type = "different-source"
    assert _import_needs_restart(base, video_changed) == []

    otp_changed = AppConfig()
    otp_changed.otp_output.enabled = not base.otp_output.enabled
    assert _import_needs_restart(base, otp_changed) == []

    rttrpm_changed = AppConfig()
    rttrpm_changed.rttrpm_output.enabled = not base.rttrpm_output.enabled
    assert _import_needs_restart(base, rttrpm_changed) == []

    detection_off_to_on = AppConfig()
    detection_off_to_on.detection.enabled = True
    assert _import_needs_restart(base, detection_off_to_on) == []


def test_import_needs_restart_returns_empty_when_identical() -> None:
    from openfollow.web.routes import _import_needs_restart

    base = AppConfig()
    twin = AppConfig()
    assert _import_needs_restart(base, twin) == []


# ---------------------------------------------------------------------------
# _as_bool / _as_positive_int / _as_optional_float helpers – full branch coverage
# ---------------------------------------------------------------------------


def test_as_bool_accepts_literal_bool() -> None:
    from openfollow.web.routes import _as_bool

    assert _as_bool(True, False) is True
    assert _as_bool(False, True) is False


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("Yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("NO", False),
        ("off", False),
    ],
)
def test_as_bool_parses_truthy_and_falsy_strings(text: str, expected: bool) -> None:
    from openfollow.web.routes import _as_bool

    assert _as_bool(text, not expected) is expected


def test_as_bool_unknown_string_returns_default() -> None:
    from openfollow.web.routes import _as_bool

    assert _as_bool("maybe", True) is True
    assert _as_bool("maybe", False) is False


def test_as_bool_falls_back_to_default_for_other_types() -> None:
    """_as_bool accepts only real bool and recognized string forms; other types fall back to default."""
    from openfollow.web.routes import _as_bool

    # Ints, lists, dicts, None – none of these are accepted; the field's
    # declared default wins regardless of the value's "truthiness".
    assert _as_bool(0, True) is True
    assert _as_bool(0, False) is False
    assert _as_bool(2, True) is True
    assert _as_bool(2, False) is False
    assert _as_bool([], True) is True
    assert _as_bool([0], False) is False
    assert _as_bool({}, True) is True
    assert _as_bool({"k": 1}, False) is False
    assert _as_bool(None, True) is True
    assert _as_bool(None, False) is False


def test_as_positive_int_clamps_zero_and_negative_to_one() -> None:
    from openfollow.web.routes import _as_positive_int

    assert _as_positive_int(0, 5) == 1
    assert _as_positive_int(-3, 5) == 1
    assert _as_positive_int("bogus", 5) == 5  # falls back to default first
    # 0 as default is also clamped up – a divide-by-zero could happen otherwise.
    assert _as_positive_int("bogus", 0) == 1


def test_as_optional_float_returns_none_for_none_and_blank() -> None:
    from openfollow.web.routes import _as_optional_float

    assert _as_optional_float(None, 1.0) is None
    assert _as_optional_float("", 1.0) is None
    assert _as_optional_float("   ", 1.0) is None


def test_as_optional_float_parses_numeric_strings() -> None:
    from openfollow.web.routes import _as_optional_float

    assert _as_optional_float("3.14", 0.0) == pytest.approx(3.14)
    assert _as_optional_float("1e3", 0.0) == pytest.approx(1000.0)


def test_as_optional_float_falls_back_on_non_numeric() -> None:
    from openfollow.web.routes import _as_optional_float

    assert _as_optional_float("not a number", 1.5) == pytest.approx(1.5)
    # OverflowError path: int(10**5000) coerced to float.
    assert _as_optional_float(10**5000, 1.5) == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# _peer_auth_headers
# ---------------------------------------------------------------------------


def test_peer_auth_headers_returns_empty_when_pin_empty() -> None:
    from openfollow.web.routes import _peer_auth_headers

    # No PIN configured -> unsigned requests; don't add spurious headers.
    assert _peer_auth_headers("", "POST", "/api/x", b"{}") == {}


def test_peer_auth_headers_includes_signature_and_timestamp_when_pin_set() -> None:
    from openfollow.web import peer_auth
    from openfollow.web.routes import _peer_auth_headers

    headers = _peer_auth_headers("sekret", "POST", "/api/config/camera", b"{}")
    assert peer_auth.TIMESTAMP_HEADER in headers
    assert peer_auth.SIGNATURE_HEADER in headers
    # Signature must be deterministic given same pin+body+path+method+timestamp,
    # so verify matches.
    ts = int(headers[peer_auth.TIMESTAMP_HEADER])
    sig = headers[peer_auth.SIGNATURE_HEADER]
    assert (
        peer_auth.verify(
            "sekret",
            "POST",
            "/api/config/camera",
            b"{}",
            str(ts),
            sig,
        )
        is True
    )


# ---------------------------------------------------------------------------
# _send_config_to_peer / _send_config_import_to_peer SSRF guards
# ---------------------------------------------------------------------------


def test_send_config_to_peer_refuses_public_ip(caplog) -> None:
    from openfollow.web.routes import _send_config_to_peer

    with caplog.at_level("WARNING", logger="openfollow.web.routes"):
        assert _send_config_to_peer("8.8.8.8", 80, "camera", {}, pin="", expected_port=80) is False
    assert any("non-private" in r.message for r in caplog.records)


def test_send_config_import_to_peer_refuses_public_ip(caplog) -> None:
    from openfollow.web.routes import _send_config_import_to_peer

    with caplog.at_level("WARNING", logger="openfollow.web.routes"):
        assert _send_config_import_to_peer("1.1.1.1", 80, {}, pin="", expected_port=80) is False
    assert any("non-private" in r.message for r in caplog.records)


def test_send_config_to_peer_posts_on_private_ip(monkeypatch) -> None:
    import openfollow.web.routes as routes_mod
    from openfollow.web.routes import _send_config_to_peer

    captured: dict[str, object] = {}

    class _FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = req.data
        return _FakeResp()

    monkeypatch.setattr(routes_mod.urllib.request, "urlopen", _fake_urlopen)

    ok = _send_config_to_peer("10.0.0.5", 8080, "camera", {"pos_x": 1.5}, pin="", expected_port=8080)
    assert ok is True
    assert captured["url"] == "http://10.0.0.5:8080/api/config/camera"
    assert captured["method"] == "POST"
    assert b'"pos_x": 1.5' in captured["body"]  # type: ignore[operator]


def test_send_config_to_peer_returns_false_on_urlerror(monkeypatch) -> None:
    import urllib.error

    import openfollow.web.routes as routes_mod
    from openfollow.web.routes import _send_config_to_peer

    def _fail(req, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(routes_mod.urllib.request, "urlopen", _fail)

    assert _send_config_to_peer("10.0.0.5", 80, "camera", {}, pin="", expected_port=80) is False


def test_send_config_import_to_peer_returns_false_on_timeout(monkeypatch) -> None:
    import openfollow.web.routes as routes_mod
    from openfollow.web.routes import _send_config_import_to_peer

    def _timeout(req, timeout):
        raise TimeoutError("socket timed out")

    monkeypatch.setattr(routes_mod.urllib.request, "urlopen", _timeout)

    assert _send_config_import_to_peer("10.0.0.5", 80, {}, pin="", expected_port=80) is False


# ---------------------------------------------------------------------------
# _detection_export_script – source-checkout / packaged probe
# ---------------------------------------------------------------------------


def test_detection_export_script_found_in_source_checkout() -> None:
    from openfollow.web.routes import _detection_export_script

    # The dev loop runs from a source checkout, so the script resolves to the
    # repo's scripts/export_onnx.py.
    script = _detection_export_script()
    assert script is not None
    assert script.name == "export_onnx.py"
    assert script.is_file()


def test_detection_export_script_falls_back_to_packaged_path(monkeypatch, tmp_path) -> None:
    """When no pyproject is reachable (a packaged install), the helper finds the
    .deb's /usr/share/openfollow/scripts/export_onnx.py."""
    from openfollow.web import routes as routes_mod

    packaged = tmp_path / "export_onnx.py"
    packaged.write_text("# stub")

    # No pyproject ancestor: point __file__ resolution at a parentless fake.
    class _NoPyproject:
        def resolve(self):
            return self

        @property
        def parents(self):
            return []

    monkeypatch.setattr(routes_mod, "Path", lambda *_a, **_kw: _NoPyproject())
    monkeypatch.setattr(routes_mod, "_PACKAGED_EXPORT_SCRIPT", packaged)
    assert routes_mod._detection_export_script() == packaged


def test_detection_export_script_none_when_unreachable(monkeypatch, tmp_path) -> None:
    from openfollow.web import routes as routes_mod

    class _NoPyproject:
        def resolve(self):
            return self

        @property
        def parents(self):
            return []

    monkeypatch.setattr(routes_mod, "Path", lambda *_a, **_kw: _NoPyproject())
    monkeypatch.setattr(routes_mod, "_PACKAGED_EXPORT_SCRIPT", tmp_path / "missing.py")
    assert routes_mod._detection_export_script() is None


# ---------------------------------------------------------------------------
# _get_detection_missing_deps – graceful fallbacks
# ---------------------------------------------------------------------------


def test_get_detection_missing_deps_maps_cv2_to_opencv_python(monkeypatch) -> None:
    from openfollow.web import routes as routes_mod

    def _raise_cv2(_cfg):
        raise ModuleNotFoundError("No module named 'cv2'", name="cv2")

    monkeypatch.setattr(
        "openfollow.video.detection.check_detection_dependencies",
        _raise_cv2,
    )
    missing = routes_mod._get_detection_missing_deps(config=None)
    assert missing == ["opencv-python"]


def test_get_detection_missing_deps_passes_through_other_module_names(
    monkeypatch,
) -> None:
    from openfollow.web import routes as routes_mod

    def _raise_onnx(_cfg):
        raise ModuleNotFoundError(
            "No module named 'onnxruntime'",
            name="onnxruntime",
        )

    monkeypatch.setattr(
        "openfollow.video.detection.check_detection_dependencies",
        _raise_onnx,
    )
    missing = routes_mod._get_detection_missing_deps(config=None)
    assert missing == ["onnxruntime"]


def test_get_detection_missing_deps_falls_back_on_unexpected_failure(
    monkeypatch,
) -> None:
    from openfollow.web import routes as routes_mod

    def _explode(_cfg):
        raise RuntimeError("probe crashed")

    monkeypatch.setattr(
        "openfollow.video.detection.check_detection_dependencies",
        _explode,
    )
    missing = routes_mod._get_detection_missing_deps(config=None)
    assert missing == ["detection dependencies unavailable"]


# ---------------------------------------------------------------------------
# _get_detection_extras_status – conditional install/uninstall buttons
# ---------------------------------------------------------------------------


def _stub_find_spec(monkeypatch, *, present: set[str]) -> None:
    """Replace ``importlib.util.find_spec`` so the helper only sees
    the modules in ``present`` as installed. Tests inject this so
    they don't depend on whichever extras happen to be on the host."""
    from openfollow.web import routes as routes_mod

    def _fake(name: str):
        return object() if name in present else None

    monkeypatch.setattr(routes_mod.importlib.util, "find_spec", _fake)


def test_get_detection_extras_status_installed(monkeypatch) -> None:
    """Happy path: the backend installed → the extra reports True so the
    template renders an Uninstall button."""
    from openfollow.web import routes as routes_mod

    _stub_find_spec(monkeypatch, present={"onnxruntime"})
    assert routes_mod._get_detection_extras_status() == {"detection": True, "export": False}


def test_get_detection_extras_status_not_installed(monkeypatch) -> None:
    from openfollow.web import routes as routes_mod

    _stub_find_spec(monkeypatch, present=set())
    assert routes_mod._get_detection_extras_status() == {"detection": False, "export": False}


def test_get_detection_extras_status_export_installed(monkeypatch) -> None:
    """The optional export toolchain reports independently of the backend."""
    from openfollow.web import routes as routes_mod

    _stub_find_spec(monkeypatch, present={"ultralytics"})
    assert routes_mod._get_detection_extras_status() == {"detection": False, "export": True}


# ---------------------------------------------------------------------------
# _available_models – Model dropdown filter
# ---------------------------------------------------------------------------


def test_available_models_marks_entries_available_when_installed(monkeypatch, tmp_path) -> None:
    """With a blank storage path, availability tracks <cwd>/yolo/models."""
    from openfollow.web import routes as routes_mod

    monkeypatch.setattr(os.path, "ismount", lambda _p: False)  # no NVMe
    monkeypatch.chdir(tmp_path)
    models = tmp_path / "yolo" / "models"
    models.mkdir(parents=True)
    (models / "yolov8n.onnx").write_bytes(b"stub")
    (models / "yolo11s.onnx").write_bytes(b"stub")

    rows = routes_mod._available_models(
        {"detection": True},
        saved_value="yolov8n.onnx",
    )
    by_value = {value: avail for value, _label, avail in rows}
    assert by_value["yolov8n.onnx"] is True
    assert by_value["yolo11s.onnx"] is True
    # A catalogue model not on disk stays unavailable.
    assert by_value["yolo11x.onnx"] is False


def test_available_models_empty_storage_requires_file_on_disk(monkeypatch, tmp_path) -> None:
    """Blank storage + installed extra must NOT report a missing model available.

    A blank ``storage_path`` resolves to ``<cwd>/yolo`` (no NVMe); a model that
    exists nowhere on disk must render unavailable instead of trusting the
    installed extra alone.
    """
    from openfollow.web import routes as routes_mod

    monkeypatch.setattr(os.path, "ismount", lambda _p: False)  # no NVMe
    monkeypatch.chdir(tmp_path)  # no models dir present
    rows = routes_mod._available_models(
        {"detection": True},
        saved_value="yolo11m.onnx",
        storage_path="",
    )
    by_value = {value: avail for value, _label, avail in rows}
    assert by_value["yolo11m.onnx"] is False
    assert all(avail is False for avail in by_value.values())


def test_available_models_marks_entries_unavailable_when_not_installed() -> None:
    from openfollow.web import routes as routes_mod

    rows = routes_mod._available_models(
        {"detection": False},
        saved_value="yolov8n.onnx",
    )
    by_value = {value: avail for value, _label, avail in rows}
    assert all(avail is False for avail in by_value.values())


def test_available_models_surfaces_saved_value_outside_catalogue(monkeypatch, tmp_path) -> None:
    """A saved uncatalogued model that ISN'T on disk is preserved as a leading
    entry so saving the form doesn't silently drop the operator's value."""
    from openfollow.web import routes as routes_mod

    monkeypatch.setattr(os.path, "ismount", lambda _p: False)  # no NVMe
    monkeypatch.chdir(tmp_path)  # <cwd>/yolo/models absent -> nothing on disk

    rows = routes_mod._available_models(
        {"detection": True},
        saved_value="my-custom.onnx",
    )
    # Custom entry comes first so it's the natural ``selected`` candidate.
    assert rows[0][0] == "my-custom.onnx"
    assert rows[0][2] is False  # not on disk -> not selectable, but preserved


def test_available_models_does_not_duplicate_catalogue_entry() -> None:
    from openfollow.web import routes as routes_mod

    rows = routes_mod._available_models(
        {"detection": True},
        saved_value="yolov8n.onnx",
    )
    values = [value for value, _label, _avail in rows]
    assert values.count("yolov8n.onnx") == 1


def test_available_models_gates_catalogue_on_disk_presence(tmp_path) -> None:
    """With a storage path set, only models present on disk are selectable."""
    from openfollow.web import routes as routes_mod

    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "yolo11m.onnx").write_bytes(b"stub")

    rows = routes_mod._available_models(
        {"detection": True},
        saved_value="yolo11m.onnx",
        storage_path=str(tmp_path),
    )
    by_value = {value: avail for value, _label, avail in rows}
    # On disk -> selectable.
    assert by_value["yolo11m.onnx"] is True
    # Catalogued but absent -> rendered disabled.
    assert by_value["yolov8n.onnx"] is False
    assert by_value["yolo11x.onnx"] is False


def test_available_models_surfaces_uncatalogued_disk_model(tmp_path) -> None:
    """A custom ``.onnx`` on disk shows up by filename and is selectable."""
    from openfollow.web import routes as routes_mod

    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "my-custom.onnx").write_bytes(b"stub")

    rows = routes_mod._available_models(
        {"detection": True},
        saved_value="yolo11s.onnx",
        storage_path=str(tmp_path),
    )
    by_value = {value: (label, avail) for value, label, avail in rows}
    assert "my-custom.onnx" in by_value
    label, avail = by_value["my-custom.onnx"]
    assert label == "my-custom.onnx"
    assert avail is True


def _redirect_nvme_storage(monkeypatch, tmp_path):
    """Point the detection NVMe constants at ``tmp_path`` and pretend it's
    mounted, so blank-storage_path auto-detect is hermetic. Returns the
    detection-storage root the resolver will hand back."""
    from openfollow.video import detection as detection_mod

    nvme_root = tmp_path / "nvme"
    storage = nvme_root / "openfollow" / "yolo"
    monkeypatch.setattr(detection_mod, "_NVME_MOUNTPOINT", str(nvme_root))
    monkeypatch.setattr(detection_mod, "_NVME_DETECTION_STORAGE", str(storage))
    monkeypatch.setattr(os.path, "ismount", lambda p: p == str(nvme_root))
    return storage


def test_discover_storage_models_blank_uses_mounted_nvme(tmp_path, monkeypatch) -> None:
    """A blank storage_path scans the NVMe models dir when the drive is mounted."""
    from openfollow.web import routes as routes_mod

    storage = _redirect_nvme_storage(monkeypatch, tmp_path)
    (storage / "models").mkdir(parents=True)
    (storage / "models" / "yolo11s.onnx").write_bytes(b"stub")

    assert routes_mod._discover_storage_models("") == {"yolo11s.onnx"}


def test_discover_storage_models_blank_empty_without_nvme(monkeypatch, tmp_path) -> None:
    """A blank storage_path with no NVMe resolves to <cwd>/yolo/models; a missing
    dir yields an empty set (nothing on disk), never None."""
    from openfollow.web import routes as routes_mod

    monkeypatch.setattr(os.path, "ismount", lambda _p: False)
    monkeypatch.chdir(tmp_path)
    assert routes_mod._discover_storage_models("") == set()


def test_detection_storage_info_reports_free_space(tmp_path) -> None:
    from openfollow.web import routes as routes_mod

    info = routes_mod._detection_storage_info(str(tmp_path))
    assert info["path"] == str(tmp_path / "models")
    assert isinstance(info["free_bytes"], int) and info["free_bytes"] > 0
    assert isinstance(info["total_bytes"], int) and info["total_bytes"] > 0
    assert info["free_h"].endswith("GiB")
    assert info["total_h"].endswith("GiB")


def test_detection_storage_info_walks_to_existing_ancestor(tmp_path) -> None:
    """A not-yet-created storage dir reports its filesystem's space (the disk
    stat walks up to the nearest existing ancestor), and creates nothing."""
    from openfollow.web import routes as routes_mod

    missing = tmp_path / "deep" / "nested" / "yolo"
    info = routes_mod._detection_storage_info(str(missing))
    assert info["free_bytes"] is not None and info["free_bytes"] > 0
    assert not (tmp_path / "deep").exists()  # render created nothing


def test_detection_installed_models_lists_onnx_with_sizes(tmp_path) -> None:
    from openfollow.web import routes as routes_mod

    models = tmp_path / "models"
    models.mkdir()
    (models / "b.onnx").write_bytes(b"x" * 2048)
    (models / "a.onnx").write_bytes(b"y" * 1024)
    (models / "notes.txt").write_text("ignore me")

    out = routes_mod._detection_installed_models(str(tmp_path))
    names = [m["name"] for m in out]
    assert names == ["a.onnx", "b.onnx"]  # sorted, .txt skipped
    assert all(m["size_h"].endswith("MiB") for m in out)


def test_detection_installed_models_empty_when_dir_missing(tmp_path) -> None:
    from openfollow.web import routes as routes_mod

    assert routes_mod._detection_installed_models(str(tmp_path / "nope")) == []


def test_detection_installed_models_zero_size_on_stat_error(monkeypatch, tmp_path) -> None:
    """A file that disappears between ``iterdir`` and ``stat`` (or is otherwise
    unstattable) is listed with size 0 rather than crashing the render."""
    from pathlib import Path as RealPath

    from openfollow.web import routes as routes_mod

    models = tmp_path / "models"
    models.mkdir()
    (models / "a.onnx").write_bytes(b"x" * 10)

    monkeypatch.setattr(RealPath, "is_file", lambda self: self.suffix.lower() == ".onnx")

    def boom(self, *_a, **_kw):
        raise OSError("vanished")

    monkeypatch.setattr(RealPath, "stat", boom)

    out = routes_mod._detection_installed_models(str(tmp_path))
    assert out == [{"name": "a.onnx", "size_bytes": 0, "size_h": "0.0 MiB"}]


def test_format_gib_unknown_when_none() -> None:
    from openfollow.web import routes as routes_mod

    assert routes_mod._format_gib(None) == "?"


def test_detection_storage_info_unknown_when_disk_usage_fails(monkeypatch, tmp_path) -> None:
    """When the filesystem can't be stat'd, free/total are None and render as
    ``"?"`` instead of raising out of the section render."""
    import shutil

    from openfollow.web import routes as routes_mod

    def boom(_path):
        raise OSError("no such filesystem")

    monkeypatch.setattr(shutil, "disk_usage", boom)

    info = routes_mod._detection_storage_info(str(tmp_path))
    assert info["free_bytes"] is None
    assert info["total_bytes"] is None
    assert info["free_h"] == "?"
    assert info["total_h"] == "?"


def test_detection_export_script_break_when_checkout_lacks_script(monkeypatch, tmp_path) -> None:
    """A pyproject ancestor with no scripts/export_onnx.py stops the ancestor
    walk; with no packaged script either, the helper returns None."""
    import pathlib

    from openfollow.web import routes as routes_mod

    repo = tmp_path / "repo"
    pkg = repo / "openfollow" / "web"
    pkg.mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[tool]\n")
    fake_file = pkg / "routes.py"
    fake_file.write_text("# stub")

    monkeypatch.setattr(routes_mod, "Path", lambda *_a, **_kw: pathlib.Path(str(fake_file)))
    monkeypatch.setattr(routes_mod, "_PACKAGED_EXPORT_SCRIPT", tmp_path / "missing.py")
    assert routes_mod._detection_export_script() is None


def test_available_models_disk_model_unavailable_without_extra(tmp_path) -> None:
    """A model on disk is still not selectable when onnxruntime is missing."""
    from openfollow.web import routes as routes_mod

    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "yolo11m.onnx").write_bytes(b"stub")

    rows = routes_mod._available_models(
        {"detection": False},
        saved_value="yolo11m.onnx",
        storage_path=str(tmp_path),
    )
    by_value = {value: avail for value, _label, avail in rows}
    assert all(avail is False for avail in by_value.values())


def test_available_models_empty_models_dir_marks_all_unavailable(tmp_path) -> None:
    """A storage path whose models dir is empty disables every catalogue entry."""
    from openfollow.web import routes as routes_mod

    (tmp_path / "models").mkdir()

    rows = routes_mod._available_models(
        {"detection": True},
        saved_value="yolov8n.onnx",
        storage_path=str(tmp_path),
    )
    assert all(avail is False for _value, _label, avail in rows)


def test_available_models_nonexistent_models_dir_marks_unavailable(monkeypatch, tmp_path) -> None:
    """A storage path whose models dir doesn't exist disables every entry."""
    from openfollow.web import routes as routes_mod

    monkeypatch.chdir(tmp_path)  # no model files present
    rows = routes_mod._available_models(
        {"detection": True},
        saved_value="yolov8n.onnx",
        storage_path=str(tmp_path / "nope"),
    )
    by_value = {value: avail for value, _label, avail in rows}
    assert by_value["yolov8n.onnx"] is False


def test_available_models_saved_value_absent_on_disk_is_disabled(tmp_path) -> None:
    """A saved model that isn't on disk renders as an unavailable leading entry."""
    from openfollow.web import routes as routes_mod

    (tmp_path / "models").mkdir()

    rows = routes_mod._available_models(
        {"detection": True},
        saved_value="ghost.onnx",
        storage_path=str(tmp_path),
    )
    assert rows[0][0] == "ghost.onnx"
    assert rows[0][2] is False


# ---------------------------------------------------------------------------
# Model export helpers (TODO: ultralytics export extra)
# ---------------------------------------------------------------------------


def test_export_indicator_maps_to_ultralytics() -> None:
    from openfollow.web import routes as routes_mod

    assert routes_mod._DETECTION_EXTRA_INDICATOR["export"] == "ultralytics"
    assert routes_mod._DETECTION_EXTRA_INDICATOR["detection"] == "onnxruntime"


def test_catalogue_model_values_are_onnx_filenames() -> None:
    from openfollow.web import routes as routes_mod

    values = routes_mod._catalogue_model_values()
    assert "yolov8n.onnx" in values
    assert all(v.endswith(".onnx") for v in values)


@pytest.mark.parametrize("family", ["yolo12", "yolo26"])
def test_catalogue_offers_full_yolo12_and_yolo26_families(family: str) -> None:
    """The YOLO12 (GPU tier) and YOLO26 (edge tier) families are downloadable.

    Both are catalogued for every size and accepted by the export allowlist, so
    the export action can fetch + convert them to ONNX. The filenames use the
    Ultralytics ``yoloNN<size>`` ids (no "v"), which the export ``.onnx``->``.pt``
    strip turns into the right weight name.
    """
    from openfollow.web import routes as routes_mod

    values = routes_mod._catalogue_model_values()
    for size in ("n", "s", "m", "l", "x"):
        assert f"{family}{size}.onnx" in values


@pytest.mark.parametrize("present,expected", [(object(), True), (None, False)])
def test_export_tools_available_reflects_find_spec(monkeypatch, present, expected) -> None:
    import importlib.util

    from openfollow.web import routes as routes_mod

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: present if name == "ultralytics" else None)
    assert routes_mod._export_tools_available() is expected


@pytest.mark.parametrize(
    "raw,default,expected",
    [
        ("640", 320, 640),  # already a multiple of 32
        ("321", 320, 320),  # snapped down to nearest 32
        ("100", 320, 160),  # clamped up to floor
        ("5000", 320, 1280),  # clamped to ceiling
        ("", 416, 416),  # empty -> default
        ("abc", 512, 512),  # non-numeric -> default
    ],
)
def test_coerce_export_imgsz(raw: str, default: int, expected: int) -> None:
    from openfollow.web import routes as routes_mod

    assert routes_mod._coerce_export_imgsz(raw, default) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [("17", 17), ("3", 7), ("99", 22), ("", 17), ("x", 17)],
)
def test_coerce_export_opset(raw: str, expected: int) -> None:
    from openfollow.web import routes as routes_mod

    assert routes_mod._coerce_export_opset(raw) == expected


def test_resolved_models_dir_is_storage_models(tmp_path) -> None:
    from openfollow.web import routes as routes_mod

    assert routes_mod._resolved_models_dir(str(tmp_path)) == tmp_path / "models"


# ---------------------------------------------------------------------------
# _load_json_body
# ---------------------------------------------------------------------------


def test_load_json_body_returns_none_for_malformed_json(monkeypatch) -> None:
    from openfollow.web import routes as routes_mod

    class _FakeReq:
        class body:
            @staticmethod
            def read() -> bytes:
                return b"{not valid json"

    class _FakeResp:
        status = 200

    monkeypatch.setattr(routes_mod, "request", _FakeReq())
    monkeypatch.setattr(routes_mod, "response", _FakeResp())

    assert routes_mod._load_json_body() is None
    assert routes_mod.response.status == 400


def test_load_json_body_rejects_non_utf8_body(monkeypatch) -> None:
    from openfollow.web import routes as routes_mod

    class _FakeReq:
        class body:
            @staticmethod
            def read() -> bytes:
                return b"\xff\xfe"  # invalid UTF-8 → UnicodeDecodeError

    class _FakeResp:
        status = 200

    monkeypatch.setattr(routes_mod, "request", _FakeReq())
    monkeypatch.setattr(routes_mod, "response", _FakeResp())

    assert routes_mod._load_json_body() is None  # 400, not an uncaught 500
    assert routes_mod.response.status == 400


def test_is_safe_template_filename_rejects_nul_and_control_chars() -> None:
    from openfollow.web.routes import TEMPLATE_FILE_SUFFIX, _is_safe_template_filename

    good = f"osc_output.my-cue{TEMPLATE_FILE_SUFFIX}"
    assert _is_safe_template_filename(good) is True
    assert _is_safe_template_filename(f"osc_output.my\x00cue{TEMPLATE_FILE_SUFFIX}") is False
    assert _is_safe_template_filename(f"osc_output.my\ncue{TEMPLATE_FILE_SUFFIX}") is False


def test_load_json_body_rejects_null_as_invalid(monkeypatch) -> None:
    from openfollow.web import routes as routes_mod

    class _FakeReq:
        class body:
            @staticmethod
            def read() -> bytes:
                return b"null"

    class _FakeResp:
        status = 200

    monkeypatch.setattr(routes_mod, "request", _FakeReq())
    monkeypatch.setattr(routes_mod, "response", _FakeResp())

    assert routes_mod._load_json_body() is None
    assert routes_mod.response.status == 400


def test_load_json_body_returns_parsed_dict(monkeypatch) -> None:
    from openfollow.web import routes as routes_mod

    class _FakeReq:
        class body:
            @staticmethod
            def read() -> bytes:
                return b'{"a": 1, "b": [2, 3]}'

    class _FakeResp:
        status = 200

    monkeypatch.setattr(routes_mod, "request", _FakeReq())
    monkeypatch.setattr(routes_mod, "response", _FakeResp())

    assert routes_mod._load_json_body() == {"a": 1, "b": [2, 3]}
    assert routes_mod.response.status == 200


@pytest.mark.parametrize(
    "unknown_section",
    [
        "rttrpm_does_not_exist",  # plausible-looking typo near a real section
        "no-such-section",  # arbitrary garbage
        "",  # empty string
    ],
)
def test_apply_section_data_unknown_section_returns_false(unknown_section: str) -> None:
    cfg = AppConfig()
    assert apply_section_data(cfg, unknown_section, {"x": 1}) is False


@pytest.mark.parametrize(
    "ip",
    [
        "10.0.0.1",  # RFC 1918 class A
        "172.16.0.1",  # RFC 1918 class B
        "192.168.1.50",  # RFC 1918 class C
        "169.254.1.2",  # link-local
        "127.0.0.1",  # loopback (handy for test/loopback peers)
        "::1",  # IPv6 loopback – ipaddress.ip_address handles both families
        "::ffff:10.0.0.1",  # IPv4-mapped private → evaluated on the IPv4
    ],
)
def test_is_private_peer_ip_accepts_lan_addresses(ip: str) -> None:
    assert _is_private_peer_ip(ip) is True


@pytest.mark.parametrize(
    "ip",
    [
        "8.8.8.8",  # public DNS
        "1.1.1.1",  # public
        "93.184.216.34",  # example.com (public)
        "not-an-ip",  # malformed
        "",  # empty
        "10.0.0.1:80",  # host:port in one string
        "0.0.0.0",  # unspecified – dials localhost on many stacks
        "::",  # IPv6 unspecified
        "::ffff:8.8.8.8",  # IPv4-mapped public must not slip past
    ],
)
def test_is_private_peer_ip_rejects_non_lan_addresses(ip: str) -> None:
    assert _is_private_peer_ip(ip) is False


def test_apply_section_data_general_rejects_invalid_service_name(monkeypatch) -> None:
    monkeypatch.setattr(inputs_module, "get_available_input_ids", lambda: ["rtsp", "srt"])
    monkeypatch.setattr(inputs_module, "get_input_class", lambda _input_id: None)

    config = AppConfig(video_source_type="rtsp", update_service_name="openfollow")

    ok = apply_section_data(config, "general", {"update_service_name": "openfollow service"})

    assert ok is True
    # Invalid (contains a space) → original value preserved.
    assert config.update_service_name == "openfollow"


def test_apply_section_data_general_ignores_removed_git_pull_keys(monkeypatch) -> None:
    """A crafted POST carrying the removed git-pull fields must be ignored –
    no crash, nothing persisted – while the surviving .deb fields still save."""
    monkeypatch.setattr(inputs_module, "get_available_input_ids", lambda: ["rtsp", "srt"])
    monkeypatch.setattr(inputs_module, "get_input_class", lambda _input_id: None)

    config = AppConfig(video_source_type="rtsp")

    ok = apply_section_data(
        config,
        "general",
        {
            "update_source_url": "git@evil.example:bad.git",
            "update_repo_branch": "attacker",
            "update_allowed_hosts": "evil.example",
            "update_github_repo": "someone/fork",
            "update_service_name": "my-tracker",
        },
    )

    assert ok is True
    # Removed git-pull fields never materialise on the config.
    assert not hasattr(config, "update_source_url")
    assert not hasattr(config, "update_repo_branch")
    assert not hasattr(config, "update_allowed_hosts")
    # The .deb-release fields still save.
    assert config.update_github_repo == "someone/fork"
    assert config.update_service_name == "my-tracker"


# ---------------------------------------------------------------------------
# Section data shape validation across apply_section_data / get_section_data
# ---------------------------------------------------------------------------


def test_get_section_data_psn_returns_dedicated_psn_dict() -> None:
    """PSN section view is distinct from general with dedicated transport fields; psn_source_iface is the pin."""
    cfg = AppConfig()
    cfg.psn_system_name = "Stage A"
    cfg.psn_mcast_ip = "236.10.10.10"
    cfg.psn_source_iface = "eth0"

    data = get_section_data(cfg, "psn")

    assert data == {
        "psn_system_name": "Stage A",
        "psn_mcast_ip": "236.10.10.10",
        "psn_source_iface": "eth0",
    }


def test_apply_section_data_psn_updates_fields_and_strips_iface() -> None:
    """The "psn" branch only touches the PSN transport fields.
    ``psn_source_iface`` is stripped (mirrors AppConfig.__post_init__)
    so a value like ``" eth0 "`` doesn't desync the stored config
    from the runtime. Other fields are left alone."""
    cfg = AppConfig()
    cfg.web_pin = "untouched-pin"
    cfg.controlled_marker_ids = [9]

    ok = apply_section_data(
        cfg,
        "psn",
        {
            "psn_system_name": "PSN-Two",
            "psn_mcast_ip": "236.0.0.42",
            "psn_source_iface": "  eth0  ",
        },
    )

    assert ok is True
    assert cfg.psn_system_name == "PSN-Two"
    assert cfg.psn_mcast_ip == "236.0.0.42"
    assert cfg.psn_source_iface == "eth0"
    # PSN section must not bleed into general/web/marker fields.
    assert cfg.web_pin == "untouched-pin"
    assert cfg.controlled_marker_ids == [9]


def test_apply_section_data_video_source_unknown_id_keeps_current(monkeypatch) -> None:
    monkeypatch.setattr(inputs_module, "get_available_input_ids", lambda: ["rtsp", "srt"])
    monkeypatch.setattr(inputs_module, "get_input_class", lambda _input_id: None)

    cfg = AppConfig(video_source_type="rtsp")
    ok = apply_section_data(cfg, "video_source", {"video_source_type": "totally-not-a-plugin"})
    assert ok is True
    assert cfg.video_source_type == "rtsp"


def test_apply_section_data_detection_accepts_valid_pin_point() -> None:
    """The valid-value arm of ``pin_point`` only assigns when the value matches
    the allowlist. Unlike the rejection arm (covered elsewhere), this exercises
    the *assignment* path."""
    cfg = AppConfig()
    cfg.detection.pin_point = "top"

    ok = apply_section_data(
        cfg,
        "detection",
        {"pin_point": "bottom"},
    )

    assert ok is True
    assert cfg.detection.pin_point == "bottom"


def test_apply_section_data_otp_output_runs_post_init_clamping() -> None:
    """The post-save ``__post_init__`` re-runs the same coercion that protects
    hand-edited config.toml – without it a crafted POST could persist e.g.
    a port outside [1, 65535]. Asserting on a clamped field proves the
    elif arm fired."""
    cfg = AppConfig()
    ok = apply_section_data(
        cfg,
        "otp_output",
        {"port": 999_999, "fps": 1000},
    )
    assert ok is True
    # OtpOutputConfig.__post_init__ clamps the port back into range.
    assert 1 <= cfg.otp_output.port <= 65535


def test_apply_section_data_rttrpm_output_runs_post_init_clamping() -> None:
    cfg = AppConfig()
    ok = apply_section_data(
        cfg,
        "rttrpm_output",
        {"fps": 0},
    )
    assert ok is True
    assert cfg.rttrpm_output.fps == 1


def test_apply_section_data_general_psn_source_iface_strips_whitespace(monkeypatch) -> None:
    """``general`` section – the same iface-name strip as the PSN
    section, but exercised via the general route's broader parser.
    Closes a missed branch where the field is present but
    whitespace-padded."""
    monkeypatch.setattr(inputs_module, "get_available_input_ids", lambda: ["rtsp"])
    monkeypatch.setattr(inputs_module, "get_input_class", lambda _input_id: None)

    cfg = AppConfig()
    ok = apply_section_data(cfg, "general", {"psn_source_iface": "  eth0\t"})
    assert ok is True
    assert cfg.psn_source_iface == "eth0"


# ---------------------------------------------------------------------------
# _apply_import_data – full-config import from a peer/backup
# ---------------------------------------------------------------------------


def test_apply_import_data_skips_non_dict_zone_entries() -> None:
    from openfollow.web.routes import _apply_import_data

    base = AppConfig()
    new = _apply_import_data(
        base,
        {
            "trigger_zones": {
                "zones": [
                    "not a dict",  # skipped
                    42,  # skipped
                    None,  # skipped
                    {"name": "ValidZone"},  # kept
                ],
            },
        },
    )
    assert [z.name for z in new.trigger_zones.zones] == ["ValidZone"]


def test_apply_import_data_imports_marker_section_via_both_parsers() -> None:
    """The ``marker`` import branch dispatches the same dict to both the
    "marker" (visuals) and "movement" (speeds/positions) parser maps –
    this is the only path the importer takes for the marker section. A
    regression here would split the field set across separate import
    branches."""
    from openfollow.web.routes import _apply_import_data

    base = AppConfig()
    new = _apply_import_data(
        base,
        {
            "marker": {
                "ball_visible": False,  # marker (visual) parser
                "min_speed": 0.42,  # movement parser
                "default_pos_x": 1.5,  # movement parser
            },
        },
    )
    assert new.marker.ball_visible is False
    assert new.marker.min_speed == pytest.approx(0.42)
    assert new.marker.default_pos_x == pytest.approx(1.5)


def test_apply_import_data_preserves_psn_source_ip_across_import() -> None:
    from openfollow.web.routes import _apply_import_data

    base = AppConfig()
    base.psn_source_ip = "10.10.10.10"

    new = _apply_import_data(base, {"psn_source_ip": "192.168.99.99"})

    assert new.psn_source_ip == "10.10.10.10"


def test_apply_import_data_skip_restart_applies_every_section() -> None:
    """All sections apply live; skip_restart_sections flag is preserved for backwards compatibility."""
    from openfollow.web.routes import _apply_import_data

    base = AppConfig()
    base.detection.confidence = 0.5
    base.otp_output.priority = 30  # real OtpOutputConfig field
    base.rttrpm_output.fps = 25  # real RttrpmOutputConfig field

    new = _apply_import_data(
        base,
        {
            "detection": {"confidence": 0.9},  # – live
            "otp_output": {"priority": 99},
            "rttrpm_output": {"fps": 60},
            "camera": {"pos_x": 7.5},  # always live
        },
        skip_restart_sections=True,
    )

    # All sections applied – made detection live too.
    assert new.detection.confidence == pytest.approx(0.9)
    assert new.otp_output.priority == 99
    assert new.rttrpm_output.fps == 60
    assert new.camera.pos_x == pytest.approx(7.5)


# ---------------------------------------------------------------------------
# _send_config_import_to_peer – peer-broadcast helper
# ---------------------------------------------------------------------------


def test_send_config_import_to_peer_refuses_non_private_ip(caplog) -> None:
    import logging as _logging

    from openfollow.web.routes import _send_config_import_to_peer

    with caplog.at_level(_logging.WARNING, logger="openfollow.web.routes"):
        ok = _send_config_import_to_peer("8.8.8.8", 8000, {"x": 1}, expected_port=8000)
    assert ok is False
    assert any("non-private" in rec.message.lower() for rec in caplog.records)


def test_send_config_import_to_peer_returns_true_on_http_200(monkeypatch) -> None:
    """Happy path: the peer accepts the imported config; the helper returns
    True so the broadcast aggregator marks the peer ``success=True``."""
    from openfollow.web import routes as routes_mod

    captured: dict = {}

    class _FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["body"] = req.data
        return _FakeResp()

    monkeypatch.setattr(routes_mod.urllib.request, "urlopen", _fake_urlopen)

    ok = routes_mod._send_config_import_to_peer(
        "192.168.1.5",
        8000,
        {"camera": {"pos_x": 1.0}},
        expected_port=8000,
    )
    assert ok is True
    # Exact path the peer's bottle dispatcher expects – drift here would
    # break peer broadcasts silently (the receiver would 404 and the
    # import would never apply).
    assert captured["url"] == "http://192.168.1.5:8000/api/config/import?skip_restart=1"
    assert captured["timeout"] == 10
    assert b'"camera"' in captured["body"]


def test_send_config_import_to_peer_returns_false_on_url_error(monkeypatch) -> None:
    from openfollow.web import routes as routes_mod

    def _boom(req, timeout):
        raise routes_mod.urllib.error.URLError("connection refused")

    monkeypatch.setattr(routes_mod.urllib.request, "urlopen", _boom)

    assert routes_mod._send_config_import_to_peer("10.0.0.1", 8000, {}, expected_port=8000) is False


# ---------------------------------------------------------------------------
# _broadcast_to_peers – capacity / timeout exhaustion
# ---------------------------------------------------------------------------


def test_broadcast_to_peers_marks_remaining_peers_failed_when_overall_timeout_zero() -> None:
    """When the overall_timeout has already expired before any peer can be
    dispatched, the loop hits the capacity_exhausted break and the result
    list still contains an entry per input peer (all marked failed) so
    the UI can render a stable per-peer status."""
    from openfollow.web.discovery import PeerInfo
    from openfollow.web.routes import _broadcast_to_peers

    peers = [
        PeerInfo(name="A", ip="192.168.1.10", web_port=8000, version="0.1.0", last_seen=0.0),
        PeerInfo(name="B", ip="192.168.1.11", web_port=8000, version="0.1.0", last_seen=0.0),
    ]
    # Negative timeout guarantees ``remaining <= 0`` on the first iteration,
    # so no thread is ever spawned.
    results = _broadcast_to_peers(peers, lambda _peer: True, overall_timeout=-1.0)

    assert len(results) == len(peers)
    assert [r["ip"] for r in results] == ["192.168.1.10", "192.168.1.11"]
    assert all(r["success"] is False for r in results)


def test_broadcast_to_peers_returns_empty_for_empty_peer_list_short_circuits() -> None:
    """Short-circuit guard at the top – no semaphore acquire, no thread
    spawn. A regression that dropped this guard would still return
    correctly but burn semaphore slots / thread spawns under repeated
    no-op broadcasts."""
    from openfollow.web.routes import _broadcast_to_peers

    calls: list[str] = []

    def _send(_peer):
        calls.append("called")
        return True

    assert _broadcast_to_peers([], _send, overall_timeout=5.0) == []
    assert calls == []


# ---------------------------------------------------------------------------
# Misc tiny helpers that were previously only reached transitively
# ---------------------------------------------------------------------------


def test_as_str_returns_default_when_value_is_none() -> None:
    """``_as_str`` is the basic string coercer used by every section
    parser. The ``None`` short-circuit lets callers omit a key without
    blanking it – without this, an absent JSON value would coerce to
    ``"None"``, persisting the literal four-character string."""
    from openfollow.web.routes import _as_str

    assert _as_str(None, "kept-default") == "kept-default"
    # Non-None values still go through ``str()`` so ints/bools coerce.
    assert _as_str(42, "default") == "42"
    assert _as_str(True, "default") == "True"


def test_apply_import_data_ignores_zones_key_when_value_is_not_a_list() -> None:
    from openfollow.web.routes import _apply_import_data

    base = AppConfig()
    new = _apply_import_data(
        base,
        {
            "trigger_zones": {
                "enabled": True,
                "zones": {"not": "a list"},  # malformed; must be skipped
            },
        },
    )
    # Globals applied; zones list left untouched.
    assert new.trigger_zones.enabled is True
    assert new.trigger_zones.zones == base.trigger_zones.zones


# ---------------------------------------------------------------------------
# _is_on_device_request – loopback detection for the on-device footer hint
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "remote",
    ["127.0.0.1", "::1", "localhost"],
)
def test_is_on_device_request_true_for_loopback(monkeypatch, remote) -> None:
    """The embedded WebKit overlay loads ``http://127.0.0.1:<port>/``;
    a loopback ``remote_addr`` is the signal to render gamepad-only
    affordances. Cover IPv4, IPv6, and the hostname-resolved form."""
    from openfollow.web import routes as routes_mod

    class _FakeReq:
        def __init__(self, addr: str) -> None:
            self.remote_addr = addr

    monkeypatch.setattr(routes_mod, "request", _FakeReq(remote))
    assert routes_mod._is_on_device_request() is True


@pytest.mark.parametrize(
    "remote",
    ["192.168.1.42", "10.0.0.5", "203.0.113.7", ""],
)
def test_is_on_device_request_false_for_lan_and_empty(monkeypatch, remote) -> None:
    """LAN clients hit the server's external IP – never appear as
    loopback. An empty ``remote_addr`` (no source available) is
    treated as off-device so the footer hint doesn't leak to remote
    operators who can't act on the B-button instruction."""
    from openfollow.web import routes as routes_mod

    class _FakeReq:
        def __init__(self, addr: str) -> None:
            self.remote_addr = addr

    monkeypatch.setattr(routes_mod, "request", _FakeReq(remote))
    assert routes_mod._is_on_device_request() is False


def test_is_on_device_request_strips_surrounding_whitespace(monkeypatch) -> None:
    """Be lenient about whitespace in ``remote_addr`` since some WSGI
    layers normalise inconsistently; the loopback check must still
    fire on ``" 127.0.0.1 "`` and similar."""
    from openfollow.web import routes as routes_mod

    class _FakeReq:
        remote_addr = "  127.0.0.1  "

    monkeypatch.setattr(routes_mod, "request", _FakeReq())
    assert routes_mod._is_on_device_request() is True


def test_is_on_device_request_handles_none_remote_addr(monkeypatch) -> None:
    from openfollow.web import routes as routes_mod

    class _FakeReq:
        remote_addr = None

    monkeypatch.setattr(routes_mod, "request", _FakeReq())
    assert routes_mod._is_on_device_request() is False


# ---------------------------------------------------------------------------
# _cancel_button_label – gamepad-button label for the on-device footer hint
# ---------------------------------------------------------------------------


def test_cancel_button_label_returns_configured_value() -> None:
    """The operator's mapped ``btn_menu_cancel`` (default "B") is the
    label the footer renders. Remapping to A / X / Y / etc. via the
    Button Detection wizard must flow through to the hint so the
    operator isn't told to press a button that no longer cancels."""
    from openfollow.configuration import AppConfig
    from openfollow.web import routes as routes_mod

    cfg = AppConfig()
    cfg.controller.btn_menu_cancel = "Y"
    assert routes_mod._cancel_button_label(cfg) == "Y"


def test_cancel_button_label_default_is_B() -> None:
    """Out-of-box config has ``btn_menu_cancel = "B"`` – the hint
    starts as the gamepad convention and stays correct until the
    operator remaps."""
    from openfollow.configuration import AppConfig
    from openfollow.web import routes as routes_mod

    cfg = AppConfig()
    assert routes_mod._cancel_button_label(cfg) == "B"


@pytest.mark.parametrize("cleared", ["", "   "])
def test_cancel_button_label_falls_back_when_binding_cleared(cleared) -> None:
    """Empty / whitespace ``btn_menu_cancel`` (an operator cleared the
    binding via the web form) → fall back to the word ``"Cancel"`` so
    the footer renders ``<kbd>Cancel</kbd>Close embedded browser``
    instead of an empty key-cap hint."""
    from openfollow.configuration import AppConfig
    from openfollow.web import routes as routes_mod

    cfg = AppConfig()
    cfg.controller.btn_menu_cancel = cleared
    assert routes_mod._cancel_button_label(cfg) == "Cancel"


class TestPeerPortAllowlist:
    """Outbound peer connections may only target a plausible web port;
    the beacon-advertised port is otherwise an attacker-controlled SSRF."""

    def test_is_allowed_peer_port(self) -> None:
        from openfollow.web.routes import _is_allowed_peer_port

        assert _is_allowed_peer_port(8080, 9000) is True  # conventional default
        assert _is_allowed_peer_port(80, 9000) is True  # conventional default
        assert _is_allowed_peer_port(9000, 9000) is True  # matches configured
        assert _is_allowed_peer_port(31337, 8080) is False  # arbitrary beacon port
        assert _is_allowed_peer_port(22, 8080) is False  # internal-service probe

    def test_probe_peer_refuses_unexpected_port(self) -> None:
        from openfollow.web.routes import _probe_peer

        out = _probe_peer("10.0.0.5", 22, "rig", expected_port=8080)
        assert out["ok"] is False
        assert out["status"] == 0
        assert "unexpected port" in out["error"]

    def test_send_config_to_peer_refuses_unexpected_port(self, caplog) -> None:
        from openfollow.web.routes import _send_config_to_peer

        with caplog.at_level("WARNING", logger="openfollow.web.routes"):
            ok = _send_config_to_peer("10.0.0.5", 31337, "camera", {}, pin="", expected_port=8080)
        assert ok is False
        assert any("unexpected port" in r.message for r in caplog.records)

    def test_send_config_import_to_peer_refuses_unexpected_port(self, caplog) -> None:
        from openfollow.web.routes import _send_config_import_to_peer

        with caplog.at_level("WARNING", logger="openfollow.web.routes"):
            ok = _send_config_import_to_peer("10.0.0.5", 31337, {}, pin="", expected_port=8080)
        assert ok is False
        assert any("unexpected port" in r.message for r in caplog.records)


class TestAllowedRequestHosts:
    """The CSRF host allowlist covers loopback, local IPs, and the
    device hostname so legitimate access isn't blocked."""

    def test_includes_loopback_and_hostname(self) -> None:
        import socket

        from openfollow.web.routes import _allowed_request_hosts

        hosts = _allowed_request_hosts()
        assert "127.0.0.1" in hosts
        assert "localhost" in hosts
        assert "::1" in hosts
        host = socket.gethostname().strip().lower()
        if host:
            assert host in hosts
            assert f"{host}.local" in hosts


def test_allowed_request_hosts_blank_hostname_falls_back(monkeypatch) -> None:
    """A blank ``socket.gethostname()`` leaves only loopback + local IPs
    in the allowlist (no empty/``.local`` host entries)."""
    import openfollow.web.routes as routes_mod

    monkeypatch.setattr(routes_mod.socket, "gethostname", lambda: "   ")
    hosts = routes_mod._allowed_request_hosts()
    assert "127.0.0.1" in hosts and "localhost" in hosts and "::1" in hosts
    assert "" not in hosts
    assert not any(h.endswith(".local") for h in hosts)


# ---------------------------------------------------------------------------
# OSC destinations: client list + <script>-safe JSON embedding
# ---------------------------------------------------------------------------


def test_osc_destinations_client_list_shape() -> None:
    cfg = AppConfig(
        osc_destinations=OscDestinationsConfig(
            destinations=[
                OscDestinationConfig(
                    id="d1",
                    name="A",
                    host="10.0.0.9",
                    port=9000,
                    protocol="tcp",
                    framing="length_prefix",
                )
            ]
        )
    )
    assert osc_destinations_client_list(cfg) == [
        {
            "id": "d1",
            "name": "A",
            "host": "10.0.0.9",
            "port": 9000,
            "protocol": "tcp",
            "framing": "length_prefix",
        }
    ]


def test_osc_destinations_script_json_escapes_script_breakout() -> None:
    """A destination name/host with ``</script>`` or ``&`` must not break out of
    the ``<script>`` block it is embedded in via ``{{! ... }}``."""
    import json

    cfg = AppConfig(
        osc_destinations=OscDestinationsConfig(
            destinations=[
                OscDestinationConfig(
                    id="d1",
                    name="</script><img src=x onerror=alert(1)>",
                    host="a&b",
                )
            ]
        )
    )
    out = osc_destinations_script_json(cfg)
    # No raw HTML-significant characters survive: a script element cannot be
    # closed, no tag can open, no entity is introduced.
    assert "</script>" not in out
    assert "<" not in out and ">" not in out and "&" not in out
    assert "\\u003c/script\\u003e" in out
    # The payload still round-trips: a JSON/JS parser decodes the escapes back.
    restored = json.loads(out)
    assert restored[0]["name"] == "</script><img src=x onerror=alert(1)>"
    assert restored[0]["host"] == "a&b"
