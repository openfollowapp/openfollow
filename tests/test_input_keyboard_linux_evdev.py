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
from openfollow import keyboard_layout
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
    # What opening the node or reading its capabilities raises besides OSError.
    open_raises: Exception | None = None
    caps_raises: Exception | None = None
    closed: int = 0


# A name python-evdev decodes as strict UTF-8.
_BAD_NAME = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")


def _sysfs_bitmap(keys: list[int]) -> str:
    """The kernel's ``capabilities/key`` text: hex longs, most significant first."""
    mask = sum(1 << key for key in set(keys))
    words = []
    while mask:
        words.append(f"{mask & (1 << 64) - 1:x}")
        mask >>= 64
    return " ".join(reversed(words)) or "0"


class _FakeDevice:
    def __init__(self, node: _Node) -> None:
        self._node = node

    def capabilities(self) -> dict[int, list[int]]:
        if self._node.caps_error:
            raise OSError("capabilities")
        if self._node.caps_raises is not None:
            raise self._node.caps_raises
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
    """The slice of python-evdev the poller uses, over real files standing in for the nodes and sysfs."""

    def __init__(self, input_dir: Path, sysfs_dir: Path) -> None:
        super().__init__("evdev")
        self.input_dir = input_dir
        self.sysfs_dir = sysfs_dir
        self.ecodes = SimpleNamespace(EV_KEY=EV_KEY, **KEY_CODES)
        self.nodes: dict[str, _Node] = {}
        self.opened: list[str] = []
        self.list_error = False

    def add(self, name: str, keys: list[int] | None = None, *, sysfs: bool = True, **flags: Any) -> _Node:
        """A node; with ``sysfs=False`` sysfs cannot classify it and the poller must open it to tell."""
        path = self.input_dir / name
        path.touch()
        node = _Node(keys=KEYBOARD_KEYS if keys is None else keys, **flags)
        self.nodes[str(path)] = node
        if sysfs:
            self.write_sysfs(name, node.keys)
        return node

    def write_sysfs(self, name: str, keys: list[int]) -> None:
        caps = self.sysfs_dir / name / "device" / "capabilities"
        caps.mkdir(parents=True, exist_ok=True)
        (caps / "key").write_text(_sysfs_bitmap(keys) + "\n")

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
        if node.open_raises is not None:
            raise node.open_raises
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
def sysfs_dir(tmp_path: Path) -> Path:
    path = tmp_path / "sysfs"
    path.mkdir()
    return path


@pytest.fixture
def fake_evdev(monkeypatch: pytest.MonkeyPatch, input_dir: Path, sysfs_dir: Path) -> _FakeEvdev:
    fake = _FakeEvdev(input_dir, sysfs_dir)
    monkeypatch.setitem(sys.modules, "evdev", fake)
    return fake


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


def _sysfs_of(input_dir: Path) -> str:
    """The fake sysfs beside ``input_dir`` (the ``sysfs_dir`` fixture)."""
    return str(input_dir.parent / "sysfs")


def _poller(input_dir: Path, clock: _Clock) -> LinuxEvdevKeyboardPoller:
    return LinuxEvdevKeyboardPoller(input_dir=str(input_dir), clock=clock, sysfs_dir=_sysfs_of(input_dir))


def _deny(monkeypatch: pytest.MonkeyPatch, path: Path, mode: int = os.R_OK | os.W_OK) -> None:
    """``path`` grants no access that needs any bit of ``mode``."""
    real_access = os.access
    monkeypatch.setattr(os, "access", lambda p, m, **kw: not (p == str(path) and m & mode) and real_access(p, m, **kw))


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


