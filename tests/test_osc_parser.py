# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for :mod:`openfollow.osc.parser`.

Covers quote-aware tokenisation, the inverse ``join_osc_message``
round-trip, and ``quote_arg`` corner cases.
"""

from __future__ import annotations

import pytest

from openfollow.osc.parser import (
    coerce_osc_args,
    join_osc_message,
    quote_arg,
    tokenize_osc_message,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# tokenize_osc_message
# ---------------------------------------------------------------------------


def test_tokenize_regression_pin_for_grandma3_quoted_string() -> None:
    """A quoted string argument must arrive as a single arg, not split
    on whitespace with literal quote characters glued onto the ends."""
    address, args = tokenize_osc_message('/cmd "Fadermaster Executor 202 At 100 Fade 1"')
    assert address == "/cmd"
    assert args == ["Fadermaster Executor 202 At 100 Fade 1"]


@pytest.mark.parametrize(
    "raw,expected_address,expected_args",
    [
        # No-quote path: classic ``str.split()`` tokenisation.
        ("/cue/go", "/cue/go", []),
        ("/cue/go 1.5", "/cue/go", ["1.5"]),
        ("/cue/go 1 2 3", "/cue/go", ["1", "2", "3"]),
        ('/cue/go "My Cue"', "/cue/go", ["My Cue"]),
        ('/cue/go "My Cue" 1.5', "/cue/go", ["My Cue", "1.5"]),
        # shlex accepts single quotes too.
        ("/cue/go 'My Cue'", "/cue/go", ["My Cue"]),
        (
            '/cue/go "First Cue" "Second Cue"',
            "/cue/go",
            ["First Cue", "Second Cue"],
        ),
        (
            '/cue/go "My Cue" 1.5 0',
            "/cue/go",
            ["My Cue", "1.5", "0"],
        ),
        # Escaped double quote inside a double-quoted string.
        (r'/say "she said \"hi\""', "/say", ['she said "hi"']),
        # ``\\`` produces a literal ``\``.
        (r'/path "C:\\Users\\test"', "/path", [r"C:\Users\test"]),
        # Whitespace runs collapse, as in ``str.split()``.
        ("/a   b\t\tc", "/a", ["b", "c"]),
        # shlex passes Unicode through.
        ('/say "héllo wörld"', "/say", ["héllo wörld"]),
        # Empty / whitespace-only input → no-op.
        ("", "", []),
        ("   ", "", []),
        ("\t\n  ", "", []),
        ("/foo", "/foo", []),
        # Empty quoted arg preserved as an empty string.
        ('/cue ""', "/cue", [""]),
    ],
)
def test_tokenize_osc_message_parses(
    raw: str,
    expected_address: str,
    expected_args: list[str],
) -> None:
    address, args = tokenize_osc_message(raw)
    assert address == expected_address
    assert args == expected_args


@pytest.mark.parametrize(
    "raw",
    [
        '/cue "unclosed',  # missing closing double quote
        "/cue 'unclosed",  # missing closing single quote
        '/cue "open "and close" left-open',  # subtler: middle close + new open
    ],
)
def test_tokenize_osc_message_raises_on_unclosed_quote(raw: str) -> None:
    """An unclosed quote raises ``ValueError``; callers surface it via
    the blur validator and preserve the row's prior values on save."""
    with pytest.raises(ValueError):
        tokenize_osc_message(raw)


# ---------------------------------------------------------------------------
# quote_arg – round-trip helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "arg,expected",
    [
        # No special chars → emit raw, so placeholders survive the round-trip.
        ("foo", "foo"),
        ("[x]", "[x]"),
        ("/cue/go", "/cue/go"),
        ("1.5", "1.5"),
        ("[x:marker2]", "[x:marker2]"),
        # Whitespace → wrap in double quotes.
        ("My Cue", '"My Cue"'),
        ("a\tb", '"a\tb"'),
        # Internal double quote → escape it.
        ('she said "hi"', r'"she said \"hi\""'),
        # Internal single quote forces quoting; it also tokenises, so the
        # round-trip would otherwise misfire.
        ("don't", '"don\'t"'),
        # Internal backslash → escape it.
        (r"C:\Users", r'"C:\\Users"'),
        # Empty string → ``""`` so re-tokenise yields an empty arg, not none.
        ("", '""'),
    ],
)
def test_quote_arg(arg: str, expected: str) -> None:
    assert quote_arg(arg) == expected


