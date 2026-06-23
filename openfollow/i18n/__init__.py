# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Internationalisation (i18n) support for the OpenFollow config web UI.

How it works
------------
1. ``I18NPlugin`` is a Bottle plugin that runs before every request.
   It reads the operator's preferred language from a ``lang`` cookie,
   falling back to the browser's ``Accept-Language`` header.

2. The plugin sets a per-context ``gettext`` translator so that
   every template rendered in that request sees the right language.

3. Templates call ``{{_('Some English text')}}``.  Bottle's
   ``SimpleTemplate`` resolves ``_`` from its ``defaults`` dict, which
   points back to the per-context translator.  The bridge is wired
   during ``I18NPlugin.setup()`` so it is scoped to plugin lifecycle.

4. ``locale/<lang>/LC_MESSAGES/openfollow.mo`` are compiled gettext
   catalogs.  Adding a new language is just: drop in a .mo file and
   restart.  The framework auto-discovers ``.mo`` files; no code
   changes needed.

Why not ``gettext.install()``?
    Bottle's ``SimpleTemplate`` executes in a sandbox that does *not*
    expose Python builtins.  ``gettext.install()`` rebinds the builtin
    ``_`` globally, which Bottle templates cannot see.  The context-
    variable bridge avoids this without a global mutex.
