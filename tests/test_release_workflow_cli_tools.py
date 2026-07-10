# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Invariant checks for release workflow CLI dependencies."""

from __future__ import annotations

import inspect
import os
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _workflow_path() -> Path:
    source = inspect.getsourcefile(_workflow_path)
    assert source, "Could not resolve current test source path"
    here = Path(source)
    return here.resolve().parents[1] / ".github" / "workflows" / "release-deb.yml"


def _build_deb_path() -> Path:
    source = inspect.getsourcefile(_build_deb_path)
    assert source, "Could not resolve current test source path"
    here = Path(source)
    return here.resolve().parents[1] / "packaging" / "build-deb.sh"


def _pep440_from_build_script(raw_version: str) -> str:
    lines = _build_deb_path().read_text().splitlines()
    start = next(
        (idx for idx, line in enumerate(lines) if line.strip().startswith('pep440_version="$(printf')),
        -1,
    )
    assert start >= 0, "pep440 version transform start not found in build-deb.sh"
    end = next((idx for idx in range(start, len(lines)) if lines[idx].strip() == "esac"), -1)
    assert end >= 0, "pep440 version transform case block not found in build-deb.sh"
    snippet = "\n".join(lines[start : end + 1])
    script = f'raw_version="$RAW_VERSION"\n{snippet}\nprintf "%s" "$pep440_version"\n'
    out = subprocess.run(
        ["bash"],
        input=script,
        text=True,
        capture_output=True,
        check=True,
        env={**os.environ, "RAW_VERSION": raw_version},
    ).stdout
    return out.strip()


def test_release_build_job_installs_gh_cli() -> None:
    text = _workflow_path().read_text()
    assert "gh release upload" in text
    m = re.search(
        r"- name: Install build dependencies\b.*?(apt-get install -y --no-install-recommends[^\n]*)",
        text,
        re.DOTALL,
    )
    assert m, "release workflow build dependency install command not found"
    install_line = m.group(1)
    assert re.search(r"\bgh\b", install_line), f"`gh` missing from install command: {install_line}"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.2.6-rc10f1", "0.2.6rc10+f1"),
        ("0.2.4-rc9-citest", "0.2.4rc9+citest"),
        ("0.2.4-rc12", "0.2.4rc12"),
    ],
)
def test_build_deb_pep440_version_transform(raw: str, expected: str) -> None:
    assert _pep440_from_build_script(raw) == expected


def test_build_deb_model_export_pins_cpu_torch() -> None:
    """The amd64 build must not pull the multi-GB CUDA torch wheel: the model
    export venv installs CPU-only torch from the PyTorch CPU index *before*
    ultralytics, so ultralytics' torch dep is already satisfied (the arm64
    default is CPU-only anyway). A bare `pip install ultralytics` would silently
    reintroduce the CUDA download on x86_64."""
    text = _build_deb_path().read_text()
    cpu_idx = text.find("https://download.pytorch.org/whl/cpu")
    assert cpu_idx >= 0, "model export must install CPU-only torch from the PyTorch CPU index"
    ultra = text.find('"ultralytics>=8.4.72"')
    assert ultra >= 0, "model export ultralytics install not found"
    assert cpu_idx < ultra, "CPU-only torch must be installed before ultralytics so its torch dep is pre-satisfied"


def _visudo_resolution_snippet() -> str:
    lines = _build_deb_path().read_text().splitlines()
    start = next((idx for idx, line in enumerate(lines) if "command -v visudo" in line), -1)
    assert start >= 0, "visudo resolution block not found in build-deb.sh"
    end = next((idx for idx in range(start, len(lines)) if 'die "visudo not found' in lines[idx]), -1)
    assert end >= 0, "visudo not-found guard not found in build-deb.sh"
    return "\n".join(lines[start : end + 1])


@pytest.mark.skipif(
    not any(os.path.exists(p) for p in ("/usr/sbin/visudo", "/sbin/visudo", "/usr/bin/visudo")),
    reason="visudo not installed on this host",
)
def test_build_deb_resolves_visudo_without_sbin_on_path() -> None:
    """A non-root `build-deb.sh` must still find visudo. visudo lives in
    /usr/sbin, which is absent from a normal user's PATH – CI builds as root
    (where it is on PATH) so a bare `visudo` call regressed silently. Run the
    resolution snippet with /usr/sbin off PATH and assert it still resolves."""
    script = (
        f'die() {{ printf "DIE:%s\\n" "$*" >&2; exit 3; }}\n{_visudo_resolution_snippet()}\nprintf "%s" "$visudo_bin"\n'
    )
    result = subprocess.run(
        ["bash"],
        input=script,
        text=True,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, f"visudo resolution failed: {result.stderr}"
    resolved = result.stdout.strip()
    assert resolved and os.path.exists(resolved), f"resolved visudo path missing: {resolved!r}"
