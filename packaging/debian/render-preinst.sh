#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Render the preinst: inline the settings-backup program into preinst.in.
# Usage: render-preinst.sh <preinst.in> <settings_backup.py> <out>
set -eu

TEMPLATE="$1"
PROGRAM="$2"
OUT="$3"

[ "$(grep -c '^@SETTINGS_BACKUP_PY@$' "$TEMPLATE")" = 1 ] \
  || { echo "render-preinst: $TEMPLATE needs exactly one @SETTINGS_BACKUP_PY@ line" >&2; exit 1; }
if grep -q '^OPENFOLLOW_SETTINGS_BACKUP$' "$PROGRAM"; then
  echo "render-preinst: $PROGRAM contains the heredoc terminator" >&2
  exit 1
fi

awk -v program="$PROGRAM" '
  $0 == "@SETTINGS_BACKUP_PY@" { while ((getline line < program) > 0) print line; next }
  { print }
' "$TEMPLATE" > "$OUT"
chmod 0755 "$OUT"
