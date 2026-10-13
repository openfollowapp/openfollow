# Plan: touch marker remote

A setup tool in the web UI: from a tablet, drive one of this station's controlled
markers by a virtual stick and Z wheel, so a person standing on a known spot can
focus fixtures on themselves. Experimental paths are supported (Pathfinder).
Built on `feat/paths-editor`, merging after it.

Feel prototype (simulated stage, no station): the artifact linked from the PR.

## Decisions

| Topic | Decision |
|---|---|
| Layout | Compact card (~340 px) docked to the bottom corner of the thumb's side. Hand toggle mirrors it, remembered per device (`localStorage`). |
| Launcher | Round button, bottom corner, shown in every browser while the station allows the remote. |
| Gate | `touch_remote.enabled`, device-local, off by default. Off ⇒ no launcher, every remote route answers 403. |
| Picker | Chips for `controlled_marker_ids` only, with catalog name and colour. |
| Arbitration | While the card is open, the remote **holds** the picked marker: gamepad, keyboard, 3D mouse, 2D mouse, OSC positions, OSC path commands and the detection pin skip it. Closing the card releases. |
| Two remotes | The newest pick steals the hold; the earlier card shows "Taken by another remote" (error level) and stops sending. |
| Stick | Rate control. Deflection past the dead zone, shaped by the curve, times the station's `marker.max_speed` (`MarkerConfig.max_speed`), never a marker's own adjusted speed. `invert_control_direction` applied once in `InputManager.update`, as for every relative input. A per-device "facing FOH" flip turns the stick 180° on the client. |
| Dead-man | No drive message for 250 ms ⇒ velocity zero. No heartbeat for 2 s ⇒ hold released (the marker stays where it stopped). |
| Z | Detented drum: one notch per `z_notch_px` of finger travel moves Z by `z_step_m`, snapped to the step. Ignored while the marker is on a path (the path owns height). |
| Buttons | Reset and Pathfinder only. Pathfinder shows only while paths run; it reads "Record point" while a path records. |
| X / Y / Z entry | Behind an "X Y Z" toggle. Glide releases any path lock, then travels to the target at the station's `marker.max_speed`. No clamp; non-finite or wrong-type input answers 400. |
| Units | Follow `ui.unit_system` for the fields, readout and notch label. Wire format is always metres. |
| Warning | A red error-level banner at the top of the card: "Don't have this open during a show – for setup only". |
| Tuning | Station config with the tested defaults, no web form: `stick_curve` 2.0, `stick_dead_zone` 0.08, `z_step_m` 0.05, `z_notch_px` 20. |

## Work items

### Config (`add-config-field` skill)
- [ ] `TouchRemoteConfig` (`[touch_remote]`): `enabled` (bool, False), `stick_curve` (1.0–3.0), `stick_dead_zone` (0.0–0.5), `z_step_m` (0.001–1.0), `z_notch_px` (int, 8–80). Normalised in `__post_init__` with the `_coerce_*` helpers.
- [ ] `enabled` in `_DEVICE_LOCAL_FIELDS_BY_SECTION` (stripped from export and broadcast). Tuning fields travel.
- [ ] `apply_section_data` re-runs `__post_init__` for `touch_remote`.
- [ ] `FIELD_RULES` entry and validate markup for the `enabled` checkbox only (the tuning fields have no input).
- [ ] Tests in `tests/test_configuration.py`: wrong type, range and `None`/`"abc"`/`True` per field (parametrised), TOML round trip, device-local stripping on export and broadcast.

