# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Packaging contract for the 3D Mouse input.

The feature must work on an offline show Pi installed from the ``.deb`` with no
operator setup, so its dependency stack has to ship with the package: the
Python side (``pyspacemouse``) bundled in the venv as a base runtime dependency,
and the system HID library (``libhidapi``) pulled as an apt ``Depends``. These
tests lock that in ``make ci`` so a regression (moving the dep to an optional
extra, dropping the ``Depends``) is caught long before the tag-time ``.deb``
build's own bundled-in-venv assertion.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import tomllib

import openfollow

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(openfollow.__file__).resolve().parent.parent


def _pyproject() -> dict[str, Any]:
    path = openfollow._PYPROJECT
    if path is None or not path.is_file():
        pytest.skip("no checkout pyproject.toml (wheel install)")
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_pyspacemouse_is_a_base_runtime_dependency() -> None:
    deps = _pyproject()["project"]["dependencies"]
    assert any("pyspacemouse" in req for req in deps), (
        "pyspacemouse must be a base [project.dependencies] entry so the .deb "
        "build bundles it into the venv (the feature can't fall back online)"
    )


def test_pyspacemouse_is_not_hidden_behind_an_optional_extra() -> None:
    extras = _pyproject()["project"].get("optional-dependencies", {})
    for name, reqs in extras.items():
        assert not any("pyspacemouse" in req for req in reqs), (
            f"pyspacemouse must not live in the optional extra '{name}' – an extra "
            "is not installed by the .deb build, so the feature would silently no-op"
        )


def test_libhidapi_is_a_deb_runtime_dependency() -> None:
    control = _REPO_ROOT / "packaging" / "debian" / "control.in"
    if not control.is_file():
        pytest.skip("no debian/control.in in this tree")
    # easyhid dlopens libhidapi at runtime; it must be an apt Depends so it is
    # present on an offline target with no manual install.
    assert "libhidapi-hidraw0" in control.read_text(encoding="utf-8"), (
        "libhidapi-hidraw0 must be a Depends in debian/control.in"
    )
