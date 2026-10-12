# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""NetworkManager backend (shells out to ``nmcli``)."""

from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

from openfollow.network.adapter import (
    LOOPBACK_NAMES,
    AddressSourceReading,
    ApplyResult,
    BackendReadError,
    Ipv4Config,
    Ipv4Method,
    LeaseInfo,
    NetworkAdapter,
    NetworkInterface,
    NetworkReading,
    NetworkState,
    VlanInterface,
    address_source_of,
    is_loopback,
)
from openfollow.network.validate import is_link_local, parse_prefix, validate_apply, vlan_interface_name
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

_PROFILE_LIST_FIELDS = "NAME,UUID,TYPE,DEVICE,AUTOCONNECT,AUTOCONNECT-PRIORITY,TIMESTAMP"
# Every listed profile's interface, IPv4 method and VLAN link in one call; nmcli
# leaves the vlan fields out for a profile that is not a VLAN.
_PROFILE_FIELDS = "connection.uuid,connection.interface-name,ipv4.method,ipv4.addresses,vlan.parent,vlan.id"
# Every device's state, active profile and address in one call.
_SOURCE_FIELDS = "GENERAL.DEVICE,GENERAL.TYPE,GENERAL.STATE,GENERAL.CON-UUID,IP4.ADDRESS"
# ... and everything the network card shows of it.
_STATE_FIELDS = f"{_SOURCE_FIELDS},GENERAL.HWADDR,IP4.GATEWAY,IP4.DNS,DHCP4.OPTION"

# nmcli(1) EXIT STATUS
_NMCLI_NOT_FOUND = 10

# The devices whose profiles carry the station's DHCP name. Bridges and tunnels
# (docker0, a VPN) belong to whatever made them.
_FQDN_DEVICE_KINDS = frozenset({"ethernet", "wifi", "vlan"})

_SYS_CLASS_NET = Path("/sys/class/net")

# Shown when the privilege broker is absent, which on a real device means the
# sudoers rules were never installed. Kept short: the on-screen result line wraps
# to three lines and cuts the rest.
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


def _as_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0


def _show_records(text: str) -> list[tuple[str, str]]:
    """``(field, value)`` per line of a terse ``connection show``, in order."""
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            out.append((key.strip(), _unescape_terse(value.strip())))
    return out


def _records(text: str, first: str) -> list[dict[str, str]]:
    """One ``{field: value}`` per record of a terse ``show`` over several, each opening with its *first* field."""
    records: list[dict[str, str]] = [{}]  # collects anything before the first record, then dropped
    for key, value in _show_records(text):
        if key == first:
            records.append({})
        records[-1].setdefault(key, value)
    return records[1:]


def _split_ipv4(value: str) -> tuple[str | None, int | None]:
    """``(address, prefix)`` from nmcli's ``a.b.c.d/nn``; either is None when absent or unparsable."""
    if "/" not in value:
        return (value or None, None)
    address, _, prefix = value.partition("/")
    try:
        return (address or None, int(prefix))
    except ValueError:
        return (address or None, None)


def _profile_method(fields: Mapping[str, str]) -> Ipv4Method | None:
    """A profile's IPv4 method from its ``ipv4.method`` / ``ipv4.addresses``; None for any other method."""
    method = fields.get("ipv4.method", "").lower()
    addresses = fields.get("ipv4.addresses", "")
    if method == "manual":
        return Ipv4Method.STATIC if not addresses.startswith("dhcp") else Ipv4Method.DHCP_WITH_MANUAL_ADDRESS
    if method == "auto":
        return Ipv4Method.DHCP
    return None


class _Profile(NamedTuple):
    name: str
    uuid: str
    kind: str
    device: str  # the device it is active on, empty when it is not
    rank: tuple[bool, int, int]  # as autoconnect ranks profiles: enabled, priority, last used


def _profile_rows(text: str) -> list[_Profile]:
    """One :class:`_Profile` per row of a terse ``connection show`` list."""
    out: list[_Profile] = []
    for line in text.splitlines():
        fields = _split_terse(line)
        if len(fields) < 7:
            continue
        name, uuid, kind, device, autoconnect, priority, timestamp = fields[:7]
        out.append(_Profile(name, uuid, kind, device, (autoconnect == "yes", _as_int(priority), _as_int(timestamp))))
    return out


