# Camera

Where the real camera sits in the venue and which lens it uses. OpenFollow uses these to project tracked points into stage coordinates, so they must match the physical rig.

The easiest way to set them is the **Setup Wizard** (the Open Setup Wizard button on the Camera & Grid tab), which solves position, orientation, and field of view from four marked grid corners. The fields here are for direct edits and fine-tuning.

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

## Lens distortion (experimental)

Wide-angle and fisheye lenses bow straight lines, so the pinhole overlay (grid, markers, zones) no longer sits on top of the curved video. These two sliders bow the **overlay** to match the lens. The video frame itself is never warped (warping every pixel would be too slow on a Pi), so there is no performance cost when the sliders are at `0`.

- **Barrel / fisheye (k1)** – the main correction. Drag it negative until the overlay grid hugs a wide-angle / fisheye image (lines curve inward); positive corrects a pincushion lens (lines curve outward).
- **Edge fit (k2)** – a finer, higher-order adjustment for the frame edges, where strong fisheye lenses bend the most. Set k1 first, then nudge k2.

The correction is centred on the middle of the image. Tune by eye: enable experimental features, open this page next to the live display, and adjust until the overlay lines follow the video. Mouse placement and AI tracking are corrected to match, so clicking a point in the video still lands the marker there. `0` / `0` disables the correction (plain pinhole).

The **Setup Wizard** also carries these sliders in its Corner Pinning step (behind the same experimental-features toggle): bow the projected grid to match the lens, then pin the corners. The solve undistorts the pinned corners before fitting the pinhole pose, so a fisheye lens no longer skews the calibration. Sliders set in the wizard are saved back here on Apply, and vice versa.

## Saving & sharing

- **Save** – make the current values durable. Camera and Grid apply live as you type but revert on reload unless you Save.
- **Apply to all stations** – broadcast Camera and Grid to every OpenFollow station on the network. Use it when several operators share one physical camera.
- **Save as template… / Load template…** – store Camera and Grid together as a portable file and recall a full venue setup later. The Load chooser also lets you **Export** a template as a single `.oftemplate` file and **Import** one from another machine.
