# OSC Input

OpenFollow's built-in OSC receiver lets external systems – lighting consoles, show-control software, media servers – drive marker positions over the network. When enabled, the station listens for incoming OSC messages and updates the named markers in real time.

## OSC Server

- **OSC server** – master toggle for the OSC receiver. When off, the station ignores all incoming OSC traffic and the port is released.
- **UDP Port** – the UDP port the station listens on, 1–65535. Default: `8765`. Every sender must target this port. After changing it, click **Save**; the receiver restarts and listens on the new port immediately.
- **Multicast group** – an IPv4 multicast address (`224.0.0.0`–`239.255.255.255`) to join, so a single sender reaches every station at once – the same model PSN uses. Defaults to `239.20.20.20`; clear it to disable. Unicast (sending straight to a station's IP) and subnet broadcast work regardless of this setting. Which interface takes the subscription is set under **Network Interface Assignment**, in the OSC input row.
- **Allowed sender IPs** – a comma-separated list of IPv4 or IPv6 addresses permitted to inject marker positions (e.g. `192.168.1.10, 192.168.1.20`). Leave blank to accept any host. Individual IP addresses only – CIDR ranges are not supported.

> If the **OSC server** is on and the allowlist is empty, any device on the LAN can update marker positions. Add at least one IP if your show network is shared with other equipment.

## OSC address format

The receiver accepts two message shapes:

- **Full triple** – `/marker/{id}` with three float arguments: `x`, `y`, `z`. All three axes update in one message.
- **Per-axis** – `/marker/{id}/x`, `/marker/{id}/y`, `/marker/{id}/z`, each carrying a single float. Only the addressed axis updates; the others keep their values, allowing partial updates from systems that work axis-by-axis.

In both shapes `{id}` is the integer marker ID (1 or higher). A marker locked onto a path stays on it: a position sent to it lands on the nearest point of its path, or of a path joined to it, and its height is the path's. Coordinates follow OpenFollow's stage convention: X is stage left (positive) / stage right (negative); Y is upstage (positive) / downstage (negative); Z is height above the stage floor. All values are in **metres**, relative to the **Reference Point**.

## Path commands *(experimental)*

- `/path/{name}/active` with one argument, `1` / `0` or `true` / `false`, switches the path called `{name}` on or off. Path names match ignoring case. Switching a path off releases every marker on it where it stands.
- `/marker/{id}/path` with a path name locks marker `{id}` onto that path: it slides there over the slide time and then moves only along it. With no name it releases the marker. Only active paths, and only markers this station controls, are affected.

Path commands are ignored while experimental features are off.

## Saving

- **Save** – writes the current settings and restarts the OSC receiver if the port or enabled state changed. Changes take effect immediately after saving.
