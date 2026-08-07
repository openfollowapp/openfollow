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
    VlanInterface,
)
from openfollow.network.validate import validate_apply, vlan_interface_name
from openfollow.privilege.broker import PrivilegeBroker, PrivilegeError
from openfollow.privilege.capabilities import (
    NETWORK_NM_CON_ADD,
    NETWORK_NM_CON_DELETE,
    NETWORK_NM_CON_DOWN,
    NETWORK_NM_CON_MOD,
    NETWORK_NM_CON_UP,
    Capability,
)

logger = logging.getLogger(__name__)

_NMCLI_TIMEOUT = 8

# Shown when the privilege broker is absent, which on a real device means the
# sudoers rules were never installed. Kept short: the on-screen banner is one
# truncated line.
_NO_BROKER_MESSAGE = "Cannot change network settings - the privileged helper is not configured."


def _split_terse(line: str) -> list[str]:
    """Split one nmcli ``-t`` row on its *unescaped* field separators.

    ``nmcli -t`` emits a literal ``:`` inside a value as ``\\:``, so splitting
    on every colon shifts each field after a colon-bearing one: a profile named
    ``Wired connection: office`` yields a fragment where the UUID belongs, and
    the device column lands on the type. Unescapes as it goes, so callers get
    finished values.
    """
    fields: list[str] = []
    current: list[str] = []
    i = 0
    n = len(line)
    while i < n:
        char = line[i]
        if char == "\\" and i + 1 < n:
            current.append(line[i + 1])
            i += 2
        elif char == ":":
            fields.append("".join(current))
            current = []
            i += 1
        else:
            current.append(char)
            i += 1
    fields.append("".join(current))
    return fields


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
            return (False, _NO_BROKER_MESSAGE)
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

    def _device_state(self, iface: str) -> str | None:
        """nmcli's STATE word for *iface*, ``""`` if absent, ``None`` if unreadable.

        The three are different and the callers rely on it: a read failure is
        not evidence the device is gone, and telling an operator their plugged-in
        adapter "is not present" because nmcli timed out contradicts the card
        they just clicked.
        """
        try:
            res = self._run(["nmcli", "-t", "-f", "DEVICE,STATE", "device"])
        except (RuntimeError, FileNotFoundError, subprocess.SubprocessError):
            return None
        for line in res.stdout.splitlines():
            name, _, state = line.partition(":")
            if name == iface:
                return state.strip().lower()
        return ""

    def _has_carrier(self, iface: str) -> bool:
        """False only when nmcli explicitly reports no link on *iface*.

        Distinguishes "activation failed because the cable is out" – the
        pre-stage workflow, not an error – from a real activation failure. An
        unreadable state counts as *having* carrier so an activation failure we
        can't explain is still reported as one; downgrading it to
        saved-but-pending would hide a real problem behind a reassuring
        message.
        """
        return self._device_state(iface) != "unavailable"

    def _no_profile_message(self, iface: str) -> str:
        """Say what the operator should check, not what the adapter didn't find.

        Kept to one short sentence: the on-screen Settings banner is a single
        truncated line, so a second sentence is the half that gets cut.
        """
        state = self._device_state(iface)
        if state is None:
            return f"Could not read {iface} from NetworkManager. Try Scan."
        if state == "unmanaged":
            return f"{iface} is not managed by NetworkManager. See the help drawer."
        if not state:
            return f"{iface} is not present. Check the adapter, then Scan."
        return f"No saved profile for {iface}. Connect the cable once to create one."

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
                message=self._no_profile_message(iface),
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
            return ApplyResult(
                ok=False,
                message=f"Could not save the settings to profile '{name}'; nothing was changed ({detail})."
                if detail
                else f"Could not save the settings to profile '{name}'; nothing was changed.",
            )

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
            # Activation can only fail for want of a carrier once the profile
            # itself saved, and that is the pre-stage-before-the-show workflow:
            # the settings are persisted and take effect on next plug-in, so
            # reporting a hard failure would be wrong.
            if not self._has_carrier(iface) and not up_detail:
                # Only when nmcli gave no reason of its own: a real failure -
                # rfkill, a missing con-up grant - must not be reported as a
                # cable problem just because the device reads "unavailable".
                return ApplyResult(
                    ok=True,
                    pending=True,
                    message=f"Saved; the settings take effect when {iface} has a link.",
                    partial_failures=tuple(partial),
                )
            return ApplyResult(
                ok=False,
                message=f"Saved, but {iface} could not be brought up ({up_detail})."
                if up_detail
                else f"Saved, but {iface} could not be brought up.",
            )
        return ApplyResult(ok=True, message="Applied.", partial_failures=tuple(partial))

    def renew_lease(self, iface: str) -> ApplyResult:
        name = self._connection_for(iface)
        if not name:
            return ApplyResult(ok=False, message=self._no_profile_message(iface))
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
            # Only claim it's the link when nmcli gave no reason of its own -
            # a missing helper or an ungranted rule is not a cable problem, and
            # sending the operator to check a cable hides the real fix.
            if not up_detail and not self._has_carrier(iface):
                return ApplyResult(ok=False, message=f"{iface} has no link, so there is no lease to request.")
            return ApplyResult(
                ok=False,
                message=f"Could not renew {iface} ({up_detail})." if up_detail else f"Could not renew {iface}.",
            )
        return ApplyResult(ok=True, message="Lease renewed.")

    # ---- VLAN sub-interfaces --------------------------------------------

    def supports_vlans(self) -> bool:
        return True

    def _vlan_profiles(self) -> tuple[list[tuple[str, str]], dict[str, str]]:
        """Return ``([(profile name, device)], {uuid: device})`` for the
        connection list, filtered to VLAN profiles. The UUID map covers every
        profile, not just VLANs, because it exists to resolve a VLAN's parent
        reference back to an interface name."""
        try:
            res = self._run(["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show"])
        except (RuntimeError, FileNotFoundError, subprocess.SubprocessError) as exc:
            logger.warning("nmcli VLAN profile list failed: %s", exc)
            return ([], {})
        out: list[tuple[str, str]] = []
        device_by_uuid: dict[str, str] = {}
        for line in res.stdout.splitlines():
            parts = _split_terse(line)
            if len(parts) < 4:
                continue
            name, uuid, kind, device = parts[0], parts[1], parts[2], parts[3]
            device_by_uuid[uuid] = device
            if kind == "vlan":
                out.append((name, device))
        return (out, device_by_uuid)

    def list_vlans(self) -> list[VlanInterface]:
        profiles, device_by_uuid = self._vlan_profiles()
        vlans: list[VlanInterface] = []
        for name, device in profiles:
            try:
                res = self._run(["nmcli", "-t", "-f", "vlan.parent,vlan.id", "connection", "show", "id", name])
            except (RuntimeError, FileNotFoundError, subprocess.SubprocessError):
                continue
            parsed = self._parse_show(res.stdout)
            raw_parent = (parsed.get("vlan.parent") or [""])[0]
            raw_id = (parsed.get("vlan.id") or [""])[0]
            try:
                vlan_id = int(raw_id)
            except ValueError:
                continue
            # ``vlan.parent`` holds either the parent interface name or the
            # UUID of the parent's own profile, depending on how the VLAN was
            # created. Resolve the UUID form back to a device so the caller
            # always gets a name.
            parent = device_by_uuid.get(raw_parent, raw_parent)
            if not parent or not device:
                continue
            vlans.append(VlanInterface(name=device, parent=parent, vlan_id=vlan_id))
        return vlans

    def create_vlan(self, parent: str, vlan_id: int) -> ApplyResult:
        name = vlan_interface_name(parent, vlan_id)
        ok, detail = self._run_privileged(
            NETWORK_NM_CON_ADD,
            [
                "/usr/bin/nmcli",
                "con",
                "add",
                "type",
                "vlan",
                "con-name",
                name,
                "ifname",
                name,
                "dev",
                parent,
                "id",
                str(vlan_id),
            ],
            reason=f"Create VLAN {vlan_id} on {parent}",
        )
        if not ok:
            return ApplyResult(ok=False, message=detail or f"Could not create VLAN {vlan_id} on {parent}.")
        return ApplyResult(ok=True, message=f"Created {name}. Give it an address with Configure.")

    def delete_vlan(self, name: str) -> ApplyResult:
        profile = self._vlan_profile_name(name)
        if profile is None:
            return ApplyResult(ok=False, message=f"{name} is not a VLAN interface.")
        ok, detail = self._run_privileged(
            NETWORK_NM_CON_DELETE,
            ["/usr/bin/nmcli", "con", "delete", "id", profile],
            reason=f"Delete VLAN profile {profile}",
        )
        if not ok:
            return ApplyResult(ok=False, message=detail or f"Could not delete {name}.")
        return ApplyResult(ok=True, message=f"Deleted {name}.")

    def _vlan_profile_name(self, iface: str) -> str | None:
        """Return the VLAN profile bound to ``iface``, or None when ``iface``
        is not a VLAN. This is the check that keeps ``con delete`` – a
        wildcarded grant – off an operator's physical-NIC profile."""
        profiles, _ = self._vlan_profiles()
        for name, device in profiles:
            if device == iface:
                return name
        return None
