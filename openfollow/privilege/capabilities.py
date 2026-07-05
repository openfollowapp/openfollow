# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Declarative registry of privileged operations (names are stable identifiers)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

# Network capabilities – dhcpcd writes via tee, NM via nmcli + systemctl.

# Wildcard probe_argv needs placeholder to match sudoers rule correctly.
# INVARIANT: no *fixed* probe_argv token may contain ``_`` (the in-process
# argv check locates the wildcard slot by it). A test pins this.
_PROBE_PLACEHOLDER = "_"

# Anchored sudoers regex (sudo >= 1.9.10) matching ONE POSIX username token.
# Bounds the ``usermod`` user slot to a single arg – a sudoers ``*`` would
# also match an injected ``-aG sudo <user>`` (passwordless sudo-group join).
# Char classes mirror ``_is_safe_user`` in drop_in.py; a test keeps them in sync.
_USERNAME_ARG_RE = "^[a-z_][a-z0-9_-]*$"


class CapabilityState(str, Enum):
    """Result of probing whether the host can run a capability silently."""

    PASSWORDLESS = "passwordless"
    NEEDS_PASSWORD = "needs_password"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Capability:
    """One privileged operation (probe_argv is used for sudo -n -l)."""

    name: str
    probe_argv: tuple[str, ...]
    description: str
    sudoers_pattern: str
    """Sudoers rule pattern (fixed tokens + an anchored ``^…$`` regex or
    ``*`` glob for the free slot, or fully fixed argv)."""

    arg_pattern: str | None = None
    """Optional anchored regex the single trailing wildcard arg must match
    in-process. Set ONLY where the free slot is a security-sensitive single
    token (the ``usermod`` group-join user). ``None`` leaves the tail to the
    sudoers rule – needed where it is multi-token or may contain whitespace
    (e.g. ``nmcli con mod``)."""

    probe_arg: str | None = None
    """Concrete value substituted into the free ``_PROBE_PLACEHOLDER`` slot when
    *probing* (``sudo -n -ll``). Needed when the sudoers rule bounds the slot
    with an :attr:`arg_pattern` the bare ``_`` placeholder can't satisfy (the
    ``^@[0-9]+$`` epoch regex): the probe must pass a rule-matching token or
    ``sudo`` finds no covering rule and the capability never reads as
    PASSWORDLESS even when the grant is installed. ``None`` keeps the raw ``_``,
    which already matches every ``*`` glob and the username regex."""

    def probe_command(self) -> tuple[str, ...]:
        """The argv passed to ``sudo -n -ll`` when probing this capability.

        Fills the free placeholder slot with :attr:`probe_arg` when set so a
        capability whose sudoers rule bounds the slot with an
        :attr:`arg_pattern` still probes against a rule-matching token. Only
        the security-inert probe path is affected; :meth:`assert_argv_allowed`
        and the real ``run`` argv are unchanged.
        """
        if self.probe_arg is None:
            return self.probe_argv
        return tuple(self.probe_arg if tok == _PROBE_PLACEHOLDER else tok for tok in self.probe_argv)

    def assert_argv_allowed(self, argv: list[str]) -> None:
        """Fail-closed in-process allow-set check (defence-in-depth).

        Ties the executed argv to this capability so a caller can't pair an
        arbitrary argv with it and lean on the sudoers rule as the sole gate.
        Raises :class:`ValueError`; the broker turns that into a
        ``PrivilegeError`` before invoking sudo.

        1. Fixed prefix: every ``probe_argv`` token before the wildcard slot
           must equal the matching ``argv`` token (binary + fixed flags).
           Tokens *after* the slot are left to the sudoers rule, so the
           custom-service-name install still falls through to the password
           prompt instead of being rejected here.
        2. Safe slot (only when :attr:`arg_pattern` is set): exactly one
           trailing arg, matching the anchored regex.
        3. Fully-fixed argv (no wildcard slot, :attr:`arg_pattern` ``None``):
           argv must equal the fixed prefix exactly – no extra trailing
           tokens, mirroring the trailing-``*``-free sudoers rule.
        """
        probe = self.probe_argv
        has_wildcard = any(_PROBE_PLACEHOLDER in tok for tok in probe)
        prefix_len = next(
            (i for i, tok in enumerate(probe) if _PROBE_PLACEHOLDER in tok),
            len(probe),
        )
        if list(argv[:prefix_len]) != list(probe[:prefix_len]):
            raise ValueError(
                f"argv does not match the {self.name} capability's fixed prefix {list(probe[:prefix_len])!r}"
            )
        if not has_wildcard and self.arg_pattern is None and len(argv) != prefix_len:
            raise ValueError(f"argv {list(argv)!r} has extra trailing tokens for fully-fixed capability {self.name}")
        if self.arg_pattern is not None:
            tail = list(argv[prefix_len:])
            if len(tail) != 1 or re.fullmatch(self.arg_pattern, tail[0]) is None:
                raise ValueError(
                    f"argv tail {tail!r} is not a single token matching {self.arg_pattern!r} for capability {self.name}"
                )


