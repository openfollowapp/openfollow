# Agent Workflow & Project Knowledge Base

See `docs/PROJECT_STRUCTURE.md` for layout.

## Principles

1. **Plan first** for non-trivial tasks (3+ steps or architectural impact)
2. **Verify before done** – prove changes work
3. **Minimal impact** – only touch necessary code
4. **Simplicity first** – find root causes, no temporary fixes
5. **No laziness** – senior developer standards

## Code comments

- **Short, precise, technical, and only where required.** One line is the
  target; two is the ceiling outside a genuinely subtle invariant. Match the
  density of the surrounding file.
- Comment the **non-obvious** part only. Delete anything the code, the field
  name, or an adjacent string already says – a comment restating the line below
  it is noise.
- Don't repeat a comment above each of several near-identical blocks. Put it on
  the first and leave the rest bare.
- **Reasoning belongs in the commit message / PR description, not the source**:
  why a decision was taken, what alternatives were weighed, what review raised
  it.
- **A comment must be self-explaining.** Never reference an issue, PR, or bug
  number in code, a comment, a docstring, or a user-facing string – not even as
  a leading tag like `"""#86: ..."""`. A reader must not need to open a
  tracker to know what a comment means, and an operator reading a log line
  cannot open ours at all. The number goes in the commit message and the PR
  description.
- Docstrings follow the same instinct once they grow into essays. A load-bearing
  contract (what callers must not do, an invariant a refactor could break) earns
  more room than an inline comment; a narrative does not.
- Do NOT leave "legacy" / "removed" / "no longer" breadcrumbs when deleting a
  feature. Write code, comments, tests, and docs as if the current design was
  always the only one – e.g. ONNX Runtime is *the* detection backend, not "the
  backend that replaced the old one". No "we used to…", no naming the removed
  thing even to say it's gone. Put the history in the commit message instead.

## Writing & punctuation

- **Never use em-dashes (Unicode U+2014).** Use an en-dash (U+2013), a hyphen,
  or rewrite the sentence. This applies everywhere: code comments, docstrings,
  UI strings, templates, help text, and Markdown.

## Support cases in public artefacts

Issues, pull requests, commit messages, code comments, tests and docs are
public and permanent. **Never carry a specific operator's details into them.**
That means no IP addresses, hostnames, station names, stream URLs, ports,
config dumps or log excerpts taken from a real report, and no wording that
identifies the report itself ("the bundle from X", a ticket title, a date that
pins it).

Write the **failure class**, not the case:

- Not "the station was on 192.168.3.5/24 looking for a camera on
  192.168.1.100", but "a station addressed on one subnet, with the camera on
  another and no route between them".
- Not "the 381 kB bundle from that unit", but "a bundle dominated by one
  repeating reconnect cycle".
- Where an example address genuinely helps a reader, invent one from the
  documentation ranges (RFC 5737 `192.0.2.0/24`, `198.51.100.0/24`,
  `203.0.113.0/24`; RFC 3849 `2001:db8::/32`).

The class is what a reader needs, and it ages better: the next person hitting
the same fault does not have the same address. Diagnostics bundles attached to
reports stay in the support channel and are never quoted verbatim in the
tracker.

## Commit messages, PR and issue titles

