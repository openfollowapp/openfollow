#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann, Michel Honold, and Vinzenz Schultz
# Copyright (C) 2026 Paul Hermann and Michel Honold
#
# Run the CI gate (`make ci`) on a testing Pi over the LAN when one is
# reachable, falling back to running it locally otherwise.
#
# The Pi is the real deployment target (aarch64 / Python 3.13 / trixie), so the
# gate catches arch- and version-specific failures the dev Mac masks (missing
# cp313 wheels, mypy reexport rules, ...). See CLAUDE.md "Development".
#
# Mechanism: rsync the working tree onto the Pi's existing checkout, run
# `make ci` in its already-installed poetry env (no fresh install – a fresh
# trixie venv would recompile rtmidi/pycairo from source), then restore the Pi
# to its exact pre-run commit. Device-local ignored files (config.toml, models)
# are excluded from the sync, so they are neither overwritten nor deleted. An
# interrupted gate stops its own run on the Pi before that restore.
#
# Env overrides:
#   OPENFOLLOW_CI_HOSTS  space-separated candidate hosts, first reachable wins
#                        (default: "192.168.178.66 192.168.178.59")
#   OPENFOLLOW_CI_USER   ssh user on the Pi          (default: openfollow)
#   OPENFOLLOW_CI_DIR    repo path on the Pi         (default: /home/openfollow/openfollow)
#   OPENFOLLOW_CI_FORCE  =1 to overwrite a Pi with a dirty checkout
#   OPENFOLLOW_CI_LOCAL  =1 to skip the Pi entirely and run locally
#   OPENFOLLOW_CI_PERF   auto (default): time the Operator Screen drawing against
#                        the merge-base when the change touches the drawing paths;
#                        1 always, 0 never
#   OPENFOLLOW_CI_PERF_BASE  ref the merge-base is taken against (default: origin/main)
set -euo pipefail

HOSTS="${OPENFOLLOW_CI_HOSTS:-192.168.178.66 192.168.178.59}"
SSH_USER="${OPENFOLLOW_CI_USER:-openfollow}"
REMOTE_DIR="${OPENFOLLOW_CI_DIR:-/home/openfollow/openfollow}"
SSH_OPTS=(-o ConnectTimeout=5 -o BatchMode=yes)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log() { printf '\033[1;36m[ci-remote]\033[0m %s\n' "$*" >&2; }

# A change (uncommitted part included) to these paths is timed against the merge-base
# after a green `make ci`. The bench runs bare: make turns its exit 1 (slower) into 2.
PERF_AB="poetry run python scripts/hw_validation/hud_bench.py --compare"
HUD_PATHS='^(openfollow/(video/(overlay|hud_render)\.py|runtime/overlay_[a-z_]+\.py|scene/(solver|camera)\.py|zones/geometry\.py|window\.py)|tests/_hud_scenes\.py|scripts/hw_validation/hud_bench\.py)$'
perf_base=""
perf_dir=""

perf_wanted() {
  local mode="${OPENFOLLOW_CI_PERF:-auto}" ref="${OPENFOLLOW_CI_PERF_BASE:-origin/main}"
  [ "$mode" = "0" ] && return 1
  if ! perf_base="$(git -C "$REPO_ROOT" merge-base "$ref" HEAD 2>/dev/null)"; then
    log "No merge-base with ${ref}: skipping the Operator Screen drawing comparison."
    return 1
  fi
  [ "$mode" = "1" ] && return 0
  # grep reads all of its input: an early exit would fail the pipe under pipefail.
  { git -C "$REPO_ROOT" diff --name-only "$perf_base"; git -C "$REPO_ROOT" ls-files --others --exclude-standard; } \
    | grep -E "$HUD_PATHS" >/dev/null
}

# The merge-base's package, and its scenes when it has them: each side draws scenes its renderer reads.
perf_archive() {
  local paths=(openfollow)
  git -C "$REPO_ROOT" cat-file -e "${perf_base}:tests/_hud_scenes.py" 2>/dev/null && paths+=(tests/_hud_scenes.py)
  git -C "$REPO_ROOT" archive "$perf_base" "${paths[@]}"
}

perf_verdict() {
  case "$1" in
    0) return 0 ;;
    1) log "Operator Screen drawing is more than 15 % slower than ${perf_base:0:9} on a bench scene (table above)." ;;
    *) log "The Operator Screen drawing comparison could not run (exit $1)." ;;
  esac
  return "$1"
}

