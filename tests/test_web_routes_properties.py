# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Property-based tests for the web-form apply path.

``apply_section_data`` is the trust boundary for the config web UI: it takes a
section name and a JSON-decoded dict straight off the wire and mutates the live
``AppConfig``. The contract (CLAUDE.md "Validation contract") is that a
crafted POST is held to the same rules as a hand-edited config.toml – it must
never raise, never persist a non-finite number, and only ever apply a coerced
value or preserve the prior one. These tests fuzz it with hostile JSON values
(nan, inf, huge ints, wrong types, None, lists) across every section.

Conventions mirror ``test_web_routes_unit.py``.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Any

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from openfollow.configuration import AppConfig
from openfollow.video.inputs import get_available_input_ids
from openfollow.web.routes import apply_section_data

pytestmark = pytest.mark.unit

# Sections whose handler re-runs the sub-config's ``__post_init__`` – these
# carry the strong "config stays finite/in-range" guarantee.
_POSTINIT_SECTIONS = [
    "camera",
    "grid",
    "marker",
    "movement",
    "controller",
    "gamepad",
    "keyboard",
    "mouse",
    "osc",
    "detection",
    "otp_output",
    "rttrpm_output",
    "trigger_zones",
]
# ``psn`` / ``general`` are string/int handlers (also safe to fuzz for crashes).
# ``video_source`` delegates coercion to plugin code and is out of scope here.
_SAFE_SECTIONS = _POSTINIT_SECTIONS + ["psn", "general"]
_ALL_KNOWN_SECTIONS = frozenset(_SAFE_SECTIONS + ["video_source"])

# Real field names sampled across sections so the per-section field parsers are
# actually exercised (unknown keys are ignored by ``_apply_parsed_updates``).
_FIELD_POOL = [
    "fov",
    "pos_x",
    "pos_y",
    "pos_z",
    "pitch",
    "yaw",
    "roll",
    "sensor_width_mm",
    "focal_length_mm",
    "width",
    "depth",
    "spacing",
    "x_offset",
    "max_height",
    "color",
    "thickness",
    "transparency",
    "origin_visible",
    "origin_length",
    "min_speed",
    "max_speed",
    "move_speed",
    "ball_size",
    "crosshair_size",
    "crosshair_color",
    "drop_line_thickness",
    "enabled",
    "deadzone",
    "host",
    "port",
    "protocol",
    "model",
    "backend",
    "inference_size",
    "confidence",
    "interval_ms",
    "show_boxes",
    "box_thickness",
    "max_persons",
    "pin_marker_id",
    "pin_point",
    "smoothing",
    "prediction",
    "grace_period_ms",
    "hysteresis",
    "debounce_ms",
]

_JSON_VALUES = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.integers(min_value=10**200, max_value=10**500),  # float()/int() overflow
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=12),
    st.lists(st.integers(), max_size=3),
)

_DATA = st.dictionaries(
    keys=st.one_of(st.sampled_from(_FIELD_POOL), st.text(max_size=8)),
    values=_JSON_VALUES,
    max_size=8,
)


def _all_finite(obj: Any) -> bool:
    """True if no float anywhere in a (possibly nested) structure is inf/nan."""
    if isinstance(obj, bool):
        return True
    if isinstance(obj, float):
        return math.isfinite(obj)
    if isinstance(obj, dict):
        return all(_all_finite(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return all(_all_finite(v) for v in obj)
    return True


@given(section=st.sampled_from(_SAFE_SECTIONS), data=_DATA)
def test_apply_section_data_never_raises_and_returns_bool(
    section: str,
    data: dict[str, Any],
) -> None:
    result = apply_section_data(AppConfig(), section, data)
    assert isinstance(result, bool)


@given(section=st.sampled_from(_POSTINIT_SECTIONS), data=_DATA)
def test_apply_section_data_never_persists_a_non_finite_value(
    section: str,
    data: dict[str, Any],
) -> None:
    cfg = AppConfig()
    apply_section_data(cfg, section, data)
    assert _all_finite(dataclasses.asdict(cfg))


@given(name=st.text(max_size=10), data=_DATA)
def test_apply_section_data_unknown_section_returns_false(
    name: str,
    data: dict[str, Any],
) -> None:
    assume(name not in _ALL_KNOWN_SECTIONS)
    assert apply_section_data(AppConfig(), name, data) is False


# The ``video_source`` section dispatches into per-plugin ``apply_config_fields``
# (not the shared ``__post_init__`` re-coercion), so it gets its own no-crash
# test. ``vst`` may force any registered input id to exercise every plugin's
# field-apply path; finiteness is the plugin's contract and out of scope here.
_VIDEO_DATA = st.dictionaries(
    keys=st.one_of(
        st.sampled_from(
            [
                "video_source_type",
                "stall_timeout",
                "heal_interval",
                "rtsp_url",
                "srt_host",
                "srt_port",
                "rtp_url",
                "ndi_source_name",
                "picam_width",
                "v4l2_device",
                "avf_device_index",
                "testpattern_pattern",
            ]
        ),
        st.text(max_size=8),
    ),
    values=_JSON_VALUES,
    max_size=8,
)


@given(
    data=_VIDEO_DATA,
    vst=st.one_of(st.none(), st.sampled_from(get_available_input_ids())),
)
def test_apply_section_data_video_source_never_raises(
    data: dict[str, Any],
    vst: str | None,
) -> None:
    payload = dict(data)
    if vst is not None:
        payload["video_source_type"] = vst
    assert apply_section_data(AppConfig(), "video_source", payload) is True
