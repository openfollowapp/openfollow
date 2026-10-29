# OSC Destinations

A destination is a named OSC connection: host, port and transport. Transmitters and trigger zones point at it by name. Edit a destination once and everything using it follows live, with no restart.

## Fields

- **Name** – a label for your own reference. Shown in the Destination dropdowns.
- **Host** – IP address or hostname.
- **Port** – 1–65535.
- **Protocol** – `UDP` or `TCP`. UDP suits most lighting and audio receivers.
- **Framing** – TCP only. **SLIP (RFC 1055)** is the OSC 1.1 default; **Length-prefix (OSC 1.0)** precedes each packet with a 32-bit length.
- **Source Interface** – read-only: the interface messages leave from, set under **General → Network Interface Assignment**. Not shown in Simple network mode.

## Managing destinations

- **+ New destination** – add a blank one.
- **Save** – applies at once to every transmitter and zone using it.
- **Duplicate** – copy a destination.
- **Delete** – transmitters and zones still pointing at it stop sending until you repoint them.

Destinations travel with the config export/import file, but are not part of peer broadcast: each station keeps its own routing.
