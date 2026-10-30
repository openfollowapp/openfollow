# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann, Michel Honold, and Vinzenz Schultz
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""On-screen Network screen – how to reach the web UI.

The reachability fallback, not a second copy of the web UI's network config:
it lists the URL that reaches this station on each interface and offers only
the actions that restore one. Interface assignment is web-UI only.

Holds the field editor for a static address plus the apply/renew worker that
drives the privileged NetworkAdapter off the main thread."""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from openfollow.configuration import is_simple_mode
from openfollow.net_adapters import Adapter, describe, display_name
from openfollow.net_utils import auto_station_iface, serves_every_interface
from openfollow.network.adapter import (
    ApplyResult,
    Ipv4Config,
    Ipv4Method,
    NetworkAdapter,
    NetworkState,
    SavedInterface,
    format_lease_remaining,
)
from openfollow.network.validate import (
    describe_applied,
    describe_renewed,
    is_link_local,
    parse_ipv4,
    parse_prefix,
    prefix_to_mask,
    validate_apply,
)
from openfollow.runtime import ipv4_digit_grid
from openfollow.station_fqdn import web_ui_host

if TYPE_CHECKING:
    from openfollow.app import OpenFollowApp

logger = logging.getLogger(__name__)


def _network_adapter(app: OpenFollowApp) -> NetworkAdapter | None:
    services = getattr(app, "_runtime_services", None)
    if services is None:
        return None
    return getattr(services, "network_adapter", None)


def _set_banner(app: OpenFollowApp, text: str, level: str = "error") -> None:
    """The screen's result line; ``level`` is "error", "caution", "info" or "success"."""
    app._pi_network_banner = text
    app._pi_network_banner_level = level


def enter_pi_network(app: OpenFollowApp) -> None:
    app._pi_network_active = True
    app._pi_network_banner = ""
    app._pi_network_busy = False
    # The screen opens on the URL list every time; a half-typed address from
    # a previous visit is not what an operator who just lost reachability
    # needs to see first.
    app._pi_network_static_edit = False
    app._pi_network_picker = ""
    _refresh_pi_network_bounded(app)
    # Skip non-selectable header row.
    app._pi_network_index = _first_selectable_index(app)


def _first_selectable_index(app: OpenFollowApp) -> int:
    rows = build_pi_network_rows(app)
    for i, row in enumerate(rows):
        if row.get("kind") in _SELECTABLE_KINDS:
            return i
    # Reachable: a station with no interfaces and an unpinned web UI has
    # nothing to select, and leaves by the cancel button like every other menu.
    return 0


def exit_pi_network(app: OpenFollowApp) -> None:
    app._pi_network_active = False
    app._pi_network_banner = ""
    # Leaving drops the drill-down, so re-entering opens on the interface list
    # rather than inside whichever interface was last looked at.
    app._pi_network_open_iface = ""
    app._pi_network_picker = ""
    # Bump generation to orphan in-flight worker threads.
    app._pi_network_worker_generation = getattr(app, "_pi_network_worker_generation", 0) + 1
    app._pi_network_busy = False


# Cap how long an interactive screen-entry read can stall the 60 fps loop
# when an nmcli/dhcpcd backend hangs (each adapter call has an 8 s timeout).
_ENTRY_READ_BUDGET_S = 2.0


_METHOD_LABELS: dict[Ipv4Method, str] = {
    Ipv4Method.DHCP: "DHCP",
    Ipv4Method.DHCP_WITH_MANUAL_ADDRESS: "DHCP",
    Ipv4Method.STATIC: "Static",
}
_STATIC_LABEL = _METHOD_LABELS[Ipv4Method.STATIC]


@dataclass
class _NetworkSnapshot:
    """Result of a blocking adapter read; applied to the screen cache later."""

    has_adapter: bool
    interfaces: list[Any]
    active_iface: str
    state: NetworkState | None
    pending: Ipv4Config | None
    # Interface name -> "DHCP" / "Static". Read here rather than in the row
    # builder: the rows are rebuilt every frame and this is a backend read,
    # which the render loop cannot afford. This read is already off-thread
    # and budgeted.
    methods: dict[str, str] = field(default_factory=dict)
    # Interface name -> which adapter it is. Off-thread for the same reason:
    # describing one can read sysfs or run ``ip`` / ``networksetup``.
    adapters: dict[str, Adapter] = field(default_factory=dict)
    # Simple: what an Automatic station interface resolves to, read here because
    # it probes the routing table.
    auto_iface: str = ""
    # The station interface the read was made for; None when not read for Simple.
    station: str | None = None
    # Interfaces that are not present but have saved settings.
    saved: tuple[SavedInterface, ...] = ()


def _simple(app: OpenFollowApp) -> bool:
    """Simple network mode: one screen for the station interface, which carries everything."""
    return is_simple_mode(getattr(app, "_config", None))


def _read_pi_network(app: OpenFollowApp) -> _NetworkSnapshot:
    """Blocking adapter read. Pure read of app state – safe off the main thread."""
    adapter = _network_adapter(app)
    if adapter is None:
        # Nothing to edit, but Simple still names the interface carrying the traffic.
        station = _station_iface(app) if _simple(app) else None
        auto = auto_station_iface() if station is not None else ""
        return _NetworkSnapshot(False, [], station or auto, None, None, auto_iface=auto, station=station)
    reading = adapter.read_network()
    interfaces = list(reading.interfaces)
    if not interfaces:
        return _NetworkSnapshot(True, [], "", None, None, saved=reading.saved)
    active = getattr(app, "_pi_network_active_iface", "") or interfaces[0].name
    auto = ""
    methods = {name: _METHOD_LABELS.get(iface_state.ipv4.method, "") for name, iface_state in reading.states.items()}
    adapters = {iface.name: describe(iface.name) for iface in interfaces}
    names = {i.name for i in interfaces}
    # Read once: a hot reload between two reads would pair one station's values with another's.
    station_read = _station_iface(app) if _simple(app) else None
    if station_read is not None:
        station = station_read
        # Always: the picker's Automatic row says where traffic would go.
        auto = auto_station_iface()
        target = station or auto
        if target and target not in names:
            # Never another interface in its place: the screen would edit one it does not name.
            return _NetworkSnapshot(
                True, interfaces, target, None, None, methods, adapters, auto, station, reading.saved
            )
        # Automatic with no address anywhere edits the first interface, never one left over from a visit.
        active = station or auto or interfaces[0].name
    if active not in names:
        active = interfaces[0].name
    state = reading.states.get(active)
    pending = state.ipv4 if state is not None else Ipv4Config(method=Ipv4Method.DHCP)
    # Three servers are shown and allowed; a fourth would make every apply refused, unseen.
    pending = replace(pending, dns=tuple(pending.dns)[:_MAX_DNS])
    return _NetworkSnapshot(
        True, interfaces, active, state, pending, methods, adapters, auto, station_read, reading.saved
    )


def _apply_pi_network_snapshot(app: OpenFollowApp, snap: _NetworkSnapshot) -> None:
    """Write a read snapshot into the screen cache. Main-thread only."""
    # The one funnel for every change this screen makes - entry, interface
    # pick, cancel, and a finished apply / renew - so the address enumeration
    # it reads is re-taken on the next rebuild rather than waiting out its TTL.
    app._pi_network_addr_ts = 0.0
    app._pi_network_reading = False
    app._pi_network_saved = snap.saved
    if not snap.has_adapter:
        app._pi_network_state_cache = None
        app._pi_network_pending_config = None
        app._pi_network_interfaces = []
        app._pi_network_active_iface = snap.active_iface
        app._pi_network_auto_iface = snap.auto_iface
        app._pi_network_read_station = snap.station
        return
    app._pi_network_interfaces = snap.interfaces
    if not snap.interfaces:
        app._pi_network_state_cache = None
        app._pi_network_pending_config = None
        # Nothing of an earlier visit's interface may stay on screen.
        app._pi_network_active_iface = ""
        app._pi_network_auto_iface = ""
        app._pi_network_read_station = snap.station
        return
    app._pi_network_active_iface = snap.active_iface
    app._pi_network_methods = snap.methods
    app._pi_network_adapters = snap.adapters
    app._pi_network_auto_iface = snap.auto_iface
    app._pi_network_read_station = snap.station
    app._pi_network_state_cache = snap.state
    app._pi_network_pending_config = snap.pending
    app._pi_network_dns_edited = False
    app._pi_network_read_dns = tuple(snap.pending.dns) if snap.pending else ()