NETWORK_DHCPCD_RENEW = Capability(
    name="network.dhcpcd.renew",
    probe_argv=("/usr/sbin/dhcpcd", "-n", _PROBE_PLACEHOLDER),
    description="Renew DHCP lease (dhcpcd)",
    sudoers_pattern="/usr/sbin/dhcpcd -n *",
)

NETWORK_DHCPCD_RELEASE = Capability(
    name="network.dhcpcd.release",
    probe_argv=("/usr/sbin/dhcpcd", "-k", _PROBE_PLACEHOLDER),
    description="Release DHCP lease (dhcpcd)",
    sudoers_pattern="/usr/sbin/dhcpcd -k *",
)

NETWORK_NM_CON_MOD = Capability(
    name="network.nm.con_mod",
    probe_argv=("/usr/bin/nmcli", "con", "mod", _PROBE_PLACEHOLDER),
    description="Modify NetworkManager connection profile",
    sudoers_pattern="/usr/bin/nmcli con mod *",
)

NETWORK_NM_CON_UP = Capability(
    name="network.nm.con_up",
    probe_argv=("/usr/bin/nmcli", "con", "up", _PROBE_PLACEHOLDER),
    description="Bring NetworkManager connection up",
    sudoers_pattern="/usr/bin/nmcli con up *",
)

NETWORK_NM_CON_DOWN = Capability(
    name="network.nm.con_down",
    probe_argv=("/usr/bin/nmcli", "con", "down", _PROBE_PLACEHOLDER),
    description="Bring NetworkManager connection down",
    sudoers_pattern="/usr/bin/nmcli con down *",
)

# /etc/dhcpcd.conf is rewritten atomically in two privileged steps: stage the
# full file to the sibling ``.tmp`` (``tee`` truncates in place, so a killed /
# timed-out / OOM'd write can only corrupt the tmp – never the live conf), then
# commit with ``mv`` (``rename(2)`` on the same filesystem is atomic, so the
# live conf is always a whole file). Both argvs are fully fixed – no wildcard
# slot – so neither grant can tee/mv into any other path.
NETWORK_DHCPCD_CONF_WRITE_TMP = Capability(
    name="network.dhcpcd.conf_write_tmp",
    probe_argv=("/usr/bin/tee", "/etc/dhcpcd.conf.tmp"),
    description="Stage /etc/dhcpcd.conf (atomic write, step 1)",
    sudoers_pattern="/usr/bin/tee /etc/dhcpcd.conf.tmp",
)

NETWORK_DHCPCD_CONF_COMMIT = Capability(
    name="network.dhcpcd.conf_commit",
    probe_argv=("/usr/bin/mv", "/etc/dhcpcd.conf.tmp", "/etc/dhcpcd.conf"),
    description="Commit /etc/dhcpcd.conf (atomic rename, step 2)",
    sudoers_pattern="/usr/bin/mv /etc/dhcpcd.conf.tmp /etc/dhcpcd.conf",
)

NETWORK_DHCPCD_RELOAD = Capability(
    name="network.dhcpcd.reload",
    probe_argv=("/usr/bin/systemctl", "reload", "dhcpcd"),
    description="Reload dhcpcd",
    sudoers_pattern="/usr/bin/systemctl reload dhcpcd",
)


# ---------------------------------------------------------------------------
# Service control + update – folded here so the broker is the single
# source of truth.
# ---------------------------------------------------------------------------

SERVICE_RESTART = Capability(
    name="service.restart",
    probe_argv=("/usr/bin/systemctl", "restart", _PROBE_PLACEHOLDER),
    description="Restart a systemd unit",
    sudoers_pattern="/usr/bin/systemctl restart *",
)

DEB_UPDATE_TMP_PREFIX = "openfollow-update-"
"""mkstemp-style prefix used when staging a downloaded .deb before install.
Referenced in both the download code (deb_update.py) and the sudoers pattern
below, so renaming one surface is caught by tests."""

