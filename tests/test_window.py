# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for :class:`GtkNativeSinkWindow` in ``openfollow.window``.

``GtkNativeSinkWindow`` wraps GTK 3 widgets behind a RenderCanvas-
compatible event API.  The constructor does ``Gtk.init(None)`` and
builds several real GTK widgets, so we inject fake ``gi`` and
``gi.repository`` modules into ``sys.modules`` before each test so:

 - ``import gi`` returns our stub
 - ``gi.require_version`` is a no-op
 - ``from gi.repository import Gdk, Gtk`` resolves to our fakes

The fakes are recording objects (properties, signal connections, event
masks) so tests can assert on event-handler wiring and still drive the
recorded callbacks directly for signal-dispatch tests.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# Fake GTK / GDK widgets
# --------------------------------------------------------------------------- #


class FakeGdkEventMask:
    KEY_PRESS_MASK = 1
    KEY_RELEASE_MASK = 2
    SCROLL_MASK = 4
    SMOOTH_SCROLL_MASK = 8
    BUTTON_PRESS_MASK = 16
    BUTTON_RELEASE_MASK = 32
    POINTER_MOTION_MASK = 64


class FakeGdkScrollDirection:
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"
    SMOOTH = "smooth"


class FakeGdkModifierType:
    BUTTON1_MASK = 256  # GDK_BUTTON1_MASK
    BUTTON3_MASK = 1024  # GDK_BUTTON3_MASK


class FakeGdkWindowHints:
    ASPECT = 1


class FakeGdkGeometry:
    def __init__(self) -> None:
        self.min_aspect = 0.0
        self.max_aspect = 0.0


# Lookup table consulted by the fake ``Gdk.keyval_name``. Tests can
# prime this by calling ``set_keyval_mapping``.
_KEYVAL_TABLE: dict[int, str | None] = {}


def set_keyval_mapping(table: dict[int, str | None]) -> None:
    _KEYVAL_TABLE.clear()
    _KEYVAL_TABLE.update(table)


def fake_keyval_name(val: int) -> str | None:
    return _KEYVAL_TABLE.get(val)


class FakeGdkCursorType:
    """Stand-in for ``Gdk.CursorType`` enum members used as the legacy
    fallback when no named cursor is resolvable."""

    LEFT_PTR = "LEFT_PTR"
    BLANK_CURSOR = "BLANK_CURSOR"


class FakeGdkCursor:
    """Recorder for ``Gdk.Cursor.new_from_name`` so tests can assert
    which named cursor the window asks for when mounting an overlay.

    ``unavailable_names`` lets a test simulate a Pi without the
    adwaita-icon-theme installed: any name in that set returns None
    from ``new_from_name``, exercising the fallback chain.
    """

    name_calls: list[str] = []
    unavailable_names: set[str] = set()
    type_calls: list[Any] = []
    type_unavailable: bool = False

    def __init__(self, name: str) -> None:
        self.name = name

    @classmethod
    def new_from_name(cls, _display: Any, name: str) -> FakeGdkCursor | None:
        cls.name_calls.append(name)
        if name in cls.unavailable_names:
            return None
        return cls(name)

    @classmethod
    def new_for_display(cls, _display: Any, cursor_type: Any) -> FakeGdkCursor:
        cls.type_calls.append(cursor_type)
        if cls.type_unavailable:
            # Real GTK raises on a bogus enum value; simulate the same
            # so the helper exercises its final-warning branch.
            raise RuntimeError("no enum-based cursor available")
        return cls(str(cursor_type))


class FakeGdkSurface:
    """Stand-in for the realised ``Gdk.Window`` returned by
    ``Gtk.Window.get_window`` – exposes ``set_cursor`` so tests can
    inspect the pointer visibility transitions."""

    def __init__(self) -> None:
        self.cursor_calls: list[Any] = []

    def set_cursor(self, cursor: Any) -> None:
        self.cursor_calls.append(cursor)


class FakeGdk:
    EventMask = FakeGdkEventMask
    ScrollDirection = FakeGdkScrollDirection
    ModifierType = FakeGdkModifierType
    WindowHints = FakeGdkWindowHints
    Geometry = FakeGdkGeometry
    Cursor = FakeGdkCursor
    CursorType = FakeGdkCursorType

    @staticmethod
    def keyval_name(val: int) -> str | None:
        return fake_keyval_name(val)


class FakeAllocation:
    def __init__(self, width: int = 1920, height: int = 1080) -> None:
        self.width = width
        self.height = height


class FakeBox:
    def __init__(self, orientation: str = "vertical") -> None:
        self.orientation = orientation
        self.children: list[tuple[Any, bool, bool, int]] = []
        self._alloc_w = 0
        self._alloc_h = 0

    def pack_start(self, widget: Any, expand: bool, fill: bool, padding: int) -> None:
        self.children.append((widget, expand, fill, padding))

    def get_allocated_width(self) -> int:
        return self._alloc_w

    def get_allocated_height(self) -> int:
        return self._alloc_h


class FakeOverlay:
    def __init__(self) -> None:
        self._main_child: Any = None
        self._overlays: list[Any] = []
        self._pass_through: dict[int, bool] = {}
        self.queue_draw_calls = 0

    def queue_draw(self) -> None:
        self.queue_draw_calls += 1

    def add_overlay(self, widget: Any) -> None:
        self._overlays.append(widget)

    def set_overlay_pass_through(self, widget: Any, value: bool) -> None:
        self._pass_through[id(widget)] = value

    def add(self, widget: Any) -> None:
        self._main_child = widget
        if hasattr(widget, "_parent"):
            widget._parent = self

    def remove(self, widget: Any) -> None:
        if self._main_child is widget:
            self._main_child = None
        elif widget in self._overlays:
            self._overlays.remove(widget)
        if hasattr(widget, "_parent") and widget._parent is self:
            widget._parent = None

    def get_child(self) -> Any:
        return self._main_child


class FakeDrawingArea:
    def __init__(self) -> None:
        self._can_focus = True
        self._focus_on_click = True
        self.signals: list[tuple[str, Callable]] = []
        self.queue_draw_calls = 0
        self.allocation = FakeAllocation()

    def set_can_focus(self, value: bool) -> None:
        self._can_focus = value

    def set_focus_on_click(self, value: bool) -> None:
        self._focus_on_click = value

    def connect(self, signal: str, callback: Callable) -> None:
        self.signals.append((signal, callback))

    def queue_draw(self) -> None:
        self.queue_draw_calls += 1

    def get_allocation(self) -> FakeAllocation:
        return self.allocation

    def show(self) -> None:  # for widgets reparented into FakeOverlay
        return None


