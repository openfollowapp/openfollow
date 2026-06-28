# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""First-boot config seeding: the shipped example must reach a fresh device.

A fresh image install boots with no ``config.toml``. ``bootstrap_config_if_missing``
copies the curated ``config.example.toml`` from the *config directory* (the
service's WorkingDirectory) so the device comes up with the stage scene and a
default marker instead of bare dataclass defaults (grey screen, no markers).

The bug these tests guard: the deb shipped the example only to ``/usr/share`` while
the service runs from ``/var/lib/openfollow`` and ``config.toml`` lives there – so
bootstrap never found it and every fresh image fell through to dataclass defaults.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from openfollow.configuration import bootstrap_config_if_missing, load_config

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[1]
_EXAMPLE = _REPO_ROOT / "config.example.toml"
_SERVICE = _REPO_ROOT / "packaging" / "debian" / "openfollow.service"
_BUILD_DEB = _REPO_ROOT / "packaging" / "build-deb.sh"


def test_bootstrapping_the_shipped_example_yields_stage_and_default_marker(tmp_path) -> None:
    """Placing the real example in the config dir and booting with no config.toml
    must produce the out-of-box experience: stage test pattern + Marker 1 visible.

    This is the contract a fresh image relies on – exercised against the actual
    ``config.example.toml`` so a future edit that drops the stage pattern or the
    default marker from the example is caught here, not on a freshly-flashed Pi.
    """
    config_path = tmp_path / "config.toml"
    shutil.copy(_EXAMPLE, tmp_path / "config.example.toml")

    assert bootstrap_config_if_missing(str(config_path)) is True
    assert config_path.exists()

    cfg = load_config(str(config_path))
    assert cfg.video_source_type == "testpattern"
    assert cfg.testpattern_selected_media == "default:stage"
    # init_markers() registers one marker per controlled id; a non-empty list is
    # what makes Marker 1 appear at its default position on first launch.
    assert cfg.controlled_marker_ids == [1]
    assert cfg.viewer_marker_ids == [1]


def _service_working_directory() -> str:
    for line in _SERVICE.read_text(encoding="utf-8").splitlines():
        if line.startswith("WorkingDirectory="):
            return line.split("=", 1)[1].strip()
    raise AssertionError("openfollow.service has no WorkingDirectory")


def _example_install_destinations() -> list[str]:
    """Destination paths (under the deb staging root) that build-deb.sh installs
    ``config.example.toml`` to. Strips the ``$STAGE`` staging prefix."""
    text = _BUILD_DEB.read_text(encoding="utf-8")
    dests: list[str] = []
    for m in re.finditer(r'install\b[^\n]*config\.example\.toml"\s+"([^"]+)"', text):
        dest = m.group(1).replace("$STAGE", "").replace("${STAGE}", "")
        dests.append(dest)
    return dests


def test_deb_ships_example_into_service_working_directory() -> None:
    """The deb must install the example into the dir bootstrap searches – the
    service WorkingDirectory – or a fresh image never seeds its config."""
    workdir = _service_working_directory()
    dests = _example_install_destinations()
    expected = f"{workdir}/config.example.toml"
    assert expected in dests, (
        f"build-deb.sh must install config.example.toml into the service "
        f"WorkingDirectory {workdir!r} so first-boot bootstrap finds it; "
        f"found destinations: {dests}"
    )
