# Zone Editor

Draw zone polygons that fire OSC messages when tracked markers (or detected people) enter or leave. Coordinates are in **metres** on the stage plane, relative to the Reference Point. The canvas is a top-down view; upstage is at the top.

## Canvas

- **Click a zone** to select it; drag its labelled handles (V1, V2, …) to reshape it.
- Occupied zones brighten and show the occupant count.
- **Top-down / Camera** switches to the camera picture with the zones drawn on the stage floor as the saved camera sees them. **Refresh Image** takes a new snapshot. New zones are drawn in the top-down view.

### Camera view

The chosen zone's corners, named as in the top-down view, move on the picture.

- **Dragging a corner** shows the loupe and the corner's stage position; the corner is saved when let go. A press that does not move only chooses it.
- **Dragging from an edge** of the chosen zone adds a corner there, between the edge's two corners. A mouse shows a dashed dot where it starts.
- **Delete corner** removes the chosen corner. A zone keeps at least three.
- A corner outside the picture or behind the camera has no handle; it moves in the top-down view.
- The **full screen** button at the top right of the picture opens it over the page.

### Keys on the picture

| Key | Action |
|---|---|
| Tab / Shift+Tab | Next / previous corner of the chosen zone |
| Arrows | Move the corner 1 picture pixel, 10 with Shift |
| Delete / Backspace | Delete the corner |
| Esc | Let go of the corner, then leave full screen |

A corner moved by keys is saved a moment after the last step.

## Drawing

- **+ New Zone** – each click drops a vertex.
- **Finish Polygon** – closes the zone at three or more vertices. Clicking the first vertex or double-clicking also closes it.
- **Cancel Drawing** – discards the polygon in progress.
- **Delete zone** – permanently removes the chosen zone, after confirmation.

## Zone details

### Basic

- **Name**, **Color** – label and outline colour.
- **Zone** – untick **Use this zone** to disable it. A disabled zone fires nothing and is not drawn.
- **Trigger Source** – `Markers (PSN)` (default), `Detection (AI)` (needs Person Detection enabled) or `Both`.
- **Triggered By** – comma-separated marker IDs (`1, 2, 5`). Blank means any marker. Ignored for Detection only.

### Area

One row per vertex, matching the canvas labels.

- **Vertex X / Y** – edit for precise placement.
- **× button** – removes a vertex; a zone keeps at least three.

### Settings

- **OSC Destination** – picked from **OSC Destinations**. A zone with no destination sends nothing.
- **First Entry Address** – empty → occupied.
- **Additional Entry Address** – another occupant enters an occupied zone.
- **Partial Exit Address** – one occupant leaves, the zone stays occupied.
- **Final Exit Address** – the last occupant leaves.

A blank address suppresses that event. Each field takes a full OSC message: address path first, then arguments; quotes group multi-word arguments.

### Diagnostics

- **Live state** – occupant count and the last fired address.
- **Test send** – **First Entry**, **Additional Entry**, **Partial Exit** and **Final Exit** each send their address at once. Pending edits are saved first.

## Actions

- **Save Zone** – saves Basic, Area and Settings changes; the live engine updates immediately.
- **Duplicate** – copies the zone with its shape and settings.
- **Save as template…** / **Load template…** – store or replace the whole zones configuration. Loading asks first. In the chooser, **Export** downloads a `.oftemplate` file and **Import…** loads one from another machine.
