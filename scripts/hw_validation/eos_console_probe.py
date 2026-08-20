#!/usr/bin/env python3
"""Drive the deployed OSC transmitter at an Eos console / ETCnomad.

Runs the shipped ``OscTransmitterManager`` + ``OscService`` against stub
marker and grid providers, so what lands on the wire is what the app sends -
no probe-local re-implementation of the template renderer.

Single-axis sweeps are the point: the bundled ``ETC Eos`` template's X/Y/Z
mapping onto Augment3d's axes is documented, not observed, and no unit test
can catch a wrong convention. Sweep one axis with the other two pinned at
zero and watch which way the scenic object travels.

    # one-shot, confirms Eos accepts the message at all
    python3 scripts/hw_validation/eos_console_probe.py --host 10.0.0.5 --channel 1 test

    # the axis convention check
    python3 scripts/hw_validation/eos_console_probe.py --host 10.0.0.5 --channel 1 sweep

    # 30 Hz soak: does the console stay responsive
    python3 scripts/hw_validation/eos_console_probe.py --host 10.0.0.5 --channel 1 stream

Exit code reports whether the *sends* succeeded, not whether the object moved
the right way - that verdict is the operator's, from Augment3d and Tab 99.
"""

from __future__ import annotations

import argparse
import math
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Run from a source checkout without installing.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from openfollow.configuration import (  # noqa: E402
    OscDestinationConfig,
    OscDestinationsConfig,
    OscTransmitterConfig,
    OscTransmittersConfig,
)
from openfollow.osc.service import OscService  # noqa: E402
from openfollow.osc.template import builtin_by_id  # noqa: E402
from openfollow.osc.transmitter import OscTransmitterManager  # noqa: E402

_DEST_ID = "eos-probe-dest"
_ROW_ID = "eos-probe-row"

# Grid is read every tick but the Eos templates use absolute-metre
# placeholders only, so these values never reach the wire. Non-zero
# max_height keeps a fractional-Z row from raising if someone edits the
# template to one.
_GRID = (10.0, 6.0, 5.0, 0.0)

# What the operator should see in Augment3d, per axis. The whole reason
# this script exists.
_AXIS_EXPECTATION = {
    "x": "object travels STAGE LEFT <-> RIGHT (lateral)",
    "y": "object travels UPSTAGE <-> DOWNSTAGE (depth)",
    "z": "object travels UP <-> DOWN (height)",
}


class _CountingOscService(OscService):
    """Real service, plus a send counter.

    The transmitter's ring buffer caps at 100 entries, so a before/after
    delta on it under-reports a long sweep and reads zero once it is full.
    Counting at the socket boundary is what "did it transmit" actually means.
    """

    def __init__(self) -> None:
        super().__init__()
        self.sent = 0

    def send(self, address: str, args: Any = (), **kwargs: Any) -> None:
        super().send(address, args, **kwargs)
        self.sent += 1


class _StubMarker:
    """Stands in for ``psn.marker.Marker``; the renderer reads ``pos`` only."""

    def __init__(self) -> None:
        self.pos: tuple[float, float, float] = (0.0, 0.0, 0.0)


def _type_tag(value: Any) -> str:
    """OSC type tag pythonosc will encode ``value`` as.

    Test 6 hinges on this: the patch template's rotation args are written
    ``0.0`` so they type as ``f``; a bare ``0`` would go out as ``i``.
    """
    if isinstance(value, bool):
        return "T" if value else "F"
    if isinstance(value, int):
        return "i"
    if isinstance(value, float):
        return "f"
    if isinstance(value, str):
        return "s"
    return "?"


def _typetag_string(args: list[Any]) -> str:
    return "," + "".join(_type_tag(a) for a in args)


@dataclass
class _Probe:
    manager: OscTransmitterManager
    service: _CountingOscService
    marker: _StubMarker
    row: OscTransmitterConfig
    dest: OscDestinationConfig

    def apply(self, row: OscTransmitterConfig) -> None:
        self.row = row
        self.manager.restart(
            OscTransmittersConfig(transmitters=[row]),
            OscDestinationsConfig(destinations=[self.dest]),
        )


