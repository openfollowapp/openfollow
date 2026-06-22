# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Internationalisation (i18n) support for the OpenFollow config web UI.

How it works
------------
1. ``I18NPlugin`` is a Bottle plugin that runs before every request.
   It reads the operator's preferred language from a ``lang`` cookie,
   falling back to the browser's ``Accept-Language`` header.

2. The plugin sets a thread-local ``gettext`` translator so that
   every template rendered in that request sees the right language.

3. Templates call ``{{_('Some English text')}}``.  Bottle's
   ``SimpleTemplate`` resolves ``_`` from its ``defaults`` dict, which
   points back to the thread-local translator.

4. ``locale/<lang>/LC_MESSAGES/openfollow.mo`` are compiled gettext
   catalogs.  Adding a new language is just: drop in a .mo file,
   update ``_AVAILABLE_LANGUAGES``, and add a lang-switch link in
   ``base.tpl``.

Why not ``gettext.install()``?
    Bottle's ``SimpleTemplate`` executes in a sandbox that does *not*
    expose Python builtins.  ``gettext.install()`` rebinds the builtin
    ``_`` globally, which Bottle templates cannot see.  The thread-local
    bridge avoids this without a global mutex.
"""

from __future__ import annotations

import gettext
import logging
import threading
from pathlib import Path
from typing import Any

from bottle import SimpleTemplate, request, response

logger = logging.getLogger(__name__)

_LOCALE_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "locale"

_tls = threading.local()

def _template_translate(message: str) -> str:
    """Bridge for SimpleTemplate.defaults — reads per-thread translator."""
    translate = getattr(_tls, '_translate', None)
    if translate is None:
        return message
    return translate(message)

SimpleTemplate.defaults["_"] = _template_translate


# -- Lazy (deferred) translation strings ----------------------------------
# Pattern borrowed from Django's ``gettext_lazy``: class-level constants
# are defined with ``_l("USB Camera")`` but only resolved to a translated
# string when ``str()`` is called on them (i.e. at request-time, after the
# thread-local translator has been set).  This avoids forcing every plugin
# ``display_name`` into a ``@classmethod`` (which would ripple ~50 call
# sites).
#
# Known limitation: ``__hash__`` uses the untranslated *msgid*, not the
# resolved translation, so mixing translated strings and ``_LazyString``
# in a single ``set``/``dict`` can produce lookup misses.  In this project
# ``display_name`` is only used for presentation (log messages, drop-down
# labels, HTML text nodes) – never as a collection key, so the risk is
# acceptable.
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
        return str(self) == str(other)

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

_AVAILABLE_LANGUAGES: tuple[str, ...] = ("en", "zh_CN")


def _best_language(accept_lang_header: str | None) -> str:
    if not accept_lang_header:
        return "en"
    candidates: list[tuple[float, str]] = []
    for part in accept_lang_header.split(","):
        part = part.strip()
        if ";" in part:
            tag, _, qs = part.partition(";")
            try:
                q = float(qs.strip().removeprefix("q="))
            except ValueError:
                q = 1.0
        else:
            tag = part
            q = 1.0
        candidates.append((q, tag.strip().replace("-", "_")))
    candidates.sort(key=lambda x: x[0], reverse=True)
    for _q, tag in candidates:
        if tag in _AVAILABLE_LANGUAGES:
            return tag
        for lang in _AVAILABLE_LANGUAGES:
            if lang.startswith(tag) or tag.startswith(lang):
                return lang
    return "en"


class I18NPlugin:
    name = "i18n"
    api = 2

    def __init__(self, domain: str = "openfollow") -> None:
        self.domain = domain
        self._translations: dict[str, gettext.NullTranslations] = {}

    def setup(self, app: Any) -> None:
        for lang in _AVAILABLE_LANGUAGES:
            try:
                trans = gettext.translation(
                    self.domain,
                    localedir=str(_LOCALE_ROOT),
                    languages=[lang],
                    fallback=True,
                )
            except Exception:
                trans = gettext.NullTranslations()
            self._translations[lang] = trans

    def apply(self, callback: Any, route: Any) -> Any:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            lang = request.get_cookie("lang")
            if not lang:
                lang = _best_language(request.headers.get("Accept-Language"))
                response.set_cookie("lang", lang, path="/", max_age=86400 * 365)
            if lang not in self._translations:
                lang = "en"
            _tls._translate = self._translations[lang].gettext
            try:
                return callback(*args, **kwargs)
            finally:
                _tls._translate = lambda s: s
        return wrapper

    def close(self) -> None:
        pass
