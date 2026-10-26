# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""``LinuxEvdevKeyboardPoller`` against a fake ``evdev`` module: no real input device is opened."""

from __future__ import annotations

import logging
import os
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import openfollow.input.keyboard as keyboard_module
from openfollow.configuration import AppConfig
from openfollow.input.keyboard import (
    POLLED_KEYS,
    FallbackKeyboardPoller,
    KeyboardHandler,
    LinuxEvdevKeyboardPoller,
    create_keyboard_poller,
)

pytestmark = pytest.mark.unit

# The kernel's codes (linux/input-event-codes.h) for every name the poller looks up.
KEY_CODES: dict[str, int] = {
    "KEY_ESC": 1,
    "KEY_TAB": 15,
    "KEY_Q": 16,
    "KEY_W": 17,
    "KEY_E": 18,
    "KEY_R": 19,
    "KEY_T": 20,
    "KEY_Y": 21,
    "KEY_U": 22,
    "KEY_I": 23,
    "KEY_O": 24,
    "KEY_P": 25,
    "KEY_ENTER": 28,
    "KEY_LEFTCTRL": 29,
    "KEY_A": 30,
    "KEY_S": 31,
    "KEY_D": 32,
    "KEY_F": 33,
    "KEY_G": 34,
    "KEY_H": 35,
    "KEY_J": 36,
    "KEY_K": 37,
    "KEY_L": 38,
    "KEY_LEFTSHIFT": 42,
    "KEY_Z": 44,
    "KEY_X": 45,
    "KEY_C": 46,
    "KEY_V": 47,
    "KEY_B": 48,
    "KEY_N": 49,
    "KEY_M": 50,
    "KEY_RIGHTSHIFT": 54,
    "KEY_LEFTALT": 56,
    "KEY_SPACE": 57,
    "KEY_KP7": 71,
    "KEY_KP8": 72,
    "KEY_KP9": 73,
    "KEY_KP4": 75,
    "KEY_KP5": 76,
    "KEY_KP6": 77,
    "KEY_KP1": 79,
    "KEY_KP2": 80,
    "KEY_KP3": 81,
    "KEY_KP0": 82,
    "KEY_KPENTER": 96,
    "KEY_RIGHTCTRL": 97,
    "KEY_RIGHTALT": 100,
    "KEY_UP": 103,
    "KEY_LEFT": 105,
    "KEY_RIGHT": 106,
    "KEY_DOWN": 108,
    "KEY_LEFTMETA": 125,
    "KEY_RIGHTMETA": 126,
}
EV_KEY = 1
BTN_LEFT = 272
KEYBOARD_KEYS = sorted(KEY_CODES.values())
MOUSE_KEYS = [BTN_LEFT]

# The evdev keys each polled key reads, US positions.
EXPECTED_KEYS: dict[str, tuple[str, ...]] = {
    **{letter: (f"KEY_{letter.upper()}",) for letter in "abcdefghijklmnopqrstuvwxyz"},
    "Tab": ("KEY_TAB",),
    "Enter": ("KEY_ENTER", "KEY_KPENTER"),
    "Escape": ("KEY_ESC",),
    "Shift": ("KEY_LEFTSHIFT", "KEY_RIGHTSHIFT"),
    "Control": ("KEY_LEFTCTRL", "KEY_RIGHTCTRL"),
    "Alt": ("KEY_LEFTALT", "KEY_RIGHTALT"),
    "Meta": ("KEY_LEFTMETA", "KEY_RIGHTMETA"),
    "Space": ("KEY_SPACE",),
    "ArrowUp": ("KEY_UP",),
    "ArrowDown": ("KEY_DOWN",),
    "ArrowLeft": ("KEY_LEFT",),
    "ArrowRight": ("KEY_RIGHT",),
    **{f"Numpad{n}": (f"KEY_KP{n}",) for n in range(10)},
}


@dataclass
class _Node:
    """One ``/dev/input/event*`` node: what it reports and how it fails."""

    keys: list[int]
    held: set[int] = field(default_factory=set)
    unplugged: bool = False
    open_error: bool = False
    caps_error: bool = False
    close_error: bool = False
    closed: int = 0


class _FakeDevice:
    def __init__(self, node: _Node) -> None:
        self._node = node

    def capabilities(self) -> dict[int, list[int]]:
        if self._node.caps_error:
            raise OSError("capabilities")
        return {0: [0, 1], EV_KEY: list(self._node.keys)}

    def active_keys(self) -> list[int]:
        if self._node.unplugged:
            raise OSError(19, "No such device")
        return sorted(self._node.held)

    def close(self) -> None:
        self._node.closed += 1
        if self._node.close_error:
            raise OSError("close")


