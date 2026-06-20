# Trigger Zones

> Zone Occupancy Detection is an experimental feature. The wire format and field names may change between releases.

Polygon regions on the stage plane that fire OSC messages whenever a marker or detection crosses in or out. Use them to drive cue-style automation the moment a performer enters or leaves an area – without a continuous positional stream.

## Global

These settings govern the zone engine as a whole and apply to every zone.

- **Enabled** – master on/off for the entire zone engine. Off by default; turn it on before configuring individual zones in the Zone Editor.
- **Show Overlay** – draw zone outlines on the live video overlay. Has no effect on OSC output.
- **Eval Rate (FPS)** – how many times per second the engine checks each marker against every zone. Lower values reduce CPU load; higher values give more responsive detection. Options: 1, 5, 10, 15, 30, 60 FPS. Default `10`.
- **Debounce (ms)** – suppresses rapid in/out flicker at a boundary; an event fires only after the marker has stayed on the new side for at least this long. Range 0–60 000 ms; default `200`.
- **Hysteresis** – a buffer band just inside each zone boundary; a marker must move this distance past the edge before an exit fires, preventing repeated events when a performer stands on a border. Range 0–10 m; default `0.05 m`. On a station set to imperial units, enter a value in feet and inches – it is stored internally in metres.

> If zones flicker with rapid in/out events, increase **Hysteresis** first; use **Debounce** as the secondary safety net.

- **Default OSC Host** – IP address that zone OSC messages are sent to, used by any zone that hasn't set its own destination. Default `127.0.0.1`.
- **Default OSC Port** – UDP port for the default destination. Default `53000` (QLab's standard OSC receive port). Range 1–65535.

**Save** – write the global settings to disk. The zone engine reloads immediately; no restart is needed.
