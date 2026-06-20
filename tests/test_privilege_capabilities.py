# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Smoke tests for the capability registry."""

from __future__ import annotations

import re
from dataclasses import FrozenInstanceError

import pytest

from openfollow.privilege.capabilities import (
    _PROBE_PLACEHOLDER,
    _USERNAME_ARG_RE,
    ALL_CAPABILITIES,
    DEB_UPDATE_TMP_PREFIX,
    DEVICE_GROUP_JOIN,
    DEVICE_INSTALL_SUDOERS_DROPIN,
    DEVICE_INSTALL_SYSTEMD_UNIT,
    DEVICE_TRUNCATE_LEGACY_SUDOERS,
    JOURNAL_GROUP_JOIN,
    NETWORK_DHCPCD_CONF_COMMIT,
    NETWORK_DHCPCD_CONF_WRITE_TMP,
    NETWORK_NM_CON_MOD,
    PACKAGE_SELF_UPDATE,
    REQUIRED_HARDWARE_GROUPS_JOINED,
    SELF_UPDATE_SCRIPT,
    SELF_UPDATE_UNIT,
    SERVICE_DAEMON_RELOAD,
    SERVICE_RESTART,
    SUDOERS_DROP_IN_FILENAME,
    SUDOERS_TMP_PREFIX,
    SYSTEMD_UNIT_TMP_PREFIX,
    Capability,
    capability_by_name,
)

pytestmark = pytest.mark.unit


def test_all_capabilities_have_unique_names() -> None:
    names = [c.name for c in ALL_CAPABILITIES]
    assert len(names) == len(set(names))


def test_all_capabilities_have_a_description() -> None:
    for cap in ALL_CAPABILITIES:
        assert cap.description.strip()


def test_every_capability_in_registry_has_sudoers_pattern() -> None:
    for cap in ALL_CAPABILITIES:
        assert cap.sudoers_pattern, f"{cap.name} missing sudoers_pattern"


def test_install_capabilities_are_narrowly_scoped() -> None:
    """Each install capability is pinned to a specific (tmp-prefix, destination) pair
    to prevent the NOPASSWD grant from being repurposed."""
    # Sudoers drop-in install: tmp file comes from the dedicated
    # ``openfollow-sudoers-*`` prefix; destination is the exact
    # drop-in path.
    assert SUDOERS_TMP_PREFIX in DEVICE_INSTALL_SUDOERS_DROPIN.sudoers_pattern
    assert f"/etc/sudoers.d/{SUDOERS_DROP_IN_FILENAME}" in DEVICE_INSTALL_SUDOERS_DROPIN.sudoers_pattern
    # Legacy truncate: source is ``/dev/null``, destination is
    # the legacy filename. No globbing.
    assert "/dev/null" in DEVICE_TRUNCATE_LEGACY_SUDOERS.sudoers_pattern
    # Systemd unit install: tmp prefix + ``.service`` suffix, exact
    # destination path.
    assert SYSTEMD_UNIT_TMP_PREFIX in DEVICE_INSTALL_SYSTEMD_UNIT.sudoers_pattern
    assert "/etc/systemd/system/openfollow.service" in DEVICE_INSTALL_SYSTEMD_UNIT.sudoers_pattern


def test_capability_by_name_finds_registered() -> None:
    assert capability_by_name(SERVICE_RESTART.name) is SERVICE_RESTART


def test_capability_by_name_returns_none_for_unknown() -> None:
    assert capability_by_name("does.not.exist") is None


