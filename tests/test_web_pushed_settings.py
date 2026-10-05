# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Settings another station pushed here: what is held until the warning is
removed, and the locked modal every page shows meanwhile."""

from __future__ import annotations

from pathlib import Path

import pytest

from openfollow.web.pushed_settings import SHOWN, PushedSettings
from tests._template_source import function_body

pytestmark = pytest.mark.unit

_BASE = Path(__file__).resolve().parent.parent / "openfollow" / "web" / "templates" / "base.tpl"


def _stations(register: PushedSettings) -> list[str]:
    return [push.station for push in register.snapshot()[0]]


class TestPushedSettings:
    def test_a_push_records_who_sent_what_and_when(self) -> None:
        register = PushedSettings(clock=lambda: 1234.5)
        push = register.record("Stage Left", "198.51.100.7", "Grid", "No space left on device")
        assert (push.at, push.station, push.address, push.what, push.backup_error) == (
            1234.5,
            "Stage Left",
            "198.51.100.7",
            "Grid",
            "No space left on device",
        )
        assert register.snapshot() == ((push,), 0)

    def test_pushes_list_newest_first_with_rising_ids(self) -> None:
        register = PushedSettings()
        first = register.record("A", "198.51.100.1", "Grid")
        second = register.record("B", "198.51.100.2", "Camera")
        assert second.id > first.id
        assert _stations(register) == ["B", "A"]

    def test_past_the_shown_count_older_pushes_are_only_counted(self) -> None:
        register = PushedSettings()
        for n in range(SHOWN + 3):
            register.record(f"S{n}", "198.51.100.1", "Grid")
        pushes, earlier = register.snapshot()
        assert [push.station for push in pushes] == [f"S{n}" for n in range(SHOWN + 2, 2, -1)]
        assert earlier == 3

    def test_removing_up_to_the_newest_seen_clears_everything_seen(self) -> None:
        register = PushedSettings()
        for n in range(SHOWN + 2):
            newest = register.record(f"S{n}", "198.51.100.1", "Grid")
        register.remove(newest.id)
        assert register.snapshot() == ((), 0)

    def test_a_push_that_arrived_after_the_page_looked_stays(self) -> None:
        register = PushedSettings()
        seen = register.record("A", "198.51.100.1", "Grid")
        register.record("B", "198.51.100.2", "Camera")
        register.remove(seen.id)
        assert _stations(register) == ["B"]

    def test_removing_older_pushes_keeps_the_count_of_newer_overflow(self) -> None:
        register = PushedSettings()
        pushes = [register.record(f"S{n}", "198.51.100.1", "Grid") for n in range(SHOWN + 2)]
        # The two oldest overflowed; a removal older than both keeps the count.
        register.remove(pushes[0].id - 1)
        assert register.snapshot()[1] == 2

    def test_a_removal_older_than_the_overflow_counts_only_what_stays(self) -> None:
        # The Operator Screen removes up to the push its menu opened with; more arrived since.
        register = PushedSettings()
        pushes = [register.record(f"S{n}", "198.51.100.1", "Grid") for n in range(3)]
        for n in range(3, SHOWN + 5):
            register.record(f"S{n}", "198.51.100.1", "Grid")
        register.remove(pushes[2].id)
        listed, earlier = register.snapshot()
        assert len(listed) == SHOWN
        assert earlier == 2


def _poller() -> str:
    base = _BASE.read_text(encoding="utf-8")
    start = base.index(" (function () {\n const POLL_MS = 3000;")
    return base[start : base.index(" })();", start)]


class TestPushedSettingsModal:
    def test_only_pages_rendered_for_a_station_poll(self) -> None:
        base = _BASE.read_text(encoding="utf-8")
        script = base.index(" (function () {\n const POLL_MS = 3000;")
        assert base.rindex(" % if defined('config') and config:", 0, script) > base.rindex(" % end", 0, script)

    def test_the_modal_is_locked_and_names_its_one_way_out(self) -> None:
        poll = function_body(_poller(), "pollPushedSettings")
        modal = poll[poll.index("openModal({") :]
        assert "dismissable: false," in modal
        assert (
            "{ label: 'Remove Warning', kind: 'primary', onClick: (btn) => removePushedSettingsWarning(latest, btn) }"
            in (modal)
        )

    def test_it_never_replaces_another_open_modal(self) -> None:
        poll = function_body(_poller(), "pollPushedSettings")
        assert poll.index("if (!ours && !root.hidden) return;") < poll.index("openModal({")

    def test_it_reopens_only_for_a_newer_push(self) -> None:
        poll = function_body(_poller(), "pollPushedSettings")
        assert poll.index("if (ours && latest === shownLatest) return;") < poll.index("openModal({")

    def test_it_closes_once_the_warning_is_gone_elsewhere(self) -> None:
        poll = function_body(_poller(), "pollPushedSettings")
        empty = poll[poll.index("if (!state.pushes.length) {") :]
        assert empty.index("if (ours) closeModal();") < empty.index("return;")

    def test_a_device_password_prompt_stays_answerable(self) -> None:
        poll = function_body(_poller(), "pollPushedSettings")
        prompt = poll.index("if (document.querySelector('#privilege-password-modal [role=\"dialog\"]')) {")
        yielded = poll[prompt : poll.index("return;", prompt)]
        assert "if (ours) closeModal();" in yielded
        assert prompt < poll.index("openModal({")

    def test_removing_closes_only_the_modal_it_removed(self) -> None:
        remove = function_body(_poller(), "removePushedSettingsWarning")
        assert "if (root && root.dataset.pushedSettings === '1' && shownLatest === upto) closeModal();" in remove
        assert remove.count("closeModal();") == 1

    def test_removing_sends_the_newest_push_the_page_showed(self) -> None:
        remove = function_body(_poller(), "removePushedSettingsWarning")
        assert "fetch('/api/pushed-settings/remove', {" in remove
        assert "body: JSON.stringify({ upto })," in remove
        assert remove.rindex("closeModal();") > remove.index("if (!res.ok) {")

    def test_a_failed_removal_reports_like_a_failed_save(self) -> None:
        remove = function_body(_poller(), "removePushedSettingsWarning")
        assert "saveError.show(card, await saveError.fromResponse(res), 'Not removed.', btn.parentElement);" in remove
        assert "saveError.show(card, saveError.UNREACHABLE, 'Not removed.', btn.parentElement);" in remove

    def test_pushed_names_are_text_never_markup(self) -> None:
        body = function_body(_poller(), "pushedSettingsBody")
        assert "innerHTML" not in body
        assert "item.append(at, sender + ' pushed ' + push.what);" in body

    def test_a_push_without_a_backup_says_so_in_its_line(self) -> None:
        body = function_body(_poller(), "pushedSettingsBody")
        caution = body[body.index("if (push.backup_error) {") :]
        assert "caution.className = 'field-caution-msg';" in caution
        assert "caution.textContent = 'No backup was made: ' + push.backup_error + '.';" in caution

    def test_the_box_says_the_settings_already_apply(self) -> None:
        body = function_body(_poller(), "pushedSettingsBody")
        assert "box.className = 'notice';" in body
        assert "lead.textContent = 'The pushed settings are already applied.';" in body
