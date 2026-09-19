# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the boot-autostart read + switch."""

from __future__ import annotations

import subprocess

import pytest

from openfollow.privilege.autostart import (
    AutostartState,
    read_autostart,
    set_autostart,
    unit_file_name,
)
from openfollow.privilege.broker import PrivilegeError
from openfollow.privilege.capabilities import SERVICE_DISABLE, SERVICE_ENABLE
from tests._fake_broker import FakeBroker, make_failure

pytestmark = pytest.mark.unit


def _stub_is_enabled(monkeypatch, stdout: str, *, returncode: int = 0) -> list[list[str]]:
    """Answer ``systemctl is-enabled`` with ``stdout``; return the recorded argvs."""
    monkeypatch.setattr(
        "openfollow.privilege.autostart.shutil.which",
        lambda name: "/usr/bin/systemctl",
    )
    seen: list[list[str]] = []

    def _run(argv, **kw):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, returncode, stdout, "")

    monkeypatch.setattr("openfollow.privilege.autostart.subprocess.run", _run)
    return seen


class TestUnitFileName:
    @pytest.mark.parametrize(
        ("service", "expected"),
        [
            ("openfollow", "openfollow.service"),
            ("openfollow.service", "openfollow.service"),
            ("  openfollow  ", "openfollow.service"),
            ("openfollow.target", "openfollow.target"),
            ("", ""),
            ("   ", ""),
        ],
    )
    def test_suffix(self, service: str, expected: str) -> None:
        assert unit_file_name(service) == expected


class TestReadAutostart:
    @pytest.mark.parametrize(
        ("state", "enabled"),
        [("enabled", True), ("enabled-runtime", True), ("disabled", False), ("indirect", False)],
    )
    def test_switchable_states(self, monkeypatch, state: str, enabled: bool) -> None:
        _stub_is_enabled(monkeypatch, f"{state}\n")
        assert read_autostart("openfollow") == AutostartState(available=True, enabled=enabled)

    @pytest.mark.parametrize("state", ["masked", "masked-runtime", "not-found", "static", "generated", "transient"])
    def test_states_no_switch_can_move_report_unavailable(self, monkeypatch, state: str) -> None:
        """A switch whose only outcome is an error is not offered at all."""
        _stub_is_enabled(monkeypatch, f"{state}\n")
        result = read_autostart("openfollow")
        assert result.available is False
        assert result.reason

    def test_missing_unit_is_read_from_stdout_not_the_exit_code(self, monkeypatch) -> None:
        """``systemctl is-enabled`` prints ``not-found`` on stdout and exits 4.

        Reading the exit code instead would report every uninstalled unit as
        unreadable, which sends the operator looking for a fault that isn't there.
        """
        _stub_is_enabled(monkeypatch, "not-found\n", returncode=4)
        result = read_autostart("openfollow")
        assert result.available is False
        assert "system service" in result.reason

    def test_masked_exits_nonzero_and_is_still_read(self, monkeypatch) -> None:
        _stub_is_enabled(monkeypatch, "masked\n", returncode=1)
        assert read_autostart("openfollow").reason == "This service is masked on this host."

    def test_enabled_state_survives_a_nonzero_exit(self, monkeypatch) -> None:
        _stub_is_enabled(monkeypatch, "enabled\n", returncode=1)
        assert read_autostart("openfollow") == AutostartState(available=True, enabled=True)

    def test_no_systemd_reports_unavailable_without_running_anything(self, monkeypatch) -> None:
        monkeypatch.setattr("openfollow.privilege.autostart.shutil.which", lambda name: None)

        def _explode(*a, **kw):  # pragma: no cover - asserted never to run
            raise AssertionError("no subprocess should be spawned without systemctl")

        monkeypatch.setattr("openfollow.privilege.autostart.subprocess.run", _explode)
        assert read_autostart("openfollow").reason == "This host does not run systemd."

    @pytest.mark.parametrize("exc", [OSError("nope"), subprocess.TimeoutExpired("systemctl", 5)])
    def test_subprocess_failure_reports_unreadable(self, monkeypatch, exc: Exception) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.autostart.shutil.which",
            lambda name: "/usr/bin/systemctl",
        )

        def _run(argv, **kw):
            raise exc

        monkeypatch.setattr("openfollow.privilege.autostart.subprocess.run", _run)
        result = read_autostart("openfollow")
        assert result.available is False
        assert result.reason == "Could not read whether this service starts at boot."

    def test_empty_output_reports_unreadable(self, monkeypatch) -> None:
        _stub_is_enabled(monkeypatch, "   \n")
        assert read_autostart("openfollow").reason == "Could not read whether this service starts at boot."

    @pytest.mark.parametrize("service", ["", "   ", "-rf", "open;follow", "open follow", "../openfollow"])
    def test_unusable_service_name_never_reaches_an_argv(self, monkeypatch, service: str) -> None:
        def _explode(*a, **kw):  # pragma: no cover - asserted never to run
            raise AssertionError("an unusable service name must not be executed")

        monkeypatch.setattr("openfollow.privilege.autostart.shutil.which", lambda name: "/usr/bin/systemctl")
        monkeypatch.setattr("openfollow.privilege.autostart.subprocess.run", _explode)
        result = read_autostart(service)
        assert result.available is False
        assert result.reason == "The configured service name is not valid."

    def test_reads_the_configured_unit(self, monkeypatch) -> None:
        seen = _stub_is_enabled(monkeypatch, "enabled\n")
        read_autostart("station-app")
        assert seen == [["systemctl", "is-enabled", "station-app.service"]]


