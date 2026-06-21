# OSC Destinations

A **destination** is a named, reusable OSC connection: a host, a port, and a transport. Transmitters and trigger zones point at a destination by name, so the connection lives in one place. Edit a destination's host once and every transmitter and zone referencing it repoints live, with no restart.

When a receiver of OSC Data moves to a new IP, change that one destination and every transmitter and zone using it follows.

## Fields

- **Name** – a label for your own reference (e.g. `Main console`, `Media server`). Shown in the Destination dropdowns on transmitters and zones.
- **Host** – destination IP address or hostname.
- **Port** – destination port number (1–65535).
- **Protocol** – `UDP` or `TCP`. UDP is correct for most lighting and audio receivers.
- **Framing** – visible only when Protocol is `TCP`. Choose what your receiver expects:
  - **SLIP (RFC 1055)** – default per OSC 1.1; each packet is delimited by `0xC0`.
  - **Length-prefix (OSC 1.0)** – each packet is preceded by a 32-bit big-endian length.

## Managing destinations

- **+ New destination** – add a blank destination.
- **Save** – write the destination's settings to disk. Changes apply immediately to every transmitter and zone that references this destination.
- **Duplicate** – copy a destination (useful for a second receiver on a different port).
- **↑ / ↓** – reorder the list (cosmetic).
- **Delete** – remove a destination. Any transmitter or zone still pointing at it stops sending until you repoint it at another destination.

## Sharing between stations

Destinations travel with the **config export/import file** alongside the transmitters and zones that reference them, so a saved show carries a consistent routing set. They are deliberately **not** part of real-time peer broadcast: each station keeps its own OSC routing, because the right console IP for one station's network is rarely the right one for another's.
