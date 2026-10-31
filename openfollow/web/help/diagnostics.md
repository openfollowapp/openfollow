# Status & Diagnostics

How this station is doing right now, and the file for a support report. The boxes refresh every second while in view, the station list every 5 seconds. Person Detection shows only while its feature is on.

### Station Network

Every OpenFollow station discovered on your LAN, this one and the others.

- **Status indicator** – a green dot is online; a red ring was seen before but is not reachable now. This station's row is blue and labelled *(this station)*.
- **Station name** – the name set under General → Station Settings.
- **Address** – the IP address and web port. Other stations' rows open their web interface in a new window; an offline one opens at its last known address.

Discovery does not cross router hops or VLAN boundaries.

### Video

The active source. When it fails, a red box says what the station saw and what to try.

- **Source** – the configured source.
- **Signal** – `Connected` with a live feed, otherwise the failure the box names.
- **Input resolution** / **Frame Rate (source)** – what the source delivers or advertises. `N/A` until it connects.
- **Pipeline** – `Connecting`, `Reconnecting`, `Connected`, or `Disconnected` once retries have run out.

A red box also appears when arriving video is over Full HD or 30 fps. It keeps playing, but the picture or markers may lag.

### This Station

The Raspberry Pi and the OpenFollow process on it.

A red box means the Pi is short of power or slowed down now: **under voltage** (freezes, USB dropouts) or **CPU throttled** (too hot). An orange box means it happened since the last start and stays until restart.

A red **Unsupported platform** box means OpenFollow is not developed for this hardware or operating system. Supported are a Raspberry Pi 5, CM5 or Pi 500, and on other hardware Debian 13 or a Debian derivative, each with a 64-bit OS and no desktop. Errors are expected anywhere else and no support is provided. The same warning shows on every page and on the Operator Screen.

- **IP** – the address this station is reachable on.
- **Web port** – the port this web UI is served on, and the one other stations are told. **Fallback** means the configured port was taken and a backup port (8080 or 2010) is used.
- **Uptime** – how long the OpenFollow process has been running.
- **Controllers** – gamepads and 3D Mice connected, and how many are missing; a red box names each one.
- **CPU** / **RAM** – load in percent. CPU above roughly 80–90 % can cause frame drops or tracking lag.
- **Temperature** – in °C. Above 80 °C may throttle the Pi.
- **Output resolution** – the canvas the overlay is drawn on. A different aspect ratio than **Input resolution** puts the overlay off the video.
- **Overlay redraw rate** – Operator Screen pictures per second. `0.0 fps` with no screen is normal.
- **Frame clock** – `Running` is normal. `Stalled` (no run for over a second) freezes marker positions, input and detection; PSN, OTP, RTTrPM and OSC keep sending the last known position.

### Station network discovery

How OpenFollow stations on the network find each other: each one sends a beacon and listens for the others'.

The chip shows the worse of the two: **Running**; orange **Send errors**; red **Sender stopped** (other stations do not see this one), **Receiver stopped** (this station does not see the others) or **Stopped** (both).

- **Stations seen** – the other OpenFollow stations currently known on the local network.
- **Last sent** / **Last received** – the most recent beacon out and in.
- **Send errors** – beacons in a row that could not be sent; back to 0 once one goes out.
- **Beacons sent** / **Beacons received** – since the process started.

### Person Detection

The chip: green **Running** (processing frames), blue **Idle** (enabled, waiting, e.g. for video), red **Unavailable** (packages missing, named in a red box), grey **Off** (switched off in its section).

- **Tracked People** – people currently tracked.
- **Inference (avg)** – milliseconds per detection pass. Higher means more load.
- **Inference Rate** – detection passes per second.
- **Detections (last)** – boxes from the last pass, before tracking or smoothing.

### Bundle & tools

The support file, and actions on this station.

- **Download diagnostics bundle** – one plain-text file, named `ofdiag-<station>-<UTC date and time>.txt`, with whether the platform is supported, the video signal, network, configuration, recent errors and a log tail, versions and power or USB faults. Credentials are always redacted. The station keeps its ten newest.
- **Save to USB storage device** – the same file, to the top level of a USB stick or SD card reader on this station. A device that can't be written stays in the list, unavailable, with the reason. A file already on the device is never overwritten. A device the station mounted itself is unmounted again before the save reports success. One save runs at a time.
- **Test peer connectivity** – probes every known station and shows a table: green with a round-trip time when it responded, red with a reason when it was unreachable.
- **Restart application** – restarts the OpenFollow process, not the operating system, after a confirmation. The page reloads once the station is back. Refused while a software update is running.
