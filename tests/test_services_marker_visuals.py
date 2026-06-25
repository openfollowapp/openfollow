# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the overlay-state sync helpers in
``runtime/services_marker_visuals``: ``sync_marker_config`` /
``sync_grid_config`` / ``sync_ui_config`` / ``build_initial_overlay_state`` /
``_populate_zone_overlay`` / ``_populate_pi_network_overlay``, plus the
``_soft_float`` / ``_soft_bool`` / ``_resolve_marker_color`` /
``_resolve_marker_name`` defensive helpers."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from openfollow.runtime.overlay_state import OverlayState
from openfollow.runtime.services_marker_visuals import (
    _populate_pi_network_overlay,
    _populate_zone_overlay,
    build_initial_overlay_state,
    build_marker_visual_state,
    sync_grid_config,
    sync_marker_config,
    sync_ui_config,
)
from openfollow.runtime_metrics import OverlayStatePool
from openfollow.units import UnitSystem

pytestmark = pytest.mark.unit


def _make_marker_config() -> SimpleNamespace:
    return SimpleNamespace(
        ball_visible=False,
        crosshair_visible=False,
        crosshair_size=0.5,
        crosshair_color="#ff0000",
        crosshair_thickness=3.0,
        transparency=0.8,
        drop_line=False,
        drop_line_thickness=2.0,
        ground_circle=True,
        ground_circle_size=0.6,
        ground_circle_filled=False,
        z_display_from_stage=True,
        min_speed=0.2,
        max_speed=5.0,
    )


def _make_grid_config() -> SimpleNamespace:
    return SimpleNamespace(
        visible=True,
        width=30.0,
        depth=20.0,
        spacing=2.0,
        x_offset=1.0,
        y_offset=2.0,
        z_offset=0.5,
        color="#abcdef",
        thickness=2,
        transparency=0.25,
        origin_visible=True,
        origin_length=2.0,
        origin_thickness=4.0,
    )


class TestSyncMarkerConfig:
    def test_copies_all_fields(self) -> None:
        state = OverlayState()
        tc = _make_marker_config()
        cfg = SimpleNamespace(marker=tc)

        sync_marker_config(state, cfg)

        assert state.show_ball is False
        assert state.show_crosshair is False
        assert state.crosshair_size == 0.5
        assert state.crosshair_color == "#ff0000"
        assert state.crosshair_thickness == 3
        assert state.transparency == 0.8
        assert state.show_drop_line is False
        assert state.drop_line_thickness == 2
        assert state.show_ground_circle is True
        assert state.ground_circle_size == 0.6
        assert state.ground_circle_filled is False
        assert state.z_display_from_stage is True
        assert state.min_speed == 0.2
        assert state.max_speed == 5.0


class TestSyncUiConfig:
    def test_metric_and_imperial_map_to_enum(self) -> None:
        state = OverlayState()
        sync_ui_config(state, SimpleNamespace(ui=SimpleNamespace(unit_system="imperial")))
        assert state.unit_system is UnitSystem.IMPERIAL
        sync_ui_config(state, SimpleNamespace(ui=SimpleNamespace(unit_system="metric")))
        assert state.unit_system is UnitSystem.METRIC

    def test_missing_ui_section_falls_back_to_metric(self) -> None:
        # Duck-typed cfg with no ``ui`` (test shims / early runtime stubs).
        state = OverlayState()
        state.unit_system = UnitSystem.IMPERIAL
        sync_ui_config(state, SimpleNamespace())
        assert state.unit_system is UnitSystem.METRIC

    def test_invalid_value_falls_back_to_metric(self) -> None:
        # A real UiConfig normalises bad input, but a shim can smuggle an
        # out-of-range string straight through – fail soft, don't raise.
        state = OverlayState()
        state.unit_system = UnitSystem.IMPERIAL
        sync_ui_config(state, SimpleNamespace(ui=SimpleNamespace(unit_system="furlongs")))
        assert state.unit_system is UnitSystem.METRIC

    def test_non_string_value_falls_back_to_metric(self) -> None:
        # Unhashable values (dict/list) must fail soft without freezing the HUD.
        # UnitSystem(raw) raises TypeError, not ValueError for bad inputs.
        state = OverlayState()
        state.unit_system = UnitSystem.IMPERIAL
        sync_ui_config(state, SimpleNamespace(ui=SimpleNamespace(unit_system=["imperial"])))
        assert state.unit_system is UnitSystem.METRIC


