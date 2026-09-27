# Station Network

A live view of every OpenFollow station discovered on your LAN. Confirm all stations are online and reachable before a show.

Each row is one station – this one or a peer discovered by multicast.

- **Status indicator** – a green dot = online and responding; a red ring = seen previously but not currently reachable. The row takes the same colour, and this station's row is blue.
- **Station name** – the name set under General → Station Settings. This station's row is labelled *(this station)*.
- **Address** – the IP address and web port the station serves on (e.g. `192.168.1.42:80`). Open it in a browser to reach that station's web interface.

> If no peers appear, check that the other OpenFollow instances are running and on the same network segment. Multicast peer discovery does not cross router hops or VLAN boundaries.

The panel refreshes automatically every 5 seconds. Use **Refresh** for an immediate update – e.g. right after bringing a second station online.
