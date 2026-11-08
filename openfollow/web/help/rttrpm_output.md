# RTTrPM Output

> RTTrPM output is experimental. Confirm compatibility with your receiver before a live show.

Sends marker positions as RTTrPM unicast UDP datagrams, for immersive audio engines and other show-control systems.

- **RTTrPM output** – master toggle.
- **Destination Host** – IP address or hostname of the receiver. Default: `127.0.0.1`.
- **UDP Port** – 1–65535. Default: `36700`.
- **Send Rate (FPS)** – packets per second, 1–240. Default: `60`.
- **Context** – a 32-bit integer (0–4294967295) in every packet, to tell this station's stream apart at a shared receiver. Default `0`.
- **Source Interface** – read-only; set under **General → Network Interface Assignment**. Not shown in Simple network mode. A pinned interface that is down stops RTTrPM.

**Save** writes the settings; unsaved changes are lost on reload.
