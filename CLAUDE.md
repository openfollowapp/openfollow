# Agent Workflow & Project Knowledge Base

See `docs/PROJECT_STRUCTURE.md` for layout.

## Principles

1. **Plan first** for non-trivial tasks (3+ steps or architectural impact)
2. **Verify before done** – prove changes work
3. **Minimal impact** – only touch necessary code
4. **Simplicity first** – find root causes, no temporary fixes
5. **No laziness** – senior developer standards

## Code comments

- Keep comments short and concise.
- Do NOT reference issue or PR numbers in code or comments. Put that context in
  commit messages / PR descriptions instead.
- Do NOT leave "legacy" / "removed" / "no longer" breadcrumbs when deleting a
  feature. Write code, comments, tests, and docs as if the current design was
  always the only one – e.g. ONNX Runtime is *the* detection backend, not "the
  backend that replaced the old one". No "we used to…", no naming the removed
  thing even to say it's gone. Put the history in the commit message instead.

## Writing & punctuation

- **Never use em-dashes (Unicode U+2014).** Use an en-dash (U+2013), a hyphen,
  or rewrite the sentence. This applies everywhere: code comments, docstrings,
  UI strings, templates, help text, and Markdown.

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
- **No outbound HTTP from server-side code at runtime.** Two documented exceptions, both gated on an explicit operator click and never reachable on the data path: (1) the signed-``.deb`` release updater (``runtime/deb_update.py``) fetches releases only from the GitHub repo named in ``update_github_repo``; (2) the detection **model export** action (``/section/detection/export``) shells out to the optional ``export`` extra (ultralytics), which downloads YOLO weights and exports them to ONNX under ``<storage_path>/models``. Export needs the extra installed *and* an uplink, so it only works on a workstation – on an offline show Pi the button is hidden/disabled and operators copy the ``.onnx`` over manually. Anything else (telemetry, analytics, license check, "phone home", remote feature flags) is rejected.
- **No silent fallback to "online" if a resource is unreachable.** A page that renders fine without its CDN-loaded script but where Save silently no-ops is the worst possible failure mode – the historical example was [`base.tpl`](openfollow/web/templates/base.tpl) loading htmx from ``unpkg.com``: on offline LANs the script never loaded, so every form fell through to a native GET on the current URL and saves silently no-opped. The regression test in [`tests/test_web_server.py`](tests/test_web_server.py) (``test_index_page_uses_locally_bundled_htmx`` + ``test_htmx_static_asset_is_served``) pins the local-asset reference and the asset's content type so this can't quietly revert.

The local LAN is fair game: mDNS-style multicast beacon, peer broadcast, PSN multicast, OSC, RTSP/SRT/RTP/NDI receivers all stay on the show network and are fine. CI runs offline; new external-service dependencies break that gate.

When adding a feature that needs an external resource at design time (e.g. a new JS library, a font, a model file): add it as a bundled asset shipped with the repo, not as a runtime fetch. When in doubt, ask "does this still work after I unplug the WAN cable?" – if no, it's broken.

---

## What this app does

