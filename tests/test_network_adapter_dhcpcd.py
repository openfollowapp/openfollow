# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for the dhcpcd-backed IPv4 network adapter."""

from __future__ import annotations

import re
import subprocess

import pytest

from openfollow.network.adapter import VLAN_UNSUPPORTED_MESSAGE, Ipv4Config, Ipv4Method
from openfollow.network.dhcpcd_adapter import DhcpcdAdapter
from openfollow.privilege.broker import PrivilegeError
from tests._fake_broker import FakeBroker, make_failure, make_nonzero

pytestmark = pytest.mark.unit


@pytest.fixture
def broker() -> FakeBroker:
    """Default FakeBroker for adapter tests. Returns rc=0 / no exceptions
    on every call so apply / renew exercise the happy path unless the
    test seeds :attr:`FakeBroker.responses` / :attr:`exceptions`."""
    return FakeBroker()


@pytest.fixture
def adapter(tmp_path, broker):
    conf = tmp_path / "dhcpcd.conf"
    conf.write_text("# pre-existing content\nhostname\n")
    a = DhcpcdAdapter(conf_path=conf, broker=broker)
    # Apply tests exercise the write/bounce path, not the post-apply address
    # read-back; stub it so they stay hermetic (no real ``dhcpcd -U``).
    a._read_lease = lambda iface: None  # type: ignore[assignment,method-assign]
    return a, conf


@pytest.fixture
def record_run(monkeypatch, adapter, broker):
    """Backwards-compat fixture: returns the broker's call list so
    tests can still assert on argv. The list shape matches the legacy
    ``_run`` record (list of argv lists) – broker calls land here as
    ``call.argv`` so ``[expected_argv] in record_run`` keeps working."""
    return _ArgvProxy(broker)


class _ArgvProxy:
    """View onto :attr:`FakeBroker.calls` that exposes the recorded
    argv list with the legacy ``[argv] in proxy`` semantics so the
    existing assertions keep working unchanged."""

    def __init__(self, broker: FakeBroker) -> None:
        self._broker = broker

    def __contains__(self, item: list[str]) -> bool:
        # Each broker call is ``["sudo", "-n", *argv]`` shape internally
        # but ``FakeBroker.calls[i].argv`` is the bare argv the adapter
        # passed in. dhcpcd argv is e.g. ``["/usr/sbin/dhcpcd", "-k",
        # "eth0"]``; legacy tests assert ``["dhcpcd", "-k", "eth0"]``.
        # Strip the leading path so legacy expectations match.
        target = [_basename(item[0])] + list(item[1:]) if item else item
        for call in self._broker.calls:
            argv = [_basename(call.argv[0])] + list(call.argv[1:]) if call.argv else call.argv
            if argv == target:
                return True
        return False


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


class TestApplyIpv4:
    def test_static_writes_block_and_runs_dhcpcd(self, adapter, record_run) -> None:
        a, conf = adapter
        config = Ipv4Config(
            method=Ipv4Method.STATIC,
            address="192.168.1.50",
            prefix=24,
            router="192.168.1.1",
            dns=("8.8.8.8", "1.1.1.1"),
        )
        result = a.apply_ipv4("eth0", config)
        assert result.ok is True
        text = conf.read_text()
        assert "# >>> openfollow managed: eth0 >>>" in text
        assert "static ip_address=192.168.1.50/24" in text
        assert "static routers=192.168.1.1" in text
        assert "static domain_name_servers=8.8.8.8 1.1.1.1" in text
        # original content preserved
        assert "# pre-existing content" in text
        # called release + rebind
        assert ["dhcpcd", "-k", "eth0"] in record_run
        assert ["dhcpcd", "-n", "eth0"] in record_run

    def test_dhcp_writes_minimal_block(self, adapter, record_run) -> None:
        a, conf = adapter
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is True
        text = conf.read_text()
        assert "interface eth0" in text
        assert "static ip_address" not in text

    def test_dhcp_manual_uses_inform(self, adapter, record_run) -> None:
        a, conf = adapter
        config = Ipv4Config(
            method=Ipv4Method.DHCP_WITH_MANUAL_ADDRESS,
            address="192.168.1.77",
        )
        a.apply_ipv4("eth0", config)
        text = conf.read_text()
        assert "inform 192.168.1.77" in text

    def test_replace_existing_block(self, adapter, record_run) -> None:
        a, conf = adapter
        a.apply_ipv4(
            "eth0",
            Ipv4Config(method=Ipv4Method.STATIC, address="10.0.0.5", prefix=24, router="10.0.0.1"),
        )
        a.apply_ipv4(
            "eth0",
            Ipv4Config(method=Ipv4Method.STATIC, address="10.0.0.6", prefix=24, router="10.0.0.1"),
        )
        text = conf.read_text()
        assert text.count("# >>> openfollow managed: eth0 >>>") == 1
        assert "10.0.0.6" in text
        assert "10.0.0.5" not in text

    def test_separate_iface_blocks_coexist(self, adapter, record_run) -> None:
        a, conf = adapter
        a.apply_ipv4(
            "eth0",
            Ipv4Config(method=Ipv4Method.STATIC, address="10.0.0.5", prefix=24, router="10.0.0.1"),
        )
        a.apply_ipv4(
            "wlan0",
            Ipv4Config(method=Ipv4Method.STATIC, address="10.0.1.5", prefix=24, router="10.0.1.1"),
        )
        text = conf.read_text()
        assert "managed: eth0" in text
        assert "managed: wlan0" in text


class TestRenewLease:
    def test_renew_lease_runs_dhcpcd_n(self, adapter, record_run) -> None:
        a, _ = adapter
        result = a.renew_lease("eth0")
        assert result.ok is True
        assert ["dhcpcd", "-n", "eth0"] in record_run


class TestStripBlock:
    def test_strip_removes_only_named_block(self) -> None:
        text = (
            "hostname\n"
            "# >>> openfollow managed: eth0 >>>\n"
            "interface eth0\n"
            "static ip_address=10.0.0.5/24\n"
            "# <<< openfollow managed: eth0 <<<\n"
            "# >>> openfollow managed: wlan0 >>>\n"
            "interface wlan0\n"
            "# <<< openfollow managed: wlan0 <<<\n"
        )
        stripped = DhcpcdAdapter._strip_block(text, "eth0")
        assert "managed: eth0" not in stripped
        assert "managed: wlan0" in stripped


class TestRunHelper:
    def test_run_uses_subprocess_run(self, monkeypatch) -> None:
        import subprocess as sp

        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        calls = []

        def fake_run(argv, capture_output=True, text=True, timeout=None):
            calls.append(list(argv))
            return sp.CompletedProcess(argv, 0, "ok\n", "")

        monkeypatch.setattr(sp, "run", fake_run)
        a = DhcpcdAdapter()
        result = a._run(["dhcpcd", "--help"])
        assert result.stdout == "ok\n"
        assert calls == [["dhcpcd", "--help"]]

    def test_run_raises_on_check_failure(self, monkeypatch) -> None:
        import subprocess as sp

        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        monkeypatch.setattr(
            sp,
            "run",
            lambda argv, capture_output, text, timeout: sp.CompletedProcess(argv, 1, "", "boom"),
        )
        a = DhcpcdAdapter()
        with pytest.raises(RuntimeError, match="boom"):
            a._run(["dhcpcd", "x"])


class TestReadConf:
    def test_returns_empty_when_file_missing(self, tmp_path) -> None:
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        a = DhcpcdAdapter(conf_path=tmp_path / "nope.conf")
        assert a._read_conf() == ""


