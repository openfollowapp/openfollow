# Network Interface Settings

The station's network adapters and their IPv4 settings. When this page is unreachable, the station's own **Settings → Network** screen lists the addresses that reach it.

## The list

One row per adapter:

- **Name** – your label for the adapter when it has one, with the interface name and which adapter it is underneath: `USB 2, port 2 · ASIX AX88179B`, `Built-in Ethernet`, `VLAN 13 on Production (eth0)`. Without a label the interface name leads.
- **Dot** – green: up with an address. Grey: no address.
- **Address** – with its prefix, or `(no address)`.
- **Method tag** – `DHCP`, `DHCP + manual` or `Static`.
- **VLAN n** – a tagged VLAN sub-interface and its ID.
- **This session** – the address answering your browser belongs to this adapter. Changing its addressing drops this page.

A labelled adapter that is not plugged in stays in the list as **Not connected**, so its label stays taken. **Forget** drops the label, for example to give it to a replacement adapter.

Open a row to see its settings. **Scan** re-reads the adapter list, for example after a USB adapter is plugged in.

## Adapter

- **Label** – your name for this adapter, up to 20 characters, different from every other label. It is shown wherever the interface is: every interface picker, the protocol sections, the station's own screen and its warnings, and the diagnostics bundle. Its **Save** stores only the label and changes nothing on the network, so it works on a read-only station too. An empty label removes it.
- **Port** – which USB socket the adapter sits in, numbered like the Controller Slots table, or `Built-in Ethernet` / `Built-in Wi-Fi` for the station's own.
- **Model** – what the USB adapter reports itself as.
- **MAC address** – the adapter's hardware address, usually printed on it.
- **VLAN** – for a VLAN, its ID and the adapter it runs on.

Labels belong to this station: a config export leaves them out, an import keeps this station's, and **Restore defaults** clears them.

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
