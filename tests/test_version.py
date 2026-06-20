# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Coverage for :func:`openfollow._detect_version` and its sources.

Prefers checkout ``pyproject.toml`` over install metadata to ensure the
displayed version matches running code, not stale dist-info."""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError

import pytest

import openfollow

pytestmark = pytest.mark.unit


def _declared_project_version(pyproject_text: str) -> str | None:
    """Parser-independent read of ``[project] version`` from raw TOML text.

    Deliberately does NOT use ``tomllib`` – it cross-checks the same value
    the production code derives via ``tomllib``, so it must not share that
    path.  Scans line by line, tracks the *current* table so it only reads
    ``version`` inside ``[project]`` (never ``[tool.poetry]`` or
    ``[project.scripts]``), and accepts either quote style.
    """
    in_project = False
    for line in pyproject_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_project = stripped == "[project]"
            continue
        if in_project:
            match = re.match(r"""version\s*=\s*['"]([^'"]+)['"]""", stripped)
            if match:
                return match.group(1)
    return None


def test_version_matches_pyproject_in_source_checkout() -> None:
    """Imported version matches [project] version in working tree's
    pyproject.toml, not stale install metadata."""
    pyproject = openfollow._PYPROJECT
    if pyproject is None or not pyproject.is_file():
        pytest.skip("not running from a source checkout")

    declared = _declared_project_version(pyproject.read_text(encoding="utf-8"))
    assert declared is not None, "no [project] version in pyproject.toml"

    assert openfollow._version_from_pyproject() == declared
    assert openfollow._detect_version() == declared
    assert openfollow.__version__ == declared


def test_detect_version_prefers_pyproject_over_metadata(monkeypatch) -> None:
    """A stale dist-info must not win when the working tree is readable."""
    monkeypatch.setattr(openfollow, "_version_from_pyproject", lambda: "9.9.9")
    monkeypatch.setattr(openfollow, "_pkg_version", lambda _name: "0.0.1-stale")
    assert openfollow._detect_version() == "9.9.9"


def test_detect_version_falls_back_to_metadata(monkeypatch) -> None:
    """Wheel install with no ``pyproject.toml`` alongside the package:
    ``_version_from_pyproject`` returns ``None`` and metadata is used."""
    monkeypatch.setattr(openfollow, "_version_from_pyproject", lambda: None)
    monkeypatch.setattr(openfollow, "_pkg_version", lambda _name: "1.2.3")
    assert openfollow._detect_version() == "1.2.3"


def test_detect_version_falls_back_to_sentinel(monkeypatch) -> None:
    """Neither source available → the ``0.0.0+unknown`` sentinel."""
    monkeypatch.setattr(openfollow, "_version_from_pyproject", lambda: None)

    def _raise(_name: str) -> str:
        raise PackageNotFoundError(_name)

    monkeypatch.setattr(openfollow, "_pkg_version", _raise)
    assert openfollow._detect_version() == "0.0.0+unknown"


def test_find_pyproject_walks_up_to_ancestor(tmp_path) -> None:
    """Ascends from package file to first ancestor with pyproject.toml;
    handles nested and src-layout configurations."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    nested = tmp_path / "src" / "pkg"
    nested.mkdir(parents=True)
    found = openfollow._find_pyproject(str(nested / "__init__.py"))
    assert found == tmp_path / "pyproject.toml"


def test_find_pyproject_returns_none_without_ancestor(tmp_path) -> None:
    """No ``pyproject.toml`` anywhere above the start (wheel install) →
    ``None``, and the caller falls through to install metadata."""
    start = tmp_path / "lib" / "openfollow" / "__init__.py"
    assert openfollow._find_pyproject(str(start)) is None


@pytest.mark.parametrize("install_dir", ["site-packages", "dist-packages"])
def test_find_pyproject_skips_installed_package_dirs(tmp_path, install_dir) -> None:
    """Skip ancestor pyproject.toml files when walking from installed
    package dirs; fall through to importlib.metadata instead."""
    # An ancestor pyproject that the unguarded walk would wrongly pick up.
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "openfollow"\nversion = "9.9.9"\n',
        encoding="utf-8",
    )
    installed = tmp_path / install_dir / "openfollow" / "__init__.py"
    installed.parent.mkdir(parents=True)
    assert openfollow._find_pyproject(str(installed)) is None


def test_version_from_pyproject_not_a_source_tree(monkeypatch) -> None:
    """When ``_PYPROJECT`` is ``None`` (no source pyproject located),
    ``_version_from_pyproject`` short-circuits to ``None`` without I/O."""
    monkeypatch.setattr(openfollow, "_PYPROJECT", None)
    assert openfollow._version_from_pyproject() is None


def test_version_from_pyproject_missing_file(monkeypatch, tmp_path) -> None:
    """An unreadable / absent ``pyproject.toml`` yields ``None`` (caller
    then falls through to install metadata)."""
    monkeypatch.setattr(openfollow, "_PYPROJECT", tmp_path / "absent.toml")
    assert openfollow._version_from_pyproject() is None


