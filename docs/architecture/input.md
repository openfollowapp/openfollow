# Input

Covers `input/`, `binding_conflicts.py`, the OSC listener. The rules that apply everywhere are in [CLAUDE.md](../../CLAUDE.md).

## Keyboard (`input/keyboard.py`)
**macOS:** Quartz `CGEventSourceKeyState` – hardware polling, reliable under GStreamer load.
**Linux:** `LinuxEvdevKeyboardPoller` reads the kernel's held-key bitmap (`EVIOCGKEY`, python-evdev `active_keys()`): no event queue, so a key-up GTK drops under load cannot strand a key. It unions every evdev device whose `EV_KEY` capabilities include `KEY_A` (world-readable sysfs, `/sys/class/input/eventN/device/capabilities/key`, rules a node out before it is opened; a node sysfs cannot classify is opened and asked), behind a 4 ms snapshot so one frame's many `is_key_pressed` calls share one read; a device that raises `OSError` (unplugged) is dropped and the others keep their keys. The device list is rescanned every second, so a keyboard plugged in after a headless boot is read within a second; a node sysfs cannot classify that turns out not to be a keyboard is opened once per inode, and any node python-evdev cannot read (a device name that is not UTF-8) is skipped once per inode with one WARNING. A modifier reads both sides, and Enter also reads keypad Enter, as GTK reports both as one key. **Available** means evdev imports and every `/dev/input/event*` node that is, or may be, a keyboard is open to the service user for reading and writing (the `input` group; `evdev.list_devices` lists nothing less), not that a keyboard is attached: the poller is fixed when `KeyboardHandler` starts. The HUD keyboard indicator follows the poller's device list; the `/proc` probe serves the fallback only. On a Linux desktop where the user is in `input`, the poller reads keys typed into other windows too, as evdev knows no focus.
**Letters follow the layout in use.** evdev reports physical positions (on QWERTZ the key printed `Z` is `KEY_Y`), so the poller compiles the keymap with libxkbcommon and moves each letter of `POLLED_KEYS` to the key whose level-0 keysym in the first layout is that letter (`evdev code = xkb keycode - 8`, lowest keycode wins); every other key keeps its US position. The names are the keymap Cage compiled: the session's `XKB_DEFAULT_LAYOUT` / `_VARIANT` / `_MODEL` / `_OPTIONS` (see the environment contract below), and only when the session exported none `/etc/default/keyboard`, then `us`. Never `controller.keyboard_layout` and never the system file ahead of the environment: a saved layout applies at the next restart, and the system file is only the session's fallback, so either would poll another layout than GTK and the compositor type in. Any failure (no xkbcommon, a keymap that does not compile, no letters) keeps the US positions with a WARNING; success logs the layout and its source at INFO.
**Fallback:** GTK event-based tracking (`FallbackKeyboardPoller`) on any other platform, on a Linux host where evdev is missing, the keyboards are closed to the user or the evdev poller raises at start, and when Quartz fails to load. Typing (the URL editor, the Pi-network field editor, the browser overlay) rides GTK key events whatever the poller: a key-state poller has no repeat and reports positions, not characters. A hardware poller (evdev, Quartz) still sees the keys typed there, so `KeyboardHandler` is gated while `text_entry_owns_input` holds, the same test the GTK path uses to stop forwarding keys: `keys` reports nothing held and no press edge or bus `KeyEvent` is produced, and every key held while gated, or as the screen closes (the Escape or Enter whose GTK event closed it), stays invisible until released, so it cannot act on the screen underneath. The opening side mirrors it: the poller sees a key before GTK delivers its event, so the press that opened a text screen can reach `on_key_down` after it is open. A key held at the last poll before the screen opened is a late echo until its GTK key_up or the poller sees it released: `on_key_down` drops it and marks the event, and the window keeps a marked event from the browser overlay's WebView. `KeyEvent.modifiers` always stamp what is physically held, a swallowed Shift included. The fallback is not gated; its GTK path already is.

