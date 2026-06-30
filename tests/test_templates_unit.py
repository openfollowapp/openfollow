# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for the file-based template system."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openfollow.templates import (
    TEMPLATE_FILE_SUFFIX,
    TEMPLATE_LEGACY_SUFFIX,
    TEMPLATE_VERSION,
    OpenFollowTemplate,
    TemplateValidationError,
    validate_payload,
)
from openfollow.templates.bootstrap import seed_system_templates
from openfollow.templates.loader import (
    LoadedTemplate,
    find_template,
    list_templates,
    list_templates_by_type,
    parse_envelope,
)
from openfollow.templates.writer import (
    TemplateWriteError,
    delete_user_template,
    slugify,
    write_user_template,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _osc_payload(**overrides: object) -> dict[str, object]:
    out: dict[str, object] = {
        "address": "/cue/[markerid]/go",
        "args": ["[x]", "[y]"],
    }
    out.update(overrides)
    return out


def _envelope(**overrides: object) -> dict[str, object]:
    out: dict[str, object] = {
        "version": TEMPLATE_VERSION,
        "type": "osc_output",
        "id": "abc123",
        "name": "Sample",
        "is_system": False,
        "payload": _osc_payload(),
    }
    out.update(overrides)
    return out


# ---------------------------------------------------------------------------
# Schema envelope
# ---------------------------------------------------------------------------


class TestEnvelope:
    def test_minimal_valid_envelope_round_trips(self) -> None:
        tpl = OpenFollowTemplate(**_envelope())
        out = tpl.to_dict()
        assert out["version"] == TEMPLATE_VERSION
        assert out["type"] == "osc_output"
        assert out["id"] == "abc123"
        assert out["name"] == "Sample"
        assert out["is_system"] is False
        assert out["payload"] == _osc_payload()

    def test_missing_id_mints_uuid(self) -> None:
        tpl = OpenFollowTemplate(**_envelope(id=""))
        assert tpl.id and len(tpl.id) == 32  # uuid4().hex length

    def test_id_with_whitespace_is_stripped(self) -> None:
        tpl = OpenFollowTemplate(**_envelope(id="  xyz  "))
        assert tpl.id == "xyz"

    def test_non_int_version_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="version"):
            OpenFollowTemplate(**_envelope(version="1"))  # type: ignore[arg-type]

    def test_bool_version_rejected(self) -> None:
        # ``True`` is an ``int`` subclass; rejected explicitly.
        with pytest.raises(TemplateValidationError, match="version"):
            OpenFollowTemplate(**_envelope(version=True))  # type: ignore[arg-type]

    def test_unsupported_version_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="unsupported version"):
            OpenFollowTemplate(**_envelope(version=99))

    def test_unknown_type_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="unknown type"):
            OpenFollowTemplate(**_envelope(type="bogus"))

    def test_non_str_type_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="unknown type"):
            OpenFollowTemplate(**_envelope(type=42))  # type: ignore[arg-type]

    def test_non_str_id_mints_fresh(self) -> None:
        # Wrong-type id is treated as missing: mint fresh, don't fail the file.
        tpl = OpenFollowTemplate(**_envelope(id=42))  # type: ignore[arg-type]
        assert tpl.id and len(tpl.id) == 32

    def test_non_str_name_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="name must be str"):
            OpenFollowTemplate(**_envelope(name=42))  # type: ignore[arg-type]

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="name must not be empty"):
            OpenFollowTemplate(**_envelope(name="   "))

    def test_non_bool_is_system_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="is_system must be bool"):
            OpenFollowTemplate(**_envelope(is_system="true"))  # type: ignore[arg-type]

    def test_non_dict_payload_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="payload must be object"):
            OpenFollowTemplate(**_envelope(payload="not an object"))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Per-type payload validators
# ---------------------------------------------------------------------------


