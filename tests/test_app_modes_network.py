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


class _FakeWebServer:
    """Stand-in for the running ``ConfigWebServer``.

    The screen reads the **live** bind off this, never the config pin, so
    tests set the two apart deliberately - that gap is the fail-open case.
    """

    def __init__(self, bind_host: str = "0.0.0.0", display_port: int = 80, banner: str = "") -> None:
        self.bind_host = bind_host
        self.display_port = display_port
        self._banner = banner

    def get_web_bind_advisory(self) -> dict[str, str]:
        return {"status": "down" if self._banner else "", "banner": self._banner, "resolved_ip": ""}


def _make_app(adapter: _FakeAdapter | None = None) -> SimpleNamespace:
    if adapter is None:
        adapter = _FakeAdapter()
    apply_calls: list[str] = []
    services = SimpleNamespace(
        network_adapter=adapter,
        apply_psn_source_ip_change=lambda ip: apply_calls.append(ip),
    )
    # The screen reads ``psn_source_iface`` only to prove it never writes it;
    # ``web_bind_iface`` is the one config field it does own.
    config = SimpleNamespace(psn_source_iface="", web_bind_iface="", web_port=80)
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
        _pi_network_static_edit=False,
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
    app._web_server = _FakeWebServer()
    app._restart_requests = 0
    app._web_commands = SimpleNamespace(
        request_restart=lambda: setattr(app, "_restart_requests", app._restart_requests + 1)
    )
    app._advisory_refreshes = 0

    def _refresh_psn_source_advisory() -> str:
        app._advisory_refreshes += 1
        return ""

    app._refresh_psn_source_advisory = _refresh_psn_source_advisory
    app._enter_settings_called = False
    app._apply_calls = apply_calls
    return app


