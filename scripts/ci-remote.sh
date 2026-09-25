#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
#
# Run the CI gate (`make ci`) on a testing Pi over the LAN when one is
# reachable, falling back to running it locally otherwise.
#
# The Pi is the real deployment target (aarch64 / Python 3.13 / trixie), so the
# gate catches arch- and version-specific failures the dev Mac masks (missing
# cp313 wheels, mypy reexport rules, ...). See CLAUDE.md "Local CI gate".
#
# Mechanism: rsync the working tree onto the Pi's existing checkout, run
# `make ci` in its already-installed poetry env (no fresh install – a fresh
# trixie venv would recompile rtmidi/pycairo from source), then restore the Pi
# to its exact pre-run commit. Device-local ignored files (config.toml, models)
# are excluded from the sync, so they are neither overwritten nor deleted.
#
# Env overrides:
#   OPENFOLLOW_CI_HOSTS  space-separated candidate hosts, first reachable wins
#                        (default: "192.168.178.66 192.168.178.59")
#   OPENFOLLOW_CI_USER   ssh user on the Pi          (default: openfollow)
#   OPENFOLLOW_CI_DIR    repo path on the Pi         (default: /home/openfollow/openfollow)
#   OPENFOLLOW_CI_FORCE  =1 to overwrite a Pi with a dirty checkout
#   OPENFOLLOW_CI_LOCAL  =1 to skip the Pi entirely and run locally
set -euo pipefail

HOSTS="${OPENFOLLOW_CI_HOSTS:-192.168.178.66 192.168.178.59}"
SSH_USER="${OPENFOLLOW_CI_USER:-openfollow}"
REMOTE_DIR="${OPENFOLLOW_CI_DIR:-/home/openfollow/openfollow}"
SSH_OPTS=(-o ConnectTimeout=5 -o BatchMode=yes)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log() { printf '\033[1;36m[ci-remote]\033[0m %s\n' "$*" >&2; }

run_local() {
  log "Running 'make ci' locally on this machine."
  exec make -C "$REPO_ROOT" ci
}

[ "${OPENFOLLOW_CI_LOCAL:-0}" = "1" ] && run_local

pick_host() {
  for h in $HOSTS; do
    if ssh "${SSH_OPTS[@]}" "${SSH_USER}@${h}" 'exit 0' 2>/dev/null; then
      printf '%s' "$h"
      return 0
    fi
  done
  return 1
}

if ! HOST="$(pick_host)"; then
  log "No testing Pi reachable ($HOSTS) – falling back to local."
  run_local
fi
log "Testing Pi reachable at ${HOST}."

remote() { ssh "${SSH_OPTS[@]}" "${SSH_USER}@${HOST}" "$@"; }

# Guard: a missing repo, or a dirty Pi checkout we'd silently clobber. Queried
# separately: packed into one string, an empty HEAD takes the dirty count's place.
orig_ref="$(remote "cd '$REMOTE_DIR' 2>/dev/null && test -d .git && git rev-parse --verify -q HEAD" || true)"
if [ -z "$orig_ref" ]; then
  log "ERROR: no git checkout with a commit at ${HOST}:${REMOTE_DIR} (.git must be a directory)."
  exit 2
fi
if ! porcelain="$(remote "cd '$REMOTE_DIR' && git status --porcelain")"; then
  log "ERROR: cannot read git status at ${HOST}:${REMOTE_DIR}"
  exit 2
fi
if [ -n "$porcelain" ] && [ "${OPENFOLLOW_CI_FORCE:-0}" != "1" ]; then
  log "ERROR: ${HOST}:${REMOTE_DIR} has $(grep -c '' <<<"$porcelain") uncommitted change(s)."
  log "Refusing to overwrite. Commit/stash on the Pi, or set OPENFOLLOW_CI_FORCE=1."
  exit 2
fi

# Mirror the working tree to the Pi. --delete makes the Pi match our tree
# exactly (so a file our branch deleted is gone during the run); excluded paths
# are protected from both copy and delete, keeping the Pi's device-local state
# (config.toml, detection models) and all build/cache junk untouched. `.git`
# has no trailing slash: in a git worktree it is a file, not a directory.
log "Syncing working tree -> ${HOST}:${REMOTE_DIR}"
rsync -az --delete \
  --exclude='.git' \
  --exclude='.venv/' \
  --exclude='config.toml' \
  --exclude='models/' \
  --exclude='__pycache__/' \
  --exclude='*.py[cod]' \
  --exclude='*.egg-info/' \
  --exclude='.mypy_cache/' \
  --exclude='.pytest_cache/' \
  --exclude='.ruff_cache/' \
  --exclude='.hypothesis/' \
  --exclude='htmlcov/' \
  --exclude='.coverage' \
  --exclude='.coverage.*' \
  --exclude='coverage.xml' \
  --exclude='dist/' \
  --exclude='build/' \
  --exclude='mutants/' \
  --exclude='.mutmut-cache' \
  --exclude='.DS_Store' \
  --exclude='packaging/image/*.deb' \
  --exclude='packaging/image/*.img*' \
  -e "ssh ${SSH_OPTS[*]}" \
  "$REPO_ROOT/" "${SSH_USER}@${HOST}:${REMOTE_DIR}/"

restore() {
  trap - EXIT
  log "Restoring ${HOST}:${REMOTE_DIR} to ${orig_ref:0:9}"
  # `git clean -e models -e config.toml`: drop the files our sync added, but
  # keep the device-local state the rsync also excluded. config.toml is
  # gitignored (clean skips it anyway) but models/ is NOT, so without -e a
  # clean would delete a Pi's detection weights – the one path that lives in
  # the repo dir yet must survive.
  if ! remote "cd '$REMOTE_DIR' && git reset --hard '$orig_ref' >/dev/null 2>&1 && git clean -fd -e models -e config.toml >/dev/null 2>&1"; then
    log "ERROR: ${HOST}:${REMOTE_DIR} could not be restored to ${orig_ref:0:9} – check it by hand."
    return 1
  fi
}
# Covers an interrupted run; the normal path restores before the verdict.
trap 'restore || exit 3' EXIT

log "Running 'make ci' on ${HOST} (real aarch64 / py3.13 target)"
set +e
remote "export PATH=\$HOME/.local/bin:\$PATH; cd '$REMOTE_DIR' && make ci"
status=$?
set -e

# The next gate runs on this checkout, so leaving it broken fails this one.
if ! restore; then
  log "CI FAILED on ${HOST}: make ci exited ${status}, but the Pi was left in an unknown state."
  exit 3
fi
if [ "$status" -eq 0 ]; then
  log "CI PASSED on ${HOST}."
else
  log "CI FAILED on ${HOST} (exit ${status})."
fi
exit "$status"
