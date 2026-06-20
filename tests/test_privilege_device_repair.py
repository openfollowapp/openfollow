# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the Device Setup Repair probes + appliers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from openfollow.privilege.capabilities import (
    DEVICE_GROUP_JOIN,
    DEVICE_HOSTS_WRITE,
    DEVICE_LINGER_ENABLE,
    DEVICE_SET_HOSTNAME,
    JOURNAL_GROUP_JOIN,
    SERVICE_DISABLE,
    SERVICE_ENABLE,
    SERVICE_MASK,
    CapabilityState,
)
from openfollow.privilege.device_repair import (
    BOOT_DELAY_SERVICES,
    REQUIRED_GROUPS,
    all_actions,
    apply_disable_boot_delay,
    apply_enable_seatd,
    apply_hardware_groups,
    apply_journal_group,
    apply_linger,
    apply_mask_getty,
    current_hostname,
    ensure_loopback_hosts_line,
    probe_all_boot_delay_disabled,
    probe_hardware_groups,
    probe_journal_group,
    probe_linger,
    probe_service_disabled,
    probe_service_masked,
    sync_etc_hosts,
    sync_station_hostname,
)
from tests._fake_broker import FakeBroker, make_failure

pytestmark = pytest.mark.unit


def _try_getgrnam(grp_module, name):
    """Return ``grp.getgrnam(name)`` or ``None`` on KeyError.

    Used to find a known-present group on the current host without
    raising – host-specific (Linux has ``root``, macOS has ``wheel`` /
    ``staff``), so we probe a few candidates and pick whichever the
    CI worker has."""
    try:
        return grp_module.getgrnam(name)
    except KeyError:
        return None


# ---------- probes ----------------------------------------------------------


class TestProbeHardwareGroups:
    def test_all_present_returns_true(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair._group_exists",
            lambda g: True,
        )
        monkeypatch.setattr(
            "openfollow.privilege.device_repair._user_in_group",
            lambda user, group: True,
        )
        assert probe_hardware_groups("alice") is True

    def test_missing_group_returns_false(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair._group_exists",
            lambda g: True,
        )
        monkeypatch.setattr(
            "openfollow.privilege.device_repair._user_in_group",
            lambda user, group: group != "video",
        )
        assert probe_hardware_groups("alice") is False

    def test_nonexistent_groups_are_ignored(self, monkeypatch) -> None:
        """Groups missing on this host (e.g. ``render`` on a non-Wayland
        container) are skipped – they aren't fixable, so reporting
        them as 'needs repair' would confuse the operator."""
        monkeypatch.setattr(
            "openfollow.privilege.device_repair._group_exists",
            lambda g: g != "render",
        )
        monkeypatch.setattr(
            "openfollow.privilege.device_repair._user_in_group",
            lambda user, group: True,
        )
        assert probe_hardware_groups("alice") is True


