# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""One running station per config file."""

from __future__ import annotations

import os

import pytest

from openfollow.instance_lock import AlreadyRunningError, acquire_instance_lock, lock_path_for

pytestmark = pytest.mark.unit


def test_the_lock_sits_beside_the_config(tmp_path) -> None:
    assert lock_path_for(tmp_path / "config.toml") == tmp_path / "config.toml.lock"


def test_a_relative_config_locks_beside_its_absolute_path(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert lock_path_for("config.toml") == tmp_path / "config.toml.lock"


def test_a_second_holder_is_refused_and_named(tmp_path) -> None:
    config = tmp_path / "config.toml"
    held = acquire_instance_lock(config)
    assert held is not None

    with pytest.raises(AlreadyRunningError) as excinfo:
        acquire_instance_lock(config)

    assert excinfo.value.pid == os.getpid()
    assert excinfo.value.lock_path == tmp_path / "config.toml.lock"
    assert f"PID {os.getpid()}" in str(excinfo.value)
    held.close()


def test_a_released_lock_can_be_taken_again(tmp_path) -> None:
    config = tmp_path / "config.toml"
    first = acquire_instance_lock(config)
    assert first is not None
    first.close()

    second = acquire_instance_lock(config)

    assert second is not None
    assert (tmp_path / "config.toml.lock").read_text(encoding="ascii") == f"{os.getpid()}\n"
    second.close()


def test_a_stale_pid_from_a_dead_holder_does_not_block(tmp_path) -> None:
    (tmp_path / "config.toml.lock").write_text("999999\n", encoding="ascii")

    held = acquire_instance_lock(tmp_path / "config.toml")

    assert held is not None
    assert (tmp_path / "config.toml.lock").read_text(encoding="ascii") == f"{os.getpid()}\n"
    held.close()


def test_different_configs_do_not_share_a_lock(tmp_path) -> None:
    first = acquire_instance_lock(tmp_path / "a.toml")
    second = acquire_instance_lock(tmp_path / "b.toml")

    assert first is not None and second is not None
    first.close()
    second.close()


@pytest.mark.parametrize("content", ["", "abc", "-1"])
def test_a_holder_without_a_readable_pid_is_still_refused(tmp_path, content) -> None:
    config = tmp_path / "config.toml"
    held = acquire_instance_lock(config)
    assert held is not None
    held.seek(0)
    held.truncate()
    held.write(content)
    held.flush()

    with pytest.raises(AlreadyRunningError) as excinfo:
        acquire_instance_lock(config)

    assert excinfo.value.pid is None
    assert "another process" in str(excinfo.value)
    held.close()


def test_the_lock_is_not_inherited_by_an_exec(tmp_path) -> None:
    held = acquire_instance_lock(tmp_path / "config.toml")

    assert held is not None
    assert os.get_inheritable(held.fileno()) is False
    held.close()


def test_an_unopenable_lock_lets_the_station_run(tmp_path, caplog) -> None:
    held = acquire_instance_lock(tmp_path / "missing" / "config.toml")

    assert held is None
    assert "would not be refused" in caplog.text


def test_a_lock_the_filesystem_refuses_lets_the_station_run(tmp_path, monkeypatch, caplog) -> None:
    import openfollow.instance_lock as instance_lock

    def _refuse(fd, op):
        raise OSError(37, "No locks available")

    monkeypatch.setattr(instance_lock.fcntl, "flock", _refuse)

    assert acquire_instance_lock(tmp_path / "config.toml") is None
    assert "would not be refused" in caplog.text


def test_a_pid_that_cannot_be_recorded_still_holds_the_lock(tmp_path, monkeypatch) -> None:
    import openfollow.instance_lock as instance_lock

    config = tmp_path / "config.toml"
    real_getpid = os.getpid

    def _broken_getpid() -> int:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(instance_lock.os, "getpid", _broken_getpid)
    held = acquire_instance_lock(config)
    monkeypatch.setattr(instance_lock.os, "getpid", real_getpid)

    assert held is not None
    with pytest.raises(AlreadyRunningError):
        acquire_instance_lock(config)
    held.close()