def _build(args: argparse.Namespace) -> _Probe:
    builtin = builtin_by_id(args.template)
    if builtin is None:
        raise SystemExit(f"unknown template id {args.template!r}")

    template_args = list(builtin.args)

    marker = _StubMarker()
    dest = OscDestinationConfig(
        id=_DEST_ID,
        name="Eos probe",
        host=args.host,
        port=args.port,
        protocol="udp",
    )
    row = OscTransmitterConfig(
        id=_ROW_ID,
        enabled=True,
        name=f"probe:{builtin.id}",
        destination_id=_DEST_ID,
        markers=[str(args.channel)],
        template_id=builtin.id,
        address=builtin.address,
        args=template_args,
        trigger={"kind": "stream", "rate_hz": args.rate, "mode": "always"},
    )
    service = _CountingOscService()
    manager = OscTransmitterManager(
        osc_service=service,
        marker_provider=lambda mid: marker if mid == args.channel else None,
        grid_provider=lambda: _GRID,
    )
    probe = _Probe(manager=manager, service=service, marker=marker, row=row, dest=dest)
    probe.apply(row)
    return probe


def _describe(probe: _Probe) -> None:
    preview = probe.manager.preview_for(_ROW_ID) or {}
    address = preview.get("address", "")
    prev_args = list(preview.get("args", []))
    print(f"  template : {probe.row.template_id}")
    print(f"  target   : {probe.dest.host}:{probe.dest.port} udp")
    print(f"  address  : {address}")
    print(f"  args     : {prev_args}")
    print(f"  typetags : {_typetag_string(prev_args)}")


# ---------------------------------------------------------------------------
# Readback: Eos's own account of what it did
# ---------------------------------------------------------------------------

# Eos answers /eos/ping with /eos/out/ping carrying the same arguments. That
# makes it a positive control: a reply proves the console is processing input
# from this address, which a silently-discarding interface does not.
_PING_TOKEN = "openfollow-verify"

# Values the assertion drives. Distinct from each other, non-zero, and
# asymmetric in sign so a transposed or mirrored axis cannot coincidentally
# satisfy the check. The alternate set is used when the channel already holds
# the primary one - Eos reports only changes, so re-sending a held value looks
# exactly like rejection.
_PROBE_XYZ = (-5.5, 4.25, 2.75)
_ALT_XYZ = (3.75, -6.25, 1.25)


def _osc_pad(index: int) -> int:
    return (index + 3) & ~3


def _parse_osc(data: bytes) -> tuple[str, list[Any]]:
    """Minimal OSC decode for the reply types Eos sends (s/f/i/T/F)."""
    try:
        end = data.index(b"\x00")
    except ValueError:
        return ("", [])
    address = data[:end].decode(errors="replace")
    off = _osc_pad(end + 1)
    try:
        tags_end = data.index(b"\x00", off)
    except ValueError:
        return (address, [])
    tags = data[off:tags_end].decode(errors="replace")
    off = _osc_pad(tags_end + 1)
    args: list[Any] = []
    for tag in tags[1:]:
        try:
            if tag == "f":
                args.append(struct.unpack_from(">f", data, off)[0])
                off += 4
            elif tag == "i":
                args.append(struct.unpack_from(">i", data, off)[0])
                off += 4
            elif tag == "s":
                end = data.index(b"\x00", off)
                args.append(data[off:end].decode(errors="replace"))
                off = _osc_pad(end + 1)
            elif tag in "TF":
                args.append(tag == "T")
        except (struct.error, ValueError, IndexError):
            break
    return (address, args)


class _EosListener:
    """Collects Eos's OSC TX output on a UDP port.

    Requires OSC TX enabled on the console with its TX address pointed back
    here. Without it every readback is empty and the verify cannot run - which
    the caller reports as a setup failure, not a template failure.
    """

    def __init__(self, port: int) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("0.0.0.0", port))
        self._sock.settimeout(0.3)
        self._messages: list[tuple[str, list[Any]]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, _addr = self._sock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                break
            self._messages.append(_parse_osc(data))

    def mark(self) -> int:
        """Index to read forward from, so a probe sees only its own replies."""
        return len(self._messages)

    def since(self, mark: int) -> list[tuple[str, list[Any]]]:
        return self._messages[mark:]

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)
        self._sock.close()


