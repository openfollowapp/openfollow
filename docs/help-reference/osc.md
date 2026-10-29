# OSC Input

OpenFollow's built-in OSC receiver lets external systems – lighting consoles, show-control software, media servers – drive marker positions over the network. When enabled, the station listens for incoming OSC messages and updates the named markers in real time.

## OSC Server

- **OSC server** – master toggle for the OSC receiver. When off, the station ignores all incoming OSC traffic and the port is released.
- **UDP Port** – the UDP port the station listens on, 1–65535. Default: `8765`. Every sender must target this port. After changing it, click **Save**; the receiver restarts and listens on the new port immediately.
- **Multicast group** – an IPv4 multicast address (`224.0.0.0`–`239.255.255.255`) to join, so a single sender reaches every station at once – the same model PSN uses. Defaults to `239.20.20.20`; clear it to disable. Unicast (sending straight to a station's IP) and subnet broadcast work regardless of this setting. Which interface takes the subscription is set under **Network Interface Assignment**, in the OSC input row; in Simple network mode it is the station interface.
- **Allowed sender IPs** – a comma-separated list of the IPv4 addresses permitted to inject marker positions (e.g. `192.0.2.10, 192.0.2.20`). Leave blank to accept any host. Individual IPv4 addresses only – CIDR ranges and IPv6 addresses are not supported.

> If the **OSC server** is on and the allowlist is empty, any device on the LAN can update marker positions. Add at least one IP if your show network is shared with other equipment.

## OSC address format

The receiver accepts two message shapes:

- **Full triple** – `/marker/{id}` with three float arguments: `x`, `y`, `z`. All three axes update in one message.
- **Per-axis** – `/marker/{id}/x`, `/marker/{id}/y`, `/marker/{id}/z`, each carrying a single float. Only the addressed axis updates; the others keep their values, allowing partial updates from systems that work axis-by-axis.

In both shapes `{id}` is the integer marker ID (1 or higher). Coordinates follow OpenFollow's stage convention: X is stage left (positive) / stage right (negative); Y is upstage (positive) / downstage (negative); Z is height above the stage floor. All values are in **metres**, relative to the **Reference Point**.

Two more addresses park and unpark a marker this station controls: `/marker/{id}/park` and `/marker/{id}/unpark`. Their arguments are ignored: a console button that sends `/marker/{id}/park` with 1 on press and 0 on release leaves the marker parked. A parked marker still follows the position messages above; only the keyboard, gamepads and 3D mice leave it alone. Messages for a marker this station does not control are ignored.

## Saving

- **Save** – writes the current settings and restarts the OSC receiver if the port or enabled state changed. Changes take effect immediately after saving.
