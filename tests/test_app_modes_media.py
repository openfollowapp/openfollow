# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Operator Screen's drive picker, export screen and status-corner row."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest

import openfollow.runtime.app_modes_media as mm
from openfollow.input.gamepad import SettingsMenuInput
from openfollow.runtime.diagnostics_export import COLLECTING, DONE, HUD, WEB, WRITING, ExportStatus
from openfollow.runtime.removable_media import Media, MediaWatch

pytestmark = pytest.mark.unit

STICK = Media("sda1", "/dev/sda1", "SanDisk Ultra", "SanDisk Ultra · FAT32 · 32 GB", None, True)
MAC = Media(
    "sdc1", "/dev/sdc1", "WD Passport (MAC)", "WD Passport (MAC) · APFS · 2.0 TB", None, False, "APFS can't be written"
)
CARD = Media("sdb1", "/dev/sdb1", "SD Reader", "SD Reader · exFAT · 128 GB", "/media/card", True)


class _Watch:
    def __init__(self, media: list[Media], listed: bool = True) -> None:
        self.media, self.listed = media, listed
        self.running = False

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    def snapshot(self) -> tuple[list[Media], bool]:
        return list(self.media), self.listed


class _Export:
    def __init__(self, status: ExportStatus | None = None, *, busy: bool = False) -> None:
        self._status = status or ExportStatus()
        self.busy = busy
        self.started: list[tuple[str, str, str]] = []
        self.done: dict[str, ExportStatus] = {}

    def status(self) -> ExportStatus:
        return self._status

    def last_done(self, origin: str) -> ExportStatus | None:
        if origin in self.done:
            return self.done[origin]
        status = self._status
        return status if status.phase == DONE and status.origin == origin else None

    def start(self, media_id: str, drive: str, origin: str) -> bool:
        self.started.append((media_id, drive, origin))
        return not self.busy


def _app(*, export: Any = None, media: list[Media] | None = None, inputs: list[SettingsMenuInput] | None = None) -> Any:
    feed = list(inputs or [])
    gamepad = SimpleNamespace(read_settings_menu_input=lambda: feed.pop(0) if feed else SettingsMenuInput())
    app = SimpleNamespace(
        _runtime_services=SimpleNamespace(diagnostics_export=export, _status_flags={}, _privilege_broker=None),
        _input_manager=SimpleNamespace(gamepad_handler=gamepad),
        _media_picker_active=False,
        _media_picker_title="",
        _media_picker_action="",
        _media_picker_selected="",
        _media_watch=_Watch(media if media is not None else [MAC, STICK, CARD]),
        _media_export_active=False,
        _media_export_seen=0,
        _media_export_badge_at=None,
        _settings_menu_active=False,
    )
    app.back_to_settings = 0

    def _enter_settings_menu(**_kw: Any) -> None:
        app._settings_menu_active = True
        app.back_to_settings += 1

    app._enter_settings_menu = _enter_settings_menu
    return app


def _done(
    ok: bool, origin: str = HUD, generation: int = 1, message: str = "Saved b.txt to SanDisk Ultra."
) -> ExportStatus:
    return ExportStatus(DONE, origin, "SanDisk Ultra", ok, message, generation, "It can be removed now." if ok else "")


class TestEntering:
    def test_opens_the_picker_and_starts_listing(self) -> None:
        app = _app(export=_Export())
        mm.enter_diagnostics_export(app)
        assert (app._media_picker_active, app._media_picker_title, app._media_picker_action) == (
            True,
            "SAVE DIAGNOSTICS",
            "diagnostics",
        )
        assert app._media_watch.running is True

    @pytest.mark.parametrize("phase", [COLLECTING, WRITING])
    def test_a_running_export_shows_its_progress(self, phase: str) -> None:
        app = _app(export=_Export(ExportStatus(phase, HUD, "SanDisk Ultra", generation=1)))
        mm.enter_diagnostics_export(app)
        assert (app._media_export_active, app._media_picker_active) == (True, False)

    def test_a_failure_the_badge_points_to_shows_its_reason_and_clears_the_row(self) -> None:
        app = _app(export=_Export(_done(False, message="The USB storage device is full.")))
        app._runtime_services._status_flags[mm.BADGE_KEY] = ("error", "Export failed: The USB storage device is full")
        mm.enter_diagnostics_export(app)
        assert app._media_export_active is True
        assert app._runtime_services._status_flags[mm.BADGE_KEY] is None

    def test_a_success_row_is_cleared_and_the_picker_opens(self) -> None:
        app = _app(export=_Export(_done(True)))
        app._runtime_services._status_flags[mm.BADGE_KEY] = ("success", "Diagnostics saved to SanDisk Ultra")
        app._media_export_badge_at = 5.0
        mm.enter_diagnostics_export(app)
        assert (app._media_picker_active, app._media_export_badge_at) == (True, None)
        assert app._runtime_services._status_flags[mm.BADGE_KEY] is None

    def test_nothing_without_an_export(self) -> None:
        app = _app(export=None)
        mm.enter_diagnostics_export(app)
        assert (app._media_picker_active, app._media_export_active) == (False, False)

    def test_the_first_picker_builds_its_watch_with_the_broker(self) -> None:
        app = _app(export=_Export())
        app._media_watch = None
        assert isinstance(mm._watch(app), MediaWatch)
        assert mm._watch(app) is app._media_watch


