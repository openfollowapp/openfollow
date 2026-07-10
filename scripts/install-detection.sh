#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Install OpenFollow person-detection support: the ONNX Runtime backend
# (onnxruntime + opencv-python) plus the ultralytics export toolchain, into the
# application venv. Documented at https://openfollow.app/docs/detection-install.html.
# Idempotent; safe to re-run.
#
# These are not bundled in the image: torch (pulled by ultralytics) is large and
# ultralytics is AGPL-3.0, so the operator fetches them on demand instead.
#
# Run as the normal user (e.g. ``openfollow``), NOT root – the script invokes
# ``sudo`` itself only to write into the packaged (root-owned) venv.
#
# Usage (run from a checkout, or the packaged copy that the .deb / image install
# ships at /usr/share/openfollow/install-detection.sh):
#   install-detection.sh                 install the detection stack
#   install-detection.sh --force         reinstall even if already present
#   install-detection.sh --no-restart    skip restarting the openfollow service
set -euo pipefail

log() { echo "[install-detection] $*"; }
warn() { echo "[install-detection] WARNING: $*" >&2; }
die() {
  echo "[install-detection] ERROR: $*" >&2
  exit 1
}

usage() {
  cat <<'USAGE'
Install OpenFollow person-detection support (onnxruntime + opencv-python +
ultralytics) into the app venv. Idempotent. Run as your normal user (not root);
the script uses sudo only to write into the packaged venv.

Usage:
  install-detection.sh                 install the detection stack
  install-detection.sh --force         reinstall even if already present
  install-detection.sh --no-restart    skip restarting the openfollow service
USAGE
}

# Tunables (overridable via env for tests / non-standard installs).
VENV_PY_DEFAULT="${OPENFOLLOW_VENV_PY:-/opt/openfollow/venv/bin/python}"
NVME_MOUNT="${OPENFOLLOW_NVME_MOUNT:-/mnt/nvme}"
MAIN_MIN_MB=4096    # hard gate: require 4 GiB free where the packages land
MODEL_WARN_MB=24576 # advisory: warn under 24 GiB where models land

# --- pure helpers (unit-tested by sourcing this file) ----------------------

# Available MiB on the filesystem holding $1 (POSIX df -Pk; portable Mac/Linux).
# Resolves to the nearest existing ancestor first, so a not-yet-created target
# (e.g. a fresh NVMe model dir) reports its filesystem's free space, not 0.
free_mb() {
  local path=$1 parent mb
  while [ -n "$path" ] && [ ! -e "$path" ]; do
    parent="$(dirname "$path")"
    if [ "$parent" = "$path" ]; then break; fi
    path="$parent"
  done
  mb="$(df -Pk "$path" 2>/dev/null | awk 'NR==2 {print int($4 / 1024)}')"
  echo "${mb:-0}"
}

# Hard gate: fail loudly (non-zero) when free space is below the minimum.
require_free_mb() { # label free_mb min_mb path
  local label=$1 free=$2 min=$3 path=$4
  if [ "$free" -lt "$min" ]; then
    die "not enough space on $label ($path): ${free} MiB free, need at least ${min} MiB. Free up space and re-run."
  fi
  log "$label OK: ${free} MiB free on $path (need ${min})."
}

# Advisory only: warn (never fail) when the model store is tight.
warn_low_model_storage() { # free_mb warn_mb path
  local free=$1 warn=$2 path=$3
  if [ "$free" -lt "$warn" ]; then
    warn "model storage at $path has only ${free} MiB free. Detection models need a lot of space; only small models will likely fit on this device. An NVMe gives room to grow."
  fi
}

# Echo the NVMe model directory when the drive is mounted, else nothing.
nvme_model_dir() { # mounted(0/1) nvme_root
  [ "$1" -eq 1 ] && echo "$2/openfollow/yolo" || true
}

# True when $1 is a real mountpoint.
is_mounted() { # path
  if command -v mountpoint >/dev/null 2>&1; then
    mountpoint -q "$1"
  else
    awk -v p="$1" '$2 == p {f = 1} END {exit f ? 0 : 1}' /proc/mounts 2>/dev/null
  fi
}

