# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Pi network settings screen – interface selection and IPv4 configuration.

On-device sub-screens (method/iface pickers, field editor) plus the apply/renew
worker that drives the privileged NetworkAdapter off the main thread."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from openfollow.configuration import save_config
from openfollow.network.adapter import (
    ApplyResult,
    Ipv4Config,
    Ipv4Method,
    NetworkAdapter,
    NetworkState,
    is_loopback,
)
from openfollow.network.validate import (
    parse_ipv4,
    parse_prefix,
    prefix_to_mask,
    validate_apply,
)

if TYPE_CHECKING:
    from openfollow.app import OpenFollowApp

logger = logging.getLogger(__name__)

_METHOD_LABELS: dict[Ipv4Method, str] = {
    Ipv4Method.DHCP: "DHCP",
    Ipv4Method.DHCP_WITH_MANUAL_ADDRESS: "DHCP with manual address",
    Ipv4Method.STATIC: "Static",
}


def _network_adapter(app: OpenFollowApp) -> NetworkAdapter | None:
    services = getattr(app, "_runtime_services", None)
    if services is None:
        return None
    return getattr(services, "network_adapter", None)


def enter_pi_network(app: OpenFollowApp) -> None:
    app._pi_network_active = True
    app._pi_network_banner = ""
    app._pi_network_busy = False
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