class TestOscOutputPayload:
    def test_valid_payload_passes(self) -> None:
        validate_payload("osc_output", _osc_payload())

    def test_missing_address_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="missing required key 'address'"):
            validate_payload("osc_output", {"args": []})

    def test_non_str_address_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="'address' must be str"):
            validate_payload("osc_output", {"address": 42})

    def test_non_list_args_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="'args' must be list"):
            validate_payload("osc_output", {"address": "/x", "args": "abc"})

    def test_non_str_arg_element_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match=r"'args\[1\]' must be str"):
            validate_payload(
                "osc_output",
                {"address": "/x", "args": ["[x]", 42, "[y]"]},
            )

    def test_args_default_to_empty(self) -> None:
        # Address-only payload with no ``args`` key is valid.
        validate_payload("osc_output", {"address": "/cue/go"})

    def test_relative_address_rejected(self) -> None:
        # OSC 1.0 addresses must start with ``/``; a relative address
        # would land a row whose send never works.
        with pytest.raises(TemplateValidationError, match=r"must start with '/'"):
            validate_payload("osc_output", {"address": "cue/go"})

    def test_empty_address_passes(self) -> None:
        # Empty address is a valid intermediate state; apply lands a
        # blank row that skips sends until the address is filled in.
        validate_payload("osc_output", {"address": ""})

    def test_extended_payload_passes(self) -> None:
        # Non-address fields are optional but type-checked.
        validate_payload(
            "osc_output",
            {
                "name": "ETC stage",
                "destination_id": "dest-1",
                "address": "/cue/go",
                "args": ["[x]"],
                "rate_hz": 60,
                "trigger": {"kind": "stream", "rate_hz": 60},
            },
        )

    @pytest.mark.parametrize("field_name", ["name", "destination_id"])
    def test_non_str_extended_string_fields_rejected(
        self,
        field_name: str,
    ) -> None:
        with pytest.raises(TemplateValidationError, match=field_name):
            validate_payload(
                "osc_output",
                {
                    "address": "/x",
                    field_name: 42,
                },
            )

    def test_non_int_rate_hz_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="rate_hz"):
            validate_payload(
                "osc_output",
                {
                    "address": "/x",
                    "rate_hz": "8000",
                },
            )

    def test_bool_for_rate_hz_rejected(self) -> None:
        # ``True`` is an ``int`` subclass; rejected so ``rate_hz = true``
        # doesn't become rate 1 on apply.
        with pytest.raises(TemplateValidationError, match="rate_hz"):
            validate_payload(
                "osc_output",
                {
                    "address": "/x",
                    "rate_hz": True,
                },
            )

    def test_non_dict_trigger_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="'trigger' must be object"):
            validate_payload(
                "osc_output",
                {
                    "address": "/x",
                    "trigger": "stream",
                },
            )

    # ``trigger`` needs per-kind validation: a bare isinstance(dict)
    # check lets ``{"kind": "bogus"}`` apply as a fallback
    # ``StreamTrigger`` via ``_trigger_from_dict``.

    def test_trigger_kind_missing_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="'trigger.kind'"):
            validate_payload(
                "osc_output",
                {
                    "address": "/x",
                    "trigger": {"rate_hz": 30},
                },
            )

    def test_trigger_kind_unknown_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="'trigger.kind' must be one of"):
            validate_payload(
                "osc_output",
                {
                    "address": "/x",
                    "trigger": {"kind": "bogus"},
                },
            )

    def test_encoder_on_change_kind_rejected(self) -> None:
        # Not a selectable kind (no relative-encoder hardware;
        # ``_trigger_from_dict`` downgrades it to ``StreamTrigger`` on load),
        # so it isn't a valid template trigger.
        with pytest.raises(TemplateValidationError, match="'trigger.kind' must be one of"):
            validate_payload(
                "osc_output",
                {"address": "/x", "trigger": {"kind": "encoder_on_change"}},
            )

    def test_midi_message_trigger_valid_full_shape(self) -> None:
        validate_payload(
            "osc_output",
            {
                "address": "/x",
                "trigger": {
                    "kind": "midi_message",
                    "patch_id": 0,
                    "type": "note_on",
                    "channel": 1,
                    "number": 60,
                    "value": 100,
                },
            },
        )

    def test_midi_message_trigger_coerced_field_rejected(self) -> None:
        # A bad ``type`` would silently become ``note_on`` on apply.
        with pytest.raises(TemplateValidationError, match=r"'trigger.type' is invalid"):
            validate_payload(
                "osc_output",
                {
                    "address": "/x",
                    "trigger": {"kind": "midi_message", "type": "bogus_type"},
                },
            )

    def test_midi_message_trigger_unknown_key_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="unknown key"):
            validate_payload(
                "osc_output",
                {
                    "address": "/x",
                    "trigger": {"kind": "midi_message", "bogus": 1},
                },
            )

    def test_fader_on_change_trigger_valid_full_shape(self) -> None:
        validate_payload(
            "osc_output",
            {
                "address": "/x",
                "trigger": {
                    "kind": "fader_on_change",
                    "fader": 1,
                    "rate_hz": 30,
                    "marker_id": 0,
                },
            },
        )

    def test_fader_on_change_trigger_coerced_field_rejected(self) -> None:
        # An out-of-range fader index would clamp on apply.
        with pytest.raises(TemplateValidationError, match=r"'trigger.fader' is invalid"):
            validate_payload(
                "osc_output",
                {
                    "address": "/x",
                    "trigger": {"kind": "fader_on_change", "fader": 9999},
                },
            )

    def test_stream_trigger_valid_full_shape(self) -> None:
        validate_payload(
            "osc_output",
            {
                "address": "/x",
                "trigger": {
                    "kind": "stream",
                    "rate_hz": 60,
                    "mode": "on_change",
                    "min_change_m": 0.1,
                },
            },
        )

    def test_stream_trigger_non_int_rate_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="'trigger.rate_hz' must be int"):
            validate_payload(
                "osc_output",
                {
                    "address": "/x",
                    "trigger": {"kind": "stream", "rate_hz": "60"},
                },
            )

    def test_stream_trigger_bad_mode_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="'trigger.mode' must be one of"):
            validate_payload(
                "osc_output",
                {
                    "address": "/x",
                    "trigger": {"kind": "stream", "mode": "ALWAYS"},
                },
            )

    def test_stream_trigger_bad_min_change_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="'trigger.min_change_m'"):
            validate_payload(
                "osc_output",
                {
                    "address": "/x",
                    "trigger": {"kind": "stream", "min_change_m": "5cm"},
                },
            )

    def test_hotkey_trigger_valid_full_shape(self) -> None:
        validate_payload(
            "osc_output",
            {
                "address": "/x",
                "trigger": {
                    "kind": "hotkey",
                    "key": "Space",
                    "modifiers": ["ctrl", "shift"],
                    "edge": "press",
                },
            },
        )

    def test_hotkey_trigger_non_str_key_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="'trigger.key'"):
            validate_payload(
                "osc_output",
                {
                    "address": "/x",
                    "trigger": {"kind": "hotkey", "key": 42},
                },
            )

    def test_hotkey_trigger_bad_modifier_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match=r"'trigger.modifiers\[0\]'"):
            validate_payload(
                "osc_output",
                {
                    "address": "/x",
                    "trigger": {"kind": "hotkey", "modifiers": ["meta"]},
                },
            )

    def test_hotkey_trigger_modifiers_not_list_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="'trigger.modifiers' must be a list"):
            validate_payload(
                "osc_output",
                {
                    "address": "/x",
                    "trigger": {"kind": "hotkey", "modifiers": "ctrl"},
                },
            )

    def test_hotkey_trigger_bad_edge_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="'trigger.edge'"):
            validate_payload(
                "osc_output",
                {
                    "address": "/x",
                    "trigger": {"kind": "hotkey", "edge": "tap"},
                },
            )

    def test_controller_button_trigger_valid_full_shape(self) -> None:
        validate_payload(
            "osc_output",
            {
                "address": "/x",
                "trigger": {
                    "kind": "controller_button",
                    "button": "A",
                    "edge": "release",
                },
            },
        )

    def test_controller_button_trigger_non_str_button_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="'trigger.button'"):
            validate_payload(
                "osc_output",
                {
                    "address": "/x",
                    "trigger": {"kind": "controller_button", "button": 1},
                },
            )

    def test_controller_button_trigger_bad_edge_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="'trigger.edge'"):
            validate_payload(
                "osc_output",
                {
                    "address": "/x",
                    "trigger": {"kind": "controller_button", "edge": "double"},
                },
            )

    # Connection is no longer part of the template (it lives on a shared
    # OSC destination); a template only carries a ``destination_id`` string.
    def test_legacy_connection_keys_rejected_as_unknown(self) -> None:
        for bad_key in ("host", "port", "protocol", "framing"):
            with pytest.raises(TemplateValidationError, match="unknown key"):
                validate_payload(
                    "osc_output",
                    {"address": "/x", bad_key: "whatever"},
                )

    def test_destination_id_passes(self) -> None:
        validate_payload("osc_output", {"address": "/x", "destination_id": "dest-1"})

    def test_top_level_rate_hz_out_of_set_rejected(self) -> None:
        # 59 isn't in (1, 5, 10, 20, 30, 60); row config silently snaps
        # to 60, so reject to avoid auto-mutation.
        with pytest.raises(TemplateValidationError, match="'rate_hz' must be one of"):
            validate_payload(
                "osc_output",
                {
                    "address": "/x",
                    "rate_hz": 59,
                },
            )

    def test_top_level_rate_hz_in_set_passes(self) -> None:
        validate_payload("osc_output", {"address": "/x", "rate_hz": 30})

    def test_trigger_rate_hz_out_of_set_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="'trigger.rate_hz' must be one of"):
            validate_payload(
                "osc_output",
                {
                    "address": "/x",
                    "trigger": {"kind": "stream", "rate_hz": 59},
                },
            )

    def test_trigger_min_change_m_negative_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="'trigger.min_change_m' must be >= 0"):
            validate_payload(
                "osc_output",
                {
                    "address": "/x",
                    "trigger": {"kind": "stream", "min_change_m": -0.1},
                },
            )

    def test_trigger_min_change_m_zero_passes(self) -> None:
        validate_payload(
            "osc_output",
            {
                "address": "/x",
                "trigger": {"kind": "stream", "min_change_m": 0.0},
            },
        )

    # ``OscTransmitterConfig`` and ``_trigger_from_dict`` silently drop
    # unknown keys, so a typo would vanish and fall back to default on
    # apply. Reject up front so malformed files surface as unreadable.
    def test_unknown_top_level_key_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="unknown key"):
            validate_payload(
                "osc_output",
                {
                    "address": "/x",
                    "protcol": "udp",  # typo
                },
            )

    def test_known_top_level_keys_pass(self) -> None:
        validate_payload(
            "osc_output",
            {
                "address": "/x",
                "args": ["[x]"],
                "name": "Eos",
                "destination_id": "dest-1",
                "rate_hz": 30,
                "trigger": {"kind": "stream", "rate_hz": 30, "mode": "always"},
            },
        )

    def test_unknown_trigger_key_for_stream_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="unknown key"):
            validate_payload(
                "osc_output",
                {
                    "address": "/x",
                    "trigger": {"kind": "stream", "min_chnage_m": 0.1},  # typo
                },
            )

    def test_unknown_trigger_key_for_hotkey_rejected(self) -> None:
        # ``rate_hz`` is valid for stream, not hotkey; the per-kind
        # whitelist rejects it here.
        with pytest.raises(TemplateValidationError, match="unknown key"):
            validate_payload(
                "osc_output",
                {
                    "address": "/x",
                    "trigger": {"kind": "hotkey", "key": "space", "rate_hz": 30},
                },
            )

    def test_unknown_trigger_key_for_controller_button_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="unknown key"):
            validate_payload(
                "osc_output",
                {
                    "address": "/x",
                    "trigger": {
                        "kind": "controller_button",
                        "button": "a",
                        "modifiers": [],
                    },
                },
            )


