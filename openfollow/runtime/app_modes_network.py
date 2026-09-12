# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""On-screen Network screen – how to reach the web UI.

The reachability fallback, not a second copy of the web UI's network config:
it lists the URL that reaches this station on each interface and offers only
the actions that restore one. Interface assignment is web-UI only.

Holds the field editor for a static address plus the apply/renew worker that
drives the privileged NetworkAdapter off the main thread."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from openfollow.net_utils import WEB_BIND_ALL
from openfollow.network.adapter import (
    ApplyResult,
    Ipv4Config,
    Ipv4Method,
    NetworkAdapter,
    NetworkState,
    is_loopback,
)
from openfollow.network.validate import (
    is_link_local,
    parse_ipv4,
    parse_prefix,
    prefix_to_mask,
    validate_apply,
)
from openfollow.runtime import ipv4_digit_grid

if TYPE_CHECKING:
    from openfollow.app import OpenFollowApp

logger = logging.getLogger(__name__)


def _network_adapter(app: OpenFollowApp) -> NetworkAdapter | None:
    services = getattr(app, "_runtime_services", None)
    if services is None:
        return None
    return getattr(services, "network_adapter", None)


def enter_pi_network(app: OpenFollowApp) -> None:
    app._pi_network_active = True
    app._pi_network_banner = ""
    app._pi_network_busy = False
    # The screen opens on the URL list every time; a half-typed address from
    # a previous visit is not what an operator who just lost reachability
    # needs to see first.
    app._pi_network_static_edit = False
    _refresh_pi_network_bounded(app)
    # Skip non-selectable header row.
    app._pi_network_index = _first_selectable_index(app)


def _first_selectable_index(app: OpenFollowApp) -> int:
    rows = build_pi_network_rows(app)
    for i, row in enumerate(rows):
        if row.get("kind") in _SELECTABLE_KINDS:
            return i
    return 0  # pragma: no cover - build_pi_network_rows always emits a Back action


def exit_pi_network(app: OpenFollowApp) -> None:
    app._pi_network_active = False
    app._pi_network_banner = ""
    # Bump generation to orphan in-flight worker threads.
    app._pi_network_worker_generation = getattr(app, "_pi_network_worker_generation", 0) + 1
    app._pi_network_busy = False


# Cap how long an interactive screen-entry read can stall the 60 fps loop
# when an nmcli/dhcpcd backend hangs (each adapter call has an 8 s timeout).
_ENTRY_READ_BUDGET_S = 2.0


@dataclass
class _NetworkSnapshot:
    """Result of a blocking adapter read; applied to the screen cache later."""

    has_adapter: bool
    interfaces: list[Any]
    active_iface: str
    state: NetworkState | None
    pending: Ipv4Config | None


def _read_pi_network(app: OpenFollowApp) -> _NetworkSnapshot:
    """Blocking adapter read. Pure read of app state – safe off the main thread."""
    adapter = _network_adapter(app)
    if adapter is None:
        return _NetworkSnapshot(False, [], "", None, None)
    # Drop loopback; would become default selection on Linux.
    interfaces = [i for i in adapter.list_interfaces() if not is_loopback(i)]
    if not interfaces:
        return _NetworkSnapshot(True, [], "", None, None)
    active = getattr(app, "_pi_network_active_iface", "") or interfaces[0].name
    if active not in {i.name for i in interfaces}:
        active = interfaces[0].name
    state = adapter.get_state(active)
    pending = state.ipv4 if state is not None else Ipv4Config(method=Ipv4Method.DHCP)
    return _NetworkSnapshot(True, interfaces, active, state, pending)


def _apply_pi_network_snapshot(app: OpenFollowApp, snap: _NetworkSnapshot) -> None:
    """Write a read snapshot into the screen cache. Main-thread only."""
    if not snap.has_adapter:
        app._pi_network_state_cache = None
        app._pi_network_pending_config = None
        return
    app._pi_network_interfaces = snap.interfaces
    if not snap.interfaces:
        app._pi_network_state_cache = None
        app._pi_network_pending_config = None
        return
    app._pi_network_active_iface = snap.active_iface
    app._pi_network_state_cache = snap.state
    app._pi_network_pending_config = snap.pending


def _refresh_pi_network(app: OpenFollowApp) -> None:
    """Synchronous read + apply into the screen cache."""
    _apply_pi_network_snapshot(app, _read_pi_network(app))


