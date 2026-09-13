# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the IPv4 validation helpers: parsing, masks, DNS list, router-subnet, validate_apply, VLAN create."""

from __future__ import annotations

import pytest

from openfollow.network.adapter import Ipv4Method
from openfollow.network.validate import (
    is_link_local,
    parse_dns_list,
    parse_ipv4,
    parse_prefix,
    parse_vlan_id,
    prefix_to_mask,
    router_in_subnet,
    validate_apply,
    validate_vlan_create,
    vlan_interface_name,
)

pytestmark = pytest.mark.unit


class TestIsLinkLocal:
    @pytest.mark.parametrize(
        "value",
        ["169.254.0.1", "169.254.255.254", "  169.254.8.31  ", "169.254.0.0"],
    )
    def test_link_local(self, value: str) -> None:
        assert is_link_local(value) is True

    @pytest.mark.parametrize(
        "value",
        # 169.253/169.255 bracket the block: an off-by-one on the second octet
        # would classify a routable address as a DHCP failure.
        ["192.168.1.5", "10.0.0.1", "169.253.1.1", "169.255.1.1", "127.0.0.1"],
    )
    def test_routable(self, value: str) -> None:
        assert is_link_local(value) is False

    @pytest.mark.parametrize("value", ["", "   ", None, "not-an-ip", "fe80::1"])
    def test_unparseable_is_not_link_local(self, value) -> None:
        assert is_link_local(value) is False


class TestParseIpv4:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("192.168.1.1", "192.168.1.1"),
            ("  10.0.0.1  ", "10.0.0.1"),
            ("0.0.0.0", "0.0.0.0"),
            ("255.255.255.255", "255.255.255.255"),
        ],
    )
    def test_valid(self, value: str, expected: str) -> None:
        assert parse_ipv4(value) == expected

    @pytest.mark.parametrize(
        "value",
        ["", "   ", "not-an-ip", "256.0.0.1", "1.2.3", "1.2.3.4.5", None, "::1"],
    )
    def test_invalid(self, value) -> None:
        assert parse_ipv4(value) is None


class TestParsePrefix:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("24", 24),
            ("/24", 24),
            (24, 24),
            ("0", 0),
            ("32", 32),
            ("255.255.255.0", 24),
            ("255.255.0.0", 16),
            ("255.0.0.0", 8),
            ("0.0.0.0", 0),
            ("255.255.255.255", 32),
        ],
    )
    def test_valid(self, value, expected: int) -> None:
        assert parse_prefix(value) == expected

    @pytest.mark.parametrize(
        "value",
        [
            "33",
            "-1",
            "abc",
            "",
            None,
            "255.255.0.255",  # non-contiguous mask
            "128.0.0.1",  # not a valid mask
            "²⁴",  # superscript digits: str.isdigit True, int() would raise
            "٥",  # Arabic-Indic digit: not ASCII
            "//24",  # only one leading slash is stripped
        ],
    )
    def test_invalid(self, value) -> None:
        # Must return None, never raise (the unicode cases previously crashed
        # int() with ValueError straight out of the apply route).
        assert parse_prefix(value) is None


class TestPrefixToMask:
    """Round-trip with :func:`parse_prefix`: every prefix length must
    render as a mask that ``parse_prefix`` accepts back to the same
    integer. Operators see the mask form in both the on-screen and
    web UI today; mismatches between the two would surface as silent
    truncation when they hit Apply."""

    @pytest.mark.parametrize(
        "prefix,expected_mask",
        [
            (0, "0.0.0.0"),
            (8, "255.0.0.0"),
            (16, "255.255.0.0"),
            (24, "255.255.255.0"),
            (25, "255.255.255.128"),
            (30, "255.255.255.252"),
            (32, "255.255.255.255"),
        ],
    )
    def test_valid(self, prefix: int, expected_mask: str) -> None:
        assert prefix_to_mask(prefix) == expected_mask
        # Round-trip: the rendered mask must parse back to the original prefix.
        assert parse_prefix(expected_mask) == prefix

    @pytest.mark.parametrize("bad", [None, -1, 33, "24", 3.14])
    def test_invalid_returns_none(self, bad) -> None:
        assert prefix_to_mask(bad) is None


class TestRouterInSubnet:
    def test_router_inside(self) -> None:
        assert router_in_subnet("192.168.1.50", 24, "192.168.1.1") is True

    def test_router_outside(self) -> None:
        assert router_in_subnet("192.168.1.50", 24, "10.0.0.1") is False

    def test_invalid_inputs(self) -> None:
        assert router_in_subnet("not-an-ip", 24, "192.168.1.1") is False
        assert router_in_subnet("192.168.1.50", 99, "192.168.1.1") is False


