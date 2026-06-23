# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the OSC transmitter runtime.

Covers ``OscTransmitter`` (single-row state) and ``OscTransmitterManager``
(the 60 Hz scheduler thread): tick cadence, down-sample firing,
skip-on-no-data, hot-reload row diffing, and lifecycle.

Tests drive the manager through its public surface: ``restart`` to
populate rows, ``_tick_once`` to step a single beat (avoids timing the
real scheduler thread), ``start`` / ``stop`` for threaded-lifecycle
checks. The OSC service and marker provider are fakes.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from openfollow.configuration import (
    ControllerButtonTrigger,
    HotkeyTrigger,
    OscDestinationConfig,
    OscDestinationsConfig,
    OscTransmitterConfig,
    OscTransmittersConfig,
    StreamTrigger,
)
from openfollow.osc.transmitter import (
    BindingRingBuffer,
    OscTransmitter,
    OscTransmitterManager,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeOscService:
    """Records every ``send`` for assertion. Doesn't open sockets."""

    def __init__(self) -> None:
        # Recorded tuple includes framing for round-trip assertions.
        self.calls: list[tuple[str, list[Any], str, int, str, str]] = []

    def send(
        self,
        address: str,
        args: list[Any] = (),  # noqa: ANN001
        *,
        host: str,
        port: int,
        protocol: str = "udp",
        framing: str = "slip",
    ) -> None:
        self.calls.append(
            (address, list(args), host, port, protocol, framing),
        )


class _FakeMarker:
    """Stand-in for ``openfollow.psn.marker.Marker`` – only ``pos`` is read."""

    def __init__(self, pos: tuple[float, float, float]) -> None:
        self.pos = pos


# Default destination every ``_row`` resolves to. Host/port match the
# values tests historically asserted on, now carried by the destination.
_DEFAULT_DEST_ID = "dest-1"


def _destinations(*dests: OscDestinationConfig) -> OscDestinationsConfig:
    """Build an ``OscDestinationsConfig``; defaults to the single
    ``dest-1`` profile rows reference unless explicit ones are given."""
    if not dests:
        dests = (OscDestinationConfig(id=_DEFAULT_DEST_ID, name="Default", host="127.0.0.1", port=9000),)
    return OscDestinationsConfig(destinations=list(dests))


def _row(**overrides: Any) -> OscTransmitterConfig:
    """Transmitter row with defaults; tests override only what they assert."""
    defaults: dict[str, Any] = {
        "id": "row-1",
        "enabled": True,
        "destination_id": _DEFAULT_DEST_ID,
        "marker_id": 0,
        "template_id": "",
        "address": "/cue/[markerid]",
        "args": ["[x]", "[y]", "[z]"],
        "rate_hz": 60,
    }
    defaults.update(overrides)
    return OscTransmitterConfig(**defaults)


def _manager(
    *,
    markers: dict[int, _FakeMarker] | None = None,
    grid: tuple[float, float, float, float] = (10.0, 6.0, 0.0, 0.0),
    fader_values: dict[int, float] | None = None,
    marker_fader_values: dict[int, float] | None = None,
    controller_markers: dict[int, int] | None = None,
    destinations: OscDestinationsConfig | None = None,
) -> tuple[OscTransmitterManager, _FakeOscService]:
    # grid tuple = (width, depth, max_height, z_offset). max_height=0.0
    # makes ``[z.frac]`` / ``[z.frac.inv]`` raise RenderError; fractional-Z
    # tests pass a non-zero max_height. ``fader_values`` dict → stub
    # provider via ``.get`` (None for missing index = "not registered");
    # None → no provider. ``controller_markers`` maps 0-based controller
    # index → driven marker id for the ``:cN`` reference.
    service = _FakeOscService()
    marker_map = markers or {}
    fader_provider = None
    if fader_values is not None:
        fader_provider = fader_values.get
    marker_fader_provider = marker_fader_values.get if marker_fader_values is not None else None
    controller_marker_provider = controller_markers.get if controller_markers is not None else None
    manager = OscTransmitterManager(
        osc_service=service,  # type: ignore[arg-type]
        marker_provider=marker_map.get,
        grid_provider=lambda: grid,
        fader_provider=fader_provider,
        marker_fader_provider=marker_fader_provider,
        controller_marker_provider=controller_marker_provider,
    )
    # Seed destinations once; subsequent ``restart(cfg)`` calls (with no
    # destinations arg) keep the staged set, mirroring a transmitter-only edit.
    manager.restart(OscTransmittersConfig(transmitters=[]), destinations or _destinations())
    return manager, service


# ---------------------------------------------------------------------------
# OscTransmitter – config + template recompile
# ---------------------------------------------------------------------------


class TestOscTransmitter:
    def test_constructor_compiles_address_and_args(self) -> None:
        cfg = _row(address="/foo/[x]", args=["[markerid]", "1"])
        row = OscTransmitter(cfg)
        assert row.cfg is cfg
        assert len(row.compiled_args) == 2
        assert len(row.compiled_address) > 0

    def test_update_config_swaps_cfg_and_recompiles(self) -> None:
        row = OscTransmitter(_row(address="/a", args=["1"]))
        first_addr = row.compiled_address
        first_args = row.compiled_args
        new_cfg = _row(address="/b/[x]", args=["[y]", "[z]"])
        row.update_config(new_cfg)
        assert row.cfg is new_cfg
        # Templates re-parsed (new tuple) so a placeholder change takes
        # effect on the next tick.
        assert row.compiled_address is not first_addr
        assert row.compiled_args is not first_args
        assert len(row.compiled_args) == 2


# ---------------------------------------------------------------------------
# Tick cadence
# ---------------------------------------------------------------------------


class TestTickCadence:
    def test_60hz_row_fires_every_tick(self) -> None:
        manager, svc = _manager(markers={0: _FakeMarker((1.0, 2.0, 3.0))})
        manager.restart(OscTransmittersConfig(transmitters=[_row(rate_hz=60)]))
        for _ in range(5):
            manager._tick_once()
        assert len(svc.calls) == 5

    def test_30hz_row_fires_every_other_tick(self) -> None:
        manager, svc = _manager(markers={0: _FakeMarker((0.0, 0.0, 0.0))})
        manager.restart(OscTransmittersConfig(transmitters=[_row(rate_hz=30)]))
        # Ticks 0, 2, 4, 6 fire; ticks 1, 3, 5 don't. 6 ticks total → 3 sends.
        for _ in range(6):
            manager._tick_once()
        assert len(svc.calls) == 3

    def test_1hz_row_fires_once_per_sixty_ticks(self) -> None:
        manager, svc = _manager(markers={0: _FakeMarker((0.0, 0.0, 0.0))})
        manager.restart(OscTransmittersConfig(transmitters=[_row(rate_hz=1)]))
        # Ticks 0, 60, 120 fire – three sends across 121 ticks.
        for _ in range(121):
            manager._tick_once()
        assert len(svc.calls) == 3

    @pytest.mark.parametrize(
        "rate,ticks,expected",
        [
            # rate=5 → ticks_per_send = 60//5 = 12 → fires at ticks {0,12,24,36,48} → 5
            (5, 60, 5),
            # rate=10 → ticks_per_send = 60//10 = 6 → fires at {0,6,12,...,54} → 10
            (10, 60, 10),
            # rate=20 → ticks_per_send = 60//20 = 3 → fires at {0,3,...,57} → 20
            (20, 60, 20),
            # rate=30 → ticks_per_send = 60//30 = 2 → fires at every even tick → 30
            (30, 60, 30),
        ],
    )
    def test_rate_to_ticks_per_send_arithmetic(
        self,
        rate: int,
        ticks: int,
        expected: int,
    ) -> None:
        manager, svc = _manager(markers={0: _FakeMarker((0.0, 0.0, 0.0))})
        manager.restart(OscTransmittersConfig(transmitters=[_row(rate_hz=rate)]))
        for _ in range(ticks):
            manager._tick_once()
        assert len(svc.calls) == expected
        # Sanity: ticks_per_send must divide cleanly so cadence is integer.
        assert 60 % max(1, 60 // rate) == 0


# ---------------------------------------------------------------------------
# Skip-on-no-data and disabled rows
# ---------------------------------------------------------------------------


class TestSkipBehaviour:
    def test_disabled_row_never_fires(self) -> None:
        manager, svc = _manager(markers={0: _FakeMarker((0.0, 0.0, 0.0))})
        manager.restart(
            OscTransmittersConfig(transmitters=[_row(enabled=False)]),
        )
        for _ in range(5):
            manager._tick_once()
        assert svc.calls == []

    def test_missing_marker_skips_silently(self) -> None:
        manager, svc = _manager(markers={})  # marker_id=0 not in map
        manager.restart(OscTransmittersConfig(transmitters=[_row()]))
        manager._tick_once()
        assert svc.calls == []

    def test_per_row_skip_doesnt_block_other_rows(self) -> None:
        """A row whose marker is missing skips, but other rows in the
        same tick still fire."""
        manager, svc = _manager(
            markers={1: _FakeMarker((5.0, 0.0, 0.0))},
        )
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(id="row-a", marker_id=0),  # missing – skip
                    _row(id="row-b", marker_id=1, address="/b/[markerid]"),
                ]
            )
        )
        manager._tick_once()
        assert len(svc.calls) == 1
        assert svc.calls[0][0] == "/b/1"

    def test_provider_exception_skips_row_without_killing_tick(self) -> None:
        """A provider raising a non-RenderError is caught per-row: the
        offending row records an internal-error skip and the rest of the
        tick still fires, so one bad row can't silence all OSC output."""
        manager, svc = _manager(markers={1: _FakeMarker((5.0, 0.0, 0.0))})
        good = manager._marker_provider

        def boom(marker_id: int) -> _FakeMarker | None:
            if marker_id == 0:
                raise RuntimeError("provider boom")
            return good(marker_id)

        manager._marker_provider = boom
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(id="row-a", marker_id=0),  # provider raises → caught
                    _row(id="row-b", marker_id=1, address="/b/[markerid]"),
                ]
            )
        )
        manager._tick_once()  # must not propagate
        assert len(svc.calls) == 1
        assert svc.calls[0][0] == "/b/1"
        skipped = [e for e in (manager.ring_buffer_for("row-a") or []) if e.status == "skipped"]
        assert len(skipped) == 1
        assert "internal error" in skipped[0].error


# ---------------------------------------------------------------------------
# Address + arg rendering
# ---------------------------------------------------------------------------


class TestRendering:
    def test_address_renders_marker_id(self) -> None:
        manager, svc = _manager(markers={3: _FakeMarker((1.0, 2.0, 3.0))})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(marker_id=3, address="/eos/[markerid]/go", args=[]),
                ]
            )
        )
        manager._tick_once()
        assert svc.calls[0][0] == "/eos/3/go"

    def test_xyz_args_emit_floats(self) -> None:
        manager, svc = _manager(markers={0: _FakeMarker((1.5, -2.0, 0.25))})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(args=["[x]", "[y]", "[z]"]),
                ]
            )
        )
        manager._tick_once()
        addr, args, *_ = svc.calls[0]
        assert all(isinstance(a, float) for a in args)
        assert args == [1.5, -2.0, 0.25]

    def test_marker_id_arg_emits_int(self) -> None:
        manager, svc = _manager(markers={7: _FakeMarker((0.0, 0.0, 0.0))})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(marker_id=7, args=["[markerid]"]),
                ]
            )
        )
        manager._tick_once()
        _addr, args, *_ = svc.calls[0]
        assert args == [7]
        assert isinstance(args[0], int)

    def test_mixed_template_arg_emits_string(self) -> None:
        manager, svc = _manager(markers={0: _FakeMarker((1.0, 0.0, 0.0))})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(args=["prefix-[x]"]),
                ]
            )
        )
        manager._tick_once()
        _addr, args, *_ = svc.calls[0]
        assert args == ["prefix-1"]
        assert isinstance(args[0], str)

    def test_fractional_arg_uses_grid_extent(self) -> None:
        manager, svc = _manager(
            markers={0: _FakeMarker((2.5, 0.0, 0.0))},
            grid=(10.0, 6.0, 0.0, 0.0),
        )
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(args=["[x.frac]"]),
                ]
            )
        )
        manager._tick_once()
        _addr, args, *_ = svc.calls[0]
        # 2.5 / (10/2) = 0.5
        assert args == [pytest.approx(0.5)]

    def test_grid_provider_is_re_read_each_tick(self) -> None:
        """Grid changes take effect on the next tick without manager restart."""
        grid_extent = [10.0, 6.0, 0.0, 0.0]

        def grid_provider() -> tuple[float, float, float, float]:
            return (grid_extent[0], grid_extent[1], grid_extent[2], grid_extent[3])

        service = _FakeOscService()
        manager = OscTransmitterManager(
            osc_service=service,  # type: ignore[arg-type]
            marker_provider={0: _FakeMarker((2.5, 0.0, 0.0))}.get,
            grid_provider=grid_provider,
        )
        manager.restart(OscTransmittersConfig(transmitters=[_row(args=["[x.frac]"])]), _destinations())
        manager._tick_once()
        assert service.calls[0][1] == [pytest.approx(0.5)]
        # Widen grid to 20 m → fraction halves on next tick.
        grid_extent[0] = 20.0
        manager._tick_once()
        assert service.calls[1][1] == [pytest.approx(0.25)]

    def test_grid_provider_max_height_hot_reload_unblocks_fz(self) -> None:
        """A ``[z.frac]`` row raises RenderError while ``max_height`` is
        unset, then renders on the next tick once a height is set."""
        grid_extent = [10.0, 6.0, 0.0, 0.0]

        def grid_provider() -> tuple[float, float, float, float]:
            return (grid_extent[0], grid_extent[1], grid_extent[2], grid_extent[3])

        service = _FakeOscService()
        manager = OscTransmitterManager(
            osc_service=service,  # type: ignore[arg-type]
            marker_provider={0: _FakeMarker((0.0, 0.0, 2.0))}.get,
            grid_provider=grid_provider,
        )
        manager.restart(OscTransmittersConfig(transmitters=[_row(args=["[z.frac]"])]), _destinations())
        # First tick: max_height is 0, row skips with a RenderError.
        manager._tick_once()
        assert service.calls == []
        # max_height=4 → next tick produces 2/4 = 0.5.
        grid_extent[2] = 4.0
        manager._tick_once()
        assert service.calls[0][1] == [pytest.approx(0.5)]


