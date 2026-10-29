# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""What a restore holds until its restart. Every test holds paths of its own, under its own ``tmp_path``."""

from __future__ import annotations

from pathlib import Path

import pytest

from openfollow.restore_hold import HeldForRestartError, hold, is_held, unless_held

pytestmark = pytest.mark.unit


def test_a_held_file_and_everything_in_a_held_folder_are_refused(tmp_path: Path) -> None:
    hold(tmp_path / "config.toml", tmp_path / "lens-calibrations")
    for path in ("config.toml", "lens-calibrations", "lens-calibrations/zoom.json", "lens-calibrations/a/b"):
        assert is_held(tmp_path / path), path
        with pytest.raises(HeldForRestartError), unless_held(tmp_path / path):
            pytest.fail("written while held")


@pytest.mark.parametrize("path", ["config.toml.bak", "lens-calibrations-2/a.json", "markers.toml", "."])
def test_what_a_restore_did_not_write_is_written(tmp_path: Path, path: str) -> None:
    hold(tmp_path / "config.toml", tmp_path / "lens-calibrations")
    written = []
    with unless_held(tmp_path / path):
        written.append(path)
    assert (is_held(tmp_path / path), written) == (False, [path])


def test_a_held_path_is_matched_through_a_link(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    hold(tmp_path / "link")
    assert is_held(tmp_path / "real" / "a.json")
    assert is_held(tmp_path / "link" / "a.json")