class TestSyncGridConfig:
    def test_copies_all_fields(self) -> None:
        state = OverlayState()
        gc = _make_grid_config()
        cfg = SimpleNamespace(grid=gc)

        sync_grid_config(state, cfg)

        assert state.grid_config == (30.0, 20.0, 2.0, 1.0, 2.0, 0.5)
        assert state.grid_visible is True
        assert state.grid_color == "#abcdef"
        assert state.grid_thickness == 2
        assert state.grid_transparency == 0.25
        assert state.show_origin is True

    def test_copies_visible_false(self) -> None:
        state = OverlayState()
        gc = _make_grid_config()
        gc.visible = False
        sync_grid_config(state, SimpleNamespace(grid=gc))
        assert state.grid_visible is False

    def test_grid_visible_defaults_true_for_partial_object(self) -> None:
        # A grid object predating the field must not crash the per-tick path
        # and falls back to visible (the default).
        state = OverlayState()
        gc = _make_grid_config()
        del gc.visible
        sync_grid_config(state, SimpleNamespace(grid=gc))
        assert state.grid_visible is True
        assert state.origin_length == 2.0
        assert state.origin_thickness == 4

    def test_tolerates_bad_appearance_from_shim_object(self) -> None:
        # A SimpleNamespace stand-in skips GridConfig.__post_init__, so bad
        # field types land straight in sync_grid_config. The overlay update
        # path must not raise – it runs every animation tick.
        state = OverlayState()
        gc = SimpleNamespace(
            width=30.0,
            depth=20.0,
            spacing=2.0,
            x_offset=1.0,
            y_offset=2.0,
            z_offset=0.5,
            color=42,  # not a string
            thickness="abc",  # not coercible to int
            transparency="way too high",  # not coercible to float
            origin_visible=True,
            origin_length=2.0,
            origin_thickness=None,  # not coercible to int
        )
        cfg = SimpleNamespace(grid=gc)

        sync_grid_config(state, cfg)

        assert state.grid_color == "#545454"
        assert state.grid_thickness == 1
        assert state.grid_transparency == 0.6
        assert state.origin_thickness == 3

    def test_tolerates_bad_geometry_from_shim_object(self) -> None:
        # draw_grid unpacks state.grid_config and does arithmetic on every
        # tick (gw/gs, int(gw/gs), etc.); non-numeric entries would crash
        # the animation loop the same way bad appearance fields would.
        state = OverlayState()
        gc = SimpleNamespace(
            width="wide",
            depth=None,
            spacing="two",
            x_offset="left",
            y_offset=None,
            z_offset="deep",
            color="#abcdef",
            thickness=1,
            transparency=0.6,
            origin_visible=False,
            origin_length="long",
            origin_thickness=3,
        )
        cfg = SimpleNamespace(grid=gc)

        sync_grid_config(state, cfg)

        # Tuple must be fully numeric and match GridConfig declared defaults.
        assert state.grid_config == (10.0, 6.0, 1.0, 0.0, 3.0, 0.0)
        assert state.origin_length == 1.0

    def test_rejects_non_hex_color_strings(self) -> None:
        # A stand-in supplying "red" or "#12" would otherwise pass the
        # isinstance(str) check and render as white via parse_hex fallback,
        # diverging from the documented #545454 default.
        state = OverlayState()
        for bad_color in ("red", "#12", "not-a-hex", "#gggggg", "#ABCDEFG"):
            gc = SimpleNamespace(
                width=10.0,
                depth=6.0,
                spacing=1.0,
                x_offset=0.0,
                y_offset=3.0,
                z_offset=0.0,
                color=bad_color,
                thickness=1,
                transparency=0.6,
                origin_visible=False,
                origin_length=1.0,
                origin_thickness=3,
            )
            sync_grid_config(state, SimpleNamespace(grid=gc))
            assert state.grid_color == "#545454", f"bad color {bad_color!r}"

    def test_tolerates_inf_and_huge_int_without_raising(self) -> None:
        # ``float(huge_int)`` and ``int(float('inf'))`` raise OverflowError
        # (not TypeError/ValueError). The overlay update path runs every
        # animation tick – it must survive both.
        state = OverlayState()
        gc = SimpleNamespace(
            width=10**5000,  # float(huge_int) → OverflowError
            depth=6.0,
            spacing=1.0,
            x_offset=0.0,
            y_offset=3.0,
            z_offset=0.0,
            color="#abcdef",
            thickness=float("inf"),  # int(inf) → OverflowError
            transparency=0.6,
            origin_visible=False,
            origin_length=1.0,
            origin_thickness=float("inf"),
        )
        sync_grid_config(state, SimpleNamespace(grid=gc))
        # Both fall back to GridConfig declared defaults (via _GRID_DEFAULTS).
        assert state.grid_config[0] == 10.0
        assert state.grid_thickness == 1
        assert state.origin_thickness == 3

    def test_shim_string_origin_visible_does_not_flip_to_truthy(self) -> None:
        # ``bool("false") is True`` – ``state.show_origin = bool(g.origin_visible)``
        # would render the origin glyph when the operator wanted it hidden.
        # ``_soft_bool`` must recognise the common string forms (and fall back
        # to the GridConfig default on anything unrecognised).
        state = OverlayState()
        gc = SimpleNamespace(
            width=10.0,
            depth=6.0,
            spacing=1.0,
            x_offset=0.0,
            y_offset=3.0,
            z_offset=0.0,
            color="#abcdef",
            thickness=1,
            transparency=0.6,
            origin_visible="false",  # string, not bool – the failure case
            origin_length=1.0,
            origin_thickness=3,
        )
        sync_grid_config(state, SimpleNamespace(grid=gc))
        assert state.show_origin is False

        # Symmetry check: "true" flips it on.
        gc.origin_visible = "true"
        sync_grid_config(state, SimpleNamespace(grid=gc))
        assert state.show_origin is True

        # Junk falls back to the GridConfig default (False).
        gc.origin_visible = 42
        sync_grid_config(state, SimpleNamespace(grid=gc))
        assert state.show_origin is False

    def test_rejects_inf_and_nan_without_leaking_non_finite(self) -> None:
        # ``float("inf")`` and ``float("nan")`` do NOT raise from ``float()``
        # – they slip past an OverflowError-only catch. ``draw_grid`` does
        # ``int(width / spacing)`` though, which raises on inf/nan. The
        # ``_soft_float`` non-finite guard must reject them to the default.
        state = OverlayState()
        gc = SimpleNamespace(
            width=float("inf"),
            depth=float("nan"),
            spacing=1.0,
            x_offset=float("nan"),
            y_offset=3.0,
            z_offset=0.0,
            color="#abcdef",
            thickness=1,
            transparency=0.5,
            origin_visible=False,
            origin_length=float("inf"),
            origin_thickness=3,
        )
        sync_grid_config(state, SimpleNamespace(grid=gc))
        gw, gd, gs, gx, gy, gz = state.grid_config
        assert gw == 10.0  # inf → default
        assert gd == 6.0  # nan → default
        assert gx == 0.0  # nan → default
        assert state.origin_length == 1.0  # inf → default

    def test_normalises_valid_hex_color_to_lowercase(self) -> None:
        # Matches the GridConfig.__post_init__ contract so config.toml and
        # the overlay state stay byte-for-byte consistent.
        state = OverlayState()
        gc = SimpleNamespace(
            width=10.0,
            depth=6.0,
            spacing=1.0,
            x_offset=0.0,
            y_offset=3.0,
            z_offset=0.0,
            color="#ABCDEF",
            thickness=1,
            transparency=0.6,
            origin_visible=False,
            origin_length=1.0,
            origin_thickness=3,
        )
        sync_grid_config(state, SimpleNamespace(grid=gc))
        assert state.grid_color == "#abcdef"

    def test_clamps_out_of_range_appearance_values(self) -> None:
        # Even with coercible-but-out-of-range values, the sync helper keeps
        # the overlay within renderable bounds rather than trusting callers.
        state = OverlayState()
        gc = SimpleNamespace(
            width=30.0,
            depth=20.0,
            spacing=2.0,
            x_offset=1.0,
            y_offset=2.0,
            z_offset=0.5,
            color="#abcdef",
            thickness=0,  # below min
            transparency=5.0,  # above max
            origin_visible=False,
            origin_length=1.0,
            origin_thickness=0,  # below min
        )
        cfg = SimpleNamespace(grid=gc)

        sync_grid_config(state, cfg)

        assert state.grid_thickness == 1
        assert state.grid_transparency == 1.0
        assert state.origin_thickness == 1

    def test_shim_bool_thickness_falls_back_to_default(self) -> None:
        # ``bool`` is an ``int`` subclass – without an explicit guard
        # ``int(True) == 1`` and ``int(False) == 0`` would silently
        # coerce a hand-edited ``thickness = true`` to 1 and a
        # ``origin_thickness = false`` to 0 (then clamped to 1) instead
        # of falling back to the GridConfig default. ``_GRID_DEFAULTS``
        # Has origin_thickness = 3 to disambiguate rejection from coercion.
        state = OverlayState()
        gc = SimpleNamespace(
            width=10.0,
            depth=6.0,
            spacing=1.0,
            x_offset=0.0,
            y_offset=3.0,
            z_offset=0.0,
            color="#abcdef",
            thickness=True,  # bool – must NOT silently → 1
            transparency=0.6,
            origin_visible=False,
            origin_length=1.0,
            origin_thickness=False,  # bool – must NOT silently → 0/clamp(1)
        )
        sync_grid_config(state, SimpleNamespace(grid=gc))
        # ``_GRID_DEFAULTS.thickness`` is 1; ``_GRID_DEFAULTS.origin_thickness``
        # is 3.  The first assertion is a no-regression guard (the buggy
        # silent-coerce path also yields 1); the second assertion only
        # passes if booleans truly fall back to the default.
        assert state.grid_thickness == 1
        assert state.origin_thickness == 3

        # Symmetry: ``True`` for origin_thickness must also fall back, not
        # silently → 1.
        gc.origin_thickness = True
        sync_grid_config(state, SimpleNamespace(grid=gc))
        assert state.origin_thickness == 3

    def test_shim_inf_nan_transparency_falls_back_to_default(self) -> None:
        # ``float('inf')`` and ``float('nan')`` slip past ``float()``
        # without raising. The old inline ``max(0.0, min(1.0, float(x)))``
        # silently mapped ``inf → 1.0`` (full opacity) and produced
        # ``nan`` on the clamp path (which then propagated into Cairo
        # _soft_float rejects non-finite to default for consistency.
        state = OverlayState()
        for bad in (float("inf"), float("-inf"), float("nan")):
            gc = SimpleNamespace(
                width=10.0,
                depth=6.0,
                spacing=1.0,
                x_offset=0.0,
                y_offset=3.0,
                z_offset=0.0,
                color="#abcdef",
                thickness=1,
                transparency=bad,
                origin_visible=False,
                origin_length=1.0,
                origin_thickness=3,
            )
            sync_grid_config(state, SimpleNamespace(grid=gc))
            # ``_GRID_DEFAULTS.transparency`` is 0.6.
            assert state.grid_transparency == 0.6, (
                f"transparency={bad!r} should fall back to 0.6, got {state.grid_transparency!r}"
            )


