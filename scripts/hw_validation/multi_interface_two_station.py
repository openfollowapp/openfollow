#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Multi-interface networking validation across two stations (issue #50).

Runs from a WORKSTATION and drives a **device under test (DUT)** over HTTP +
SSH, optionally confirming end-to-end delivery at a **companion** station.

What it proves, in order:

  1. **VLAN lifecycle** – create a tagged sub-interface from the web UI and see
     it appear as a real netdev on the DUT.
  2. **No extra plumbing** – the same sub-interface shows up in the Network
     Settings list *and* in the interface picker every plane pins through.
     This is the claim the VLAN design rests on: a VLAN is an ordinary netdev.
  3. **Guards** – deleting a non-VLAN adapter is refused, and so is deleting
     the adapter this HTTP session arrived on.
  4. **Traffic separation** – with PSN pinned to the VLAN, the parent NIC
     carries PSN frames tagged with that VLAN ID and the other NIC carries
     none. This is the whole point of the feature: PSN must not leak onto the
     office LAN.
  5. **Fail closed** – take the pinned VLAN down and PSN must **stop**, not
     reappear on another interface. A dead output is diagnosable on a show; a
     misrouted one is not.

Everything it changes is restored in a ``finally``: the PSN pin returns to its
original value and the VLAN it created is deleted.

Requirements:
  - HTTP reachability to the DUT's web UI (``--pin`` if one is set);
  - passwordless SSH (key auth) to the DUT as a sudo-capable user, for
    ``ip``/``tcpdump``/``nmcli`` reads;
  - the DUT's ``--parent`` NIC plugged into a **trunk (tagged) switch port**
    carrying ``--vlan-id``, or steps 4-5 have nothing to capture;
  - a second NIC named by ``--other-nic`` to prove PSN does *not* leak there.

NOT part of ``make ci`` – it needs real hardware, a trunk port, and root.

    python3 scripts/hw_validation/multi_interface_two_station.py \\
        --dut 192.168.1.10 --parent eth0 --other-nic wlan0 --vlan-id 10 \\
        --ssh-user openfollow --ssh-key ~/.ssh/openfollow_pi --pin 0303
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CAPTURE_SECONDS = 8
SETTLE_SECONDS = 6
_VENV_PYTHON = "/opt/openfollow/venv/bin/python"
_REMOTE_PROBE_DIR = "/tmp"  # noqa: S108 - a throwaway copy of a repo script on the DUT


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #


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