def _refresh_pi_network(app: OpenFollowApp) -> None:
    """Synchronous read + apply into the screen cache."""
    _apply_pi_network_snapshot(app, _read_pi_network(app))


def _refresh_pi_network_bounded(app: OpenFollowApp) -> None:
    """Refresh on screen entry without stalling the render loop on a hung
    backend: read off-thread with a short join budget. An overrun drops the
    last reading, which may be another interface's. The late read only writes
    the local holder, never app state."""
    holder: list[_NetworkSnapshot] = []
    thread = threading.Thread(
        target=lambda: holder.append(_read_pi_network(app)),
        name="pi-network-read",
        daemon=True,
    )
    thread.start()
    thread.join(timeout=_ENTRY_READ_BUDGET_S)
    if holder:
        _apply_pi_network_snapshot(app, holder[0])
        return
    # Overrun: the last reading may be another interface's, so nothing of it is left to apply.
    app._pi_network_pending_config = None
    app._pi_network_state_cache = None
    app._pi_network_read_station = None
    app._pi_network_reading = True
    _set_banner(app, "Querying network status…", "info")


# An address changes on a human timescale; the rows are rebuilt on the frame
# loop. One enumeration a second is plenty, and an action refreshes it outright.
_IFACE_ADDR_TTL_S = 1.0

_SELECTABLE_KINDS = {"choice", "text", "action"}

# Interface rows carry their interface name in the key so the dispatcher can
# recover it without a parallel index into the list it rendered from.
_IFACE_ROW_PREFIX = "iface:"


def _web_server(app: OpenFollowApp) -> Any:
    return getattr(app, "_web_server", None)


def _served_port(app: OpenFollowApp) -> int:
    """Port the operator should type.

    ``display_port`` over the configured one: an unprivileged station that
    could not take :80 is serving on the fallback, and this screen exists to
    hand out an address that answers.
    """
    server = _web_server(app)
    port = getattr(server, "display_port", None) if server is not None else None
    if port is None:
        port = getattr(app._config, "web_port", 80)
    try:
        return int(port or 80)
    except (TypeError, ValueError):  # pragma: no cover - __post_init__ coerces this
        return 80


def _web_url_for(app: OpenFollowApp, host: str) -> str:
    """``http://<host>``, with the port only when it isn't the default 80."""
    port = _served_port(app)
    return f"http://{host}" if port == 80 else f"http://{host}:{port}"


def _served_bind_host(app: OpenFollowApp) -> str:
    """The address the web server is **actually** listening on.

    Read from the running server, never derived from the config pin. The pin
    fails open, so a pin naming a dark interface leaves the UI on the wildcard
    - and a screen that reported the config would tell an operator that none
    of their addresses work while every one of them does.
    """
    server = _web_server(app)
    host = str(getattr(server, "bind_host", "") or "") if server is not None else ""
    if host:
        return host
    # No server wired (boot, tests): fall back to what it would bind.
    from openfollow.net_utils import resolve_web_bind

    cfg = app._config
    return resolve_web_bind(getattr(cfg, "web_bind", ""), getattr(cfg, "web_bind_iface", ""))[0]


def _serves_every_interface(app: OpenFollowApp) -> bool:
    return serves_every_interface(_served_bind_host(app))


def _station_fqdn(app: OpenFollowApp) -> str:
    return str(getattr(app._config, "station_fqdn", "") or "")


def _fqdn_problems(app: OpenFollowApp) -> tuple[str, ...]:
    """What the last DHCP change for the configured name could not do."""
    problems = getattr(getattr(app, "_runtime_services", None), "station_fqdn_problems", None)
    return tuple(problems()) if problems is not None else ()


def _web_ui_host(app: OpenFollowApp) -> str:
    """The station's FQDN, else ``<hostname>.local``, or "" when the host has no usable name."""
    try:
        return web_ui_host(_station_fqdn(app))
    except Exception:  # noqa: BLE001 - a hostname lookup must not blank the screen
        return ""


def _iface_addresses(app: OpenFollowApp) -> list[tuple[str, str]]:
    """``(name, address)`` for each interface on the screen, one enumeration.

    The whole row list is rebuilt every frame while the screen is open, so a
    per-interface lookup would walk every NIC dozens of times a second for
    data that cannot change between two reads of the same frame.

    Held for :data:`_IFACE_ADDR_TTL_S` for the same reason one frame's reads
    are shared: this runs on the clock that drives marker state and every
    output, and an address does not change 60 times a second. Every state
    change on this screen funnels through :func:`_apply_pi_network_snapshot`,
    which drops the cache - so an action's result is on screen at once rather
    than up to an interval late.
    """
    from openfollow.net_utils import list_iface_ipv4

    now = time.monotonic()
    if now - float(getattr(app, "_pi_network_addr_ts", 0.0)) >= _IFACE_ADDR_TTL_S:
        app._pi_network_addr_cache = dict(list_iface_ipv4())
        app._pi_network_addr_ts = now
    addresses: dict[str, str] = getattr(app, "_pi_network_addr_cache", None) or {}
    return [
        (name, addresses.get(name, ""))
        for name in (str(getattr(i, "name", "") or "") for i in getattr(app, "_pi_network_interfaces", []))
        if name
    ]


def _labels(app: OpenFollowApp) -> dict[str, str]:
    return dict(getattr(getattr(app, "_config", None), "interface_labels", None) or {})


def _absent_rows(app: OpenFollowApp, ifaces: list[tuple[str, str]]) -> list[dict[str, object]]:
    """Adapters that are not plugged in but labelled or with saved settings: listed, with nothing to open.

    A saved one shows the address it comes up with, each VLAN under its parent, as the web card lists them.
    """
    labels = _labels(app)
    present = {name for name, _address in ifaces}
    saved = {entry.name: entry for entry in getattr(app, "_pi_network_saved", ())}

    def _order(name: str) -> tuple[str, int]:
        entry = saved.get(name)
        if entry is None or entry.vlan_id is None:
            return (name, -1)
        return (entry.vlan_parent or name, entry.vlan_id)

    return [
        {
            "kind": "display",
            "key": f"absent:{name}",
            "label": display_name(name, labels),
            "value": (entry.ipv4.address or "") if (entry := saved.get(name)) is not None else "",
            "pill": "not connected",
            "pill_level": "",
        }
        for name in sorted((set(labels) | set(saved)) - present, key=_order)
    ]


def _adapter_rows(adapter: Adapter, labels: dict[str, str]) -> list[dict[str, object]]:
    """Which physical adapter an interface is: the same rows the web card shows."""
    fields = adapter.fields(labels)
    shown = [
        ("vlan", "VLAN", fields["vlan_on"]),
        ("port", "Port", fields["port"]),
        ("model", "Model", fields["model"]),
        ("mac", "MAC address", fields["mac"]),
    ]
    rows: list[dict[str, object]] = [
        {"kind": "display", "key": f"adapter_{key}", "label": label, "value": value}
        for key, label, value in shown
        if value
    ]
    return [{"kind": "header", "label": "Adapter"}, *rows] if rows else []


def _iface_rows(app: OpenFollowApp, ifaces: list[tuple[str, str]]) -> list[dict[str, object]]:
    """One row per interface: the URL that reaches this UI there.

    A restricted web UI answers at one address only, so the others say why
    they won't work instead of showing a URL that would fail. Handing the
    operator an address that doesn't answer is the one thing this screen must
    not do - which is why "restricted" is judged from the live bind and not
    from the pin that asked for it.
    """
    everywhere = _serves_every_interface(app)
    bind_host = _served_bind_host(app)
    methods = getattr(app, "_pi_network_methods", {}) or {}
    labels = _labels(app)
    rows: list[dict[str, object]] = []
    for name, address in ifaces:
        # The pill states the one thing worth knowing at a glance, worst first:
        # anything that breaks reachability outranks how the address was come
        # by, because that is what the operator is on this screen to find. No
        # address is neutral: an adapter nobody plugged in is not a fault.
        if not address:
            pill, level = "no address", ""
        elif is_link_local(address):
            # Only DHCP gives itself one; an address set by hand is not a fallback.
            pill, level = ("link-local" if methods.get(name) == _STATIC_LABEL else "fallback"), "error"
        elif not everywhere and address != bind_host:
            pill, level = "web UI not here", "info"
        else:
            pill, level = methods.get(name, ""), ""
        rows.append(
            {
                "kind": "choice",
                "key": f"{_IFACE_ROW_PREFIX}{name}",
                "label": display_name(name, labels),
                "value": address,
                "pill": pill,
                "pill_level": level,
                # Confirming this row opens that interface's own screen.
                "opens": True,
            }
        )
    return rows


