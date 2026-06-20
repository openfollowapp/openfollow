# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit-level coverage for the :class:`InputManager` branches the
integration suite in :mod:`tests.test_input_manager` doesn't reach.

The integration suite covers the single-gamepad vs multi-gamepad
routing matrix and the gamepad button signal forwarding. This file
fills in:

* OSC enabled path: ``__init__`` spins up an ``OscInputHandler``;
  ``update`` applies flushed positions *only* to marker ids present in
  ``_controlled_ids`` (positions for non-controlled ids must not leak
  through); ``restart_osc`` tears down and rebuilds it.
* Out-of-range controller index: ``_gamepad_marker_id`` returns
  ``None`` → the matching move / reset must no-op instead of indexing
  past the end of ``_controlled_ids``.
* ``stop`` teardown: calls ``gamepad.stop`` always; calls ``osc.stop``
  only when an OSC handler was created (so stop is safe when OSC is
  disabled).
* ``OscInputHandler`` construction source-of-truth: the port +
  allowlist passed into the constructor come straight from
  ``AppConfig.osc`` rather than being hard-coded.
"""

from __future__ import annotations

from typing import Any

import pytest

import openfollow.input.input_manager as input_manager_module
from openfollow.configuration import AppConfig
from openfollow.input.gamepad import GamepadUpdate
from openfollow.input.input_manager import InputManager
from openfollow.operator_messages import OperatorMessageStore
from openfollow.psn.marker import Marker

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _DummyServer:
    def __init__(self) -> None:
        self.markers: dict[int, Marker] = {}

    def add_marker(self, marker_id: int) -> Marker:
        marker = Marker(marker_id, f"Marker {marker_id}")
        self.markers[marker_id] = marker
        return marker

    def get_marker(self, marker_id: int) -> Marker | None:
        return self.markers.get(marker_id)


class _FakeKeyboardHandler:
    next_velocity: tuple[float, float, float] | None = None

    def __init__(self, app, *, event_bus=None) -> None:  # noqa: ANN001
        # Mirror the real KeyboardHandler.__init__ signature including event_bus.
        # Event bus emission is tested separately against the real handler.
        self.app = app
        self.event_bus = event_bus

    def update(self, _dt: float) -> tuple[float, float, float] | None:
        return _FakeKeyboardHandler.next_velocity

    def is_connected(self) -> bool:
        return True


class _FakeGamepadHandler:
    next_update: GamepadUpdate = GamepadUpdate()
    joystick_indices: tuple[int, ...] = (0, 1, 2)
    effective_speeds: dict[int, float] = {}

    def __init__(  # noqa: ANN001
        self,
        app,
        *,
        event_bus=None,
        virtual_faders=None,
        marker_resolver=None,
    ) -> None:
        # Mirror the real GamepadHandler signature with virtual_faders and marker_resolver.
        # These are captured but unused in the test fake.
        self.app = app
        self.event_bus = event_bus
        self.virtual_faders = virtual_faders
        self.marker_resolver = marker_resolver
        self.joysticks = {idx: object() for idx in self.joystick_indices}
        self.stop_called = False

    def update(self, _dt: float) -> GamepadUpdate:
        return _FakeGamepadHandler.next_update

    def get_controller_info(self) -> list[dict]:
        return []

    def get_controller_effective_speeds(self) -> dict[int, float]:
        return dict(_FakeGamepadHandler.effective_speeds)

    def stop(self) -> None:
        self.stop_called = True


class _FakeMouseHandler:
    def __init__(self, app) -> None:  # noqa: ANN001
        self.app = app


class _FakeOscHandler:
    """Recording stand-in for ``OscInputHandler``.

    Captures construction kwargs + start/stop calls so tests can assert
    that the manager wires config → handler correctly, and that
    ``restart_osc`` swaps cleanly.
    """

    instances: list[_FakeOscHandler] = []
    next_updates: dict[int, tuple[float, float, float]] = {}

    def __init__(
        self,
        service: Any,
        *,
        port: int,
        allowed_sender_ips: list[str] | None = None,
        multicast_group: str = "",
    ) -> None:
        self.service = service
        self.port = port
        self.allowed_sender_ips = list(allowed_sender_ips or [])
        self.multicast_group = multicast_group
        self.started = False
        self.stopped = False
        _FakeOscHandler.instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def flush_updates(self) -> dict[int, dict[str, float]]:
        # Deep copy + clear mirrors the real adapter's queue semantics:
        # each flush drains the pending state. Without the clear, a
        # second ``InputManager.update()`` in the same test would
        # silently re-apply the same OSC packet.
        snapshot = {tid: dict(axes) for tid, axes in _FakeOscHandler.next_updates.items()}
        _FakeOscHandler.next_updates = {}
        return snapshot


class _FakeOperatorMessageAdapter:
    """Recording stand-in for ``OperatorMessageOscAdapter``.

    Captures the store, controlled-ids callback, and start/stop calls.
    """

    instances: list[_FakeOperatorMessageAdapter] = []

    def __init__(
        self,
        service: Any,
        store: Any,
        *,
        get_controlled_marker_ids: Any,
        route_by_marker: bool = True,
    ) -> None:
        self.service = service
        self.store = store
        self.get_controlled_marker_ids = get_controlled_marker_ids
        self.route_by_marker = route_by_marker
        self.started = False
        self.stopped = False
        _FakeOperatorMessageAdapter.instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class _DummyRuntimeServices:
    """Minimal stand-in for ``AppRuntimeServices`` providing the fields
    ``InputManager`` reads – the shared ``OscService`` and the operator-
    message store. Tests monkeypatch the adapter constructors, so these
    sentinels never reach network code."""

    def __init__(self) -> None:
        self._osc_service = object()  # opaque sentinel
        # Real store: _rebuild_operator_message_handler calls clear_all() on it.
        self._operator_message_store = OperatorMessageStore()
        # Disable the virtual faders integrator for focused input-pipeline tests.
        self._virtual_faders = None


class _DummyApp:
    def __init__(self, *, osc_enabled: bool = False) -> None:
        self._config = AppConfig()
        self._config.osc.enabled = osc_enabled
        self._config.osc.port = 9001
        self._config.osc.allowed_sender_ips = ("10.0.0.1",)
        self._config.marker.default_pos_x = 5.0
        self._config.marker.default_pos_y = 6.0
        self._config.marker.default_pos_z = 7.0

        self._controlled_ids = [10, 11]
        self._selected_id = 10

        self._server = _DummyServer()
        self._server.add_marker(10).set_pos(0.0, 0.0, 0.0)
        self._server.add_marker(11).set_pos(0.0, 0.0, 0.0)
        self._runtime_services = _DummyRuntimeServices()

    def _get_default_marker_position(self) -> tuple[float, float, float]:
        cfg = self._config.marker
        return (cfg.default_pos_x, cfg.default_pos_y, cfg.default_pos_z)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _patch_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)
    monkeypatch.setattr(input_manager_module, "MouseHandler", _FakeMouseHandler)
    monkeypatch.setattr(input_manager_module, "OscMarkerAdapter", _FakeOscHandler)
    monkeypatch.setattr(
        input_manager_module,
        "OperatorMessageOscAdapter",
        _FakeOperatorMessageAdapter,
    )
    _FakeOscHandler.instances = []
    _FakeOscHandler.next_updates = {}
    _FakeOperatorMessageAdapter.instances = []
    _FakeKeyboardHandler.next_velocity = None
    _FakeGamepadHandler.next_update = GamepadUpdate()
    _FakeGamepadHandler.joystick_indices = (0, 1, 2)
    _FakeGamepadHandler.effective_speeds = {}


# --------------------------------------------------------------------------- #
# OSC construction + update path
# --------------------------------------------------------------------------- #


class TestOscInit:
    def test_disabled_osc_leaves_handler_none(self) -> None:
        app = _DummyApp(osc_enabled=False)
        manager = InputManager(app)
        assert manager.osc_handler is None
        assert _FakeOscHandler.instances == []

    def test_enabled_osc_starts_handler_with_config_values(self) -> None:
        app = _DummyApp(osc_enabled=True)
        manager = InputManager(app)
        assert manager.osc_handler is not None
        assert len(_FakeOscHandler.instances) == 1
        handler = _FakeOscHandler.instances[0]
        assert handler.port == 9001
        assert handler.allowed_sender_ips == ["10.0.0.1"]
        assert handler.started is True

    def test_bind_failure_logs_and_disables_osc_without_crashing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:

        class _FailingOscHandler(_FakeOscHandler):
            def start(self) -> None:
                raise OSError("port 9001 already in use")

        monkeypatch.setattr(
            input_manager_module,
            "OscMarkerAdapter",
            _FailingOscHandler,
        )
        app = _DummyApp(osc_enabled=True)
        with caplog.at_level("ERROR"):
            manager = InputManager(app)
        assert manager.osc_handler is None
        assert any("OSC input bind failed" in rec.message for rec in caplog.records)


class TestOscUpdateApplication:
    def test_flushed_positions_apply_to_controlled_markers(self) -> None:
        _FakeOscHandler.next_updates = {10: {"x": 1.5, "y": 2.5, "z": 3.5}}
        app = _DummyApp(osc_enabled=True)
        app._server.get_marker(10).set_pos(99.0, 99.0, 99.0)
        manager = InputManager(app)
        manager.update(0.016)
        assert app._server.get_marker(10).pos == pytest.approx((1.5, 2.5, 3.5))

    def test_per_axis_update_merges_with_current_position(self) -> None:
        """Per-axis OSC writes (``/marker/<id>/x`` etc.) only set the
        axes they carry; un-set axes keep whatever the marker's
        current position is."""
        _FakeOscHandler.next_updates = {10: {"y": 42.0}}
        app = _DummyApp(osc_enabled=True)
        app._server.get_marker(10).set_pos(1.0, 2.0, 3.0)
        manager = InputManager(app)
        manager.update(0.016)
        # Y was overwritten; X and Z came from the marker's prior pos.
        assert app._server.get_marker(10).pos == pytest.approx((1.0, 42.0, 3.0))

    def test_per_axis_update_with_all_three_axes_overwrites_everything(self) -> None:
        """Sparse dict carrying every axis is equivalent to a triple –
        no merge with the current pos is needed for the unset case
        because nothing is unset."""
        _FakeOscHandler.next_updates = {
            10: {"x": 7.0, "y": 8.0, "z": 9.0},
        }
        app = _DummyApp(osc_enabled=True)
        app._server.get_marker(10).set_pos(1.0, 2.0, 3.0)
        manager = InputManager(app)
        manager.update(0.016)
        assert app._server.get_marker(10).pos == pytest.approx((7.0, 8.0, 9.0))

    def test_positions_for_non_controlled_ids_are_dropped(self) -> None:
        """Without this gate, a message for marker 99 would create or
        overwrite it via ``get_marker`` – tests for a marker that
        isn't locally controlled but happens to exist in the server.
        """
        _FakeOscHandler.next_updates = {99: {"x": 1.0, "y": 2.0, "z": 3.0}}
        app = _DummyApp(osc_enabled=True)
        # Plant a marker 99 the app isn't controlling – OSC must not
        # reach it because 99 isn't in ``_controlled_ids``.
        app._server.add_marker(99).set_pos(50.0, 50.0, 50.0)
        manager = InputManager(app)
        manager.update(0.016)
        assert app._server.get_marker(99).pos == pytest.approx((50.0, 50.0, 50.0))

    def test_missing_marker_on_server_is_silently_skipped(self) -> None:
        _FakeOscHandler.next_updates = {10: {"x": 1.0, "y": 2.0, "z": 3.0}}
        app = _DummyApp(osc_enabled=True)
        app._server.markers.pop(10)  # pretend it was never added
        manager = InputManager(app)
        manager.update(0.016)  # must not raise


# --------------------------------------------------------------------------- #
# Out-of-range controller index → None marker_id → skip
# --------------------------------------------------------------------------- #


class TestOutOfRangeControllerIndex:
    def test_move_from_orphan_controller_index_is_skipped(self) -> None:
        _FakeGamepadHandler.joystick_indices = (0, 1, 5)
        _FakeGamepadHandler.next_update = GamepadUpdate(
            movements={5: (1.0, 0.0, 0.0)},
        )
        app = _DummyApp(osc_enabled=False)
        manager = InputManager(app)
        manager.update(1.0)
        # No marker moved – both remain at their initial (0, 0, 0).
        assert app._server.get_marker(10).pos == pytest.approx((0.0, 0.0, 0.0))
        assert app._server.get_marker(11).pos == pytest.approx((0.0, 0.0, 0.0))

    def test_reset_from_orphan_controller_index_is_skipped(self) -> None:
        _FakeGamepadHandler.joystick_indices = (0, 1, 5)
        _FakeGamepadHandler.next_update = GamepadUpdate(resets={5})
        app = _DummyApp(osc_enabled=False)
        app._server.get_marker(10).set_pos(1.0, 1.0, 1.0)
        app._server.get_marker(11).set_pos(2.0, 2.0, 2.0)
        manager = InputManager(app)
        manager.update(0.016)
        # Nothing got reset to the default pos.
        assert app._server.get_marker(10).pos == pytest.approx((1.0, 1.0, 1.0))
        assert app._server.get_marker(11).pos == pytest.approx((2.0, 2.0, 2.0))

    def test_move_for_missing_marker_is_skipped(self) -> None:
        _FakeGamepadHandler.joystick_indices = (0, 1)
        _FakeGamepadHandler.next_update = GamepadUpdate(
            movements={0: (1.0, 0.0, 0.0)},
        )
        app = _DummyApp(osc_enabled=False)
        app._server.markers.pop(10)
        manager = InputManager(app)
        manager.update(1.0)  # must not raise

    def test_keyboard_velocity_on_missing_selected_marker_is_skipped(self) -> None:
        """Keyboard movement path ``if marker:`` False side – the
        selected_id is set and keyboard_enabled, but the server doesn't
        hold a marker for that id (happens briefly during a config
        reload that renames the controlled ids).
        """
        _FakeKeyboardHandler.next_velocity = (1.0, 0.0, 0.0)
        app = _DummyApp(osc_enabled=False)
        app._server.markers.pop(10)  # selected marker missing
        manager = InputManager(app)
        manager.update(1.0)  # must not raise

    def test_reset_for_missing_marker_is_skipped(self) -> None:
        """Reset-path ``if marker:`` False side: the gamepad slot maps
        to a valid id but the server has no such marker registered.
        """
        _FakeGamepadHandler.joystick_indices = (0, 1)
        _FakeGamepadHandler.next_update = GamepadUpdate(resets={0})
        app = _DummyApp(osc_enabled=False)
        app._server.markers.pop(10)
        manager = InputManager(app)
        manager.update(0.016)  # must not raise


# --------------------------------------------------------------------------- #
# restart_osc
# --------------------------------------------------------------------------- #


class TestRestartOsc:
    def test_enabled_to_disabled_stops_and_clears(self) -> None:
        app = _DummyApp(osc_enabled=True)
        manager = InputManager(app)
        original = manager.osc_handler
        assert original is not None

        manager.restart_osc(enabled=False, port=9001)

        assert original.stopped is True
        assert manager.osc_handler is None

    def test_disabled_to_enabled_starts_fresh_handler(self) -> None:
        app = _DummyApp(osc_enabled=False)
        manager = InputManager(app)
        assert manager.osc_handler is None
        assert _FakeOscHandler.instances == []

        manager.restart_osc(enabled=True, port=9010, allowed_sender_ips=["172.16.0.1"])

        assert manager.osc_handler is not None
        assert len(_FakeOscHandler.instances) == 1
        assert _FakeOscHandler.instances[0].port == 9010
        assert _FakeOscHandler.instances[0].allowed_sender_ips == ["172.16.0.1"]
        assert _FakeOscHandler.instances[0].started is True

    def test_enabled_to_enabled_rebuilds_handler(self) -> None:
        app = _DummyApp(osc_enabled=True)
        manager = InputManager(app)
        first = manager.osc_handler
        assert first is not None

        manager.restart_osc(enabled=True, port=9999, allowed_sender_ips=["10.0.0.9"])

        assert first.stopped is True
        assert manager.osc_handler is not first
        assert len(_FakeOscHandler.instances) == 2
        assert _FakeOscHandler.instances[1].port == 9999
        assert _FakeOscHandler.instances[1].started is True

    def test_none_allowlist_collapses_to_empty_list(self) -> None:
        app = _DummyApp(osc_enabled=False)
        manager = InputManager(app)
        manager.restart_osc(enabled=True, port=9001)
        assert _FakeOscHandler.instances[0].allowed_sender_ips == []

    def test_bind_failure_logs_and_leaves_handler_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Same hot-reload-safe behaviour as ``__init__`` – a port-busy
        bind on the new handler logs an error and clears
        ``osc_handler`` instead of crashing the dispatcher."""
        # Successful first start in __init__, then make subsequent
        # ``start()`` calls (the restart path) raise.
        app = _DummyApp(osc_enabled=False)
        manager = InputManager(app)

        class _FailingOscHandler(_FakeOscHandler):
            def start(self) -> None:
                raise OSError("port 9999 already in use")

        monkeypatch.setattr(
            input_manager_module,
            "OscMarkerAdapter",
            _FailingOscHandler,
        )
        with caplog.at_level("ERROR"):
            manager.restart_osc(enabled=True, port=9999)
        assert manager.osc_handler is None
        assert any("OSC input restart failed" in rec.message for rec in caplog.records)


