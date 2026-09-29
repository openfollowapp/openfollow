# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Saving the diagnostics bundle to a drive: one export at a time, on a worker for the HUD."""

from __future__ import annotations

import logging
import threading

import pytest

import openfollow.runtime.diagnostics_export as de
from openfollow.runtime.diagnostics_export import DiagnosticsExport, ExportBusy, ExportStatus
from openfollow.runtime.removable_media import Media, MediaError, WriteResult

pytestmark = pytest.mark.unit

_STICK = Media("sda1", "/dev/sda1", "SanDisk Ultra", "SanDisk Ultra · FAT32 · 32 GB", None, True)


def _saved(name: str) -> WriteResult:
    return WriteResult(name, _STICK, f"Saved {name} to SanDisk Ultra.", "It can be removed now.")


class TestRun:
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
        status = export.run("sda1", "SanDisk Ultra", de.WEB)
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
        export.run("sda1", "x", de.WEB)
        assert export.run("sda1", "x", de.HUD).generation == 2

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
        status = export.run("sda1", "SanDisk Ultra", de.WEB)
        assert (status.phase, status.ok, status.message) == (de.DONE, False, message)
        # The slot is free again.
        export._build, export._write = (lambda: ("b.txt", "x")), (lambda *a: _saved("b.txt"))
        assert export.run("sda1", "SanDisk Ultra", de.WEB).ok is True


class TestJournal:
    """Each save leaves one line in the journal, which the next bundle's log tail carries."""

    def test_a_saved_bundle_names_the_file_it_got_and_the_device(self, caplog: pytest.LogCaptureFixture) -> None:
        export = DiagnosticsExport(lambda: ("ofdiag-rig.txt", "x"), lambda *a: _saved("ofdiag-rig-1.txt"))
        with caplog.at_level(logging.DEBUG, logger=de.__name__):
            export.run("sda1", "SanDisk Ultra", de.HUD)
        assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
            (logging.INFO, "Saved the diagnostics bundle as ofdiag-rig-1.txt to SanDisk Ultra (/dev/sda1).")
        ]

    def test_a_refused_save_is_a_warning_with_its_reason(self, caplog: pytest.LogCaptureFixture) -> None:
        def write(*_a: object) -> WriteResult:
            raise MediaError("The USB storage device is full.")

        export = DiagnosticsExport(lambda: ("ofdiag-rig.txt", "x"), write)
        with caplog.at_level(logging.DEBUG, logger=de.__name__):
            export.run("sda1", "SanDisk Ultra", de.WEB)
        assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
            (logging.WARNING, "Saving the diagnostics bundle to SanDisk Ultra failed: The USB storage device is full.")
        ]


def test_a_failure_with_its_own_next_step_keeps_it() -> None:
    def write(*_a: object) -> WriteResult:
        raise MediaError("b.txt was written, but it could not be unmounted.", action="Wait before removing it.")

    status = DiagnosticsExport(lambda: ("b.txt", "x"), write).run("sda1", "SanDisk Ultra", de.WEB)
    assert (status.ok, status.message, status.action) == (
        False,
        "b.txt was written, but it could not be unmounted.",
        "Wait before removing it.",
    )


class _StartOnRelease:
    """The slot, starting a HUD export the moment a finishing one frees it."""

    def __init__(self, export: DiagnosticsExport) -> None:
        self._lock, self._export, self.fired = threading.Lock(), export, False

    def acquire(self, blocking: bool = True) -> bool:
        return self._lock.acquire(blocking)

    def release(self) -> None:
        self._lock.release()
        if not self.fired:
            self.fired = True
            self._export.start("sdb1", "Other stick", de.HUD)


def test_run_returns_its_own_result_though_another_export_starts_as_it_ends() -> None:
    hold = threading.Event()

    def build() -> tuple[str, str]:
        if slot.fired:
            hold.wait(2)
        return "b.txt", "x"

    export = DiagnosticsExport(build, lambda *a: _saved("b.txt"))
    slot = _StartOnRelease(export)
    export._slot = slot  # type: ignore[assignment]
    status = export.run("sda1", "SanDisk Ultra", de.WEB)
    try:
        assert (status.origin, status.phase, status.ok) == (de.WEB, de.DONE, True)
        assert export.status().origin == de.HUD
    finally:
        hold.set()


def test_the_last_result_of_each_origin_is_kept() -> None:
    export = DiagnosticsExport(lambda: ("b.txt", "x"), lambda *a: _saved("b.txt"))
    assert export.last_done(de.HUD) is None
    export.run("sda1", "SanDisk Ultra", de.HUD)
    export.run("sda1", "SanDisk Ultra", de.WEB)
    hud, web = export.last_done(de.HUD), export.last_done(de.WEB)
    assert (hud.origin, hud.generation, web.origin, web.generation) == (de.HUD, 1, de.WEB, 2)  # type: ignore[union-attr]


class TestOneAtATime:
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
        with pytest.raises(ExportBusy, match="still running"):
            export.run("sda1", "SanDisk Ultra", de.WEB)
        assert export.start("sda1", "SanDisk Ultra", de.HUD) is False
        release.set()


class TestStart:
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

    def test_a_worker_that_cannot_start_reports_it(self, monkeypatch) -> None:  # noqa: ANN001
        def _refuse(self: threading.Thread) -> None:
            raise RuntimeError("can't start new thread")

        monkeypatch.setattr(de.threading.Thread, "start", _refuse)
        export = DiagnosticsExport(lambda: ("b.txt", "x"), lambda *a: _saved("b.txt"))
        assert export.start("sda1", "SanDisk Ultra", de.HUD) is True
        status = export.status()
        assert (status.phase, status.ok, status.message) == (de.DONE, False, "The export could not be started.")
        monkeypatch.undo()
        assert export.run("sda1", "SanDisk Ultra", de.WEB).ok is True
