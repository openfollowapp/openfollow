# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for :class:`CairoOverlayRenderer` dispatch + helpers.

``CairoOverlayRenderer.draw()`` is the routing boundary between
``OverlayState`` flags (video/wizard/etc) and the actual drawing passes.
These tests patch every ``_pass`` function imported at module load time
and assert the correct one is invoked for each state shape, so we
verify the dispatch contract without rendering real pixels.

``_visible()`` and ``measured_fps()`` are pure helpers tested directly.
"""

from __future__ import annotations

import re

import numpy as np
import pytest

from openfollow.runtime.overlay_state import (
    ButtonDetectionState,
    MarkerOverlayData,
)
from openfollow.video import overlay as overlay_module
from openfollow.video.overlay import CairoOverlayRenderer
from tests._fake_cairo import FakeCairo as RecordingCairo

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class FakeCairo:
    """Minimal Cairo surface stand-in.

    Only ``draw()`` uses ``save`` / ``restore`` / ``show_text`` etc. on
    the outer object – the inner drawing passes we patch out for these
    dispatch tests.  The only method that still runs against this fake
    is the fallback "Overlay Error" branch, which uses the small subset
    recorded here.
    """

    def __init__(self) -> None:
        self.saves = 0
        self.restores = 0
        self.text_calls: list[str] = []
        self.move_tos: list[tuple[float, float]] = []
        self.rgba_calls: list[tuple[float, ...]] = []
        self.font_size_calls: list[float] = []
        self.translates: list[tuple[float, float]] = []
        self.clip_rects: list[tuple[float, float, float, float]] = []
        self._path_rect: tuple[float, float, float, float] | None = None

    def save(self) -> None:
        self.saves += 1

    def restore(self) -> None:
        self.restores += 1

    def translate(self, x: float, y: float) -> None:
        self.translates.append((x, y))

    def rectangle(self, x: float, y: float, w: float, h: float) -> None:
        self._path_rect = (x, y, w, h)

    def clip(self) -> None:
        assert self._path_rect is not None, "clip() without a path"
        self.clip_rects.append(self._path_rect)
        self._path_rect = None

    def set_source_rgba(self, *args: float) -> None:
        self.rgba_calls.append(args)

    def select_font_face(self, *args) -> None:
        pass

    def set_font_face(self, *args) -> None:
        pass

    def set_font_size(self, size: float) -> None:
        self.font_size_calls.append(size)

    def move_to(self, x: float, y: float) -> None:
        self.move_tos.append((x, y))

    def show_text(self, text: str) -> None:
        self.text_calls.append(text)


@pytest.fixture
def patched_passes(monkeypatch):
    """Replace every ``*_pass`` entry point with a recording stub."""
    calls: list[str] = []

    def _record(name: str):
        def _stub(*args, **kwargs) -> None:
            calls.append(name)

        return _stub

    for name in (
        "draw_about_overlay_pass",
        "draw_media_picker_overlay_pass",
        "draw_media_export_overlay_pass",
        "draw_menu_help_pass",
        "draw_button_detection_overlay_pass",
        "draw_hud_pass",
        "draw_settings_overlay_pass",
        "draw_source_selection_overlay_pass",
        "draw_source_type_selection_overlay_pass",
        "draw_url_editor_overlay_pass",
        "draw_field_choice_picker_overlay_pass",
        "draw_pi_network_screen_overlay_pass",
        "draw_pi_network_field_edit_overlay_pass",
        "draw_detections_pass",
        "draw_grid_pass",
        "draw_origin_pass",
        "draw_marker_pass",
        "draw_zones_pass",
    ):
        monkeypatch.setattr(overlay_module, name, _record(name))
    return calls


@pytest.fixture
def pass_sizes(monkeypatch):
    """Record the ``(w, h)`` each scene / HUD pass is handed, keyed by name."""
    sizes: dict[str, list[tuple[int, int]]] = {}

    def _record(name: str):
        def _stub(*args, **kwargs) -> None:
            # Every pass takes its canvas as the two ints among its arguments.
            ints = tuple(a for a in args if type(a) is int)
            sizes.setdefault(name, []).append((ints[0], ints[1]))

        return _stub

    for name in (
        "draw_hud_pass",
        "draw_detections_pass",
        "draw_grid_pass",
        "draw_origin_pass",
        "draw_marker_pass",
        "draw_zones_pass",
        "draw_settings_overlay_pass",
        "draw_menu_help_pass",
        "draw_button_detection_overlay_pass",
    ):
        monkeypatch.setattr(overlay_module, name, _record(name))
    return sizes


# --------------------------------------------------------------------------- #
# Dispatch tests
# --------------------------------------------------------------------------- #


class TestDrawDispatch:
    def test_button_detection_wizard_shortcircuits_other_passes(self, patched_passes) -> None:
        renderer = CairoOverlayRenderer()
        renderer.state.button_detection = ButtonDetectionState(active=True)
        renderer.draw(FakeCairo(), 1280, 720)
        assert patched_passes == ["draw_button_detection_overlay_pass"]

    def test_settings_menu_active_dispatches_settings_overlay(self, patched_passes) -> None:
        renderer = CairoOverlayRenderer()
        renderer.state.settings_menu_active = True
        renderer.draw(FakeCairo(), 1280, 720)
        assert patched_passes == ["draw_settings_overlay_pass", "draw_menu_help_pass"]

    def test_about_active_dispatches_about_overlay(self, patched_passes) -> None:
        """About screen renders via its own pass in the modal-priority slot."""
        renderer = CairoOverlayRenderer()
        renderer.state.about_active = True
        renderer.draw(FakeCairo(), 1280, 720)
        assert patched_passes == ["draw_about_overlay_pass", "draw_menu_help_pass"]

    @pytest.mark.parametrize(
        ("flag", "draw_pass"),
        [
            ("media_picker_active", "draw_media_picker_overlay_pass"),
            ("media_export_active", "draw_media_export_overlay_pass"),
        ],
    )
    def test_the_drive_picker_and_export_screen_take_the_modal_slot(self, patched_passes, flag, draw_pass) -> None:  # noqa: ANN001
        renderer = CairoOverlayRenderer()
        setattr(renderer.state, flag, True)
        renderer.draw(FakeCairo(), 1280, 720)
        assert patched_passes == [draw_pass, "draw_menu_help_pass"]

    def test_source_type_selection_dispatches_source_type_overlay(self, patched_passes) -> None:
        """Source-type picker renders when source_type_selection_active is set."""
        renderer = CairoOverlayRenderer()
        renderer.state.source_type_selection_active = True
        renderer.state.video_connected = False  # simulate prior plugin death
        renderer.draw(FakeCairo(), 1280, 720)
        assert patched_passes == ["draw_source_type_selection_overlay_pass", "draw_menu_help_pass"]

    def test_url_editor_dispatches_url_editor_overlay(self, patched_passes) -> None:
        """URL editor renders above No-Signal when url_editor_active is set."""
        renderer = CairoOverlayRenderer()
        renderer.state.url_editor_active = True
        renderer.state.video_connected = False
        renderer.draw(FakeCairo(), 1280, 720)
        assert patched_passes == ["draw_url_editor_overlay_pass", "draw_menu_help_pass"]

    def test_field_choice_picker_dispatches_field_choice_overlay(self, patched_passes) -> None:
        """Enum-style picker is sibling to the URL editor in the modal
        priority chain: when ``field_choice_active`` is set, the
        picker pass renders above any backdrop so the operator can
        finish their value choice without a frozen-frame distraction."""
        renderer = CairoOverlayRenderer()
        renderer.state.field_choice_active = True
        renderer.state.video_connected = False
        renderer.draw(FakeCairo(), 1280, 720)
        assert patched_passes == ["draw_field_choice_picker_overlay_pass", "draw_menu_help_pass"]

    def test_pi_network_field_edit_dispatches_field_edit_overlay(self, patched_passes) -> None:
        """Field editor takes deepest priority in Network sub-states."""
        renderer = CairoOverlayRenderer()
        renderer.state.pi_network.field_edit_active = True
        renderer.draw(FakeCairo(), 1280, 720)
        assert patched_passes == ["draw_pi_network_field_edit_overlay_pass", "draw_menu_help_pass"]

    def test_pi_network_screen_dispatches_screen_overlay(self, patched_passes) -> None:
        renderer = CairoOverlayRenderer()
        renderer.state.pi_network.screen_active = True
        renderer.draw(FakeCairo(), 1280, 720)
        assert patched_passes == ["draw_pi_network_screen_overlay_pass", "draw_menu_help_pass"]

    def test_disconnected_video_does_not_draw_no_signal_overlay(
        self,
        patched_passes,
    ) -> None:
        renderer = CairoOverlayRenderer()
        renderer.state.video_connected = False
        # No camera params → ``draw`` falls through to the bare HUD pass.
        renderer.draw(FakeCairo(), 1280, 720)
        assert "draw_no_signal_pass" not in patched_passes
        assert "draw_hud_pass" in patched_passes

    def test_source_selection_path_dispatches_source_overlay(self, patched_passes) -> None:
        renderer = CairoOverlayRenderer()
        renderer.state.source_selection_active = True
        renderer.draw(FakeCairo(), 1280, 720)
        assert patched_passes == ["draw_source_selection_overlay_pass", "draw_menu_help_pass"]

    def test_no_camera_params_draws_hud_only(self, patched_passes) -> None:
        renderer = CairoOverlayRenderer()
        renderer.state.camera_params = None
        renderer.draw(FakeCairo(), 1280, 720)
        # With no camera, HUD is drawn but scene passes are skipped
        assert patched_passes == ["draw_hud_pass"]

    def test_full_scene_with_camera(self, patched_passes) -> None:
        renderer = CairoOverlayRenderer()
        renderer.state.camera_params = np.zeros(7, dtype=np.float64)
        renderer.state.markers = [
            MarkerOverlayData(marker_id=0, x=0, y=0, z=0, color="#fff"),
        ]
        renderer.draw(FakeCairo(), 1280, 720)
        # Grid, origin, zones, marker, HUD – no detections (empty).
        assert "draw_grid_pass" in patched_passes
        assert "draw_origin_pass" in patched_passes
        assert "draw_zones_pass" in patched_passes
        assert "draw_marker_pass" in patched_passes
        assert "draw_hud_pass" in patched_passes
        assert "draw_detections_pass" not in patched_passes
        # The menus' key list belongs to the menus; the HUD has its own help.
        assert "draw_menu_help_pass" not in patched_passes

    def test_full_scene_with_detections_shown(self, patched_passes) -> None:
        renderer = CairoOverlayRenderer()
        renderer.state.camera_params = np.zeros(7, dtype=np.float64)
        renderer.state.detections = [object()]  # truthy triggers draw
        renderer.state.detection_show_boxes = True
        renderer.draw(FakeCairo(), 1280, 720)
        assert "draw_detections_pass" in patched_passes

    def test_draw_error_falls_back_to_error_text(self, monkeypatch) -> None:
        renderer = CairoOverlayRenderer()

        def _boom(*args, **kwargs) -> None:
            raise RuntimeError("pass failure")

        monkeypatch.setattr(overlay_module, "draw_hud_pass", _boom)
        fake = FakeCairo()
        renderer.state.camera_params = None
        renderer.draw(fake, 1280, 720)
        assert any("Overlay Error" in t for t in fake.text_calls)

    def test_draw_error_and_fallback_error_both_fail_silently(self, monkeypatch) -> None:

        renderer = CairoOverlayRenderer()

        def _boom(*args, **kwargs) -> None:
            raise RuntimeError("pass failure")

        monkeypatch.setattr(overlay_module, "draw_hud_pass", _boom)

        class AlwaysFailingCairo:
            def save(self) -> None:
                pass

            def restore(self) -> None:
                pass

            def set_source_rgba(self, *args) -> None:
                raise RuntimeError("double fail")

            def select_font_face(self, *args) -> None:
                pass

            def set_font_face(self, *args) -> None:
                pass

            def set_font_size(self, size: float) -> None:
                pass

            def move_to(self, *args) -> None:
                pass

            def show_text(self, *args) -> None:
                pass

        renderer.state.camera_params = None
        # Must not raise
        renderer.draw(AlwaysFailingCairo(), 1280, 720)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


# What a screen's own key help said: a key, a pad control, or a help block's heading.
_KEY_HELP = re.compile(r"\b(Enter|Esc|Escape|Backspace|Arrows?|D-?pad|Press)\b|^(KEYBOARD|CONTROLLER)$", re.IGNORECASE)

# Each menu screen, by the state that opens it and the title it draws.
_MENU_SCREENS = {
    "settings": (
        {"settings_menu_active": True, "settings_items": ["Network"], "settings_items_enabled": [True]},
        "SETTINGS",
    ),
    "about": ({"about_active": True}, "ABOUT"),
    "drive-picker": (
        {"media_picker_active": True, "media_picker_title": "SAVE DIAGNOSTICS", "media_picker_items": ["SanDisk"]},
        "SAVE DIAGNOSTICS",
    ),
    "export": (
        {
            "media_export_active": True,
            "media_export_lines": ("Saved a.txt to SanDisk.", "It can be removed now.", True),
        },
        "SAVE DIAGNOSTICS",
    ),
    "network": ({}, "NETWORK INTERFACES"),
    "network-field": ({}, "CHANGE IP ADDRESS"),
    "source-type": (
        {"source_type_selection_active": True, "available_source_types": [("rtsp", "RTSP")]},
        "VIDEO SOURCE TYPE",
    ),
    "url-editor": ({"url_editor_active": True, "url_editor_field_label": "RTSP URL"}, "RTSP URL"),
    "field-choice": ({"field_choice_active": True, "field_choice_items": ["Stage", "Grey"]}, "SELECT VALUE"),
    "source-selection": ({"source_selection_active": True, "discovered_sources": ["CAM 1"]}, "SELECT SOURCE"),
}


class TestMenuScreensNameNoKeys:
    @pytest.mark.parametrize("screen", list(_MENU_SCREENS))
    def test_no_menu_screen_names_its_own_keys(self, monkeypatch, screen: str) -> None:  # noqa: ANN001
        """Every key is in the one list beside the menus; a screen's own key
        help is a second, drifting copy of it."""
        monkeypatch.setattr(overlay_module, "draw_menu_help_pass", lambda *args, **kwargs: None)
        renderer = CairoOverlayRenderer()
        renderer._logo_handle = None  # the About screen's text fallback, not an SVG render
        state = renderer.state
        state.keyboard_connected = state.controller_connected = True
        state.button_labels = {"menu_confirm": "A", "menu_cancel": "B", "settings": "BACK"}
        fields, title = _MENU_SCREENS[screen]
        for name, value in fields.items():
            setattr(state, name, value)
        state.pi_network.screen_active = screen == "network"
        if screen == "network-field":
            state.pi_network.field_edit_active = True
            state.pi_network.field_label = "IP Address"
            state.pi_network.active_iface = "eth0"
        cr = RecordingCairo()
        renderer.draw(cr, 1920, 1080)
        texts = cr.show_text_strings()
        assert title in texts
        assert [t for t in texts if _KEY_HELP.search(t)] == []


class TestVisibleHelper:
    def test_non_finite_points_invisible(self) -> None:
        scr = np.array([[0.0, 0.0], [float("nan"), 100.0]])
        assert CairoOverlayRenderer._visible(scr, 1280, 720) is False

    def test_points_inside_margin_are_visible(self) -> None:
        scr = np.array([[100.0, 100.0], [800.0, 500.0]])
        assert CairoOverlayRenderer._visible(scr, 1280, 720) is True

    def test_points_outside_margin_are_not_visible(self) -> None:
        scr = np.array([[-5000.0, -5000.0]])
        assert CairoOverlayRenderer._visible(scr, 1280, 720) is False


class TestMeasuredFps:
    def test_returns_zero_without_samples(self) -> None:
        renderer = CairoOverlayRenderer()
        assert renderer.measured_fps() == 0.0

    def test_returns_zero_with_single_sample(self) -> None:
        renderer = CairoOverlayRenderer()
        renderer._frame_timestamps.append(0.0)
        assert renderer.measured_fps() == 0.0

    def test_stalled_window_returns_zero(self, monkeypatch) -> None:
        renderer = CairoOverlayRenderer()
        # Samples older than 2s relative to "now" → stalled
        renderer._frame_timestamps.append(0.0)
        renderer._frame_timestamps.append(0.001)
        # Freeze "now" well past the 2s stale window so the test is
        # deterministic regardless of wall-clock / monotonic offset.
        monkeypatch.setattr(overlay_module.time, "monotonic", lambda: 100.0)
        assert renderer.measured_fps() == 0.0

    def test_fps_calculation_matches_span(self, monkeypatch) -> None:
        renderer = CairoOverlayRenderer()
        for t in (10.0, 10.1, 10.2, 10.3, 10.4):
            renderer._frame_timestamps.append(t)
        # Freeze "now" just after the last sample
        monkeypatch.setattr(overlay_module.time, "monotonic", lambda: 10.5)
        # 5 samples span 0.4s → 4/0.4 = 10.0 fps
        assert renderer.measured_fps() == pytest.approx(10.0)

    def test_zero_or_negative_span_returns_zero(self, monkeypatch) -> None:
        renderer = CairoOverlayRenderer()
        renderer._frame_timestamps.append(10.0)
        renderer._frame_timestamps.append(10.0)  # same timestamp → span 0
        monkeypatch.setattr(overlay_module.time, "monotonic", lambda: 10.1)
        assert renderer.measured_fps() == 0.0


class TestProjectDelegation:
    def test_project_delegates_to_overlay_draw_scene(self, monkeypatch) -> None:
        recorded: list[tuple] = []

        def _fake_project(cam, pts, w, h):
            recorded.append((cam, pts, w, h))
            return np.zeros((0, 2))

        monkeypatch.setattr(overlay_module, "project_overlay_points", _fake_project)
        cam = np.zeros(7)
        pts = [(0.0, 0.0, 0.0)]
        CairoOverlayRenderer._project(cam, pts, 1280, 720)
        assert recorded == [(cam, pts, 1280, 720)]


class TestTextTruncation:
    def test_truncate_returns_full_when_fits(self) -> None:
        class FakeExt:
            def __init__(self, width: float) -> None:
                self.width = width

        class _FakeCr:
            def text_extents(self, s: str) -> FakeExt:
                return FakeExt(len(s) * 1.0)

        assert CairoOverlayRenderer._truncate_text_to_width(_FakeCr(), "hi", 100.0) == "hi"

    def test_truncate_uses_ellipsis_when_long(self) -> None:
        class FakeExt:
            def __init__(self, width: float) -> None:
                self.width = width

        class _FakeCr:
            def text_extents(self, s: str) -> FakeExt:
                return FakeExt(len(s) * 1.0)

        # Width budget 8 → we can only fit ~5 chars + "..." = 8 chars exactly
        out = CairoOverlayRenderer._truncate_text_to_width(_FakeCr(), "abcdefghij", 8.0)
        assert out.endswith("...")
        assert len(out) <= 8

    def test_truncate_returns_empty_when_ellipsis_wont_fit(self) -> None:
        class FakeExt:
            def __init__(self, width: float) -> None:
                self.width = width

        class _FakeCr:
            def text_extents(self, s: str) -> FakeExt:
                return FakeExt(len(s) * 1.0)

        out = CairoOverlayRenderer._truncate_text_to_width(_FakeCr(), "hello world", 2.0)
        assert out == ""


# --------------------------------------------------------------------------- #
# Constructor / icon loading
# --------------------------------------------------------------------------- #


class TestIconLoading:
    def test_constructor_without_rsvg_disables_icon(self, monkeypatch) -> None:
        monkeypatch.setattr(overlay_module, "_HAS_RSVG", False)
        renderer = CairoOverlayRenderer()
        assert renderer._icon_handle is None

    def test_icon_load_failure_swallowed(self, monkeypatch) -> None:
        class _FailHandle:
            @staticmethod
            def new_from_file(path: str):
                raise RuntimeError("boom")

        class _Rsvg:
            Handle = _FailHandle

        monkeypatch.setattr(overlay_module, "_HAS_RSVG", True)
        monkeypatch.setattr(overlay_module, "_Rsvg", _Rsvg, raising=False)
        renderer = CairoOverlayRenderer()
        # Failure was swallowed – renderer constructed, icon + logo None
        assert renderer._icon_handle is None
        assert renderer._logo_handle is None

    def test_draw_icon_noop_without_handle(self) -> None:
        renderer = CairoOverlayRenderer()
        renderer._icon_handle = None
        # Must not raise
        renderer._draw_icon(FakeCairo(), 10, 20, 50)

    def test_draw_icon_calls_handle_render(self) -> None:
        class _Handle:
            def __init__(self) -> None:
                self.rendered = 0

            def render_cairo(self, cr) -> None:
                self.rendered += 1

        renderer = CairoOverlayRenderer()
        handle = _Handle()
        renderer._icon_handle = handle

        class _Cr:
            def save(self) -> None:
                pass

            def restore(self) -> None:
                pass

            def translate(self, *a) -> None:
                pass

            def scale(self, *a) -> None:
                pass

        renderer._draw_icon(_Cr(), 0.0, 0.0, 100.0)
        assert handle.rendered == 1

    def test_draw_icon_swallows_exception(self) -> None:
        class _ExplodingHandle:
            def render_cairo(self, cr) -> None:
                raise RuntimeError("boom")

        renderer = CairoOverlayRenderer()
        renderer._icon_handle = _ExplodingHandle()

        class _Cr:
            def save(self) -> None:
                pass

            def restore(self) -> None:
                pass

            def translate(self, *a) -> None:
                pass

            def scale(self, *a) -> None:
                pass

        # Must not raise
        renderer._draw_icon(_Cr(), 0.0, 0.0, 100.0)

    def test_draw_logo_noop_without_handle(self) -> None:
        renderer = CairoOverlayRenderer()
        renderer._logo_handle = None
        assert renderer._draw_logo(FakeCairo(), 10, 20, 200) == 0.0

    def test_draw_logo_renders_and_returns_scaled_height(self) -> None:
        class _Handle:
            def __init__(self) -> None:
                self.rendered = 0

            def render_cairo(self, cr) -> None:
                self.rendered += 1

        class _Cr:
            def save(self) -> None: ...
            def restore(self) -> None: ...
            def translate(self, *a) -> None: ...
            def scale(self, *a) -> None: ...

        renderer = CairoOverlayRenderer()
        renderer._logo_handle = _Handle()
        height = renderer._draw_logo(_Cr(), 0.0, 0.0, overlay_module._LOGO_NATURAL_W)
        # Rendered once; full-natural-width request -> natural height back.
        assert renderer._logo_handle.rendered == 1
        assert height == pytest.approx(overlay_module._LOGO_NATURAL_H)

    def test_draw_logo_swallows_exception(self) -> None:
        class _ExplodingHandle:
            def render_cairo(self, cr) -> None:
                raise RuntimeError("boom")

        class _Cr:
            def save(self) -> None: ...
            def restore(self) -> None: ...
            def translate(self, *a) -> None: ...
            def scale(self, *a) -> None: ...

        renderer = CairoOverlayRenderer()
        renderer._logo_handle = _ExplodingHandle()
        # render_cairo raises -> swallowed; degrades to 0.0 so the caller's
        # layout still advances.
        assert renderer._draw_logo(_Cr(), 0.0, 0.0, 200.0) == 0.0


# --------------------------------------------------------------------------- #
# Module-import Rsvg branches – both sides forced under fake ``gi``
# --------------------------------------------------------------------------- #


class TestModuleImportRsvgBranches:
    """Deterministically exercise both sides of the module-level Rsvg
    import in :mod:`openfollow.video.overlay` under coverage.

    The ``try`` / ``except`` block fires one branch per platform:

    * ``try`` body (``_HAS_RSVG = True``) when ``gi`` is importable and
      the ``Rsvg 2.0`` typelib is present – typical macOS dev setup.
    * ``except (ImportError, ValueError)`` (``_HAS_RSVG = False``) when
      ``gi`` is missing or the Rsvg typelib isn't installed – e.g. our
      Linux CI image, which omits ``gir1.2-rsvg-2.0``.

    Availability is platform- and image-dependent, so relying on the
    natural import leaves whichever branch the current platform *didn't*
    take permanently uncovered. Both tests below reload the overlay
    module against a fake ``gi`` / ``gi.repository`` to force the
    branch they target regardless of the host environment.
    """

    @staticmethod
    def _reload_with_gi(fake_gi, fake_repo) -> bool:
        """Reload ``overlay`` against the given ``gi`` / ``gi.repository``
        fakes and return the resulting ``_HAS_RSVG`` value, then always
        restore the real modules and re-reload so downstream tests see
        the natural import result (whichever branch that lands on for
        the current platform).

        The returned ``_HAS_RSVG`` is captured *before* the finally-block
        restore runs, so callers can assert on the fake's effect even
        after the module has been reloaded back to its platform-natural
        state.
        """
        import importlib
        import sys

        from openfollow.video import overlay as overlay_module

        orig_gi = sys.modules.get("gi")
        orig_repo = sys.modules.get("gi.repository")
        try:
            sys.modules["gi"] = fake_gi
            sys.modules["gi.repository"] = fake_repo
            importlib.reload(overlay_module)
            observed = overlay_module._HAS_RSVG
        finally:
            if orig_gi is not None:
                sys.modules["gi"] = orig_gi
            else:
                sys.modules.pop("gi", None)
            if orig_repo is not None:
                sys.modules["gi.repository"] = orig_repo
            else:
                sys.modules.pop("gi.repository", None)
            importlib.reload(overlay_module)
        return observed

    def test_module_sets_has_rsvg_false_when_require_version_raises(self) -> None:
        """Covers the ``except`` branch – natural path on Linux CI (no
        Rsvg typelib installed), forced path on macOS dev environments
        (where the typelib is usually present).
        """
        import types

        def _fail_require(namespace: str, _version: str) -> None:
            raise ValueError(f"{namespace} typelib not available")

        fake_gi = types.ModuleType("gi")
        fake_gi.require_version = _fail_require  # type: ignore[attr-defined]
        fake_repo = types.ModuleType("gi.repository")

        # ``observed`` is captured while the fake is installed, so we
        # verify the branch's *effect* on ``_HAS_RSVG`` rather than just
        # nudging coverage – even though the finally-block restore has
        # already reloaded the module back to its platform-natural state
        # by the time we return.
        observed = self._reload_with_gi(fake_gi, fake_repo)
        assert observed is False

    def test_module_sets_has_rsvg_true_when_rsvg_import_succeeds(self) -> None:
        """Covers the ``try`` body success path (the
        ``from gi.repository import Rsvg`` + ``_HAS_RSVG = True`` sequence).

        Natural path on macOS dev boxes (Rsvg typelib present), forced
        path on Linux CI (where the gir1.2-rsvg-2.0 package isn't
        installed). Without this fake, Linux CI leaves that branch
        permanently uncovered.
        """
        import types

        class _FakeRsvg:
            """Surface-compatible stand-in for ``gi.repository.Rsvg``.

            The module-level code only imports ``Rsvg``; the ``Rsvg.Handle``
            attribute is referenced lazily inside ``CairoOverlayRenderer.__init__``
            – not during module import – so we only need the name to resolve.
            """

            class Handle:
                @staticmethod
                def new_from_file(_path: str) -> None:
                    return None

        fake_gi = types.ModuleType("gi")
        fake_gi.require_version = lambda *_args, **_kw: None  # type: ignore[attr-defined]
        fake_repo = types.ModuleType("gi.repository")
        fake_repo.Rsvg = _FakeRsvg  # type: ignore[attr-defined]

        observed = self._reload_with_gi(fake_gi, fake_repo)
        assert observed is True


@pytest.mark.filterwarnings("ignore:Rsvg.Handle.render_cairo is deprecated:DeprecationWarning")
def test_the_screen_reuses_a_still_cone_and_forgets_a_removed_one(monkeypatch) -> None:
    """Drawn on every display refresh, a cone that did not change is painted from its picture."""
    import cairo

    from openfollow.runtime import overlay_draw_scene as scene
    from openfollow.runtime.overlay_state import MarkerOverlayData

    worked_out: list[int] = []
    real = scene._cone_geometry

    def _counting(state, t, w, h):
        worked_out.append(t.marker_id)
        return real(state, t, w, h)

    monkeypatch.setattr(scene, "_cone_geometry", _counting)
    renderer = CairoOverlayRenderer()
    state = renderer.state
    state.camera_params = np.array([0.0, -11.0, 6.0, -22.0, 0.0, 0.0, 60.0])
    state.grid_config = (10.0, 6.0, 1.0, 0.0, 3.0, 0.0)
    state.marker_style = "cone"
    a = MarkerOverlayData(marker_id=1, x=-1.0, y=2.0, z=1.6, color="#ff3333")
    b = MarkerOverlayData(marker_id=2, x=1.0, y=2.0, z=1.6, color="#33aaff")
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1280, 720)

    def frame(*markers: MarkerOverlayData) -> None:
        state.markers = list(markers)
        renderer.draw(cairo.Context(surface), 1280, 720)

    frame(a, b)
    frame(a, b)
    assert sorted(worked_out) == [1, 2]
    frame(a)
    frame(a, b)
    assert sorted(worked_out) == [1, 2, 2]


# --------------------------------------------------------------------------- #
# The scene sits on the video; the HUD sits on the screen
# --------------------------------------------------------------------------- #


_SCENE = ("draw_grid_pass", "draw_origin_pass", "draw_zones_pass", "draw_marker_pass")


def _scene_renderer(source: tuple[int, int]) -> CairoOverlayRenderer:
    renderer = CairoOverlayRenderer()
    renderer.state.camera_params = np.zeros(7, dtype=np.float64)
    renderer.state.source_resolution = source
    renderer.state.markers = [MarkerOverlayData(marker_id=0, x=0, y=0, z=0, color="#fff")]
    return renderer


class TestLetterboxedScene:
    """gtksink letterboxes the frame into a rectangle of the source's shape;
    the scene passes are translated and clipped into that same rectangle so
    the overlay stays on the video whatever the window's shape, while the
    HUD keeps the whole canvas. The rect values are gtksink's own, worked by
    hand."""

    def test_scene_passes_get_the_video_rect_and_the_hud_keeps_the_canvas(self, pass_sizes) -> None:
        fake = FakeCairo()
        _scene_renderer((1920, 1080)).draw(fake, 1000, 1000)
        for name in _SCENE:
            assert pass_sizes[name] == [(1000, 562)], name
        assert pass_sizes["draw_hud_pass"] == [(1000, 1000)]
        assert fake.translates == [(0, 219)]
        assert fake.clip_rects == [(0, 0, 1000, 562)]

    @pytest.mark.parametrize(
        ("source", "canvas", "rect"),
        [
            ((1024, 768), (1920, 1080), (240, 0, 1440, 1080)),
            ((1080, 1920), (1280, 720), (437, 0, 405, 720)),
        ],
        ids=["4:3", "portrait"],
    )
    def test_a_narrower_source_is_pillarboxed(self, pass_sizes, source, canvas, rect) -> None:
        fake = FakeCairo()
        _scene_renderer(source).draw(fake, *canvas)
        x, y, w, h = rect
        assert pass_sizes["draw_grid_pass"] == [(w, h)]
        assert pass_sizes["draw_hud_pass"] == [canvas]
        assert fake.translates == [(x, y)]
        assert fake.clip_rects == [(0, 0, w, h)]

    def test_detection_boxes_scale_with_the_video_rect(self, pass_sizes) -> None:
        renderer = _scene_renderer((1920, 1080))
        renderer.state.detections = [object()]
        renderer.state.detection_show_boxes = True
        renderer.draw(FakeCairo(), 1000, 1000)
        assert pass_sizes["draw_detections_pass"] == [(1000, 562)]

    @pytest.mark.parametrize("source", [(0, 0), (1920, 1080)], ids=["unknown", "matching"])
    def test_the_whole_canvas_when_there_is_nothing_to_box(self, pass_sizes, source) -> None:
        fake = FakeCairo()
        _scene_renderer(source).draw(fake, 1280, 720)
        for name in (*_SCENE, "draw_hud_pass"):
            assert pass_sizes[name] == [(1280, 720)], name
        assert fake.translates == [(0, 0)]

    def test_menus_and_the_wizard_ignore_the_video_rect(self, pass_sizes) -> None:
        renderer = _scene_renderer((1920, 1080))
        renderer.state.settings_menu_active = True
        fake = FakeCairo()
        renderer.draw(fake, 1000, 1000)
        assert pass_sizes["draw_settings_overlay_pass"] == [(1000, 1000)]
        assert pass_sizes["draw_menu_help_pass"] == [(1000, 1000)]
        assert fake.translates == []

        renderer.state.settings_menu_active = False
        renderer.state.button_detection = ButtonDetectionState(active=True)
        fake = FakeCairo()
        renderer.draw(fake, 1000, 1000)
        assert pass_sizes["draw_button_detection_overlay_pass"] == [(1000, 1000)]
        assert fake.translates == []

    def test_a_degenerate_rect_skips_the_scene_but_draws_the_hud(self, pass_sizes, caplog) -> None:  # noqa: ANN001
        # A 16:9 frame fitted into one pixel has no height: nothing to draw it in.
        fake = FakeCairo()
        _scene_renderer((1920, 1080)).draw(fake, 1, 1)
        assert all(name not in pass_sizes for name in _SCENE)
        assert pass_sizes["draw_hud_pass"] == [(1, 1)]
        assert fake.translates == []
        assert not any("overlay draw error" in r.message.lower() for r in caplog.records)

    def test_the_translate_is_undone_before_the_hud(self, monkeypatch, pass_sizes) -> None:
        fake = FakeCairo()
        _scene_renderer((1920, 1080)).draw(fake, 1000, 1000)
        assert fake.saves == fake.restores == 2

        def _boom(*args, **kwargs) -> None:
            raise RuntimeError("pass failure")

        # A raising scene pass still leaves a balanced context for the fallback text.
        monkeypatch.setattr(overlay_module, "draw_zones_pass", _boom)
        fake = FakeCairo()
        _scene_renderer((1920, 1080)).draw(fake, 1000, 1000)
        assert fake.saves == fake.restores
        assert any("Overlay Error" in t for t in fake.text_calls)
