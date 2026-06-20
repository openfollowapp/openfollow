# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Invariant: the Ansible Poetry install stays runtime-only by default.

Consumer Pis must not pull the dev/CI group (ruff, pytest, mypy, bandit,
pip-audit, mutmut, pre-commit, hypothesis, coverage) – none of it runs on the
device, and a dev-only package's broken download must not be able to fail a
production install. The dev group is opt-in via ``openfollow_install_dev`` for a
testing Pi that runs ``make ci`` (``make ci-remote``).
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _playbook_path() -> Path:
    """Resolve the playbook relative to this test file so the assertion
    works regardless of pytest's cwd."""
    here = Path(inspect.getsourcefile(_playbook_path) or "")
    return here.resolve().parents[1] / "scripts" / "ansible" / "install-raspberry-pi.yml"


def _install_task_argv() -> str:
    """Return the install task's templated ``argv`` expression as a single
    whitespace-collapsed line."""
    text = _playbook_path().read_text()
    m = re.search(
        r"- name: Install Python dependencies with Poetry\b.*?argv: >-\n(?P<argv>.*?)\n\s+args:",
        text,
        re.DOTALL,
    )
    assert m, "Install Python dependencies with Poetry task / argv block not found"
    return " ".join(line.strip() for line in m.group("argv").splitlines())


def test_default_install_restricts_to_main_group() -> None:
    """The production path passes ``--only main`` so the dev group is skipped."""
    assert "'--only', 'main'" in _install_task_argv()


def test_dev_group_is_opt_in_behind_the_toggle() -> None:
    """``--only main`` is dropped only when ``openfollow_install_dev`` is set –
    Poetry's ``--only`` is exclusive, so the toggle removes the flag rather than
    adding ``--with dev``."""
    argv = _install_task_argv()
    assert re.search(r"if openfollow_install_dev[^)]*else \['--only', 'main'\]", argv), argv


def test_detection_extra_stays_gated() -> None:
    """The detection extra remains conditional on ``install_detection_extra``."""
    assert re.search(r"\['-E', 'detection'\] if install_detection_extra", _install_task_argv())


def test_dev_toggle_defaults_off() -> None:
    text = _playbook_path().read_text()
    assert re.search(r"^\s+openfollow_install_dev: false\s*$", text, re.MULTILINE)


def test_no_separate_detection_install_task_remains() -> None:
    """The split detection/non-detection install tasks were collapsed into one
    templated task; a reappearing second task usually means the dev-group guard
    regressed back to a bare ``poetry install``."""
    text = _playbook_path().read_text()
    assert text.count("- name: Install Python dependencies with Poetry") == 1
