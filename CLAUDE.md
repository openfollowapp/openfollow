# OpenFollow – agent guide

OpenFollow is a Raspberry Pi (or macOS) followspot station. It receives a video
feed (NDI, SRT, RTSP, RTP, Pi Camera, USB, or the built-in Media Gallery), draws
a Cairo HUD over it, lets operators steer markers by keyboard, mouse, gamepad,
3D mouse, OSC or person detection, and sends marker positions to stage systems
over PSN, OTP, RTTrPM and OSC. A Bottle web UI configures it from any browser on
the show LAN; on a Pi it runs as a systemd service.

Layout: [`docs/PROJECT_STRUCTURE.md`](docs/PROJECT_STRUCTURE.md). Before changing
a subsystem, read its guide (table at the end).

## Working rules

1. **Plan first** for anything with 3+ steps or architectural impact: a plan with
   checkable items, checked in before implementation.
2. **Verify before done** – prove the change works.
3. **Minimal impact** – touch only what the change needs.
4. **Root causes, no temporary fixes.** Senior-developer standards.
5. **Capture the lesson** after a correction.

## Code comments

- Short, precise, technical, only where required. One line is the target, two
  the ceiling outside a genuinely subtle invariant. Match the file's density.
- Comment the non-obvious part only. A comment restating the line below it is
  noise; one comment above several near-identical blocks is enough.
- Reasoning (why, alternatives weighed, what review raised) goes in the commit
  message / PR description, not the source.
- **Never reference an issue, PR or bug number** in code, comments, docstrings
  or user-facing strings – not even as a leading tag.
- A load-bearing contract (what callers must not do, an invariant a refactor
  could break) earns more room than an inline comment; a narrative does not.
- No "legacy" / "removed" / "no longer" / "used to" breadcrumbs. Write code,
  comments, tests and docs as if the current design was always the only one.

## Writing

**Never use an em-dash (U+2014)** anywhere: code, comments, UI strings,
templates, help text, Markdown. Use an en-dash (U+2013), a hyphen, or rewrite.

## Public artefacts carry no support-case details

