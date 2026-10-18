# Plan: a shorter Setup Wizard with two calibration modes

The Setup Wizard shrinks to five steps. The camera pose is solved from points
the operator pins on the video, each with a known X / Y / Z, so the origin
comes from the measurements instead of the grid offsets. The camera position
the operator enters is a hint the solve is checked against, never an input it
is bound to. Own PR from `main`.

**Status (2026-10-18):** designed and built as an interactive mockup on the
Media Gallery's Concert picture, with the real web UI components and a
JavaScript port of the solve. The mockup is being shown to operators; the
answers to [Mockup review](#mockup-review) decide the open points before
implementation starts. Nothing in the app is changed yet.

## Why

Operators skip FOV, lens and point of origin and go straight to corner
pinning. That is mostly harmless and partly not:

- The corner solve reads only the grid's world corners, the four pins, the
  snapshot size and the lens pair. Camera Position (FOV included) only seeds
  the handles, and Corner Pinning overwrites it; Reference Mapping is
  superseded by the same solve.
- What silently breaks a calibration is the part operators skip in Grid
  Setup: a grid size wrong by a factor scales every output, and the x / y
  offsets alone decide where the origin is.
- Four coplanar points fit exactly whatever the lens is, so a skipped lens
  step leaves no trace in the pin error: with the Concert picture's lens
  (-0.2 / 0.03) ignored, four perfect corners put the camera 2.4 m off.

## Steps of the Setup Wizard

1 Video Source, 2 Lens (only with `lens_distortion` on), 3 Camera Position,
4 Camera Calibration, 5 Review. Preparation, Grid Setup and Reference Mapping
go.

## Decisions

### Video Source and Lens

| Topic | Decision |
|---|---|
| Video Source | Unchanged |
| Lens | Unchanged: suggestions, traced lines, Fine-tune, the five-step rating, Refresh Image |

### Camera Position

| Topic | Decision |
|---|---|
| Unit system first | The step opens with **Displayed unit system** (the `unit_system_select` partial, moved here from Grid Setup; the station-wide setting, as today), so every length after it is typed in the operator's units. A switch converts the lengths already entered, on this step and in Camera Calibration |
| Location | "Where is your camera located?": a dropdown of Front of House (like a balcony), Front Truss, Back Truss, Stage Left, Stage Right, with an **Apply sample values** button beside it. The button only fills X / Y / Z with example values that show which way each axis counts (in m: Front of House 0 / -20 / 8, Front Truss 0 / -2 / 8, Back Truss 0 / 9 / 7, Stage Left 10 / 4 / 6, Stage Right -10 / 4 / 6; converted in Imperial). Nothing else reads the choice |
| Position | X / Y / Z of the lens, measured from the point of origin, in the active units (Imperial: ft / in text with the "Stored:" metric echo, as today). Prefilled from the saved camera when a calibration was applied before, empty and required otherwise |
| Role | A **hint**: the starting pose looks from it at the points (roll 0, the saved FOV), and the plausibility check reads it. The fit never weighs it |

### Camera Calibration

Top to bottom: the mode switch, the mode's inputs, the picture with
**Refresh Image** and **Reset Pins** under it, the pin hint, the corner list
(Simple), the solve status, the caution, the solved values, Distances to
check.

| Topic | Decision |
|---|---|
| Mode switch | The segmented switch (`.seg-toggle`, as Network mode): **Simple** (four corners) and **Advanced** (measured points) |
| Simple: rectangle | Width and depth of the largest rectangle the operator can measure and tape in the tracking area |
| Simple: point of origin | **Point of origin for all position data (0/0/0)**: a dropdown, default "Centred between DSL and DSR" (rectangle centre at X 0, Y depth / 2, Z 0), or "Custom", which asks "Where is the centre of the rectangle, measured from the point of origin?" with X / Y / Z fields, filled with the default on switching. The origin is drawn on the picture as 0/0/0: through the corners' homography before the solve, through the solved camera after it, and in Review |
| Simple: corners | Once the size reads, the rectangle is drawn on the picture as the starting camera sees it, so each corner starts on its own side; at most 50 % of the picture's width and height, inside the frame. The operator drags each corner onto its tape mark. The corner list sits below the picture: name, a state chip (Not drawn, Not moved, then the pixel error) and the corner's PSN X / Y / Z. The camera solves only once all four corners have been moved |
| Simple: grid | Simple reads and writes the grid, as today. A later grid edit that moves a corner drops the pins, as today |
| Advanced: points | Two kinds. **Known**: X / Y / Z typed. **Laser**: a distance to each of two other points (known or laser) and a Z, default 0. Each row: name, kind, chip, Pin on picture / Move pin, Remove pin, Delete. Advanced never touches the grid |
| Advanced: rules | At least two known points; at least four pinned points of any kind; a known point may stay unpinned (an anchor outside the picture); a laser point must be pinned (its pin picks between the two mirror places); no anchor cycles; at most 12 points. A requirement line names what is still missing |
| Switching modes | Simple → Advanced keeps the four corners as known points with their pins. Advanced → Simple clears every point, after a confirmation in the shared modal (only the confirm is red) |
| Refresh Image | Takes a new snapshot (someone stood on a mark). The pins stay; a snapshot of another resolution moves them with the picture. A success notice says so |
| Solved values | Pos X / Y / Z, pitch, yaw, roll, FOV, and **Lens**: the pair in use, or **Not measured** in caution text |
| Distances to check | The computed straight-line distance of every pair the operator did not measure by laser, in the active units, drawn as a labelled line on the picture through the solved camera. Simple draws width, depth and both diagonals (the diagonals share one label); Advanced draws the pair selected in the table. Display only: there is no field for the taped value |

### Solve

| Topic | Decision |
|---|---|
| One fit | Both modes go through one joint fit: the 7 camera values plus each laser point's X / Y, against the pins (`PIXEL_SIGMA` 1 px) and the laser distances (`DISTANCE_SIGMA` 0.04 m); pins are undistorted with the lens pair first |
| Seeds | The DLT of the pinned known points when four of them share a plane, and the hint pose looking at the points with FOV 60, 40 and 90°; the lowest cost wins. With no hint, the DLT seed alone |
| Laser points | Start at their trilateration, chained in anchor order; the mirror place is picked by the pin. Two distances that cannot meet within `3 √2 · DISTANCE_SIGMA` leave the point unplaced, and its row says why ("The two distances differ by more than their points are apart." / "… add up to less than …"); a point measured from an unplaced one is unplaced too |
| Per point | Status, reason, pixel error, laser distance error, solved position |

### Something might be wrong

One caution (`.notice.warning`, `docs/STATUS_LANGUAGE.md`) in Camera
Calibration and Review, listing every reason that applies. It never blocks
Apply.

| Reason | Fires when | Text names |
|---|---|---|
| Lens not measured | Simple mode, `lens_distortion` on, the Lens step skipped or rated Low and not applied | That four corners fit any lens |
| Camera far from the hint | Distance > max(1 m, 10 % of the camera's distance to the points) | Each axis carrying at least a quarter of the gap, in stage words ("2.5 m above", "4.0 m upstage and 5.0 m above … (6.4 m in all, allowed 1.6 m)"), in the active units; with a Custom origin, asks whether the camera was measured from the same origin |
| Field of view | Outside 20-120° | The FOV |
| Floor / up | Camera below the floor, or looking up | |
| Point disagrees | A pinned point's error > 5 px | Point and error |
| Laser distances | A laser point's distance error > 3 σ (12 cm) | Point and error, in cm or in |

### Review

The picture with the grid, the pins and 0/0/0; the status and the caution;
Camera, Lens (with the coverage meter), Calibration (mode, points, pinned,
largest error, entered position), Distances to check, Grid (Simple: from the
rectangle; Advanced: unchanged). **Discard & Leave** / **Apply & Finish**.
Apply writes the camera, the marks and the mode, and in Simple mode the grid
before the camera; each response's `.ok` is checked.

### Config and the feature list

| Topic | Decision |
|---|---|
| Calibration points feature | Retired: `calibration_points` leaves `FEATURE_KEYS`; Advanced mode is always available. A stored `shown_features` entry is already dropped as unknown |
| Migration | Old `camera.calibration_points` with entries ⇒ Advanced: the four grid corners become known points with `camera.corner_pins` as their pins, each old point a laser point anchored to its two corners, `height` as its Z. Otherwise ⇒ Simple with `corner_pins` as the corner pins. The old keys are read once on load and never written |

## Simulation findings

From the mockup's solver against the Concert picture (camera 0 / 13 / 12 m,
pitch -48°, 70°, lens -0.2 / 0.03, 12 x 6 m taped grid). These set the
thresholds above; the Python tests pin them again.

| Case | Result |
|---|---|
| Simple, exact pins, true lens | Exact camera, with a good, 5 m-off, wrong-side or no hint |
| Simple, lens fitted from the suggestions (-0.197 / 0.026) | Camera within 1 cm |
| Simple, lens ignored | Camera 2.4 m off, pin error 0 px: only the hint check catches it |
| Advanced example (Reference Point, DSL, DSR known; riser corners and USR by laser) | Camera within 2 cm, laser points within 6 mm |
| Advanced, one laser distance 46 cm long | Two laser points flagged (27 cm, 15 cm) |
| Advanced, a known X mistyped by 0.5 m | Camera 3.9 m off the hint: flagged |
| Pin noise σ 1 / 2 / 3 px (earlier Concert picture) | Camera off by median 0.14 / 0.26 / 0.38 m, p95 0.27 / 0.51 / 0.78 m |
| Upstage corners pinned 5 / 10 / 20 px low (earlier picture) | Camera 0.3 / 0.6 / 1.2 m off |
| Depth typed 8.5 / 9 m instead of 8 (earlier picture) | Camera 2.6 / 4.9 m off |

## Mockup review

Questions for the people shown the mockup:

- Do the steps read in order without the help drawer? Where did they stop?
- Mode names: **Simple** / **Advanced**, with the sub-labels "Four corners
  of a rectangle" / "Known points and laser distances"?
- Is "Point of origin for all position data (0/0/0)" with "Centred between
  DSL and DSR" understood, and is the Custom question clear?
- Is dragging a drawn rectangle's corners clearer than clicking four pins?
- Does the caution say enough to find the mistake (axis words, the origin
  question)?
- Would they tape the computed distances to check, and are four lines on
  the picture too many?
- Advanced: are laser points understood without the example? Should a laser
  point's solved X / Y show in its row?

## Open questions

- The mode labels (above).
- Whether a laser point's solved position shows in its row.
- Anything the review turns up.

## Implementation steps

### 1. Config (`add-config-field` skill)
- [ ] `CameraConfig.calibration_mode` (`"simple"` / `"advanced"`) and
      `calibration_marks: list[CalibrationMarkConfig]`: `id`, `name`, `kind`
      (`known` / `laser`), `x`, `y`, `z`, `anchor_a`, `anchor_b` (ids),
      `distance_a`, `distance_b`, `pin_x`, `pin_y` (fractions, NaN = not
      pinned), `position` (solved, laser only).
- [ ] `problem()` per mark and per list (kinds, anchors exist and differ, no
      cycle, distances in `(0, 200]` m, pins inside the snapshot, counts); a
      rejected entry is dropped with one WARNING per distinct set, never
      clamped.
- [ ] Migration of `calibration_points` / `corner_pins` in `load_config`,
      before `_filter_known` (also for import and push payloads).
- [ ] A grid write in Simple mode drops the corner pins (generalise
      `_forget_corner_pins_off_grid`); Advanced marks are left alone.
- [ ] `FIELD_RULES` for every editable field, matching the server bounds.
- [ ] Tests (`tests/test_configuration.py`): valid lists, wrong type, range,
      enum, dangling / self / cyclic anchors, laser unpinned, fewer than two
      known, more than the cap, TOML round trip, migration both ways
      (with / without points, with / without pins), export / import / push.

### 2. Solver (`scene/calibration_points.py`)
- [ ] Generalise `solve_with_points` to marks: known points fixed, laser
      points free in X / Y at their Z, chained trilateration with
      `_pick_by_pin`, `_fit` unchanged in shape.
- [ ] Seeds: coplanar DLT and the hint pose at three FOVs; lowest cost wins.
      Simple mode goes through the same entry point.
- [ ] Per-mark answer: status, reason, pixel error, distance error, position.
- [ ] `plausibility(solve, hint, mode, lens_measured)` returning the reasons
      with the axis breakdown; thresholds as in the table above.
- [ ] Tests (`tests/test_calibration_points.py`, `tests/test_solver.py`):
      the Concert picture's camera and a front-of-house camera through known
      poses with absolute pose asserts, the mirror case, an off-picture
      anchor, chained laser points, the lens-ignored case, a mistyped point
      flagged, each plausibility reason firing and not firing, the axis
      wording; mutation-check every regression test.

### 3. Routes (`web/routes.py`)
- [ ] `/api/wizard/solve` takes `marks`, `mode`, `hint`; strict number
      reading, 400 on malformed, 409 when superseded, 422 when unsolvable.
- [ ] `/api/wizard/project` drops the reference point; remove
      `/api/wizard/unproject` if nothing calls it.
- [ ] Tests (`tests/test_wizard.py`): malformed bodies per failure mode,
      the answer's shape, the superseded turn.

### 4. Setup Wizard (`web/templates/wizard.tpl`)
- [ ] Five steps; Preparation, Grid Setup and Reference Mapping removed with
      their state, overlays and session keys (bump the session schema).
- [ ] Camera Position: unit selector first (a switch converts the entered
      lengths), location dropdown and Apply sample values, X / Y / Z,
      prefill rule.
- [ ] Camera Calibration, Simple: rectangle, point of origin with Custom,
      the drawn starting rectangle (50 % cap, inside the frame), corner drag
      with `pointHandle` and the loupe, the "all four moved" gate, the
      corner list with PSN X / Y / Z, the 0/0/0 mark.
- [ ] Camera Calibration, Advanced: point list (add, kind, coordinates or
      anchors and distances, pin, remove pin, delete), requirement line.
- [ ] **Refresh Image** and **Reset Pins** under the picture in Lens and
      Camera Calibration; a new snapshot keeps the pins.
- [ ] Solve status, caution, solved values with Lens, distance lines and
      table.
- [ ] Mode switch rules and the Advanced → Simple confirmation.
- [ ] Every length field and readout (rectangle, origin, marks, distances,
      caution) in the active units.
- [ ] Review and Apply as decided.
- [ ] Tests: `tests/test_wizard_js.py` for the pure functions (starting
      rectangle, distances, anchor order, unit conversion on switch, prefill),
      `tests/test_wizard.py` for the page.

### 5. Camera tab and feature list
- [ ] The Camera form lists the mode and the marks read-only.
- [ ] Remove `calibration_points` from `FEATURE_KEYS` / the experimental set
      and every `data-feature="calibration_points"`.

### 6. Docs
- [ ] `help/camera.md` and `help/grid.md`: the five steps, unit system,
      sample values, both modes, the point of origin, the distances check,
      the caution.
- [ ] `docs/architecture/web.md` Setup Wizard section,
      `docs/architecture/config.md`.
- [ ] What's new entry (operator action changed).

### 7. Verification
- [ ] `make ci-remote` on pi59.
- [ ] Station check on pi66: the Concert picture with its stored
      calibration reproduced in both modes, metric and imperial; a real
      camera, tape-measured distances against the computed ones; the
      caution with a deliberately wrong camera position and with the lens
      step skipped; a migrated config from the current release.
- [ ] Web UI checked in Safari.
