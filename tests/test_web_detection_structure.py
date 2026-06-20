# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Structure tests for the Person Detection partial.

Renders ``partials/detection`` directly (no live server) and asserts the
three-collapsible-box layout (the same ``.section`` + fold component as the
Output PSN/OTP/RTTrPM boxes), the Pi-5 performance note, the available-only
model dropdown, the single-button download UI, the Tracking Mode segmented
toggle, and the mode-conditional Assisted Tracking block.
"""

from __future__ import annotations

import pytest
from bottle import template

from openfollow.configuration import AppConfig
from openfollow.web import server as _server_module  # noqa: F401 - registers tpl path

pytestmark = pytest.mark.unit


def _render(**ctx: object) -> str:
    base: dict[str, object] = {"config": AppConfig(), "saved": False}
    base.update(ctx)
    return template("partials/detection", **base)


def test_layout_uses_three_collapsible_section_boxes() -> None:
    html = _render()
    # Three boxes built from the same component as the Output PSN/OTP/RTTrPM
    # boxes: a ``.section`` with a ``data-fold-key`` (fold toggle is JS-injected)
    # and a ``data-help`` affordance.
    for key in ("detection_models", "detection_inference", "detection_tracking"):
        assert f'data-fold-key="{key}"' in html
    assert html.count("data-fold-key=") == 3
    for title in ("Models", "Detection &amp; Display", "Tracking"):
        assert f">{title}</h2>" in html
    # Help is reachable from each box (mirrors the Output boxes' per-box "?").
    assert html.count('data-help="detection"') == 3


def test_each_box_is_its_own_form_with_its_own_save() -> None:
    # Output PSN/OTP/RTTrPM parity: three independent forms, each posting to its
    # own per-box route, each carrying exactly one Save submit button.
    html = _render()
    for route in (
        "/section/detection/models",
        "/section/detection/inference",
        "/section/detection/tracking",
    ):
        assert f'hx-post="{route}"' in html
    assert html.count("<form") == 3
    # The only submit buttons are the three per-box Saves (install / download
    # buttons are type="button").
    assert html.count('type="submit"') == 3


def test_subgroups_are_labelled() -> None:
    html = _render()
    for title in ("Model", "Detection", "Display", "Assisted Tracking"):
        assert f">{title}</h3>" in html


def test_no_dependencies_group_or_storage_field() -> None:
    # Dependencies install/uninstall view and the Storage Path field were
    # removed: deps come from install-detection.sh and storage is automatic.
    html = _render(detection_extras_installed={"detection": True, "export": True})
    assert ">Dependencies</h3>" not in html
    assert "Uninstall" not in html
    assert 'name="storage_path"' not in html
    assert "/section/detection/install" not in html
    assert "/section/detection/uninstall" not in html


def test_pi5_performance_note_present() -> None:
    html = _render()
    assert "workstation is recommended" in html


def test_available_models_are_selectable_unavailable_are_not() -> None:
    cfg = AppConfig()
    cfg.detection.model = "yolov8n.onnx"
    html = _render(
        config=cfg,
        detection_available_models=[
            ("yolov8n.onnx", "YOLOv8 Nano ONNX", True),
            ("yolov8x.onnx", "YOLOv8 XLarge ONNX", False),
        ],
    )
    # The available model is an <option>; the unavailable one is not offered in
    # the dropdown (it belongs in the Download model UI instead).
    assert 'value="yolov8n.onnx"' in html
    assert 'value="yolov8x.onnx"' not in html


def test_saved_model_not_on_disk_is_preserved() -> None:
    cfg = AppConfig()
    cfg.detection.model = "custom.onnx"
    html = _render(
        config=cfg,
        detection_available_models=[("yolov8n.onnx", "YOLOv8 Nano ONNX", False)],
    )
    # No models on disk: the saved value is preserved via a hidden input so Save
    # can't drop it, but it is NOT surfaced as a "(not on disk)" picker entry.
    assert 'type="hidden"' in html
    assert 'name="model"' in html
    assert 'value="custom.onnx"' in html
    assert "not on disk" not in html
    assert "No models installed" in html


def test_models_disk_readout_shown() -> None:
    # The Models group shows free/total space + the resolved path so the
    # operator can judge how many models fit.
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


def test_max_persons_lives_in_the_detection_group() -> None:
    # Max Persons is a general-detection setting, not a Display one.
    html = _render()
    after_detection = html.split(">Detection</h3>", 1)[1]
    detection_block = after_detection.split(">Display</h3>", 1)[0]
    assert 'name="max_persons"' in detection_block


def test_missing_deps_banner_points_at_install_script() -> None:
    # A clear reason + the next step, announced to screen readers. Deps install
    # only via install-detection.sh now, so the banner always points there.
    html = _render(detection_missing=["onnxruntime"])
    banner = html.split('role="alert"', 1)[1].split("</div>", 1)[0]
    assert "onnxruntime" in banner
    assert "install-detection.sh" in banner


def test_no_missing_deps_banner_when_nothing_missing() -> None:
    html = _render(detection_missing=[])
    assert 'role="alert"' not in html


def test_download_ui_is_a_single_dropdown_plus_button() -> None:
    # One model dropdown + one Download button, not a button per model.
    cfg = AppConfig()
    cfg.detection.storage_path = "/tmp/of-store"
    html = _render(
        config=cfg,
        detection_available_models=[
            ("yolov8n.onnx", "YOLOv8 Nano ONNX", False),
            ("yolo11x.onnx", "YOLO11 XLarge ONNX", False),
        ],
        detection_extras_installed={"detection": True, "export": True},
    )
    download_section = html.split(">Download Model</h3>", 1)[1]
    # A select listing the unavailable models...
    assert 'name="export_model"' in download_section
    assert 'value="yolov8n.onnx"' in download_section
    assert 'value="yolo11x.onnx"' in download_section
    # ...and exactly one Download button (no per-model buttons).
    assert download_section.count("/section/detection/export") == 1
    assert download_section.count("Download model") == 1


def test_download_form_shown_when_export_tools_installed() -> None:
    # Storage is automatic now (always a valid absolute path), so the only gate
    # on the export form is the export tools being installed.
    html = _render(
        detection_available_models=[("yolov8n.onnx", "YOLOv8 Nano ONNX", False)],
        detection_extras_installed={"detection": True, "export": True},
    )
    download_section = html.split(">Download Model</h3>", 1)[1]
    assert 'name="export_model"' in download_section


def test_download_form_gated_when_export_tools_missing() -> None:
    # Without the export tools the form is hidden and a hint points at the
    # install script instead.
    html = _render(
        detection_available_models=[("yolov8n.onnx", "YOLOv8 Nano ONNX", False)],
        detection_extras_installed={"detection": True, "export": False},
    )
    download_section = html.split(">Download Model</h3>", 1)[1]
    assert 'name="export_model"' not in download_section
    assert "install-detection.sh" in download_section


def test_tracking_mode_is_a_segmented_toggle_not_a_dropdown() -> None:
    cfg = AppConfig()
    cfg.detection.pin_mode = "assist"
    html = _render(config=cfg)
    assert "seg-toggle" in html
    # The toggle is the topmost control in the Tracking box, labelled
    # Tracking Mode, with both human-facing mode names.
    assert ">Tracking Mode</label>" in html
    assert "AI Assisted" in html
    assert "Fully Automatic" in html
    # Two radio options for the two modes, with the saved one checked.
    assert html.count('name="pin_mode"') == 2
    assert 'value="assist" checked' in html
    assert '<select name="pin_mode">' not in html


def test_tracking_mode_toggle_precedes_pin_marker() -> None:
    # "Move pin marker a bit down": Tracking Mode comes first in the box.
    html = _render()
    assert html.index(">Tracking Mode</label>") < html.index('name="pin_marker"')


def test_assisted_tracking_hidden_unless_assist_selected() -> None:
    # The Assisted Tracking sub-block is server-rendered ``hidden`` in Fully
    # Automatic mode and visible in AI Assisted mode; a delegated JS handler
    # toggles it live (data-assist-only is the hook).
    replace_cfg = AppConfig()
    replace_cfg.detection.pin_mode = "replace"
    replace_html = _render(config=replace_cfg)
    block = replace_html.split("data-assist-only", 1)[1].split(">", 1)[0]
    assert "hidden" in block

    assist_cfg = AppConfig()
    assist_cfg.detection.pin_mode = "assist"
    assist_html = _render(config=assist_cfg)
    block = assist_html.split("data-assist-only", 1)[1].split(">", 1)[0]
    assert "hidden" not in block


def test_onnx_opset_field_removed_from_export_ui() -> None:
    cfg = AppConfig()
    cfg.detection.storage_path = "/tmp/of-store"
    html = _render(
        config=cfg,
        detection_available_models=[("yolov8n.onnx", "YOLOv8 Nano ONNX", False)],
        detection_extras_installed={"detection": True, "export": True},
    )
    assert "detection-export-opset" not in html
    assert "ONNX opset" not in html


def test_export_image_size_field_present_in_export_ui() -> None:
    # The export image size stays an operator field so a model can be exported
    # at a chosen input resolution.
    cfg = AppConfig()
    cfg.detection.storage_path = "/tmp/of-store"
    html = _render(
        config=cfg,
        detection_available_models=[("yolov8n.onnx", "YOLOv8 Nano ONNX", False)],
        detection_extras_installed={"detection": True, "export": True},
    )
    assert "detection-export-imgsz" in html
    assert "Export image size" in html
    assert 'name="imgsz"' in html


def test_detection_and_tracking_use_paired_rows() -> None:
    # Dense rows are split so at most two inputs sit side by side.
    html = _render()
    assert "row--pair" in html