class TestListInterfaces:
    def test_delegates_to_psutil(self, monkeypatch) -> None:
        from openfollow.network import dhcpcd_adapter as mod
        from openfollow.network.adapter import NetworkInterface

        fake = [NetworkInterface(name="eth0", mac=None, kind=None, is_up=True)]

        class FakePsutil:
            def list_interfaces(self):
                return fake

        monkeypatch.setattr(
            mod,
            "PsutilReadOnlyAdapter",
            FakePsutil,
            raising=False,
        )
        # Local import is inside the method; patch via attribute chain.
        from openfollow.network import psutil_adapter as pa

        monkeypatch.setattr(pa, "PsutilReadOnlyAdapter", FakePsutil)
        a = mod.DhcpcdAdapter()
        assert a.list_interfaces() == fake


class TestGetState:
    def _setup_with_lease(self, tmp_path, monkeypatch, *, block: str = "", lease_stdout: str = ""):
        import subprocess as sp

        from openfollow.network import psutil_adapter as pa
        from openfollow.network.adapter import NetworkInterface
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        conf.write_text(block)

        class FakePsutil:
            def list_interfaces(self):
                return [NetworkInterface(name="eth0", mac=None, kind=None, is_up=True)]

        monkeypatch.setattr(pa, "PsutilReadOnlyAdapter", FakePsutil)
        a = DhcpcdAdapter(conf_path=conf)

        def fake_run(argv, *, check=True):
            if argv[:2] == ["dhcpcd", "-U"]:
                return sp.CompletedProcess(argv, 0 if lease_stdout else 1, lease_stdout, "")
            return sp.CompletedProcess(argv, 0, "", "")

        a._run = fake_run
        return a

    def test_unknown_iface_returns_none(self, tmp_path, monkeypatch) -> None:
        a = self._setup_with_lease(tmp_path, monkeypatch)
        assert a.get_state("nope0") is None

    def test_dhcp_method_lease_state(self, tmp_path, monkeypatch) -> None:
        a = self._setup_with_lease(
            tmp_path,
            monkeypatch,
            lease_stdout=(
                "ip_address=10.0.0.50\n"
                "subnet_cidr=24\n"
                "routers=10.0.0.1\n"
                "domain_name_servers=8.8.8.8 1.1.1.1\n"
                "dhcp_lease_time=3600\n"
            ),
        )
        from openfollow.network.adapter import Ipv4Method

        state = a.get_state("eth0")
        assert state is not None
        assert state.ipv4.method == Ipv4Method.DHCP
        assert state.ipv4.address == "10.0.0.50"
        assert state.ipv4.prefix == 24
        assert state.lease.lease_seconds_remaining == 3600

    def test_static_method_block_overrides_lease(self, tmp_path, monkeypatch) -> None:
        block = (
            "# >>> openfollow managed: eth0 >>>\n"
            "interface eth0\n"
            "static ip_address=192.168.1.10/24\n"
            "static routers=192.168.1.1\n"
            "static domain_name_servers=9.9.9.9\n"
            "# <<< openfollow managed: eth0 <<<\n"
        )
        a = self._setup_with_lease(
            tmp_path,
            monkeypatch,
            block=block,
            lease_stdout=("ip_address=10.0.0.99\nsubnet_cidr=16\n"),
        )
        from openfollow.network.adapter import Ipv4Method

        state = a.get_state("eth0")
        assert state is not None
        assert state.ipv4.method == Ipv4Method.STATIC
        assert state.ipv4.address == "192.168.1.10"
        assert state.ipv4.prefix == 24
        assert state.ipv4.router == "192.168.1.1"
        assert "9.9.9.9" in state.ipv4.dns

    def test_dhcp_manual_method_via_inform(self, tmp_path, monkeypatch) -> None:
        block = (
            "# >>> openfollow managed: eth0 >>>\n"
            "interface eth0\n"
            "inform 192.168.1.77\n"
            "# <<< openfollow managed: eth0 <<<\n"
        )
        a = self._setup_with_lease(tmp_path, monkeypatch, block=block)
        from openfollow.network.adapter import Ipv4Method

        state = a.get_state("eth0")
        assert state is not None
        assert state.ipv4.method == Ipv4Method.DHCP_WITH_MANUAL_ADDRESS


class TestReadLeaseEdges:
    def _make(self, tmp_path):
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        return DhcpcdAdapter(conf_path=tmp_path / "dhcpcd.conf")

    def test_no_lease_returns_none(self, tmp_path, monkeypatch) -> None:
        a = self._make(tmp_path)
        import subprocess as sp

        a._run = lambda argv, *, check=True: sp.CompletedProcess(argv, 1, "", "")
        assert a._read_lease("eth0") is None

    def test_empty_lease_returns_none(self, tmp_path) -> None:
        a = self._make(tmp_path)
        import subprocess as sp

        a._run = lambda argv, *, check=True: sp.CompletedProcess(argv, 0, "", "")
        assert a._read_lease("eth0") is None

    def test_subnet_mask_fallback_when_cidr_absent(self, tmp_path) -> None:
        a = self._make(tmp_path)
        import subprocess as sp

        a._run = lambda argv, *, check=True: sp.CompletedProcess(
            argv,
            0,
            "ip_address=10.0.0.5\nsubnet_mask=255.255.255.0\nrouters=10.0.0.1\n",
            "",
        )
        lease = a._read_lease("eth0")
        assert lease is not None
        assert lease.prefix == 24

    def test_bad_cidr_drops_to_none(self, tmp_path) -> None:
        a = self._make(tmp_path)
        import subprocess as sp

        a._run = lambda argv, *, check=True: sp.CompletedProcess(
            argv,
            0,
            "ip_address=10.0.0.5\nsubnet_cidr=notanumber\n",
            "",
        )
        lease = a._read_lease("eth0")
        assert lease is not None
        assert lease.prefix is None

    def test_invalid_lease_time_drops_to_none(self, tmp_path) -> None:
        a = self._make(tmp_path)
        import subprocess as sp

        a._run = lambda argv, *, check=True: sp.CompletedProcess(
            argv,
            0,
            "ip_address=10.0.0.5\ndhcp_lease_time=notanumber\n",
            "",
        )
        lease = a._read_lease("eth0")
        assert lease is not None
        assert lease.lease_seconds_remaining is None

    def test_subprocess_failure_returns_none(self, tmp_path) -> None:
        a = self._make(tmp_path)

        def boom(argv, *, check=True):
            raise FileNotFoundError("dhcpcd missing")

        a._run = boom
        assert a._read_lease("eth0") is None


