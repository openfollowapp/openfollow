# Marker Movement

Speed limits, the default operating speed, and the home position markers snap back to on a reset. These affect every input method – gamepad, keyboard, mouse, and OSC – since all draw from the same speed range.

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
- **Default Z** – height above the stage floor. A typical standing performer head height is around `1.6`–`1.8`. `0` places the marker on the floor.

X and Y default to `0.0` (the Reference Point itself); Z defaults to `1.6` – roughly performer head height above the floor.

## Control direction

**Invert control direction** – reverses left/right *and* forward/back for the gamepad, keyboard, and 3D Mouse. Off by default.

Marker movement is driven in stage axes, so if your camera sits **upstage looking downstage**, the picture is rotated 180° from those axes and the marker travels the wrong way on screen – push right, it goes left. Turning this on flips both axes so the marker follows the operator's view again.

Both axes always flip together, because that is what a 180° camera position does. There is no single-axis option: inverting only one would mirror the stage, which matches no real camera placement. A camera off to one side needs a different fix (a rotation, not an inversion) and is not covered by this setting.

Height is never affected – up is up from any camera position.

Two things this does **not** change, because they already follow the picture:

- **Mouse control** – clicking and dragging a marker positions it where the cursor is, so it is correct from any camera angle by construction.
- **OSC** – position messages set an absolute stage coordinate rather than a direction of travel.

> The gamepad's own **Invert Y** (Input → Gamepad) is a separate stick-feel preference and still applies on top of this. With both switched on they cancel out on the gamepad's Y axis, while the keyboard and 3D Mouse still flip – so use one or the other, not both.

**Save** – write the current values to this station's configuration. Settings apply immediately but are not stored until you save.
