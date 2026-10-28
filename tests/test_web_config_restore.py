# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The station's own settings backups over HTTP: the list and a restore, each refusal and each restored part."""

from __future__ import annotations

import io
import json
import tarfile
import tomllib
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import openfollow
import openfollow.services as services_module
import openfollow.web.discovery as discovery_module
import openfollow.web.routes as routes_module
from openfollow import config_restore
from openfollow.configuration import AppConfig, load_config, save_config
from openfollow.privilege import settings_backup
from openfollow.privilege.settings_backup import WebBackup, archive_name, run_backup, run_web_backup, web_backup_dir
from openfollow.web.server import ConfigWebServer
from tests._ports import live_on_free_port

pytestmark = pytest.mark.integration

_RUNNING = "0.5.0rc3"
_NOW = datetime(2026, 10, 28, 20, 40, 0, tzinfo=UTC)
_RESTORE = "/api/config/backups/restore"
_OLD_CATALOG = b'[[marker]]\nid = 4\nname = "Old lead"\n'


def _identity(cfg: AppConfig) -> AppConfig:
    """*cfg* with what the restore must keep set to this station's values."""
    cfg.psn_system_name = "OpenFollow Stage Left"
    cfg.web_pin = ""
    cfg.web_port = 8123
    cfg.station_id = "abc123"
    cfg.psn_source_iface = "eth0"
    cfg.detection.storage_path = "/mnt/nvme/yolo"
    cfg.markers_catalog_path = "show/markers.toml"
    return cfg