class TestApplyErrors:
    def test_write_failure_returns_failure(self, tmp_path) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        conf.write_text("")
        a = DhcpcdAdapter(conf_path=conf, broker=FakeBroker())

        # The tmp_path conf bypasses the broker (the adapter only
        # routes /etc/dhcpcd.conf writes through tee), so the write
        # failure surface is a plain OSError from ``Path.write_text``.
        def boom(text):
            raise OSError("denied")

        a._write_conf_privileged = boom  # type: ignore[method-assign]
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is False
        assert "denied" in result.message

    def test_rebind_fallback_to_systemctl_reload(self, tmp_path) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        conf.write_text("")
        broker = FakeBroker()
        # Per-call responses follow the apply order: release (ok),
        # rebind (fail), reload (ok).
        broker.responses = [
            subprocess.CompletedProcess(["sudo"], 0, "", ""),  # release
            make_nonzero(stderr="rebind failed"),  # rebind
            subprocess.CompletedProcess(["sudo"], 0, "", ""),  # reload
        ]
        a = DhcpcdAdapter(conf_path=conf, broker=broker)
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is True
        assert any("rebind" in p for p in result.partial_failures)

    def test_rebind_and_reload_both_fail(self, tmp_path) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        conf.write_text("")
        broker = FakeBroker()
        broker.responses = [
            subprocess.CompletedProcess(["sudo"], 0, "", ""),  # release
            make_nonzero(stderr="rebind boom"),  # rebind
            make_nonzero(stderr="reload boom"),  # reload
        ]
        a = DhcpcdAdapter(conf_path=conf, broker=broker)
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is False
        assert "previous one was restored" in result.message
        assert "reload boom" in result.message

    def test_release_partial_failure_is_warning(self, tmp_path) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        conf.write_text("")
        broker = FakeBroker()
        broker.responses = [
            make_nonzero(stderr="release failed"),  # release
            subprocess.CompletedProcess(["sudo"], 0, "", ""),  # rebind
        ]
        a = DhcpcdAdapter(conf_path=conf, broker=broker)
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is True
        assert any("release failed" in p for p in result.partial_failures)

    def test_apply_privilege_error_surfaces(self, tmp_path) -> None:
        """A PrivilegeError raised by the broker (e.g. operator cancelled
        the password prompt) on the rebind step surfaces as an
        ``ApplyResult`` failure – never an unhandled exception. Release
        failures are partial warnings and don't trip apply; rebind is
        the gate."""
        from openfollow.network.adapter import Ipv4Config, Ipv4Method
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        conf.write_text("")
        broker = FakeBroker()
        # Release ok, rebind cancelled, reload also cancelled.
        broker.exceptions = [
            None,
            make_failure("password prompt cancelled"),
            make_failure("password prompt cancelled"),
        ]
        a = DhcpcdAdapter(conf_path=conf, broker=broker)
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is False
        assert "password prompt cancelled" in result.message

    def test_apply_strip_existing_only_branch(self, tmp_path) -> None:
        # When current conf is whitespace-only, the rstrip+newline branch
        # doesn't fire and the block is written directly. Needs a working
        # broker so the bounce succeeds and the new conf isn't rolled back.
        from openfollow.network.adapter import Ipv4Config, Ipv4Method
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        conf.write_text("   \n   \n")
        a = DhcpcdAdapter(conf_path=conf, broker=FakeBroker())
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is True
        text = conf.read_text()
        # No "old" content to preserve – block written directly.
        assert text.startswith("# >>> openfollow managed: eth0 >>>")

    def test_apply_outer_exception_caught(self, tmp_path) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        conf.write_text("")
        a = DhcpcdAdapter(conf_path=conf)

        def boom():
            raise RuntimeError("read crashed")

        a._read_conf_for_write = boom
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is False
        assert "Could not update" in result.message
        # Says the interface was left alone, so the operator knows where they stand.
        assert "nothing was changed" in result.message


class TestRenewErrors:
    def test_renew_failure_returns_message(self, tmp_path) -> None:
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        conf.write_text("")
        broker = FakeBroker()
        broker.responses = [make_nonzero(stderr="no permission")]
        a = DhcpcdAdapter(conf_path=conf, broker=broker)
        result = a.renew_lease("eth0")
        assert result.ok is False
        assert "no permission" in result.message

    def test_renew_with_blank_stderr_uses_broker_failure_format(self, tmp_path) -> None:
        """Real broker formats blank stderr as ``Command exited with code N``.
        Renew path surfaces that formatted message."""
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        conf.write_text("")
        broker = FakeBroker()
        broker.responses = [make_nonzero(stderr="")]
        a = DhcpcdAdapter(conf_path=conf, broker=broker)
        result = a.renew_lease("eth0")
        assert result.ok is False
        assert "Command exited with code 1" in result.message

    def test_write_dhcpcd_conf_routed_through_broker_atomically(self, monkeypatch) -> None:
        """When the conf path IS /etc/dhcpcd.conf, ``_write_conf_privileged``
        routes through ``broker.run`` (the production path that needs sudo),
        staging to the sibling ``.tmp`` then committing with an atomic ``mv``
        – never writing the live conf directly."""
        from openfollow.network.adapter import Ipv4Config, Ipv4Method
        from openfollow.network.dhcpcd_adapter import DHCPCD_CONF, DhcpcdAdapter
        from openfollow.privilege.capabilities import (
            NETWORK_DHCPCD_CONF_COMMIT,
            NETWORK_DHCPCD_CONF_WRITE_TMP,
        )

        broker = FakeBroker()
        a = DhcpcdAdapter(conf_path=DHCPCD_CONF, broker=broker)
        # Bypass the real /etc/dhcpcd.conf read – make it return empty.
        a._read_conf_for_write = lambda: ""  # type: ignore[method-assign]
        a._read_lease = lambda iface: None  # type: ignore[assignment,method-assign]
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is True
        # Step 1: tee the full file into the staging ``.tmp`` (never the live conf).
        stage = broker.calls[0]
        assert stage.capability is NETWORK_DHCPCD_CONF_WRITE_TMP
        assert stage.argv == ["/usr/bin/tee", "/etc/dhcpcd.conf.tmp"]
        assert stage.stdin is not None
        assert "interface eth0" in stage.stdin
        # Step 2: atomic ``mv`` of that exact tmp onto the live conf.
        commit = broker.calls[1]
        assert commit.capability is NETWORK_DHCPCD_CONF_COMMIT
        assert commit.argv == ["/usr/bin/mv", "/etc/dhcpcd.conf.tmp", "/etc/dhcpcd.conf"]
        # The live conf is never a direct ``tee`` target.
        assert ["/usr/bin/tee", "/etc/dhcpcd.conf"] not in [c.argv for c in broker.calls]

    def test_failed_stage_never_commits_so_live_conf_is_not_truncated(self, monkeypatch) -> None:
        """A failure of the staging ``tee`` (killed / OOM / timeout / cancelled
        prompt) must NOT run the ``mv`` that would replace the live conf – the
        live ``/etc/dhcpcd.conf`` can never be left truncated, and the apply
        chain (release / rebind / reload) doesn't run either."""
        from openfollow.network.adapter import Ipv4Config, Ipv4Method
        from openfollow.network.dhcpcd_adapter import DHCPCD_CONF, DhcpcdAdapter
        from openfollow.privilege.capabilities import (
            NETWORK_DHCPCD_CONF_COMMIT,
            NETWORK_DHCPCD_CONF_WRITE_TMP,
        )

        broker = FakeBroker()
        broker.exceptions = [make_failure("write killed mid-stream")]  # tee step fails
        a = DhcpcdAdapter(conf_path=DHCPCD_CONF, broker=broker)
        a._read_conf_for_write = lambda: "interface eth0\n# existing\n"  # type: ignore[method-assign]
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is False
        assert "write killed mid-stream" in result.message
        # Only the staging tee ran; the commit ``mv`` (and the bounce) did not.
        assert [c.capability.name for c in broker.calls] == [NETWORK_DHCPCD_CONF_WRITE_TMP.name]
        assert NETWORK_DHCPCD_CONF_COMMIT.name not in [c.capability.name for c in broker.calls]

    def test_conf_write_privilege_error_surfaces(self, monkeypatch) -> None:
        """A PrivilegeError on the dhcpcd.conf write (e.g. operator
        cancels the password prompt) surfaces as a clean ApplyResult
        – the rest of the apply chain doesn't run."""
        from openfollow.network.adapter import Ipv4Config, Ipv4Method
        from openfollow.network.dhcpcd_adapter import DHCPCD_CONF, DhcpcdAdapter

        broker = FakeBroker()
        broker.exceptions = [make_failure("operator cancelled")]
        a = DhcpcdAdapter(conf_path=DHCPCD_CONF, broker=broker)
        a._read_conf_for_write = lambda: ""  # type: ignore[method-assign]
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is False
        assert "operator cancelled" in result.message
        # Subsequent commit / release / rebind / reload calls must not have run.
        assert len(broker.calls) == 1

    def test_renew_without_broker_surfaces_message(self, tmp_path) -> None:
        """Adapter constructed without a broker can't perform writes.
        ``renew_lease`` surfaces a clean ``ApplyResult.ok=False`` so the
        on-screen banner doesn't hand the operator a stack trace."""
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        conf.write_text("")
        a = DhcpcdAdapter(conf_path=conf)  # broker omitted
        result = a.renew_lease("eth0")
        assert result.ok is False
        # Names something the operator can act on, not an internal object.
        assert "privileged helper is not configured" in result.message


