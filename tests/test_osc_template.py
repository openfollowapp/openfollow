# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for the OSC template engine.

Pure-function module; no I/O, no threads. Covers the placeholder grammar
``[source(:index)(.transform)*]``: source parsing, positional ``:N``
references, the ``inv`` / ``frac`` transforms, OSC type-tag inference,
rejection of removed spellings, and the wire format of every built-in
template.

The fader / ``markerfader`` / event sources and the ``pct`` / ``int`` /
``scale`` transforms are covered in :mod:`tests.test_template_phase_b`.
"""

from __future__ import annotations

import pytest

from openfollow.osc.template import (
    BUILTIN_TEMPLATES,
    PLACEHOLDERS,
    RenderContext,
    RenderError,
    builtin_by_id,
    compile_template,
    constant_arg_value,
    osc_arg_for,
    render,
    requires_default_marker,
    unresolved_placeholders,
)

pytestmark = pytest.mark.unit


def _ctx(
    pos: tuple[float, float, float] = (1.0, 2.0, 3.0),
    grid_w: float = 10.0,
    grid_d: float = 6.0,
    grid_h: float = 0.0,
    z_offset: float = 0.0,
    marker_id: int | None = 7,
    marker_resolver=None,  # noqa: ANN001 - test helper
    marker_fader_resolver=None,  # noqa: ANN001 - test helper
    controller_marker_resolver=None,  # noqa: ANN001 - test helper
) -> RenderContext:
    # grid_h=0.0 means unset; resolvers default None (only the
    # explicit-marker / [markerfader] / [:cN] tests supply them).
    return RenderContext(
        pos_m=pos,
        grid_w=grid_w,
        grid_d=grid_d,
        grid_h=grid_h,
        z_offset=z_offset,
        marker_id=marker_id,
        marker_resolver=marker_resolver,
        marker_fader_resolver=marker_fader_resolver,
        controller_marker_resolver=controller_marker_resolver,
    )


def _resolver_factory(by_id: dict[int, tuple[float, float, float]]):  # noqa: ANN202
    """marker_resolver returning a stored position by id, or None for
    unmapped ids."""
    return lambda tid: by_id.get(tid)


def _is_literal(s: str) -> bool:
    """True when ``s`` compiles to a single literal part (not recognised
    as a placeholder)."""
    ct = compile_template(s)
    return len(ct) == 1 and type(ct[0]).__name__ == "_Literal"


# ---------------------------------------------------------------------------
# Placeholder source set
# ---------------------------------------------------------------------------


def test_placeholder_set_is_the_unified_sources() -> None:
    """``PLACEHOLDERS`` is the flat set of *sources*. The index /
    transform grammar isn't enumerable here – the client recogniser
    mirrors it with regexes (parity gate
    ``tests/test_web_osc_placeholder_recognition.py``)."""
    assert set(PLACEHOLDERS) == {
        "x",
        "y",
        "z",
        "markerid",
        "fader",
        "markerfader",
        "value",
        "velocity",
        "note",
    }


# ---------------------------------------------------------------------------
# Removed spellings now read as literal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "removed",
    [
        "[ix]",
        "[iy]",
        "[iz]",
        "[fx]",
        "[fy]",
        "[fz]",
        "[ifx]",
        "[ify]",
        "[ifz]",
        "[markerId]",
        "[float]",
        "[float:fader3]",
        "[int:0-127]",
        "[int:0-127:fader2]",
        "[x:marker3]",
        "[markerId:marker7]",
        "[markerfader:marker3]",
    ],
)
def test_removed_grammar_compiles_to_literal(removed: str) -> None:
    """Removed spellings are no longer recognised and render verbatim,
    like any unknown token."""
    assert _is_literal(removed)
    assert render(compile_template(removed), _ctx()) == removed


@pytest.mark.parametrize(
    "bad",
    [
        "[bogus]",
        "[bogus:3]",
        "[x.pct]",  # transform not allowed for a position source
        "[x.scale:0-1]",
        "[fader.frac]",  # frac is position-only
        "[markerid.inv]",  # markerid takes no transform
        "[value:3]",  # event sources take no index
        "[note.inv]",
        "[x:]",  # empty index
        "[x:3foo]",  # trailing garbage after the index
        "[fader.bogus]",  # unknown transform keyword
        "[fader.int:0-]",  # malformed range (missing max)
        "[fader.int:abc]",  # non-numeric range
        "[fader.int:0-1.]",  # trailing decimal point, no digit
        "[fader.scale:0-1.5.7]",  # two decimal points
        "[z.frac:2]",  # index must precede transforms
        "[markerfader.int:0-]",
    ],
)
def test_invalid_grammar_compiles_to_literal(bad: str) -> None:
    assert _is_literal(bad)


def test_unicode_digit_index_and_range_compile_to_literal_not_crash() -> None:
    """Index and range digits are ASCII-only.

    ``str.isdigit()`` is True for Unicode digits too. Superscript /
    circled ones (U+00B2 / U+2460) crash ``int()``; the Unicode-decimal
    set ``int()`` accepts (Arabic-Indic U+0660..U+0669) diverges from the
    ASCII-``\\d`` client recogniser. All must fall back to literal;
    compile_template must never raise."""
    for bad in (
        "/cue [x:²]",  # superscript two
        "[markerfader:①]",  # circled one
        "[x:٥]",  # Arabic-Indic five (int() accepts, ASCII client doesn't)
        "[fader.int:0-٩]",  # Unicode digit as a range bound
        "[fader.int:٠-٩]",
    ):
        assert _is_literal(bad), bad


def test_overlong_digit_run_compiles_to_literal_not_crash() -> None:
    """A digit run past ``int()``'s string-conversion limit (default 4300)
    must fall back to literal at all three parse sites, not raise."""
    run = "9" * 5000
    for bad in (
        f"[x:{run}]",  # :N marker index
        f"[markerid:c{run}]",  # :cN controller index
        f"[fader.int:0-{run}]",  # range bound
        f"[fader.scale:-{run}-1]",  # negative range bound
    ):
        assert _is_literal(bad), bad


def test_token_has_explicit_index() -> None:
    """Classifies tokens as explicit ``[x:7]`` vs default-marker ``[x]``
    without colon-sniffing, so a transform-borne colon (range bound)
    can't fool it."""
    from openfollow.osc.template import token_has_explicit_index

    assert token_has_explicit_index("[x:7]") is True
    assert token_has_explicit_index("[markerfader:3]") is True
    assert token_has_explicit_index("[x]") is False
    assert token_has_explicit_index("[z.frac]") is False
    # A hypothetical colon-bearing transform must NOT read as an index.
    assert token_has_explicit_index("[fader.int:0-100]") is False
    # Bracket-stripping is defensive – a bare inner name works too.
    assert token_has_explicit_index("x:7") is True
    assert token_has_explicit_index("bogus") is False


