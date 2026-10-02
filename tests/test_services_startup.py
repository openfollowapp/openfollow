# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Integration tests for ``AppRuntimeServices`` construction and startup wiring:
GStreamer-availability guard, canvas/fullscreen setup, network-adapter selection
and state providers, privilege-broker wiring, and the network apply/renew handlers.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import openfollow.privilege.broker as broker_module
import openfollow.services as services_module
from openfollow.configuration import AppConfig
from openfollow.net_egress import Egress

pytestmark = pytest.mark.integration


class _FakeWindow:
    def __init__(self, width: int, height: int, title: str = "OpenFollow") -> None:
        self.width = width
        self.height = height
        self.title = title
        self.fullscreen_called = False
        self.handlers: dict[str, list] = {}
        self.pointer_base_visible: bool | None = None

    def add_event_handler(self, handler, event_type: str) -> None:  # noqa: ANN001
        self.handlers.setdefault(event_type, []).append(handler)

    def set_title(self, title: str) -> None:
        self.title = title

    def set_pointer_base_visible(self, visible: bool) -> None:
        self.pointer_base_visible = visible

    def fullscreen(self) -> None:
        self.fullscreen_called = True


class _DummyApp:
    def __init__(self, *, web_commands=None) -> None:
        self._config = AppConfig(psn_system_name="OpenFollow Test")
        self._canvas = None
        # web_commands read at construction; privilege broker wires
        # password prompter to the same queue the web UI uses.
        self._web_commands = web_commands

    def _on_key_down(self, _event: dict) -> None:
        pass

    def _on_key_up(self, _event: dict) -> None:
        pass

    def _on_wheel(self, _event: dict) -> None:
        pass

    def _on_resize(self, _event: dict) -> None:
        pass

    def _on_pointer_down(self, _event: dict) -> None:
        pass

    def _on_pointer_move(self, _event: dict) -> None:
        pass

    def _on_pointer_up(self, _event: dict) -> None:
        pass

    def _on_close(self, _event: dict) -> None:
        pass

    def _on_blur(self, _event: dict) -> None:
        pass


def test_runtime_services_exit_when_gstreamer_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(services_module, "gst_runtime_available", lambda: False)
    monkeypatch.setattr(
        services_module.AppRuntimeServices,
        "_setup_gc_tuning",
        staticmethod(lambda: None),
    )

    with pytest.raises(SystemExit):
        services_module.AppRuntimeServices(_DummyApp())


def test_init_canvas_enters_fullscreen_on_raspberry_pi(monkeypatch) -> None:
    monkeypatch.setattr(services_module, "gst_runtime_available", lambda: True)
    monkeypatch.setattr(services_module, "GtkNativeSinkWindow", _FakeWindow)
    monkeypatch.setattr(
        services_module.AppRuntimeServices,
        "_setup_gc_tuning",
        staticmethod(lambda: None),
    )
    monkeypatch.setattr(
        services_module.AppRuntimeServices,
        "_is_raspberry_pi",
        staticmethod(lambda: True),
    )

    app = _DummyApp()
    services = services_module.AppRuntimeServices(app)
    services.init_canvas()

    assert isinstance(app._canvas, _FakeWindow)
    assert app._canvas.fullscreen_called is True


def test_init_canvas_stays_windowed_off_raspberry_pi(monkeypatch) -> None:
    monkeypatch.setattr(services_module, "gst_runtime_available", lambda: True)
    monkeypatch.setattr(services_module, "GtkNativeSinkWindow", _FakeWindow)
    monkeypatch.setattr(
        services_module.AppRuntimeServices,
        "_setup_gc_tuning",
        staticmethod(lambda: None),
    )
    monkeypatch.setattr(
        services_module.AppRuntimeServices,
        "_is_raspberry_pi",
        staticmethod(lambda: False),
    )

    app = _DummyApp()
    services = services_module.AppRuntimeServices(app)
    services.init_canvas()

    assert isinstance(app._canvas, _FakeWindow)
    assert app._canvas.fullscreen_called is False


def _init_canvas_with_mouse(monkeypatch, *, mouse_enabled: bool) -> _FakeWindow:
    monkeypatch.setattr(services_module, "gst_runtime_available", lambda: True)
    monkeypatch.setattr(services_module, "GtkNativeSinkWindow", _FakeWindow)
    monkeypatch.setattr(
        services_module.AppRuntimeServices,
        "_setup_gc_tuning",
        staticmethod(lambda: None),
    )
    monkeypatch.setattr(
        services_module.AppRuntimeServices,
        "_is_raspberry_pi",
        staticmethod(lambda: False),
    )
    app = _DummyApp()
    app._config.controller.mouse_enabled = mouse_enabled
    services = services_module.AppRuntimeServices(app)
    services.init_canvas()
    return app._canvas


def test_init_canvas_hides_pointer_when_mouse_disabled(monkeypatch) -> None:
    canvas = _init_canvas_with_mouse(monkeypatch, mouse_enabled=False)
    assert canvas.pointer_base_visible is False


def test_init_canvas_shows_pointer_when_mouse_enabled(monkeypatch) -> None:
    """With mouse input on, the operator needs the pointer to aim, so
    startup leaves it visible."""
    canvas = _init_canvas_with_mouse(monkeypatch, mouse_enabled=True)
    assert canvas.pointer_base_visible is True


# Network adapter wiring + state provider
def _build_services_with_psutil_backend(monkeypatch) -> services_module.AppRuntimeServices:
    """Construct AppRuntimeServices with the psutil backend forced.

    Side-steps the GStreamer / GC guards so the constructor reaches the
    network-adapter wiring path under test.
    """
    monkeypatch.setattr(services_module, "gst_runtime_available", lambda: True)
    monkeypatch.setattr(
        services_module.AppRuntimeServices,
        "_setup_gc_tuning",
        staticmethod(lambda: None),
    )
    monkeypatch.setenv("OPENFOLLOW_NETWORK_BACKEND", "psutil")
    return services_module.AppRuntimeServices(_DummyApp())


def test_network_adapter_property_returns_wired_adapter(monkeypatch) -> None:
    """Network adapter property exposes the selected backend."""
    services = _build_services_with_psutil_backend(monkeypatch)
    from openfollow.network.psutil_adapter import PsutilReadOnlyAdapter

    assert isinstance(services.network_adapter, PsutilReadOnlyAdapter)


def test_network_backend_choice_defaults_to_auto_without_config(monkeypatch) -> None:
    """Static helper must tolerate the no-config / no-network-section case."""
    from openfollow.services import AppRuntimeServices

    class _NoCfg:
        pass

    assert AppRuntimeServices._network_backend_choice(_NoCfg()) == "auto"


def test_network_backend_choice_reads_config_value(monkeypatch) -> None:
    from types import SimpleNamespace

    from openfollow.configuration import NetworkConfig
    from openfollow.services import AppRuntimeServices

    app = SimpleNamespace(_config=SimpleNamespace(network=NetworkConfig(backend="dhcpcd")))
    assert AppRuntimeServices._network_backend_choice(app) == "dhcpcd"


def test_network_backend_choice_falls_back_when_section_missing(monkeypatch) -> None:
    """``[network]`` may legitimately be absent in older configs."""
    from types import SimpleNamespace

    from openfollow.services import AppRuntimeServices

    app = SimpleNamespace(_config=SimpleNamespace())  # no .network attribute
    assert AppRuntimeServices._network_backend_choice(app) == "auto"


def test_network_state_provider_returns_none_when_no_adapter(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    services._network_adapter = None
    assert services._network_state_provider() is None


def test_network_state_provider_returns_empty_when_no_interfaces(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)

    class _FakeAdapter:
        backend_name = "fake"

        def list_interfaces(self):
            return []

        def is_writable(self):
            return False

        def get_state(self, _iface):
            return None

    services._network_adapter = _FakeAdapter()
    snapshot = services._network_state_provider()
    assert snapshot == {"interfaces": [], "writable": False}


def test_network_state_provider_returns_full_state(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)

    from openfollow.network.adapter import (
        Ipv4Config,
        Ipv4Method,
        LeaseInfo,
        NetworkInterface,
        NetworkState,
    )

    class _FakeAdapter:
        backend_name = "fake-dhcpcd"

        def list_interfaces(self):
            return [
                NetworkInterface(name="eth0", mac="aa:bb", kind="ethernet", is_up=True),
            ]

        def is_writable(self):
            return True

        def get_state(self, iface):
            return NetworkState(
                interface=NetworkInterface(name=iface, mac="aa:bb", kind="ethernet", is_up=True),
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
                    lease_seconds_remaining=600,
                ),
            )

    services._network_adapter = _FakeAdapter()
    snap = services._network_state_provider()
    assert snap["active_interface"] == "eth0"
    assert snap["address"] == "192.168.1.50"
    assert snap["method"] == "DHCP"
    assert snap["lease_remaining"] == 600
    assert snap["backend"] == "fake-dhcpcd"


def test_network_state_provider_filters_loopback(monkeypatch) -> None:
    """The ``_network_state_provider`` must skip loopback and pick the first real
    ``is_up`` interface to surface the correct device address in the web Overview."""
    services = _build_services_with_psutil_backend(monkeypatch)
    from openfollow.network.adapter import (
        Ipv4Config,
        Ipv4Method,
        NetworkInterface,
        NetworkState,
    )

    class _FakeAdapter:
        backend_name = "fake"

        def list_interfaces(self):
            return [
                NetworkInterface(name="lo", mac=None, kind=None, is_up=True),
                NetworkInterface(name="eth0", mac="aa:bb", kind="ethernet", is_up=True),
            ]

        def is_writable(self):
            return True

        def get_state(self, iface):
            return NetworkState(
                interface=NetworkInterface(name=iface, mac=None, kind=None, is_up=True),
                ipv4=Ipv4Config(method=Ipv4Method.DHCP, address="192.168.1.50", prefix=24),
                lease=None,
            )

    services._network_adapter = _FakeAdapter()
    snap = services._network_state_provider()
    assert snap["active_interface"] == "eth0"
    assert snap["interfaces"] == ["eth0"]


def test_network_state_provider_returns_empty_when_only_loopback(monkeypatch) -> None:
    """If the host only exposes loopback (e.g. CI sandboxes), surface
    an empty snapshot rather than picking ``lo`` as ``chosen``."""
    services = _build_services_with_psutil_backend(monkeypatch)
    from openfollow.network.adapter import NetworkInterface

    class _FakeAdapter:
        backend_name = "fake"

        def list_interfaces(self):
            return [NetworkInterface(name="lo", mac=None, kind="loopback", is_up=True)]

        def is_writable(self):
            return False

        def get_state(self, _iface):
            return None

    services._network_adapter = _FakeAdapter()
    snap = services._network_state_provider()
    assert snap == {"interfaces": [], "writable": False}