def _reachability_notices(app: OpenFollowApp, ifaces: list[tuple[str, str]]) -> list[dict[str, object]]:
    """The states that break reachability without looking broken, station-wide.

    Per-interface trouble is not here: each row carries a pill saying so, and
    the sentence explaining it belongs on that interface's own screen. Repeating
    it once per adapter turned the bottom of a five-interface list into a block
    of warnings that said nothing the rows had not already said.

    What remains is about the station rather than an adapter: a restricted web
    UI explains why the other rows have no URL, and a pin that missed explains
    why the UI is reachable everywhere despite the config asking otherwise.
    """
    notices: list[dict[str, object]] = []
    if not _serves_every_interface(app):
        bind_host = _served_bind_host(app)
        if bind_host in {address for _name, address in ifaces if address}:
            notices.append(
                {"kind": "notice", "level": "info", "label": f"Web UI is served only at {bind_host}", "value": ""}
            )
        else:
            # The bind is fixed for the life of the process, so an address that
            # moved under it (a DHCP lease change on the pinned interface)
            # leaves the UI answering nowhere. Naming it as gone is the
            # difference between a station that looks dead and one that tells
            # the operator which action brings it back.
            notices.append(
                {
                    "kind": "notice",
                    "level": "error",
                    "label": f"Web UI is served at {bind_host}, which no interface has any more - restart to move it",
                    "value": "",
                }
            )
    else:
        banner = _web_bind_banner(app)
        if banner:
            # The UI still answers everywhere; the isolation the pin asked for is what is missing.
            notices.append({"kind": "notice", "level": "caution", "label": banner, "value": ""})
    return notices


def _web_bind_banner(app: OpenFollowApp) -> str:
    """The runtime's own account of a pin it could not honour, or "".

    One guard covers all three ways this is absent - no server yet, a server
    without the accessor, and a provider that raises. The banner is decoration
    on a screen whose job is fixing reachability; none of those may cost it
    the URLs it exists to show.
    """
    try:
        return str(_web_server(app).get_web_bind_advisory().get("banner", "") or "")
    except Exception:  # noqa: BLE001
        return ""


def _addr_of(pending: Ipv4Config | None, field: str) -> str:
    if pending is None:
        return "\u2013"
    value = getattr(pending, field, None)
    return str(value) if value else "\u2013"


def _prefix_text(pending: Ipv4Config | None) -> str:
    """The dotted subnet mask - more operators recognise it from router
    consoles than the CIDR prefix length."""
    if pending and pending.prefix is not None:
        mask = prefix_to_mask(pending.prefix)
        if mask is not None:
            return mask
    return "\u2013"


def build_pi_network_rows(app: OpenFollowApp) -> list[dict[str, object]]:
    """Rows for the on-screen Network screen: how to reach the web UI.

    This screen is the reachability fallback, not a second copy of the web
    UI's network config. Interface assignment lives in the web UI; here the
    question is only "which address do I type", and the actions are the ones
    that restore an answer to it.

    Rows carry a ``kind`` discriminator:

    - ``"header"``  – section heading, not selectable
    - ``"display"`` – read-only value, not selectable
    - ``"notice"``  – a reachability warning, not selectable
    - ``"choice"``  – an interface row; confirming it makes that interface
      the one the actions below name
    - ``"text"``    – selectable; Enter opens the field editor
    - ``"action"``  – selectable; Enter runs the action
    """
    adapter = _network_adapter(app)
    writable = bool(adapter and adapter.is_writable())
    pending: Ipv4Config | None = getattr(app, "_pi_network_pending_config", None)
    busy = bool(getattr(app, "_pi_network_busy", False))
    static_edit = bool(getattr(app, "_pi_network_static_edit", False))

    ifaces = _iface_addresses(app)
    if _simple(app):
        picker = str(getattr(app, "_pi_network_picker", "") or "")
        if picker == "interface":
            return _station_picker_rows(app, ifaces)
        if picker == "method":
            return _method_picker_rows(pending)
        return _simple_rows(app, ifaces, writable=writable, busy=busy, pending=pending)
    opened = str(getattr(app, "_pi_network_open_iface", "") or "")
    if opened:
        return _iface_detail_rows(
            app,
            opened,
            ifaces,
            writable=writable,
            busy=busy,
            static_edit=static_edit,
            pending=pending,
        )
    return _iface_list_rows(app, ifaces)


def _iface_list_rows(app: OpenFollowApp, ifaces: list[tuple[str, str]]) -> list[dict[str, object]]:
    """The first screen: where the station can be reached, and on what.

    Choosing an interface opens its own screen rather than retargeting actions
    further down this one. An operator cannot be expected to notice that a list
    formatted as addresses is also the control that decides what the buttons
    below it will change - and the old screen opened already naming an
    interface nobody had picked.
    """
    rows: list[dict[str, object]] = [{"kind": "header", "label": "Open on a computer on the same network"}]
    fqdn = _station_fqdn(app)
    host = _web_ui_host(app)
    if host:
        # First, and not selectable: there is no per-interface screen it could
        # open. It is also the one line an operator can read out over comms. The
        # ``.local`` name answers on any interface; an FQDN points wherever the
        # venue's DNS does, so it claims none.
        value = "" if fqdn else "any interface"
        rows.append({"kind": "display", "key": "web_host", "label": _web_url_for(app, host), "value": value})
    for problem in _fqdn_problems(app):
        rows.append({"kind": "notice", "level": "caution", "label": f"DHCP: {problem}", "value": ""})

    rows.append({"kind": "header", "label": "Interfaces"})
    rows.extend(_iface_rows(app, ifaces))
    rows.extend(_absent_rows(app, ifaces))
    rows.extend(_reachability_notices(app, ifaces))

    restricted = _web_ui_is_restricted(app)
    if restricted or fqdn:
        # The heading comes with the action, not before it: a section whose
        # only content was a Back button announced a remedy that was not there.
        rows.append({"kind": "header", "label": "If you still can't reach it"})
    # Both belong to no single interface, so they stay on this screen, and are
    # deliberately not gated on ``writable``: they write config, not the network
    # stack, so they stay available on a host whose addressing this build cannot
    # manage - which is exactly where a lockout would strand the operator otherwise.
    if restricted:
        rows.append({"kind": "action", "key": "web_unpin", "label": "Serve web UI on all interfaces", "value": ""})
    if fqdn:
        # Where this build manages the network, removing it reconnects every interface.
        adapter = _network_adapter(app)
        label = "Remove FQDN (interrupts network traffic)" if adapter and adapter.is_writable() else "Remove FQDN"
        rows.append({"kind": "action", "key": "fqdn_remove", "label": label, "value": ""})
    return rows


_METHOD_CHOICES: tuple[tuple[Ipv4Method, str], ...] = (
    (Ipv4Method.DHCP, "DHCP"),
    (Ipv4Method.DHCP_WITH_MANUAL_ADDRESS, "DHCP with manual address"),
    (Ipv4Method.STATIC, "Static"),
)
_DNS_FIELDS = ("dns_1", "dns_2", "dns_3")
_MAX_DNS = len(_DNS_FIELDS)
_PICK_IFACE_PREFIX = "pick:"
_PICK_METHOD_PREFIX = "method:"


def _station_iface(app: OpenFollowApp) -> str:
    """The station interface as configured; "" is Automatic."""
    return str(getattr(getattr(app, "_config", None), "psn_source_iface", "") or "")


