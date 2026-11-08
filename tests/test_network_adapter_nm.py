# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for NetworkManagerAdapter: nmcli argv shape, terse-output parsing, lease/state reads, broker apply/renew."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

import pytest

from openfollow.network import nm_adapter
from openfollow.network.adapter import (
    AddressSourceReading,
    BackendReadError,
    Ipv4Config,
    Ipv4Method,
    LeaseInfo,
    NetworkInterface,
    NetworkReading,
    NetworkState,
    VlanInterface,
)
from openfollow.network.nm_adapter import NetworkManagerAdapter
from openfollow.privilege.capabilities import NETWORK_NM_CON_ADD, NETWORK_NM_CON_DELETE
from tests._fake_broker import FakeBroker, make_failure, make_nonzero

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
def adapter(monkeypatch, tmp_path):
    """Construct an NM adapter wired to a FakeBroker.

    ``captured`` holds **all** subprocess argvs the adapter attempted
    – both read calls (via ``_run``) and write calls (via the broker)
    – after normalisation so the legacy assertions stay readable. The
    broker's own call list is kept on ``broker.calls`` for tests that
    care about the privileged-vs-read split.
    """
    monkeypatch.setattr(nm_adapter, "_SYS_CLASS_NET", tmp_path)
    broker = FakeBroker()
    a = NetworkManagerAdapter(broker=broker)

    captured: list[list[str]] = []
    responses: dict[tuple[str, ...], subprocess.CompletedProcess] = {}

    def _run(argv, *, check=True):
        captured.append(_normalise(list(argv)))
        result = responses.get(tuple(argv)) or subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if check and result.returncode != 0:
            raise RuntimeError(f"{' '.join(argv)} failed (rc={result.returncode})")
        return result

    monkeypatch.setattr(a, "_run", _run)

    # Mirror broker writes into ``captured`` so tests can assert on
    # them in the same shape as the legacy ``_run`` records.
    original_broker_run = broker.run

    def _spy(capability, argv, *, cwd=None, timeout=30.0, reason="", stdin=None, allow_prompt=True):
        captured.append(_normalise(list(argv)))
        return original_broker_run(
            capability,
            argv,
            cwd=cwd,
            timeout=timeout,
            reason=reason,
            stdin=stdin,
            allow_prompt=allow_prompt,
        )

    broker.run = _spy  # type: ignore[method-assign]
    return a, captured, responses


_PROFILES = ["nmcli", "-t", "-f", nm_adapter._PROFILE_LIST_FIELDS, "connection", "show"]


def _active(responses, *rows: str) -> None:
    """The profile list with each ``name:device`` of *rows* active on its device, its UUID ``uuid-<name>``."""
    listed = []
    for row in rows:
        name, _, device = row.rpartition(":")
        listed.append(f"{name}:uuid-{name}:802-3-ethernet:{device}:yes:0:0\n")
    _set(responses, _PROFILES, stdout="".join(listed))


def _bound_to(*uuids: str) -> list[str]:
    argv = ["nmcli", "-t", "-f", nm_adapter._PROFILE_FIELDS, "connection", "show"]
    for uuid in uuids:
        argv += ["uuid", uuid]
    return argv


def _written(a: NetworkManagerAdapter, iface: str) -> str | None:
    """The UUID an Apply to *iface* writes; None when it writes nothing."""
    a.apply_ipv4(iface, Ipv4Config(method=Ipv4Method.DHCP))
    return a._broker.calls[0].argv[4] if a._broker.calls else None


def _carrier(iface: str, value: str) -> None:
    """The kernel's link flag for *iface*, under the fixture's sysfs root."""
    (nm_adapter._SYS_CLASS_NET / iface).mkdir()
    (nm_adapter._SYS_CLASS_NET / iface / "carrier").write_text(value)


# What the bench's nmcli 1.52 printed, and the exit status it gave, for a profile
# activated on an adapter with no cable and a con down of an inactive profile.
_NO_SUITABLE_DEVICE = subprocess.CompletedProcess(
    ["sudo"],
    4,
    "",
    "Error: Connection activation failed: No suitable device found for this connection.",
)
_NOT_ACTIVE = subprocess.CompletedProcess(["sudo"], 10, "", "Error: 'Wired' is not an active connection.")
_OK = subprocess.CompletedProcess(["sudo"], 0, "", "")


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
        _active(responses, f"{name}:{device}")

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
        _set(responses, _PROFILES, stdout="")
        # Device absent from nmcli entirely: says to check the adapter, which
        # is the only one of the three causes that fits an unknown device.
        _set(responses, ["nmcli", "-t", "-f", "DEVICE,STATE", "device"], stdout="")
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is False
        assert "is not present" in result.message


class TestActionableMessages:
    """Each cause needs a different operator action, so each needs its own
    message - "no profile bound" described adapter state and told them nothing."""

    def _no_profile(self, responses) -> None:
        _set(responses, _PROFILES, stdout="")

    def test_unmanaged_device_says_how_to_hand_it_to_networkmanager(self, adapter) -> None:
        a, _captured, responses = adapter
        self._no_profile(responses)
        _set(responses, ["nmcli", "-t", "-f", "DEVICE,STATE", "device"], stdout="eth0:unmanaged\n")
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is False
        assert "not managed by NetworkManager" in result.message

    def test_known_device_without_a_profile_says_how_to_get_one(self, adapter) -> None:
        a, _captured, responses = adapter
        self._no_profile(responses)
        _set(responses, ["nmcli", "-t", "-f", "DEVICE,STATE", "device"], stdout="eth0:disconnected\n")
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert "No saved profile" in result.message
        assert "Connect the cable once" in result.message

    @pytest.mark.parametrize("device_list", ["", "eth1:connected\nwlan0:disconnected\n"])
    def test_absent_device_says_to_check_the_adapter(self, adapter, device_list: str) -> None:
        """Absent whether the device table is empty or simply lists others -
        the second is the realistic case and used to fall through the loop."""
        a, _captured, responses = adapter
        self._no_profile(responses)
        _set(responses, ["nmcli", "-t", "-f", "DEVICE,STATE", "device"], stdout=device_list)
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert "is not present" in result.message

    def test_an_unreadable_device_state_is_not_reported_as_absent(self, adapter, monkeypatch) -> None:
        """nmcli timing out is not evidence the adapter is gone. Telling the
        operator their plugged-in NIC "is not present" contradicts the card
        they just clicked to get here."""
        a, _captured, responses = adapter
        self._no_profile(responses)
        original = a._run

        def _run(argv, *, check=True):
            if list(argv)[:5] == ["nmcli", "-t", "-f", "DEVICE,STATE", "device"]:
                raise RuntimeError("nmcli wedged")
            return original(argv, check=check)

        monkeypatch.setattr(a, "_run", _run)
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is False
        assert "Could not read" in result.message
        assert "not present" not in result.message

    def test_every_apply_message_fits_the_on_screen_banner(self, adapter) -> None:
        """The Settings > Network banner is one truncated line, so a message
        long enough to wrap loses exactly the actionable half - and that banner
        is the operator's surface when the web UI is unreachable."""
        a, _captured, responses = adapter
        self._no_profile(responses)
        for state in ("", "unmanaged", "disconnected"):
            _set(responses, ["nmcli", "-t", "-f", "DEVICE,STATE", "device"], stdout=f"eth0:{state}\n")
            message = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP)).message
            assert len(message) <= 80, f"{message!r} will be truncated on the HUD"

    @pytest.mark.parametrize(
        "config",
        [Ipv4Config(method=Ipv4Method.DHCP), Ipv4Config(method=Ipv4Method.STATIC, address="192.0.2.50", prefix=24)],
        ids=["dhcp", "static"],
    )
    def test_an_adapter_without_a_link_is_saved_and_left_alone(self, adapter, config) -> None:
        """Saved for when the cable goes in. A con down here marks the profile as
        switched off by the operator, and NetworkManager then never brings it up."""
        a, captured, responses = adapter
        _active(responses, "Wired:eth0")
        _carrier("eth0", "0\n")
        result = a.apply_ipv4("eth0", config)
        assert (result.ok, result.pending) == (True, True)
        assert result.message == "Saved; the settings take effect when eth0 has a link."
        assert result.partial_failures == ()
        assert [c[1:3] for c in captured if c[0] == "nmcli" and c[1] != "-t"] == [["connection", "modify"]]

    def test_a_failed_save_without_a_link_is_not_pending(self, adapter) -> None:
        a, _captured, responses = adapter
        _active(responses, "Wired:eth0")
        _carrier("eth0", "0\n")
        a._broker.responses = [subprocess.CompletedProcess(["sudo"], 1, "", "Sorry, user may not run nmcli con mod")]
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert (result.ok, result.pending) == (False, False)
        assert "may not run" in result.message

    @pytest.mark.parametrize("carrier", [None, "1\n"], ids=["no-carrier-flag", "has-link"])
    def test_with_a_link_a_failed_activation_is_reported(self, adapter, carrier) -> None:
        """An admin-down interface (rfkill) reports no carrier at all; it is not
        told to check a cable."""
        a, _captured, responses = adapter
        _active(responses, "Wired:eth0")
        if carrier is not None:
            _carrier("eth0", carrier)
        a._broker.responses = [_OK, _OK, _NO_SUITABLE_DEVICE]
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert (result.ok, result.pending) == (False, False)
        assert _NO_SUITABLE_DEVICE.stderr in result.message

    def test_con_down_of_an_inactive_profile_is_not_a_warning(self, adapter) -> None:
        a, _captured, responses = adapter
        _active(responses, "Wired:eth0")
        a._broker.responses = [_OK, _NOT_ACTIVE, _OK]
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert (result.ok, result.message, result.partial_failures) == (True, "Applied.", ())

    def test_a_real_con_down_failure_is_still_reported(self, adapter) -> None:
        a, _captured, responses = adapter
        _active(responses, "Wired:eth0")
        a._broker.responses = [_OK, subprocess.CompletedProcess(["sudo"], 1, "", "Error: timed out."), _OK]
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is True
        assert result.partial_failures == ("nmcli con down: Bring NetworkManager connection down: Error: timed out.",)

    # The broker gives up on con up after its timeout; NetworkManager keeps going.
    _TIMED_OUT = "Bring NetworkManager connection up: timed out after 8s."

    def test_dhcp_with_no_server_answering_is_saved_not_failed(self, adapter) -> None:
        """With no DHCP server the activation never completes, so con up always
        outlasts the broker - the profile is applied and its fallback in use."""
        a, _captured, responses = adapter
        _active(responses, "Wired:eth0")
        _set(
            responses,
            ["nmcli", "-t", "-f", "DEVICE,STATE", "device"],
            stdout="eth0:connecting (getting IP configuration)\n",
        )
        a._broker.exceptions = [None, None, make_failure(self._TIMED_OUT)]
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert (result.ok, result.pending) == (True, True)
        assert result.message == "Saved; no DHCP server has answered on eth0 yet."

    @pytest.mark.parametrize(
        ("config", "state"),
        [
            (
                Ipv4Config(method=Ipv4Method.STATIC, address="192.0.2.50", prefix=24),
                "connecting (getting IP configuration)",
            ),
            (Ipv4Config(method=Ipv4Method.DHCP), "disconnected"),
        ],
        ids=["static", "not-activating"],
    )
    def test_any_other_failed_activation_is_still_a_failure(self, adapter, config, state) -> None:
        a, _captured, responses = adapter
        _active(responses, "Wired:eth0")
        _set(responses, ["nmcli", "-t", "-f", "DEVICE,STATE", "device"], stdout=f"eth0:{state}\n")
        a._broker.exceptions = [None, None, make_failure(self._TIMED_OUT)]
        result = a.apply_ipv4("eth0", config)
        assert (result.ok, result.pending) == (False, False)
        assert self._TIMED_OUT in result.message

    def test_renew_with_no_server_answering_says_so(self, adapter) -> None:
        a, _captured, responses = adapter
        _active(responses, "Wired:eth0")
        _set(
            responses,
            ["nmcli", "-t", "-f", "DEVICE,STATE", "device"],
            stdout="eth0:connecting (getting IP configuration)\n",
        )
        a._broker.exceptions = [None, make_failure(self._TIMED_OUT)]
        result = a.renew_lease("eth0")
        assert result.ok is False
        assert result.message == "No DHCP server has answered on eth0 yet."

    def test_renew_without_a_link_explains_why_and_leaves_the_profile_alone(self, adapter) -> None:
        a, _captured, responses = adapter
        _active(responses, "Wired:eth0")
        _carrier("eth0", "0\n")
        result = a.renew_lease("eth0")
        assert result.ok is False
        assert result.message == "eth0 has no link, so there is no lease to request."
        assert a._broker.calls == []

    def test_renew_does_not_blame_the_cable_for_a_real_failure(self, adapter) -> None:
        """A missing helper or an ungranted rule is not a link problem, and
        sending the operator to check a cable hides the actual fix."""
        a, _captured, responses = adapter
        _active(responses, "Wired:eth0")
        _carrier("eth0", "1\n")
        a._broker.exceptions = [None, make_failure("not authorised")]
        result = a.renew_lease("eth0")
        assert result.ok is False
        assert "not authorised" in result.message
        assert "no link" not in result.message

    def test_save_failure_names_the_profile(self, adapter) -> None:
        a, _captured, responses = adapter
        _active(responses, "Wired:eth0")
        a._broker.exceptions = [make_failure("read-only")]
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is False
        assert "Wired" in result.message
        assert "read-only" in result.message