# --------------------------------------------------------------------------- #
# stop
# --------------------------------------------------------------------------- #


class TestStop:
    def test_stops_gamepad_and_osc_when_enabled(self) -> None:
        app = _DummyApp(osc_enabled=True)
        manager = InputManager(app)
        osc = manager.osc_handler
        manager.stop()
        assert manager.gamepad_handler.stop_called is True
        assert osc is not None and osc.stopped is True

    def test_stops_gamepad_only_when_osc_disabled(self) -> None:
        """Previously this path was uncovered – without the ``if osc is
        not None`` guard, stop() would AttributeError when OSC was
        disabled (which is the default for developers running the app
        without configuring the OSC input).
        """
        app = _DummyApp(osc_enabled=False)
        manager = InputManager(app)
        manager.stop()
        assert manager.gamepad_handler.stop_called is True


# --------------------------------------------------------------------------- #
# Multicast group threading
# --------------------------------------------------------------------------- #


class TestMulticastGroup:
    def test_init_threads_multicast_group_to_adapter(self) -> None:
        app = _DummyApp(osc_enabled=True)
        app._config.osc.multicast_group = "239.10.10.10"
        InputManager(app)
        assert _FakeOscHandler.instances[0].multicast_group == "239.10.10.10"

    def test_restart_osc_threads_multicast_group(self) -> None:
        app = _DummyApp(osc_enabled=False)
        manager = InputManager(app)
        manager.restart_osc(enabled=True, port=9001, multicast_group="239.1.2.3")
        assert _FakeOscHandler.instances[0].multicast_group == "239.1.2.3"

    def test_default_multicast_group(self) -> None:
        app = _DummyApp(osc_enabled=True)
        InputManager(app)
        assert _FakeOscHandler.instances[0].multicast_group == "239.20.20.20"


