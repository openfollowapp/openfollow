# Keyboard Input

Configure how a keyboard drives markers on the Operator Screen. Keyboard input uses direct hardware polling, so it stays responsive under heavy pipeline load. All four input methods (keyboard, gamepad, mouse, OSC) can be active at once – within a frame, last-write-wins per axis.

**Enabled** – master toggle. When off, all keyboard movement and action keys are ignored. Leave on unless an operator is using a gamepad exclusively on this station.

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

> If these keys move the marker the *opposite* way to what you see on screen, check **Invert control direction** under Markers & Zones → Marker Movement. It reverses left/right and forward/back for the keyboard, gamepad and 3D Mouse together, so the controls match a camera looking from upstage.

- **Z+** – raise the active marker. Default: `q`.
- **Z-** – lower the active marker. Default: `e`.

### Actions

- **Reset Marker** – snap the active marker to its configured default position. Default: `x`.
- **Toggle Help** – show or hide the help overlay on the Operator Screen. Default: `h`.
- **Toggle Zone Overlay** – show or hide the zone overlay on the Operator Screen. Default: `z`.
- **Speed -** / **Speed +** – step movement speed down or up through the configured range. Defaults: `r` / `t`.
- **Settings Menu** – open the Settings menu on the Operator Screen. Default: `m`.
- **Clear Messages** – clear every operator-message card. Default: *(unset)*.
- **Next Marker** / **Prev Marker** – cycle which marker your inputs drive, when more than one controlled marker is configured. Defaults: `Tab` / *(unset)*.

> Each key drives one action. Typing a key another action already uses moves it when the field loses focus: the field you changed says in blue where it came from, and the field it left empties and turns red, since that action does nothing until it gets a key again. Once the key moves on, the red field only names the key it lost. The notes clear on **Save**. The WASD and IJKL keys are kept for movement whichever layout is chosen, so no action can use them.

## Saving

- **Save** – write the current settings to disk. Changes take effect immediately on the Operator Screen but are lost on restart unless you Save.