@pytest.fixture()
def station(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    """A live server whose state folder holds the config and both backup folders, as on a station."""
    for cls in (discovery_module.BeaconSender, discovery_module.BeaconReceiver):
        monkeypatch.setattr(cls, "start", lambda self: None)
        monkeypatch.setattr(cls, "stop", lambda self: None)
    monkeypatch.setattr(settings_backup, "STATE_DIR", tmp_path)
    monkeypatch.setattr(openfollow, "__version__", _RUNNING)
    config_path = tmp_path / "config.toml"
    save_config(_identity(AppConfig()), str(config_path))
    with live_on_free_port(
        lambda port: ConfigWebServer(config_path=str(config_path), host="127.0.0.1", port=port, system_name="T")
    ) as (server, base):
        yield server, base, tmp_path


def _the_old_show(state: Path) -> None:
    """The settings, catalog, template and lens a backup will hold; the station's own identity differs."""
    old = AppConfig(psn_system_name="OpenFollow Old Name", station_id="old999", web_pin="1111", web_port=9000)
    old.grid.width = 42.0
    old.camera.pos_x = 3.25
    old.controlled_marker_ids = [4]
    old.psn_source_iface = "wlan0"
    old.detection.storage_path = "/elsewhere"
    save_config(old, str(state / "config.toml"))
    (state / "markers.toml").write_bytes(_OLD_CATALOG)
    (state / "templates" / "user").mkdir(parents=True, exist_ok=True)
    (state / "templates" / "user" / "old.oftemplate").write_text("old", encoding="utf-8")
    (state / "lens-calibrations").mkdir(exist_ok=True)
    (state / "lens-calibrations" / "old.json").write_text("{}", encoding="utf-8")


def _the_show_now(state: Path) -> None:
    """The station today: its own identity, other settings, its catalog where its config points."""
    save_config(_identity(AppConfig()), str(state / "config.toml"))
    (state / "show").mkdir(exist_ok=True)
    (state / "show" / "markers.toml").write_text("[[marker]]\nid = 9\n", encoding="utf-8")
    for folder in ("templates/user", "lens-calibrations"):
        for path in (state / folder).iterdir():
            path.unlink()
    (state / "templates" / "user" / "now.oftemplate").write_text("now", encoding="utf-8")


def _update_backup(state: Path, minutes: int = 0, old: str = "0.4.3") -> str:
    record = run_backup(state, old, "0.5.0~rc2", now=_NOW + timedelta(minutes=minutes))
    assert record.archive, record.error
    return record.archive


def _web_backup(state: Path, minutes: int = 0, version: str = "0.5.0rc2", kind: str = "import") -> str:
    result = run_web_backup(state / "config.toml", version, kind, now=_NOW + timedelta(minutes=minutes))
    assert result.archive, result.error
    return result.archive


def _get(base: str, path: str) -> tuple[int, dict[str, Any]]:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _post(base: str, path: str, data: bytes) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(
        f"{base}{path}", data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _restore(base: str, name: str) -> tuple[int, dict[str, Any]]:
    return _post(base, _RESTORE, json.dumps({"name": name}).encode())


def _web_archives(state: Path) -> list[str]:
    folder = web_backup_dir(state / "config.toml")
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def _tree(folder: Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in folder.iterdir()}


# --- the list -----------------------------------------------------------------


def test_the_list_names_each_backup_newest_first(station) -> None:
    _server, base, state = station
    update = _update_backup(state, 0)
    web = _web_backup(state, 1, version="0.6.0", kind="defaults")

    status, body = _get(base, "/api/config/backups")

    assert status == 200
    assert body == {
        "backups": [
            {
                "name": web,
                "kind": "defaults",
                "before": "Restore Defaults",
                "version": "0.6.0",
                "created": "2026-10-28T20:41:00+00:00",
                "newer": True,
            },
            {
                "name": update,
                "kind": "update",
                "before": "Update to 0.5.0rc2",
                "version": "0.4.3",
                "created": "2026-10-28T20:40:00+00:00",
                "newer": False,
            },
        ]
    }


def test_a_station_without_backups_lists_none(station) -> None:
    _server, base, _state = station
    assert _get(base, "/api/config/backups") == (200, {"backups": []})


def test_a_list_that_cannot_be_read_says_so(station) -> None:
    _server, base, state = station
    (state / "backups").write_text("a file where the folder goes", encoding="utf-8")
    status, body = _get(base, "/api/config/backups")
    assert status == 500
    assert body == {"error": "The backups on this station could not be read.", "action": "Details in Logs."}


# --- a restore ----------------------------------------------------------------


def test_an_update_backup_is_restored_with_this_station_s_identity(station) -> None:
    server, base, state = station
    _the_old_show(state)
    name = _update_backup(state)
    _the_show_now(state)

    status, body = _restore(base, name)

    assert status == 200, body
    assert {key: body[key] for key in ("success", "needs_restart", "restarting", "instance")} == {
        "success": True,
        "needs_restart": True,
        "restarting": True,
        "instance": server.instance_id,
    }
    assert server.check_restart_requested() is True
    saved = load_config(str(state / "config.toml"), strict=True)
    assert (saved.grid.width, saved.camera.pos_x, saved.controlled_marker_ids) == (42.0, 3.25, [4])
    kept = _identity(AppConfig())
    for path in routes_module._DEVICE_IDENTITY_FIELDS + ("psn_system_name",):
        owner, attr = routes_module._device_field_owner(saved, path)
        expected_owner, _ = routes_module._device_field_owner(kept, path)
        assert getattr(owner, attr) == getattr(expected_owner, attr), path
    # The catalog lands where this station keeps it; the archive's own path is not followed.
    assert (state / "show" / "markers.toml").read_bytes() == _OLD_CATALOG
    assert (state / "markers.toml").read_bytes() == _OLD_CATALOG  # untouched: the old show's own copy
    assert _tree(state / "templates" / "user") == {"old.oftemplate": "old"}
    assert _tree(state / "lens-calibrations") == {"old.json": "{}"}


def test_a_web_backup_is_restored(station) -> None:
    server, base, state = station
    _the_old_show(state)
    name = _web_backup(state)
    _the_show_now(state)

    status, body = _restore(base, name)

    assert status == 200, body
    assert load_config(str(state / "config.toml"), strict=True).grid.width == 42.0
    assert (state / "show" / "markers.toml").read_bytes() == _OLD_CATALOG
    assert server.check_restart_requested() is True


def test_the_settings_it_replaces_are_backed_up_first(station) -> None:
    _server, base, state = station
    _the_old_show(state)
    name = _update_backup(state)
    _the_show_now(state)

    status, body = _restore(base, name)

    assert status == 200
    assert "-restore-" in body["backup"]
    assert "backup_error" not in body
    archive = web_backup_dir(state / "config.toml") / body["backup"]
    contents = config_restore.read_backup(archive)
    assert config_restore.load_backup_config(contents).grid.width == AppConfig().grid.width
    assert contents.folders["templates/user"] == {"now.oftemplate": b"now"}


def test_a_restore_goes_ahead_when_no_backup_can_be_made(station, monkeypatch: pytest.MonkeyPatch) -> None:
    server, base, state = station
    _the_old_show(state)
    name = _update_backup(state)
    _the_show_now(state)
    monkeypatch.setattr(settings_backup, "run_web_backup", lambda *a, **kw: WebBackup("", "No space left on device"))

    status, body = _restore(base, name)

    assert status == 200
    assert body["backup_error"] == "No space left on device"
    assert "backup" not in body
    assert load_config(str(state / "config.toml"), strict=True).grid.width == 42.0
    assert server.check_restart_requested() is True


def test_the_oldest_web_backup_restores_though_the_backup_before_it_evicts_it(station) -> None:
    _server, base, state = station
    _the_old_show(state)
    oldest = _web_backup(state, 0)
    _the_show_now(state)
    for minute in range(1, settings_backup.KEEP):
        _web_backup(state, minute)

    status, body = _restore(base, oldest)

    assert status == 200, body
    assert oldest not in _web_archives(state)
    assert load_config(str(state / "config.toml"), strict=True).grid.width == 42.0


@pytest.mark.parametrize(
    ("mode", "pin"),
    [("simple", ""), ("advanced", "eth1")],
)
def test_the_station_s_network_mode_decides_the_restored_pins(station, mode: str, pin: str) -> None:
    _server, base, state = station
    old = AppConfig(network_mode="advanced")
    old.osc.listen_iface = "eth1"
    save_config(old, str(state / "config.toml"))
    name = _update_backup(state)
    save_config(_identity(AppConfig(network_mode=mode)), str(state / "config.toml"))

    assert _restore(base, name)[0] == 200

    # Read raw: a load clears a Simple station's pins again, so only the file shows one written.
    saved = tomllib.loads((state / "config.toml").read_text(encoding="utf-8"))
    assert (saved["network_mode"], saved["osc"]["listen_iface"]) == (mode, pin)


# --- refusals -----------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        b"null",
        b'"abc"',
        b"true",
        b"[1]",
        b"{}",
        b'{"name": null}',
        b'{"name": true}',
        b'{"name": 5}',
        b'{"name": ""}',
        b'{"name": ["x"]}',
        b"not json",
        b"\xff",
    ],
)
def test_a_request_that_names_no_backup_is_malformed(station, body: bytes) -> None:
    server, base, state = station
    before = (state / "config.toml").read_bytes()
    assert _post(base, _RESTORE, body) == (400, {"error": "The request named no backup.", "action": ""})
    assert (state / "config.toml").read_bytes() == before
    assert server.check_restart_requested() is False


