#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# NetworkManager-wait-online for a station: succeeds once NetworkManager's
# startup is complete (nm-online -s), or once every device it is still
# activating holds an IPv4 address, a lease or the link-local fallback. Fails
# after $1 seconds (default 20) with a device still addressless.
#
# nm-online -s alone never succeeds on a LAN without DHCP: the infinite
# dhcp-timeout (10-openfollow-dhcp-fallback.conf) keeps that activation running
# beside its fallback address, so startup never completes. At least one device
# must be activating, so a boot that has not started a device yet (no carrier)
# waits for startup as before instead of racing the lease.
set -u
export LC_ALL=C
cap="${1:-20}"

activating_devices_addressed() {
    found=1
    for dev in $(nmcli -t -f DEVICE,TYPE,STATE device 2>/dev/null |
        awk -F: '$2 != "loopback" && $3 ~ /^connecting/ { print $1 }'); do
        ip -4 -o addr show dev "$dev" 2>/dev/null | grep -v ' inet 127\.' | grep -q ' inet ' || return 1
        found=0
    done
    return "$found"
}

polls=0
while [ "$polls" -lt "$cap" ]; do
    nm-online -s -q --timeout=1 && exit 0
    activating_devices_addressed && exit 0
    polls=$((polls + 1))
done
echo "a device NetworkManager is activating has no IPv4 address after ${cap} s" >&2
exit 1
