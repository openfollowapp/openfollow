# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Video Source section reports why the last attempt failed.

The reason lived only on the Statistics panel and the HUD, so the page where
the fix gets typed was the one page that did not say what was wrong.
"""

from __future__ import annotations

import pytest
from bottle import template

from openfollow.configuration import AppConfig
from openfollow.web import server as _server_module  # noqa: F401 - registers tpl path

pytestmark = pytest.mark.unit

_NOTICE = '<div class="notice error"'


def _render(**ctx: object) -> str:
    base: dict[str, object] = {
        "config": AppConfig(),
        "saved": False,
        "available_inputs": [("testpattern", "Media Gallery"), ("rtsp", "RTSP")],
        "input_html_fragments": {},
    }
    base.update(ctx)
    return template("partials/video_source", **base)


class TestVideoSourceFailureNotice:
    def test_a_classified_failure_is_reported(self) -> None:
        body = _render(video_failure="unreachable", video_failure_text="Nothing answered at 192.0.2.10:554.")
        assert "Nothing answered at 192.0.2.10:554." in body

    def test_it_sits_above_the_form_controls(self) -> None:
        """Not something to find by reading past the Source Type picker."""
        body = _render(video_failure="refused", video_failure_text="192.0.2.10:554 refused the connection.")
        assert body.index(_NOTICE) < body.index("video-source-type")

    def test_a_healthy_source_shows_no_notice(self) -> None:
        assert _NOTICE not in _render(video_failure="none", video_failure_text="")

    @pytest.mark.parametrize("failure", ["none", "unknown"])
    def test_an_unclassified_failure_shows_no_notice(self, failure: str) -> None:
        """No second line here carries the element's wording, so a bare
        unreadable verdict is worse than nothing."""
        body = _render(video_failure=failure, video_failure_text="Video from X failed for a reason we don't know.")
        assert _NOTICE not in body

    def test_the_section_renders_without_the_keys_at_all(self) -> None:
        """A caller with no runtime stats must still get a usable form."""
        assert _NOTICE not in _render()

    def test_it_announces_politely(self) -> None:
        """The operator came here to deal with it; it must not interrupt an edit."""
        body = _render(video_failure="unauthorized", video_failure_text="192.0.2.10:554 rejected the login.")
        assert 'role="status"' in body
        assert 'aria-live="polite"' in body

    def test_the_sentence_is_escaped(self) -> None:
        body = _render(video_failure="unreachable", video_failure_text="<script>alert(1)</script>")
        assert "<script>alert" not in body


class TestEveryRenderPathCarriesTheKeys:
    """The banner reached the operator only after a Save, because only the POST
    render passed the keys. Both GET paths render the same partial.
    """

    def test_the_shared_builder_supplies_them(self) -> None:
        """Every render of the partial goes through this one helper, so a new
        render path cannot silently lose the failure line again."""
        from openfollow.web.routes import _build_input_template_data

        data = _build_input_template_data(
            AppConfig(), {"failure": "unreachable", "failure_text": "Nothing answered at 192.0.2.10:554."}
        )
        assert data["video_failure"] == "unreachable"
        assert data["video_failure_text"] == "Nothing answered at 192.0.2.10:554."

    def test_it_defaults_to_no_failure_without_stats(self) -> None:
        """The Setup Wizard shares the helper and has no stats to hand."""
        from openfollow.web.routes import _build_input_template_data

        data = _build_input_template_data(AppConfig())
        assert data["video_failure"] == "none"
        assert data["video_failure_text"] == ""

    def test_a_provider_returning_none_is_survivable(self) -> None:
        from openfollow.web.routes import _build_input_template_data

        assert _build_input_template_data(AppConfig(), None)["video_failure"] == "none"


class TestTheSaveResponseDoesNotNameTheOldSource:
    """The swap is asynchronous and the section does not poll.

    Rendering the live verdict into the save response names the URL the
    operator has just replaced, and it stays there until a page reload - so a
    correct fix reads as though it failed.
    """

    def test_a_save_carries_no_failure(self) -> None:
        from openfollow.web.routes import _build_input_template_data

        stale = {"failure": "unreachable", "failure_text": "Nothing answered at 192.0.2.10:554."}
        saved = _build_input_template_data(AppConfig(), None)  # what the save path now passes
        live = _build_input_template_data(AppConfig(), stale)

        assert saved["video_failure_text"] == ""
        assert live["video_failure_text"] == stale["failure_text"]

    def test_the_section_renders_clean_after_a_save(self) -> None:
        assert _NOTICE not in _render(saved=True, video_failure="none", video_failure_text="")