"""

from __future__ import annotations

import functools
import gettext
import logging
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from bottle import SimpleTemplate, request, response

logger = logging.getLogger(__name__)

_LOCALE_ROOT = Path(__file__).resolve().parent.parent.parent / "locale"

_translate_ctx: ContextVar[Any] = ContextVar("i18n_translate", default=None)

# Base cookie options shared across all I18NPlugin instances.  The ``secure``
# flag is per-instance, derived from ``app.config["use_https"]`` in setup().
_COOKIE_OPTS = {
    "path": "/",
    "max_age": 86400 * 365,
    "httponly": True,
    "samesite": "Lax",
}


def _template_translate(message: str) -> str:
    """Bridge for SimpleTemplate.defaults — reads per-context translator."""
    translate: Any = _translate_ctx.get()
    if translate is None:
        return message
    return str(translate(message))


# -- Lazy (deferred) translation strings ----------------------------------
# Pattern borrowed from Django's ``gettext_lazy``: class-level constants
# are defined with ``_l("USB Camera")`` but only resolved to a translated
# string when ``str()`` is called on them (i.e. at request-time, after the
# context-local translator has been set).  This avoids forcing every plugin
# ``display_name`` into a ``@classmethod`` (which would ripple ~50 call
# sites).
#
# _LazyString equality is deliberately conservative: ``__eq__`` compares
# the untranslated *msgid* only, never the resolved translation.  This
# keeps ``__eq__`` and ``__hash__`` consistent (a == b ⇒ hash(a) == hash(b))
# and avoids the trap of matching by translated text which changes at
# runtime.  Calling code that needs a translated comparison can call
# ``str()`` on both sides first.
class _LazyString:
    __slots__ = ("_message",)

    def __init__(self, message: str) -> None:
        self._message = message

    def __str__(self) -> str:
        return _template_translate(self._message)

    def __repr__(self) -> str:
        return repr(str(self))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _LazyString):
            return self._message == other._message
        if isinstance(other, str):
            return self._message == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._message)

    def __mod__(self, rhs: object) -> str:
        return str(self) % rhs

    def __add__(self, other: object) -> str:
        return str(self) + str(other)

    def __radd__(self, other: object) -> str:
        return str(other) + str(self)


def lazy_gettext(message: str) -> _LazyString:
    """Deferred-translation factory.

    Use *lazy_gettext* (aliased as ``_l``) for module-level / class-level
    strings that must be importable before the first request (e.g. plugin
    ``display_name`` class attributes).  The actual translation is resolved
    lazily when ``str()`` is called.

    For immediate (request-scoped) strings use ``_()`` in templates or
    ``from openfollow.i18n import _`` in Python code.
    """
    return _LazyString(message)


# Convenience aliases
_l = lazy_gettext   # for class-level (import-time) strings
def _(message: str) -> str:
    """Immediate translation for per-request Python code."""
    return _template_translate(message)

# Framework ships English-only.  Language pack maintainers drop a .mo under
# locale/<code>/LC_MESSAGES/ — the framework auto-discovers it at startup.
#
# Set to a non-empty tuple to override discovery and lock the language list.
# NOTE: if "en" is omitted from the override, available[0] becomes the
# fallback language.  msgids still render as-is (English) via NullTranslations
# when no translation catalog matches, so it is safe but unintuitive.
_AVAILABLE_LANGUAGES: tuple[str, ...] = ()


def _discover_languages(locale_root: Path, domain: str) -> tuple[str, ...]:
    """Scan locale/ for .mo files — drop a .mo, restart, and it Just Works.

    Directory names are normalised (``-`` → ``_``) so ``locale/zh-CN/`` and
    ``locale/zh_CN/`` are both matched against ``Accept-Language: zh-CN``.
    The physical directory on disk MUST use the underscore form so
    ``gettext.translation`` can resolve the path.
    """
    found: set[str] = set()
    if locale_root.is_dir():
        for mo in locale_root.glob(f"*/LC_MESSAGES/{domain}.mo"):
            found.add(mo.parent.parent.name.replace("-", "_"))
    found.add("en")  # source language never needs a .mo
    return ("en",) + tuple(sorted(found - {"en"}))


def _subtag_match(available: str, requested: str) -> bool:
    """RFC 5646 boundary-safe language tag prefix match.

    ``"en_US".startswith("en")`` is correct because ``en`` is the primary
    subtag of ``en_US``.  But ``"english".startswith("en")`` is a false
    positive — ``"english"`` is not a valid BCP 47 tag and the ``en`` prefix
    does not sit on a subtag boundary.  This helper only returns True when
    the shorter tag ends at a subtag separator (``_``).
    """
    if available == requested:
        return True
    if len(available) > len(requested):
        # e.g. available="en_US", requested="en" — check boundary after prefix
        return available.startswith(requested) and available[len(requested):len(requested)+1] == "_"
    else:
        # e.g. available="en", requested="en_US" — check boundary after available
        return requested.startswith(available) and requested[len(available):len(available)+1] == "_"


def _best_language(accept_lang_header: str | None, available: tuple[str, ...]) -> str:
    """Pick the best language from the Accept-Language header.

    Pure function — no global state.  Tests can pass any ``available`` tuple.
    """
    if not accept_lang_header:
        return available[0]
    candidates: list[tuple[float, str]] = []
    for part in accept_lang_header.split(","):
        part = part.strip()
        if ";" in part:
            tag, _, qs = part.partition(";")
            try:
                q = float(qs.strip().lower().removeprefix("q="))
                # RFC 7231 §5.3.1: valid range is 0.000–1.000.
                q = max(0.0, min(1.0, q))
            except ValueError:
                q = 1.0
        else:
            tag = part
            q = 1.0
        # RFC 7231 §5.3.1: q=0 (and clamped-to-zero) means "not acceptable".
        if q <= 0.0:
            continue
        candidates.append((q, tag.strip().replace("-", "_").lower()))
    candidates.sort(key=lambda x: x[0], reverse=True)
    for _q, tag in candidates:
        best_match: str | None = None
        for lang in available:
            if _subtag_match(lang.lower(), tag):
                # Exact match wins immediately.
                if lang.lower() == tag:
                    return lang
                # Otherwise pick the longest (most specific) match.
                if best_match is None or len(lang) > len(best_match):
                    best_match = lang
        if best_match is not None:
            return best_match
    return available[0]


class I18NPlugin:
    name = "i18n"
    api = 2

    def __init__(self, domain: str = "openfollow") -> None:
        self.domain = domain
        self._cookie_opts: dict[str, Any] = dict(_COOKIE_OPTS)
        self._translations: dict[str, gettext.NullTranslations] = {}
        self._available_languages: tuple[str, ...] = ()

    def setup(self, app: Any) -> None:
        # Auto-discover available languages from .mo files on disk.
        # Set _AVAILABLE_LANGUAGES to a non-empty tuple to override
        # discovery and lock the language list manually.
        if _AVAILABLE_LANGUAGES:
            self._available_languages = _AVAILABLE_LANGUAGES
        else:
            self._available_languages = _discover_languages(_LOCALE_ROOT, self.domain)

        # Wire the template bridge during plugin setup so it is scoped to
        # the plugin lifecycle rather than module import time.
        SimpleTemplate.defaults["_"] = _template_translate

        # Respect the deployment scheme: set the cookie ``secure`` flag when
        # the app is served over HTTPS.  Unconditional assignment eliminates
        # the one-way ratchet if setup() is called more than once.
        self._cookie_opts["secure"] = bool(app.config.get("use_https", False))

        for lang in self._available_languages:
            try:
                trans = gettext.translation(
                    self.domain,
                    localedir=str(_LOCALE_ROOT),
                    languages=[lang],
                    fallback=True,
                )
            except Exception as exc:
                logger.warning(
                    "i18n: failed to load translations for %r: %s", lang, exc
                )
                trans = gettext.NullTranslations()
            self._translations[lang] = trans

    def apply(self, callback: Any, route: Any) -> Any:
        cookie_opts = self._cookie_opts

        available = self._available_languages

        @functools.wraps(callback)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            lang = request.get_cookie("lang")
            if not lang:
                lang = _best_language(request.headers.get("Accept-Language"), available)
                response.set_cookie("lang", lang, **cookie_opts)
            if lang not in self._translations:
                lang = available[0]
                # Repair the cookie so the next request doesn't repeat the
                # silent fallback (stale / forged cookie after language removal).
                response.set_cookie("lang", lang, **cookie_opts)
            token = _translate_ctx.set(self._translations[lang].gettext)
            try:
                return callback(*args, **kwargs)
            finally:
                _translate_ctx.reset(token)
        return wrapper

    def close(self) -> None:
        # Only remove the template bridge if our binding is still the
        # active one — another plugin instance in the same process may
        # have installed its own (multi-app / test suite scenarios).
        if SimpleTemplate.defaults.get("_") is _template_translate:
            SimpleTemplate.defaults.pop("_", None)