class TestBuildInitialOverlayState:
    def test_returns_disconnected_state(self) -> None:
        cfg = SimpleNamespace(
            grid=_make_grid_config(),
            marker=_make_marker_config(),
        )
        state = build_initial_overlay_state(cfg)

        assert state.video_connected is False
        assert state.source_label == ""
        assert state.reconnect_attempt == 0
        assert state.error_message == ""
        assert state.controller_connected is False
        assert state.keyboard_connected is False
        assert state.source_selection_active is False
        assert state.source_selection_title == "SELECT SOURCE"
        assert state.discovered_sources == []
        assert state.selected_source_index == 0
        assert state.iface_selection_active is False
        assert state.available_interfaces == []
        assert state.selected_iface_index == 0

    def test_grid_config_is_applied(self) -> None:
        cfg = SimpleNamespace(
            grid=_make_grid_config(),
            marker=_make_marker_config(),
        )
        state = build_initial_overlay_state(cfg)
        assert state.grid_config == (30.0, 20.0, 2.0, 1.0, 2.0, 0.5)

    def test_marker_config_is_applied(self) -> None:
        cfg = SimpleNamespace(
            grid=_make_grid_config(),
            marker=_make_marker_config(),
        )
        state = build_initial_overlay_state(cfg)
        assert state.show_ball is False
        assert state.crosshair_color == "#ff0000"