def test_version_from_pyproject_rejects_foreign_project(monkeypatch, tmp_path) -> None:
    foreign = tmp_path / "pyproject.toml"
    foreign.write_text(
        '[project]\nname = "not-openfollow"\nversion = "5.5.5"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(openfollow, "_PYPROJECT", foreign)
    assert openfollow._version_from_pyproject() is None


def test_version_from_pyproject_handles_malformed_toml(monkeypatch, tmp_path) -> None:
    """Corrupt TOML is swallowed (returns ``None``) rather than crashing
    every ``import openfollow``."""
    broken = tmp_path / "pyproject.toml"
    broken.write_text("[project\nname = ", encoding="utf-8")
    monkeypatch.setattr(openfollow, "_PYPROJECT", broken)
    assert openfollow._version_from_pyproject() is None


def test_version_from_pyproject_handles_non_utf8(monkeypatch, tmp_path) -> None:
    binary = tmp_path / "pyproject.toml"
    binary.write_bytes(b"\xff\xfe\x00not utf-8 \x80\x81")
    monkeypatch.setattr(openfollow, "_PYPROJECT", binary)
    assert openfollow._version_from_pyproject() is None


def test_version_from_pyproject_missing_version_key(monkeypatch, tmp_path) -> None:
    """``[project]`` present but without a ``version`` → ``None``."""
    no_version = tmp_path / "pyproject.toml"
    no_version.write_text('[project]\nname = "openfollow"\n', encoding="utf-8")
    monkeypatch.setattr(openfollow, "_PYPROJECT", no_version)
    assert openfollow._version_from_pyproject() is None


# ---------------------------------------------------------------------------
# git commit detection – the footer / About "v… (de678f4e)" suffix
# ---------------------------------------------------------------------------

_SHA = "de678f4e8c0ffee0c0ffee0c0ffee0c0ffee1234"


def _git_dir_with_head(tmp_path, head: str):
    git_dir = tmp_path / ".git"
    git_dir.mkdir(parents=True, exist_ok=True)
    (git_dir / "HEAD").write_text(head, encoding="utf-8")
    return git_dir


def _write_loose_ref(git_dir, ref: str, content: str) -> None:
    path = git_dir / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_commit_is_short_hex_or_none() -> None:
    c = openfollow.__commit__
    assert c is None or (len(c) == 8 and all(ch in "0123456789abcdef" for ch in c))


def test_detect_commit_none_without_checkout() -> None:
    assert openfollow._detect_commit(None) is None


def test_detect_commit_shortens_to_eight(tmp_path) -> None:
    git_dir = _git_dir_with_head(tmp_path, "ref: refs/heads/main\n")
    _write_loose_ref(git_dir, "refs/heads/main", _SHA + "\n")
    assert openfollow._detect_commit(tmp_path / "pyproject.toml") == _SHA[:8]


def test_detect_commit_none_when_unresolvable(tmp_path) -> None:
    # pyproject present but no .git alongside → HEAD read fails → None.
    assert openfollow._detect_commit(tmp_path / "pyproject.toml") is None


def test_read_git_head_missing_head(tmp_path) -> None:
    assert openfollow._read_git_head_commit(tmp_path / ".git") is None


def test_read_git_head_detached(tmp_path) -> None:
    git_dir = _git_dir_with_head(tmp_path, _SHA + "\n")
    assert openfollow._read_git_head_commit(git_dir) == _SHA


def test_read_git_head_detached_empty(tmp_path) -> None:
    git_dir = _git_dir_with_head(tmp_path, "\n")
    assert openfollow._read_git_head_commit(git_dir) is None


def test_read_git_head_symbolic_loose_ref(tmp_path) -> None:
    git_dir = _git_dir_with_head(tmp_path, "ref: refs/heads/main\n")
    _write_loose_ref(git_dir, "refs/heads/main", _SHA + "\n")
    assert openfollow._read_git_head_commit(git_dir) == _SHA


def test_read_git_head_symbolic_empty_loose_ref(tmp_path) -> None:
    git_dir = _git_dir_with_head(tmp_path, "ref: refs/heads/main\n")
    _write_loose_ref(git_dir, "refs/heads/main", "\n")
    assert openfollow._read_git_head_commit(git_dir) is None


def test_read_git_head_symbolic_packed_ref(tmp_path) -> None:
    git_dir = _git_dir_with_head(tmp_path, "ref: refs/heads/main\n")
    # No loose ref; the ref lives in packed-refs (a comment line + a peeled tag
    # line also present, so the len-2 filter is exercised).
    (git_dir / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted\n"
        f"^cafebabecafebabecafebabecafebabecafebabe\n"
        f"{_SHA} refs/heads/main\n",
        encoding="utf-8",
    )
    assert openfollow._read_git_head_commit(git_dir) == _SHA


def test_read_git_head_symbolic_no_packed_refs(tmp_path) -> None:
    git_dir = _git_dir_with_head(tmp_path, "ref: refs/heads/main\n")
    # Loose ref absent and no packed-refs file → None.
    assert openfollow._read_git_head_commit(git_dir) is None


def test_read_git_head_symbolic_ref_not_in_packed(tmp_path) -> None:
    git_dir = _git_dir_with_head(tmp_path, "ref: refs/heads/main\n")
    (git_dir / "packed-refs").write_text(f"{_SHA} refs/heads/other\n", encoding="utf-8")
    assert openfollow._read_git_head_commit(git_dir) is None
