# Paths

*Experimental.* A path is a line on stage that a marker can be locked onto. Once locked, the marker moves only along the path, and its height follows the path's points. Paths are edited in the Path Editor below; this section holds the settings every path shares.

- **Show paths** – draws the active paths on the Operator Screen: semi-transparent grey lines, opaque white while a marker is locked onto one. An open path's end that holds has a dot, an end that releases an arrowhead. The path being recorded is drawn too. The **Toggle Paths** binding switches the same setting. Kept by this station when settings are imported or pushed.
- **Slide time (s)** – how long the pull onto a path lasts after a **Pathfinder** press, however far away the path is, from `0` (at once) to `10`. Default `1`. The marker keeps moving under its input while the path pulls it in, harder and harder, and it ends on the path where its movement took it.

## Locking and moving

The **Pathfinder** binding (keyboard, gamepad, 3D mouse) pulls the marker that input drives onto the nearest active path. Pressed again, it releases the marker where it stands. **Reset** releases it too, and so does switching the path off or deleting it.

While locked, the keyboard, gamepad and 3D mouse move the marker by the part of the push that points along the path; pushing across the path moves nothing. Grabbing the marker with the mouse takes it off its path: the mouse never moves a marker along one. Positions from OSC and from Fully Automatic detection land on the nearest point the marker can reach. Height inputs are ignored: the height is the path's. At an open end that releases, only a push from the keyboard, gamepad or 3D mouse carries the marker off; OSC and detection positions stop at the end.

Where two active paths cross, or one ends within 20 cm of another, and their heights there are within 20 cm, the paths join. At a junction the marker takes whichever path matches the push best; pushing straight on keeps it on its own path. Within 30 cm of a junction, either side, a push along the other path turns the marker onto it, so stopping a little short of a crossing does not miss it. A path that ends on another path's rounded corner point is extended to that corner's curve, so the two join there.

## OSC

`/path/{name}/active 1` / `0` switches a path, and `/marker/{id}/path {name}` locks a marker onto a path (no name releases it). See OSC Input.

While experimental features are off, paths do nothing, and turning them off switches every path off.
