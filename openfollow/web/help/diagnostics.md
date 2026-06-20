# Diagnostics

Live runtime health for the web server, peer discovery, and logging. Use this section to spot a sick subsystem at a glance and to download or view the information needed for a support report.

The section is inside the Overview tab and refreshes every five seconds.

## Live summary cards

Four cards across the top of the section. Each card shows a status chip and a small set of metrics.

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

### Logs

Tells you where log lines are coming from. Normally this reads **journalctl** (the system journal, preferred). If the system journal isn't reachable, it falls back to **ring** – an in-memory log buffer covering the current session.

> If journalctl is expected but unavailable, a yellow warning banner appears above the cards explaining why.

## Bundle & tools

Four actions sit below the cards.

- **Download diagnostics bundle** – produces a single UTF-8 text file and saves it in your browser. The bundle is plain-text, easy to skim, and contains everything needed to triage a misbehaving installation: web-server and discovery state, the full redacted configuration, recent errors, a log tail, runtime and dependency versions, OS and hardware details, network interfaces, USB device tree, and more. Your web PIN is always replaced with `***`; auth signature values are stripped from log lines. Review the bundle before sharing publicly, as it includes IPs, hostnames, and USB descriptors as-is.

- **Test peer connectivity** – probes every known station on its advertised port and displays a small results table. A green chip means the station responded with an HTTP status and a round-trip time; a red chip means it was unreachable, with a reason. If the table shows "No peers known yet", wait for discovery or check the **Beacon receiver** card above.

- **Show recent log tail (last 100 lines)** – pulls the last 100 lines from the live log source and displays them inline. The first line identifies the source. Auth signatures are redacted before display.

- **Restart application** – restarts the OpenFollow process (not the operating system). A confirmation prompt appears first; the page reloads automatically once the server comes back. Use this after changing a setting that requires a restart, such as the web port or Person Detection engine.