def _simple_rows(
    app: OpenFollowApp,
    ifaces: list[tuple[str, str]],
    *,
    writable: bool,
    busy: bool,
    pending: Ipv4Config | None,
) -> list[dict[str, object]]:
    """Simple: the station interface on one screen - the URL, the interface, IPv4, DNS and the actions."""
    rows: list[dict[str, object]] = [{"kind": "header", "label": "Open on a computer on the same network"}]
    fqdn = _station_fqdn(app)
    host = _web_ui_host(app)
    if host:
        rows.append(
            {
                "kind": "display",
                "key": "web_host",
                "label": _web_url_for(app, host),
                "value": "" if fqdn else "any interface",
            }
        )
    for problem in _fqdn_problems(app):
        rows.append({"kind": "notice", "level": "caution", "label": f"DHCP: {problem}", "value": ""})
    # ``web_bind`` and a pin awaiting its restart survive into Simple.
    rows.extend(_reachability_notices(app, ifaces))

    reading = bool(getattr(app, "_pi_network_reading", False))
    station = _station_iface(app)
    auto = str(getattr(app, "_pi_network_auto_iface", "") or "")
    # Unanswered, the last reading may be another interface's: nothing of it is shown.
    name = "" if reading else str(getattr(app, "_pi_network_active_iface", "") or "")
    labels = _labels(app)
    shown = display_name(name, labels)
    address = next((addr for iface_name, addr in ifaces if iface_name == name), "")
    methods = getattr(app, "_pi_network_methods", {}) or {}
    rows.append({"kind": "header", "label": "Interface"})
    if reading:
        pill, level = "", ""
    elif address and is_link_local(address):
        pill, level = ("link-local" if methods.get(name) == _STATIC_LABEL else "fallback"), "error"
    elif address:
        pill, level = methods.get(name, ""), ""
    else:
        pill, level = "no address", ""
    rows.append(
        {
            "kind": "choice",
            "key": "interface",
            "label": "Interface",
            "value": display_name(station, labels)
            or (f"Automatic ({display_name(auto, labels)})" if auto else "Automatic"),
            "pill": pill,
            "pill_level": level,
            "opens": True,
        }
    )
    if address and is_link_local(address):
        rows.append(
            {
                "kind": "notice",
                "level": "error",
                "label": f"{shown} has the link-local address {address}. "
                "Other machines on this network may not reach the station here.",
                "value": "",
            }
        )

    present = name in {iface.name for iface in getattr(app, "_pi_network_interfaces", None) or []}
    # Read for another station interface, or in Advanced: the values below are not this one's.
    current = getattr(app, "_pi_network_read_station", None) == station
    if reading:
        # The banner says it is still being read; neither notice below would be true yet.
        current = False
    elif not present and _network_adapter(app) is not None:
        # Up with an address but not the backend's: it carries the traffic, it is just not edited here.
        kernel_address = (getattr(app, "_pi_network_addr_cache", None) or {}).get(name, "")
        if name and (kernel_address or not station):
            missing, notice_level = f"{shown}'s addressing is not managed here.", "info"
        else:
            missing = f"{shown} is not connected." if name else "This station has no network interface."
            notice_level = "error"
        rows.append({"kind": "notice", "level": notice_level, "label": missing, "value": ""})
    elif not current:
        rows.append(
            {
                "kind": "notice",
                "level": "info",
                "label": "The network settings changed. Close and reopen this screen.",
                "value": "",
            }
        )
    editable = writable and present and current
    method = pending.method if pending else Ipv4Method.DHCP
    # Named, because Automatic with no address anywhere edits the first interface.
    rows.append({"kind": "header", "label": f"IPv4 \u2013 {shown}" if name else "IPv4"})
    rows.append(
        {
            "kind": "choice" if editable else "display",
            "key": "method",
            "label": "Configure",
            "value": dict(_METHOD_CHOICES).get(method, str(method)),
            "opens": editable,
        }
    )
    owns_address = editable and method in (Ipv4Method.STATIC, Ipv4Method.DHCP_WITH_MANUAL_ADDRESS)
    owns_rest = editable and method == Ipv4Method.STATIC
    rows.append(
        {
            "kind": "text" if owns_address else "display",
            "key": "address",
            "label": "IP Address",
            "value": _addr_of(pending, "address"),
        }
    )
    rows.append(
        {"kind": "text" if owns_rest else "display", "key": "prefix", "label": "Subnet", "value": _prefix_text(pending)}
    )
    rows.append(
        {
            "kind": "text" if owns_rest else "display",
            "key": "router",
            "label": "Router (optional)" if owns_rest else "Router",
            "value": _addr_of(pending, "router"),
        }
    )
    state: NetworkState | None = getattr(app, "_pi_network_state_cache", None)
    # A lease is the interface's own, not the method picked but not yet applied.
    on_dhcp = state is not None and state.ipv4.method != Ipv4Method.STATIC
    if on_dhcp and state is not None and state.lease and state.lease.lease_seconds_remaining:
        lease = format_lease_remaining(state.lease.lease_seconds_remaining)
        rows.append({"kind": "display", "key": "lease", "label": "Lease", "value": lease})

    rows.append({"kind": "header", "label": "DNS Servers"})
    dns = list(pending.dns) if pending else []
    for i, key in enumerate(_DNS_FIELDS):
        value = dns[i] if i < len(dns) and dns[i] else ""
        rows.append({"kind": "text" if editable else "display", "key": key, "label": f"DNS {i + 1}", "value": value})

    rows.append({"kind": "header", "label": "Actions"})
    if editable:
        rows.append({"kind": "action", "key": "apply", "label": "Working..." if busy else "Apply Changes", "value": ""})
        if on_dhcp:
            rows.append(
                {"kind": "action", "key": "renew", "label": "Working..." if busy else "Renew DHCP lease", "value": ""}
            )
    elif not writable:
        rows.append(
            {
                "kind": "notice",
                "level": "info",
                "label": "This station's addressing cannot be changed from here.",
                "value": "",
            }
        )
    # ``web_bind`` survives Simple, so the lockout escape does too.
    if _web_ui_is_restricted(app):
        rows.append({"kind": "action", "key": "web_unpin", "label": "Serve web UI on all interfaces", "value": ""})
    if fqdn:
        label = "Remove FQDN (interrupts network traffic)" if writable else "Remove FQDN"
        rows.append({"kind": "action", "key": "fqdn_remove", "label": label, "value": ""})
    return rows


def _station_picker_rows(app: OpenFollowApp, ifaces: list[tuple[str, str]]) -> list[dict[str, object]]:
    """Simple: Automatic or a named interface; the one chosen carries every plane."""
    station = _station_iface(app)
    auto = str(getattr(app, "_pi_network_auto_iface", "") or "")
    labels = _labels(app)
    now = f"now {display_name(auto, labels)}" if auto else ""
    if not ifaces:
        # No backend to list them: the kernel's interfaces with an address still carry traffic.
        ifaces = sorted((getattr(app, "_pi_network_addr_cache", None) or {}).items())
    auto_address = next((addr for name, addr in ifaces if name == auto), "")
    rows: list[dict[str, object]] = [{"kind": "header", "label": "Carries all of this station's traffic"}]
    rows.append(
        {
            "kind": "choice",
            "key": _PICK_IFACE_PREFIX,
            "label": "Automatic",
            "value": f"{now} \u2013 {auto_address}" if auto and auto_address else now,
            "pill": "current" if not station else "",
            "pill_level": "",
        }
    )
    names = [name for name, _addr in ifaces]
    if station and station not in names:
        ifaces = [*ifaces, (station, "")]
    for name, address in ifaces:
        rows.append(
            {
                "kind": "choice",
                "key": f"{_PICK_IFACE_PREFIX}{name}",
                "label": display_name(name, labels),
                "value": address,
                "pill": "current" if name == station else ("" if address else "no address"),
                # Neutral like the Advanced list: an adapter nobody plugged in is not a fault.
                "pill_level": "",
            }
        )
    return rows


def _method_picker_rows(pending: Ipv4Config | None) -> list[dict[str, object]]:
    current = pending.method if pending else Ipv4Method.DHCP
    rows: list[dict[str, object]] = [{"kind": "header", "label": "How this interface gets its address"}]
    for method, label in _METHOD_CHOICES:
        rows.append(
            {
                "kind": "choice",
                "key": f"{_PICK_METHOD_PREFIX}{method.value}",
                "label": label,
                "value": "",
                "pill": "current" if method == current else "",
                "pill_level": "",
            }
        )
    return rows