class TestCameraGridPayload:
    def test_both_pass(self) -> None:
        validate_payload("camera_grid", {"camera": {}, "grid": {}})

    # ``camera_grid`` is a full camera+grid snapshot; a partial
    # template would overwrite one half and desync the pair. Require
    # BOTH halves so partial files surface as unreadable at load time.
    def test_camera_only_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="must contain BOTH"):
            validate_payload("camera_grid", {"camera": {}})

    def test_grid_only_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="must contain BOTH"):
            validate_payload("camera_grid", {"grid": {}})

    def test_neither_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="must contain BOTH"):
            validate_payload("camera_grid", {})

    def test_non_dict_camera_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="'camera' must be object"):
            validate_payload(
                "camera_grid",
                {"camera": "x", "grid": {}},
            )

    def test_non_dict_grid_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="'grid' must be object"):
            validate_payload(
                "camera_grid",
                {"camera": {}, "grid": []},
            )

    def test_unknown_camera_field_rejected(self) -> None:
        # Unknown keys raise ``TypeError`` at the dataclass constructor;
        # the validator re-raises as ``TemplateValidationError`` to keep
        # the loader's error surface uniform.
        with pytest.raises(TemplateValidationError, match="'camera' invalid"):
            validate_payload(
                "camera_grid",
                {"camera": {"bogus_field": 1}, "grid": {}},
            )

    def test_unknown_grid_field_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="'grid' invalid"):
            validate_payload(
                "camera_grid",
                {"camera": {}, "grid": {"bogus_field": 1}},
            )