def _make_zone(vertices: list[tuple[float, float]], *, enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        vertices=vertices,
        color="#ffcc00",
        name="Z",
        enabled=enabled,
    )


def _make_cfg(
    *,
    enabled: bool,
    show_overlay: bool,
    zones: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        grid=_make_grid_config(),
        trigger_zones=SimpleNamespace(
            enabled=enabled,
            show_overlay=show_overlay,
            zones=zones or [],
        ),
    )


class _StubZoneEngine:
    def __init__(self, states: list[tuple[int, bool, int]]) -> None:
        self._states = states

    def get_zone_states(self) -> list[tuple[int, bool, int]]:
        return self._states


class TestPopulateZoneOverlay:
    def test_show_overlay_alone_renders_polygons_when_triggers_disabled(self) -> None:
        """Overlay hotkey must still work when ``enabled=False``."""
        square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        cfg = _make_cfg(enabled=False, show_overlay=True, zones=[_make_zone(square)])
        app = SimpleNamespace(_runtime_services=SimpleNamespace(_zone_engine=None))
        state = OverlayState()

        _populate_zone_overlay(state, cfg, app)

        assert state.show_zones is True
        assert len(state.zone_polygons) == 1
        # Occupancy defaults to (False, 0) when the engine is not consulted.
        _, _, _, is_occupied, count = state.zone_polygons[0]
        assert is_occupied is False
        assert count == 0

    def test_hides_overlay_only_when_show_overlay_is_false(self) -> None:
        square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        cfg = _make_cfg(enabled=True, show_overlay=False, zones=[_make_zone(square)])
        app = SimpleNamespace(_runtime_services=SimpleNamespace(_zone_engine=None))
        state = OverlayState()

        _populate_zone_overlay(state, cfg, app)

        assert state.show_zones is False
        assert state.zone_polygons == []

    def test_engine_occupancy_is_used_when_enabled(self) -> None:
        square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        cfg = _make_cfg(enabled=True, show_overlay=True, zones=[_make_zone(square)])
        engine = _StubZoneEngine([(0, True, 3)])
        app = SimpleNamespace(_runtime_services=SimpleNamespace(_zone_engine=engine))
        state = OverlayState()

        _populate_zone_overlay(state, cfg, app)

        assert state.show_zones is True
        _, _, _, is_occupied, count = state.zone_polygons[0]
        assert is_occupied is True
        assert count == 3

    def test_engine_occupancy_is_ignored_when_disabled(self) -> None:
        """When triggers are off, occupancy must not be read from the engine."""
        square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        cfg = _make_cfg(enabled=False, show_overlay=True, zones=[_make_zone(square)])
        engine = _StubZoneEngine([(0, True, 5)])
        app = SimpleNamespace(_runtime_services=SimpleNamespace(_zone_engine=engine))
        state = OverlayState()

        _populate_zone_overlay(state, cfg, app)

        _, _, _, is_occupied, count = state.zone_polygons[0]
        assert is_occupied is False
        assert count == 0

    def test_enabled_but_engine_is_none_skips_occupancy_lookup(self) -> None:
        square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        cfg = _make_cfg(enabled=True, show_overlay=True, zones=[_make_zone(square)])
        app = SimpleNamespace(_runtime_services=SimpleNamespace(_zone_engine=None))
        state = OverlayState()

        _populate_zone_overlay(state, cfg, app)

        assert state.show_zones is True
        assert len(state.zone_polygons) == 1
        _, _, _, is_occupied, count = state.zone_polygons[0]
        assert is_occupied is False
        assert count == 0

    def test_disabled_and_degenerate_zones_are_skipped(self) -> None:
        """Cover the ``continue`` at ``services_marker_visuals.py:167``.

        The per-zone filter drops zones that are either:

        * individually disabled (the hotkey doesn't arm them) – saves
          the draw pass a render cycle on hidden polygons.
        * degenerate (< 3 vertices) – the fill-preserve path in
          ``draw_zones`` assumes a closeable path; a 2-vertex polygon
          would stroke into a stray line segment.
        """
        square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        disabled_zone = _make_zone(square, enabled=False)
        degenerate_zone = _make_zone([(0.0, 0.0), (1.0, 0.0)])  # 2 vertices
        live_zone = _make_zone(square)
        cfg = _make_cfg(
            enabled=True,
            show_overlay=True,
            zones=[disabled_zone, degenerate_zone, live_zone],
        )
        app = SimpleNamespace(_runtime_services=SimpleNamespace(_zone_engine=None))
        state = OverlayState()

        _populate_zone_overlay(state, cfg, app)

        # Only the one live zone survives the filter.
        assert len(state.zone_polygons) == 1
        verts, _color, _name, _is_occupied, _count = state.zone_polygons[0]
        assert verts == [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]