class TestPicker:
    def test_highlights_the_first_writable_drive(self) -> None:
        app = _app()
        media, _ = mm.picker_rows(app)
        assert mm.picker_index(app, media) == 1

    def test_the_highlight_follows_the_drive_not_the_row(self) -> None:
        app = _app()
        app._media_picker_selected = "sdb1"
        app._media_watch.media = [CARD, MAC, STICK]
        assert mm.picker_index(app, app._media_watch.media) == 0

    def test_no_writable_drive_highlights_nothing(self) -> None:
        app = _app(media=[MAC])
        assert mm.picker_index(app, [MAC]) == -1

    @pytest.mark.parametrize(("key", "expected"), [("ArrowDown", "sdb1"), ("ArrowUp", "sdb1")])
    def test_arrows_skip_what_cannot_be_written_and_wrap(self, key: str, expected: str) -> None:
        app = _app()
        app._media_picker_active = True
        assert mm.handle_media_key(app, key) is True
        assert app._media_picker_selected == expected

    def test_arrows_with_nothing_writable(self) -> None:
        app = _app(media=[MAC])
        app._media_picker_active = True
        mm.handle_media_key(app, "ArrowDown")
        assert app._media_picker_selected == ""

    def test_enter_starts_the_export_on_the_worker_and_shows_it(self) -> None:
        export = _Export()
        app = _app(export=export)
        mm.enter_media_picker(app, mm.EXPORT_TITLE, mm.DIAGNOSTICS)
        mm.handle_media_key(app, "Enter")
        assert export.started == [("sda1", "SanDisk Ultra", HUD)]
        assert (app._media_picker_active, app._media_export_active, app._media_watch.running) == (False, True, False)
        assert app.back_to_settings == 0

    def test_enter_while_another_export_runs_shows_that_one(self) -> None:
        export = _Export(busy=True)
        app = _app(export=export)
        mm.enter_media_picker(app, mm.EXPORT_TITLE, mm.DIAGNOSTICS)
        mm.handle_media_key(app, "Enter")
        assert app._media_export_active is True

    def test_a_pick_with_no_export_returns_to_settings(self) -> None:
        app = _app(export=None)
        mm.enter_media_picker(app, mm.EXPORT_TITLE, mm.DIAGNOSTICS)
        mm.handle_media_key(app, "Enter")
        assert (app._media_export_active, app.back_to_settings) == (False, 1)

    def test_enter_with_nothing_writable_does_nothing(self) -> None:
        export = _Export()
        app = _app(export=export, media=[MAC])
        mm.enter_media_picker(app, mm.EXPORT_TITLE, mm.DIAGNOSTICS)
        mm.handle_media_key(app, "Enter")
        assert (export.started, app._media_picker_active) == ([], True)

    def test_escape_returns_to_settings(self) -> None:
        app = _app()
        mm.enter_media_picker(app, mm.EXPORT_TITLE, mm.DIAGNOSTICS)
        mm.handle_media_key(app, "Escape")
        assert (app._media_picker_active, app._media_watch.running, app.back_to_settings) == (False, False, 1)

    def test_other_keys_are_swallowed(self) -> None:
        app = _app()
        mm.enter_media_picker(app, mm.EXPORT_TITLE, mm.DIAGNOSTICS)
        assert mm.handle_media_key(app, "x") is True
        assert app._media_picker_active is True


