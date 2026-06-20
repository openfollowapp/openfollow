# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the embedded WebKitGTK browser overlay.

WebKit2 is a heavy system dep we can't depend on inside the test runner.
These tests cover contract pieces that don't need a live WebView: URL
construction, AVAILABLE flag, dispatch wiring, and on-close callback.
"""

from __future__ import annotations

import importlib
import sys
import types
from types import SimpleNamespace
from typing import Any

import pytest

from openfollow.runtime import webkit_browser

pytestmark = pytest.mark.unit


def _reload_with_gi(fake_gi: Any, fake_repo: Any) -> tuple[bool, str]:
    """Reload :mod:`openfollow.runtime.webkit_browser` against the given
    ``gi`` / ``gi.repository`` fakes; return the resulting
    ``(AVAILABLE, IMPORT_ERROR)`` snapshot, then always restore real
    modules and reload back to the natural import state so downstream
    tests see whichever branch the host platform actually took.
    """
    orig_gi = sys.modules.get("gi")
    orig_repo = sys.modules.get("gi.repository")
    try:
        sys.modules["gi"] = fake_gi
        sys.modules["gi.repository"] = fake_repo
        importlib.reload(webkit_browser)
        observed = (webkit_browser.AVAILABLE, webkit_browser.IMPORT_ERROR)
    finally:
        if orig_gi is not None:
            sys.modules["gi"] = orig_gi
        else:
            sys.modules.pop("gi", None)
        if orig_repo is not None:
            sys.modules["gi.repository"] = orig_repo
        else:
            sys.modules.pop("gi.repository", None)
        importlib.reload(webkit_browser)
    return observed


class TestModuleImport:
    """Deterministically exercise both arms of the module-level
    WebKit2 try-import so neither branch stays uncovered regardless
    of which side of the platform-dep matrix the host is on.

    Pattern mirrors ``tests/test_video_overlay.py``'s Rsvg coverage.
    """

    def test_module_marks_unavailable_when_gi_missing(self) -> None:
        """The outer ``except Exception`` branch fires when ``gi``
        itself is missing (no GObject introspection installed) –
        captures the import error for the diagnostics surface."""

        # A fake ``gi.require_version`` that always raises forces the
        # outer except branch even on hosts where WebKit2 actually
        # exists.
        fake_gi = types.ModuleType("gi")

        def _fail_require(namespace: str, _version: str) -> None:
            raise ImportError(f"{namespace} module not present")

        fake_gi.require_version = _fail_require  # type: ignore[attr-defined]
        fake_repo = types.ModuleType("gi.repository")
        available, error = _reload_with_gi(fake_gi, fake_repo)
        assert available is False
        assert "module not present" in error

    def test_module_marks_available_on_webkit_4_1(self) -> None:
        """Covers the success path with the 4.1 typelib (the current
        Debian Bookworm / Raspberry Pi OS package)."""
        fake_gi = types.ModuleType("gi")
        fake_gi.require_version = lambda *_a, **_kw: None  # type: ignore[attr-defined]

        class _FakeWebKit2:
            class WebView:
                pass

        fake_repo = types.ModuleType("gi.repository")
        fake_repo.WebKit2 = _FakeWebKit2  # type: ignore[attr-defined]
        available, _error = _reload_with_gi(fake_gi, fake_repo)
        assert available is True

    def test_module_falls_back_to_webkit_4_0(self) -> None:
        """Older Debian / Raspberry Pi installs ship only the 4.0
        typelib. The inner try / except ladders down to 4.0 before
        importing – without this fallback, those hosts would surface
        the "Open Web UI" item as permanently disabled even though
        WebKit2 is actually installed."""
        calls: list[str] = []

        def _require_version(namespace: str, version: str) -> None:
            calls.append(version)
            if version == "4.1":
                raise ValueError("4.1 typelib not present")
            # 4.0 succeeds

        fake_gi = types.ModuleType("gi")
        fake_gi.require_version = _require_version  # type: ignore[attr-defined]

        class _FakeWebKit2:
            class WebView:
                pass

        fake_repo = types.ModuleType("gi.repository")
        fake_repo.WebKit2 = _FakeWebKit2  # type: ignore[attr-defined]
        available, _error = _reload_with_gi(fake_gi, fake_repo)
        assert available is True
        assert calls == ["4.1", "4.0"]

    def test_module_disables_webkit_accelerated_compositing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import os as _os

        # Reload the module against a fresh, empty environment for
        # the two vars; ``setdefault`` only takes effect when the var
        # isn't already set, so we have to delete first to exercise
        # the default-setting branch.
        monkeypatch.delenv("WEBKIT_DISABLE_COMPOSITING_MODE", raising=False)
        monkeypatch.delenv("WEBKIT_DISABLE_DMABUF_RENDERER", raising=False)
        importlib.reload(webkit_browser)
        assert _os.environ.get("WEBKIT_DISABLE_COMPOSITING_MODE") == "1"
        assert _os.environ.get("WEBKIT_DISABLE_DMABUF_RENDERER") == "1"

    def test_module_respects_operator_override_of_webkit_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An operator who already set the WebKit env var (e.g. via a
        debug spelling like ``"compositing-on-request"`` that some
        WebKit2GTK builds recognise, or just a different non-empty
        sentinel) must keep their value through module import.
        ``setdefault`` semantics – verified explicitly so a future
        refactor to plain assignment trips this test. Note: WebKit
        treats any non-empty value as "disabled", so the override
        path is NOT how an operator re-enables AC – it just lets the
        operator's own choice of disable-flag sentinel survive."""
        import os as _os

        monkeypatch.setenv("WEBKIT_DISABLE_COMPOSITING_MODE", "operator-value")
        monkeypatch.setenv("WEBKIT_DISABLE_DMABUF_RENDERER", "operator-value")
        importlib.reload(webkit_browser)
        assert _os.environ["WEBKIT_DISABLE_COMPOSITING_MODE"] == "operator-value"
        assert _os.environ["WEBKIT_DISABLE_DMABUF_RENDERER"] == "operator-value"


