# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Platform-layer coverage for :mod:`openfollow.input.keyboard`.

Complements :mod:`tests.test_input_keyboard` (discrete-key edge
detection + movement-layout parametrisation) and
:mod:`tests.test_input_keyboard_platforms` (fallback-poller mechanics,
factory-function platform dispatch, ``_probe_linux_keyboard_connected``
procfs / glob fallback).

This file drives the remaining branches:

* ``MacOSKeyboardPoller.is_key_pressed`` matrix –
  unavailable / unknown-key / left-hand modifier / right-hand fallback
  / no match.
* ``MacOSKeyboardPoller.__init__`` Quartz load failure + layout-map
  Z-differs (QWERTZ) INFO log + layout-map None WARNING log.
* ``_build_layout_keycode_map`` failure branches (Carbon load
  ``OSError`` / ``in_dll`` ``ValueError`` / ``TIS...InputSource``
  null / ``layout_data`` null / ``layout_ptr`` null) driven via a
  ``ctypes.cdll.LoadLibrary`` monkeypatch – plus a success branch that
  returns a ``{char: keycode}`` mapping.
* ``_probe_keyboard_connected`` platform dispatch (Darwin vs Linux vs
  other).
* ``_probe_linux_keyboard_connected`` 'gpio / power-button' filter:
  procfs entry whose handlers mention ``kbd`` but whose name is the
  kernel's synthetic power-button device must be treated as
  disconnected so we don't lie about a real keyboard being attached.
* ``KeyboardHandler.update`` vertical-axis binding (``z_up`` / ``z_down``)
  and the all-idle ``return None`` branch.
* ``KeyboardHandler.on_key_up`` event filter edge case – unknown keys
  must take the ``if key in POLLED_KEYS`` False branch cleanly.
