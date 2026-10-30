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
    r"\.(notice|update-notice|modal-error|diag-error|gallery-error|wizard-action-required|"
    r"network-banner(?:-error|-ok|-info)?|net-iface-method-badge|stat-chip|diag-status-pill|diag-event-status|badge-experimental|"
    r"modal-list-item-badge|slot-row|slot-missing|slot-state|slot-note|slot-activity|osc-binding-fault|"
    r"osc-binding-row|osc-binding-enabled-dot|osc-binding-nested-row|osc-pill|peer-item|peer-status|toast|"
    r"save-error|save-failed|field-error-msg|field-warn-msg|field-note-msg|conflict-flag|not-controlled|"
    r"add-feedback|saved-flash|wizard-status|wizard-preview-container|wizard-meter|awaiting-password|wizard-field-error|"
    r"update-flag|danger|btn-danger|modal-list-item-delete|gallery-del|dme-row|ia-dot|midi-fader-strip-not-picked-up|"
    r"park-toggle|park-badge)"
    r"(?![\w-])"
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
    for component in (
        ".notice.error",
        ".stat-chip.ok",
        ".osc-pill",
        ".peer-item.offline",
        ".conflict-flag",
        ".park-badge",
        ".park-toggle",
    ):
        assert component in selectors


def test_the_web_boxes_take_the_documented_radius() -> None:
    radius = re.search(r"\*\*Web boxes:\*\* (\S+),", _DOC.read_text(encoding="utf-8"))
    assert radius
    boxes = [body for _, selector, body in _status_rules() if selector.startswith(".notice, .update-notice")]
    assert len(boxes) == 1
    assert f"border-radius: {radius.group(1)};" in boxes[0]


def test_status_rules_take_their_colours_from_tokens() -> None:
    literal = []
    for name, selector, body in _status_rules():
        for colour in _COLOUR.findall(body):
            r, g, b, _ = _rgba(colour)
            if max(r, g, b) - min(r, g, b) > _MAX_NEUTRAL_CHROMA:
                literal.append(f"{name}: {selector} uses {colour}")
    assert literal == []


def _over(fg: tuple[int, int, int, float], bg: tuple[int, int, int]) -> tuple[int, int, int]:
    r, g, b, a = fg
    return (round(r * a + bg[0] * (1 - a)), round(g * a + bg[1] * (1 - a)), round(b * a + bg[2] * (1 - a)))


def _contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    def lum(rgb: tuple[int, int, int]) -> float:
        lin = [c / 255 / 12.92 if c / 255 <= 0.04045 else ((c / 255 + 0.055) / 1.055) ** 2.4 for c in rgb]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _pickup_pill_rule() -> str:
    bodies = [body for _, selector, body in _status_rules() if selector == ".midi-fader-strip-not-picked-up"]
    assert len(bodies) == 1
    return bodies[0]


def test_an_unpicked_midi_fader_is_a_caution_row_pill() -> None:
    body = _pickup_pill_rule()
    for declaration in (
        "background: var(--caution-chip);",
        "border: 1px solid var(--caution-line);",
        "color: var(--text);",
        "border-radius: 0.2rem;",
        "font-size: 0.7rem;",
    ):
        assert declaration in body
    assert "--accent" not in body
    assert "line-height: 1.2;" in body
    assert "overflow-wrap: anywhere;" in body
    base = (_TEMPLATES / "base.tpl").read_text(encoding="utf-8")
    supports = base.index("@supports (text-box: trim-both cap alphabetic)")
    fallback = base[: base.index("{", base.rindex("/* Pills set --pill-pad-y", 0, supports))]
    trim = base[supports:].split("}", 1)[0]
    assert ".midi-fader-strip-not-picked-up" in fallback.split("*/")[-1]
    assert ".midi-fader-strip-not-picked-up" in trim


def test_a_picked_up_fader_keeps_the_pills_slot() -> None:
    """Hidden, not removed: the strips stretch to the tallest, so a pill that came and went would resize them all."""
    rules = {
        selector: body.strip()
        for _, selector, body in _status_rules()
        if selector.startswith(".midi-fader-strip-not-picked-up[data-picked-up]")
    }
    assert rules == {".midi-fader-strip-not-picked-up[data-picked-up]": "visibility: hidden;"}


def _fader_tint_alpha() -> float:
    """The alpha midi.tpl appends to a fader's colour to tint its strip."""
    midi = (_TEMPLATES / "partials" / "midi.tpl").read_text(encoding="utf-8")
    suffix = re.search(r"_fader_color \+ '([0-9a-fA-F]{2})'", midi)
    assert suffix, "the strip tint moved"
    return round(int(suffix.group(1), 16) / 255, 3)


def _fader_colours() -> list[str]:
    from openfollow.palette import GREYS, HUE_COLUMNS

    return [hex_ for column in [*HUE_COLUMNS, GREYS] for hex_, _ in column if hex_ != "#000000"]


@pytest.mark.parametrize("colour", _fader_colours())
def test_the_pickup_pill_holds_aa_over_every_fader_tint(colour: str) -> None:
    """The strip sits in a section, on the page or under its gold glow (the accent at the soft alpha)."""
    defined = _root_tokens()
    deep = _rgba(defined["--bg-deep"])[:3]
    pages = (deep, _rgba(defined["--bg-base"])[:3], _over(_rgba(defined["--accent-soft"]), deep))
    tint = (*_rgba(colour)[:3], _fader_tint_alpha())
    for page in pages:
        pill = _over(_rgba(defined["--caution-chip"]), _over(tint, _over(_rgba(defined["--surface"]), page)))
        assert _contrast(_rgba(defined["--text"])[:3], pill) >= 4.5