class TestExportScreen:
    def test_escape_while_running_leaves_it_running(self) -> None:
        app = _app(export=_Export(ExportStatus(COLLECTING, HUD, "SanDisk Ultra", generation=3)))
        app._media_export_active = True
        mm.handle_media_key(app, "Escape")
        assert (app._media_export_active, app._media_export_seen, app.back_to_settings) == (False, 0, 1)

    def test_escape_after_the_result_marks_it_seen(self) -> None:
        app = _app(export=_Export(_done(True, generation=3)))
        app._media_export_active = True
        mm.handle_media_key(app, "Escape")
        assert app._media_export_seen == 3

    def test_enter_after_the_result_picks_a_drive_again(self) -> None:
        app = _app(export=_Export(_done(False, generation=2)))
        app._media_export_active = True
        mm.handle_media_key(app, "Enter")
        assert (app._media_export_active, app._media_picker_active, app._media_export_seen) == (False, True, 2)

    def test_enter_while_running_does_nothing(self) -> None:
        app = _app(export=_Export(ExportStatus(WRITING, HUD, "SanDisk Ultra", generation=1)))
        app._media_export_active = True
        mm.handle_media_key(app, "Enter")
        assert app._media_export_active is True

    def test_not_open_is_not_handled(self) -> None:
        assert mm.handle_media_key(_app(), "Enter") is False

    def test_other_keys_are_swallowed(self) -> None:
        app = _app(export=_Export(_done(True)))
        app._media_export_active = True
        assert mm.handle_media_key(app, "x") is True


class TestGamepad:
    def test_inactive_is_not_handled(self) -> None:
        assert mm.process_media_input(_app()) is False

    def test_without_an_input_manager_it_still_owns_input(self) -> None:
        app = _app()
        app._media_picker_active = True
        app._input_manager = None
        assert mm.process_media_input(app) is True

    def test_dpad_moves_and_a_starts(self) -> None:
        export = _Export()
        app = _app(
            export=export,
            inputs=[
                SettingsMenuInput(down_pressed=True),
                SettingsMenuInput(up_pressed=True),
                SettingsMenuInput(confirm_pressed=True),
            ],
        )
        mm.enter_media_picker(app, mm.EXPORT_TITLE, mm.DIAGNOSTICS)
        mm.process_media_input(app)
        assert app._media_picker_selected == "sdb1"
        mm.process_media_input(app)
        assert app._media_picker_selected == "sda1"
        mm.process_media_input(app)
        assert export.started == [("sda1", "SanDisk Ultra", HUD)]

    def test_b_leaves_the_picker(self) -> None:
        app = _app(inputs=[SettingsMenuInput(cancel_pressed=True)])
        mm.enter_media_picker(app, mm.EXPORT_TITLE, mm.DIAGNOSTICS)
        mm.process_media_input(app)
        assert (app._media_picker_active, app.back_to_settings) == (False, 1)

    def test_a_and_b_on_the_export_screen(self) -> None:
        app = _app(export=_Export(_done(True)), inputs=[SettingsMenuInput(confirm_pressed=True)])
        app._media_export_active = True
        mm.process_media_input(app)
        assert app._media_picker_active is True
        app2 = _app(export=_Export(_done(True)), inputs=[SettingsMenuInput(cancel_pressed=True)])
        app2._media_export_active = True
        mm.process_media_input(app2)
        assert (app2._media_export_active, app2.back_to_settings) == (False, 1)

    def test_no_button_on_the_export_screen(self) -> None:
        app = _app(export=_Export(_done(True)))
        app._media_export_active = True
        assert mm.process_media_input(app) is True
        assert app._media_export_active is True


