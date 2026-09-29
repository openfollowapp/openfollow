# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Saving the diagnostics bundle to a drive: one export at a time, on a worker."""

from __future__ import annotations

import logging
import threading
from types import SimpleNamespace
from typing import Any

import pytest

import openfollow.runtime.diagnostics_export as de
from openfollow.runtime.diagnostics_export import DiagnosticsExport, ExportStatus, status_lines
from openfollow.runtime.removable_media import Media, MediaError, WriteResult

pytestmark = pytest.mark.unit

_STICK = Media("sda1", "/dev/sda1", "SanDisk Ultra", "SanDisk Ultra · FAT32 · 32 GB", None, True)


def _saved(name: str) -> WriteResult:
    return WriteResult(name, _STICK, f"Saved {name} to SanDisk Ultra.", "It can be removed now.")


class _InlineThread:
    """Runs the worker's target on start(), so an export has finished when start() returns."""

    def __init__(self, target: Any, args: tuple, daemon: bool, name: str) -> None:
        assert (daemon, name) == (True, "DiagnosticsExport")
        self._target, self._args = target, args

    def start(self) -> None:
        self._target(*self._args)


@pytest.fixture
def inline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(de, "threading", SimpleNamespace(Thread=_InlineThread, Lock=threading.Lock))


def _export(export: DiagnosticsExport, origin: str = de.WEB) -> ExportStatus:
    assert export.start("sda1", "SanDisk Ultra", origin) is True
    return export.status()


@pytest.mark.usefixtures("inline")
class TestExport:
    def test_collects_then_writes_and_reports(self) -> None:
        phases: list[str] = []
        writes: list[tuple[str, str, bytes]] = []
        export: DiagnosticsExport

        def build() -> tuple[str, str]:
            phases.append(export.status().phase)
            return "ofdiag-rig.txt", "bündle"

        def write(media_id: str, filename: str, data: bytes) -> WriteResult:
            phases.append(export.status().phase)
            writes.append((media_id, filename, data))
            return _saved(filename)

        export = DiagnosticsExport(build, write)
        status = _export(export)
        assert phases == [de.COLLECTING, de.WRITING]
        assert writes == [("sda1", "ofdiag-rig.txt", "bündle".encode())]
        assert status == ExportStatus(
            de.DONE,
            de.WEB,
            "SanDisk Ultra",
            True,
            "Saved ofdiag-rig.txt to SanDisk Ultra.",
            1,
            "It can be removed now.",
        )

    def test_each_export_counts(self) -> None:
        export = DiagnosticsExport(lambda: ("b.txt", ""), lambda *a: _saved("b.txt"))
        _export(export)
        assert _export(export, de.HUD).generation == 2

    @pytest.mark.parametrize(
        ("build_error", "write_error", "message"),
        [
            (RuntimeError("boom"), None, "The diagnostics could not be collected."),
            (None, MediaError("The USB storage device is full."), "The USB storage device is full."),
            (None, RuntimeError("boom"), "The file could not be saved."),
        ],
        ids=["collect-failed", "drive-refused", "write-crashed"],
    )
    def test_a_failure_is_reported_and_frees_the_slot(self, build_error, write_error, message) -> None:  # noqa: ANN001
        def build() -> tuple[str, str]:
            if build_error:
                raise build_error
            return "b.txt", "x"

        def write(*_a: object) -> WriteResult:
            if write_error:
                raise write_error
            return _saved("b.txt")

        export = DiagnosticsExport(build, write)
        status = _export(export)
        assert (status.phase, status.ok, status.message) == (de.DONE, False, message)
        # The slot is free again.
        export._build, export._write = (lambda: ("b.txt", "x")), (lambda *a: _saved("b.txt"))
        assert _export(export).ok is True

    def test_a_failure_with_its_own_next_step_keeps_it(self) -> None:
        def write(*_a: object) -> WriteResult:
            raise MediaError("b.txt was written, but it could not be unmounted.", action="Wait before removing it.")

        status = _export(DiagnosticsExport(lambda: ("b.txt", "x"), write))
        assert (status.ok, status.message, status.action) == (
            False,
            "b.txt was written, but it could not be unmounted.",
            "Wait before removing it.",
        )

    def test_the_last_result_of_each_origin_is_kept(self) -> None:
        export = DiagnosticsExport(lambda: ("b.txt", "x"), lambda *a: _saved("b.txt"))
        assert export.last_done(de.HUD) is None
        _export(export, de.HUD)
        _export(export, de.WEB)
        hud, web = export.last_done(de.HUD), export.last_done(de.WEB)
        assert (hud.origin, hud.generation, web.origin, web.generation) == (de.HUD, 1, de.WEB, 2)  # type: ignore[union-attr]


