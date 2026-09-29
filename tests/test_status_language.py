# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The web UI's status colours come from one token set, documented in docs/STATUS_LANGUAGE.md."""

from __future__ import annotations

import colorsys
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES = _ROOT / "openfollow" / "web" / "templates"
_DOC = _ROOT / "docs" / "STATUS_LANGUAGE.md"
_LEVELS = ("error", "caution", "info", "success")

# Every class that shows a state; a rule for any of them must take its colours from the tokens.
_STATUS_CLASS = re.compile(
    r"\.(notice|update-notice|restart-notice|modal-error|diag-error|gallery-error|wizard-action-required|"
    r"network-banner(?:-error|-ok)?|stat-chip|diag-status-pill|diag-event-status|badge-experimental|"
    r"modal-list-item-badge|slot-row|slot-missing|slot-state|slot-note|slot-activity|osc-binding-fault|"
    r"osc-binding-row|osc-binding-enabled-dot|osc-binding-nested-row|osc-pill|peer-item|peer-status|toast|"
    r"save-error|save-failed|field-error-msg|field-warn-msg|field-note-msg|conflict-flag|not-controlled|"
    r"add-feedback|saved-flash|wizard-status|wizard-preview-container|awaiting-password|wizard-field-error|"
    r"update-flag|danger|btn-danger|modal-list-item-delete|gallery-del|dme-row|ia-dot)(?![\w-])"
)
_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)")
# Greys, whites and the off-white text: neutral, so a literal is fine.
_MAX_NEUTRAL_CHROMA = 24


def _rgba(text: str) -> tuple[int, int, int, float]:
    text = text.strip().lower()
    if text.startswith("#"):
        digits = text[1:]
        if len(digits) in (3, 4):
            digits = "".join(c * 2 for c in digits)
        r, g, b = (int(digits[i : i + 2], 16) for i in (0, 2, 4))
        a = int(digits[6:8], 16) / 255 if len(digits) == 8 else 1.0
        return r, g, b, round(a, 3)
    name, args = text.split("(", 1)
    parts = [p for p in re.split(r"[\s,/]+", args.rstrip(")")) if p]
    if name.startswith("rgb"):
        r, g, b = (int(float(p)) for p in parts[:3])
        return r, g, b, round(float(parts[3]) if len(parts) > 3 else 1.0, 3)
    h = float(parts[0].removesuffix("deg")) / 360
    s, lum = (float(p.rstrip("%")) / 100 for p in parts[1:3])
    r, g, b = (round(c * 255) for c in colorsys.hls_to_rgb(h, lum, s))
    return r, g, b, 1.0


def _root_tokens() -> dict[str, str]:
    base = (_TEMPLATES / "base.tpl").read_text(encoding="utf-8")
    root = base[base.index(":root {") : base.index("}", base.index(":root {"))]
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", root))


def _doc_table() -> dict[str, str | None]:
    """Token name -> value as documented; None where the doc says the level has none."""
    rows: dict[str, str | None] = {}
    doc = _DOC.read_text(encoding="utf-8")
    section = doc[doc.index("## Tokens") : doc.index("\n## ", doc.index("## Tokens") + 1)]
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        name = re.match(r"`(--[\w<>-]+)`", cells[0]) if cells else None
        if not name:
            continue
        if "<level>" not in name.group(1):
            rows[name.group(1)] = cells[1]
            continue
        for level, cell in zip(_LEVELS, cells[1:], strict=True):
            rows[name.group(1).replace("<level>", level)] = None if cell == "none" else cell
    return rows


def _doc_rgba(cell: str) -> tuple[int, int, int, float]:
    colour = re.search(r"`([^`]+)`", cell)
    assert colour, cell
    r, g, b, _ = _rgba(colour.group(1))
    alpha = re.search(r"at (\d+)%", cell)
    return r, g, b, round(int(alpha.group(1)) / 100, 3) if alpha else 1.0


def test_the_doc_token_table_covers_every_level() -> None:
    documented = _doc_table()
    assert len(documented) >= 29, "the Tokens table no longer parses"
    assert {f"--{level}-fill" for level in _LEVELS} <= documented.keys()


@pytest.mark.parametrize("token", sorted(t for t, v in _doc_table().items() if v is not None))
def test_each_token_has_the_documented_value(token: str) -> None:
    defined = _root_tokens()
    assert token in defined, f"{token} is documented but not defined on :root"
    assert _rgba(defined[token]) == _doc_rgba(_doc_table()[token])


@pytest.mark.parametrize("token", sorted(t for t, v in _doc_table().items() if v is None))
def test_a_level_documented_without_a_token_defines_none(token: str) -> None:
    assert token not in _root_tokens()


def _style_blocks() -> list[tuple[str, str]]:
    blocks = []
    for path in sorted(_TEMPLATES.rglob("*.tpl")):
        text = path.read_text(encoding="utf-8")
        for css in re.findall(r"<style[^>]*>(.*?)</style>", text, re.S):
            blocks.append((path.name, re.sub(r"/\*.*?\*/", "", css, flags=re.S)))
    return blocks


