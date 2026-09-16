# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for ``openfollow.uri_redaction``.

Every surface that displays, logs, or exports a stream URL goes through these
helpers, so a gap here leaks a camera password onto the projected HUD, into
``/api/stats``, or into a diagnostics bundle attached to a public issue report.
"""

from __future__ import annotations

import pytest

from openfollow.uri_redaction import (
    redact_uri,
    redact_uris_in_text,
    strip_uri_query_key,
    strip_uri_userinfo,
)

pytestmark = pytest.mark.unit


class TestRedactUri:
    def test_strips_inline_credentials(self) -> None:
        assert redact_uri("rtsp://user:pass@cam.local:554/h264") == "rtsp://cam.local:554/h264"

    def test_masks_srt_query_secrets_keeps_host(self) -> None:
        out = redact_uri("srt://host:5000?streamid=r=0&passphrase=secret&latency=20")
        assert "secret" not in out
        assert "r=0" not in out
        assert "host:5000" in out
        assert "latency=20" in out

    def test_the_mask_announces_itself(self) -> None:
        """``***`` reaches the operator literally.

        The mask is rendered in the bundle's config dump and in log lines. Put
        through a URL encoder it becomes ``%2A%2A%2A``, which reads as an opaque
        token rather than a redaction - and could be mistaken for the real
        value by someone triaging the bundle.
        """
        assert redact_uri("srt://h:1600?passphrase=secret") == "srt://h:1600?passphrase=***"

    def test_passes_through_credential_free_and_schemeless(self) -> None:
        assert redact_uri("rtsp://cam:554/stream") == "rtsp://cam:554/stream"  # no creds
        assert redact_uri("not-a-uri") == "not-a-uri"  # no scheme
        assert redact_uri("file:///x") == "file:///x"  # no netloc
        assert redact_uri("host:554/path") == "host:554/path"  # bare host:port, no userinfo

    def test_strips_credentials_from_schemeless_shorthand(self) -> None:
        # urlsplit reads the userinfo as a bogus scheme + empty netloc; a plain
        # pass-through would have logged/displayed the password verbatim.
        assert redact_uri("user:pass@192.168.0.1/stream") == "192.168.0.1/stream"
        assert redact_uri("admin:hunter2@cam.local:554") == "cam.local:554"

    def test_a_password_containing_an_at_sign_is_fully_stripped(self) -> None:
        assert redact_uri("rtsp://operator:p@ss@cam.local:554/s") == "rtsp://cam.local:554/s"

    @pytest.mark.parametrize(
        "uri",
        ["rtsp://user:pa/ss@cam.local/stream", "rtsp://cam.local:554/path@2x"],
    )
    def test_an_unstrippable_at_sign_fails_closed(self, uri: str) -> None:
        """A ``/`` in a password lands the userinfo in the *path*
        (``rtsp://user:pa/ss@cam/s`` splits as netloc ``user:pa``), and nothing
        distinguishes that from a genuine ``@`` in a path. This feeds the HUD
        label, our own logging and the bundle's config dump, so the unprovable
        case must not print the string it cannot vouch for.
        """
        out = redact_uri(uri)
        assert out == "rtsp://***"
        assert "@" not in out

    def test_a_clean_uri_is_not_swept_up_by_the_fail_closed_rule(self) -> None:
        assert redact_uri("rtsp://user:pass@cam/s") == "rtsp://cam/s"
        assert redact_uri("srt://h:5000?passphrase=x") == "srt://h:5000?passphrase=***"

    def test_drops_a_fragment_that_trails_a_masked_secret(self) -> None:
        """Deliberately reversed. This asserted the fragment survived, which
        assumes a parse can tell ``?passphrase=x#frag`` (value plus fragment)
        from a passphrase that simply contains a ``#`` - it cannot, and the
        module's rule for exactly that ambiguity elsewhere is to fail closed.
        Neither RTSP nor SRT gives a fragment any meaning, so the cost of
        dropping it is nil against publishing half a passphrase."""
        assert redact_uri("rtsp://u:p@h/s?passphrase=x#frag") == "rtsp://h/s?passphrase=***#***"

    def test_keeps_a_fragment_when_no_secret_was_masked(self) -> None:
        assert redact_uri("rtsp://u:p@h/s#frag") == "rtsp://h/s#frag"


class TestStripUriUserinfo:
    @pytest.mark.parametrize(
        "uri, expected",
        [
            ("rtsp://u:p@cam:554/s", "rtsp://cam:554/s"),
            ("u:p@cam:554/s", "cam:554/s"),
            ("rtsp://cam:554/s", "rtsp://cam:554/s"),
        ],
    )
    def test_strips_only_the_userinfo(self, uri: str, expected: str) -> None:
        assert strip_uri_userinfo(uri) == expected

    def test_a_password_containing_an_at_sign_is_fully_stripped(self) -> None:
        assert strip_uri_userinfo("rtsp://operator:p@ss@cam.local/s") == "rtsp://cam.local/s"

    @pytest.mark.parametrize("uri", ["rtsp://[::1", "rtsp://admin:hunter2@[::1"])
    def test_a_malformed_authority_still_comes_back_safe(self, uri: str) -> None:
        """``urlsplit`` raises ``ValueError`` on an unterminated IPv6 literal.

        Returning the input untouched would print whatever it holds, so the
        malformed case falls back to the schemeless handling instead.
        """
        out = strip_uri_userinfo(uri)
        assert "hunter2" not in out


class TestStripUriQueryKey:
    def test_removes_only_the_named_key(self) -> None:
        assert strip_uri_query_key("srt://h:5000?passphrase=x&latency=20", "passphrase") == "srt://h:5000?latency=20"

    def test_preserves_every_other_parameter_byte_for_byte(self) -> None:
        """An SRT ``streamid`` carries its own ``=`` and ``,`` separators.

        Rebuilding the query through a URL encoder would hand the element
        ``streamid=r%3D0%2Cm%3Drequest`` - a different URI from the one in
        ``config.toml``, and only when an unrelated field happens to be set.
        """
        out = strip_uri_query_key("srt://cam:1600?streamid=r=0,m=request&passphrase=old", "passphrase")
        assert out == "srt://cam:1600?streamid=r=0,m=request"

    def test_dropping_the_last_parameter_drops_the_separator(self) -> None:
        assert strip_uri_query_key("srt://h:5000?passphrase=x", "passphrase") == "srt://h:5000"

    def test_uri_without_a_query_is_untouched(self) -> None:
        assert strip_uri_query_key("srt://h:5000", "passphrase") == "srt://h:5000"

    def test_match_is_case_insensitive(self) -> None:
        assert strip_uri_query_key("srt://h:5000?PassPhrase=x", "passphrase") == "srt://h:5000"


class TestRedactUrisInText:
    def test_strips_a_credential_from_a_gstreamer_error(self) -> None:
        out = redact_uris_in_text(
            "Could not open resource for reading rtsp://operator:hunter2@192.168.0.182:554/video/stream1"
        )
        assert "hunter2" not in out
        assert "operator:" not in out
        assert "rtsp://192.168.0.182:554/video/stream1" in out

    def test_two_adjacent_uris_are_each_redacted(self) -> None:
        """A greedy run-to-end-of-line match would swallow both URIs as one and
        strip only the first one's credential, leaving the second verbatim in
        the bundle."""
        out = redact_uris_in_text("Tried rtsp://admin:hunter2@10.0.0.5/a,rtsp://admin:pw2@10.0.0.6/b")
        assert "hunter2" not in out
        assert "pw2" not in out
        assert out == "Tried rtsp://10.0.0.5/a,rtsp://10.0.0.6/b"

    def test_a_password_containing_an_at_sign_is_fully_stripped(self) -> None:
        """``@`` is exactly the character the credential fields exist to make
        typeable, so it turns up unencoded in URLs operators paste. Matching to
        the *first* ``@`` leaves the rest of the password in the line."""
        out = redact_uris_in_text("failed rtsp://operator:p@ss@cam.local/s")
        assert out == "failed rtsp://cam.local/s"
        assert "ss@" not in out

    def test_an_at_sign_in_a_query_value_is_masked_not_mangled(self) -> None:
        """``?passphrase=p@ss`` must fall to the query rule. Swallowing it as
        userinfo would both destroy the URL and leave half the secret."""
        out = redact_uris_in_text("connecting srt://10.0.0.5:5000?passphrase=p@ss&latency=125")
        assert out == "connecting srt://10.0.0.5:5000?passphrase=***&latency=125"

    def test_the_schemeless_shorthand_is_redacted_too(self) -> None:
        """``strip_uri_userinfo`` accepts ``user:pass@host/path`` as a real
        input, so the free-text path has to cover it or the guarantee splits:
        the label would be clean while the log tail beside it leaked."""
        out = redact_uris_in_text("failed to open operator:hunter2@cam.local:554/stream")
        assert out == "failed to open cam.local:554/stream"

    @pytest.mark.parametrize(
        "line",
        [
            "mail user@example.com and rtsp://cam.local",
            "contact michel.honold+tag@example.co.uk today",
            "07:38:36 [INFO] /GstPipeline:rtsp-sink/GstRTSPSrc:rtspsrc:",
        ],
    )
    def test_the_schemeless_rule_does_not_eat_ordinary_text(self, line: str) -> None:
        """An address has no colon-separated pair before the ``@``; a
        credential always does. That is the whole of the distinction."""
        assert redact_uris_in_text(line) == line

    @pytest.mark.parametrize(
        "line",
        [
            "ratio 16:9@60Hz output",
            "Renewed lease at 10:30:00@host",
        ],
    )
    def test_ordinary_token_colon_token_at_token_text_survives(self, line: str) -> None:
        """Deleting a run is destructive, and log content shares the shape.

        What follows the ``@`` has to look like a host - dotted, or followed by
        a path - or an aspect ratio and a timestamped event get eaten out of a
        diagnostics bundle.
        """
        assert redact_uris_in_text(line) == line

    def test_a_dotless_host_with_a_path_is_still_a_credential(self) -> None:
        assert redact_uris_in_text("creds user:pw@camera/stream here") == "creds camera/stream here"

    def test_the_schemeless_rule_leaves_a_query_to_the_query_rule(self) -> None:
        """``?passphrase=p@ss`` reads as ``host:port?…=p`` + userinfo unless the
        query delimiter is excluded - and then the masking never runs."""
        out = redact_uris_in_text("connecting srt://10.0.0.5:5000?passphrase=p@ss&latency=125")
        assert out == "connecting srt://10.0.0.5:5000?passphrase=***&latency=125"

    def test_an_unrelated_email_address_is_left_alone(self) -> None:
        line = "mail user@example.com and rtsp://cam.local"
        assert redact_uris_in_text(line) == line

    def test_masks_a_passphrase_mid_line(self) -> None:
        out = redact_uris_in_text("connecting srt://10.0.0.5:5000?passphrase=topsecret&latency=125 now")
        assert "topsecret" not in out
        assert "latency=125" in out
        assert "now" in out

    def test_sentence_punctuation_survives(self) -> None:
        out = redact_uris_in_text("gave up on rtsp://u:p@cam.local:554/s.")
        assert out == "gave up on rtsp://cam.local:554/s."

    def test_an_at_sign_in_the_path_is_not_userinfo(self) -> None:
        line = "playing rtsp://cam.local:554/path@2x"
        assert redact_uris_in_text(line) == line

    def test_credential_free_text_is_untouched(self) -> None:
        line = "INFO video: RTSP source: rtsp://192.168.0.182:554/stream1 (latency=0)"
        assert redact_uris_in_text(line) == line


# ---------------------------------------------------------------------------
# An ``@`` that is not userinfo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        (
            "srt://10.0.0.5:1600?passphrase=Sh@w2026",
            "srt://10.0.0.5:1600?passphrase=***",
        ),
        (
            "srt://10.0.0.5:1600?streamid=live@stage&latency=125",
            "srt://10.0.0.5:1600?streamid=***&latency=125",
        ),
    ],
)
def test_redact_uri_keeps_the_host_when_the_at_is_inside_a_query_value(uri: str, expected: str) -> None:
    """A passphrase holding an ``@`` is ordinary - operators pick show names
    like ``Sh@w2026``. Treating that ``@`` as possible userinfo collapsed the
    whole URI to ``srt://***``, so the HUD label, ``/api/stats``, the
    Statistics panel and the bundle all lost the address, while the query
    masking that already handles it correctly never ran."""
    assert redact_uri(uri) == expected


def test_redact_uri_still_fails_closed_on_an_at_in_the_path() -> None:
    """Narrowing the check to authority-and-path must not narrow it further:
    a ``/`` in a password puts the rest in the path, and nothing distinguishes
    that from a genuine path ``@``, so the unprovable case stays closed."""
    assert redact_uri("rtsp://operator:pa/ss@cam.local:554/stream") == "rtsp://***"


def test_redact_uris_in_text_strips_a_password_containing_a_slash() -> None:
    """The scheme rule stops at the first ``/`` and the schemeless rule
    excludes one, so this shape reached the bundle verbatim - and it arrives
    through GStreamer's own ``location`` debug string, on exactly the auth
    failure that makes an operator send us a bundle."""
    text = "gstrtspsrc.c(9070): location=rtsp://operator:pa/ss@cam.local:554/stream failed"
    redacted = redact_uris_in_text(text)
    assert "pa/ss" not in redacted
    assert "operator" not in redacted
    assert "rtsp://cam.local:554/stream" in redacted


@pytest.mark.parametrize(
    "text",
    [
        "location=rtsp://cam.local:554/path@with@at",
        "location=rtsp://cam.local/p@th",
        "location=http://host:8080/a@b/c",
    ],
)
def test_redact_uris_in_text_leaves_a_path_at_alone(text: str) -> None:
    """A numeric port says the authority is a real host, so the ``@`` is in a
    path. Deleting the run would strip the address out of a log line that has
    no credential in it at all."""
    assert redact_uris_in_text(text) == text


def test_redact_uris_in_text_handles_two_uris_on_one_line() -> None:
    text = "from rtsp://u:pa/ss@cam1/s to rtsp://cam2:554/x"
    redacted = redact_uris_in_text(text)
    assert "pa/ss" not in redacted
    assert "rtsp://cam1/s" in redacted
    assert "rtsp://cam2:554/x" in redacted


# ---------------------------------------------------------------------------
# A "fragment" is often the tail of the secret
# ---------------------------------------------------------------------------


def test_redact_uri_fails_closed_on_a_hash_inside_a_password() -> None:
    """``rtsp://user:pa#ss@cam/s`` parses with netloc ``user:pa`` and the rest
    of the credential in what looks like a fragment - the mirror of a ``/`` in
    a password putting it in the path. Narrowing the fail-closed check to the
    authority alone published this one in full."""
    assert redact_uri("rtsp://user:pa#ss@cam.local/s") == "rtsp://***"


def test_redact_uri_fails_closed_on_an_at_in_a_fragment() -> None:
    assert redact_uri("srt://10.0.0.5:1600?passphrase=show#act@2026") == "srt://***"


def test_redact_uri_drops_a_fragment_that_follows_a_masked_secret() -> None:
    """``?passphrase=show#act2026`` is a passphrase containing a ``#`` as
    readily as a value plus a fragment, and no parse can tell them apart.
    Printing the tail publishes half the secret; the separator survives so the
    reader can see something was removed."""
    assert redact_uri("srt://10.0.0.5:1600?passphrase=show#act2026") == "srt://10.0.0.5:1600?passphrase=***#***"


def test_redact_uri_keeps_a_fragment_when_nothing_was_masked() -> None:
    """Nothing was secret, so nothing is ambiguous."""
    assert redact_uri("rtsp://cam.local:554/s#chapter1") == "rtsp://cam.local:554/s#chapter1"


# ---------------------------------------------------------------------------
# Bracketed IPv6 authorities
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "location=rtsp://[2001:db8::1]:554/path@2x",
        "location=rtsp://[2001:db8::1]/p@th",
        "location=rtsp://[2001:db8::1]:554/stream",
    ],
)
def test_redact_uris_in_text_leaves_a_bracketed_ipv6_host_alone(text: str) -> None:
    """Every IPv6 literal is full of colons, so the port test that separates
    userinfo from ``host:port`` reads one as a password and eats a URI that
    never carried a credential."""
    assert redact_uris_in_text(text) == text


def test_redact_uris_in_text_still_strips_userinfo_before_an_ipv6_host() -> None:
    text = "location=rtsp://u:p/w@[2001:db8::1]:554/s"
    redacted = redact_uris_in_text(text)
    assert "u:p/w" not in redacted
    assert "rtsp://[2001:db8::1]:554/s" in redacted


def test_redact_uris_in_text_leaves_an_unclosed_bracket_alone() -> None:
    """A malformed authority is not a licence to delete the line.

    The address has to carry colons: ``[bad`` passes this even with the
    bracket guard removed entirely, because the colon test it is guarding
    never fires. An unterminated literal is precisely where that test did its
    damage.
    """
    text = "location=rtsp://[2001:db8::1/p@th"
    assert redact_uris_in_text(text) == text


# ---------------------------------------------------------------------------
# A password can scatter itself into the query too
# ---------------------------------------------------------------------------


def test_redact_uri_fails_closed_on_a_question_mark_inside_a_password() -> None:
    """``rtsp://user:pa?ss@cam/stream`` parses with the rest of the credential
    in the query string, where it matches no key we mask and would be rejoined
    verbatim. Third spelling of one ambiguity, after ``/`` into the path and
    ``#`` into the fragment."""
    assert redact_uri("rtsp://user:pa?ss@cam.local/stream") == "rtsp://***"


def test_redact_uri_still_keeps_the_host_for_an_at_inside_a_masked_value() -> None:
    """The narrowing this guards must not swallow the case it exists for."""
    assert redact_uri("srt://10.0.0.5:1600?passphrase=Sh@w2026") == "srt://10.0.0.5:1600?passphrase=***"


def test_redact_uri_masks_only_the_secret_among_several_parameters() -> None:
    assert redact_uri("srt://h:1600?latency=125&passphrase=a@b") == "srt://h:1600?latency=125&passphrase=***"


# ---------------------------------------------------------------------------
# Rule order and run boundaries in free text
# ---------------------------------------------------------------------------


def test_redact_uris_in_text_strips_a_password_holding_both_an_at_and_a_slash() -> None:
    """The first-slash rule truncates such a password at the ``/`` and leaves
    nothing with a colon behind it, so whichever rule sees the run first has
    to be the one that can read the whole of it."""
    redacted = redact_uris_in_text("location=rtsp://operator:p@a/ss@cam/s")
    assert "p@a/ss" not in redacted
    assert "operator" not in redacted
    assert "rtsp://cam/s" in redacted


def test_redact_uris_in_text_judges_each_uri_on_its_own_authority() -> None:
    """Only whitespace used to end a run, so two URIs separated by anything
    else were spanned as one and the first authority decided for both."""
    redacted = redact_uris_in_text("rtsp://cam.local:554/a@b,rtsp://user:pa/ss@cam2/s")
    assert "user:pa/ss" not in redacted
    assert "rtsp://cam.local:554/a@b" in redacted  # clean, left intact
    assert "rtsp://cam2/s" in redacted


def test_redact_uris_in_text_does_not_delete_a_clean_uri_behind_a_dirty_one() -> None:
    redacted = redact_uris_in_text("rtsp://u:pa/ss@cam1/s,rtsp://cam2:554/p@th")
    assert "u:pa/ss" not in redacted
    assert "rtsp://cam1/s" in redacted
    assert "rtsp://cam2:554/p@th" in redacted


def test_redact_uris_in_text_still_strips_userinfo_without_a_colon() -> None:
    """The cross-slash rule declines this one (no colon to judge), so it has
    to still reach the simpler rule that runs after it."""
    assert redact_uris_in_text("location=rtsp://user@host/s") == "location=rtsp://host/s"