def test_network_dhcpcd_conf_write_is_atomic_and_exact_path() -> None:
    """The conf is written atomically (stage to ``.tmp`` then ``mv``), and
    both rules are scoped to the exact file paths – no wildcard – so neither
    NOPASSWD grant can tee/mv into other system files.

    The ``tee`` targets the ``.tmp`` (never the live conf), and the ``mv``
    commit goes from that exact ``.tmp`` to the exact live path: a partial
    ``tee`` can only corrupt the tmp, while the ``mv`` rename is atomic, so
    ``/etc/dhcpcd.conf`` is never left truncated.
    """
    assert NETWORK_DHCPCD_CONF_WRITE_TMP.sudoers_pattern == "/usr/bin/tee /etc/dhcpcd.conf.tmp"
    assert NETWORK_DHCPCD_CONF_COMMIT.sudoers_pattern == "/usr/bin/mv /etc/dhcpcd.conf.tmp /etc/dhcpcd.conf"
    # Fully fixed: no wildcard slot, so ``assert_argv_allowed`` rejects any
    # argv that isn't the exact staged/commit invocation.
    assert "*" not in NETWORK_DHCPCD_CONF_WRITE_TMP.sudoers_pattern
    assert "*" not in NETWORK_DHCPCD_CONF_COMMIT.sudoers_pattern
    NETWORK_DHCPCD_CONF_WRITE_TMP.assert_argv_allowed(["/usr/bin/tee", "/etc/dhcpcd.conf.tmp"])
    NETWORK_DHCPCD_CONF_COMMIT.assert_argv_allowed(["/usr/bin/mv", "/etc/dhcpcd.conf.tmp", "/etc/dhcpcd.conf"])


def test_network_dhcpcd_conf_commit_rejects_arbitrary_destination() -> None:
    """The fully-fixed commit grant can't be repurposed to ``mv`` the staged
    tmp over some other privileged file (e.g. a sudoers drop-in)."""
    with pytest.raises(ValueError):
        NETWORK_DHCPCD_CONF_COMMIT.assert_argv_allowed(["/usr/bin/mv", "/etc/dhcpcd.conf.tmp", "/etc/sudoers.d/evil"])


def test_capability_is_frozen() -> None:
    """The dataclass is frozen so a misbehaving caller can't mutate a
    capability's argv mid-flight."""
    with pytest.raises(FrozenInstanceError):
        SERVICE_RESTART.name = "hack"  # type: ignore[misc]


def test_capability_can_be_constructed_directly() -> None:
    """Bespoke capabilities (e.g. test-only ones) can be built from
    outside the registry."""
    cap = Capability(
        name="t.bespoke",
        probe_argv=("/bin/x",),
        description="x",
        sudoers_pattern="/bin/x *",
    )
    assert cap.name == "t.bespoke"


def test_package_self_update_in_registry() -> None:
    """The detached self-update install must be in the registry so the rendered
    sudoers drop-in grants the systemd-run + wrapper invocation NOPASSWD."""
    assert PACKAGE_SELF_UPDATE in ALL_CAPABILITIES


# ---- usermod group-join hardening ----------------------------------


@pytest.mark.parametrize("cap", [JOURNAL_GROUP_JOIN, DEVICE_GROUP_JOIN])
def test_usermod_caps_pin_user_slot_with_anchored_regex(cap: Capability) -> None:
    """The user slot must be the anchored ``^…$`` regex, NOT a trailing ``*``
    (which would also match an injected ``-aG sudo <user>``)."""
    assert cap.sudoers_pattern.endswith(_USERNAME_ARG_RE)
    assert not cap.sudoers_pattern.endswith("*")
    assert cap.arg_pattern == _USERNAME_ARG_RE


def test_username_regex_matches_probe_placeholder() -> None:
    """The probe placeholder must satisfy the regex, else ``sudo -n -ll``
    can't match the rule and the broker reports NEEDS_PASSWORD even with the
    drop-in installed."""
    assert re.fullmatch(_USERNAME_ARG_RE, _PROBE_PLACEHOLDER)


@pytest.mark.parametrize("good", ["pi", "openfollow", "_svc", "a-b_c"])
def test_username_regex_accepts_valid_users(good: str) -> None:
    assert re.fullmatch(_USERNAME_ARG_RE, good)


@pytest.mark.parametrize(
    "bad",
    ["-aG sudo root", "a b", "a/b", "1abc", "-x", "Root", "pi\n", ""],
)
def test_username_regex_rejects_escalation_and_junk(bad: str) -> None:
    assert re.fullmatch(_USERNAME_ARG_RE, bad) is None


def test_assert_argv_allowed_accepts_real_group_join() -> None:
    DEVICE_GROUP_JOIN.assert_argv_allowed(["/usr/sbin/usermod", "-aG", REQUIRED_HARDWARE_GROUPS_JOINED, "pi"])


