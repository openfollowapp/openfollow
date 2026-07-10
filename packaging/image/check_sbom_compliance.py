#!/usr/bin/env python3
"""Fail the release if the appliance image SBOM violates the compliance policy.

Reads the SPDX SBOM that rpi-image-gen's Syft scan already produces over the
final rootfs (dpkg + venv) and enforces:

* NDI (whole image) – the proprietary NDI SDK/plugin, matched by name (its
  license metadata is unreliable).
* Export toolchain (venv only) – `ultralytics` must not be bundled in the venv:
  it is AGPL-3.0 and is only needed to *export* models on a workstation, never at
  show time. The inference backend (`onnxruntime` + `opencv`) IS bundled in the
  venv on purpose – both are permissively licensed – so detection runs on an
  offline Pi with no pip. The venv scope (pypi purl only) means a transitive copy
  in the OS media stack (e.g. gstreamer pulls libonnxruntime) never trips it.

OpenFollow itself is AGPL-3.0-or-later, so there is no blanket AGPL-license gate:
AGPL is the image's own license, and the Debian OS it sits beside is an
independent work on the same medium (mere aggregation).

    check_sbom_compliance.py SBOM.spdx.json

Exit codes: 0 clean, 1 policy violation, 2 bad input / unreadable SBOM.
"""

from __future__ import annotations

import json
import re
import sys

# Names forbidden anywhere in the image. Token-bounded so 'ndi' matches only as a
# delimited segment (not 'indicator-application').
_GLOBAL_DENYLISTED_NAMES = [
    ("ndi", re.compile(r"(?:^|-)ndi(?:$|-)|libndi")),
]

# Names forbidden in the bundled venv – the AGPL model-export toolchain. The
# inference backend (onnxruntime + opencv, both permissive) is bundled on purpose
# so detection runs offline; only ultralytics (AGPL-3.0, export-only, workstation)
# must stay out.
_VENV_DENYLISTED_NAMES = [
    ("ultralytics", re.compile(r"ultralytics")),
]


def _normalise(name: str) -> str:
    return re.sub(r"[-_.+]+", "-", name.strip()).lower().strip("-")


def _match_name(name: str, denylist: list[tuple[str, re.Pattern[str]]]) -> str | None:
    norm = _normalise(name)
    for label, pattern in denylist:
        if pattern.search(norm):
            return label
    return None


def _is_venv_package(pkg: dict) -> bool:
    """True if the package is a bundled-venv Python package (purl pkg:pypi/...)."""
    for ref in pkg.get("externalRefs", []):
        if ref.get("referenceType") == "purl" and str(ref.get("referenceLocator", "")).startswith("pkg:pypi/"):
            return True
    return False


def find_violations(sbom: dict) -> list[str]:
    """Policy violations in an SPDX ``sbom`` (NDI image-wide, detection extras in venv)."""
    packages = sbom.get("packages")
    if not isinstance(packages, list):
        raise ValueError("SBOM has no 'packages' list")
    # A zero-package SBOM means Syft scanned nothing – treat a degenerate scan as
    # bad input (exit 2) rather than silently green-lighting the release.
    if not packages:
        raise ValueError("SBOM 'packages' list is empty")

    violations: list[str] = []
    for pkg in packages:
        if not isinstance(pkg, dict):
            continue
        name = str(pkg.get("name", ""))

        label = _match_name(name, _GLOBAL_DENYLISTED_NAMES)
        if label:
            violations.append(f"name: {name}  ->  denylisted ({label})")

        if _is_venv_package(pkg):
            extra = _match_name(name, _VENV_DENYLISTED_NAMES)
            if extra:
                violations.append(f"venv: {name}  ->  bundled detection extra ({extra})")
    return violations


def load_sbom(path: str) -> dict:
    """Parse ``path`` as an SPDX JSON document."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or not str(data.get("spdxVersion", "")).startswith("SPDX-"):
        raise ValueError("not an SPDX document")
    return data


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: check_sbom_compliance.py SBOM.spdx.json", file=sys.stderr)
        return 2

    try:
        sbom = load_sbom(argv[0])
        violations = find_violations(sbom)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[check-sbom] ERROR: cannot read SBOM: {exc}", file=sys.stderr)
        return 2

    if violations:
        print("[check-sbom] FAIL: forbidden components in the image:", file=sys.stderr)
        for line in violations:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print("[check-sbom] OK: no NDI / bundled export toolchain in the image.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
