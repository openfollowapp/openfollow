#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Install NDI® support: the NDI® SDK runtime (libndi) plus the GStreamer
# ``ndisrc`` plugin built from source. Automates the manual procedure at
# https://openfollow.app/docs/ndi-install.html – everything except the SDK
# download itself, which NDI gates behind a browser + EULA and so cannot be
# scripted. Idempotent; safe to re-run.
#
# Run as the normal user (e.g. ``openfollow``), NOT root – the Rust toolchain
# and plugin build belong in the operator's home; the script invokes ``sudo``
# itself for the privileged apt / copy / ldconfig steps.
#
# Usage (run from a checkout, or the packaged copy that the .deb / image install
# ships at /usr/share/openfollow/install-ndi.sh):
#   install-ndi.sh [PATH_TO_NDI_SDK.tar.gz]
#   install-ndi.sh --force        # rebuild even if ndisrc already works
#   install-ndi.sh --no-restart   # skip restarting the openfollow service
set -euo pipefail

log() { echo "[install-ndi] $*"; }
die() {
  echo "[install-ndi] ERROR: $*" >&2
  exit 1
}

# === clock / index diagnostics (pure; sourced by tests) ====================
# True when apt output shows a future-dated signature ("Not live until …"),
# i.e. the system clock is behind real time – the usual Pi cause being no RTC
# plus NTP not yet synced. Reads the captured output on stdin.
clock_skew_in() {
  grep -qiE 'not live until|not yet valid|is not valid yet' 2>/dev/null
}

# True when apt could not resolve dependencies. On Raspberry Pi OS this is
# almost always a stale / inconsistent index (a failed apt-get update), not a
# genuine conflict. Reads the captured output on stdin.
broken_index_in() {
  grep -qiE 'held broken packages|unmet dependencies|unable to correct problems' 2>/dev/null
}

clock_skew_message() {
  cat <<'MSG'
the system clock is wrong, so APT rejects the repository signatures as
"Not live until <future date>" and cannot refresh the package index. The
dependency errors that follow are a side effect of the stale index, not a real
package conflict.

Fix the clock, then re-run this script:
  sudo timedatectl set-ntp true
  sudo systemctl restart systemd-timesyncd
  timedatectl status         # wait for: System clock synchronized: yes
If NTP cannot reach a server (UDP port 123 is often blocked), set it by hand:
  sudo date -u -s 'YYYY-MM-DD HH:MM:SS'    # real current UTC time (-u = interpret as UTC)
Then refresh the index and re-run:
  sudo apt-get update && sudo apt-get full-upgrade -y
MSG
}

broken_index_message() {
  cat <<'MSG'
APT could not install the build prerequisites: the package index is
inconsistent. On Raspberry Pi OS this almost always means the index is stale
(apt-get update did not refresh it, frequently because the system clock was
wrong). Refresh and reconcile, then re-run this script:
  sudo apt-get update
  sudo apt-get full-upgrade -y
If apt-get update reports signatures "Not live until <future date>", fix the
system clock first (timedatectl / sudo date -u -s).
MSG
}
# === end diagnostics =======================================================

FORCE=0
RESTART_SERVICE=1
SDK_TARBALL=""
for arg in "$@"; do
  case "$arg" in
    --force | --rebuild) FORCE=1 ;;
    --no-restart) RESTART_SERVICE=0 ;;
    -h | --help)
      cat <<'USAGE'
Install NDI® support (SDK runtime + GStreamer ndisrc plugin). Idempotent.
Run as your normal user (not root); the script uses sudo where needed.

Usage:
  install-ndi.sh [PATH_TO_NDI_SDK.tar.gz]   download the SDK first; auto-detected in $HOME if omitted
  install-ndi.sh --force                    rebuild even if ndisrc already works
  install-ndi.sh --no-restart               skip restarting the openfollow service
USAGE
      exit 0
      ;;
    -*) die "unknown option: $arg" ;;
    *) SDK_TARBALL="$arg" ;;
  esac
done

# --- Preconditions ---------------------------------------------------------
[ "$(id -u)" -ne 0 ] || die "run as your normal user (e.g. openfollow), not root / sudo – the script calls sudo itself where needed."
command -v sudo >/dev/null 2>&1 || die "sudo is required but not installed."
ARCH="$(uname -m)"

# Already working? Nothing to do (unless forced).
if [ "$FORCE" -eq 0 ] && command -v gst-inspect-1.0 >/dev/null 2>&1 && gst-inspect-1.0 ndisrc >/dev/null 2>&1; then
  log "ndisrc is already available – NDI support is installed. Re-run with --force to rebuild."
  exit 0
fi