class TestUserInGroup:
    """_user_in_group detects membership via primary GID, not just supplemental gr_mem list."""

    def test_membership_via_primary_gid(self, monkeypatch) -> None:
        from types import SimpleNamespace

        from openfollow.privilege import device_repair

        monkeypatch.setattr(
            device_repair.grp,
            "getgrnam",
            lambda g: SimpleNamespace(gr_gid=42, gr_mem=[]),
        )
        monkeypatch.setattr(
            device_repair.pwd,
            "getpwnam",
            lambda u: SimpleNamespace(pw_gid=42),
        )
        # ``getgrouplist`` isn't reached because ``pw_gid == target_gid``
        # short-circuits to True.
        assert device_repair._user_in_group("alice", "video") is True

    def test_membership_via_supplemental_through_getgrouplist(self, monkeypatch) -> None:
        from types import SimpleNamespace

        from openfollow.privilege import device_repair

        monkeypatch.setattr(
            device_repair.grp,
            "getgrnam",
            lambda g: SimpleNamespace(gr_gid=99, gr_mem=["alice"]),
        )
        monkeypatch.setattr(
            device_repair.pwd,
            "getpwnam",
            lambda u: SimpleNamespace(pw_gid=1000),  # primary != target
        )
        monkeypatch.setattr(
            device_repair.os,
            "getgrouplist",
            lambda user, gid: [gid, 99, 1234],
        )
        assert device_repair._user_in_group("alice", "video") is True

    def test_non_membership_when_getgrouplist_omits(self, monkeypatch) -> None:
        from types import SimpleNamespace

        from openfollow.privilege import device_repair

        monkeypatch.setattr(
            device_repair.grp,
            "getgrnam",
            lambda g: SimpleNamespace(gr_gid=99, gr_mem=[]),
        )
        monkeypatch.setattr(
            device_repair.pwd,
            "getpwnam",
            lambda u: SimpleNamespace(pw_gid=1000),
        )
        monkeypatch.setattr(
            device_repair.os,
            "getgrouplist",
            lambda user, gid: [gid],  # only primary, not target
        )
        assert device_repair._user_in_group("alice", "video") is False

    def test_missing_group_returns_false(self, monkeypatch) -> None:
        from openfollow.privilege import device_repair

        def _missing(_):
            raise KeyError("video")

        monkeypatch.setattr(device_repair.grp, "getgrnam", _missing)
        assert device_repair._user_in_group("alice", "video") is False

    def test_missing_user_returns_false(self, monkeypatch) -> None:
        from types import SimpleNamespace

        from openfollow.privilege import device_repair

        monkeypatch.setattr(
            device_repair.grp,
            "getgrnam",
            lambda g: SimpleNamespace(gr_gid=99, gr_mem=[]),
        )

        def _missing(_):
            raise KeyError("alice")

        monkeypatch.setattr(device_repair.pwd, "getpwnam", _missing)
        assert device_repair._user_in_group("alice", "video") is False

    def test_falls_back_to_gr_mem_when_getgrouplist_fails(self, monkeypatch) -> None:
        from types import SimpleNamespace

        from openfollow.privilege import device_repair

        monkeypatch.setattr(
            device_repair.grp,
            "getgrnam",
            lambda g: SimpleNamespace(gr_gid=99, gr_mem=["alice"]),
        )
        monkeypatch.setattr(
            device_repair.pwd,
            "getpwnam",
            lambda u: SimpleNamespace(pw_gid=1000),
        )

        def _boom(*_a, **_kw):
            raise OSError("nss unavailable")

        monkeypatch.setattr(device_repair.os, "getgrouplist", _boom)
        assert device_repair._user_in_group("alice", "video") is True

    def test_fallback_to_gr_mem_user_not_listed(self, monkeypatch) -> None:
        from types import SimpleNamespace

        from openfollow.privilege import device_repair

        monkeypatch.setattr(
            device_repair.grp,
            "getgrnam",
            lambda g: SimpleNamespace(gr_gid=99, gr_mem=["bob"]),
        )
        monkeypatch.setattr(
            device_repair.pwd,
            "getpwnam",
            lambda u: SimpleNamespace(pw_gid=1000),
        )

        def _boom(*_a, **_kw):
            raise OSError("nss unavailable")

        monkeypatch.setattr(device_repair.os, "getgrouplist", _boom)
        assert device_repair._user_in_group("alice", "video") is False

    def test_fallback_getgrnam_keyerror_on_retry(self, monkeypatch) -> None:
        """Defensive: if ``grp.getgrnam`` raises ``KeyError`` on the
        fallback retry (group deleted between the first lookup and the
        retry), return False instead of propagating."""
        from types import SimpleNamespace

        from openfollow.privilege import device_repair

        call_count = {"n": 0}

        def _flaky(_):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return SimpleNamespace(gr_gid=99, gr_mem=["alice"])
            raise KeyError("video")

        monkeypatch.setattr(device_repair.grp, "getgrnam", _flaky)
        monkeypatch.setattr(
            device_repair.pwd,
            "getpwnam",
            lambda u: SimpleNamespace(pw_gid=1000),
        )

        def _boom(*_a, **_kw):
            raise OSError("nss unavailable")

        monkeypatch.setattr(device_repair.os, "getgrouplist", _boom)
        assert device_repair._user_in_group("alice", "video") is False


class TestProbeJournalGroup:
    def test_group_absent_returns_true(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair._group_exists",
            lambda g: False,
        )
        # No group on this host = nothing to repair.
        assert probe_journal_group("alice") is True

    def test_user_in_group_returns_true(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair._group_exists",
            lambda g: True,
        )
        monkeypatch.setattr(
            "openfollow.privilege.device_repair._user_in_group",
            lambda user, group: True,
        )
        assert probe_journal_group("alice") is True

    def test_user_not_in_group_returns_false(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair._group_exists",
            lambda g: True,
        )
        monkeypatch.setattr(
            "openfollow.privilege.device_repair._user_in_group",
            lambda user, group: False,
        )
        assert probe_journal_group("alice") is False