class FakeWindow:
    def __init__(self, title: str = "") -> None:
        self.title = title
        self.default_size: tuple[int, int] = (0, 0)
        self.children: list[Any] = []
        self.signals: list[tuple[str, Callable]] = []
        self.event_mask = 0
        self.shown = False
        self.fullscreen_calls = 0
        self.grab_focus_calls = 0
        self.set_title_calls: list[str] = []
        self.set_geometry_hints_calls: list[tuple[Any, Any, Any]] = []
        self.resize_calls: list[tuple[int, int]] = []
        self._tick_id = 0
        self._tick_cb: tuple[Callable, Any] | None = None
        self.remove_tick_calls: list[int] = []
        self.toplevel_focus = True
        # ``get_window`` returns the realised ``Gdk.Window`` (or None
        # before map). Tests can set ``_gdk_window`` to None to exercise
        # the pre-realize branch.
        self._gdk_window: Any = FakeGdkSurface()
        self._display: Any = object()

    def set_default_size(self, w: int, h: int) -> None:
        self.default_size = (w, h)

    def resize(self, w: int, h: int) -> None:
        self.resize_calls.append((w, h))

    def add(self, widget: Any) -> None:
        self.children.append(widget)

    def add_events(self, mask: int) -> None:
        self.event_mask |= mask

    def connect(self, signal: str, callback: Callable) -> None:
        self.signals.append((signal, callback))

    def show_all(self) -> None:
        self.shown = True

    def set_title(self, title: str) -> None:
        self.set_title_calls.append(title)
        self.title = title

    def get_size(self) -> tuple[int, int]:
        return self.default_size

    def has_toplevel_focus(self) -> bool:
        return self.toplevel_focus

    def get_window(self) -> Any:
        return self._gdk_window

    def get_display(self) -> Any:
        return self._display

    def grab_focus(self) -> None:
        self.grab_focus_calls += 1

    def fullscreen(self) -> None:
        self.fullscreen_calls += 1

    def set_geometry_hints(self, widget: Any, geometry: Any, mask: Any) -> None:
        self.set_geometry_hints_calls.append((widget, geometry, mask))

    def add_tick_callback(self, cb: Callable, user_data: Any) -> int:
        self._tick_id += 1
        self._tick_cb = (cb, user_data)
        return self._tick_id

    def remove_tick_callback(self, tick_id: int) -> None:
        self.remove_tick_calls.append(tick_id)
        self._tick_cb = None

    # -- Test helpers ----------------------------------------------------
    def fire(self, signal: str, *args: Any) -> Any:
        """Invoke the registered handler for *signal* with *args*."""
        for name, cb in self.signals:
            if name == signal:
                return cb(self, *args)
        raise AssertionError(f"no handler for {signal!r}")

    def tick(self, frame_clock: Any = None, user_data: Any = None) -> bool:
        assert self._tick_cb is not None, "no tick callback registered"
        cb, data = self._tick_cb
        return cb(self, frame_clock, data if user_data is None else user_data)


class FakeOrientation:
    VERTICAL = "vertical"
    HORIZONTAL = "horizontal"


