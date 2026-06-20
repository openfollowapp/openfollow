# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Platform + poller tests for ``openfollow.input.keyboard``.

The existing ``test_input_keyboard.py`` covers the happy path with a fake
poller. This file fills in the platform-specific detection paths and
the poller abstraction:

- ``FallbackKeyboardPoller`` event → state API.
- ``create_keyboard_poller`` factory: Darwin → Mac native when available,
  fallback otherwise; non-Darwin → fallback.
- ``KeyboardHandler.is_connected`` caching against a clock.
- ``KeyboardHandler.on_key_down/up``: routes to the fallback poller,
  noop for the native poller.
- ``_probe_linux_keyboard_connected`` variants: procfs with kbd handler,
  procfs without kbd, procfs OSError → glob fallback.
- ``_discrete_polled_keys`` pulls config-bound action keys into the set.
"""

from __future__ import annotations

import builtins

import pytest

import openfollow.input.keyboard as keyboard_module
from openfollow.configuration import AppConfig
from openfollow.input.keyboard import (
    FallbackKeyboardPoller,
    KeyboardHandler,
    MacOSKeyboardPoller,
    create_keyboard_poller,
)

pytestmark = pytest.mark.unit


class _App:
    def __init__(self) -> None:
        self._config = AppConfig()


# ---------------------------------------------------------------------------
# FallbackKeyboardPoller
# ---------------------------------------------------------------------------


def test_fallback_poller_tracks_press_and_release() -> None:
    p = FallbackKeyboardPoller()
    assert p.is_available() is True
    assert p.is_key_pressed("w") is False

    p.on_key_down("w")
    assert p.is_key_pressed("w") is True

    p.on_key_up("w")
    assert p.is_key_pressed("w") is False


def test_fallback_poller_get_pressed_keys_filters_to_requested() -> None:
    p = FallbackKeyboardPoller()
    p.on_key_down("a")
    p.on_key_down("b")
    p.on_key_down("z")
    pressed = p.get_pressed_keys(["a", "z", "Tab"])
    assert pressed == {"a", "z"}


def test_fallback_poller_clear_drops_all() -> None:
    p = FallbackKeyboardPoller()
    p.on_key_down("a")
    p.on_key_down("b")
    p.clear()
    assert p.is_key_pressed("a") is False
    assert p.is_key_pressed("b") is False


def test_fallback_poller_key_up_for_unpressed_is_silent() -> None:
    p = FallbackKeyboardPoller()
    p.on_key_up("never-pressed")  # Must not raise.


# ---------------------------------------------------------------------------
# create_keyboard_poller factory
# ---------------------------------------------------------------------------


def test_factory_returns_mac_poller_on_darwin_when_available(monkeypatch) -> None:
    monkeypatch.setattr(keyboard_module.platform, "system", lambda: "Darwin")

    class _AvailableMac:
        def is_available(self) -> bool:
            return True

    monkeypatch.setattr(keyboard_module, "MacOSKeyboardPoller", lambda: _AvailableMac())
    poller = create_keyboard_poller()
    assert isinstance(poller, _AvailableMac)


def test_factory_falls_back_on_darwin_when_mac_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(keyboard_module.platform, "system", lambda: "Darwin")

    class _UnavailableMac:
        def is_available(self) -> bool:
            return False

    monkeypatch.setattr(keyboard_module, "MacOSKeyboardPoller", lambda: _UnavailableMac())
    poller = create_keyboard_poller()
    assert isinstance(poller, FallbackKeyboardPoller)


def test_factory_uses_fallback_on_non_darwin(monkeypatch) -> None:
    monkeypatch.setattr(keyboard_module.platform, "system", lambda: "Linux")
    poller = create_keyboard_poller()
    assert isinstance(poller, FallbackKeyboardPoller)


# ---------------------------------------------------------------------------
# MacOSKeyboardPoller – framework load failure
# ---------------------------------------------------------------------------


def test_macos_poller_marks_unavailable_when_quartz_missing(monkeypatch) -> None:

    class _BrokenCDLL:
        def LoadLibrary(self, name):  # noqa: ANN001, ANN201
            raise OSError(f"no such library: {name}")

    monkeypatch.setattr(keyboard_module.ctypes, "cdll", _BrokenCDLL())
    poller = MacOSKeyboardPoller()
    assert poller.is_available() is False
    # Polling an unavailable poller is silent.
    assert poller.is_key_pressed("w") is False


# ---------------------------------------------------------------------------
# KeyboardHandler – connected probe cache + fallback event routing
# ---------------------------------------------------------------------------


def test_keyboard_handler_on_key_down_routes_to_fallback_poller(monkeypatch) -> None:
    monkeypatch.setattr(keyboard_module.platform, "system", lambda: "Linux")
    handler = KeyboardHandler(_App())
    # The handler's poller is FallbackKeyboardPoller under Linux.
    assert isinstance(handler._poller, FallbackKeyboardPoller)

    handler.on_key_down({"key": "W"})  # single-char is lowercased
    assert "w" in handler.keys

    handler.on_key_up({"key": "W"})
    assert "w" not in handler.keys


def test_keyboard_handler_on_key_down_ignores_unknown_keys(monkeypatch) -> None:
    monkeypatch.setattr(keyboard_module.platform, "system", lambda: "Linux")
    handler = KeyboardHandler(_App())
    handler.on_key_down({"key": "F13"})  # not in POLLED_KEYS
    assert "F13" not in handler.keys


def test_keyboard_handler_on_key_down_noop_for_native_poller(monkeypatch) -> None:

    class _Native:
        def is_available(self) -> bool:
            return True

        def is_key_pressed(self, key) -> bool:  # noqa: ANN001
            return False

        def get_pressed_keys(self, keys) -> set[str]:  # noqa: ANN001
            return set()

    monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: _Native())
    handler = KeyboardHandler(_App())
    # Calling the event handler must not raise – the "native" poller has
    # no on_key_down attribute but the gate in the handler prevents access.
    handler.on_key_down({"key": "w"})
    handler.on_key_up({"key": "w"})


def test_is_connected_caches_probe_result(monkeypatch) -> None:
    monkeypatch.setattr(keyboard_module.platform, "system", lambda: "Linux")
    handler = KeyboardHandler(_App())

    calls: list[bool] = []

    def _probe() -> bool:
        calls.append(True)
        return True

    handler._probe_keyboard_connected = _probe  # type: ignore[assignment]
    # Time mocked forward in small steps – second call must hit the cache.
    now = {"t": 0.0}
    monkeypatch.setattr(keyboard_module.time, "monotonic", lambda: now["t"])

    now["t"] = 100.0
    assert handler.is_connected() is True
    now["t"] = 100.5  # <1.0s elapsed
    assert handler.is_connected() is True
    # Only one probe call so far.
    assert len(calls) == 1

    now["t"] = 101.6  # >1.0s gap → re-probe
    handler.is_connected()
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# _probe_linux_keyboard_connected
# ---------------------------------------------------------------------------

_KBD_PROC = """
I: Bus=0003 Vendor=046d Product=c31c
N: Name="Logitech USB Keyboard"
H: Handlers=sysrq kbd event2 leds

