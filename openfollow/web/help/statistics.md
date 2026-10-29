# Live Statistics

Read-only panel on the Overview tab, refreshed every second while in view.

## Video

When the source fails, a red banner says what the station saw and what to try.

- **Source** – the configured source.
- **Signal** – `Connected` with a live feed, otherwise the failure the banner names.
- **Input resolution** / **Frame Rate (source)** – what the source delivers or advertises. `N/A` until it connects.
- **Pipeline** – `Connecting`, `Reconnecting`, `Connected`, or `Disconnected` once retries have run out.

A red box also appears when arriving video is over Full HD or 30 fps. It keeps playing, but the picture or markers may lag.

## Device

A red box means the Raspberry Pi is short of power or slowed down now: **under voltage** (freezes, USB dropouts) or **CPU throttled** (too hot). An orange box means it happened since the last start and stays until restart.

- **IP** – the address this station is reachable on.
- **Controllers** – gamepads and 3D Mice connected, and how many are missing; a red warning names each one.
- **CPU** / **RAM** – load in percent. CPU above roughly 80–90 % can cause frame drops or tracking lag.
- **Temperature** – in °C. Above 80 °C may throttle the Pi.
- **Output resolution** – the canvas the overlay is drawn on. A different aspect ratio than **Input resolution** puts the overlay off the video.
- **Overlay redraw rate** – Operator Screen pictures per second. `0.0 fps` with no screen is normal; check **Signal** for the video.
- **Frame clock** – `Running` is normal. `Stalled` (no run for over a second) freezes marker positions, input and detection; PSN, OTP, RTTrPM and OSC keep sending the last known position.

## Person Detection

The header chip: green **Running** (processing frames), blue **Idle** (enabled, waiting, e.g. for video), red **Unavailable** (packages missing, listed in a banner), grey **Off**.

- **Tracked People** – people currently tracked.
- **Inference (avg)** – milliseconds per detection pass. Higher means more load.
- **Inference Rate** – detection passes per second.
- **Detections (last)** – boxes from the last pass, before tracking or smoothing.
