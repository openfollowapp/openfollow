# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Checks for scripts/install-detection.sh: storage preflight, NVMe handling,
and the export toolchain."""

from __future__ import annotations

import inspect
import os
import shlex
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _script() -> Path:
    source = inspect.getsourcefile(_script)
    assert source, "Could not resolve current test source path"
    return Path(source).resolve().parents[1] / "scripts" / "install-detection.sh"


def _run(snippet: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Source the script (defs only – main is guarded) and run a snippet."""
    code = f'source "{_script()}"\n{snippet}\n'
    full_env = {"PATH": os.environ.get("PATH", "")}
    if env:
        full_env.update(env)
    return subprocess.run(["bash", "-c", code], text=True, capture_output=True, env=full_env)


# --- storage preflight ------------------------------------------------------


def test_require_free_mb_passes_when_enough() -> None:
    r = _run('require_free_mb "main storage" 5000 4096 /x')
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout


def test_require_free_mb_fails_loudly_when_short() -> None:
    r = _run('require_free_mb "main storage" 100 4096 /x')
    assert r.returncode != 0
    # Names the shortfall with concrete numbers so the operator knows why.
    assert "not enough space" in r.stderr
    assert "100" in r.stderr and "4096" in r.stderr


def test_warn_low_model_storage_warns_under_threshold() -> None:
    r = _run("warn_low_model_storage 1000 24576 /models")
    assert r.returncode == 0, r.stderr
    assert "WARNING" in r.stderr
    assert "only small models" in r.stderr


def test_warn_low_model_storage_silent_when_ample() -> None:
    r = _run("warn_low_model_storage 50000 24576 /models")
    assert r.returncode == 0, r.stderr
    assert r.stderr.strip() == ""


def test_free_mb_returns_positive_int_for_root() -> None:
    r = _run("free_mb /")
    assert r.returncode == 0, r.stderr
    assert int(r.stdout.strip()) > 0


def test_free_mb_walks_to_existing_ancestor_for_missing_path() -> None:
    # A not-yet-created target (e.g. a fresh NVMe model dir before first install)
    # must report its filesystem's free space via the nearest existing ancestor,
    # not 0 – otherwise the advisory warns "only small models fit" on a big NVMe.
    r = _run("free_mb /tmp/openfollow-does-not-exist-xyz/deeper/still")
    assert r.returncode == 0, r.stderr
    assert int(r.stdout.strip()) > 0


# --- NVMe model dir ---------------------------------------------------------


def test_nvme_model_dir_when_mounted() -> None:
    r = _run("nvme_model_dir 1 /mnt/nvme")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "/mnt/nvme/openfollow/yolo"


def test_nvme_model_dir_empty_when_absent() -> None:
    r = _run("nvme_model_dir 0 /mnt/nvme")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == ""


# --- script structure -------------------------------------------------------


def test_preflight_runs_before_any_install() -> None:
    text = _script().read_text()
    preflight = text.index('require_free_mb "main storage"')
    install = text.index("pip install")
    assert preflight < install, "storage preflight must run before any pip install"


def test_refuses_to_run_as_root_and_pins_install_targets() -> None:
    text = _script().read_text()
    assert '[ "$(id -u)" -ne 0 ]' in text  # not root
    # CPU-only torch index + the pinned floors must not silently drift.
    assert "download.pytorch.org/whl/cpu" in text
    assert "onnxruntime>=1.17" in text
    assert "opencv-python>=4.8" in text
    assert "ultralytics" in text


def test_export_toolchain_includes_onnx_and_onnxslim() -> None:
    # The export shells out to ultralytics, which needs onnx + onnxslim in the
    # SAME venv. They were missing before, so export failed with "No module
    # named 'onnx'"; the install line must pull them in.
    text = _script().read_text()
    install_line = next(line for line in text.splitlines() if "pip install" in line and "ultralytics" in line)
    assert " onnx " in f" {install_line} "
    assert "onnxslim" in install_line


def test_ultralytics_is_a_soft_dependency_not_a_hard_gate() -> None:
    text = _script().read_text()
    # The post-install hard gate is the onnxruntime + opencv backend.
    assert 'die "detection backend still not importable after install."' in text
    # the export tools (ultralytics + onnx) are probed, but a failure warns,
    # never dies – so a missing export toolchain can't fail a working install.
    assert "import ultralytics, onnx" in text
    assert 'die "detection backend' in text
    assert 'die "ultralytics' not in text and "die 'ultralytics" not in text


# --- clock-skew diagnostics -------------------------------------------------


def _classify(sample: str) -> subprocess.CompletedProcess[str]:
    # Pipe a captured-output sample through clock_skew_in and report the verdict.
    return _run(f"printf '%s' {shlex.quote(sample)} | clock_skew_in && echo SKEW || echo CLEAN")


@pytest.mark.parametrize(
    "sample",
    [
        # The pip / TLS form: a wrong clock makes PyPI's cert look future-dated.
        "ERROR: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate is not yet valid (_ssl.c:1006)",
        # The apt-style wording, in case a clock-skew run logs it.
        "Verifying signature: Not live until 2026-06-25T01:42:42Z",
    ],
)
def test_clock_skew_in_detects_future_dated_tls(sample: str) -> None:
    r = _classify(sample)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "SKEW"


@pytest.mark.parametrize(
    "sample",
    [
        "Successfully installed onnxruntime-1.17.0 opencv-python-4.8.0",
        "ERROR: Could not find a version that satisfies the requirement foo",
        "ERROR: connection timed out",
        # A plain cert-verify failure (bad CA, MITM) is NOT a clock problem and
        # must not be misattributed to the clock.
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate",
    ],
)
def test_clock_skew_in_ignores_unrelated_failures(sample: str) -> None:
    r = _classify(sample)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "CLEAN"


def test_clock_skew_message_is_actionable() -> None:
    r = _run("clock_skew_message")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "clock" in out.lower()
    assert "timedatectl" in out
    # ``date -u -s`` so the "enter UTC" instruction is correct regardless of the
    # host timezone; the ambiguous bare ``date -s '...'`` form must be gone.
    assert "date -u -s" in out
    assert "date -s '" not in out


def test_pip_failures_route_through_clock_skew_check() -> None:
    text = _script().read_text()
    # Every pip install path must capture output and consult clock_skew_in on
    # failure, so a wrong clock surfaces as the fix, not a raw SSL traceback.
    assert 'die "$(clock_skew_message)"' in text
    # torch (incl. fallback) + the backend install = at least three checks.
    assert text.count("clock_skew_in <") >= 3


def test_pip_log_fallback_path_is_unique_per_run() -> None:
    # When mktemp fails, the fallback log path must be PID-suffixed so concurrent
    # runs can't collide on (or clobber a pre-existing) fixed /tmp file.
    text = _script().read_text()
    assert 'echo "/tmp/openfollow-install-detection-pip.$$.log"' in text
