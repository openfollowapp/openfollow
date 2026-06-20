# Marker Control & Visibility

Defines which markers exist in the show and how this station drives and displays them. The **catalog** is shared across every OpenFollow station on the LAN; the **selection** is local to this station.

## Shared catalog

A live table of every marker, synced to all stations within a few seconds of saving. Each row:

- **ID** – numeric identifier (integer ≥ 1) that receivers see on PSN, OSC, OTP, and RTTrPM. Match your console's numbering.
- **Name** – a human-readable label for the interface; not sent on the PSN wire.
- **Color** – the marker's swatch on the Operator Screen and web UI; click it to open the picker. The add-row suggests the first unused palette colour.
- **Controlled by / Viewed by** – read-only: which stations currently claim control or view. A ⚠ and red row highlight flag a control conflict (more than one station controlling the same marker); resolve it in each station's selection table.
- **Save** / **Delete** (per row) – commit a row's name/colour, or remove the marker from all stations (after a confirmation prompt).

Add a marker via the bottom row (**ID**, **Name**, **Color** → **Add**); the ID pre-fills with the next free integer.

> Deleting a marker another station still has selected removes it from that station on the next sync – coordinate before removing catalog entries during a show.

## This station's selection

Which markers this station drives and renders. Changes apply immediately and auto-save (no Save button).

- **Control** – claim control: inputs routed to this station (gamepad, keyboard, mouse, OSC) can drive the marker, and it's included in this station's PSN broadcast.
- **View** – render the marker on the Operator Screen regardless of which station controls it.

The two are independent per row – view without controlling, or control without rendering.