`/api/stats` publishes `keyboard` (a wire interface): `poller` is `evdev`, `quartz` or `fallback` (`null` before input starts) and `devices` the evdev keyboard count (`null` for the other pollers). The diagnostics bundle's A3 section prints both, so a station silently on the fallback shows in a support thread.

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
Gamepad input runs on **pygame-ce** (it installs the `pygame` module). The classic `pygame` distribution installs the same module, so the two must not share an environment: `GamepadHandler` logs a WARNING at startup when classic pygame is what loaded, and the diagnostics bundle names the loaded build and its SDL version. 3Dconnexion pucks are kept out of SDL's joystick API with `SDL_JOYSTICK_BLACKLIST_DEVICES`, built from `_SPACEMOUSE_IDS` (the 3D Mouse subsystem drives them; SDL would bind one as a gamepad). The list holds exact VID/PID pairs because SDL's blacklist has no ranges, and it never blocks Logitech's `0x046d` wholesale, since Logitech's own gamepads share that vendor ID. An explicit value in the environment wins. `GamepadHandler` starts only the SDL subsystems pads need, never `pygame.init()`, which keeps an SDL timer thread and, through the mixer, an audio thread running all show: `pygame.display.init()` (pygame's event API needs the video subsystem; `SDL_VIDEODRIVER=dummy`), `pygame.joystick.init()` and the game-controller module; `stop()` quits them in reverse. `SDL_NO_SIGNAL_HANDLERS=1` keeps SDL from installing its own SIGINT/SIGTERM handler when the display start brings up its event subsystem, which would swallow a stop until the GLib main loop takes the signals over. The blacklist needs SDL 2.30 or later, so `_open_device` also skips any index `_is_spacemouse` reports (vendor and product read with `SDL_JoystickGetDeviceVendor` / `Product` through pygame's own SDL, without opening the device): the rule holds even when a stray classic pygame loads an SDL that ignores the blacklist. It also skips a device whose SDL name (`SDL_JoystickNameForIndex`, read the same way) is `System Control`, `Consumer Control` or `Wireless Radio Control`, alone or after a space (surrounding whitespace ignored): Linux hid-input splits a keyboard's power, media and radio keys into devices named after those collections, SDL 2.32 can list one as a joystick, and a pad's own collection is never suffixed. All bindings are `ControllerConfig` fields.

**Hotplug touches one device.** Every per-device map (`joysticks`, `controllers`, `capabilities`, edge state, trigger baselines, stick priming) is keyed by the SDL **instance id**, which is stable while the device stays attached. A `JOYDEVICEADDED` / `JOYDEVICEREMOVED` makes `_apply_hotplug` match the open set to SDL's current device list by instance id: an id SDL no longer lists is closed, a listed id not yet open is opened, and a device that stayed attached is never closed, reopened or reset. The event's own index is not trusted, SDL's announcement of the devices attached at startup is a no-op, and the game-controller duplicates of those events are consumed, not acted on. The ids come from `_device_instance_id` (`SDL_JoystickGetDeviceInstanceID`, looked up through pygame's own joystick module so it resolves in pygame's SDL rather than OpenCV's copy); a test pins that the lookup resolves. A full `_detect_controllers` rescan runs only for the first enumeration, the no-controllers retry, and a hotplug when that lookup is unavailable; it keeps the state of every device that reopens, prunes the rest, and publishes the new maps in one swap so the web and OSC threads never read a partial set. Never call `pygame.joystick.Joystick(i)` for a device that is already open: pygame opens it again inside SDL before returning its cached object, so each such call leaks an SDL reference. A failed open releases only what it created, never an object already registered.

## Control frame (`input/control_frame.py`)