Commit summaries, PR titles and issue titles all use
[Conventional Commits](https://www.conventionalcommits.org):
`<type>(<scope>): <summary>`.

- **Types:** `feat`, `fix`, `test`, `docs`, `refactor`, `perf`, `build`, `ci`,
  `chore`.
- **Scope is required: exactly one from the list below**, the area a reader
  would look in first, even when the change touches others. No comma lists. A
  new scope is added to this list in the same change that first uses it.
- **Summary:** starts lower-case, no trailing period, whole title under ~72
  chars. Rationale goes in the body. No issue/PR numbers (see Code comments).
- **A PR title is imperative** and names what the change does, not the symptom:
  `fix(hud): draw caution status rows in the caution colors`. The squash commit
  reuses it verbatim, and every commit summary is imperative too.
- **An issue title may describe the problem**, because the fix is often unknown
  when it is filed: `fix(hud): status badge draws a caution row as an error`.
  When the change is already clear, write it as the PR title will be. An
  umbrella issue names the outcome it delivers.
- **Labels follow the type:** `fix` ⇒ `bug`, `feat` ⇒ `enhancement`, any other
  type carries neither. Platform and area labels (`Linux`, `Mac`, `Network`)
  are independent of the title.

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

New issues start from the Bug / Feature templates in
[`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE/), which prefill the type and
label.

## Task Management

1. Write plan with checkable items
2. Check in before implementation
3. Track progress as you go
4. Explain changes
5. Document results
6. Capture lessons after corrections

---

## Test coverage (REQUIRED for every code change)

State-of-the-art, senior-level test coverage is the project's standard. Every code change ships with tests – no exceptions for "trivial" fixes or "obvious" refactors. Untested code is treated as broken code, whether it happens to work today or not.

**What "covered" means here:**

1. **New code ships with tests in the same PR.** New functions, methods, branches, dataclasses, config fields, web routes, input handlers, rendering paths, and plugin hooks each get direct test coverage. A PR that adds a function without exercising it is incomplete.
2. **Bug fixes ship with a regression test.** Before writing the fix, write a failing test that reproduces the bug, then make it pass. The test stays in the suite – its job is to prevent the bug from coming back, not to prove the fix works right now.
3. **Both happy and failure paths are exercised.** For anything that parses, coerces, or validates input (config `__post_init__`, web form parsers, OSC handlers, key-code translators, plugin config): cover the valid input, the out-of-range input, the wrong-type input (`None`, `"abc"`, `True`), and the enum-mismatch input. `@pytest.mark.parametrize` is the right tool – one parametrize block per failure mode.
4. **Integration points are integration-tested.** Web-form → config → renderer round-trips (`tests/test_web_helpers.py`), config → hot-reload (`tests/test_configuration.py`), and input → overlay state (`tests/test_services_marker_visuals.py`) each have dedicated suites. When touching one of these seams, add to the matching suite rather than stopping at unit-level coverage.
5. **Tests prove behaviour, not implementation.** Assert on observable output (state fields, emitted values, rendered frames, HTTP responses), not on private helpers or call order. A refactor that preserves behaviour should not break tests – if it does, the test is measuring the wrong thing.
6. **Tests stay fast and hermetic.** No real network (use `monkeypatch`), no real GTK/Cairo windows (pytest-gtk isn't in use here – mock the renderer boundary), no reliance on timers, wall-clock time, or host IP. A test that's flaky once is flaky forever – fix the flake or delete the test.
7. **`make ci` must pass before every `git push`.** This runs lint + full test suite + build. No `pytest -k`, no `--no-verify`, no pushing a red branch and "fixing it on the next commit."
8. **Untestable lines get a pragma + audit row, never a silent gap.** When a line is genuinely not worth (or not possible) to test – optional-dependency fallback branches, bytecode the Python peephole optimizer folds away, OS-specific code paths that can't be reproduced in the sandbox – add `# pragma: no cover - <one-line reason>` on the source line **and** add a matching row to the "Pragma audit" table in [`docs/COVERAGE.md`](docs/COVERAGE.md). A pragma without a row is a review blocker. "I didn't write a test for this" is not a justification – "this line cannot execute in the test sandbox because X" is. If you can't articulate the X in one sentence, write the test instead.

**What NOT to do:**

- Don't add tests that assert the code you just wrote (tautological tests – "this function returns `x` when I pass `x`"). Test against the spec or the bug report, not the implementation.
- Don't test through private helpers (`_coerce_int`, `_apply_parsed_updates`) when you can test through the public boundary (`GridConfig(thickness="0")`, `apply_section_data(cfg, "grid", ...)`). The public boundary is what the rest of the codebase relies on.
- Don't skip tests with `@pytest.mark.skip` to unblock a merge. If a test must be skipped, open an issue, link it in the skip reason, and fix it in a follow-up PR – don't let the skip rot in the tree.

A code change that skips these standards is incomplete regardless of how clean the diff looks. Code review should reject it.

---

## Runtime offline requirement (REQUIRED for every change)

OpenFollow runs on isolated event / stage LANs with no internet uplink – tracker operators bring a Pi (or laptop) onto a show network that has zero outbound connectivity. The full app, including the web UI, MUST work end-to-end without any outbound network access. Every change must keep this contract:

- **No CDN-loaded JS / CSS / fonts.** No ``unpkg.com``, ``cdn.jsdelivr.net``, ``fonts.googleapis.com``, ``cdnjs.cloudflare.com``, etc. Bundle the asset under [`openfollow/web/static/`](openfollow/web/static/) and reference it via the existing ``/assets/<filename:path>`` route. The poetry-core wheel build already ships the entire static dir.
- **No outbound HTTP from server-side code at runtime.** Four documented exceptions, none reachable on the data path: (1) the signed-``.deb`` release updater (``runtime/deb_update.py``) fetches releases only from the GitHub repo named in ``update_github_repo``; (2) the detection **model export** action (``/section/detection/export``) shells out to the optional ``export`` extra (ultralytics), which downloads YOLO weights and exports them to ONNX under ``<storage_path>/models``. Export needs the extra installed *and* an uplink, so it only works on a workstation – on an offline show Pi the button is hidden/disabled and operators copy the ``.onnx`` over manually; (3) the background **online-sync worker** (``runtime/online_sync.py``) which, on startup and on IP change, queries an NTP server (``time_sync_server``, to set the clock since a Pi has no RTC) and the same ``update_github_repo`` GitHub Releases API (to surface the update banner). Unlike (1)/(2) it isn't click-gated, but it is config-gated (``auto_time_sync`` / ``auto_update_check``, default on), fails silently when the LAN has no uplink, and never blocks startup or the render path. The clock-set runs through the privilege broker only when the ``system.set_clock`` capability is already passwordless – it never prompts; (4) the diagnostics bundle's **video-source reachability probe** (``web/diagnostics.py``, section A5), a single bounded TCP connect to the host the *operator's own* video input is configured to dial, plus a bounded DNS lookup for it. It answers the most common support question there is – whether the station can reach its camera at all – which no amount of local state can. It is operator-click-gated (a bundle download, or a save of the bundle to a drive from the web UI or the Operator Screen), sends no stream data and reads nothing back, is capped at ~2.5 s total, is never scheduled and never runs at startup or on the render path, and when the resolved address is public rather than LAN the section says so in the output. Resolving a name the operator configured as a destination is not an exception: it is a query to the show LAN's own resolver, never an outbound connection. The Interface Assignment panel and the bundle's A5 / A6 ask the same shared bounded resolvers the outputs use (`net_utils.HOST_RESOLVER` / `IPV4_RESOLVER`), only for names configured as an output's destination, waiting at most 0.2 s per render. **A caller waits its own budget counted from when the lookup started**, so a short wait (the panel's first, zero-wait pass) never shortens another caller's. Anything else (telemetry, analytics, license check, "phone home", remote feature flags) is rejected.
- **No silent fallback to "online" if a resource is unreachable.** A page that renders fine without its CDN-loaded script but where Save silently no-ops is the worst possible failure mode – the historical example was [`base.tpl`](openfollow/web/templates/base.tpl) loading htmx from ``unpkg.com``: on offline LANs the script never loaded, so every form fell through to a native GET on the current URL and saves silently no-opped. The regression test in [`tests/test_web_server.py`](tests/test_web_server.py) (``test_index_page_uses_locally_bundled_htmx`` + ``test_htmx_static_asset_is_served``) pins the local-asset reference and the asset's content type so this can't quietly revert.

The local LAN is fair game: mDNS-style multicast beacon, peer broadcast, PSN multicast, OSC, RTSP/SRT/RTP/NDI receivers all stay on the show network and are fine. CI runs offline; new external-service dependencies break that gate.

When adding a feature that needs an external resource at design time (e.g. a new JS library, a font, a model file): add it as a bundled asset shipped with the repo, not as a runtime fetch. When in doubt, ask "does this still work after I unplug the WAN cable?" – if no, it's broken.

---

## What this app does

OpenFollow is a Raspberry Pi (or macOS) application that:
- Receives a video signal (NDI or SRT) and displays it fullscreen via GStreamer
- Overlays a Cairo-based HUD on top of the video (marker positions, speed, grid, crosshair)
- Sends PSN (PosiStageNet) marker coordinates via multicast UDP to stage systems (e.g. grandMA3)
- Receives PSN data from other stations and displays viewer markers
- Has a Bottle-based web config UI accessible from any browser on the network
- Runs on Raspberry Pi as a systemd service; also runs on macOS for development

---

## Architecture

```
OpenFollowApp (app.py)
├── AppRuntimeServices (services.py)          – init + per-frame update orchestrator
├── AppConfig (configuration.py)              – all settings, TOML I/O, hot-reload
├── GstNativeSinkReceiver (video/receiver.py) – generic pipeline orchestrator
│   └── video/inputs/                        – pluggable video input modules (ndi.py, srt.py, ...)
├── CairoOverlayRenderer (video/overlay.py)   – HUD drawn on Gtk.DrawingArea above gtksink (display-tick driven)
├── PersonDetector (video/detection.py)       – optional YOLO person detection (bg thread)
├── PsnServer (psn/server.py)                 – sends PSN multicast UDP
├── PsnReceiver (psn/receiver.py)             – receives PSN from other stations
├── InputManager (input/input_manager.py)     – keyboard + gamepad + mouse + OSC
└── ConfigWebServer (web/server.py)           – Bottle web UI + mDNS beacon
```

Camera calibration is web-only (the `/wizard` setup wizard) – there is no on-device calibration overlay. `scene/` holds just `camera.py` + `solver.py` (the DLT solver).

**Frame loop:** `_animate()` runs on a `GLib.timeout_add` at `_FRAME_INTERVAL_MS` (~60 Hz), so `dt` integrates against real elapsed time, not a fixed step.
Order: `_process_input(dt)` → `svc.update_video()` → `svc.apply_detection_pin()` → `svc.update_zone_triggers()` → `svc.update_marker_visuals()`

**Three independent clocks, none of them the display.** Marker state, input, detection pinning, zone evaluation, and every output run off the frame timeout; slow web-driven housekeeping runs off its own 100 ms timeout; the display vsync tick (`Gtk.Widget.add_tick_callback`, wired by `GtkNativeSinkWindow.start_hud_tick`) redraws the HUD and polls the macOS pointer, and **carries no app state**. That separation is load-bearing: a unit with no display attached gets no compositor frame clock, so anything on the vsync tick simply never runs, while `PsnServer` / `OtpServer` / `RttrpmServer` / `OscTransmitterManager` keep transmitting the last known marker state from their own threads at full rate. Do NOT move per-frame state back onto `start_hud_tick`, and do NOT give it a callback parameter.

**Stall watchdog:** `check_frame_loop_stall` (on the housekeeping timeout, because a stalled loop can't report itself) flags `app._frame_stalled` after `_FRAME_STALL_AFTER_S` (**derived from `MARKER_STALE_AFTER_S`** so the indicator and the wire can't disagree) and logs one line per episode on each edge, timed from the last completed frame so the figure is the whole outage. It reads `app._last_frame_completed`, stamped as the **last line of `animate`** – a frame that raises or hangs part-way must not advance the liveness clock, or a loop failing every tick reads as healthy while every output goes stale. `get_runtime_stats_snapshot` overlays `playback.stalled` + `playback.seconds_since_last_frame` at **read** time. The Statistics → Device `Frame clock` chip is driven by the **age**, not the flag alone: the watchdog shares the main loop with the frame clock, so a block inside one callback stops both and leaves the flag False. It compares against `playback.stale_after_s` (published from `MARKER_STALE_AFTER_S`), never a literal, so the chip can't disagree with what the outputs are doing.

---

## Config model (`configuration.py`)

All config lives in `config.toml` (auto-reloaded when file changes on disk).

### AppConfig top-level fields
| Field | Default | Notes |
|---|---|---|
| `video_source_type` | `"testpattern"` | `"testpattern"` (Media Gallery, the default), `"ndi"`, `"srt"`, `"rtsp"`, `"rtp"`, `"picam"`, `"v4l2"` (Linux USB camera), `"avf"` (macOS USB camera), or any registered plugin ID |
| `ndi_source_name` | `""` | NDI source string (read by NDI plugin) |
| `srt_host` | `"srt://0.0.0.0:5000"` | SRT URL (read by SRT plugin) |
| `srt_passphrase` | `""` | SRT encryption key; drives `srtsrc.passphrase` and outranks a `?passphrase=` in the URL |
| `rtsp_user` / `rtsp_password` | `""` | RTSP login; drives `rtspsrc.user-id` / `user-pw` and outranks URL userinfo. **Not** device-local: shared across stations pointed at the same camera, so both survive export / peer broadcast (`web_pin` does not). Redacted in the diagnostics bundle |
| `window_width/height` | `1280×720` | |
| `psn_system_name` | `"OpenFollow"` | Shown in PSN + web UI |
| `psn_mcast_ip` | `"236.10.10.10"` | PSN multicast group |
| `psn_source_iface` | `""` | Bind PSN / beacon to this interface **by name**; empty = auto-detect |
| `web_port` | `80` | Web config UI port |
| `web_bind_iface` | `""` | Serve the web UI on this interface only, **by name**; empty = every interface. Resolved by `net_utils.resolve_web_bind`, which an explicit `web_bind` address outranks. The **one plane that fails open**: an unresolvable pin serves everywhere and records an advisory, because a silent output is diagnosable from another station and an unreachable config UI is not. Device-local |
| `interface_labels` | `{}` | Operator labels by interface name (`"enx9c69d3ac16ab" -> "Lighting"`): unique ignoring case, at most 20 characters (`net_adapters.normalize_labels`). Edited per row on the Network Interface card (`POST /section/network/label`, `.../label/forget`); every surface then names the interface `Lighting (enx…)` via `net_adapters.display_name`. Device-local: stripped from export, never broadcast, an import keeps this station's, Restore defaults clears them (not in `_DEVICE_IDENTITY_FIELDS`) |
| `station_fqdn` | `""` | This station's name on a venue's own DNS (`of-1.stage.example.com`). One rule in `station_fqdn.py` for every surface: RFC 1123, at least two labels, lower-cased, trailing dot dropped, no IP literal, nothing under `.local`; invalid loads as blank. Edited in General → Station Settings → Advanced Settings (an input joined to the name/PIN form by `form=`, re-rendered by its `hx-select-oob`). The web server accepts changes through it (`_allowed_request_hosts`, read from the request's config, so no restart) and the HUD shows it in place of `<host>.local` (`web_ui_host`); the `.local` name keeps working. Never resolved. The on-screen Network screen's **Remove FQDN** clears it (labelled "interrupts network traffic" where the backend is writable). On Linux a change runs `AppRuntimeServices.apply_station_fqdn_change` off-thread (latest name wins, under `_network_op_lock`): the `/etc/hosts` 127.0.1.1 line lists it before the short hostname, and `NetworkAdapter.set_dhcp_fqdn` sends it as DHCP option 81 and **reconnects every interface** so the server sees it now (NM: `ipv4.dhcp-fqdn` per profile + `con up`, refused while a profile sets `ipv4.dhcp-hostname`; dhcpcd: one global managed block, `hostname` + `fqdn both`, then `dhcpcd -n` per interface; `hostname` alone sends the whole name as option 12). Option 81 replaces option 12, per RFC 4702. It never prompts for a password (`allow_prompt=False`: nobody is at a prompt), and NM touches only ethernet, Wi-Fi and VLAN profiles it manages itself (a bridge, a tunnel or a connection NM reports as external belongs to whatever made it). At startup `reconcile_station_fqdn` writes only the profiles (or dhcpcd block) whose name differs from config and reconnects nothing: the one sanctioned startup write to network config. It shares the change counter, so a save made since startup is never written over by the startup name. What DHCP could not do is a caution under the field and on the Network screen (`station_fqdn_problems`). `/etc/hosts` is Linux only, under a lock a rename shares. No new privilege capability. Device-local, in `_DEVICE_IDENTITY_FIELDS`: stripped from export and section broadcast, kept by import and Restore defaults |
| `video_input_iface` | `""` | Interface SRT / RTSP / RTP reach the camera through, **by name**; empty follows `psn_source_iface`, and with the station on auto-detect too the routing table picks per camera. A plugin field (`video_input_pin_field`, `pins_interface = True`) holding the operator's own pin; `VideoInputBase.runtime_config` resolves a blank one through the station and drops the pin where `uses_interface` says none governs the connection (loopback; an RTP wildcard or listen address whose pin is only the station's), and the running input, its network plane and the panel are all built from that (so `config_changed` also live-swaps the input when the station pin it follows changes; the dispatcher compares against the station interface it actually committed, so a rejected station change moves nothing). **`uses_interface` reads config only, never the live addresses**: a plane whose on/off followed the addresses switched itself off once its source was stopped and never rebuilt it. A source at one of this station's own addresses is exempted where it is decided live instead: the preflight never refuses it, the plane does not stop it while that address exists, and the panel reads `This station`. SRT on Linux appends `bindtodevice` to the URI query (libsrt `SRTO_BINDTODEVICE`) for a remote IPv4 camera only: libsrt throws on a device for any other socket family, so a name with an IPv6 address is judged by the routing table's choice for every address. RTSP gets only `multicast-iface` because `rtspsrc` exposes no socket; RTP gets `multicast-iface`, binds a wildcard unicast listener to its own pin's address and refuses a URL address that is not the pin's. `VideoInputBase.preflight` runs before every build and refuses rather than dial off the pin, checking every address the name resolves to (both families): SRT on Linux needs a route through the device in `/proc/net/route` (a probe connect cannot tell - the kernel lets a device-bound socket connect anywhere, assuming on-link), RTSP and SRT elsewhere compare the routing table's chosen source address with the pin. Device-local (not in `_DEVICE_IDENTITY_FIELDS`: blank follows the preserved station pin); edited in the Network Interface Assignment panel |
| `web_pin` | `""` | Auth PIN; when non-empty, browser routes require login + cookie (`SameSite=Strict`), peer-to-peer routes require HMAC-signed headers |
| `update_github_repo` | `"openfollowapp/openfollow"` | `owner/repo` slug the `.deb`-release updater queries for new releases |
| `update_service_name` | `"openfollow"` | systemd unit restarted after a `.deb` install |
| `update_include_prereleases` | `False` | Hidden (config-only): offer pre-release builds, not just stable, in both the auto-check and `Check & Install Latest` |
| `auto_update_check` | `True` | Auto-query GitHub on startup / IP change (drives the update banner + footer flag); fail-silent offline |
| `auto_time_sync` | `True` | Auto-sync the system clock from `time_sync_server` on startup / IP change (Pi has no RTC); needs the `system.set_clock` grant |
| `time_sync_server` | `"ptbtime1.ptb.de"` | NTP server the online-sync worker queries for trusted time |
| `controlled_marker_ids` | `[]` | Markers this instance moves |
| `viewer_marker_ids` | `[]` | Markers shown in overlay (incl. remote); always holds every controlled id (`viewed_with_controlled`: on load, in the selection route and in `init_markers`) |

### Sub-configs
- **CameraConfig:** pos_x/y/z, pitch/yaw/roll, fov
- **GridConfig:** visible, width, depth, spacing, x_offset, y_offset, z_offset, origin_visible, origin_length, origin_thickness
- **MarkerConfig:** min_speed, max_speed, move_speed, default_pos_x/y/z, `invert_control_direction` (flips X **and** Y together for *relative* input – keyboard, gamepad, 3D mouse – so an upstage camera's picture matches the controls; applied once in `InputManager.update` via `_oriented`, never on Z, and never on the absolute paths: 2D mouse unprojection and OSC writes), `marker_style` (`crosshair` | `cone`; `cone` draws a truncated cone between the stage plane and the marker's Z, below the stage included, from `cone_base_diameter` / `cone_top_diameter` (m) and `cone_thickness` (px), filled at `cone_opacity` when `cone_filled` (the side under a left-lit gradient when `cone_shaded`), and ignores the ball / crosshair / Z line / ground circle fields; the silhouette edges come from `scene/solver.cone_silhouette_angles` in closed form and are inserted into the rings (`cone_ring_angles`), with the convex hull `ring_silhouette_indices` only where the camera looks into the cone or part of it is behind the camera; the renderer keeps each cone as a picture (`ConeCache`) and draws it again only when an input it is drawn from changes, and the mouse grab area is the base ring built from half of `cone_base_diameter`), ball_visible, ball_size, transparency, crosshair_visible, crosshair_size, crosshair_color, crosshair_thickness, z_line, z_line_thickness, ground_circle, ground_circle_size, ground_circle_filled, z_display_from_stage
- **ControllerConfig:** enabled, keyboard_enabled, mouse_enabled, mouse_hysteresis_px, mouse_smoothing, mouse_max_y, mouse_wheel_z_enabled, mouse_wheel_invert, mouse_wheel_z_step, mouse_double_click_reset, deadzone, invert_y, curve, the gamepad button map (`btn_reset`, `btn_speed_up/down`, `btn_move_z_up/down`, `btn_settings`, `btn_next/prev_marker`, …), the keyboard binding map (`key_move_layout`, `key_reset`, `key_speed_up/down`, `key_toggle_help`, `key_toggle_zones`, `key_settings`, …), `move_xy_stick` (no LED fields)
- **DetectionConfig:** enabled (the **only** detection on/off – the web Tracking control writes it; `True` ⇒ detection runs and drives markers per `pin_mode`), model (default `yolo26n.onnx`; the web Models picker abstracts the five YOLO26 sizes as quality tiers Fastest/Fast/Balanced/Accurate/Most Accurate, pre-shipped with each distribution – see "Pre-shipped detection models"), storage_path (not exposed in the UI; device-local – stripped from config export and preserved-across-import so a path never crosses machines; blank auto-resolves to `/mnt/nvme/openfollow/yolo` when `/mnt/nvme` is a mountpoint, else a `yolo` folder under the working dir – via `resolve_detection_storage_path` in [`video/detection.py`](openfollow/video/detection.py), used by `_prepare_model_path` + the web model-discover/export helpers; set an absolute path in `config.toml` to override), inference_size (hidden in the UI; auto-detected from the model's export), confidence, interval_ms, show_boxes, show_labels, box_color, box_thickness, max_persons, pin_marker_id (`-1` = follow selected marker; used by `replace` mode only), pin_point (`top`|`bottom`), smoothing, prediction, grace_period_ms, pin_mode (`replace`|`assist`, default `assist`; `replace` = Fully Automatic auto-pins one marker, `assist` = AI-Assisted refines **all** controlled markers), assist_radius_m, assist_strength, masks_enabled (master switch for region-of-interest masking, default `False` ⇒ masks inactive even when drawn; `True` ⇒ detection confined to the enabled masks; live-applied via the `/api/detection/masks/enabled` route + staged-config drain), masks (`list[DetectionMaskConfig]`: region-of-interest polygons in normalised 0–1 frame coords; detection confined to the union of enabled masks only when `masks_enabled`, empty = unrestricted; live-applied, no restart). CLAHE preprocessing is always on (no config field). There is no `pin_marker` boolean – `enabled` gates the whole subsystem.
- **OscConfig:** enabled, port (default 8765), allowed_sender_ips (default `[]` = allow-all + startup WARNING; normalised to `list[str]` by `__post_init__` to survive malformed TOML), `listen_iface` (pin the **multicast membership** to one interface **by name**; empty follows `psn_source_iface`, and a station on auto-detect leaves the choice to the routing table. It governs `IP_ADD_MEMBERSHIP` only – **the listener socket always binds the wildcard**, and must: a socket bound to one unicast address receives no multicast and no broadcast at all, because the kernel matches a datagram's destination against the bound address and a group address is neither. `IP_ADD_MEMBERSHIP` still returns success on such a socket, so the failure is silent – do NOT reintroduce a bind host. Resolved by `net_utils.resolve_multicast_iface`, whose three states map onto `join_multicast_group_on_iface`: `""` unpinned, an address pinned, `None` pinned-but-down. Fails **closed** – a down pin holds no membership rather than one on another interface, while unicast/broadcast keep arriving – and the observer's `OSC input` plane resubscribes via `OscService.set_multicast_iface` when the interface returns. That **rebinds the listener**; it does NOT drop the membership, because `IP_DROP_MEMBERSHIP` is keyed by interface address and the case that needs it is the one where that address is already gone – the kernel then reports the drop as successful and releases nothing, stranding the group on the excluded adapter for longer than the process lives. Closing the socket is the only unconditional release; subscriptions sit on the service dispatcher, so they survive it. Device-local; edited in the Network Interface Assignment panel)

### Validation contract (REQUIRED for every config change)

All config values must survive a hand-edited `config.toml` and a crafted POST to `/section/<name>` without crashing the app or silently producing bad behavior. Python dataclasses **do not** validate field types at runtime – a `fov = "wide"` entry in TOML would flow straight into `project_points` and crash rendering; a `transparency = 5` would mis-render forever.

When you add, remove, or change a field on any config dataclass:

1. **Normalise in `__post_init__`.** Use the helpers at the top of [`configuration.py`](openfollow/configuration.py): `_coerce_float` / `_coerce_int` / `_coerce_hex_color` / `_coerce_optional_float` / `_coerce_choice`. They coerce to the target type, clamp to `[lo, hi]` if supplied, and fall back to the declared default when coercion fails.
2. **Re-run `__post_init__` after web-form saves.** `apply_section_data` in [`web/routes.py`](openfollow/web/routes.py) dispatches on section name and invokes the re-run for every dataclass that has one (currently: `camera`, `grid`, `marker`/`movement`, `controller` (+ `gamepad`/`keyboard`/`mouse`), `osc`, `detection`, `otp_output`, `rttrpm_output`, `trigger_zones`). Extend the dispatch when adding a new section with its own `__post_init__` – otherwise a crafted POST bypasses validation that a hand-edited TOML would trip.
3. **Add regression tests in `tests/test_configuration.py`.** Cover: wrong type (string / `None`), out-of-range, enum mismatch, and (for list fields) heterogeneous entries. Follow the existing `test_grid_config_*` / `test_detection_config_*` pattern – one `@pytest.mark.parametrize` per failure mode.
4. **Register every web-form field in [`openfollow/web/validation.py`](openfollow/web/validation.py)::`FIELD_RULES`.** Each entry MUST use the same parser as `_SECTION_FIELD_PARSERS` (or the corresponding inline parser used by the special sections in `apply_section_data`), the same `lo` / `hi` / `choices` / `pattern` bounds enforced by `__post_init__`, and a `human_error` string the user will see on blur. Templates render every input with the standard markup (see [`partials/grid.tpl`](openfollow/web/templates/partials/grid.tpl)): `hx-get="/api/validate/<section>/<name>"`, `hx-trigger="blur changed delay:200ms"`, `hx-target` pointing at a sibling `<span class="field-error">`, `hx-include="closest form"`, and `aria-describedby` / `aria-invalid` for accessibility. The form-gate JS in `base.tpl` (`refreshFormGate`) only disables actual config-save/broadcast submit controls – Save buttons MUST be `<button type="submit" class="save-btn">…</button>` and Broadcast buttons MUST carry the inline `broadcastSection(...)` call shape (`<button class="broadcast-btn" onclick="broadcastSection(…)">…</button>`); the gate selector matches `button[type="submit"].save-btn, button.broadcast-btn[onclick*="broadcastSection"]`. Buttons that share the `.save-btn` / `.broadcast-btn` *styling* but trigger non-save actions (Detection Install / Uninstall, Reset to Defaults) MUST NOT carry `type="submit"` or `broadcastSection(...)` – that's what keeps them clickable while a validation error is visible.

   Every string-typed `FieldRule` declares its sanitisation contract: `strip_whitespace` (defaults to `True`), `max_len`, optional `sanitiser` (defaults to control-char + bidi-override stripping), `pattern` for syntax checks. The contract MUST match what `__post_init__` actually does to that field on Save – a `__post_init__` strip / coercion that lacks a matching `FieldRule` flag is a CI failure. Lists with per-entry rules (`allowed_sender_ips`, `colors`, `controlled_marker_ids`) get a `custom` validator that surfaces the offending entry: `"Entry 3 ('999.0.0.1') is not a valid IPv4 / IPv6 address."`.

   Cross-field auto-corrections (`marker.max_speed >= min_speed`, `detection.inference_size` snap-to-32, `psn_system_name` empty fallback) are surfaced via `note()` as advisory **blue** notes – not errors. They DO NOT set `aria-invalid` and DO NOT gate Save; the server-side `__post_init__` chain repairs the value at save time.

   The consistency test in [`tests/test_template_validation_consistency.py`](tests/test_template_validation_consistency.py) walks every partial and asserts this wiring. A new field that ships without a `FIELD_RULES` entry, or an `<input>` whose `name` matches an entry but lacks the standard markup, fails CI. **Code review should reject any web form change that does not extend the registry.** [`openfollow/web/templates/wizard.tpl`](openfollow/web/templates/wizard.tpl) is intentionally excluded from the consistency test; wiring it into the registry is tracked as a separate follow-up.

A config change that skips any of these four steps is incomplete, regardless of what the happy path looks like. Code review should reject it.

### Hot-reload rules (`apply_runtime_config_changes`)
- **Requires app restart:** **detection** changes the worker can't serve in-process: enabling (`detection.enabled` going `False → True`, because the receiver pipeline must wire the GStreamer appsink into a fresh detector and only `init_video` does that), changing `detection.inference_size` (GStreamer appsink caps are pinned at pipeline build time; live-restamping the worker's `_inference_size` would silently disagree with the appsink resolution), and any detection edit when the detector is missing or unavailable (`_person_detector` is None or `available is False` because the backend never loaded at startup – `reload_config` would silently no-op since the worker thread was never started). `web_port` and `web_bind_iface` also stay restart-required (the listening socket can't move under a request being served on it; server-restart-in-place is fragile, and both are rare changes). The on-screen Network screen's `Serve web UI on all interfaces` clears the pin and requests the restart itself - it is the documented lockout escape and is deliberately not gated on a writable network backend
- **Live update (no restart):** **video_source_type and any plugin config field** (auto-detected via `plugin.config_changed()`; the receiver live-swaps the active input plugin in place via `swap_video` → `receiver.swap_input`, transactional with rollback – see [`AppRuntimeServices.swap_video`](openfollow/services.py)), camera, grid, movement (speed limits + default position), marker, controller, **mouse3d** (read thread runs for the handler's lifetime; the block swaps the mapping config and the `enabled` gate is read live in `InputManager.update`), osc, trigger_zones, controlled_marker_ids, viewer_marker_ids, psn_system_name, **psn_source_iface, otp_output, rttrpm_output**, **detection** running-detector cases – on→on (worker drains a staged config between frames; rebuilds the inference session in-thread when model / storage_path changes) and on→off, **window_width / window_height, web_pin**
- Restart triggered via `_web_commands.request_restart()`, polled in `_check_restart_request()`
- Saving config with restart-requiring changes triggers an automatic restart via the hot-reload file watcher within ~1 animation frame

### Live-apply pattern
Service rebind/restart live-apply paths (sockets, worker threads) route through `_apply_with_fallback("name", apply_fn, on_failure=…)` in [`configuration.py`](openfollow/configuration.py). The helper logs duration on success, logs `logger.exception` plus runs `on_failure` on exception, and returns so the dispatcher keeps applying subsequent settings – a single failed live-apply on one service must not bypass live-applying everything else. Pure in-memory mutations that can't fail at runtime (e.g. `window_width/height` resizing the GTK window, `web_pin` mirroring into `app._config`) skip the helper because there's no failure mode to revert from.

Each underlying service exposes a small `restart(...)` (or `rebind(...)` for receivers) that does `stop()` → reassign attributes → `start()`. Marker registrations and other shared state survive across the cycle by living on instance attributes that aren't touched by `stop()`/`start()`. `start()` mints a **fresh stop event per generation** and hands it to the threads it spawns – never `clear()` a shared one, or a send thread that outlived `stop()`'s join is put back to work alongside the new generation. Every loop, send, and socket-retry path judges its own generation's event, so a dying survivor can't tear down the live socket. See [`OtpServer.restart`](openfollow/otp/server.py), [`RttrpmServer.restart`](openfollow/rttrpm/server.py), and [`PsnReceiver.rebind`](openfollow/psn/receiver.py).

The four-state transition matrix (off→on, on→on with new cfg, on→off, off→off) lives on `AppRuntimeServices.apply_*_change` orchestrators in [`services.py`](openfollow/services.py); the dispatcher only knows to call them with the new cfg.

For services with a worker thread (e.g. detection), the GTK thread does NOT mutate worker state directly. Instead the GTK side calls `service.reload_config(new_cfg)`, which stages the config under a small lock; the worker drains the staged value at the top of its next loop iteration. Heavy operations – backend session rebuilds for [`PersonDetector`](openfollow/video/detection.py) on a model / storage_path change – happen on the worker thread, not the GTK thread. Failure during a worker-side rebuild keeps the prior config + backend so the loop never lands in a half-applied state. The pattern is "single-element pending slot, latest-wins" – no queue, no event drain loop.

---

## Video pipeline (modular input plugins)

### Plugin architecture (`video/inputs/`)
Each video protocol is a self-contained file in `video/inputs/` that subclasses `VideoInputBase`:

```
video/inputs/
    _base.py        # ABC: VideoInputBase, ConfigField, InputCapabilities, ReconnectPolicy
    __init__.py     # Auto-discovery registry (pkgutil) + is_available() filter
    ndi.py          # NDI plugin (pipeline, ctypes discovery, web UI)
    srt.py          # SRT plugin (pipeline, decoder config, web UI)
    rtsp.py         # RTSP plugin (rtspsrc, auto-codec, multi-transport)
    rtp.py          # RTP plugin (udpsrc, manual codec selection)
    picam.py        # Raspberry Pi camera plugin (libcamerasrc, CSI/MIPI); caps pin format=I420
    v4l2.py         # USB camera / capture card plugin (v4l2src, UVC, Linux-only)
    avf.py          # USB camera / capture card plugin (avfvideosrc, macOS-only)
    testpattern.py  # Media Gallery source (id stays "testpattern"): plays a stored image / VP8 clip
                    #   or the Stage / Grey defaults from media_store.py – always available
```

The **Media Gallery** (`MediaGalleryInput`, `input_id` stays `testpattern`) plays a device-local image / looping VP8 clip / bundled default selected via `testpattern_selected_media`. Its library lives in [`video/media_store.py`](openfollow/video/media_store.py) (storage resolver, id rules, hard cap, magic-byte + `GstDiscoverer` validation, thumbnails); the web management routes (`/video-input/testpattern/*` in [`web/routes.py`](openfollow/web/routes.py)) handle list / upload / capture / select / download / delete. The selection is device-local (web-only via `ConfigField.device_editable=False`; stripped on export, preserved on import) and silently falls back to Stage when unresolvable. Clips loop **seamlessly** via a SEGMENT seek: `on_bus_async_done` arms the loop once (a flushing `FLUSH | SEGMENT` seek), so the clip posts `SEGMENT_DONE` instead of `EOS` at the end, and `on_bus_segment_done` queues the next pass with a non-flushing seek – the sink never stops, so there is no end-of-clip freeze and the stall watchdog never trips. `GstPipeline` aggregates every sink's segment-done into ONE `SEGMENT_DONE` posted by the pipeline itself (`message.src` is the pipeline, not the sink), so the receiver forwards it unfiltered – do NOT re-add a per-sink (`shared_videosink`) src filter, it drops the aggregated message and the clip stalls. `on_bus_eos` stays a fallback that re-arms the loop (a stray EOS is not a disconnect); do NOT revert looping to a flushing EOS seek – that caused the freeze + "No Signal" flash. WebP image support relies on `gstreamer1.0-plugins-bad` (webpdec/webpenc, via `libgstwebp`); it is treated as always-present and pinned as a dedicated dependency by [`tests/test_system_dependencies.py`] so a pipeline refactor can't silently drop it.

**Adding a new protocol:** create `video/inputs/<name>.py` with a `VideoInputBase` subclass. No other files need changes – the registry auto-discovers it, the web UI renders its fields, the receiver delegates to it.

**Platform availability:** plugins declare `is_available() → (bool, reason)` so the picker can hide backends that won't run on the current host (e.g. `v4l2src` is Linux-only, `avfvideosrc` is macOS-only). `get_registry()` returns every discovered plugin; `get_available_registry()` / `get_available_input_ids()` filter by `is_available()` – UI pickers and the wizard use the filtered view, while discovery-contract tests use the full registry. Plugins must still register on every platform; `is_available()` only gates display, not import.

Each plugin declares:
- `config_fields()` → config fields stored in config.toml
- `capabilities()` → feature flags (discovery, selection hotkey, latency handling)
- `reconnect_policy()` → max attempts, backoff, timeout, fallback behavior
- `create_pipeline()` → GStreamer element chain
- `web_ui_html()` → HTML fragment for the web settings form
- `web_routes()` → additional HTTP endpoints (e.g. `/video-input/ndi/sources`)
- `is_available()` → `(bool, reason)`; defaults to `(True, "")`. Override when the plugin needs an OS or GStreamer element that isn't universally present (e.g. `v4l2src` on Linux, `avfvideosrc` on macOS, the `ndisrc` plugin from gst-plugin-ndi).
- `source_element_name` → the **instance name** of the element that first produces bytes from the source (`"rtspsrc"`, `"udpsrc"`, `"media_source"`, …), or `None` for an input with no such boundary. The receiver probes it for the first buffer → `DATA_ARRIVING`. `TestSourceElementDeclaration` in [`tests/test_video_input_plugins.py`](tests/test_video_input_plugins.py) builds each plugin's pipeline and asserts the name resolves, so a rename inside `create_pipeline` can't silently detach the probe
- `source_kind` → `SourceKind.REMOTE` (dials a host: rtsp, srt), `NAMED` (finds a source by name: ndi), `LISTENER` (waits at an address: rtp) or `LOCAL` (hardware or a file on this box). It picks the **wording** of the sentence and the action, so an NDI source is not told to check an address and port it does not have, and a USB camera is not told to check a sender. Defaults to `LOCAL`, which names no address, port or sender - a plugin that forgets is vague, never wrong
- `observe_progress(pipeline, report)` → optional; report protocol progress only that plugin can see. RTSP wires `rtspsrc::on-sdp` → `TRANSPORT_UP` + `STREAM_DESCRIBED`. The receiver keeps the furthest phase reported, so a duplicate or out-of-order report is harmless

### Receiver (`video/receiver.py`) – generic orchestrator
Delegates protocol-specific work to the active plugin. Retains shared infrastructure:
- `_build_overlay_tail()` – links the plugin tail directly to `shared_videosink` (no `cairooverlay`; HUD is drawn on a `Gtk.DrawingArea` above the gtksink widget – see "HUD rendering" below)
- `_create_placeholder_pipeline()` – black-frame "No Signal" fallback
- Shared gtksink management (detach/reattach across pipeline switches)
- Connection timeout, reconnection scheduling (driven by plugin's `ReconnectPolicy`)
- First-frame / caps detection via downstream pad probes (`_on_pad_event` for the caps event, `_on_sink_buffer` → `_handle_video_connected` on the first buffer) – replaces the old `cairooverlay` caps-changed signal
- Source-byte observation: `_attach_source_probe()` puts a one-shot buffer probe on the plugin's `source_element_name`. Attached **per pipeline** (the source element is rebuilt on every reconnect), unlike the sink probes which attach once for the shared sink's lifetime. A source with no static `src` pad (`rtspsrc`) is followed via `pad-added`. The same first buffer records `source_format` from that pad's caps (a pixel format for raw video, else the media type), read from the caps **string** because the structure getters are what GStreamer 1.26.2 broke; the sink probe sits after `videoconvert` and never sees it. It is logged once per connection and published as `video.source_format`
- Source discovery scheduling (calls plugin's `discover_sources()`)
- Source selection state management (generic, checks `InputCapabilities.has_source_selection`)

### NDI plugin (`video/inputs/ndi.py`)
```
ndisrc → ndisrcdemux → ndi_video_queue (leaky) → videoconvert → shared_videosink
```
- Source discovery via ctypes → libndi SDK
- Source selection overlay activated by N key or controller back button
- Forces pipeline latency to 0 on ASYNC_DONE
- Reconnect: max 1 attempt, then fallback to source selection

### SRT plugin (`video/inputs/srt.py`)
```
srtsrc → pre_queue → decodebin → post_queue → videoconvert → shared_videosink
```
- `srtsrc`: `mode=caller`, `wait-for-connection=True`, `latency=125ms`, **`auto-reconnect=False`** (left on, it retries internally and the failure never reaches the bus)
- `srt_passphrase`, when set, drives the `passphrase` property and the URL's own `?passphrase=` is stripped first, so one field answers "which key is this stream encrypted with". Blank leaves the URL path untouched
- Hardware decoder priority boosting (V4L2 > avdec > openh264)
- Preserves decoder latency on ASYNC_DONE (do NOT force 0)
- `pad-added` on decodebin: **do NOT filter by pad name** (uses `src_0`, not `video_0`)
- Reconnect: 3 retries with 8s first-frame timeout, then no-signal placeholder fallback

### RTSP plugin (`video/inputs/rtsp.py`)
```
rtspsrc → decodebin → post_queue (leaky) → videoconvert → shared_videosink
```
- `protocols` allows TCP + UDP + multicast, **except for a name that resolves to an IPv6 address, which gets TCP interleaved only**. `rtspsrc` opens its UDP ports for the family of the literal in the URL, so a name reads as IPv4 even when the control connection lands on IPv6 (a dual-stack server, `localhost`), and `udpsink` then refuses the server's address with `Invalid address family`. `_pin.resolves_to_ipv6` asks the bounded resolver (`_RESOLVE_WAIT_S`); a name still resolving keeps every transport this attempt and the next attempt reads the answer. An IPv6 literal is left to the element, which reads its family itself
- `tcp-timeout` 10 s so the element gives up before the connection timeout, `latency` 0, `drop-on-latency`, `buffer-mode` none; only the video track is linked into `decodebin`, since an NVR may list its audio track first

### Stream credentials (RTSP / SRT)
`rtsp_user` / `rtsp_password` / `srt_passphrase` are rendered as a login block under each plugin's URL (password inputs) and drive the element properties directly. The URL's own credential is **stripped before** `location` / `uri` is handed over – `rtspsrc` tries URL userinfo first and only then falls back to `user-id` / `user-pw`, so leaving it in would let a stale URL credential outrank the form. Blank fields leave the existing URL-userinfo path working untouched.

`ConfigField(strip=False)` marks a credential so the web-save path keeps its edge whitespace, where every other string field is trimmed: the whitespace can be part of the secret and is invisible in a password field, so trimming it fails authentication with nothing on screen to explain why.

### Video failure taxonomy (`video/failure.py`)

Four outcomes that all presented as the same "no video received after 8s":
**nothing ever answered**, **it answered but sent no media**, **media arrives
but never decodes**, and **it was flowing and stopped**. Each sends the
operator to different equipment.

`ConnectionPhase` (`STARTING` → `TRANSPORT_UP` → `STREAM_DESCRIBED` →
`DATA_ARRIVING` → `DECODING`) records how far an attempt got. `DATA_ARRIVING`
is the load-bearing one (bytes out of the source element, which every protocol
has), and `ReceiverStateMachine.note_phase` keeps the **furthest** reached
because the probes fire from different threads and arrive out of order.

`classify_failure(phase, domain, code, message, debug)` names the failure, and
the phase is what decides an ambiguous error: the same
`GstResourceError.OPEN_READ` is `UNREACHABLE` before any bytes arrive and
`STALLED` after them. **`debug` is not optional detail** - the refusal marker
and the empty-SDP marker both live in GStreamer's debug string, not its message,
so a caller that drops it discards the evidence the mapping depends on. A known
domain carrying a code with no rule (the generic `FAILED = 1`) stays `UNKNOWN`
rather than landing in a neighbouring bucket. The one exception is observed on
hardware: a caps negotiation failure arrives as `gst-stream-error-quark:1` with
`reason not-negotiated` in the debug string, and **before any video** that is
`UNSUPPORTED_MODE` (the source cannot deliver the format, size or frame rate
asked for); after video it stays `STALLED`.

**Classify before `_reset_video_flow_state`.** `_schedule_reconnect` clears the
phase it classifies from, so the verdict is computed at the top of that method;
computing it afterwards reads every failure as a cold start.

**`ReceiverStateMachine.phase` is the furthest the *feed* ever reached, not the
attempt.** It survives `reset_video_flow` (which clears only per-attempt state
like `video_flow_detected`) and is cleared solely by `forget_video_history` on a
source change. A retry knows nothing, so classifying or publishing from a
per-attempt phase redescribes every dropped feed as one that was never
reachable - which is exactly what hardware showed, twice: first as a `STALLED`
verdict overwritten by the next retry's `UNREACHABLE`, then as `stalled`
published alongside `phase: starting`.

There is deliberately **one** such concept. An earlier shape had a separate
`had_video` latch beside a per-attempt phase, and the two disagreed on the
device the moment a retry landed.

**Only `DECODING` is evidence video arrived** - nothing but `mark_frame_received`
sets it. `DATA_ARRIVING` is bytes out of the source element, which a feed
carrying an undecodable payload produces just as readily, so it means "it
answered", not "it worked". Because only RTSP reports `TRANSPORT_UP` /
`STREAM_DESCRIBED`, `DATA_ARRIVING` is also the only route to `NO_DATA` for
every other input.
Hardware found this: a camera pulled mid-stream was classified `STALLED`
correctly and then reclassified `UNREACHABLE` by the very next retry, telling
the operator nothing answered about a camera that had been on screen a second
earlier.

One module owns the enum and both text maps so the five surfaces that render
them cannot drift. `VideoFailure` values are a **wire interface** –
`/api/stats` publishes `video.failure` for support tooling, so renaming a
member breaks a consumer's matching.

Sentences state **only what this station observed**, never what the far end
did. "answered, but sent no video" asserted the camera sent nothing when it may
have been sending into a blocked path; a stall is "stopped arriving", not
"stopped sending". Sentences otherwise **describe the observation and stop**,
and `failure_action` carries
**one short next step** beside them - two fields, never blurred into one, so a
reader quoting the observation into a support thread does not carry our advice
with it. Anything longer than a line belongs in the website docs, which an
operator on a stage cannot open. The pipeline's own wording ("Could not open
resource for reading and writing.") is **not** shown to an operator: it reads as
a second, unrelated fault and nothing can be done with it. It stays in
`/api/stats` and the diagnostics bundle, and stands in for the sentence only
where there is no classification at all. `where` must already be redacted – it reaches the HUD and
`/section/statistics`. `UNKNOWN` renders **no** sentence on any surface: it
would sit above the element's own wording and contradict it.

**The phase is published inside `_StatusSnapshot`, with the verdict it
explains.** Publishing it separately from `failure` would let a reader pair one
generation's phase with another's verdict.

**Surfaces:** the top-right status badge carries the *chip* only
(`_status_flags["video_failure"]`, ~40 characters per row). The Settings error
box and both web boxes carry the **sentence plus the action**, and never the
element's own wording - every renderer uses `failure_text or error_message`, so
the raw text appears only when there is no classification to replace it. It
stays in `/api/stats` and the diagnostics bundle, which is what support reads
against.

**GStreamer does not report everything the taxonomy can express.** Verified on
the bench against 1.26: a bad RTSP path comes back as
`gst-resource-error-quark:13` with `SDP contains no streams`, not
`RESOURCE_NOT_FOUND`. Check what the element actually emits before adding a
mapping - a code that looks obvious from the enum may never be sent.

**`INTERFACE_DOWN` and `WRONG_INTERFACE` never come from the bus.** Only the
video input pin's preflight produces them, before anything is dialled, so
`classify_failure` has no rule for either. A refused build builds nothing, not
even the placeholder, and retries on the input's own backoff; the placeholder
appears only once the reconnects run out, as for any failure. `error_message`
carries the interfaces the sentence cannot name.

**`REFUSED` has no observed producer.** `rtspsrc` reports a refused connection
as `Failed to connect. (Generic error)`, the errno discarded inside GStreamer's
RTSP stack, confirmed against a socket probe raising `ECONNREFUSED` for the same
endpoint. `srtsrc` posts a real error once internal retry is off (below), but
reports every cause identically. The remaining inputs are a listener, a
discovery-by-name protocol and local devices, none of which can be refused. The
member ships unreachable and its text match is dead.

**The element must give up before our watchdog does**, or the pipeline is torn
down before it can say why and the failure is classified from the phase alone.
The defaults do not: `rtspsrc.tcp-timeout` is 20 s and `srtsrc.auto-reconnect`
retries forever. **The budget each is measured against is per plugin**, not one
number - RTSP allows 15 s and sets `tcp-timeout` to 10 s inside it; SRT allows
8 s and turns internal retry off, which is what puts an error on the bus at
all. **`rtspsrc.timeout` is deliberately left alone**: it is the live
UDP-to-TCP fallback trigger, armed for the whole session, so lowering it
downgrades a working feed to TCP-interleaved on any brief gap. `tcp-timeout` is
likewise not the connect deadline but the wait for each RTSP response, so it
stays generous enough for a busy NVR to answer DESCRIBE. **`udpsrc.timeout` is
left alone too**: the stall watchdog already covers socket silence and honours
the operator's `stall_timeout` including its `0 = off`, and a second reporter at
the same window only races it for the message. Read what a property does before
setting it - two of these three were misread first time.
`TestTheElementGivesUpFirst` keys on `source_element_name` and `SourceKind`, so
a networked plugin absent from its map fails rather than passing silently.

**SRT reports *that* it failed, never *why*.** With `auto-reconnect` off a real
error reaches the bus in ~3 s instead of nothing at all, but an unresponsive
listener, a wrong passphrase, a closed port and an unroutable host are all
`gst-resource-error-quark:9` with `Connection timeout (16)`. That is libsrt's
design at the caller - telling one from another for the passphrase case would be
an oracle - not a gap in the taxonomy. Do not add a mapping that pretends
otherwise.

**The Pi Camera's capsfilter pins `format=I420`.** Left open, libcamerasrc takes
the lowest-sorting fourcc it offers (raw Bayer or greyscale), not the camera's
YUV420 default: on a CM5 with libcamera 0.7.1 that negotiated a raw stream and
failed outright. Colorimetry is **not** pinned: a value the ISP adjusts can fail
negotiation. `v4l2.py` is a different mechanism (driver-preference order) and
is left open deliberately.
Cameras are listed through GStreamer's libcamera device provider (the
`gstreamer1.0-libcamera` package the pipeline needs), which shares
libcamerasrc's camera manager and so lists a camera that is already streaming.
`rpicam-apps` is not a dependency. A start that `set_state(PLAYING)` refuses
(no camera present) is classified from the element's bus error in both `play()`
branches, so a missing Pi Camera reads as not found, not unknown.

**Camera setup** (Video Source → Pi Camera) names the sensor and connector in
`config.txt` (a Compute Module never auto-detects its camera).
`privilege/camera_config.py` owns the parsing, the state read and the root CLI:
the `.deb` installs a copy of that **stdlib-only** file as
`/usr/share/openfollow/camera-setup`, run by `python3 -I`, so root never imports
from the venv (keep it stdlib-only). It rewrites an `# --- OpenFollow camera ---`
block (with `camera_auto_detect=0`) under `[all]`, comments out hand-written
camera lines, and loads/unloads the overlay live with `dtoverlay`. Markers that
don't pair up (`MalformedBlockError`) leave the file untouched and the setup
unavailable, since an unclosed block would claim every later line;
`/run/openfollow-camera` records what it loaded and the boot-time lines, so the
page can tell "running" from "takes effect after the next restart". A
boot-loaded overlay cannot be unloaded live. **Never unload under a streaming
pipeline**: the device vanishes under `libcamerasrc`, which then never reaches
NULL, and every later swap refuses. `apply_camera`'s `release` callback first
has the main loop stop the Pi Camera pipeline (`receiver.release_source()`, via
`WebCommandQueue.release_camera`); unanswered, the change waits for a restart
instead. `release_camera` drops a rebuild an earlier change left pending, or
the same housekeeping pass would restart the camera about to be unloaded. The block (`partials/camera_setup.tpl`, loaded into the Pi Camera
fragment) owns the **Camera** row: what libcamera detects plus what config.txt
names, with Raspberry Pi module names (`MODULE_NAMES`). It renders
`picam_camera_name` only when there is a choice (2+ cameras, or a saved one
that is gone), opens the setup by itself when no camera is found, and after a
live Apply re-checks after 3 s rather than reporting a success it has not seen.

### Placeholder pipeline vs source state
The "No Signal" placeholder is a black `videotestsrc` pinned at 1920x1080 @ 30 that feeds the **shared** sink, and both sink probes are attached once for that sink's lifetime – so its caps reach the same writer the real source uses. `ReceiverStateMachine.set_resolution` / `set_source_framerate` / `set_source_format` therefore refuse while `is_placeholder_pipeline`, mirroring `mark_frame_received`, and `_create_placeholder_pipeline` calls `clear_source_caps()` rather than writing its own geometry in. **Do not publish placeholder caps as source state**: `video.resolution` / `source_fps` are what the Statistics panel reports as the feed's own, and what `update_video` shapes the window from – a source that has never delivered a frame would otherwise present as a working 1080p feed and pin the window to 16:9 for the session.

`update_video` applies the aspect-ratio hint whenever the real resolution changes (tracked in `app._video_aspect`), **not** once per session: the HUD projects across the canvas while calibration is solved against the input, so a window left at a previous source's aspect ratio slides the overlay off the video with nothing in the UI to explain it. `app._video_logged` stays a one-shot latch for its log line only.

### Snapshot provider (`video/preview.py`)
`SnapshotProvider` captures on-demand full-resolution JPEG frames for the setup wizard. It connects to a `tee` → `queue` → `videoconvert` → `jpegenc` → `appsink` branch in the receiver pipeline (no downscale). The snapshot is pulled lazily – no background polling. The last captured frame is cached so repeated requests don't block on GStreamer. Wired via `full_snapshot_provider` callback on `ConfigWebServer`.
- `get_snapshot()` **serialises the blocking `try_pull_sample` → extract under a dedicated `_pull_lock`** so concurrent HTTP requests can't race on the single appsink. The `_valve` / `_jpeg_bytes` references are guarded by a separate short-lived `_state_lock` (also taken by `set_valve`), which is **not** held across the ~500 ms pull – so a source-swap / pipeline rebuild's `set_valve` never blocks on an in-flight encode. Keep the pull serialised, but don't move it back under `_state_lock`.

### HUD rendering (decoupled from buffer flow)
The HUD is **not** in the GStreamer chain. The video sink (`gtksink`) is wrapped in a `Gtk.Overlay`; a `Gtk.DrawingArea` is layered on top via `Gtk.Overlay.add_overlay()` with `set_overlay_pass_through(True)` so it doesn't intercept input.
- The DrawingArea's `draw` signal calls `CairoOverlayRenderer.draw(cr, w, h)`.
- A `Gtk.Widget.add_tick_callback` queues a redraw every display vsync (~60–120 Hz) – independent of source buffer rate, so a 0-fps NDI source no longer freezes the HUD.
- First-frame / caps detection moved to a downstream pad probe in the receiver (see above).
- `CairoOverlayRenderer.measured_fps()` reports HUD redraw rate (display tick), **not** source buffer rate. Use `receiver.source_framerate` (parsed from caps) for actual source FPS.
- **Don't re-couple the HUD to buffer flow** (e.g. by reintroducing `cairooverlay` or a videorate-driven redraw). See `feedback_hud_decoupled_from_buffer_flow.md` in auto-memory.

### Connection lifecycle
- `play()` → `_start_connection_timeout()` (uses plugin's `connection_timeout`)
- On error: `_schedule_reconnect()` with exponential backoff (plugin's `ReconnectPolicy`)
- `_is_placeholder_pipeline`: True when showing "No Signal" placeholder
- `STATE_CHANGED` check: sink name is `"shared_videosink"` (not `"videosink"`); connected status is set on first frame/caps, not on PLAYING alone

---

## PSN (`psn/`)

### Coordinate system
**X = stage left, Y = upstage (away from audience), Z = up**

### The one canonical frame

`marker.pos` is **PSN-absolute world coordinates** everywhere. Every touchpoint on the marker-position pipeline reads and writes that one frame – no site translates by `grid.{x,y}_offset`:

| Site | Frame |
|---|---|
| `psn/receiver.py` (`apply_remote` from incoming PSN packet) | PSN-absolute (raw) |
| `psn/server.py` (`marker.to_psn_marker()` outbound) | PSN-absolute (verbatim) |
| `input/mouse.py` (`unproject_to_plane` → `set_pos`) | PSN-absolute (direct) |
| `runtime/services_detection_pin.py` (pin target) | PSN-absolute (direct) |
| `services._collect_marker_positions` (zone-engine input) | PSN-absolute (verbatim) |
| `TriggerZoneConfig.vertices` (stored in config) | PSN-absolute |
| `runtime/overlay_draw_scene.draw_marker` (renderer) | PSN-absolute (no offset adjust) |
| `runtime/overlay_draw_zones.draw_zones` (renderer) | PSN-absolute |
| `scene/solver.unproject_to_plane` output / `project_points` input | PSN-absolute |

`grid.{x,y}_offset` is **display-positioning metadata** for the grid rectangle itself – used by the web setup wizard / `scene/solver.py` to build the four grid corners in world space, and by the web zone editor to centre its viewport on the grid origin. It is **never** added to or subtracted from `marker.pos`, a detection world-point, or a zone vertex.

**Do not add offset arithmetic to any marker-position flow.** The historical bug sites all had the shape "subtract offset on write, add offset on read" as a hidden convention that worked only in the zero-offset case. The regression suite in `tests/test_coordinate_system_invariants.py` parametrises nine invariants across three offset configurations (zero, positive, mixed-sign) – any future attempt to reintroduce an offset at one of these sites fails loudly across every non-zero case.

### Marker freshness (`is_marker_stale`, shared by every output)

`build_marker_visual_state` rewrites every **controlled** marker each frame (an unconditional `set_speed` carrying the marker's velocity estimate, `(0, 0, 0)` when it is at rest), and every `Marker` data write stamps `timestamp`. That stamp is therefore a per-marker "the frame loop ran" signal, which is what the four output protocols use to avoid transmitting a frozen position as if it were live – each runs its own send thread, so none of them stops when the frame loop does.

`marker_age_s(marker, *, now_us=None)` / `is_marker_stale(marker, *, now_us=None)` / `MARKER_STALE_AFTER_S` (1.0 s ≈ 60 missed frames) live in [`psn/marker.py`](openfollow/psn/marker.py). A remote marker (sender's epoch) and a never-written one both report `inf`, so anything unaged fails safe as stale – and `inf` is not `int()`-able, which the OTP sampled-timestamp path has to handle. The reference is the **marker's own clock** (`Marker.clock_now_us`), never the module function: `PsnServer.add_marker` passes its `clock=` down, so a server on an injected clock would otherwise age every marker against an unrelated epoch and ship `status=0.0` for a freshly written position. Pass one `now_us` (from that same clock) when ageing several markers for one packet.

| Output | What staleness does on the wire |
|---|---|
| PSN | `to_psn_marker(stale=True)` publishes `PSN_DATA_TRACKER_STATUS = 0.0` for that packet only. It does **not** mutate `Marker._status` – recovery must restore the real validity, and the detection-derived status (see "Tracker status" below) must not be clobbered |
| OTP | The Point Layer's `sampled_timestamp_us` is `timestamp_us - marker_age_us` (E1.59 §9.6: when the Producer read *that Point*), clamped at 0. A frozen point stops ageing while the Transform Layer's timestamp runs on |
| RTTrPM | No validity field exists, so absence is the idiom: stale trackables are filtered out of the packet, and an all-stale set sends nothing so the receiver's own timeout fires |
| OSC transmitter | A stale marker skips with the reason in that row's ring buffer. Explicit `[x:N]` / controller `[x:cN]` refs go through `RenderContext.marker_stale_resolver` (neither sets `needs_default_marker`), which raises `RenderError(..., hint="position is stale")` so the skip is distinguishable from an unregistered marker. Constant / hotkey / MIDI rows are unaffected |

**Do not gate the per-frame `set_speed` on movement.** The value may be zero; the write never skips. A deliberately still marker would otherwise go stale and drop off the wire on all four protocols. `tests/test_output_staleness.py` pins the wire behaviour; `tests/test_marker_freshness.py` pins the rule.

### Tracker status (`PSN_DATA_TRACKER_STATUS`)

`Marker.status` is the tracker's validity and is read only by PSN (OTP / RTTrPM / OSC have their own freshness idioms above). A marker under manual or assist control carries `1.0`: `_stamp_locked` promotes an untouched marker on its first data write, and an explicit `set_status` stands, **0.0 included** – `_status` is `None` until either happens, so an explicit invalid is never mistaken for "nothing written yet" and promoted back by the next per-frame `set_speed`. Only the Fully Automatic pin writes it, through `detection_status(score, threshold, age_s=, grace_s=)` in [`runtime/services_detection_pin.py`](openfollow/runtime/services_detection_pin.py): the configured confidence reads 0.5, a perfect score 1.0, a low-band recovery match continues the same line below 0.5; a coasting track (`DetectionBox.age_s`) decays linearly to 0.0 across `PersonDetector.coast_s`; nobody tracked, no feed, or a pin point that does not unproject is 0.0. **A box has one age**, `_coast_age_s`: seconds since its last match, less the detector's own step period, floored at 0 – so a box one step overdue is fresh and a match on every step never reads as coasting between steps. `_track` stamps it on a lost track and `tracked_detection` recomputes it live for every box it hands out, sticky or re-acquired, so a detector that stops stepping ages its last box too and a re-acquire from frozen results is not a fresh sighting. The step period (`_step_allowance_s`) is the longest of the last `_STEP_HISTORY` gaps, floored at `interval_ms` (only the pull timeout: inference time sets the cadence); each gap is **clamped to `_MAX_DT_REL` nominal steps** before it is recorded, because a pull timeout, an inference error or a backend rebuild never steps the tracker and the first step after one spans the whole outage. `coast_s` is how long a box stays handed out past its due step: the grace period, **never under one step period**, so a step longer than the recent ones (which always overruns an allowance that *is* the longest recent step) is overdue, not lost, and a zero grace does not release the pin on cadence jitter. The pin fades across that same window, so 0.0 lands exactly where the box stops being handed out, and `_track` retains a lost track for `allowance + coast_s`, the span it is handed out for, so the tracker never drops it mid-fade. Position and status land in **one** `Marker.set_pos(..., status=)` write: the sender reads both under one lock, so two writes would let a packet pair the new position with the previous frame's status. The value is raw per frame, no smoothing, and both the threshold (`PersonDetector.confidence_threshold`) and the window are the detector's, not the config's, because the worker drains config changes on its own cadence. `_release_status` restores 1.0 on every path that stops driving a marker, all of them through `_prune_pin_states`: detection off, another marker pinned, marker left `controlled_marker_ids`, and a `pin_mode` switch (`app._detection_pin_mode` remembers the mode the states were built under, and a change prunes them all before either mode runs, so the other mode's smoothing and status never reach its first frame, feed or no feed); plus the replace path when there is no detector or the detector never loaded a backend (`available` is False: it tracks nobody, ever, and the operator drives the marker). Both key on `DetectionPinState.status`, which also feeds `tracking.pin_status` in `/api/stats` (a wire interface) so support can set the pin's view beside what the wire carries. Staleness outranks the stored value on the wire. Help text lives in `web/help/psn.md`.

### Datagram size (`packet_chunking.MAX_DATAGRAM_BYTES`, shared by every output)

Every marker-carrying output grows its datagram with the marker count, and **1472 bytes** (a 1500 B Ethernet MTU less the 20 B IPv4 and 8 B UDP headers) is the ceiling for all of them. That is the *fragmentation* threshold, which bites before any protocol's own packet limit and degrades silently – a quiet bench LAN reassembles the fragments, a loaded show network drops one and loses the whole datagram. Budget against 1472, never 1500.

`chunk_to_datagrams(items, encoded_size, budget)` in [`packet_chunking.py`](openfollow/packet_chunking.py) is the one splitter: it measures through the caller's own encoder (so a protocol field added later moves the split instead of silently pushing the datagram over), keeps order, loses nothing, and emits an item that can never fit alone rather than dropping it or spinning. Each protocol then applies its own grouping mechanism:

| Output | How one marker set spans datagrams | Crossed at |
|---|---|---|
| PSN data / info | Packets of one frame: one `frame_id` and one header timestamp repeated across them, `frame_packet_count` = the chunk count. Build the header **once per frame** – a per-packet id reassembles nothing. Capped at 255 packets (`frame_packet_count` is a uint8) | 14 / 85 markers |
| OTP transform / name advertisement | Pages of one folio (E1.59 §6.7-6.9): shared Folio Number, `page` 0..`last_page`. Everything outside the split list repeats verbatim on every page, the Transform Layer's **Full Point Set** flag included – it describes the folio, so a page contradicting its siblings describes no coherent point set | 32 / 36 markers |
| RTTrPM | Independent packets, each with its own `pkt_id`. RTTrPM groups nothing across packets, so no reassembly is involved | 35 markers |

`PsnReceiver` needs no reassembly buffer: it reads 65535 and applies each packet's trackers independently, so a peer's split frame accumulates naturally. The `MAX_OTP_MESSAGE_OCTETS` (§6.3.1) and RTTrPM `_MAX_MODULES` / `_MAX_PACKET_BYTES` checks stay as structural backstops behind the split – they guard what the wire format can *express*, which is a different question from what the network can carry.

`tests/test_output_packet_splitting.py` pins the cross-protocol invariant (no stream emits a datagram past 1472 at any marker count) plus each protocol's grouping; `scripts/hw_validation/output_datagram_size_probe.py` re-checks it on the DUT against the deployed send paths.

### PsnServer (`psn/server.py`)
- Sends PSN multicast at `data_fps` (default 60 Hz)
- `add_marker(id, name)` / `remove_marker(id)` / `get_marker(id)`
- `marker.set_pos(x, y, z)`, `marker.set_speed(vx, vy, vz)`
- **Speed encoding:** every frame in `build_marker_visual_state`, each controlled marker's speed is its estimated **velocity vector** in m/s, PSN-absolute frame ([`runtime/marker_velocity.py`](openfollow/runtime/marker_velocity.py): position delta over the **real** elapsed seconds – `animate` hands `update_marker_visuals` the unclamped `elapsed`, not the clamped motion `dt`, because a clamped divisor inflates the rate of a slow frame – EMA-smoothed at alpha 0.3 per nominal frame, frame-rate independent via `ema_factor`). Two bounds, neither of which classifies motion: the sample rate is **clamped** to `_MAX_REPORTED_SPEED_MPS` (20 m/s), so a repositioning (reset, OSC snap) can't put an unbounded rate on the wire while a fast mouse drag still reports as moving; and the smoothed output snaps to exactly `(0, 0, 0)` below `_STILL_SPEED_MPS`, so a marker that comes to rest reads as stopped rather than asymptotically slow. The HUD card of a controlled marker shows the configured move speed (what R / T and the bumpers adjust), not this vector; viewer cards show `‖speed‖` of the received marker.

### PsnReceiver (`psn/receiver.py`)
- Receives PSN multicast in background thread
- `ignore_ids`: controlled_marker_ids – prevents loopback overwriting own markers
- `_last_seen[tid]`: monotonic timestamp of last received packet
- `_last_pos[tid]`: previous position for speed derivation
- `_wire_speed_sender[tid]`: the source address last seen publishing a non-zero speed for that tracker (dropped on TTL eviction). PSN has no server id, so several stations on one group can send the same tracker id – keying the trust by id alone would let a zero-only station's packets be stored verbatim because a different station earned the trust
- **Speed logic (per packet):**
  1. Any non-zero `t.speed` records this datagram's sender as the tracker's wire-speed source
  2. The packet carries a speed **and** comes from that sender: store the vector verbatim, zeros included (the sender says it is still)
  3. Otherwise – no speed chunk (it is optional, and holding the last one would freeze a stale vector forever), or a different sender – derive from `delta_pos/dt` inside the `0.001 < dt < 1.0` window, only when actually moving (preserves last known speed when stationary)
- **Received markers carry the sender's own `timestamp` / `status`.** Position, speed, and both fields land in one `Marker.apply_remote(...)` write, which never stamps the local clock – the wire values are the sender's, in *its* epoch, and are not comparable to `psn_timestamp_usec()` or to another sender's. Such a marker is built `remote=True` and reports `is_remote`; don't re-broadcast its timestamp as ours. Local freshness is `is_marker_online` (arrival), never the tracker timestamp
- `is_marker_online(tid, timeout=2.0)`: returns True if packet received within 2s
- `source_ip` parameter binds receive socket to a specific interface

---

## Overlay (`video/overlay.py`)

### Key OverlayState fields
```python
markers: list[MarkerOverlayData]   # all viewer markers
video_source_type: str               # "ndi" | "srt" | ...
video_connected: bool
source_label: str                    # human-readable source label (from plugin)
error_message: str                   # connection error text
source_selection_active: bool        # source selection overlay (plugin-driven)
source_selection_title: str          # e.g. "SELECT NDI SOURCE"
iface_selection_active: bool         # interface selection overlay active
settings_menu_active: bool           # Settings menu overlay active
settings_items: list[str]            # labels for the Settings menu rows
settings_items_enabled: list[bool]   # per-row enablement (matches settings_items)
settings_selected_index: int         # currently highlighted Settings row
button_labels: dict[str, str]        # action -> controller button (help overlay)
keyboard_labels: dict[str, str]      # action -> keyboard key (help overlay)
```
(This is the load-bearing subset – `OverlayState` carries many more fields for stats, Pi network, operator messages, virtual faders, detection boxes, and zone polygons.)

### MarkerOverlayData
```python
marker_id: int
x, y, z: float          # PSN coords
color: str              # hex from the marker catalog
radius: float           # ball_size from config
speed: float | None     # scalar m/s; None → shows as 0.00
online: bool            # green dot = online, off-white crossed disc = offline
name: str               # marker label
controller_idx: int | None    # bound gamepad slot, or None
controller_connected: bool    # is that pad currently plugged in
is_controlled: bool           # in controlled_marker_ids (vs viewer-only)
marker_fader: float | None    # per-marker fader value, or None
```

### HUD layout
- **Top-left:** the help panel (`key_toggle_help`), or its key hint while closed. While any menu screen is open, the menus' one key list instead (`build_help_sections(mode="menus")`), the same on every screen: `CairoOverlayRenderer.draw` draws it after `_draw_menu_screen`, never a screen itself, and no screen names its own keys. The Button Detection Wizard is not a menu here (every pad button is the input it records) and names only `Esc` itself
- **Top-right:** system stats (CPU, mem, temp, FPS)
- **Bottom-left:** combined info panel (`IP Address:` + `Video Source:`)
- **Bottom-center:** controller connection status
- **Right side:** marker cards (one per viewer marker)

### Marker card (180×64px)
- **Top-right mark:** online status: a green dot (radius 4px) when online, an off-white disc with a cut-out cross when offline
- **y+18:** Marker ID ("T0"), yellow if selected
- **y+34:** Coordinates (x, y, z in metres)
- **y+47:** Speed text "X.XX m/s"
- **y+50:** Speed bar (color from `_speed_color`, max reference 20 m/s)

---

## Input handling (`input/`)

### Keyboard (`input/keyboard.py`)
**macOS:** Quartz `CGEventSourceKeyState` – hardware polling, reliable under GStreamer load.
**Linux/fallback:** GTK event-based tracking.

#### macOS key codes (`_MAC_KEY_CODES`)
| Key | Hex |
|---|---|
| W/A/S/D | 0x0D/0x00/0x01/0x02 |
| X | 0x07 |
| C | 0x08 |
| R | 0x0F |
| T | 0x11 |
| N | 0x2D |
| I | 0x22 |
| Enter | 0x24 |
| Escape | 0x35 |
| Arrow Up/Down/Left/Right | 0x7E/0x7D/0x7B/0x7C |

#### Discrete vs continuous keys (config-driven)
Discrete keys (edge-detected, one event per press) come from two sources: a fixed set of modal-overlay keys `_MODAL_DISCRETE_KEYS` (`Tab`, `Enter`, `Escape`, the arrows, `b`) **plus** the operator-bound action keys named in `_DISCRETE_ACTION_FIELDS`, whose values are read from `ControllerConfig` (`key_reset`, `key_toggle_help`, `key_toggle_zones`, `key_speed_down`, `key_speed_up`, `key_next_marker`, `key_prev_marker`). The polled set is rebuilt from config – there are no hardcoded `n`/`c`/`i` action keys.

Movement keys (`key_move_layout`, default WASD) are continuous-polled (held = repeated).

### Key actions – normal mode (default bindings)
Every binding is a `ControllerConfig` field; the defaults are shown. There is no on-device calibration mode – calibration is the web `/wizard`.

| Key | Config field | Action |
|---|---|---|
| W/A/S/D | `key_move_layout` (`wasd`) | Move selected marker |
| Q / E | `key_move_z_up` / `key_move_z_down` | Raise / lower marker Z |
| X | `key_reset` | Reset selected marker to default position |
| R / T | `key_speed_down` / `key_speed_up` | Decrease / increase move speed |
| H | `key_toggle_help` | Toggle help overlay |
| Z | `key_toggle_zones` | Toggle trigger-zone overlay |
| Tab | `key_next_marker` | Select next marker |
| M | `key_settings` | Open the Settings menu (source / network / button detection / web UI / restart / about) |
| N | NDI plugin `hotkey_label` | NDI source selection (NDI source only) |
| Esc | modal | Close the active overlay |

### Speed adjustment (`adjust_move_speed`)
- **Marker:** base step 0.1 m/s (≤4.0), 0.5 m/s (>4.0), clamped to configurable `min_speed`–`max_speed` (defaults 0.1–3.0)
- **Streak acceleration:** ×1 (0–4 presses), ×3 (5–9), ×8 (10+), resets after 0.75s
- Both keyboard `key_speed_up`/`key_speed_down` and controller LB/RB call the same method

### Mouse (`input/mouse.py`)
Left-click on a marker's **ground circle** grabs that marker (hit-tested against the projected ground-circle polygon, with a pixel-radius fallback when the circle is off/tiny; nearest centre wins on overlap); right-click releases. A grab seeds the glide at the marker's current position – it never yanks the marker to the click – and a click on empty stage is a no-op. While held, pointer moves record a target (after the `mouse_hysteresis_px` pixel deadband, and rejecting targets past the `mouse_max_y` upstage cap); the marker is steered by `MouseHandler.update()`, called every frame from `InputManager.update`, which EMA-glides it toward the target at `mouse_smoothing` (0 = instant, higher = smoother; glide alpha = `1 - mouse_smoothing`, floored so the max never freezes). Positions are unprojected onto the stage floor plane (via `scene/solver.unproject_to_plane`) in the one PSN-absolute frame. The ground-circle ring geometry is shared with the overlay via `scene/solver.ground_circle_world_ring`. Scroll wheel adjusts Z when `mouse_wheel_z_enabled`, by `mouse_wheel_z_step` m per tick, sign flipped by `mouse_wheel_invert`. One tick is one wheel click, and on both platforms the event counts, not its size: libinput sends one event per click whose magnitude is an arbitrary per-device scale, and quartz sizes a click by how fast the wheel turned (scroll acceleration: ~13 slow, ~103 fast), so each quartz event is at least one click and only a fast flick's batched event, a multiple of `_SCROLL_UNIT_QUARTZ`, counts as several. Reading the quartz magnitude as distance makes the same click move Z eightfold more when turned fast. `MouseHandler._take_wheel_clicks` then rate-limits in whole clicks (`_WHEEL_BURST_CLICKS` at once, `_WHEEL_CLICKS_PER_S` after that) and drops the excess rather than queueing it: a free-spinning wheel (SmartShift) reports hundreds of clicks for one flick, which would otherwise send a marker tens of metres up. A trackpad sends many small events per swipe, each counted as a click, so it is not a wheel-Z input. Double **right-clicking** (two right-clicks within `_DOUBLE_CLICK_S`/`_DOUBLE_CLICK_PX`, detected in-handler via the injectable `_clock`) resets the selected marker to `_get_default_marker_position()` and **releases control** when `mouse_double_click_reset` is set. It lives on the right button because that's the release button, and a reset releases too (staying grabbed would snap the absolute marker straight back to the cursor on the next move). Left-click stays a pure grab.

**Event source per platform.** `MouseHandler`'s `on_pointer_*` entry points are fed by the GTK pointer signal handlers in [`window.py`](openfollow/window.py) (`_on_button_press`/`_on_motion`/`_on_scroll`). GTK doesn't reliably deliver pointer events to the gtksink-hosted window on **macOS** under the GStreamer pipeline (the same reason the keyboard polls Quartz instead of reading GTK key events), so on macOS the window also runs `poll_pointer()` once per frame from `_on_tick`: it reads `gdk_window.get_device_position()` (window-relative position + a button mask) and synthesises the same `pointer_down`/`pointer_move` events through `_emit`, so everything downstream is unchanged. Edge detection lives in the pure `_poll_pointer_events` helper. The scroll **wheel can't be polled** (no current-scroll-position API), so wheel-Z rides the GTK `scroll-event`, which *does* arrive on macOS – measured under both SDL video drivers. It is **button** events that never arrive there, and motion that arrives intermittently; that is what the poll covers. The poll is a no-op on Linux/Pi, where the GTK events work.

### Gamepad (`input/gamepad.py`)
Gamepad input runs on **pygame-ce** (it installs the `pygame` module). The classic `pygame` distribution installs the same module, so the two must not share an environment: `GamepadHandler` logs a WARNING at startup when classic pygame is what loaded, and the diagnostics bundle names the loaded build and its SDL version. 3Dconnexion pucks are kept out of SDL's joystick API with `SDL_JOYSTICK_BLACKLIST_DEVICES`, built from `_SPACEMOUSE_IDS` (the 3D Mouse subsystem drives them; SDL would bind one as a gamepad). The list holds exact VID/PID pairs because SDL's blacklist has no ranges, and it never blocks Logitech's `0x046d` wholesale, since Logitech's own gamepads share that vendor ID. An explicit value in the environment wins. `SDL_NO_SIGNAL_HANDLERS=1` keeps SDL from installing its own SIGINT/SIGTERM handler at `pygame.init()`, which would swallow a stop until the GLib main loop takes the signals over. The blacklist needs SDL 2.30 or later, so `_open_device` also skips any index `_is_spacemouse` reports (vendor and product read with `SDL_JoystickGetDeviceVendor` / `Product` through pygame's own SDL, without opening the device): the rule holds even when a stray classic pygame loads an SDL that ignores the blacklist. All bindings are `ControllerConfig` fields; defaults shown.
- Left stick (`move_xy_stick`, default `left`): move selected marker
- LB / RB (`btn_speed_down` / `btn_speed_up`): adjust move speed
- LT / RT (`btn_move_z_down` / `btn_move_z_up`): lower / raise marker Z
- `btn_reset` (default `X`): reset marker to default position
- `btn_settings` (default `BACK`): Settings menu
- `btn_toggle_help` (default `Y`), `btn_toggle_zones` (default `B`), `btn_next/prev_marker` (DPAD)

**Hotplug touches one device.** Every per-device map (`joysticks`, `controllers`, `capabilities`, edge state, trigger baselines, stick priming) is keyed by the SDL **instance id**, which is stable while the device stays attached. A `JOYDEVICEADDED` / `JOYDEVICEREMOVED` makes `_apply_hotplug` match the open set to SDL's current device list by instance id: an id SDL no longer lists is closed, a listed id not yet open is opened, and a device that stayed attached is never closed, reopened or reset. The event's own index is not trusted, SDL's announcement of the devices attached at startup is a no-op, and the game-controller duplicates of those events are consumed, not acted on. The ids come from `_device_instance_id` (`SDL_JoystickGetDeviceInstanceID`, looked up through pygame's own joystick module so it resolves in pygame's SDL rather than OpenCV's copy); a test pins that the lookup resolves. A full `_detect_controllers` rescan runs only for the first enumeration, the no-controllers retry, and a hotplug when that lookup is unavailable; it keeps the state of every device that reopens, prunes the rest, and publishes the new maps in one swap so the web and OSC threads never read a partial set. Never call `pygame.joystick.Joystick(i)` for a device that is already open: pygame opens it again inside SDL before returning its cached object, so each such call leaks an SDL reference. A failed open releases only what it created, never an object already registered.

### Gamepad → marker routing (`InputManager._gamepad_marker_id`)
Gamepad movement, reset, speed readout, and controller-info all go through
`_gamepad_marker_id(controller_idx)` so every routing surface stays
consistent. The routing surface is also passed to `GamepadHandler` as a
`marker_resolver` callback so the bumper-speed and effective-speed paths
get the same marker_id without reaching back into the InputManager. Two
modes:

- **Single-gamepad mode** – predicate is *exactly one controller slot*
  (a gamepad or a 3D mouse, counted together; a missing slot counts) *AND*
  `app._selected_id is not None`. Route to
  `app._selected_id`. DPAD next/prev cycles `_selected_id` – same
  contract as the keyboard.
- **Multi-gamepad mode** – the fallback: triggered when the
  single-gamepad predicate fails, i.e. **2+ slots** *or* **one slot with
  no selection**. Fixed slot mapping `app._controlled_ids[unified_idx]`
  (derived from `controlled_marker_ids`), so each physical controller keeps
  its own marker regardless of the shared `app._selected_id`. The selection route
  keeps `controlled_marker_ids` in its order and appends a newly controlled
  marker, so taking control of one never moves another pad's marker. `unified_idx`
  is the controller's position in `_controller_slots()`, see "Controller
  slots" below. **DPAD next/prev
  is disabled in this mode** – pressing it is a no-op at flag-set time
  (the edge state in `_button_prev` still advances normally so a later
  single-pad disconnect leaves no stuck carryover, and the OSC trigger
  bus's independent `_button_bus_prev` keeps its own dispatch
  unaffected). The help overlay also hides the `next_marker` /
  `prev_marker` labels in this mode.

Don't reintroduce direct `app._controlled_ids[controller_idx]` indexing in
a new consumer (`controller_idx` is the gamepad handler's key, the SDL
instance id, not a slot) – route through `_gamepad_marker_id` (or the injected
`marker_resolver` inside `GamepadHandler`) so HUD, speed, movement, and
reset stay in sync.

### Controller slots (`input/controller_slots.py`, `input/controller_identity.py`)
Which slot a controller holds comes from the **USB socket** it is plugged
into, never the order it was probed in. `controller_identity.resolve_key`
walks a device node (`/dev/input/event*` from `SDL_JoystickPathForIndex`, a
puck's `/dev/hidraw*`) through sysfs to the USB device: the key is
`usb:<host controller path>:<devpath>`, and `devpath` excludes the bus
number, so a renumbered bus and a controller's USB 2 / USB 3 root hubs keep
one socket's key. Bluetooth keys by address; anything else is keyless.

`ControllerSlotTable` is the session table, owned by `InputManager` and
refreshed once per frame (`_refresh_slots`, after gamepad hotplug, before
routing); other threads read its immutable `slots` tuple. It is seeded in
port order until the 3D mouse's first scan settles (`initial_scan_settled`,
or 3 s), then frozen:
- a departed controller leaves its slot **missing**, keeping its marker, so
  nobody behind it moves and OSC `cN` keeps sending;
- an arrival reclaims a missing / reserved slot of its kind with its key,
  else takes the lowest one of its kind (any kind when there is exactly one
  slot), else appends;
- **Forget** (web) makes a missing slot **reserved**: it keeps its place,
  drives no marker, raises no warning;
- switching gamepads or the 3D mouse off leaves that kind's slots missing,
  alarms included, and nobody else moves; switched back on, each device
  reclaims its own slot. A kind that is off at startup holds no slots.

Nothing persists: every start numbers by socket again. 3D mice carry
session-unique instance ids (like SDL's), so a puck keeps its id while a
sibling comes and goes. `identify_slot` rumbles a pad (main loop) or blinks a
puck's LED (its read thread, scheduled between reads) and flashes the slot's
marker card. A puck's LED is lit while its handler has it open, switched off
when OpenFollow lets go, and left alone when the puck vanished, so the blink
ends lit; `get_controller_info` carries each slot's state, kind, port
label and time since last use for the web Controller Slots table.

Each slot also carries `notes`, what its controller can't do, as stable ids
published in `/api/stats` (a wire interface): `buttons_unrecognised` (raw
joystick backend, no Button Detection Map), `button_map_other_model` (the saved
map's identity doesn't match) and `cannot_identify` (nothing Identify can pulse:
a pad SDL can't rumble, read once at open with `SDL_JoystickHasRumble`, or a
puck without an LED). Notes follow the controller while connected, never count
as a slot-table change, stay on a missing slot and are cleared by `forget()`.

### 3D Mouse status (`input/mouse3d_status.py`)
`Mouse3DManager.status()` reports what the supervisor already holds and never enumerates or imports: the backend is `ok`, `not_installed`, or `could_not_start` (enumeration raised; easyhid without libhidapi binds the interpreter and raises `AttributeError`), and every attached puck is `open`, `opening`, `no_profile`, `not_permitted` or `open_failed`. Unprofiled pucks are enumerated and reported but never opened. hidapi drops the errno of a refused open, so `_PySpaceMouseBackend.open` checks `os.access` on the node and raises `PermissionError`. `/api/stats` publishes the block as `mouse3d` (its values are a wire interface), the bundle renders it as E10, and the web section shows only faults: its poll answers 204 while `status_key` is unchanged, so a `role="alert"` box is inserted once per change. **macOS is unsupported by platform** (`_platform_supported`): the real backend never starts there and the section shows one Info line; an injected backend bypasses the gate, as it bypasses the dependency check. Lift the gate with the pyspacemouse release whose `open_by_path` accepts macOS hidapi paths.

### One input, one action (`binding_conflicts.py`)
Within a binding group no two actions share an input: the gamepad's normal-mode actions (held Z and speed included), its menu Confirm / Cancel, the keyboard's action keys, and the 3D Mouse's buttons. Menu buttons are only read while a menu is open, so the two gamepad groups may share a button; devices never conflict. The groups are the `*_FORM_LABELS` constants in `configuration.py`, in **form order**, and `tests/test_web_binding_forms.py` pins the templates to it, because form order decides who keeps a shared input: `settle_bindings` (run by `__post_init__`) leaves it on the upper field and unbinds the rest with a WARNING. `apply_section_data` passes the fields a write changed as `prefer`, so a save or API write keeps what it changed. Move X/Y has no unbound value and always keeps its stick; a marker-fader stick on it is unbound. Menu Confirm / Cancel never take D-Pad Up / Down, which move the highlight.

On the form the validate route decides (`web/bindings.check_binding`) and `static/js/binding-steal.js` only applies its `bindingChecked` event: the field that lost its input empties and gets `data-binding-lost` (red, never `aria-invalid`, so Save stays open). An OSC hotkey / controller-button trigger on an action's input is a caution, not a conflict (`osc_trigger_overlap`): it can be deliberate.

### Speed per marker (`AppConfig.marker_move_speeds`)
Move speed is stored **per marker** in a `dict[int, float]` keyed by
`marker_id` (default empty). The global `MarkerConfig.move_speed` is the
fallback used when a marker has no override. All callers read via
`OpenFollowApp.get_marker_move_speed(marker_id)` so the storage stays a
single source of truth.

- **Keyboard R/T** writes to `app._selected_id`'s entry (or no-ops when
  no marker is selected).
- **Gamepad LB/RB** writes to the marker currently routed to the
  pressing pad – resolved via the `marker_resolver` callback inside
  `GamepadHandler` so single-pad uses `_selected_id` and multi-pad uses
  the fixed slot mapping.
- **Streak acceleration** is global to the session (one counter per
  `app`) so one operator's tap-streak isn't reset by another operator
  nudging a different marker on a different pad.

`save_config` stringifies keys (TOML requires string keys) and prunes
entries whose marker is no longer in `controlled_marker_ids` so the file
stays tidy after the operator removes a marker via the web UI. In-memory
entries are not pruned at runtime (a brief live-reload remove-and-re-add
keeps the operator's per-marker speed).

### Marker card → controller binding
Each `MarkerOverlayData` carries `controller_idx`, `controller_connected`,
and `is_controlled`, populated by reverse-mapping
`InputManager.get_controller_info()`. The marker card renders a small
top-left badge "C1" / "C2" / … (the unified index plus one: operator-facing
numbering starts at 1, a 0 never appears in the UI) mirroring the status dot
top-right. A card whose controller is missing turns red with the badge
"C1 missing", and the top-right status badge carries one row per missing
controller; Identify flashes the card. Connected pads
with no marker (more pads than `controlled_marker_ids`) surface in the
Settings menu's info card under "Unbound controllers". Assignment stays
**implicit**: the unified slot order (see the routing section above) × the
`controlled_marker_ids` list (edited via the web UI).

Viewer-only markers (in `viewer_marker_ids` but NOT in
`controlled_marker_ids`) render at reduced alpha (≈0.6 via a Cairo group
wrap) and skip the speed bar – the bar is a control-context affordance.
Marker-card borders use each marker's own colour from the shared marker
catalog (`MarkerCatalog.get(marker_id).color` via
`services_marker_visuals._resolve_marker_color`, with a
`DEFAULT_MARKER_COLORS[marker_id % len(...)]` palette fallback for the
transient race where a controlled id has no catalog entry yet) instead
of the global golden accent, so each card is identifiable at a glance.

---

## Web UI (`web/`)

### UI copy: explanations live in the help drawer (REQUIRED)

**Do not add inline help to web forms.** A `<label>` (plus a `placeholder` where
one genuinely helps) is the whole of a control's inline copy. Everything else –
what the setting does, when to use it, defaults, side-effects, caveats, examples,
and "manage X under Y" pointers – goes in that section's **help drawer markdown**
(`openfollow/web/help/<section>.md`, surfaced by the per-section `?` drawer via
`data-help="<section>"`).

- **A new `field-note` is a review blocker**, including a terse one-line pointer.
  "For a camera looking from upstage", "Comma-separated; e.g. …", and
  "Manage destinations under OSC Destinations." have all been stripped on sight.
  If the label alone doesn't identify the control, fix the label.
- When you add or change a control, update the matching
  `openfollow/web/help/<section>.md` (and keep the website-docs mirror in mind –
  see the `Check help on merge` auto-memory). The help drawer is the single home
  for the explanation, so it can't drift between the form and the docs.
- The same goes for `title` tooltips carrying explanation. A `title` is fine for
  a badge or icon with no visible label (e.g. the "This session" interface badge).
- Existing `field-note`s predate this rule – leave them where they are unless you
  are already editing that block, then move the text into the help `.md`.
- `section-note` (the one-line subtitle beside a section's `<h2>`) is a *name*,
  not help: "Marker speed limits, default speed, and default position" is fine;
  a sentence teaching behaviour is not.

### Status & warning language (REQUIRED)

Every state surface on the web UI and the HUD (boxes, chips, pills, dots, table
rows, borders, flashes, inline text, the toast, HUD rows and cards) uses the four
levels, tokens and components in [`docs/STATUS_LANGUAGE.md`](docs/STATUS_LANGUAGE.md).
A new element picks an existing level and component and uses the `:root` tokens:
no literal colours, alphas, radii or icons in templates, partials or scripts. If
nothing fits, extend that document in the same change. A one-off red is a review
blocker. `tests/test_status_language.py` pins the `:root` tokens to the document's
table and fails a status rule that carries a literal colour.

### Key routes
| Route | Method | Description |
|---|---|---|
| `/` | GET | Main config page |
| `/section/overview` | GET | Station list partial (HTMX-polled every 5s) |
| `/section/<name>` | GET | Config section partial |
| `/section/movement` | POST | Save movement settings (speed limits + default position) |
| `/section/general` | POST | Save + apply general settings |
| `/video-input/ndi/sources` | GET | NDI source `<option>` list (served by the NDI plugin's `web_routes()`) |
| `/network/interfaces/by_name` | GET | Interface `<option>` list (iface-keyed) |
| `/section/network/status` | GET | The interface card, every row read-only |
| `/section/network/edit/<iface>` | GET | Same card with that one interface's row editable |
| `/section/network/apply` | POST | Validate + write IPv4 config via the privileged adapter |
| `/section/network/renew` | POST | Renew DHCP lease via the privileged adapter |
| `/section/general/startup` | GET/POST | Start-at-boot switch; reads `systemctl is-enabled`, writes via the `service.enable` / `service.disable` grants |
| `/api/info` | GET | JSON: system_name, ip, port |
| `/api/peers` | GET | JSON: discovered peers |
| `/api/config/export` | GET | Download full config as JSON file |
| `/api/config/import` | POST | Import config JSON (preserves device IP); supports `?confirm_restart=1` and `?skip_restart=1` |
| `/api/config/reset` | POST | Restore every setting to `AppConfig()` defaults, keeping the device-identity fields; restarts |
| `/api/config/<section>` | GET/POST | JSON config API |
| `/api/config/<section>/broadcast` | POST | Push config to all peers |
| `/wizard` | GET | Setup wizard (camera positioning + grid calibration) |
| `/api/video/snapshot/full` | GET | Full-resolution JPEG snapshot (503 if no feed) |
| `/api/wizard/project` | POST | Project grid corners + ref point to screen coords |
| `/api/wizard/unproject` | POST | Unproject screen points to world plane, return delta |
| `/api/wizard/solve` | POST | Run DLT solve from 4 corner screen positions |

### Config transfer (export / import / restore defaults)
- **Export:** `GET /api/config/export` returns the full config as a downloadable JSON file. Device-local fields are stripped from the payload by `_config_dict_redacted`: `web_pin` (login secret), `detection.storage_path` (an absolute path that only makes sense on the exporting host), the interface pins that name this box's NICs (`otp_output.source_iface`, `rttrpm_output.source_iface`, `osc.listen_iface`, each OSC destination's `source_iface`), and `interface_labels`, which name its adapters. Stream credentials (`rtsp_user` / `rtsp_password` / `srt_passphrase`) deliberately **stay**: a station PIN is that station's own login, while a camera password is shared by every station pointed at the same camera, so stripping it would break the fleet-provisioning workflow export and broadcast exist for. They are redacted in the diagnostics bundle instead, which is the artefact operators attach to public issue reports.
- **Import:** `POST /api/config/import` applies imported JSON, then writes the device-identity snapshot back (captured before the section apply, restored after, in `_apply_import_data`). Section-level peer broadcast / `/api/config/<section>` go through `strip_device_local_fields`, which drops the same per-section set (`_DEVICE_LOCAL_FIELDS_BY_SECTION`), and so does every section the import applies. `interface_assignment` is dropped whole (`_DEVICE_LOCAL_SECTIONS`): its per-destination keys are not known in advance. OSC destinations are rebuilt wholesale from the file, so their pins are carried over by destination id; a destination new to this station starts blank. A storage path from another machine must never land here – it would be unwritable and break model storage / export.
- **Restore defaults:** `POST /api/config/reset` writes `reset_config_to_defaults(current)` – a fresh `AppConfig()` carrying the same device-identity snapshot – through `save_config`, then calls `request_restart()`. **The restart is part of the operation.** A section save only writes fields `apply_runtime_config_changes` applies, so it finishes live; a whole-config reset also touches fields it does not – `network.backend` is startup-only by design, the update / time-sync group is read off `app._config` and never reassigned, and `marker_move_speeds` is deliberately runtime-authoritative. Left running, those keep their pre-reset values in memory and the next wholesale `save_config(app._config)` (the zone-overlay hotkey is one) writes them back over the reset, so it would silently, partially undo itself. `psn_system_name` follows the preserved `station_id` via `derive_station_name`, the way a first run seeds it: resetting it to the bare `"OpenFollow"` default would let the next restart's `_bootstrap_station_identity` silently rename the station. The marker catalog (`markers.toml`), the media gallery and the model store are separate files and are untouched.
- **The device-identity set is one list, `_DEVICE_IDENTITY_FIELDS`**, shared by import and restore-defaults via `capture_device_identity` / `restore_device_identity` (one dot addresses a sub-config). It holds what identifies or connects *this box* rather than describes the show: `psn_source_iface`, `web_pin`, `web_port`, `web_bind`, `web_bind_iface`, `station_fqdn`, `station_id`, `markers_catalog_path`, `testpattern_selected_media`, `detection.storage_path`. A default or foreign value in any of them locks the operator out of the only interface an offline show LAN has, moves the station off its interface, makes two stations claim one name, re-mints its identity, or points a path at a directory that does not exist here. `otp_output.source_iface`, `rttrpm_output.source_iface`, `osc.listen_iface`, `video_input_iface` and the OSC destination pins are deliberately **not** in the set: blank means "follow `psn_source_iface`", which is preserved, so resetting them drops nothing off the network. `web_bind_iface` is the opposite case: blank means every interface, so a reset would unpin the web UI.
- Import uses a two-phase flow when restart-requiring changes are detected: the first request analyses without saving, then the user confirms one of three actions:
  - **Restart Now** (`?confirm_restart=1`): saves full config, hot-reload triggers restart
  - **Apply Without Restart** (`?skip_restart=1`): saves only live-reloadable changes (skips video source, OTP, RTTrPM, detection)
  - **Cancel**: nothing is saved
- Routes are registered before the wildcard `/api/config/<section>` routes to avoid interception

### HTMX notes
- HTMX version: 1.9 – use `hx-on::after-request` (double colon), NOT `hx-on:htmx:after-request`
- Overview auto-refresh: `hx-trigger="every 5s"`
- Restart detection: polls `/section/general` every 2s; on success → `window.location.reload()`

### Setup wizard (`/wizard`)
Guided workflow for camera positioning and grid calibration, seven steps, eight with the experimental **Lens** step:
1. **Preparation** – info + SVG stage layout illustration
2. **Grid Setup** – width, depth, z_offset, spacing, x_offset, y_offset; dynamic SVG illustration updates from input
3. **Video Source** – select and configure camera input (reuses video source UI); save & restart to activate
4. **Lens** (only with `ui.show_experimental_features`) – measure `lens_k1` / `lens_k2` from lines that are straight in reality (see "Lens distortion from straight lines" below); skipping keeps the current pair
5. **Camera Position** – pos_x/y/z, pitch/yaw/roll, fov; dynamic isometric illustration
6. **Reference Mapping** – draggable crosshair for coarse calibration (single known point); rigid-body shift of all corners
7. **Corner Pinning** – 4 draggable corners, DLT solve, solved camera params displayed
8. **Review & Apply** – read-only summary of all values + green overlay; Apply or Discard

Key implementation details:
- **Steps are keyed, never numbered, in script.** The template builds the step list (`_steps`, the Lens step only with the toggle) and publishes it as `window.WIZ` (key → index) and `WIZ_STEPS`; step content divs are `#wizard-step-<key>`, nav buttons call `wizardNext()` / `wizardPrev()`, and the session stores `_stepKey`. A step inserted or left out shifts nothing
- Server-side projection/unprojection via `/api/wizard/project` and `/api/wizard/unproject` to avoid JS↔Python coordinate math mismatches
- SVG viewBox matches native image resolution so coordinates map 1:1 regardless of CSS scaling
- `sessionStorage` persists wizard state across accidental navigation/refresh
- Touch-friendly: 44px hit areas, `touch-action: none`
- Keyboard arrow support for draggable points (debounced at 300ms)
- DLT-solved values back-propagate to form fields when navigating back
- Finish uses JSON API (`/api/config/camera`, `/api/config/grid`) to avoid resetting unrelated bool fields; `applyAndFinish()` must check each response's `.ok` before clearing session + redirecting (silent-drop regression guard in `tests/test_wizard.py`)
- **Input validation:** `/api/wizard/{project,unproject,solve}` must return 400 – not 500 – on malformed bodies. All float coercion and shape checks (including numpy array construction from caller-supplied coords) belong *inside* the try/except. Shared `_wizard_camera_params()` helper validates the 7-field camera vector
- **Step nav semantics:** `<nav aria-label="Setup wizard steps">` landmark with `aria-current="step"`, **not** `role=tablist`/`role=tab`. The earlier tablist markup was an incomplete ARIA tab pattern (missing `aria-selected`/`aria-controls`/`role=tabpanel`); for a linear wizard, nav + aria-current is the correct lighter-weight semantics
- Snapshot blob URLs: `loadSnapshot()` must revoke the previous `URL.createObjectURL` after the new image loads to avoid a per-refresh memory leak
- "No feed" UX: each preview container has a sibling `.wizard-no-feed` placeholder div; `setPreviewVisibility()` toggles both so the snapshot steps never render blank when `/api/video/snapshot/full` fails

### Lens distortion from straight lines (`scene/lens_fit.py`, `scene/edge_snap.py`, `lens_model.py`)

The overlay correction is `f = 1 + k1·r² + k2·r⁴` about the image centre, `r` normalised to the half-diagonal (`r = 1` at a corner); `project_points` / `unproject_to_plane` stay pinhole, `apply_overlay_distortion` bows what the HUD draws and `invert_overlay_distortion` undistorts clicks, detection pins and zone feet. **The one validity rule is the fold check**, `lens_model.lens_warp_is_valid` (stdlib-only, because `configuration.py` reads it): `r·f(r)` must keep growing on `[0, 1]`, i.e. `1 + 3·k1·s + 5·k2·s² > 0` for `s ∈ [0, 1]`, closed form in `lens_fold_radius`. There is no box: a 100° lens on 16:9 sits near `(-0.47, 0.25)`. `CameraConfig.__post_init__` falls a folding pair back to `0 / 0` with a WARNING, the Camera form's blur check reads the sibling field through `validate(..., context=)` and refuses the pair, `_wizard_lens_coeffs` falls back to pinhole, and the fit never leaves the valid region. The inverse is a **bracketed Newton solve** (`invert_normalised_radius`, broadcasting radii against a grid of pairs) exact over the whole valid range; a distorted radius the warp never reaches lands on the fold radius. The fixed-point iteration it replaced was tens of pixels off at a corner for pairs inside the old box. `wizard.tpl` mirrors the fold check and the inverse (`wizLensFoldRadius`, `wizInvertRadius`) for its bowed edges and live curves.

**The fit** (`fit_lens_from_lines`) is the plumb-line method: each traced line's active points are undistorted and measured against their own total-least-squares line; the cost is the sum of squared perpendicular residuals. A coarse grid over both coefficients (vectorised through `_Lines.grid_cost`, closed-form smallest eigenvalue of each line's scatter) seeds a damped Gauss-Newton refinement, because a start at zero diverges on strong barrel distortion. **k2 is only fitted when its predicted sigma is below `K2_SIGMA_GATE`** (0.05, needs lines near the corners), else it stays `0` and the k1-only solution is reported. The rating is the predicted uncertainty of the warp at the frame corner in px, from the full 2×2 covariance with the residual sigma floored at 1 px: `RATING_THRESHOLDS_PX` (2.5 / 5 / 10 / 20 → excellent / good / okay / medium, else low) are fixed from simulation and pinned in `tests/test_lens_fit.py`; the hint names the emptiest edge band, or the missing corner line. A line **does not fit the others** when, with k1 refitted without it (k2 held, so two remaining lines cannot be overfitted), its RMS exceeds 3 px, three times the others' RMS, and three times the displacement the refit's own k1 uncertainty predicts at that line (`_k1_variance`, the residual sigma floored at 1 px); the last term is what keeps the one line carrying the curvature from being convicted by the lines that cannot pin k1 down without it. Needs three lines. Zero coefficients reproduce the pinhole path exactly.

**Edge snap** (`snap_line_to_edges`) runs on a luma band the browser cuts along the traced chord (`lensBand()`: one canvas `drawImage` with a rotating transform, so the server never needs the JPEG decoded and the snapshot the operator sees is the one measured): one column per `band_step` pixels of the chord, one row per pixel across it, `band_half_size` rows to either side, both mirrored by the wizard and pinned by a test. A straight edge seen through a lens bows by far more than any search around the chord's own points could cover (the stage edge in a wide shot sat 60 px off its chord), and with a strong lens the bow is not even one arc: the r^4 term pushes the ends of a line near the frame edge out while the r^2 term pulls its middle in. So the band is searched for the **highest-scoring smooth path** through all its columns (dynamic programming over the signed luma gradient, each column clipped at `_GRADIENT_CLIP` so one bright crossing cannot carry it, at most `_PATH_SLOPE` rows of climb per chord pixel and `_PATH_BEND_PENALTY` per row so it runs straight across a gap, both ends within `end_search_radius` of the clicks), the path is smoothed by the shape a straight line can take through a radial lens (the bows of the r^2 and r^4 terms, drawn with `_REFERENCE_K1` / `_REFERENCE_K2`, plus a shift of either end, fitted by least squares weighted by the path's evidence; the references are pincushion because a barrel reference folds before the corners), and each point then takes the gradient peak within `_POINT_WINDOW` of the curve. **All five points take one polarity** (the sign whose path scores higher), otherwise gaffa tape's width reads as curvature; a point without a peak above `_MIN_EDGE_STRENGTH` stays on the curve, is reported `snapped=False`, and the wizard starts a middle point like that switched off. Cells outside the snapshot carry no evidence (masked from the geometry, since a canvas paints nothing there).

**Routes** `POST /api/wizard/lens/snap` (endpoints + five base64 luma patches → five points with their marks) and `POST /api/wizard/lens/fit` (lines of active points → k1, k2, `k2_fitted`, per-line RMS / misfit / predicted curve, rating, hint) both answer 400 on malformed bodies like the other wizard routes. **In the wizard**, lines live in the session (`_lensLines`) as endpoints plus middle points in the line's frame `(t, off)`, so an endpoint move keeps every middle point's perpendicular offset and a middle point only moves across the line; the fit runs debounced after every release; refreshing the snapshot keeps the lines unless the resolution changed. The sliders under the result are **Fine-tune** (they moved there from Corner Pinning). A solve records its pins (`notePinnedCorners`); a lens change solves again from them (`solveFromPinnedCorners`), and Review shows the caution "Lens changed – redo Corner Pinning" only when there are no pins to solve from. The five-segment coverage meter is the status language's graded meter (`docs/STATUS_LANGUAGE.md`).

### Discovery (`web/discovery.py`)
- UDP multicast `239.255.50.50:50505` (`BEACON_MCAST_GROUP` / `BEACON_PORT`), JSON beacon every `BEACON_INTERVAL` = 2s
- `iface_ip` used for both `IP_MULTICAST_IF` (send) and `IP_ADD_MEMBERSHIP` (receive)
- Self-filtering: `iface_ip` explicitly added to `_local_ips` to prevent self-listing
- **Beacon validation** (`BeaconPacket.from_bytes`): `web_port` must be an `int` in `[1, 65535]` (bool rejected – it's an `int` subclass); `name` / `version` strings are capped (`BEACON_NAME_MAX_LEN`, `BEACON_VERSION_MAX_LEN`) and non-printable characters are stripped. Malformed datagrams are silently dropped – a crafted beacon cannot stuff unbounded strings into the peer list or redirect broadcasts to privileged ports.

### Peer authentication (`web/peer_auth.py`)
Peer-to-peer config broadcast is HMAC-signed. The web PIN is the HMAC key; the PIN itself never leaves the host.

- **Signature headers:** `X-Auth-Signature` (hex HMAC-SHA256) + `X-Auth-Timestamp` (unix seconds).
- **Signed payload:** `method\npath\nSHA256(body)\ntimestamp` – path includes the query string when present, so `?skip_restart=1` is semantically bound to the signed operation.
- **Timestamp window:** `TIMESTAMP_WINDOW_SECONDS = 30` – tolerant of typical NTP-free LAN skew, too narrow for offline replay to be practical.
- **Pre-auth body cap:** `_check_auth` requires `Content-Length` and rejects requests over `peer_auth.MAX_SIGNED_BODY_SIZE` (1 MiB) with 413 before reading – the verifier has to hash the full body before the HMAC proves the sender, so an unauthenticated client could otherwise force an unbounded spool.
- **Hard cutover:** the legacy `X-Auth-Pin` header is no longer accepted. Third-party scripts must either authenticate via cookie (browser flow) or sign with `peer_auth.sign()`.
- **Host binding not included:** OpenFollow only performs broadcast-to-all operations and restricts targets to private IPs (`_is_private_peer_ip`), so cross-peer replay produces only the same effect a legitimate broadcast would have.
- **Offline brute-force note:** a captured signed request lets an attacker test candidate PINs offline. The HMAC construction only hides the PIN from the wire – it does not make a short / low-entropy PIN strong. Operators wanting meaningful resistance should use a longer shared secret; a future KDF derivation would raise the cost further.

### Browser auth (`_check_auth` + `login_submit`)
- `web_pin` non-empty → every non-asset route requires either a valid HMAC signature (peer path above) or the `_openfollow_auth` cookie.
- Cookie is set with `httponly=True`, `path="/"`, **`samesite="strict"`**. Strict blocks the browser from attaching the cookie to any cross-site request, which defeats CSRF without a separate token layer. OpenFollow is LAN-only so the reduced cross-site ergonomics are acceptable.
- `/login`, `/assets/*` and the About pages are exempt from auth. The login page shows no station state.

### Refused names and failed saves (`_check_auth`, `static/js/save-feedback.js`)
- **A change through a name the station does not accept** (an `Origin` / `Referer` host outside `_allowed_request_hosts()`, the CSRF / DNS-rebind defence) is answered by `_refused` in the shape its caller reads: JSON `{error, action, href}` for HTMX and script requests, and a page for a plain form post (`_is_navigation`: `Sec-Fetch-Mode: navigate`, else `Accept: text/html` without `HX-Request`) – the login page with the reason where "Incorrect PIN" sits, or `refused.tpl`. Never Bottle's bare 403 page.
- **The address offered** is the local address the connection arrived on (`SERVER_ADDR`, recorded by `_QuietHandler.get_environ`): the browser just reached it, and no DNS lookup is involved. Loopback (a proxy on the box) offers none.
- **On page load**, every full page (index, wizard, login, about) spreads `_page_host_context()`, so a page opened through such a name shows the red banner before anything is edited. The `Host` header decides it; the refusal stays the backstop, because a reverse proxy can make `Host` and `Origin` disagree.
- **Every save reports failure the same way** (`window.OpenFollow.saveError`): the form's ring flashes red for 0.55 s like the green save flash, and a red line under its actions says what the station observed plus one next step, until the next edit or save. Non-GET HTMX requests are covered globally (`htmx:responseError` / `htmx:sendError`); a script-driven save calls `saveError.show(box, info, lead, near)`. A dialog that closed before its request failed hands the line to the section last clicked. Don't add toasts, alerts or modals for a failed save; the offline update upload keeps the updater's own dialog, and the save to a USB storage device ends its dialog on the export's result (a refused start still reports through `saveError`). `tests/test_save_feedback.py` fails a template or input plugin that saves by script without the helper.

### Login throttle (`web/login_throttle.py`)
Per-IP exponential-backoff lockout on PIN authentication. Without this, a 4-digit PIN is exhausted in seconds over a LAN – no rate limit, no attempt counter, no back-off.

- **Curve:** 1 s after the first failure, doubling each subsequent failure (1, 2, 4, 8, 16, 30 …), capped at `_MAX_LOCKOUT_S = 30 s`. The exponent saturates at 60 to keep `2 ** n` inside float range under sustained probing – without this an attacker could drive `failures` past 1024 over ~8.5 hours and turn brute-force traffic into server 500s via `OverflowError`.
- **Two vectors, one throttle:** `setup_routes` builds a single `LoginThrottle` instance shared by `POST /login` (form path) and the peer-auth signature verifier in `_check_auth`. An attacker can't sidestep the lockout by alternating between vectors. The lockout check runs **before** any HMAC compute on the peer path, so a flood of bogus signatures can't keep the verifier hashing.
- **Reset on success:** a correct PIN check (`record_success`) clears that IP's history. Idle entries (no failure within `_RESET_AFTER_S = 10 min`) get GC'd by both per-IP cleanup in `remaining_lockout` and a periodic full sweep in `record_failure` (amortised O(1) per call) – required because an attacker rotating through one-off IPs would otherwise grow `_entries` without bound.
- **`_is_stale` requires both conditions:** an entry is collectable only when the idle threshold has passed *and* `lockout_until` has expired. Defends against a misconfigured `reset_after_s < max_lockout_s` where the per-IP GC could otherwise drop an entry mid-lockout, letting an attacker reset their failure count by waiting one idle window.
- **Wire response:** locked-out callers get `429 + Retry-After: <seconds>`. The header is computed via `math.ceil(remaining)` so it never overshoots the cap (a previous `int(remaining) + 1` advertised `31` on a `30 s` cap). `bottle.abort()` discards thread-local response headers, so the 429 is raised as `HTTPResponse(headers={"Retry-After": …})` directly – not via `abort()`.
- **Threading:** every public method takes the instance lock, and `now = self._clock()` is read **inside** the critical section so a contended caller can't compute `lockout_until = now + delay` against a stale timestamp.
- **Per-IP, not per-account:** acceptable because there is only one shared PIN. Legitimate users behind the same NAT share lockout – a known trade-off on a LAN show network. No persistence across server restarts; matches the LAN threat model.
- **Coverage:** 100% line + branch in `tests/test_login_throttle.py` (mock-clock unit tests, including thread-safety stress) + integration tests in `tests/test_web_server.py` (live server, real `urllib`, both vectors). Module is in the `mypy --strict` batch.

### Credential redaction (`uri_redaction.py`)
Stream URLs carry credentials inline (RTSP userinfo, the SRT `?passphrase=`), so [`openfollow/uri_redaction.py`](openfollow/uri_redaction.py) is the one home for stripping them. It sits outside both the video and web packages because both reach for it, and it depends on nothing but the stdlib.

- `redact_uri(uri)` – a whole URI, for labels and our own log formatting. `strip_uri_userinfo` / `strip_uri_query_key` hand a *bare* URI to an element that authenticates from explicit properties instead. Both operate on the query **textually**: a round trip through `urlencode` would percent-escape an SRT `?streamid=r=0,m=request` purely because an unrelated field was filled in, and would render the `***` mask as `%2A%2A%2A`.
- `redact_uris_in_text(text)` – for free text where a URI is one token among many. It matches `scheme://userinfo@` rather than extracting a whole URI, so two URIs adjacent on one line are each redacted instead of being swallowed as a single run.

Three surfaces consume it, and **a new one that displays, logs, or exports a stream URL must too**:

- **The status marker** ([`video/connection_status.py`](openfollow/video/connection_status.py)) redacts in `set_disconnected` / `set_reconnecting`, the two writers that take free text. They are handed GStreamer's raw error/debug string, which for an `rtspsrc` auth failure carries the full `location`; `error_message` is rendered on the projected HUD, served from `/api/stats`, and shown **verbatim** in the Video panel's failure banner, so redacting at the writer is what makes rendering it safe. A reader that redacted at the render site instead would publish the camera password on the next surface someone added.
- **The diagnostics config dump** – `redact_config_secrets` collapses `_SECRET_CONFIG_KEYS` (`web_pin`, `rtsp_user`, `rtsp_password`, `srt_passphrase`) to `"***"` / `"(empty)"`, whether a login is *set* being the whole useful content, and runs `_URI_CONFIG_KEYS` (`rtsp_url`, `srt_host`) through `redact_uri`. A value in an unparsed shape **fails closed** to `"***"`. Add a new credential or URL-valued field to the matching set.
- **`redact_log_line`** is the single redactor for log content: the bundle's log tail, its failure extract, and worker-thread tracebacks. **Do not add a log path that bypasses it.**

### Broadcast target restriction
`_send_config_to_peer` / `_send_config_import_to_peer` refuse to POST to non-private IPs – `ipaddress.IPv4Address.is_private` covers RFC 1918, link-local, and loopback, which is the set a legitimate peer can plausibly have. Closes the SSRF vector where a crafted beacon advertised an attacker-chosen endpoint (e.g. `web_port=22` or an internal service port).

### Software update flow
The only in-app updater is the **signed-`.deb` GitHub-release installer** ([`runtime/deb_update.py`](openfollow/runtime/deb_update.py)), driven by the `/section/general/deb-update*` + `/section/general/deb-upload` routes. *Check & Install Latest* queries the GitHub Releases API for `update_github_repo`, downloads the matching `openfollow_<version>_<arch>.ofupdate` bundle, verifies its signature (against the on-device public key) and the inner `.deb`'s SHA-256, then installs as root via the privilege broker and restarts `update_service_name`. *Offline install* takes an operator-supplied `.ofupdate` over the LAN through the same verify-then-install path. **A failed install restarts the station too**: the package's prerm stopped and disabled both units, so `apply-update.sh` records `failed`, finishes what dpkg left half-done and enables and starts them again. The starting app keeps that `failed` as its update status rather than `idle`, which the updater dialog (the outgoing release's script) takes for success. The legacy `git pull` + `poetry install` updater (and its `update_source_url` / `update_repo_branch` / `update_allowed_hosts` config + host-allowlist) has been removed entirely – see [`docs/PACKAGING.md`](docs/PACKAGING.md) ("Removed: in-app git updater").

The **background online-sync worker** ([`runtime/online_sync.py`](openfollow/runtime/online_sync.py)) is the only *non-click* trigger: on startup and on IP change it runs `check_for_update` and, when a newer release exists, stashes the version in `WebCommandQueue.set_update_available`. That drives the read-only **update banner** in the General → Software Update section plus the **footer flag** (`(Update available: vX)`) in `base.tpl`; both render on page load only (no extra poll). The banner's "Install now" button reuses the existing `Check & Install Latest` confirm-and-install flow. The same worker also runs the NTP clock-sync first (see the online-sync paragraph under the offline contract). `update_include_prereleases` gates pre-release builds for *both* the auto-check and the manual installer.

**What's new** ([`web/whats_new.py`](openfollow/web/whats_new.py)) is the updater's last step. `WebCommandQueue` reads the installer's state file before clearing it at startup: `running` / `restarting` means that install started this version, so every authenticated full page (via `_page_update_context`) opens the step until someone closes it, which records the version in `/var/lib/openfollow/whats-new-seen`. The *new* version detects it rather than the updater dialog, because the page that ran the update still runs the old release's script. The seen record is load-bearing: `apply-update.sh` can write `restarting` after the new version already cleared the file, so the next start finds the same install again. The notes are `web/whatsnew/whatsnew.md`, first line the version they describe (`v0.4.4`), images under `web/static/whatsnew/`. A first line that doesn't match the installed release (compared on the release segment, so a candidate build shows its release's notes) falls back to a generic "Updated to vX" pointing at openfollow.app/docs, so a package still carrying the previous release's file never shows it.

**Support OpenFollow** (`partials/support_card.tpl`) is the request for contributions: docked beside Continue in What's new (`footerHTML`, so long notes scroll without hiding it), on the About page, and as the third QR column on the Operator Screen's Settings (`SUPPORT` in [`runtime/overlay_links.py`](openfollow/runtime/overlay_links.py); the web draws the same rows via `link_qr_svg`). Its link and QR go to `https://openfollow.app/support-openfollow`, the website's stable redirect, **never** to the payment page: a station keeps this release's address for as long as it runs it, so only the website may move. For the same reason the text makes no claim that can go stale. Its edge is the website's dashed gold (`--support-border` / `COLOR_SUPPORT_BORDER`, `#ffbc00` at 60%); dashed, on a neutral fill and led by a heart, it never takes a caution box's solid border, fill or sign (see `docs/STATUS_LANGUAGE.md`).

**Settings backup** ([`privilege/settings_backup.py`](openfollow/privilege/settings_backup.py)): the `.deb` `preinst` archives `config.toml`, the marker catalog and `templates/user/` into `/var/lib/openfollow/backups/<station>-v<old>-<UTC>.ofbackup` on every `upgrade` (in-app, offline or hand-run `apt`), keeping the ten newest. `render-preinst.sh` inlines the module into `preinst.in` because nothing from the new package is on disk yet, so keep it **stdlib-only** and free of the heredoc terminator line. It reads the sources **as the service user**, never root: `config.toml` names the catalog path, and a root reader would copy any file into an archive that user owns. A failed backup **never fails the install** (a full disk evicts the oldest archives, never the newest existing one); the outcome goes to `backups/last-backup.json`, which What's new (matched to the installed version) and diagnostics E5c read. The archives hold secrets: never export, broadcast or quote their contents.

### Removable drives (`runtime/removable_media.py`)
The diagnostics bundle (and later settings / templates) can be saved to the top level of a USB drive or card reader, from Diagnostics → Bundle & tools and from Settings → **Export Diagnostics File for Support** on the Operator Screen.

- **Listing** (`list_media`): `lsblk` on Linux, `diskutil` on macOS (an APFS stick's partition is only its container's store, so its volumes are read from the container); every removable partition with a label an operator recognises, writable or not, the reason when not. **Never the station's own disks**: a disk carrying `/`, `/boot/firmware` or `/mnt/nvme` is left out whatever its bus – the boot SD reports `hotplug: true`, so hotplug alone is not "removable". `list_media` (the pickers) logs a failed listing and lists nothing; `scan_media` raises `MediaError` instead, and the diagnostics USB table reads that one (`media_scan_provider`), so a failed listing reads as not checked, never as no device.
- **Writing** (`write_file`) enumerates afresh and accepts only an id from that list, never a path. A drive something else mounted (desktop automount, macOS `/Volumes`) is written directly, without overwriting, synced and left mounted – only what the station mounted does it unmount. One nothing mounted goes through the `media.write` grant: `/usr/share/openfollow/write-to-media write <device>`, a stdlib-only copy of `privilege/media_writer.py` run by `python3 -I` (**keep it stdlib-only**), name + bytes on stdin (`<name>\n<bytes>`), never prompting (`broker.run(..., stdin=bytes, allow_prompt=False)` – bytes are refused on a prompting run, since the password shares the pipe). The helper re-checks the device (USB, not a system disk, unmounted, FAT32 / exFAT / NTFS via `ntfs3` / ext4), the name rule it shares with the app (safe charset + `.txt` / `.ofsettings` / `.oftemplate`), a 32 MB cap, mounts `nosuid,nodev,noexec` under `/run/openfollow-media`, never overwrites (`-1`, `-2`), and unmounts on every path. One exit code + one sentence per failure; `WriteResult` carries the result and the next step separately.
- **The export** (`runtime/diagnostics_export.py`) holds **one slot across HUD and web**: both start it on its worker (`start`, busy → `False`) – the ~20 s collection must never run on the GTK main loop or a web request. The HUD reads `status()` each frame; the web dialog (`openfollowSaveToDrive` in `base.tpl`, over `GET /api/diagnostics/drives` and `GET`/`POST /api/diagnostics/export`) polls it, asking for its own export by number (`?generation=N`, answered from `last_done(WEB)`) because a newer export may already hold the slot. Both word it with `status_lines`, so the two screens say the same thing. `build_diagnostics_bundle(server, cfg)` in `web/routes.py` is the one builder the download, the drive save and the HUD share.
- **HUD** (`runtime/app_modes_media.py`): the picker lists drives through `MediaWatch` (a worker re-listing ~1 Hz while open), the highlight follows the drive id, rows that can't be written are skipped. A HUD export the operator left before it finished posts to the status corner (`_status_flags["diagnostics_export"]`): success green, cleared after 15 s; failure red until the export is reopened (where its reason shows) or a later export succeeds. Web exports never post there.

### OSC input allowlist
`OscConfig.allowed_sender_ips` drives a `verify_request` filter in `_FilteredOSCUDPServer`:
- Empty allowlist = allow-all + loud WARNING at `start()` (preserves legacy behaviour on upgrade).
- Non-empty allowlist = drop packets from non-listed IPs (DEBUG-level drop log, rate-limit-friendly).
- `__post_init__` on `OscConfig` normalises the field to `list[str]` so a hand-edited TOML can't feed a bare string or non-string entries into the runtime filter / web UI / HMAC flow.
- `_as_ip_list` parser **fails closed**: a submission of `"192.168.1.999"` (all-invalid) preserves the existing value rather than becoming `[]` and silently disabling the filter. Explicit empty (`""`, `[]`, whitespace-only CSV) still clears.

### Argument injection defence
`_is_valid_branch_name` / `_is_valid_service_name` reject any value starting with `-` in addition to the existing regex + `..` guards; `run_update` passes `--` option terminators before `repo_url`, `branch`, and `service_name` into `git remote set-url`, `git pull`, and `systemctl restart`.

### IP enumeration
`get_local_ipv4_addresses()` in `net_utils.py` uses `psutil.net_if_addrs()` – complete enumeration including all adapters.

---

## Camera calibration (web `/wizard`)

Calibration is **web-only** – there is no on-device calibration overlay or calibration key mode. The setup wizard's Corner-Pinning step drives a 4-corner DLT solve:

- 4-corner quad: DSL (downstage-left), DSR, USR, USL (in PSN coords)
- `scene/solver.solve_camera_dlt()` solves the camera params from the four screen↔world corner correspondences; the wizard back-propagates the solved values into the camera form.
- Server-side projection / unprojection (`/api/wizard/project`, `/unproject`, `/solve`) keeps the JS and Python coordinate math in sync.

---

## Person Detection (`video/detection.py`)

Optional YOLO-based person detection that can auto-pin a marker to a detected person.

The inference backend is **ONNX Runtime** (`openfollow[detection]`).

Default tuning:
- `model = "yolo26n.onnx"` (the Fastest quality tier; pre-shipped on every distribution)
- `inference_size = 640` (auto-detected from the model's export)
- `interval_ms = 67`
- CLAHE preprocessing is always on (no config field)

### Architecture
- `PersonDetector` runs inference in a background thread, pulling frames from a GStreamer `appsink` (tee branch)
- Inference runs through `_OnnxBackend` in `detection.py`
- Pure NumPy post-processing handles **both YOLO head layouts** (branched on output column count in `_OnnxBackend.predict`): YOLOv8 / YOLO11 (`[cx, cy, w, h, <class scores>]` → cx,cy,w,h→xyxy + NMS) and the NMS-free end-to-end head used by YOLO26 / YOLOv10 (`[x1, y1, x2, y2, conf, class]` → xyxy as-is, person-class filter, **no** second NMS). Mixing these up is what produced full-frame boxes for YOLO26
- **Inference size auto-detects from the model**: `_OnnxBackend.model_input_size` reads the ONNX input shape and `_load_backend` adopts it into `_inference_size` (driving the appsink caps via `input_resolution`), so the operator needn't match `inference_size` to the export. A dynamic-axis model reports `None` → the configured `inference_size` stands
- CLAHE preprocessing is always applied (`PersonDetector._preprocess`) – no config toggle
- Detection frequency controlled by `interval_ms` (default 67ms ≈ 15 FPS)
- Results stored as `list[DetectionBox]` (normalised 0–1 coordinates), swapped atomically via GIL
- **Detection masks (region-of-interest):** `filter_detections_to_masks(boxes, cfg.masks, masks_enabled=cfg.masks_enabled)` runs in `_run_inner` **between** `backend.predict()` and `_track()`, so out-of-mask detections never spawn tracklets. Gated on the `masks_enabled` master switch (default off ⇒ the whole frame is detected even when masks are drawn). Masks are normalised 0–1 frame polygons (`DetectionMaskConfig`); a box passes when its bottom-centre (feet) lies inside the union of enabled masks. Empty/all-disabled = unrestricted. Edited via the web `detection_mask_editor.tpl` canvas (drawn on `/api/video/snapshot/full`) through the `/api/detection/masks` CRUD routes; the master switch persists via `/api/detection/masks/enabled`; live-applied via the detector's staged-config drain (no restart)

### ByteTrack tracking (`video/tracking.py`)
- `ByteTracker` binds detections to tracklets via a two-stage IoU association on top of a per-track constant-velocity Kalman filter (`_KalmanFilter`), all pure NumPy (no SciPy / external tracker dependency – keeps the offline-runtime contract)
- **`confidence` is the high threshold**: detections at/above it drive the first association; detections in `[LOW_DETECTION_THRESHOLD, confidence)` drive a second (recovery) pass over **every** unmatched track – including ones already lost – so a performer who dims into shadow (their score dropping with the light), even across several frames, stays bound to their existing track instead of being dropped and re-numbered. The strict `_LOW_IOU_GATE` against the Kalman prediction is what keeps a noisy low box from resurrecting the wrong track. Low-only detections never spawn a new track
- Each tracked person gets a stable `track_id` (incrementing int); the Kalman filter predicts a lost track forward so its reported box (and any pinned marker) glides along the trajectory through a brief occlusion rather than freezing at the last seen box
- **Time-aware motion model:** `ByteTracker.update` takes a `dt` (elapsed time ÷ nominal `interval_ms`, computed in `_track`, clamped `[_MIN_DT_REL, _MAX_DT_REL]`); the Kalman `predict` scales both the centre extrapolation and the process noise by it, so a dropped frame / jittery cadence propagates the box the right amount instead of assuming a fixed step. `dt=1.0` (steady cadence, and the default for direct `ByteTracker` use) reproduces the fixed-step behaviour exactly
- **Distance rescue (first association only):** a high detection whose centre lies within `_DIST_RESCUE_FACTOR` predicted-box **widths** (width = the horizontal axis stage motion happens along, so the reach stays tight, not the half-frame a height-dominated diagonal would give) is admitted even when IoU is below `_HIGH_IOU_GATE`, so a fast mover whose predicted box stops overlapping its detection stays bound (not dropped + re-numbered). Pairs rank by `(IoU, -centre_distance)`: overlapping pairs always win, and among the IoU-0 rescues the **nearest** detection wins, so a missed frame can't snap a track onto an arbitrary farther person. The low (recovery) stage stays IoU-strict
- `PersonDetector._track` splits the raw detections (pulled down to the low floor in `_run_inner`), runs the tracker, then publishes `self._results` (tracks matched this frame, highest-confidence first, capped at `max_persons`) plus `self._tracked` (every live tracklet, lost ones included while inside their coast window) for `tracked_detection`
- `tracked_detection` property returns the currently pinned person (sticky-by-`track_id` while the track lives); on pin expiry / re-numbering it re-acquires the detection nearest the last-followed box centre (`_REACQUIRE_MAX_CENTER_DIST`, normalised) so a re-numbered performer keeps being followed, falling back to the largest visible detection only on a true cold start or when no candidate is within the gate
- Grace period (`grace_period_ms`): the coast window after the step that misses a track, never under one step period (`PersonDetector.coast_s`). The tracker retains a lost track for the step period plus that window – exactly as long as `tracked_detection` hands out its predicted box – and removes it after; the retention is derived from the same two numbers, never from `grace_period_ms` alone, or the fade is cut short by a step

### Marker pinning (`services.py: apply_detection_pin`)
- Runs every frame at 60 FPS in `_animate()` loop. Gated on `detection.enabled` alone (no `pin_marker` bool); off ⇒ prune all ghosts + pin states and return
- Per-marker smoothing state lives in `app._detection_pin_states: dict[int, DetectionPinState]` (lazy via `_get_pin_state`, pruned by `_prune_pin_states(keep=...)`). `replace` mode uses a 1-entry dict for the resolved marker; `assist` uses one entry per controlled marker
- `pin_point`: `"top"` pins to head (top-center of box, unprojects at marker Z), `"bottom"` pins to feet (bottom-center, unprojects at grid `z_offset` = stage floor)
- Velocity estimation: EMA-smoothed (alpha=0.3) from target position deltas, normalised to a per-nominal-frame rate
- Prediction: `predicted = target + velocity × prediction` – lookahead to compensate for detection lag on fast-moving persons
- EMA smoothing applied **after** prediction: `smooth += alpha × (predicted - smooth)` – smooths the final output including lookahead
- **Frame-rate-independent:** the filter is tuned for the ~60 FPS animate tick but `apply_detection_pin` receives the frame `dt`; the velocity rate and both EMA factors are re-derived for the real `dt` (`dt_steps` / `ema_factor` in [`runtime/frame_timing.py`](openfollow/runtime/frame_timing.py), shared with the assist glide and the broadcast velocity estimate), so `smoothing` / `prediction` behave the same on a Mac at 60 FPS and a Pi running animate slower, and across stalls. `dt = NOMINAL_FRAME_DT` reproduces the per-frame tuning exactly
- `smoothing`: 0.01–1.0 (lower = smoother/laggier, higher = more responsive)
- `prediction`: multiplier on velocity vector (0 = disabled, ~2–5 typical range)
- `replace` mode also writes the PSN tracker status of the marker it drives (see "Tracker status" under PSN); `assist` leaves every marker at `1.0`

### Tracking modes (`pin_mode`) – Fully Automatic vs AI Assisted
The web Tracking control is the only on/off: **Off** ⇒ `enabled=False`; **Fully Automatic** ⇒ `enabled=True, pin_mode="replace"`; **AI Assisted** ⇒ `enabled=True, pin_mode="assist"`.

`replace` (Fully Automatic) overwrites a single resolved marker (`pin_marker_id`) with the tracked detection.

`assist` (AI Assisted) refines **every** controlled marker simultaneously. Each marker splits into **two entities** (see `_apply_assist_all` / `_assist_one` in `runtime/services_detection_pin.py`):
- **Manual anchor** – operator-steered, freely movable, rendered as the **solid carded marker** (the operator-facing one) at the anchor position. Stored per-marker-id in `app._assist_manual: dict[int, Marker]` (a real `Marker`, **never** registered with `PsnServer` → never broadcast, never in zones). Operator input reaches it because the input resolvers (`InputManager._get_marker`, `mouse.MouseHandler._get_selected_marker`) redirect to it when `is_assist_controlled(app, marker_id)` (assist active AND id in `controlled_marker_ids`). The pin **never writes the anchor**.
- **AI-corrected output** – the existing registered/controlled marker (broadcast + zones, unchanged plumbing), rendered as the **dim ghost** crosshair + ground ring (`MarkerOverlayData.is_assist_ghost`, no card) – one ghost per assist-controlled marker. Each frame it **glides** (single EMA at `smoothing`, seeded once, never reset → never snaps) toward the detection nearest its anchor within `assist_radius_m` (eased by `assist_strength`, 1.0 = exactly on the person), or back toward the anchor when none is in range. Output Z follows the anchor's Z. The same detection may drive more than one marker (no claim dedup; collisions self-resolve as anchors diverge).
- Per-frame efficiency: `_apply_assist_all` unprojects each detection **once per unproject plane** and memoises by plane Z, so N markers don't re-unproject M detections N times.
- `DetectionPinState.ai_smooth_x/y` is the never-reset outer glide; `soft_release()` (lost detection) drops the lock + velocity but keeps the glide. `assist_active` / `is_assist_controlled` / `get_or_create_manual_marker` are the single source of truth for "which ids are assist-controlled" and lazy anchor seeding; `_prune_manual_markers(keep=set())` / `_prune_pin_states(keep=set())` discard stale ghosts + states when the controlled set changes or assist disengages. Every per-marker state map (pin states, assist anchors, velocity estimates) shares one lazy-create / prune pair – `get_or_create` + `prune_to_keep` in [`runtime/state_maps.py`](openfollow/runtime/state_maps.py) – so a new one doesn't grow a fourth copy of the same three lines on the 60 Hz path.

### Pre-shipped detection models
The five YOLO26 sizes ship as quality tiers (`_DETECTION_TIERS` in `web/routes.py`: n/s/m/l/x → Fastest/Fast/Balanced/Accurate/Most Accurate). They are built into each distribution and seeded into the storage `models/` folder on first run via `openfollow/model_seed.py` (`seed_bundled_models` + `bundled_models_dir`, called from `AppRuntimeServices._seed_bundled_detection_models` in `init_video`). macOS bundles all five (the launcher's `seed_user_data` copies them); the `.deb` ships n/s/m to `/usr/share/openfollow/models` (Large/XLarge are Advanced downloads on a Pi); the Pi image installs the `.deb` so the startup seed copies them onto the NVMe. Build-time export of the `.onnx` files needs the `export` extra + an uplink (build host only; runtime stays offline).

### Pipeline integration
- Detector created in `AppRuntimeServices.init_video()` when `detection.enabled = true`
- `GstNativeSinkReceiver` builds a `tee` → `appsink` branch when detector is provided
- Detection branch caps are driven by `PersonDetector.input_resolution` (from `inference_size`, 4:3 pre-scale)
- Enabling/disabling detection requires pipeline restart; other detection settings are live

### Optional-dependency handling
- `cv2` is imported inside a `try/except` in `video/detection.py`; if missing, the module still imports and `check_detection_dependencies()` returns `["opencv-python"]`
- `services.init_video()` calls `check_detection_dependencies()` before constructing `PersonDetector`; when deps are missing it logs a warning and leaves the detector as `None` (no crash loop)
- The web UI detection section renders a red banner listing missing packages. `routes._get_detection_missing_deps()` probes at render time and is passed as `detection_missing` to `partials/detection.tpl` from the `index`, `get_section("detection")`, `update_detection`, and install/uninstall routes
- `check_detection_dependencies(cfg.detection)` reports the missing pip packages (`opencv-python` / `onnxruntime`) so the banner names them; the `config` argument is accepted but does not change the result
- `publish_runtime_stats` caches the probe via `_resolve_detection_missing_deps` (TTL = 5s, keyed by `repr(detection_cfg)`) so the 4Hz stats loop doesn't call `find_spec` every tick; the cache invalidates on config change or install/uninstall action
- The "Person Detection" stats panel (renamed from "Tracking") shows an `Unavailable` chip + banner when `tracking_missing` is non-empty AND detection is enabled; a disabled-but-missing state shows `Off` without the alert
- `onnxruntime` is imported lazily inside `_OnnxBackend`; its absence is logged there, not surfaced in the banner

### Web-UI install / uninstall (`/section/detection/install`, `/section/detection/uninstall`)
Source checkouts get HTMX buttons in `partials/detection.tpl` that install or remove the `detection` extra without leaving the browser. Wheel installs (no `pyproject.toml` reachable from `__file__`) never see the buttons because `_detection_source_root()` returns `None`.

- Subprocess invocation is always `[sys.executable, "-m", "pip", ...]` – never `poetry install -E ...`, which could resolve to a sibling venv if OpenFollow was started without `poetry run`
- Extra names are allowlisted at the HTTP boundary (`_ALLOWED_DETECTION_EXTRAS = {"detection"}`); unknown values are rejected before subprocess launch
- Install specifiers in `_DETECTION_INSTALL_PACKAGES` carry the same version floors as `[project.optional-dependencies]` in `pyproject.toml` (`onnxruntime>=1.17`, `opencv-python>=4.8`) – keep them aligned when bumping pyproject
- Uninstall (`_DETECTION_EXTRA_PACKAGES`) removes only `onnxruntime` and deliberately leaves shared `opencv-python` installed
- A module-level `_detection_install_lock` serialises install/uninstall so a double-click can't race two package managers against the same site-packages
- `_run_package_command` streams combined stdout/stderr through a bounded `collections.deque(maxlen=_SUBPROCESS_TAIL_LINES)` drained by a daemon thread, so a verbose `pip` resolve can't OOM the web process and can't deadlock on a full OS pipe buffer. On `TimeoutExpired` → `proc.kill()` → second `TimeoutExpired` (child ignoring SIGKILL), the helper closes `proc.stdout` and falls back to a bounded `drainer.join(timeout=1)` so the web request thread can't wedge indefinitely
- `_get_detection_missing_deps` catches `ModuleNotFoundError` narrowly (`cv2` → `["opencv-python"]`; other modules by name) and logs at INFO; unexpected exceptions log at WARNING and surface a generic `"detection dependencies unavailable"` so the UI never silently blames OpenCV for an unrelated import failure
- The install-feedback banner renders with `role="alert"` + `aria-live="assertive"` on error and `role="status"` + `aria-live="polite"` on success (both `aria-atomic="true"`) so screen readers announce failures with appropriate urgency

### ONNX export workflow
Use `scripts/export_onnx.py` on a dev machine with ultralytics installed:
```bash
poetry run python scripts/export_onnx.py yolo26n.pt --imgsz 320 --opset 17
```
Copy the produced `.onnx` into `<storage_path>/models/` on the Pi. The same export runs from the detection web UI's **Download Model** action (in the Models box's **Advanced** section) for any catalogued model (`_DETECTION_MODEL_CATALOGUE` in `web/routes.py` – the YOLOv8, YOLO11, YOLO12, and YOLO26 families); both need the `export` extra + an uplink, so they're a workstation task. The five YOLO26 quality tiers are pre-shipped, so a fresh install needs no download.

### ONNX troubleshooting (no detections)
- Install ONNX runtime in the active app environment: `poetry install -E detection`
- If `storage_path` is set and `model` is relative (e.g. `yolo11s.onnx`), model resolution is `<storage_path>/models/<model>`. A blank `storage_path` auto-resolves to `/mnt/nvme/openfollow/yolo` on a unit with the NVMe mounted at `/mnt/nvme`, else falls back to the working directory
- Ensure the selected `.onnx` file exists there; export from `.pt` if needed
- Restart the running app after dependency/model changes
- Verify runtime telemetry: `/api/stats` should show `tracking.available=true`, `tracking.backend="onnx"`, and increasing `tracking.inference_count`
- For Pi performance, use a nano model (`yolo26n.onnx` or `yolo11n.onnx`) at `inference_size=320`

---

## Trigger Zones (`zones/`)

Polygonal zones in world coordinates that emit OSC events as marker markers or detection points enter/exit.

### Layout
```
zones/
    geometry.py     – point-in-polygon (ray casting), inward polygon shrink (miter-clamped)
    engine.py       – ZoneEngine state machine: occupancy, debounce, hysteresis, transitions
    osc_sender.py   – OscOutputClient: per-(host, port) UDP client cache via python-osc
```

### Config (`TriggerZonesConfig`)
- `enabled` (bool) – arms the engine (OSC output). Default `False`.
- `show_overlay` (bool) – renders zone polygons on the HUD. Independent of `enabled` – the overlay hotkey (`z` / `B`) flips this alone, so rendering must not be gated on `enabled`.
- `eval_fps` (int, one of `1, 5, 10, 15, 30, 60`) – engine evaluation rate; marker/detection collection is skipped between ticks.
- `debounce_ms` (int) – transitions inside the window are **discarded, not queued**.
- `hysteresis` (float, metres) – inward polygon offset applied to build the exit polygon (`shrink_polygon`), so near-boundary flicker doesn't re-fire OSC.
- `zones: list[TriggerZoneConfig]` – per-zone: `vertices`, `color`, `trigger_source` (`"markers" | "detection" | "both"`), four OSC addresses (`osc_address_first_entry`, `_additional_entry`, `_partial_exit`, `_final_exit`), `destination_id` (references a shared `OscDestinationConfig`; blank/dangling = emit nothing), `enabled`.

### Occupancy state machine (`ZoneEngine._emit_transitions`)
Per zone, tracks the set of occupants and an integer count. Transitions, in order:
- **first entry** (count 0 → 1): `osc_address_first_entry`
- **additional entry** (count ≥ 1, new occupant): `osc_address_additional_entry` – emitted in sorted occupant order for determinism.
- **partial exit** (count ≥ 2 → lower, still ≥ 1): `osc_address_partial_exit`
- **final exit** (count → 0): `osc_address_final_exit`

Exit tests use the **shrunken** polygon (inward by `hysteresis`); entry tests use the original polygon.

### Coordinates
Vertices, `marker.pos`, and detection-unprojected world points all share the **PSN-absolute** frame (see `## PSN → Coordinate system → The one canonical frame`). `services._collect_marker_positions` returns `marker.pos` verbatim – no offset arithmetic on either side of the containment check. `_populate_zone_overlay` passes vertices through unchanged.

### Hot reload (`ZoneEngine.reload_config`)
Occupancy is carried across config reloads via `_zone_signature = (vertices, trigger_source, enabled)`. Duplicate-signature zones match in FIFO order. A zone that was disabled (or is re-disabled) drops its carried occupancy so re-enabling fires `first_entry` again rather than a silent "already inside" state.

### Overlay rendering (`runtime/services_marker_visuals.py`)
`_populate_zone_overlay`:
- `state.show_zones = bool(tz.show_overlay)` – rendering is driven by `show_overlay` alone.
- Engine occupancy (`is_occupied`, `count`) is only read when `tz.enabled` is also true; otherwise polygons render with `(False, 0)`.

### Web API (`web/routes.py`)
- `GET /api/zones` / `POST /api/zones` / `PUT /api/zones/<index>` / `DELETE /api/zones/<index>` – CRUD for the zones list
- `GET/POST /api/config/trigger_zones` – section-level config
- `_load_json_body()` returns `Any`; callers must also `isinstance(data, dict)` check and 400 on non-object bodies.

### Keybinds
- `key_toggle_zones` (default `z`) – flips `trigger_zones.show_overlay`
- `btn_toggle_zones` (default `B`) – gamepad equivalent; persisted to `config.toml` via `save_config`

---

## Known patterns & gotchas

### Merge conflicts
This repo has two active development streams (Mac dev + Pi). Merge conflicts happen regularly in:
- `openfollow/services.py` – most common (marker allocation/pool changes)
- `openfollow/video/overlay.py` – second most common
- **Always combine both sides** – never just pick one side

### macOS vs Pi differences
- **macOS:** Quartz keyboard polling, per-frame GDK pointer polling (`window.poll_pointer`; under the pipeline GTK button events never arrive and motion arrives intermittently, while scroll events do arrive, so wheel-Z works, counted per wheel click and rate-limited as described under Mouse), NDI via libndi dylib, SDL's HIDAPI joystick backend switched off (`SDL_JOYSTICK_HIDAPI=0` in `GamepadHandler`: SDL before 2.32.6 kept a closed device's HID callback registered, so a reopen turned input into a use-after-free; pygame-ce bundles a fixed SDL, and the backend stays off until pads are verified on it)
- **Pi:** GTK event-based keyboard + GTK pointer/scroll events, NDI via ARM libndi, Cage compositor, systemd service
- `gst_runtime_available()` checks GStreamer at runtime

### GStreamer SRT gotchas
- `decodebin` src pads named `src_0`, `src_1` – NOT `video_0` → don't filter by name
- `set_latency(0)` only works after ASYNC_DONE, not at pipeline creation
- `set_state(PLAYING)` returning ASYNC is normal for SRT caller mode

### PSN speed convention
- Controlled markers broadcast their true velocity vector (m/s, PSN-absolute frame), clamped to 20 m/s; a marker at rest sends `(0, 0, 0)` and the write still happens every frame (freshness stamp)
- Viewer cards show `‖speed‖` of the received marker; controlled cards show the configured move speed, which never reaches the wire
- Receiver stores a wire vector verbatim only when the datagram's sender is the one that published a non-zero speed for that tracker; anything else (no speed chunk, a second station on the same id) falls back to position-based derivation (and only updates when moving)

### `psn_source_iface` propagation
`psn_source_iface` is the **station** interface. PSN in / out, the discovery beacon and marker-catalog sync always use it (read-only rows in the Interface Assignment panel). OTP, RTTrPM, the OSC listener's multicast membership, each OSC destination and the video input carry their own pin and follow the station interface while it is blank (`net_utils.plane_source_iface`). The web UI (`web_bind_iface`) never follows it: blank means every interface.

Every plane is live-applied by `NetworkPlaneObserver` (`runtime/network_observer.py`): it re-resolves each plane once a second and rebinds when the address changes. **Two timings, deliberately different:** the red top-right row is raised from the first addressless poll, while the plane is stopped (rather than sent on another interface) only after `DOWN_POLLS_BEFORE_SUSPEND` such polls, so an Apply or a DHCP renewal does not tear it down. A failed rebind or stop leads the row and the snapshot, unless an outage began after it; a failure's backoff holds back only the rebind and the stop, and nothing new is recorded inside it. `snapshot()` publishes every plane's state (`ok` / `not followed` / `down` / `stopped` / `failing`) as a tuple replaced whole per poll, so the web thread can read it. The diagnostics bundle's **A6 Network bind map** prints the panel's rows (what is configured) beside that snapshot plus the web UI's actual listener (what each plane holds now), and **E7** marks each interface address with its source: DHCP / static from a writable network backend, link-local from the address itself. E7 asks the backend through `read_address_sources`, never the panel's reads: those fall back to DHCP or to no interfaces so the card keeps working, which in a bundle presents a failing backend as data. A failed read says so (`[unavailable: network backend: …]` for the whole backend, `[source unavailable: …]` per address), and an address the backend did not set reads `source unknown`. NetworkManager answers from the device's *active* profile only: a saved one is a guess, and an externally configured device's generated profile always reads `manual`. The kernel route table is read by one parser, `net_utils.read_ipv4_routes`, shared by diagnostics, the video pin and the read-only network backend; it carries no up/down state, because the kernel sets `RTF_UP` on every row it prints.

**A pinned multicast socket selects its interface by index, never by address alone.** A VLAN child has its parent's MAC and so its link-local address, and on connect the parent briefly holds that address too: a socket handed only the address picks whichever interface holds it when it opens. So every plane passes the pinned interface's **name** with the address - PSN and OTP send through `McastTxSocket(iface=<name>)`, the beacon, catalog sync, PSN input and OSC input set `IP_MULTICAST_IF` / `IP_ADD_MEMBERSHIP` with `net_utils.iface_mreqn` - and an interface that is gone is refused. macOS reads only the group and address of an `IP_ADD_MEMBERSHIP`, so the struct keeps the real address. Unpinned planes still go by address.

**Which adapter a name is** (`net_adapters.py`): `AdapterReader` reads the USB socket (`controller_identity.resolve_key` on the `net` class + `port_label`, the Controller Slots numbering), the USB product strings, built-in vs. VLAN-on-parent and the MAC from sysfs, or macOS's hardware-port names from `networksetup`, cached per name and interface index; it never raises. The card, pickers, protocol pointers, the Operator Screen, the observer's alerts and logs, and E7 / A6 all name an interface `Label (name)`. A plane that cannot send tells an unplugged adapter (`net_utils.interface_present` false: `not connected`, the panel's red `Not connected` chip) from one present without an address (`is down`, `Interface down`); the panel reads `present_interfaces()` once per render. The panel never re-renders by itself, since that would drop an unsaved picker choice: its 5 s poll (`/section/interface_assignment/status`) swaps only the Address cells out of band, and when `_iface_options_fingerprint` (names, addresses, adapters, presence, labels) differs from the page's it triggers `iface-options-changed`, on which `refreshIfacePickers` reloads each picker's options with the value it currently shows. Describing an adapter can run `ip` or `networksetup`, so the Operator Screen reads it with its off-thread snapshot (`_NetworkSnapshot.adapters`), never on the frame clock. Tests get a reader that knows no adapter (`tests/conftest.py::_no_host_adapters`); one that cares installs its own with a fake sysfs tree.

---

## Development

### Package management
This project uses **Poetry** for dependency management. Always use `poetry` commands:
- `poetry install` – install all dependencies
- `poetry run openfollow` – run the app
- `poetry add <package>` – add a dependency
- `poetry run pytest` – run tests

### System dependencies (Raspberry Pi)
GStreamer and GTK bindings are system packages (not pip-installable):
```bash
sudo apt install libgirepository-1.0-dev gir1.2-gst-plugins-base-1.0 \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
    gstreamer1.0-tools python3-gi python3-gi-cairo gir1.2-gtk-3.0
```

### Local CI gate (`make ci-remote` → Pi, falls back to `make ci`)
The Makefile runs the same lint / security / test / build steps as `.github/workflows/ci.yml`, **plus** a `typecheck` (mypy) step the GitHub workflow does **not** run – so a `make ci` (especially on the Linux/Pi target) is a stricter superset of the GitHub gate, not a 1:1 mirror.

**The pre-push gate is `make ci-remote`, not bare `make ci`.** When a testing Pi is reachable on the LAN, the gate runs `make ci` **on the Pi** – the real deployment target (aarch64 / Python 3.13 / trixie). That catches arch- and version-specific failures the dev Mac masks (missing cp313 wheels, `mypy` reexport rules). When no Pi is reachable it transparently falls back to running `make ci` locally on the Mac. **Run `make ci-remote` before every `git push`.**

- `make ci-remote` – pre-push gate. `scripts/ci-remote.sh` picks the first reachable host in `OPENFOLLOW_CI_HOSTS` (default `192.168.178.66 192.168.178.59`), rsyncs the working tree onto the Pi's checkout (excluding `config.toml`, detection `models/`, and build/cache junk so device state is never touched), runs `make ci` in the Pi's existing poetry env, then restores the Pi to its exact pre-run commit. Env overrides: `OPENFOLLOW_CI_HOSTS`, `OPENFOLLOW_CI_USER`, `OPENFOLLOW_CI_DIR`, `OPENFOLLOW_CI_FORCE=1` (overwrite a dirty Pi), `OPENFOLLOW_CI_LOCAL=1` (skip the Pi). Requires passwordless SSH (key auth) to the Pi; without it the host probe fails and the gate falls back to local.
- `make ci` – full gate run either on the Pi (by `ci-remote`) or locally as the fallback: `make lint` + `make typecheck` + `make security` + `make test` + `make test-smoke-e2e` + `make build`. The e2e smoke step is the only one that wires the real receivers to the real outputs; it skips itself where the GStreamer runtime is absent.
- `make lint` – `ruff check` + `ruff format --check` on `openfollow/` and `tests/` (rule sets + line-length 120 configured in the `[tool.ruff]` block of `pyproject.toml`)
- `make format` – `ruff format` the tree (run this to fix a `make lint` formatting failure; not part of `make ci`, which only checks)
- `make test` – `pytest -m unit -q` then `pytest -m "integration or smoke" -q`
- `make build` – `poetry build --no-interaction`
- `make install-hooks` – installs the pre-commit hook (one-time)

The pre-commit hook (`.pre-commit-config.yaml`) runs `ruff check` + `ruff format --check` + bandit + EOF/whitespace fixers automatically on `git commit`, but `make ci-remote` (Pi-first, with the local `make ci` fallback) is the authoritative pre-push gate (the hook only sees staged files and skips the typecheck/test/build steps). ruff and bandit run out of the poetry env (`repo: local`, `language: system`), so the hook needs `poetry install` and can never run a different version than `make lint` / `make security`. Do **not** give either a `rev:`-pinned hook repo: that re-pins a tool `pyproject.toml` already pins, and Dependabot bumps the two as unrelated ecosystems, one PR each, neither of which passes alone.
