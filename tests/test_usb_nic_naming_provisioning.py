# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Provisioning contract for stable USB network adapter names.

Kernel ``ethN`` names are handed out in probe order, not by device identity, so
a station with two USB adapters can swap ``eth1`` and ``eth2`` across a reboot.
Every network plane pins an interface by *name*, and after a swap both names
still resolve - so the pin binds cleanly to the wrong adapter and stage data
leaves on the wrong network. It is the one case the fail-closed rule cannot
catch, because nothing is down. Naming every USB adapter after its MAC removes
the ordering entirely, and like the DHCP fallback it is provisioning: nothing in
the running app may rewrite an operator's network configuration.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path

import pytest

import openfollow

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(openfollow.__file__).resolve().parent.parent
_LINK_NAME = "72-openfollow-usb-net-by-mac.link"

# The image installs the .deb, so the .deb is the one route that carries the rule.
_SOURCES = {
    "deb link file": _REPO_ROOT / "packaging" / "debian" / "usb-net-by-mac.link",
    "deb build script": _REPO_ROOT / "packaging" / "build-deb.sh",
}

# Routes carrying the rule itself; the build script only installs it.
_BLOCK_SOURCES = ("deb link file",)
_INSTALLING_SOURCES = ("deb build script",)


def _read(name: str) -> str:
    path = _SOURCES[name]
    if not path.is_file():
        pytest.skip(f"no checkout {path.name} (wheel install)")
    return path.read_text(encoding="utf-8")


def _match_section(name: str) -> dict[str, str]:
    """The rule's ``[Match]`` keys."""
    keys: dict[str, str] = {}
    inside = False
    for line in _read(name).splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            inside = stripped == "[Match]"
        elif inside and "=" in stripped and not stripped.startswith("#"):
            key, _sep, value = stripped.partition("=")
            keys[key] = value
    return keys


def _list_matches(spec: str, value: str) -> bool:
    """systemd.link: a list of globs, any of which matches; a leading ``!`` inverts the list."""
    inverted = spec.startswith("!")
    hit = any(fnmatchcase(value, pattern) for pattern in spec.removeprefix("!").split())
    return hit != inverted


def _renamed(keys: dict[str, str], *, kind: str, id_path: str, driver: str, mac_name: bool) -> bool:
    """Whether the rule applies to a device: every key in ``[Match]`` has to."""
    checks = {
        "Type": lambda spec: _list_matches(spec, kind),
        "Path": lambda spec: _list_matches(spec, id_path),
        "Driver": lambda spec: _list_matches(spec, driver),
        "Property": lambda spec: spec == "ID_NET_NAME_MAC=*" and mac_name,
    }
    assert set(keys) <= set(checks), f"no model for match keys {set(keys) - set(checks)}"
    return all(checks[key](spec) for key, spec in keys.items())


@pytest.mark.parametrize("name", sorted(_INSTALLING_SOURCES))
def test_every_install_route_ships_the_link_file(name: str) -> None:
    """A route that skips it leaves that install method exposed to the reorder
    while the others are safe - the hardest kind of gap to notice, because it
    only shows up as data on the wrong network."""
    assert _LINK_NAME in _read(name)


@pytest.mark.parametrize("name", sorted(_BLOCK_SOURCES))
def test_routes_name_adapters_by_mac(name: str) -> None:
    assert "NamePolicy=mac" in _read(name)


# (type, ID_PATH, driver, has a MAC name) as udev reports them, and whether the name moves.
_USB = "platform-xhci-hcd.0-usb-0:1:1.0"
_DEVICES = {
    # An ASIX AX88179B in a Pi 5 USB socket, as the bench station reports it.
    "usb adapter, listed driver": (("ether", "platform-xhci-hcd.1-usb-0:2:2.0", "cdc_ncm", True), True),
    "usb adapter, realtek": (("ether", _USB, "r8152", True), True),
    # The gap a driver list left: any make of adapter is covered.
    "usb adapter, any other driver": (("ether", _USB, "a_driver_nobody_listed", True), True),
    "pi 5 onboard": (("ether", "platform-1f00100000.ethernet", "macb", True), False),
    "pi 3 onboard on usb": (("ether", "platform-3f980000.usb-usb-0:1.1:1.0", "smsc95xx", True), False),
    "pi 3b+ onboard on usb": (("ether", "platform-3f980000.usb-usb-0:1.1.1:1.0", "lan78xx", True), False),
    "pcie nic": (("ether", "pci-0000:01:00.0", "r8169", True), False),
    "usb adapter without a hardware mac": (("ether", _USB, "r8152", False), False),
    # The onboard NIC's drivers are kept out wherever they appear, so an adapter on one keeps ethN.
    "usb adapter on an onboard nic's driver": (("ether", _USB, "lan78xx", True), False),
    # Never covered by the driver list either: their names, and the profiles bound to them, stay.
    "usb wi-fi dongle": (("wlan", _USB, "mt7601u", True), False),
    "usb mobile modem": (("wwan", _USB, "qmi_wwan", True), False),
}


@pytest.mark.parametrize("name", sorted(_BLOCK_SOURCES))
@pytest.mark.parametrize("device", sorted(_DEVICES))
def test_usb_ethernet_adapters_and_no_onboard_nic_are_named_by_mac(name: str, device: str) -> None:
    """A USB Ethernet adapter gets its MAC name, whatever its make, so two can
    never trade names. The onboard NIC of the Pi 3 and earlier hangs off USB too;
    renaming it would dangle every eth0 pin an operator already has, so its two
    drivers are kept out."""
    (kind, id_path, driver, mac_name), renamed = _DEVICES[device]
    found = _renamed(_match_section(name), kind=kind, id_path=id_path, driver=driver, mac_name=mac_name)
    assert found is renamed


def test_the_match_model_follows_systemd_link() -> None:
    """The model above is what the device table proves against; it must invert
    a ``!`` list and require every key, or the table proves nothing."""
    assert _list_matches("!a b", "c") and not _list_matches("!a b", "b")
    assert _list_matches("*-usb-*", "platform-x-usb-0:1") and not _list_matches("*-usb-*", "pci-0000")
    assert not _renamed(
        {"Path": "*-usb-*", "Driver": "r8152"}, kind="ether", id_path="p-usb-1", driver="asix", mac_name=True
    )
    with pytest.raises(AssertionError, match="no model"):
        _renamed({"MACAddress": "x"}, kind="ether", id_path="", driver="", mac_name=True)


def test_deb_declares_the_link_file_as_a_conffile() -> None:
    """An operator who adjusted the match keeps it across an upgrade."""
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
