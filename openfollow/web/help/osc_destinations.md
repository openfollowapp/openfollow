# OSC Destinations

A destination is a named OSC connection: host, port and transport. Transmitters and trigger zones point at it by name. Edit a destination once and everything using it follows live, with no restart.

## Fields

- **Name** – a label for your own reference. Shown in the Destination dropdowns.
- **Host** – IP address or hostname.
- **Port** – 1–65535.
- **Protocol** – `UDP` or `TCP`. UDP suits most lighting and audio receivers.
- **Framing** – TCP only. **SLIP (RFC 1055)** is the OSC 1.1 default; **Length-prefix (OSC 1.0)** precedes each packet with a 32-bit length.
- **Source Interface** – read-only: the interface messages leave from, set under **General → Network Interface Assignment**. Not shown in Simple network mode.

## When a destination cannot be reached

A destination whose sends keep failing while its interface is up, refused by a firewall or with no route to its host, gets a red row on the Operator Screen and in Statistics → Device, named after the destination (its address when it has no name). Only destinations an enabled transmitter or trigger zone sends to are shown. The row clears once sends to it have kept going through for a second.

A **TCP** destination that is powered off, or not listening on its port, shows that red row for as long as it stays that way, a console switched off during a rehearsal included: every TCP send needs a connection, and none can be made. Start the receiver, or switch off the transmitters and zones that send to it, and the row clears. A destination taken out of use starts afresh when it is used again: its row comes back only if its sends fail again.

## Managing destinations

- **+ New destination** – add a blank one.
- **Save** – applies at once to every transmitter and zone using it.
- **Duplicate** – copy a destination.
- **Delete** – transmitters and zones still pointing at it stop sending until you repoint them.

Destinations travel with the config export/import file, but are not part of peer broadcast: each station keeps its own routing.
