---
name: pre-push-ci
description: >-
  Use before pushing, committing, or opening a PR on OpenFollow, or when asked
  "is this ready", "run CI", "run make ci", "pre-push check". Encodes the
  project's pre-push gate and the rules around it: `make ci-remote` runs the gate
  on the testing Pi (falling back to the Mac), the full gate must pass, format
  fixes go through make format (not by hand), no -k / --no-verify / red-branch
  pushes, the offline runtime contract, branch hygiene, and the docs gate.
---

# Pre-push gate

`make ci-remote` is the authoritative gate. It runs `make ci`
(lint + typecheck + security + unit/integration tests + build – a stricter
superset of `.github/workflows/ci.yml`, which runs everything **except**
`typecheck`/mypy) **on a reachable testing Pi** – the real deployment target
(aarch64 / Python 3.13 / trixie), which catches wheel and typecheck gaps the
dev Mac masks. When no Pi is reachable it falls back to
running `make ci` locally. **It must pass before every `git push`.** No
exceptions for "trivial" changes.

## Run it

```bash
make ci-remote   # Pi if reachable, else local
```

It rsyncs the working tree onto the Pi, runs `make ci` in the Pi's poetry env,
then restores the Pi to its pre-run commit. Device-local files (`config.toml`,
detection `models/`) are excluded from the sync. Overrides in
`scripts/ci-remote.sh`: `OPENFOLLOW_CI_HOSTS` (default
`192.168.178.66 192.168.178.59`), `OPENFOLLOW_CI_USER`, `OPENFOLLOW_CI_DIR`,
`OPENFOLLOW_CI_FORCE=1` (overwrite a dirty Pi), `OPENFOLLOW_CI_LOCAL=1` (force
local). The Pi path needs passwordless SSH; without it the probe fails and the
gate runs locally instead. `make ci` remains the underlying job – what runs on
the Pi, or locally as the fallback.

## Interpreting failures

- **Formatting failure (`ruff format --check`)** → fix with `make format`. Never
  hand-edit code to satisfy the formatter.
- **Lint / type / test failure** → fix the root cause. Do NOT:
  - narrow the run with `pytest -k` and call it green,
  - bypass the hook with `--no-verify`,
  - push a red branch "to fix on the next commit".
- A test that's flaky once is flaky forever – fix the flake or delete it, don't
  re-run until it passes.

## Offline runtime contract (re-check on any web/asset change)

OpenFollow runs on isolated show LANs with no WAN. The app – including the web UI
– must work end-to-end offline:

- No CDN-loaded JS/CSS/fonts. Bundle assets under `openfollow/web/static/` and
  reference via the `/assets/<filename:path>` route.
- No outbound HTTP from server-side code at runtime. The gated `git pull` update
  flow is the only sanctioned exception.
- No silent "online fallback". Ask: *does this still work after I unplug the WAN
  cable?* If no, it's broken.

## Branch + docs hygiene

- If you're on `main` and about to commit feature work, branch first (unless the
  user explicitly directed otherwise).
- **Docs gate:** if the change touched a control, config field, route, keybind,
  or documented behaviour, update the matching docs in the same change –
  `openfollow/web/help/<section>.md`, the relevant `docs/` file, and/or the
  `CLAUDE.md` tables. A behaviour change with stale docs is incomplete.

**Done when:** `make ci-remote` exits 0 (on the Pi when reachable, else locally)
and the docs reflect the change.