def _focus_values(messages: list[tuple[str, list[Any]]]) -> dict[str, float]:
    """Pull X/Y/Z Focus out of Eos's active-parameter reports, last write wins.

    Reports look like ``/eos/out/active/wheel/2 ['X Focus  [-6]', 2, -5.5]``:
    a display label carrying a rounded value, the parameter category, then the
    actual float. The float is what gets asserted; the label only names it.
    """
    out: dict[str, float] = {}
    for address, args in messages:
        if "/active/wheel/" not in address or len(args) < 3:
            continue
        if not isinstance(args[0], str) or not isinstance(args[2], (int, float)):
            continue
        label = args[0].split("[")[0].strip()
        if label:
            out[label] = float(args[2])
    return out


def _do_test(probe: _Probe, args: argparse.Namespace) -> int:
    """One-shot send at a fixed position (tests 1 and 5)."""
    probe.marker.pos = (args.x, args.y, args.z)
    print(f"position   : x={args.x} y={args.y} z={args.z}")
    result = probe.manager.test_send(_ROW_ID)
    if result is None:
        print("FAIL: row vanished from the manager")
        return 1
    print(f"  address  : {result['address']}")
    print(f"  args     : {result['args']}")
    print(f"  typetags : {_typetag_string(list(result['args']))}")
    if not result["sent"]:
        print(f"FAIL: send skipped - {result['error']}")
        return 1
    print("sent. Check Eos Tab 99 for the message and Augment3d for the object.")
    return 0


def _sweep_axis(probe: _Probe, axis: str, args: argparse.Namespace) -> int:
    """Drive one axis through a full cycle, the other two pinned at zero."""
    idx = "xyz".index(axis)
    print(f"\n--- sweep {axis.upper()} " + "-" * 46)
    print(f"  expect   : {_AXIS_EXPECTATION[axis]}")
    print(f"  range    : {-args.span:+.2f} m .. {args.span:+.2f} m over {args.duration:.0f}s")
    if args.pause:
        input("  press Enter to start (Ctrl-C to abort) ... ")

    sent_before = probe.service.sent
    start = time.monotonic()
    last_print = 0.0
    try:
        while True:
            elapsed = time.monotonic() - start
            if elapsed >= args.duration:
                break
            # 0 -> +span -> -span -> 0 over the duration, so the object
            # returns to origin and the turnarounds make the direction of
            # travel unambiguous.
            value = args.span * math.sin(2.0 * math.pi * (elapsed / args.duration))
            pos = [0.0, 0.0, 0.0]
            pos[idx] = value
            probe.marker.pos = (pos[0], pos[1], pos[2])
            if elapsed - last_print >= 0.1:
                print(f"\r  {axis}={value:+7.3f} m   ", end="", flush=True)
                last_print = elapsed
            time.sleep(1.0 / 60.0)
    finally:
        # Park at origin and let one more tick carry it out, so the object
        # doesn't stay parked wherever the loop happened to end.
        probe.marker.pos = (0.0, 0.0, 0.0)
        time.sleep(2.0 / max(args.rate, 1))
    sent = probe.service.sent - sent_before
    print(f"\r  sent     : {sent} messages" + " " * 16)
    if sent == 0:
        print("  FAIL: nothing left the transmitter")
        return 1
    return 0


def _do_sweep(probe: _Probe, args: argparse.Namespace) -> int:
    axes = ["x", "y", "z"] if args.axis == "all" else [args.axis]
    rc = 0
    # One scheduler for the whole run - restarting it per axis drops the
    # first ticks of each sweep.
    probe.manager.start()
    try:
        for axis in axes:
            rc |= _sweep_axis(probe, axis, args)
    finally:
        probe.manager.stop()
    print("\nSweeps complete. The verdict is what you saw in Augment3d:")
    for axis in axes:
        print(f"  {axis.upper()}: {_AXIS_EXPECTATION[axis]}")
    return rc


def _do_stream(probe: _Probe, args: argparse.Namespace) -> int:
    """Continuous circular drive - the 30 Hz soak (test 7)."""
    print(f"\nstreaming a {args.span:.1f} m circle at {args.rate} Hz for {args.duration:.0f}s")
    print("watch: object tracks smoothly, console stays responsive to manual input")
    probe.manager.start()
    start = time.monotonic()
    last_print = 0.0
    try:
        while True:
            elapsed = time.monotonic() - start
            if elapsed >= args.duration:
                break
            angle = 2.0 * math.pi * (elapsed / max(args.period, 0.1))
            probe.marker.pos = (
                args.span * math.cos(angle),
                args.span * math.sin(angle),
                args.z,
            )
            if elapsed - last_print >= 0.25:
                print(f"\r  t={elapsed:5.1f}s  sent={probe.service.sent}   ", end="", flush=True)
                last_print = elapsed
            time.sleep(1.0 / 60.0)
    except KeyboardInterrupt:
        print("\n  interrupted")
    finally:
        probe.manager.stop()
    sent = probe.service.sent
    print(f"\r  sent     : {sent} messages in {time.monotonic() - start:.1f}s" + " " * 16)
    return 0 if sent else 1