class TestBuildBlockBranches:
    def test_static_with_dns_only(self) -> None:
        """STATIC method, no address – only DNS line in the block."""
        from openfollow.network.adapter import Ipv4Config, Ipv4Method
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        block = DhcpcdAdapter._build_block(
            "eth0",
            Ipv4Config(method=Ipv4Method.STATIC, dns=("9.9.9.9",)),
        )
        # No `static ip_address=` since address/prefix are missing.
        assert "static ip_address=" not in block
        assert "static domain_name_servers=9.9.9.9" in block

    def test_dhcp_with_manual_includes_dns(self) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        block = DhcpcdAdapter._build_block(
            "eth0",
            Ipv4Config(
                method=Ipv4Method.DHCP_WITH_MANUAL_ADDRESS,
                address="10.0.0.5",
                dns=("9.9.9.9",),
            ),
        )
        assert "inform 10.0.0.5" in block
        assert "static domain_name_servers=9.9.9.9" in block

    def test_dhcp_with_dns_override(self) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        block = DhcpcdAdapter._build_block(
            "eth0",
            Ipv4Config(method=Ipv4Method.DHCP, dns=("9.9.9.9",)),
        )
        assert "static domain_name_servers=9.9.9.9" in block


class TestDetectMethodFallback:
    def test_block_with_only_dns_falls_back_to_dhcp(self, tmp_path) -> None:
        """A managed block that has neither `static ip_address=` nor `inform `
        is still a DHCP-method block (the operator overrode only DNS)."""
        from openfollow.network.adapter import Ipv4Method
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        conf.write_text(
            "# >>> openfollow managed: eth0 >>>\n"
            "interface eth0\n"
            "static domain_name_servers=9.9.9.9\n"
            "# <<< openfollow managed: eth0 <<<\n"
        )
        a = DhcpcdAdapter(conf_path=conf)
        assert a._detect_method("eth0") == Ipv4Method.DHCP


class TestReadManagedOverrides:
    def test_address_without_slash(self, tmp_path) -> None:
        """Operator's manual edit might omit /prefix – must still parse."""
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        conf.write_text(
            "# >>> openfollow managed: eth0 >>>\n"
            "interface eth0\n"
            "static ip_address=10.0.0.5\n"
            "# <<< openfollow managed: eth0 <<<\n"
        )
        a = DhcpcdAdapter(conf_path=conf)
        overrides = a._read_managed_overrides("eth0")
        assert overrides is not None
        assert overrides.get("address") == "10.0.0.5"
        assert "prefix" not in overrides

    def test_bad_prefix_is_skipped(self, tmp_path) -> None:
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        conf.write_text(
            "# >>> openfollow managed: eth0 >>>\n"
            "interface eth0\n"
            "static ip_address=10.0.0.5/notanumber\n"
            "# <<< openfollow managed: eth0 <<<\n"
        )
        a = DhcpcdAdapter(conf_path=conf)
        overrides = a._read_managed_overrides("eth0")
        assert overrides is not None
        assert overrides.get("address") == "10.0.0.5"
        assert "prefix" not in overrides


class TestReadLeaseSubnetMaskFallback:
    def test_subnet_mask_does_not_overwrite_existing_prefix(self, tmp_path) -> None:
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        conf.write_text("")
        a = DhcpcdAdapter(conf_path=conf)
        import subprocess as sp

        a._run = lambda argv, *, check=True: sp.CompletedProcess(
            argv,
            0,
            "ip_address=10.0.0.5\nsubnet_cidr=16\nsubnet_mask=255.255.255.0\n",
            "",
        )
        lease = a._read_lease("eth0")
        assert lease is not None
        assert lease.prefix == 16  # cidr wins; mask doesn't stomp

    def test_lease_skips_line_without_equals(self, tmp_path) -> None:
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        conf.write_text("")
        a = DhcpcdAdapter(conf_path=conf)
        import subprocess as sp

        a._run = lambda argv, *, check=True: sp.CompletedProcess(
            argv,
            0,
            "garbage_no_equals\nip_address=10.0.0.5\n",
            "",
        )
        lease = a._read_lease("eth0")
        assert lease is not None
        assert lease.address == "10.0.0.5"

    def test_lease_returns_none_when_no_relevant_keys(self, tmp_path) -> None:
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        conf.write_text("")
        a = DhcpcdAdapter(conf_path=conf)
        import subprocess as sp

        # Output is non-empty but holds no recognised keys.
        a._run = lambda argv, *, check=True: sp.CompletedProcess(
            argv,
            0,
            "host_name=pi\n",
            "",
        )
        assert a._read_lease("eth0") is None


class TestBuildBlockSkipBranches:
    def test_dhcp_with_manual_no_dns_skips_dns_line(self) -> None:
        """Covers the branch where DHCP_WITH_MANUAL_ADDRESS + empty DNS
        skips the dns line emission."""
        from openfollow.network.adapter import Ipv4Config, Ipv4Method
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        block = DhcpcdAdapter._build_block(
            "eth0",
            Ipv4Config(method=Ipv4Method.DHCP_WITH_MANUAL_ADDRESS, address="10.0.0.5"),
        )
        assert "static domain_name_servers" not in block

    def test_dhcp_no_dns_emits_minimal_block(self) -> None:
        """DHCP with no DNS = pure DHCP, no static lines."""
        from openfollow.network.adapter import Ipv4Config, Ipv4Method
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        block = DhcpcdAdapter._build_block(
            "eth0",
            Ipv4Config(method=Ipv4Method.DHCP),
        )
        assert "static domain_name_servers" not in block
        assert "interface eth0" in block

    def test_dhcp_with_manual_no_address_emits_no_inform(self) -> None:
        """DHCP_WITH_MANUAL with no address skips the inform line – covers
        the false branch of `if config.address:` in _build_block."""
        from openfollow.network.adapter import Ipv4Config, Ipv4Method
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        block = DhcpcdAdapter._build_block(
            "eth0",
            Ipv4Config(method=Ipv4Method.DHCP_WITH_MANUAL_ADDRESS, dns=("9.9.9.9",)),
        )
        assert "inform" not in block
        assert "static domain_name_servers=9.9.9.9" in block


class TestGetStateOverrideBranches:
    def test_override_addr_not_string_is_skipped(self, tmp_path, monkeypatch) -> None:
        import subprocess as sp

        from openfollow.network import psutil_adapter as pa
        from openfollow.network.adapter import NetworkInterface
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        class FakePsutil:
            def list_interfaces(self):
                return [NetworkInterface(name="eth0", mac=None, kind=None, is_up=True)]

        monkeypatch.setattr(pa, "PsutilReadOnlyAdapter", FakePsutil)
        conf = tmp_path / "dhcpcd.conf"
        conf.write_text("")
        a = DhcpcdAdapter(conf_path=conf)
        a._run = lambda argv, *, check=True: sp.CompletedProcess(
            argv,
            0,
            "ip_address=10.0.0.99\nsubnet_cidr=16\n",
            "",
        )
        # Inject a managed-override dict where "address" is a non-string.
        a._read_managed_overrides = lambda iface: {"address": 42, "prefix": "not-int"}
        state = a.get_state("eth0")
        assert state is not None
        # Non-str override ignored; lease value preserved.
        assert state.ipv4.address == "10.0.0.99"
        assert state.ipv4.prefix == 16


# ---------------------------------------------------------------------------
# Apply ordering, post-apply verification, orphan markers, and
# adapter-boundary validation.
# ---------------------------------------------------------------------------