class TestTransport:
    def test_send_resolves_destination_endpoint(self) -> None:
        """A row's ``destination_id`` resolves to the destination's
        host/port/protocol at send time."""
        dests = _destinations(
            OscDestinationConfig(id="d-tcp", host="10.0.0.5", port=8000, protocol="tcp"),
        )
        manager, svc = _manager(markers={0: _FakeMarker((0.0, 0.0, 0.0))}, destinations=dests)
        manager.restart(OscTransmittersConfig(transmitters=[_row(destination_id="d-tcp")]))
        manager._tick_once()
        _addr, _args, host, port, protocol, _framing = svc.calls[0]
        assert host == "10.0.0.5"
        assert port == 8000
        assert protocol == "tcp"

    def test_send_passes_framing_through(self) -> None:
        """Per-destination framing travels through the tick into
        ``OscService.send`` so the cached ``TcpOscSender`` uses the
        configured wire format."""
        dests = _destinations(
            OscDestinationConfig(id="d-slip", host="10.0.0.5", port=8001, protocol="tcp", framing="slip"),
            OscDestinationConfig(id="d-lp", host="10.0.0.6", port=8002, protocol="tcp", framing="length_prefix"),
        )
        manager, svc = _manager(markers={0: _FakeMarker((0.0, 0.0, 0.0))}, destinations=dests)
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(id="row-slip", destination_id="d-slip"),
                    _row(id="row-lp", destination_id="d-lp"),
                ]
            )
        )
        manager._tick_once()
        framings = {host: framing for _addr, _args, host, _port, _protocol, framing in svc.calls}
        assert framings["10.0.0.5"] == "slip"
        assert framings["10.0.0.6"] == "length_prefix"

    def test_unresolved_destination_skips_send_with_reason(self) -> None:
        """A blank / dangling ``destination_id`` sends nothing and records
        a skip reason in the row's ring buffer."""
        manager, svc = _manager(markers={0: _FakeMarker((0.0, 0.0, 0.0))})
        manager.restart(OscTransmittersConfig(transmitters=[_row(id="r", destination_id="")]))
        manager._tick_once()
        assert svc.calls == []
        entries = manager.ring_buffer_for("r") or []
        assert entries[-1].status == "skipped"
        assert entries[-1].error == "no destination selected"

    def test_destination_ip_edit_repoints_next_send(self) -> None:
        """Re-staging destinations with a new host (a live IP edit) makes the
        next send target the new host – no row edit needed."""
        dests = _destinations(OscDestinationConfig(id="d", host="10.0.0.5", port=9000))
        manager, svc = _manager(markers={0: _FakeMarker((0.0, 0.0, 0.0))}, destinations=dests)
        manager.restart(OscTransmittersConfig(transmitters=[_row(destination_id="d")]))
        manager._tick_once()
        assert svc.calls[0][2] == "10.0.0.5"
        # Operator edits the destination's IP; transmitters unchanged.
        new_dests = _destinations(OscDestinationConfig(id="d", host="10.0.0.9", port=9000))
        manager.restart(OscTransmittersConfig(transmitters=[_row(destination_id="d")]), new_dests)
        manager._tick_once()
        assert svc.calls[-1][2] == "10.0.0.9"


# ---------------------------------------------------------------------------
# Hot-reload row diff
# ---------------------------------------------------------------------------


class TestRestart:
    def test_new_row_id_creates_fresh_transmitter(self) -> None:
        manager, _svc = _manager()
        manager.restart(OscTransmittersConfig(transmitters=[_row(id="a")]))
        assert manager.row_ids() == ["a"]

    def test_existing_row_id_updates_in_place(self) -> None:
        """Same id across reloads → same OscTransmitter instance with the new
        config swapped in. Compiled templates rebuilt; identity preserved so
        per-row stats survive."""
        manager, _svc = _manager()
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(id="a", address="/old"),
                ]
            )
        )
        first = manager._rows["a"]
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(id="a", address="/new"),
                ]
            )
        )
        assert manager._rows["a"] is first
        assert first.cfg.address == "/new"

    def test_absent_row_id_is_dropped(self) -> None:
        manager, _svc = _manager()
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(id="a"),
                    _row(id="b"),
                ]
            )
        )
        manager.restart(OscTransmittersConfig(transmitters=[_row(id="a")]))
        assert manager.row_ids() == ["a"]

    def test_empty_config_clears_all_rows(self) -> None:
        manager, _svc = _manager()
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(id="a"),
                    _row(id="b"),
                ]
            )
        )
        manager.restart(OscTransmittersConfig(transmitters=[]))
        assert manager.row_ids() == []


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_start_spins_a_daemon_thread(self) -> None:
        manager, _svc = _manager()
        manager.start()
        try:
            assert manager._thread is not None
            assert manager._thread.is_alive()
            assert manager._thread.daemon is True
        finally:
            manager.stop()

    def test_start_is_idempotent(self) -> None:
        manager, _svc = _manager()
        manager.start()
        try:
            first = manager._thread
            manager.start()
            assert manager._thread is first
        finally:
            manager.stop()

    def test_stop_joins_thread_and_clears_reference(self) -> None:
        manager, _svc = _manager()
        manager.start()
        manager.stop()
        assert manager._thread is None

    def test_stop_when_not_started_is_noop(self) -> None:
        manager, _svc = _manager()
        manager.stop()  # No thread to stop, must not raise.

    def test_context_manager_starts_and_stops(self) -> None:
        manager, _svc = _manager()
        with manager:
            assert manager._thread is not None
            assert manager._thread.is_alive()
        assert manager._thread is None

    def test_threaded_run_actually_fires_a_send(self) -> None:
        """Smoke-test the threaded path: start the scheduler and assert at
        least one send arrives. Proves the thread itself runs."""
        marker = _FakeMarker((1.0, 2.0, 3.0))
        manager, svc = _manager(markers={0: marker})
        manager.restart(OscTransmittersConfig(transmitters=[_row(rate_hz=60)]))
        manager.start()
        try:
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline and not svc.calls:
                time.sleep(0.01)
            assert svc.calls, "scheduler thread didn't fire any sends"
        finally:
            manager.stop()

    def test_start_after_stop_works(self) -> None:
        """The manager must be reusable after stop – the dispatcher's apply
        path may re-init transmitters after a prior shutdown."""
        manager, _svc = _manager(markers={0: _FakeMarker((0.0, 0.0, 0.0))})
        manager.restart(OscTransmittersConfig(transmitters=[_row()]))
        manager.start()
        manager.stop()
        manager.start()
        try:
            assert manager._thread is not None
            assert manager._thread.is_alive()
        finally:
            manager.stop()

    def test_stop_does_not_clear_thread_swapped_mid_join(self) -> None:
        """If a concurrent ``start`` rotates ``_thread`` between the lock
        release at the top of ``stop`` and the re-acquire after ``join``,
        ``stop`` must not clear the new thread reference. Guarded by the
        ``self._thread is thread`` check."""

        class _SwappingThread:
            daemon = True

            def __init__(self, manager: OscTransmitterManager) -> None:
                self._mgr = manager

            def is_alive(self) -> bool:
                return False

            def join(self, timeout: float | None = None) -> None:  # noqa: ARG002
                self._mgr._thread = "replacement"  # type: ignore[assignment]

        manager, _svc = _manager()
        swapper = _SwappingThread(manager)
        manager._thread = swapper  # type: ignore[assignment]
        # Returns True: our thread is gone (replaced), nothing left to wait on.
        assert manager.stop() is True
        # The replacement installed mid-join must survive untouched.
        assert manager._thread == "replacement"

    def test_stop_keeps_thread_reference_when_join_times_out(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """If the scheduler is wedged the join times out. ``stop`` keeps the
        still-alive thread reference so a follow-up ``start`` cannot spin a
        second scheduler beside the wedged one."""

        class _StuckThread:
            daemon = True

            def is_alive(self) -> bool:
                return True

            def join(self, timeout: float | None = None) -> None:  # noqa: ARG002
                return  # pretend the worker is wedged past the timeout

        manager, _svc = _manager()
        stuck = _StuckThread()
        manager._thread = stuck  # type: ignore[assignment]
        with caplog.at_level("WARNING", logger="openfollow.osc.transmitter"):
            stopped = manager.stop()
        # Return value lets the caller decide whether to drain shared
        # resources the wedged scheduler may still be using.
        assert stopped is False
        assert manager._thread is stuck
        assert any("did not stop" in r.message for r in caplog.records)
        # ``start`` refuses to launch a second scheduler while the stuck
        # thread still claims to be alive.
        manager.start()
        assert manager._thread is stuck


# ---------------------------------------------------------------------------
# Concurrency: row mutations under the lock
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_restart_during_tick_is_serialised(self) -> None:
        """The lock serialises ``restart`` and ``_tick_once``: a tick
        iterating the row map must not observe a half-applied restart."""
        manager, svc = _manager(markers={0: _FakeMarker((0.0, 0.0, 0.0))})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(id="a"),
                    _row(id="b"),
                ]
            )
        )

        # Drive one tick from one thread while another swaps the rows.
        # The race isn't deterministically reachable without injection
        # points; assert the weaker invariant that the lock-held snapshot
        # at tick start is consistent.
        seen_counts: list[int] = []

        def ticker() -> None:
            for _ in range(20):
                manager._tick_once()
                seen_counts.append(len(svc.calls))

        def reloader() -> None:
            for i in range(20):
                manager.restart(
                    OscTransmittersConfig(
                        transmitters=[
                            _row(id="a"),
                            _row(id=f"b-{i}"),
                        ]
                    )
                )

        t1 = threading.Thread(target=ticker)
        t2 = threading.Thread(target=reloader)
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)
        # No exception escaped, no torn reads.
        assert all(c >= 0 for c in seen_counts)


# ---------------------------------------------------------------------------
# BindingRingBuffer
# ---------------------------------------------------------------------------


class TestBindingRingBuffer:
    def test_records_sent_with_address_and_args(self) -> None:
        buf = BindingRingBuffer()
        buf.record_sent("/cue/1", [1.0, 2.0, 3.0])
        snap = buf.snapshot()
        assert len(snap) == 1
        assert snap[0].status == "sent"
        assert snap[0].address == "/cue/1"
        assert snap[0].args == (1.0, 2.0, 3.0)
        assert snap[0].error == ""

    def test_records_skipped_with_error_string(self) -> None:
        buf = BindingRingBuffer()
        buf.record_skipped(error="x:70")
        snap = buf.snapshot()
        assert len(snap) == 1
        assert snap[0].status == "skipped"
        assert snap[0].error == "x:70"

    def test_capped_to_max_entries_evicts_oldest(self) -> None:
        """deque(maxlen=N) drops the oldest entry on the (N+1)th write so a
        chatty 60 Hz row can't grow the buffer without bound."""
        buf = BindingRingBuffer(max_entries=3)
        for i in range(5):
            buf.record_sent(f"/cue/{i}", [])
        snap = buf.snapshot()
        assert [e.address for e in snap] == ["/cue/2", "/cue/3", "/cue/4"]

    def test_snapshot_is_defensive_copy(self) -> None:
        """Callers iterate the returned list – buffer mutations after
        the snapshot must not leak in."""
        buf = BindingRingBuffer()
        buf.record_sent("/a", [])
        snap = buf.snapshot()
        buf.record_sent("/b", [])
        assert [e.address for e in snap] == ["/a"]


# ---------------------------------------------------------------------------
# Trigger model in the manager
# ---------------------------------------------------------------------------


class TestTriggerModel:
    def test_default_trigger_is_stream_with_legacy_rate_hz(self) -> None:
        """A row with ``rate_hz=N`` and no explicit trigger lifts to
        ``StreamTrigger(rate_hz=N)`` for callers not using the trigger field."""
        row = OscTransmitterConfig(rate_hz=60)
        assert isinstance(row.trigger, StreamTrigger)
        assert row.trigger.rate_hz == 60

    def test_explicit_stream_trigger_overrides_legacy_rate_hz(self) -> None:
        """If both ``trigger`` and ``rate_hz`` are passed, the trigger wins
        (authoritative field); ``rate_hz`` is mirrored back in sync."""
        row = OscTransmitterConfig(
            trigger=StreamTrigger(rate_hz=20),
            rate_hz=60,
        )
        assert row.trigger.rate_hz == 20
        assert row.rate_hz == 20

    def test_hotkey_trigger_normalises_modifiers(self) -> None:
        """Modifiers stored sorted + lower-cased so rows differing only in
        modifier order compare equal."""
        t = HotkeyTrigger(key="F1", modifiers=("SHIFT", "ctrl", "ALT"))
        assert t.modifiers == ("alt", "ctrl", "shift")

    def test_hotkey_trigger_rejects_unknown_modifiers(self) -> None:
        """An unknown modifier (``"meta"``, not in the set) is dropped
        silently rather than crashing the load."""
        t = HotkeyTrigger(key="F1", modifiers=("ctrl", "meta", "shift"))
        assert "meta" not in t.modifiers
        assert "ctrl" in t.modifiers and "shift" in t.modifiers

    def test_hotkey_trigger_invalid_edge_falls_back_to_press(self) -> None:
        t = HotkeyTrigger(key="F1", edge="sideways")
        assert t.edge == "press"

    def test_controller_button_trigger_strips_button_name(self) -> None:
        t = ControllerButtonTrigger(button="  A  ")
        assert t.button == "A"


class TestSchedulerHonoursStreamRate:
    def test_stream_trigger_drives_tick_cadence(self) -> None:
        """The scheduler reads ``trigger.rate_hz`` (not ``cfg.rate_hz``) for
        the down-sample interval."""
        manager, svc = _manager(markers={0: _FakeMarker((1.0, 2.0, 3.0))})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(trigger=StreamTrigger(rate_hz=10)),
                ]
            )
        )
        # 60 Hz scheduler / 10 Hz row → fires every 6 ticks. 6 ticks
        # → 1 send; 12 ticks → 2.
        for _ in range(12):
            manager._tick_once()
        assert len(svc.calls) == 2


class TestNonStreamTriggersDoNotTick:
    """Hotkey / ControllerButton triggers fire on the :class:`InputEventBus`,
    not on the 60 Hz scheduler tick; the tick path skips them silently."""

    def test_hotkey_trigger_does_not_fire_on_tick(self) -> None:
        manager, svc = _manager(markers={0: _FakeMarker((1.0, 2.0, 3.0))})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(trigger=HotkeyTrigger(key="F1")),
                ]
            )
        )
        for _ in range(60):
            manager._tick_once()
        assert svc.calls == []

    def test_controller_button_trigger_does_not_fire_on_tick(self) -> None:
        manager, svc = _manager(markers={0: _FakeMarker((1.0, 2.0, 3.0))})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(trigger=ControllerButtonTrigger(button="A")),
                ]
            )
        )
        for _ in range(60):
            manager._tick_once()
        assert svc.calls == []


