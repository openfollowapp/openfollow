# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""OSC message tokeniser.

Parses a freeform string (``/cue/go "My Cue" 1.5``) into ``(address, args)``
and the inverse round-trip back to a string. Both directions are quote-aware:
an arg containing whitespace, quotes, or backslashes round-trips via
double-quote wrapping with backslash-escaped internals.

Pure module – no I/O, threads, or logging.
"""

from __future__ import annotations

import math
import shlex
from typing import Any

# Unicode space separators (category ``Zs``) beyond ASCII space that
# ``shlex``'s default whitespace set (" \t\r\n") omits. The editor's
# ``contenteditable`` inserts U+00A0 around placeholder pills and paste can
# carry any of these; added to the lexer so they act as token boundaries.
_UNICODE_WHITESPACE = "\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000"

# Characters that force :func:`quote_arg` to double-quote an arg so it
# round-trips through :func:`tokenize_osc_message`. Must cover EVERY code
# point the tokeniser treats as a separator – shlex's ASCII whitespace
# (incl. ``\r``) plus ``_UNICODE_WHITESPACE`` – else an arg containing one
# is emitted unquoted and re-split on the next parse. Quote/backslash too.
_QUOTE_TRIGGERS = frozenset(" \t\r\n\"'\\") | frozenset(_UNICODE_WHITESPACE)


def tokenize_osc_message(raw: str) -> tuple[str, list[str]]:
    """Parse a free-form OSC message string into ``(address, args)``.

    POSIX ``shlex.split`` semantics plus the Unicode separators in
    ``_UNICODE_WHITESPACE`` as token boundaries:

    - ``"double quoted"`` / ``'single quoted'`` strings are preserved as a
      single arg (inner whitespace kept).
    - Backslash escapes follow POSIX ``shlex``: they apply in unquoted text
      and inside double quotes (``\\"``, ``\\\\``), but NOT inside
      single-quoted strings (POSIX single quotes are fully literal).
    - Runs of whitespace between tokens collapse.
    - Empty / whitespace-only input → ``("", [])``.

    The first token is the OSC address; the rest are args as plain ``str``.
    Numeric typing happens at the wire boundary, not here (see
    :func:`classify_osc_literal`); storing args as strings keeps the
    on-disk shape stable across edits.

    Raises ``ValueError`` on an unclosed quote.
    """
    # ``shlex.split(raw, posix=True)`` plus the Unicode separators added to
    # the whitespace set. Quoting takes precedence, so a Unicode space inside
    # a quoted arg stays literal. Iterating raises ``ValueError`` on
    # unbalanced quotes, which we propagate. Empty input collapses to ``[]``.
    lexer = shlex.shlex(raw, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    lexer.whitespace += _UNICODE_WHITESPACE
    tokens = list(lexer)
    if not tokens:
        return ("", [])
    return (tokens[0], list(tokens[1:]))


def quote_arg(arg: str) -> str:
    """Round-trip a single arg back to a token that
    :func:`tokenize_osc_message` re-parses identically.

    - No special characters → emit as-is.
    - Contains a ``_QUOTE_TRIGGERS`` char (whitespace, quote, backslash) →
      wrap in double quotes with internal ``"`` and ``\\`` backslash-escaped.
    - Empty arg → ``""`` (distinct from no arg at all).

    Not :func:`shlex.quote`, which single-quotes on shell metacharacters;
    double-quote wrapping is easier to escape (POSIX single quotes are
    fully literal).
    """
    if not arg:
        return '""'
    needs_quote = any(c in _QUOTE_TRIGGERS for c in arg)
    if not needs_quote:
        return arg
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def coerce_osc_args(args: list[str]) -> list[Any]:
    """Type-coerce string args at the wire boundary.

    ``"42"`` → ``42`` (OSC ``i``), ``"1.5"`` → ``1.5`` (OSC ``f``),
    ``"hello"`` → ``"hello"`` (OSC ``s``). ``send_message`` infers the
    typetag from the Python type, so the receiver sees the numeric wire
    type rather than a stringified number. Delegates to
    :func:`classify_osc_literal`.
    """
    return [_coerce_one(a) for a in args]


def _is_ascii_int(s: str) -> bool:
    """True for an optionally-signed run of ASCII ``0``–``9`` (no decimal,
    no exponent). ASCII only – ``str.isdigit()`` also accepts Unicode
    digits, and superscript/circled ones crash ``int()``."""
    body = s[1:] if s[:1] in ("+", "-") else s
    return bool(body) and body.isascii() and body.isdigit()


def classify_osc_literal(text: str) -> tuple[str, Any]:
    """Classify a bare-literal OSC arg by wire type.

    ``"42"`` → ``("i", 42)``; ``"1.5"`` → ``("f", 1.5)``; otherwise
    ``("s", text)`` (unchanged). Int detection is ASCII-only so a Unicode
    digit isn't reinterpreted and can't crash ``int()`` (superscript/circled
    digits pass ``str.isdigit()`` but ``int()`` rejects them).

    An over-long all-digit run exceeds ``int()``'s string-conversion limit
    (``sys.get_int_max_str_digits``, default 4300) and a non-finite float
    (``inf`` / ``nan`` / ``1e400``) are both treated as the literal string
    ``s`` rather than raising or emitting a surprising non-finite ``f``."""
    stripped = text.strip()
    if _is_ascii_int(stripped):
        try:
            return ("i", int(stripped))
        except (ValueError, OverflowError):
            # Over-long digit run – not a valid OSC ``i``; fall through.
            pass
    try:
        value = float(stripped)
    except (ValueError, OverflowError):
        return ("s", text)
    if not math.isfinite(value):
        return ("s", text)
    return ("f", value)


def _coerce_one(text: str) -> Any:
    return classify_osc_literal(text)[1]


def join_osc_message(address: str, args: list[str]) -> str:
    """Inverse of :func:`tokenize_osc_message`.

    Round-trip invariant: ``tokenize_osc_message(join_osc_message(a, args))``
    equals ``(a, args)`` for every valid pair. Empty address and empty args
    produce the empty string.

    The address is quoted via :func:`quote_arg` when it contains whitespace
    or quotes, so the invariant holds even for a space inside the address
    (which would otherwise emit ``/a b`` and re-tokenise to ``/a`` + ``b``).
    """
    if not address and not args:
        return ""
    parts: list[str] = []
    if address:
        parts.append(quote_arg(address))
    parts.extend(quote_arg(a) for a in args)
    return " ".join(parts)
