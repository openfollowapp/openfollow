v0.5.0
## Highlights

**A Setup Wizard in four steps.** Video Source, Lens, Camera Calibration and Review. The lens is measured from lines that are straight on stage, or loaded from an OpenCV calibration file, and the camera is calculated from marks pinned on the picture: **Simple** uses the four corners of a measured rectangle, **Advanced** known and triangulated points.

**Simple and Advanced network mode.** **General → Network Interface Settings** runs everything over one interface in **Simple**, or gives each function its own interface in **Advanced**, with VLANs and adapter labels.

**Controllers keep their slot.** Gamepads and 3D Mice take their slot from the USB socket they are plugged into. **Input → Controller Slots** shows which drives which marker, with **Identify** and **Forget**, and **Button Names** shows Xbox, PlayStation or Nintendo names.

**Status & Diagnostics.** The Overview tab is one section: the stations on the network, then the video, this station and its network discovery, with power and controller warnings. The diagnostics bundle saves to a USB storage device.

**Settings backups.** The station backs up its settings before every update, import, restore and push. **General → Configuration Backup & Import** lists the backups, and **Restore…** brings one back.

**Zones on the camera picture.** The **Zone Editor**'s Camera view moves a zone's corners on the picture, as the Setup Wizard does. With the experimental **Zone heights** feature, a zone can sit above the stage and raise the markers inside it.

**And more:** cone markers, parking a marker so controllers skip it, the camera's latency measured from **Video Source**, and a **Features** table under **General → Station Settings → Advanced Settings**.

## Before the next show

**Network pins.** A station whose config pins a function to an interface starts in **Advanced**. Check the pins: USB Ethernet adapters are now named after their MAC (`enx…`), so a pin to one may have moved.

**Controller order.** Controllers take their slot by USB socket, so one may drive a different marker than before. Check **Input → Controller Slots**.

**Controlled markers are always shown.** A marker this station controls appears on the Operator Screen and counts in the trigger zones, even if it was hidden before.

**Keyboard layout.** The Operator Screen uses the keyboard layout set in the station's system instead of assuming a US keyboard. Choose another under **Input → Keyboard Input**.

**RTTrPM may start off.** The **Show experimental features** switch is now the **Features** table. On a station that did not show experimental features, **RTTrPM output** starts off there, even where **Send marker positions over RTTrPM** is ticked: turn it on if this station sends RTTrPM.

**Control frame.** **Invert control direction** is now **Control frame** under **Markers & Zones → Marker Movement**. A station that had it ticked keeps its inverted controls as **Fixed heading** 180°.

**The camera is set in the Setup Wizard only.** The Camera & Grid tab shows the camera and lens read-only, and Ctrl/Cmd+S on the Operator Screen no longer saves the camera. Run the **Setup Wizard** to change them; in Simple, check the grid's point of origin against the console's before saving.

**Pictures keep their shape.** A Media Gallery picture or USB Camera that is not 16:9 now plays with black bars instead of stretched. A station calibrated on such a picture needs the **Setup Wizard** again.

**The Arena picture is gone.** The Media Gallery no longer offers **Arena**; a station that played it plays **Stage**. Choose a picture under **Video Source** and run the **Setup Wizard** for it.

**Video over Full HD or 30 fps shows an error.** A source sending more is reported on the Operator Screen and in the web UI, and keeps playing, with lag possible. Set the camera or encoder to 1920×1080 at 30 fps or less.

**Zones without names.** Zones on the Operator Screen are drawn without their names. **Markers & Zones → Trigger Zones → Occupied zones** chooses whether a zone lights up while a marker or person is inside it; it is on, as before.
