# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Invariant: a Cage kiosk launcher that backgrounds kanshi must not `exec` the
app, and must tear kanshi down when the app exits.

The web-UI restart calls ``sys.exit`` and relies on Cage exiting so systemd
(``Restart=always``) respawns a fresh session. A backgrounded ``kanshi`` keeps
the Cage Wayland session non-empty, so with ``exec python`` Cage stayed alive
after the app process died – zombie app, black screen, no respawn. Running the
app in the foreground and killing kanshi on exit empties the session so Cage
exits cleanly. This guards every Cage launcher against reintroducing the
``kanshi & exec <app>`` shape.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _repo_root() -> Path:
    here = Path(inspect.getsourcefile(_repo_root) or "")
    return here.resolve().parents[1]


# Every file that launches the app under Cage – the shell / template launchers
# plus the self-install systemd unit rendered by the privilege broker.
_LAUNCHERS = (
    "packaging/debian/session.sh",
    "scripts/ansible/templates/openfollow.service.j2",
    "config/openfollow.service",
    "scripts/run-on-display.sh",
    "openfollow/privilege/systemd_unit.py",
)


def _cage_commands(rel: str) -> list[str]:
    """Return each Cage invocation in a launcher, with shell line continuations
    collapsed so a wrapped command is one string.

    The privilege broker's self-install unit builds its command in Python, so
    render it and read the ExecStart back rather than grepping the source.
    """
    if rel == "openfollow/privilege/systemd_unit.py":
        from openfollow.privilege.systemd_unit import render_service_unit

        text = render_service_unit(
            user="openfollow",
            uid=1000,
            repo_dir=Path("/opt/openfollow"),
            poetry_bin=Path("/usr/bin/poetry"),
        )
    else:
        text = (_repo_root() / rel).read_text()
    joined = text.replace("\\\n", " ")
    return [ln.strip() for ln in joined.splitlines() if "/usr/bin/cage" in ln or re.search(r"\bcage\b --", ln)]


def test_launchers_exist_and_are_found() -> None:
    for rel in _LAUNCHERS:
        assert (_repo_root() / rel).is_file(), rel
    # At least the two kanshi-backgrounding launchers must be present.
    assert _cage_commands("packaging/debian/session.sh")
    assert _cage_commands("scripts/ansible/templates/openfollow.service.j2")


@pytest.mark.parametrize("rel", _LAUNCHERS)
def test_no_kanshi_background_then_exec_app(rel: str) -> None:
    """The regression shape: backgrounding kanshi and then ``exec``-ing the app
    leaves Cage alive after the app dies. Forbid it in every launcher."""
    for cmd in _cage_commands(rel):
        assert not re.search(r"kanshi\b.*&.*\bexec\b", cmd), (
            f"{rel}: backgrounded kanshi followed by `exec` – Cage will not exit when the app dies:\n  {cmd}"
        )


@pytest.mark.parametrize(
    "rel",
    (
        "packaging/debian/session.sh",
        "scripts/ansible/templates/openfollow.service.j2",
        "openfollow/privilege/systemd_unit.py",
    ),
)
def test_kanshi_launcher_tears_down_on_app_exit(rel: str) -> None:
    """A launcher that backgrounds kanshi must (a) run the app in the foreground,
    (b) kill the backgrounded job on exit so the Cage session empties, and
    (c) preserve the app's exit code so the kill doesn't mask a crash."""
    cmds = [c for c in _cage_commands(rel) if "kanshi" in c]
    assert cmds, f"{rel}: expected a kanshi-backgrounding Cage command"
    for cmd in cmds:
        assert "&" in cmd, f"{rel}: kanshi should be backgrounded:\n  {cmd}"
        assert 'kill "$!"' in cmd, (
            f"{rel}: must kill the backgrounded kanshi when the app exits so Cage exits and systemd respawns:\n  {cmd}"
        )
        # The kill must be non-fatal: capture the app's exit status before it
        # and re-emit it, so a crashing app isn't masked by the kill succeeding
        # (and a clean exit isn't reported as failure when kanshi already died).
        assert "rc=$?" in cmd, f"{rel}: capture the app exit status before kill:\n  {cmd}"
        assert "exit $rc" in cmd, f"{rel}: re-emit the app exit code after killing kanshi:\n  {cmd}"