class TestProbeLinger:
    def test_missing_loginctl_treated_as_satisfied(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.shutil.which",
            lambda name: None,
        )
        assert probe_linger("alice") is True

    def test_linger_yes_returns_true(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.shutil.which",
            lambda name: "/usr/bin/loginctl",
        )
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(
                [],
                0,
                "Linger=yes\n",
                "",
            ),
        )
        assert probe_linger("alice") is True

    def test_linger_no_returns_false(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.shutil.which",
            lambda name: "/usr/bin/loginctl",
        )
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(
                [],
                0,
                "Linger=no\n",
                "",
            ),
        )
        assert probe_linger("alice") is False

    def test_loginctl_failure_flags_for_repair(self, monkeypatch) -> None:
        """A transient loginctl error fails closed (the binary is present),
        so the row stays flagged for repair rather than silently satisfied."""
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.shutil.which",
            lambda name: "/usr/bin/loginctl",
        )

        def _boom(*a, **kw):
            raise subprocess.SubprocessError("boom")

        monkeypatch.setattr(
            "openfollow.privilege.device_repair.subprocess.run",
            _boom,
        )
        assert probe_linger("alice") is False


class TestProbeServiceDisabled:
    @pytest.mark.parametrize(
        "stdout, expected",
        [
            ("disabled", True),
            ("masked", True),
            ("static", True),
            ("enabled", False),
        ],
    )
    def test_states(self, monkeypatch, stdout, expected) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.shutil.which",
            lambda name: "/usr/bin/systemctl",
        )
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess([], 0, stdout + "\n", ""),
        )
        assert probe_service_disabled("x") is expected

    def test_enabled_runtime_with_install_section_is_not_disabled(
        self,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.shutil.which",
            lambda name: "/usr/bin/systemctl",
        )

        def _run(argv, **kw):
            cmd = argv[1] if len(argv) > 1 else ""
            if cmd == "is-enabled":
                return subprocess.CompletedProcess(argv, 0, "enabled-runtime\n", "")
            # ``systemctl cat`` output includes ``[Install]`` so the
            # unit IS disable-able.
            return subprocess.CompletedProcess(
                argv,
                0,
                "[Unit]\n...\n[Install]\nWantedBy=multi-user.target\n",
                "",
            )

        monkeypatch.setattr("openfollow.privilege.device_repair.subprocess.run", _run)
        assert probe_service_disabled("x") is False

    def test_enabled_runtime_without_install_section_treated_as_disabled(
        self,
        monkeypatch,
    ) -> None:
        """Enabled-runtime without [Install] section is treated as already-disabled."""
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.shutil.which",
            lambda name: "/usr/bin/systemctl",
        )

        def _run(argv, **kw):
            cmd = argv[1] if len(argv) > 1 else ""
            if cmd == "is-enabled":
                return subprocess.CompletedProcess(argv, 0, "enabled-runtime\n", "")
            # No ``[Install]`` header – runtime-injected by another
            # mechanism, no persistent enable to revoke.
            return subprocess.CompletedProcess(
                argv,
                0,
                "[Unit]\nDescription=Runtime target\n",
                "",
            )

        monkeypatch.setattr("openfollow.privilege.device_repair.subprocess.run", _run)
        assert probe_service_disabled("cloud-init.target") is True


class TestProbeServiceMasked:
    def test_masked_returns_true(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.shutil.which",
            lambda name: "/usr/bin/systemctl",
        )
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess([], 0, "masked\n", ""),
        )
        assert probe_service_masked("getty@tty1.service") is True

    def test_other_state_returns_false(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.shutil.which",
            lambda name: "/usr/bin/systemctl",
        )
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess([], 0, "enabled\n", ""),
        )
        assert probe_service_masked("x") is False


