# Diagnostics

Live runtime health for the web server and peer discovery, and the information for a support report. It refreshes every five seconds while open and in view.

## Live summary cards

Each card shows a status chip and a few metrics.

### Web server

- **Configured** – the web port set in your configuration.
- **Serving on** – the port the server actually bound to, and the one other stations are told. If the configured port was unavailable, OpenFollow falls back to a backup port (8080 or 2010).
- **Uptime** – how long the OpenFollow process has been running.
- The chip reads **OK** when both ports match, **fallback** on a backup port.

### Beacon sender

Health of the outgoing peer-discovery beacon. If it is unhealthy, other stations won't see this one.

- **Last sent** – the most recent outgoing beacon.
- **Errors** – send errors since the process started.
- **Sent count** – beacons sent since the process started.

### Beacon receiver

Health of the incoming listener. If it is unhealthy, this station won't see other stations.

- **Peers seen** – OpenFollow stations currently known on the local network.
- **Last packet** – the most recent received beacon.
- **Packets total** – packets consumed since the process started.

## Bundle & tools

- **Download diagnostics bundle** – saves one plain-text file in your browser, named `ofdiag-<station>-<UTC date and time>.txt`. It captures the video signal, network, configuration, recent errors and a log tail, versions and power or USB faults. Credentials are always redacted. The station keeps its ten newest.
- **Save to USB storage device** – saves the same file to the top level of a USB stick or SD card reader attached to this station. A device that can't be written stays in the list, unavailable, with the reason. A file already on the device is never overwritten. A device the station mounted itself is unmounted again before the save reports success. Only one save runs at a time.
- **Test peer connectivity** – probes every known station and shows a results table. A green chip means it responded, with a round-trip time; a red chip means it was unreachable, with a reason. "No peers known yet" points to the **Beacon receiver** card.
- **Restart application** – restarts the OpenFollow process, not the operating system, after a confirmation. The page reloads once the station is back. Refused while a software update is running.