class TestRenewLease:
    def test_renew_does_down_then_up(self, adapter) -> None:
        a, captured, responses = adapter
        _active(responses, "Wired:eth0")
        result = a.renew_lease("eth0")
        assert result.ok is True
        down = [c for c in captured if c[:3] == ["nmcli", "connection", "down"]]
        up = [c for c in captured if c[:3] == ["nmcli", "connection", "up"]]
        assert down and up

    def test_renew_no_connection_returns_failure(self, adapter) -> None:
        a, _captured, responses = adapter
        _set(responses, _PROFILES, stdout="")
        _set(responses, ["nmcli", "-t", "-f", "DEVICE,STATE", "device"], stdout="eth0:disconnected\n")
        result = a.renew_lease("eth0")
        assert result.ok is False
        # Names the cause the operator can act on, not adapter state.
        assert "No saved profile" in result.message

    def test_renew_propagates_up_failure(self, adapter) -> None:
        a, _captured, responses = adapter
        _active(responses, "Wired:eth0")
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
        _active(responses, "Wired:eth0")
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
        _active(responses, "Wired:eth0")
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

    def test_con_verbs_pass_the_profile_uuid_with_the_uuid_keyword(self, adapter) -> None:
        """``con mod`` / ``down`` / ``up`` pass the UUID after the explicit
        ``uuid`` keyword, never the name: a leading-dash name never reaches the
        argv, and nmcli's ``con`` subcommands do not treat ``--`` as
        end-of-options (they read it as a literal name)."""
        a, _captured, responses = adapter
        _active(responses, "-weird:eth0")
        a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert [call.argv[2:5] for call in a._broker.calls] == [
            ["mod", "uuid", "uuid--weird"],
            ["down", "uuid", "uuid--weird"],
            ["up", "uuid", "uuid--weird"],
        ]

    def test_dhcp_with_manual_address_no_lease_falls_back_to_defaults(self, adapter) -> None:
        a, captured, responses = adapter
        _active(responses, "Wired:eth0")
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
        _active(responses, "Wired:eth0")
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
        _active(responses, "Wired:eth0")
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
        _active(responses, "Wired:eth0")
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


class TestSavedProfileForAnUnpluggedAdapter:
    """A profile that isn't active lists no DEVICE, so an adapter with its cable
    out has to be matched to its profile by the interface the profile names."""

    def _saved(self, responses, rows: str, records: str, *uuids: str) -> None:
        _set(responses, _PROFILES, stdout=rows)
        _set(responses, _bound_to(*uuids), stdout=records)

    def test_apply_edits_the_profile_saved_for_the_adapter(self, adapter) -> None:
        a, captured, responses = adapter
        self._saved(
            responses,
            "Wired connection 1:u-1:802-3-ethernet::yes:-999:1790000000\n"
            "Wired connection 2:u-2:802-3-ethernet::yes:-999:1790000500\n",
            "connection.uuid:u-1\nconnection.interface-name:eth0\n\n"
            "connection.uuid:u-2\nconnection.interface-name:eth1\n",
            "u-1",
            "u-2",
        )
        a.apply_ipv4("eth1", Ipv4Config(method=Ipv4Method.STATIC, address="192.0.2.50", prefix=24))
        modify = next(c for c in captured if c[:3] == ["nmcli", "connection", "modify"])
        assert modify[3:5] == ["uuid", "u-2"]

    @pytest.mark.parametrize(
        ("rows", "chosen"),
        [
            # Autoconnect off loses to on, however recent.
            ("Old:u-1:802-3-ethernet::yes:0:100\nNewer:u-2:802-3-ethernet::no:0:900\n", "u-1"),
            # Priority beats recency; the auto-created profile sits at -999.
            ("Wired connection 2:u-1:802-3-ethernet::yes:-999:900\nStage:u-2:802-3-ethernet::yes:0:100\n", "u-2"),
            # Equal otherwise: the one last used.
            ("Old:u-1:802-3-ethernet::yes:0:100\nNewer:u-2:802-3-ethernet::yes:0:900\n", "u-2"),
            # Never used: nmcli prints 0, and an unreadable field counts as 0.
            ("Never:u-1:802-3-ethernet::yes:0:\nUsed:u-2:802-3-ethernet::yes:0:5\n", "u-2"),
        ],
    )
    def test_several_profiles_for_one_adapter_rank_as_autoconnect_does(self, adapter, rows, chosen) -> None:
        a, _captured, responses = adapter
        self._saved(
            responses,
            rows,
            "connection.uuid:u-1\nconnection.interface-name:eth1\n\n"
            "connection.uuid:u-2\nconnection.interface-name:eth1\n",
            "u-1",
            "u-2",
        )
        assert _written(a, "eth1") == chosen

    def test_a_profile_naming_no_interface_is_not_claimed(self, adapter) -> None:
        """It can come up on any adapter, so editing it edits more than this one."""
        a, _captured, responses = adapter
        self._saved(
            responses,
            "Any wired:u-1:802-3-ethernet::yes:0:100\n",
            "connection.uuid:u-1\nconnection.interface-name:\n",
            "u-1",
        )
        _set(responses, ["nmcli", "-t", "-f", "DEVICE,STATE", "device"], stdout="eth1:unavailable\n")
        assert "No saved profile" in a.apply_ipv4("eth1", Ipv4Config(method=Ipv4Method.DHCP)).message
        assert a._broker.calls == []

    def test_a_colon_in_a_profile_name_survives(self, adapter) -> None:
        a, _captured, responses = adapter
        self._saved(
            responses,
            "garbage\nStage\\: left:u-1:802-3-ethernet::yes:0:100\n",
            "connection.uuid:u-1\nconnection.interface-name:eth1\n",
            "u-1",
        )
        assert _written(a, "eth1") == "u-1"

    @pytest.mark.parametrize("failing", ["list", "show"])
    def test_an_unreadable_profile_list_finds_nothing(self, adapter, failing) -> None:
        a, _captured, responses = adapter
        self._saved(responses, "Stage:u-1:802-3-ethernet::yes:0:100\n", "", "u-1")
        _set(responses, _PROFILES if failing == "list" else _bound_to("u-1"), returncode=8)
        assert _written(a, "eth1") is None

    def test_fallback_subprocess_failure_returns_none(self, adapter) -> None:
        a, _captured, _responses = adapter

        def fake_run(argv, *, check=True):
            raise RuntimeError("missing")

        a._run = fake_run
        assert _written(a, "eth0") is None

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

        # Prime the profile lookup; otherwise apply short-circuits before
        # reaching the broker path.
        def _run(argv, *, check=True):
            if argv == _PROFILES:
                return sp.CompletedProcess(argv, 0, "Wired:u-1:802-3-ethernet:eth0:yes:0:0\n", "")
            return sp.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(a, "_run", _run)
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is False
        assert "privileged helper is not configured" in result.message

    def test_renew_without_broker_returns_broker_message(self, monkeypatch) -> None:
        import subprocess as sp

        from openfollow.network.nm_adapter import NetworkManagerAdapter

        a = NetworkManagerAdapter()  # no broker

        def _run(argv, *, check=True):
            return sp.CompletedProcess(argv, 0, "Wired:u-1:802-3-ethernet:eth0:yes:0:0\n", "")

        monkeypatch.setattr(a, "_run", _run)
        result = a.renew_lease("eth0")
        assert result.ok is False
        # Names something the operator can act on, not an internal object.
        assert "privileged helper is not configured" in result.message

    def test_apply_privilege_error_surfaces(self, adapter) -> None:
        """A PrivilegeError on the modify step surfaces with the
        broker's message preserved – no unhandled traceback."""
        a, _captured, responses = adapter
        _active(responses, "Wired:eth0")
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
        _active(responses, "Wired:eth0")
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
        _active(responses, "Wired:eth0")
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
        _active(responses, f"{name}:{device}")

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


