# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""The macOS app's Third-Party Notices list what its bundle holds."""

from __future__ import annotations

import importlib.util
import sys
import types
from email.message import Message
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SCRIPT = Path(__file__).resolve().parents[1] / "packaging" / "macos" / "bundle_notices.py"


@pytest.fixture
def notices(monkeypatch) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("openfollow_bundle_notices", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Dataclasses look their module up while the class is built.
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


_FORMULAE = [
    {
        "name": "gstreamer",
        "license": "LGPL-2.0-or-later",
        "homepage": "https://gstreamer.freedesktop.org/",
        "installed": [{"version": "1.26.7"}],
    },
    {
        "name": "ffmpeg",
        "license": "GPL-2.0-or-later",
        "homepage": "https://ffmpeg.org/",
        "installed": [{"version": "8.0_1"}],
    },
    {"name": "unused", "license": "MIT", "homepage": "", "installed": [{"version": "1.0"}]},
    {"name": "nolicense", "license": None, "installed": []},
]
_KEGS = {
    "gstreamer": [
        "/opt/homebrew/Cellar/gstreamer/1.26.7/lib/libgstreamer-1.0.0.dylib",
        "/opt/homebrew/Cellar/gstreamer/1.26.7/lib/gstreamer-1.0/libgstcoreelements.dylib",
        "/opt/homebrew/Cellar/gstreamer/1.26.7/bin/gst-launch-1.0",
    ],
    "ffmpeg": ["/opt/homebrew/Cellar/ffmpeg/8.0_1/lib/libavcodec.62.dylib"],
    "unused": ["/opt/homebrew/Cellar/unused/1.0/lib/libunused.dylib"],
}


def test_each_library_maps_to_the_formula_that_ships_it(notices) -> None:
    index = notices.cellar_index(_FORMULAE, _KEGS)

    assert index == {
        "libgstreamer-1.0.0.dylib": "gstreamer",
        "libgstcoreelements.dylib": "gstreamer",
        "libavcodec.62.dylib": "ffmpeg",
        "libunused.dylib": "unused",
    }


def test_only_the_formulae_the_bundle_uses_are_listed(notices) -> None:
    rows = notices.brew_rows(_FORMULAE, {"gstreamer", "ffmpeg", "nolicense"})

    assert [(r.name, r.version, r.license) for r in rows] == [
        ("ffmpeg", "8.0_1", "GPL-2.0-or-later"),
        ("gstreamer", "1.26.7", "LGPL-2.0-or-later"),
        ("nolicense", "", "unknown"),
    ]


def test_toc_entries_are_read_by_typecode(notices) -> None:
    toc = """('PYZ-00.pyz',
 [('bottle', '/venv/bottle.py', 'PYMODULE'),
  ('numpy.core', '/venv/numpy/core/__init__.py', 'PYMODULE'),
  ('cv2/cv2.abi3.so', '/venv/cv2/cv2.abi3.so', 'EXTENSION'),
  ('LICENSE', '/repo/LICENSE', 'DATA'),
  ('struct', None, 'PYMODULE')])"""

    names = notices.toc_names(toc, ("PYMODULE", "EXTENSION"))

    assert names == ["bottle", "numpy.core", "cv2/cv2.abi3.so", "struct"]
    assert {notices.top_level(n) for n in names} == {"bottle", "numpy", "cv2", "struct"}


def _metadata(**fields) -> Message:
    message = Message()
    for key, value in fields.items():
        for item in value if isinstance(value, list) else [value]:
            message[key.replace("_", "-")] = item
    return message


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"License_Expression": "MIT"}, "MIT"),
        ({"License": "BSD-3-Clause"}, "BSD-3-Clause"),
        ({"License": "x" * 200, "Classifier": ["License :: OSI Approved :: MIT License"]}, "MIT License"),
        (
            {
                "License": "line one\nline two",
                "Classifier": [
                    "License :: OSI Approved :: Apache Software License",
                    "License :: OSI Approved :: BSD License",
                ],
            },
            "Apache Software License / BSD License",
        ),
        ({}, "unknown"),
    ],
)
def test_a_distributions_license_is_read_from_its_metadata(notices, fields, expected: str) -> None:
    assert notices.dist_license(_metadata(**fields)) == expected


def test_a_license_without_metadata_is_unknown(notices) -> None:
    assert notices.dist_license(None) == "unknown"


def test_python_rows_name_each_providing_distribution_once(notices) -> None:
    providers = {
        "cv2": ["opencv-python-headless"],
        "numpy": ["numpy"],
        "openfollow": ["openfollow"],
        "yaml": ["PyYAML"],
    }

    rows = notices.python_rows(
        {"cv2", "numpy", "openfollow", "struct"},
        providers,
        lambda dist: notices.Row(dist, "1", "MIT"),
    )

    assert [r.name for r in rows] == ["numpy", "opencv-python-headless"]


