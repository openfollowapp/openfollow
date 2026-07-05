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
