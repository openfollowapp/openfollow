# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Pin which Statistics panel each figure is rendered in.

An operator whose RTSP camera had never authenticated read `1920x1080` beside a
display-rate frame counter and concluded video was flowing. Both numbers were
the station's own: the resolution came from our "No Signal" picture and the rate
from the overlay redraw loop. Keeping a device figure out of the Video panel is
what stops the two being read as measurements of the feed, so the split is
pinned here rather than left to the template's shape.
"""

from __future__ import annotations

from typing import Any

import pytest
from bottle import template

from openfollow.web import server as _server_module  # noqa: F401 – registers tpl path

pytestmark = pytest.mark.unit


def _render(**stats: Any) -> str:
    return template("partials/statistics", stats=stats)


def _panel(body: str, title: str) -> str:
    """Return the markup of one ``stat-panel``, by its heading."""
    head = f'<h3 class="stat-panel-title">{title}</h3>'
    start = body.index(head)
    return body[start : body.index("</section>", start)]


def _row(panel: str, label: str) -> str:
    """Return the rendered value of one metric row, by its label."""
    marker = f'<dt class="metric-label">{label}</dt>'
    start = panel.index(marker) + len(marker)
    value = panel[start : panel.index("</dd>", start)]
    return value.split('<dd class="metric-value">', 1)[1].strip()


def _connected(**overrides: Any) -> dict[str, Any]:
    video = {
        "connected": True,
        "resolution": {"width": 1024, "height": 768},
        "source_fps": 25.0,
    }
    video.update(overrides)
    return video


class TestVideoPanel:
    def test_reports_no_geometry_while_nothing_is_connected(self) -> None:
        """The placeholder publishes nothing, so both rows read ``N/A`` rather
        than the black pattern's 1080p."""
        panel = _panel(
            _render(video={"connected": False, "resolution": {"width": 0, "height": 0}, "source_fps": 0.0}),
            "Video",
        )
        assert _row(panel, "Input resolution") == "N/A"
        assert _row(panel, "Frame Rate (source)") == "N/A"
        assert "1920x1080" not in panel

    def test_a_disconnected_source_reports_no_figures_even_if_present(self) -> None:
        """Connection state is what decides, not whether a number happens to be
        non-zero: the panel must never present a figure as the live feed's
        while nothing is connected."""
        panel = _panel(
            _render(video={"connected": False, "resolution": {"width": 1920, "height": 1080}, "source_fps": 30.0}),
            "Video",
        )
        assert _row(panel, "Input resolution") == "N/A"
        assert _row(panel, "Frame Rate (source)") == "N/A"

    def test_reports_the_source_figures_once_connected(self) -> None:
        panel = _panel(_render(video=_connected()), "Video")
        assert _row(panel, "Input resolution") == "1024x768"
        assert _row(panel, "Frame Rate (source)") == "25.0 fps"

    def test_a_connected_source_advertising_no_rate_is_not_reported_as_offline(self) -> None:
        """A variable-frame-rate source negotiates ``framerate=0/1``, which the
        receiver stores as ``0.0``. Rendering ``N/A`` for it would mean exactly
        what the help drawer says ``N/A`` means - "nothing is connected" -
        inverting the diagnosis this panel was reworked to fix.
        """
        panel = _panel(_render(video=_connected(source_fps=0.0)), "Video")
        assert _row(panel, "Frame Rate (source)") == "0.0 fps"

    def test_carries_no_device_figure(self) -> None:
        """``Frame Rate (measured)`` was the overlay redraw rate sitting between
        two real video figures, which is what made it read as a third one."""
        body = _render(video=_connected(), system={"hud_fps": 59.7})
        panel = _panel(body, "Video")
        assert "Frame Rate (measured)" not in body
        assert "59.7 fps" not in panel
        assert "Output resolution" not in panel


class TestDevicePanel:
    def test_carries_the_redraw_rate_and_the_live_canvas_size(self) -> None:
        panel = _panel(
            _render(
                video=_connected(),
                system={"hud_fps": 59.7, "output_resolution": {"width": 1920, "height": 1200}},
            ),
            "Device",
        )
        assert _row(panel, "Overlay redraw rate") == "59.7 fps"
        assert _row(panel, "Output resolution") == "1920x1200"

    def test_headless_station_says_so_instead_of_fabricating_a_size(self) -> None:
        panel = _panel(_render(system={"hud_fps": 0.0, "output_resolution": None}), "Device")
        assert _row(panel, "Output resolution") == "N/A (no display)"
        assert _row(panel, "Overlay redraw rate") == "0.0 fps"

    def test_a_snapshot_without_the_device_figures_still_renders(self) -> None:
        # A payload published before these fields existed (an older peer).
        panel = _panel(_render(system={"ip": "10.0.0.7"}), "Device")
        assert _row(panel, "Output resolution") == "N/A (no display)"
        assert _row(panel, "Overlay redraw rate") == "0.0 fps"
