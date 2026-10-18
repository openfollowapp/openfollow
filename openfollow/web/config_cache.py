# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""``config.toml`` parsed once per change for the web server's requests."""

from __future__ import annotations

import os
import pickle  # nosec B403 - only ever loads what it just dumped in this process
import threading
import time
from collections.abc import Callable

import tomllib

from openfollow.configuration import AppConfig, load_config


class ParsedConfigCache:
    """Hands each request its own copy of the config, parsing the file again only when it changed.

    The copy keeps a handler that changes its config in place from reaching any other request. A
    file that does not read whole (gone, empty, unparseable, or changed while it was read) is served
    as it last read whole: the backup is a save behind, and could hand out an older PIN, or none.
    Before any whole read, it is read as ``load_config`` reads it.

    A parse is not reused while the file is younger than ``_RACY_S`` (an edit in place within one
    mtime tick would not show in the key: git's "racily clean" rule) or still holds a legacy OTP
    ``source_ip``, which the parse resolves against interfaces that may not have their address yet;
    it is still what a file that broke since is served as.
    """

    def __init__(self, now: Callable[[], float] = time.time) -> None:
        self._now = now
        self._lock = threading.Lock()
        # (key, pickled config, whether it may be reused while the key holds)
        self._entry: tuple[tuple[str, int, int, int, int], bytes, bool] | None = None
        # Counts stored entries: a parse keeps its entry only if none was stored since it began.
        self._generation = 0

    def load(self, path: str) -> AppConfig:
        with self._lock:
            entry, generation = self._entry, self._generation
        try:
            key = _key(path)
        except OSError:
            return self._last_whole(entry, path)
        if entry is None or entry[0] != key or not entry[2]:
            try:
                parsed = load_config(path, strict=True, missing_ok=False)
                read_whole = _key(path) == key
            except Exception:
                read_whole = False
            if not read_whole:
                return self._last_whole(entry, path)
            reusable = self._now() - key[3] / 1e9 >= _RACY_S and not _holds_legacy_otp_ip(path)
            entry = (key, pickle.dumps(parsed), reusable)
            with self._lock:
                if self._generation == generation:
                    self._entry = entry
                    self._generation += 1
        return pickle.loads(entry[1])  # type: ignore[no-any-return]  # nosec B301 - our own bytes

    @staticmethod
    def _last_whole(entry: tuple[tuple[str, int, int, int, int], bytes, bool] | None, path: str) -> AppConfig:
        if entry is not None and entry[0][0] == path:
            return pickle.loads(entry[1])  # type: ignore[no-any-return]  # nosec B301 - our own bytes
        return load_config(path)


# Younger than this, an edit in place within one mtime tick could still follow unseen.
_RACY_S = 1.0


def _holds_legacy_otp_ip(path: str) -> bool:
    """Whether ``[otp_output]`` pins a ``source_ip`` with no ``source_iface``, resolved anew on each read."""
    try:
        with open(path, "rb") as f:
            otp = tomllib.load(f).get("otp_output")
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return isinstance(otp, dict) and "source_ip" in otp and "source_iface" not in otp


def _key(path: str) -> tuple[str, int, int, int, int]:
    st = os.stat(path)
    # A save replaces the file, so the inode moves even within one mtime tick.
    return (path, st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)
