# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for NetworkManagerAdapter: nmcli argv shape, terse-output parsing, lease/state reads, broker apply/renew."""

from __future__ import annotations

import subprocess

import pytest

from openfollow.network.adapter import Ipv4Config, Ipv4Method
from openfollow.network.nm_adapter import NetworkManagerAdapter
from tests._fake_broker import FakeBroker, make_failure

pytestmark = pytest.mark.unit

_NMCLI_LONG: dict[str, str] = {
    "con": "connection",
    "mod": "modify",
}


def _normalise(argv: list[str]) -> list[str]:
    """Strip directory prefixes + expand the ``con mod`` short form to
    the legacy ``connection modify`` long form so test assertions
    written against the pre-broker shape keep matching. The broker
    refactor switched to absolute paths and ``con mod`` because that's
    what the generated sudoers rule literal-matches against."""
    if not argv:
        return argv
    out = [argv[0].rsplit("/", 1)[-1]] + list(argv[1:])
    out = [_NMCLI_LONG.get(a, a) for a in out]
    return out


@pytest.fixture
def adapter(monkeypatch):
    """Construct an NM adapter wired to a FakeBroker.

    ``captured`` holds **all** subprocess argvs the adapter attempted
    – both read calls (via ``_run``) and write calls (via the broker)
    – after normalisation so the legacy assertions stay readable. The
    broker's own call list is kept on ``broker.calls`` for tests that
    care about the privileged-vs-read split.
    """
    broker = FakeBroker()
    a = NetworkManagerAdapter(broker=broker)

    captured: list[list[str]] = []
    responses: dict[tuple[str, ...], subprocess.CompletedProcess] = {}

    def _run(argv, *, check=True):
        captured.append(_normalise(list(argv)))
        key = tuple(argv)
        if key in responses:
            return responses[key]
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(a, "_run", _run)

    # Mirror broker writes into ``captured`` so tests can assert on
    # them in the same shape as the legacy ``_run`` records.
    original_broker_run = broker.run

    def _spy(capability, argv, *, cwd=None, timeout=30.0, reason="", stdin=None):
        captured.append(_normalise(list(argv)))
        return original_broker_run(
            capability,
            argv,
            cwd=cwd,
            timeout=timeout,
            reason=reason,
            stdin=stdin,
        )

    broker.run = _spy  # type: ignore[method-assign]
    return a, captured, responses


def _set(responses, argv, stdout="", returncode=0):
    responses[tuple(argv)] = subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")


class TestListInterfaces:
    def test_parses_device_output(self, adapter) -> None:
        a, _captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"],
            stdout="eth0:ethernet:connected\nwlan0:wifi:disconnected\nlo:loopback:unmanaged\n",
        )
        ifaces = a.list_interfaces()
        names = {i.name: i for i in ifaces}
        assert names["eth0"].is_up is True
        assert names["wlan0"].is_up is False
        assert names["lo"].kind == "loopback"


