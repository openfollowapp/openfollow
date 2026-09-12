# Interface Assignment

Which network each function uses. **Network Settings** above answers "what address does this adapter have"; this panel answers "which adapter does each function go out on".

Useful when lighting, video, and management traffic live on separate networks – a dedicated adapter each, or tagged VLANs on one Ethernet run. Pinning keeps each protocol on its intended network: no PSN leaking onto the office LAN, no need for one flat network just because the tracker only binds one way.

## How a row is resolved

Interfaces are pinned **by name** (`eth0`, `wlan0`, `eth0.10`), not by IP address, so a pin survives a DHCP renewal or a venue change. The **Address** column shows the address that name currently resolves to, so you can see where a function will actually send before you save.

- **Station default** – the interface everything else falls back to. Leave it on `Auto-detect` and the system picks the primary outbound adapter.
- **Follow station interface** – the default for every other row. That row uses whatever Station default resolves to, so on a single-adapter station you never have to touch this panel.
- A specific interface – that function uses it regardless of what the station default is.

## When a configured interface is unavailable

A function stays on the interface you gave it, always. If that interface has no address – cable out, switch port down, VLAN gone – the function **stops** and the Address column says so. It does not move to another interface.

That is deliberate. During a show, output that has stopped is something you can see and diagnose; output that quietly reappeared on the office LAN is not. It also means nothing this station sends can end up on a network you didn't choose.

The function resumes on its own as soon as the interface has an address again – no restart, no re-save. The station checks about once a second, so a cable going back in recovers within a moment. Nothing about your configuration changes while the interface is away.

While a function is stopped, the on-screen display lists it under **Network**, next to the IP address. That matters when the interface that went away is the one carrying this web page: the screen on the device is the only place left to look.

The **address** is allowed to change. If the interface is on DHCP and comes back with a different address than before, that is normal and the function follows it. Only the interface itself is fixed.

## Rows that can't be pinned

**PSN in / out** and **Discovery / marker sync** always follow the station interface and are shown read-only. They carry this station's identity on the network – the address other stations and consoles see it at – so splitting them from the station default would mean the box advertised one address and answered on another.

Following the station interface includes stopping with it. When the station interface has no address, peer discovery and marker-name sync go quiet until it returns, the same as every pinned row above – a station that kept announcing itself would put its name, version and web address on a network you did not choose. Unless you have pinned it yourself, the web UI stays reachable on every interface throughout, so the station is still there to browse to; it just stops appearing in other stations' peer lists.

## Web UI

This page. Left blank it answers on every interface, which is what you want on almost every station – it is how you reach the box, not something the show depends on.

Pin it when management traffic has to stay off a show network: with the row set to one interface, the web UI answers only at that interface's address and is simply not there on the others.

This is the one row that does **not** stop when its interface goes away. It falls back to answering everywhere and says so both at the top of this panel and on the station's own screen, which lists the addresses that really reach it rather than the ones the pin asked for. Every other function going quiet is something you can diagnose from another station; a config page that went quiet would leave nobody able to undo the setting that did it.

Two things stay true whichever way this row is set. The screen on the station always reaches the UI, so the built-in browser keeps working. And the station's own **Network** screen, reached with the Settings key, can put the UI back on every interface without a working web page – it lists the addresses that reach the station and offers **Serve web UI on all interfaces**. That is the way back if you pin this row to the wrong interface.

Changing this row takes effect on restart: the web UI cannot move the socket it is answering your request on. The panel offers **Save & Restart** while the saved pin and the running one differ, and tells you the address to use afterwards.

For the same reason this row is the one exception to *the address is allowed to change*, above. The data planes re-resolve their interface and follow a new lease on their own; the web UI holds the address it opened with until the next restart. If the pinned interface is on DHCP and its address moves, the UI keeps answering at the old one until you restart the station. The station's own **Network** screen says so in as many words, listing the addresses that really reach it and naming the bind that no longer does.

If that matters for a venue, give the interface a static address, or leave this row blank so the UI answers everywhere.

Note that this row moves the web UI only. `Station default` still decides the address this station is *known* by – what appears in other stations' peer lists and in PSN.

## USB Ethernet adapters

A USB adapter is named after its own hardware address, so it appears as something like `enx88a29edf04e3` rather than `eth1`. The name is long, but it belongs to that one physical adapter and stays the same wherever it is plugged in and whatever else is fitted.

That matters because a plain `eth1` is handed out in the order adapters are found at boot, not by which adapter it is. With two fitted, `eth1` and `eth2` can trade places after a restart, and since a pin stores the name, the function would carry on sending to a name that now means the other adapter. Nothing looks wrong in that state, which is why the naming is worth the ugliness.

Replacing a failed adapter gives you a new name, so re-pick the affected rows. A row pinned to an adapter that is no longer present stops and says so rather than moving to another one.

A newly plugged adapter keeps whatever name it already had until it is unplugged and back in, or the station restarts.

## Saving

Save applies immediately to the running station. PSN, OTP, and the other data planes rebind their sockets in place – no restart, and no interruption to anything on an interface you didn't change. The **Web UI** row is the exception and waits for a restart, as described above.

**Scan** re-reads the adapter list from the system. Use it after plugging in a USB Ethernet adapter or creating a VLAN so the new interface appears in the dropdowns.
