# Hardware validation tooling (two-Pi)

End-to-end validation of network-driven features across two devices: a
**device under test (DUT)** running the deployed code, and a **companion**
device that drives traffic at it. The seed here validates the OSC
**operator-message** feature (#330) over unicast **and** multicast; it's meant
to grow into a fuller two-Pi validation suite (see the tracking issue).

## Layout

| Script | Runs on | Purpose |
| --- | --- | --- |
| `operator_message_receiver.py` | DUT | Drives the deployed `OscService` + ingest adapter + store on a real listener; records every accept/clear to JSON. Writes a readiness marker once bound. |
| `operator_message_sender.py` | companion | Fires the operator-message battery (unicast + multicast). |
| `analyze_results.py` | anywhere | Asserts every functionality from the receiver JSON; exits non-zero on failure. |
| `raw_udp_probe.py` | both | Dependency-free UDP reachability preflight – tells a network drop apart from an app bug. |
| `psn_packet_size_probe.py` | DUT | Builds real multi-tracker PSN datagrams with the deployed encoder, round-trips them through a loopback socket at 1500 vs 65535, and guards the receiver's `recvfrom` buffer (#463). |
| `osc_socket_options_probe.py` | DUT | Builds clients via the deployed `OscService._make_client` and asserts the broadcast/multicast socket options (#482). |
| `marker_catalog_two_station.py` | workstation | Reproduces the clock-skew marker-rename revert across two stations: steps station B's clock ahead, renames on A, asserts the rename holds on both. Exits `0` (PASS) / `1` (FAIL). |
| `multi_interface_two_station.py` | workstation | Drives the multi-interface feature end-to-end: creates a tagged VLAN from the web UI, asserts it becomes a pinnable netdev, exercises the delete guards, then proves PSN leaves tagged on the pinned NIC and **stops** when its interface is unavailable. Exits `0` (PASS) / `1` (FAIL). |
| `vlan_tag_probe.py` | DUT | Dependency-free raw-socket capture reporting the 802.1Q tag on each frame. A station ships no `tcpdump` and no uplink to install one. |

## DUT-local probes (no companion)

`psn_packet_size_probe.py` and `osc_socket_options_probe.py` validate OS-level
socket behaviour that fake-socket unit tests can't reach, entirely on the DUT –
no second device, no service stop. Each exits `0` (PASS) / `1` (FAIL):

```sh
# On the DUT, from the repo root
poetry run python scripts/hw_validation/psn_packet_size_probe.py
poetry run python scripts/hw_validation/osc_socket_options_probe.py
```

- **PSN** – a PSN data packet crosses 1500 B at ~15 trackers; a `recvfrom(1500)`
  receiver then silently drops the tail markers every frame. The probe confirms
  the deployed receiver's buffer covers a realistic packet (40 trackers ≈ 2.1 kB).
- **OSC** – a plain `SimpleUDPClient` to `255.255.255.255` raises `EACCES`; the
  probe confirms the deployed `_make_client` sets `SO_BROADCAST` (and reports the
  multicast TTL/loop) so broadcast/multicast rows actually transmit.

## Two-station marker-catalog conflict (clock skew)

`marker_catalog_two_station.py` runs from a **workstation** (not the Pis) and
drives both stations over HTTP + SSH. It reproduces the field bug where a
clock-ahead peer's stale catalog entry reverted a fresh marker rename: it pauses
NTP and steps station B's clock ~1 h ahead so B's write carries a far-future
`updated_at`, renames the marker on A (normal clock), then asserts both stations
converge on A's name and hold it. B's clock + NTP are restored in a `finally`.

The catalog resolves conflicts by a Lamport logical clock, so the rename wins
despite the skew (on the old wall-clock code the test FAILS — B keeps the stale
name). Needs HTTP reachability to both web UIs and passwordless SSH (key auth) to
both as a sudo-capable user. The marker id must already exist in the catalog.

```sh
# From a workstation on the same LAN
python3 scripts/hw_validation/marker_catalog_two_station.py \
    --a <STATION_A_IP> --b <STATION_B_IP> \
    --ssh-user openfollow --ssh-key ~/.ssh/openfollow_pi
```

## Running

The receiver imports the installed package, so run it from the repo root on the
DUT. If you bind the device's configured OSC port (8765), stop the app first so
the port is free:

```sh
# On the DUT
sudo systemctl stop openfollow.service
cd /home/openfollow/openfollow
python3 scripts/hw_validation/operator_message_receiver.py \
    --port 8765 --group 239.20.20.20 --controlled 3,4 --window 15 &
# wait for the readiness marker (/tmp/of_validate_ready) before sending

# On the companion Pi (after readiness)
cd /home/openfollow/openfollow
python3 scripts/hw_validation/operator_message_sender.py --host <DUT_IP> --port 8765

# Back on the DUT once the window closes
python3 scripts/hw_validation/analyze_results.py /tmp/of_validate_out.json
sudo systemctl start openfollow.service
```

The receiver/sender/analyzer need `pythonosc`, which the project venv provides
(`poetry run python ...`). `raw_udp_probe.py` is pure stdlib.

## What it checks

Unicast + multicast receive; broadcast routing (markerId 0); marker routing
accept (controlled ids) vs drop (uncontrolled); negative-id and empty-message
drops; the `info`/`seconds` fields; clear-by-marker and clear-all.

Overlay-only concerns (title bar, compact layout, `+N more` overflow, top/bottom
placement) are render-layer, not OSC-driven, and are covered by the unit suite
plus on-screen checks – not by this network harness.

## Known caveat: port reachability

On the test bench, UDP to an **arbitrary** port (8790) never reached the DUT
from either the companion Pi or a laptop, while the configured OSC port **8765**
worked – with **no firewall** on the DUT (nft/ufw/firewalld inactive, iptables
absent). Likely switch/AP isolation of non-service ports. Until that's
understood, **bind the harness to 8765** (stop the app first), and run
`raw_udp_probe.py` as a preflight if a run records zero packets:

```sh
# DUT
python3 scripts/hw_validation/raw_udp_probe.py listen --port 8765
# companion
python3 scripts/hw_validation/raw_udp_probe.py send --host <DUT_IP> --port 8765
```

## Multi-interface networking (two stations)

`multi_interface_two_station.py` runs from a **workstation** and drives the DUT
over HTTP + SSH. It is the hardware half of issue #50 - the parts a fake-socket
test cannot reach, because they depend on real frames on a real wire.

```sh
python3 scripts/hw_validation/multi_interface_two_station.py \
    --dut <DUT_IP> --parent eth0 --vlan-id 10 \
    --ssh-user openfollow --ssh-key ~/.ssh/openfollow_pi --pin <WEB_PIN>
```

The two load-bearing checks:

- **Traffic separation** - with PSN pinned to `eth0.10`, every PSN frame on the
  parent carries `vlan 10` and none from this station is untagged. If PSN
  appears untagged on the office LAN the feature has failed at its purpose.
- **Fail closed** - pinning to an interface that has no address must make PSN
  **stop**, closing its sockets rather than rebinding. On a show a dead output
  is diagnosable and a misrouted one is not.

### Four things that make this harder than it looks

**A trunk port is not needed for the tagging check.** Tagging is done by the
host: sending via `eth0.10` makes the kernel add the VLAN header before the
frame reaches `eth0`, so capturing on the parent proves the pin is honoured
even on an access port. Only end-to-end delivery to a second station needs a
trunk. Capture on the **parent** - the VLAN interface sees its own frames
untagged.

**There is no `tcpdump` on a station**, and the offline contract means a
validation step cannot install one. `vlan_tag_probe.py` is a raw `AF_PACKET`
socket in the bundled venv's Python. A tcpdump-based check reports "no traffic"
on every station, which reads as a pass.

**A VLAN is not pinnable until it has an address.** The interface pickers list
interfaces that *have* an IPv4, so a freshly created VLAN is absent from them
until `Configure` gives it one. The Create banner says so; the script skips the
traffic phases rather than reporting a false failure.

**Do not set the pin through `/api/config/interface_assignment`.**
`psn_source_iface` is device-local, so the JSON section API strips it and
answers `{"success": true}` having written nothing - every later assertion then
measures the old pin. The panel's form POST to `/section/interface_assignment`
is the working path, and the script re-reads the pin to confirm it landed.

### Why the fail-closed check has no cross-station control

Suspending PSN stops the *receiver* as well, which drops the DUT's IGMP
membership for the group. The switch then prunes it from that port, so **every**
station's PSN vanishes from the capture, not just the DUT's. A second station is
therefore not a usable control here; the DUT's own closed send socket plus the
journal line naming the interface are the direct evidence, and that is what the
script asserts.

The script restores what it changed in a `finally`: the PSN pin returns to its
original value and the VLAN it created is deleted. It refuses to start if the
VLAN name already exists, so cleanup can never remove one you rely on.

`--other-nic` and `--companion` are optional; both skip loudly when absent
rather than passing vacuously.
