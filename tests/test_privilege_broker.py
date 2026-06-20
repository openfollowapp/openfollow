# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for openfollow.privilege.broker."""

from __future__ import annotations

import subprocess

import pytest

from openfollow.privilege import PrivilegeBroker, PrivilegeError
from openfollow.privilege.broker import (
    _COMMAND_NOT_FOUND_MARKER,
    _PASSWORD_REQUIRED_MARKER,
)
from openfollow.privilege.capabilities import (
    DEVICE_GROUP_JOIN,
    NETWORK_DHCPCD_CONF_WRITE_TMP,
    NETWORK_NM_CON_MOD,
    REQUIRED_HARDWARE_GROUPS_JOINED,
    SERVICE_RESTART,
    Capability,
    CapabilityState,
)

pytestmark = pytest.mark.unit

# ---------- helpers ---------------------------------------------------------


def _fake_subprocess_factory(handler):
    """Build a ``subprocess.run`` replacement that delegates to
    ``handler(argv, *, input, env, ...)`` and returns a ``CompletedProcess``.

    ``handler`` may return either a ``CompletedProcess`` or a tuple
    ``(rc, stdout, stderr)`` for brevity. Returning ``None`` raises
    ``FileNotFoundError`` to simulate a missing binary.
    """

    def _runner(argv, **kwargs):
        result = handler(list(argv), **kwargs)
        if result is None:
            raise FileNotFoundError(argv[0])
        if isinstance(result, tuple):
            rc, stdout, stderr = result
            return subprocess.CompletedProcess(argv, rc, stdout, stderr)
        return result

    return _runner


@pytest.fixture
def broker():
    return PrivilegeBroker()


# ---------- probing --------------------------------------------------------

_NOPASSWD_LISTING = (
    "Sudoers entry: /etc/sudoers.d/openfollow-privileged\n"
    "    RunAsUsers: root\n"
    "    Options: !authenticate\n"
    "    Commands:\n"
    "\t/usr/bin/systemctl restart *\n"
    "    Matched: /usr/bin/systemctl restart openfollow\n"
)

_PASSWORD_LISTING = (
    "Sudoers entry: /etc/sudoers\n"
    "    RunAsUsers: ALL\n"
    "    RunAsGroups: ALL\n"
    "    Commands:\n"
    "\tALL\n"
    "    Matched: /usr/bin/systemctl restart\n"
)


