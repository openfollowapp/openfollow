#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Run OpenFollow on the HDMI display via Cage with a crash-restart watchdog.
# Usage: ./scripts/run-on-display.sh [--no-watchdog] [config.toml]
#   --no-watchdog  Run once without the restart loop (for development)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

NO_WATCHDOG=0
if [[ "${1:-}" == "--no-watchdog" ]]; then
    NO_WATCHDOG=1
    shift
fi

CONFIG_FILE="${1:-config.toml}"

# Ensure XDG_RUNTIME_DIR exists (required for Wayland)
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
if [ ! -d "$XDG_RUNTIME_DIR" ]; then
    echo "Creating XDG_RUNTIME_DIR at $XDG_RUNTIME_DIR"
    sudo mkdir -p "$XDG_RUNTIME_DIR"
    sudo chown "$(id -u):$(id -g)" "$XDG_RUNTIME_DIR"
    sudo chmod 700 "$XDG_RUNTIME_DIR"
fi

# wlroots (Cage compositor) configuration
export WLR_BACKENDS=drm
export WLR_RENDERER=gles2

cd "$REPO_ROOT"

if (( NO_WATCHDOG )); then
    echo "Starting OpenFollow (no watchdog)..."
    exec cage -- poetry run python -m openfollow.main "$CONFIG_FILE"
fi

# Restart loop – automatically restart on crash or exit
MAX_RESTARTS=10
RESTART_WINDOW=300  # 5 minutes
restart_times=()

while true; do
    current_time=$(date +%s)

    # Remove restart times older than RESTART_WINDOW
    recent_restarts=()
    for t in "${restart_times[@]}"; do
        if (( current_time - t < RESTART_WINDOW )); then
            recent_restarts+=("$t")
        fi
    done
    restart_times=("${recent_restarts[@]}")

    # Check circuit breaker
    if (( ${#restart_times[@]} >= MAX_RESTARTS )); then
        echo "Circuit breaker: too many restarts (${#restart_times[@]} in ${RESTART_WINDOW}s). Stopping."
        exit 1
    fi

    echo "Starting OpenFollow..."
    cage -- poetry run python -m openfollow.main "$CONFIG_FILE" || true

    restart_times+=("$(date +%s)")
    echo "App exited. Restarting in 3s... (${#restart_times[@]}/$MAX_RESTARTS recent restarts)"
    sleep 3
done
