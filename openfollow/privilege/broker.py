# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Privilege broker: TTL cache + passwordless-first with prompt fallback."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Final

from openfollow.privilege.capabilities import (
    Capability,
    CapabilityState,
)

logger = logging.getLogger(__name__)


_PROBE_TIMEOUT_S: Final[float] = 15.0
_DEFAULT_RUN_TIMEOUT_S: Final[float] = 30.0
_CACHE_TTL_S: Final[float] = 60.0

# Locale + prompt-suppressors for deterministic sudo behavior.
_ENV_OVERRIDES: Final[dict[str, str]] = {
    "LC_ALL": "C",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "/bin/true",
    "GCM_INTERACTIVE": "Never",
}

# Standard sudo stderr for password prompt (English via LC_ALL=C).
_PASSWORD_REQUIRED_MARKER: Final[str] = "a password is required"

# Stderr signature when the probed binary isn't installed (e.g.
# ``sudo: /usr/bin/nmcli: command not found``). Marks the host UNAVAILABLE,
# not NEEDS_PASSWORD – a password can't conjure a missing binary.
_COMMAND_NOT_FOUND_MARKER: Final[str] = "command not found"

# Sudo NOPASSWD token (appears on Options: line, never in user content).
_NOPASSWD_OPTION_TOKEN: Final[str] = "!authenticate"


def _has_nopasswd_option(listing: str) -> bool:
    """Return True if sudo -n -ll output shows NOPASSWD on Options: line."""
    for line in listing.splitlines():
        stripped = line.strip()
        if stripped.startswith("Options:") and _NOPASSWD_OPTION_TOKEN in stripped:
            return True
    return False


class PrivilegeError(RuntimeError):
    """Raised when privileged operation fails (missing sudo, cancelled, or exit error)."""


Prompter = Callable[[Capability, str], "str | None"]
"""Callable to prompt for password: (capability, reason) -> password or None."""