"""

from __future__ import annotations

import ctypes
from types import SimpleNamespace
from typing import Any

import pytest

import openfollow.input.keyboard as keyboard_module
from openfollow.configuration import AppConfig
from openfollow.input.keyboard import (
    _MAC_KEY_CODES,
    KeyboardHandler,
    MacOSKeyboardPoller,
    _build_layout_keycode_map,
)

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# Shared fakes
# --------------------------------------------------------------------------- #


class _DummyApp:
    def __init__(self) -> None:
        self._config = AppConfig()
        self._selected_id: int | None = 0

    def get_marker_move_speed(self, marker_id: int | None) -> float:
        """Mirror ``OpenFollowApp.get_marker_move_speed``."""
        if marker_id is None:
            return self._config.marker.move_speed
        return self._config.marker_move_speeds.get(
            marker_id,
            self._config.marker.move_speed,
        )


class _FakePoller:
    def __init__(self) -> None:
        self.pressed: set[str] = set()

    def is_key_pressed(self, key: str) -> bool:
        return key in self.pressed

    def get_pressed_keys(self, keys: list[str]) -> set[str]:
        return {k for k in keys if k in self.pressed}

    def is_available(self) -> bool:
        return True


# --------------------------------------------------------------------------- #
# MacOSKeyboardPoller.is_key_pressed matrix
# --------------------------------------------------------------------------- #


class _RecordingCGState:
    """Stand-in for ``CGEventSourceKeyState``.

    Tracks every ``(state_id, keycode)`` invocation and returns True
    for any keycode in ``pressed_codes``.
    """

    def __init__(self, pressed_codes: set[int]) -> None:
        self.pressed = set(pressed_codes)
        self.calls: list[tuple[int, int]] = []

    def __call__(self, state_id: int, keycode: int) -> bool:
        self.calls.append((state_id, keycode))
        return keycode in self.pressed


def _make_poller_with_quartz(
    pressed_codes: set[int], *, key_codes: dict[str, int] | None = None
) -> tuple[MacOSKeyboardPoller, _RecordingCGState]:
    """Construct a ``MacOSKeyboardPoller`` bypassing the real ctypes init.

    ``MacOSKeyboardPoller.__init__`` does two things we don't want to
    run in a test: ``ctypes.cdll.LoadLibrary("/System/.../Quartz")`` and
    the Carbon layout-map probe.  Use ``object.__new__`` + manual field
    seeding to side-step both and plant a recording CG-state func.
    """
    poller = object.__new__(MacOSKeyboardPoller)
    poller._quartz = object()  # not None – truthy for is_available
    cg_state = _RecordingCGState(pressed_codes)
    poller._cg_event_source_key_state = cg_state
    poller._available = True
    poller._key_codes = dict(key_codes or _MAC_KEY_CODES)
    # Layout-refresh throttle (bypassed __init__): seed so _maybe_refresh_layout
    # is a no-op (now - last < interval) and leaves the seeded _key_codes alone.
    poller._layout_clock = lambda: 0.0
    poller._last_layout_check = 0.0
    poller._layout_check_interval = 1.0
    return poller, cg_state


class TestMacOSPollerIsKeyPressed:
    def test_unavailable_poller_returns_false(self) -> None:
        poller, _ = _make_poller_with_quartz(set())
        poller._available = False
        assert poller.is_key_pressed("a") is False

    def test_unavailable_when_cg_func_is_none(self) -> None:
        poller, _ = _make_poller_with_quartz(set())
        poller._cg_event_source_key_state = None
        assert poller.is_key_pressed("a") is False

    def test_unknown_key_returns_false_without_touching_quartz(self) -> None:
        poller, cg = _make_poller_with_quartz(set())
        assert poller.is_key_pressed("Hyper") is False
        assert cg.calls == []

    def test_known_key_hits_state_func_once(self) -> None:
        poller, cg = _make_poller_with_quartz({_MAC_KEY_CODES["a"]})
        assert poller.is_key_pressed("a") is True
        assert cg.calls == [(1, _MAC_KEY_CODES["a"])]

    def test_left_modifier_unset_falls_back_to_right_variant(self) -> None:
        """Modifiers map to a left-hand virtual keycode by default. When
        the operator uses the right-hand Shift / Ctrl / Alt, the poller
        must try the right-hand code before reporting "not pressed".
        """
        # Plant "right-shift pressed, left-shift not pressed".
        right_code = _MAC_KEY_CODES["RightShift"]
        left_code = _MAC_KEY_CODES["Shift"]
        poller, cg = _make_poller_with_quartz({right_code})
        assert poller.is_key_pressed("Shift") is True
        # Two calls: left first, then right fallback.
        assert cg.calls[0] == (1, left_code)
        assert cg.calls[1] == (1, right_code)

    def test_modifier_without_right_variant_in_map_returns_false(self) -> None:
        # Strip both variants of Alt from the map.
        codes = dict(_MAC_KEY_CODES)
        codes.pop("RightAlt", None)
        codes["Alt"] = 0x3A  # left-only presence
        poller, _ = _make_poller_with_quartz(set(), key_codes=codes)
        assert poller.is_key_pressed("Alt") is False

    def test_neither_variant_pressed_returns_false(self) -> None:
        poller, cg = _make_poller_with_quartz(set())
        assert poller.is_key_pressed("Shift") is False
        # Left was queried, then right variant was queried too.
        assert len(cg.calls) == 2

    def test_non_modifier_unpressed_key_skips_right_variant_lookup(self) -> None:
        poller, cg = _make_poller_with_quartz(set())  # 'a' not pressed
        assert poller.is_key_pressed("a") is False
        # Exactly one call – no right-variant lookup for plain letters.
        assert cg.calls == [(1, _MAC_KEY_CODES["a"])]


# --------------------------------------------------------------------------- #
# MacOSKeyboardPoller.__init__ – Quartz load failure + layout-map branches
# --------------------------------------------------------------------------- #


class _FakeQuartzLib:
    """Stand-in for the Quartz framework cdll handle."""

    def __init__(self) -> None:
        self.CGEventSourceKeyState = _RecordingCGState(set())


class TestMacOSPollerInit:
    def test_quartz_load_failure_marks_unavailable(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If LoadLibrary("Quartz") raises, the poller must set
        ``_available=False`` and log a WARNING rather than bubbling.
        """

        def _fail_load(name: str) -> Any:
            raise OSError("not found")

        monkeypatch.setattr(ctypes.cdll, "LoadLibrary", _fail_load)

        with caplog.at_level("WARNING", logger="openfollow.input.keyboard"):
            poller = MacOSKeyboardPoller()
        assert poller.is_available() is False
        assert poller._cg_event_source_key_state is None
        assert any("Failed to load Quartz framework" in r.message for r in caplog.records if r.levelname == "WARNING")

    def test_layout_map_different_z_logs_info(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Force Quartz load to succeed via a fake.
        monkeypatch.setattr(ctypes.cdll, "LoadLibrary", lambda name: _FakeQuartzLib())
        # QWERTZ: z sits at keycode 0x10 (US QWERTY's Y).
        qwertz = {"a": 0x00, "z": 0x10, "y": 0x06}
        monkeypatch.setattr(keyboard_module, "_build_layout_keycode_map", lambda: qwertz)

        with caplog.at_level("INFO", logger="openfollow.input.keyboard"):
            poller = MacOSKeyboardPoller()

        assert poller._key_codes["z"] == 0x10
        assert any(
            "Non-US keyboard layout detected" in r.message and "z=0x10" in r.message
            for r in caplog.records
            if r.levelname == "INFO"
        )

    def test_layout_map_none_logs_warning(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``_build_layout_keycode_map`` returning ``None`` means Carbon
        or CoreFoundation failed to load – we fall back to the hard-coded
        US QWERTY map and warn so QWERTZ operators know why Z/Y feel wrong.
        """
        monkeypatch.setattr(ctypes.cdll, "LoadLibrary", lambda name: _FakeQuartzLib())
        monkeypatch.setattr(keyboard_module, "_build_layout_keycode_map", lambda: None)
        with caplog.at_level("WARNING", logger="openfollow.input.keyboard"):
            poller = MacOSKeyboardPoller()
        assert poller._key_codes["z"] == _MAC_KEY_CODES["z"]
        assert any(
            "Could not resolve active keyboard layout" in r.message for r in caplog.records if r.levelname == "WARNING"
        )

    def test_layout_map_same_z_does_not_log_info(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """US layout: z keycode matches the hard-coded fallback. No INFO
        log – operators on QWERTY shouldn't get a phantom "layout
        detected" every startup.
        """
        monkeypatch.setattr(ctypes.cdll, "LoadLibrary", lambda name: _FakeQuartzLib())
        monkeypatch.setattr(
            keyboard_module,
            "_build_layout_keycode_map",
            lambda: {"z": _MAC_KEY_CODES["z"]},
        )
        with caplog.at_level("INFO", logger="openfollow.input.keyboard"):
            MacOSKeyboardPoller()
        assert not any("Non-US keyboard layout detected" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# _build_layout_keycode_map – ctypes failure branches
# --------------------------------------------------------------------------- #


class TestBuildLayoutKeycodeMapFailures:
    """The Carbon / CoreFoundation chain has five failure points, each
    returning ``None`` so the caller falls back to the US layout.
    """

    def test_carbon_load_failure_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _fail_load(name: str) -> Any:
            raise OSError("Carbon missing")

        monkeypatch.setattr(ctypes.cdll, "LoadLibrary", _fail_load)
        assert _build_layout_keycode_map() is None

    def test_tispropertyunicodekeylayoutdata_missing_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``ctypes.c_void_p.in_dll`` raises ``ValueError`` when the
        symbol isn't exported – happens on older macOS where Carbon was
        slimmed down.
        """
        fake_lib = SimpleNamespace(
            TISCopyCurrentKeyboardLayoutInputSource=None,
            TISGetInputSourceProperty=None,
            LMGetKbdType=None,
            UCKeyTranslate=None,
        )
        monkeypatch.setattr(ctypes.cdll, "LoadLibrary", lambda _name: fake_lib)

        def _fail_in_dll(_cls: Any, _lib: Any, _name: str) -> Any:
            raise ValueError("symbol missing")

        # ``c_void_p.in_dll`` is a classmethod on ``ctypes.c_void_p``.
        monkeypatch.setattr(ctypes.c_void_p, "in_dll", classmethod(_fail_in_dll))
        assert _build_layout_keycode_map() is None

    def test_null_input_source_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``TISCopyCurrentKeyboardLayoutInputSource`` returns 0 (null
        pointer) when no layout is currently loaded – rare but happens
        on fresh user sessions before Dock launches.
        """
        fake_lib = _build_fake_carbon_lib(source_ptr=0)
        monkeypatch.setattr(ctypes.cdll, "LoadLibrary", lambda _name: fake_lib)
        monkeypatch.setattr(
            ctypes.c_void_p,
            "in_dll",
            classmethod(lambda cls, _lib, _name: ctypes.c_void_p(0x1000)),
        )
        assert _build_layout_keycode_map() is None

    def test_null_layout_data_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_lib = _build_fake_carbon_lib(source_ptr=0xDEAD, layout_data_ptr=0)
        monkeypatch.setattr(ctypes.cdll, "LoadLibrary", lambda _name: fake_lib)
        monkeypatch.setattr(
            ctypes.c_void_p,
            "in_dll",
            classmethod(lambda cls, _lib, _name: ctypes.c_void_p(0x1000)),
        )
        assert _build_layout_keycode_map() is None

    def test_null_layout_ptr_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_lib = _build_fake_carbon_lib(source_ptr=0xDEAD, layout_data_ptr=0xBEEF, layout_byte_ptr=0)
        monkeypatch.setattr(ctypes.cdll, "LoadLibrary", lambda _name: fake_lib)
        monkeypatch.setattr(
            ctypes.c_void_p,
            "in_dll",
            classmethod(lambda cls, _lib, _name: ctypes.c_void_p(0x1000)),
        )
        assert _build_layout_keycode_map() is None


def _build_fake_carbon_lib(
    *,
    source_ptr: int = 0xDEAD,
    layout_data_ptr: int = 0xBEEF,
    layout_byte_ptr: int = 0xCAFE,
    translate_result: dict[int, int] | None = None,
    mapping: dict[int, str] | None = None,
) -> Any:
    """Build a Carbon + CoreFoundation stand-in usable by
    ``_build_layout_keycode_map``.

    ``translate_result`` overrides ``UCKeyTranslate``'s status per
    keycode (missing → 0 = success).  ``mapping`` overrides which
    character each keycode produces (default: letter at keycode 0x00
    is 'a', and nothing else).
    """
    translate_result = translate_result or {}
    mapping = mapping or {}

    class _FakeFunc:
        def __init__(self) -> None:
            self.restype: Any = None
            self.argtypes: Any = None

        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            return None  # overridden below

    fake = SimpleNamespace()

    def _copy_source() -> int:
        return source_ptr

    def _get_prop(_source: int, _prop: int) -> int:
        return layout_data_ptr if _source == source_ptr else 0

    def _byte_ptr(_data: int) -> int:
        return layout_byte_ptr if _data == layout_data_ptr else 0

    def _release(_ptr: int) -> None:
        return None

    def _kbd_type() -> int:
        return 40

    def _translate(
        _layout_ptr: int,
        keycode: int,
        _action: int,
        _modifiers: int,
        _kbd: int,
        _flags: int,
        _dead_state: Any,
        _max_len: int,
        actual: Any,
        chars: Any,
    ) -> int:
        status = translate_result.get(keycode, 0)
        ch = mapping.get(keycode)
        if ch is None:
            actual._obj.value = 0
            return status
        chars[0] = ord(ch)
        actual._obj.value = 1
        return status

    # Carbon side
    fake.TISCopyCurrentKeyboardLayoutInputSource = _FakeFunc()
    fake.TISCopyCurrentKeyboardLayoutInputSource.__call__ = staticmethod(_copy_source)
    fake.TISCopyCurrentKeyboardLayoutInputSource = _copy_source
    fake.TISGetInputSourceProperty = _get_prop
    fake.LMGetKbdType = _kbd_type
    fake.UCKeyTranslate = _translate
    # CoreFoundation side – same object (both LoadLibrary calls return
    # it in the test; ``ctypes.cdll.LoadLibrary`` is mocked to a lambda
    # that ignores the path, so both calls get the same fake object).
    fake.CFDataGetBytePtr = _byte_ptr
    fake.CFRelease = _release
    return fake


class TestBuildLayoutKeycodeMapSuccess:
    def test_returns_mapping_for_letters_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Success path: walk all 128 keycodes, keep only letters."""
        fake_lib = _build_fake_carbon_lib(
            mapping={0x00: "a", 0x06: "z", 0x10: "y", 0x31: " "},
        )
        monkeypatch.setattr(ctypes.cdll, "LoadLibrary", lambda _name: fake_lib)
        monkeypatch.setattr(
            ctypes.c_void_p,
            "in_dll",
            classmethod(lambda cls, _lib, _name: ctypes.c_void_p(0x1000)),
        )
        result = _build_layout_keycode_map()
        assert result == {"a": 0x00, "z": 0x06, "y": 0x10}

    def test_maps_digits_and_symbols(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A bindable field can use a digit/symbol; those positions differ on
        AZERTY/QWERTZ, so they must be layout-translated too (not just letters).
        Space is still excluded."""
        fake_lib = _build_fake_carbon_lib(
            mapping={0x12: "1", 0x2C: "/", 0x00: "a", 0x31: " "},
        )
        monkeypatch.setattr(ctypes.cdll, "LoadLibrary", lambda _name: fake_lib)
        monkeypatch.setattr(
            ctypes.c_void_p,
            "in_dll",
            classmethod(lambda cls, _lib, _name: ctypes.c_void_p(0x1000)),
        )
        result = _build_layout_keycode_map()
        assert result == {"1": 0x12, "/": 0x2C, "a": 0x00}  # " " excluded

    def test_prefers_lowest_keycode_for_a_repeated_char(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A char reachable from several physical keys keeps the lowest/primary
        keycode (ascending iteration + first-wins)."""
        fake_lib = _build_fake_carbon_lib(mapping={0x0A: "x", 0x02: "x"})
        monkeypatch.setattr(ctypes.cdll, "LoadLibrary", lambda _name: fake_lib)
        monkeypatch.setattr(
            ctypes.c_void_p,
            "in_dll",
            classmethod(lambda cls, _lib, _name: ctypes.c_void_p(0x1000)),
        )
        result = _build_layout_keycode_map()
        assert result == {"x": 0x02}

    def test_skips_non_printable_char(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A control char from UCKeyTranslate is not a bindable key – skip it."""
        fake_lib = _build_fake_carbon_lib(mapping={0x00: "a", 0x05: "\x01"})
        monkeypatch.setattr(ctypes.cdll, "LoadLibrary", lambda _name: fake_lib)
        monkeypatch.setattr(
            ctypes.c_void_p,
            "in_dll",
            classmethod(lambda cls, _lib, _name: ctypes.c_void_p(0x1000)),
        )
        result = _build_layout_keycode_map()
        assert result == {"a": 0x00}

    def test_status_nonzero_skips_keycode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """UCKeyTranslate returning a non-zero status means the
        translation failed – skip that keycode rather than inserting
        garbage.
        """
        fake_lib = _build_fake_carbon_lib(
            mapping={0x00: "a", 0x06: "z"},
            translate_result={0x06: -1},
        )
        monkeypatch.setattr(ctypes.cdll, "LoadLibrary", lambda _name: fake_lib)
        monkeypatch.setattr(
            ctypes.c_void_p,
            "in_dll",
            classmethod(lambda cls, _lib, _name: ctypes.c_void_p(0x1000)),
        )
        result = _build_layout_keycode_map()
        assert result == {"a": 0x00}

    def test_empty_mapping_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If no keycode produced a letter (surreal but possible with a
        stripped-down layout), return None so the caller falls back to
        US QWERTY.
        """
        fake_lib = _build_fake_carbon_lib(mapping={})
        monkeypatch.setattr(ctypes.cdll, "LoadLibrary", lambda _name: fake_lib)
        monkeypatch.setattr(
            ctypes.c_void_p,
            "in_dll",
            classmethod(lambda cls, _lib, _name: ctypes.c_void_p(0x1000)),
        )
        assert _build_layout_keycode_map() is None


# --------------------------------------------------------------------------- #
# _probe_keyboard_connected – platform dispatch
# --------------------------------------------------------------------------- #


class TestProbeKeyboardConnected:
    def test_darwin_delegates_to_is_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(keyboard_module.platform, "system", lambda: "Darwin")
        poller = _FakePoller()
        monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)
        handler = KeyboardHandler(_DummyApp())
        assert handler._probe_keyboard_connected() is True

    def test_linux_delegates_to_linux_probe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(keyboard_module.platform, "system", lambda: "Linux")
        poller = _FakePoller()
        monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)
        monkeypatch.setattr(
            keyboard_module.KeyboardHandler,
            "_probe_linux_keyboard_connected",
            staticmethod(lambda: True),
        )
        handler = KeyboardHandler(_DummyApp())
        assert handler._probe_keyboard_connected() is True

    def test_other_platform_falls_back_to_is_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(keyboard_module.platform, "system", lambda: "FreeBSD")
        poller = _FakePoller()
        monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)
        handler = KeyboardHandler(_DummyApp())
        assert handler._probe_keyboard_connected() is True


# --------------------------------------------------------------------------- #
# _probe_linux_keyboard_connected – pseudo-keyboard filter
# --------------------------------------------------------------------------- #


class TestProbeLinuxPseudoKeyboardFilter:
    def test_gpio_power_button_entry_is_treated_as_not_a_keyboard(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """Rockpi / Raspberry Pi kernels expose a power button under
        ``input<N>`` that claims ``Handlers=kbd`` – ignore it so the app
        doesn't report a keyboard attached when only the power rail is.
        """
        procfs = tmp_path / "devices"
        procfs.write_text(
            "\n".join(
                [
                    "I: Bus=0019 Vendor=0001 Product=0001",
                    'N: Name="gpio-power-button"',
                    "H: Handlers=kbd event0",
                    "",
                ]
            )
        )
        # Ensure glob fallback doesn't accidentally pass.
        monkeypatch.setattr(keyboard_module.glob, "glob", lambda _p: [])

        real_open = open

        def _fake_open(path: str, *args: Any, **kw: Any) -> Any:
            if str(path) == "/proc/bus/input/devices":
                return real_open(procfs, *args, **kw)
            return real_open(path, *args, **kw)

        monkeypatch.setattr("builtins.open", _fake_open)
        from openfollow.input.keyboard import KeyboardHandler as _KH

        assert _KH._probe_linux_keyboard_connected() is False

    def test_empty_block_between_devices_is_skipped(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        """``/proc/bus/input/devices`` contains blocks separated by
        blank lines; ``"\\n\\n\\n"`` runs produce empty blocks in the
        ``split("\\n\\n")`` result.  The parser's ``if not lines:
        continue`` guard handles that cleanly.
        """
        procfs = tmp_path / "devices"
        # Three blocks joined by ``\n\n`` separators – the middle one
        # is deliberately empty so the ``.split("\n\n")`` produces an
        # empty string in the iteration, exercising the ``if not lines:
        # continue`` guard.
        procfs.write_text(
            "I: Bus=0003\n"
            'N: Name="Keychron K8"\n'
            "H: Handlers=sysrq kbd event1"
            "\n\n"
            ""
            "\n\n"
            "I: Bus=0019\n"
            'N: Name="Lid Switch"\n'
            "H: Handlers=event2"
        )
        real_open = open

        def _fake_open(path: str, *args: Any, **kw: Any) -> Any:
            if str(path) == "/proc/bus/input/devices":
                return real_open(procfs, *args, **kw)
            return real_open(path, *args, **kw)

        monkeypatch.setattr("builtins.open", _fake_open)
        from openfollow.input.keyboard import KeyboardHandler as _KH

        # The Keychron block has ``kbd`` in handlers and isn't GPIO/power
        # – true keyboard. The empty middle block hits the guard cleanly.
        assert _KH._probe_linux_keyboard_connected() is True


# --------------------------------------------------------------------------- #
# KeyboardHandler.update – z_up / z_down / all-idle return None
# --------------------------------------------------------------------------- #


class TestKeyboardUpdateVerticalAndIdle:
    def test_z_up_binding_raises_vz(self, monkeypatch: pytest.MonkeyPatch) -> None:
        poller = _FakePoller()
        monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)
        app = _DummyApp()
        # Default bindings: key_move_z_up="r", key_move_z_down="f".
        poller.pressed = {app._config.controller.key_move_z_up}
        handler = KeyboardHandler(app)
        move_speed = app._config.marker.move_speed
        assert handler.update(0.016) == (0.0, 0.0, move_speed)

    def test_z_down_binding_lowers_vz(self, monkeypatch: pytest.MonkeyPatch) -> None:
        poller = _FakePoller()
        monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)
        app = _DummyApp()
        poller.pressed = {app._config.controller.key_move_z_down}
        handler = KeyboardHandler(app)
        move_speed = app._config.marker.move_speed
        assert handler.update(0.016) == (0.0, 0.0, -move_speed)

    def test_no_keys_pressed_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        poller = _FakePoller()
        monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)
        handler = KeyboardHandler(_DummyApp())
        assert handler.update(0.016) is None


