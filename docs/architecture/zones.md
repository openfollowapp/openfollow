# Trigger zones

Covers `zones/` and the zone overlay. The rules that apply everywhere are in [CLAUDE.md](../../CLAUDE.md).

Polygonal zones in world coordinates that emit OSC events as markers or detection points enter and exit.

## Layout
```
zones/
    geometry.py     – point-in-polygon (ray casting), inward polygon shrink (miter-clamped)
    engine.py       – ZoneEngine state machine: occupancy, debounce, hysteresis, transitions
    osc_sender.py   – OscOutputClient: per-(host, port) UDP client cache via python-osc
    heights.py      – ZoneHeights: each controlled marker's lift from the zones that raise markers
```

## Config (`TriggerZonesConfig`)
- `enabled` (bool) – arms the engine (OSC output). Default `False`.
- `show_overlay` (bool) – renders zone polygons on the HUD. Independent of `enabled` – the overlay hotkey (`z` / `B`) flips this alone, so rendering must not be gated on `enabled`.
- `eval_fps` (int, one of `1, 5, 10, 15, 30, 60`) – engine evaluation rate; marker/detection collection is skipped between ticks.
- `debounce_ms` (int) – transitions inside the window are **discarded, not queued**.
- `hysteresis` (float, metres) – inward polygon offset applied to build the exit polygon (`shrink_polygon`), so near-boundary flicker doesn't re-fire OSC.
- `zones: list[TriggerZoneConfig]` – per-zone: `vertices`, `color`, `trigger_source` (`"markers" | "detection" | "both"`), four OSC addresses (`osc_address_first_entry`, `_additional_entry`, `_partial_exit`, `_final_exit`), `destination_id` (references a shared `OscDestinationConfig`; blank/dangling = emit nothing), `enabled`, and the three [zone height](#zone-heights) fields `z_offset`, `raise_markers`, `height_transition_s`.

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

## Zone heights
`zones/heights.py`, the experimental `zone_heights` feature. A zone's `z_offset` (metres, -10 to 20) is its height above the stage plane, the main grid's `z_offset`. With the feature on, the Operator Screen draws the zone there. The trigger stays in X and Y: occupancy never reads a height.

`raise_markers` lifts a marker inside the zone: its Z plus the zone's `z_offset` goes out on every output and on the Operator Screen, while `marker.pos`, the operator's own Z, is never changed. `ZoneHeights.step` runs every frame from `update_zone_triggers(dt)`, before the throttled OSC evaluation and on the clamped motion step `dt`, never a clock of its own:
- the target is the highest `z_offset` among `raising_zones(cfg)` (enabled, `raise_markers`, three corners; none unless both the `zones` and `zone_heights` features are on) whose polygon holds the marker's X/Y, else 0; it is independent of `trigger_source`, `triggered_by` and the section's `enabled`, which only arm OSC;
- a change of target eases linearly from the current lift over the `height_transition_s` of the zone entered (one holding the marker now and not on the previous step, known by its outline; of several, the one setting the target, else the highest), or else of the zone that set the previous target (its time as it is now while it still holds the marker, as last seen once it is gone, so a deleted or disabled zone, or the feature switched off, eases out over its own time); 0 s steps. Stepping out of a high zone into a lower one that already held the marker enters nothing, so the high zone's time applies;
- a marker first seen starts at 0 and eases up; a marker that leaves `controlled_marker_ids` is forgotten.

Only this station's controlled markers are lifted. A received PSN marker carries its sender's Z, already lifted where its own station raised it, so lifting it here would count the zone twice; detection boxes are not markers. The detection pin writes `marker.pos` like any other input, so a pinned marker is lifted too; the head pin (`pin_point` top) unprojects on the marker's Z plus its lift as set on the previous frame (in assist on the anchor's Z plus the lift), where the head is drawn and sent, so a marker on a riser stays put. The feet pin stays on the stage plane, so it places a person standing on a raised zone as if their feet were on the stage. The lift reaches the outputs through `Marker.set_lift` / `Marker.sent_pos` (see [outputs](outputs.md#zone-lift-markersent_pos)).

## Overlay rendering (`runtime/services_marker_visuals.py`)
`_populate_zone_overlay`:
- `state.show_zones = bool(tz.show_overlay)` – rendering is driven by `show_overlay` alone.
- Engine occupancy (`is_occupied`, `count`) is only read when `tz.enabled` is also true; otherwise polygons render with `(False, 0)`.
- Each polygon carries its height above the stage plane: the zone's `z_offset` with `zone_heights` on, else 0; `_zone_outlines` projects it at `zone_z_offset` plus that height.
- `overlay_draw_zones.ZoneCache` keeps every zone as one picture and the renderer paints it into each HUD picture (on the GL display it goes up as its own [layer](hud.md#the-gl-display)), at most once per display refresh, with no projection on a cache hit. The picture's size comes from the ink extents of the zones drawn on a recording surface at the screen's device scale, so a miter tip at an acute corner or a glyph past its label box is never cut off; where lens distortion throws an outline too far out for cairo to measure, the picture is the whole frame. It is let go while no zone is drawn, and drawn again only when a zone's vertices, colour, name, occupancy, count or height (its `z_offset`, 0 with `zone_heights` off), the grid z offset (the stage plane), the camera, the lens, the frame size, the device scale or the screen's font options change. Anything a zone is drawn from and missing from that key freezes on screen: a new projection input (in `project()` or `_zone_outlines`) must be added to the key and to the redraw test's cases in the same change. Zone labels are drawn with grayscale antialiasing, cached or not, since subpixel coverage does not survive the picture's alpha channel, so the screen's antialias mode is not part of the key.

## Zone Editor (`partials/zone_editor.tpl`)
The top-down plan is the shared `static/js/polygon-plan.js` (`PolygonPlan`: the fitted view and its transforms, the main grid, live markers, drawing a polygon, dragging the selected polygon's corners with the view held still); the editor keeps its zones, selection, details panel and saving. A Top-down / Camera switch (remembered per browser) swaps in `static/js/picture-view.js`: the snapshot with every zone outline on the floor plane as the saved camera and lens see it (`/api/scene/project`), the selected zone's corners as handles that drop onto that plane (`/api/scene/unproject` at the grid's `z_offset`). New zones are drawn in the top-down view only. The ground grid's editor uses the same two modules. The details panel's Basic tab carries the zone height fields in a row gated on `zone_heights` (`data-feature`), so they hide with the feature off and keep their values.

## Web API (`web/routes.py`)
- `GET /api/zones` / `POST /api/zones` / `PUT /api/zones/<index>` / `DELETE /api/zones/<index>` – CRUD for the zones list; the GET also carries the main grid (with `z_offset`) and the live marker positions for the editors. `/api/zones/<index>/duplicate` deep-copies every field of the zone. The zone height fields are validated on blur through `FIELD_RULES["zone"]`; `z_offset` is a length (`_SECTION_LENGTH_FIELDS["zone"]`), typed in feet and inches on an imperial station and sent in metres
- `GET/POST /api/config/trigger_zones` – section-level config
- `_load_json_body()` returns `Any`; callers must also `isinstance(data, dict)` check and 400 on non-object bodies.

## Keybinds
- `key_toggle_zones` (default `z`) – flips `trigger_zones.show_overlay`
- `btn_toggle_zones` (unbound by default) – gamepad equivalent; both write only `trigger_zones.show_overlay` to `config.toml` (`save_config_fields`)
