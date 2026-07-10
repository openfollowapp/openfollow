#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Install OpenFollow person-detection support into the application venv.
# Documented at https://openfollow.app/docs/detection-install.html. Idempotent.
#
# The inference backend (onnxruntime + opencv-python) is all a Pi needs to run
# detection against a pre-exported .onnx model; on the packaged Pi image it is
# already bundled in the venv, so a bare run there is a no-op. This installs it
# for source / dev checkouts.
#
# The model-export toolchain (torch + ultralytics + onnx) is a separate,
# workstation-only add-on installed with --with-export. torch is large and
# ultralytics is AGPL-3.0, so neither is bundled in the image nor needed at show
# time.
#
# Run as the normal user (e.g. ``openfollow``), NOT root – the script invokes
# ``sudo`` itself only to write into the packaged (root-owned) venv.
#
# Usage (run from a checkout, or the packaged copy that the .deb / image install
# ships at /usr/share/openfollow/install-detection.sh):
#   install-detection.sh                 install the detection backend
#   install-detection.sh --with-export   also install the model-export toolchain
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
Install OpenFollow person-detection support into the app venv. The inference
backend (onnxruntime + opencv-python) is all the Pi needs and is already bundled
on the packaged image; this installs it for source / dev checkouts. Idempotent.
Run as your normal user (not root); the script uses sudo only to write into the
packaged venv.

Usage:
  install-detection.sh                 install the detection backend
  install-detection.sh --with-export   also install the model-export toolchain
                                       (torch + ultralytics; workstation only)
  install-detection.sh --force         reinstall even if already present
  install-detection.sh --no-restart    skip restarting the openfollow service
USAGE
}

# Tunables (overridable via env for tests / non-standard installs).
VENV_PY_DEFAULT="${OPENFOLLOW_VENV_PY:-/opt/openfollow/venv/bin/python}"
NVME_MOUNT="${OPENFOLLOW_NVME_MOUNT:-/mnt/nvme}"
BACKEND_MIN_MB="${OPENFOLLOW_BACKEND_MIN_MB:-512}"   # hard gate: backend-only install (onnxruntime+opencv)
EXPORT_MIN_MB="${OPENFOLLOW_EXPORT_MIN_MB:-4096}"    # hard gate: --with-export adds the multi-GiB torch stack
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

# True when pip output shows no DNS / no network route (offline show-LAN: static
# IP, no gateway / no DNS). Narrow on purpose: a reachable-but-refused / timed-out
# mirror falls through to the generic error. Reads output on stdin.
network_unreachable_in() {
  grep -qiE 'temporary failure in name resolution|could not resolve host|name or service not known|network is unreachable|no route to host' 2>/dev/null
}

offline_message() {
  cat <<'MSG'
this host has no working internet connection (DNS resolution or the network
route failed), so pip cannot download the detection packages.

On the packaged Raspberry Pi image the detection backend already ships in the
app venv, so nothing needs downloading – just reload the web page.

For a source install, or to add the export tools with --with-export, run this on
a network with an uplink (or point pip at a local package mirror), then re-run.
MSG
}

# --- main ------------------------------------------------------------------

