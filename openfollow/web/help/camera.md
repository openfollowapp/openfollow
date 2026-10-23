# Camera

Where the real camera sits in the venue and which lens it uses. OpenFollow uses these to project tracked points into stage coordinates, so they must match the physical rig.

The easiest way to set them is the **Setup Wizard** (the Open Setup Wizard button on the General or Camera & Grid tab), which calculates position, orientation, and field of view from marks on the stage pinned on the picture: the four corners of a measured rectangle (**Simple**), or known points and distance points (**Advanced**). The fields here are for entering or adjusting the values by hand.

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

If the overlay doesn't sit on the video on a camera whose yaw isn't `0`, re-run the **Setup Wizard** – it calculates the orientation from the pinned marks rather than from the stored angles.

## Lens

OpenFollow only needs the **horizontal** field of view; the sensor and focal-length fields are an optional way to work it out.

- **Horizontal Field of View** – the angular width the camera sees, in degrees. Pull this from the camera datasheet.
- **Sensor Size** + **Focal Length** – pick your sensor format (or *Custom…* with a width in mm) and enter the focal length; OpenFollow computes the field of view from the geometry. If you then edit the field of view by hand, these dim to show they're no longer authoritative – your manual value wins.

## Lens distortion

Shown with the **Lens distortion** feature on (General tab, Features); off, everything works as if the lens were straight and these values are kept.

Wide-angle and fisheye lenses bow straight lines, so the pinhole overlay (grid, markers, zones) no longer sits on top of the curved video. Two values and the lens centre bow the **overlay** to match the lens. The video frame itself is never warped (warping every pixel would be too slow on a Pi), so there is no performance cost when all of them are `0`.

- **Barrel / fisheye** – the main correction. Negative hugs a wide-angle / fisheye image (lines curve inward); positive corrects a pincushion lens (lines curve outward).
- **Corner correction** – the extra correction near the corners of the picture, where strong fisheye lenses bend the most.

**Measure them instead of guessing.** The **Setup Wizard** has a **Lens** step between Video Source and Camera Calibration. It suggests the clearest edges of the picture as dashed lines, each with a bubble: the check mark uses a line that is straight in reality – a stage edge, a tape line, a truss, the edge of an LED wall – and the cross rejects one that is not, such as a curtain or a cable. You can also click the start and the end of such an edge yourself. Each line follows its edge along the whole length, bowed the way the lens bows it, and its five points can be dragged; its ends stay within a few pixels of where you clicked. From the keyboard, Tab reaches the bubbles and the points: the arrow keys move a point by one pixel, Tab moves on to the next point of the same line, and Delete removes its line. The Setup Wizard calculates the values so the lines come out straight. Nothing is printed or carried, and the lens is measured as rigged, at show zoom and focus. A five-step rating (Excellent, Good, Fair, Poor, Not usable) says how exactly the lines measure the lens: lines near the edges and corners of the picture help the most, and the correction near the corners is only measured once a line reaches a corner. **Save and finish** is never blocked by the rating; some venues simply have no straight edges in the corners. **Not usable** means the lines say too little about the lens (lines through the middle of the picture, for one, or a strong wide-angle lens drawn only along the top and bottom edges, which a weaker lens fits about as well), so its values are shown but not applied until more lines raise it. The sliders and the lens centre fields under the result, **User input**, change the values, and Review repeats the rating while the lens is the calculated one, or shows **User input** once values are typed in. Skipping the step keeps the current lens.

The correction bends the overlay around the **lens centre**. Mouse placement and AI tracking are corrected to match, so clicking a point in the video still lands the marker there. `0` / `0` with the lens centre at `0` / `0` disables the correction (plain pinhole).

**Lens centre** is the point the lens bends the picture around. On many cameras it sits a little off the centre of the picture. **Right of the picture centre** and **Above the picture centre** give it in pixels of the live video, measured from the centre of the picture: a negative value is to the left of it or below it. Nothing measures it: type it here or under **User input** in the Setup Wizard's Lens step, or load a lens calibration that has one there. The Lens step measures Barrel / fisheye and Corner correction around it. The station keeps it relative to the picture's diagonal, so a calibration from another resolution of the same picture shape still fits. Without video the two fields are off and show **No video**, and **Save** keeps the saved lens centre.

The one limit on the lens is that the overlay must follow the picture out to every corner: the pair and the lens centre together must not run out of the lens before the corner farthest from the lens centre. The form checks the lens whenever a value, a slider or a lens centre field changes and keeps **Save** disabled while it cannot reach the corners: the error names the pair while the pair alone falls short, else the lens centre. A lens from a hand-edited file or a request that skips the form is scaled back toward `0` / `0` to just short of the limit, keeping its lens centre, so it keeps most of its correction, and the log says which values it became. There is no fixed range: a lens with about 100° horizontal field of view on 16:9 lands around `-0.33` barrel / fisheye and `-0.03` corner correction, one with about 126° around `-0.53` and `-0.07`. The sliders cover `-1.0` to `0.5` and `-0.8` to `0.3`, and the number fields accept any pair that reaches the corners. Each axis of the lens centre is bounded on its own, at a quarter of the picture's half-diagonal: at 1920 × 1080 up to 275 px right or left, and up to 275 px up or down. The Setup Wizard corrects the pins for the current lens when it calculates the camera, so after a lens change the camera is calculated again from the same pins.

## Calibration

The calibration the **Setup Wizard** applied last is listed read-only under **Calibration (Simple)** or **Calibration (Advanced)**, and the next run of the Setup Wizard starts from it. One row per point:

- **Point** – its name. In Simple the points are the rectangle's corners, DSL, DSR, USR and USL.
- **Kind** – **Known point** for a typed position, **Distance point** for a point measured by its distances to two other points plus its height.
- **Position** – X, Y and Z. A distance point's is where the calculation of the saved pose put it, or `–` when none did.
- **Measured from** – a distance point's two points and the distance to each.
- **Pinned** – whether it is placed on the picture.

**Camera height: Fixed at …** under the list is the exact height of the camera's lens, entered in the Setup Wizard for a camera that looks almost straight down. From straight above, the height and the field of view cannot be told apart, so the calculation kept the camera at this height. The line is missing when no height was entered.

In Simple the rectangle is the grid: a change to the grid's width, depth or offsets, on the Grid tab, from another station or by an import, moves the corners with it and unpins them, since they were pinned on the old grid. Advanced points stay as they are.

## Saving & sharing

- **Save** – make the current values durable. Camera and Grid apply live as you type but revert on reload unless you Save.
- **Apply to all stations** – broadcast Camera and Grid to every OpenFollow station on the network. Use it when several operators share one physical camera.
- **Save as template… / Load template…** – store Camera and Grid together as a portable file and recall a full venue setup later. The Load chooser also lets you **Export** a template as a single `.oftemplate` file and **Import** one from another machine.