def build_pi_network_rows(app: OpenFollowApp) -> list[dict[str, object]]:
    """Build the sectioned row list for the Pi Network screen.

    Rows carry a ``kind`` discriminator:

    - ``"header"``  – section heading, not selectable
    - ``"display"`` – read-only value, not selectable
    - ``"choice"``  – selectable; Enter opens a picker (interface, method)
    - ``"text"``    – selectable; Enter opens the field editor
    - ``"action"``  – selectable; Enter runs the action (apply / renew / back)

    Method-specific fields are *hidden*, not greyed, to match the Apple
    Network pane reference: DHCP shows lease-derived values as display
    rows; Static promotes them to editable text rows; DHCP+manual shows
    the address as editable, the rest as display.
    """
    adapter = _network_adapter(app)
    writable = bool(adapter and adapter.is_writable())
    state: NetworkState | None = getattr(app, "_pi_network_state_cache", None)
    pending: Ipv4Config | None = getattr(app, "_pi_network_pending_config", None)
    method = pending.method if pending else Ipv4Method.DHCP
    interfaces = getattr(app, "_pi_network_interfaces", [])
    busy = bool(getattr(app, "_pi_network_busy", False))

    def _addr(field: str) -> str:
        if pending is None:
            return "–"
        value = getattr(pending, field, None)
        return str(value) if value else "–"

    def _prefix_value() -> str:
        # Render as the dotted subnet mask (``255.255.255.0``) rather
        # than ``/24`` – more operators recognise the mask form from
        # router consoles than the CIDR prefix length.
        if pending and pending.prefix is not None:
            mask = prefix_to_mask(pending.prefix)
            if mask is not None:
                return mask
        return "–"

    def _dns_at(i: int) -> str:
        if pending is None or not pending.dns or i >= len(pending.dns):
            return "–"
        return pending.dns[i]

    rows: list[dict[str, object]] = []

    # ---- Interface section --------------------------------------------
    rows.append({"kind": "header", "label": "Interface"})
    rows.append(
        {
            "kind": "choice" if writable and len(interfaces) > 1 else "display",
            "key": "interface",
            "label": "Selected",
            "value": app._pi_network_active_iface or "–",
        }
    )

    # ---- IPv4 section -------------------------------------------------
    rows.append({"kind": "header", "label": "IPv4"})
    rows.append(
        {
            "kind": "choice" if writable else "display",
            "key": "method",
            "label": "Configure",
            "value": _METHOD_LABELS.get(method, str(method)),
        }
    )
    # Method-specific fields. Hidden when not relevant to the chosen method.
    if method == Ipv4Method.STATIC and writable:
        rows.append({"kind": "text", "key": "address", "label": "IP Address", "value": _addr("address")})
        rows.append({"kind": "text", "key": "prefix", "label": "Subnet", "value": _prefix_value()})
        rows.append({"kind": "text", "key": "router", "label": "Router (optional)", "value": _addr("router")})
    elif method == Ipv4Method.DHCP_WITH_MANUAL_ADDRESS and writable:
        rows.append({"kind": "text", "key": "address", "label": "IP Address", "value": _addr("address")})
        rows.append({"kind": "display", "key": "prefix", "label": "Subnet", "value": _prefix_value()})
        rows.append({"kind": "display", "key": "router", "label": "Router (from lease)", "value": _addr("router")})
    else:
        # DHCP (or read-only host): show all three as display.
        rows.append({"kind": "display", "key": "address", "label": "IP Address", "value": _addr("address")})
        rows.append({"kind": "display", "key": "prefix", "label": "Subnet", "value": _prefix_value()})
        rows.append({"kind": "display", "key": "router", "label": "Router", "value": _addr("router")})

    # Lease info – only meaningful for DHCP-driven methods.
    if (
        method in (Ipv4Method.DHCP, Ipv4Method.DHCP_WITH_MANUAL_ADDRESS)
        and state is not None
        and state.lease
        and state.lease.lease_seconds_remaining is not None
    ):
        minutes = max(int(state.lease.lease_seconds_remaining) // 60, 0)
        rows.append({"kind": "display", "key": "lease_remaining", "label": "Lease", "value": f"{minutes} min"})

    # ---- DNS section --------------------------------------------------
    rows.append({"kind": "header", "label": "DNS Servers"})
    dns_kind = "text" if writable else "display"
    rows.append({"kind": dns_kind, "key": "dns_1", "label": "DNS 1", "value": _dns_at(0)})
    rows.append({"kind": dns_kind, "key": "dns_2", "label": "DNS 2", "value": _dns_at(1)})
    rows.append({"kind": dns_kind, "key": "dns_3", "label": "DNS 3", "value": _dns_at(2)})

    # ---- Actions section ----------------------------------------------
    rows.append({"kind": "header", "label": "Actions"})
    if writable:
        rows.append(
            {
                "kind": "action",
                "key": "apply",
                "label": "Apply Changes" if not busy else "Working…",
                "value": "",
            }
        )
        if method in (Ipv4Method.DHCP, Ipv4Method.DHCP_WITH_MANUAL_ADDRESS):
            rows.append(
                {
                    "kind": "action",
                    "key": "renew",
                    "label": "Renew Lease" if not busy else "Working…",
                    "value": "",
                }
            )
    rows.append({"kind": "action", "key": "back", "label": "Back", "value": ""})
    return rows


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
    key = row.get("key")
    # While apply/renew worker is in flight, ignore everything except Back.
    if getattr(app, "_pi_network_busy", False) and key != "back":
        return
    if key == "back":
        exit_pi_network(app)
        app._enter_settings_menu()
    elif key == "interface":
        enter_pi_network_iface_picker(app)
    elif key == "method":
        enter_pi_network_method_picker(app)
    elif key in ("address", "prefix", "router", "dns_1", "dns_2", "dns_3"):
        enter_pi_network_field_edit(app, str(key))
    elif key == "apply":
        _apply_pi_network(app)
    elif key == "renew":
        _renew_pi_network(app)


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
# Interface picker (sub-state of Pi Network)
# ---------------------------------------------------------------------------


def enter_pi_network_iface_picker(app: OpenFollowApp) -> None:
    app._pi_network_iface_picker_active = True
    names = [i.name for i in getattr(app, "_pi_network_interfaces", [])]
    current = getattr(app, "_pi_network_active_iface", "")
    app._pi_network_iface_picker_index = names.index(current) if current in names else 0


def exit_pi_network_iface_picker(app: OpenFollowApp) -> None:
    app._pi_network_iface_picker_active = False


def _pi_network_iface_picker_move(app: OpenFollowApp, step: int) -> None:
    names = [i.name for i in getattr(app, "_pi_network_interfaces", [])]
    if not names:
        return
    app._pi_network_iface_picker_index = (app._pi_network_iface_picker_index + step) % len(names)


def _pi_network_iface_picker_confirm(app: OpenFollowApp) -> None:
    names = [i.name for i in getattr(app, "_pi_network_interfaces", [])]
    if not names:
        exit_pi_network_iface_picker(app)
        return
    idx = app._pi_network_iface_picker_index
    if 0 <= idx < len(names):
        app._pi_network_active_iface = names[idx]
        _refresh_pi_network_bounded(app)
        _apply_as_bind_iface(app, names[idx])
    exit_pi_network_iface_picker(app)


def _apply_as_bind_iface(app: OpenFollowApp, iface: str) -> None:
    """Bind PSN/mDNS/web sockets to the selected NIC and hot-reload the resolver."""
    state = getattr(app, "_pi_network_state_cache", None)
    if state is None or state.ipv4.address is None:
        return
    # Defense-in-depth: ``_refresh_pi_network`` already filters loopback
    # out of the picker, but bail here too in case a caller bypasses
    # the picker. Rebinding PSN/mDNS/web to 127.0.0.1 would silently
    # take the device off the show network.
    if is_loopback(state.interface) or state.ipv4.address.startswith("127."):
        return
    new_ip = state.ipv4.address
    if not new_ip:
        return
    if iface == getattr(app._config, "psn_source_iface", ""):
        return
    old_iface = app._config.psn_source_iface
    app._config.psn_source_iface = iface
    try:
        app._runtime_services.apply_psn_source_ip_change(new_ip)
    except Exception as exc:  # noqa: BLE001
        app._config.psn_source_iface = old_iface
        # Restore the advisory to the prior iface's state.
        app._refresh_psn_source_advisory()
        logger.warning(
            "Failed to rebind OpenFollow listeners to %s (%s): %s – keeping iface=%r.",
            iface,
            new_ip,
            exc,
            old_iface,
        )
        return
    try:
        save_config(app._config, app._config_path)
        app._config_mtime = app._get_config_mtime()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to persist bind iface change: %s", exc)
        app._pi_network_banner = "Applied live but failed to save – will revert on restart."
    # Clear/refresh the stale-iface advisory now the pin is honoured.
    app._refresh_psn_source_advisory()
    logger.info(
        "Network screen rebound OpenFollow listeners to %s (%s, live).",
        iface,
        new_ip,
    )


def process_pi_network_iface_picker_input(app: OpenFollowApp) -> None:
    input_manager = app._input_manager
    if input_manager is None:
        return
    try:
        inp = input_manager.gamepad_handler.read_settings_menu_input()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pi network iface picker input error: %s", exc)
        return
    if inp.up_pressed:
        _pi_network_iface_picker_move(app, -1)
    if inp.down_pressed:
        _pi_network_iface_picker_move(app, +1)
    if inp.confirm_pressed:
        _pi_network_iface_picker_confirm(app)
    elif inp.cancel_pressed:
        exit_pi_network_iface_picker(app)


def handle_pi_network_iface_picker_key(app: OpenFollowApp, key: str) -> None:
    if key == "ArrowUp":
        _pi_network_iface_picker_move(app, -1)
    elif key == "ArrowDown":
        _pi_network_iface_picker_move(app, +1)
    elif key == "Enter":
        _pi_network_iface_picker_confirm(app)
    elif key == "Escape":
        exit_pi_network_iface_picker(app)


# ---------------------------------------------------------------------------
# Method picker (sub-state of Pi Network)
# ---------------------------------------------------------------------------


_METHOD_PICKER_ITEMS: tuple[tuple[Ipv4Method, str], ...] = (
    (Ipv4Method.DHCP, "DHCP"),
    (Ipv4Method.DHCP_WITH_MANUAL_ADDRESS, "DHCP with manual address"),
    (Ipv4Method.STATIC, "Static"),
)


def enter_pi_network_method_picker(app: OpenFollowApp) -> None:
    app._pi_network_method_picker_active = True
    pending = getattr(app, "_pi_network_pending_config", None)
    current_method = pending.method if pending else Ipv4Method.DHCP
    for i, (method, _) in enumerate(_METHOD_PICKER_ITEMS):
        if method == current_method:
            app._pi_network_method_picker_index = i
            return
    app._pi_network_method_picker_index = 0


def exit_pi_network_method_picker(app: OpenFollowApp) -> None:
    app._pi_network_method_picker_active = False


def method_picker_items() -> tuple[tuple[Ipv4Method, str], ...]:
    return _METHOD_PICKER_ITEMS


def _pi_network_method_picker_move(app: OpenFollowApp, step: int) -> None:
    total = len(_METHOD_PICKER_ITEMS)
    app._pi_network_method_picker_index = (app._pi_network_method_picker_index + step) % total


def _pi_network_method_picker_confirm(app: OpenFollowApp) -> None:
    idx = app._pi_network_method_picker_index
    if not 0 <= idx < len(_METHOD_PICKER_ITEMS):
        exit_pi_network_method_picker(app)
        return
    method, _ = _METHOD_PICKER_ITEMS[idx]
    pending = getattr(app, "_pi_network_pending_config", None) or Ipv4Config(method=method)
    # Clear method-irrelevant fields when changing methods; stale prefix/router
    # from a prior STATIC config would silently override active lease values. DNS is
    # always editable across all three methods, so it carries over.
    if method == Ipv4Method.DHCP:
        # Pure DHCP – operator only owns the DNS override (the
        # lease drives address / prefix / router).
        next_address: str | None = None
        next_prefix: int | None = None
        next_router: str | None = None
    elif method == Ipv4Method.DHCP_WITH_MANUAL_ADDRESS:
        # Operator owns the address; prefix + router come from the
        # lease. Preserve a previously-typed manual address but drop
        # any static prefix/router so the lease wins on the apply path.
        next_address = pending.address
        next_prefix = None
        next_router = None
    else:
        # STATIC – operator owns every field; carry them all over so
        # a previously-typed value isn't lost on a Static→Static
        # method-picker re-confirm.
        next_address = pending.address
        next_prefix = pending.prefix
        next_router = pending.router
    app._pi_network_pending_config = Ipv4Config(
        method=method,
        address=next_address,
        prefix=next_prefix,
        router=next_router,
        dns=pending.dns,
    )
    exit_pi_network_method_picker(app)


def process_pi_network_method_picker_input(app: OpenFollowApp) -> None:
    input_manager = app._input_manager
    if input_manager is None:
        return
    try:
        inp = input_manager.gamepad_handler.read_settings_menu_input()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pi network method picker input error: %s", exc)
        return
    if inp.up_pressed:
        _pi_network_method_picker_move(app, -1)
    if inp.down_pressed:
        _pi_network_method_picker_move(app, +1)
    if inp.confirm_pressed:
        _pi_network_method_picker_confirm(app)
    elif inp.cancel_pressed:
        exit_pi_network_method_picker(app)


def handle_pi_network_method_picker_key(app: OpenFollowApp, key: str) -> None:
    if key == "ArrowUp":
        _pi_network_method_picker_move(app, -1)
    elif key == "ArrowDown":
        _pi_network_method_picker_move(app, +1)
    elif key == "Enter":
        _pi_network_method_picker_confirm(app)
    elif key == "Escape":
        exit_pi_network_method_picker(app)


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
    elif field in ("dns_1", "dns_2", "dns_3"):
        idx = int(field.split("_")[1]) - 1
        if pending.dns and idx < len(pending.dns):
            app._pi_network_field_value = pending.dns[idx]
        else:
            app._pi_network_field_value = ""
    else:
        app._pi_network_field_value = ""


def exit_pi_network_field_edit(app: OpenFollowApp) -> None:
    app._pi_network_field_edit_active = False
    app._pi_network_field_name = ""
    app._pi_network_field_value = ""


def confirm_pi_network_field_edit(app: OpenFollowApp) -> None:
    field = getattr(app, "_pi_network_field_name", "")
    value = getattr(app, "_pi_network_field_value", "").strip()
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
    elif field in ("dns_1", "dns_2", "dns_3"):
        idx = int(field.split("_")[1]) - 1
        canon = parse_ipv4(value) if value else None
        if value and canon is None:
            app._pi_network_banner = "Invalid DNS server address."
            return
        while len(new_dns) <= idx:
            new_dns.append("")
        new_dns[idx] = canon or ""
        # Keep positional: blank a middle slot in place, only trim trailing blanks.
        while new_dns and not new_dns[-1]:
            new_dns.pop()

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
    if key == "Backspace":
        app._pi_network_field_value = app._pi_network_field_value[:-1]
        return
    # Accept numeric-keypad digits/decimal, which arrive as "Numpad5" /
    # "KP_Decimal" rather than the bare characters the top number row sends.
    key = _NUMPAD_FIELD_CHARS.get(key, key)
    if len(key) == 1 and key in _ALLOWED_FIELD_CHARS:
        app._pi_network_field_value += key


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
    elif inp.confirm_pressed:
        # Confirm with whatever's in the buffer – the validator will
        # reject and keep the editor open if the value is invalid.
        confirm_pi_network_field_edit(app)


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
    # Compact in-place-cleared DNS slots so blanks don't reach nmcli/dhcpcd.
    pending = replace(pending, dns=tuple(d for d in pending.dns if d))
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
