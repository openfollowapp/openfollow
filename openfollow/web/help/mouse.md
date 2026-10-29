# Mouse Input

Click-and-drag control for steering markers directly on the live video. Off by default – turn it on with **Steer markers with the mouse**.

**One mouse per station.** All mice share a single system cursor, so only one mouse steers one marker at a time. To control several markers at once, use one gamepad per operator.

## Taking and releasing control

**Left-click a marker's ground circle** to take control of that marker, then move the mouse to steer it. The marker is grabbed where it stands and does not jump to the cursor. A click on empty stage does nothing. Left-click another marker's circle to switch. **Right-click** releases control. If the ground circle is turned off in Marker settings, the small area around the marker's base stays clickable.

**Double right-click** – when on, snaps the controlled marker back to the default position (like the reset key) and lets go of it.

The mouse never moves a marker along a path *(experimental)*: grabbing a locked marker takes it off the path where it stands.

## Steering

- **Hysteresis (px)** – a deadband on the cursor, in screen pixels, against hand tremor. The marker moves only once the cursor travels past this many pixels from where it last settled. `0` applies every movement.
- **Smoothing** – how heavily the marker glides toward the cursor. `0` is direct 1:1 control; higher values ease it for a smoother, laggier feel. Range `0`–`1`.
- **Maximum Y+ (m)** – the farthest upstage a mouse target may go. Near the top of the picture a tiny cursor move maps to a huge upstage distance; a move past the limit is ignored and the marker holds its last position. `0` means no limit.

## Scroll wheel (height)

- **Scroll wheel** – when on, the wheel raises and lowers the controlled marker's height.
- **Wheel direction** – whether scrolling up raises or lowers the marker. Default: **Scroll up raises the marker**.
- **Step per tick (m)** – how far each wheel click moves the height. Default `0.1` m. A trackpad or Magic Mouse sends many scroll events per swipe, so it moves the height much further than a wheel.

**Save** – store the settings. Changes apply immediately but are lost on restart unless saved.
