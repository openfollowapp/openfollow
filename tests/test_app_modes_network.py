# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for ``openfollow.runtime.app_modes_network``: the on-screen Pi
network settings screen – interface and method pickers, IPv4 field
editing, and the apply/renew worker lifecycle (generation-guarded drain)."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

import openfollow.runtime.app_modes_network as anm
from openfollow.network.adapter import (
    ApplyResult,
    Ipv4Config,
    Ipv4Method,
    LeaseInfo,
    NetworkInterface,
    NetworkState,
)

pytestmark = pytest.mark.unit


class _FakeAdapter:
    backend_name = "fake"

    def __init__(self, writable: bool = True) -> None:
        self._writable = writable
        self._interfaces = [
            NetworkInterface(name="eth0", mac="aa:bb", kind="ethernet", is_up=True),
            NetworkInterface(name="wlan0", mac="cc:dd", kind="wifi", is_up=False),
        ]
        self.apply_calls: list[tuple[str, Ipv4Config]] = []
        self.renew_calls: list[str] = []
        self.apply_result = ApplyResult(ok=True, message="Applied.")
        self.renew_result = ApplyResult(ok=True, message="Renewed.")

    def is_writable(self) -> bool:
        return self._writable

    def list_interfaces(self):
        return list(self._interfaces)

    def get_state(self, iface: str):
        return NetworkState(
            interface=self._interfaces[0] if iface == "eth0" else self._interfaces[1],
            ipv4=Ipv4Config(
                method=Ipv4Method.DHCP,
                address="192.168.1.50",
                prefix=24,
                router="192.168.1.1",
                dns=("8.8.8.8",),
            ),
            lease=LeaseInfo(
                address="192.168.1.50",
                prefix=24,
                router="192.168.1.1",
                dns=("8.8.8.8",),
                lease_seconds_remaining=3600,
            ),
        )

    def apply_ipv4(self, iface: str, config: Ipv4Config):
        self.apply_calls.append((iface, config))
        return self.apply_result

    def renew_lease(self, iface: str):
        self.renew_calls.append(iface)
        return self.renew_result


def _make_app(adapter: _FakeAdapter | None = None) -> SimpleNamespace:
    if adapter is None:
        adapter = _FakeAdapter()
    apply_calls: list[str] = []
    services = SimpleNamespace(
        network_adapter=adapter,
        apply_psn_source_ip_change=lambda ip: apply_calls.append(ip),
    )
    # Network picker reads/writes ``psn_source_iface``.
    config = SimpleNamespace(psn_source_iface="")
    app = SimpleNamespace(
        _runtime_services=services,
        _input_manager=None,
        _config=config,
        _config_path="config.toml",
        _config_mtime=0.0,
        _pi_network_active=False,
        _pi_network_index=0,
        _pi_network_interfaces=[],
        _pi_network_active_iface="",
        _pi_network_state_cache=None,
        _pi_network_pending_config=None,
        _pi_network_iface_picker_active=False,
        _pi_network_iface_picker_index=0,
        _pi_network_method_picker_active=False,
        _pi_network_method_picker_index=0,
        _pi_network_field_edit_active=False,
        _pi_network_field_name="",
        _pi_network_field_value="",
        _pi_network_banner="",
        _pi_network_busy=False,
        _pi_network_worker=None,
        _pi_network_worker_generation=0,
        _pi_network_worker_lock=threading.Lock(),
        _pi_network_pending_result=None,
    )

    def _enter_settings_menu(*, banner: str = "") -> None:  # noqa: ARG001
        app._enter_settings_called = True

    def _get_config_mtime() -> float:
        return 0.0

    app._enter_settings_menu = _enter_settings_menu
    app._get_config_mtime = _get_config_mtime
    app._advisory_refreshes = 0

    def _refresh_psn_source_advisory() -> str:
        app._advisory_refreshes += 1
        return ""

    app._refresh_psn_source_advisory = _refresh_psn_source_advisory
    app._enter_settings_called = False
    app._apply_calls = apply_calls
    return app