class FakeGtk:
    main_quit_calls = 0
    init_calls = 0

    Window = FakeWindow
    Box = FakeBox
    Overlay = FakeOverlay
    DrawingArea = FakeDrawingArea
    Orientation = FakeOrientation

    @classmethod
    def init(cls, _argv: Any) -> None:
        cls.init_calls += 1

    @classmethod
    def main_quit(cls) -> None:
        cls.main_quit_calls += 1


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _install_fake_gi(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``gi`` and ``gi.repository`` in ``sys.modules`` with fakes.

    Any import of ``gi`` or ``from gi.repository import Gtk, Gdk`` after
    this call lands on the fakes.  Restore is handled automatically by
    monkeypatch at test teardown.
    """
    fake_gi = types.ModuleType("gi")
    fake_gi.require_version = lambda *_args, **_kw: None  # type: ignore[attr-defined]

    fake_repo = types.ModuleType("gi.repository")
    fake_repo.Gtk = FakeGtk  # type: ignore[attr-defined]
    fake_repo.Gdk = FakeGdk  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "gi", fake_gi)
    monkeypatch.setitem(sys.modules, "gi.repository", fake_repo)


@pytest.fixture
def fake_gi(monkeypatch):
    _install_fake_gi(monkeypatch)
    # Reset class-level counters on our fakes so tests that assert on
    # them start from a clean baseline.
    FakeGtk.main_quit_calls = 0
    FakeGtk.init_calls = 0
    FakeGdkCursor.name_calls = []
    FakeGdkCursor.unavailable_names = set()
    FakeGdkCursor.type_calls = []
    FakeGdkCursor.type_unavailable = False
    set_keyval_mapping({})


@pytest.fixture
def window(fake_gi):
    """Return a freshly-constructed ``GtkNativeSinkWindow``."""
    from openfollow.window import GtkNativeSinkWindow

    return GtkNativeSinkWindow(width=1280, height=720, title="Test")


# --------------------------------------------------------------------------- #
# Module-level
# --------------------------------------------------------------------------- #


class TestModuleLevel:
    def test_gdk_key_map_covers_arrow_keys(self) -> None:
        """The module-level ``_GDK_KEY_MAP`` is a lookup table consumed
        by ``_translate_key``.  Asserting on a few well-known entries
        guards against accidental deletions during refactors."""
        from openfollow.window import _GDK_KEY_MAP

        assert _GDK_KEY_MAP["Up"] == "ArrowUp"
        assert _GDK_KEY_MAP["Down"] == "ArrowDown"
        assert _GDK_KEY_MAP["Left"] == "ArrowLeft"
        assert _GDK_KEY_MAP["Right"] == "ArrowRight"

    def test_gdk_key_map_covers_modifier_aliases(self) -> None:
        from openfollow.window import _GDK_KEY_MAP

        # Left/right variants of modifiers collapse onto a single
        # RenderCanvas-style key name.
        for left, right in [
            ("Shift_L", "Shift_R"),
            ("Control_L", "Control_R"),
            ("Alt_L", "Alt_R"),
            ("Meta_L", "Meta_R"),
            ("Super_L", "Super_R"),
        ]:
            assert _GDK_KEY_MAP[left] == _GDK_KEY_MAP[right]

    def test_gdk_key_map_maps_numpad_digits(self) -> None:
        from openfollow.window import _GDK_KEY_MAP

        for i in range(10):
            assert _GDK_KEY_MAP[f"KP_{i}"] == f"Numpad{i}"

    def test_gdk_key_map_maps_enter_and_escape(self) -> None:
        from openfollow.window import _GDK_KEY_MAP

        assert _GDK_KEY_MAP["Return"] == "Enter"
        assert _GDK_KEY_MAP["KP_Enter"] == "Enter"
        assert _GDK_KEY_MAP["Escape"] == "Escape"
        assert _GDK_KEY_MAP["space"] == " "


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #


class TestConstructor:
    def test_constructor_initialises_gtk_and_wires_widgets(self, fake_gi) -> None:
        from openfollow.window import GtkNativeSinkWindow

        w = GtkNativeSinkWindow(width=800, height=600, title="My App")

        # Gtk.init was called.
        assert FakeGtk.init_calls == 1
        # Window got the declared default size.
        assert w._window.default_size == (800, 600)
        # Window is shown (show_all).
        assert w._window.shown is True
        # Overlay contains the HUD drawing area.
        assert w._hud_drawing_area in w._overlay._overlays
        # HUD drawing area is pass-through (doesn't steal input).
        assert w._overlay._pass_through[id(w._hud_drawing_area)] is True

    def test_constructor_subscribes_all_expected_signals(self, fake_gi) -> None:
        from openfollow.window import GtkNativeSinkWindow

        w = GtkNativeSinkWindow(width=100, height=100, title="t")
        signal_names = {name for name, _cb in w._window.signals}
        expected = {
            "key-press-event",
            "key-release-event",
            "scroll-event",
            "button-press-event",
            "button-release-event",
            "motion-notify-event",
            "configure-event",
            "delete-event",
        }
        assert expected.issubset(signal_names)

    def test_constructor_adds_expected_event_mask_bits(self, fake_gi) -> None:
        from openfollow.window import GtkNativeSinkWindow

        w = GtkNativeSinkWindow(width=100, height=100, title="t")
        mask = w._window.event_mask
        for bit in (
            FakeGdkEventMask.KEY_PRESS_MASK,
            FakeGdkEventMask.KEY_RELEASE_MASK,
            FakeGdkEventMask.SCROLL_MASK,
            FakeGdkEventMask.SMOOTH_SCROLL_MASK,
            FakeGdkEventMask.BUTTON_PRESS_MASK,
            FakeGdkEventMask.BUTTON_RELEASE_MASK,
            FakeGdkEventMask.POINTER_MOTION_MASK,
        ):
            assert mask & bit


# --------------------------------------------------------------------------- #
# RenderCanvas-compatible API
# --------------------------------------------------------------------------- #


class TestPublicApi:
    def test_add_event_handler_is_invoked_on_emit(self, window) -> None:
        collected: list[dict[str, Any]] = []
        window.add_event_handler(collected.append, "key_down")
        window._emit("key_down", key="a")
        assert collected == [{"key": "a"}]

    def test_add_event_handler_multiple_callbacks_fan_out(self, window) -> None:
        seen1, seen2 = [], []
        window.add_event_handler(seen1.append, "pointer_down")
        window.add_event_handler(seen2.append, "pointer_down")
        window._emit("pointer_down", x=1, y=2, button=1)
        assert seen1 == seen2 == [{"x": 1, "y": 2, "button": 1}]

    def test_emit_unknown_event_is_silent(self, window) -> None:
        # No subscribers → must not raise.
        window._emit("nonexistent_event", foo=1)

    def test_request_draw_is_noop(self, window) -> None:
        window.request_draw(lambda *_a: None)

    def test_close_stops_tick_and_quits_gtk(self, window) -> None:
        window.start_tick_animation(lambda: None)
        assert window._tick_callback_id is not None

        window.close()

        assert window._closing is True
        assert window._tick_callback_id is None
        assert FakeGtk.main_quit_calls == 1

    def test_set_title_propagates_to_window(self, window) -> None:
        window.set_title("New Title")
        assert window._window.set_title_calls == ["New Title"]

    def test_fullscreen_calls_window_fullscreen(self, window) -> None:
        window.fullscreen()
        assert window._window.fullscreen_calls == 1

    def test_set_aspect_ratio_writes_geometry_hint(self, window) -> None:
        window.set_aspect_ratio(16, 9)
        assert len(window._window.set_geometry_hints_calls) == 1
        _widget, geom, mask = window._window.set_geometry_hints_calls[0]
        assert geom.min_aspect == pytest.approx(16 / 9)
        assert geom.max_aspect == pytest.approx(16 / 9)
        assert mask == FakeGdkWindowHints.ASPECT

    def test_apply_window_size_updates_default_and_resizes(self, window) -> None:
        """Live-resize updates default size and realised window geometry."""
        window.apply_window_size(1920, 1080)
        assert window._window.default_size == (1920, 1080)
        assert window._window.resize_calls == [(1920, 1080)]


# --------------------------------------------------------------------------- #
# Canvas size
# --------------------------------------------------------------------------- #


class TestCanvasSize:
    def test_returns_box_allocation_when_laid_out(self, window) -> None:
        window._box._alloc_w = 1600
        window._box._alloc_h = 900
        assert window.get_canvas_size() == (1600, 900)

    def test_falls_back_to_toplevel_size_before_layout(self, window) -> None:
        window._box._alloc_w = 0
        window._box._alloc_h = 0
        window._window.default_size = (1280, 720)
        assert window.get_canvas_size() == (1280, 720)

    def test_falls_back_when_box_alloc_is_1x1(self, window) -> None:
        """GTK briefly allocates widgets at 1x1 before real layout;
        the helper treats this as "not yet laid out"."""
        window._box._alloc_w = 1
        window._box._alloc_h = 1
        window._window.default_size = (800, 450)
        assert window.get_canvas_size() == (800, 450)


# --------------------------------------------------------------------------- #
# embed_widget
# --------------------------------------------------------------------------- #


class _FakeVideoWidget:
    def __init__(self) -> None:
        self._parent: Any = None
        self._can_focus = True
        self._focus_on_click = True
        self.shown = False
        self.realize_cbs: list[Callable] = []
        self.grab_focus_calls = 0
        self.queue_draw_calls = 0
        self._next_handler_id = 0
        self.connected: dict[int, tuple[str, Callable]] = {}
        self.disconnected_ids: list[int] = []

    def get_parent(self) -> Any:
        return self._parent

    def set_can_focus(self, v: bool) -> None:
        self._can_focus = v

    def set_focus_on_click(self, v: bool) -> None:
        self._focus_on_click = v

    def show(self) -> None:
        self.shown = True

    def queue_draw(self) -> None:
        self.queue_draw_calls += 1

    def connect(self, signal: str, callback: Callable) -> int:
        self._next_handler_id += 1
        hid = self._next_handler_id
        self.connected[hid] = (signal, callback)
        if signal == "realize":
            self.realize_cbs.append(callback)
        return hid

    def disconnect(self, handler_id: int) -> None:
        self.disconnected_ids.append(handler_id)
        self.connected.pop(handler_id, None)

    def grab_focus(self) -> None:
        self.grab_focus_calls += 1


class TestEmbedWidget:
    def test_none_widget_is_silently_ignored(self, window) -> None:
        window.embed_widget(None)
        assert window._overlay.get_child() is None

    def test_widget_already_embedded_is_noop(self, window) -> None:
        widget = _FakeVideoWidget()
        # Prime the overlay so the widget is genuinely already embedded:
        # FakeOverlay.add sets widget._parent = overlay, matching the
        # condition the real code uses to detect re-embed.
        window.embed_widget(widget)
        assert window._overlay.get_child() is widget
        grab_calls_before = window._window.grab_focus_calls

        window.embed_widget(widget)

        # Overlay still holds the same widget – no remove/add churn.
        assert window._overlay.get_child() is widget
        # And no redundant focus re-grab either (the no-op path returns
        # before touching focus).
        assert window._window.grab_focus_calls == grab_calls_before

    def test_embed_widget_reparents_and_grabs_focus(self, window) -> None:
        widget = _FakeVideoWidget()
        window.embed_widget(widget)
        assert window._overlay.get_child() is widget
        assert widget.shown is True
        # Focus is immediately grabbed on the toplevel window.
        assert window._window.grab_focus_calls >= 1
        assert widget._can_focus is False
        assert widget._focus_on_click is False

    def test_embed_widget_removes_existing_child_first(self, window) -> None:
        first = _FakeVideoWidget()
        window.embed_widget(first)
        assert window._overlay.get_child() is first

        second = _FakeVideoWidget()
        window.embed_widget(second)
        assert window._overlay.get_child() is second

    def test_embed_widget_detaches_from_previous_parent(self, window) -> None:

        class PrevParent:
            def __init__(self) -> None:
                self.removed: list[Any] = []

            def remove(self, child: Any) -> None:
                self.removed.append(child)

        prev = PrevParent()
        widget = _FakeVideoWidget()
        widget._parent = prev

        window.embed_widget(widget)
        assert prev.removed == [widget]

    def test_embed_widget_realize_callback_regrabs_focus(self, window) -> None:
        """The GTK ``realize`` signal fires asynchronously once the
        widget is added to the widget tree – the handler regrabs the
        toplevel's focus so keystrokes keep flowing to the window."""
        widget = _FakeVideoWidget()
        window.embed_widget(widget)
        assert widget.realize_cbs
        before = window._window.grab_focus_calls
        widget.realize_cbs[0](widget)
        assert window._window.grab_focus_calls == before + 1

    def test_reembed_same_widget_repaints_sink(self, window) -> None:
        """Re-submitting the already-parented shared sink (pipeline rebuild)
        must invalidate the sink + overlay so the freshly-rebuilt frames
        repaint – the HUD redraw path doesn't touch the video region."""
        widget = _FakeVideoWidget()
        window.embed_widget(widget)
        draws_before = widget.queue_draw_calls
        overlay_draws_before = window._overlay.queue_draw_calls
        realize_before = len(widget.realize_cbs)

        window.embed_widget(widget)

        # Sink region invalidated...
        assert widget.queue_draw_calls == draws_before + 1
        assert window._overlay.queue_draw_calls == overlay_draws_before + 1
        # ...via the no-remove/add fast path (no macOS reparent flash) and
        # without stacking another realize handler.
        assert window._overlay.get_child() is widget
        assert len(widget.realize_cbs) == realize_before

    def test_embed_none_clears_mounted_widget(self, window) -> None:
        """``embed_widget(None)`` is an explicit clear: the stale video widget
        is unmounted (not left showing its last frame) and the canvas repainted."""
        widget = _FakeVideoWidget()
        window.embed_widget(widget)
        assert window._overlay.get_child() is widget
        overlay_draws_before = window._overlay.queue_draw_calls

        window.embed_widget(None)

        assert window._overlay.get_child() is None
        assert window._overlay.queue_draw_calls == overlay_draws_before + 1
        # The cleared widget's realize handler is disconnected, not orphaned.
        assert widget.disconnected_ids

    def test_embed_none_on_empty_overlay_is_noop(self, window) -> None:
        # Nothing mounted: clearing must not blow up or spuriously repaint.
        draws_before = window._overlay.queue_draw_calls
        window.embed_widget(None)
        assert window._overlay.get_child() is None
        assert window._overlay.queue_draw_calls == draws_before

    def test_embed_swap_disconnects_prior_realize_handler(self, window) -> None:
        """A genuine widget swap disconnects the previous widget's realize
        handler instead of leaking the connection."""
        first = _FakeVideoWidget()
        window.embed_widget(first)
        first_handler_id = next(iter(first.connected))

        second = _FakeVideoWidget()
        window.embed_widget(second)

        assert first_handler_id in first.disconnected_ids
        assert second.realize_cbs  # new handler wired on the replacement


class TestOverlayWidget:
    """add_overlay_widget mounts overlay above HUD for WebKit browser."""

    def test_add_none_widget_is_silently_ignored(self, window) -> None:
        # Defensive guard – runtime callers gate on ``WEBKIT_AVAILABLE``
        # but the API still has to tolerate the falsy case.
        window.add_overlay_widget(None)

    def test_remove_none_widget_is_silently_ignored(self, window) -> None:
        window.remove_overlay_widget(None)

    def test_add_overlay_widget_mounts_and_shows(self, window) -> None:
        widget = _FakeVideoWidget()
        window.add_overlay_widget(widget)
        assert widget in window._overlay._overlays
        assert widget.shown is True

    def test_add_overlay_widget_detaches_from_prior_parent(self, window) -> None:
        class _PrevParent:
            def __init__(self) -> None:
                self.removed: list = []

            def remove(self, child) -> None:  # noqa: ANN001
                self.removed.append(child)

        prev = _PrevParent()
        widget = _FakeVideoWidget()
        widget._parent = prev
        window.add_overlay_widget(widget)
        assert prev.removed == [widget]

    def test_remove_overlay_widget_detaches(self, window) -> None:
        widget = _FakeVideoWidget()
        # Mount via the public API so the FakeOverlay tracks parentage.
        window.add_overlay_widget(widget)
        # FakeOverlay's ``add_overlay`` doesn't set ``_parent`` (only
        # ``add`` does), so wire it manually to match the precondition
        # ``remove_overlay_widget`` checks for.
        widget._parent = window._overlay
        window.remove_overlay_widget(widget)
        assert widget not in window._overlay._overlays

    def test_remove_overlay_widget_no_op_when_not_attached(self, window) -> None:
        widget = _FakeVideoWidget()
        widget._parent = object()  # belongs to something else
        # Pre-condition: nothing in the overlay.
        assert widget not in window._overlay._overlays
        # Call must not raise.
        window.remove_overlay_widget(widget)

    def test_add_overlay_widget_grabs_focus(self, window) -> None:
        widget = _FakeVideoWidget()
        window.add_overlay_widget(widget)
        assert widget._can_focus is True
        assert widget._focus_on_click is True
        assert widget.grab_focus_calls >= 1
        # And a realize callback was wired so focus also gets a second
        # try once GTK finishes realising the widget (async).
        assert widget.realize_cbs

    def test_add_overlay_widget_realize_callback_grabs_focus(self, window) -> None:
        """The realize-time focus grab covers the case where the
        widget hasn't realised yet when ``grab_focus`` ran the first
        time – without it, the focus call would silently no-op."""
        widget = _FakeVideoWidget()
        window.add_overlay_widget(widget)
        before = widget.grab_focus_calls
        widget.realize_cbs[0](widget)
        assert widget.grab_focus_calls == before + 1

    def test_add_overlay_swap_disconnects_prior_realize_handler(self, window) -> None:
        """Mounting a second overlay disconnects the first's realize handler
        rather than stacking handler connections."""
        first = _FakeVideoWidget()
        window.add_overlay_widget(first)
        first_handler_id = next(iter(first.connected))

        second = _FakeVideoWidget()
        window.add_overlay_widget(second)

        assert first_handler_id in first.disconnected_ids

    def test_remove_overlay_widget_disconnects_realize_handler(self, window) -> None:
        """Removing the overlay disconnects the realize→grab_focus handler so
        re-adding the same widget later doesn't stack a second one."""
        widget = _FakeVideoWidget()
        window.add_overlay_widget(widget)
        handler_id = next(iter(widget.connected))
        widget._parent = window._overlay  # FakeOverlay.add_overlay doesn't set _parent

        window.remove_overlay_widget(widget)

        assert handler_id in widget.disconnected_ids

    def test_remove_older_overlay_keeps_active_overlay_handler(self, window) -> None:
        """Removing an older overlay widget must NOT disconnect the currently
        tracked (active) overlay's realize handler."""
        first = _FakeVideoWidget()
        second = _FakeVideoWidget()
        window.add_overlay_widget(first)
        window.add_overlay_widget(second)  # second becomes the tracked overlay
        second_handler_id = next(iter(second.connected))
        first._parent = window._overlay  # precondition for remove

        window.remove_overlay_widget(first)

        # The active overlay (second) keeps its handler and stays tracked.
        assert second_handler_id not in second.disconnected_ids
        assert window._overlay_realize is not None
        assert window._overlay_realize[0] is second

    def test_clear_realize_swallows_disconnect_failure(self, window) -> None:
        """A widget mid-teardown can raise on disconnect; _clear_realize must
        swallow it (mirrors the defensive disconnect used elsewhere) and still
        forget the slot."""

        class _RaisingWidget(_FakeVideoWidget):
            def disconnect(self, handler_id: int) -> None:
                raise RuntimeError("handler already auto-disconnected")

        window._embed_realize = (_RaisingWidget(), 1)
        window._clear_realize("_embed_realize")  # must not raise
        assert window._embed_realize is None

    # -- Overlay-active state + pointer visibility -----------------------

    def test_add_overlay_widget_marks_overlay_active_and_shows_cursor(
        self,
        window,
    ) -> None:
        """Mounting an overlay flips the window into "overlay-owned"
        mode so the key handlers stop swallowing keys, and asks Cage
        (the Wayland kiosk on the Pi) to render a default cursor –
        without this the operator has no way to see where they're
        pointing inside the embedded browser."""
        widget = _FakeVideoWidget()
        window.add_overlay_widget(widget)

        assert window._overlay_widget is widget
        # ``new_from_name(display, "default")`` is the first try in
        # the cursor-name fallback chain; the fake records every
        # request so we can assert the order is "default" first.
        assert FakeGdkCursor.name_calls[0] == "default"
        # And the cursor landed on the Gdk.Window via set_cursor.
        cursor_calls = window._window._gdk_window.cursor_calls
        assert len(cursor_calls) == 1
        assert isinstance(cursor_calls[0], FakeGdkCursor)

    def test_set_pointer_visible_walks_name_fallback_chain(self, window) -> None:
        FakeGdkCursor.unavailable_names = {"default"}
        window._set_pointer_visible(True)
        # Tried "default" first, fell through to "left_ptr".
        assert FakeGdkCursor.name_calls[:2] == ["default", "left_ptr"]
        # And ended up setting the resolved cursor (the second try).
        assert window._window._gdk_window.cursor_calls[-1].name == "left_ptr"

    def test_set_pointer_visible_falls_back_to_cursor_type_enum(
        self,
        window,
    ) -> None:
        """When every named cursor is missing, the helper drops to the
        legacy ``Gdk.Cursor.new_for_display(display, CursorType.LEFT_PTR)``
        API which uses GTK-internal fallbacks not requiring a theme
        directory on disk."""
        FakeGdkCursor.unavailable_names = {"default", "left_ptr", "arrow"}
        window._set_pointer_visible(True)
        # Every named lookup ran (3) and returned None; the helper
        # then asked the enum API for LEFT_PTR.
        assert FakeGdkCursor.name_calls == ["default", "left_ptr", "arrow"]
        assert FakeGdkCursor.type_calls == [FakeGdkCursorType.LEFT_PTR]
        # And the enum cursor landed on the Gdk.Window.
        cursor_calls = window._window._gdk_window.cursor_calls
        assert isinstance(cursor_calls[-1], FakeGdkCursor)

    def test_set_pointer_visible_logs_and_returns_when_no_cursor_available(
        self,
        window,
        caplog,
    ) -> None:
        FakeGdkCursor.unavailable_names = {"default", "left_ptr", "arrow"}
        FakeGdkCursor.type_unavailable = True
        # No cursor set during this call – measure the pre-state.
        gdk_window = window._window._gdk_window
        calls_before = list(gdk_window.cursor_calls)

        import logging

        with caplog.at_level(logging.WARNING):
            window._set_pointer_visible(True)

        assert gdk_window.cursor_calls == calls_before
        assert any("cursor theme" in rec.message.lower() for rec in caplog.records)

    def test_remove_overlay_widget_clears_state_and_hides_cursor(
        self,
        window,
    ) -> None:
        """Tearing the overlay down restores the no-cursor default the
        rest of the app expects (the cursor would otherwise sit on
        top of the tracker overlay) and unblocks the key-swallowing
        behaviour for app shortcuts again."""
        widget = _FakeVideoWidget()
        window.add_overlay_widget(widget)
        # Real GTK Overlay.remove flips _parent off; the FakeOverlay
        # already does this on remove(), but the public API path goes
        # through ``remove_overlay_widget`` which checks
        # ``get_parent() is self._overlay`` first.
        widget._parent = window._overlay

        window.remove_overlay_widget(widget)

        assert window._overlay_widget is None
        # Second cursor call: an explicit blank ("none") cursor – the base
        # state defaults to hidden when the app never set one. ``None`` is
        # not enough on X11/macOS, where it inherits the default arrow.
        cursor_calls = window._window._gdk_window.cursor_calls
        assert cursor_calls[-1].name == "none"

    def test_remove_overlay_widget_only_clears_on_match(self, window) -> None:
        active = _FakeVideoWidget()
        window.add_overlay_widget(active)
        unrelated = _FakeVideoWidget()
        # The unrelated widget never went through add_overlay_widget,
        # so the window still tracks ``active``. Removing the
        # unrelated one must not flip ``_overlay_widget`` to None.
        window.remove_overlay_widget(unrelated)
        assert window._overlay_widget is active

    def test_set_pointer_visible_noop_before_realize(self, window) -> None:
        window._window._gdk_window = None
        # Direct call – bypasses the add_overlay_widget plumbing that
        # would have already been exercised by other tests.
        window._set_pointer_visible(True)
        window._set_pointer_visible(False)
        # No exception, no recorded calls (the gdk_window we cleared
        # is the only sink the helper writes to).

    def test_hide_uses_blank_named_cursor(self, window) -> None:
        """Hiding the pointer must set an explicit invisible cursor, not
        ``set_cursor(None)`` – the latter inherits the parent's default
        arrow on X11/macOS, which is exactly the stray-pointer bug. The
        ``"none"`` named cursor is invisible on every backend."""
        window._set_pointer_visible(False)
        assert FakeGdkCursor.name_calls == ["none"]
        cursor_calls = window._window._gdk_window.cursor_calls
        assert cursor_calls[-1].name == "none"

    def test_hide_falls_back_to_blank_cursor_enum(self, window) -> None:
        """A theme-less host has no ``"none"`` named cursor; the helper
        then drops to the legacy ``CursorType.BLANK_CURSOR`` enum, which
        needs no theme directory on disk."""
        FakeGdkCursor.unavailable_names = {"none"}
        window._set_pointer_visible(False)
        assert FakeGdkCursor.name_calls == ["none"]
        assert FakeGdkCursor.type_calls == [FakeGdkCursorType.BLANK_CURSOR]
        cursor_calls = window._window._gdk_window.cursor_calls
        assert cursor_calls[-1].name == "BLANK_CURSOR"

    def test_hide_sets_none_when_no_blank_cursor_available(
        self,
        window,
    ) -> None:
        """If neither the named nor the enum blank cursor resolves, the
        helper falls all the way back to ``set_cursor(None)`` – the
        best it can do on a fully theme-less host, and harmless on Cage
        where ``None`` already means no pointer."""
        FakeGdkCursor.unavailable_names = {"none"}
        FakeGdkCursor.type_unavailable = True
        window._set_pointer_visible(False)
        cursor_calls = window._window._gdk_window.cursor_calls
        assert cursor_calls[-1] is None

    def test_set_pointer_base_visible_hides_when_mouse_disabled(
        self,
        window,
    ) -> None:
        """The startup path calls this with ``mouse_enabled``; when False
        the pointer is hidden so a desktop compositor doesn't park a
        default cursor over the video with no mouse attached."""
        window.set_pointer_base_visible(False)
        assert window._base_pointer_visible is False
        cursor_calls = window._window._gdk_window.cursor_calls
        assert cursor_calls[-1].name == "none"

    def test_set_pointer_base_visible_shows_when_mouse_enabled(
        self,
        window,
    ) -> None:
        """With mouse input on, the operator needs to see where they're
        pointing, so the base state shows the default arrow."""
        window.set_pointer_base_visible(True)
        assert window._base_pointer_visible is True
        assert FakeGdkCursor.name_calls[0] == "default"

    def test_set_pointer_base_visible_is_idempotent(self, window) -> None:
        window.set_pointer_base_visible(False)
        calls_after_first = len(window._window._gdk_window.cursor_calls)
        window.set_pointer_base_visible(False)
        assert len(window._window._gdk_window.cursor_calls) == calls_after_first

    def test_set_pointer_base_visible_defers_while_overlay_mounted(
        self,
        window,
    ) -> None:
        widget = _FakeVideoWidget()
        window.add_overlay_widget(widget)
        widget._parent = window._overlay
        calls_after_mount = len(window._window._gdk_window.cursor_calls)

        # Operator disables mouse while the browser is open.
        window.set_pointer_base_visible(False)
        # Intent stored, but no cursor change while the overlay owns it.
        assert window._base_pointer_visible is False
        assert len(window._window._gdk_window.cursor_calls) == calls_after_mount

        # Closing the overlay restores the stored (hidden) base state.
        window.remove_overlay_widget(widget)
        assert window._window._gdk_window.cursor_calls[-1].name == "none"

    def test_remove_overlay_widget_returns_focus_to_window(
        self,
        window,
    ) -> None:
        """After detaching the overlay, the toplevel window re-grabs
        focus so app shortcuts (settings key, marker cycling, …) work
        again immediately – without the regrab, GTK retains the
        just-removed widget as the focus owner and keystrokes fall
        through to nowhere."""
        widget = _FakeVideoWidget()
        window.add_overlay_widget(widget)
        widget._parent = window._overlay  # FakeOverlay quirk
        before = window._window.grab_focus_calls
        window.remove_overlay_widget(widget)
        assert window._window.grab_focus_calls == before + 1


# --------------------------------------------------------------------------- #
# HUD draw wiring
# --------------------------------------------------------------------------- #


class TestHudDraw:
    def test_attach_hud_queues_redraw(self, window) -> None:
        before = window._hud_drawing_area.queue_draw_calls
        window.attach_hud(lambda cr, w, h: None)
        assert window._hud_drawing_area.queue_draw_calls == before + 1

    def test_hud_draw_signal_routes_to_registered_fn(self, window) -> None:
        calls: list[tuple[Any, int, int]] = []

        def draw_fn(cr: Any, w: int, h: int) -> None:
            calls.append((cr, w, h))

        window.attach_hud(draw_fn)
        # Fire the draw signal via the recording drawing area.
        alloc = window._hud_drawing_area.get_allocation()
        sentinel_cr = object()
        # Look up the registered "draw" handler and call it directly.
        for sig, cb in window._hud_drawing_area.signals:
            if sig == "draw":
                cb(window._hud_drawing_area, sentinel_cr)
        assert calls == [(sentinel_cr, alloc.width, alloc.height)]

    def test_hud_draw_without_registration_is_harmless(self, window) -> None:
        for sig, cb in window._hud_drawing_area.signals:
            if sig == "draw":
                result = cb(window._hud_drawing_area, object())
                assert result is False

    def test_hud_draw_exception_is_swallowed(self, window, caplog) -> None:
        def boom(cr: Any, w: int, h: int) -> None:
            raise RuntimeError("draw blew up")

        window.attach_hud(boom)
        import logging

        with caplog.at_level(logging.ERROR):
            for sig, cb in window._hud_drawing_area.signals:
                if sig == "draw":
                    result = cb(window._hud_drawing_area, object())
                    # Must not re-raise – GTK would repeatedly reinvoke us.
                    assert result is False
        assert any("HUD draw" in rec.message for rec in caplog.records)


# --------------------------------------------------------------------------- #
# Tick animation
# --------------------------------------------------------------------------- #


class TestTickAnimation:
    def test_start_registers_tick_callback_on_window(self, window) -> None:
        window.start_tick_animation(lambda: None)
        assert window._tick_callback_id is not None

    def test_start_is_idempotent(self, window) -> None:
        window.start_tick_animation(lambda: None)
        first = window._tick_callback_id
        window.start_tick_animation(lambda: None)
        assert window._tick_callback_id == first

    def test_stop_is_idempotent(self, window) -> None:
        window.stop_tick_animation()  # no raise when not running
        window.start_tick_animation(lambda: None)
        window.stop_tick_animation()
        assert window._tick_callback_id is None
        window.stop_tick_animation()  # second call noop
        assert window._tick_callback_id is None

    def test_tick_invokes_registered_fn_and_returns_true(self, window) -> None:
        called = []
        window.start_tick_animation(lambda: called.append(1))
        assert window._window.tick() is True
        assert called == [1]

    def test_tick_returns_false_during_shutdown(self, window) -> None:
        """Once ``close()`` has flipped ``_closing``, the tick callback
        returns False so GTK drops it – otherwise a late tick could run
        after we've already torn down the HUD."""
        window.start_tick_animation(lambda: None)
        window._closing = True
        assert window._window.tick() is False

    def test_tick_exception_does_not_stop_loop(self, window, caplog) -> None:
        def boom() -> None:
            raise RuntimeError("frame blew up")

        window.start_tick_animation(boom)
        import logging

        with caplog.at_level(logging.ERROR):
            assert window._window.tick() is True
        assert any("animation tick" in rec.message for rec in caplog.records)

    def test_tick_redraws_hud_when_attached(self, window) -> None:
        window.attach_hud(lambda *_a: None)
        before = window._hud_drawing_area.queue_draw_calls
        window.start_tick_animation(lambda: None)
        window._window.tick()
        # attach_hud queued one draw, the tick queued another.
        assert window._hud_drawing_area.queue_draw_calls >= before + 1

    def test_tick_regrabs_focus_when_lost(self, window) -> None:
        """If the toplevel lost focus (e.g. user clicked another app),
        the tick callback defensively regrabs it so keyboard events
        keep being delivered here once it refocuses."""
        window._window.toplevel_focus = False
        window.start_tick_animation(lambda: None)
        before = window._window.grab_focus_calls
        window._window.tick()
        assert window._window.grab_focus_calls == before + 1

    def test_tick_without_registered_fn_still_redraws_hud(self, window) -> None:
        window.start_tick_animation(lambda: None)
        # Manually clear the fn to simulate the transient state.
        window._tick_fn = None
        window.attach_hud(lambda *_a: None)
        before = window._hud_drawing_area.queue_draw_calls
        assert window._window.tick() is True
        assert window._hud_drawing_area.queue_draw_calls > before


# --------------------------------------------------------------------------- #
# Key translation
# --------------------------------------------------------------------------- #


class TestTranslateKey:
    def test_returns_empty_string_when_keyval_name_is_none(self, fake_gi) -> None:
        from openfollow.window import GtkNativeSinkWindow

        set_keyval_mapping({99: None})
        event = SimpleNamespace(keyval=99)
        assert GtkNativeSinkWindow._translate_key(event) == ""

    def test_maps_arrow_via_gdk_key_map(self, fake_gi) -> None:
        from openfollow.window import GtkNativeSinkWindow

        set_keyval_mapping({123: "Up"})
        event = SimpleNamespace(keyval=123)
        assert GtkNativeSinkWindow._translate_key(event) == "ArrowUp"

    def test_lowercases_single_character_names(self, fake_gi) -> None:
        """An unmapped single-char name (letters produced directly by
        Gdk) lower-cases so 'A' and 'a' both surface as 'a' – matching
        the RenderCanvas convention."""
        from openfollow.window import GtkNativeSinkWindow

        set_keyval_mapping({65: "A"})
        event = SimpleNamespace(keyval=65)
        assert GtkNativeSinkWindow._translate_key(event) == "a"

    def test_preserves_multichar_unmapped_names(self, fake_gi) -> None:
        """Unmapped names longer than one character are passed through
        unchanged (e.g. `F1`, `PageUp`)."""
        from openfollow.window import GtkNativeSinkWindow

        set_keyval_mapping({200: "F1"})
        event = SimpleNamespace(keyval=200)
        assert GtkNativeSinkWindow._translate_key(event) == "F1"


# --------------------------------------------------------------------------- #
# Keyboard / pointer / scroll event dispatch
# --------------------------------------------------------------------------- #


class TestEventDispatch:
    def _register_key_table(self) -> None:
        set_keyval_mapping({1: "Up", 2: "Down", 3: "A"})

    def _handler_list(self, window, event: str) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        window.add_event_handler(collected.append, event)
        return collected

    def test_key_press_emits_translated_key_down(self, window) -> None:
        self._register_key_table()
        received = self._handler_list(window, "key_down")

        event = SimpleNamespace(keyval=1)
        result = window._window.fire("key-press-event", event)
        assert result is True  # absorbs the signal
        assert received == [{"key": "ArrowUp"}]

    def test_key_release_emits_translated_key_up(self, window) -> None:
        self._register_key_table()
        received = self._handler_list(window, "key_up")

        event = SimpleNamespace(keyval=2)
        result = window._window.fire("key-release-event", event)
        assert result is True
        assert received == [{"key": "ArrowDown"}]

    def test_key_press_with_unknown_name_suppresses_event(self, window) -> None:
        """When ``Gdk.keyval_name`` returns None, ``_translate_key``
        returns "" – the window silently drops the event rather than
        emitting an empty-string key to subscribers."""
        set_keyval_mapping({9999: None})
        received = self._handler_list(window, "key_down")

        window._window.fire("key-press-event", SimpleNamespace(keyval=9999))
        assert received == []

    def test_key_release_with_unknown_name_suppresses_event(self, window) -> None:
        set_keyval_mapping({8888: None})
        received = self._handler_list(window, "key_up")

        window._window.fire("key-release-event", SimpleNamespace(keyval=8888))
        assert received == []

    def test_key_press_returns_false_when_overlay_active(self, window) -> None:
        self._register_key_table()
        received = self._handler_list(window, "key_down")
        widget = _FakeVideoWidget()
        window.add_overlay_widget(widget)

        result = window._window.fire("key-press-event", SimpleNamespace(keyval=3))

        assert result is False
        # The emit still runs – downstream handlers (e.g. on_key_down
        # in app_modes) own the ``_browser_active`` gating.
        assert received == [{"key": "a"}]

    def test_key_release_returns_false_when_overlay_active(self, window) -> None:
        self._register_key_table()
        received = self._handler_list(window, "key_up")
        widget = _FakeVideoWidget()
        window.add_overlay_widget(widget)

        result = window._window.fire("key-release-event", SimpleNamespace(keyval=3))

        assert result is False
        assert received == [{"key": "a"}]

    def test_scroll_event_normalises_smooth_delta_to_unit_tick(self, window) -> None:
        # A smooth-scroll delta is collapsed to a single unit tick so one notch
        # is exactly one ``mouse_wheel_z_step`` regardless of the device's delta
        # magnitude. GTK dy > 0 is scroll *down* (emit -1); dy < 0 is up (+1).
        # The magnitudes here (1.5, -2.0, 2.5) must NOT scale the tick.
        received = self._handler_list(window, "wheel")

        for delta in (1.5, 2.5):  # scroll down at different per-notch magnitudes
            window._window.fire(
                "scroll-event",
                SimpleNamespace(
                    get_scroll_deltas=lambda d=delta: (True, 0.0, d),
                    direction=FakeGdkScrollDirection.SMOOTH,
                ),
            )
        window._window.fire(
            "scroll-event",
            SimpleNamespace(
                get_scroll_deltas=lambda: (True, 0.0, -2.0),
                direction=FakeGdkScrollDirection.SMOOTH,
            ),
        )
        assert received == [{"dy": -1.0}, {"dy": -1.0}, {"dy": 1.0}]

    def test_scroll_event_horizontal_smooth_delta_emits_nothing(self, window) -> None:
        # A horizontal-only smooth event (dy == 0) must not emit a vertical tick.
        received = self._handler_list(window, "wheel")
        window._window.fire(
            "scroll-event",
            SimpleNamespace(
                get_scroll_deltas=lambda: (True, 1.5, 0.0),
                direction=FakeGdkScrollDirection.SMOOTH,
            ),
        )
        assert received == []

    def test_scroll_one_notch_pair_emits_single_tick(self, window) -> None:
        # Real devices emit a legacy discrete UP/DOWN event AND a smooth event
        # per wheel notch. Only the smooth one is processed; handling both would
        # double-count and move Z two steps per notch. One notch up = discrete
        # UP (ok=False) followed by smooth (ok=True, dy=-1.5) -> a single +1.
        received = self._handler_list(window, "wheel")
        window._window.fire(
            "scroll-event",
            SimpleNamespace(
                get_scroll_deltas=lambda: (False, 0.0, 0.0),
                direction=FakeGdkScrollDirection.UP,
            ),
        )
        window._window.fire(
            "scroll-event",
            SimpleNamespace(
                get_scroll_deltas=lambda: (True, 0.0, -1.5),
                direction=FakeGdkScrollDirection.SMOOTH,
            ),
        )
        assert received == [{"dy": 1.0}]

    def test_scroll_event_legacy_discrete_is_ignored(self, window) -> None:
        """A legacy discrete event (ok=False) on its own emits nothing – it is
        a duplicate of the smooth event on every supported platform, so the
        smooth path is authoritative."""
        received = self._handler_list(window, "wheel")
        for direction in (FakeGdkScrollDirection.UP, FakeGdkScrollDirection.DOWN):
            window._window.fire(
                "scroll-event",
                SimpleNamespace(
                    get_scroll_deltas=lambda: (False, 0.0, 0.0),
                    direction=direction,
                ),
            )
        assert received == []

    def test_scroll_event_other_direction_emits_nothing(self, window) -> None:
        """LEFT/RIGHT scroll is a no-op: the app has no horizontal
        scroll semantics, and we'd rather drop the event than emit a
        confusing ``dy=0`` signal."""
        received = self._handler_list(window, "wheel")

        event = SimpleNamespace(
            get_scroll_deltas=lambda: (False, 0.0, 0.0),
            direction=FakeGdkScrollDirection.LEFT,
        )
        window._window.fire("scroll-event", event)
        assert received == []

    def test_button_press_and_release_payloads(self, window) -> None:
        downs = self._handler_list(window, "pointer_down")
        ups = self._handler_list(window, "pointer_up")

        press = SimpleNamespace(x=12.0, y=34.0, button=1)
        release = SimpleNamespace(x=56.0, y=78.0, button=3)
        window._window.fire("button-press-event", press)
        window._window.fire("button-release-event", release)

        assert downs == [{"x": 12.0, "y": 34.0, "button": 1}]
        assert ups == [{"x": 56.0, "y": 78.0, "button": 3}]

    def test_motion_event_payload(self, window) -> None:
        moves = self._handler_list(window, "pointer_move")
        window._window.fire("motion-notify-event", SimpleNamespace(x=100.0, y=200.0))
        assert moves == [{"x": 100.0, "y": 200.0}]

    def test_configure_event_emits_resize(self, window) -> None:
        resizes = self._handler_list(window, "resize")
        window._window.fire("configure-event", SimpleNamespace(width=1366, height=768))
        assert resizes == [{"width": 1366, "height": 768}]

    def test_configure_event_handler_returns_false(self, window) -> None:
        """The configure handler returns False so GTK continues its
        default re-layout; returning True would freeze the window."""
        self._handler_list(window, "resize")
        result = window._window.fire("configure-event", SimpleNamespace(width=1, height=1))
        assert result is False

    def test_delete_event_closes_and_quits(self, window) -> None:
        closes = self._handler_list(window, "close")
        window._window.fire("delete-event", SimpleNamespace())
        assert closes == [{}]
        assert window._closing is True
        assert FakeGtk.main_quit_calls == 1

    def test_focus_out_event_emits_blur(self, window) -> None:
        blurs = self._handler_list(window, "blur")
        result = window._window.fire("focus-out-event", SimpleNamespace())
        assert blurs == [{}]
        # Returns False so GTK keeps its default focus handling.
        assert result is False


# --------------------------------------------------------------------------- #
# macOS pointer polling
# --------------------------------------------------------------------------- #


class _PollSurface:
    """Fake realised Gdk.Window returning scripted device positions."""

    def __init__(self, samples: list[tuple[int, int, int]]) -> None:
        self._samples = samples
        self._i = 0

    def get_device_position(self, _device: Any) -> tuple[Any, int, int, int]:
        x, y, mask = self._samples[min(self._i, len(self._samples) - 1)]
        self._i += 1
        return (None, x, y, mask)


_B1 = FakeGdkModifierType.BUTTON1_MASK
_B3 = FakeGdkModifierType.BUTTON3_MASK


class TestPointerPoll:
    """On macOS the window polls the pointer each frame and synthesises the
    same pointer events the GTK signal handlers would."""

    def _handler_list(self, window, event: str) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        window.add_event_handler(collected.append, event)
        return collected

    # --- pure edge-detection helper -------------------------------------

    def test_left_press_over_canvas_grabs(self, window) -> None:
        events, state = window._poll_pointer_events((None, False, False), 100, 200, True, False, True)
        assert events == [("pointer_down", {"x": 100, "y": 200, "button": 1})]
        assert state == ((100, 200), True, False)

    def test_press_outside_canvas_does_not_grab(self, window) -> None:
        events, _ = window._poll_pointer_events((None, False, False), 5, 5, True, False, False)
        assert events == []

    def test_right_press_over_canvas_emits_button3(self, window) -> None:
        events, _ = window._poll_pointer_events(((10, 10), False, False), 10, 10, False, True, True)
        assert events == [("pointer_down", {"x": 10, "y": 10, "button": 3})]

    def test_position_change_emits_move(self, window) -> None:
        events, _ = window._poll_pointer_events(((10, 10), False, False), 30, 40, False, False, True)
        assert events == [("pointer_move", {"x": 30, "y": 40})]

    def test_unchanged_position_emits_nothing(self, window) -> None:
        events, _ = window._poll_pointer_events(((30, 40), False, False), 30, 40, False, False, True)
        assert events == []

    def test_no_move_on_grab_frame(self, window) -> None:
        # Cursor moved AND left-pressed in one frame: only the grab is emitted
        # so it seeds its baseline without yanking the marker.
        events, _ = window._poll_pointer_events((None, False, False), 50, 60, True, False, True)
        assert events == [("pointer_down", {"x": 50, "y": 60, "button": 1})]

    def test_no_move_when_outside_canvas(self, window) -> None:
        events, _ = window._poll_pointer_events(((10, 10), False, False), 99, 99, False, False, False)
        assert events == []

    def test_held_button_is_not_a_new_press(self, window) -> None:
        events, _ = window._poll_pointer_events(((10, 10), True, False), 10, 10, True, False, True)
        assert events == []

    # --- poll_pointer integration (GDK read -> emit) --------------------

    def test_poll_pointer_is_noop_when_disabled(self, window) -> None:
        window._pointer_poll = False
        downs = self._handler_list(window, "pointer_down")
        window.poll_pointer()
        assert downs == []

    def test_poll_pointer_emits_grab_then_move(self, window) -> None:
        window._pointer_poll = True
        window._pointer_device = object()  # skip the lazy Gdk.Display acquire
        window._box._alloc_w = 1280
        window._box._alloc_h = 720
        window._window._gdk_window = _PollSurface(
            [
                (100, 200, _B1),  # left button down over canvas -> grab
                (140, 260, _B1),  # held + moved -> move
                (140, 260, 0),  # released -> no event (pointer_up is a no-op)
            ]
        )
        downs = self._handler_list(window, "pointer_down")
        moves = self._handler_list(window, "pointer_move")
        for _ in range(3):
            window.poll_pointer()
        assert downs == [{"x": 100, "y": 200, "button": 1}]
        assert moves == [{"x": 140, "y": 260}]

    def test_poll_pointer_disables_when_device_unavailable(self, window) -> None:
        # Lazy acquisition fails (FakeGdk has no Display) -> polling disables
        # itself rather than retrying every frame.
        window._pointer_poll = True
        window._pointer_device = None
        downs = self._handler_list(window, "pointer_down")
        window.poll_pointer()
        assert window._pointer_poll is False
        assert downs == []

    def test_poll_pointer_noop_when_window_not_realized(self, window) -> None:
        window._pointer_poll = True
        window._pointer_device = object()
        window._window._gdk_window = None  # pre-realize
        downs = self._handler_list(window, "pointer_down")
        window.poll_pointer()
        assert downs == []

    def test_poll_pointer_acquires_device_lazily(self, window, monkeypatch) -> None:
        class _Seat:
            def get_pointer(self) -> Any:
                return object()

        class _Display:
            @staticmethod
            def get_default() -> Any:
                return SimpleNamespace(get_default_seat=lambda: _Seat())

        monkeypatch.setattr(window._Gdk, "Display", _Display, raising=False)
        window._pointer_poll = True
        window._pointer_device = None
        window._box._alloc_w = 1280
        window._box._alloc_h = 720
        window._window._gdk_window = _PollSurface([(100, 200, _B1)])
        downs = self._handler_list(window, "pointer_down")
        window.poll_pointer()
        assert window._pointer_device is not None  # acquired on first poll
        assert downs == [{"x": 100, "y": 200, "button": 1}]

    def test_pointer_poll_error_in_tick_is_isolated(self, window, caplog) -> None:
        class _Raising:
            def get_device_position(self, _device: Any) -> Any:
                raise RuntimeError("poll boom")

        window._pointer_poll = True
        window._pointer_device = object()
        window._window._gdk_window = _Raising()
        window.start_tick_animation(lambda: None)
        import logging

        with caplog.at_level(logging.ERROR):
            assert window._window.tick() is True
        assert any("Pointer poll failed" in rec.message for rec in caplog.records)
