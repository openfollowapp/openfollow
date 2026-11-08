# Trigger Zones

Polygon regions on the stage plane that send OSC messages when a marker or detection moves in or out. Draw the zones in the Zone Editor.

## Global

These settings apply to every zone.

- **Trigger zones** – master on/off for all zones. Off by default.
- **Overlay** – draw the zones on the Operator Screen as filled outlines. Does not affect OSC output.
- **Occupied zones** – on the Operator Screen, a zone is drawn stronger while a marker or detection is inside it. On by default; off, every zone looks the same whatever is inside. Needs **Trigger zones** on.
- **Eval Rate (FPS)** – how often each marker is checked against every zone: 1, 5, 10, 15, 30 or 60. Default `10`. Lower saves CPU, higher reacts sooner.
- **Debounce (ms)** – an event fires only after the marker has stayed on the new side this long. 0–60 000 ms, default `200`.
- **Hysteresis** – the distance a marker must move past the edge before an exit fires. 0–10 m, default `0.05 m`.

If zones flicker, raise **Hysteresis** first, then **Debounce**.

Each zone sends to an **OSC Destination** chosen in the zone editor. **Save** applies at once.
