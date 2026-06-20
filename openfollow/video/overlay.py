# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Lightweight Cairo-based overlay renderer for GStreamer native sink mode."""

from __future__ import annotations

import logging
import time
from collections import deque
from pathlib import Path
from typing import Any

import cairo
import numpy as np
import numpy.typing as npt

from openfollow.runtime import overlay_draw_style as _style
from openfollow.runtime.overlay_draw_hud import (
    draw_about_overlay as draw_about_overlay_pass,
)
from openfollow.runtime.overlay_draw_hud import (
    draw_button_detection_overlay as draw_button_detection_overlay_pass,
)
from openfollow.runtime.overlay_draw_hud import (
    draw_field_choice_picker_overlay as draw_field_choice_picker_overlay_pass,
)
from openfollow.runtime.overlay_draw_hud import (
    draw_hud as draw_hud_pass,
)
from openfollow.runtime.overlay_draw_hud import (
    draw_iface_selection_overlay as draw_iface_selection_overlay_pass,
)
from openfollow.runtime.overlay_draw_hud import (
    draw_pi_network_field_edit_overlay as draw_pi_network_field_edit_overlay_pass,
)
from openfollow.runtime.overlay_draw_hud import (
    draw_pi_network_iface_picker_overlay as draw_pi_network_iface_picker_overlay_pass,
)
from openfollow.runtime.overlay_draw_hud import (
    draw_pi_network_method_picker_overlay as draw_pi_network_method_picker_overlay_pass,
)
from openfollow.runtime.overlay_draw_hud import (
    draw_pi_network_screen_overlay as draw_pi_network_screen_overlay_pass,
)
from openfollow.runtime.overlay_draw_hud import (
    draw_settings_overlay as draw_settings_overlay_pass,
)
from openfollow.runtime.overlay_draw_hud import (
    draw_source_selection_overlay as draw_source_selection_overlay_pass,
)
from openfollow.runtime.overlay_draw_hud import (
    draw_source_type_selection_overlay as draw_source_type_selection_overlay_pass,
)
from openfollow.runtime.overlay_draw_hud import (
    draw_url_editor_overlay as draw_url_editor_overlay_pass,
)
from openfollow.runtime.overlay_draw_messages import (
    draw_operator_messages as draw_operator_messages_pass,
)
from openfollow.runtime.overlay_draw_scene import (
    draw_detections as draw_detections_pass,
)
from openfollow.runtime.overlay_draw_scene import (
    draw_grid as draw_grid_pass,
)
from openfollow.runtime.overlay_draw_scene import (
    draw_marker as draw_marker_pass,
)
from openfollow.runtime.overlay_draw_scene import (
    draw_origin as draw_origin_pass,
)
from openfollow.runtime.overlay_draw_scene import (
    project as project_overlay_points,
)
from openfollow.runtime.overlay_draw_zones import draw_zones as draw_zones_pass
from openfollow.runtime.overlay_state import MarkerOverlayData, OverlayState

logger = logging.getLogger(__name__)

try:
    import gi as _gi

    _gi.require_version("Rsvg", "2.0")
    from gi.repository import Rsvg as _Rsvg

    _HAS_RSVG = True
except (ImportError, ValueError):
    _HAS_RSVG = False

_ICON_SVG_PATH = Path(__file__).parent.parent / "web" / "static" / "icon.svg"
_ICON_NATURAL_SIZE = 190.14  # SVG viewBox width

_LOGO_SVG_PATH = Path(__file__).parent.parent / "web" / "static" / "openfollow.svg"
_LOGO_NATURAL_W = 694.06  # SVG viewBox width
_LOGO_NATURAL_H = 189.96  # SVG viewBox height

# ==================================================================
# Color Palette (adapted from web UI)
# ==================================================================
# Background colors
COLOR_BG_BASE = _style.COLOR_BG_BASE
COLOR_BG_SOFT = _style.COLOR_BG_SOFT

