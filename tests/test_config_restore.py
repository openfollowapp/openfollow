# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Reading the station's own settings backups back: the list, the archive checks and the staged write."""

from __future__ import annotations

import io
import json
import os
import sys
import tarfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from packaging.version import Version

from openfollow import config_restore, restore_hold
from openfollow.config_restore import (
    BackupDamagedError,
    BackupsUnreadableError,
    find_backup,
    is_newer,
    list_backups,
    load_backup_config,
    parse_version,
    read_backup,
    shown_version,
    stage_restore,
)
from openfollow.configuration import AppConfig, save_config
from openfollow.privilege import settings_backup
from openfollow.privilege.settings_backup import archive_name, run_backup, run_web_backup

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 28, 20, 40, 0, tzinfo=UTC)


@pytest.fixture()
def station(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A station's state folder: config, catalog, a saved template and a lens, both backup folders here."""
    monkeypatch.setattr(settings_backup, "STATE_DIR", tmp_path)
    save_config(AppConfig(psn_system_name="OpenFollow brave-otter"), str(tmp_path / "config.toml"))
    (tmp_path / "markers.toml").write_text('[[marker]]\nid = 1\nname = "Lead"\n', encoding="utf-8")
    (tmp_path / "templates" / "user").mkdir(parents=True)
    (tmp_path / "templates" / "user" / "grid.oftemplate").write_text("grid", encoding="utf-8")
    (tmp_path / "lens-calibrations").mkdir()
    (tmp_path / "lens-calibrations" / "zoom.json").write_text("{}", encoding="utf-8")
    return tmp_path


def _config(state: Path) -> str:
    return str(state / "config.toml")


def _web(state: Path, kind: str, minutes: int, version: str = "0.5.0rc3") -> str:
    result = run_web_backup(state / "config.toml", version, kind, now=_NOW + timedelta(minutes=minutes))
    assert result.archive, result.error
    return result.archive


def _update(state: Path, minutes: int, old: str = "0.4.3", new: str = "0.5.0~rc2") -> str:
    record = run_backup(state, old, new, now=_NOW + timedelta(minutes=minutes))
    assert record.archive, record.error
    return record.archive


def _tar(path: Path, members: list[tuple[tarfile.TarInfo, bytes]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tar:
        for info, data in members:
            tar.addfile(info, io.BytesIO(data) if info.isfile() else None)
    return path


def _file(name: str, data: bytes) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    return info, data


def _link(name: str, kind: bytes, target: str = "/etc/passwd") -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = target
    return info, b""


def _folder(name: str) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    return info, b""


_CONFIG = _file("config.toml", b'psn_system_name = "Old"\n')


def _crafted(state: Path, members: list[tuple[tarfile.TarInfo, bytes]], minutes: int = 0) -> Path:
    """An archive in the update folder, named as the station names one."""
    name = archive_name("OpenFollow brave-otter", "0.4.3", _NOW + timedelta(minutes=minutes))
    return _tar(state / "backups" / name, members)


# --- versions ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0.5.0~rc2", Version("0.5.0rc2")),
        ("0.5.0rc3", Version("0.5.0rc3")),
        ("0.5.0-rc3", Version("0.5.0rc3")),
        ("1:0.5.0", Version("0.5.0")),
        ("0.5.0-1", Version("0.5.0")),
        ("0.5.0~rc2-1", Version("0.5.0rc2")),
        ("1:0.4.3-2+rpi1", Version("0.4.3")),
        (" 0.4.3 ", Version("0.4.3")),
        ("garbage", None),
        ("", None),
    ],
)
def test_a_version_reads_debian_and_pep_440_spellings(text: str, expected: Version | None) -> None:
    assert parse_version(text) == expected


@pytest.mark.parametrize(
    ("text", "shown"), [("0.5.0~rc2", "0.5.0rc2"), ("0.5.0rc3", "0.5.0rc3"), ("unknown", "unknown")]
)
def test_a_version_is_shown_as_the_footer_spells_it(text: str, shown: str) -> None:
    assert shown_version(text) == shown


@pytest.mark.parametrize(
    ("version", "running", "newer"),
    [
        ("0.6.0", "0.5.0rc3", True),
        ("0.5.0", "0.5.0rc3", True),
        ("0.5.0~rc4", "0.5.0rc3", True),
        ("0.5.0~rc3", "0.5.0rc3", False),
        ("0.5.0rc2", "0.5.0rc3", False),
        ("0.4.3", "0.5.0rc3", False),
        ("0.5.0-1", "0.5.0", False),
        ("garbage", "0.5.0rc3", False),
        ("", "0.5.0rc3", False),
        ("0.6.0", "garbage", False),
        ("0.6.0", "0.0.0+unknown", False),
        ("0.6.0", "0.0.0", False),
    ],
)
def test_only_a_known_newer_version_is_newer(version: str, running: str, newer: bool) -> None:
    assert is_newer(version, running) is newer


# --- the list -----------------------------------------------------------------


def test_a_station_without_backups_lists_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_backup, "STATE_DIR", tmp_path / "state")
    assert list_backups(tmp_path / "config.toml", "0.5.0rc3") == []


def test_both_folders_are_listed_together_newest_first(station: Path) -> None:
    update_old = _update(station, 0)
    imported = _web(station, "import", 1)
    defaults = _web(station, "defaults", 2)
    update_new = _update(station, 3, "0.5.0~rc2", "0.5.0~rc3")
    pushed = _web(station, "push", 4)
    restored = _web(station, "restore", 5)

    rows = list_backups(_config(station), "0.5.0rc3")

    assert [(row.name, row.kind, row.before, row.made_before) for row in rows] == [
        (restored, "restore", "Restore", "a restore"),
        (pushed, "push", "Settings push", "a settings push"),
        (update_new, "update", "Update to 0.5.0rc3", "the update to 0.5.0rc3"),
        (defaults, "defaults", "Restore Defaults", "Restore Defaults"),
        (imported, "import", "Import", "an import"),
        (update_old, "update", "Update to 0.5.0rc2", "the update to 0.5.0rc2"),
    ]