def _open_picker(app: OpenFollowApp, picker: str) -> None:
    app._pi_network_picker = picker
    rows = build_pi_network_rows(app)
    current = next((row.get("key") for row in rows if row.get("pill") == "current"), "")
    _focus_row(app, str(current or ""))


def _close_picker(app: OpenFollowApp) -> None:
    picker = str(getattr(app, "_pi_network_picker", "") or "")
    app._pi_network_picker = ""
    _focus_row(app, "interface" if picker == "interface" else "method")


def _pick_station_iface(app: OpenFollowApp, name: str) -> None:
    """Move every plane to ``name`` ("" is Automatic) through the config file and its hot reload.

    The file is written as it is on disk and applied at once, so the running
    config never gets ahead of what was saved.
    """
    from openfollow.configuration import config_write_lock, load_config, save_config

    _close_picker(app)
    if name == _station_iface(app):
        return
    try:
        with config_write_lock:
            # A missing file would load as defaults and be saved as such over everything else.
            cfg = load_config(app._config_path, strict=True, missing_ok=False)
            cfg.psn_source_iface = name
            save_config(cfg, app._config_path)
    except Exception:  # noqa: BLE001 - a failed save is reported on the screen
        from openfollow.runtime.app_modes import _why_unsaved

        logger.exception("Could not save the station interface")
        _set_banner(app, f"Could not save - the interface is unchanged. {_why_unsaved(app, 'pick it again')}")
        return
    # Applied now, not on the next one-second poll: the screen re-reads from the running config.
    app._last_config_check = 0.0
    app._check_config_reload()
    _refresh_pi_network_bounded(app)
    target = name or str(getattr(app, "_pi_network_auto_iface", "") or "")
    target_address = (getattr(app, "_pi_network_addr_cache", None) or {}).get(target, "")
    shown = display_name(target, _labels(app))
    if _station_iface(app) != name:
        _set_banner(app, "Saved, but not applied yet. Check the station's status rows.", "caution")
    elif not target_address:
        # Every plane fails closed on an interface without an address.
        _set_banner(
            app,
            f"All traffic now uses {shown or 'the automatic interface'}, which has no address: "
            "nothing is sent until it gets one.",
            "caution",
        )
    else:
        _set_banner(app, f"All traffic now uses {shown}.", "success")
    _focus_row(app, "interface")


def _pick_method(app: OpenFollowApp, value: str) -> None:
    """Change the pending method; only the fields that method owns carry over.

    DNS does too, except from a static interface to either DHCP method: its
    servers are the static network's, and DHCP brings its own. Servers typed
    here always stay.
    """
    method = next((m for m, _label in _METHOD_CHOICES if m.value == value), None)
    _close_picker(app)
    if method is None:
        return
    pending = getattr(app, "_pi_network_pending_config", None) or Ipv4Config(method=method)
    keep_address = method != Ipv4Method.DHCP
    keep_rest = method == Ipv4Method.STATIC
    app._pi_network_pending_config = Ipv4Config(
        method=method,
        address=pending.address if keep_address else None,
        prefix=pending.prefix if keep_rest else None,
        router=pending.router if keep_rest else None,
        dns=(
            ()
            if method != Ipv4Method.STATIC
            and _interface_is_static(app)
            and not getattr(app, "_pi_network_dns_edited", False)
            else pending.dns
        ),
    )
    _focus_row(app, "method")


def _interface_is_static(app: OpenFollowApp) -> bool:
    state: NetworkState | None = getattr(app, "_pi_network_state_cache", None)
    return state is not None and state.ipv4.method == Ipv4Method.STATIC


def _iface_detail_rows(
    app: OpenFollowApp,
    name: str,
    ifaces: list[tuple[str, str]],
    *,
    writable: bool,
    busy: bool,
    static_edit: bool,
    pending: Ipv4Config | None,
) -> list[dict[str, object]]:
    """The second screen: one interface, named in the title, and its actions.

    Every action here acts on ``name``, so none of them repeats it - the title
    carries the subject, which is what stops the operator having to hold it in
    their head while they read down a list.
    """
    address = next((addr for iface_name, addr in ifaces if iface_name == name), "")
    rows: list[dict[str, object]] = [{"kind": "header", "label": "Reach it at"}]
    if not address:
        rows.append({"kind": "display", "key": "url", "label": "-- no address --", "value": ""})
    elif not _serves_every_interface(app) and address != _served_bind_host(app):
        rows.append({"kind": "display", "key": "url", "label": "-- web UI not served here --", "value": address})
    else:
        rows.append({"kind": "display", "key": "url", "label": _web_url_for(app, address), "value": "now"})

    if is_link_local(address):
        set_by_hand = (getattr(app, "_pi_network_methods", {}) or {}).get(name) == _STATIC_LABEL
        rows.append(
            {
                "kind": "notice",
                "level": "error",
                "label": (
                    f"{name} is set to the link-local address {address}. "
                    "Only machines with a link-local address of their own will reach the station here."
                    if set_by_hand
                    else f"No DHCP server answered, so {name} gave itself {address}. "
                    "Other machines on this network will not reach the station here."
                ),
                "value": "",
            }
        )

    adapters = getattr(app, "_pi_network_adapters", None) or {}
    rows.extend(_adapter_rows(adapters.get(name, Adapter(name)), _labels(app)))
    rows.append({"kind": "header", "label": "Change this interface"})
    if not writable:
        rows.append(
            {
                "kind": "notice",
                "level": "info",
                "label": "This station's addressing cannot be changed from here.",
                "value": "",
            }
        )
    elif static_edit:
        rows.append({"kind": "text", "key": "address", "label": "IP Address", "value": _addr_of(pending, "address")})
        rows.append({"kind": "text", "key": "prefix", "label": "Subnet", "value": _prefix_text(pending)})
        rows.append(
            {"kind": "text", "key": "router", "label": "Router (optional)", "value": _addr_of(pending, "router")}
        )
        rows.append({"kind": "action", "key": "apply", "label": "Working..." if busy else "Apply", "value": ""})
        rows.append({"kind": "action", "key": "cancel_static", "label": "Cancel", "value": ""})
    else:
        rows.append({"kind": "action", "key": "dhcp", "label": "Working..." if busy else "Set to DHCP", "value": ""})
        rows.append({"kind": "action", "key": "static", "label": "Set a static address...", "value": ""})
        rows.append(
            {"kind": "action", "key": "renew", "label": "Working..." if busy else "Renew DHCP lease", "value": ""}
        )

    return rows


def _focus_row(app: OpenFollowApp, key: str) -> None:
    """Put the cursor on ``key``, or on the nearest selectable row.

    Every action that reshapes the list has to call this. The cursor is a
    bare index into a list that is rebuilt from scratch each frame, so a row
    appearing or disappearing under it silently moves the highlight onto a
    different action - and an index past the end leaves Enter a no-op with
    nothing on screen explaining why.
    """
    rows = build_pi_network_rows(app)
    for i, row in enumerate(rows):
        if row.get("key") == key and row.get("kind") in _SELECTABLE_KINDS:
            app._pi_network_index = i
            return
    idx = min(max(int(getattr(app, "_pi_network_index", 0)), 0), max(len(rows) - 1, 0))
    while idx >= 0:
        if rows[idx].get("kind") in _SELECTABLE_KINDS:
            app._pi_network_index = idx
            return
        idx -= 1
    app._pi_network_index = _first_selectable_index(app)


def _pi_network_move(app: OpenFollowApp, step: int) -> None:
    """Move the cursor, skipping header/display rows (non-selectable)."""
    rows = build_pi_network_rows(app)
    total = len(rows)
    if total == 0:
        return
    idx = app._pi_network_index
    # Snap to first selectable row if we landed on a non-selectable one
    # (e.g. after a method change reshaped the list).
    if not 0 <= idx < total or rows[idx].get("kind") not in _SELECTABLE_KINDS:
        for j, row in enumerate(rows):
            if row.get("kind") in _SELECTABLE_KINDS:
                idx = j
                break
    for _ in range(total):
        idx = (idx + step) % total
        if rows[idx].get("kind") in _SELECTABLE_KINDS:
            app._pi_network_index = idx
            return


