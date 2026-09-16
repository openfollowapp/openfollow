# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Credential handling for media URIs.

Stream URLs carry credentials inline – RTSP userinfo (``rtsp://user:pass@host``)
and the SRT ``?passphrase=`` query – so every surface that displays, logs, or
exports one has to strip them first. This lives outside both the video and web
packages because both reach for it: the plugins for labels and their own log
lines, the receiver for the status marker that feeds the HUD and ``/api/stats``,
and the diagnostics bundle for its config dump and log tail.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

# What replaces a credential. Deliberately not passed through any URL encoder:
# an operator reading a bundle must see it announce itself as a redaction, not
# a ``%2A%2A%2A`` token that could be misread as the real value.
REDACTION = "***"

# Query parameters whose *value* is a credential. The key stays visible – that
# a passphrase is set is the diagnostic, the passphrase itself never is.
_REDACTED_QUERY_KEYS = frozenset({"passphrase", "streamid"})

# ``scheme://user:pass@host`` anywhere in free text.
#
# The run stops at the first ``/``, ``?``, ``#`` or space, so it stays inside
# one URI's authority: two URIs adjacent on one line match independently rather
# than being swallowed as a single run, an ``@`` in a path or query is not
# mistaken for userinfo, and an SRT ``?passphrase=p@ss`` is left for the query
# rule below. Within the authority it is greedy, so it consumes through to the
# *last* ``@`` - a password containing an unencoded ``@``
# (``rtsp://operator:p@ss@cam/s``) would otherwise leave ``ss@`` behind and
# publish half the secret.
_USERINFO_IN_TEXT_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^/?#\s]*@")

# The schemeless ``user:pass@host/path`` shorthand, which ``strip_uri_userinfo``
# also accepts, as it appears in free text. The ``:`` is what keeps an ordinary
# ``someone@example.com`` out: an address has no colon-separated pair before the
# ``@``, a credential always does. ``?`` and ``#`` are excluded for the same
# reason they are above - without them an SRT ``?passphrase=p@ss`` reads as a
# host:port pair followed by userinfo, and the query rule never gets to mask it.
#
# What follows the ``@`` has to look like a host - dotted, or followed by a path.
# Deleting a run is destructive, and plenty of ordinary log content has the same
# ``token:token@token`` shape: an aspect ratio and refresh (``16:9@60Hz``) or a
# timestamped event (``10:30:00@host``) would otherwise be eaten out of a
# diagnostics bundle. The cost is a credential against a bare, dotless host with
# no path, which the structural helpers below still strip.
_SCHEMELESS_USERINFO_IN_TEXT_RE = re.compile(
    r"(?<![\w@.+-])[^\s:@/?#]+:[^\s@/?#]*@(?=[^\s@/?#]*\.[^\s@/?#]|[^\s@/?#]+/)",
)

# ``scheme://user:pa/ss@host`` in free text - a password holding an unencoded
# ``/``. The scheme rule above stops at the first ``/`` and the schemeless rule
# excludes one, so this shape slipped past both and published the credential
# verbatim, while ``redact_uri`` on the same string fails closed.
#
# Catching it needs a run that crosses slashes, which also spans an ordinary
# ``@`` in a path, so the authority decides which it is: a numeric port
# (``cam.local:554/p@th``) is a real host and is left alone, anything else in
# that position (``operator:pa/ss@cam.local``) is userinfo and goes.
# The run is tempered so it cannot cross a second ``://``: without that, two
# URIs separated by anything but whitespace are spanned as one and the first
# authority decides for both - leaving the second URI's credential intact, or
# deleting a clean second URI because the first looked like userinfo.
_SCHEME_RUN_TO_AT_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)((?:(?!://)[^\s?#])*)@")


def _strip_scheme_userinfo_across_slash(text: str) -> str:
    """Drop ``scheme://user:pa/ss@`` runs the authority shows to be userinfo."""

    def _replace(match: re.Match[str]) -> str:
        scheme, run = match.group(1), match.group(2)
        authority = run.partition("/")[0]
        if authority.startswith("["):
            # An IPv6 literal is all colons, so the port test below reads one
            # as a password and eats a URI that carried no credential. RFC 3986
            # keeps ``[`` out of userinfo entirely and reserves it for an
            # IP-literal, so a leading bracket settles it without having to
            # parse the address - which matters, because an *unterminated*
            # bracket is exactly where the colon test did its damage.
            return match.group(0)
        _host, sep, port = authority.partition(":")
        if sep and not port.isdigit():
            return scheme
        return match.group(0)

    return _SCHEME_RUN_TO_AT_RE.sub(_replace, text)


# A secret query value in free text, running to the next separator.
_SECRET_QUERY_IN_TEXT_RE = re.compile(
    r"(?i)([?&](?:" + "|".join(sorted(_REDACTED_QUERY_KEYS)) + r")=)[^&\s\"'<>]*",
)


def _split_query(uri: str) -> tuple[str, str, str, str]:
    """Split ``uri`` into ``(head, query, fragment_sep, fragment)``.

    Textual rather than via ``urlsplit`` + ``urlencode``: a round trip through
    the encoder rewrites every parameter it touches, so an SRT
    ``?streamid=r=0,m=request`` would reach the element percent-escaped purely
    because an unrelated field was filled in.
    """
    head, sep, rest = uri.partition("?")
    if not sep:
        return uri, "", "", ""
    query, hash_sep, fragment = rest.partition("#")
    return head, query, hash_sep, fragment


def strip_uri_userinfo(uri: str) -> str:
    """Return ``uri`` with any ``user:pass@`` prefix removed from its authority.

    Hands a bare location to an element that authenticates from explicit
    credential properties instead: ``rtspsrc`` tries URL userinfo first and
    only falls back to ``user-id`` / ``user-pw``, so a credential left in the
    URL would outrank the one the operator typed into the form.

    A schemeless ``user:pass@host/s`` (common RTSP shorthand) is handled too –
    ``urlsplit`` parses its userinfo as a bogus scheme with an empty netloc,
    so a plain pass-through would leave the password in place.
    """
    try:
        parts = urlsplit(uri)
    except ValueError:
        # Malformed authority (an unterminated IPv6 literal, ``rtsp://[::1``).
        # There is no parse to work from, but the string still has to be safe
        # to print, so strip the userinfo textually. The schemeless branch
        # below can't serve here: it splits on the first ``/``, which for a
        # ``scheme://`` URI falls before the credentials rather than after.
        return _USERINFO_IN_TEXT_RE.sub(r"\1", uri)
    if not parts.netloc:
        prefix, slash, rest = uri.partition("/")
        if "@" in prefix:
            return prefix.rsplit("@", 1)[-1] + slash + rest
        return uri
    if "@" not in parts.netloc:
        return uri
    netloc = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def strip_uri_query_key(uri: str, key: str) -> str:
    """Return ``uri`` without ``key`` (case-insensitive) in its query string.

    Every surviving parameter is preserved byte for byte – see ``_split_query``.
    """
    head, query, hash_sep, fragment = _split_query(uri)
    if not query:
        return uri
    kept = [p for p in query.split("&") if p.partition("=")[0].lower() != key.lower()]
    new_query = f"?{'&'.join(kept)}" if kept else ""
    return f"{head}{new_query}{hash_sep}{fragment}"


def redact_uri(uri: str) -> str:
    """Strip inline credentials from a media URI so it is safe to log/display.

    ``rtsp://user:pass@host:554/s`` → ``rtsp://host:554/s``; SRT
    ``?passphrase=..`` / ``?streamid=..`` values become ``***``. Everything
    that is not a credential – host, port, path, other query parameters –
    survives, or the result stops being useful for diagnosing the connection
    it describes.
    """
    stripped = strip_uri_userinfo(uri)
    scheme, sep, _rest = stripped.partition("://")
    head, query, hash_sep, fragment = _split_query(stripped)

    # A credential is only provably safe inside the value of a key we are
    # about to mask. Everywhere else an ``@`` is unprovable and fails closed:
    # a password holding a ``/``, ``#`` or ``?`` scatters the rest of itself
    # into the path, the fragment or the query respectively, and no parse can
    # tell the difference from a URI that never had a credential.
    unsafe_at = "@" in head.partition("://")[2] or "@" in fragment
    masked: list[str] = []
    masked_a_secret = False
    for part in query.split("&") if query else []:
        key, eq, _value = part.partition("=")
        if eq and key.lower() in _REDACTED_QUERY_KEYS:
            masked.append(f"{key}={REDACTION}")
            masked_a_secret = True
            continue
        unsafe_at = unsafe_at or "@" in part
        masked.append(part)

    if sep and unsafe_at:
        return f"{scheme}://{REDACTION}"
    if not query:
        return stripped
    if masked_a_secret and fragment:
        # The same ambiguity one step on: ``?passphrase=show#act2026`` is a
        # passphrase containing a ``#`` as readily as a value plus a fragment,
        # and printing the tail publishes half the secret. The separator stays
        # so the reader can see something was dropped.
        return f"{head}?{'&'.join(masked)}{hash_sep}{REDACTION}"
    return f"{head}?{'&'.join(masked)}{hash_sep}{fragment}"


def redact_uris_in_text(text: str) -> str:
    """Strip credentials from every media URI embedded in free text.

    For log lines and error messages, where a URI is one token among many and
    may not be the last. Our own logging redacts before it formats, but
    GStreamer's does not: an ``rtspsrc`` failure carries the full ``location``
    in its debug string, and an auth failure is both the condition that puts it
    there and the condition that makes an operator send us a bundle.
    """
    # Cross-slash first. The rule below stops at the first ``/``, so on a
    # password holding both ``@`` and ``/`` it truncates there and leaves
    # nothing with a colon for the cross-slash rule to recognise - publishing
    # most of the password. Run the greedier rule while the whole run is still
    # intact; whatever it declines (userinfo with no colon at all) still falls
    # to the simpler one after.
    text = _strip_scheme_userinfo_across_slash(text)
    text = _USERINFO_IN_TEXT_RE.sub(r"\1", text)
    # After the scheme rules, a ``scheme://`` URI has no userinfo left, so this
    # only ever sees the schemeless form.
    text = _SCHEMELESS_USERINFO_IN_TEXT_RE.sub("", text)
    return _SECRET_QUERY_IN_TEXT_RE.sub(r"\g<1>" + REDACTION, text)