def test_a_row_carries_the_version_that_wrote_it_and_when(station: Path) -> None:
    _update(station, 0, "0.4.3", "0.5.0~rc2")
    _web(station, "import", 1, version="0.5.0rc2")

    rows = list_backups(_config(station), "0.5.0rc3")

    assert [row.as_json() for row in rows] == [
        {
            "name": rows[0].name,
            "kind": "import",
            "before": "Import",
            "made_before": "an import",
            "version": "0.5.0rc2",
            "created": "2026-10-28T20:41:00+00:00",
            "newer": False,
        },
        {
            "name": rows[1].name,
            "kind": "update",
            "before": "Update to 0.5.0rc2",
            "made_before": "the update to 0.5.0rc2",
            "version": "0.4.3",
            "created": "2026-10-28T20:40:00+00:00",
            "newer": False,
        },
    ]


def test_a_backup_from_a_newer_version_is_marked(station: Path) -> None:
    _web(station, "import", 0, version="0.6.0")
    _update(station, 1, "0.5.0~rc2", "0.5.0~rc3")
    newer = {row.kind: row.newer for row in list_backups(_config(station), "0.5.0rc3")}
    assert newer == {"import": True, "update": False}


def test_a_row_never_carries_the_archive_or_its_path(station: Path) -> None:
    _web(station, "import", 0)
    (row,) = list_backups(_config(station), "0.5.0rc3")
    assert set(row.as_json()) == {"name", "kind", "before", "made_before", "version", "created", "newer"}


def _rewritten_manifest(archive: Path, manifest: bytes | None) -> None:
    """The archive with its manifest replaced, or dropped when *manifest* is None."""
    with tarfile.open(archive, "r:gz") as tar:
        members = [
            (m, tar.extractfile(m).read() if m.isfile() else b"")  # type: ignore[union-attr]
            for m in tar.getmembers()
            if m.name != "manifest.json"
        ]
    if manifest is not None:
        members.append(_file("manifest.json", manifest))
    _tar(archive, members)


@pytest.mark.parametrize(
    "manifest",
    [None, b"not json", b"[1, 2]", b'{"kind": 5, "version": ["x"], "created": "yesterday"}', b"\xff\xfe"],
    ids=["missing", "garbled", "not-an-object", "wrong-types", "not-utf8"],
)
def test_a_backup_without_a_manifest_that_reads_is_described_by_its_name(station: Path, manifest: bytes | None) -> None:
    web = station / "backups" / "web" / _web(station, "defaults", 0, version="0.5.0rc2")
    update = station / "backups" / _update(station, 1, "0.4.4~rc1", "0.5.0")
    _rewritten_manifest(web, manifest)
    _rewritten_manifest(update, manifest)

    rows = list_backups(_config(station), "0.5.0rc3")

    assert [(row.kind, row.before, row.version, row.created) for row in rows] == [
        ("update", "Update", "0.4.4-rc1", "2026-10-28T20:41:00+00:00"),
        ("defaults", "Restore Defaults", "0.5.0rc2", "2026-10-28T20:40:00+00:00"),
    ]


def test_a_backup_that_is_no_archive_is_still_listed_by_its_name(station: Path) -> None:
    (station / "backups").mkdir()
    name = archive_name("OpenFollow cam-v2", "0.5.0", _NOW)
    (station / "backups" / name).write_bytes(b"not an archive")
    (row,) = list_backups(_config(station), "0.5.0rc3")
    assert (row.name, row.version, row.before, row.made_before) == (name, "0.5.0", "Update", "an update")


def test_an_unknown_kind_is_a_settings_change(station: Path) -> None:
    _web(station, "wizard", 0)
    rows = list_backups(_config(station), "0.5.0rc3")
    assert [(row.before, row.made_before) for row in rows] == [("Settings change", "a settings change")]


def test_a_manifest_time_without_a_zone_is_utc(station: Path) -> None:
    archive = station / "backups" / "web" / _web(station, "import", 0)
    _rewritten_manifest(archive, b'{"kind": "import", "version": "0.5.0", "created": "2026-10-01T08:00:00"}')
    assert [row.created for row in list_backups(_config(station), "0.5.0rc3")] == ["2026-10-01T08:00:00+00:00"]


def test_only_archives_by_their_names_are_listed(station: Path) -> None:
    kept = _update(station, 0)
    backups = station / "backups"
    (backups / "notes.txt").write_text("x", encoding="utf-8")
    (backups / "OpenFollow-v0.4.3-20261399T000000Z.ofbackup").write_bytes(b"x")  # no such month
    (backups / archive_name("OpenFollow", "0.4.3", _NOW + timedelta(days=1))).symlink_to(backups / kept)
    (backups / archive_name("OpenFollow", "0.4.3", _NOW + timedelta(days=2))).mkdir()
    assert [row.name for row in list_backups(_config(station), "0.5.0rc3")] == [kept]


@pytest.mark.parametrize("blocked", ["backups", "backups/web"])
def test_a_folder_that_does_not_list_is_said(station: Path, blocked: str) -> None:
    _web(station, "import", 0)
    target = station / blocked
    for child in sorted(target.rglob("*"), reverse=True):
        child.unlink() if child.is_file() else child.rmdir()
    target.rmdir()
    target.write_text("a file where the folder goes", encoding="utf-8")
    with pytest.raises(BackupsUnreadableError):
        list_backups(_config(station), "0.5.0rc3")


