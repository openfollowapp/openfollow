# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The settings backup the ``.deb`` ``preinst`` takes before an upgrade unpacks."""

from __future__ import annotations

import errno
import json
import os
import pwd
import stat
import subprocess
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import openfollow
from openfollow.privilege import settings_backup
from openfollow.privilege.settings_backup import archive_name, list_archives, read_record, run_backup, station_label

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(openfollow.__file__).resolve().parent.parent
_DEBIAN = _REPO_ROOT / "packaging" / "debian"
_NOW = datetime(2026, 9, 29, 18, 0, 0, tzinfo=timezone.utc)


def _station(tmp_path: Path, config: str = 'psn_system_name = "OpenFollow brave-otter"\n') -> Path:
    state = tmp_path / "state"
    (state / "templates" / "user").mkdir(parents=True)
    (state / "config.toml").write_text(config, encoding="utf-8")
    (state / "markers.toml").write_text("[[markers]]\nid = 1\n", encoding="utf-8")
    (state / "templates" / "user" / "grid.oftemplate").write_text("grid", encoding="utf-8")
    (state / "templates" / "system").mkdir()
    (state / "templates" / "system" / "stock.oftemplate").write_text("stock", encoding="utf-8")
    return state


def _members(archive: Path) -> dict[str, bytes]:
    with tarfile.open(archive, "r:gz") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}  # type: ignore[union-attr]


def _seed_archives(backup_dir: Path, count: int, *, size: int = 4) -> list[str]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    names = []
    for i in range(count):
        name = archive_name("OpenFollow old-name", "0.1.0", _NOW - timedelta(days=count - i))
        (backup_dir / name).write_bytes(b"x" * size)
        names.append(name)
    return names


# --- file name ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("system_name", "expected"),
    [
        ("OpenFollow brave-otter", "brave-otter"),
        ("OpenFollow Stage Left", "Stage-Left"),
        ("FOH Spot #2", "FOH-Spot-2"),
        ("OpenFollow", "OpenFollow"),
        ("../evil", "evil"),
        (".hidden", "hidden"),
        ("  ", "OpenFollow"),
        ("OpenFollow ", "OpenFollow"),
        ("Bühne Links", "B-hne-Links"),
        ("a" * 80, "a" * 64),
        (None, "OpenFollow"),
        (42, "OpenFollow"),
    ],
)
def test_station_label(system_name: object, expected: str) -> None:
    assert station_label(system_name) == expected


def test_a_station_label_never_leaves_the_backup_folder() -> None:
    assert "/" not in station_label("../../etc/passwd")


@pytest.mark.parametrize(
    ("old_version", "expected"),
    [
        ("0.4.3", "v0.4.3"),
        ("0.4.4~rc1", "v0.4.4-rc1"),
        ("1:0.5.0", "v1-0.5.0"),
        ("0.2.4~rc9-citest", "v0.2.4-rc9-citest"),
        ("", "vunknown"),
    ],
)
def test_archive_name_carries_a_file_name_safe_version(old_version: str, expected: str) -> None:
    assert (
        archive_name("OpenFollow brave-otter", old_version, _NOW) == f"brave-otter-{expected}-20260929T180000Z.ofbackup"
    )


def test_archive_name_is_stamped_in_utc() -> None:
    local = _NOW.astimezone(timezone(timedelta(hours=2)))
    assert archive_name("x", "1", local).endswith("-20260929T180000Z.ofbackup")


# --- archive contents ---------------------------------------------------------


def test_the_archive_holds_the_three_sources_and_a_manifest(tmp_path: Path) -> None:
    state = _station(tmp_path)
    record = run_backup(state, "0.4.3", "0.4.4", now=_NOW)
    assert record.error == ""
    assert record.archive == "brave-otter-v0.4.3-20260929T180000Z.ofbackup"
    members = _members(state / "backups" / record.archive)
    assert set(members) == {"config.toml", "markers.toml", "templates/user/grid.oftemplate", "manifest.json"}
    assert members["config.toml"] == (state / "config.toml").read_bytes()
    manifest = json.loads(members["manifest.json"])
    assert manifest["from"] == "0.4.3"
    assert manifest["to"] == "0.4.4"
    assert manifest["sources"]["markers.toml"] == str(state / "markers.toml")
    assert manifest["missing"] == {}


def test_manifest_keeps_the_exact_version(tmp_path: Path) -> None:
    state = _station(tmp_path)
    record = run_backup(state, "1:0.4.4~rc1", "0.4.5", now=_NOW)
    manifest = json.loads(_members(state / "backups" / record.archive)["manifest.json"])
    assert manifest["from"] == "1:0.4.4~rc1"


