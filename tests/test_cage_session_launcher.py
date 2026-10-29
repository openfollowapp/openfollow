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
import os
import re
import subprocess
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


def _launcher_text(rel: str) -> str:
    """The privilege broker's self-install unit builds its command in Python, so
    render it rather than grepping the source."""
    if rel == "openfollow/privilege/systemd_unit.py":
        from openfollow.privilege.systemd_unit import render_service_unit

        return render_service_unit(
            user="openfollow",
            uid=1000,
            repo_dir=Path("/opt/openfollow"),
            poetry_bin=Path("/usr/bin/poetry"),
        )
    return (_repo_root() / rel).read_text()


def _cage_commands(rel: str) -> list[str]:
    """Return each Cage invocation in a launcher, with shell line continuations
    collapsed so a wrapped command is one string."""
    joined = _launcher_text(rel).replace("\\\n", " ")
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


def _kiosk_env_source(rel: str) -> str:
    """Return the text that sets a launcher's environment. ``session.sh``
    inherits the .deb unit's, so its unit is read in its place."""
    if rel == "packaging/debian/session.sh":
        return _launcher_text("packaging/debian/openfollow.service")
    return _launcher_text(rel)


@pytest.mark.parametrize("rel", _LAUNCHERS)
def test_launcher_marks_the_kiosk_session(rel: str) -> None:
    """Every Cage launcher hands the app the kiosk flag, so the window is
    fullscreen on any host, not only on a Pi."""
    from openfollow.services import KIOSK_ENV

    lines = [ln.strip() for ln in _kiosk_env_source(rel).splitlines()]
    assert f"Environment={KIOSK_ENV}=1" in lines or f"export {KIOSK_ENV}=1" in lines, rel


# --- the keyboard layout the packaged session starts Cage with ----------------

_SESSION = "packaging/debian/session.sh"
_HELPER = "/usr/share/openfollow/keyboard-layout"
_RUNTIME_DIR = '"/run/user/$(id -u)"'
_LAYOUT_ENV = (
    "XKB_DEFAULT_LAYOUT",
    "XKB_DEFAULT_VARIANT",
    "XKB_DEFAULT_MODEL",
    "XKB_DEFAULT_OPTIONS",
    "OPENFOLLOW_KEYBOARD_SOURCE",
)


def _run_session(tmp_path: Path, helper: str | None) -> dict[str, str]:
    """Run the packaged launcher with a stub Cage and a stub layout helper.

    ``helper`` is the stub's shell body; ``None`` leaves no helper at all.
    Returns the layout variables Cage was started with.
    """
    text = (_repo_root() / _SESSION).read_text()
    for literal in ("/usr/bin/cage", _HELPER, _RUNTIME_DIR):
        assert literal in text, f"session.sh no longer names {literal}"
    seen = tmp_path / "cage-env"
    cage = tmp_path / "cage"
    cage.write_text(f'#!/bin/sh\nenv > "{seen}"\n')
    cage.chmod(0o755)
    helper_path = tmp_path / "keyboard-layout"
    if helper is not None:
        helper_path.write_text("#!/bin/sh\n" + helper)
        helper_path.chmod(0o755)
    script = tmp_path / "session.sh"
    script.write_text(
        text.replace("/usr/bin/cage", str(cage))
        .replace(_HELPER, str(helper_path))
        .replace(_RUNTIME_DIR, f'"{tmp_path}"')
    )
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    subprocess.run(["sh", str(script)], cwd=tmp_path, env=env, check=True, timeout=10)
    lines = seen.read_text().splitlines()
    return {k: v for k, _, v in (line.partition("=") for line in lines) if k in _LAYOUT_ENV}


def _helper_printing(*lines: str) -> str:
    return "".join(f"printf '%s\\n' '{line}'\n" for line in lines)


def test_the_session_starts_cage_in_the_stations_layout(tmp_path: Path) -> None:
    helper = _helper_printing("layout=de", "variant=nodeadkeys", "source=config", "model=", "options=")
    assert _run_session(tmp_path, helper) == {
        "XKB_DEFAULT_LAYOUT": "de",
        "XKB_DEFAULT_VARIANT": "nodeadkeys",
        "OPENFOLLOW_KEYBOARD_SOURCE": "config",
    }


@pytest.mark.parametrize(("layout", "source"), [("gb", "system"), ("de", "config"), ("us", "default")])
def test_the_session_passes_the_system_keyboard_through(tmp_path: Path, layout: str, source: str) -> None:
    helper = _helper_printing(
        f"layout={layout}", "variant=", f"source={source}", "model=pc105", "options=grp:alt_shift_toggle,ctrl:nocaps"
    )
    assert _run_session(tmp_path, helper) == {
        "XKB_DEFAULT_LAYOUT": layout,
        "XKB_DEFAULT_MODEL": "pc105",
        "XKB_DEFAULT_OPTIONS": "grp:alt_shift_toggle,ctrl:nocaps",
        "OPENFOLLOW_KEYBOARD_SOURCE": source,
    }


def test_the_session_asks_the_helper_about_the_apps_own_config(tmp_path: Path) -> None:
    """The app reads ``config.toml`` from the working directory; the helper must read the same file."""
    helper = 'printf "layout=us\\nsource=default\\n"; [ "$1 $2" = "env config.toml" ] || exit 1\n'
    assert _run_session(tmp_path, helper)["XKB_DEFAULT_LAYOUT"] == "us"


@pytest.mark.parametrize(
    "helper",
    [
        None,  # not installed
        "exit 1\n",  # failed
        _helper_printing("variant=nodeadkeys", "source=config"),  # no layout
        _helper_printing("layout=de"),  # no source
    ],
)
def test_without_an_answer_the_session_exports_nothing_about_the_layout(tmp_path: Path, helper: str | None) -> None:
    """Cage then starts in its own default, and the web UI hides a setting that could not take effect."""
    assert _run_session(tmp_path, helper) == {}


def test_the_session_never_evaluates_what_the_helper_prints(tmp_path: Path) -> None:
    marker = tmp_path / "pwned"
    helper = _helper_printing(
        f"layout=de$(touch {marker})",
        f"variant=`touch {marker}`",
        "source=config;id",
        "model=pc105 x",
        f"options=a;touch {marker}",
        f"XKB_DEFAULT_LAYOUT=$(touch {marker})",
    )
    assert _run_session(tmp_path, helper) == {}
    assert not marker.exists()


def test_the_session_ignores_a_bad_value_but_keeps_the_good_ones(tmp_path: Path) -> None:
    helper = _helper_printing("layout=de", "variant=no dead", "source=config", "options=a b")
    assert _run_session(tmp_path, helper) == {"XKB_DEFAULT_LAYOUT": "de", "OPENFOLLOW_KEYBOARD_SOURCE": "config"}
