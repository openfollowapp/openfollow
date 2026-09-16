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

    @pytest.mark.parametrize(
        "state, rendered",
        [
            ("connected", "Connected"),
            ("connecting", "Connecting"),
            ("reconnecting", "Reconnecting"),
            ("disconnected", "Disconnected"),
        ],
    )
    def test_pipeline_state_reads_like_every_other_value(self, state: str, rendered: str) -> None:
        """It was the only row shouting in capitals. The states are the four
        connection states - never GStreamer's ``PLAYING`` / ``NULL``, which the
        help drawer used to promise and which cannot appear here.
        """
        panel = _panel(_render(video={"connected": state == "connected", "pipeline_state": state}), "Video")
        assert _row(panel, "Pipeline") == rendered

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


class TestVideoFailureBanner:
    """The receiver knows exactly why a source failed, and used to say so
    nowhere. An operator facing `Disconnected` had to guess between a wrong
    URL, a firewall, a dead encoder, a codec the box can't decode, and a source
    name that no longer exists - while the reason sat in the payload the page
    had already fetched.
    """

    _ERROR = "Could not open resource for reading: Unauthorized"

    def _banner(self, **video: Any) -> str:
        payload: dict[str, Any] = {"connected": False, "error_message": self._ERROR}
        payload.update(video)
        panel = _panel(_render(video=payload), "Video")
        start = panel.index('<div class="notice error">')
        return panel[start : panel.index("</div>", panel.rindex("</div>", start, len(panel)))]

    def test_a_failure_reason_is_rendered(self) -> None:
        assert self._ERROR in self._banner()

    def test_the_reason_is_shown_verbatim(self) -> None:
        """No mapping to friendlier categories: a wrong mapping is worse than a
        blunt string, and the raw text is what separates "connection refused"
        from "no such NDI source"."""
        raw = "gstrtspsrc.c(7469): gst_rtspsrc_send (): Unauthorized (401)"
        assert raw in self._banner(error_message=raw)

    def test_it_sits_above_the_metric_rows(self) -> None:
        """A row saying `Unauthorized` among six other rows is one more figure
        to weigh, not an answer. It has to be the first thing in the panel."""
        panel = _panel(_render(video={"connected": False, "error_message": self._ERROR}), "Video")
        assert panel.index('class="notice error"') < panel.index('<dl class="metric-list">')

    def test_it_announces_itself_assertively(self) -> None:
        banner = self._banner()
        assert 'role="alert"' in banner
        assert 'aria-live="assertive"' in banner
        assert 'aria-atomic="true"' in banner

    def test_it_reuses_the_shared_notice_style(self) -> None:
        """``.notice.error`` from base.tpl, not another one-off inline-styled
        block like the Person Detection banner's."""
        banner = self._banner()
        assert 'class="notice error"' in banner
        assert "style=" not in banner

    @pytest.mark.parametrize(
        "video",
        [
            {"connected": True, "error_message": "stale reason carried forward"},
            {"connected": False, "error_message": ""},
            {"connected": False},
        ],
        ids=["connected", "empty-reason", "no-reason"],
    )
    def test_no_banner_without_a_live_failure(self, video: dict[str, Any]) -> None:
        assert 'class="notice error"' not in _panel(_render(video=video), "Video")

    def test_the_reason_is_escaped(self) -> None:
        """Straight from GStreamer into the DOM: a ``<`` in an error message
        must not parse as markup."""
        body = _render(video={"connected": False, "error_message": "<script>alert(1)</script>"})
        assert "<script>alert" not in body

    def test_retry_progress_is_shown_while_reconnecting(self) -> None:
        """A source working through its backoff and one that has given up read
        identically without this, and they call for different responses."""
        assert "Reconnect attempt 3." in self._banner(reconnect_attempt=3)

    @pytest.mark.parametrize("attempt", [0, None])
    def test_no_retry_line_when_not_retrying(self, attempt: Any) -> None:
        assert "Reconnect attempt" not in self._banner(reconnect_attempt=attempt)


class TestVideoFailureBannerIsNotReannounced:
    """This partial is re-swapped every second. A freshly inserted
    ``role="alert"`` on each poll would have a screen reader repeating the
    failure without pause, so the announcing node is preserved across swaps and
    identified by what it says.
    """

    def _alert_id(self, message: str, **video: Any) -> str:
        payload: dict[str, Any] = {"connected": False, "error_message": message}
        payload.update(video)
        panel = _panel(_render(video=payload), "Video")
        start = panel.index('<div id="video-error-')
        return panel[start + len('<div id="') : panel.index('"', start + len('<div id="'))]

    def test_the_announcing_node_is_preserved_across_swaps(self) -> None:
        banner = _panel(_render(video={"connected": False, "error_message": "boom"}), "Video")
        assert 'hx-preserve="true"' in banner

    def test_an_unchanged_reason_keeps_the_same_node_identity(self) -> None:
        first = self._alert_id("Could not open resource for reading")
        second = self._alert_id("Could not open resource for reading")
        assert first == second

    def test_a_changed_reason_takes_a_new_identity_so_it_announces(self) -> None:
        assert self._alert_id("Connection refused") != self._alert_id("Unauthorized")

    def test_a_retry_tick_alone_does_not_change_the_announcing_node(self) -> None:
        """The attempt counter moves every few seconds. It is progress on a
        failure already announced, so it lives outside the preserved node."""
        message = "Connection refused"
        assert self._alert_id(message, reconnect_attempt=1) == self._alert_id(message, reconnect_attempt=7)
        banner = _panel(_render(video={"connected": False, "error_message": message, "reconnect_attempt": 7}), "Video")
        alert_start = banner.index('<div id="video-error-')
        alert_end = banner.index("</div>", alert_start)
        assert "Reconnect attempt" not in banner[alert_start:alert_end]


def test_a_credential_in_the_receiver_error_never_reaches_the_banner() -> None:
    """End to end, through the real status marker.

    The reason is shown verbatim, and an ``rtspsrc`` failure carries the full
    location - so for a camera authenticated through the URL the reason *is*
    the password. This partial is exempt from the web PIN
    (``routes.py`` ``_check_auth``), so an un-redacted banner would publish the
    camera credential to anything that can reach the station.
    """
    from openfollow.video.connection_status import NdiStatusMarker

    marker = NdiStatusMarker()
    marker.set_reconnecting(
        2,
        "Could not open resource for reading rtsp://operator:hunter2@192.168.0.182:554/video/stream1",
    )
    # Exactly what ``publish_runtime_stats`` copies into the snapshot.
    panel = _panel(
        _render(
            video={
                "connected": bool(marker.is_connected),
                "error_message": marker.error_message,
                "reconnect_attempt": int(marker.reconnect_attempt),
            }
        ),
        "Video",
    )
    assert "hunter2" not in panel
    assert "operator:" not in panel
    # Still the answer the operator needs.
    assert "192.168.0.182:554/video/stream1" in panel
    assert "Reconnect attempt 2." in panel