class TestApplyIpv4:
    def _prime_connection(self, responses, name="Wired connection 1", device="eth0"):
        _set(
            responses,
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            stdout=f"{name}:{device}\n",
        )

    def test_static_issues_modify_argv(self, adapter) -> None:
        a, captured, responses = adapter
        self._prime_connection(responses)
        result = a.apply_ipv4(
            "eth0",
            Ipv4Config(
                method=Ipv4Method.STATIC,
                address="192.168.1.50",
                prefix=24,
                router="192.168.1.1",
                dns=("8.8.8.8", "1.1.1.1"),
            ),
        )
        assert result.ok is True
        modify = next(c for c in captured if c[:3] == ["nmcli", "connection", "modify"])
        assert "ipv4.method" in modify
        assert modify[modify.index("ipv4.method") + 1] == "manual"
        assert modify[modify.index("ipv4.addresses") + 1] == "192.168.1.50/24"
        assert modify[modify.index("ipv4.gateway") + 1] == "192.168.1.1"
        assert modify[modify.index("ipv4.dns") + 1] == "8.8.8.8 1.1.1.1"

    def test_static_zero_prefix_round_trips(self, adapter) -> None:
        """``/0`` (match-everything) is a valid IPv4 prefix and must round-trip correctly."""
        a, captured, responses = adapter
        self._prime_connection(responses)
        a.apply_ipv4(
            "eth0",
            Ipv4Config(
                method=Ipv4Method.STATIC,
                address="0.0.0.0",
                prefix=0,
                router="0.0.0.0",
            ),
        )
        modify = next(c for c in captured if c[:3] == ["nmcli", "connection", "modify"])
        assert modify[modify.index("ipv4.addresses") + 1] == "0.0.0.0/0"

    def test_dhcp_manual_zero_prefix_round_trips(self, adapter) -> None:
        """The ``/0`` correctness applies to the DHCP+manual branch as well."""
        a, captured, responses = adapter
        self._prime_connection(responses)
        a.apply_ipv4(
            "eth0",
            Ipv4Config(
                method=Ipv4Method.DHCP_WITH_MANUAL_ADDRESS,
                address="10.0.0.5",
                prefix=0,
            ),
        )
        modify = next(c for c in captured if c[:3] == ["nmcli", "connection", "modify"])
        assert modify[modify.index("ipv4.addresses") + 1] == "10.0.0.5/0"

    def test_dhcp_clears_static_fields(self, adapter) -> None:
        a, captured, responses = adapter
        self._prime_connection(responses)
        a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        modify = next(c for c in captured if c[:3] == ["nmcli", "connection", "modify"])
        assert modify[modify.index("ipv4.method") + 1] == "auto"
        assert modify[modify.index("ipv4.addresses") + 1] == ""
        assert modify[modify.index("ipv4.gateway") + 1] == ""
        assert modify[modify.index("ipv4.ignore-auto-dns") + 1] == "no"

    def test_dhcp_with_dns_overrides_sets_ignore_auto(self, adapter) -> None:
        a, captured, responses = adapter
        self._prime_connection(responses)
        a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP, dns=("9.9.9.9",)))
        modify = next(c for c in captured if c[:3] == ["nmcli", "connection", "modify"])
        # both keys appear; the second "ignore-auto-dns" must be "yes"
        idxs = [i for i, v in enumerate(modify) if v == "ipv4.ignore-auto-dns"]
        assert modify[idxs[-1] + 1] == "yes"

    def test_unknown_connection_returns_failure(self, adapter) -> None:
        a, _captured, responses = adapter
        _set(responses, ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"], stdout="")
        _set(responses, ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show"], stdout="")
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is False
        assert "No NetworkManager" in result.message


class TestRenewLease:
    def test_renew_does_down_then_up(self, adapter) -> None:
        a, captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            stdout="Wired:eth0\n",
        )
        result = a.renew_lease("eth0")
        assert result.ok is True
        down = [c for c in captured if c[:3] == ["nmcli", "connection", "down"]]
        up = [c for c in captured if c[:3] == ["nmcli", "connection", "up"]]
        assert down and up

    def test_renew_no_connection_returns_failure(self, adapter) -> None:
        a, _captured, responses = adapter
        _set(responses, ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"], stdout="")
        _set(responses, ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show"], stdout="")
        result = a.renew_lease("eth0")
        assert result.ok is False
        assert "No NetworkManager" in result.message

    def test_renew_propagates_up_failure(self, adapter) -> None:
        a, _captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            stdout="Wired:eth0\n",
        )
        # Renew: 1) con down (ok), 2) con up (fail). Seed broker
        # responses in that order.
        broker = a._broker
        broker.responses = [
            subprocess.CompletedProcess(["sudo"], 0, "", ""),  # down
            subprocess.CompletedProcess(["sudo"], 1, "", "iface down"),  # up
        ]
        result = a.renew_lease("eth0")
        assert result.ok is False
        assert "iface down" in result.message


class TestParseShow:
    def test_groups_repeated_keys(self) -> None:
        from openfollow.network.nm_adapter import NetworkManagerAdapter

        a = NetworkManagerAdapter()
        parsed = a._parse_show("foo:1\nfoo:2\nbar:x\n\n:skip\n")
        assert parsed["foo"] == ["1", "2"]
        assert parsed["bar"] == ["x"]

    def test_unescapes_terse_colon_escaping_in_value(self) -> None:
        """nmcli ``-t`` escapes a literal ``:`` inside a value as ``\\:``;
        the MAC in GENERAL.HWADDR must come back without stray backslashes
        (the field separator is still the first, unescaped colon)."""
        from openfollow.network.nm_adapter import NetworkManagerAdapter

        a = NetworkManagerAdapter()
        parsed = a._parse_show("GENERAL.HWADDR:AA\\:BB\\:CC\\:DD\\:EE\\:FF\n")
        assert parsed["GENERAL.HWADDR"] == ["AA:BB:CC:DD:EE:FF"]
        # A literal backslash escapes as ``\\\\``.
        assert a._parse_show("X:a\\\\b\n")["X"] == ["a\\b"]


class TestGetState:
    def _prime_state(self, responses, *, dev_show: str, method_show: str = "ipv4.method:auto\n", lease_show: str = ""):
        # device list (for ifaces dict)
        _set(
            responses,
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"],
            stdout="eth0:ethernet:connected\n",
        )
        _set(
            responses,
            ["nmcli", "-t", "-f", "IP4.ADDRESS,IP4.GATEWAY,IP4.DNS,GENERAL.HWADDR", "device", "show", "eth0"],
            stdout=dev_show,
        )
        _set(
            responses,
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            stdout="Wired:eth0\n",
        )
        _set(
            responses,
            ["nmcli", "-t", "-f", "ipv4.method,ipv4.addresses", "connection", "show", "Wired"],
            stdout=method_show,
        )
        _set(
            responses,
            ["nmcli", "-t", "-f", "DHCP4.OPTION", "device", "show", "eth0"],
            stdout=lease_show,
        )

    def test_unknown_iface_returns_none(self, adapter) -> None:
        a, _captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"],
            stdout="eth0:ethernet:connected\n",
        )
        assert a.get_state("nope0") is None

    def test_parses_address_and_dns(self, adapter, monkeypatch) -> None:
        a, _captured, responses = adapter
        # Freeze ``time.time`` so the ``expiry - now`` conversion in
        # ``_read_lease`` is deterministic. NM reports ``expiry`` as
        # an absolute epoch, so we craft a lease that ends 3600s
        # after the frozen "now".
        frozen_now = 1_700_000_000
        monkeypatch.setattr(
            "openfollow.network.nm_adapter.time.time",
            lambda: frozen_now,
        )
        self._prime_state(
            responses,
            dev_show=(
                "IP4.ADDRESS[1]:192.168.1.50/24\n"
                "IP4.GATEWAY:192.168.1.1\n"
                "IP4.DNS[1]:8.8.8.8\n"
                "IP4.DNS[2]:1.1.1.1\n"
                # Real ``nmcli -t`` output escapes the MAC's colons.
                "GENERAL.HWADDR:AA\\:BB\\:CC\\:DD\\:EE\\:FF\n"
            ),
            lease_show=(
                "DHCP4.OPTION[1]:ip_address = 192.168.1.50\n"
                "DHCP4.OPTION[2]:subnet_mask = 255.255.255.0\n"
                "DHCP4.OPTION[3]:routers = 192.168.1.1\n"
                "DHCP4.OPTION[4]:domain_name_servers = 8.8.8.8 1.1.1.1\n"
                f"DHCP4.OPTION[5]:expiry = {frozen_now + 3600}\n"
            ),
        )
        state = a.get_state("eth0")
        assert state is not None
        assert state.ipv4.address == "192.168.1.50"
        assert state.ipv4.prefix == 24
        assert state.ipv4.router == "192.168.1.1"
        assert state.ipv4.dns[:2] == ("8.8.8.8", "1.1.1.1")
        assert state.lease is not None
        assert state.lease.lease_seconds_remaining == 3600
        assert state.interface.mac == "AA:BB:CC:DD:EE:FF"

    def test_expired_lease_clamps_to_zero(self, adapter, monkeypatch) -> None:
        """An expiry epoch in the past clamps to 0 rather than surfacing a negative duration."""
        a, _captured, responses = adapter
        frozen_now = 1_700_000_000
        monkeypatch.setattr(
            "openfollow.network.nm_adapter.time.time",
            lambda: frozen_now,
        )
        self._prime_state(
            responses,
            dev_show="IP4.ADDRESS[1]:10.0.0.1/24\n",
            lease_show=(
                "DHCP4.OPTION[1]:ip_address = 10.0.0.1\n"
                # 100s in the past
                f"DHCP4.OPTION[2]:expiry = {frozen_now - 100}\n"
            ),
        )
        state = a.get_state("eth0")
        assert state is not None
        assert state.lease is not None
        assert state.lease.lease_seconds_remaining == 0

    def test_address_without_prefix(self, adapter) -> None:
        a, _captured, responses = adapter
        self._prime_state(
            responses,
            dev_show="IP4.ADDRESS[1]:10.0.0.5\n",
        )
        state = a.get_state("eth0")
        assert state is not None
        assert state.ipv4.address == "10.0.0.5"
        assert state.ipv4.prefix is None

    def test_address_with_bad_prefix(self, adapter) -> None:
        a, _captured, responses = adapter
        self._prime_state(
            responses,
            dev_show="IP4.ADDRESS[1]:10.0.0.5/notanumber\n",
        )
        state = a.get_state("eth0")
        assert state is not None
        assert state.ipv4.prefix is None

    def test_get_state_handles_subprocess_failure(self, adapter, monkeypatch) -> None:
        a, _captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"],
            stdout="eth0:ethernet:connected\n",
        )

        def fake_run(argv, *, check=True):
            if argv[:5] == ["nmcli", "-t", "-f", "IP4.ADDRESS,IP4.GATEWAY,IP4.DNS,GENERAL.HWADDR", "device"]:
                raise RuntimeError("boom")
            return responses.get(tuple(argv), None) or __import__("subprocess").CompletedProcess(argv, 0, "", "")

        a._run = fake_run
        assert a.get_state("eth0") is None


