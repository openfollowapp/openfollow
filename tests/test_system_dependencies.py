# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Guards on declared system (apt) dependencies the Python import graph can't see.

WebP decode/encode for the Media Gallery rides on ``gstreamer1.0-plugins-bad``
(the ``webpdec`` / ``webpenc`` elements live in ``libgstwebp.so`` there, which
also pulls in ``libwebp``). That package is currently declared for the SRT/RTSP
inputs too, so a pipeline refactor that drops those could remove it by accident
and silently kill WebP uploads. Pin it to the gallery here, in both the .deb
``Depends`` and the manual install script, so removing it fails CI rather than
the feature.

The PyGObject floor is the same shape of invariant read the other way: a
packaged install runs the OS bindings, so a floor above what the distro ships
declares the shipped configuration unsupported and forces ``pip install`` into
a source build to reach a state that already works.
"""

from __future__ import annotations

import pathlib

import pytest
import tomllib

pytestmark = pytest.mark.unit

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_CONTROL = _ROOT / "packaging" / "debian" / "control.in"
_INSTALL = _ROOT / "scripts" / "install-system-deps.sh"
_PYPROJECT = _ROOT / "pyproject.toml"

# python3-gi on Debian Trixie, the binding a packaged install actually imports.
_OS_PYGOBJECT_VERSION = (3, 50, 0)

# Provides webpdec/webpenc (Media Gallery WebP) and srtsrc (SRT input). It is a
# dedicated dependency of WebP support, not only of the current input pipeline.
_WEBP_PACKAGE = "gstreamer1.0-plugins-bad"


def test_webp_package_declared_in_deb_depends() -> None:
    assert _WEBP_PACKAGE in _CONTROL.read_text(), (
        f"{_WEBP_PACKAGE} provides webpdec for the Media Gallery; it must stay in the .deb Depends."
    )


def test_webp_package_declared_in_install_script() -> None:
    assert _WEBP_PACKAGE in _INSTALL.read_text(), (
        f"{_WEBP_PACKAGE} provides webpdec for the Media Gallery; it must stay in install-system-deps.sh."
    )


def _version_tuple(text: str) -> tuple[int, int, int]:
    parts = [int(part) for part in text.split(".")]
    parts += [0] * (3 - len(parts))
    return parts[0], parts[1], parts[2]


def _declared_pygobject_floor() -> tuple[int, int, int]:
    dependencies = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]["dependencies"]
    specs = [spec for spec in dependencies if spec.lower().replace("-", "").startswith("pygobject")]
    assert len(specs) == 1, f"expected exactly one PyGObject dependency, found {specs}."
    floor = specs[0].partition(">=")[2].strip()
    assert floor, f"PyGObject must declare a >= floor so this guard can read it, got {specs[0]!r}."
    return _version_tuple(floor)


def test_pygobject_floor_does_not_exceed_os_binding() -> None:
    """The declared floor must not rule out the binding the packaged app runs on."""
    assert _declared_pygobject_floor() <= _OS_PYGOBJECT_VERSION, (
        "The PyGObject floor in pyproject.toml is above the python3-gi that Debian Trixie ships "
        f"({'.'.join(str(part) for part in _OS_PYGOBJECT_VERSION)}), which is the binding a packaged "
        "install imports - build-deb.sh drops the pip-built copy. Raising the floor declares the "
        "shipped configuration unsupported and forces a source build to reach a working state."
    )
