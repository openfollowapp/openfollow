# Grid

A reference plane drawn over the live video showing where stage coordinates land in the image. A **visual aid** only; it doesn't change tracking.

Easiest to set with the **Setup Wizard** (Open Setup Wizard on the General or Camera & Grid tab): its Simple Camera Calibration saves the measured rectangle as the grid. Distances are in **metres**, relative to the Reference Point.

## Display

- **Grid** – show or hide the grid on the Operator Screen. The Origin Marker has its own toggle.

## Dimensions

- **Width** – size along X (stage left–right).
- **Depth** – size along Y (downstage–upstage).
- **Maximum Height** – height of the tracking volume above the grid plane. The fractional-height OSC placeholders (`[z.frac]` / `[z.frac.inv]`) divide by it; bindings using them are skipped while it is `0` or empty.
- **Spacing** – distance between grid lines.

## Appearance

- **Line Color**
- **Line Thickness** – pixels, 1–20.
- **Opacity** – `0` transparent, `1` opaque.

## Offset Position

For when the Reference Point isn't at the grid centre.

- **X Offset** – shift along X. `0` centres the grid.
- **Y Offset** – shift along Y. Half the depth puts the Reference Point at the front (downstage) edge.
- **Z Offset** – height of the grid plane above the Reference Point. `0` is the floor; raise it for a riser.

After a Simple calibration, saving a new width, depth or offset moves the rectangle's corners with it and unpins them; the next Setup Wizard run pins them again.

## Origin Marker

A cross at the Reference Point.

- **Origin** – on or off.
- **Length** – arm length in metres.
- **Thickness** – pixels, 1–20.

## Saving & sharing

- **Save** – Grid applies live but reverts on reload until saved.
- **Apply to all stations** – broadcasts Camera and Grid to every station on the network.
- **Save as template… / Load template…** – store Camera and Grid together as a file. The chooser also offers **Export** and **Import** of a `.oftemplate` file.