def _instrument(svc: OscService, host: str, port: int, address: str, args: list[Any]) -> None:
    """Send a console-control message.

    Deliberately not routed through the transmitter manager: /eos/ping,
    /eos/subscribe and /eos/cmd are instrumentation, not the thing under test.
    Only the template send goes through the deployed path.
    """
    svc.send(address, list(args), host=host, port=port, protocol="udp")


def _check_link(svc: OscService, listener: _EosListener, args: argparse.Namespace) -> bool:
    """Prove Eos processes input from this address before trusting any silence."""
    mark = listener.mark()
    _instrument(svc, args.host, args.port, "/eos/ping", [_PING_TOKEN])
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        for address, reply in listener.since(mark):
            if address == "/eos/out/ping" and _PING_TOKEN in reply:
                return True
        time.sleep(0.1)
    return False


def _select(svc: OscService, listener: _EosListener, args: argparse.Namespace, channel: int) -> dict[str, float]:
    """Select a channel and return the parameters Eos reports for it.

    Eos reports only changes, so selecting an already-selected channel is
    silent. The caller parks the selection elsewhere first to force a report.
    """
    mark = listener.mark()
    _instrument(svc, args.host, args.port, "/eos/cmd", [f"Chan {channel}#"])
    time.sleep(args.settle)
    return _focus_values(listener.since(mark))


def _verify_one(probe: _Probe, svc: OscService, listener: _EosListener, args: argparse.Namespace) -> bool:
    """Drive one template and assert Eos's readback matches what was sent."""
    tpl_id = probe.row.template_id
    print(f"\n--- {tpl_id}  ({probe.row.address})")

    _select(svc, listener, args, args.park_channel)
    baseline = _select(svc, listener, args, args.channel)
    if not any(k.endswith("Focus") for k in baseline):
        print(f"    SETUP: channel {args.channel} reports no X/Y/Z Focus parameters.")
        print("           Patch it as ETC Fixtures > Scenic Element > Scenic Element Movable,")
        print("           or point --channel at a moving light.")
        return False
    print(f"    before  {_fmt_focus(baseline)}")

    # Re-sending a value the channel already holds produces no report, which
    # is indistinguishable from rejection - so pick the set it is not on.
    target = _PROBE_XYZ
    if all(abs(baseline.get(f"{ax} Focus", 1e9) - v) < 0.01 for ax, v in zip("XYZ", _PROBE_XYZ, strict=True)):
        target = _ALT_XYZ
        print("    (channel already holds the primary probe values; using the alternate set)")

    probe.marker.pos = target
    mark = listener.mark()
    result = probe.manager.test_send(_ROW_ID)
    if result is None or not result["sent"]:
        err = "row vanished" if result is None else result["error"]
        print(f"    FAIL: transmitter did not send - {err}")
        return False
    print(f"    sent    {result['address']}  {result['args']}  {_typetag_string(list(result['args']))}")
    time.sleep(args.settle)
    at_send = _focus_values(listener.since(mark))

    _select(svc, listener, args, args.park_channel)
    readback = _select(svc, listener, args, args.channel)
    print(f"    after   {_fmt_focus(readback)}")

    ok = True
    for axis, expected in zip("XYZ", target, strict=True):
        label = f"{axis} Focus"
        actual = readback.get(label, at_send.get(label))
        if actual is None:
            print(f"    FAIL: {label} not reported")
            ok = False
        elif abs(actual - expected) > args.tolerance:
            print(f"    FAIL: {label} = {actual:g}, sent {expected:g}")
            ok = False
    if ok:
        mapping = "  ".join(f"{a}->{a} Focus" for a in "XYZ")
        print(f"    PASS    1:1 in metres  ({mapping})")
    return ok


