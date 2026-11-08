# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The CoreML compile child: when it runs, how it is waited for and cut short,
and what it leaves in the compiled-model cache."""

from __future__ import annotations

import logging
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from openfollow.video import coreml_compile
from openfollow.video.detection import _COMPILED_MARKER, _coreml_cache_dir, _coreml_cache_path

pytestmark = pytest.mark.unit

_COREML_HOST = ["CoreMLExecutionProvider", "CPUExecutionProvider"]
_VERSION = "1.24.3"
_real_coreml_available = coreml_compile.coreml_available


class _FakeSession:
    def get_providers(self) -> list[str]:
        return _COREML_HOST

    def get_inputs(self) -> list[Any]:
        return [types.SimpleNamespace(name="images", shape=[1, 3, 320, 320])]

    def get_outputs(self) -> list[Any]:
        return [types.SimpleNamespace(name="output0", shape=[1, 84, 2100])]


def _install_ort(
    monkeypatch: pytest.MonkeyPatch,
    providers: list[str] | None = None,
    session: Any = None,
) -> None:
    fake = types.ModuleType("onnxruntime")
    fake.__version__ = _VERSION  # type: ignore[attr-defined]
    fake.SessionOptions = type("SessionOptions", (), {"intra_op_num_threads": 0})  # type: ignore[attr-defined]
    fake.get_available_providers = lambda: list(providers or _COREML_HOST)  # type: ignore[attr-defined]
    fake.InferenceSession = session or (lambda *_a, **_k: _FakeSession())  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "onnxruntime", fake)
    monkeypatch.setattr(coreml_compile, "coreml_available", _real_coreml_available)


def _model(tmp_path: Path) -> Path:
    path = tmp_path / "models" / "yolo26n.onnx"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"model")
    return path


def _mark_compiled(cache_root: Path, model: Path) -> None:
    cache_dir = _coreml_cache_path(cache_root, str(model), _VERSION)
    assert cache_dir is not None
    cache_dir.mkdir(parents=True)
    (cache_dir / _COMPILED_MARKER).touch()


class _FakeChild:
    """A ``Popen`` stand-in that finishes after ``polls`` waits."""

    instances: list[_FakeChild] = []

    def __init__(self, argv: list[str], *, polls: int = 0, returncode: int = 0, stderr: str = "", on_exit: Any = None):
        self.argv = argv
        self._polls = polls
        self.returncode: int | None = None
        self._exit_code = returncode
        self._stderr = stderr
        self._on_exit = on_exit
        self.killed = False
        _FakeChild.instances.append(self)

    def communicate(self, timeout: float | None = None) -> tuple[None, str]:
        if self.killed:
            self.returncode = -9
            return None, ""
        if self._polls > 0:
            self._polls -= 1
            raise subprocess.TimeoutExpired(self.argv, timeout or 0)
        if self._on_exit is not None:
            self._on_exit()
        self.returncode = self._exit_code
        return None, self._stderr

    def kill(self) -> None:
        self.killed = True


def _install_child(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> list[_FakeChild]:
    _FakeChild.instances = []
    monkeypatch.setattr(coreml_compile.subprocess, "Popen", lambda argv, **_k: _FakeChild(argv, **kwargs))
    return _FakeChild.instances


class TestCompileNeeded:
    def test_cpu_only_host_never_compiles(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _install_ort(monkeypatch, providers=["CPUExecutionProvider"])
        assert coreml_compile.compile_needed(str(_model(tmp_path)), tmp_path / "cache") is False

    def test_host_without_onnxruntime_never_compiles(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setitem(sys.modules, "onnxruntime", None)
        monkeypatch.setattr(coreml_compile, "coreml_available", _real_coreml_available)
        assert coreml_compile.compile_needed(str(_model(tmp_path)), tmp_path / "cache") is False

    def test_missing_model_is_left_to_the_build(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _install_ort(monkeypatch)
        assert coreml_compile.compile_needed(str(tmp_path / "absent.onnx"), tmp_path / "cache") is False

    def test_coreml_host_compiles_an_uncached_model(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _install_ort(monkeypatch)
        assert coreml_compile.compile_needed(str(_model(tmp_path)), tmp_path / "cache") is True

    def test_finished_compile_is_loaded_not_recompiled(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _install_ort(monkeypatch)
        model = _model(tmp_path)
        _mark_compiled(tmp_path / "cache", model)
        assert coreml_compile.compile_needed(str(model), tmp_path / "cache") is False

    def test_compile_cut_short_is_compiled_again(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _install_ort(monkeypatch)
        model = _model(tmp_path)
        cache_dir = _coreml_cache_path(tmp_path / "cache", str(model), _VERSION)
        assert cache_dir is not None
        cache_dir.mkdir(parents=True)
        (cache_dir / "compiled_model.mlmodelc").write_bytes(b"truncated")
        assert coreml_compile.compile_needed(str(model), tmp_path / "cache") is True


def test_cache_dir_clears_a_compile_cut_short(tmp_path: Path) -> None:
    model = _model(tmp_path)
    cache_dir = _coreml_cache_path(tmp_path / "cache", str(model), _VERSION)
    assert cache_dir is not None
    cache_dir.mkdir(parents=True)
    (cache_dir / "compiled_model.mlmodelc").write_bytes(b"truncated")

    assert _coreml_cache_dir(tmp_path / "cache", str(model), _VERSION) == (cache_dir, False)
    assert list(cache_dir.iterdir()) == []


@pytest.mark.parametrize(
    ("frozen", "expected_head"),
    [
        (True, ["--coreml-compile"]),
        (False, ["-m", "openfollow.video.coreml_compile"]),
    ],
)
def test_child_argv_per_launch_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, frozen: bool, expected_head: list[str]
) -> None:
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    argv = coreml_compile.child_argv("/models/m.onnx", tmp_path)
    assert argv == [sys.executable, *expected_head, "/models/m.onnx", str(tmp_path)]


class TestPrecompile:
    def test_no_child_when_no_compile_is_needed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _install_ort(monkeypatch, providers=["CPUExecutionProvider"])
        children = _install_child(monkeypatch)
        assert coreml_compile.precompile(str(_model(tmp_path)), tmp_path / "cache", lambda: False) is True
        assert children == []

    def test_child_that_fills_the_cache_succeeds(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _install_ort(monkeypatch)
        model = _model(tmp_path)
        cache_root = tmp_path / "cache"
        children = _install_child(monkeypatch, polls=3, on_exit=lambda: _mark_compiled(cache_root, model))

        assert coreml_compile.precompile(str(model), cache_root, lambda: False) is True
        assert len(children) == 1
        assert children[0].argv[-2:] == [str(model), str(cache_root)]
        assert coreml_compile.compile_needed(str(model), cache_root) is False

    # Exit 0 without a compile: the child fell back to an uncached build.
    @pytest.mark.parametrize("returncode", [1, 0, -11])
    def test_child_that_leaves_no_compile_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        returncode: int,
    ) -> None:
        _install_ort(monkeypatch)
        _install_child(monkeypatch, returncode=returncode, stderr="line1\nCoreML refused the model\n")

        with caplog.at_level(logging.WARNING, logger=coreml_compile.__name__):
            assert coreml_compile.precompile(str(_model(tmp_path)), tmp_path / "cache", lambda: False) is False

        assert any("CoreML refused the model" in r.getMessage() for r in caplog.records)

    def test_cancel_kills_the_child(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _install_ort(monkeypatch)
        children = _install_child(monkeypatch, polls=1000)
        polls = iter([False, False, True])

        assert coreml_compile.precompile(str(_model(tmp_path)), tmp_path / "cache", lambda: next(polls)) is False
        assert children[0].killed is True

    def test_timeout_kills_the_child(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _install_ort(monkeypatch)
        children = _install_child(monkeypatch, polls=1000)
        clock = iter([0.0, 1.0, coreml_compile._COMPILE_TIMEOUT_S + 1.0])
        monkeypatch.setattr(coreml_compile.time, "monotonic", lambda: next(clock))

        assert coreml_compile.precompile(str(_model(tmp_path)), tmp_path / "cache", lambda: False) is False
        assert children[0].killed is True

    def test_child_that_cannot_start_fails(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _install_ort(monkeypatch)

        def _refuse(*_a: object, **_k: object) -> object:
            raise OSError("exec format error")

        monkeypatch.setattr(coreml_compile.subprocess, "Popen", _refuse)
        assert coreml_compile.precompile(str(_model(tmp_path)), tmp_path / "cache", lambda: False) is False

    def test_waits_for_another_compile_to_finish(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _install_ort(monkeypatch, providers=["CPUExecutionProvider"])
        coreml_compile._compile_lock.acquire()
        waits: list[bool] = []

        def _release_after_one_wait() -> bool:
            waits.append(False)
            coreml_compile._compile_lock.release()
            return False

        assert coreml_compile.precompile(str(_model(tmp_path)), tmp_path / "cache", _release_after_one_wait) is True
        assert waits == [False]

    def test_cancel_while_another_compile_runs(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        children = _install_child(monkeypatch)
        coreml_compile._compile_lock.acquire()
        try:
            assert coreml_compile.precompile(str(_model(tmp_path)), tmp_path / "cache", lambda: True) is False
        finally:
            coreml_compile._compile_lock.release()
        assert children == []


class TestChildMain:
    def test_compile_fills_the_cache(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _install_ort(monkeypatch)
        model = _model(tmp_path)

        assert coreml_compile.main([str(model), str(tmp_path / "cache")]) == 0
        assert coreml_compile.compile_needed(str(model), tmp_path / "cache") is False

    def test_failed_build_exits_nonzero(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        def _unusable(*_a: object, **_k: object) -> object:
            raise RuntimeError("not an ONNX model")

        _install_ort(monkeypatch, session=_unusable)
        assert coreml_compile.main([str(_model(tmp_path)), str(tmp_path / "cache")]) == 1

    @pytest.mark.parametrize("argv", [[], ["only-model.onnx"], ["a", "b", "c"]])
    def test_wrong_arguments_exit_with_usage(self, argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
        assert coreml_compile.main(argv) == 2
        assert "usage" in capsys.readouterr().err


class TestDetectionHelpers:
    @staticmethod
    def _cfg(tmp_path: Path, storage: str | None = None) -> Any:
        from openfollow.configuration import DetectionConfig

        _model(tmp_path)
        return DetectionConfig(enabled=True, model="yolo26n.onnx", storage_path=storage or str(tmp_path))

    def test_host_without_coreml_needs_no_compile(self, tmp_path: Path) -> None:
        cfg = self._cfg(tmp_path)
        assert coreml_compile.detection_compile_needed(cfg) is False
        assert coreml_compile.precompile_detection(cfg, lambda: False) is True

    def test_coreml_host_compiles_the_resolved_model(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _install_ort(monkeypatch)
        cfg = self._cfg(tmp_path)
        seen: list[tuple[str, Path]] = []
        monkeypatch.setattr(coreml_compile, "precompile", lambda m, c, _cancel: seen.append((m, c)) or True)

        assert coreml_compile.detection_compile_needed(cfg) is True
        assert coreml_compile.precompile_detection(cfg, lambda: False) is True
        assert seen == [(str(tmp_path / "models" / "yolo26n.onnx"), tmp_path / "cache")]

    def test_unusable_storage_is_left_to_the_detector(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _install_ort(monkeypatch)
        cfg = self._cfg(tmp_path, storage="relative/path")
        assert coreml_compile.detection_compile_needed(cfg) is False
        assert coreml_compile.precompile_detection(cfg, lambda: False) is True


@pytest.mark.parametrize("busy", [False, True])
def test_wait_idle(busy: bool) -> None:
    if busy:
        coreml_compile._compile_lock.acquire()
    try:
        assert coreml_compile.wait_idle(0.01) is not busy
    finally:
        if busy:
            coreml_compile._compile_lock.release()
