# Diagnostics

Live runtime health for the web server and peer discovery. Use this section to spot a sick subsystem at a glance and to download or view the information needed for a support report.

The section is inside the Overview tab and refreshes every five seconds.

## Live summary cards

Three cards across the top of the section. Each card shows a status chip and a small set of metrics.

### Web server

- **Configured** – the web port set in your configuration.
- **Serving on** – the port the server actually bound to. If port 80 was unavailable, OpenFollow falls back to a backup port (8080 or 2010) and this row shows which one is in use.
- **Uptime** – how long the OpenFollow process has been running.
- Status chip reads **OK** when the configured and serving ports match, and **fallback** when the server is on a backup port.

### Beacon sender

Health of the outgoing peer-discovery beacon. If this card is unhealthy, other stations on the network won't see this one.

- **Last sent** – timestamp of the most recent outgoing beacon.
- **Errors** – running count of send errors since the process started.
- **Sent count** – total beacons sent since the process started.

### Beacon receiver

Health of the incoming peer-discovery listener. If this card is unhealthy, this station won't see other stations.

- **Peers seen** – number of OpenFollow stations currently known on the local network.
- **Last packet** – timestamp of the most recent received beacon.
- **Packets total** – running count of packets the receiver has consumed since the process started.

## Bundle & tools

Four actions sit below the cards.

- **Download diagnostics bundle** – produces a single plain-text file and saves it in your browser, named `ofdiag-<station>-<UTC date and time>.txt` (for example `ofdiag-Stage_Left-20260928T101500Z.txt`); the station keeps a copy of its ten newest. It captures what the station is doing: the video signal and its last error, the frame clock, person detection and connected controllers; network interfaces and routes, where each interface's address came from, and which interface each network function is set to use beside the one it is using now; whether the station has internet, and when that was last checked; the configuration, with any values changed from the defaults listed separately; recent errors and a log tail, in the station's local time (the file's first lines name its zone), with repeated blocks folded to one copy and a count; which video inputs this station can offer and why any are missing; the detection models present on disk; the installed package version against the running one; and OS, hardware and dependency versions, including any power or USB faults the kernel has recorded. It also reports whether the camera's address is one this station can reach, which includes one brief connection attempt to it; no stream data is sent. Credentials are always redacted: the web PIN, camera login and SRT passphrase become `***` (or `(empty)` where none is set), and any login inside a stream URL is stripped from the config and every log line.

- **Save to USB storage device** – opens a dialog that saves the same file to the top level of a USB stick or SD card reader attached to this station. It lists the attached devices, read again about once a second while it is open, so a device plugged in now appears. One that can't be written stays in the list, unavailable, with the reason: a format the station can't write (APFS or HFS+ on a Raspberry Pi), a device mounted read-only, or `needs Apply Permissions` where the station lacks the permission to mount it. A file already on the device is never overwritten: a name that is taken gets `-1`, `-2`. A device the station mounted itself is unmounted again before the save reports success, so it can be pulled straight away; one that something else mounted stays mounted. After **Save** the dialog shows what the save is doing, then what happened and one next step, as the Operator Screen does; closed while it runs, the save carries on, and the button shows it again. Only one save runs at a time, from here or from **Export Diagnostics File for Support** in the Operator Screen's Settings menu.

- **Test peer connectivity** – probes every known station on its advertised port and displays a small results table. A green chip means the station responded with an HTTP status and a round-trip time; a red chip means it was unreachable, with a reason. If the table shows "No peers known yet", wait for discovery or check the **Beacon receiver** card above.

- **Restart application** – restarts the OpenFollow process (not the operating system). A confirmation prompt appears first; the page reloads automatically once the station comes back. Use this after changing a setting that requires a restart, such as the web port or Person Detection engine.
