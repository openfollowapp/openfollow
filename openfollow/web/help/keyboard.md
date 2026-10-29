# Keyboard Input

How a keyboard drives markers on the Operator Screen.

**Keyboard** – master toggle for the movement keys. When off, they are ignored; the action keys still work.

**Keyboard Layout** – the layout of the keyboard plugged into this station, so a key reads as the letter printed on it. **System** uses the layout set in the station's operating system. **Variant** picks a variant of the chosen layout; **Standard** is the layout as it is.

A new layout takes effect when OpenFollow restarts. Until then the section names the layout still in use and offers **Restart OpenFollow**; outputs pause until it is back. The layout belongs to this station: it is not exported or pushed to other stations, and **Restore defaults** returns it to System. The setting appears only on a station that runs OpenFollow's own Operator Screen session.

## Button Mapping

Expand **Button Mapping** to change which keys do what. **Reset to Defaults** restores the layout below.

**X / Y Layout** – the key cluster for stage-left/right and upstage/downstage movement:

| Layout | Forward (upstage) | Back (downstage) | Left | Right |
|---|---|---|---|---|
| WASD (default) | W | S | A | D |
| IJKL | I | K | J | L |
| Numpad (8/4/2/6) | 8 | 2 | 4 | 6 |

Arrow keys are reserved for the on-screen menu. If these keys move the marker differently from what you see, set **Control frame** under Marker Movement.

- **Z+** / **Z-** – raise / lower the active marker. Defaults: `q` / `e`.
- **Reset Marker** – snap the active marker to its default position. Default: `x`.
- **Toggle Help** – show or hide the help overlay. Default: `h`.
- **Toggle Zone Overlay** – show or hide the zone overlay. Default: `z`.
- **Speed -** / **Speed +** – step movement speed down or up. Defaults: `r` / `t`.
- **Settings Menu** – open the Settings menu; pressed in any menu, it closes them all. Default: `m`.
- **Clear Messages** – clear every operator-message card. Default: unset.
- **Next Marker** / **Prev Marker** – cycle which marker your inputs drive. Defaults: `Tab` / unset.
- **Pathfinder** *(experimental)* – slide the selected marker onto the nearest active path; while a path is recording, add the marker's position to it instead. Default: unset.
- **Toggle Paths** *(experimental)* – show or hide paths on the Operator Screen. Default: unset.

Each key drives one action. Typing a key another action already uses moves it: the field you changed says in blue where it came from, and the field it left empties and turns red, since that action does nothing until it gets a key again. The WASD and IJKL keys are kept for movement, so no action can use them.

**Save** – changes apply immediately but are lost on restart unless you save.
