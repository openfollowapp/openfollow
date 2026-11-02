# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann, Michel Honold, and Vinzenz Schultz
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""Tests for the station hostname and /etc/hosts sync."""

from __future__ import annotations

from pathlib import Path

import pytest

from openfollow.privilege.capabilities import (
    DEVICE_HOSTS_WRITE,
    DEVICE_SET_HOSTNAME,
    CapabilityState,
)
from openfollow.privilege.device_repair import (
    current_hostname,
    ensure_loopback_hosts_line,
    full_hostname,
    sync_etc_hosts,
    sync_station_hostname,
)
from tests._fake_broker import FakeBroker, make_failure

pytestmark = pytest.mark.unit


class TestSyncStationHostname:
    """``sync_station_hostname`` self-names the device from its station id."""

    @staticmethod
    def _arrange(monkeypatch, *, current: str, hostnamectl: str | None = "/usr/bin/hostnamectl") -> None:
        monkeypatch.setattr("openfollow.privilege.device_repair.current_hostname", lambda: current)
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.shutil.which",
            lambda name: hostnamectl,
        )
        # Keep the hostname tests hermetic: point /etc/hosts at a path that
        # can't be read so the follow-on sync_etc_hosts is a silent no-op and
        # we never touch the host's real /etc/hosts. The /etc/hosts behaviour
        # is exercised directly in TestSyncEtcHosts.
        monkeypatch.setattr(
            "openfollow.privilege.device_repair._ETC_HOSTS",
            Path("/openfollow-nonexistent/etc/hosts"),
        )

    def test_sets_hostname_when_passwordless_and_different(self, monkeypatch) -> None:
        self._arrange(monkeypatch, current="openfollow")
        broker = FakeBroker()
        changed = sync_station_hostname(broker, "OpenFollow noble-bear")
        assert changed is True
        assert len(broker.calls) == 1
        call = broker.calls[0]
        assert call.capability is DEVICE_SET_HOSTNAME
        assert call.argv == ["/usr/bin/hostnamectl", "set-hostname", "openfollow-noble-bear"]
        # A passwordless verdict can go stale; the rename then fails rather than waits on a prompt.
        assert call.allow_prompt is False

    def test_noop_when_hostname_already_matches(self, monkeypatch) -> None:
        self._arrange(monkeypatch, current="openfollow-noble-bear")
        broker = FakeBroker()
        assert sync_station_hostname(broker, "OpenFollow noble-bear") is False
        assert broker.calls == []

    def test_noop_when_slug_is_empty(self, monkeypatch) -> None:
        self._arrange(monkeypatch, current="openfollow")
        broker = FakeBroker()
        assert sync_station_hostname(broker, "***") is False
        assert broker.calls == []

    def test_noop_when_hostnamectl_missing(self, monkeypatch) -> None:
        self._arrange(monkeypatch, current="openfollow", hostnamectl=None)
        broker = FakeBroker()
        assert sync_station_hostname(broker, "OpenFollow noble-bear") is False
        assert broker.calls == []

    def test_does_not_prompt_when_grant_needs_password(self, monkeypatch) -> None:
        """A cosmetic hostname change must never pop a password prompt: when
        the grant isn't passwordless the function bails before calling run."""
        self._arrange(monkeypatch, current="openfollow")
        broker = FakeBroker(states_map={DEVICE_SET_HOSTNAME.name: CapabilityState.NEEDS_PASSWORD})
        assert sync_station_hostname(broker, "OpenFollow noble-bear") is False
        assert broker.calls == []

    def test_swallows_broker_failure(self, monkeypatch) -> None:
        self._arrange(monkeypatch, current="openfollow")
        broker = FakeBroker(exceptions=[make_failure("hostnamectl blew up")])
        assert sync_station_hostname(broker, "OpenFollow noble-bear") is False
        assert len(broker.calls) == 1

    def test_also_syncs_etc_hosts_after_rename(self, monkeypatch, tmp_path) -> None:
        """After the hostname is set, the 127.0.1.1 line in /etc/hosts is
        rewritten too so ``sudo`` can resolve the new name."""
        self._arrange(monkeypatch, current="openfollow")
        hosts = tmp_path / "hosts"
        hosts.write_text("127.0.0.1\tlocalhost\n")
        monkeypatch.setattr("openfollow.privilege.device_repair._ETC_HOSTS", hosts)
        broker = FakeBroker()
        assert sync_station_hostname(broker, "OpenFollow noble-bear") is True
        assert [c.capability for c in broker.calls] == [DEVICE_SET_HOSTNAME, DEVICE_HOSTS_WRITE]
        hosts_call = broker.calls[1]
        assert hosts_call.argv == ["/usr/bin/tee", str(hosts)]
        assert "127.0.1.1\topenfollow-noble-bear" in hosts_call.stdin

    def test_syncs_etc_hosts_when_hostname_already_matches(self, monkeypatch, tmp_path) -> None:
        """Fresh-image case: hostname is already correct but /etc/hosts still
        carries a stale 127.0.1.1 line – repair it without a rename. No
        hostnamectl call (returns False), but /etc/hosts IS rewritten."""
        self._arrange(monkeypatch, current="openfollow-noble-bear")
        hosts = tmp_path / "hosts"
        hosts.write_text("127.0.1.1\topenfollow\n127.0.0.1\tlocalhost\n")
        monkeypatch.setattr("openfollow.privilege.device_repair._ETC_HOSTS", hosts)
        broker = FakeBroker()
        assert sync_station_hostname(broker, "OpenFollow noble-bear") is False
        assert [c.capability for c in broker.calls] == [DEVICE_HOSTS_WRITE]
        assert "127.0.1.1\topenfollow-noble-bear" in broker.calls[0].stdin

    @pytest.mark.parametrize("current", ["openfollow", "openfollow-noble-bear"], ids=["renamed", "already-named"])
    def test_the_hosts_line_carries_the_station_fqdn(self, monkeypatch, tmp_path, current: str) -> None:
        self._arrange(monkeypatch, current=current)
        hosts = tmp_path / "hosts"
        hosts.write_text("127.0.0.1\tlocalhost\n")
        monkeypatch.setattr("openfollow.privilege.device_repair._ETC_HOSTS", hosts)
        broker = FakeBroker()
        sync_station_hostname(broker, "OpenFollow noble-bear", "of-1.stage.example.com")
        hosts_call = next(c for c in broker.calls if c.capability is DEVICE_HOSTS_WRITE)
        assert "127.0.1.1\tof-1.stage.example.com openfollow-noble-bear\n" in hosts_call.stdin