class TestProbe:
    def test_passwordless_when_listing_marks_nopasswd(self, broker, monkeypatch) -> None:
        """``sudo -n -ll <argv>`` rc=0 with ``!authenticate`` on the
        matched rule's Options line == NOPASSWD coverage."""
        monkeypatch.setattr(
            "openfollow.privilege.broker.shutil.which",
            lambda name: "/usr/bin/sudo",
        )
        monkeypatch.setattr(
            "openfollow.privilege.broker.subprocess.run",
            _fake_subprocess_factory(lambda argv, **kw: (0, _NOPASSWD_LISTING, "")),
        )
        assert broker.state(SERVICE_RESTART) == CapabilityState.PASSWORDLESS

    def test_needs_password_when_listing_lacks_nopasswd(self, broker, monkeypatch) -> None:
        """Operator has a catch-all ``(ALL : ALL) ALL`` rule that
        matches with rc=0 but without ``!authenticate`` – sudo still
        demands a password. This discriminates password-required from
        passwordless coverage."""
        monkeypatch.setattr(
            "openfollow.privilege.broker.shutil.which",
            lambda name: "/usr/bin/sudo",
        )
        monkeypatch.setattr(
            "openfollow.privilege.broker.subprocess.run",
            _fake_subprocess_factory(lambda argv, **kw: (0, _PASSWORD_LISTING, "")),
        )
        assert broker.state(SERVICE_RESTART) == CapabilityState.NEEDS_PASSWORD

    def test_passwordless_token_must_be_on_options_line(self, broker, monkeypatch) -> None:
        listing = "Sudoers entry: /etc/sudoers\n    Commands:\n\t/bin/echo '!authenticate'\n    Matched: /bin/echo\n"
        monkeypatch.setattr(
            "openfollow.privilege.broker.shutil.which",
            lambda name: "/usr/bin/sudo",
        )
        monkeypatch.setattr(
            "openfollow.privilege.broker.subprocess.run",
            _fake_subprocess_factory(lambda argv, **kw: (0, listing, "")),
        )
        assert broker.state(SERVICE_RESTART) == CapabilityState.NEEDS_PASSWORD

    def test_needs_password_when_marker_in_stderr(self, broker, monkeypatch) -> None:
        """Sudo writes the canonical "a password is required" stderr
        line when ``-n`` would otherwise prompt. The broker treats
        that as ``NEEDS_PASSWORD`` rather than ``UNAVAILABLE``."""
        monkeypatch.setattr(
            "openfollow.privilege.broker.shutil.which",
            lambda name: "/usr/bin/sudo",
        )
        monkeypatch.setattr(
            "openfollow.privilege.broker.subprocess.run",
            _fake_subprocess_factory(
                lambda argv, **kw: (1, "", f"sudo: {_PASSWORD_REQUIRED_MARKER}\n"),
            ),
        )
        assert broker.state(SERVICE_RESTART) == CapabilityState.NEEDS_PASSWORD

    def test_unavailable_when_binary_not_installed(self, broker, monkeypatch) -> None:
        """``sudo -ll`` resolves the command path before listing, so a
        capability whose binary the host doesn't ship (e.g. nmcli on a
        dhcpcd-only host, or dhcpcd on a NetworkManager-only image)
        exits non-zero with "command not found". That's UNAVAILABLE –
        the NOPASSWD rule may exist but there's nothing to run – not
        NEEDS_PASSWORD, which would mislead the operator into thinking
        a password would unblock it."""
        monkeypatch.setattr(
            "openfollow.privilege.broker.shutil.which",
            lambda name: "/usr/bin/sudo",
        )
        monkeypatch.setattr(
            "openfollow.privilege.broker.subprocess.run",
            _fake_subprocess_factory(
                lambda argv, **kw: (1, "", f"sudo: /usr/bin/nmcli: {_COMMAND_NOT_FOUND_MARKER}\n"),
            ),
        )
        assert broker.state(NETWORK_NM_CON_MOD) == CapabilityState.UNAVAILABLE

    def test_needs_password_without_marker_falls_through(self, broker, monkeypatch) -> None:
        """When sudo -n -ll fails *without* the marker (no rule at
        all), the broker optimistically surfaces NEEDS_PASSWORD – the
        operator's account might still be in ``sudo`` group and able
        to authenticate. A real "not in sudoers" error surfaces from
        ``run`` after the prompt."""
        monkeypatch.setattr(
            "openfollow.privilege.broker.shutil.which",
            lambda name: "/usr/bin/sudo",
        )
        monkeypatch.setattr(
            "openfollow.privilege.broker.subprocess.run",
            _fake_subprocess_factory(lambda argv, **kw: (1, "", "no rule")),
        )
        assert broker.state(SERVICE_RESTART) == CapabilityState.NEEDS_PASSWORD

    def test_unavailable_when_sudo_missing(self, broker, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.broker.shutil.which",
            lambda name: None,
        )
        assert broker.state(SERVICE_RESTART) == CapabilityState.UNAVAILABLE

    def test_unavailable_when_probe_raises(self, broker, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.broker.shutil.which",
            lambda name: "/usr/bin/sudo",
        )

        def _boom(argv, **kw):
            raise subprocess.TimeoutExpired(argv, 1)

        monkeypatch.setattr(
            "openfollow.privilege.broker.subprocess.run",
            _boom,
        )
        assert broker.state(SERVICE_RESTART) == CapabilityState.UNAVAILABLE

    def test_state_uses_cache_within_ttl(self, broker, monkeypatch) -> None:
        """Once probed, ``state`` returns the cached verdict without
        re-running ``sudo -n -l`` on every call."""
        monkeypatch.setattr(
            "openfollow.privilege.broker.shutil.which",
            lambda name: "/usr/bin/sudo",
        )
        calls = {"n": 0}

        def _run(argv, **kw):
            calls["n"] += 1
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr("openfollow.privilege.broker.subprocess.run", _run)
        broker.state(SERVICE_RESTART)
        broker.state(SERVICE_RESTART)
        broker.state(SERVICE_RESTART)
        assert calls["n"] == 1

    def test_invalidate_forces_reprobe(self, broker, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.broker.shutil.which",
            lambda name: "/usr/bin/sudo",
        )
        calls = {"n": 0}

        def _run(argv, **kw):
            calls["n"] += 1
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr("openfollow.privilege.broker.subprocess.run", _run)
        broker.state(SERVICE_RESTART)
        broker.invalidate(SERVICE_RESTART)
        broker.state(SERVICE_RESTART)
        assert calls["n"] == 2

    def test_invalidate_all_clears_every_entry(self, broker, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.broker.shutil.which",
            lambda name: "/usr/bin/sudo",
        )
        monkeypatch.setattr(
            "openfollow.privilege.broker.subprocess.run",
            _fake_subprocess_factory(lambda argv, **kw: (0, "", "")),
        )
        broker.state(SERVICE_RESTART)
        broker.state(NETWORK_NM_CON_MOD)
        broker.invalidate()  # all
        # After invalidate(), states() re-probes every capability.
        # The internal cache dict is empty at this point.
        assert broker._cache == {}


