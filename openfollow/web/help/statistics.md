# Live Statistics

A read-only panel on the Overview tab that refreshes every second. Use it to confirm the pipeline is healthy and to narrow down problems during a show.

## Video

State of the active camera source.

- **Source** – the configured source name or type (for example the RTSP URL label or NDI source name). Confirms which input the pipeline is reading from.
- **Signal** – `Connected` with a live feed; `Disconnected` when the source is unreachable or not yet opened. The panel header chip mirrors this.
- **Resolution** – pixel dimensions (width × height) of the incoming frames.
- **Frame Rate (measured)** – actual frame rate delivered, measured at runtime. Compare to the source rate to spot frame drops or decoder stalls.
- **Frame Rate (source)** – frame rate advertised by the source. On a healthy pipeline the measured and source rates should be close.
- **Pipeline** – internal GStreamer pipeline state in uppercase (for example `PLAYING`, `PAUSED`, `NULL`). `PLAYING` is normal; anything else means it's not running.

## Device

System health for the station hardware.

- **IP** – the network address this station is reachable on. Useful for pointing another tool or peer at it.
- **Controllers** – number of input controllers (gamepads, MIDI devices, etc.) currently connected. A drop to zero during a show indicates a disconnected or unpowered device.
- **CPU** – processor load as a percentage. Sustained values above roughly 80–90 % can cause frame drops or tracking lag.
- **RAM** – memory usage as a percentage. Approaching 100 % on a Raspberry Pi typically causes slowdowns and should be investigated.
- **Temperature** – processor temperature in degrees Celsius; `N/A` on platforms without a thermal sensor. On a Raspberry Pi, sustained values above 80 °C may trigger thermal throttling, visible as CPU spikes paired with frame-rate drops.

## Person Detection

State of the optional AI-based person detection engine. The panel header chip summarises the state at a glance.

| Chip colour | Meaning |
|-------------|---------|
| Green – **Running** | Detection is enabled and the engine is actively processing frames. |
| Yellow – **Idle** | Detection is enabled but not yet running (for example, waiting for a video signal). |
| Yellow – **Unavailable** | Detection is enabled but required packages are missing. A banner lists them and prompts you to install from the Person Detection section, then restart. |
| Grey – **Off** | Detection is disabled. |

- **Status** – a text label matching the header chip state above.
- **Tracked People** – number of people the engine is currently tracking in the frame.
- **Inference (avg)** – average time in milliseconds for one detection pass. Higher means the engine is under load; if it climbs past the inter-frame interval, the inference rate falls.
- **Inference Rate** – detection passes per second completing. Compare to the video frame rate to see how well the engine keeps up.
- **Detections (last)** – raw count of bounding boxes from the last inference pass, before tracking or smoothing.

> If you see **Unavailable** with a missing-packages banner, go to the Person Detection tab, install the listed packages, and use the **Restart application** button in the Diagnostics section to apply the change.