class _FakeEvdev(types.ModuleType):
    """The slice of python-evdev the poller uses, over real files standing in for the nodes."""

    def __init__(self, input_dir: Path) -> None:
        super().__init__("evdev")
        self.input_dir = input_dir
        self.ecodes = SimpleNamespace(EV_KEY=EV_KEY, **KEY_CODES)
        self.nodes: dict[str, _Node] = {}
        self.opened: list[str] = []
        self.list_error = False

    def add(self, name: str, keys: list[int] | None = None, **flags: Any) -> _Node:
        path = self.input_dir / name
        path.touch()
        node = _Node(keys=KEYBOARD_KEYS if keys is None else keys, **flags)
        self.nodes[str(path)] = node
        return node

    def replace(self, name: str, keys: list[int]) -> _Node:
        """A new node at the same path (a different inode), as after an unplug and replug."""
        staged = self.input_dir / f"{name}.new"
        staged.touch()
        os.replace(staged, self.input_dir / name)
        node = _Node(keys=keys)
        self.nodes[str(self.input_dir / name)] = node
        return node

    def list_devices(self, input_device_dir: str) -> list[str]:
        assert input_device_dir == str(self.input_dir)
        if self.list_error:
            raise OSError("glob")
        return sorted(self.nodes)

    def InputDevice(self, path: str) -> _FakeDevice:  # noqa: N802 - python-evdev's name
        self.opened.append(path)
        node = self.nodes[path]
        if node.open_error:
            raise OSError(13, "Permission denied")
        return _FakeDevice(node)


class _Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def input_dir(tmp_path: Path) -> Path:
    path = tmp_path / "input"
    path.mkdir()
    return path


@pytest.fixture
def fake_evdev(monkeypatch: pytest.MonkeyPatch, input_dir: Path) -> _FakeEvdev:
    fake = _FakeEvdev(input_dir)
    monkeypatch.setitem(sys.modules, "evdev", fake)
    return fake


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


def _poller(input_dir: Path, clock: _Clock) -> LinuxEvdevKeyboardPoller:
    return LinuxEvdevKeyboardPoller(input_dir=str(input_dir), clock=clock)


def _codes(*names: str) -> set[int]:
    return {KEY_CODES[name] for name in names}


# --------------------------------------------------------------------------- #
# Availability
# --------------------------------------------------------------------------- #


def test_unavailable_when_evdev_does_not_import(input_dir: Path, clock: _Clock) -> None:
    poller = _poller(input_dir, clock)
    assert poller.is_available() is False
    assert poller.is_key_pressed("w") is False
    assert poller.device_count() == 0


def test_unavailable_when_the_input_directory_is_missing(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock
) -> None:
    poller = _poller(input_dir / "absent", clock)
    assert poller.is_available() is False
    assert fake_evdev.opened == []


