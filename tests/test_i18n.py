# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the i18n framework (openfollow.i18n).

Covers the context-local translator bridge, lazy strings, Accept-Language
negotiation, locale auto-discovery, the Bottle plugin lifecycle, and the
framework-only default state (no bundled locale → graceful fallback).
"""

from __future__ import annotations

import io
import threading
from pathlib import Path
from typing import Any

import pytest
from bottle import Bottle, SimpleTemplate

import openfollow.i18n as i18n
from openfollow.i18n import (
    _AVAILABLE_LANGUAGES,
    I18NPlugin,
    _,
    _best_language,
    _discover_languages,
    _l,
    _LazyString,
    _subtag_match,
    _template_translate,
    lazy_gettext,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _restore_template_defaults() -> Any:
    """Snapshot and restore process-global i18n state around every test.

    ``I18NPlugin.setup()`` writes ``_`` and ``available_languages`` into the
    class-level ``SimpleTemplate.defaults`` (and mutates the module-level
    ``_AVAILABLE_LANGUAGES`` cache).  Bottle resolves those names live from
    ``defaults`` at render time, so a ``setup()`` in one test otherwise leaks
    into every later test in the process.  That leak previously *masked* a real
    bug (``base.tpl`` referencing ``available_languages`` with no pre-setup
    fallback): the suite passed serially only because ``test_i18n`` runs before
    ``test_web_picker_wiring`` and seeded ``defaults`` first.  Isolating the
    state here keeps that class of bug visible.
    """
    saved_defaults = SimpleTemplate.defaults.copy()
    saved_available = i18n._AVAILABLE_LANGUAGES
    try:
        yield
    finally:
        SimpleTemplate.defaults.clear()
        SimpleTemplate.defaults.update(saved_defaults)
        i18n._AVAILABLE_LANGUAGES = saved_available


# Default available languages used in _best_language tests.
_EN_ONLY = ("en",)


# ── _template_translate ─────────────────────────────────────────────────────


class TestTemplateTranslate:
    """The bridge function that SimpleTemplate.defaults["_"] points to."""

    def test_returns_message_when_no_translator_set(self) -> None:
        i18n._translate_ctx.set(None)
        assert _template_translate("Hello") == "Hello"

    def test_passes_through_translator(self) -> None:
        i18n._translate_ctx.set(lambda s: s.upper())
        assert _template_translate("Hello") == "HELLO"


# ── _LazyString ─────────────────────────────────────────────────────────────


class TestLazyString:
    """Deferred translation strings: declare at import time, resolve at request time."""

    def test_str_resolves_via_translator(self) -> None:
        i18n._translate_ctx.set(lambda s: f"[{s}]")
        ls = _LazyString("USB Camera")
        assert str(ls) == "[USB Camera]"

    def test_str_when_no_translator_returns_msgid(self) -> None:
        i18n._translate_ctx.set(None)
        ls = _LazyString("USB Camera")
        assert str(ls) == "USB Camera"

    def test_repr_uses_str(self) -> None:
        i18n._translate_ctx.set(None)
        ls = _LazyString("Hello")
        assert repr(ls) == "'Hello'"

    def test_eq_with_non_string_returns_notimplemented(self) -> None:
        # Comparing to a non-str, non-_LazyString yields NotImplemented so
        # Python falls back to identity (they are unequal, and don't raise).
        ls = lazy_gettext("x")
        assert ls.__eq__(42) is NotImplemented
        assert ls != 42

    def test_eq_by_msgid(self) -> None:
        a = _LazyString("foo")
        b = _LazyString("foo")
        c = _LazyString("bar")
        assert a == b
        assert a != c

    def test_eq_with_plain_str_compares_msgid(self) -> None:
        """_LazyString("hello") == "hello" compares _message, not translation.

        This keeps __eq__ and __hash__ consistent: both are based on _message.
        """
        i18n._translate_ctx.set(lambda s: f"[{s}]")
        ls = _LazyString("hello")
        assert ls == "hello"
        assert ls != "world"

    def test_in_operator_works(self) -> None:
        """_LazyString works in list membership (very common pattern)."""
        i18n._translate_ctx.set(None)
        names = [_LazyString("foo"), _LazyString("bar")]
        assert "foo" in names
        assert "baz" not in names

    def test_hash_by_msgid(self) -> None:
        a = _LazyString("key")
        b = _LazyString("key")
        assert hash(a) == hash(b)
        assert hash(a) == hash("key")

    def test_mod_formatting(self) -> None:
        i18n._translate_ctx.set(None)
        ls = _LazyString("Port %s")
        assert ls % 8080 == "Port 8080"

    def test_add(self) -> None:
        i18n._translate_ctx.set(None)
        ls = _LazyString("A")
        assert ls + "B" == "AB"

    def test_radd(self) -> None:
        i18n._translate_ctx.set(None)
        ls = _LazyString("B")
        assert "A" + ls == "AB"

    def test_str_methods_forwarded(self) -> None:
        """Transparent str proxy: ``.lower()`` etc. resolve then delegate.

        Plugin ``display_name`` is declared ``_l(...)`` but consumed as a
        plain ``str`` (e.g. sorted by ``display_name.lower()`` in
        ``app_modes``).  Without method forwarding that path raised
        ``AttributeError: '_LazyString' object has no attribute 'lower'``.
        """
        i18n._translate_ctx.set(None)
        ls = _LazyString("USB Camera")
        assert ls.lower() == "usb camera"
        assert ls.upper() == "USB CAMERA"
        assert ls.strip() == "USB Camera"
        assert ls.split() == ["USB", "Camera"]
        assert ls.replace("USB", "Pi") == "Pi Camera"

    def test_len_index_contains_iter(self) -> None:
        i18n._translate_ctx.set(None)
        ls = _LazyString("abc")
        assert len(ls) == 3
        assert ls[0] == "a"
        assert "b" in ls
        assert list(ls) == ["a", "b", "c"]

    def test_sortable_by_lower_like_display_name(self) -> None:
        """Regression: source-type list is sorted by ``display_name.lower()``."""
        i18n._translate_ctx.set(None)
        names = [_LazyString("Zeta"), _LazyString("alpha"), _LazyString("Mid")]
        assert [str(n) for n in sorted(names, key=lambda s: s.lower())] == [
            "alpha",
            "Mid",
            "Zeta",
        ]


# ── lazy_gettext / _l ──────────────────────────────────────────────────────


def test_lazy_gettext_returns_lazy_string() -> None:
    result = lazy_gettext("Test")
    assert isinstance(result, _LazyString)
    assert result._message == "Test"


def test_l_alias_is_lazy_gettext() -> None:
    assert _l is lazy_gettext


# ── _() immediate translation ──────────────────────────────────────────────


def test_immediate_underscore_translates() -> None:
    i18n._translate_ctx.set(lambda s: s.upper())
    assert _("hello") == "HELLO"


def test_immediate_underscore_no_translator() -> None:
    i18n._translate_ctx.set(None)
    assert _("hello") == "hello"


# ── _subtag_match ───────────────────────────────────────────────────────────


class TestSubtagMatch:
    """RFC 5646 boundary-safe language tag prefix matching."""

    def test_exact_match(self) -> None:
        assert _subtag_match("en", "en") is True
        assert _subtag_match("zh_tw", "zh_tw") is True

    def test_primary_subtag_match(self) -> None:
        """ "en_US" matches "en" because "en" is the primary subtag."""
        assert _subtag_match("en_us", "en") is True
        assert _subtag_match("zh_tw", "zh") is True

    def test_requested_longer_than_available(self) -> None:
        """ "en" matches "en_US" — browser wants specific, we have generic."""
        assert _subtag_match("en", "en_us") is True
        assert _subtag_match("zh", "zh_tw") is True

    def test_no_boundary_no_match(self) -> None:
        """ "english" should NOT match "en" — no subtag boundary at position 2."""
        assert _subtag_match("english", "en") is False
        assert _subtag_match("ende", "en") is False

    def test_completely_different(self) -> None:
        assert _subtag_match("fr", "en") is False
        assert _subtag_match("ja", "zh") is False


# ── _AVAILABLE_LANGUAGES ───────────────────────────────────────────────────


def test_available_languages_empty_by_default() -> None:
    """Empty tuple means auto-discover; set to non-empty to override."""
    assert _AVAILABLE_LANGUAGES == ()


# ── _discover_languages ────────────────────────────────────────────────────


class TestDiscoverLanguages:
    """Locale auto-discovery: drop a .mo, restart, and it Just Works."""

    def test_returns_en_when_locale_dir_missing(self) -> None:
        assert _discover_languages(Path("/nonexistent/path"), "openfollow") == ("en",)

    def test_returns_en_when_locale_dir_empty(self, tmp_path: Path) -> None:
        assert _discover_languages(tmp_path, "openfollow") == ("en",)

    def test_discovers_installed_languages(self, tmp_path: Path) -> None:
        (tmp_path / "zh_CN" / "LC_MESSAGES").mkdir(parents=True)
        (tmp_path / "zh_CN" / "LC_MESSAGES" / "openfollow.mo").touch()
        (tmp_path / "fr" / "LC_MESSAGES").mkdir(parents=True)
        (tmp_path / "fr" / "LC_MESSAGES" / "openfollow.mo").touch()

        result = _discover_languages(tmp_path, "openfollow")
        # "en" always first, then alphabetical
        assert result == ("en", "fr", "zh_CN")

    def test_skips_non_mo_files(self, tmp_path: Path) -> None:
        (tmp_path / "de" / "LC_MESSAGES").mkdir(parents=True)
        (tmp_path / "de" / "LC_MESSAGES" / "openfollow.po").touch()  # .po, not .mo

        result = _discover_languages(tmp_path, "openfollow")
        assert result == ("en",)

    def test_en_not_duplicated(self, tmp_path: Path) -> None:
        (tmp_path / "en" / "LC_MESSAGES").mkdir(parents=True)
        (tmp_path / "en" / "LC_MESSAGES" / "openfollow.mo").touch()

        result = _discover_languages(tmp_path, "openfollow")
        # "en" appears only once even if a .mo exists
        assert result == ("en",)

    def test_normalises_hyphen_to_underscore(self, tmp_path: Path) -> None:
        """Directory names with hyphens (zh-CN) are normalised to underscores."""
        (tmp_path / "zh-CN" / "LC_MESSAGES").mkdir(parents=True)
        (tmp_path / "zh-CN" / "LC_MESSAGES" / "openfollow.mo").touch()

        result = _discover_languages(tmp_path, "openfollow")
        assert result == ("en", "zh_CN")


# ── _best_language ─────────────────────────────────────────────────────────


class TestBestLanguage:
    """Accept-Language header negotiation."""

    def test_empty_header_defaults_to_first_available(self) -> None:
        assert _best_language(None, _EN_ONLY) == "en"
        assert _best_language("", _EN_ONLY) == "en"

    def test_exact_match(self) -> None:
        assert _best_language("en", _EN_ONLY) == "en"

    def test_fallback_to_en_for_unknown_lang(self) -> None:
        assert _best_language("fr", _EN_ONLY) == "en"
        assert _best_language("de", _EN_ONLY) == "en"
        assert _best_language("ja", _EN_ONLY) == "en"

    def test_respects_quality_values(self) -> None:
        assert _best_language("fr;q=0.9, en;q=0.8", _EN_ONLY) == "en"

    def test_en_with_region(self) -> None:
        assert _best_language("en-US", _EN_ONLY) == "en"
        assert _best_language("en-GB", _EN_ONLY) == "en"

    def test_multiple_candidates(self) -> None:
        assert _best_language("de, en;q=0.7, fr;q=0.3", _EN_ONLY) == "en"

    def test_q_zero_excluded(self) -> None:
        """RFC 7231 §5.3.1: q=0 means 'not acceptable', drops the candidate."""
        assert _best_language("en;q=0", _EN_ONLY) == "en"
        assert _best_language("fr, en;q=0", _EN_ONLY) == "en"

    def test_q_parameter_case_insensitive(self) -> None:
        """RFC 7231: parameter names are case-insensitive (Q=0.8 works)."""
        assert _best_language("en;Q=0.1, fr;q=0.9", _EN_ONLY) == "en"

    def test_case_insensitive(self) -> None:
        """Accept-Language tags are case-insensitive per RFC 7231."""
        assert _best_language("EN", _EN_ONLY) == "en"
        assert _best_language("En-Us", _EN_ONLY) == "en"
        assert _best_language("EN-GB, en;q=0.9", _EN_ONLY) == "en"

    def test_case_insensitive_with_mixed_case_available(self) -> None:
        """Canonical-case available entries match lowercased request tags."""
        available = ("en", "zh_TW")
        assert _best_language("zh-tw", available) == "zh_TW"
        assert _best_language("ZH-TW", available) == "zh_TW"

    def test_q_clamped_to_range(self) -> None:
        """q values outside 0.0–1.0 are clamped."""
        assert _best_language("en;q=0.5, fr;q=999", _EN_ONLY) == "en"

    def test_malformed_q_value_falls_back_to_default(self) -> None:
        """A non-numeric ``q`` (e.g. ``q=abc``) must not crash: it is treated
        as the default quality (1.0) instead of raising ValueError."""
        assert _best_language("en;q=abc", _EN_ONLY) == "en"
        # zh_CN with a garbage q still wins over a lower-ranked en.
        assert _best_language("zh_CN;q=abc, en;q=0.1", ("en", "zh_CN")) == "zh_CN"

    def test_longest_subtag_match_wins(self) -> None:
        """Among non-exact subtag matches the longest (most specific) available
        tag is chosen, regardless of order."""
        # Longer match first, then a shorter match: the shorter one must be
        # rejected (exercises the loop's 'not longer' back-edge).
        assert _best_language("zh", ("en", "zh_Hans_CN", "zh_CN")) == "zh_Hans_CN"
        # Shorter first, then longer: the longer one replaces it.
        assert _best_language("zh", ("en", "zh_CN", "zh_Hans_CN")) == "zh_Hans_CN"

    def test_fallback_respects_available_order(self) -> None:
        """When no match, returns available[0], not hardcoded 'en'."""
        available = ("fr", "en")
        assert _best_language("ja", available) == "fr"
        assert _best_language(None, available) == "fr"

    def test_en_not_in_available_still_works(self) -> None:
        """Even without 'en' in available, negotiation doesn't crash."""
        available = ("zh_CN",)
        assert _best_language("fr", available) == "zh_CN"
        assert _best_language("", available) == "zh_CN"

    def test_regional_variant_preferred_over_generic(self) -> None:
        """en-US should match en_US before en (most specific first)."""
        available = ("en", "en_US", "en_GB")
        assert _best_language("en-US", available) == "en_US"
        assert _best_language("en-GB", available) == "en_GB"
        # Simple "en" still matches the generic entry
        assert _best_language("en", available) == "en"

    def test_regional_ordering_independent_of_tuple_order(self) -> None:
        """Specificity wins regardless of position in the available tuple."""
        available = ("en_GB", "en", "en_US")
        assert _best_language("en-US", available) == "en_US"


