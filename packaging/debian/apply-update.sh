#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Install a staged OpenFollow .deb update and make sure the service is running.
#
# Must run detached from openfollow.service (launched as root in a transient
# systemd-run unit, owned by PID 1): the package prerm stops openfollow.service,
# so a child of that service would be killed mid-upgrade and leave the package
# half-configured. Detached, the install survives the stop and completes.
#
# Detached means the web UI can't see the result directly: progress is published
# to a status file the web UI polls. It lives in the openfollow-owned state dir
# (not /run) so the openfollow-user app can clear it. Keep STATE_FILE in sync
# with _DETACHED_UPDATE_STATE_FILE in services.py.
#
# Single argument: the staged .deb under /tmp/openfollow-update-*.
set -u

SPEC="${1:?usage: apply-update.sh <staged-deb>}"
STATE_FILE=/var/lib/openfollow/update-state.json

# Defence in depth with the sudoers wildcard: only ever act on the staging
# prefix the upload route / downloader writes into.
case "$SPEC" in
    /tmp/openfollow-update-*) ;;
    *)
        echo "apply-update: refusing spec outside /tmp/openfollow-update-*: '$SPEC'" >&2
        exit 2
        ;;
esac

# Publish {state, message, error} as JSON for the web UI to poll. Values are
# sanitised (newlines collapsed to spaces; all other control chars, backslashes
# and double-quotes stripped/replaced – apt/dpkg output routinely contains tabs)
# so the file stays valid JSON without a real serialiser.
write_state() {
    _msg=$(printf '%s' "${2:-}" | tr '\n' ' ' | tr -d '\000-\037' | sed 's/\\/ /g; s/"/'"'"'/g')
    _err=$(printf '%s' "${3:-}" | tr '\n' ' ' | tr -d '\000-\037' | sed 's/\\/ /g; s/"/'"'"'/g')
    printf '{"state":"%s","message":"%s","error":"%s","ts":%s}\n' \
        "$1" "$_msg" "$_err" "$(date +%s)" >"$STATE_FILE" 2>/dev/null || true
}

# Remove the staged .deb and, when it sits inside the verifier's extraction dir
# (/tmp/openfollow-update-*.ofupdate.d/, which also holds SHA256SUMS[.sig]), that
# dir too. The case-guard means a bare-file spec never deletes its parent (/tmp).
cleanup_spec() {
    rm -rf "$SPEC" 2>/dev/null || true
    case "$SPEC" in
        /tmp/openfollow-update-*.ofupdate.d/*) rm -rf "$(dirname "$SPEC")" 2>/dev/null || true ;;
    esac
}

fail() {
    write_state failed "Update failed." "$1"
    cleanup_spec  # don't leak staging on failure
    exit 1
}

# prerm stops and disables both units and only postinst brings them back, so a
# failed install would leave the station down with no web UI to retry from. The
# failure is recorded first: the starting app must not read "running" and take
# itself for the update. Then whatever dpkg left half-done is finished.
fail_install() {
    write_state failed "Update failed." "$1"
    cleanup_spec
    dpkg --force-confdef --force-confold --configure -a >/dev/null 2>&1 || true
    systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl enable openfollow.service openfollow-splash.service >/dev/null 2>&1 || true
    systemctl start openfollow.service >/dev/null 2>&1 \
        || write_state failed "Update failed, and OpenFollow could not be started again." "$1"
    exit 1
}

export DEBIAN_FRONTEND=noninteractive

[ -f "$SPEC" ] || fail "Staged update not found."
write_state running "Installing update…"
# --allow-downgrades so an operator can roll back offline; --reinstall because
# apt otherwise skips the version already installed and still exits 0. A
# conffile the operator changed is kept (confold) rather than stopping the
# install at a prompt nothing here can answer; an unchanged one is updated.
if ! out=$(apt-get install -y --reinstall --allow-downgrades \
        -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold "$SPEC" 2>&1); then
    # Prefer the apt/dpkg error lines; fall back to the last few non-empty
    # lines when the failure emits no E:/dpkg: marker (disk space, dependency
    # summaries, …) so the operator still gets an actionable reason.
    detail=$(printf '%s' "$out" | grep -E '^(E:|dpkg:)' | head -3 | tr '\n' ' ')
    [ -n "$detail" ] || detail=$(printf '%s' "$out" | grep -v '^[[:space:]]*$' | tail -3 | tr '\n' ' ')
    fail_install "$detail"
fi

# Success: prerm stopped the unit and postinst started the new version, so
# `start` only covers a postinst start that failed. Never restart here: that
# stops the instance postinst just started while it is still starting.
write_state restarting "Update installed. Restarting…"
cleanup_spec
systemctl start openfollow.service \
    || { write_state failed "Start failed. Service may need manual attention." ""; exit 1; }
