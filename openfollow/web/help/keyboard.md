# Keyboard Input

Configure how a keyboard drives markers on the Operator Screen. Keyboard input uses direct hardware polling, so it stays responsive under heavy pipeline load. All four input methods (keyboard, gamepad, mouse, OSC) can be active at once – within a frame, last-write-wins per axis.

**Enabled** – master toggle. When off, all keyboard movement and action keys are ignored. Leave on unless an operator is using a gamepad exclusively on this station.

## Button Mapping

Expand **Button Mapping** to customise which keys perform which actions. Click **Reset to Defaults** to restore the factory layout below.

### Movement

**X / Y Layout** – selects the cluster of keys for stage-left/right and upstage/downstage movement:

| Layout | Forward (upstage) | Back (downstage) | Left | Right |
|---|---|---|---|---|
| WASD (default) | W | S | A | D |
| IJKL | I | K | J | L |
| Numpad (8/4/2/6) | 8 | 2 | 4 | 6 |

Arrow keys are reserved for navigating the on-screen menu and cannot be used for movement.

- **Z+** – raise the active marker. Default: `q`.
- **Z-** – lower the active marker. Default: `e`.

### Actions

- **Reset Marker** – snap the active marker to its configured default position. Default: `x`.
- **Toggle Help** – show or hide the help overlay on the Operator Screen. Default: `h`.
- **Toggle Zone Overlay** – show or hide the zone overlay on the Operator Screen. Default: `z`. Cannot be set to any key already used by the WASD or IJKL movement cluster.
- **Speed -** / **Speed +** – step movement speed down or up through the configured range. Defaults: `r` / `t`.
- **Settings Menu** – open the Settings menu on the Operator Screen. Default: `m`.
- **Next Marker** / **Prev Marker** – cycle which marker your inputs drive, when more than one controlled marker is configured. Defaults: `Tab` / *(unset)*.

> Each key binding must be unique. Assigning the same key to two actions shows a validation error – correct it before saving. Movement layout keys (the four directional keys in the chosen cluster) cannot be reused as action keys.

## Saving

- **Save** – write the current settings to disk. Changes take effect immediately on the Operator Screen but are lost on restart unless you Save.
