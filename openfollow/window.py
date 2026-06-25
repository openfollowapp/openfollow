# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""GTK 3 window abstraction for GStreamer native-sink mode.

``GtkNativeSinkWindow`` wraps a GTK overlay hosting the gtksink video
widget plus a pass-through HUD DrawingArea, exposing a
RenderCanvas-compatible event API and a display-tick animation loop.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from typing import Any

from openfollow.logging_setup import ThrottledExceptionLogger

logger = logging.getLogger(__name__)

# GDK key name → rendercanvas-style key name
_GDK_KEY_MAP: dict[str, str] = {
    "Return": "Enter",
    "KP_Enter": "Enter",
    "Escape": "Escape",
    "Tab": "Tab",
    "Shift_L": "Shift",
    "Shift_R": "Shift",
    "Control_L": "Control",
    "Control_R": "Control",
    "Super_L": "Meta",
    "Super_R": "Meta",
    "Meta_L": "Meta",
    "Meta_R": "Meta",
    "Alt_L": "Alt",
    "Alt_R": "Alt",
    "Up": "ArrowUp",
    "Down": "ArrowDown",
    "Left": "ArrowLeft",
    "Right": "ArrowRight",
    "space": " ",
    "BackSpace": "Backspace",
    "Delete": "Delete",
    # Numeric keypad (digit keys only; operators not mapped).
    "KP_0": "Numpad0",
    "KP_1": "Numpad1",
    "KP_2": "Numpad2",
    "KP_3": "Numpad3",
    "KP_4": "Numpad4",
    "KP_5": "Numpad5",
    "KP_6": "Numpad6",
    "KP_7": "Numpad7",
    "KP_8": "Numpad8",
    "KP_9": "Numpad9",
    # URL-relevant punctuation (1-char keys for URL editor).
    "slash": "/",
    "backslash": "\\",
    "colon": ":",
    "semicolon": ";",
    "period": ".",
    "comma": ",",
    "minus": "-",
    "underscore": "_",
    "at": "@",
    "question": "?",
    "equal": "=",
    "plus": "+",
    "exclam": "!",
    "asciitilde": "~",
    "asciicircum": "^",
    "ampersand": "&",
    "asterisk": "*",
    "percent": "%",
    "dollar": "$",
    "numbersign": "#",
    "bar": "|",
    "parenleft": "(",
    "parenright": ")",
    "bracketleft": "[",
    "bracketright": "]",
    "braceleft": "{",
    "braceright": "}",
    "less": "<",
    "greater": ">",
    "apostrophe": "'",
    "quotedbl": '"',
    "grave": "`",
}


