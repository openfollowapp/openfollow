# Trigger zones

Covers `zones/` and the zone overlay. The rules that apply everywhere are in [CLAUDE.md](../../CLAUDE.md).

Polygonal zones in world coordinates that emit OSC events as markers or detection points enter and exit.

## Layout
```
zones/
    geometry.py     – point-in-polygon (ray casting), inward polygon shrink (miter-clamped)
    engine.py       – ZoneEngine state machine: occupancy, debounce, hysteresis, transitions
    osc_sender.py   – OscOutputClient: per-(host, port) UDP client cache via python-osc
```

## Config (`TriggerZonesConfig`)
- `enabled` (bool) – arms the engine (OSC output). Default `False`.
- `show_overlay` (bool) – renders zone polygons on the HUD. Independent of `enabled` – the overlay hotkey (`z` / `B`) flips this alone, so rendering must not be gated on `enabled`.
- `eval_fps` (int, one of `1, 5, 10, 15, 30, 60`) – engine evaluation rate; marker/detection collection is skipped between ticks.
- `debounce_ms` (int) – transitions inside the window are **discarded, not queued**.
- `hysteresis` (float, metres) – inward polygon offset applied to build the exit polygon (`shrink_polygon`), so near-boundary flicker doesn't re-fire OSC.
- `zones: list[TriggerZoneConfig]` – per-zone: `vertices`, `color`, `trigger_source` (`"markers" | "detection" | "both"`), four OSC addresses (`osc_address_first_entry`, `_additional_entry`, `_partial_exit`, `_final_exit`), `destination_id` (references a shared `OscDestinationConfig`; blank/dangling = emit nothing), `enabled`.

## Occupancy state machine (`ZoneEngine._emit_transitions`)
Per zone, tracks the set of occupants and an integer count. Transitions, in order:
- **first entry** (count 0 → 1): `osc_address_first_entry`
- **additional entry** (count ≥ 1, new occupant): `osc_address_additional_entry` – emitted in sorted occupant order for determinism.
- **partial exit** (count ≥ 2 → lower, still ≥ 1): `osc_address_partial_exit`
- **final exit** (count → 0): `osc_address_final_exit`

Exit tests use the **shrunken** polygon (inward by `hysteresis`); entry tests use the original polygon.

## Coordinates
Vertices, `marker.pos`, and detection-unprojected world points all share the **PSN-absolute** frame (see [the one canonical frame](outputs.md#the-one-canonical-frame)). `services._collect_marker_positions` returns `marker.pos` verbatim – no offset arithmetic on either side of the containment check. `_populate_zone_overlay` passes vertices through unchanged.

## Hot reload (`ZoneEngine.reload_config`)
Occupancy is carried across config reloads via `_zone_signature = (vertices, trigger_source, enabled)`. Duplicate-signature zones match in FIFO order. A zone that was disabled (or is re-disabled) drops its carried occupancy so re-enabling fires `first_entry` again rather than a silent "already inside" state.

## Overlay rendering (`runtime/services_marker_visuals.py`)
`_populate_zone_overlay`:
- `state.show_zones = bool(tz.show_overlay)` – rendering is driven by `show_overlay` alone.
- Engine occupancy (`is_occupied`, `count`) is only read when `tz.enabled` is also true; otherwise polygons render with `(False, 0)`.
- `overlay_draw_zones.ZoneCache` keeps every zone as one picture and the renderer paints it on each display refresh. The zones are projected on every refresh and the picture is keyed on that result: each drawn zone's outline on screen, colour, name, occupancy and count, plus the frame size, the device scale and the screen's font options. A camera, lens or stage-plane change therefore redraws it through the outlines, and a change to a zone left out of the drawing (fewer than three corners, or a corner behind the camera) does not. The picture's size comes from the ink extents of the zones drawn on a recording surface at the screen's device scale, so a miter tip at an acute corner or a glyph past its label box is never cut off; where lens distortion throws an outline too far out for cairo to measure, the picture is the whole frame. It is let go while no zone is drawn. Zone labels are drawn with grayscale antialiasing, cached or not, since subpixel coverage does not survive the picture's alpha channel.

## Web API (`web/routes.py`)
- `GET /api/zones` / `POST /api/zones` / `PUT /api/zones/<index>` / `DELETE /api/zones/<index>` – CRUD for the zones list
- `GET/POST /api/config/trigger_zones` – section-level config
- `_load_json_body()` returns `Any`; callers must also `isinstance(data, dict)` check and 400 on non-object bodies.

## Keybinds
- `key_toggle_zones` (default `z`) – flips `trigger_zones.show_overlay`
- `btn_toggle_zones` (default `B`) – gamepad equivalent; persisted to `config.toml` via `save_config`