def test_a_backup_is_found_only_by_a_listed_name(station: Path) -> None:
    name = _web(station, "import", 0)
    stray = station / name
    stray.write_bytes(b"x")
    assert find_backup(_config(station), name, "0.5.0rc3").path == station / "backups" / "web" / name  # type: ignore[union-attr]
    for asked in (f"web/{name}", f"../{name}", f"/{name}", "nope.ofbackup", ""):
        assert find_backup(_config(station), asked, "0.5.0rc3") is None


# --- reading an archive back ----------------------------------------------------


def test_an_archive_holds_its_config_catalog_templates_and_lenses(station: Path) -> None:
    archive = station / "backups" / "web" / _web(station, "import", 0)
    contents = read_backup(archive)
    assert contents.config == (station / "config.toml").read_bytes()
    assert contents.catalog == (station / "markers.toml").read_bytes()
    assert contents.folders == {
        "templates/user": {"grid.oftemplate": b"grid"},
        "lens-calibrations": {"zoom.json": b"{}"},
    }


def test_a_part_the_archive_lacks_is_not_restored_and_an_empty_folder_is(station: Path) -> None:
    archive = _crafted(station, [_CONFIG, _folder("templates/user")])
    contents = read_backup(archive)
    assert (contents.catalog, contents.folders) == (None, {"templates/user": {}})


def test_nested_template_folders_and_a_dot_prefix_are_read(station: Path) -> None:
    archive = _crafted(station, [_CONFIG, _file("./templates/user/osc/eos.oftemplate", b"eos")])
    assert read_backup(archive).folders == {"templates/user": {"osc/eos.oftemplate": b"eos"}}


def test_members_a_backup_does_not_restore_are_ignored(station: Path) -> None:
    archive = _crafted(station, [_CONFIG, _file("media/clip.mp4", b"m"), _file("templates/system/a", b"s")])
    contents = read_backup(archive)
    assert (contents.catalog, contents.folders) == (None, {})


@pytest.mark.parametrize(
    "members",
    [
        [_file("markers.toml", b"[[marker]]\n")],
        [_CONFIG, _link("templates/user/evil", tarfile.SYMTYPE)],
        [_CONFIG, _link("templates/user/evil", tarfile.LNKTYPE, "config.toml")],
        [_CONFIG, _link("dev", tarfile.CHRTYPE)],
        [_CONFIG, _file("../config.toml", b"x")],
        [_CONFIG, _file("templates/user/../../outside", b"x")],
        [_CONFIG, _file("/etc/cron.d/x", b"x")],
        [_CONFIG, _file("markers.toml", b"not [[ toml")],
        [_CONFIG, _file("markers.toml", b"\xff\xfe")],
    ],
    ids=[
        "no-config",
        "symlink",
        "hardlink",
        "device",
        "dotdot",
        "dotdot-inside",
        "absolute",
        "catalog",
        "catalog-utf8",
    ],
)
def test_a_damaged_archive_is_refused(station: Path, members: list[tuple[tarfile.TarInfo, bytes]]) -> None:
    with pytest.raises(BackupDamagedError):
        read_backup(_crafted(station, members))


@pytest.mark.parametrize("data", [b"not gzip", b""], ids=["not-gzip", "empty"])
def test_a_file_that_is_no_archive_is_refused(station: Path, data: bytes) -> None:
    path = station / "backups" / archive_name("x", "0.4.3", _NOW)
    path.parent.mkdir()
    path.write_bytes(data)
    with pytest.raises(BackupDamagedError):
        read_backup(path)