class TestActiveLoopFallthrough:
    def test_active_loop_no_match_then_fallback_returns_none(self, adapter) -> None:
        """A profile is active, but on another interface, and none is saved
        for the requested one – overall None."""
        a, _captured, responses = adapter
        _active(responses, "Wifi:wlan0")
        _set(responses, _bound_to("uuid-Wifi"), stdout="connection.uuid:uuid-Wifi\nconnection.interface-name:wlan0\n")
        assert _written(a, "eth0") is None


class TestConnectionLookupSurvivesAColonInTheName:
    """A colon in a profile name must not hide the profile.

    ``nmcli -t`` escapes it as ``\\:``, and cutting at the first colon puts the
    device column in the remainder where it can never match - so the profile is
    invisible to every lookup and ``apply_ipv4`` answers "No NetworkManager
    connection profile bound to eth0" for an interface that has one. That is
    the same failure the actionable-message work exists to remove, reached by a
    different route.
    """

    _OFFICE = "Wired connection\\: office:u-1:802-3-ethernet:eth0:yes:0:0\n"

    def test_active_pass_finds_a_colon_named_profile(self, adapter) -> None:
        a, _captured, responses = adapter
        _set(responses, _PROFILES, stdout=self._OFFICE)
        a._broker.exceptions = [make_failure("read-only")]
        assert "profile 'Wired connection: office'" in a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP)).message

    def test_apply_reaches_the_profile_instead_of_reporting_none(self, adapter) -> None:
        """The operator-visible half: the apply succeeds rather than claiming
        the interface has no profile."""
        a, captured, responses = adapter
        _set(responses, _PROFILES, stdout=self._OFFICE)
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is True, result.message
        modify = next(c for c in captured if c[:3] == ["nmcli", "connection", "modify"])
        assert modify[3:5] == ["uuid", "u-1"]

    def test_a_short_row_is_ignored(self, adapter) -> None:
        a, _captured, responses = adapter
        _set(responses, _PROFILES, stdout="truncated:eth0\n")
        assert _written(a, "eth0") is None


class TestVlans:
    @staticmethod
    def _prime(responses, rows: str, records: str = "", *uuids: str) -> None:
        """The profile list and, for the listed *uuids*, their one detail read."""
        _set(responses, _PROFILES, stdout=rows)
        if uuids:
            _set(responses, _bound_to(*uuids), stdout=records)

    _ETH0_AND_VLAN10 = (
        "Wired connection 1:uuid-eth0:802-3-ethernet:eth0:yes:0:0\nvlan10:uuid-v10:vlan:eth0.10:yes:0:0\n"
    )

    def test_backend_supports_vlans(self, adapter) -> None:
        a, _captured, _responses = adapter
        assert a.supports_vlans() is True

    @pytest.mark.parametrize("parent", ["eth0", "uuid-eth0"])
    def test_lists_vlans_with_parent_and_id(self, adapter, parent: str) -> None:
        """nmcli stores ``vlan.parent`` as either an interface name or the
        parent profile's UUID. A raw UUID in the parent column would render
        as gibberish in the interface list."""
        a, _captured, responses = adapter
        self._prime(
            responses,
            self._ETH0_AND_VLAN10,
            "connection.uuid:uuid-eth0\nconnection.interface-name:eth0\n\n"
            f"connection.uuid:uuid-v10\nconnection.interface-name:eth0.10\nvlan.parent:{parent}\nvlan.id:10\n",
            "uuid-eth0",
            "uuid-v10",
        )
        assert a.list_vlans() == [VlanInterface(name="eth0.10", parent="eth0", vlan_id=10)]

    @pytest.mark.parametrize(
        ("rows", "detail"),
        [
            ("vlan10:uuid-v10:vlan:eth0.10:yes:0:0\n", "vlan.parent:eth0\nvlan.id:not-a-number\n"),
            # Not active on a device.
            ("vlan10:uuid-v10:vlan::yes:0:0\n", "vlan.parent:eth0\nvlan.id:10\n"),
            ("vlan10:uuid-v10:vlan:eth0.10:yes:0:0\n", "vlan.parent:\nvlan.id:10\n"),
            # Its detail was not printed.
            ("vlan10:uuid-v10:vlan:eth0.10:yes:0:0\n", None),
        ],
    )
    def test_skips_a_vlan_it_cannot_place(self, adapter, rows: str, detail: str | None) -> None:
        a, _captured, responses = adapter
        records = "" if detail is None else f"connection.uuid:uuid-v10\n{detail}"
        self._prime(responses, rows, records, "uuid-v10")
        assert a.list_vlans() == []

    def test_a_colon_in_a_profile_name_does_not_shift_the_columns(self, adapter) -> None:
        """nmcli -t escapes a literal ':' in a value as '\\:'.

        Splitting on every colon shifts each field after a colon-bearing name,
        so an unrelated profile called "Wired connection: office" poisons the
        UUID->device map that resolves a VLAN's parent - and a VLAN whose own
        name contains one drops out of the list entirely, which makes it
        undeletable from the UI.
        """
        a, _captured, responses = adapter
        self._prime(
            responses,
            "Wired connection\\: office:uuid-eth0:802-3-ethernet:eth0:yes:0:0\n"
            "vlan\\: ten:uuid-v10:vlan:eth0.10:yes:0:0\n",
            "connection.uuid:uuid-v10\nvlan.parent:uuid-eth0\nvlan.id:10\n",
            "uuid-eth0",
            "uuid-v10",
        )
        assert a.list_vlans() == [VlanInterface(name="eth0.10", parent="eth0", vlan_id=10)]

    def test_a_colon_named_vlan_is_still_deletable(self, adapter) -> None:
        a, captured, responses = adapter
        self._prime(responses, "vlan\\: ten:uuid-v10:vlan:eth0.10:yes:0:0\n")
        result = a.delete_vlan("eth0.10")
        assert result.ok is True
        assert ["nmcli", "connection", "delete", "uuid", "uuid-v10"] in captured

    def test_ignores_non_vlan_profiles(self, adapter) -> None:
        a, _captured, responses = adapter
        self._prime(
            responses,
            "Wired connection 1:uuid-eth0:802-3-ethernet:eth0:yes:0:0\n",
            "connection.uuid:uuid-eth0\nvlan.parent:eth1\nvlan.id:10\n",
            "uuid-eth0",
        )
        assert a.list_vlans() == []

    def test_create_issues_the_add_argv(self, adapter) -> None:
        a, captured, _responses = adapter
        result = a.create_vlan("eth0", 10)
        assert result.ok is True
        assert [
            "nmcli",
            "connection",
            "add",
            "type",
            "vlan",
            "con-name",
            "eth0.10",
            "ifname",
            "eth0.10",
            "dev",
            "eth0",
            "id",
            "10",
        ] in captured

    def test_create_uses_the_add_capability(self, adapter) -> None:
        a, _captured, _responses = adapter
        a.create_vlan("eth0", 10)
        assert a._broker.calls[-1].capability is NETWORK_NM_CON_ADD

    def test_create_reports_a_broker_failure(self, adapter) -> None:
        a, _captured, _responses = adapter
        a._broker.exceptions.append(make_failure("parent device not found"))
        result = a.create_vlan("eth0", 10)
        assert result.ok is False
        assert "parent device not found" in result.message

    def test_delete_issues_the_delete_argv_for_the_bound_profile(self, adapter) -> None:
        a, captured, responses = adapter
        self._prime(responses, "vlan10:uuid-v10:vlan:eth0.10:yes:0:0\n")
        result = a.delete_vlan("eth0.10")
        assert result.ok is True
        assert result.message == "Removed VLAN interface eth0.10."
        assert ["nmcli", "connection", "delete", "uuid", "uuid-v10"] in captured

    def test_delete_targets_the_matching_profile_not_the_first(self, adapter) -> None:
        """With several VLANs on one parent, deleting the wrong profile tears
        down a different network than the operator asked for."""
        a, captured, responses = adapter
        self._prime(
            responses,
            "vlan10:uuid-v10:vlan:eth0.10:yes:0:0\nvlan20:uuid-v20:vlan:eth0.20:yes:0:0\n",
        )
        result = a.delete_vlan("eth0.20")
        assert result.ok is True
        assert ["nmcli", "connection", "delete", "uuid", "uuid-v20"] in captured
        assert ["nmcli", "connection", "delete", "uuid", "uuid-v10"] not in captured

    def test_delete_uses_the_delete_capability(self, adapter) -> None:
        a, _captured, responses = adapter
        self._prime(responses, "vlan10:uuid-v10:vlan:eth0.10:yes:0:0\n")
        a.delete_vlan("eth0.10")
        assert a._broker.calls[-1].capability is NETWORK_NM_CON_DELETE

    def test_delete_refuses_a_non_vlan_interface(self, adapter) -> None:
        """``con delete`` is a wildcarded grant, so this is the check that
        keeps it off a physical NIC's profile."""
        a, captured, responses = adapter
        self._prime(responses, "Wired connection 1:uuid-eth0:802-3-ethernet:eth0:yes:0:0\n")
        result = a.delete_vlan("eth0")
        assert result.ok is False
        assert "not a VLAN" in result.message
        assert not any("delete" in argv for argv in captured)

    def test_delete_reports_a_broker_failure(self, adapter) -> None:
        a, _captured, responses = adapter
        self._prime(responses, "vlan10:uuid-v10:vlan:eth0.10:yes:0:0\n")
        a._broker.exceptions.append(make_failure("profile is in use"))
        result = a.delete_vlan("eth0.10")
        assert result.ok is False
        assert "profile is in use" in result.message

    def test_a_failure_with_no_reason_still_names_what_was_not_removed(self, adapter) -> None:
        a, _captured, responses = adapter
        self._prime(responses, "vlan10:uuid-v10:vlan:eth0.10:yes:0:0\n")
        a._broker.exceptions.append(make_failure(""))
        assert a.delete_vlan("eth0.10").message == "Could not remove VLAN interface eth0.10."

    def test_profile_list_survives_an_nmcli_failure(self, adapter) -> None:
        a, _captured, _responses = adapter

        def _boom(argv, *, check=True):
            raise RuntimeError("nmcli exploded")

        a._run = _boom
        assert a.list_vlans() == []
        assert a.delete_vlan("eth0.10").ok is False

    def test_profile_list_skips_short_rows(self, adapter) -> None:
        a, _captured, responses = adapter
        self._prime(
            responses,
            "truncated:row\n\nvlan10:uuid-v10:vlan:eth0.10:yes:0:0\n",
            "connection.uuid:uuid-v10\nvlan.parent:eth0\nvlan.id:10\n",
            "uuid-v10",
        )
        assert a.list_vlans() == [VlanInterface(name="eth0.10", parent="eth0", vlan_id=10)]

    def test_a_profile_deleted_between_the_reads_lists_no_vlans(self, adapter) -> None:
        """nmcli stops at a profile that is gone, so what it printed before is not the whole list."""
        a, _captured, responses = adapter
        rows = "vlan10:uuid-v10:vlan:eth0.10:yes:0:0\nvlan20:uuid-v20:vlan:eth0.20:yes:0:0\n"
        self._prime(
            responses,
            rows,
            "connection.uuid:uuid-v10\nvlan.parent:eth0\nvlan.id:10\n",
            "uuid-v10",
            "uuid-v20",
        )
        responses[tuple(_bound_to("uuid-v10", "uuid-v20"))].returncode = 10
        assert a.list_vlans() == []


