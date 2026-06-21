# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Structure tests for the Person Detection partial.

Renders ``partials/detection`` directly (no live server) and asserts the
three-form layout (Tracking, Models, Detection & Display), the section
ordering, the Tracking 3-state segmented radio, the YOLO26 quality tiers,
the replace-only "Follow marker" select, and the absence of the fields the
refactor dropped (``enabled`` / ``pin_marker`` / ``preprocess_clahe`` /
``inference_size`` inputs).
"""

from __future__ import annotations

import re

import pytest
from bottle import template

from openfollow.configuration import AppConfig
from openfollow.web import server as _server_module  # noqa: F401 - registers tpl path
from openfollow.web.routes import _DETECTION_TIERS, _detection_tiers

pytestmark = pytest.mark.unit


def _render(**ctx: object) -> str:
    base: dict[str, object] = {
        "config": AppConfig(),
        "saved": False,
        "detection_tiers": _detection_tiers({"detection": True}, storage_path="/tmp/of-no-models"),
    }
    base.update(ctx)
    return template("partials/detection", **base)


def test_three_forms_each_post_to_their_own_route() -> None:
    html = _render()
    for route in (
        "/section/detection/tracking",
        "/section/detection/models",
        "/section/detection/inference",
    ):
        assert f'hx-post="{route}"' in html
    # Three config forms (the mask editor is a div, not a form).
    assert html.count("<form") == 3
    # The only submit buttons are the three per-form Saves.
    assert html.count('type="submit"') == 3


def test_section_order_tracking_then_models_then_detection_display() -> None:
    html = _render()
    pos_tracking = html.index("<h2>Tracking</h2>")
    pos_models = html.index("<h2>Models</h2>")
    pos_display = html.index("<h2>Detection &amp; Display</h2>")
    assert pos_tracking < pos_models < pos_display


def test_tracking_is_a_three_state_segmented_radio() -> None:
    html = _render()
    # One radiogroup with three radios, values off / assist / replace.
    assert html.count('name="tracking_state"') == 3
    for value in ("off", "assist", "replace"):
        assert f'name="tracking_state" value="{value}"' in html
    assert "seg-toggle" in html
    # No standalone enabled checkbox or pin_marker checkbox survives.
    assert 'name="enabled"' not in html
    assert not re.search(r'name="pin_marker"(?!_id)', html)


def test_tracking_state_reflects_config_mode() -> None:
    off_cfg = AppConfig()
    off_cfg.detection.enabled = False
    off_html = _render(config=off_cfg)
    assert 'name="tracking_state" value="off" checked' in off_html

    assist_cfg = AppConfig()
    assist_cfg.detection.enabled = True
    assist_cfg.detection.pin_mode = "assist"
    assist_html = _render(config=assist_cfg)
    assert 'name="tracking_state" value="assist" checked' in assist_html

    replace_cfg = AppConfig()
    replace_cfg.detection.enabled = True
    replace_cfg.detection.pin_mode = "replace"
    replace_html = _render(config=replace_cfg)
    assert 'name="tracking_state" value="replace" checked' in replace_html


def test_no_removed_fields_rendered() -> None:
    # CLAHE, the visible inference-size select, the standalone enabled checkbox,
    # and the pin_marker checkbox were all dropped in the refactor.
    html = _render()
    assert 'name="preprocess_clahe"' not in html
    assert 'name="inference_size"' not in html
    assert 'name="enabled"' not in html
    assert "CLAHE" not in html


def test_quality_tiers_are_five_yolo26_radios() -> None:
    html = _render()
    tier_values = re.findall(r'name="model" value="(yolo26[a-z]\.onnx)"', html)
    assert tier_values == ["yolo26n.onnx", "yolo26s.onnx", "yolo26m.onnx", "yolo26l.onnx", "yolo26x.onnx"]
    for label in ("Fastest", "Fast", "Balanced", "Accurate", "Most Accurate"):
        assert f"<strong>{label}</strong>" in html


def test_unavailable_tiers_render_disabled() -> None:
    # No models on disk → every tier is unavailable → every radio is disabled.
    html = _render(detection_tiers=_detection_tiers({"detection": True}, storage_path="/tmp/of-no-models"))
    radios = re.findall(r'<input type="radio" name="model"[^>]*>', html)
    yolo26_radios = [r for r in radios if "yolo26" in r]
    assert len(yolo26_radios) == 5
    assert all("disabled" in r for r in yolo26_radios)


def test_follow_marker_select_is_replace_only() -> None:
    html = _render()
    assert ">Follow marker</label>" in html
    assert 'name="pin_marker_id"' in html
    # The Follow marker field lives inside a data-replace-only container.
    container = html.split("data-replace-only", 1)[1].split("</div>", 1)[0]
    assert ">Follow marker</label>" in container
    assert 'name="pin_marker_id"' in container


def test_follow_marker_hidden_unless_replace_selected() -> None:
    assist_cfg = AppConfig()
    assist_cfg.detection.enabled = True
    assist_cfg.detection.pin_mode = "assist"
    assist_html = _render(config=assist_cfg)
    tag = assist_html.split("data-replace-only", 1)[1].split(">", 1)[0]
    assert "hidden" in tag

    replace_cfg = AppConfig()
    replace_cfg.detection.enabled = True
    replace_cfg.detection.pin_mode = "replace"
    replace_html = _render(config=replace_cfg)
    tag = replace_html.split("data-replace-only", 1)[1].split(">", 1)[0]
    assert "hidden" not in tag


def test_assisted_tracking_block_hidden_unless_assist() -> None:
    assist_cfg = AppConfig()
    assist_cfg.detection.enabled = True
    assist_cfg.detection.pin_mode = "assist"
    assist_html = _render(config=assist_cfg)
    tag = assist_html.split("data-assist-only", 1)[1].split(">", 1)[0]
    assert "hidden" not in tag

    replace_cfg = AppConfig()
    replace_cfg.detection.enabled = True
    replace_cfg.detection.pin_mode = "replace"
    replace_html = _render(config=replace_cfg)
    tag = replace_html.split("data-assist-only", 1)[1].split(">", 1)[0]
    assert "hidden" in tag


def test_advanced_models_details_present() -> None:
    # The model download / other-installed / installed-list / storage info live
    # inside an Advanced <details> below the quality tiers.
    html = _render()
    assert 'class="inline-advanced"' in html
    assert "<summary>Advanced models</summary>" in html


def test_models_disk_readout_shown() -> None:
    html = _render(
        detection_storage_info={
            "path": "/mnt/nvme/openfollow/yolo/models",
            "free_h": "18.2 GiB",
            "total_h": "58.0 GiB",
        }
    )
    assert "Models disk" in html
    assert "18.2 GiB free" in html
    assert "/mnt/nvme/openfollow/yolo/models" in html


def test_installed_models_list_has_delete_buttons() -> None:
    html = _render(
        detection_installed_models=[
            {"name": "yolo11n.onnx", "size_bytes": 10_000_000, "size_h": "9.5 MiB"},
        ]
    )
    section = html.split(">Installed models</h3>", 1)[1]
    assert "yolo11n.onnx" in section
    assert "9.5 MiB" in section
    assert "/section/detection/models/delete" in section
    assert '"model": "yolo11n.onnx"' in section


def test_no_installed_models_group_when_empty() -> None:
    assert ">Installed models</h3>" not in _render(detection_installed_models=[])


def test_detection_and_display_groups_labelled() -> None:
    html = _render()
    for title in ("Quality", "Detection", "Display", "Assisted Tracking"):
        assert f">{title}</h3>" in html


def test_max_persons_and_confidence_use_new_labels() -> None:
    html = _render()
    assert "Detection sensitivity" in html
    assert "Maximum people" in html
    assert 'name="max_persons"' in html
    assert 'name="confidence"' in html


def test_display_group_keeps_box_controls() -> None:
    html = _render()
    display_block = html.split(">Display</h3>", 1)[1]
    assert 'name="show_boxes"' in display_block
    assert 'name="show_labels"' in display_block
    assert 'name="box_color"' in display_block
    assert 'name="box_thickness"' in display_block


def test_missing_deps_banner_points_at_install_script() -> None:
    html = _render(detection_missing=["onnxruntime"])
    banner = html.split('role="alert"', 1)[1].split("</div>", 1)[0]
    assert "onnxruntime" in banner
    assert "install-detection.sh" in banner


def test_no_missing_deps_banner_when_nothing_missing() -> None:
    html = _render(detection_missing=[])
    assert 'role="alert"' not in html


def test_detection_tiers_helper_maps_labels_and_availability() -> None:
    # Five tiers in fixed order with the published labels; availability is
    # gated on both the extra being installed AND the .onnx being on disk.
    tiers = _detection_tiers({"detection": True}, storage_path="/tmp/of-no-models")
    assert [t["model"] for t in tiers] == [m for m, _label, _blurb in _DETECTION_TIERS]
    assert [t["label"] for t in tiers] == ["Fastest", "Fast", "Balanced", "Accurate", "Most Accurate"]
    # No files on disk → none available.
    assert all(t["available"] is False for t in tiers)
    # Extra not installed → none available regardless of disk.
    no_extra = _detection_tiers({"detection": False}, storage_path="/tmp/of-no-models")
    assert all(t["available"] is False for t in no_extra)


def test_detection_tier_availability_reflects_on_disk(tmp_path) -> None:
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "yolo26n.onnx").write_bytes(b"x")
    tiers = _detection_tiers({"detection": True}, storage_path=str(tmp_path))
    by_model = {t["model"]: t["available"] for t in tiers}
    assert by_model["yolo26n.onnx"] is True
    assert by_model["yolo26s.onnx"] is False


def test_all_select_tags_are_closed() -> None:
    """Every ``<select>`` is balanced by a ``</select>``.

    The test HTML parser auto-closes tags, so a missing ``</select>`` slips past
    structural assertions while a real browser mis-parses every element after the
    unclosed select – breaking the Pin point picker and the rest of the form.
    Render with controlled markers + replace mode so all three selects emit.
    """
    cfg = AppConfig()
    cfg.controlled_marker_ids = [0, 1]
    cfg.detection.enabled = True
    cfg.detection.pin_mode = "replace"
    html = _render(config=cfg)
    assert html.count("<select") == html.count("</select>") >= 3


def test_pin_point_select_renders_head_and_feet() -> None:
    """The Pin point picker (head / feet) is present and well-formed."""
    html = _render()
    assert 'name="pin_point"' in html
    assert 'value="top"' in html and 'value="bottom"' in html
