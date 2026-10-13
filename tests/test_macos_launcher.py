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


def _write_resources(root: Path, *, models: dict[str, bytes] | None = None) -> Path:
    """Build a stand-in resource dir (what PyInstaller unpacks at runtime).

    ``models`` maps ``<name>.onnx`` -> bytes; ``None`` ships no ``models/`` dir.
    """
    (root / "config.seed.toml").write_text(
        'web_port = 8080\n\n[detection]\nstorage_path = "@STORAGE_PATH@"\n',
        encoding="utf-8",
    )
    if models is not None:
        models_dir = root / "models"
        models_dir.mkdir()
        for name, payload in models.items():
            (models_dir / name).write_bytes(payload)
    return root


# ---------------------------------------------------------------------------
# seed_user_data
# ---------------------------------------------------------------------------


def test_seed_user_data_first_run_seeds_config_and_every_model(launcher, tmp_path) -> None:
    resources = tmp_path / "res"
    resources.mkdir()
    _write_resources(
        resources,
        models={
            "yolo26n.onnx": b"nano",
            "yolo11s.onnx": b"small",
            "yolo11m.onnx": b"medium",
            "notes.txt": b"ignore me",  # written into models/ but not an .onnx
        },
    )
    config_dir = tmp_path / "support"

    config_path = launcher.seed_user_data(config_dir, resources)

    assert config_path == config_dir / "config.toml"
    text = config_path.read_text(encoding="utf-8")
    # The storage placeholder is rewritten to the absolute per-user yolo dir.
    assert "@STORAGE_PATH@" not in text
    assert str(config_dir / "yolo") in text
    assert "web_port = 8080" in text
    # Every bundled .onnx tier is copied into <storage>/models/ verbatim.
    models_dir = config_dir / "yolo" / "models"
    assert (models_dir / "yolo26n.onnx").read_bytes() == b"nano"
    assert (models_dir / "yolo11s.onnx").read_bytes() == b"small"
    assert (models_dir / "yolo11m.onnx").read_bytes() == b"medium"
    # Non-.onnx resources are left behind.
    assert not (models_dir / "notes.txt").exists()


def test_seed_user_data_does_not_clobber_existing_files(launcher, tmp_path) -> None:
    resources = tmp_path / "res"
    resources.mkdir()
    _write_resources(resources, models={"yolo26n.onnx": b"bundled", "yolo11s.onnx": b"bundled-s"})
    config_dir = tmp_path / "support"
    (config_dir / "yolo" / "models").mkdir(parents=True)
    (config_dir / "config.toml").write_text("web_port = 9999\n", encoding="utf-8")
    # One model already present (an operator download); the other is new.
    (config_dir / "yolo" / "models" / "yolo26n.onnx").write_bytes(b"user-model")

    launcher.seed_user_data(config_dir, resources)

    # Operator edits survive: neither the config nor the pre-existing model is
    # overwritten, but the missing bundled tier is still seeded alongside it.
    assert (config_dir / "config.toml").read_text(encoding="utf-8") == "web_port = 9999\n"
    assert (config_dir / "yolo" / "models" / "yolo26n.onnx").read_bytes() == b"user-model"
    assert (config_dir / "yolo" / "models" / "yolo11s.onnx").read_bytes() == b"bundled-s"


def test_seed_user_data_without_bundled_models_still_seeds_config(launcher, tmp_path) -> None:
    resources = tmp_path / "res"
    resources.mkdir()
    _write_resources(resources, models=None)
    config_dir = tmp_path / "support"

    launcher.seed_user_data(config_dir, resources)

    # No resources/models dir → seeding models is a no-op, config still lands.
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


# ---------------------------------------------------------------------------
# run_selfcheck
# ---------------------------------------------------------------------------