def test_render_unknown_placeholder_passes_through_as_literal() -> None:
    ct = compile_template("/cue/[unknown]/[markerid]")
    assert render(ct, _ctx(marker_id=3)) == "/cue/[unknown]/3"


def test_render_unmatched_open_bracket_treated_as_literal() -> None:
    ct = compile_template("/foo/[broken")
    assert render(ct, _ctx()) == "/foo/[broken"


def test_compile_empty_string_yields_empty_template() -> None:
    assert compile_template("") == ()
    assert render((), _ctx()) == ""


# ---------------------------------------------------------------------------
# Position sources – raw, .inv, .frac, chains
# ---------------------------------------------------------------------------


def test_render_absolute_passes_meters_through() -> None:
    ct = compile_template("/x=[x] y=[y] z=[z]")
    out = render(ct, _ctx(pos=(1.5, -2.25, 0.5)))
    assert out == "/x=1.5 y=-2.25 z=0.5"


def test_render_inverted_negates() -> None:
    ct = compile_template("/i=[x.inv],[y.inv],[z.inv]")
    out = render(ct, _ctx(pos=(1.0, 2.0, 3.0)))
    assert out == "/i=-1,-2,-3"


def test_render_consecutive_placeholders() -> None:
    ct = compile_template("[x][y][z]")
    assert render(ct, _ctx(pos=(1.0, 2.0, 3.0))) == "123"


def test_render_uses_g_format_for_floats() -> None:
    """`g` format trims trailing zeros so [x] doesn't read as 1.0000000000."""
    ct = compile_template("[x]")
    assert render(ct, _ctx(pos=(1.0, 0.0, 0.0))) == "1"


def test_absolute_placeholders_pass_through_when_outside_grid() -> None:
    """Outside the grid, [x]/[y]/[z] still emit absolute metres
    unchanged. Only ``.frac`` clamps."""
    ct = compile_template("[x]")
    assert render(ct, _ctx(pos=(12.0, 0.0, 0.0), grid_w=10.0)) == "12"


def test_frac_clamps_at_positive_boundary() -> None:
    ct = compile_template("[x.frac]")
    assert render(ct, _ctx(pos=(12.0, 0.0, 0.0), grid_w=10.0)) == "1"


def test_frac_clamps_at_negative_boundary() -> None:
    ct = compile_template("[y.frac]")
    assert render(ct, _ctx(pos=(0.0, -7.0, 0.0), grid_d=6.0)) == "-1"


def test_frac_inverted_clamps_after_chain() -> None:
    """``[x.frac.inv]`` = -clamp(x / (W/2)). x=12 → frac 1 → inv -1."""
    ct = compile_template("[x.frac.inv]")
    assert render(ct, _ctx(pos=(12.0, 0.0, 0.0), grid_w=10.0)) == "-1"
    ct2 = compile_template("[y.frac.inv]")
    assert render(ct2, _ctx(pos=(0.0, 7.0, 0.0), grid_d=6.0)) == "-1"


def test_frac_inside_grid_yields_fraction() -> None:
    ct = compile_template("[x.frac]")
    # x=2.5 on a 10 m grid → 2.5 / 5 = 0.5
    assert render(ct, _ctx(pos=(2.5, 0.0, 0.0), grid_w=10.0)) == "0.5"


def test_frac_with_non_positive_extent_returns_zero() -> None:
    """Zero/negative grid extent collapses to 0 instead of raising
    ZeroDivisionError."""
    ct = compile_template("[x.frac]")
    assert render(ct, _ctx(pos=(1.0, 0.0, 0.0), grid_w=0.0)) == "0"
    assert render(ct, _ctx(pos=(1.0, 0.0, 0.0), grid_w=-3.0)) == "0"


def test_inv_then_frac_order_matters() -> None:
    """Transforms fold left-to-right: ``[x.inv.frac]`` negates raw metres
    then normalises – differs from ``[x.frac.inv]`` only when it
    saturates."""
    ct = compile_template("[x.inv.frac]")
    # x=2.5 → inv -2.5 → /5 → -0.5
    assert render(ct, _ctx(pos=(2.5, 0.0, 0.0), grid_w=10.0)) == "-0.5"


