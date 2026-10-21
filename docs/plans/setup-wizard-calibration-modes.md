# Plan: a four-step Setup Wizard with two calibration modes

The Setup Wizard shrinks to four steps. The camera pose is solved from points
the operator places on the video, each with a known or measured position, so
the origin comes from the measurements instead of the grid offsets. A camera
position the operator enters is an estimate the solve starts from or is
checked against, never an input it is bound to. Own PR from `main`.

**Status (2026-10-21):** designed as an interactive mockup on the Media
Gallery's Concert picture, with the real web UI components and a JavaScript
port of the solve, and reviewed with operators. This plan describes the
mockup's version 58, which is what gets built. Nothing in the app is changed
yet. Nothing is left open.

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

1 Video Source, 2 Lens (only with `lens_distortion` on, as today), 3 Camera
Calibration, 4 Review. Preparation, Grid Setup, Camera Position and Reference
Mapping go; Corner Pinning becomes Camera Calibration.

Every step has a **?** in its section head that opens the help drawer
(new in the Setup Wizard). Camera Calibration has one help text per mode; the
drawer's title names the mode, and an open drawer follows a mode switch.

## Working on the picture

The same rules on every picture (Lens, Camera Calibration, Review).

### Loupe

| Topic | Decision |
|---|---|
| Look | Round, 120 px, the picture at 4×, a yellow crosshair over a wider black line (readable on a white and on a dark floor) |
| Placement | Diagonally off the point, its edge 12 px from a mouse pointer or 30 px from a touch. Tried up-right, up-left, down-left, down-right; a spot outside the picture or over an open menu is skipped |
| When | While dragging; while a point or corner is selected; in Advanced Camera Calibration it also follows the pointer and stays on the spot of an open menu |

### Selecting a point

Lens points and Simple corners behave alike:

- A click or drag selects the point. It keeps the focus and the loupe stays
  on it. A press that moves less than 3 screen px is a click and moves
  nothing.
- **Arrow keys** nudge it 1 px, **Shift** + arrow 10 px; **Tab** selects one
  too.
- **Enter** confirms it (Simple: places the corner where it is, also one that
  started on its mark). **Esc** or a press or focus anywhere else ends the
  selection.

### Full screen

For screens where the picture is too small to work on.

| Topic | Decision |
|---|---|
| Entry | A button in each picture's top right corner (four-corner icon, `aria-label` "Full screen") |
| Shell | The picture moves into a full-screen shell with one toolbar line, and back on exit. The shell asks the browser for full screen; where it refuses (iPhone Safari, a framed page), the shell fills the window |
| Picture | Keeps the snapshot's aspect ratio: as wide as the space below the toolbar allows without being taller, centred on black |
| Toolbar | Step title (Calibration adds "· Simple" / "· Advanced"), the current hint, a status, the step's action, **?**, **Exit full screen**. On a phone (≤ 640 px) only the hint and the buttons |
| Hint | Lens: "Tap a dashed suggestion, or click the start and the end of a straight edge." with no lines, "Click the other end of the straight edge." with a line started, else the lens result. Calibration: the hint box's heading. Review: "The green grid is the one the Operator Screen will draw." or "No camera solve yet." |
| Status | Calibration: "Largest error N px", plus "· Something might be wrong" in the caution colour when the caution has reasons. Lens: "A line does not fit the others" in the caution colour |
| Action | Lens only: **Delete line**, disabled without a selected line |
| Inside | Everything works as on the page: dragging, selecting, the Advanced menus and forms, the loupe |
| Esc | Ends full screen and cancels whatever is active: a selection, an open menu, a started line, the help panel. The browser's own exit (Esc, a gesture) ends it the same way. Exit puts the picture back and focuses its full-screen button |
| **?** | The **?** button, or the **?** key unless a field has the focus, toggles a non-modal panel under the toolbar's right end; it stays open while the picture is used and closes with its ×, **?** or full screen ending |

The panel, titled **Full screen**, holds one or two sentences and a key table
for the picture, each ending with "**?** Show or hide this help":

