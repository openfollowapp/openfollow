# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The station's own settings backups: list them, and read one back for a restore.

The archives are written by ``privilege/settings_backup.py``, which the ``.deb``
``preinst`` inlines and so stays standard-library only; reading them lives here.
An archive holds the web PIN and stream credentials: nothing here hands its
contents to anyone but the restore that writes them back.
"""

from __future__ import annotations

import contextlib
import glob
import gzip
import json
import logging
import os
import re
import shutil
import tarfile
import tempfile
import tomllib
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import IO, Any, cast

from packaging.version import InvalidVersion, Version

from openfollow.configuration import AppConfig, ConfigFileUnreadableError, load_config
from openfollow.privilege import settings_backup

__all__ = [
    "CATALOG",
    "CONFIG",
    "LENSES",
    "MAX_ARCHIVE_BYTES",
    "MAX_MEMBERS",
    "MAX_MEMBER_BYTES",
    "MAX_TOTAL_BYTES",
    "MAX_UNPACKED_BYTES",
    "TEMPLATES",
    "Backup",
    "BackupContents",
    "BackupDamagedError",
    "BackupsUnreadableError",
    "StagedRestore",
    "backup_folders",
    "find_backup",
    "is_newer",
    "list_backups",
    "load_backup_config",
    "parse_version",
    "read_backup",
    "recover_interrupted_restore",
    "shown_version",
    "stage_restore",
]

CONFIG = "config.toml"
CATALOG = "markers.toml"
MANIFEST = "manifest.json"
TEMPLATES = "templates/user"
LENSES = "lens-calibrations"
_FOLDERS = (TEMPLATES, LENSES)

# Far above what a station writes; they stop a damaged or crafted archive, not a real one.
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_MEMBERS = 4096
# Everything the gzip stream unpacks to, headers included: a pax or long-name header is no member's size.
MAX_UNPACKED_BYTES = MAX_TOTAL_BYTES + 8 * 1024 * 1024

UPDATE_KIND = "update"
# The table's label, and the words for running text ("made before an import").
_KIND_LABELS = {
    "import": ("Import", "an import"),
    "defaults": ("Restore Defaults", "Restore Defaults"),
    "push": ("Settings push", "a settings push"),
    "restore": ("Restore", "a restore"),
}
_OTHER_KIND = ("Settings change", "a settings change")

# ``archive_name``: ``<station>-v<version>[-<kind>]-<UTC stamp>.ofbackup``.
_NAME_RE = re.compile(
    r"(?P<head>.+)-(?P<stamp>\d{8}T\d{6})(?:\.(?P<ms>\d{3}))?Z" + re.escape(settings_backup.ARCHIVE_SUFFIX)
)
# The last ``-v<digit>``: a station name may hold ``-v`` too, a version never does.
_NAMED_VERSION_RE = re.compile(r".*-v(\d[A-Za-z0-9._-]*)")
# A Debian revision (``-1``, ``-2+rpi1``); ``-rc3`` is a pre-release, not a revision.
_DEBIAN_REVISION_RE = re.compile(r"-\d[A-Za-z0-9.+]*$")
# What a commit leaves beside its target until it finishes: the staged copy and the one it replaced.
_STAGED, _REPLACED = ".restore", ".old"

logger = logging.getLogger(__name__)


class BackupsUnreadableError(OSError):
    """A backup folder that is there but cannot be listed."""


class BackupDamagedError(ValueError):
    """An archive that does not read as a settings backup."""


@dataclass(frozen=True)
class Backup:
    """One archive as the list shows it; ``version`` is as written, ``created`` ISO 8601 UTC."""

    name: str
    path: Path
    moment: datetime
    kind: str
    before: str
    made_before: str
    version: str
    created: str
    newer: bool

    def as_json(self) -> dict[str, Any]:
        """The row for the web UI; never the path or anything the archive holds but its manifest."""
        return {
            "name": self.name,
            "kind": self.kind,
            "before": self.before,
            "made_before": self.made_before,
            "version": shown_version(self.version),
            "created": self.created,
            "newer": self.newer,
        }


@dataclass(frozen=True)
class BackupContents:
    """What a restore writes back. ``folders`` names only the folders the archive holds."""

    config: bytes
    catalog: bytes | None
    folders: dict[str, dict[str, bytes]]


def backup_folders(config_path: str | os.PathLike[str]) -> tuple[Path, Path]:
    """The pre-update backups' folder and the web UI's, in that order."""
    update_dir = settings_backup.STATE_DIR / settings_backup.BACKUP_DIR_NAME
    return update_dir, settings_backup.web_backup_dir(Path(config_path))


def parse_version(text: str) -> Version | None:
    """``text`` as a PEP 440 version: Debian's ``~rc`` read as the pre-release, an epoch and a revision dropped."""
    bare = _DEBIAN_REVISION_RE.sub("", text.strip().rpartition(":")[2]).replace("~", "")
    try:
        return Version(bare)
    except InvalidVersion:
        return None