def test_network_state_provider_when_get_state_returns_none(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    from openfollow.network.adapter import NetworkInterface

    class _FakeAdapter:
        backend_name = "fake-nm"

        def list_interfaces(self):
            return [
                NetworkInterface(name="eth0", mac=None, kind=None, is_up=False),
                NetworkInterface(name="wlan0", mac=None, kind=None, is_up=False),
            ]

        def is_writable(self):
            return True

        def get_state(self, _iface):
            return None

    services._network_adapter = _FakeAdapter()
    snap = services._network_state_provider()
    assert snap == {
        "interfaces": ["eth0", "wlan0"],
        "writable": True,
        "backend": "fake-nm",
    }


# Privilege broker wiring
def test_privilege_broker_property_returns_broker(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    from openfollow.privilege import PrivilegeBroker

    assert isinstance(services.privilege_broker, PrivilegeBroker)


def test_privilege_states_provider_returns_value_dict(monkeypatch) -> None:
    """``_privilege_states_provider`` flattens the broker's enum
    snapshot into a ``{name: state.value}`` dict the templates can
    diff against string literals."""
    services = _build_services_with_psutil_backend(monkeypatch)
    states = services._privilege_states_provider()
    assert isinstance(states, dict)
    # Every value must be a string (the enum's ``.value``), never an
    # Enum member – bottle templates compare to strings.
    assert all(isinstance(v, str) for v in states.values())


def test_privilege_states_provider_returns_empty_without_broker(monkeypatch) -> None:
    """Test the defensive ``broker is None`` branch – when called
    before broker init completes (e.g. mid-bootstrap) the provider
    short-circuits to an empty dict instead of raising."""
    services = _build_services_with_psutil_backend(monkeypatch)
    delattr(services, "_privilege_broker")
    assert services._privilege_states_provider() == {}


# Autostart providers (boot-enablement read + write for the General tab)


def _stub_autostart(monkeypatch, *, read=None, write=None) -> None:
    import openfollow.privilege.autostart as autostart_module

    if read is not None:
        monkeypatch.setattr(autostart_module, "read_autostart", read)
    if write is not None:
        monkeypatch.setattr(autostart_module, "set_autostart", write)


def test_autostart_state_provider_flattens_the_host_read(monkeypatch) -> None:
    """Templates compare plain values, so the dataclass is flattened here."""
    from openfollow.privilege.autostart import AutostartState

    services = _build_services_with_psutil_backend(monkeypatch)
    seen: list[str] = []

    def _read(name):
        seen.append(name)
        return AutostartState(available=True, enabled=True, reason="")

    _stub_autostart(monkeypatch, read=_read)
    assert services._autostart_state_provider("openfollow") == {
        "available": True,
        "enabled": True,
        "reason": "",
    }
    assert seen == ["openfollow"]


def test_handle_autostart_apply_reports_the_state_after_the_write(monkeypatch) -> None:
    from openfollow.privilege.autostart import AutostartState

    services = _build_services_with_psutil_backend(monkeypatch)
    calls: list[tuple[str, bool]] = []

    def _write(_broker, name, *, enabled):
        calls.append((name, enabled))
        return AutostartState(available=True, enabled=enabled, reason="")

    _stub_autostart(monkeypatch, write=_write)
    result = services._handle_autostart_apply("openfollow", False)
    assert calls == [("openfollow", False)]
    assert result == {"ok": True, "error": "", "available": True, "enabled": False, "reason": ""}


def test_handle_autostart_apply_refuses_success_when_the_host_disagrees(monkeypatch) -> None:
    """A zero exit from systemctl is not the same as the setting having taken.

    Reporting ok on the exit code alone renders a success banner announcing the
    opposite of the switch drawn beside it.
    """
    from openfollow.privilege.autostart import AutostartState

    services = _build_services_with_psutil_backend(monkeypatch)
    _stub_autostart(
        monkeypatch,
        write=lambda _broker, _name, *, enabled: AutostartState(available=True, enabled=False, reason=""),
    )
    result = services._handle_autostart_apply("openfollow", True)
    assert result["ok"] is False
    assert result["error"] == "The station accepted the change but did not apply it."
    assert result["enabled"] is False


def test_handle_autostart_apply_surfaces_a_host_that_became_unswitchable(monkeypatch) -> None:
    """When the post-write read says why it can't be switched, that beats the
    generic "did not apply" sentence."""
    from openfollow.privilege.autostart import AutostartState

    services = _build_services_with_psutil_backend(monkeypatch)
    _stub_autostart(
        monkeypatch,
        write=lambda _broker, _name, *, enabled: AutostartState(
            available=False, enabled=False, reason="This service is masked on this host."
        ),
    )
    result = services._handle_autostart_apply("openfollow", True)
    assert result["ok"] is False
    assert result["error"] == "This service is masked on this host."


def test_handle_autostart_apply_rereads_the_host_when_the_write_fails(monkeypatch) -> None:
    """A refused change still has to answer with where the host actually is."""
    from openfollow.privilege.autostart import AutostartState
    from openfollow.privilege.broker import PrivilegeError

    services = _build_services_with_psutil_backend(monkeypatch)

    def _write(_broker, _name, *, enabled):
        raise PrivilegeError("Disable a systemd unit: Interactive authentication required.")

    _stub_autostart(
        monkeypatch,
        read=lambda _name: AutostartState(available=True, enabled=True, reason=""),
        write=_write,
    )
    result = services._handle_autostart_apply("openfollow", False)
    assert result["ok"] is False
    assert result["error"] == "Interactive authentication required."
    assert result["enabled"] is True


def test_handle_autostart_apply_without_a_broker_answers_the_full_shape(monkeypatch) -> None:
    """The renderer reads state keys unconditionally, so every path carries them."""
    services = _build_services_with_psutil_backend(monkeypatch)
    delattr(services, "_privilege_broker")
    result = services._handle_autostart_apply("openfollow", True)
    assert result["ok"] is False
    assert result["available"] is False
    assert result["enabled"] is False
    assert result["reason"] == result["error"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            f"Disable a systemd unit: {broker_module.PROMPT_CANCELLED_DETAIL}",
            "Cancelled - the setting was not changed.",
        ),
        ("Enable a systemd unit: Failed to enable unit.", "Failed to enable unit."),
        ("no colon here", "no colon here"),
        ("Enable a systemd unit: ", "The setting could not be changed."),
    ],
)
def test_autostart_failure_text_drops_the_capability_prefix(raw: str, expected: str) -> None:
    """The broker's prefix names internal plumbing; the operator reads the reason."""
    assert services_module._autostart_failure_text(Exception(raw)) == expected


def test_autostart_failure_text_does_not_promise_a_timeout_changed_nothing() -> None:
    """A command timeout may have applied the setting.

    Reporting it as "not changed" would sit above a switch showing the new
    state - the same contradiction the post-write check exists to prevent. Only
    a dismissed password prompt can promise nothing happened.
    """
    text = services_module._autostart_failure_text(Exception("Enable a systemd unit: timed out after 10s."))
    assert text == "timed out after 10s."


# Privilege prompter closure (created during init)
class _FakeWebCommands:
    """Stand-in for :class:`WebCommandQueue` that records calls without
    threading or events. Used to exercise the ``_prompt_for_password``
    closure registered on the broker at init."""

    def __init__(self, *, request_ok: bool = True, password: str | None = "hunter2") -> None:
        self.request_ok = request_ok
        self.password = password
        self.request_calls: list[tuple[str, str]] = []
        self.consume_calls: list[float] = []

    def request_privilege_password(self, *, reason: str, capability_name: str) -> bool:
        self.request_calls.append((reason, capability_name))
        return self.request_ok

    def consume_privilege_password(self, timeout: float):
        self.consume_calls.append(timeout)
        return self.password


def _build_services_with_web_commands(monkeypatch, web_commands):
    monkeypatch.setattr(services_module, "gst_runtime_available", lambda: True)
    monkeypatch.setattr(
        services_module.AppRuntimeServices,
        "_setup_gc_tuning",
        staticmethod(lambda: None),
    )
    monkeypatch.setenv("OPENFOLLOW_NETWORK_BACKEND", "psutil")
    app = _DummyApp(web_commands=web_commands)
    return services_module.AppRuntimeServices(app)


def test_prompter_returns_password_when_request_accepted(monkeypatch) -> None:
    """Happy path: broker calls the closure → it requests a prompt →
    queue accepts → consume returns the operator's password."""
    web = _FakeWebCommands(request_ok=True, password="hunter2")
    services = _build_services_with_web_commands(monkeypatch, web)
    from openfollow.privilege.capabilities import SERVICE_RESTART

    result = services._privilege_broker._prompter(SERVICE_RESTART, "Restart service")
    assert result == "hunter2"
    assert web.request_calls == [("Restart service", "service.restart")]
    assert web.consume_calls == [300.0]


def test_prompter_returns_none_when_queue_busy(monkeypatch) -> None:
    """When another prompt is already in flight, the closure refuses
    rather than parking and showing the wrong reason text."""
    web = _FakeWebCommands(request_ok=False)
    services = _build_services_with_web_commands(monkeypatch, web)
    from openfollow.privilege.capabilities import SERVICE_RESTART

    result = services._privilege_broker._prompter(SERVICE_RESTART, "x")
    assert result is None
    assert web.consume_calls == []


def test_prompter_short_circuits_when_web_commands_missing(monkeypatch) -> None:
    """Defensive branch – if web_commands is somehow None (test /
    headless construction path) the closure returns None instead of
    raising AttributeError."""
    services = _build_services_with_web_commands(monkeypatch, web_commands=None)
    assert services._privilege_broker._prompter is None


# _format_lease_remaining (web-facing helper)
class TestFormatLeaseRemaining:
    """Compact human label for DHCP lease seconds-remaining. Covers
    every branch – the NM lease bug surfaced as "29664461 min" in the
    on-device screenshot because the previous renderer divided raw
    epoch by 60. The helper is the single source of truth now."""

    def test_none_returns_none(self) -> None:
        from openfollow.services import _format_lease_remaining

        assert _format_lease_remaining(None) is None

    def test_zero_renders_as_seconds(self) -> None:
        """Clamped lease shows 0s so the operator sees the lease is
        actively expiring rather than a stale display."""
        from openfollow.services import _format_lease_remaining

        assert _format_lease_remaining(0) == "0 s"

    def test_under_a_minute_renders_seconds(self) -> None:
        from openfollow.services import _format_lease_remaining

        assert _format_lease_remaining(45) == "45 s"

    def test_under_an_hour_renders_minutes(self) -> None:
        from openfollow.services import _format_lease_remaining

        assert _format_lease_remaining(3 * 60) == "3 min"
        assert _format_lease_remaining(59 * 60) == "59 min"

    def test_under_a_day_renders_hours_and_minutes(self) -> None:
        from openfollow.services import _format_lease_remaining

        assert _format_lease_remaining(2 * 3600 + 13 * 60) == "2h 13m"
        # Zero-pad the minute slot so columns align in operator-facing UIs.
        assert _format_lease_remaining(5 * 3600 + 7 * 60) == "5h 07m"

    def test_multi_day_renders_days_and_hours(self) -> None:
        from openfollow.services import _format_lease_remaining

        assert _format_lease_remaining(3 * 86400 + 4 * 3600) == "3d 04h"

    def test_negative_input_clamps_to_zero(self) -> None:
        """Belt-and-braces: even if a caller passes a negative value
        (the NM adapter clamps to 0 on its side, but other future
        adapters might not), the helper still produces a clean label."""
        from openfollow.services import _format_lease_remaining

        assert _format_lease_remaining(-100) == "0 s"