def test_assert_argv_allowed_rejects_injected_sudo_group() -> None:
    with pytest.raises(ValueError, match="single token"):
        DEVICE_GROUP_JOIN.assert_argv_allowed(
            ["/usr/sbin/usermod", "-aG", REQUIRED_HARDWARE_GROUPS_JOINED, "-aG", "sudo", "root"]
        )


def test_assert_argv_allowed_rejects_wrong_prefix() -> None:
    with pytest.raises(ValueError, match="fixed prefix"):
        SERVICE_RESTART.assert_argv_allowed(["/usr/sbin/usermod", "-aG", "sudo", "root"])


def test_assert_argv_allowed_allows_multitoken_tail_when_unconstrained() -> None:
    """``arg_pattern`` is None for nmcli, whose ``con mod`` tail is multi-token
    and may contain whitespace – only the fixed prefix is checked."""
    NETWORK_NM_CON_MOD.assert_argv_allowed(
        ["/usr/bin/nmcli", "con", "mod", "Wired connection 1", "ipv4.method", "manual"]
    )


def test_assert_argv_allowed_allows_custom_install_destination() -> None:
    """The destination is *after* the wildcard slot, so a custom service name
    is not rejected here – it falls through to the broker's password path."""
    DEVICE_INSTALL_SYSTEMD_UNIT.assert_argv_allowed(
        [
            "/usr/bin/install",
            "-m",
            "0644",
            "-o",
            "root",
            "-g",
            "root",
            "/tmp/openfollow-abc.service",
            "/etc/systemd/system/custom-name.service",
        ]
    )


def test_assert_argv_allowed_accepts_exact_fully_fixed_argv() -> None:
    """A fully-fixed capability (no wildcard slot) accepts its exact argv."""
    SERVICE_DAEMON_RELOAD.assert_argv_allowed(["/usr/bin/systemctl", "daemon-reload"])


def test_assert_argv_allowed_rejects_extra_trailing_args_on_fully_fixed() -> None:
    """A fully-fixed capability must reject extra trailing tokens – the
    in-process check is as tight as the trailing-``*``-free sudoers rule."""
    with pytest.raises(ValueError, match="extra trailing tokens"):
        SERVICE_DAEMON_RELOAD.assert_argv_allowed(["/usr/bin/systemctl", "daemon-reload", "--whatever"])


def test_no_fixed_probe_token_contains_placeholder() -> None:
    """Guards the wildcard-slot heuristic in ``assert_argv_allowed``: a token
    holds the placeholder only if it is the bare slot or a ``/tmp/`` staging
    path – never a fixed flag/destination."""
    for cap in ALL_CAPABILITIES:
        for tok in cap.probe_argv:
            if _PROBE_PLACEHOLDER in tok:
                assert tok == _PROBE_PLACEHOLDER or tok.startswith("/tmp/"), (
                    f"{cap.name}: unexpected placeholder-bearing token {tok!r}"
                )


def test_package_self_update_pattern_pins_systemd_run_script_and_prefix() -> None:
    """The sudoers pattern must pin the systemd-run launch, the transient unit
    name, the fixed wrapper-script path, AND the temp staging prefix, so the
    grant can't be repurposed to run an arbitrary unit or command as root."""
    pattern = PACKAGE_SELF_UPDATE.sudoers_pattern
    assert pattern.startswith(f"/usr/bin/systemd-run --collect --unit={SELF_UPDATE_UNIT} ")
    assert SELF_UPDATE_SCRIPT in pattern
    assert DEB_UPDATE_TMP_PREFIX in pattern
    assert pattern.endswith("*")
    # The argv the worker builds must line up with the pattern's fixed tokens.
    assert PACKAGE_SELF_UPDATE.probe_argv[:4] == (
        "/usr/bin/systemd-run",
        "--collect",
        f"--unit={SELF_UPDATE_UNIT}",
        SELF_UPDATE_SCRIPT,
    )
    assert DEB_UPDATE_TMP_PREFIX in PACKAGE_SELF_UPDATE.probe_argv[4]