def shown_version(text: str) -> str:
    """``text`` as the web UI's footer spells a version; one that does not parse as written."""
    parsed = parse_version(text)
    return str(parsed) if parsed is not None else text


def is_newer(version: str, running: str) -> bool:
    """Whether a backup written by ``version`` is newer than ``running``; an unknown version never is."""
    theirs, ours = parse_version(version), parse_version(running)
    # A running 0.0.0 is the metadata-less fallback: it knows no version to compare.
    if theirs is None or ours is None or not any(ours.release):
        return False
    return theirs > ours


def _member_name(member: tarfile.TarInfo) -> str:
    """The member's name; refuses what the ``data`` extraction filter refuses, though nothing is extracted."""
    pure = PurePosixPath(member.name)
    if pure.is_absolute() or ".." in pure.parts or not (member.isfile() or member.isdir()):
        raise BackupDamagedError(f"the archive holds {member.name!r}, which no settings backup does")
    return str(pure)


class _Capped:
    """A stream that refuses to read past *cap* bytes."""

    def __init__(self, stream: gzip.GzipFile, cap: int) -> None:
        self._stream, self._left = stream, cap

    def read(self, size: int = -1) -> bytes:
        data = self._stream.read(self._left + 1 if size < 0 else min(size, self._left + 1))
        self._left -= len(data)
        if self._left < 0:
            raise BackupDamagedError("the archive unpacks to more than a settings backup")
        return data


def _read_members(path: Path, wanted: Callable[[str], bool]) -> tuple[dict[str, bytes], set[str]]:
    """The *wanted* regular files of the archive at *path* by name, and the names of its folders.

    Each size is checked from the member's header before its data is read, and
    the stream as a whole is capped, so a damaged or crafted archive is refused
    without unpacking it.
    """
    files: dict[str, bytes] = {}
    folders: set[str] = set()
    try:
        if path.stat().st_size > MAX_ARCHIVE_BYTES:
            raise BackupDamagedError("the archive is larger than a settings backup")
        with (
            gzip.open(path, "rb") as unpacked,
            tarfile.open(fileobj=cast(Any, _Capped(unpacked, MAX_UNPACKED_BYTES)), mode="r|") as tar,
        ):
            total = 0
            for count, member in enumerate(tar, start=1):
                name = _member_name(member)
                total += member.size
                if count > MAX_MEMBERS or member.size > MAX_MEMBER_BYTES or total > MAX_TOTAL_BYTES:
                    raise BackupDamagedError("the archive holds more than a settings backup")
                if member.isdir():
                    folders.add(name)
                elif wanted(name):
                    files[name] = cast(IO[bytes], tar.extractfile(member)).read()
    except (OSError, tarfile.TarError, EOFError, zlib.error) as exc:
        raise BackupDamagedError(str(exc) or type(exc).__name__) from exc
    return files, folders


def _restorable(name: str) -> bool:
    return name in (CONFIG, CATALOG) or any(name.startswith(folder + "/") for folder in _FOLDERS)