SELF_UPDATE_SCRIPT = "/usr/share/openfollow/apply-update.sh"
"""Fixed-path wrapper that installs a staged update (a single ``.deb`` file or a
bundle directory of ``.deb``s) and restarts the service. Launched as root in a
transient systemd unit (see :data:`PACKAGE_SELF_UPDATE`) so the install is
DETACHED from ``openfollow.service`` – the package's prerm stops that service
mid-install, which would otherwise kill an in-process ``apt-get`` and leave the
package half-configured. Referenced by both the broker call site
(``deb_update.py``) and the sudoers pattern below so the two can't drift."""

SELF_UPDATE_UNIT = "openfollow-self-update"
"""Transient unit name for the detached installer. ``--collect`` reaps it on
exit, so a fresh self-update can reuse the name."""

PACKAGE_SELF_UPDATE = Capability(
    name="package.self_update",
    probe_argv=(
        "/usr/bin/systemd-run",
        "--collect",
        f"--unit={SELF_UPDATE_UNIT}",
        SELF_UPDATE_SCRIPT,
        f"/tmp/{DEB_UPDATE_TMP_PREFIX}{_PROBE_PLACEHOLDER}",  # nosec B108
    ),
    description="Install an OpenFollow update in a detached unit",
    # Fixed flags + script path; trailing ``*`` matches the staged spec arg.
    # A sudoers ``*`` is NOT bounded – it crosses ``/``, whitespace, and extra
    # args. The real bound is downstream: apply-update.sh re-checks the
    # ``/tmp/openfollow-update-`` prefix before acting.
    sudoers_pattern=(
        f"/usr/bin/systemd-run --collect --unit={SELF_UPDATE_UNIT} {SELF_UPDATE_SCRIPT} /tmp/{DEB_UPDATE_TMP_PREFIX}*"
    ),
)

SERVICE_DAEMON_RELOAD = Capability(
    name="service.daemon_reload",
    probe_argv=("/usr/bin/systemctl", "daemon-reload"),
    description="Reload systemd unit definitions",
    sudoers_pattern="/usr/bin/systemctl daemon-reload",
)

SERVICE_ENABLE = Capability(
    name="service.enable",
    probe_argv=("/usr/bin/systemctl", "enable", _PROBE_PLACEHOLDER),
    description="Enable a systemd unit",
    sudoers_pattern="/usr/bin/systemctl enable *",
)

SERVICE_DISABLE = Capability(
    name="service.disable",
    probe_argv=("/usr/bin/systemctl", "disable", _PROBE_PLACEHOLDER),
    description="Disable a systemd unit",
    sudoers_pattern="/usr/bin/systemctl disable *",
)

SERVICE_MASK = Capability(
    name="service.mask",
    # ``systemctl mask`` is only ever called on ``getty@tty1.service``
    # (the cage-tty1 conflict mask in :func:`device_repair.apply_mask_getty`).
    # Pinning the pattern to ``getty@*.service`` keeps the rule open to other
    # getty units (e.g. tty2 on a multi-tty deployment) while preventing it
    # from being repurposed to mask security-critical services.
    # ``restart``/``enable``/``disable`` stay broad – they target
    # operator-configured / batched unit lists where pinning the exact unit
    # names would either be brittle (operator renames the service) or
    # infeasible (``disable`` batches an N-service list into one call, so
    # enumerating combinations is 2^N rules).
    probe_argv=("/usr/bin/systemctl", "mask", "getty@tty1.service"),
    description="Mask a getty unit (cage-vs-tty1 conflict fix)",
    sudoers_pattern="/usr/bin/systemctl mask getty@*.service",
)


# ---------------------------------------------------------------------------
# Log / journal capabilities (closes the "journalctl is unavailable on
# this host" gap when the binary is present but the user is not in the
# systemd-journal group).
# ---------------------------------------------------------------------------

LOG_READ_SUDO = Capability(
    name="log.read.sudo",
    probe_argv=("/usr/bin/journalctl", "-u", _PROBE_PLACEHOLDER),
    description="Read service logs via sudo (one-shot fallback)",
    sudoers_pattern="/usr/bin/journalctl -u *",
)

