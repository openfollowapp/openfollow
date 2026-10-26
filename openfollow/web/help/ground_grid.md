# Ground Grid

> Experimental. Shown only with the **Ground grid** feature on (General tab,
> Features); turning it off hides the ground grid and stops it, and every
> setting here is kept.

A second floor at its own height, for an area that is lower or higher than the
main grid: a pit, a riser, a ramp's landing. It is one polygon drawn on the
stage plan. A marker standing inside it (seen from above) gets its Z line,
ground circle, cone base and assist ring drawn down to the ground grid's
height instead of the main grid's.

It is a **visual aid** on the Operator Screen only. Person detection, the OSC
height placeholders and the height on the marker card stay on the main grid,
and no output protocol changes. A marker inside it is grabbed with the mouse
by its ring where it is drawn, and dragged across the floor it was grabbed on.

## Area

The polygon is drawn on a top-down stage plan with the main grid and the live markers, like the Zone Editor.

- **Draw polygon** – click to place corners; click the first corner or double-click to close it (**Finish polygon** works too). The new polygon replaces the old one.
- **Drag a corner** to move it. **Clear polygon** removes it.
- **Vertices** lists the corners as x and y on the stage, in the station's units. Type a value to move a corner; a value that cannot be read leaves the corner where it was. **×** removes a corner, down to three.
- **Top-down / Camera** switches to the camera picture with the polygon's outline and its grid lines at the **Height** typed in, as the saved camera and lens see them. Dragging a corner there lands it on the ground grid's own height under the pointer. This browser remembers the choice.

Changes to the polygon show at once but are kept only when you **Save** (or **Apply to all stations**).

## Settings

- **Ground grid** – markers standing inside the polygon get their floor
  elements drawn down to its height. Off, they stay on the main grid; the
  polygon is kept.
- **Display** – draw the ground grid: the main grid's lines inside the
  polygon, lined up with the main grid, plus the polygon's outline, in the
  main grid's colour, thickness and opacity, whether or not **Ground grid**
  is on. Off, markers inside it still drop onto it when **Ground grid** is on.
- **Height** – the ground grid's height in world coordinates, the same frame
  as the main grid's Z offset and every marker. A pit below the main grid has
  a lower value; the Z line of a marker inside it can also point up when
  the marker sits below this height.

The polygon needs at least three corners; with fewer the ground grid does
nothing. Up to 64 corners are kept; a drawing closes by itself at the 64th. Where the outline crosses itself, areas
covered an odd number of times count as inside, as for trigger zones.

## Saving & sharing

**Save** stores the settings and the polygon on this station. **Apply to all
stations** saves here and sends them, polygon included, to every other
station. The ground grid also travels with a config export and import.
