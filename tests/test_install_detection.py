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


# --- behavioral: run main() against a stub venv python + pip ----------------
#
# The stub python answers the import probes (backend / export present?), reports
# a writable purelib (so no sudo), and logs each `pip install` invocation instead
# of running it, so tests can assert what the script WOULD install, in order.

_STUB_PY = r"""#!/usr/bin/env bash
sd="${STUB_STATE_DIR:-/tmp}"
has_backend() { [ "${STUB_BACKEND:-1}" = "1" ] || [ -f "$sd/backend" ]; }
has_export() { [ "${STUB_EXPORT:-1}" = "1" ] || [ -f "$sd/export" ]; }
if [ "$1" = "-c" ]; then
  case "$2" in
    *"import onnxruntime, cv2"*) has_backend && exit 0 || exit 1 ;;
    *"import ultralytics, onnx"*) has_export && exit 0 || exit 1 ;;
    *purelib*) printf '%s\n' "${STUB_PURELIB:-/tmp}"; exit 0 ;;
    *) exit 0 ;;
  esac
fi
if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "install" ]; then
  shift 3
  args="$*"
  printf 'PIPINSTALL %s\n' "$args" >> "$STUB_PIP_LOG"
  if [ -n "${STUB_PIP_FAIL:-}" ] && printf '%s' "$args" | grep -qE "$STUB_PIP_FAIL"; then
    printf '%s\n' "${STUB_FAIL_MSG:-simulated failure}" >&2
    exit 1
  fi
  # A successful install makes the module importable on subsequent probes.
  case "$args" in *onnxruntime*) : >"$sd/backend" ;; esac
  case "$args" in *ultralytics*) : >"$sd/export" ;; esac
  exit 0
fi
exit 0
"""

_skip_if_root = pytest.mark.skipif(os.geteuid() == 0, reason="install-detection.sh refuses to run as root")


def _run_main(
    tmp_path: Path,
    args: list[str],
    *,
    backend: int = 0,
    export: int = 0,
    pip_fail: str = "",
    fail_msg: str = "simulated failure",
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    py = venv_bin / "python"
    py.write_text(_STUB_PY)
    py.chmod(0o755)
    piplog = tmp_path / "pip.log"
    piplog.write_text("")
    state = tmp_path / "state"
    state.mkdir()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "OPENFOLLOW_VENV_PY": str(py),
        "OPENFOLLOW_NVME_MOUNT": str(tmp_path / "no-nvme"),
        "OPENFOLLOW_CONFIG": str(tmp_path / "config.toml"),
        "STUB_BACKEND": str(backend),
        "STUB_EXPORT": str(export),
        "STUB_STATE_DIR": str(state),
        "STUB_PURELIB": str(venv_bin.parent),  # writable -> no sudo
        "STUB_PIP_LOG": str(piplog),
        "STUB_PIP_FAIL": pip_fail,
        "STUB_FAIL_MSG": fail_msg,
    }
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(["bash", str(_script()), *args], text=True, capture_output=True, env=env)
    installs = [ln[len("PIPINSTALL ") :] for ln in piplog.read_text().splitlines() if ln.startswith("PIPINSTALL ")]
    return proc, installs


@_skip_if_root
def test_bare_run_installs_backend_only(tmp_path: Path) -> None:
    proc, installs = _run_main(tmp_path, ["--no-restart"], backend=0)
    assert proc.returncode == 0, proc.stderr
    # Exactly the backend, and never the torch/export stack the Pi doesn't need.
    assert len(installs) == 1
    assert "onnxruntime>=1.17" in installs[0] and "opencv-python>=4.8" in installs[0]
    assert "torch" not in installs[0] and "ultralytics" not in installs[0]


@_skip_if_root
def test_bare_run_noops_when_backend_present(tmp_path: Path) -> None:
    proc, installs = _run_main(tmp_path, ["--no-restart"], backend=1)
    assert proc.returncode == 0, proc.stderr
    assert installs == []  # early-exit, no pip


@_skip_if_root
def test_with_export_installs_backend_then_torch_then_ultralytics(tmp_path: Path) -> None:
    proc, installs = _run_main(tmp_path, ["--with-export", "--no-restart"], backend=0, export=0)
    assert proc.returncode == 0, proc.stderr
    assert len(installs) == 3
    assert "onnxruntime>=1.17" in installs[0] and "torch" not in installs[0]
    assert "download.pytorch.org/whl/cpu" in installs[1] and "torch" in installs[1]
    assert "ultralytics" in installs[2] and "onnxslim" in installs[2]


@_skip_if_root
def test_with_export_fallthrough_installs_export_when_backend_present(tmp_path: Path) -> None:
    proc, installs = _run_main(tmp_path, ["--with-export", "--no-restart"], backend=1, export=0)
    assert proc.returncode == 0, proc.stderr
    # Backend present -> skipped; export toolchain still installed.
    assert not any("onnxruntime>=1.17" in ln for ln in installs)
    assert any("torch" in ln for ln in installs) and any("ultralytics" in ln for ln in installs)


@_skip_if_root
def test_with_export_noops_when_both_present(tmp_path: Path) -> None:
    proc, installs = _run_main(tmp_path, ["--with-export", "--no-restart"], backend=1, export=1)
    assert proc.returncode == 0, proc.stderr
    assert installs == []


