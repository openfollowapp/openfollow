# Controller Slots

Which controller holds which slot (`C1`, `C2`, …) on this station, and which marker each slot drives. Gamepads and 3D mice share one numbering. The table refreshes every second while it is open.

## How slots are assigned

Slots follow the **USB socket** each controller is plugged into, not the order the station happened to find them in. Controllers attached when the station starts are numbered by socket, so a station that is switched off and on again with the same cables comes back in the same order. A slot's marker is the matching entry of **Controlled Marker IDs**: `C1` drives the first, `C2` the second.

With exactly one slot, that controller drives whichever marker is selected instead, and the marker-cycle buttons switch it.

## Columns

- **Controller** – the device's name as it reports it. Two identical pads show the same name; the port tells them apart.
- **Kind** – `Gamepad` or `3D Mouse`.
- **Port** – the socket, as the USB host controller's number and the port path: `USB 2 · port 1` is port 1 of host controller 2, and `port 1.4` is port 4 of a hub plugged into port 1. `Bluetooth` for a wireless pad. `no stable port` for a controller whose socket the station cannot read; it keeps its slot for the session, but after a restart it is placed after every controller that has one.
- **State**:
  - **Connected** – the dot lights while the controller is being used (a stick moved or a button held).
  - **Missing** – the controller was unplugged or dropped out. Its slot and marker stay put, so nobody else changes marker. The marker card turns red on the Operator Screen, and the status corner and the Statistics panel name it.
  - **Reserved** – a missing slot that was forgotten. It keeps its place in the numbering but drives no marker and raises no warning.
- **Marker** – the marker this slot drives right now.

## Actions

- **Identify** – pulses the controller where it can (a pad vibrates, a 3D mouse blinks its light) and flashes its slot's marker card on the Operator Screen. On a missing slot only the card flashes.
- **Forget** – on a missing slot, silences it: the slot becomes reserved.

## When a controller comes back

A controller plugged back into its own socket takes its own slot again. A different controller of the same kind takes the lowest missing or reserved slot, so a spare picks up where a failed one left off. On a station with a single slot, any controller takes it over. Anything else joins at the end. A restart numbers everything by socket again.