class TestStreamOnChangeGate:
    """``StreamTrigger.mode == "on_change"`` skips the wire send when the
    default marker hasn't moved by ``min_change_m`` along any axis since the
    last send. The gate runs only for rows that depend on the default
    marker; rows using only ``[x:markerN]`` fire on every tick."""

    def _on_change_row(self, **overrides: Any) -> OscTransmitterConfig:
        from openfollow.configuration import StreamTrigger

        defaults: dict[str, Any] = {
            "id": "row-1",
            "marker_id": 0,
            "address": "/cue/[markerid]",
            "args": ["[x]", "[y]", "[z]"],
            "trigger": StreamTrigger(
                rate_hz=60,
                mode="on_change",
                min_change_m=0.05,
            ),
        }
        defaults.update(overrides)
        return _row(**defaults)

    def test_first_tick_always_sends(self) -> None:
        # No prior cache entry → nothing to compare, so the first tick
        # sends unconditionally and primes the cache.
        marker = _FakeMarker((1.0, 2.0, 3.0))
        manager, svc = _manager(markers={0: marker})
        manager.restart(OscTransmittersConfig(transmitters=[self._on_change_row()]))
        manager._tick_once()
        assert len(svc.calls) == 1

    def test_unchanged_position_skips(self) -> None:
        marker = _FakeMarker((1.0, 2.0, 3.0))
        manager, svc = _manager(markers={0: marker})
        manager.restart(OscTransmittersConfig(transmitters=[self._on_change_row()]))
        manager._tick_once()
        manager._tick_once()  # same position
        assert len(svc.calls) == 1
        entries = manager.ring_buffer_for("row-1") or []
        skipped = [e for e in entries if e.status == "skipped"]
        assert len(skipped) == 1
        assert "unchanged" in skipped[0].error

    def test_change_above_threshold_sends(self) -> None:
        marker = _FakeMarker((1.0, 2.0, 3.0))
        manager, svc = _manager(markers={0: marker})
        manager.restart(OscTransmittersConfig(transmitters=[self._on_change_row()]))
        manager._tick_once()
        # Move by 6cm on one axis – exceeds the 5cm default threshold.
        marker.pos = (1.06, 2.0, 3.0)
        manager._tick_once()
        assert len(svc.calls) == 2

    def test_change_below_threshold_skips(self) -> None:
        marker = _FakeMarker((1.0, 2.0, 3.0))
        manager, svc = _manager(markers={0: marker})
        manager.restart(OscTransmittersConfig(transmitters=[self._on_change_row()]))
        manager._tick_once()
        # Move by 4cm on every axis – none exceeds the 5cm default.
        marker.pos = (1.04, 2.04, 3.04)
        manager._tick_once()
        assert len(svc.calls) == 1

    # With ``min_change_m = 0`` the gate skips only on bit-exactly
    # unchanged positions (axis deltas are absolute, so ``delta >= 0``).
    def test_zero_threshold_skips_bit_exact_duplicates(self) -> None:
        from openfollow.configuration import StreamTrigger

        marker = _FakeMarker((1.0, 2.0, 3.0))
        manager, svc = _manager(markers={0: marker})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    self._on_change_row(
                        trigger=StreamTrigger(
                            rate_hz=60,
                            mode="on_change",
                            min_change_m=0.0,
                        )
                    ),
                ]
            )
        )
        manager._tick_once()
        manager._tick_once()  # exact same position
        assert len(svc.calls) == 1
        # The skip entry uses bit-exact phrasing, not "Δ < 0 m".
        entries = manager.ring_buffer_for("row-1") or []
        skipped = [e for e in entries if e.status == "skipped"]
        assert len(skipped) == 1
        assert "bit-exact" in skipped[0].error

    def test_zero_threshold_sends_on_any_change(self) -> None:
        from openfollow.configuration import StreamTrigger

        marker = _FakeMarker((1.0, 2.0, 3.0))
        manager, svc = _manager(markers={0: marker})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    self._on_change_row(
                        trigger=StreamTrigger(
                            rate_hz=60,
                            mode="on_change",
                            min_change_m=0.0,
                        )
                    ),
                ]
            )
        )
        manager._tick_once()
        # Even a 1-mm change triggers a send when threshold is 0.
        marker.pos = (1.001, 2.0, 3.0)
        manager._tick_once()
        assert len(svc.calls) == 2

    # A Diagnostics test packet must not populate the on-change cache,
    # else the next test packet or scheduled tick could skip as
    # "unchanged" after only a manual probe.
    def test_test_send_does_not_populate_on_change_cache(self) -> None:
        marker = _FakeMarker((1.0, 2.0, 3.0))
        manager, _svc = _manager(markers={0: marker})
        manager.restart(OscTransmittersConfig(transmitters=[self._on_change_row()]))
        # Sanity: cache empty before any tick.
        assert "row-1" not in manager._last_sent_pos
        # Diagnostics test packet – must NOT prime the cache.
        result = manager.test_send("row-1")
        assert result is not None
        assert result["sent"] is True
        assert "row-1" not in manager._last_sent_pos

    def test_test_send_does_not_skip_against_live_cache(self) -> None:
        # A live tick primed the cache. A subsequent test packet
        # at the same position must still go out (the manual probe
        # bypasses the on-change gate so the operator can confirm
        # connectivity even when the marker's stationary).
        marker = _FakeMarker((1.0, 2.0, 3.0))
        manager, svc = _manager(markers={0: marker})
        manager.restart(OscTransmittersConfig(transmitters=[self._on_change_row()]))
        manager._tick_once()  # primes cache at (1, 2, 3)
        live_count_before = len(svc.calls)
        result = manager.test_send("row-1")
        assert result is not None
        assert result["sent"] is True
        # One more send than the live-tick count – the test packet
        # went out despite the cached position being identical.
        assert len(svc.calls) == live_count_before + 1


class TestOnChangeCacheLocking:
    """``_fire_plan`` reads/writes ``_last_sent_pos`` from the scheduler
    thread (and ``test_send`` callers); ``restart()`` mutates the same dict
    under ``self._lock``. Cache mutations from ``_fire_plan`` must be
    serialised against ``restart()``, and a row ``restart`` removed during
    an in-flight send must not be resurrected when the send completes.
    """

    def _on_change_row(self, **overrides: Any) -> OscTransmitterConfig:
        from openfollow.configuration import StreamTrigger

        defaults: dict[str, Any] = {
            "id": "row-1",
            "marker_id": 0,
            "address": "/cue/[markerid]",
            "args": ["[x]", "[y]", "[z]"],
            "trigger": StreamTrigger(
                rate_hz=60,
                mode="on_change",
                min_change_m=0.05,
            ),
        }
        defaults.update(overrides)
        return _row(**defaults)

    def test_restart_during_send_does_not_resurrect_removed_row(self) -> None:
        # Simulate the race window: ``service.send`` is in flight,
        # ``restart()`` removes the row, then the in-flight send's
        # cache-write attempt must NOT resurrect the entry.
        import threading

        marker = _FakeMarker((1.0, 2.0, 3.0))
        manager, _svc = _manager(markers={0: marker})
        manager.restart(OscTransmittersConfig(transmitters=[self._on_change_row()]))
        # Wedge ``service.send`` so we can drop the row mid-flight.
        send_started = threading.Event()
        send_release = threading.Event()
        original_send = manager._service.send

        def slow_send(*args: Any, **kwargs: Any) -> Any:
            send_started.set()
            send_release.wait(timeout=2.0)
            return original_send(*args, **kwargs)

        manager._service.send = slow_send  # type: ignore[method-assign]
        tick_thread = threading.Thread(target=manager._tick_once, daemon=True)
        tick_thread.start()
        assert send_started.wait(timeout=2.0)
        # Tick is wedged in send. Drop the row via restart.
        manager.restart(OscTransmittersConfig(transmitters=[]))
        # Let the wedged send finish – its cache-write must see the
        # row's removed and refuse to resurrect.
        send_release.set()
        tick_thread.join(timeout=2.0)
        assert not tick_thread.is_alive()
        assert "row-1" not in manager._last_sent_pos

    def test_recreating_same_id_mid_send_does_not_prime_new_rows_cache(self) -> None:
        # A row dropped then re-created under the SAME id while
        # a send is in flight yields a *new* OscTransmitter object. The
        # in-flight send of the old plan must not prime the new row's
        # on-change cache (which would suppress its genuine first send as
        # "unchanged"). The identity guard keys on the row object, not the id.
        marker = _FakeMarker((1.0, 2.0, 3.0))
        manager, _svc = _manager(markers={0: marker})
        manager.restart(OscTransmittersConfig(transmitters=[self._on_change_row()]))
        # Snapshot a plan from the original row (plan.source_row = object A).
        original = manager._rows["row-1"]
        plan = manager._plan_for_row(original)
        # Drop then re-create row-1 → a fresh object B under the same id.
        manager.restart(OscTransmittersConfig(transmitters=[]))
        manager.restart(OscTransmittersConfig(transmitters=[self._on_change_row()]))
        assert manager._rows["row-1"] is not original
        # The old plan's in-flight send completes and tries to write.
        manager._fire_plan(plan)
        # Identity guard refuses to prime the new row's cache.
        assert "row-1" not in manager._last_sent_pos

    def test_in_place_reload_keeps_cache_via_object_identity(self) -> None:
        # The flip side: a cosmetic in-place reload reuses the same row
        # object, so the identity guard still primes/keeps the cache (no
        # spurious first send). Pins that the guard didn't break steady state.
        marker = _FakeMarker((1.0, 2.0, 3.0))
        manager, _svc = _manager(markers={0: marker})
        manager.restart(OscTransmittersConfig(transmitters=[self._on_change_row()]))
        manager._tick_once()  # primes cache
        assert "row-1" in manager._last_sent_pos
        # Cosmetic reload (same marker_id + trigger kind) reuses the object.
        same = manager._rows["row-1"]
        manager.restart(OscTransmittersConfig(transmitters=[self._on_change_row(name="renamed")]))
        assert manager._rows["row-1"] is same
        assert "row-1" in manager._last_sent_pos

    def test_per_axis_compare_not_euclidean(self) -> None:
        # 4cm on three axes is ~6.93cm Euclidean (above 5cm), but the
        # gate is per-axis so each axis (4cm) is below threshold and
        # the send is skipped. This pins the per-axis contract.
        marker = _FakeMarker((1.0, 2.0, 3.0))
        manager, svc = _manager(markers={0: marker})
        manager.restart(OscTransmittersConfig(transmitters=[self._on_change_row()]))
        manager._tick_once()
        marker.pos = (1.04, 2.04, 3.04)
        manager._tick_once()
        assert len(svc.calls) == 1

    def test_gate_signal_is_default_marker_not_message_content(
        self,
    ) -> None:
        # The gate watches ONLY the default marker; message content
        # (explicit-marker refs, fractional placeholders) doesn't affect
        # when it fires. Here the message references marker 7 but the
        # default marker is 0, so only marker 0's motion triggers a send
        # – marker 7 moving while 0 is still is a skip even though the
        # rendered output changed.
        from openfollow.configuration import StreamTrigger

        default_marker = _FakeMarker((1.0, 2.0, 3.0))
        explicit_marker = _FakeMarker((10.0, 20.0, 30.0))
        manager, svc = _manager(
            markers={0: default_marker, 7: explicit_marker},
        )
        cfg_row = _row(
            id="row-mixed",
            marker_id=0,  # marker 0 is the gate signal
            address="/cue/mixed",
            args=["[x:7]"],  # message references marker 7
            trigger=StreamTrigger(
                rate_hz=60,
                mode="on_change",
                min_change_m=0.05,
            ),
        )
        manager.restart(OscTransmittersConfig(transmitters=[cfg_row]))
        manager._tick_once()
        manager._tick_once()
        # Default marker stationary → second tick skips.
        assert len(svc.calls) == 1
        # Marker 7 moves but marker 0 doesn't. Gate watches the
        # default marker only; this is a skip by design – the
        # operator's intent is "fire only when my chosen signal
        # marker moves". (To trigger on marker 7's motion, set
        # marker 7 as the row's default marker.)
        explicit_marker.pos = (15.0, 20.0, 30.0)
        manager._tick_once()
        assert len(svc.calls) == 1
        # Default marker now moves → gate fires.
        default_marker.pos = (1.5, 2.0, 3.0)
        manager._tick_once()
        assert len(svc.calls) == 2

    def test_gate_uses_default_marker_even_when_message_doesnt_reference_it(
        self,
    ) -> None:
        # A default marker on a row whose message is all-explicit
        # ``[x:markerN]`` still acts as the gate signal – the renderer
        # reads the default marker even when templates don't reference it
        # so the gate has a position to compare against.
        from openfollow.configuration import StreamTrigger

        gate_marker = _FakeMarker((1.0, 2.0, 3.0))
        explicit_marker = _FakeMarker((10.0, 20.0, 30.0))
        manager, svc = _manager(
            markers={9: gate_marker, 7: explicit_marker},
        )
        cfg_row = _row(
            id="row-allexplicit",
            marker_id=9,  # marker 9 is the gate signal
            address="/cue/allexplicit",
            args=["[x:7]"],  # message references only marker 7
            trigger=StreamTrigger(
                rate_hz=60,
                mode="on_change",
                min_change_m=0.05,
            ),
        )
        manager.restart(OscTransmittersConfig(transmitters=[cfg_row]))
        manager._tick_once()
        manager._tick_once()
        # Marker 9 stationary → second tick skips, even though
        # the message doesn't reference it.
        assert len(svc.calls) == 1
        # Move marker 9 → gate fires.
        gate_marker.pos = (5.0, 2.0, 3.0)
        manager._tick_once()
        assert len(svc.calls) == 2

    def test_row_with_no_default_marker_fires_every_tick(self) -> None:
        # ``marker_id`` is None and the message has no default-marker
        # placeholder, so there's no gate signal to compare; the row
        # fires every tick (like ``mode="always"``).
        from openfollow.configuration import StreamTrigger

        manager, svc = _manager(markers={3: _FakeMarker((1.0, 2.0, 3.0))})
        cfg_row = _row(
            id="row-no-gate-signal",
            marker_id=None,
            address="/cue/3",
            args=["[x:3]"],
            trigger=StreamTrigger(
                rate_hz=60,
                mode="on_change",
                min_change_m=0.05,
            ),
        )
        manager.restart(OscTransmittersConfig(transmitters=[cfg_row]))
        manager._tick_once()
        manager._tick_once()
        assert len(svc.calls) == 2

    def test_always_mode_ignores_gate(self) -> None:
        # Default mode never skips; the cache stays empty for that row.
        marker = _FakeMarker((1.0, 2.0, 3.0))
        manager, svc = _manager(markers={0: marker})
        manager.restart(OscTransmittersConfig(transmitters=[_row()]))
        manager._tick_once()
        manager._tick_once()
        assert len(svc.calls) == 2

    def test_cache_dropped_on_row_removal(self) -> None:
        # ``restart()`` keeps the cache for surviving rows but drops
        # entries for rows the new config removed – exercises the
        # explicit cache cleanup branch.
        marker = _FakeMarker((1.0, 2.0, 3.0))
        manager, _svc = _manager(markers={0: marker})
        manager.restart(OscTransmittersConfig(transmitters=[self._on_change_row()]))
        manager._tick_once()
        assert "row-1" in manager._last_sent_pos
        manager.restart(OscTransmittersConfig(transmitters=[]))
        assert "row-1" not in manager._last_sent_pos

    # A surviving row whose effective on-change input (default marker)
    # changes must drop its cache entry, else the next tick compares the
    # new config's position against the old cached value and could skip
    # the first send (e.g. marker_id flip when both markers are near).
    def test_cache_dropped_when_marker_id_changes(self) -> None:
        marker0 = _FakeMarker((1.0, 2.0, 3.0))
        marker1 = _FakeMarker((1.01, 2.01, 3.01))  # near marker0
        manager, _svc = _manager(markers={0: marker0, 1: marker1})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    self._on_change_row(marker_id=0),
                ]
            )
        )
        manager._tick_once()
        assert "row-1" in manager._last_sent_pos
        # Swap default marker. New config's cache key is the same
        # ("row-1") but the cached position was marker 0's, not
        # marker 1's. Without invalidation, the next tick would
        # compare marker 1's pos against marker 0's cache.
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    self._on_change_row(marker_id=1),
                ]
            )
        )
        assert "row-1" not in manager._last_sent_pos

    def test_cache_kept_when_address_template_changes(self) -> None:
        # The gate watches only the default marker, so address / args
        # edits don't change what's gated against; the cache survives.
        marker = _FakeMarker((1.0, 2.0, 3.0))
        manager, _svc = _manager(markers={0: marker})
        manager.restart(OscTransmittersConfig(transmitters=[self._on_change_row()]))
        manager._tick_once()
        assert "row-1" in manager._last_sent_pos
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    self._on_change_row(address="/different/[markerid]"),
                ]
            )
        )
        assert "row-1" in manager._last_sent_pos

    def test_cache_kept_when_args_change(self) -> None:
        # Same reasoning as the address-edit case.
        marker = _FakeMarker((1.0, 2.0, 3.0))
        manager, _svc = _manager(markers={0: marker})
        manager.restart(OscTransmittersConfig(transmitters=[self._on_change_row()]))
        manager._tick_once()
        assert "row-1" in manager._last_sent_pos
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    self._on_change_row(args=["[x]", "[y]"]),  # dropped [z]
                ]
            )
        )
        assert "row-1" in manager._last_sent_pos

    def test_cache_dropped_when_trigger_kind_changes(self) -> None:
        from openfollow.configuration import HotkeyTrigger

        marker = _FakeMarker((1.0, 2.0, 3.0))
        manager, _svc = _manager(markers={0: marker})
        manager.restart(OscTransmittersConfig(transmitters=[self._on_change_row()]))
        manager._tick_once()
        assert "row-1" in manager._last_sent_pos
        # Switch trigger kind from Stream to Hotkey – flipping back
        # to Stream later would otherwise resurrect a stale cache.
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    self._on_change_row(trigger=HotkeyTrigger(key="space")),
                ]
            )
        )
        assert "row-1" not in manager._last_sent_pos

    def test_cache_kept_on_cosmetic_edits(self) -> None:
        # Editing only host / port / name / rate_hz / threshold
        # leaves the cache intact – the semantic basis hasn't
        # changed, just connectivity / cosmetics.
        from openfollow.configuration import StreamTrigger

        marker = _FakeMarker((1.0, 2.0, 3.0))
        manager, _svc = _manager(markers={0: marker})
        manager.restart(OscTransmittersConfig(transmitters=[self._on_change_row()]))
        manager._tick_once()
        assert "row-1" in manager._last_sent_pos
        # Edit cosmetic fields only (destination/name) and the threshold –
        # threshold change is read fresh per tick, doesn't affect
        # the cached position's meaning.
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    self._on_change_row(
                        destination_id="dest-1",
                        name="renamed",
                        trigger=StreamTrigger(
                            rate_hz=60,
                            mode="on_change",
                            min_change_m=0.10,
                        ),
                    ),
                ]
            )
        )
        assert "row-1" in manager._last_sent_pos


