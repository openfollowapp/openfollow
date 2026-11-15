# Touch Remote

> Experimental. Shown only with the **Touch remote** feature on (General tab, Features).

A setup tool for focusing lights: stand on a known spot with a tablet or phone and drive a marker onto yourself. **Don't keep it open during a show.**

## Settings

- **Touch remote** – allows browsers that can open this web UI to drive this station's markers. While off, the remote's button is hidden and the station refuses remote requests. Saving reloads the page. This setting is never exported, imported or sent to other stations. Default: off.

## Using the remote

Tap the round button in the bottom corner of the page to open the remote card.

- **Marker chips** – the markers this station controls. Tap one to give the remote that marker.
- **Stick** – push to move the marker; the further you push, the faster, up to the station's **Max Speed**. Let go and it stops. It always steers in stage directions, whatever the **Control frame**; the UPSTAGE / DOWNSTAGE labels show where it drives.
- **Facing FOH** (turning arrows) – turns the stick around for when you stand on stage facing the audience. This tablet remembers it.
- **Hand** – moves the card to the other corner. This tablet remembers it.
- **Z wheel** – drag up or down; each notch moves the marker's height one step (5 cm by default). Does nothing while the marker is on a path.
- **Reset** – sends the marker to its default position.
- **Pathfinder** – shown while paths run. Locks the marker onto the nearest active path, or lets it go. While a path is recording it reads **Record point** and adds the marker's position to that path.
- **X Y Z** – show where the marker is until you type; enter a position and tap **Glide**. The marker leaves any path and travels there at the station's max speed. Values are in the web UI's units, within 10 km of the origin.

## While the remote holds a marker

- No other input moves that marker: gamepads, keyboard, mice, 3D mice, OSC positions and path commands, and Fully Automatic tracking are ignored for it. Speed buttons and the marker fader still work. The Operator Screen shows a caution row naming it.
- Taking a marker while person detection is on switches detection off for the whole station; the card says so. You can switch it back on under Person Detection at any time.
- Another tablet taking the same marker takes it over; the first card shows that it lost it.
- Opening the card takes no marker; tapping a chip does. Closing the card gives the marker back.
- If the tablet stops talking to the station (Wi-Fi drop, screen lock), the marker stops within a quarter of a second and the remote lets go of it after 10 seconds. Tap the chip again when the tablet is back.
