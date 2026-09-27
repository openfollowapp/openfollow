# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""What the 3D Mouse section says for each published status block."""

from __future__ import annotations

from typing import Any

import pytest

from openfollow.input.mouse3d_status import (
    BackendState,
    DeviceState,
    Mouse3DDeviceStatus,
    Mouse3DStatus,
    Notice,
    none_connected,
    status_key,
    status_notices,
)

pytestmark = pytest.mark.unit


def _device(state: DeviceState, *, name: str = "SpaceNavigator", vid: int = 0x046D, pid: int = 0xC626) -> Any:
    return Mouse3DDeviceStatus(
        path="/dev/hidraw2",
        product_name=name,
        vendor_id=vid,
        product_id=pid,
        port_key="usb:platform/xhci-hcd.1:1",
        state=state,
        error="raw wording",
    )


def _block(**overrides: Any) -> dict[str, Any]:
    status = Mouse3DStatus(enabled=True, supported=True, scanned=True)
    block = status.to_dict()
    block.update(overrides)
    return block


def test_the_block_is_published_with_its_wire_values() -> None:
    status = Mouse3DStatus(
        enabled=True,
        supported=True,
        scanned=True,
        backend=BackendState.COULD_NOT_START,
        backend_error="boom",
        backend_version="2.1.0",
        devices=(_device(DeviceState.NOT_PERMITTED, vid=0x46D, pid=0x626),),
    )
    assert status.to_dict() == {
        "enabled": True,
        "supported": True,
        "scanned": True,
        "backend": "could_not_start",
        "backend_error": "boom",
        "backend_version": "2.1.0",
        "devices": [
            {
                "path": "/dev/hidraw2",
                "product_name": "SpaceNavigator",
                # Zero-padded, the form lsusb and the log line use.
                "usb_id": "046d:0626",
                "port_key": "usb:platform/xhci-hcd.1:1",
                "state": "not_permitted",
                "error": "raw wording",
            }
        ],
    }


@pytest.mark.parametrize("enabled", [True, False])
def test_an_unsupported_platform_says_so_whether_enabled_or_not(enabled: bool) -> None:
    block = _block(enabled=enabled, supported=False, backend="could_not_start")
    assert status_notices(block) == [
        Notice("info", "3D Mouse input is not supported on macOS by this version of OpenFollow.")
    ]
    assert not none_connected(block)


def test_switched_off_shows_nothing_even_with_a_fault() -> None:
    block = _block(enabled=False, backend="could_not_start", devices=[_device(DeviceState.NO_PROFILE).to_dict()])
    assert status_notices(block) == []
    assert not none_connected(block)


@pytest.mark.parametrize(
    ("backend", "text"),
    [
        ("not_installed", "3D Mouse support is not installed."),
        ("could_not_start", "3D Mouse support could not start, so no 3D Mouse can be read."),
    ],
)
def test_a_backend_that_cannot_look_is_one_error_whatever_is_attached(backend: str, text: str) -> None:
    block = _block(backend=backend, devices=[_device(DeviceState.NO_PROFILE).to_dict()])
    assert status_notices(block) == [Notice("error", text, "Reinstall OpenFollow.")]
    assert not none_connected(block)


@pytest.mark.parametrize(
    ("state", "text", "step"),
    [
        (
            DeviceState.NO_PROFILE,
            "SpaceNavigator (046d:c626) is not supported by this version of OpenFollow.",
            "Use a supported model.",
        ),
        (
            DeviceState.NOT_PERMITTED,
            "SpaceNavigator (046d:c626) was found, but OpenFollow is not allowed to open it.",
            "Reinstall OpenFollow, then replug the 3D Mouse.",
        ),
        (
            DeviceState.OPEN_FAILED,
            "SpaceNavigator (046d:c626) was found, but could not be opened.",
            "Unplug it and plug it back in.",
        ),
    ],
)
def test_each_device_fault_names_the_unit_and_one_step(state: DeviceState, text: str, step: str) -> None:
    block = _block(devices=[_device(state).to_dict()])
    assert status_notices(block) == [Notice("error", text, step)]


@pytest.mark.parametrize("state", [DeviceState.OPEN, DeviceState.OPENING])
def test_a_working_or_opening_puck_gets_no_box(state: DeviceState) -> None:
    block = _block(devices=[_device(state).to_dict()])
    assert status_notices(block) == []
    # Something is attached, so it is not "none connected" either.
    assert not none_connected(block)


def test_a_puck_without_a_product_name_is_still_named() -> None:
    block = _block(devices=[_device(DeviceState.NO_PROFILE, name="", vid=0x256F, pid=0xC62F).to_dict()])
    assert status_notices(block)[0].text == "3D Mouse (256f:c62f) is not supported by this version of OpenFollow."


def test_every_faulty_puck_gets_its_own_box_in_order() -> None:
    block = _block(
        devices=[
            _device(DeviceState.NOT_PERMITTED, name="Compact").to_dict(),
            _device(DeviceState.OPEN, name="Working").to_dict(),
            _device(DeviceState.NO_PROFILE, name="Receiver").to_dict(),
        ]
    )
    assert [n.text.split(" (")[0] for n in status_notices(block)] == ["Compact", "Receiver"]


def test_a_state_this_version_does_not_know_gets_no_box() -> None:
    assert status_notices(_block(devices=[{"state": "later_state", "usb_id": "256f:c635"}])) == []


def test_none_connected_needs_every_condition() -> None:
    assert none_connected(_block())


@pytest.mark.parametrize(
    "overrides",
    [
        {"scanned": False},
        {"enabled": False},
        {"supported": False},
        {"backend": "could_not_start"},
        {"devices": [{"state": "opening"}]},
    ],
    ids=["first-scan-pending", "disabled", "unsupported", "backend-fault", "a-puck-attached"],
)
def test_none_connected_is_not_claimed_otherwise(overrides: dict[str, Any]) -> None:
    assert not none_connected(_block(**overrides))


def test_an_empty_block_shows_nothing() -> None:
    assert status_notices({}) == []
    assert not none_connected({})


def test_the_key_follows_what_is_shown_only() -> None:
    fault = _block(devices=[_device(DeviceState.NOT_PERMITTED).to_dict()])
    # Detail the section never shows does not change the key: no swap, no re-announce.
    same = _block(backend_version="9.9", devices=[{**_device(DeviceState.NOT_PERMITTED).to_dict(), "error": "other"}])
    assert status_key(fault) == status_key(same)
    other = _block(devices=[_device(DeviceState.OPEN_FAILED).to_dict()])
    assert status_key(fault) != status_key(other)
    assert status_key(_block()) != status_key(_block(scanned=False))
