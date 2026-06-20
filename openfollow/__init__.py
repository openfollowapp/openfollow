# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Top-level package for openfollow.

OpenFollowApp is lazily imported to avoid loading GObject/GStreamer until needed.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    # Python 3.10 lacks tomllib; use tomli backport
    import tomli as tomllib  # type: ignore[no-redef]

if TYPE_CHECKING:
    from openfollow.app import OpenFollowApp


_VERSION_FALLBACK = "0.0.0+unknown"

# Directory markers for installed wheels; avoid picking up repo's pyproject.toml
# when running from a venv inside the checkout.
_INSTALL_DIR_MARKERS = frozenset({"site-packages", "dist-packages"})


def _find_pyproject(start: str = __file__) -> Path | None:
    """Locate checkout pyproject.toml; None for wheel installs."""
    resolved = Path(start).resolve()
    if any(parent.name in _INSTALL_DIR_MARKERS for parent in resolved.parents):
        return None
    for parent in resolved.parents:
        candidate = parent / "pyproject.toml"
        if candidate.is_file():
            return candidate
    return None


_PYPROJECT = _find_pyproject()


def _version_from_pyproject() -> str | None:
    """Return [project] version from pyproject.toml, None if not in source tree."""
    if _PYPROJECT is None:
        return None
    try:
        with open(_PYPROJECT, "rb") as f:
            data = tomllib.load(f)
    except (OSError, ValueError):
        return None
    project = data.get("project")
    if not isinstance(project, dict) or project.get("name") != "openfollow":
        return None
    version = project.get("version")
    return version if isinstance(version, str) and version else None


def _detect_version() -> str:
    """Resolve package version: pyproject.toml > importlib.metadata > fallback."""
    from_pyproject = _version_from_pyproject()
    if from_pyproject is not None:
        return from_pyproject
    try:
        return _pkg_version("openfollow")
    except PackageNotFoundError:
        return _VERSION_FALLBACK


def _read_git_head_commit(git_dir: Path) -> str | None:
    """Resolve the commit sha ``.git/HEAD`` points at (loose or packed ref)."""
    try:
        head = (git_dir / "HEAD").read_text().strip()
    except OSError:
        return None
    if not head.startswith("ref:"):
        return head or None  # detached HEAD holds the sha directly
    ref = head.split(":", 1)[1].strip()
    try:
        return (git_dir / ref).read_text().strip() or None
    except OSError:
        pass  # loose ref absent – fall back to packed-refs
    try:
        packed = (git_dir / "packed-refs").read_text()
    except OSError:
        return None
    for line in packed.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == ref:
            return parts[0]
    return None


def _detect_commit(pyproject: Path | None = _PYPROJECT) -> str | None:
    """Short git commit (8 chars) of the checkout, or None for a non-checkout
    (wheel / .deb / image) install where ``.git`` isn't present."""
    if pyproject is None:
        return None
    sha = _read_git_head_commit(pyproject.parent / ".git")
    return sha[:8] if sha else None


__version__ = _detect_version()
__commit__ = _detect_commit()

__all__ = ["OpenFollowApp", "__commit__", "__version__"]


def __getattr__(name: str) -> Any:
    if name == "OpenFollowApp":
        from openfollow.app import OpenFollowApp as _OpenFollowApp

        return _OpenFollowApp
    raise AttributeError(f"module 'openfollow' has no attribute {name!r}")
