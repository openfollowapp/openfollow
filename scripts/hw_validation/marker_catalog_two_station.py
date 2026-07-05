#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Two-station marker-catalog conflict regression check (clock skew).

Reproduces the field bug where a clock-ahead peer's stale catalog entry reverted
a fresh marker rename. The catalog resolves conflicts by a Lamport logical clock
(not wall time), so a rename must win even when a peer's clock runs ahead.

Runs from a WORKSTATION (not on the Pis). Requirements:
  - HTTP reachability to both stations' web UIs (default port 80);
  - passwordless SSH (key auth) to both stations as a sudo-capable user
    (used only to pause NTP + step station B's clock, then restore it).

Scenario:
  1. Pause ``systemd-timesyncd`` on B and set its clock ~1 h ahead, so B's
     write carries a far-future ``updated_at``.
  2. B edits the marker -> stale name + future timestamp; it syncs to A.
  3. A (normal clock) renames the marker.
  4. Assert BOTH stations converge on A's name and hold it (no revert).
  5. Restore B's clock and re-enable NTP (always, via ``finally``).

With the wall-clock code this FAILS (B rejects A's rename, keeping the stale
name); with the logical-clock fix it PASSES. Exits 0 (PASS) / 1 (FAIL).

NOT part of ``make ci`` -- it needs two real devices, SSH, and clock control.

    python3 scripts/hw_validation/marker_catalog_two_station.py \\
        --a 192.168.1.10 --b 192.168.1.11 \\
        --ssh-user openfollow --ssh-key ~/.ssh/openfollow_pi
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request


def build_ssh_base(user: str, key: str) -> list[str]:
    return [
        "ssh",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "IdentityAgent=none",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "-i",
        os.path.expanduser(key),
        f"{user}@__HOST__",
    ]


def ssh(ssh_base: list[str], host: str, cmd: str) -> subprocess.CompletedProcess[str]:
    argv = [a.replace("__HOST__", host) for a in ssh_base]
    return subprocess.run([*argv, cmd], capture_output=True, text=True, timeout=30)


def http(ip: str, path: str, method: str = "GET", body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://{ip}{path}", data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310 - LAN HTTP to a known host
        return resp.status, json.loads(resp.read().decode())


def get_entry(ip: str, mid: int) -> dict | None:
    _, cat = http(ip, "/api/markers/catalog")
    for entry in cat.get("entries", []):
        if entry["id"] == mid:
            return entry
    return None


def wait_for(ip: str, mid: int, predicate, timeout: float, label: str) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = get_entry(ip, mid)
        if last is not None and predicate(last):
            return last
        time.sleep(1.0)
    raise AssertionError(f"timeout waiting for {label} on {ip}; last entry={last}")


def run(args: argparse.Namespace) -> int:
    ssh_base = build_ssh_base(args.ssh_user, args.ssh_key)
    token = str(int(time.time()))
    name_b = f"OwnedByB-{token}"
    name_a = f"RenamedByA-{token}"
    failures: list[str] = []

    baseline = get_entry(args.a, args.marker)
    print(f"[setup] marker {args.marker} baseline: {baseline}")
    if baseline is None:
        print(f"[setup] marker {args.marker} not present on A; create it in the catalog first.")
        return 1

    try:
        # 1. Pause NTP on B and skew its clock ahead.
        print(f"[skew] pausing timesyncd on B and stepping clock +{args.skew_seconds}s")
        ssh(ssh_base, args.b, "sudo systemctl stop systemd-timesyncd")
        ssh(ssh_base, args.b, f"sudo date -s \"$(date -d '+{args.skew_seconds} seconds')\"")
        b_epoch = int(ssh(ssh_base, args.b, "date +%s").stdout.strip())
        skew = b_epoch - int(time.time())
        print(f"[skew] B clock is now {skew}s ahead of this host")
        if skew < args.skew_seconds - 120:
            failures.append(f"clock skew did not take effect (only {skew}s)")
            return _finish(failures)

        # 2. B (clock-ahead) writes the entry -> stale name, future updated_at.
        st, resp = http(args.b, f"/api/markers/catalog/{args.marker}", "PUT", {"name": name_b, "color": "#3366ff"})
        print(f"[B edit] PUT -> {st} {resp.get('entry', {}).get('name')}")
        assert st == 200, f"B PUT returned {st}"

        # 3. B's entry must reach A carrying a future updated_at (skew confirmed).
        ent = wait_for(args.a, args.marker, lambda e: e["name"] == name_b, args.timeout, "B's edit to reach A")
        future_by = ent["updated_at"] - int(time.time())
        print(f"[A sees B] name={ent['name']} updated_at is {future_by:.0f}s in the future")
        if future_by < args.skew_seconds - 300:
            failures.append("B's entry on A is not far-future -> bug condition not set up")
            return _finish(failures)

        # 4. A (normal clock) renames the marker.
        st, resp = http(args.a, f"/api/markers/catalog/{args.marker}", "PUT", {"name": name_a, "color": "#22cc55"})
        print(f"[A edit] PUT -> {st} {resp.get('entry', {}).get('name')}")
        assert st == 200, f"A PUT returned {st}"

        # 5. Both stations must converge on A's name and stay there.
        wait_for(args.a, args.marker, lambda e: e["name"] == name_a, args.timeout, "A keeps its rename")
        wait_for(args.b, args.marker, lambda e: e["name"] == name_a, args.timeout, "B adopts A's rename")
        print("[converged] both stations show A's rename; holding 12s to catch a revert...")
        time.sleep(12)
        final_a = get_entry(args.a, args.marker)
        final_b = get_entry(args.b, args.marker)
        print(f"[final] A={final_a['name']!r}  B={final_b['name']!r}")
        if final_a["name"] != name_a:
            failures.append(f"A reverted: {final_a['name']!r} != {name_a!r}")
        if final_b["name"] != name_a:
            failures.append(f"B reverted: {final_b['name']!r} != {name_a!r}")

    except AssertionError as exc:
        failures.append(str(exc))
    finally:
        print("[restore] restoring B clock + re-enabling timesyncd")
        ssh(ssh_base, args.b, f"sudo date -s @{int(time.time())}")
        ssh(ssh_base, args.b, "sudo systemctl start systemd-timesyncd")
        # Reset the marker to its baseline label so the catalog is left tidy.
        try:
            http(
                args.a,
                f"/api/markers/catalog/{args.marker}",
                "PUT",
                {"name": baseline["name"], "color": baseline["color"]},
            )
        except OSError as exc:
            print(f"[restore] marker reset skipped: {exc}")

    return _finish(failures)


def _finish(failures: list[str]) -> int:
    print()
    if failures:
        print("RESULT: FAIL")
        for f in failures:
            print("  -", f)
        return 1
    print("RESULT: PASS - rename held on both stations despite the clock skew")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--a", required=True, help="station A address (edits with a normal clock)")
    parser.add_argument("--b", required=True, help="station B address (clock stepped ahead)")
    parser.add_argument("--ssh-user", default="openfollow", help="SSH user on both stations (default: openfollow)")
    parser.add_argument("--ssh-key", default="~/.ssh/openfollow_pi", help="SSH private key path")
    parser.add_argument("--marker", type=int, default=1, help="marker id to rename (must exist in the catalog)")
    parser.add_argument("--skew-seconds", type=int, default=3600, help="how far ahead to step B's clock")
    parser.add_argument("--timeout", type=float, default=35.0, help="per-step sync timeout (seconds)")
    return run(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
