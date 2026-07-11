# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Read and validate .oftemplate files from system and user folders."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openfollow.templates.schema import (
    TEMPLATE_FILE_SUFFIX,
    TEMPLATE_LEGACY_SUFFIX,
    OpenFollowTemplate,
    TemplateValidationError,
)

# Both the canonical suffix and the legacy one are read off disk so templates
# saved by an earlier build keep loading; the writer only ever emits the
# canonical suffix.
_READ_SUFFIXES: tuple[str, ...] = (TEMPLATE_FILE_SUFFIX, TEMPLATE_LEGACY_SUFFIX)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoadedTemplate:
    """One entry from the templates folder (either loaded or with error)."""

    path: Path
    filename: str
    is_system: bool
    template: OpenFollowTemplate | None
    error: str  # empty when ``template`` is set; non-empty otherwise


def _load_one(path: Path, *, is_system: bool) -> LoadedTemplate:
    """Read one .oftemplate file. Never raises – errors are returned in LoadedTemplate."""
    try:
        # ``utf-8-sig`` transparently strips a leading BOM so a file authored
        # by a BOM-adding editor still loads; a BOM-less file is unaffected.
        raw = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        msg = f"not valid UTF-8: {exc.reason}"
        logger.warning("template %s: %s", path, msg)
        return LoadedTemplate(
            path=path,
            filename=path.name,
            is_system=is_system,
            template=None,
            error=msg,
        )
    # Read failures need filesystem-level injection (read-only / permission).
    except OSError as exc:  # pragma: no cover
        msg = f"could not read file: {exc.strerror or exc}"
        logger.warning("template %s: %s", path, msg)
        return LoadedTemplate(
            path=path,
            filename=path.name,
            is_system=is_system,
            template=None,
            error=msg,
        )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"invalid JSON: {exc.msg} (line {exc.lineno})"
        logger.warning("template %s: %s", path, msg)
        return LoadedTemplate(
            path=path,
            filename=path.name,
            is_system=is_system,
            template=None,
            error=msg,
        )
    # Deeply-nested JSON exhausts the C decoder's recursion before it can
    # raise JSONDecodeError; keep the "never raises" contract so one crafted
    # file can't take down the whole listing.
    except RecursionError:
        msg = "invalid JSON: nesting too deep"
        logger.warning("template %s: %s", path, msg)
        return LoadedTemplate(
            path=path,
            filename=path.name,
            is_system=is_system,
            template=None,
            error=msg,
        )
    if not isinstance(data, dict):
        msg = f"top-level JSON value must be object, got {type(data).__name__}"
        logger.warning("template %s: %s", path, msg)
        return LoadedTemplate(
            path=path,
            filename=path.name,
            is_system=is_system,
            template=None,
            error=msg,
        )
    # Source folder is the authority for provenance; the on-disk
    # ``is_system`` tag is informational. Override before construction
    # so a crafted user file can't claim to be a system template.
    data["is_system"] = is_system
    try:
        template = parse_envelope(data)
    except TemplateValidationError as exc:
        msg = str(exc)
        logger.warning("template %s: %s", path, msg)
        return LoadedTemplate(
            path=path,
            filename=path.name,
            is_system=is_system,
            template=None,
            error=msg,
        )
    return LoadedTemplate(
        path=path,
        filename=path.name,
        is_system=is_system,
        template=template,
        error="",
    )


def _migrate_envelope(data: dict[str, Any]) -> dict[str, Any]:
    """Forward-migrate an on-disk envelope to the current format version.

    No-op for the only format version that exists today. This is the
    single seam where a future ``version`` bump lands its migration: read
    the incoming ``data["version"]``, transform the older shape into the
    current one, and return it with ``version`` updated. The strict gate in
    :meth:`OpenFollowTemplate.__post_init__` then accepts the migrated
    envelope. A version this build doesn't know how to migrate up from is
    left untouched and rejected by that gate with a clear message.
    """
    return data


def parse_envelope(data: dict[str, Any]) -> OpenFollowTemplate:
    """Build a validated :class:`OpenFollowTemplate` from a raw dict.

    Runs the forward-migration seam, filters to known envelope fields,
    then constructs (which validates the envelope and per-type payload).
    Raises :class:`TemplateValidationError` on any failure. Shared by the
    on-disk loader and the web import route so an uploaded file is held
    to the exact same rules as one read off disk – no second, drifting
    validator.
    """
    return OpenFollowTemplate(**_envelope_kwargs(_migrate_envelope(data)))


def _envelope_kwargs(data: dict[str, Any]) -> dict[str, Any]:
    """Filter the on-disk dict down to known envelope fields.

    A future version that adds envelope fields drops them silently here
    so ``OpenFollowTemplate(**data)`` doesn't raise ``TypeError`` on the
    unknown key; the version check then reports the mismatch clearly.
    """
    known = {
        "version",
        "type",
        "id",
        "name",
        "is_system",
        "app_version",
        "payload",
    }
    return {k: v for k, v in data.items() if k in known}


def list_templates(templates_root: Path) -> list[LoadedTemplate]:
    """Return every template file under ``<templates_root>/system/`` and
    ``<templates_root>/user/``, sorted by (folder, filename) for stable
    UI ordering.

    Both the canonical ``.oftemplate`` suffix and the legacy
    ``.openfollowtemplate`` one are picked up so templates saved by an
    earlier build still appear. Missing folders are treated as empty – a
    brand-new install with no seeded templates is a valid state (the
    bootstrap step normally seeds ``system/`` first, but the loader
    doesn't depend on it). Returns successful + failed loads in one flat
    list so callers can render both in the UI.
    """
    out: list[LoadedTemplate] = []
    for subdir, is_system in (("system", True), ("user", False)):
        folder = templates_root / subdir
        if not folder.is_dir():
            continue
        paths = {p for suffix in _READ_SUFFIXES for p in folder.glob(f"*{suffix}")}
        for path in sorted(paths):
            out.append(_load_one(path, is_system=is_system))
    return out


def list_templates_by_type(
    templates_root: Path,
    template_type: str,
) -> list[LoadedTemplate]:
    """Convenience filter – only return templates whose envelope
    ``type`` matches. Failed loads (whose envelope didn't decode at
    all) are dropped from this view because we can't tell what type
    they were meant to be; callers that need to surface broken files
    in the UI should call :func:`list_templates` directly and filter
    themselves."""
    return [
        entry
        for entry in list_templates(templates_root)
        if entry.template is not None and entry.template.type == template_type
    ]


def find_template(
    templates_root: Path,
    filename: str,
) -> LoadedTemplate | None:
    """Look up a single template by filename (basename). Returns
    ``None`` if no file with that name exists in either subfolder.

    Callers must validate that ``filename`` is a plain basename (no
    ``/`` / ``..``) before calling – the route layer does this for
    HTTP-facing entry points so a crafted request can't escape the
    templates folder.

    When the same basename exists in both ``user/`` and ``system/``
    (operators copy-and-edit system files into ``user/`` to customise),
    prefer the user copy so the operator's customised version wins. The
    original system file stays on disk and is reseeded by the bootstrap
    on next start; rename the user file out of the way to address the
    system copy specifically.
    """
    for subdir, is_system in (("user", False), ("system", True)):
        path = templates_root / subdir / filename
        if path.is_file():
            return _load_one(path, is_system=is_system)
    return None
