# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""CI guard: every user-visible template string must be wrapped in ``{{_()}}``.

The i18n framework only translates strings that are wrapped in a gettext
call.  A UI string added without ``{{_('…')}}`` renders fine in English but
is invisible to translators, so the catalogue silently drifts out of date.

This test scans every ``.tpl`` under ``openfollow/web/templates`` for visible
HTML text (and the handful of visible-text attributes) that still contains
literal English words *outside* a template expression, and fails with a
file:line list so the author can wrap them.

Scope / deliberate exclusions (rules, not an ever-growing allowlist):

* ``<script>`` / ``<style>`` / ``<code>`` block *contents* are skipped —
  client-side JS i18n is separate future work and ``<code>`` holds literal
  syntax (OSC placeholders, shell commands), never prose.
* Template expressions ``{{ … }}`` / ``{{! … }}`` are stripped *before* text
  extraction, so an embedded ``<code>`` inside a wrapped string cannot leak a
  spurious node.
* All-caps short tokens (stage abbreviations: DSL, USR, FOV, REF …) and
  parenthesised unit suffixes ``(mm)`` ``(px)`` ``(FPS)`` are not prose.
* Technical identifiers (URLs, filesystem paths, SPDX license ids) are not
  translated.

Known limitations (rules, not accidents — a self-test pins each one so the
behaviour cannot drift silently):

* Text nodes are only inspected when the opening ``>`` and closing ``<`` sit
  on the *same* line.  Any ``>text`` that is separated from its next ``<`` by
  a newline is out of scope — this includes multi-line prose *and* a single
  line of text whose closing tag simply wraps (``<p>Trailing text\n</p>``).
  In the current templates every string in this blind spot is deliberately
  untranslated: the AGPL notice paragraphs in ``about.tpl`` (legal text is
  conventionally not localised) and the brand/copyright footer in
  ``base.tpl``.  If a template ever grows genuine multi-line *user* copy,
  upgrade this scanner to cross-line capture (capture ``>...<`` across
  newlines, then drop ``%`` logic lines and ``<!-- -->`` comments from the
  captured span) rather than relying on this exemption.
* Visible strings passed as keyword arguments on a ``%`` logic line
  (e.g. ``% include('x.tpl', title='Device Settings')``) are not scanned;
  such call sites are rare and are wrapped at the include site instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import openfollow.web

pytestmark = pytest.mark.unit

_TEMPLATE_DIR = Path(openfollow.web.__file__).resolve().parent / "templates"

# Visible-text attributes worth translating.
_VISIBLE_ATTRS = ("alt", "aria-label", "placeholder", "title")