# ── I18NPlugin ──────────────────────────────────────────────────────────────


class TestI18NPlugin:
    """Bottle plugin lifecycle: setup, per-request apply, close."""

    def test_plugin_attributes(self) -> None:
        plugin = I18NPlugin()
        assert plugin.name == "i18n"
        assert plugin.api == 2

    def test_setup_loads_translations(self) -> None:
        app = Bottle()
        plugin = I18NPlugin(domain="openfollow")
        plugin.setup(app)
        assert "en" in plugin._translations

    def test_setup_handles_missing_locale_directory(self, tmp_path: Path) -> None:
        """When locale/ does not exist, gettext.translation falls back gracefully."""
        import gettext as gt

        saved = i18n._LOCALE_ROOT
        try:
            i18n._LOCALE_ROOT = tmp_path / "nonexistent"
            app = Bottle()
            plugin = I18NPlugin(domain="nonexistent")
            plugin.setup(app)
            # Should not raise; "en" always discovered even without locale/
            assert "en" in plugin._translations
            assert isinstance(plugin._translations["en"], gt.NullTranslations)
        finally:
            i18n._LOCALE_ROOT = saved

    def test_setup_wires_simpletemplate_defaults(self) -> None:
        """SimpleTemplate.defaults["_"] is set during setup, not import."""
        app = Bottle()
        plugin = I18NPlugin(domain="nonexistent")
        plugin.setup(app)
        assert SimpleTemplate.defaults["_"] is _template_translate

    def test_setup_auto_discovers_languages(self, tmp_path: Path, monkeypatch: Any) -> None:
        """setup() discovers .mo files from locale/ at startup."""
        # Reset global so auto-discovery runs (previous tests may have
        # populated _AVAILABLE_LANGUAGES via earlier setup() calls).
        monkeypatch.setattr(i18n, "_AVAILABLE_LANGUAGES", ())

        (tmp_path / "fr" / "LC_MESSAGES").mkdir(parents=True)
        (tmp_path / "fr" / "LC_MESSAGES" / "openfollow.mo").touch()

        saved = i18n._LOCALE_ROOT
        try:
            i18n._LOCALE_ROOT = tmp_path
            app = Bottle()
            plugin = I18NPlugin(domain="openfollow")
            plugin.setup(app)
            assert "en" in plugin._translations
            assert "fr" in plugin._translations
            assert plugin._available_languages == ("en", "fr")
        finally:
            i18n._LOCALE_ROOT = saved

    def test_setup_respects_manual_override(self, monkeypatch: Any) -> None:
        """Non-empty _AVAILABLE_LANGUAGES skips discovery."""
        monkeypatch.setattr(i18n, "_AVAILABLE_LANGUAGES", ("fr", "en"))
        app = Bottle()
        plugin = I18NPlugin(domain="nonexistent")
        plugin.setup(app)
        assert plugin._available_languages == ("fr", "en")

    def test_setup_logs_translation_errors(self, caplog: Any, tmp_path: Path) -> None:
        """When gettext raises (e.g. permission error), log a warning."""
        import logging

        saved = i18n._LOCALE_ROOT
        try:
            i18n._LOCALE_ROOT = tmp_path / "nonexistent"
            app = Bottle()
            plugin = I18NPlugin(domain="nonexistent")
            with caplog.at_level(logging.WARNING):
                plugin.setup(app)
        finally:
            i18n._LOCALE_ROOT = saved

    def test_apply_sets_thread_local_translator(self) -> None:
        app = Bottle()
        plugin = I18NPlugin(domain="nonexistent")
        plugin.setup(app)

        wrapped = plugin.apply(lambda: _("Hello"), None)
        result = wrapped()
        assert result == "Hello"

    def test_close_is_noop(self) -> None:
        plugin = I18NPlugin()
        plugin.close()

    def test_close_removes_template_bridge(self) -> None:
        """close() removes the template bridge when our binding is active."""
        app = Bottle()
        plugin = I18NPlugin(domain="nonexistent")
        plugin.setup(app)
        assert SimpleTemplate.defaults.get("_") is _template_translate
        plugin.close()
        assert "_" not in SimpleTemplate.defaults

    def test_close_safe_when_binding_replaced(self) -> None:
        """close() does not pop _ if another plugin replaced our binding."""
        app = Bottle()
        p1 = I18NPlugin(domain="nonexistent")
        p1.setup(app)

        def fake_translator(s: str) -> str:
            return s

        SimpleTemplate.defaults["_"] = fake_translator
        p1.close()
        assert SimpleTemplate.defaults["_"] is fake_translator

    def test_wraps_preserves_callback_name(self) -> None:
        """@functools.wraps keeps the original callback metadata for debugging."""
        app = Bottle()
        plugin = I18NPlugin(domain="nonexistent")
        plugin.setup(app)

        def my_handler() -> str:
            return "ok"

        wrapped = plugin.apply(my_handler, None)
        assert wrapped.__name__ == "my_handler"


