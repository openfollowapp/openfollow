# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Contract of the macOS app bundle, read from the build files.

PyInstaller only runs on a Mac with the Homebrew stack, so ``openfollow.spec``
and ``build-dmg.sh`` are read as AST and text here, and a missing document,
plugin or minimum-version check fails ``make ci`` instead of a DMG on a stage.
The minimum-version checker ``build-dmg.sh`` runs is exercised on a stand-in
``.app``.
"""

from __future__ import annotations

import ast
import importlib.util
import plistlib
import types
from pathlib import Path

import pytest

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
    # _bundled_doc_path looks for them directly under sys._MEIPASS.
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
# GStreamer plugins
# ---------------------------------------------------------------------------

# The plugin each element the self-check requires lives in (Homebrew 1.26).
_ELEMENT_PLUGINS = {
    "gtksink": "libgstgtk.dylib",
    "videoconvert": "libgstvideoconvertscale.dylib",
    "videotestsrc": "libgstvideotestsrc.dylib",
    "avfvideosrc": "libgstapplemedia.dylib",
    "decodebin": "libgstplayback.dylib",
    "rtspsrc": "libgstrtsp.dylib",
    "srtsrc": "libgstsrt.dylib",
    "jpegenc": "libgstjpeg.dylib",
    "webpdec": "libgstwebp.dylib",
}


def _plugin_allowlist(tree: ast.Module) -> set[str]:
    call = _assigned(tree, "_GST_PLUGIN_ALLOW")
    assert isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "frozenset"
    return set(ast.literal_eval(call.args[0]))


def test_webp_plugin_is_kept(spec_tree) -> None:
    assert "libgstwebp.dylib" in _plugin_allowlist(spec_tree)


@pytest.mark.parametrize("element", _load("launcher").REQUIRED_GST_ELEMENTS)
def test_every_element_the_selfcheck_requires_survives_the_allowlist(spec_tree, element) -> None:
    assert element in _ELEMENT_PLUGINS, f"name the plugin that provides {element!r} in _ELEMENT_PLUGINS"
    assert _ELEMENT_PLUGINS[element] in _plugin_allowlist(spec_tree)


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


def test_packaging_doc_states_the_declared_minimum(spec_tree) -> None:
    minimum = _info_plist_value(spec_tree, "LSMinimumSystemVersion")
    doc = (_REPO / "docs" / "PACKAGING.md").read_text(encoding="utf-8")
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


@pytest.mark.parametrize("declared", ["26.0", "26"])
def test_check_passes_when_the_plist_covers_every_library(checker, tmp_path, capsys, declared) -> None:
    app = _fake_app(tmp_path, {"LSMinimumSystemVersion": declared})

    assert checker.main([str(app)], load_commands=_load_commands) == 0
    assert "macOS 26.0" in capsys.readouterr().out


def test_check_fails_without_a_declared_minimum(checker, tmp_path, capsys) -> None:
    app = _fake_app(tmp_path, {"CFBundleName": "OpenFollow"})

    assert checker.main([str(app)], load_commands=_load_commands) == 1
    assert "declares no LSMinimumSystemVersion" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [[], ["a.app", "b.app"]])
def test_check_usage(checker, argv, capsys) -> None:
    assert checker.main(argv) == 2
    assert "usage:" in capsys.readouterr().err
