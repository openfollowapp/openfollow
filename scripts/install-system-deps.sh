#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Install OpenFollow system (apt) dependencies. Idempotent; safe to re-run.
# Single source of truth for the package list, invoked by Ansible provisioning
# and the in-app web updater. Requires root (run via sudo or the playbook).
set -euo pipefail

# Silence interactive debconf prompts (e.g. service-restart questions).
export DEBIAN_FRONTEND=noninteractive

APT_PACKAGES=(
  git
  curl
  libglfw3
  libglfw3-dev
  python3-dev
  python3-venv
  # Source-build toolchain. Several native deps ship no cp313/aarch64 wheel and
  # compile from source during ``poetry install`` on the Pi (python-rtmidi,
  # pycairo, pygobject, netifaces). Their meson/setuptools builds need a C/C++
  # compiler and pkg-config, which a minimal Pi OS Lite image may not preinstall.
  build-essential
  pkg-config
  python3-gi
  python3-gi-cairo
  gir1.2-gstreamer-1.0
  gir1.2-gst-plugins-base-1.0
  gir1.2-gtk-3.0
  gir1.2-rsvg-2.0
  libgstreamer1.0-0
  gstreamer1.0-tools
  gstreamer1.0-plugins-base
  gstreamer1.0-plugins-good
  gstreamer1.0-plugins-bad
  gstreamer1.0-libav
  gstreamer1.0-libcamera
  gstreamer1.0-gtk3
  cage
  seatd
  kanshi
  librsvg2-bin
  libcairo2-dev
  libgirepository-2.0-dev
  # Cursor theme – without it Cage has no pixmap to render when the embedded
  # WebKit overlay asks for a cursor via ``wl_pointer.set_cursor``.
  adwaita-icon-theme
  # HID backend for the 3D Mouse (3Dconnexion 6DOF) input. pyspacemouse loads
  # this at runtime via easyhid-ng; the udev rule (99-openfollow-3dmouse.rules)
  # grants the service user access to the device's /dev/hidraw node.
  libhidapi-hidraw0
)

echo "[install-system-deps] Refreshing apt index..."
# Soft-fail: a transient network blip on apt-get update shouldn't abort an
# in-app upgrade; the cached index is usually current enough for the install
# step, which fails loudly if it isn't.
if ! apt-get update; then
  echo "[install-system-deps] WARN: apt-get update failed; proceeding with cached index"
fi

echo "[install-system-deps] Installing base packages..."
apt-get install -y "${APT_PACKAGES[@]}"

# ALSA development headers for the python-rtmidi source build (MIDI is a base
# dep). No prebuilt wheel exists for aarch64 + Python 3.13 (trixie), so Poetry
# compiles it; its meson build resolves the ALSA MIDI backend via
# ``pkg-config alsa`` (alsa.pc), shipped in ``libasound2-dev`` – the runtime lib
# alone is not enough. The ``-dev`` name is stable across releases (unlike
# runtime ``libasound2`` -> ``libasound2t64``) and pulls the matching runtime.
# Soft-fall back to the runtime-only package so hosts with a prebuilt wheel still
# proceed; a miss there is benign as ``gstreamer1.0-plugins-base`` pulls ALSA.
echo "[install-system-deps] Installing ALSA dev headers for python-rtmidi build..."
if apt-get install -y libasound2-dev; then
  echo "[install-system-deps] Installed libasound2-dev"
elif apt-get install -y libasound2t64; then
  echo "[install-system-deps] WARN: libasound2-dev unavailable; installed libasound2t64 runtime only (source build of python-rtmidi will fail if no wheel)"
elif apt-get install -y libasound2; then
  echo "[install-system-deps] WARN: libasound2-dev unavailable; installed libasound2 runtime only"
else
  echo "[install-system-deps] WARN: no ALSA package available; relying on transitive ALSA runtime"
fi

# WebKit2 GObject introspection: 4.1 preferred, 4.0 fallback. Soft-fail:
# webkit_browser.py guards the import, so a host without either package boots
# cleanly with the on-screen browser overlay unavailable.
echo "[install-system-deps] Installing WebKit2 introspection typelib..."
if apt-get install -y gir1.2-webkit2-4.1; then
  echo "[install-system-deps] Installed gir1.2-webkit2-4.1"
elif apt-get install -y gir1.2-webkit2-4.0; then
  echo "[install-system-deps] Installed gir1.2-webkit2-4.0 (4.1 unavailable)"
else
  echo "[install-system-deps] WARN: neither gir1.2-webkit2-4.1 nor -4.0 installed; browser overlay disabled"
fi

# Reclaim disk so repeated installs don't pile up on small SD cards.
# ``autoremove`` drops orphaned deps; ``clean`` empties /var/cache/apt/archives.
# Both are re-derivable from the mirror and soft-failed so a space-reclaim hiccup
# can't abort an otherwise-successful install or update.
echo "[install-system-deps] Reclaiming disk (apt autoremove + clean)..."
if ! apt-get autoremove -y; then
  echo "[install-system-deps] WARN: apt-get autoremove failed; continuing"
fi
if ! apt-get clean; then
  echo "[install-system-deps] WARN: apt-get clean failed; continuing"
fi

echo "[install-system-deps] Done."