# The config.toml the running service uses: explicit override, else the
# packaged WorkingDirectory, else the checkout-relative default.
config_path() {
  if [ -n "${OPENFOLLOW_CONFIG:-}" ]; then
    echo "$OPENFOLLOW_CONFIG"
  elif [ -f /var/lib/openfollow/config.toml ]; then
    echo /var/lib/openfollow/config.toml
  else
    echo config.toml
  fi
}

# True when pip output shows a certificate that is not valid yet, i.e. the
# system clock is behind real time (no RTC + NTP not synced is the usual Pi
# cause) so TLS verification of PyPI / the torch index fails. Reads the
# captured output on stdin.
clock_skew_in() {
  grep -qiE 'not yet valid|is not valid yet|not live until' 2>/dev/null
}

clock_skew_message() {
  cat <<'MSG'
the system clock is wrong, so TLS certificates fail to verify
("certificate is not yet valid") and pip cannot download the detection
packages over HTTPS.

Fix the clock, then re-run this script:
  sudo timedatectl set-ntp true
  sudo systemctl restart systemd-timesyncd
  timedatectl status         # wait for: System clock synchronized: yes
If NTP cannot reach a server (UDP port 123 is often blocked), set it by hand:
  sudo date -u -s 'YYYY-MM-DD HH:MM:SS'    # real current UTC time (-u = interpret as UTC)
MSG
}

# --- main ------------------------------------------------------------------

