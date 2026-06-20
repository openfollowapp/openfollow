# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""OSC message template engine – placeholders, compile, render, builtins.

Operators author OSC addresses and arguments as a free-form string with
bracketed placeholders. The compiler walks the string once, producing a
list of ``_Literal`` and ``_Slot`` parts; the renderer substitutes
placeholder values from a ``RenderContext`` at each call site.

Placeholder grammar:

    [ source (":" index)? ("." transform)* ]

- ``source`` is one of :data:`_SOURCES` (``x`` / ``y`` / ``z`` /
  ``markerid`` / ``fader`` / ``markerfader`` / ``value`` / ``velocity``
  / ``note``).
- ``index`` is a positional ``:N`` – a marker id for ``x`` / ``y`` /
  ``z`` / ``markerid`` / ``markerfader``, a fader number for ``fader``.
  Omit it to resolve against the row's default marker / default fader.
  Event sources (``value`` / ``velocity`` / ``note``) take no index.
  A ``:cN`` index (1-based) is a *controller reference* – "the marker
  controller ``N`` is currently driving" – resolved live at render time.
  It's accepted only on the marker-keyed sources (``x`` / ``y`` / ``z``
  / ``markerid`` / ``markerfader``), never ``fader`` or the event
  sources; ``c0`` is invalid (→ literal).
- ``transform`` is a ``.``-separated chain applied left-to-right:
  ``inv`` (reflect about the domain centre – negate a position,
  ``1 − v`` a 0..1 fader), ``frac`` (positions only – normalise to
  ``[-1, 1]`` by grid extent), ``pct`` (``× 100`` → float), and the
  parametric ``int:min-max`` (integer output; ``min > max`` inverts)
  / ``scale:min-max`` (float output). Bounds are signed and may be
  decimal (``scale:-1-1`` / ``scale:-0.5-0.5``). Validity is per source:
  positions accept ``inv`` / ``frac``; ``fader`` / ``markerfader``
  accept ``inv`` / ``pct`` / ``int`` / ``scale``; ``markerid`` and the
  event sources accept none.

A ``.`` is a transform separator only when immediately followed by a
transform keyword; the decimal point is part of a range bound only when
a digit follows it, so ``scale:0-1.pct`` still chains a ``.pct``.
Anything that doesn't parse – unknown source, ``:N`` on a source that
doesn't take one, a transform not allowed for the source, a malformed
range – compiles to literal text, exactly like an unrecognised token.

Pure module – no I/O, no threads, no logging. Safe to call from the
scheduler thread without locks.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from openfollow.osc.parser import classify_osc_literal