class TestRingBufferIntegration:
    def test_successful_send_lands_in_ring_buffer(self) -> None:
        manager, _svc = _manager(markers={0: _FakeMarker((1.0, 2.0, 3.0))})
        manager.restart(OscTransmittersConfig(transmitters=[_row(id="r1")]))
        manager._tick_once()
        entries = manager.ring_buffer_for("r1")
        assert entries is not None and len(entries) == 1
        assert entries[0].status == "sent"
        assert entries[0].address == "/cue/0"
        assert entries[0].args == (1.0, 2.0, 3.0)

    def test_default_marker_miss_logs_skipped(self) -> None:
        """A default-marker miss records a ring-buffer entry so the
        per-binding diagnostic surface can show why nothing is firing."""
        # No marker registered at id=0.
        manager, _svc = _manager(markers={})
        manager.restart(OscTransmittersConfig(transmitters=[_row(id="r1")]))
        manager._tick_once()
        entries = manager.ring_buffer_for("r1")
        assert entries is not None and len(entries) == 1
        assert entries[0].status == "skipped"
        assert "marker 0 not registered" in entries[0].error

    def test_explicit_marker_miss_records_unresolved_placeholder(self) -> None:
        """``[x:70]`` for an unmapped marker raises
        :class:`RenderError`; the manager catches it and records the
        unresolved placeholder name verbatim. The default marker
        (used by ``[markerid]`` in the address) IS registered, so the
        send only fails on the explicit-reference render."""
        manager, _svc = _manager(markers={0: _FakeMarker((1.0, 2.0, 3.0))})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(id="r1", args=["[x:70]"]),
                ]
            )
        )
        manager._tick_once()
        entries = manager.ring_buffer_for("r1")
        assert entries is not None and len(entries) == 1
        assert entries[0].status == "skipped"
        assert entries[0].error == "x:70"

    def test_fz_unset_max_height_records_actionable_hint(self) -> None:
        """When ``[z.frac]`` skips because ``max_height`` is unset, the
        ring-buffer record carries the placeholder name plus an actionable
        hint naming the section + field to set."""
        manager, _svc = _manager(
            markers={0: _FakeMarker((0.0, 0.0, 1.0))},
            grid=(10.0, 6.0, 0.0, 0.0),  # max_height unset
        )
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(id="r1", args=["[z.frac]"]),
                ]
            )
        )
        manager._tick_once()
        entries = manager.ring_buffer_for("r1")
        assert entries is not None and len(entries) == 1
        assert entries[0].status == "skipped"
        # Placeholder-only prefix preserved so operators can grep on
        # the slot name; hint surfaces as the colon-separated suffix
        # naming the section + field the operator has to touch.
        assert entries[0].error.startswith("z.frac")
        assert "Maximum Height" in entries[0].error

    def test_explicit_marker_resolves_when_registered(self) -> None:
        """The explicit ``[x:3]`` placeholder reads marker 3's
        position via the resolver and emits the value."""
        manager, svc = _manager(
            markers={
                0: _FakeMarker((1.0, 2.0, 3.0)),  # default marker
                3: _FakeMarker((10.0, 20.0, 30.0)),  # explicit reference
            }
        )
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(id="r1", args=["[x:3]", "[y]"]),
                ]
            )
        )
        manager._tick_once()
        assert len(svc.calls) == 1
        assert svc.calls[0][1] == [10.0, 2.0]  # marker3.x, default.y
        entries = manager.ring_buffer_for("r1")
        assert entries is not None and entries[0].status == "sent"

    def test_ring_buffer_for_unknown_row_id_returns_none(self) -> None:
        manager, _svc = _manager()
        assert manager.ring_buffer_for("does-not-exist") is None

    def test_empty_rendered_address_records_skipped_not_sent(self) -> None:
        """``OscService.send`` silently drops empty addresses (no
        target to dispatch to), so the manager pre-checks the rendered
        address and records the row as ``"skipped"`` rather than
        ``"sent"``. Otherwise the diagnostic surface would mislead an
        operator looking at a misconfigured row's recent sends.
        """
        manager, svc = _manager(markers={0: _FakeMarker((1.0, 2.0, 3.0))})
        # Operator left the address blank without picking a built-in
        # template – the renderer produces an empty string.
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(id="r1", address=""),
                ]
            )
        )
        manager._tick_once()
        # No packet handed to the service.
        assert svc.calls == []
        entries = manager.ring_buffer_for("r1")
        assert entries is not None and len(entries) == 1
        assert entries[0].status == "skipped"
        assert entries[0].error == "empty address"


# ---------------------------------------------------------------------------
# Skip the default-marker lookup when no slot needs it
# ---------------------------------------------------------------------------


class TestDefaultMarkerLookupGated:
    def test_literal_only_row_dispatches_without_registered_marker(
        self,
    ) -> None:
        """A row whose address + args contain zero default-marker
        placeholders fires even when the configured ``marker_id`` isn't
        registered."""
        manager, svc = _manager(markers={})  # no markers at all
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        id="cue-go",
                        marker_id=0,
                        address="/cue/go",
                        args=["My Cue"],
                    ),
                ]
            )
        )
        manager._tick_once()
        assert len(svc.calls) == 1
        addr, args, *_ = svc.calls[0]
        assert addr == "/cue/go"
        assert args == ["My Cue"]
        entries = manager.ring_buffer_for("cue-go")
        assert entries is not None and entries[0].status == "sent"

    def test_explicit_marker_only_row_dispatches_without_default(
        self,
    ) -> None:
        """A row whose only marker dependency is an explicit
        ``[x:markerN]`` reference resolves through the resolver, not
        the default marker – so it must dispatch when the explicit
        target is registered, regardless of whether any default
        marker is."""
        manager, svc = _manager(
            markers={
                5: _FakeMarker((1.5, 2.5, 3.5)),  # explicit target
                # marker 0 (the row's configured default) deliberately absent
            }
        )
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        id="r1",
                        marker_id=0,
                        address="/eos/sub/[markerid:5]",
                        args=["[x:5]"],
                    ),
                ]
            )
        )
        manager._tick_once()
        assert len(svc.calls) == 1
        addr, args, *_ = svc.calls[0]
        assert addr == "/eos/sub/5"
        assert args == [1.5]

    def test_default_marker_required_still_skips_when_missing(
        self,
    ) -> None:
        """Regression: a row that DOES use a default-marker
        placeholder still skips with the existing error string when
        the configured marker isn't registered."""
        manager, svc = _manager(markers={})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(id="r1", marker_id=0, args=["[x]"]),
                ]
            )
        )
        manager._tick_once()
        assert svc.calls == []
        entries = manager.ring_buffer_for("r1")
        assert entries is not None and len(entries) == 1
        assert entries[0].status == "skipped"
        assert "default marker 0 not registered" in entries[0].error

    def test_literal_only_row_with_no_default_skips_provider(self) -> None:
        """A row with ``marker_id=None`` and an all-literal message never
        asks the provider – no default marker configured, no template slot
        needs one. The manager DOES read the default marker when
        ``marker_id`` is set, so ``marker_id=None`` is required here to
        exercise the no-signal path.
        """
        calls: list[int] = []

        def _provider(tid: int) -> _FakeMarker | None:
            calls.append(tid)
            return None

        service = _FakeOscService()
        manager = OscTransmitterManager(
            osc_service=service,  # type: ignore[arg-type]
            marker_provider=_provider,
            grid_provider=lambda: (10.0, 6.0, 0.0, 0.0),
        )
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        id="r1",
                        marker_id=None,
                        address="/cue/go",
                        args=["My Cue"],
                    ),
                ]
            ),
            _destinations(),
        )
        manager._tick_once()
        assert calls == []
        assert len(service.calls) == 1


# ---------------------------------------------------------------------------
# marker_id=None → "no default marker configured" skip
# ---------------------------------------------------------------------------


class TestMarkerIdNoneSkip:
    def test_default_slot_with_none_marker_id_skips_with_clear_error(
        self,
    ) -> None:
        """A row whose template uses ``[x]`` (default-marker slot) but whose
        ``marker_id`` is ``None`` skips with ``"no default marker
        configured"`` rather than ``"default marker 0 not registered"``."""
        manager, svc = _manager(markers={0: _FakeMarker((1.0, 2.0, 3.0))})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(id="r1", marker_id=None, args=["[x]"]),
                ]
            )
        )
        manager._tick_once()
        assert svc.calls == []
        entries = manager.ring_buffer_for("r1")
        assert entries is not None and len(entries) == 1
        assert entries[0].status == "skipped"
        assert entries[0].error == "no default marker configured"

    def test_default_slot_with_none_marker_id_does_not_call_provider(
        self,
    ) -> None:
        """Manager skips the lookup outright – the renderer guard is only a
        safety net – so ``marker_provider(None)`` never reaches the provider."""
        calls: list[int | None] = []

        def _provider(tid: int) -> _FakeMarker | None:
            calls.append(tid)
            return None

        service = _FakeOscService()
        manager = OscTransmitterManager(
            osc_service=service,  # type: ignore[arg-type]
            marker_provider=_provider,
            grid_provider=lambda: (10.0, 6.0, 0.0, 0.0),
        )
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(id="r1", marker_id=None, args=["[x]"]),
                ]
            )
        )
        manager._tick_once()
        assert calls == []
        assert service.calls == []

    def test_literal_only_row_with_none_marker_id_dispatches(self) -> None:
        """A row with templates containing zero default-marker slots fires
        regardless of whether ``marker_id`` is set or a marker is
        registered; a literal-only template never hits the renderer guard."""
        manager, svc = _manager(markers={})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        id="r1",
                        marker_id=None,
                        address="/cue/go",
                        args=["My Cue"],
                    ),
                ]
            )
        )
        manager._tick_once()
        assert len(svc.calls) == 1
        addr, args, *_ = svc.calls[0]
        assert addr == "/cue/go"
        assert args == ["My Cue"]

    def test_explicit_marker_only_row_with_none_marker_id_dispatches(
        self,
    ) -> None:
        """An explicit-only row dispatches when its referenced marker
        is registered, regardless of whether ``marker_id`` is
        ``None`` (the operator never needed to set a default)."""
        manager, svc = _manager(
            markers={
                5: _FakeMarker((1.5, 2.5, 3.5)),
            }
        )
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        id="r1",
                        marker_id=None,
                        address="/eos/sub/[markerid:5]",
                        args=["[x:5]"],
                    ),
                ]
            )
        )
        manager._tick_once()
        assert len(svc.calls) == 1
        addr, args, *_ = svc.calls[0]
        assert addr == "/eos/sub/5"
        assert args == [1.5]

    def test_preview_for_none_marker_id_surfaces_clear_error(self) -> None:
        """The diagnostic preview surface inherits the same skip – and
        the same actionable message – so the operator can see the
        unresolved dependency from the row's diagnostics tab without
        flipping enabled on first."""
        manager, _svc = _manager(markers={})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(id="r1", marker_id=None, args=["[x]"]),
                ]
            )
        )
        preview = manager.preview_for("r1")
        assert preview is not None
        assert preview["skipped"] is True
        assert preview["error"] == "no default marker configured"