# ---------------------------------------------------------------------------
# Fractional Z ([z.frac]) – full denominator + RenderError on unset height
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "z, z_offset, max_height, expected",
    [
        (0.0, 0.0, 4.0, 0.0),
        (4.0, 0.0, 4.0, 1.0),
        (2.0, 0.0, 4.0, 0.5),
        (5.0, 0.0, 4.0, 1.0),
        (100.0, 0.0, 4.0, 1.0),
        (-2.0, 0.0, 4.0, -0.5),
        (-4.0, 0.0, 4.0, -1.0),
        (-100.0, 0.0, 4.0, -1.0),
        (1.0, 0.0, 4.0, 0.25),
        (1.0, 1.0, 4.0, 0.0),  # z == z_offset → floor of the volume
        (5.0, 1.0, 4.0, 1.0),  # z == z_offset + max_height → ceiling
        (-1.0, 1.0, 4.0, -0.5),  # 2 m below the raised floor
    ],
)
def test_z_frac_formula_at_boundaries(
    z: float,
    z_offset: float,
    max_height: float,
    expected: float,
) -> None:
    ct = compile_template("[z.frac]")
    rc = _ctx(pos=(0.0, 0.0, z), grid_h=max_height, z_offset=z_offset)
    typetag, value = osc_arg_for(ct, rc)
    assert typetag == "f"
    assert value == pytest.approx(expected)


def test_z_frac_inv_is_negation_of_z_frac() -> None:
    """``[z.frac.inv]`` is a pure sign flip of ``[z.frac]``."""
    ct_fz = compile_template("[z.frac]")
    ct_ifz = compile_template("[z.frac.inv]")
    for z in (-3.0, -1.0, 0.0, 1.0, 3.0):
        rc = _ctx(pos=(0.0, 0.0, z), grid_h=4.0, z_offset=0.0)
        fz = osc_arg_for(ct_fz, rc)[1]
        ifz = osc_arg_for(ct_ifz, rc)[1]
        assert ifz == pytest.approx(-fz), f"z={z}: fz={fz}, ifz={ifz}"


@pytest.mark.parametrize("tpl", ["[z.frac]", "[z.frac.inv]"])
def test_z_frac_raises_render_error_when_max_height_unset(tpl: str) -> None:
    """``z.frac`` raises (doesn't silently return 0) when ``max_height``
    is unset, same as unresolved ``[x:N]``. The exception carries the
    placeholder token so the ring buffer can render it verbatim."""
    ct = compile_template(tpl)
    rc = _ctx(pos=(0.0, 0.0, 1.0), grid_h=0.0)
    with pytest.raises(RenderError) as ei:
        osc_arg_for(ct, rc)
    assert ei.value.placeholder == tpl[1:-1]


def test_z_frac_render_error_on_negative_max_height() -> None:
    """``_clamped_z_fraction`` defends against a context carrying a
    negative ``grid_h`` (``GridConfig`` itself clamps negatives to 0)."""
    ct = compile_template("[z.frac]")
    rc = _ctx(pos=(0.0, 0.0, 1.0), grid_h=-2.0)
    with pytest.raises(RenderError):
        osc_arg_for(ct, rc)


def test_z_frac_render_error_carries_actionable_hint() -> None:
    ct = compile_template("[z.frac]")
    rc = _ctx(pos=(0.0, 0.0, 1.0), grid_h=0.0)
    with pytest.raises(RenderError) as ei:
        osc_arg_for(ct, rc)
    assert ei.value.placeholder == "z.frac"
    assert "Maximum Height" in ei.value.hint
    assert "Maximum Height" in str(ei.value)


# ---------------------------------------------------------------------------
# markerid source
# ---------------------------------------------------------------------------


def test_render_markerid_substitutes_int() -> None:
    ct = compile_template("/eos/set/patch/[markerid]/augment3d/position")
    assert render(ct, _ctx(marker_id=42)) == "/eos/set/patch/42/augment3d/position"


def test_markerid_alone_is_int_typed() -> None:
    ct = compile_template("[markerid]")
    assert osc_arg_for(ct, _ctx(marker_id=4)) == ("i", 4)


def test_explicit_markerid_returns_referenced_id() -> None:
    """``[markerid:7]`` resolves to literal 7 (names the marker, no
    lookup); the default marker is ignored."""
    ct = compile_template("[markerid:7]")
    rc = _ctx(marker_id=42)
    assert osc_arg_for(ct, rc) == ("i", 7)


# ---------------------------------------------------------------------------
# [markerfader] / [markerfader:N] – per-marker fader source
# ---------------------------------------------------------------------------