class TestProbeAllBootDelayDisabled:
    def test_aggregates_per_service(self, monkeypatch, tmp_path) -> None:
        calls: list[str] = []

        def fake_disabled(unit: str) -> bool:
            calls.append(unit)
            return True

        # Pin the nm-online drop-in absent so a provisioned host's bounded
        # timeout can't short-circuit the per-service probe.
        monkeypatch.setattr(
            "openfollow.privilege.device_repair._NM_WAIT_ONLINE_TIMEOUT_DROPIN",
            str(tmp_path / "absent.conf"),
        )
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.probe_service_disabled",
            fake_disabled,
        )
        assert probe_all_boot_delay_disabled() is True
        assert calls == list(BOOT_DELAY_SERVICES)


# ---------- appliers --------------------------------------------------------


class TestApplyHardwareGroups:
    def test_skips_when_already_in_all_groups(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair._group_exists",
            lambda g: True,
        )
        monkeypatch.setattr(
            "openfollow.privilege.device_repair._user_in_group",
            lambda user, group: True,
        )
        broker = FakeBroker()
        apply_hardware_groups(broker, "alice")
        assert broker.calls == []

    def test_uses_canonical_group_argv_when_some_missing(self, monkeypatch) -> None:
        """Always uses the full REQUIRED_HARDWARE_GROUPS list for argv matching sudoers rules."""
        from openfollow.privilege.capabilities import REQUIRED_HARDWARE_GROUPS_JOINED

        monkeypatch.setattr(
            "openfollow.privilege.device_repair._group_exists",
            lambda g: True,
        )
        # User already in everything but ``input`` + ``plugdev``.
        monkeypatch.setattr(
            "openfollow.privilege.device_repair._user_in_group",
            lambda user, group: group not in ("input", "plugdev"),
        )
        broker = FakeBroker()
        apply_hardware_groups(broker, "alice")
        assert len(broker.calls) == 1
        assert broker.calls[0].capability == DEVICE_GROUP_JOIN
        # Canonical argv: full group list (not just the missing subset).
        assert broker.calls[0].argv == [
            "/usr/sbin/usermod",
            "-aG",
            REQUIRED_HARDWARE_GROUPS_JOINED,
            "alice",
        ]

    def test_raises_when_required_group_missing_on_host(self, monkeypatch) -> None:
        from openfollow.privilege.broker import PrivilegeError

        # ``render`` absent – common on non-Wayland containers.
        monkeypatch.setattr(
            "openfollow.privilege.device_repair._group_exists",
            lambda g: g != "render",
        )
        monkeypatch.setattr(
            "openfollow.privilege.device_repair._user_in_group",
            lambda user, group: False,
        )
        broker = FakeBroker()
        with pytest.raises(PrivilegeError, match="render"):
            apply_hardware_groups(broker, "alice")
        assert broker.calls == []

    def test_rejects_unsafe_user_before_privileged_call(self, monkeypatch) -> None:
        """An unsafe user name is rejected before it reaches the root
        ``usermod`` argv – mirrors render_drop_in's allowlist."""
        from openfollow.privilege.broker import PrivilegeError

        broker = FakeBroker()
        with pytest.raises(PrivilegeError, match="unsafe user"):
            apply_hardware_groups(broker, "root\nExecStartPre=evil")
        assert broker.calls == []


class TestApplyJournalGroup:
    def test_skips_when_group_missing(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair._group_exists",
            lambda g: False,
        )
        broker = FakeBroker()
        apply_journal_group(broker, "alice")
        assert broker.calls == []

    def test_skips_when_already_member(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair._group_exists",
            lambda g: True,
        )
        monkeypatch.setattr(
            "openfollow.privilege.device_repair._user_in_group",
            lambda user, group: True,
        )
        broker = FakeBroker()
        apply_journal_group(broker, "alice")
        assert broker.calls == []

    def test_joins_when_missing(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair._group_exists",
            lambda g: True,
        )
        monkeypatch.setattr(
            "openfollow.privilege.device_repair._user_in_group",
            lambda user, group: False,
        )
        broker = FakeBroker()
        apply_journal_group(broker, "alice")
        assert len(broker.calls) == 1
        assert broker.calls[0].capability == JOURNAL_GROUP_JOIN


class TestApplyLinger:
    def test_skips_when_already_enabled(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.probe_linger",
            lambda user=None: True,
        )
        broker = FakeBroker()
        apply_linger(broker, "alice")
        assert broker.calls == []

    def test_enables_when_disabled(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.probe_linger",
            lambda user=None: False,
        )
        broker = FakeBroker()
        apply_linger(broker, "alice")
        assert len(broker.calls) == 1
        assert broker.calls[0].capability == DEVICE_LINGER_ENABLE
        assert broker.calls[0].argv == [
            "/usr/bin/loginctl",
            "enable-linger",
            "alice",
        ]