_DEVICES = ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"]
_DEVICE_STATES = ["nmcli", "-t", "-f", "DEVICE,STATE", "device"]
_FQDN = "of-1.stage.example.com"
# What nmcli 1.52 printed on the bench for a profile that names its own DHCP hostname.
_HOSTNAME_ALSO_SET = subprocess.CompletedProcess(
    ["sudo"],
    1,
    "",
    "Error: Failed to modify connection 'Wired connection 1': ipv4.dhcp-fqdn: "
    "property cannot be set when dhcp-hostname is also set",
)


class TestSetDhcpFqdn:
    """Every interface's profile carries the name as ``ipv4.dhcp-fqdn``, which NetworkManager
    sends as option 81 in place of option 12, and every interface with a link reconnects so
    its DHCP server sees it now."""

    @staticmethod
    def _station(adapter, *, devices: str, active: str):
        a, _captured, responses = adapter
        responses[tuple(_DEVICES)] = subprocess.CompletedProcess(_DEVICES, 0, stdout=devices, stderr="")
        _active(responses, *active.splitlines())
        return a, responses

    def _eth0_and_vlan(self, adapter):
        a, responses = self._station(
            adapter,
            devices="eth0:ethernet:connected\nlo:loopback:connected (externally)\n"
            "eth0.13:vlan:connected\nwlan0:wifi:disconnected\n",
            active="Wired connection 1:eth0\nlo:lo\neth0.13:eth0.13\n",
        )
        _carrier("eth0", "1")
        _carrier("eth0.13", "1")
        return a, responses

    @staticmethod
    def _argv(a) -> list[list[str]]:
        return [call.argv for call in a._broker.calls]

    def test_every_profile_carries_the_name_and_every_linked_interface_reconnects(self, adapter) -> None:
        """The loopback profile and an interface with no profile are left alone."""
        a, _ = self._eth0_and_vlan(adapter)
        result = a.set_dhcp_fqdn(_FQDN)
        assert (result.ok, result.partial_failures) == (True, ())
        assert self._argv(a) == [
            ["/usr/bin/nmcli", "con", "mod", "uuid", "uuid-Wired connection 1", "ipv4.dhcp-fqdn", _FQDN],
            ["/usr/bin/nmcli", "con", "mod", "uuid", "uuid-eth0.13", "ipv4.dhcp-fqdn", _FQDN],
            ["/usr/bin/nmcli", "con", "up", "uuid", "uuid-Wired connection 1"],
            ["/usr/bin/nmcli", "con", "up", "uuid", "uuid-eth0.13"],
        ]

    def test_a_blank_name_clears_it_so_the_hostname_goes_out_again(self, adapter) -> None:
        a, _ = self._eth0_and_vlan(adapter)
        assert a.set_dhcp_fqdn("").ok
        mods = [argv for argv in self._argv(a) if argv[2] == "mod"]
        assert [argv[-2:] for argv in mods] == [["ipv4.dhcp-fqdn", ""]] * 2

    def test_an_interface_without_a_link_keeps_the_name_for_when_it_connects(self, adapter) -> None:
        a, responses = self._station(adapter, devices="eth1:ethernet:unavailable\n", active="")
        responses[tuple(_PROFILES)] = subprocess.CompletedProcess(
            _PROFILES, 0, stdout="Wired connection 2:u2:802-3-ethernet::yes:0:1\n", stderr=""
        )
        responses[tuple(_bound_to("u2"))] = subprocess.CompletedProcess(
            [], 0, stdout="connection.uuid:u2\nconnection.interface-name:eth1\n", stderr=""
        )
        _carrier("eth1", "0")
        result = a.set_dhcp_fqdn(_FQDN)
        assert (result.ok, result.partial_failures) == (True, ())
        assert self._argv(a) == [["/usr/bin/nmcli", "con", "mod", "uuid", "u2", "ipv4.dhcp-fqdn", _FQDN]]

    def test_a_profile_naming_its_own_dhcp_hostname_is_reported_and_left_alone(self, adapter) -> None:
        """NetworkManager refuses the two together; the operator's hostname is not cleared to make room."""
        a, _ = self._eth0_and_vlan(adapter)
        a._broker.responses = [_HOSTNAME_ALSO_SET]
        result = a.set_dhcp_fqdn(_FQDN)
        assert result.ok
        assert len(result.partial_failures) == 1
        assert result.partial_failures[0].startswith("Profile 'Wired connection 1' was not updated (")
        assert "dhcp-hostname is also set" in result.partial_failures[0]
        ups = [argv[-1] for argv in self._argv(a) if argv[2] == "up"]
        assert ups == ["uuid-eth0.13"]

    def test_a_profile_still_waiting_for_a_lease_is_not_a_failure(self, adapter) -> None:
        """With no DHCP server answering, ``con up`` outlasts the broker's timeout though the
        profile is applied, as on a VLAN with nothing serving it."""
        a, responses = self._eth0_and_vlan(adapter)
        responses[tuple(_DEVICE_STATES)] = subprocess.CompletedProcess(
            _DEVICE_STATES, 0, stdout="eth0:connected\neth0.13:connecting (getting IP configuration)\n", stderr=""
        )
        a._broker.responses = [_OK, _OK, _OK, _NO_SUITABLE_DEVICE]
        result = a.set_dhcp_fqdn(_FQDN)
        assert (result.ok, result.partial_failures) == (True, ())

    def test_an_interface_that_fails_to_reconnect_is_reported(self, adapter) -> None:
        a, responses = self._eth0_and_vlan(adapter)
        responses[tuple(_DEVICE_STATES)] = subprocess.CompletedProcess(
            _DEVICE_STATES, 0, stdout="eth0:disconnected\neth0.13:connected\n", stderr=""
        )
        a._broker.responses = [_OK, _OK, _NO_SUITABLE_DEVICE]
        result = a.set_dhcp_fqdn(_FQDN)
        assert result.ok
        assert len(result.partial_failures) == 1
        assert result.partial_failures[0].startswith("eth0 could not be reconnected (")

    def test_a_station_with_no_profile_says_so(self, adapter) -> None:
        a, _ = self._station(adapter, devices="lo:loopback:connected (externally)\n", active="lo:lo\n")
        result = a.set_dhcp_fqdn(_FQDN)
        assert (result.ok, result.message) == (False, "No interface has a NetworkManager profile to carry the name.")
        assert self._argv(a) == []

    def test_no_profile_updated_is_a_failure(self, adapter) -> None:
        a, _ = self._eth0_and_vlan(adapter)
        a._broker.responses = [_HOSTNAME_ALSO_SET, _HOSTNAME_ALSO_SET]
        result = a.set_dhcp_fqdn(_FQDN)
        assert (result.ok, result.message) == (False, "No profile could be updated.")
        assert len(result.partial_failures) == 2
        assert not any(argv[2] == "up" for argv in self._argv(a))


