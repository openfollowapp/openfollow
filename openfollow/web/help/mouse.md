# Mouse Input

> Mouse Input is marked **Experimental**. It is off by default and not recommended as a primary input method during live shows.

Click-and-drag control for steering markers directly on the live video.

## Taking and releasing control

**Left-click a marker's ground circle** to take control of that marker, then move the mouse to steer it. Clicking the circle grabs the marker where it stands – it does not jump to the cursor. A click on empty stage does nothing, so a stray click can't fling a marker. Left-click a different marker's circle to switch control to it. **Right-click** releases control (movement stops until the next grab). If the ground circle is turned off in Marker settings, the small area around the marker's base stays clickable.

**Double-click resets marker** – when on, double-clicking a marker's ground circle snaps it back to the default position (the same as the reset key). Turn it off if you find yourself resetting markers by accident.

## Steering refinements

**Hysteresis (px)** – a deadband on the cursor, in screen pixels, so small hand-tremor doesn't make the marker wiggle. The marker only moves once the cursor travels past this many pixels from where it last settled. `0` applies every movement (direct control). Start around `3`–`6` if a resting hand makes the marker drift.

**Smoothing** – how heavily the marker glides toward the cursor. `1.0` is instant 1:1 control; lower values (e.g. `0.2`) ease the marker toward the cursor for a smoother, slightly laggier feel. Range `0.01`–`1.0`.

**Maximum Y+ (m)** – the farthest **upstage** (away from the audience, the +Y direction) a mouse target may go. Near the top of the picture the floor stretches toward the horizon, where a tiny cursor move maps to an enormous upstage distance; this caps that so the marker (and its light) can't shoot off into the far field. A move past the limit is ignored and the marker holds its last position. `0` means no upstage limit.

## Scroll wheel (height)

**Wheel controls Z** – when on, the scroll wheel raises and lowers the controlled marker's height. Turn it off to disable wheel-height entirely.

**Invert wheel** – flips the scroll direction. By default scroll **up raises**; invert makes scroll up lower.

**Step per tick (m)** – how far each wheel notch moves the marker's height. Default `0.1` m.

**Save** – store the current settings. Changes apply immediately but are lost on restart unless you save.
