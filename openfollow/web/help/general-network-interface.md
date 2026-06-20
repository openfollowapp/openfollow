# Network Settings

The station's IPv4 configuration – the same settings the on-device **Settings → Network** screen writes.

**Interface** – the adapter to configure (often just `eth0`). Switching it reloads the form for that adapter.

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

**Modes:** the form opens in **View mode** – fields are locked so settings can't change by mistake. Use **Switch to edit view** to unlock them; **Edit mode** then shows Apply / Renew / Cancel. On a station whose network backend is read-only, the form shows a **Read only** badge instead – configure from the on-screen **Settings → Network** menu, or see openfollow.app for troubleshooting and how to enable web editing.

**Buttons:**

- **Switch to edit view** – unlocks the fields (enters Edit mode). Absent when the backend is read-only.
- **Apply** – validates and commits the form. Invalid input is rejected and nothing is written.
- **Renew DHCP lease** – requests a fresh lease (DHCP methods only).
- **Cancel** – discards unsaved edits and returns to View mode.