main() {
  local FORCE=0 RESTART=1
  for arg in "$@"; do
    case "$arg" in
      --force | --reinstall) FORCE=1 ;;
      --no-restart) RESTART=0 ;;
      -h | --help)
        usage
        exit 0
        ;;
      -*) die "unknown option: $arg" ;;
      *) die "unexpected argument: $arg" ;;
    esac
  done

  # --- Preconditions -------------------------------------------------------
  [ "$(id -u)" -ne 0 ] || die "run as your normal user (e.g. openfollow), not root / sudo – the script calls sudo itself where needed."
  command -v sudo >/dev/null 2>&1 || die "sudo is required but not installed."

  # The app venv on a packaged install, else the active interpreter (checkout).
  local venv_py="$VENV_PY_DEFAULT"
  if [ ! -x "$venv_py" ]; then
    venv_py="$(command -v python3 || true)"
    [ -n "$venv_py" ] || die "no python interpreter found (looked for $VENV_PY_DEFAULT and python3)."
  fi
  log "Using interpreter: $venv_py"

  # --- Step 1: storage preflight (before any install side effect) ----------
  local venv_root
  venv_root="$(cd "$(dirname "$venv_py")/.." && pwd)"
  require_free_mb "main storage" "$(free_mb "$venv_root")" "$MAIN_MIN_MB" "$venv_root"

  local mounted=0
  is_mounted "$NVME_MOUNT" && mounted=1 || true
  local model_dir
  model_dir="$(nvme_model_dir "$mounted" "$NVME_MOUNT")"
  # No NVMe: the runtime auto-resolves models under the service working dir
  # (<workdir>/yolo); the warn below just reports that filesystem's free space.
  [ -n "$model_dir" ] || model_dir="$(dirname "$(config_path)")"
  warn_low_model_storage "$(free_mb "$model_dir")" "$MODEL_WARN_MB" "$model_dir"

  # NVMe present: keep the install's download/build transients off the small eMMC.
  local pip_env=""
  if [ "$mounted" -eq 1 ]; then
    local cache="$NVME_MOUNT/openfollow/cache"
    if mkdir -p "$cache/pip" "$cache/tmp" 2>/dev/null; then
      pip_env="PIP_CACHE_DIR=$cache/pip TMPDIR=$cache/tmp"
      log "Using NVMe for pip cache + temp: $cache"
    else
      warn "could not create $cache; pip will use the default cache/temp on main storage."
    fi
  fi

  # Already installed? Nothing to do.
  if [ "$FORCE" -eq 0 ] && "$venv_py" -c "import onnxruntime, cv2" >/dev/null 2>&1; then
    log "onnxruntime + opencv already present – detection backend is installed. Re-run with --force to reinstall."
    "$venv_py" -c "import ultralytics, onnx" >/dev/null 2>&1 ||
      warn "the model export tools (ultralytics + onnx) are not fully installed; the Download Model action stays unavailable. Re-run with --force to add them."
    exit 0
  fi

  # Writing into a root-owned (packaged) venv needs sudo; a user-owned checkout
  # venv does not.
  local sudo_pip=""
  local purelib
  purelib="$("$venv_py" -c "import sysconfig; print(sysconfig.get_path('purelib'))" 2>/dev/null || true)"
  if [ -n "$purelib" ] && [ ! -w "$purelib" ]; then
    sudo_pip="sudo"
    log "venv is not writable by $(id -un); using sudo for pip."
  fi

  # --- Install: prefer the explicit CPU-only torch wheel first. On x86_64 this
  #     drops the ~400 MiB of unused CUDA libs the default wheel bundles; on
  #     aarch64 the default wheel is already CPU-only, so it's a no-op win there.
  #     Fall back to the default wheel when no CPU build exists for this platform.
  # Capture each pip run's output (while still streaming it live via tee) so a
  # failure can be classified: a wrong clock surfaces a clear fix instead of a
  # raw SSL traceback.
  local pip_log
  pip_log="$(mktemp 2>/dev/null || echo "/tmp/openfollow-install-detection-pip.$$.log")"
  trap 'rm -f "$pip_log"' EXIT

  log "Installing CPU-only torch + torchvision (PyTorch CPU index)…"
  # shellcheck disable=SC2086
  if ! $sudo_pip env $pip_env "$venv_py" -m pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision 2>&1 | tee "$pip_log"; then
    if clock_skew_in <"$pip_log"; then die "$(clock_skew_message)"; fi
    warn "CPU-only torch install failed; falling back to the default wheel (on x86_64 this also pulls unused CUDA libs)."
    # shellcheck disable=SC2086
    if ! $sudo_pip env $pip_env "$venv_py" -m pip install torch torchvision 2>&1 | tee "$pip_log"; then
      if clock_skew_in <"$pip_log"; then die "$(clock_skew_message)"; fi
      die "pip could not install torch/torchvision – see the output above. Check the network connection and re-run."
    fi
  fi
  # onnx + onnxslim land in the venv here so the export never falls back to a
  # per-user pip install (the packaged venv is root-owned and a venv doesn't
  # import ~/.local), which is what left export failing with "No module named
  # 'onnx'" until now.
  log "Installing onnxruntime + opencv-python + the export tools (ultralytics + onnx + onnxslim)…"
  # shellcheck disable=SC2086
  if ! $sudo_pip env $pip_env "$venv_py" -m pip install "onnxruntime>=1.17" "opencv-python>=4.8" ultralytics onnx onnxslim 2>&1 | tee "$pip_log"; then
    if clock_skew_in <"$pip_log"; then die "$(clock_skew_message)"; fi
    die "pip could not install the detection backend – see the output above. Check the network connection and re-run."
  fi

  # --- Verify --------------------------------------------------------------
  # The detection backend is onnxruntime + opencv (the hard gate); the export
  # tools are optional, so a missing import warns, never fails.
  "$venv_py" -c "import onnxruntime, cv2" >/dev/null 2>&1 || die "detection backend still not importable after install."
  log "Verified: onnxruntime + opencv import cleanly."
  "$venv_py" -c "import ultralytics, onnx" >/dev/null 2>&1 ||
    warn "the export tools (ultralytics + onnx) did not import after install; model export (Download Model) will be unavailable."

  # --- Restart the service -------------------------------------------------
  if [ "$RESTART" -eq 1 ] && systemctl list-unit-files openfollow.service >/dev/null 2>&1; then
    log "Restarting the openfollow service…"
    sudo systemctl restart openfollow || warn "could not restart openfollow – restart it manually."
  fi

  log "Done. Detection works in the web UI once a model (.onnx) is present in the storage models folder."
}

# Run main only when executed, not when sourced (tests source the helpers).
if [ "${BASH_SOURCE[0]:-$0}" = "${0}" ]; then
  main "$@"
fi
