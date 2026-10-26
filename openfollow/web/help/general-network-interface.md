# Network Interface Settings

The station's network adapters and their IPv4 settings. When this page is unreachable, the station's own **Settings → Network** screen lists the addresses that reach it.

## Network mode

- **Simple** – one interface carries everything: PSN, discovery, marker sync, OTP, RTTrPM, OSC in and out, and network video. The web UI answers on every interface, unless `web_bind` in `config.toml` fixes it to one address. A station on one show network needs nothing more.
- **Advanced** – every function can be pinned to its own interface under **Network Interface Assignment**, and VLANs can be added.

The mode belongs to this station: a config export leaves it out, and an import or **Restore defaults** keeps it. It can be changed here only, not on the station's screen. A station that has never chosen starts in Simple, or in Advanced when its config already pins a function or the web UI to an interface.

**Switching to Simple** asks first when it has something to undo, and lists it:

- every function's interface pin and the web UI's are cleared, so everything follows the station interface. With a web UI pin in force OpenFollow restarts; when it cannot (an update is running), the card says the web UI answers on one interface until it does, with **Restart OpenFollow**;
- every VLAN interface is removed except the station interface's own and the one carrying this browser session, and its label goes with it. A VLAN that cannot be removed does not stop the switch: it keeps its label, and a caution under the mode names what is left, to remove in Advanced mode.

**Switching to Advanced** changes nothing on the network: every pin is blank, so everything still follows the station interface.

## Simple

- **Interface** – the interface that carries every function, by its label when it has one. **Automatic** (the default) uses the one the station picks, named in brackets, such as `Automatic (eth0)`. Choosing another first asks whether to move all traffic to it, naming the interface it leaves and, while the web UI answers on every interface, that it still does; **Cancel** keeps the interface in force.

Below it the card shows that one interface, with the **Adapter**, **Settings** and **Buttons** described further down. A camera reached only through another interface is refused, and the message names Advanced mode, where the video input can have its own pin.

## The list

In Advanced:

One row per adapter:

- **Name** – your label for the adapter when it has one, with the interface name and which adapter it is underneath: `USB 2, port 2 · ASIX AX88179B`, `Built-in Ethernet`, `VLAN 13 on Production (eth0)`. Without a label the interface name leads.
- **Dot** – green: up with an address. Grey: no address.
- **Address** – with its prefix, or `(no address)`.
- **Method tag** – `DHCP`, `DHCP + manual` or `Static`.
- **VLAN n** – a tagged VLAN sub-interface and its ID.
- **This session** – the address answering your browser belongs to this adapter. Changing its addressing drops this page.

An adapter that is not plugged in stays in the list as **Not connected** while the station keeps settings for it, labelled or not, with the address it comes up with. A VLAN on it is listed under it. A labelled adapter without saved settings stays too, so its label stays taken. These are listed in Advanced only; the station's own screen lists them too.

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

In Advanced, **+ Add VLAN** creates a sub-interface for one 802.1Q tag: **Parent interface** (never another VLAN) and **VLAN ID** (`1`–`4094`). It is named `<parent>.<id>`, with the parent part shortened when the name would be too long, and starts without an address. VLANs need NetworkManager; on another network backend the controls are not shown.

## Buttons

- **Edit** – unlocks one row's fields. Absent on a read-only station, which shows a **Read only** badge.
- **Apply** – checks and saves the row. Without a cable, with no DHCP server answering yet, or on an adapter that is not plugged in, the settings are saved and the result says so; they take effect when it comes up.
- **Renew DHCP lease** – asks for a fresh lease.
- **Remove VLAN** – removes the station's interface for that VLAN; anything pinned to it stops sending. Refused for the interface answering your browser.
- **Forget** – on an adapter that is not plugged in: deletes its saved settings, those of every VLAN on it, and their labels, after asking. Plugged in again, it gets the settings a new adapter gets. On a labelled adapter without saved settings it drops only the label, for example to give it to a replacement adapter.
- **Cancel** – discards unsaved edits.
