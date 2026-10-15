# Input

Covers `input/`, `binding_conflicts.py`, the OSC listener. The rules that apply everywhere are in [CLAUDE.md](../../CLAUDE.md).

## Keyboard (`input/keyboard.py`)
**macOS:** Quartz `CGEventSourceKeyState` – hardware polling, reliable under GStreamer load.
**Linux/fallback:** GTK event-based tracking.

### Discrete vs continuous keys (config-driven)
Discrete keys (edge-detected, one event per press) come from two sources: a fixed set of modal-overlay keys `_MODAL_DISCRETE_KEYS` (`Tab`, `Enter`, `Escape`, the arrows, `b`) **plus** the operator-bound action keys named in `_DISCRETE_ACTION_FIELDS`, whose values are read from `ControllerConfig` (`key_reset`, `key_toggle_help`, `key_toggle_zones`, `key_speed_down`, `key_speed_up`, `key_next_marker`, `key_prev_marker`, `key_clear_messages`, `key_pathfinder`, `key_toggle_paths`). The polled set is rebuilt from config – there are no hardcoded `n`/`c`/`i` action keys.

Movement keys (`key_move_layout`, default WASD) are continuous-polled (held = repeated).

## Keyboard layout (`keyboard_layout.py`, packaged session)
The packaged kiosk session (`packaging/debian/session.sh`) starts Cage in the layout `keyboard_layout.py` resolves, run as `/usr/share/openfollow/keyboard-layout env config.toml` (a stdlib-only copy of the module, like `camera-setup`) from the app's working directory. **Resolution order:** `controller.keyboard_layout` (+ `keyboard_variant`) when the system's xkb catalog lists it, else `XKBLAYOUT` / `XKBVARIANT` from `/etc/default/keyboard` (first entry of a comma list; an `export ` prefix is accepted) when listed, else `us`. The catalog is `/usr/share/X11/xkb/rules/evdev.lst` (from `xkb-data`) cut down to the layouts with a file under `/usr/share/X11/xkb/symbols`: the rules list `custom`, which ships no symbols, and Cage builds no keymap from it, so the helper, the web picker and validation all read the same cut-down catalog. A variant the chosen layout lacks is dropped; any read or parse failure falls through to the next source. Model and options describe the keyboard, not the layout, so every resolution carries the system file's, whichever source picked the layout. Its `XKBOPTIONS` keep only the entries the catalog's `! option` section lists, never the `custom:` group (it needs files a user adds), and the rest of a comma list survives; `XKBMODEL` is checked for its characters only, since an unknown model still compiles. The image writes `/etc/default/keyboard` with `us` where its base sets none.