def test_a_relative_marker_catalog_path_resolves_against_the_config_folder(tmp_path: Path) -> None:
    state = _station(tmp_path, 'markers_catalog_path = "catalog/show.toml"\n')
    (state / "catalog").mkdir()
    (state / "catalog" / "show.toml").write_text("show", encoding="utf-8")
    record = run_backup(state, "0.4.3", "0.4.4", now=_NOW)
    assert _members(state / "backups" / record.archive)["markers.toml"] == b"show"


def test_an_absolute_marker_catalog_path_is_used_as_is(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere.toml"
    elsewhere.write_text("abs", encoding="utf-8")
    state = _station(tmp_path, f'markers_catalog_path = "{elsewhere}"\n')
    record = run_backup(state, "0.4.3", "0.4.4", now=_NOW)
    assert _members(state / "backups" / record.archive)["markers.toml"] == b"abs"


@pytest.mark.parametrize("missing", ["config.toml", "markers.toml", "templates/user"])
def test_a_missing_source_is_skipped_and_listed(tmp_path: Path, missing: str) -> None:
    state = _station(tmp_path)
    target = state / missing
    if target.is_dir():
        for child in target.iterdir():
            child.unlink()
        target.rmdir()
    else:
        target.unlink()
    record = run_backup(state, "0.4.3", "0.4.4", now=_NOW)
    assert record.error == ""
    members = _members(state / "backups" / record.archive)
    assert not any(name == missing or name.startswith(missing + "/") for name in members)
    assert json.loads(members["manifest.json"])["missing"] == {missing: str(target)}


@pytest.mark.parametrize("config", ["this is = = not toml", "psn_system_name = 3\n", "\xff"])
def test_an_unreadable_config_is_still_archived_under_the_default_name(tmp_path: Path, config: str) -> None:
    state = _station(tmp_path)
    (state / "config.toml").write_bytes(config.encode("latin-1"))
    record = run_backup(state, "0.4.3", "0.4.4", now=_NOW)
    assert record.archive.startswith("OpenFollow-v0.4.3-")
    members = _members(state / "backups" / record.archive)
    assert members["config.toml"] == config.encode("latin-1")
    assert "markers.toml" in members


def test_a_symlinked_source_is_not_followed(tmp_path: Path) -> None:
    secret = tmp_path / "secret"
    secret.write_text("secret", encoding="utf-8")
    state = _station(tmp_path)
    (state / "markers.toml").unlink()
    (state / "markers.toml").symlink_to(secret)
    record = run_backup(state, "0.4.3", "0.4.4", now=_NOW)
    members = _members(state / "backups" / record.archive)
    assert b"secret" not in members.values()


# --- secrets ------------------------------------------------------------------


def test_the_archive_and_its_folder_are_private(tmp_path: Path) -> None:
    state = _station(tmp_path)
    record = run_backup(state, "0.4.3", "0.4.4", now=_NOW)
    assert stat.S_IMODE((state / "backups").stat().st_mode) == 0o700
    assert stat.S_IMODE((state / "backups" / record.archive).stat().st_mode) == 0o600


def test_a_loose_existing_folder_is_tightened(tmp_path: Path) -> None:
    state = _station(tmp_path)
    (state / "backups").mkdir(mode=0o755)
    run_backup(state, "0.4.3", "0.4.4", now=_NOW)
    assert stat.S_IMODE((state / "backups").stat().st_mode) == 0o700


# --- retention and eviction ---------------------------------------------------


def test_retention_keeps_the_ten_newest(tmp_path: Path) -> None:
    state = _station(tmp_path)
    seeded = _seed_archives(state / "backups", 12)
    record = run_backup(state, "0.4.3", "0.4.4", now=_NOW)
    names = [p.name for p in list_archives(state / "backups")]
    assert names == seeded[3:] + [record.archive]


def test_retention_ignores_the_station_name(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    for name in (
        "a-z-v1-20260101T000000Z.ofbackup",
        "OpenFollow-v0.1-20250101T000000Z.ofbackup",
        "zz-v2-20260601T000000Z.ofbackup",
        "notes.txt",
        "x-v1-2026.ofbackup",
    ):
        (backup_dir / name).write_bytes(b"")
    assert [p.name for p in list_archives(backup_dir)] == [
        "OpenFollow-v0.1-20250101T000000Z.ofbackup",
        "a-z-v1-20260101T000000Z.ofbackup",
        "zz-v2-20260601T000000Z.ofbackup",
    ]


def test_list_archives_of_a_missing_folder_is_empty(tmp_path: Path) -> None:
    assert list_archives(tmp_path / "absent") == []


class _FullDisk:
    """A writer that runs out of space until ``free`` archives are gone."""

    def __init__(self, backup_dir: Path, free_after: int | None, code: int = errno.ENOSPC) -> None:
        self.backup_dir = backup_dir
        self.free_after = free_after
        self.code = code
        self.start = len(list_archives(backup_dir))
        self.attempts = 0

    def __call__(self, target: Path, sources: list[tuple[str, Path]], manifest: dict[str, object]) -> None:
        self.attempts += 1
        target.write_bytes(b"partial")
        gone = self.start - len(list_archives(self.backup_dir))
        if self.free_after is None or gone < self.free_after:
            raise OSError(self.code, os.strerror(self.code))
        settings_backup._write_archive(target, sources, manifest)


def _leftovers(backup_dir: Path) -> list[str]:
    return [p.name for p in backup_dir.iterdir() if p.name.startswith(".tmp-")]


def test_a_full_disk_evicts_the_oldest_until_it_fits(tmp_path: Path) -> None:
    state = _station(tmp_path)
    seeded = _seed_archives(state / "backups", 5)
    writer = _FullDisk(state / "backups", free_after=2)
    record = run_backup(state, "0.4.3", "0.4.4", now=_NOW, write=writer)
    assert record.error == ""
    assert [p.name for p in list_archives(state / "backups")] == seeded[2:] + [record.archive]
    assert _leftovers(state / "backups") == []


def test_eviction_never_deletes_the_newest_existing_archive(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    state = _station(tmp_path)
    seeded = _seed_archives(state / "backups", 3)
    record = run_backup(state, "0.4.3", "0.4.4", now=_NOW, write=_FullDisk(state / "backups", free_after=None))
    assert record.archive == ""
    assert record.error == os.strerror(errno.ENOSPC)
    assert [p.name for p in list_archives(state / "backups")] == seeded[-1:]
    assert _leftovers(state / "backups") == []
    assert "continuing the install" in capsys.readouterr().err


def test_a_full_disk_with_no_older_archive_warns(tmp_path: Path) -> None:
    state = _station(tmp_path)
    record = run_backup(state, "0.4.3", "0.4.4", now=_NOW, write=_FullDisk(state / "backups", free_after=None))
    assert record.error == os.strerror(errno.ENOSPC)
    assert read_record(state) == record


@pytest.mark.parametrize("code", [errno.EDQUOT])
def test_a_quota_counts_as_a_full_disk(tmp_path: Path, code: int) -> None:
    state = _station(tmp_path)
    _seed_archives(state / "backups", 3)
    record = run_backup(state, "0.4.3", "0.4.4", now=_NOW, write=_FullDisk(state / "backups", free_after=1, code=code))
    assert record.error == ""


@pytest.mark.parametrize("code", [errno.EACCES, errno.EIO])
def test_any_other_failure_deletes_nothing(tmp_path: Path, code: int) -> None:
    state = _station(tmp_path)
    seeded = _seed_archives(state / "backups", 3)
    record = run_backup(
        state, "0.4.3", "0.4.4", now=_NOW, write=_FullDisk(state / "backups", free_after=None, code=code)
    )
    assert record.error == os.strerror(code)
    assert [p.name for p in list_archives(state / "backups")] == seeded


def test_a_broken_archive_writer_is_recorded(tmp_path: Path) -> None:
    def broken(target: Path, sources: list[tuple[str, Path]], manifest: dict[str, object]) -> None:
        raise tarfile.TarError("bad member")

    state = _station(tmp_path)
    assert run_backup(state, "0.4.3", "0.4.4", now=_NOW, write=broken).error == "bad member"


def test_an_uncreatable_backup_folder_is_reported(tmp_path: Path) -> None:
    state = _station(tmp_path)
    (state / "backups").write_text("a file, not a folder", encoding="utf-8")
    record = run_backup(state, "0.4.3", "0.4.4", now=_NOW)
    assert record.archive == ""
    assert "backups" in record.error


# --- the record ---------------------------------------------------------------


def test_the_record_names_the_archive(tmp_path: Path) -> None:
    state = _station(tmp_path)
    record = run_backup(state, "0.4.3", "0.4.4", now=_NOW)
    assert read_record(state) == record
    assert record.ts == "2026-09-29T18:00:00+00:00"


@pytest.mark.parametrize(
    "body",
    ["", "not json", "[]", '{"from": "1"}', '{"from": 1, "to": "2", "archive": "", "error": "", "ts": ""}'],
)
def test_an_unreadable_record_is_none(tmp_path: Path, body: str) -> None:
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / "last-backup.json").write_text(body, encoding="utf-8")
    assert read_record(tmp_path) is None


def test_a_missing_record_is_none(tmp_path: Path) -> None:
    assert read_record(tmp_path) is None


# --- the preinst --------------------------------------------------------------


def _render(tmp_path: Path, state: Path) -> Path:
    """The real preinst, pointed at ``state`` and running as whoever runs the test."""
    rendered = tmp_path / "preinst"
    subprocess.run(
        ["sh", str(_DEBIAN / "render-preinst.sh"), str(_DEBIAN / "preinst.in"),
         str(_REPO_ROOT / "openfollow" / "privilege" / "settings_backup.py"), str(rendered)],
        check=True,
    )  # fmt: skip
    text = rendered.read_text(encoding="utf-8")
    for line, replacement in (
        ('STATE_DIR = Path("/var/lib/openfollow")', f"STATE_DIR = Path({str(state)!r})"),
        ('SERVICE_USER = "openfollow"', f"SERVICE_USER = {pwd.getpwuid(os.getuid()).pw_name!r}"),
    ):
        assert text.count(line) == 1
        text = text.replace(line, replacement)
    rendered.write_text(text, encoding="utf-8")
    return rendered


def _preinst(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["sh", str(script), *args], capture_output=True, text=True, timeout=30, check=False)


def test_the_preinst_backs_up_on_upgrade(tmp_path: Path) -> None:
    state = _station(tmp_path)
    result = _preinst(_render(tmp_path, state), "upgrade", "0.4.3", "0.4.4")
    assert result.returncode == 0, result.stderr
    record = read_record(state)
    assert record is not None
    assert (record.from_version, record.to_version, record.error) == ("0.4.3", "0.4.4", "")
    assert record.archive.startswith("brave-otter-v0.4.3-")
    assert "config.toml" in _members(state / "backups" / record.archive)


@pytest.mark.parametrize("args", [(), ("install",), ("install", "0.4.3"), ("abort-upgrade", "0.4.4")])
def test_the_preinst_does_nothing_outside_an_upgrade(tmp_path: Path, args: tuple[str, ...]) -> None:
    state = _station(tmp_path)
    result = _preinst(_render(tmp_path, state), *args)
    assert result.returncode == 0
    assert not (state / "backups").exists()


def test_a_failed_backup_still_lets_the_install_proceed(tmp_path: Path) -> None:
    state = _station(tmp_path)
    (state / "backups").write_text("a file, not a folder", encoding="utf-8")
    result = _preinst(_render(tmp_path, state), "upgrade", "0.4.3", "0.4.4")
    assert result.returncode == 0
    assert "settings backup failed, continuing the install" in result.stderr


def test_a_crashing_backup_program_still_lets_the_install_proceed(tmp_path: Path) -> None:
    script = _render(tmp_path, _station(tmp_path))
    script.write_text(script.read_text(encoding="utf-8").replace("import tomllib\n", "import no_such_module\n"))
    result = _preinst(script, "upgrade", "0.4.3", "0.4.4")
    assert result.returncode == 0
    assert "settings backup failed, continuing the install" in result.stderr


def test_the_preinst_skips_a_station_without_its_state_folder(tmp_path: Path) -> None:
    result = _preinst(_render(tmp_path, tmp_path / "absent"), "upgrade", "0.4.3", "0.4.4")
    assert result.returncode == 0
    assert not (tmp_path / "absent").exists()


def test_the_backup_program_never_contains_the_heredoc_terminator() -> None:
    program = (_REPO_ROOT / "openfollow" / "privilege" / "settings_backup.py").read_text(encoding="utf-8")
    assert "\nOPENFOLLOW_SETTINGS_BACKUP\n" not in program


def test_render_refuses_a_program_holding_the_terminator(tmp_path: Path) -> None:
    program = tmp_path / "program.py"
    program.write_text("x = 1\nOPENFOLLOW_SETTINGS_BACKUP\n", encoding="utf-8")
    result = subprocess.run(
        ["sh", str(_DEBIAN / "render-preinst.sh"), str(_DEBIAN / "preinst.in"), str(program), str(tmp_path / "out")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "terminator" in result.stderr


def test_the_deb_build_installs_the_rendered_preinst() -> None:
    build = (_REPO_ROOT / "packaging" / "build-deb.sh").read_text(encoding="utf-8")
    assert '"$DEBIAN_DIR/render-preinst.sh" "$DEBIAN_DIR/preinst.in"' in build
    assert '"$STAGE/DEBIAN/preinst"' in build


def test_the_backup_program_is_standard_library_only() -> None:
    program = (_REPO_ROOT / "openfollow" / "privilege" / "settings_backup.py").read_text(encoding="utf-8")
    assert "openfollow" not in "".join(line for line in program.splitlines() if line.startswith(("import ", "from ")))


def test_an_archive_that_cannot_be_evicted_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = _station(tmp_path)
    seeded = _seed_archives(state / "backups", 3)
    real_unlink = Path.unlink

    def locked(self: Path, missing_ok: bool = False) -> None:
        if self.name == seeded[0]:
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(self))
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", locked)
    record = run_backup(state, "0.4.3", "0.4.4", now=_NOW, write=_FullDisk(state / "backups", free_after=1))
    assert record.error == f"{os.strerror(errno.EACCES)}: {state / 'backups' / seeded[0]}"


def test_an_archive_already_gone_counts_as_evicted(tmp_path: Path) -> None:
    state = _station(tmp_path)
    seeded = _seed_archives(state / "backups", 3)

    class _RacingDisk(_FullDisk):
        def __call__(self, target: Path, sources: list[tuple[str, Path]], manifest: dict[str, object]) -> None:
            if self.attempts == 0:
                (self.backup_dir / seeded[0]).unlink()
                self.start -= 1
            super().__call__(target, sources, manifest)

    record = run_backup(state, "0.4.3", "0.4.4", now=_NOW, write=_RacingDisk(state / "backups", free_after=1))
    assert record.error == ""


# --- main, in process ---------------------------------------------------------


@pytest.fixture
def umasks(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The masks ``main`` sets, kept off the test process's own umask."""
    masks: list[int] = []
    monkeypatch.setattr(settings_backup.os, "umask", lambda mask: masks.append(mask) or 0o022)
    return masks


@pytest.fixture
def station_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, umasks: list[int]) -> Path:
    state = _station(tmp_path)
    monkeypatch.setattr(settings_backup, "STATE_DIR", state)
    monkeypatch.setattr(settings_backup.os, "geteuid", lambda: 1000)
    return state


def test_main_backs_up_on_upgrade(station_dir: Path, umasks: list[int]) -> None:
    assert settings_backup.main(["upgrade", "0.4.3", "0.4.4"]) == 0
    assert umasks == [0o077]
    record = read_record(station_dir)
    assert record is not None
    assert record.to_version == "0.4.4"


def test_main_without_a_new_version(station_dir: Path) -> None:
    assert settings_backup.main(["upgrade", "0.4.3"]) == 0
    record = read_record(station_dir)
    assert record is not None
    assert record.to_version == ""


@pytest.mark.parametrize("argv", [[], ["upgrade"], ["install", "0.4.3"]])
def test_main_ignores_everything_but_an_upgrade(station_dir: Path, argv: list[str]) -> None:
    assert settings_backup.main(argv) == 0
    assert not (station_dir / "backups").exists()


def test_main_survives_a_crash(station_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(settings_backup, "run_backup", boom)
    assert settings_backup.main(["upgrade", "0.4.3", "0.4.4"]) == 0
    assert "continuing the install: unexpected" in capsys.readouterr().err


def test_main_as_root_becomes_the_service_user(station_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(settings_backup.os, "geteuid", lambda: 0)
    monkeypatch.setattr(settings_backup.pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=999, pw_gid=998))
    for name in ("setgroups", "setgid", "setuid"):
        monkeypatch.setattr(settings_backup.os, name, lambda value, name=name: calls.append((name, value)))
    assert settings_backup.main(["upgrade", "0.4.3", "0.4.4"]) == 0
    assert calls == [("setgroups", []), ("setgid", 998), ("setuid", 999)]
    assert read_record(station_dir) is not None


def test_main_as_root_without_the_service_user_skips(
    station_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    def missing(name: str) -> None:
        raise KeyError(name)

    monkeypatch.setattr(settings_backup.os, "geteuid", lambda: 0)
    monkeypatch.setattr(settings_backup.pwd, "getpwnam", missing)
    assert settings_backup.main(["upgrade", "0.4.3", "0.4.4"]) == 0
    assert not (station_dir / "backups").exists()
    assert "settings backup skipped" in capsys.readouterr().err


def test_main_without_the_state_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_backup, "STATE_DIR", tmp_path / "absent")
    assert settings_backup.main(["upgrade", "0.4.3", "0.4.4"]) == 0
    assert not (tmp_path / "absent").exists()