# Placeholder sources. ``x`` / ``y`` / ``z`` are marker positions in
# metres; ``markerid`` is the resolved marker id; ``fader`` /
# ``markerfader`` are 0..1 driver values; ``value`` / ``velocity`` /
# ``note`` carry the live MIDI event payload.
_SOURCES: frozenset[str] = frozenset(
    {
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
)

# Longest-first match so ``markerfader`` is never shadowed by
# ``markerid``. The boundary check in :func:`_match_source` makes this
# safe regardless of order; sorting keeps the intent obvious.
_SOURCES_BY_LENGTH: tuple[str, ...] = tuple(sorted(_SOURCES, key=len, reverse=True))

# Sources that accept a positional ``:N`` reference. Event sources don't
# – ``[value:3]`` is a parse error (→ literal).
_INDEXED_SOURCES: frozenset[str] = frozenset(
    {"x", "y", "z", "markerid", "fader", "markerfader"},
)

# Position sources share the metre/grid domain (centre 0) and accept the
# ``frac`` transform; ``fader`` / ``markerfader`` share the 0..1 driver
# domain (centre 0.5) and accept the numeric range transforms. ``inv``
# reflects about each domain's centre – see :func:`_apply_transform`.
_POSITION_SOURCES: frozenset[str] = frozenset({"x", "y", "z"})

# Marker-keyed sources: a bare (index-less) one resolves against the
# row's default marker. ``fader`` is keyed by the default fader instead
# (see :func:`requires_default_fader`).
_DEFAULT_MARKER_SOURCES: frozenset[str] = _POSITION_SOURCES | frozenset({"markerid", "markerfader"})

# Sources whose OSC wire type is integer when emitted bare (no
# transform). A trailing ``int`` transform forces ``i`` regardless of
# source; every other transform forces ``f`` (see :func:`_wire_type`).
_INT_SOURCES: frozenset[str] = frozenset({"markerid", "value", "velocity", "note"})

# Event sources resolve from the live MIDI event; they raise
# :class:`RenderError` when fired outside an event context.
_EVENT_SOURCES: frozenset[str] = frozenset({"value", "velocity", "note"})

# Per-source transform whitelist. A placeholder whose chain contains a
# transform not in its source's set compiles to literal text (e.g.
# ``[x.pct]`` / ``[fader.frac]`` / ``[markerid.inv]``). Sources absent
# from this table (``markerid`` + event sources) accept no transform.
_ALLOWED_TRANSFORMS: dict[str, frozenset[str]] = {
    "x": frozenset({"inv", "frac"}),
    "y": frozenset({"inv", "frac"}),
    "z": frozenset({"inv", "frac"}),
    "fader": frozenset({"inv", "pct", "int", "scale"}),
    "markerfader": frozenset({"inv", "pct", "int", "scale"}),
}

# Transform keywords split by arity: the nullary ones take no argument;
# the range ones carry a ``min-max`` bound. The parser's dot-rule (a
# ``.`` is a separator only when followed by a keyword) keys off both.
_NULLARY_TRANSFORMS: tuple[str, ...] = ("inv", "frac", "pct")
_RANGE_TRANSFORMS: tuple[str, ...] = ("int", "scale")

# Transforms whose output leaves the source's native value domain (0..1
# for faders, metres for positions). A later transform that reads that
# native domain (see :func:`_requires_native_domain`) folds over a
# garbage value, so the parser rejects such a chain (→ literal). ``inv``
# reflects within the domain and isn't listed.
_DOMAIN_LEAVING_TRANSFORMS: frozenset[str] = frozenset({"pct", "int", "scale", "frac"})

# Set form of ``_RANGE_TRANSFORMS`` for the "at most one range transform
# per chain" parse check.
_RANGE_TRANSFORM_SET: frozenset[str] = frozenset(_RANGE_TRANSFORMS)

# Only position + ``markerid`` slots surface as red pills in the editor.
# Fader / ``markerfader`` slots surface as runtime skips instead, so
# they're excluded here.
_EDIT_TIME_SOURCES: frozenset[str] = _POSITION_SOURCES | frozenset({"markerid"})

# Recognised placeholder sources – public export the web layer uses to
# populate the chip palette + the per-row ``data-osc-placeholder-names``
# attribute. The transform / index grammar isn't enumerable as a flat
# set; the ``base.tpl`` client recogniser mirrors it with regexes, kept
# honest by ``tests/test_web_osc_placeholder_recognition.py``.
PLACEHOLDERS: frozenset[str] = _SOURCES

# ``int:min-max`` / ``scale:min-max`` range bounds – signed, optionally
# decimal. ``min > max`` inverts the mapping naturally. Matched as a
# prefix (no ``$``) so a trailing ``.transform`` separator isn't
# swallowed; the decimal group fires only when digits follow the dot.
# ASCII ``[0-9]`` (not ``\d``, which also matches Unicode decimal digits)
# keeps parity with the ASCII ``\d`` of the base.tpl client recogniser –
# see ``_is_ascii_digit``.
_RANGE_PREFIX_RE = re.compile(r"(-?[0-9]+(?:\.[0-9]+)?)-(-?[0-9]+(?:\.[0-9]+)?)")


class RenderError(ValueError):
    """Raised when a placeholder cannot be resolved at render time.

    ``placeholder`` carries the unresolvable token (e.g. ``"x:70"`` /
    ``"z.frac"``); ``hint`` carries the actionable reason (empty for an
    unregistered explicit marker). ``str()`` is ``"unresolved
    placeholder: [<name>] (<hint>)"`` with ``hint`` only when non-empty.
    The transmitter manager ring buffer records only ``placeholder``.
    """

    def __init__(self, placeholder: str, *, hint: str = "") -> None:
        if hint:
            super().__init__(
                f"unresolved placeholder: [{placeholder}] ({hint})",
            )
        else:
            super().__init__(f"unresolved placeholder: [{placeholder}]")
        self.placeholder = placeholder
        self.hint = hint


# ``marker_resolver(marker_id)`` returns the explicitly-referenced
# marker's position, or ``None`` if no such marker is registered.
MarkerResolver = Callable[[int], tuple[float, float, float] | None]


# ``fader_resolver(index)`` returns fader ``index``'s current 0..1 value,
# or ``None`` when no fader with that index is registered.
FaderResolver = Callable[[int], float | None]


# ``marker_fader_resolver(marker_id)`` returns that marker's current 0..1
# fader value, or ``None`` when the marker has no provisioned fader.
MarkerFaderResolver = Callable[[int], float | None]


# ``controller_marker_resolver(controller_idx)`` maps a 0-based controller
# index to the marker id that controller currently drives, or ``None`` when
# the controller isn't connected / drives no marker. Resolves the ``:cN``
# reference; ``None`` becomes a runtime skip (ring-buffer reason), never a
# default-marker dependency.
ControllerMarkerResolver = Callable[[int], int | None]


@dataclass(frozen=True)
class RenderContext:
    """Inputs the renderer needs to resolve placeholder values.

    ``pos_m`` is the default marker's position; explicit ``[x:N]`` slots
    go through ``marker_resolver`` and don't consult ``pos_m``.

    ``marker_id is None`` means no default marker is configured; a
    default-marker slot fired in that case raises :class:`RenderError`
    rather than silently substituting marker 0.

    - ``fader_resolver`` looks up a fader's current 0..1 value by index.
    - ``marker_fader_resolver`` looks up a marker's 0..1 fader value.
    - ``controller_marker_resolver`` maps a 0-based controller index to
      the marker id it currently drives (resolves ``:cN``); ``None`` ->
      runtime skip.
    - ``default_fader`` is the row's default fader index (1..8) or
      ``None``; a bare ``[fader]`` slot fired with ``None`` raises.
    - ``event_value`` / ``event_velocity`` / ``event_note`` carry the
      MIDI payload; ``None`` outside an event context.
    """

    pos_m: tuple[float, float, float]  # default marker's position
    grid_w: float  # GridConfig.width  (for x.frac)
    grid_d: float  # GridConfig.depth  (for y.frac)
    # ``z.frac`` is ``clamp((z - z_offset) / grid_h, -1, 1)``. ``grid_h
    # <= 0`` means ``max_height`` is unset; the renderer raises
    # :class:`RenderError` on ``z.frac`` rather than divide.
    grid_h: float  # GridConfig.max_height (for z.frac)
    z_offset: float  # GridConfig.z_offset   (for z.frac)
    marker_id: int | None  # default marker id (for [markerid])
    marker_resolver: MarkerResolver | None = None
    fader_resolver: FaderResolver | None = None
    marker_fader_resolver: MarkerFaderResolver | None = None
    controller_marker_resolver: ControllerMarkerResolver | None = None
    default_fader: int | None = None
    event_value: int | None = None
    event_velocity: int | None = None
    event_note: int | None = None


@dataclass(frozen=True)
class _Literal:
    text: str


@dataclass(frozen=True)
class _Transform:
    """One link in a placeholder's transform chain.

    ``kind`` is one of ``inv`` / ``frac`` / ``pct`` / ``int`` /
    ``scale``. ``lo`` / ``hi`` carry the signed bounds for ``int`` /
    ``scale`` (``int`` when the bound is integral, ``float`` when
    decimal) and are ``None`` for the nullary transforms.
    """

    kind: str
    lo: float | None = field(default=None)
    hi: float | None = field(default=None)


@dataclass(frozen=True)
class _Slot:
    """A parsed placeholder: a source, an optional positional reference,
    and a left-to-right transform chain.

    ``ref_index=None`` resolves against the row default (default marker
    for position / ``markerid`` / ``markerfader``, default fader for
    ``fader``); ``ref_index=N`` targets marker / fader ``N`` explicitly.
    ``controller_index`` (0-based; ``N − 1`` of a ``:cN`` reference)
    resolves the marker controller ``N`` currently drives at render time;
    at most one of ``ref_index`` / ``controller_index`` is set.
    ``transforms`` is empty for a bare source.
    """

    source: str
    ref_index: int | None = field(default=None)
    controller_index: int | None = field(default=None)
    transforms: tuple[_Transform, ...] = field(default=())


# A compiled template is the parsed sequence of parts.
CompiledTemplate = tuple[_Literal | _Slot, ...]


def compile_template(s: str) -> CompiledTemplate:
    """Parse a template string into a tuple of literals and slots.

    Tokens of the form ``[...]`` become slots when the inner text parses
    as a placeholder per :func:`_slot_from_name`. Anything else –
    unrecognised brackets, bare text, numeric literals – becomes a
    ``_Literal`` part and renders verbatim.
    """
    parts: list[_Literal | _Slot] = []
    i = 0
    n = len(s)
    pending = []  # buffered literal characters
    while i < n:
        ch = s[i]
        if ch == "[":
            close = s.find("]", i + 1)
            if close == -1:
                # No matching ``]`` – the rest of the string is literal.
                pending.append(s[i:])
                break
            name = s[i + 1 : close]
            slot = _slot_from_name(name)
            if slot is not None:
                if pending:
                    parts.append(_Literal("".join(pending)))
                    pending = []
                parts.append(slot)
                i = close + 1
                continue
            # Unrecognised – treat the bracketed run as literal text.
            pending.append(s[i : close + 1])
            i = close + 1
            continue
        pending.append(ch)
        i += 1
    if pending:
        parts.append(_Literal("".join(pending)))
    return tuple(parts)


def requires_default_marker(parts: CompiledTemplate) -> bool:
    """Return True iff ``parts`` contains any default-marker slot.

    A default-marker slot is a marker-keyed ``_Slot`` (source in
    :data:`_DEFAULT_MARKER_SOURCES`) with ``ref_index is None``. The
    transmitter uses this to decide whether to look up the default marker
    before rendering: a row of only literals + explicit refs (or fader /
    event slots) must dispatch even when no default is registered.

    ``[markerfader]`` IS included; ``[fader]`` is NOT (it's keyed by the
    default fader – see :func:`requires_default_fader`). A ``:cN``
    controller reference (``controller_index`` set) is also excluded – it
    resolves its own marker at render time, so a ``[markerid:c1]``-only
    row must dispatch with no default marker configured.
    """
    return any(
        isinstance(p, _Slot)
        and p.ref_index is None
        and p.controller_index is None
        and p.source in _DEFAULT_MARKER_SOURCES
        for p in parts
    )


def requires_default_fader(parts: CompiledTemplate) -> bool:
    """Return True iff ``parts`` contains any default-fader slot.

    A default-fader slot is a bare ``[fader]`` (``source == "fader"``
    with ``ref_index is None``), with or without a transform chain. A row
    using only ``[fader:3]`` explicit refs must dispatch even with
    ``default_fader`` unset.
    """
    return any(isinstance(p, _Slot) and p.ref_index is None and p.source == "fader" for p in parts)


def unresolved_placeholders(
    parts: CompiledTemplate,
    *,
    default_marker_id: int | None,
    registered_marker_ids: frozenset[int],
    grid_max_height: float = 0.0,
) -> tuple[str, ...]:
    """Return the bracketed placeholder tokens in ``parts`` that can't
    be resolved given the current row + registry + grid state.

    Each entry is the operator-facing token (``"[x]"`` / ``"[y:5]"`` /
    ``"[z.frac]"`` / ``"[markerid]"``), in stable order – the web UI pill
    renderer compares each pill's textContent against this list.

    Only position + ``markerid`` slots are surfaced
    (:data:`_EDIT_TIME_SOURCES`); fader / ``markerfader`` slots surface
    as runtime skips instead. A ``:cN`` controller reference is likewise
    a runtime skip (controller connection / selection is unknown at edit
    time), so it never surfaces here even on a position source.
    Resolution rules:

    - **Default slot** (``ref_index is None``): unresolved when
      ``default_marker_id is None`` OR not in ``registered_marker_ids``.
    - **Explicit slot** (``ref_index=N``): unresolved when ``N`` is not
      registered – except ``[markerid:N]``, which substitutes ``N``
      directly and so never misses.
    - **``z`` carrying ``frac``**: additionally unresolved when
      ``grid_max_height <= 0`` (no denominator – the renderer raises).

    Duplicates collapse.
    """
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        if not isinstance(p, _Slot):
            continue
        if p.source not in _EDIT_TIME_SOURCES:
            continue
        if p.controller_index is not None:
            # ``:cN`` resolves live – runtime skip, never an edit-time pill.
            continue
        is_z_frac = p.source == "z" and any(t.kind == "frac" for t in p.transforms)
        if p.ref_index is None:
            marker_unresolved = default_marker_id is None or default_marker_id not in registered_marker_ids
            grid_unresolved = is_z_frac and grid_max_height <= 0.0
            if marker_unresolved or grid_unresolved:
                token = _slot_token(p)
                if token not in seen:
                    out.append(token)
                    seen.add(token)
        else:
            # ``[markerid:N]`` substitutes the literal id ``N`` directly,
            # so it resolves regardless of whether marker ``N`` exists.
            if p.source == "markerid":
                continue
            if p.ref_index not in registered_marker_ids:
                token = _slot_token(p)
                if token not in seen:
                    out.append(token)
                    seen.add(token)
            elif is_z_frac and grid_max_height <= 0.0:
                # ``[z:N.frac]`` resolves the marker but still needs
                # ``max_height`` to render.
                token = _slot_token(p)
                if token not in seen:
                    out.append(token)
                    seen.add(token)
    return tuple(out)


def token_has_explicit_index(token: str) -> bool:
    """True when ``token`` – a bracketed form like ``"[x:7]"`` /
    ``"[x:c1]"`` vs the default ``"[x]"`` – names an explicit target
    (a marker index or a ``:cN`` controller reference) rather than the
    row's default marker.

    Parses via the grammar rather than sniffing for ``":"`` so a colon
    carried by a transform can't be mistaken for the index separator."""
    inner = token[1:-1] if len(token) >= 2 and token[0] == "[" and token[-1] == "]" else token
    slot = _slot_from_name(inner)
    return slot is not None and (slot.ref_index is not None or slot.controller_index is not None)


# ---------------------------------------------------------------------------
# Parser – [source(:index)(.transform)*]
# ---------------------------------------------------------------------------


def _slot_from_name(name: str) -> _Slot | None:
    """Map a bracketed inner name to a typed slot, or ``None`` if it
    isn't a recognised placeholder.

    Grammar: ``source (":" (index | "c" index))? ("." transform)*``.
    Returns ``None`` (→ literal) for an unknown source, a ``:N`` on a
    source that doesn't take one, a ``:cN`` on a source that isn't
    marker-keyed (or ``c0`` / a leading-zero index), a transform not
    allowed for the source, a malformed range, or any trailing garbage.
    """
    source = _match_source(name)
    if source is None:
        return None
    rest = name[len(source) :]
    ref_index: int | None = None
    controller_index: int | None = None
    if rest.startswith(":"):
        after = rest[1:]
        if after[:1] == _CONTROLLER_PREFIX:
            # ``:cN`` controller reference – marker-keyed sources only,
            # 1-based with no leading zero (mirrors the client ``c[1-9]\d*``;
            # ``c0`` is invalid → literal).
            if source not in _DEFAULT_MARKER_SOURCES:
                return None
            digits, rest = _take_while(after[1:], _is_ascii_digit)
            if not digits or digits[0] == "0":
                return None
            parsed_index = _parse_index(digits)
            if parsed_index is None:
                return None
            controller_index = parsed_index - 1
        else:
            # ASCII digits only – ``str.isdigit()`` also accepts Unicode
            # digits, which either crash ``int()`` (superscripts/circled) or
            # diverge from the ASCII ``\d`` of the base.tpl client recogniser.
            digits, rest = _take_while(after, _is_ascii_digit)
            # Reject an empty, zero, or leading-zero index (mirrors the
            # ``:cN`` guard above): marker / fader ids are 1-based – id 0
            # can't exist – and a leading zero would let the canonical
            # reconstruction (``:7``) diverge from the typed form
            # (``:007``), desyncing edit-time pill matching.
            if not digits or digits[0] == "0":
                return None
            ref_index = _parse_index(digits)
            if ref_index is None:
                return None
            if source not in _INDEXED_SOURCES:
                return None
    transforms: list[_Transform] = []
    allowed = _ALLOWED_TRANSFORMS.get(source, frozenset())
    family = "position" if source in _POSITION_SOURCES else "fader"
    seen_kinds: set[str] = set()
    left_domain = False
    while rest:
        if not rest.startswith("."):
            # Trailing characters that aren't a transform separator –
            # e.g. ``x:3foo`` / ``faderx``. Not a placeholder.
            return None
        parsed = _parse_transform(rest[1:])
        if parsed is None:
            return None
        transform, rest = parsed
        kind = transform.kind
        if kind not in allowed:
            return None
        # Degenerate chains fall back to literal (the file's "anything
        # that doesn't make sense → literal" contract): a repeated
        # transform, more than one range transform, or a domain-assuming
        # transform folded after one that already left the native domain
        # (e.g. ``[fader.pct.inv]`` → ``1.0 - 50`` rather than the
        # intended ``[fader.inv.pct]`` → 50).
        if kind in seen_kinds:
            return None
        if kind in _RANGE_TRANSFORM_SET and not seen_kinds.isdisjoint(_RANGE_TRANSFORM_SET):
            return None
        if left_domain and _requires_native_domain(kind, family):
            return None
        seen_kinds.add(kind)
        if kind in _DOMAIN_LEAVING_TRANSFORMS:
            left_domain = True
        transforms.append(transform)
    return _Slot(
        source,
        ref_index=ref_index,
        controller_index=controller_index,
        transforms=tuple(transforms),
    )


def _match_source(name: str) -> str | None:
    """Return the source keyword ``name`` starts with (boundary-checked
    so the next char is ``:`` / ``.`` / end), or ``None``."""
    for cand in _SOURCES_BY_LENGTH:
        if name == cand or name.startswith(cand + ":") or name.startswith(cand + "."):
            return cand
    return None


# Sigil that distinguishes a ``:cN`` controller reference from a ``:N``
# marker/fader index in the index slot.
_CONTROLLER_PREFIX = "c"


def _is_ascii_digit(ch: str) -> bool:
    """True for ASCII ``0``–``9`` only.

    Unlike ``str.isdigit()`` this excludes Unicode digits, so index/range
    parsing can't feed ``int()`` a value it rejects or accepts but the
    ASCII-``\\d`` client recogniser doesn't.
    """
    return "0" <= ch <= "9"


def _take_while(s: str, pred: Callable[[str], bool]) -> tuple[str, str]:
    """Split ``s`` into (leading run matching ``pred``, remainder)."""
    i = 0
    while i < len(s) and pred(s[i]):
        i += 1
    return s[:i], s[i:]


def _parse_transform(rest: str) -> tuple[_Transform, str] | None:
    """Parse one transform off the front of ``rest`` (the text after a
    ``.`` separator), returning ``(transform, remainder)`` or ``None``.

    A ``.`` upstream is only a separator when the text here starts with a
    transform keyword – an unrecognised keyword yields ``None`` and the
    whole placeholder falls back to literal.
    """
    for kw in _NULLARY_TRANSFORMS:
        if rest == kw or rest.startswith(kw + "."):
            return _Transform(kw), rest[len(kw) :]
    for kw in _RANGE_TRANSFORMS:
        prefix = kw + ":"
        if rest.startswith(prefix):
            # Match the bounds as a prefix so a following ``.transform``
            # separator survives. A decimal point is consumed only when
            # digits follow it: ``scale:0-1.pct`` splits into
            # ``scale:0-1`` + ``.pct``; ``scale:0-1.5`` keeps ``.5`` as
            # the bound. The remainder returns to the caller's dot-rule.
            m = _RANGE_PREFIX_RE.match(rest, len(prefix))
            if m is None:
                return None
            lo = _parse_bound(m.group(1))
            hi = _parse_bound(m.group(2))
            if lo is None or hi is None:
                return None
            return _Transform(kw, lo, hi), rest[m.end() :]
    return None


def _parse_index(digits: str) -> int | None:
    """``int(digits)`` or ``None`` for an over-long run.

    Keeps :func:`compile_template` total: a digit run past ``int()``'s
    string-conversion limit falls back to literal instead of raising.
    """
    try:
        return int(digits)
    except (ValueError, OverflowError):
        return None


def _parse_bound(s: str) -> float | None:
    """Parse one range bound, or ``None`` for an over-long / non-finite value.

    Integer bounds stay ``int`` (round-trip as ``0-127``, not ``0.0-127.0``);
    decimal ones become ``float``.
    """
    try:
        value = float(s) if "." in s else int(s)
    except (ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _requires_native_domain(kind: str, family: str) -> bool:
    """True when transform ``kind`` reads the source's native value
    domain and so emits garbage if an earlier transform already left it.

    ``pct`` (``v * 100``) and ``frac`` (metre → fraction) always read the
    native domain. Fader ``inv`` (``1.0 - v``) reads the 0..1 domain;
    position ``inv`` (``-v``) is domain-agnostic. ``int`` / ``scale``
    clamp their input and tolerate any domain, so they're never here.
    """
    if kind in ("pct", "frac"):
        return True
    if kind == "inv":
        return family == "fader"
    return False


def _slot_inner(slot: _Slot) -> str:
    """Reconstruct the operator-facing inner token for a slot (no
    brackets) – ``x:3`` / ``z.frac`` / ``fader.scale:-60-12``. Used for
    :class:`RenderError` labels.
    """
    parts = [slot.source]
    if slot.ref_index is not None:
        parts.append(f":{slot.ref_index}")
    elif slot.controller_index is not None:
        parts.append(f":{_CONTROLLER_PREFIX}{slot.controller_index + 1}")
    for t in slot.transforms:
        if t.kind in _RANGE_TRANSFORMS:
            parts.append(f".{t.kind}:{t.lo}-{t.hi}")
        else:
            parts.append(f".{t.kind}")
    return "".join(parts)


def _slot_token(slot: _Slot) -> str:
    """Bracketed form of :func:`_slot_inner` – ``[x:3]`` / ``[z.frac]``."""
    return f"[{_slot_inner(slot)}]"


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


def _slot_value(slot: _Slot, ctx: RenderContext) -> Any:
    """Resolve a single slot to its runtime value: resolve the source's
    base value, then fold the transform chain left-to-right.

    Raises :class:`RenderError` for any unmet dependency (missing default
    marker / fader, unregistered explicit ref, unset ``max_height`` for
    ``z.frac``, event field outside an event context).
    """
    source = slot.source
    if source == "markerid":
        return _resolve_markerid(slot, ctx)
    if source in _EVENT_SOURCES:
        return _resolve_event_source(slot, ctx)
    # Sources that carry a transform chain (positions + faders).
    if source in _POSITION_SOURCES:
        value: float = _resolve_position_source(slot, ctx)
        family = "position"
    elif source == "fader":
        value = _resolve_fader_source(slot, ctx)
        family = "fader"
    else:  # markerfader
        value = _resolve_marker_fader_source(slot, ctx)
        family = "fader"
    for transform in slot.transforms:
        value = _apply_transform(transform, value, slot, family, ctx)
    return value


def _resolve_markerid(slot: _Slot, ctx: RenderContext) -> int:
    """Resolve ``[markerid]`` / ``[markerid:N]`` / ``[markerid:cN]`` to a
    marker id.

    ``[markerid:N]`` substitutes the literal ``N`` (no lookup);
    ``[markerid:cN]`` is a live lookup of the marker controller ``N``
    drives; bare ``[markerid]`` returns the row's default marker id,
    raising when none is configured.
    """
    if slot.controller_index is not None:
        return _resolve_controller_marker(slot, ctx)
    if slot.ref_index is not None:
        return slot.ref_index
    if ctx.marker_id is None:
        raise RenderError("markerid", hint="no default marker configured")
    return ctx.marker_id


def _resolve_event_source(slot: _Slot, ctx: RenderContext) -> int:
    """Resolve ``[value]`` / ``[velocity]`` / ``[note]`` from the live
    event payload. Raises when fired outside an event context."""
    source = slot.source
    if source == "value":
        field_value = ctx.event_value
    elif source == "velocity":
        field_value = ctx.event_velocity
    else:  # note
        field_value = ctx.event_note
    return _resolve_event_field(field_value, source)


def _resolve_position_source(slot: _Slot, ctx: RenderContext) -> float:
    """Resolve an ``x`` / ``y`` / ``z`` slot to its raw metre value.

    Default slots read ``ctx.pos_m`` (raising when no default marker is
    configured); explicit ``[x:N]`` slots go through
    :func:`_resolve_explicit_marker`; ``[x:cN]`` resolves the controller's
    marker, then looks up its position by id.
    """
    if slot.controller_index is not None:
        marker_id = _resolve_controller_marker(slot, ctx)
        x, y, z = _resolve_marker_position(marker_id, _slot_inner(slot), ctx)
    elif slot.ref_index is not None:
        x, y, z = _resolve_explicit_marker(slot, ctx)
    else:
        if ctx.marker_id is None:
            raise RenderError(_slot_inner(slot), hint="no default marker configured")
        x, y, z = ctx.pos_m
    source = slot.source
    if source == "x":
        return x
    if source == "y":
        return y
    return z


def _resolve_fader_source(slot: _Slot, ctx: RenderContext) -> float:
    """Resolve ``[fader]`` / ``[fader:N]`` to the target fader's current
    0..1 value.

    Bare ``[fader]`` fired with ``ctx.default_fader is None`` raises
    (hint ``"no default fader configured"``); resolver-missing /
    index-unknown failures come from :func:`_resolve_fader_value`.
    """
    if slot.ref_index is not None:
        target = slot.ref_index
    else:
        if ctx.default_fader is None:
            raise RenderError(_slot_inner(slot), hint="no default fader configured")
        target = ctx.default_fader
    return _resolve_fader_value(target, ctx, slot=slot)


def _resolve_marker_fader_source(slot: _Slot, ctx: RenderContext) -> float:
    """Resolve ``[markerfader]`` / ``[markerfader:N]`` /
    ``[markerfader:cN]`` to a marker's current 0..1 fader value.

    Bare ``[markerfader]`` is marker-keyed (uses ``ctx.marker_id``); the
    explicit form targets marker ``N`` directly; ``[markerfader:cN]``
    targets the marker controller ``N`` drives. Raises on no default
    marker, an unwired resolver, or a marker with no provisioned fader.
    """
    if slot.controller_index is not None:
        target = _resolve_controller_marker(slot, ctx)
    elif slot.ref_index is not None:
        target = slot.ref_index
    else:
        if ctx.marker_id is None:
            raise RenderError(_slot_inner(slot), hint="no default marker configured")
        target = ctx.marker_id
    if ctx.marker_fader_resolver is None:
        raise RenderError(_slot_inner(slot), hint="marker fader resolver not wired")
    value = ctx.marker_fader_resolver(target)
    if value is None:
        raise RenderError(_slot_inner(slot), hint=f"marker {target} has no fader")
    return value


def _apply_transform(
    transform: _Transform,
    value: float,
    slot: _Slot,
    family: str,
    ctx: RenderContext,
) -> float:
    """Fold one transform over the running ``value``.

    ``family`` is ``"position"`` (centre 0) or ``"fader"`` (centre 0.5)
    and only matters for ``inv``. The parser guarantees ``frac`` only
    reaches here for position sources.
    """
    kind = transform.kind
    if kind == "inv":
        return -value if family == "position" else 1.0 - value
    if kind == "frac":
        return _apply_frac(value, slot, ctx)
    if kind == "pct":
        return value * 100.0
    # int / scale share the affine map ``lo + v*(hi-lo)``; int rounds,
    # scale stays float. Bounds populated together (parser invariant);
    # the asserts are for the type checker.
    assert transform.lo is not None  # pragma: no cover
    assert transform.hi is not None  # pragma: no cover
    lo, hi = transform.lo, transform.hi
    # Clamp to the declared band (sorted, since ``min > max`` inverts).
    # Without this an out-of-[0,1] input – or a chain that left the 0..1
    # domain, e.g. ``[fader.pct.int:0-100]`` where ``pct`` already
    # produced 100 – would emit far outside the stated range.
    raw = lo + value * (hi - lo)
    clamped = min(max(raw, min(lo, hi)), max(lo, hi))
    if kind == "int":
        # ``round`` is half-to-even (banker's): an exact .5 lands on the
        # nearest even integer (0.5 → 0, 1.5 → 2, 2.5 → 2). Immaterial for
        # continuous fader inputs, where an exact half is vanishing.
        return round(clamped)
    # scale – float output.  # pragma: no branch
    return float(clamped)


def _apply_frac(value: float, slot: _Slot, ctx: RenderContext) -> float:
    """Apply ``frac`` to a position value: normalise to ``[-1, 1]`` by
    the relevant grid extent. ``z`` uses ``max_height`` and raises when
    it's unset."""
    source = slot.source
    if source == "x":
        return _clamped_fraction(value, ctx.grid_w)
    if source == "y":
        return _clamped_fraction(value, ctx.grid_d)
    # z  # pragma: no branch
    return _clamped_z_fraction(value, ctx.z_offset, ctx.grid_h, _slot_inner(slot))


def _resolve_explicit_marker(
    slot: _Slot,
    ctx: RenderContext,
) -> tuple[float, float, float]:
    """Look up an explicit ``[x:N]`` slot's position via the context's
    resolver. Raises :class:`RenderError` if the resolver is absent
    (manager forgot to wire it) or the marker isn't registered.
    """
    ref = slot.ref_index
    # Only reached when ``ref_index is not None``; assert for the type checker.
    assert ref is not None  # pragma: no cover
    return _resolve_marker_position(ref, _slot_inner(slot), ctx)


def _resolve_marker_position(
    marker_id: int,
    label: str,
    ctx: RenderContext,
) -> tuple[float, float, float]:
    """Look up ``marker_id``'s position via ``ctx.marker_resolver``.

    Shared by the explicit ``[x:N]`` and controller ``[x:cN]`` paths.
    Raises :class:`RenderError` (carrying ``label``) when the resolver is
    absent or the marker isn't registered.
    """
    if ctx.marker_resolver is None:
        raise RenderError(label)
    pos = ctx.marker_resolver(marker_id)
    if pos is None:
        raise RenderError(label)
    return pos


def _resolve_controller_marker(slot: _Slot, ctx: RenderContext) -> int:
    """Resolve a ``:cN`` slot's controller index to the marker id it
    currently drives.

    Raises :class:`RenderError` when the resolver isn't wired or the
    controller drives no marker (not connected / nothing selected) – a
    runtime skip the ring buffer records as ``controller N controls no
    marker``.
    """
    # Only reached when ``controller_index is not None``; assert for the
    # type checker.
    assert slot.controller_index is not None  # pragma: no cover
    label = _slot_inner(slot)
    if ctx.controller_marker_resolver is None:
        raise RenderError(label, hint="controller marker resolver not wired")
    marker_id = ctx.controller_marker_resolver(slot.controller_index)
    if marker_id is None:
        raise RenderError(
            label,
            hint=f"controller {slot.controller_index + 1} controls no marker",
        )
    return marker_id


def _clamped_fraction(value: float, full_extent: float) -> float:
    """Convert an absolute coord to a fraction of half-extent, clamped
    to ``[-1, 1]``.

    A non-positive ``full_extent`` (operator typo, hand-edited config)
    collapses to 0 – preferable to ``ZeroDivisionError`` mid-show.
    """
    if full_extent <= 0:
        return 0.0
    half = full_extent / 2.0
    fraction = value / half
    if fraction < -1.0:
        return -1.0
    if fraction > 1.0:
        return 1.0
    return fraction


def _clamped_z_fraction(
    z_value: float,
    z_offset: float,
    max_height: float,
    slot_label: str,
) -> float:
    """Convert a Z coord to a fraction of ``max_height``, clamped to
    ``[-1, 1]``.

    Differs from :func:`_clamped_fraction` in two ways:

    1. **Full denominator (not half-extent).** Z is asymmetric:
       ``z_offset`` is the origin, ``max_height`` the upward extent.
       ``Z = z_offset`` → ``0``; ``Z = z_offset + max_height`` → ``1``;
       ``Z = z_offset - max_height`` → ``-1``. Negatives are valid.
    2. **Strict on ``max_height <= 0``.** ``x.frac`` / ``y.frac`` collapse
       to ``0`` because grid width / depth are always positive;
       ``max_height`` defaults to ``0`` and is unset until configured, so
       a ``z.frac`` template against it is a misconfiguration. Raise
       :class:`RenderError`.
    """
    if max_height <= 0:
        raise RenderError(
            slot_label,
            hint="Grid → Maximum Height is not set",
        )
    fraction = (z_value - z_offset) / max_height
    if fraction < -1.0:
        return -1.0
    if fraction > 1.0:
        return 1.0
    return fraction


def _resolve_fader_value(
    target: int,
    ctx: RenderContext,
    *,
    slot: _Slot,
) -> float:
    """Look up ``target``'s current value via ``ctx.fader_resolver``.

    The error label is built lazily (``_slot_inner`` only on raise paths)
    so the per-tick streaming success path does no string work.
    """
    if ctx.fader_resolver is None:
        raise RenderError(_slot_inner(slot), hint="fader resolver not wired")
    value = ctx.fader_resolver(target)
    if value is None:
        raise RenderError(_slot_inner(slot), hint=f"fader {target} is not registered")
    return value


def _resolve_event_field(
    value: int | None,
    field_name: str,
) -> int:
    """Return ``value`` if populated, else raise :class:`RenderError`.

    ``[value]`` / ``[velocity]`` / ``[note]`` only make sense inside a
    MIDI-event render context; a fader-driven row referencing one is a
    misconfiguration, surfaced as a skip rather than a runtime error.
    """
    if value is None:
        raise RenderError(
            field_name,
            hint=f"no {field_name} in current event",
        )
    return value


def render(ct: CompiledTemplate, rc: RenderContext) -> str:
    """Render a compiled template to its string form.

    Used for OSC addresses (always strings) and as the fallback for any
    multi-part argument template. Single-slot args go through
    :func:`osc_arg_for` instead so they keep a typed OSC value (``f`` /
    ``i``) rather than coercing to ``s``.

    Raises :class:`RenderError` when any slot's reference cannot be
    resolved.
    """
    out: list[str] = []
    for part in ct:
        if isinstance(part, _Literal):
            out.append(part.text)
        else:
            out.append(_format_value(_slot_value(part, rc)))
    return "".join(out)


def _format_value(value: Any) -> str:
    """Stringify a slot value for string-rendered OSC templates.

    Integers render as decimal strings. Other numerics use ``format(...,
    "g")``, which trims trailing zeros (``1.0`` → ``"1"``) and may emit
    exponent notation at extreme magnitudes. Reached by multi-part
    templates; single-slot args go through :func:`osc_arg_for` to keep
    their OSC typetag.
    """
    if isinstance(value, bool):  # pragma: no cover - bools never reach here
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    return format(float(value), "g")


def _wire_type(slot: _Slot) -> str:
    """OSC wire typetag for a single-slot arg.

    A trailing ``int`` transform forces ``i``; any other transform forces
    ``f``. A bare source takes its natural type: sources in
    :data:`_INT_SOURCES` → ``i``, all others → ``f``.
    """
    if slot.transforms:
        return "i" if slot.transforms[-1].kind == "int" else "f"
    if slot.source in _INT_SOURCES:
        return "i"
    return "f"


def osc_arg_for(ct: CompiledTemplate, rc: RenderContext) -> tuple[str, Any]:
    """Render a single template argument to an OSC ``(typetag, value)`` pair.

    Auto-infer rules:

    - One slot, no literal text → typed by :func:`_wire_type`.
    - One literal that parses as int / float → typed numeric.
    - Anything else (mixed slots + literal text, or non-numeric literal)
      → ``s`` (string).

    Raises :class:`RenderError` when any slot's reference cannot be
    resolved.
    """
    # Single-part templates take their type from the part directly.
    if len(ct) == 1:
        part = ct[0]
        if isinstance(part, _Slot):
            value = _slot_value(part, rc)
            if _wire_type(part) == "i":
                return ("i", int(value))
            return ("f", float(value))
        return _typed_literal(part.text)
    # Multi-part templates fall back to string rendering – there's no
    # well-defined numeric type for, say, ``"prefix-[x]"``.
    return ("s", render(ct, rc))


def constant_arg_value(ct: CompiledTemplate) -> Any | None:
    """Final OSC value for a template that renders to a compile-time constant.

    A single bare ``_Literal`` part produces the same value on every
    fire, so it's coerced once and reused. Returns ``None`` when the arg
    has a slot (or is multi-part) and must render per-fire;
    ``_typed_literal`` never yields ``None``, so the sentinel is
    unambiguous. The value matches what :func:`osc_arg_for` produces.
    """
    if len(ct) == 1 and isinstance(ct[0], _Literal):
        return _typed_literal(ct[0].text)[1]
    return None


def _typed_literal(text: str) -> tuple[str, Any]:
    """Parse a bare-literal arg as int → ``i``, float → ``f``, else ``s``.

    Thin alias over :func:`openfollow.osc.parser.classify_osc_literal`,
    the shared classifier so the send paths can't drift.
    """
    return classify_osc_literal(text)


# ---------------------------------------------------------------------------
# Built-in templates
# ---------------------------------------------------------------------------


# Default trigger sub-table applied when a template doesn't declare its
# own. Wire format matches ``OscTransmitterConfig.__post_init__``, so the
# dispatch site only copies the mapping onto the row before it types it.
# Wrapped in ``MappingProxyType`` because ``frozen=True`` prevents
# reassigning the field but not mutating the dict it points at – an
# in-place edit would corrupt every future application of the template.
_DEFAULT_STREAM_30HZ_TRIGGER: Mapping[str, Any] = MappingProxyType(
    {"kind": "stream", "rate_hz": 30},
)


@dataclass(frozen=True)
class BuiltinTemplate:
    """Template the operator can pick from a dropdown.

    Adding a new format = append one entry to ``BUILTIN_TEMPLATES``; the
    address and each arg are parsed by :func:`compile_template` like any
    user-authored row, and the ``trigger`` sub-table is copied onto the
    new row at application time.

    ``trigger`` uses the read-only mapping wire form to stay decoupled
    from ``configuration``'s trigger dataclasses – the dispatch site
    copies it into a fresh dict on an ``OscTransmitterConfig`` whose
    ``__post_init__`` type-checks it. ``MappingProxyType`` keeps it
    immutable so an in-place edit can't corrupt future applications.
    """

    id: str
    name: str
    address: str
    args: tuple[str, ...]
    trigger: Mapping[str, Any] = field(
        default_factory=lambda: _DEFAULT_STREAM_30HZ_TRIGGER,
    )


# Order is the dropdown order. The trigger is set explicitly even though
# it matches the default, so the dispatch site can rely on
# ``builtin.trigger`` always being populated. All share the one read-only
# ``MappingProxyType`` instance – sharing is safe since it's immutable.
BUILTIN_TEMPLATES: tuple[BuiltinTemplate, ...] = (
    BuiltinTemplate(
        id="etc",
        name="ETC Eos",
        address="/eos/set/patch/[markerid]/augment3d/position",
        args=("[x]", "[z]", "[y]", "0", "0", "0"),
        trigger=_DEFAULT_STREAM_30HZ_TRIGGER,
    ),
    # ``id`` stays ``adm-osc`` so existing rows referencing it keep
    # resolving. Third arg is a literal ``0``, not the marker's absolute
    # Z, so the 2D variant stays 2D.
    BuiltinTemplate(
        id="adm-osc",
        name="ADM-OSC 2D",
        address="/adm/obj/[markerid]/xyz",
        args=("[x.frac]", "[y.frac]", "0"),
        trigger=_DEFAULT_STREAM_30HZ_TRIGGER,
    ),
    # 3D variant: ``[z.frac]`` for height. Requires
    # ``GridConfig.max_height`` to be set or the renderer raises and the
    # row skips. Same address as the 2D variant; only the third arg's
    # wire type differs (fractional vs the literal 0).
    BuiltinTemplate(
        id="adm-osc-3d",
        name="ADM-OSC 3D",
        address="/adm/obj/[markerid]/xyz",
        args=("[x.frac]", "[y.frac]", "[z.frac]"),
        trigger=_DEFAULT_STREAM_30HZ_TRIGGER,
    ),
    BuiltinTemplate(
        id="dnb-abs",
        name="d&b absolute",
        address="/dbaudio1/coordinatemapping/source_position_xyz/1/[markerid]",
        args=("[x]", "[y]", "[z]"),
        trigger=_DEFAULT_STREAM_30HZ_TRIGGER,
    ),
)


def builtin_by_id(template_id: str) -> BuiltinTemplate | None:
    """Look up a built-in template by id, or ``None`` if unknown."""
    for tpl in BUILTIN_TEMPLATES:
        if tpl.id == template_id:
            return tpl
    return None