# Web network write providers / handlers (services layer)
class _FakeWritableAdapter:
    """Minimal writable NetworkAdapter stand-in for network handlers."""

    backend_name = "fake"

    def __init__(self, interfaces, state) -> None:
        self._ifaces = interfaces
        self._state = state
        self.applied: list = []
        self.renewed: list = []
        self.vlans: list = []
        self.vlans_created: list = []
        self.vlans_deleted: list = []
        self.vlan_support = True
        self.vlan_list_raises = False

    def list_interfaces(self):
        return list(self._ifaces)

    def get_state(self, iface):
        return self._state

    def is_writable(self) -> bool:
        return True

    def apply_ipv4(self, iface, config):
        from openfollow.network.adapter import ApplyResult

        self.applied.append((iface, config))
        return ApplyResult(ok=True)

    def renew_lease(self, iface):
        from openfollow.network.adapter import ApplyResult

        self.renewed.append(iface)
        return ApplyResult(ok=True)

    def supports_vlans(self) -> bool:
        return self.vlan_support

    def list_vlans(self):
        if self.vlan_list_raises:
            raise RuntimeError("nmcli exploded")
        return list(self.vlans)

    def create_vlan(self, parent, vlan_id):
        from openfollow.network.adapter import ApplyResult

        self.vlans_created.append((parent, vlan_id))
        return ApplyResult(ok=True)

    def delete_vlan(self, name):
        from openfollow.network.adapter import ApplyResult

        self.vlans_deleted.append(name)
        return ApplyResult(ok=True)


def _iface(name="eth0", up=True):
    from openfollow.network.adapter import NetworkInterface

    return NetworkInterface(name=name, mac=None, kind="ether", is_up=up)