def _status_rules() -> list[tuple[str, str, str]]:
    rules = []
    for name, css in _style_blocks():
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            if _STATUS_CLASS.search(selector):
                rules.append((name, " ".join(selector.split()), body))
    return rules


@pytest.mark.parametrize("path", sorted(_TEMPLATES.rglob("*.tpl")), ids=lambda p: p.name)
def test_every_style_block_closes_each_brace_it_opens(path: Path) -> None:
    """A stray ``}`` turns the next selector invalid, and the browser drops that whole rule unseen."""
    for css in re.findall(r"<style[^>]*>(.*?)</style>", path.read_text(encoding="utf-8"), re.S):
        depth = 0
        for ch in re.sub(r"/\*.*?\*/", "", css, flags=re.S):
            depth += {"{": 1, "}": -1}.get(ch, 0)
            assert depth >= 0, f"{path.name}: a '}}' closes nothing"
        assert depth == 0, f"{path.name}: a '{{' is never closed"


def test_the_status_rule_scan_finds_the_components() -> None:
    selectors = " ".join(selector for _, selector, _ in _status_rules())
    for component in (".notice.error", ".stat-chip.ok", ".osc-pill", ".peer-item.offline", ".conflict-flag"):
        assert component in selectors


def test_status_rules_take_their_colours_from_tokens() -> None:
    literal = []
    for name, selector, body in _status_rules():
        for colour in _COLOUR.findall(body):
            r, g, b, _ = _rgba(colour)
            if max(r, g, b) - min(r, g, b) > _MAX_NEUTRAL_CHROMA:
                literal.append(f"{name}: {selector} uses {colour}")
    assert literal == []


@pytest.mark.parametrize(("keyframes", "token"), [("flash-green", "--success-line"), ("flash-red", "--error-line")])
def test_the_save_flashes_use_the_level_line(keyframes: str, token: str) -> None:
    base = (_TEMPLATES / "base.tpl").read_text(encoding="utf-8")
    body = base[base.index(f"@keyframes {keyframes}") :].split("}\n", 2)
    assert f"var({token})" in body[0]
    assert not _COLOUR.search(body[0])


# The one-off colours the status language replaced.
_RETIRED = (
    "#ffd7d7", "#ffd6d6", "#ff8a8a", "#f55", "#6cf07a", "#c0392b", "#fdf2f2", "#d6ffd9", "#d6e6ff",
    "#c8ffd8", "#ffe1a2", "#ffe7ae", "#9fc9ff", "#d5e9ff", "#ffd7a8", "#ff7070", "#ffb066", "#ff5c5c",
    "255,76,76", "120,180,255", "76,175,80", "255,120,120", "#ff8c8c", "#7de59f", "#ffd166", "#5ad17a",
    "#ffcc55", "255,140,140", "125,229,159", "159,201,255", "120,200,120", "255,160,160", "90,209,122",
)  # fmt: skip


def _markup_sources() -> list[Path]:
    web = _ROOT / "openfollow" / "web"
    inputs = _ROOT / "openfollow" / "video" / "inputs"
    return sorted(
        [*web.rglob("*.tpl"), *web.rglob("*.js"), *web.glob("*.py"), *inputs.glob("*.py")],
    )


@pytest.mark.parametrize("colour", _RETIRED)
def test_a_retired_status_colour_stays_gone(colour: str) -> None:
    pattern = re.compile(re.escape(colour) + r"(?![0-9a-fA-F])", re.I)
    found = [
        path.name
        for path in _markup_sources()
        if pattern.search(
            re.sub(r"\s", "", path.read_text(encoding="utf-8")) if "," in colour else path.read_text(encoding="utf-8")
        )
    ]
    assert found == []


@pytest.mark.parametrize("token", ["--ok", "--danger", "--btn-danger-border"])
def test_a_retired_colour_token_stays_gone(token: str) -> None:
    assert token not in _root_tokens()
    assert [path.name for path in _markup_sources() if f"var({token})" in path.read_text(encoding="utf-8")] == []


def _hover_backgrounds() -> dict[str, str]:
    """Each ``:hover`` selector in the templates' CSS, mapped to the background it sets."""
    found = {}
    for _, css in _style_blocks():
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            background = re.search(r"background(?:-color)?\s*:\s*([^;]+);", body)
            for part in selector.split(","):
                if ":hover" in part and background:
                    found[" ".join(part.split())] = background.group(1).strip()
    return found


@pytest.mark.parametrize(
    "control",
    [
        ".btn-danger:hover:not(:disabled)",
        "button.danger:hover:not(:disabled)",
        ".modal-list-item-delete:hover",
        ".gallery-del:hover",
        "#detection-mask-editor .dme-row button:hover",
        "#detection-mask-editor .dme-btn.danger:hover:not(:disabled)",
    ],
)
def test_every_destructive_control_darkens_to_the_fault_row_tint(control: str) -> None:
    assert _hover_backgrounds().get(control) == "var(--error-row)"