# Block elements whose *contents* are never translatable prose.
_SKIP_BLOCK = re.compile(r"<(script|style|code)\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
# Template expressions, possibly spanning lines: {{ … }} and {{! … }}.
_TEMPLATE_EXPR = re.compile(r"\{\{.*?\}\}", re.DOTALL)
_HTML_ENTITY = re.compile(r"&[#0-9a-zA-Z]+;")
_HAS_LETTERS = re.compile(r"[A-Za-z]{2,}")
_TEXT_NODE = re.compile(r">([^<>]*)<")

# ── Non-prose exclusion rules ────────────────────────────────────────────────
# Stage abbreviations and other all-caps tokens: DSL, USR, FOV, REF, DOWNSTAGE.
_ABBREVIATION = re.compile(r"^[A-Z]{2,10}$")
# Brand tokens that stand alone (never translated); "About OpenFollow" etc.
# containing the brand *plus* prose is still checked.
_BRAND = {"OpenFollow"}
# URLs, filesystem paths, SPDX ids, shell scripts — technical, not prose.
_TECHNICAL = re.compile(
    r"github\.com|gnu\.org|openfollow\.app|/usr/share|/licenses|"
    r"AGPL-|\.txt$|\.app$|^/|\.sh$"
)
# Parenthesised unit / abbreviation suffixes: (mm) (px) (m) (FPS) (dB).
_UNIT_SUFFIX = re.compile(r"\(\s*[A-Za-z/]{1,4}\s*\)")


def _blank_preserving_lines(match: re.Match) -> str:
    """Replace a matched span with blank lines so line numbers stay correct."""
    return "\n" * match.group(0).count("\n")


def _is_non_prose(text: str) -> bool:
    """True when ``text`` is a token we deliberately never translate."""
    if _ABBREVIATION.match(text):
        return True
    if text in _BRAND:
        return True
    if _TECHNICAL.search(text):
        return True
    # After removing unit suffixes, nothing translatable remains.
    return not _HAS_LETTERS.search(_UNIT_SUFFIX.sub(" ", text))


def _scan_template(text: str) -> list[tuple[int, str]]:
    """Return (line_no, fragment) for each unwrapped visible string."""
    # 1. Blank out non-prose blocks and template expressions up front, keeping
    #    line numbers intact.  Order matters: expressions are removed before
    #    text-node extraction so a wrapped string containing markup does not
    #    surface as a bare node.
    text = _SKIP_BLOCK.sub(_blank_preserving_lines, text)
    text = _TEMPLATE_EXPR.sub(_blank_preserving_lines, text)
    text = _HTML_ENTITY.sub(" ", text)

    hits: list[tuple[int, str]] = []
    for line_no, raw in enumerate(text.split("\n"), 1):
        line = raw.strip()
        if line.startswith("%"):  # Bottle template logic line
            continue
        for match in _TEXT_NODE.finditer(line):
            fragment = match.group(1).strip()
            if _HAS_LETTERS.search(fragment) and not _is_non_prose(fragment):
                hits.append((line_no, fragment))
        for attr in _VISIBLE_ATTRS:
            # Attribute values may be single- or double-quoted; match either.
            # The ``(?<![\w-])`` guard stops ``title=`` from matching inside a
            # longer attribute name such as ``data-title=`` (a false positive).
            for match in re.finditer(r"(?<![\w-])" + attr + r"""\s*=\s*(["'])(.*?)\1""", line):
                value = match.group(2).strip()
                if _HAS_LETTERS.search(value) and not _is_non_prose(value):
                    hits.append((line_no, f'{attr}="{value}"'))
    return hits


def _all_templates() -> list[Path]:
    return sorted(_TEMPLATE_DIR.rglob("*.tpl"))


def test_template_dir_exists() -> None:
    assert _TEMPLATE_DIR.is_dir(), f"template dir not found: {_TEMPLATE_DIR}"
    assert _all_templates(), "no .tpl files discovered"


@pytest.mark.parametrize("template", _all_templates(), ids=lambda p: p.name)
def test_no_unwrapped_user_visible_strings(template: Path) -> None:
    """Every visible string in the template is wrapped in ``{{_()}}``."""
    hits = _scan_template(template.read_text(encoding="utf-8"))
    if hits:
        rel = template.relative_to(_TEMPLATE_DIR.parent.parent.parent)
        listing = "\n".join(f"    {rel}:{ln}  {frag!r}" for ln, frag in hits)
        pytest.fail(
            f"{len(hits)} unwrapped user-visible string(s) in {template.name}:\n"
            f"{listing}\n"
            "→  Wrap each with {{_('…')}}.  If a fragment is genuinely not "
            "prose (an abbreviation, unit, URL, or brand token), extend the "
            "rule-based exclusions in this test rather than adding a literal "
            "allowlist entry."
        )


# ── Guard-logic self-tests (the scanner must not silently rot) ───────────────


class TestScannerLogic:
    """Direct tests for the extraction rules, independent of the templates."""

    def test_flags_bare_html_text(self) -> None:
        assert _scan_template("<h2>Detection Masks</h2>") == [(1, "Detection Masks")]

    def test_passes_wrapped_html_text(self) -> None:
        assert _scan_template("<h2>{{_('Detection Masks')}}</h2>") == []

    def test_flags_bare_visible_attribute(self) -> None:
        assert _scan_template('<img alt="Camera snapshot">') == [(1, 'alt="Camera snapshot"')]

    def test_passes_wrapped_visible_attribute(self) -> None:
        assert _scan_template("<img alt=\"{{_('Camera snapshot')}}\">") == []

    def test_skips_script_block_contents(self) -> None:
        assert _scan_template("<script>\nconst x = 'Hello world';\n</script>") == []

    def test_skips_code_block_contents(self) -> None:
        assert _scan_template("<span><code>[fader:3].transform</code></span>") == []

    def test_skips_bottle_logic_line(self) -> None:
        assert _scan_template("% if error:") == []

    def test_ignores_all_caps_abbreviation(self) -> None:
        assert _scan_template("<text>DSL</text>") == []

    def test_ignores_unit_suffix_left_outside_wrap(self) -> None:
        # "{{_('Detection rate')}} (FPS)" → residual "(FPS)" is a unit.
        assert _scan_template("<label>{{_('Detection rate')}} (FPS)</label>") == []

    def test_ignores_brand_token(self) -> None:
        assert _scan_template('<input placeholder="OpenFollow">') == []

    def test_ignores_technical_identifier(self) -> None:
        assert _scan_template("<span>AGPL-3.0-or-later</span>") == []

    def test_embedded_code_in_wrapped_string_not_flagged(self) -> None:
        line = "<span>{{!_('Send with <code>[markerfader]</code> placeholder.')}}</span>"
        assert _scan_template(line) == []

    def test_reports_correct_line_number(self) -> None:
        text = "<div>\n<span>{{_('wrapped ok')}}</span>\n<h2>Bare heading</h2>\n"
        assert _scan_template(text) == [(3, "Bare heading")]

    def test_flags_text_partially_left_outside_wrap(self) -> None:
        # A literal word sitting *beside* a wrapped expression is still a leak.
        assert _scan_template("<span>Prefix {{_('ok')}}</span>") == [(1, "Prefix")]

    def test_flags_single_quoted_visible_attribute(self) -> None:
        # Attribute values may use single quotes; the scanner must still see them.
        assert _scan_template("<img alt='Camera snapshot'>") == [(1, 'alt="Camera snapshot"')]

    def test_passes_wrapped_single_quoted_visible_attribute(self) -> None:
        assert _scan_template("<img alt='{{_(\"Camera snapshot\")}}'>") == []

    def test_visible_attr_name_not_matched_as_substring(self) -> None:
        # ``title=`` must not fire inside a longer attribute name like
        # ``data-title=`` (that value is not a visible tooltip string).
        assert _scan_template('<div data-title="Hidden tooltip text">') == []

    def test_known_limitation_include_kwarg_not_scanned(self) -> None:
        # Documented blind spot: visible strings passed as keyword arguments
        # on a ``%`` logic line are not scanned (see "Known limitations").
        # Pins the include-kwarg exemption named in the docstring.
        assert _scan_template("% include('x.tpl', title='Device Settings')") == []

    def test_known_limitation_cross_line_text_not_flagged(self) -> None:
        # Documented blind spot: a text node whose closing tag wraps to the
        # next line is out of scope (see the "Known limitations" docstring).
        # This pins the boundary — if a scanner change starts catching
        # cross-line text, update the docstring and the exemptions with it.
        assert _scan_template("<p>Should NOT be caught\n</p>") == []