class TestReadMethod:
    def test_manual_with_dhcp_address_is_dhcp_manual(self, adapter) -> None:
        a, _captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            stdout="Wired:eth0\n",
        )
        _set(
            responses,
            ["nmcli", "-t", "-f", "ipv4.method,ipv4.addresses", "connection", "show", "Wired"],
            stdout="ipv4.method:manual\nipv4.addresses:dhcp-managed/24\n",
        )
        from openfollow.network.adapter import Ipv4Method

        assert a._read_method("eth0") == Ipv4Method.DHCP_WITH_MANUAL_ADDRESS

    def test_manual_without_dhcp_is_static(self, adapter) -> None:
        a, _captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            stdout="Wired:eth0\n",
        )
        _set(
            responses,
            ["nmcli", "-t", "-f", "ipv4.method,ipv4.addresses", "connection", "show", "Wired"],
            stdout="ipv4.method:manual\nipv4.addresses:192.168.1.50/24\n",
        )
        from openfollow.network.adapter import Ipv4Method

        assert a._read_method("eth0") == Ipv4Method.STATIC

    def test_no_connection_returns_dhcp(self, adapter) -> None:
        a, _captured, responses = adapter
        _set(responses, ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"], stdout="")
        _set(responses, ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show"], stdout="")
        from openfollow.network.adapter import Ipv4Method

        assert a._read_method("eth0") == Ipv4Method.DHCP

    def test_unknown_method_returns_dhcp(self, adapter) -> None:
        a, _captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            stdout="Wired:eth0\n",
        )
        _set(
            responses,
            ["nmcli", "-t", "-f", "ipv4.method,ipv4.addresses", "connection", "show", "Wired"],
            stdout="ipv4.method:somemode\n",
        )
        from openfollow.network.adapter import Ipv4Method

        assert a._read_method("eth0") == Ipv4Method.DHCP

    def test_subprocess_failure_returns_dhcp(self, adapter) -> None:
        a, _captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            stdout="Wired:eth0\n",
        )

        def fake_run(argv, *, check=True):
            if argv[:5] == ["nmcli", "-t", "-f", "ipv4.method,ipv4.addresses", "connection"]:
                raise RuntimeError("boom")
            return responses.get(tuple(argv)) or __import__("subprocess").CompletedProcess(argv, 0, "", "")

        a._run = fake_run
        from openfollow.network.adapter import Ipv4Method

        assert a._read_method("eth0") == Ipv4Method.DHCP