# ---------- run -------------------------------------------------------------


class TestRunPasswordless:
    def test_runs_with_sudo_n_and_returns_completed_process(self, broker, monkeypatch) -> None:
        """Happy path: capability is PASSWORDLESS, ``sudo -n <argv>``
        runs cleanly and the broker returns the ``CompletedProcess``."""
        broker._cache[SERVICE_RESTART.name] = (
            __import__("time").monotonic(),
            CapabilityState.PASSWORDLESS,
        )
        seen: list[list[str]] = []

        def _run(argv, **kw):
            seen.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, "ok", "")

        monkeypatch.setattr("openfollow.privilege.broker.subprocess.run", _run)
        proc = broker.run(
            SERVICE_RESTART,
            ["/usr/bin/systemctl", "restart", "openfollow"],
        )
        assert proc.returncode == 0
        assert proc.stdout == "ok"
        assert seen[0][:2] == ["sudo", "-n"]
        assert seen[0][2:] == ["/usr/bin/systemctl", "restart", "openfollow"]

    def test_stale_passwordless_invalidates_and_prompts(self, broker, monkeypatch) -> None:
        broker._cache[SERVICE_RESTART.name] = (
            __import__("time").monotonic(),
            CapabilityState.PASSWORDLESS,
        )
        attempts = {"n": 0}

        def _run(argv, **kw):
            attempts["n"] += 1
            if attempts["n"] == 1:
                # First (PASSWORDLESS-path) attempt – return marker.
                return subprocess.CompletedProcess(
                    argv,
                    1,
                    "",
                    f"sudo: {_PASSWORD_REQUIRED_MARKER}\n",
                )
            # Retry with ``sudo -S`` after the prompter – succeed.
            return subprocess.CompletedProcess(argv, 0, "ok", "")

        monkeypatch.setattr("openfollow.privilege.broker.subprocess.run", _run)

        prompted: list[str] = []

        def _prompter(cap, reason):
            prompted.append(cap.name)
            return "hunter2"

        broker.set_prompter(_prompter)
        proc = broker.run(
            SERVICE_RESTART,
            ["/usr/bin/systemctl", "restart", "openfollow"],
        )
        assert proc.returncode == 0
        assert prompted == [SERVICE_RESTART.name]
        # Cache was dropped so the next call re-probes.
        assert SERVICE_RESTART.name not in broker._cache

    def test_nonzero_nonpassword_failure_raises(self, broker, monkeypatch) -> None:
        broker._cache[SERVICE_RESTART.name] = (
            __import__("time").monotonic(),
            CapabilityState.PASSWORDLESS,
        )

        def _run(argv, **kw):
            return subprocess.CompletedProcess(argv, 5, "", "unit not found")

        monkeypatch.setattr("openfollow.privilege.broker.subprocess.run", _run)
        with pytest.raises(PrivilegeError, match="unit not found"):
            broker.run(SERVICE_RESTART, ["/usr/bin/systemctl", "restart", "x"])