class PrivilegeBroker:
    """Probe and run privileged operations with on-demand password prompts (thread-safe)."""

    def __init__(self, *, prompter: Prompter | None = None) -> None:
        self._prompter: Prompter | None = prompter
        self._cache: dict[str, tuple[float, CapabilityState]] = {}
        self._lock = threading.Lock()

    # ----- prompter ------------------------------------------------------

    def set_prompter(self, prompter: Prompter | None) -> None:
        """Replace the prompter callback. ``None`` clears it (any
        capability that resolves to ``NEEDS_PASSWORD`` then raises
        :class:`PrivilegeError` instead of silently hanging)."""
        self._prompter = prompter

    # ----- state cache ---------------------------------------------------

    def invalidate(self, capability: Capability | None = None) -> None:
        """Drop a cache entry (or the whole cache when ``capability``
        is ``None``). Called by :meth:`run` when sudo returns
        "password required" despite a cached PASSWORDLESS verdict, and
        by the sudoers-drop-in installer after a successful write."""
        with self._lock:
            if capability is None:
                self._cache.clear()
            else:
                self._cache.pop(capability.name, None)

    def state(self, capability: Capability) -> CapabilityState:
        """Return cached capability state, probing if necessary.

        Returns PASSWORDLESS, NEEDS_PASSWORD, or UNAVAILABLE (cached for _CACHE_TTL_S).
        """
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(capability.name)
            if cached is not None and now - cached[0] < _CACHE_TTL_S:
                return cached[1]

        probed = self._probe(capability)

        with self._lock:
            self._cache[capability.name] = (time.monotonic(), probed)
        return probed

    def states(self) -> dict[str, CapabilityState]:
        """Return a snapshot of the state for every known capability.

        Triggers a probe for any capability not currently cached.
        Used by the Privileges page to render the full row list in
        one pass.
        """
        from openfollow.privilege.capabilities import ALL_CAPABILITIES

        return {cap.name: self.state(cap) for cap in ALL_CAPABILITIES}

    # ----- probe ---------------------------------------------------------

    def _probe(self, capability: Capability) -> CapabilityState:
        """Probe sudo for NOPASSWD state via sudo -n -ll (parse !authenticate token).

        Never raises (errors → UNAVAILABLE).
        """
        if shutil.which("sudo") is None:
            return CapabilityState.UNAVAILABLE

        env = {**os.environ, **_ENV_OVERRIDES}
        try:
            probe = subprocess.run(
                ["sudo", "-n", "-ll", *capability.probe_argv],
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT_S,
                check=False,
                env=env,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return CapabilityState.UNAVAILABLE

        if probe.returncode == 0:
            # Parse !authenticate token from Options line (present only for NOPASSWD rules).
            if _has_nopasswd_option(probe.stdout):
                return CapabilityState.PASSWORDLESS
            return CapabilityState.NEEDS_PASSWORD

        stderr = (probe.stderr or "").strip().lower()
        # Binary not installed – UNAVAILABLE, not NEEDS_PASSWORD.
        if _COMMAND_NOT_FOUND_MARKER in stderr:
            return CapabilityState.UNAVAILABLE

        # Check for "a password is required" marker.
        if _PASSWORD_REQUIRED_MARKER in stderr:
            return CapabilityState.NEEDS_PASSWORD

        # No covering rule but user might be in sudo group; surface as NEEDS_PASSWORD.
        return CapabilityState.NEEDS_PASSWORD

    # ----- run -----------------------------------------------------------

    def run(
        self,
        capability: Capability,
        argv: list[str],
        *,
        cwd: str | None = None,
        timeout: float = _DEFAULT_RUN_TIMEOUT_S,
        reason: str = "",
        stdin: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run sudo argv for capability, prompting if needed. Raises PrivilegeError on failure."""
        # Defence-in-depth: in-process allow-set must match the sudoers rule,
        # so a caller can't pair a capability with an arbitrary argv.
        try:
            capability.assert_argv_allowed(argv)
        except ValueError as exc:
            raise PrivilegeError(f"{capability.description}: {exc}") from exc

        env = {**os.environ, **_ENV_OVERRIDES}
        run_cwd = cwd or "/"

        cached_state = self.state(capability)
        if cached_state == CapabilityState.UNAVAILABLE:
            raise PrivilegeError(f"{capability.description}: sudo is unavailable on this host.")

        # Try non-interactive first regardless of cached_state (warm timestamp cache helps).
        try:
            proc = subprocess.run(
                ["sudo", "-n", *argv],
                cwd=run_cwd,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise PrivilegeError(f"{capability.description}: timed out after {timeout:g}s.") from exc
        if proc.returncode == 0:
            return proc
        stderr = (proc.stderr or "").strip()
        if _PASSWORD_REQUIRED_MARKER not in stderr.lower():
            # Non-password failure (command exited non-zero or user not in sudoers).
            raise PrivilegeError(_format_failure(capability, proc))
        # Invalidate cache; re-probe after password succeeds.
        self.invalidate(capability)

        # Prompt for password and retry.
        if self._prompter is None:
            raise PrivilegeError(f"{capability.description}: password required but no prompter is registered.")

        password = self._prompter(capability, reason or capability.description)
        if password is None:
            raise PrivilegeError(f"{capability.description}: cancelled or timed out waiting for the device password.")

        try:
            return self._run_with_password(
                capability,
                argv,
                password,
                cwd=run_cwd,
                timeout=timeout,
                env=env,
                extra_stdin=stdin,
            )
        finally:
            # Best-effort wipe of the locally-held password. CPython
            # interns short strings so this is defence-in-depth, but
            # the frame reference is gone either way.
            password = ""  # noqa: F841

    def _run_with_password(
        self,
        capability: Capability,
        argv: list[str],
        password: str,
        *,
        cwd: str,
        timeout: float,
        env: dict[str, str],
        extra_stdin: str | None,
    ) -> subprocess.CompletedProcess[str]:
        """Pipe password to sudo -S, then extra_stdin if any. -p '' suppresses sudo's prompt."""
        payload = password + "\n"
        if extra_stdin is not None:
            payload += extra_stdin
        try:
            proc = subprocess.run(
                ["sudo", "-S", "-p", "", *argv],
                cwd=cwd,
                input=payload,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise PrivilegeError(f"{capability.description}: timed out after {timeout:g}s.") from exc
        if proc.returncode == 0:
            # Successful; caller invalidates cache if needed.
            return proc
        raise PrivilegeError(_format_failure(capability, proc))


def _format_failure(
    capability: Capability,
    proc: subprocess.CompletedProcess[str],
) -> str:
    """Build a human-readable error string for a non-zero sudo run."""
    detail = (proc.stderr or proc.stdout or "").strip()
    if not detail:
        detail = f"Command exited with code {proc.returncode}."
    lowered = detail.lower()
    if "incorrect password" in lowered or "sorry, try again" in lowered:
        detail = "Incorrect password."
    elif "not in the sudoers file" in lowered:
        detail = "This user is not in the sudoers file."
    return f"{capability.description}: {detail}"