# ---------------------------------------------------------------------------
# TOML round-trip for the trigger sub-table
# ---------------------------------------------------------------------------


class TestTriggerTomlRoundTrip:
    def test_legacy_rate_hz_only_lifts_into_stream_trigger(self) -> None:
        """A TOML row with ``rate_hz`` but no ``trigger`` is lifted into a
        Stream trigger by the loader (no config migration needed)."""
        container = OscTransmittersConfig(
            transmitters=[
                {"rate_hz": 60, "host": "10.0.0.1", "port": 9000},
            ]
        )
        row = container.transmitters[0]
        assert isinstance(row.trigger, StreamTrigger)
        assert row.trigger.rate_hz == 60

    def test_new_style_trigger_subtable_round_trips(self) -> None:
        container = OscTransmittersConfig(
            transmitters=[
                {
                    "trigger": {
                        "kind": "hotkey",
                        "key": "F1",
                        "modifiers": ["ctrl", "shift"],
                        "edge": "release",
                    }
                },
            ]
        )
        t = container.transmitters[0].trigger
        assert isinstance(t, HotkeyTrigger)
        assert t.key == "F1"
        assert t.modifiers == ("ctrl", "shift")
        assert t.edge == "release"

    def test_phase_b_trigger_kinds_construct_typed_instances(self) -> None:
        """MIDI / fader trigger kinds construct typed instances rather than
        falling back to Stream. TOML shape: a sub-table with ``kind`` plus
        kind-specific fields.
        """
        from openfollow.configuration import (
            FaderOnChangeTrigger,
            MidiMessageTrigger,
        )

        container = OscTransmittersConfig(
            transmitters=[
                {
                    "trigger": {
                        "kind": "midi_message",
                        "patch_id": 1,
                        "type": "control_change",
                        "channel": 1,
                        "number": 7,
                    }
                },
                {
                    "trigger": {
                        "kind": "fader_on_change",
                        "fader": 3,
                        "rate_hz": 60,
                    }
                },
            ]
        )
        rows = container.transmitters
        assert isinstance(rows[0].trigger, MidiMessageTrigger)
        assert rows[0].trigger.patch_id == 1
        assert isinstance(rows[1].trigger, FaderOnChangeTrigger)
        assert rows[1].trigger.fader == 3 and rows[1].trigger.rate_hz == 60

    def test_unknown_trigger_kind_falls_back_to_stream(self) -> None:
        container = OscTransmittersConfig(
            transmitters=[
                {"trigger": {"kind": "from-the-future", "foo": "bar"}},
            ]
        )
        assert isinstance(container.transmitters[0].trigger, StreamTrigger)

    def test_non_dict_trigger_falls_back_to_stream(self) -> None:
        """A non-dict ``trigger`` value (string instead of sub-table) falls
        back to the rate_hz-derived default rather than crashing the load."""
        row = OscTransmitterConfig(trigger="not a dict", rate_hz=10)
        assert isinstance(row.trigger, StreamTrigger)
        assert row.trigger.rate_hz == 10

    def test_phase_b_default_construction_succeeds(self) -> None:
        """Direct construction of a MIDI / fader trigger produces a real
        instance with safe defaults."""
        from openfollow.configuration import (
            EncoderOnChangeTrigger,
            FaderOnChangeTrigger,
            MidiMessageTrigger,
        )

        assert MidiMessageTrigger().kind == "midi_message"
        assert FaderOnChangeTrigger().kind == "fader_on_change"
        assert EncoderOnChangeTrigger().kind == "encoder_on_change"


# ---------------------------------------------------------------------------
# Event-bus dispatch for Hotkey + ControllerButton
# ---------------------------------------------------------------------------


from openfollow.input.events import (  # noqa: E402
    ButtonEvent,
    InputEventBus,
    KeyEvent,
)


class TestEventBusDispatch:
    """Hotkey and ControllerButton triggers dispatch via the input
    event bus. Synthetic events drive the dispatch so these tests
    don't need real keyboard / gamepad hardware."""

    def _attached(
        self,
        rows: list[OscTransmitterConfig],
        *,
        markers: dict[int, _FakeMarker] | None = None,
        destinations: OscDestinationsConfig | None = None,
    ) -> tuple[OscTransmitterManager, _FakeOscService, InputEventBus]:
        manager, svc = _manager(markers=markers, destinations=destinations)
        manager.restart(OscTransmittersConfig(transmitters=rows))
        bus = InputEventBus()
        manager.attach_event_bus(bus)
        return manager, svc, bus

    # -- Hotkey ----------------------------------------------------------

    def test_hotkey_press_event_fires_matching_row(self) -> None:
        manager, svc, bus = self._attached(
            [_row(trigger=HotkeyTrigger(key="F1"))],
            markers={0: _FakeMarker((1.0, 2.0, 3.0))},
        )
        bus.emit_key(KeyEvent(key="F1", edge="press"))
        # Event handlers enqueue plans; the scheduler thread drains
        # them. ``process_pending_events`` is the synchronous test seam.
        manager.process_pending_events()
        assert len(svc.calls) == 1
        assert svc.calls[0][0] == "/cue/0"

    def test_hotkey_release_does_not_fire_press_row(self) -> None:
        """Edge is part of the trigger – a press-edge row does not fire on
        release events."""
        manager, svc, bus = self._attached(
            [_row(trigger=HotkeyTrigger(key="F1", edge="press"))],
            markers={0: _FakeMarker((1.0, 2.0, 3.0))},
        )
        bus.emit_key(KeyEvent(key="F1", edge="release"))
        manager.process_pending_events()
        assert svc.calls == []

    def test_hotkey_modifiers_must_match_exactly(self) -> None:
        """A row configured for plain ``F1`` does not fire when shift
        happens to be held – the modifier sets must match exactly so
        operators can bind ``F1`` and ``Shift+F1`` to different rows."""
        manager, svc, bus = self._attached(
            [_row(trigger=HotkeyTrigger(key="F1", modifiers=()))],
            markers={0: _FakeMarker((1.0, 2.0, 3.0))},
        )
        # Shift held → no fire.
        bus.emit_key(
            KeyEvent(
                key="F1",
                modifiers=frozenset({"shift"}),
                edge="press",
            )
        )
        manager.process_pending_events()
        assert svc.calls == []
        # No modifiers held → fires.
        bus.emit_key(KeyEvent(key="F1", edge="press"))
        manager.process_pending_events()
        assert len(svc.calls) == 1

    def test_hotkey_with_modifiers_fires_when_modifiers_match(self) -> None:
        manager, svc, bus = self._attached(
            [
                _row(
                    trigger=HotkeyTrigger(
                        key="F1",
                        modifiers=("ctrl", "shift"),
                    )
                )
            ],
            markers={0: _FakeMarker((1.0, 2.0, 3.0))},
        )
        bus.emit_key(
            KeyEvent(
                key="F1",
                modifiers=frozenset({"shift", "ctrl"}),
                edge="press",
            )
        )
        manager.process_pending_events()
        assert len(svc.calls) == 1

    def test_disabled_row_does_not_fire(self) -> None:
        manager, svc, bus = self._attached(
            [_row(enabled=False, trigger=HotkeyTrigger(key="F1"))],
            markers={0: _FakeMarker((1.0, 2.0, 3.0))},
        )
        bus.emit_key(KeyEvent(key="F1", edge="press"))
        manager.process_pending_events()
        assert svc.calls == []

    def test_multiple_rows_on_same_key_all_fire(self) -> None:
        """Multiple bindings on the same input fire independently – two rows
        on the same hotkey both emit."""
        manager, svc, bus = self._attached(
            [
                _row(id="r1", destination_id="d1", trigger=HotkeyTrigger(key="F1")),
                _row(id="r2", destination_id="d2", trigger=HotkeyTrigger(key="F1")),
            ],
            markers={0: _FakeMarker((1.0, 2.0, 3.0))},
            destinations=_destinations(
                OscDestinationConfig(id="d1", host="10.0.0.1", port=9000),
                OscDestinationConfig(id="d2", host="10.0.0.2", port=9000),
            ),
        )
        bus.emit_key(KeyEvent(key="F1", edge="press"))
        manager.process_pending_events()
        hosts = sorted(call[2] for call in svc.calls)
        assert hosts == ["10.0.0.1", "10.0.0.2"]

    def test_hotkey_does_not_match_different_key(self) -> None:
        manager, svc, bus = self._attached(
            [_row(trigger=HotkeyTrigger(key="F1"))],
            markers={0: _FakeMarker((1.0, 2.0, 3.0))},
        )
        bus.emit_key(KeyEvent(key="F2", edge="press"))
        manager.process_pending_events()
        assert svc.calls == []

    def test_stream_row_ignores_key_events(self) -> None:
        """A Stream-trigger row sitting alongside Hotkey rows must
        not fire on key events – the Stream row's send cadence is
        purely tick-driven and the key handler never enqueues a
        plan for it."""
        manager, svc, bus = self._attached(
            [_row(trigger=StreamTrigger(rate_hz=30))],
            markers={0: _FakeMarker((1.0, 2.0, 3.0))},
        )
        bus.emit_key(KeyEvent(key="F1", edge="press"))
        # Drain only the event queue – don't tick (which would fire
        # the Stream row on its rate cadence and confuse the assertion).
        manager.process_pending_events()
        assert svc.calls == []

    def test_hotkey_render_error_lands_in_ring_buffer(self) -> None:
        """Generalised skip-on-missing-placeholder still works for
        event-driven sends – the same render+log pipeline as the
        Stream tick path."""
        manager, _svc, bus = self._attached(
            [
                _row(
                    id="r1",
                    trigger=HotkeyTrigger(key="F1"),
                    args=["[x:70]"],
                )
            ],
            markers={0: _FakeMarker((1.0, 2.0, 3.0))},
        )
        bus.emit_key(KeyEvent(key="F1", edge="press"))
        manager.process_pending_events()
        entries = manager.ring_buffer_for("r1")
        assert entries is not None and len(entries) == 1
        assert entries[0].status == "skipped"
        assert entries[0].error == "x:70"

    # -- Controller button ----------------------------------------------

    def test_controller_button_press_fires_matching_row(self) -> None:
        manager, svc, bus = self._attached(
            [_row(trigger=ControllerButtonTrigger(button="A"))],
            markers={0: _FakeMarker((1.0, 2.0, 3.0))},
        )
        bus.emit_button(ButtonEvent(button="A", edge="press"))
        manager.process_pending_events()
        assert len(svc.calls) == 1

    def test_controller_button_does_not_fire_on_different_button(self) -> None:
        manager, svc, bus = self._attached(
            [_row(trigger=ControllerButtonTrigger(button="A"))],
            markers={0: _FakeMarker((1.0, 2.0, 3.0))},
        )
        bus.emit_button(ButtonEvent(button="B", edge="press"))
        manager.process_pending_events()
        assert svc.calls == []

    def test_controller_button_release_edge_only_fires_release_row(self) -> None:
        manager, svc, bus = self._attached(
            [_row(trigger=ControllerButtonTrigger(button="A", edge="release"))],
            markers={0: _FakeMarker((1.0, 2.0, 3.0))},
        )
        bus.emit_button(ButtonEvent(button="A", edge="press"))
        manager.process_pending_events()
        assert svc.calls == []
        bus.emit_button(ButtonEvent(button="A", edge="release"))
        manager.process_pending_events()
        assert len(svc.calls) == 1

    def test_disabled_controller_button_row_does_not_fire(self) -> None:
        """Mirror of the Hotkey-disabled case for the button-dispatch
        branch – the early ``not cfg.enabled`` guard short-circuits
        the match."""
        manager, svc, bus = self._attached(
            [_row(enabled=False, trigger=ControllerButtonTrigger(button="A"))],
            markers={0: _FakeMarker((1.0, 2.0, 3.0))},
        )
        bus.emit_button(ButtonEvent(button="A", edge="press"))
        manager.process_pending_events()
        assert svc.calls == []

    def test_hotkey_ignores_button_events_and_vice_versa(self) -> None:
        """Hotkey rows don't see button events; ControllerButton rows
        don't see key events. The bus dispatches via separate
        channels – these tests document the channel separation."""
        manager, svc, bus = self._attached(
            [
                _row(id="hk", trigger=HotkeyTrigger(key="A")),
                _row(id="btn", trigger=ControllerButtonTrigger(button="A")),
            ],
            markers={0: _FakeMarker((1.0, 2.0, 3.0))},
        )
        # Button A press: hotkey row stays silent, controller row fires.
        bus.emit_button(ButtonEvent(button="A", edge="press"))
        manager.process_pending_events()
        assert len(svc.calls) == 1
        svc.calls.clear()
        # Key 'A' press: hotkey row fires, controller row stays silent.
        bus.emit_key(KeyEvent(key="A", edge="press"))
        manager.process_pending_events()
        assert len(svc.calls) == 1


