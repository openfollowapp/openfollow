# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for openfollow.web.peer_auth."""

from __future__ import annotations

import pytest

from openfollow.web import peer_auth

pytestmark = pytest.mark.unit


def test_sign_verify_roundtrip() -> None:
    body = b'{"pos_x": 1.0}'
    ts, sig = peer_auth.sign("1234", "POST", "/api/config/camera", body)

    assert (
        peer_auth.verify(
            "1234",
            "POST",
            "/api/config/camera",
            body,
            str(ts),
            sig,
            now=ts,
        )
        is True
    )


def test_verify_rejects_non_ascii_signature() -> None:
    # A non-ASCII signature can't be a hex digest; verify must return False,
    # not raise TypeError from hmac.compare_digest.
    ts, _ = peer_auth.sign("1234", "POST", "/api/x", b"{}")
    assert (
        peer_auth.verify(
            "1234",
            "POST",
            "/api/x",
            b"{}",
            str(ts),
            "deadbeefé",
            now=ts,
        )
        is False
    )


def test_verify_rejects_wrong_pin() -> None:
    body = b"{}"
    ts, sig = peer_auth.sign("1234", "POST", "/api/x", body)

    assert (
        peer_auth.verify(
            "4321",
            "POST",
            "/api/x",
            body,
            str(ts),
            sig,
            now=ts,
        )
        is False
    )


def test_verify_rejects_modified_body() -> None:
    ts, sig = peer_auth.sign("pin", "POST", "/api/x", b'{"a":1}')

    assert (
        peer_auth.verify(
            "pin",
            "POST",
            "/api/x",
            b'{"a":2}',
            str(ts),
            sig,
            now=ts,
        )
        is False
    )


def test_verify_rejects_modified_path() -> None:
    body = b"{}"
    ts, sig = peer_auth.sign("pin", "POST", "/api/x", body)

    assert (
        peer_auth.verify(
            "pin",
            "POST",
            "/api/y",
            body,
            str(ts),
            sig,
            now=ts,
        )
        is False
    )


def test_verify_rejects_modified_method() -> None:
    body = b"{}"
    ts, sig = peer_auth.sign("pin", "POST", "/api/x", body)

    assert (
        peer_auth.verify(
            "pin",
            "PUT",
            "/api/x",
            body,
            str(ts),
            sig,
            now=ts,
        )
        is False
    )


def test_verify_includes_query_string() -> None:
    # Paths differing only by query string produce different signatures –
    # the query is semantically part of the authorized operation.
    body = b"{}"
    ts, sig = peer_auth.sign("pin", "POST", "/api/x?skip_restart=1", body)

    assert (
        peer_auth.verify(
            "pin",
            "POST",
            "/api/x",
            body,
            str(ts),
            sig,
            now=ts,
        )
        is False
    )
    assert (
        peer_auth.verify(
            "pin",
            "POST",
            "/api/x?skip_restart=1",
            body,
            str(ts),
            sig,
            now=ts,
        )
        is True
    )


def test_verify_rejects_stale_timestamp() -> None:
    body = b"{}"
    ts, sig = peer_auth.sign("pin", "POST", "/api/x", body, timestamp=1000)

    # ``now`` is more than TIMESTAMP_WINDOW_SECONDS after ``ts``.
    later = 1000 + peer_auth.TIMESTAMP_WINDOW_SECONDS + 1
    assert (
        peer_auth.verify(
            "pin",
            "POST",
            "/api/x",
            body,
            str(ts),
            sig,
            now=later,
        )
        is False
    )


def test_verify_rejects_future_timestamp() -> None:
    body = b"{}"
    ts, sig = peer_auth.sign("pin", "POST", "/api/x", body, timestamp=1_000_000)

    earlier = 1_000_000 - peer_auth.TIMESTAMP_WINDOW_SECONDS - 1
    assert (
        peer_auth.verify(
            "pin",
            "POST",
            "/api/x",
            body,
            str(ts),
            sig,
            now=earlier,
        )
        is False
    )


def test_verify_accepts_at_exact_window_boundary() -> None:
    body = b"{}"
    ts, sig = peer_auth.sign("pin", "POST", "/api/x", body, timestamp=1000)

    at_boundary = 1000 + peer_auth.TIMESTAMP_WINDOW_SECONDS
    assert (
        peer_auth.verify(
            "pin",
            "POST",
            "/api/x",
            body,
            str(ts),
            sig,
            now=at_boundary,
        )
        is True
    )


def test_verify_rejects_non_numeric_timestamp() -> None:
    body = b"{}"
    _, sig = peer_auth.sign("pin", "POST", "/api/x", body)

    assert (
        peer_auth.verify(
            "pin",
            "POST",
            "/api/x",
            body,
            "not-a-number",
            sig,
            now=0,
        )
        is False
    )


def test_verify_rejects_missing_headers() -> None:
    body = b"{}"
    ts, sig = peer_auth.sign("pin", "POST", "/api/x", body)

    assert peer_auth.verify("pin", "POST", "/api/x", body, "", sig, now=ts) is False
    assert peer_auth.verify("pin", "POST", "/api/x", body, str(ts), "", now=ts) is False


def test_verify_rejects_empty_pin() -> None:
    body = b"{}"
    ts, sig = peer_auth.sign("pin", "POST", "/api/x", body)

    assert peer_auth.verify("", "POST", "/api/x", body, str(ts), sig, now=ts) is False


def test_method_is_case_insensitive_on_sign_and_verify() -> None:
    body = b"{}"
    ts, sig = peer_auth.sign("pin", "post", "/api/x", body)

    assert (
        peer_auth.verify(
            "pin",
            "POST",
            "/api/x",
            body,
            str(ts),
            sig,
            now=ts,
        )
        is True
    )


def test_canonical_message_uses_uppercase_method() -> None:
    # Golden-value guard: the canonical message format is part of the
    # wire protocol.  Any change to how the method is normalised (e.g.
    # accidentally switching upper() → lower()) would silently break
    # interop with peers running a different build, so lock the exact
    # bytes.  A pure round-trip test does not catch this because both
    # sides of the round-trip mutate together.
    msg = peer_auth._canonical_message("post", "/api/x", b"{}", 1000)
    assert msg.startswith(b"POST\n")


def test_sign_produces_known_golden_digest() -> None:
    # Companion to the canonical-message test: pins the full HMAC for a
    # fixed input so future refactors of the digest pipeline (encoding,
    # hash algorithm, field order, separators) cannot silently change
    # the on-the-wire format.  Regenerate only when the wire protocol is
    # deliberately revised; if this breaks, peers running an older build
    # will stop authenticating against a host running the new build.
    ts, digest = peer_auth.sign(
        "test-pin",
        "POST",
        "/api/config/camera",
        b'{"x":1}',
        timestamp=1000,
    )
    assert ts == 1000
    assert digest == ("dd07a97eee62b793349f15a5b89785e6f9737e54db1039cfed8febc1f7057e4f")


class _FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_replay_cache_accepts_fresh_rejects_replay() -> None:
    clock = _FakeClock()
    cache = peer_auth.ReplayCache(window_s=30, clock=clock)

    assert cache.check_and_record("sigA") is True  # fresh
    assert cache.check_and_record("sigA") is False  # replay within window
    assert cache.check_and_record("sigB") is True  # different signature


def test_replay_cache_entry_expires_after_window() -> None:
    clock = _FakeClock()
    cache = peer_auth.ReplayCache(window_s=30, clock=clock)

    assert cache.check_and_record("sigA") is True
    clock.advance(31)  # past the window → entry pruned
    assert cache.check_and_record("sigA") is True