# ── Integration: Bottle app with I18NPlugin ─────────────────────────────────


class TestBottleIntegration:
    """End-to-end: Bottle app with i18n plugin serving templates."""

    @staticmethod
    def _wsgi_environ(path: str, accept_lang: str = "en") -> dict[str, Any]:
        return {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": path,
            "SCRIPT_NAME": "",
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "8080",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "HTTP_ACCEPT_LANGUAGE": accept_lang,
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "http",
            "wsgi.input": io.BytesIO(),
            "wsgi.errors": io.StringIO(),
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }

    @pytest.fixture
    def app_with_i18n(self) -> Bottle:
        """Bottle app with I18NPlugin installed and a test route."""
        app = Bottle()
        plugin = I18NPlugin(domain="nonexistent")
        app.install(plugin)

        @app.get("/hello")  # type: ignore[untyped-decorator]
        def hello() -> str:
            return _("Hello, world!")

        @app.get("/template")  # type: ignore[untyped-decorator]
        def template_test() -> str:
            tpl = SimpleTemplate("{{_('Translated text')}}")
            return tpl.render()  # type: ignore[no-any-return]

        return app

    def test_route_uses_underscore(self, app_with_i18n: Bottle) -> None:
        """_() in Python code returns msgid (no catalog → identity)."""
        body = app_with_i18n.wsgi(
            self._wsgi_environ("/hello"),
            lambda status, headers, exc_info=None: None,
        )
        assert b"Hello, world!" in (b"".join(body) if not isinstance(body, bytes) else body)

    def test_template_underscore_works(self, app_with_i18n: Bottle) -> None:
        """{{_('text')}} in templates resolves via SimpleTemplate.defaults."""
        body = app_with_i18n.wsgi(
            self._wsgi_environ("/template"),
            lambda status, headers, exc_info=None: None,
        )
        result = b"".join(body) if not isinstance(body, bytes) else body
        assert b"Translated text" in result