def read_backup(path: Path) -> BackupContents:
    """What the archive at *path* restores; raises :class:`BackupDamagedError` when it does not read."""
    files, folders = _read_members(path, _restorable)
    config = files.get(CONFIG)
    if config is None:
        raise BackupDamagedError("the archive holds no config.toml")
    catalog = files.get(CATALOG)
    if catalog is not None:
        try:
            tomllib.loads(catalog.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise BackupDamagedError(f"its marker catalog does not read: {exc}") from exc
    for name in files:
        if name in folders or any(str(parent) in files for parent in PurePosixPath(name).parents):
            raise BackupDamagedError(f"{name!r} needs a folder where the archive holds a file")
    held: dict[str, dict[str, bytes]] = {}
    for folder in _FOLDERS:
        prefix = folder + "/"
        inside = {name.removeprefix(prefix): data for name, data in files.items() if name.startswith(prefix)}
        if inside or folder in folders:
            held[folder] = inside
    return BackupContents(config, catalog, held)


def load_backup_config(contents: BackupContents) -> AppConfig:
    """The archive's ``config.toml`` read as a hand-edited one is, every field through ``__post_init__``."""
    with tempfile.TemporaryDirectory(prefix="openfollow-restore-") as scratch:
        path = Path(scratch) / CONFIG
        path.write_bytes(contents.config)
        try:
            return load_config(str(path), strict=True, missing_ok=False)
        except ConfigFileUnreadableError as exc:
            raise BackupDamagedError(f"its config.toml does not read: {exc}") from exc


def _manifest(path: Path) -> dict[str, Any]:
    """The archive's manifest, or ``{}`` when it has none that reads."""
    try:
        files, _folders = _read_members(path, lambda name: name == MANIFEST)
        data = json.loads(files[MANIFEST])
    except (BackupDamagedError, KeyError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _text(manifest: dict[str, Any], key: str) -> str:
    value = manifest.get(key)
    return value.strip() if isinstance(value, str) else ""


def _created(manifest: dict[str, Any]) -> str:
    """The manifest's ``created`` as ISO 8601 UTC, or ``""``; a time without a zone is UTC, as the writer's."""
    try:
        moment = datetime.fromisoformat(_text(manifest, "created"))
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def _before(kind: str, to: str) -> tuple[str, str]:
    """The row's label for what the backup was made before, and the same in running text."""
    if kind == UPDATE_KIND:
        return (
            (f"Update to {shown_version(to)}", f"the update to {shown_version(to)}") if to else ("Update", "an update")
        )
    return _KIND_LABELS.get(kind, _OTHER_KIND)


def _describe(path: Path, match: re.Match[str], moment: datetime, *, web: bool, running: str) -> Backup:
    """The archive's row: the manifest's words, else what its name says."""
    head, kind = match["head"], UPDATE_KIND
    if web:
        head, _, kind = head.rpartition("-")
    named = _NAMED_VERSION_RE.fullmatch(head)
    manifest = _manifest(path)
    version = _text(manifest, "version" if web else "from") or (named.group(1) if named else "")
    if web:
        kind = _text(manifest, "kind") or kind
    before, made_before = _before(kind, _text(manifest, "to"))
    return Backup(
        name=path.name,
        path=path,
        moment=moment,
        kind=kind,
        before=before,
        made_before=made_before,
        version=version,
        created=_created(manifest) or moment.isoformat(timespec="seconds"),
        newer=is_newer(version, running),
    )


def _archives(folder: Path) -> list[tuple[Path, re.Match[str], datetime]]:
    """The archives in *folder* with the time their names give; none when it is not there. Never a link."""
    try:
        with os.scandir(folder) as scan:
            entries = list(scan)
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise BackupsUnreadableError(exc.errno, exc.strerror, str(folder)) from exc
    found = []
    for entry in entries:
        match = _NAME_RE.fullmatch(entry.name)
        if not match or not entry.is_file(follow_symlinks=False):
            continue
        try:
            moment = datetime.strptime(match["stamp"], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
        except ValueError:
            continue
        found.append((Path(entry.path), match, moment.replace(microsecond=int(match["ms"] or 0) * 1000)))
    return found


def list_backups(config_path: str | os.PathLike[str], running: str) -> list[Backup]:
    """Every backup on the station, both folders, newest first.

    Raises :class:`BackupsUnreadableError` when a folder is there but cannot be
    listed: a list missing its archives would read as a station without them.
    """
    update_dir, web_dir = backup_folders(config_path)
    rows = [_describe(*found, web=False, running=running) for found in _archives(update_dir)]
    rows += [_describe(*found, web=True, running=running) for found in _archives(web_dir)]
    return sorted(rows, key=lambda row: (row.moment, row.name), reverse=True)


def find_backup(config_path: str | os.PathLike[str], name: str, running: str) -> Backup | None:
    """The listed backup called *name*, or ``None``: a request's text never becomes a path."""
    return next((row for row in list_backups(config_path, running) if row.name == name), None)


@dataclass
class StagedRestore:
    """The archive's catalog and folders written beside their targets, swapped in by :meth:`commit`."""

    files: list[tuple[Path, Path]] = field(default_factory=list)
    folders: list[tuple[Path, Path]] = field(default_factory=list)

    def commit(self) -> None:
        """Move each staged copy into place: a rename each, so a target is never half written."""
        for staged, target in self.files:
            os.replace(staged, target)
        for staged, target in self.folders:
            old = Path(tempfile.mkdtemp(prefix=f".{target.name}.", suffix=_REPLACED, dir=target.parent))
            with contextlib.suppress(FileNotFoundError):
                os.replace(target, old)
            os.replace(staged, target)
            shutil.rmtree(old, ignore_errors=True)

    def discard(self) -> None:
        """Remove whatever :meth:`commit` did not move into place."""
        for staged, _target in self.files:
            staged.unlink(missing_ok=True)
        for staged, _target in self.folders:
            shutil.rmtree(staged, ignore_errors=True)


def stage_restore(contents: BackupContents, catalog_path: Path, state_dir: Path) -> StagedRestore:
    """Write the archive's catalog and folders next to where they go, ready for :meth:`StagedRestore.commit`.

    A part the archive does not hold is left as it is on the station.
    """
    staged = StagedRestore()
    try:
        if contents.catalog is not None:
            catalog_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=f".{catalog_path.name}.", suffix=_STAGED, dir=catalog_path.parent)
            staged.files.append((Path(tmp), catalog_path))
            with os.fdopen(fd, "wb") as fh:
                fh.write(contents.catalog)
                fh.flush()
                os.fsync(fh.fileno())
        for folder, inside in contents.folders.items():
            target = state_dir / folder
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp_dir = Path(tempfile.mkdtemp(prefix=f".{target.name}.", suffix=_STAGED, dir=target.parent))
            staged.folders.append((tmp_dir, target))
            for relative, data in inside.items():
                dest = tmp_dir / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
    except BaseException:
        staged.discard()
        raise
    return staged


def _leftovers(target: Path, suffix: str) -> list[Path]:
    """What a commit of *target* left with *suffix*, newest first."""
    found = target.parent.glob(f".{glob.escape(target.name)}.*{suffix}")
    return sorted(found, key=lambda path: path.lstat().st_mtime_ns, reverse=True)


def recover_interrupted_restore(state_dir: Path, catalog_path: Path) -> None:
    """Put right a restore cut short between its renames; run at start and before the next restore.

    A folder gone between the two renames takes its staged copy, which was
    whole before the first rename, else the copy it replaced. Every other
    leftover is removed. A failure is logged: a start never stops on it.
    """
    for leftover in _leftovers(catalog_path, _STAGED):
        _remove(leftover)
    for folder in _FOLDERS:
        target = state_dir / folder
        candidates = _leftovers(target, _STAGED) + _leftovers(target, _REPLACED)
        if candidates and not target.exists():
            try:
                os.replace(candidates[0], target)
            except OSError as exc:
                logger.warning("Could not put back %s from %s: %s", target, candidates[0], exc)
                continue
            logger.warning("Put back %s from %s, left by a restore cut short", target, candidates[0].name)
            candidates = candidates[1:]
        for leftover in candidates:
            _remove(leftover)


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)
