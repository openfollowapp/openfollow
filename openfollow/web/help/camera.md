# Camera

Where the real camera sits in the venue and which lens it uses. OpenFollow uses these to project tracked points into stage coordinates, so they must match the physical rig.

The easiest way to set them is the **Setup Wizard** (the Open Setup Wizard button on the General or Camera & Grid tab), which solves position, orientation, and field of view from marks on the stage pinned on the picture: the four corners of a measured rectangle (**Simple**), or known and triangulated points (**Advanced**). The fields here are for direct edits and fine-tuning.

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

If the overlay doesn't sit on the video on a camera whose yaw isn't `0`, re-run the **Setup Wizard** – it resolves the orientation from the pinned marks rather than from the stored angles.

## Lens

OpenFollow only needs the **horizontal** field of view; the sensor and focal-length fields are an optional way to work it out.

- **Horizontal Field of View** – the angular width the camera sees, in degrees. Pull this from the camera datasheet.
- **Sensor Size** + **Focal Length** – pick your sensor format (or *Custom…* with a width in mm) and enter the focal length; OpenFollow computes the field of view from the geometry. If you then edit the field of view by hand, these dim to show they're no longer authoritative – your manual value wins.

## Lens distortion

Shown with the **Lens distortion** feature on (General tab, Features); off, everything works as if the lens were straight and these values are kept.

Wide-angle and fisheye lenses bow straight lines, so the pinhole overlay (grid, markers, zones) no longer sits on top of the curved video. Two values bow the **overlay** to match the lens. The video frame itself is never warped (warping every pixel would be too slow on a Pi), so there is no performance cost when both are `0`.

- **Barrel / fisheye** – the main correction. Negative hugs a wide-angle / fisheye image (lines curve inward); positive corrects a pincushion lens (lines curve outward).
- **Edge fit** – a finer adjustment for the frame edges, where strong fisheye lenses bend the most.

**Measure them instead of guessing.** The **Setup Wizard** has a **Lens** step between Video Source and Camera Calibration. It suggests the clearest edges of the picture as dashed lines, each with a bubble: the check mark uses a line that is straight in reality – a stage edge, a tape line, a truss, the edge of an LED wall – and the cross rejects one that is not, such as a curtain or a cable. You can also click the start and the end of such an edge yourself. Each line follows its edge along the whole length, bowed the way the lens bows it, and its five points can be dragged; its ends stay within a few pixels of where you clicked. From the keyboard, Tab reaches the bubbles and the points: the arrow keys move a point by one pixel, Tab moves on to the next point of the same line, Space switches a middle point off and on, and Delete removes its line. The Setup Wizard calculates both values so the lines come out straight. Nothing is printed or carried, and the lens is measured as rigged, at show zoom and focus. A five-step rating (Excellent, Good, Fair, Poor, Not usable) says how exactly the lines measure the lens: lines near the edges and corners of the picture help the most, and the correction near the corners is only measured once a line reaches a corner. **Save and finish** is never blocked by the rating; some venues simply have no straight edges in the corners. **Not usable** means the lines say too little about the lens (lines through the middle of the picture, for one, or a strong wide-angle lens drawn only along the top and bottom edges, which a weaker lens fits about as well), so its values are shown but not applied until more lines raise it. The sliders under the result, **Adjust by hand**, change the pair, and Review repeats the rating while the pair is the calculated one, or says it was adjusted by hand. Skipping the step keeps the current pair.

The correction is centred on the middle of the image. Mouse placement and AI tracking are corrected to match, so clicking a point in the video still lands the marker there. `0` / `0` disables the correction (plain pinhole).

The one limit on the pair is that the overlay must not fold anywhere out to the frame corner. The form checks the pair whenever either value or its slider changes and keeps **Save** disabled while it folds; a pair from a hand-edited file, an earlier release or a request that skips the form is pulled back along its own direction to just short of the fold, so it keeps most of its correction, and the log says which pair it became. There is no fixed range: a lens with about 100° horizontal field of view on 16:9 lands around `-0.47` barrel / fisheye and `0.25` edge fit, and the sliders cover the common span while the number fields accept any pair that does not fold. The Setup Wizard's camera solve undistorts the pins with the current pair, so a lens changed after pinning is solved again from the same pins.

## Calibration

The calibration the **Setup Wizard** applied last is listed read-only under **Calibration (Simple)** or **Calibration (Advanced)**, and the next run of the Setup Wizard starts from it. One row per point:

- **Point** – its name. In Simple the points are the rectangle's corners, DSL, DSR, USR and USL.
- **Kind** – **Known** for a typed position, **Triangulated** for a point measured by its distances to two other points plus its height.
- **Position** – X, Y and Z. A triangulated point's is where the solve of the saved pose put it, or `–` when none did.
- **Measured from** – a triangulated point's two points and the distance to each.
- **Pinned** – whether it is placed on the picture.

In Simple the rectangle is the grid: a change to the grid's width, depth or offsets, on the Grid tab, from another station or by an import, moves the corners with it and unpins them, since they were pinned on the old grid. Advanced points stay as they are.

## Saving & sharing

- **Save** – make the current values durable. Camera and Grid apply live as you type but revert on reload unless you Save.
- **Apply to all stations** – broadcast Camera and Grid to every OpenFollow station on the network. Use it when several operators share one physical camera.
- **Save as template… / Load template…** – store Camera and Grid together as a portable file and recall a full venue setup later. The Load chooser also lets you **Export** a template as a single `.oftemplate` file and **Import** one from another machine.