class TestSetDhcpFqdnSafeguards:
    """The DHCP name runs in the background and touches only what the station manages."""

    _FQDN = "of-1.stage.example.com"

    @staticmethod
    def _station(adapter, *, devices: str, active: str, states: str = ""):
        a, _captured, responses = adapter
        responses[tuple(_DEVICES)] = subprocess.CompletedProcess(_DEVICES, 0, stdout=devices, stderr="")
        _active(responses, *active.splitlines())
        responses[tuple(_DEVICE_STATES)] = subprocess.CompletedProcess(_DEVICE_STATES, 0, stdout=states, stderr="")
        return a, responses

    def test_it_never_asks_for_a_password(self, adapter) -> None:
        """Nobody is at a prompt: the worker runs after the save was answered, holding the network lock."""
        a, _ = self._station(adapter, devices="eth0:ethernet:connected\n", active="Wired connection 1:eth0\n")
        _carrier("eth0", "1")
        a.set_dhcp_fqdn(self._FQDN)
        assert [call.allow_prompt for call in a._broker.calls] == [False, False]

    def test_bridges_tunnels_and_external_connections_are_left_alone(self, adapter) -> None:
        """docker0 or a VPN belong to whatever made them; NM would take a device over on con up."""
        a, _ = self._station(
            adapter,
            devices="eth0:ethernet:connected\ndocker0:bridge:connected (externally)\n"
            "tailscale0:tun:connected (externally)\neth1:ethernet:connected (externally)\n",
            active="Wired connection 1:eth0\ndocker0:docker0\ntailscale0:tailscale0\neth1:eth1\n",
            states="eth0:connected\ndocker0:connected (externally)\ntailscale0:connected (externally)\n"
            "eth1:connected (externally)\n",
        )
        _carrier("eth0", "1")
        a.set_dhcp_fqdn(self._FQDN)
        assert {call.argv[4] for call in a._broker.calls} == {"uuid-Wired connection 1"}

    def _two_profiles(self, adapter, *, current: dict[str, str]):
        a, responses = self._station(
            adapter,
            devices="eth0:ethernet:connected\neth1:ethernet:connected\n",
            active="Wired connection 1:eth0\nWired connection 2:eth1\n",
        )
        for name, value in current.items():
            argv = ["nmcli", "-g", "ipv4.dhcp-fqdn", "connection", "show", "uuid", f"uuid-{name}"]
            responses[tuple(argv)] = subprocess.CompletedProcess(argv, 0, stdout=value + "\n", stderr="")
        _carrier("eth0", "1")
        _carrier("eth1", "1")
        return a

    @pytest.mark.parametrize("failing", ["once", "always"])
    def test_a_device_whose_state_cannot_be_read_is_left_alone_and_named(self, adapter, failing: str) -> None:
        """Unknown could be external, which is not the station's to change."""
        a = self._two_profiles(adapter, current={})
        reads = a._run
        failed: list[list[str]] = []

        def _run(argv, *, check=True):
            if list(argv) == _DEVICE_STATES and (failing == "always" or not failed):
                failed.append(list(argv))
                raise RuntimeError("nmcli timed out")
            return reads(argv, check=check)

        a._run = _run  # type: ignore[method-assign]
        result = a.set_dhcp_fqdn(self._FQDN)
        unread = "NetworkManager did not report its state, so it was left alone."
        if failing == "once":
            assert {call.argv[4] for call in a._broker.calls} == {"uuid-Wired connection 2"}
            assert (result.ok, result.partial_failures) == (True, (f"eth0: {unread}",))
        else:
            assert a._broker.calls == []
            assert (result.ok, result.partial_failures) == (False, (f"eth0: {unread}", f"eth1: {unread}"))

    def test_reconciling_writes_only_what_differs_and_reconnects_nothing(self, adapter) -> None:
        a = self._two_profiles(
            adapter, current={"Wired connection 1": self._FQDN, "Wired connection 2": "old.example.com"}
        )
        result = a.set_dhcp_fqdn(self._FQDN, reconnect=False)
        assert (result.ok, result.message) == (True, "Applied.")
        assert [call.argv for call in a._broker.calls] == [
            ["/usr/bin/nmcli", "con", "mod", "uuid", "uuid-Wired connection 2", "ipv4.dhcp-fqdn", self._FQDN]
        ]

    def test_reconciling_a_station_already_in_line_touches_nothing(self, adapter) -> None:
        a = self._two_profiles(adapter, current={"Wired connection 1": "", "Wired connection 2": ""})
        result = a.set_dhcp_fqdn("", reconnect=False)
        assert (result.ok, result.message) == (True, "Unchanged.")
        assert a._broker.calls == []

    def test_an_unreadable_profile_is_written_rather_than_trusted(self, adapter) -> None:
        a = self._two_profiles(adapter, current={})
        reads = a._run

        def _run(argv, *, check=True):
            if argv[:3] == ["nmcli", "-g", "ipv4.dhcp-fqdn"]:
                raise RuntimeError("nmcli failed")
            return reads(argv, check=check)

        a._run = _run  # type: ignore[method-assign]
        a.set_dhcp_fqdn(self._FQDN, reconnect=False)
        assert [call.argv[4] for call in a._broker.calls] == ["uuid-Wired connection 1", "uuid-Wired connection 2"]


@dataclass(frozen=True)
class _Device:
    """One device as nmcli 1.52 reports it on a station."""

    name: str
    address: str = ""  # ``a.b.c.d/nn``
    profile: str = ""  # the active profile, empty when none is
    method: str = "auto"
    addresses: str = ""
    state: str = "100 (connected)"
    kind: str = "ethernet"
    gateway: str = ""
    dns: tuple[str, ...] = ()
    hwaddr: str = ""
    dhcp: tuple[str, ...] = ()  # DHCP4.OPTION values, ``name = value``
    vlan: tuple[str, str] | None = None  # its profile's (vlan.parent, vlan.id)
    fqdn: str = ""  # its profile's ipv4.dhcp-fqdn
    profile_uuid: str = ""  # set where another profile shares the name

    @property
    def uuid(self) -> str:
        return (self.profile_uuid or f"uuid-{self.profile}") if self.profile else ""


@dataclass(frozen=True)
class _Saved:
    """A profile no device has active."""

    name: str
    interface: str = ""  # connection.interface-name
    method: str = "auto"
    addresses: str = ""
    rank: str = "yes:0:0"  # AUTOCONNECT:AUTOCONNECT-PRIORITY:TIMESTAMP
    fqdn: str = ""
    profile_uuid: str = ""
    kind: str = "802-3-ethernet"
    vlan: tuple[str, str] | None = None

    @property
    def uuid(self) -> str:
        return self.profile_uuid or f"uuid-{self.name}"


class _Station:
    """A fake ``nmcli`` answering the panel's reads and the diagnostics read from one set of devices.

    ``fail`` maps an argv predicate to the stderr a failing call prints; the call
    then raises as ``_run`` does for a non-zero exit. A profile in ``gone`` is
    listed but deleted before its detail is read, which nmcli 1.52 answers as it
    does on a station: the profiles before it printed, none after, exit status 10.
    """

    def __init__(
        self,
        *devices: _Device,
        saved: tuple[_Saved, ...] = (),
        gone: tuple[str, ...] = (),
        fail: dict | None = None,
    ) -> None:
        self.devices = devices
        self.saved = saved
        self.gone = gone
        self.fail = fail or {}
        self.calls: list[list[str]] = []

    def __call__(self, argv, *, check=True) -> subprocess.CompletedProcess:
        argv = list(argv)
        self.calls.append(argv)
        for matches, stderr in self.fail.items():
            if matches(argv) and check:
                raise RuntimeError(f"{' '.join(argv)} failed (rc=8): {stderr}")
            if matches(argv):
                return subprocess.CompletedProcess(argv, 8, stdout="", stderr=stderr)
        missing = argv[3] == nm_adapter._PROFILE_FIELDS and any(
            argv[i + 1] in self.gone for i in range(6, len(argv), 2)
        )
        if missing and check:
            raise RuntimeError(f"{' '.join(argv)} failed (rc=10): no such connection profile.")
        return subprocess.CompletedProcess(argv, 10 if missing else 0, stdout=self._answer(argv), stderr="")

    def _profiles(self) -> list[dict[str, str]]:
        """Every profile, active ones first, as the list and the detail read describe it."""
        out = []
        for d in self.devices:
            if d.profile:
                kind = {"ethernet": "802-3-ethernet"}.get(d.kind, d.kind)
                out.append(
                    {"name": d.profile, "uuid": d.uuid, "kind": kind, "device": d.name, "rank": "yes:0:0"}
                    | {"interface": d.name, "method": d.method, "addresses": d.addresses, "vlan": d.vlan}
                    | {"fqdn": d.fqdn}
                )
        for p in self.saved:
            out.append(
                {"name": p.name, "uuid": p.uuid, "kind": p.kind, "device": "", "rank": p.rank}
                | {"interface": p.interface, "method": p.method, "addresses": p.addresses, "vlan": p.vlan}
                | {"fqdn": p.fqdn}
            )
        return out

    def _answer(self, argv: list[str]) -> str:
        if argv[1:3] == ["-g", "ipv4.dhcp-fqdn"]:
            by_uuid = {p["uuid"]: p for p in self._profiles()}
            return f"{by_uuid[argv[-1]]['fqdn']}\n" if argv[-2] == "uuid" and argv[-1] in by_uuid else ""
        fields, verb = argv[3], argv[4:]
        if fields == "DEVICE,TYPE,STATE":
            return "".join(f"{d.name}:{d.kind}:{d.state.split(' ', 1)[1][1:-1]}\n" for d in self.devices)
        if fields == "DEVICE,STATE":
            return "".join(f"{d.name}:{d.state.split(' ', 1)[1][1:-1]}\n" for d in self.devices)
        if fields == nm_adapter._SOURCE_FIELDS:
            return "\n".join(
                f"GENERAL.DEVICE:{d.name}\nGENERAL.TYPE:{d.kind}\nGENERAL.STATE:{d.state}\n"
                f"GENERAL.CON-UUID:{d.uuid}\n" + (f"IP4.ADDRESS[1]:{d.address}\n" if d.address else "")
                for d in self.devices
            )
        if fields == nm_adapter._STATE_FIELDS:
            return "\n".join(
                f"GENERAL.DEVICE:{d.name}\nGENERAL.TYPE:{d.kind}\nGENERAL.STATE:{d.state}\n"
                f"GENERAL.CON-UUID:{d.uuid}\n"
                + (f"IP4.ADDRESS[1]:{d.address}\n" if d.address else "")
                + f"GENERAL.HWADDR:{d.hwaddr}\nIP4.GATEWAY:{d.gateway}\n"
                + "".join(f"IP4.DNS[{i}]:{v}\n" for i, v in enumerate(d.dns, 1))
                + "".join(f"DHCP4.OPTION[{i}]:{v}\n" for i, v in enumerate(d.dhcp, 1))
                for d in self.devices
            )
        if fields == nm_adapter._PROFILE_LIST_FIELDS:
            return "".join(
                f"{p['name'].replace(':', chr(92) + ':')}:{p['uuid']}:{p['kind']}:{p['device']}:{p['rank']}\n"
                for p in self._profiles()
            )
        if fields == nm_adapter._PROFILE_FIELDS:
            by_uuid = {p["uuid"]: p for p in self._profiles()}
            records = []
            for uuid in verb[3::2]:
                p = by_uuid.get(uuid)
                if p is None or uuid in self.gone:
                    break
                record = (
                    f"connection.uuid:{uuid}\nconnection.interface-name:{p['interface']}\n"
                    f"ipv4.method:{p['method']}\nipv4.addresses:{p['addresses']}\n"
                )
                if p["vlan"]:
                    record += f"vlan.parent:{p['vlan'][0]}\nvlan.id:{p['vlan'][1]}\n"
                records.append(record)
            return "\n".join(records)
        if fields == "ipv4.method,ipv4.addresses":
            key = verb[-1]
            device = next((d for d in self.devices if key in (d.profile, d.uuid)), None)
            return f"ipv4.method:{device.method}\nipv4.addresses:{device.addresses}\n" if device else ""
        return ""  # saved profiles, DHCP options


