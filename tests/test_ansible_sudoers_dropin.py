# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Invariant: the Ansible sudoers drop-in matches the capability registry.

This module is the automated check: every Capability with a non-empty
``sudoers_pattern`` must appear verbatim in the playbook (excepting
two legacy compatibility lines documented in the playbook itself).
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from openfollow.privilege.capabilities import ALL_CAPABILITIES

pytestmark = pytest.mark.unit


def _playbook_path() -> Path:
    """Resolve the playbook relative to this test file so the assertion
    works regardless of pytest's cwd."""
    here = Path(inspect.getsourcefile(_playbook_path) or "")
    return here.resolve().parents[1] / "scripts" / "ansible" / "install-raspberry-pi.yml"


_NOPASSWD_RE = re.compile(
    r"^\s+\{\{ openfollow_user \}\} ALL=\(root\) NOPASSWD:\s+(?P<rule>.+?)\s*$",
    re.MULTILINE,
)
# Legacy compatibility line for old argvs without broker capabilities.
# Ignored by diff check but pinned here so edits fail loudly.
_LEGACY_LINE = (
    "/usr/bin/systemctl restart {{ openfollow_service_name }}, "
    "/bin/systemctl restart {{ openfollow_service_name }}, "
    "/bin/bash {{ openfollow_repo_dir }}/scripts/install-system-deps.sh"
)


def _extract_dropin_rules() -> list[str]:
    """Return the list of NOPASSWD argv-patterns in the playbook block,
    in file order."""
    text = _playbook_path().read_text()
    return [m.group("rule") for m in _NOPASSWD_RE.finditer(text)]


def test_legacy_compat_line_present_and_first() -> None:
    rules = _extract_dropin_rules()
    assert rules, "no NOPASSWD rules found in playbook"
    assert rules[0] == _LEGACY_LINE


def test_every_registry_capability_appears_in_dropin() -> None:
    rules = _extract_dropin_rules()
    legacy_excluded = [r for r in rules if r != _LEGACY_LINE]
    registry = {cap.sudoers_pattern for cap in ALL_CAPABILITIES if cap.sudoers_pattern}
    playbook = set(legacy_excluded)
    missing_from_playbook = registry - playbook
    extra_in_playbook = playbook - registry
    assert missing_from_playbook == set(), (
        f"Capability sudoers_patterns missing from the playbook: {sorted(missing_from_playbook)}"
    )
    assert extra_in_playbook == set(), (
        "Playbook has NOPASSWD lines not covered by any Capability: "
        f"{sorted(extra_in_playbook)}. Either add a Capability or "
        "remove the playbook line."
    )


def test_dropin_does_not_grant_unscoped_install() -> None:
    """Defensive: ``/usr/bin/install *`` must not be present as it would allow
    the device user to overwrite any file as root."""
    rules = _extract_dropin_rules()
    assert "/usr/bin/install *" not in rules
