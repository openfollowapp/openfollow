# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Guard tests for the rpi-image-gen appliance layer.

The image build is config-only (no Python runtime), so ``make ci`` can't
exercise it on a flashed unit. These tests catch the failure modes that bit
past changes: the layer YAML must parse, and every shell script heredoc'd into
the rootfs must be syntactically valid with its ``#!/bin/sh`` landing at column
0 once YAML strips the block-scalar indentation.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_LAYER = Path(__file__).resolve().parents[1] / "packaging" / "image" / "layer" / "openfollow.yaml"


def _customize_hooks() -> list[str]:
    doc = yaml.safe_load(_LAYER.read_text(encoding="utf-8"))
    return list(doc["mmdebstrap"]["customize-hooks"])


def _heredoc_scripts(hook: str) -> list[str]:
    """Extract every ``cat > ... <<'TAG' ... TAG`` shell-script body in a hook."""
    bodies: list[str] = []
    for tag in re.findall(r"<<'([A-Z0-9_]+)'", hook):
        m = re.search(rf"<<'{tag}'\n(.*?)\n {{0,}}{tag}\n", hook, re.DOTALL)
        if m:
            bodies.append(m.group(1))
    return bodies


def test_layer_yaml_parses() -> None:
    doc = yaml.safe_load(_LAYER.read_text(encoding="utf-8"))
    assert "mmdebstrap" in doc
    assert isinstance(doc["mmdebstrap"]["customize-hooks"], list)


def test_parted_is_in_the_package_list() -> None:
    doc = yaml.safe_load(_LAYER.read_text(encoding="utf-8"))
    assert "parted" in doc["mmdebstrap"]["packages"]


def _mount_nvme_hook() -> str:
    hooks = [h for h in _customize_hooks() if "openfollow-mount-nvme.sh" in h]
    assert len(hooks) == 1, "expected exactly one NVMe mount hook"
    return hooks[0]


def test_mount_nvme_script_shebang_at_column_zero() -> None:
    # The shell body the device runs starts where YAML's block scalar stripped
    # the layer indentation; a stray indent would make #!/bin/sh inert.
    body = _heredoc_scripts(_mount_nvme_hook())[0]
    assert body.startswith("#!/bin/sh\n")


def _sh_n(body: str, tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    path.write_text(body + "\n", encoding="utf-8")
    proc = subprocess.run(["sh", "-n", str(path)], capture_output=True, text=True)
    assert proc.returncode == 0, f"sh -n failed:\n{proc.stderr}\n---\n{body}"


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX sh available")
def test_every_heredoc_script_is_sh_syntax_clean(tmp_path: Path) -> None:
    scripts = [body for hook in _customize_hooks() for body in _heredoc_scripts(hook)]
    # The growroot + mount-nvme hooks each ship one script.
    assert len(scripts) >= 2
    for i, body in enumerate(scripts):
        _sh_n(body, tmp_path, f"script_{i}.sh")


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX sh available")
def test_heredoc_carrying_hook_wrappers_are_sh_syntax_clean(tmp_path: Path) -> None:
    # The outer customize-hook shell (the cat/chmod/ln wrapper around the
    # heredocs) runs in the build chroot – it must parse too.
    wrappers = [h for h in _customize_hooks() if "<<'" in h]
    assert len(wrappers) >= 2
    for i, hook in enumerate(wrappers):
        _sh_n(hook, tmp_path, f"wrapper_{i}.sh")


def test_mount_nvme_unit_is_presence_gated_and_ordered_before_app() -> None:
    hook = _mount_nvme_hook()
    # Empty slot -> systemd skips the unit cleanly instead of failing.
    assert "ConditionPathExistsGlob=/dev/nvme*" in hook
    # Storage is ready before the kiosk app starts.
    assert "Before=openfollow.service" in hook
    # nofail keeps a later drive removal from blocking boot.
    assert "nofail" in hook


def test_mount_nvme_never_reformats_existing_data() -> None:
    body = _heredoc_scripts(_mount_nvme_hook())[0]
    # The format path is gated on a truly-blank disk: zero partitions AND no
    # wipefs signatures. A disk with data is left untouched.
    assert "wipefs --no-act" in body
    assert 'part_count" -eq 0' in body
    assert "leaving untouched" in body