def test_the_document_lists_every_part_and_no_pi_image(notices) -> None:
    text = notices.render(
        "0.5.0",
        [notices.Row("gstreamer", "1.26.7", "LGPL-2.0-or-later", "https://gstreamer.freedesktop.org/")],
        [notices.Row("numpy", "2.3.4", "BSD-3-Clause")],
        ["libonnxruntime.1.23.dylib"],
    )

    assert "OpenFollow 0.5.0 macOS app" in text
    assert "| gstreamer | 1.26.7 | LGPL-2.0-or-later | https://gstreamer.freedesktop.org/ |" in text
    assert "| numpy | 2.3.4 | BSD-3-Clause |" in text
    assert "- `libonnxruntime.1.23.dylib`" in text
    assert "Raspberry Pi" not in text and "Debian" not in text
    assert "—" not in text


def test_empty_sections_are_left_out(notices) -> None:
    text = notices.render("0.5.0", [], [], [])

    assert "Native libraries (Homebrew)" not in text
    assert "Python packages" not in text
    assert "Other native libraries" not in text


def test_the_bundles_copies_are_replaced_through_links(notices, tmp_path) -> None:
    resources = tmp_path / "OpenFollow.app" / "Contents" / "Resources"
    frameworks = tmp_path / "OpenFollow.app" / "Contents" / "Frameworks"
    resources.mkdir(parents=True)
    frameworks.mkdir(parents=True)
    (resources / "THIRD_PARTY_NOTICES.md").write_text("Pi image", encoding="utf-8")
    (frameworks / "THIRD_PARTY_NOTICES.md").symlink_to(resources / "THIRD_PARTY_NOTICES.md")
    (frameworks / "libgstreamer-1.0.0.dylib").write_bytes(b"")
    (frameworks / "liblink.dylib").symlink_to(frameworks / "libgstreamer-1.0.0.dylib")

    written = notices.replace_in_bundle(tmp_path / "OpenFollow.app", "THIRD_PARTY_NOTICES.md", "macOS")

    assert written == 1
    assert (resources / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8") == "macOS"
    assert notices.replace_in_bundle(tmp_path / "OpenFollow.app", "WRITTEN_OFFER.md", "x") == 0
    assert [p.name for p in notices.native_files(tmp_path / "OpenFollow.app")] == ["libgstreamer-1.0.0.dylib"]


def test_the_macos_offer_covers_the_dmg_not_the_pi_image(notices) -> None:
    text = notices.OFFER_SOURCE.read_text(encoding="utf-8")

    assert "macOS app" in text and "disk image" in text
    assert "Raspberry Pi" not in text
    assert "—" not in text


def test_main_writes_both_documents_into_the_bundle(notices, tmp_path, monkeypatch) -> None:
    import json

    app = tmp_path / "OpenFollow.app"
    frameworks = app / "Contents" / "Frameworks"
    frameworks.mkdir(parents=True)
    for name in ("THIRD_PARTY_NOTICES.md", "WRITTEN_OFFER.md"):
        (frameworks / name).write_text("repo copy", encoding="utf-8")
    (frameworks / "libavcodec.62.dylib").write_bytes(b"")
    (frameworks / "libpython3.13.dylib").write_bytes(b"")
    work = tmp_path / "work"
    work.mkdir()
    (work / "PYZ-00.toc").write_text("[('bottle', '/v/bottle.py', 'PYMODULE')]", encoding="utf-8")
    (work / "COLLECT-00.toc").write_text("[('LICENSE', '/r/LICENSE', 'DATA')]", encoding="utf-8")
    cellar = tmp_path / "Cellar"
    (cellar / "ffmpeg" / "8.0_1" / "lib").mkdir(parents=True)
    (cellar / "ffmpeg" / "8.0_1" / "lib" / "libavcodec.62.dylib").write_bytes(b"")
    answers = {
        ("info", "--json=v2", "--installed"): json.dumps({"formulae": [_FORMULAE[1]]}),
        ("--cellar",): f"{cellar}\n",
    }
    monkeypatch.setattr(notices, "_brew", lambda *args: answers[args])
    monkeypatch.setattr(notices.importlib.metadata, "packages_distributions", lambda: {"bottle": ["bottle"]})
    monkeypatch.setattr(notices, "_describe", lambda dist: notices.Row(dist, "0.13.4", "MIT"))

    assert notices.main(["--app", str(app), "--workdir", str(work), "--version", "0.5.0"]) == 0

    text = (frameworks / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "| ffmpeg | 8.0_1 | GPL-2.0-or-later | https://ffmpeg.org/ |" in text
    assert "| bottle | 0.13.4 | MIT |" in text
    assert "- `libpython3.13.dylib`" in text
    assert (frameworks / "WRITTEN_OFFER.md").read_text(encoding="utf-8") == notices.OFFER_SOURCE.read_text(
        encoding="utf-8"
    )


def test_main_fails_a_bundle_without_the_documents(notices, tmp_path, monkeypatch) -> None:
    import json

    app = tmp_path / "OpenFollow.app"
    app.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    for name in ("PYZ-00.toc", "COLLECT-00.toc"):
        (work / name).write_text("[]", encoding="utf-8")
    answers = {("info", "--json=v2", "--installed"): json.dumps({"formulae": []}), ("--cellar",): str(tmp_path)}
    monkeypatch.setattr(notices, "_brew", lambda *args: answers[args])

    assert notices.main(["--app", str(app), "--workdir", str(work), "--version", "0.5.0"]) == 1


def test_describe_reads_an_installed_distribution(notices) -> None:
    row = notices._describe("pytest")

    assert row.name == "pytest" and row.version and row.license != ""