class TestReadLease:
    def test_empty_lease_returns_none(self, adapter) -> None:
        a, _captured, responses = adapter
        _set(responses, ["nmcli", "-t", "-f", "DHCP4.OPTION", "device", "show", "eth0"], stdout="")
        assert a._read_lease("eth0") is None

    def test_invalid_expiry_drops_to_none(self, adapter) -> None:
        a, _captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "DHCP4.OPTION", "device", "show", "eth0"],
            stdout=("DHCP4.OPTION[1]:ip_address = 10.0.0.1\nDHCP4.OPTION[2]:expiry = notanumber\n"),
        )
        lease = a._read_lease("eth0")
        assert lease is not None
        assert lease.lease_seconds_remaining is None

    def test_subprocess_failure_returns_none(self, adapter) -> None:
        a, _captured, _responses = adapter

        def fake_run(argv, *, check=True):
            raise RuntimeError("boom")

        a._run = fake_run
        assert a._read_lease("eth0") is None


class TestApplyVariants:
    def test_dhcp_with_manual_address_uses_lease_defaults(self, adapter) -> None:
        a, captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            stdout="Wired:eth0\n",
        )
        _set(
            responses,
            ["nmcli", "-t", "-f", "DHCP4.OPTION", "device", "show", "eth0"],
            stdout=(
                "DHCP4.OPTION[1]:ip_address = 10.0.0.50\n"
                "DHCP4.OPTION[2]:subnet_mask = 255.255.255.0\n"
                "DHCP4.OPTION[3]:routers = 10.0.0.1\n"
                "DHCP4.OPTION[4]:domain_name_servers = 9.9.9.9\n"
            ),
        )
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP_WITH_MANUAL_ADDRESS, address="10.0.0.77"))
        assert result.ok is True
        modify = next(c for c in captured if c[:3] == ["nmcli", "connection", "modify"])
        # gateway from lease
        assert modify[modify.index("ipv4.gateway") + 1] == "10.0.0.1"
        # DNS from lease (since user didn't override)
        assert "9.9.9.9" in modify[modify.index("ipv4.dns") + 1]

    def test_dhcp_with_manual_address_drops_invalid_lease_gateway_and_dns(self, adapter) -> None:
        """Lease-sourced gateway/DNS are validated before reaching the root
        nmcli argv – a rogue DHCP server's garbage values are dropped
        (gateway → empty) rather than applied verbatim."""
        a, captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            stdout="Wired:eth0\n",
        )
        _set(
            responses,
            ["nmcli", "-t", "-f", "DHCP4.OPTION", "device", "show", "eth0"],
            stdout=(
                "DHCP4.OPTION[3]:routers = not-an-ip\nDHCP4.OPTION[4]:domain_name_servers = 9.9.9.9 garbage 1.1.1.1\n"
            ),
        )
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP_WITH_MANUAL_ADDRESS, address="10.0.0.77"))
        assert result.ok is True
        modify = next(c for c in captured if c[:3] == ["nmcli", "connection", "modify"])
        # Invalid gateway dropped → empty (no garbage in the privileged argv).
        assert modify[modify.index("ipv4.gateway") + 1] == ""
        # Only the valid DNS entries survive, canonicalised and in order.
        assert modify[modify.index("ipv4.dns") + 1] == "9.9.9.9 1.1.1.1"

    def test_con_verbs_pass_profile_name_with_id_keyword(self, adapter) -> None:
        """``con mod`` / ``con up`` pass the profile name after the explicit
        ``id`` keyword so a leading-dash name is the connection ID, not
        consumed as an option. nmcli's ``con`` subcommands do not treat
        ``--`` as end-of-options (they read it as a literal name)."""
        a, captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            stdout="-weird:eth0\n",
        )
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        modify = next(c for c in captured if c[:3] == ["nmcli", "connection", "modify"])
        assert modify[3:5] == ["id", "-weird"]
        up = next(c for c in captured if c[:3] == ["nmcli", "connection", "up"])
        assert up[3:5] == ["id", "-weird"]

    def test_dhcp_with_manual_address_no_lease_falls_back_to_defaults(self, adapter) -> None:
        a, captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            stdout="Wired:eth0\n",
        )
        _set(
            responses,
            ["nmcli", "-t", "-f", "DHCP4.OPTION", "device", "show", "eth0"],
            stdout="",
        )
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP_WITH_MANUAL_ADDRESS, address="10.0.0.77"))
        assert result.ok is True
        modify = next(c for c in captured if c[:3] == ["nmcli", "connection", "modify"])
        assert modify[modify.index("ipv4.addresses") + 1] == "10.0.0.77/24"  # default /24

    def test_unsupported_method_returns_failure(self, adapter, monkeypatch) -> None:
        a, _captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            stdout="Wired:eth0\n",
        )
        from openfollow.network.adapter import Ipv4Config

        class _FakeMethod:
            value = "weird"

            def __eq__(self, other):
                return False

            def __hash__(self):
                return 0

        cfg = Ipv4Config.__new__(Ipv4Config)
        # Bypass dataclass init; populate fields manually so we can force a bad method.
        object.__setattr__(cfg, "method", _FakeMethod())
        object.__setattr__(cfg, "address", None)
        object.__setattr__(cfg, "prefix", None)
        object.__setattr__(cfg, "router", None)
        object.__setattr__(cfg, "dns", ())
        result = a.apply_ipv4("eth0", cfg)
        assert result.ok is False
        assert "Unsupported method" in result.message

    def test_apply_modify_failure_short_circuits(self, adapter) -> None:
        a, _captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            stdout="Wired:eth0\n",
        )
        # Apply: 1) con mod (fail) – should short-circuit before down/up.
        a._broker.responses = [
            subprocess.CompletedProcess(["sudo"], 1, "", "modify failed"),
        ]
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is False
        assert "modify failed" in result.message

    def test_apply_up_failure_reported_as_partial(self, adapter) -> None:
        a, _captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            stdout="Wired:eth0\n",
        )
        # Apply: 1) mod (ok), 2) down (ok), 3) up (fail) – apply is the
        # gate, so this surfaces as ``ok=False`` not a partial. Asserts
        # the message preserves the broker stderr.
        a._broker.responses = [
            subprocess.CompletedProcess(["sudo"], 0, "", ""),  # mod
            subprocess.CompletedProcess(["sudo"], 0, "", ""),  # down
            subprocess.CompletedProcess(["sudo"], 1, "", "up failed"),  # up
        ]
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is False
        assert "up failed" in result.message