def test_network_config_provider_none_without_adapter(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    services._network_adapter = None
    assert services._network_config_provider() is None


def test_network_config_provider_empty_interfaces(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    services._network_adapter = _FakeWritableAdapter([], None)
    assert services._network_config_provider() == {
        "interfaces": [],
        "writable": True,
        "backend": "fake",
    }


def test_network_config_provider_no_state(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    services._network_adapter = _FakeWritableAdapter([_iface()], None)
    cfg = services._network_config_provider()
    assert cfg["active_interface"] == "eth0"
    assert cfg["method"] == "dhcp" and cfg["address"] == ""
    assert cfg["prefix"] is None and cfg["lease_display"] is None


def test_network_config_provider_full_state_with_lease(monkeypatch) -> None:
    from openfollow.network.adapter import (
        Ipv4Config,
        Ipv4Method,
        LeaseInfo,
        NetworkState,
    )

    services = _build_services_with_psutil_backend(monkeypatch)
    iface = _iface()
    state = NetworkState(
        interface=iface,
        ipv4=Ipv4Config(
            method=Ipv4Method.STATIC,
            address="10.0.0.5",
            prefix=24,
            router="10.0.0.1",
            dns=("1.1.1.1",),
        ),
        lease=LeaseInfo(
            address="10.0.0.5",
            prefix=24,
            router="10.0.0.1",
            dns=("1.1.1.1",),
            lease_seconds_remaining=3600,
        ),
    )
    services._network_adapter = _FakeWritableAdapter([iface], state)
    cfg = services._network_config_provider(iface="eth0")
    assert cfg["method"] == "static"
    assert cfg["address"] == "10.0.0.5" and cfg["prefix"] == 24
    assert cfg["router"] == "10.0.0.1" and cfg["dns"] == ["1.1.1.1"]
    assert cfg["writable"] is True
    assert cfg["lease_display"]  # non-empty formatted label


def test_handle_network_apply_writable_calls_adapter(monkeypatch) -> None:
    from openfollow.network.adapter import Ipv4Config, Ipv4Method

    services = _build_services_with_psutil_backend(monkeypatch)
    fake = _FakeWritableAdapter([_iface()], None)
    services._network_adapter = fake
    config = Ipv4Config(method=Ipv4Method.DHCP)
    result = services._handle_network_apply("eth0", config)
    assert result.ok is True
    assert fake.applied == [("eth0", config)]


def test_handle_network_apply_read_only_host(monkeypatch) -> None:
    # The psutil backend is read-only.
    services = _build_services_with_psutil_backend(monkeypatch)
    from openfollow.network.adapter import Ipv4Config, Ipv4Method

    result = services._handle_network_apply(
        "eth0",
        Ipv4Config(method=Ipv4Method.DHCP),
    )
    assert result.ok is False and "Read-only" in result.message


def test_handle_network_apply_no_adapter(monkeypatch) -> None:
    from openfollow.network.adapter import Ipv4Config, Ipv4Method

    services = _build_services_with_psutil_backend(monkeypatch)
    services._network_adapter = None
    result = services._handle_network_apply(
        "eth0",
        Ipv4Config(method=Ipv4Method.DHCP),
    )
    assert result.ok is False and "No network adapter" in result.message


def test_handle_network_renew_writable_calls_adapter(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    fake = _FakeWritableAdapter([_iface()], None)
    services._network_adapter = fake
    result = services._handle_network_renew("eth0")
    assert result.ok is True and fake.renewed == ["eth0"]


def test_handle_network_renew_read_only_host(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    result = services._handle_network_renew("eth0")
    assert result.ok is False and "Read-only" in result.message


def test_handle_network_renew_no_adapter(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    services._network_adapter = None
    result = services._handle_network_renew("eth0")
    assert result.ok is False and "No network adapter" in result.message


# ---------------------------------------------------------------------------
# Diagnostics providers wired into the web server
# ---------------------------------------------------------------------------


def test_online_sync_status_provider_reports_nothing_before_the_worker_exists(monkeypatch) -> None:
    """``init_web_server`` runs before ``init_online_sync``, so the provider is
    handed to the server while the worker is still absent. It has to read as
    "not wired" rather than raise on a bundle downloaded during startup."""
    services = _build_services_with_psutil_backend(monkeypatch)
    assert services._online_sync_status_provider() == {}


def test_online_sync_status_provider_returns_the_workers_health(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    services._online_sync = SimpleNamespace(health=lambda: {"online": True, "cycles": 3})
    assert services._online_sync_status_provider() == {"online": True, "cycles": 3}


def test_online_sync_status_provider_copies_the_health_mapping(monkeypatch) -> None:
    """The worker publishes an immutable snapshot; the dict built from it is
    still handed across a thread boundary, so the provider does not share one
    the caller could mutate."""
    services = _build_services_with_psutil_backend(monkeypatch)
    live = {"online": False}
    services._online_sync = SimpleNamespace(health=lambda: live)
    handed_out = services._online_sync_status_provider()
    handed_out["online"] = True
    assert live == {"online": False}


# Pi camera setup providers (Video Source -> Pi Camera -> Camera setup)


def _camera_state(**kw):  # noqa: ANN003, ANN202
    from openfollow.privilege.camera_config import CameraSetupState

    return CameraSetupState(available=True, sensors=("imx708", "ov5647"), **kw)


def _camera_services(monkeypatch):  # noqa: ANN001, ANN202
    return _build_services_with_web_commands(monkeypatch, services_module.WebCommandQueue())


def _stub_camera(monkeypatch, *, read=None, apply=None, restart=None) -> None:  # noqa: ANN001
    import openfollow.privilege.camera_config as config_module
    import openfollow.privilege.camera_setup as setup_module

    if read is not None:
        monkeypatch.setattr(config_module, "read_camera_setup", read)
    if apply is not None:
        monkeypatch.setattr(setup_module, "apply_camera", apply)
    if restart is not None:
        monkeypatch.setattr(setup_module, "restart_station", restart)


def test_camera_setup_state_provider_flattens_the_host_read(monkeypatch) -> None:  # noqa: ANN001
    from openfollow.privilege.camera_config import Camera

    ov = Camera("ov5647", "cam0")
    import openfollow.video.inputs.picam as picam_module

    services = _camera_services(monkeypatch)
    _stub_camera(monkeypatch, read=lambda: _camera_state(configured=ov, managed=True, active=(ov,), live=ov))
    detected = [{"model": "ov5647", "path": "/base/ov5647@36"}]
    monkeypatch.setattr(picam_module, "discover_cameras", lambda: detected)
    assert services._camera_setup_state_provider() == {
        "available": True,
        "reason": "",
        "sensors": ["imx708", "ov5647"],
        "configured": "ov5647,cam0",
        "managed": True,
        "active": ["ov5647,cam0"],
        "live": "ov5647,cam0",
        "pending": False,
        "detected": detected,
    }


@pytest.mark.parametrize("changed", [True, False])
def test_camera_setup_apply_rebuilds_the_pipeline_only_after_a_live_change(monkeypatch, changed) -> None:  # noqa: ANN001
    from openfollow.privilege.camera_config import Camera

    services = _camera_services(monkeypatch)
    seen: list[object] = []

    def _apply(_broker, choice, release):  # noqa: ANN001, ANN202
        seen.append(choice)
        return _camera_state(configured=choice), changed

    _stub_camera(monkeypatch, apply=_apply)
    result = services._handle_camera_setup_apply("ov5647,cam0")
    assert seen == [Camera("ov5647", "cam0")]
    assert (result["ok"], result["configured"]) == (True, "ov5647,cam0")
    assert services._app._web_commands.consume_video_rebuild_requested() is changed


def test_camera_setup_apply_automatic_passes_none(monkeypatch) -> None:  # noqa: ANN001
    services = _camera_services(monkeypatch)
    seen: list[object] = []
    _stub_camera(monkeypatch, apply=lambda _b, choice, release: (seen.append(choice) or _camera_state(), False))
    assert services._handle_camera_setup_apply("automatic")["ok"] is True
    assert seen == [None]


def test_camera_setup_apply_refuses_a_token_that_is_no_camera(monkeypatch) -> None:  # noqa: ANN001
    services = _camera_services(monkeypatch)
    _stub_camera(monkeypatch, read=_camera_state, apply=lambda *_a, **_kw: pytest.fail("must not apply"))
    result = services._handle_camera_setup_apply("../etc,cam0")
    assert (result["ok"], result["error"], result["available"]) == (
        False,
        "That is not a camera this station offers.",
        True,
    )


def test_camera_setup_apply_reports_a_refused_change(monkeypatch) -> None:  # noqa: ANN001
    from openfollow.privilege.broker import PrivilegeError

    def _refuse(*_a, **_kw):  # noqa: ANN002, ANN003, ANN202
        raise PrivilegeError("Name the Pi camera in the boot configuration: This user is not in the sudoers file.")

    services = _camera_services(monkeypatch)
    _stub_camera(monkeypatch, read=_camera_state, apply=_refuse)
    result = services._handle_camera_setup_apply("ov5647,cam0")
    assert (result["ok"], result["error"]) == (False, "This user is not in the sudoers file.")
    assert result["sensors"] == ["imx708", "ov5647"]  # re-read from the host
    assert services._app._web_commands.consume_video_rebuild_requested() is False


@pytest.mark.parametrize("refused", [False, True], ids=["applied", "unload-refused"])
def test_camera_setup_rebuilds_after_every_release(monkeypatch, refused) -> None:  # noqa: ANN001
    """A pipeline stopped for an unload is built again, whether or not the change went through."""
    from openfollow.privilege.broker import PrivilegeError

    monkeypatch.setattr(services_module, "_CAMERA_RELEASE_TIMEOUT_S", 0.01)  # no main loop answers here
    services = _camera_services(monkeypatch)
    answers: list[bool] = []

    def _apply(_broker, _choice, release):  # noqa: ANN001, ANN202
        answers.append(release())
        if refused:
            raise PrivilegeError("Stop the Pi camera started earlier: refused")
        return _camera_state(pending=True), False

    _stub_camera(monkeypatch, read=_camera_state, apply=_apply)
    result = services._handle_camera_setup_apply("imx708,cam0")
    assert answers == [False]  # unanswered: apply_camera leaves the camera loaded
    assert result["ok"] is not refused
    assert services._app._web_commands.consume_video_rebuild_requested() is True


def test_camera_setup_applies_one_change_at_a_time(monkeypatch) -> None:  # noqa: ANN001
    import threading

    services = _camera_services(monkeypatch)
    inside: list[str] = []
    first_in = threading.Event()
    let_first_finish = threading.Event()

    def _apply(_broker, choice, release):  # noqa: ANN001, ANN202
        inside.append(f"enter {choice.sensor}")
        if choice.sensor == "ov5647":
            first_in.set()
            let_first_finish.wait(5)
        inside.append(f"leave {choice.sensor}")
        return _camera_state(), False

    _stub_camera(monkeypatch, apply=_apply)
    first = threading.Thread(target=services._handle_camera_setup_apply, args=("ov5647,cam0",))
    first.start()
    first_in.wait(5)
    second = threading.Thread(target=services._handle_camera_setup_apply, args=("imx708,cam0",))
    second.start()
    second.join(timeout=0.2)
    assert second.is_alive()  # held at the lock while the first change runs
    let_first_finish.set()
    first.join(5)
    second.join(5)
    assert inside == ["enter ov5647", "leave ov5647", "enter imx708", "leave imx708"]


def test_camera_setup_without_a_broker(monkeypatch) -> None:  # noqa: ANN001
    services = _camera_services(monkeypatch)
    delattr(services, "_privilege_broker")
    _stub_camera(monkeypatch, read=_camera_state)
    assert (
        services._handle_camera_setup_apply("ov5647,cam0")["error"]
        == "Elevated actions are not available on this build."
    )
    assert services._handle_camera_setup_restart() == {
        "ok": False,
        "error": "Elevated actions are not available on this build.",
    }


def test_camera_setup_restart(monkeypatch) -> None:  # noqa: ANN001
    from openfollow.privilege.broker import PrivilegeError

    services = _camera_services(monkeypatch)
    calls: list[object] = []
    _stub_camera(monkeypatch, restart=calls.append)
    assert services._handle_camera_setup_restart() == {"ok": True}
    assert calls == [services._privilege_broker]

    def _refuse(_broker):  # noqa: ANN001, ANN202
        raise PrivilegeError("Restart the station: refused")

    _stub_camera(monkeypatch, restart=_refuse)
    assert services._handle_camera_setup_restart() == {"ok": False, "error": "refused"}


# --------------------------------------------------------------------------- #
# _network_interfaces_provider – backs the Network Settings interface list
# --------------------------------------------------------------------------- #


def _ifrow(name: str, *, is_up: bool = True, kind: str = "ethernet"):
    from openfollow.network.adapter import NetworkInterface

    return NetworkInterface(name=name, mac="aa:bb", kind=kind, is_up=is_up)


def _ifrow_state(
    iface: str,
    *,
    address: str,
    prefix: int | None,
    method_value: str,
    router: str | None = None,
    dns: tuple[str, ...] = (),
    lease_seconds: int | None = None,
):
    from openfollow.network.adapter import Ipv4Config, Ipv4Method, LeaseInfo, NetworkState

    return NetworkState(
        interface=_ifrow(iface),
        ipv4=Ipv4Config(
            method=Ipv4Method(method_value),
            address=address,
            prefix=prefix,
            router=router,
            dns=dns,
        ),
        lease=None
        if lease_seconds is None
        else LeaseInfo(
            address=address,
            prefix=prefix,
            router=router,
            dns=dns,
            lease_seconds_remaining=lease_seconds,
        ),
    )


def test_network_interfaces_provider_returns_empty_without_adapter(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    services._network_adapter = None
    assert services._network_interfaces_provider() == []


def test_network_interfaces_provider_lists_every_interface(monkeypatch) -> None:
    """The old form showed one adapter at a time; this backs the list that
    replaced it, so every adapter carries its own address and method."""
    services = _build_services_with_psutil_backend(monkeypatch)

    class _FakeAdapter:
        backend_name = "fake"

        def list_interfaces(self):
            return [_ifrow("eth0"), _ifrow("eth1")]

        def is_writable(self):
            return True

        def get_state(self, iface):
            if iface == "eth0":
                return _ifrow_state(iface, address="192.168.1.5", prefix=24, method_value="dhcp")
            return _ifrow_state(iface, address="10.0.0.9", prefix=16, method_value="static")

    services._network_adapter = _FakeAdapter()
    rows = {r["name"]: r for r in services._network_interfaces_provider()}
    assert rows["eth0"]["address"] == "192.168.1.5"
    assert rows["eth0"]["method"] == "dhcp"
    assert rows["eth0"]["subnet_mask"] == "255.255.255.0"
    assert rows["eth1"]["address"] == "10.0.0.9"
    assert rows["eth1"]["method"] == "static"
    assert rows["eth1"]["prefix"] == 16


def test_network_interfaces_provider_carries_router_dns_and_lease(monkeypatch) -> None:
    """The card renders every interface's editor, not just the active one's,
    and does it off this list. ``get_state`` already returns router / DNS /
    lease, so dropping them here would cost a second backend read per row."""
    services = _build_services_with_psutil_backend(monkeypatch)

    class _FakeAdapter:
        backend_name = "fake"

        def list_interfaces(self):
            return [_ifrow("eth0"), _ifrow("eth1")]

        def is_writable(self):
            return True

        def get_state(self, iface):
            if iface == "eth0":
                return _ifrow_state(
                    iface,
                    address="192.168.1.5",
                    prefix=24,
                    method_value="dhcp",
                    router="192.168.1.1",
                    dns=("1.1.1.1", "8.8.8.8"),
                    lease_seconds=3600,
                )
            return _ifrow_state(iface, address="10.0.0.9", prefix=16, method_value="static")

    services._network_adapter = _FakeAdapter()
    rows = {r["name"]: r for r in services._network_interfaces_provider()}
    assert rows["eth0"]["router"] == "192.168.1.1"
    assert rows["eth0"]["dns"] == ["1.1.1.1", "8.8.8.8"]
    assert rows["eth0"]["lease_display"]
    # A static interface has no lease and no router of its own here; the keys
    # are still present so the renderer never has to guess at a missing one.
    assert rows["eth1"]["router"] == ""
    assert rows["eth1"]["dns"] == []
    assert rows["eth1"]["lease_display"] is None


def test_network_interfaces_provider_defaults_the_detail_when_state_is_unreadable(monkeypatch) -> None:
    """A per-interface read can fail without invalidating the rest of the
    list, and the row still has to carry every key the editor renders."""
    services = _build_services_with_psutil_backend(monkeypatch)

    class _FakeAdapter:
        backend_name = "fake"

        def list_interfaces(self):
            return [_ifrow("eth0")]

        def is_writable(self):
            return True

        def get_state(self, iface):
            raise RuntimeError("backend hiccup")

    services._network_adapter = _FakeAdapter()
    (row,) = services._network_interfaces_provider()
    assert row["router"] == ""
    assert row["dns"] == []
    assert row["lease_display"] is None


def test_network_interfaces_provider_skips_loopback(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)

    class _FakeAdapter:
        backend_name = "fake"

        def list_interfaces(self):
            return [_ifrow("lo", kind="loopback"), _ifrow("eth0")]

        def is_writable(self):
            return True

        def get_state(self, iface):
            return _ifrow_state(iface, address="192.168.1.5", prefix=24, method_value="dhcp")

    services._network_adapter = _FakeAdapter()
    assert [r["name"] for r in services._network_interfaces_provider()] == ["eth0"]


def test_network_interfaces_provider_carries_the_device_type(monkeypatch) -> None:
    """The web card tells a VLAN device from a parent by it, whether or not a
    VLAN profile still names it."""
    services = _build_services_with_psutil_backend(monkeypatch)

    class _FakeAdapter:
        backend_name = "fake"

        def list_interfaces(self):
            return [_ifrow("eth0"), _ifrow("eth0.10", kind="vlan")]

        def is_writable(self):
            return True

        def get_state(self, iface):
            return _ifrow_state(iface, address="192.168.1.5", prefix=24, method_value="dhcp")

    services._network_adapter = _FakeAdapter()
    rows = services._network_interfaces_provider()
    assert {r["name"]: r["kind"] for r in rows} == {"eth0": "ethernet", "eth0.10": "vlan"}


def test_network_interfaces_provider_reports_an_addressless_interface(monkeypatch) -> None:
    """An interface with no state still has to appear – "wlan0 has no address"
    is exactly what the operator needs to see."""
    services = _build_services_with_psutil_backend(monkeypatch)

    class _FakeAdapter:
        backend_name = "fake"

        def list_interfaces(self):
            return [_ifrow("wlan0", is_up=False)]

        def is_writable(self):
            return True

        def get_state(self, _iface):
            return None

    services._network_adapter = _FakeAdapter()
    (row,) = services._network_interfaces_provider()
    assert row == {
        "name": "wlan0",
        "kind": "ethernet",
        "is_up": False,
        "address": "",
        "prefix": None,
        "subnet_mask": "",
        "method": "dhcp",
        "router": "",
        "dns": [],
        "lease_display": None,
    }


def test_network_interfaces_provider_degrades_one_failing_row(monkeypatch) -> None:
    """One adapter read failing (interface vanishing mid-scan, backend hiccup)
    must degrade that row, not drop the whole list."""
    services = _build_services_with_psutil_backend(monkeypatch)

    class _FakeAdapter:
        backend_name = "fake"

        def list_interfaces(self):
            return [_ifrow("eth0"), _ifrow("eth1")]

        def is_writable(self):
            return True

        def get_state(self, iface):
            if iface == "eth0":
                raise RuntimeError("nmcli exploded")
            return _ifrow_state(iface, address="10.0.0.9", prefix=24, method_value="dhcp")

    services._network_adapter = _FakeAdapter()
    rows = {r["name"]: r for r in services._network_interfaces_provider()}
    assert rows["eth0"]["address"] == ""
    assert rows["eth1"]["address"] == "10.0.0.9"


# --------------------------------------------------------------------------- #
# Network plane observer wiring
# --------------------------------------------------------------------------- #


def _fake_ifaces(monkeypatch, spec: dict[str, str]) -> None:
    import socket as _socket
    from types import SimpleNamespace

    import openfollow.net_utils as net_utils_module

    monkeypatch.setattr(
        net_utils_module.psutil,
        "net_if_addrs",
        lambda: {name: [SimpleNamespace(family=_socket.AF_INET, address=addr)] for name, addr in spec.items()},
    )


def test_planes_resolve_their_own_and_the_station_interface(monkeypatch) -> None:
    """PSN is the station itself; OTP inherits it only when its own pin is blank."""
    services = _build_services_with_psutil_backend(monkeypatch)
    _fake_ifaces(monkeypatch, {"eth0": "192.168.1.5", "eth1": "10.0.0.9"})
    services._app._config.psn_source_iface = "eth0"
    services._app._config.otp_output.source_iface = "eth1"

    resolved = {p.label: p.resolve() for p in services._build_network_planes()}
    assert resolved["PSN"] == ("192.168.1.5", "iface", "eth0")
    assert resolved["OTP output"] == ("10.0.0.9", "iface", "eth1")

    services._app._config.otp_output.source_iface = ""
    resolved = {p.label: p.resolve() for p in services._build_network_planes()}
    assert resolved["OTP output"] == ("192.168.1.5", "station", "eth0")


def _rttrpm_plane(services):
    return next(p for p in services._build_network_planes() if p.label == "RTTrPM output")


def test_rttrpm_is_a_plane_only_while_an_interface_is_configured(monkeypatch) -> None:
    """Unpinned, the OS routes RTTrPM. Following it anyway would compare the
    auto-detected address with an unbound socket and restart it every poll."""
    services = _build_services_with_psutil_backend(monkeypatch)
    _fake_ifaces(monkeypatch, {"eth0": "192.168.1.5", "eth1": "10.0.0.9"})
    cfg = services._app._config
    cfg.rttrpm_output.enabled = True
    cfg.rttrpm_output.host = "203.0.113.50"
    plane = _rttrpm_plane(services)
    assert plane.enabled() is False

    cfg.psn_source_iface = "eth0"
    assert plane.enabled() is True
    assert plane.resolve() == ("192.168.1.5", "station", "eth0")

    cfg.psn_source_iface = ""
    cfg.rttrpm_output.source_iface = "eth1"
    assert plane.enabled() is True
    assert plane.resolve() == ("10.0.0.9", "iface", "eth1")

    cfg.rttrpm_output.enabled = False
    assert plane.enabled() is False


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_rttrpm_to_this_box_is_not_a_plane(monkeypatch, host) -> None:
    """Its socket is never pinned, so there is no interface to follow, and
    comparing the station address with an unbound socket restarts it every poll."""
    services = _build_services_with_psutil_backend(monkeypatch)
    _fake_ifaces(monkeypatch, {"eth0": "192.168.1.5", "eth1": "10.0.0.9"})
    cfg = services._app._config
    cfg.rttrpm_output.enabled = True
    cfg.rttrpm_output.host = host
    cfg.psn_source_iface = "eth0"
    cfg.rttrpm_output.source_iface = "eth1"
    assert _rttrpm_plane(services).enabled() is False


def test_the_rttrpm_plane_drives_the_running_server(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    services._app._rttrpm_server = None
    plane = _rttrpm_plane(services)
    assert plane.current() is None
    plane.suspend()

    class _Server:
        stops = 0

        def bound_source_ip(self) -> str:
            return "10.0.0.9"

        def stop(self) -> None:
            self.stops += 1

    server = _Server()
    services._app._rttrpm_server = server
    assert plane.current() == "10.0.0.9"
    plane.suspend()
    assert server.stops == 1

    applied: list[object] = []
    monkeypatch.setattr(services, "apply_rttrpm_output_change", applied.append)
    plane.apply("10.0.0.10")
    assert applied == [services._app._config.rttrpm_output]


def _osc_routing(services, *dests, rows=(), zones=(), zones_enabled=False) -> None:
    from openfollow.configuration import (
        OscDestinationsConfig,
        OscTransmitterConfig,
        OscTransmittersConfig,
        TriggerZoneConfig,
    )

    cfg = services._app._config
    cfg.osc_destinations = OscDestinationsConfig(destinations=list(dests))
    cfg.osc_transmitters = OscTransmittersConfig(
        transmitters=[
            OscTransmitterConfig(id=f"row-{i}", enabled=on, destination_id=d) for i, (d, on) in enumerate(rows)
        ]
    )
    cfg.trigger_zones.enabled = zones_enabled
    cfg.trigger_zones.zones = [TriggerZoneConfig(destination_id=d, enabled=True) for d in zones]


def _dest(dest_id: str, source_iface: str = "", host: str = "198.51.100.20"):
    from openfollow.configuration import OscDestinationConfig

    return OscDestinationConfig(id=dest_id, host=host, source_iface=source_iface)


def _osc_output_planes(services):
    return [p for p in services._build_network_planes() if p.label == "OSC output"]


def test_osc_output_has_one_plane_per_interface_in_use(monkeypatch) -> None:
    """Per interface, so two destinations on one NIC cannot disagree about its
    address and the HUD lists the outage once."""
    services = _build_services_with_psutil_backend(monkeypatch)
    _osc_routing(
        services,
        _dest("a", "eth1"),
        _dest("b", "eth1"),
        _dest("unused", "eth2"),
        _dest("local", "eth3", host="127.0.0.1"),
        rows=[("a", True), ("b", True), ("local", True)],
    )
    assert [p.key for p in _osc_output_planes(services)] == ["osc_out:eth1"]


def test_only_enabled_rows_and_armed_zones_put_an_interface_in_use(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    _osc_routing(services, _dest("a", "eth1"), _dest("z", "eth2"), rows=[("a", False)], zones=["z"])
    assert _osc_output_planes(services) == []

    services._app._config.trigger_zones.enabled = True
    assert [p.key for p in _osc_output_planes(services)] == ["osc_out:eth2"]


def test_a_blank_destination_pin_follows_the_station_plane(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    _osc_routing(services, _dest("a"), rows=[("a", True)])
    assert _osc_output_planes(services) == []

    services._app._config.psn_source_iface = "eth0"
    assert [p.key for p in _osc_output_planes(services)] == ["osc_out:eth0"]


def test_the_osc_output_plane_drives_the_egress_table(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    _fake_ifaces(monkeypatch, {"eth1": "10.0.0.9"})
    service = _RecordingOscService()
    services._osc_service = service
    dest = _dest("a", "eth1")
    _osc_routing(services, dest, rows=[("a", True)])
    services._restage_osc_egress(services._app._config.osc_destinations)
    (plane,) = _osc_output_planes(services)

    assert plane.resolve() == ("10.0.0.9", "iface", "eth1")
    assert plane.current() == "10.0.0.9"

    plane.suspend()
    assert plane.current() is None
    assert services._osc_egress.for_destination(dest).down is True

    plane.apply("10.0.0.10")
    assert services._osc_egress.for_destination(dest) == Egress("eth1", "10.0.0.10")
    assert service.evicted == ["eth1", "eth1"]


def test_a_station_change_restages_osc_destinations(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    _fake_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
    dest = _dest("a")
    _osc_routing(services, dest, rows=[("a", True)])
    services._restage_osc_egress(services._app._config.osc_destinations)
    assert services._osc_egress.for_destination(dest) is None

    services._app._otp_server = None
    services._app._rttrpm_server = None
    services._app._config.psn_source_iface = "eth0"
    services.apply_station_iface_change()
    assert services._osc_egress.for_destination(dest) == Egress("eth0", "192.168.1.5")


def test_a_routing_change_restages_the_egress_table(monkeypatch) -> None:
    from openfollow.configuration import OscDestinationsConfig

    services = _build_services_with_psutil_backend(monkeypatch)
    _fake_ifaces(monkeypatch, {"eth1": "10.0.0.9"})
    services._osc_transmitter_manager = SimpleNamespace(restart=lambda cfg, dests: None)
    moved = _dest("a", "eth1")
    services.apply_osc_transmitters_change(
        services._app._config.osc_transmitters, OscDestinationsConfig(destinations=[moved])
    )
    assert services._osc_egress.for_destination(moved) == Egress("eth1", "10.0.0.9")


def test_a_zone_test_send_to_a_down_interface_says_so(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    _fake_ifaces(monkeypatch, {})
    service = _RecordingOscService()
    service.sent = []
    service.send = lambda *a, **kw: service.sent.append(a)  # type: ignore[method-assign]
    services._osc_service = service
    _osc_routing(services, _dest("a", "eth1"), zones=["a"])
    services._app._config.trigger_zones.zones[0].osc_address_first_entry = "/go"
    services._restage_osc_egress(services._app._config.osc_destinations)

    assert services._zone_test_send(0, "first") == {"skipped": True, "reason": "interface eth1 is down"}
    assert service.sent == []


def test_a_down_plane_reports_down_not_another_interface(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    _fake_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
    services._app._config.psn_source_iface = "eth0"
    services._app._config.otp_output.source_iface = "eth_gone"

    resolved = {p.label: p.resolve() for p in services._build_network_planes()}
    assert resolved["OTP output"] == ("", "down", "eth_gone")


class _RecordingOscService:
    """Stands in for ``OscService`` on the observer's OSC plane.

    Models the membership the real service holds rather than only recording
    calls, so ``current`` can disagree with the pin the way the real pair can.
    """

    def __init__(self, *, port: int | None = 8765, group: str = "239.20.20.20") -> None:
        self.port = port
        self.group = group
        self.iface: str | None = ""
        # The kernel can refuse IP_ADD_MEMBERSHIP on an interface that resolves
        # perfectly well, so "which interface" and "is it subscribed" are
        # independent - a double that derives one from the other cannot tell a
        # failed join from a healthy one.
        self.join_ok = True
        self.calls: list[str | None] = []
        self.retained: list[object] = []
        self.evicted: list[str] = []

    def retain_egress(self, live: object) -> None:
        self.retained.append(live)

    def evict_egress(self, iface: str) -> None:
        self.evicted.append(iface)

    def listener_status(self) -> dict[str, object]:
        joined = self.port is not None and bool(self.group) and self.iface is not None and self.join_ok
        return {
            "port": self.port,
            "multicast_group": self.group,
            "multicast_iface": self.iface,
            "multicast_joined": joined,
            "allowed_sender_ips": [],
        }

    def set_multicast_iface(self, iface: str | None) -> bool:
        self.calls.append(iface)
        self.iface = iface
        return iface is not None and self.join_ok


def _osc_plane(services, service: _RecordingOscService):
    services._osc_service = service
    return next(p for p in services._build_network_planes() if p.label == "OSC input")


def test_osc_input_plane_follows_its_own_and_the_station_interface(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    _fake_ifaces(monkeypatch, {"eth0": "192.168.1.5", "eth1": "10.0.0.9"})
    services._app._config.psn_source_iface = "eth0"
    services._app._config.osc.listen_iface = "eth1"

    resolved = {p.label: p.resolve() for p in services._build_network_planes()}
    assert resolved["OSC input"] == ("10.0.0.9", "iface", "eth1")

    services._app._config.osc.listen_iface = ""
    resolved = {p.label: p.resolve() for p in services._build_network_planes()}
    assert resolved["OSC input"] == ("192.168.1.5", "station", "eth0")


@pytest.mark.parametrize(
    ("label", "setup"),
    [
        ("nothing pinned", lambda cfg: None),
        (
            "no multicast group",
            lambda cfg: (setattr(cfg, "psn_source_iface", "eth0"), setattr(cfg.osc, "multicast_group", "")),
        ),
        ("osc disabled", lambda cfg: (setattr(cfg, "psn_source_iface", "eth0"), setattr(cfg.osc, "enabled", False))),
    ],
)
def test_osc_input_is_only_a_plane_when_a_pin_governs_something(monkeypatch, label, setup) -> None:
    """Three ways there is nothing to follow. Unpinned the membership is the
    routing table's to choose; with no group the pin governs nothing; switched
    off it is not broken. Any of them alerting would put a fault on the HUD for
    a station behaving exactly as configured."""
    services = _build_services_with_psutil_backend(monkeypatch)
    _fake_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
    setup(services._app._config)
    assert _osc_plane(services, _RecordingOscService()).enabled() is False


def test_osc_input_is_not_a_plane_while_the_listener_is_down(monkeypatch) -> None:
    """A listener that never bound (port in use) has no socket to move a
    membership on. Following it would call apply once a second forever, with no
    backoff, because the failure never surfaces to the observer."""
    services = _build_services_with_psutil_backend(monkeypatch)
    _fake_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
    services._app._config.psn_source_iface = "eth0"
    assert _osc_plane(services, _RecordingOscService(port=None)).enabled() is False


def test_osc_input_plane_reports_the_live_membership_not_the_pin(monkeypatch) -> None:
    """``current`` drives the observer's "already correct" short-circuit. Read
    from config it would report an interface the socket never subscribed on."""
    services = _build_services_with_psutil_backend(monkeypatch)
    service = _RecordingOscService()
    plane = _osc_plane(services, service)

    service.iface = "10.0.0.9"
    assert plane.current() == "10.0.0.9"

    # Pinned but down: no membership is held, which must not read as one.
    service.iface = None
    assert plane.current() is None

    # A refused join on an interface that resolves: the address is recorded but
    # nothing is subscribed, so this must read as "not bound" and let the
    # observer re-apply. Reporting the interface here would short-circuit it.
    service.iface = "10.0.0.9"
    service.join_ok = False
    assert plane.current() is None


def test_osc_input_plane_resubscribes_and_unsubscribes(monkeypatch) -> None:
    """Recovery is why this plane exists: suspend holds no membership, and
    apply takes it on the returned interface. The service moves it by rebinding
    the listener, which the subscriptions survive."""
    services = _build_services_with_psutil_backend(monkeypatch)
    _fake_ifaces(monkeypatch, {"eth1": "10.0.0.9"})
    services._app._config.osc.listen_iface = "eth1"
    service = _RecordingOscService()
    plane = _osc_plane(services, service)

    plane.suspend()
    assert service.calls[-1] is None
    assert service.listener_status()["multicast_joined"] is False

    plane.apply("10.0.0.9")
    assert service.calls[-1] == "10.0.0.9"
    assert service.listener_status()["multicast_joined"] is True


def test_osc_input_plane_raises_when_the_membership_is_refused(monkeypatch) -> None:
    """A refused join must reach the observer as a failure.

    ``apply`` returning normally is how the observer is told the plane is well:
    it clears the outage, logs that the output resumed, and polls on with no
    backoff. Swallowing the refusal would retry once a second for the length of
    the show while every surface reported health.
    """
    services = _build_services_with_psutil_backend(monkeypatch)
    _fake_ifaces(monkeypatch, {"eth1": "10.0.0.9"})
    services._app._config.osc.listen_iface = "eth1"
    service = _RecordingOscService()
    service.join_ok = False
    plane = _osc_plane(services, service)

    with pytest.raises(OSError):
        plane.apply("10.0.0.9")


def test_osc_input_plane_does_not_raise_on_a_successful_move(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    _fake_ifaces(monkeypatch, {"eth1": "10.0.0.9"})
    services._app._config.osc.listen_iface = "eth1"
    plane = _osc_plane(services, _RecordingOscService())

    plane.apply("10.0.0.9")


def test_clearing_the_station_pin_repoints_an_inheriting_membership(monkeypatch) -> None:
    """The observer cannot cover this: clearing Station default leaves the
    plane unpinned, so it stops being followed while the socket still holds a
    membership on the interface that was just given up."""
    services = _build_services_with_psutil_backend(monkeypatch)
    _fake_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
    services._app._config.psn_source_iface = "eth0"
    service = _RecordingOscService()
    services._osc_service = service
    service.iface = "192.168.1.5"
    services._app._otp_server = None

    services._app._config.psn_source_iface = ""
    services.apply_station_iface_change()

    assert service.calls[-1] == ""


@pytest.mark.parametrize(
    ("label", "setup"),
    [
        ("own pin", lambda cfg: setattr(cfg.osc, "listen_iface", "eth1")),
        ("no group", lambda cfg: setattr(cfg.osc, "multicast_group", "")),
        ("osc disabled", lambda cfg: setattr(cfg.osc, "enabled", False)),
    ],
)
def test_the_station_pin_does_not_repoint_a_membership_it_does_not_own(monkeypatch, label, setup) -> None:
    """Only an inheriting membership follows the Station default row. Its own
    pin outranks the station, a station with no group has no membership, and a
    disabled receiver has no socket - moving any of them on a station edit
    would override a choice the operator made elsewhere."""
    services = _build_services_with_psutil_backend(monkeypatch)
    _fake_ifaces(monkeypatch, {"eth0": "192.168.1.5", "eth1": "10.0.0.9"})
    services._app._config.psn_source_iface = "eth0"
    setup(services._app._config)
    service = _RecordingOscService()
    services._osc_service = service
    services._app._otp_server = None

    services.apply_station_iface_change()

    assert service.calls == []


def test_suspending_psn_stops_both_directions(monkeypatch) -> None:
    """Leaving the receiver joined on a dead address would keep viewer markers
    showing stale positions."""
    services = _build_services_with_psutil_backend(monkeypatch)

    class _Stoppable:
        def __init__(self) -> None:
            self.stopped = 0

        def stop(self) -> None:
            self.stopped += 1

    server, receiver = _Stoppable(), _Stoppable()
    services._app._server = server
    services._app._psn_receiver = receiver
    psn = next(p for p in services._build_network_planes() if p.label == "PSN")
    psn.suspend()
    assert (server.stopped, receiver.stopped) == (1, 1)


def test_station_followers_are_repointed_without_a_web_request(monkeypatch) -> None:
    """These used to heal only from a request path, so a station whose address
    changed stayed stale unless somebody had a browser tab open."""
    services = _build_services_with_psutil_backend(monkeypatch)
    _fake_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
    services._app._config.psn_source_iface = "eth0"

    class _Server:
        def __init__(self) -> None:
            self.refreshes = 0

        def refresh_local_ip(self) -> bool:
            self.refreshes += 1
            return False

        def suspend_beacons(self) -> None:
            raise AssertionError("a healthy interface must not suspend the beacons")

    class _Sync:
        def __init__(self) -> None:
            self.ips: list[str] = []

        def update_iface_ip(self, ip: str, *, force: bool = False) -> None:
            self.ips.append(ip)

    server, sync = _Server(), _Sync()
    services._app._web_server = server
    services._app._marker_catalog_sync = sync
    services._follow_station_ip()
    assert server.refreshes == 1
    assert sync.ips == ["192.168.1.5"]


def test_a_dark_station_interface_suspends_the_beacons(monkeypatch) -> None:
    """The one plane with no ``Plane`` entry of its own still has to stop.

    Discovery is the last thing left announcing the station's name, version and
    web port, so leaving it running while the observer stops PSN would put
    exactly the information a peer acts on onto an unchosen network.
    """
    services = _build_services_with_psutil_backend(monkeypatch)
    _fake_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
    services._app._config.psn_source_iface = "of-nodev0"  # configured, absent

    class _Server:
        def __init__(self) -> None:
            self.suspends = 0
            self.refreshes = 0

        def suspend_beacons(self) -> None:
            self.suspends += 1

        def refresh_local_ip(self) -> None:
            self.refreshes += 1

    server = _Server()
    services._app._web_server = server
    services._app._marker_catalog_sync = None

    # A blip shorter than the debounce must not drop the station out of every
    # peer's list: Apply and Renew DHCP lease each produce one.
    _drive_down_polls(services, 1)
    assert server.suspends == 0, "a single missing sample suspended discovery"

    _drive_down_polls(services)
    assert server.suspends == 1
    # Once, on the transition: the beacons are already silent, and re-suspending
    # every second for the length of the outage only refills the journal.
    _drive_down_polls(services)
    assert server.suspends == 1, "discovery was re-suspended while already quiet"
    assert server.refreshes == 0, "a dark interface must not repoint to anything"


def test_nothing_configured_does_not_suspend_the_beacons(monkeypatch) -> None:
    """ "Nothing configured and nothing auto-detected" is not a dark pin.

    Suspending there would take a station with no interface settings at all
    off the network, which is the default configuration.
    """
    services = _build_services_with_psutil_backend(monkeypatch)
    _fake_ifaces(monkeypatch, {})
    # An empty adapter list is not enough: with no pin the resolver falls
    # through to the primary-address probe, which reaches the real host and
    # reports "primary". Neutralising it is what actually produces "none".
    import openfollow.net_utils as net_utils_module

    monkeypatch.setattr(net_utils_module, "get_primary_local_ipv4", lambda default="N/A": default)
    services._app._config.psn_source_iface = ""
    assert net_utils_module.resolve_plane_source_ip("", "")[1] == "none"

    class _Server:
        def __init__(self) -> None:
            self.suspends = 0

        def suspend_beacons(self) -> None:
            self.suspends += 1

        def refresh_local_ip(self) -> None:
            pass

    server = _Server()
    services._app._web_server = server
    services._app._marker_catalog_sync = None
    services._follow_station_ip()

    assert server.suspends == 0


def test_sync_recovers_from_a_station_booted_with_a_dark_interface(monkeypatch) -> None:
    """The gap a bench run found: the beacon self-healed and sync did not.

    Sync used not to be constructed at all when the station booted dark, and
    the recovery path can only repoint an object that exists - so marker names
    stayed unsynced until somebody restarted the station, long after the cable
    was back in.
    """
    services = _build_services_with_psutil_backend(monkeypatch)
    services._app._config.psn_source_iface = "eth0"
    # Booted dark, so both followers start pointed nowhere - not at an address
    # they never had.
    sync, server = _FollowerSync(None), _FollowerServer(None)
    services._app._marker_catalog_sync = sync
    services._app._web_server = server

    _fake_ifaces(monkeypatch, {})
    _drive_down_polls(services)
    assert sync.ips == [None], "a dark interface must put sync into its silent state"
    assert sync.rebuilds == 0, "a sync that never pointed anywhere had nothing to tear down"

    # The cable goes back in. No restart, no config change.
    _fake_ifaces(monkeypatch, {"eth0": _FOLLOWER_ADDRESS})
    services._follow_station_ip()

    assert sync.ips == [None, _FOLLOWER_ADDRESS], "sync never came back after the interface returned"
    assert sync.rebuilds == 1, "the membership the kernel dropped was not rebuilt exactly once"


def test_a_station_with_no_web_server_still_silences_sync(monkeypatch) -> None:
    """The two followers stop independently.

    Each puts this station's identity on its own socket, so whichever one
    exists has to go quiet on the down edge regardless of the other. Guarding
    them together would leave marker names on an excluded network on any
    station whose web server had not been built.
    """
    services = _build_services_with_psutil_backend(monkeypatch)
    services._app._config.psn_source_iface = "eth0"
    sync, _server = _wire_followers(services)
    services._app._web_server = None

    _fake_ifaces(monkeypatch, {})
    _drive_down_polls(services)

    assert sync.ips == [None], "sync kept sending because there was no web server to suspend alongside it"


def test_follow_station_ip_tolerates_missing_services(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    services._app._web_server = None
    services._app._marker_catalog_sync = None
    services._follow_station_ip()


def test_alerts_are_empty_before_the_first_poll(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    assert services.network_alerts() == []


def test_applying_psn_routes_through_the_rebind_orchestrator(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    applied: list[str] = []
    services.apply_psn_source_ip_change = applied.append  # type: ignore[method-assign]
    psn = next(p for p in services._build_network_planes() if p.label == "PSN")
    psn.apply("10.0.0.9")
    assert applied == ["10.0.0.9"]


def test_suspending_psn_tolerates_a_service_that_never_started(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    services._app._server = None
    services._app._psn_receiver = None
    next(p for p in services._build_network_planes() if p.label == "PSN").suspend()


def test_applying_otp_re_resolves_through_its_orchestrator(monkeypatch) -> None:
    """The orchestrator resolves the pin itself, so it binds the address this
    poll observed rather than one the observer passed along."""
    services = _build_services_with_psutil_backend(monkeypatch)
    applied: list[object] = []
    services.apply_otp_output_change = applied.append  # type: ignore[method-assign]
    otp = next(p for p in services._build_network_planes() if p.label == "OTP output")
    otp.apply("10.0.0.9")
    assert applied == [services._app._config.otp_output]


def test_suspending_otp_stops_the_server(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)

    class _Server:
        def __init__(self) -> None:
            self.stopped = 0

        def stop(self) -> None:
            self.stopped += 1

    server = _Server()
    services._app._otp_server = server
    otp = next(p for p in services._build_network_planes() if p.label == "OTP output")
    otp.suspend()
    assert server.stopped == 1
    services._app._otp_server = None
    otp.suspend()  # no server to stop


def test_observe_builds_the_observer_once(monkeypatch) -> None:
    """Housekeeping calls this ~10x/s; rebuilding the plane list each time
    would re-enumerate interfaces on every tick."""
    services = _build_services_with_psutil_backend(monkeypatch)
    _fake_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
    services._app._config.psn_source_iface = "eth0"
    services.apply_psn_source_ip_change = lambda _ip: None  # type: ignore[method-assign]
    services._follow_station_ip = lambda: None  # type: ignore[method-assign]

    services.observe_network_planes()
    first = services._network_observer
    services.observe_network_planes()
    assert services._network_observer is first


def test_station_followers_share_the_observer_throttle(monkeypatch) -> None:
    """Resolving the station address enumerates every adapter, and
    housekeeping runs at 100ms - paying that ten times a second on the render
    thread is exactly what the observer's own throttle exists to avoid."""
    services = _build_services_with_psutil_backend(monkeypatch)
    _fake_ifaces(monkeypatch, {"eth0": "192.168.1.5"})
    services._app._config.psn_source_iface = "eth0"
    services.apply_psn_source_ip_change = lambda _ip: None  # type: ignore[method-assign]

    followed: list[int] = []
    services._follow_station_ip = lambda: followed.append(1)  # type: ignore[method-assign]

    services.observe_network_planes()
    services.observe_network_planes()  # same instant - throttled
    assert len(followed) == 1


def test_station_followers_do_not_move_to_another_interface(monkeypatch) -> None:
    """The station interface being down must not put this station's identity -
    its name, marker names and colours - on whatever else happens to be up, at
    the exact moment the observer is stopping PSN for that same reason."""
    services = _build_services_with_psutil_backend(monkeypatch)
    _fake_ifaces(monkeypatch, {"eth1": "10.0.0.9"})
    services._app._config.psn_source_iface = "eth0_gone"

    class _Sync:
        def __init__(self) -> None:
            self.ips: list[str] = []

        def update_iface_ip(self, ip: str) -> None:
            self.ips.append(ip)

    class _Server:
        def __init__(self) -> None:
            self.refreshes = 0
            self.suspends = 0

        def refresh_local_ip(self) -> None:
            self.refreshes += 1

        def suspend_beacons(self) -> None:
            self.suspends += 1

    sync, server = _Sync(), _Server()
    services._app._marker_catalog_sync = sync
    services._app._web_server = server
    _drive_down_polls(services)
    assert server.refreshes == 0
    # Not merely "not repointed": both followers are told to stop, so each
    # goes quiet by decision instead of waiting for a send on a dead address
    # to fail. None is the sync's own "stay silent" state.
    assert sync.ips == [None]
    assert server.suspends == 1


def test_suspending_psn_stops_the_receiver_even_if_the_server_raises(monkeypatch) -> None:
    """Aborting on the first failure left the receiver joined on the dead
    address - the exact outcome the suspend exists to prevent - and swallowed
    the HUD alert with it."""
    services = _build_services_with_psutil_backend(monkeypatch)

    class _Boom:
        def stop(self) -> None:
            raise OSError("stop failed")

    class _Stoppable:
        def __init__(self) -> None:
            self.stopped = 0

        def stop(self) -> None:
            self.stopped += 1

    receiver = _Stoppable()
    services._app._server = _Boom()
    services._app._psn_receiver = receiver
    psn = next(p for p in services._build_network_planes() if p.label == "PSN")
    with pytest.raises(OSError, match="stop failed"):
        psn.suspend()
    assert receiver.stopped == 1


def test_a_disabled_otp_output_is_not_a_plane_to_alert_on(monkeypatch) -> None:
    """The shipped default has OTP off; a false 'is down' row would put a
    second fault on the HUD for a protocol nobody enabled."""
    services = _build_services_with_psutil_backend(monkeypatch)
    otp = next(p for p in services._build_network_planes() if p.label == "OTP output")
    services._app._config.otp_output.enabled = False
    assert otp.enabled() is False
    services._app._config.otp_output.enabled = True
    assert otp.enabled() is True


def test_plane_current_reports_the_live_binding(monkeypatch) -> None:
    """Drives the 'already bound correctly, leave it alone' decision, which is
    what keeps the first poll from tearing down a healthy startup binding."""
    services = _build_services_with_psutil_backend(monkeypatch)

    class _Server:
        def bound_source_ip(self) -> str | None:
            return "192.168.1.5"

    services._app._server = _Server()
    services._app._otp_server = None
    planes = {p.label: p for p in services._build_network_planes()}
    assert planes["PSN"].current() == "192.168.1.5"
    assert planes["OTP output"].current() is None


# The address every follower test's interface resolves to.
_FOLLOWER_ADDRESS = "192.168.1.5"


class _FollowerSync:
    """Models the real short-circuit, not just the call.

    A double that only recorded the address could not tell one rebuild from
    two, which is exactly the defect this shape exists to catch: a forced
    reopen stacked on a repoint tears down sockets the worker may have just
    opened. ``rebuilds`` counts what the real object would actually do.
    """

    def __init__(self, iface_ip: str | None = None) -> None:
        self.ips: list[str | None] = []
        self.reopens = 0
        self.rebuilds = 0
        self._iface_ip = iface_ip

    def update_iface_ip(self, ip: str | None, *, force: bool = False) -> None:
        self.ips.append(ip)
        if ip == self._iface_ip and not force:
            return
        self._iface_ip = ip
        self.reopen()

    def reopen(self) -> None:
        self.reopens += 1
        self.rebuilds += 1


class _FollowerServer:
    """Same contract as the real server: the refresh reports whether it
    repointed, so a caller can tell the rebuild has already happened."""

    def __init__(self, local_ip: str | None = None) -> None:
        self.refreshes = 0
        self.reopens = 0
        self.suspends = 0
        self.rebuilds = 0
        self._local_ip = local_ip

    def refresh_local_ip(self) -> bool:
        self.refreshes += 1
        if self._local_ip == _FOLLOWER_ADDRESS:
            return False
        self._local_ip = _FOLLOWER_ADDRESS
        self.rebuilds += 1
        return True

    def reopen_beacons(self) -> None:
        self.reopens += 1
        self.rebuilds += 1

    def suspend_beacons(self) -> None:
        self.suspends += 1
        self.rebuilds += 1
        self._local_ip = None


def _drive_down_polls(services, count: int | None = None) -> None:
    """Poll a down interface until the suspend debounce clears.

    The followers debounce on the same count as the observer's own planes, so a
    test that wants the suspended state has to earn it rather than assume the
    first poll does it.
    """
    from openfollow.runtime.network_observer import DOWN_POLLS_BEFORE_SUSPEND

    for _ in range(DOWN_POLLS_BEFORE_SUSPEND if count is None else count):
        services._follow_station_ip()


def _wire_followers(services):
    """Wired the way production starts: both followers already pointed at the
    station address, so a steady poll is a genuine no-op rather than an
    artefact of the double booting blank."""
    sync = _FollowerSync(_FOLLOWER_ADDRESS)
    server = _FollowerServer(_FOLLOWER_ADDRESS)
    services._app._marker_catalog_sync = sync
    services._app._web_server = server
    return sync, server


def test_station_followers_rebuild_after_a_same_lease_flap(monkeypatch) -> None:
    """The observer forces its own planes to rebuild after an outage even at an
    unchanged address; the followers short-circuit on an unchanged IP, so a
    replug returning the same lease left catalog sync and the beacon joined to
    memberships the kernel had already dropped - converging with nobody while
    looking healthy."""
    services = _build_services_with_psutil_backend(monkeypatch)
    services._app._config.psn_source_iface = "eth0"
    sync, server = _wire_followers(services)

    _fake_ifaces(monkeypatch, {"eth0": _FOLLOWER_ADDRESS})
    services._follow_station_ip()
    assert (sync.rebuilds, server.rebuilds) == (0, 0)

    _fake_ifaces(monkeypatch, {})  # cable out
    _drive_down_polls(services)

    before = (sync.rebuilds, server.rebuilds)
    _fake_ifaces(monkeypatch, {"eth0": _FOLLOWER_ADDRESS})  # same lease back
    services._follow_station_ip()

    # Exactly one, not zero and not two: zero leaves both joined to
    # memberships the kernel dropped, and two tears down sockets the worker
    # may have just opened.
    assert sync.rebuilds - before[0] == 1
    assert server.rebuilds - before[1] == 1


def test_a_blip_shorter_than_the_debounce_still_forces_a_rebuild(monkeypatch) -> None:
    """The case the forced rebuild actually exists for.

    An outage too short to trip the suspend debounce never puts the followers
    into their silent state, so the address they hold is still the one that
    comes back - and their own short-circuit would skip the rebuild. The
    kernel dropped the membership and the egress route regardless of how long
    the cable was out, so the flag has to override that guard.
    """
    services = _build_services_with_psutil_backend(monkeypatch)
    services._app._config.psn_source_iface = "eth0"
    sync, server = _wire_followers(services)

    _fake_ifaces(monkeypatch, {"eth0": _FOLLOWER_ADDRESS})
    services._follow_station_ip()
    assert (sync.rebuilds, server.rebuilds) == (0, 0)

    # One missed sample - below DOWN_POLLS_BEFORE_SUSPEND, so nothing suspends.
    _fake_ifaces(monkeypatch, {})
    services._follow_station_ip()
    assert server.suspends == 0, "the blip should not have reached the suspend"
    assert None not in sync.ips, "the blip should not have silenced sync"

    _fake_ifaces(monkeypatch, {"eth0": _FOLLOWER_ADDRESS})
    services._follow_station_ip()

    assert sync.rebuilds == 1, "the unchanged address skipped the rebuild the kernel needs"
    assert server.rebuilds == 1


def test_a_steady_station_never_forces_a_rebuild(monkeypatch) -> None:
    """Rebuilding sockets once a second would be worse than the bug."""
    services = _build_services_with_psutil_backend(monkeypatch)
    services._app._config.psn_source_iface = "eth0"
    sync, server = _wire_followers(services)
    _fake_ifaces(monkeypatch, {"eth0": _FOLLOWER_ADDRESS})
    for _ in range(5):
        services._follow_station_ip()
    assert (sync.rebuilds, server.rebuilds) == (0, 0)


def test_the_outage_flag_clears_after_one_recovery(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    services._app._config.psn_source_iface = "eth0"
    sync, server = _wire_followers(services)
    _fake_ifaces(monkeypatch, {})
    _drive_down_polls(services)
    before = (sync.rebuilds, server.rebuilds)
    _fake_ifaces(monkeypatch, {"eth0": _FOLLOWER_ADDRESS})
    services._follow_station_ip()
    services._follow_station_ip()
    # The second healthy poll must not force a further rebuild.
    assert sync.rebuilds - before[0] == 1
    assert server.rebuilds - before[1] == 1


# VLAN providers / handlers
# --------------------------------------------------------------------------- #


def test_vlan_provider_reports_unsupported_without_adapter(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    services._network_adapter = None
    assert services._network_vlan_provider() == {"supported": False, "vlans": []}


def test_vlan_provider_reports_unsupported_on_a_read_only_backend(monkeypatch) -> None:
    """The psutil backend reads interfaces; it cannot create links."""
    services = _build_services_with_psutil_backend(monkeypatch)
    assert services._network_vlan_provider() == {"supported": False, "vlans": []}


def test_vlan_provider_lists_the_backend_vlans(monkeypatch) -> None:
    from openfollow.network.adapter import VlanInterface

    services = _build_services_with_psutil_backend(monkeypatch)
    fake = _FakeWritableAdapter([_iface()], None)
    fake.vlans = [VlanInterface(name="eth0.10", parent="eth0", vlan_id=10)]
    services._network_adapter = fake
    assert services._network_vlan_provider() == {
        "supported": True,
        "vlans": [{"name": "eth0.10", "parent": "eth0", "vlan_id": 10}],
    }


def test_vlan_provider_survives_a_backend_failure(monkeypatch) -> None:
    """Still reports supported – the backend does own links, this read just
    failed – so the card keeps the controls rather than pretending the
    station cannot do VLANs at all."""
    services = _build_services_with_psutil_backend(monkeypatch)
    fake = _FakeWritableAdapter([_iface()], None)
    fake.vlan_list_raises = True
    services._network_adapter = fake
    assert services._network_vlan_provider() == {"supported": True, "vlans": []}


def test_vlan_create_calls_the_adapter(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    fake = _FakeWritableAdapter([_iface()], None)
    services._network_adapter = fake
    result = services._handle_network_vlan_create("eth0", 10)
    assert result.ok is True
    assert fake.vlans_created == [("eth0", 10)]


def test_vlan_create_refused_on_a_read_only_host(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    result = services._handle_network_vlan_create("eth0", 10)
    assert result.ok is False and "Read-only" in result.message


def test_vlan_create_refused_without_adapter(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    services._network_adapter = None
    result = services._handle_network_vlan_create("eth0", 10)
    assert result.ok is False and "No network adapter" in result.message


def test_vlan_delete_calls_the_adapter(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    fake = _FakeWritableAdapter([_iface()], None)
    services._network_adapter = fake
    result = services._handle_network_vlan_delete("eth0.10")
    assert result.ok is True
    assert fake.vlans_deleted == ["eth0.10"]


def test_vlan_delete_refused_on_a_read_only_host(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    result = services._handle_network_vlan_delete("eth0.10")
    assert result.ok is False and "Read-only" in result.message


def test_vlan_delete_refused_without_adapter(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    services._network_adapter = None
    result = services._handle_network_vlan_delete("eth0.10")
    assert result.ok is False and "No network adapter" in result.message


class _RecordingVideoReceiver:
    def __init__(self, pinned_to: str | None = None) -> None:
        self.pinned_to = pinned_to
        self.released: list[str] = []

    def release_for_pin(self, detail: str) -> None:
        self.released.append(detail)
        self.pinned_to = None


def _video_plane(services, receiver):
    services._app._video_receiver = receiver
    return next(p for p in services._build_network_planes() if p.label == "Video input")


def test_video_input_plane_never_follows_the_station(monkeypatch) -> None:
    """Cameras usually sit on another network than PSN, so blank means the
    routing table, not the station's interface."""
    services = _build_services_with_psutil_backend(monkeypatch)
    _fake_ifaces(monkeypatch, {"eth0": "192.168.1.5", "eth1": "10.0.0.9"})
    cfg = services._app._config
    cfg.psn_source_iface = "eth0"
    cfg.video_input_iface = "eth1"
    plane = _video_plane(services, _RecordingVideoReceiver())

    assert plane.resolve() == ("10.0.0.9", "iface", "eth1")
    cfg.video_input_iface = ""
    address, _status, iface = plane.resolve()
    assert (address, iface) != ("192.168.1.5", "eth0")
    assert iface == ""


@pytest.mark.parametrize(
    ("source", "pin", "has_receiver", "expected"),
    [
        ("srt", "eth1", True, True),
        ("rtsp", "eth1", True, True),
        ("rtp", "eth1", True, True),
        ("srt", "", True, False),
        ("testpattern", "eth1", True, False),
        ("ndi", "eth1", True, False),
        ("srt", "eth1", False, False),
    ],
    ids=["srt", "rtsp", "rtp", "blank", "media-gallery", "ndi", "no-receiver"],
)
def test_video_input_is_a_plane_only_for_a_pinned_network_input(
    monkeypatch, source: str, pin: str, has_receiver: bool, expected: bool
) -> None:
    """An input that cannot honour the pin is not broken when its interface
    goes away, so it must not alert."""
    services = _build_services_with_psutil_backend(monkeypatch)
    cfg = services._app._config
    cfg.video_source_type = source
    cfg.video_input_iface = pin
    plane = _video_plane(services, _RecordingVideoReceiver() if has_receiver else None)
    assert plane.enabled() is expected


def test_video_input_plane_reports_what_the_input_was_built_for(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    receiver = _RecordingVideoReceiver(pinned_to="10.0.0.9")
    plane = _video_plane(services, receiver)
    assert plane.current() == "10.0.0.9"

    receiver.pinned_to = None
    assert plane.current() is None
    services._app._video_receiver = None
    assert plane.current() is None


def test_video_input_plane_stops_and_rebuilds_the_input(monkeypatch) -> None:
    services = _build_services_with_psutil_backend(monkeypatch)
    services._app._config.video_input_iface = "eth1"
    receiver = _RecordingVideoReceiver(pinned_to="10.0.0.9")
    plane = _video_plane(services, receiver)
    swapped: list = []
    monkeypatch.setattr(services, "swap_video", swapped.append)

    plane.suspend()
    assert receiver.released == ["eth1 has no address"]

    plane.apply("10.0.0.9")
    assert swapped == [services._app._config]

    services._app._video_receiver = None
    plane.suspend()  # nothing to stop
    assert receiver.released == ["eth1 has no address"]