# --------------------------------------------------------------------------- #
# _soft_float / _soft_bool – defensive helpers for shim configs
# --------------------------------------------------------------------------- #


class TestSoftHelpersResidualBranches:
    """Cover residual branches in ``_soft_float`` and ``_soft_bool``."""

    def test_soft_float_clamps_finite_value_below_lo(self) -> None:
        """Cover ``services_marker_visuals.py:52`` – the lo-clamp.

        A shim that skipped ``GridConfig.__post_init__`` can smuggle a
        sub-0.1 spacing value past the dataclass guard. ``sync_grid_config``
        calls ``_soft_float(..., lo=0.1)`` so the overlay pass doesn't
        hit ``int(width / spacing)`` with a near-zero divisor.
        """
        state = OverlayState()
        gc = SimpleNamespace(
            width=0.05,  # below lo=0.1 – hits the clamp
            depth=0.02,  # below lo=0.1 – hits the clamp
            spacing=0.01,  # below lo=0.1 – hits the clamp
            x_offset=0.0,
            y_offset=3.0,
            z_offset=0.0,
            color="#abcdef",
            thickness=1,
            transparency=0.5,
            origin_visible=False,
            origin_length=0.0,  # below lo=0.1 – hits the clamp
            origin_thickness=3,
        )
        sync_grid_config(state, SimpleNamespace(grid=gc))
        gw, gd, gs, _gx, _gy, _gz = state.grid_config
        assert gw == 0.1
        assert gd == 0.1
        assert gs == 0.1
        assert state.origin_length == 0.1

    def test_soft_bool_unrecognised_string_falls_back_to_default(self) -> None:
        state = OverlayState()
        gc = SimpleNamespace(
            width=10.0,
            depth=6.0,
            spacing=1.0,
            x_offset=0.0,
            y_offset=3.0,
            z_offset=0.0,
            color="#abcdef",
            thickness=1,
            transparency=0.6,
            origin_visible="maybe",  # string, neither truthy nor falsy
            origin_length=1.0,
            origin_thickness=3,
        )
        sync_grid_config(state, SimpleNamespace(grid=gc))
        # Unrecognised string must fall back to the GridConfig default
        # (``origin_visible`` defaults to False).
        assert state.show_origin is False