@_skip_if_root
def test_backend_failure_offline_dies_with_offline_message(tmp_path: Path) -> None:
    proc, _ = _run_main(
        tmp_path,
        ["--no-restart"],
        backend=0,
        pip_fail="onnxruntime",
        fail_msg="ERROR: ... Temporary failure in name resolution",
    )
    assert proc.returncode != 0
    out = (proc.stdout + proc.stderr).lower()
    assert "no working internet" in out and "uplink" in out


@_skip_if_root
def test_with_export_failure_exits_nonzero_but_installs_backend(tmp_path: Path) -> None:
    proc, installs = _run_main(tmp_path, ["--with-export", "--no-restart"], backend=0, export=0, pip_fail="torch")
    # Backend installed first (present in the log) but the requested export failed.
    assert any("onnxruntime>=1.17" in ln for ln in installs)
    assert proc.returncode != 0


@_skip_if_root
def test_with_export_offline_export_failure_surfaces_offline_message(tmp_path: Path) -> None:
    proc, _ = _run_main(
        tmp_path,
        ["--with-export", "--no-restart"],
        backend=1,  # skip backend so only the export path can fail
        export=0,
        pip_fail="torch",
        fail_msg="[Errno 101] Network is unreachable",
    )
    out = (proc.stdout + proc.stderr).lower()
    assert "no working internet" in out  # export path classifies offline too


@_skip_if_root
def test_bare_run_uses_backend_min_not_export_min(tmp_path: Path) -> None:
    # A huge EXPORT min must NOT gate a bare (backend-only) run.
    proc, installs = _run_main(
        tmp_path, ["--no-restart"], backend=0, extra_env={"OPENFOLLOW_EXPORT_MIN_MB": "999999999"}
    )
    assert proc.returncode == 0, proc.stderr
    assert any("onnxruntime>=1.17" in ln for ln in installs)


@_skip_if_root
def test_backend_min_gate_blocks_when_short(tmp_path: Path) -> None:
    proc, installs = _run_main(
        tmp_path, ["--no-restart"], backend=0, extra_env={"OPENFOLLOW_BACKEND_MIN_MB": "999999999"}
    )
    assert proc.returncode != 0
    assert "not enough space" in proc.stderr
    assert installs == []  # died at preflight, before any install


@_skip_if_root
def test_export_min_gate_blocks_with_export_when_short(tmp_path: Path) -> None:
    proc, installs = _run_main(
        tmp_path,
        ["--with-export", "--no-restart"],
        backend=0,
        extra_env={"OPENFOLLOW_EXPORT_MIN_MB": "999999999"},
    )
    assert proc.returncode != 0
    assert "not enough space" in proc.stderr
    assert installs == []


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


def _classify_offline(sample: str) -> subprocess.CompletedProcess[str]:
    # Pipe a captured-output sample through network_unreachable_in and report it.
    return _run(f"printf '%s' {shlex.quote(sample)} | network_unreachable_in && echo OFFLINE || echo ONLINE")


@pytest.mark.parametrize(
    "sample",
    [
        # The exact DNS failures an offline show Pi (no gateway / no DNS) hits.
        "after connection broken by 'NameResolutionError(...Temporary failure in name resolution...)'",
        "ERROR: Could not resolve host 'pypi.org'",
        "curl: (6) Could not resolve host: download.pytorch.org",
        "[Errno 101] Network is unreachable",
        "OSError: [Errno 113] No route to host",
    ],
)
def test_network_unreachable_in_detects_offline(sample: str) -> None:
    r = _classify_offline(sample)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "OFFLINE"


@pytest.mark.parametrize(
    "sample",
    [
        "Successfully installed onnxruntime-1.17.0 opencv-python-4.8.0",
        "ERROR: Could not find a version that satisfies the requirement torch",
        # A wrong clock is a separate cause, not an offline host.
        "certificate is not yet valid (_ssl.c:1006)",
        # A reached-but-slow mirror is not a name/route failure.
        "ERROR: connection timed out",
    ],
)
def test_network_unreachable_in_ignores_unrelated(sample: str) -> None:
    r = _classify_offline(sample)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "ONLINE"


def test_offline_message_is_actionable() -> None:
    r = _run("offline_message")
    assert r.returncode == 0, r.stderr
    out = r.stdout.lower()
    assert "internet" in out or "network" in out
    assert "uplink" in out or "mirror" in out
    # Points an offline Pi operator at the bundled backend / reload path.
    assert "reload" in out


def test_pip_failures_route_through_diagnostics() -> None:
    text = _script().read_text()
    # Every pip install path captures output and consults clock_skew_in on
    # failure, so a wrong clock surfaces as the fix, not a raw SSL traceback.
    assert 'die "$(clock_skew_message)"' in text
    # backend + torch (primary + fallback) + export tools = at least three checks.
    assert text.count("clock_skew_in <") >= 3
    # The backend (hard gate) also classifies an offline host, not just the clock.
    assert 'die "$(offline_message)"' in text
    assert text.count("network_unreachable_in <") >= 2


def test_pip_log_fallback_path_is_unique_per_run() -> None:
    # When mktemp fails, the fallback log path must be PID-suffixed so concurrent
    # runs can't collide on (or clobber a pre-existing) fixed /tmp file.
    text = _script().read_text()
    assert 'echo "/tmp/openfollow-install-detection-pip.$$.log"' in text
