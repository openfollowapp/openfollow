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
- **Arrow keys** move it 1 px; **Tab** selects one too. **Shift** + arrow
  moves 10 px, but no text mentions it (it confused testers), and the copy
  says "move", never "nudge", which reads oddly to non-native speakers.
- **Enter** confirms it (Simple: places the corner where it is, also one that
  started on its mark). **Esc** or a press or focus anywhere else ends the
  selection. After Enter or Esc the point keeps the focus, so the next Tab
  moves on from it.

### Full screen

For screens where the picture is too small to work on.

| Topic | Decision |
|---|---|
| Entry | A button in each picture's top right corner (four-corner icon, `aria-label` "Full screen") |
| The picture | Named once, "Camera snapshot", on the drawing over the snapshot; the snapshot itself is decorative, so a drawing with nothing on it is no second image. Its focus ring is drawn inside the frame and follows its rounded corners |
| Shell | The picture moves into a full-screen shell with one toolbar line, and back on exit. The shell asks the browser for full screen; where it refuses (iPhone Safari, a framed page), the shell fills the window |
| Picture | Keeps the snapshot's aspect ratio: as wide as the space below the toolbar allows without being taller, centred on black |
| Toolbar | Step title (Calibration adds "· Simple" / "· Advanced"), the current hint, a status, the step's action, **?**, **Exit full screen**. On a phone (≤ 640 px) only the hint and the buttons |
| Hint | Lens: "Use or reject each dashed suggestion, or click the start and the end of a straight edge." with no lines, "Click the other end of the straight edge." with a line started, else the lens result. Calibration: the hint box's heading. Review: "The green grid is the one the Operator Screen will draw." or "The camera is not calculated yet." |
| Status | Calibration: "Largest error N px", plus "· Something might be wrong" in the caution colour when the caution has reasons. Lens: "A line does not fit the others" in the caution colour |
| Action | Lens only: **Point off** and **Delete line**, each disabled without a selected point or line |
| Inside | Everything works as on the page: dragging, selecting, the Advanced menus and forms, the loupe |
| Esc | Ends full screen and cancels whatever is active: a selection, an open menu, a started line, the help panel. The browser's own exit (Esc, a gesture) ends it the same way. Exit puts the picture back, with the page scrolled where it was, and focuses its full-screen button |
| **?** | The **?** button, or the **?** key unless a field has the focus, toggles a non-modal panel under the toolbar's right end; it stays open while the picture is used and closes with its ×, **?** or full screen ending |

The panel, titled **Full screen**, holds one or two sentences and a key table
for the picture, each ending with "**?** Show or hide this help":

| Picture | Sentences | Keys |
|---|---|---|
| Lens | "Check each dashed suggested line: the check mark uses it, the cross removes it. Or click the start and the end of a straight edge. Click a line to select it. Drag a point to correct it." | Tab: Next suggestion bubble or line. With a point selected: the next point on its line, after the last the first again · Shift + Tab: The previous point on its line, before the first the last again · Arrows: Move the selected point 1 px · Enter: Confirm the point and end the selection · Space: Switch a middle point off or on · Delete or Backspace: Remove the selected line, or cancel a line you started · Esc: End full screen (also cancels the selection) |
| Calibration, Simple | "Drag each corner onto its tape mark. The loupe shows the picture 4× larger. Click a corner to select it." | Tab: Select the next corner · Arrows: Move the selected corner 1 px · Enter: Place the corner where the crosshair is · Esc: End full screen (also cancels the selection) |
| Calibration, Advanced | "Click the picture where a point is, and choose the point from the menu. Or create a New known point or a New distance point there. Click a pin to remove the pin or delete the point. Drag a pin to correct it." | Arrows: Menu open: move the spot 1 px. Pin selected: move the pin 1 px · Tab: Next menu item, field or pin · Enter: Choose the menu item, or place the new point · Esc: End full screen (also cancels the selection) |
| Review | "The picture shows the calculated grid, the points and 0/0/0, as the Operator Screen will draw them. You cannot change anything here." | Esc: End full screen |

On the page (not in full screen) Esc keeps its order: close an open menu, end
a selection, cancel a started line.

## Video Source

Unchanged: the source picker, **Save** and **Save & Next**.

## Lens

Unchanged: the suggestions, traced lines, the five-step rating, Refresh
Image, Clear lines. Changed:

| Topic | Decision |
|---|---|
| Suggestions | Each dashed suggestion carries a bubble beside it with a pointer to a dot on the line: a circle with a check (**Use this line**) and a circle with a cross (**Reject this line**). The bubble is a 72 × 38 px rectangle with 8 px corners; bubble and pointer are one outline. The pointer is an equilateral triangle, 18 px high, with a 3 px rounded tip that stops 4 px short of the line's centre, so it points at the line without touching it. Placement follows tooltip and map-popup libraries: the anchor sits off the line's middle (from 38 % or 62 % of its length outwards); the bubble goes above or below a flat line and beside a steep one, may slide along that side with the pointer kept on the straight part of the edge, and flips to the other side when there is no room. It never covers its own line and avoids other suggestions, the frame's edge and other bubbles. Its buttons do not move on hover. Pointing at a bubble (hover or Tab) lights up its line and pointer and dims the others; pointing at a line lights up its bubble. A used line loses its bubble and becomes a traced line (select it, Delete removes it); a rejected line disappears with its bubble, from the picture and the fit, until **Refresh Image** suggests the lines again. After a choice by keyboard the focus moves on to the next bubble. A click on a dashed line still uses it. Lines traced by hand get no bubble |
| Hint box | One blue `.notice` above the picture, always the one next thing to do (heading, then the sub-line), mirrored in the full-screen toolbar: a line started: "Click the other end of the straight edge." / "Esc cancels the line."; a point selected: "Move the point exactly onto the edge." / "Drag it, or use the arrow keys while the loupe shows the edge. Press Enter when it is in the right place."; suggestions left: "Check the suggested lines." / "Confirm each of the lines that is straight in reality by pressing ✔. Reject the others, such as a curtain, a cloth or a cable, by pressing ✖. N left to check. Or load a saved lens calibration."; a loaded calibration and no lines: "A saved lens calibration is loaded." / "Press Next, or draw straight lines to measure the lens again."; no lines: "Draw straight lines on the picture." / "Click the start and the end of an edge that is straight in reality, such as a stage edge, a tape line or a truss. Then move its points exactly onto the edge. Or load a saved lens calibration."; a misfit: "Check the line that does not fit." / "It is drawn in the warning colour. Move its points onto the edge, or delete it if it is not straight in reality."; rated Good or better on lines that stay near the middle (shown as Fair): "The result may already be good in the middle." / "If possible, add lines near the edges of the picture to measure them too. Or press Next."; rated Good or better: "The lens is measured." / "Press Next." followed by the weak edges, if any ("If possible, add lines near the top and left edges to confirm it."), which only the hint box shows; lines that already agree (Rating hint): "The result may already be good." / "If possible, add a line near the top edge to confirm it. Or press Next."; applied otherwise: "Improve the lines, or add more." / "Select a point and move it exactly onto the edge. Lines near the edges and corners of the picture help the most." plus the rating hint; Not usable: "Draw more straight lines." / "Click the start and the end of a straight edge. Then move its points exactly onto the edge." plus the rating hint. It replaces the explanatory paragraph under User input; the tip stays. Its words change on a tap's click, never between press and click, and a started line keeps the box's height, so the picture never moves under a finger |
| User input | The two sliders and the **Lens centre** row (**Right of the picture centre (px)**, **Above the picture centre (px)**) are folded away under **User input (optional)**, the `inline-advanced` disclosure Simple's camera position uses; closed on entry. A lens that cannot follow the picture out to its corners opens it, so its error is never hidden: "With these two values, the overlay cannot follow the picture out to its corners. Set one of the values closer to 0." while the pair alone falls short, else "With this lens centre, the overlay cannot follow the picture out to its corners. Set the lens centre closer to 0."; a centre field out of range, empty or not whole pixels: "Enter a whole number from −275 to 275." (the snapshot's bound), once the field is left |
| Weak edges | The hint (`lens_fit.coverage_hint`) names every edge whose outer band (15 % of that side) holds fewer than 5 points, weakest first, always "If possible", since a picture may show nothing straight there: "If possible, add a line near the top edge.", "If possible, add lines near the top and bottom edges.", "If possible, add lines near the top, left and bottom edges.", all four: "If possible, add lines near all edges of the picture."; rated Good or better the same sentence ends "to confirm it.". Before the edges it asks "Corner correction needs a line that reaches a corner of the picture." while the corner correction is unmeasured and no line reaches a corner |
| Rating hint | When an edge band is still weak but the lines already agree (at least 4 lines, none flagged, the points within 1.5 px RMS of their fitted curves, the corner correction measured and rated Fair or better: lines that never reach a corner agree with a wrong lens just as well): "The result may already be good. If possible, add a line near the top edge to confirm it." With several weak edges it names them all ("If possible, add lines near the top and bottom edges to confirm it."). Otherwise the weak-edges hint |
| Selecting a line | A click anywhere along a traced line selects the whole line, drawn in the text colour. A line just added (a tapped suggestion or a traced edge) is selected |
| Deleting | **Delete** or **Backspace** removes the selected line wherever the focus is, except in a field or with a dialog open; with a line started, the key cancels it instead. **Delete line** does the same |
| Points | As [Selecting a point](#selecting-a-point). Each traced line is one Tab stop, its first point; its other points are not in the Tab order. With a point selected **Tab** moves on to the next point of the same line and **Shift+Tab** back (the loupe follows), round the line: after the last point comes the first, before the first the last. **Enter** ends the hold and leaves the focus on the point; then Tab leaves the line in page order (the next line's first point, the remaining bubbles, the buttons, **Next**) and Shift+Tab moves back past the line, so the keys never trap the focus on the picture. **Esc** and a press elsewhere also end the hold; Esc leaves the focus on the point too. The suggestion bubbles keep their own Tab order. **Point off** and **Space** switch a middle point off or on, as today: a middle point that found no edge starts off |
| Buttons | **Refresh Image**, **Point off**, **Delete line**, **Clear lines**, **Load lens calibration…**, **Save lens calibration…** in one row under the picture |
| Result line | A fit: "Fair – barrel / fisheye −0.200, corner correction −0.071" (the rating, then the pair), as the loaded line below reads; the lens centre in use follows when one is set, typed or loaded, since the Lens step never calculates it: ", lens centre 42 px right, 18 px up" (an axis that rounds to 0 px left out, nothing when the centre is 0). A corner correction the lines did not measure is the fit's estimate, drawn toward no lens, applied with the pair and marked: "corner correction −0.183 (estimated)"; a Not usable fit, or one with a line that does not fit the others, adds " – not applied" and is shown at the caution level. The ratings, best to worst: Excellent (σ ≤ 2.5 px), Good (≤ 5 px), Fair (≤ 10 px), Poor (≤ 20 px), Not usable (above) |
| Tip | "Click a point or press Tab to select it. The loupe stays on it, and the arrow keys move it by one pixel. While a point is selected, Tab goes round the points of its line. Press Enter when the crosshair is in the right place; then Tab moves on to the next line. Esc or a click somewhere else ends the selection. Space switches a middle point off or on. Delete or Backspace removes the selected line. Esc cancels a line you have started. If you skip this step, the current lens values stay." |

### Load lens calibration

For a picture with no straight edge to trace, with the same camera and lens at
the same zoom. A dialog, **Load Lens Calibration**:

| Part | Decision |
|---|---|
| Saved on this station | A radio list (`.tier-list`): the name, then "Barrel / fisheye −0.118 · Corner correction −0.021 · 2026-09-12 · 1920 × 1080", with " · Lens centre 42 px right, 18 px up" after the pair when the centre is not 0. Empty: "No lens calibrations are saved on this station." A list that cannot be read: a `.notice.error` "The saved lens calibrations could not be read." with the station's reason; the settings file still loads |
| Other picture shape | An entry measured on a snapshot of another aspect ratio (over 1 % apart) carries a caution line: "Measured on a 1920 × 1080 picture, this one is 1280 × 1024. The values only carry over at the same picture shape." It can still be loaded. A settings file records no size, so it never warns |
| From a settings file | "Only the lens values of an exported `.ofsettings` file are loaded." and **Choose settings file…** (accepts `.ofsettings` and `.openfollowsettings`). The file is read in the browser; only `camera.lens_k1` / `camera.lens_k2` / `camera.lens_cx` / `camera.lens_cy` are used, a missing centre read as 0 |
| A good file | Becomes an entry under the button, selected: the file name and its values. The button then reads **Choose another file…** |
| A bad file | A field error under the button: "This is not an OpenFollow settings file." (not JSON, or no `camera` object), "This settings file has no lens calibration." (either value of the pair missing, or any value not a finite number), "With the lens values of this file, the overlay cannot follow the picture out to its corners." (a lens the station would clamp or pull back) |
| Traced lines | "Loading removes the N line(s) on the picture." |
| Buttons | **Cancel**, **Load** (disabled until an entry is selected) |
| After loading | The traced lines go, User input shows the pair and the lens centre. The result box (info, no meter) reads "Loaded “name” – barrel / fisheye x, corner correction y" and the centre when it is not 0, under it "Saved on this station on 2026-09-12." or "From the settings file name.ofsettings."; after a User input change "Changed by user input after loading." comes first. A later fit that applies (rated above Not usable) replaces the loaded pair |

### Save lens calibration

A dialog, **Save Lens Calibration**, enabled once the lens has values: fitted,
loaded or typed under User input in this run, or the pair the station already had (so an
upgraded station can name the lens it has):

| Part | Decision |
|---|---|
| Field | **Calibration name**, at most 60 characters, placeholder "Main stage – FOH wide", `autocomplete="off"` and the password-manager ignore attributes |
| Values | "Barrel / fisheye x · Corner correction y" under the field |
| Same name | A name already saved (trimmed, ignoring case) shows the warning "A calibration with this name is saved on this station. Saving replaces it." The dialog opens once the list is in; a list that cannot be read shows a `.notice.error` "The saved lens calibrations could not be read." with the station's reason and "If a calibration with this name is saved on this station, saving replaces it.", and Save stays possible |
| Buttons | **Cancel**, **Save** (disabled while the name is empty); **Enter** in the field saves |
| Saving | Stored on the station at once as its own file (see [Config](#config)), with the OpenFollow version that saved it and the snapshot's size, independent of Save and finish. A success notice under the buttons: "Saved “name” on this station." |

## Camera Calibration

Section head: **Camera Calibration**, the subtitle ("Drag four corners onto
their tape marks" / "Place measured points on the picture"), **Switch units
to Imperial** / **Metric** (small, left of the **?**), **?**.

| Topic | Decision |
|---|---|
| Mode switch | Labelled **Calibration**, the segmented switch (`.seg-toggle`): **Simple** "Four corners of a rectangle", **Advanced** "Known points and distance points" |
| Hint box | One blue `.notice` that always names the next thing to do, moved above the part of the page it is about |
| Per mode | The subtitle, the hint, the tip under the picture and the help drawer |
| Units | **Switch units** changes the station's unit system (the setting in General) and converts every length already entered. Imperial reads ft / in text (`26' 3"`, `26 ft 3 in`, a bare number as feet) with the "Stored:" metric echo under each field, left out while the field shows an error |
| Refresh Image | Takes a new snapshot (someone stood on a mark). The pins stay; a snapshot of another resolution moves them with the picture. Success notice for 4 s: "New snapshot loaded. The corners stay where they are." (Simple) / "New snapshot loaded. The pins stay where they are." (Advanced), read out each time it shows, a second snapshot within the 4 s too |
| Reset Corners / Reset Pins | One button, named by mode. Simple, **Reset Corners**: every corner back to the drawn rectangle. Advanced, **Reset Pins**: every pin removed, the points kept |
| Status line | Under the picture: the waiting text, "Calculated from N pinned points, largest error N px." (Simple: "Calculated from 4 corners, largest error N px."; success; N counts the pinned points the page sent, a pin behind the camera included, as Review's Pinned does) or "No camera fits these points. Check the pins and the measurements." (Simple: "No camera fits these corners. Check the corners and the measurements."; error) |
| Solved values | Pos X / Y / Z, pitch, yaw, roll, FOV, and **Lens**: the pair in use, or **Not measured** in the caution colour |
| Next | With one or two camera position fields filled: the field error, the box opened (Simple), no step change |

### Simple

Top to bottom:

Four numbered steps, each headed by a question in the same bold style, so it is clear where to start. They open in order: a step is greyed out (`step-locked`, `inert`) until the one before it is done. Step 2 opens with a location chosen, step 3 with a valid width and depth, step 4 (the heading, the picture, the corner list and the tip) with the point of origin chosen and, for Custom, its centre complete. A rectangle already entered (a reopened Setup Wizard) counts as step 1 done. Refresh Image is disabled with step 4; Reset Corners only appears once a corner has been moved (and goes again after a reset). **Next** stays disabled until all four corners have been placed (dragged, moved with the arrow keys, or confirmed with Enter); the hint names the next corner. Advanced locks nothing and always shows Reset Pins.

- **1. Where is your camera?** A dropdown (Choose…, Front of House (balcony
   railing / ceiling), Front Truss, Back Truss, Stage Left, Stage Right), and a
   collapsed box **Optional: enter a rough camera position to check the
   result**, holding the Camera Position fields (below).
- **2. What is the size of your rectangle?** Under the heading: "Mark the
   four corners of the **largest rectangle** that fits on the floor of the
   performance area. The camera must see all four corners. Only the corners
   need a mark, such as a tape cross. Enter the size of the rectangle." Then Width and Depth, empty with no
   placeholder (an example number read as a value already entered), and the
   diagonals note.
- **3. Where is the point of origin?** **Point of origin for all position
   data (0/0/0)**, an active choice: it starts at "Choose…", and the
   operator picks "Default: centred between DSL and DSR" (the rectangle's centre at
   X 0, Y depth / 2, Z 0) or "Custom". Until then the hint above the step
   reads "Choose the point of origin." / "All position data that the
   station sends is measured from this point (0/0/0). It must match the
   point of origin of your console or tracking system." "Custom" which asks **Where is the
   centre of the rectangle, measured from the point of origin?** with X
   (stage left +), Y (upstage +), Z (up +), filled with the default on
   switching.
- **4. Place the corners on the picture** above the picture, Refresh Image
   and Reset Corners, the tip, **Corners**, the status, the caution, the
   solved values. Simple has no Distances to check.

| Topic | Decision |
|---|---|
| Location | Orients the starting rectangle only, as a camera at Front of House 0 / -20 / 8, Front Truss 0 / -2 / 8, Back Truss 0 / 9 / 7, Stage Left 10 / 4 / 6, Stage Right -10 / 4 / 6 m. Review lists it as Camera location |
| Camera position | Optional: "Estimate the position of the camera's lens, measured from the point of origin, in m: X = stage left, Y = upstage, Z = up. An estimate within about 1.0 m is good enough. The calculated camera is checked against this position. This finds a skipped lens step or a wrong rectangle size." The amount is in the active units (3.3 ft in Imperial). Fields Pos X (stage left +), Pos Y (upstage +), Pos Z (height). Partly filled: "Enter all three values, or leave all three empty." The box opens by itself when a value is in it |
| Diagonals note | Under Width / Depth, always shown. Before both are entered: "**Optional:** measure the two diagonals between opposite tape marks. If both are the same, you have a true rectangle. Skip this for a rectangle you know is true, such as the edges of stage decks." After: the middle sentence reads "If both are 13.42 m, the marks are correct and form a true rectangle." (the computed diagonal, in the active units). Four corners always fit, so a wrong mark cannot show after the solve |
| Starting rectangle | Once the size reads: projected from a camera at the entered position, else at the chosen location, else 15 m in front of the rectangle's front edge and 8 m above, looking at the rectangle's centre with the saved FOV. Scaled to at most 50 % of the picture's width and height (a projection under 20 % in both is scaled up to that), kept 5 % inside the frame, bent by the lens pair, outlined dashed until the solve. A corner not yet placed follows every input change; a placed one stays |
| Corners | Dragged or selected and confirmed (see [Selecting a point](#selecting-a-point)). Unplaced corners are drawn dashed |
| Corner list | Below the picture. Each row: DSL / DSR / USR / USL, "Downstage left" etc., a chip (Waiting for size, Not placed, Placed, then the pixel error, or Behind the camera), and the corner's X / Y / Z |
| Corner positions | Centre (cx, cy, z): DSL cx + w/2, cy - d/2; DSR cx - w/2, cy - d/2; USR cx - w/2, cy + d/2; USL cx + w/2, cy + d/2; all at z |
| Solve | Only once all four corners are placed |
| 0/0/0 | The origin is drawn on the picture: through the corners' homography before the solve, through the solved camera after it, and in Review |
| Grid | Simple writes the rectangle as the grid (2 m spacing kept from the stored grid). A later grid edit that moves a corner drops the pins, as today |
| Tip | "Drag each corner onto its tape mark. The loupe shows the picture 4× larger. Click a corner or press Tab to select it. The loupe stays on it, and the arrow keys move it by one pixel. Press Enter when the crosshair is in the right place. Esc or a click somewhere else ends the selection." |

The Simple hint, first match wins:

| When | Above | Heading | Sub-line |
|---|---|---|---|
| No location and no width / depth | the location | Choose where your camera is located. | The location decides on which side of the picture each corner of the rectangle starts. |
| Camera position partly filled, once Next showed the error or the focus left the camera fields (then until the values are complete or empty, so the hint never moves while a value is typed) | the location | Enter all three values of the camera position, or leave all three empty. | An estimate within about 1.0 m is good enough. (3.3 ft in Imperial) |
| Nothing of the rectangle entered | the rectangle text | Enter the size of the rectangle you marked on stage. | Only the corners need a mark, such as a tape cross. The Setup Wizard then draws the rectangle on the picture. |
| Something missing | the rectangle text, or the custom-centre question when only the centre is missing | "Enter the rectangle's width / depth / width and depth.", "Enter the X, Y and Z of the rectangle's centre, measured from the point of origin." (naming exactly the missing ones), "Width and depth must be between 0 and 200 m." | as above |
| A corner not placed | the picture | Drag DSL onto its tape mark. | Downstage left. N of 4 corners are on their marks. Click a corner to select it and move it with the arrow keys. Press Enter when the crosshair is in the right place. |
| Solved | the picture | Check the result. | The grid on the picture should match the tape marks. Then press Next. |
| No solve | the picture | The camera cannot be calculated from these corners. | Check the size of the rectangle, and check that each corner is on its tape mark. |
| No solve at an entered camera height | the picture | The camera cannot be calculated at this camera height. | Check the camera height, the size of the rectangle, and that each corner is on its tape mark. |

Simple status line before the solve: the rectangle problem plus "Then the
rectangle is drawn on the picture.", then "Drag each corner onto its tape
mark. The camera is calculated when all four corners are moved. N left."

### Advanced

Top to bottom: the hint (while no camera position), **Camera Position**, the
picture (the hint above it otherwise), Refresh Image and Reset Pins, "To
calculate the camera, add …" (muted, hidden once it solves), the tip, **Points**,
the status, the caution, the solved values, **Distances to check**. Advanced
never touches the grid.

| Topic | Decision |
|---|---|
| Camera position | Required and first: "**Where is your camera?** Enter the position of its lens, measured from the point of origin, in m: X = stage left, Y = upstage, Z = up. An estimate within about 2.0 m per axis is good enough." (6.6 ft in Imperial) Partly filled: "Enter all three values." |
| Partly filled, both modes | Next shows the error under the fields (an alert), which then describes all three; the ones without a usable value are marked invalid and the first of them takes the focus. Completing or clearing the values removes it; a value typed again shows no error until the next Next |
| Kinds | **Known point**: X / Y / Z typed. **Distance point** (stored as kind `triangulated`): found from its distances to two other points (known points or distance points) and its Z, default 0 |
| Rules | The camera position, at least two known points, at least four pinned points. A known point may stay unpinned (a point outside the picture still counts as one to measure from); a distance point must be pinned (two distances leave two places mirrored across the line between its points). No point measured from itself through others. **Add distance point** and **New distance point** appear once two known points read |
| Picture | Over the picture the loupe follows the pointer. A click opens **Which point is here?** at that spot (a dashed ring marks it; with the menu as a panel, a ring with a crosshair), on the click, not the press: on a phone a menu opened by the press would sit under the finger and take the same tap. A press that moved between press and release opens nothing (3 CSS px for a mouse, 8 for a finger or a pen; the same on every picture and pin): the unpinned points (name, Known point / Distance point), a separator, **New known point** (X/Y/Z), **New distance point** (disabled with "Needs two known points" until then). Choosing a point pins it there |
| The menu | A labelled group of buttons, not an ARIA menu (the arrow keys move the spot, not between items). It ignores the clicks of the press or key that opened it, and the second click of a double-click (the page may have scrolled under it). Placed 12 px right of and below the spot, flipped to stay inside the picture and scrolled into view (nearest) as it opens or changes; its first item takes the focus. **×**, Esc or a press outside the menu and the picture close it. While it is open the loupe stays on the spot (clear of the menu) and the arrow keys move the spot 1 px (Shift 10) unless a field has the focus |
| Menu on a phone | Where the picture is under 480 × 360 CSS px (a phone; a 1024 × 768 tablet and a desktop keep the popup), the same menu is a full-width panel directly under the picture, before Refresh Image: not clipped, no scrolling of its own, scrolled into view (nearest) as it opens or changes. Decided again on a resize or a turn of the phone, which moves an open menu and keeps its focus. In full screen it is a sheet at the bottom of the shell (at most half its height, scrolling inside), and the picture fits above it. Same items, forms, keys, focus and gesture rule as the popup; a click outside the panel and the picture closes it once the clicked control has acted, a press or the focus it gives does not (a swipe that scrolls the page down to it starts with one). A pin's menu shows its pin selected |
| New known point | The menu becomes a form: **Point** (prefilled P<n>), X / Y / Z (Z prefilled 0), **Back**, **Place point** (disabled until X / Y / Z read) |
| New distance point | **Point**, two pairs of **Measured from** + **Distance**, **Z** (empty means 0); **Place point** once both Measured from choices differ and both distances are above 0. Each Measured from list leaves out the point chosen in the other |
| While a form is open | A click on the picture moves the spot |
| A pin | Dragging moves it. A click on it opens its menu (on the click, as on the picture), titled "P1 · Known point": **Remove pin** ("Keeps the point") and **Delete point** (error colour) |
| Point list | Below the picture. Each row: name, kind chip, status chip, for a distance point its X / Y / Z, **Remove pin** (when pinned), **Delete**. Fields: **Point** (never labelled "Name": browsers and password managers fill in the operator's own name; `autocomplete="off"` and the ignore attributes), then X / Y / Z, or Z and Measured from / Distance / Measured from / Distance. A reason line under a row with a problem. "No points yet." while empty. **Add known point** (for a point outside the picture) and **Add distance point** under the list |
| Status chips | Not placed (a problem), Not pinned, Pinned, then "0.4 px" or "0.4 px · 3 cm" (warning when over the caution limits), or "Behind the camera" (warning) for a pin the solved camera puts behind itself, its pin drawn flagged like one far off |
| Distance point X / Y / Z | Shown before any solve, by trilateration, once its two points have positions and it and both of them are pinned. Of the two mirrored places, the pin's side of the line between its two points' pins picks one (the image's y runs down, so seen from above the floor the sides swap). The solve then refines it. Dashes until then |
| Deleting | Removes the point from every Measured from that named it |
| Tip | "Click the picture where a point is, and choose the point from the menu. Or create a New known point or a New distance point there. Click a pin to remove the pin or delete the point. While a menu is open, the loupe stays on the spot, and the arrow keys move the spot by one pixel. Drag a pin to correct it." |
| Missing for the solve | Named as "the camera position", "N more known point(s)", "N more pinned point(s)", joined with "and" |

The Advanced hint, first match wins:

| When | Above | Heading | Sub-line |
|---|---|---|---|
| No camera position | Camera Position | Enter the rough camera position first. | An estimate within about 2.0 m per axis is good enough. (6.6 ft in Imperial) |
| Camera position partly filled, once Next showed the error or the focus left the camera fields (as in Simple) | Camera Position | Enter all three values of the camera position. | as above |
| Fewer than two known points | the picture | Enter two known points that are far apart, such as the DSL and DSR corners. | Click where one of them is on the picture, choose New known point and type its X/Y/Z. N of 2 entered. |
| Fewer than four points | the picture | Enter one more / two more known points or distance points. | Click where it is on the picture and choose New known point or New distance point. |
| A point to pin (a distance point always, a known point until four are pinned) | the picture | Pin P3 on the picture. | Click where it is on the picture, then choose it from the menu. N of at least 4 pinned. |
| Solved | the picture | Check the result. | Check the grid on the picture and the Distances to check. Then press Next. More known points or distance points give a better result. |
| No solve | the picture | The camera cannot be calculated from these points. | Check the pins and the measurements. |
| No solve at an entered camera height | the picture | The camera cannot be calculated at this camera height. | Check the camera height, the pins and the measurements. |

A point's reason line, as the first problem found:

| Problem | Text |
|---|---|
| Known, a value missing | Enter its X, Y and Z. |
| No Measured from chosen | Choose the two points its distances are measured from. |
| Both Measured from the same | Choose two different points. |
| A distance missing or out of range | Enter both distances, between 0 and 200 m. |
| Z unreadable | Enter its Z. |
| Not pinned | Pin it on the picture. Its two distances allow two places, mirrored across the line between its two points. |
| A loop | It is measured from itself, through other points. |
| A Measured from point deleted | One of its points was deleted. |
| A Measured from point unplaced | It is measured from P2, which cannot be placed. / It is measured from a point that cannot be placed. |
| Height | A distance is shorter than the height difference between the points. |
| Same place | Its two points are at the same place. |
| Distances cannot meet | The difference between the two distances is larger than the distance between its two points. / The two distances together are shorter than the distance between its two points. |

**Distances to check:** "Measure these distances between the marks on the
stage. If a distance is different, a typed or measured value is wrong. The
distances from a distance point to the two points it is measured from are not listed." A table (Points, Distance) of every pair of
placed points except those, computed from the typed and solved positions in
the active units. A click, Enter or Space on a row draws that pair on the picture as
a labelled line through the solved camera; the row keeps the focus and is
marked as the current one (`aria-current`). Display only, no field for the
measured value.

### Switching modes

| From | Decision |
|---|---|
| Simple → Advanced | All four corners become known points named DSL, DSR, USR, USL with their pins (when placed). Each axis the rectangle gives is filled (X needs the width and the centre X, Y the depth and the centre Y, Z the centre Z); one it does not give stays empty, so the point has a problem ("DSL needs its Y.") until it is entered. The camera position carries over |
| Advanced → Simple | With points: the shared modal "Switch to Simple?", "Simple uses the four corners of a rectangle. Switching clears all N points and their pins.", **Cancel** / **Clear points and switch** (only the confirm is red) |

## Solve

| Topic | Decision |
|---|---|
| One fit | Both modes go through one Levenberg-Marquardt fit: the 7 camera values plus each distance point's X / Y, against the pins (`PIXEL_SIGMA` 1 px) and the distance points' distances (`DISTANCE_SIGMA` 0.04 m). Pins are undistorted with the lens pair first. Every pin counts the same, and no point is left out automatically: one that disagrees is named in the caution |
| Needs | Four pinned points: 7 camera unknowns, 2 equations per pin; a distance point's two distances fix its own X / Y |
| Seeds | With a camera position: that position looking at 0/0/0, roll 0, at the saved FOV, 40° and 90°. With four pinned known points at one height (within 1 cm, no three in a line): the homography decomposition. Simple has the second unless its decomposition gives none; Advanced always has the first. Without a camera position, when that decomposition gives none or a camera steeper than -82.5°, the camera is also seeded above the pinned known points' centre (at the entered camera height, else as high as the pins' spread says at the saved FOV), looking down at -89° at four bearings. A hint seed steeper than -60° is also tried turned by a quarter, a half and three quarters |
| Distance points | Start at their trilateration, chained after the points they are measured from, the mirror place picked by the pin as seen from the seed. Two distances that miss by up to `3 √2 · DISTANCE_SIGMA` meet halfway; more leaves the point unplaced with its reason |
| Mirror sides | Every seed is fitted twice, with the pin-picked places and with all of them flipped. An upright camera (at or above the lowest point, pitch ≤ 0) beats one that is not, then the lowest cost wins |
| Answer | Camera, per point: status, reason, pixel error, distance error, position; largest error; the caution reasons |
| Unsolvable | No finite fit or a FOV outside 10-170°: "No camera fits these points. Check the pins and the measurements." (Simple: "No camera fits these corners. Check the corners and the measurements."). With a camera height at or below the lowest pinned point, or one no camera fits: "No camera fits at this camera height. Check the camera height and the measurements." |

### Camera height (both modes)

From straight above, a flat set of points cannot separate the camera's height
from its field of view: a camera steeper than about -85° can come out metres
off with nothing in the errors. So whenever the calculated camera is steeper
than -82.5°, its own blue instruction box appears under the warning, "Enter
the exact height of the camera's lens" / "The camera looks almost straight down
– its height and its field of view cannot be told apart.",
with a **Camera height** field (in the active units, "(Z from the point of
origin)") under it. It is not a warning reason. An entered height holds the fit (a residual on the camera's Z with
`HEIGHT_SIGMA` 0.01 m, and every seed starts at that height); the blue box then gives way to
the note "The camera height is fixed at 9.80 m." The field stays
while a value is in it or it has the focus, and once asked until a camera calculated
without a height is no longer that steep. A typed height that cannot be read shows the field
error "Enter the height of the camera's lens." under it, one farther than
10 km "Enter a height within 10000.0 m of the point of origin." (32808.4 ft in
Imperial); either counts as no height. A missing height never blocks Save and finish. The
height is stored with the calibration (`camera.calibration_height`,
m, empty when not entered), so a reopened Setup Wizard calculates the same
camera, and Review lists "Camera height: Fixed at 9.80 m" or "Not entered"
when the camera is that steep.

### Something might be wrong

One caution (`.notice.warning`) in Camera Calibration and Review, headed
"Something might be wrong", listing every reason that applies and ending
"You can still save the camera. Check the measurements, then place the
corners again." (Simple) / "… then pin the points again." (Advanced). It blocks
**Save and finish** only while a pin is more than 20 px off or behind the camera (see Point far off).

| Reason | Fires when | Text |
|---|---|---|
| Lens not measured | Simple, `lens_distortion` on, the lens has no values from this run (not fitted, loaded or typed under User input) and the station had no pair | The lens was not measured. Four corners fit with any lens, so a missing lens measurement does not show in their errors. Measure the lens in the Lens step, unless straight lines in the picture already look straight. |
| Camera far from the position (Simple) | A position was entered and the camera is further from it than max(1 m, 10 % of its distance to the points' centre) | The calculated camera is 2.5 m above the camera position you entered (… in total, limit 1.6 m). Each axis carrying at least a quarter of the gap, in stage words, in the active units. With a Custom origin it adds: Was the camera measured from the same point of origin as the rectangle's centre? |
| Camera far from the position (Advanced) | Any axis off by more than max(2.5 m, 10 % of that distance), headroom over the promised 2 m | As above, "limit 2.5 m per axis" |
| Field of view | Outside 20-120° | The calculated field of view, N°, is outside the usual range for cameras. |
| Floor | Camera below the lowest point | The calculated camera is below the floor. |
| Up | Pitch above 0 | The calculated camera points upwards. |
| Point disagrees | A pin's error over 5 px | P2 disagrees with the other points by N px. |
| Point far off | A pin's error over 20 px (`APPLY_MAX_PX`), or a pinned point the camera puts behind itself: **Save and finish** is disabled, as 0.4.3 refused such a solve; the closing line reads "Save and finish stays off while a point is more than 20 px off or behind the camera. Check the measurements, then pin the points again." | P2 disagrees with the other points by N px. / P2 lies behind the calculated camera. |
| Floor reach | Four or more pinned known points at one height and another point: across the picture, within reach of the points, the fit puts the floor more than twice as far or as near (`FLOOR_REACH` 2) as those points' homography alone. Skipped when the fit or that homography camera is steeper than -82.5°, where the reference is noise and the camera height prompt takes over | The calculated camera puts the far floor more than twice as far, or as near, as the floor points alone do. |
| Distances | A distance point's distance error over 3 σ (12 cm) | P4's distances disagree with the picture by N cm (in in Imperial). |

## Review

| Part | Decision |
|---|---|
| Picture | The grid through the solved camera, each placed point as a ring with its name, 0/0/0; the full-screen button. A name that would run under the button (outside full screen) or off the picture's right edge is drawn left of its point, on the Camera Calibration picture too |
| No solve | A red `.notice.error`: "The camera is not calculated yet." with "To calculate the camera, add … Go back to Camera Calibration to finish it.", or the solve's error. **Save and finish** is disabled |
| Incomplete points | A red `.notice.error`: "Some points are not complete." with a list naming each point with a problem and what it needs ("P5 needs a pin.", "P6 needs its X.", or the point's reason line, "P5: It is measured from itself, through other points."), ending "Complete or delete them in Camera Calibration." **Save and finish** is disabled while any point has a problem; Simple's rectangle problem leaves no solve, so it is not listed |
| No feed | "No video feed available, so the visual review is unavailable. The camera is calculated from a snapshot: configure a video source in the Video Source step, then return here." |
| Caution | As above |
| Camera | Pos X / Y / Z, Pitch, Yaw, Roll, FOV |
| Lens | Barrel / fisheye, Corner correction, Lens rating: the meter and its rating, "User input", "Loaded: name" (", changed by user input" after a change), "The station's lens" or "Not measured" |
| Station's lens | Simple, the station's pair kept, nothing measured in this run, with a solved camera (as the caution): an info `.notice` in Camera Calibration and Review, "Using the station's lens. It was not measured in this run." It is not a caution reason |
| Calibration | Mode, Points, Pinned (both count what Save and finish writes: every point), Largest error (px), Your camera position (or "Not entered"), and in Simple the Camera location (or "Not chosen") |
| Distances to check | Advanced only, the same table, not clickable |
| Grid | Width, Depth, Spacing, X / Y / Z Offset, and Grid: "From the rectangle" (Simple) or "Kept" (Advanced) |
| Buttons | **Back**, **Leave without saving** (the modal "Leave without saving?", "None of your changes in the Setup Wizard are saved. The station keeps its current camera, lens and grid.", **Cancel** / **Leave without saving**, only the confirm red), **Save and finish** |
| Save and finish | Disabled while a solve is still running, a point is far off or behind the camera, or a point has a problem. Writes the camera, the lens pair, the mode and every mark with its pin, and in Simple the grid before the camera; each response's `.ok` is checked. Help: "Save and finish saves the camera, the lens and the points. In Simple mode it also saves the rectangle as the grid. The green grid is the one the Operator Screen will draw." |

## Help drawer

One `openfollow/web/help/` file per text, matching the mockup:

- **Video Source:** the snapshot of this input is used in every later step.
- **Lens:** what the step measures; Lines (straight in reality, edges and
  corners tell the most, selecting points and lines, the keys); Rating (Not
  usable is not applied, Corner correction is the extra correction near the
  corners and needs a line into a corner); Load and
  save; Full screen.
- **Camera Calibration (Simple):** the rectangle and the grid; point of
  origin and Custom; the starting rectangle and the location; the hint; the
  diagonals; dragging and selecting corners; the optional camera position and
  its check; Units; Refresh Image; Something might be wrong (and why a
  missing lens hides behind four corners); Full screen; Switching to Advanced.
- **Camera Calibration (Advanced):** the camera estimate and its per-axis
  check; known points and distance points (two known points far apart solve
  about as well as four, close together two to three times worse); placing
  points on the picture; Calculation; Units; Refresh Image; Distances to check
  (they check the shape, not where it sits); Something might be wrong; Full
  screen; Switching to Simple.
- **Review:** what Save and finish saves.

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
| Mode and marks | `camera.calibration_mode` (`"simple"` / `"advanced"`) and `camera.calibration_marks`: `id`, `name`, `kind` (`known` / `triangulated`), `x`, `y`, `z`, `from_a`, `from_b` (ids), `distance_a`, `distance_b`, `pin` (`[x, y]` as fractions of the snapshot, `[]` = not pinned), `position` (solved, triangulated only). At most 12 marks |
| Saved lens calibrations | Not in `config.toml`: one small JSON file per calibration in a `lens-calibrations` directory beside it (as the web backups sit in `backups/web`), so they stay on the station and are never exported, imported or pushed. Each file holds `name` (1-60 characters, unique ignoring case), `k1`, `k2`, `cx`, `cy` (the lens rule), `saved` (date), `width` / `height` (the snapshot it was measured on) and `openfollow_version` (the `__version__` that wrote it), so a later version can tell old calibrations apart and warn about them. Saving a name that exists rewrites its file. No limit on the number of files. The list is filled only by **Save lens calibration**: a new station has none, and nothing ships or imports any A file that does not parse or fails the checks is left out of the list with one WARNING, never deleted. The on-station settings backup (`privilege/settings_backup.py`) holds the directory beside `config.toml` |
| Calibration points feature | Retired: `calibration_points` leaves `FEATURE_KEYS` and the experimental set; Advanced is always available. A stored `shown_features` entry is dropped as unknown |
| Migration | Old `camera.calibration_points` with entries ⇒ Advanced: the four grid corners become known points with `camera.corner_pins` as their pins, each old point a distance point measured from its two corners, `height` as its Z. Otherwise ⇒ Simple with `corner_pins` as the corner pins. The old keys are read once on load and never written |

## Simulation findings

From the mockup's solver against the Concert picture (camera 0 / 13 / 12 m,
pitch -48°, 70°, lens -0.2 / 0.03, 12 x 6 m rectangle). These set the
thresholds above; the Python tests pin them again.

| Case | Result |
|---|---|
| Simple, exact pins, true lens | Exact camera, with a good, 5 m-off, wrong-side or no camera position |
| Simple, lens fitted from the suggestions (-0.197 / 0.026) | Camera within 1 cm |
| Simple, lens ignored | Camera 2.4 m off, pin error 0 px: only the position check and the lens caution catch it |
| Advanced example (Reference Point, DSL, DSR known; riser corners and USR as distance points) | Camera within 2 cm, distance points within 6 mm; pre-solve X / Y / Z match the solve |
| Advanced, DSL and DSR (12 m apart) plus two distance points, tape right within 5 cm | Worst point median 7 cm, p95 12 cm; four known points 6 / 10 cm |
| Advanced, Reference Point and DSL (6 m apart) | 16 / 35 cm; a 6 m-off estimate flips both distance points to the mirror side until both sides are tried (the camera below the floor loses) |
| Advanced, one distance of a distance point 46 cm long | Two distance points flagged (27 cm, 15 cm) |
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
      enum, dangling / self / looping From, distance point unpinned, fewer than
      two known, more than the cap, TOML round trip, migration both ways
      (with / without points, with / without pins).
- [ ] Tests for the lens store (`tests/test_lens_calibrations.py`): save and
      list round trip with the version recorded, replace ignoring case, names
      empty / too long / path-like (`../x`, `a/b`), a lens that cannot follow the picture out to its corners, a broken
      or hand-edited file left out with one WARNING, many files, export and
      push never carrying them.

### 2. Solver (`scene/calibration_points.py`)
- [ ] Generalise `solve_with_points` to marks: known points fixed,
      distance points free in X / Y at their Z, chained trilateration
      with `_pick_by_pin`, both mirror sides per seed, upright before cost.
- [ ] Seeds: the camera position at three FOVs and the coplanar homography;
      Simple goes through the same entry point.
- [ ] Pre-solve placement of distance points from their pins (the
      image-side rule), for the rows.
- [ ] Per-mark answer: status, reason, pixel error, distance error, position.
- [ ] `plausibility(solve, hint, mode, lens_measured, origin_custom)`
      returning the reasons with the axis wording; thresholds as above.
- [ ] Tests (`tests/test_calibration_points.py`, `tests/test_solver.py`):
      the Concert picture's camera and a front-of-house camera through known
      poses with absolute pose asserts, the mirror case, an off-picture known
      point, chained distance points, the pre-solve side rule, the
      lens-ignored case, a mistyped point flagged, each caution reason firing
      and not firing, the axis wording; mutation-check every regression test.

### 3. Routes (`web/routes.py`)
- [ ] `/api/wizard/solve` takes `marks`, `mode`, `hint`; strict number
      reading, 400 on malformed, 409 when superseded, 422 when unsolvable.
- [ ] Saving a lens calibration (`name`, `k1`, `k2`): 400 on a malformed
      body, an empty or too long name or a lens that cannot follow the picture out to its corners; replaces a name
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
      menus and forms, the point list, pre-solve distance point positions, the
      hints and the "To calculate the camera, add …" line, Distances to check.
- [ ] Unit switch converting every entered length; the status line, the
      caution, the solved values with Lens.
- [ ] Mode switch rules and the Advanced → Simple confirmation.
- [ ] Review and Save and finish as decided.
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