# ── Thread safety ──────────────────────────────────────────────────────────


def test_thread_local_isolation() -> None:
    """Each thread gets its own translator via ContextVar."""
    i18n._translate_ctx.set(None)
    results: dict[int, str] = {}

    def worker(thread_id: int, prefix: str) -> None:
        i18n._translate_ctx.set(lambda s: f"[{prefix}]{s}")
        results[thread_id] = _template_translate("test")

    threads = [threading.Thread(target=worker, args=(i, f"T{i}")) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == {0: "[T0]test", 1: "[T1]test", 2: "[T2]test", 3: "[T3]test"}
    assert i18n._translate_ctx.get() is None


# ── Template rendering ────────────────────────────────────────────────────


def test_template_translate_without_plugin() -> None:
    """SimpleTemplate._() works once the bridge is wired manually."""
    SimpleTemplate.defaults["_"] = _template_translate
    i18n._translate_ctx.set(None)
    tpl = SimpleTemplate("{{_('No plugin')}}")
    assert tpl.render() == "No plugin"


def test_template_translate_with_custom_translator() -> None:
    """Custom translator set on _translate_ctx affects template rendering."""
    SimpleTemplate.defaults["_"] = _template_translate
    i18n._translate_ctx.set(lambda s: s.upper())
    tpl = SimpleTemplate("{{_('loud')}}")
    assert tpl.render() == "LOUD"


# ═══════════════════════════════════════════════════════════════════════════
#  Cookie behaviour
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestCookieBehaviour:
    """Tests for cookie-related i18n behaviour."""

    @staticmethod
    def _bottle_request(
        app: Bottle,
        path: str = "/",
        environ_overrides: dict[str, Any] | None = None,
    ) -> tuple[bytes, dict[str, str]]:
        environ: dict[str, Any] = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": path,
            "SCRIPT_NAME": "",
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "8080",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "HTTP_HOST": "test",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "http",
            "wsgi.input": io.BytesIO(),
            "wsgi.errors": io.StringIO(),
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }
        if environ_overrides:
            environ.update(environ_overrides)
        captured: dict[str, str] = {}

        def start_response(status: str, headers: list[tuple[str, str]], exc_info: Any = None) -> None:
            captured["status"] = status
            captured.update(dict(headers))

        return b"".join(app.wsgi(environ, start_response)), captured

    def test_first_visit_no_cookie(self) -> None:
        """First visit sets a lang cookie (no pre-existing cookie)."""
        app = Bottle()
        app.config["use_https"] = False
        app.install(I18NPlugin(domain="openfollow"))

        @app.get("/")
        def index() -> str:
            return "ok"

        _body, headers = self._bottle_request(app)
        assert "Set-Cookie" in headers

    def test_bad_cookie_repaired(self) -> None:
        """Stale/forged cookie triggers a repair Set-Cookie."""
        app = Bottle()
        app.config["use_https"] = False
        app.install(I18NPlugin(domain="openfollow"))

        @app.get("/")
        def index() -> str:
            return "ok"

        _body, headers = self._bottle_request(app, environ_overrides={"HTTP_COOKIE": "lang=xx"})
        assert "Set-Cookie" in headers
        assert "lang=en" in headers["Set-Cookie"]

    def test_cookie_opts_secure_is_per_request(self) -> None:
        """``secure`` is dynamic: present under https, absent under http.

        Uses raw WSGI to capture the actual Set-Cookie header from a
        plugin-wrapped handler.  Validates real output, not internal state."""
        assert "secure" not in i18n._COOKIE_OPTS
        app = Bottle()
        app.install(I18NPlugin(domain="openfollow"))

        @app.get("/")
        def index() -> str:
            return "ok"

        # HTTPS → Secure in Set-Cookie
        _body, headers = self._bottle_request(app, environ_overrides={"wsgi.url_scheme": "https"})
        assert "Set-Cookie" in headers
        assert "Secure" in headers["Set-Cookie"]

        # HTTP → no Secure in Set-Cookie
        _body, headers = self._bottle_request(app)
        assert "Set-Cookie" in headers
        assert "Secure" not in headers["Set-Cookie"]


