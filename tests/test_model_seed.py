# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for the bundled-model seeder (openfollow/model_seed.py).

The seeder copies pre-shipped YOLO ``.onnx`` tiers into the per-device storage
folder on startup so detection works offline out of the box. These tests pin the
copy / no-overwrite / no-op contract and the packaged-dir resolution.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import openfollow.model_seed as model_seed
from openfollow.model_seed import bundled_models_dir, seed_bundled_models

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# seed_bundled_models
# ---------------------------------------------------------------------------


def test_seed_copies_every_missing_onnx(tmp_path: Path) -> None:
    source = tmp_path / "bundled"
    source.mkdir()
    (source / "yolo26n.onnx").write_bytes(b"nano")
    (source / "yolo11s.onnx").write_bytes(b"small")
    (source / "yolo11m.onnx").write_bytes(b"medium")
    dest = tmp_path / "storage" / "models"  # does not exist yet

    seeded = seed_bundled_models(source, dest)

    # The destination tree is created and every tier lands with its bytes.
    assert (dest / "yolo26n.onnx").read_bytes() == b"nano"
    assert (dest / "yolo11s.onnx").read_bytes() == b"small"
    assert (dest / "yolo11m.onnx").read_bytes() == b"medium"
    assert sorted(seeded) == ["yolo11m.onnx", "yolo11s.onnx", "yolo26n.onnx"]


def test_seed_does_not_overwrite_existing_destination(tmp_path: Path) -> None:
    source = tmp_path / "bundled"
    source.mkdir()
    (source / "yolo26n.onnx").write_bytes(b"bundled")
    (source / "yolo11s.onnx").write_bytes(b"bundled-s")
    dest = tmp_path / "models"
    dest.mkdir()
    (dest / "yolo26n.onnx").write_bytes(b"operator-edit")

    seeded = seed_bundled_models(source, dest)

    # The pre-existing file is preserved and excluded from the returned list;
    # only the genuinely-missing tier is copied + reported.
    assert (dest / "yolo26n.onnx").read_bytes() == b"operator-edit"
    assert "yolo26n.onnx" not in seeded
    assert seeded == ["yolo11s.onnx"]
    assert (dest / "yolo11s.onnx").read_bytes() == b"bundled-s"


def test_seed_returns_empty_when_models_dir_cannot_be_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # An unwritable storage location must degrade to a no-op, not crash startup.
    source = tmp_path / "bundled"
    source.mkdir()
    (source / "yolo26n.onnx").write_bytes(b"nano")
    dest = tmp_path / "storage" / "models"

    def _boom(*_a: object, **_k: object) -> None:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "mkdir", _boom)

    assert seed_bundled_models(source, dest) == []
    assert not dest.exists()


def test_seed_skips_files_that_fail_to_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A copy that fails (e.g. disk full) is logged and skipped; the file is not
    # reported as seeded and the rest of the run continues.
    source = tmp_path / "bundled"
    source.mkdir()
    (source / "yolo26n.onnx").write_bytes(b"nano")
    dest = tmp_path / "models"

    def _boom(*_a: object, **_k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(model_seed.shutil, "copyfile", _boom)

    assert seed_bundled_models(source, dest) == []
    assert not (dest / "yolo26n.onnx").exists()


def test_seed_ignores_non_onnx_files(tmp_path: Path) -> None:
    source = tmp_path / "bundled"
    source.mkdir()
    (source / "yolo26n.onnx").write_bytes(b"nano")
    (source / "readme.txt").write_bytes(b"docs")
    (source / "weights.pt").write_bytes(b"torch")
    dest = tmp_path / "models"

    seeded = seed_bundled_models(source, dest)

    assert seeded == ["yolo26n.onnx"]
    assert not (dest / "readme.txt").exists()
    assert not (dest / "weights.pt").exists()


def test_seed_no_op_when_source_missing(tmp_path: Path) -> None:
    source = tmp_path / "does-not-exist"
    dest = tmp_path / "models"

    seeded = seed_bundled_models(source, dest)

    # A source checkout ships no bundled models: nothing copied, dest untouched.
    assert seeded == []
    assert not dest.exists()


# ---------------------------------------------------------------------------
# bundled_models_dir
# ---------------------------------------------------------------------------


def test_bundled_models_dir_returns_packaged_dir_when_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    packaged = tmp_path / "share" / "openfollow" / "models"
    packaged.mkdir(parents=True)
    monkeypatch.setattr(model_seed, "_PACKAGED_MODELS_DIR", packaged)

    assert bundled_models_dir() == packaged


def test_bundled_models_dir_returns_none_when_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "share" / "openfollow" / "models"  # never created
    monkeypatch.setattr(model_seed, "_PACKAGED_MODELS_DIR", missing)

    assert bundled_models_dir() is None