@pytest.mark.parametrize("asked", ["../config.toml", "/etc/passwd", "nope.ofbackup", "web", "{place}", "web/{name}"])
def test_a_name_the_list_does_not_hold_is_not_found(station, asked: str) -> None:
    server, base, state = station
    name = _web_backup(state)
    stray = archive_name("OpenFollow", "0.4.3", _NOW)
    (state / stray).write_bytes((web_backup_dir(state / "config.toml") / name).read_bytes())
    before = (state / "config.toml").read_bytes()

    status, body = _restore(base, asked.format(place=stray, name=name))

    assert (status, body) == (
        404,
        {"error": "This backup is no longer on the station.", "action": "Reload the page."},
    )
    assert (state / "config.toml").read_bytes() == before
    assert _web_archives(state) == [name]
    assert server.check_restart_requested() is False


@pytest.mark.parametrize("state_name", ["queued", "running", "restarting"])
def test_a_restore_waits_for_a_running_update_and_writes_nothing(station, state_name: str) -> None:
    server, base, state = station
    name = _update_backup(state)
    server.set_update_status(state=state_name)
    before = (state / "config.toml").read_bytes()

    status, body = _restore(base, name)

    assert (status, body) == (
        409,
        {"error": "An update is running.", "action": "Restore the backup once it has finished."},
    )
    assert (state / "config.toml").read_bytes() == before
    assert _web_archives(state) == []
    assert server.check_restart_requested() is False


