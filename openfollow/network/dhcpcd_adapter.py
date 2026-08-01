# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""dhcpcd backend: owns a marked block in ``/etc/dhcpcd.conf``."""

from __future__ import annotations

import logging
import re
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from openfollow.network.adapter import (
    ApplyResult,
    Ipv4Config,
    Ipv4Method,
    LeaseInfo,
    NetworkAdapter,
    NetworkInterface,
    NetworkState,
)
from openfollow.network.validate import validate_apply
from openfollow.privilege.broker import PrivilegeBroker, PrivilegeError
from openfollow.privilege.capabilities import (
    NETWORK_DHCPCD_CONF_COMMIT,
    NETWORK_DHCPCD_CONF_WRITE_TMP,
    NETWORK_DHCPCD_RELEASE,
    NETWORK_DHCPCD_RELOAD,
    NETWORK_DHCPCD_RENEW,
    Capability,
)

logger = logging.getLogger(__name__)

DHCPCD_CONF = Path("/etc/dhcpcd.conf")
# Staging path for the atomic write. Pinned to the exact sibling of
# DHCPCD_CONF so it matches the fully-fixed ``conf_write_tmp`` /
# ``conf_commit`` sudoers rules. (``Path.with_suffix`` would yield
# ``/etc/dhcpcd.tmp`` – wrong; the rule pins ``.conf.tmp``.)
DHCPCD_CONF_TMP = Path("/etc/dhcpcd.conf.tmp")


@dataclass(frozen=True)
class _BrokerCallResult:
    """Result tuple from broker run (ok, detail)."""

    ok: bool
    detail: str


