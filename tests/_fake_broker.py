# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Test double for :class:`openfollow.privilege.PrivilegeBroker`.

Records calls and returns canned :class:`subprocess.CompletedProcess`
results so adapter / installer tests can assert on argv without
spawning a real ``sudo`` subprocess.

Default behaviour: every :meth:`FakeBroker.run` returns rc=0 with
empty stdout/stderr. Override by appending to :attr:`responses` (per-call
queue) or :attr:`exceptions` (per-call PrivilegeError). Both FIFO.
FakeBroker.run mirrors real broker: non-zero responses converted to raised
PrivilegeError. Use make_failure in exceptions to seed failure paths
(preferred), or make_nonzero in responses
for the legacy spelling – both surface the same way at the call site.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Any

from openfollow.privilege.broker import PrivilegeError, _format_failure
from openfollow.privilege.capabilities import (
    Capability,
    CapabilityState,
)


@dataclass
class RecordedCall:
    capability: Capability
    argv: list[str]
    cwd: str | None
    timeout: float
    reason: str
    stdin: str | None
    allow_prompt: bool = True


@dataclass
class FakeBroker:
    """In-memory broker that records calls. ``run`` is the only method
    adapters depend on; ``state`` / ``states`` return ``PASSWORDLESS``
    so probes don't accidentally hit a real ``sudo`` binary."""

    calls: list[RecordedCall] = field(default_factory=list)
    responses: list[subprocess.CompletedProcess[str]] = field(default_factory=list)
    exceptions: list[Exception | None] = field(default_factory=list)
    states_map: dict[str, CapabilityState] = field(default_factory=dict)
    invalidate_calls: list[Capability | None] = field(default_factory=list)
    prompter: Any = None

    def run(
        self,
        capability: Capability,
        argv: list[str],
        *,
        cwd: str | None = None,
        timeout: float = 30.0,
        reason: str = "",
        stdin: str | None = None,
        allow_prompt: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        idx = len(self.calls)
        self.calls.append(
            RecordedCall(
                capability=capability,
                argv=list(argv),
                cwd=cwd,
                timeout=timeout,
                reason=reason,
                stdin=stdin,
                allow_prompt=allow_prompt,
            )
        )
        if idx < len(self.exceptions) and self.exceptions[idx] is not None:
            raise self.exceptions[idx]  # type: ignore[misc]
        if idx < len(self.responses):
            proc = self.responses[idx]
            # Mirror real broker: non-zero rc → PrivilegeError, via the real
            # _format_failure for the exact "<description>: <detail>" shape.
            if proc.returncode != 0:
                raise PrivilegeError(_format_failure(capability, proc))
            return proc
        return subprocess.CompletedProcess(
            ["sudo", *argv],
            0,
            stdout="",
            stderr="",
        )

    def state(self, capability: Capability) -> CapabilityState:
        return self.states_map.get(capability.name, CapabilityState.PASSWORDLESS)

    def states(self) -> dict[str, CapabilityState]:
        from openfollow.privilege.capabilities import ALL_CAPABILITIES

        return {cap.name: self.states_map.get(cap.name, CapabilityState.PASSWORDLESS) for cap in ALL_CAPABILITIES}

    def invalidate(self, capability: Capability | None = None) -> None:
        self.invalidate_calls.append(capability)

    def set_prompter(self, prompter: Any) -> None:
        self.prompter = prompter


def make_failure(message: str = "boom") -> PrivilegeError:
    """Construct a ``PrivilegeError`` for use in :attr:`FakeBroker.exceptions`."""
    return PrivilegeError(message)


def make_nonzero(
    *,
    rc: int = 1,
    stdout: str = "",
    stderr: str = "boom",
) -> subprocess.CompletedProcess[str]:
    """Build a non-zero ``CompletedProcess`` for :attr:`FakeBroker.responses`."""
    return subprocess.CompletedProcess(["sudo"], rc, stdout=stdout, stderr=stderr)
