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
        """"en_US" matches "en" because "en" is the primary subtag."""
        assert _subtag_match("en_us", "en") is True
        assert _subtag_match("zh_tw", "zh") is True

    def test_requested_longer_than_available(self) -> None:
        """"en" matches "en_US" — browser wants specific, we have generic."""
        assert _subtag_match("en", "en_us") is True
        assert _subtag_match("zh", "zh_tw") is True

    def test_no_boundary_no_match(self) -> None:
        """"english" should NOT match "en" — no subtag boundary at position 2."""
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

    def test_setup_auto_discovers_languages(self, tmp_path: Path) -> None:
        """setup() discovers .mo files from locale/ at startup."""
        (tmp_path / "zh_CN" / "LC_MESSAGES").mkdir(parents=True)
        (tmp_path / "zh_CN" / "LC_MESSAGES" / "openfollow.mo").touch()

        saved = i18n._LOCALE_ROOT
        try:
            i18n._LOCALE_ROOT = tmp_path
            app = Bottle()
            plugin = I18NPlugin(domain="openfollow")
            plugin.setup(app)
            assert "en" in plugin._translations
            assert "zh_CN" in plugin._translations
            assert plugin._available_languages == ("en", "zh_CN")
        finally:
            i18n._LOCALE_ROOT = saved

    def test_setup_respects_manual_override(self, monkeypatch: Any) -> None:
        """Non-empty _AVAILABLE_LANGUAGES skips discovery."""
        monkeypatch.setattr(i18n, "_AVAILABLE_LANGUAGES", ("fr", "en"))
        app = Bottle()
        plugin = I18NPlugin(domain="nonexistent")
        plugin.setup(app)
        assert plugin._available_languages == ("fr", "en")

    def test_setup_sets_secure_cookie_flag_when_https(self) -> None:
        """When app.config.use_https is True, instance cookie opts get secure=True."""
        app = Bottle()
        app.config["use_https"] = True
        plugin = I18NPlugin(domain="nonexistent")
        plugin.setup(app)
        assert plugin._cookie_opts.get("secure") is True
        assert "secure" not in i18n._COOKIE_OPTS

    def test_setup_no_secure_flag_when_http(self) -> None:
        """When app.config lacks use_https, secure is explicitly False."""
        app = Bottle()
        plugin = I18NPlugin(domain="nonexistent")
        plugin.setup(app)
        assert plugin._cookie_opts.get("secure") is False

    def test_secure_flag_isolated_between_instances(self) -> None:
        """secure flag on one instance does not leak to another."""
        app1 = Bottle()
        app1.config["use_https"] = True
        p1 = I18NPlugin(domain="nonexistent")
        p1.setup(app1)

        app2 = Bottle()
        p2 = I18NPlugin(domain="nonexistent")
        p2.setup(app2)

        assert p1._cookie_opts.get("secure") is True
        assert p2._cookie_opts.get("secure") is False

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
            "wsgi.errors": io.BytesIO(),
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

    threads = [
        threading.Thread(target=worker, args=(i, f"T{i}"))
        for i in range(4)
    ]
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
