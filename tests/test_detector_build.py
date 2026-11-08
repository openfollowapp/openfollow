# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""A deferred detector build: latest request wins, cancel drops it."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from openfollow.configuration import DetectionConfig
from openfollow.runtime.detector_build import DetectorBuild, needs_compile
from openfollow.video import coreml_compile

pytestmark = pytest.mark.unit


def _cfg(tmp_path: Path, model: str = "yolo26n.onnx") -> DetectionConfig:
    models = tmp_path / "models"
    models.mkdir(parents=True, exist_ok=True)
    (models / model).write_bytes(b"model")
    return DetectionConfig(enabled=True, model=model, storage_path=str(tmp_path))


def _wait_for(condition: Callable[[], bool]) -> bool:
    done = threading.Event()
    for _ in range(200):
        if condition():
            return True
        done.wait(0.01)
    return False


class _GatedCompile:
    """A compile that waits for ``release`` and records each model it compiled."""

    def __init__(self) -> None:
        self.models: list[str] = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.cut_short = 0

    def __call__(self, model_path: str, _cache_root: Path, cancelled: Callable[[], bool]) -> bool:
        self.models.append(Path(model_path).name)
        self.started.set()
        while not self.release.wait(0.01):
            if cancelled():
                self.cut_short += 1
                return False
        return True


def test_ready_once_the_compile_is_done(tmp_path: Path) -> None:
    compile_model = _GatedCompile()
    cfg = _cfg(tmp_path)
    build = DetectorBuild(cfg, compile_model)
    assert compile_model.started.wait(2)
    assert build.ready() is None

    compile_model.release.set()
    build.join(2)

    assert build.ready() is cfg
    assert compile_model.models == ["yolo26n.onnx"]


def test_request_for_another_model_cuts_the_compile_short(tmp_path: Path) -> None:
    compile_model = _GatedCompile()
    build = DetectorBuild(_cfg(tmp_path), compile_model)
    assert compile_model.started.wait(2)

    latest = _cfg(tmp_path, "yolo26m.onnx")
    build.update(latest)
    assert _wait_for(lambda: compile_model.models == ["yolo26n.onnx", "yolo26m.onnx"])
    compile_model.release.set()
    build.join(2)

    assert build.ready() is latest
    assert compile_model.cut_short == 1


def test_request_for_the_same_model_lets_the_compile_finish(tmp_path: Path) -> None:
    compile_model = _GatedCompile()
    build = DetectorBuild(_cfg(tmp_path), compile_model)
    assert compile_model.started.wait(2)

    latest = replace(_cfg(tmp_path), inference_size=640)
    build.update(latest)
    compile_model.release.set()
    build.join(2)

    assert build.ready() is latest
    assert compile_model.models == ["yolo26n.onnx", "yolo26n.onnx"]
    assert compile_model.cut_short == 0


def test_cancel_drops_the_build(tmp_path: Path) -> None:
    compile_model = _GatedCompile()
    build = DetectorBuild(_cfg(tmp_path), compile_model)
    assert compile_model.started.wait(2)

    build.cancel()
    build.join(2)

    assert build.ready() is None


def test_unusable_storage_is_left_to_the_detector(tmp_path: Path) -> None:
    compile_model = _GatedCompile()
    cfg = replace(_cfg(tmp_path), storage_path="relative/path")
    build = DetectorBuild(cfg, compile_model)
    build.join(2)

    assert build.ready() is cfg
    assert compile_model.models == []


def test_raising_compile_still_finishes(tmp_path: Path) -> None:
    def _broken(*_args: object) -> bool:
        raise RuntimeError("boom")

    cfg = _cfg(tmp_path)
    build = DetectorBuild(cfg, _broken)
    build.join(2)

    assert build.ready() is cfg


class TestNeedsCompile:
    def test_host_without_coreml_never_compiles(self, tmp_path: Path) -> None:
        assert needs_compile(_cfg(tmp_path)) is False

    def test_coreml_host_compiles_an_uncached_model(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(coreml_compile, "coreml_available", lambda: True)
        seen: list[tuple[str, Path]] = []
        monkeypatch.setattr(coreml_compile, "compile_needed", lambda m, c: seen.append((m, c)) or True)

        assert needs_compile(_cfg(tmp_path)) is True
        assert seen == [(str(tmp_path / "models" / "yolo26n.onnx"), tmp_path / "cache")]

    def test_unusable_storage_is_left_to_the_detector(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(coreml_compile, "coreml_available", lambda: True)
        cfg = replace(_cfg(tmp_path), storage_path="relative/path")
        assert needs_compile(cfg) is False
