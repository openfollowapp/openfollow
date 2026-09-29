# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The updater's What's new step: which bundled notes a release shows."""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path

import pytest
import tomllib

from openfollow.web import whats_new as whats_new_module
from openfollow.web.whats_new import load_backup_note, load_whats_new

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


def test_the_shipped_notes_render_for_their_release() -> None:
    """The bundled file names the release it describes on its first line and renders for it."""
    first_line = whats_new_module.WHATS_NEW_FILE.read_text(encoding="utf-8").partition("\n")[0].strip()
    notes = load_whats_new(first_line.lstrip("v"))
    assert notes.matches
    assert "This Station" in notes.html


# --- the settings backup the update left behind -------------------------------


def _record(state: Path, **fields: str) -> None:
    body = {"from": "0.4.3", "to": "0.4.4", "archive": "brave-otter-v0.4.3-20260929T180000Z.ofbackup", "error": ""}
    body.update(fields, ts="2026-09-29T18:00:00+00:00")
    (state / "backups").mkdir(exist_ok=True)
    (state / "backups" / "last-backup.json").write_text(json.dumps(body), encoding="utf-8")


def test_a_backup_names_its_archive(tmp_path: Path) -> None:
    _record(tmp_path)
    note = load_backup_note("0.4.4", tmp_path)
    assert note is not None
    assert note.level == "success"
    assert "v0.4.3" in note.text
    assert "brave-otter-v0.4.3-20260929T180000Z.ofbackup" in note.step
    assert str(tmp_path / "backups") in note.step


def test_a_failed_backup_is_a_caution_with_its_reason(tmp_path: Path) -> None:
    _record(tmp_path, archive="", error="No space left on device")
    note = load_backup_note("0.4.4", tmp_path)
    assert note is not None
    assert note.level == "warning"
    assert "No space left on device" in note.text
    assert "Export" in note.step


@pytest.mark.parametrize(
    ("package_version", "installed"),
    [
        ("0.4.4", "0.4.4"),
        ("0.4.4~rc1", "0.4.4rc1"),
        ("0.4.4~dev0", "0.4.4.dev0"),
        ("0.2.4~rc9-citest", "0.2.4rc9+citest"),
    ],
)
def test_the_package_version_matches_the_installed_release(
    tmp_path: Path, package_version: str, installed: str
) -> None:
    _record(tmp_path, to=package_version)
    assert load_backup_note(installed, tmp_path) is not None


@pytest.mark.parametrize(
    ("package_version", "installed"), [("0.4.3", "0.4.4"), ("0.4.4~rc1", "0.4.4rc2"), ("", "0.4.4")]
)
def test_a_record_from_another_install_is_not_shown(tmp_path: Path, package_version: str, installed: str) -> None:
    _record(tmp_path, to=package_version)
    assert load_backup_note(installed, tmp_path) is None


def test_no_record_means_no_note(tmp_path: Path) -> None:
    assert load_backup_note("0.4.4", tmp_path) is None


def test_the_note_reads_the_station_state_folder_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _record(tmp_path)
    monkeypatch.setattr(whats_new_module, "STATE_DIR", tmp_path)
    assert load_backup_note("0.4.4") is not None
