# Camera

Where the real camera sits in the venue and which lens it uses. OpenFollow uses these to project tracked points into stage coordinates, so they must match the physical rig.

The easiest way to set them is the **Setup Wizard** (the Open Setup Wizard button on the General or Camera & Grid tab), which solves position, orientation, and field of view from four marked grid corners. The fields here are for direct edits and fine-tuning.

Positions are in **metres**, relative to the **Reference Point** – the single physical point on stage that is the (0, 0, 0) of your show (see Core Concepts). Orientation is in **degrees**.

## Position (X, Y, Z)

- **Position X** – stage left positive, stage right negative. `0` is on the centre line.
- **Position Y** – upstage positive, downstage (towards the audience) negative. A camera out in the house has a negative Y.
- **Position Z** – height of the lens above the stage floor.

## Orientation (Pitch, Yaw, Roll)

Think of the camera on a pan-and-tilt head: yaw swings it round to face the stage, then pitch tilts it down into the stage, then roll levels the picture.

- **Pitch** – tilt up or down, measured from the horizon. Negative looks down; a front-of-house camera is typically around −20°. Pitch means the same thing whichever way the camera faces, so a rig behind or beside the stage still tilts down with a negative pitch.
- **Yaw** – which way the camera faces, as a compass bearing on the stage floor. `0` looks straight upstage, `180` is a camera hung upstage looking back towards the audience, and `−90` / `90` are box-boom positions looking across the stage.
- **Roll** – rotation around the lens axis. Leave at `0` unless the camera is physically canted.

If the overlay doesn't sit on the video on a camera whose yaw isn't `0`, re-run the **Setup Wizard** – it resolves the orientation from the grid corners rather than from the stored angles.

## Lens

OpenFollow only needs the **horizontal** field of view; the sensor and focal-length fields are an optional way to work it out.

- **Horizontal Field of View** – the angular width the camera sees, in degrees. Pull this from the camera datasheet.
- **Sensor Size** + **Focal Length** – pick your sensor format (or *Custom…* with a width in mm) and enter the focal length; OpenFollow computes the field of view from the geometry. If you then edit the field of view by hand, these dim to show they're no longer authoritative – your manual value wins.

## Lens distortion

Wide-angle and fisheye lenses bow straight lines, so the pinhole overlay (grid, markers, zones) no longer sits on top of the curved video. Two values bow the **overlay** to match the lens. The video frame itself is never warped (warping every pixel would be too slow on a Pi), so there is no performance cost when both are `0`.

- **Barrel / fisheye** – the main correction. Negative hugs a wide-angle / fisheye image (lines curve inward); positive corrects a pincushion lens (lines curve outward).
- **Edge fit** – a finer adjustment for the frame edges, where strong fisheye lenses bend the most.

**Measure them instead of guessing.** The **Setup Wizard** has a **Lens** step between Video Source and Camera Position. It suggests the clearest edges of the picture as dashed lines; tap the ones that are straight in reality – a stage edge, a tape line, a truss, the edge of an LED wall – or click the start and the end of such an edge yourself. Each line follows its edge along the whole length, bowed the way the lens bows it, and its five points can be dragged; its ends stay within a few pixels of where you clicked. From the keyboard, Tab reaches the suggestions (Enter adds one) and the points: the arrow keys move a point (Shift for ten pixels), Space switches a middle point off and on, and Delete removes its line. The wizard fits both values so the lines come out straight. Nothing is printed or carried, and the lens is measured as rigged, at show zoom and focus. A five-step coverage rating says how well the lines pin the lens down: lines near the edges and corners of the image tell the most, and the edge fit is only measured once a line reaches a corner. Apply is never blocked by the rating; some venues simply have no straight edges in the corners. A **Low** rating means the lines say too little about the lens (lines through the middle of the picture, for one, or a strong wide-angle lens traced only along the top and bottom edges, which a weaker lens fits about as well), so its values are shown but not applied until more lines raise it. The sliders under the result fine-tune the pair, and Review repeats the rating while the pair is the fit's own, or says it was fine-tuned. Skipping the step keeps the current pair.