class TestEnsureLoopbackHostsLine:
    """``ensure_loopback_hosts_line`` keeps exactly one 127.0.1.1 mapping."""

    def test_replaces_existing_loopback_line(self) -> None:
        text = "127.0.0.1\tlocalhost\n127.0.1.1\told-name\n::1\tip6-localhost\n"
        result = ensure_loopback_hosts_line(text, "new-name")
        assert result == "127.0.0.1\tlocalhost\n127.0.1.1\tnew-name\n::1\tip6-localhost\n"

    def test_drops_duplicate_loopback_lines(self) -> None:
        text = "127.0.1.1\ta\n127.0.1.1\tb\n"
        result = ensure_loopback_hosts_line(text, "host")
        assert result == "127.0.1.1\thost\n"

    def test_inserts_after_localhost_when_absent(self) -> None:
        text = "127.0.0.1\tlocalhost\n::1\tip6-localhost\n"
        result = ensure_loopback_hosts_line(text, "host")
        assert result.splitlines() == ["127.0.0.1\tlocalhost", "127.0.1.1\thost", "::1\tip6-localhost"]

    def test_appends_when_neither_present(self) -> None:
        text = "::1\tip6-localhost\n"
        result = ensure_loopback_hosts_line(text, "host")
        assert result == "::1\tip6-localhost\n127.0.1.1\thost\n"

    def test_appends_to_empty_text(self) -> None:
        assert ensure_loopback_hosts_line("", "host") == "127.0.1.1\thost\n"

    def test_result_always_ends_with_newline(self) -> None:
        assert ensure_loopback_hosts_line("127.0.0.1 localhost", "host").endswith("\n")


