# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Provisioning contract for the no-DHCP link-local fallback.

A station on a show LAN with no DHCP server must still self-assign an address,
or it is unreachable from the web UI and from SSH alike. Nothing in the running
app arms this - it is provisioning only, so every install route has to write the
same NetworkManager connection default: the image layer, the Ansible playbook,
and the ``.deb``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import openfollow

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(openfollow.__file__).resolve().parent.parent
_DROPIN_NAME = "10-openfollow-dhcp-fallback.conf"

_SOURCES = {
    "image layer": _REPO_ROOT / "packaging" / "image" / "layer" / "openfollow.yaml",
    "ansible playbook": _REPO_ROOT / "scripts" / "ansible" / "install-raspberry-pi.yml",
    "deb drop-in": _REPO_ROOT / "packaging" / "debian" / "nm-dhcp-fallback.conf",
    "deb build script": _REPO_ROOT / "packaging" / "build-deb.sh",
}

# NMSettingIP4LinkLocal 4 = fallback; 3 ("enabled") would put a 169.254 address
# on healthy interfaces too. 45s pins NM's own DHCP retry window.
_REQUIRED = (
    "ipv4.dhcp-timeout=45",
    "ipv4.link-local=4",
)

# NetworkManager accepts only a subset of properties as [connection] defaults.
# One it rejects, logged on every config read as "unknown key".
_REJECTED_BY_NETWORKMANAGER = ("ipv4.may-fail",)

# Routes carrying the property block itself; the build script only installs it.
_BLOCK_SOURCES = ("image layer", "ansible playbook", "deb drop-in")

# Routes that name the installed file. The deb's drop-in *is* the content, so
# it is the build script that has to reference the destination path.
_INSTALLING_SOURCES = ("image layer", "ansible playbook", "deb build script")


def _read(name: str) -> str:
    path = _SOURCES[name]
    if not path.is_file():
        pytest.skip(f"no checkout {path.name} (wheel install)")
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(_INSTALLING_SOURCES))
def test_every_install_route_ships_the_dropin(name: str) -> None:
    """A route that skips it leaves that install method dark on a DHCP-less
    LAN while the others are fine - the hardest kind of gap to notice."""
    assert _DROPIN_NAME in _read(name)


@pytest.mark.parametrize("name", sorted(_BLOCK_SOURCES))
@pytest.mark.parametrize("prop", _REQUIRED)
def test_routes_agree_on_the_properties(name: str, prop: str) -> None:
    """All three must write identical settings, or the wait before the
    fallback appears would depend on which path provisioned the unit."""
    assert prop in _read(name)


@pytest.mark.parametrize("name", sorted(_BLOCK_SOURCES))
def test_fallback_is_not_unconditionally_enabled(name: str) -> None:
    assert "ipv4.link-local=3" not in _read(name)


@pytest.mark.parametrize("name", sorted(_BLOCK_SOURCES))
@pytest.mark.parametrize("prop", _REJECTED_BY_NETWORKMANAGER)
def test_no_property_networkmanager_rejects_as_a_connection_default(name: str, prop: str) -> None:
    """A key NM does not accept here makes it log "unknown key" every time it
    reads its config, and leaves a provisioning file whose comments describe a
    setting that never applied - the reader has no way to tell which of the
    remaining ones are real."""
    assert prop not in _read(name)


def test_ansible_reloads_networkmanager_after_writing_it() -> None:
    """NetworkManager parses conf.d only at startup, so a drop-in written to an
    already-running Pi is inert until it re-reads. Without the notify the
    operator believes the fallback is armed and it is not."""
    text = _read("ansible playbook")
    assert "reload networkmanager" in text
    assert "nmcli general reload" in text


def test_deb_declares_the_dropin_as_a_conffile() -> None:
    """It is the one file this package ships under /etc. An operator who tuned
    the DHCP timeout must keep that value across an upgrade."""
    text = _read("deb build script")
    assert "DEBIAN/conffiles" in text
    assert f"/etc/NetworkManager/conf.d/{_DROPIN_NAME}" in text


def test_nothing_arms_the_fallback_at_runtime() -> None:
    """The app must never rewrite a NIC's persistent NetworkManager profile:
    an operator's network config is not ours to change while a show is running.
    The fallback is provisioning, and only provisioning."""
    for module in ("network/nm_adapter.py", "network/adapter.py", "network/dhcpcd_adapter.py", "services.py"):
        text = (_REPO_ROOT / "openfollow" / module).read_text(encoding="utf-8")
        assert "ensure_dhcp_fallback" not in text
        assert "ipv4.link-local" not in text
        assert "ipv4.dhcp-timeout" not in text


def test_every_install_route_ships_an_mdns_responder() -> None:
    """The HUD advertises <hostname>.local as the recovery route that survives
    an address change. A station without a responder answers nothing there, so
    the advice sends the operator to a dead name - worse than showing none."""
    assert "avahi-daemon" in (_REPO_ROOT / "packaging" / "debian" / "control.in").read_text(encoding="utf-8")
    assert "avahi-daemon" in _read("ansible playbook")
    assert "avahi-daemon" in _read("image layer")


def test_help_does_not_tie_the_fallback_to_the_dhcp_timeout() -> None:
    """``ipv4.link-local=4`` is not gated on ``ipv4.dhcp-timeout``: the 169.254
    address lands within seconds of the first failed lease attempt, not after
    the retry window. Quoting the timeout as the wait sends an operator away
    from a station that is already reachable."""
    help_text = (_REPO_ROOT / "openfollow" / "web" / "help" / "general-network-interface.md").read_text(
        encoding="utf-8"
    )
    timeout = next(prop for prop in _REQUIRED if prop.startswith("ipv4.dhcp-timeout")).split("=")[1]
    assert f"{timeout} seconds" not in help_text


def test_help_does_not_promise_the_station_name_resolves() -> None:
    """The name that answers mDNS is the machine's hostname, which is only
    usually the station name - the HUD shows the real one for that reason, and
    the help must not contradict it."""
    help_text = (_REPO_ROOT / "openfollow" / "web" / "help" / "general-network-interface.md").read_text(
        encoding="utf-8"
    )
    assert "<station-name>.local" not in help_text