main() {
  local FORCE=0 RESTART=1 WITH_EXPORT=0
  for arg in "$@"; do
    case "$arg" in
      --force | --reinstall) FORCE=1 ;;
      --no-restart) RESTART=0 ;;
      --with-export | --export) WITH_EXPORT=1 ;;
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

  # --- Already installed? --------------------------------------------------
  # Backend bundled in the packaged venv -> bare run is a no-op. Checked before
  # the preflight so a satisfied backend never trips the space gate.
  if [ "$FORCE" -eq 0 ] && "$venv_py" -c "import onnxruntime, cv2" >/dev/null 2>&1; then
    if [ "$WITH_EXPORT" -eq 0 ]; then
      log "onnxruntime + opencv already present – detection backend is installed. Re-run with --force to reinstall, or --with-export to add the model-export toolchain."
      exit 0
    fi
    if "$venv_py" -c "import ultralytics, onnx" >/dev/null 2>&1; then
      log "detection backend + export toolchain already present. Re-run with --force to reinstall."
      exit 0
    fi
    log "detection backend present; installing the requested model-export toolchain…"
  fi

  # --- Storage preflight (before any install side effect) ------------------
  # The backend (onnxruntime + opencv) is small; the export toolchain (torch +
  # ultralytics) is multi-GiB, so require the large headroom only with --with-export.
  local venv_root
  venv_root="$(cd "$(dirname "$venv_py")/.." && pwd)"
  local main_min_mb=$BACKEND_MIN_MB
  [ "$WITH_EXPORT" -eq 1 ] && main_min_mb=$EXPORT_MIN_MB
  require_free_mb "main storage" "$(free_mb "$venv_root")" "$main_min_mb" "$venv_root"

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

  # Writing into a root-owned (packaged) venv needs sudo; a user-owned checkout
  # venv does not.
  local sudo_pip=""
  local purelib
  purelib="$("$venv_py" -c "import sysconfig; print(sysconfig.get_path('purelib'))" 2>/dev/null || true)"
  if [ -n "$purelib" ] && [ ! -w "$purelib" ]; then
    sudo_pip="sudo"
    log "venv is not writable by $(id -un); using sudo for pip."
  fi

  # Capture each pip run's output (streamed live via tee) so a failure can be
  # classified. Not 'local': the EXIT trap runs after main returns, so pip_log
  # must stay in scope (else set -u aborts cleanup on a successful run).
  pip_log="$(mktemp 2>/dev/null || echo "/tmp/openfollow-install-detection-pip.$$.log")"
  trap 'rm -f "$pip_log"' EXIT

  # --- Step 1: the inference backend (onnxruntime + opencv) – the hard gate.
  #     This is all a Pi needs to run detection against a pre-exported .onnx; on
  #     the packaged image it is already bundled, so this step is skipped there.
  if [ "$FORCE" -eq 1 ] || ! "$venv_py" -c "import onnxruntime, cv2" >/dev/null 2>&1; then
    log "Installing the detection backend (onnxruntime + opencv-python)…"
    # shellcheck disable=SC2086
    if ! $sudo_pip env $pip_env "$venv_py" -m pip install "onnxruntime>=1.17" "opencv-python>=4.8" 2>&1 | tee "$pip_log"; then
      if clock_skew_in <"$pip_log"; then die "$(clock_skew_message)"; fi
      if network_unreachable_in <"$pip_log"; then die "$(offline_message)"; fi
      die "pip could not install the detection backend – see the output above. Check the network connection and re-run."
    fi
  else
    log "Detection backend (onnxruntime + opencv) already present."
  fi

  # --- Step 2 (opt-in): model-export toolchain, workstation only. Large + AGPL,
  #     so never on a show Pi. Failure doesn't abort (backend is what runs), but
  #     flips export_ok so a scripted --with-export caller sees a non-zero exit.
  local export_ok=1
  if [ "$WITH_EXPORT" -eq 1 ]; then
    # Prefer the explicit CPU-only torch wheel: on x86_64 this drops ~400 MiB of
    # unused CUDA libs the default wheel bundles; on aarch64 the default wheel is
    # already CPU-only. Fall back to the default wheel when no CPU build exists.
    # CPU-only wheel first: drops ~400 MiB of CUDA libs on x86_64; a no-op on
    # aarch64. Fall back to the default wheel when no CPU build exists.
    log "Installing the model-export toolchain: CPU-only torch + torchvision (PyTorch CPU index)…"
    # shellcheck disable=SC2086
    if ! $sudo_pip env $pip_env "$venv_py" -m pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision 2>&1 | tee "$pip_log"; then
      if clock_skew_in <"$pip_log"; then warn "$(clock_skew_message)"; fi
      warn "CPU-only torch install failed; falling back to the default wheel."
      # shellcheck disable=SC2086
      if ! $sudo_pip env $pip_env "$venv_py" -m pip install torch torchvision 2>&1 | tee "$pip_log"; then
        if clock_skew_in <"$pip_log"; then warn "$(clock_skew_message)"; fi
        if network_unreachable_in <"$pip_log"; then warn "$(offline_message)"; fi
        warn "could not install torch/torchvision; model export (Download Model) stays unavailable."
        export_ok=0
      fi
    fi
    # onnx + onnxslim go in the same venv so export doesn't fall back to a
    # per-user pip install (a venv doesn't import ~/.local).
    log "Installing the export tools (ultralytics + onnx + onnxslim)…"
    # shellcheck disable=SC2086
    if ! $sudo_pip env $pip_env "$venv_py" -m pip install ultralytics onnx onnxslim 2>&1 | tee "$pip_log"; then
      if clock_skew_in <"$pip_log"; then warn "$(clock_skew_message)"; fi
      if network_unreachable_in <"$pip_log"; then warn "$(offline_message)"; fi
      warn "could not install the export tools (ultralytics/onnx); model export (Download Model) stays unavailable."
      export_ok=0
    fi
  fi

  # --- Verify --------------------------------------------------------------
  # Backend is the hard gate; export tools are optional (warn, don't die).
  "$venv_py" -c "import onnxruntime, cv2" >/dev/null 2>&1 || die "detection backend still not importable after install."
  log "Verified: onnxruntime + opencv import cleanly."
  if [ "$WITH_EXPORT" -eq 1 ]; then
    "$venv_py" -c "import ultralytics, onnx" >/dev/null 2>&1 || {
      warn "the export tools (ultralytics + onnx) did not import after install; model export (Download Model) will be unavailable."
      export_ok=0
    }
  fi

  # --- Restart the service -------------------------------------------------
  if [ "$RESTART" -eq 1 ] && systemctl list-unit-files openfollow.service >/dev/null 2>&1; then
    log "Restarting the openfollow service…"
    sudo systemctl restart openfollow || warn "could not restart openfollow – restart it manually."
  fi

  # Backend is installed + restarted; a requested-but-failed export exits non-zero.
  if [ "$WITH_EXPORT" -eq 1 ] && [ "$export_ok" -eq 0 ]; then
    die "backend installed, but the model-export toolchain did not fully install (see warnings above). Fix the issue and re-run with --with-export."
  fi

  log "Done. Detection works in the web UI once a model (.onnx) is present in the storage models folder."
}

# Run main only when executed, not when sourced (tests source the helpers).
if [ "${BASH_SOURCE[0]:-$0}" = "${0}" ]; then
  main "$@"
fi