def _refresh_pi_network_bounded(app: OpenFollowApp) -> None:
    """Refresh on screen entry without stalling the render loop on a hung
    backend: read off-thread with a short join budget, keep last-known state
    if it overruns. The late read only writes the local holder, never app state."""
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
    else:
        app._pi_network_banner = "Querying network status…"


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
    host = _served_bind_host(app)
    return not host or host == WEB_BIND_ALL


def _mdns_host(app: OpenFollowApp) -> str:
    """``<hostname>.local``, or "" when the host has no usable name.

    Always the running system's hostname, never the station slug the config
    asks for: when the rename was skipped, the desired name sends the operator
    to an address avahi never answers on.
    """
    from openfollow.privilege.device_repair import current_hostname

    try:
        name = current_hostname()
    except Exception:  # noqa: BLE001 - a hostname lookup must not blank the screen
        return ""
    if not name or name == "localhost":
        return ""
    return f"{name}.local"


def _iface_addresses(app: OpenFollowApp) -> list[tuple[str, str]]:
    """``(name, address)`` for each interface on the screen, one enumeration.

    The whole row list is rebuilt every frame while the screen is open, so a
    per-interface lookup would walk every NIC dozens of times a second for
    data that cannot change between two reads of the same frame.
    """
    from openfollow.net_utils import list_iface_ipv4

    addresses = dict(list_iface_ipv4())
    return [
        (name, addresses.get(name, ""))
        for name in (str(getattr(i, "name", "") or "") for i in getattr(app, "_pi_network_interfaces", []))
        if name
    ]


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
    rows: list[dict[str, object]] = []
    for name, address in ifaces:
        if not address:
            label = "-- no address --"
        elif not everywhere and address != bind_host:
            label = "-- web UI not served here --"
        else:
            label = _web_url_for(app, address)
        rows.append({"kind": "choice", "key": f"{_IFACE_ROW_PREFIX}{name}", "label": label, "value": name})
    return rows


def _reachability_notices(app: OpenFollowApp, ifaces: list[tuple[str, str]]) -> list[dict[str, object]]:
    """The states that break reachability without looking broken.

    A link-local address reads like a working lease to anyone who doesn't know
    the 169.254 prefix; a restricted web UI explains why the other rows have
    no URL; and a pin that missed explains why the UI is reachable everywhere
    despite the config asking otherwise.
    """
    notices: list[dict[str, object]] = [
        {"kind": "notice", "label": f"{name}  DHCP unavailable, using fallback {address}", "value": ""}
        for name, address in ifaces
        if is_link_local(address)
    ]
    if not _serves_every_interface(app):
        notices.append(
            {
                "kind": "notice",
                "label": f"Web UI is served only at {_served_bind_host(app)}",
                "value": "",
            }
        )
    else:
        banner = _web_bind_banner(app)
        if banner:
            notices.append({"kind": "notice", "label": banner, "value": ""})
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
    active = str(getattr(app, "_pi_network_active_iface", "") or "")
    static_edit = bool(getattr(app, "_pi_network_static_edit", False))

    rows: list[dict[str, object]] = [{"kind": "header", "label": "Open on a computer on the same network"}]

    ifaces = _iface_addresses(app)
    mdns = _mdns_host(app)
    if mdns:
        # First, and not selectable: it reaches the station on any interface,
        # so there is no per-interface action it could drive. It is also the
        # one line an operator can read out over comms.
        rows.append({"kind": "display", "key": "mdns", "label": _web_url_for(app, mdns), "value": "any interface"})
    rows.extend(_iface_rows(app, ifaces))
    rows.extend(_reachability_notices(app, ifaces))

    rows.append({"kind": "header", "label": "Fix reachability"})
    if _web_ui_is_restricted(app):
        # Deliberately not gated on ``writable``: this writes config, not the
        # network stack, so it stays available on a host whose addressing this
        # build cannot manage - which is exactly where a lockout would strand
        # the operator otherwise.
        rows.append({"kind": "action", "key": "web_unpin", "label": "Serve web UI on all interfaces", "value": ""})
    if writable and active:
        if static_edit:
            rows.append(
                {"kind": "text", "key": "address", "label": "IP Address", "value": _addr_of(pending, "address")}
            )
            rows.append({"kind": "text", "key": "prefix", "label": "Subnet", "value": _prefix_text(pending)})
            rows.append(
                {"kind": "text", "key": "router", "label": "Router (optional)", "value": _addr_of(pending, "router")}
            )
            rows.append(
                {"kind": "action", "key": "apply", "label": "Working…" if busy else f"Apply to {active}", "value": ""}
            )
            rows.append({"kind": "action", "key": "cancel_static", "label": "Cancel", "value": ""})
        else:
            rows.append(
                {"kind": "action", "key": "dhcp", "label": "Working…" if busy else f"Set {active} to DHCP", "value": ""}
            )
            rows.append({"kind": "action", "key": "static", "label": f"Set {active} to a static address…", "value": ""})
            rows.append(
                {
                    "kind": "action",
                    "key": "renew",
                    "label": "Working…" if busy else f"Renew DHCP lease on {active}",
                    "value": "",
                }
            )
    rows.append({"kind": "action", "key": "back", "label": "Back", "value": ""})
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
    # While apply/renew worker is in flight, ignore everything except Back.
    if getattr(app, "_pi_network_busy", False) and key != "back":
        return
    if key == "back":
        exit_pi_network(app)
        app._enter_settings_menu()
    elif key.startswith(_IFACE_ROW_PREFIX):
        _select_pi_network_iface(app, key[len(_IFACE_ROW_PREFIX) :])
    elif key in ("address", "prefix", "router"):
        enter_pi_network_field_edit(app, key)
    elif key == "web_unpin":
        _unpin_web_ui(app)
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


