# PSN Output

Configures the PosiStageNet (PSN) multicast stream that carries live marker positions to consoles and media servers on your show network. Every controlled marker is broadcast at 60 fps (data packets) and 1 fps (info packets) on UDP port 56565.

## Network Identity

- **PSN System Name** – the name this station advertises to PSN receivers in the 1 fps info packet. Read-only here; change it under **General → Station Settings**.

- **PSN Multicast IP** – the multicast group the stream is sent to. Standard PSN group is `236.10.10.10`; change only for a non-standard group or to isolate multiple PSN sources on the same VLAN.

- **PSN Network Interface** – pins the transmitter to a specific interface (for example `eth0` or `wlan0`). Leave blank to auto-select the primary outbound interface. With both Ethernet and Wi-Fi active, pinning to the wired interface is strongly recommended so multicast doesn't leave on the wrong NIC. Press **Scan** to refresh the interface list.

> On managed switches, PSN multicast requires IGMP snooping with a querier active on the relevant VLAN. If a console can't see the stream, verify the switch fabric isn't silently dropping multicast to the `236.10.10.10` group.

**Save** – writes the multicast IP and interface selection to disk and applies them to the running stream immediately. No restart needed.

## Tracker Status

Every tracker in the data packet carries a **status**, PSN's validity for that position, from `0.0` to `1.0`:

- **Tracking off and AI Assisted** – `1.0`. Your input (or your anchor) drives the position, so it is fully valid.
- **Fully Automatic** – how much detection vouches for the position. A sighting right at the **Detection sensitivity** threshold reads `0.5` and a perfect score `1.0`; a dimmer sighting the tracker still accepts reads below `0.5`. While the tracked person is briefly lost, or the video stops arriving, the status fades to `0.0` across the **Grace period** (never shorter than one detection step, so a slow step is not read as a loss). It stays at `0.0` while nobody is tracked or there is no video, even while you move the marker by hand to cover the gap.
- **Stale** – `0.0` whenever this station has not updated the marker for one second, whatever it would otherwise carry, so a frozen station never advertises a valid position.

Each tracker also carries a **timestamp** that stops advancing when the marker stops being updated. grandMA3 colours a tracker by packet arrival rather than by status; a receiver that does read validity gets the signal above.
