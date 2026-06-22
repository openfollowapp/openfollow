# Zone Editor

A canvas for drawing and editing zone polygons that drive OSC cue messages. Each zone watches for tracked markers (and optionally person-detection results) crossing its boundary, then fires OSC messages to your show-control software – e.g. triggering a QLab cue when a performer enters an area of stage.

Zone coordinates use the same stage plane as the Grid: **metres**, relative to the **Reference Point** – the (0, 0, 0) of your show (see Core Concepts). The canvas is a top-down view; upstage is towards the top.

## Canvas

Interactive top-down stage view; the grid and any placed markers stay visible as context.

- **Zones** are coloured polygons, names centred inside. When occupied, the fill brightens and the occupant count appears next to the name.
- **Click a zone** to select it. Its vertices show as labelled handles (V1, V2, …) you can drag to reshape it.
- **Drag a vertex** to move it. The Area tab's coordinate fields update live.
- **Click an empty area** in drawing mode to place the next vertex.

## Drawing controls

- **+ New Zone** – enter drawing mode (crosshair cursor); each click drops a vertex.
- **Finish Polygon** – close and save once three or more vertices are placed. Drawing only. You can also close by clicking the first vertex (highlighted amber) or double-clicking.
- **Cancel Drawing** – discard the in-progress polygon. Drawing only.
- **Delete Selected** – permanently remove the selected zone after confirmation. Selection only.

## Zone details

Selecting a zone opens a tabbed detail panel below the canvas.

### Basic tab

- **Name** – label shown on the canvas and in diagnostics.
- **Color** – zone outline and label colour. Click the swatch for the colour picker.
- **Enabled** – uncheck to disable without deleting. Disabled zones don't fire OSC messages and aren't evaluated for occupancy.
- **Trigger Source** – what feeds the occupancy engine:
  - `Markers (PSN)` – tracked markers drive events (default).
  - `Detection (AI)` – person-detection bounding boxes drive events. Requires Person Detection enabled.
  - `Both` – either source fires events.
- **Triggered By** – comma-separated marker IDs this zone responds to (e.g. `1, 2, 5`). Blank = any marker. Ignored when Trigger Source is Detection only.

### Area tab

One editable row per polygon vertex, labelled V1, V2, … to match the canvas handles.

- **Vertex X / Y** – stage-plane coordinates of that vertex. Edit for precise placement; the canvas updates live.
- **× button** – remove that vertex. Available only above three vertices (a triangle is the minimum).

> Tip: vertex row labels (V1, V2, …) match the canvas handle labels, so you can always tell which row is which corner.

### Settings tab

The four OSC messages the zone fires on occupancy transitions. Leave any address blank to suppress that event.

- **OSC Destination** – the shared connection (host, port, transport) this zone's messages go to, picked from the list you maintain under **OSC Destinations**. A zone with no destination selected emits nothing. Edit the destination's IP once and every referencing zone and transmitter repoints live.
- **First Entry Address** – sent on empty → occupied (0 → 1 occupant). E.g. trigger a cue or raise an audio bus.
- **Additional Entry Address** – sent when another occupant enters an already-occupied zone (n → n+1). E.g. increment a counter or layer an effect.
- **Partial Exit Address** – sent when one occupant leaves but the zone stays occupied (n+1 → n, n ≥ 1). E.g. decrement a counter.
- **Final Exit Address** – sent when the last occupant leaves (1 → 0). E.g. restore default state or kill an effect.

Each address field accepts a full OSC message: the first token is the address path, the rest are arguments. Quotes group multi-word arguments. Arguments are typed at the wire boundary – integers stay integers, floats stay floats, everything else is a string.

### Diagnostics tab

Live status for the selected zone – nothing here changes configuration.

- **Live state panel** – current occupant count and the most recently fired event address (with how long ago).
- **Test send panel** – force a single OSC packet on one of the four event addresses without a live occupancy transition. **First Entry**, **Additional Entry**, **Partial Exit**, and **Final Exit** each fire their address immediately. Pending edits are saved first, so the packet reflects the address shown in the Settings tab. The raw server response appears beneath the buttons.

## Zone actions

At the bottom of the detail panel, for the selected zone.

- **Save Zone** – persist all Basic, Area, and Settings changes to disk. The canvas and live engine update immediately.
- **Duplicate** – copy the selected zone with the same shape and settings. Edit the copy instead of redrawing when zones share a boundary or configuration.

## Saving & sharing

- **Save as template…** – write the complete zones configuration (all polygons plus settings) as a `.openfollowtemplate` file in your user templates folder. Use it for per-show or per-venue presets.
- **Load template…** – replace the entire current zones configuration from a saved template. You'll confirm before the current zones are overwritten; the page reloads automatically afterwards.
