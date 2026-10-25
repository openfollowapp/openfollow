#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# NetworkManager-wait-online for a station: succeeds once NetworkManager's
# startup is complete (nm-online -s), or once every device it is still
# activating holds an IPv4 address: a lease at once, the link-local fallback
# once held for 3 s. Checks every $step seconds and gives up after $1 seconds
# (default 20), both counted on the time since boot.
#
# nm-online -s alone never succeeds on a LAN without DHCP: the infinite
# dhcp-timeout (10-openfollow-dhcp-fallback.conf) keeps that activation running
# beside its fallback address, so startup never completes. At least one device
# must be activating, so a boot that has not started a device yet (no carrier)
# waits for startup as before instead of racing the lease.
set -u
export LC_ALL=C
cap_ms=$((${1:-20} * 1000))
# A lease arriving this soon after the 169.254 fallback still wins.
grace_ms=3000
step=0.25
# Seconds since boot; the tests point this at a clock of their own.
uptime="${OPENFOLLOW_UPTIME:-/proc/uptime}"

now_ms() {
    awk '{ printf "%d", $1 * 1000 }' "$uptime"
}

# $held lists "device:ms" for each activating device seen holding only its
# fallback on consecutive polls, with the time it was first seen so; a device
# missing from a poll starts over.
held=""

held_since() {
    for entry in $held; do
        [ "${entry%:*}" = "$1" ] && echo "${entry##*:}" && return
    done
    echo "$now"
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
            since=$(held_since "$dev")
            now_held="$now_held $dev:$since"
            [ $((now - since)) -ge "$grace_ms" ] || waiting=1
            ;;
        *) waiting=1 ;;
        esac
    done
    held="$now_held"
    [ "$waiting" -eq 0 ] && return "$found"
    return 1
}

start=$(now_ms)
now=$start
# A clock that does not move still ends the wait, at twice the polls the cap allows.
polls=0
max_polls=$((cap_ms / 125))
while [ $((now - start)) -lt "$cap_ms" ] && [ "$polls" -lt "$max_polls" ]; do
    nm-online -s -q --timeout=0 && exit 0
    activating_devices_addressed && exit 0
    sleep "$step"
    polls=$((polls + 1))
    now=$(now_ms)
done
echo "a device NetworkManager is activating has no settled IPv4 address after $((cap_ms / 1000)) s" >&2
exit 1
