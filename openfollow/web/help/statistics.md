# Live Statistics

A read-only panel on the Overview tab that refreshes every second. Use it to confirm the pipeline is healthy and to narrow down problems during a show.

## Video

State of the active camera source.

When the source fails, a red banner at the top of the panel gives the reason reported by the video pipeline itself – `Unauthorized`, `Connection refused`, `Could not open resource for reading`, or the name of a source that no longer exists. It is shown word for word rather than translated into something friendlier, because the exact wording is what tells you whether to check the network, the encoder, or the login. While the station is still retrying, the banner also counts the attempts, so a source working through its reconnect schedule is distinguishable from one that has given up. Any password embedded in a stream URL is stripped before the banner is drawn.

- **Source** – the configured source name or type (for example the RTSP URL label or NDI source name). Confirms which input the pipeline is reading from.
- **Signal** – `Connected` with a live feed; `Disconnected` when the source is unreachable or not yet opened. The panel header chip mirrors this. When it reads `Disconnected`, the banner above says why.
- **Input resolution** – pixel dimensions (width × height) of the frames arriving from the source, read from the negotiated stream. `N/A` until a source has actually connected: the black *No Signal* picture is generated on this station, so it reports no geometry of its own.
- **Frame Rate (source)** – frame rate the source advertises in that same stream. `N/A` while nothing is connected, for the same reason.
- **Pipeline** – the connection attempt behind the Signal row, in more detail: `Connected` when frames are arriving, `Connecting` while the first attempt is still open, `Reconnecting` while retries are in progress, and `Disconnected` once they have run out. It is the row that separates a source still working through its retries from one that has given up – Signal reads `Disconnected` for both.

Every figure in this panel describes the incoming feed only. Nothing in this panel measures how fast this station is drawing – that lives under **Device**, and a station with no screen attached draws nothing while the feed stays perfectly healthy.

## Device

System health for the station hardware.

- **IP** – the network address this station is reachable on. Useful for pointing another tool or peer at it.
- **Controllers** – number of input controllers (gamepads, MIDI devices, etc.) currently connected. A drop to zero during a show indicates a disconnected or unpowered device.
- **CPU** – processor load as a percentage. Sustained values above roughly 80–90 % can cause frame drops or tracking lag.
- **RAM** – memory usage as a percentage. Approaching 100 % on a Raspberry Pi typically causes slowdowns and should be investigated.
- **Temperature** – processor temperature in degrees Celsius; `N/A` on platforms without a thermal sensor. On a Raspberry Pi, sustained values above 80 °C may trigger thermal throttling, visible as CPU spikes paired with frame-rate drops.
- **Output resolution** – the size of the canvas the overlay is drawn on: the actual window, or the whole screen when running fullscreen, rather than the size requested under Display. Read it against **Input resolution** in the Video panel. Camera calibration is solved against the input while the grid, zones, and markers are drawn across the output, so when the two have different aspect ratios the overlay sits off the video with nothing else on screen to explain it. `N/A (no display)` on a station with no screen attached.
- **Overlay redraw rate** – how often this station redraws its on-screen overlay, in frames per second. This measures the display, not the feed: it tracks the screen's refresh rate, and it reads `0.0 fps` on a station with no screen attached while video, tracking, and every position output keep running normally. It is not a measure of video throughput, and a healthy figure here says nothing about whether a single video frame has arrived – check **Signal** in the Video panel for that.
- **Frame clock** – the loop that reads your input and updates marker positions. `Running` is normal, and it stays running with no display attached. `Stalled` means the loop has not run for over a second: marker positions, input, and detection are frozen, while PSN, OTP, RTTrPM, and OSC keep transmitting the last known position at full rate. To a receiving console that looks like a healthy stream whose coordinates never move, so treat this chip as the first thing to check when a station appears connected but nothing follows. Restart the application from the Diagnostics section, and send the diagnostics bundle if it recurs.

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