class TestApplyRollback:
    """A failed bounce must not leave the new (unapplied) config
    persisted on disk – the prior conf is restored."""

    def test_conf_restored_when_bounce_fails(self, tmp_path) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        original = "# pre-existing\nhostname\n"
        conf.write_text(original)
        broker = FakeBroker()
        broker.responses = [
            subprocess.CompletedProcess(["sudo"], 0, "", ""),  # release ok
            make_nonzero(stderr="rebind boom"),  # rebind fails
            make_nonzero(stderr="reload boom"),  # reload also fails
        ]
        a = DhcpcdAdapter(conf_path=conf, broker=broker)
        result = a.apply_ipv4(
            "eth0",
            Ipv4Config(method=Ipv4Method.STATIC, address="10.0.0.5", prefix=24, router="10.0.0.1"),
        )
        assert result.ok is False
        # The unapplied static block must not survive on disk.
        assert conf.read_text() == original
        assert "10.0.0.5" not in conf.read_text()

    def test_conf_restored_when_broker_not_configured(self, tmp_path) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        original = "# pre-existing\n"
        conf.write_text(original)
        a = DhcpcdAdapter(conf_path=conf)  # no broker → bounce never happens
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is False
        assert "privileged helper is not configured" in result.message
        assert conf.read_text() == original

    def test_rebounce_attempted_after_double_failure(self, tmp_path) -> None:
        # release dropped the lease, then rebind + reload both failed. After
        # restoring the prior conf the adapter must best-effort re-bounce so the
        # interface comes back up on its previous config rather than staying
        # released with no IPv4.
        from openfollow.network.adapter import Ipv4Config, Ipv4Method
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        conf.write_text("# pre-existing\n")
        broker = FakeBroker()
        broker.responses = [
            subprocess.CompletedProcess(["sudo"], 0, "", ""),  # release ok
            make_nonzero(stderr="rebind boom"),  # rebind fails
            make_nonzero(stderr="reload boom"),  # reload fails
            subprocess.CompletedProcess(["sudo"], 0, "", ""),  # re-bounce ok
        ]
        a = DhcpcdAdapter(conf_path=conf, broker=broker)
        result = a.apply_ipv4(
            "eth0",
            Ipv4Config(method=Ipv4Method.STATIC, address="10.0.0.5", prefix=24, router="10.0.0.1"),
        )
        assert result.ok is False
        # Two ``dhcpcd -n`` calls: the failed apply rebind and the recovery
        # re-bounce on the restored config.
        rebounce_calls = [c for c in broker.calls if c.argv == ["/usr/sbin/dhcpcd", "-n", "eth0"]]
        assert len(rebounce_calls) == 2
        assert "manual retry" in result.message

    def test_rebounce_failure_does_not_raise(self, tmp_path) -> None:
        # The recovery re-bounce is best-effort: if it too fails the apply still
        # returns a clean failure, never an unhandled exception.
        from openfollow.network.adapter import Ipv4Config, Ipv4Method
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        conf.write_text("# pre-existing\n")
        broker = FakeBroker()
        broker.responses = [
            subprocess.CompletedProcess(["sudo"], 0, "", ""),  # release ok
            make_nonzero(stderr="rebind boom"),  # rebind fails
            make_nonzero(stderr="reload boom"),  # reload fails
            make_nonzero(stderr="rebounce boom"),  # re-bounce also fails
        ]
        a = DhcpcdAdapter(conf_path=conf, broker=broker)
        result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is False
        assert "previous one was restored" in result.message
        assert "reload boom" in result.message

    def test_restore_failure_is_logged_not_raised(self, tmp_path, caplog) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        conf.write_text("# orig\n")
        a = DhcpcdAdapter(conf_path=conf)  # no broker → restore path taken

        calls = {"n": 0}
        original_write = a._write_conf_privileged

        def flaky(text: str) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                original_write(text)  # the apply write succeeds
                return
            raise OSError("disk full")  # the restore write fails

        a._write_conf_privileged = flaky  # type: ignore[method-assign]
        with caplog.at_level("ERROR", logger="openfollow.network.dhcpcd_adapter"):
            result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is False
        assert any("Failed to restore" in r.message for r in caplog.records)


class TestApplyVerification:
    """Best-effort post-apply read-back of the static address."""

    def _static(self):
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        return Ipv4Config(method=Ipv4Method.STATIC, address="10.0.0.5", prefix=24, router="10.0.0.1")

    def _adapter(self, tmp_path, lease):
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        conf.write_text("")
        a = DhcpcdAdapter(conf_path=conf, broker=FakeBroker())
        a._read_lease = lambda iface: lease  # type: ignore[assignment,method-assign]
        # Keep the async-settle poll hermetic – no real wall-clock sleep.
        a._settle = lambda seconds: None  # type: ignore[assignment,method-assign]
        return a

    def test_warns_when_applied_address_diverges(self, tmp_path) -> None:
        from openfollow.network.adapter import LeaseInfo

        lease = LeaseInfo(address="10.0.0.99", prefix=24, router=None, dns=(), lease_seconds_remaining=None)
        a = self._adapter(tmp_path, lease)
        result = a.apply_ipv4("eth0", self._static())
        assert result.ok is True
        assert any("10.0.0.99" in p and "10.0.0.5" in p for p in result.partial_failures)

    def test_no_warning_when_address_matches(self, tmp_path) -> None:
        from openfollow.network.adapter import LeaseInfo

        lease = LeaseInfo(address="10.0.0.5", prefix=24, router=None, dns=(), lease_seconds_remaining=None)
        a = self._adapter(tmp_path, lease)
        result = a.apply_ipv4("eth0", self._static())
        assert result.ok is True
        assert result.partial_failures == ()

    def test_no_warning_when_address_unreadable(self, tmp_path) -> None:
        a = self._adapter(tmp_path, None)
        result = a.apply_ipv4("eth0", self._static())
        assert result.ok is True
        assert result.partial_failures == ()

    def test_no_false_positive_on_stale_then_settled_address(self, tmp_path) -> None:
        # dhcpcd -n is async: the first read-back can still report the old lease
        # before the static address lands. The verify must poll/settle and emit
        # no warning once it converges on the requested address.
        from openfollow.network.adapter import LeaseInfo
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        conf.write_text("")
        a = DhcpcdAdapter(conf_path=conf, broker=FakeBroker())
        a._settle = lambda seconds: None  # type: ignore[assignment,method-assign]
        leases = [
            LeaseInfo(address="10.0.0.99", prefix=24, router=None, dns=(), lease_seconds_remaining=None),
            LeaseInfo(address="10.0.0.5", prefix=24, router=None, dns=(), lease_seconds_remaining=None),
        ]
        a._read_lease = lambda iface: leases.pop(0)  # type: ignore[assignment,method-assign]
        result = a.apply_ipv4("eth0", self._static())
        assert result.ok is True
        assert result.partial_failures == ()

    def test_warns_when_address_stays_divergent_after_settle(self, tmp_path) -> None:
        # If the address never converges across retries, the warning still fires.
        from openfollow.network.adapter import LeaseInfo

        lease = LeaseInfo(address="10.0.0.99", prefix=24, router=None, dns=(), lease_seconds_remaining=None)
        a = self._adapter(tmp_path, lease)
        settles = {"n": 0}
        a._settle = lambda seconds: settles.__setitem__("n", settles["n"] + 1)  # type: ignore[assignment,method-assign]
        result = a.apply_ipv4("eth0", self._static())
        assert result.ok is True
        assert any("10.0.0.99" in p and "10.0.0.5" in p for p in result.partial_failures)
        # Polled with a settle between each retry but not after the last attempt.
        assert settles["n"] == 2

    def test_settle_sleeps(self, tmp_path, monkeypatch) -> None:
        # The default settle delegates to time.sleep – patch it so the test
        # stays hermetic while still exercising the real line.
        from openfollow.network import dhcpcd_adapter as mod
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        slept: list[float] = []
        monkeypatch.setattr(mod.time, "sleep", lambda s: slept.append(s))
        DhcpcdAdapter(conf_path=tmp_path / "dhcpcd.conf")._settle(0.25)
        assert slept == [0.25]

    def test_verify_skips_static_without_address(self, tmp_path) -> None:
        # Defensive branch: validate_apply blocks a static-without-address at
        # the public boundary, so exercise the guard directly.
        from openfollow.network.adapter import Ipv4Config, Ipv4Method
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        a = DhcpcdAdapter(conf_path=tmp_path / "dhcpcd.conf")
        cfg = Ipv4Config(method=Ipv4Method.STATIC, address=None)
        assert a._verify_static_applied("eth0", cfg) is None


