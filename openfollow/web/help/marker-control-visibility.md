# Marker Control & Visibility

Defines which markers exist in the show and what this station does with each. The **catalog** is shared across every OpenFollow station on the LAN; the **This Station** column is local to this station.

## Shared catalog

A live table of every marker, synced to all stations within a few seconds of saving. Very large catalogs (roughly 55 markers and up) sync in batches, so the last of them can take a few heartbeats longer to appear on every station. Each row:

- **ID** – numeric identifier (integer ≥ 1) that receivers see on PSN, OSC, OTP, and RTTrPM. Match your console's numbering.
- **Name** – a human-readable label for the interface, up to 64 characters; longer names are shortened when saved. Not sent on the PSN wire.
- **Color** – the marker's swatch on the Operator Screen and web UI; click it to open the picker. The add-row suggests the first unused palette colour.
- **Controlled by** – read-only: which stations currently claim control. A red row with a **Conflict** chip flags a control conflict (more than one station controlling the same marker); it clears once only one station still has the marker on View & Control. A marker no station controls reads **Not controlled**.
- **This Station** – what this station does with the marker. A click saves it: **Saved** appears beside the column header and the row flashes green (red if the save failed).
  - **View & Control** – inputs routed to this station (gamepad, keyboard, mouse, OSC) drive the marker, it's in this station's PSN broadcast, and its card shows on the Operator Screen. A controlled marker is always shown.
  - **View** – the marker's card shows on the Operator Screen and it counts in the trigger zones, whichever station controls it.
  - **Hide** – this station neither drives nor shows the marker.
- **Save** / **Delete** (per row) – commit a row's name/colour, or remove the marker from all stations (after a confirmation prompt).

Add a marker via the bottom row (**ID**, **Name**, **Color**, **This Station** → **Add**); the ID pre-fills with the next free integer, and This Station starts at Hide.

Controllers follow the order markers were set to View & Control: **C1** drives the first, **C2** the second. A newly controlled marker takes the next slot, so no controller changes marker; setting one back to View or Hide moves the ones after it up a slot.

> Deleting a marker another station still has selected removes it from that station on the next sync – coordinate before removing catalog entries during a show.
