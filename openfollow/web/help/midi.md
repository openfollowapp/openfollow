# MIDI

MIDI device patches and virtual faders. Patches and fader values feed OSC Transmitters rows through MIDI message triggers and fader placeholders.

## MIDI Patches

A patch is a numbered slot you assign a connected MIDI device to. The patch ID, not the port name or alias, is what everything else references. IDs stay fixed across reboots and device swaps. Click **Add new MIDI Patch**, then fill in the row:

- **ID** – read-only permanent number. `0` is reserved to mean "any patch".
- **Alias** – optional name (up to 64 characters), such as `FOH`.
- **Device** – the MIDI input port bound to this patch. An unplugged device shows as `(not connected)`; the binding is kept for when it reconnects. On Linux, a device with a USB serial number shows it after the port name, and the patch follows that unit, so two controllers of the same model each keep their own patch.

Each row has its own **Save** and **Delete**.

## Virtual Faders

Eight faders (0.00 – 1.00), numbered 1 to 8. Each value feeds OSC Transmitters rows via fader placeholders. The bus is fixed at eight.

The strips show the values as they were when the page opened. **Show live** follows them for 10 seconds, then stops by itself; **Stop live** ends it sooner.

A strip marked **not picked up** is a MIDI fader whose physical control has not yet met the fader's value. Moving the physical fader changes nothing until it crosses that value.

Click a strip to edit it in the panel below.

- **Display name** – label on the Operator Screen (when enabled) and in trigger forms. Up to 32 characters.
- **Default value** – the value at startup.
- **Colour** – tints the strip in the web interface.
- **Operator Screen** – show this fader on the Operator Screen.
- **Source Type** – `No source` holds the fader at its default value; `MIDI` drives it from an incoming MIDI message.

With `MIDI`:

- **Patch** – which patch to listen on; `(any patch)` accepts all.
- **Message Type** – Control Change, Key Pressure or Channel Pressure.
- **Channel** – `0` matches any channel; `1`–`16` one channel.
- **CC / Note number** – the controller or note number (0 – 127). Not used for Channel Pressure.
- **Learn** – click, then move the physical fader or knob within 10 seconds. The next message fills in Patch, Message Type, Channel and CC / Note number.

**Save fader** applies changes to the selected fader.

## Marker Faders

Read-only. One strip per controlled marker, driven by the gamepad that controls it and fed to OSC Transmitters via the `[markerfader]` placeholder. The gamepad axis and travel speed are set on the Gamepad page.
