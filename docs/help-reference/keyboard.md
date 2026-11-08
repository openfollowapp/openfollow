# Keyboard Input

Configure how a keyboard drives markers on the Operator Screen. Keyboard input uses direct hardware polling, so it stays responsive under heavy pipeline load. All four input methods (keyboard, gamepad, mouse, OSC) can be active at once – within a frame, last-write-wins per axis.

On a Mac, keys act on the Operator Screen only while its window is the active one, and a key pressed with Command, Control or Option held does nothing there, unless that modifier is itself bound to an action below: typing into Spotlight and macOS shortcuts such as Cmd+H leave the markers and menus alone.

**Keyboard** – master toggle for the movement keys. When off, the movement keys are ignored; the action keys still work. Leave on unless an operator is using a gamepad exclusively on this station.

**Keyboard Layout** – the layout of the keyboard plugged into this station, so a key reads as the letter printed on it. The list holds the layouts installed on this station. **System** uses the layout set in the station's operating system; its entry names that layout. **Variant** picks a variant of the chosen layout, such as one without dead keys; **Standard** is the layout as it is. The keyboard model and options set in the operating system, such as Caps Lock as Ctrl, apply whichever layout is chosen.

A new layout takes effect when OpenFollow restarts. Until then the section names the layout still in use and offers **Restart OpenFollow**; outputs pause until it is back, and it is refused while a software update is running. When only the operating system's keyboard model or options changed since OpenFollow started, the section says so instead. The layout belongs to this station: it is not exported, pushed to other stations or replaced by an import, and **Restore defaults** returns it to System.

The setting appears only on a station that runs OpenFollow's own Operator Screen session. On macOS, and wherever OpenFollow runs inside another desktop, the keyboard follows that system's own layout.

## Button Mapping

Expand **Button Mapping** to customise which keys perform which actions. Click **Reset to Defaults** to restore the default layout below.

### Movement

**X / Y Layout** – selects the cluster of keys for stage-left/right and upstage/downstage movement:

| Layout | Forward (upstage) | Back (downstage) | Left | Right |
|---|---|---|---|---|
| WASD (default) | W | S | A | D |
| IJKL | I | K | J | L |
| Numpad (8/4/2/6) | 8 | 2 | 4 | 6 |

Arrow keys are reserved for navigating the on-screen menu and cannot be used for movement.

> If these keys move the marker a different way to what you see on screen, set **Control frame** under Markers & Zones → Marker Movement. It turns left/right and forward/back for the keyboard, gamepad and 3D Mouse together, so the controls follow the camera's view.

- **Z+** – raise the active marker. Default: `q`.
- **Z-** – lower the active marker. Default: `e`.

### Actions

- **Reset Marker** – snap the active marker to its configured default position. Default: `x`.
- **Toggle Help** – show or hide the help overlay on the Operator Screen. Default: `h`.
- **Toggle Zone Overlay** – show or hide the zone overlay on the Operator Screen. Default: `z`.
- **Speed -** / **Speed +** – step movement speed down or up through the configured range. Defaults: `r` / `t`.
- **Settings Menu** – open the Settings menu on the Operator Screen; pressed in any menu, it closes them all. While a menu is open, the Operator Screen lists the menu keys in its top-left corner. Default: `m`.
- **Clear Messages** – clear every operator-message card. Default: *(unset)*.
- **Next Marker** / **Prev Marker** – cycle which marker your inputs drive, when more than one controlled marker is configured. Defaults: `Tab` / *(unset)*.
- **Pathfinder** *(experimental)* – slide the selected marker onto the nearest active path, where it stays until pressed again or moved past an end of the path that releases it; while a path is recording, add the marker's position to it instead. Default: *(unset)*.
- **Toggle Paths** *(experimental)* – show or hide paths on the Operator Screen. Default: *(unset)*.

> Each key drives one action. Typing a key another action already uses moves it when the field loses focus: the field you changed says in blue where it came from, and the field it left empties and turns red, since that action does nothing until it gets a key again. Once the key moves on, the red field only names the key it lost. The notes clear on **Save**. The WASD and IJKL keys are kept for movement whichever layout is chosen, so no action can use them.

## Saving

- **Save** – write the current settings to disk. Changes take effect immediately on the Operator Screen but are lost on restart unless you Save.