class TestStripBlockOrphan:
    """An end-marker-less orphan must be stripped so apply never
    appends a duplicate ``interface`` stanza."""

    def test_orphan_at_eof_is_removed(self) -> None:
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        text = "hostname\n# >>> openfollow managed: eth0 >>>\ninterface eth0\nstatic ip_address=10.0.0.5/24\n"
        result = DhcpcdAdapter._strip_block(text, "eth0")
        assert "managed: eth0" not in result
        assert "static ip_address" not in result
        assert "hostname" in result

    def test_orphan_start_marker_alone_is_removed(self) -> None:
        # Truncation cut right after the start marker: there is no own
        # ``interface`` line to consume, just drop the marker.
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        text = "hostname\n# >>> openfollow managed: eth0 >>>\n"
        result = DhcpcdAdapter._strip_block(text, "eth0")
        assert "managed: eth0" not in result
        assert result == "hostname\n"

    def test_orphan_stops_at_next_interface(self) -> None:
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        text = (
            "# >>> openfollow managed: eth0 >>>\n"
            "interface eth0\n"
            "static ip_address=10.0.0.5/24\n"
            "interface wlan0\n"
            "static ip_address=10.0.1.5/24\n"
        )
        result = DhcpcdAdapter._strip_block(text, "eth0")
        assert "managed: eth0" not in result
        assert "10.0.0.5" not in result
        assert "interface wlan0" in result  # unrelated stanza preserved
        assert "10.0.1.5" in result

    def test_orphan_stops_at_next_managed_marker(self) -> None:
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        text = (
            "# >>> openfollow managed: eth0 >>>\n"
            "interface eth0\n"
            "static ip_address=10.0.0.5/24\n"
            "# >>> openfollow managed: wlan0 >>>\n"
            "interface wlan0\n"
            "# <<< openfollow managed: wlan0 <<<\n"
        )
        result = DhcpcdAdapter._strip_block(text, "eth0")
        assert "managed: eth0" not in result
        assert "managed: wlan0" in result  # well-formed other block preserved

    def test_orphan_with_crlf_line_endings_is_removed(self) -> None:
        # A hand-edit saved with CRLF endings: the substring guard still
        # matches the start marker, but the per-line comparison must normalise
        # the trailing \r (via .strip()) or the orphan survives and apply
        # re-appends a duplicate ``interface`` stanza.
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        text = "hostname\r\n# >>> openfollow managed: eth0 >>>\r\ninterface eth0\r\nstatic ip_address=10.0.0.5/24\r\n"
        result = DhcpcdAdapter._strip_block(text, "eth0")
        assert "managed: eth0" not in result
        assert "static ip_address" not in result
        assert "hostname" in result

    def test_apply_over_orphan_does_not_duplicate_interface(self, tmp_path) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        conf.write_text(
            "# >>> openfollow managed: eth0 >>>\ninterface eth0\nstatic ip_address=10.0.0.5/24\n"
        )  # orphan: no end marker
        a = DhcpcdAdapter(conf_path=conf, broker=FakeBroker())
        a._read_lease = lambda iface: None  # type: ignore[assignment,method-assign]
        result = a.apply_ipv4(
            "eth0",
            Ipv4Config(method=Ipv4Method.STATIC, address="10.0.0.9", prefix=24, router="10.0.0.1"),
        )
        assert result.ok is True
        text = conf.read_text()
        assert text.count("interface eth0") == 1
        assert "10.0.0.5" not in text
        assert "10.0.0.9" in text


class TestApplyBoundaryValidation:
    """Re-validate iface + address/router/dns inside apply_ipv4."""

    @pytest.mark.parametrize("bad_iface", ["-eth0", "eth0; rm -rf /", "eth 0", "", "eth0\nstatic x=y"])
    def test_rejects_invalid_iface(self, tmp_path, bad_iface) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        conf.write_text("# orig\n")
        a = DhcpcdAdapter(conf_path=conf, broker=FakeBroker())
        result = a.apply_ipv4(bad_iface, Ipv4Config(method=Ipv4Method.DHCP))
        assert result.ok is False
        assert "interface name" in result.message
        # Conf untouched – validation runs before any write.
        assert conf.read_text() == "# orig\n"

    @pytest.mark.parametrize(
        "address",
        ["not-an-ip", "10.0.0.5\nstatic domain_name_servers=evil"],
    )
    def test_rejects_invalid_static_address(self, tmp_path, address) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        conf = tmp_path / "dhcpcd.conf"
        conf.write_text("")
        a = DhcpcdAdapter(conf_path=conf, broker=FakeBroker())
        result = a.apply_ipv4(
            "eth0",
            Ipv4Config(method=Ipv4Method.STATIC, address=address, prefix=24),
        )
        assert result.ok is False
        assert conf.read_text() == ""  # nothing written


class TestPendingOnADeadLink:
    """``dhcpcd -n`` returns 0 on a carrier-less interface - it only signals the
    daemon - so without an explicit pending state the apply reads as clean and
    the web layer redirects the browser to an address nothing is serving."""

    def test_apply_reports_pending_when_the_link_is_down(self, adapter, monkeypatch) -> None:
        a, _conf = adapter
        monkeypatch.setattr(a, "_has_carrier", lambda _iface: False)
        result = a.apply_ipv4(
            "eth0",
            Ipv4Config(method=Ipv4Method.STATIC, address="192.168.9.9", prefix=24),
        )
        assert result.ok is True
        assert result.pending is True
        assert "has a link" in result.message

    def test_apply_is_not_pending_with_a_link(self, adapter, monkeypatch) -> None:
        a, _conf = adapter
        monkeypatch.setattr(a, "_has_carrier", lambda _iface: True)
        result = a.apply_ipv4(
            "eth0",
            Ipv4Config(method=Ipv4Method.STATIC, address="192.168.9.9", prefix=24),
        )
        assert result.ok is True
        assert result.pending is False

    def test_carrier_reads_the_kernel_operstate(self, adapter, tmp_path, monkeypatch) -> None:
        a, _conf = adapter
        import openfollow.network.dhcpcd_adapter as mod

        class _Path:
            def __init__(self, value: str) -> None:
                self._value = value

            def read_text(self, encoding: str = "utf-8") -> str:
                return self._value

        monkeypatch.setattr(mod, "Path", lambda _p: _Path("down\n"))
        assert a._has_carrier("eth0") is False
        monkeypatch.setattr(mod, "Path", lambda _p: _Path("up\n"))
        assert a._has_carrier("eth0") is True

    def test_an_unreadable_operstate_counts_as_having_carrier(self, adapter, monkeypatch) -> None:
        """An apply we can't explain must never be downgraded to a reassuring
        "saved, pending"."""
        a, _conf = adapter
        import openfollow.network.dhcpcd_adapter as mod

        class _Missing:
            def read_text(self, encoding: str = "utf-8") -> str:
                raise OSError("no such file")

        monkeypatch.setattr(mod, "Path", lambda _p: _Missing())
        assert a._has_carrier("eth0") is True


