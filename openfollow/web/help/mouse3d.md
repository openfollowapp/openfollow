# 3D Mouse Input

Steer the selected marker with a 6DOF "3D Mouse" – a spring-centred puck you push, pull, lift and twist. Off by default; turn it on with the **Enabled** checkbox.

**One device, the selected marker.** A single 3D Mouse drives whichever marker is currently selected, the same as the keyboard and the on-screen mouse. Use the marker-cycle buttons (below) to change which marker it steers.

**Device not found.** If no 3D Mouse is connected the section still saves, but nothing moves. On a fresh device, plug the unit in and the connection is picked up automatically; unplugging and re-plugging recovers on its own.

## Axis mapping

The puck has six source axes: three translations – **Pan X** (left/right), **Pan Y** (forward/back) and **Lift** (up/down) – and three rotations – **Pitch**, **Yaw** and **Roll**. Each axis has its own row:

**Target** – where that axis sends its motion:

- **x / y / z** – move the marker along that stage axis (X = stage left/right, Y = upstage/downstage, Z = height).
- **speed** – instead of moving the marker, the axis ramps the marker's move-speed while held: push one way to speed up, the other to slow down. Useful for trimming speed on the fly without reaching for a key.
- **none** – ignore the axis.

By default the three translations map to x / y / z (with lift geared down for gentler height), twisting the puck (yaw) ramps the move-speed, and pitch / roll are off. Re-point any axis at any target – e.g. map Yaw to z if you'd rather twist for height.

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

Bindable actions: **Reset marker** (return the marker to its default position), **Next / Previous marker** (cycle the selected marker), **Speed up / Speed down** (step the move-speed), **Toggle help**, **Toggle zones**, and **Settings menu**.

**Save** – store the settings. Changes apply immediately, with no restart, but are lost on restart unless you save.