def _method_read(argv: list[str]) -> bool:
    return argv[3] == "ipv4.method,ipv4.addresses"


def _device_show(argv: list[str]) -> bool:
    return argv[4:6] == ["device", "show"]


def _profile_list(argv: list[str]) -> bool:
    return argv[3] == nm_adapter._PROFILE_LIST_FIELDS


def _profile_detail(argv: list[str]) -> bool:
    return argv[3] == nm_adapter._PROFILE_FIELDS


_NOT_RUNNING = "Error: NetworkManager is not running."


@pytest.fixture
def station(monkeypatch, tmp_path):
    """An adapter wired to a :class:`_Station`; call it with the station's devices."""
    monkeypatch.setattr(nm_adapter, "_SYS_CLASS_NET", tmp_path)

    def _make(*devices: _Device, **station) -> tuple[NetworkManagerAdapter, _Station]:
        nmcli = _Station(*devices, **station)
        a = NetworkManagerAdapter(broker=FakeBroker())
        monkeypatch.setattr(a, "_run", nmcli)
        return a, nmcli

    return _make


class TestReadAddressSources:
    """The diagnostics read: where each address came from, or why it cannot say."""

    def test_reads_each_address_from_its_active_profile(self, station) -> None:
        a, nmcli = station(
            _Device("eth0", "192.0.2.10/24", "Wired connection 1"),
            _Device("eth1", "198.51.100.5/24", "Stage", method="manual", addresses="198.51.100.5/24"),
            _Device("eth2", "203.0.113.9/24", "Desk", method="manual", addresses="dhcp-managed/24"),
            _Device("eth0.13", "169.254.32.55/16", "eth0.13", kind="vlan"),
            _Device("eth3", state="20 (unavailable)"),
            _Device("lo", "127.0.0.1/8", "lo", method="manual", state="100 (connected (externally))", kind="loopback"),
        )
        assert a.read_address_sources() == [
            AddressSourceReading("eth0", "192.0.2.10", "dhcp"),
            AddressSourceReading("eth1", "198.51.100.5", "static"),
            AddressSourceReading("eth2", "203.0.113.9", "static"),
            AddressSourceReading("eth0.13", "169.254.32.55", "link-local"),
            AddressSourceReading("eth3", "", "none"),
        ]
        # A link-local address says what it is by itself; its profile is not read.
        assert [c[-1] for c in nmcli.calls if _method_read(c)] == ["uuid-Wired connection 1", "uuid-Stage", "uuid-Desk"]

    def test_a_backend_that_cannot_be_read_raises_where_the_panel_lists_nothing(self, station) -> None:
        a, _ = station(_Device("eth0", "192.0.2.10/24", "Wired"), fail={lambda argv: True: _NOT_RUNNING})
        with pytest.raises(BackendReadError, match="NetworkManager is not running"):
            a.read_address_sources()
        assert a.list_interfaces() == []

    def test_a_failing_device_read_raises_where_the_panel_drops_the_row(self, station) -> None:
        a, _ = station(_Device("eth0", "192.0.2.10/24", "Wired"), fail={_device_show: "Error: timeout."})
        with pytest.raises(BackendReadError, match="device show failed .*timeout"):
            a.read_address_sources()
        assert [i.name for i in a.list_interfaces()] == ["eth0"]
        assert a.get_state("eth0") is None

    def test_a_failing_profile_read_is_unreadable_where_the_panel_says_dhcp(self, station) -> None:
        a, _ = station(
            _Device("eth0", "198.51.100.5/24", "Stage", method="manual", addresses="198.51.100.5/24"),
            fail={
                _method_read: "Error: uuid-Stage - no such connection profile.",
                _profile_detail: "Error: uuid-Stage - no such connection profile.",
            },
        )
        (reading,) = a.read_address_sources()
        assert (reading.name, reading.address, reading.source) == ("eth0", "198.51.100.5", "unreadable")
        assert "uuid uuid-Stage failed (rc=8): Error: uuid-Stage - no such connection profile." in reading.reason
        state = a.get_state("eth0")
        assert state is not None
        assert state.address_source == "dhcp"

    def test_an_address_without_an_active_profile_is_unknown_where_the_panel_says_dhcp(self, station) -> None:
        a, _ = station(_Device("eth0", "198.51.100.5/24"))
        assert a.read_address_sources() == [
            AddressSourceReading("eth0", "198.51.100.5", "unknown", "no active NetworkManager profile")
        ]
        state = a.get_state("eth0")
        assert state is not None
        assert state.address_source == "dhcp"

    @pytest.mark.parametrize("address", ["192.0.2.10/24", ""])
    def test_an_unmanaged_device_is_unknown_whatever_it_lists(self, station, address: str) -> None:
        """NetworkManager may list no address on a device it does not manage; the kernel can still hold one."""
        a, _ = station(_Device("eth1", address, state="10 (unmanaged)"))
        (reading,) = a.read_address_sources()
        assert (reading.source, reading.reason) == ("unknown", "not managed by NetworkManager")
        assert reading.address == address.partition("/")[0]

    def test_an_externally_configured_device_is_not_read_from_its_generated_profile(self, station) -> None:
        a, nmcli = station(
            _Device("eth1", "192.0.2.10/24", "eth1", method="manual", state="100 (connected (externally))")
        )
        assert a.read_address_sources() == [
            AddressSourceReading("eth1", "192.0.2.10", "unknown", "configured outside NetworkManager")
        ]
        assert not any(_method_read(c) for c in nmcli.calls)

    def test_a_profile_method_that_is_neither_auto_nor_manual_is_unknown(self, station) -> None:
        a, _ = station(_Device("eth0", "10.42.0.1/24", "Hotspot", method="shared"))
        assert a.read_address_sources() == [
            AddressSourceReading("eth0", "10.42.0.1", "unknown", "profile ipv4.method is shared")
        ]


