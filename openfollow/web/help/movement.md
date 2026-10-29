# Marker Movement

Speed limits, the home position for a reset, and the direction relative controllers steer in. Speeds apply to every input method.

## Speed

In metres per second, or feet per second on a station set to imperial units.

- **Min Speed** – the slowest the marker can travel; the bottom of the range the Speed controls step through. Default `0.1` m/s.
- **Max Speed** – the fastest the marker can travel; not lower than Min Speed. Default `3.0` m/s.
- **Default Speed** – the speed of every marker at start, and of any marker whose speed was not adjusted. Must lie within Min–Max. Default `2.0` m/s.

Each marker keeps its own live speed during a show. Changing Min or Max Speed takes effect the next time an operator adjusts speed.

## Default Position (on reset)

Where a marker jumps when the operator presses Reset. Relative to the **Reference Point**, the (0, 0, 0) of your show.

- **Default X** – stage left is positive, stage right negative. `0` is the centre line.
- **Default Y** – upstage is positive, downstage negative.
- **Default Z** – the marker's Z in world coordinates. The floor sits at the grid's **Z Offset**, so `0` is the floor only when that offset is `0`.

X and Y default to `0.0`, Z to `1.6`.

## Control frame

**Control frame** – which way the gamepad, keyboard and 3D Mouse move a marker:

- **Stage axes** (default) – stick up moves upstage, stick right moves stage left, whatever the camera sees.
- **Fixed heading** – stage axes turned to the bearing in **Heading (°)**, measured like the camera's **Yaw**: `0` looks upstage, `180` looks back at the audience, `90` / `270` look across the stage. Set by hand; needs no calibration.
- **Camera heading** – stick up moves away from the camera, stick right to its right, along the floor. Follows the calibrated camera, so it follows a recalibration.
- **Screen** – stick up moves the marker up the picture, at its current height. Where the picture gives no answer (marker off the picture, behind the camera, at the horizon, or higher than the camera), that move uses Camera heading.

Each controller keeps the direction it started with until its stick returns to centre. The setting picks only the direction: speed and height are not affected. Mouse control and OSC position messages are not affected either.

**Save** – write the values to this station's configuration.