def _select_pi_network_iface(app: OpenFollowApp, name: str) -> None:
    """Make ``name`` the interface the Fix-reachability actions name.

    A half-typed static address belongs to the interface it was started on,
    so switching interfaces drops the editor rather than carrying the values
    across to a different adapter.
    """
    if not name or name == getattr(app, "_pi_network_active_iface", ""):
        return
    app._pi_network_active_iface = name
    app._pi_network_static_edit = False
    _refresh_pi_network_bounded(app)
    _focus_row(app, f"{_IFACE_ROW_PREFIX}{name}")


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
        return
    cfg.web_bind = ""
    cfg.web_bind_iface = ""
    if not _persist_config(app):
        cfg.web_bind, cfg.web_bind_iface = previous
        app._pi_network_banner = "Could not save - web UI is still pinned."
        return
    app._pi_network_banner = "Web UI will serve on all interfaces after the restart."
    app._web_commands.request_restart()
    # The row just removed itself; without this the same index is now the
    # next action down, and a second Enter tap would run it.
    _focus_row(app, "dhcp")


def _set_pi_network_dhcp(app: OpenFollowApp) -> None:
    """Put the selected interface on DHCP. One confirm, no form."""
    adapter = _network_adapter(app)
    iface = str(getattr(app, "_pi_network_active_iface", "") or "")
    if adapter is None or not iface:
        app._pi_network_banner = "No network adapter available."
        return
    if not adapter.is_writable():
        app._pi_network_banner = "Read-only host - cannot apply."
        return
    config = Ipv4Config(method=Ipv4Method.DHCP)
    app._pi_network_pending_config = config
    _start_worker(app, lambda: adapter.apply_ipv4(iface, config), "Apply")


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
        exit_pi_network(app)
        app._enter_settings_menu()


def handle_pi_network_key(app: OpenFollowApp, key: str) -> None:
    if key == "ArrowUp":
        _pi_network_move(app, -1)
    elif key == "ArrowDown":
        _pi_network_move(app, +1)
    elif key == "Enter":
        _pi_network_confirm(app)
    elif key == "Escape":
        exit_pi_network(app)
        app._enter_settings_menu()


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


def enter_pi_network_field_edit(app: OpenFollowApp, field: str) -> None:
    app._pi_network_field_edit_active = True
    app._pi_network_field_name = field
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
            app._pi_network_field_value = ""
    elif field == "router":
        app._pi_network_field_value = pending.router or ""
    else:
        app._pi_network_field_value = ""


