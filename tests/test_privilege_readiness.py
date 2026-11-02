# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann, Michel Honold, and Vinzenz Schultz
"""Tests for the page banner shown when privileged actions can't run."""

from __future__ import annotations

import sys

import pytest

from openfollow.privilege import readiness
from openfollow.privilege.broker import INSTALL_SUDO_REMEDY, REINSTALL_REMEDY
from openfollow.privilege.capabilities import PACKAGE_SELF_UPDATE, CapabilityState
from openfollow.privilege.readiness import NO_SUDO, PACKAGED_PREFIX, RULES_MISSING, current
from tests._fake_broker import FakeBroker

pytestmark = pytest.mark.unit


def _broker(state: CapabilityState) -> FakeBroker:
    return FakeBroker(states_map={PACKAGE_SELF_UPDATE.name: state})


@pytest.fixture()
def sudo(monkeypatch: pytest.MonkeyPatch):
    def _set(installed: bool) -> None:
        monkeypatch.setattr(readiness, "sudo_installed", lambda: installed)

    return _set


@pytest.mark.parametrize("prefix", [PACKAGED_PREFIX, "/home/dev/.venv", "/Applications/OpenFollow.app"])
@pytest.mark.parametrize("broker", [None, _broker(CapabilityState.PASSWORDLESS)], ids=["no-broker", "broker"])
def test_a_missing_sudo_warns_on_every_install(sudo, prefix: str, broker: FakeBroker | None) -> None:
    sudo(False)
    assert current(broker, prefix=prefix) is NO_SUDO


@pytest.mark.parametrize(
    ("state", "warning"),
    [
        (CapabilityState.PASSWORDLESS, None),
        (CapabilityState.NEEDS_PASSWORD, RULES_MISSING),
        (CapabilityState.UNAVAILABLE, RULES_MISSING),
    ],
)
def test_a_packaged_install_warns_when_the_update_rule_is_not_passwordless(sudo, state, warning) -> None:
    sudo(True)
    assert current(_broker(state), prefix=PACKAGED_PREFIX) is warning


@pytest.mark.parametrize("state", [CapabilityState.NEEDS_PASSWORD, CapabilityState.UNAVAILABLE])
def test_a_source_install_never_warns_about_rules(sudo, state: CapabilityState) -> None:
    sudo(True)
    assert current(_broker(state), prefix="/home/dev/.venv") is None


def test_no_broker_never_warns_about_rules(sudo) -> None:
    sudo(True)
    assert current(None, prefix=PACKAGED_PREFIX) is None


def test_the_running_interpreter_decides_the_install_kind(sudo, monkeypatch: pytest.MonkeyPatch) -> None:
    sudo(True)
    broker = _broker(CapabilityState.NEEDS_PASSWORD)
    monkeypatch.setattr(sys, "prefix", PACKAGED_PREFIX)
    assert current(broker) is RULES_MISSING
    monkeypatch.setattr(sys, "prefix", "/home/dev/.venv")
    assert current(broker) is None


def test_each_warning_carries_its_remedy() -> None:
    assert (NO_SUDO.step, RULES_MISSING.step) == (INSTALL_SUDO_REMEDY, REINSTALL_REMEDY)