Relative input (keyboard, each gamepad, each 3D mouse) arrives as (right, up) and leaves `ControlFrame.orient` as a stage (X, Y) velocity of the same magnitude; Z passes through untouched, and the 2D mouse and OSC never go through it (they are absolute). `marker.control_frame` picks the two stage directions: `stage` is the identity; `heading` is `scene/solver.bearing_axes(control_heading)`, the floor heading of a camera with that yaw, so for a level camera (roll 0) a heading equal to its yaw gives the same result as `camera`; `camera` is `scene/solver.ground_axes` (right is the camera's right axis flattened onto the floor and forward a quarter turn left of it, so a camera with no roll keeps its yaw bearing at any pitch; as roll stands the right axis up, the flattened view axis takes over forward by the share it lost, so a level camera keeps its bearing up to 120° of roll, and an upside-down one steers as its picture shows: no continuous rule can give both a level and a top-down upside-down camera their natural forward, so the turn between them falls among upside-down poses); `screen` is `scene/solver.screen_axes_at`, the stage directions that move the marker **ball** right and up the lens-distorted picture, measured on the plane at the marker's own Z over a 1 % canvas-height step. Screen axes need not be perpendicular, so the result is rescaled to the input's length. The picture size is `operator_screen_size(app)`, which the mouse shares: the window's allocation, the requested size without a window, and `(0, 0)` when GTK cannot read it. Where `screen_axes_at` has no answer (ball off the picture or behind the camera, a step past the horizon, the ball's plane not below the camera) or the canvas has no size, the move takes `camera`; `camera` with no camera object takes `stage`.

**Latch.** Each source, keyed with the marker it steers (`("keyboard", marker_id)`, `("gamepad", idx, marker_id)`, `("mouse3d", idx, marker_id)`), keeps the axes it took when its XY input left zero until that input returns to zero, or the source stops reporting for a frame (`begin_frame` prunes it at the top of `InputManager.update`); input returning from a modal (the same transition that clears held keys in `app_modes.process_input`) drops every latch via `release_all`. So a recalibration, a source swap or a web save never bends a move in progress, a marker newly taken over mid-hold gets its own directions, and in `screen` mode a held direction is a straight stage line. The setting is read per latch, so it applies live with no restart.

## Failure isolation (`InputManager.update`, `update_touch_remote`)
The 3D Mouse, mouse, MIDI hotplug, keyboard and OSC blocks of `InputManager.update` each run under their own guard, so one that raises never drops the gamepad result or the blocks after it. Inside a block each device or marker has its own guard, so one never costs the others their frame: each gamepad's move, each 3D Mouse puck's `_apply_mouse3d`, each OSC marker's write, and in `update_touch_remote` each held marker. These guards log through a `ThrottledExceptionLogger` (one traceback per interval, with a count of the rest), never a bare `logger.exception`; the MIDI hotplug poll throttles itself to its poll interval. A step that raises keeps what it already applied (a puck's button edges folded into `GamepadUpdate`, speed steps taken) and loses the rest of that device's frame; nothing is retried.

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

A slot whose marker is parked resolves to `None` here (see Parked markers below).

Never index `app._controlled_ids[controller_idx]` directly in a consumer (`controller_idx` is the gamepad handler's key, the SDL
instance id, not a slot) – route through `_gamepad_marker_id` (or the injected
`marker_resolver` inside `GamepadHandler`) so HUD, speed, movement, and
reset stay in sync.

## Parked markers (`input/parking.py`)
A parked marker is a controlled marker no relative input moves; it stays on every output (the per-frame `set_speed` in `build_marker_visual_state` still stamps it) and in the trigger zones. `app._parked_ids` is the live set, mirrored in `AppConfig.parked_marker_ids`, and `is_parked(app, id)` is the one predicate every routing site reads. `set_parked` is the one writer (startup, hot reload, OSC):
- **Selection never rests on a parked marker.** `rehome_selection` moves it to the next unparked marker in `_controlled_ids` order, or to `None` when every one is parked; unparking with nothing selected selects the first unparked marker. Next/prev cycling and the digit keys step over parked markers, and the keyboard's move, Reset and Pathfinder skip one.
- **A slot whose marker is parked idles.** `_controller_marker_id` returns `None` for it, so movement, reset, Pathfinder, bumper speed, the marker fader and the 3D mouse all skip it; slot order is unchanged. `_slot_marker_id` is the same resolution without the park: `controller_marker_id` (OSC `cN`), `get_controller_info` (which adds `parked`) and Identify use it, so a parked slot keeps its marker everywhere it is shown or sent.
- **The 2D mouse, OSC and the touch remote still move a parked marker**: each names one marker explicitly. A mouse grab of a parked marker steers it through `MouseHandler._parked_grab` and leaves `_selected_id` alone (double right-click reset and wheel-Z follow the grab); parking the marker a drag holds keeps the drag on it (`keep_grab`). OSC positions apply as for any controlled marker.
- **The web P toggle** posts only the clicked row's change (`park: {"<id>": true|false}` on `/api/markers/selection`), applied to the parked set read from the file under the config write lock, so a park OSC saved since the page last polled survives an unrelated selection save.
- **OSC** `/marker/<id>/park` and `/marker/<id>/unpark` ignore their arguments, are queued by `OscMarkerAdapter` and applied at the top of `InputManager.update`, ignored for a marker this station does not control, and saved with `save_park_changes`: one read-modify-write under the config write lock that applies the change to the file's parked set within the file's controlled set, so a web park the running config has not reloaded yet survives.

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
- an arrival in a pinned socket takes the pinned slot first;
- **Forget** (web) makes a missing slot **reserved**: it keeps its place,
  drives no marker, raises no warning;
- switching gamepads or the 3D mouse off leaves that kind's slots missing,
  alarms included, and nobody else moves; switched back on, each device
  reclaims its own slot. A kind that is off at startup holds no slots.

**Pins** (`ControllerConfig.slot_pins`, 1-based `SlotPinConfig(slot, key,
kind, label)`) bind a socket to a slot: whatever is plugged into it takes the
slot, matched by key only; `kind` and `label` are what was pinned, shown while
the socket is empty (the slot is then missing). Seeding places pinned sockets
first and fills the rest in port order around them. A pin above the occupied
slots leaves **empty** placeholders below it: silent, no marker, taken by an
arrival of any kind, trailing ones trimmed, and not counted by the
single-controller rule. `_vacant_for` never hands a pinned index to another
socket. `pin()` swaps: the displaced entry takes the pressed controller's old
index and its pin along; `unpin()` moves nothing. A hand-edited change reaches
`set_pins` through the controller block of `apply_runtime_config_changes`.

**Pin here** is an exclusive mode like Button Detection: while
`InputManager.pin_capture_active`, `process_input` runs only
`process_slot_pin_capture` (`update()` is skipped, the key and pointer
handlers stand down), for up to `CAPTURE_TIMEOUT_S`. `PinCapture`
(`input/slot_pin_capture.py`) reads pads as raw buttons + hats against a
baseline taken at arm (a held button is not a press) and pucks as latched
presses drained at arm, then waits for the release, so the press reaches no
other consumer; on end the pads' edge state is resynced and puck presses
drained. The result (`pinned` / `no_port` / `no_slot` / `timeout` /
`cancelled`, a wire interface in `/api/stats` `controllers.pin_capture`) is
saved through `save_slot_pins` into `config.toml` and `app._config`, so the
reload watcher sees no change. The HUD prompt is an operator message raised
with `from_app=True`, drawn even with `[operator_messages] enabled = false`
(that switch gates OSC ingest), and retracted by its `seq` when the capture
ends. Pins are device-local: in `_DEVICE_IDENTITY_FIELDS`, stripped from
export and section broadcast.

Without pins, every start numbers by socket again. 3D mice carry
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

## 3D Mouse read loop (`Mouse3DHandler._pump`)
pyspacemouse's `read()` is non-blocking, never returns `None`, and hands back the same state object every time: a HID report updates only the fields its channel carries (the axes in one or two channels by model, the buttons in another) and restamps `t` (`-1.0` before the first report). An unchanged `t` is therefore a silent read, and only a new stamp publishes a snapshot. A puck reports only while deflected (measured on a SpaceNavigator): at rest it is silent, held off-centre it reports about every 8 ms even with the values still, and on release it reports down to an all-zero reading. So `_RECENTER_AFTER_IDLE_S` of silence re-zeroes a non-zero snapshot (a lost return-to-centre report), and the zeroed axes stay at rest until a report changes their value, since a later report of another channel (a button press) restamps the state with the stale axes still in it. Any successful read counts as a working connection for the reconnect backoff.

## 3D Mouse recognition (`input/mouse3d.py`)
A puck is recognised by the HID usage it declares, not by an ID list. hidapi (0.14, as Debian trixie ships it) lists one entry per top-level collection, so one hidraw node shows up several times (a SpaceNavigator: Generic Desktop / Multi-axis Controller, usage 1/8, plus two vendor-defined collections). `_PySpaceMouseBackend.enumerate` groups the entries by node and keeps a node when **any** of its entries declares 1/8 under a 3Dconnexion VID/PID: vendor `0x256f`, or Logitech `0x046d` within `0xc600`-`0xc6ff`. A CadMouse (Mouse, 1/2), the 3Dconnexion keyboards and numpads (Keyboard, 1/6) and another vendor's multi-axis device are dropped without an exclusion list, and the Universal Receiver's mouse interface, its own node, is skipped while its multi-axis node is kept. A node whose every entry reports usage 0/0 (older hidapi builds) is kept only when its VID/PID has a device profile; a vendor range never admits a device on its own. A node that is not a puck is never logged.

Readable is a separate question (`Mouse3DDeviceInfo.profiled`). A puck is opened only when it has a device profile: pyspacemouse's built-in table, or a local profile `_device_profiles` builds for a model pyspacemouse lacks by copying a sibling's spec under the model's own IDs and name (siblings from Blender's NDOF table and spacenavd: the same internals in a different housing):