# --- 1. Build + runtime prerequisites (apt) --------------------------------
# The minimal Pi OS Lite image omits several of these; install-system-deps.sh
# covers the runtime set but not the GStreamer -dev headers the plugin needs.
log "Installing build prerequisites (apt)…"
apt_log="$(mktemp 2>/dev/null || echo "/tmp/openfollow-install-ndi-apt.$$.log")"
trap 'rm -f "$apt_log"' EXIT
if ! sudo env DEBIAN_FRONTEND=noninteractive apt-get update 2>&1 | tee "$apt_log"; then
  # A failed update is fatal only when it's a wrong-clock signature failure –
  # pressing on with the stale index then produces a cryptic dependency
  # conflict. A plain unreachable-mirror failure still falls through to the
  # cached index (works on a fully-provisioned offline Pi).
  if clock_skew_in <"$apt_log"; then
    die "$(clock_skew_message)"
  fi
  log "WARN: apt-get update failed; proceeding with cached index"
fi
if ! sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y \
  curl git build-essential pkg-config \
  meson ninja-build gstreamer1.0-tools \
  libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev libglib2.0-dev libssl-dev \
  2>&1 | tee "$apt_log"; then
  if clock_skew_in <"$apt_log"; then
    die "$(clock_skew_message)"
  fi
  if broken_index_in <"$apt_log"; then
    die "$(broken_index_message)"
  fi
  die "apt-get could not install the NDI build prerequisites – see the output above."
fi

# --- 2. NDI® SDK runtime (libndi.so.*) -------------------------------------
if ls /usr/local/lib/libndi.so.* >/dev/null 2>&1; then
  log "NDI SDK runtime already present: $(ls /usr/local/lib/libndi.so.* | tr '\n' ' ')"
  # A prior/manual install may have left only the versioned libndi.so.N. The app
  # resolves an unversioned libndi.so (video/inputs/ndi.py), so ensure that dev
  # link exists on the already-present path too, not only on a fresh install.
  if [ ! -e /usr/local/lib/libndi.so ]; then
    latest="$(ls -1 /usr/local/lib/libndi.so.* 2>/dev/null | sort -V | tail -1 || true)"
    if [ -n "$latest" ]; then
      sudo ln -sf "$(basename "$latest")" /usr/local/lib/libndi.so
      sudo ldconfig
    fi
  fi
else
  # Reuse an already-extracted SDK if the operator ran the installer earlier,
  # otherwise unpack the tarball and run NDI's installer interactively so the
  # operator – not this script – accepts the licence.
  ndi_lib="$(find "$HOME" -name 'libndi.so*' -path "*${ARCH}*" 2>/dev/null | sort | head -1 || true)"
  if [ -z "$ndi_lib" ]; then
    if [ -z "$SDK_TARBALL" ]; then
      # Newest matching tarball in the home folder, if any.
      SDK_TARBALL="$(ls -1t "$HOME"/Install_NDI_SDK_*_Linux.tar.gz 2>/dev/null | head -1 || true)"
    fi
    [ -n "$SDK_TARBALL" ] || die "NDI SDK not found. Download the Linux NDI® SDK from https://ndi.video/for-developers/ndi-sdk/ , copy the Install_NDI_SDK_*_Linux.tar.gz into this Pi's home folder, then re-run (or pass its path as an argument)."
    [ -f "$SDK_TARBALL" ] || die "SDK tarball not found: $SDK_TARBALL"
    log "Unpacking $SDK_TARBALL…"
    tar -xf "$SDK_TARBALL" -C "$HOME"
    installer="$(ls -1t "$HOME"/Install_NDI_SDK_*_Linux.sh 2>/dev/null | head -1 || true)"
    [ -n "$installer" ] || die "NDI installer script not found after unpacking the SDK."
    log "Running NDI's installer – accept the licence when prompted."
    (cd "$HOME" && sh "$installer")
    ndi_lib="$(find "$HOME" -name 'libndi.so*' -path "*${ARCH}*" 2>/dev/null | sort | head -1 || true)"
  fi
  [ -n "$ndi_lib" ] || die "could not find a ${ARCH} libndi.so in the SDK."
  # Install the real (non-symlink) shared object, preferring a Raspberry-Pi
  # build. The SDK's lib/ dir holds the real libndi.so.N.N.N plus its SONAME/dev
  # symlinks; the bin/ dir holds only a cross-directory SONAME symlink, which
  # `cp -P` would land in /usr/local/lib as a dangling link. -type f selects the
  # real file and so always resolves to the lib/ copy.
  ndi_real="$(find "$HOME" -path "*${ARCH}*rpi*" -name 'libndi.so.*' -type f 2>/dev/null | sort -V | tail -1)"
  [ -n "$ndi_real" ] || ndi_real="$(find "$HOME" -path "*${ARCH}*" -name 'libndi.so.*' -type f 2>/dev/null | sort -V | tail -1)"
  [ -n "$ndi_real" ] || die "could not find a real ${ARCH} libndi.so.* in the SDK."
  log "Installing NDI runtime: $ndi_real"
  sudo install -m 0644 "$ndi_real" /usr/local/lib/
  sudo ldconfig  # creates the SONAME symlink (libndi.so.N) from the ELF DT_SONAME
  # Unversioned dev link so tools and the app's ctypes discovery resolve libndi
  # (ldconfig makes only the SONAME link, not the bare libndi.so).
  sudo ln -sf "$(basename "$ndi_real")" /usr/local/lib/libndi.so
fi