class GtkNativeSinkWindow:
    """GTK 3 window for GStreamer native sink mode.

    Provides a RenderCanvas-compatible event API so existing event-handler
    wiring in the app works unchanged.  The ``gtksink`` video widget is
    embedded via ``embed_widget()``.
    """

    def __init__(self, width: int, height: int, title: str = "OpenFollow") -> None:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk, Gtk

        Gtk.init(None)

        self._Gtk = Gtk
        self._Gdk = Gdk

        self._window = Gtk.Window(title=title)
        self._window.set_default_size(width, height)

        self._box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._window.add(self._box)

        # Video in main slot; HUD overlay drawn above (decoupled from video).
        self._overlay = Gtk.Overlay()
        self._box.pack_start(self._overlay, True, True, 0)

        self._hud_drawing_area = Gtk.DrawingArea()
        self._hud_drawing_area.set_can_focus(False)
        self._hud_drawing_area.set_focus_on_click(False)
        self._hud_drawing_area.connect("draw", self._on_hud_draw)
        self._overlay.add_overlay(self._hud_drawing_area)
        self._overlay.set_overlay_pass_through(self._hud_drawing_area, True)
        self._hud_draw_fn: Callable[[Any, int, int], None] | None = None

        self._handlers: dict[str, list[Callable[[dict[str, Any]], None]]] = {}
        self._tick_callback_id: int | None = None
        self._tick_fn: Callable[[], None] | None = None
        self._closing = False
        self._overlay_widget: Any = None  # Currently-mounted overlay widget
        self._base_pointer_visible: bool | None = None  # Pointer visibility when no overlay
        # Tracked (widget, handler_id) for the realize→grab_focus handlers so a
        # re-embed / re-mount disconnects the prior one instead of stacking.
        self._embed_realize: tuple[Any, int] | None = None
        self._overlay_realize: tuple[Any, int] | None = None
        # macOS doesn't reliably deliver GTK pointer events to the gtksink-hosted
        # window under the GStreamer pipeline (the same reason the keyboard polls
        # Quartz instead of reading GTK key events). Poll the pointer position +
        # button state once per frame there and synthesise the same pointer
        # events; a no-op elsewhere, where the GTK signal handlers work.
        self._pointer_poll = sys.platform == "darwin"
        self._pointer_device: Any = None  # lazily acquired on the first poll
        self._poll_last_pos: tuple[int, int] | None = None
        self._poll_btn1 = False
        self._poll_btn3 = False
        # The pointer poll runs every frame; throttle its failure log so a
        # persistent error can't flood the journal at the display tick rate.
        self._poll_err_log = ThrottledExceptionLogger(logger, "Pointer poll failed")
        self._setup_events()
        self._window.show_all()

    # -- RenderCanvas-compatible API ------------------------------------------

    def add_event_handler(
        self,
        handler: Callable[[dict[str, Any]], None],
        event_type: str,
    ) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    def request_draw(self, fn: Callable[[], None]) -> None:
        pass  # animation driven by tick callback

    def close(self) -> None:
        self._closing = True
        self.stop_tick_animation()
        self._Gtk.main_quit()

    # -- Extended API ---------------------------------------------------------

    def embed_widget(self, widget: Any) -> None:
        """Add, replace, or clear the video widget as the overlay's main child."""
        if widget is None:
            # Explicit clear: drop the stale widget so the canvas blanks rather
            # than keeping the last frame of a torn-down pipeline mounted.
            existing = self._overlay.get_child()
            if existing is not None:
                self._overlay.remove(existing)
                self._overlay.queue_draw()
            self._clear_realize("_embed_realize")
            return

        # Same widget re-submitted on a pipeline rebuild (NULL→PLAYING reuses
        # the shared sink). The HUD redraw path is decoupled from the video, so
        # nothing else invalidates the sink region – repaint it here. Keep the
        # fast path (no remove/add) to avoid the macOS reparent flash.
        if widget.get_parent() is self._overlay:
            widget.queue_draw()
            self._overlay.queue_draw()
            return

        # Remove existing main child (HUD area lives in add_overlay() slot).
        existing = self._overlay.get_child()
        if existing is not None:
            self._overlay.remove(existing)

        # Detach from any prior parent before re-parenting.
        current_parent = widget.get_parent()
        if current_parent is not None:
            current_parent.remove(widget)

        # Prevent video widget from stealing focus; re-grab after realized.
        widget.set_can_focus(False)
        widget.set_focus_on_click(False)
        self._overlay.add(widget)
        widget.show()
        self._track_realize("_embed_realize", widget, lambda w: self._window.grab_focus())
        self._window.grab_focus()

    def add_overlay_widget(self, widget: Any) -> None:
        """Attach widget as fullscreen overlay above HUD layer."""
        if widget is None:
            return
        current_parent = widget.get_parent()
        if current_parent is not None:
            current_parent.remove(widget)
        # Required for GTK to route keystrokes into the widget.
        widget.set_can_focus(True)
        widget.set_focus_on_click(True)
        self._track_realize("_overlay_realize", widget, lambda w: w.grab_focus())
        self._overlay.add_overlay(widget)
        widget.show()
        widget.grab_focus()
        # Mark as overlay-owned so key dispatcher lets GTK handle keystrokes.
        self._overlay_widget = widget
        # Show cursor for interactive overlay (Wayland/Cage requirement).
        self._set_pointer_visible(True)

    def remove_overlay_widget(self, widget: Any) -> None:
        """Detach widget and re-grab focus onto toplevel window."""
        if widget is None:
            return
        if widget.get_parent() is self._overlay:
            self._overlay.remove(widget)
            # Only drop the tracked realize handler when it belongs to the
            # widget being removed – removing a stale/older overlay must not
            # disconnect the currently-active overlay's focus handler.
            if self._overlay_realize is not None and self._overlay_realize[0] is widget:
                self._clear_realize("_overlay_realize")
            self._window.grab_focus()
        if self._overlay_widget is widget:
            self._overlay_widget = None
            # Restore pointer state (hidden for disabled mouse, arrow when enabled).
            self._set_pointer_visible(bool(self._base_pointer_visible))

    def _track_realize(self, slot: str, widget: Any, callback: Callable[[Any], None]) -> None:
        """Connect a ``realize`` handler, disconnecting any previously tracked
        one in ``slot`` first so repeated embeds/mounts don't stack handlers."""
        self._clear_realize(slot)
        handler_id = widget.connect("realize", callback)
        setattr(self, slot, (widget, handler_id))

    def _clear_realize(self, slot: str) -> None:
        """Disconnect and forget the realize handler tracked in ``slot``."""
        tracked = getattr(self, slot)
        if tracked is not None:
            widget, handler_id = tracked
            # GTK can already have auto-disconnected the handler during widget
            # teardown; mirror the defensive disconnect used elsewhere so a
            # clear on a tearing-down widget can't crash embed/overlay paths.
            try:
                widget.disconnect(handler_id)
            except Exception:  # noqa: BLE001
                import logging

                logging.getLogger(__name__).exception("Failed to disconnect realize handler")
            setattr(self, slot, None)

    def set_pointer_base_visible(self, visible: bool) -> None:
        """Set pointer visibility when no overlay mounted (driven by mouse_enabled)."""
        if visible == self._base_pointer_visible:
            return
        self._base_pointer_visible = visible
        # Overlay forces pointer visible while mounted; only apply when no overlay.
        if self._overlay_widget is None:
            self._set_pointer_visible(visible)

    def _set_pointer_visible(self, visible: bool) -> None:
        """Show or hide the mouse pointer."""
        gdk_window = self._window.get_window()
        if gdk_window is None:
            return
        display = self._window.get_display()
        if not visible:
            # Use explicit blank cursor, not None (which inherits parent on X11/Wayland).
            blank: Any = self._Gdk.Cursor.new_from_name(display, "none")
            if blank is None:
                try:
                    blank = self._Gdk.Cursor.new_for_display(display, self._Gdk.CursorType.BLANK_CURSOR)
                except Exception:  # noqa: BLE001
                    blank = None
            gdk_window.set_cursor(blank)
            return
        cursor: Any = None
        # Try common cursor names, fall back to legacy enum.
        for cursor_name in ("default", "left_ptr", "arrow"):
            cursor = self._Gdk.Cursor.new_from_name(display, cursor_name)
            if cursor is not None:
                break
        if cursor is None:
            # Legacy enum (works without theme directory).
            try:
                cursor = self._Gdk.Cursor.new_for_display(
                    display,
                    self._Gdk.CursorType.LEFT_PTR,
                )
            except Exception:  # noqa: BLE001
                import logging

                logging.getLogger(__name__).warning(
                    "No cursor theme available; pointer will stay invisible. Install adwaita-icon-theme on this host.",
                )
                return
        gdk_window.set_cursor(cursor)

    def attach_hud(self, draw_fn: Callable[[Any, int, int], None]) -> None:
        """Register HUD draw callback invoked once per display frame."""
        self._hud_draw_fn = draw_fn
        self._hud_drawing_area.queue_draw()

    def _on_hud_draw(self, widget: Any, cr: Any) -> bool:
        if self._hud_draw_fn is None:
            return False
        alloc = widget.get_allocation()
        try:
            self._hud_draw_fn(cr, alloc.width, alloc.height)
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Unhandled exception in HUD draw")
        return False

    def set_title(self, title: str) -> None:
        self._window.set_title(title)

    def set_aspect_ratio(self, w: int, h: int) -> None:
        geometry = self._Gdk.Geometry()
        geometry.min_aspect = w / h
        geometry.max_aspect = w / h
        self._window.set_geometry_hints(None, geometry, self._Gdk.WindowHints.ASPECT)

    def fullscreen(self) -> None:
        self._window.fullscreen()

    def apply_window_size(self, width: int, height: int) -> None:
        """Resize live window to (width, height) pixels."""
        self._window.set_default_size(width, height)
        self._window.resize(width, height)

    def get_canvas_size(self) -> tuple[int, int]:
        """Return current window client-area size in pixels."""
        w = self._box.get_allocated_width()
        h = self._box.get_allocated_height()
        if w > 1 and h > 1:
            return w, h
        size = self._window.get_size()
        return int(size[0]), int(size[1])

    # -- Tick-based animation -------------------------------------------------

    def start_tick_animation(self, callback: Callable[[], None]) -> None:
        """Start frame-synced animation using GTK tick callback."""
        if self._tick_callback_id is not None:
            return  # already running
        self._tick_fn = callback
        self._tick_callback_id = self._window.add_tick_callback(self._on_tick, None)

    def stop_tick_animation(self) -> None:
        """Stop the tick-based animation loop."""
        if self._tick_callback_id is not None:
            self._window.remove_tick_callback(self._tick_callback_id)
            self._tick_callback_id = None
            self._tick_fn = None

    @staticmethod
    def _poll_pointer_events(
        state: tuple[tuple[int, int] | None, bool, bool],
        x: int,
        y: int,
        b1: bool,
        b3: bool,
        within: bool,
    ) -> tuple[list[tuple[str, dict[str, Any]]], tuple[tuple[int, int], bool, bool]]:
        """Pure edge-detection for the macOS pointer poll.

        Given the previous ``(last_pos, last_b1, last_b3)`` and the freshly polled
        position / button state, return the pointer events to emit and the new
        state. Grab / release / reset all happen on a button *press*, so only
        down edges over the canvas (``within``) are emitted; a move on the same
        frame as a grab is suppressed so the grab seeds its baseline without
        yanking the marker.
        """
        last_pos, last_b1, last_b3 = state
        events: list[tuple[str, dict[str, Any]]] = []
        b1_grab = b1 and not last_b1 and within
        if b1_grab:
            events.append(("pointer_down", {"x": x, "y": y, "button": 1}))
        if b3 and not last_b3 and within:
            events.append(("pointer_down", {"x": x, "y": y, "button": 3}))
        if within and (x, y) != last_pos and not b1_grab:
            events.append(("pointer_move", {"x": x, "y": y}))
        return events, ((x, y), b1, b3)

    def _acquire_pointer_device(self) -> Any:
        try:
            seat = self._Gdk.Display.get_default().get_default_seat()
        except Exception:
            return None
        return seat.get_pointer() if seat is not None else None

    def poll_pointer(self) -> None:
        """macOS: synthesise pointer events from a per-frame position/button poll.

        No-op where GTK delivers pointer events (Linux/Pi). The emitted events go
        through the same ``_emit`` path as the GTK signal handlers, so all
        downstream gating and ``MouseHandler`` behaviour is unchanged.
        """
        if not self._pointer_poll:
            return
        if self._pointer_device is None:
            self._pointer_device = self._acquire_pointer_device()
            if self._pointer_device is None:
                self._pointer_poll = False  # unavailable – stop polling
                return
        gwin = self._window.get_window()
        if gwin is None:
            return
        _w, x, y, mask = gwin.get_device_position(self._pointer_device)
        Gdk = self._Gdk
        b1 = bool(mask & Gdk.ModifierType.BUTTON1_MASK)
        b3 = bool(mask & Gdk.ModifierType.BUTTON3_MASK)
        cw, ch = self.get_canvas_size()
        within = 0 <= x < cw and 0 <= y < ch
        events, new_state = self._poll_pointer_events(
            (self._poll_last_pos, self._poll_btn1, self._poll_btn3), x, y, b1, b3, within
        )
        self._poll_last_pos, self._poll_btn1, self._poll_btn3 = new_state
        for etype, kwargs in events:
            self._emit(etype, **kwargs)

    def _on_tick(self, widget: Any, frame_clock: Any, user_data: Any) -> bool:
        """GTK tick callback – runs once per display frame."""
        if self._closing:
            return False
        try:
            self.poll_pointer()
        except Exception:
            self._poll_err_log.log()
        if self._tick_fn is not None:
            try:
                self._tick_fn()
            except Exception:
                import logging

                logging.getLogger(__name__).exception("Unhandled exception in animation tick")
        # Redraw HUD independent of video buffer flow.
        if self._hud_draw_fn is not None:
            self._hud_drawing_area.queue_draw()
        # Keep window focus.
        if not self._window.has_toplevel_focus():
            self._window.grab_focus()
        return True

    # -- Internal event wiring ------------------------------------------------

    def _emit(self, event_type: str, **kwargs: Any) -> None:
        for h in self._handlers.get(event_type, []):
            h(kwargs)

    def _setup_events(self) -> None:
        Gdk = self._Gdk
        self._window.add_events(
            Gdk.EventMask.KEY_PRESS_MASK
            | Gdk.EventMask.KEY_RELEASE_MASK
            | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.SMOOTH_SCROLL_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        self._window.connect("key-press-event", self._on_key_press)
        self._window.connect("key-release-event", self._on_key_release)
        self._window.connect("scroll-event", self._on_scroll)
        self._window.connect("button-press-event", self._on_button_press)
        self._window.connect("button-release-event", self._on_button_release)
        self._window.connect("motion-notify-event", self._on_motion)
        self._window.connect("configure-event", self._on_configure)
        self._window.connect("delete-event", self._on_delete)
        self._window.connect("focus-out-event", self._on_focus_out)

    @staticmethod
    def _translate_key(event: Any) -> str:
        from gi.repository import Gdk

        name = Gdk.keyval_name(event.keyval)
        if name is None:
            return ""
        mapped = _GDK_KEY_MAP.get(name)
        if mapped:
            return mapped
        if len(name) == 1:
            return str(name).lower()
        return str(name)

    def _on_key_press(self, widget: Any, event: Any) -> bool:
        name = self._translate_key(event)
        if name:
            self._emit("key_down", key=name)
        # Return False when overlay mounted to dispatch keys to it.
        return self._overlay_widget is None

    def _on_key_release(self, widget: Any, event: Any) -> bool:
        name = self._translate_key(event)
        if name:
            self._emit("key_up", key=name)
        return self._overlay_widget is None

    @staticmethod
    def _scroll_is_emulated(event: Any) -> bool:
        """True if a discrete scroll event is a smooth-scroll duplicate.

        GDK marks the legacy UP/DOWN event it synthesises alongside each smooth
        notch as pointer-emulated. Dropping those (regardless of arrival order)
        is what stops a notch from moving Z twice on a smooth-capable device,
        while leaving a genuine discrete-only device (not emulated) working.
        Defaults to ``True`` (treat as a duplicate) when the GDK accessor is
        unavailable, preserving the smooth-authoritative behaviour.
        """
        getter = getattr(event, "get_pointer_emulated", None)
        if getter is None:
            return True
        try:
            return bool(getter())
        except Exception:  # pragma: no cover - defensive: GDK accessor failure
            return True

    def _on_scroll(self, widget: Any, event: Any) -> bool:
        Gdk = self._Gdk
        ok, _dx, dy = event.get_scroll_deltas()
        if ok:
            # Smooth event – authoritative. Collapsed to a single unit tick so
            # one notch == one ``mouse_wheel_z_step`` regardless of the device's
            # reported magnitude (some report e.g. 1.5/notch, which would
            # otherwise scale the step). GTK dy > 0 is scroll *down* → emit -1
            # (lower); dy < 0 is up → +1 (raise). dy == 0 is horizontal → no tick.
            if dy:
                self._emit("wheel", dy=-1.0 if dy > 0 else 1.0)
            return True
        # Legacy discrete event. On a smooth-capable device GDK emits one of
        # these per notch *in addition* to the smooth event, flagged as
        # pointer-emulated; that duplicate is dropped so the notch isn't counted
        # twice. A genuine discrete-only device (no smooth events) is not
        # emulated, so it drives wheel-Z here.
        if self._scroll_is_emulated(event):
            return True
        if event.direction == Gdk.ScrollDirection.UP:
            self._emit("wheel", dy=1.0)
        elif event.direction == Gdk.ScrollDirection.DOWN:
            self._emit("wheel", dy=-1.0)
        return True

    def _on_button_press(self, widget: Any, event: Any) -> bool:
        if self._pointer_poll:
            return True  # macOS: poll is authoritative – see _on_motion
        self._emit("pointer_down", x=event.x, y=event.y, button=int(event.button))
        return True

    def _on_button_release(self, widget: Any, event: Any) -> bool:
        if self._pointer_poll:
            return True  # macOS: poll is authoritative – see _on_motion
        self._emit("pointer_up", x=event.x, y=event.y, button=int(event.button))
        return True

    def _on_motion(self, widget: Any, event: Any) -> bool:
        if self._pointer_poll:
            # macOS: the per-frame poll is the authoritative pointer source. GTK also
            # delivers these intermittently here, and a duplicated right-click reads as a
            # double-click (reset). Defer so one physical click is one event.
            return True
        self._emit("pointer_move", x=event.x, y=event.y)
        return True

    def _on_configure(self, widget: Any, event: Any) -> bool:
        self._emit("resize", width=event.width, height=event.height)
        return False

    def _on_delete(self, widget: Any, event: Any) -> bool:
        self._closing = True
        self.stop_tick_animation()
        self._emit("close")
        self._Gtk.main_quit()
        return True

    def _on_focus_out(self, widget: Any, event: Any) -> bool:
        # Toplevel lost keyboard focus – emit a blur so input handlers can drop
        # any held key (it can't be released into a window that no longer has
        # focus). On a single-window kiosk this rarely fires; the in-app modal
        # transitions are the primary held-key recovery.
        self._emit("blur")
        return False