JOURNAL_GROUP_JOIN = Capability(
    name="log.group.join",
    probe_argv=(
        "/usr/sbin/usermod",
        "-aG",
        "systemd-journal",
        _PROBE_PLACEHOLDER,
    ),
    description="Grant this user permanent log access (one-time fix)",
    # Anchored username regex, NOT a trailing ``*`` – see _USERNAME_ARG_RE.
    sudoers_pattern=f"/usr/sbin/usermod -aG systemd-journal {_USERNAME_ARG_RE}",
    arg_pattern=_USERNAME_ARG_RE,
)


# ---------------------------------------------------------------------------
# Device Setup Repair – idempotent system-state items the Ansible
# installer normally deploys. Each capability is one ``sudo`` call.
# Reboot-required and destructive items are deliberately excluded.
# ---------------------------------------------------------------------------

REQUIRED_HARDWARE_GROUPS: tuple[str, ...] = (
    "video",
    "render",
    "input",
    "plugdev",
    "netdev",
    "dialout",
)
"""Canonical set of supplemental groups the device user needs for
cage / GPU / gamepad / udev access. Lives in this module (not
:mod:`openfollow.privilege.device_repair`) so the sudoers pattern
below can include the exact comma-joined list and a test in
``tests/test_privilege_capabilities.py`` pins the link between the
constant, the sudoers rule, and the rendered Ansible drop-in. Both the
group list and the user slot are pinned (no ``*``) so the grant can't be
turned into a ``sudo``-group join – see :data:`DEVICE_GROUP_JOIN`."""

REQUIRED_HARDWARE_GROUPS_JOINED = ",".join(REQUIRED_HARDWARE_GROUPS)

# Same list with commas backslash-escaped, for use inside a sudoers
# ``Cmnd_Spec``. Un-escaped commas terminate the current Cmnd in the
# rule (sudoers parser sees ``video`` and then expects a new fully-
# qualified path starting with ``render``), so ``visudo -cf`` rejects
# the drop-in with "expected a fully-qualified path name" error.
REQUIRED_HARDWARE_GROUPS_JOINED_SUDOERS = "\\,".join(REQUIRED_HARDWARE_GROUPS)

DEVICE_GROUP_JOIN = Capability(
    name="device.group_join",
    # Probe argv must include the canonical group list so ``sudo -n -l``
    # matches the narrowed sudoers rule below. With only ``usermod -aG``
    # (no group-list arg) sudo's matcher would not find a rule covering
    # ``-aG <something>`` and the broker would always report
    # ``NEEDS_PASSWORD`` – even with the drop-in installed.
    probe_argv=(
        "/usr/sbin/usermod",
        "-aG",
        REQUIRED_HARDWARE_GROUPS_JOINED,
        _PROBE_PLACEHOLDER,
    ),
    description=(f"Join supplemental hardware groups ({REQUIRED_HARDWARE_GROUPS_JOINED})"),
    # Pinned group list AND anchored username regex for the user slot (not a
    # trailing ``*``): pinning the list alone is not enough, since a sudoers
    # ``*`` would also match ``-aG <groups> -aG sudo <user>`` – a passwordless
    # ``sudo``-group join (local root). See _USERNAME_ARG_RE.
    #
    # Commas in the group list MUST be backslash-escaped in the sudoers
    # ``Cmnd_Spec``; un-escaped commas terminate the current command and
    # ``visudo -cf`` rejects the file ("expected a fully-qualified path name").
    sudoers_pattern=(f"/usr/sbin/usermod -aG {REQUIRED_HARDWARE_GROUPS_JOINED_SUDOERS} {_USERNAME_ARG_RE}"),
    arg_pattern=_USERNAME_ARG_RE,
)

DEVICE_LINGER_ENABLE = Capability(
    name="device.linger_enable",
    probe_argv=("/usr/bin/loginctl", "enable-linger", _PROBE_PLACEHOLDER),
    description="Enable user linger (autostart at boot)",
    sudoers_pattern="/usr/bin/loginctl enable-linger *",
)

DEVICE_SET_HOSTNAME = Capability(
    name="device.set_hostname",
    # The app self-names the device from its station identity at boot
    # (see :func:`device_repair.sync_station_hostname`) so it advertises a
    # memorable ``<slug>.local`` over mDNS. The trailing ``*`` matches the
    # derived single-label hostname; ``hostnamectl`` itself validates it.
    probe_argv=("/usr/bin/hostnamectl", "set-hostname", _PROBE_PLACEHOLDER),
    description="Set the system hostname",
    sudoers_pattern="/usr/bin/hostnamectl set-hostname *",
)