def test_an_update_queued_during_the_backup_refuses_the_restore(station, monkeypatch: pytest.MonkeyPatch) -> None:
    server, base, state = station
    monkeypatch.setattr(services_module, "clear_detached_update_state", lambda: None)
    _the_old_show(state)
    name = _update_backup(state)
    _the_show_now(state)
    before = (state / "config.toml").read_bytes()
    real_backup = routes_module._backup_before

    def back_up_then_queue_an_update(*args: Any) -> dict[str, str]:
        fields = real_backup(*args)
        assert server.request_deb_update("openfollow") is True
        return fields

    monkeypatch.setattr(routes_module, "_backup_before", back_up_then_queue_an_update)
    status, body = _restore(base, name)

    assert status == 409
    assert body["error"] == "An update is running."
    assert (state / "config.toml").read_bytes() == before
    assert _tree(state / "templates" / "user") == {"now.oftemplate": "now"}
    assert list(state.rglob(".*.restore")) == []
    assert server.check_restart_requested() is False


def test_a_backup_from_a_newer_version_is_refused_with_the_reason(station) -> None:
    server, base, state = station
    name = _web_backup(state, version="0.6.0")
    before = (state / "config.toml").read_bytes()

    status, body = _restore(base, name)

    assert (status, body) == (
        409,
        {
            "error": "This backup was made by OpenFollow 0.6.0, which is newer than this station (0.5.0rc3).",
            "action": "Update the station first.",
        },
    )
    assert (state / "config.toml").read_bytes() == before
    assert _web_archives(state) == [name]
    assert server.check_restart_requested() is False


def _member(name: str, data: bytes = b"", kind: bytes = tarfile.REGTYPE) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.size = len(data) if kind == tarfile.REGTYPE else 0
    info.linkname = "/etc/passwd" if kind != tarfile.REGTYPE else ""
    return info, data