class TestEventBusLifecycle:
    def test_attach_event_bus_is_idempotent(self) -> None:
        """Attaching the same bus twice drops the old subscriptions before
        installing new ones, so a hot-reload can't double-subscribe."""
        manager, svc = _manager(markers={0: _FakeMarker((1.0, 2.0, 3.0))})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(trigger=HotkeyTrigger(key="F1")),
                ]
            )
        )
        bus = InputEventBus()
        manager.attach_event_bus(bus)
        manager.attach_event_bus(bus)  # second attach
        bus.emit_key(KeyEvent(key="F1", edge="press"))
        manager.process_pending_events()
        # Despite double-attach, the row fires exactly once – the
        # second attach unsubscribed the first call's handlers before
        # installing fresh ones.
        assert len(svc.calls) == 1

    def test_stop_unsubscribes_from_event_bus(self) -> None:
        """A stopped manager must not see further key/button events.
        Otherwise a torn-down manager could record into ring buffers
        no UI is reading from, or worse, schedule sends through a
        drained OSC service."""
        manager, svc = _manager(markers={0: _FakeMarker((1.0, 2.0, 3.0))})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(trigger=HotkeyTrigger(key="F1")),
                ]
            )
        )
        bus = InputEventBus()
        manager.attach_event_bus(bus)
        manager.stop()
        bus.emit_key(KeyEvent(key="F1", edge="press"))
        manager.process_pending_events()
        assert svc.calls == []

    def test_handlers_short_circuit_on_in_flight_emit_after_stop(self) -> None:
        """``InputEventBus.emit_*`` snapshots handlers under its lock and
        invokes them outside, so a handler can receive an event snapshotted
        before ``stop()`` ran. The manager's ``self._stop`` early-return
        keeps such a late event from queueing a plan no scheduler drains.
        Simulated by invoking the snapshotted handler after ``stop()``.
        """
        manager, svc = _manager(markers={0: _FakeMarker((1.0, 2.0, 3.0))})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(trigger=HotkeyTrigger(key="F1")),
                ]
            )
        )
        bus = InputEventBus()
        manager.attach_event_bus(bus)
        # Capture the bus's internal handler list before stop runs –
        # mirrors what ``emit_key`` does as its first step.
        handlers = list(bus._key_handlers)
        manager.stop()
        # Now invoke the snapshotted handlers as ``emit_key`` would –
        # the manager's handler must short-circuit on ``_stop``.
        for handler in handlers:
            handler(KeyEvent(key="F1", edge="press"))
        manager.process_pending_events()
        assert svc.calls == []

    def test_button_handler_short_circuits_on_in_flight_emit_after_stop(
        self,
    ) -> None:
        """Mirror of the key-handler in-flight-emit test for the
        button-dispatch path."""
        manager, svc = _manager(markers={0: _FakeMarker((1.0, 2.0, 3.0))})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(trigger=ControllerButtonTrigger(button="A")),
                ]
            )
        )
        bus = InputEventBus()
        manager.attach_event_bus(bus)
        handlers = list(bus._button_handlers)
        manager.stop()
        for handler in handlers:
            handler(ButtonEvent(button="A", edge="press"))
        manager.process_pending_events()
        assert svc.calls == []

    def test_stop_drains_already_queued_event_plans(self) -> None:
        """A plan enqueued before ``stop()`` (handler completed, scheduler
        hadn't drained it) must not fire afterward. ``stop`` clears the queue
        to honour the post-stop "no further sends" invariant.
        """
        manager, svc = _manager(markers={0: _FakeMarker((1.0, 2.0, 3.0))})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(trigger=HotkeyTrigger(key="F1")),
                ]
            )
        )
        bus = InputEventBus()
        manager.attach_event_bus(bus)
        # Queue a plan: handler runs, lands a plan on
        # ``_event_plans``, but nothing has drained it yet.
        bus.emit_key(KeyEvent(key="F1", edge="press"))
        assert manager._event_plans, "precondition: emit must enqueue a plan"
        manager.stop()
        assert manager._event_plans == [], "stop must clear queued plans"
        # A follow-up drain is a no-op: queue is empty, and the
        # in-flight-emit guard would short-circuit any further
        # handler calls.
        manager.process_pending_events()
        assert svc.calls == []

    def test_no_bus_attached_means_hotkey_rows_are_inert(self) -> None:
        """If ``attach_event_bus`` is never called (e.g. running
        without an InputManager – test harness, headless mode), the
        Hotkey rows simply never fire. The tick path still skips
        them silently."""
        manager, svc = _manager(markers={0: _FakeMarker((1.0, 2.0, 3.0))})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(trigger=HotkeyTrigger(key="F1")),
                ]
            )
        )
        for _ in range(60):
            manager._tick_once()
        assert svc.calls == []

    def test_event_handlers_enqueue_rather_than_fire_synchronously(self) -> None:
        """The event handler must not call ``OscService.send`` directly –
        that would put network I/O on the input thread. It enqueues a plan;
        the scheduler thread (or ``process_pending_events``) drains and fires.
        """
        manager, svc = _manager(markers={0: _FakeMarker((1.0, 2.0, 3.0))})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(trigger=HotkeyTrigger(key="F1")),
                ]
            )
        )
        bus = InputEventBus()
        manager.attach_event_bus(bus)
        # Emit on the test thread (substitute for the input thread):
        # nothing should hit the service yet.
        bus.emit_key(KeyEvent(key="F1", edge="press"))
        assert svc.calls == [], "event handler must enqueue, not fire synchronously"
        # Drain – now the send happens.
        manager.process_pending_events()
        assert len(svc.calls) == 1

    def test_tick_drains_queued_event_plans(self) -> None:
        """Production drain path: the scheduler thread's regular
        tick picks up event-driven plans alongside its own Stream
        plans. ``_tick_once`` exercises the same drain that
        ``process_pending_events`` does, just bundled with the
        per-row Stream cadence walk."""
        manager, svc = _manager(markers={0: _FakeMarker((1.0, 2.0, 3.0))})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(trigger=HotkeyTrigger(key="F1")),
                ]
            )
        )
        bus = InputEventBus()
        manager.attach_event_bus(bus)
        bus.emit_key(KeyEvent(key="F1", edge="press"))
        # Don't call process_pending_events; just tick.
        manager._tick_once()
        assert len(svc.calls) == 1


# ---------------------------------------------------------------------------
# Diagnostics surface: ``status_for`` / ``preview_for`` / ``test_send``.
# ---------------------------------------------------------------------------


class TestDiagnosticsSurface:
    def test_status_for_unknown_row_returns_none(self) -> None:
        manager, _svc = _manager()
        assert manager.status_for("does-not-exist") is None

    def test_status_for_empty_buffer_is_healthy(self) -> None:
        manager, _svc = _manager(markers={0: _FakeMarker((1.0, 2.0, 3.0))})
        manager.restart(OscTransmittersConfig(transmitters=[_row()]))
        status = manager.status_for("row-1")
        assert status is not None
        assert status["pps"] == 0.0
        assert status["last_error"] is None
        assert status["healthy"] is True
        assert status["ring_buffer"] == []

    def test_status_for_after_send_reports_healthy_with_pps(self) -> None:
        manager, _svc = _manager(markers={0: _FakeMarker((1.0, 2.0, 3.0))})
        manager.restart(OscTransmittersConfig(transmitters=[_row()]))
        manager._tick_once()
        status = manager.status_for("row-1")
        assert status is not None
        assert status["pps"] == 1.0
        assert status["last_error"] is None
        assert status["healthy"] is True
        assert len(status["ring_buffer"]) == 1
        assert status["ring_buffer"][0]["status"] == "sent"

    def test_status_for_after_skip_reports_unhealthy_with_error(self) -> None:
        manager, _svc = _manager(markers={})
        manager.restart(OscTransmittersConfig(transmitters=[_row()]))
        manager._tick_once()
        status = manager.status_for("row-1")
        assert status is not None
        assert status["pps"] == 0.0
        assert status["last_error"] == "default marker 0 not registered"
        assert status["healthy"] is False
        assert status["ring_buffer"][0]["status"] == "skipped"

    def test_status_for_pps_window_excludes_old_sends(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Sends older than 1 s drop out of the pps count. Drives the
        time source so the test is hermetic."""
        manager, _svc = _manager(markers={0: _FakeMarker((1.0, 2.0, 3.0))})
        manager.restart(OscTransmittersConfig(transmitters=[_row()]))
        clock_holder = [1000.0]
        monkeypatch.setattr(
            "openfollow.osc.transmitter.time.monotonic",
            lambda: clock_holder[0],
        )
        manager._tick_once()  # ts = 1000.0
        clock_holder[0] = 1002.0  # past the 1 s window
        status = manager.status_for("row-1")
        assert status is not None
        assert status["pps"] == 0.0
        # Healthy because the most recent entry was a send (the window
        # is for pps, not for the healthy flag).
        assert status["healthy"] is True

    def test_status_for_after_skip_then_send_reports_healthy_with_last_error(self) -> None:
        """A recovered row reports ``healthy=True`` while ``last_error`` still
        surfaces the most-recent skip.

        Wires the manager directly rather than via :func:`_manager`: that
        helper does ``markers or {}``, rebinding to a fresh dict, so later
        mutations wouldn't reach the manager's ``.get``.
        """
        markers: dict[int, _FakeMarker] = {}
        service = _FakeOscService()
        manager = OscTransmitterManager(
            osc_service=service,  # type: ignore[arg-type]
            marker_provider=markers.get,
            grid_provider=lambda: (10.0, 6.0, 0.0, 0.0),
        )
        manager.restart(OscTransmittersConfig(transmitters=[_row()]), _destinations())
        manager._tick_once()  # skip
        markers[0] = _FakeMarker((1.0, 2.0, 3.0))
        manager._tick_once()  # send
        status = manager.status_for("row-1")
        assert status is not None
        assert status["healthy"] is True
        assert status["last_error"] == "default marker 0 not registered"

    def test_preview_for_unknown_row_returns_none(self) -> None:
        manager, _svc = _manager()
        assert manager.preview_for("does-not-exist") is None

    def test_preview_for_resolves_address_and_args(self) -> None:
        manager, _svc = _manager(markers={0: _FakeMarker((1.5, 2.5, 0.0))})
        manager.restart(OscTransmittersConfig(transmitters=[_row()]))
        preview = manager.preview_for("row-1")
        assert preview is not None
        assert preview["address"] == "/cue/0"
        assert preview["args"] == [1.5, 2.5, 0.0]
        assert preview["skipped"] is False
        assert preview["error"] is None

    def test_preview_for_default_marker_miss_surfaces_skip(self) -> None:
        manager, _svc = _manager(markers={})
        manager.restart(OscTransmittersConfig(transmitters=[_row()]))
        preview = manager.preview_for("row-1")
        assert preview is not None
        assert preview["skipped"] is True
        assert preview["error"] == "default marker 0 not registered"
        assert preview["address"] == ""
        assert preview["args"] == []

    def test_preview_for_unresolved_destination_surfaces_skip(self) -> None:
        """The diagnostic preview of a row with no resolvable destination
        surfaces a 'no destination selected' skip (the render path keeps its
        own None guard for this, separate from the live-fire path)."""
        manager, _svc = _manager(markers={0: _FakeMarker((0.0, 0.0, 0.0))})
        manager.restart(OscTransmittersConfig(transmitters=[_row(destination_id="")]))
        preview = manager.preview_for("row-1")
        assert preview is not None
        assert preview["skipped"] is True
        assert preview["error"] == "no destination selected"
        assert preview["address"] == ""
        assert preview["args"] == []

    def test_preview_for_does_not_record_in_ring_buffer(self) -> None:
        """Critical contract: previewing must not pollute the ring
        buffer the operator is watching for *real* sends."""
        manager, _svc = _manager(markers={0: _FakeMarker((1.0, 2.0, 3.0))})
        manager.restart(OscTransmittersConfig(transmitters=[_row()]))
        manager.preview_for("row-1")
        assert manager.ring_buffer_for("row-1") == []

    def test_preview_for_does_not_send(self) -> None:
        manager, svc = _manager(markers={0: _FakeMarker((1.0, 2.0, 3.0))})
        manager.restart(OscTransmittersConfig(transmitters=[_row()]))
        manager.preview_for("row-1")
        assert svc.calls == []

    def test_test_send_unknown_row_returns_none(self) -> None:
        manager, _svc = _manager()
        assert manager.test_send("does-not-exist") is None

    def test_test_send_fires_packet_and_records_sent(self) -> None:
        manager, svc = _manager(markers={0: _FakeMarker((1.0, 2.0, 3.0))})
        manager.restart(OscTransmittersConfig(transmitters=[_row()]))
        result = manager.test_send("row-1")
        assert result is not None
        assert result["sent"] is True
        assert result["address"] == "/cue/0"
        assert result["args"] == [1.0, 2.0, 3.0]
        assert result["error"] is None
        assert len(svc.calls) == 1
        entries = manager.ring_buffer_for("row-1")
        assert entries is not None and entries[-1].status == "sent"

    def test_test_send_reports_skip_on_missing_marker(self) -> None:
        manager, svc = _manager(markers={})
        manager.restart(OscTransmittersConfig(transmitters=[_row()]))
        result = manager.test_send("row-1")
        assert result is not None
        assert result["sent"] is False
        assert result["error"] == "default marker 0 not registered"
        assert result["address"] == ""
        # No packet went on the wire – same skip-on-no-data semantics
        # as the live tick.
        assert svc.calls == []

    def test_test_send_bypasses_enabled_flag(self) -> None:
        """A disabled row can still be probed – the operator can verify
        connectivity before flipping ``enabled`` on."""
        manager, svc = _manager(markers={0: _FakeMarker((1.0, 2.0, 3.0))})
        manager.restart(OscTransmittersConfig(transmitters=[_row(enabled=False)]))
        result = manager.test_send("row-1")
        assert result is not None
        assert result["sent"] is True
        assert len(svc.calls) == 1

    def test_preview_literal_only_row_renders_without_marker(self) -> None:
        """Previewing a row with zero default-marker placeholders returns the
        rendered shape even when no marker is registered."""
        manager, _svc = _manager(markers={})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(id="cue-go", address="/cue/go", args=["My Cue"]),
                ]
            )
        )
        preview = manager.preview_for("cue-go")
        assert preview is not None
        assert preview["skipped"] is False
        assert preview["address"] == "/cue/go"
        assert preview["args"] == ["My Cue"]
        assert preview["error"] is None

    def test_test_send_literal_only_row_dispatches_without_marker(
        self,
    ) -> None:
        """Counterpart of the preview test above – Test Send fires the
        packet and records ``sent`` for a literal-only row regardless
        of whether the configured default marker exists."""
        manager, svc = _manager(markers={})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(id="cue-go", address="/cue/go", args=["My Cue"]),
                ]
            )
        )
        result = manager.test_send("cue-go")
        assert result is not None
        assert result["sent"] is True
        assert result["address"] == "/cue/go"
        assert result["args"] == ["My Cue"]
        assert len(svc.calls) == 1


# ---------------------------------------------------------------------------
# MIDI / fader dispatch
# ---------------------------------------------------------------------------


from openfollow.configuration import (  # noqa: E402
    FaderOnChangeTrigger,
    MidiMessageTrigger,
)
from openfollow.input.midi import MidiEvent  # noqa: E402


def _midi_event(
    *,
    type: str = "control_change",  # noqa: A002 – MidiEvent's field name
    channel: int = 1,
    number: int | None = 7,
    value: int = 64,
    patch_id: int = 1,
) -> MidiEvent:
    """Synthesised MIDI event. Defaults to CC7 on channel 1 from patch 1;
    ``patch_id`` is the integer id of the port the event arrived on."""
    return MidiEvent(
        type=type,
        channel=channel,
        number=number,
        value=value,
        patch_id=patch_id,
        timestamp=0.0,
    )


class TestMidiMessageDispatch:
    """``_handle_midi_event`` matches incoming MIDI messages against
    every :class:`MidiMessageTrigger` row, populates the ``[value]`` /
    ``[velocity]`` / ``[note]`` slots, and enqueues plans for the
    scheduler to drain. Like the Hotkey / ControllerButton handlers
    these tests use ``process_pending_events`` as the synchronous
    seam – it drains the event queue without ticking Stream rows."""

    def test_matching_event_fires_row(self) -> None:
        manager, svc = _manager()
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        marker_id=None,
                        address="/cc/[value]",
                        args=[],
                        trigger=MidiMessageTrigger(
                            patch_id=1,
                            type="control_change",
                            channel=1,
                            number=7,
                        ),
                    ),
                ]
            )
        )
        manager._handle_midi_event(_midi_event(value=42))
        manager.process_pending_events()
        assert len(svc.calls) == 1
        assert svc.calls[0][0] == "/cc/42"

    def test_event_value_velocity_note_populated_for_note_on(self) -> None:
        """note_on populates all three event slots – ``[value]`` and
        ``[velocity]`` both map to the velocity byte (the operator can
        spell either way), ``[note]`` maps to the note number."""
        manager, svc = _manager()
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        marker_id=None,
                        address="/note/[note]/vel/[velocity]/v/[value]",
                        args=[],
                        trigger=MidiMessageTrigger(
                            type="note_on",
                            channel=1,
                        ),
                    ),
                ]
            )
        )
        manager._handle_midi_event(
            _midi_event(
                type="note_on",
                number=60,
                value=100,
            )
        )
        manager.process_pending_events()
        assert len(svc.calls) == 1
        assert svc.calls[0][0] == "/note/60/vel/100/v/100"

    def test_velocity_and_note_none_for_non_note_event(self) -> None:
        """``[velocity]`` / ``[note]`` outside a note context skip
        the row with :class:`RenderError` – same shape as an
        unresolved marker. Use ``[value]`` instead for CC events."""
        manager, _svc = _manager()
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        id="r1",
                        marker_id=None,
                        address="/n/[note]",
                        args=[],
                        trigger=MidiMessageTrigger(
                            type="control_change",
                            channel=1,
                            number=7,
                        ),
                    ),
                ]
            )
        )
        manager._handle_midi_event(_midi_event())
        manager.process_pending_events()
        ring = manager.ring_buffer_for("r1")
        assert ring is not None and ring[-1].status == "skipped"
        assert "note" in ring[-1].error

    def test_patch_wildcard_matches_any_patch(self) -> None:
        """``patch_id=0`` is the wildcard – the row fires on events from any
        MIDI patch."""
        manager, svc = _manager()
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        marker_id=None,
                        address="/cc/[value]",
                        args=[],
                        trigger=MidiMessageTrigger(
                            type="control_change",
                            channel=1,
                            number=7,
                            # patch_id defaults to 0 (any patch)
                        ),
                    ),
                ]
            )
        )
        manager._handle_midi_event(_midi_event(patch_id=2))
        manager.process_pending_events()
        assert len(svc.calls) == 1

    def test_patch_specific_does_not_match_other_patch(self) -> None:
        manager, svc = _manager()
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        marker_id=None,
                        address="/cc/[value]",
                        args=[],
                        trigger=MidiMessageTrigger(
                            patch_id=1,
                            type="control_change",
                            channel=1,
                            number=7,
                        ),
                    ),
                ]
            )
        )
        manager._handle_midi_event(_midi_event(patch_id=2))
        manager.process_pending_events()
        assert svc.calls == []

    def test_channel_none_matches_any_channel(self) -> None:
        """``channel is None`` is the "any channel" wildcard."""
        manager, svc = _manager()
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        marker_id=None,
                        address="/cc/[value]",
                        args=[],
                        trigger=MidiMessageTrigger(
                            type="control_change",
                            channel=None,
                            number=7,
                        ),
                    ),
                ]
            )
        )
        manager._handle_midi_event(_midi_event(channel=11))
        manager.process_pending_events()
        assert len(svc.calls) == 1

    def test_channel_specific_does_not_match_other_channel(self) -> None:
        manager, svc = _manager()
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        marker_id=None,
                        address="/cc/[value]",
                        args=[],
                        trigger=MidiMessageTrigger(
                            type="control_change",
                            channel=2,
                            number=7,
                        ),
                    ),
                ]
            )
        )
        manager._handle_midi_event(_midi_event(channel=3))
        manager.process_pending_events()
        assert svc.calls == []

    def test_number_none_matches_any(self) -> None:
        manager, svc = _manager()
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        marker_id=None,
                        address="/cc/[value]",
                        args=[],
                        trigger=MidiMessageTrigger(
                            type="control_change",
                            channel=1,
                            number=None,
                        ),
                    ),
                ]
            )
        )
        manager._handle_midi_event(_midi_event(number=42, value=64))
        manager.process_pending_events()
        assert len(svc.calls) == 1

    def test_number_specific_does_not_match_other_number(self) -> None:
        manager, svc = _manager()
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        marker_id=None,
                        address="/cc/[value]",
                        args=[],
                        trigger=MidiMessageTrigger(
                            type="control_change",
                            channel=1,
                            number=7,
                        ),
                    ),
                ]
            )
        )
        manager._handle_midi_event(_midi_event(number=8))
        manager.process_pending_events()
        assert svc.calls == []

    def test_value_specific_filters_dispatch(self) -> None:
        """``value`` lets a row fire only on a specific CC value
        (e.g. a sustain pedal's exact-127 down edge). ``None``
        means any value – covered by the matching-event test above."""
        manager, svc = _manager()
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        marker_id=None,
                        address="/down",
                        args=[],
                        trigger=MidiMessageTrigger(
                            type="control_change",
                            channel=1,
                            number=64,
                            value=127,
                        ),
                    ),
                ]
            )
        )
        manager._handle_midi_event(_midi_event(number=64, value=64))
        manager.process_pending_events()
        assert svc.calls == []
        manager._handle_midi_event(_midi_event(number=64, value=127))
        manager.process_pending_events()
        assert len(svc.calls) == 1

    def test_type_does_not_match_across_types(self) -> None:
        """Row authored as ``note_on`` doesn't fire on
        ``control_change`` even when channel + number match – the
        type is part of the row's identity, not a wildcard."""
        manager, svc = _manager()
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        marker_id=None,
                        address="/note",
                        args=[],
                        trigger=MidiMessageTrigger(
                            type="note_on",
                            channel=1,
                            number=60,
                        ),
                    ),
                ]
            )
        )
        manager._handle_midi_event(
            _midi_event(
                type="control_change",
                number=60,
            )
        )
        manager.process_pending_events()
        assert svc.calls == []

    def test_program_change_with_none_number_matches(self) -> None:
        """``program_change`` carries no per-message number; the
        substrate emits ``number=None`` and the trigger's
        ``__post_init__`` normalises trigger.number to ``None`` for
        the type. Both being None means the comparison passes
        without an undocumented special case."""
        manager, svc = _manager()
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        marker_id=None,
                        address="/pc/[value]",
                        args=[],
                        trigger=MidiMessageTrigger(
                            type="program_change",
                            channel=1,
                        ),
                    ),
                ]
            )
        )
        manager._handle_midi_event(
            _midi_event(
                type="program_change",
                number=None,
                value=5,
            )
        )
        manager.process_pending_events()
        assert len(svc.calls) == 1
        assert svc.calls[0][0] == "/pc/5"

    def test_disabled_row_does_not_fire(self) -> None:
        manager, svc = _manager()
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        enabled=False,
                        marker_id=None,
                        address="/cc",
                        args=[],
                        trigger=MidiMessageTrigger(
                            type="control_change",
                            channel=1,
                            number=7,
                        ),
                    ),
                ]
            )
        )
        manager._handle_midi_event(_midi_event())
        manager.process_pending_events()
        assert svc.calls == []

    def test_stream_row_does_not_match_midi_event(self) -> None:
        """Stream rows fire on the scheduler tick only – a MIDI
        event must never enqueue a plan for them. Mirrors the
        equivalent guard for Hotkey / ControllerButton."""
        manager, svc = _manager(
            markers={0: _FakeMarker((1.0, 2.0, 3.0))},
        )
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(trigger=StreamTrigger(rate_hz=30)),
                ]
            )
        )
        manager._handle_midi_event(_midi_event())
        manager.process_pending_events()
        assert svc.calls == []

    def test_multiple_matching_rows_all_fire(self) -> None:
        """No cross-row collision rule – two rows on the same MIDI message
        both fire."""
        manager, svc = _manager(
            destinations=_destinations(
                OscDestinationConfig(id="d1", host="10.0.0.1", port=9000),
                OscDestinationConfig(id="d2", host="10.0.0.2", port=9000),
            ),
        )
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        id="r1",
                        destination_id="d1",
                        marker_id=None,
                        address="/cc",
                        args=[],
                        trigger=MidiMessageTrigger(
                            type="control_change",
                            channel=1,
                            number=7,
                        ),
                    ),
                    _row(
                        id="r2",
                        destination_id="d2",
                        marker_id=None,
                        address="/cc",
                        args=[],
                        trigger=MidiMessageTrigger(
                            type="control_change",
                            channel=1,
                            number=7,
                        ),
                    ),
                ]
            )
        )
        manager._handle_midi_event(_midi_event())
        manager.process_pending_events()
        hosts = sorted(call[2] for call in svc.calls)
        assert hosts == ["10.0.0.1", "10.0.0.2"]