| Model | VID/PID | Read with the profile of |
|---|---|---|
| SpaceNavigator for Notebooks | `046d:c628` | SpaceNavigator `046d:c626` |
| SpaceMouse Wireless receiver | `256f:c62f` | SpaceMouse Wireless `256f:c62e` |
| SpaceMouse Pro Wireless, cabled | `256f:c631` | SpaceMouse Pro Wireless `256f:c632` |
| SpaceMouse Module | `256f:c636` | SpaceMouse Compact `256f:c635` |

A built-in profile for the same ID always wins. Built-in specs are in pyspacemouse's LEGACY axis convention, which its built-in open applies unchanged, so `open` passes a copied spec as `open_by_path(path, device_spec=...)` and it reads exactly like its sibling; a built-in model is opened without `device_spec`. `open` picks the spec from the IDs of the device at the node at that moment, never from an earlier enumeration: a custom spec skips pyspacemouse's own ID check, and a hidraw node can pass to another device between two passes. A puck with no profile is named once in the log (`_note_unprofiled`) and reported as `no_profile`, never opened. When the built-in table cannot be read, every declared puck counts as profiled and is tried, so a broken table shows as an open error rather than a missing puck.

## 3D Mouse status (`input/mouse3d_status.py`)
`Mouse3DManager.status()` reports what the supervisor already holds and never enumerates or imports: the backend is `ok`, `not_installed`, or `could_not_start` (enumeration raised; easyhid without libhidapi binds the interpreter and raises `AttributeError`), and every attached puck is `open`, `opening`, `no_profile`, `not_permitted` or `open_failed`. Unprofiled pucks are enumerated and reported but never opened. hidapi drops the errno of a refused open, so `_PySpaceMouseBackend.open` checks `os.access` on the node and raises `PermissionError`. `/api/stats` publishes the block as `mouse3d` (its values are a wire interface), the bundle renders it as E10, and the web section shows only faults: its poll answers 204 while `status_key` is unchanged, so a `role="alert"` box is inserted once per change. **macOS is unsupported by platform** (`_platform_supported`): the real backend never starts there and the section shows one Info line; an injected backend bypasses the gate, as it bypasses the dependency check. Lift the gate with the pyspacemouse release whose `open_by_path` accepts macOS hidapi paths.

