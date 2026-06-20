# Server Network

A live view of every OpenFollow station discovered on your LAN. Confirm all stations are online and reachable before a show.

Each row is one station – this server or a peer discovered by multicast.

- **Status indicator** – filled circle (●) = online and responding; open circle (○) = seen previously but not currently reachable.
- **Station name** – the name set under General → Station Settings. This server's row is labelled *(this server)*.
- **Address** – the IP address and web port the station serves on (e.g. `192.168.1.42:80`). Open it in a browser to reach that station's web interface.

> If no peers appear, check that the other OpenFollow instances are running and on the same network segment. Multicast peer discovery does not cross router hops or VLAN boundaries.

The panel refreshes automatically every 5 seconds. Use **Refresh** for an immediate update – e.g. right after bringing a second station online.