PERF=0
if perf_wanted; then
  PERF=1
fi

run_local() {
  log "Running 'make ci' locally on this machine."
  [ "$PERF" = 1 ] || exec make -C "$REPO_ROOT" ci
  make -C "$REPO_ROOT" ci
  perf_dir="$(mktemp -d)"
  trap 'rm -rf "$perf_dir"' EXIT
  perf_archive | tar -x -C "$perf_dir"
  log "Timing the Operator Screen drawing against ${perf_base:0:9}"
  local status=0
  (cd "$REPO_ROOT" && $PERF_AB "$perf_dir") || status=$?
  perf_verdict "$status" || exit "$status"
  exit 0
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

# A gate step runs as its own process group on the Pi, recorded so an interrupted gate
# (Ctrl-C, a dropped connection) stops exactly that run before restoring the checkout,
# and so the next gate refuses while it is still running. sshd starts each command as
# a session leader, so the remote shell's pid is the group id.
RUN_FILE=".git/ci-remote.pid"
# Sets pid to the recorded group; never 0 or 1, which kill reads as "every process".
READ_RUN="pid=\$(cat $RUN_FILE 2>/dev/null) && case \$pid in ''|*[!0-9]*|0*|1) false ;; esac"

remote_run() {
  remote "export PATH=\$HOME/.local/bin:\$PATH; cd '$REMOTE_DIR' && echo \$\$ > $RUN_FILE && $1"
}

# Ends the recorded run, TERM then KILL; fails only while it is still running.
stop_run() {
  remote "cd '$REMOTE_DIR' || exit 1
    $READ_RUN || { rm -f $RUN_FILE; exit 0; }
    for sig in TERM KILL; do
      kill -\$sig -\$pid 2>/dev/null
      n=0
      while [ \$n -lt 50 ]; do
        kill -0 -\$pid 2>/dev/null || { rm -f $RUN_FILE; exit 0; }
        sleep 0.2
        n=\$((n + 1))
      done
    done
    exit 1"
}

# Guard: a missing repo, or a dirty Pi checkout we'd silently clobber. Queried
# separately: packed into one string, an empty HEAD takes the dirty count's place.
orig_ref="$(remote "cd '$REMOTE_DIR' 2>/dev/null && test -d .git && git rev-parse --verify -q HEAD" || true)"
if [ -z "$orig_ref" ]; then
  log "ERROR: no git checkout with a commit at ${HOST}:${REMOTE_DIR} (.git must be a directory)."
  exit 2
fi
running="$(remote "cd '$REMOTE_DIR' && if $READ_RUN && kill -0 -\$pid 2>/dev/null; then echo \$pid; else rm -f $RUN_FILE; fi" || true)"
if [ -n "$running" ]; then
  log "ERROR: an earlier gate is still running on ${HOST}:${REMOTE_DIR} (process group ${running})."
  log "Wait for it to end, or stop it on the Pi."
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
  if ! stop_run; then
    log "ERROR: the gate on ${HOST} could not be stopped, so ${REMOTE_DIR} was not restored – check it by hand."
    return 1
  fi
  [ -z "$perf_dir" ] || remote "rm -rf '$perf_dir'" || log "Could not remove ${HOST}:${perf_dir}."
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

if [ "$PERF" = 1 ]; then
  perf_dir="$(remote mktemp -d)"
  log "Copying the merge-base's package and scenes (${perf_base:0:9}) -> ${HOST}:${perf_dir}"
  perf_archive | remote "tar -x -C '$perf_dir'"
fi

log "Running 'make ci' on ${HOST} (real aarch64 / py3.13 target)"
set +e
remote_run "make ci"
status=$?
perf_status=0
if [ "$status" -eq 0 ] && [ "$PERF" = 1 ]; then
  log "Timing the Operator Screen drawing against ${perf_base:0:9} on ${HOST}"
  remote_run "$PERF_AB '$perf_dir'"
  perf_status=$?
fi
set -e

# The next gate runs on this checkout, so leaving it broken fails this one.
if ! restore; then
  log "CI FAILED on ${HOST}: make ci exited ${status}, but the Pi was left in an unknown state."
  exit 3
fi
if [ "$status" -ne 0 ]; then
  log "CI FAILED on ${HOST} (exit ${status})."
  exit "$status"
fi
if ! perf_verdict "$perf_status"; then
  log "CI FAILED on ${HOST}."
  exit "$perf_status"
fi
log "CI PASSED on ${HOST}."