class TestBuildUrl:
    def test_default_port_omits_explicit_80(self) -> None:
        cfg = SimpleNamespace(web_port=80)
        assert webkit_browser.build_url(cfg) == "http://127.0.0.1/"

    def test_non_default_port_appears_in_url(self) -> None:
        cfg = SimpleNamespace(web_port=9000)
        assert webkit_browser.build_url(cfg) == "http://127.0.0.1:9000/"

    def test_zero_or_missing_falls_back_to_80(self) -> None:
        assert webkit_browser.build_url(SimpleNamespace(web_port=0)) == "http://127.0.0.1/"
        assert webkit_browser.build_url(SimpleNamespace()) == "http://127.0.0.1/"

    def test_string_port_coerces_to_int(self) -> None:
        """``ConfigField`` for web_port is int, but the dispatcher /
        web form layer might hand it through as a string. Coerce
        rather than crashing on f-string formatting."""
        cfg = SimpleNamespace(web_port="8080")
        assert webkit_browser.build_url(cfg) == "http://127.0.0.1:8080/"

    def test_display_port_overrides_config_when_server_wired(self) -> None:
        cfg = SimpleNamespace(web_port=80)
        web_server = SimpleNamespace(display_port=8080)
        assert (
            webkit_browser.build_url(
                cfg,
                web_server=web_server,
            )
            == "http://127.0.0.1:8080/"
        )

    def test_falls_back_to_config_when_server_lacks_display_port(self) -> None:
        """Early-init paths (or stub servers in tests) may not have
        ``display_port`` set – fall back to the configured port so
        the URL is always constructible."""
        cfg = SimpleNamespace(web_port=9000)
        # ``display_port = None`` is the "not yet bound" state.
        web_server = SimpleNamespace(display_port=None)
        assert (
            webkit_browser.build_url(
                cfg,
                web_server=web_server,
            )
            == "http://127.0.0.1:9000/"
        )


