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
"""

from __future__ import annotations

import pathlib

import pytest

pytestmark = pytest.mark.unit

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_CONTROL = _ROOT / "packaging" / "debian" / "control.in"
_INSTALL = _ROOT / "scripts" / "install-system-deps.sh"

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
