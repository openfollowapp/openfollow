# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""One running station per config file.

The lock is an ``flock`` on ``<config>.lock``, held for the process lifetime.
The kernel drops it when the process exits, so a crash never leaves it stale,
and Python opens the file non-inheritable, so a restart through ``os.execv``
releases it to the new image.
"""

from __future__ import annotations

import fcntl
import logging
import os
from pathlib import Path
from typing import IO

logger = logging.getLogger(__name__)


class AlreadyRunningError(RuntimeError):
    """Another process holds the lock for this config."""

    def __init__(self, lock_path: Path, pid: int | None) -> None:
        self.lock_path = lock_path
        self.pid = pid
        holder = f"PID {pid}" if pid is not None else "another process"
        super().__init__(f"OpenFollow is already running on this config ({holder}, lock {lock_path}).")


def lock_path_for(config_path: str | os.PathLike[str]) -> Path:
    path = Path(config_path).expanduser().absolute()
    return path.with_name(path.name + ".lock")


def _holder_pid(handle: IO[str]) -> int | None:
    handle.seek(0)
    text = handle.read().strip()
    return int(text) if text.isdigit() else None


def acquire_instance_lock(config_path: str | os.PathLike[str]) -> IO[str] | None:
    """Take the lock for ``config_path``; the caller keeps the handle open.

    Raises ``AlreadyRunningError`` when another process holds it. Returns
    ``None`` (and the station runs unguarded) when the lock file cannot be
    opened, so a read-only config directory never stops the show.
    """
    lock_path = lock_path_for(config_path)
    try:
        # "a+" never truncates the holder's PID before the lock is ours.
        handle = open(lock_path, "a+", encoding="ascii")  # noqa: SIM115 - held for the process lifetime
    except OSError as exc:
        logger.warning("Cannot open the instance lock %s (%s); a second copy would not be refused.", lock_path, exc)
        return None
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        pid = _holder_pid(handle)
        handle.close()
        raise AlreadyRunningError(lock_path, pid) from None
    except OSError as exc:
        handle.close()
        logger.warning("Cannot take the instance lock %s (%s); a second copy would not be refused.", lock_path, exc)
        return None
    try:
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
    except OSError:
        logger.debug("Could not record the PID in %s.", lock_path, exc_info=True)
    return handle