# --------------------------------------------------------------------------- #
# Operator-message adapter wiring
# --------------------------------------------------------------------------- #


class TestOperatorMessageWiring:
    def test_created_when_osc_and_operator_messages_enabled(self) -> None:
        app = _DummyApp(osc_enabled=True)
        app._config.operator_messages.enabled = True  # default is off
        manager = InputManager(app)
        assert manager.operator_message_handler is not None
        assert len(_FakeOperatorMessageAdapter.instances) == 1
        adapter = _FakeOperatorMessageAdapter.instances[0]
        assert adapter.started is True
        # Store comes from runtime services; routing callback reads live ids.
        assert adapter.store is app._runtime_services._operator_message_store
        assert adapter.get_controlled_marker_ids() == set(app._controlled_ids)
        # Default config routes by marker.
        assert adapter.route_by_marker is True

    def test_route_by_marker_config_threaded_into_adapter(self) -> None:
        # The manager passes the section's route_by_marker through to the adapter.
        app = _DummyApp(osc_enabled=True)
        app._config.operator_messages.enabled = True
        app._config.operator_messages.route_by_marker = False
        InputManager(app)
        assert _FakeOperatorMessageAdapter.instances[0].route_by_marker is False

    def test_not_created_when_operator_messages_disabled(self) -> None:
        app = _DummyApp(osc_enabled=True)
        app._config.operator_messages.enabled = False
        manager = InputManager(app)
        assert manager.operator_message_handler is None
        assert _FakeOperatorMessageAdapter.instances == []

    def test_not_created_when_osc_disabled(self) -> None:
        # No listener → no adapter, even with the section enabled.
        app = _DummyApp(osc_enabled=False)
        app._config.operator_messages.enabled = True
        manager = InputManager(app)
        assert manager.operator_message_handler is None
        assert _FakeOperatorMessageAdapter.instances == []

    def test_restart_osc_rebuilds_operator_message_handler(self) -> None:
        app = _DummyApp(osc_enabled=True)
        app._config.operator_messages.enabled = True
        manager = InputManager(app)
        first = manager.operator_message_handler
        assert first is not None
        manager.restart_osc(enabled=True, port=9999)
        assert first.stopped is True
        assert manager.operator_message_handler is not None
        assert manager.operator_message_handler is not first

    def test_restart_osc_disable_drops_operator_message_handler(self) -> None:
        app = _DummyApp(osc_enabled=True)
        app._config.operator_messages.enabled = True
        manager = InputManager(app)
        first = manager.operator_message_handler
        assert first is not None
        manager.restart_osc(enabled=False, port=9001)
        assert first.stopped is True
        assert manager.operator_message_handler is None

    def test_restart_operator_messages_toggles_handler(self) -> None:
        app = _DummyApp(osc_enabled=True)
        app._config.operator_messages.enabled = True
        manager = InputManager(app)
        assert manager.operator_message_handler is not None
        # Disable → handler torn down.
        app._config.operator_messages.enabled = False
        manager.restart_operator_messages()
        assert manager.operator_message_handler is None
        # Re-enable → fresh handler started.
        app._config.operator_messages.enabled = True
        manager.restart_operator_messages()
        assert manager.operator_message_handler is not None
        assert manager.operator_message_handler.started is True

    def test_stop_stops_operator_message_handler(self) -> None:
        app = _DummyApp(osc_enabled=True)
        app._config.operator_messages.enabled = True
        manager = InputManager(app)
        adapter = manager.operator_message_handler
        manager.stop()
        assert adapter is not None and adapter.stopped is True

    def test_disable_clears_retained_messages(self) -> None:
        # Disabling the section clears the store.
        app = _DummyApp(osc_enabled=True)
        app._config.operator_messages.enabled = True
        manager = InputManager(app)
        store = app._runtime_services._operator_message_store
        store.add("lingering", marker_id=0, duration_s=0.0)  # forever card
        assert store.snapshot()  # present while enabled
        app._config.operator_messages.enabled = False
        manager.restart_operator_messages()
        assert manager.operator_message_handler is None
        assert store.snapshot() == []  # cleared on disable


