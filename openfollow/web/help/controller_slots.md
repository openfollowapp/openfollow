# Controller Slots

Which controller holds which slot (`C1`, `C2`, …) and which marker each slot drives. Gamepads and 3D Mice share one numbering. The table refreshes every second while open.

Slots follow the **USB socket** each controller is plugged into, so the same cables give the same order after a restart. Slot `C1` drives the first entry of **Controlled Marker IDs**, `C2` the second. With exactly one slot, that controller drives whichever marker is selected, and the marker-cycle buttons switch it. A **pinned** slot belongs to whatever is plugged into its socket.

## Columns

- **Controller** – the device's name. Two identical pads show the same name; the port tells them apart. The dot after the name lights up as the controller is used. Notes below the name:
  - **Buttons not recognised** – no layout is known for this pad and no Button Detection Map is saved, so a button may not do what its label says.
  - **Button map is for another model** – the saved Button Detection Map was made on a different model.
  - **Can't identify** – the controller cannot vibrate and has no light to drive; Identify only flashes its marker card.
- **Connection** – the kind (`Gamepad` or `3D Mouse`), the slot's state and the socket (such as `on USB 2, port 1`, `on Bluetooth`, or `no stable port`), or **Pinned to …** for a pinned slot.
  - **Connected** – plugged in and driving the slot's marker.
  - **Missing** – unplugged, dropped out, or its kind was switched off. Slot and marker stay put, so nobody else changes marker. The marker card turns red on the Operator Screen, and the status corner and Statistics panel name it.
  - **Reserved** – a missing slot that was forgotten. It keeps its place in the numbering but drives no marker and raises no warning.
  - **Empty** – a free slot below a pinned one; the next controller plugged in takes it.
- **Marker** – the marker this slot drives, by name and ID, with a dot in its colour. A blue **P** means the marker is parked: the controller stays connected but moves and resets nothing until the marker is unparked (Marker Control & Visibility). On a connected or missing slot it is a picker of the controlled markers; a lone controller shows **All markers**.

### Picking a slot's marker

Swaps, and what stays on this station.

Picking a marker another slot drives swaps the two; if this slot had none, the other is left without one. Picks stay on this station: export and sending settings leave them out.

## Actions

- **Identify** – on a connected slot, pulses the controller (a pad vibrates, a 3D Mouse blinks its light) and flashes its marker card on the Operator Screen.
- **Forget** – on a missing slot, makes it reserved. Reserved slots still count, so the other controllers keep their markers.
- **Pin here** – waits up to 10 seconds for a button press and pins that controller's socket to this slot, swapping it with the slot's controller. No marker moves while it waits; **Cancel** or Esc stops it. A controller with no stable port (as on macOS) can't be pinned.
- **Unpin** – drops the pin. Slots renumber by socket at the next restart.

### When a controller comes back

Which slot it takes.

A controller plugged into a pinned socket takes that slot. One plugged back into its own socket takes its own slot again. A different controller of the same kind takes the lowest missing or reserved slot. Anything else joins at the end. A restart numbers everything by socket again.
