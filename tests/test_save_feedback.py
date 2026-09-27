# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Every script-driven save reports a failure the way an HTMX save does."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_PACKAGE = Path(__file__).resolve().parent.parent / "openfollow"
_WRITE = re.compile(r"method:\s*['\"](POST|PUT|DELETE|PATCH)['\"]")


def _sources_with_scripts() -> list[Path]:
    return sorted((_PACKAGE / "web" / "templates").rglob("*.tpl")) + sorted(
        (_PACKAGE / "video" / "inputs").glob("*.py")
    )


def test_every_file_that_saves_by_script_reports_failures_through_the_shared_helper() -> None:
    writers = [path for path in _sources_with_scripts() if _WRITE.search(path.read_text(encoding="utf-8"))]
    assert len(writers) >= 8, "the scan no longer finds the script-driven saves"
    silent = [path.name for path in writers if "saveError" not in path.read_text(encoding="utf-8")]
    assert silent == []
