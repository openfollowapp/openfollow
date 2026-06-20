# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Consistency check: every web-form input registered in FIELD_RULES has
the on-blur validation markup.

Walks every partial template and asserts that every ``<input>`` whose
``name`` matches a ``FIELD_RULES`` entry carries:

- ``hx-get="/api/validate/<section>/<name>"``
- ``hx-trigger`` containing ``"blur"``
- ``hx-target`` referencing a sibling ``<span class="field-error">``
- ``aria-describedby`` matching the same id as ``hx-target``

The section is derived from the partial's filename (``grid.tpl`` → ``"grid"``).
``type="hidden"``, ``type="checkbox"``, and ``type="radio"`` inputs are skipped
– discrete choices don't need on-blur validation. ``<select>`` is also skipped
(it's an HTML element distinct from ``<input>``; the consistency check is scoped
to ``<input>`` only).

A new partial that adds an input with a registered name without the
validation markup will fail this test – that's the gate against future
drift.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from html.parser import HTMLParser
from pathlib import Path

import pytest

from openfollow.web.validation import FIELD_RULES

pytestmark = pytest.mark.unit

_PARTIALS_DIR = Path(__file__).resolve().parents[1] / "openfollow" / "web" / "templates" / "partials"

# Read-only display partials. They contain no editable fields.
_SKIP_PARTIALS = {"overview.tpl", "statistics.tpl"}

# Inputs whose ``type`` makes blur validation meaningless.
_SKIP_INPUT_TYPES = {"hidden", "checkbox", "radio", "file", "submit", "button", "reset"}


class _InputCollector(HTMLParser):
    """Collect every ``<input>`` start tag with its attribute dict."""

    def __init__(self) -> None:
        super().__init__()
        self.inputs: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "input":
            return
        attr_map = {k: (v or "") for k, v in attrs}
        self.inputs.append(attr_map)

    # The error span we depend on is a sibling tag – for the consistency
    # check we don't need to model the full DOM, just observe that the
    # span exists somewhere in the template (which the .tpl source check
    # below covers).


def _parse_partial(path: Path) -> list[dict[str, str]]:
    parser = _InputCollector()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser.inputs


def _section_from_path(path: Path) -> str:
    return path.stem


def _registered_inputs() -> Iterable[tuple[Path, str, dict[str, str]]]:
    """Yield (path, section, input_attrs) for every input that needs markup."""
    for path in sorted(_PARTIALS_DIR.glob("*.tpl")):
        if path.name in _SKIP_PARTIALS:
            continue
        section = _section_from_path(path)
        rules = FIELD_RULES.get(section)
        if rules is None:
            continue
        for input_attrs in _parse_partial(path):
            input_type = input_attrs.get("type", "text").lower()
            if input_type in _SKIP_INPUT_TYPES:
                continue
            name = input_attrs.get("name", "")
            if not name or name not in rules:
                continue
            yield path, section, input_attrs


def test_partials_directory_exists() -> None:
    assert _PARTIALS_DIR.is_dir(), f"missing partials dir: {_PARTIALS_DIR}"


def test_every_registered_input_has_validation_markup() -> None:
    missing: list[str] = []
    for path, section, attrs in _registered_inputs():
        name = attrs["name"]
        expected_get = f"/api/validate/{section}/{name}"
        hx_get = attrs.get("hx-get", "")
        hx_trigger = attrs.get("hx-trigger", "")
        hx_target = attrs.get("hx-target", "")
        aria_describedby = attrs.get("aria-describedby", "")
        if hx_get != expected_get:
            missing.append(f"{path.name}: input name={name!r} expected hx-get={expected_get!r}, got {hx_get!r}")
            continue
        if "blur" not in hx_trigger:
            missing.append(f"{path.name}: input name={name!r} hx-trigger missing 'blur': {hx_trigger!r}")
            continue
        if not hx_target.startswith("#") or not hx_target.endswith("-error"):
            missing.append(
                f"{path.name}: input name={name!r} hx-target must point at a sibling #…-error span, got {hx_target!r}"
            )
            continue
        if aria_describedby != hx_target.lstrip("#"):
            missing.append(
                f"{path.name}: input name={name!r} aria-describedby ({aria_describedby!r}) "
                f"must match hx-target id ({hx_target!r})"
            )
            continue
        # Confirm the id and field-error class live on the *same* element.
        # Both must be on one element for proper HTMX swap targeting.
        body = path.read_text(encoding="utf-8")
        target_id = hx_target.lstrip("#")
        # Match a single <span ...> opening tag whose attributes contain
        # both ``id="<target_id>"`` and the ``field-error`` class token.
        # We don't try to be a full HTML parser – the templates are
        # generated, so a simple per-tag regex is enough to guarantee
        # both attributes ride on the same element.
        span_re = re.compile(
            r"<span\b[^>]*\bid=\"" + re.escape(target_id) + r"\"[^>]*>",
            re.IGNORECASE,
        )
        candidate_tags = span_re.findall(body)
        has_field_error_class = any(re.search(r'\bclass="[^"]*\bfield-error\b', tag) for tag in candidate_tags)
        if not candidate_tags or not has_field_error_class:
            missing.append(
                f"{path.name}: input name={name!r} declares hx-target #{target_id} "
                f'but no matching <span id={target_id!r} class="field-error"> exists'
            )
    assert not missing, "Validation markup drift:\n  " + "\n  ".join(missing)


def test_aria_invalid_starts_false() -> None:
    """Every validated input renders with ``aria-invalid="false"`` – the JS
    gate flips it to ``true`` only after a failed swap. A new template that
    omits the attribute would render with the input still gated by Save's
    pre-existing ``[aria-invalid="true"]`` selector."""
    bad: list[str] = []
    for path, _section, attrs in _registered_inputs():
        if "hx-get" not in attrs:
            continue  # other tests will already flag missing markup
        if attrs.get("aria-invalid", "") != "false":
            bad.append(
                f"{path.name}: input name={attrs.get('name')!r} expected "
                f'aria-invalid="false", got {attrs.get("aria-invalid")!r}'
            )
    assert not bad, "aria-invalid not initialised:\n  " + "\n  ".join(bad)


def test_hx_include_closest_form_for_cross_field_context() -> None:
    """Inputs include the rest of the form so cross-field notes work."""
    bad: list[str] = []
    for path, _section, attrs in _registered_inputs():
        if "hx-get" not in attrs:
            continue
        if "closest form" not in attrs.get("hx-include", ""):
            bad.append(f'{path.name}: input name={attrs.get("name")!r} missing hx-include="closest form"')
    assert not bad, "missing hx-include:\n  " + "\n  ".join(bad)