DEVICE_HOSTS_WRITE = Capability(
    name="device.hosts_write",
    # ``hostnamectl set-hostname`` does NOT touch /etc/hosts, so after a rename
    # the 127.0.1.1 loopback line is stale and every ``sudo`` logs "unable to
    # resolve host <hostname>". Rewrite /etc/hosts (via tee, mirroring the
    # dhcpcd.conf write) to keep that line in sync. Pinned to the exact path so
    # the grant can't overwrite an arbitrary file.
    probe_argv=("/usr/bin/tee", "/etc/hosts"),
    description="Rewrite /etc/hosts (loopback hostname mapping)",
    sudoers_pattern="/usr/bin/tee /etc/hosts",
)

# ---------------------------------------------------------------------------
# Install-file capabilities – scoped narrowly for security.
#
# A blanket ``/usr/bin/install *`` rule would let the device user
# overwrite ANY file as root (sudoers drop-ins, /etc/shadow, …). Each
# concrete install operation gets its own capability with a sudoers
# pattern that pins both the temp-file prefix (so a malicious staging
# path can't be substituted) and the exact destination.
#
# The temp-file prefix constants below are the source of truth – both
# the Python install code (drop_in.py / systemd_unit.py) and the
# sudoers patterns reference them, so a change to either side surfaces
# as a test failure rather than a silent NOPASSWD miss.
# ---------------------------------------------------------------------------

SUDOERS_TMP_PREFIX = "openfollow-sudoers-"
"""mkstemp prefix used by :func:`openfollow.privilege.drop_in.install_drop_in`
for the staged sudoers drop-in before it's atomically moved into
``/etc/sudoers.d/``. Referenced from the sudoers pattern below."""

SYSTEMD_UNIT_TMP_PREFIX = "openfollow-"
"""mkstemp prefix used by :func:`openfollow.privilege.systemd_unit.install_unit`
for the staged systemd unit before it's installed into
``/etc/systemd/system/``. Referenced from the sudoers pattern below."""

SUDOERS_DROP_IN_FILENAME = "openfollow-privileged"
LEGACY_SUDOERS_DROP_IN_FILENAME = "openfollow-update"
DEFAULT_SYSTEMD_UNIT_FILENAME = "openfollow.service"

DEVICE_INSTALL_SUDOERS_DROPIN = Capability(
    name="device.install_sudoers_dropin",
    # Probe argv includes BOTH file-path slots (source + destination) so
    # ``sudo -n -ll`` can match the rule's two trailing tokens. The source
    # ``*`` matches any path (it crosses ``/`` and whitespace); the bound is
    # the exact pinned destination, not the glob.
    probe_argv=(
        "/usr/bin/install",
        "-m",
        "0440",
        "-o",
        "root",
        "-g",
        "root",
        f"/tmp/{SUDOERS_TMP_PREFIX}{_PROBE_PLACEHOLDER}",  # nosec B108
        f"/etc/sudoers.d/{SUDOERS_DROP_IN_FILENAME}",
    ),
    description="Install the openfollow sudoers drop-in",
    sudoers_pattern=(
        f"/usr/bin/install -m 0440 -o root -g root /tmp/{SUDOERS_TMP_PREFIX}* /etc/sudoers.d/{SUDOERS_DROP_IN_FILENAME}"
    ),
)

DEVICE_TRUNCATE_LEGACY_SUDOERS = Capability(
    name="device.truncate_legacy_sudoers",
    # ``/dev/null`` is the exact source the actual call uses; the
    # destination is the rule's concrete path. No wildcard tokens in
    # this pattern, so the probe matches verbatim.
    probe_argv=(
        "/usr/bin/install",
        "-m",
        "0440",
        "-o",
        "root",
        "-g",
        "root",
        "/dev/null",
        f"/etc/sudoers.d/{LEGACY_SUDOERS_DROP_IN_FILENAME}",
    ),
    description="Truncate the legacy openfollow-update sudoers drop-in",
    sudoers_pattern=(
        f"/usr/bin/install -m 0440 -o root -g root /dev/null /etc/sudoers.d/{LEGACY_SUDOERS_DROP_IN_FILENAME}"
    ),
)