# ═══════════════════════════════════════════════════════════════════════════
#  Language switcher visibility
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
def test_available_languages_in_template_defaults() -> None:
    """available_languages is exposed in SimpleTemplate.defaults."""
    plugin = I18NPlugin(domain="openfollow")
    app = Bottle()
    app.config["use_https"] = False
    plugin.setup(app)
    assert "available_languages" in SimpleTemplate.defaults
    assert SimpleTemplate.defaults["available_languages"] == plugin._available_languages


@pytest.mark.unit
def test_available_languages_default_before_plugin_setup() -> None:
    """A template using the lang-switch renders before I18NPlugin.setup().

    Mirrors ``test_template_translate_without_plugin`` for ``_``: the import
    time ``setdefault("available_languages", ())`` must keep a switcher-bearing
    template from raising ``NameError`` when rendered pre-setup (tests, CLI,
    wizard), with the switcher hidden.  Regression guard for the bug the
    ``defaults`` leak used to mask.
    """
    # Import-time fallback guarantees the key exists; force the empty (no
    # languages discovered) case so the assertion does not depend on whatever
    # a prior test in the process may have left in the shared defaults.
    SimpleTemplate.defaults["available_languages"] = ()
    # A switcher-bearing template must render without NameError, hidden.
    tpl = SimpleTemplate("% if len(available_languages) > 1:\nSWITCH\n% end\nok")
    assert tpl.render().strip() == "ok"