# --------------------------------------------------------------------------- #
# get_controller_info – integration-only path that also exercises the
# orphan-controller ``None`` fill-in branch
# --------------------------------------------------------------------------- #


class TestControllerInfo:
    def test_orphan_controller_gets_marker_id_none(self) -> None:

        class _GamepadWithOrphan(_FakeGamepadHandler):
            pass

        _GamepadWithOrphan.joystick_indices = (0, 1, 5)
        info_data: list[dict] = [
            {"controller_index": 0, "name": "P1", "connected": True},
            {"controller_index": 1, "name": "P2", "connected": True},
            {"controller_index": 5, "name": "Orphan", "connected": True},
        ]

        def _get_info(self: Any) -> list[dict]:
            return [dict(item) for item in info_data]

        _GamepadWithOrphan.get_controller_info = _get_info  # type: ignore[method-assign]

        app = _DummyApp(osc_enabled=False)
        app._selected_id = None  # force slot-based routing for every controller
        # Use the orphan-capable gamepad class just for this test.
        from openfollow.input import input_manager as m

        original = m.GamepadHandler
        m.GamepadHandler = _GamepadWithOrphan  # type: ignore[assignment]
        try:
            manager = InputManager(app)
            info = manager.get_controller_info()
        finally:
            m.GamepadHandler = original  # type: ignore[assignment]

        marker_ids = [item["marker_id"] for item in info]
        assert marker_ids == [10, 11, None]
