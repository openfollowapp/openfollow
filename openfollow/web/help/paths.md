# Paths

*Experimental.* A path is a line on stage that a marker can be locked onto. Once locked, the marker moves only along the path, and its height follows the path's points. Paths are edited in the Path Editor below; this section holds the settings every path shares.

- **Show paths** – draws the active paths on the Operator Screen: semi-transparent grey lines, opaque white while a marker is locked onto one. An open path's end that holds has a dot, an end that releases an arrowhead. The path being recorded is drawn too. The **Toggle Paths** binding switches the same setting. Kept by this station when another station pushes its settings; an imported settings file replaces it.
- **Slide time (s)** – how long the pull onto a path lasts after a **Pathfinder** press, however far away the path is, from `0` (at once) to `10`. Default `1`. The marker keeps moving under its input while the path pulls it in, harder and harder, and it ends on the path where its movement took it.

## Locking and moving

The **Pathfinder** binding (keyboard, gamepad, 3D mouse) pulls the marker that input drives onto the nearest active path. Pressed again, it releases the marker where it stands. **Reset** releases it too, and so does switching the path off or deleting it.

While locked, the keyboard, gamepad and 3D mouse move the marker by the part of the push that points along the path; pushing across the path moves nothing. Grabbing the marker with the mouse takes it off its path: the mouse never moves a marker along one. Positions from OSC and from Fully Automatic detection land on the nearest point the marker can reach. Height inputs are ignored: the height is the path's. At an open end that releases, only a push from the keyboard, gamepad or 3D mouse carries the marker off; OSC and detection positions stop at the end.

Where two active paths cross, or one ends within 20 cm of another, and their heights there are within 20 cm, the paths join. At a junction the marker takes whichever path matches the push best; pushing straight on keeps it on its own path. Close to a junction, either side, a push along the other path turns the marker onto it: within 50 cm, or as far as the marker travels in half a second at the speed of the push, whichever is more. A marker turned before it reaches the junction carries on to it and turns there, so turning a little early or stopping a little short of a crossing does not miss it. Of two junctions offering the same turn, the marker takes the nearer. With **Show paths** on, while a marker can turn at a junction, the Operator Screen draws a small white arrow along each other path it can turn onto there; carrying on along its own path needs none. A path that ends on another path's rounded corner point is extended to that corner's curve, so the two join there.

## OSC

`/path/{name}/active 1` / `0` switches a path, and `/marker/{id}/path {name}` locks a marker onto a path (no name releases it). See OSC Input.

While the **Paths** feature is off (General tab, Features), paths do nothing and every marker on one is let go; each path keeps its Active setting.