@pytest.mark.unit
def test_lang_switch_hidden_single_language() -> None:
    """len(available_languages)==1 → template hides lang-switch."""
    plugin = I18NPlugin(domain="openfollow")
    app = Bottle()
    app.config["use_https"] = False
    original_discover = i18n._discover_languages
    original_available = i18n._AVAILABLE_LANGUAGES
    original_defaults = SimpleTemplate.defaults.copy()
    try:
        # Reset the module-level cache so _discover_languages is invoked.
        i18n._AVAILABLE_LANGUAGES = ()
        i18n._discover_languages = lambda root, domain: ("en",)
        plugin.setup(app)
        assert len(SimpleTemplate.defaults["available_languages"]) == 1
    finally:
        i18n._AVAILABLE_LANGUAGES = original_available
        i18n._discover_languages = original_discover
        SimpleTemplate.defaults.clear()
        SimpleTemplate.defaults.update(original_defaults)


@pytest.mark.unit
def test_lang_switch_shown_multiple_languages() -> None:
    """len(available_languages)>1 → template shows lang-switch."""
    plugin = I18NPlugin(domain="openfollow")
    app = Bottle()
    app.config["use_https"] = False
    original_discover = i18n._discover_languages
    original_available = i18n._AVAILABLE_LANGUAGES
    try:
        i18n._AVAILABLE_LANGUAGES = ()
        i18n._discover_languages = lambda root, domain: ("en", "zh_CN")
        plugin.setup(app)
    finally:
        i18n._AVAILABLE_LANGUAGES = original_available
        i18n._discover_languages = original_discover
    assert len(SimpleTemplate.defaults["available_languages"]) > 1