DEVICE_INSTALL_SYSTEMD_UNIT = Capability(
    name="device.install_systemd_unit",
    probe_argv=(
        "/usr/bin/install",
        "-m",
        "0644",
        "-o",
        "root",
        "-g",
        "root",
        f"/tmp/{SYSTEMD_UNIT_TMP_PREFIX}{_PROBE_PLACEHOLDER}.service",  # nosec B108
        f"/etc/systemd/system/{DEFAULT_SYSTEMD_UNIT_FILENAME}",
    ),
    description="Install the openfollow systemd unit",
    # The destination is hardcoded to the default service name. Operators
    # who install with a custom service name will be prompted per call
    # (the install action falls back to the broker's password path).
    sudoers_pattern=(
        f"/usr/bin/install -m 0644 -o root -g root "
        f"/tmp/{SYSTEMD_UNIT_TMP_PREFIX}*.service "
        f"/etc/systemd/system/{DEFAULT_SYSTEMD_UNIT_FILENAME}"
    ),
)

DEVICE_SYSTEMD_ANALYZE = Capability(
    name="device.systemd_analyze",
    probe_argv=("/usr/bin/systemd-analyze", "verify", _PROBE_PLACEHOLDER),
    description="Validate a systemd unit file",
    sudoers_pattern="/usr/bin/systemd-analyze verify *",
)

DEVICE_VISUDO_VALIDATE = Capability(
    name="device.visudo_validate",
    probe_argv=("/usr/sbin/visudo", "-cf", _PROBE_PLACEHOLDER),
    description="Validate a sudoers fragment with visudo -cf",
    sudoers_pattern="/usr/sbin/visudo -cf *",
)

# Anchored sudoers regex matching ONE ``@<epoch-seconds>`` token. The auto
# time-sync sets the clock to an absolute Unix instant; bounding the slot to
# ``@<digits>`` (not a ``*``) keeps the grant from being turned into an
# arbitrary ``date`` invocation that could set a time string with spaces /
# options. ``@`` makes ``date -s`` parse the value as absolute UTC seconds.
_EPOCH_ARG_RE = "^@[0-9]+$"

SYSTEM_SET_CLOCK = Capability(
    name="system.set_clock",
    probe_argv=("/usr/bin/date", "-s", _PROBE_PLACEHOLDER),
    description="Set the system clock from a trusted time source",
    # Anchored ``@<digits>`` regex, NOT a trailing ``*`` – see _EPOCH_ARG_RE.
    sudoers_pattern=f"/usr/bin/date -s {_EPOCH_ARG_RE}",
    arg_pattern=_EPOCH_ARG_RE,
    # The bare ``_`` placeholder can't satisfy the epoch regex, so probe with a
    # concrete ``@<digits>`` token or ``sudo -n -ll`` never matches the rule.
    probe_arg="@0",
)


# Registry of every capability the broker knows about. Ordering is the
# order capabilities appear in the diagnostics bundle's permissions section.
ALL_CAPABILITIES: tuple[Capability, ...] = (
    # credentials
    JOURNAL_GROUP_JOIN,
    # device setup
    DEVICE_GROUP_JOIN,
    DEVICE_LINGER_ENABLE,
    DEVICE_SET_HOSTNAME,
    DEVICE_HOSTS_WRITE,
    SERVICE_ENABLE,
    SERVICE_DISABLE,
    SERVICE_MASK,
    SERVICE_DAEMON_RELOAD,
    DEVICE_INSTALL_SUDOERS_DROPIN,
    DEVICE_TRUNCATE_LEGACY_SUDOERS,
    DEVICE_INSTALL_SYSTEMD_UNIT,
    DEVICE_SYSTEMD_ANALYZE,
    DEVICE_VISUDO_VALIDATE,
    # service control + package update
    SERVICE_RESTART,
    PACKAGE_SELF_UPDATE,
    # system clock (auto time-sync)
    SYSTEM_SET_CLOCK,
    # network apply
    NETWORK_DHCPCD_RENEW,
    NETWORK_DHCPCD_RELEASE,
    NETWORK_NM_CON_MOD,
    NETWORK_NM_CON_UP,
    NETWORK_NM_CON_DOWN,
    NETWORK_DHCPCD_CONF_WRITE_TMP,
    NETWORK_DHCPCD_CONF_COMMIT,
    NETWORK_DHCPCD_RELOAD,
    # logs
    LOG_READ_SUDO,
)


def capability_by_name(name: str) -> Capability | None:
    """Look up a capability by its stable name. Returns ``None`` if
    no capability with that name is registered (caller decides how to
    handle the miss – fail-closed or fall back)."""
    for cap in ALL_CAPABILITIES:
        if cap.name == name:
            return cap
    return None
