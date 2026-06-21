# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for the macOS .app bundle launcher (packaging/macos/launcher.py).

The launcher lives outside the importable ``openfollow`` package, so it is
loaded by file path. Its native/GUI paths are not exercised here (no gi/torch);
we cover the pure filesystem seed logic and the argv/env dispatch, which is what
the bundle relies on.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_LAUNCHER_PATH = Path(__file__).resolve().parents[1] / "packaging" / "macos" / "launcher.py"


def _load_launcher() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("openfollow_macos_launcher", _LAUNCHER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def launcher() -> types.ModuleType:
    return _load_launcher()


def _write_resources(root: Path, *, with_model: bool) -> Path:
    """Build a stand-in resource dir (what PyInstaller unpacks at runtime)."""
    (root / "config.seed.toml").write_text(
        'web_port = 8080\n\n[detection]\nstorage_path = "@STORAGE_PATH@"\n',
        encoding="utf-8",
    )
    if with_model:
        models = root / "models"
        models.mkdir()
        (models / "yolov8n.onnx").write_bytes(b"onnx-bytes")
    return root


# ---------------------------------------------------------------------------
# seed_user_data
# ---------------------------------------------------------------------------


def test_seed_user_data_first_run_seeds_config_and_model(launcher, tmp_path) -> None:
    resources = tmp_path / "res"
    resources.mkdir()
    _write_resources(resources, with_model=True)
    config_dir = tmp_path / "support"

    config_path = launcher.seed_user_data(config_dir, resources)

    assert config_path == config_dir / "config.toml"
    text = config_path.read_text(encoding="utf-8")
    # The storage placeholder is rewritten to the absolute per-user yolo dir.
    assert "@STORAGE_PATH@" not in text
    assert str(config_dir / "yolo") in text
    assert "web_port = 8080" in text
    # The bundled default model is copied into <storage>/models/.
    seeded = config_dir / "yolo" / "models" / "yolov8n.onnx"
    assert seeded.read_bytes() == b"onnx-bytes"


def test_seed_user_data_does_not_clobber_existing_files(launcher, tmp_path) -> None:
    resources = tmp_path / "res"
    resources.mkdir()
    _write_resources(resources, with_model=True)
    config_dir = tmp_path / "support"
    (config_dir / "yolo" / "models").mkdir(parents=True)
    (config_dir / "config.toml").write_text("web_port = 9999\n", encoding="utf-8")
    (config_dir / "yolo" / "models" / "yolov8n.onnx").write_bytes(b"user-model")

    launcher.seed_user_data(config_dir, resources)

    # Operator edits survive: neither the config nor the model is overwritten.
    assert (config_dir / "config.toml").read_text(encoding="utf-8") == "web_port = 9999\n"
    assert (config_dir / "yolo" / "models" / "yolov8n.onnx").read_bytes() == b"user-model"


def test_seed_user_data_without_bundled_model_still_seeds_config(launcher, tmp_path) -> None:
    resources = tmp_path / "res"
    resources.mkdir()
    _write_resources(resources, with_model=False)
    config_dir = tmp_path / "support"

    launcher.seed_user_data(config_dir, resources)

    assert (config_dir / "config.toml").is_file()
    assert not (config_dir / "yolo" / "models").exists()


def test_default_config_dir_under_home(launcher, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert launcher.default_config_dir() == tmp_path / "Library" / "Application Support" / "OpenFollow"


def test_resource_root_prefers_meipass(launcher, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(launcher.sys, "_MEIPASS", str(tmp_path), raising=False)
    assert launcher.resource_root() == tmp_path
    monkeypatch.delattr(launcher.sys, "_MEIPASS", raising=False)
    assert launcher.resource_root() == _LAUNCHER_PATH.parent


# ---------------------------------------------------------------------------
# dispatch: main()
# ---------------------------------------------------------------------------


def test_main_routes_export(launcher, monkeypatch) -> None:
    captured: list[list[str]] = []
    monkeypatch.setattr(launcher, "run_export", lambda args: captured.append(args) or 7)
    monkeypatch.setattr(launcher, "run_gui", lambda: pytest.fail("GUI must not run for --export"))

    rc = launcher.main(["--export", "yolov8n.pt", "--imgsz", "320"])

    assert rc == 7
    assert captured == [["yolov8n.pt", "--imgsz", "320"]]


def test_main_routes_selfcheck(launcher, monkeypatch) -> None:
    monkeypatch.setenv("OPENFOLLOW_SELFCHECK", "1")
    monkeypatch.setattr(launcher, "run_selfcheck", lambda: 0)
    monkeypatch.setattr(launcher, "run_gui", lambda: pytest.fail("GUI must not run for selfcheck"))

    assert launcher.main([]) == 0


def test_main_routes_gui_by_default(launcher, monkeypatch) -> None:
    monkeypatch.delenv("OPENFOLLOW_SELFCHECK", raising=False)
    monkeypatch.setattr(launcher, "run_gui", lambda: 0)
    monkeypatch.setattr(launcher, "run_export", lambda args: pytest.fail("export must not run by default"))

    assert launcher.main([]) == 0


# ---------------------------------------------------------------------------
# _export_workdir
# ---------------------------------------------------------------------------


def test_export_workdir_uses_output_dir_parent(launcher, tmp_path) -> None:
    models = tmp_path / "yolo" / "models"
    args = ["m.pt", "--imgsz", "320", "--output-dir", str(models)]
    # .pt downloads land in the storage root (parent of the .onnx output dir).
    assert launcher._export_workdir(args) == tmp_path / "yolo"


def test_export_workdir_falls_back_to_storage(launcher, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(launcher, "default_config_dir", lambda: tmp_path)
    assert launcher._export_workdir(["m.pt"]) == tmp_path / "yolo"


# ---------------------------------------------------------------------------
# run_export
# ---------------------------------------------------------------------------


def _install_fake_export(monkeypatch, main_fn) -> list[list[str]]:
    seen_argv: list[list[str]] = []
    fake = types.ModuleType("export_onnx")

    def _main() -> None:
        seen_argv.append(list(sys.argv))
        main_fn()

    fake.main = _main  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "export_onnx", fake)
    return seen_argv


def _prep_run_export(launcher, monkeypatch, tmp_path) -> list[str]:
    """Make run_export side-effect-safe: capture chdir, point the fallback
    workdir into tmp, and clear the cache env vars (monkeypatch restores them)."""
    chdirs: list[str] = []
    monkeypatch.setattr(launcher.os, "chdir", lambda p: chdirs.append(str(p)))
    monkeypatch.setattr(launcher, "default_config_dir", lambda: tmp_path)
    for var in ("YOLO_CONFIG_DIR", "MPLCONFIGDIR", "XDG_CACHE_HOME"):
        monkeypatch.delenv(var, raising=False)
    return chdirs


def test_run_export_sets_argv_and_disables_autoinstall(launcher, monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("YOLO_AUTOINSTALL", raising=False)
    monkeypatch.setattr(sys, "argv", ["frozen"], raising=False)
    _prep_run_export(launcher, monkeypatch, tmp_path)
    seen = _install_fake_export(monkeypatch, lambda: None)

    rc = launcher.run_export(["yolov8n.pt", "--imgsz", "320"])

    assert rc == 0
    assert seen == [["export_onnx", "yolov8n.pt", "--imgsz", "320"]]
    assert os.environ["YOLO_AUTOINSTALL"] == "false"


def test_run_export_chdirs_to_writable_storage_and_pins_caches(launcher, monkeypatch, tmp_path) -> None:
    models = tmp_path / "yolo" / "models"
    chdirs = _prep_run_export(launcher, monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["frozen"], raising=False)
    seen = _install_fake_export(monkeypatch, lambda: None)

    rc = launcher.run_export(["m.pt", "--imgsz", "320", "--output-dir", str(models)])

    assert rc == 0
    # Ran from the writable storage root (parent of --output-dir), not the
    # read-only cwd a Finder-launched .app inherits.
    assert chdirs == [str(tmp_path / "yolo")]
    cache = tmp_path / "yolo" / "cache"
    assert os.environ["YOLO_CONFIG_DIR"] == str(cache / "ultralytics")
    assert os.environ["MPLCONFIGDIR"] == str(cache / "matplotlib")
    assert os.environ["XDG_CACHE_HOME"] == str(cache)
    assert (cache / "ultralytics").is_dir()
    assert (cache / "matplotlib").is_dir()
    assert seen == [["export_onnx", "m.pt", "--imgsz", "320", "--output-dir", str(models)]]


def test_run_export_propagates_systemexit_code(launcher, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sys, "argv", ["frozen"], raising=False)
    _prep_run_export(launcher, monkeypatch, tmp_path)

    def _exit() -> None:
        raise SystemExit(3)

    _install_fake_export(monkeypatch, _exit)
    assert launcher.run_export(["m.pt"]) == 3


def test_run_export_returns_one_on_failure(launcher, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sys, "argv", ["frozen"], raising=False)
    _prep_run_export(launcher, monkeypatch, tmp_path)

    def _boom() -> None:
        raise RuntimeError("export blew up")

    _install_fake_export(monkeypatch, _boom)
    assert launcher.run_export(["m.pt"]) == 1
