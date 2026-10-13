# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""config.toml parsed once per change for the web server, each caller getting its own copy."""

from __future__ import annotations

import os
from pathlib import Path
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


def test_what_it_hands_out_is_the_config_in_the_file(config_file: str) -> None:
    assert ParsedConfigCache().load(config_file) == load_config(config_file)


def test_an_unchanged_file_is_parsed_once(config_file: str, parses: list[str]) -> None:
    cache = ParsedConfigCache()
    for _ in range(3):
        cache.load(config_file)
    assert parses == [config_file]


def test_a_save_is_seen_even_within_the_same_mtime(config_file: str, parses: list[str]) -> None:
    cache = ParsedConfigCache()
    cache.load(config_file)
    before = os.stat(config_file)
    save_config(AppConfig(psn_system_name="Behind"), config_file)  # the same length as "Before"
    os.utime(config_file, ns=(before.st_atime_ns, before.st_mtime_ns))  # a save that lands in the same tick
    assert os.stat(config_file).st_size == before.st_size, "only the inode may tell the two files apart"
    assert cache.load(config_file).psn_system_name == "Behind"
    assert len(parses) == 2


def test_an_edit_in_place_of_another_length_is_seen_within_the_same_mtime(config_file: str) -> None:
    cache = ParsedConfigCache()
    cache.load(config_file)
    before = os.stat(config_file)
    text = Path(config_file).read_text().replace('psn_system_name = "Before"', 'psn_system_name = "Much longer"')
    with open(config_file, "w") as handle:  # same file, same inode
        handle.write(text)
    os.utime(config_file, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert os.stat(config_file).st_size != before.st_size, "only the size may tell the two files apart"
    assert cache.load(config_file).psn_system_name == "Much longer"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a file whatever its mode")
def test_a_read_that_failed_is_served_but_never_kept(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    save_config(AppConfig(web_pin="4711"), str(path))
    Path(str(path) + ".bak").unlink(missing_ok=True)
    cache = ParsedConfigCache()
    path.chmod(0)  # leaves the inode, mtime and size alone
    try:
        assert cache.load(str(path)).web_pin == "", "the failed read falls back to defaults"
    finally:
        path.chmod(0o600)
    assert cache.load(str(path)).web_pin == "4711", "kept, the defaults would leave the login open"


def test_an_edit_in_place_is_seen(config_file: str) -> None:
    cache = ParsedConfigCache()
    cache.load(config_file)
    text = Path(config_file).read_text().replace('psn_system_name = "Before"', 'psn_system_name = "Edited"')
    with open(config_file, "w") as handle:  # same file, same inode
        handle.write(text)
    stat = os.stat(config_file)
    os.utime(config_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert cache.load(config_file).psn_system_name == "Edited"


def test_each_caller_gets_its_own_copy(config_file: str) -> None:
    cache = ParsedConfigCache()
    first = cache.load(config_file)
    first.psn_system_name = "Changed in one request"
    first.trigger_zones.zones.append(None)  # type: ignore[arg-type]
    second = cache.load(config_file)
    assert second is not first
    assert second.psn_system_name == "Before"
    assert second.trigger_zones.zones == []


def test_a_missing_file_is_read_through_every_time(tmp_path: Path, parses: list[str]) -> None:
    missing = str(tmp_path / "absent.toml")
    cache = ParsedConfigCache()
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

    cache = ParsedConfigCache()
    monkeypatch.setattr(cache_module, "load_config", _vanishing)
    cache.load(str(path))
    monkeypatch.setattr(cache_module, "load_config", real)
    aside.rename(path)  # back with the same inode, mtime and size
    assert cache.load(str(path)).web_pin == "4711", "kept, the defaults would leave the login open"


def test_a_file_on_another_device_with_the_same_inode_is_read_again(
    config_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = ParsedConfigCache()
    cache.load(config_file)
    before = os.stat(config_file)
    text = Path(config_file).read_text().replace('psn_system_name = "Before"', 'psn_system_name = "Moved!"')
    with open(config_file, "w") as handle:  # same inode, same size
        handle.write(text)
    os.utime(config_file, ns=(before.st_atime_ns, before.st_mtime_ns))
    real_stat = os.stat

    def _other_device(p: str) -> os.stat_result:
        st = real_stat(p)
        fields = list(st)
        fields[2] = st.st_dev + 1  # st_dev
        return os.stat_result(fields)

    monkeypatch.setattr(cache_module.os, "stat", _other_device)
    assert cache.load(config_file).psn_system_name == "Moved!"
