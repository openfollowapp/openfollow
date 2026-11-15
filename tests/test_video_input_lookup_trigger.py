# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""A hidden input's source lookup waits until the page shows that input.

Every input's fragment is on the Video Source page, the hidden ones too. A
lookup on load would scan for NDI sources (starting the NDI runtime, which
then keeps a Mac awake) or cameras while another source is in use.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from openfollow.video.inputs.avf import AvfInput
from openfollow.video.inputs.ndi import NdiInput

pytestmark = pytest.mark.unit

_TEMPLATES = Path(__file__).resolve().parents[1] / "openfollow" / "web" / "templates"


@pytest.mark.parametrize(
    ("plugin", "values", "route", "refresh"),
    [
        (NdiInput, {"ndi_source_name": ""}, "/video-input/ndi/sources", "#refresh-ndi"),
        (AvfInput, {"avf_unique_id": ""}, "/video-input/avf/devices", "#refresh-avf"),
    ],
)
def test_the_lookup_runs_only_for_the_shown_input(plugin, values, route: str, refresh: str) -> None:
    html = plugin.web_ui_html(values)
    select = re.search(rf'<select[^>]*hx-get="{re.escape(route)}"[^>]*>', html)

    assert select is not None
    trigger = re.search(r'hx-trigger="([^"]*)"', select.group(0))
    assert trigger is not None
    # Checked in a browser against the bundled htmx: nothing loads while the input is hidden.
    assert trigger.group(1) == (
        "load[!this.closest('[data-input-type][style*=none],[data-wizard-input-type][style*=none]')], "
        f"of-input-shown, click from:{refresh}"
    )


@pytest.mark.parametrize(
    ("template", "container"),
    [("partials/video_source.tpl", "data-input-type"), ("wizard.tpl", "data-wizard-input-type")],
)
def test_the_source_type_picker_announces_the_input_it_shows(template: str, container: str) -> None:
    text = (_TEMPLATES / template).read_text(encoding="utf-8")

    assert (
        f"<div {container}=\"{{{{iid}}}}\" style=\"display:{{{{'none' if config.video_source_type != iid else ''}}}}\">"
        in text
    )
    assert re.search(r"htmx\.trigger\(t,\s*'of-input-shown'\)", text)
