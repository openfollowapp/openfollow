# PSN Output

Configures the PosiStageNet (PSN) multicast stream that carries live marker positions to consoles and media servers on your show network. Every controlled marker is broadcast at 60 fps (data packets) and 1 fps (info packets) on UDP port 56565.

## Network Identity

- **PSN System Name** – the name this station advertises to PSN receivers in the 1 fps info packet. Read-only here; change it under **General → Station Settings**.

- **PSN Multicast IP** – the multicast group the stream is sent to. Standard PSN group is `236.10.10.10`; change only for a non-standard group or to isolate multiple PSN sources on the same VLAN.

- **Network Interface** – read-only here; it reports which interface PSN is currently bound to. Set it under **General → Interface Assignment**, where every protocol's interface is chosen in one place. PSN follows the *Station default* row: it carries this station's identity on the network, so it uses the same interface as peer discovery and marker sync. With both Ethernet and Wi-Fi active, pinning the station to the wired interface is strongly recommended so multicast doesn't leave on the wrong NIC.

> On managed switches, PSN multicast requires IGMP snooping with a querier active on the relevant VLAN. If a console can't see the stream, verify the switch fabric isn't silently dropping multicast to the `236.10.10.10` group.

**Save** – writes the multicast IP and interface selection to disk and applies them to the running stream immediately. No restart needed.
