# Path Editor

*Experimental.* Lists every path on this station and edits the selected one. Coordinates are stage coordinates in the station's length unit, relative to the **Reference Point**.

## Paths

One row per path. The row's header shows a green dot while the path is active, its name, whether it is **Open** or **Closed**, how many points it has, a blue **Recording** chip while it records, and which markers are locked onto it now. Open a row to edit it; the picture below edits the row opened last.

- **Path** (Basics) – an active path can be locked onto and is drawn on the Operator Screen. Saved, so it survives a restart; each station keeps its own when paths are imported or pushed. Switching a path off releases every marker on it.
- **Name** (Basics) – names are what OSC uses: letters, digits, `.`, `-` and `_`, up to 32, never the same as another path's ignoring case. A renamed path keeps the markers on it and its recording.
- **Record** / **Stop recording** – while a path records, every **Pathfinder** press adds the position of the marker that input drives (X, Y and height) as the path's next point. One path records at a time; a restart stops recording.
- **Delete** – removes the path after confirmation.

**+ New path** adds an empty, inactive path. **Save as template… / Load template…** store and load every path with the slide time; loading replaces this station's paths and keeps the Active setting of the ones it already has by name. **Apply to all stations** sends the paths and the slide time to every station found; each keeps its own Active settings.

## Basics and Points

- **Shape** – a closed path joins its last point to its first, and a marker can go round it.
- **Points** – X, Y and Z of each point, in order. Z is the height the locked marker has there; between points it changes evenly.
- **Option** – for a corner, its **Radius**: how much the path is rounded there. Default 0.5 m. A radius too large for the neighbouring points is made smaller. For the first and last point of an open path, what a marker pushed past that end does: **Hold at end** keeps it there, **Release at end** lets it go.
- **↑ / ↓** move a point in the order; **Delete** removes it. **+ Point** adds one a metre past the last. **Point from marker** adds the position a marker has on the wire now.

## Picture

The current video picture with every path drawn as the camera sees it: the selected path in the accent colour with its points as handles, other active paths solid, inactive ones dashed, and markers as dots. Drag a point of the selected path to move it at its own height. Clicking the picture adds a point at stage-floor height. **Refresh Image** takes a new picture.