## MIDI device identity (`input/midi.py`)
mido names an ALSA sequencer port `<client>:<port> <client number>:<port number>`. The numbers change across restarts, so a port's identity is its name without them (`_normalize_port_name`), and `raw_port_name` is only what `open_input` takes.

On Linux a port of a USB sound card also carries the serial number of its USB device, read in three steps, all from sysfs and never written:
1. **Client to card.** The kernel gives sound card N the sequencer clients 16 + 4N to 16 + 4N + 3, so the client number names the card. Below 16 are global clients (Midi Through); from 128 on, clients are numbered on demand (applications, cards past the 28th) and name no card.
2. **Card to USB device.** `/sys/class/sound/cardN/device` resolves to a USB interface; `controller_identity.usb_serial` walks up it to the USB device, the same walk `resolve_key` uses for a controller's socket.
3. **USB device to serial.** The device's `serial` file, control characters removed.

Every card and port of one device carries its serial. A device without a serial descriptor, a card that is not on USB, a missing or unreadable sysfs entry and every other platform give `serial=None`, and discovery never raises over it. Each card is read once per `discover()` and nothing is cached across calls: a replug can put another unit of the same model on the same card number. `discover()` runs on every MIDI config apply (a patch save, a hot reload that changes `[midi]`, a port-set change the hotplug poll sees), on every patch save's device lookup and on every render of the MIDI page; the hotplug poll itself only lists port names.