# Text colors
COLOR_TEXT = _style.COLOR_TEXT
COLOR_TEXT_MUTED = _style.COLOR_TEXT_MUTED

# Accent (golden)
COLOR_ACCENT = _style.COLOR_ACCENT
COLOR_ACCENT_SOFT = _style.COLOR_ACCENT_SOFT

# Borders and UI
COLOR_BORDER_SOFT = _style.COLOR_BORDER_SOFT
COLOR_BORDER = _style.COLOR_BORDER

# Status indicators
COLOR_OK = _style.COLOR_OK
COLOR_DANGER = _style.COLOR_DANGER

# Typography
FONT_UI_FAMILY = _style.FONT_UI_FAMILY

# Cached toy font faces keyed by ``bold``. ``select_font_face``
# re-resolves the family string into a font face on every call, and the HUD
# calls ``_set_ui_font`` 30-50x per frame; a ``ToyFontFace`` built once and
# reused via ``set_font_face`` skips that re-resolution. Only two faces exist
# (normal + bold) and they're process-global constants, so a module-level
# cache is the natural home.
_UI_FONT_FACES: dict[bool, cairo.ToyFontFace] = {}


def _ui_font_face(bold: bool) -> cairo.ToyFontFace:
    face = _UI_FONT_FACES.get(bold)
    if face is None:
        weight = cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL
        face = cairo.ToyFontFace(
            FONT_UI_FAMILY,
            cairo.FONT_SLANT_NORMAL,
            weight,
        )
        _UI_FONT_FACES[bold] = face
    return face


