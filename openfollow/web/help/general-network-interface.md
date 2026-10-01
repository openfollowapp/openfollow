# Network Interface Settings

The station's network adapters and their IPv4 settings. When this page is unreachable, the station's own **Settings → Network** screen lists the addresses that reach it.

## The list

One row per adapter:

- **Dot** – green: up with an address. Grey: no address.
- **Address** – with its prefix, or `(no address)`.
- **Method tag** – `DHCP`, `DHCP + manual` or `Static`.
- **VLAN n** – a tagged VLAN sub-interface and its ID.
- **This session** – the address answering your browser belongs to this adapter. Changing its addressing drops this page.

Open a row to see its settings. **Scan** re-reads the adapter list, for example after a USB adapter is plugged in.

## Settings

- **Method** – `DHCP (automatic)`: the network assigns everything. `DHCP with manual address`: DHCP provides the subnet and router, the address is fixed. `Static`: address, subnet mask and router are entered here.
- **IP address**, **Subnet mask** (required for `Static`), **Router** (optional; must sit inside the subnet).
- **DNS (Server 1–3)** – only needed to reach outside hostnames; blank on an offline LAN.
- **Lease remaining** – time left on the DHCP lease.

An adapter on DHCP with no DHCP server on its network gives itself a `169.254.x.x` address within a few seconds, and the HUD marks it `DHCP unavailable`. It takes a normal address as soon as a server answers.

## VLANs

**+ Add VLAN** creates a sub-interface for one 802.1Q tag: **Parent interface** (never another VLAN) and **VLAN ID** (`1`–`4094`). It is named `<parent>.<id>`, with the parent part shortened when the name would be too long, and starts without an address. VLANs need NetworkManager; on another network backend the controls are not shown.

## Buttons

- **Edit** – unlocks one row's fields. Absent on a read-only station, which shows a **Read only** badge.
- **Apply** – checks and saves the row. Without a cable, or with no DHCP server answering yet, the settings are saved and the result says so.
- **Renew DHCP lease** – asks for a fresh lease.
- **Remove VLAN** – removes the station's interface for that VLAN; anything pinned to it stops sending. Refused for the interface answering your browser.
- **Cancel** – discards unsaved edits.
