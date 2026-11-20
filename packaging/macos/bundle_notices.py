# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""Write the macOS app's Third-Party Notices from what the frozen bundle holds.

Run by ``build-dmg.sh`` after PyInstaller, before signing:

    python bundle_notices.py --app dist/OpenFollow.app --workdir build/openfollow

Every native library in the bundle is matched to the Homebrew formula whose
Cellar keg ships a file of that name (``brew info --json=v2 --installed``), and
every Python module PyInstaller froze to the distribution that provides it
(``importlib.metadata``, from the build venv). The notices and the macOS written
offer then replace the repository copies inside the bundle, so the About page
lists what this app contains, not what the Pi image does.

Stdlib only: it runs in the build venv and is unit-tested off macOS.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import re
import subprocess  # nosec B404 - fixed brew argv, no shell
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

NOTICES_NAME = "THIRD_PARTY_NOTICES.md"
OFFER_NAME = "WRITTEN_OFFER.md"
OFFER_SOURCE = Path(__file__).resolve().parent / OFFER_NAME

_NATIVE_SUFFIXES = (".dylib", ".so")
# PyInstaller TOC entries: ('dest name', 'source path', 'TYPECODE').
_TOC_ENTRY = re.compile(r"\(\s*'([^']+)'\s*,\s*(?:'[^']*'|None)\s*,\s*'([A-Z_]+)'\s*\)")
_CLASSIFIER_LICENSE = "License :: OSI Approved :: "


@dataclass(frozen=True)
class Row:
    name: str
    version: str
    license: str
    homepage: str = ""


def toc_names(text: str, typecodes: Iterable[str]) -> list[str]:
    """The dest names of a PyInstaller TOC file's entries of *typecodes*."""
    wanted = set(typecodes)
    return [name for name, code in _TOC_ENTRY.findall(text) if code in wanted]


def top_level(name: str) -> str:
    """The import package a module (``a.b``) or a collected file (``a/b.so``) belongs to."""
    return re.split(r"[./\\]", name, maxsplit=1)[0]


def cellar_index(formulae: Iterable[Mapping[str, object]], keg_files: Mapping[str, Iterable[str]]) -> dict[str, str]:
    """Native-library file name -> formula name, from each installed keg's files."""
    index: dict[str, str] = {}
    for formula in formulae:
        name = str(formula.get("name", ""))
        for path in keg_files.get(name, ()):
            base = os.path.basename(path)
            if base.endswith(_NATIVE_SUFFIXES) or ".dylib" in base:
                index.setdefault(base, name)
    return index


def brew_rows(formulae: Iterable[Mapping[str, object]], used: Iterable[str]) -> list[Row]:
    """One row per formula the bundle uses, with the installed version."""
    wanted = set(used)
    rows = []
    for formula in formulae:
        name = str(formula.get("name", ""))
        if name not in wanted:
            continue
        installed = formula.get("installed") or []
        version = ""
        if isinstance(installed, list) and installed and isinstance(installed[0], dict):
            version = str(installed[0].get("version", ""))
        rows.append(Row(name, version, str(formula.get("license") or "unknown"), str(formula.get("homepage") or "")))
    return sorted(rows, key=lambda row: row.name.lower())


def dist_license(metadata: Mapping[str, object] | object) -> str:
    """A distribution's license: its SPDX expression, a short License field, else its classifiers."""
    get = getattr(metadata, "get", None)
    get_all = getattr(metadata, "get_all", None)
    if get is None:
        return "unknown"
    expression = get("License-Expression")
    if expression:
        return str(expression).strip()
    text = str(get("License") or "").strip()
    if text and "\n" not in text and len(text) <= 60:
        return text
    classifiers = [str(c) for c in (get_all("Classifier") if get_all else None) or []]
    names = [c[len(_CLASSIFIER_LICENSE) :] for c in classifiers if c.startswith(_CLASSIFIER_LICENSE)]
    return " / ".join(names) if names else "unknown"


def python_rows(
    packages: Iterable[str],
    providers: Mapping[str, list[str]],
    describe: Callable[[str], Row],
) -> list[Row]:
    """One row per distribution providing a frozen top-level package; *describe(dist)* gives its Row."""
    dists = sorted({dist for package in packages for dist in providers.get(package, [])}, key=str.lower)
    return [describe(dist) for dist in dists if dist.lower() != "openfollow"]


def _table(title: str, rows: list[Row], *, homepage: bool) -> list[str]:
    if not rows:
        return []
    header = "| Component | Version | License |" + (" Homepage |" if homepage else "")
    rule = "|-----------|---------|---------|" + ("----------|" if homepage else "")
    lines = [f"## {title}", "", header, rule]
    for row in rows:
        line = f"| {row.name} | {row.version} | {row.license} |"
        if homepage:
            line += f" {row.homepage} |" if row.homepage else " |"
        lines.append(line)
    return [*lines, ""]