# --------------------------------------------------------------------------- #
# on_key_up POLLED_KEYS filter
# --------------------------------------------------------------------------- #


class TestOnKeyUpPolledKeyFilter:
    def test_key_up_for_unknown_key_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        poller = _FakePoller()
        monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)
        handler = KeyboardHandler(_DummyApp())
        # Pretend we're in fallback mode so the method doesn't early-exit.
        handler._is_fallback = True
        poller.on_key_up = lambda _k: setattr(poller, "_touched", True)  # type: ignore[attr-defined]
        handler.on_key_up({"key": "MediaPlay"})
        assert getattr(poller, "_touched", False) is False

    def test_key_up_empty_event_falls_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Event dicts without a ``"key"`` field (edge case from GTK
        synthetic events) must not AttributeError – the ``event.get("key", "")``
        default is ``""`` which then also fails the ``in POLLED_KEYS`` gate.
        """
        poller = _FakePoller()
        monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)
        handler = KeyboardHandler(_DummyApp())
        handler._is_fallback = True
        handler.on_key_up({})  # must not raise


class TestMacOSLayoutRefresh:
    """``_maybe_refresh_layout`` re-resolves the keycode map on a cadence so a
    runtime input-source switch (QWERTZ<->US) doesn't leave it stale."""

    def test_refresh_rebuilds_key_codes_when_layout_changes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        poller, _ = _make_poller_with_quartz(set())
        poller._layout_clock = lambda: 100.0  # >> interval since last check (0.0)
        monkeypatch.setattr(keyboard_module, "_build_layout_keycode_map", lambda: {"z": 0x63})

        poller._maybe_refresh_layout()

        assert poller._key_codes["z"] == 0x63

    def test_refresh_is_throttled_within_interval(self, monkeypatch: pytest.MonkeyPatch) -> None:
        poller, _ = _make_poller_with_quartz(set())
        poller._layout_clock = lambda: 0.5  # within the 1.0s interval since 0.0
        calls: list[int] = []
        monkeypatch.setattr(keyboard_module, "_build_layout_keycode_map", lambda: calls.append(1) or {})

        poller._maybe_refresh_layout()

        assert calls == []  # not re-resolved

    def test_refresh_noop_when_builder_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        poller, _ = _make_poller_with_quartz(set(), key_codes={"z": 0x10})
        before = dict(poller._key_codes)
        poller._layout_clock = lambda: 100.0
        monkeypatch.setattr(keyboard_module, "_build_layout_keycode_map", lambda: None)

        poller._maybe_refresh_layout()

        assert poller._key_codes == before  # layout unresolvable → keep prior map

    def test_refresh_noop_when_layout_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        same = {"z": 0x06}
        poller, _ = _make_poller_with_quartz(set(), key_codes={**_MAC_KEY_CODES, **same})
        poller._layout_clock = lambda: 100.0
        monkeypatch.setattr(keyboard_module, "_build_layout_keycode_map", lambda: same)

        poller._maybe_refresh_layout()

        # Rebuilt map equals the current one → no change.
        assert poller._key_codes == {**_MAC_KEY_CODES, **same}