def _active_profile(profiles: Sequence[_Profile], iface: str) -> _Profile | None:
    """The profile active on *iface* itself; a plugin VPN lists the device it runs over."""
    if not iface:
        return None  # every inactive profile lists an empty device
    return next((p for p in profiles if p.device == iface and p.kind != "vpn"), None)


def _saved_profile(
    profiles: Sequence[_Profile], details: Mapping[str, Mapping[str, str]], iface: str
) -> _Profile | None:
    """The saved profile NetworkManager would bring up on *iface*.

    An inactive profile lists no DEVICE, so it is found by the interface it
    names. Ranked as autoconnect ranks them: enabled, priority, last used. A
    profile naming no interface is never claimed: it can come up on any adapter.
    """
    if not iface:
        return None
    bound = [p for p in profiles if details.get(p.uuid, {}).get("connection.interface-name") == iface]
    return max(bound, key=lambda p: (p.rank, p.name)) if bound else None


def _device_by_uuid(profiles: Sequence[_Profile], details: Mapping[str, Mapping[str, str]]) -> dict[str, str]:
    """Each profile's interface: the one it is active on, else the one it names."""
    return {p.uuid: p.device or details.get(p.uuid, {}).get("connection.interface-name", "") for p in profiles}


def _vlan_link(fields: Mapping[str, str], device_by_uuid: Mapping[str, str]) -> tuple[str, int] | None:
    """A VLAN profile's parent interface and VLAN id; None when it cannot be placed."""
    # ``vlan.parent`` holds either the parent interface name or the UUID of the
    # parent's own profile, which names its interface even while another is active on it.
    try:
        vlan_id = int(fields.get("vlan.id", ""))
    except ValueError:
        return None
    raw_parent = fields.get("vlan.parent", "")
    parent = device_by_uuid.get(raw_parent, raw_parent)
    return (parent, vlan_id) if parent else None


def _vlans(profiles: Sequence[_Profile], details: Mapping[str, Mapping[str, str]]) -> tuple[VlanInterface, ...]:
    """The VLAN link of each VLAN profile active on a device."""
    device_by_uuid = _device_by_uuid(profiles, details)
    out: list[VlanInterface] = []
    for profile in profiles:
        if profile.kind != "vlan" or not profile.device:
            continue
        if (link := _vlan_link(details.get(profile.uuid, {}), device_by_uuid)) is not None:
            out.append(VlanInterface(name=profile.device, parent=link[0], vlan_id=link[1]))
    return tuple(out)


def _is_connected(state: str) -> bool:
    """``device show``'s ``100 (connected)``, read as the ``device`` list's ``connected``."""
    return state.partition(" (")[2].startswith("connected")


def _parse_lease(options: Iterable[str]) -> LeaseInfo | None:
    """The lease in NetworkManager's ``DHCP4.OPTION`` values (``name = value``); None when it holds none."""
    addr: str | None = None
    prefix: int | None = None
    router: str | None = None
    dns: list[str] = []
    lease_seconds: int | None = None
    for option in options:
        if "=" not in option:
            continue
        key, _, val = option.partition("=")
        key = key.strip()
        val = val.strip()
        if key == "ip_address":
            addr = val
        elif key == "subnet_mask":
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


def _reading_state(device: Mapping[str, str], method: Ipv4Method) -> NetworkState:
    """One device of a ``device show`` over every device, with its profile's IPv4 method."""
    address, prefix = _split_ipv4(device.get("IP4.ADDRESS[1]", ""))
    interface = NetworkInterface(
        name=device.get("GENERAL.DEVICE", ""),
        mac=device.get("GENERAL.HWADDR") or None,
        kind=device.get("GENERAL.TYPE") or None,
        is_up=_is_connected(device.get("GENERAL.STATE", "")),
    )
    dns = [value for key, value in device.items() if key.startswith("IP4.DNS") and value]
    ipv4 = Ipv4Config(
        method=method,
        address=address,
        prefix=prefix,
        router=device.get("IP4.GATEWAY") or None,
        dns=tuple(dns[:3]),
    )
    lease = _parse_lease(value for key, value in device.items() if key.startswith("DHCP4.OPTION"))
    return NetworkState(interface=interface, ipv4=ipv4, lease=lease)


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