class CairoOverlayRenderer:
    """Draws overlay elements using a Cairo context on top of video frames.

    Thread model:
    - The GTK main thread calls ``draw()`` once per display frame (from the
      ``DrawingArea`` "draw" signal), reading ``self.state`` and owning the
      per-frame memo caches – so neither needs locking.
    - Other threads only update ``self.state`` (a GIL-atomic attribute swap)
      and read ``measured_fps`` off the timestamp deque.
    """

    def __init__(self) -> None:
        self.state: OverlayState = OverlayState()
        # Pre-allocated buffer for grid points (reduces per-frame tuple allocation).
        # Max 200 lines = 400 endpoints. Resized if needed.
        self._grid_pts_buf = np.zeros((400, 3), dtype=np.float64)

        # Rolling timestamps of recent draw callbacks for FPS measurement.
        # Appended on the GTK main thread (per-frame ``draw``); read on the
        # Bottle thread (``measured_fps``). deque.append is GIL-atomic; the
        # FPS read is best-effort.
        self._frame_timestamps: deque[float] = deque(maxlen=90)

        # Per-frame HUD memo caches. The HUD draws on the GTK main thread only
        # (one ``draw`` per display frame), so these are single-threaded – no
        # locking. Each holds ``(key, value)`` and recomputes only when its
        # inputs change.
        #  - info panel: key = (ip, source, station, w, h)
        #  - help sections: key = the build_help_sections inputs
        self._info_panel_cache: tuple[Any, Any] | None = None
        self._help_sections_cache: tuple[Any, Any] | None = None

        # Optional SVG icon + wordmark-logo handles (require librsvg via
        # gi.repository.Rsvg). The logo is the About-screen headline.
        self._icon_handle = None
        self._logo_handle = None
        if _HAS_RSVG:
            try:
                self._icon_handle = _Rsvg.Handle.new_from_file(str(_ICON_SVG_PATH))
            except Exception as exc:
                logger.debug("Could not load overlay icon: %s", exc)
            try:
                self._logo_handle = _Rsvg.Handle.new_from_file(str(_LOGO_SVG_PATH))
            except Exception as exc:
                logger.debug("Could not load overlay logo: %s", exc)
        else:
            logger.info("Overlay icon disabled (Rsvg not available). Install gir1.2-rsvg-2.0 to enable it.")

    @staticmethod
    def _set_ui_font(cr: Any, size: float, bold: bool = False) -> None:
        # Reuse the cached toy font face instead of re-resolving the family
        # string on every call (runs 30-50x per frame).
        cr.set_font_face(_ui_font_face(bold))
        cr.set_font_size(size)

    def _draw_icon(self, cr: Any, x: float, y: float, size: float) -> None:
        """Render the OpenFollow SVG icon at (x, y) scaled to `size` pixels."""
        if self._icon_handle is None:
            return
        try:
            scale = size / _ICON_NATURAL_SIZE
            cr.save()
            cr.translate(x, y)
            cr.scale(scale, scale)
            self._icon_handle.render_cairo(cr)
        except Exception:
            pass
        finally:
            cr.restore()

    def _draw_logo(self, cr: Any, x: float, y: float, width: float) -> float:
        """Render the OpenFollow wordmark logo with its top-left at (x, y),
        scaled to ``width`` px (aspect preserved). Returns the rendered height
        so the caller can advance its layout cursor; 0.0 if no logo is loaded."""
        if self._logo_handle is None:
            return 0.0
        scale = width / _LOGO_NATURAL_W
        try:
            cr.save()
            cr.translate(x, y)
            cr.scale(scale, scale)
            self._logo_handle.render_cairo(cr)
        except Exception:
            return 0.0
        finally:
            cr.restore()
        return _LOGO_NATURAL_H * scale

    @staticmethod
    def _truncate_text_to_width(cr: Any, text: str, max_width: float) -> str:
        if cr.text_extents(text).width <= max_width:
            return text
        ellipsis = "..."
        if cr.text_extents(ellipsis).width > max_width:
            return ""
        end = len(text)
        while end > 0:
            candidate = text[:end].rstrip() + ellipsis
            if cr.text_extents(candidate).width <= max_width:
                return candidate
            end -= 1
        return ellipsis

    # ------------------------------------------------------------------
    # Public entry point (``draw`` is called on the GTK main thread, once
    # per display frame, from the overlay DrawingArea's "draw" signal)
    # ------------------------------------------------------------------

    def measured_fps(self) -> float:
        """Return recent-window FPS measured from HUD draw callbacks.

        Safe to call from any thread; reads the rolling timestamp deque.
        Returns 0.0 until enough samples accumulate or if the window has
        stalled (last sample older than 2 s). Note: this is the HUD redraw
        rate (display tick), not the source video framerate.
        """
        ts = list(self._frame_timestamps)
        if len(ts) < 2:
            return 0.0
        now = time.monotonic()
        if now - ts[-1] > 2.0:
            return 0.0
        span = ts[-1] - ts[0]
        if span <= 0.0:
            return 0.0
        return (len(ts) - 1) / span

    def draw(self, cr: Any, width: int, height: int) -> None:
        try:
            self._frame_timestamps.append(time.monotonic())
            state = self.state
            source_selection_active = state.source_selection_active

            # Button detection wizard takes exclusive visual control
            if state.button_detection is not None and state.button_detection.active:
                self._draw_button_detection_overlay(cr, state, width, height)
                return

            # Settings menu takes priority over other selection overlays.
            if state.settings_menu_active:
                self._draw_settings_overlay(cr, state, width, height)
                return

            # About / license screen: opened from the Settings menu,
            # same modal-priority slot.
            if state.about_active:
                self._draw_about_overlay(cr, state, width, height)
                return

            # Network screens: same modal-priority slot as iface / source-type.
            # Deeper sub-states first.
            net = state.pi_network
            if net.field_edit_active:
                self._draw_pi_network_field_edit_overlay(cr, state, width, height)
                return
            if net.method_picker_active:
                self._draw_pi_network_method_picker_overlay(cr, state, width, height)
                return
            if net.iface_picker_active:
                self._draw_pi_network_iface_picker_overlay(cr, state, width, height)
                return
            if net.screen_active:
                self._draw_pi_network_screen_overlay(cr, state, width, height)
                return

            # Interface selection takes priority – must work in any state
            if state.iface_selection_active:
                self._draw_iface_selection_overlay(cr, state, width, height)
                return

            # Source-type selection – same modal-priority slot as iface
            # selection so it isn't masked by a "No Signal" frame when
            # the prior plugin failed to start.
            if state.source_type_selection_active:
                self._draw_source_type_selection_overlay(cr, state, width, height)
                return

            # URL editor – auto-chained from the source-type picker on
            # empty-URL failure or opened from the Settings menu's
            # "Edit Video URL" item. Shares the same priority slot so the
            # editor remains visible while the operator types over a
            # "No Signal" backdrop.
            if state.url_editor_active:
                self._draw_url_editor_overlay(cr, state, width, height)
                return

            # Field-choice picker – enum-style sibling to the URL
            # editor, opened for plugin fields whose valid values are
            # a small fixed set (testpattern grey/stage). Same modal
            # priority so it stays visible over any backdrop.
            if state.field_choice_active:
                self._draw_field_choice_picker_overlay(cr, state, width, height)
                return

            # Video disconnects no longer render as their own overlay
            # – ``check_video_disconnect_banner`` routes operators into
            # the Settings menu with a banner carrying every field the
            # old "No Signal" overlay shows. The HUD keeps drawing under
            # the (potentially frozen) last video frame; web-UI / log
            # surfaces still expose ``video_connected = False`` for ops
            # dashboards.

            # Draw source selection overlay on top of video if active
            if source_selection_active:
                self._draw_source_selection_overlay(cr, state, width, height)
                return

            if state.camera_params is None:
                self._draw_hud(cr, state, width, height)
                return

            cr.save()
            try:
                self._draw_grid(cr, state, width, height)
                self._draw_origin(cr, state, width, height)
                self._draw_zones(cr, state, width, height)
                for t in state.markers:
                    self._draw_marker(cr, state, t, width, height)
                if state.detections and state.detection_show_boxes:
                    self._draw_detections(cr, state, width, height)
                self._draw_hud(cr, state, width, height)
            finally:
                # Balance the save even if a draw raised, so the "Overlay
                # Error" fallback below paints on a clean state (markers now
                # also pop their own group/clip – see draw_marker_card).
                cr.restore()
        except Exception as e:
            # Don't crash GStreamer thread on drawing errors
            logger.warning("Cairo overlay draw error: %s", e)
            # Fall back to simple error message
            try:
                cr.set_source_rgba(0.8, 0.2, 0.2, 1.0)
                self._set_ui_font(cr, 16)
                cr.move_to(10, 30)
                cr.show_text("Overlay Error - See Logs")
            except Exception:
                pass  # If even basic drawing fails, just give up

    # ------------------------------------------------------------------
    # 3D projection helper
    # ------------------------------------------------------------------

    @staticmethod
    def _project(
        cam: npt.NDArray[Any],
        pts_psn: list[Any] | npt.NDArray[Any],
        w: int,
        h: int,
    ) -> npt.NDArray[Any]:
        return project_overlay_points(cam, pts_psn, w, h)

    @staticmethod
    def _visible(scr: npt.NDArray[Any], w: int, h: int) -> bool:
        """True if all projected points are finite and within a generous margin."""
        if not np.all(np.isfinite(scr)):
            return False
        margin = max(w, h)
        return bool(
            np.all(scr[:, 0] > -margin)
            and np.all(scr[:, 0] < w + margin)
            and np.all(scr[:, 1] > -margin)
            and np.all(scr[:, 1] < h + margin)
        )

    # ------------------------------------------------------------------
    # Grid
    # ------------------------------------------------------------------

    def _draw_grid(self, cr: Any, state: OverlayState, w: int, h: int) -> None:
        draw_grid_pass(self, cr, state, w, h)

    # ------------------------------------------------------------------
    # Origin marker (RGB axis lines at world origin)
    # ------------------------------------------------------------------

    def _draw_origin(self, cr: Any, state: OverlayState, w: int, h: int) -> None:
        draw_origin_pass(cr, state, w, h)

    # ------------------------------------------------------------------
    # Trigger zones (polygon overlays)
    # ------------------------------------------------------------------

    def _draw_zones(self, cr: Any, state: OverlayState, w: int, h: int) -> None:
        draw_zones_pass(self, cr, state, w, h)

    # ------------------------------------------------------------------
    # Person detection bounding boxes
    # ------------------------------------------------------------------

    def _draw_detections(self, cr: Any, state: OverlayState, w: int, h: int) -> None:
        draw_detections_pass(self, cr, state, w, h)

    # ------------------------------------------------------------------
    # Marker (circle + crosshair + drop line)
    # ------------------------------------------------------------------

    def _draw_marker(self, cr: Any, state: OverlayState, t: MarkerOverlayData, w: int, h: int) -> None:
        draw_marker_pass(cr, state, t, w, h)

    # ------------------------------------------------------------------
    # Source-selection / iface / settings / browser / URL-editor overlays
    # ------------------------------------------------------------------

    def _draw_source_selection_overlay(self, cr: Any, state: OverlayState, w: int, h: int) -> None:
        draw_source_selection_overlay_pass(self, cr, state, w, h)

    def _draw_iface_selection_overlay(self, cr: Any, state: OverlayState, w: int, h: int) -> None:
        draw_iface_selection_overlay_pass(self, cr, state, w, h)

    def _draw_source_type_selection_overlay(
        self,
        cr: Any,
        state: OverlayState,
        w: int,
        h: int,
    ) -> None:
        draw_source_type_selection_overlay_pass(self, cr, state, w, h)

    def _draw_url_editor_overlay(
        self,
        cr: Any,
        state: OverlayState,
        w: int,
        h: int,
    ) -> None:
        draw_url_editor_overlay_pass(self, cr, state, w, h)

    def _draw_field_choice_picker_overlay(
        self,
        cr: Any,
        state: OverlayState,
        w: int,
        h: int,
    ) -> None:
        draw_field_choice_picker_overlay_pass(self, cr, state, w, h)

    def _draw_settings_overlay(self, cr: Any, state: OverlayState, w: int, h: int) -> None:
        draw_settings_overlay_pass(self, cr, state, w, h)

    def _draw_about_overlay(self, cr: Any, state: OverlayState, w: int, h: int) -> None:
        draw_about_overlay_pass(self, cr, state, w, h)

    # ------------------------------------------------------------------
    # Network screens
    # ------------------------------------------------------------------

    def _draw_pi_network_screen_overlay(
        self,
        cr: Any,
        state: OverlayState,
        w: int,
        h: int,
    ) -> None:
        draw_pi_network_screen_overlay_pass(self, cr, state, w, h)

    def _draw_pi_network_iface_picker_overlay(
        self,
        cr: Any,
        state: OverlayState,
        w: int,
        h: int,
    ) -> None:
        draw_pi_network_iface_picker_overlay_pass(self, cr, state, w, h)

    def _draw_pi_network_method_picker_overlay(
        self,
        cr: Any,
        state: OverlayState,
        w: int,
        h: int,
    ) -> None:
        draw_pi_network_method_picker_overlay_pass(self, cr, state, w, h)

    def _draw_pi_network_field_edit_overlay(
        self,
        cr: Any,
        state: OverlayState,
        w: int,
        h: int,
    ) -> None:
        draw_pi_network_field_edit_overlay_pass(self, cr, state, w, h)

    # ------------------------------------------------------------------
    # Button Detection Wizard
    # ------------------------------------------------------------------

    def _draw_button_detection_overlay(self, cr: Any, state: OverlayState, w: int, h: int) -> None:
        draw_button_detection_overlay_pass(self, cr, state, w, h)

    # ------------------------------------------------------------------
    # HUD (2D screen-space)
    # ------------------------------------------------------------------

    def _draw_hud(self, cr: Any, state: OverlayState, w: int, h: int) -> None:
        draw_hud_pass(self, cr, state, w, h)
        # Operator-message cards on the normal-HUD layer.
        draw_operator_messages_pass(self, cr, state, w, h)
