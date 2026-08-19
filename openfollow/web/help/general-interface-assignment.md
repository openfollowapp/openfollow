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

Nothing about your configuration changes while the interface is away. Save the panel again once it is back to start the function on it.

The **address** is allowed to change. If the interface is on DHCP and comes back with a different address than before, that is normal and the function follows it. Only the interface itself is fixed.

## Rows that can't be pinned

**PSN in / out** and **Discovery / marker sync** always follow the station interface and are shown read-only. They carry this station's identity on the network – the address other stations and consoles see it at – so splitting them from the station default would mean the box advertised one address and answered on another.

## USB Ethernet adapters

A USB adapter is named after its own hardware address, so it appears as something like `enx88a29edf04e3` rather than `eth1`. The name is long, but it belongs to that one physical adapter and stays the same wherever it is plugged in and whatever else is fitted.

That matters because a plain `eth1` is handed out in the order adapters are found at boot, not by which adapter it is. With two fitted, `eth1` and `eth2` can trade places after a restart, and since a pin stores the name, the function would carry on sending to a name that now means the other adapter. Nothing looks wrong in that state, which is why the naming is worth the ugliness.

Replacing a failed adapter gives you a new name, so re-pick the affected rows. A row pinned to an adapter that is no longer present stops and says so rather than moving to another one.

A newly plugged adapter keeps whatever name it already had until it is unplugged and back in, or the station restarts.

## Saving

Save applies immediately to the running station. PSN, OTP, and the other data planes rebind their sockets in place – no restart, and no interruption to anything on an interface you didn't change.

**Scan** re-reads the adapter list from the system. Use it after plugging in a USB Ethernet adapter or creating a VLAN so the new interface appears in the dropdowns.