class _Privileged(NamedTuple):
    ok: bool
    detail: str
    returncode: int | None = None  # the command's own exit status, when it ran and failed


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

    def _read(self, argv: list[str]) -> str:
        """stdout of a read-only nmcli call; any failure raises :class:`BackendReadError`."""
        try:
            return self._run(argv).stdout
        except (RuntimeError, FileNotFoundError, subprocess.SubprocessError) as exc:
            raise BackendReadError(str(exc)) from exc

    def _run_privileged(
        self,
        capability: Capability,
        argv: list[str],
        *,
        reason: str,
        allow_prompt: bool = True,
    ) -> _Privileged:
        """Invoke capability via broker."""
        if self._broker is None:
            return _Privileged(False, _NO_BROKER_MESSAGE)
        try:
            proc = self._broker.run(
                capability,
                argv,
                reason=reason,
                timeout=_NMCLI_TIMEOUT,
                allow_prompt=allow_prompt,
            )
        except PrivilegeError as exc:
            return _Privileged(False, str(exc), exc.returncode)
        # Real broker raises on any non-zero rc, so reaching here means
        # success. The stdout is the only useful detail at this point.
        return _Privileged(True, (proc.stdout or "").strip())

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

    def _profile_for(self, iface: str) -> _Profile | None:
        """The profile active on *iface*, else the saved one NetworkManager would bring up there.

        A write names it by UUID: profiles may share a name, and nmcli given a
        name edits the first namesake it finds and deletes them all.
        """
        try:
            profiles = self._read_profiles()
            if (active := _active_profile(profiles, iface)) is not None:
                return active
            return _saved_profile(profiles, self._profile_details(profiles), iface)
        except (RuntimeError, FileNotFoundError, subprocess.SubprocessError):
            return None

    def _read_profiles(self) -> list[_Profile]:
        return _profile_rows(self._run(["nmcli", "-t", "-f", _PROFILE_LIST_FIELDS, "connection", "show"]).stdout)

    def _profile_details(self, profiles: Sequence[_Profile]) -> dict[str, dict[str, str]]:
        """Every listed profile's :data:`_PROFILE_FIELDS`, in one call.

        Raises when a profile was deleted since the list was read: nmcli stops at
        it, so the profiles after it would be missing, and a saved profile ranked
        among only some of them could be the wrong one.
        """
        if not profiles:
            return {}
        argv = ["nmcli", "-t", "-f", _PROFILE_FIELDS, "connection", "show"]
        for profile in profiles:
            argv += ["uuid", profile.uuid]
        return {r["connection.uuid"]: r for r in _records(self._run(argv).stdout, "connection.uuid")}

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
        """False only when the kernel reports no link on *iface*.

        Not NetworkManager's device state: a cable-less adapter reads
        ``disconnected`` there once a profile has been up on it. An admin-down
        interface (rfkill) or an unreadable flag counts as a link, so the profile is
        still brought up and a real failure reported, never a reassuring "pending".
        """
        try:
            return (_SYS_CLASS_NET / iface / "carrier").read_text(encoding="utf-8").strip() != "0"
        except OSError:
            return True

    def _awaiting_dhcp(self, iface: str) -> bool:
        """NetworkManager is running the profile and still asking for a lease.

        With no DHCP server on the network the activation never completes, so
        ``con up`` outlasts the broker's timeout although the profile is applied.
        """
        return (self._device_state(iface) or "").startswith("connecting")

    def _no_profile_message(self, iface: str) -> str:
        """Say what the operator should check, not what the adapter didn't find.

        Kept to one short sentence: the on-screen result line wraps to three
        lines and cuts the rest, which would be the actionable half.
        """
        state = self._device_state(iface)
        if state is None:
            return f"Could not read {iface} from NetworkManager. Try Scan."
        if state == "unmanaged":
            return f"{iface} is not managed by NetworkManager. See the help drawer."
        if not state:
            return f"{iface} is not present. Check the adapter, then Scan."
        return f"No saved profile for {iface}. Connect the cable once to create one."

    def get_state(self, iface: str) -> NetworkState | None:
        return self.read_network().states.get(iface)

    def read_network(self) -> NetworkReading:
        """Three ``nmcli`` runs however many interfaces: every device, the profile list, every profile.

        Never raises: a failed device read reads as no interfaces, a failed profile
        read as every interface on DHCP and no VLANs, never as no addresses.
        """
        try:
            devices = _records(
                self._run(["nmcli", "-t", "-f", _STATE_FIELDS, "device", "show"]).stdout, "GENERAL.DEVICE"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("nmcli device read failed: %s", exc)
            return NetworkReading()
        try:
            profiles = self._read_profiles()
            details = self._profile_details(profiles)
        except Exception as exc:  # noqa: BLE001
            logger.warning("nmcli profile read failed: %s", exc)
            profiles, details = [], {}
        states: dict[str, NetworkState] = {}
        for device in devices:
            name = device.get("GENERAL.DEVICE", "")
            uuid = device.get("GENERAL.CON-UUID", "")
            if not uuid and (saved := _saved_profile(profiles, details, name)) is not None:
                uuid = saved.uuid
            state = _reading_state(device, _profile_method(details.get(uuid, {})) or Ipv4Method.DHCP)
            if name and not is_loopback(state.interface):
                states[name] = state
        return NetworkReading(tuple(state.interface for state in states.values()), states, _vlans(profiles, details))

    # ---- diagnostics ----------------------------------------------------

    def read_address_sources(self) -> list[AddressSourceReading]:
        devices = _records(self._read(["nmcli", "-t", "-f", _SOURCE_FIELDS, "device", "show"]), "GENERAL.DEVICE")
        return [
            self._address_source(device)
            for device in devices
            if device.get("GENERAL.TYPE") != "loopback" and device.get("GENERAL.DEVICE") not in LOOPBACK_NAMES
        ]

    def _address_source(self, device: dict[str, str]) -> AddressSourceReading:
        """Only the active profile describes the address on a device now; a saved one is a guess."""
        name = device.get("GENERAL.DEVICE", "")
        address, _ = _split_ipv4(device.get("IP4.ADDRESS[1]", ""))
        state = device.get("GENERAL.STATE", "")
        if "unmanaged" in state:
            # Checked before the address: NetworkManager may not list one it does not manage.
            return AddressSourceReading(name, address or "", "unknown", "not managed by NetworkManager")
        if not address:
            return AddressSourceReading(name, "", "none")
        if is_link_local(address):
            return AddressSourceReading(name, address, "link-local")
        if "externally" in state:
            # NetworkManager wraps the device in a generated profile reading ``manual``.
            return AddressSourceReading(name, address, "unknown", "configured outside NetworkManager")
        uuid = device.get("GENERAL.CON-UUID", "")
        if not uuid:
            return AddressSourceReading(name, address, "unknown", "no active NetworkManager profile")
        try:
            show = self._read(["nmcli", "-t", "-f", "ipv4.method,ipv4.addresses", "connection", "show", "uuid", uuid])
        except BackendReadError as exc:
            return AddressSourceReading(name, address, "unreadable", str(exc))
        fields = dict(_show_records(show))
        method = _profile_method(fields)
        if method is None:
            word = fields.get("ipv4.method") or "unset"
            return AddressSourceReading(name, address, "unknown", f"profile ipv4.method is {word}")
        return AddressSourceReading(name, address, address_source_of(address, method))

    def _read_lease(self, iface: str) -> LeaseInfo | None:
        try:
            res = self._run(["nmcli", "-t", "-f", "DHCP4.OPTION", "device", "show", iface])
        except (RuntimeError, FileNotFoundError, subprocess.SubprocessError):
            return None
        return _parse_lease(value for _key, value in _show_records(res.stdout))

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

        profile = self._profile_for(iface)
        if profile is None:
            return ApplyResult(
                ok=False,
                message=self._no_profile_message(iface),
            )
        name = profile.name
        # Long argv form to match the sudoers rule; ``uuid`` pins the argument to a UUID,
        # never a name (nmcli's ``con`` verbs read ``--`` as a connection name).
        modify_argv = ["/usr/bin/nmcli", "con", "mod", "uuid", profile.uuid]
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

        ok, detail, _ = self._run_privileged(
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

        if not self._has_carrier(iface):
            # Saved only: con down here leaves the profile blocked from
            # autoconnect, so it would not come up when the cable goes in.
            return ApplyResult(
                ok=True,
                pending=True,
                message=f"Saved; the settings take effect when {iface} has a link.",
            )

        partial: list[str] = []
        # con down can fail; con up failure is fatal. A profile that is not
        # active is already in the state con down asks for.
        down = self._run_privileged(
            NETWORK_NM_CON_DOWN,
            ["/usr/bin/nmcli", "con", "down", "uuid", profile.uuid],
            reason=f"Bring NetworkManager profile {name} down",
        )
        if not down.ok and down.returncode != _NMCLI_NOT_FOUND and down.detail:
            partial.append(f"nmcli con down: {down.detail}")

        up = self._run_privileged(
            NETWORK_NM_CON_UP,
            ["/usr/bin/nmcli", "con", "up", "uuid", profile.uuid],
            reason=f"Bring NetworkManager profile {name} up",
        )
        if not up.ok:
            if config.method == Ipv4Method.DHCP and self._awaiting_dhcp(iface):
                return ApplyResult(
                    ok=True,
                    pending=True,
                    message=f"Saved; no DHCP server has answered on {iface} yet.",
                    partial_failures=tuple(partial),
                )
            return ApplyResult(
                ok=False,
                message=f"Saved, but {iface} could not be brought up ({up.detail})."
                if up.detail
                else f"Saved, but {iface} could not be brought up.",
            )
        return ApplyResult(ok=True, message="Applied.", partial_failures=tuple(partial))

    def renew_lease(self, iface: str) -> ApplyResult:
        profile = self._profile_for(iface)
        if profile is None:
            return ApplyResult(ok=False, message=self._no_profile_message(iface))
        if not self._has_carrier(iface):
            return ApplyResult(ok=False, message=f"{iface} has no link, so there is no lease to request.")
        # NM has no explicit renew verb; use down/up cycle.
        self._run_privileged(
            NETWORK_NM_CON_DOWN,
            ["/usr/bin/nmcli", "con", "down", "uuid", profile.uuid],
            reason=f"Bring NetworkManager profile {profile.name} down",
        )
        up = self._run_privileged(
            NETWORK_NM_CON_UP,
            ["/usr/bin/nmcli", "con", "up", "uuid", profile.uuid],
            reason=f"Renew DHCP lease via NetworkManager profile {profile.name}",
        )
        if not up.ok:
            if self._awaiting_dhcp(iface):
                return ApplyResult(ok=False, message=f"No DHCP server has answered on {iface} yet.")
            return ApplyResult(
                ok=False,
                message=f"Could not renew {iface} ({up.detail})." if up.detail else f"Could not renew {iface}.",
            )
        return ApplyResult(ok=True, message="Lease renewed.")

    # ---- VLAN sub-interfaces --------------------------------------------

    def set_dhcp_fqdn(self, fqdn: str, *, reconnect: bool = True) -> ApplyResult:
        profiles: list[tuple[str, _Profile]] = []
        # An external connection belongs to whatever made it, and one whose state is unknown may be one.
        skipped: list[str] = []
        for device in self.list_interfaces():
            if device.kind not in _FQDN_DEVICE_KINDS:
                continue
            state = self._device_state(device.name)
            if state is None:
                skipped.append(f"{device.name}: NetworkManager did not report its state, so it was left alone.")
            elif "externally" not in state and (profile := self._profile_for(device.name)) is not None:
                profiles.append((device.name, profile))
        if not profiles:
            return ApplyResult(
                ok=False,
                message="No interface has a NetworkManager profile to carry the name.",
                partial_failures=tuple(skipped),
            )
        failures: list[str] = []
        written: list[tuple[str, _Profile]] = []
        for iface, profile in profiles:
            if not reconnect and self._profile_dhcp_fqdn(profile.uuid) == fqdn:
                continue
            # Refused while the profile names its own ``ipv4.dhcp-hostname``: the two
            # are exclusive, and the operator's own setting is left as it is.
            mod = self._run_privileged(
                NETWORK_NM_CON_MOD,
                ["/usr/bin/nmcli", "con", "mod", "uuid", profile.uuid, "ipv4.dhcp-fqdn", fqdn],
                reason=f"Set the DHCP name of NetworkManager profile {profile.name}",
                allow_prompt=False,
            )
            if mod.ok:
                written.append((iface, profile))
            else:
                failures.append(
                    f"Profile '{profile.name}' was not updated ({mod.detail})."
                    if mod.detail
                    else f"Profile '{profile.name}' was not updated."
                )
        for iface, profile in written if reconnect else []:
            if not self._has_carrier(iface):
                continue  # the profile carries the name; it goes out when the link comes up
            up = self._run_privileged(
                NETWORK_NM_CON_UP,
                ["/usr/bin/nmcli", "con", "up", "uuid", profile.uuid],
                reason=f"Reconnect {iface} so its DHCP server sees the name",
                allow_prompt=False,
            )
            if not up.ok and not self._awaiting_dhcp(iface):
                failures.append(
                    f"{iface} could not be reconnected ({up.detail})."
                    if up.detail
                    else f"{iface} could not be reconnected."
                )
        problems = (*skipped, *failures)
        if failures and not written:
            return ApplyResult(ok=False, message="No profile could be updated.", partial_failures=problems)
        return ApplyResult(ok=True, message="Applied." if written else "Unchanged.", partial_failures=problems)

    def _profile_dhcp_fqdn(self, uuid: str) -> str | None:
        """The profile's ``ipv4.dhcp-fqdn``, or None when it cannot be read."""
        try:
            res = self._run(["nmcli", "-g", "ipv4.dhcp-fqdn", "connection", "show", "uuid", uuid])
        except (RuntimeError, FileNotFoundError, subprocess.SubprocessError):
            return None
        return res.stdout.strip()

    def supports_vlans(self) -> bool:
        return True

    def list_vlans(self) -> list[VlanInterface]:
        return list(self.read_network().vlans)

    def create_vlan(self, parent: str, vlan_id: int) -> ApplyResult:
        name = vlan_interface_name(parent, vlan_id)
        ok, detail, _ = self._run_privileged(
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
        return ApplyResult(ok=True, message=f"Created {name}. Give it an address with Edit.")

    def delete_vlan(self, name: str) -> ApplyResult:
        try:
            profiles = self._vlan_profiles(name)
        except (RuntimeError, FileNotFoundError, subprocess.SubprocessError) as exc:
            logger.warning("nmcli VLAN profile read failed: %s", exc)
            return ApplyResult(ok=False, message=f"Could not read the profiles of {name}; nothing was removed.")
        if not profiles:
            return ApplyResult(ok=False, message=f"{name} is not a VLAN interface.")
        argv = ["/usr/bin/nmcli", "con", "delete"]
        for profile in profiles:
            argv += ["uuid", profile.uuid]
        ok, detail, returncode = self._run_privileged(
            NETWORK_NM_CON_DELETE,
            argv,
            reason=f"Delete the profiles of VLAN interface {name}",
        )
        # nmcli still deletes every UUID it finds when one is already gone.
        if not ok and not (returncode == _NMCLI_NOT_FOUND and self._vlan_removed(name)):
            return ApplyResult(ok=False, message=detail or f"Could not remove VLAN interface {name}.")
        return ApplyResult(ok=True, message=f"Removed VLAN interface {name}.")

    def _vlan_profiles(self, iface: str) -> list[_Profile]:
        """The VLAN profile active on ``iface``, then every other one for the
        same parent and VLAN id that autoconnects, which would bring it straight
        back; empty when ``iface`` is not a VLAN. Raises when the profiles
        cannot be read. This is the check that keeps ``con delete`` – a
        wildcarded grant – off an operator's physical-NIC profile, which may
        share a VLAN profile's name: the delete names each by UUID."""
        profiles = self._read_profiles()
        vlans = [p for p in profiles if p.kind == "vlan"]
        active = _active_profile(vlans, iface)
        if active is None:
            return []
        if not (others := [p for p in vlans if p is not active and p.rank[0]]):  # rank[0]: autoconnects
            return [active]
        details = self._profile_details(profiles)
        device_by_uuid = _device_by_uuid(profiles, details)
        if (link := _vlan_link(details.get(active.uuid, {}), device_by_uuid)) is None:
            return [active]
        return [active, *(p for p in others if _vlan_link(details.get(p.uuid, {}), device_by_uuid) == link)]

    def _vlan_removed(self, iface: str) -> bool:
        """No VLAN profile is active on ``iface`` any more; False when that cannot be read."""
        try:
            return not self._vlan_profiles(iface)
        except (RuntimeError, FileNotFoundError, subprocess.SubprocessError):
            return False
