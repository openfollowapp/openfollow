# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""config.toml parsed once per change for the web server, each caller getting its own copy."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import openfollow.web.config_cache as cache_module
from openfollow.configuration import AppConfig, load_config, save_config
from openfollow.web.config_cache import ParsedConfigCache

pytestmark = pytest.mark.unit


@pytest.fixture
def parses(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    real = cache_module.load_config

    def _counting(path: str, **kwargs: Any) -> AppConfig:
        calls.append(path)
        return real(path, **kwargs)

    monkeypatch.setattr(cache_module, "load_config", _counting)
    return calls


@pytest.fixture
def config_file(tmp_path: Path) -> str:
    path = str(tmp_path / "config.toml")
    save_config(AppConfig(psn_system_name="Before"), path)
    return path


def _cache() -> ParsedConfigCache:
    """A cache whose clock reads the year 2100, so every file it reads is old enough to keep."""
    return ParsedConfigCache(now=lambda: 4_102_444_800.0)


def test_what_it_hands_out_is_the_config_in_the_file(config_file: str) -> None:
    assert _cache().load(config_file) == load_config(config_file)


def test_an_unchanged_file_is_parsed_once(config_file: str, parses: list[str]) -> None:
    cache = _cache()
    for _ in range(3):
        cache.load(config_file)
    assert parses == [config_file]


def test_a_save_is_seen_even_within_the_same_mtime(config_file: str, parses: list[str]) -> None:
    cache = _cache()
    cache.load(config_file)
    before = os.stat(config_file)
    save_config(AppConfig(psn_system_name="Behind"), config_file)  # the same length as "Before"
    os.utime(config_file, ns=(before.st_atime_ns, before.st_mtime_ns))  # a save that lands in the same tick
    assert os.stat(config_file).st_size == before.st_size, "only the inode may tell the two files apart"
    assert cache.load(config_file).psn_system_name == "Behind"
    assert len(parses) == 2


def test_an_edit_in_place_of_another_length_is_seen_within_the_same_mtime(config_file: str) -> None:
    cache = _cache()
    cache.load(config_file)
    before = os.stat(config_file)
    text = Path(config_file).read_text().replace('psn_system_name = "Before"', 'psn_system_name = "Much longer"')
    with open(config_file, "w") as handle:  # same file, same inode
        handle.write(text)
    os.utime(config_file, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert os.stat(config_file).st_size != before.st_size, "only the size may tell the two files apart"
    assert cache.load(config_file).psn_system_name == "Much longer"


def test_a_read_that_failed_is_served_but_never_kept(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    save_config(AppConfig(web_pin="4711"), str(path))
    Path(str(path) + ".bak").unlink(missing_ok=True)
    good = path.read_bytes()
    before = os.stat(path)
    cache = _cache()
    # Same inode, mtime and size: only the parse can tell the broken file apart.
    path.write_bytes(b"[" * len(good))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert cache.load(str(path)).web_pin == "", "the failed read falls back to defaults"
    path.write_bytes(good)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert cache.load(str(path)).web_pin == "4711", "kept, the defaults would leave the login open"


def test_an_edit_in_place_is_seen(config_file: str) -> None:
    cache = _cache()
    cache.load(config_file)
    text = Path(config_file).read_text().replace('psn_system_name = "Before"', 'psn_system_name = "Edited"')
    with open(config_file, "w") as handle:  # same file, same inode
        handle.write(text)
    stat = os.stat(config_file)
    os.utime(config_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert cache.load(config_file).psn_system_name == "Edited"


def test_each_caller_gets_its_own_copy(config_file: str) -> None:
    cache = _cache()
    first = cache.load(config_file)
    first.psn_system_name = "Changed in one request"
    first.trigger_zones.zones.append(None)  # type: ignore[arg-type]
    second = cache.load(config_file)
    assert second is not first
    assert second.psn_system_name == "Before"
    assert second.trigger_zones.zones == []


def test_a_missing_file_is_read_through_every_time(tmp_path: Path, parses: list[str]) -> None:
    missing = str(tmp_path / "absent.toml")
    cache = _cache()
    assert cache.load(missing) == load_config(missing)
    cache.load(missing)
    assert parses == [missing, missing]


def test_a_file_that_went_away_while_it_was_read_is_never_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.toml"
    save_config(AppConfig(web_pin="4711"), str(path))
    Path(str(path) + ".bak").unlink(missing_ok=True)
    aside = tmp_path / "aside.toml"
    real = cache_module.load_config

    def _vanishing(p: str, **kwargs: Any) -> AppConfig:
        if kwargs.get("strict") and path.exists():
            path.rename(aside)  # between the stat and the read; a strict read of no file is defaults
        return real(p, **kwargs)

    cache = _cache()
    monkeypatch.setattr(cache_module, "load_config", _vanishing)
    cache.load(str(path))
    monkeypatch.setattr(cache_module, "load_config", real)
    aside.rename(path)  # back with the same inode, mtime and size
    assert cache.load(str(path)).web_pin == "4711", "kept, the defaults would leave the login open"


def test_a_file_on_another_device_with_the_same_inode_is_read_again(
    config_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = _cache()
    cache.load(config_file)
    before = os.stat(config_file)
    text = Path(config_file).read_text().replace('psn_system_name = "Before"', 'psn_system_name = "Moved!"')
    with open(config_file, "w") as handle:  # same inode, same size
        handle.write(text)
    os.utime(config_file, ns=(before.st_atime_ns, before.st_mtime_ns))
    real_stat = os.stat

    def _other_device(p: object, *args: object, **kwargs: object) -> object:
        st = real_stat(p, *args, **kwargs)  # type: ignore[arg-type]
        if p != config_file:
            return st
        return SimpleNamespace(st_dev=st.st_dev + 1, st_ino=st.st_ino, st_mtime_ns=st.st_mtime_ns, st_size=st.st_size)

    monkeypatch.setattr(cache_module.os, "stat", _other_device)
    assert cache.load(config_file).psn_system_name == "Moved!"


@pytest.mark.parametrize(
    "broken", [b"", b"  \n", b"not [[ toml", None], ids=["empty", "whitespace", "unparseable", "missing"]
)
def test_a_file_that_broke_is_served_as_it_last_read_whole(tmp_path: Path, broken: bytes | None) -> None:
    """The backup is one save behind: a PIN set in the last save must not be dropped with the file."""
    path = tmp_path / "config.toml"
    save_config(AppConfig(psn_system_name="Backup"), str(path))
    save_config(AppConfig(psn_system_name="Latest", web_pin="4711"), str(path))
    cache = _cache()
    cache.load(str(path))
    if broken is None:
        path.unlink()
    else:
        path.write_bytes(broken)

    served = cache.load(str(path))

    assert (served.psn_system_name, served.web_pin) == ("Latest", "4711")


def test_a_file_broken_since_the_first_read_is_served_from_the_backup(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    save_config(AppConfig(psn_system_name="Backup"), str(path))
    save_config(AppConfig(psn_system_name="Latest"), str(path))
    path.write_bytes(b"")

    assert _cache().load(str(path)).psn_system_name == "Backup"


def test_a_backup_that_is_not_utf8_is_no_backup(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(b"")
    Path(str(path) + ".bak").write_bytes(b'psn_system_name = "\xff\xfe"\n')

    assert _cache().load(str(path)) == AppConfig()


@pytest.mark.parametrize("backup", [None, b"grid = 5\n"], ids=["no-backup", "backup-no-config"])
def test_a_file_that_is_no_config_before_any_whole_read_is_served_without_raising(tmp_path: Path, backup) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(b'camera = "x"\n')
    if backup is not None:
        Path(str(path) + ".bak").write_bytes(backup)

    assert _cache().load(str(path)) == AppConfig()


def test_an_older_parse_finishing_last_never_replaces_a_newer_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The last whole read is what a broken file is served as: it must be the newest, not the slowest."""
    path = tmp_path / "config.toml"
    save_config(AppConfig(psn_system_name="Older", web_pin="1111"), str(path))
    cache = _cache()
    real_key = cache_module._key
    calls: list[int] = []

    def _key(p: str) -> Any:
        key = real_key(p)
        calls.append(1)
        if len(calls) == 2:  # the first request has read the file whole; before it keeps it, another one runs
            save_config(AppConfig(psn_system_name="Newer", web_pin="2222"), str(path))
            assert cache.load(str(path)).web_pin == "2222"
        return key

    monkeypatch.setattr(cache_module, "_key", _key)
    assert cache.load(str(path)).web_pin == "1111"
    monkeypatch.setattr(cache_module, "_key", real_key)
    path.write_bytes(b"")

    assert cache.load(str(path)).web_pin == "2222"


def test_a_file_younger_than_a_second_is_parsed_again(config_file: str, parses: list[str]) -> None:
    """An edit in place within one mtime tick, of the same length, would not show in the key."""
    written = os.stat(config_file).st_mtime_ns / 1e9
    clock = {"now": written + 0.5}
    cache = ParsedConfigCache(now=lambda: clock["now"])
    cache.load(config_file)
    cache.load(config_file)
    assert len(parses) == 2
    clock["now"] = written + 1.5
    cache.load(config_file)
    cache.load(config_file)
    assert len(parses) == 3


def test_a_clock_set_behind_the_file_still_keeps_the_parse(config_file: str, parses: list[str]) -> None:
    """A station that boots with its clock behind the last save, as a Pi with no RTC does after a power cut."""
    written = os.stat(config_file).st_mtime_ns / 1e9
    cache = ParsedConfigCache(now=lambda: written - 3600)
    for _ in range(3):
        cache.load(config_file)
    assert len(parses) == 1


def test_a_file_that_is_no_text_by_the_second_look_is_served_as_parsed(
    config_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = cache_module.load_config

    def _then_garbled(p: str, **kwargs: Any) -> AppConfig:
        parsed = real(p, **kwargs)
        before = os.stat(p)
        with open(p, "r+b") as handle:  # same inode, size and mtime: the key still holds
            handle.write(b"\xff" * before.st_size)
        os.utime(p, ns=(before.st_atime_ns, before.st_mtime_ns))
        return parsed

    monkeypatch.setattr(cache_module, "load_config", _then_garbled)
    assert _cache().load(config_file).psn_system_name == "Before"


@pytest.mark.parametrize(
    ("otp", "kept"),
    [
        ('[otp_output]\nsource_ip = "192.0.2.10"\n', False),
        ('[otp_output]\nsource_ip = "192.0.2.10"\nsource_iface = "eth0"\n', True),
        ('[otp_output]\nsource_iface = "eth0"\n', True),
        ('[otp_output]\nsource_ip = " "\n', True),
        ("[otp_output]\nsource_ip = 5\n", True),
        ('psn_system_name = "Elsewhere"\n', True),
    ],
    ids=["ip", "ip-and-iface", "iface", "blank-ip", "ip-not-text", "no-otp"],
)
def test_an_otp_address_is_resolved_again_on_each_read(
    tmp_path: Path, parses: list[str], monkeypatch: pytest.MonkeyPatch, otp: str, kept: bool
) -> None:
    """The parse resolves it against live interfaces, which may get their address only later."""
    monkeypatch.setattr("openfollow.net_utils.get_iface_for_ip", lambda ip: "eth0")
    path = tmp_path / "config.toml"
    path.write_text(otp)
    cache = _cache()
    cache.load(str(path))
    cache.load(str(path))
    assert len(parses) == (1 if kept else 2)
