#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Assert every operator-message functionality from a receiver JSON dump::

    python3 scripts/hw_validation/analyze_results.py /tmp/of_validate_out.json

Exits non-zero if any check fails. Assumes the default battery sent by
``operator_message_sender.py`` against controlled markers {3, 4}.
"""

from __future__ import annotations

import json
import sys


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/of_validate_out.json"
    with open(path) as fh:
        data = json.load(fh)
    adds = data["adds"]
    clears = data["clears"]
    accepted = {a["message"] for a in adds}
    by_msg = {a["message"]: a for a in adds}

    clear_marker = next((c for c in clears if c["op"] == "clear_marker" and c.get("id") == 3), None)
    clear_all = next((c for c in clears if c["op"] == "clear_all"), None)

    checks = [
        ("unicast received", "U-bcast-timed" in accepted),
        ("multicast received", "M-bcast" in accepted),
        ("broadcast routed (markerId 0)", {"U-bcast-timed", "U-bcast-forever", "M-bcast"} <= accepted),
        ("controlled marker accepted (3/4)", {"U-mk3", "U-mk4", "M-mk3"} <= accepted),
        ("uncontrolled marker dropped", not ({"U-mk7-drop", "M-mk9-drop"} & accepted)),
        ("negative markerId dropped", "U-neg-drop" not in accepted),
        ("empty message dropped", "" not in accepted),
        ("info field carried", by_msg.get("U-bcast-timed", {}).get("info") == "i1"),
        ("timed duration carried", by_msg.get("U-bcast-timed", {}).get("duration_s") == 30.0),
        ("forever duration is 0", by_msg.get("U-bcast-forever", {}).get("duration_s") == 0.0),
        ("clear-by-marker removed marker 3", clear_marker is not None and 3 not in clear_marker.get("after_ids", [3])),
        ("clear-all emptied the store", clear_all is not None and clear_all.get("after") == []),
    ]

    width = max(len(name) for name, _ in checks)
    passed = 0
    for name, ok in checks:
        passed += bool(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name.ljust(width)}")
    print(f"\n{passed}/{len(checks)} checks passed")
    sys.exit(0 if passed == len(checks) else 1)


if __name__ == "__main__":
    main()
