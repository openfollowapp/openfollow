# Paths

*Experimental.* A path is a line on stage that a marker can be locked onto. Once locked, the marker moves only along the path, and its height follows the path's points. Paths are edited in the Path Editor below; this section holds the settings every path shares.

- **Show paths** – draws the active paths on the Operator Screen: semi-transparent grey lines, opaque white while a marker is locked onto one. An open path's end that holds has a dot, an end that releases an arrowhead. The **Toggle Paths** binding switches the same setting.
- **Slide time (s)** – how long the pull onto a path lasts after a **Pathfinder** press, from `0` (at once) to `10`. Default `1`.

## Locking and moving

The **Pathfinder** binding (keyboard, gamepad, 3D mouse) pulls the marker that input drives onto the nearest active path. Pressed again, it releases the marker where it stands. **Reset** releases it too, and so does switching the path off or deleting it.

While locked, keyboard, gamepad and 3D mouse move the marker only by the part of the push along the path. Grabbing the marker with the mouse takes it off its path. Positions from OSC and from Fully Automatic detection land on the nearest point the marker can reach. Height inputs are ignored: the height is the path's. At an open end that releases, only keyboard, gamepad or 3D mouse push the marker off; OSC and detection positions stop at the end.

Where two active paths cross, or one ends within 20 cm of another, and their heights there are within 20 cm, the paths join. At a junction the marker takes whichever path matches the push best; pushing straight on keeps it on its own path. With **Show paths** on, a small white arrow marks each other path the marker can turn onto.

## OSC

`/path/{name}/active 1` / `0` switches a path, and `/marker/{id}/path {name}` locks a marker onto a path (no name releases it). See OSC Input.

While the **Paths** feature is off (General tab, Features), paths do nothing and every marker on one is let go; each path keeps its Active setting.