class TestResolveMarkerColor:
    """Covers _resolve_marker_color: catalog hit vs fallback.
    New colors use catalog editor's JS or Python fallback."""

    def test_catalog_entry_color_takes_priority(self) -> None:
        """Catalog has the marker → return its color."""
        from openfollow.runtime.services_marker_visuals import _resolve_marker_color

        class _Catalog:
            def get(self, mid: int):
                if mid == 3:
                    return SimpleNamespace(color="#abcdef")
                return None

        app = SimpleNamespace(_marker_catalog=_Catalog())
        assert _resolve_marker_color(app, 3) == "#abcdef"

    def test_palette_fallback_when_catalog_missing_entry(self) -> None:
        """No catalog entry → indexed pick from ``AUTO_PICK_ORDER``,
        lowercased to match the catalog-stored form (so a caller
        comparing colours across catalog-hit and catalog-miss never
        sees a case mismatch)."""
        from openfollow.palette import AUTO_PICK_ORDER
        from openfollow.runtime.services_marker_visuals import _resolve_marker_color

        class _Catalog:
            def get(self, _mid: int):
                return None

        app = SimpleNamespace(_marker_catalog=_Catalog())
        assert _resolve_marker_color(app, 4) == AUTO_PICK_ORDER[4 % len(AUTO_PICK_ORDER)].lower()

    def test_palette_used_when_catalog_attr_absent(self) -> None:
        """No ``_marker_catalog`` attr on the app → same fallback."""
        from openfollow.palette import AUTO_PICK_ORDER
        from openfollow.runtime.services_marker_visuals import _resolve_marker_color

        app = SimpleNamespace()  # no _marker_catalog
        assert _resolve_marker_color(app, 5) == AUTO_PICK_ORDER[5 % len(AUTO_PICK_ORDER)].lower()


class TestResolveMarkerName:
    """``_resolve_marker_name`` mirrors the colour helper: catalog hit
    returns the entry's ``name``, everything else returns ``""`` so
    the HUD's ``t.name or f"M{id}"`` branch renders the fallback
    label."""

    def test_catalog_entry_name_returned(self) -> None:
        from openfollow.runtime.services_marker_visuals import _resolve_marker_name

        class _Catalog:
            def get(self, mid: int):
                if mid == 3:
                    return SimpleNamespace(name="House Left")
                return None

        app = SimpleNamespace(_marker_catalog=_Catalog())
        assert _resolve_marker_name(app, 3) == "House Left"

    def test_no_catalog_entry_returns_empty(self) -> None:
        from openfollow.runtime.services_marker_visuals import _resolve_marker_name

        class _Catalog:
            def get(self, _mid: int):
                return None

        app = SimpleNamespace(_marker_catalog=_Catalog())
        assert _resolve_marker_name(app, 4) == ""

    def test_no_catalog_attr_returns_empty(self) -> None:
        from openfollow.runtime.services_marker_visuals import _resolve_marker_name

        assert _resolve_marker_name(SimpleNamespace(), 5) == ""