Issues, PRs, commits, comments, tests and docs are public and permanent. Never
carry an operator's IP addresses, hostnames, station names, stream URLs, ports,
config dumps or log excerpts into them, nor wording that identifies the report.
Write the **failure class** ("a station on one subnet, its camera on another,
no route between them"). Example addresses come from RFC 5737 / RFC 3849
ranges. Diagnostics bundles stay in the support channel.

## Commit messages, PR and issue titles

[Conventional Commits](https://www.conventionalcommits.org):
`<type>(<scope>): <summary>`.

- **Types:** `feat`, `fix`, `test`, `docs`, `refactor`, `perf`, `build`, `ci`, `chore`.
- **Scope:** exactly one from the table, the area a reader would look in first.
  A new scope is added to the table in the change that first uses it.
- **Summary:** lower-case start, no trailing period, title under ~72 chars, no
  issue/PR numbers. PR titles and commit summaries are imperative and name what
  the change does (`fix(hud): draw caution status rows in the caution colors`);
  the squash commit reuses the PR title. An issue title may describe the problem.
- **Labels:** `fix` ⇒ `bug`, `feat` ⇒ `enhancement`, other types neither.
  Platform / area labels (`Linux`, `Mac`, `Network`) are independent.
- New issues start from [`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE/).

| Scope | Covers |
|---|---|
| `web` | Web UI pages, routes, help drawer (not the wizard) |
| `wizard` | Setup wizard (`/wizard`) |
| `hud` | Operator Screen: window, overlay, on-screen menus |
| `video` | Video receiver and input plugins |
| `gallery` | Media Gallery source |
| `detection` | Person detection, tracking, masks, models |
| `input` | Keyboard, mouse, MIDI, controller slots, shared input plumbing |
| `gamepad` | Gamepads |
| `mouse3d` | 3D mice |
| `psn` / `otp` / `rttrpm` | That protocol's sender / receiver |
| `osc` | OSC input and output |
| `zones` | Trigger zones |
| `markers` | Marker catalog, marker control and visibility |
| `scene` | Camera model and calibration solver |
| `config` | Config model, validation, import / export / peer transfer |
| `network` | Interfaces, IP configuration, discovery, name resolution |
| `diagnostics` | Diagnostics bundle, statistics, export to a drive |
| `update` | Updater, What's new, online sync |
| `packaging` | `.deb`, image, macOS bundle, privilege grants, settings backup |
| `release` | Version bumps and release notes |
| `ci` | Workflows, the `make ci` gate, lint / security tooling, hardware validation |
| `deps` / `deps-dev` | Dependency bumps |
| `repo` | README, CLAUDE.md, docs conventions, `.github` templates |

## Test coverage (REQUIRED for every code change)

Untested code is treated as broken. A change without tests is incomplete.

1. New code ships with tests in the same PR: functions, branches, config fields,
   routes, input handlers, render paths, plugin hooks.
2. A bug fix starts with a failing regression test that stays in the suite.
3. Anything that parses or validates input covers valid, out-of-range,
   wrong-type (`None`, `"abc"`, `True`) and enum-mismatch input – one
   `@pytest.mark.parametrize` per failure mode.
4. Seams get integration tests in their suite: web form → config → renderer
   (`tests/test_web_helpers.py`), config → hot-reload
   (`tests/test_configuration.py`), input → overlay
   (`tests/test_services_marker_visuals.py`).
5. Assert observable behaviour through the public boundary
   (`GridConfig(thickness="0")`, `apply_section_data(...)`), never private
   helpers or call order. No tautological tests.
6. Fast and hermetic: no real network, no real GTK/Cairo window, no wall clock,
   no host IP. A flaky test is fixed or deleted.
7. An untestable line gets `# pragma: no cover - <reason the line cannot run in
   the sandbox>` **and** a row in the Pragma audit table of
   [`docs/COVERAGE.md`](docs/COVERAGE.md). A pragma without a row blocks review.
8. No `@pytest.mark.skip` to unblock a merge.

## Offline runtime (REQUIRED for every change)

Stations run on show LANs with no internet uplink. The whole app, web UI
included, must work with the WAN cable unplugged.

- **No CDN assets.** JS / CSS / fonts / models are bundled under
  [`openfollow/web/static/`](openfollow/web/static/) and served via
  `/assets/<filename:path>`. `test_index_page_uses_locally_bundled_htmx` pins
  this for htmx.
- **No outbound HTTP at runtime**, except these four, none on the data path:
  1. the signed-`.deb` updater (`runtime/deb_update.py`), GitHub releases of
     `update_github_repo` only, operator-clicked ([update](docs/architecture/update.md));
  2. detection **model export** (`/section/detection/export`), needs the
     `export` extra and an uplink, a workstation task ([detection](docs/architecture/detection.md));
  3. the **online-sync worker** (`runtime/online_sync.py`): NTP clock set plus
     the update-check, on startup and IP change, config-gated, fail-silent,
     never blocking, never prompting for a password ([update](docs/architecture/update.md#online-sync));
  4. the diagnostics bundle's **video-source reachability probe** (section A5):
     one bounded TCP connect plus DNS lookup to the operator's own video host,
     click-gated, ~2.5 s cap, never scheduled ([diagnostics](docs/architecture/diagnostics.md));
- Resolving a name the operator configured as a destination is a query to the
  show LAN's own resolver, not an exception ([network](docs/architecture/network.md#name-resolution)).
- Telemetry, analytics, licence checks, remote flags: rejected.
- Never a silent fallback when a resource is unreachable: a page whose Save
  no-ops offline is the worst failure mode.

LAN traffic (multicast beacon, PSN, OSC, RTSP/SRT/RTP/NDI) is fine. CI runs
offline.

## Config changes (REQUIRED)

Use the **`add-config-field`** skill. A field is done only when it survives a
hand-edited `config.toml` and a crafted `POST /section/<name>`:

1. Normalise in `__post_init__` with the `_coerce_*` helpers.
2. `apply_section_data` (`web/routes.py`) re-runs `__post_init__` for the section.
3. Regression tests in `tests/test_configuration.py` (wrong type, range, enum,
   list entries).
4. A `FIELD_RULES` entry in `web/validation.py` matching the server-side bounds
   and sanitisation, with the standard validate markup on the input
   (`tests/test_template_validation_consistency.py` enforces it).

Device-local fields (interface pins, paths, the PIN) are stripped from export and
broadcast; device-identity fields survive import and Restore defaults. Both sets,
hot reload and live-apply are in the [config guide](docs/architecture/config.md).

## Web UI copy and status (REQUIRED)

- **No inline help in forms.** A `<label>` (and a `placeholder` where it helps)
  is the whole inline copy. Explanations go in the section's help drawer,
  `openfollow/web/help/<section>.md`. A new `field-note` or explanatory `title`
  is a review blocker. Update the help `.md` when a control changes.
- **Status language:** every state surface (web and HUD) uses the four levels,
  tokens and components in [`docs/STATUS_LANGUAGE.md`](docs/STATUS_LANGUAGE.md).
  No literal colours, alphas, radii or icons; extend the document if nothing
  fits. `tests/test_status_language.py` enforces it.

## Architecture

```
OpenFollowApp (app.py)
├── AppRuntimeServices (services.py)          – init + per-frame update orchestrator
├── AppConfig (configuration.py)              – all settings, TOML I/O, hot-reload
├── GstNativeSinkReceiver (video/receiver.py) – generic pipeline orchestrator
│   └── video/inputs/                         – one plugin per protocol, auto-discovered
├── CairoOverlayRenderer (video/overlay.py)   – HUD on a Gtk.DrawingArea above gtksink
├── PersonDetector (video/detection.py)       – optional YOLO detection (bg thread)
├── PsnServer / OtpServer / RttrpmServer      – outputs, each on its own send thread
├── PsnReceiver (psn/receiver.py)             – PSN from other stations
├── InputManager (input/input_manager.py)     – keyboard, gamepad, mouse, 3D mouse, OSC
└── ConfigWebServer (web/server.py)           – Bottle web UI + discovery beacon
```

**Frame loop:** `_animate()` on a ~60 Hz `GLib.timeout_add`, integrating real
elapsed time: `_process_input(dt)` → `update_video()` → `apply_detection_pin()`
→ `update_zone_triggers()` → `update_marker_visuals()`.

**Three clocks, none of them the display.** Marker state, input, detection,
zones and outputs run off the frame timeout; slow web-driven housekeeping off a
100 ms timeout; the display vsync tick (`start_hud_tick`) only redraws the HUD
and polls the macOS pointer. A unit with no display gets no vsync tick, so
nothing with app state may live there. Do not move per-frame state onto
`start_hud_tick` and do not give it a callback parameter.

**One coordinate frame.** `marker.pos` is PSN-absolute world coordinates
everywhere (X stage left, Y upstage, Z up): receiver, senders, mouse unproject,
detection pin, zones, renderer, solver. `grid.{x,y}_offset` positions the grid
rectangle only and is **never** added to or subtracted from a marker, detection
point or zone vertex. `tests/test_coordinate_system_invariants.py` guards it.

**Hot reload.** Saved config is applied live by `apply_runtime_config_changes`
unless it can't be: enabling detection, changing `detection.inference_size`,
any detection edit with no running detector, `web_port` and `web_bind_iface`
restart the app. A video source or plugin field change live-swaps the input.

## Load-bearing invariants

Each of these has broken before or would break silently. The guides hold the why.

**Runtime and live-apply**
- `_last_frame_completed` is stamped as the **last line** of `animate`; the
  Frame clock chip compares its **age** with `playback.stale_after_s`, never a literal.
- Live-apply paths go through `_apply_with_fallback`, so one failed service
  doesn't stop the rest from applying.
- A service's `start()` mints a **fresh stop event per generation**; never
  `clear()` a shared one.
- A worker-thread service takes config through `reload_config` (one pending
  slot, latest wins); the GTK thread never mutates worker state.

**Config**
- `_DEVICE_IDENTITY_FIELDS` is the one device-identity list, shared by import and
  Restore defaults. Restore defaults includes the restart; it can't finish live.
- A push from another station never carries `psn_system_name`.

**Outputs**
- Never gate the per-frame `set_speed` of a controlled marker on movement: the
  write is the freshness stamp every output uses.
- Marker age is measured on the marker's own clock (`Marker.clock_now_us`).
- Position and status land in **one** `Marker.set_pos(..., status=)` write.
- Datagrams are budgeted against **1472 B** through
  `packet_chunking.chunk_to_datagrams`; a PSN frame header is built once per frame.

**Video**
- The HUD is not in the GStreamer chain. Don't re-couple it to buffer flow.
- Classify a failure **before** `_reset_video_flow_state`.
  `ReceiverStateMachine.phase` is the feed's furthest phase, cleared only by
  `forget_video_history`; only `DECODING` is evidence video arrived.
- Placeholder caps are never published as source state.
- Media Gallery loops by SEGMENT seek; `SEGMENT_DONE` comes from the pipeline,
  so don't filter it by sink, and don't revert to a flushing EOS seek.
- SRT `decodebin` pads are `src_0`: don't filter by pad name.
- Pi Camera caps pin `format=I420`, never colorimetry. Never unload a camera
  overlay under a streaming pipeline.
- URL credentials are stripped before the URL reaches the element, and every
  surface that shows, logs or exports a stream URL goes through
  `uri_redaction` (logs through `redact_log_line`).

**Network**
- The OSC listener always binds the wildcard. A membership is released by
  closing the socket, never `IP_DROP_MEMBERSHIP`.
- A pinned multicast socket selects its interface by **name/index**
  (`net_utils.iface_mreqn`, `McastTxSocket(iface=)`), never by address alone.
- A pinned plane **fails closed**; only the web UI fails open.
- `uses_interface` reads config only, never live addresses.
- The network backend is read once per render (`NetworkAdapter.read_network()`):
  never loop `get_state` per interface in a view.

**Input**
- Gamepad → marker routing goes through `_gamepad_marker_id` (or the injected
  `marker_resolver`); never index `_controlled_ids[controller_idx]`.
- Never call `pygame.joystick.Joystick(i)` for an already-open device.
- `invert_control_direction` is applied once in `InputManager.update`, on
  relative input only, never on Z.

**Web**
- Save buttons are `<button type="submit" class="save-btn">`, broadcast buttons
  carry `onclick="broadcastSection(...)"`; non-save actions sharing the styling
  carry neither.
- A failed save reports through `window.OpenFollow.saveError`: no toasts,
  alerts or modals.
- htmx 1.9: `hx-on::after-request`.
- Wizard APIs answer a malformed body with 400, never 500.
- Peer broadcast goes only to private IPs, HMAC-signed with the PIN as key.
- A client address that decides anything (throttle, on-device check, records)
  is `_client_address()`, the connection's `REMOTE_ADDR`; never Bottle's
  `request.remote_addr`, which trusts a client-set `X-Forwarded-For`.

**Packaging**
- `privilege/camera_config.py`, `privilege/media_writer.py` and
  `privilege/settings_backup.py` stay **stdlib-only**: they run as root outside
  the venv or are inlined into `preinst`.

## Development

- **Poetry** for everything: `poetry install`, `poetry run openfollow`,
  `poetry run pytest`. System GStreamer/GTK setup: [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md).
- **`make ci-remote` passes before every `git push`** (the `pre-push-ci` skill
  has the rules). It runs `make ci` on a testing Pi when one is reachable
  (`OPENFOLLOW_CI_HOSTS`), else locally. `make ci` = lint + typecheck + security
  + tests + e2e smoke + build, a superset of GitHub CI (which skips mypy).
- `make format` fixes a lint formatting failure. No `-k`, no `--no-verify`.
- The pre-commit hook runs ruff and bandit from the poetry env (`repo: local`).
  Never give either a `rev:`-pinned hook repo: it re-pins what `pyproject.toml`
  pins and splits every Dependabot bump in two.
- Merge conflicts are common in `services.py` and `video/overlay.py`: combine
  both sides, never pick one.

## Subsystem guides

Read the guide before changing code in its paths.

| Paths | Guide |
|---|---|
| `configuration.py`, config import / export / reset / push, hot reload | [config](docs/architecture/config.md) |
| `video/` (not detection), `video/inputs/`, `privilege/camera_config.py` | [video](docs/architecture/video.md) |
| `video/overlay.py`, `runtime/overlay_*`, `window.py`, on-screen menus | [hud](docs/architecture/hud.md) |
| `psn/`, `otp/`, `rttrpm/`, the OSC transmitter, `packet_chunking.py`, the stall watchdog | [outputs](docs/architecture/outputs.md) |
| `net_utils.py`, `net_adapters.py`, `network/`, `station_fqdn.py`, `runtime/network_observer.py`, `web/discovery.py`, any `*_iface` field | [network](docs/architecture/network.md) |
| `input/`, `binding_conflicts.py`, the OSC listener | [input](docs/architecture/input.md) |
| `video/detection.py`, `video/tracking.py`, `runtime/services_detection_pin.py`, `model_seed.py` | [detection](docs/architecture/detection.md) |
| `zones/` | [zones](docs/architecture/zones.md) |
| `web/` (copy, routes, auth, peers, Setup Wizard) | [web](docs/architecture/web.md) |
| `runtime/deb_update.py`, `runtime/online_sync.py`, `web/whats_new.py`, `privilege/settings_backup.py` | [update](docs/architecture/update.md) |
| `uri_redaction.py`, `web/diagnostics.py`, `runtime/removable_media.py`, `runtime/diagnostics_export.py`, `privilege/media_writer.py` | [diagnostics](docs/architecture/diagnostics.md) |
| `packaging/`, the `.deb`, image and macOS bundle | [`docs/PACKAGING.md`](docs/PACKAGING.md) |
