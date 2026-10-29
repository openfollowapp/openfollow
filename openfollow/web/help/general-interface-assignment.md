# Network Interface Assignment

Which network each function goes out on, in **Advanced** network mode. In Simple mode the station interface carries every function and this panel is not shown. Interfaces are pinned by name, not by IP address, so a pin survives a DHCP renewal. The **Address** column shows the address that name currently resolves to.

- **Station default** – the interface every blank row falls back to. `Auto-detect` leaves it to the system.
- **Follow station default interface** – what a blank row means. With Station default on `Auto-detect` nothing is pinned.
- A specific interface – that function uses it, whatever Station default is.

**PSN in / out** and **Discovery / marker sync** always follow Station default and are read-only.

## When an interface is down

A pinned function stops; it never moves to another interface. The Address column shows a red **Interface down**, or **Not connected** when the adapter is unplugged. The Operator Screen shows the function as a red row in the top-right corner. The function resumes by itself within a moment of the interface regaining an address. A DHCP address may change; the function follows it.

## Rows

- **OSC input** – the interface for the OSC multicast subscription. It does nothing unless a multicast group is set under OSC Input, and it does not change which addresses OSC is accepted at (use **Allowed sender IPs**). A pinned interface without an address holds no subscription.
- **RTTrPM output** and each **OSC Destination** – the interface those messages leave from. A destination the interface cannot reach gets nothing. A destination on this station reads `Loopback` or `This station`.
- **Video input** – the interface the camera is reached through. `Interface down` means the pinned interface has no address; `Wrong interface` means the camera is reached through another one. The video returns by itself. NDI, Pi Camera, USB cameras and the Media Gallery are not pinned.
- **Web UI** – blank answers on every interface. Pinned, it answers only at that interface's address. If that interface goes away it falls back to answering everywhere. The row takes effect only after a restart: **Save**, then the red **Restart OpenFollow**. If you pin it to the wrong interface, **Serve web UI on all interfaces** on the station's own **Network** screen puts it back.

Pins are left out of a config export.

## USB Ethernet adapters

A USB Ethernet adapter is named after its hardware address, like `enx88a29edf04e3`, so the name stays with that adapter. A replacement adapter gets a new name: re-pick the rows. Give each adapter a label under **Network Interface Settings** to tell long names apart.

## Saving

**Save** applies at once; outputs rebind in place and **Video input** reconnects. **Scan** re-reads all adapters.