class TestPiNetworkScreen:
    def test_enter_populates_state(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        assert app._pi_network_active is True
        assert app._pi_network_active_iface == "eth0"
        assert app._pi_network_state_cache is not None
        assert app._pi_network_pending_config.address == "192.168.1.50"

    def test_build_rows_emits_headers_and_actions(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        rows = anm.build_pi_network_rows(app)
        kinds = [r.get("kind") for r in rows]
        keys = [r.get("key") for r in rows if r.get("key")]
        assert "header" in kinds
        assert "apply" in keys
        assert "back" in keys

    def test_back_returns_to_settings(self) -> None:
        """Back from the Network screen goes straight to Settings;
        wrapper submenu was removed."""
        app = _make_app()
        anm.enter_pi_network(app)
        rows = anm.build_pi_network_rows(app)
        idx = next(i for i, r in enumerate(rows) if r.get("key") == "back")
        app._pi_network_index = idx
        anm.handle_pi_network_key(app, "Enter")
        assert app._pi_network_active is False
        assert app._enter_settings_called is True

    def test_dhcp_method_hides_editable_address(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        rows = anm.build_pi_network_rows(app)
        addr = next(r for r in rows if r.get("key") == "address")
        assert addr["kind"] == "display"  # not "text"

    def test_static_method_promotes_address_to_editable(self) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        app = _make_app()
        anm.enter_pi_network(app)
        app._pi_network_pending_config = Ipv4Config(
            method=Ipv4Method.STATIC,
            address="10.0.0.5",
            prefix=24,
            router="10.0.0.1",
        )
        rows = anm.build_pi_network_rows(app)
        addr = next(r for r in rows if r.get("key") == "address")
        prefix = next(r for r in rows if r.get("key") == "prefix")
        router = next(r for r in rows if r.get("key") == "router")
        assert addr["kind"] == "text"
        assert prefix["kind"] == "text"
        assert router["kind"] == "text"

    def test_readonly_adapter_omits_apply_and_renew(self) -> None:
        adapter = _FakeAdapter(writable=False)
        app = _make_app(adapter)
        anm.enter_pi_network(app)
        rows = anm.build_pi_network_rows(app)
        keys = [r.get("key") for r in rows]
        assert "apply" not in keys
        assert "renew" not in keys
        assert "back" in keys

    def test_cursor_skips_header_rows(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm._pi_network_move(app, +1)
        rows = anm.build_pi_network_rows(app)
        assert rows[app._pi_network_index].get("kind") in {"choice", "text", "action"}

    def test_move_snaps_to_selectable_when_index_lands_on_header(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        app._pi_network_index = 0
        anm._pi_network_move(app, +1)
        rows = anm.build_pi_network_rows(app)
        assert rows[app._pi_network_index].get("kind") in {"choice", "text", "action"}

    def test_enter_lands_on_first_selectable_not_header(self) -> None:
        """``enter_pi_network`` must set the index to the first selectable row, not the header."""
        app = _make_app()
        anm.enter_pi_network(app)
        rows = anm.build_pi_network_rows(app)
        assert rows[app._pi_network_index].get("kind") in {"choice", "text", "action"}

    def test_loopback_filtered_from_interface_list(self) -> None:
        """Loopback must be filtered from the interface list so it doesn't become the default selection."""
        adapter = _FakeAdapter()
        adapter._interfaces = [
            NetworkInterface(name="lo", mac=None, kind=None, is_up=True),
            NetworkInterface(name="eth0", mac="aa:bb", kind="ethernet", is_up=True),
        ]
        app = _make_app(adapter)
        anm.enter_pi_network(app)
        names = [i.name for i in app._pi_network_interfaces]
        assert "lo" not in names
        assert app._pi_network_active_iface == "eth0"

    def test_loopback_filtered_by_kind_even_when_named_differently(self) -> None:
        """nm_adapter populates ``kind='loopback'`` on the dummy 'lo:0'
        alias – name-only matching would miss it."""
        adapter = _FakeAdapter()
        adapter._interfaces = [
            NetworkInterface(name="loop1", mac=None, kind="loopback", is_up=True),
            NetworkInterface(name="eth0", mac="aa:bb", kind="ethernet", is_up=True),
        ]
        app = _make_app(adapter)
        anm.enter_pi_network(app)
        names = [i.name for i in app._pi_network_interfaces]
        assert "loop1" not in names


class TestMethodPicker:
    def test_change_to_static(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_method_picker(app)
        # _METHOD_PICKER_ITEMS order: DHCP, DHCP+manual, STATIC
        app._pi_network_method_picker_index = 2
        anm.handle_pi_network_method_picker_key(app, "Enter")
        assert app._pi_network_method_picker_active is False
        assert app._pi_network_pending_config.method == Ipv4Method.STATIC

    def test_switch_static_to_dhcp_clears_static_fields(self) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        app = _make_app()
        anm.enter_pi_network(app)
        app._pi_network_pending_config = Ipv4Config(
            method=Ipv4Method.STATIC,
            address="192.168.1.50",
            prefix=24,
            router="192.168.1.1",
            dns=("9.9.9.9",),
        )
        anm.enter_pi_network_method_picker(app)
        app._pi_network_method_picker_index = 0  # DHCP
        anm.handle_pi_network_method_picker_key(app, "Enter")
        cfg = app._pi_network_pending_config
        assert cfg.method == Ipv4Method.DHCP
        assert cfg.address is None
        assert cfg.prefix is None
        assert cfg.router is None
        # DNS override survives – it's editable across all methods.
        assert cfg.dns == ("9.9.9.9",)

    def test_switch_static_to_dhcp_manual_clears_prefix_and_router(self) -> None:
        """Static → DHCP+manual: operator keeps the typed address but
        prefix/router come from the lease, not from the prior static config."""
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        app = _make_app()
        anm.enter_pi_network(app)
        app._pi_network_pending_config = Ipv4Config(
            method=Ipv4Method.STATIC,
            address="192.168.1.50",
            prefix=24,
            router="192.168.1.1",
        )
        anm.enter_pi_network_method_picker(app)
        app._pi_network_method_picker_index = 1  # DHCP+manual
        anm.handle_pi_network_method_picker_key(app, "Enter")
        cfg = app._pi_network_pending_config
        assert cfg.method == Ipv4Method.DHCP_WITH_MANUAL_ADDRESS
        # Address survives (operator-typed manual IP).
        assert cfg.address == "192.168.1.50"
        # Prefix + router cleared – lease drives them.
        assert cfg.prefix is None
        assert cfg.router is None

    def test_static_to_static_preserves_all_fields(self) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        app = _make_app()
        anm.enter_pi_network(app)
        app._pi_network_pending_config = Ipv4Config(
            method=Ipv4Method.STATIC,
            address="10.0.0.5",
            prefix=24,
            router="10.0.0.1",
            dns=("8.8.8.8",),
        )
        anm.enter_pi_network_method_picker(app)
        app._pi_network_method_picker_index = 2  # STATIC
        anm.handle_pi_network_method_picker_key(app, "Enter")
        cfg = app._pi_network_pending_config
        assert cfg.method == Ipv4Method.STATIC
        assert cfg.address == "10.0.0.5"
        assert cfg.prefix == 24
        assert cfg.router == "10.0.0.1"
        assert cfg.dns == ("8.8.8.8",)


class TestFieldEdit:
    def test_enter_field_seeds_value_from_pending(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "address")
        assert app._pi_network_field_value == "192.168.1.50"

    def test_only_digits_and_dots_accepted(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "address")
        app._pi_network_field_value = ""
        for k in "1.2.A.3":
            anm.handle_pi_network_field_edit_key(app, k)
        # A is rejected
        assert app._pi_network_field_value == "1.2..3"

    def test_invalid_address_keeps_editor_open_with_banner(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "address")
        app._pi_network_field_value = "1.2.3"  # invalid IPv4
        anm.confirm_pi_network_field_edit(app)
        assert app._pi_network_field_edit_active is True
        assert "Invalid IPv4" in app._pi_network_banner

    def test_valid_address_commits_to_pending(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "address")
        app._pi_network_field_value = "10.0.0.5"
        anm.confirm_pi_network_field_edit(app)
        assert app._pi_network_field_edit_active is False
        assert app._pi_network_pending_config.address == "10.0.0.5"

    def test_prefix_accepts_mask(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "prefix")
        app._pi_network_field_value = "255.255.255.0"
        anm.confirm_pi_network_field_edit(app)
        assert app._pi_network_pending_config.prefix == 24


class TestApplyAndRenew:
    def test_apply_uses_worker_and_calls_adapter(self) -> None:
        adapter = _FakeAdapter()
        app = _make_app(adapter)
        anm.enter_pi_network(app)
        # Switch to STATIC so validate doesn't reject DHCP for missing fields.
        app._pi_network_pending_config = Ipv4Config(
            method=Ipv4Method.STATIC,
            address="192.168.1.50",
            prefix=24,
            router="192.168.1.1",
            dns=("8.8.8.8",),
        )
        rows = anm.build_pi_network_rows(app)
        idx = next(i for i, r in enumerate(rows) if r.get("key") == "apply")
        app._pi_network_index = idx
        anm.handle_pi_network_key(app, "Enter")
        # Worker is a daemon thread; wait briefly for completion, then drain
        # the stashed result on the (test's) main thread.
        worker = app._pi_network_worker
        assert worker is not None
        worker.join(timeout=2.0)
        anm.drain_pi_network_worker(app)
        assert adapter.apply_calls
        assert app._pi_network_busy is False
        assert app._pi_network_banner.startswith("Apply ok")

    def test_start_worker_refuses_while_previous_worker_alive(self) -> None:
        # #552: an orphaned, still-running worker (exit/re-enter cleared busy)
        # must block a second concurrent privileged apply/renew on the same NIC.
        app = _make_app()
        anm.enter_pi_network(app)
        app._pi_network_worker = SimpleNamespace(is_alive=lambda: True)
        ran: list[str] = []
        anm._start_worker(app, lambda: ran.append("ran") or ApplyResult(ok=True, message=""), "Apply")
        assert ran == []  # fn never ran – no second worker launched
        assert app._pi_network_busy is False
        assert app._pi_network_banner  # operator gets feedback, not a silent no-op

    def test_start_worker_launches_after_previous_worker_finished(self) -> None:
        # A finished (dead) previous worker must not block a new launch.
        app = _make_app()
        anm.enter_pi_network(app)
        app._pi_network_worker = SimpleNamespace(is_alive=lambda: False)
        ran: list[str] = []
        anm._start_worker(app, lambda: ran.append("ran") or ApplyResult(ok=True, message=""), "Apply")
        worker = app._pi_network_worker
        assert worker is not None
        worker.join(timeout=2.0)
        assert ran == ["ran"]

    def test_apply_rejects_invalid_static(self) -> None:
        adapter = _FakeAdapter()
        app = _make_app(adapter)
        anm.enter_pi_network(app)
        app._pi_network_pending_config = Ipv4Config(
            method=Ipv4Method.STATIC,
            address=None,
            prefix=None,
            router=None,
        )
        anm._apply_pi_network(app)
        assert adapter.apply_calls == []
        assert app._pi_network_banner  # banner set with first error

    def test_renew_calls_adapter(self) -> None:
        adapter = _FakeAdapter()
        app = _make_app(adapter)
        anm.enter_pi_network(app)
        anm._renew_pi_network(app)
        worker = app._pi_network_worker
        assert worker is not None
        worker.join(timeout=2.0)
        anm.drain_pi_network_worker(app)
        assert adapter.renew_calls == ["eth0"]

    def test_readonly_adapter_blocks_apply_and_renew(self) -> None:
        adapter = _FakeAdapter(writable=False)
        app = _make_app(adapter)
        anm.enter_pi_network(app)
        anm._apply_pi_network(app)
        anm._renew_pi_network(app)
        assert adapter.apply_calls == []
        assert adapter.renew_calls == []
        assert "Read-only" in app._pi_network_banner


class TestGamepadFieldEditCancel:
    """Gamepad Cancel must exit field editor; gamepad-only operator
    can't get stranded inside text input."""

    def _fake_input(self, **pressed: bool):
        """Build the real input dataclass, not a stand-in.

        A hand-rolled namespace silently stops matching the moment a button is
        added, and the poll then fails on an attribute the production code is
        entitled to read.
        """
        from openfollow.input.gamepad import SettingsMenuInput

        return SettingsMenuInput(**{f"{name}_pressed": value for name, value in pressed.items()})

    def test_cancel_exits_editor(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "dns_1")
        # Wire a fake gamepad poll to return a Cancel-pressed event.
        from types import SimpleNamespace as NS

        gp = NS(read_settings_menu_input=lambda: self._fake_input(cancel=True))
        app._input_manager = NS(gamepad_handler=gp)
        anm.process_pi_network_field_edit_input(app)
        assert app._pi_network_field_edit_active is False

    def test_confirm_commits(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "dns_1")
        app._pi_network_field_value = "9.9.9.9"
        from types import SimpleNamespace as NS

        gp = NS(read_settings_menu_input=lambda: self._fake_input(confirm=True))
        app._input_manager = NS(gamepad_handler=gp)
        anm.process_pi_network_field_edit_input(app)
        assert app._pi_network_field_edit_active is False
        assert "9.9.9.9" in app._pi_network_pending_config.dns


class TestDpadEntryThroughTheGamepadPoll:
    """Driven through the poll rather than the helpers it calls.

    The poll is the only thing a gamepad actually reaches; testing the cursor
    and digit helpers directly leaves the wiring between the two untested, which
    is where a missing button or a swapped direction would live.
    """

    def _editing(self, value: str = "192.168.1.5"):
        from types import SimpleNamespace as NS

        from openfollow.input.gamepad import SettingsMenuInput

        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "address")
        app._pi_network_field_value = value

        def press(**pressed: bool) -> None:
            inp = SettingsMenuInput(**{f"{k}_pressed": v for k, v in pressed.items()})
            app._input_manager = NS(gamepad_handler=NS(read_settings_menu_input=lambda: inp))
            anm.process_pi_network_field_edit_input(app)

        return app, press

    def test_up_raises_the_digit_under_the_cursor(self) -> None:
        app, press = self._editing()
        press(up=True)
        assert app._pi_network_field_value == "292.168.001.005"

    def test_down_lowers_it(self) -> None:
        app, press = self._editing()
        press(down=True)
        assert app._pi_network_field_value == "092.168.001.005"

    def test_right_then_up_moves_to_the_next_digit(self) -> None:
        app, press = self._editing()
        press(right=True)
        press(up=True)
        assert app._pi_network_field_value == "102.168.001.005"

    def test_left_walks_back(self) -> None:
        app, press = self._editing()
        press(right=True)
        press(right=True)
        press(left=True)
        press(up=True)
        assert app._pi_network_field_value == "102.168.001.005"

    def test_the_cursor_stops_at_the_first_digit(self) -> None:
        """Wrapping to the far end would read as the value jumping."""
        app, press = self._editing()
        press(left=True)
        press(up=True)
        assert app._pi_network_field_value == "292.168.001.005"


class TestGamepadEntryOnTheSubnetField:
    """The Subnet field accepts a bare prefix length as well as a mask.

    A bare ``24`` has no digit grid: read as one it means ``24.0.0.0``, which
    is not a contiguous mask, so a single d-pad press would rewrite the
    operator's value into one the parser then rejects - with no way back to
    ``24`` but retyping it.
    """

    def _editing_prefix(self, value: str):
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "prefix")
        app._pi_network_field_value = value
        return app

    def test_a_bare_prefix_becomes_the_mask_it_means(self) -> None:
        app = self._editing_prefix("24")
        anm._bump_field_digit(app, 0)
        assert app._pi_network_field_value == "255.255.255.000"

    def test_and_still_commits_as_the_same_subnet(self) -> None:
        from openfollow.network.validate import parse_prefix

        app = self._editing_prefix("24")
        anm._bump_field_digit(app, 0)
        anm.confirm_pi_network_field_edit(app)
        assert app._pi_network_pending_config.prefix == 24
        assert parse_prefix(app._pi_network_field_value or "255.255.255.0") is not None

    def test_moving_the_cursor_expands_it_too(self) -> None:
        """The cursor is meaningless against a value the grid cannot address."""
        app = self._editing_prefix("24")
        anm._move_field_digit_cursor(app, 1)
        assert app._pi_network_field_value == "255.255.255.0"

    def test_a_mask_is_left_alone(self) -> None:
        app = self._editing_prefix("255.255.255.0")
        anm._move_field_digit_cursor(app, 1)
        assert app._pi_network_field_value == "255.255.255.0"

    def test_an_unparseable_prefix_is_not_invented_into_one(self) -> None:
        app = self._editing_prefix("99")
        anm._bump_field_digit(app, 0)
        assert app._pi_network_field_value != "255.255.255.000"

    def test_an_address_field_is_not_treated_as_a_prefix(self) -> None:
        """Only Subnet accepts the bare form; ``24`` in Address is a partial
        address, not a /24."""
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "address")
        app._pi_network_field_value = "24"
        anm._bump_field_digit(app, 0)
        assert app._pi_network_field_value == "024.000.000.000"


class TestTheScreenDoesNotAssignInterfaces:
    """No on-screen path writes ``psn_source_iface``.

    Picking an interface here used to rebind the whole PSN plane and persist the
    choice, so configuring a NIC's *address* silently repointed where stage data
    left the station. Assignment is a web-UI decision; this screen only says
    what address an interface has, and how to reach the web UI.

    Asserted across every action the screen offers rather than against the one
    function that used to do it, because the guarantee is about the screen, not
    about a since-deleted helper.
    """

    def _actions(self, app):
        """Every on-screen path that could plausibly touch config."""
        anm.enter_pi_network_iface_picker(app)
        anm._pi_network_iface_picker_confirm(app)
        anm.enter_pi_network_method_picker(app)
        anm._pi_network_method_picker_confirm(app)
        anm.enter_pi_network_field_edit(app, "address")
        app._pi_network_field_value = "192.168.1.77"
        anm.confirm_pi_network_field_edit(app)
        anm._pi_network_confirm(app)

    def test_no_action_repoints_the_psn_plane(self) -> None:
        app = _make_app()
        app._config.psn_source_iface = "wlan0"
        anm.enter_pi_network(app)

        self._actions(app)

        assert app._config.psn_source_iface == "wlan0", "the network screen reassigned the PSN interface"
        assert app._apply_calls == [], "the network screen rebound the PSN sockets"

    def test_picking_an_interface_still_changes_what_the_screen_shows(self) -> None:
        """The picker keeps its real job; only the side effect is gone."""
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_iface_picker(app)
        app._pi_network_iface_picker_index = 1
        anm._pi_network_iface_picker_confirm(app)

        assert app._pi_network_active_iface == app._pi_network_interfaces[1].name
        assert app._config.psn_source_iface == ""


class TestRefreshPiNetworkEdgeCases:
    def test_no_adapter_clears_state(self) -> None:
        app = _make_app(adapter=_FakeAdapter())
        app._runtime_services = SimpleNamespace(network_adapter=None)
        anm._refresh_pi_network(app)
        assert app._pi_network_state_cache is None
        assert app._pi_network_pending_config is None

    def test_no_interfaces_clears_state(self) -> None:
        adapter = _FakeAdapter()
        adapter._interfaces = []
        app = _make_app(adapter)
        anm._refresh_pi_network(app)
        assert app._pi_network_state_cache is None
        assert app._pi_network_pending_config is None

    def test_active_iface_not_in_list_resets_to_first(self) -> None:
        app = _make_app()
        app._pi_network_active_iface = "ghost0"
        anm._refresh_pi_network(app)
        assert app._pi_network_active_iface == "eth0"

    def test_get_state_none_defaults_to_dhcp(self) -> None:
        adapter = _FakeAdapter()

        def stub_get_state(_iface):
            return None

        adapter.get_state = stub_get_state
        app = _make_app(adapter)
        anm._refresh_pi_network(app)
        assert app._pi_network_pending_config is not None
        from openfollow.network.adapter import Ipv4Method

        assert app._pi_network_pending_config.method == Ipv4Method.DHCP


class TestPiNetworkMove:
    def test_move_with_empty_rows_is_no_op(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        app._pi_network_state_cache = None
        app._pi_network_pending_config = None
        # build_pi_network_rows still emits headers/back, so total >0 in
        # practice – emulate empty by monkey-patching the builder.
        original = anm.build_pi_network_rows
        anm.build_pi_network_rows = lambda _app: []
        try:
            anm._pi_network_move(app, +1)
        finally:
            anm.build_pi_network_rows = original

    def test_move_up_wraps(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        # Find the first selectable row, then move up once – should wrap.
        rows = anm.build_pi_network_rows(app)
        first_selectable = next(i for i, r in enumerate(rows) if r.get("kind") in {"choice", "text", "action"})
        app._pi_network_index = first_selectable
        anm._pi_network_move(app, -1)
        # After wrap, index points to the LAST selectable row.
        last_selectable = max(i for i, r in enumerate(rows) if r.get("kind") in {"choice", "text", "action"})
        assert app._pi_network_index == last_selectable

    def test_confirm_on_header_row_is_no_op(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        rows = anm.build_pi_network_rows(app)
        header_idx = next(i for i, r in enumerate(rows) if r.get("kind") == "header")
        app._pi_network_index = header_idx
        # Should silently return – no state change.
        anm._pi_network_confirm(app)
        assert app._pi_network_active is True  # still on the screen

    def test_confirm_out_of_range_is_no_op(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        app._pi_network_index = 9999
        anm._pi_network_confirm(app)
        assert app._pi_network_active is True


class TestPiNetworkInputDispatchers:
    """Cover the gamepad-poll input dispatchers (cancel / confirm / move)."""

    def _fake_input(self, **pressed: bool):
        """Build the real input dataclass, not a stand-in.

        A hand-rolled namespace silently stops matching the moment a button is
        added, and the poll then fails on an attribute the production code is
        entitled to read.
        """
        from openfollow.input.gamepad import SettingsMenuInput

        return SettingsMenuInput(**{f"{name}_pressed": value for name, value in pressed.items()})

    def _attach_gamepad(self, app, inp_obj):
        from types import SimpleNamespace

        app._input_manager = SimpleNamespace(
            gamepad_handler=SimpleNamespace(
                read_settings_menu_input=lambda: inp_obj,
            ),
        )

    def test_process_pi_network_input_handles_all_actions(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        # Up/Down/Confirm/Cancel paths.
        self._attach_gamepad(app, self._fake_input(down=True))
        anm.process_pi_network_input(app)
        self._attach_gamepad(app, self._fake_input(up=True))
        anm.process_pi_network_input(app)
        # Cancel exits to Settings.
        self._attach_gamepad(app, self._fake_input(cancel=True))
        anm.process_pi_network_input(app)
        assert app._enter_settings_called is True

    def test_process_pi_network_input_no_input_manager(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        app._input_manager = None
        anm.process_pi_network_input(app)  # no crash

    def test_process_pi_network_input_swallows_exception(self) -> None:
        from types import SimpleNamespace

        def boom():
            raise RuntimeError("read failed")

        app = _make_app()
        anm.enter_pi_network(app)
        app._input_manager = SimpleNamespace(
            gamepad_handler=SimpleNamespace(read_settings_menu_input=boom),
        )
        anm.process_pi_network_input(app)  # no crash

    def test_process_iface_picker_input(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_iface_picker(app)
        self._attach_gamepad(app, self._fake_input(down=True))
        anm.process_pi_network_iface_picker_input(app)
        self._attach_gamepad(app, self._fake_input(up=True))
        anm.process_pi_network_iface_picker_input(app)
        self._attach_gamepad(app, self._fake_input(cancel=True))
        anm.process_pi_network_iface_picker_input(app)
        assert app._pi_network_iface_picker_active is False

    def test_process_iface_picker_input_no_manager(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_iface_picker(app)
        app._input_manager = None
        anm.process_pi_network_iface_picker_input(app)

    def test_process_iface_picker_input_exception(self) -> None:
        from types import SimpleNamespace

        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_iface_picker(app)
        app._input_manager = SimpleNamespace(
            gamepad_handler=SimpleNamespace(
                read_settings_menu_input=lambda: (_ for _ in ()).throw(RuntimeError("x")),
            ),
        )
        anm.process_pi_network_iface_picker_input(app)

    def test_process_iface_picker_confirm(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_iface_picker(app)
        self._attach_gamepad(app, self._fake_input(confirm=True))
        anm.process_pi_network_iface_picker_input(app)
        assert app._pi_network_iface_picker_active is False

    def test_iface_picker_no_interfaces_exits(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_iface_picker(app)
        app._pi_network_interfaces = []
        anm._pi_network_iface_picker_confirm(app)
        assert app._pi_network_iface_picker_active is False

    def test_process_method_picker_input(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_method_picker(app)
        self._attach_gamepad(app, self._fake_input(down=True))
        anm.process_pi_network_method_picker_input(app)
        self._attach_gamepad(app, self._fake_input(up=True))
        anm.process_pi_network_method_picker_input(app)
        self._attach_gamepad(app, self._fake_input(cancel=True))
        anm.process_pi_network_method_picker_input(app)
        assert app._pi_network_method_picker_active is False

    def test_process_method_picker_input_no_manager(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_method_picker(app)
        app._input_manager = None
        anm.process_pi_network_method_picker_input(app)

    def test_process_method_picker_input_exception(self) -> None:
        from types import SimpleNamespace

        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_method_picker(app)
        app._input_manager = SimpleNamespace(
            gamepad_handler=SimpleNamespace(
                read_settings_menu_input=lambda: (_ for _ in ()).throw(RuntimeError("x")),
            ),
        )
        anm.process_pi_network_method_picker_input(app)

    def test_method_picker_confirm_out_of_range_exits(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_method_picker(app)
        app._pi_network_method_picker_index = 999
        anm._pi_network_method_picker_confirm(app)
        assert app._pi_network_method_picker_active is False

    def test_process_field_edit_input_no_manager(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "dns_1")
        app._input_manager = None
        anm.process_pi_network_field_edit_input(app)

    def test_process_field_edit_input_exception(self) -> None:
        from types import SimpleNamespace

        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "dns_1")
        app._input_manager = SimpleNamespace(
            gamepad_handler=SimpleNamespace(
                read_settings_menu_input=lambda: (_ for _ in ()).throw(RuntimeError("x")),
            ),
        )
        anm.process_pi_network_field_edit_input(app)


class TestFieldEditAllFields:
    def test_prefix_field_seed_value(self) -> None:
        """Pre-fill in the dotted-mask form so the operator edits in
        the shape they recognise. ``parse_prefix`` still accepts
        ``/24`` on commit for the operators that prefer CIDR."""
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        app = _make_app()
        anm.enter_pi_network(app)
        app._pi_network_pending_config = Ipv4Config(method=Ipv4Method.STATIC, prefix=24)
        anm.enter_pi_network_field_edit(app, "prefix")
        assert app._pi_network_field_value == "255.255.255.0"

    def test_prefix_field_seed_falls_back_when_mask_unrenderable(self) -> None:
        """``prefix_to_mask`` returns ``None`` for an out-of-range
        prefix integer – covers the ``mask or str(prefix)`` fallback
        in the field-editor seed."""
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        app = _make_app()
        anm.enter_pi_network(app)
        # 99 is outside 0..32, so prefix_to_mask returns None.
        app._pi_network_pending_config = Ipv4Config(method=Ipv4Method.STATIC, prefix=99)
        anm.enter_pi_network_field_edit(app, "prefix")
        assert app._pi_network_field_value == "99"

    def test_prefix_field_seed_when_pending_has_no_prefix(self) -> None:
        """``pending.prefix is None`` opens the editor with an empty
        value – covers the ``else`` arm of the prefix-seed branch."""
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        app = _make_app()
        anm.enter_pi_network(app)
        app._pi_network_pending_config = Ipv4Config(
            method=Ipv4Method.STATIC,
            prefix=None,
        )
        anm.enter_pi_network_field_edit(app, "prefix")
        assert app._pi_network_field_value == ""

    def test_prefix_display_falls_back_to_em_dash_when_mask_unrenderable(self) -> None:
        """``_prefix_value`` in the row builder shows ``–`` when the
        prefix is set but out-of-range – covers the ``mask is None``
        false-branch of the dotted-mask render path."""
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        app = _make_app()
        anm.enter_pi_network(app)
        # prefix=99 → prefix_to_mask returns None → row should show "–".
        app._pi_network_pending_config = Ipv4Config(
            method=Ipv4Method.STATIC,
            address="10.0.0.5",
            prefix=99,
            router="10.0.0.1",
        )
        rows = anm.build_pi_network_rows(app)
        prefix_row = next(r for r in rows if r.get("key") == "prefix")
        assert prefix_row["value"] == "–"

    def test_router_field_seed_value(self) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        app = _make_app()
        anm.enter_pi_network(app)
        app._pi_network_pending_config = Ipv4Config(method=Ipv4Method.STATIC, router="10.0.0.1")
        anm.enter_pi_network_field_edit(app, "router")
        assert app._pi_network_field_value == "10.0.0.1"

    def test_dns_field_seed_empty_when_missing(self) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        app = _make_app()
        anm.enter_pi_network(app)
        app._pi_network_pending_config = Ipv4Config(method=Ipv4Method.DHCP, dns=())
        anm.enter_pi_network_field_edit(app, "dns_2")
        assert app._pi_network_field_value == ""

    def test_field_edit_no_pending_config_resets_value(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        app._pi_network_pending_config = None
        anm.enter_pi_network_field_edit(app, "address")
        assert app._pi_network_field_value == ""

    def test_field_edit_unknown_field_resets_value(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "unknown_field")
        assert app._pi_network_field_value == ""

    def test_invalid_prefix_sets_banner(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "prefix")
        app._pi_network_field_value = "999"
        anm.confirm_pi_network_field_edit(app)
        assert "Subnet prefix" in app._pi_network_banner

    def test_invalid_router_sets_banner(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "router")
        app._pi_network_field_value = "not-an-ip"
        anm.confirm_pi_network_field_edit(app)
        assert "router" in app._pi_network_banner.lower()

    def test_invalid_dns_sets_banner(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "dns_1")
        app._pi_network_field_value = "garbage"
        anm.confirm_pi_network_field_edit(app)
        assert "DNS" in app._pi_network_banner

    def test_clearing_dns_removes_entry(self) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        app = _make_app()
        anm.enter_pi_network(app)
        app._pi_network_pending_config = Ipv4Config(
            method=Ipv4Method.DHCP,
            dns=("8.8.8.8", "1.1.1.1"),
        )
        anm.enter_pi_network_field_edit(app, "dns_1")
        app._pi_network_field_value = ""  # clear
        anm.confirm_pi_network_field_edit(app)
        # Empty dns_1 removes the entry → tuple shrinks.
        assert "8.8.8.8" not in app._pi_network_pending_config.dns

    def test_clearing_address_sets_none(self) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        app = _make_app()
        anm.enter_pi_network(app)
        app._pi_network_pending_config = Ipv4Config(
            method=Ipv4Method.STATIC,
            address="10.0.0.5",
        )
        anm.enter_pi_network_field_edit(app, "address")
        app._pi_network_field_value = ""
        anm.confirm_pi_network_field_edit(app)
        assert app._pi_network_pending_config.address is None

    def test_confirm_with_no_pending_config_exits_silently(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "address")
        app._pi_network_pending_config = None
        anm.confirm_pi_network_field_edit(app)
        assert app._pi_network_field_edit_active is False

    def test_confirm_with_empty_field_name_exits_silently(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "address")
        app._pi_network_field_name = ""
        anm.confirm_pi_network_field_edit(app)
        assert app._pi_network_field_edit_active is False

    def test_key_press_backspace_shortens_buffer(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "address")
        app._pi_network_field_value = "192."
        anm.handle_pi_network_field_edit_key(app, "Backspace")
        assert app._pi_network_field_value == "192"

    def test_key_press_rejects_alphanumeric(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "address")
        app._pi_network_field_value = ""
        anm.handle_pi_network_field_edit_key(app, "A")  # not in [0-9.]
        anm.handle_pi_network_field_edit_key(app, "1")
        assert app._pi_network_field_value == "1"

    def test_key_press_accepts_numpad_digits_and_decimal(self) -> None:
        # Keypad keys arrive normalized as "Numpad5" / "KP_Decimal", not the
        # bare characters the top number row sends; they must still type.
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "address")
        app._pi_network_field_value = ""
        for key in ("Numpad1", "Numpad9", "Numpad2", "KP_Decimal", "Numpad0"):
            anm.handle_pi_network_field_edit_key(app, key)
        assert app._pi_network_field_value == "192.0"

    def test_handle_pi_network_key_arrow_navigation(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.handle_pi_network_key(app, "ArrowDown")
        anm.handle_pi_network_key(app, "ArrowUp")
        # No crash; cursor sits on a selectable row.


class TestApplyEdgeCases:
    def test_apply_with_no_adapter_sets_banner(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        app._runtime_services = SimpleNamespace(network_adapter=None)
        anm._apply_pi_network(app)
        assert "No network adapter" in app._pi_network_banner

    def test_renew_with_no_adapter_sets_banner(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        app._runtime_services = SimpleNamespace(network_adapter=None)
        anm._renew_pi_network(app)
        assert "No network adapter" in app._pi_network_banner

    def test_busy_blocks_repeat_apply(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        app._pi_network_busy = True
        # Force STATIC w/ valid fields so validation passes.
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        app._pi_network_pending_config = Ipv4Config(
            method=Ipv4Method.STATIC,
            address="10.0.0.5",
            prefix=24,
            router="10.0.0.1",
        )
        anm._apply_pi_network(app)
        # No worker started (still busy).
        assert app._pi_network_worker is None

    def test_apply_worker_exception_path(self) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        adapter = _FakeAdapter()

        def boom(_iface, _cfg):
            raise RuntimeError("adapter crashed")

        adapter.apply_ipv4 = boom
        app = _make_app(adapter)
        anm.enter_pi_network(app)
        app._pi_network_pending_config = Ipv4Config(
            method=Ipv4Method.STATIC,
            address="10.0.0.5",
            prefix=24,
            router="10.0.0.1",
        )
        anm._apply_pi_network(app)
        worker = app._pi_network_worker
        assert worker is not None
        worker.join(timeout=2.0)
        anm.drain_pi_network_worker(app)
        assert "Apply failed" in app._pi_network_banner

    def test_apply_partial_failure_warning(self) -> None:
        from openfollow.network.adapter import ApplyResult, Ipv4Config, Ipv4Method

        adapter = _FakeAdapter()
        adapter.apply_result = ApplyResult(ok=True, message="ok", partial_failures=("warn-1",))
        app = _make_app(adapter)
        anm.enter_pi_network(app)
        app._pi_network_pending_config = Ipv4Config(
            method=Ipv4Method.STATIC,
            address="10.0.0.5",
            prefix=24,
            router="10.0.0.1",
        )
        anm._apply_pi_network(app)
        worker = app._pi_network_worker
        assert worker is not None
        worker.join(timeout=2.0)
        anm.drain_pi_network_worker(app)
        assert "warn-1" in app._pi_network_banner


class TestBusyShortCircuit:
    """While an apply/renew worker is in flight, ignore confirm except for Back,
    and never let a late worker mutate state after the operator exits the screen."""

    def test_confirm_ignored_while_busy_except_back(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        rows = anm.build_pi_network_rows(app)
        # Land on the Interface row (a selectable choice that would
        # normally open the iface picker).
        iface_idx = next(i for i, r in enumerate(rows) if r.get("key") == "interface")
        app._pi_network_index = iface_idx
        app._pi_network_busy = True
        anm._pi_network_confirm(app)
        assert app._pi_network_iface_picker_active is False
        # Back stays live so the operator can leave a hung screen.
        back_idx = next(i for i, r in enumerate(rows) if r.get("key") == "back")
        app._pi_network_index = back_idx
        anm._pi_network_confirm(app)
        assert app._pi_network_active is False

    def test_late_worker_drops_result_after_exit(self) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        # Block adapter so the worker can't finish before we exit.
        block = threading.Event()
        release = threading.Event()
        adapter = _FakeAdapter()
        original_apply = adapter.apply_ipv4

        def slow_apply(iface, cfg):
            block.set()
            release.wait(timeout=2.0)
            return original_apply(iface, cfg)

        adapter.apply_ipv4 = slow_apply
        app = _make_app(adapter)
        anm.enter_pi_network(app)
        app._pi_network_pending_config = Ipv4Config(
            method=Ipv4Method.STATIC,
            address="10.0.0.5",
            prefix=24,
            router="10.0.0.1",
        )
        anm._apply_pi_network(app)
        # Wait until the worker is actually inside apply, then exit.
        assert block.wait(timeout=2.0)
        anm.exit_pi_network(app)
        app._pi_network_banner = "operator-set"  # would be clobbered by stale worker
        release.set()
        worker = app._pi_network_worker
        assert worker is not None
        worker.join(timeout=2.0)
        anm.drain_pi_network_worker(app)
        # Drain detected the generation mismatch (screen exited) and
        # left the banner alone.
        assert app._pi_network_banner == "operator-set"
        # Busy claim cleared so a future entry sees a clean slate.
        assert app._pi_network_busy is False

    def test_second_apply_orphans_first_worker_completion(self) -> None:
        """If a second worker launches while the first is in flight,
        the first worker's late completion is dropped (generation
        mismatch) – the second worker's result is the only one that
        writes the banner."""
        from openfollow.network.adapter import ApplyResult, Ipv4Config, Ipv4Method

        block = threading.Event()
        release = threading.Event()
        adapter = _FakeAdapter()

        def slow_apply(iface, cfg):
            block.set()
            release.wait(timeout=2.0)
            return ApplyResult(ok=True, message="first")

        adapter.apply_ipv4 = slow_apply
        app = _make_app(adapter)
        anm.enter_pi_network(app)
        app._pi_network_pending_config = Ipv4Config(
            method=Ipv4Method.STATIC,
            address="10.0.0.5",
            prefix=24,
            router="10.0.0.1",
        )
        anm._apply_pi_network(app)
        assert block.wait(timeout=2.0)
        # Simulate the operator cancelling + relaunching by clearing busy
        # and bumping the generation (the bump that normally happens
        # inside ``_start_worker`` for a second call).
        app._pi_network_busy = False
        app._pi_network_worker_generation += 1
        app._pi_network_banner = "second-result-here"
        release.set()
        first_worker = app._pi_network_worker
        assert first_worker is not None
        first_worker.join(timeout=2.0)
        anm.drain_pi_network_worker(app)
        # First worker's late ``_finish_worker`` saw the bumped generation
        # and dropped its result.
        assert app._pi_network_banner == "second-result-here"


class TestExits:
    def test_exit_pi_network_clears_banner(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        app._pi_network_banner = "Some msg"
        anm.exit_pi_network(app)
        assert app._pi_network_active is False
        assert app._pi_network_banner == ""

    def test_exit_iface_picker(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_iface_picker(app)
        anm.exit_pi_network_iface_picker(app)
        assert app._pi_network_iface_picker_active is False

    def test_exit_method_picker(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_method_picker(app)
        anm.exit_pi_network_method_picker(app)
        assert app._pi_network_method_picker_active is False

    def test_iface_picker_handle_key_arrows_and_enter(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_iface_picker(app)
        anm.handle_pi_network_iface_picker_key(app, "ArrowDown")
        anm.handle_pi_network_iface_picker_key(app, "ArrowUp")
        anm.handle_pi_network_iface_picker_key(app, "Enter")
        assert app._pi_network_iface_picker_active is False

    def test_iface_picker_handle_key_escape(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_iface_picker(app)
        anm.handle_pi_network_iface_picker_key(app, "Escape")
        assert app._pi_network_iface_picker_active is False

    def test_method_picker_handle_key_navigation(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_method_picker(app)
        anm.handle_pi_network_method_picker_key(app, "ArrowDown")
        anm.handle_pi_network_method_picker_key(app, "ArrowUp")
        anm.handle_pi_network_method_picker_key(app, "Enter")
        assert app._pi_network_method_picker_active is False

    def test_method_picker_handle_key_escape(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_method_picker(app)
        anm.handle_pi_network_method_picker_key(app, "Escape")
        assert app._pi_network_method_picker_active is False

    def test_iface_picker_no_interfaces_move_noop(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_iface_picker(app)
        app._pi_network_interfaces = []
        anm._pi_network_iface_picker_move(app, +1)  # should silently no-op

    def test_method_picker_seeds_current_method(self) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        app = _make_app()
        anm.enter_pi_network(app)
        app._pi_network_pending_config = Ipv4Config(method=Ipv4Method.STATIC)
        anm.enter_pi_network_method_picker(app)
        from openfollow.runtime.app_modes_network import method_picker_items

        methods = method_picker_items()
        # Index should match Static position.
        static_idx = next(i for i, (m, _) in enumerate(methods) if m == Ipv4Method.STATIC)
        assert app._pi_network_method_picker_index == static_idx

    def test_method_picker_seeds_default_when_pending_missing(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        app._pi_network_pending_config = None
        anm.enter_pi_network_method_picker(app)
        # Defaults to DHCP (first item).
        assert app._pi_network_method_picker_index == 0


class TestConfirmDispatchEachRow:
    """Cover the per-key branches inside _pi_network_confirm."""

    def _confirm_row_by_key(self, app, key: str) -> None:
        rows = anm.build_pi_network_rows(app)
        idx = next(i for i, r in enumerate(rows) if r.get("key") == key)
        app._pi_network_index = idx
        anm._pi_network_confirm(app)

    def test_enter_on_interface_opens_iface_picker(self) -> None:
        # Need >1 interface for the row to be selectable (kind="choice").
        adapter = _FakeAdapter()
        from openfollow.network.adapter import NetworkInterface

        adapter._interfaces = [
            NetworkInterface("eth0", "aa", "ethernet", True),
            NetworkInterface("wlan0", "bb", "wifi", False),
        ]
        app = _make_app(adapter)
        anm.enter_pi_network(app)
        self._confirm_row_by_key(app, "interface")
        assert app._pi_network_iface_picker_active is True

    def test_enter_on_method_opens_method_picker(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        self._confirm_row_by_key(app, "method")
        assert app._pi_network_method_picker_active is True

    def test_enter_on_address_opens_field_editor(self) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        app = _make_app()
        anm.enter_pi_network(app)
        # Switch to STATIC so address becomes a text row (selectable).
        app._pi_network_pending_config = Ipv4Config(
            method=Ipv4Method.STATIC,
            address="10.0.0.5",
            prefix=24,
            router="10.0.0.1",
        )
        self._confirm_row_by_key(app, "address")
        assert app._pi_network_field_edit_active is True
        assert app._pi_network_field_name == "address"

    def test_enter_on_apply_runs_apply(self) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        adapter = _FakeAdapter()
        app = _make_app(adapter)
        anm.enter_pi_network(app)
        app._pi_network_pending_config = Ipv4Config(
            method=Ipv4Method.STATIC,
            address="10.0.0.5",
            prefix=24,
            router="10.0.0.1",
        )
        self._confirm_row_by_key(app, "apply")
        worker = app._pi_network_worker
        assert worker is not None
        worker.join(timeout=2.0)
        assert adapter.apply_calls  # apply was dispatched

    def test_enter_on_renew_runs_renew(self) -> None:
        adapter = _FakeAdapter()
        app = _make_app(adapter)
        anm.enter_pi_network(app)
        # DHCP method shows Renew row.
        self._confirm_row_by_key(app, "renew")
        worker = app._pi_network_worker
        assert worker is not None
        worker.join(timeout=2.0)
        assert adapter.renew_calls == ["eth0"]


class TestKeyboardEscape:
    def test_pi_network_escape_returns_to_settings(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.handle_pi_network_key(app, "Escape")
        assert app._pi_network_active is False
        assert app._enter_settings_called is True


class TestRowCoverageEdges:
    def test_addr_helper_with_no_pending_returns_dash(self) -> None:
        """When pending is None, _addr returns the em-dash (line 147)."""
        app = _make_app()
        anm.enter_pi_network(app)
        # Force STATIC method via pending, then null out pending after
        # method is captured to drive _addr's None branch through display.
        app._pi_network_pending_config = None
        app._pi_network_state_cache = None
        rows = anm.build_pi_network_rows(app)
        # Address row must render "–" rather than crashing.
        addr = next(r for r in rows if r.get("key") == "address")
        assert addr["value"] == "–"

    def test_prefix_value_em_dash_when_no_pending(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        app._pi_network_pending_config = None
        rows = anm.build_pi_network_rows(app)
        prefix = next(r for r in rows if r.get("key") == "prefix")
        assert prefix["value"] == "–"

    def test_dhcp_with_manual_writable_path(self) -> None:
        """Covers the elif Ipv4Method.DHCP_WITH_MANUAL_ADDRESS branch
        (lines 186-188): address is text, prefix/router are display."""
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        app = _make_app()
        anm.enter_pi_network(app)
        app._pi_network_pending_config = Ipv4Config(
            method=Ipv4Method.DHCP_WITH_MANUAL_ADDRESS,
            address="10.0.0.5",
        )
        rows = anm.build_pi_network_rows(app)
        addr = next(r for r in rows if r.get("key") == "address")
        prefix = next(r for r in rows if r.get("key") == "prefix")
        router = next(r for r in rows if r.get("key") == "router")
        assert addr["kind"] == "text"
        assert prefix["kind"] == "display"
        assert router["kind"] == "display"

    def test_method_picker_iface_picker_move_with_no_interfaces(self) -> None:
        """Covers line 376: _pi_network_iface_picker_move early return."""
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_iface_picker(app)
        app._pi_network_interfaces = []
        anm._pi_network_iface_picker_move(app, +1)
        anm._pi_network_iface_picker_move(app, -1)
        # No crash, no index change.

    def test_iface_picker_confirm_bad_index(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_iface_picker(app)
        app._pi_network_iface_picker_index = 999
        anm._pi_network_iface_picker_confirm(app)
        assert app._pi_network_iface_picker_active is False


class TestRouterAndDnsFieldEdit:
    def test_router_valid_commits(self) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        app = _make_app()
        anm.enter_pi_network(app)
        app._pi_network_pending_config = Ipv4Config(method=Ipv4Method.STATIC)
        anm.enter_pi_network_field_edit(app, "router")
        app._pi_network_field_value = "10.0.0.1"
        anm.confirm_pi_network_field_edit(app)
        assert app._pi_network_pending_config.router == "10.0.0.1"

    def test_dns_2_extends_existing_list(self) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        app = _make_app()
        anm.enter_pi_network(app)
        # Pre-existing dns_1; dns_2 padding covers the `while` loop.
        app._pi_network_pending_config = Ipv4Config(
            method=Ipv4Method.DHCP,
            dns=("8.8.8.8",),
        )
        anm.enter_pi_network_field_edit(app, "dns_2")
        app._pi_network_field_value = "1.1.1.1"
        anm.confirm_pi_network_field_edit(app)
        assert app._pi_network_pending_config.dns == ("8.8.8.8", "1.1.1.1")


class TestNetworkAdapterHelperGuards:
    def test_returns_none_when_services_missing(self) -> None:
        from types import SimpleNamespace

        app = SimpleNamespace()  # no _runtime_services attribute
        assert anm._network_adapter(app) is None


class TestProcessInputConfirmsAndCancels:
    """Cover the confirm-button paths in the input handlers (lines 291,
    411->exit, 496->exit, 623->exit)."""

    def _fake_input(self, **pressed: bool):
        """Build the real input dataclass, not a stand-in.

        A hand-rolled namespace silently stops matching the moment a button is
        added, and the poll then fails on an attribute the production code is
        entitled to read.
        """
        from openfollow.input.gamepad import SettingsMenuInput

        return SettingsMenuInput(**{f"{name}_pressed": value for name, value in pressed.items()})

    def test_process_pi_network_confirm_branch(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        # Land on Back, then send a Confirm event.
        rows = anm.build_pi_network_rows(app)
        back_idx = next(i for i, r in enumerate(rows) if r.get("key") == "back")
        app._pi_network_index = back_idx
        from types import SimpleNamespace

        app._input_manager = SimpleNamespace(
            gamepad_handler=SimpleNamespace(
                read_settings_menu_input=lambda: self._fake_input(confirm=True),
            ),
        )
        anm.process_pi_network_input(app)
        # Confirm on Back returns to Settings.
        assert app._pi_network_active is False
        assert app._enter_settings_called is True

    def test_iface_picker_no_action_when_no_buttons(self) -> None:
        """Cover the exit branches where no button is pressed."""
        from types import SimpleNamespace

        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_iface_picker(app)
        app._input_manager = SimpleNamespace(
            gamepad_handler=SimpleNamespace(
                read_settings_menu_input=lambda: self._fake_input(),
            ),
        )
        anm.process_pi_network_iface_picker_input(app)
        # Still active – no button was pressed.
        assert app._pi_network_iface_picker_active is True

    def test_method_picker_no_action_when_no_buttons(self) -> None:
        from types import SimpleNamespace

        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_method_picker(app)
        app._input_manager = SimpleNamespace(
            gamepad_handler=SimpleNamespace(
                read_settings_menu_input=lambda: self._fake_input(),
            ),
        )
        anm.process_pi_network_method_picker_input(app)
        assert app._pi_network_method_picker_active is True

    def test_field_edit_no_action_when_no_buttons(self) -> None:
        from types import SimpleNamespace

        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "dns_1")
        app._input_manager = SimpleNamespace(
            gamepad_handler=SimpleNamespace(
                read_settings_menu_input=lambda: self._fake_input(),
            ),
        )
        anm.process_pi_network_field_edit_input(app)
        # Still active – no button.
        assert app._pi_network_field_edit_active is True


class TestKeyboardFallthroughs:
    """Cover the final 'unrecognised key' branches on every key handler."""

    def test_pi_network_key_unrecognised(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.handle_pi_network_key(app, "Tab")  # not handled
        # No state change.
        assert app._pi_network_active is True

    def test_iface_picker_key_unrecognised(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_iface_picker(app)
        anm.handle_pi_network_iface_picker_key(app, "Tab")
        assert app._pi_network_iface_picker_active is True

    def test_method_picker_key_unrecognised(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_method_picker(app)
        anm.handle_pi_network_method_picker_key(app, "Tab")
        assert app._pi_network_method_picker_active is True

    def test_field_edit_key_escape_via_handler(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "address")
        anm.handle_pi_network_field_edit_key(app, "Escape")
        assert app._pi_network_field_edit_active is False

    def test_field_edit_key_enter_via_handler(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "dns_1")
        app._pi_network_field_value = "9.9.9.9"
        anm.handle_pi_network_field_edit_key(app, "Enter")
        assert app._pi_network_field_edit_active is False
        assert "9.9.9.9" in app._pi_network_pending_config.dns


class TestConfirmFieldEditUnknownField:
    """Covers the 'no matching elif' fall-through in confirm_pi_network_field_edit."""

    def test_unknown_field_still_writes_pending(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        # Bypass the normal enter path to force an unknown field name.
        app._pi_network_field_edit_active = True
        app._pi_network_field_name = "completely_unknown"
        app._pi_network_field_value = ""
        anm.confirm_pi_network_field_edit(app)
        # Exits the editor without mutating pending config.
        assert app._pi_network_field_edit_active is False


class TestRemainingMethodPickerPaths:
    def test_process_method_picker_input_confirm(self) -> None:
        from types import SimpleNamespace

        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_method_picker(app)
        # Pick STATIC (index 2 in _METHOD_PICKER_ITEMS).
        app._pi_network_method_picker_index = 2
        app._input_manager = SimpleNamespace(
            gamepad_handler=SimpleNamespace(
                read_settings_menu_input=lambda: SimpleNamespace(
                    up_pressed=False,
                    down_pressed=False,
                    confirm_pressed=True,
                    cancel_pressed=False,
                ),
            ),
        )
        anm.process_pi_network_method_picker_input(app)
        from openfollow.network.adapter import Ipv4Method

        assert app._pi_network_method_picker_active is False
        assert app._pi_network_pending_config.method == Ipv4Method.STATIC


class TestMethodPickerFallback:
    def test_enter_method_picker_falls_back_to_zero_on_unknown_method(self) -> None:
        """Covers line 435: when pending.method isn't in the picker list,
        the index resets to 0."""
        from types import SimpleNamespace

        from openfollow.network.adapter import Ipv4Config

        app = _make_app()
        anm.enter_pi_network(app)
        # Build a pending config whose method isn't in _METHOD_PICKER_ITEMS.
        fake_method = SimpleNamespace(value="unknown")
        cfg = Ipv4Config.__new__(Ipv4Config)
        object.__setattr__(cfg, "method", fake_method)
        object.__setattr__(cfg, "address", None)
        object.__setattr__(cfg, "prefix", None)
        object.__setattr__(cfg, "router", None)
        object.__setattr__(cfg, "dns", ())
        app._pi_network_pending_config = cfg
        anm.enter_pi_network_method_picker(app)
        assert app._pi_network_method_picker_index == 0


class TestMoveAndConfirmDefensiveBranches:
    """Exercise the defensive guards that fire only when every row is
    non-selectable, or when a row carries an unrecognised key."""

    def test_move_when_no_row_is_selectable(self, monkeypatch) -> None:
        """Forces the inner snap-to-first loop AND the outer move loop to
        complete without finding a selectable row (branches 242->246,
        246->exit)."""
        app = _make_app()
        anm.enter_pi_network(app)
        monkeypatch.setattr(
            anm,
            "build_pi_network_rows",
            lambda _app: [{"kind": "header", "label": "Only"}],
        )
        anm._pi_network_move(app, +1)
        # No crash; index unchanged.

    def test_confirm_with_unrecognised_key_is_no_op(self, monkeypatch) -> None:
        """Covers the elif fall-through (branch 273->exit) when the row's
        key isn't in the known action set."""
        app = _make_app()
        anm.enter_pi_network(app)
        monkeypatch.setattr(
            anm,
            "build_pi_network_rows",
            lambda _app: [{"kind": "action", "key": "future_action", "label": "X"}],
        )
        app._pi_network_index = 0
        anm._pi_network_confirm(app)
        # No state change.
        assert app._pi_network_active is True


class TestDnsPositionalClear:
    def test_clear_trailing_dns_pops_blank(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        app._pi_network_pending_config = Ipv4Config(
            method=Ipv4Method.STATIC,
            address="10.0.0.5",
            prefix=24,
            router="10.0.0.1",
            dns=("1.1.1.1",),
        )
        anm.enter_pi_network_field_edit(app, "dns_2")
        app._pi_network_field_value = ""
        anm.confirm_pi_network_field_edit(app)
        assert app._pi_network_pending_config.dns == ("1.1.1.1",)

    def test_clear_middle_dns_preserves_position(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        app._pi_network_pending_config = Ipv4Config(
            method=Ipv4Method.STATIC,
            address="10.0.0.5",
            prefix=24,
            router="10.0.0.1",
            dns=("1.1.1.1", "2.2.2.2", "3.3.3.3"),
        )
        anm.enter_pi_network_field_edit(app, "dns_2")
        app._pi_network_field_value = ""
        anm.confirm_pi_network_field_edit(app)
        # Slot 2 blanked in place; slot 3 keeps its position.
        assert app._pi_network_pending_config.dns == ("1.1.1.1", "", "3.3.3.3")

    def test_apply_compacts_blank_dns_slot(self) -> None:
        adapter = _FakeAdapter()
        app = _make_app(adapter)
        anm.enter_pi_network(app)
        app._pi_network_pending_config = Ipv4Config(
            method=Ipv4Method.STATIC,
            address="10.0.0.5",
            prefix=24,
            router="10.0.0.1",
            dns=("1.1.1.1", "", "3.3.3.3"),
        )
        anm._apply_pi_network(app)
        worker = app._pi_network_worker
        assert worker is not None
        worker.join(timeout=2.0)
        anm.drain_pi_network_worker(app)
        # Blank middle slot dropped before reaching the adapter.
        applied = adapter.apply_calls[0][1]
        assert applied.dns == ("1.1.1.1", "3.3.3.3")


class TestNetworkWorkerDrain:
    def test_bounded_refresh_times_out_keeps_querying_banner(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _make_app()
        monkeypatch.setattr(anm, "_ENTRY_READ_BUDGET_S", 0.05)
        block = threading.Event()

        def _slow_read(_app: object) -> anm._NetworkSnapshot:
            block.wait(timeout=1.0)
            return anm._NetworkSnapshot(True, [], "", None, None)

        monkeypatch.setattr(anm, "_read_pi_network", _slow_read)
        anm._refresh_pi_network_bounded(app)
        assert app._pi_network_banner == "Querying network status…"
        block.set()

    def test_drain_without_lock_is_noop(self) -> None:
        app = SimpleNamespace()
        anm.drain_pi_network_worker(app)  # must not raise

    def test_drain_without_pending_is_noop(self) -> None:
        app = _make_app()
        app._pi_network_banner = "untouched"
        anm.drain_pi_network_worker(app)
        assert app._pi_network_banner == "untouched"
