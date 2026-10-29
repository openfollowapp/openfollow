# Marker Control & Visibility

Defines which markers exist in the show and what this station does with each. The catalog is shared by every OpenFollow station on the LAN; the **This Station** column is local to this station.

## Shared catalog

Synced to all stations within a few seconds of saving.

- **ID** – the number receivers see on PSN, OSC, OTP and RTTrPM (integer ≥ 1). Match your console's numbering.
- **Name** – label for the interface, up to 64 characters. Not sent on the PSN wire.
- **Color** – the marker's swatch on the Operator Screen and web UI.
- **Controlled by** – which stations currently claim control. A red row with a **Conflict** chip means more than one station controls the marker: each sends it on every output, so receivers see it jump. The Operator Screen shows a red **Control conflict** status row and turns the marker's card red. **Not controlled** means no station has it.
- **This Station** – what this station does with the marker. A click saves it.
  - **View & Control** – inputs routed to this station drive the marker, it is in this station's PSN broadcast, and its card shows on the Operator Screen.
  - **View** – the card shows on the Operator Screen and the marker counts in the trigger zones, whichever station controls it.
  - **Hide** – this station neither drives nor shows the marker.
  - **P** (park) – only on a View & Control row. A parked marker stays on every output at its position and keeps counting in the trigger zones, but keyboard, gamepads and 3D mice skip it. The 2D mouse, OSC and the touch remote still move it; person detection leaves it where it is. Its card carries a blue **P**. Setting the row to View or Hide unparks it. It stays parked after a restart.
- **Save** / **Delete** (per row) – commit a row's name and colour, or remove the marker from all stations after a confirmation.

Add a marker in the bottom row (**ID**, **Name**, **Color**, **This Station**, **Add**). The ID pre-fills with the next free integer; This Station starts at Hide.

Controllers follow the order markers were set to View & Control: **C1** drives the first, **C2** the second. A newly controlled marker takes the next slot; setting one back to View or Hide moves the ones after it up a slot.

> Deleting a marker another station has selected removes it from that station on the next sync. Coordinate before deleting during a show.
