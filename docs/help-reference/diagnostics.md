# Status & Diagnostics

How this station is doing right now, and the information needed for a support report. It is the Overview tab's one section: the Station Network list at the top refreshes every 5 seconds, the boxes below it every second, while the section is open and in view. Each box shows only what applies: Person Detection appears only while its feature is on.

On a wide screen Station Network runs full width, and the boxes below it keep their own height: This Station fills the right column and the others stack beside it. With Person Detection shown, Station network discovery runs full width beneath, its rows in three columns. On a phone they stack in one column.

## Station Network

A live view of every OpenFollow station discovered on your LAN. Confirm all stations are online and reachable before a show.

Each row is one station – this one or another discovered by multicast.

- **Status indicator** – a green dot = online and responding; a red ring = seen previously but not currently reachable. The row takes the same colour, and this station's row is blue.
- **Station name** – the name set under General → Station Settings. This station's row is labelled *(this station)*.
- **Address** – the IP address and web port the station serves on (e.g. `192.0.2.42:80`). Every other station's row opens its web interface in a new window, an offline one at its last known address; the mark after the name says so.

> If no other stations appear, check that the other OpenFollow instances are running and on the same network segment. Multicast discovery does not cross router hops or VLAN boundaries.

The list refreshes on its own every 5 seconds, so a station brought online appears within a few seconds.

## Video

State of the active camera source.

When the source fails, a red box at the top of the box gives two lines: what the station saw, then the one thing to try. Passwords are stripped from both. The pipeline's own wording (`Could not open resource for reading and writing.`) is not shown - it names the same fault in terms nothing can be done with. It is in the diagnostics bundle, which is what to attach to a report.

The first line is the station's reading of how far the connection got before it stopped, which is what separates faults that look identical from here:

- **Unreachable** – nothing answered. The address, the route or the cabling.
- **Refused** – something answered and said no. The host is there; the port is not open.
- **Login rejected** – the source answered and refused the credentials.
- **Not found** – nothing was there under that path, name or device.
- **No video** – it answered, and no video reached this station. Typically a blocked media path, or a format nothing here can decode.
- **Unsupported** / **Decode error** – video is arriving; this station cannot turn it into pictures.
- **Stalled** – video was arriving and stopped.
- **Device busy** – a local capture device another program is holding.
- **Unsupported mode** – the source could not deliver the format, size or frame rate asked of it, so no video started.
- **Not configured** – no source has been set.
- **Failed** – the pipeline reported something this station has no reading for. Here, and only here, its own wording is shown instead, because it is the whole story. **Signal** reads `Disconnected` for this case rather than inventing a label.

- **Source** – the configured source name or type (for example the RTSP URL label or NDI source name). Confirms which input the pipeline is reading from.
- **Signal** – `Connected` with a live feed. Otherwise it carries the reading above rather than a bare `Disconnected`, so the state names which piece of equipment is involved; an unrecognised failure stays `Disconnected`. The box's header chip mirrors this.
- **Input resolution** – pixel dimensions (width × height) of the frames arriving from the source. `N/A` until one actually connects; the *No Signal* picture is generated here and reports no geometry of its own.
- **Frame Rate (source)** – frame rate the source advertises. `N/A` while nothing is connected, for the same reason.
- **Pipeline** – the connection attempt in more detail: `Connecting`, `Reconnecting`, `Connected`, or `Disconnected` once the retries have run out. It is where retry state lives: the banner deliberately carries no attempt counter, because a figure that changes every few seconds moves the box without telling you anything.

A red box also appears while the video arriving is more than the station is sized for: over Full HD (the longer side over 1920 or the shorter over 1080 pixels) or over 30 fps. It names what arrives beside the limit, then the one thing to change. The video keeps playing; the box says why the picture or the markers may lag. Its frame rate is counted from the frames that arrive, so it can differ from **Frame Rate (source)**, which is what the source advertises. **Video Source** explains the limits.

## This Station

The station hardware and the OpenFollow process running on it.

A red box above the list means the Raspberry Pi is short of power or slowed down right now: **under voltage** when the power supply cannot hold 5 V, which leads to freezes and USB devices dropping out, or **CPU throttled** when the Pi has slowed its processor because it is too hot. An under voltage slows the processor too, so while one lasts only the power supply is named. The second line is the usual fix.

Once it passes, an orange box says it was reported since the station last started. It stays until the Pi restarts, because that is how long the Pi itself keeps the record, so an intermittent fault is still visible after the moment has gone. The Operator Screen shows the live state only, as a red row in the top right that stays 10 s after the state passes, so a run of short dips reads as one warning.

A station that is not a Raspberry Pi, or cannot read these, shows neither box; `/api/stats` reports them as not available.