def test_unavailable_when_an_input_node_cannot_be_opened(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_evdev.add("event0")
    fake_evdev.add("event1")
    denied = str(input_dir / "event1")
    real_access = os.access
    monkeypatch.setattr(os, "access", lambda path, mode, **kw: path != denied and real_access(path, mode, **kw))
    assert _poller(input_dir, clock).is_available() is False


def test_entries_other_than_event_nodes_do_not_decide_availability(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_evdev.add("event0")
    (input_dir / "mouse0").touch()
    denied = str(input_dir / "mouse0")
    real_access = os.access
    monkeypatch.setattr(os, "access", lambda path, mode, **kw: path != denied and real_access(path, mode, **kw))
    assert _poller(input_dir, clock).is_available() is True


def test_available_with_no_keyboard_attached(fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock) -> None:
    poller = _poller(input_dir, clock)
    assert poller.is_available() is True
    assert poller.device_count() == 0
    assert poller.is_key_pressed("w") is False


def test_startup_log_names_the_poller_and_the_keyboard_count(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock, caplog: pytest.LogCaptureFixture
) -> None:
    fake_evdev.add("event0")
    fake_evdev.add("event1")
    with caplog.at_level(logging.INFO, logger=keyboard_module.__name__):
        _poller(input_dir, clock)
    assert "evdev keyboard poller initialized (2 keyboard(s))" in caplog.text


# --------------------------------------------------------------------------- #
# Key state
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("key", POLLED_KEYS)
def test_each_polled_key_reads_its_evdev_keys(key: str, fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock) -> None:
    node = fake_evdev.add("event0")
    poller = _poller(input_dir, clock)
    for name in EXPECTED_KEYS[key]:
        node.held = _codes(name)
        clock.t += 1.0
        assert poller.is_key_pressed(key) is True, name
    node.held = set()
    clock.t += 1.0
    assert poller.is_key_pressed(key) is False


def test_the_key_table_covers_exactly_the_polled_keys() -> None:
    assert set(EXPECTED_KEYS) == set(POLLED_KEYS)


@pytest.mark.parametrize(
    ("key", "right_side"),
    [
        ("Shift", "KEY_RIGHTSHIFT"),
        ("Control", "KEY_RIGHTCTRL"),
        ("Alt", "KEY_RIGHTALT"),
        ("Meta", "KEY_RIGHTMETA"),
    ],
)
def test_a_modifier_reads_its_right_hand_key_alone(
    key: str, right_side: str, fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock
) -> None:
    fake_evdev.add("event0", held=_codes(right_side))
    assert _poller(input_dir, clock).is_key_pressed(key) is True


def test_an_unknown_key_is_never_pressed(fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock) -> None:
    fake_evdev.add("event0", held=set(KEYBOARD_KEYS))
    assert _poller(input_dir, clock).is_key_pressed("F13") is False


def test_keys_held_on_different_keyboards_are_one_state(fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock) -> None:
    fake_evdev.add("event0", held=_codes("KEY_W"))
    fake_evdev.add("event1", held=_codes("KEY_LEFTSHIFT"))
    poller = _poller(input_dir, clock)
    assert poller.get_pressed_keys(["w", "Shift", "a"]) == {"w", "Shift"}


def test_one_frame_shares_one_read_of_the_devices(fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock) -> None:
    node = fake_evdev.add("event0", held=_codes("KEY_W"))
    poller = _poller(input_dir, clock)
    assert poller.is_key_pressed("w") is True
    node.held = set()
    clock.t += 0.003
    assert poller.is_key_pressed("w") is True
    clock.t += 0.001
    assert poller.is_key_pressed("w") is False


def test_an_unplugged_keyboard_is_dropped_and_the_others_keep_their_keys(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock
) -> None:
    gone = fake_evdev.add("event0", held=_codes("KEY_W"))
    fake_evdev.add("event1", held=_codes("KEY_A"))
    poller = _poller(input_dir, clock)
    gone.unplugged = True
    assert poller.get_pressed_keys(["w", "a"]) == {"a"}
    assert gone.closed == 1
    assert poller.device_count() == 1


# --------------------------------------------------------------------------- #
# Device scan
# --------------------------------------------------------------------------- #


def test_a_keyboard_plugged_in_later_is_read_after_the_rescan(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock
) -> None:
    poller = _poller(input_dir, clock)
    fake_evdev.add("event0", held=_codes("KEY_W"))
    clock.t += 0.5
    assert poller.is_key_pressed("w") is False
    clock.t += 0.5
    assert poller.is_key_pressed("w") is True
    assert poller.device_count() == 1


def test_a_device_without_letter_keys_is_not_read(fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock) -> None:
    mouse = fake_evdev.add("event0", keys=MOUSE_KEYS, held={BTN_LEFT})
    poller = _poller(input_dir, clock)
    assert poller.device_count() == 0
    assert mouse.closed == 1


def test_a_device_that_is_not_a_keyboard_is_opened_once(fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock) -> None:
    fake_evdev.add("event0", keys=MOUSE_KEYS)
    poller = _poller(input_dir, clock)
    for _ in range(3):
        clock.t += 1.0
        poller.device_count()
    assert fake_evdev.opened == [str(input_dir / "event0")]


def test_a_keyboard_on_the_node_a_mouse_left_is_picked_up(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock
) -> None:
    fake_evdev.add("event0", keys=MOUSE_KEYS)
    poller = _poller(input_dir, clock)
    fake_evdev.replace("event0", KEYBOARD_KEYS)
    clock.t += 1.0
    assert poller.device_count() == 1


def test_a_node_that_leaves_and_returns_is_opened_again(fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock) -> None:
    path = str(input_dir / "event0")
    mouse = fake_evdev.add("event0", keys=MOUSE_KEYS)
    poller = _poller(input_dir, clock)
    del fake_evdev.nodes[path]
    clock.t += 1.0
    poller.device_count()
    fake_evdev.nodes[path] = mouse
    clock.t += 1.0
    poller.device_count()
    assert fake_evdev.opened == [path, path]


def test_a_keyboard_whose_node_is_gone_is_closed_at_the_rescan(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock
) -> None:
    node = fake_evdev.add("event0")
    poller = _poller(input_dir, clock)
    del fake_evdev.nodes[str(input_dir / "event0")]
    clock.t += 1.0
    assert poller.device_count() == 0
    assert node.closed == 1


def test_a_failed_listing_keeps_the_keyboards_already_open(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock
) -> None:
    fake_evdev.add("event0", held=_codes("KEY_W"))
    poller = _poller(input_dir, clock)
    fake_evdev.list_error = True
    clock.t += 1.0
    assert poller.is_key_pressed("w") is True


def test_a_listed_node_that_vanished_before_its_stat_is_skipped(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock
) -> None:
    fake_evdev.nodes[str(input_dir / "event9")] = _Node(keys=KEYBOARD_KEYS)
    poller = _poller(input_dir, clock)
    assert poller.device_count() == 0
    assert fake_evdev.opened == []


def test_a_node_that_fails_to_open_is_tried_again(fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock) -> None:
    node = fake_evdev.add("event0", open_error=True)
    poller = _poller(input_dir, clock)
    assert poller.device_count() == 0
    node.open_error = False
    clock.t += 1.0
    assert poller.device_count() == 1


def test_a_node_whose_capabilities_fail_is_closed_and_tried_again(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock
) -> None:
    node = fake_evdev.add("event0", caps_error=True)
    poller = _poller(input_dir, clock)
    assert (poller.device_count(), node.closed) == (0, 1)
    node.caps_error = False
    clock.t += 1.0
    assert poller.device_count() == 1


def test_a_close_that_fails_still_drops_the_keyboard(fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock) -> None:
    node = fake_evdev.add("event0", close_error=True)
    poller = _poller(input_dir, clock)
    node.unplugged = True
    assert poller.is_key_pressed("w") is False
    assert poller.device_count() == 0


# --------------------------------------------------------------------------- #
# Factory and handler
# --------------------------------------------------------------------------- #


def _install_poller(monkeypatch: pytest.MonkeyPatch, input_dir: Path, clock: _Clock) -> None:
    """Linux, with the poller the factory builds reading the fake nodes on the test's clock."""

    class _Poller(LinuxEvdevKeyboardPoller):
        def __init__(self) -> None:
            super().__init__(input_dir=str(input_dir), clock=clock)

    monkeypatch.setattr(keyboard_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(keyboard_module, "LinuxEvdevKeyboardPoller", _Poller)


def test_factory_picks_evdev_on_linux_when_usable(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_poller(monkeypatch, input_dir, clock)
    assert isinstance(create_keyboard_poller(), LinuxEvdevKeyboardPoller)


def test_factory_falls_back_on_linux_without_evdev(
    input_dir: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_poller(monkeypatch, input_dir, clock)
    assert isinstance(create_keyboard_poller(), FallbackKeyboardPoller)


class _App:
    def __init__(self) -> None:
        self._config = AppConfig()


def test_handler_on_evdev_reports_the_poller_and_its_keyboards(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_evdev.add("event0")
    _install_poller(monkeypatch, input_dir, clock)
    assert KeyboardHandler(_App()).status() == {"poller": "evdev", "devices": 1}


def test_handler_on_the_fallback_reports_no_device_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keyboard_module.platform, "system", lambda: "FreeBSD")
    assert KeyboardHandler(_App()).status() == {"poller": "fallback", "devices": None}


def test_handler_on_evdev_does_not_warn_about_the_fallback(
    fake_evdev: _FakeEvdev,
    input_dir: Path,
    clock: _Clock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _install_poller(monkeypatch, input_dir, clock)
    with caplog.at_level(logging.WARNING, logger=keyboard_module.__name__):
        KeyboardHandler(_App())
    assert "fallback" not in caplog.text


def test_handler_on_evdev_ignores_gtk_key_events(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_evdev.add("event0")
    _install_poller(monkeypatch, input_dir, clock)
    handler = KeyboardHandler(_App())
    handler.on_key_down({"key": "w"})
    assert "w" not in handler.keys


def test_keyboard_presence_follows_the_evdev_device_list(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_poller(monkeypatch, input_dir, clock)
    # The procfs probe belongs to the fallback; it must not answer for evdev.
    monkeypatch.setattr(KeyboardHandler, "_probe_linux_keyboard_connected", staticmethod(lambda: True))
    now = {"t": 0.0}
    monkeypatch.setattr(keyboard_module.time, "monotonic", lambda: now["t"])
    handler = KeyboardHandler(_App())
    assert handler.is_connected() is False
    fake_evdev.add("event0")
    clock.t += 1.0
    now["t"] += 1.0
    assert handler.is_connected() is True