def test_unavailable_when_a_keyboard_node_cannot_be_opened(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_evdev.add("event0")
    fake_evdev.add("event1")
    _deny(monkeypatch, input_dir / "event1")
    assert _poller(input_dir, clock).is_available() is False


def test_unavailable_when_a_keyboard_node_is_readable_but_not_writable(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # evdev.list_devices lists only nodes open both ways, so a read-only keyboard would never be read.
    fake_evdev.add("event0")
    _deny(monkeypatch, input_dir / "event0", mode=os.W_OK)
    assert _poller(input_dir, clock).is_available() is False


def test_unavailable_when_a_node_sysfs_cannot_classify_is_closed(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_evdev.add("event0", keys=MOUSE_KEYS, sysfs=False)
    _deny(monkeypatch, input_dir / "event0")
    assert _poller(input_dir, clock).is_available() is False


def test_a_closed_node_that_is_not_a_keyboard_keeps_evdev(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_evdev.add("event0")
    fake_evdev.add("event1", keys=MOUSE_KEYS)
    _deny(monkeypatch, input_dir / "event1")
    poller = _poller(input_dir, clock)
    assert (poller.is_available(), poller.device_count()) == (True, 1)


@pytest.mark.parametrize("text", ["", "zz\n", "\xff"])
def test_an_unreadable_sysfs_bitmap_does_not_classify_the_node(
    text: str, fake_evdev: _FakeEvdev, input_dir: Path, sysfs_dir: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_evdev.add("event0", keys=MOUSE_KEYS)
    (sysfs_dir / "event0" / "device" / "capabilities" / "key").write_text(text, encoding="latin-1")
    _deny(monkeypatch, input_dir / "event0")
    # An empty bitmap lists no key; garbage tells nothing, so the closed node may be a keyboard.
    assert _poller(input_dir, clock).is_available() is (text == "")


def test_entries_other_than_event_nodes_do_not_decide_availability(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_evdev.add("event0")
    (input_dir / "mouse0").touch()
    _deny(monkeypatch, input_dir / "mouse0")
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
    clock.t += 0.002
    assert poller.is_key_pressed("w") is True
    clock.t += 0.003
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


def test_a_device_sysfs_lists_without_letter_keys_is_never_opened(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock
) -> None:
    fake_evdev.add("event0", keys=MOUSE_KEYS, held={BTN_LEFT})
    poller = _poller(input_dir, clock)
    clock.t += 1.0
    assert poller.device_count() == 0
    assert fake_evdev.opened == []


def test_the_kernels_word_decides_not_the_sysfs_hint(fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock) -> None:
    mouse = fake_evdev.add("event0", keys=MOUSE_KEYS)
    fake_evdev.write_sysfs("event0", KEYBOARD_KEYS)
    poller = _poller(input_dir, clock)
    assert poller.device_count() == 0
    assert mouse.closed == 1


def test_a_device_without_letter_keys_is_not_read(fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock) -> None:
    mouse = fake_evdev.add("event0", keys=MOUSE_KEYS, held={BTN_LEFT}, sysfs=False)
    poller = _poller(input_dir, clock)
    assert poller.device_count() == 0
    assert mouse.closed == 1


def test_a_device_that_is_not_a_keyboard_is_opened_once(fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock) -> None:
    fake_evdev.add("event0", keys=MOUSE_KEYS, sysfs=False)
    poller = _poller(input_dir, clock)
    for _ in range(3):
        clock.t += 1.0
        poller.device_count()
    assert fake_evdev.opened == [str(input_dir / "event0")]


def test_a_keyboard_on_the_node_a_mouse_left_is_picked_up(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock
) -> None:
    fake_evdev.add("event0", keys=MOUSE_KEYS, sysfs=False)
    poller = _poller(input_dir, clock)
    fake_evdev.replace("event0", KEYBOARD_KEYS)
    clock.t += 1.0
    assert poller.device_count() == 1


def test_a_node_that_leaves_and_returns_is_opened_again(fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock) -> None:
    path = str(input_dir / "event0")
    mouse = fake_evdev.add("event0", keys=MOUSE_KEYS, sysfs=False)
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


def test_a_device_evdev_cannot_decode_is_skipped_once_and_the_rest_are_opened(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock, caplog: pytest.LogCaptureFixture
) -> None:
    fake_evdev.add("event0", open_raises=_BAD_NAME)
    fake_evdev.add("event1", held=_codes("KEY_W"))
    with caplog.at_level(logging.WARNING, logger=keyboard_module.__name__):
        poller = _poller(input_dir, clock)
        for _ in range(3):
            clock.t += 1.0
            assert poller.is_key_pressed("w") is True
    assert poller.device_count() == 1
    assert fake_evdev.opened.count(str(input_dir / "event0")) == 1
    assert caplog.text.count("evdev keyboard poller skips") == 1


def test_a_device_plugged_in_later_that_evdev_cannot_decode_does_not_stop_the_frame(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock
) -> None:
    fake_evdev.add("event1", held=_codes("KEY_W"))
    poller = _poller(input_dir, clock)
    fake_evdev.add("event0", open_raises=_BAD_NAME)
    clock.t += 1.0
    assert poller.is_key_pressed("w") is True


def test_a_device_whose_capabilities_raise_is_closed_and_skipped_once(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock
) -> None:
    node = fake_evdev.add("event0", caps_raises=ValueError("bad capability table"))
    poller = _poller(input_dir, clock)
    clock.t += 1.0
    assert poller.device_count() == 0
    assert node.closed == 1
    assert fake_evdev.opened == [str(input_dir / "event0")]


# --------------------------------------------------------------------------- #
# Factory and handler
# --------------------------------------------------------------------------- #


def _install_poller(monkeypatch: pytest.MonkeyPatch, input_dir: Path, clock: _Clock) -> None:
    """Linux, with the poller the factory builds reading the fake nodes on the test's clock."""

    class _Poller(LinuxEvdevKeyboardPoller):
        def __init__(self) -> None:
            super().__init__(input_dir=str(input_dir), clock=clock, sysfs_dir=_sysfs_of(input_dir))

    monkeypatch.setattr(keyboard_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(keyboard_module, "LinuxEvdevKeyboardPoller", _Poller)


@pytest.fixture
def host_sysfs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A host /sys where every event node is a mouse, set as the poller's default."""
    root = tmp_path / "host-sysfs"
    for n in range(4):
        caps = root / f"event{n}" / "device" / "capabilities"
        caps.mkdir(parents=True)
        (caps / "key").write_text(_sysfs_bitmap(MOUSE_KEYS) + "\n")
    monkeypatch.setattr(keyboard_module, "_SYSFS_INPUT_DIR", str(root))
    return root


def test_a_poller_without_a_sysfs_root_reads_the_default(
    fake_evdev: _FakeEvdev, host_sysfs: Path, input_dir: Path, clock: _Clock
) -> None:
    fake_evdev.add("event0")
    poller = LinuxEvdevKeyboardPoller(input_dir=str(input_dir), clock=clock)
    assert poller.device_count() == 0
    assert fake_evdev.opened == []


def test_the_factory_path_poller_reads_the_tests_sysfs_not_the_hosts(
    fake_evdev: _FakeEvdev, host_sysfs: Path, input_dir: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_evdev.add("event0")
    _install_poller(monkeypatch, input_dir, clock)
    assert KeyboardHandler(_App()).status() == {"poller": "evdev", "devices": 1}


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


def test_factory_falls_back_when_the_evdev_poller_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _broken() -> LinuxEvdevKeyboardPoller:
        raise RuntimeError("evdev blew up")

    monkeypatch.setattr(keyboard_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(keyboard_module, "LinuxEvdevKeyboardPoller", _broken)
    assert isinstance(create_keyboard_poller(), FallbackKeyboardPoller)


def test_handler_starts_beside_a_device_evdev_cannot_decode(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_evdev.add("event0", open_raises=_BAD_NAME)
    fake_evdev.add("event1")
    _install_poller(monkeypatch, input_dir, clock)
    assert KeyboardHandler(_App()).status() == {"poller": "evdev", "devices": 1}


class _App:
    def __init__(self) -> None:
        self._config = AppConfig()


def test_handler_on_evdev_reports_the_poller_and_its_keyboards(
    fake_evdev: _FakeEvdev, input_dir: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_evdev.add("event0")
    _install_poller(monkeypatch, input_dir, clock)
    assert KeyboardHandler(_App()).status() == {"poller": "evdev", "devices": 1}


def test_handler_on_quartz_reports_the_quartz_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    class _NoQuartz:
        def LoadLibrary(self, name: str) -> None:  # noqa: N802 - ctypes' name
            raise OSError(name)

    monkeypatch.setattr(keyboard_module.ctypes, "cdll", _NoQuartz())
    monkeypatch.setattr(keyboard_module, "create_keyboard_poller", keyboard_module.MacOSKeyboardPoller)
    assert KeyboardHandler(_App()).status() == {"poller": "quartz", "devices": None}


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


# --------------------------------------------------------------------------- #
# Layout-aware letters (xkbcommon)
# --------------------------------------------------------------------------- #

_LETTER_CODES = {letter: KEY_CODES[f"KEY_{letter.upper()}"] for letter in "abcdefghijklmnopqrstuvwxyz"}


def _keymap(letters: dict[str, int]) -> dict[int, list[int]]:
    """xkb keycode -> level-0 keysyms; a Latin-1 keysym is its character's code point."""
    return {code + 8: [ord(letter)] for letter, code in letters.items()}


_US = _keymap(_LETTER_CODES)
# QWERTZ: the keys US prints Y and Z on swap letters.
_DE = _keymap({**_LETTER_CODES, "y": KEY_CODES["KEY_Z"], "z": KEY_CODES["KEY_Y"]})


class _FakeKeymap:
    def __init__(self, levels: dict[int, list[int]]) -> None:
        self._levels = levels

    def __iter__(self) -> Any:
        return iter(sorted(self._levels))

    def key_get_syms_by_level(self, key: int, layout: int, level: int) -> list[int]:
        assert (layout, level) == (0, 0)
        return list(self._levels[key])


class _FakeXkbModule(types.ModuleType):
    """The slice of python-xkbcommon's ``xkb`` module the layout map uses."""

    def __init__(self, keymaps: dict[str, dict[int, list[int]]]) -> None:
        super().__init__("xkbcommon.xkb")
        self.keymaps = keymaps
        self.compiled: list[dict[str, Any]] = []
        self.contexts: list[bool] = []
        fake = self

        class Context:
            def __init__(self, no_environment_names: bool = False) -> None:
                fake.contexts.append(no_environment_names)

            def keymap_new_from_names(self, **names: Any) -> _FakeKeymap:
                fake.compiled.append(names)
                layout = names.get("layout") or "us"
                if layout not in fake.keymaps:
                    raise RuntimeError("xkb_keymap_new_from_names returned NULL")
                return _FakeKeymap(fake.keymaps[layout])

        self.Context = Context

    @staticmethod
    def keysym_to_string(keysym: int) -> str | None:
        return chr(keysym) if keysym else None


@pytest.fixture
def fake_xkb(monkeypatch: pytest.MonkeyPatch) -> _FakeXkbModule:
    xkb = _FakeXkbModule({"us": _US, "de": _DE})
    package = types.ModuleType("xkbcommon")
    package.xkb = xkb  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "xkbcommon", package)
    monkeypatch.setitem(sys.modules, "xkbcommon.xkb", xkb)
    return xkb


@pytest.fixture
def system_keyboard(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "keyboard"
    monkeypatch.setattr(keyboard_layout, "SYSTEM_KEYBOARD", path)
    return path


def _letter_keys(poller: LinuxEvdevKeyboardPoller, node: _Node, clock: _Clock, code: int) -> set[str]:
    """The letters the poller reports while only the key with evdev ``code`` is held."""
    node.held = {code}
    clock.t += 1.0
    return poller.get_pressed_keys(list("abcdefghijklmnopqrstuvwxyz"))


def test_a_qwertz_session_polls_z_and_y_on_their_printed_keys(
    fake_evdev: _FakeEvdev, fake_xkb: _FakeXkbModule, input_dir: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XKB_DEFAULT_LAYOUT", "de")
    node = fake_evdev.add("event0")
    poller = _poller(input_dir, clock)
    assert _letter_keys(poller, node, clock, KEY_CODES["KEY_Y"]) == {"z"}
    assert _letter_keys(poller, node, clock, KEY_CODES["KEY_Z"]) == {"y"}
    assert _letter_keys(poller, node, clock, KEY_CODES["KEY_W"]) == {"w"}


def test_the_layout_never_moves_a_key_that_is_not_a_letter(
    fake_evdev: _FakeEvdev, fake_xkb: _FakeXkbModule, input_dir: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The space bar types " " and the digit row's first key "1": neither is a letter.
    fake_xkb.keymaps["de"] = {**_DE, KEY_CODES["KEY_SPACE"] + 8: [ord(" ")], 2 + 8: [ord("1")]}
    monkeypatch.setenv("XKB_DEFAULT_LAYOUT", "de")
    node = fake_evdev.add("event0")
    poller = _poller(input_dir, clock)
    node.held = _codes("KEY_SPACE")
    assert poller.get_pressed_keys(["Space", "a"]) == {"Space"}


def test_a_letter_on_several_keys_stays_on_the_lowest(
    fake_evdev: _FakeEvdev, fake_xkb: _FakeXkbModule, input_dir: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_xkb.keymaps["de"] = {**_DE, 300: [ord("y")]}
    monkeypatch.setenv("XKB_DEFAULT_LAYOUT", "de")
    node = fake_evdev.add("event0")
    poller = _poller(input_dir, clock)
    assert _letter_keys(poller, node, clock, KEY_CODES["KEY_Z"]) == {"y"}
    assert _letter_keys(poller, node, clock, 300 - 8) == set()


# The lowest xkb keycode (Escape), so a letter it named would win over the letter's own key.
_ESC_XKB = KEY_CODES["KEY_ESC"] + 8


@pytest.mark.parametrize(
    ("syms", "why"),
    [
        ([ord("y"), ord("z")], "a key with several level-0 keysyms names none"),
        ([], "a key with no keysym names none"),
        ([0], "a keysym with no text names none"),
    ],
)
def test_a_key_without_one_keysym_names_no_letter(
    syms: list[int],
    why: str,
    fake_evdev: _FakeEvdev,
    fake_xkb: _FakeXkbModule,
    input_dir: Path,
    clock: _Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_xkb.keymaps["de"] = {**_DE, _ESC_XKB: syms}
    monkeypatch.setenv("XKB_DEFAULT_LAYOUT", "de")
    node = fake_evdev.add("event0")
    poller = _poller(input_dir, clock)
    assert _letter_keys(poller, node, clock, KEY_CODES["KEY_ESC"]) == set(), why
    assert _letter_keys(poller, node, clock, KEY_CODES["KEY_Z"]) == {"y"}, why


def test_the_session_environment_is_the_whole_rmlvo(
    fake_evdev: _FakeEvdev,
    fake_xkb: _FakeXkbModule,
    input_dir: Path,
    clock: _Clock,
    monkeypatch: pytest.MonkeyPatch,
    system_keyboard: Path,
) -> None:
    system_keyboard.write_text('XKBMODEL="pc86"\nXKBLAYOUT="us"\nXKBVARIANT="intl"\nXKBOPTIONS="ctrl:nocaps"\n')
    monkeypatch.setenv("XKB_DEFAULT_LAYOUT", "de")
    monkeypatch.setenv("XKB_DEFAULT_VARIANT", "nodeadkeys")
    monkeypatch.setenv("XKB_DEFAULT_MODEL", "pc105")
    monkeypatch.setenv("XKB_DEFAULT_OPTIONS", "grp:alt_shift_toggle")
    _poller(input_dir, clock)
    assert fake_xkb.compiled == [
        {"layout": "de", "variant": "nodeadkeys", "model": "pc105", "options": "grp:alt_shift_toggle"}
    ]
    # The names come from the poller alone, never from libxkbcommon reading the environment itself.
    assert fake_xkb.contexts == [True]


def test_a_session_naming_only_the_layout_leaves_the_rest_to_xkb(
    fake_evdev: _FakeEvdev,
    fake_xkb: _FakeXkbModule,
    input_dir: Path,
    clock: _Clock,
    monkeypatch: pytest.MonkeyPatch,
    system_keyboard: Path,
) -> None:
    system_keyboard.write_text('XKBMODEL="pc86"\nXKBLAYOUT="de"\nXKBVARIANT="nodeadkeys"\n')
    monkeypatch.setenv("XKB_DEFAULT_LAYOUT", "us")
    _poller(input_dir, clock)
    assert fake_xkb.compiled == [{"layout": "us", "variant": None, "model": None, "options": None}]


def test_a_session_exporting_anything_keeps_the_system_file_out(
    fake_evdev: _FakeEvdev,
    fake_xkb: _FakeXkbModule,
    input_dir: Path,
    clock: _Clock,
    monkeypatch: pytest.MonkeyPatch,
    system_keyboard: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    system_keyboard.write_text('XKBLAYOUT="de"\n')
    monkeypatch.setenv("XKB_DEFAULT_OPTIONS", "ctrl:nocaps")
    with caplog.at_level(logging.INFO, logger=keyboard_module.__name__):
        _poller(input_dir, clock)
    assert fake_xkb.compiled == [{"layout": None, "variant": None, "model": None, "options": "ctrl:nocaps"}]
    assert "Keyboard layout (xkb default) (from session)" in caplog.text


def test_without_a_session_layout_the_system_file_decides(
    fake_evdev: _FakeEvdev,
    fake_xkb: _FakeXkbModule,
    input_dir: Path,
    clock: _Clock,
    system_keyboard: Path,
) -> None:
    system_keyboard.write_text('XKBMODEL="pc105"\nXKBLAYOUT="de"\nXKBVARIANT="nodeadkeys"\nXKBOPTIONS=""\n')
    node = fake_evdev.add("event0")
    poller = _poller(input_dir, clock)
    assert fake_xkb.compiled == [{"layout": "de", "variant": "nodeadkeys", "model": "pc105", "options": None}]
    assert _letter_keys(poller, node, clock, KEY_CODES["KEY_Y"]) == {"z"}


def test_a_system_file_without_a_layout_falls_to_us_and_keeps_its_model(
    fake_evdev: _FakeEvdev, fake_xkb: _FakeXkbModule, input_dir: Path, clock: _Clock, system_keyboard: Path
) -> None:
    system_keyboard.write_text('XKBMODEL="pc86"\nXKBVARIANT="nodeadkeys"\n')
    _poller(input_dir, clock)
    assert fake_xkb.compiled == [{"layout": "us", "variant": None, "model": "pc86", "options": None}]


def test_with_neither_session_nor_system_file_the_layout_is_us(
    fake_evdev: _FakeEvdev, fake_xkb: _FakeXkbModule, input_dir: Path, clock: _Clock, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger=keyboard_module.__name__):
        _poller(input_dir, clock)
    assert fake_xkb.compiled == [{"layout": "us", "variant": None, "model": None, "options": None}]
    assert "Keyboard layout us (from default)" in caplog.text


def test_the_layout_log_names_the_layout_its_variant_and_its_source(
    fake_evdev: _FakeEvdev,
    fake_xkb: _FakeXkbModule,
    input_dir: Path,
    clock: _Clock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("XKB_DEFAULT_LAYOUT", "de")
    monkeypatch.setenv("XKB_DEFAULT_VARIANT", "nodeadkeys")
    with caplog.at_level(logging.INFO, logger=keyboard_module.__name__):
        _poller(input_dir, clock)
    assert "Keyboard layout de (nodeadkeys) (from session); using layout-aware keycodes" in caplog.text


def _on_us_positions(poller: LinuxEvdevKeyboardPoller, node: _Node, clock: _Clock) -> bool:
    y_key = _letter_keys(poller, node, clock, KEY_CODES["KEY_Y"])
    z_key = _letter_keys(poller, node, clock, KEY_CODES["KEY_Z"])
    return (y_key, z_key) == ({"y"}, {"z"})


def test_without_xkbcommon_the_letters_stay_on_us_positions(
    fake_evdev: _FakeEvdev,
    input_dir: Path,
    clock: _Clock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("XKB_DEFAULT_LAYOUT", "de")
    node = fake_evdev.add("event0")
    with caplog.at_level(logging.WARNING, logger=keyboard_module.__name__):
        poller = _poller(input_dir, clock)
    assert _on_us_positions(poller, node, clock)
    assert "Could not resolve keyboard layout de (from session); falling back to US QWERTY" in caplog.text


def test_a_keymap_that_does_not_compile_keeps_us_positions(
    fake_evdev: _FakeEvdev, fake_xkb: _FakeXkbModule, input_dir: Path, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XKB_DEFAULT_LAYOUT", "xx")
    node = fake_evdev.add("event0")
    assert _on_us_positions(_poller(input_dir, clock), node, clock)


@pytest.mark.parametrize("text", ["1", "Y", "ä"])
def test_a_keymap_without_polled_letters_keeps_us_positions(
    text: str,
    fake_evdev: _FakeEvdev,
    fake_xkb: _FakeXkbModule,
    input_dir: Path,
    clock: _Clock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_xkb.keymaps["xx"] = {KEY_CODES["KEY_Z"] + 8: [ord(text)]}
    monkeypatch.setenv("XKB_DEFAULT_LAYOUT", "xx")
    node = fake_evdev.add("event0")
    with caplog.at_level(logging.WARNING, logger=keyboard_module.__name__):
        poller = _poller(input_dir, clock)
    assert _on_us_positions(poller, node, clock)
    assert "Could not resolve keyboard layout xx" in caplog.text


# --------------------------------------------------------------------------- #
# The real python-xkbcommon (Linux CI and the Pi gate)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("layout", "z_key", "y_key"), [("de", "KEY_Y", "KEY_Z"), ("us", "KEY_Z", "KEY_Y")])
def test_the_real_xkbcommon_places_z_and_y(
    layout: str, z_key: str, y_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins the binding's API the fakes assume; compiles a keymap from the host's xkb-data."""
    monkeypatch.delitem(sys.modules, "xkbcommon")
    try:
        import xkbcommon.xkb  # noqa: F401
    except ImportError:
        pytest.skip("python-xkbcommon is not installed (Linux only)")
    layout_map = keyboard_module._build_linux_layout_keycode_map(
        {"layout": layout, "variant": "", "model": "", "options": ""}
    )
    assert layout_map is not None
    assert (layout_map["z"], layout_map["y"]) == (KEY_CODES[z_key], KEY_CODES[y_key])