- **IP** – the network address this station is reachable on. Useful for pointing another tool or station at it.
- **Web port** – the port this web UI is served on, and the one other stations are told. If the configured port was unavailable, or another program already used it, OpenFollow falls back to a backup port (8080 or 2010): the row shows that port with an orange **Fallback** chip. The configured port is in the configuration, and in the diagnostics bundle.
- **Uptime** – how long the OpenFollow process has been running.
- **Controllers** – gamepads and 3D mice connected, and how many are missing. A red warning above the list names each missing controller, the marker its slot drives and the port it was in; the Controller Slots section on the Input tab shows the rest.
- **CPU** – processor load as a percentage. Sustained values above roughly 80–90 % can cause frame drops or tracking lag.
- **RAM** – memory usage as a percentage. Approaching 100 % on a Raspberry Pi typically causes slowdowns.
- **Temperature** – processor temperature in degrees Celsius; `N/A` on platforms without a thermal sensor. On a Raspberry Pi, sustained values above 80 °C may trigger thermal throttling, visible as CPU spikes paired with frame-rate drops.
- **Output resolution** – the canvas the overlay is drawn on: the real window or full screen, not the size requested under Display. If its aspect ratio differs from **Input resolution**, the overlay sits off the video – that mismatch is the only on-screen explanation for it. `N/A (no display)` with no screen attached.
- **Overlay redraw rate** – how many Operator Screen pictures this station draws a second. It follows the screen, not the feed: at most the display's refresh rate, or a steady half, third and so on of it once pictures take longer than one, two or more refreshes; `0.0 fps` with no screen attached is normal and says nothing about the video. Check **Signal** for that.
- **Frame clock** – the loop that reads your input and updates marker positions. `Running` is normal, and it stays running with no display attached. `Stalled` means the loop has not run for over a second: marker positions, input, and detection are frozen, while PSN, OTP, RTTrPM, and OSC keep transmitting the last known position at full rate.

## Station network discovery

How OpenFollow stations on the local network find each other: each one sends a beacon and listens for the others'. If the sender is unhealthy, other stations won't see this one; if the receiver is unhealthy, this station won't see the others.

The header chip shows the worse of the two:

| Chip colour | Meaning |
|-------------|---------|
| Green – **Running** | Sender and receiver both running, the last beacon sent. |
| Orange – **Send errors** | The sender runs, but its latest beacons could not be sent. |
| Red – **Sender stopped** | Other stations do not see this one. |
| Red – **Receiver stopped** | This station does not see the others. |
| Red – **Stopped** | Neither runs. |

- **Stations seen** – number of OpenFollow stations currently known on the local network.
- **Last sent** – age of the most recent outgoing beacon.
- **Last received** – age of the most recent received beacon.
- **Send errors** – beacons in a row that could not be sent; back to 0 once one goes out.
- **Beacons sent** – total beacons sent since the process started.
- **Beacons received** – total packets the receiver has consumed since the process started.

## Person Detection

State of the optional AI-based person detection engine. The box is shown only while the Person Detection feature is on (Station Settings › Advanced Settings › Features); with the feature off, its missing-packages alert is not spoken either. The header chip summarises the state at a glance.

| Chip colour | Meaning |
|-------------|---------|
| Green – **Running** | Detection is enabled and the engine is actively processing frames. |
| Blue – **Idle** | Detection is enabled but not yet running (for example, waiting for a video signal). |
| Red – **Unavailable** | Detection is enabled but required packages are missing; a banner lists them. |
| Grey – **Off** | The feature is on, but detection is switched off in its section. |

- **Tracked People** – number of people the engine is currently tracking in the frame.
- **Inference (avg)** – average time in milliseconds for one detection pass. Higher means the engine is under load; if it climbs past the inter-frame interval, the inference rate falls.
- **Inference Rate** – detection passes per second completing. Compare to the video frame rate to see how well the engine keeps up.
- **Detections (last)** – raw count of bounding boxes from the last inference pass, before tracking or smoothing.

## Bundle & tools

Four actions sit below the boxes.

- **Download diagnostics bundle** – produces a single plain-text file and saves it in your browser, named `ofdiag-<station>-<UTC date and time>.txt` (for example `ofdiag-Stage_Left-20260928T101500Z.txt`); the station keeps a copy of its ten newest. It captures what the station is doing: the video signal and its last error, the frame clock, person detection and connected controllers; network interfaces and routes, where each interface's address came from, and which interface each network function is set to use beside the one it is using now; whether the station has internet, and when that was last checked; the configuration, with any values changed from the defaults listed separately; recent errors and a log tail, in the station's local time (the file's first lines name its zone), with repeated blocks folded to one copy and a count; which video inputs this station can offer and why any are missing; the detection models present on disk; the installed package version against the running one; and OS, hardware and dependency versions, including any power or USB faults the kernel has recorded. It also reports whether the camera's address is one this station can reach, which includes one brief connection attempt to it; no stream data is sent. Credentials are always redacted: the web PIN, camera login and SRT passphrase become `***` (or `(empty)` where none is set), and any login inside a stream URL is stripped from the config and every log line.

- **Save to USB storage device** – opens a dialog that saves the same file to the top level of a USB stick or SD card reader attached to this station. It lists the attached devices, read again about once a second while it is open, so a device plugged in now appears. One that can't be written stays in the list, unavailable, with the reason: a format the station can't write (APFS or HFS+ on a Raspberry Pi), a device mounted read-only, or `needs Apply Permissions` where the station lacks the permission to mount it. A file already on the device is never overwritten: a name that is taken gets `-1`, `-2`. A device the station mounted itself is unmounted again before the save reports success, so it can be pulled straight away; one that something else mounted stays mounted. After **Save** the dialog shows what the save is doing, then what happened and one next step, as the Operator Screen does; closed while it runs, the save carries on, and the button shows it again. Only one save runs at a time, from here or from **Export Diagnostics File for Support** in the Operator Screen's Settings menu.

- **Test peer connectivity** – probes every known station on its advertised port and displays a small results table. A green chip means the station responded with an HTTP status and a round-trip time; a red chip means it was unreachable, with a reason. If the table shows "No peers known yet", wait for discovery or check **Station network discovery** above.

- **Restart application** – restarts the OpenFollow process (not the operating system). A confirmation prompt appears first; the page reloads automatically once the station comes back. It is refused while a software update is running. Use this after changing a setting that requires a restart, such as the web port or Person Detection engine.
