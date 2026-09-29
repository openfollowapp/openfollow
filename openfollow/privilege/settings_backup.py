#!/usr/bin/python3 -I
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Archive the station's settings before a package upgrade unpacks.

The ``.deb`` embeds this file in its ``preinst``, which dpkg runs as root for
every install route before the new files land. Keep it standard-library only:
it runs under the system interpreter, and nothing from the package is on disk
yet.

It reads the sources as the service user, never as root: ``config.toml`` and
the marker catalog path it names are the service user's to write, so a root
reader would copy whatever file they point at into an archive that user owns.

A failed backup never fails the install. The update is often the fix, so the
reason goes to stderr and to :data:`RECORD_NAME` for the web UI.
"""

from __future__ import annotations

import contextlib
import errno
import io
import json
import os
import pwd
import re
import stat
import sys
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import tomllib

__all__ = [
    "ARCHIVE_SUFFIX",
    "BACKUP_DIR_NAME",
    "KEEP",
    "RECORD_NAME",
    "STATE_DIR",
    "BackupRecord",
    "archive_name",
    "list_archives",
    "main",
    "read_record",
    "run_backup",
    "safe_component",
    "station_label",
]

STATE_DIR = Path("/var/lib/openfollow")
SERVICE_USER = "openfollow"
BACKUP_DIR_NAME = "backups"
RECORD_NAME = "last-backup.json"
ARCHIVE_SUFFIX = ".ofbackup"
KEEP = 10

_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_NAME_MAX = 64
_DEFAULT_STATION = "OpenFollow"
_STATION_PREFIX = "OpenFollow "
_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
# The timestamp is the last field, so a dash in the station or version never shifts it.
_ARCHIVE_RE = re.compile(r".+-(\d{8}T\d{6}Z)" + re.escape(ARCHIVE_SUFFIX))
_NO_SPACE = frozenset({errno.ENOSPC, errno.EDQUOT})


@dataclass(frozen=True)
class BackupRecord:
    """The last backup attempt: ``archive`` on success, ``error`` otherwise."""

    from_version: str
    to_version: str
    archive: str
    error: str
    ts: str


def safe_component(value: str) -> str:
    """``value`` reduced to ``[A-Za-z0-9._-]``, other runs as one ``-``; no edge dash or dot (hidden file)."""
    return _UNSAFE_RE.sub("-", value).strip("-.")[:_NAME_MAX].strip("-.")


def station_label(system_name: object) -> str:
    """The file-name form of ``psn_system_name``, without a leading ``OpenFollow ``."""
    if not isinstance(system_name, str):
        return _DEFAULT_STATION
    name = system_name.removeprefix(_STATION_PREFIX)
    return safe_component(name) or _DEFAULT_STATION


def archive_name(system_name: object, old_version: str, now: datetime) -> str:
    """``<station>-v<old>-<UTC timestamp>.ofbackup``."""
    version = safe_component(old_version) or "unknown"
    stamp = now.astimezone(timezone.utc).strftime(_TIMESTAMP_FORMAT)
    return f"{station_label(system_name)}-v{version}-{stamp}{ARCHIVE_SUFFIX}"


def list_archives(backup_dir: Path) -> list[Path]:
    """Every archive in ``backup_dir``, oldest first by the timestamp in its name."""
    try:
        entries = list(backup_dir.iterdir())
    except OSError:
        return []
    dated = []
    for entry in entries:
        match = _ARCHIVE_RE.fullmatch(entry.name)
        if match and entry.is_file():
            dated.append((match.group(1), entry.name, entry))
    return [entry for _stamp, _name, entry in sorted(dated)]


def _load_config(config_path: Path) -> dict[str, object]:
    try:
        with open(config_path, "rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return {}


def _sources(state_dir: Path, config: dict[str, object]) -> list[tuple[str, Path]]:
    """``(arcname, path)`` for each source; ``config.toml`` sits in ``state_dir``."""
    raw = config.get("markers_catalog_path")
    catalog = Path(raw) if isinstance(raw, str) and raw else Path("markers.toml")
    if not catalog.is_absolute():
        catalog = state_dir / catalog
    return [
        ("config.toml", state_dir / "config.toml"),
        ("markers.toml", catalog),
        ("templates/user", state_dir / "templates" / "user"),
    ]


def _add_bytes(tar: tarfile.TarFile, arcname: str, data: bytes, mtime: float) -> None:
    info = tarfile.TarInfo(arcname)
    info.size = len(data)
    info.mtime = int(mtime)
    info.mode = 0o600
    tar.addfile(info, io.BytesIO(data))


def _write_archive(
    target: Path,
    sources: list[tuple[str, Path]],
    manifest: dict[str, object],
) -> None:
    """Write the gzip tar to ``target``; a missing source is listed in the manifest, not archived."""
    included: dict[str, str] = {}
    missing: dict[str, str] = {}
    with open(target, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as tar:
        for arcname, path in sources:
            try:
                st = os.lstat(path)
            except FileNotFoundError:
                missing[arcname] = str(path)
                continue
            if stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode):
                tar.add(path, arcname=arcname, recursive=True)
                included[arcname] = str(path)
            else:
                missing[arcname] = str(path)
        body = dict(manifest, sources=included, missing=missing)
        _add_bytes(tar, "manifest.json", json.dumps(body, indent=2).encode(), datetime.now().timestamp())
        tar.close()
        raw.flush()
        os.fsync(raw.fileno())


def _write_once(
    backup_dir: Path,
    name: str,
    sources: list[tuple[str, Path]],
    manifest: dict[str, object],
    write: Callable[[Path, list[tuple[str, Path]], dict[str, object]], None],
) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=ARCHIVE_SUFFIX, dir=backup_dir)
    os.close(fd)
    try:
        write(Path(tmp), sources, manifest)
        os.replace(tmp, backup_dir / name)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _describe(exc: BaseException) -> str:
    if isinstance(exc, OSError) and exc.strerror:
        where = f": {exc.filename}" if exc.filename else ""
        return f"{exc.strerror}{where}"
    return str(exc) or type(exc).__name__


def _write_record(backup_dir: Path, record: BackupRecord) -> None:
    body = json.dumps(
        {
            "from": record.from_version,
            "to": record.to_version,
            "archive": record.archive,
            "error": record.error,
            "ts": record.ts,
        }
    )
    try:
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=backup_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body + "\n")
        os.replace(tmp, backup_dir / RECORD_NAME)
    except OSError as exc:
        print(f"openfollow: could not record the settings backup: {_describe(exc)}", file=sys.stderr)


def read_record(state_dir: Path = STATE_DIR) -> BackupRecord | None:
    """The last backup attempt, or ``None`` when there is no readable record."""
    try:
        data = json.loads((state_dir / BACKUP_DIR_NAME / RECORD_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    fields = [data.get(key) for key in ("from", "to", "archive", "error", "ts")]
    strings = [value for value in fields if isinstance(value, str)]
    if len(strings) != len(fields):
        return None
    return BackupRecord(*strings)


def run_backup(
    state_dir: Path,
    old_version: str,
    new_version: str,
    *,
    now: datetime | None = None,
    keep: int = KEEP,
    write: Callable[[Path, list[tuple[str, Path]], dict[str, object]], None] = _write_archive,
) -> BackupRecord:
    """Archive the settings under ``state_dir/backups`` and record the outcome.

    Out of space, it deletes the oldest archives one at a time and retries, but
    never the newest one that already existed. Any other failure deletes nothing.
    """
    moment = now or datetime.now(timezone.utc)
    ts = moment.astimezone(timezone.utc).isoformat(timespec="seconds")
    backup_dir = state_dir / BACKUP_DIR_NAME
    config = _load_config(state_dir / "config.toml")
    name = archive_name(config.get("psn_system_name"), old_version, moment)
    manifest: dict[str, object] = {"from": old_version, "to": new_version, "created": ts}

    def finish(archive: str, error: str) -> BackupRecord:
        record = BackupRecord(old_version, new_version, archive, error, ts)
        if error:
            print(f"openfollow: settings backup failed, continuing the install: {error}", file=sys.stderr)
        _write_record(backup_dir, record)
        return record

    try:
        backup_dir.mkdir(mode=0o700, exist_ok=True)
        os.chmod(backup_dir, 0o700)
    except OSError as exc:
        return finish("", _describe(exc))

    evictable = list_archives(backup_dir)[:-1]
    while True:
        try:
            _write_once(backup_dir, name, _sources(state_dir, config), manifest, write)
            break
        except OSError as exc:
            if exc.errno not in _NO_SPACE or not evictable:
                return finish("", _describe(exc))
            try:
                evictable.pop(0).unlink(missing_ok=True)
            except OSError as unlink_exc:
                return finish("", _describe(unlink_exc))
        except (tarfile.TarError, ValueError) as exc:
            return finish("", _describe(exc))

    for stale in list_archives(backup_dir)[:-keep]:
        with contextlib.suppress(OSError):
            stale.unlink()
    return finish(name, "")


def _drop_to_service_user() -> bool:
    """Become the service user when running as root; ``False`` when that user doesn't exist."""
    if os.geteuid() != 0:
        return True
    try:
        entry = pwd.getpwnam(SERVICE_USER)
    except KeyError:
        return False
    os.setgroups([])
    os.setgid(entry.pw_gid)
    os.setuid(entry.pw_uid)
    return True


def main(argv: list[str]) -> int:
    """dpkg ``preinst`` entry point: act on ``upgrade <old> [<new>]``, always return 0."""
    if len(argv) < 2 or argv[0] != "upgrade":
        return 0
    old_version = argv[1]
    new_version = argv[2] if len(argv) > 2 else ""
    if not STATE_DIR.is_dir():
        return 0
    try:
        if not _drop_to_service_user():
            print(f"openfollow: settings backup skipped: no user {SERVICE_USER!r}", file=sys.stderr)
            return 0
        os.umask(0o077)
        run_backup(STATE_DIR, old_version, new_version)
    except Exception as exc:  # the install must never fail on a backup
        print(f"openfollow: settings backup failed, continuing the install: {_describe(exc)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
