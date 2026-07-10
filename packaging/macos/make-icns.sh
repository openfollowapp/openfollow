#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
#
# Render the app icon (.icns) from the bundled SVG. Generated at build time so
# no binary blob lives in the repo. Requires rsvg-convert (brew install librsvg)
# and iconutil (ships with macOS).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." && pwd)"

SVG="$REPO/openfollow/web/static/icon.svg"
OUT_DIR="$SCRIPT_DIR/_build"
ICONSET="$OUT_DIR/OpenFollow.iconset"
ICNS="$OUT_DIR/OpenFollow.icns"

command -v rsvg-convert >/dev/null 2>&1 || {
  echo "rsvg-convert not found. Install it: brew install librsvg" >&2
  exit 1
}
command -v iconutil >/dev/null 2>&1 || {
  echo "iconutil not found (this step must run on macOS)." >&2
  exit 1
}

rm -rf "$ICONSET"
mkdir -p "$ICONSET"

for size in 16 32 128 256 512; do
  rsvg-convert -w "$size" -h "$size" "$SVG" -o "$ICONSET/icon_${size}x${size}.png"
  retina=$((size * 2))
  rsvg-convert -w "$retina" -h "$retina" "$SVG" -o "$ICONSET/icon_${size}x${size}@2x.png"
done

iconutil -c icns "$ICONSET" -o "$ICNS"
echo "Wrote $ICNS"
