# 3D Mouse Input

Steer the selected marker with a 6DOF "3D Mouse" – a spring-centred puck you push, pull, lift and twist. Off by default; turn it on with the **Enabled** checkbox.

**A controller in its own right.** A 3D Mouse counts alongside gamepads in one shared numbering (3D mice first, then gamepads), so its bound marker carries a controller badge (`C1`, `C2`, …) on the Operator Screen.

- **On its own** it drives whichever marker is currently selected, the same as the keyboard and the on-screen mouse; use the marker-cycle buttons (below) to change which marker it steers.
- **Alongside other controllers** it's pinned to its own marker by plug order, the way two gamepads each keep their own marker, so two operators don't fight over one selection. The marker-cycle buttons go quiet in this mode, since each controller already owns a marker.

**Device not found.** If no 3D Mouse is connected the section still saves, but nothing moves. On a fresh device, plug the unit in and the connection is picked up automatically; unplugging and re-plugging recovers on its own.

## Axis mapping

The puck senses six motions. Each gets its own row in the form, named for the gesture you make – the same push / pull / tilt / twist motions shown in the 3Dconnexion manual. The config-file key is in parentheses.

| | Motion | Sends to (default) |
|---|---|---|
| ![Cap pushed left and right](/assets/help/mouse3d-pan-x.svg) | **Push left / right** (`pan_x`) | Move X |
| ![Cap pushed forward and back](/assets/help/mouse3d-pan-y.svg) | **Push forward / back** (`pan_y`) | Move Y |
| ![Cap pulled up and pushed down](/assets/help/mouse3d-lift.svg) | **Pull up / push down** (`lift`) | Move Z (height) |
| ![Cap tilted forward and back](/assets/help/mouse3d-pitch.svg) | **Tilt forward / back** (`pitch`) | off |
| ![Cap twisted](/assets/help/mouse3d-yaw.svg) | **Twist / spin** (`yaw`) | Speed |
| ![Cap tilted left and right](/assets/help/mouse3d-roll.svg) | **Tilt left / right** (`roll`) | off |

Each row has four controls:

**Target** – where that motion sends its movement:

- **x / y / z** – move the marker along that stage axis (X = stage left/right, Y = upstage/downstage, Z = height).
- **speed** – instead of moving the marker, the axis ramps the marker's move-speed while held: push one way to speed up, the other to slow down. Useful for trimming speed on the fly without reaching for a key.
- **fader** – drives the controlled marker's fader (0–1) while held: push one way to raise it, the other to lower. Same integrator the gamepad's marker-fader stick uses, so the fader-speed setting (under Gamepad) sets how fast full deflection travels.
- **none** – ignore the axis.

The defaults are in the table above (push drives x / y / z with the up/down motion geared down for gentler height, twist ramps the move-speed, the two tilts are off). Re-point any motion at any target – e.g. send twist to z if you'd rather twist for height.

**Sensitivity** – a per-axis multiplier (0–10). `1` means full deflection moves at the marker's configured move-speed; `2` doubles it; values below `1` make that axis finer. Set it per axis so, say, height is gentler than lateral movement.

**Deadzone** (0–1) – a per-axis dead band near centre: deflection below it is ignored so the marker doesn't creep when your hand rests on the puck. Set it per axis – e.g. a larger deadzone on a twitchy rotation axis than on the translations. Raise an axis's deadzone if the marker drifts at rest on it.

**Invert** – flips that axis's direction.

## Response curve

**Response curve** – shapes how deflection maps to speed, shared across all axes:

- **linear** – direct 1:1.
- **logarithmic** – fine control near centre, fast at the edges.
- **quadratic** – even finer near centre.
- **s-law** – smooth ease-in and ease-out.

## Buttons

Every action binds to a device button by its **index** (a whole number). Leave a binding **blank** to unbind it. Button counts vary by model – a compact unit has two, larger ones have many.

**Detect** – click a binding's **Detect** button, then press the button on the device; the field fills in automatically. It works whether or not the feature is enabled, as long as the device is connected.

Bindable actions: **Reset marker** (return the marker to its default position), **Next / Previous marker** (cycle the selected marker, when this is the only controller – see above), **Speed up / Speed down** (step the move-speed), **Toggle help**, and **Toggle zones**. The Settings menu has no 3D-mouse binding: it needs a keyboard (or gamepad) to open and navigate.

**Save** – store the settings. Changes apply immediately, with no restart, but are lost on restart unless you save.