def _make_two_phase_runner(prompt_then_succeed_input: list[str | None]) -> object:
    """Return a fake ``subprocess.run`` that simulates the broker's
    two-phase flow: the first call (``sudo -n``) fails with the
    password-required marker, the second call (``sudo -S``) succeeds.

    ``prompt_then_succeed_input`` is mutated with the ``input=`` kwarg
    of each invocation so callers can assert on what the broker piped
    to ``sudo -S`` after collecting the password.
    """
    calls = {"n": 0}

    def _run(argv, *, input=None, **kw):
        prompt_then_succeed_input.append(input)
        calls["n"] += 1
        if calls["n"] == 1:
            # First call: sudo -n (no password available). Return the
            # canonical "password is required" stderr so the broker
            # falls through to its prompter.
            return subprocess.CompletedProcess(
                argv,
                1,
                "",
                f"sudo: {_PASSWORD_REQUIRED_MARKER}\n",
            )
        # Second call: sudo -S with the password – succeed.
        return subprocess.CompletedProcess(argv, 0, "", "")

    return _run


class TestRunNeedsPassword:
    def test_prompts_and_pipes_password(self, broker, monkeypatch) -> None:
        """``broker.run()`` tries ``sudo -n`` first so a warm sudo
        timestamp short-circuits the prompter. When ``-n`` fails with
        the password-required marker, it calls the prompter and pipes
        the collected password through ``sudo -S``."""
        broker._cache[NETWORK_NM_CON_MOD.name] = (
            __import__("time").monotonic(),
            CapabilityState.NEEDS_PASSWORD,
        )
        seen_stdin: list[str | None] = []
        monkeypatch.setattr(
            "openfollow.privilege.broker.subprocess.run",
            _make_two_phase_runner(seen_stdin),
        )
        broker.set_prompter(lambda cap, reason: "hunter2")
        broker.run(NETWORK_NM_CON_MOD, ["/usr/bin/nmcli", "con", "mod", "x"])
        # First entry is ``sudo -n``'s input (None – no stdin), second
        # is ``sudo -S``'s input (password + newline).
        assert seen_stdin == [None, "hunter2\n"]

    def test_password_attempt_timeout_raises_privilege_error(
        self,
        broker,
        monkeypatch,
    ) -> None:
        """``sudo -S`` taking longer than the call's ``timeout=`` is
        almost always pam or pamd misbehaviour – surface it as a
        clean PrivilegeError instead of letting the
        ``TimeoutExpired`` propagate out of the broker."""
        broker._cache[NETWORK_NM_CON_MOD.name] = (
            __import__("time").monotonic(),
            CapabilityState.NEEDS_PASSWORD,
        )
        calls = {"n": 0}

        def _run(argv, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                # First call: sudo -n returns the password-required
                # marker so the broker falls through to the prompt
                # path and we exercise ``_run_with_password``.
                return subprocess.CompletedProcess(
                    argv,
                    1,
                    "",
                    f"sudo: {_PASSWORD_REQUIRED_MARKER}\n",
                )
            raise subprocess.TimeoutExpired(argv, kw.get("timeout", 30))

        monkeypatch.setattr("openfollow.privilege.broker.subprocess.run", _run)
        broker.set_prompter(lambda cap, reason: "hunter2")
        with pytest.raises(PrivilegeError, match="timed out"):
            broker.run(NETWORK_NM_CON_MOD, ["/usr/bin/nmcli", "con", "mod", "x"])

    def test_password_attempt_nonzero_raises_with_failure_format(
        self,
        broker,
        monkeypatch,
    ) -> None:
        """``sudo -S`` exiting non-zero (typed-correct password but the
        underlying command itself failed) is wrapped via
        ``_format_failure`` so adapter callers can read the same
        stderr-rewriting that the no-prompt path produces."""
        broker._cache[NETWORK_NM_CON_MOD.name] = (
            __import__("time").monotonic(),
            CapabilityState.NEEDS_PASSWORD,
        )
        calls = {"n": 0}

        def _run(argv, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return subprocess.CompletedProcess(
                    argv,
                    1,
                    "",
                    f"sudo: {_PASSWORD_REQUIRED_MARKER}\n",
                )
            return subprocess.CompletedProcess(argv, 2, "", "nmcli: not found")

        monkeypatch.setattr("openfollow.privilege.broker.subprocess.run", _run)
        broker.set_prompter(lambda cap, reason: "hunter2")
        with pytest.raises(PrivilegeError, match="nmcli: not found"):
            broker.run(NETWORK_NM_CON_MOD, ["/usr/bin/nmcli", "con", "mod", "x"])

    def test_warm_sudo_cache_skips_prompt(self, broker, monkeypatch) -> None:
        broker._cache[NETWORK_NM_CON_MOD.name] = (
            __import__("time").monotonic(),
            CapabilityState.NEEDS_PASSWORD,
        )

        def _run(argv, **kw):
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr("openfollow.privilege.broker.subprocess.run", _run)
        prompter_called = {"n": 0}

        def _prompter(cap, reason):
            prompter_called["n"] += 1
            return "should-not-be-used"

        broker.set_prompter(_prompter)
        broker.run(NETWORK_NM_CON_MOD, ["/usr/bin/nmcli", "con", "mod", "x"])
        assert prompter_called["n"] == 0

    def test_cancel_raises_privilege_error(self, broker, monkeypatch) -> None:
        """``broker.run()`` calls ``sudo -n`` before consulting the
        prompter. Mock it to return the password-required marker so
        the flow reaches the prompter and its ``None`` cancel response."""
        broker._cache[NETWORK_NM_CON_MOD.name] = (
            __import__("time").monotonic(),
            CapabilityState.NEEDS_PASSWORD,
        )
        monkeypatch.setattr(
            "openfollow.privilege.broker.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(
                a[0] if a else [],
                1,
                "",
                f"sudo: {_PASSWORD_REQUIRED_MARKER}\n",
            ),
        )
        broker.set_prompter(lambda cap, reason: None)
        with pytest.raises(PrivilegeError, match="cancelled or timed out"):
            broker.run(NETWORK_NM_CON_MOD, ["/usr/bin/nmcli", "con", "mod", "x"])

    def test_missing_prompter_raises(self, broker, monkeypatch) -> None:
        broker._cache[NETWORK_NM_CON_MOD.name] = (
            __import__("time").monotonic(),
            CapabilityState.NEEDS_PASSWORD,
        )
        monkeypatch.setattr(
            "openfollow.privilege.broker.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(
                a[0] if a else [],
                1,
                "",
                f"sudo: {_PASSWORD_REQUIRED_MARKER}\n",
            ),
        )
        broker.set_prompter(None)
        with pytest.raises(PrivilegeError, match="no prompter is registered"):
            broker.run(NETWORK_NM_CON_MOD, ["/usr/bin/nmcli", "con", "mod", "x"])

    def test_incorrect_password_error_is_rewritten(self, broker, monkeypatch) -> None:
        """Sudo's raw stderr ('1 incorrect password attempt') becomes
        a clearer operator-facing 'Incorrect password.' message."""
        broker._cache[NETWORK_NM_CON_MOD.name] = (
            __import__("time").monotonic(),
            CapabilityState.NEEDS_PASSWORD,
        )

        def _run(argv, **kw):
            return subprocess.CompletedProcess(
                argv,
                1,
                "",
                "Sorry, try again.\n1 incorrect password attempt",
            )

        monkeypatch.setattr("openfollow.privilege.broker.subprocess.run", _run)
        broker.set_prompter(lambda cap, reason: "wrong")
        with pytest.raises(PrivilegeError, match="Incorrect password"):
            broker.run(NETWORK_NM_CON_MOD, ["/usr/bin/nmcli", "con", "mod", "x"])

    def test_not_in_sudoers_error_is_rewritten(self, broker, monkeypatch) -> None:
        broker._cache[NETWORK_NM_CON_MOD.name] = (
            __import__("time").monotonic(),
            CapabilityState.NEEDS_PASSWORD,
        )

        def _run(argv, **kw):
            return subprocess.CompletedProcess(
                argv,
                1,
                "",
                "user is not in the sudoers file",
            )

        monkeypatch.setattr("openfollow.privilege.broker.subprocess.run", _run)
        broker.set_prompter(lambda cap, reason: "x")
        with pytest.raises(PrivilegeError, match="not in the sudoers file"):
            broker.run(NETWORK_NM_CON_MOD, ["/usr/bin/nmcli", "con", "mod", "x"])

    def test_timeout_raises_privilege_error(self, broker, monkeypatch) -> None:
        broker._cache[NETWORK_NM_CON_MOD.name] = (
            __import__("time").monotonic(),
            CapabilityState.NEEDS_PASSWORD,
        )

        def _run(argv, **kw):
            raise subprocess.TimeoutExpired(argv, 1)

        monkeypatch.setattr("openfollow.privilege.broker.subprocess.run", _run)
        broker.set_prompter(lambda cap, reason: "x")
        with pytest.raises(PrivilegeError, match="timed out"):
            broker.run(
                NETWORK_NM_CON_MOD,
                ["/usr/bin/nmcli", "con", "mod", "x"],
                timeout=1,
            )

    def test_passwordless_timeout_raises(self, broker, monkeypatch) -> None:
        broker._cache[SERVICE_RESTART.name] = (
            __import__("time").monotonic(),
            CapabilityState.PASSWORDLESS,
        )

        def _run(argv, **kw):
            raise subprocess.TimeoutExpired(argv, 1)

        monkeypatch.setattr("openfollow.privilege.broker.subprocess.run", _run)
        with pytest.raises(PrivilegeError, match="timed out"):
            broker.run(
                SERVICE_RESTART,
                ["/usr/bin/systemctl", "restart", "x"],
                timeout=1,
            )