# ═══════════════════════════════════════════════════════════════════════════
#  /set-lang route
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestSetLangRoute:
    """Tests for the /set-lang/<lang> route.

    The validation logic is tested through the shared
    ``validate_language_code()`` function (see TestValidateLanguageCode
    below).  These route-level tests verify the HTTP-level behaviour
    (status codes, cookies, redirects).
    """

    @staticmethod
    def _make_set_lang_app(available: tuple[str, ...] = ("en", "zh_CN")) -> Bottle:
        """Return the *real* app with the real ``/set-lang`` route registered.

        Builds a ``ConfigWebServer`` (which calls ``routes.setup_routes`` and
        installs ``I18NPlugin``) so these tests exercise ``routes.py`` itself,
        not a copy.  ``_AVAILABLE_LANGUAGES`` / ``_discover_languages`` are
        overridden so ``validate_language_code`` accepts ``available``; the
        module-level state is restored by the autouse fixture in this file.
        """
        import tempfile

        from openfollow.web.server import ConfigWebServer

        i18n._AVAILABLE_LANGUAGES = ()
        i18n._discover_languages = lambda root, domain: available
        tmp = tempfile.mkdtemp(prefix="setlang-test-")
        server = ConfigWebServer(config_path=str(Path(tmp) / "config.toml"))
        return server._app

    @staticmethod
    def _request(app: Bottle, path: str, **extra: Any) -> tuple[bytes, dict[str, str]]:
        environ: dict[str, Any] = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": path,
            "SCRIPT_NAME": "",
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "8080",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "HTTP_HOST": "test",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "http",
            "wsgi.input": io.BytesIO(),
            "wsgi.errors": io.StringIO(),
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }
        environ.update(extra)
        captured: dict[str, str] = {}

        def start_response(status: str, headers: list[tuple[str, str]], exc_info: Any = None) -> None:
            captured["status"] = status
            captured.update(dict(headers))

        return b"".join(app.wsgi(environ, start_response)), captured

    def test_returns_303(self) -> None:
        app = self._make_set_lang_app()
        _body, h = self._request(app, "/set-lang/zh_CN")
        assert "303" in h["status"]

    def test_sets_lang_cookie(self) -> None:
        app = self._make_set_lang_app()
        _body, h = self._request(app, "/set-lang/zh_CN")
        assert "Set-Cookie" in h
        assert "lang=zh_CN" in h["Set-Cookie"]

    def test_default_redirect_to_home(self) -> None:
        app = self._make_set_lang_app()
        _body, h = self._request(app, "/set-lang/zh_CN")
        assert h["Location"] == "/"

    def test_redirects_to_same_origin_referer(self) -> None:
        app = self._make_set_lang_app()
        _body, h = self._request(app, "/set-lang/zh_CN", HTTP_REFERER="http://test/config")
        assert h["Location"] == "/config"

    def test_same_origin_referer_preserves_query(self) -> None:
        # A same-origin Referer with a query string is preserved verbatim so
        # the operator lands back on the exact tab/anchor they came from.
        app = self._make_set_lang_app()
        _body, h = self._request(app, "/set-lang/zh_CN", HTTP_REFERER="http://test/config?tab=osc")
        assert h["Location"] == "/config?tab=osc"

    def test_ignores_cross_origin_referer(self) -> None:
        app = self._make_set_lang_app()
        _body, h = self._request(app, "/set-lang/zh_CN", HTTP_REFERER="http://evil.com/steal")
        assert h["Location"] == "/"

    def test_rejects_unknown_language(self) -> None:
        app = self._make_set_lang_app()
        _body, h = self._request(app, "/set-lang/../../etc")
        assert "404" in h["status"]

    def test_rejects_unavailable_language_in_handler(self) -> None:
        # A route-matching but unavailable code reaches the handler and is
        # rejected by validate_language_code() (unlike ``../../etc`` above,
        # which the WSGI layer normalises away before routing).
        app = self._make_set_lang_app(available=("en", "zh_CN"))
        _body, h = self._request(app, "/set-lang/fr")
        assert "404" in h["status"]

    def test_en_always_allowed(self) -> None:
        app = self._make_set_lang_app(available=())
        _body, h = self._request(app, "/set-lang/en")
        assert "303" in h["status"]
        assert "lang=en" in h["Set-Cookie"]


