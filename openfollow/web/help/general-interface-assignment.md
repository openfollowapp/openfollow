# Interface Assignment

Which network each function uses. **Network Settings** above answers "what address does this adapter have"; this panel answers "which adapter does each function go out on".

Useful when lighting, video, and management traffic live on separate networks – a dedicated adapter each, or tagged VLANs on one Ethernet run. Pinning keeps each protocol on its intended network: no PSN leaking onto the office LAN, no need for one flat network just because the tracker only binds one way.

## How a row is resolved

Interfaces are pinned **by name** (`eth0`, `wlan0`, `eth0.10`), not by IP address, so a pin survives a DHCP renewal or a venue change. The **Address** column shows the address that name currently resolves to, so you can see where a function will actually send before you save.

- **Station default** – the interface everything else falls back to. Leave it on `Auto-detect` and the system picks the primary outbound adapter.
- **Follow station interface** – the default for every other row. That row uses whatever Station default resolves to, so on a single-adapter station you never have to touch this panel.
- A specific interface – that function uses it regardless of what the station default is.

If a pinned interface is down or missing, the function falls back to the station interface rather than going silent, and the log records that the pin wasn't honoured. A stale pin degrades output onto the wrong network; it never stops it.

## Rows that can't be pinned

**PSN in / out** and **Discovery / marker sync** always follow the station interface and are shown read-only. They carry this station's identity on the network – the address other stations and consoles see it at – so splitting them from the station default would mean the box advertised one address and answered on another.

## Saving

Save applies immediately to the running station. PSN, OTP, and the other data planes rebind their sockets in place – no restart, and no interruption to anything on an interface you didn't change.

**Scan** re-reads the adapter list from the system. Use it after plugging in a USB Ethernet adapter or creating a VLAN so the new interface appears in the dropdowns.