class TestMarkerFaderSource:
    def test_in_placeholder_set(self) -> None:
        assert "markerfader" in PLACEHOLDERS

    def test_requires_default_marker(self) -> None:
        # Bare [markerfader] resolves the row's marker, so it depends on
        # the default marker.
        assert requires_default_marker(compile_template("/w/[markerfader]"))

    def test_renders_default_marker_fader_value(self) -> None:
        ct = compile_template("/w/[markerfader]")
        out = render(
            ct,
            _ctx(marker_id=3, marker_fader_resolver=lambda m: 0.42 if m == 3 else None),
        )
        assert out == "/w/0.42"

    def test_explicit_marker_ref_resolves_named_marker(self) -> None:
        """``[markerfader:3]`` targets marker 3's fader directly,
        regardless of the row's default marker."""
        ct = compile_template("/w/[markerfader:3]")
        out = render(
            ct,
            _ctx(marker_id=99, marker_fader_resolver=lambda m: 0.6 if m == 3 else None),
        )
        assert out == "/w/0.6"

    def test_explicit_marker_ref_does_not_need_default_marker(self) -> None:
        # A row using only [markerfader:3] must dispatch with no default.
        assert requires_default_marker(compile_template("[markerfader:3]")) is False
        ct = compile_template("[markerfader:3]")
        out = render(ct, _ctx(marker_id=None, marker_fader_resolver=lambda m: 0.5))
        assert out == "0.5"

    def test_pct_transform_emits_percent(self) -> None:
        ct = compile_template("[markerfader.pct]")
        rc = _ctx(marker_id=3, marker_fader_resolver=lambda m: 0.5)
        assert osc_arg_for(ct, rc) == ("f", pytest.approx(50.0))

    def test_explicit_ref_with_int_transform(self) -> None:
        ct = compile_template("[markerfader:3.int:0-100]")
        rc = _ctx(marker_id=99, marker_fader_resolver=lambda m: 0.5 if m == 3 else None)
        assert osc_arg_for(ct, rc) == ("i", 50)

    def test_no_default_marker_raises(self) -> None:
        ct = compile_template("/w/[markerfader]")
        with pytest.raises(RenderError) as exc:
            render(ct, _ctx(marker_id=None, marker_fader_resolver=lambda m: 0.5))
        assert exc.value.hint == "no default marker configured"

    def test_resolver_unwired_raises(self) -> None:
        ct = compile_template("/w/[markerfader]")
        with pytest.raises(RenderError) as exc:
            render(ct, _ctx(marker_id=3, marker_fader_resolver=None))
        assert "resolver not wired" in exc.value.hint

    def test_marker_without_fader_raises(self) -> None:
        ct = compile_template("/w/[markerfader]")
        with pytest.raises(RenderError) as exc:
            render(ct, _ctx(marker_id=9, marker_fader_resolver=lambda m: None))
        assert "marker 9 has no fader" in exc.value.hint

    def test_explicit_ref_without_fader_names_referenced_marker(self) -> None:
        ct = compile_template("[markerfader:5]")
        with pytest.raises(RenderError) as exc:
            render(ct, _ctx(marker_id=3, marker_fader_resolver=lambda m: None))
        assert "marker 5 has no fader" in exc.value.hint
        assert exc.value.placeholder == "markerfader:5"


# ---------------------------------------------------------------------------
# [source:cN] – controller reference ("the marker controller N drives")
# ---------------------------------------------------------------------------


class TestControllerReference:
    """``:cN`` resolves the marker controller ``N`` (1-based) currently
    drives at render time, then applies the source to that marker. Accepted
    only on the marker-keyed sources; ``c0`` / leading-zero / non-marker
    sources fall back to literal text."""

    @pytest.mark.parametrize(
        "tpl,expected_index",
        [
            ("markerid:c1", 0),
            ("markerfader:c2", 1),
            ("x:c1", 0),
            ("y:c10", 9),
            ("z:c1.frac", 0),
            ("markerfader:c1.int:0-100", 0),
        ],
    )
    def test_parses_to_controller_slot(self, tpl: str, expected_index: int) -> None:
        from openfollow.osc.template import _slot_from_name

        slot = _slot_from_name(tpl)
        assert slot is not None
        assert slot.controller_index == expected_index
        assert slot.ref_index is None

    @pytest.mark.parametrize(
        "bad",
        [
            "[fader:c1]",  # fader is fader-indexed, not marker-keyed
            "[markerid:c0]",  # 1-based – no controller 0
            "[markerfader:c0]",
            "[markerid:c01]",  # leading zero rejected
            "[x:cx]",  # non-numeric index
            "[markerid:c]",  # empty index
            "[value:c1]",  # event sources take no index
            "[velocity:c1]",
        ],
    )
    def test_invalid_controller_ref_is_literal(self, bad: str) -> None:
        assert _is_literal(bad)

    def test_excluded_from_requires_default_marker(self) -> None:
        # A cN-only row resolves its own marker – never gated on the
        # row's default marker.
        assert requires_default_marker(compile_template("[markerid:c1]")) is False
        assert requires_default_marker(compile_template("/a/[x:c1] [markerfader:c2]")) is False

    def test_excluded_from_unresolved_placeholders(self) -> None:
        # cN slots are runtime skips, not edit-time pills – even on a
        # position source that is otherwise edit-time-highlighted.
        parts = compile_template("/a/[x:c1] [markerid:c2] [z:c1.frac]")
        assert (
            unresolved_placeholders(
                parts,
                default_marker_id=None,
                registered_marker_ids=frozenset(),
                grid_max_height=0.0,
            )
            == ()
        )

    def test_token_has_explicit_index_true_for_controller_ref(self) -> None:
        from openfollow.osc.template import token_has_explicit_index

        assert token_has_explicit_index("[markerid:c1]") is True
        assert token_has_explicit_index("[x:c2]") is True

    def test_slot_inner_round_trips_controller_ref(self) -> None:
        # The RenderError label (built from _slot_inner) names the cN form,
        # and re-compiling it yields the same slot.
        from openfollow.osc.template import _slot_from_name, _slot_inner

        for inner in ("markerid:c1", "markerfader:c1.int:0-100", "x:c10"):
            slot = _slot_from_name(inner)
            assert slot is not None
            # _slot_inner reconstructs the operator-facing token, and
            # re-compiling that token yields a slot equal to the original.
            assert _slot_inner(slot) == inner
            assert _slot_from_name(_slot_inner(slot)) == slot
            # Re-render path exercises the label through a forced miss.
            ct = compile_template(f"[{inner}]")
            with pytest.raises(RenderError) as exc:
                render(ct, _ctx(marker_id=None, controller_marker_resolver=lambda _i: None))
            assert exc.value.placeholder == inner

    def test_markerid_resolves_controller_marker_as_int(self) -> None:
        ct = compile_template("[markerid:c1]")
        rc = _ctx(marker_id=None, controller_marker_resolver=lambda idx: 42 if idx == 0 else None)
        # markerid stays int-typed and is the resolved id, not a literal.
        assert osc_arg_for(ct, rc) == ("i", 42)

    def test_markerfader_resolves_via_controller_marker(self) -> None:
        ct = compile_template("[markerfader:c1.int:0-100]")
        rc = _ctx(
            marker_id=None,
            controller_marker_resolver=lambda idx: 5 if idx == 0 else None,
            marker_fader_resolver=lambda m: 0.5 if m == 5 else None,
        )
        assert osc_arg_for(ct, rc) == ("i", 50)

    def test_position_resolves_via_controller_marker(self) -> None:
        ct = compile_template("[x:c1.frac]")
        rc = _ctx(
            grid_w=10.0,
            marker_id=None,
            controller_marker_resolver=lambda idx: 3 if idx == 0 else None,
            marker_resolver=_resolver_factory({3: (2.5, 0.0, 0.0)}),
        )
        # 2.5 / (10/2) = 0.5
        assert osc_arg_for(ct, rc) == ("f", pytest.approx(0.5))

    def test_controller_drives_no_marker_skips_with_reason(self) -> None:
        ct = compile_template("[markerid:c1]")
        with pytest.raises(RenderError) as exc:
            render(ct, _ctx(marker_id=None, controller_marker_resolver=lambda _i: None))
        assert exc.value.hint == "controller 1 controls no marker"
        assert exc.value.placeholder == "markerid:c1"

    def test_resolver_unwired_raises(self) -> None:
        ct = compile_template("[markerfader:c1]")
        with pytest.raises(RenderError) as exc:
            render(ct, _ctx(marker_id=None, controller_marker_resolver=None))
        assert exc.value.hint == "controller marker resolver not wired"

    def test_position_controller_marker_unregistered_raises(self) -> None:
        # Controller resolves to a marker id, but the position resolver has
        # no such marker – surfaces the cN label, no hint (same shape as
        # an unregistered [x:N]).
        ct = compile_template("[x:c1]")
        rc = _ctx(
            marker_id=None,
            controller_marker_resolver=lambda _i: 7,
            marker_resolver=_resolver_factory({3: (1.0, 2.0, 3.0)}),
        )
        with pytest.raises(RenderError) as exc:
            render(ct, rc)
        assert exc.value.placeholder == "x:c1"