class TestZonesPayload:
    def test_empty_zones_payload_rejected(self) -> None:
        # An empty payload would apply as a destructive factory reset
        # (wipes every zone + resets every section default). Reject so
        # the loader surfaces it as unreadable instead.
        with pytest.raises(TemplateValidationError, match="must include a 'zones' array"):
            validate_payload("zones", {})

    def test_zones_payload_without_zones_key_rejected(self) -> None:
        # A file with section-level defaults but no ``zones`` array
        # would still wipe the zones on apply; require a real snapshot.
        with pytest.raises(TemplateValidationError, match="must include a 'zones' array"):
            validate_payload("zones", {"debounce_ms": 100})

    def test_zones_payload_with_empty_zones_array_passes(self) -> None:
        # Explicit ``"zones": []`` is allowed: a blank-slate template
        # with the empty-zones intent stated explicitly.
        validate_payload("zones", {"zones": []})

    def test_full_zones_section_passes(self) -> None:
        validate_payload(
            "zones",
            {
                "enabled": True,
                "show_overlay": True,
                "eval_fps": 10,
                "debounce_ms": 200,
                "hysteresis": 0.05,
                "zones": [],
            },
        )

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(TemplateValidationError, match="zones payload invalid"):
            validate_payload("zones", {"bogus_field": 1, "zones": []})


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


