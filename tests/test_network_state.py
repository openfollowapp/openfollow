# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for NetworkState.address_source: the operator-facing origin of an interface's address."""

from __future__ import annotations

import pytest

from openfollow.network.adapter import (
    Ipv4Config,
    Ipv4Method,
    NetworkInterface,
    NetworkState,
)

pytestmark = pytest.mark.unit


def _state(method: Ipv4Method, address: str | None) -> NetworkState:
    return NetworkState(
        interface=NetworkInterface(name="eth0", mac=None, kind="ethernet", is_up=True),
        ipv4=Ipv4Config(method=method, address=address, prefix=24),
        lease=None,
    )


class TestAddressSource:
    @pytest.mark.parametrize("method", list(Ipv4Method))
    @pytest.mark.parametrize("address", [None, ""])
    def test_no_address_reports_none(self, method: Ipv4Method, address: str | None) -> None:
        assert _state(method, address).address_source == "none"

    @pytest.mark.parametrize("method", list(Ipv4Method))
    def test_link_local_outranks_the_configured_method(self, method: Ipv4Method) -> None:
        # NM hands out the 169.254 fallback while the profile still reads
        # ``auto``, so the address has to win over the method or a DHCP
        # failure would render as a healthy DHCP lease.
        assert _state(method, "169.254.8.31").address_source == "link-local"

    def test_dhcp_lease_reports_dhcp(self) -> None:
        assert _state(Ipv4Method.DHCP, "192.168.1.5").address_source == "dhcp"

    @pytest.mark.parametrize(
        "method",
        [Ipv4Method.STATIC, Ipv4Method.DHCP_WITH_MANUAL_ADDRESS],
    )
    def test_operator_chosen_address_reports_static(self, method: Ipv4Method) -> None:
        assert _state(method, "10.20.0.5").address_source == "static"
