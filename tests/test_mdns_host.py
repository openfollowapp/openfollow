# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The host name this station answers to over mDNS."""

from __future__ import annotations

import subprocess
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from openfollow import mdns_host
from openfollow.station_fqdn import mdns_name, web_ui_host
from tests._threads import HeldThreads, InlineThread

pytestmark = pytest.mark.unit

# A Mac named by DHCP: the kernel reports the DHCP name, Bonjour publishes another.
_KERNEL = "studio"
_BONJOUR = "Studio-Mac"


class _Scutil:
    """``subprocess.run`` for ``scutil --get LocalHostName``, answering in turn."""

    def __init__(self, *answers: str, returncode: int = 0) -> None:
        self._answers = list(answers)
        self._returncode = returncode
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> SimpleNamespace:
        self.calls.append((argv, kwargs))
        answer = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        return SimpleNamespace(returncode=self._returncode, stdout=answer + "\n")


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def kernel_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("openfollow.privilege.device_repair.current_hostname", lambda: _KERNEL)


@pytest.fixture
def mac(monkeypatch: pytest.MonkeyPatch, kernel_name: None) -> None:
    monkeypatch.setattr(mdns_host.sys, "platform", "darwin")


def _install(**kwargs: Any) -> mdns_host.LocalHostNameReader:
    reader = mdns_host.LocalHostNameReader(**kwargs)
    mdns_host.set_reader(reader)
    return reader


def test_macos_advertises_the_name_bonjour_publishes(mac: None) -> None:
    scutil = _Scutil(_BONJOUR)
    _install(run=scutil, thread_factory=InlineThread)
    assert mdns_name() == f"{_BONJOUR}.local"
    assert web_ui_host("") == f"{_BONJOUR}.local"
    argv, kwargs = scutil.calls[0]
    assert argv == ["/usr/sbin/scutil", "--get", "LocalHostName"]
    assert 0 < kwargs["timeout"] <= 5


def test_linux_keeps_the_kernel_name_and_never_runs_scutil(monkeypatch: pytest.MonkeyPatch, kernel_name: None) -> None:
    monkeypatch.setattr(mdns_host.sys, "platform", "linux")

    def _never(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("scutil on Linux")

    _install(run=_never, thread_factory=InlineThread)
    assert mdns_name() == f"{_KERNEL}.local"


@pytest.mark.parametrize(
    "run",
    [
        pytest.param(_Scutil("LocalHostName: not set", returncode=1), id="not-set"),
        pytest.param(_Scutil(""), id="empty"),
        pytest.param(_Scutil("not a name"), id="spaces"),
        pytest.param(_Scutil("-studio"), id="leading-hyphen"),
        pytest.param(_Scutil("a" * 64), id="too-long"),
    ],
)
def test_macos_without_a_usable_bonjour_name_keeps_the_kernel_name(mac: None, run: _Scutil) -> None:
    _install(run=run, thread_factory=InlineThread)
    assert mdns_name() == f"{_KERNEL}.local"


@pytest.mark.parametrize(
    "error",
    [
        OSError("no scutil"),
        subprocess.TimeoutExpired(["scutil"], 2.0),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
    ],
    ids=["missing", "timeout", "undecodable"],
)
def test_a_failed_read_keeps_the_kernel_name(mac: None, error: Exception) -> None:
    def _fail(*_args: object, **_kwargs: object) -> object:
        raise error

    _install(run=_fail, thread_factory=InlineThread)
    assert mdns_name() == f"{_KERNEL}.local"


def test_a_read_with_no_thread_to_run_on_keeps_the_kernel_name(mac: None) -> None:
    def _no_thread(**_kwargs: object) -> object:
        raise RuntimeError("can't start new thread")

    _install(run=_Scutil(_BONJOUR), thread_factory=_no_thread)
    assert mdns_name() == f"{_KERNEL}.local"


def test_the_frame_clock_never_waits_for_the_first_read(mac: None) -> None:
    # No name rather than the kernel name, which nothing may answer.
    threads = HeldThreads()
    _install(run=_Scutil(_BONJOUR), thread_factory=threads)
    assert mdns_name() == ""
    assert web_ui_host("") == ""
    assert web_ui_host("of-1.stage.example.com") == "of-1.stage.example.com"
    assert threads.started == 1


def test_one_read_runs_at_a_time(mac: None) -> None:
    threads = HeldThreads()
    _install(run=_Scutil(_BONJOUR), thread_factory=threads)
    for _ in range(3):
        mdns_name()
    assert threads.started == 1


def test_a_caller_waits_for_the_first_read_within_its_budget(mac: None) -> None:
    _install(run=_Scutil(_BONJOUR))
    assert mdns_name(wait_s=10.0) == f"{_BONJOUR}.local"


def test_a_caller_waits_no_longer_than_its_budget(mac: None) -> None:
    release = threading.Event()

    def _hung(*_args: object, **_kwargs: object) -> SimpleNamespace:
        release.wait(10.0)
        return SimpleNamespace(returncode=0, stdout=_BONJOUR)

    _install(run=_hung)
    try:
        assert mdns_name(wait_s=0.05) == ""
    finally:
        release.set()


def test_a_rename_shows_after_the_refresh(mac: None) -> None:
    clock = _Clock()
    scutil = _Scutil(_BONJOUR, "Stage-Mac")
    _install(run=scutil, clock=clock, thread_factory=InlineThread, refresh_s=10.0)
    assert mdns_name() == f"{_BONJOUR}.local"
    clock.now += 9.0
    assert mdns_name() == f"{_BONJOUR}.local"
    assert len(scutil.calls) == 1
    clock.now += 1.0
    assert mdns_name() == "Stage-Mac.local"
    assert len(scutil.calls) == 2


class _FirstReadOnly:
    """Runs the first read at once and holds every later one in flight."""

    def __init__(self) -> None:
        self.held = HeldThreads()
        self._first = True

    def __call__(self, **kwargs: Any) -> object:
        if self._first:
            self._first = False
            return InlineThread(**kwargs)
        return self.held(**kwargs)


def test_a_refresh_still_running_serves_the_last_name(mac: None) -> None:
    clock = _Clock()
    threads = _FirstReadOnly()
    _install(run=_Scutil(_BONJOUR), clock=clock, thread_factory=threads, refresh_s=10.0)
    assert mdns_name() == f"{_BONJOUR}.local"
    clock.now += 10.0
    assert mdns_name(wait_s=10.0) == f"{_BONJOUR}.local"
    assert threads.held.started == 1
