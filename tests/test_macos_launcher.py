# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for the macOS .app bundle launcher (packaging/macos/launcher.py).

The launcher lives outside the importable ``openfollow`` package, so it is
loaded by file path. Its native/GUI paths are not exercised here (no gi/torch);
we cover the pure filesystem seed logic and the argv/env dispatch, which is what
the bundle relies on.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
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


_HOMEBREW_TYPELIBS = "/opt/homebrew/lib/girepository-1.0"

# What the fake repository loads with a namespace, as "<namespace>-<version>".
_TYPELIB_DEPENDENCIES = {"GstPbutils": ("GstVideo-1.0", "Gst-1.0")}

# What PyGObject's overrides load on import, though no typelib depends on it.
_OVERRIDE_LOADS = {"GLib": "GLibUnix", "Gio": "GioUnix"}


class _OverrideImporter(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Imports ``gi.repository.GLib`` / ``Gio``, loading what their overrides load."""

    def __init__(self, loaded: set[str]) -> None:
        self._loaded = loaded

    def find_spec(self, fullname: str, path: object, target: object = None) -> importlib.machinery.ModuleSpec | None:
        name = fullname.removeprefix("gi.repository.")
        return importlib.util.spec_from_loader(fullname, self) if name in _OVERRIDE_LOADS else None

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> None:
        return None

    def exec_module(self, module: types.ModuleType) -> None:
        name = module.__name__.removeprefix("gi.repository.")
        self._loaded |= {name, _OVERRIDE_LOADS[name]}


def _install_fake_gst(
    monkeypatch,
    missing: set[str],
    *,
    plugins: frozenset[str] = frozenset({"typefindfunctions"}),
    namespaces: frozenset[str] = frozenset(),
    unloadable: frozenset[str] = frozenset({"Gtk", "Gdk"}),
    require_errors: dict[str, str] | None = None,
    typelib_dir: str = _HOMEBREW_TYPELIBS,
    typelib_dirs: dict[str, str] | None = None,
    discoverer_error: str = "",
) -> None:
    """Stand in for gi / Gst, resolving every element except ``missing``.

    ``plugins`` are the GStreamer plugins the registry holds, ``namespaces``
    the typelibs gi reports as unavailable, ``unloadable`` the ones whose
    import fails. Gtk and Gdk fail by default: importing them opens the display.
    ``require_errors`` maps a namespace to the error loading its typelib raises.
    Every typelib loads from ``typelib_dir`` unless ``typelib_dirs`` names another.
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
    gi = types.ModuleType("gi")
    gi.RepositoryError = type("RepositoryError", (Exception,), {})  # type: ignore[attr-defined]
    loaded: set[str] = set()

    def require(namespace: str, version: str) -> None:
        if namespace in (require_errors or {}):
            raise gi.RepositoryError((require_errors or {})[namespace])  # type: ignore[attr-defined]
        loaded.add(namespace)
        loaded.update(dependency.rsplit("-", 1)[0] for dependency in _TYPELIB_DEPENDENCIES.get(namespace, ()))

    def get_typelib_path(namespace: str) -> str:
        if namespace not in loaded:
            raise RuntimeError(f"Namespace '{namespace}' not loaded")
        return f"{(typelib_dirs or {}).get(namespace, typelib_dir)}/{namespace}.typelib"

    default_repository = types.SimpleNamespace(
        require=require,
        get_loaded_namespaces=lambda: sorted(loaded),
        get_typelib_path=get_typelib_path,
    )
    repository = types.ModuleType("gi.repository")
    repository.__path__ = []  # type: ignore[attr-defined]  # a package, so a missing name falls back to sys.modules

    def require_version(namespace: str, version: str) -> None:
        if namespace in namespaces:
            raise ValueError(f"Namespace {namespace} not available")

    gi.require_version = require_version  # type: ignore[attr-defined]
    gi.Repository = types.SimpleNamespace(get_default=lambda: default_repository)  # type: ignore[attr-defined]
    gi.repository = repository  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    for name in _OVERRIDE_LOADS:
        # Absent, so the import reaches the importer; the teardown restores the real module.
        monkeypatch.setitem(sys.modules, f"gi.repository.{name}", None)
        monkeypatch.delitem(sys.modules, f"gi.repository.{name}")
    monkeypatch.setattr(sys, "meta_path", [_OverrideImporter(loaded), *sys.meta_path])
    for name, module in {
        "Gst": gst,
        "GstPbutils": types.SimpleNamespace(Discoverer=types.SimpleNamespace(new=new_discoverer)),
        "GstApp": types.SimpleNamespace(),
        "Gtk": types.SimpleNamespace(),
        "Gdk": types.SimpleNamespace(),
        "Rsvg": types.SimpleNamespace(),
    }.items():
        if name in unloadable:
            monkeypatch.setitem(sys.modules, f"gi.repository.{name}", None)
        else:
            setattr(repository, name, module)
            monkeypatch.setitem(sys.modules, f"gi.repository.{name}", module)


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


@pytest.mark.parametrize(
    "namespace",
    [
        *(name for name, _version in _load_launcher().REQUIRED_TYPELIBS),
        pytest.param("GstVideo", id="dependency-of-GstPbutils"),
        pytest.param("GLibUnix", id="loaded-by-the-GLib-overrides"),
        pytest.param("GioUnix", id="loaded-by-the-Gio-overrides"),
    ],
)
def test_selfcheck_fails_on_a_typelib_outside_the_bundle(launcher, monkeypatch, capsys, tmp_path, namespace) -> None:
    # On the build host, libgirepository finds a typelib the freeze dropped in Homebrew.
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    _install_fake_gst(
        monkeypatch,
        set(),
        typelib_dir=str(tmp_path / "gi_typelibs"),
        typelib_dirs={namespace: _HOMEBREW_TYPELIBS},
    )

    assert launcher.run_selfcheck() == 1
    assert (
        capsys.readouterr().out.strip()
        == f"FAIL: typelibs from outside the bundle: {_HOMEBREW_TYPELIBS}/{namespace}.typelib"
    )


def test_selfcheck_fails_on_a_typelib_beside_the_bundle(launcher, monkeypatch, capsys, tmp_path) -> None:
    beside = tmp_path / "Frameworks-old" / "gi_typelibs"
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "Frameworks"), raising=False)
    _install_fake_gst(
        monkeypatch,
        set(),
        typelib_dir=str(tmp_path / "Frameworks" / "gi_typelibs"),
        typelib_dirs={"GstApp": str(beside)},
    )

    assert launcher.run_selfcheck() == 1
    assert capsys.readouterr().out.strip() == f"FAIL: typelibs from outside the bundle: {beside}/GstApp.typelib"


