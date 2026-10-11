# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Contract of the macOS app bundle, read from the build files.

PyInstaller only runs on a Mac with the Homebrew stack, so ``openfollow.spec``
and ``build-dmg.sh`` are read as AST and text here, and a missing document,
plugin, GStreamer element or minimum-version check fails ``make ci`` instead of
a DMG on a stage. The minimum-version checker and the self-check step
``build-dmg.sh`` runs are exercised on a stand-in ``.app``.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import plistlib
import shlex
import subprocess
import types
from pathlib import Path

import pytest

from openfollow.web import diagnostics as diagnostics_module
from openfollow.web import routes as routes_module

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[1]
_MACOS = _REPO / "packaging" / "macos"
_SPEC = _MACOS / "openfollow.spec"
_BUILD_DMG = _MACOS / "build-dmg.sh"


def _load(name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(f"openfollow_macos_{name}", _MACOS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def spec_tree() -> ast.Module:
    return ast.parse(_SPEC.read_text(encoding="utf-8"))


def _assigned(tree: ast.Module, name: str) -> ast.expr:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return node.value
    pytest.fail(f"openfollow.spec assigns no {name}")


def _info_plist_value(tree: ast.Module, key: str) -> object:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "BUNDLE":
            for keyword in node.keywords:
                if keyword.arg == "info_plist" and isinstance(keyword.value, ast.Dict):
                    for k, v in zip(keyword.value.keys, keyword.value.values, strict=True):
                        if isinstance(k, ast.Constant) and k.value == key:
                            return ast.literal_eval(v)
    pytest.fail(f"openfollow.spec's BUNDLE info_plist has no {key}")


# ---------------------------------------------------------------------------
# Documents the About page shows offline
# ---------------------------------------------------------------------------


def _about_page_documents(monkeypatch) -> set[str]:
    requested: set[str] = set()
    monkeypatch.setattr(routes_module, "_bundled_doc_path", lambda name: requested.add(name))
    routes_module._read_license_text()
    routes_module._third_party_notices_html()
    routes_module._written_offer_html()
    return requested


def test_every_document_the_about_page_reads_is_bundled(spec_tree, monkeypatch) -> None:
    bundled = set(ast.literal_eval(_assigned(spec_tree, "_ABOUT_DOCS")))
    needed = _about_page_documents(monkeypatch)
    assert needed == {"LICENSE", "THIRD_PARTY_NOTICES.md", "WRITTEN_OFFER.md"}
    assert needed <= bundled
    for name in bundled:
        assert (_REPO / name).is_file(), f"openfollow.spec bundles {name}, which the repo root does not have"


def test_documents_land_at_the_bundle_root(spec_tree) -> None:
    # _bundled_doc_path looks beside the openfollow package, which the bundle keeps at its root.
    additions = [
        node.value
        for node in ast.walk(spec_tree)
        if isinstance(node, ast.AugAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "datas"
        and isinstance(node.value, ast.ListComp)
        and any(isinstance(g.iter, ast.Name) and g.iter.id == "_ABOUT_DOCS" for g in node.value.generators)
    ]
    assert len(additions) == 1, "openfollow.spec must add _ABOUT_DOCS to datas exactly once"
    entry = additions[0].elt
    assert isinstance(entry, ast.Tuple)
    assert ast.literal_eval(entry.elts[1]) == "."


# ---------------------------------------------------------------------------
# GStreamer elements
# ---------------------------------------------------------------------------

_REQUIRED_GST_ELEMENTS = _load("launcher").REQUIRED_GST_ELEMENTS

# The plugin each required element lives in, as gst-inspect-1.0 reports it on
# Homebrew GStreamer 1.28.
_ELEMENT_PLUGINS = {
    "appsink": "libgstapp.dylib",
    "avdec_vp8": "libgstlibav.dylib",
    "avfvideosrc": "libgstapplemedia.dylib",
    "capsfilter": "libgstcoreelements.dylib",
    "decodebin": "libgstplayback.dylib",
    "filesrc": "libgstcoreelements.dylib",
    "gtksink": "libgstgtk.dylib",
    "h264parse": "libgstvideoparsersbad.dylib",
    "h265parse": "libgstvideoparsersbad.dylib",
    "imagefreeze": "libgstimagefreeze.dylib",
    "jpegdec": "libgstjpeg.dylib",
    "jpegenc": "libgstjpeg.dylib",
    "matroskademux": "libgstmatroska.dylib",
    "pngdec": "libgstpng.dylib",
    "queue": "libgstcoreelements.dylib",
    "rtph264depay": "libgstrtp.dylib",
    "rtph265depay": "libgstrtp.dylib",
    "rtpjitterbuffer": "libgstrtpmanager.dylib",
    "rtpmp2tdepay": "libgstrtp.dylib",
    "rtspsrc": "libgstrtsp.dylib",
    "srtsrc": "libgstsrt.dylib",
    "tee": "libgstcoreelements.dylib",
    "tsdemux": "libgstmpegtsdemux.dylib",
    "udpsrc": "libgstudp.dylib",
    "valve": "libgstcoreelements.dylib",
    "videoconvert": "libgstvideoconvertscale.dylib",
    "videoscale": "libgstvideoconvertscale.dylib",
    "videotestsrc": "libgstvideotestsrc.dylib",
    "webpdec": "libgstwebp.dylib",
}

# Inputs the app never runs: NDI is not shipped on macOS, the cameras are Linux only.
_NOT_ON_MACOS = frozenset(
    {"openfollow/video/inputs/ndi.py", "openfollow/video/inputs/picam.py", "openfollow/video/inputs/v4l2.py"}
)


def _plugin_allowlist(tree: ast.Module) -> set[str]:
    call = _assigned(tree, "_GST_PLUGIN_ALLOW")
    assert isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "frozenset"
    return set(ast.literal_eval(call.args[0]))


def _launch_elements(description: ast.expr) -> set[str]:
    """Element names in a ``Gst.parse_launch`` description; a caps segment carries a ``/``."""
    if isinstance(description, ast.Constant) and isinstance(description.value, str):
        text = description.value
    elif isinstance(description, ast.JoinedStr):
        text = "".join(part.value if isinstance(part, ast.Constant) else "0" for part in description.values)
    else:
        pytest.fail(f"cannot read the parse_launch description at line {description.lineno}")
    return {name for segment in text.split("!") if "/" not in (name := segment.split()[0])}


def _is_element_factory(func: ast.expr) -> bool:
    """``<...>.ElementFactory.make``, or the ``make`` helper an input defines around it."""
    if isinstance(func, ast.Name):
        return func.id == "make"
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "make"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "ElementFactory"
    )


def _elements_built_by_name(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        first = node.args[0]
        if isinstance(node.func, ast.Attribute) and node.func.attr == "parse_launch":
            names |= _launch_elements(first)
        elif _is_element_factory(node.func) and isinstance(first, ast.Constant) and isinstance(first.value, str):
            names.add(first.value)
    return names


@pytest.fixture(scope="module")
def built_by_name() -> dict[str, set[str]]:
    """Elements each source the app runs on macOS builds from a literal name."""
    found = {}
    for path in sorted((_REPO / "openfollow").rglob("*.py")):
        source = path.relative_to(_REPO).as_posix()
        if source not in _NOT_ON_MACOS and (names := _elements_built_by_name(path)):
            found[source] = names
    return found


def test_selfcheck_requires_every_element_the_sources_build(built_by_name) -> None:
    missing = sorted(
        f"{element} ({source})"
        for source, names in built_by_name.items()
        for element in names - set(_REQUIRED_GST_ELEMENTS)
    )
    assert not missing, f"add to REQUIRED_GST_ELEMENTS in packaging/macos/launcher.py: {missing}"


def test_the_scan_reads_every_pipeline_the_app_builds(built_by_name) -> None:
    # Elements built some other way would pass the test above unseen.
    inputs = _REPO / "openfollow" / "video" / "inputs"
    expected = {path.relative_to(_REPO).as_posix() for path in inputs.glob("[!_]*.py")} - _NOT_ON_MACOS
    expected |= {"openfollow/runtime/receiver_pipeline.py", "openfollow/video/media_store.py"}
    assert expected <= built_by_name.keys()


def test_selfcheck_requires_every_element_the_diagnostics_bundle_lists() -> None:
    listed = {name for name, _used_for in diagnostics_module._SHARED_GST_ELEMENTS}
    assert listed <= set(_REQUIRED_GST_ELEMENTS), sorted(listed - set(_REQUIRED_GST_ELEMENTS))


def test_webp_plugin_is_kept(spec_tree) -> None:
    assert "libgstwebp.dylib" in _plugin_allowlist(spec_tree)


@pytest.mark.parametrize("element", _REQUIRED_GST_ELEMENTS)
def test_every_element_the_selfcheck_requires_survives_the_allowlist(spec_tree, element) -> None:
    assert element in _ELEMENT_PLUGINS, f"name the plugin that provides {element!r} in _ELEMENT_PLUGINS"
    assert _ELEMENT_PLUGINS[element] in _plugin_allowlist(spec_tree)


# ---------------------------------------------------------------------------
# Post-build self-check
# ---------------------------------------------------------------------------


def _run_selfcheck_step(tmp_path: Path, output: str, status: int) -> subprocess.CompletedProcess[str]:
    """Run build-dmg.sh's self-check step against a stand-in app that prints ``output``."""
    script = _BUILD_DMG.read_text(encoding="utf-8")
    step = script[script.index('echo "==> Self-checking bundle') : script.index('echo "==> Building DMG"')]
    app = tmp_path / "OpenFollow.app"
    binary = app / "Contents" / "MacOS" / "OpenFollow"
    binary.parent.mkdir(parents=True)
    binary.write_text(f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(output)}\nexit {status}\n")
    binary.chmod(0o755)
    return subprocess.run(
        ["bash", "-c", f"set -euo pipefail\nAPP={shlex.quote(str(app))}\n{step}"],
        capture_output=True,
        text=True,
        check=False,
        env={"HOME": str(tmp_path), "PATH": os.environ["PATH"]},
        timeout=30,
    )


def test_selfcheck_step_passes_on_ok(tmp_path) -> None:
    result = _run_selfcheck_step(tmp_path, "OK", 0)
    assert result.returncode == 0
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("output", "status"),
    [
        pytest.param("FAIL: missing GStreamer elements: udpsrc", 1, id="missing-element"),
        pytest.param("FAIL: No module named 'cv2'", 1, id="missing-module"),
        pytest.param("", 134, id="crash"),
    ],
)
def test_selfcheck_step_fails_and_shows_what_the_app_printed(tmp_path, output, status) -> None:
    result = _run_selfcheck_step(tmp_path, output, status)
    assert result.returncode == 1
    assert result.stderr.splitlines()[0] == "Bundle self-check failed - the frozen native stack is incomplete:"
    assert output in result.stderr.splitlines()[1:]


# ---------------------------------------------------------------------------
# Declared minimum macOS
# ---------------------------------------------------------------------------


def test_build_checks_the_bundle_against_the_declared_minimum() -> None:
    script = _BUILD_DMG.read_text(encoding="utf-8")
    check = 'poetry run python "$SCRIPT_DIR/check_macos_minimum.py" "$APP"\n'
    assert "set -euo pipefail" in script
    assert check in script.splitlines(keepends=True), "build-dmg.sh must run the check unguarded"
    # After the freeze that writes the Info.plist, before anything is packaged.
    assert script.index("pyinstaller") < script.index(check) < script.index("create-dmg \\")


@pytest.mark.parametrize("doc_name", ["PACKAGING.md", "DEVELOPMENT.md"])
def test_docs_state_the_declared_minimum(spec_tree, doc_name) -> None:
    minimum = _info_plist_value(spec_tree, "LSMinimumSystemVersion")
    doc = " ".join((_REPO / "docs" / doc_name).read_text(encoding="utf-8").split())
    assert f"macOS {minimum} or later" in doc
    assert "Apple Silicon only" in doc


@pytest.fixture(scope="module")
def checker() -> types.ModuleType:
    return _load("check_macos_minimum")


def _otool(command: str, field: str, value: str) -> str:
    return f"Load command 9\n      cmd {command}\n  cmdsize 32\n    {field} {value}\n"


_BUILD_VERSION_15 = (
    "Load command 10\n      cmd LC_BUILD_VERSION\n  cmdsize 32\n platform 1\n    minos 15.0\n"
    "      sdk 15.4\n   ntools 1\n     tool 3\n  version 1230.1\n"
    "Load command 11\n      cmd LC_SOURCE_VERSION\n  cmdsize 16\n  version 0.0\n"
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("15.0", (15,)), ("15", (15,)), ("15.0.0", (15,)), ("13.3", (13, 3)), ("10.15.4", (10, 15, 4))],
)
def test_parse_version(checker, text, expected) -> None:
    assert checker.parse_version(text) == expected


@pytest.mark.parametrize(
    ("listing", "expected"),
    [
        pytest.param(_BUILD_VERSION_15, (15,), id="build-version-ignores-tool-and-source-versions"),
        pytest.param(_otool("LC_VERSION_MIN_MACOSX", "version", "10.13"), (10, 13), id="version-min-macosx"),
        pytest.param(_otool("LC_SOURCE_VERSION", "version", "99.0"), None, id="no-minimum"),
        pytest.param(
            _otool("LC_BUILD_VERSION", "minos", "13.0") + _otool("LC_BUILD_VERSION", "minos", "14.2"),
            (14, 2),
            id="newest-of-several",
        ),
    ],
)
def test_minimum_from_load_commands(checker, listing, expected) -> None:
    assert checker.minimum_from_load_commands(listing) == expected


_MACHO = b"\xcf\xfa\xed\xfe" + bytes(28)


def _fake_app(root: Path, plist: dict) -> Path:
    app = root / "OpenFollow.app"
    frameworks = app / "Contents" / "Frameworks"
    frameworks.mkdir(parents=True)
    with (app / "Contents" / "Info.plist").open("wb") as handle:
        plistlib.dump(plist, handle)
    (frameworks / "libnew.dylib").write_bytes(_MACHO)
    (frameworks / "libold.dylib").write_bytes(_MACHO)
    (frameworks / "LICENSE").write_text("not a Mach-O")
    (frameworks / "libalias.dylib").symlink_to(frameworks / "libnew.dylib")
    return app


_LISTINGS = {
    "libnew.dylib": _otool("LC_BUILD_VERSION", "minos", "26.0"),
    "libold.dylib": _otool("LC_BUILD_VERSION", "minos", "11.0"),
}


def _load_commands(path: Path) -> str:
    return _LISTINGS[path.name]


def test_check_fails_on_a_library_newer_than_the_plist(checker, tmp_path, capsys) -> None:
    app = _fake_app(tmp_path, {"LSMinimumSystemVersion": "15.0"})

    assert checker.main([str(app)], load_commands=_load_commands) == 1
    err = capsys.readouterr().err
    assert "LSMinimumSystemVersion 15.0" in err
    assert "Contents/Frameworks/libnew.dylib: macOS 26.0" in err
    assert "libold" not in err
    assert "libalias" not in err


@pytest.mark.parametrize(
    ("declared", "runs_on"),
    [
        pytest.param("26.0", "26.0", id="exact"),
        pytest.param("26", "26.0", id="exact-without-minor"),
        pytest.param("27.0", "27.0", id="declared-above-every-library"),
    ],
)
def test_check_passes_and_names_the_newest_minimum(checker, tmp_path, capsys, declared, runs_on) -> None:
    app = _fake_app(tmp_path, {"LSMinimumSystemVersion": declared})

    assert checker.main([str(app)], load_commands=_load_commands) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"Every bundled Mach-O runs on macOS {runs_on}.",
        "Newest minimum among them: macOS 26.0 (Contents/Frameworks/libnew.dylib).",
    ]


def test_check_fails_without_a_declared_minimum(checker, tmp_path, capsys) -> None:
    app = _fake_app(tmp_path, {"CFBundleName": "OpenFollow"})

    assert checker.main([str(app)], load_commands=_load_commands) == 1
    assert "declares no LSMinimumSystemVersion" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [[], ["a.app", "b.app"]])
def test_check_usage(checker, argv, capsys) -> None:
    assert checker.main(argv) == 2
    assert "usage:" in capsys.readouterr().err