# ---------------------------------------------------------------------------
# Explicit-marker references [x:N] + RenderError
# ---------------------------------------------------------------------------


def test_render_explicit_marker_uses_resolver() -> None:
    ct = compile_template("[x:3]")
    rc = _ctx(
        pos=(0.0, 0.0, 0.0),
        marker_id=0,
        marker_resolver=_resolver_factory({3: (12.34, 0.0, 0.0)}),
    )
    assert render(ct, rc) == "12.34"


def test_render_explicit_marker_unregistered_raises_render_error() -> None:
    ct = compile_template("[x:70]")
    rc = _ctx(marker_id=0, marker_resolver=_resolver_factory({3: (1.0, 2.0, 3.0)}))
    with pytest.raises(RenderError) as ei:
        render(ct, rc)
    assert ei.value.placeholder == "x:70"


def test_render_explicit_marker_without_resolver_raises_render_error() -> None:
    ct = compile_template("[x:3]")
    rc = _ctx()  # no marker_resolver
    with pytest.raises(RenderError) as ei:
        render(ct, rc)
    assert ei.value.placeholder == "x:3"


def test_explicit_marker_arg_emits_float_typetag() -> None:
    ct = compile_template("[x:3]")
    rc = _ctx(marker_id=0, marker_resolver=_resolver_factory({3: (1.5, 0.0, 0.0)}))
    assert osc_arg_for(ct, rc) == ("f", 1.5)


def test_explicit_marker_with_transform_chain() -> None:
    """``[x:3.inv]`` inverts the explicit marker's x (the chain folds
    over the referenced value), not the default marker's."""
    ct = compile_template("[x:3.inv]")
    rc = _ctx(pos=(99.0, 0.0, 0.0), marker_id=0, marker_resolver=_resolver_factory({3: (5.0, 0.0, 0.0)}))
    assert render(ct, rc) == "-5"


def test_render_explicit_marker_renders_when_marker_id_is_none() -> None:
    """Explicit ``[x:3]`` goes through the resolver, not
    ``ctx.marker_id``, so it renders even with no default marker."""
    ct = compile_template("[x:3]")
    rc = _ctx(marker_id=None, marker_resolver=_resolver_factory({3: (1.5, 0.0, 0.0)}))
    assert render(ct, rc) == "1.5"


# ---------------------------------------------------------------------------
# OSC argument typing – auto-inferred wire types
# ---------------------------------------------------------------------------


def test_osc_arg_for_single_numeric_slot_is_float() -> None:
    ct = compile_template("[x]")
    assert osc_arg_for(ct, _ctx(pos=(1.5, 0.0, 0.0))) == ("f", pytest.approx(1.5))


def test_osc_arg_for_frac_inv_chain_is_float() -> None:
    ct = compile_template("[x.frac.inv]")
    typetag, value = osc_arg_for(ct, _ctx(pos=(2.5, 0.0, 0.0), grid_w=10.0))
    assert typetag == "f"
    assert value == pytest.approx(-0.5)


def test_osc_arg_for_bare_int_literal_is_int() -> None:
    assert osc_arg_for(compile_template("0"), _ctx()) == ("i", 0)


def test_osc_arg_for_bare_negative_int_literal_is_int() -> None:
    assert osc_arg_for(compile_template("-3"), _ctx()) == ("i", -3)