def test_a_cut_off_archive_is_refused(station: Path) -> None:
    archive = station / "backups" / "web" / _web(station, "import", 0)
    data = archive.read_bytes()
    archive.write_bytes(data[: len(data) // 2])
    with pytest.raises(BackupDamagedError):
        read_backup(archive)


def test_an_archive_gone_since_the_list_is_refused(station: Path) -> None:
    with pytest.raises(BackupDamagedError):
        read_backup(station / "backups" / "gone.ofbackup")


@pytest.mark.parametrize(
    ("cap", "value", "members"),
    [
        ("MAX_MEMBER_BYTES", 8, [_CONFIG]),
        ("MAX_TOTAL_BYTES", 30, [_CONFIG, _file("markers.toml", b"[[marker]]\nid = 1\n")]),
        ("MAX_MEMBERS", 2, [_CONFIG, _folder("templates/user"), _file("templates/user/a", b"a")]),
        ("MAX_ARCHIVE_BYTES", 10, [_CONFIG]),
    ],
)
def test_an_archive_past_a_cap_is_refused_before_it_is_read(
    station: Path, monkeypatch: pytest.MonkeyPatch, cap: str, value: int, members: list
) -> None:
    archive = _crafted(station, members)
    read_backup(archive)
    monkeypatch.setattr(config_restore, cap, value)
    with pytest.raises(BackupDamagedError):
        read_backup(archive)


def test_an_oversize_member_is_refused_from_its_header(station: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = _crafted(station, [_CONFIG, _file("templates/user/big", b"x" * 64)])
    monkeypatch.setattr(config_restore, "MAX_MEMBER_BYTES", 32)
    read = []
    real = tarfile.TarFile.extractfile
    monkeypatch.setattr(tarfile.TarFile, "extractfile", lambda self, m: read.append(m.name) or real(self, m))
    with pytest.raises(BackupDamagedError):
        read_backup(archive)
    assert read == ["config.toml"]


def test_the_config_reads_as_a_hand_edited_one(station: Path) -> None:
    archive = _crafted(station, [_file("config.toml", b'psn_system_name = "Old"\n[grid]\nwidth = "abc"\n')])
    cfg = load_backup_config(read_backup(archive))
    assert cfg.psn_system_name == "Old"
    assert cfg.grid.width == AppConfig().grid.width


@pytest.mark.parametrize("config", [b"not [[ toml", b"   \n", b"grid = 5\n", b"\xff\xfe"])
def test_a_config_that_does_not_load_is_damage(station: Path, config: bytes) -> None:
    archive = _crafted(station, [_file("config.toml", config)])
    with pytest.raises(BackupDamagedError):
        load_backup_config(read_backup(archive))


# --- writing it back ----------------------------------------------------------


def _tree(folder: Path) -> dict[str, bytes]:
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


def test_a_staged_restore_replaces_the_catalog_and_both_folders(station: Path) -> None:
    archive = _crafted(
        station,
        [
            _CONFIG,
            _file("markers.toml", b"[[marker]]\nid = 7\n"),
            _file("templates/user/new.oftemplate", b"new"),
            _file("lens-calibrations/tele.json", b"[]"),
        ],
    )
    catalog = station / "show" / "catalog.toml"
    staged = stage_restore(read_backup(archive), catalog, station / "config.toml")
    assert not catalog.exists()
    assert _tree(station / "templates" / "user") == {"grid.oftemplate": b"grid"}

    staged.commit()
    staged.discard()

    assert catalog.read_bytes() == b"[[marker]]\nid = 7\n"
    assert _tree(station / "templates" / "user") == {"new.oftemplate": b"new"}
    assert _tree(station / "lens-calibrations") == {"tele.json": b"[]"}
    assert (station / "markers.toml").read_bytes() == b'[[marker]]\nid = 1\nname = "Lead"\n'
    assert sorted(p.name for p in station.iterdir() if p.name.startswith(".")) == []
    assert sorted(p.name for p in (station / "templates").iterdir()) == ["user"]


def test_a_part_the_archive_lacks_stays_as_it_is(station: Path) -> None:
    staged = stage_restore(read_backup(_crafted(station, [_CONFIG])), station / "markers.toml", station / "config.toml")
    staged.commit()
    assert (station / "markers.toml").read_bytes() == b'[[marker]]\nid = 1\nname = "Lead"\n'
    assert _tree(station / "templates" / "user") == {"grid.oftemplate": b"grid"}
    assert _tree(station / "lens-calibrations") == {"zoom.json": b"{}"}


def test_a_folder_the_station_does_not_have_yet_is_made(tmp_path: Path) -> None:
    contents = config_restore.BackupContents(b"", None, {"lens-calibrations": {"a.json": b"{}"}})
    stage_restore(contents, tmp_path / "markers.toml", tmp_path / "config.toml").commit()
    assert _tree(tmp_path / "lens-calibrations") == {"a.json": b"{}"}
    assert [p.name for p in tmp_path.iterdir()] == ["lens-calibrations"]


def test_a_discarded_restore_leaves_nothing_behind(station: Path) -> None:
    contents = read_backup(station / "backups" / "web" / _web(station, "import", 0))
    before = _tree(station)
    stage_restore(contents, station / "markers.toml", station / "config.toml").discard()
    assert _tree(station) == before
    assert list(station.rglob(".*")) == []


def test_a_restore_that_fails_inside_a_folder_cleans_up(station: Path) -> None:
    contents = config_restore.BackupContents(b"", b"[[marker]]\n", {"templates/user": {"a": b"a", "a/b": b"b"}})
    before = _tree(station)
    with pytest.raises(OSError):
        stage_restore(contents, station / "markers.toml", station / "config.toml")
    assert _tree(station) == before
    assert list(station.rglob(".*")) == []


@pytest.mark.parametrize(
    "members",
    [
        [_CONFIG, _file("templates/user/a", b"a"), _file("templates/user/a/b", b"b")],
        [_CONFIG, _file("lens-calibrations/x/y", b"y"), _file("lens-calibrations/x", b"x")],
        [_CONFIG, _file("templates/user/a/b/c", b"c"), _file("templates/user/a", b"a")],
    ],
    ids=["file-then-inside", "inside-then-file", "grandparent"],
)
def test_a_file_where_another_needs_a_folder_is_damage(station: Path, members: list) -> None:
    with pytest.raises(BackupDamagedError):
        read_backup(_crafted(station, members))


def test_a_folder_member_beside_files_inside_it_reads(station: Path) -> None:
    archive = _crafted(
        station,
        [_CONFIG, _folder("templates/user"), _folder("templates/user/osc"), _file("templates/user/osc/a", b"a")],
    )
    assert read_backup(archive).folders == {"templates/user": {"osc/a": b"a"}}


def test_a_header_that_unpacks_past_the_cap_is_refused(station: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A pax header is read whole before its member is yielded: its size is no member's.
    info, data = _file("templates/user/" + "a" * 4000, b"x")
    archive = path = station / "backups" / archive_name("OpenFollow", "0.4.3", _NOW)
    path.parent.mkdir()
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as tar:
        tar.addfile(_CONFIG[0], io.BytesIO(_CONFIG[1]))
        tar.addfile(info, io.BytesIO(data))
    read_backup(archive)
    monkeypatch.setattr(config_restore, "MAX_UNPACKED_BYTES", 4096)
    with pytest.raises(BackupDamagedError):
        read_backup(archive)


# --- a restore cut short --------------------------------------------------------


def _leftover(parent: Path, name: str, files: dict[str, str]) -> Path:
    folder = parent / name
    folder.mkdir(parents=True)
    for relative, text in files.items():
        (folder / relative).write_text(text, encoding="utf-8")
    return folder


def _journal(station: Path, name: str, catalog: Path | None = None) -> None:
    (station / name).write_text(json.dumps({"catalog": str(catalog or station / "markers.toml")}), encoding="utf-8")


def _cut_short_mid_commit(station: Path) -> None:
    """Down between the lens folder's two renames; the config and templates were not reached yet."""
    (station / ".config.toml.gggg7777.restore").write_text('psn_system_name = "Restored"\n', encoding="utf-8")
    (station / "lens-calibrations").rename(station / ".lens-calibrations.aaaa1111.old")
    _leftover(station, ".lens-calibrations.bbbb2222.restore", {"new.json": "[]"})
    _leftover(station / "templates", ".user.dddd4444.restore", {"new.oftemplate": "new"})
    (station / "templates" / ".user.eeee5555.old").mkdir()
    (station / ".markers.toml.ffff6666.restore").write_text("[[marker]]\nid = 7\n", encoding="utf-8")


def test_a_committed_restore_cut_short_is_finished(station: Path) -> None:
    _cut_short_mid_commit(station)
    _journal(station, config_restore.JOURNAL)

    config_restore.recover_interrupted_restore(station / "config.toml")

    assert (station / "config.toml").read_text(encoding="utf-8") == 'psn_system_name = "Restored"\n'
    assert _tree(station / "lens-calibrations") == {"new.json": b"[]"}
    assert _tree(station / "templates" / "user") == {"new.oftemplate": b"new"}
    assert (station / "markers.toml").read_text(encoding="utf-8") == "[[marker]]\nid = 7\n"
    assert list(station.rglob(".*")) == []


def test_a_restore_that_never_committed_leaves_the_station_s_own(station: Path) -> None:
    _cut_short_mid_commit(station)
    _journal(station, config_restore.PENDING_JOURNAL)
    config = (station / "config.toml").read_bytes()

    config_restore.recover_interrupted_restore(station / "config.toml")

    assert (station / "config.toml").read_bytes() == config
    assert _tree(station / "lens-calibrations") == {"zoom.json": b"{}"}
    assert _tree(station / "templates" / "user") == {"grid.oftemplate": b"grid"}
    assert (station / "markers.toml").read_bytes() == b'[[marker]]\nid = 1\nname = "Lead"\n'
    assert list(station.rglob(".*")) == []


def test_a_power_cut_while_staging_never_installs_a_folder_the_station_did_not_have(tmp_path: Path) -> None:
    contents = config_restore.BackupContents(b"", b"[[marker]]\n", {"lens-calibrations": {"a.json": b"{}"}})
    stage_restore(contents, tmp_path / "markers.toml", tmp_path / "config.toml")  # and then the power went

    config_restore.recover_interrupted_restore(tmp_path / "config.toml")

    assert list(tmp_path.iterdir()) == []


def test_a_commit_cut_short_by_a_power_cut_is_finished_at_the_start(
    station: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged = stage_restore(_all_parts(station), station / "markers.toml", station / "config.toml")
    real = config_restore.os.replace

    def power_cut(src: str | Path, dst: str | Path) -> None:
        if Path(dst).name == "lens-calibrations" and str(src).endswith(".restore"):
            raise SystemExit("power cut")
        real(src, dst)

    monkeypatch.setattr(config_restore.os, "replace", power_cut)
    with pytest.raises(SystemExit):
        staged.commit()
    monkeypatch.undo()

    config_restore.recover_interrupted_restore(station / "config.toml")

    assert (station / "markers.toml").read_bytes() == b"[[marker]]\nid = 7\n"
    assert _tree(station / "templates" / "user") == {"new.oftemplate": b"new"}
    assert _tree(station / "lens-calibrations") == {"tele.json": b"[]"}
    assert list(station.rglob(".*")) == []


def _synced_paths(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Every path ``os.fsync`` is called on from here on."""
    synced: list[Path] = []
    real = os.fsync

    def fsync(fd: int) -> None:
        if sys.platform == "darwin":
            import fcntl

            raw = fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024))  # type: ignore[attr-defined]
            synced.append(Path(raw.rstrip(b"\0").decode()).resolve())
        else:
            synced.append(Path(os.readlink(f"/proc/self/fd/{fd}")).resolve())
        real(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    return synced


def test_what_a_restore_writes_reaches_the_disk(station: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    synced = _synced_paths(monkeypatch)

    staged = stage_restore(_all_parts(station), station / "markers.toml", station / "config.toml")
    staged_paths = {p.resolve() for p in station.rglob(".*") if p.suffix in (".restore", ".pending")}
    staged_files = {p for folder in staged_paths if folder.is_dir() for p in folder.rglob("*")} | {
        p for p in staged_paths if p.is_file()
    }
    assert staged_files | staged_paths <= set(synced)
    synced.clear()

    staged.commit()

    assert {station.resolve(), (station / "templates").resolve()} <= set(synced)


def test_a_folder_whose_entries_cannot_be_synced_is_no_failure(station: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = os.open

    def no_folders(path: str | Path, flags: int, *args: int, **kwargs: int) -> int:
        if kwargs.get("dir_fd") is None and Path(path).is_dir():
            raise PermissionError(13, "Permission denied")
        return real(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", no_folders)
    staged = stage_restore(_all_parts(station), station / "markers.toml", station / "config.toml")
    assert staged.commit() == []
    monkeypatch.undo()
    assert _tree(station / "lens-calibrations") == {"tele.json": b"[]"}


def test_a_folder_gone_with_only_its_old_copy_left_is_put_back(station: Path) -> None:
    (station / "lens-calibrations").rename(station / ".lens-calibrations.aaaa1111.old")
    config_restore.recover_interrupted_restore(station / "config.toml")
    assert _tree(station / "lens-calibrations") == {"zoom.json": b"{}"}
    assert list(station.rglob(".*")) == []


@pytest.mark.parametrize("suffix", [".old", ".restore"])
def test_a_leftover_beside_a_folder_in_place_is_removed(station: Path, suffix: str) -> None:
    _leftover(station, f".lens-calibrations.aaaa1111{suffix}", {"stale.json": "{}"})
    _leftover(station / "templates", f".user.bbbb2222{suffix}", {"stale": "x"})
    config_restore.recover_interrupted_restore(station / "config.toml")
    assert _tree(station / "lens-calibrations") == {"zoom.json": b"{}"}
    assert _tree(station / "templates" / "user") == {"grid.oftemplate": b"grid"}
    assert list(station.rglob(".*")) == []


def test_a_staged_catalog_left_behind_is_removed(station: Path) -> None:
    catalog = station / "show" / "markers.toml"
    catalog.parent.mkdir()
    catalog.write_text("[[marker]]\nid = 2\n", encoding="utf-8")
    (catalog.parent / ".markers.toml.aaaa1111.restore").write_text("half", encoding="utf-8")
    _journal(station, config_restore.PENDING_JOURNAL, catalog)
    config_restore.recover_interrupted_restore(station / "config.toml")
    assert catalog.read_text(encoding="utf-8") == "[[marker]]\nid = 2\n"
    assert [p.name for p in catalog.parent.iterdir()] == ["markers.toml"]


def test_a_station_without_leftovers_is_left_as_it_is(tmp_path: Path) -> None:
    config_restore.recover_interrupted_restore(tmp_path / "state" / "config.toml")
    assert list(tmp_path.iterdir()) == []


def test_a_leftover_that_cannot_be_put_back_is_logged_and_left(
    station: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    (station / "lens-calibrations").rename(station / ".lens-calibrations.aaaa1111.old")

    def refuse(*_args: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(config_restore.os, "replace", refuse)
    config_restore.recover_interrupted_restore(station / "config.toml")
    assert (station / ".lens-calibrations.aaaa1111.old").is_dir()
    assert "lens-calibrations" in caplog.text


# --- a commit that cannot finish --------------------------------------------------


def _all_parts(station: Path) -> config_restore.BackupContents:
    return config_restore.BackupContents(
        b"",
        b"[[marker]]\nid = 7\n",
        {"templates/user": {"new.oftemplate": b"new"}, "lens-calibrations": {"tele.json": b"[]"}},
    )


def _refusing(monkeypatch: pytest.MonkeyPatch, refused: Callable[[Path, Path], bool]) -> None:
    real = config_restore.os.replace

    def replace(src: str | Path, dst: str | Path) -> None:
        if refused(Path(src), Path(dst)):
            raise PermissionError(13, "Permission denied")
        real(src, dst)

    monkeypatch.setattr(config_restore.os, "replace", replace)


def test_a_commit_needs_no_new_space(station: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    staged = stage_restore(_all_parts(station), station / "markers.toml", station / "config.toml")

    def no_space(*_args: object, **_kwargs: object) -> str:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(config_restore.tempfile, "mkdtemp", no_space)
    monkeypatch.setattr(config_restore.tempfile, "mkstemp", no_space)
    assert staged.commit() == []
    assert _tree(station / "lens-calibrations") == {"tele.json": b"[]"}
    assert list(station.rglob(".*")) == []


def test_a_part_that_cannot_move_in_is_named_and_the_rest_restored(
    station: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged = stage_restore(_all_parts(station), station / "markers.toml", station / "config.toml")
    _refusing(monkeypatch, lambda src, dst: dst.name == "markers.toml")

    assert staged.commit() == ["markers.toml"]
    staged.discard()

    assert (station / "markers.toml").read_bytes() == b'[[marker]]\nid = 1\nname = "Lead"\n'
    assert _tree(station / "templates" / "user") == {"new.oftemplate": b"new"}
    assert _tree(station / "lens-calibrations") == {"tele.json": b"[]"}
    assert list(station.rglob(".*")) == []


def test_a_folder_that_cannot_move_in_gets_its_own_back(station: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    staged = stage_restore(_all_parts(station), station / "markers.toml", station / "config.toml")
    _refusing(monkeypatch, lambda src, dst: dst.name == "lens-calibrations" and src.name.endswith(".restore"))

    assert staged.commit() == ["lens-calibrations"]
    staged.discard()

    assert _tree(station / "lens-calibrations") == {"zoom.json": b"{}"}
    assert _tree(station / "templates" / "user") == {"new.oftemplate": b"new"}
    assert list(station.rglob(".*")) == []


def test_a_folder_that_cannot_be_moved_aside_is_left_as_it_is(station: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    staged = stage_restore(_all_parts(station), station / "markers.toml", station / "config.toml")
    _refusing(monkeypatch, lambda src, dst: src.name == "lens-calibrations")

    assert staged.commit() == ["lens-calibrations"]

    assert _tree(station / "lens-calibrations") == {"zoom.json": b"{}"}
    assert list(station.rglob(".*")) == []


def test_a_folder_that_cannot_move_in_nor_back_gets_its_own_back_at_the_next_start(
    station: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged = stage_restore(_all_parts(station), station / "markers.toml", station / "config.toml")
    _refusing(monkeypatch, lambda src, dst: dst.name == "lens-calibrations" and src.name != "lens-calibrations")

    assert staged.commit() == ["lens-calibrations"]
    staged.discard()
    assert not (station / "lens-calibrations").exists()

    monkeypatch.undo()
    # The commit had finished: as its answer said, the station keeps its own.
    config_restore.recover_interrupted_restore(station / "config.toml")
    assert _tree(station / "lens-calibrations") == {"zoom.json": b"{}"}
    assert list(station.rglob(".*")) == []


def test_a_folder_the_station_did_not_have_is_left_absent_when_it_cannot_move_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contents = config_restore.BackupContents(b"", None, {"lens-calibrations": {"a.json": b"{}"}})
    staged = stage_restore(contents, tmp_path / "markers.toml", tmp_path / "config.toml")
    _refusing(monkeypatch, lambda src, dst: dst.name == "lens-calibrations")

    assert staged.commit() == ["lens-calibrations"]
    assert list(tmp_path.iterdir()) == []


def test_no_room_to_move_a_folder_aside_stages_nothing(station: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = config_restore.tempfile.mkdtemp

    def no_room_aside(*args: object, **kwargs: object) -> str:
        if kwargs.get("suffix") == ".old":
            raise OSError(28, "No space left on device")
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(config_restore.tempfile, "mkdtemp", no_room_aside)
    with pytest.raises(OSError):
        stage_restore(_all_parts(station), station / "markers.toml", station / "config.toml")
    assert list(station.rglob(".*")) == []


def _with_config(station: Path) -> config_restore.StagedRestore:
    staged = stage_restore(_all_parts(station), station / "markers.toml", station / "config.toml")
    staged.config_slot().write_text('psn_system_name = "Restored"\n', encoding="utf-8")
    return staged


def _untouched(station: Path, config: bytes) -> None:
    assert (station / "config.toml").read_bytes() == config
    assert (station / "markers.toml").read_bytes() == b'[[marker]]\nid = 1\nname = "Lead"\n'
    assert _tree(station / "lens-calibrations") == {"zoom.json": b"{}"}
    assert not restore_hold.is_held(station / "config.toml")
    assert list(station.rglob(".*")) == []


def test_a_commit_puts_the_config_in_place_and_holds_every_part(station: Path) -> None:
    staged = _with_config(station)
    assert staged.commit() == []
    assert (station / "config.toml").read_text(encoding="utf-8") == 'psn_system_name = "Restored"\n'
    for part in ("config.toml", "markers.toml", "templates/user/x", "lens-calibrations"):
        assert restore_hold.is_held(station / part), part
    assert list(station.rglob(".*")) == []


@pytest.mark.parametrize("refused", [config_restore.JOURNAL, "config.toml"])
def test_a_commit_that_cannot_start_changes_nothing(
    station: Path, monkeypatch: pytest.MonkeyPatch, refused: str
) -> None:
    config = (station / "config.toml").read_bytes()
    staged = _with_config(station)
    real = os.replace

    def refuse(src: str | Path, dst: str | Path) -> None:
        if Path(dst).name == refused:
            raise PermissionError(13, "Permission denied")
        real(src, dst)

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError):
        staged.commit()
    staged.discard()
    monkeypatch.undo()
    _untouched(station, config)


def test_a_power_cut_between_journal_and_config_is_finished_at_the_start(
    station: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged = _with_config(station)
    real = os.replace

    def power_cut(src: str | Path, dst: str | Path) -> None:
        if Path(dst).name == "config.toml":
            raise SystemExit("power cut")
        real(src, dst)

    monkeypatch.setattr(os, "replace", power_cut)
    with pytest.raises(SystemExit):
        staged.commit()
    monkeypatch.undo()

    config_restore.recover_interrupted_restore(station / "config.toml")

    assert (station / "config.toml").read_text(encoding="utf-8") == 'psn_system_name = "Restored"\n'
    assert (station / "markers.toml").read_bytes() == b"[[marker]]\nid = 7\n"
    assert _tree(station / "lens-calibrations") == {"tele.json": b"[]"}
    assert list(station.rglob(".*")) == []


def test_a_power_cut_while_the_config_is_staged_undoes_the_restore(station: Path) -> None:
    config = (station / "config.toml").read_bytes()
    _with_config(station)  # and then the power went
    config_restore.recover_interrupted_restore(station / "config.toml")
    _untouched(station, config)


def test_a_commit_whose_clean_up_fails_still_returns(station: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    staged = _with_config(station)
    real = os.replace

    def refuse_the_catalog(src: str | Path, dst: str | Path) -> None:
        if Path(dst).name == "markers.toml":
            raise PermissionError(13, "Permission denied")
        real(src, dst)

    def read_only(self: Path, missing_ok: bool = False) -> None:
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(os, "replace", refuse_the_catalog)
    monkeypatch.setattr(Path, "unlink", read_only)
    assert staged.commit() == ["markers.toml"]
    assert restore_hold.is_held(station / "config.toml")


def test_a_commit_that_breaks_after_the_config_names_what_it_did_not_reach(
    station: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged = _with_config(station)
    real = os.replace

    def broken(src: str | Path, dst: str | Path) -> None:
        if Path(dst).name == "user":
            raise RuntimeError("unexpected")
        real(src, dst)

    monkeypatch.setattr(os, "replace", broken)
    assert staged.commit() == ["templates/user", "lens-calibrations"]
    assert (station / "markers.toml").read_bytes() == b"[[marker]]\nid = 7\n"
    assert not (station / config_restore.JOURNAL).exists()


@pytest.mark.parametrize("name", ["templates/user/\udcffx", "templates/user/a\x00b"], ids=["not-text", "nul"])
def test_a_name_no_file_can_take_is_damage(station: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    info = tarfile.TarInfo("templates/user/x")
    info.size = 1
    path = station / "backups" / archive_name("OpenFollow", "0.4.3", _NOW)
    path.parent.mkdir()
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as tar:
        tar.addfile(_CONFIG[0], io.BytesIO(_CONFIG[1]))
        tar.addfile(info, io.BytesIO(b"x"))
    real = tarfile.TarFile.next

    def as_a_crafted_header_names_it(self: tarfile.TarFile) -> tarfile.TarInfo | None:
        member = real(self)
        if member is not None and member.name == "templates/user/x":
            member.name = name
        return member

    monkeypatch.setattr(tarfile.TarFile, "next", as_a_crafted_header_names_it)
    with pytest.raises(BackupDamagedError):
        read_backup(path)


def test_a_catalog_left_staged_by_an_unexpected_error_is_removed(
    station: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged = _with_config(station)
    real = os.replace

    def broken(src: str | Path, dst: str | Path) -> None:
        if Path(dst).name == "markers.toml":
            raise RuntimeError("unexpected")
        real(src, dst)

    monkeypatch.setattr(os, "replace", broken)
    assert staged.commit() == ["markers.toml", "templates/user", "lens-calibrations"]
    assert list(station.glob(".markers.toml.*")) == []


def test_a_recovery_that_meets_an_unreadable_leftover_logs_and_goes_on(
    station: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (station / "templates" / "user").rename(station / "templates" / ".user.aaaa1111.old")
    locked = station / "templates" / ".user.aaaa1111.old"
    _leftover(station, ".lens-calibrations.bbbb2222.restore", {"stale.json": "{}"})
    locked.chmod(0)
    try:
        config_restore.recover_interrupted_restore(station / "config.toml")
    finally:
        locked.chmod(0o700)
    assert "restore cut short" in caplog.text
    assert list(station.glob(".lens-calibrations.*")) == []  # the next part was still put right


# --- a recovery that meets a storage fault ------------------------------------------------


def _faulty(monkeypatch: pytest.MonkeyPatch, names: set[str]) -> None:
    """``os.replace`` into any of *names* fails as a bad disk does."""
    real = os.replace

    def replace(src: str | Path, dst: str | Path) -> None:
        if Path(dst).name in names:
            raise OSError(5, "Input/output error")
        real(src, dst)

    monkeypatch.setattr(os, "replace", replace)


def test_a_folder_that_cannot_be_put_in_place_keeps_both_copies_for_the_next_start(
    station: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Down after the commit moved the station's templates aside, before the backup's moved in.
    (station / "templates" / "user").rename(station / "templates" / ".user.aaaa1111.old")
    _leftover(station / "templates", ".user.bbbb2222.restore", {"backup.oftemplate": "restored"})
    _journal(station, config_restore.JOURNAL)

    _faulty(monkeypatch, {"user"})
    config_restore.recover_interrupted_restore(station / "config.toml")
    monkeypatch.undo()

    assert _tree(station / "templates" / ".user.aaaa1111.old") == {"grid.oftemplate": b"grid"}
    assert _tree(station / "templates" / ".user.bbbb2222.restore") == {"backup.oftemplate": b"restored"}
    assert (station / config_restore.JOURNAL).exists()
    assert [r.levelname for r in caplog.records if "templates" in r.getMessage()] == ["ERROR"]

    config_restore.recover_interrupted_restore(station / "config.toml")

    assert _tree(station / "templates" / "user") == {"backup.oftemplate": b"restored"}
    assert list(station.rglob(".*")) == []


def test_a_config_that_cannot_be_put_in_place_keeps_the_journal_for_the_next_start(
    station: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = (station / "config.toml").read_bytes()
    (station / ".config.toml.aaaa1111.restore").write_text('psn_system_name = "Restored"\n', encoding="utf-8")
    _journal(station, config_restore.JOURNAL)

    _faulty(monkeypatch, {"config.toml"})
    config_restore.recover_interrupted_restore(station / "config.toml")
    monkeypatch.undo()

    assert (station / "config.toml").read_bytes() == before
    assert (station / config_restore.JOURNAL).exists()

    config_restore.recover_interrupted_restore(station / "config.toml")

    assert (station / "config.toml").read_text(encoding="utf-8") == 'psn_system_name = "Restored"\n'
    assert list(station.rglob(".*")) == []


def test_a_retry_after_a_partial_recovery_finishes_only_what_is_left(
    station: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cut_short_mid_commit(station)
    _journal(station, config_restore.JOURNAL)

    _faulty(monkeypatch, {"lens-calibrations"})
    config_restore.recover_interrupted_restore(station / "config.toml")
    monkeypatch.undo()

    assert (station / "config.toml").read_text(encoding="utf-8") == 'psn_system_name = "Restored"\n'
    assert _tree(station / "templates" / "user") == {"new.oftemplate": b"new"}
    assert (station / "markers.toml").read_text(encoding="utf-8") == "[[marker]]\nid = 7\n"
    assert not (station / "lens-calibrations").exists()
    (station / "templates" / "user" / "edited.oftemplate").write_text("kept", encoding="utf-8")

    config_restore.recover_interrupted_restore(station / "config.toml")

    assert _tree(station / "lens-calibrations") == {"new.json": b"[]"}
    assert _tree(station / "templates" / "user") == {"new.oftemplate": b"new", "edited.oftemplate": b"kept"}
    assert (station / "config.toml").read_text(encoding="utf-8") == 'psn_system_name = "Restored"\n'
    assert list(station.rglob(".*")) == []


def test_a_folder_in_place_that_a_committed_copy_cannot_replace_is_finished_next_time(
    station: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Down before the commit reached this folder: the station's is still in place.
    _leftover(station / "templates", ".user.bbbb2222.restore", {"backup.oftemplate": "restored"})
    _journal(station, config_restore.JOURNAL)
    real = os.replace

    def fail_moving_in(src: str | Path, dst: str | Path) -> None:
        if Path(dst).name == "user" and str(src).endswith(".restore"):
            raise OSError(5, "Input/output error")
        real(src, dst)

    monkeypatch.setattr(os, "replace", fail_moving_in)
    config_restore.recover_interrupted_restore(station / "config.toml")
    monkeypatch.undo()
    assert (station / config_restore.JOURNAL).exists()
    assert _tree(station / "templates" / ".user.bbbb2222.restore") == {"backup.oftemplate": b"restored"}
    # Nothing is deleted before its replacement is in place.
    assert _tree(station / "templates" / ".user.recovered.old") == {"grid.oftemplate": b"grid"}

    config_restore.recover_interrupted_restore(station / "config.toml")

    assert _tree(station / "templates" / "user") == {"backup.oftemplate": b"restored"}
    assert list(station.rglob(".*")) == []


def test_a_journal_that_cannot_be_removed_is_logged(
    station: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _journal(station, config_restore.JOURNAL)
    real = Path.unlink

    def stuck(self: Path, missing_ok: bool = False) -> None:
        if self.name == config_restore.JOURNAL:
            raise OSError(5, "Input/output error")
        real(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", stuck)
    config_restore.recover_interrupted_restore(station / "config.toml")
    assert "Storage fault" in caplog.text