class TestLoader:
    def test_missing_folders_returns_empty(self, tmp_path: Path) -> None:
        assert list_templates(tmp_path) == []

    def test_empty_folders_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "system").mkdir()
        (tmp_path / "user").mkdir()
        assert list_templates(tmp_path) == []

    def test_loads_valid_user_template(self, tmp_path: Path) -> None:
        user = tmp_path / "user"
        user.mkdir()
        (user / f"osc_output.cue{TEMPLATE_FILE_SUFFIX}").write_text(
            json.dumps(_envelope(name="Cue")),
        )
        loaded = list_templates(tmp_path)
        assert len(loaded) == 1
        assert loaded[0].error == ""
        assert loaded[0].template is not None
        assert loaded[0].template.name == "Cue"
        assert loaded[0].is_system is False

    def test_loads_valid_system_template(self, tmp_path: Path) -> None:
        sysdir = tmp_path / "system"
        sysdir.mkdir()
        # File says ``is_system: false``; loader overrides to ``True``
        # from the source folder.
        (sysdir / f"osc_output.etc{TEMPLATE_FILE_SUFFIX}").write_text(
            json.dumps(_envelope(name="ETC", is_system=False)),
        )
        loaded = list_templates(tmp_path)
        assert len(loaded) == 1
        assert loaded[0].is_system is True
        assert loaded[0].template is not None
        assert loaded[0].template.is_system is True

    def test_user_file_claiming_system_is_overridden(self, tmp_path: Path) -> None:
        # ``is_system: true`` in a user file must not escalate; the
        # folder is the authority.
        user = tmp_path / "user"
        user.mkdir()
        (user / f"osc_output.x{TEMPLATE_FILE_SUFFIX}").write_text(
            json.dumps(_envelope(is_system=True)),
        )
        loaded = list_templates(tmp_path)
        assert loaded[0].is_system is False
        assert loaded[0].template is not None
        assert loaded[0].template.is_system is False

    def test_invalid_json_surfaces_as_error(self, tmp_path: Path) -> None:
        user = tmp_path / "user"
        user.mkdir()
        (user / f"osc_output.broken{TEMPLATE_FILE_SUFFIX}").write_text("{not json")
        loaded = list_templates(tmp_path)
        assert len(loaded) == 1
        assert loaded[0].template is None
        assert "invalid JSON" in loaded[0].error

    def test_non_utf8_file_surfaces_as_error(self, tmp_path: Path) -> None:
        # A file saved in a non-UTF-8 encoding raises UnicodeDecodeError,
        # which is not an OSError. It must surface as a per-entry error
        # rather than aborting the whole list.
        user = tmp_path / "user"
        user.mkdir()
        # 0xFF is never valid UTF-8.
        (user / f"osc_output.latin1{TEMPLATE_FILE_SUFFIX}").write_bytes(b"\xff\xfe bad")
        loaded = list_templates(tmp_path)
        assert len(loaded) == 1
        assert loaded[0].template is None
        assert "UTF-8" in loaded[0].error

    def test_non_utf8_file_does_not_abort_other_entries(self, tmp_path: Path) -> None:
        # One bad-encoding file must not disable the list for valid files.
        user = tmp_path / "user"
        user.mkdir()
        (user / f"osc_output.bad{TEMPLATE_FILE_SUFFIX}").write_bytes(b"\xff\xfe")
        (user / f"osc_output.good{TEMPLATE_FILE_SUFFIX}").write_text(
            json.dumps(_envelope(name="Good")),
        )
        loaded = list_templates(tmp_path)
        assert len(loaded) == 2
        good = [e for e in loaded if e.error == ""]
        assert len(good) == 1
        assert good[0].template is not None
        assert good[0].template.name == "Good"

    def test_top_level_array_rejected(self, tmp_path: Path) -> None:
        user = tmp_path / "user"
        user.mkdir()
        (user / f"osc_output.list{TEMPLATE_FILE_SUFFIX}").write_text("[1,2,3]")
        loaded = list_templates(tmp_path)
        assert loaded[0].template is None
        assert "must be object" in loaded[0].error

    def test_envelope_validation_error_surfaces(self, tmp_path: Path) -> None:
        user = tmp_path / "user"
        user.mkdir()
        (user / f"osc_output.bad{TEMPLATE_FILE_SUFFIX}").write_text(
            json.dumps(_envelope(type="not_a_type")),
        )
        loaded = list_templates(tmp_path)
        assert loaded[0].template is None
        assert "unknown type" in loaded[0].error

    def test_payload_validation_error_surfaces(self, tmp_path: Path) -> None:
        user = tmp_path / "user"
        user.mkdir()
        (user / f"osc_output.bad{TEMPLATE_FILE_SUFFIX}").write_text(
            json.dumps(_envelope(payload={"args": []})),  # missing address
        )
        loaded = list_templates(tmp_path)
        assert loaded[0].template is None
        assert "missing required key 'address'" in loaded[0].error

    def test_unrecognised_envelope_field_dropped(self, tmp_path: Path) -> None:
        # Loader drops unknown envelope keys so the version check, not
        # the ``__init__`` signature, surfaces a future-version file.
        user = tmp_path / "user"
        user.mkdir()
        data = _envelope()
        data["future_field"] = "xyz"
        (user / f"osc_output.future{TEMPLATE_FILE_SUFFIX}").write_text(json.dumps(data))
        loaded = list_templates(tmp_path)
        assert loaded[0].error == ""

    def test_glob_skips_non_template_files(self, tmp_path: Path) -> None:
        user = tmp_path / "user"
        user.mkdir()
        (user / "README.md").write_text("not a template")
        (user / "config.toml").write_text("# stray")
        assert list_templates(tmp_path) == []

    def test_list_by_type_filters(self, tmp_path: Path) -> None:
        user = tmp_path / "user"
        user.mkdir()
        (user / f"osc_output.a{TEMPLATE_FILE_SUFFIX}").write_text(
            json.dumps(_envelope(name="A")),
        )
        (user / f"zones.b{TEMPLATE_FILE_SUFFIX}").write_text(
            json.dumps(_envelope(name="B", type="zones", payload={})),
        )
        only_osc = list_templates_by_type(tmp_path, "osc_output")
        assert len(only_osc) == 1
        assert only_osc[0].template is not None
        assert only_osc[0].template.name == "A"

    def test_list_by_type_drops_unreadable(self, tmp_path: Path) -> None:
        user = tmp_path / "user"
        user.mkdir()
        (user / f"osc_output.bad{TEMPLATE_FILE_SUFFIX}").write_text("garbage")
        assert list_templates_by_type(tmp_path, "osc_output") == []

    def test_find_by_filename_user(self, tmp_path: Path) -> None:
        user = tmp_path / "user"
        user.mkdir()
        (user / f"osc_output.cue{TEMPLATE_FILE_SUFFIX}").write_text(
            json.dumps(_envelope()),
        )
        found = find_template(tmp_path, f"osc_output.cue{TEMPLATE_FILE_SUFFIX}")
        assert isinstance(found, LoadedTemplate)
        assert found.is_system is False

    def test_find_by_filename_system(self, tmp_path: Path) -> None:
        sysdir = tmp_path / "system"
        sysdir.mkdir()
        (sysdir / f"osc_output.x{TEMPLATE_FILE_SUFFIX}").write_text(
            json.dumps(_envelope()),
        )
        found = find_template(tmp_path, f"osc_output.x{TEMPLATE_FILE_SUFFIX}")
        assert found is not None and found.is_system is True

    def test_find_returns_none_for_missing(self, tmp_path: Path) -> None:
        assert find_template(tmp_path, "nope.oftemplate") is None

    def test_find_prefers_user_over_system_on_basename_collision(
        self,
        tmp_path: Path,
    ) -> None:
        # On a basename collision across folders, the user copy must
        # win, else it is undeletable from the UI and unappliable by
        # filename.
        sysdir = tmp_path / "system"
        sysdir.mkdir()
        (sysdir / f"osc_output.shared{TEMPLATE_FILE_SUFFIX}").write_text(
            json.dumps(_envelope(name="System Copy")),
        )
        user = tmp_path / "user"
        user.mkdir()
        (user / f"osc_output.shared{TEMPLATE_FILE_SUFFIX}").write_text(
            json.dumps(_envelope(name="User Copy")),
        )
        found = find_template(
            tmp_path,
            f"osc_output.shared{TEMPLATE_FILE_SUFFIX}",
        )
        assert found is not None
        assert found.is_system is False
        assert found.template is not None
        assert found.template.name == "User Copy"


# ---------------------------------------------------------------------------
# Writer / slugify
# ---------------------------------------------------------------------------


class TestSlugify:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("My Cue", "my-cue"),
            ("ETC Eos", "etc-eos"),
            ("d&b absolute", "d-b-absolute"),
            ("Indoor / Outdoor!", "indoor-outdoor"),
            ("café", "cafe"),  # NFKD strips accents
            ("multiple   spaces", "multiple-spaces"),
            ("---trim---me---", "trim-me"),
            ("", "untitled"),
            ("   ", "untitled"),
            ("@@@@", "untitled"),  # stripped to nothing
        ],
    )
    def test_normalises(self, raw: str, expected: str) -> None:
        assert slugify(raw) == expected

    def test_truncates_long_input(self) -> None:
        # Truncated to 64, no trailing dash.
        out = slugify("a" * 200)
        assert len(out) == 64
        assert not out.endswith("-")

    def test_truncation_strips_trailing_dash(self) -> None:
        # Truncation point lands on a dash so the trailing-dash strip
        # kicks in.
        raw = ("ab" * 31) + "-c-d-e-f-g"  # 62 chars + suffix
        out = slugify(raw)
        assert not out.endswith("-")

    def test_non_str_input_falls_back(self) -> None:
        assert slugify(None) == "untitled"  # type: ignore[arg-type]