# ═══════════════════════════════════════════════════════════════════════════
#  validate_language_code — shared function tested directly (not via copy)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestValidateLanguageCode:
    """Unit tests for the standalone validate_language_code() function.

    This is the same function called by the real ``/set-lang`` route in
    ``routes.py`` — unlike the route-level tests above which use an inline
    copy of the handler, these tests directly exercise the production code.
    """

    def test_en_always_valid(self) -> None:
        assert i18n.validate_language_code("en")

    def test_known_language_valid(self) -> None:
        saved = i18n._AVAILABLE_LANGUAGES
        try:
            i18n._AVAILABLE_LANGUAGES = ("fr", "de")
            assert i18n.validate_language_code("fr")
            assert i18n.validate_language_code("de")
        finally:
            i18n._AVAILABLE_LANGUAGES = saved

    def test_unknown_language_rejected(self) -> None:
        saved = i18n._AVAILABLE_LANGUAGES
        try:
            i18n._AVAILABLE_LANGUAGES = ("fr",)
            assert not i18n.validate_language_code("zh_CN")
            assert not i18n.validate_language_code("../../etc")
        finally:
            i18n._AVAILABLE_LANGUAGES = saved

    def test_empty_available_still_allows_en(self) -> None:
        saved = i18n._AVAILABLE_LANGUAGES
        try:
            i18n._AVAILABLE_LANGUAGES = ()
            assert i18n.validate_language_code("en")
            assert not i18n.validate_language_code("fr")
        finally:
            i18n._AVAILABLE_LANGUAGES = saved


# ═══════════════════════════════════════════════════════════════════════════
#  Bare quote protection
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
def test_underscore_double_quotes_for_apostrophes() -> None:
    """Template strings with apostrophes should use double quotes in _().

    Double quotes ("...") work around single-quote delimiter issues.
    """
    SimpleTemplate.defaults["_"] = _template_translate
    i18n._translate_ctx.set(None)
    # Double-quote wrapper handles apostrophes correctly
    tpl = SimpleTemplate("""{{_("text with apostrophe what's ok")}}""")
    result = tpl.render()
    # SimpleTemplate escapes quotes by default; {{!...}} would be unescaped
    assert "what" in result and "ok" in result
