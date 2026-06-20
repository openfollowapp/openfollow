# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the offline .deb upload route's stream/cleanup helpers."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from openfollow.web.routes import _discard_staged, _stream_to_file

pytestmark = pytest.mark.unit


class TestStreamToFile:
    def test_streams_exact_bytes_across_chunks(self, tmp_path: Path) -> None:
        data = b"x" * (3 * 1024 * 1024 + 17)  # spans several 1 MiB chunks
        dest = tmp_path / "out.bin"
        _stream_to_file(io.BytesIO(data), str(dest), len(data), chunk=1024 * 1024)
        assert dest.read_bytes() == data

    def test_stops_at_total_even_if_more_available(self, tmp_path: Path) -> None:
        # Never read past the declared body length (defends against reading
        # into a following pipelined request).
        dest = tmp_path / "out.bin"
        _stream_to_file(io.BytesIO(b"abcdefghij"), str(dest), 4)
        assert dest.read_bytes() == b"abcd"

    def test_raises_on_truncated_stream(self, tmp_path: Path) -> None:
        dest = tmp_path / "out.bin"
        with pytest.raises(RuntimeError, match="truncated"):
            _stream_to_file(io.BytesIO(b"only5"), str(dest), 100)

    def test_stages_file_owner_only(self, tmp_path: Path) -> None:
        import stat

        dest = tmp_path / "staged.deb"
        _stream_to_file(io.BytesIO(b"deb-bytes"), str(dest), 9)
        assert stat.S_IMODE(dest.stat().st_mode) == 0o600


class TestDiscardStaged:
    def test_removes_file(self, tmp_path: Path) -> None:
        f = tmp_path / "staged.deb"
        f.write_bytes(b"x")
        _discard_staged(str(f))
        assert not f.exists()

    def test_tolerates_missing_path(self) -> None:
        _discard_staged("/tmp/does-not-exist-openfollow.deb")  # no raise
        _discard_staged(None)