@pytest.mark.usefixtures("inline")
class TestJournal:
    """Each save leaves one line in the journal, which the next bundle's log tail carries."""

    def test_a_saved_bundle_names_the_file_it_got_and_the_device(self, caplog: pytest.LogCaptureFixture) -> None:
        export = DiagnosticsExport(lambda: ("ofdiag-rig.txt", "x"), lambda *a: _saved("ofdiag-rig-1.txt"))
        with caplog.at_level(logging.DEBUG, logger=de.__name__):
            _export(export, de.HUD)
        assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
            (logging.INFO, "Saved the diagnostics bundle as ofdiag-rig-1.txt to SanDisk Ultra (/dev/sda1).")
        ]

    def test_a_refused_save_is_a_warning_with_its_reason(self, caplog: pytest.LogCaptureFixture) -> None:
        def write(*_a: object) -> WriteResult:
            raise MediaError("The USB storage device is full.")

        export = DiagnosticsExport(lambda: ("ofdiag-rig.txt", "x"), write)
        with caplog.at_level(logging.DEBUG, logger=de.__name__):
            _export(export)
        assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
            (logging.WARNING, "Saving the diagnostics bundle to SanDisk Ultra failed: The USB storage device is full.")
        ]


class TestWorker:
    def test_reports_progress_at_once_and_finishes_on_the_worker(self) -> None:
        release = threading.Event()
        done = threading.Event()

        def build() -> tuple[str, str]:
            release.wait(5)
            return "b.txt", "x"

        def write(*_a: object) -> WriteResult:
            done.set()
            return _saved("b.txt")

        export = DiagnosticsExport(build, write)
        assert export.start("sda1", "SanDisk Ultra", de.HUD) is True
        assert (export.status().phase, export.status().origin) == (de.COLLECTING, de.HUD)
        release.set()
        assert done.wait(5)
        for worker in [t for t in threading.enumerate() if t.name == "DiagnosticsExport"]:
            worker.join(5)
        assert (export.status().phase, export.status().ok) == (de.DONE, True)

    def test_a_second_export_is_refused_while_one_runs(self) -> None:
        release = threading.Event()
        entered = threading.Event()

        def build() -> tuple[str, str]:
            entered.set()
            release.wait(5)
            return "b.txt", "x"

        export = DiagnosticsExport(build, lambda *a: _saved("b.txt"))
        assert export.start("sda1", "SanDisk Ultra", de.HUD) is True
        assert entered.wait(5)
        assert export.start("sdb1", "Other stick", de.WEB) is False
        assert export.status().origin == de.HUD
        release.set()

    def test_a_worker_that_cannot_start_reports_it_and_frees_the_slot(self, monkeypatch) -> None:  # noqa: ANN001
        def _refuse(self: threading.Thread) -> None:
            raise RuntimeError("can't start new thread")

        monkeypatch.setattr(de.threading.Thread, "start", _refuse)
        export = DiagnosticsExport(lambda: ("b.txt", "x"), lambda *a: _saved("b.txt"))
        assert export.start("sda1", "SanDisk Ultra", de.HUD) is True
        status = export.status()
        assert (status.phase, status.ok, status.message) == (de.DONE, False, "The export could not be started.")
        monkeypatch.undo()
        monkeypatch.setattr(de, "threading", SimpleNamespace(Thread=_InlineThread, Lock=threading.Lock))
        assert _export(export).ok is True


@pytest.mark.parametrize(
    ("status", "lines"),
    [
        (ExportStatus(), ("", "", None)),
        (
            ExportStatus(de.COLLECTING, de.HUD, "SanDisk Ultra"),
            ("Collecting diagnostics", "The export continues in the background.", None),
        ),
        (
            ExportStatus(de.WRITING, de.WEB, "SanDisk Ultra"),
            ("Writing to SanDisk Ultra", "The export continues in the background.", None),
        ),
        (
            ExportStatus(
                de.DONE, de.HUD, "SanDisk Ultra", True, "Saved b.txt to SanDisk Ultra.", 1, "It can be removed now."
            ),
            ("Saved b.txt to SanDisk Ultra.", "It can be removed now.", True),
        ),
        (
            ExportStatus(de.DONE, de.WEB, "SanDisk Ultra", False, "The USB storage device is full.", 1),
            ("The USB storage device is full.", "Pick a USB storage device to try again.", False),
        ),
        (
            ExportStatus(de.DONE, de.HUD, "SanDisk Ultra", False, "b.txt was written, but not unmounted.", 1, "Wait."),
            ("b.txt was written, but not unmounted.", "Wait.", False),
        ),
    ],
    ids=["idle", "collecting", "writing", "saved", "failed", "failed-with-its-own-next-step"],
)
def test_the_status_says_what_is_happening_and_the_next_step(status: ExportStatus, lines: tuple) -> None:
    # One wording for the Operator Screen and the web UI.
    assert status_lines(status) == lines
