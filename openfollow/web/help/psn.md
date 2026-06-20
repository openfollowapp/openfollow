# PSN Output

Configures the PosiStageNet (PSN) multicast stream that carries live marker positions to consoles and media servers on your show network. Every controlled marker is broadcast at 60 fps (data packets) and 1 fps (info packets) on UDP port 56565.

## Network Identity

- **PSN System Name** – the name this station advertises to PSN receivers in the 1 fps info packet. Read-only here; change it under **General → Station Settings**.

- **PSN Multicast IP** – the multicast group the stream is sent to. Standard PSN group is `236.10.10.10`; change only for a non-standard group or to isolate multiple PSN sources on the same VLAN.

- **PSN Network Interface** – pins the transmitter to a specific interface (for example `eth0` or `wlan0`). Leave blank to auto-select the primary outbound interface. With both Ethernet and Wi-Fi active, pinning to the wired interface is strongly recommended so multicast doesn't leave on the wrong NIC. Press **Scan** to refresh the interface list.

> On managed switches, PSN multicast requires IGMP snooping with a querier active on the relevant VLAN. If a console can't see the stream, verify the switch fabric isn't silently dropping multicast to the `236.10.10.10` group.

**Save** – writes the multicast IP and interface selection to disk and applies them to the running stream immediately. No restart needed.
