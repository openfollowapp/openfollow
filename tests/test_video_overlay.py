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

import numpy as np
import pytest

from openfollow.runtime.overlay_state import (
    ButtonDetectionState,
    MarkerOverlayData,
)
from openfollow.video import overlay as overlay_module
from openfollow.video.overlay import CairoOverlayRenderer

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

    def save(self) -> None:
        self.saves += 1

    def restore(self) -> None:
        self.restores += 1

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
        "draw_button_detection_overlay_pass",
        "draw_hud_pass",
        "draw_iface_selection_overlay_pass",
        "draw_settings_overlay_pass",
        "draw_source_selection_overlay_pass",
        "draw_source_type_selection_overlay_pass",
        "draw_url_editor_overlay_pass",
        "draw_field_choice_picker_overlay_pass",
        "draw_pi_network_screen_overlay_pass",
        "draw_pi_network_iface_picker_overlay_pass",
        "draw_pi_network_method_picker_overlay_pass",
        "draw_pi_network_field_edit_overlay_pass",
        "draw_detections_pass",
        "draw_grid_pass",
        "draw_origin_pass",
        "draw_marker_pass",
        "draw_zones_pass",
    ):
        monkeypatch.setattr(overlay_module, name, _record(name))
    return calls


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
        assert patched_passes == ["draw_settings_overlay_pass"]

    def test_about_active_dispatches_about_overlay(self, patched_passes) -> None:
        """About screen renders via its own pass in the modal-priority slot."""
        renderer = CairoOverlayRenderer()
        renderer.state.about_active = True
        renderer.draw(FakeCairo(), 1280, 720)
        assert patched_passes == ["draw_about_overlay_pass"]

    def test_iface_selection_dispatches_iface_overlay(self, patched_passes) -> None:
        renderer = CairoOverlayRenderer()
        renderer.state.iface_selection_active = True
        renderer.draw(FakeCairo(), 1280, 720)
        assert patched_passes == ["draw_iface_selection_overlay_pass"]

    def test_source_type_selection_dispatches_source_type_overlay(self, patched_passes) -> None:
        """Source-type picker renders when source_type_selection_active is set."""
        renderer = CairoOverlayRenderer()
        renderer.state.source_type_selection_active = True
        renderer.state.video_connected = False  # simulate prior plugin death
        renderer.draw(FakeCairo(), 1280, 720)
        assert patched_passes == ["draw_source_type_selection_overlay_pass"]

    def test_url_editor_dispatches_url_editor_overlay(self, patched_passes) -> None:
        """URL editor renders above No-Signal when url_editor_active is set."""
        renderer = CairoOverlayRenderer()
        renderer.state.url_editor_active = True
        renderer.state.video_connected = False
        renderer.draw(FakeCairo(), 1280, 720)
        assert patched_passes == ["draw_url_editor_overlay_pass"]

    def test_field_choice_picker_dispatches_field_choice_overlay(self, patched_passes) -> None:
        """Enum-style picker is sibling to the URL editor in the modal
        priority chain: when ``field_choice_active`` is set, the
        picker pass renders above any backdrop so the operator can
        finish their value choice without a frozen-frame distraction."""
        renderer = CairoOverlayRenderer()
        renderer.state.field_choice_active = True
        renderer.state.video_connected = False
        renderer.draw(FakeCairo(), 1280, 720)
        assert patched_passes == ["draw_field_choice_picker_overlay_pass"]

    def test_pi_network_field_edit_dispatches_field_edit_overlay(self, patched_passes) -> None:
        """Field editor takes deepest priority in Network sub-states."""
        renderer = CairoOverlayRenderer()
        renderer.state.pi_network.field_edit_active = True
        renderer.draw(FakeCairo(), 1280, 720)
        assert patched_passes == ["draw_pi_network_field_edit_overlay_pass"]

    def test_pi_network_method_picker_dispatches_method_picker_overlay(self, patched_passes) -> None:
        renderer = CairoOverlayRenderer()
        renderer.state.pi_network.method_picker_active = True
        renderer.draw(FakeCairo(), 1280, 720)
        assert patched_passes == ["draw_pi_network_method_picker_overlay_pass"]

    def test_pi_network_iface_picker_dispatches_iface_picker_overlay(self, patched_passes) -> None:
        renderer = CairoOverlayRenderer()
        renderer.state.pi_network.iface_picker_active = True
        renderer.draw(FakeCairo(), 1280, 720)
        assert patched_passes == ["draw_pi_network_iface_picker_overlay_pass"]

    def test_pi_network_screen_dispatches_screen_overlay(self, patched_passes) -> None:
        renderer = CairoOverlayRenderer()
        renderer.state.pi_network.screen_active = True
        renderer.draw(FakeCairo(), 1280, 720)
        assert patched_passes == ["draw_pi_network_screen_overlay_pass"]

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
        assert patched_passes == ["draw_source_selection_overlay_pass"]

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
