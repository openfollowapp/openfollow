# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""``config.toml`` parsed once per change for the web server's requests."""

from __future__ import annotations

import os
import pickle  # nosec B403 - only ever loads what it just dumped in this process
import threading

from openfollow.configuration import AppConfig, load_config


class ParsedConfigCache:
    """Hands each request its own copy of the config, parsing the file again only when it changed.

    The copy keeps a handler that changes its config in place from reaching any other request.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entry: tuple[tuple[str, int, int, int, int], bytes] | None = None

    def load(self, path: str) -> AppConfig:
        try:
            key = _key(path)
        except OSError:
            return load_config(path)
        with self._lock:
            entry = self._entry
        if entry is None or entry[0] != key:
            try:
                parsed = load_config(path, strict=True)
                read_whole = _key(path) == key
            except Exception:
                read_whole = False
            if not read_whole:
                # Served but never kept: a read that failed, or a file that changed or went away while
                # it was read, falls back to the backup or to defaults, whose empty web_pin opens the login.
                return load_config(path)
            entry = (key, pickle.dumps(parsed))
            with self._lock:
                self._entry = entry
        return pickle.loads(entry[1])  # type: ignore[no-any-return]  # nosec B301 - our own bytes


def _key(path: str) -> tuple[str, int, int, int, int]:
    st = os.stat(path)
    # A save replaces the file, so the inode moves even within one mtime tick.
    return (path, st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)
