# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""HMAC-signed peer-to-peer authentication for OpenFollow config broadcast."""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
from collections.abc import Callable

TIMESTAMP_WINDOW_SECONDS = 30  # tolerates NTP-free LAN skew while preventing replay
SIGNATURE_HEADER = "X-Auth-Signature"
TIMESTAMP_HEADER = "X-Auth-Timestamp"
MAX_SIGNED_BODY_SIZE = 1 * 1024 * 1024  # hard upper bound on signed request body


class ReplayCache:
    """Rejects replayed signed peer requests within the timestamp window.

    ``verify`` only proves a signature is valid and recent; on a plain-HTTP
    LAN an on-path attacker can capture and replay a valid signed request
    within the window. Keyed by signature; entries expire after the window so
    the set stays bounded. Thread-safe – the web server handles peer requests
    on concurrent threads.
    """

    def __init__(
        self,
        *,
        window_s: float = TIMESTAMP_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window_s = window_s
        self._clock = clock
        self._lock = threading.Lock()
        self._seen: dict[str, float] = {}  # signature -> expiry time

    def check_and_record(self, signature: str) -> bool:
        """Return True if ``signature`` is fresh (recording it), or False if it
        was already seen within the window – i.e. a replay."""
        with self._lock:
            now = self._clock()
            if self._seen:
                for sig in [s for s, exp in self._seen.items() if exp <= now]:
                    del self._seen[sig]
            if signature in self._seen:
                return False
            self._seen[signature] = now + self._window_s
            return True


def _canonical_message(
    method: str,
    path: str,
    body: bytes,
    timestamp: int,
) -> bytes:
    """Build the exact bytes that both sender and verifier feed into HMAC.

    Fields are joined by newlines in a fixed order; a SHA-256 hex digest
    stands in for the raw body so the verifier can compare without
    re-reading a potentially large request stream.

    ``path`` must include the query string when present (e.g.,
    ``"/api/config/import?skip_restart=1"``) – the query is semantically
    part of the operation being authorized and must therefore be signed.
    """
    body_hash = hashlib.sha256(body).hexdigest()
    return f"{method.upper()}\n{path}\n{body_hash}\n{timestamp}".encode()


def sign(
    pin: str,
    method: str,
    path: str,
    body: bytes,
    *,
    timestamp: int | None = None,
) -> tuple[int, str]:
    """Return ``(timestamp, hex_digest)`` for a peer request.

    ``timestamp`` defaults to the current wall-clock time; tests may
    pin it for determinism.
    """
    if timestamp is None:
        timestamp = int(time.time())
    msg = _canonical_message(method, path, body, timestamp)
    digest = hmac.new(pin.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return timestamp, digest


def verify(
    pin: str,
    method: str,
    path: str,
    body: bytes,
    timestamp_header: str,
    signature_header: str,
    *,
    now: int | None = None,
    window: int = TIMESTAMP_WINDOW_SECONDS,
) -> bool:
    """Verify a signed peer request. Returns ``False`` on any bad input.

    Comparison uses ``hmac.compare_digest`` so timing does not leak
    information about which byte of the digest first differed.
    """
    # A non-ASCII signature can't be a valid hex digest and would make
    # hmac.compare_digest raise TypeError; reject it up front.
    if not pin or not timestamp_header or not signature_header or not signature_header.isascii():
        return False
    try:
        ts = int(timestamp_header)
    except ValueError:
        return False
    if now is None:
        now = int(time.time())
    if abs(now - ts) > window:
        return False
    expected_msg = _canonical_message(method, path, body, ts)
    expected = hmac.new(pin.encode("utf-8"), expected_msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)
