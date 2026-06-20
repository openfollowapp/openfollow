# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Embedded WebKitGTK browser overlay for local web-UI access."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

# Disable WebKit compositing for RPi 5; must be set before WebKit2 import.
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")

logger = logging.getLogger(__name__)

# Public availability flag; False if WebKit import fails.
AVAILABLE: bool = False
IMPORT_ERROR: str = ""

try:
    import gi as _gi

    # ``gir1.2-webkit2-4.1`` is the supported package on current Debian /
    # Raspberry Pi OS releases; ``-4.0`` is the older typelib still
    # present on some installs. Try the newer one first, fall back to
    # the older without surfacing the failure to the operator.
    try:
        _gi.require_version("WebKit2", "4.1")
    except ValueError:
        _gi.require_version("WebKit2", "4.0")
    from gi.repository import WebKit2 as _WebKit2

    AVAILABLE = True
except Exception as _exc:  # noqa: BLE001
    # Both ImportError (gi or typelib missing) and ValueError (gi
    # present but no matching WebKit2 introspection bundle) land here.
    # Capture the message for the diagnostics surface so an operator
    # who picks "Open Web UI" on an unsupported host gets a useful hint
    # instead of a silent no-op.
    IMPORT_ERROR = str(_exc)
    _WebKit2 = None


class WebKitBrowserOverlay:
    """Thin wrapper around ``WebKit2.WebView`` for the on-device browser.

    Keeps WebKit imports out of the rest of the codebase: only this
    class touches ``_WebKit2``, which means tests + non-WebKit hosts
    can monkey-patch ``AVAILABLE`` without a guarded import everywhere.
    """

    def __init__(self, url: str, on_close: Callable[[], None]) -> None:
        if not AVAILABLE:
            # Defensive – callers should gate on ``AVAILABLE`` first.
            # Raising here makes the contract explicit instead of
            # constructing a half-initialised object.
            raise RuntimeError(
                f"WebKit2 not available: {IMPORT_ERROR or 'unknown reason'}",
            )
        self._on_close = on_close
        self._webview: Any = _WebKit2.WebView()
        # Esc inside the WebView calls back into ``on_close`` so the
        # operator can dismiss the overlay with the same key the rest
        # of the modal-mode surfaces use.
        self._key_handler_id: int = self._webview.connect("key-press-event", self._on_key_press)
        self.load(url)

    @property
    def widget(self) -> Any:
        return self._webview

    def load(self, url: str) -> None:
        self._webview.load_uri(url)

    def close(self) -> None:
        """Tear down the WebView so its web process exits without waiting on GC."""
        webview = self._webview
        if webview is None:
            return
        self._webview = None
        try:
            webview.disconnect(self._key_handler_id)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to disconnect browser key handler.")
        try_close = getattr(webview, "try_close", None)
        if callable(try_close):
            try:
                try_close()
            except Exception:  # noqa: BLE001
                logger.exception("Browser try_close failed.")

    def _on_key_press(self, _widget: Any, event: Any) -> bool:
        # Gdk.KEY_Escape == 0xff1b. Avoid importing Gdk here so the
        # module stays decoupled from a top-level ``gi`` dependency
        # for the test suite that runs without WebKit.
        if event.keyval == 0xFF1B:
            try:
                self._on_close()
            except Exception:  # noqa: BLE001
                logger.exception("Browser on_close handler failed.")
            return True
        return False


def build_url(config: Any, *, web_server: Any = None) -> str:
    """Construct the local web-UI URL from the running app config.

    Uses ``127.0.0.1`` rather than the LAN IP because the browser is
    embedded in the same process – localhost works regardless of
    interface state, including the unreachable-IP recovery flow.

    Prefers ``web_server.display_port`` when the server is wired so
    the embedded browser hits the port the HTTP listener actually
    bound to (the configured port can fall back at startup: 80 → 8080
    when the privileged bind fails). Falls back to ``config.web_port``
    when the server hasn't initialised yet (covers early-call / test paths).
    """
    port: int | None = None
    if web_server is not None:
        port = getattr(web_server, "display_port", None)
    if not port:
        port = int(getattr(config, "web_port", 80) or 80)
    else:
        port = int(port)
    if port == 80:
        return "http://127.0.0.1/"
    return f"http://127.0.0.1:{port}/"