OpenFollow is a Raspberry Pi (or macOS) application that:
- Receives a video signal (NDI or SRT) and displays it fullscreen via GStreamer
- Overlays a Cairo-based HUD on top of the video (marker positions, speed, grid, crosshair)
- Sends PSN (PosiStageNet) marker coordinates via multicast UDP to stage systems (e.g. grandMA3)
- Receives PSN data from other servers and displays viewer markers
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
├── PsnReceiver (psn/receiver.py)             – receives PSN from other servers
├── InputManager (input/input_manager.py)     – keyboard + gamepad + mouse + OSC
└── ConfigWebServer (web/server.py)           – Bottle web UI + mDNS beacon
```

Camera calibration is web-only (the `/wizard` setup wizard) – there is no on-device calibration overlay. `scene/` holds just `camera.py` + `solver.py` (the DLT solver).

**Frame loop:** `_animate()` runs on the display vsync tick (`Gtk.Widget.add_tick_callback`, ~60–120 Hz), so `dt` integrates against real elapsed time, not a fixed step; a separate `GLib.timeout_add` drives slow housekeeping.
Order: `_process_input(dt)` → `svc.update_video()` → `svc.apply_detection_pin()` → `svc.update_zone_triggers()` → `svc.update_marker_visuals()`

---

## Config model (`configuration.py`)

All config lives in `config.toml` (auto-reloaded when file changes on disk).

### AppConfig top-level fields
| Field | Default | Notes |
|---|---|---|
| `video_source_type` | `"rtsp"` | `"ndi"`, `"srt"`, `"rtsp"`, `"rtp"`, `"picam"`, `"v4l2"` (Linux USB camera), `"avf"` (macOS USB camera), or any registered plugin ID |
| `ndi_source_name` | `""` | NDI source string (read by NDI plugin) |
| `srt_host` | `"srt://0.0.0.0:5000"` | SRT URL (read by SRT plugin) |
| `window_width/height` | `1280×720` | |
| `psn_system_name` | `"OpenFollow"` | Shown in PSN + web UI |
| `psn_mcast_ip` | `"236.10.10.10"` | PSN multicast group |
| `psn_source_iface` | `""` | Bind PSN / beacon to this interface **by name**; empty = auto-detect |
| `web_port` | `80` | Web config UI port |
| `web_pin` | `""` | Auth PIN; when non-empty, browser routes require login + cookie (`SameSite=Strict`), peer-to-peer routes require HMAC-signed headers |
| `update_github_repo` | `"openfollowapp/openfollow"` | `owner/repo` slug the `.deb`-release updater queries for new releases |
| `update_service_name` | `"openfollow"` | systemd unit restarted after a `.deb` install |
| `controlled_marker_ids` | `[]` | Markers this instance moves |
| `viewer_marker_ids` | `[]` | Markers shown in overlay (incl. remote) |

### Sub-configs
- **CameraConfig:** pos_x/y/z, pitch/yaw/roll, fov
- **GridConfig:** visible, width, depth, spacing, x_offset, y_offset, z_offset, origin_visible, origin_length, origin_thickness
- **MarkerConfig:** min_speed, max_speed, move_speed, default_pos_x/y/z, ball_visible, ball_size, transparency, crosshair_visible, crosshair_size, crosshair_color, crosshair_thickness, drop_line, drop_line_thickness, ground_circle, ground_circle_size, ground_circle_filled, z_display_from_stage
- **ControllerConfig:** enabled, keyboard_enabled, mouse_enabled, mouse_hysteresis_px, mouse_smoothing, mouse_max_y, mouse_wheel_z_enabled, mouse_wheel_invert, mouse_wheel_z_step, mouse_double_click_reset, deadzone, invert_y, curve, the gamepad button map (`btn_reset`, `btn_source_select`, `btn_speed_up/down`, `btn_move_z_up/down`, `btn_settings`, `btn_next/prev_marker`, …), the keyboard binding map (`key_move_layout`, `key_reset`, `key_speed_up/down`, `key_toggle_help`, `key_toggle_zones`, `key_settings`, …), `move_xy_stick` (no LED fields)
- **DetectionConfig:** enabled, model, storage_path (not exposed in the UI; device-local – stripped from config export and preserved-across-import so a path never crosses machines; blank auto-resolves to `/mnt/nvme/openfollow/yolo` when `/mnt/nvme` is a mountpoint, else a `yolo` folder under the working dir – via `resolve_detection_storage_path` in [`video/detection.py`](openfollow/video/detection.py), used by `_prepare_model_path` + the web model-discover/export helpers; set an absolute path in `config.toml` to override), inference_size, preprocess_clahe, confidence, interval_ms, show_boxes, show_labels, box_color, box_thickness, max_persons, pin_marker, pin_marker_id (`-1` = follow selected marker), pin_point (`top`|`bottom`), smoothing, prediction, grace_period_ms, pin_mode (`replace`|`assist`, default `assist`), assist_radius_m, assist_strength
- **OscConfig:** enabled, port (default 8765), allowed_sender_ips (default `[]` = allow-all + startup WARNING; normalised to `list[str]` by `__post_init__` to survive malformed TOML)

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
- **Requires app restart:** **detection** changes the worker can't serve in-process: enabling (`detection.enabled` going `False → True`, because the receiver pipeline must wire the GStreamer appsink into a fresh detector and only `init_video` does that), changing `detection.inference_size` (GStreamer appsink caps are pinned at pipeline build time; live-restamping the worker's `_inference_size` would silently disagree with the appsink resolution), and any detection edit when the detector is missing or unavailable (`_person_detector` is None or `available is False` because the backend never loaded at startup – `reload_config` would silently no-op since the worker thread was never started). `web_port` also stays restart-required (server-restart-in-place is fragile while a request is in flight, rare change)
- **Live update (no restart):** **video_source_type and any plugin config field** (auto-detected via `plugin.config_changed()`; the receiver live-swaps the active input plugin in place via `swap_video` → `receiver.swap_input`, transactional with rollback – see [`AppRuntimeServices.swap_video`](openfollow/services.py)), camera, grid, movement (speed limits + default position), marker, controller, osc, trigger_zones, controlled_marker_ids, viewer_marker_ids, psn_system_name, **psn_source_iface, otp_output, rttrpm_output**, **detection** running-detector cases – on→on (worker drains a staged config between frames; rebuilds the inference session in-thread when model / storage_path changes) and on→off, **window_width / window_height, web_pin**
- Restart triggered via `_web_commands.request_restart()`, polled in `_check_restart_request()`
- Saving config with restart-requiring changes triggers an automatic restart via the hot-reload file watcher within ~1 animation frame

### Live-apply pattern
Service rebind/restart live-apply paths (sockets, worker threads) route through `_apply_with_fallback("name", apply_fn, on_failure=…)` in [`configuration.py`](openfollow/configuration.py). The helper logs duration on success, logs `logger.exception` plus runs `on_failure` on exception, and returns so the dispatcher keeps applying subsequent settings – a single failed live-apply on one service must not bypass live-applying everything else. Pure in-memory mutations that can't fail at runtime (e.g. `window_width/height` resizing the GTK window, `web_pin` mirroring into `app._config`) skip the helper because there's no failure mode to revert from.

Each underlying service exposes a small `restart(...)` (or `rebind(...)` for receivers) that does `stop()` → reassign attributes → `start()`. Marker registrations and other shared state survive across the cycle by living on instance attributes that aren't touched by `stop()`/`start()`. See [`OtpServer.restart`](openfollow/otp/server.py), [`RttrpmServer.restart`](openfollow/rttrpm/server.py), and [`PsnReceiver.rebind`](openfollow/psn/receiver.py).

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
    picam.py        # Raspberry Pi camera plugin (libcamerasrc, CSI/MIPI)
    v4l2.py         # USB camera / capture card plugin (v4l2src, UVC, Linux-only)
    avf.py          # USB camera / capture card plugin (avfvideosrc, macOS-only)
    testpattern.py  # Synthetic test-pattern source (videotestsrc) – always available
```

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

