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
        """The element's own wording survives alongside the classification. It
        is what a support bundle gets read against, and it carries detail no
        twelve-member enum can - the failing element, the line, the code."""
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

    @pytest.mark.parametrize("attempt", [0, 3, None])
    def test_the_retry_count_is_not_in_the_box(self, attempt: Any) -> None:
        """It changed on every attempt, which both resized the box and made it
        flicker under the poll. The Pipeline row below distinguishes a source
        still retrying from one that has given up, without anything moving."""
        assert "Reconnect attempt" not in self._banner(reconnect_attempt=attempt)

    def test_the_panel_still_says_whether_it_is_retrying(self) -> None:
        panel = _panel(
            _render(video={"connected": False, "error_message": self._ERROR, "pipeline_state": "reconnecting"}),
            "Video",
        )
        assert _row(panel, "Pipeline") == "Reconnecting"


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


class TestVideoFailureClassification:
    """The classification leads the banner: it names which piece of equipment to
    go and look at, where "Could not open resource for reading" does not."""

    def _panel_for(self, **video: Any) -> str:
        payload: dict[str, Any] = {"connected": False}
        payload.update(video)
        return _panel(_render(video=payload), "Video")

    def test_the_sentence_leads_and_the_next_step_follows(self) -> None:
        panel = self._panel_for(
            failure="unreachable",
            failure_text="Nothing answered at 192.0.2.10:554.",
            failure_action="Check the camera is powered and on this network.",
        )
        assert panel.index("Nothing answered at 192.0.2.10:554.") < panel.index(
            "Check the camera is powered and on this network."
        )

    def test_the_signal_state_names_the_failure(self) -> None:
        panel = self._panel_for(failure="unauthorized", failure_text="192.0.2.10:554 rejected the login.")
        assert "Login rejected" in panel
        assert _row(panel, "Signal") == "Login rejected"

    def test_a_connected_feed_reads_as_connected(self) -> None:
        panel = _panel(_render(video=_connected(failure="none")), "Video")
        assert _row(panel, "Signal") == "Connected"

    @pytest.mark.parametrize("failure", ["none", "unknown"])
    def test_an_unclassified_failure_contributes_no_sentence(self, failure: str) -> None:
        """Its sentence would sit above the element's wording and contradict it."""
        panel = self._panel_for(
            failure=failure,
            failure_text="Video from X failed for a reason this station does not recognise.",
            error_message="v4l2src is Linux-only",
        )
        assert "does not recognise" not in panel
        assert "v4l2src is Linux-only" in panel
        assert _row(panel, "Signal") == "Disconnected"

    def test_a_failure_token_from_a_newer_build_degrades(self) -> None:
        """A live provider's dict; an unknown token must not take the panel down."""
        panel = self._panel_for(failure="teleported_away", error_message="boom")
        assert _row(panel, "Signal") == "Disconnected"

    def test_the_sentence_alone_still_raises_the_banner(self) -> None:
        """A classified failure with no element text is still a failure."""
        panel = self._panel_for(failure="not_configured", failure_text="No video source is configured.")
        assert 'class="notice error"' in panel
        assert "No video source is configured." in panel

    def test_the_sentence_is_escaped(self) -> None:
        body = _render(
            video={"connected": False, "failure": "unreachable", "failure_text": "<script>alert(1)</script>"}
        )
        assert "<script>alert" not in body

    def test_a_changed_classification_is_reannounced(self) -> None:
        """The preserved node is keyed by what it says, sentence included."""

        def alert_id(**video: Any) -> str:
            panel = self._panel_for(**video)
            start = panel.index('<div id="video-error-')
            return panel[start + len('<div id="') : panel.index('"', start + len('<div id="'))]

        same_text = {"error_message": "Could not open resource for reading"}
        unreachable = alert_id(failure="unreachable", failure_text="Nothing answered at X.", **same_text)
        refused = alert_id(failure="refused", failure_text="X refused the connection.", **same_text)
        assert unreachable != refused


class TestTheBoxIsStableUnderThePoll:
    """The panel is re-swapped every second. Anything in the box that changes,
    or that sits outside the preserved node, is rebuilt on each swap and reads
    as a flicker.
    """

    def _rows(self, attempt: Any) -> int:
        panel = _panel(
            _render(
                video={
                    "connected": False,
                    "failure": "unreachable",
                    "failure_text": "Nothing answered at X.",
                    "failure_action": "Check the camera.",
                    "reconnect_attempt": attempt,
                }
            ),
            "Video",
        )
        return panel.count('class="notice-sub"')

    def test_the_row_count_is_the_same_retrying_or_not(self) -> None:
        assert self._rows(3) == self._rows(0) == 1

    def test_everything_sits_inside_the_preserved_node(self) -> None:
        """``hx-preserve`` keeps only the keyed node. A detail line outside it
        is rebuilt on every swap even when the text has not changed."""
        panel = _panel(
            _render(
                video={
                    "connected": False,
                    "failure": "unreachable",
                    "failure_text": "Nothing answered at X.",
                    "failure_action": "Check the camera.",
                }
            ),
            "Video",
        )
        start = panel.index("hx-preserve")
        preserved = panel[start : panel.index("</div>\n        </div>", start)]
        assert "Nothing answered at X." in preserved
        assert "Check the camera." in preserved

    def test_the_node_id_is_unchanged_while_the_failure_is(self) -> None:
        """A moving id defeats the preserve and re-inserts the node each poll."""

        def token(attempt: int) -> str:
            panel = _panel(
                _render(
                    video={
                        "connected": False,
                        "failure": "unreachable",
                        "failure_text": "Nothing answered at X.",
                        "error_message": "Could not open resource.",
                        "reconnect_attempt": attempt,
                    }
                ),
                "Video",
            )
            start = panel.index('<div id="video-error-')
            return panel[start : panel.index('"', start + len('<div id="'))]

        assert token(1) == token(2) == token(0)


class TestThePipelineWordingIsEvidenceNotTheMessage:
    """ "Could not open resource for reading and writing." reads to an operator
    as a second, unrelated fault - permissions, a disk - when it is the same one
    already named in plain terms above it. It stays reachable because it is what
    a support conversation quotes, but it does not lead.
    """

    def _panel_for(self, **video: Any) -> str:
        payload: dict[str, Any] = {"connected": False}
        payload.update(video)
        return _panel(_render(video=payload), "Video")

    def test_it_says_what_was_seen_then_what_to_try(self) -> None:
        panel = self._panel_for(
            failure="unreachable",
            failure_text="Nothing answered at 192.0.2.10:554.",
            failure_action="Check the camera is powered and on this network.",
            error_message="Could not open resource for reading and writing.",
        )
        assert panel.index("Nothing answered at 192.0.2.10:554.") < panel.index(
            "Check the camera is powered and on this network."
        )

    def test_the_pipeline_wording_is_not_shown(self) -> None:
        panel = self._panel_for(
            failure="unreachable",
            failure_text="Nothing answered at X.",
            failure_action="Check the camera.",
            error_message="Could not open resource for reading and writing.",
        )
        assert "Could not open resource" not in panel

    def test_the_wording_stands_in_when_there_is_no_classification(self) -> None:
        """A local fault has no sentence, so the raw text is the whole message."""
        panel = self._panel_for(failure="unknown", error_message="v4l2src is Linux-only")
        assert "v4l2src is Linux-only" in panel
