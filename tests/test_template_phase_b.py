# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for the fader / event sources and the numeric transforms.

Pure-function module; no I/O, no threads. Covers:

- ``[fader]`` (default-fader form) and the explicit ``[fader:N]``.
- The ``pct`` / ``int:min-max`` / ``scale:min-max`` transforms on
  ``fader`` / ``markerfader``: forward, inverted (``min>max``), signed
  bounds, banker's rounding, percent, float scale, malformed-range
  rejection, and the ``inv`` reflect-about-0.5 semantics.
- ``[value]`` / ``[velocity]`` / ``[note]`` MIDI-event sources –
  resolution under a populated context, :class:`RenderError` outside an
  event context.
- :func:`requires_default_fader`.
- ``osc_arg_for`` typetag inference: ``[fader]`` / ``[fader.pct]`` /
  ``[fader.scale:..]`` → ``f``; ``[value]`` / ``[fader.int:..]`` → ``i``.
"""

from __future__ import annotations

import pytest

from openfollow.osc.template import (
    RenderContext,
    RenderError,
    compile_template,
    osc_arg_for,
    render,
    requires_default_fader,
    requires_default_marker,
    unresolved_placeholders,
)

pytestmark = pytest.mark.unit


def _ctx(
    *,
    fader_values: dict[int, float] | None = None,
    default_fader: int | None = None,
    event_value: int | None = None,
    event_velocity: int | None = None,
    event_note: int | None = None,
    marker_id: int | None = None,
) -> RenderContext:
    """Build a :class:`RenderContext` populated only with the slots the
    fader / event sources need. ``marker_id`` defaults to ``None`` so a
    stray ``[x]`` would raise rather than silently substitute marker 0.
    """
    fvs = fader_values or {}

    def resolver(idx: int) -> float | None:
        return fvs.get(idx)

    return RenderContext(
        pos_m=(0.0, 0.0, 0.0),
        grid_w=10.0,
        grid_d=6.0,
        grid_h=0.0,
        z_offset=0.0,
        marker_id=marker_id,
        fader_resolver=resolver,
        default_fader=default_fader,
        event_value=event_value,
        event_velocity=event_velocity,
        event_note=event_note,
    )


def test_bare_fader_resolves_via_default_fader() -> None:
    ct = compile_template("[fader]")
    out = render(ct, _ctx(default_fader=3, fader_values={3: 0.42}))
    assert out == "0.42"


def test_bare_fader_without_default_raises_render_error() -> None:
    ct = compile_template("[fader]")
    with pytest.raises(RenderError) as exc:
        render(ct, _ctx(default_fader=None, fader_values={1: 0.5}))
    assert exc.value.placeholder == "fader"
    assert exc.value.hint == "no default fader configured"


def test_bare_fader_without_resolver_raises_render_error() -> None:
    """Context built without a resolver raises rather than substituting."""
    ct = compile_template("[fader]")
    rc = RenderContext(
        pos_m=(0.0, 0.0, 0.0),
        grid_w=10.0,
        grid_d=6.0,
        grid_h=0.0,
        z_offset=0.0,
        marker_id=None,
        default_fader=1,  # configured, but no resolver
    )
    with pytest.raises(RenderError) as exc:
        render(ct, rc)
    assert exc.value.placeholder == "fader"
    assert "fader resolver not wired" in exc.value.hint


def test_bare_fader_with_unknown_default_index_raises_render_error() -> None:
    ct = compile_template("[fader]")
    with pytest.raises(RenderError) as exc:
        render(ct, _ctx(default_fader=9, fader_values={1: 0.5}))
    assert exc.value.placeholder == "fader"
    assert exc.value.hint == "fader 9 is not registered"


def test_explicit_fader_ref_resolves_via_resolver() -> None:
    ct = compile_template("[fader:5]")
    out = render(ct, _ctx(fader_values={5: 0.1}, default_fader=None))
    assert out == "0.1"


def test_explicit_fader_ref_label_round_trips_in_error() -> None:
    ct = compile_template("[fader:7]")
    with pytest.raises(RenderError) as exc:
        render(ct, _ctx(fader_values={1: 0.5}))
    assert exc.value.placeholder == "fader:7"
    assert exc.value.hint == "fader 7 is not registered"


def test_explicit_fader_ref_does_not_consult_default() -> None:
    ct = compile_template("[fader:3]")
    out = render(ct, _ctx(fader_values={3: 0.5}, default_fader=None))
    assert out == "0.5"


def test_fader_inv_reflects_about_half() -> None:
    """``inv`` on a 0..1 fader is ``1 - v`` (centre 0.5), distinct from a
    position ``inv`` (negate, centre 0)."""
    ct = compile_template("[fader.inv]")
    assert render(ct, _ctx(default_fader=1, fader_values={1: 0.25})) == "0.75"
    assert render(ct, _ctx(default_fader=1, fader_values={1: 1.0})) == "0"


def test_fader_inv_then_pct_chains() -> None:
    ct = compile_template("[fader.inv.pct]")
    typetag, value = osc_arg_for(ct, _ctx(default_fader=1, fader_values={1: 0.25}))
    assert typetag == "f"
    assert value == pytest.approx(75.0)


def test_fader_pct_scales_to_percent() -> None:
    ct = compile_template("[fader.pct]")
    typetag, value = osc_arg_for(ct, _ctx(default_fader=1, fader_values={1: 0.5}))
    assert typetag == "f"
    assert value == pytest.approx(50.0)


def test_explicit_fader_pct() -> None:
    ct = compile_template("[fader:2.pct]")
    typetag, value = osc_arg_for(ct, _ctx(fader_values={2: 0.25}))
    assert typetag == "f"
    assert value == pytest.approx(25.0)


def test_fader_scale_float_output() -> None:
    """``scale`` maps 0..1 to a float range, preserving resolution (unlike
    ``int``)."""
    ct = compile_template("[fader.scale:-60-12]")
    typetag, value = osc_arg_for(ct, _ctx(default_fader=1, fader_values={1: 0.25}))
    assert typetag == "f"
    assert value == pytest.approx(-42.0)  # -60 + 0.25 * 72


def test_fader_scale_endpoints() -> None:
    ct = compile_template("[fader.scale:-60-12]")
    assert osc_arg_for(ct, _ctx(default_fader=1, fader_values={1: 0.0}))[1] == pytest.approx(-60.0)
    assert osc_arg_for(ct, _ctx(default_fader=1, fader_values={1: 1.0}))[1] == pytest.approx(12.0)


def test_int_forward_range() -> None:
    """``int`` maps 0..1 to the integer range; 0.5 → 64 (banker's rounding)."""
    ct = compile_template("[fader.int:0-127]")
    assert render(ct, _ctx(default_fader=1, fader_values={1: 0.0})) == "0"
    assert render(ct, _ctx(default_fader=1, fader_values={1: 1.0})) == "127"
    assert render(ct, _ctx(default_fader=1, fader_values={1: 0.5})) == "64"


def test_int_signed_bounds() -> None:
    ct = compile_template("[fader.int:-100-100]")
    assert render(ct, _ctx(default_fader=1, fader_values={1: 0.0})) == "-100"
    assert render(ct, _ctx(default_fader=1, fader_values={1: 1.0})) == "100"
    assert render(ct, _ctx(default_fader=1, fader_values={1: 0.5})) == "0"


def test_int_inverted_range() -> None:
    """``min>max`` sweeps high-to-low; inversion falls out of
    ``round(min + v*(max-min))``."""
    ct = compile_template("[fader.int:127-0]")
    assert render(ct, _ctx(default_fader=1, fader_values={1: 0.0})) == "127"
    assert render(ct, _ctx(default_fader=1, fader_values={1: 1.0})) == "0"


def test_int_explicit_fader_ref() -> None:
    """The fader index precedes the transform chain and overrides the
    row default."""
    ct = compile_template("[fader:2.int:0-127]")
    out = render(ct, _ctx(fader_values={2: 1.0}, default_fader=None))
    assert out == "127"


def test_int_banker_rounding_at_halfway() -> None:
    ct = compile_template("[fader.int:0-2]")
    assert render(ct, _ctx(default_fader=1, fader_values={1: 0.25})) == "0"  # 0.5 → 0
    assert render(ct, _ctx(default_fader=1, fader_values={1: 0.75})) == "2"  # 1.5 → 2


def test_int_clamps_out_of_domain_input() -> None:
    """int output is clamped to the declared band, so an out-of-[0,1]
    fader value can't emit beyond the range."""
    ct = compile_template("[fader.int:0-100]")
    assert render(ct, _ctx(default_fader=1, fader_values={1: 2.0})) == "100"
    assert render(ct, _ctx(default_fader=1, fader_values={1: -1.0})) == "0"
    # An inverted band clamps to the same [min, max] interval.
    inv = compile_template("[fader.int:100-0]")
    assert render(inv, _ctx(default_fader=1, fader_values={1: 2.0})) == "0"


def test_scale_clamps_out_of_domain_input() -> None:
    ct = compile_template("[fader.scale:0-100]")
    assert osc_arg_for(ct, _ctx(default_fader=1, fader_values={1: 1.5}))[1] == pytest.approx(100.0)
    assert osc_arg_for(ct, _ctx(default_fader=1, fader_values={1: -0.5}))[1] == pytest.approx(0.0)


def test_pct_then_range_chain_clamps() -> None:
    """``pct`` leaves the 0..1 domain (→ 100), so without clamping the
    range would emit 10000; clamped it stays 100."""
    ct = compile_template("[fader.pct.int:0-100]")
    assert render(ct, _ctx(default_fader=1, fader_values={1: 1.0})) == "100"


def test_scale_accepts_decimal_bounds() -> None:
    """Decimal range bounds parse (not dropped to literal)."""
    ct = compile_template("[fader.scale:-1-1]")
    assert osc_arg_for(ct, _ctx(default_fader=1, fader_values={1: 0.0}))[1] == pytest.approx(-1.0)
    assert osc_arg_for(ct, _ctx(default_fader=1, fader_values={1: 1.0}))[1] == pytest.approx(1.0)
    half = compile_template("[fader.scale:-0.5-0.5]")
    assert osc_arg_for(half, _ctx(default_fader=1, fader_values={1: 0.5}))[1] == pytest.approx(0.0)
    # Negative-to-negative decimal band (both bounds carry a sign).
    neg = compile_template("[fader.scale:-1--0.5]")
    assert osc_arg_for(neg, _ctx(default_fader=1, fader_values={1: 0.0}))[1] == pytest.approx(-1.0)
    assert osc_arg_for(neg, _ctx(default_fader=1, fader_values={1: 1.0}))[1] == pytest.approx(-0.5)


def test_decimal_bound_keeps_following_transform_separator() -> None:
    """The decimal point is part of a bound only when a digit follows it,
    so a decimal range still splits a trailing ``.transform`` separator.

    Tested at the parser level: the combined ``scale.pct`` chain is itself
    rejected as degenerate (pct reads the 0..1 domain scale already left –
    see ``test_degenerate_transform_chain_is_literal``), but the boundary
    between ``scale:0-2.5`` and ``.pct`` must still be recognised."""
    from openfollow.osc.template import _parse_transform

    parsed = _parse_transform("scale:0-2.5.pct")
    assert parsed is not None
    transform, rest = parsed
    assert transform.kind == "scale"
    assert (transform.lo, transform.hi) == (0, 2.5)
    assert rest == ".pct"


def test_integer_bounds_round_trip_without_decimal_point() -> None:
    """Integer bounds round-trip as ``0-127``, not ``0.0-127.0``; decimal
    support must not reformat them."""
    from openfollow.osc.template import _slot_token

    assert _slot_token(compile_template("[fader.int:0-127]")[0]) == "[fader.int:0-127]"
    assert _slot_token(compile_template("[fader.scale:-1-1]")[0]) == "[fader.scale:-1-1]"
    assert _slot_token(compile_template("[fader.scale:0-2.5]")[0]) == "[fader.scale:0-2.5]"


@pytest.mark.parametrize(
    "raw",
    [
        "[fader.int: 0-127]",  # leading whitespace
        "[fader.int:0 -127]",  # mid-token whitespace
        "[fader.int:0-127 ]",  # trailing whitespace
        "[fader.int:+0-127]",  # leading plus on min
        "[fader.int:0-+127]",  # leading plus on max
        "[fader.int:0-]",  # missing max
        "[fader.int:abc]",  # non-numeric
        "[fader.int:1-2-3]",  # three-part range
        "[fader.int:0-1.]",  # trailing decimal point, no digit
        "[fader:2.int:0-127 ]",  # trailing space inside the bracket
    ],
)
def test_int_rejects_malformed_brackets_as_literal(raw: str) -> None:
    """Anything the grammar doesn't accept renders as literal text."""
    ct = compile_template(raw)
    out = render(ct, _ctx(default_fader=1, fader_values={1: 0.5}))
    assert out == raw


@pytest.mark.parametrize(
    "raw",
    [
        # ``inv`` / ``pct`` read the 0..1 fader domain, so neither can
        # follow a transform that already left it (would emit a negative /
        # >100 value rather than the intended reordering).
        "[fader.pct.inv]",
        "[fader.scale:0-100.inv]",
        "[fader.int:0-100.inv]",
        "[fader.scale:0-2.5.pct]",
        "[markerfader.pct.inv]",
        # duplicate transform
        "[fader.pct.pct]",
        "[fader.inv.inv]",
        "[x.frac.frac]",
        # more than one range transform
        "[fader.int:0-100.scale:0-1]",
        "[fader.scale:0-1.int:0-100]",
    ],
)
def test_degenerate_transform_chain_is_literal(raw: str) -> None:
    """A repeated transform, two range transforms, or a domain-assuming
    transform folded after one that left the native domain falls back to
    literal (the "anything that doesn't make sense → literal" contract),
    so the operator sees the bad token verbatim rather than a silently
    out-of-range value on the wire."""
    ct = compile_template(raw)
    assert render(ct, _ctx(default_fader=1, fader_values={1: 0.5})) == raw


@pytest.mark.parametrize(
    "raw,fader_value,expected",
    [
        ("[fader.inv.pct]", 0.5, "50"),  # inv keeps 0..1, then pct
        ("[fader.pct.int:0-100]", 1.0, "100"),  # pct then a clamping range
        ("[fader.inv.int:0-127]", 0.0, "127"),  # inv → 1.0, int → 127
        ("[fader.inv.scale:0-100]", 0.25, "75"),  # inv → 0.75, scale → 75
    ],
)
def test_valid_ordered_chain_still_renders(raw: str, fader_value: float, expected: str) -> None:
    """The accepted ordering – ``inv`` (stays 0..1) → ``pct`` (→ 0..100) →
    one clamping range – keeps working; only the disordered forms above
    fall back to literal."""
    ct = compile_template(raw)
    assert render(ct, _ctx(default_fader=1, fader_values={1: fader_value})) == expected


def test_int_without_default_raises_render_error() -> None:
    ct = compile_template("[fader.int:0-127]")
    with pytest.raises(RenderError) as exc:
        render(ct, _ctx(default_fader=None, fader_values={1: 0.5}))
    assert exc.value.placeholder == "fader.int:0-127"
    assert exc.value.hint == "no default fader configured"


def test_int_label_round_trips_in_error() -> None:
    """Error label preserves the original placeholder spelling."""
    ct = compile_template("[fader:7.int:0-127]")
    with pytest.raises(RenderError) as exc:
        render(ct, _ctx(fader_values={1: 0.5}))
    assert exc.value.placeholder == "fader:7.int:0-127"
    assert exc.value.hint == "fader 7 is not registered"


def test_event_value_resolves_when_populated() -> None:
    assert render(compile_template("[value]"), _ctx(event_value=64)) == "64"


def test_event_velocity_resolves_when_populated() -> None:
    assert render(compile_template("[velocity]"), _ctx(event_velocity=100)) == "100"


def test_event_note_resolves_when_populated() -> None:
    assert render(compile_template("[note]"), _ctx(event_note=60)) == "60"


@pytest.mark.parametrize(
    "raw,expected_placeholder",
    [
        ("[value]", "value"),
        ("[velocity]", "velocity"),
        ("[note]", "note"),
    ],
)
def test_event_slots_outside_event_context_raise_render_error(
    raw: str,
    expected_placeholder: str,
) -> None:
    ct = compile_template(raw)
    with pytest.raises(RenderError) as exc:
        render(ct, _ctx())  # no event fields populated
    assert exc.value.placeholder == expected_placeholder
    assert "no" in exc.value.hint and "in current event" in exc.value.hint


def test_osc_arg_for_bare_fader_is_float() -> None:
    ct = compile_template("[fader]")
    typetag, value = osc_arg_for(ct, _ctx(default_fader=1, fader_values={1: 0.5}))
    assert typetag == "f"
    assert value == pytest.approx(0.5)


def test_osc_arg_for_explicit_fader_is_float() -> None:
    ct = compile_template("[fader:3]")
    typetag, value = osc_arg_for(ct, _ctx(fader_values={3: 0.25}))
    assert typetag == "f"
    assert value == pytest.approx(0.25)


@pytest.mark.parametrize(
    "raw,kwargs,expected",
    [
        ("[value]", {"event_value": 64}, 64),
        ("[velocity]", {"event_velocity": 100}, 100),
        ("[note]", {"event_note": 60}, 60),
    ],
)
def test_osc_arg_for_event_slots_are_int(raw: str, kwargs: dict, expected: int) -> None:
    ct = compile_template(raw)
    typetag, value = osc_arg_for(ct, _ctx(**kwargs))
    assert typetag == "i"
    assert value == expected


def test_osc_arg_for_int_transform_is_int() -> None:
    """A trailing ``int`` transform forces typetag ``i`` despite the
    float fader source."""
    ct = compile_template("[fader.int:0-127]")
    typetag, value = osc_arg_for(ct, _ctx(default_fader=1, fader_values={1: 0.5}))
    assert typetag == "i"
    assert value == 64


def test_osc_arg_for_pct_transform_is_float() -> None:
    ct = compile_template("[fader.pct]")
    typetag, _ = osc_arg_for(ct, _ctx(default_fader=1, fader_values={1: 0.5}))
    assert typetag == "f"


def test_requires_default_marker_ignores_fader_slots() -> None:
    assert requires_default_marker(compile_template("[fader]")) is False
    assert requires_default_marker(compile_template("[velocity]")) is False
    assert requires_default_marker(compile_template("[fader.int:0-127]")) is False


def test_requires_default_marker_still_matches_position_slots() -> None:
    assert requires_default_marker(compile_template("[x]")) is True
    assert requires_default_marker(compile_template("/p/[markerid]")) is True


def test_requires_default_fader_matches_bare_fader_family() -> None:
    """Bare ``[fader]`` and its transform forms all need a default fader."""
    assert requires_default_fader(compile_template("[fader]")) is True
    assert requires_default_fader(compile_template("[fader.pct]")) is True
    assert requires_default_fader(compile_template("[fader.int:0-127]")) is True
    assert requires_default_fader(compile_template("[fader.scale:-60-12]")) is True


def test_requires_default_fader_ignores_explicit_refs() -> None:
    assert requires_default_fader(compile_template("[fader:3]")) is False
    assert requires_default_fader(compile_template("[fader:2.int:0-127]")) is False


def test_requires_default_fader_ignores_other_sources() -> None:
    """Position, ``markerfader`` and MIDI-event slots are unrelated to the
    default *fader* and stay out of the predicate."""
    assert requires_default_fader(compile_template("[x]")) is False
    assert requires_default_fader(compile_template("[markerfader]")) is False
    assert requires_default_fader(compile_template("[velocity]")) is False
    assert requires_default_fader(compile_template("/cue/go")) is False


def test_unresolved_placeholders_ignores_fader_slots() -> None:
    parts = compile_template("[fader]/[velocity]/[fader.int:0-127]")
    out = unresolved_placeholders(parts, default_marker_id=None, registered_marker_ids=frozenset())
    assert out == ()


def test_unresolved_placeholders_still_flags_position_slots() -> None:
    parts = compile_template("[fader]/[x]/[velocity]")
    out = unresolved_placeholders(parts, default_marker_id=None, registered_marker_ids=frozenset())
    assert out == ("[x]",)


def test_render_multi_slot_mixed_fader_and_event() -> None:
    ct = compile_template("/note/[note]/vel/[velocity]/level/[fader]")
    out = render(
        ct,
        _ctx(default_fader=1, fader_values={1: 0.42}, event_note=60, event_velocity=100),
    )
    assert out == "/note/60/vel/100/level/0.42"
