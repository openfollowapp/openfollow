# Controller Slots

Which controller holds which slot (`C1`, `C2`, …) on this station, and which marker each slot drives. Gamepads and 3D mice share one numbering. The table refreshes every second while it is open.

## How slots are assigned

Slots follow the **USB socket** each controller is plugged into, not the order the station happened to find them in. Controllers attached when the station starts are numbered by socket, so a station that is switched off and on again with the same cables comes back in the same order. A slot's marker is the matching entry of **Controlled Marker IDs**: `C1` drives the first, `C2` the second.

With exactly one slot, that controller drives whichever marker is selected instead, and the marker-cycle buttons switch it.

## Columns

- **Controller** – the device's name as it reports it. Two identical pads show the same name; the port tells them apart. While the controller is connected, the dot after its name lights up as it is used (a stick moved or a button held).
- **Connection** – the kind (`Gamepad` or `3D Mouse`), the slot's state, and the socket below them.
  - **Connected** – the controller is plugged in and drives the slot's marker.
  - **Missing** – the controller was unplugged or dropped out, or its kind (Gamepads or 3D Mouse) was switched off. Its slot and marker stay put, so nobody else changes marker. The marker card turns red on the Operator Screen, and the status corner and the Statistics panel name it.
  - **Reserved** – a missing slot that was forgotten. It keeps its place in the numbering but drives no marker and raises no warning.
  - The socket is the USB host controller's number and the port path: `on USB 2, port 1` is port 1 of host controller 2, and `port 1.4` is port 4 of a hub plugged into port 1. `on Bluetooth` for a wireless pad. `no stable port` for a controller whose socket the station cannot read; it keeps its slot for the session, but after a restart it is placed after every controller that has one.
- **Marker** – the marker this slot drives right now, by its catalog name and ID, with a dot in its colour. A marker the catalog has no entry for reads `Marker 5`.

## Actions

- **Identify** – on a connected slot, pulses the controller where it can (a pad vibrates, a 3D mouse blinks its light) and flashes its slot's marker card on the Operator Screen.
- **Forget** – on a missing slot, silences it: the slot becomes reserved. A reserved slot still counts as a slot, so the other controllers keep their own markers rather than switching to follow the selected one.

## When a controller comes back

A controller plugged back into its own socket takes its own slot again. A different controller of the same kind takes the lowest missing or reserved slot, so a spare picks up where a failed one left off. On a station with a single slot, any controller takes it over. Anything else joins at the end. Switching a kind back on returns each of its controllers to its own slot. A restart numbers everything by socket again, leaving out a kind that is switched off.