class TestReadNetwork:
    """What the network card shows of every interface, read in the same three
    ``nmcli`` runs however many interfaces the station has."""

    _FROZEN_NOW = 1_700_000_000

    @pytest.mark.parametrize("count", [1, 6])
    def test_three_runs_read_every_interface(self, station, count: int) -> None:
        devices = [_Device(f"eth{i}", f"192.0.2.{i + 10}/24", f"Wired {i}") for i in range(count)]
        a, nmcli = station(*devices)
        reading = a.read_network()
        assert [i.name for i in reading.interfaces] == [d.name for d in devices]
        assert {name: s.ipv4.address for name, s in reading.states.items()} == {
            d.name: d.address.partition("/")[0] for d in devices
        }
        assert len(nmcli.calls) == 3

    def test_reads_address_gateway_dns_mac_and_lease(self, station, monkeypatch) -> None:
        monkeypatch.setattr(nm_adapter.time, "time", lambda: self._FROZEN_NOW)
        a, _ = station(
            _Device(
                "eth0",
                "192.0.2.50/24",
                "Wired",
                gateway="192.0.2.1",
                dns=("198.51.100.53", "198.51.100.54", "198.51.100.55", "198.51.100.56"),
                # nmcli -t may escape the colons of a MAC.
                hwaddr="AA\\:BB\\:CC\\:DD\\:EE\\:FF",
                dhcp=(
                    "ip_address = 192.0.2.50",
                    "subnet_mask = 255.255.255.0",
                    "routers = 192.0.2.1",
                    "domain_name_servers = 198.51.100.53 198.51.100.54",
                    f"expiry = {self._FROZEN_NOW + 3600}",
                ),
            )
        )
        assert a.get_state("eth0") == NetworkState(
            interface=NetworkInterface("eth0", "AA:BB:CC:DD:EE:FF", "ethernet", is_up=True),
            ipv4=Ipv4Config(
                Ipv4Method.DHCP, "192.0.2.50", 24, "192.0.2.1", ("198.51.100.53", "198.51.100.54", "198.51.100.55")
            ),
            lease=LeaseInfo("192.0.2.50", 24, "192.0.2.1", ("198.51.100.53", "198.51.100.54"), 3600),
        )

    def test_an_expired_lease_reads_zero_seconds_left(self, station, monkeypatch) -> None:
        monkeypatch.setattr(nm_adapter.time, "time", lambda: self._FROZEN_NOW)
        a, _ = station(
            _Device(
                "eth0", "192.0.2.50/24", "Wired", dhcp=("ip_address = 192.0.2.50", f"expiry = {self._FROZEN_NOW - 100}")
            )
        )
        state = a.get_state("eth0")
        assert state is not None and state.lease is not None
        assert state.lease.lease_seconds_remaining == 0

    @pytest.mark.parametrize(
        ("address", "expected"),
        [("192.0.2.5", ("192.0.2.5", None)), ("192.0.2.5/notanumber", ("192.0.2.5", None)), ("", (None, None))],
    )
    def test_an_address_without_a_usable_prefix(self, station, address: str, expected) -> None:
        a, _ = station(_Device("eth0", address, "Wired"))
        state = a.get_state("eth0")
        assert state is not None
        assert (state.ipv4.address, state.ipv4.prefix) == expected
        assert (state.ipv4.router, state.ipv4.dns, state.lease, state.interface.mac) == (None, (), None, None)

    @pytest.mark.parametrize(
        ("method", "addresses", "expected"),
        [
            ("manual", "dhcp-managed/24", Ipv4Method.DHCP_WITH_MANUAL_ADDRESS),
            ("manual", "192.0.2.50/24", Ipv4Method.STATIC),
            ("auto", "", Ipv4Method.DHCP),
            ("shared", "", Ipv4Method.DHCP),
        ],
    )
    def test_the_method_is_the_active_profiles(self, station, method: str, addresses: str, expected) -> None:
        a, _ = station(
            _Device("eth0", "192.0.2.50/24", "Wired"),
            _Device("eth1", "198.51.100.5/24", "Stage", method=method, addresses=addresses),
        )
        reading = a.read_network()
        assert {name: s.ipv4.method for name, s in reading.states.items()} == {
            "eth0": Ipv4Method.DHCP,
            "eth1": expected,
        }

    def test_an_unplugged_adapter_reads_the_profile_saved_for_it(self, station) -> None:
        """Ranked as autoconnect ranks them; a profile naming no interface belongs to
        none, and an adapter with an active profile reads that one."""
        a, _ = station(
            _Device("eth0", "192.0.2.10/24", "Wired"),
            _Device("eth1", state="20 (unavailable)"),
            _Device("eth2", state="30 (disconnected)"),
            saved=(
                _Saved("Spare", "eth0", method="manual", addresses="192.0.2.99/24", rank="yes:100:999"),
                _Saved("Old", "eth1", rank="yes:0:100"),
                _Saved("Newer", "eth1", method="manual", addresses="198.51.100.5/24", rank="yes:0:900"),
                _Saved("Any wired", "", method="manual", addresses="203.0.113.5/24"),
            ),
        )
        reading = a.read_network()
        assert {name: s.ipv4.method for name, s in reading.states.items()} == {
            "eth0": Ipv4Method.DHCP,
            "eth1": Ipv4Method.STATIC,
            "eth2": Ipv4Method.DHCP,
        }

    @pytest.mark.parametrize(
        ("state", "is_up"),
        [
            ("100 (connected)", True),
            ("100 (connected (externally))", True),
            ("70 (connecting (getting IP configuration))", False),
            ("30 (disconnected)", False),
            ("20 (unavailable)", False),
            ("10 (unmanaged)", False),
        ],
    )
    def test_up_is_connected(self, station, state: str, is_up: bool) -> None:
        a, _ = station(_Device("eth0", state=state))
        assert [i.is_up for i in a.read_network().interfaces] == [is_up]

    def test_loopback_is_not_an_interface_of_the_card(self, station) -> None:
        a, _ = station(
            _Device("lo", "127.0.0.1/8", "lo", method="manual", state="100 (connected (externally))", kind="loopback"),
            _Device("lo0", "127.0.0.1/8"),
            _Device("eth0", "192.0.2.10/24", "Wired"),
        )
        reading = a.read_network()
        assert [i.name for i in reading.interfaces] == ["eth0"]
        assert set(reading.states) == {"eth0"}
        assert a.get_state("lo") is None

    def test_an_unknown_interface_has_no_state(self, station) -> None:
        a, _ = station(_Device("eth0", "192.0.2.10/24", "Wired"))
        assert a.get_state("nope0") is None

    def test_the_vlans_come_from_the_same_reads(self, station) -> None:
        a, nmcli = station(
            _Device("eth0", "192.0.2.10/24", "Wired"),
            _Device("eth0.13", "169.254.32.55/16", "eth0.13", kind="vlan", vlan=("eth0", "13")),
            _Device("eth0.14", "169.254.32.56/16", "eth0.14", kind="vlan", vlan=("uuid-Wired", "14")),
        )
        reading = a.read_network()
        assert reading.vlans == (
            VlanInterface("eth0.13", "eth0", 13),
            VlanInterface("eth0.14", "eth0", 14),
        )
        assert len(nmcli.calls) == 3

    def test_a_vlan_on_a_parent_whose_profile_is_not_the_active_one(self, station) -> None:
        """Its ``vlan.parent`` is that profile's UUID, which names the interface it belongs to."""
        a, _ = station(
            _Device("eth0", "192.0.2.10/24", "Wired"),
            _Device("eth0.13", "169.254.32.55/16", "eth0.13", kind="vlan", vlan=("uuid-Stage", "13")),
            saved=(_Saved("Stage", "eth0"),),
        )
        assert a.read_network().vlans == (VlanInterface("eth0.13", "eth0", 13),)

    def test_an_unreadable_device_list_reads_nothing(self, station, caplog) -> None:
        a, _ = station(_Device("eth0", "192.0.2.10/24", "Wired"), fail={_device_show: _NOT_RUNNING})
        assert a.read_network() == NetworkReading()
        assert "nmcli device read failed" in caplog.text

    def test_a_profile_list_nmcli_cannot_decode_leaves_the_addresses(self, station, caplog) -> None:
        a, nmcli = station(_Device("eth0", "198.51.100.5/24", "Stage", method="manual", addresses="198.51.100.5/24"))

        def _run(argv, *, check=True):
            if _profile_list(list(argv)):
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
            return nmcli(argv, check=check)

        a._run = _run
        state = a.get_state("eth0")
        assert state is not None
        assert (state.ipv4.address, state.ipv4.method) == ("198.51.100.5", Ipv4Method.DHCP)
        assert "nmcli profile read failed" in caplog.text

    @pytest.mark.parametrize("failing", [_profile_list, _profile_detail])
    def test_unreadable_profiles_leave_the_devices_on_dhcp(self, station, caplog, failing) -> None:
        a, _ = station(
            _Device("eth0", "198.51.100.5/24", "Stage", method="manual", addresses="198.51.100.5/24"),
            _Device("eth0.13", "169.254.32.55/16", "eth0.13", kind="vlan", vlan=("eth0", "13")),
            fail={failing: _NOT_RUNNING},
        )
        reading = a.read_network()
        assert {name: s.ipv4.address for name, s in reading.states.items()} == {
            "eth0": "198.51.100.5",
            "eth0.13": "169.254.32.55",
        }
        assert {s.ipv4.method for s in reading.states.values()} == {Ipv4Method.DHCP}
        assert reading.vlans == ()
        assert "nmcli profile read failed" in caplog.text

    def test_a_profile_deleted_between_the_reads_costs_the_methods_not_the_addresses(self, station, caplog) -> None:
        a, _ = station(
            _Device("eth0", "198.51.100.5/24", "Stage", method="manual", addresses="198.51.100.5/24"),
            _Device("eth0.13", "169.254.32.55/16", "eth0.13", kind="vlan", vlan=("eth0", "13")),
            gone=("uuid-eth0.13",),
        )
        reading = a.read_network()
        assert {name: s.ipv4.address for name, s in reading.states.items()} == {
            "eth0": "198.51.100.5",
            "eth0.13": "169.254.32.55",
        }
        assert {s.ipv4.method for s in reading.states.values()} == {Ipv4Method.DHCP}
        assert reading.vlans == ()
        assert "nmcli profile read failed" in caplog.text

    def test_a_profile_deleted_between_the_reads_never_picks_a_lower_ranked_one(self, station) -> None:
        """nmcli prints only the profiles before the deleted one; ranking those alone would
        edit a profile NetworkManager never brings up on the adapter."""
        a, nmcli = station(
            _Device("eth1", state="20 (unavailable)"),
            saved=(
                _Saved("Low", "eth1", rank="yes:0:100"),
                _Saved("Deleted", "eth2"),
                _Saved("High", "eth1", rank="yes:100:100"),
            ),
            gone=("uuid-Deleted",),
        )
        result = a.apply_ipv4("eth1", Ipv4Config(method=Ipv4Method.STATIC, address="192.0.2.50", prefix=24))
        assert result.ok is False
        assert "No saved profile for eth1" in result.message
        assert a._broker.calls == []

    def test_a_device_list_nmcli_cannot_decode_reads_nothing(self, station, caplog) -> None:
        a, nmcli = station(_Device("eth0", "198.51.100.5/24", "Stage"))

        def _run(argv, *, check=True):
            if _device_show(list(argv)):
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
            return nmcli(argv, check=check)

        a._run = _run
        assert a.read_network() == NetworkReading()
        assert "nmcli device read failed" in caplog.text


