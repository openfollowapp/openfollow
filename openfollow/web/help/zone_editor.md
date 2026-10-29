# Zone Editor

Draw zone polygons that fire OSC messages when tracked markers (or detected people) enter or leave. Coordinates are in **metres** on the stage plane, relative to the Reference Point. The top-down view has upstage at the top; the camera view shows the zones on the camera picture.

## Canvas

- **Click a zone** to select it; drag its labelled handles (V1, V2, …) to reshape it.
- Occupied zones brighten and show the occupant count.
- **Top-down / Camera** switches to the camera picture, the zones on the stage floor as the saved camera sees them. **Refresh Image** takes a new snapshot.

### Camera view

Corners move on the picture.

- **Drag a corner** (V1, V2, …) – the loupe follows; saved when let go. A press without a move only chooses it.
- **Drag from an edge** – adds a corner there.
- **Delete corner** – a zone keeps three.
- A corner off the picture has no handle; it moves in the top-down view.
- **Keys** – Tab: next corner; arrows: 1 px, 10 with Shift; Delete: remove; Esc: let go, then leave full screen.

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
- **Z offset** – with *Zone heights* on, the zone's height above the stage floor; the Operator Screen draws it there. Entry and exit still depend on X and Y only.
- **Marker height** – with **Add the Z offset inside this zone** ticked, a marker this station controls is sent and drawn with the Z offset added to its Z while it is inside. Its own Z stays as set. Where such zones overlap, the highest counts.
- **Height transition** – seconds the added height takes to change at the zone's edge: the zone entered sets it, otherwise the zone left. 0 changes it at once.

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
