# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Provisioning contract for stable USB network adapter names.

Kernel ``ethN`` names are handed out in probe order, not by device identity, so
a station with two USB adapters can swap ``eth1`` and ``eth2`` across a reboot.
Every network plane pins an interface by *name*, and after a swap both names
still resolve - so the pin binds cleanly to the wrong adapter and stage data
leaves on the wrong network. It is the one case the fail-closed rule cannot
catch, because nothing is down. Naming an adapter after its MAC removes the
ordering entirely, and like the DHCP fallback it is provisioning: nothing in the
running app may rewrite an operator's network configuration.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import openfollow

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(openfollow.__file__).resolve().parent.parent
_LINK_NAME = "72-openfollow-usb-net-by-mac.link"

_SOURCES = {
    "image layer": _REPO_ROOT / "packaging" / "image" / "layer" / "openfollow.yaml",
    "ansible playbook": _REPO_ROOT / "scripts" / "ansible" / "install-raspberry-pi.yml",
    "deb link file": _REPO_ROOT / "packaging" / "debian" / "usb-net-by-mac.link",
    "deb build script": _REPO_ROOT / "packaging" / "build-deb.sh",
}

# Routes carrying the rule itself; the build script only installs it.
_BLOCK_SOURCES = ("image layer", "ansible playbook", "deb link file")
_INSTALLING_SOURCES = ("image layer", "ansible playbook", "deb build script")

# The onboard NIC is USB-attached on Pi 3 and Zero. Matching those drivers would
# rename eth0 on those boards and dangle every pin an operator already has.
_ONBOARD_USB_DRIVERS = ("smsc95xx", "lan78xx")


def _read(name: str) -> str:
    path = _SOURCES[name]
    if not path.is_file():
        pytest.skip(f"no checkout {path.name} (wheel install)")
    return path.read_text(encoding="utf-8")


def _matched_drivers(name: str) -> set[str]:
    """The drivers the rule actually matches.

    Read off the ``Driver=`` line rather than the file, because the comment
    above it names the onboard drivers precisely to say they are excluded - a
    substring search over the whole text cannot tell the two apart.
    """
    for line in _read(name).splitlines():
        stripped = line.strip()
        if stripped.startswith("Driver="):
            return set(stripped.removeprefix("Driver=").split())
    return set()


@pytest.mark.parametrize("name", sorted(_INSTALLING_SOURCES))
def test_every_install_route_ships_the_link_file(name: str) -> None:
    """A route that skips it leaves that install method exposed to the reorder
    while the others are safe - the hardest kind of gap to notice, because it
    only shows up as data on the wrong network."""
    assert _LINK_NAME in _read(name)


@pytest.mark.parametrize("name", sorted(_BLOCK_SOURCES))
def test_routes_name_adapters_by_mac(name: str) -> None:
    assert "NamePolicy=mac" in _read(name)


@pytest.mark.parametrize("name", sorted(_BLOCK_SOURCES))
def test_matched_by_driver_not_by_usb_path(name: str) -> None:
    """A ``Path=*-usb-*`` match would also catch the onboard NIC on the boards
    where it hangs off USB. Driver matching is what keeps eth0 called eth0."""
    assert _matched_drivers(name)
    assert "Path=*-usb-*" not in _read(name)


@pytest.mark.parametrize("name", sorted(_BLOCK_SOURCES))
@pytest.mark.parametrize("driver", _ONBOARD_USB_DRIVERS)
def test_onboard_usb_nic_drivers_are_never_matched(name: str, driver: str) -> None:
    """Renaming the onboard NIC would break every existing ``eth0`` pin on a
    Pi 3 / Zero, and the operator would meet it as a plane that stopped."""
    assert driver not in _matched_drivers(name)


@pytest.mark.parametrize("name", sorted(_BLOCK_SOURCES))
def test_the_usual_adapter_chipsets_are_covered(name: str) -> None:
    """The drivers behind the adapters an operator is likely to buy. One that is
    missing gets ordering-dependent names back without any signal."""
    matched = _matched_drivers(name)
    for driver in ("r8152", "ax88179_178a", "asix", "cdc_ether"):
        assert driver in matched


def test_deb_declares_the_link_file_as_a_conffile() -> None:
    """An operator who adjusted the match list keeps it across an upgrade."""
    text = _read("deb build script")
    assert "DEBIAN/conffiles" in text
    assert f"/etc/systemd/network/{_LINK_NAME}" in text


def test_nothing_renames_an_interface_at_runtime() -> None:
    """The app must never rewrite a station's network configuration while it is
    running. Stable naming is provisioning, and only provisioning."""
    for module in ("network/nm_adapter.py", "network/adapter.py", "network/dhcpcd_adapter.py", "services.py"):
        text = (_REPO_ROOT / "openfollow" / module).read_text(encoding="utf-8")
        assert "NamePolicy" not in text
        assert _LINK_NAME not in text
