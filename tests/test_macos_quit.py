# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for :mod:`openfollow.macos_quit`: the answer to the quit Apple Event.

The Apple Event Manager is replaced by a fake library, so nothing is installed
in the test process; the handler the fake receives is the real C callback and
is called the way the Apple Event Manager calls it.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from typing import Any

import pytest

from openfollow import macos_quit

pytestmark = pytest.mark.unit

_AEVT = int.from_bytes(b"aevt", "big")
_QUIT = int.from_bytes(b"quit", "big")


class _FakeInstall:
    """``AEInstallEventHandler``: records each call, answers (or raises) ``result``."""

    def __init__(self, result: int | Exception) -> None:
        self.result = result
        self.calls: list[tuple[Any, ...]] = []
        self.argtypes: Any = None
        self.restype: Any = None

    def __call__(self, *args: Any) -> int:
        self.calls.append(args)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _FakeCoreServices:
    def __init__(self, result: int = 0) -> None:
        self.AEInstallEventHandler = _FakeInstall(result)


def _install(on_quit: Any, core_services: Any) -> bool:
    return macos_quit.install_quit_handler(on_quit, load_library=lambda: core_services)


def _handler(core_services: _FakeCoreServices) -> Any:
    return core_services.AEInstallEventHandler.calls[-1][2]


@pytest.fixture
def on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")


@pytest.mark.usefixtures("on_macos")
class TestOnMacOS:
    def test_installs_an_application_handler_for_the_quit_event(self) -> None:
        core_services = _FakeCoreServices()

        assert _install(lambda: None, core_services) is True

        [(event_class, event_id, _handler_fn, refcon, is_system)] = core_services.AEInstallEventHandler.calls
        assert (event_class, event_id) == (_AEVT, _QUIT)
        assert refcon is None
        # The application's own handler table, not the system-wide one.
        assert not is_system

    def test_quit_event_runs_on_quit_and_answers_no_error(self) -> None:
        core_services = _FakeCoreServices()
        quits: list[bool] = []
        _install(lambda: quits.append(True), core_services)

        assert _handler(core_services)(None, None, None) == 0
        assert quits == [True]

    def test_every_quit_event_reaches_on_quit(self) -> None:
        core_services = _FakeCoreServices()
        quits: list[bool] = []
        _install(lambda: quits.append(True), core_services)

        _handler(core_services)(None, None, None)
        _handler(core_services)(None, None, None)

        assert quits == [True, True]

    def test_quit_event_is_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        core_services = _FakeCoreServices()
        _install(lambda: None, core_services)

        with caplog.at_level(logging.INFO, logger="openfollow.macos_quit"):
            _handler(core_services)(None, None, None)

        assert [r.getMessage() for r in caplog.records] == [
            "Quit requested by macOS (Dock, logout, restart or shutdown)."
        ]

    def test_failing_on_quit_answers_an_error_and_logs_it(self, caplog: pytest.LogCaptureFixture) -> None:
        core_services = _FakeCoreServices()

        def _boom() -> None:
            raise RuntimeError("no main loop")

        _install(_boom, core_services)

        with caplog.at_level(logging.ERROR, logger="openfollow.macos_quit"):
            result = _handler(core_services)(None, None, None)

        # errAEEventFailed: the sender learns the app is not quitting instead of waiting for it.
        assert result == -10000
        assert any(r.exc_info and "no main loop" in str(r.exc_info[1]) for r in caplog.records)

    @pytest.mark.parametrize("error", [-1708, -50, 1])
    def test_refused_install_reports_false_and_warns(self, error: int, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="openfollow.macos_quit"):
            assert _install(lambda: None, _FakeCoreServices(result=error)) is False

        assert any(str(error) in r.getMessage() for r in caplog.records)

    def test_unloadable_event_manager_reports_false_and_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        def _load() -> Any:
            raise OSError("image not found")

        with caplog.at_level(logging.WARNING, logger="openfollow.macos_quit"):
            assert macos_quit.install_quit_handler(lambda: None, load_library=_load) is False

        assert [r.levelno for r in caplog.records] == [logging.WARNING]

    def test_missing_install_function_reports_false_and_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="openfollow.macos_quit"):
            assert _install(lambda: None, object()) is False

        assert [r.levelno for r in caplog.records] == [logging.WARNING]

    def test_raising_install_reports_false_and_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        core_services = _FakeCoreServices()
        core_services.AEInstallEventHandler.result = ctypes.ArgumentError("argument 3: wrong type")

        with caplog.at_level(logging.WARNING, logger="openfollow.macos_quit"):
            assert _install(lambda: None, core_services) is False

        assert [r.levelno for r in caplog.records] == [logging.WARNING]

    def test_default_loader_opens_core_services(self, monkeypatch: pytest.MonkeyPatch) -> None:
        opened: list[str] = []
        core_services = _FakeCoreServices()

        def _cdll(path: str) -> _FakeCoreServices:
            opened.append(path)
            return core_services

        monkeypatch.setattr(ctypes, "CDLL", _cdll)

        assert macos_quit.install_quit_handler(lambda: None) is True
        assert opened == ["/System/Library/Frameworks/CoreServices.framework/CoreServices"]


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_other_platforms_install_nothing(platform: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    loads: list[bool] = []

    def _load() -> Any:
        loads.append(True)
        return _FakeCoreServices()

    assert macos_quit.install_quit_handler(lambda: None, load_library=_load) is False
    assert loads == []
