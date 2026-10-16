# Touch Remote

A setup tool for focusing lights: stand on a known spot with a tablet or phone and drive a marker onto yourself. **Don't keep it open during a show.**

## Settings

- **Touch remote** – allows browsers that can open this web UI to drive this station's markers. While it is off, the remote's button is hidden and the station refuses remote requests. Saving a change reloads the page so the button appears or goes. This setting belongs to this station: it is never exported, imported or sent to other stations. Default: off.

The stick response, dead zone and Z wheel step live in the `[touch_remote]` section of `config.toml` (`stick_curve`, `stick_dead_zone`, `z_step_m`, `z_notch_px`).

## Using the remote

Tap the round button in the bottom corner of the page to open the remote card.

- **Marker chips** – the markers this station controls. Tapping one gives the remote that marker.
- **Stick** – push to move the marker; the further you push, the faster it goes, up to the station's **Max Speed** (Markers & Zones › Marker Movement). Let go and it stops. It always steers in stage directions, whatever the station's **Control frame**, since you may not be watching the camera's picture; the UPSTAGE / DOWNSTAGE labels on the stick show where it drives.
- **Facing FOH** (the turning arrows) – turns the stick around for when you stand on stage facing the audience. This tablet remembers it.
- **Hand** – moves the card to the other corner so your thumb reaches everything. This tablet remembers it.
- **Z wheel** – drag up or down; every notch moves the marker's height one step (5 cm by default) along a grid of steps, so from a height between two steps the first notch reaches the next one. It does nothing while the marker is on a path, whose height the path sets.
- **Reset** – sends the marker to its default position.
- **Pathfinder** – shown while paths run (experimental features on). Locks the marker onto the nearest active path, or lets it go. While a path is recording, it reads **Record point** and adds the marker's position to that path.
- **X Y Z** – the fields show where the marker is until you type in one; type a position and tap **Glide**: the marker leaves any path and travels there at the station's max speed. Values are in the web UI's units, within 10 km of the origin.

## While the remote holds a marker

- No other input moves that marker: gamepads, keyboard, mice, 3D mice, OSC positions and path commands, and Fully Automatic tracking are ignored for it. A gamepad's or 3D mouse's speed buttons and marker fader still work. The Operator Screen shows a caution row naming it.
- Taking a marker while person detection is on switches detection off for the whole station; the card says so. Closing the remote leaves it off. You can switch it back on under Person Detection at any time, even while the remote holds a marker: only the next marker taken switches it off again.
- Another tablet taking the same marker takes it over; the first card shows that it lost the marker.
- Opening the card takes no marker; tapping a chip does. Closing the card gives the marker back.
- If the tablet stops talking to the station (Wi-Fi drop, screen lock), the marker stops within a quarter of a second, and after 10 seconds the remote lets go of it. When the tablet comes back, tap the chip again: another tablet may have the marker by then.