### Runtime arbitration (`runtime/touch_remote.py`)
- [ ] `TouchRemote`: thread-safe registry. Web threads write sessions (token → held marker, stick vector, last-seen, queued Z notches, queued Reset / Pathfinder / Glide); the frame loop reads and applies. Clock injected (`time.monotonic` default) for hermetic tests.
- [ ] `hold(token, marker_id)`: newest wins; the previous holder's next request learns it lost the marker.
- [ ] `held_ids()` read by `InputManager` each frame; every other source skips a held marker (gamepad, keyboard, 3D mouse, 2D mouse grab, OSC `/marker/*`, OSC `/marker/<id>/path`, the detection pin).
- [ ] Per-frame apply in `InputManager.update`: the velocity goes through `_move` (paths-aware), with `invert_control_direction` applied there once; Z notches via `set_pos` unless locked; Reset through `_reset`; Pathfinder through `InputManager.pathfinder`; Glide releases the lock, then steps toward the target at the station's `marker.max_speed` each frame until it arrives or the stick moves.
- [ ] The per-frame `set_speed` freshness stamp is kept while held (never gated on movement).
- [ ] Dead-man: 250 ms stale ⇒ zero velocity; 2 s stale ⇒ release.
- [ ] Holds released for markers that leave `controlled_marker_ids` and when `enabled` turns off (hot reload).
- [ ] HUD caution status row "Web remote holds marker N" while any hold exists (status language).

### Web routes (`web/routes.py`)
- [ ] `POST /api/remote/open` → `{token}`; `POST /api/remote/close`.
- [ ] `POST /api/remote/drive` `{token, marker, sx, sy, z_notches, seq}`, sent ~20 Hz while the card is open (also the heartbeat). A stale `seq` is ignored.
- [ ] `POST /api/remote/action` `{token, marker, action: reset|pathfinder|glide, x?, y?, z?}`.
- [ ] `GET /api/remote/state`: controlled markers (id, name, colour, pos), who holds what, the station's `marker.max_speed`, tuning values, unit system, paths running / recording / locked.
- [ ] 403 while disabled, 400 on a malformed body or out-of-type values, 409 for a token that lost its marker. Existing PIN auth and Origin check apply.

### Web UI
- [ ] Launcher button and card markup in `base.tpl`, rendered only while enabled.
- [ ] `static/js/touch-remote.js` (bundled, no CDN): pointer-event stick with spring-back, detented drum, chips, hand and flip toggles in `localStorage` (try/catch), X / Y / Z row, unit conversion, `visibilitychange` ⇒ stop sending (the dead-man takes over), close ⇒ `/api/remote/close`.
- [ ] Status chips and banner only from `docs/STATUS_LANGUAGE.md` tokens and components (error banner, caution "Remote has …", info "On path", error "Taken by another remote").
- [ ] "Touch remote" section with the single `enabled` checkbox (Input tab), `<label>` only; explanation in a new `openfollow/web/help/touch_remote.md`.

### Tests
- [ ] `tests/test_touch_remote.py`: hold, steal, release on close / stale / uncontrolled / disabled; dead-man at 250 ms and 2 s on an injected clock; curve and dead-zone shaping; Z notch snapping; Glide arrival and cancellation; Pathfinder hand-off; held marker ignored by the other sources.
- [ ] Route tests: each endpoint's 200 / 400 / 403 / 409, wrong-type parametrisation, PIN required.
- [ ] Seam tests: `POST /api/remote/drive` → frame update → `marker.pos` (`tests/test_services_marker_visuals.py` style); config hot reload disabling releases holds (`tests/test_configuration.py`).
- [ ] Template tests: launcher absent while disabled; validate markup on the checkbox (`test_template_validation_consistency.py`); status language (`test_status_language.py`).
- [ ] JS unit tests for the stick shaping and drum notch maths, in the style of `tests/test_paths_editor_js.py`.

### Docs
- [ ] `docs/architecture/input.md`: the remote as an input source and its hold rule.
- [ ] `docs/architecture/web.md`: the routes and the gate.
- [ ] Add `touch_remote` to the config guide's device-local list.

## Verify
- [ ] `make ci-remote` green.
- [ ] Manual on an iPad against a station: stick, drum, Reset, Pathfinder on and off a path, Glide, hand and flip toggles, Wi-Fi off mid-push (stops within 250 ms, releases after 2 s), two tablets stealing a marker.