class TestApplyEnableSeatd:
    """Apply does systemctl enable only (no --now), so probe must be is-enabled."""

    def test_skips_when_already_enabled(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.probe_service_enabled",
            lambda unit: True,
        )
        broker = FakeBroker()
        apply_enable_seatd(broker)
        assert broker.calls == []

    def test_enables_when_not_enabled(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.probe_service_enabled",
            lambda unit: False,
        )
        broker = FakeBroker()
        apply_enable_seatd(broker)
        assert len(broker.calls) == 1
        assert broker.calls[0].capability == SERVICE_ENABLE
        assert broker.calls[0].argv == [
            "/usr/bin/systemctl",
            "enable",
            "seatd",
        ]


class TestProbeServiceEnabled:
    def test_returns_true_when_systemctl_reports_enabled(self, monkeypatch) -> None:
        import subprocess as _sp

        from openfollow.privilege import device_repair

        monkeypatch.setattr(device_repair.shutil, "which", lambda _: "/usr/bin/systemctl")
        monkeypatch.setattr(
            device_repair.subprocess,
            "run",
            lambda *a, **kw: _sp.CompletedProcess(a[0], 0, stdout="enabled\n", stderr=""),
        )
        assert device_repair.probe_service_enabled("seatd") is True

    def test_returns_true_for_enabled_runtime(self, monkeypatch) -> None:
        import subprocess as _sp

        from openfollow.privilege import device_repair

        monkeypatch.setattr(device_repair.shutil, "which", lambda _: "/usr/bin/systemctl")
        monkeypatch.setattr(
            device_repair.subprocess,
            "run",
            lambda *a, **kw: _sp.CompletedProcess(a[0], 0, stdout="enabled-runtime\n", stderr=""),
        )
        assert device_repair.probe_service_enabled("seatd") is True

    def test_returns_false_when_disabled(self, monkeypatch) -> None:
        import subprocess as _sp

        from openfollow.privilege import device_repair

        monkeypatch.setattr(device_repair.shutil, "which", lambda _: "/usr/bin/systemctl")
        monkeypatch.setattr(
            device_repair.subprocess,
            "run",
            lambda *a, **kw: _sp.CompletedProcess(a[0], 1, stdout="disabled\n", stderr=""),
        )
        assert device_repair.probe_service_enabled("seatd") is False

    def test_returns_true_when_systemctl_missing(self, monkeypatch) -> None:
        from openfollow.privilege import device_repair

        monkeypatch.setattr(device_repair.shutil, "which", lambda _: None)
        assert device_repair.probe_service_enabled("seatd") is True

    def test_returns_false_on_subprocess_error(self, monkeypatch) -> None:
        """Fails closed (systemctl present): a transient error flags the
        enable-at-boot repair rather than reporting it satisfied."""
        from openfollow.privilege import device_repair

        def _boom(*_a, **_kw):
            raise OSError("systemctl crashed")

        monkeypatch.setattr(device_repair.shutil, "which", lambda _: "/usr/bin/systemctl")
        monkeypatch.setattr(device_repair.subprocess, "run", _boom)
        assert device_repair.probe_service_enabled("seatd") is False


class TestApplyDisableBootDelay:
    def test_batches_enabled_units_into_single_call(self, monkeypatch, tmp_path) -> None:
        """The missing services are passed to ``systemctl disable`` in a
        single argv – that's one broker call (one password prompt)
        regardless of how many services need disabling."""
        enabled_units = ["NetworkManager-wait-online.service", "bluetooth.service"]
        # Pin the nm-online drop-in absent so a provisioned host doesn't drop
        # NM-wait-online from the batch via the bounded-timeout short-circuit.
        monkeypatch.setattr(
            "openfollow.privilege.device_repair._NM_WAIT_ONLINE_TIMEOUT_DROPIN",
            str(tmp_path / "absent.conf"),
        )
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.probe_service_disabled",
            lambda unit: unit not in enabled_units,
        )
        broker = FakeBroker()
        apply_disable_boot_delay(broker)
        assert len(broker.calls) == 1
        call = broker.calls[0]
        assert call.capability == SERVICE_DISABLE
        assert call.argv[:2] == ["/usr/bin/systemctl", "disable"]
        # Order in the argv tail follows BOOT_DELAY_SERVICES iteration;
        # set comparison stays robust to ordering.
        assert set(call.argv[2:]) == set(enabled_units)

    def test_skips_call_when_nothing_enabled(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.probe_service_disabled",
            lambda unit: True,
        )
        broker = FakeBroker()
        apply_disable_boot_delay(broker)
        assert broker.calls == []


