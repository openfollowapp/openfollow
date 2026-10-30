# Plan: zone corners on the camera picture, and zone heights

Two changes to trigger zones for 0.5.0-rc3, built and merged separately.

1. **Camera editor** (#569): the Zone Editor's Camera view gets the Setup Wizard's point handling. It is the default for every station.
2. **Zone heights** (#570): a zone can sit at a height above the stage, and can raise the Z of a marker inside it. An experimental feature, off by default.

The interactive mockup, built on the Media Gallery's Concert picture and its calibration, is the reference for both: corner handling, the loupe, edge drag, full screen and the zone height behaviour.

## Decisions

| Topic | Decision |
|---|---|
| Corners | 44 CSS px targets drawn in screen pixels, named V1…Vn as in the Top-down view, in the wizard's gold pin style. The chosen zone is drawn thicker. |
| Dragging | The wizard's loupe (120 px, 4×, diagonally off the pointer: 12 px for a mouse, 30 px for touch) with the corner's stage position under it (X, Y; and Z with zone heights on). The outline follows the corner while it moves. |
| Click | A press that moves less than 3 px (mouse) or 8 px (touch, pen) chooses the corner and does not move it. |
| Keys | With the picture focused: Tab / Shift+Tab go round the chosen zone's corners (corners the camera does not see are skipped), arrows move the chosen corner 1 picture px, Shift with an arrow 10 px, Delete / Backspace delete it, Esc lets go of it and then leaves full screen. |
| Edge drag | A press on an edge of the chosen zone (a 20 px band) followed by a drag adds a corner between the edge's two corners, which then follows the pointer. A press that does not move adds nothing. With a mouse, a dashed dot on the edge shows where it starts. |
| Delete | **Delete corner** removes the chosen corner; a zone keeps at least three. **Delete Selected** becomes **Delete zone**. |
| Full screen | A button on the picture opens it as in the wizard: a bar with the zone and its corner count, Refresh Image, Delete corner and Exit full screen. Esc leaves it. |
| Unseen corners | A corner outside the picture or behind the camera has no handle; it is moved in the Top-down view. |
| Copy | No instructions on the picture; the keys and limits are in the Zone Editor's help drawer. |
| New zones | Still drawn in the Top-down view. |
| Feature | `zone_heights` in `FEATURE_KEYS` and `EXPERIMENTAL_FEATURES`. Features row: **Zone heights** / "Draws a zone at a height above the stage, and can raise a marker's Z while it is inside." / switched off: "zones are drawn on the stage floor again and markers keep their own height." Off: the fields are hidden and nothing below applies; the settings are kept. |
| Z offset | Per zone, metres above the stage plane (the main grid's `z_offset`). The zone is drawn at that height in the Camera view and in the Operator Screen's zone overlay, and its corners are picked on that plane. The trigger stays in X and Y. |
| Marker height | Per zone, a checkbox: label **Marker height**, inline **Add the Z offset inside this zone**. While a marker is inside such a zone (its X/Y in the zone's polygon), its Z is raised by the zone's Z offset in everything the station sends (PSN, OTP, RTTrPM, OSC) and on the Operator Screen. The marker's own Z, as the operator sets it, is not changed. Where several such zones overlap, the highest Z offset counts. |
| Height transition | Per zone, **Height transition (s)**, default 0.3. Crossing a zone's edge eases the added height over this time instead of stepping it: the time of the zone entered, or else of the zone left. |

## Design

**Camera editor**
- Zone corners are stage positions. The Camera view needs the projection on the client, so a drag, the arrow keys and the outline do not wait for a round trip per step. The page gets the camera (pose, horizontal FOV, lens k1/k2/centre) and the stage plane in the `GET /api/zones` answer, and a small shared module projects and unprojects with the same maths as `scene/solver.py` (`project_points` + `apply_overlay_distortion`, `invert_overlay_distortion` + `unproject_to_plane`). A test pins the module against the Python on a set of points; the mockup's port matched to 0.01 px.
- The loupe, handles and keys live in `static/js/picture-view.js` (or a module it uses), so the ground-grid and path editors can take them up later. The wizard keeps its own code: no refactor of `wizard.tpl` in this change.
- Saving stays as now: a corner is saved when its drag ends, or a moment after the last arrow-key step, through the zone's existing save route. The server keeps validating.
- The 1 s polling of the zone list must not rebuild the overlay during a drag.

**Zone heights**
- `TriggerZoneConfig` gains `z_offset` (float, default 0.0), `raise_markers` (bool, default false) and `height_transition_s` (float, default 0.3, 0–10). Each goes through `__post_init__`, `FIELD_RULES`, the regression tests and the help drawer (the `add-config-field` skill).
- The added height of each marker is worked out per frame, next to the zone triggers: a target per marker (the highest `z_offset` among the zones with `raise_markers` whose polygon holds its X/Y) and a ramp towards it over the transition time, timed on the frame loop's elapsed time. The lift is kept apart from the marker's own position. The outputs and the Operator Screen read the marker's Z plus its lift. It never goes through `Marker.set_pos`, so the operator's Z and the per-frame freshness stamp are untouched. Where exactly the lift is applied is settled when building, against `docs/architecture/outputs.md` and `hud.md`. One place feeds every sender.
- The zone overlay draws each zone at the stage plane plus its Z offset. The Z offset is part of the `ZoneCache` key, with a redraw test case.
- Feature off: no lift (target 0, so a lift already under way eases out), zones drawn at the stage plane, fields hidden.

## Not in this change / known limits

- A corner save given up after 10 s without an answer may still reach the station later and land after a newer one; preventing that needs revisions on the server.
- The station addresses zones by index, so while edits keep the poll held, a zone deleted from another browser, or a delete that failed or timed out but still landed, can leave the page's list stale, and a later queued write can reach another zone; closing it needs stable zone ids on the station.

## Work items

**Camera editor (#569)**
- [x] Camera and lens in `GET /api/zones`; the client projection module, with a test against the Python.
- [x] Corners as named 44 px handles, the chosen zone drawn thicker, unseen corners skipped.
- [x] Drag with the loupe and the stage-position readout; the outline moves with the corner; click without move chooses.
- [x] Tab, arrows, Shift, Delete and Esc.
- [x] Edge drag adds a corner; the hover dot for a mouse.
- [x] **Delete corner** (at least three), **Delete zone** renamed.
- [x] Full screen with its bar.
- [x] Polling holds during a drag.
- [x] JS tests for each interaction; help drawer `zone_editor.md`; `docs/architecture/zones.md`.
- [ ] Review to cosmetic, gate, station check on pi66.

**Zone heights (#570)**
- [ ] `zone_heights` feature: its key, its Features row, and the off behaviour.
- [ ] The three zone fields: config, validation, form (shown with the feature on), tests.
- [ ] Camera view and HUD overlay at the zone's height; the cache key and its redraw test.
- [ ] The per-marker lift with its ramp, applied to every sender and the Operator Screen; tests for the overlap rule, the transition times, feature off, and a marker whose own Z the operator changes meanwhile.
- [ ] Help drawers, `docs/architecture/zones.md` and `outputs.md`, What's new (experimental, so a short line).
- [ ] Review to cosmetic, gate, station check on pi66: a marker walked onto a raised zone, watched in the PSN output and on the Operator Screen.