def test_osc_arg_for_bare_float_literal_is_float() -> None:
    typetag, value = osc_arg_for(compile_template("0.0"), _ctx())
    assert typetag == "f"
    assert value == pytest.approx(0.0)


def test_osc_arg_for_non_numeric_literal_is_string() -> None:
    assert osc_arg_for(compile_template("hello"), _ctx()) == ("s", "hello")


def test_osc_arg_for_mixed_template_is_string() -> None:
    """Mixed slot + literal text renders to a single 's' arg."""
    ct = compile_template("prefix-[x]")
    typetag, value = osc_arg_for(ct, _ctx(pos=(1.5, 0.0, 0.0)))
    assert typetag == "s"
    assert value == "prefix-1.5"


# constant_arg_value – compile-time pre-typing of invariant literal args


@pytest.mark.parametrize(
    "text,expected",
    [("0", 0), ("-3", -3), ("0.0", 0.0), ("hello", "hello")],
)
def test_constant_arg_value_returns_pretyped_literal(text, expected) -> None:
    ct = compile_template(text)
    const = constant_arg_value(ct)
    assert const == expected
    assert type(const) is type(expected)
    assert const == osc_arg_for(ct, _ctx())[1]


def test_constant_arg_value_none_for_slot_template() -> None:
    assert constant_arg_value(compile_template("[x]")) is None
    assert constant_arg_value(compile_template("prefix-[x]")) is None


# ---------------------------------------------------------------------------
# RenderError shapes
# ---------------------------------------------------------------------------


def test_render_error_explicit_marker_has_no_hint() -> None:
    err = RenderError("x:70")
    assert err.hint == ""
    assert str(err) == "unresolved placeholder: [x:70]"


def test_render_error_with_hint_str_includes_hint() -> None:
    err = RenderError("x", hint="no default marker configured")
    assert err.placeholder == "x"
    assert err.hint == "no default marker configured"
    assert "no default marker configured" in str(err)
    assert "[x]" in str(err)


@pytest.mark.parametrize(
    "tpl,placeholder",
    [
        ("[x]", "x"),
        ("[y.frac]", "y.frac"),
        ("[markerid]", "markerid"),
        ("/eos/[markerid]/go", "markerid"),
    ],
)
def test_render_default_slot_raises_when_marker_id_is_none(
    tpl: str,
    placeholder: str,
) -> None:
    """A default-marker slot with no default marker raises, carrying the
    token + a ``"no default marker configured"`` hint. ``[y.frac]`` also
    needs ``max_height`` but the missing marker is checked first."""
    ct = compile_template(tpl)
    rc = _ctx(marker_id=None, grid_d=6.0)
    with pytest.raises(RenderError) as ei:
        render(ct, rc)
    assert ei.value.placeholder == placeholder
    assert ei.value.hint == "no default marker configured"


# ---------------------------------------------------------------------------
# Built-in templates – wire format
# ---------------------------------------------------------------------------


def test_builtin_etc_eos_wire_format() -> None:
    """Args order X, Z, Y, 0, 0, 0 – the Z-up→Y-up swap is done by
    placeholder choice, not code."""
    tpl = builtin_by_id("etc")
    assert tpl is not None
    addr_ct = compile_template(tpl.address)
    arg_cts = [compile_template(a) for a in tpl.args]
    rc = _ctx(pos=(1.0, 2.0, 3.0), marker_id=5)
    assert render(addr_ct, rc) == "/eos/set/patch/5/augment3d/position"
    typed = [osc_arg_for(ct, rc) for ct in arg_cts]
    assert typed[0] == ("f", pytest.approx(1.0))  # [x]
    assert typed[1] == ("f", pytest.approx(3.0))  # [z]
    assert typed[2] == ("f", pytest.approx(2.0))  # [y]
    assert typed[3] == ("i", 0)
    assert typed[4] == ("i", 0)
    assert typed[5] == ("i", 0)


def test_builtin_adm_osc_2d_uses_fractional_xy_zero_z() -> None:
    """Args ``[x.frac]``, ``[y.frac]``, ``0``. The id stays ``adm-osc``
    for compat with stored ``template_id``."""
    tpl = builtin_by_id("adm-osc")
    assert tpl is not None
    assert tpl.name == "ADM-OSC 2D"
    addr_ct = compile_template(tpl.address)
    arg_cts = [compile_template(a) for a in tpl.args]
    rc = _ctx(pos=(2.5, -1.5, 0.4), grid_w=10.0, grid_d=6.0, marker_id=2)
    assert render(addr_ct, rc) == "/adm/obj/2/xyz"
    typed = [osc_arg_for(ct, rc) for ct in arg_cts]
    assert typed[0] == ("f", pytest.approx(0.5))  # 2.5 / 5
    assert typed[1] == ("f", pytest.approx(-0.5))  # -1.5 / 3
    assert typed[2] == ("i", 0)  # literal zero, irrespective of pos.z


def test_builtin_adm_osc_3d_uses_fractional_xyz() -> None:
    tpl = builtin_by_id("adm-osc-3d")
    assert tpl is not None
    assert tpl.name == "ADM-OSC 3D"
    addr_ct = compile_template(tpl.address)
    arg_cts = [compile_template(a) for a in tpl.args]
    rc = _ctx(pos=(2.5, -1.5, 2.0), grid_w=10.0, grid_d=6.0, grid_h=4.0, marker_id=2)
    assert render(addr_ct, rc) == "/adm/obj/2/xyz"
    typed = [osc_arg_for(ct, rc) for ct in arg_cts]
    assert typed[0] == ("f", pytest.approx(0.5))  # x.frac: 2.5 / 5
    assert typed[1] == ("f", pytest.approx(-0.5))  # y.frac: -1.5 / 3
    assert typed[2] == ("f", pytest.approx(0.5))  # z.frac: 2.0 / 4


