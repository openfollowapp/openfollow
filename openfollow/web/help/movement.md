# Marker Movement

Speed limits, the default operating speed, the home position markers snap back to on a reset, and the direction relative controllers steer in. Speeds affect every input method – gamepad, keyboard, mouse, and OSC – since all draw from the same speed range.

## Speed

Values are in metres per second (m/s); on a station set to imperial units, enter feet per second and the stored metric value is shown under each field.

- **Min Speed** – the slowest the marker can travel; the bottom of the range operators step through with the Speed up / down controls. Must be ≥ 0. Default `0.1` m/s.
- **Max Speed** – the fastest the marker can travel. Must be ≥ 0 and not lower than Min Speed. Default `3.0` m/s.
- **Default Speed** – the speed applied to any marker when the station starts, and to any marker whose speed the operator hasn't adjusted. Must fall within the Min–Max range. Default `2.0` m/s.

> Each marker keeps its own live speed during a show; two operators on two gamepads adjust independently. Changing Min Speed or Max Speed takes effect the next time an operator adjusts speed – it does not snap the current live speed.

## Default Position (on reset)

The position a marker jumps to when the operator presses the Reset key or button (default **X** on keyboard, **X** on gamepad). Coordinates are in metres relative to the **Reference Point** – the (0, 0, 0) of your show.

- **Default X** – stage left is positive, stage right negative. `0` is on the centre line.
- **Default Y** – upstage is positive, downstage (towards the audience) negative.
- **Default Z** – the marker's Z in world coordinates, the frame the Reference Point defines. The stage floor sits at the grid's **Z Offset**, so `0` puts the marker on the floor only when that offset is `0`.

X and Y default to `0.0` (the Reference Point itself); Z defaults to `1.6`.

## Control frame

**Control frame** – which way the gamepad, keyboard and 3D Mouse move a marker. Pick the one that matches how your operators think:

- **Stage axes** (default) – stick right moves stage left, stick up moves upstage, whatever the camera sees. Right for a camera at front of house looking straight upstage, or for operators who think in stage directions.
- **Fixed heading** – stage axes turned to the bearing in **Heading (°)**, measured like the camera's **Yaw**: `0` is a camera looking upstage, `180` one hung upstage looking back at the audience, `90` / `270` box-boom positions looking across the stage. Needs no calibration; set it by hand. A value outside one turn is stored as the same bearing within it (`-90` becomes `270`).
- **Camera heading** – stick up moves away from the camera, stick right to the camera's right, both along the floor, like a third-person game. Worked out from the calibrated camera, so it follows a recalibration. On the default camera it is the same as Stage axes.
- **Screen** – stick up moves the marker ball up the picture, wherever it is, at its current height. Closest to "the marker moves as I see it", and the most different from Camera heading near the picture edges. Where the picture gives no answer (the ball is off the picture, behind the camera, or at the horizon) that move uses Camera heading.

**While the stick is held** each controller keeps the direction it started with until its stick (or keys, or puck) returns to centre. Recalibrating the camera or changing this setting never bends a move in progress.

The setting only picks the direction: a full push still moves at the marker's speed, and height is never affected – up is up in every frame.

Two things this does **not** change, because they already follow the picture:

- **Mouse control** – clicking and dragging a marker positions it where the cursor is, so it is correct from any camera angle by construction.
- **OSC** – position messages set an absolute stage coordinate rather than a direction of travel.

> The gamepad's own **Stick Y axis** (Input → Gamepad) and the 3D Mouse's per-axis **Direction** are device feel and apply on top of the control frame.

**Save** – write the current values to this station's configuration. Settings apply immediately but are not stored until you save.
