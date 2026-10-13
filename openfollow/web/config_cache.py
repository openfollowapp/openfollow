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

    Every request reads the config, and an open page polls; parsing on each one
    cost a Pi tens of milliseconds per request on a large config. A copy costs a
    small fraction of that, and keeps a handler that changes its config in place
    from reaching any other request.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entry: tuple[tuple[str, int, int, int], bytes] | None = None

    def load(self, path: str) -> AppConfig:
        try:
            st = os.stat(path)
        except OSError:
            return load_config(path)
        # A save replaces the file, so the inode moves even within one mtime tick.
        key = (path, st.st_ino, st.st_mtime_ns, st.st_size)
        with self._lock:
            entry = self._entry
        if entry is None or entry[0] != key:
            try:
                parsed = load_config(path, strict=True)
            except Exception:
                # Served but never kept: a read that failed once falls back to the backup or
                # to defaults, whose empty web_pin would open the login until the next save.
                return load_config(path)
            entry = (key, pickle.dumps(parsed))
            with self._lock:
                self._entry = entry
        return pickle.loads(entry[1])  # type: ignore[no-any-return]  # nosec B301 - our own bytes