@pytest.mark.parametrize(
    ("namespace", "dependency"),
    [
        pytest.param("Gtk", "'Atk', version '1.0'", id="Gtk-without-Atk"),
        pytest.param("Gdk", "'HarfBuzz', version '0.0'", id="Gdk-without-HarfBuzz"),
    ],
)
def test_selfcheck_fails_when_a_display_typelib_does_not_load(
    launcher, monkeypatch, capsys, namespace, dependency
) -> None:
    # Gtk / Gdk are never imported, so only loading their typelibs finds a dependency the bundle lacks.
    error = f"Typelib file for namespace {dependency} not found"
    _install_fake_gst(monkeypatch, set(), require_errors={namespace: error})

    assert launcher.run_selfcheck() == 1
    assert capsys.readouterr().out.strip() == f"FAIL: {error}"


def test_selfcheck_takes_the_typelibs_linked_into_the_bundle(launcher, monkeypatch, capsys, tmp_path) -> None:
    # PyInstaller keeps them in Contents/Resources and links the directory into sys._MEIPASS.
    (tmp_path / "Resources" / "gi_typelibs").mkdir(parents=True)
    (tmp_path / "Frameworks").mkdir()
    (tmp_path / "Frameworks" / "gi_typelibs").symlink_to(Path("..") / "Resources" / "gi_typelibs")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "Frameworks"), raising=False)
    _install_fake_gst(
        monkeypatch,
        set(),
        typelib_dir=str(tmp_path / "Frameworks" / "gi_typelibs"),
        discoverer_error="reached the discoverer",
    )

    assert launcher.run_selfcheck() == 1
    assert capsys.readouterr().out.strip() == "FAIL: reached the discoverer"


def test_selfcheck_outside_a_bundle_takes_the_system_typelibs(launcher, monkeypatch, capsys) -> None:
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    _install_fake_gst(
        monkeypatch,
        set(),
        typelib_dir=_HOMEBREW_TYPELIBS,
        discoverer_error="reached the discoverer",
    )

    assert launcher.run_selfcheck() == 1
    assert capsys.readouterr().out.strip() == "FAIL: reached the discoverer"


def test_selfcheck_fails_when_the_discoverer_cannot_be_built(launcher, monkeypatch, capsys) -> None:
    # The typelib imports lazily; only building a Discoverer loads libgstpbutils.
    _install_fake_gst(monkeypatch, set(), discoverer_error="cannot load libgstpbutils")

    assert launcher.run_selfcheck() == 1
    assert capsys.readouterr().out.strip() == "FAIL: cannot load libgstpbutils"