The correction is centred on the middle of the image. Mouse placement and AI tracking are corrected to match, so clicking a point in the video still lands the marker there. `0` / `0` disables the correction (plain pinhole).

The one limit on the pair is that the overlay must not fold anywhere out to the frame corner. The form checks the pair whenever either value or its slider changes and keeps **Save** disabled while it folds; a pair from a hand-edited file, an earlier release or a request that skips the form is pulled back along its own direction to just short of the fold, so it keeps most of its correction, and the log says which pair it became. There is no fixed range: a lens with about 100° horizontal field of view on 16:9 lands around `-0.47` barrel / fisheye and `0.25` edge fit, and the sliders cover the common span while the number fields accept any pair that does not fold. The Corner Pinning solve undistorts the pinned corners with the current pair, so a lens changed after pinning is solved again from the same pins, and Review says so when there are no pins to solve from.

## Calibration points (experimental)

The four grid corners pin the camera well on and near the marked rectangle. When the camera sees only a narrow strip of the stage from far away (a back-truss camera looking across a deep auditorium), the corners leave the far field loose, and a position 30 m out can land metres off. A **calibration point** is a spot in the far field whose distances from two grid corners were measured with a laser distance meter: two distances and a dot on the picture tie the camera down where the corners cannot.

With experimental features on, the **Setup Wizard**'s Corner Pinning step has an **Advanced** section for them, up to six. Each point has a dot placed on the spot in the picture (a new point's dot starts in the middle and counts once it is moved), the two corners it was measured from, the two distances (from each corner mark on the stage floor to the spot) and the spot's **height above stage**: how far it sits above the corner marks' level, `0` for a mark on the same floor. Corners far apart give the clearest answer. The solve weighs every pin and distance together, so the corners are refined too: it takes a corner pin to be good to about 3 px of the snapshot, a point's pin to about 1 px and a laser distance to about 4 cm.

Each point carries a chip:

- **Incomplete** – a corner, a distance or the height is missing, or a new point's dot has not been moved yet. It is not solved with, and Apply does not save it.
- **Not solved** – no solve has used it yet in this run of the Setup Wizard. The next change to a corner or a point solves with it.
- **Used** – it agrees with the corners and the other points within the measuring error.
- **Unverified** – it is the only spot, so nothing can tell a wrong distance from a right one. A second spot measured independently cross-checks it. Points pinned within a few pixels of each other, or placed within a few centimetres, are one spot measured twice: they count once here and below, and are left out together.
- **Left out** – it is not used, with the reason under it, and a red ring on the picture where its distances put it. With three or more points, the one that disagrees with the rest is left out; with two that disagree, both are, because two cannot tell which one is wrong. A single point that fits only by moving the camera off the corners is left out too, and so is a fit that moves the floor across the picture to more than twice, or less than half, as far as the corners alone put it, or that tilts the camera past straight up or down, or narrows or widens its lens past what a corner solve gives (89° of tilt, a field of view of 10° to 170°). A point whose distances cannot meet (they add up to less than the corners are apart, say) is left out before the solve.

The points are saved with the camera when the Setup Wizard applies, each with the position the solve put it at (none for a point left out, or when the pose came from somewhere else, a camera value typed into the Setup Wizard for instance), and listed read-only on this tab with that position, so a later run of the Setup Wizard starts from them. The position belongs to the same solve as the camera pose: like the pose, it does not follow a later change to the grid, and the next run of the Setup Wizard brings both up to date. A pose changed on this tab keeps the positions of the last solve. The Setup Wizard also saves the corner pins its last solve with the points took, so the next run starts Corner Pinning from them rather than from where the saved pose puts the corners; **Reset Corners** goes back to that projection. A change to the grid's width, depth or offsets, here, from another station or by an import, forgets those pins, since they were placed on the old grid.

## Saving & sharing

- **Save** – make the current values durable. Camera and Grid apply live as you type but revert on reload unless you Save.
- **Apply to all stations** – broadcast Camera and Grid to every OpenFollow station on the network. Use it when several operators share one physical camera.
- **Save as template… / Load template…** – store Camera and Grid together as a portable file and recall a full venue setup later. The Load chooser also lets you **Export** a template as a single `.oftemplate` file and **Import** one from another machine.