class TestListInterfacesEdge:
    def test_skips_short_lines(self, adapter) -> None:
        a, _captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"],
            stdout="eth0:ethernet:connected\nshort\n",
        )
        ifaces = a.list_interfaces()
        names = {i.name for i in ifaces}
        assert names == {"eth0"}

    def test_subprocess_failure_returns_empty(self, adapter, monkeypatch) -> None:
        a, _captured, _responses = adapter

        def fake_run(argv, *, check=True):
            raise RuntimeError("nmcli missing")

        a._run = fake_run
        assert a.list_interfaces() == []


class TestConnectionForFallback:
    def test_fallback_to_all_connections_when_active_misses(self, adapter) -> None:
        a, _captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            stdout="",
        )
        _set(
            responses,
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show"],
            stdout="Wired-Saved:eth0\n",
        )
        assert a._connection_for("eth0") == "Wired-Saved"

    def test_fallback_subprocess_failure_returns_none(self, adapter) -> None:
        a, _captured, _responses = adapter

        def fake_run(argv, *, check=True):
            raise RuntimeError("missing")

        a._run = fake_run
        assert a._connection_for("eth0") is None

    def test_run_actually_uses_subprocess_run(self, monkeypatch) -> None:
        # Cover the real _run path (not the test fixture's override).
        import subprocess as sp

        from openfollow.network.nm_adapter import NetworkManagerAdapter

        calls = []

        def fake_subprocess_run(argv, capture_output=True, text=True, timeout=None):
            calls.append(list(argv))
            return sp.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

        monkeypatch.setattr(sp, "run", fake_subprocess_run)
        a = NetworkManagerAdapter()
        result = a._run(["nmcli", "--help"])
        assert result.stdout == "ok\n"
        assert calls == [["nmcli", "--help"]]

    def test_run_raises_on_check_failure(self, monkeypatch) -> None:
        import subprocess as sp

        from openfollow.network.nm_adapter import NetworkManagerAdapter

        monkeypatch.setattr(
            sp,
            "run",
            lambda argv, capture_output, text, timeout: sp.CompletedProcess(argv, 1, "", "boom"),
        )
        a = NetworkManagerAdapter()
        import pytest

        with pytest.raises(RuntimeError, match="boom"):
            a._run(["nmcli", "x"])