def _fmt_focus(values: dict[str, float]) -> str:
    parts = [f"{ax}={values[f'{ax} Focus']:g}" for ax in "XYZ" if f"{ax} Focus" in values]
    return "  ".join(parts) if parts else "(none reported)"


def _do_verify(args: argparse.Namespace) -> int:
    """Assert the bundled templates' axis mapping against a live console.

    Unit tests pin the wire bytes; only the console can say which axis each
    argument lands on. This drives the deployed transmitter and reads Eos's
    own parameter values back, so it needs no human watching Augment3d.
    """
    template_ids = [args.template] if args.template else ["etc", "etc-user99"]
    try:
        listener = _EosListener(args.rx_port)
    except OSError as exc:
        print(f"cannot listen on udp/{args.rx_port}: {exc}")
        return 1

    svc = OscService()
    try:
        print("=== Eos console probe - verify " + "=" * 28)
        print(f"  console  : {args.host}:{args.port}")
        print(f"  channel  : {args.channel}   (parking selection on {args.park_channel})")
        print(f"  listening: udp/{args.rx_port}\n")

        if not _check_link(svc, listener, args):
            print("SETUP FAIL: no /eos/out/ping reply. Either OSC TX is off on the console")
            print("            (set its TX port to this port and TX address to this machine),")
            print("            or Eos is discarding input from this interface - it does that")
            print("            silently when two interfaces share a subnet. Check")
            print("            Setup > Device > Network for a duplicate.")
            return 1
        print("  link OK  : console answered /eos/ping")

        _instrument(svc, args.host, args.port, "/eos/subscribe", [1])
        time.sleep(0.5)

        results = {}
        for tpl_id in template_ids:
            args.template = tpl_id
            results[tpl_id] = _verify_one(_build(args), svc, listener, args)
    finally:
        listener.close()

    print("\n" + "=" * 58)
    for tpl_id, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {tpl_id}")
    return 0 if all(results.values()) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drive the deployed OSC transmitter at an Eos console / ETCnomad.",
    )
    parser.add_argument("--host", required=True, help="Eos OSC RX address (use the real NIC, not 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Eos OSC RX port (default 8000)")
    parser.add_argument("--channel", type=int, default=1, help="Eos channel = OpenFollow marker id (default 1)")
    parser.add_argument(
        "--template",
        default=None,
        choices=["etc", "etc-user99"],
        help="bundled template id (default etc; verify does both unless set)",
    )
    parser.add_argument("--rate", type=int, default=30, help="stream rate in Hz (default 30)")
    parser.add_argument("--span", type=float, default=3.0, help="sweep/circle amplitude in metres (default 3.0)")
    parser.add_argument("--duration", type=float, default=10.0, help="seconds per axis / per stream (default 10)")
    parser.add_argument("--period", type=float, default=8.0, help="stream: seconds per circle (default 8)")
    parser.add_argument("--no-pause", dest="pause", action="store_false", help="don't wait for Enter between axes")
    parser.add_argument("-x", type=float, default=1.0, help="test: x metres (default 1.0)")
    parser.add_argument("-y", type=float, default=2.0, help="test: y metres (default 2.0)")
    parser.add_argument("-z", type=float, default=0.5, help="test: z metres (default 0.5)")
    parser.add_argument("--axis", default="all", choices=["x", "y", "z", "all"], help="sweep: axis (default all)")
    parser.add_argument("--rx-port", type=int, default=8001, help="verify: port Eos's OSC TX targets (default 8001)")
    parser.add_argument(
        "--park-channel",
        type=int,
        default=1,
        help="verify: channel to park the selection on so a re-select reports (default 1)",
    )
    parser.add_argument("--settle", type=float, default=2.0, help="verify: seconds to wait for replies (default 2)")
    parser.add_argument("--tolerance", type=float, default=0.05, help="verify: metres of slack (default 0.05)")
    parser.add_argument("command", choices=["test", "sweep", "stream", "verify"])
    args = parser.parse_args(argv)

    if args.command == "verify":
        return _do_verify(args)
    if args.template is None:
        args.template = "etc"

    probe = _build(args)
    print(f"=== Eos console probe - {args.command} " + "=" * 30)
    _describe(probe)
    print()

    if args.command == "test":
        return _do_test(probe, args)
    if args.command == "sweep":
        return _do_sweep(probe, args)
    return _do_stream(probe, args)


if __name__ == "__main__":
    raise SystemExit(main())