class TestTheLoopbackLineCarriesTheStationFqdn:
    """The FQDN comes first, as the canonical name, with the short hostname as its alias."""

    def test_the_fqdn_leads_the_line(self) -> None:
        text = "127.0.0.1\tlocalhost\n127.0.1.1\topenfollow-noble-bear\n"
        assert ensure_loopback_hosts_line(text, "openfollow-noble-bear", "of-1.stage.example.com") == (
            "127.0.0.1\tlocalhost\n127.0.1.1\tof-1.stage.example.com openfollow-noble-bear\n"
        )

    def test_a_cleared_fqdn_leaves_the_hostname_alone(self) -> None:
        text = "127.0.1.1\tof-1.stage.example.com openfollow-noble-bear\n"
        assert ensure_loopback_hosts_line(text, "openfollow-noble-bear", "") == "127.0.1.1\topenfollow-noble-bear\n"

    def test_sync_writes_the_fqdn_and_is_then_settled(self, monkeypatch, tmp_path) -> None:
        hosts = tmp_path / "hosts"
        hosts.write_text("127.0.0.1\tlocalhost\n127.0.1.1\topenfollow-noble-bear\n")
        monkeypatch.setattr("openfollow.privilege.device_repair._ETC_HOSTS", hosts)
        broker = FakeBroker()
        assert sync_etc_hosts(broker, "openfollow-noble-bear", "of-1.stage.example.com") is True
        assert (
            broker.calls[0].stdin == "127.0.0.1\tlocalhost\n127.0.1.1\tof-1.stage.example.com openfollow-noble-bear\n"
        )
        hosts.write_text(broker.calls[0].stdin)
        assert sync_etc_hosts(broker, "openfollow-noble-bear", "of-1.stage.example.com") is False


class TestSyncEtcHosts:
    """``sync_etc_hosts`` rewrites the 127.0.1.1 line via the privileged tee."""

    def test_writes_when_line_missing_and_passwordless(self, monkeypatch, tmp_path) -> None:
        hosts = tmp_path / "hosts"
        hosts.write_text("127.0.0.1\tlocalhost\n")
        monkeypatch.setattr("openfollow.privilege.device_repair._ETC_HOSTS", hosts)
        broker = FakeBroker()
        assert sync_etc_hosts(broker, "openfollow-noble-bear") is True
        assert len(broker.calls) == 1
        call = broker.calls[0]
        assert call.capability is DEVICE_HOSTS_WRITE
        assert call.argv == ["/usr/bin/tee", str(hosts)]
        assert call.stdin == "127.0.0.1\tlocalhost\n127.0.1.1\topenfollow-noble-bear\n"
        # Startup and the FQDN worker both write here; the worker holds the network lock meanwhile.
        assert call.allow_prompt is False

    def test_noop_when_already_correct(self, monkeypatch, tmp_path) -> None:
        hosts = tmp_path / "hosts"
        hosts.write_text("127.0.0.1\tlocalhost\n127.0.1.1\topenfollow-noble-bear\n")
        monkeypatch.setattr("openfollow.privilege.device_repair._ETC_HOSTS", hosts)
        broker = FakeBroker()
        assert sync_etc_hosts(broker, "openfollow-noble-bear") is False
        assert broker.calls == []

    def test_noop_when_file_unreadable(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair._ETC_HOSTS",
            Path("/openfollow-nonexistent/etc/hosts"),
        )
        broker = FakeBroker()
        assert sync_etc_hosts(broker, "host") is False
        assert broker.calls == []

    def test_noop_when_file_not_utf8(self, monkeypatch, tmp_path) -> None:
        """A non-UTF-8 /etc/hosts raises UnicodeDecodeError (a ValueError, not an
        OSError) on read – it must be swallowed, not crash startup."""
        hosts = tmp_path / "hosts"
        hosts.write_bytes(b"\xff\xfe 127.0.0.1 localhost\n")
        monkeypatch.setattr("openfollow.privilege.device_repair._ETC_HOSTS", hosts)
        broker = FakeBroker()
        assert sync_etc_hosts(broker, "host") is False
        assert broker.calls == []

    def test_noop_when_grant_needs_password(self, monkeypatch, tmp_path) -> None:
        hosts = tmp_path / "hosts"
        hosts.write_text("127.0.0.1\tlocalhost\n")
        monkeypatch.setattr("openfollow.privilege.device_repair._ETC_HOSTS", hosts)
        broker = FakeBroker(states_map={DEVICE_HOSTS_WRITE.name: CapabilityState.NEEDS_PASSWORD})
        assert sync_etc_hosts(broker, "host") is False
        assert broker.calls == []

    def test_swallows_broker_failure(self, monkeypatch, tmp_path) -> None:
        hosts = tmp_path / "hosts"
        hosts.write_text("127.0.0.1\tlocalhost\n")
        monkeypatch.setattr("openfollow.privilege.device_repair._ETC_HOSTS", hosts)
        broker = FakeBroker(exceptions=[make_failure("tee blew up")])
        assert sync_etc_hosts(broker, "host") is False
        assert len(broker.calls) == 1


