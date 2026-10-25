#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Stops the boot splash so DRM is free for Cage, once it has been up $1 seconds
# (default 1.5) since its unit started. On a normal boot the network takes
# longer than that, so it stops at once; a splash that is not running (a
# restart of openfollow.service, no display) is left alone without waiting.
set -u
min_ms=$(awk -v s="${1:-1.5}" 'BEGIN { printf "%d", s * 1000 }')
unit=openfollow-splash.service
# Seconds since boot, the clock systemd's monotonic timestamps count on; the
# tests point this at a clock of their own.
uptime="${OPENFOLLOW_UPTIME:-/proc/uptime}"

[ "$(systemctl show -P ActiveState "$unit" 2>/dev/null)" = active ] || exit 0
started_us=$(systemctl show -P ActiveEnterTimestampMonotonic "$unit" 2>/dev/null)
now_ms=$(awk '{ printf "%d", $1 * 1000 }' "$uptime")
case "$started_us" in
'' | *[!0-9]*) left_ms=0 ;;
*) left_ms=$((min_ms - (now_ms - started_us / 1000))) ;;
esac
[ "$left_ms" -gt "$min_ms" ] && left_ms=$min_ms
[ "$left_ms" -gt 0 ] && sleep "$(awk -v ms="$left_ms" 'BEGIN { printf "%.3f", ms / 1000 }')"
systemctl stop "$unit"