class TestWriteUserTemplate:
    def test_writes_envelope_with_minted_id(self, tmp_path: Path) -> None:
        path = write_user_template(
            tmp_path,
            "osc_output",
            "My Cue",
            _osc_payload(),
        )
        assert path.is_file()
        assert path.parent == tmp_path / "user"
        assert path.name == f"osc_output.my-cue{TEMPLATE_FILE_SUFFIX}"
        on_disk = json.loads(path.read_text())
        assert on_disk["type"] == "osc_output"
        assert on_disk["name"] == "My Cue"
        assert on_disk["is_system"] is False
        assert on_disk["id"] and len(on_disk["id"]) == 32

    def test_explicit_id_preserved(self, tmp_path: Path) -> None:
        path = write_user_template(
            tmp_path,
            "osc_output",
            "x",
            _osc_payload(),
            template_id="my-stable-id",
        )
        assert json.loads(path.read_text())["id"] == "my-stable-id"

    def test_conflict_appends_dash_n(self, tmp_path: Path) -> None:
        write_user_template(tmp_path, "osc_output", "Cue", _osc_payload())
        write_user_template(tmp_path, "osc_output", "Cue", _osc_payload())
        third = write_user_template(tmp_path, "osc_output", "Cue", _osc_payload())
        assert (tmp_path / "user" / f"osc_output.cue{TEMPLATE_FILE_SUFFIX}").is_file()
        assert (tmp_path / "user" / f"osc_output.cue-1{TEMPLATE_FILE_SUFFIX}").is_file()
        assert third.name == f"osc_output.cue-2{TEMPLATE_FILE_SUFFIX}"
        assert json.loads(third.read_text())["name"] == "Cue (2)"

    def test_conflict_with_system_filename_skips(self, tmp_path: Path) -> None:
        sysdir = tmp_path / "system"
        sysdir.mkdir()
        (sysdir / f"osc_output.shared{TEMPLATE_FILE_SUFFIX}").write_text(
            json.dumps(_envelope(name="Shared", is_system=True)),
        )
        path = write_user_template(
            tmp_path,
            "osc_output",
            "Shared",
            _osc_payload(),
        )
        # Lands at ``-1``: the bare slug collides with a system filename.
        assert path.name == f"osc_output.shared-1{TEMPLATE_FILE_SUFFIX}"
        assert json.loads(path.read_text())["name"] == "Shared (1)"

    def test_unknown_type_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(TemplateWriteError, match="unknown template type"):
            write_user_template(tmp_path, "bogus", "x", {})

    def test_empty_name_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(TemplateWriteError, match="name must be a non-empty string"):
            write_user_template(tmp_path, "osc_output", "", _osc_payload())

    def test_non_str_name_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(TemplateWriteError, match="name must be a non-empty string"):
            write_user_template(tmp_path, "osc_output", 42, _osc_payload())  # type: ignore[arg-type]

    def test_invalid_payload_rejected_before_write(self, tmp_path: Path) -> None:
        with pytest.raises(TemplateValidationError, match="missing required key 'address'"):
            write_user_template(tmp_path, "osc_output", "x", {"args": []})
        # Nothing landed on disk.
        assert not (tmp_path / "user").exists() or not list(
            (tmp_path / "user").glob(f"*{TEMPLATE_FILE_SUFFIX}"),
        )

    def test_atomic_create_skips_externally_filled_slots(
        self,
        tmp_path: Path,
    ) -> None:
        userdir = tmp_path / "user"
        userdir.mkdir(parents=True, exist_ok=True)
        bare = userdir / f"osc_output.cue{TEMPLATE_FILE_SUFFIX}"
        bare.write_text("not our file", encoding="utf-8")
        path = write_user_template(
            tmp_path,
            "osc_output",
            "Cue",
            _osc_payload(),
        )
        assert path.name == f"osc_output.cue-1{TEMPLATE_FILE_SUFFIX}"
        # Atomic create refused to clobber the pre-existing file.
        assert bare.read_text() == "not our file"

    def test_conflict_loop_ceiling_surfaces_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exhausting the conflict-resolution ceiling raises
        ``TemplateWriteError`` instead of spinning forever; verified by
        lowering ``_CONFLICT_NUMBER_MAX`` and filling its slots."""
        from openfollow.templates import writer as writer_mod

        monkeypatch.setattr(writer_mod, "_CONFLICT_NUMBER_MAX", 3)
        # Fill all 3 slots (bare slug + -1 + -2) so the loop runs out at -3.
        userdir = tmp_path / "user"
        userdir.mkdir(parents=True, exist_ok=True)
        for n in range(3):
            suffix = "" if n == 0 else f"-{n}"
            (userdir / f"osc_output.cue{suffix}{TEMPLATE_FILE_SUFFIX}").write_text("{}")
        with pytest.raises(TemplateWriteError, match="could not find a free filename"):
            write_user_template(
                tmp_path,
                "osc_output",
                "Cue",
                _osc_payload(),
            )


class TestDeleteUserTemplate:
    def test_deletes_existing_file(self, tmp_path: Path) -> None:
        path = write_user_template(
            tmp_path,
            "osc_output",
            "x",
            _osc_payload(),
        )
        assert delete_user_template(tmp_path, path.name) is True
        assert not path.exists()

    def test_returns_false_for_missing(self, tmp_path: Path) -> None:
        (tmp_path / "user").mkdir()
        assert delete_user_template(tmp_path, "nope.oftemplate") is False

    def test_path_traversal_rejected(self, tmp_path: Path) -> None:
        # A ``filename`` resolving outside the user folder is refused
        # even when the target file exists.
        sysdir = tmp_path / "system"
        sysdir.mkdir()
        (sysdir / f"osc_output.x{TEMPLATE_FILE_SUFFIX}").write_text(
            json.dumps(_envelope()),
        )
        with pytest.raises(TemplateWriteError, match="refusing to delete"):
            delete_user_template(
                tmp_path,
                f"../system/osc_output.x{TEMPLATE_FILE_SUFFIX}",
            )
        assert (sysdir / f"osc_output.x{TEMPLATE_FILE_SUFFIX}").is_file()


# ---------------------------------------------------------------------------
# Bootstrap (mirrors bundled system/ files into <root>/system/)
# ---------------------------------------------------------------------------


class TestBootstrap:
    def test_seeds_bundled_templates(self, tmp_path: Path) -> None:
        n = seed_system_templates(tmp_path)
        assert n == 4
        sysdir = tmp_path / "system"
        files = sorted(p.name for p in sysdir.glob(f"*{TEMPLATE_FILE_SUFFIX}"))
        assert files == [
            "osc_output.adm-osc-3d.oftemplate",
            "osc_output.adm-osc.oftemplate",
            "osc_output.dnb-absolute.oftemplate",
            "osc_output.etc-eos.oftemplate",
        ]

    def test_seeded_files_load_cleanly(self, tmp_path: Path) -> None:
        seed_system_templates(tmp_path)
        loaded = list_templates(tmp_path)
        assert len(loaded) == 4
        for entry in loaded:
            assert entry.error == "", entry.error
            assert entry.is_system is True
            assert entry.template is not None
            assert entry.template.type == "osc_output"

    def test_idempotent_overwrite(self, tmp_path: Path) -> None:
        # A re-seed restores a mutated file.
        seed_system_templates(tmp_path)
        target = tmp_path / "system" / "osc_output.adm-osc.oftemplate"
        target.write_text(json.dumps(_envelope(name="Hacked", is_system=True)))
        seed_system_templates(tmp_path)
        on_disk = json.loads(target.read_text())
        assert on_disk["name"] == "ADM-OSC 2D"

    def test_prunes_stale_system_templates(self, tmp_path: Path) -> None:
        # Mirror semantics: anything in the on-disk system folder no
        # longer in the bundled set is pruned (else removed/renamed
        # templates would linger across upgrades).
        sysdir = tmp_path / "system"
        sysdir.mkdir(parents=True, exist_ok=True)
        stale = sysdir / "osc_output.removed-in-v2.oftemplate"
        stale.write_text(
            json.dumps(_envelope(name="Removed in v2", is_system=True)),
        )
        seed_system_templates(tmp_path)
        assert not stale.exists()
        assert (sysdir / "osc_output.adm-osc.oftemplate").is_file()

    def test_prune_leaves_foreign_files_alone(self, tmp_path: Path) -> None:
        # Prune only touches template-suffix files; foreign files are
        # left alone.
        sysdir = tmp_path / "system"
        sysdir.mkdir(parents=True, exist_ok=True)
        foreign = sysdir / "README.md"
        foreign.write_text("operator notes")
        seed_system_templates(tmp_path)
        assert foreign.is_file()

    def test_does_not_touch_user_folder(self, tmp_path: Path) -> None:
        user = tmp_path / "user"
        user.mkdir()
        keep = user / f"osc_output.mine{TEMPLATE_FILE_SUFFIX}"
        keep.write_text(json.dumps(_envelope(name="Mine")))
        seed_system_templates(tmp_path)
        assert keep.is_file()
        assert json.loads(keep.read_text())["name"] == "Mine"


class TestZonesPayloadValidatorObjectGuard:
    """The zones validator must reject a non-list 'zones' or any
    non-object element so a crafted/imported template can't persist a bare
    str/int that crashes the zone-eval thread on apply."""

    def test_non_list_zones_rejected(self) -> None:
        from openfollow.templates.schema import TemplateValidationError, validate_payload

        with pytest.raises(TemplateValidationError, match="must be a list"):
            validate_payload("zones", {"zones": "abc"})

    @pytest.mark.parametrize("bad", ["evil", 123, 1.5, ["nested"], None])
    def test_non_object_zone_element_rejected(self, bad: object) -> None:
        from openfollow.templates.schema import TemplateValidationError, validate_payload

        with pytest.raises(TemplateValidationError, match="must be object"):
            validate_payload("zones", {"zones": [bad]})

    def test_valid_object_zones_accepted(self) -> None:
        from openfollow.templates.schema import validate_payload

        # A list of dicts passes (empty/partial zone dicts are coerced by the
        # dataclass); no exception is the assertion.
        validate_payload("zones", {"zones": [{"name": "Z1", "vertices": []}]})
        validate_payload("zones", {"zones": []})


# ---------------------------------------------------------------------------
# Legacy suffix (read-only transition)
# ---------------------------------------------------------------------------


class TestLegacySuffix:
    def test_loader_reads_legacy_suffix_file(self, tmp_path: Path) -> None:
        user = tmp_path / "user"
        user.mkdir()
        legacy = user / f"osc_output.old{TEMPLATE_LEGACY_SUFFIX}"
        legacy.write_text(json.dumps(_envelope(name="From an older build")))
        entries = list_templates(tmp_path)
        assert len(entries) == 1
        assert entries[0].template is not None
        assert entries[0].template.name == "From an older build"
        assert entries[0].filename.endswith(TEMPLATE_LEGACY_SUFFIX)

    def test_find_template_resolves_legacy_suffix(self, tmp_path: Path) -> None:
        user = tmp_path / "user"
        user.mkdir()
        name = f"osc_output.old{TEMPLATE_LEGACY_SUFFIX}"
        (user / name).write_text(json.dumps(_envelope(name="Old")))
        found = find_template(tmp_path, name)
        assert found is not None and found.template is not None
        assert found.template.name == "Old"

    def test_bootstrap_prunes_stale_legacy_system_file(self, tmp_path: Path) -> None:
        # A system default carried over from before the suffix rename isn't
        # in the bundled .oftemplate set, so seeding prunes it – the folder
        # never ends up with two copies of the same default.
        sysdir = tmp_path / "system"
        sysdir.mkdir(parents=True, exist_ok=True)
        stale = sysdir / f"osc_output.adm-osc{TEMPLATE_LEGACY_SUFFIX}"
        stale.write_text(json.dumps(_envelope(name="ADM-OSC 2D", is_system=True)))
        seed_system_templates(tmp_path)
        assert not stale.exists()
        assert (sysdir / "osc_output.adm-osc.oftemplate").is_file()

    def test_writer_only_emits_canonical_suffix(self, tmp_path: Path) -> None:
        path = write_user_template(tmp_path, "osc_output", "New", _osc_payload())
        assert path.name.endswith(TEMPLATE_FILE_SUFFIX)
        assert not path.name.endswith(TEMPLATE_LEGACY_SUFFIX)

    def test_save_disambiguates_against_legacy_suffix_collision(self, tmp_path: Path) -> None:
        # An upgraded install with a legacy file of the same slug must not get a
        # second, identically-named .oftemplate beside it: the conflict loop
        # bumps to -1 so the operator sees "Foo (1)", not two "Foo" rows.
        user = tmp_path / "user"
        user.mkdir()
        (user / f"osc_output.foo{TEMPLATE_LEGACY_SUFFIX}").write_text(json.dumps(_envelope(name="Foo")))
        path = write_user_template(tmp_path, "osc_output", "Foo", _osc_payload())
        assert path.name == f"osc_output.foo-1{TEMPLATE_FILE_SUFFIX}"
        assert json.loads(path.read_text())["name"] == "Foo (1)"

    def test_save_disambiguates_against_legacy_system_collision(self, tmp_path: Path) -> None:
        # Same, but the colliding legacy file is a bundled system template.
        system = tmp_path / "system"
        system.mkdir()
        (system / f"osc_output.foo{TEMPLATE_LEGACY_SUFFIX}").write_text(
            json.dumps(_envelope(name="Foo", is_system=True))
        )
        path = write_user_template(tmp_path, "osc_output", "Foo", _osc_payload())
        assert path.name == f"osc_output.foo-1{TEMPLATE_FILE_SUFFIX}"


# ---------------------------------------------------------------------------
# app_version provenance (diagnostics-only)
# ---------------------------------------------------------------------------


class TestAppVersion:
    def test_save_stamps_current_build(self, tmp_path: Path) -> None:
        import openfollow

        path = write_user_template(tmp_path, "osc_output", "X", _osc_payload())
        assert json.loads(path.read_text())["app_version"] == openfollow.__version__

    def test_explicit_app_version_preserved(self, tmp_path: Path) -> None:
        # The import path passes the originating build's version through so
        # provenance survives the round-trip instead of being overwritten
        # with this build's version.
        path = write_user_template(tmp_path, "osc_output", "X", _osc_payload(), app_version="9.9.9")
        assert json.loads(path.read_text())["app_version"] == "9.9.9"

    def test_app_version_never_gates_load(self, tmp_path: Path) -> None:
        # A wildly-newer app_version is informational only – the file still
        # loads as long as the format version and payload are valid.
        tpl = parse_envelope(_envelope(app_version="999.0.0"))
        assert tpl.app_version == "999.0.0"

    def test_missing_app_version_defaults_empty(self) -> None:
        tpl = parse_envelope(_envelope())  # _envelope() carries no app_version
        assert tpl.app_version == ""

    def test_to_dict_includes_app_version(self) -> None:
        tpl = OpenFollowTemplate(**_envelope(app_version="1.2.3"))
        assert tpl.to_dict()["app_version"] == "1.2.3"

    @pytest.mark.parametrize("bad", [123, None, True, ["1.0"]])
    def test_non_str_app_version_coerced_empty(self, bad: object) -> None:
        # A hand-edited / malformed app_version must never raise – it's
        # diagnostics-only, so a non-string is coerced to "" rather than
        # rejecting an otherwise-valid template.
        tpl = OpenFollowTemplate(**_envelope(app_version=bad))
        assert tpl.app_version == ""


# ---------------------------------------------------------------------------
# parse_envelope + cross-version handling
# ---------------------------------------------------------------------------


class TestParseEnvelope:
    def test_round_trips_valid_dict(self) -> None:
        tpl = parse_envelope(_envelope(name="RoundTrip"))
        assert tpl.name == "RoundTrip"
        assert tpl.type == "osc_output"

    def test_newer_format_version_rejected_with_message(self) -> None:
        with pytest.raises(TemplateValidationError, match="unsupported version"):
            parse_envelope(_envelope(version=TEMPLATE_VERSION + 1))

    def test_unknown_envelope_key_dropped_not_crashed(self) -> None:
        # A future envelope field is filtered out so construction doesn't
        # raise TypeError; the template still loads under the same version.
        tpl = parse_envelope(_envelope(future_field="whatever"))
        assert tpl.type == "osc_output"


# ---------------------------------------------------------------------------
# Backward-compatibility contract (old payload → newer build)
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    def test_old_payload_missing_field_fills_default(self) -> None:
        # A camera_grid template authored before a CameraConfig field
        # existed must still load – the dataclass fills the default rather
        # than rejecting. Model "older" by dropping ``fov`` from the camera
        # dict and assert the reconstructed config uses the default.
        from dataclasses import asdict

        from openfollow.configuration import CameraConfig, GridConfig

        camera = asdict(CameraConfig())
        camera.pop("fov")
        payload = {"camera": camera, "grid": asdict(GridConfig())}
        tpl = parse_envelope(
            {
                "version": TEMPLATE_VERSION,
                "type": "camera_grid",
                "name": "Old rig",
                "payload": payload,
            }
        )
        cam = CameraConfig(**tpl.payload["camera"])
        assert cam.fov == CameraConfig().fov
