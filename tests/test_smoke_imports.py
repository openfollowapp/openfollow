# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Smoke tests: import the core modules and verify the lazy package re-exports
(``openfollow.OpenFollowApp`` / ``openfollow.web.ConfigWebServer``) resolve without
pulling in the GObject/GStreamer chain at leaf-import time."""

from __future__ import annotations

import importlib

import pytest

pytestmark = pytest.mark.smoke

SMOKE_MODULES = [
    "openfollow.app",
    "openfollow.configuration",
    "openfollow.osc",
    "openfollow.osc.service",
    "openfollow.osc.template",
    "openfollow.osc.input",
    "openfollow.osc.transmitter",
    "openfollow.osc.transport",
    "openfollow.psn.server",
    "openfollow.psn.receiver",
    "openfollow.runtime.receiver_bus",
    "openfollow.runtime.receiver_pipeline",
    "openfollow.runtime.receiver_state",
    "openfollow.scene.solver",
    "openfollow.system_stats",
    "openfollow.video.receiver",
    "openfollow.web.routes",
]


@pytest.mark.parametrize("module_name", SMOKE_MODULES)
def test_module_import_smoke(module_name: str) -> None:
    module = importlib.import_module(module_name)
    assert module is not None


def test_openfollow_lazy_app_export() -> None:
    # ``openfollow`` re-exports ``OpenFollowApp`` via a lazy
    # ``__getattr__`` so leaf submodule imports don't pull in the
    # GObject / GStreamer chain.  Confirm the re-export still works
    # end-to-end and that the attribute-error fallback is sound.
    import openfollow

    klass = openfollow.OpenFollowApp
    assert klass.__name__ == "OpenFollowApp"
    with pytest.raises(AttributeError):
        openfollow.ThisDoesNotExist  # noqa: B018


def test_openfollow_web_lazy_server_export() -> None:
    # Same pattern on ``openfollow.web`` for ``ConfigWebServer``.
    import openfollow.web as web_pkg

    klass = web_pkg.ConfigWebServer
    assert klass.__name__ == "ConfigWebServer"
    with pytest.raises(AttributeError):
        web_pkg.NotAThing  # noqa: B018
