# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Reading the station's own settings backups back: the list, the archive checks and the staged write."""

from __future__ import annotations

import io
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from packaging.version import Version

from openfollow import config_restore
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

    assert [(row.name, row.kind, row.before) for row in rows] == [
        (restored, "restore", "Restore"),
        (pushed, "push", "Settings push"),
        (update_new, "update", "Update to 0.5.0rc3"),
        (defaults, "defaults", "Restore Defaults"),
        (imported, "import", "Import"),
        (update_old, "update", "Update to 0.5.0rc2"),
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
            "version": "0.5.0rc2",
            "created": "2026-10-28T20:41:00+00:00",
            "newer": False,
        },
        {
            "name": rows[1].name,
            "kind": "update",
            "before": "Update to 0.5.0rc2",
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
    assert set(row.as_json()) == {"name", "kind", "before", "version", "created", "newer"}


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
    assert (row.name, row.version, row.before) == (name, "0.5.0", "Update")


def test_an_unknown_kind_is_a_settings_change(station: Path) -> None:
    _web(station, "wizard", 0)
    assert [row.before for row in list_backups(_config(station), "0.5.0rc3")] == ["Settings change"]


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
    staged = stage_restore(read_backup(archive), catalog, station)
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
    staged = stage_restore(read_backup(_crafted(station, [_CONFIG])), station / "markers.toml", station)
    staged.commit()
    assert (station / "markers.toml").read_bytes() == b'[[marker]]\nid = 1\nname = "Lead"\n'
    assert _tree(station / "templates" / "user") == {"grid.oftemplate": b"grid"}
    assert _tree(station / "lens-calibrations") == {"zoom.json": b"{}"}


def test_a_folder_the_station_does_not_have_yet_is_made(tmp_path: Path) -> None:
    contents = config_restore.BackupContents(b"", None, {"lens-calibrations": {"a.json": b"{}"}})
    stage_restore(contents, tmp_path / "markers.toml", tmp_path).commit()
    assert _tree(tmp_path / "lens-calibrations") == {"a.json": b"{}"}
    assert [p.name for p in tmp_path.iterdir()] == ["lens-calibrations"]


def test_a_discarded_restore_leaves_nothing_behind(station: Path) -> None:
    contents = read_backup(station / "backups" / "web" / _web(station, "import", 0))
    before = _tree(station)
    stage_restore(contents, station / "markers.toml", station).discard()
    assert _tree(station) == before
    assert list(station.rglob(".*")) == []


def test_a_restore_that_fails_inside_a_folder_cleans_up(station: Path) -> None:
    contents = config_restore.BackupContents(b"", b"[[marker]]\n", {"templates/user": {"a": b"a", "a/b": b"b"}})
    before = _tree(station)
    with pytest.raises(OSError):
        stage_restore(contents, station / "markers.toml", station)
    assert _tree(station) == before
    assert list(station.rglob(".*")) == []