class TestPopulatePiNetworkOverlay:
    """Copies Network screen state into OverlayState for draw pass."""

    def _bare_app(self) -> SimpleNamespace:
        return SimpleNamespace()

    def test_all_inactive_resets_target(self) -> None:
        state = OverlayState()
        # Dirty the slot first so reset() is observable.
        state.pi_network.screen_active = True
        state.pi_network.banner = "stale"
        _populate_pi_network_overlay(self._bare_app(), state)
        assert state.pi_network.screen_active is False
        assert state.pi_network.banner == ""

    def test_screen_active_copies_rows_and_metadata(self, monkeypatch) -> None:
        from openfollow.runtime import app_modes_network as anm

        sentinel_rows = [{"kind": "header", "label": "Interface"}]
        monkeypatch.setattr(anm, "build_pi_network_rows", lambda _app: sentinel_rows)
        app = SimpleNamespace(
            _pi_network_active=True,
            _pi_network_index=3,
            _pi_network_active_iface="eth0",
            _pi_network_banner="Apply ok.",
        )
        state = OverlayState()
        _populate_pi_network_overlay(app, state)
        assert state.pi_network.screen_active is True
        assert state.pi_network.rows == sentinel_rows
        assert state.pi_network.selected_index == 3
        assert state.pi_network.active_iface == "eth0"
        assert state.pi_network.banner == "Apply ok."

    def test_iface_picker_active_copies_names(self) -> None:
        from openfollow.network.adapter import NetworkInterface

        app = SimpleNamespace(
            _pi_network_iface_picker_active=True,
            _pi_network_iface_picker_index=1,
            _pi_network_interfaces=[
                NetworkInterface(name="eth0", mac=None, kind=None, is_up=True),
                NetworkInterface(name="wlan0", mac=None, kind=None, is_up=False),
            ],
        )
        state = OverlayState()
        _populate_pi_network_overlay(app, state)
        assert state.pi_network.iface_picker_active is True
        assert state.pi_network.iface_picker_items == ["eth0", "wlan0"]
        assert state.pi_network.iface_picker_selected_index == 1

    def test_method_picker_active_copies_labels(self) -> None:
        app = SimpleNamespace(
            _pi_network_method_picker_active=True,
            _pi_network_method_picker_index=2,
        )
        state = OverlayState()
        _populate_pi_network_overlay(app, state)
        assert state.pi_network.method_picker_active is True
        assert "DHCP" in state.pi_network.method_picker_items
        assert state.pi_network.method_picker_selected_index == 2

    def test_field_edit_active_humanises_label(self) -> None:
        """``dns_1`` → "Dns 1"; ``ip_address`` → "Ip Address"."""
        app = SimpleNamespace(
            _pi_network_field_edit_active=True,
            _pi_network_field_name="dns_1",
            _pi_network_field_value="8.8.8.8",
        )
        state = OverlayState()
        _populate_pi_network_overlay(app, state)
        assert state.pi_network.field_edit_active is True
        assert state.pi_network.field_label == "Dns 1"
        assert state.pi_network.field_value == "8.8.8.8"


class _TornMarker:
    """Marker stub whose ``pos`` property returns a *different* whole-tuple
    snapshot on every access – simulating the PSN receiver thread calling
    ``set_pos`` between two reads. A correct consumer reads ``pos`` once, so
    its card's x/y/z come from a single packet; a torn consumer reading
    ``pos[0]``/``pos[1]``/``pos[2]`` separately mixes two packets.
    """

    def __init__(self, packets: list[tuple[float, float, float]]) -> None:
        self._packets = packets
        self._idx = 0

    @property
    def pos(self) -> tuple[float, float, float]:
        packet = self._packets[min(self._idx, len(self._packets) - 1)]
        self._idx += 1
        return packet

    @property
    def speed(self) -> tuple[float, float, float]:
        return (0.0, 0.0, 0.0)


def _make_full_marker_config() -> SimpleNamespace:
    tc = _make_marker_config()
    tc.ball_size = 0.15
    return tc


