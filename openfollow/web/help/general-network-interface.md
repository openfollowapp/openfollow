# Network Settings

The station's IPv4 configuration – the same settings the on-device **Settings → Network** screen writes.

**Interfaces on this station** – every network adapter the station can see, with its address, subnet method, and whether it currently holds an address. **Configure** opens that adapter's settings underneath its own row; opening a different row moves the form there.

A **This session** marker means your browser reached the station over that adapter. Changing its address will drop the page you are reading – the station stays reachable by the name shown on its own screen (`Web address`, e.g. `openfollow-noble-bear.local`) and from the on-screen **Settings → Network** menu, but you will need to reconnect. The marker only appears when the station can tell which adapter you arrived on; with some setups it can't, so its absence is not a guarantee.

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

**Lease remaining** – countdown on the active DHCP lease (read-only, refreshes every 5 s).

> Applying may disconnect this web session. A static/manual address reloads the UI at the new address automatically; for DHCP, reconnect manually if the session drops.

## If DHCP is unavailable

On a show LAN with no DHCP server, an adapter set to `DHCP (automatic)` self-assigns an address in the `169.254.x.x` range within a few seconds so the station is still reachable from the same network segment. It is a fallback, not a lease: as soon as a real DHCP server appears the station takes a normal address and the `169.254` one goes away.

Two ways to reach a station in that state, both shown on the on-screen HUD:

- **By name** – browse to the address shown on the station's own screen under `Web address` (for example `openfollow-noble-bear.local`). This works whatever address the station ends up with. It is the machine's actual hostname, which is usually the station name but can differ, so read it off the screen rather than assuming. macOS resolves it natively; Windows needs Bonjour installed, and some corporate images block mDNS entirely.
- **By address** – read the address off the station's screen and type it into your browser. The HUD marks it `DHCP unavailable` so a fallback address isn't mistaken for a working lease.

Your computer needs an address on the same network to reach a `169.254` one. Most laptops self-assign one automatically when they see no DHCP server either; if yours doesn't, give the interface a static `169.254.x.x` address with a `255.255.0.0` subnet mask.

A station with a `Static` address never uses the fallback – it already has the address you gave it.

## Tagged VLANs

Many venues deliver one Ethernet run carrying several tagged 802.1Q VLANs rather than a separate cable per network. **+ Add VLAN** creates a sub-interface on top of a physical adapter for one tag, so a single cable can carry lighting, video, and management traffic on separate networks.

A VLAN sub-interface behaves like any other adapter once it exists: it appears in the list as `eth0.10`, takes its own address, and every row in **Interface Assignment** can point at it. That is how PSN reaches the lighting VLAN while OTP goes out on another, over one cable.

- **Parent interface** – the physical adapter carrying the tags. The switch port it plugs into must be configured as a trunk (tagged) port for that VLAN, or no traffic arrives. A VLAN cannot be stacked on another VLAN.
- **VLAN ID** – `1`–`4094`, matching the tag the switch sends. `0` and `4095` are reserved by the standard.
- The name is derived as `<parent>.<id>` and cannot be chosen.

The parent keeps its own untagged address; adding VLANs does not take it away. Each new sub-interface starts with no address – use **Configure** on its row to give it one. A tagged lighting VLAN frequently has no DHCP server, in which case the fallback above applies to it too.

**Delete VLAN** appears inside a VLAN row's own settings, so it can only ever remove the adapter named at the top of that form. It is refused for the adapter your browser arrived on – reconnect over another network first. Anything pinned to a deleted VLAN stops sending until it is reassigned.

VLAN creation needs NetworkManager. On a station using another network backend the controls are not shown.

**Modes:** the form opens in **View mode** – fields are locked so settings can't change by mistake. Use **Switch to edit view** to unlock them; **Edit mode** then shows Apply / Renew / Cancel. On a station whose network backend is read-only, the form shows a **Read only** badge instead – configure from the on-screen **Settings → Network** menu, or see openfollow.app for troubleshooting and how to enable web editing.

**Buttons:**

- **Switch to edit view** – unlocks the fields (enters Edit mode). Absent when the backend is read-only.
- **Apply** – validates and commits the form. Invalid input is rejected and nothing is written.
- **Renew DHCP lease** – requests a fresh lease (DHCP methods only).
- **+ Add VLAN** – creates a tagged sub-interface (Edit mode, NetworkManager only).
- **Delete VLAN** – removes the sub-interface whose settings are open.
- **Cancel** – discards unsaved edits and returns to View mode.
