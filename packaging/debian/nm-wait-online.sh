#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# NetworkManager-wait-online for a station: succeeds once NetworkManager's
# startup is complete (nm-online -s), or once every device it is still
# activating holds an IPv4 address: a lease at once, the link-local fallback
# once held for $grace seconds. Fails after $1 seconds (default 20) without.
#
# nm-online -s alone never succeeds on a LAN without DHCP: the infinite
# dhcp-timeout (10-openfollow-dhcp-fallback.conf) keeps that activation running
# beside its fallback address, so startup never completes. At least one device
# must be activating, so a boot that has not started a device yet (no carrier)
# waits for startup as before instead of racing the lease.
set -u
export LC_ALL=C
cap="${1:-20}"
# A lease arriving this soon after the 169.254 fallback still wins.
grace=3

# Each poll lasts about a second (nm-online's --timeout=1), so the cap and the
# grace count polls. $held lists "device:polls" for each activating device seen
# holding only its fallback on consecutive polls; a device missing from a poll
# starts over.
held=""

held_polls() {
    for entry in $held; do
        [ "${entry%:*}" = "$1" ] && echo "${entry##*:}" && return
    done
    echo -1
}

activating_devices_addressed() {
    found=1
    waiting=0
    now_held=""
    for dev in $(nmcli -t -f DEVICE,TYPE,STATE device 2>/dev/null |
        awk -F: '$2 != "loopback" && $3 ~ /^connecting/ { print $1 }'); do
        found=0
        case "$(ip -4 -o addr show dev "$dev" 2>/dev/null | awk '
            $3 != "inet" || $4 ~ /^127\./ { next }
            $4 !~ /^169\.254\./ { lease = 1 }
            { any = 1 }
            END { print lease ? "lease" : any ? "fallback" : "none" }')" in
        lease) ;;
        fallback)
            polls_held=$(($(held_polls "$dev") + 1))
            now_held="$now_held $dev:$polls_held"
            [ "$polls_held" -ge "$grace" ] || waiting=1
            ;;
        *) waiting=1 ;;
        esac
    done
    held="$now_held"
    [ "$waiting" -eq 0 ] && return "$found"
    return 1
}

polls=0
while [ "$polls" -lt "$cap" ]; do
    nm-online -s -q --timeout=1 && exit 0
    activating_devices_addressed && exit 0
    polls=$((polls + 1))
done
echo "a device NetworkManager is activating has no IPv4 address after ${cap} s" >&2
exit 1
