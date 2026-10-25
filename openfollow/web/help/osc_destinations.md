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
- **Source Interface** – read-only here; the interface this destination's messages leave from, set under **General → Network Interface Assignment**. `Follows station default interface` is the default. In Simple network mode every destination follows the station interface and this row is not shown.

## When a destination cannot be reached

A destination whose sends keep failing while its interface is up, refused by a firewall or with no route to its host, gets a red row on the Operator Screen and in Statistics → Device, named after the destination (its address when it has no name). Only destinations an enabled transmitter or trigger zone sends to are shown. The row clears once sends to it have kept going through for a second.

A **TCP** destination that is powered off, or not listening on its port, shows that red row for as long as it stays that way, a console switched off during a rehearsal included: every TCP send needs a connection, and none can be made. Start the receiver, or switch off the transmitters and zones that send to it, and the row clears. A destination taken out of use starts afresh when it is used again: its row comes back only if its sends fail again.

## Managing destinations

- **+ New destination** – add a blank destination.
- **Save** – write the destination's settings to disk. Changes apply immediately to every transmitter and zone that references this destination.
- **Duplicate** – copy a destination (useful for a second receiver on a different port).
- **Reorder** – drag the ⋮⋮ handle on the left of a collapsed destination to reorder the list (cosmetic).
- **Delete** – remove a destination. Any transmitter or zone still pointing at it stops sending until you repoint it at another destination.

## Sharing between stations

Destinations travel with the **config export/import file** alongside the transmitters and zones that reference them, so a saved show carries a consistent routing set. They are deliberately **not** part of real-time peer broadcast: each station keeps its own OSC routing, because the right console IP for one station's network is rarely the right one for another's.