def _make_visual_app(marker: object, *, controlled: bool) -> SimpleNamespace:
    """Minimal app shim driving ``build_marker_visual_state`` down the
    viewer-marker card-build path with everything else inert.
    """
    cfg = SimpleNamespace(
        video_source_type="ndi",
        web_port=80,
        psn_system_name="OF",
        marker=_make_full_marker_config(),
        grid=_make_grid_config(),
        camera=SimpleNamespace(lens_k1=0.0, lens_k2=0.0),
        ui=SimpleNamespace(unit_system="metric"),
        controller=SimpleNamespace(
            enabled=False,
            keyboard_enabled=False,
            mouse_enabled=False,
            mouse_double_click_reset=True,
            btn_reset="",
            btn_toggle_help="",
            btn_toggle_zones="",
            btn_speed_down="",
            btn_speed_up="",
            btn_move_z_down="",
            btn_move_z_up="",
            btn_next_marker="",
            btn_prev_marker="",
            btn_settings="",
            btn_menu_confirm="",
            btn_menu_cancel="",
            move_xy_stick="",
            key_move_layout="",
            key_move_z_up="",
            key_move_z_down="",
            key_reset="",
            key_toggle_help="",
            key_toggle_zones="",
            key_speed_down="",
            key_speed_up="",
            key_next_marker="",
            key_prev_marker="",
            key_settings="",
        ),
        trigger_zones=SimpleNamespace(enabled=False, show_overlay=False, zones=[]),
        operator_messages=SimpleNamespace(enabled=False),
        detection=SimpleNamespace(enabled=False, pin_marker=False, pin_mode="replace", pin_marker_id=-1),
    )
    video_receiver = SimpleNamespace(
        status_marker=SimpleNamespace(
            snapshot=lambda: SimpleNamespace(is_connected=False, reconnect_attempt=0, error_message="")
        ),
        source_name="",
        source_selection_active=False,
        discovered_sources=[],
        selected_source_index=0,
        source_selection_title="",
    )
    camera = SimpleNamespace(
        to_config=lambda: SimpleNamespace(pos_x=0.0, pos_y=0.0, pos_z=0.0, pitch=0.0, yaw=0.0, roll=0.0, fov=60.0)
    )
    receiver = SimpleNamespace(
        get_marker=lambda _tid: marker,
        is_marker_online=lambda _tid: False,
    )
    server = SimpleNamespace(get_marker=lambda _tid: marker)
    return SimpleNamespace(
        _config=cfg,
        _controlled_ids=[1] if controlled else [],
        _viewer_ids=[1],
        _input_manager=None,
        _video_receiver=video_receiver,
        _iface_selection_active=False,
        _available_interfaces=[],
        _selected_iface_index=0,
        _source_type_selection_active=False,
        _available_source_types=[],
        _selected_source_type_index=0,
        _url_editor_active=False,
        _field_choice_active=False,
        _settings_menu_active=False,
        _selected_id=None,
        _server=server,
        _psn_receiver=receiver,
        _marker_catalog=None,
        _camera=camera,
        _button_detection=None,
        _show_hud_help=False,
        _runtime_services=None,
        _assist_manual={},
    )


class TestBuildMarkerVisualStateTornRead:
    def test_viewer_marker_card_uses_single_pos_snapshot(self) -> None:
        # Two distinct packets: a correct single read yields one of them
        # verbatim; a torn three-read mixes (10,21,32).
        marker = _TornMarker([(10.0, 20.0, 30.0), (11.0, 21.0, 31.0), (12.0, 22.0, 32.0)])
        app = _make_visual_app(marker, controlled=False)

        state = build_marker_visual_state(
            app,
            overlay_state_pool=OverlayStatePool(),
            system_stats=None,
            person_detector=None,
            cam_params_buffer=np.zeros(7),
        )

        assert len(state.markers) == 1
        card = state.markers[0]
        # All three components must come from the same packet – the first one,
        # since a correct consumer reads ``pos`` exactly once.
        assert (card.x, card.y, card.z) == (10.0, 20.0, 30.0)

    def test_lens_coefficients_are_copied_from_config(self) -> None:
        # The overlay warp reads k1/k2 off the live config (the Camera object is
        # pinhole), so a slider edit must land on the state for the renderer.
        marker = _TornMarker([(0.0, 0.0, 0.0)])
        app = _make_visual_app(marker, controlled=False)
        app._config.camera.lens_k1 = -0.15
        app._config.camera.lens_k2 = 0.04

        state = build_marker_visual_state(
            app,
            overlay_state_pool=OverlayStatePool(),
            system_stats=None,
            person_detector=None,
            cam_params_buffer=np.zeros(7),
        )

        assert state.lens_k1 == -0.15
        assert state.lens_k2 == 0.04
