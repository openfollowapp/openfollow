# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The directory a station keeps its device-local files in."""

from __future__ import annotations

from pathlib import Path

import pytest

from openfollow import station_dir

pytestmark = pytest.mark.unit


def test_before_a_config_is_named_it_is_the_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert station_dir.station_dir() == tmp_path


def test_the_appliance_default_config_name_keeps_the_service_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The Pi service starts in its state directory and names no config file.
    state = tmp_path / "var" / "lib" / "openfollow"
    state.mkdir(parents=True)
    monkeypatch.chdir(state)
    station_dir.set_config_path("config.toml")
    assert station_dir.station_dir() == state == Path.cwd()


def test_an_app_started_in_the_root_directory_uses_its_config_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A Finder-launched app starts in "/" with an absolute per-user config path.
    support = tmp_path / "Library" / "Application Support" / "OpenFollow"
    monkeypatch.chdir("/")
    station_dir.set_config_path(str(support / "config.toml"))
    assert station_dir.station_dir() == support


def test_the_anchor_does_not_follow_a_later_change_of_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    station_dir.set_config_path("config.toml")
    monkeypatch.chdir("/")
    assert station_dir.station_dir() == tmp_path