class TestProfilesSharingAName:
    """NetworkManager lets profiles share a name. Given one, nmcli edits the first namesake it
    finds, prints every namesake's value and deletes them all, so every write and every
    per-profile read names the profile by its UUID."""

    _FQDN = "of-1.stage.example.com"

    @staticmethod
    def _targets(a) -> list[list[str]]:
        """Verb and profile of each privileged ``nmcli con`` call."""
        return [call.argv[2:5] for call in a._broker.calls]

    @staticmethod
    def _active_and_namesake(station, **namesake):
        """``Wired connection 1`` active on eth0 and a saved profile of the same name, ranked
        above it by autoconnect, so neither listing order nor ranking finds the active one."""
        saved = _Saved("Wired connection 1", "eth0", rank="yes:100:999", profile_uuid="uuid-namesake", **namesake)
        return station(
            _Device("eth0", "192.0.2.10/24", "Wired connection 1", profile_uuid="uuid-active"), saved=(saved,)
        )

    def test_apply_writes_and_reconnects_the_active_profile(self, station) -> None:
        a, _ = self._active_and_namesake(station, method="manual", addresses="198.51.100.7/24")
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert (result.ok, result.message) == (True, "Applied.")
        assert self._targets(a) == [
            ["mod", "uuid", "uuid-active"],
            ["down", "uuid", "uuid-active"],
            ["up", "uuid", "uuid-active"],
        ]

    def test_apply_to_an_unplugged_adapter_writes_the_profile_saved_for_it(self, station) -> None:
        a, _ = station(
            _Device("eth1", state="20 (unavailable)"),
            saved=(
                _Saved("Wired connection 2", "eth2", rank="yes:100:999", profile_uuid="uuid-eth2"),
                _Saved("Wired connection 2", "eth1", profile_uuid="uuid-eth1"),
            ),
        )
        _carrier("eth1", "0")
        result = a.apply_ipv4("eth1", Ipv4Config(method=Ipv4Method.STATIC, address="192.0.2.50", prefix=24))
        assert (result.ok, result.pending) == (True, True)
        assert self._targets(a) == [["mod", "uuid", "uuid-eth1"]]

    def test_a_failed_save_names_the_profile_by_its_name(self, station) -> None:
        a, _ = self._active_and_namesake(station)
        a._broker.exceptions = [make_failure("read-only")]
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.message == (
            "Could not save the settings to profile 'Wired connection 1'; nothing was changed (read-only)."
        )

    def test_renew_reconnects_the_active_profile(self, station) -> None:
        a, _ = self._active_and_namesake(station)
        assert a.renew_lease("eth0").ok
        assert self._targets(a) == [["down", "uuid", "uuid-active"], ["up", "uuid", "uuid-active"]]

    def test_the_dhcp_name_goes_to_the_active_profile(self, station) -> None:
        a, _ = self._active_and_namesake(station)
        result = a.set_dhcp_fqdn(self._FQDN)
        assert (result.ok, result.partial_failures) == (True, ())
        assert [call.argv for call in a._broker.calls] == [
            ["/usr/bin/nmcli", "con", "mod", "uuid", "uuid-active", "ipv4.dhcp-fqdn", self._FQDN],
            ["/usr/bin/nmcli", "con", "up", "uuid", "uuid-active"],
        ]

    def test_reconciling_reads_the_dhcp_name_of_the_active_profile(self, station) -> None:
        """Read by name, the namesake's blank value would come back with it and never match."""
        a, nmcli = station(
            _Device("eth0", "192.0.2.10/24", "Wired connection 1", fqdn=self._FQDN, profile_uuid="uuid-active"),
            saved=(_Saved("Wired connection 1", "eth0", rank="yes:100:999", profile_uuid="uuid-namesake"),),
        )
        result = a.set_dhcp_fqdn(self._FQDN, reconnect=False)
        assert (result.ok, result.message) == (True, "Unchanged.")
        assert a._broker.calls == []
        assert [c[-2:] for c in nmcli.calls if c[1:3] == ["-g", "ipv4.dhcp-fqdn"]] == [["uuid", "uuid-active"]]

    def _vlan_and_namesakes(self, station, *, active_vlan: tuple[str, str] = ("eth0", "10"), **fail):
        """eth0.10 active; saved copies of it, naming its interface or none, its parent given as
        the interface or the parent's profile; a manual copy; another adapter's VLAN of the same
        name; other VLANs; and an ethernet profile named like it."""
        return station(
            _Device("eth0", "192.0.2.10/24", "Wired connection 1"),
            _Device("eth0.10", "169.254.32.55/16", "eth0.10", kind="vlan", vlan=active_vlan),
            saved=(
                _Saved("eth0.10", "eth0.10", kind="vlan", vlan=("eth0", "10"), profile_uuid="uuid-copy"),
                _Saved("Hand-made", "", kind="vlan", vlan=("eth0", "10")),
                _Saved("On the parent profile", "", kind="vlan", vlan=("uuid-Wired connection 1", "10")),
                _Saved("Manual", "eth0.10", kind="vlan", vlan=("eth0", "10"), rank="no:0:0"),
                _Saved("eth0.10", "eth0.10", kind="vlan", vlan=("eth1", "10"), profile_uuid="uuid-eth1"),
                _Saved("eth0.20", "eth0.20", kind="vlan", vlan=("eth0", "20")),
                _Saved("Unplaceable", "", kind="vlan", vlan=("eth0", "ten")),
                _Saved("eth0.10", "of-dup0", profile_uuid="uuid-ethernet"),
            ),
            **fail,
        )

    def test_removing_a_vlan_deletes_its_saved_copies_too(self, station) -> None:
        """A copy for the same parent and VLAN id that autoconnects would bring the VLAN straight
        back; a manual one, or another adapter's VLAN whose name shortens alike, would not."""
        a, _ = self._vlan_and_namesakes(station)
        assert a.delete_vlan("eth0.10").ok
        assert [call.argv for call in a._broker.calls] == [
            ["/usr/bin/nmcli", "con", "delete"]
            + [
                "uuid",
                "uuid-eth0.10",
                "uuid",
                "uuid-copy",
                "uuid",
                "uuid-Hand-made",
                "uuid",
                "uuid-On the parent profile",
            ]
        ]
        assert a._broker.calls[0].reason == "Delete the profiles of VLAN interface eth0.10"

    def test_an_active_vlan_that_cannot_be_placed_deletes_only_itself(self, station) -> None:
        a, _ = self._vlan_and_namesakes(station, active_vlan=("eth0", "ten"))
        assert a.delete_vlan("eth0.10").ok
        assert [call.argv[3:] for call in a._broker.calls] == [["uuid", "uuid-eth0.10"]]

    # What nmcli 1.52 printed on the bench, exit status 10, for one UUID of several already gone.
    _ONE_GONE = make_nonzero(rc=10, stderr="Error: cannot delete unknown connection(s): uuid 'uuid-copy'.")

    def _delete_then(self, a, nmcli, **after) -> None:
        """Apply *after* to the fake nmcli once the delete has run, as NetworkManager would."""
        run = a._broker.run

        def _run(*args, **kwargs):
            try:
                return run(*args, **kwargs)
            finally:
                for key, value in after.items():
                    setattr(nmcli, key, value)

        a._broker.run = _run

    def test_a_copy_gone_before_the_delete_still_reports_the_vlan_removed(self, station) -> None:
        """nmcli deletes every UUID it finds and exits 10 for one already gone."""
        a, nmcli = self._vlan_and_namesakes(station)
        a._broker.responses = [self._ONE_GONE]
        self._delete_then(a, nmcli, devices=nmcli.devices[:1], saved=())
        result = a.delete_vlan("eth0.10")
        assert (result.ok, result.message) == (True, "Removed VLAN interface eth0.10.")

    @pytest.mark.parametrize("after", ["still up", "unreadable"])
    def test_a_partial_delete_is_a_failure_unless_the_vlan_is_gone(self, station, after: str) -> None:
        """Exit 10 may name the active profile itself, replaced since it was read."""
        a, nmcli = self._vlan_and_namesakes(station)
        a._broker.responses = [self._ONE_GONE]
        if after == "unreadable":
            self._delete_then(a, nmcli, fail={_profile_list: _NOT_RUNNING})
        result = a.delete_vlan("eth0.10")
        assert result.ok is False
        assert "unknown connection" in result.message

    def test_unreadable_copies_remove_nothing(self, station, caplog) -> None:
        """Removing only the active profile would leave a copy to bring the VLAN back."""
        a, _ = self._vlan_and_namesakes(station, fail={_profile_detail: _NOT_RUNNING})
        result = a.delete_vlan("eth0.10")
        assert (result.ok, result.message) == (False, "Could not read the profiles of eth0.10; nothing was removed.")
        assert a._broker.calls == []
        assert "nmcli VLAN profile read failed" in caplog.text

    def test_removing_a_vlan_deletes_only_its_own_profile(self, station) -> None:
        """``con delete`` given the name would also delete the ethernet profile sharing it."""
        a, _ = station(
            _Device("eth0", "192.0.2.10/24", "Wired connection 1"),
            _Device("eth0.99", "169.254.32.55/16", "eth0.99", kind="vlan", vlan=("eth0", "99")),
            saved=(_Saved("eth0.99", "of-dup0", profile_uuid="uuid-ethernet"),),
        )
        result = a.delete_vlan("eth0.99")
        assert (result.ok, result.message) == (True, "Removed VLAN interface eth0.99.")
        assert [call.argv for call in a._broker.calls] == [["/usr/bin/nmcli", "con", "delete", "uuid", "uuid-eth0.99"]]


class TestWhichProfileAWriteAddresses:
    def test_a_vpn_running_over_the_interface_is_not_its_profile(self, adapter) -> None:
        """nmcli lists a plugin VPN with the device it runs over, as the card's device read does not."""
        a, _captured, responses = adapter
        _set(
            responses,
            _PROFILES,
            stdout="Backstage VPN:uuid-vpn:vpn:eth0:yes:0:0\n"
            "Wired connection 1:uuid-wired:802-3-ethernet:eth0:yes:0:0\n",
        )
        assert a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP)).ok
        assert {call.argv[4] for call in a._broker.calls} == {"uuid-wired"}

    def test_no_interface_has_no_profile(self, station) -> None:
        """Every inactive profile lists an empty device, and an unbound one an empty interface."""
        a, _ = station(
            _Device("eth1", state="20 (unavailable)"),
            saved=(_Saved("Any wired", ""), _Saved("eth1.10", "eth1.10", kind="vlan")),
        )
        assert not a.apply_ipv4("", Ipv4Config(method=Ipv4Method.DHCP)).ok
        assert not a.renew_lease("").ok
        assert not a.delete_vlan("").ok
        assert a._broker.calls == []