def _pi_network_confirm(app: OpenFollowApp) -> None:
    rows = build_pi_network_rows(app)
    idx = app._pi_network_index
    if not 0 <= idx < len(rows):
        return
    row = rows[idx]
    if row.get("kind") not in _SELECTABLE_KINDS:
        return
    key = str(row.get("key") or "")
    # While the apply/renew worker is in flight, nothing on the screen acts.
    # Leaving is unaffected: it is the cancel button, which never comes through
    # here, so a hung screen can always be left.
    if getattr(app, "_pi_network_busy", False):
        return
    if key.startswith(_IFACE_ROW_PREFIX):
        _open_pi_network_iface(app, key[len(_IFACE_ROW_PREFIX) :])
    elif key == "interface":
        _open_picker(app, "interface")
    elif key == "method":
        _open_picker(app, "method")
    elif key.startswith(_PICK_IFACE_PREFIX):
        _pick_station_iface(app, key[len(_PICK_IFACE_PREFIX) :])
    elif key.startswith(_PICK_METHOD_PREFIX):
        _pick_method(app, key[len(_PICK_METHOD_PREFIX) :])
    elif key in ("address", "prefix", "router", *_DNS_FIELDS):
        enter_pi_network_field_edit(app, key)
    elif key == "web_unpin":
        _unpin_web_ui(app)
    elif key == "fqdn_remove":
        _remove_fqdn(app)
    elif key == "dhcp":
        _set_pi_network_dhcp(app)
    elif key == "static":
        _begin_static_edit(app)
    elif key == "cancel_static":
        _cancel_static_edit(app)
    elif key == "apply":
        _apply_pi_network(app)
    elif key == "renew":
        _renew_pi_network(app)


def _open_pi_network_iface(app: OpenFollowApp, name: str) -> None:
    """Open ``name``'s own screen.

    Unconditional, unlike the retargeting it replaces: reopening the interface
    already loaded is a normal navigation step, and refusing it would leave
    confirm doing nothing on the row the cursor rests on when the screen opens.

    A half-typed static address belongs to the interface it was started on, so
    opening one drops the editor rather than carrying the values across.
    """
    if not name:
        return
    app._pi_network_open_iface = name
    app._pi_network_active_iface = name
    app._pi_network_static_edit = False
    _refresh_pi_network_bounded(app)
    # The first thing that changes something, not the title above it.
    _focus_row(app, "dhcp")


def _close_pi_network_iface(app: OpenFollowApp) -> None:
    """Return to the interface list, with the cursor back on the row we came from."""
    name = str(getattr(app, "_pi_network_open_iface", "") or "")
    app._pi_network_open_iface = ""
    app._pi_network_static_edit = False
    # The line reports an action on that interface; on the list it reads as the list's.
    app._pi_network_banner = ""
    _focus_row(app, f"{_IFACE_ROW_PREFIX}{name}" if name else "back")


def _leave_pi_network_level(app: OpenFollowApp) -> None:
    """Cancel backs out one level, so it never skips the list on the way out."""
    if _simple(app) and str(getattr(app, "_pi_network_picker", "") or ""):
        _close_picker(app)
        return
    if not _simple(app) and str(getattr(app, "_pi_network_open_iface", "") or ""):
        _close_pi_network_iface(app)
        return
    exit_pi_network(app)
    app._enter_settings_menu()


def _web_ui_is_restricted(app: OpenFollowApp) -> bool:
    """True when the UI answers at one address, or is configured to.

    Covers both pins - the interface name and the older literal ``web_bind``
    address - and stays true for a pin that has not taken effect yet, so the
    escape is offered before the restart as well as after it.
    """
    cfg = app._config
    if getattr(cfg, "web_bind", "") or getattr(cfg, "web_bind_iface", ""):
        return True
    return not _serves_every_interface(app)


def _unpin_web_ui(app: OpenFollowApp) -> None:
    """Clear the web UI's pins and ask for a restart.

    The lockout escape: with both cleared the UI answers on every interface
    again. It cannot take effect without a restart because the listening
    socket is fixed for the life of the server. ``web_bind`` goes too - it
    outranks the interface pin, so clearing only the latter would leave a
    literal-address station exactly as unreachable while reporting success.
    """
    from openfollow.runtime.app_modes import _persist_config

    cfg = app._config
    previous = (getattr(cfg, "web_bind", ""), getattr(cfg, "web_bind_iface", ""))
    if not any(previous):
        # Cleared already, but the listener keeps the pin it started on until a restart,
        # which waits for an update, as the web card's does.
        if not _serves_every_interface(app):
            if app._web_commands.request_restart_unless_updating():
                _set_banner(app, "Web UI will serve on all interfaces after the restart.", "info")
            else:
                _set_banner(app, "An update is running, so OpenFollow was not restarted.", "caution")
            _focus_row(app, "")
        return
    cfg.web_bind = ""
    cfg.web_bind_iface = ""
    # The escape must work on a file that no longer parses.
    if not _persist_config(app, "web_bind", "web_bind_iface", replace_unreadable=True):
        cfg.web_bind, cfg.web_bind_iface = previous
        _set_banner(app, "Could not save - web UI is still pinned.")
        return
    _set_banner(app, "Web UI will serve on all interfaces after the restart.", "info")
    app._web_commands.request_restart()
    # The row just removed itself, and took its heading with it; without this
    # the same index is now a different row, and a second tap would run it.
    # No key to name - the nearest selectable row above is an interface.
    _focus_row(app, "")


def _remove_fqdn(app: OpenFollowApp) -> None:
    """Clear the station FQDN: the way out of a mistyped name, or one the venue's
    DNS never pointed here. The screen names the ``.local`` address again at once.

    Only this field is written, into the file as it is on disk, so a web save the
    hot reload has not picked up yet is kept rather than overwritten.
    """
    from openfollow.runtime.app_modes import _persist_config

    previous = _station_fqdn(app)
    if not previous:
        return
    app._config.station_fqdn = ""
    if not _persist_config(app, "station_fqdn"):
        app._config.station_fqdn = previous
        _set_banner(app, "Could not save - the FQDN is still set.")
        return
    app._runtime_services.apply_station_fqdn_change("")
    _set_banner(app, "FQDN removed.", "success")
    _focus_row(app, "")


def _set_pi_network_dhcp(app: OpenFollowApp) -> None:
    """Put the selected interface on DHCP. One confirm, no form."""
    adapter = _network_adapter(app)
    iface = str(getattr(app, "_pi_network_active_iface", "") or "")
    if adapter is None or not iface:
        _set_banner(app, "No network adapter available.")
        return
    if not adapter.is_writable():
        _set_banner(app, "Read-only host - cannot apply.")
        return
    config = Ipv4Config(method=Ipv4Method.DHCP)
    app._pi_network_pending_config = config
    _start_worker(app, lambda: adapter.apply_ipv4(iface, config), "Apply", done=describe_applied(iface, config))


def _begin_static_edit(app: OpenFollowApp) -> None:
    """Reveal the static-address fields, seeded from what the interface has.

    Seeding from the current addressing means a venue correcting one octet
    types one digit, not a whole address on a d-pad.
    """
    pending: Ipv4Config | None = getattr(app, "_pi_network_pending_config", None)
    # ``dns`` carries over untouched. It is not editable here, but the apply
    # path writes whatever this config holds, so dropping it would silently
    # clear the interface's nameservers as a side effect of setting an address.
    app._pi_network_pending_config = Ipv4Config(
        method=Ipv4Method.STATIC,
        address=pending.address if pending else None,
        prefix=pending.prefix if pending else None,
        router=pending.router if pending else None,
        dns=pending.dns if pending else (),
    )
    app._pi_network_static_edit = True
    _focus_row(app, "address")


def _cancel_static_edit(app: OpenFollowApp) -> None:
    """Drop the typed values and go back to the interface's real state.

    Bounded, like every other on-screen read: a hung network backend must
    cost this screen its refresh, not the frame loop that every output
    depends on.
    """
    app._pi_network_static_edit = False
    _refresh_pi_network_bounded(app)
    _focus_row(app, "static")