class TestOverlayConstruction:
    def test_construct_without_webkit_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(webkit_browser, "AVAILABLE", False)
        monkeypatch.setattr(webkit_browser, "IMPORT_ERROR", "no typelib")
        with pytest.raises(RuntimeError, match="no typelib"):
            webkit_browser.WebKitBrowserOverlay("http://x/", on_close=lambda: None)

    def test_construct_without_webkit_uses_fallback_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Empty IMPORT_ERROR (e.g. caller-cleared) still raises a
        useful message rather than a confusing 'unknown reason: '."""
        monkeypatch.setattr(webkit_browser, "AVAILABLE", False)
        monkeypatch.setattr(webkit_browser, "IMPORT_ERROR", "")
        with pytest.raises(RuntimeError, match="unknown reason"):
            webkit_browser.WebKitBrowserOverlay("http://x/", on_close=lambda: None)

    def test_construct_with_webkit_loads_url_and_wires_close(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Smoke-test the construction path with a stub WebKit2 module:
        the WebView gets the URL and a key-press handler that fires
        ``on_close`` for Esc."""
        loaded: list[str] = []
        connected: list[tuple[str, object]] = []
        closed: list[bool] = []

        class _FakeWebView:
            def load_uri(self, uri: str) -> None:
                loaded.append(uri)

            def connect(self, signal: str, handler) -> None:  # noqa: ANN001
                connected.append((signal, handler))

        class _FakeWebKit2:
            WebView = _FakeWebView

        monkeypatch.setattr(webkit_browser, "AVAILABLE", True)
        monkeypatch.setattr(webkit_browser, "_WebKit2", _FakeWebKit2)
        overlay = webkit_browser.WebKitBrowserOverlay(
            "http://127.0.0.1:9000/",
            on_close=lambda: closed.append(True),
        )
        assert loaded == ["http://127.0.0.1:9000/"]
        assert len(connected) == 1
        assert connected[0][0] == "key-press-event"

        # Esc keypress fires on_close.
        handler = connected[0][1]
        handler(overlay.widget, SimpleNamespace(keyval=0xFF1B))
        assert closed == [True]

    def test_non_escape_keypress_does_not_fire_on_close(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        connected: list = []
        closed: list[bool] = []

        class _FakeWebView:
            def load_uri(self, uri: str) -> None: ...

            def connect(self, signal: str, handler) -> None:  # noqa: ANN001
                connected.append(handler)

        class _FakeWebKit2:
            WebView = _FakeWebView

        monkeypatch.setattr(webkit_browser, "AVAILABLE", True)
        monkeypatch.setattr(webkit_browser, "_WebKit2", _FakeWebKit2)
        overlay = webkit_browser.WebKitBrowserOverlay(
            "http://127.0.0.1/",
            on_close=lambda: closed.append(True),
        )
        # Letter 'a' (Gdk.KEY_a == 0x61) – page input, not a close.
        result = connected[0](overlay.widget, SimpleNamespace(keyval=0x61))
        assert result is False
        assert closed == []

    def test_on_close_handler_exception_is_logged_not_raised(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        connected: list = []

        class _FakeWebView:
            def load_uri(self, uri: str) -> None: ...

            def connect(self, signal: str, handler) -> None:  # noqa: ANN001
                connected.append(handler)

        class _FakeWebKit2:
            WebView = _FakeWebView

        monkeypatch.setattr(webkit_browser, "AVAILABLE", True)
        monkeypatch.setattr(webkit_browser, "_WebKit2", _FakeWebKit2)

        def _explode() -> None:
            raise RuntimeError("close blew up")

        overlay = webkit_browser.WebKitBrowserOverlay(
            "http://x/",
            on_close=_explode,
        )
        with caplog.at_level("ERROR"):
            result = connected[0](overlay.widget, SimpleNamespace(keyval=0xFF1B))
        assert result is True
        assert any("on_close" in r.message for r in caplog.records)

    def test_close_disconnects_and_tries_close(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        events: list[tuple[str, object]] = []

        class _FakeWebView:
            def load_uri(self, uri: str) -> None: ...

            def connect(self, signal: str, handler) -> int:  # noqa: ANN001
                return 7

            def disconnect(self, handler_id: int) -> None:
                events.append(("disconnect", handler_id))

            def try_close(self) -> None:
                events.append(("try_close", None))

        class _FakeWebKit2:
            WebView = _FakeWebView

        monkeypatch.setattr(webkit_browser, "AVAILABLE", True)
        monkeypatch.setattr(webkit_browser, "_WebKit2", _FakeWebKit2)
        overlay = webkit_browser.WebKitBrowserOverlay("http://x/", on_close=lambda: None)
        overlay.close()
        assert events == [("disconnect", 7), ("try_close", None)]
        # Idempotent: the webview ref is dropped, so a second close no-ops.
        overlay.close()
        assert events == [("disconnect", 7), ("try_close", None)]

    def test_close_tolerates_missing_try_close_and_disconnect_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        class _FakeWebView:
            def load_uri(self, uri: str) -> None: ...

            def connect(self, signal: str, handler) -> int:  # noqa: ANN001
                return 1

            def disconnect(self, handler_id: int) -> None:
                raise RuntimeError("disconnect boom")

            # No ``try_close`` – older WebKit / stub host.

        class _FakeWebKit2:
            WebView = _FakeWebView

        monkeypatch.setattr(webkit_browser, "AVAILABLE", True)
        monkeypatch.setattr(webkit_browser, "_WebKit2", _FakeWebKit2)
        overlay = webkit_browser.WebKitBrowserOverlay("http://x/", on_close=lambda: None)
        with caplog.at_level("ERROR"):
            overlay.close()
        assert any("disconnect" in r.message for r in caplog.records)

    def test_close_logs_try_close_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        class _FakeWebView:
            def load_uri(self, uri: str) -> None: ...

            def connect(self, signal: str, handler) -> int:  # noqa: ANN001
                return 1

            def disconnect(self, handler_id: int) -> None: ...

            def try_close(self) -> None:
                raise RuntimeError("try_close boom")

        class _FakeWebKit2:
            WebView = _FakeWebView

        monkeypatch.setattr(webkit_browser, "AVAILABLE", True)
        monkeypatch.setattr(webkit_browser, "_WebKit2", _FakeWebKit2)
        overlay = webkit_browser.WebKitBrowserOverlay("http://x/", on_close=lambda: None)
        with caplog.at_level("ERROR"):
            overlay.close()
        assert any("try_close" in r.message.lower() for r in caplog.records)


class TestEnterExit:
    def _make_app(
        self,
        *,
        browser_active: bool = False,
        sink_widget: Any | None = None,
    ) -> SimpleNamespace:
        from openfollow.configuration import AppConfig

        canvas_calls: list[tuple[str, object]] = []

        class _Canvas:
            def add_overlay_widget(self, widget) -> None:  # noqa: ANN001
                canvas_calls.append(("add", widget))

            def remove_overlay_widget(self, widget) -> None:  # noqa: ANN001
                canvas_calls.append(("remove", widget))

        # ``_video_receiver`` is consulted by ``enter_browser`` to hide
        # the gtksink widget while the overlay is up (compositor-fight
        # fix for the WebKit-on-gtksink stripes). Tests that don't care
        # pass ``sink_widget=None`` and the hide path becomes a no-op.
        class _Receiver:
            def __init__(self, widget: Any) -> None:
                self._widget = widget

            def get_sink_widget(self) -> Any:
                return self._widget

        back_calls: list[bool] = []
        app = SimpleNamespace(
            _config=AppConfig(),
            _canvas=_Canvas(),
            _browser_active=browser_active,
            _browser_overlay=None,
            _browser_hidden_sink=None,
            _video_receiver=_Receiver(sink_widget),
        )
        app._canvas_calls = canvas_calls
        # exit_browser re-opens Settings menu so Esc in WebView returns to hub.
        app._back_calls = back_calls
        app._enter_settings_menu = lambda *, banner="": back_calls.append(True)

        # Wire ``_exit_browser`` as the WebView's on_close target –
        # mirrors the real OpenFollowApp delegator binding.
        from openfollow.runtime.app_modes import exit_browser

        app._exit_browser = lambda: exit_browser(app)
        return app

    def test_enter_no_op_when_already_active(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        called: list = []
        monkeypatch.setattr(
            webkit_browser,
            "WebKitBrowserOverlay",
            lambda *a, **kw: called.append((a, kw)),
        )
        monkeypatch.setattr(webkit_browser, "AVAILABLE", True)
        app = self._make_app(browser_active=True)
        app_modes.enter_browser(app)
        assert called == []

    def test_enter_warns_and_no_ops_when_webkit_unavailable(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr(webkit_browser, "AVAILABLE", False)
        monkeypatch.setattr(webkit_browser, "IMPORT_ERROR", "no typelib")
        app = self._make_app()
        with caplog.at_level("WARNING"):
            app_modes.enter_browser(app)
        assert app._browser_active is False
        assert any("WebKit2 not available" in r.message for r in caplog.records)

    def test_enter_no_op_when_canvas_is_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr(webkit_browser, "AVAILABLE", True)
        app = self._make_app()
        app._canvas = None
        app_modes.enter_browser(app)
        assert app._browser_active is False

    def test_enter_constructs_overlay_and_mounts_widget(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        sentinel_widget = object()

        class _FakeOverlay:
            def __init__(self, url: str, on_close) -> None:  # noqa: ANN001
                self.url = url
                self.on_close = on_close
                self.widget = sentinel_widget

        monkeypatch.setattr(webkit_browser, "AVAILABLE", True)
        monkeypatch.setattr(webkit_browser, "WebKitBrowserOverlay", _FakeOverlay)
        app = self._make_app()
        app_modes.enter_browser(app)
        assert app._browser_active is True
        assert isinstance(app._browser_overlay, _FakeOverlay)
        assert app._browser_overlay.url == "http://127.0.0.1/"
        assert app._canvas_calls == [("add", sentinel_widget)]

    def test_exit_no_op_when_inactive(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(browser_active=False)
        app_modes.exit_browser(app)
        assert app._canvas_calls == []

    def test_exit_tolerates_missing_overlay(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(browser_active=True)
        app._browser_overlay = None
        app._browser_hidden_sink = None
        app_modes.exit_browser(app)
        assert app._browser_active is False
        assert app._back_calls == [True]

    def test_exit_removes_widget_and_clears_state(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        sentinel_widget = object()

        class _FakeOverlay:
            def __init__(self, url: str, on_close) -> None:  # noqa: ANN001
                self.widget = sentinel_widget

            def close(self) -> None: ...

        monkeypatch.setattr(webkit_browser, "AVAILABLE", True)
        monkeypatch.setattr(webkit_browser, "WebKitBrowserOverlay", _FakeOverlay)
        app = self._make_app()
        app_modes.enter_browser(app)
        app_modes.exit_browser(app)
        assert app._browser_active is False
        assert app._browser_overlay is None
        assert ("remove", sentinel_widget) in app._canvas_calls

    def test_exit_handles_missing_canvas(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        sentinel_widget = object()

        class _FakeOverlay:
            def __init__(self, url: str, on_close) -> None:  # noqa: ANN001
                self.widget = sentinel_widget

            def close(self) -> None: ...

        monkeypatch.setattr(webkit_browser, "AVAILABLE", True)
        monkeypatch.setattr(webkit_browser, "WebKitBrowserOverlay", _FakeOverlay)
        app = self._make_app()
        app_modes.enter_browser(app)
        app._canvas = None
        app_modes.exit_browser(app)
        assert app._browser_active is False
        assert app._browser_overlay is None

    def test_enter_hides_sink_widget_exit_re_shows(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        class _FakeOverlay:
            def __init__(self, url: str, on_close) -> None:  # noqa: ANN001
                self.widget = object()

            def close(self) -> None: ...

        class _SinkWidget:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def hide(self) -> None:
                self.calls.append("hide")

            def show(self) -> None:
                self.calls.append("show")

        monkeypatch.setattr(webkit_browser, "AVAILABLE", True)
        monkeypatch.setattr(webkit_browser, "WebKitBrowserOverlay", _FakeOverlay)
        sink = _SinkWidget()
        app = self._make_app(sink_widget=sink)
        app_modes.enter_browser(app)
        assert sink.calls == ["hide"]
        assert app._browser_hidden_sink is sink
        app_modes.exit_browser(app)
        assert sink.calls == ["hide", "show"]
        assert app._browser_hidden_sink is None

    def test_enter_tolerates_missing_video_receiver(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        class _FakeOverlay:
            def __init__(self, url: str, on_close) -> None:  # noqa: ANN001
                self.widget = object()

            def close(self) -> None: ...

        monkeypatch.setattr(webkit_browser, "AVAILABLE", True)
        monkeypatch.setattr(webkit_browser, "WebKitBrowserOverlay", _FakeOverlay)
        app = self._make_app()
        app._video_receiver = None
        app_modes.enter_browser(app)
        assert app._browser_active is True
        assert app._browser_hidden_sink is None
        app_modes.exit_browser(app)
        assert app._browser_active is False

    def test_enter_rolls_back_when_sink_hide_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        closed: list[bool] = []

        class _FakeOverlay:
            def __init__(self, url: str, on_close) -> None:  # noqa: ANN001
                self.widget = object()

            def close(self) -> None:
                closed.append(True)

        class _BadSink:
            def hide(self) -> None:
                raise RuntimeError("hide boom")

            def show(self) -> None: ...

        monkeypatch.setattr(webkit_browser, "AVAILABLE", True)
        monkeypatch.setattr(webkit_browser, "WebKitBrowserOverlay", _FakeOverlay)
        app = self._make_app(sink_widget=_BadSink())
        app_modes.enter_browser(app)
        # Mount succeeded but sink-hide raised → rolled back so the overlay
        # is torn down and dismissable, not wedged with active=False.
        assert app._browser_active is False
        assert app._browser_overlay is None
        assert closed == [True]
        assert app._back_calls == [True]


class TestProcessBrowserInput:
    """Gamepad-only operators have no Esc key path out of the WebView
    overlay. ``process_browser_input`` polls the gamepad's mapped
    cancel button (default ``B``) and dismisses the overlay when
    pressed – mirroring the cancel-only pattern of iface selection
    and source-type selection."""

    def _make_app(self, *, cancel_pressed: bool) -> SimpleNamespace:
        exit_calls: list[bool] = []

        class _GamepadHandler:
            def read_source_selection_input(self) -> SimpleNamespace:
                return SimpleNamespace(cancel_pressed=cancel_pressed)

        class _InputManager:
            gamepad_handler = _GamepadHandler()

        app = SimpleNamespace(
            _input_manager=_InputManager(),
            _exit_browser=lambda: exit_calls.append(True),
        )
        app._exit_calls = exit_calls
        return app

    def test_cancel_press_exits_browser(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(cancel_pressed=True)
        app_modes.process_browser_input(app)
        assert app._exit_calls == [True]

    def test_no_press_is_no_op(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(cancel_pressed=False)
        app_modes.process_browser_input(app)
        assert app._exit_calls == []

    def test_no_op_when_input_manager_missing(self) -> None:
        from openfollow.runtime import app_modes

        exit_calls: list[bool] = []
        app = SimpleNamespace(
            _input_manager=None,
            _exit_browser=lambda: exit_calls.append(True),
        )
        app_modes.process_browser_input(app)
        assert exit_calls == []

    def test_gamepad_read_failure_is_logged_not_raised(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        from openfollow.runtime import app_modes

        class _GamepadHandler:
            def read_source_selection_input(self) -> None:
                raise RuntimeError("SDL2 wedged")

        class _InputManager:
            gamepad_handler = _GamepadHandler()

        exit_calls: list[bool] = []
        app = SimpleNamespace(
            _input_manager=_InputManager(),
            _exit_browser=lambda: exit_calls.append(True),
        )
        with caplog.at_level("WARNING"):
            app_modes.process_browser_input(app)
        assert exit_calls == []
        assert any("Browser input error" in r.message for r in caplog.records)


class TestVideoDisconnectBanner:
    """Edge-triggered auto-open of Settings menu when video is
    disconnected. Disconnects surface as a banner inside the menu."""

    def _make_app(
        self,
        *,
        connected: bool = False,
        was_connected: bool = False,
        deadline_passed: bool = True,
        already_shown: bool = False,
        any_modal: bool = False,
        status_marker: object | None = None,
    ) -> SimpleNamespace:
        from openfollow.configuration import AppConfig

        cfg = AppConfig()
        cfg.video_source_type = "rtsp"

        if status_marker is None:
            status_marker = SimpleNamespace(
                source_name="",
                error_message="",
                reconnect_attempt=0,
            )
        receiver = SimpleNamespace(
            connected=connected,
            status_marker=status_marker,
        )

        opened: list[dict] = []
        app = SimpleNamespace(
            _config=cfg,
            _video_receiver=receiver,
            _video_disconnect_deadline=(0.0 if deadline_passed else float("inf")),
            _video_disconnect_banner_shown=already_shown,
            _video_was_connected=was_connected,
            _settings_menu_active=any_modal,
            _iface_selection_active=False,
            _source_type_selection_active=False,
            _url_editor_active=False,
            _field_choice_active=False,
            _browser_active=False,
            _button_detection=None,
        )
        app._opened = opened
        app._enter_settings_menu = lambda *, banner="": opened.append(
            {"banner": banner},
        )
        return app

    def test_no_op_when_receiver_missing(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app()
        app._video_receiver = None
        app_modes.check_video_disconnect_banner(app)
        assert app._opened == []

    def test_connect_latches_and_disables_startup_prompt(self) -> None:
        """The first successful connect latches ``_video_was_connected``
        True and clears the startup deadline, permanently disabling the
        auto-open for the session (mid-stream drops self-heal)."""
        from openfollow.runtime import app_modes

        app = self._make_app(
            connected=True,
            was_connected=False,
        )
        app._video_disconnect_deadline = 0.0  # past
        app_modes.check_video_disconnect_banner(app)
        assert app._video_was_connected is True
        assert app._video_disconnect_deadline == float("inf")
        assert app._opened == []  # no fire on connect

    def test_connected_steady_state_does_not_fire(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(connected=True, was_connected=True)
        app_modes.check_video_disconnect_banner(app)
        assert app._opened == []

    def test_midstream_disconnect_does_not_fire(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(connected=False, was_connected=True)
        app._video_disconnect_deadline = 0.0  # would have fired pre-change
        app_modes.check_video_disconnect_banner(app)
        assert app._opened == []
        assert app._video_disconnect_banner_shown is False

    def test_no_op_before_deadline(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(connected=False, deadline_passed=False)
        app_modes.check_video_disconnect_banner(app)
        assert app._opened == []

    def test_no_op_when_already_latched(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(connected=False, already_shown=True)
        app_modes.check_video_disconnect_banner(app)
        assert app._opened == []

    def test_no_op_when_settings_menu_already_open(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(connected=False, any_modal=True)
        app_modes.check_video_disconnect_banner(app)
        assert app._opened == []

    def test_no_op_when_iface_selection_open(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(connected=False)
        app._iface_selection_active = True
        app_modes.check_video_disconnect_banner(app)
        assert app._opened == []

    def test_no_op_when_source_type_selection_open(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(connected=False)
        app._source_type_selection_active = True
        app_modes.check_video_disconnect_banner(app)
        assert app._opened == []

    def test_no_op_when_url_editor_open(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(connected=False)
        app._url_editor_active = True
        app_modes.check_video_disconnect_banner(app)
        assert app._opened == []

    def test_no_op_when_browser_open(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(connected=False)
        app._browser_active = True
        app_modes.check_video_disconnect_banner(app)
        assert app._opened == []

    def test_no_op_when_field_choice_picker_open(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(connected=False)
        app._field_choice_active = True
        app_modes.check_video_disconnect_banner(app)
        assert app._opened == []

    def test_no_op_when_button_detection_active(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(connected=False)
        app._button_detection = object()
        app_modes.check_video_disconnect_banner(app)
        assert app._opened == []

    def test_fires_when_disconnected_past_deadline(self) -> None:
        """Startup case: receiver started disconnected (was_connected
        False), no edge to detect, deadline armed in run(). When the
        clock passes the deadline, the Settings menu opens."""
        from openfollow.runtime import app_modes

        app = self._make_app(
            connected=False,
            was_connected=False,
            status_marker=SimpleNamespace(
                source_name="rtsp://10.0.0.5/stream",
                error_message="Connection refused",
                reconnect_attempt=3,
            ),
        )
        app_modes.check_video_disconnect_banner(app)
        assert len(app._opened) == 1
        banner = app._opened[0]["banner"]
        assert "rtsp" in banner
        assert "rtsp://10.0.0.5/stream" in banner
        assert "Connection refused" in banner
        assert "Reconnect attempt 3" in banner
        assert app._video_disconnect_banner_shown is True

    def test_banner_omits_optional_fields_when_empty(self) -> None:
        """The banner gracefully drops empty source label, error, and
        zero reconnect-attempt without leaving dangling separators."""
        from openfollow.runtime import app_modes

        app = self._make_app(connected=False)
        app_modes.check_video_disconnect_banner(app)
        banner = app._opened[0]["banner"]
        assert "Error:" not in banner
        assert "Reconnect attempt" not in banner

    def test_latch_blocks_repeat_fire_after_dismiss(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(connected=False)
        app_modes.check_video_disconnect_banner(app)
        assert len(app._opened) == 1

        # Operator dismissed the menu – but the latch persists.
        app._settings_menu_active = False
        app_modes.check_video_disconnect_banner(app)
        assert len(app._opened) == 1

    def test_deferred_banner_fires_after_modal_closes(self) -> None:
        from openfollow.runtime import app_modes

        # Deadline passed, but a modal is open → defer.
        app = self._make_app(connected=False, any_modal=True)
        app_modes.check_video_disconnect_banner(app)
        assert app._opened == []
        # Critical: the latch did NOT flip, so a later check can fire.
        assert app._video_disconnect_banner_shown is False

        # Operator closes the modal – next animate tick re-runs the
        # check and the banner fires this time.
        app._settings_menu_active = False
        app_modes.check_video_disconnect_banner(app)
        assert len(app._opened) == 1
        assert app._video_disconnect_banner_shown is True

    def test_connect_then_disconnect_never_refires(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Full lifecycle: the startup banner fires once, the source
        connects (latching the prompt off), and a later mid-stream
        disconnect does NOT re-open the menu – recovery is left to the
        pipeline's self-healing."""
        from openfollow.runtime import app_modes

        clock = [1000.0]
        monkeypatch.setattr(app_modes.time, "monotonic", lambda: clock[0])

        app = self._make_app(connected=False)
        app._video_disconnect_deadline = 999.0  # already past
        app_modes.check_video_disconnect_banner(app)
        assert len(app._opened) == 1

        # Source connects – latches the startup prompt off permanently.
        app._video_receiver.connected = True
        app._settings_menu_active = False  # operator dismissed menu
        app_modes.check_video_disconnect_banner(app)
        assert app._video_was_connected is True
        assert app._video_disconnect_deadline == float("inf")

        # Later mid-stream disconnect – must stay quiet no matter how
        # far past any deadline the clock runs.
        app._video_receiver.connected = False
        clock[0] = 5000.0
        app_modes.check_video_disconnect_banner(app)
        clock[0] = 9000.0
        app_modes.check_video_disconnect_banner(app)
        assert len(app._opened) == 1  # never re-fired


class TestProcessInputAndKeyDispatchShortCircuit:
    """When ``_browser_active`` is True the gamepad poll path and the
    polled-key dispatch path must early-return so app-level shortcuts
    don't fire while the operator is interacting with the web UI."""

    def test_process_input_short_circuits_when_browser_active(self) -> None:
        from openfollow.configuration import AppConfig
        from openfollow.runtime import app_modes

        class _KB:
            keys: set[str] = set()

        class _IM:
            keyboard_handler = _KB()

            def update(self, _dt):  # noqa: ANN001
                pytest.fail("must not poll gamepad while browser open")

        app = SimpleNamespace(
            _input_manager=_IM(),
            _button_detection=None,
            _settings_menu_active=False,
            _video_receiver=None,
            _iface_selection_active=False,
            _source_type_selection_active=False,
            _url_editor_active=False,
            _field_choice_active=False,
            _browser_active=True,
            # Dispatcher now routes the gamepad poll into
            # ``_process_browser_input`` while the overlay is up so a
            # cancel-button press dismisses the WebView; a no-op stub
            # is enough here because this test only verifies that the
            # gamepad-MAIN-PATH (``_input_manager.update``) does NOT
            # fire – the cancel poll itself is covered separately by
            # ``TestProcessBrowserInput``.
            _process_browser_input=lambda: None,
            _config=AppConfig(),
        )
        app_modes.process_input(app, 0.01)

    def test_handle_key_press_short_circuits_when_browser_active(self) -> None:
        from openfollow.configuration import AppConfig
        from openfollow.runtime import app_modes

        cfg = AppConfig()
        cfg.controller.key_toggle_help = "h"
        app = SimpleNamespace(
            _config=cfg,
            _button_detection=None,
            _settings_menu_active=False,
            _video_receiver=None,
            _iface_selection_active=False,
            _source_type_selection_active=False,
            _url_editor_active=False,
            _field_choice_active=False,
            _browser_active=True,
            _show_hud_help=False,
        )
        app_modes.handle_key_press(app, "h")
        assert app._show_hud_help is False  # toggle did NOT fire

    def test_on_key_down_short_circuits_when_browser_active(self) -> None:
        from openfollow.runtime import app_modes

        class _KB:
            def on_key_down(self, _ev) -> None: ...

        class _IM:
            keyboard_handler = _KB()

        app = SimpleNamespace(
            _input_manager=_IM(),
            _url_editor_active=False,
            _field_choice_active=False,
            _browser_active=True,
            _controlled_ids=[10, 20, 30],
            _selected_id=10,
        )
        app._normalize_key = app_modes.normalize_key
        app_modes.on_key_down(app, {"key": "2"})
        assert app._selected_id == 10  # marker pick did NOT fire