I: Bus=0003 Vendor=046d Product=c52b
N: Name="Logitech M-UV96a Optical Mouse"
H: Handlers=mouse0 event3
""".strip()

_NO_KBD_PROC = """
I: Bus=0000 Vendor=0000 Product=0001
N: Name="GPIO Button"
H: Handlers=kbd event1

I: Bus=0003 Vendor=046d Product=c52b
N: Name="Logitech Mouse"
H: Handlers=mouse0 event3
""".strip()


def _install_fake_open(monkeypatch, contents: str | None) -> None:
    real_open = builtins.open

    def _fake_open(path, *args, **kwargs):  # noqa: ANN001, ANN202
        if str(path) == "/proc/bus/input/devices":
            if contents is None:
                raise OSError("not available")
            # text-mode open returns a context manager backed by StringIO
            import io

            return io.StringIO(contents)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _fake_open)


def test_probe_linux_detects_keyboard_from_procfs(monkeypatch) -> None:
    _install_fake_open(monkeypatch, _KBD_PROC)
    assert KeyboardHandler._probe_linux_keyboard_connected() is True


def test_probe_linux_ignores_gpio_power_button(monkeypatch) -> None:
    _install_fake_open(monkeypatch, _NO_KBD_PROC)
    # No real keyboard – GPIO "kbd" handler must be filtered out,
    # and the fallback glob is unlikely to match in the test env.
    monkeypatch.setattr(keyboard_module.glob, "glob", lambda _p: [])
    assert KeyboardHandler._probe_linux_keyboard_connected() is False


def test_probe_linux_falls_back_to_by_id_glob_on_oserror(monkeypatch) -> None:
    _install_fake_open(monkeypatch, None)

    calls: list[str] = []

    def _fake_glob(pattern: str):  # noqa: ANN202
        calls.append(pattern)
        return ["/dev/input/by-id/fake-keyboard-usb-kbd"]

    monkeypatch.setattr(keyboard_module.glob, "glob", _fake_glob)
    assert KeyboardHandler._probe_linux_keyboard_connected() is True
    # The glob must have been consulted with the kbd-suffixed pattern.
    assert any("kbd" in p for p in calls)


def test_probe_linux_returns_false_when_by_id_empty(monkeypatch) -> None:
    _install_fake_open(monkeypatch, None)
    monkeypatch.setattr(keyboard_module.glob, "glob", lambda _p: [])
    assert KeyboardHandler._probe_linux_keyboard_connected() is False


# ---------------------------------------------------------------------------
# _discrete_polled_keys
# ---------------------------------------------------------------------------


def test_discrete_polled_keys_includes_modal_and_user_bindings(monkeypatch) -> None:
    monkeypatch.setattr(keyboard_module.platform, "system", lambda: "Linux")
    app = _App()
    app._config.controller.key_reset = "r"
    app._config.controller.key_toggle_help = "h"
    app._config.controller.key_toggle_zones = "z"
    app._config.controller.key_speed_up = "+"
    app._config.controller.key_speed_down = "-"
    app._config.controller.key_next_marker = "]"
    app._config.controller.key_prev_marker = "["

    handler = KeyboardHandler(app)
    discrete = handler._discrete_polled_keys()
    # Modal keys are always included.
    for modal in ("Tab", "Enter", "Escape", "ArrowUp"):
        assert modal in discrete
    # User-configured action keys are folded in.
    for user in ("r", "h", "z", "+", "-", "]", "["):
        assert user in discrete


def test_discrete_polled_keys_skips_empty_bindings(monkeypatch) -> None:
    monkeypatch.setattr(keyboard_module.platform, "system", lambda: "Linux")
    app = _App()
    # Wipe all user bindings – the helper must still return modal keys.
    for fname in KeyboardHandler._DISCRETE_ACTION_FIELDS:
        setattr(app._config.controller, fname, "")
    handler = KeyboardHandler(app)
    discrete = handler._discrete_polled_keys()
    assert discrete == set(KeyboardHandler._MODAL_DISCRETE_KEYS)