class TestStatusCorner:
    def test_a_success_the_operator_left_before_is_posted_and_ages_out(self) -> None:
        app = _app(export=_Export(_done(True)))
        mm.check_diagnostics_export(app, now=100.0)
        assert app._runtime_services._status_flags[mm.BADGE_KEY] == ("success", "Diagnostics saved to SanDisk Ultra")
        mm.check_diagnostics_export(app, now=114.9)
        assert app._runtime_services._status_flags[mm.BADGE_KEY] is not None
        mm.check_diagnostics_export(app, now=115.0)
        assert app._runtime_services._status_flags[mm.BADGE_KEY] is None

    def test_a_failure_stays(self) -> None:
        app = _app(export=_Export(_done(False, message="The USB storage device is full.")))
        mm.check_diagnostics_export(app, now=100.0)
        mm.check_diagnostics_export(app, now=10_000.0)
        assert app._runtime_services._status_flags[mm.BADGE_KEY] == (
            "error",
            "Export failed: The USB storage device is full",
        )

    def test_a_later_success_replaces_a_failure(self) -> None:
        export = _Export(_done(False, generation=1))
        app = _app(export=export)
        mm.check_diagnostics_export(app, now=1.0)
        export._status = _done(True, generation=2)
        mm.check_diagnostics_export(app, now=2.0)
        assert app._runtime_services._status_flags[mm.BADGE_KEY][0] == "success"

    def test_a_later_web_success_clears_a_failure_without_posting(self) -> None:
        export = _Export(_done(False, generation=1))
        app = _app(export=export)
        mm.check_diagnostics_export(app, now=1.0)
        export._status = _done(True, origin=WEB, generation=2)
        mm.check_diagnostics_export(app, now=2.0)
        assert app._runtime_services._status_flags[mm.BADGE_KEY] is None

    @pytest.mark.parametrize(
        ("first_ok", "web_ok", "kind"),
        [(True, True, "success"), (False, False, "error")],
        ids=["web-success-keeps-a-success-row", "web-failure-keeps-a-failure-row"],
    )
    def test_any_other_web_result_leaves_the_row(self, first_ok: bool, web_ok: bool, kind: str) -> None:
        export = _Export(_done(first_ok, generation=1))
        app = _app(export=export)
        mm.check_diagnostics_export(app, now=1.0)
        export._status = _done(web_ok, origin=WEB, generation=2)
        mm.check_diagnostics_export(app, now=2.0)
        assert app._runtime_services._status_flags[mm.BADGE_KEY][0] == kind

    def test_a_hud_result_is_posted_though_a_web_export_already_took_the_slot(self) -> None:
        export = _Export(ExportStatus(COLLECTING, WEB, "Other stick", generation=2))
        export.done[HUD] = _done(False, generation=1, message="The USB storage device is full.")
        app = _app(export=export)
        mm.check_diagnostics_export(app, now=1.0)
        row = app._runtime_services._status_flags[mm.BADGE_KEY]
        assert row == ("error", "Export failed: The USB storage device is full")

    def test_results_are_read_in_the_order_the_exports_ran(self) -> None:
        # Both finished between two housekeeping ticks: the later web success clears the earlier HUD failure.
        export = _Export(_done(True, origin=WEB, generation=2))
        export.done[HUD] = _done(False, generation=1)
        app = _app(export=export)
        mm.check_diagnostics_export(app, now=1.0)
        assert app._runtime_services._status_flags[mm.BADGE_KEY] is None
        assert app._media_export_seen == 2

    def test_a_result_shown_on_screen_is_not_posted(self) -> None:
        app = _app(export=_Export(_done(True)))
        app._media_export_active = True
        mm.check_diagnostics_export(app, now=1.0)
        assert mm.BADGE_KEY not in app._runtime_services._status_flags
        assert app._media_export_seen == 1

    @pytest.mark.parametrize(
        "status",
        [_done(True, origin=WEB), _done(True, generation=0), ExportStatus(COLLECTING, HUD, "x", generation=1)],
        ids=["web-export", "already-seen", "still-running"],
    )
    def test_nothing_else_is_posted(self, status: ExportStatus) -> None:
        app = _app(export=_Export(status))
        mm.check_diagnostics_export(app, now=1.0)
        assert mm.BADGE_KEY not in app._runtime_services._status_flags

    def test_nothing_without_an_export(self) -> None:
        app = _app(export=None)
        mm.check_diagnostics_export(app)
        assert app._runtime_services._status_flags == {}

    def test_reads_the_clock_when_none_is_given(self) -> None:
        app = _app(export=_Export(_done(True)))
        mm.check_diagnostics_export(app)
        assert app._media_export_badge_at is not None


class TestMediaWatch:
    def test_lists_until_stopped_and_keeps_the_last_listing(self) -> None:
        calls: list[int] = []
        stop = threading.Event()

        def list_fn() -> list[Media]:
            calls.append(1)
            if len(calls) == 2:
                stop.set()
            return [STICK] if len(calls) == 1 else [CARD]

        watch = MediaWatch(list_fn, interval_s=0)
        watch._run(stop)
        assert calls == [1, 1]
        # The listing that finished after the stop is not served.
        assert watch.snapshot() == ([STICK], True)

    def test_a_listing_that_raises_lists_nothing_and_the_worker_carries_on(self, caplog) -> None:  # noqa: ANN001
        calls: list[int] = []
        stop = threading.Event()

        def list_fn() -> list[Media]:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("unexpected listing")
            stop.set()
            return [CARD]

        watch = MediaWatch(list_fn, interval_s=0)
        watch._run(stop)
        assert calls == [1, 1]
        assert watch.snapshot() == ([], True)
        assert "unexpected listing" in caplog.text

    def test_start_runs_one_worker_and_stop_ends_it(self, monkeypatch) -> None:  # noqa: ANN001
        started: list[threading.Event] = []

        class _Thread:
            def __init__(self, target: Any, args: tuple, daemon: bool, name: str) -> None:
                assert (daemon, name) == (True, "MediaWatch")
                started.append(args[0])

            def start(self) -> None:
                pass

        monkeypatch.setattr("openfollow.runtime.removable_media.threading.Thread", _Thread)
        watch = MediaWatch(list)
        watch.start()
        watch.start()
        assert len(started) == 1 and watch.snapshot() == ([], False)
        watch.stop()
        assert started[0].is_set()
        watch.stop()
        watch.start()
        assert len(started) == 2 and started[1] is not started[0]
