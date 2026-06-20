# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Parity gate: the web UI's client-side OSC placeholder recogniser must
accept exactly the tokens the server's ``_slot_from_name`` accepts.

The OSC editor in ``base.tpl`` flags any ``[token]`` it doesn't recognise
as literal text. Its recogniser is a hand-maintained mirror of
``openfollow.osc.template._slot_from_name`` using five source-family
regexes (``OSC_POSITION_RE`` / ``OSC_MARKERID_RE`` / ``OSC_FADER_RE`` /
``OSC_MARKERFADER_RE`` / ``OSC_EVENT_RE``) over the grammar
``[source(:index)(.transform)*]``, where ``index`` is ``:N`` or, on the
marker-keyed sources, a 1-based ``:cN`` controller reference.

Extracts those regex literals from ``base.tpl`` and replays the client
accept logic in Python, asserting agreement with the server for a
representative token set so a grammar change updating only one side fails
here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from openfollow.osc.template import (
    _ALLOWED_TRANSFORMS,
    _INDEXED_SOURCES,
    _RANGE_TRANSFORMS,
    _SOURCES,
    _slot_from_name,
)

pytestmark = pytest.mark.unit


_BASE_TPL = Path(__file__).resolve().parents[1] / "openfollow" / "web" / "templates" / "base.tpl"


def _extract_js_regex(source: str, const_name: str) -> re.Pattern[str]:
    """Pull ``const <const_name> = /<pattern>/<flags>;`` out of the template
    and compile the pattern as a Python regex.

    The recogniser patterns use only constructs common to JS and Python, so
    the literal transfers verbatim. Compiled with :data:`re.ASCII` so Python's
    ``\\d`` matches the SAME code points the client's does: the client RegExp
    literals carry no ``u`` flag, so their ``\\d`` is ASCII ``[0-9]`` only.
    Without ``re.ASCII`` Python's Unicode-aware ``\\d`` would accept
    Arabic-Indic / full-width digits the browser rejects, hiding a
    client/server divergence."""
    m = re.search(rf"const {const_name} = /(.+?)/[a-z]*;", source)
    if m is None:  # pragma: no cover – guards against a rename in base.tpl
        msg = f"{const_name} not found in base.tpl – recogniser refactored?"
        raise AssertionError(msg)
    return re.compile(m.group(1), re.ASCII)


_SOURCE = _BASE_TPL.read_text(encoding="utf-8")
_POSITION_RE = _extract_js_regex(_SOURCE, "OSC_POSITION_RE")
_MARKERID_RE = _extract_js_regex(_SOURCE, "OSC_MARKERID_RE")
_FADER_RE = _extract_js_regex(_SOURCE, "OSC_FADER_RE")
_MARKERFADER_RE = _extract_js_regex(_SOURCE, "OSC_MARKERFADER_RE")
_EVENT_RE = _extract_js_regex(_SOURCE, "OSC_EVENT_RE")
_PILL_RE = _extract_js_regex(_SOURCE, "OSC_PILL_RE")


def _client_recognises(token: str) -> bool:
    """Replay the ``oscIsRecognised`` branch from ``base.tpl``.

    ``token`` includes the surrounding brackets, e.g. ``"[fader.pct]"``.
    """
    return bool(
        _POSITION_RE.match(token)
        or _MARKERID_RE.match(token)
        or _FADER_RE.match(token)
        or _MARKERFADER_RE.match(token)
        or _EVENT_RE.match(token),
    )


def _server_recognises(token: str) -> bool:
    """True when ``compile_template`` would emit a slot (not a literal)
    for ``token`` – i.e. ``_slot_from_name`` accepts the inner name."""
    return _slot_from_name(token[1:-1]) is not None


# Representative valid tokens both sides must accept.
_RECOGNISED = [
    "[x]",
    "[y]",
    "[z]",
    "[x.inv]",
    "[x.frac]",
    "[x.frac.inv]",
    "[x:3]",
    "[z:5.frac]",
    "[markerid]",
    "[markerid:9]",
    "[fader]",
    "[fader:8]",
    "[fader.pct]",
    "[fader.inv.pct]",
    "[fader.int:0-127]",
    "[fader:2.int:0-127]",
    "[fader.int:127-0]",
    "[fader.int:-60-12]",
    "[fader.scale:-60-12]",
    # decimal range bounds
    "[fader.scale:-1-1]",
    "[fader.scale:-0.5-0.5]",
    "[fader.scale:-1--0.5]",
    "[fader.int:0-10.5]",
    # ordered chains: inv (stays 0..1) → pct (→ 0..100) → one range (clamps)
    "[fader.inv.int:0-127]",
    "[fader.pct.int:0-100]",  # pct then a clamping range is valid
    "[markerfader]",
    "[markerfader:3]",
    "[markerfader.pct]",
    "[markerfader:3.int:0-100]",
    "[markerfader.scale:0-1.5]",
    # controller references – marker-keyed sources, 1-based
    "[markerid:c1]",
    "[markerfader:c2]",
    "[markerfader:c1.int:0-100]",
    "[x:c1]",
    "[y:c10]",
    "[z:c1.frac]",
    "[value]",
    "[velocity]",
    "[note]",
]

