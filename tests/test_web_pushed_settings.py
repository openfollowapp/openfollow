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

    def test_a_removal_past_the_newest_push_still_counts_later_ones(self) -> None:
        register = PushedSettings()
        register.record("A", "198.51.100.1", "Grid")
        register.remove(1000)
        for n in range(SHOWN + 1):
            register.record(f"S{n}", "198.51.100.1", "Grid")
        listed, earlier = register.snapshot()
        assert (len(listed), earlier) == (SHOWN, 1)

    def test_received_counts_every_push_of_the_run_removed_ones_too(self) -> None:
        register = PushedSettings()
        assert register.received == 0
        seen = register.record("A", "198.51.100.1", "Grid")
        register.record("B", "198.51.100.2", "Camera")
        register.remove(seen.id)
        assert register.received == 2

    def test_each_run_has_its_own_name(self) -> None:
        # Ids start at 1 on every run, so a page's ids mean nothing in another run.
        assert PushedSettings().run != PushedSettings().run


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
            "{ label: 'Remove Warning', kind: 'primary', "
            "onClick: (btn) => removePushedSettingsWarning(latest, state.run, btn) }" in (modal)
        )

    def test_a_locked_modal_shows_no_close_button(self) -> None:
        # The global ``button`` rule sets ``display``, which outranks the browser's own ``[hidden]`` rule.
        base = _BASE.read_text(encoding="utf-8")
        assert "closeBtn.hidden = !_modalDismissable;" in function_body(base, "openModal")
        assert "\n .modal-close[hidden] { display: none; }\n" in base

    def test_it_never_replaces_another_open_modal(self) -> None:
        poll = function_body(_poller(), "pollPushedSettings")
        assert poll.index("if (!ours && !root.hidden) return;") < poll.index("openModal({")

    def test_it_redraws_whenever_its_list_changes(self) -> None:
        # Not only on a newer push: a removal elsewhere can drop older rows and leave the newest.
        poll = function_body(_poller(), "pollPushedSettings")
        key = "const key = state.run + ':' + state.earlier + ':' + state.pushes.map((push) => push.id).join(',');"
        assert poll.index(key) < poll.index("if (ours && key === shownKey) return;") < poll.index("openModal({")
        assert poll.index("shownKey = key;") < poll.index("openModal({")

    def test_a_page_counts_pushes_from_its_first_answer(self) -> None:
        poll = function_body(_poller(), "pollPushedSettings")
        first = poll.index("if (!baseline) baseline = { run: state.run, received: state.received };")
        since = poll.index(
            "const pushedSinceLoad = state.run === baseline.run "
            "? state.received !== baseline.received : state.received > 0;"
        )
        assert first < since < poll.index("if (!state.pushes.length) {")

    def test_a_removal_elsewhere_reloads_a_page_loaded_before_the_push(self) -> None:
        # Even one that never saw it pending; another open modal is left alone, and an older push just closes.
        poll = function_body(_poller(), "pollPushedSettings")
        empty = poll[poll.index("if (!state.pushes.length) {") :]
        empty = empty[: empty.index("return;")]
        assert (
            "if (pushedSinceLoad && (ours || root.hidden)) window.location.reload();\n else if (ours) closeModal();"
            in (empty)
        )

    def test_a_hidden_tab_asks_only_once_shown(self) -> None:
        poll = function_body(_poller(), "pollPushedSettings")
        assert poll.index("if (document.hidden && baseline) return;") < poll.index("fetch('/api/pushed-settings'")
        assert "document.addEventListener('visibilitychange', pollPushedSettings);" in _poller()

    def test_a_device_password_prompt_stays_answerable(self) -> None:
        poll = function_body(_poller(), "pollPushedSettings")
        prompt = poll.index("if (document.querySelector('#privilege-password-modal [role=\"dialog\"]')) {")
        yielded = poll[prompt : poll.index("return;", prompt)]
        assert "if (ours) closeModal();" in yielded
        assert prompt < poll.index("openModal({")

    def test_removing_reloads_the_page_to_show_the_pushed_values(self) -> None:
        remove = function_body(_poller(), "removePushedSettingsWarning")
        assert remove.rstrip().endswith("window.location.reload();")
        assert "closeModal" not in remove

    def test_a_removal_from_before_a_restart_reloads_rather_than_failing(self) -> None:
        remove = function_body(_poller(), "removePushedSettingsWarning")
        failed = remove[remove.index("if (!res.ok && res.status !== 409) {") :]
        assert failed.index("saveError.show(") < failed.index("return;")

    def test_removing_sends_the_newest_push_the_page_showed_and_its_run(self) -> None:
        remove = function_body(_poller(), "removePushedSettingsWarning")
        assert "fetch('/api/pushed-settings/remove', {" in remove
        assert "body: JSON.stringify({ upto, run })," in remove

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
        assert "caution.textContent = NO_BACKUP_MADE;" in caution

    def test_the_box_says_the_settings_already_apply(self) -> None:
        body = function_body(_poller(), "pushedSettingsBody")
        assert "box.className = 'notice';" in body
        assert "lead.textContent = 'The pushed settings are already applied.';" in body