class TestBootDelayNmWaitOnlineDropin:
    """NetworkManager-wait-online is kept enabled with a bounded-timeout drop-in.
    Boot-delay repair must treat this as satisfied."""

    _NM = "NetworkManager-wait-online.service"

    def _patch_dropin(self, monkeypatch, path) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair._NM_WAIT_ONLINE_TIMEOUT_DROPIN",
            str(path),
        )

    def test_enabled_with_bounded_dropin_is_satisfied(self, monkeypatch, tmp_path) -> None:
        dropin = tmp_path / "timeout.conf"
        dropin.write_text("[Service]\nExecStart=\nExecStart=/usr/bin/nm-online -s -q --timeout=30\n")
        self._patch_dropin(monkeypatch, dropin)
        # Everything disabled except NM-wait-online, which is enabled.
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.probe_service_disabled",
            lambda unit: unit != self._NM,
        )
        assert probe_all_boot_delay_disabled() is True

    def test_enabled_without_dropin_is_pending(self, monkeypatch, tmp_path) -> None:
        # No drop-in file -> a genuinely un-bounded enabled unit is flagged.
        self._patch_dropin(monkeypatch, tmp_path / "absent.conf")
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.probe_service_disabled",
            lambda unit: unit != self._NM,
        )
        assert probe_all_boot_delay_disabled() is False

    def test_dropin_without_bounded_timeout_is_pending(self, monkeypatch, tmp_path) -> None:
        # A drop-in that doesn't bound the timeout isn't the installer's.
        dropin = tmp_path / "timeout.conf"
        dropin.write_text("[Service]\n# operator left this empty\n")
        self._patch_dropin(monkeypatch, dropin)
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.probe_service_disabled",
            lambda unit: unit != self._NM,
        )
        assert probe_all_boot_delay_disabled() is False

    def test_apply_keeps_nm_wait_online_when_bounded(self, monkeypatch, tmp_path) -> None:
        # Force-apply must NOT disable NM-wait-online when bounded; other enabled services are disabled.
        dropin = tmp_path / "timeout.conf"
        dropin.write_text("ExecStart=/usr/bin/nm-online --timeout=30\n")
        self._patch_dropin(monkeypatch, dropin)
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.probe_service_disabled",
            lambda unit: unit not in (self._NM, "bluetooth.service"),
        )
        broker = FakeBroker()
        apply_disable_boot_delay(broker)
        assert len(broker.calls) == 1
        assert self._NM not in broker.calls[0].argv
        assert "bluetooth.service" in broker.calls[0].argv


class TestApplyMaskGetty:
    def test_skips_when_already_masked(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.probe_service_masked",
            lambda unit: True,
        )
        broker = FakeBroker()
        apply_mask_getty(broker)
        assert broker.calls == []

    def test_masks_when_not_masked(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.device_repair.probe_service_masked",
            lambda unit: False,
        )
        broker = FakeBroker()
        apply_mask_getty(broker)
        assert len(broker.calls) == 1
        assert broker.calls[0].capability == SERVICE_MASK
        assert broker.calls[0].argv == [
            "/usr/bin/systemctl",
            "mask",
            "getty@tty1.service",
        ]


class TestAllActions:
    def test_returns_repair_action_tuple(self) -> None:
        actions = all_actions()
        assert len(actions) == 6
        names = [a.name for a in actions]
        assert "repair.hardware_groups" in names
        assert "repair.systemd_unit" not in names  # systemd_unit is its own page section


