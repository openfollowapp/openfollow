# Detection Masks

Polygons that confine person detection to part of the camera image. A person is detected only when their feet fall inside an enabled mask.

## Detection area

**Detection area** is the master switch. **Whole frame** (default) ignores masks, so drawn masks do nothing until you choose **Only inside the enabled masks**. With no mask enabled, detection covers the whole frame either way.

Masks are drawn on a camera snapshot, not in stage coordinates. A detection counts by its ground point (bottom-center of its box), and several masks act as a union. Redraw masks if the camera is moved or re-zoomed.

## Canvas

Shows a still from the active video source. **Refresh Image** grabs a new frame.

- Enabled masks are green polygons; disabled ones are gray and dashed.
- Click a mask to select it, drag a corner handle to reshape it, right-click a handle to remove that corner (three minimum).

## Drawing controls

- **+ New Mask** – each click drops a corner.
- **Finish Polygon** – closes the mask once three or more corners are placed. Clicking the first corner or double-clicking also closes it.
- **Cancel** – discards the polygon in progress.
- **Delete Selected** – removes the selected mask.

## Mask list

One row per mask: a checkbox to enable or disable it without deleting it, a **Name** for your reference, and **Delete**.

Changes apply live.
