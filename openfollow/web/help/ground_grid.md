# Ground Grid

> Experimental. Shown only with **Experimental features** on (General tab);
> turning those off also switches the ground grid off and keeps its polygon.

A second floor at its own height, for an area that is lower or higher than the
main grid: a pit, a riser, a ramp's landing. It is one polygon drawn on the
stage plan. A marker standing inside it (seen from above) gets its dropline,
ground circle, cone base and assist ring drawn down to the ground grid's
height instead of the main grid's.

It is a **visual aid** on the Operator Screen only. Mouse placement, person
detection, the OSC height placeholders and the height on the marker card stay
on the main grid, and no output protocol changes.

## Settings

- **Ground grid** – use the polygon. Off, nothing changes on the Operator
  Screen; the polygon is kept.
- **Display** – draw the ground grid: the main grid's lines inside the
  polygon, lined up with the main grid, plus the polygon's outline, in the
  main grid's colour, thickness and opacity. Off, markers still drop onto it.
- **Height** – the ground grid's height in world coordinates, the same frame
  as the main grid's Z offset and every marker. A pit below the main grid has
  a lower value; the dropline of a marker inside it can also point up when
  the marker sits below this height.

The polygon needs at least three corners; with fewer the ground grid does
nothing. Up to 64 corners are kept. Where the outline crosses itself, areas
covered an odd number of times count as inside, as for trigger zones.

## Saving & sharing

**Save** stores the settings and the polygon on this station. **Apply to all
stations** saves here and sends them, polygon included, to every other
station. The ground grid also travels with a config export and import.