def exit_pi_network_field_edit(app: OpenFollowApp) -> None:
    app._pi_network_field_edit_active = False
    app._pi_network_field_name = ""
    app._pi_network_field_value = ""
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

    if field == "address":
        canon = parse_ipv4(value) if value else None
        if value and canon is None:
            app._pi_network_banner = "Invalid IPv4 address."
            return
        new_address = canon
    elif field == "prefix":
        prefix = parse_prefix(value) if value else None
        if value and prefix is None:
            app._pi_network_banner = "Subnet prefix must be 0-32 or a mask like 255.255.255.0."
            return
        new_prefix = prefix
    elif field == "router":
        canon = parse_ipv4(value) if value else None
        if value and canon is None:
            app._pi_network_banner = "Invalid router IPv4 address."
            return
        new_router = canon

    app._pi_network_pending_config = Ipv4Config(
        method=pending.method,
        address=new_address,
        prefix=new_prefix,
        router=new_router,
        dns=pending.dns,
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
    if key == "Backspace":
        app._pi_network_field_value = app._pi_network_field_value[:-1]
        return
    # Accept numeric-keypad digits/decimal, which arrive as "Numpad5" /
    # "KP_Decimal" rather than the bare characters the top number row sends.
    key = _NUMPAD_FIELD_CHARS.get(key, key)
    if len(key) == 1 and key in _ALLOWED_FIELD_CHARS:
        app._pi_network_field_value += key


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
    _, index = _field_digit_state(app)
    app._pi_network_field_digit_index = ipv4_digit_grid.move_cursor(index, delta)


def _bump_field_digit(app: OpenFollowApp, delta: int) -> None:
    """Cycle the digit under the cursor and write the padded value back.

    The buffer keeps its padding from here on: it is what holds the cursor and
    the character it points at in fixed correspondence, and ``confirm`` strips
    it again before anything parses the value.
    """
    digits, index = _field_digit_state(app)
    app._pi_network_field_digit_index = index
    app._pi_network_field_value = ipv4_digit_grid.from_grid(ipv4_digit_grid.bump_digit(digits, index, delta))


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
    if adapter is None or pending is None or not iface:
        app._pi_network_banner = "No network adapter available."
        return
    errors = validate_apply(pending.method, pending.address, pending.prefix, pending.router, list(pending.dns))
    if errors:
        app._pi_network_banner = errors[0]
        return
    if not adapter.is_writable():
        app._pi_network_banner = "Read-only host – cannot apply."
        return
    _start_worker(app, lambda: adapter.apply_ipv4(iface, pending), "Apply")


def _renew_pi_network(app: OpenFollowApp) -> None:
    adapter = _network_adapter(app)
    iface = getattr(app, "_pi_network_active_iface", "")
    if adapter is None or not iface:
        app._pi_network_banner = "No network adapter available."
        return
    if not adapter.is_writable():
        app._pi_network_banner = "Read-only host – cannot renew."
        return
    _start_worker(app, lambda: adapter.renew_lease(iface), "Renew")


def _start_worker(
    app: OpenFollowApp,
    fn: Callable[[], ApplyResult],
    action_label: str,
) -> None:
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
        app._pi_network_banner = "Previous network action still finishing; please wait."
        return
    app._pi_network_busy = True
    # Broker may prompt for device password on non-Ansible install (web UI only).
    app._pi_network_banner = (
        f"{action_label} in progress… "
        "If a password modal appears in the web UI, enter the device "
        "password there to complete this action."
    )
    # Per-launch generation token to orphan stale workers on screen exit.
    generation = getattr(app, "_pi_network_worker_generation", 0) + 1
    app._pi_network_worker_generation = generation

    def _run() -> None:
        result: ApplyResult
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Network %s failed", action_label)
            result = ApplyResult(ok=False, message=str(exc))
        # Re-read the post-action state here (off the main thread), then hand
        # result + snapshot to the main-thread drain. The worker never writes
        # render-read state itself.
        snap = _read_pi_network(app)
        with app._pi_network_worker_lock:
            app._pi_network_pending_result = (result, action_label, generation, snap)

    thread = threading.Thread(target=_run, name=f"pi-network-{action_label.lower()}", daemon=True)
    app._pi_network_worker = thread
    thread.start()


def _finish_worker(
    app: OpenFollowApp,
    result: ApplyResult,
    action_label: str,
    generation: int,
    snap: _NetworkSnapshot,
) -> None:
    # Drop late results from orphaned workers: ``exit_pi_network`` and a new
    # ``_start_worker`` both bump the generation. Runs on the main thread (the
    # drain), so the check and writes can't race the exit/re-enter that bumps
    # it. ``busy`` is owned by whoever bumped the generation; don't touch it.
    if getattr(app, "_pi_network_worker_generation", 0) != generation:
        return
    app._pi_network_busy = False
    if result.ok:
        msg = f"{action_label} ok."
        if result.partial_failures:
            msg += " Warnings: " + "; ".join(result.partial_failures)
        app._pi_network_banner = msg
    else:
        app._pi_network_banner = f"{action_label} failed: {result.message}"
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
