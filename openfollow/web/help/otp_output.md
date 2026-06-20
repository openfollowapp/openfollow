# OTP Output

> OTP output is currently experimental. Test thoroughly with your receiver before relying on it in a show.

Sends tracked marker positions as ANSI E1.59 Object Transform Protocol (OTP) multicast UDP packets, running in parallel with any other active outputs. OTP receivers pick up each controlled marker as an OTP point – marker ID 1 appears as OTP point 1, and so on.

## OTP Network

- **Enabled** – master toggle for the OTP transmitter. Off by default; no packets are sent while unchecked.
- **System Number** – OTP system identifier, 1 – 200. It also determines the multicast addresses packets are sent to (see **Multicast addresses**); change it to move OTP traffic onto a different multicast group. Default: `1`.
- **Multicast addresses** – read-only display of the two addresses derived automatically from System Number, per ANSI E1.59 Table 15-19. **Transform** carries the position data (e.g. `239.159.1.1` for System Number 1); **Advertisement** carries discovery packets (`239.159.2.1`). Adjust System Number to change the group.
- **UDP Port** – port the transmitter binds to and sends from, 1–65535. Default: `5568`.
- **Priority** – OTP priority value sent in each packet, 0 – 200. When multiple OTP sources are present, receivers use priority to resolve conflicts – higher values win. Default: `100`.
- **Source Interface (optional)** – selects which network interface the multicast packets are sent from, pinned by name (`eth0` / `wlan0`) like the PSN source interface, so the pin survives a DHCP lease change. Leave blank (Auto) on a single-NIC station; on a host with both Ethernet and Wi-Fi, pin it to the interface on the show network so packets reach the right subnet. A pinned interface that's down falls back to the primary interface. Click **Scan** to refresh the detected interfaces.

## Saving

- **Save** – writes the current settings to disk and applies them live. OTP output starts or restarts immediately after saving if **Enabled** is checked.