def test_builtin_adm_osc_3d_skips_when_max_height_unset() -> None:
    tpl = builtin_by_id("adm-osc-3d")
    assert tpl is not None
    fz_ct = compile_template(tpl.args[2])  # the [z.frac] arg
    rc = _ctx(pos=(1.0, 1.0, 1.0), grid_h=0.0)
    with pytest.raises(RenderError) as ei:
        osc_arg_for(fz_ct, rc)
    assert ei.value.placeholder == "z.frac"


def test_builtin_dnb_absolute_uses_xyz_meters() -> None:
    tpl = builtin_by_id("dnb-abs")
    assert tpl is not None
    addr_ct = compile_template(tpl.address)
    arg_cts = [compile_template(a) for a in tpl.args]
    rc = _ctx(pos=(1.0, 2.0, 3.0), marker_id=9)
    assert render(addr_ct, rc) == "/dbaudio1/coordinatemapping/source_position_xyz/1/9"
    typed = [osc_arg_for(ct, rc) for ct in arg_cts]
    assert typed[0] == ("f", pytest.approx(1.0))
    assert typed[1] == ("f", pytest.approx(2.0))
    assert typed[2] == ("f", pytest.approx(3.0))


def test_builtin_by_id_unknown_returns_none() -> None:
    assert builtin_by_id("does-not-exist") is None


def test_builtin_templates_have_unique_ids() -> None:
    ids = [t.id for t in BUILTIN_TEMPLATES]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("template_id", ["etc", "adm-osc", "adm-osc-3d", "dnb-abs"])
def test_builtin_template_carries_stream_30hz_trigger_pre_fill(template_id: str) -> None:
    tpl = builtin_by_id(template_id)
    assert tpl is not None
    assert tpl.trigger == {"kind": "stream", "rate_hz": 30}


@pytest.mark.parametrize("template_id", ["etc", "adm-osc", "dnb-abs"])
def test_builtin_template_trigger_is_immutable(template_id: str) -> None:
    """The trigger field is a read-only ``MappingProxyType`` – mutation
    raises ``TypeError``."""
    tpl = builtin_by_id(template_id)
    assert tpl is not None
    with pytest.raises(TypeError):
        tpl.trigger["rate_hz"] = 999  # type: ignore[index]
    with pytest.raises(TypeError):
        del tpl.trigger["kind"]  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# requires_default_marker – gates the default-marker lookup
# ---------------------------------------------------------------------------


def test_requires_default_marker_default_only_returns_true() -> None:
    assert requires_default_marker(compile_template("[x]")) is True
    assert requires_default_marker(compile_template("[markerid]")) is True
    assert requires_default_marker(compile_template("[markerfader]")) is True


def test_requires_default_marker_explicit_only_returns_false() -> None:
    assert requires_default_marker(compile_template("[x:3]")) is False
    assert requires_default_marker(compile_template("[markerid:3]")) is False
    assert requires_default_marker(compile_template("[markerfader:3]")) is False


def test_requires_default_marker_mixed_returns_true() -> None:
    assert requires_default_marker(compile_template("[x:3]/[y]")) is True


def test_requires_default_marker_literal_only_returns_false() -> None:
    assert requires_default_marker(compile_template("/cue/go")) is False
    assert requires_default_marker(compile_template("My Cue")) is False


def test_requires_default_marker_empty_template_returns_false() -> None:
    assert requires_default_marker(()) is False
    assert requires_default_marker(compile_template("")) is False


def test_requires_default_marker_unknown_brackets_are_literal() -> None:
    assert requires_default_marker(compile_template("[bogus]")) is False


# ---------------------------------------------------------------------------
# unresolved_placeholders – server-side dependency check for the editor
# ---------------------------------------------------------------------------


def _ct(s: str):  # noqa: ANN202 – internal helper
    return compile_template(s)


def test_unresolved_default_slot_with_no_default_marker() -> None:
    out = unresolved_placeholders(
        _ct("[x]"),
        default_marker_id=None,
        registered_marker_ids=frozenset({0, 1, 2}),
    )
    assert out == ("[x]",)


def test_unresolved_default_slot_with_unregistered_default_marker() -> None:
    out = unresolved_placeholders(
        _ct("[y.frac]"),
        default_marker_id=5,
        registered_marker_ids=frozenset({0, 1, 2}),
    )
    assert out == ("[y.frac]",)


def test_unresolved_default_slot_resolves_when_default_registered() -> None:
    out = unresolved_placeholders(
        _ct("[x] [y] [markerid]"),
        default_marker_id=0,
        registered_marker_ids=frozenset({0, 1, 2}),
    )
    assert out == ()


def test_unresolved_explicit_slot_when_target_not_registered() -> None:
    out = unresolved_placeholders(
        _ct("[x:7]"),
        default_marker_id=0,
        registered_marker_ids=frozenset({0, 1, 2}),
    )
    assert out == ("[x:7]",)


def test_unresolved_explicit_slot_resolves_when_target_registered() -> None:
    out = unresolved_placeholders(
        _ct("[x:2] [y:1]"),
        default_marker_id=None,
        registered_marker_ids=frozenset({1, 2}),
    )
    assert out == ()


def test_unresolved_mixed_default_and_explicit_returns_both() -> None:
    out = unresolved_placeholders(
        _ct("[x]/[y:7]"),
        default_marker_id=None,
        registered_marker_ids=frozenset({0, 1}),
    )
    assert out == ("[x]", "[y:7]")