class TestMessagesFitTheOnScreenBanner:
    """Every operator-facing message must be one sentence.

    The on-screen Settings banner is a single truncated line, so a second
    sentence is the half that gets cut - and it is reliably the actionable
    half, because the first sentence says what broke and the second says what
    to do about it. Asserting the shape here means a regression fails CI
    rather than waiting for someone to read it on a device.
    """

    # ". " before a capital is the two-sentence signature. It cannot match a
    # path like ``dhcpcd.conf`` or an address like ``192.168.1.5``, which is
    # why the check is not simply "contains a period".
    _SECOND_SENTENCE = re.compile(r"\.\s+[A-Z]")

    def _messages(self, broker: FakeBroker, tmp_path) -> list[str]:
        """Drive the failure paths and collect what the operator would see."""
        out: list[str] = []
        adapter = DhcpcdAdapter(conf_path=str(tmp_path / "dhcpcd.conf"), broker=broker)

        out.append(adapter.apply_ipv4("bad iface!", Ipv4Config(method=Ipv4Method.DHCP)).message)

        def _boom() -> str:
            raise OSError("read crashed")

        adapter._read_conf_for_write = _boom  # type: ignore[method-assign]
        out.append(adapter.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP)).message)
        return [m for m in out if m]

    def test_no_message_carries_a_second_sentence(self, broker: FakeBroker, tmp_path) -> None:
        offenders = [m for m in self._messages(broker, tmp_path) if self._SECOND_SENTENCE.search(m)]
        assert offenders == [], f"multi-sentence operator messages: {offenders}"

    def test_the_detector_would_catch_a_two_sentence_message(self) -> None:
        """Guards the guard: a check that never matches proves nothing."""
        assert self._SECOND_SENTENCE.search("Could not update the file. Nothing was changed.")
        assert not self._SECOND_SENTENCE.search("Could not update /etc/dhcpcd.conf; nothing was changed.")
        assert not self._SECOND_SENTENCE.search("Saved; eth0 is at 192.168.1.5 now.")


class TestVlansUnsupported:
    """dhcpcd configures addresses on links; it does not create them. The Pi
    image masks systemd-networkd, so NetworkManager owns link creation."""

    def test_reports_unsupported(self, broker: FakeBroker) -> None:
        assert DhcpcdAdapter(broker=broker).supports_vlans() is False

    def test_lists_nothing(self, broker: FakeBroker) -> None:
        assert DhcpcdAdapter(broker=broker).list_vlans() == []

    def test_create_refuses(self, broker: FakeBroker) -> None:
        result = DhcpcdAdapter(broker=broker).create_vlan("eth0", 10)
        assert result.ok is False
        assert result.message == VLAN_UNSUPPORTED_MESSAGE
        assert broker.calls == []

    def test_delete_refuses(self, broker: FakeBroker) -> None:
        result = DhcpcdAdapter(broker=broker).delete_vlan("eth0.10")
        assert result.ok is False
        assert result.message == VLAN_UNSUPPORTED_MESSAGE
        assert broker.calls == []


class TestSetDhcpFqdn:
    """The name goes into one managed block of global options, ``hostname`` plus ``fqdn both``
    (``hostname`` alone sends the whole name as option 12), and every interface rebinds so its
    DHCP server sees it now."""

    _FQDN = "of-1.stage.example.com"
    _BLOCK = (
        "# >>> openfollow managed: station fqdn >>>\n"
        "hostname of-1.stage.example.com\n"
        "fqdn both\n"
        "# <<< openfollow managed: station fqdn <<<\n"
    )

    @pytest.fixture(autouse=True)
    def _interfaces(self, monkeypatch):
        from openfollow.network import psutil_adapter as pa
        from openfollow.network.adapter import NetworkInterface

        class FakePsutil:
            def list_interfaces(self):
                return [
                    NetworkInterface(name="lo", mac=None, kind=None, is_up=True),
                    NetworkInterface(name="eth0", mac=None, kind=None, is_up=True),
                    NetworkInterface(name="wlan0", mac=None, kind=None, is_up=False),
                ]

        monkeypatch.setattr(pa, "PsutilReadOnlyAdapter", FakePsutil)

    def test_the_name_closes_the_global_options_and_every_interface_rebinds(self, adapter, broker) -> None:
        """Global options stop at the first section, and a block the station manages starts at its marker."""
        a, conf = adapter
        stock = (
            "# stock\nhostname\nclientid\n\n"
            "# >>> openfollow managed: eth0 >>>\ninterface eth0\n# <<< openfollow managed: eth0 <<<\n"
        )
        conf.write_text(stock)
        result = a.set_dhcp_fqdn(self._FQDN)
        assert (result.ok, result.partial_failures) == (True, ())
        assert conf.read_text() == stock.replace(
            "# >>> openfollow managed: eth0", self._BLOCK + "\n# >>> openfollow managed: eth0"
        )
        assert [call.argv for call in broker.calls] == [
            ["/usr/sbin/dhcpcd", "-n", "eth0"],
            ["/usr/sbin/dhcpcd", "-n", "wlan0"],
        ]

    def test_an_operator_section_keeps_its_place_after_the_name(self, adapter) -> None:
        a, conf = adapter
        conf.write_text("hostname\n\ninterface wlan0\nhostname mine.example.com\n")
        a.set_dhcp_fqdn(self._FQDN)
        assert conf.read_text() == "hostname\n\n" + self._BLOCK + "\ninterface wlan0\nhostname mine.example.com\n"

    @pytest.mark.parametrize(
        "original",
        [
            "# stock\nhostname\n\n"
            "# >>> openfollow managed: eth0 >>>\ninterface eth0\n# <<< openfollow managed: eth0 <<<\n",
            "hostname\n\ninterface wlan0\n",
            "hostname\nclientid",
            "",
        ],
        ids=["managed-section", "operator-section", "no-final-newline", "empty"],
    )
    def test_a_new_name_replaces_the_old_and_a_blank_one_gives_the_file_back(self, adapter, original: str) -> None:
        a, conf = adapter
        conf.write_text(original)
        a.set_dhcp_fqdn(self._FQDN)
        a.set_dhcp_fqdn("other.example.com")
        text = conf.read_text()
        assert text.count("openfollow managed: station fqdn >>>") == 1
        assert "hostname other.example.com\n" in text and self._FQDN not in text
        a.set_dhcp_fqdn("")
        assert conf.read_text() == original + ("\n" if original and not original.endswith("\n") else "")

    def test_an_orphaned_start_marker_is_replaced_not_duplicated(self, adapter) -> None:
        """A hand edit that lost the end marker must not leave a second ``hostname`` behind."""
        a, conf = adapter
        conf.write_text(
            "# >>> openfollow managed: station fqdn >>>\nhostname old.example.com\nfqdn both\ninterface eth0\n"
        )
        a.set_dhcp_fqdn(self._FQDN)
        assert conf.read_text() == self._BLOCK + "\ninterface eth0\n"

    def test_an_interface_that_fails_to_rebind_is_reported(self, adapter, broker) -> None:
        a, _ = adapter
        broker.exceptions = [make_failure("dhcpcd not running")]
        result = a.set_dhcp_fqdn(self._FQDN)
        assert result.ok
        assert result.partial_failures == ("eth0 could not be reconnected (dhcpcd not running).",)

    def test_a_conf_that_cannot_be_written_changes_nothing_else(self, adapter, broker, monkeypatch) -> None:
        a, _ = adapter

        def _refuse(text: str, **_kw) -> None:
            raise OSError("read-only file system")

        monkeypatch.setattr(a, "_write_conf_privileged", _refuse)
        result = a.set_dhcp_fqdn(self._FQDN)
        assert not result.ok
        assert "is not mounted read-only (read-only file system)" in result.message
        assert broker.calls == []

    def test_a_refused_conf_write_is_reported(self, adapter, broker, monkeypatch) -> None:
        a, _ = adapter

        def _refuse(text: str, **_kw) -> None:
            raise PrivilegeError("sudo: a password is required")

        monkeypatch.setattr(a, "_write_conf_privileged", _refuse)
        result = a.set_dhcp_fqdn(self._FQDN)
        assert (result.ok, result.message) == (False, "sudo: a password is required")

    def test_without_the_privileged_helper_nothing_rebinds(self, tmp_path) -> None:
        conf = tmp_path / "dhcpcd.conf"
        conf.write_text("hostname\n")
        result = DhcpcdAdapter(conf_path=conf).set_dhcp_fqdn(self._FQDN)
        assert not result.ok
        assert "privileged helper is not configured" in result.message

    @pytest.mark.parametrize("fqdn", ["of-2.stage.example.com", ""], ids=["renamed", "cleared"])
    def test_a_block_missing_its_end_marker_takes_only_its_own_lines(self, adapter, fqdn: str) -> None:
        """Truncated or hand-edited, the block's marker and its directives go; the global
        options an operator wrote after it stay."""
        a, conf = adapter
        conf.write_text(
            "# stock\n"
            "# >>> openfollow managed: station fqdn >>>\n"
            "hostname old.example.com\n"
            "fqdn both\n"
            "clientid\n"
            "option ntp_servers\n"
            "\n"
            "interface eth0\n"
        )
        a.set_dhcp_fqdn(fqdn, reconnect=False)
        text = conf.read_text()
        assert "clientid\noption ntp_servers\n" in text
        assert "old.example.com" not in text
        assert text.count("openfollow managed: station fqdn >>>") == (1 if fqdn else 0)
        assert text.endswith("interface eth0\n")

    def test_an_unchanged_name_still_rebinds(self, adapter, broker) -> None:
        """Saving the same name again is how an operator asks for it to be sent again."""
        a, conf = adapter
        conf.write_text(self._BLOCK)
        a.set_dhcp_fqdn(self._FQDN)
        assert conf.read_text() == self._BLOCK
        assert len(broker.calls) == 2


