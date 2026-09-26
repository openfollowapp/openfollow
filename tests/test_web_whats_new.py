# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The updater's What's new step: which bundled notes a release shows."""

from __future__ import annotations

import fnmatch
from pathlib import Path

import pytest
import tomllib

from openfollow.web import whats_new as whats_new_module
from openfollow.web.whats_new import load_whats_new

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _notes(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "whatsnew.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_notes_for_the_installed_release_render(tmp_path: Path) -> None:
    notes = load_whats_new("0.4.4", _notes(tmp_path, "v0.4.4\n\n## Controllers\n\nPorts decide the order.\n"))
    assert notes.matches
    assert notes.version == "0.4.4"
    assert "<h2>Controllers</h2>" in notes.html
    assert "<p>Ports decide the order.</p>" in notes.html
    assert "v0.4.4" not in notes.html


@pytest.mark.parametrize("first_line", ["v0.4.4", "0.4.4", "  v0.4.4  ", "v0.4.4rc2"])
def test_version_line_forms(tmp_path: Path, first_line: str) -> None:
    assert load_whats_new("0.4.4", _notes(tmp_path, f"{first_line}\n\nBody.\n")).matches


@pytest.mark.parametrize("installed", ["0.4.4rc1", "0.4.4.dev0", "0.4.4+g1a2b3c4"])
def test_a_candidate_build_shows_its_release_notes(tmp_path: Path, installed: str) -> None:
    notes = load_whats_new(installed, _notes(tmp_path, "v0.4.4\n\nBody.\n"))
    assert notes.matches
    assert notes.version == installed


@pytest.mark.parametrize(
    ("first_line", "installed"),
    [("v0.4.3", "0.4.4"), ("v0.4.4", "0.4.40"), ("v0.4", "0.4.4"), ("v0.4.4", "0.0.0+unknown")],
)
def test_notes_for_another_release_are_not_shown(tmp_path: Path, first_line: str, installed: str) -> None:
    notes = load_whats_new(installed, _notes(tmp_path, f"{first_line}\n\nBody.\n"))
    assert not notes.matches
    assert notes.html == ""


@pytest.mark.parametrize(
    "text",
    ["", "\n", "v0.4.4", "v0.4.4\n   \n", "Release notes\nBody.", "v0.4.4 notes\nBody.", "## v0.4.4\nBody."],
)
def test_malformed_notes_are_not_shown(tmp_path: Path, text: str) -> None:
    assert not load_whats_new("0.4.4", _notes(tmp_path, text)).matches


def test_missing_notes_are_not_shown(tmp_path: Path) -> None:
    assert not load_whats_new("0.4.4", tmp_path / "absent.md").matches


def test_undecodable_notes_are_not_shown(tmp_path: Path) -> None:
    path = tmp_path / "whatsnew.md"
    path.write_bytes(b"v0.4.4\n\n\xff\xfe\n")
    assert not load_whats_new("0.4.4", path).matches


def test_images_come_from_the_bundled_assets(tmp_path: Path) -> None:
    notes = load_whats_new("0.4.4", _notes(tmp_path, "v0.4.4\n\n![Slots](/assets/whatsnew/slots.png)\n"))
    assert '<img src="/assets/whatsnew/slots.png" alt="Slots"' in notes.html


def test_raw_html_in_the_notes_is_inert(tmp_path: Path) -> None:
    notes = load_whats_new("0.4.4", _notes(tmp_path, "v0.4.4\n\n<script>alert(1)</script>\n"))
    assert "<script>" not in notes.html


def test_the_default_notes_file_is_read_at_call_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(whats_new_module, "WHATS_NEW_FILE", _notes(tmp_path, "v0.4.4\n\nBody.\n"))
    assert load_whats_new("0.4.4").matches


def test_the_notes_file_ships_in_the_wheel() -> None:
    # Poetry ships only Python modules unless told otherwise; a missing include
    # would drop the notes from every packaged install.
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    relative = whats_new_module.WHATS_NEW_FILE.relative_to(_REPO_ROOT).as_posix()
    shipped = [
        entry["path"]
        for entry in pyproject["tool"]["poetry"]["include"]
        if isinstance(entry, dict) and "wheel" in entry.get("format", [])
    ]
    assert any(fnmatch.fnmatch(relative, pattern) for pattern in shipped)