def _install_fake_gst(
    monkeypatch,
    missing: set[str],
    *,
    plugins: frozenset[str] = frozenset({"typefindfunctions"}),
    namespaces: frozenset[str] = frozenset(),
    unloadable: frozenset[str] = frozenset({"Gtk", "Gdk"}),
    discoverer_error: str = "",
) -> None:
    """Stand in for gi / Gst, resolving every element except ``missing``.

    ``plugins`` are the GStreamer plugins the registry holds, ``namespaces``
    the typelibs gi reports as unavailable, ``unloadable`` the ones whose
    import fails. Gtk and Gdk fail by default: importing them opens the display.
    """

    def new_discoverer(timeout: int) -> object:
        if discoverer_error:
            raise RuntimeError(discoverer_error)
        return object()

    gst = types.SimpleNamespace(
        init=lambda argv: None,
        SECOND=1_000_000_000,
        ElementFactory=types.SimpleNamespace(find=lambda name: None if name in missing else object()),
        Registry=types.SimpleNamespace(
            get=lambda: types.SimpleNamespace(find_plugin=lambda name: object() if name in plugins else None)
        ),
    )
    repository = types.ModuleType("gi.repository")
    gi = types.ModuleType("gi")

    def require_version(namespace: str, version: str) -> None:
        if namespace in namespaces:
            raise ValueError(f"Namespace {namespace} not available")

    gi.require_version = require_version  # type: ignore[attr-defined]
    gi.repository = repository  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    for name, module in {
        "Gst": gst,
        "GstPbutils": types.SimpleNamespace(Discoverer=types.SimpleNamespace(new=new_discoverer)),
        "GstApp": types.SimpleNamespace(),
        "Gtk": types.SimpleNamespace(),
        "Gdk": types.SimpleNamespace(),
        "Rsvg": types.SimpleNamespace(),
    }.items():
        setattr(repository, name, module)
        monkeypatch.setitem(sys.modules, f"gi.repository.{name}", None if name in unloadable else module)


@pytest.mark.parametrize("element", _load_launcher().REQUIRED_GST_ELEMENTS)
def test_selfcheck_fails_when_an_element_the_app_builds_is_missing(launcher, monkeypatch, capsys, element) -> None:
    _install_fake_gst(monkeypatch, {element})

    assert launcher.run_selfcheck() == 1
    assert capsys.readouterr().out.strip() == f"FAIL: missing GStreamer elements: {element}"


def test_selfcheck_fails_without_the_typefinders_plugin(launcher, monkeypatch, capsys) -> None:
    # decodebin identifies streams and uploads through it; another plugin's typefinder must not stand in.
    _install_fake_gst(monkeypatch, set(), plugins=frozenset({"subparse"}))

    assert launcher.run_selfcheck() == 1
    assert capsys.readouterr().out.strip() == "FAIL: missing GStreamer plugin: typefindfunctions"


@pytest.mark.parametrize("namespace", [name for name, _version in _load_launcher().REQUIRED_TYPELIBS])
def test_selfcheck_fails_without_a_typelib_the_app_pins(launcher, monkeypatch, capsys, namespace) -> None:
    _install_fake_gst(monkeypatch, set(), namespaces=frozenset({namespace}))

    assert launcher.run_selfcheck() == 1
    assert capsys.readouterr().out.strip() == f"FAIL: Namespace {namespace} not available"


@pytest.mark.parametrize("namespace", ["Gst", "GstApp", "GstPbutils", "Rsvg"])
def test_selfcheck_fails_when_a_typelib_does_not_load(launcher, monkeypatch, capsys, namespace) -> None:
    _install_fake_gst(monkeypatch, set(), unloadable=frozenset({"Gtk", "Gdk", namespace}))

    assert launcher.run_selfcheck() == 1
    assert capsys.readouterr().out.strip() == f"FAIL: import of gi.repository.{namespace} halted; None in sys.modules"


def test_selfcheck_reaches_the_discoverer_without_importing_gtk(launcher, monkeypatch, capsys) -> None:
    # The fake leaves Gtk / Gdk unimportable, so reaching the Discoverer proves neither was imported.
    _install_fake_gst(monkeypatch, set(), discoverer_error="reached the discoverer")

    assert launcher.run_selfcheck() == 1
    assert capsys.readouterr().out.strip() == "FAIL: reached the discoverer"


def test_selfcheck_fails_when_the_discoverer_cannot_be_built(launcher, monkeypatch, capsys) -> None:
    # The typelib imports lazily; only building a Discoverer loads libgstpbutils.
    _install_fake_gst(monkeypatch, set(), discoverer_error="cannot load libgstpbutils")

    assert launcher.run_selfcheck() == 1
    assert capsys.readouterr().out.strip() == "FAIL: cannot load libgstpbutils"
