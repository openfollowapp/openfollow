# Plan: control frame for relative steering

Operators on relative controllers (gamepad, keyboard, 3D mouse) expect the
marker to move as seen from the camera, the way third-person games and a
SpaceMouse in Object mode behave. Today relative input drives stage axes, with a
single 180° flip (`marker.invert_control_direction`). A camera off to one side
has no fix at all.

This plan replaces the flip with a station-wide **control frame** of four modes.

## Modes

| Value | UI label | Stick right / stick up mean |
|---|---|---|
| `stage` (default) | Stage axes | +X (stage left) / +Y (upstage) |
| `heading` | Fixed heading | The floor heading of a camera whose yaw is `control_heading`, so for a level camera a heading equal to its yaw matches `camera` |
| `camera` | Camera heading | The calibrated camera's right axis / its view direction, both flattened onto the floor |
| `screen` | Screen | Right / up **in the picture at the marker ball**, at the marker's current Z |

Shared rules:

- **Z is never rotated.** Up is world up in every mode.
- **Speed is unchanged.** The world velocity has magnitude `|stick| * speed` m/s; a mode only picks the direction.
- **Direction latch.** Each source (keyboard, each gamepad, each 3D mouse) latches its 2×2 mapping when its XY input leaves zero and drops it when the input returns to zero. A recalibration, live source swap or edit of the setting never bends a move in progress. In `screen` mode a projective camera maps the picture line through the marker to a straight stage line, so the latch matches the picture without lens distortion.
- **Absolute inputs are untouched.** The 2D mouse and OSC already follow the picture.
- **Paths.** The mapping runs before `paths.move`, which still receives a world velocity.
- **Per-device feel stacks on top.** The gamepad's Stick Y axis and the 3D mouse's per-axis Direction are unchanged.

Geometry:

- `camera`: right `r` = the camera's right axis (column of `_rotation_matrix`) converted to PSN and projected onto XY; forward `f = (-r.y, r.x)`, plus the flattened view axis weighted by the length `r` lost to roll. Well defined at any pitch, including a top-down camera, and continuous through roll. On the default pose (yaw 0) this is the identity, the same as `stage`.
- `screen`: project the ball `(x, y, z)` to the picture (with lens distortion), offset by a small step along screen +x and screen −y, undo the distortion, unproject both onto the plane at the marker's Z, and normalise the two world directions. Use the canvas size the mouse uses (`MouseHandler._view_inputs`).
- `screen` with no answer (ball behind the camera or off the picture, unprojection NaN, plane seen edge-on): that latch uses the `camera` mapping. The help drawer documents this.

## Config

- `MarkerConfig.control_frame: str = "stage"`, `_coerce_choice` over the four values.
- `MarkerConfig.control_heading: float = 0.0` degrees in the camera-yaw convention, finite, wrapped into `[0, 360)` (`-90` is stored as `270`); a bool falls back to `0`.
- `invert_control_direction` is removed. A config file, config-API body or older peer that still carries it is read through `apply_renamed_marker_keys`: `true` with no `control_frame` becomes `heading` / `180`, `false` becomes `stage`; one warning per key, only when it applies. The current keys win when both are present.
- Both fields stay live: they are read per frame like the flag is today, with no restart.

## Checklist

### Geometry (`scene/`)
- [x] Add `ground_axes(params)` → `(right, forward)` unit XY vectors from the camera pose.
- [x] Add `screen_axes_at(params, point, canvas_w, canvas_h, k1, k2)` → `(right, up)` unit XY vectors, or `None`.
- [x] Tests: default pose = identity; yaw 90 / 180 / 270; top-down pitch −90; level pitch 0; roll; `screen` at the picture centre agrees with `camera` for roll 0; off-picture / behind-camera / edge-on plane → `None`; lens distortion applied.

### Input mapping (`input/`)
- [x] Replace `_oriented` with a control-frame mapper that owns the per-source latch and is called once per source in `InputManager.update` (keyboard, gamepad, 3D mouse).
- [x] Never touch Z; keep the magnitude `|stick| * speed`.
- [x] Tests in `tests/test_input_manager.py`: each mode for each relative device; Z unchanged; latch holds across a camera edit until the stick centres; `screen` fallback to `camera`; a marker locked on a path still receives a world velocity; mouse and OSC unaffected.

### Config (`configuration.py`, `add-config-field` skill)
- [x] Add both fields with `__post_init__` coercion; remove `invert_control_direction`.
- [x] Extend `apply_renamed_marker_keys` with the bool → `heading` / `180` mapping and its warning.
- [x] Tests in `tests/test_configuration.py`: valid values; enum mismatch; wrong types (`None`, `"abc"`, `True`) per field; heading out of range, NaN and inf; the old key `true` / `false`; both old and new keys present; hot-reload applies without a restart.

### Web (`movement` section)
- [x] `partials/movement.tpl`: replace the Control direction select with **Control frame** (four options) and a **Heading (°)** number input with the standard validate markup, shown for Fixed heading only.
- [x] `routes.py` parsing and a `FIELD_RULES` entry for each field in `web/validation.py`, matching the server bounds.
- [x] Tests: form → config → mapper in `tests/test_web_helpers.py`; a crafted `POST /section/movement` with bad values; `tests/test_template_validation_consistency.py` passes; update the existing tests in `tests/test_web_server.py`, `tests/test_input_mouse.py` and `tests/test_input_mouse3d.py` that set the removed field.

### Docs and copy
- [x] `openfollow/web/help/movement.md`: rewrite the Control direction section as Control frame (the four modes, when to pick each, the latch, the `screen` fallback).
- [x] `openfollow/web/help/mouse3d.md`: point the upstage-camera note at Control frame.
- [x] `config.example.toml`, `docs/architecture/config.md` (sub-config note) and `docs/architecture/input.md` (a Control frame section).
- [x] `CLAUDE.md` Input invariant: the control frame is applied once in `InputManager.update`, on relative input only, never on Z.

### Verify
- [x] `make ci-remote` green (run locally; no testing Pi reachable).
- [ ] Run the app with a side camera (yaw 90) and a downstage camera (yaw 180) on the Media Gallery source: in `camera` and `screen`, stick right moves the marker right on the picture; in `stage` nothing changed.

## Out of scope

- Per-controller-slot frames: the setting is station-wide so every device agrees.
- Marker-relative ("tank") control: a marker has no heading.
- A HUD indicator of the active frame.
