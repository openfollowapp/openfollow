# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for select_adapter: backend auto-detection (NM > dhcpcd > psutil), explicit choice, and env override."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import openfollow.network.detect as detect_module
from openfollow.network.detect import select_adapter
from openfollow.network.dhcpcd_adapter import DhcpcdAdapter
from openfollow.network.nm_adapter import NetworkManagerAdapter
from openfollow.network.psutil_adapter import PsutilReadOnlyAdapter

pytestmark = pytest.mark.unit


def _fake_run_factory(active: dict[str, bool]):
    def _run(argv, capture_output=True, text=True, timeout=3, **kwargs):
        unit = argv[-1]
        out = "active" if active.get(unit, False) else "inactive"
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    return _run


class TestSelectAdapter:
    def test_nm_wins_when_active(self, monkeypatch) -> None:
        monkeypatch.setattr(detect_module.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(
            detect_module.subprocess,
            "run",
            _fake_run_factory({"NetworkManager": True, "dhcpcd": False}),
        )
        adapter = select_adapter("auto")
        assert isinstance(adapter, NetworkManagerAdapter)

    def test_dhcpcd_when_nm_inactive_and_conf_present(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(detect_module.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(
            detect_module.subprocess,
            "run",
            _fake_run_factory({"NetworkManager": False, "dhcpcd": True}),
        )
        fake_conf = tmp_path / "dhcpcd.conf"
        fake_conf.write_text("")
        monkeypatch.setattr(detect_module, "_DHCPCD_CONF", fake_conf)
        adapter = select_adapter("auto")
        assert isinstance(adapter, DhcpcdAdapter)

    def test_psutil_when_nothing_active(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(detect_module.shutil, "which", lambda name: None)
        monkeypatch.setattr(
            detect_module.subprocess,
            "run",
            _fake_run_factory({}),
        )
        monkeypatch.setattr(detect_module, "_DHCPCD_CONF", tmp_path / "missing.conf")
        adapter = select_adapter("auto")
        assert isinstance(adapter, PsutilReadOnlyAdapter)

    @pytest.mark.parametrize(
        "name,cls",
        [
            ("nm", NetworkManagerAdapter),
            ("dhcpcd", DhcpcdAdapter),
            ("psutil", PsutilReadOnlyAdapter),
        ],
    )
    def test_explicit_choice(self, name: str, cls) -> None:
        assert isinstance(select_adapter(name), cls)

    def test_unknown_choice_falls_back_to_auto(self, monkeypatch) -> None:
        monkeypatch.setattr(detect_module.shutil, "which", lambda name: None)
        monkeypatch.setattr(detect_module.subprocess, "run", _fake_run_factory({}))
        monkeypatch.setattr(detect_module, "_DHCPCD_CONF", Path("/nope/dhcpcd.conf"))
        adapter = select_adapter("garbage")
        assert isinstance(adapter, PsutilReadOnlyAdapter)

    def test_env_override_wins(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENFOLLOW_NETWORK_BACKEND", "psutil")
        adapter = select_adapter("nm")
        assert isinstance(adapter, PsutilReadOnlyAdapter)

    def test_systemctl_handles_missing_binary(self, monkeypatch) -> None:
        # shutil.which returns None for systemctl → helper returns False.
        monkeypatch.setattr(detect_module.shutil, "which", lambda _name: None)
        assert detect_module._systemctl_is_active("NetworkManager") is False

    def test_systemctl_handles_subprocess_error(self, monkeypatch) -> None:
        monkeypatch.setattr(detect_module.shutil, "which", lambda _name: "/usr/bin/systemctl")

        def _boom(*args, **kwargs):
            raise OSError("denied")

        monkeypatch.setattr(detect_module.subprocess, "run", _boom)
        assert detect_module._systemctl_is_active("NetworkManager") is False