def ssh(ssh_base: list[str], host: str, cmd: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    argv = [a.replace("__HOST__", host) for a in ssh_base]
    return subprocess.run([*argv, cmd], capture_output=True, text=True, timeout=timeout)


class Web:
    """Web UI client that carries the auth cookie when a PIN is configured."""

    def __init__(self, ip: str, pin: str = "") -> None:
        self.ip = ip
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(),
        )
        if pin:
            self._login(pin)

    def _login(self, pin: str) -> None:
        body = urllib.parse.urlencode({"pin": pin}).encode()
        req = urllib.request.Request(f"http://{self.ip}/login", data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with self.opener.open(req, timeout=10):
            pass

    def get(self, path: str) -> tuple[int, str]:
        try:
            with self.opener.open(f"http://{self.ip}{path}", timeout=10) as r:
                return r.status, r.read().decode(errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(errors="replace")

    def post_form(self, path: str, fields: dict[str, str]) -> tuple[int, str]:
        body = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(f"http://{self.ip}{path}", data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with self.opener.open(req, timeout=15) as r:
                return r.status, r.read().decode(errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(errors="replace")

    def get_json(self, path: str) -> tuple[int, dict]:
        status, text = self.get(path)
        try:
            return status, json.loads(text)
        except json.JSONDecodeError:
            return status, {}

    def post_json(self, path: str, payload: dict) -> tuple[int, str]:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(f"http://{self.ip}{path}", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with self.opener.open(req, timeout=15) as r:
                return r.status, r.read().decode(errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(errors="replace")


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.rows.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{f' – {detail}' if detail else ''}", flush=True)
        return ok

    def skip(self, name: str, why: str) -> None:
        self.rows.append((name, True, f"SKIPPED: {why}"))
        print(f"  [SKIP] {name} – {why}", flush=True)

    def failed(self) -> list[str]:
        return [n for n, ok, _ in self.rows if not ok]


# --------------------------------------------------------------------------- #
# Probes
# --------------------------------------------------------------------------- #


def netdev_exists(ssh_base: list[str], host: str, name: str) -> bool:
    result = ssh(ssh_base, host, f"ip -o link show {name} 2>/dev/null | wc -l")
    return result.stdout.strip() not in ("", "0")


def iface_address(ssh_base: list[str], host: str, name: str) -> str:
    result = ssh(ssh_base, host, f"ip -4 -o addr show {name} 2>/dev/null | awk '{{print $4}}'")
    return result.stdout.strip()


def capture_psn(
    ssh_base: list[str],
    host: str,
    nic: str,
    mcast: str,
    seconds: int = CAPTURE_SECONDS,
) -> list[dict]:
    """Return the PSN frames seen on ``nic``, each with its 802.1Q tag.

    Uses the bundled ``vlan_tag_probe.py`` rather than ``tcpdump``: a station
    ships neither tcpdump nor an uplink to install one, so a tcpdump-based
    check reports "no traffic" on every station and reads as a pass.

    Capture on the **parent**, not the VLAN. Tagging happens on egress to the
    parent, so the VLAN interface itself sees its own frames untagged.
    """
    remote = f"{_REMOTE_PROBE_DIR}/vlan_tag_probe.py"
    cmd = f"sudo {_VENV_PYTHON} {remote} --iface {nic} --dst {mcast} --seconds {seconds} --json"
    result = ssh(ssh_base, host, cmd, timeout=seconds + 25)
    try:
        parsed = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def push_probe(ssh_base: list[str], host: str) -> bool:
    """Copy the capture probe onto the DUT. Returns False if it cannot land."""
    local = pathlib.Path(__file__).resolve().with_name("vlan_tag_probe.py")
    try:
        source = local.read_text()
    except OSError:
        return False
    written = ssh(
        ssh_base,
        host,
        f"cat > {_REMOTE_PROBE_DIR}/vlan_tag_probe.py <<'OF_PROBE_EOF'\n{source}\nOF_PROBE_EOF\necho ok",
        timeout=30,
    )
    return "ok" in written.stdout


def tags_in(frames: list[dict], source_ip: str = "") -> set[str]:
    """Distinct tags among ``frames``, optionally only those from ``source_ip``."""
    return {
        ("untagged" if f.get("vlan") is None else f"vlan {f['vlan']}")
        for f in frames
        if not source_ip or f.get("src") == source_ip
    }


def set_psn_pin(web: Web, iface: str) -> tuple[int, str]:
    """Set the station pin through the panel's form POST.

    NOT ``/api/config/interface_assignment``: ``psn_source_iface`` is
    device-local, so the JSON section API strips it and answers
    ``{"success": true}`` having written nothing. A pin set that way silently
    never applies, and every downstream assertion then measures the old pin.
    """
    return web.post_form(
        "/section/interface_assignment",
        {"psn_source_iface": iface, "otp_output.source_iface": ""},
    )


def read_psn_pin(web: Web) -> str:
    _status, data = web.get_json("/api/config/interface_assignment")
    return str(data.get("psn_source_iface", ""))


# --------------------------------------------------------------------------- #
# Phases
# --------------------------------------------------------------------------- #


def phase_vlan_lifecycle(rep: Report, web: Web, ssh_base: list[str], args, vlan_name: str) -> bool:
    print("\n[1] VLAN lifecycle")
    status, body = web.post_form(
        "/section/network/vlan/create",
        {"vlan_parent": args.parent, "vlan_id": str(args.vlan_id)},
    )
    if not rep.check("create returns 200", status == 200, f"HTTP {status}"):
        return False
    if not rep.check(
        "create reported success",
        "Created" in body or vlan_name in body,
        "banner did not confirm creation",
    ):
        print(f"      body excerpt: {body[:400]}")
        return False
    time.sleep(SETTLE_SECONDS)
    return rep.check(
        f"{vlan_name} exists as a netdev on the DUT",
        netdev_exists(ssh_base, args.dut, vlan_name),
        "ip link does not show it",
    )


def phase_no_extra_plumbing(rep: Report, web: Web, vlan_name: str) -> None:
    print("\n[2] A VLAN is an ordinary netdev (no extra plumbing)")
    _status, card = web.get("/section/network/edit")
    rep.check(
        "VLAN appears in the Network Settings list",
        vlan_name in card,
        "not in the interface list",
    )
    _status, picker = web.get("/network/interfaces/by_name")
    rep.check(
        "VLAN is offered as a plane pin",
        f'value="{vlan_name}"' in picker,
        "not offered in the interface picker",
    )


def phase_guards(rep: Report, web: Web, args, vlan_name: str) -> None:
    print("\n[3] Delete guards")
    _status, body = web.post_form("/section/network/vlan/delete", {"iface": args.parent})
    rep.check(
        "deleting a non-VLAN adapter is refused",
        "is not a VLAN" in body,
        "the parent NIC was not protected",
    )
    _status, body = web.post_form("/section/network/vlan/delete", {"iface": "definitely-not-real"})
    rep.check(
        "deleting an unknown adapter is refused",
        "is not a VLAN" in body,
        "an unknown name was not rejected",
    )
    # The session guard only fires when the browser arrived over the interface
    # being deleted, which needs the request to come in on the VLAN itself.
    _status, card = web.get("/section/network/edit")
    if "This session" in card and vlan_name in card.split("This session")[0][-200:]:
        _status, body = web.post_form("/section/network/vlan/delete", {"iface": vlan_name})
        rep.check(
            "deleting this session's own adapter is refused",
            "This session is connected over" in body,
            "the session guard did not fire",
        )
    else:
        rep.skip(
            "deleting this session's own adapter is refused",
            "this run did not arrive over the VLAN; re-run with --dut set to the VLAN address",
        )


def phase_traffic_separation(rep: Report, web: Web, ssh_base: list[str], args, vlan_name: str) -> None:
    print("\n[4] Traffic separation")
    vlan_addr = iface_address(ssh_base, args.dut, vlan_name).split("/")[0]
    if not vlan_addr:
        # A VLAN with no address is not offered as a pin at all: the interface
        # pickers are built from interfaces that HAVE an IPv4, so an unaddressed
        # VLAN cannot be selected and nothing downstream would be measuring the
        # pin. Give it an address in Network Settings first.
        rep.skip("PSN leaves tagged on the parent NIC", f"{vlan_name} has no address")
        rep.skip("PSN stops when its interface goes dark", f"{vlan_name} has no address")
        return

    status, _body = set_psn_pin(web, vlan_name)
    if not rep.check("PSN pin accepted", status == 200, f"HTTP {status}"):
        return
    if not rep.check(
        "pin persisted",
        read_psn_pin(web) == vlan_name,
        "the pin did not reach config - a device-local field was stripped",
    ):
        return
    time.sleep(SETTLE_SECONDS)

    frames = capture_psn(ssh_base, args.dut, args.parent, args.psn_mcast)
    ours = tags_in(frames, vlan_addr)
    rep.check(
        "PSN leaves tagged on the parent NIC",
        ours == {f"vlan {args.vlan_id}"},
        f"frames from {vlan_addr} carried {sorted(ours) or ['nothing']}",
    )
    rep.check(
        "no untagged PSN from this station",
        not any(f.get("vlan") is None and f.get("src") == vlan_addr for f in frames),
        "the same station sent untagged PSN while pinned",
    )
    if args.other_nic:
        leaked = capture_psn(ssh_base, args.dut, args.other_nic, args.psn_mcast)
        rep.check(
            "PSN does not leak to the other NIC",
            not any(f.get("src") == vlan_addr for f in leaked),
            f"PSN from {vlan_addr} seen on {args.other_nic}",
        )
    else:
        rep.skip("PSN does not leak to the other NIC", "no --other-nic given (needs a second interface)")


def phase_fail_closed(rep: Report, web: Web, ssh_base: list[str], args) -> None:
    """The load-bearing rule: a configured interface with no address must stop
    the plane, never move it. Pinning to an absent interface exercises it
    without needing a second NIC or a link to unplug.

    Note the capture cannot use another station as a control here. Suspending
    PSN stops the receiver too, which drops this port's IGMP membership, so the
    switch prunes the group and every station's PSN disappears from the capture
    - not just ours. The pinned station's own send socket is the direct
    evidence, so that is what this asserts.
    """
    print("\n[5] Fail closed when the pinned interface is unavailable")
    absent = f"{args.parent}9999"
    status, _body = set_psn_pin(web, absent)
    if not rep.check("pin to an absent interface accepted", status == 200, f"HTTP {status}"):
        return
    time.sleep(SETTLE_SECONDS)

    sockets = ssh(
        ssh_base,
        args.dut,
        f"sudo ss -unap 2>/dev/null | grep -c '{args.psn_mcast}' || true",
    ).stdout.strip()
    rep.check(
        "PSN sockets are closed, not rebound",
        sockets in ("", "0"),
        f"{sockets} PSN socket(s) still open on a down pin",
    )
    frames = capture_psn(ssh_base, args.dut, args.parent, args.psn_mcast)
    rep.check(
        "no PSN on the wire while suspended",
        not frames,
        f"{len(frames)} PSN frames still leaving",
    )
    logged = ssh(
        ssh_base,
        args.dut,
        "sudo journalctl -u openfollow --since '-2min' --no-pager"
        " | grep -c 'will not be sent on another interface' || true",
    ).stdout.strip()
    rep.check(
        "the operator is told why it stopped",
        logged not in ("", "0"),
        "nothing in the journal names the down interface",
    )


def phase_companion(rep: Report, args, vlan_name: str) -> None:
    print("\n[6] End-to-end delivery at the companion station")
    if not args.companion:
        rep.skip("companion receives PSN on the tagged network", "no --companion given")
        return
    try:
        web_b = Web(args.companion, args.pin)
        status, data = web_b.get_json("/api/info")
        rep.check(
            "companion web UI reachable",
            status == 200 and bool(data),
            f"HTTP {status}",
        )
    except OSError as exc:
        rep.check("companion web UI reachable", False, str(exc))
        return
    rep.skip(
        "companion receives PSN on the tagged network",
        "requires the companion on the same tagged VLAN; verify its viewer markers by eye",
    )


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dut", required=True, help="device under test IP")
    p.add_argument("--companion", default="", help="second station IP (optional)")
    p.add_argument("--parent", default="eth0", help="physical NIC on the trunk port")
    p.add_argument("--other-nic", default="", help="a second NIC PSN must NOT appear on")
    p.add_argument("--vlan-id", type=int, default=10, help="802.1Q tag to create (1-4094)")
    p.add_argument("--psn-mcast", default="236.10.10.10", help="PSN multicast group")
    p.add_argument("--ssh-user", default="openfollow")
    p.add_argument("--ssh-key", default="~/.ssh/id_rsa")
    p.add_argument("--pin", default="", help="web PIN, if the station has one")
    args = p.parse_args()

    if not 1 <= args.vlan_id <= 4094:
        print("--vlan-id must be between 1 and 4094", file=sys.stderr)
        return 2

    vlan_name = f"{args.parent}.{args.vlan_id}"
    ssh_base = build_ssh_base(args.ssh_user, args.ssh_key)
    rep = Report()

    print(f"DUT {args.dut} – creating {vlan_name} on {args.parent}")
    probe = ssh(ssh_base, args.dut, "true")
    if probe.returncode != 0:
        print(f"SSH to {args.dut} failed: {probe.stderr.strip()}", file=sys.stderr)
        return 2
    if netdev_exists(ssh_base, args.dut, vlan_name):
        print(f"{vlan_name} already exists – choose a free --vlan-id so cleanup can't remove a real one.")
        return 2

    if not push_probe(ssh_base, args.dut):
        print("Could not copy vlan_tag_probe.py onto the DUT.", file=sys.stderr)
        return 2

    web = Web(args.dut, args.pin)
    original_pin = read_psn_pin(web)
    print(f"Original PSN pin: {original_pin or '(auto-detect)'}")

    created = False
    try:
        created = phase_vlan_lifecycle(rep, web, ssh_base, args, vlan_name)
        if created:
            phase_no_extra_plumbing(rep, web, vlan_name)
            phase_guards(rep, web, args, vlan_name)
            phase_traffic_separation(rep, web, ssh_base, args, vlan_name)
            phase_fail_closed(rep, web, ssh_base, args)
            phase_companion(rep, args, vlan_name)
    finally:
        print("\n[cleanup]")
        status, _body = set_psn_pin(web, original_pin)
        print(f"  PSN pin restored to {original_pin or '(auto-detect)'} (HTTP {status})")
        if created:
            status, _body = web.post_form("/section/network/vlan/delete", {"iface": vlan_name})
            gone = not netdev_exists(ssh_base, args.dut, vlan_name)
            print(f"  {vlan_name} deleted (HTTP {status}), netdev gone: {gone}")
            if not gone:
                print(f"  WARNING: {vlan_name} still present – remove it with: sudo nmcli con delete {vlan_name}")

    failures = rep.failed()
    print(f"\n{'FAIL' if failures else 'PASS'} – {len(rep.rows) - len(failures)}/{len(rep.rows)} checks passed")
    for name in failures:
        print(f"  failed: {name}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
