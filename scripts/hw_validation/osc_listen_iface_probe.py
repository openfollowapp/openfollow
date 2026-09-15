#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Assert the OSC multicast subscription sits on the pinned interface only.

Runs **on the device**, against the running station, with no dependencies.

``osc.listen_iface`` governs one thing: the interface the inbound listener
takes its ``IP_ADD_MEMBERSHIP`` on. Two properties have to hold together, and
checking either alone passes while the feature is broken:

- the group arrives **only** via the pinned interface, and
- ordinary unicast and subnet broadcast still arrive via **every** interface,
  because the listener binds the wildcard and must.

A socket bound to one address would satisfy the first and fail the second
silently - the join still reports success while no multicast is ever
delivered - which is why the unicast leg is not optional padding.

Writes a marker over OSC and reads the position back through the web API, so
it proves **delivery**, not just what ``/proc/net/igmp`` claims. Each write
uses a coordinate the marker is not already at: a target it happens to hold
reads as delivered no matter what the socket did.

    sudo /opt/openfollow/venv/bin/python osc_listen_iface_probe.py \\
        --marker 301 --pin 0303

Exit code is 0 when every expectation held and 1 when any did not. The marker
is left where the last write put it; point it at a spare id, not one a console
is following.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import socket
import struct
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

CONFIG_PATH = "/var/lib/openfollow/config.toml"
SETTLE_S = 3.0


def interface_addresses() -> dict[str, str]:
    """Every interface carrying an IPv4 address, excluding loopback.

    Read live rather than taken as an argument: a DHCP lease that moves during
    an outage test would otherwise leave the probe sending from an address the
    station no longer has, which fails as ``EADDRNOTAVAIL`` and reads as a
    containment pass.
    """
    out = subprocess.run(["ip", "-4", "-brief", "address"], capture_output=True, text=True, check=True).stdout
    found: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] != "lo":
            found[parts[0]] = parts[2].split("/")[0]
    return found


def membership_interfaces(group: str) -> list[str]:
    """Interfaces the kernel currently holds *group* on, by name."""
    packed = socket.inet_aton(group)
    wanted = "".join(f"{b:02X}" for b in reversed(packed))
    device, holders = "", []
    with open("/proc/net/igmp") as handle:
        for line in handle:
            if line and not line[0].isspace():
                parts = line.split()
                device = parts[1].rstrip(":") if len(parts) >= 2 else device
            elif wanted in line.upper().split()[0:1]:
                holders.append(device)
    return holders


def osc_message(address: str, *values: float) -> bytes:
    """Encode one OSC message with an all-float argument list."""

    def pad(raw: bytes) -> bytes:
        remainder = len(raw) % 4
        return raw if remainder == 0 else raw + b"\x00" * (4 - remainder)

    out = pad(address.encode() + b"\x00") + pad(("," + "f" * len(values)).encode() + b"\x00")
    return out + b"".join(struct.pack(">f", float(v)) for v in values)


class Station:
    """The station's marker state, read back over the web API."""

    def __init__(self, base: str, pin: str, marker: int) -> None:
        self._base, self._marker = base.rstrip("/"), marker
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        if pin:
            body = urllib.parse.urlencode({"pin": pin}).encode()
            try:
                self._opener.open(urllib.request.Request(f"{self._base}/login", data=body), timeout=10).read()
            except urllib.error.HTTPError as exc:
                if exc.code not in (302, 303):
                    raise

    def position(self) -> tuple[float, float] | None:
        raw = self._opener.open(f"{self._base}/api/zones", timeout=10).read()
        for entry in json.loads(raw).get("markers", []):
            if int(entry["id"]) == self._marker:
                return round(float(entry["x"]), 3), round(float(entry["y"]), 3)
        return None


def send(payload: bytes, dest: str, port: int, *, via: str = "", broadcast: bool = False) -> bool:
    """Send one datagram, returning False when the interface cannot source it."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        if via:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(via))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        if broadcast:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for _ in range(3):
            sock.sendto(payload, (dest, port))
            time.sleep(0.1)
        return True
    except OSError as exc:
        print(f"    (cannot send via {via or dest}: {exc})")
        return False
    finally:
        sock.close()


def delivered(station: Station, marker: int, port: int, target: tuple[float, float], **kwargs: object) -> bool:
    """Write *target* and report whether the station's marker got there."""
    if station.position() == target:  # a target it already holds proves nothing
        target = (target[0], target[1] + 0.5)
    payload = osc_message(f"/marker/{marker}", target[0], target[1], 1.5)
    if not send(payload, kwargs.pop("dest"), port, **kwargs):  # type: ignore[arg-type]
        return False
    deadline = time.time() + 2.5
    while time.time() < deadline:
        time.sleep(0.2)
        if station.position() == target:
            return True
    return False


def configured_group() -> str:
    try:
        with open(CONFIG_PATH) as handle:
            for line in handle:
                if line.strip().startswith("multicast_group"):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--marker", type=int, required=True, help="marker id to write (use a spare one)")
    parser.add_argument("--pin", default="", help="web PIN, when one is set")
    parser.add_argument("--port", type=int, default=8765, help="OSC listener port")
    parser.add_argument("--group", default="", help="multicast group (default: read from config.toml)")
    parser.add_argument("--web", default="http://127.0.0.1", help="station web UI base URL")
    args = parser.parse_args()

    group = args.group or configured_group()
    if not group:
        print("FAIL: no multicast group configured - the pin governs nothing")
        return 1

    station = Station(args.web, args.pin, args.marker)
    if station.position() is None:
        print(f"FAIL: marker {args.marker} is not registered on this station")
        return 1

    addresses = interface_addresses()
    holders = membership_interfaces(group)
    print(f"group {group} held on: {', '.join(holders) or 'nothing'}")
    print(f"interfaces: {', '.join(f'{n}={a}' for n, a in addresses.items())}\n")

    failures: list[str] = []
    step = 0.0
    for name, address in addresses.items():
        step += 1.0
        expected = name in holders
        got = delivered(station, args.marker, args.port, (step, 1.0), dest=group, via=address)
        verdict = "ok" if got == expected else "FAIL"
        print(f"  multicast via {name:<18} delivered={str(got):<5} expected={str(expected):<5} {verdict}")
        if got != expected:
            failures.append(
                f"multicast via {name} was {'delivered' if got else 'dropped'} but the membership is "
                f"{'not ' if not expected else ''}on it"
            )

    for name, address in addresses.items():
        step += 1.0
        got = delivered(station, args.marker, args.port, (step, 2.0), dest=address)
        print(f"  unicast to {name:<21} delivered={str(got):<5} expected=True  {'ok' if got else 'FAIL'}")
        if not got:
            failures.append(f"unicast to {address} was dropped - the listener is not bound to every interface")

    if failures:
        print("\nFAIL:")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("\nPASS: the group arrives only where it is pinned, and unicast arrives everywhere")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