class TestSetDhcpFqdnInTheBackground:
    _FQDN = "of-1.stage.example.com"

    @pytest.fixture(autouse=True)
    def _interfaces(self, monkeypatch):
        from openfollow.network import psutil_adapter as pa
        from openfollow.network.adapter import NetworkInterface

        class FakePsutil:
            def list_interfaces(self):
                return [NetworkInterface(name="eth0", mac=None, kind=None, is_up=True)]

        monkeypatch.setattr(pa, "PsutilReadOnlyAdapter", FakePsutil)

    def test_it_never_asks_for_a_password(self, adapter, broker, monkeypatch) -> None:
        a, conf = adapter
        writes: list[bool] = []
        monkeypatch.setattr(a, "_write_conf_privileged", lambda text, *, allow_prompt=True: writes.append(allow_prompt))
        a.set_dhcp_fqdn(self._FQDN)
        assert writes == [False]
        assert [call.allow_prompt for call in broker.calls] == [False]

    @pytest.mark.parametrize("write", ["fqdn", "fqdn-at-startup", "ipv4"])
    def test_an_unreadable_conf_is_never_rewritten(self, tmp_path, broker, monkeypatch, write: str) -> None:
        """Read as empty, it would be replaced by the managed blocks alone, at startup unasked."""
        conf = tmp_path / "dhcpcd.conf"
        conf.mkdir()
        a = DhcpcdAdapter(conf_path=conf, broker=broker)
        writes: list[str] = []
        monkeypatch.setattr(a, "_write_conf_privileged", lambda text, **_kw: writes.append(text))
        if write == "ipv4":
            result = a.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        else:
            result = a.set_dhcp_fqdn(self._FQDN, reconnect=write == "fqdn")
        assert not result.ok
        assert "nothing was changed" in result.message
        assert (writes, broker.calls) == ([], [])

    def test_a_missing_conf_gets_the_block_alone(self, tmp_path, broker, monkeypatch) -> None:
        a = DhcpcdAdapter(conf_path=tmp_path / "dhcpcd.conf", broker=broker)
        writes: list[str] = []
        monkeypatch.setattr(a, "_write_conf_privileged", lambda text, **_kw: writes.append(text))
        assert a.set_dhcp_fqdn(self._FQDN, reconnect=False).ok
        assert writes == [TestSetDhcpFqdn._BLOCK]

    def test_reconciling_writes_the_block_and_rebinds_nothing(self, adapter, broker) -> None:
        a, conf = adapter
        result = a.set_dhcp_fqdn(self._FQDN, reconnect=False)
        assert (result.ok, result.message) == (True, "Applied.")
        assert "hostname of-1.stage.example.com\nfqdn both\n" in conf.read_text()
        assert broker.calls == []
        assert a.set_dhcp_fqdn(self._FQDN, reconnect=False).message == "Unchanged."


_STATIC_BLOCK = (
    "# >>> openfollow managed: eth0 >>>\ninterface eth0\nstatic ip_address=198.51.100.5/24\n"
    "# <<< openfollow managed: eth0 <<<\n"
    "# >>> openfollow managed: eth2 >>>\ninterface eth2\ninform 203.0.113.9\n# <<< openfollow managed: eth2 <<<\n"
)


class TestReadAddressSources:
    """The diagnostics read: where each address came from, or why it cannot say."""

    @pytest.fixture
    def station(self, tmp_path, monkeypatch):
        from openfollow.network import psutil_adapter

        names = ["lo", "eth0", "eth1", "eth2", "eth3"]
        monkeypatch.setattr(psutil_adapter.psutil, "net_if_addrs", lambda: {n: [] for n in names})
        monkeypatch.setattr(psutil_adapter.psutil, "net_if_stats", lambda: {})
        conf = tmp_path / "dhcpcd.conf"
        conf.write_text(_STATIC_BLOCK)
        a = DhcpcdAdapter(conf_path=conf)
        leases = {"eth1": "ip_address=192.0.2.10\nsubnet_cidr=24\n", "eth3": ""}
        dumped: list[str] = []

        def _run(argv, *, check=True):
            dumped.append(argv[-1])
            out = leases.get(argv[-1], "")
            return subprocess.CompletedProcess(argv, 0 if out else 1, out, "")

        a._run = _run  # type: ignore[method-assign]
        return a, conf, dumped

    def test_reads_the_method_from_the_conf_and_a_dhcp_address_from_the_lease(self, station) -> None:
        from openfollow.network.adapter import AddressSourceReading

        a, _conf, dumped = station
        assert a.read_address_sources() == [
            AddressSourceReading("eth0", "198.51.100.5", "static"),
            AddressSourceReading("eth1", "192.0.2.10", "dhcp"),
            AddressSourceReading("eth2", "203.0.113.9", "static"),
            AddressSourceReading("eth3", "", "none"),
        ]
        # A configured address needs no lease to name it.
        assert dumped == ["eth1", "eth3"]

    def test_an_unreadable_conf_is_reported_where_the_panel_says_dhcp(self, station) -> None:
        a, conf, _dumped = station
        conf.unlink()
        conf.mkdir()
        readings = a.read_address_sources()
        assert [(r.name, r.source) for r in readings] == [(n, "unreadable") for n in ("eth0", "eth1", "eth2", "eth3")]
        assert all(r.reason == f"could not read {conf}: Is a directory" for r in readings)
        state = a.get_state("eth0")
        assert state is not None
        assert state.ipv4.method == Ipv4Method.DHCP

    @pytest.mark.parametrize("error", [FileNotFoundError("dhcpcd"), subprocess.TimeoutExpired(["dhcpcd"], 8)])
    def test_a_lease_that_cannot_be_asked_for_is_reported_where_the_panel_shows_no_address(
        self, station, error
    ) -> None:
        a, _conf, _dumped = station

        def _run(argv, *, check=True):
            raise error

        a._run = _run  # type: ignore[method-assign]
        sources = {r.name: (r.source, r.reason) for r in a.read_address_sources()}
        assert sources["eth0"] == ("static", "")
        assert sources["eth1"] == ("unreadable", str(error))
        state = a.get_state("eth1")
        assert state is not None
        assert state.address_source == "none"

    def test_an_interface_list_that_cannot_be_read_raises_where_the_panel_lists_nothing(
        self, station, monkeypatch
    ) -> None:
        from openfollow.network import psutil_adapter
        from openfollow.network.adapter import BackendReadError

        a, _conf, _dumped = station
        monkeypatch.setattr(psutil_adapter.psutil, "net_if_stats", lambda: 1 / 0)
        with pytest.raises(BackendReadError, match="ZeroDivisionError"):
            a.read_address_sources()
        assert a.list_interfaces() == []