| Picture | Sentences | Keys |
|---|---|---|
| Lens | "Tap a dashed suggestion that is straight in reality, or click the start and the end of a straight edge. Click a line to select it; drag a point to correct it." | Tab: Select the next point · Arrows: Nudge the selected point 1 px · Shift + arrow: Nudge it 10 px · Enter: Confirm the point · Delete or Backspace: Remove the selected line, or cancel a started one · Esc: End full screen (cancels active selection) |
| Calibration, Simple | "Drag each corner onto its tape mark; the loupe shows the picture at 4×. Click a corner to select it." | Tab: Select the next corner · Arrows: Nudge the selected corner 1 px · Shift + arrow: Nudge it 10 px · Enter: Place the corner where the crosshair is · Esc: End full screen (cancels active selection) |
| Calibration, Advanced | "Click the picture where a point is and choose it from the menu, or make a New point or New triangulated point there. Click a pin to remove it or delete the point; drag a pin to correct it." | Arrows: With the menu open: move its spot 1 px. On a pin: nudge it · Shift + arrow: 10 px · Tab: Next menu item, field or pin · Enter: Choose the menu item, or place the new point · Esc: End full screen (cancels active selection) |
| Review | "The picture shows the solved grid, the points and 0/0/0, as the Operator Screen will draw them. Nothing can be changed here." | Esc: End full screen |

On the page (not in full screen) Esc keeps its order: close an open menu, end
a selection, cancel a started line.

## Video Source

Unchanged: the source picker, **Save** and **Save & Next**.

## Lens

Unchanged: the suggestions, traced lines, the misfit notice, Fine-tune, the
five-step rating, Refresh Image, Clear lines. Changed:

| Topic | Decision |
|---|---|
| Selecting a line | A click anywhere along a traced line selects the whole line, drawn in the text colour. A line just added (a tapped suggestion or a traced edge) is selected |
| Deleting | **Delete** or **Backspace** removes the selected line wherever the focus is, except in a field or with a dialog open; with a line started, the key cancels it instead. **Delete line** does the same |
| Points | As [Selecting a point](#selecting-a-point) |
| Buttons | **Refresh Image**, **Delete line**, **Clear lines**, **Load lens calibration…**, **Save lens calibration…** in one row under the picture |
| Tip | "Click a point or use Tab to select it; the loupe stays on it and the arrow keys nudge it (Shift for bigger steps). Press Enter when the crosshair is in the right spot; Esc or a click elsewhere ends the selection. Delete or Backspace removes the selected line, Esc cancels a started line. Skipping this step keeps the current values." |

### Load lens calibration

For a picture with no straight edge to trace, with the same camera and lens at
the same zoom. A dialog, **Load Lens Calibration**:

| Part | Decision |
|---|---|
| Saved on this station | A radio list (`.tier-list`): the name, then "Barrel / fisheye −0.118 · Edge fit 0.021 · 2026-09-12". Empty: "No lens calibrations are saved on this station." |
| From a settings file | "Only the lens values of an exported `.ofsettings` file are loaded." and **Choose settings file…** (accepts `.ofsettings` and `.openfollowsettings`). The file is read in the browser; only `camera.lens_k1` / `camera.lens_k2` are used |
| A good file | Becomes an entry under the button, selected: the file name and its values. The button then reads **Choose another file…** |
| A bad file | A field error under the button: "This is not an OpenFollow settings file." (not JSON, or no `camera` object), "This settings file has no lens calibration." (either value missing or not a finite number), "This file's lens values fold the overlay inside the frame." (fails the fold check) |
| Traced lines | "Loading removes the N traced line(s)." |
| Buttons | **Cancel**, **Load** (disabled until an entry is selected) |
| After loading | The traced lines go, Fine-tune shows the pair. The result box (info, no meter) reads "Loaded “name” – barrel / fisheye x, edge fit y", under it "Saved on this station on 2026-09-12." or "From the settings file name.ofsettings."; after a Fine-tune change "Fine-tuned after loading." comes first. A later fit that applies (rated above Low) replaces the loaded pair |

### Save lens calibration

A dialog, **Save Lens Calibration**, enabled once the lens has values in this
run (fitted, loaded or fine-tuned):

| Part | Decision |
|---|---|
| Field | **Calibration name**, at most 60 characters, placeholder "Main stage – FOH wide", `autocomplete="off"` and the password-manager ignore attributes |
| Values | "Barrel / fisheye x · Edge fit y" under the field |
| Same name | A name already saved (trimmed, ignoring case) shows the warning "A calibration with this name is saved on this station. Saving replaces it." |
| Buttons | **Cancel**, **Save** (disabled while the name is empty); **Enter** in the field saves |
| Saving | Stored on the station at once as its own file (see [Config](#config)), with the OpenFollow version that saved it, independent of Apply & Finish. A success notice under the buttons: "Saved “name” on this station." |

## Camera Calibration

Section head: **Camera Calibration**, the subtitle ("Drag four corners onto
their tape marks" / "Place measured points on the picture"), **Switch units
to Imperial** / **Metric** (small, left of the **?**), **?**.

| Topic | Decision |
|---|---|
| Mode switch | Labelled **Calibration**, the segmented switch (`.seg-toggle`): **Simple** "Four corners of a rectangle", **Advanced** "Known and triangulated points" |
| Hint box | One blue `.notice` that always names the next thing to do, moved above the part of the page it is about |
| Per mode | The subtitle, the hint, the tip under the picture and the help drawer |
| Units | **Switch units** changes the station's unit system (the setting in General) and converts every length already entered. Imperial reads ft / in text (`26' 3"`, `26 ft 3 in`, a bare number as feet) with the "Stored:" metric echo under each field |
| Refresh Image | Takes a new snapshot (someone stood on a mark). The pins stay; a snapshot of another resolution moves them with the picture. Success notice for 4 s: "New snapshot loaded. The pins stay where they are." |
| Reset Pins | Simple: every corner back to the drawn rectangle. Advanced: every pin removed, the points kept |
| Status line | Under the picture: the waiting text, "Solved from N pinned points, largest error N px." (success) or "No camera fits these points. Check the pins and the measurements." (error) |
| Solved values | Pos X / Y / Z, pitch, yaw, roll, FOV, and **Lens**: the pair in use, or **Not measured** in the caution colour |
| Next | With one or two camera position fields filled: the field error, the box opened (Simple), no step change |

### Simple

Top to bottom:

1. The location group: **Where is your camera located?**, a dropdown
   (Choose…, Front of House (balcony railing / ceiling), Front Truss, Back
   Truss, Stage Left, Stage Right), and a collapsed box **Optional: enter a
   rough camera position to check the result**, holding the Camera Position
   fields (below).
2. "Mark the four corners of the **largest rectangle** you can fit on the
   floor of the performance area, all visible to the camera; only the corners
   need a mark, such as a tape cross. Enter its size: the wizard draws it on
   the picture as a camera there would see it, and you drag each corner onto
   its tape mark. Positions outside the rectangle are less accurate."
3. **Rectangle**: Width and Depth (placeholders 12 and 6 m), and the
   diagonals note.
4. **Point of Origin**: **Point of origin for all position data (0/0/0)**,
   "Centred between DSL and DSR" (default; the rectangle's centre at X 0,
   Y depth / 2, Z 0) or "Custom", which asks **Where is the centre of the
   rectangle, measured from the point of origin?** with X (stage left +),
   Y (upstage +), Z (up +), filled with the default on switching.
5. The picture, Refresh Image and Reset Pins, the tip, **Corners**, the
   status, the caution, the solved values. Simple has no Distances to check.

| Topic | Decision |
|---|---|
| Location | Orients the starting rectangle only, as a camera at Front of House 0 / -20 / 8, Front Truss 0 / -2 / 8, Back Truss 0 / 9 / 7, Stage Left 10 / 4 / 6, Stage Right -10 / 4 / 6 m. Review lists it as Camera location |
| Camera position | Optional: "Estimate where the camera's lens is from the point of origin, in m: X = stage left, Y = upstage, Z = up. About a metre off is close enough. The solved camera is checked against it, which catches a skipped lens step or a wrong rectangle size." Fields Pos X (stage left +), Pos Y (upstage +), Pos Z (height). Partly filled: "Enter all three values, or leave all three empty." The box opens by itself when a value is in it |
| Diagonals note | Under Width / Depth, always shown. Before both are entered: "**Optional:** measure the two diagonals between opposite corner marks. If both are the same, you have a true rectangle. Skip this for a rectangle you know is true, such as the edges of stage decks." After: the middle sentence reads "If both are 13.42 m, the marks are right and form a true rectangle." (the computed diagonal, in the active units). Four corners always fit, so a wrong mark cannot show after the solve |
| Starting rectangle | Once the size reads: projected from a camera at the entered position, else at the chosen location, else 15 m in front of the rectangle's front edge and 8 m above, looking at the rectangle's centre with the saved FOV. Scaled to at most 50 % of the picture's width and height (a projection under 20 % in both is scaled up to that), kept 5 % inside the frame, bent by the lens pair, outlined dashed until the solve. A corner not yet placed follows every input change; a placed one stays |
| Corners | Dragged or selected and confirmed (see [Selecting a point](#selecting-a-point)). Unplaced corners are drawn dashed |
| Corner list | Below the picture. Each row: DSL / DSR / USR / USL, "Downstage left" etc., a chip (Not drawn, Not placed, Placed, then the pixel error), and the corner's X / Y / Z |
| Corner positions | Centre (cx, cy, z): DSL cx + w/2, cy - d/2; DSR cx - w/2, cy - d/2; USR cx - w/2, cy + d/2; USL cx + w/2, cy + d/2; all at z |
| Solve | Only once all four corners are placed |
| 0/0/0 | The origin is drawn on the picture: through the corners' homography before the solve, through the solved camera after it, and in Review |
| Grid | Simple writes the rectangle as the grid (2 m spacing kept from the stored grid). A later grid edit that moves a corner drops the pins, as today |
| Tip | "Drag each corner onto its tape mark; the loupe shows the picture at 4×. Click a corner or use Tab to select it; the loupe stays on it and the arrow keys nudge it (Shift for bigger steps). Press Enter when the crosshair is in the right spot; Esc or a click elsewhere ends the selection." |

The Simple hint, first match wins:

| When | Above | Heading | Sub-line |
|---|---|---|---|
| No location and no width / depth | the location | Choose where your camera is located. | It decides on which side of the picture each corner of the rectangle starts. |
| Nothing of the rectangle entered | the rectangle text | Mark the four corners of the largest rectangle you can fit on the floor of the performance area, all visible to the camera, and enter its width and depth. | Only the corners need a mark, such as a tape cross. The wizard then draws the rectangle on the picture. |
| Something missing | the rectangle text, or the custom-centre question when only the centre is missing | "Enter the rectangle's width / depth / width and depth.", "Enter the X, Y and Z of the rectangle's centre, measured from the point of origin." (naming exactly the missing ones), "Width and depth must be between 0 and 200 m." | as above |
| A corner not placed | the picture | Drag DSL onto its tape mark. | Downstage left. N of 4 corners on their marks. Click a corner to select it, nudge it with the arrow keys, and press Enter when the crosshair is in the right spot. |
| Solved | the picture | Check the result. | The grid on the picture should meet the corner marks. Then press Next. |
| No solve | the picture | The camera does not solve with these corners. | Check the rectangle's size and that each corner sits on its tape mark. |

Simple status line before the solve: the rectangle problem plus "Then the
rectangle is drawn on the picture.", then "Drag each corner onto its tape
mark. The camera solves once all four are moved: N to go."

### Advanced

Top to bottom: the hint (while no camera position), **Camera Position**, the
picture (the hint above it otherwise), Refresh Image and Reset Pins, "To
solve the camera, add …" (muted, hidden once it solves), the tip, **Points**,
the status, the caution, the solved values, **Distances to check**. Advanced
never touches the grid.

| Topic | Decision |
|---|---|
| Camera position | Required and first: "**Where is your camera?** Its lens, from the point of origin, in m: X = stage left, Y = upstage, Z = up. An estimate within roughly 2 m per axis is good enough." Partly filled: "Enter all three values." |
| Kinds | **Known**: X / Y / Z typed. **Triangulated**: the distances to two other points (known or triangulated) and its Z, default 0 |
| Rules | The camera position, at least two known points, at least four pinned points. A known point may stay unpinned (a point outside the picture still counts as one to measure from); a triangulated point must be pinned (two distances leave two places mirrored across the line between its points). No point measured from itself through others. **Add triangulated point** and **New triangulated point** appear once two known points read |
| Picture | Over the picture the loupe follows the pointer. A click opens **Place here** at that spot (a dashed ring marks it): the unpinned points (name, Known / Triangulated), a separator, **New point** (X/Y/Z), **New triangulated point** (disabled with "Needs two known points" until then). Choosing a point pins it there |
| The menu | Placed 12 px right of and below the spot, flipped to stay inside the picture; its first item takes the focus. **×**, Esc or a press outside the menu and the picture close it. While it is open the loupe stays on the spot (clear of the menu) and the arrow keys move the spot 1 px (Shift 10) unless a field has the focus |
| New point | The menu becomes a form: **Point** (prefilled P<n>), X / Y / Z (Z prefilled 0), **Back**, **Place point** (disabled until X / Y / Z read) |
| New triangulated point | **Point**, two pairs of **From** + **Distance**, **Z** (empty means 0); **Place point** once both From differ and both distances are above 0. Each From list leaves out the point chosen in the other |
| While a form is open | A click on the picture moves the spot |
| A pin | Dragging moves it. A click on it opens its menu, titled "P1 · Known": **Remove pin** ("Keeps the point") and **Delete point** (error colour) |
| Point list | Below the picture. Each row: name, kind chip, status chip, for a triangulated point its X / Y / Z, **Remove pin** (when pinned), **Delete**. Fields: **Point** (never labelled "Name": browsers and password managers fill in the operator's own name; `autocomplete="off"` and the ignore attributes), then X / Y / Z, or Z and From / Distance / From / Distance. A reason line under a row with a problem. "No points yet." while empty. **Add known point** (for a point outside the picture) and **Add triangulated point** under the list |
| Status chips | Not placed (a problem), Not pinned, Pinned, then "0.4 px" or "0.4 px · 3 cm" (warning when over the caution limits) |
| Triangulated X / Y / Z | Shown before any solve, by trilateration, once its two points have positions and it and both of them are pinned. Of the two mirrored places, the pin's side of the line between its two points' pins picks one (the image's y runs down, so seen from above the floor the sides swap). The solve then refines it. Dashes until then |
| Deleting | Removes the point from every From that named it |
| Tip | "Click the picture where a point is and choose it from the menu, or make a New point or New triangulated point there. Click a pin to remove it or delete the point. While a menu is open, the loupe stays on its spot and the arrow keys move it (Shift for bigger steps). Drag a pin to correct it." |
| Missing for the solve | Named as "the camera position", "N more known point(s)", "N more pinned point(s)", joined with "and" |

The Advanced hint, first match wins:

| When | Above | Heading | Sub-line |
|---|---|---|---|
| No camera position | Camera Position | Enter the rough camera position first. | Roughly 2 m per axis is good enough. |
| Fewer than two known points | the picture | Enter two points with known coordinates far apart, like the DSL and DSR corners. | Click where one is on the picture, choose New point and type its X/Y/Z. N of 2 entered. |
| Fewer than four points | the picture | Enter one more / two more known points or triangulated points. | Click where it is on the picture and choose New point or New triangulated point. |
| A point to pin (a triangulated one always, a known one until four are pinned) | the picture | Pin P3 on the picture. | Click where it is on the picture, then choose it from the menu. N of at least 4 pinned. |
| Solved | the picture | Check the result. | Check the grid on the picture and the distances to check, then press Next. More known or triangulated points give a better result. |
| No solve | the picture | The camera does not solve with these points. | Check the pins and the measurements. |

A point's reason line, as the first problem found:

| Problem | Text |
|---|---|
| Known, a value missing | Enter its X, Y and Z. |
| No From chosen | Choose the two points its distances are measured from. |
| Both From the same | Choose two different points. |
| A distance missing or out of range | Enter both distances, between 0 and 200 m. |
| Z unreadable | Enter its Z. |
| Not pinned | Pin it on the picture: its two distances allow two places, mirrored across the line between its points. |
| A loop | Its distances lead back to itself. |
| A From deleted | One of its points was deleted. |
| A From unplaced | It is measured from P2, which cannot be placed. / It is measured from a point that cannot be placed. |
| Height | A distance is shorter than the height between the two points. |
| Same place | Its two points are at the same place. |
| Distances cannot meet | The two distances differ by more than their points are apart. / The two distances add up to less than their points are apart. |

**Distances to check:** "Measure these between the marks. A distance that
differs means a typed or measured value is wrong. Pairs a triangulated point
was measured from are left out." A table (Between, Distance) of every pair of
placed points except those, computed from the typed and solved positions in
the active units. A click or Enter on a row draws that pair on the picture as
a labelled line through the solved camera. Display only, no field for the
measured value.

### Switching modes

| From | Decision |
|---|---|
| Simple → Advanced | The corners become known points named DSL, DSR, USR, USL with their X / Y / Z (when the rectangle reads) and their pins (when placed). The camera position carries over |
| Advanced → Simple | With points: the shared modal "Switch to Simple?", "Simple uses the four corners of a rectangle. Switching clears all N points and their pins.", **Cancel** / **Clear points and switch** (only the confirm is red) |

## Solve

| Topic | Decision |
|---|---|
| One fit | Both modes go through one Levenberg-Marquardt fit: the 7 camera values plus each triangulated point's X / Y, against the pins (`PIXEL_SIGMA` 1 px) and the triangulated distances (`DISTANCE_SIGMA` 0.04 m). Pins are undistorted with the lens pair first |
| Needs | Four pinned points: 7 camera unknowns, 2 equations per pin; a triangulated point's two distances fix its own X / Y |
| Seeds | With a camera position: that position looking at 0/0/0, roll 0, at the saved FOV, 40° and 90°. With four pinned known points at one height (within 1 cm, no three in a line): the homography decomposition. Simple always has the second; Advanced always has the first |
| Triangulated points | Start at their trilateration, chained after the points they are measured from, the mirror place picked by the pin as seen from the seed. Two distances that miss by up to `3 √2 · DISTANCE_SIGMA` meet halfway; more leaves the point unplaced with its reason |
| Mirror sides | Every seed is fitted twice, with the pin-picked places and with all of them flipped. An upright camera (at or above the lowest point, pitch ≤ 0) beats one that is not, then the lowest cost wins |
| Answer | Camera, per point: status, reason, pixel error, distance error, position; largest error; the caution reasons |
| Unsolvable | No finite fit or a FOV outside 10-170°: "No camera fits these points. Check the pins and the measurements." |

### Something might be wrong

One caution (`.notice.warning`) in Camera Calibration and Review, headed
"Something might be wrong", listing every reason that applies and ending
"The camera still applies. Check the measurements, then pin again." It never
blocks Apply.

| Reason | Fires when | Text |
|---|---|---|
| Lens not measured | Simple, `lens_distortion` on, and the lens has no values from this run (not fitted, loaded or fine-tuned) | The lens was not measured. Four corners fit any lens, so a missing lens does not show in their errors. Measure it in the Lens step unless straight lines in the picture are straight. |
| Camera far from the position (Simple) | A position was entered and the camera is further from it than max(1 m, 10 % of its distance to the points' centre) | The solved camera is 2.5 m above the camera position you entered (… in all, allowed 1.6 m). Each axis carrying at least a quarter of the gap, in stage words, in the active units. With a Custom origin it adds: Was the camera measured from the same point of origin as the rectangle's centre? |
| Camera far from the position (Advanced) | Any axis off by more than max(2.5 m, 10 % of that distance), headroom over the promised 2 m | As above, "allowed 2.5 m per axis" |
| Field of view | Outside 20-120° | The solved field of view, N°, is outside what cameras usually have. |
| Floor | Camera below the lowest point | The solved camera is below the floor. |
| Up | Pitch above 0 | The solved camera looks upward. |
| Point disagrees | A pin's error over 5 px | P2 disagrees with the other points by N px. |
| Distances | A triangulated point's distance error over 3 σ (12 cm) | P4's distances disagree with the picture by N cm (in in Imperial). |

## Review

| Part | Decision |
|---|---|
| Picture | The grid through the solved camera, each placed point as a ring with its name, 0/0/0; the full-screen button |
| No solve | A red `.notice.error`: "No camera solve yet." with "To solve the camera, add … Go back to Camera Calibration to finish it.", or the solve's error. **Apply & Finish** is disabled |
| Caution | As above |
| Camera | Pos X / Y / Z, Pitch, Yaw, Roll, FOV |
| Lens | Barrel / fisheye, Edge fit, Lens coverage: the meter and its rating, "Fine-tuned", "Loaded: name" (", fine-tuned" after a change), or "Not measured" |
| Calibration | Mode, Points, Pinned, Largest error, Entered position (or "Not entered"), and in Simple the Camera location (or "Not chosen") |
| Distances to check | Advanced only, the same table, not clickable |
| Grid | Width, Depth, Spacing, X / Y / Z Offset, and Change: "From the rectangle" (Simple) or "Unchanged" (Advanced) |
| Buttons | **Back**, **Discard & Leave** (the modal "Discard the Setup Wizard?", "Nothing from this run is saved. The station keeps its current camera, lens and grid.", **Cancel** / **Discard & Leave**), **Apply & Finish** |
| Apply | Writes the camera, the lens pair, the mode and the marks with their pins, and in Simple the grid before the camera; each response's `.ok` is checked. Help: "Apply saves the camera, the lens and the points. In Simple mode it also saves the rectangle as the grid. The green grid is the one the Operator Screen will draw." |

## Help drawer

One `openfollow/web/help/` file per text, matching the mockup:

- **Video Source:** the snapshot of this input is used in every later step.
- **Lens:** what the step measures; Lines (straight in reality, edges and
  corners tell the most, selecting points and lines, the keys); Coverage (the
  rating, Low is not applied, Edge fit needs a line into a corner); Load and
  save; Full screen.
- **Camera Calibration (Simple):** the rectangle and the grid; point of
  origin and Custom; the starting rectangle and the location; the hint; the
  diagonals; dragging and selecting corners; the optional camera position and
  its check; Units; Refresh Image; Something might be wrong (and why a
  missing lens hides behind four corners); Full screen; Switching to Advanced.
- **Camera Calibration (Advanced):** the camera estimate and its per-axis
  check; known and triangulated points (two known points far apart solve
  about as well as four, close together two to three times worse); placing
  points on the picture; Solving; Units; Refresh Image; Distances to check
  (they check the shape, not where it sits); Something might be wrong; Full
  screen; Switching to Simple.
- **Review:** what Apply saves.

## Beyond the mockup

What the mockup cannot show, decided here:

- Opening the Setup Wizard on a station with a stored calibration restores
  its mode, the rectangle (from the grid) or the marks with their pins, as
  today's wizard restores the corner pins. The camera position starts empty;
  the lens step starts from the station's pair, as today.
- A snapshot of another resolution scales every pin with the picture.

## Config

| Topic | Decision |
|---|---|
| Mode and marks | `camera.calibration_mode` (`"simple"` / `"advanced"`) and `camera.calibration_marks`: `id`, `name`, `kind` (`known` / `triangulated`), `x`, `y`, `z`, `from_a`, `from_b` (ids), `distance_a`, `distance_b`, `pin_x`, `pin_y` (fractions of the snapshot, NaN = not pinned), `position` (solved, triangulated only). At most 12 marks |
| Saved lens calibrations | Not in `config.toml`: one small JSON file per calibration in a `lens-calibrations` directory beside it (as the web backups sit in `backups/web`), so they stay on the station and are never exported, imported or pushed. Each file holds `name` (1-60 characters, unique ignoring case), `k1`, `k2` (the fold check), `saved` (date) and `openfollow_version` (the `__version__` that wrote it), so a later version can tell old calibrations apart and warn about them. Saving a name that exists rewrites its file. No limit on the number of files. The list is filled only by **Save lens calibration**: a new station has none, and nothing ships or imports any A file that does not parse or fails the checks is left out of the list with one WARNING, never deleted |
| Calibration points feature | Retired: `calibration_points` leaves `FEATURE_KEYS` and the experimental set; Advanced is always available. A stored `shown_features` entry is dropped as unknown |
| Migration | Old `camera.calibration_points` with entries ⇒ Advanced: the four grid corners become known points with `camera.corner_pins` as their pins, each old point a triangulated point measured from its two corners, `height` as its Z. Otherwise ⇒ Simple with `corner_pins` as the corner pins. The old keys are read once on load and never written |

## Simulation findings

From the mockup's solver against the Concert picture (camera 0 / 13 / 12 m,
pitch -48°, 70°, lens -0.2 / 0.03, 12 x 6 m rectangle). These set the
thresholds above; the Python tests pin them again.

| Case | Result |
|---|---|
| Simple, exact pins, true lens | Exact camera, with a good, 5 m-off, wrong-side or no camera position |
| Simple, lens fitted from the suggestions (-0.197 / 0.026) | Camera within 1 cm |
| Simple, lens ignored | Camera 2.4 m off, pin error 0 px: only the position check and the lens caution catch it |
| Advanced example (Reference Point, DSL, DSR known; riser corners and USR triangulated) | Camera within 2 cm, triangulated points within 6 mm; pre-solve X / Y / Z match the solve |
| Advanced, DSL and DSR (12 m apart) plus two triangulated points, tape right within 5 cm | Worst point median 7 cm, p95 12 cm; four known points 6 / 10 cm |
| Advanced, Reference Point and DSL (6 m apart) | 16 / 35 cm; a 6 m-off estimate flips both triangulated points to the mirror side until both sides are tried (the camera below the floor loses) |
| Advanced, one triangulated distance 46 cm long | Two triangulated points flagged (27 cm, 15 cm) |
| Advanced, a known X mistyped by 0.5 m | Camera 3.9 m off the position: flagged |
| Pin noise σ 1 / 2 / 3 px (earlier Concert picture) | Camera off by median 0.14 / 0.26 / 0.38 m, p95 0.27 / 0.51 / 0.78 m |
| Upstage corners pinned 5 / 10 / 20 px low (earlier picture) | Camera 0.3 / 0.6 / 1.2 m off |
| Depth typed 8.5 / 9 m instead of 8 (earlier picture) | Camera 2.6 / 4.9 m off |

## Deferred

A warning about lens calibrations saved by an older version is decided when a
version first needs one; until then the files only record the version.

## Implementation steps

### 1. Config (`add-config-field` skill) and the lens calibration files
- [ ] `CameraConfig.calibration_mode` and `calibration_marks:
      list[CalibrationMarkConfig]` as above.
- [ ] `problem()` per mark and per list (kinds, From exist and differ, no
      loop, distances in `(0, 200]` m, pins inside the snapshot reach,
      counts); a rejected entry is dropped with one WARNING per distinct set,
      never clamped.
- [ ] The saved lens calibrations store (`openfollow/web/lens_calibrations.py`):
      list, read one, save (atomic write, replace by name ignoring case),
      each file with `openfollow_version`; a file name derived from the
      name, never from client input as a path.
- [ ] Migration of `calibration_points` / `corner_pins` in `load_config`,
      before `_filter_known` (also for import and push payloads).
- [ ] A grid write in Simple mode drops the corner pins (generalise
      `_forget_corner_pins_off_grid`); Advanced marks are left alone.
- [ ] `FIELD_RULES` for every editable field, matching the server bounds.
- [ ] Tests (`tests/test_configuration.py`): valid lists, wrong type, range,
      enum, dangling / self / looping From, triangulated unpinned, fewer than
      two known, more than the cap, TOML round trip, migration both ways
      (with / without points, with / without pins).
- [ ] Tests for the lens store (`tests/test_lens_calibrations.py`): save and
      list round trip with the version recorded, replace ignoring case, names
      empty / too long / path-like (`../x`, `a/b`), a folding pair, a broken
      or hand-edited file left out with one WARNING, many files, export and
      push never carrying them.

### 2. Solver (`scene/calibration_points.py`)
- [ ] Generalise `solve_with_points` to marks: known points fixed,
      triangulated points free in X / Y at their Z, chained trilateration
      with `_pick_by_pin`, both mirror sides per seed, upright before cost.
- [ ] Seeds: the camera position at three FOVs and the coplanar homography;
      Simple goes through the same entry point.
- [ ] Pre-solve placement of triangulated points from their pins (the
      image-side rule), for the rows.
- [ ] Per-mark answer: status, reason, pixel error, distance error, position.
- [ ] `plausibility(solve, hint, mode, lens_measured, origin_custom)`
      returning the reasons with the axis wording; thresholds as above.
- [ ] Tests (`tests/test_calibration_points.py`, `tests/test_solver.py`):
      the Concert picture's camera and a front-of-house camera through known
      poses with absolute pose asserts, the mirror case, an off-picture known
      point, chained triangulated points, the pre-solve side rule, the
      lens-ignored case, a mistyped point flagged, each caution reason firing
      and not firing, the axis wording; mutation-check every regression test.

### 3. Routes (`web/routes.py`)
- [ ] `/api/wizard/solve` takes `marks`, `mode`, `hint`; strict number
      reading, 400 on malformed, 409 when superseded, 422 when unsolvable.
- [ ] Saving a lens calibration (`name`, `k1`, `k2`): 400 on a malformed
      body, an empty or too long name or a folding pair; replaces a name
      already saved. The saved list reaches the page with the wizard.
- [ ] `/api/wizard/project` drops the reference point; remove
      `/api/wizard/unproject` if nothing calls it.
- [ ] Tests (`tests/test_wizard.py`): malformed bodies per failure mode,
      the answers' shape, the superseded turn, saving and replacing a lens.

### 4. Setup Wizard (`web/templates/wizard.tpl`)
- [ ] Four steps; Preparation, Grid Setup, Camera Position and Reference
      Mapping removed with their state, overlays and session keys (bump the
      session schema).
- [ ] The help drawer on every step, per mode in Camera Calibration.
- [ ] The loupe placement, point selection and full screen (shell, toolbar,
      help panel, Esc) shared by every picture.
- [ ] Lens: line selection, Delete / Backspace, point selection, the Load
      and Save dialogs, the loaded state in the result box and Review.
- [ ] Camera Calibration, Simple: location, optional position box,
      rectangle with the diagonals note, point of origin with Custom, the
      starting rectangle, corner drag and selection, the "all four placed"
      gate, the corner list, the 0/0/0 mark, the hints.
- [ ] Camera Calibration, Advanced: camera position first, the picture
      menus and forms, the point list, pre-solve triangulated positions, the
      hints and the "To solve the camera, add …" line, Distances to check.
- [ ] Unit switch converting every entered length; the status line, the
      caution, the solved values with Lens.
- [ ] Mode switch rules and the Advanced → Simple confirmation.
- [ ] Review and Apply as decided.
- [ ] Tests: `tests/test_wizard_js.py` for the pure functions (starting
      rectangle, diagonal, pre-solve placement and side rule, From
      exclusion, settings-file lens reading, unit conversion on switch),
      `tests/test_wizard.py` for the page.

### 5. Camera tab and feature list
- [ ] The Camera form lists the mode and the marks read-only.
- [ ] Remove `calibration_points` from `FEATURE_KEYS` / the experimental set
      and every `data-feature="calibration_points"`.

### 6. Docs
- [ ] The help drawer files listed above; `help/camera.md` and `help/grid.md`
      point to the Setup Wizard.
- [ ] `docs/architecture/web.md` Setup Wizard section,
      `docs/architecture/config.md` (the marks, the lens calibration files
      and why they stay on the station).
- [ ] What's new entry (operator action changed).

### 7. Verification
- [ ] `make ci-remote` on pi59.
- [ ] Station check on pi66: the Concert picture with its stored
      calibration reproduced in both modes, metric and imperial; a real
      camera, tape-measured distances against the computed ones; the
      caution with a deliberately wrong camera position and with the lens
      step skipped; a lens saved, loaded, and loaded from an exported
      settings file; full screen on a low-resolution display; a migrated
      config from the current release.
- [ ] Web UI checked in Safari (full screen included) and on a phone.
