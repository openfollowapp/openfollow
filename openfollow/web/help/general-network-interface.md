# Network Interface Settings

The station's network adapters and their IPv4 settings. If this page is unreachable, the station's **Settings → Network** screen lists its addresses.

## Network mode

- **Simple** – one interface carries everything: PSN, discovery, marker sync, OTP, RTTrPM, OSC and network video.
- **Advanced** – each function can be pinned to its own interface under **Network Interface Assignment**, and VLANs can be added.

**Switching to Simple** asks first: every interface pin is cleared and every VLAN is removed except the station interface's own and the one carrying this browser session. **Switching to Advanced** changes nothing.

In Simple, **Interface** picks the one that carries every function; **Automatic** (default) lets the station pick. A camera reached only through another interface is refused.

## The list

One row per adapter:

- **Name** – your label, with the interface name and adapter underneath.
- **Dot** – green: up with an address. Grey: none, or not plugged in.
- **Address** – with its prefix, or `(no address)`.
- **Method tag** – `DHCP`, `DHCP + manual` or `Static`; **VLAN n** marks a VLAN and its ID.
- **This session** – this adapter answers your browser; changing its addressing drops this page.

An unplugged adapter with saved settings stays listed as **Not connected**. Open a row to see its settings. **Scan** re-reads the adapter list.

## Adapter

- **Label** – your name for the adapter, up to 20 characters, unique. Shown in every interface picker. Its **Save** stores only the label. Empty removes it.
- **Port** – the USB socket, or `Built-in Ethernet` / `Wi-Fi`.
- **Model**, **MAC address**, **VLAN** (ID and parent) – as reported.

## Settings

- **Method** – `DHCP (automatic)`: the network assigns everything. `DHCP with manual address`: DHCP gives subnet and router, the address is fixed. `Static`: you enter address, subnet mask and router.
- **IP address**, **Subnet mask** (required for `Static`), **Router** (optional, inside the subnet).
- **DNS (Server 1–3)** – only for outside hostnames; blank on an offline LAN.
- **Lease remaining** – time left on the lease.

On DHCP with no server answering, an adapter takes a `169.254.x.x` address within a few seconds and the HUD shows `DHCP unavailable`.

## VLANs

In Advanced, **+ Add VLAN** creates a sub-interface for one 802.1Q tag: **Parent interface** and **VLAN ID** (`1`–`4094`). It starts without an address.

## Buttons

- **Edit** – unlocks a row. Absent on a read-only station.
- **Apply** – checks and saves the row. For an unplugged adapter, it takes effect when it comes up. `DHCP with manual address` needs the adapter plugged in.
- **Renew DHCP lease** – asks for a new lease.
- **Remove VLAN** – anything pinned to it stops sending. Refused for the interface answering your browser.
- **Forget** – on an unplugged adapter: deletes its settings, VLANs and labels, after asking.
- **Cancel** – discards edits.
