# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for the install-and-repair helper functions:

- ``openfollow.privilege.device_repair._unit_has_install_section`` –
  distinguishes a runtime-injected unit (no ``[Install]``) from a
  unit that ``systemctl disable`` can actually flip.
"""

from __future__ import annotations

import subprocess

import pytest

from openfollow.privilege.device_repair import _unit_has_install_section

pytestmark = pytest.mark.unit


# ---------- _unit_has_install_section -----------------------------------


class TestUnitHasInstallSection:
    """``probe_service_disabled`` calls this to distinguish a
    ``cloud-init.target``-style runtime-only unit (no ``[Install]``
    section, ``systemctl disable`` is a no-op) from one we can
    actually flip."""

    def test_returns_true_when_systemctl_missing(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.shutil.which",
            lambda name: None,
        )
        # Defaults to True so callers don't silently mask a real gap
        # on hosts without systemctl.
        assert _unit_has_install_section("x") is True

    def test_returns_true_on_subprocess_error(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.shutil.which",
            lambda name: "/usr/bin/systemctl",
        )

        def _raise(*args, **kw):
            raise OSError("ENOENT")

        monkeypatch.setattr("openfollow.privilege.device_repair.subprocess.run", _raise)

        assert _unit_has_install_section("x") is True

    def test_returns_true_on_nonzero_returncode(self, monkeypatch) -> None:
        """``systemctl cat <missing-unit>`` exits non-zero – treat
        as disable-able rather than special-case it. The probe layer
        will already have handled the missing-unit case via
        ``is-enabled``'s ``not-installed`` state."""
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.shutil.which",
            lambda name: "/usr/bin/systemctl",
        )
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess([], 1, "", "no such unit"),
        )

        assert _unit_has_install_section("ghost.target") is True

    def test_returns_true_when_install_header_present(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.shutil.which",
            lambda name: "/usr/bin/systemctl",
        )
        cat_output = (
            "# /lib/systemd/system/foo.service\n"
            "[Unit]\n"
            "Description=Foo\n"
            "[Service]\n"
            "ExecStart=/bin/foo\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess([], 0, cat_output, ""),
        )

        assert _unit_has_install_section("foo.service") is True

    def test_returns_false_when_install_header_absent(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.shutil.which",
            lambda name: "/usr/bin/systemctl",
        )
        cat_output = "# /lib/systemd/system/cloud-init.target\n[Unit]\nDescription=Cloud-init target\n"
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess([], 0, cat_output, ""),
        )

        assert _unit_has_install_section("cloud-init.target") is False