def test_current_hostname_returns_short_form() -> None:
    """The real ``current_hostname()`` returns the short (un-qualified) name."""
    result = current_hostname()
    assert isinstance(result, str)
    assert "." not in result


def test_full_hostname_keeps_the_domain_and_survives_an_oserror(monkeypatch) -> None:
    monkeypatch.setattr("openfollow.privilege.device_repair.socket.gethostname", lambda: "box.example.lan")
    assert full_hostname() == "box.example.lan"
    assert current_hostname() == "box"

    def boom() -> str:
        raise OSError("no hostname")

    monkeypatch.setattr("openfollow.privilege.device_repair.socket.gethostname", boom)
    assert full_hostname() == ""
    assert current_hostname() == ""


class TestSyncRunningHostnameHosts:
    """The FQDN worker maps the hostname the system has now, read under the same lock a rename takes."""

    def test_the_running_hostname_follows_the_fqdn(self, monkeypatch, tmp_path) -> None:
        from openfollow.privilege.device_repair import sync_running_hostname_hosts

        hosts = tmp_path / "hosts"
        hosts.write_text("127.0.0.1\tlocalhost\n127.0.1.1\topenfollow-noble-bear\n")
        monkeypatch.setattr("openfollow.privilege.device_repair._ETC_HOSTS", hosts)
        monkeypatch.setattr("openfollow.privilege.device_repair.current_hostname", lambda: "openfollow-noble-bear")
        broker = FakeBroker()
        assert sync_running_hostname_hosts(broker, "of-1.stage.example.com") is True
        assert "127.0.1.1\tof-1.stage.example.com openfollow-noble-bear\n" in broker.calls[0].stdin

    def test_a_host_with_no_name_writes_nothing(self, monkeypatch, tmp_path) -> None:
        from openfollow.privilege.device_repair import sync_running_hostname_hosts

        hosts = tmp_path / "hosts"
        hosts.write_text("127.0.0.1\tlocalhost\n")
        monkeypatch.setattr("openfollow.privilege.device_repair._ETC_HOSTS", hosts)
        monkeypatch.setattr("openfollow.privilege.device_repair.current_hostname", lambda: "")
        broker = FakeBroker()
        assert sync_running_hostname_hosts(broker, "of-1.stage.example.com") is False
        assert broker.calls == []


@pytest.mark.parametrize("entry", ["rename", "fqdn"])
def test_both_hosts_writers_read_the_hostname_under_one_lock(monkeypatch, tmp_path, entry: str) -> None:
    """A rename and an FQDN change each read the running hostname and rewrite /etc/hosts; reading
    it outside the shared lock let the slower one write back a name already gone."""
    import openfollow.privilege.device_repair as device_repair

    held: list[bool] = []

    def _hostname() -> str:
        held.append(device_repair._HOSTS_LOCK.locked())
        return "openfollow-noble-bear"

    monkeypatch.setattr(device_repair, "current_hostname", _hostname)
    monkeypatch.setattr(device_repair, "_ETC_HOSTS", tmp_path / "missing-hosts")
    if entry == "rename":
        device_repair.sync_station_hostname(FakeBroker(), "OpenFollow noble-bear", "of-1.stage.example.com")
    else:
        device_repair.sync_running_hostname_hosts(FakeBroker(), "of-1.stage.example.com")
    assert held == [True]
    assert not device_repair._HOSTS_LOCK.locked()
