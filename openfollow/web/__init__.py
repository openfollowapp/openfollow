# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Web config UI package; lazily exports ``ConfigWebServer`` via ``__getattr__``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openfollow.web.server import ConfigWebServer

__all__ = ["ConfigWebServer"]


def __getattr__(name: str) -> Any:
    if name == "ConfigWebServer":
        from openfollow.web.server import ConfigWebServer as _ConfigWebServer

        return _ConfigWebServer
    raise AttributeError(f"module 'openfollow.web' has no attribute {name!r}")
