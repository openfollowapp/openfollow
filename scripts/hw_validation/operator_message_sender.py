#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Operator-message OSC sender (runs on a second Pi).

Fires a battery of operator-message OSC packets at the device under test over
BOTH unicast and multicast, exercising every OSC-driven functionality. Pair
with ``operator_message_receiver.py`` on the DUT and ``analyze_results.py``::

    python3 scripts/hw_validation/operator_message_sender.py --host 192.168.178.59 --port 8765

The message strings are stable keys ``analyze_results.py`` asserts on
(``U-`` = sent unicast, ``M-`` = sent multicast).
"""

from __future__ import annotations

import argparse
import time

from pythonosc.udp_client import SimpleUDPClient

MSG = "/openfollow/operator/message"
CLEAR = MSG + "/clear"

# (transport, message, info, marker_id, seconds)
BATTERY = [
    ("uni", "U-bcast-timed", "i1", 0, 30.0),  # broadcast + info + timed
    ("uni", "U-bcast-forever", "", 0, 0.0),  # broadcast + forever
    ("uni", "U-mk3", "", 3, 0.0),  # controlled marker -> accept
    ("uni", "U-mk4", "", 4, 15.0),  # controlled marker + timed -> accept
    ("uni", "U-mk7-drop", "", 7, 0.0),  # uncontrolled marker -> drop
    ("uni", "U-neg-drop", "", -1, 0.0),  # negative id -> drop
    ("uni", "", "", 0, 0.0),  # empty message -> drop
    ("mc", "M-bcast", "", 0, 0.0),  # multicast receive proof (broadcast)
    ("mc", "M-mk3", "", 3, 0.0),  # multicast + controlled marker
    ("mc", "M-mk9-drop", "", 9, 0.0),  # multicast + uncontrolled -> drop
]


def main() -> None:
    ap = argparse.ArgumentParser(description="Operator-message OSC sender battery.")
    ap.add_argument("--host", required=True, help="DUT unicast IP")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--group", default="239.20.20.20")
    args = ap.parse_args()

    uni = SimpleUDPClient(args.host, args.port)
    mc = SimpleUDPClient(args.group, args.port)

    for transport, message, info, marker, secs in BATTERY:
        client = uni if transport == "uni" else mc
        client.send_message(MSG, [message, info, marker, secs])
        time.sleep(0.25)

    time.sleep(1.0)
    uni.send_message(CLEAR, [3])  # clear marker 3 only
    time.sleep(0.6)
    uni.send_message(CLEAR, [])  # clear all
    time.sleep(0.6)
    print("SENDER DONE")


if __name__ == "__main__":
    main()