**Environment contract** (what the app and anything reading the session may rely on): the session exports `XKB_DEFAULT_LAYOUT`, `XKB_DEFAULT_VARIANT` (only when set), `XKB_DEFAULT_MODEL` / `XKB_DEFAULT_OPTIONS` (from the system file, only when set) and `OPENFOLLOW_KEYBOARD_SOURCE` (`config` / `system` / `default`). The helper's `key=value` lines are read with `read`, never evaluated, and a value outside `[A-Za-z0-9_-]` (options: plus `:` and `,`) is ignored. A missing or failing helper exports none of them and Cage starts in its own `us`. `OPENFOLLOW_KEYBOARD_SOURCE` is the marker that this session resolved the layout: the web UI shows Keyboard Layout only while it is set (`web/keyboard_layout_view.py`), so a desktop, macOS or a launcher that bypasses `session.sh` never offers a setting that could not take effect. What is in use is the session's `XKB_DEFAULT_LAYOUT` / `_VARIANT` / `_MODEL` / `_OPTIONS` (absent reads as empty); while the saved setting resolves to anything else, the section shows a caution and Restart OpenFollow: the layout still in use when the layout or variant differ, else that the system keyboard settings change on restart (the system file's model or options changed since the session started). A failed Variant reload falls back to Standard. The Variant picker reloads with the layout, and Save is disabled for the reload, so a save never pairs the new layout with the previous one's variant. Both fields are device-local in every section whose config is `controller` (`controller`, `gamepad`, `keyboard`, `mouse`, derived from `_SECTION_CONFIG_ATTRS`) and not in `_DEVICE_IDENTITY_FIELDS`, so Restore defaults returns them to System. The diagnostics bundle's E3 row adds the session's model and options to what is in use, and prints each value as is when it is a plain xkb name, else escaped, so it stays one row.

## Speed adjustment (`adjust_move_speed`)
- **Marker:** base step 0.1 m/s (≤4.0), 0.5 m/s (>4.0), clamped to configurable `min_speed`–`max_speed` (defaults 0.1–3.0)
- **Streak acceleration:** ×1 (0–4 presses), ×3 (5–9), ×8 (10+), resets after 0.75s
- Both keyboard `key_speed_up`/`key_speed_down` and controller LB/RB call the same method

## Mouse (`input/mouse.py`)
Left-click on a marker's **ground circle** grabs that marker (hit-tested against the projected ground-circle polygon, with a pixel-radius fallback when the circle is off/tiny; nearest centre wins on overlap); right-click releases. A grab seeds the glide at the marker's current position – it never yanks the marker to the click – and a click on empty stage is a no-op. While held, pointer moves record a target (after the `mouse_hysteresis_px` pixel deadband, and rejecting targets past the `mouse_max_y` upstage cap); the marker is steered by `MouseHandler.update()`, called every frame from `InputManager.update`, which EMA-glides it toward the target at `mouse_smoothing` (0 = instant, higher = smoother; glide alpha = `1 - mouse_smoothing`, floored so the max never freezes). Positions are unprojected onto the stage floor plane (via `scene/solver.unproject_to_plane`) in the one PSN-absolute frame. The ground-circle ring geometry is shared with the overlay via `scene/solver.ground_circle_world_ring`. Scroll wheel adjusts Z when `mouse_wheel_z_enabled`, by `mouse_wheel_z_step` m per tick, sign flipped by `mouse_wheel_invert`. One tick is one wheel click, and on both platforms the event counts, not its size: libinput sends one event per click whose magnitude is an arbitrary per-device scale, and quartz sizes a click by how fast the wheel turned (scroll acceleration: ~13 slow, ~103 fast), so each quartz event is at least one click and only a fast flick's batched event, a multiple of `_SCROLL_UNIT_QUARTZ`, counts as several. Reading the quartz magnitude as distance makes the same click move Z eightfold more when turned fast. `MouseHandler._take_wheel_clicks` then rate-limits in whole clicks (`_WHEEL_BURST_CLICKS` at once, `_WHEEL_CLICKS_PER_S` after that) and drops the excess rather than queueing it: a free-spinning wheel (SmartShift) reports hundreds of clicks for one flick, which would otherwise send a marker tens of metres up. A trackpad sends many small events per swipe, each counted as a click, so it is not a wheel-Z input. Double **right-clicking** (two right-clicks within `_DOUBLE_CLICK_S`/`_DOUBLE_CLICK_PX`, detected in-handler via the injectable `_clock`) resets the selected marker to `_get_default_marker_position()` and **releases control** when `mouse_double_click_reset` is set. It lives on the right button because that's the release button, and a reset releases too (staying grabbed would snap the absolute marker straight back to the cursor on the next move). Left-click stays a pure grab.

**Event source per platform.** `MouseHandler`'s `on_pointer_*` entry points are fed by the GTK pointer signal handlers in [`window.py`](../../openfollow/window.py) (`_on_button_press`/`_on_motion`/`_on_scroll`). GTK doesn't reliably deliver pointer events to the gtksink-hosted window on **macOS** under the GStreamer pipeline (the same reason the keyboard polls Quartz instead of reading GTK key events), so on macOS the window also runs `poll_pointer()` once per frame from `_on_tick`: it reads `gdk_window.get_device_position()` (window-relative position + a button mask) and synthesises the same `pointer_down`/`pointer_move` events through `_emit`, so everything downstream is unchanged. Edge detection lives in the pure `_poll_pointer_events` helper. The scroll **wheel can't be polled** (no current-scroll-position API), so wheel-Z rides the GTK `scroll-event`, which *does* arrive on macOS – measured under both SDL video drivers. It is **button** events that never arrive there, and motion that arrives intermittently; that is what the poll covers. The poll is a no-op on Linux/Pi, where the GTK events work.

## Gamepad (`input/gamepad.py`)
Gamepad input runs on **pygame-ce** (it installs the `pygame` module). The classic `pygame` distribution installs the same module, so the two must not share an environment: `GamepadHandler` logs a WARNING at startup when classic pygame is what loaded, and the diagnostics bundle names the loaded build and its SDL version. 3Dconnexion pucks are kept out of SDL's joystick API with `SDL_JOYSTICK_BLACKLIST_DEVICES`, built from `_SPACEMOUSE_IDS` (the 3D Mouse subsystem drives them; SDL would bind one as a gamepad). The list holds exact VID/PID pairs because SDL's blacklist has no ranges, and it never blocks Logitech's `0x046d` wholesale, since Logitech's own gamepads share that vendor ID. An explicit value in the environment wins. `SDL_NO_SIGNAL_HANDLERS=1` keeps SDL from installing its own SIGINT/SIGTERM handler at `pygame.init()`, which would swallow a stop until the GLib main loop takes the signals over. The blacklist needs SDL 2.30 or later, so `_open_device` also skips any index `_is_spacemouse` reports (vendor and product read with `SDL_JoystickGetDeviceVendor` / `Product` through pygame's own SDL, without opening the device): the rule holds even when a stray classic pygame loads an SDL that ignores the blacklist. All bindings are `ControllerConfig` fields.

**Hotplug touches one device.** Every per-device map (`joysticks`, `controllers`, `capabilities`, edge state, trigger baselines, stick priming) is keyed by the SDL **instance id**, which is stable while the device stays attached. A `JOYDEVICEADDED` / `JOYDEVICEREMOVED` makes `_apply_hotplug` match the open set to SDL's current device list by instance id: an id SDL no longer lists is closed, a listed id not yet open is opened, and a device that stayed attached is never closed, reopened or reset. The event's own index is not trusted, SDL's announcement of the devices attached at startup is a no-op, and the game-controller duplicates of those events are consumed, not acted on. The ids come from `_device_instance_id` (`SDL_JoystickGetDeviceInstanceID`, looked up through pygame's own joystick module so it resolves in pygame's SDL rather than OpenCV's copy); a test pins that the lookup resolves. A full `_detect_controllers` rescan runs only for the first enumeration, the no-controllers retry, and a hotplug when that lookup is unavailable; it keeps the state of every device that reopens, prunes the rest, and publishes the new maps in one swap so the web and OSC threads never read a partial set. Never call `pygame.joystick.Joystick(i)` for a device that is already open: pygame opens it again inside SDL before returning its cached object, so each such call leaks an SDL reference. A failed open releases only what it created, never an object already registered.

## Gamepad → marker routing (`InputManager._gamepad_marker_id`)
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

Never index `app._controlled_ids[controller_idx]` directly in a consumer (`controller_idx` is the gamepad handler's key, the SDL
instance id, not a slot) – route through `_gamepad_marker_id` (or the injected
`marker_resolver` inside `GamepadHandler`) so HUD, speed, movement, and
reset stay in sync.

## Controller slots (`input/controller_slots.py`, `input/controller_identity.py`)
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

## 3D Mouse status (`input/mouse3d_status.py`)
`Mouse3DManager.status()` reports what the supervisor already holds and never enumerates or imports: the backend is `ok`, `not_installed`, or `could_not_start` (enumeration raised; easyhid without libhidapi binds the interpreter and raises `AttributeError`), and every attached puck is `open`, `opening`, `no_profile`, `not_permitted` or `open_failed`. Unprofiled pucks are enumerated and reported but never opened. hidapi drops the errno of a refused open, so `_PySpaceMouseBackend.open` checks `os.access` on the node and raises `PermissionError`. `/api/stats` publishes the block as `mouse3d` (its values are a wire interface), the bundle renders it as E10, and the web section shows only faults: its poll answers 204 while `status_key` is unchanged, so a `role="alert"` box is inserted once per change. **macOS is unsupported by platform** (`_platform_supported`): the real backend never starts there and the section shows one Info line; an injected backend bypasses the gate, as it bypasses the dependency check. Lift the gate with the pyspacemouse release whose `open_by_path` accepts macOS hidapi paths.

## One input, one action (`binding_conflicts.py`)
Within a binding group no two actions share an input: the gamepad's normal-mode actions (held Z and speed included), its menu Confirm / Cancel, the keyboard's action keys, and the 3D Mouse's buttons. Menu buttons are only read while a menu is open, so the two gamepad groups may share a button; devices never conflict. The groups are the `*_FORM_LABELS` constants in `configuration.py`, in **form order**, and `tests/test_web_binding_forms.py` pins the templates to it, because form order decides who keeps a shared input: `settle_bindings` (run by `__post_init__`) leaves it on the upper field and unbinds the rest with a WARNING. `apply_section_data` passes the fields a write changed as `prefer`, so a save or API write keeps what it changed. Move X/Y has no unbound value and always keeps its stick; a marker-fader stick on it is unbound. Menu Confirm / Cancel never take D-Pad Up / Down, which move the highlight.

On the form the validate route decides (`web/bindings.check_binding`) and `static/js/binding-steal.js` only applies its `bindingChecked` event: the field that lost its input empties and gets `data-binding-lost` (red, never `aria-invalid`, so Save stays open). An OSC hotkey / controller-button trigger on an action's input is a caution, not a conflict (`osc_trigger_overlap`): it can be deliberate.

## Speed per marker (`AppConfig.marker_move_speeds`)
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

## Marker card → controller binding
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

## OSC input allowlist
`OscConfig.allowed_sender_ips` drives a `verify_request` filter in `_FilteredOSCUDPServer`:
- Empty allowlist = allow-all + loud WARNING at `start()`.
- Non-empty allowlist = drop packets from non-listed IPs (DEBUG-level drop log, rate-limit-friendly).
- `__post_init__` on `OscConfig` normalises the field to `list[str]` so a hand-edited TOML can't feed a bare string or non-string entries into the runtime filter / web UI / HMAC flow.
- `_as_ip_list` parser **fails closed**: a submission of `"192.168.1.999"` (all-invalid) preserves the existing value rather than becoming `[]` and silently disabling the filter. Explicit empty (`""`, `[]`, whitespace-only CSV) still clears.

## OSC position write logging (`InputManager.update`, `restart_osc`)
`OscMarkerAdapter.flush_updates` merges every message since the last frame, so each frame applies at most one write per marker, traced at DEBUG only. One INFO line per marker marks the start of a stream: its first applied write, and the first after `_OSC_STREAM_GAP_S` without one. `restart_osc` resets the per-marker times, so a stream on a rebuilt listener logs its start again. A write for a marker the station does not control, or cannot resolve, is dropped before either line.

## Touch remote (`input/touch_remote.py`, `InputManager.update_touch_remote`)
A setup tool in the web UI: a browser session holds one controlled marker and drives it. Web request threads write the thread-safe `TouchRemote` registry (`app._touch_remote`) through `/api/remote/*`; `animate` calls `InputManager.update_touch_remote` every frame after `update_paths`, outside `process_input`, so an open on-screen menu never stops a held marker's dead-man.
- **A hold is exclusive.** While `holds(app, id)`, every other writer skips that marker: keyboard, gamepad move / reset / Pathfinder, the Reset key, 3D mouse move / reset / Pathfinder (its speed steps and fader stay with the puck, like a gamepad's), 2D mouse grab and wheel, OSC positions and OSC path commands. A new writer of marker positions checks `holds` too.
- **Newest wins.** A second session's `hold` takes the marker; the first learns it from `status` / a 409 and sends nothing more.
- **Dead-man.** A stick older than `DRIVE_STALE_S` (0.25 s) reads as centred; a session quiet for `HOLD_STALE_S` (10 s) is forgotten and lets go. Closing the card lets go at once.
- **Relative input.** `stick_velocity` shapes the deflection by `[touch_remote] stick_curve` / `stick_dead_zone` up to the station's `marker.max_speed` (never a marker's own speed), then `_oriented` applies `invert_control_direction`; the move goes through `_move`, so a locked marker walks its path. Z notches move line by line along the `z_step_m` grid (`snap_z`), within `MAX_REACH_M` of the origin, and are ignored on a path. A glide releases the path, then steps toward its target at `marker.max_speed` until it lands or the remote steers; the dead-man holds it where it is until drive messages arrive again.
- **Detection.** A hold while `detection.enabled` saves detection off (`save_config_change`) and stops the detector live; nothing turns it back on.
- `touch_remote.enabled` off, or a marker leaving `controlled_marker_ids`, lets go. The Operator Screen shows the caution row `touch_remote` while anything is held.

## macOS vs Pi differences
- **macOS:** Quartz keyboard polling, per-frame GDK pointer polling (`window.poll_pointer`; under the pipeline GTK button events never arrive and motion arrives intermittently, while scroll events do arrive, so wheel-Z works, counted per wheel click and rate-limited as described under Mouse), NDI via libndi dylib, SDL's HIDAPI joystick backend switched off (`SDL_JOYSTICK_HIDAPI=0` in `GamepadHandler`: SDL before 2.32.6 kept a closed device's HID callback registered, so a reopen turned input into a use-after-free; pygame-ce bundles a fixed SDL, and the backend stays off until pads are verified on it)
- **Pi:** GTK event-based keyboard + GTK pointer/scroll events, NDI via ARM libndi, Cage compositor, systemd service
- `gst_runtime_available()` checks GStreamer at runtime