def render(app_version: str, homebrew: list[Row], python: list[Row], other: list[str]) -> str:
    """The notices document for one build of the macOS app."""
    lines = [
        "# Third-Party Notices (macOS app)",
        "",
        "OpenFollow is free software licensed under the **GNU Affero General Public",
        "License, version 3 or (at your option) any later version** (see `LICENSE`). Its",
        "source is at [github.com/openfollowapp/openfollow](https://github.com/openfollowapp/openfollow),",
        "linked from the About page.",
        "",
        f"This list was generated from the OpenFollow {app_version} macOS app bundle when",
        "it was built: every native library and Python package below is inside the app.",
        "The LGPL- and GPL-covered components are covered by the **Written Offer**. It",
        "is an informational summary, **not legal advice**; each component is governed",
        "by its own license.",
        "",
        "The **NDI® SDK** is proprietary and is not part of the macOS app: the NDI video",
        "input works only once the operator has installed NDI Tools, whose runtime is",
        "loaded from there at run time. **NDI® is a registered trademark of Vizrt NDI AB.**",
        "",
        "## Bundled with the app",
        "",
        "| Component | Version | License |",
        "|-----------|---------|---------|",
        "| [htmx](https://htmx.org/) | 1.9.10 | BSD-2-Clause |",
        "",
    ]
    lines += _table("Native libraries (Homebrew)", homebrew, homepage=True)
    lines += _table("Python packages", python, homepage=False)
    if other:
        lines += [
            "## Other native libraries",
            "",
            "Shipped inside the Python packages above or by the Python runtime.",
            "",
            *[f"- `{name}`" for name in other],
            "",
        ]
    return "\n".join(lines)


def native_files(app: Path) -> list[Path]:
    """Every native library inside *app*, links not followed twice."""
    found = []
    for root, _dirs, files in os.walk(app):
        for name in files:
            path = Path(root) / name
            if not path.is_symlink() and (name.endswith(_NATIVE_SUFFIXES) or ".dylib" in name):
                found.append(path)
    return sorted(found)


def replace_in_bundle(app: Path, name: str, text: str) -> int:
    """Write *text* over every *name* the bundle holds; the number of files written."""
    targets = {path.resolve() for path in app.rglob(name) if path.resolve().is_file()}
    for target in targets:
        target.write_text(text, encoding="utf-8")
    return len(targets)


def _brew(*args: str) -> str:
    return subprocess.run(["brew", *args], check=True, capture_output=True, text=True).stdout  # nosec B603 B607


def _keg_files(cellar: Path, formulae: Iterable[Mapping[str, object]]) -> dict[str, list[str]]:
    files: dict[str, list[str]] = {}
    for formula in formulae:
        name = str(formula.get("name", ""))
        installed = formula.get("installed")
        for keg_info in installed if isinstance(installed, list) else []:
            keg = cellar / name / str(keg_info.get("version", "") if isinstance(keg_info, dict) else "")
            files.setdefault(name, []).extend(str(p) for p in keg.rglob("*") if p.is_file())
    return files


def _describe(dist: str) -> Row:
    metadata = importlib.metadata.metadata(dist)
    return Row(dist, importlib.metadata.version(dist), dist_license(metadata))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True, help="PyInstaller's work dir for the spec")
    parser.add_argument("--version", required=True)
    args = parser.parse_args(argv)

    formulae = json.loads(_brew("info", "--json=v2", "--installed"))["formulae"]
    index = cellar_index(formulae, _keg_files(Path(_brew("--cellar").strip()), formulae))
    natives = native_files(args.app)
    used = {index[path.name] for path in natives if path.name in index}

    toc = "".join((args.workdir / name).read_text(encoding="utf-8") for name in ("PYZ-00.toc", "COLLECT-00.toc"))
    packages = {top_level(name) for name in toc_names(toc, ("PYMODULE", "EXTENSION"))}
    python = python_rows(packages, importlib.metadata.packages_distributions(), _describe)
    other = sorted({path.name for path in natives if path.name not in index})

    notices = render(args.version, brew_rows(formulae, used), python, other)
    written = replace_in_bundle(args.app, NOTICES_NAME, notices)
    offered = replace_in_bundle(args.app, OFFER_NAME, OFFER_SOURCE.read_text(encoding="utf-8"))
    if not written or not offered:
        print(f"FAIL: the bundle holds no {NOTICES_NAME} or {OFFER_NAME} to replace", file=sys.stderr)
        return 1
    print(f"Notices: {len(used)} formulae, {len(python)} Python packages, {len(other)} other libraries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