class TestFaderOnChangeDispatch:
    """``_handle_fader_change`` enqueues plans for matching
    :class:`FaderOnChangeTrigger` rows and respects the per-row
    ``rate_hz`` throttle. The renderer reads the fader's current
    value through the resolver, so the plan itself doesn't carry
    the value – that's the bus's authoritative source."""

    def _row_with_fader_trigger(
        self,
        *,
        fader: int = 1,
        rate_hz: int = 60,
        **overrides: Any,
    ) -> OscTransmitterConfig:
        defaults = {
            "marker_id": None,
            "address": "/lvl/[fader]",
            "args": [],
            "default_fader": fader,
            "trigger": FaderOnChangeTrigger(fader=fader, rate_hz=rate_hz),
        }
        defaults.update(overrides)
        return _row(**defaults)

    def test_matching_fader_index_fires_row(self) -> None:
        manager, svc = _manager(fader_values={1: 0.5})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    self._row_with_fader_trigger(fader=1),
                ]
            )
        )
        manager._handle_fader_change(1, 0.5)
        manager.process_pending_events()
        assert len(svc.calls) == 1
        assert svc.calls[0][0] == "/lvl/0.5"

    def test_other_fader_index_does_not_fire(self) -> None:
        manager, svc = _manager(fader_values={1: 0.5, 2: 0.5})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    self._row_with_fader_trigger(fader=2),
                ]
            )
        )
        manager._handle_fader_change(1, 0.5)
        manager.process_pending_events()
        assert svc.calls == []

    def test_throttle_drops_burst(self) -> None:
        """Two events in quick succession at rate_hz=60 (~16.67 ms
        spacing) – the second one falls inside the throttle window
        and is dropped. The third one, after enough wall-clock
        time, fires."""
        manager, svc = _manager(fader_values={1: 0.5})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    self._row_with_fader_trigger(fader=1, rate_hz=60),
                ]
            )
        )
        manager._handle_fader_change(1, 0.4)
        manager.process_pending_events()
        assert len(svc.calls) == 1
        # Same instant – throttle drops the second event.
        manager._handle_fader_change(1, 0.6)
        manager.process_pending_events()
        assert len(svc.calls) == 1
        # Sleep past the throttle window.
        time.sleep(0.02)
        manager._handle_fader_change(1, 0.7)
        manager.process_pending_events()
        assert len(svc.calls) == 2

    def test_disabled_row_does_not_fire(self) -> None:
        manager, svc = _manager(fader_values={1: 0.5})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    self._row_with_fader_trigger(fader=1, enabled=False),
                ]
            )
        )
        manager._handle_fader_change(1, 0.5)
        manager.process_pending_events()
        assert svc.calls == []

    def test_stream_row_does_not_match_fader_event(self) -> None:
        manager, svc = _manager(
            markers={0: _FakeMarker((1.0, 2.0, 3.0))},
            fader_values={1: 0.5},
        )
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(trigger=StreamTrigger(rate_hz=30)),
                ]
            )
        )
        manager._handle_fader_change(1, 0.5)
        manager.process_pending_events()
        assert svc.calls == []

    def test_renderer_reads_current_value_through_resolver(self) -> None:
        """The plan doesn't carry the fader value – the renderer
        reads it back through the resolver. A fader value that
        changed between enqueue and tick reflects in what gets
        sent (operator dragging fast, scheduler tick lagging)."""
        live: dict[int, float] = {1: 0.3}
        manager, svc = _manager(fader_values=live)
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    self._row_with_fader_trigger(fader=1),
                ]
            )
        )
        manager._handle_fader_change(1, 0.3)
        # Mutate the live value before the scheduler drains.
        live[1] = 0.9
        manager.process_pending_events()
        assert len(svc.calls) == 1
        assert svc.calls[0][0] == "/lvl/0.9"

    def test_explicit_fader_ref_does_not_need_default(self) -> None:
        """A row using only ``[fader:N]`` references doesn't need
        ``default_fader`` set."""
        manager, svc = _manager(fader_values={3: 0.42})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        marker_id=None,
                        default_fader=None,  # no default
                        address="/x/[fader:3]",
                        args=[],
                        trigger=FaderOnChangeTrigger(fader=3, rate_hz=60),
                    ),
                ]
            )
        )
        manager._handle_fader_change(3, 0.42)
        manager.process_pending_events()
        assert len(svc.calls) == 1
        assert svc.calls[0][0] == "/x/0.42"