_BLOCK_START = "# >>> openfollow managed: {iface} >>>"
_BLOCK_END = "# <<< openfollow managed: {iface} <<<"
_DHCPCD_TIMEOUT = 8
# ``dhcpcd -n`` rebinds asynchronously, so the address read-back can still
# report the old lease for a moment. Poll a few times with a short settle so
# the verify doesn't false-positive a clean static apply on stale state.
_VERIFY_RETRIES = 3
_VERIFY_SETTLE_S = 0.5
# Interface name allow-set: must start alphanumeric (no leading "-" that
# argv would read as an option) and contain only chars valid in Linux iface
# names (vlan/bridge names use "." / "-").
_IFACE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class DhcpcdAdapter(NetworkAdapter):
    """Rewrite ``/etc/dhcpcd.conf`` blocks and bounce dhcpcd."""

    backend_name = "dhcpcd"

    def __init__(
        self,
        conf_path: Path | None = None,
        *,
        broker: PrivilegeBroker | None = None,
    ) -> None:
        self.conf_path = conf_path or DHCPCD_CONF
        # Broker optional for tests/read-only contexts.
        self._broker = broker

    # ---- helpers --------------------------------------------------------

    def _run(self, argv: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        """Execute read-only subprocess call."""
        result = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=_DHCPCD_TIMEOUT,
        )
        if check and result.returncode != 0:
            raise RuntimeError(f"{' '.join(argv)} failed (rc={result.returncode}): {result.stderr.strip()}")
        return result

    def _read_conf(self) -> str:
        try:
            return self.conf_path.read_text()
        except OSError:
            return ""

    def _write_conf_privileged(self, text: str) -> None:
        """Rewrite the conf atomically, or write directly in tests.

        Production path (broker + real ``/etc/dhcpcd.conf``): stage the
        full file to the sibling ``.tmp`` via ``tee``, then commit with a
        privileged ``mv``. ``rename(2)`` on the same filesystem is atomic,
        so the live conf is always a whole file. A ``tee`` that is killed,
        times out, or OOMs can only truncate the tmp – it never replaces
        the live conf, and the ``mv`` below never runs because ``broker.run``
        raises on the failed step. (The committed file inherits the tmp's
        owner/mode, which depends on the process umask.)
        """
        if self._broker is None or self.conf_path != DHCPCD_CONF:
            self.conf_path.write_text(text)
            return
        self._broker.run(
            NETWORK_DHCPCD_CONF_WRITE_TMP,
            ["/usr/bin/tee", str(DHCPCD_CONF_TMP)],
            stdin=text,
            reason="Apply IPv4 network changes (stage dhcpcd.conf)",
            timeout=10,
        )
        self._broker.run(
            NETWORK_DHCPCD_CONF_COMMIT,
            ["/usr/bin/mv", str(DHCPCD_CONF_TMP), str(DHCPCD_CONF)],
            reason="Apply IPv4 network changes (commit dhcpcd.conf)",
            timeout=10,
        )

    @staticmethod
    def _strip_block(text: str, iface: str) -> str:
        pattern = re.compile(
            rf"# >>> openfollow managed: {re.escape(iface)} >>>.*?"
            rf"# <<< openfollow managed: {re.escape(iface)} <<<\n?",
            re.DOTALL,
        )
        text = pattern.sub("", text)

        start = _BLOCK_START.format(iface=iface)
        if start not in text:
            return text
        # Orphaned start marker (end marker hand-deleted, or a write truncated
        # mid-block): the regex above can't pair it, so apply would append a
        # second ``interface <iface>`` stanza. Drop the marker and its stanza,
        # stopping at the next stanza/marker boundary (a new ``interface`` line
        # or any managed marker) or EOF so unrelated config is preserved.
        # Compare with ``.strip()`` so a hand-edit that added incidental
        # whitespace or a CRLF (the substring guard above still matched) can't
        # leave the orphan in place and reintroduce the duplicate stanza.
        own_interface = f"interface {iface}"
        out: list[str] = []
        lines = text.splitlines(keepends=True)
        i = 0
        while i < len(lines):
            if lines[i].strip() != start:
                out.append(lines[i])
                i += 1
                continue
            i += 1
            if i < len(lines) and lines[i].strip() == own_interface:
                i += 1
            while i < len(lines):
                stripped = lines[i].lstrip()
                if (
                    stripped.startswith("interface ")
                    or stripped.startswith("# >>> openfollow managed:")
                    or stripped.startswith("# <<< openfollow managed:")
                ):
                    break
                i += 1
        return "".join(out)

    @staticmethod
    def _build_block(iface: str, config: Ipv4Config) -> str:
        lines = [_BLOCK_START.format(iface=iface), f"interface {iface}"]
        if config.method == Ipv4Method.STATIC:
            if config.address and config.prefix is not None:
                lines.append(f"static ip_address={config.address}/{config.prefix}")
            if config.router:
                lines.append(f"static routers={config.router}")
            if config.dns:
                lines.append("static domain_name_servers=" + " ".join(config.dns))
        elif config.method == Ipv4Method.DHCP_WITH_MANUAL_ADDRESS:
            if config.address:
                lines.append(f"inform {config.address}")
            if config.dns:
                lines.append("static domain_name_servers=" + " ".join(config.dns))
        elif config.method == Ipv4Method.DHCP:  # pragma: no branch – exhaustive enum
            if config.dns:
                lines.append("static domain_name_servers=" + " ".join(config.dns))
            # else: no overrides; pure DHCP
        lines.append(_BLOCK_END.format(iface=iface))
        return "\n".join(lines) + "\n"

    # ---- list / get -----------------------------------------------------

    def list_interfaces(self) -> list[NetworkInterface]:
        # dhcpcd doesn't introspect interfaces; reuse psutil.
        from openfollow.network.psutil_adapter import PsutilReadOnlyAdapter

        return PsutilReadOnlyAdapter().list_interfaces()

    def get_state(self, iface: str) -> NetworkState | None:
        ifaces = {i.name: i for i in self.list_interfaces()}
        if iface not in ifaces:
            return None
        method = self._detect_method(iface)
        lease = self._read_lease(iface)
        addr = lease.address if lease else None
        prefix = lease.prefix if lease else None
        router = lease.router if lease else None
        dns = lease.dns if lease else ()
        # Override with whatever we wrote to the managed block (if any).
        block_overrides = self._read_managed_overrides(iface)
        if block_overrides:
            override_addr = block_overrides.get("address")
            if isinstance(override_addr, str):
                addr = override_addr
            override_prefix = block_overrides.get("prefix")
            if isinstance(override_prefix, int):
                prefix = override_prefix
            override_router = block_overrides.get("router")
            if isinstance(override_router, str):
                router = override_router
            override_dns = block_overrides.get("dns")
            if isinstance(override_dns, list) and override_dns:
                dns = tuple(d for d in override_dns if isinstance(d, str))
        ipv4 = Ipv4Config(
            method=method,
            address=addr,
            prefix=prefix,
            router=router,
            dns=tuple(dns),
        )
        return NetworkState(interface=ifaces[iface], ipv4=ipv4, lease=lease)

    def _detect_method(self, iface: str) -> Ipv4Method:
        block = self._extract_block_text(iface)
        if block is None:
            return Ipv4Method.DHCP
        if "static ip_address=" in block:
            return Ipv4Method.STATIC
        if "inform " in block:
            return Ipv4Method.DHCP_WITH_MANUAL_ADDRESS
        return Ipv4Method.DHCP

    def _extract_block_text(self, iface: str) -> str | None:
        text = self._read_conf()
        pattern = re.compile(
            rf"# >>> openfollow managed: {re.escape(iface)} >>>(.*?)"
            rf"# <<< openfollow managed: {re.escape(iface)} <<<",
            re.DOTALL,
        )
        match = pattern.search(text)
        return match.group(1) if match else None

    def _read_managed_overrides(self, iface: str) -> dict[str, object] | None:
        block = self._extract_block_text(iface)
        if block is None:
            return None
        out: dict[str, object] = {}
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("static ip_address="):
                value = line.split("=", 1)[1]
                if "/" in value:
                    addr, _, prefix = value.partition("/")
                    out["address"] = addr
                    try:
                        out["prefix"] = int(prefix)
                    except ValueError:
                        pass
                else:
                    out["address"] = value
            elif line.startswith("static routers="):
                out["router"] = line.split("=", 1)[1].strip()
            elif line.startswith("static domain_name_servers="):
                out["dns"] = line.split("=", 1)[1].split()
            elif line.startswith("inform "):
                out["address"] = line.split(None, 1)[1].strip()
        return out

    def _read_lease(self, iface: str) -> LeaseInfo | None:
        try:
            res = self._run(["dhcpcd", "-U", iface], check=False)
        except (FileNotFoundError, subprocess.SubprocessError):
            return None
        if res.returncode != 0 or not res.stdout.strip():
            return None
        addr: str | None = None
        prefix: int | None = None
        router: str | None = None
        dns: list[str] = []
        lease_seconds: int | None = None
        for line in res.stdout.splitlines():
            line = line.strip()
            if "=" not in line:
                continue
            key, _, raw = line.partition("=")
            value = raw.strip().strip("'\"")
            if key == "ip_address":
                addr = value
            elif key == "subnet_cidr":
                try:
                    prefix = int(value)
                except ValueError:
                    pass
            elif key == "subnet_mask" and prefix is None:
                from openfollow.network.validate import parse_prefix

                prefix = parse_prefix(value)
            elif key == "routers":
                router = value.split()[0] if value else None
            elif key == "domain_name_servers":
                dns = value.split()[:3]
            elif key == "dhcp_lease_time":
                try:
                    lease_seconds = int(value)
                except ValueError:
                    pass
        if addr is None and router is None and not dns:
            return None
        return LeaseInfo(
            address=addr,
            prefix=prefix,
            router=router,
            dns=tuple(dns),
            lease_seconds_remaining=lease_seconds,
        )

    # ---- mutation -------------------------------------------------------

    def apply_ipv4(self, iface: str, config: Ipv4Config) -> ApplyResult:
        # Defence-in-depth: re-validate at the privileged boundary so the conf
        # write and the ``dhcpcd <iface>`` argv are safe regardless of caller.
        # ``parse_ipv4`` (via validate_apply) rejects embedded newlines, which
        # blocks injecting extra directives into the conf.
        if not _IFACE_RE.fullmatch(iface):
            return ApplyResult(ok=False, message=f"Invalid interface name: {iface!r}")
        errors = validate_apply(config.method, config.address, config.prefix, config.router, list(config.dns))
        if errors:
            return ApplyResult(ok=False, message="; ".join(errors))

        try:
            current = self._read_conf()
            stripped = self._strip_block(current, iface)
            block = self._build_block(iface, config)
            new_text = stripped.rstrip() + "\n\n" + block if stripped.strip() else block
            try:
                self._write_conf_privileged(new_text)
            except PrivilegeError as exc:
                return ApplyResult(ok=False, message=str(exc))
            except OSError as exc:
                return ApplyResult(ok=False, message=f"Cannot write {self.conf_path}: {exc}")
        except Exception as exc:  # noqa: BLE001
            return ApplyResult(ok=False, message=f"Failed to update dhcpcd.conf: {exc}")

        partial: list[str] = []
        release = self._broker_run(
            NETWORK_DHCPCD_RELEASE,
            ["/usr/sbin/dhcpcd", "-k", iface],
            reason=f"Release DHCP lease on {iface}",
        )
        if release is not None and not release.ok:
            partial.append(f"dhcpcd -k: {release.detail}")

        rebind = self._broker_run(
            NETWORK_DHCPCD_RENEW,
            ["/usr/sbin/dhcpcd", "-n", iface],
            reason=f"Renew DHCP lease on {iface}",
        )
        # The conf was already rewritten above. If the bounce never lands, the
        # new (never-applied) config must not persist on disk – restore the
        # prior conf so the next reload/reboot doesn't silently come up on it.
        if rebind is None:
            self._restore_conf(current)
            return ApplyResult(ok=False, message="Broker not configured.")
        if not rebind.ok:
            reload_ = self._broker_run(
                NETWORK_DHCPCD_RELOAD,
                ["/usr/bin/systemctl", "reload", "dhcpcd"],
                reason="Reload dhcpcd after a failed -n bounce",
            )
            if reload_ is None or not reload_.ok:
                detail = reload_.detail if reload_ is not None else "broker unavailable"
                self._restore_conf(current)
                # The earlier release dropped the lease/address; with the prior
                # conf back on disk, best-effort re-bounce so the interface comes
                # back up on its previous config instead of staying released.
                self._broker_run(
                    NETWORK_DHCPCD_RENEW,
                    ["/usr/sbin/dhcpcd", "-n", iface],
                    reason=f"Re-bounce {iface} on the restored config",
                )
                return ApplyResult(
                    ok=False,
                    message=(
                        f"dhcpcd -n failed ({rebind.detail}); systemctl reload dhcpcd also failed: {detail}. "
                        f"Restored the prior config; the device may need a manual retry."
                    ),
                )
            partial.append(f"dhcpcd -n: {rebind.detail} (fell back to systemctl reload)")

        warning = self._verify_static_applied(iface, config)
        if warning:
            partial.append(warning)
        return ApplyResult(ok=True, message="Applied.", partial_failures=tuple(partial))

    def _restore_conf(self, text: str) -> None:
        """Best-effort restore of the prior conf after a failed bounce.

        Swallow restore errors – the apply is already returning a failure – but
        log them so a divergent on-disk state is explainable.
        """
        try:
            self._write_conf_privileged(text)
        except (PrivilegeError, OSError) as exc:
            logger.error("Failed to restore %s after a failed apply: %s", self.conf_path, exc)

    def _verify_static_applied(self, iface: str, config: Ipv4Config) -> str | None:
        """Best-effort check that a STATIC apply took effect.

        Reads the interface address back via ``dhcpcd -U``; returns a warning
        when it differs from the requested address (e.g. dhcpcd rejected the
        block and fell back to DHCP). Returns ``None`` when the method isn't
        static, the address can't be read, or it matches – never raises.

        ``dhcpcd -n`` rebinds asynchronously, so the first read-back can still
        carry the old lease. The check retries a few times with a short settle
        and warns only if the address is still divergent after the last attempt,
        so a clean apply that settles within the window isn't flagged.
        """
        if config.method != Ipv4Method.STATIC or not config.address:
            return None
        last_address = ""
        for attempt in range(_VERIFY_RETRIES):
            lease = self._read_lease(iface)
            if lease is None or not lease.address:
                return None
            if lease.address == config.address:
                return None
            last_address = lease.address
            if attempt < _VERIFY_RETRIES - 1:
                self._settle(_VERIFY_SETTLE_S)
        return f"interface came up on {last_address}, expected {config.address}"

    def _settle(self, seconds: float) -> None:
        """Sleep between read-back attempts. Overridable in tests."""
        time.sleep(seconds)

    def renew_lease(self, iface: str) -> ApplyResult:
        result = self._broker_run(
            NETWORK_DHCPCD_RENEW,
            ["/usr/sbin/dhcpcd", "-n", iface],
            reason=f"Renew DHCP lease on {iface}",
        )
        if result is None:
            return ApplyResult(ok=False, message="Broker not configured.")
        if not result.ok:
            return ApplyResult(ok=False, message=result.detail)
        return ApplyResult(ok=True, message="Lease renewed.")

    def _broker_run(
        self,
        capability: Capability,
        argv: list[str],
        *,
        reason: str,
    ) -> _BrokerCallResult | None:
        """Invoke the broker, return a small (ok, detail) value, never raise.

        Returning ``None`` signals "no broker wired" – only happens in
        tests / read-only contexts where the adapter was constructed
        without a broker. The apply/renew paths short-circuit on that
        rather than handing the operator a confusing AttributeError.
        """
        if self._broker is None:
            return None
        try:
            proc = self._broker.run(
                capability,
                argv,
                reason=reason,
                timeout=_DHCPCD_TIMEOUT,
            )
        except PrivilegeError as exc:
            return _BrokerCallResult(ok=False, detail=str(exc))
        # Real broker raises on any non-zero rc, so reaching here means
        # ok=True. Surface stdout/stderr as detail in case the call emitted
        # a success-side warning the operator might want to see.
        return _BrokerCallResult(
            ok=True,
            detail=(proc.stderr or proc.stdout or "").strip(),
        )