def test_unresolved_collapses_duplicate_placeholders() -> None:
    out = unresolved_placeholders(
        _ct("[x]/[x]/[x]"),
        default_marker_id=None,
        registered_marker_ids=frozenset({0}),
    )
    assert out == ("[x]",)


def test_unresolved_collapses_duplicate_explicit_placeholders() -> None:
    out = unresolved_placeholders(
        _ct("[x:7] [y:7] [x:7]"),
        default_marker_id=None,
        registered_marker_ids=frozenset({0}),
    )
    assert out == ("[x:7]", "[y:7]")


def test_unresolved_literal_only_template_returns_empty() -> None:
    out = unresolved_placeholders(
        _ct("/cue/go"),
        default_marker_id=None,
        registered_marker_ids=frozenset(),
    )
    assert out == ()


def test_unresolved_empty_template_returns_empty() -> None:
    out = unresolved_placeholders((), default_marker_id=None, registered_marker_ids=frozenset())
    assert out == ()


def test_explicit_slot_zero_or_leading_zero_index_is_literal() -> None:
    """Index 0 (no marker / fader is 0) and any leading-zero index fall
    back to literal – ``[x:0]`` / ``[x:007]`` / ``[fader:01]`` are never
    placeholders, so they can't be flagged and round-trip verbatim."""
    for tpl in ("[x:0]", "[x:007]", "[fader:01]"):
        assert _is_literal(tpl), tpl
        assert (
            unresolved_placeholders(
                _ct(tpl),
                default_marker_id=None,
                registered_marker_ids=frozenset(),
            )
            == ()
        )


def test_unresolved_explicit_markerid_resolves_regardless_of_registry() -> None:
    """``[markerid:7]`` substitutes the literal id, so it resolves even
    when marker 7 isn't registered and is never flagged."""
    out = unresolved_placeholders(
        _ct("[markerid:7]"),
        default_marker_id=None,
        registered_marker_ids=frozenset(),
    )
    assert out == ()


def test_unresolved_bare_markerid_flagged_when_no_default() -> None:
    out = unresolved_placeholders(
        _ct("[markerid]"),
        default_marker_id=None,
        registered_marker_ids=frozenset(),
    )
    assert out == ("[markerid]",)


def test_unresolved_explicit_markerid_alongside_other_explicit_slot() -> None:
    out = unresolved_placeholders(
        _ct("[markerid:9]/[x:9]"),
        default_marker_id=None,
        registered_marker_ids=frozenset(),
    )
    assert out == ("[x:9]",)


def test_unresolved_ignores_fader_and_event_slots() -> None:
    """Fader / markerfader / event slots surface as runtime skips, not
    edit-time pills – they must not light up just because no default
    marker is set."""
    out = unresolved_placeholders(
        _ct("[fader]/[markerfader]/[velocity]/[fader.pct]"),
        default_marker_id=None,
        registered_marker_ids=frozenset(),
    )
    assert out == ()


# z.frac grid dependency


@pytest.mark.parametrize("tpl", ["[z.frac]", "[z.frac.inv]"])
def test_unresolved_z_frac_when_max_height_unset(tpl: str) -> None:
    out = unresolved_placeholders(
        _ct(tpl),
        default_marker_id=0,
        registered_marker_ids=frozenset({0, 1}),
        grid_max_height=0.0,
    )
    assert out == (tpl,)


@pytest.mark.parametrize("tpl", ["[z.frac]", "[z.frac.inv]"])
def test_unresolved_z_frac_resolves_when_max_height_set(tpl: str) -> None:
    out = unresolved_placeholders(
        _ct(tpl),
        default_marker_id=0,
        registered_marker_ids=frozenset({0}),
        grid_max_height=2.5,
    )
    assert out == ()


def test_unresolved_xy_frac_not_grid_gated() -> None:
    """``[x.frac]`` / ``[y.frac]`` use grid width/depth (always > 0), so
    they're never grid-gated – only marker-gated."""
    out = unresolved_placeholders(
        _ct("[x.frac] [y.frac]"),
        default_marker_id=0,
        registered_marker_ids=frozenset({0}),
        grid_max_height=0.0,
    )
    assert out == ()


def test_unresolved_explicit_z_frac_when_max_height_unset() -> None:
    out = unresolved_placeholders(
        _ct("[z:3.frac]"),
        default_marker_id=None,
        registered_marker_ids=frozenset({3}),
        grid_max_height=0.0,
    )
    assert out == ("[z:3.frac]",)


def test_unresolved_explicit_z_frac_resolves_when_max_height_set() -> None:
    out = unresolved_placeholders(
        _ct("[z:3.frac.inv]"),
        default_marker_id=None,
        registered_marker_ids=frozenset({3}),
        grid_max_height=4.0,
    )
    assert out == ()


def test_unresolved_explicit_z_frac_collapses_duplicates() -> None:
    out = unresolved_placeholders(
        _ct("[z:3.frac]/[z:3.frac]"),
        default_marker_id=None,
        registered_marker_ids=frozenset({3}),
        grid_max_height=0.0,
    )
    assert out == ("[z:3.frac]",)


def test_unresolved_explicit_z_frac_unregistered_marker_takes_precedence() -> None:
    """When both marker and grid are unset, the token is still reported
    once."""
    out = unresolved_placeholders(
        _ct("[z:9.frac]"),
        default_marker_id=None,
        registered_marker_ids=frozenset({0, 1}),
        grid_max_height=0.0,
    )
    assert out == ("[z:9.frac]",)
