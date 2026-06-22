# Detection Masks

Polygons that confine person detection to part of the camera image. With masking on, a person is detected only when their feet fall inside at least one mask; anyone outside every mask is ignored. Use this to keep the audience, wings, or a side screen from being tracked.

## Apply masks

**Apply masks** is the master switch. It is **off by default**, so drawing a mask does not restrict anything until you turn it on - the masks you draw are saved but inactive. Turn it on to limit detection to the enabled regions; turn it off to detect over the whole frame again without deleting your masks.

This section is collapsible and starts collapsed; click the section header to expand it.

## How masks work

- Masks are **image-relative**, drawn directly on a snapshot of the camera. They are not stage coordinates and need no camera calibration.
- The check uses each detection's **ground point** (the bottom-center of its box - where the person stands), so a performer at the edge of a masked region counts as in or out by where their feet are.
- Multiple masks act as a **union**: a detection inside any enabled mask is kept.
- Because masks track the camera image, **redraw them if the camera is repositioned or re-zoomed**.

## Canvas

The canvas shows a still snapshot from the active video source. Press **Refresh Image** to grab a fresh frame. If no feed is available, configure a video source first, then refresh.

- **Drawn masks** appear as green polygons (gray and dashed when disabled).
- **Click a mask** to select it; its corners show as draggable handles.
- **Drag a handle** to reshape the mask. **Right-click a handle** to remove that corner (three corners minimum).

## Drawing controls

- **+ New Mask** – start drawing; each click drops a corner.
- **Finish Polygon** – close and save once three or more corners are placed. You can also close by clicking the first corner (highlighted amber) or double-clicking.
- **Cancel** – discard the in-progress polygon.
- **Delete Selected** – remove the selected mask.

## Mask list

Below the canvas, one row per mask:

- **Checkbox** – enable or disable that one mask without deleting it. Disabled masks are ignored even when **Apply masks** is on.
- **Name** – a label for your own reference.
- **Delete** – remove the mask.

Changes apply live – no restart needed.
