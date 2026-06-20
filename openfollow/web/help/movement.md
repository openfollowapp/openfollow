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

**Save** – write the current values to this station's configuration. Settings apply immediately but are not stored until you save.