class TestSetAutostart:
    def test_enabling_a_disabled_unit_uses_the_enable_grant(self, monkeypatch) -> None:
        _stub_is_enabled(monkeypatch, "disabled\n")
        broker = FakeBroker()
        set_autostart(broker, "openfollow", enabled=True)
        assert len(broker.calls) == 1
        assert broker.calls[0].capability == SERVICE_ENABLE
        assert broker.calls[0].argv == ["/usr/bin/systemctl", "enable", "openfollow.service"]

    def test_disabling_an_enabled_unit_uses_the_disable_grant(self, monkeypatch) -> None:
        _stub_is_enabled(monkeypatch, "enabled\n")
        broker = FakeBroker()
        set_autostart(broker, "openfollow", enabled=False)
        assert len(broker.calls) == 1
        assert broker.calls[0].capability == SERVICE_DISABLE
        assert broker.calls[0].argv == ["/usr/bin/systemctl", "disable", "openfollow.service"]

    @pytest.mark.parametrize(("state", "enabled"), [("enabled", True), ("disabled", False)])
    def test_a_unit_already_in_the_requested_state_is_left_alone(self, monkeypatch, state: str, enabled: bool) -> None:
        _stub_is_enabled(monkeypatch, f"{state}\n")
        broker = FakeBroker()
        result = set_autostart(broker, "openfollow", enabled=enabled)
        assert broker.calls == []
        assert result == AutostartState(available=True, enabled=enabled)

    def test_returns_what_the_host_reports_after_the_write(self, monkeypatch) -> None:
        """The answer is re-read from systemd, not assumed from the request.

        A write that systemd accepted but did not act on would otherwise render
        a switch in a position the host does not hold.
        """
        monkeypatch.setattr("openfollow.privilege.autostart.shutil.which", lambda n: "/usr/bin/systemctl")
        states = iter(["disabled\n", "disabled\n"])
        monkeypatch.setattr(
            "openfollow.privilege.autostart.subprocess.run",
            lambda argv, **kw: subprocess.CompletedProcess(argv, 0, next(states), ""),
        )
        result = set_autostart(FakeBroker(), "openfollow", enabled=True)
        assert result == AutostartState(available=True, enabled=False)

    @pytest.mark.parametrize("state", ["masked", "not-found"])
    def test_an_unswitchable_host_is_refused_before_any_elevation(self, monkeypatch, state: str) -> None:
        _stub_is_enabled(monkeypatch, f"{state}\n")
        broker = FakeBroker()
        with pytest.raises(PrivilegeError):
            set_autostart(broker, "openfollow", enabled=False)
        assert broker.calls == []

    def test_a_failed_elevation_propagates(self, monkeypatch) -> None:
        _stub_is_enabled(monkeypatch, "enabled\n")
        broker = FakeBroker(exceptions=[make_failure("Disable a systemd unit: nope")])
        with pytest.raises(PrivilegeError):
            set_autostart(broker, "openfollow", enabled=False)

    def test_the_reason_names_the_unit(self, monkeypatch) -> None:
        """The reason reaches the password prompt, so it has to say what is being changed."""
        _stub_is_enabled(monkeypatch, "disabled\n")
        broker = FakeBroker()
        set_autostart(broker, "openfollow", enabled=True)
        assert "openfollow.service" in broker.calls[0].reason
