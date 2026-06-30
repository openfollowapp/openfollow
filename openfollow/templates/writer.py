# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Slugify operator names and write .oftemplate files with conflict resolution."""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

from openfollow.templates.schema import (
    TEMPLATE_FILE_SUFFIX,
    TEMPLATE_LEGACY_SUFFIX,
    TEMPLATE_VERSION,
    VALID_TYPES,
    OpenFollowTemplate,
    _new_uuid_hex,
)

logger = logging.getLogger(__name__)

_SLUG_KEEP_RE = re.compile(r"[^a-z0-9-]+")  # keep ASCII lowercase, digits, dashes
_SLUG_COLLAPSE_RE = re.compile(r"-+")  # collapse multiple dashes
_SLUG_MAX_LEN: int = 64  # max slug length for filesystem compatibility

# Ceiling on the conflict-resolution loop; past this, manual cleanup
# beats further auto-numbering – surface the failure, don't spin forever.
_CONFLICT_NUMBER_MAX: int = 1024


class TemplateWriteError(RuntimeError):
    """Raised when a template can't be written to disk.

    Reasons today: writes refused because the target lands under
    ``templates/system/`` (defence-in-depth – the route layer's gate
    is the primary check); the conflict-numbering loop ran out of
    tries; the underlying ``write_text`` raised an OS error. The
    exception message is the operator-facing reason.
    """


def slugify(name: str) -> str:
    """Normalise an operator-typed name into a filename-safe slug.

    Pipeline:
    1. NFKD-normalise so ``é`` decomposes to ``e + combining acute``,
       then strip combining marks. The operator gets a recognisable
       ASCII slug for accented input rather than a percent-encoded
       blob in the filename.
    2. Lowercase.
    3. Replace runs of non-slug chars with a single dash.
    4. Trim leading / trailing dashes.
    5. Truncate to ``_SLUG_MAX_LEN``, then trim trailing dashes again
       (so the truncation never leaves a dangling separator).

    Empty / fully-stripped input falls back to ``"untitled"`` so the
    filename is always non-empty. The conflict resolver upstream will
    then number it ``untitled-1``, ``untitled-2``, … if needed.
    """
    if not isinstance(name, str):
        return "untitled"
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    dashed = _SLUG_KEEP_RE.sub("-", lowered)
    collapsed = _SLUG_COLLAPSE_RE.sub("-", dashed).strip("-")
    if len(collapsed) > _SLUG_MAX_LEN:
        collapsed = collapsed[:_SLUG_MAX_LEN].rstrip("-")
    return collapsed or "untitled"


def _build_filename(template_type: str, slug: str, suffix_n: int) -> str:
    """Compose ``<type>.<slug>[-N].oftemplate``.

    ``suffix_n=0`` means "no suffix" – the first attempt at saving a
    template uses the bare slug. Non-zero N appends ``-N`` between the
    slug and the file suffix, so the dot-separator structure stays
    intact.
    """
    if suffix_n == 0:
        return f"{template_type}.{slug}{TEMPLATE_FILE_SUFFIX}"
    return f"{template_type}.{slug}-{suffix_n}{TEMPLATE_FILE_SUFFIX}"


def _disambiguate_name(name: str, suffix_n: int) -> str:
    """Mirror the filename's suffix into the JSON ``name`` field so
    the operator sees the same disambiguation in the UI label as on
    disk."""
    if suffix_n == 0:
        return name
    return f"{name} ({suffix_n})"


def _current_app_version() -> str:
    """The running build's version, stamped into freshly-authored templates."""
    import openfollow

    return getattr(openfollow, "__version__", "")


