"""The Setup Wizard hides elements with the ``hidden`` attribute; a class or tag rule that sets ``display`` beats it."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_TEMPLATES = Path(__file__).resolve().parent.parent / "openfollow" / "web" / "templates"
_BASE = (_TEMPLATES / "base.tpl").read_text(encoding="utf-8")
_WIZARD = (_TEMPLATES / "wizard.tpl").read_text(encoding="utf-8")
_SIMPLE = r"(\.[\w-]+|[a-z]+)"


def _display_selectors() -> tuple[set[str], set[str]]:
    """Simple selectors (a class or a tag) that set a visible display, and those with a ``[hidden]`` rule."""
    css = "".join(re.findall(r"<style[^>]*>(.*?)</style>", _BASE + _WIZARD, re.S))
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    shown: set[str] = set()
    hides: set[str] = set()
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        display = re.search(r"display\s*:\s*([a-z-]+)", body)
        if not display:
            continue
        for sel in (s.strip() for s in selectors.split(",")):
            if display.group(1) != "none" and re.fullmatch(_SIMPLE, sel):
                shown.add(sel)
            hidden = re.fullmatch(_SIMPLE + r"\[hidden\]", sel)
            if display.group(1) == "none" and hidden:
                hides.add(hidden.group(1))
    return shown, hides


def _hideable_elements() -> list[tuple[str, str, list[str]]]:
    """(id or markup, tag, classes) of every wizard element that carries ``hidden`` or has it set by script."""
    toggled = set(re.findall(r"\$\('([\w-]+)'\)\.hidden\s*=", _WIZARD))
    toggled |= set(re.findall(r"getElementById\('([\w-]+)'\)\.hidden\s*=", _WIZARD))
    found = []
    for tag, attrs in re.findall(r"<([a-z]+)\b([^<>]*)>", _WIZARD):
        ident = re.search(r'\bid="([\w-]+)"', attrs)
        if not (re.search(r"\shidden\b", attrs) or (ident and ident.group(1) in toggled)):
            continue
        classes = re.search(r'\bclass="([^"]*)"', attrs)
        found.append((ident.group(1) if ident else attrs.strip(), tag, classes.group(1).split() if classes else []))
    return found


def test_the_wizard_has_hideable_elements_to_check() -> None:
    names = [name for name, _, _ in _hideable_elements()]
    assert {"cal-solved", "adv-add-tri", "cal-lens-note"} <= set(names)


def test_every_hideable_wizard_element_disappears_when_hidden() -> None:
    shown, hides = _display_selectors()
    leaks = [
        (name, sel)
        for name, tag, classes in _hideable_elements()
        for sel in [tag, *("." + c for c in classes)]
        if sel in shown and sel not in hides
    ]
    assert leaks == []