class TestGetStateNoAddressList:
    def test_dev_show_without_ip4_address_yields_none_address(self, adapter) -> None:
        a, _captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"],
            stdout="eth0:ethernet:connected\n",
        )
        _set(
            responses,
            ["nmcli", "-t", "-f", "IP4.ADDRESS,IP4.GATEWAY,IP4.DNS,GENERAL.HWADDR", "device", "show", "eth0"],
            stdout="",  # no IP4.ADDRESS[1] at all
        )
        _set(
            responses,
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            stdout="Wired:eth0\n",
        )
        _set(
            responses,
            ["nmcli", "-t", "-f", "ipv4.method,ipv4.addresses", "connection", "show", "Wired"],
            stdout="",
        )
        _set(
            responses,
            ["nmcli", "-t", "-f", "DHCP4.OPTION", "device", "show", "eth0"],
            stdout="",
        )
        state = a.get_state("eth0")
        assert state is not None
        assert state.ipv4.address is None


class TestLeaseEdgeKeys:
    def test_value_without_equals_is_skipped(self, adapter) -> None:
        a, _captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "DHCP4.OPTION", "device", "show", "eth0"],
            stdout=("DHCP4.OPTION[1]:noequalshere\nDHCP4.OPTION[2]:ip_address = 10.0.0.5\n"),
        )
        lease = a._read_lease("eth0")
        assert lease is not None
        assert lease.address == "10.0.0.5"

    def test_unknown_key_is_ignored(self, adapter) -> None:
        a, _captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "DHCP4.OPTION", "device", "show", "eth0"],
            stdout=("DHCP4.OPTION[1]:host_name = pi\nDHCP4.OPTION[2]:ip_address = 10.0.0.5\n"),
        )
        lease = a._read_lease("eth0")
        assert lease is not None
        assert lease.address == "10.0.0.5"


