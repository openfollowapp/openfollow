# Network Interface Settings

The station's IPv4 configuration.

This is where addressing is set. The station's own **Settings → Network** screen is the fallback for getting *back* here when this page is unreachable: it lists the address that reaches the web UI on each adapter, and offers a short list of fixes (DHCP, a static address, renew a lease, and serving the web UI on every interface again). Everything else – DNS, VLANs, and which network each protocol uses – is set here.

Every network adapter the station can see is listed, each showing its address, its method, and whether it currently holds one. Clicking a row opens that adapter's settings underneath it; rows stay open until you close them, and the browser remembers which were open. **Edit** inside an open row unlocks that row's fields. Only one row is editable at a time, so two adapters can't be half-edited against each other.

A **This session** marker means the address answering your browser belongs to that adapter. It is the address, not necessarily the cable: a station answers for any of its addresses on whichever adapter your request arrives over, so the marker can land on a VLAN you are not otherwise on. Either way, changing that adapter's addressing drops the page you are reading – the station's own **Settings → Network** screen then lists the address that reaches it, and the name shown on the HUD (`Web address`, e.g. `openfollow-noble-bear.local`) keeps working whatever address it ends up with, but you will need to reconnect. The marker only appears when the station can tell which address answered; with some setups it can't, and then every open editor carries a general caution instead.

**Scan** re-reads the adapter list. Use it after plugging in a USB Ethernet adapter so it appears without waiting.

**Method**:

- `DHCP (automatic)` – the router assigns everything.
- `DHCP with manual address` – DHCP provides the subnet and router; you pin the IP.
- `Static` – you enter address, subnet mask, and router yourself.

**Address fields** (always shown; editable in `Static` or `DHCP with manual address`):

- **IP address** – this station's IPv4 address; used for PSN output, peer discovery, and the web server.
- **Subnet mask** – required for `Static`; inherited from the lease in `DHCP with manual address`.
- **Router** (optional) – default gateway; leave blank on a LAN with no internet connection. Must sit inside the subnet or it's rejected.

**DNS (Server 1–3)** – resolver addresses in priority order. Only needed to reach external hostnames (e.g. software updates); leave blank on an offline LAN.

**Lease remaining** – countdown on the active DHCP lease, with **Renew DHCP lease** beside it.

> Applying to the adapter answering your browser disconnects this web session. A static or manual address reloads the UI at the new one automatically; for DHCP, reconnect manually if the session drops. Applying to any other adapter leaves the page you are reading alone.

## If DHCP is unavailable

On a show LAN with no DHCP server, an adapter set to `DHCP (automatic)` self-assigns an address in the `169.254.x.x` range within a few seconds so the station is still reachable from the same network segment. It is a fallback, not a lease: as soon as a real DHCP server appears the station takes a normal address and the `169.254` one goes away.

Two ways to reach a station in that state, both shown on the on-screen HUD:

- **By name** – browse to the address shown on the station's own screen under `Web address` (for example `openfollow-noble-bear.local`). This works whatever address the station ends up with. It is the machine's actual hostname, which is usually the station name but can differ, so read it off the screen rather than assuming. macOS resolves it natively; Windows needs Bonjour installed, and some corporate images block mDNS entirely.
- **By address** – read the address off the station's screen and type it into your browser. The HUD marks it `DHCP unavailable` so a fallback address isn't mistaken for a working lease.

Your computer needs an address on the same network to reach a `169.254` one. Most laptops self-assign one automatically when they see no DHCP server either; if yours doesn't, give the interface a static `169.254.x.x` address with a `255.255.0.0` subnet mask.

A station with a `Static` address never uses the fallback – it already has the address you gave it.

## Tagged VLANs

Many venues deliver one Ethernet run carrying several tagged 802.1Q VLANs rather than a separate cable per network. **+ Add VLAN** creates a sub-interface on top of a physical adapter for one tag, so a single cable can carry lighting, video, and management traffic on separate networks.

A VLAN sub-interface behaves like any other adapter once it exists: it appears in the list as `eth0.10`, takes its own address, and every row in **Network Interface Assignment** can point at it. That is how PSN reaches the lighting VLAN while OTP goes out on another, over one cable.

- **Parent interface** – the physical adapter carrying the tags. The switch port it plugs into must be configured as a trunk (tagged) port for that VLAN, or no traffic arrives. A VLAN cannot be stacked on another VLAN.
- **VLAN ID** – `1`–`4094`, matching the tag the switch sends. `0` and `4095` are reserved by the standard.
- The name is derived as `<parent>.<id>` and cannot be chosen. Where the parent's own name is long enough to leave no room for the tag, it is shortened to make space – the tag always survives, and the real parent is recorded on the sub-interface itself.

The parent keeps its own untagged address; adding VLANs does not take it away. Each new sub-interface starts with no address – open its row and use **Edit** to give it one. A tagged lighting VLAN frequently has no DHCP server, in which case the fallback above applies to it too.

**Delete VLAN** appears inside a VLAN row's own settings, so it can only ever remove the adapter named at the top of that form. It is refused for the adapter whose address is answering your browser – reconnect at another of the station's addresses first. Anything pinned to a deleted VLAN stops sending until it is reassigned.

VLAN creation needs NetworkManager. On a station using another network backend the controls are not shown.

An open row reads out its settings with its fields locked, so nothing changes by mistake. On a station whose network backend is read-only there is no **Edit** at all, and the card carries a **Read only** badge instead – the station's own screen says the same. See openfollow.app for troubleshooting and how to enable web editing.

**Buttons:**

- **Edit** – unlocks that row's fields. Absent when the backend is read-only.
- **Apply** – validates and commits the row. Invalid input is rejected and nothing is written.
- **Renew DHCP lease** – requests a fresh lease; sits on the lease line, DHCP methods only.
- **+ Add VLAN** – creates a tagged sub-interface (NetworkManager only).
- **Delete VLAN** – removes the sub-interface whose settings are open.
- **Cancel** – discards unsaved edits and locks the fields again. The row stays open.