# ---------------------------------------------------------------------------
# join_osc_message – round-trip with tokenize_osc_message
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address,args,expected",
    [
        # Empty address + empty args → empty string (no-op row).
        ("", [], ""),
        ("/cue/go", [], "/cue/go"),
        ("/cue/go", ["1.5"], "/cue/go 1.5"),
        ("/cue/go", ["1", "2", "3"], "/cue/go 1 2 3"),
        ("/cue/go", ["My Cue"], '/cue/go "My Cue"'),
        ("/cue/go", ["My Cue", "1.5"], '/cue/go "My Cue" 1.5'),
        (
            "/cue/go",
            ["First Cue", "1.5", "Second Cue"],
            '/cue/go "First Cue" 1.5 "Second Cue"',
        ),
        # Empty arg preserved as ``""``.
        ("/cue", [""], '/cue ""'),
        # Empty address with non-empty args (never produced by the
        # tokeniser) renders without a leading space.
        ("", ["foo"], "foo"),
    ],
)
def test_join_osc_message(
    address: str,
    args: list[str],
    expected: str,
) -> None:
    assert join_osc_message(address, args) == expected


@pytest.mark.parametrize(
    "address,args",
    [
        ("/cue/go", []),
        ("/cue/go", ["1.5"]),
        ("/cue/go", ["My Cue"]),
        ("/cue/go", ["My Cue", "1.5", "Layer Two"]),
        ("/cue/go", ['she said "hi"']),
        ("/cue/go", [r"C:\Users\test"]),
        ("/cue/go", ["don't"]),
        ("/cue", [""]),
        ("", []),
    ],
)
def test_round_trip_join_then_tokenize_is_identity(
    address: str,
    args: list[str],
) -> None:
    """Contract: rendering ``(address, args)`` to a string and
    re-parsing must return the same ``(address, args)``."""
    text = join_osc_message(address, args)
    parsed_address, parsed_args = tokenize_osc_message(text)
    assert parsed_address == address
    assert parsed_args == args


# ---------------------------------------------------------------------------
# coerce_osc_args – numeric typing at the wire boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Integer-like → int, sent on the wire as OSC ``i`` not a string.
        (["1"], [1]),
        (["0"], [0]),
        (["-1"], [-1]),
        (["+42"], [42]),
        (["12345"], [12345]),
        # Float-like → float, OSC ``f``.
        (["1.5"], [1.5]),
        (["0.0"], [0.0]),
        (["-3.14"], [-3.14]),
        (["1e3"], [1000.0]),
        # Non-numeric → str, unchanged.
        (["hello"], ["hello"]),
        (["My Cue"], ["My Cue"]),
        (["go"], ["go"]),
        # Each arg is classified independently.
        (["Go Cue 1", "1.5"], ["Go Cue 1", 1.5]),
        (["1", "2.0", "three"], [1, 2.0, "three"]),
        # Empty args list passes through unchanged (address-only path).
        ([], []),
        # Empty string: ``float("")`` raises ``ValueError`` → kept as "".
        ([""], [""]),
    ],
)
def test_coerce_osc_args(raw: list[str], expected: list[object]) -> None:
    """Wire-boundary typing: int-like → int, float-like → float, else str.

    Mirrors :func:`openfollow.osc.template._typed_literal` so the wire
    type is identical for the same value across OSC sites.
    """
    result = coerce_osc_args(raw)
    assert result == expected
    # Assert type equality, not just value: ``1 == 1.0 == True``.
    for got, want in zip(result, expected, strict=False):
        assert type(got) is type(want), (
            f"coerce_osc_args({raw!r}): got {type(got).__name__}, want {type(want).__name__}"
        )


class TestClassifyOscLiteralRobustness:
    """classify_osc_literal must not raise on a pathological arg and
    must not emit a non-finite float."""

    def test_over_long_digit_run_is_string_not_crash(self) -> None:
        from openfollow.osc.parser import classify_osc_literal

        # int("9"*5000) raises ValueError (exceeds int-str-conversion limit);
        # _is_ascii_int still passes, so the int branch must catch + fall through.
        big = "9" * 5000
        assert classify_osc_literal(big) == ("s", big)

    def test_signed_over_long_digit_run_is_string(self) -> None:
        from openfollow.osc.parser import classify_osc_literal

        big = "-" + "8" * 6000
        assert classify_osc_literal(big) == ("s", big)

    def test_normal_int_still_typed_i(self) -> None:
        from openfollow.osc.parser import classify_osc_literal

        assert classify_osc_literal("42") == ("i", 42)
        assert classify_osc_literal("-7") == ("i", -7)

    @pytest.mark.parametrize("word", ["inf", "-inf", "infinity", "nan", "NaN", "1e400"])
    def test_non_finite_floats_fall_back_to_string(self, word: str) -> None:
        from openfollow.osc.parser import classify_osc_literal

        assert classify_osc_literal(word) == ("s", word)

    def test_finite_float_still_typed_f(self) -> None:
        from openfollow.osc.parser import classify_osc_literal

        assert classify_osc_literal("1.5") == ("f", 1.5)
        assert classify_osc_literal("-0.25") == ("f", -0.25)