class TestParseDnsList:
    def test_comma_separated(self) -> None:
        assert parse_dns_list("8.8.8.8, 1.1.1.1") == ["8.8.8.8", "1.1.1.1"]

    def test_whitespace_separated(self) -> None:
        assert parse_dns_list("8.8.8.8 1.1.1.1") == ["8.8.8.8", "1.1.1.1"]

    def test_caps_at_three(self) -> None:
        result = parse_dns_list("1.1.1.1 2.2.2.2 3.3.3.3 4.4.4.4")
        assert result == ["1.1.1.1", "2.2.2.2", "3.3.3.3"]

    def test_dedupes(self) -> None:
        assert parse_dns_list("8.8.8.8 8.8.8.8") == ["8.8.8.8"]

    def test_drops_invalid(self) -> None:
        assert parse_dns_list("8.8.8.8 garbage 1.1.1.1") == ["8.8.8.8", "1.1.1.1"]

    def test_empty(self) -> None:
        assert parse_dns_list("") == []
        assert parse_dns_list(None) == []


class TestValidateApply:
    def test_dhcp_no_other_fields_required(self) -> None:
        assert validate_apply(Ipv4Method.DHCP, None, None, None, None) == []

    def test_dhcp_allows_dns(self) -> None:
        assert validate_apply(Ipv4Method.DHCP, None, None, None, ["8.8.8.8"]) == []

    def test_static_requires_address_and_prefix_but_not_router(self) -> None:
        errors = validate_apply(Ipv4Method.STATIC, None, None, None, None)
        assert any("IP address" in e for e in errors)
        assert any("Subnet prefix" in e for e in errors)
        # Router is optional for flat show networks (no gateway).
        assert not any("Router" in e for e in errors)

    def test_static_valid(self) -> None:
        assert validate_apply(Ipv4Method.STATIC, "192.168.1.50", 24, "192.168.1.1", ["8.8.8.8"]) == []

    def test_static_valid_without_router(self) -> None:
        # Flat Art-Net/sACN/PSN LAN: address + prefix, no gateway.
        assert validate_apply(Ipv4Method.STATIC, "192.168.1.50", 24, None, []) == []

    def test_static_invalid_router_still_rejected(self) -> None:
        # A router that *is* supplied must still be a valid IPv4.
        errors = validate_apply(Ipv4Method.STATIC, "192.168.1.50", 24, "not-an-ip", [])
        assert any("Router must be a valid IPv4 address." in e for e in errors)

    def test_static_whitespace_only_router_treated_as_unset(self) -> None:
        # Whitespace-only router is "unset", not an error (like blank gateway on flat show LAN).
        assert validate_apply(Ipv4Method.STATIC, "192.168.1.50", 24, "   ", []) == []

    def test_static_router_canonicalised_before_subnet_check(self) -> None:
        # Padded-but-valid router must be normalised before the subnet check.
        assert validate_apply(Ipv4Method.STATIC, " 192.168.1.50 ", 24, " 192.168.1.1 ", []) == []

    def test_static_padded_router_outside_subnet_still_caught(self) -> None:
        # Normalisation must not mask a genuinely out-of-subnet router.
        errors = validate_apply(Ipv4Method.STATIC, "192.168.1.50", 24, " 10.0.0.1 ", [])
        assert any("not inside the subnet" in e for e in errors)

    def test_static_router_must_be_in_subnet(self) -> None:
        errors = validate_apply(Ipv4Method.STATIC, "192.168.1.50", 24, "10.0.0.1", [])
        assert any("not inside the subnet" in e for e in errors)

    def test_dhcp_manual_requires_address_but_not_router(self) -> None:
        errors = validate_apply(Ipv4Method.DHCP_WITH_MANUAL_ADDRESS, None, None, None, None)
        assert any("IP address" in e for e in errors)
        assert not any("Router" in e for e in errors)

    def test_dhcp_manual_with_address_valid(self) -> None:
        assert validate_apply(Ipv4Method.DHCP_WITH_MANUAL_ADDRESS, "192.168.1.50", None, None, []) == []

    def test_too_many_dns(self) -> None:
        errors = validate_apply(Ipv4Method.DHCP, None, None, None, ["1.1.1.1", "2.2.2.2", "3.3.3.3", "4.4.4.4"])
        assert any("At most" in e for e in errors)

    def test_invalid_dns(self) -> None:
        errors = validate_apply(Ipv4Method.DHCP, None, None, None, ["garbage"])
        assert any("DNS server" in e for e in errors)

    def test_invalid_mask_text_returns_none(self) -> None:
        # Hit the AddressValueError branch in parse_prefix's mask path.
        assert parse_prefix("999.0.0.0") is None
        assert parse_prefix("abc.def.ghi.jkl") is None

    def test_empty_tokens_in_dns_list(self) -> None:
        # Trailing/leading separators produce empty tokens; the loop
        # must skip them (line 68 of validate.py).
        assert parse_dns_list(",,8.8.8.8,,1.1.1.1,,") == ["8.8.8.8", "1.1.1.1"]

    def test_static_router_outside_subnet_skips_chained_check(self) -> None:
        """Static method with valid address+prefix+router but router outside
        subnet hits the chained validation (line 99->117 branch)."""
        errors = validate_apply(Ipv4Method.STATIC, "192.168.1.50", 24, "10.0.0.1", [])
        assert any("not inside" in e for e in errors)

    def test_static_with_bad_prefix_skips_router_subnet_check(self) -> None:
        errors = validate_apply(Ipv4Method.STATIC, "192.168.1.50", -1, "192.168.1.1", [])
        # Prefix error reported; subnet-membership error must NOT also fire
        # because prefix is out of range.
        assert any("Subnet prefix" in e for e in errors)
        assert not any("not inside the subnet" in e for e in errors)