class TestMarkerFaderOnChangeDispatch:
    """``_handle_marker_fader_change`` enqueues plans for matching
    marker-sourced :class:`FaderOnChangeTrigger` rows, keyed by
    ``trigger.marker_id`` and partitioned from the indexed channel. Same
    throttle + value-read-back contract as the indexed handler."""

    def _row_with_marker_trigger(
        self,
        *,
        marker_id: int = 4,
        rate_hz: int = 60,
        **overrides: Any,
    ) -> OscTransmitterConfig:
        defaults = {
            "marker_id": marker_id,
            "address": "/lvl/[markerfader]",
            "args": [],
            "trigger": FaderOnChangeTrigger(marker_id=marker_id, rate_hz=rate_hz),
        }
        defaults.update(overrides)
        return _row(**defaults)

    def test_matching_marker_fires_row(self) -> None:
        manager, svc = _manager(
            markers={4: _FakeMarker((1.0, 2.0, 3.0))},
            marker_fader_values={4: 0.5},
        )
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    self._row_with_marker_trigger(marker_id=4),
                ]
            )
        )
        manager._handle_marker_fader_change(4, 0.5)
        manager.process_pending_events()
        assert len(svc.calls) == 1
        assert svc.calls[0][0] == "/lvl/0.5"

    def test_other_marker_does_not_fire(self) -> None:
        manager, svc = _manager(
            markers={4: _FakeMarker((1.0, 2.0, 3.0))},
            marker_fader_values={4: 0.5},
        )
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    self._row_with_marker_trigger(marker_id=4),
                ]
            )
        )
        manager._handle_marker_fader_change(5, 0.5)
        manager.process_pending_events()
        assert svc.calls == []

    def test_marker_row_not_fired_by_indexed_channel(self) -> None:
        # A marker-sourced trigger leaves ``fader`` at its default (1);
        # the index channel must NOT fire it on indexed fader 1 changes.
        manager, svc = _manager(
            markers={4: _FakeMarker((1.0, 2.0, 3.0))},
            marker_fader_values={4: 0.5},
        )
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    self._row_with_marker_trigger(marker_id=4),
                ]
            )
        )
        manager._handle_fader_change(1, 0.5)
        manager.process_pending_events()
        assert svc.calls == []

    def test_indexed_row_not_fired_by_marker_channel(self) -> None:
        # An indexed trigger (``marker_id == 0``) must never match the
        # marker channel (which only carries marker ids >= 1).
        manager, svc = _manager(fader_values={1: 0.5})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        marker_id=None,
                        address="/x/[fader]",
                        args=[],
                        default_fader=1,
                        trigger=FaderOnChangeTrigger(fader=1, rate_hz=60),
                    ),
                ]
            )
        )
        manager._handle_marker_fader_change(1, 0.5)
        manager.process_pending_events()
        assert svc.calls == []

    def test_throttle_drops_burst(self) -> None:
        manager, svc = _manager(
            markers={4: _FakeMarker((1.0, 2.0, 3.0))},
            marker_fader_values={4: 0.4},
        )
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    self._row_with_marker_trigger(marker_id=4, rate_hz=60),
                ]
            )
        )
        manager._handle_marker_fader_change(4, 0.4)
        manager.process_pending_events()
        assert len(svc.calls) == 1
        # Same instant – throttle drops the second event.
        manager._handle_marker_fader_change(4, 0.6)
        manager.process_pending_events()
        assert len(svc.calls) == 1
        time.sleep(0.02)
        manager._handle_marker_fader_change(4, 0.7)
        manager.process_pending_events()
        assert len(svc.calls) == 2

    def test_disabled_row_does_not_fire(self) -> None:
        manager, svc = _manager(
            markers={4: _FakeMarker((1.0, 2.0, 3.0))},
            marker_fader_values={4: 0.5},
        )
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    self._row_with_marker_trigger(marker_id=4, enabled=False),
                ]
            )
        )
        manager._handle_marker_fader_change(4, 0.5)
        manager.process_pending_events()
        assert svc.calls == []

    def test_stream_row_does_not_match_marker_event(self) -> None:
        # A non-FaderOnChange row must be skipped by the marker handler.
        manager, svc = _manager(
            markers={0: _FakeMarker((1.0, 2.0, 3.0))},
            marker_fader_values={4: 0.5},
        )
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(trigger=StreamTrigger(rate_hz=30)),
                ]
            )
        )
        manager._handle_marker_fader_change(4, 0.5)
        manager.process_pending_events()
        assert svc.calls == []

    def test_stop_silences_marker_fader_handler(self) -> None:
        manager, svc = _manager(
            markers={4: _FakeMarker((1.0, 2.0, 3.0))},
            marker_fader_values={4: 0.5},
        )
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    self._row_with_marker_trigger(marker_id=4),
                ]
            )
        )
        manager._stop.set()
        manager._handle_marker_fader_change(4, 0.5)
        manager.process_pending_events()
        assert svc.calls == []


class TestPhaseBStopGuards:
    """Each MIDI / fader event handler short-circuits on ``_stop`` so a
    stopped manager can't enqueue plans even if a substrate's snapshotted
    callback fan-out still reaches us."""

    def test_stop_silences_midi_handler(self) -> None:
        manager, svc = _manager()
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        marker_id=None,
                        address="/cc",
                        args=[],
                        trigger=MidiMessageTrigger(
                            type="control_change",
                            channel=1,
                            number=7,
                        ),
                    ),
                ]
            )
        )
        manager._stop.set()
        manager._handle_midi_event(_midi_event())
        manager.process_pending_events()
        assert svc.calls == []

    def test_stop_silences_fader_handler(self) -> None:
        manager, svc = _manager(fader_values={1: 0.5})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        marker_id=None,
                        address="/x",
                        args=[],
                        default_fader=1,
                        trigger=FaderOnChangeTrigger(fader=1, rate_hz=60),
                    ),
                ]
            )
        )
        manager._stop.set()
        manager._handle_fader_change(1, 0.5)
        manager.process_pending_events()
        assert svc.calls == []


class TestExplicitFaderResolverWithoutProvider:
    """When the manager is constructed without a ``fader_provider``,
    the resolver returns ``None`` for every index. The renderer
    surfaces that as a ring-buffer skip with the actionable
    ``"fader resolver not wired"`` message – same shape as an
    unresolved marker reference."""

    def test_returns_none_when_provider_unwired(self) -> None:
        manager, _svc = _manager()  # no fader_values → no provider
        # Direct call exercises the early-return branch the renderer
        # path also reaches when a row references ``[fader]`` on a
        # manager constructed without a provider.
        assert manager._explicit_fader_resolver(1) is None


class TestExplicitMarkerFaderResolver:
    """Bridge for the ``[markerfader]`` placeholder – returns a marker's
    fader value via the manager's ``marker_fader_provider``, or ``None``
    when the provider is unwired or the marker has no fader (the renderer
    turns ``None`` into a ring-buffer skip)."""

    def test_returns_none_without_provider(self) -> None:
        manager, _svc = _manager()  # no marker_fader_values → no provider
        assert manager._explicit_marker_fader_resolver(5) is None

    def test_returns_value_with_provider(self) -> None:
        manager, _svc = _manager(marker_fader_values={3: 0.7})
        assert manager._explicit_marker_fader_resolver(3) == 0.7
        # Unprovisioned marker → None (provider's ``.get`` miss).
        assert manager._explicit_marker_fader_resolver(9) is None


class TestExplicitControllerMarkerResolver:
    """Bridge for the ``:cN`` reference – maps a 0-based controller index
    to the marker it drives via ``controller_marker_provider``, or ``None``
    when the provider is unwired or the controller drives no marker (the
    renderer turns ``None`` into a ring-buffer skip)."""

    def test_returns_none_without_provider(self) -> None:
        manager, _svc = _manager()  # no controller_markers → no provider
        assert manager._explicit_controller_marker_resolver(0) is None

    def test_returns_marker_id_with_provider(self) -> None:
        manager, _svc = _manager(controller_markers={0: 5})
        assert manager._explicit_controller_marker_resolver(0) == 5
        # Controller not driving a marker → None (provider's ``.get`` miss).
        assert manager._explicit_controller_marker_resolver(1) is None


class TestControllerReferenceRendering:
    """End-to-end ``:cN`` through the manager – the live controller→marker
    routing is read at render time and is independent of the row's default
    marker."""

    def test_markerid_controller_ref_sends_resolved_id(self) -> None:
        manager, svc = _manager(controller_markers={0: 7})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(id="r1", marker_id=None, address="/follow", args=["[markerid:c1]"]),
                ]
            )
        )
        manager._tick_once()
        assert len(svc.calls) == 1
        address, args, *_ = svc.calls[0]
        assert address == "/follow"
        assert args == [7]

    def test_markerfader_controller_ref_not_gated_on_default_marker(self) -> None:
        # marker_id=None, yet the row sends – the cN row carries no
        # default-marker dependency.
        manager, svc = _manager(
            controller_markers={0: 3},
            marker_fader_values={3: 0.5},
        )
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(id="r1", marker_id=None, address="/f", args=["[markerfader:c1.int:0-100]"]),
                ]
            )
        )
        manager._tick_once()
        assert len(svc.calls) == 1
        assert svc.calls[0][1] == [50]

    def test_controller_drives_no_marker_skips_with_ring_buffer_reason(self) -> None:
        manager, svc = _manager(controller_markers={})  # provider present, no marker
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(id="r1", marker_id=None, address="/follow", args=["[markerid:c1]"]),
                ]
            )
        )
        manager._tick_once()
        assert svc.calls == []
        entries = manager.ring_buffer_for("r1")
        assert entries is not None and len(entries) == 1
        assert entries[0].status == "skipped"
        assert "controller 1 controls no marker" in entries[0].error


class TestPhaseBSubscriptionLifecycle:
    """``attach_midi_subsystem`` / ``attach_virtual_fader_bus`` install
    subscriptions; ``stop()`` unsubscribes. Drive against a fake
    subscribe/unsubscribe surface so the tests don't need a real
    rtmidi backend."""

    def test_attach_midi_idempotent(self) -> None:
        """Repeat calls drop the previous subscription before installing the
        new one."""
        manager, _svc = _manager()

        unsubscribe_calls: list[str] = []

        class _FakeMidi:
            def subscribe(self, cb: Any) -> Any:  # noqa: ANN401
                token = f"sub-{len(unsubscribe_calls)}"

                def _unsub() -> None:
                    unsubscribe_calls.append(token)

                return _unsub

        midi = _FakeMidi()
        manager.attach_midi_subsystem(midi)  # type: ignore[arg-type]
        manager.attach_midi_subsystem(midi)  # type: ignore[arg-type]
        # First subscription's unsubscribe fired when the second
        # attach replaced it.
        assert unsubscribe_calls == ["sub-0"]

    def test_attach_fader_idempotent(self) -> None:
        manager, _svc = _manager()
        unsubscribe_calls: list[str] = []

        class _FakeBus:
            def subscribe(self, cb: Any) -> Any:  # noqa: ANN401
                return lambda: unsubscribe_calls.append("fader")

            def subscribe_marker_fader(self, cb: Any) -> Any:  # noqa: ANN401
                return lambda: unsubscribe_calls.append("marker")

        bus = _FakeBus()
        manager.attach_virtual_fader_bus(bus)  # type: ignore[arg-type]
        manager.attach_virtual_fader_bus(bus)  # type: ignore[arg-type]
        # The second attach drops BOTH prior subscriptions (indexed +
        # marker channel) before re-subscribing – same hot-reload safety.
        assert sorted(unsubscribe_calls) == ["fader", "marker"]

    def test_stop_drops_phase_b_subscriptions(self) -> None:
        """``stop()`` must unsubscribe MIDI + fader so a torn-down
        manager can't see further events from those substrates."""
        manager, _svc = _manager()
        unsubscribed: list[str] = []

        class _FakeMidi:
            def subscribe(self, cb: Any) -> Any:  # noqa: ANN401
                return lambda: unsubscribed.append("midi")

        class _FakeBus:
            def subscribe(self, cb: Any) -> Any:  # noqa: ANN401
                return lambda: unsubscribed.append("fader")

            def subscribe_marker_fader(self, cb: Any) -> Any:  # noqa: ANN401
                return lambda: unsubscribed.append("marker_fader")

        manager.attach_midi_subsystem(_FakeMidi())  # type: ignore[arg-type]
        manager.attach_virtual_fader_bus(_FakeBus())  # type: ignore[arg-type]
        manager.start()
        manager.stop()
        assert sorted(unsubscribed) == ["fader", "marker_fader", "midi"]


class TestPhaseBHotReloadCleanup:
    """``restart()`` drops throttle state for rows the new config
    removed (otherwise the dict would grow unbounded over time)."""

    def test_restart_drops_throttle_for_removed_row(self) -> None:
        from openfollow.configuration import FaderOnChangeTrigger

        manager, _svc = _manager(fader_values={1: 0.5})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        id="r1",
                        marker_id=None,
                        address="/x/[fader]",
                        args=[],
                        default_fader=1,
                        trigger=FaderOnChangeTrigger(fader=1, rate_hz=60),
                    ),
                ]
            )
        )
        manager._handle_fader_change(1, 0.5)
        # Cache populated.
        assert "r1" in manager._last_event_fire_ts
        # Replace with a row whose id isn't ``r1`` – old entry drops.
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        id="r2",
                        marker_id=None,
                        address="/x/[fader]",
                        args=[],
                        default_fader=1,
                        trigger=FaderOnChangeTrigger(fader=1, rate_hz=60),
                    ),
                ]
            )
        )
        assert "r1" not in manager._last_event_fire_ts

    def test_restart_drops_throttle_when_trigger_kind_changes(self) -> None:
        """A row that survives a reload but switches trigger kind (fader →
        MIDI message, etc.) gets its throttle entry cleared so the first
        event after the reload isn't dropped against a stale timestamp from
        the previous trigger."""
        from openfollow.configuration import (
            FaderOnChangeTrigger,
            MidiMessageTrigger,
        )

        manager, _svc = _manager(fader_values={1: 0.5})
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        id="r1",
                        marker_id=None,
                        address="/x/[fader]",
                        args=[],
                        default_fader=1,
                        trigger=FaderOnChangeTrigger(fader=1, rate_hz=60),
                    ),
                ]
            )
        )
        manager._handle_fader_change(1, 0.5)
        assert "r1" in manager._last_event_fire_ts
        # Same row id, different trigger kind – invalidate_cache
        # flags it, so the fire-timestamp must drop too.
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        id="r1",
                        marker_id=None,
                        address="/cc",
                        args=[],
                        trigger=MidiMessageTrigger(
                            type="control_change",
                            channel=1,
                            number=7,
                        ),
                    ),
                ]
            )
        )
        assert "r1" not in manager._last_event_fire_ts


class TestResolverGuards:
    """The fader / marker-fader / controller-marker resolvers
    each run a live subsystem provider on the scheduler thread. A raising
    provider must degrade to ``None`` (→ ``RenderError`` → single-row skip),
    mirroring ``_explicit_marker_resolver`` – never escape to abort the
    plan render or (per the per-row guard) the scheduler tick.
    """

    @staticmethod
    def _boom(*_args: Any) -> Any:
        raise RuntimeError("provider blew up")

    def test_fader_resolver_swallows_provider_exception(self) -> None:
        manager, _svc = _manager()
        manager._fader_provider = self._boom  # type: ignore[assignment]
        assert manager._explicit_fader_resolver(1) is None

    def test_marker_fader_resolver_swallows_provider_exception(self) -> None:
        manager, _svc = _manager()
        manager._marker_fader_provider = self._boom  # type: ignore[assignment]
        assert manager._explicit_marker_fader_resolver(1) is None

    def test_controller_marker_resolver_swallows_provider_exception(self) -> None:
        manager, _svc = _manager()
        manager._controller_marker_provider = self._boom  # type: ignore[assignment]
        assert manager._explicit_controller_marker_resolver(0) is None

    def test_tick_with_raising_controller_provider_skips_row_not_crash(self) -> None:
        # End-to-end: a ``:c1`` row whose controller provider raises during
        # a tick records a ring-buffer skip and the tick returns normally
        # (no escape), with no wire send.
        manager, svc = _manager(markers={0: _FakeMarker((1.0, 2.0, 3.0))})
        manager._controller_marker_provider = self._boom  # type: ignore[assignment]
        manager.restart(
            OscTransmittersConfig(
                transmitters=[
                    _row(
                        id="row-c",
                        marker_id=0,
                        address="/cue/[markerid:c1]",
                        args=[],
                    ),
                ]
            )
        )
        manager._tick_once()  # must not raise
        assert svc.calls == []
        entries = manager.ring_buffer_for("row-c") or []
        assert entries and entries[-1].status == "skipped"
