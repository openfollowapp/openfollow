# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Shared hermetic Gtk / GLib fakes for services-lifecycle tests.

``AppRuntimeServices.init_canvas`` and ``init_video`` touch two kinds
of objects tests must keep hermetic:

* The canvas – a :class:`openfollow.window.GtkNativeSinkWindow`, which
  internally imports ``gi.repository`` and builds real GTK 3 widgets.
* The video receiver (:class:`openfollow.video.receiver.GstNativeSinkReceiver`),
  which reaches into GStreamer.

Tests that only need to drive ``init_video`` (the lifecycle surface
between ``AppRuntimeServices`` and the receiver / overlay / subsystems)
use :class:`FakeCanvas` to stand in for the GTK window. It records
``add_event_handler`` / ``embed_widget`` / ``attach_hud`` / ``set_title``
calls so tests can assert the wiring contract without spinning up a
real GTK 3 display.

Tests that need finer control over the Gtk / GLib module surface (e.g.
to exercise ``GtkNativeSinkWindow.__init__`` directly) use
:func:`install_fake_gi` + the module-level fakes, which mirror
``tests/test_window.py`` but live here so other test files can reuse
them without duplicating the ``sys.modules`` dance.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Callable
from typing import Any

# ---------------------------------------------------------------------------
# Canvas stand-in used by init_video tests
# ---------------------------------------------------------------------------


class FakeCanvas:
    """Recording stand-in for :class:`GtkNativeSinkWindow`.

    Implements the subset of the canvas API that ``AppRuntimeServices``
    reaches into during ``init_canvas`` / ``init_video`` / ``update_video``.
    Every call is captured so tests can assert on ordering (``handlers``
    keyed by event type, ``embed_widget_calls`` in order) without
    instantiating a real GTK widget tree.
    """

    def __init__(self, *, width: int = 1920, height: int = 1080) -> None:
        self._width = width
        self._height = height
        self.handlers: dict[str, list[Callable[..., Any]]] = {}
        self.embed_widget_calls: list[Any] = []
        self.hud_draw_fn: Callable[..., Any] | None = None
        self.title: str | None = None
        self.aspect_ratio: tuple[int, int] | None = None
        self.fullscreen_called: bool = False
        self.close_called: bool = False
        self.tick_callback: Callable[..., Any] | None = None
        self.tick_started: bool = False
        self.tick_stopped: bool = False

    # -- RenderCanvas-compatible API ----------------------------------------

    def add_event_handler(self, handler: Callable[..., Any], event_type: str) -> None:
        self.handlers.setdefault(event_type, []).append(handler)

    def request_draw(self, _fn: Callable[..., Any]) -> None:
        return None

    def close(self) -> None:
        self.close_called = True

    # -- Extended API (used by init_video / update_video) -------------------

    def embed_widget(self, widget: Any) -> None:
        self.embed_widget_calls.append(widget)

    def attach_hud(self, draw_fn: Callable[..., Any]) -> None:
        self.hud_draw_fn = draw_fn

    def set_title(self, title: str) -> None:
        self.title = title

    def set_aspect_ratio(self, w: int, h: int) -> None:
        self.aspect_ratio = (w, h)

    def fullscreen(self) -> None:
        self.fullscreen_called = True

    def get_canvas_size(self) -> tuple[int, int]:
        return (self._width, self._height)

    # -- Tick animation -----------------------------------------------------

    def start_tick_animation(self, callback: Callable[..., Any]) -> None:
        self.tick_callback = callback
        self.tick_started = True

    def stop_tick_animation(self) -> None:
        self.tick_stopped = True


# ---------------------------------------------------------------------------
# Gtk / GLib / Gdk module fakes for tests that need to construct
# GtkNativeSinkWindow directly. Modelled after tests/test_window.py – kept
# minimal: enough to walk __init__ without real GTK widgets.
# ---------------------------------------------------------------------------


