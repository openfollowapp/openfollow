# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""NetworkManager backend (shells out to ``nmcli``)."""

from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import Sequence

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
    NETWORK_NM_CON_DOWN,
    NETWORK_NM_CON_MOD,
    NETWORK_NM_CON_UP,
    Capability,
)

logger = logging.getLogger(__name__)

_NMCLI_TIMEOUT = 8


def _unescape_terse(value: str) -> str:
    """Reverse nmcli ``-t`` (terse) escaping in a field value: a literal
    ``:`` is emitted as ``\\:`` and a literal ``\\`` as ``\\\\``. Without
    this a colon-bearing value – ``GENERAL.HWADDR`` (a MAC) is the only one
    read here – carries stray backslashes (``AA\\:BB\\:…``)."""
    if "\\" not in value:
        return value
    out: list[str] = []
    i = 0
    n = len(value)
    while i < n:
        if value[i] == "\\" and i + 1 < n:
            out.append(value[i + 1])
            i += 2
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


class NetworkManagerAdapter(NetworkAdapter):
    """Drive ``nmcli`` to read/write IPv4 connection settings."""

    backend_name = "NetworkManager"

    def __init__(self, *, broker: PrivilegeBroker | None = None) -> None:
        self._broker = broker

    def _run(self, argv: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        """Execute read-only nmcli call."""
        result = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=_NMCLI_TIMEOUT,
        )
        if check and result.returncode != 0:
            raise RuntimeError(f"{' '.join(argv)} failed (rc={result.returncode}): {result.stderr.strip()}")
        return result

    def _run_privileged(
        self,
        capability: Capability,
        argv: list[str],
        *,
        reason: str,
    ) -> tuple[bool, str]:
        """Invoke capability via broker, return (ok, detail)."""
        if self._broker is None:
            return (False, "Broker not configured.")
        try:
            proc = self._broker.run(
                capability,
                argv,
                reason=reason,
                timeout=_NMCLI_TIMEOUT,
            )
        except PrivilegeError as exc:
            return (False, str(exc))
        # Real broker raises on any non-zero rc, so reaching here means
        # success. The stdout is the only useful detail at this point.
        return (True, (proc.stdout or "").strip())

    # ---- list / get -----------------------------------------------------

    def list_interfaces(self) -> list[NetworkInterface]:
        try:
            res = self._run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"])
        except (RuntimeError, FileNotFoundError, subprocess.SubprocessError) as exc:
            logger.warning("nmcli list_interfaces failed: %s", exc)
            return []
        out: list[NetworkInterface] = []
        for line in res.stdout.splitlines():
            parts = line.split(":")
            if len(parts) < 3:
                continue
            name, kind, state = parts[0], parts[1], parts[2]
            if kind in ("loopback", "bridge"):
                # Include but tag for UI filtering
                pass
            out.append(
                NetworkInterface(
                    name=name,
                    mac=None,
                    kind=kind or None,
                    is_up=state.startswith("connected"),
                )
            )
        return out

    def _connection_for(self, iface: str) -> str | None:
        try:
            res = self._run(["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"])
        except (RuntimeError, FileNotFoundError, subprocess.SubprocessError):
            return None
        for line in res.stdout.splitlines():
            name, _, dev = line.partition(":")
            if dev == iface:
                return name
        # Fallback to any profile bound to this device
        try:
            res = self._run(["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show"])
        except (RuntimeError, FileNotFoundError, subprocess.SubprocessError):
            return None
        for line in res.stdout.splitlines():
            name, _, dev = line.partition(":")
            if dev == iface:
                return name
        return None

    def _parse_show(self, text: str) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for line in text.splitlines():
            # nmcli ``-t`` uses ``:`` as the field separator; the field name
            # never contains one, so the first ``:`` is always the split.
            key, _, value = line.partition(":")
            if not key:
                continue
            out.setdefault(key.strip(), []).append(_unescape_terse(value.strip()))
        return out

    def get_state(self, iface: str) -> NetworkState | None:
        ifaces = {i.name: i for i in self.list_interfaces()}
        if iface not in ifaces:
            return None
        try:
            dev = self._run(
                [
                    "nmcli",
                    "-t",
                    "-f",
                    "IP4.ADDRESS,IP4.GATEWAY,IP4.DNS,GENERAL.HWADDR",
                    "device",
                    "show",
                    iface,
                ]
            )
        except (RuntimeError, FileNotFoundError, subprocess.SubprocessError):
            return None
        parsed = self._parse_show(dev.stdout)

        addr: str | None = None
        prefix: int | None = None
        ip4_addresses = parsed.get("IP4.ADDRESS[1]") or []
        if ip4_addresses:
            first = ip4_addresses[0]
            if "/" in first:
                addr_part, _, prefix_part = first.partition("/")
                addr = addr_part or None
                try:
                    prefix = int(prefix_part)
                except ValueError:
                    prefix = None
            else:
                addr = first or None

        gw_list = parsed.get("IP4.GATEWAY") or []
        router = gw_list[0] if gw_list and gw_list[0] else None
        dns_list = [v for k, vs in parsed.items() if k.startswith("IP4.DNS") for v in vs if v]
        mac_list = parsed.get("GENERAL.HWADDR") or []
        mac = mac_list[0] if mac_list else None

        method = self._read_method(iface)
        ipv4 = Ipv4Config(
            method=method,
            address=addr,
            prefix=prefix,
            router=router,
            dns=tuple(dns_list[:3]),
        )
        iface_obj = ifaces[iface]
        if mac and iface_obj.mac is None:
            iface_obj = NetworkInterface(
                name=iface_obj.name,
                mac=mac,
                kind=iface_obj.kind,
                is_up=iface_obj.is_up,
            )
        lease = self._read_lease(iface)
        return NetworkState(interface=iface_obj, ipv4=ipv4, lease=lease)

    def _read_method(self, iface: str) -> Ipv4Method:
        name = self._connection_for(iface)
        if not name:
            return Ipv4Method.DHCP
        try:
            res = self._run(
                [
                    "nmcli",
                    "-t",
                    "-f",
                    "ipv4.method,ipv4.addresses",
                    "connection",
                    "show",
                    name,
                ]
            )
        except (RuntimeError, FileNotFoundError, subprocess.SubprocessError):
            return Ipv4Method.DHCP
        parsed = self._parse_show(res.stdout)
        method = (parsed.get("ipv4.method", [""])[0] or "").lower()
        addresses = parsed.get("ipv4.addresses", [""])[0] or ""
        if method == "manual":
            return Ipv4Method.STATIC if not addresses.startswith("dhcp") else Ipv4Method.DHCP_WITH_MANUAL_ADDRESS
        if method == "auto":
            return Ipv4Method.DHCP
        return Ipv4Method.DHCP

    def _read_lease(self, iface: str) -> LeaseInfo | None:
        try:
            res = self._run(["nmcli", "-t", "-f", "DHCP4.OPTION", "device", "show", iface])
        except (RuntimeError, FileNotFoundError, subprocess.SubprocessError):
            return None
        addr: str | None = None
        prefix: int | None = None
        router: str | None = None
        dns: list[str] = []
        lease_seconds: int | None = None
        for line in res.stdout.splitlines():
            _, _, value = line.partition(":")
            if "=" not in value:
                continue
            key, _, val = value.partition("=")
            key = key.strip()
            val = val.strip()
            if key == "ip_address":
                addr = val
            elif key == "subnet_mask":
                from openfollow.network.validate import parse_prefix

                prefix = parse_prefix(val)
            elif key == "routers":
                router = val.split()[0] if val else None
            elif key == "domain_name_servers":
                dns = val.split()[:3]
            elif key == "expiry":
                # Convert absolute epoch timestamp to seconds-remaining.
                try:
                    expiry_epoch = int(val)
                except ValueError:
                    lease_seconds = None
                else:
                    lease_seconds = max(0, expiry_epoch - int(time.time()))
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
        # Defence-in-depth: re-validate operator-influenced values at the
        # privileged boundary so the root-run nmcli argv is safe regardless of
        # caller, mirroring the dhcpcd adapter. Unsupported methods fall
        # through to the dedicated error below.
        if config.method in (Ipv4Method.DHCP, Ipv4Method.STATIC, Ipv4Method.DHCP_WITH_MANUAL_ADDRESS):
            errors = validate_apply(config.method, config.address, config.prefix, config.router, list(config.dns))
            if errors:
                return ApplyResult(ok=False, message="; ".join(errors))

        name = self._connection_for(iface)
        if not name:
            return ApplyResult(
                ok=False,
                message=f"No NetworkManager connection profile bound to {iface}.",
            )
        # Use long argv form to match sudoers rule. The explicit ``id``
        # keyword (``con mod id <name>``) makes a profile name beginning
        # with ``-`` the connection ID. nmcli's ``con`` subcommands do NOT
        # treat ``--`` as an end-of-options marker – they read it as a
        # literal connection name (``unknown connection '--'``) – so ``id``
        # is the portable disambiguator per ``ARGUMENTS := [id|uuid|path]
        # <ID>`` (the sudoers ``con mod *`` glob still matches). Verified on
        # nmcli 1.52.1 / Debian trixie.
        modify_argv = ["/usr/bin/nmcli", "con", "mod", "id", name]
        if config.method == Ipv4Method.DHCP:
            modify_argv += [
                "ipv4.method",
                "auto",
                "ipv4.addresses",
                "",
                "ipv4.gateway",
                "",
                "ipv4.ignore-auto-dns",
                "no",
            ]
            if config.dns:
                modify_argv += ["ipv4.dns", " ".join(config.dns), "ipv4.ignore-auto-dns", "yes"]
            else:
                modify_argv += ["ipv4.dns", ""]
        elif config.method == Ipv4Method.STATIC:
            # Use explicit is None check to preserve /0 CIDR prefix.
            prefix = 24 if config.prefix is None else config.prefix
            modify_argv += [
                "ipv4.method",
                "manual",
                "ipv4.addresses",
                f"{config.address}/{prefix}",
                "ipv4.gateway",
                config.router or "",
                "ipv4.dns",
                " ".join(config.dns),
                "ipv4.ignore-auto-dns",
                "yes",
            ]
        elif config.method == Ipv4Method.DHCP_WITH_MANUAL_ADDRESS:
            # NM has no native DHCP+manual; emulate using static profile.
            from openfollow.network.validate import parse_ipv4

            lease = self._read_lease(iface)
            # Lease-sourced gateway/DNS come straight from a (possibly rogue)
            # DHCP server's nmcli output, and the web route forces
            # ``config.router=None`` on this path – so validate them before
            # they reach the root-run nmcli argv, mirroring ``validate_apply``
            # on operator input. A failing value is dropped (gateway → "").
            router = config.router or (lease.router if lease else None) or ""
            router = parse_ipv4(router) or ""
            # Use explicit is None checks for prefix fallback chain.
            if config.prefix is not None:
                prefix = config.prefix
            elif lease is not None and lease.prefix is not None:
                prefix = lease.prefix
            else:
                prefix = 24
            dns = list(config.dns) or (list(lease.dns) if lease else [])
            dns = [v for v in (parse_ipv4(d) for d in dns) if v]
            modify_argv += [
                "ipv4.method",
                "manual",
                "ipv4.addresses",
                f"{config.address}/{prefix}",
                "ipv4.gateway",
                router,
                "ipv4.dns",
                " ".join(dns),
                "ipv4.ignore-auto-dns",
                "yes",
            ]
        else:
            return ApplyResult(ok=False, message=f"Unsupported method: {config.method}")

        ok, detail = self._run_privileged(
            NETWORK_NM_CON_MOD,
            modify_argv,
            reason=f"Modify NetworkManager profile {name}",
        )
        if not ok:
            return ApplyResult(ok=False, message=detail or "nmcli con mod failed")

        partial: list[str] = []
        # con down can fail; con up failure is fatal.
        down_ok, down_detail = self._run_privileged(
            NETWORK_NM_CON_DOWN,
            ["/usr/bin/nmcli", "con", "down", "id", name],
            reason=f"Bring NetworkManager profile {name} down",
        )
        if not down_ok and down_detail:
            partial.append(f"nmcli con down: {down_detail}")

        up_ok, up_detail = self._run_privileged(
            NETWORK_NM_CON_UP,
            ["/usr/bin/nmcli", "con", "up", "id", name],
            reason=f"Bring NetworkManager profile {name} up",
        )
        if not up_ok:
            return ApplyResult(ok=False, message=up_detail or "nmcli con up failed")
        return ApplyResult(ok=True, message="Applied.", partial_failures=tuple(partial))

    def renew_lease(self, iface: str) -> ApplyResult:
        name = self._connection_for(iface)
        if not name:
            return ApplyResult(ok=False, message=f"No NetworkManager profile for {iface}.")
        # NM has no explicit renew verb; use down/up cycle.
        self._run_privileged(
            NETWORK_NM_CON_DOWN,
            ["/usr/bin/nmcli", "con", "down", "id", name],
            reason=f"Bring NetworkManager profile {name} down",
        )
        up_ok, up_detail = self._run_privileged(
            NETWORK_NM_CON_UP,
            ["/usr/bin/nmcli", "con", "up", "id", name],
            reason=f"Renew DHCP lease via NetworkManager profile {name}",
        )
        if not up_ok:
            return ApplyResult(ok=False, message=up_detail or "nmcli con up failed")
        return ApplyResult(ok=True, message="Lease renewed.")
