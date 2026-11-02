# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann, Michel Honold, and Vinzenz Schultz
"""Invariant: the image's kanshi config pins 1920x1080 on every active output.

The on-screen UI (Cairo HUD) is drawn at fixed pixel sizes with no output-scale
awareness, so on a 4K panel (preferred mode 3840x2160) the whole UI renders at
~half size. The image kanshi config must pin 1080p on every active (non-disabled)
output so a 4K panel doesn't ship a tiny UI.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _kanshi_config() -> str:
    here = Path(inspect.getsourcefile(_kanshi_config) or "")
    return (here.resolve().parents[1] / "packaging" / "debian" / "kanshi.config").read_text()


def _output_lines() -> list[str]:
    return [ln.strip() for ln in _kanshi_config().splitlines() if ln.strip().startswith("output ")]


def test_has_output_lines() -> None:
    assert _output_lines(), "no `output` directives found in kanshi.config"


def test_every_active_output_pins_1080p() -> None:
    """Each active output must pin `mode 1920x1080` so a 4K panel's preferred
    3840x2160 mode can't make the fixed-pixel UI render at half size.

    Active = any `output` directive not explicitly `disable`d. Filtering on the
    `enable` keyword instead would vacuously pass if a future edit dropped
    `enable` while leaving the mode unpinned – so assert at least one active
    output is present and check every one of them.
    """
    active = [line for line in _output_lines() if "disable" not in line]
    assert active, "no active (non-disabled) output directives in kanshi.config"
    for line in active:
        assert re.search(r"\bmode 1920x1080\b", line), f"active output without pinned 1080p mode: {line!r}"


def _profiles() -> dict[str, list[str]]:
    """Profile name -> its output criteria, in file order (kanshi takes the first match)."""
    profiles: dict[str, list[str]] = {}
    current: list[str] | None = None
    for raw in _kanshi_config().splitlines():
        line = raw.strip()
        if line.startswith("profile "):
            current = profiles.setdefault(line.split()[1], [])
        elif line.startswith("output ") and current is not None:
            current.append(line.split()[1])
    return profiles


def _first_match(connected: list[str]) -> str | None:
    """kanshi's rule: as many outputs as heads, each claimed by its name or `*`."""
    for name, criteria in _profiles().items():
        if len(criteria) != len(connected):
            continue
        free = list(connected)
        for crit in criteria:
            hit = next((head for head in free if crit in ("*", head)), None)
            if hit is None:
                break
            free.remove(hit)
        else:
            return name
    return None


@pytest.mark.parametrize(
    "connected",
    [["DP-1"], ["eDP-1"], ["HDMI-A-3"], ["DP-1", "DP-2"], ["eDP-1", "HDMI-A-1"]],
)
def test_any_connector_gets_a_1080p_profile(connected: list[str]) -> None:
    """A PC's display sits on DP / eDP / a third HDMI port; with no profile there
    kanshi leaves its preferred mode and a 4K panel shows the UI at half size."""
    assert _first_match(connected) is not None, f"no kanshi profile matches {connected}"


@pytest.mark.parametrize(
    ("connected", "profile"),
    [(["HDMI-A-1"], "single-1"), (["HDMI-A-2"], "single-2"), (["HDMI-A-1", "HDMI-A-2"], "dual")],
)
def test_pi_hdmi_ports_keep_their_own_profiles(connected: list[str], profile: str) -> None:
    assert _first_match(connected) == profile
