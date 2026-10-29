# OSC Input

Lets lighting consoles, show-control software and media servers drive marker positions over the network.

## OSC Server

- **OSC server** – master toggle. When off, incoming OSC is ignored and the port is released.
- **UDP Port** – 1–65535, default `8765`. Every sender must target it. Saving restarts the receiver on the new port.
- **Multicast group** – IPv4 multicast address to join (`224.0.0.0`–`239.255.255.255`), default `239.20.20.20`; clear it to disable. Unicast and subnet broadcast work regardless. The interface is set under **Network Interface Assignment**; in Simple network mode it is the station interface.
- **Allowed sender IPs** – comma-separated IPv4 addresses allowed to move markers (e.g. `192.0.2.10, 192.0.2.20`). Blank accepts any host. No CIDR ranges or IPv6.

With the server on and this list empty, any device on the LAN can move markers.

## Address format

- `/marker/{id}` with three floats `x`, `y`, `z` updates all axes.
- `/marker/{id}/x`, `/y`, `/z` with one float updates only that axis.

`{id}` is the marker ID (1 or higher). X is stage left (positive) / right (negative), Y is upstage (positive) / downstage (negative), Z is height above the floor. Values are in **metres**, relative to the Reference Point.

`/marker/{id}/park` and `/marker/{id}/unpark` park and unpark a marker this station controls; arguments are ignored. A parked marker still follows position messages; only keyboard, gamepads and 3D Mice leave it alone. Messages for markers this station does not control are ignored.

**Save** – applies the settings; the receiver restarts if the port or enabled state changed.
