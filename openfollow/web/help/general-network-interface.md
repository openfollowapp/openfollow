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

On a show LAN with no DHCP server, an adapter set to `DHCP (automatic)` waits about 20 seconds and then self-assigns an address in the `169.254.x.x` range so the station is still reachable from the same network segment. It is a fallback, not a lease: as soon as a real DHCP server appears the station takes a normal address and the `169.254` one goes away.

Two ways to reach a station in that state, both shown on the on-screen HUD:

- **By name** – browse to `<station-name>.local`. This works whatever address the station ends up with. macOS resolves it natively; Windows needs Bonjour installed, and some corporate images block mDNS entirely.
- **By address** – read the address off the station's screen and type it into your browser. The HUD marks it `DHCP unavailable` so a fallback address isn't mistaken for a working lease.

Your computer needs an address on the same network to reach a `169.254` one. Most laptops self-assign one automatically when they see no DHCP server either; if yours doesn't, give the interface a static `169.254.x.x` address with a `255.255.0.0` subnet mask.

A station with a `Static` address never uses the fallback – it already has the address you gave it.

**Modes:** the form opens in **View mode** – fields are locked so settings can't change by mistake. Use **Switch to edit view** to unlock them; **Edit mode** then shows Apply / Renew / Cancel. On a station whose network backend is read-only, the form shows a **Read only** badge instead – configure from the on-screen **Settings → Network** menu, or see openfollow.app for troubleshooting and how to enable web editing.

**Buttons:**

- **Switch to edit view** – unlocks the fields (enters Edit mode). Absent when the backend is read-only.
- **Apply** – validates and commits the form. Invalid input is rejected and nothing is written.
- **Renew DHCP lease** – requests a fresh lease (DHCP methods only).
- **Cancel** – discards unsaved edits and returns to View mode.
