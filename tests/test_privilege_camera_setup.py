# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Applying a Pi camera: config first, live where the kernel allows, and the grants that allow it."""

from __future__ import annotations

import re
from typing import Any

import pytest

from openfollow.privilege.broker import PrivilegeError
from openfollow.privilege.camera_config import Camera, CameraSetupState
from openfollow.privilege.camera_setup import apply_camera, restart_station
from openfollow.privilege.capabilities import (
    ALL_CAPABILITIES,
    CAMERA_CONFIG_WRITE,
    CAMERA_OVERLAY_LOAD,
    CAMERA_OVERLAY_UNLOAD,
    SYSTEM_REBOOT,
)
from openfollow.privilege.drop_in import render_drop_in

pytestmark = pytest.mark.unit

OV = Camera("ov5647", "cam0")
IMX = Camera("imx708", "cam1")
_SENSORS = ("imx708", "ov5647")


class _Broker:
    """Runs nothing; checks each argv against its capability, as the real broker does first."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self._fail_on = fail_on

    def run(self, capability: Any, argv: list[str], **_kwargs: Any) -> None:
        capability.assert_argv_allowed(argv)
        self.calls.append((capability.name, argv[1:]))
        if capability.name == self._fail_on:
            raise PrivilegeError(f"{capability.description}: refused")


def _reads(*states: CameraSetupState):  # noqa: ANN202
    it = iter(states)
    return lambda: next(it)


def _state(**kw: Any) -> CameraSetupState:
    return CameraSetupState(available=True, sensors=_SENSORS, **kw)


def test_from_nothing_it_starts_now() -> None:
    broker = _Broker()
    after = _state(configured=OV, managed=True, active=(OV,), live=OV)
    state, changed = apply_camera(broker, OV, read=_reads(_state(), after))
    assert broker.calls == [
        ("camera.config_write", ["write", "ov5647,cam0"]),
        ("camera.overlay_load", ["load", "ov5647,cam0"]),
    ]
    assert (state, changed) == (after, True)


def test_switching_a_live_camera_unloads_it_first() -> None:
    broker = _Broker()
    before = _state(configured=OV, managed=True, active=(OV,), live=OV)
    apply_camera(broker, IMX, read=_reads(before, before))
    assert [name for name, _ in broker.calls] == ["camera.config_write", "camera.overlay_unload", "camera.overlay_load"]
    assert broker.calls[1] == ("camera.overlay_unload", ["unload", "ov5647,cam0"])


def test_the_same_live_camera_is_not_reloaded() -> None:
    broker = _Broker()
    before = _state(configured=OV, managed=True, active=(OV,), live=OV)
    _state_after, changed = apply_camera(broker, OV, read=_reads(before, before))
    assert [name for name, _ in broker.calls] == ["camera.config_write"]
    assert changed is False


def test_automatic_unloads_a_live_camera() -> None:
    broker = _Broker()
    before = _state(configured=OV, managed=True, active=(OV,), live=OV)
    _after, changed = apply_camera(broker, None, read=_reads(before, _state()))
    assert broker.calls == [
        ("camera.config_write", ["write", "automatic"]),
        ("camera.overlay_unload", ["unload", "ov5647,cam0"]),
    ]
    assert changed is True


def test_a_camera_loaded_at_boot_only_changes_config() -> None:
    """It cannot be unloaded while running: the change waits for a restart."""
    broker = _Broker()
    before = _state(configured=OV, managed=False, active=(OV,))
    after = _state(configured=IMX, managed=True, active=(OV,), pending=True)
    state, changed = apply_camera(broker, IMX, read=_reads(before, after))
    assert [name for name, _ in broker.calls] == ["camera.config_write"]
    assert (state.pending, changed) == (True, False)


def test_an_unavailable_station_is_refused() -> None:
    broker = _Broker()
    with pytest.raises(PrivilegeError, match="not installed"):
        apply_camera(
            broker, OV, read=_reads(CameraSetupState(available=False, reason="Camera setup is not installed."))
        )
    assert broker.calls == []


def test_a_sensor_the_station_lacks_is_refused() -> None:
    broker = _Broker()
    with pytest.raises(PrivilegeError, match="no imx477 camera overlay"):
        apply_camera(broker, Camera("imx477", "cam0"), read=_reads(_state()))
    assert broker.calls == []


def test_a_refused_write_stops_before_anything_is_loaded() -> None:
    broker = _Broker(fail_on="camera.config_write")
    with pytest.raises(PrivilegeError):
        apply_camera(broker, OV, read=_reads(_state()))
    assert [name for name, _ in broker.calls] == ["camera.config_write"]


def test_restart_reboots() -> None:
    broker = _Broker()
    restart_station(broker)  # type: ignore[arg-type]
    assert broker.calls == [("system.reboot", ["reboot"])]


class TestGrants:
    def test_all_four_are_registered_and_rendered(self) -> None:
        for capability in (CAMERA_CONFIG_WRITE, CAMERA_OVERLAY_LOAD, CAMERA_OVERLAY_UNLOAD, SYSTEM_REBOOT):
            assert capability in ALL_CAPABILITIES
        rendered = render_drop_in("openfollow")
        assert (
            "NOPASSWD: /usr/share/openfollow/camera-setup write "
            "^(automatic|(imx|ov|arducam|irs)[a-z0-9_-]*\\,cam[01])$" in rendered
        )
        assert (
            "NOPASSWD: /usr/share/openfollow/camera-setup load ^(imx|ov|arducam|irs)[a-z0-9_-]*\\,cam[01]$" in rendered
        )
        assert (
            "NOPASSWD: /usr/share/openfollow/camera-setup unload ^(imx|ov|arducam|irs)[a-z0-9_-]*\\,cam[01]$"
            in rendered
        )
        assert "NOPASSWD: /usr/bin/systemctl reboot\n" in rendered

    @pytest.mark.parametrize(
        "argv",
        [
            ["/usr/share/openfollow/camera-setup", "write", "../../etc/passwd"],
            ["/usr/share/openfollow/camera-setup", "write", "ov5647,cam0", "extra"],
            ["/usr/share/openfollow/camera-setup", "load", "automatic"],
            ["/usr/share/openfollow/camera-setup", "unload", "ov5647 cam0"],
            ["/usr/bin/python3", "write", "ov5647,cam0"],
        ],
    )
    def test_the_argv_check_refuses_anything_else(self, argv: list[str]) -> None:
        capability = {"write": CAMERA_CONFIG_WRITE, "load": CAMERA_OVERLAY_LOAD, "unload": CAMERA_OVERLAY_UNLOAD}[
            argv[1]
        ]
        with pytest.raises(ValueError):
            capability.assert_argv_allowed(argv)

    def test_reboot_takes_no_arguments(self) -> None:
        with pytest.raises(ValueError):
            SYSTEM_REBOOT.assert_argv_allowed(["/usr/bin/systemctl", "reboot", "--force"])

    @pytest.mark.parametrize("capability", [CAMERA_CONFIG_WRITE, CAMERA_OVERLAY_LOAD, CAMERA_OVERLAY_UNLOAD])
    def test_the_probe_argv_satisfies_its_own_rule(self, capability: Any) -> None:
        """Or ``sudo -n -ll`` never matches and the grant always reads as missing."""
        probe = capability.probe_command()
        assert re.fullmatch(capability.arg_pattern, probe[-1])
        capability.assert_argv_allowed(list(probe))
