# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit-level coverage for pure helpers in :mod:`openfollow.web.routes`.

Complements :mod:`tests.test_web_helpers` (the large happy-path
apply/get-section matrix) and :mod:`tests.test_web_server` (integration
routes against a real Bottle app) by walking the remaining edge
branches in the coercion + validation helpers that:

* Can be driven without a ``bottle.Bottle`` instance or a ``request`` /
  ``response`` proxy (no HTTP path needed).
* The happy-path integration suite never reaches (malformed import
  payloads, unsupported OverflowError types, wizard camera edge inputs).

Targeted helpers:

* ``_as_float`` / ``_as_optional_float`` / ``_as_int`` / ``_as_positive_int``
  OverflowError + None / blank-string branches.
* ``_as_int_list`` ValueError on malformed CSV + tuple input.
* ``_parse_vertices`` malformed entries (non-list root, short pair,
  non-numeric coord).
* ``_apply_zone_fields`` vertex/field interaction + ``__post_init__``
  re-run.
* ``_wizard_camera_params`` error surface (non-dict, missing key,
  non-numeric value) – each raises a distinct exception that the
  ``/api/wizard/*`` routes translate to HTTP 400.
* ``_import_needs_restart`` section-change detection matrix.
* ``_apply_import_data`` skip-restart-sections branch (detection /
  video_source / OTP / RTTrPM preserved when a non-restart import is
  applied).

``_is_private_peer_ip`` and ``_load_json_body`` already have parametrised
coverage in :mod:`tests.test_web_helpers`; we rely on those rather than
duplicating here.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python 3.10 – project floor is >=3.10 (tomllib is 3.11+)
    import tomli as tomllib  # type: ignore[no-redef]

import numpy as np
import pytest

import openfollow.web.routes as routes_module
from openfollow.configuration import AppConfig, DetectionConfig, DetectionMaskConfig, TriggerZoneConfig
from openfollow.web.routes import (
    _apply_import_data,
    _apply_mask_fields,
    _apply_zone_fields,
    _as_float,
    _as_float_or_zero,
    _as_int,
    _as_int_list,
    _as_optional_float,
    _as_positive_int,
    _config_to_toml,
    _find_by_id,
    _import_needs_restart,
    _parse_vertices,
    _swap_for_direction,
    _wizard_camera_params,
    apply_section_data,
    strip_device_local_fields,
)

pytestmark = pytest.mark.unit


class _Item:
    """Minimal ``.id``-bearing stand-in for the id-keyed config rows."""

    def __init__(self, item_id: str) -> None:
        self.id = item_id


class TestFindById:
    def test_returns_index_and_item_on_match(self) -> None:
        items = [_Item("a"), _Item("b"), _Item("c")]
        found = _find_by_id(items, "b")
        assert found is not None
        idx, item = found
        assert idx == 1
        assert item is items[1]

    def test_returns_none_when_missing(self) -> None:
        assert _find_by_id([_Item("a")], "z") is None

    def test_returns_first_match(self) -> None:
        items = [_Item("dup"), _Item("dup")]
        found = _find_by_id(items, "dup")
        assert found is not None and found[0] == 0


class TestSwapForDirection:
    def test_up_swaps_with_predecessor(self) -> None:
        items = ["a", "b", "c"]
        assert _swap_for_direction(items, 1, "up") is True
        assert items == ["b", "a", "c"]

    def test_down_swaps_with_successor(self) -> None:
        items = ["a", "b", "c"]
        assert _swap_for_direction(items, 1, "down") is True
        assert items == ["a", "c", "b"]

    def test_top_up_is_noop_returns_false(self) -> None:
        items = ["a", "b"]
        assert _swap_for_direction(items, 0, "up") is False
        assert items == ["a", "b"]

    def test_bottom_down_is_noop_returns_false(self) -> None:
        items = ["a", "b"]
        assert _swap_for_direction(items, 1, "down") is False
        assert items == ["a", "b"]

    def test_unknown_direction_is_noop_returns_false(self) -> None:
        items = ["a", "b"]
        assert _swap_for_direction(items, 0, "sideways") is False
        assert items == ["a", "b"]


# --------------------------------------------------------------------------- #
# _as_float / _as_optional_float / _as_int / _as_positive_int
# --------------------------------------------------------------------------- #


class TestAsFloatEdgeCases:
    def test_overflow_from_huge_int_falls_back_to_default(self) -> None:
        """``float(10**5000)`` raises ``OverflowError`` – the helper
        must catch it so a crafted JSON payload can't 500 the request.
        """
        assert _as_float(10**5000, default=42.0) == 42.0

    def test_typeerror_from_object_falls_back_to_default(self) -> None:
        assert _as_float(object(), default=1.5) == 1.5

    def test_none_falls_back_to_default(self) -> None:
        assert _as_float(None, default=7.5) == 7.5

    def test_valid_numeric_string_is_parsed(self) -> None:
        assert _as_float("3.14", default=0.0) == pytest.approx(3.14)


class TestAsFloatOrZero:
    """``_as_float_or_zero`` has a three-way contract for
    ``grid.max_height``:

    - ``None`` / empty / whitespace-only string → ``0.0`` (operator's
      intentional "clear" – disables ``[fz]`` / ``[ifz]``).
    - Valid finite number → that number.
    - Anything else (non-numeric, overflow, ``inf`` / ``nan``) →
      preserve the ``default`` (previous value). Matches every
      other coerce helper's "bad input → preserve current" rule
      so a crafted payload like ``{"max_height": "tall"}`` cannot
      silently disable every binding by collapsing to ``0.0``.
    """

    def test_none_collapses_to_zero(self) -> None:
        assert _as_float_or_zero(None, default=4.0) == 0.0

    def test_empty_string_collapses_to_zero(self) -> None:
        assert _as_float_or_zero("", default=4.0) == 0.0

    def test_blank_string_collapses_to_zero(self) -> None:
        assert _as_float_or_zero("   ", default=4.0) == 0.0

    def test_non_numeric_string_preserves_default(self) -> None:
        """Malformed payload preserves the previous value instead of
        silently zeroing."""
        assert _as_float_or_zero("tall", default=4.0) == 4.0

    def test_overflow_preserves_default(self) -> None:
        assert _as_float_or_zero(10**5000, default=4.0) == 4.0

    def test_inf_string_preserves_default(self) -> None:
        """``float("1e5000")`` overflows silently to ``inf`` – the
        helper's finiteness check rejects it so the previous value
        is kept rather than disabling every ``[fz]`` row."""
        assert _as_float_or_zero("1e5000", default=4.0) == 4.0
        assert _as_float_or_zero("inf", default=4.0) == 4.0
        assert _as_float_or_zero("-inf", default=4.0) == 4.0

    def test_nan_string_preserves_default(self) -> None:
        assert _as_float_or_zero("nan", default=4.0) == 4.0

    def test_valid_number_is_parsed(self) -> None:
        assert _as_float_or_zero("3.5", default=4.0) == pytest.approx(3.5)
        assert _as_float_or_zero(7, default=4.0) == pytest.approx(7.0)

    def test_zero_input_stays_zero(self) -> None:
        """Distinct from 'preserve current' – explicit ``0`` is a
        valid finite number, so the parser returns ``0.0`` even when
        the prior value was non-zero."""
        assert _as_float_or_zero("0", default=4.0) == 0.0
        assert _as_float_or_zero(0.0, default=4.0) == 0.0


class TestAsOptionalFloat:
    def test_none_returns_none_regardless_of_default(self) -> None:
        assert _as_optional_float(None, default=5.0) is None

    def test_blank_string_returns_none(self) -> None:
        assert _as_optional_float("   ", default=5.0) is None

    def test_malformed_string_falls_back_to_default(self) -> None:
        assert _as_optional_float("abc", default=5.0) == 5.0

    def test_overflow_falls_back_to_default(self) -> None:
        assert _as_optional_float(10**5000, default=5.0) == 5.0

    def test_valid_number_is_returned_as_float(self) -> None:
        assert _as_optional_float(42, default=5.0) == 42.0


class TestAsIntEdgeCases:
    def test_float_inf_raises_overflow_and_returns_default(self) -> None:
        """``int(float('inf'))`` raises ``OverflowError`` – catching it
        protects the request handler from 500ing on JSON ``1e400``.
        """
        assert _as_int(float("inf"), default=10) == 10

    def test_none_falls_back_to_default(self) -> None:
        assert _as_int(None, default=3) == 3

    def test_valid_string_parses(self) -> None:
        assert _as_int("42", default=0) == 42


class TestAsOptionalInt:
    """``_as_optional_int`` distinguishes "operator cleared the input"
    (``None``) from "operator typed something invalid" (default), since
    the underlying field is :class:`int | None` and the two states have
    different semantics in the runtime."""

    def test_none_returns_none_regardless_of_default(self) -> None:
        from openfollow.web.routes import _as_optional_int

        assert _as_optional_int(None, default=5) is None

    def test_empty_string_returns_none(self) -> None:
        from openfollow.web.routes import _as_optional_int

        assert _as_optional_int("", default=5) is None

    def test_whitespace_string_returns_none(self) -> None:
        from openfollow.web.routes import _as_optional_int

        assert _as_optional_int("   ", default=5) is None

    def test_valid_int_returns_int(self) -> None:
        from openfollow.web.routes import _as_optional_int

        assert _as_optional_int("3", default=None) == 3
        assert _as_optional_int(0, default=None) == 0
        assert _as_optional_int(7, default=None) == 7

    def test_malformed_string_falls_back_to_default(self) -> None:
        from openfollow.web.routes import _as_optional_int

        assert _as_optional_int("abc", default=5) == 5
        assert _as_optional_int("abc", default=None) is None

    def test_float_inf_overflows_and_falls_back(self) -> None:
        from openfollow.web.routes import _as_optional_int

        assert _as_optional_int(float("inf"), default=5) == 5

    def test_bool_collapses_to_none(self) -> None:
        """``bool`` is an ``int`` subclass, so a crafted POST with
        ``default_fader=true`` would otherwise silently save fader 1.
        ``OscTransmitterConfig.__post_init__`` already collapses bool
        inputs to ``None``; the web parser agrees so save-time and
        POST-time coercion stay in lockstep."""
        from openfollow.web.routes import _as_optional_int

        assert _as_optional_int(True, default=5) is None
        assert _as_optional_int(False, default=5) is None


class TestAsPositiveInt:
    def test_zero_clamps_to_one(self) -> None:
        """Used for fields that will be divided by the value (e.g.
        ``eval_fps``) – must never return 0 or the handler would
        ZeroDivisionError downstream.
        """
        assert _as_positive_int(0, default=5) == 1

    def test_negative_clamps_to_one(self) -> None:
        assert _as_positive_int(-10, default=5) == 1

    def test_valid_positive_passes_through(self) -> None:
        assert _as_positive_int(30, default=5) == 30

    def test_malformed_falls_back_then_clamps(self) -> None:
        """Malformed value → default. If the default is also < 1, the
        final clamp still guarantees ``>= 1``.
        """
        assert _as_positive_int("abc", default=0) == 1


# --------------------------------------------------------------------------- #
# _as_int_list
# --------------------------------------------------------------------------- #


class TestAsIntList:
    def test_malformed_csv_returns_default(self) -> None:
        """``"1,abc,3"`` – one non-int entry should revert the whole
        list to default rather than silently dropping ``abc``.  Operators
        editing config.toml by hand should see the old value preserved,
        not partially applied.
        """
        assert _as_int_list("1,abc,3", default=[99]) == [99]

    def test_tuple_input_coerced(self) -> None:
        """Covers line 509 branch – JSON arrays arrive as ``list``;
        hand-coded callers may pass ``tuple``.  Both should work.
        """
        assert _as_int_list((1, 2, 3), default=[0]) == [1, 2, 3]

    def test_list_with_non_int_returns_default(self) -> None:
        """Covers lines 512-514 – ``int("abc")`` raises ``ValueError``,
        ``int(None)`` raises ``TypeError``.  Either collapses the whole
        list to default.
        """
        assert _as_int_list([1, "abc", 3], default=[7]) == [7]
        assert _as_int_list([1, None, 3], default=[7]) == [7]

    def test_none_input_returns_default(self) -> None:
        assert _as_int_list(None, default=[1, 2]) == [1, 2]

    def test_non_string_non_iterable_returns_default(self) -> None:
        assert _as_int_list(42, default=[9]) == [9]

    def test_empty_csv_returns_default(self) -> None:
        assert _as_int_list("", default=[5]) == [5]


# --------------------------------------------------------------------------- #
# _parse_vertices / _apply_zone_fields
# --------------------------------------------------------------------------- #


class TestParseVertices:
    def test_non_list_preserves_current(self) -> None:
        """A malformed payload – e.g. ``"vertices": null`` – must not
        wipe the existing polygon.
        """
        current = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]
        assert _parse_vertices(None, current) == current
        assert _parse_vertices("oops", current) == current
        assert _parse_vertices(42, current) == current

    def test_valid_list_of_pairs_is_coerced_to_floats(self) -> None:
        assert _parse_vertices([[0, 0], [1, 0], [1, 1]], current=[]) == [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]

    def test_entries_under_two_elements_are_dropped(self) -> None:
        assert _parse_vertices([[0, 0], [1]], current=[]) == [[0.0, 0.0]]

    def test_tuple_entries_are_accepted_same_as_lists(self) -> None:
        assert _parse_vertices([(0, 0), (1, 2)], current=[]) == [[0.0, 0.0], [1.0, 2.0]]

    def test_non_numeric_coord_is_dropped(self) -> None:
        assert _parse_vertices([[0, 0], ["a", "b"], [2, 2]], current=[]) == [[0.0, 0.0], [2.0, 2.0]]

    def test_extra_columns_past_two_are_truncated(self) -> None:
        """A three-tuple ``[x, y, z]`` should still produce a 2-D
        vertex – the extra dim is silently dropped.  This keeps the
        web UI tolerant of clients that send annotated payloads.
        """
        assert _parse_vertices([[1, 2, 3], [4, 5, 6]], current=[]) == [[1.0, 2.0], [4.0, 5.0]]

    def test_empty_list_returns_empty_not_current(self) -> None:
        """Explicit empty list is NOT the malformed-payload case – the
        caller really is clearing the polygon.
        """
        assert _parse_vertices([], current=[[1, 1]]) == []


class TestApplyZoneFields:
    def test_updates_field_and_reruns_post_init(self) -> None:
        """Covers the ``zone.__post_init__`` re-run at the end of
        ``_apply_zone_fields`` – otherwise a hand-crafted payload could
        set ``trigger_source="everything"`` which the dataclass
        normally rejects at construction.
        """
        zone = TriggerZoneConfig(
            name="Z1",
            color="#ff0000",
            trigger_source="markers",
            enabled=True,
            vertices=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]],
        )
        _apply_zone_fields(zone, {"name": "Z1 renamed", "trigger_source": "both"})
        assert zone.name == "Z1 renamed"
        assert zone.trigger_source == "both"

    def test_vertices_update_runs_through_parse_vertices(self) -> None:
        zone = TriggerZoneConfig()
        _apply_zone_fields(zone, {"vertices": [[0, 0], [1, 0], [2, 0]]})
        assert zone.vertices == [[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]]

    def test_invalid_trigger_source_is_normalised_by_post_init(self) -> None:
        """``__post_init__`` snaps unknown trigger_source values to a
        safe default – covers the guard against a crafted payload.
        """
        zone = TriggerZoneConfig()
        _apply_zone_fields(zone, {"trigger_source": "garbage"})
        # Post-init must have rewritten it to an allowed value.
        assert zone.trigger_source in ("markers", "detection", "both")


class TestApplyMaskFields:
    def test_updates_name_enabled_and_vertices(self) -> None:
        mask = DetectionMaskConfig(name="M0", enabled=True)
        _apply_mask_fields(mask, {"name": "Stage", "enabled": False, "vertices": [[0, 0], [1, 0], [1, 1]]})
        assert mask.name == "Stage"
        assert mask.enabled is False
        assert mask.vertices == [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]

    def test_absent_keys_are_left_untouched(self) -> None:
        """A partial PUT toggling ``enabled`` must not wipe the polygon."""
        mask = DetectionMaskConfig(name="Keep", vertices=[[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]])
        _apply_mask_fields(mask, {"enabled": False})
        assert mask.enabled is False
        assert mask.name == "Keep"
        assert mask.vertices == [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]]

    def test_post_init_drops_non_finite_vertices(self) -> None:
        """The ``__post_init__`` re-run scrubs a crafted non-finite vertex."""
        mask = DetectionMaskConfig()
        _apply_mask_fields(mask, {"vertices": [[0.0, 0.0], [float("nan"), 0.5], [1.0, 1.0]]})
        assert mask.vertices == [[0.0, 0.0], [1.0, 1.0]]

    def test_null_vertices_preserves_existing(self) -> None:
        """``{"vertices": null}`` must not destructively blank the polygon."""
        mask = DetectionMaskConfig(vertices=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]])
        _apply_mask_fields(mask, {"vertices": None})
        assert mask.vertices == [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]


# --------------------------------------------------------------------------- #
# _wizard_camera_params – error matrix (maps to HTTP 400 at the route layer)
# --------------------------------------------------------------------------- #


class TestWizardCameraParams:
    def test_non_dict_raises_typeerror(self) -> None:
        with pytest.raises(TypeError):
            _wizard_camera_params("not a dict")

    def test_missing_key_raises_keyerror(self) -> None:
        bad = {"pos_x": 0, "pos_y": 0, "pos_z": 0, "pitch": 0, "yaw": 0, "roll": 0}
        with pytest.raises(KeyError):
            _wizard_camera_params(bad)

    def test_non_numeric_value_raises_valueerror(self) -> None:
        with pytest.raises((TypeError, ValueError)):
            _wizard_camera_params(
                {
                    "pos_x": "abc",
                    "pos_y": 0,
                    "pos_z": 0,
                    "pitch": 0,
                    "yaw": 0,
                    "roll": 0,
                    "fov": 60,
                }
            )

    def test_valid_input_returns_float64_vector(self) -> None:
        cam = {
            "pos_x": 1.0,
            "pos_y": 2.0,
            "pos_z": 3.0,
            "pitch": 4.0,
            "yaw": 5.0,
            "roll": 6.0,
            "fov": 60.0,
        }
        vec = _wizard_camera_params(cam)
        assert vec.dtype == np.float64
        assert vec.tolist() == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 60.0]


# --------------------------------------------------------------------------- #
# _is_private_peer_ip – LAN/public matrix lives in tests/test_web_helpers.py.
# Only the IPv6 loopback edge case was new here; it has been folded into the
# existing parametrized test to keep the SSRF policy in one place.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# _import_needs_restart – section-change matrix
# --------------------------------------------------------------------------- #


class TestImportNeedsRestart:
    def test_no_changes_returns_empty(self) -> None:
        cfg = AppConfig()
        assert _import_needs_restart(cfg, cfg) == []

    def test_video_source_change_not_flagged(self) -> None:
        """Video source / per-plugin field changes apply live via the
        ``swap_video`` orchestrator. The import flow must not flag a
        restart for video-only changes – the user gets a live-applied
        pipeline rebuild."""
        old = AppConfig()
        new = replace(old, video_source_type="ndi")
        assert _import_needs_restart(old, new) == []

    def test_plugin_field_change_not_flagged(self) -> None:
        """Per-plugin fields (e.g. ``rtsp_url``) also live-swap;
        ``Plugin.config_changed(old, new)`` drives the dispatcher
        but no restart is needed."""
        old = AppConfig(video_source_type="rtsp", rtsp_url="rtsp://a:554/x")
        new = AppConfig(video_source_type="rtsp", rtsp_url="rtsp://b:554/y")
        assert _import_needs_restart(old, new) == []

    def test_detection_off_to_on_not_flagged(self) -> None:
        """Enabling detection applies live via ``swap_detector``, which
        rebuilds the receiver pipeline with a fresh detection branch.
        The import flow must NOT flag a restart for this case."""
        old = AppConfig()
        new = replace(old, detection=replace(old.detection, enabled=True))
        assert _import_needs_restart(old, new) == []

    def test_detection_inference_size_change_not_flagged_when_enabled(self) -> None:
        """``inference_size`` change while detection is enabled applies
        live via ``swap_detector`` – the orchestrator constructs a fresh
        detector and rebuilds the pipeline so the appsink caps match the
        new ``input_resolution``."""
        old = AppConfig(detection=DetectionConfig(enabled=True, inference_size=320))
        new = AppConfig(detection=DetectionConfig(enabled=True, inference_size=640))
        assert _import_needs_restart(old, new) == []

    def test_detection_inference_size_change_not_flagged_when_disabled(self) -> None:
        """An ``inference_size`` change while detection is disabled
        has no runtime effect (the appsink pipeline isn't running),
        so the dispatcher doesn't even take any action – and
        ``_import_needs_restart`` must not over-report."""
        old = AppConfig(detection=DetectionConfig(enabled=False, inference_size=320))
        new = AppConfig(detection=DetectionConfig(enabled=False, inference_size=640))
        assert _import_needs_restart(old, new) == []

    def test_detection_running_change_not_flagged(self) -> None:
        """An on→on detection edit (e.g. ``confidence`` change) routes
        through the orchestrator and does NOT need a restart, so the
        import flow must not flag it.
        """
        old = AppConfig(detection=DetectionConfig(enabled=True, confidence=0.5))
        new = AppConfig(detection=DetectionConfig(enabled=True, confidence=0.8))
        assert _import_needs_restart(old, new) == []

    def test_detection_on_to_off_not_flagged(self) -> None:
        """Disabling detection is a live transition."""
        old = AppConfig(detection=DetectionConfig(enabled=True))
        new = AppConfig(detection=DetectionConfig(enabled=False))
        assert _import_needs_restart(old, new) == []

    def test_otp_output_change_not_flagged(self) -> None:
        """OTP output applies live via the orchestrator. The import
        flow must NOT flag a restart for OTP-only changes – the user
        gets a live-applied import."""
        old = AppConfig()
        new = replace(old, otp_output=replace(old.otp_output, enabled=True))
        assert _import_needs_restart(old, new) == []

    def test_rttrpm_output_change_not_flagged(self) -> None:
        """Same as OTP."""
        old = AppConfig()
        new = replace(old, rttrpm_output=replace(old.rttrpm_output, enabled=True))
        assert _import_needs_restart(old, new) == []

    def test_camera_change_is_not_flagged(self) -> None:
        """Camera edits are live-reloadable – must NOT surface as a
        restart reason.
        """
        old = AppConfig()
        new = replace(old, camera=replace(old.camera, fov=75.0))
        assert _import_needs_restart(old, new) == []


# --------------------------------------------------------------------------- #
# _apply_import_data – skip-restart-sections branch
# --------------------------------------------------------------------------- #


class TestApplyImportDataSkipRestart:
    def test_skip_restart_still_applies_every_section(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Every section (incl. detection) applies live now, so
        ``skip_restart_sections=True`` no longer gates any section. The
        flag is preserved on the API for backwards compatibility with
        callers but doesn't change the outcome – a ``skip_restart=1``
        import lands the full payload.
        """
        # ``apply_section_data("video_source", ...)`` imports
        # ``get_available_input_ids`` lazily from ``openfollow.video.inputs``
        # – the real helper triggers plugin auto-discovery (NDI probes
        # library paths, etc).  Patch the module attribute to a
        # deterministic list so this unit test stays hermetic and
        # doesn't depend on which plugins happen to auto-register.
        import openfollow.video.inputs as inputs_module

        monkeypatch.setattr(
            inputs_module,
            "get_available_input_ids",
            lambda: ["rtsp", "ndi"],
        )

        current = AppConfig(video_source_type="rtsp")
        assert current.video_source_type == "rtsp"

        payload = {
            "video_source_type": "ndi",
            "camera": {"fov": 75.0},
            "detection": {"enabled": True},
            "otp_output": {"enabled": True},
            "rttrpm_output": {"enabled": True},
        }
        out = _apply_import_data(current, payload, skip_restart_sections=True)

        assert out.camera.fov == 75.0
        assert out.otp_output.enabled is True
        assert out.rttrpm_output.enabled is True
        assert out.video_source_type == "ndi"
        # Detection now applies even with skip_restart_sections=True
        # (every detection edit is live).
        assert out.detection.enabled is True

    def test_non_skip_applies_every_section(self) -> None:
        """Without ``skip_restart_sections`` every section applies –
        symmetric to the skip path now that no section is gated.
        """
        current = AppConfig()
        out = _apply_import_data(
            current,
            {"detection": {"enabled": True}},
            skip_restart_sections=False,
        )
        assert out.detection.enabled is True

    def test_preserves_psn_source_iface_regardless_of_payload(self) -> None:
        """``psn_source_iface`` is device-specific (the NIC name to
        bind to). The import must NEVER overwrite it, even on a non-skip
        import – pointing this device at an iface name that may not even
        exist locally would send PSN binding through the auto-detect
        fallback on the next restart.
        """
        current = replace(AppConfig(), psn_source_iface="eth0")
        out = _apply_import_data(
            current,
            {"psn_source_iface": "wlan0"},
        )
        assert out.psn_source_iface == "eth0"

    def test_trigger_zones_list_imports_atomically(self) -> None:
        """The zones list replaces the existing zones entirely – an
        empty list clears them, a populated list uses the new set.
        """
        current = AppConfig()
        zones_payload = [
            {"name": "Zone A", "vertices": [[0, 0], [1, 0], [1, 1], [0, 1]]},
        ]
        out = _apply_import_data(
            current,
            {"trigger_zones": {"enabled": True, "zones": zones_payload}},
        )
        assert len(out.trigger_zones.zones) == 1
        assert out.trigger_zones.zones[0].name == "Zone A"

    def test_malformed_zones_entry_is_skipped(self) -> None:
        """A non-dict entry in the zones list must be skipped without
        raising – a crafted payload shouldn't crash the import.
        """
        current = AppConfig()
        out = _apply_import_data(
            current,
            {
                "trigger_zones": {
                    "enabled": True,
                    "zones": [
                        "garbage",  # not a dict – skipped
                        {"name": "Valid", "vertices": [[0, 0], [1, 0], [1, 1]]},
                    ],
                },
            },
        )
        assert len(out.trigger_zones.zones) == 1
        assert out.trigger_zones.zones[0].name == "Valid"

    def test_osc_routing_blocks_without_inner_lists_are_skipped(self) -> None:
        """An ``osc_transmitters`` / ``osc_destinations`` block whose inner
        list key is absent or non-list is skipped, not applied – a crafted
        payload can't crash the import or wipe the existing rows."""
        current = AppConfig()
        out = _apply_import_data(
            current,
            {
                "osc_transmitters": {},  # no "transmitters" list → skipped
                "osc_destinations": {"destinations": "nope"},  # not a list → skipped
            },
        )
        assert out.osc_transmitters.transmitters == []

    def test_window_dimensions_imported_as_ints(self) -> None:
        current = AppConfig()
        out = _apply_import_data(current, {"window_width": 1920, "window_height": 1080})
        assert out.window_width == 1920
        assert out.window_height == 1080

    def test_web_pin_not_overwritten_by_import(self) -> None:
        # web_pin is device-local (login credential): an imported config must
        # not rewrite this station's PIN.
        current = AppConfig(web_pin="9999")
        out = _apply_import_data(current, {"web_pin": "1234"})
        assert out.web_pin == "9999"

    def test_web_port_not_overwritten_by_import(self) -> None:
        # web_port is device-local (local bind): import preserves it.
        current = AppConfig(web_port=8080)
        out = _apply_import_data(current, {"web_port": 1234})
        assert out.web_port == 8080


# --------------------------------------------------------------------------- #
# apply_section_data – video recovery timers (stall_timeout / heal_interval)
# --------------------------------------------------------------------------- #


class TestVideoRecoveryTimerSection:
    def test_recovery_timers_parsed_and_clamped(self) -> None:
        """The video_source section parses ``stall_timeout`` /
        ``heal_interval`` (coerced to float, floored at 0)."""
        cfg = AppConfig(stall_timeout=3.0, heal_interval=5.0)
        changed = apply_section_data(
            cfg,
            "video_source",
            {"stall_timeout": "1.5", "heal_interval": "8"},
        )
        assert changed is True
        assert cfg.stall_timeout == 1.5
        assert cfg.heal_interval == 8.0

    def test_recovery_timers_floor_negative_to_zero(self) -> None:
        cfg = AppConfig(stall_timeout=3.0, heal_interval=5.0)
        apply_section_data(
            cfg,
            "video_source",
            {"stall_timeout": "-2", "heal_interval": "-1"},
        )
        assert cfg.stall_timeout == 0.0
        assert cfg.heal_interval == 0.0

    def test_recovery_timers_reject_non_finite(self) -> None:
        """A crafted ``inf``/``nan`` must NOT be saved – it would survive
        ``float()`` and later crash ``int(timeout*1000)`` in the watchdog.
        Non-finite input preserves the current value (matches AppConfig
        coercion, since apply_section_data doesn't re-run __post_init__)."""
        cfg = AppConfig(stall_timeout=3.0, heal_interval=5.0)
        apply_section_data(
            cfg,
            "video_source",
            {"stall_timeout": "inf", "heal_interval": "nan"},
        )
        assert cfg.stall_timeout == 3.0
        assert cfg.heal_interval == 5.0


# --------------------------------------------------------------------------- #
# apply_section_data – ``psn_source_iface``
# --------------------------------------------------------------------------- #


class TestPsnSourceIfaceSection:
    """The PSN form's network-source pin. The legacy raw-IP
    ``psn_source_ip`` was removed because operators left stale IPs in
    their configs and bricked the box on the next network change;
    pinning the stable interface name avoids that footgun."""

    def test_psn_section_accepts_iface(self) -> None:
        cfg = AppConfig(psn_source_iface="")
        apply_section_data(cfg, "psn", {"psn_source_iface": "eth0"})
        assert cfg.psn_source_iface == "eth0"

    def test_psn_section_strips_whitespace(self) -> None:
        """Mirrors AppConfig.__post_init__ – without stripping at the
        web-save path, the stored value desyncs from the runtime
        canonical form on every reload."""
        cfg = AppConfig(psn_source_iface="")
        apply_section_data(cfg, "psn", {"psn_source_iface": "  wlan0  "})
        assert cfg.psn_source_iface == "wlan0"

    def test_general_section_accepts_iface(self) -> None:
        """The General section reads/writes the same field – the
        Settings page legacy entry point must keep working."""
        cfg = AppConfig(psn_source_iface="")
        apply_section_data(cfg, "general", {"psn_source_iface": "eth0"})
        assert cfg.psn_source_iface == "eth0"

    def test_import_preserves_psn_source_iface(self) -> None:
        """Importing a config from another box must not clobber this
        device's iface pin: the iface name (and the local NIC it refers
        to) is device-specific."""
        local_cfg = AppConfig(psn_source_iface="eth0")
        imported = _apply_import_data(local_cfg, {"psn_source_iface": "different"})
        assert imported.psn_source_iface == "eth0"


# --------------------------------------------------------------------------- #
# ``_load_json_body`` malformed / null-literal / valid-object paths are all
# covered by ``tests/test_web_helpers.py`` (``test_load_json_body_*``). This
# file no longer duplicates them.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# _license_file_path / _read_license_text fallbacks
# --------------------------------------------------------------------------- #


class TestLicenseHelpers:
    """The /about page falls back gracefully when the bundled
    repo-root ``LICENSE`` isn't present (e.g. an installed wheel)."""

    def test_license_file_path_none_when_absent(self, monkeypatch) -> None:
        real_is_file = Path.is_file

        def fake_is_file(self: Path) -> bool:
            # Only the LICENSE lookup reports missing; leave every other
            # path probe (imports, stat calls, …) behaving normally.
            return False if self.name == "LICENSE" else real_is_file(self)

        monkeypatch.setattr(Path, "is_file", fake_is_file)
        assert routes_module._license_file_path() is None

    def test_read_license_text_none_when_no_file(self, monkeypatch) -> None:
        monkeypatch.setattr(routes_module, "_license_file_path", lambda: None)
        assert routes_module._read_license_text() is None

    def test_read_license_text_none_on_read_error(self, monkeypatch, tmp_path) -> None:
        # Point at a directory so ``read_text`` raises IsADirectoryError
        # (an OSError subclass) and the helper swallows it.
        monkeypatch.setattr(routes_module, "_license_file_path", lambda: tmp_path)
        assert routes_module._read_license_text() is None


class TestBundledDocResolution:
    def test_repo_root_candidate(self) -> None:
        # LICENSE ships at the repo root in a source checkout.
        assert routes_module._bundled_doc_path("LICENSE") is not None

    def test_none_when_absent(self) -> None:
        assert routes_module._bundled_doc_path("definitely-not-a-file.xyz") is None

    def test_share_dir_fallback(self, monkeypatch, tmp_path) -> None:
        (tmp_path / "NOTICES.md").write_text("x")
        monkeypatch.setattr(routes_module, "_SHARE_DOC_DIR", tmp_path)
        assert routes_module._bundled_doc_path("NOTICES.md") == tmp_path / "NOTICES.md"


class TestThirdPartyNotices:
    def test_none_when_no_file(self, monkeypatch) -> None:
        monkeypatch.setattr(routes_module, "_bundled_doc_path", lambda name: None)
        assert routes_module._third_party_notices_html() is None

    def test_none_on_read_error(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(routes_module, "_bundled_doc_path", lambda name: tmp_path)
        assert routes_module._third_party_notices_html() is None

    def test_renders_markdown(self, monkeypatch, tmp_path) -> None:
        doc = tmp_path / "THIRD_PARTY_NOTICES.md"
        doc.write_text("# Title\n\n| A | B |\n|---|---|\n| 1 | 2 |\n")
        monkeypatch.setattr(routes_module, "_bundled_doc_path", lambda name: doc)
        html = routes_module._third_party_notices_html()
        assert html is not None
        assert "<h1>Title</h1>" in html
        assert "<table" in html


# ---------------------------------------------------------------------------
# strip_device_local_fields
# ---------------------------------------------------------------------------


class TestStripDeviceLocalFields:
    """Device-local fields must NEVER cross machines via the peer-
    broadcast or full-config-import paths. Helper used at both ends
    (broadcaster-forward and peer-receive) so an older / out-of-date
    sender can't poison the receiver."""

    def test_psn_section_drops_source_iface(self) -> None:
        scrubbed = strip_device_local_fields(
            "psn",
            {
                "psn_system_name": "Stage A",
                "psn_mcast_ip": "236.10.10.10",
                "psn_source_iface": "eth0",
            },
        )
        assert "psn_source_iface" not in scrubbed
        # Non-device-local PSN fields pass through untouched.
        assert scrubbed["psn_system_name"] == "Stage A"
        assert scrubbed["psn_mcast_ip"] == "236.10.10.10"

    def test_general_section_drops_source_iface(self) -> None:
        """The General section accepts the same field on the form-save
        path, so it must filter too – broadcasts via ``/api/config/general``
        would otherwise sneak past the PSN-section guard."""
        scrubbed = strip_device_local_fields(
            "general",
            {"psn_system_name": "X", "psn_source_iface": "wlan0"},
        )
        assert "psn_source_iface" not in scrubbed
        assert scrubbed["psn_system_name"] == "X"

    def test_otp_output_section_drops_source_iface(self) -> None:
        """The OTP source interface pins THIS device's NIC, like
        ``psn_source_iface`` – a broadcast/import must not copy it to a peer
        whose interface names (or networks) differ."""
        scrubbed = strip_device_local_fields(
            "otp_output",
            {"enabled": True, "system_number": 3, "source_iface": "eth0"},
        )
        assert "source_iface" not in scrubbed
        # Non-device-local OTP fields pass through untouched.
        assert scrubbed["enabled"] is True
        assert scrubbed["system_number"] == 3

    def test_other_sections_passthrough(self) -> None:
        """Sections without registered device-local fields return a
        shallow copy of the input – no mutation, no surprise drops."""
        data = {"pos_x": 1.0, "pos_y": 2.0}
        scrubbed = strip_device_local_fields("camera", data)
        assert scrubbed == data
        # Returns a fresh dict so callers can mutate safely.
        assert scrubbed is not data

    def test_returns_fresh_dict_even_with_no_drop(self) -> None:
        """Idempotent shallow-copy contract for the PSN section when
        no device-local fields are actually present in the payload."""
        data = {"psn_system_name": "OnlySystemName"}
        scrubbed = strip_device_local_fields("psn", data)
        assert scrubbed == data
        assert scrubbed is not data


# --------------------------------------------------------------------------- #
# _config_to_toml – diagnostics "effective config" dump
# --------------------------------------------------------------------------- #


class TestConfigToToml:
    """The diagnostics dump shares ``save_config``'s serialisation, so an
    int-keyed dict like ``marker_move_speeds`` is stringified instead of
    raising ``TypeError`` and blanking the whole "Effective config" section."""

    def test_dumps_populated_marker_move_speeds_without_typeerror(self) -> None:
        cfg = AppConfig(
            controlled_marker_ids=[1, 2],
            marker_move_speeds={1: 1.5, 2: 4.0},
        )
        toml = _config_to_toml(cfg)
        # Round-trips: the produced text parses back as valid TOML.
        parsed = tomllib.loads(toml)
        assert parsed["marker_move_speeds"] == {"1": 1.5, "2": 4.0}

    def test_empty_marker_move_speeds_still_dumps(self) -> None:
        toml = _config_to_toml(AppConfig())
        assert isinstance(toml, str)


# --------------------------------------------------------------------------- #
# apply_section_data: operator messages + osc multicast
# --------------------------------------------------------------------------- #


class TestOperatorMessagesSection:
    def test_operator_messages_saves_fields(self) -> None:
        cfg = AppConfig()
        changed = apply_section_data(
            cfg,
            "operator_messages",
            {"enabled": False, "position": "top", "max_visible": "4"},
        )
        assert changed is True
        assert cfg.operator_messages.enabled is False
        assert cfg.operator_messages.position == "top"
        assert cfg.operator_messages.max_visible == 4

    def test_operator_messages_clamps_and_falls_back(self) -> None:
        # __post_init__ re-runs at the web-save path: out-of-range max_visible
        # clamps; an invalid position falls back to "bottom".
        cfg = AppConfig()
        apply_section_data(
            cfg,
            "operator_messages",
            {"enabled": True, "position": "sideways", "max_visible": "999"},
        )
        assert cfg.operator_messages.position == "bottom"
        assert cfg.operator_messages.max_visible == 20

    def test_osc_multicast_group_saved(self) -> None:
        cfg = AppConfig()
        apply_section_data(cfg, "osc", {"multicast_group": "239.1.2.3"})
        assert cfg.osc.multicast_group == "239.1.2.3"

    def test_osc_invalid_multicast_group_coerces_to_off(self) -> None:
        # __post_init__ rejects a non-multicast address → "" (off).
        cfg = AppConfig()
        apply_section_data(cfg, "osc", {"multicast_group": "10.0.0.1"})
        assert cfg.osc.multicast_group == ""

    def test_controller_clear_message_bindings_saved(self) -> None:
        cfg = AppConfig()
        apply_section_data(
            cfg,
            "keyboard",
            {"key_clear_messages": "c"},
        )
        assert cfg.controller.key_clear_messages == "c"
        apply_section_data(
            cfg,
            "gamepad",
            {"btn_clear_messages": "START"},
        )
        assert cfg.controller.btn_clear_messages == "START"