`match_patches` resolves the whole patch set at once, for `apply_config` and for the Device dropdown alike; the identifier is `serial:<serial>|<port name>`, so the ports of one device stay apart in the dropdown.
1. **Own unit.** A patch whose serial is connected takes that unit: the port of its name, else a port of the unit no other patch names, else the first.
2. **Fallback**, in patch-id order, by the `(port_name, product)` composite and then the port name alone, never onto a unit a patch names by serial (`claimed`). A unit taken this way stands in for one absent serial, all of that serial's patches and no other, so with two absent units and one spare the lower patch id gets the spare and the other counts as missing (`midi_patch_missing`). Patches saved without a serial take a unit port by port, so a config saved without serials (on another station or platform) spreads over the units connected, and a unit they use is no spare. A device without a serial is shared.

The dropdown selects the device `match_patches` opens (`bound_identifiers`, built per patch id in the route for both the page and the section render), not only an identical key: a patch on a fallback never reads "(not connected)" while it works, and saving its row stores that unit's serial, while a patch whose unit is absent and has no spare reads "(not connected)" and keeps its own serial on save. Saving one patch of a device binds only that patch to the serial; the unit is then claimed, so each sibling patch still on a fallback (saved without a serial, or standing in on the spare) is unmatched and reads "(not connected)" until its row is saved too. Two units of one model swapped between two hotplug polls leave the port names identical, so nothing re-applies until the next port-set change.

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

The speeds are device-local: an export leaves them out, and an import
keeps this station's live ones (see [config](config.md#config-transfer-export--import--restore-defaults)).

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
- **IPv4 only**, the listener's one family: an IPv6 entry could never match a sender. The form refuses one (`_validate_ip_list`, with its own message) and `_as_ip_list` drops it.
- `__post_init__` on `OscConfig` normalises the field to `list[str]` so a hand-edited TOML can't feed a bare string or non-string entries into the runtime filter / web UI / HMAC flow, and keeps only the IPv4 entries. A list with none is kept as written, so it admits nobody; the listener logs that as a WARNING.
- `_as_ip_list` parser **fails closed**: a submission with no IPv4 entry (`"192.168.1.999"`, `"2001:db8::1"`) preserves the existing value rather than becoming `[]` and silently disabling the filter. Explicit empty (`""`, `[]`, whitespace-only CSV) still clears.