def _confirm_key(app: SimpleNamespace, key: str) -> None:
    """Put the cursor on the row carrying ``key`` and confirm it.

    Going through the rendered rows (rather than calling the action helper
    directly) is what keeps these tests honest about the row actually being
    reachable, which is the thing a reframe can silently break.
    """
    rows = anm.build_pi_network_rows(app)
    app._pi_network_index = next(i for i, r in enumerate(rows) if r.get("key") == key)
    anm._pi_network_confirm(app)


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
        assert "dhcp" in keys
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

    def test_static_action_reveals_the_editable_fields(self) -> None:
        """The fields appear only after the operator asks for a static
        address, so the screen opens on the URL list it exists to show."""
        app = _make_app()
        anm.enter_pi_network(app)
        assert [r for r in anm.build_pi_network_rows(app) if r.get("key") == "address"] == []

        _confirm_key(app, "static")
        rows = anm.build_pi_network_rows(app)
        assert next(r for r in rows if r.get("key") == "address")["kind"] == "text"
        assert next(r for r in rows if r.get("key") == "prefix")["kind"] == "text"
        assert next(r for r in rows if r.get("key") == "router")["kind"] == "text"

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
        _confirm_key(app, "static")
        app._pi_network_pending_config = Ipv4Config(
            method=Ipv4Method.STATIC,
            address="192.168.1.50",
            prefix=24,
            router="192.168.1.1",
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
        anm.enter_pi_network_field_edit(app, "router")
        # Wire a fake gamepad poll to return a Cancel-pressed event.
        from types import SimpleNamespace as NS

        gp = NS(read_settings_menu_input=lambda: self._fake_input(cancel=True))
        app._input_manager = NS(gamepad_handler=gp)
        anm.process_pi_network_field_edit_input(app)
        assert app._pi_network_field_edit_active is False

    def test_confirm_commits(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "router")
        app._pi_network_field_value = "9.9.9.9"
        from types import SimpleNamespace as NS

        gp = NS(read_settings_menu_input=lambda: self._fake_input(confirm=True))
        app._input_manager = NS(gamepad_handler=gp)
        anm.process_pi_network_field_edit_input(app)
        assert app._pi_network_field_edit_active is False
        assert app._pi_network_pending_config.router == "9.9.9.9"


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
        """Every selectable row the screen offers, confirmed in turn.

        Driven off the rendered rows so a row added later is covered without
        anyone remembering to extend this list.
        """
        seen: set[str] = set()
        while True:
            keys = [
                str(r["key"])
                for r in anm.build_pi_network_rows(app)
                if r.get("kind") in {"choice", "text", "action"} and str(r.get("key", "")) not in seen | {"back"}
            ]
            if not keys:
                return
            key = keys[0]
            seen.add(key)
            _confirm_key(app, key)
            if app._pi_network_field_edit_active:
                app._pi_network_field_value = "192.168.1.77"
                anm.confirm_pi_network_field_edit(app)

    def test_no_action_repoints_the_psn_plane(self) -> None:
        app = _make_app()
        app._config.psn_source_iface = "wlan0"
        anm.enter_pi_network(app)

        self._actions(app)

        assert app._config.psn_source_iface == "wlan0", "the network screen reassigned the PSN interface"
        assert app._apply_calls == [], "the network screen rebound the PSN sockets"

    def test_picking_an_interface_still_changes_what_the_screen_shows(self) -> None:
        """Selecting an interface row keeps its real job - naming the one the
        Fix-reachability actions act on - without the side effect."""
        app = _make_app()
        anm.enter_pi_network(app)
        second = app._pi_network_interfaces[1].name
        _confirm_key(app, f"iface:{second}")

        assert app._pi_network_active_iface == second
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
        _confirm_key(app, "static")
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
        # Land on the Set-to-static row, which would normally reveal the
        # address fields.
        app._pi_network_index = next(i for i, r in enumerate(rows) if r.get("key") == "static")
        app._pi_network_busy = True
        anm._pi_network_confirm(app)
        assert app._pi_network_static_edit is False
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


class TestConfirmDispatchEachRow:
    """Cover the per-key branches inside _pi_network_confirm."""

    def _confirm_row_by_key(self, app, key: str) -> None:
        rows = anm.build_pi_network_rows(app)
        idx = next(i for i, r in enumerate(rows) if r.get("key") == key)
        app._pi_network_index = idx
        anm._pi_network_confirm(app)

    def test_enter_on_address_opens_field_editor(self) -> None:
        from openfollow.network.adapter import Ipv4Config, Ipv4Method

        app = _make_app()
        anm.enter_pi_network(app)
        _confirm_key(app, "static")
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
        _confirm_key(app, "static")
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
        """A missing pending config renders a dash rather than crashing the
        screen an operator opened because nothing else was reachable."""
        app = _make_app()
        anm.enter_pi_network(app)
        _confirm_key(app, "static")
        app._pi_network_pending_config = None
        app._pi_network_state_cache = None
        rows = anm.build_pi_network_rows(app)
        assert next(r for r in rows if r.get("key") == "address")["value"] == "\u2013"

    def test_prefix_value_em_dash_when_no_pending(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        _confirm_key(app, "static")
        app._pi_network_pending_config = None
        rows = anm.build_pi_network_rows(app)
        assert next(r for r in rows if r.get("key") == "prefix")["value"] == "\u2013"


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

    def test_field_edit_key_escape_via_handler(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "address")
        anm.handle_pi_network_field_edit_key(app, "Escape")
        assert app._pi_network_field_edit_active is False

    def test_field_edit_key_enter_via_handler(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        anm.enter_pi_network_field_edit(app, "router")
        app._pi_network_field_value = "9.9.9.9"
        anm.handle_pi_network_field_edit_key(app, "Enter")
        assert app._pi_network_field_edit_active is False
        assert app._pi_network_pending_config.router == "9.9.9.9"


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


def _patch_ifaces(monkeypatch, ifaces: dict[str, str]) -> None:
    import socket as _socket

    from openfollow import net_utils as net_utils_mod

    monkeypatch.setattr(
        net_utils_mod.psutil,
        "net_if_addrs",
        lambda: {n: [SimpleNamespace(family=_socket.AF_INET, address=a)] for n, a in ifaces.items()},
    )


def _rows_by_kind(app, kind: str) -> list[dict]:
    return [r for r in anm.build_pi_network_rows(app) if r.get("kind") == kind]


def _labels(app) -> list[str]:
    return [str(r.get("label", "")) for r in anm.build_pi_network_rows(app)]


class TestTheScreenAnswersHowToReachTheWebUi:
    """The screen exists to hand the operator an address that works.

    Everything here is about that one job: the URLs it lists, and refusing to
    show one that would not answer.
    """

    def test_each_interface_gets_the_url_that_reaches_it(self, monkeypatch) -> None:
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5", "wlan0": "172.16.4.20"})
        app = _make_app()
        anm.enter_pi_network(app)
        assert "http://192.168.1.5" in _labels(app)
        assert "http://172.16.4.20" in _labels(app)

    def test_a_non_default_port_is_part_of_the_url(self, monkeypatch) -> None:
        """An operator types what is on the screen; a URL missing the port
        would send them to a port nothing is listening on."""
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        app = _make_app()
        app._config.web_port = 8080
        app._web_server = _FakeWebServer(display_port=8080)
        anm.enter_pi_network(app)
        assert "http://192.168.1.5:8080" in _labels(app)

    def test_the_url_carries_the_port_that_actually_bound(self, monkeypatch) -> None:
        """An unprivileged station that could not take :80 is serving on the
        fallback. Printing the configured port would hand out an address
        nothing answers on - the one thing this screen must not do."""
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        app = _make_app()
        app._config.web_port = 80
        app._web_server = _FakeWebServer(display_port=8080)
        anm.enter_pi_network(app)
        assert "http://192.168.1.5:8080" in _labels(app)
        assert "http://192.168.1.5" not in _labels(app)

    def test_an_interface_with_no_address_says_so_instead_of_a_url(self, monkeypatch) -> None:
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        app = _make_app()
        anm.enter_pi_network(app)
        wlan = next(r for r in anm.build_pi_network_rows(app) if r.get("value") == "wlan0")
        assert wlan["label"] == "-- no address --"

    def test_a_pinned_web_ui_shows_a_url_only_where_it_answers(self, monkeypatch) -> None:
        """Listing every address while the UI answers on one is how an
        operator concludes the station is dead when it is merely pinned."""
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5", "wlan0": "172.16.4.20"})
        app = _make_app()
        app._config.web_bind_iface = "wlan0"
        app._web_server = _FakeWebServer(bind_host="172.16.4.20")
        anm.enter_pi_network(app)
        rows = {str(r.get("value")): str(r.get("label")) for r in anm.build_pi_network_rows(app)}
        assert rows["wlan0"] == "http://172.16.4.20"
        assert rows["eth0"] == "-- web UI not served here --"

    def test_the_mdns_name_leads_and_is_not_selectable(self, monkeypatch) -> None:
        """It reaches the station on any interface and is the line an operator
        can read out over comms, so it goes first - but it names no interface,
        so there is no action it could drive."""
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        monkeypatch.setattr("openfollow.privilege.device_repair.current_hostname", lambda: "openfollow-noble-bear")
        app = _make_app()
        anm.enter_pi_network(app)
        rows = anm.build_pi_network_rows(app)
        mdns = next(r for r in rows if r.get("key") == "mdns")
        assert mdns["label"] == "http://openfollow-noble-bear.local"
        assert mdns["kind"] not in {"choice", "text", "action"}
        assert rows.index(mdns) < min(i for i, r in enumerate(rows) if r.get("kind") == "choice")

    def test_an_unusable_hostname_is_omitted_rather_than_guessed(self, monkeypatch) -> None:
        """A station that never got renamed answers to ``localhost.local``
        nowhere; showing it would be an address that cannot work."""
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        monkeypatch.setattr("openfollow.privilege.device_repair.current_hostname", lambda: "localhost")
        app = _make_app()
        anm.enter_pi_network(app)
        assert [r for r in anm.build_pi_network_rows(app) if r.get("key") == "mdns"] == []

    def test_a_failing_hostname_lookup_does_not_blank_the_screen(self, monkeypatch) -> None:
        """This screen is the last surface an operator has; a hostname lookup
        raising must cost the one line, not the page."""
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})

        def _boom() -> str:
            raise OSError("no hostname")

        monkeypatch.setattr("openfollow.privilege.device_repair.current_hostname", _boom)
        app = _make_app()
        anm.enter_pi_network(app)
        assert "http://192.168.1.5" in _labels(app)


class TestTheScreenCallsOutWhatBreaksReachability:
    def test_a_link_local_address_is_flagged_as_a_dhcp_failure(self, monkeypatch) -> None:
        """169.254.x reads like a working lease to anyone who does not know
        the prefix, which is most people reading this screen at 2am."""
        _patch_ifaces(monkeypatch, {"eth0": "169.254.8.31"})
        app = _make_app()
        anm.enter_pi_network(app)
        notices = [str(r["label"]) for r in _rows_by_kind(app, "notice")]
        assert any("DHCP unavailable" in n and "169.254.8.31" in n for n in notices)

    def test_a_routable_address_raises_no_notice(self, monkeypatch) -> None:
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        app = _make_app()
        anm.enter_pi_network(app)
        assert _rows_by_kind(app, "notice") == []

    def test_a_restricted_web_ui_is_called_out_by_its_address(self, monkeypatch) -> None:
        """Named by the address that works, not by the interface that was
        asked for - the address is what the operator has to type."""
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        app = _make_app()
        app._config.web_bind_iface = "eth0"
        app._web_server = _FakeWebServer(bind_host="192.168.1.5")
        anm.enter_pi_network(app)
        notices = [str(r["label"]) for r in _rows_by_kind(app, "notice")]
        assert any("served only at 192.168.1.5" in n for n in notices)

    def test_notices_are_not_selectable(self, monkeypatch) -> None:
        """They carry no action, so landing on one would be a dead stop for a
        gamepad operator working down the list."""
        _patch_ifaces(monkeypatch, {"eth0": "169.254.8.31"})
        app = _make_app()
        anm.enter_pi_network(app)
        assert all(r["kind"] not in {"choice", "text", "action"} for r in _rows_by_kind(app, "notice"))


class TestServeOnAllInterfacesIsTheLockoutEscape:
    def test_it_clears_the_pin_and_asks_for_a_restart(self, monkeypatch) -> None:
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        saved: list[object] = []
        monkeypatch.setattr("openfollow.runtime.app_modes._persist_config", lambda app: saved.append(app) or True)
        app = _make_app()
        app._config.web_bind_iface = "eth0"
        anm.enter_pi_network(app)

        _confirm_key(app, "web_unpin")

        assert app._config.web_bind_iface == ""
        assert saved, "the cleared pin was never written to disk"
        assert app._restart_requests == 1

    def test_it_is_offered_only_while_the_ui_is_pinned(self, monkeypatch) -> None:
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        app = _make_app()
        anm.enter_pi_network(app)
        assert "web_unpin" not in [r.get("key") for r in anm.build_pi_network_rows(app)]

    def test_it_survives_a_read_only_network_backend(self, monkeypatch) -> None:
        """The escape writes config, not the network stack. A host whose
        addressing this build cannot manage is exactly where a lockout would
        otherwise be permanent."""
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        monkeypatch.setattr("openfollow.runtime.app_modes._persist_config", lambda app: True)
        app = _make_app(_FakeAdapter(writable=False))
        app._config.web_bind_iface = "eth0"
        anm.enter_pi_network(app)

        assert "web_unpin" in [r.get("key") for r in anm.build_pi_network_rows(app)]
        _confirm_key(app, "web_unpin")
        assert app._config.web_bind_iface == ""

    def test_a_failed_save_keeps_the_pin_and_asks_for_no_restart(self, monkeypatch) -> None:
        """Restarting on an unwritten change would reboot the station into the
        same lockout and look like the escape simply did not work."""
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        monkeypatch.setattr("openfollow.runtime.app_modes._persist_config", lambda app: False)
        app = _make_app()
        app._config.web_bind_iface = "eth0"
        anm.enter_pi_network(app)

        _confirm_key(app, "web_unpin")

        assert app._config.web_bind_iface == "eth0"
        assert app._restart_requests == 0
        assert "still pinned" in app._pi_network_banner


class TestFixReachabilityActions:
    def test_dhcp_applies_without_a_form(self, monkeypatch) -> None:
        """A venue that just needs the lease back should not have to type an
        address on a d-pad to get it."""
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        adapter = _FakeAdapter()
        app = _make_app(adapter)
        anm.enter_pi_network(app)

        _confirm_key(app, "dhcp")
        worker = app._pi_network_worker
        assert worker is not None
        worker.join(timeout=2.0)

        assert [iface for iface, _cfg in adapter.apply_calls] == ["eth0"]
        assert adapter.apply_calls[0][1].method is Ipv4Method.DHCP

    def test_the_actions_name_the_selected_interface(self, monkeypatch) -> None:
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5", "wlan0": "172.16.4.20"})
        app = _make_app()
        anm.enter_pi_network(app)
        assert any(label == "Set eth0 to DHCP" for label in _labels(app))

        _confirm_key(app, "iface:wlan0")
        assert any(label == "Set wlan0 to DHCP" for label in _labels(app))

    def test_switching_interface_drops_a_half_typed_address(self, monkeypatch) -> None:
        """The typed values belong to the interface they were started on;
        carrying them across would apply one venue's address to another NIC."""
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5", "wlan0": "172.16.4.20"})
        app = _make_app()
        anm.enter_pi_network(app)
        _confirm_key(app, "static")
        assert app._pi_network_static_edit is True

        _confirm_key(app, "iface:wlan0")
        assert app._pi_network_static_edit is False

    def test_reselecting_the_same_interface_keeps_the_editor(self, monkeypatch) -> None:
        """Confirming the row already selected is a no-op, not a reset - it is
        the easiest thing to hit by accident while navigating."""
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        app = _make_app()
        anm.enter_pi_network(app)
        _confirm_key(app, "static")

        _confirm_key(app, "iface:eth0")
        assert app._pi_network_static_edit is True

    def test_cancel_leaves_the_static_editor(self, monkeypatch) -> None:
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        app = _make_app()
        anm.enter_pi_network(app)
        _confirm_key(app, "static")

        _confirm_key(app, "cancel_static")

        assert app._pi_network_static_edit is False
        assert [r for r in anm.build_pi_network_rows(app) if r.get("key") == "address"] == []

    def test_the_static_editor_is_seeded_from_the_live_address(self, monkeypatch) -> None:
        """Correcting one octet should be one digit on a d-pad, not a whole
        address typed from scratch."""
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        app = _make_app()
        anm.enter_pi_network(app)

        _confirm_key(app, "static")

        assert app._pi_network_pending_config.address == "192.168.1.50"
        assert app._pi_network_pending_config.method is Ipv4Method.STATIC

    def test_a_read_only_host_offers_no_addressing_actions(self, monkeypatch) -> None:
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        app = _make_app(_FakeAdapter(writable=False))
        anm.enter_pi_network(app)
        keys = [r.get("key") for r in anm.build_pi_network_rows(app)]
        assert "dhcp" not in keys
        assert "static" not in keys
        assert "renew" not in keys
        assert "back" in keys


class TestTheScreenNoLongerEditsRouterAndDns:
    def test_no_dns_rows_are_offered(self, monkeypatch) -> None:
        """Neither is needed to reach a station on the same LAN, and both are
        the fiddliest things to type on a gamepad. They stay in the web UI."""
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        app = _make_app()
        anm.enter_pi_network(app)
        _confirm_key(app, "static")
        keys = [str(r.get("key", "")) for r in anm.build_pi_network_rows(app)]
        assert not any(k.startswith("dns") for k in keys)

    def test_the_router_stays_because_a_static_venue_needs_a_gateway(self, monkeypatch) -> None:
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        app = _make_app()
        anm.enter_pi_network(app)
        _confirm_key(app, "static")
        assert "router" in [r.get("key") for r in anm.build_pi_network_rows(app)]


class TestDefensivePathsOnTheReachabilityScreen:
    """Guards that only fire on a malformed adapter row or a stale row.

    A row is rendered from one snapshot and confirmed against a later one, so
    "the row was offered" is not proof the action is still valid. These are
    the paths that close that window, and none of them may leave the screen
    in a half-applied state.
    """

    def test_an_unnamed_interface_is_skipped(self, monkeypatch) -> None:
        """A blank name resolves to no address and names nothing an action
        could act on, so the row would be a dead stop for a gamepad
        operator working down the list."""
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        app = _make_app()
        anm.enter_pi_network(app)
        app._pi_network_interfaces = [
            NetworkInterface(name="", mac=None, kind=None, is_up=True),
            NetworkInterface(name="eth0", mac="aa:bb", kind="ethernet", is_up=True),
        ]
        values = [r.get("value") for r in anm.build_pi_network_rows(app) if r.get("kind") == "choice"]
        assert values == ["eth0"]

    def test_unpinning_an_already_unpinned_ui_writes_nothing(self, monkeypatch) -> None:
        """The row is only offered while pinned, so reaching this means the
        pin was cleared since it was rendered - the work is already done."""
        saved: list[object] = []
        monkeypatch.setattr("openfollow.runtime.app_modes._persist_config", lambda app: saved.append(app) or True)
        app = _make_app()
        anm.enter_pi_network(app)

        anm._unpin_web_ui(app)

        assert saved == []
        assert app._restart_requests == 0

    def test_dhcp_without_an_adapter_says_so_instead_of_raising(self) -> None:
        app = _make_app()
        anm.enter_pi_network(app)
        app._runtime_services = SimpleNamespace(network_adapter=None)

        anm._set_pi_network_dhcp(app)

        assert app._pi_network_banner == "No network adapter available."
        assert app._pi_network_worker is None

    def test_dhcp_on_a_read_only_host_says_so_instead_of_applying(self) -> None:
        adapter = _FakeAdapter(writable=False)
        app = _make_app(adapter)
        anm.enter_pi_network(app)

        anm._set_pi_network_dhcp(app)

        assert "Read-only host" in app._pi_network_banner
        assert adapter.apply_calls == []


class TestTheScreenReportsTheLiveBindNotThePin:
    """The pin fails open, so the config is not evidence of where the UI is.

    This is the scenario the screen exists for, and reading the pin instead
    of the bind inverts its answer in exactly that case.
    """

    def test_a_pin_that_missed_still_shows_every_working_url(self, monkeypatch) -> None:
        """Pinned to a dark interface, the runtime serves everywhere. A screen
        that echoed the pin would show zero usable URLs for a station that is
        reachable at all of them."""
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5", "wlan0": "172.16.4.20"})
        app = _make_app()
        app._config.web_bind_iface = "eth_gone"
        app._web_server = _FakeWebServer(bind_host="0.0.0.0")
        anm.enter_pi_network(app)

        rows = {str(r.get("value")): str(r.get("label")) for r in anm.build_pi_network_rows(app)}
        assert rows["eth0"] == "http://192.168.1.5"
        assert rows["wlan0"] == "http://172.16.4.20"
        assert "-- web UI not served here --" not in rows.values()

    def test_a_pin_that_missed_is_explained_rather_than_hidden(self, monkeypatch) -> None:
        """Serving everywhere contradicts the config, so the screen carries
        the runtime's own account of why."""
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        app = _make_app()
        app._config.web_bind_iface = "eth_gone"
        app._web_server = _FakeWebServer(
            bind_host="0.0.0.0",
            banner="Web UI is pinned to 'eth_gone', which has no address.",
        )
        anm.enter_pi_network(app)
        notices = [str(r["label"]) for r in _rows_by_kind(app, "notice")]
        assert any("eth_gone" in n for n in notices)

    def test_a_literal_bind_address_restricts_the_rows_too(self, monkeypatch) -> None:
        """``web_bind`` outranks the interface pin, so a station using it is
        just as restricted - and the screen has to say so."""
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5", "wlan0": "172.16.4.20"})
        app = _make_app()
        app._config.web_bind = "192.168.1.5"
        app._web_server = _FakeWebServer(bind_host="192.168.1.5")
        anm.enter_pi_network(app)

        rows = {str(r.get("value")): str(r.get("label")) for r in anm.build_pi_network_rows(app)}
        assert rows["eth0"] == "http://192.168.1.5"
        assert rows["wlan0"] == "-- web UI not served here --"

    def test_a_literal_bind_address_is_cleared_by_the_escape(self, monkeypatch) -> None:
        """Clearing only the interface pin would leave this station exactly
        as unreachable while reporting the escape succeeded."""
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        monkeypatch.setattr("openfollow.runtime.app_modes._persist_config", lambda app: True)
        app = _make_app()
        app._config.web_bind = "192.168.1.5"
        app._web_server = _FakeWebServer(bind_host="192.168.1.5")
        anm.enter_pi_network(app)

        _confirm_key(app, "web_unpin")

        assert app._config.web_bind == ""
        assert app._restart_requests == 1

    def test_a_failed_save_restores_both_pins(self, monkeypatch) -> None:
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        monkeypatch.setattr("openfollow.runtime.app_modes._persist_config", lambda app: False)
        app = _make_app()
        app._config.web_bind = "192.168.1.5"
        app._config.web_bind_iface = "eth0"
        app._web_server = _FakeWebServer(bind_host="192.168.1.5")
        anm.enter_pi_network(app)

        _confirm_key(app, "web_unpin")

        assert (app._config.web_bind, app._config.web_bind_iface) == ("192.168.1.5", "eth0")
        assert app._restart_requests == 0


class TestTheCursorSurvivesRowsAppearingAndDisappearing:
    """The cursor is a bare index into a list rebuilt every frame.

    A row appearing or vanishing under it moves the highlight onto a
    different action, and an index past the end makes Enter a silent no-op.
    """

    def _key_under_cursor(self, app) -> str:
        rows = anm.build_pi_network_rows(app)
        idx = app._pi_network_index
        assert 0 <= idx < len(rows), f"cursor {idx} is outside a {len(rows)}-row list"
        return str(rows[idx].get("key", ""))

    def test_opening_the_static_editor_lands_on_the_first_field(self, monkeypatch) -> None:
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        app = _make_app()
        anm.enter_pi_network(app)
        _confirm_key(app, "static")
        assert self._key_under_cursor(app) == "address"

    def test_cancelling_returns_the_cursor_to_the_action_it_came_from(self, monkeypatch) -> None:
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        app = _make_app()
        anm.enter_pi_network(app)
        _confirm_key(app, "static")
        _confirm_key(app, "cancel_static")
        assert self._key_under_cursor(app) == "static"

    def test_unpinning_does_not_leave_the_cursor_on_apply_dhcp(self, monkeypatch) -> None:
        """The escape removes its own row. Landing on the next action down
        means a second Enter tap reconfigures the interface."""
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        monkeypatch.setattr("openfollow.runtime.app_modes._persist_config", lambda app: True)
        app = _make_app()
        app._config.web_bind_iface = "eth0"
        app._web_server = _FakeWebServer(bind_host="192.168.1.5")
        anm.enter_pi_network(app)
        rows = anm.build_pi_network_rows(app)
        app._pi_network_index = next(i for i, r in enumerate(rows) if r.get("key") == "web_unpin")

        anm._pi_network_confirm(app)

        assert self._key_under_cursor(app) == "dhcp"
        adapter = app._runtime_services.network_adapter
        assert adapter.apply_calls == []

    def test_switching_interface_keeps_the_cursor_on_that_interface(self, monkeypatch) -> None:
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5", "wlan0": "172.16.4.20"})
        app = _make_app()
        anm.enter_pi_network(app)
        _confirm_key(app, "iface:wlan0")
        assert self._key_under_cursor(app) == "iface:wlan0"


class TestApplyingAStaticAddressKeepsDns:
    def test_dns_survives_the_static_editor(self, monkeypatch) -> None:
        """DNS is not editable here, but the apply path writes whatever the
        config holds - so dropping it would clear the station's nameservers
        as a side effect of setting an address, taking NTP and the update
        check down with it."""
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        adapter = _FakeAdapter()
        app = _make_app(adapter)
        anm.enter_pi_network(app)
        assert app._pi_network_pending_config.dns == ("8.8.8.8",)

        _confirm_key(app, "static")

        assert app._pi_network_pending_config.dns == ("8.8.8.8",)

    def test_the_applied_config_still_carries_dns(self, monkeypatch) -> None:
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        adapter = _FakeAdapter()
        app = _make_app(adapter)
        anm.enter_pi_network(app)
        _confirm_key(app, "static")
        _confirm_key(app, "apply")
        worker = app._pi_network_worker
        assert worker is not None
        worker.join(timeout=2.0)

        assert adapter.apply_calls[0][1].dns == ("8.8.8.8",)


class TestTheScreenNeverBlocksTheFrameLoop:
    def test_cancelling_the_static_editor_uses_the_bounded_read(self, monkeypatch) -> None:
        """A hung nmcli must cost this screen its refresh, not the frame loop
        every output depends on - each adapter call has an 8 s timeout."""
        calls: list[str] = []
        monkeypatch.setattr(anm, "_refresh_pi_network_bounded", lambda app: calls.append("bounded"))
        monkeypatch.setattr(anm, "_refresh_pi_network", lambda app: calls.append("UNBOUNDED"))
        app = _make_app()
        app._pi_network_static_edit = True

        anm._cancel_static_edit(app)

        assert calls == ["bounded"]


class TestTheScreenEnumeratesInterfacesOnce:
    def test_one_enumeration_per_row_build(self, monkeypatch) -> None:
        """The list is rebuilt every frame while the screen is open, so a
        per-interface lookup walks every NIC dozens of times a second for
        data that cannot change within one frame."""
        from openfollow.runtime import app_modes_network as mod

        calls = 0

        def _spy() -> list[tuple[str, str]]:
            nonlocal calls
            calls += 1
            return [("eth0", "192.168.1.5"), ("wlan0", "169.254.8.31")]

        app = _make_app()
        anm.enter_pi_network(app)
        monkeypatch.setattr(mod, "list_iface_ipv4", _spy, raising=False)
        monkeypatch.setattr("openfollow.net_utils.list_iface_ipv4", _spy)

        calls = 0
        anm.build_pi_network_rows(app)

        assert calls == 1


class TestTheAdvisoryNeverBlanksTheScreen:
    """The screen is the last surface an operator has when the web UI is gone.

    Everything it reads off the running server is optional decoration on a
    page whose job is fixing reachability, so none of it may cost the URLs.
    """

    def test_a_raising_advisory_costs_the_notice_not_the_page(self, monkeypatch) -> None:
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})

        class _Boom:
            bind_host = "0.0.0.0"
            display_port = 80

            def get_web_bind_advisory(self) -> dict[str, str]:
                raise RuntimeError("no runtime")

        app = _make_app()
        app._web_server = _Boom()
        anm.enter_pi_network(app)

        assert "http://192.168.1.5" in _labels(app)
        assert _rows_by_kind(app, "notice") == []

    def test_a_server_without_the_advisory_is_tolerated(self, monkeypatch) -> None:
        """Boot and unit contexts hand over a partially wired server; the
        screen has to render from whatever is there."""
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        app = _make_app()
        app._web_server = SimpleNamespace(bind_host="0.0.0.0", display_port=80)
        anm.enter_pi_network(app)

        assert "http://192.168.1.5" in _labels(app)

    def test_no_server_at_all_falls_back_to_the_configured_bind(self, monkeypatch) -> None:
        """Before ``init_web_server`` runs there is nothing to read, so the
        screen resolves what the server *would* bind rather than rendering
        blank."""
        _patch_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
        app = _make_app()
        app._web_server = None
        anm.enter_pi_network(app)

        assert "http://192.168.1.5" in _labels(app)