### Receiver (`video/receiver.py`) – generic orchestrator
Delegates protocol-specific work to the active plugin. Retains shared infrastructure:
- `_build_overlay_tail()` – links the plugin tail directly to `shared_videosink` (no `cairooverlay`; HUD is drawn on a `Gtk.DrawingArea` above the gtksink widget – see "HUD rendering" below)
- `_create_placeholder_pipeline()` – black-frame "No Signal" fallback
- Shared gtksink management (detach/reattach across pipeline switches)
- Connection timeout, reconnection scheduling (driven by plugin's `ReconnectPolicy`)
- First-frame / caps detection via downstream pad probes (`_on_pad_event` for the caps event, `_on_sink_buffer` → `_handle_video_connected` on the first buffer) – replaces the old `cairooverlay` caps-changed signal
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
- `srtsrc`: `mode=caller`, `wait-for-connection=True`, `latency=125ms`
- Hardware decoder priority boosting (V4L2 > avdec > openh264)
- Preserves decoder latency on ASYNC_DONE (do NOT force 0)
- `pad-added` on decodebin: **do NOT filter by pad name** (uses `src_0`, not `video_0`)
- Reconnect: 3 retries with 8s first-frame timeout, then no-signal placeholder fallback

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
| `psn/receiver.py` (`set_pos` from incoming PSN packet) | PSN-absolute (raw) |
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

### PsnServer (`psn/server.py`)
- Sends PSN multicast every ~33ms
- `add_marker(id, name)` / `remove_marker(id)` / `get_marker(id)`
- `marker.set_pos(x, y, z)`, `marker.set_speed(vx, vy, vz)`
- **Speed encoding:** every frame in `services.py`, each controlled marker sends its own effective speed magnitude. Controller-mapped markers use `move_speed × controller multiplier`; otherwise it falls back to configured `move_speed`.

### PsnReceiver (`psn/receiver.py`)
- Receives PSN multicast in background thread
- `ignore_ids`: controlled_marker_ids – prevents loopback overwriting own markers
- `_last_seen[tid]`: monotonic timestamp of last received packet
- `_last_pos[tid]`: previous position for speed derivation
- **Speed logic (per packet):**
  1. If `t.speed` is non-zero vector: store as `set_speed(magnitude, 0, 0)`
  2. If `t.speed` is zero or None: derive from `delta_pos/dt`, only when actually moving (preserves last known speed when stationary)
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
online: bool            # green dot = online, red dot = offline
name: str               # marker label
controller_idx: int | None    # bound gamepad slot, or None
controller_connected: bool    # is that pad currently plugged in
is_controlled: bool           # in controlled_marker_ids (vs viewer-only)
marker_fader: float | None    # per-marker fader value, or None
```

### HUD layout
- **Top-left:** key hints – the active source plugin's `hotkey_label` (e.g. `N=NDI`) plus interface / settings / exit hints (no calibration hint; calibration is web-only)
- **Top-right:** system stats (CPU, mem, temp, FPS)
- **Bottom-left:** combined info panel (`IP Address:` + `Video Source:`)
- **Bottom-center:** controller connection status
- **Right side:** marker cards (one per viewer marker)

### Marker card (180×64px)
- **Top-right dot:** online status (green/red, radius 4px)
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
| M | `key_settings` | Open the Settings menu (source / interface / network) |
| N | NDI plugin `hotkey_label` | NDI source selection (NDI source only) |
| Esc | modal | Close the active overlay |

### Speed adjustment (`adjust_move_speed`)
- **Marker:** base step 0.1 m/s (≤4.0), 0.5 m/s (>4.0), clamped to configurable `min_speed`–`max_speed` (defaults 0.1–3.0)
- **Streak acceleration:** ×1 (0–4 presses), ×3 (5–9), ×8 (10+), resets after 0.75s
- Both keyboard `key_speed_up`/`key_speed_down` and controller LB/RB call the same method

### Mouse (`input/mouse.py`)
Left-click on a marker's **ground circle** grabs that marker (hit-tested against the projected ground-circle polygon, with a pixel-radius fallback when the circle is off/tiny; nearest centre wins on overlap); right-click releases. A grab seeds the glide at the marker's current position – it never yanks the marker to the click – and a click on empty stage is a no-op. While held, pointer moves record a target (after the `mouse_hysteresis_px` pixel deadband, and rejecting targets past the `mouse_max_y` upstage cap); the marker is steered by `MouseHandler.update()`, called every frame from `InputManager.update`, which EMA-glides it toward the target at `mouse_smoothing` (0 = instant, higher = smoother; glide alpha = `1 - mouse_smoothing`, floored so the max never freezes). Positions are unprojected onto the stage floor plane (via `scene/solver.unproject_to_plane`) in the one PSN-absolute frame. The ground-circle ring geometry is shared with the overlay via `scene/solver.ground_circle_world_ring`. Scroll wheel adjusts Z when `mouse_wheel_z_enabled`, by `mouse_wheel_z_step` m per tick, sign flipped by `mouse_wheel_invert`. Double **right-clicking** (two right-clicks within `_DOUBLE_CLICK_S`/`_DOUBLE_CLICK_PX`, detected in-handler via the injectable `_clock`) resets the selected marker to `_get_default_marker_position()` and **releases control** when `mouse_double_click_reset` is set. It lives on the right button because that's the release button, and a reset releases too (staying grabbed would snap the absolute marker straight back to the cursor on the next move). Left-click stays a pure grab.

**Event source per platform.** `MouseHandler`'s `on_pointer_*` entry points are fed by the GTK pointer signal handlers in [`window.py`](openfollow/window.py) (`_on_button_press`/`_on_motion`/`_on_scroll`). GTK doesn't reliably deliver pointer events to the gtksink-hosted window on **macOS** under the GStreamer pipeline (the same reason the keyboard polls Quartz instead of reading GTK key events), so on macOS the window also runs `poll_pointer()` once per frame from `_on_tick`: it reads `gdk_window.get_device_position()` (window-relative position + a button mask) and synthesises the same `pointer_down`/`pointer_move` events through `_emit`, so everything downstream is unchanged. Edge detection lives in the pure `_poll_pointer_events` helper. The scroll **wheel can't be polled** (no current-scroll-position API), so wheel-Z stays GTK-only on macOS – use the `Q`/`E` keys for height there. The poll is a no-op on Linux/Pi, where the GTK events work.

### Gamepad (`input/gamepad.py`)
All bindings are `ControllerConfig` fields; defaults shown.
- Left stick (`move_xy_stick`, default `left`): move selected marker
- LB / RB (`btn_speed_down` / `btn_speed_up`): adjust move speed
- LT / RT (`btn_move_z_down` / `btn_move_z_up`): lower / raise marker Z
- `btn_reset` (default `X`): reset marker to default position
- `btn_source_select` / `btn_settings` (default `BACK`): source selection / Settings menu
- `btn_toggle_help` (default `Y`), `btn_toggle_zones` (default `B`), `btn_next/prev_marker` (DPAD)

### Gamepad → marker routing (`InputManager._gamepad_marker_id`)
Gamepad movement, reset, speed readout, and controller-info all go through
`_gamepad_marker_id(controller_idx)` so every routing surface stays
consistent. The routing surface is also passed to `GamepadHandler` as a
`marker_resolver` callback so the bumper-speed and effective-speed paths
get the same marker_id without reaching back into the InputManager. Two
modes:

- **Single-gamepad mode** – predicate is *exactly one controller
  connected AND* `app._selected_id is not None`. Route to
  `app._selected_id`. DPAD next/prev cycles `_selected_id` – same
  contract as the keyboard.
- **Multi-gamepad mode** – the fallback: triggered when the
  single-gamepad predicate fails, i.e. **2+ controllers connected**
  *or* **one controller with no selection**. Fixed slot mapping
  `app._controlled_ids[controller_idx]` (derived from
  `controlled_marker_ids`), so each physical gamepad keeps its own
  marker regardless of the shared `app._selected_id`. **DPAD next/prev
  is disabled in this mode** – pressing it is a no-op at flag-set time
  (the edge state in `_button_prev` still advances normally so a later
  single-pad disconnect leaves no stuck carryover, and the OSC trigger
  bus's independent `_button_bus_prev` keeps its own dispatch
  unaffected). The help overlay also hides the `next_marker` /
  `prev_marker` labels in this mode.

Don't reintroduce direct `app._controlled_ids[controller_idx]` indexing in
a new consumer – route through `_gamepad_marker_id` (or the injected
`marker_resolver` inside `GamepadHandler`) so HUD, speed, movement, and
reset stay in sync.

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
top-left badge "C0" / "C1" / … mirroring the status dot top-right; a
muted "C0·" suffix marks a disconnected pad. The bottom-center
"Ctrl0 → M0 | Ctrl1 → M1" panel from earlier builds was retired in
favour of the per-card surface. Connected pads with no marker (more pads
than `controlled_marker_ids`) surface in the Settings menu's info card
under "Unbound controllers". Assignment stays **implicit**: pygame's
plug-order × the `controlled_marker_ids` list (edited via the web UI).

Viewer-only markers (in `viewer_marker_ids` but NOT in
`controlled_marker_ids`) render at reduced alpha (≈0.6 via a Cairo group
wrap) and skip the speed bar – the bar is a control-context affordance.
Marker-card borders use each marker's own colour from the shared marker
catalog (`MarkerCatalog.get(tid).color` via
`services_marker_visuals._resolve_marker_color`, with a
`DEFAULT_MARKER_COLORS[tid % len(...)]` palette fallback for the
transient race where a controlled id has no catalog entry yet) instead
of the global golden accent, so each card is identifiable at a glance.

---

## Web UI (`web/`)

### UI copy: explanations live in the help drawer (REQUIRED)

Keep inline form copy **terse**: a `<label>` names the control, and a **one-line** `field-note` / `section-note` may *orient* ("Mouse controls for the on-display UI"; "…storage, OSC, and PSN/RTTrPM/OTP stay metric regardless"). Anything past that one line – multi-sentence behaviour, defaults, side-effects, caveats, the "why" – belongs in that section's **help drawer markdown** (`openfollow/web/help/<section>.md`, surfaced by the per-section `?` drawer via `data-help="<section>"`), **not** inline as a long `field-note` / `title` tooltip.

- The line is: terse orienting note inline = fine; behavioural *explanation* = help drawer. When in doubt, if it teaches *how the feature behaves* (not just *what the field is*), it's an explanation.
- When you add or change a control, update the matching `openfollow/web/help/<section>.md` (and keep the website-docs mirror in mind – see the `Check help on merge` auto-memory). The help drawer is the single home for the explanation, so it can't drift between the form and the docs.
- Concretely: do **not** write a `field-note` like "Turning this off also disables X; turning it back on does not re-enable…" – that's a behavioural explanation. Put it in the help `.md`. The experimental-features toggle originally made this mistake; the sentence now lives in `help/general-station.md`.

### Key routes
| Route | Method | Description |
|---|---|---|
| `/` | GET | Main config page |
| `/section/overview` | GET | Server list partial (HTMX-polled every 5s) |
| `/section/<name>` | GET | Config section partial |
| `/section/movement` | POST | Save movement settings (speed limits + default position) |
| `/section/general` | POST | Save + apply general settings |
| `/video-input/ndi/sources` | GET | NDI source `<option>` list (served by the NDI plugin's `web_routes()`) |
| `/network/interfaces/by_name` | GET | Interface `<option>` list (iface-keyed) |
| `/section/network/status` | GET | Network read-only view; `/section/network/edit` → editable form |
| `/section/network` | POST | Re-render edit form on interface/method change – no write |
| `/section/network/apply` | POST | Validate + write IPv4 config via the privileged adapter |
| `/section/network/renew` | POST | Renew DHCP lease via the privileged adapter |
| `/api/info` | GET | JSON: system_name, ip, port |
| `/api/peers` | GET | JSON: discovered peers |
| `/api/config/export` | GET | Download full config as JSON file |
| `/api/config/import` | POST | Import config JSON (preserves device IP); supports `?confirm_restart=1` and `?skip_restart=1` |
| `/api/config/<section>` | GET/POST | JSON config API |
| `/api/config/<section>/broadcast` | POST | Push config to all peers |
| `/wizard` | GET | Setup wizard (camera positioning + grid calibration) |
| `/api/video/snapshot/full` | GET | Full-resolution JPEG snapshot (503 if no feed) |
| `/api/wizard/project` | POST | Project grid corners + ref point to screen coords |
| `/api/wizard/unproject` | POST | Unproject screen points to world plane, return delta |
| `/api/wizard/solve` | POST | Run DLT solve from 4 corner screen positions |

### Config transfer (export / import)
- **Export:** `GET /api/config/export` returns the full config as a downloadable JSON file. Device-local fields are stripped from the payload by `_config_dict_redacted`: `web_pin` (login secret) and `detection.storage_path` (an absolute path that only makes sense on the exporting host).
- **Import:** `POST /api/config/import` applies imported JSON; always preserves the current device's `psn_source_iface`, `web_pin`, `web_port`, and `detection.storage_path` (captured before the section apply, restored after, in `_apply_import_data`). Section-level peer broadcast / `/api/config/<section>` go through `strip_device_local_fields`, which drops the same per-section set (`_DEVICE_LOCAL_FIELDS_BY_SECTION`). A storage path from another machine must never land here – it would be unwritable and break model storage / export.
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
7-step guided workflow for camera positioning and grid calibration:
1. **Preparation** – info + SVG stage layout illustration
2. **Grid Setup** – width, depth, z_offset, spacing, x_offset, y_offset; dynamic SVG illustration updates from input
3. **Video Source** – select and configure camera input (reuses video source UI); save & restart to activate
4. **Camera Position** – pos_x/y/z, pitch/yaw/roll, fov; dynamic isometric illustration
5. **Reference Mapping** – draggable crosshair for coarse calibration (single known point); rigid-body shift of all corners
6. **Corner Pinning** – 4 draggable corners, DLT solve, solved camera params displayed
7. **Review & Apply** – read-only summary of all values + green overlay; Apply or Discard

Key implementation details:
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
- "No feed" UX: each preview container has a sibling `.wizard-no-feed` placeholder div; `setPreviewVisibility()` toggles both so steps 5–7 never render blank when `/api/video/snapshot/full` fails

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
- `/login`, `/assets/*`, `/section/statistics` are exempt from auth.

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

### Broadcast target restriction
`_send_config_to_peer` / `_send_config_import_to_peer` refuse to POST to non-private IPs – `ipaddress.IPv4Address.is_private` covers RFC 1918, link-local, and loopback, which is the set a legitimate peer can plausibly have. Closes the SSRF vector where a crafted beacon advertised an attacker-chosen endpoint (e.g. `web_port=22` or an internal service port).

### Software update flow
The only in-app updater is the **signed-`.deb` GitHub-release installer** ([`runtime/deb_update.py`](openfollow/runtime/deb_update.py)), driven by the `/section/general/deb-update*` + `/section/general/deb-upload` routes. *Check & Install Latest* queries the GitHub Releases API for `update_github_repo`, downloads the matching `openfollow_<version>_<arch>.ofupdate` bundle, verifies its signature (against the on-device public key) and the inner `.deb`'s SHA-256, then installs as root via the privilege broker and restarts `update_service_name`. *Offline install* takes an operator-supplied `.ofupdate` over the LAN through the same verify-then-install path. The legacy `git pull` + `poetry install` updater (and its `update_source_url` / `update_repo_branch` / `update_allowed_hosts` config + host-allowlist) has been removed entirely – see [`docs/PACKAGING.md`](docs/PACKAGING.md) ("Removed: in-app git updater").

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

Default tuning is Pi-oriented:
- `model = "yolov8n.onnx"`
- `inference_size = 320`
- `preprocess_clahe = false`
- `interval_ms = 100`

### Architecture
- `PersonDetector` runs inference in a background thread, pulling frames from a GStreamer `appsink` (tee branch)
- Inference runs through `_OnnxBackend` in `detection.py`
- Pure NumPy post-processing handles **both YOLO head layouts** (branched on output column count in `_OnnxBackend.predict`): YOLOv8 / YOLO11 (`[cx, cy, w, h, <class scores>]` → cx,cy,w,h→xyxy + NMS) and the NMS-free end-to-end head used by YOLO26 / YOLOv10 (`[x1, y1, x2, y2, conf, class]` → xyxy as-is, person-class filter, **no** second NMS). Mixing these up is what produced full-frame boxes for YOLO26
- **Inference size auto-detects from the model**: `_OnnxBackend.model_input_size` reads the ONNX input shape and `_load_backend` adopts it into `_inference_size` (driving the appsink caps via `input_resolution`), so the operator needn't match `inference_size` to the export. A dynamic-axis model reports `None` → the configured `inference_size` stands
- CLAHE preprocessing is optional (`preprocess_clahe`)
- Detection frequency controlled by `interval_ms` (default 100ms = 10 FPS)
- Results stored as `list[DetectionBox]` (normalised 0–1 coordinates), swapped atomically via GIL

### ByteTrack tracking (`video/tracking.py`)
- `ByteTracker` binds detections to tracklets via a two-stage IoU association on top of a per-track constant-velocity Kalman filter (`_KalmanFilter`), all pure NumPy (no SciPy / external tracker dependency – keeps the offline-runtime contract)
- **`confidence` is the high threshold**: detections at/above it drive the first association; detections in `[LOW_DETECTION_THRESHOLD, confidence)` drive a second (recovery) pass, so a performer who dims into shadow (their score dropping with the light) stays bound to their existing track instead of being dropped and re-numbered. Low-only detections never spawn a new track
- Each tracked person gets a stable `track_id` (incrementing int); the Kalman filter predicts a lost track forward so its reported box (and any pinned marker) glides along the trajectory through a brief occlusion rather than freezing at the last seen box
- `PersonDetector._track` splits the raw detections (pulled down to the low floor in `_run_inner`), runs the tracker, then publishes `self._results` (tracks matched this frame, highest-confidence first, capped at `max_persons`) plus `self._tracked` (every live tracklet incl. lost-within-grace) for the pin grace logic
- `tracked_detection` property returns the currently pinned person (sticky selection); falls back to largest visible detection only when the pin expires
- Grace period (`grace_period_ms`): the lost-track retention window – a track unmatched for longer is removed; until then `tracked_detection` holds the pinned person's predicted box

### Marker pinning (`services.py: apply_detection_pin`)
- Runs every frame at 60 FPS in `_animate()` loop
- `pin_point`: `"top"` pins to head (top-center of box, unprojects at marker Z), `"bottom"` pins to feet (bottom-center, unprojects at grid `z_offset` = stage floor)
- Velocity estimation: EMA-smoothed (alpha=0.3) from raw target position deltas
- Prediction: `predicted = target + velocity × prediction` – lookahead to compensate for detection lag on fast-moving persons
- EMA smoothing applied **after** prediction: `smooth += alpha × (predicted - smooth)` – smooths the final output including lookahead
- `smoothing`: 0.01–1.0 (lower = smoother/laggier, higher = more responsive)
- `prediction`: multiplier on velocity vector (0 = disabled, ~2–5 typical range)

### Assist mode – two markers (`pin_mode = "assist"`)
`replace` overwrites the registered marker with the largest detection. `assist` splits the marker into **two entities** (see `runtime/services_detection_pin.py`):
- **Manual anchor** – operator-steered, freely movable, rendered as the **solid carded marker** (the operator-facing one) at the anchor position. Stored per-pinned-id in `app._assist_manual: dict[int, Marker]` (a real `Marker`, **never** registered with `PsnServer` → never broadcast, never in zones). Operator input reaches it because the input resolvers (`InputManager._get_marker`, `mouse.MouseHandler._get_selected_marker`) redirect to it when `assist_pinned_marker_id(app)` matches. The pin **never writes the anchor**.
- **AI-corrected output** – the existing registered/controlled marker (broadcast + zones, unchanged plumbing), rendered as the **dim ghost** crosshair + ground ring (`MarkerOverlayData.is_assist_ghost`, no card) so the broadcast position reads as secondary to the marker the operator steers. Each frame it **glides** (single EMA at `smoothing`, seeded once, never reset → never snaps) toward the detection nearest the anchor within `assist_radius_m` (eased onto the person by `assist_strength`, 1.0 = exactly on the person), or back toward the anchor when none is in range. Output Z always follows the anchor's Z.
- `DetectionPinState.ai_smooth_x/y` is the never-reset outer glide; `soft_release()` (lost detection) drops the lock + velocity but keeps the glide. `_prune_manual_markers` discards the manual-anchor markers when assist disengages or the pinned id changes. The shared `assist_pinned_marker_id` / `get_or_create_manual_marker` helpers are the single source of truth for "which id is assist-controlled" and lazy anchor seeding.

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
poetry run python scripts/export_onnx.py yolov8n.pt --imgsz 320 --opset 17
```
Copy the produced `.onnx` into `<storage_path>/models/` on the Pi. The same export runs from the detection web UI's **Download Model** action for any catalogued model (`_DETECTION_MODEL_CATALOGUE` in `web/routes.py` – the YOLOv8, YOLO11, YOLO12, and YOLO26 families); both need the `export` extra + an uplink, so they're a workstation task.

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
- **macOS:** Quartz keyboard polling, per-frame GDK pointer polling (`window.poll_pointer`; GTK pointer/scroll events don't fire reliably under the pipeline – wheel-Z is unavailable, use `Q`/`E`), NDI via libndi dylib
- **Pi:** GTK event-based keyboard + GTK pointer/scroll events, NDI via ARM libndi, Cage compositor, systemd service
- `gst_runtime_available()` checks GStreamer at runtime

### GStreamer SRT gotchas
- `decodebin` src pads named `src_0`, `src_1` – NOT `video_0` → don't filter by name
- `set_latency(0)` only works after ASYNC_DONE, not at pipeline creation
- `set_state(PLAYING)` returning ASYNC is normal for SRT caller mode

### PSN speed convention
- Controlled markers broadcast `set_speed(move_speed, 0, 0)` so magnitude = configured speed
- Receiver stores non-zero received speed as `set_speed(magnitude, 0, 0)` – scalar in x component
- Position-based derivation only runs when protocol speed is zero (and only updates when moving)

### `psn_source_iface` propagation
When set, the interface name is resolved to an IPv4 and flows to: PsnReceiver (`source_ip`), BeaconSender / Receiver (`iface_ip`), SystemStatsCollector (`preferred_ip`), PsnServer (`source_ip`). It is **live-applied** – the sockets rebind via `apply_psn_source_ip_change`, no restart needed.

---

## Development

### Package management
This project uses **Poetry** for dependency management. Always use `poetry` commands:
- `poetry install` – install all dependencies
- `poetry run python3 -m openfollow` – run the app
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
- `make ci` – full gate run either on the Pi (by `ci-remote`) or locally as the fallback: `make lint` + `make typecheck` + `make security` + `make test` + `make build`.
- `make lint` – `ruff check` + `ruff format --check` on `openfollow/` and `tests/` (rule sets + line-length 120 configured in the `[tool.ruff]` block of `pyproject.toml`)
- `make format` – `ruff format` the tree (run this to fix a `make lint` formatting failure; not part of `make ci`, which only checks)
- `make test` – `pytest -m unit -q` then `pytest -m "integration or smoke" -q`
- `make build` – `poetry build --no-interaction`
- `make install-hooks` – installs the pre-commit hook (one-time)

The pre-commit hook (`.pre-commit-config.yaml`) runs `ruff check` + `ruff format --check` + bandit + EOF/whitespace fixers automatically on `git commit`, but `make ci-remote` (Pi-first, with the local `make ci` fallback) is the authoritative pre-push gate (the hook only sees staged files and skips the typecheck/test/build steps).
