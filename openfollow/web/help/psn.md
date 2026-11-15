# PSN Output

Sends live marker positions as a PosiStageNet (PSN) multicast stream to consoles and media servers: data packets at 60 fps and info packets at 1 fps, on UDP port 56565.

## Network Identity

- **PSN System Name** – the name advertised to receivers. Read-only; change it under **General → Station Settings**.
- **PSN Multicast IP** – the group the stream is sent to. Standard is `236.10.10.10`; change it only for a non-standard group.
- **Network Interface** – read-only; shows the interface PSN is bound to. Set it under **General → Network Interface Assignment** (Advanced network mode). PSN follows the *Station default* row. Not shown in Simple mode.

On managed switches, PSN multicast needs IGMP snooping with a querier on the VLAN. If a console cannot see the stream, check that the switch is not dropping the group.

**Save** applies the multicast IP immediately; no restart needed.

## Tracker Status

Each tracker carries a **status** from `0.0` to `1.0`:

- **Tracking off and AI Assisted** – `1.0`.
- **Fully Automatic** – how much detection vouches for the position. A sighting at the **Detection sensitivity** threshold reads `0.5`, a perfect score `1.0`. If the person is lost or video stops, it fades to `0.0` across the **Grace period** and stays there.
- **Stale** – `0.0` when this station has not updated the marker for one second.

The **timestamp** stops advancing when the marker stops being updated. grandMA3 colours a tracker by packet arrival, not by status.