class TestBrokerNotConfigured:
    """``_run_privileged`` returns ``(False, "Broker not configured.")``
    when an adapter is built without a broker. apply/renew surface this
    as a clean ApplyResult so the on-screen banner doesn't hand the
    operator a stack trace."""

    def test_apply_without_broker_returns_broker_message(self, monkeypatch) -> None:
        import subprocess as sp

        from openfollow.network.adapter import Ipv4Config, Ipv4Method
        from openfollow.network.nm_adapter import NetworkManagerAdapter

        a = NetworkManagerAdapter()  # no broker

        # Prime the connection-name lookup; otherwise apply short-
        # circuits before reaching the broker path.
        def _run(argv, *, check=True):
            if argv[:5] == ["nmcli", "-t", "-f", "NAME,DEVICE", "connection"]:
                return sp.CompletedProcess(argv, 0, "Wired:eth0\n", "")
            return sp.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(a, "_run", _run)
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is False
        assert "Broker" in result.message

    def test_renew_without_broker_returns_broker_message(self, monkeypatch) -> None:
        import subprocess as sp

        from openfollow.network.nm_adapter import NetworkManagerAdapter

        a = NetworkManagerAdapter()  # no broker

        def _run(argv, *, check=True):
            return sp.CompletedProcess(argv, 0, "Wired:eth0\n", "")

        monkeypatch.setattr(a, "_run", _run)
        result = a.renew_lease("eth0")
        assert result.ok is False
        assert "Broker" in result.message

    def test_apply_privilege_error_surfaces(self, adapter) -> None:
        """A PrivilegeError on the modify step surfaces with the
        broker's message preserved – no unhandled traceback."""
        a, _captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            stdout="Wired:eth0\n",
        )
        a._broker.exceptions = [make_failure("operator cancelled")]
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is False
        assert "operator cancelled" in result.message


class TestApplyConnectionVerbFailures:
    def test_down_warning_then_up_failure_surfaces(self, adapter) -> None:
        """``con down`` failure is a partial warning (NM might already
        be down on a fresh boot). ``con up`` is the gate – when it
        fails after a flaky down, the apply still surfaces as
        ``ok=False`` with the up-failure detail."""
        a, _captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            stdout="Wired:eth0\n",
        )
        # Apply: 1) mod (ok), 2) down (fail; downgraded to warning),
        # 3) up (fail; gate).
        a._broker.responses = [
            subprocess.CompletedProcess(["sudo"], 0, "", ""),  # mod
            subprocess.CompletedProcess(["sudo"], 1, "", "down was already down"),  # down
            subprocess.CompletedProcess(["sudo"], 1, "", "up failed"),  # up
        ]
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is False
        assert "up failed" in result.message


