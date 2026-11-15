# Camera & Lens Calibration

Where the real camera sits and which lens it uses. These values project tracked points into stage coordinates, so they must match the rig. The **Setup Wizard** (**Open Setup Wizard**) calculates them from marks pinned on the picture; this section shows them and edits none.

Lengths are relative to the **Reference Point**. Angles are in degrees.

## Position and orientation

- **Position X** – stage left positive, stage right negative. `0` is the centre line.
- **Position Y** – upstage positive, towards the audience negative.
- **Position Z** – height of the lens above the stage floor.
- **Pitch** – tilt from the horizon. Negative looks down; front of house is typically around −20°.
- **Yaw** – compass bearing on the stage floor. `0` looks upstage, `180` looks back at the audience.
- **Roll** – rotation around the lens axis; `0` if not canted.

## Lens

- **Horizontal Field of View** – the angular width the camera sees.
- **Barrel / fisheye** – negative for wide-angle and fisheye images (lines curve inward), positive for pincushion.
- **Corner correction** – extra correction near the corners.
- **Right of the picture centre** / **Above the picture centre** – the **lens centre** in pixels of the live video, measured from the picture centre; negative is left or below. Shows **No video** without a picture.

Barrel / fisheye and Corner correction bend the overlay (grid, markers, zones) to match the lens; the video is never warped. `0` / `0` is a plain pinhole.

The Setup Wizard's Lens step measures the pair from straight lines you mark in the picture. **Not usable** values are shown but not applied.

## Calibration

The **Calibration** column shows which calibration the Setup Wizard applied last: **Simple**, **Advanced** or **None**.

- **Calibrated** – when it was saved.
- **Video source** – the source shown when it was saved. Another source, or the camera moved or zoomed, needs a new calibration.
- **Lens rating** – the Lens step's rating, **User input** for typed values, **Loaded**, or **OpenCV calibration**.
- **Pinned** – how many points are placed on the picture.
- **Accuracy** – the largest distance, in pixels, between a pinned point and where the saved camera sees it. A large value means a point was pinned wrong or the camera moved.
- **Calibration points** – a read-only list of each point's **Kind** (**Known point** or **Triangulated point**), position, pinned state and error.

**Camera height: Fixed at …** in the Calibration column is the exact height of the camera's lens, entered for a camera looking almost straight down.

In Simple, changing the grid's width, depth or offsets unpins the corners.

## Saving & sharing

- **Apply to all stations** – copy this camera and its calibration to every station on the network.
- **Save as template… / Load template…** – store Camera and Grid together; the chooser can **Export** and **Import** `.oftemplate` files.
