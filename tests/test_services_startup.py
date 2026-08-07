# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Integration tests for ``AppRuntimeServices`` construction and startup wiring:
GStreamer-availability guard, canvas/fullscreen setup, network-adapter selection
and state providers, privilege-broker wiring, and the network apply/renew handlers.
"""

from __future__ import annotations

import pytest

import openfollow.services as services_module
from openfollow.configuration import AppConfig

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