# --- 3. Rust toolchain + cargo-c -------------------------------------------
[ -f "$HOME/.cargo/env" ] && . "$HOME/.cargo/env"
if ! command -v rustc >/dev/null 2>&1; then
  log "Installing the Rust toolchain (rustup)…"
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
  . "$HOME/.cargo/env"
fi
if [ ! -x "$HOME/.cargo/bin/cargo-cbuild" ]; then
  log "Installing cargo-c (compiles from source – this can take 10+ minutes; the scrolling 'Compiling …' output is normal)…"
  cargo install cargo-c
fi

# --- 4. Build + install the GStreamer plugin -------------------------------
plugin_dir="$(pkg-config --variable=pluginsdir gstreamer-1.0 2>/dev/null || true)"
# Fallback: derive the Debian multiarch triplet from the compiler, not uname -m
# (e.g. armv7l → arm-linux-gnueabihf, which uname does not give).
[ -n "$plugin_dir" ] || plugin_dir="/usr/lib/$(gcc -dumpmachine)/gstreamer-1.0"
[ -d "$plugin_dir" ] || die "GStreamer plugin directory not found: $plugin_dir"

# Match the gst-plugins-rs stable branch to the installed GStreamer so the build
# tracks the distro rather than a moving main (branch 0.14 ↔ GStreamer 1.26,
# 0.15 ↔ 1.28, …). Override with GST_PLUGINS_RS_REF; falls back to main when the
# branch is missing or its build fails, so the result is never worse than main.
repo_url="https://gitlab.freedesktop.org/gstreamer/gst-plugins-rs.git"
ref="${GST_PLUGINS_RS_REF:-}"
if [ -z "$ref" ]; then
  gst_minor="$(pkg-config --modversion gstreamer-1.0 2>/dev/null | cut -d. -f2)"
  case "$gst_minor" in
    '' | *[!0-9]*) ref="main" ;;
    *) ref="0.$((gst_minor / 2 + 1))" ;;
  esac
  git ls-remote --exit-code --heads "$repo_url" "$ref" >/dev/null 2>&1 || ref="main"
fi

# Build under a cache dir, not a bare ~/gst-plugins-rs that rm -rf could clobber
# if the operator happens to have their own checkout there.
build_dir="${XDG_CACHE_HOME:-$HOME/.cache}/openfollow/gst-plugins-rs"
build_plugin() {
  rm -rf "$build_dir"
  mkdir -p "$(dirname "$build_dir")"
  git clone --depth 1 --branch "$1" "$repo_url" "$build_dir"
  (cd "$build_dir" && cargo cbuild -p gst-plugin-ndi --release)
}
log "Building gst-plugin-ndi from gst-plugins-rs@$ref (clean clone; takes several minutes)…"
if ! build_plugin "$ref" && [ "$ref" != "main" ]; then
  log "WARN: build from branch $ref failed – retrying from main"
  build_plugin main
fi

triple="$(rustc -vV | sed -n 's/^host: //p')"
so="$build_dir/target/$triple/release/libgstndi.so"
if [ ! -f "$so" ]; then
  so="$(find "$build_dir/target" -name libgstndi.so -path '*/release/*' ! -path '*/deps/*' 2>/dev/null | head -1 || true)"
fi
[ -n "$so" ] && [ -f "$so" ] || die "build did not produce libgstndi.so."
log "Installing plugin → $plugin_dir/libgstndi.so"
sudo install -m 0644 "$so" "$plugin_dir/libgstndi.so"
sudo ldconfig

# --- 5. Verify -------------------------------------------------------------
# ndisrc loads even when libndi is missing (the Rust plugin dlopens libndi
# lazily), so a bare gst-inspect pass can hide a broken runtime. Confirm the
# linker actually resolves libndi first.
ldconfig_bin="$(command -v ldconfig || true)"
if [ -z "$ldconfig_bin" ]; then
  for d in /usr/sbin /sbin; do
    if [ -x "$d/ldconfig" ]; then ldconfig_bin="$d/ldconfig"; break; fi
  done
fi
if [ -n "$ldconfig_bin" ] && ! "$ldconfig_bin" -p 2>/dev/null | grep -q 'libndi\.so'; then
  die "libndi is not resolvable by the linker after install – the NDI runtime did not install correctly. See https://openfollow.app/docs/ndi-install.html#verify"
fi
# Drop the cached plugin registry so a freshly added plugin is rescanned.
rm -f "$HOME/.cache/gstreamer-1.0/registry."*.bin
if gst-inspect-1.0 ndisrc >/dev/null 2>&1; then
  log "Verified: libndi + ndisrc are available."
else
  die "ndisrc still not found after install – see https://openfollow.app/docs/ndi-install.html#verify"
fi

# --- 6. Restart the service ------------------------------------------------
if [ "$RESTART_SERVICE" -eq 1 ] && systemctl list-unit-files openfollow.service >/dev/null 2>&1; then
  log "Restarting the openfollow service…"
  sudo systemctl restart openfollow || log "WARN: could not restart openfollow – restart it manually."
fi

log "Done. NDI® sources now appear as a video input in the web UI."
