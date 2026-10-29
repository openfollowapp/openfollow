# Ground Grid

> Experimental. Shown only with the **Ground grid** feature on (General tab, Features); turning it off hides and stops it, and every setting is kept.

A second floor at its own height for a lower or higher area, such as a pit or a riser. It is one polygon drawn on the stage plan. A marker standing inside it (seen from above) gets its Z line, ground circle, cone base and assist ring drawn down to the ground grid's height instead of the main grid's.

It is a **visual aid** on the Operator Screen only. Person detection, the OSC height placeholders, the height on the marker card and all output protocols stay on the main grid.

## Area

Drawn on a top-down stage plan with the main grid and the live markers.

- **Draw polygon** – click to place corners; click the first corner, double-click or **Finish polygon** to close it. The new polygon replaces the old one. **Cancel drawing** abandons it.
- **Drag a corner** to move it. **Clear polygon** removes the polygon.
- **Vertices (x, y)** – the corners on the stage. Type a value to move a corner; **×** removes a corner, down to three.
- **Top-down / Camera** switches to the camera picture, showing the polygon and its grid lines at the typed **Height**. This browser remembers the choice.

The polygon needs at least three corners and keeps up to 64. Changes show at once but are kept only when you **Save**.

## Settings

- **Ground grid** – markers inside the polygon get their floor elements drawn down to its height. Off, they stay on the main grid; the polygon is kept.
- **Display** – draw the polygon's outline and the main grid's lines inside it, in the main grid's colour, thickness and opacity. Off, markers inside it still drop onto it when **Ground grid** is on.
- **Height** – the ground grid's height in world coordinates, the same frame as the main grid's Z offset. A pit has a lower value.

**Save** stores the settings and polygon on this station. **Apply to all stations** saves here and sends them to every other station.
