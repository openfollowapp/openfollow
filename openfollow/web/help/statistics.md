# Live Statistics

A read-only panel on the Overview tab that refreshes every second. Use it to confirm the pipeline is healthy and to narrow down problems during a show.

## Video

State of the active camera source.

When the source fails, a red banner at the top of the panel gives two lines: what the station saw, then the one thing to try. Passwords are stripped from both. The pipeline's own wording (`Could not open resource for reading and writing.`) is not shown - it names the same fault in terms nothing can be done with. It is in the diagnostics bundle, which is what to attach to a report.

The first line is the station's reading of how far the connection got before it stopped, which is what separates faults that look identical from here:

- **Unreachable** – nothing answered. The address, the route or the cabling.
- **Refused** – something answered and said no. The host is there; the port is not open.
- **Login rejected** – the source answered and refused the credentials.
- **Not found** – nothing was there under that path, name or device.
- **No video** – it answered, and no video reached this station. Typically a blocked media path, or a format nothing here can decode.
- **Unsupported** / **Decode error** – video is arriving; this station cannot turn it into pictures.
- **Stalled** – video was arriving and stopped.
- **Device busy** – a local capture device another program is holding.
- **Not configured** – no source has been set.
- **Failed** – the pipeline reported something this station has no reading for. Here, and only here, its own wording is shown instead, because it is the whole story. **Signal** reads `Disconnected` for this case rather than inventing a label.

- **Source** – the configured source name or type (for example the RTSP URL label or NDI source name). Confirms which input the pipeline is reading from.
- **Signal** – `Connected` with a live feed. Otherwise it carries the reading above rather than a bare `Disconnected`, so the state names which piece of equipment is involved; an unrecognised failure stays `Disconnected`. The panel header chip mirrors this.
- **Input resolution** – pixel dimensions (width × height) of the frames arriving from the source. `N/A` until one actually connects; the *No Signal* picture is generated here and reports no geometry of its own.
- **Frame Rate (source)** – frame rate the source advertises. `N/A` while nothing is connected, for the same reason.
- **Pipeline** – the connection attempt in more detail: `Connecting`, `Reconnecting`, `Connected`, or `Disconnected` once the retries have run out. It is where retry state lives: the banner deliberately carries no attempt counter, because a figure that changes every few seconds moves the box without telling you anything.

## Device

System health for the station hardware.

- **IP** – the network address this station is reachable on. Useful for pointing another tool or peer at it.
- **Controllers** – number of input controllers (gamepads, MIDI devices, etc.) currently connected. A drop to zero during a show indicates a disconnected or unpowered device.
- **CPU** – processor load as a percentage. Sustained values above roughly 80–90 % can cause frame drops or tracking lag.
- **RAM** – memory usage as a percentage. Approaching 100 % on a Raspberry Pi typically causes slowdowns.
- **Temperature** – processor temperature in degrees Celsius; `N/A` on platforms without a thermal sensor. On a Raspberry Pi, sustained values above 80 °C may trigger thermal throttling, visible as CPU spikes paired with frame-rate drops.
- **Output resolution** – the canvas the overlay is drawn on: the real window or full screen, not the size requested under Display. If its aspect ratio differs from **Input resolution**, the overlay sits off the video – that mismatch is the only on-screen explanation for it. `N/A (no display)` with no screen attached.
- **Overlay redraw rate** – how fast this station redraws its overlay. It follows the screen, not the feed: `0.0 fps` with no screen attached is normal and says nothing about the video. Check **Signal** for that.
- **Frame clock** – the loop that reads your input and updates marker positions. `Running` is normal, and it stays running with no display attached. `Stalled` means the loop has not run for over a second: marker positions, input, and detection are frozen, while PSN, OTP, RTTrPM, and OSC keep transmitting the last known position at full rate.

## Person Detection

State of the optional AI-based person detection engine. The panel header chip summarises the state at a glance.

| Chip colour | Meaning |
|-------------|---------|
| Green – **Running** | Detection is enabled and the engine is actively processing frames. |
| Yellow – **Idle** | Detection is enabled but not yet running (for example, waiting for a video signal). |
| Yellow – **Unavailable** | Detection is enabled but required packages are missing; a banner lists them. |
| Grey – **Off** | Detection is disabled. |

- **Status** – a text label matching the header chip state above.
- **Tracked People** – number of people the engine is currently tracking in the frame.
- **Inference (avg)** – average time in milliseconds for one detection pass. Higher means the engine is under load; if it climbs past the inter-frame interval, the inference rate falls.
- **Inference Rate** – detection passes per second completing. Compare to the video frame rate to see how well the engine keeps up.
- **Detections (last)** – raw count of bounding boxes from the last inference pass, before tracking or smoothing.
