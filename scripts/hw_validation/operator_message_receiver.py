#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Operator-message OSC receiver harness (runs on the device under test).

Drives the DEPLOYED operator-message OSC stack – ``OscService`` + the OSC
ingest adapter + the message store – over a real UDP listener and records
every store mutation (accept / clear). A sender on a second Pi
(``operator_message_sender.py``) then fires a battery over unicast and
multicast, and ``analyze_results.py`` asserts every OSC-driven functionality.

Run on the DUT from the repo root so it imports the installed package::

    python3 scripts/hw_validation/operator_message_receiver.py \\
        --port 8765 --group 239.20.20.20 --controlled 3,4 --window 15

Bind on the device's configured OSC port (default 8765) if arbitrary ports are
not reachable on your network – some switches/APs isolate non-service ports;
stop ``openfollow.service`` first so the port is free. The script writes a
readiness marker once the listener is bound; wait for it before sending.
"""

from __future__ import annotations

import argparse
import json
import time


def main() -> None:
    ap = argparse.ArgumentParser(description="Operator-message OSC receiver harness.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--group", default="239.20.20.20", help='multicast group to join ("" = none)')
    ap.add_argument("--controlled", default="3,4", help="comma-separated controlled marker ids")
    ap.add_argument("--window", type=float, default=15.0, help="seconds to listen before dumping")
    ap.add_argument("--out", default="/tmp/of_validate_out.json")
    ap.add_argument("--ready", default="/tmp/of_validate_ready", help="readiness marker file")
    args = ap.parse_args()

    from openfollow.operator_messages import OperatorMessageStore
    from openfollow.osc.operator_message import OperatorMessageOscAdapter

    from openfollow.osc.service import OscService

    controlled = {int(x) for x in args.controlled.split(",") if x.strip()}
    store = OperatorMessageStore()
    adds: list = []
    clears: list = []

    # Instrument the store so each ingest decision is observable off-box.
    orig_add = store.add
    orig_clear_all = store.clear_all
    orig_clear_marker = store.clear_marker

    def add_wrap(*a, **k):
        orig_add(*a, **k)
        snap = store.snapshot()
        if snap:
            m = max(snap, key=lambda x: x.seq)
            adds.append({"message": m.message, "info": m.info, "marker_id": m.marker_id, "duration_s": m.duration_s})

    def clear_all_wrap(*a, **k):
        orig_clear_all(*a, **k)
        clears.append({"op": "clear_all", "after": [m.message for m in store.snapshot()]})

    def clear_marker_wrap(mid, *a, **k):
        orig_clear_marker(mid, *a, **k)
        snap = store.snapshot()
        clears.append(
            {
                "op": "clear_marker",
                "id": mid,
                "after_ids": sorted({m.marker_id for m in snap}),
                "after": [m.message for m in snap],
            }
        )

    store.add = add_wrap
    store.clear_all = clear_all_wrap
    store.clear_marker = clear_marker_wrap

    svc = OscService()
    adapter = OperatorMessageOscAdapter(svc, store, get_controlled_marker_ids=lambda: set(controlled))
    adapter.start()
    svc.start_listener(args.port, allowed_ips=(), multicast_group=args.group)

    with open(args.ready, "w") as fh:
        fh.write("ready")
    print(f"LISTENER READY on {args.port} (group={args.group or 'none'}, controlled={sorted(controlled)})", flush=True)

    time.sleep(args.window)

    final = [{"message": m.message, "marker_id": m.marker_id, "duration_s": m.duration_s} for m in store.snapshot()]
    with open(args.out, "w") as fh:
        json.dump(
            {
                "adds": adds,
                "clears": clears,
                "final": final,
                "port": args.port,
                "group": args.group,
                "controlled": sorted(controlled),
            },
            fh,
            indent=2,
        )
    svc.shutdown()
    print(f"RECEIVER DONE: {len(adds)} adds, {len(clears)} clears -> {args.out}", flush=True)


if __name__ == "__main__":
    main()