def process_pi_network_input(app: OpenFollowApp) -> None:
    input_manager = app._input_manager
    if input_manager is None:
        return
    try:
        inp = input_manager.gamepad_handler.read_settings_menu_input()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pi network input error: %s", exc)
        return
    if inp.up_pressed:
        _pi_network_move(app, -1)
    if inp.down_pressed:
        _pi_network_move(app, +1)
    if inp.confirm_pressed:
        _pi_network_confirm(app)
    elif inp.cancel_pressed:
        _leave_pi_network_level(app)


def handle_pi_network_key(app: OpenFollowApp, key: str) -> None:
    if key == "ArrowUp":
        _pi_network_move(app, -1)
    elif key == "ArrowDown":
        _pi_network_move(app, +1)
    elif key == "Enter":
        _pi_network_confirm(app)
    elif key == "Escape":
        _leave_pi_network_level(app)


# ---------------------------------------------------------------------------
# Field editor (sub-state of Pi Network)
# ---------------------------------------------------------------------------


_ALLOWED_FIELD_CHARS = set("0123456789.")

# Numeric-keypad equivalents of the allowed characters. The GTK fallback
# poller normalizes keypad digits to "Numpad0".."Numpad9" (so the "numpad"
# movement layout works – see ``_GDK_KEY_MAP`` in window.py) and the keypad
# decimal arrives as the bare "KP_Decimal"/"KP_Separator" keysym name. Map
# them back to the plain characters so an operator can type an IP on the
# numeric keypad, not just the top number row.
_NUMPAD_FIELD_CHARS: dict[str, str] = {f"Numpad{n}": str(n) for n in range(10)}
_NUMPAD_FIELD_CHARS["KP_Decimal"] = "."
_NUMPAD_FIELD_CHARS["KP_Separator"] = "."


# An empty Router or Subnet opens with digits to edit; the first key typed replaces them.
_ROUTER_SEED = ipv4_digit_grid.from_grid("")
_SUBNET_SEED = ipv4_digit_grid.from_grid(ipv4_digit_grid.to_grid("255.255.255.0"))


def enter_pi_network_field_edit(app: OpenFollowApp, field: str) -> None:
    app._pi_network_field_edit_active = True
    app._pi_network_field_name = field
    app._pi_network_field_seeded = False
    # Cursor starts on the first digit; it only matters once the d-pad is used.
    app._pi_network_field_digit_index = 0
    pending: Ipv4Config | None = getattr(app, "_pi_network_pending_config", None)
    if pending is None:
        app._pi_network_field_value = ""
        return
    if field == "address":
        app._pi_network_field_value = pending.address or ""
    elif field == "prefix":
        # Pre-fill with the dotted mask so the operator edits in the
        # form they recognise. ``parse_prefix`` on commit accepts
        # both mask and CIDR notations so a tweak to ``/24`` still
        # works for power users who prefer the prefix form.
        if pending.prefix is not None:
            mask = prefix_to_mask(pending.prefix)
            app._pi_network_field_value = mask or str(pending.prefix)
        else:
            app._pi_network_field_value = _SUBNET_SEED
            app._pi_network_field_seeded = True
    elif field == "router":
        app._pi_network_field_value = pending.router or _ROUTER_SEED
        app._pi_network_field_seeded = not pending.router
    elif field in _DNS_FIELDS:
        index = _DNS_FIELDS.index(field)
        server = pending.dns[index] if index < len(pending.dns) else ""
        app._pi_network_field_value = server or _ROUTER_SEED
        app._pi_network_field_seeded = not server
    else:
        app._pi_network_field_value = ""


def exit_pi_network_field_edit(app: OpenFollowApp) -> None:
    app._pi_network_field_edit_active = False
    app._pi_network_field_name = ""
    app._pi_network_field_value = ""
    app._pi_network_field_seeded = False
    app._pi_network_field_digit_index = 0


def confirm_pi_network_field_edit(app: OpenFollowApp) -> None:
    field = getattr(app, "_pi_network_field_name", "")
    value = ipv4_digit_grid.strip_padding(getattr(app, "_pi_network_field_value", "").strip())
    pending: Ipv4Config | None = getattr(app, "_pi_network_pending_config", None)
    if pending is None or not field:
        exit_pi_network_field_edit(app)
        return

    new_address = pending.address
    new_prefix = pending.prefix
    new_router = pending.router
    new_dns = list(pending.dns)

    if field == "address":
        canon = parse_ipv4(value) if value else None
        if value and canon is None:
            _set_banner(app, "Invalid IPv4 address.")
            return
        new_address = canon
    elif field == "prefix":
        prefix = parse_prefix(value) if value else None
        if value and prefix is None:
            _set_banner(app, "Subnet prefix must be 0-32 or a mask like 255.255.255.0.")
            return
        new_prefix = prefix
    elif field == "router":
        canon = parse_ipv4(value) if value else None
        if value and canon is None:
            _set_banner(app, "Invalid router IPv4 address.")
            return
        # The field opens as 0.0.0.0; left there, it means no router.
        new_router = None if canon == "0.0.0.0" else canon
    elif field in _DNS_FIELDS:
        canon = parse_ipv4(value) if value else None
        if value and canon is None:
            _set_banner(app, "Invalid DNS server address.")
            return
        index = _DNS_FIELDS.index(field)
        new_dns.extend([""] * (index + 1 - len(new_dns)))
        # Like the router: left at 0.0.0.0 it means none.
        new_dns[index] = "" if canon in (None, "0.0.0.0") else canon
        # No gaps: an apply refuses an empty server, so the rest move up.
        new_dns = [server for server in new_dns if server]
        # Edited means different from what was read, so typing the read servers back undoes it.
        app._pi_network_dns_edited = tuple(new_dns) != tuple(getattr(app, "_pi_network_read_dns", ()))

    app._pi_network_pending_config = Ipv4Config(
        method=pending.method,
        address=new_address,
        prefix=new_prefix,
        router=new_router,
        dns=tuple(new_dns),
    )
    app._pi_network_banner = ""
    exit_pi_network_field_edit(app)


def cancel_pi_network_field_edit(app: OpenFollowApp) -> None:
    exit_pi_network_field_edit(app)


def handle_pi_network_field_edit_key(app: OpenFollowApp, key: str) -> None:
    if key == "Escape":
        cancel_pi_network_field_edit(app)
        return
    if key == "Enter":
        confirm_pi_network_field_edit(app)
        return
    seeded = getattr(app, "_pi_network_field_seeded", False)
    if key == "Backspace":
        app._pi_network_field_value = "" if seeded else app._pi_network_field_value[:-1]
        app._pi_network_field_seeded = False
        return
    # Accept numeric-keypad digits/decimal, which arrive as "Numpad5" /
    # "KP_Decimal" rather than the bare characters the top number row sends.
    key = _NUMPAD_FIELD_CHARS.get(key, key)
    if len(key) == 1 and key in _ALLOWED_FIELD_CHARS:
        app._pi_network_field_value = key if seeded else app._pi_network_field_value + key
        app._pi_network_field_seeded = False


def _expand_prefix_for_grid(app: OpenFollowApp) -> None:
    """Put the Subnet field in its mask form before the grid touches it.

    That field also accepts a bare prefix length, and ``24`` has no digit grid:
    read as one it means ``24.0.0.0``, which is not a contiguous mask, so the
    operator's value would be rewritten under them into one the parser then
    rejects. ``255.255.255.0`` is the same subnet in the form the grid can edit.
    """
    if getattr(app, "_pi_network_field_name", "") != "prefix":
        return
    value = getattr(app, "_pi_network_field_value", "").strip()
    if not value or "." in value:
        return
    prefix = parse_prefix(value)
    mask = prefix_to_mask(prefix) if prefix is not None else None
    if mask:
        app._pi_network_field_value = mask


def _field_digit_state(app: OpenFollowApp) -> tuple[str, int]:
    """Current buffer as grid digits, plus the cursor, both bounds-checked."""
    _expand_prefix_for_grid(app)
    digits = ipv4_digit_grid.to_grid(getattr(app, "_pi_network_field_value", ""))
    index = getattr(app, "_pi_network_field_digit_index", 0)
    return digits, max(0, min(ipv4_digit_grid.DIGIT_SLOTS - 1, index))


