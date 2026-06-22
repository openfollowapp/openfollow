# MIDI

USB MIDI device aliases and virtual fader sources for this station. Once configured, patches and fader values feed OSC Transmitters rows via MIDI message triggers and fader placeholders.

## MIDI Patches

A patch is a stable, numbered slot you assign a connected MIDI device to – the same idea as a QLab MIDI patch. The patch ID, not the port name or alias, is the key everything else references (OSC Transmitters triggers, virtual fader sources). IDs are assigned sequentially and stay fixed across reboots and device swaps.

Click **Add new MIDI Patch** to create a slot, then fill in its row:

- **ID** – read-only permanent integer key. Referenced by virtual fader sources and OSC Transmitters trigger forms.
- **Alias** – optional friendly name (up to 64 characters), such as `FOH` or `X-Touch`. Shown alongside the ID in patch dropdowns.
- **Device** – the MIDI input port bound to this patch. The dropdown lists currently connected ports; an unplugged device shows as `(not connected)`, and saving preserves the binding for when it reconnects.

Each row has its own **Save** and **Delete** buttons.

> The patch ID `0` is reserved as a wildcard meaning "any patch" – real patches always start at 1.

## Virtual Faders

Eight normalised faders (0.00 – 1.00), addressed by position on the bus (1 through 8). Each fader's live value shows in its strip and feeds OSC Transmitters rows via fader placeholders. The bus is fixed at eight; you cannot add or remove entries.

Click a strip to open its settings in the detail panel below.

### Identity

- **Display name** – label shown on the Operator Screen (when enabled) and in trigger forms referencing this fader. Up to 32 characters; defaults to `Fader 1`, `Fader 2`, etc.
- **Default value** – the fader's value at startup (0.00 to 1.00).
- **Colour** – swatch that tints the fader strip in the web interface.
- **Show on Operator Screen** – when checked, this fader appears on the Operator Screen.

### Source

- **Source Type** – `No source` holds the fader at its default value; `MIDI` drives it from an incoming MIDI message.

When **Source Type** is `MIDI`, these additional fields appear:

- **Patch** – which MIDI patch to listen on. `(any patch)` accepts messages from all patches.
- **Message Type** – the MIDI message that moves this fader: Control Change, Key Pressure, or Channel Pressure.
- **Channel** – MIDI channel to match. `0` matches any channel; `1`–`16` targets one specific channel.
- **CC / Note number** – the controller or note number to listen to (0 – 127). Not used for Channel Pressure.
- **Learn** – click, then move the physical fader or turn the knob within 10 seconds. The next incoming message fills in Patch, Message Type, Channel, and CC / Note number automatically.

Click **Save fader** to apply changes to the selected fader.

## Marker Faders

Read-only. One strip per controlled marker, driven by whichever gamepad currently controls it. Provisioned automatically from the Controlled Markers list – nothing to configure here.

A marker fader's live value feeds OSC Transmitters rows via the `[markerfader]` placeholder. Which gamepad axis feeds the marker faders, and how fast they travel, is set on the Gamepad page.