## OSC position write logging (`InputManager.update`, `restart_osc`)
`OscMarkerAdapter.flush_updates` merges every message since the last frame, so each frame applies at most one write per marker, traced at DEBUG only. One INFO line per marker marks the start of a stream: its first applied write, and the first after `_OSC_STREAM_GAP_S` without one. `restart_osc` and `resume_osc` (the network observer's retry of a listener that is not running, which raises while it cannot bind where `restart_osc` logs and carries on) reset the per-marker times, so a stream on a rebuilt listener logs its start again. A write for a marker the station does not control, or cannot resolve, is dropped before either line.

## Touch remote (`input/touch_remote.py`, `InputManager.update_touch_remote`)
A setup tool in the web UI: a browser session holds one controlled marker and drives it. Web request threads write the thread-safe `TouchRemote` registry (`app._touch_remote`) through `/api/remote/*`; `animate` calls `InputManager.update_touch_remote` every frame after `update_paths`, outside `process_input`, so an open on-screen menu never stops a held marker's dead-man.
- **A hold is exclusive.** While `holds(app, id)`, every other writer skips that marker: keyboard, gamepad move / reset / Pathfinder, the Reset key, 3D mouse move / reset / Pathfinder (its speed steps and fader stay with the puck, like a gamepad's), 2D mouse grab and wheel, OSC positions and OSC path commands, and the Fully Automatic detection pin. Under AI Assisted the remote, like any input, steers the assist anchor. A new writer of marker positions checks `holds` too.
- **Newest wins.** A second session's `hold` takes the marker; the first learns it from `status` / a 409 and sends nothing more.
- **Dead-man.** A stick older than `DRIVE_STALE_S` (0.25 s) reads as centred; a session quiet for `HOLD_STALE_S` (10 s) is forgotten and lets go. Closing the card lets go at once.
- **Relative input.** `stick_velocity` shapes the deflection by `[touch_remote] stick_curve` / `stick_dead_zone` up to the station's `marker.max_speed` (never a marker's own speed), in stage directions (the control frame does not apply: a tablet operator need not be watching the picture); the move goes through `_move`, so a locked marker walks its path. Z notches move line by line along the `z_step_m` grid (`snap_z`), within `MAX_REACH_M` of the origin, and are ignored on a path. A glide releases the path, then steps toward its target at `marker.max_speed` until it lands or the remote steers; the dead-man holds it where it is until drive messages arrive again.
- **Detection.** A new hold (`RemoteFrame.new`, set in the same `take` that hands out the frame) while `detection.enabled` switches detection off as a hot reload would: a new `DetectionConfig` with `enabled=False`, `apply_detection_change` through `_apply_with_fallback` (reverted on failure), then `save_config_fields(..., ("detection.enabled",))`. Once per hold, so detection switched back on during a hold stays on.
- `touch_remote.enabled` off, or a marker leaving `controlled_marker_ids`, lets go. The Operator Screen shows the caution row `touch_remote` while anything is held.

## macOS vs Pi differences
- **macOS:** Quartz keyboard polling, per-frame GDK pointer polling (`window.poll_pointer`; under the pipeline GTK button events never arrive and motion arrives intermittently, while scroll events do arrive, so wheel-Z works, counted per wheel click and rate-limited as described under Mouse), NDI via libndi dylib, SDL's HIDAPI joystick backend switched off (`SDL_JOYSTICK_HIDAPI=0` in `GamepadHandler`: SDL before 2.32.6 kept a closed device's HID callback registered, so a reopen turned input into a use-after-free; pygame-ce bundles a fixed SDL, and the backend stays off until pads are verified on it)
- **Pi:** GTK event-based keyboard + GTK pointer/scroll events, NDI via ARM libndi, Cage compositor, systemd service
- `gst_runtime_available()` checks GStreamer at runtime