class FakeGtkWidget:
    """Base recording widget for the Gtk stand-in."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.children: list[FakeGtkWidget] = []
        self.parent: FakeGtkWidget | None = None
        self.connects: list[tuple[str, Callable[..., Any]]] = []
        self.properties: dict[str, Any] = {}
        self.visible = False
        self.focus_on_click = True
        self.can_focus = True
        self.default_size: tuple[int, int] | None = None
        self.title: str | None = kwargs.get("title")
        self.tick_callbacks: list[Callable[..., Any]] = []
        self.geometry_hints: dict[str, Any] | None = None

    def add(self, child: FakeGtkWidget) -> None:
        child.parent = self
        self.children.append(child)

    def remove(self, child: FakeGtkWidget) -> None:
        child.parent = None
        if child in self.children:
            self.children.remove(child)

    def pack_start(self, child: FakeGtkWidget, _expand: bool, _fill: bool, _padding: int) -> None:
        self.add(child)

    def add_overlay(self, child: FakeGtkWidget) -> None:
        self.add(child)

    def set_overlay_pass_through(self, _child: FakeGtkWidget, _pass: bool) -> None:
        return None

    def get_child(self) -> FakeGtkWidget | None:
        return self.children[0] if self.children else None

    def get_parent(self) -> FakeGtkWidget | None:
        return self.parent

    def set_default_size(self, w: int, h: int) -> None:
        self.default_size = (w, h)

    def resize(self, w: int, h: int) -> None:
        self.properties["resized_to"] = (w, h)

    def connect(self, signal: str, callback: Callable[..., Any]) -> int:
        self.connects.append((signal, callback))
        return len(self.connects)

    def set_can_focus(self, value: bool) -> None:
        self.can_focus = value

    def set_focus_on_click(self, value: bool) -> None:
        self.focus_on_click = value

    def show_all(self) -> None:
        self.visible = True

    def show(self) -> None:
        self.visible = True

    def grab_focus(self) -> None:
        self.properties["focused"] = True

    def set_title(self, title: str) -> None:
        self.title = title

    def fullscreen(self) -> None:
        self.properties["fullscreen"] = True

    def queue_draw(self) -> None:
        self.properties["queued_draw"] = True

    def add_events(self, mask: int) -> None:
        self.properties.setdefault("events_mask", 0)
        self.properties["events_mask"] |= mask

    def get_allocation(self) -> types.SimpleNamespace:
        return types.SimpleNamespace(width=1920, height=1080)

    def get_allocated_width(self) -> int:
        return 1920

    def get_allocated_height(self) -> int:
        return 1080

    def get_size(self) -> tuple[int, int]:
        return (1920, 1080)

    def add_tick_callback(self, callback: Callable[..., Any], _user_data: Any) -> int:
        self.tick_callbacks.append(callback)
        return len(self.tick_callbacks)

    def remove_tick_callback(self, _cid: int) -> None:
        return None

    def set_geometry_hints(self, _win: Any, geometry: Any, mask: int) -> None:
        self.geometry_hints = {"geometry": geometry, "mask": mask}


class FakeGtk:
    """Module-level recording fake for ``gi.repository.Gtk``.

    Tests patch ``sys.modules["gi.repository"]`` to expose this as
    ``from gi.repository import Gtk``. Only the surface ``GtkNativeSinkWindow``
    and ``run_native_loop`` touch is modelled.
    """

    Window = FakeGtkWidget
    Box = FakeGtkWidget
    Overlay = FakeGtkWidget
    DrawingArea = FakeGtkWidget
    init_calls: list[Any] = []
    main_calls: int = 0
    main_quit_calls: int = 0

    class Orientation:
        VERTICAL = "vertical"
        HORIZONTAL = "horizontal"

    @classmethod
    def init(cls, _argv: Any) -> None:
        cls.init_calls.append(_argv)

    @classmethod
    def main(cls) -> None:
        cls.main_calls += 1

    @classmethod
    def main_quit(cls) -> None:
        cls.main_quit_calls += 1


class FakeGdkWindowHints:
    ASPECT = 1


class FakeGdk:
    """Module-level recording fake for ``gi.repository.Gdk``."""

    WindowHints = FakeGdkWindowHints

    class Geometry:
        def __init__(self) -> None:
            self.min_aspect: float = 0.0
            self.max_aspect: float = 0.0


class FakeGLib:
    """Module-level recording fake for ``gi.repository.GLib``."""

    timeout_add_calls: list[tuple[int, Callable[..., Any]]] = []
    main_loop_instances: list[FakeMainLoop] = []

    @classmethod
    def timeout_add(cls, ms: int, callback: Callable[..., Any]) -> int:
        cls.timeout_add_calls.append((ms, callback))
        return len(cls.timeout_add_calls)

    @classmethod
    def MainLoop(cls) -> FakeMainLoop:  # noqa: N802 – mirror gi naming
        loop = FakeMainLoop()
        cls.main_loop_instances.append(loop)
        return loop


class FakeMainLoop:
    def __init__(self) -> None:
        self.run_called = False
        self.quit_called = False

    def run(self) -> None:
        self.run_called = True

    def quit(self) -> None:
        self.quit_called = True


def install_fake_gi(monkeypatch) -> tuple[types.ModuleType, types.ModuleType]:
    """Replace ``gi`` and ``gi.repository`` in ``sys.modules`` with fakes.

    Returns the installed (gi, gi.repository) tuple so tests can reach
    into them if they need to. Counters on ``FakeGtk`` / ``FakeGLib`` are
    reset to zero here so each test starts from a clean baseline.

    ``monkeypatch`` auto-restores ``sys.modules`` at test teardown.
    """
    fake_gi = types.ModuleType("gi")
    fake_gi.require_version = lambda *_args, **_kw: None  # type: ignore[attr-defined]

    fake_repo = types.ModuleType("gi.repository")
    fake_repo.Gtk = FakeGtk  # type: ignore[attr-defined]
    fake_repo.Gdk = FakeGdk  # type: ignore[attr-defined]
    fake_repo.GLib = FakeGLib  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "gi", fake_gi)
    monkeypatch.setitem(sys.modules, "gi.repository", fake_repo)

    # Reset class-level counters so tests start from zero.
    FakeGtk.init_calls = []
    FakeGtk.main_calls = 0
    FakeGtk.main_quit_calls = 0
    FakeGLib.timeout_add_calls = []
    FakeGLib.main_loop_instances = []

    return fake_gi, fake_repo