class TestRenewSubprocessError:
    def test_renew_up_failure_surfaces_message(self, adapter) -> None:
        """The renew path mirrors the apply path: down (best-effort)
        then up (gate). A failing up step surfaces a clean message
        with the broker's stderr detail."""
        a, _captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            stdout="Wired:eth0\n",
        )
        a._broker.responses = [
            subprocess.CompletedProcess(["sudo"], 0, "", ""),  # down
            subprocess.CompletedProcess(["sudo"], 1, "", "nmcli missing"),  # up
        ]
        result = a.renew_lease("eth0")
        assert result.ok is False
        assert "nmcli missing" in result.message


class TestApplyBoundaryValidation:
    """``apply_ipv4`` re-validates operator-influenced values at the
    privileged boundary so unvalidated address/dns never reach the
    root-run nmcli argv – mirroring the dhcpcd adapter's contract.
    The connection is primed so a missing profile can't be what makes
    the apply fail; only validation should."""

    def _prime(self, responses, name="Wired", device="eth0"):
        _set(
            responses,
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            stdout=f"{name}:{device}\n",
        )

    @pytest.mark.parametrize("address", ["not-an-ip", "10.0.0.5 8.8.8.8", "10.0.0.5\nipv4.dns evil"])
    def test_rejects_invalid_static_address(self, adapter, address) -> None:
        a, captured, responses = adapter
        self._prime(responses)
        result = a.apply_ipv4(
            "eth0",
            Ipv4Config(method=Ipv4Method.STATIC, address=address, prefix=24, router="10.0.0.1"),
        )
        assert result.ok is False
        # No modify argv ever reached the privileged broker.
        assert not [c for c in captured if c[:3] == ["nmcli", "connection", "modify"]]

    def test_rejects_invalid_dhcp_manual_address(self, adapter) -> None:
        """The DHCP+manual path also re-validates ``config.address`` – the
        specific gap the static path's downstream router/dns checks missed."""
        a, captured, responses = adapter
        self._prime(responses)
        result = a.apply_ipv4(
            "eth0",
            Ipv4Config(method=Ipv4Method.DHCP_WITH_MANUAL_ADDRESS, address="not-an-ip"),
        )
        assert result.ok is False
        assert not [c for c in captured if c[:3] == ["nmcli", "connection", "modify"]]

    def test_rejects_invalid_dns_on_dhcp(self, adapter) -> None:
        a, captured, responses = adapter
        self._prime(responses)
        result = a.apply_ipv4(
            "eth0",
            Ipv4Config(method=Ipv4Method.DHCP, dns=("not-an-ip",)),
        )
        assert result.ok is False
        assert not [c for c in captured if c[:3] == ["nmcli", "connection", "modify"]]


class TestConnectionForActiveLoopFallthrough:
    def test_active_loop_no_match_then_fallback_returns_none(self, adapter) -> None:
        """Active list has rows but none for the requested iface; fallback
        list also empty – overall None."""
        a, _captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            stdout="Wifi:wlan0\n",  # rows present, but eth0 not in them
        )
        _set(
            responses,
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show"],
            stdout="",
        )
        assert a._connection_for("eth0") is None

    def test_fallback_subprocess_failure_after_empty_active(self, adapter) -> None:
        """First call returns empty, fallback call raises – overall None.
        Covers the second try/except (lines 81-82)."""
        a, _captured, responses = adapter
        # Track call number so the first nmcli (active) succeeds and the
        # fallback nmcli (all) raises.
        calls = {"n": 0}

        def fake_run(argv, *, check=True):
            if argv[:6] == ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show"]:
                calls["n"] += 1
                if calls["n"] == 1:
                    import subprocess as sp

                    return sp.CompletedProcess(argv, 0, "", "")
                raise RuntimeError("missing")
            import subprocess as sp

            return sp.CompletedProcess(argv, 0, "", "")

        a._run = fake_run
        assert a._connection_for("eth0") is None

    def test_fallback_loop_runs_without_match(self, adapter) -> None:
        """Active empty, fallback returns rows but none for the iface –
        loop completes, function returns None (covers 85->83 branch)."""
        a, _captured, responses = adapter
        _set(
            responses,
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            stdout="",
        )
        _set(
            responses,
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show"],
            stdout="Profile-A:other0\nProfile-B:other1\n",
        )
        assert a._connection_for("eth0") is None
