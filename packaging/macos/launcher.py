# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Entry point baked into the macOS .app bundle.

A single frozen binary serves three roles, dispatched on argv / env so the GUI,
the in-app model export, and the build-time self-check all run from one bundle:

- ``--export <args>``: re-exec target for the web UI's model download. The
  frozen ``sys.executable`` is the app (not python), so the export route
  (``web/routes.py``) re-invokes the app with a leading ``--export``; we forward
  the rest to ``export_onnx`` in-process.
- ``OPENFOLLOW_SELFCHECK=1``: import the native stack (gi/Gst + detection deps)
  and verify the key GStreamer elements resolve, then exit. Used by the DMG
  build's post-bundle verifier without opening a window.
- default: seed a per-user config + the bundled default model on first run, then
  launch the GUI.

Imports are lazy so the module loads (and its seed logic stays unit-testable)
without GTK / GStreamer / torch present.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "OpenFollow"
SEED_CONFIG_NAME = "config.seed.toml"
DEFAULT_MODEL_NAME = "yolov8n.onnx"
STORAGE_PLACEHOLDER = "@STORAGE_PATH@"


def default_config_dir() -> Path:
    """Per-user, writable config home for the bundled app."""
    return Path.home() / "Library" / "Application Support" / APP_NAME


def resource_root() -> Path:
    """Directory holding bundled data (seed config, default model).

    PyInstaller unpacks ``datas`` under ``sys._MEIPASS``; from a source tree it
    is this file's directory.
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base)
    return Path(__file__).resolve().parent


def seed_user_data(config_dir: Path, resources: Path) -> Path:
    """Create the config dir and, on first run only, seed config + default model.

    Returns the config.toml path. Existing files are never overwritten, so an
    operator's edits survive across launches and reinstalls.
    """
    config_dir.mkdir(parents=True, exist_ok=True)
    storage_dir = config_dir / "yolo"
    config_path = config_dir / "config.toml"

    seed = resources / SEED_CONFIG_NAME
    if not config_path.exists() and seed.is_file():
        text = seed.read_text(encoding="utf-8").replace(STORAGE_PLACEHOLDER, str(storage_dir))
        config_path.write_text(text, encoding="utf-8")

    model_src = resources / "models" / DEFAULT_MODEL_NAME
    if model_src.is_file():
        models_dir = storage_dir / "models"
        models_dir.mkdir(parents=True, exist_ok=True)
        model_dst = models_dir / DEFAULT_MODEL_NAME
        if not model_dst.exists():
            shutil.copyfile(model_src, model_dst)

    return config_path


def _export_workdir(export_args: list[str]) -> Path:
    """A writable working directory for the export.

    A Finder-launched .app runs with cwd ``/`` (read-only), and ultralytics
    downloads the .pt weights into cwd, so the export must run somewhere
    writable. Reuse the ``--output-dir`` the export route already passes (the
    writable ``<storage>/models`` dir) and return its parent (the storage root);
    fall back to the per-user storage dir when the flag is absent.
    """
    if "--output-dir" in export_args:
        idx = export_args.index("--output-dir")
        if idx + 1 < len(export_args):
            return Path(export_args[idx + 1]).expanduser().parent
    return default_config_dir() / "yolo"


def run_export(export_args: list[str]) -> int:
    """Run a YOLO->ONNX export in-process (the frozen re-exec target).

    Auto-install is disabled so a frozen ultralytics never shells out to pip;
    the bundle already carries onnx / onnxslim. The export runs from a writable
    storage dir because a Finder-launched .app's cwd is read-only.
    """
    os.environ.setdefault("YOLO_AUTOINSTALL", "false")

    workdir = _export_workdir(export_args)
    cache = workdir / "cache"
    for directory in (workdir, cache / "ultralytics", cache / "matplotlib"):
        directory.mkdir(parents=True, exist_ok=True)
    os.chdir(workdir)
    # ultralytics' settings file + matplotlib's font cache otherwise write to
    # paths that can be unwritable under a sandboxed launch; pin them in storage.
    os.environ.setdefault("YOLO_CONFIG_DIR", str(cache / "ultralytics"))
    os.environ.setdefault("MPLCONFIGDIR", str(cache / "matplotlib"))
    os.environ.setdefault("XDG_CACHE_HOME", str(cache))

    import export_onnx  # bundled as a hidden module (scripts/export_onnx.py)

    sys.argv = [export_onnx.__name__, *export_args]
    try:
        export_onnx.main()
    except SystemExit as exc:  # argparse / explicit exits
        return int(exc.code or 0)
    except Exception:  # noqa: BLE001 - surface a non-zero code to the caller
        import traceback

        traceback.print_exc()
        return 1
    return 0


def run_selfcheck() -> int:
    """Verify the bundled native stack resolves; print OK / FAIL, return 0 / 1."""
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        required = ("gtksink", "videoconvert", "videotestsrc", "avfvideosrc")
        missing = [name for name in required if Gst.ElementFactory.find(name) is None]
        if missing:
            print(f"FAIL: missing GStreamer elements: {', '.join(missing)}")
            return 1

        # Video input plugins are discovered by walking the package at runtime;
        # PyInstaller can miss the dynamically imported ones (see openfollow.spec).
        # Assert the registry resolves the full set, or the GUI crash-loops on the
        # first init_video with "Unknown video input type".
        from openfollow.video.inputs import get_registry

        registry = get_registry()
        expected_inputs = {"testpattern", "ndi", "srt", "rtsp", "rtp", "picam", "v4l2", "avf"}
        missing_inputs = expected_inputs - registry.keys()
        if missing_inputs:
            print(f"FAIL: missing video input plugins: {', '.join(sorted(missing_inputs))}")
            return 1

        import importlib

        for mod in ("onnxruntime", "cv2", "ultralytics"):
            importlib.import_module(mod)

        # Bottle templates import these openfollow submodules at render time
        # (`% from openfollow.<mod> import ...`); a missing one is invisible until
        # the first web request 500s. Import them here so the build catches it.
        template_mods = (
            "openfollow.web.labels",
            "openfollow.web.routes",
            "openfollow.palette",
            "openfollow.units",
            "openfollow.osc.parser",
            "openfollow.osc.template",
        )
        for mod in template_mods:
            importlib.import_module(mod)
    except Exception as exc:  # noqa: BLE001 - the self-check reports, never raises
        print(f"FAIL: {exc}")
        return 1
    print("OK")
    return 0


def run_gui() -> int:
    """Seed per-user data, then hand off to the normal GUI entry point."""
    config_path = seed_user_data(default_config_dir(), resource_root())
    sys.argv = [sys.argv[0], str(config_path)]
    from openfollow.main import main

    main()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Dispatch on argv / env. Returns a process exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--export":
        return run_export(args[1:])
    if os.environ.get("OPENFOLLOW_SELFCHECK") == "1":
        return run_selfcheck()
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