class TestPrivateHelpers:
    """Coverage for the small private helpers – failure-mode branches
    that the public functions delegate to."""

    def test_user_in_group_returns_false_when_group_missing(self, monkeypatch) -> None:
        from openfollow.privilege.device_repair import _user_in_group

        # ``grp.getgrnam`` raises KeyError for unknown groups; helper
        # treats that as "user is not a member" rather than raising.
        assert _user_in_group("alice", "definitely-no-such-group-xyz") is False

    def test_user_in_group_returns_false_when_user_absent(self, monkeypatch) -> None:
        """Group exists but the user isn't a member: returns False
        rather than raising – covers the ``return user in members``
        branch at the end of the helper."""
        import grp as grp_mod

        from openfollow.privilege.device_repair import _user_in_group

        # Pick whichever well-known group exists on this host. ``root``
        # is always present on Linux; macOS CI fixture chooses ``wheel``
        # / ``staff`` / ``sys``.
        candidates = ["root", "wheel", "staff", "sys", "users"]
        existing = next(
            (g for g in candidates if _try_getgrnam(grp_mod, g) is not None),
            None,
        )
        assert existing is not None, "no probe group found on this host"
        assert (
            _user_in_group(
                "definitely-not-a-real-user-xyz",
                existing,
            )
            is False
        )

    def test_group_exists_returns_true_for_known_group(self) -> None:
        """Counterpart to the KeyError branch – covers the
        ``return True`` happy path of ``_group_exists``."""
        import grp as grp_mod

        from openfollow.privilege.device_repair import _group_exists

        candidates = ["root", "wheel", "staff", "sys", "users"]
        existing = next(
            (g for g in candidates if _try_getgrnam(grp_mod, g) is not None),
            None,
        )
        assert existing is not None
        assert _group_exists(existing) is True

    def test_group_exists_returns_false_for_unknown_group(self) -> None:
        from openfollow.privilege.device_repair import _group_exists

        assert _group_exists("definitely-no-such-group-xyz") is False

    def test_probe_linger_subprocess_error_branch(self, monkeypatch) -> None:
        from openfollow.privilege import device_repair as mod

        monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/bin/loginctl")

        def _boom_linger(*a, **kw):
            raise OSError("ENOENT mid-flight")

        monkeypatch.setattr(mod.subprocess, "run", _boom_linger)
        # OSError branch (not SubprocessError) – covers the second
        # ``except`` clause in probe_linger. Fails closed → flag for repair.
        assert mod.probe_linger("alice") is False

    def test_probe_service_disabled_no_systemctl_branch(self, monkeypatch) -> None:
        from openfollow.privilege import device_repair as mod

        monkeypatch.setattr(mod.shutil, "which", lambda name: None)
        assert mod.probe_service_disabled("anything") is True

    def test_probe_service_disabled_subprocess_error_branch(self, monkeypatch) -> None:
        from openfollow.privilege import device_repair as mod

        monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/bin/systemctl")

        def _boom_disabled(*a, **kw):
            raise OSError("ENOENT mid-flight")

        monkeypatch.setattr(mod.subprocess, "run", _boom_disabled)
        assert mod.probe_service_disabled("anything") is False  # fail closed

    def test_probe_service_masked_no_systemctl_branch(self, monkeypatch) -> None:
        from openfollow.privilege import device_repair as mod

        monkeypatch.setattr(mod.shutil, "which", lambda name: None)
        assert mod.probe_service_masked("getty@tty1.service") is True

    def test_probe_service_masked_subprocess_error_branch(self, monkeypatch) -> None:
        from openfollow.privilege import device_repair as mod

        monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/bin/systemctl")

        def _boom_masked(*a, **kw):
            raise subprocess.SubprocessError("boom")

        monkeypatch.setattr(mod.subprocess, "run", _boom_masked)
        assert mod.probe_service_masked("getty@tty1.service") is False  # fail closed


class TestRegistryConstants:
    def test_required_groups_drops_audio_and_sudo(self) -> None:
        # audio + sudo are explicitly excluded – sudo via the drop-in
        # path, audio not required by the current pipeline.
        assert "audio" not in REQUIRED_GROUPS
        assert "sudo" not in REQUIRED_GROUPS

    def test_boot_delay_services_includes_cloud_init(self) -> None:
        # cloud-init costs the most boot time on a Pi OS Lite image –
        # confirm it's covered.
        assert "cloud-init.target" in BOOT_DELAY_SERVICES
        assert "NetworkManager-wait-online.service" in BOOT_DELAY_SERVICES


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
