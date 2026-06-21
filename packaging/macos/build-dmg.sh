#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
#
# Build the self-contained macOS .app and wrap it in a .dmg.
#
#   bash packaging/macos/build-dmg.sh [output-dir]   # default: <repo>/dist
#
# Prerequisites: a working `poetry run openfollow` env (the documented macOS dev
# setup) plus `brew install librsvg create-dmg`. The build host needs internet
# (torch / ultralytics + the default model weights). The .app is ad-hoc signed;
# it is NOT notarized, so first launch needs a Gatekeeper override (see
# docs/PACKAGING.md).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." && pwd)"
BUILD="$SCRIPT_DIR/_build"
DIST="${1:-$REPO/dist}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "The macOS DMG build must run on macOS." >&2
  exit 1
fi

cd "$REPO"

# PyInstaller's gi hooks resolve the GObject / GStreamer / Rsvg dylibs through
# macholib's dyld_find, which doesn't search Homebrew's prefix by default (the
# libs are linked at runtime by absolute install-name, so the app runs, but the
# analysis-time lookup fails). Add the brew prefix plus the standard fallbacks so
# libgio / libgobject / librsvg / libgstapp resolve during the freeze.
BREW_PREFIX="$(brew --prefix 2>/dev/null || echo /opt/homebrew)"
export DYLD_FALLBACK_LIBRARY_PATH="${BREW_PREFIX}/lib:/usr/local/lib:/usr/lib${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"

VERSION="$(poetry run python -c 'import openfollow; print(openfollow.__version__)')"
ARCH="$(uname -m)"
APP="$DIST/OpenFollow.app"
DMG="$DIST/OpenFollow-${VERSION}-${ARCH}.dmg"

echo "==> Building OpenFollow ${VERSION} (${ARCH})"

rm -rf "$REPO/build" "$DIST/OpenFollow" "$APP" "$BUILD"
mkdir -p "$BUILD/models" "$DIST"

echo "==> Rendering icon"
bash "$SCRIPT_DIR/make-icns.sh"

echo "==> Exporting quality-tier models (yolo26 n/s/m/l/x @ 640)"
# A Mac workstation has the headroom to run every tier, so ship all five at the
# larger inference size for accuracy. The detector auto-detects the export size.
for tier in n s m l x; do
    poetry run python "$REPO/scripts/export_onnx.py" "yolo26${tier}.pt" --imgsz 640 --output-dir "$BUILD/models"
done

# The frozen app's GObject stack needs the Homebrew GLib, but two wheels get in
# the way of bundling it:
#
#  1. opencv-python vendors its own (older) glib / gtk / gstreamer dylibs for
#     cv2's GUI backend. OpenFollow never uses cv2's GUI, so freeze against
#     opencv-python-headless (no GUI/glib-GTK), which also shrinks the bundle.
#  2. Both cv2 and pygame still vendor an older libglib (pulled in by ffmpeg /
#     SDL). PyInstaller dedups shared libs by basename, so a vendored copy wins
#     and the newer Homebrew libglib the GObject stack needs (g_string_copy etc.)
#     is never collected. Prune the vendored GLib stack from those wheels so
#     PyInstaller resolves libglib to Homebrew and relocates it + its deps.
#
# All of this mutates the dev venv, so back up + restore on exit.
# Wheels (cv2, pygame, Pillow, matplotlib) vendor their own older copies of the
# GLib + pango/cairo text stack. In the flattened bundle those shadow the newer
# Homebrew builds the GTK/GObject/pango stack needs (e.g. libglib's g_string_copy,
# libharfbuzz's _hb_coretext_font_create). All are forward ABI compatible, so
# prune the vendored copies and let PyInstaller relocate the Homebrew ones.
SITE="$(poetry run python -c 'import site; print(site.getsitepackages()[0])')"
PRUNE_PKGS=(cv2 pygame PIL matplotlib)
PRUNE_LIBS=(libglib-2.0.0.dylib libintl.8.dylib libharfbuzz.0.dylib libfreetype.6.dylib libfontconfig.1.dylib)
VENDOR_BAK="$(mktemp -d)"

restore_venv() {
  # Move every pruned dylib back, then reinstall opencv-python over the headless
  # build (which restores cv2 wholesale regardless of what was pruned from it).
  for pkg in "${PRUNE_PKGS[@]}"; do
    [[ -d "$VENDOR_BAK/$pkg" ]] && mv "$VENDOR_BAK/$pkg"/* "$SITE/$pkg/.dylibs/" 2>/dev/null || true
  done
  rm -rf "$VENDOR_BAK"
  poetry run pip uninstall -y opencv-python-headless >/dev/null 2>&1 || true
  poetry run pip install -q "opencv-python>=4.8" >/dev/null 2>&1 || true
}
trap restore_venv EXIT

echo "==> Swapping cv2 -> opencv-python-headless for the freeze"
poetry run pip uninstall -y opencv-python >/dev/null 2>&1 || true
poetry run pip install -q opencv-python-headless

echo "==> Pruning vendored GLib / pango stack from wheels"
for pkg in "${PRUNE_PKGS[@]}"; do
  dylibs="$SITE/$pkg/.dylibs"
  [[ -d "$dylibs" ]] || continue
  for lib in "${PRUNE_LIBS[@]}"; do
    if [[ -f "$dylibs/$lib" ]]; then
      mkdir -p "$VENDOR_BAK/$pkg"
      mv "$dylibs/$lib" "$VENDOR_BAK/$pkg/$lib"
      echo "  pruned $pkg/.dylibs/$lib"
    fi
  done
done

echo "==> Freezing .app with PyInstaller"
poetry run pyinstaller --noconfirm --clean \
  --distpath "$DIST" --workpath "$REPO/build" \
  "$SCRIPT_DIR/openfollow.spec"

[[ -d "$APP" ]] || {
  echo "PyInstaller did not produce $APP" >&2
  exit 1
}

echo "==> Ad-hoc signing"
codesign --force --deep --sign - "$APP"

echo "==> Self-checking bundle (scrubbed env)"
if ! env -i HOME="$HOME" OPENFOLLOW_SELFCHECK=1 "$APP/Contents/MacOS/OpenFollow" | grep -q '^OK$'; then
  echo "Bundle self-check failed - the frozen native stack is incomplete." >&2
  exit 1
fi

echo "==> Building DMG"
rm -f "$DMG"
if command -v create-dmg >/dev/null 2>&1; then
  create-dmg \
    --volname "OpenFollow ${VERSION}" \
    --window-size 640 360 \
    --icon "OpenFollow.app" 160 180 \
    --app-drop-link 480 180 \
    "$DMG" "$APP" || true
fi
if [[ ! -f "$DMG" ]]; then
  echo "==> create-dmg unavailable or failed; using hdiutil"
  STAGE="$(mktemp -d)"
  cp -R "$APP" "$STAGE/"
  ln -s /Applications "$STAGE/Applications"
  hdiutil create -volname "OpenFollow ${VERSION}" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
  rm -rf "$STAGE"
fi

echo "==> Done: $DMG"