def _archive(members: list[tuple[tarfile.TarInfo, bytes]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for info, data in members:
            tar.addfile(info, io.BytesIO(data) if info.isfile() else None)
    return buf.getvalue()


_CONFIG = _member("config.toml", b"[grid]\nwidth = 42.0\n")


@pytest.mark.parametrize(
    "data",
    [
        b"not gzip",
        _archive([_member("markers.toml", b"[[marker]]\n")]),
        _archive([_CONFIG, _member("templates/user/x", kind=tarfile.SYMTYPE)]),
        _archive([_CONFIG, _member("../escape", b"x")]),
        _archive([_CONFIG, _member("templates/user/big", b"x" * 64)]),
        _archive([_member("config.toml", b"not [[ toml")]),
        _archive([_member("config.toml", b"grid = 5\n")]),
        _archive([_CONFIG, _member("markers.toml", b"not [[ toml")]),
    ],
    ids=[
        "not-gzip",
        "no-config",
        "symlink",
        "dotdot",
        "oversize",
        "config-unparseable",
        "config-unbuildable",
        "catalog",
    ],
)
def test_a_damaged_backup_is_refused_and_leaves_everything(
    station, monkeypatch: pytest.MonkeyPatch, data: bytes
) -> None:
    server, base, state = station
    monkeypatch.setattr(config_restore, "MAX_MEMBER_BYTES", 32)
    name = archive_name("OpenFollow", "0.4.3", _NOW)
    (state / "backups").mkdir()
    (state / "backups" / name).write_bytes(data)
    before = (state / "config.toml").read_bytes()

    status, body = _restore(base, name)

    assert (status, body) == (422, {"error": "This backup could not be read.", "action": "Choose another one."})
    assert (state / "config.toml").read_bytes() == before
    assert _web_archives(state) == []
    assert server.check_restart_requested() is False


def test_a_restore_while_the_list_cannot_be_read_is_refused(station) -> None:
    server, base, state = station
    (state / "backups").write_text("a file where the folder goes", encoding="utf-8")
    status, body = _restore(base, archive_name("OpenFollow", "0.4.3", _NOW))
    assert (status, body) == (
        500,
        {"error": "The backups on this station could not be read.", "action": "Details in Logs."},
    )
    assert server.check_restart_requested() is False


def test_a_restore_that_cannot_be_staged_writes_nothing(station) -> None:
    server, base, state = station
    _the_old_show(state)
    name = _update_backup(state)
    _the_show_now(state)
    before = (state / "config.toml").read_bytes()
    # The station's catalog folder is a file: nothing can be written into it.
    (state / "show" / "markers.toml").unlink()
    (state / "show").rmdir()
    (state / "show").write_text("x", encoding="utf-8")

    status, body = _restore(base, name)

    assert (status, body) == (500, {"error": "The settings could not be written.", "action": "Details in Logs."})
    assert (state / "config.toml").read_bytes() == before
    assert _tree(state / "templates" / "user") == {"now.oftemplate": "now"}
    assert list(state.rglob(".*.restore")) == []
    assert server.check_restart_requested() is False


def test_a_restore_that_fails_to_write_never_restarts(station, monkeypatch: pytest.MonkeyPatch) -> None:
    server, base, state = station
    _the_old_show(state)
    name = _update_backup(state)
    _the_show_now(state)

    def fail(self: config_restore.StagedRestore) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(config_restore.StagedRestore, "commit", fail)
    status, body = _restore(base, name)

    assert (status, body) == (500, {"error": "The settings could not be written.", "action": "Details in Logs."})
    assert list(state.rglob(".*.restore")) == []
    assert server.check_restart_requested() is False


# --- a settings file that does not read -----------------------------------------


def test_a_restore_replaces_a_settings_file_that_does_not_read(station) -> None:
    server, base, state = station
    _the_old_show(state)
    name = _update_backup(state)
    _the_show_now(state)
    load_config(str(state / "config.toml"))  # the last whole read the identity comes from
    (state / "config.toml").write_bytes(b"not [[ toml")

    status, body = _restore(base, name)

    assert status == 200, body
    saved = load_config(str(state / "config.toml"), strict=True)
    assert (saved.grid.width, saved.station_id, saved.web_port) == (42.0, "abc123", 8123)
    assert server.check_restart_requested() is True


def test_a_restore_over_an_empty_settings_file_is_refused_for_the_restart_that_restores_it(station) -> None:
    server, base, state = station
    name = _update_backup(state)
    save_config(_identity(AppConfig()), str(state / "config.toml"))  # leaves a backup copy a restart restores
    (state / "config.toml").write_bytes(b"")

    status, body = _restore(base, name)

    assert status == 409
    assert body["settings_file"] == "empty-restorable"
    assert (state / "config.toml").read_bytes() == b""
    assert server.check_restart_requested() is False