class TestRunUnavailable:
    def test_unavailable_raises_immediately(self, broker, monkeypatch) -> None:
        broker._cache[SERVICE_RESTART.name] = (
            __import__("time").monotonic(),
            CapabilityState.UNAVAILABLE,
        )
        with pytest.raises(PrivilegeError, match="sudo is unavailable"):
            broker.run(SERVICE_RESTART, ["/usr/bin/systemctl", "restart", "x"])


class TestStdinPassthrough:
    def test_extra_stdin_follows_password(self, broker, monkeypatch) -> None:
        """``stdin=`` payload follows the password line. The broker
        tries ``sudo -n`` first (also receiving stdin), then on marker
        fallback to the prompter, ``sudo -S`` carries password + stdin."""
        # tee-to-dhcpcd.conf.tmp is the real stdin-bearing capability; argv
        # must match its probe shape now that the broker gates argv.
        broker._cache[NETWORK_DHCPCD_CONF_WRITE_TMP.name] = (
            __import__("time").monotonic(),
            CapabilityState.NEEDS_PASSWORD,
        )
        seen: list[str | None] = []
        monkeypatch.setattr(
            "openfollow.privilege.broker.subprocess.run",
            _make_two_phase_runner(seen),
        )
        broker.set_prompter(lambda cap, reason: "hunter2")
        broker.run(
            NETWORK_DHCPCD_CONF_WRITE_TMP,
            ["/usr/bin/tee", "/etc/dhcpcd.conf.tmp"],
            stdin="line1\nline2\n",
        )
        # First entry is the ``sudo -n`` attempt's stdin (the broker
        # threads the caller's ``stdin=`` through there too so a
        # warm-cache hit can still feed the command); second entry is
        # the ``sudo -S`` retry with password + caller's stdin
        # concatenated.
        assert seen == ["line1\nline2\n", "hunter2\nline1\nline2\n"]


