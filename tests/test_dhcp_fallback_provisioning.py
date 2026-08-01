# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Provisioning contract for the no-DHCP link-local fallback.

A station on a show LAN with no DHCP server must still self-assign an address,
or it is unreachable from both the web UI and SSH. The app arms the fallback on
any profile it writes, but a unit whose Network page is never opened only gets
it from provisioning - so the image layer and the Ansible playbook have to
carry the same NetworkManager connection defaults, with the same DHCP timeout
the app uses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import openfollow
from openfollow.network.nm_adapter import _DHCP_TIMEOUT_S

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(openfollow.__file__).resolve().parent.parent
_IMAGE_LAYER = _REPO_ROOT / "packaging" / "image" / "layer" / "openfollow.yaml"
_ANSIBLE_PLAYBOOK = _REPO_ROOT / "scripts" / "ansible" / "install-raspberry-pi.yml"
_DROPIN = "/etc/NetworkManager/conf.d/10-openfollow-dhcp-fallback.conf"

# NMSettingIP4LinkLocal: 4 = fallback. "enabled" (3) would put a 169.254
# address on every healthy interface too.
_LINK_LOCAL_FALLBACK = "ipv4.link-local=4"


def _sources() -> list[tuple[str, str]]:
    out = []
    for path in (_IMAGE_LAYER, _ANSIBLE_PLAYBOOK):
        if not path.is_file():
            pytest.skip(f"no checkout {path.name} (wheel install)")
        out.append((path.name, path.read_text(encoding="utf-8")))
    return out


@pytest.mark.parametrize("name,text", _sources())
def test_provisioning_writes_the_fallback_dropin(name: str, text: str) -> None:
    assert _DROPIN.rsplit("/", 1)[-1] in text, f"{name} must write {_DROPIN}"


@pytest.mark.parametrize("name,text", _sources())
def test_fallback_is_not_unconditionally_enabled(name: str, text: str) -> None:
    """``enabled`` would add a 169.254 address to a healthy DHCP interface."""
    assert _LINK_LOCAL_FALLBACK in text
    assert "ipv4.link-local=3" not in text


@pytest.mark.parametrize("name,text", _sources())
def test_activation_may_finish_without_a_lease(name: str, text: str) -> None:
    """Without may-fail the interface retries forever and never falls back."""
    assert "ipv4.may-fail=true" in text


@pytest.mark.parametrize("name,text", _sources())
def test_dhcp_timeout_matches_the_app(name: str, text: str) -> None:
    """A provisioning timeout that drifts from the app's would make the wait
    before the fallback appear depend on which path wrote the profile."""
    assert f"ipv4.dhcp-timeout={_DHCP_TIMEOUT_S}" in text
