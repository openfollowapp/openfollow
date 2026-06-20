# RTTrPM Output

> RTTrPM output is currently experimental. Confirm compatibility with your specific receiver software before using it in a live show.

Sends tracked marker positions over Real Time Tracking Protocol – Motion (RTTrPM) as unicast UDP datagrams. Use it to feed immersive audio engines or other show-control systems that consume RTTrPM data.

## RTTrPM Network

- **Enabled** – master toggle. Turn on to start sending RTTrPM packets.
- **Destination Host** – IP address of the RTTrPM receiver (e.g. `192.168.1.50`); a hostname is also accepted. Default: `127.0.0.1`.
- **UDP Port** – destination UDP port, 1–65535. Default: `36700`.
- **Send Rate (FPS)** – position packets sent per second, 1–240. Default: `60`. Match it to your receiver's expected update rate; higher rates give smoother motion but increase network load.
- **Context** – a user-defined 32-bit integer (0–4294967295) included in every packet. Use it to distinguish this station's stream when multiple sources feed the same receiver. Leave at `0` if your receiver doesn't use it.

## Saving

- **Save** – write the current settings to disk. RTTrPM output applies immediately when Enabled is checked, but reverts to the previous saved state on reload unless you Save first.