# Hue gap that keeps caution reading apart from the accent, not a near copy of it.
_MIN_HUE_GAP_DEG = 15.0


def _hue_gap(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    ha, hb = (colorsys.rgb_to_hls(*(c / 255 for c in rgb[:3]))[0] * 360 for rgb in (a, b))
    gap = abs(ha - hb) % 360
    return min(gap, 360 - gap)


@pytest.mark.parametrize("token", ["--caution-line", "--caution-text", "--caution-border"])
def test_web_caution_reads_apart_from_the_accent(token: str) -> None:
    defined = _root_tokens()
    assert _hue_gap(_rgba(defined[token]), _rgba(defined["--accent"])) >= _MIN_HUE_GAP_DEG


def test_hud_caution_reads_apart_from_the_accent() -> None:
    from openfollow.runtime.overlay_draw_style import COLOR_ACCENT, COLOR_CAUTION_BORDER

    border, accent = (tuple(round(c * 255) for c in rgb) for rgb in (COLOR_CAUTION_BORDER, COLOR_ACCENT))
    assert _hue_gap(border, accent) >= _MIN_HUE_GAP_DEG


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


@pytest.mark.parametrize("level", ["error", "caution", "info", "success"])
def test_each_hud_level_is_the_web_chip_of_that_level(level: str) -> None:
    """One language on both surfaces: a HUD status row is the web chip's fill and line."""
    from openfollow.runtime.overlay_draw_style import STATUS_LEVEL_COLORS

    fill, border, _cut = STATUS_LEVEL_COLORS[level]
    chip = _doc_rgba(_doc_table()[f"--{level}-chip"])
    line = _doc_rgba(_doc_table()[f"--{level}-line"])
    assert (*(round(c * 255) for c in fill[:3]), fill[3]) == chip
    assert tuple(round(c * 255) for c in border) == line[:3]


def _support_section() -> str:
    doc = _DOC.read_text(encoding="utf-8")
    start = doc.index("## Not a status: Support OpenFollow")
    return doc[start : doc.index("\n## ", start + 1)]


def test_the_support_card_tokens_match_the_doc() -> None:
    """The card is documented outside the level table, so it is pinned here."""
    from openfollow.runtime.overlay_draw_style import COLOR_SUPPORT_BORDER

    section = _support_section()
    defined = _root_tokens()
    border = next(line for line in section.splitlines() if line.startswith("| `--support-border`"))
    expected = _doc_rgba(border.split("|")[2])
    assert _rgba(defined["--support-border"]) == expected
    assert tuple(round(c * 255) for c in COLOR_SUPPORT_BORDER[:3]) == expected[:3]
    assert round(COLOR_SUPPORT_BORDER[3], 3) == expected[3]
    qr = next(line for line in section.splitlines() if line.startswith("| `--qr-light`"))
    light, dark = re.findall(r"`(#[0-9a-fA-F]{6})`", qr)
    assert _rgba(defined["--qr-light"])[:3] == _rgba(light)[:3]
    assert _rgba(defined["--qr-dark"])[:3] == _rgba(dark)[:3]


def test_the_support_card_never_takes_a_level_colour() -> None:
    """It shows no state; a level's token, or a coloured literal beside its own, would make it read as one."""
    rules = [
        (" ".join(selector.split()), body)
        for name, css in _style_blocks()
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
        if re.search(r"\.(support-card|support-heart|qr-field|qr-modules)(?![\w-])", selector)
    ]
    assert rules, "the support card's rules moved"
    for selector, body in rules:
        assert not re.search(r"--(error|caution|info|success)-", body), selector
        for colour in _COLOUR.findall(body):
            r, g, b, _ = _rgba(colour)
            assert max(r, g, b) - min(r, g, b) <= _MAX_NEUTRAL_CHROMA, f"{selector} uses {colour}"
    border = next(body for selector, body in rules if selector == ".support-card")
    assert "dashed var(--support-border)" in border


def _winning_sign(classes: set[str]) -> str:
    """The ``content`` base.tpl gives ``::before`` on an element of ``classes``, by specificity, then order."""
    base = (_TEMPLATES / "base.tpl").read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", "".join(re.findall(r"<style[^>]*>(.*?)</style>", base, re.S)), flags=re.S)
    best: tuple[int, int, str] | None = None
    for order, (selector, body) in enumerate(re.findall(r"([^{}]+)\{([^{}]*)\}", css)):
        content = re.search(r"content\s*:\s*([^;]+);", body)
        if content is None:
            continue
        for part in selector.split(","):
            compound = re.fullmatch(r"\s*((?:\.[\w-]+)+)::before\s*", part)
            if compound and set(compound.group(1)[1:].split(".")) <= classes:
                specificity = compound.group(1).count(".")
                if best is None or (specificity, order) >= best[:2]:
                    best = (specificity, order, content.group(1).strip())
    assert best is not None
    return best[2]


@pytest.mark.parametrize(
    "classes",
    [
        {"network-banner", "network-banner-info", "network-banner-busy"},
        {"notice", "network-banner-busy"},
    ],
    ids=["network-apply", "notice"],
)
def test_a_progress_box_shows_its_spinner_in_place_of_the_sign(classes: set[str]) -> None:
    assert _winning_sign(classes) == "none"


@pytest.mark.parametrize(
    "classes", [{"network-banner", "network-banner-info"}, {"notice"}], ids=["network-banner", "notice"]
)
def test_a_box_that_is_not_busy_keeps_its_sign(classes: set[str]) -> None:
    assert _winning_sign(classes) == '""'
