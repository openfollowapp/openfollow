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

from openfollow import restore_hold
from openfollow.configuration import AppConfig, ConfigFileUnreadableError, load_config
from openfollow.privilege import settings_backup

__all__ = [
    "CATALOG",
    "CONFIG",
    "JOURNAL",
    "LENSES",
    "MAX_ARCHIVE_BYTES",
    "MAX_MEMBERS",
    "MAX_MEMBER_BYTES",
    "MAX_TOTAL_BYTES",
    "MAX_UNPACKED_BYTES",
    "PENDING_JOURNAL",
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
# Beside the config: staged with the copies, renamed once the restored config is saved, removed when done.
PENDING_JOURNAL = ".restore-commit.pending"
JOURNAL = ".restore-commit"

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
    try:
        member.name.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise BackupDamagedError("the archive holds a name that is no text") from exc
    pure = PurePosixPath(member.name)
    if "\0" in member.name or pure.is_absolute() or ".." in pure.parts or not (member.isfile() or member.isdir()):
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
        if any(str(parent) in files for parent in PurePosixPath(name).parents):
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


def _sync(path: Path) -> None:
    """Flush *path*, a file or a folder's entries, to the disk; a folder that cannot be synced is no failure."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        with contextlib.suppress(OSError):
            os.fsync(fd)
    finally:
        os.close(fd)


def _write_synced(path: Path, data: bytes) -> None:
    with open(path, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    _sync(path)


@dataclass
class StagedRestore:
    """The archive's config, catalog and folders written beside their targets, swapped in by :meth:`commit`.

    Everything that takes space is made before the commit, the place each
    folder is moved aside to and the journal included, so a commit only renames.
    """

    journal: Path
    config_target: Path
    catalog_target: Path
    config: Path | None = None
    files: list[tuple[Path, Path]] = field(default_factory=list)
    # ``(part, staged, aside, target)``: the archive's folder name, its copy, where the station's goes.
    folders: list[tuple[str, Path, Path, Path]] = field(default_factory=list)
    committed: bool = False

    def config_slot(self) -> Path:
        """An empty file beside the config for :func:`~openfollow.configuration.stage_config` to fill."""
        fd, tmp = tempfile.mkstemp(prefix=f".{self.config_target.name}.", suffix=_STAGED, dir=self.config_target.parent)
        os.close(fd)
        self.config = Path(tmp)
        return self.config

    def commit(self) -> list[str]:
        """Put the restore in place; the parts that kept the station's own.

        Raises ``OSError``, with nothing changed, when the journal or the config
        cannot move in. From the config on, every restored part is held
        (:mod:`openfollow.restore_hold`) and nothing raises: the caller's restart
        must follow. The journal marks the commit until it ends, so a power cut
        before then finishes the restore at the next start. A part that cannot
        move in keeps the station's own: a folder moved aside is put back now,
        or, when that fails too, by the next start, which finds no journal.
        """
        done = self.journal.with_name(JOURNAL)
        os.replace(self.journal, done)
        _sync(done.parent)
        if self.config is not None:
            try:
                os.replace(self.config, self.config_target)
            except OSError:
                with contextlib.suppress(OSError):
                    os.replace(done, self.journal)
                raise
        self.committed = True
        state_dir = self.journal.parent
        restore_hold.hold(self.config_target, self.catalog_target, state_dir / TEMPLATES, state_dir / LENSES)
        failed: list[str] = []
        pending: list[tuple[str, Path, Path | None, Path]] = [
            (CATALOG, staged, None, target) for staged, target in self.files
        ]
        pending += self.folders
        try:
            _sync(self.config_target.parent)
            while pending:
                part, staged, aside, target = pending[0]
                moved_in = self._move_file(staged, target) if aside is None else self._move_in(staged, aside, target)
                pending.pop(0)
                if not moved_in:
                    failed.append(part)
                _sync(target.parent)
        except Exception:
            logger.exception("Restore cut short after its config was written; restarting on what is in place")
            failed += [part for part, *_rest in pending]
        with contextlib.suppress(OSError):
            done.unlink(missing_ok=True)
        _sync(done.parent)
        return failed

    @staticmethod
    def _move_file(staged: Path, target: Path) -> bool:
        try:
            os.replace(staged, target)
        except OSError as exc:
            logger.warning("Could not restore %s: %s", target, exc)
            with contextlib.suppress(OSError):
                staged.unlink(missing_ok=True)
            return False
        return True

    @staticmethod
    def _move_in(staged: Path, aside: Path, target: Path) -> bool:
        moved = False
        try:
            with contextlib.suppress(FileNotFoundError):
                os.replace(target, aside)
                moved = True
            os.replace(staged, target)
        except OSError as exc:
            logger.warning("Could not restore %s: %s", target, exc)
            if moved:
                try:
                    os.replace(aside, target)
                except OSError as back:
                    logger.warning("Could not put back %s, the next start does: %s", target, back)
                    return False
            shutil.rmtree(staged, ignore_errors=True)
            shutil.rmtree(aside, ignore_errors=True)
            return False
        shutil.rmtree(aside, ignore_errors=True)
        return True

    def discard(self) -> None:
        """Remove what was staged, the journal last; after a commit, what it moved and left is its own business."""
        if self.committed:
            return
        for staged in [*(staged for staged, _target in self.files), *([self.config] if self.config else [])]:
            staged.unlink(missing_ok=True)
        for _part, staged, aside, _target in self.folders:
            shutil.rmtree(staged, ignore_errors=True)
            shutil.rmtree(aside, ignore_errors=True)
        self.journal.unlink(missing_ok=True)


def stage_restore(contents: BackupContents, catalog_path: Path, config_path: Path) -> StagedRestore:
    """Write the archive's catalog and folders next to where they go, ready for :meth:`StagedRestore.commit`.

    The journal goes first, naming the catalog for the recovery. Every file and
    folder is synced, so a staged copy is whole on the disk before the first
    rename. The config is staged later, through :meth:`StagedRestore.config_slot`.
    A part the archive does not hold is left as it is.
    """
    state_dir = config_path.parent
    staged = StagedRestore(state_dir / PENDING_JOURNAL, config_path, catalog_path)
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        _write_synced(staged.journal, json.dumps({"catalog": str(catalog_path)}).encode())
        if contents.catalog is not None:
            catalog_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=f".{catalog_path.name}.", suffix=_STAGED, dir=catalog_path.parent)
            os.close(fd)
            staged.files.append((Path(tmp), catalog_path))
            _write_synced(Path(tmp), contents.catalog)
        for folder, inside in contents.folders.items():
            target = state_dir / folder
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp_dir = Path(tempfile.mkdtemp(prefix=f".{target.name}.", suffix=_STAGED, dir=target.parent))
            try:
                aside = Path(tempfile.mkdtemp(prefix=f".{target.name}.", suffix=_REPLACED, dir=target.parent))
            except OSError:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                raise
            staged.folders.append((folder, tmp_dir, aside, target))
            for relative, data in inside.items():
                dest = tmp_dir / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                _write_synced(dest, data)
            for made in sorted({tmp_dir, *(p.parent for p in tmp_dir.rglob("*"))}, reverse=True):
                _sync(made)
        for parent in {state_dir, catalog_path.parent, *(target.parent for *_rest, target in staged.folders)}:
            _sync(parent)
    except BaseException:
        staged.discard()
        raise
    return staged


def _leftovers(target: Path, suffix: str) -> list[Path]:
    """What a commit of *target* left with *suffix*, newest first."""
    found = target.parent.glob(f".{glob.escape(target.name)}.*{suffix}")
    return sorted(found, key=lambda path: path.lstat().st_mtime_ns, reverse=True)


def _journal_catalog(journal: Path) -> Path | None:
    """The catalog a journal names, or ``None``."""
    try:
        named = json.loads(journal.read_bytes()).get("catalog")
    except (OSError, ValueError, AttributeError):
        return None
    return Path(named) if isinstance(named, str) and named else None


def recover_interrupted_restore(config_path: Path) -> None:
    """Put right a restore cut short; run at start before the config is read, and before the next restore.

    With the commit's journal on the disk the restore is finished from its
    staged copies, config included, which were whole before the first rename.
    Without it the restore never committed, or committed and ended: its copies
    are discarded, and a folder left moved aside is put back. A failure is
    logged: a start never stops on it.
    """
    state_dir = config_path.parent
    done, pending = state_dir / JOURNAL, state_dir / PENDING_JOURNAL
    committed = done.exists()
    catalog = _journal_catalog(done if committed else pending)
    for target in [config_path, *([catalog] if catalog is not None else [])]:
        staged = _leftovers(target, _STAGED)
        source = staged[0] if committed and staged else None
        if source is not None:
            _install(source, target)
        for leftover in staged:
            if leftover != source:
                _remove(leftover)
    for folder in _FOLDERS:
        target = state_dir / folder
        staged, aside = _leftovers(target, _STAGED), _leftovers(target, _REPLACED)
        # An empty one is the place a commit never reached: nothing of the station's is in it.
        moved = [path for path in aside if any(path.iterdir())]
        source = staged[0] if committed and staged else moved[0] if moved and not target.exists() else None
        if source is not None:
            _install(source, target)
        for leftover in staged + aside:
            if leftover != source:
                _remove(leftover)
    for journal in (done, pending):
        journal.unlink(missing_ok=True)


def _install(source: Path, target: Path) -> None:
    """*source* in place of *target*; a failure leaves both for the next start."""
    try:
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        os.replace(source, target)
    except OSError as exc:
        logger.warning("Could not put %s in place from %s: %s", target, source.name, exc)
        return
    _sync(target.parent)
    logger.warning("Put %s in place from %s, left by a restore cut short", target, source.name)


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)