def _move_field_digit_cursor(app: OpenFollowApp, delta: int) -> None:
    """Step the cursor, padding the buffer on the way.

    The padding is what gives the cursor a character to sit under, so it has
    to happen on the first move rather than on the first digit change. Without
    it an operator navigates blind and meets the cursor for the first time on
    a digit they have already altered.
    """
    digits, index = _field_digit_state(app)
    app._pi_network_field_value = ipv4_digit_grid.from_grid(digits)
    app._pi_network_field_digit_index = ipv4_digit_grid.move_cursor(index, delta)
    app._pi_network_field_seeded = False


def _bump_field_digit(app: OpenFollowApp, delta: int) -> None:
    """Cycle the digit under the cursor and write the padded value back.

    The buffer keeps its padding from here on: it is what holds the cursor and
    the character it points at in fixed correspondence, and ``confirm`` strips
    it again before anything parses the value.
    """
    digits, index = _field_digit_state(app)
    app._pi_network_field_digit_index = index
    app._pi_network_field_value = ipv4_digit_grid.from_grid(ipv4_digit_grid.bump_digit(digits, index, delta))
    app._pi_network_field_seeded = False


def process_pi_network_field_edit_input(app: OpenFollowApp) -> None:
    """Gamepad poll for field editor; Cancel backs out."""
    input_manager = app._input_manager
    if input_manager is None:
        return
    try:
        inp = input_manager.gamepad_handler.read_settings_menu_input()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pi network field-edit gamepad input error: %s", exc)
        return
    if inp.cancel_pressed:
        cancel_pi_network_field_edit(app)
        return
    if inp.confirm_pressed:
        # Confirm with whatever's in the buffer – the validator will
        # reject and keep the editor open if the value is invalid.
        confirm_pi_network_field_edit(app)
        return
    if inp.left_pressed:
        _move_field_digit_cursor(app, -1)
    if inp.right_pressed:
        _move_field_digit_cursor(app, 1)
    if inp.up_pressed:
        _bump_field_digit(app, 1)
    if inp.down_pressed:
        _bump_field_digit(app, -1)


# ---------------------------------------------------------------------------
# Apply / Renew workers
# ---------------------------------------------------------------------------


def _apply_pi_network(app: OpenFollowApp) -> None:
    adapter = _network_adapter(app)
    pending: Ipv4Config | None = getattr(app, "_pi_network_pending_config", None)
    iface = getattr(app, "_pi_network_active_iface", "")
    if adapter is not None and iface and getattr(app, "_pi_network_reading", False):
        _set_banner(app, "Still reading the interface. Try again in a moment.", "info")
        return
    if adapter is None or pending is None or not iface:
        _set_banner(app, "No network adapter available.")
        return
    state: NetworkState | None = getattr(app, "_pi_network_state_cache", None)
    lease_dns = state.lease.dns if state is not None and state.lease is not None else None
    if (
        pending.method in (Ipv4Method.DHCP, Ipv4Method.DHCP_WITH_MANUAL_ADDRESS)
        and not getattr(app, "_pi_network_dns_edited", False)
        and lease_dns is not None
        and tuple(pending.dns) == tuple(lease_dns)[:_MAX_DNS]
    ):
        # The lease's own servers: sent back they would be pinned over the lease's. Manual ones stay.
        pending = replace(pending, dns=())
    errors = validate_apply(pending.method, pending.address, pending.prefix, pending.router, list(pending.dns))
    if errors:
        _set_banner(app, errors[0])
        return
    if not adapter.is_writable():
        _set_banner(app, "Read-only host – cannot apply.")
        return
    _start_worker(app, lambda: adapter.apply_ipv4(iface, pending), "Apply", done=describe_applied(iface, pending))


def _renew_pi_network(app: OpenFollowApp) -> None:
    adapter = _network_adapter(app)
    iface = getattr(app, "_pi_network_active_iface", "")
    if adapter is None or not iface:
        _set_banner(app, "No network adapter available.")
        return
    if not adapter.is_writable():
        _set_banner(app, "Read-only host – cannot renew.")
        return
    _start_worker(app, lambda: adapter.renew_lease(iface), "Renew", done=describe_renewed(iface))


def _start_worker(
    app: OpenFollowApp,
    fn: Callable[[], ApplyResult],
    action_label: str,
    *,
    done: str,
) -> None:
    """Run *fn* off the main thread; *done* is the result line when it succeeds."""
    if getattr(app, "_pi_network_busy", False):
        return
    # exit_pi_network / enter_pi_network clear ``busy`` without joining an
    # in-flight worker, so the busy flag alone can't stop an exit -> re-enter ->
    # re-apply from launching a second privileged apply/renew while the first is
    # still mutating the NIC. Refuse while the previous worker is alive; the
    # generation guard only discards its stale result, it doesn't serialize the
    # overlapping nmcli/dhcpcd sequences.
    prev = getattr(app, "_pi_network_worker", None)
    if prev is not None and prev.is_alive():
        _set_banner(app, "Previous network action still finishing; please wait.", "info")
        return
    app._pi_network_busy = True
    # Broker may prompt for device password on non-Ansible install (web UI only).
    _set_banner(
        app,
        f"{action_label} in progress… "
        "If a password modal appears in the web UI, enter the device "
        "password there to complete this action.",
        "info",
    )
    # Per-launch generation token to orphan stale workers on screen exit.
    generation = getattr(app, "_pi_network_worker_generation", 0) + 1
    app._pi_network_worker_generation = generation

    # The web's apply and renew and the station FQDN's DHCP change hold the same lock:
    # dhcpcd.conf and the NM profiles are read, changed and written back.
    network_change = getattr(getattr(app, "_runtime_services", None), "network_change", None) or contextlib.nullcontext

    def _run() -> None:
        result: ApplyResult
        try:
            with network_change():
                result = fn()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Network %s failed", action_label)
            result = ApplyResult(ok=False, message=str(exc))
        # Re-read the post-action state here (off the main thread), then hand
        # result + snapshot to the main-thread drain. The worker never writes
        # render-read state itself. A failed re-read still hands the result
        # back, or nothing clears ``busy`` and the screen refuses every retry.
        snap: _NetworkSnapshot | None
        try:
            snap = _read_pi_network(app)
        except Exception:  # noqa: BLE001
            logger.exception("Network %s: re-reading the interfaces failed", action_label)
            snap = None
        with app._pi_network_worker_lock:
            app._pi_network_pending_result = (result, action_label, done, generation, snap)

    thread = threading.Thread(target=_run, name=f"pi-network-{action_label.lower()}", daemon=True)
    app._pi_network_worker = thread
    thread.start()


def _finish_worker(
    app: OpenFollowApp,
    result: ApplyResult,
    action_label: str,
    done: str,
    generation: int,
    snap: _NetworkSnapshot | None,
) -> None:
    # Drop late results from orphaned workers: ``exit_pi_network`` and a new
    # ``_start_worker`` both bump the generation. Runs on the main thread (the
    # drain), so the check and writes can't race the exit/re-enter that bumps
    # it. ``busy`` is owned by whoever bumped the generation; don't touch it.
    if getattr(app, "_pi_network_worker_generation", 0) != generation:
        return
    app._pi_network_busy = False
    warnings = list(result.partial_failures)
    if snap is None:
        warnings.append("could not re-read the interfaces")
    if result.pending:
        # Saved, but the interface has no link to come up on yet.
        msg = result.message
        if warnings:
            msg += " Warnings: " + "; ".join(warnings)
        _set_banner(app, msg, "info")
    elif result.ok:
        msg = done
        if warnings:
            msg += " Warnings: " + "; ".join(warnings)
        # Done, but with caveats worth reading: it worked, with a limitation.
        _set_banner(app, msg, "caution" if warnings else "success")
    else:
        _set_banner(app, f"{action_label} failed: {result.message}")
    # No snapshot keeps the last-known state on screen.
    if snap is not None:
        _apply_pi_network_snapshot(app, snap)


def drain_pi_network_worker(app: OpenFollowApp) -> None:
    """Apply a finished worker's result on the main thread. Called each tick."""
    lock = getattr(app, "_pi_network_worker_lock", None)
    if lock is None:
        return
    with lock:
        pending = getattr(app, "_pi_network_pending_result", None)
        app._pi_network_pending_result = None
    if pending is not None:
        _finish_worker(app, *pending)