def _atomic_create(path: Path) -> Any:
    """Atomic ``O_CREAT | O_EXCL`` open for writing. Returns the
    file-descriptor wrapped in a Python text stream, or raises
    ``FileExistsError`` when the path is already taken.

    Makes the conflict-numbering loop in :func:`write_user_template`
    race-free under the threaded WSGI server: two concurrent saves of
    the same name can't both pick the same suffix and overwrite each
    other – the second save's ``O_EXCL`` raises and the loop bumps to
    the next suffix.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    return os.fdopen(fd, "w", encoding="utf-8")


def write_user_template(
    templates_root: Path,
    template_type: str,
    name: str,
    payload: dict[str, Any],
    *,
    template_id: str | None = None,
    app_version: str | None = None,
) -> Path:
    """Save a template under ``templates/user/`` and return the
    written file's absolute path.

    ``app_version`` records which OpenFollow build authored the payload
    (diagnostics only – see :class:`OpenFollowTemplate`). ``None`` stamps
    the running build, which is what a fresh "Save as template" wants; an
    explicit value (including ``""``) is preserved verbatim, so an import
    keeps the originating build's provenance instead of masquerading as
    this one.

    - Validates ``template_type`` against :data:`VALID_TYPES`.
    - Slugifies ``name`` and resolves filename conflicts atomically
      via ``O_EXCL``: each candidate filename is opened with
      ``O_WRONLY | O_CREAT | O_EXCL``; on ``FileExistsError`` the
      loop bumps the suffix and retries. Race-free under the threaded
      WSGI server, where two concurrent saves could otherwise pick the
      same suffix and silently overwrite each other.
    - Mints a fresh UUID hex when ``template_id`` is omitted; an
      explicit id is used as-is so a "rename in place" flow can
      preserve the operator's stable handle.
    - Writes the JSON pretty-printed (2-space indent) so hand-editing
      is reasonable.

    Raises :class:`TemplateWriteError` on validation failure, conflict
    exhaustion, or OS-level write failure. The envelope construction
    runs the same payload validator the loader uses, so a bad payload
    surfaces here BEFORE the file lands on disk – never write a
    template the loader would reject.
    """
    if template_type not in VALID_TYPES:
        raise TemplateWriteError(
            f"unknown template type {template_type!r} (expected one of {', '.join(VALID_TYPES)})",
        )
    if not isinstance(name, str) or not name.strip():
        raise TemplateWriteError("name must be a non-empty string")
    slug = slugify(name)
    user_dir = templates_root / "user"
    system_dir = templates_root / "system"
    user_dir.mkdir(parents=True, exist_ok=True)
    minted_id = template_id.strip() if isinstance(template_id, str) and template_id.strip() else _new_uuid_hex()
    stamped_app_version = _current_app_version() if app_version is None else app_version
    last_exc: Exception | None = None
    for n in range(_CONFLICT_NUMBER_MAX):
        filename = _build_filename(template_type, slug, n)
        # System-folder collision is a quick non-racy check (system
        # files only land via the bootstrap, which serialises with
        # the server start). The user-folder collision for the canonical
        # name is what the atomic create handles. A same-slug file under
        # the legacy suffix (an upgraded install) is also a collision –
        # disambiguate so the operator doesn't get two identically-named
        # rows side by side.
        legacy_name = filename[: -len(TEMPLATE_FILE_SUFFIX)] + TEMPLATE_LEGACY_SUFFIX
        if (system_dir / filename).exists() or (system_dir / legacy_name).exists() or (user_dir / legacy_name).exists():
            continue
        target = user_dir / filename
        final_name = _disambiguate_name(name.strip(), n)
        template = OpenFollowTemplate(
            version=TEMPLATE_VERSION,
            type=template_type,
            id=minted_id,
            name=final_name,
            is_system=False,
            app_version=stamped_app_version,
            payload=payload,
        )
        serialised = json.dumps(
            template.to_dict(),
            indent=2,
            sort_keys=False,
        )
        try:
            stream = _atomic_create(target)
        except FileExistsError:
            # Another writer took this filename; try the next suffix.
            # Not a real failure, so clear ``last_exc``.
            last_exc = None
            continue
        # Any other OSError (read-only mount / perm) is a write failure.
        except OSError as exc:  # pragma: no cover
            last_exc = exc
            break
        try:
            with stream:
                stream.write(serialised + "\n")
        # Write failure mid-flush. Drop the partial file so the next
        # save attempt starts from a clean slate.
        except OSError as exc:  # pragma: no cover
            try:
                target.unlink()
            except OSError:
                pass
            raise TemplateWriteError(
                f"could not write {target}: {exc.strerror or exc}",
            ) from exc
        logger.info("wrote user template %s", target)
        return target
    if last_exc is not None:  # pragma: no cover – defensive
        raise TemplateWriteError(
            f"could not write template: {last_exc}",
        ) from last_exc
    raise TemplateWriteError(
        f"could not find a free filename for "
        f"{template_type}.{slug} after {_CONFLICT_NUMBER_MAX} tries; "
        f"clean up the templates folder and try again",
    )


def delete_user_template(templates_root: Path, filename: str) -> bool:
    """Delete a template under ``templates/user/``. Returns ``True`` on
    success, ``False`` when no file with that name exists.

    Refuses to delete from ``templates/system/`` even if the operator
    asked for it – the route layer also gates this, but the
    defence-in-depth check here means a misuse from a different entry
    point can't bypass it.

    ``filename`` must be a plain basename (no ``/`` / ``..``); callers
    are expected to validate that before calling. The function also
    re-checks via ``Path.parent == user_dir`` because a path-traversal
    string could otherwise resolve outside the folder even after
    rejection at the route layer (defence-in-depth – and the cost is
    one extra ``Path`` resolution per call).
    """
    user_dir = (templates_root / "user").resolve()
    target = (user_dir / filename).resolve()
    # ``target.parent`` resolves through any ``..`` in the supplied
    # filename, so a crafted ``filename = "../system/foo"`` lands its
    # parent outside ``user_dir`` even though ``user_dir / filename``
    # composed the path with the user folder. The equality check after
    # ``.resolve()`` rejects anything that escapes the folder.
    if target.parent != user_dir:
        raise TemplateWriteError(
            f"refusing to delete outside user templates folder: {filename!r}",
        )
    if not target.is_file():
        return False
    try:
        target.unlink()
    # Unlink failures need filesystem-level injection (read-only / perm).
    except OSError as exc:  # pragma: no cover
        raise TemplateWriteError(
            f"could not delete {target}: {exc.strerror or exc}",
        ) from exc
    logger.info("deleted user template %s", target)
    return True