_NOT_RECOGNISED = [
    "[bogus]",
    "[notathing]",
    # removed legacy spellings
    "[fx]",
    "[ix]",
    "[ifz]",
    "[markerId]",
    "[float]",
    "[float:fader3]",
    "[int:0-127]",
    "[int:0-127:fader1]",
    "[x:marker3]",
    "[markerfader:marker3]",
    "[x.pct]",  # transform not allowed for a position source
    "[fader.frac]",  # frac is position-only
    "[markerid.inv]",  # markerid takes no transform
    "[value:3]",  # event sources take no index
    "[x:]",  # empty index
    "[fader.int:0-]",  # malformed range
    "[fader.int:abc]",
    "[fader.int:0-1.]",  # trailing decimal point, no digit
    "[fader.scale:0-1.5.7]",  # two decimal points in a bound
    "[z.frac:2]",  # index must precede transforms
    # zero / leading-zero index (1-based; ``[1-9]\d*`` both sides)
    "[x:0]",  # no marker/fader 0
    "[fader:0]",
    "[x:007]",  # leading zero would diverge from canonical :7
    "[fader:01]",
    # degenerate transform chains (→ literal both sides)
    "[fader.pct.inv]",  # inv reads 0..1, but pct already left it (→ 1.0-50)
    "[fader.scale:0-100.inv]",
    "[fader.int:0-100.inv]",
    "[fader.scale:0-2.5.pct]",  # pct reads 0..1 after scale left it
    "[fader.pct.pct]",  # duplicate transform
    "[fader.inv.inv]",
    "[fader.int:0-100.scale:0-1]",  # two range transforms
    "[x.frac.frac]",
    # controller references – invalid forms (→ literal both sides)
    "[fader:c1]",  # fader is fader-indexed, not marker-keyed
    "[markerid:c0]",  # 1-based, no controller 0
    "[markerfader:c0]",
    "[markerid:c01]",  # leading zero rejected (mirrors c[1-9]\d*)
    "[x:cx]",  # non-numeric controller index
    "[markerid:c]",  # empty controller index
    "[value:c1]",  # event sources take no index
    # Unicode-digit index / range bounds – ASCII-only on both sides.
    # Superscripts crash int() server-side if unguarded; Arabic-Indic int()
    # accepts but the ASCII-\d client rejects – the divergence re.ASCII models.
    "[x:²]",  # superscript-two index
    "[x:٥]",  # Arabic-Indic-five index
    "[markerfader:①]",  # circled-one index
    "[fader.int:0-٩]",  # Unicode digit in a range bound
]


@pytest.mark.parametrize("token", _RECOGNISED)
def test_client_recognises_every_valid_token(token: str) -> None:
    assert _client_recognises(token), f"{token} wrongly flagged as literal"


@pytest.mark.parametrize("token", _NOT_RECOGNISED)
def test_client_rejects_non_placeholders(token: str) -> None:
    assert not _client_recognises(token), f"{token} wrongly accepted"


@pytest.mark.parametrize("token", _RECOGNISED + _NOT_RECOGNISED)
def test_client_matches_server(token: str) -> None:
    assert _client_recognises(token) == _server_recognises(token), f"client/server disagree on {token}"


def test_percent_recipe_is_recognised_both_sides() -> None:
    """The percent transform forms agree client↔server."""
    for token in ("[fader.pct]", "[markerfader.pct]"):
        assert _client_recognises(token)
        assert _server_recognises(token)


def _tokens_derived_from_server_tables() -> list[str]:
    """Build a valid-token corpus from the server's grammar tables
    (``_SOURCES`` / ``_INDEXED_SOURCES`` / ``_ALLOWED_TRANSFORMS``).

    Anti-drift net the static ``_RECOGNISED`` list can't be: a source or
    transform added server-side but not to the base.tpl regex yields a token
    the server recognises and the client doesn't, failing
    :func:`test_generated_tokens_match_server`."""
    tokens: list[str] = []
    for src in sorted(_SOURCES):
        tokens.append(f"[{src}]")
        if src in _INDEXED_SOURCES:
            tokens.append(f"[{src}:1]")
        for tr in sorted(_ALLOWED_TRANSFORMS.get(src, frozenset())):
            tokens.append(f"[{src}.{tr}:0-1]" if tr in _RANGE_TRANSFORMS else f"[{src}.{tr}]")
    return tokens


@pytest.mark.parametrize("token", _tokens_derived_from_server_tables())
def test_generated_tokens_match_server(token: str) -> None:
    """Every base form the server tables can produce must classify the same
    on the client – catches a source/transform added to one side only."""
    assert _client_recognises(token) == _server_recognises(token), f"client/server disagree on {token}"


@pytest.mark.parametrize(
    "text,first_run",
    [
        ("[fo[x]", "[fo[x]"),  # stray inner '[' → whole run, NOT a green [x]
        ("[x]", "[x]"),
        ("[hello world]", "[hello world]"),
        ("prefix[x]", "[x]"),
    ],
)
def test_pill_extraction_is_positional_like_server(text: str, first_run: str) -> None:
    """The client's pill extractor (``OSC_PILL_RE``) must consume ``[`` to
    the FIRST ``]`` – the same run the server's ``compile_template`` scans –
    so a stray inner ``[`` can't make the editor paint a recognised ``[x]``
    pill the wire never sends. ``re.ASCII`` strips the JS ``/g`` flag, so
    ``search`` returns the first run."""
    m = _PILL_RE.search(text)
    assert m is not None and m.group(0) == first_run


def test_nested_bracket_run_is_literal_both_sides() -> None:
    """``[fo[x]`` is a single literal run on both sides – the server emits
    no slot for the inner name, and the client extracts the whole run (not
    the embedded ``[x]``)."""
    assert _PILL_RE.search("[fo[x]").group(0) == "[fo[x]"
    assert not _server_recognises("[fo[x]")  # _slot_from_name("fo[x") is None