class TestVlanValidation:
    """VLAN create is the one path that can name a link into existence, so
    every rejection here is what stops a bad name reaching ``nmcli con add``."""

    _IFACES = ("lo", "eth0", "eth0.10", "wlan0")
    _VLANS = ("eth0.10",)

    def _errors(self, parent: str, vlan_id: object) -> list[str]:
        return validate_vlan_create(
            parent,
            vlan_id,  # type: ignore[arg-type]
            interfaces=self._IFACES,
            vlan_names=self._VLANS,
        )

    def test_accepts_a_new_vlan_on_a_physical_parent(self) -> None:
        assert self._errors("eth0", 20) == []

    @pytest.mark.parametrize("vlan_id", [0, 4095, -1, 4096, 100000])
    def test_rejects_reserved_and_out_of_range_ids(self, vlan_id: int) -> None:
        assert any("VLAN ID" in e for e in self._errors("eth0", vlan_id))

    @pytest.mark.parametrize("vlan_id", [1, 4094])
    def test_accepts_the_boundary_ids(self, vlan_id: int) -> None:
        assert self._errors("eth0", vlan_id) == []

    @pytest.mark.parametrize("vlan_id", ["abc", None, "", True, 12.5, "1e3"])
    def test_rejects_non_integer_ids(self, vlan_id: object) -> None:
        assert any("VLAN ID" in e for e in self._errors("eth0", vlan_id))

    def test_accepts_a_digit_string_id(self) -> None:
        assert self._errors("eth0", "20") == []

    def test_rejects_a_vlan_parent(self) -> None:
        assert any("stacked" in e for e in self._errors("eth0.10", 20))

    def test_rejects_loopback_parent(self) -> None:
        assert any("loopback" in e for e in self._errors("lo", 20))

    def test_rejects_unknown_parent(self) -> None:
        assert any("not a network interface" in e for e in self._errors("eth9", 20))

    def test_rejects_blank_parent(self) -> None:
        assert any("Choose a parent" in e for e in self._errors("   ", 20))

    def test_rejects_a_duplicate_derived_name(self) -> None:
        assert any("already exists" in e for e in self._errors("eth0", 10))

    def test_rejects_a_name_over_ifnamsiz(self) -> None:
        errors = validate_vlan_create(
            "verylongifname",
            4094,
            interfaces=("verylongifname",),
        )
        assert any("longer than" in e for e in errors)

    def test_blank_parent_does_not_also_report_a_name_error(self) -> None:
        """The derived-name checks need a parent to derive from; reporting
        'the interface name .20 is too long' on top of 'choose a parent'
        would be noise."""
        errors = self._errors("", 20)
        assert errors == ["Choose a parent interface."]


class TestVlanInterfaceName:
    def test_derives_dot_notation(self) -> None:
        assert vlan_interface_name("eth0", 10) == "eth0.10"

    def test_strips_surrounding_whitespace(self) -> None:
        assert vlan_interface_name("  eth0  ", 10) == "eth0.10"


class TestParseVlanId:
    @pytest.mark.parametrize(("value", "expected"), [("10", 10), (10, 10), (" 10 ", 10)])
    def test_accepts_valid(self, value: object, expected: int) -> None:
        assert parse_vlan_id(value) == expected  # type: ignore[arg-type]

    def test_rejects_bool_which_is_an_int_subclass(self) -> None:
        assert parse_vlan_id(True) is None
