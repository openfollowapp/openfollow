# Marker Control & Visibility

Defines which markers exist in the show and what this station does with each. The catalog is shared by every OpenFollow station on the LAN; the **This Station** column is local to this station.

## Shared catalog

Synced to all stations within a few seconds of saving.

- **ID** – the number receivers see on PSN, OSC, OTP and RTTrPM (integer ≥ 1). Match your console's numbering.
- **Name** – up to 64 characters. The label in the interface and the tracker name receivers see on PSN, OTP and RTTrPM; a saved rename reaches them within seconds.
- **Color** – the marker's swatch on the Operator Screen and web UI.
- **Controlled by** – which stations currently claim control. A red row with a **Conflict** chip means more than one station controls the marker: each sends it on every output, so receivers see it jump. The Operator Screen shows a red **Control conflict** status row and turns the marker's card red. **Not controlled** means no station has it.
- **This Station** – what this station does with the marker. A click saves it.
  - **View & Control** – inputs routed to this station drive the marker, it is in this station's PSN broadcast, and its card shows on the Operator Screen.
  - **View** – the card shows on the Operator Screen and the marker counts in the trigger zones, whichever station controls it.
  - **Hide** – this station neither drives nor shows the marker.
  - **P** (park) – only on a View & Control row. A parked marker stays on every output at its position and keeps counting in the trigger zones, but keyboard, gamepads and 3D mice skip it. The 2D mouse, OSC and the touch remote still move it; person detection leaves it where it is. Its card carries a blue **P**. Setting the row to View or Hide unparks it. It stays parked after a restart.
- **Prediction (ms)** – experimental, shown while **Output prediction** is on under Features. Only on a View & Control row; a change saves at once. See *Output prediction* below.
- **Save** / **Delete** (per row) – commit a row's name and colour, or remove the marker from all stations after a confirmation. A changed name or colour is outlined in blue, with an **Unsaved** chip, until Save; the catalog's synced values never replace it.

Add a marker in the bottom row (**ID**, **Name**, **Color**, **This Station**, **Add**). The ID pre-fills with the next free integer; This Station starts at Hide.

Controllers follow the order markers were set to View & Control: **C1** drives the first, **C2** the second. A newly controlled marker takes the next slot; setting one back to View or Hide moves the ones after it up a slot.

> Deleting a marker another station has selected removes it from that station on the next sync. Coordinate before deleting during a show.

### Output prediction

How the prediction moves what every output sends.

Each controlled marker can be sent ahead of where it is: **Prediction (ms)**, `0` to `1000`. The station sends the position that far ahead along the marker's current speed, so a moving light with slow pan / tilt or a long fade lands on the performer instead of behind them. Pick the prediction per marker, roughly the time its lights need to catch up.

- `0` (or an empty field) sends the real position. The prediction is this station's setting, like its marker selection: it travels with export and import, not with a push to other stations, and a marker that leaves View & Control loses its prediction. OSC sets it too: `/marker/{id}/prediction <ms>`.
- Only X and Y are predicted; Z is sent as it is.
- Every output carries the predicted position: PSN, OTP, RTTrPM and OSC. Other OpenFollow stations that receive this station's PSN see it too. The PSN speed field stays the real speed.
- The Operator Screen, zones, paths and person detection keep working on the real position.
- When a performer stops abruptly, the sent point overshoots by up to speed × prediction and comes back as the speed settles. It is not clipped to the grid.
- The speed used is capped at 5 m/s. A jump faster than 10 m/s (reset, OSC snap to a preset) sends no prediction for half a second, so lights go straight to the new position. A shorter snap still throws the point briefly past its target.
- Noisy positions (person detection, an external tracker over OSC) are multiplied by the prediction too: a marker that jitters while standing still swings its lights at long predictions. Use a short prediction for those markers.