class TestStates:
    def test_states_returns_one_entry_per_capability(self, broker, monkeypatch) -> None:
        from openfollow.privilege.capabilities import ALL_CAPABILITIES

        monkeypatch.setattr(
            "openfollow.privilege.broker.shutil.which",
            lambda name: "/usr/bin/sudo",
        )
        monkeypatch.setattr(
            "openfollow.privilege.broker.subprocess.run",
            _fake_subprocess_factory(lambda argv, **kw: (0, "", "")),
        )
        snapshot = broker.states()
        assert set(snapshot.keys()) == {c.name for c in ALL_CAPABILITIES}


class TestFormatFailure:
    def test_blank_stderr_falls_back_to_return_code(self, broker, monkeypatch) -> None:
        broker._cache[SERVICE_RESTART.name] = (
            __import__("time").monotonic(),
            CapabilityState.PASSWORDLESS,
        )

        def _run(argv, **kw):
            return subprocess.CompletedProcess(argv, 7, "", "")

        monkeypatch.setattr("openfollow.privilege.broker.subprocess.run", _run)
        with pytest.raises(PrivilegeError, match="exited with code 7"):
            broker.run(SERVICE_RESTART, ["/usr/bin/systemctl", "restart", "x"])


class TestArgvShapeGate:
    """``run`` rejects argv that doesn't match the capability's declared
    shape *before* invoking sudo (defence-in-depth for the usermod
    sudo-group escalation)."""

    def _no_sudo(self, monkeypatch) -> None:
        # If the gate ever lets the call through, this blows up loudly
        # instead of silently invoking a real sudo.
        def _boom(*a, **kw):
            raise AssertionError("subprocess.run should not be reached")

        monkeypatch.setattr("openfollow.privilege.broker.subprocess.run", _boom)

    def test_rejects_injected_sudo_group_argv(self, broker, monkeypatch) -> None:
        broker._cache[DEVICE_GROUP_JOIN.name] = (
            __import__("time").monotonic(),
            CapabilityState.PASSWORDLESS,
        )
        self._no_sudo(monkeypatch)
        with pytest.raises(PrivilegeError, match="single token matching"):
            broker.run(
                DEVICE_GROUP_JOIN,
                ["/usr/sbin/usermod", "-aG", REQUIRED_HARDWARE_GROUPS_JOINED, "-aG", "sudo", "root"],
            )

    def test_rejects_wrong_binary_argv(self, broker, monkeypatch) -> None:
        broker._cache[SERVICE_RESTART.name] = (
            __import__("time").monotonic(),
            CapabilityState.PASSWORDLESS,
        )
        self._no_sudo(monkeypatch)
        with pytest.raises(PrivilegeError, match="fixed prefix"):
            broker.run(SERVICE_RESTART, ["/usr/sbin/usermod", "-aG", "sudo", "root"])

    def test_accepts_legitimate_group_join(self, broker, monkeypatch) -> None:
        broker._cache[DEVICE_GROUP_JOIN.name] = (
            __import__("time").monotonic(),
            CapabilityState.PASSWORDLESS,
        )
        monkeypatch.setattr(
            "openfollow.privilege.broker.subprocess.run",
            _fake_subprocess_factory(lambda argv, **kw: (0, "", "")),
        )
        proc = broker.run(
            DEVICE_GROUP_JOIN,
            ["/usr/sbin/usermod", "-aG", REQUIRED_HARDWARE_GROUPS_JOINED, "pi"],
        )
        assert proc.returncode == 0


class TestUnknownCapabilityHandling:
    def test_run_with_unknown_capability_still_works(self, broker, monkeypatch) -> None:
        bespoke = Capability(
            name="bespoke.test",
            probe_argv=("/bin/true",),
            description="bespoke",
            sudoers_pattern="/bin/true",
        )
        broker._cache[bespoke.name] = (
            __import__("time").monotonic(),
            CapabilityState.PASSWORDLESS,
        )
        monkeypatch.setattr(
            "openfollow.privilege.broker.subprocess.run",
            _fake_subprocess_factory(lambda argv, **kw: (0, "", "")),
        )
        proc = broker.run(bespoke, ["/bin/true"])
        assert proc.returncode == 0
