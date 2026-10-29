# OSC Transmitters

Outbound OSC messages for consoles, media servers and show control. Each transmitter has four tabs: **Basics**, **Trigger**, **Settings**, **Diagnostics**.

## Basics

- **Transmitter** – master toggle. Red and forced off while a placeholder's dependency is missing.
- **Name** – a label.
- **Default markers** – the markers that bare placeholders (`[x]`, `[markerid]`) resolve against. Comma-separated (`1, 3, 7`). Each entry is a marker ID, `cN` (the marker controller N is driving), or `all`. An ID this station doesn't control is skipped and shown with a red dot.
- **Default fader** – the fader bare `[fader]` placeholders read.
- **Destination** – the connection to send to, from **OSC Destinations**. With none selected, nothing is sent.

## Trigger

- **Stream** – sends at **Rate (Hz)**. **Send only on change** waits until the Default marker has moved **Min change (m)**.
- **Hotkey** – **Key**, **Modifiers**, **Edge** (Press or Release).
- **Controller button** – **Button** and **Edge**.
- **MIDI message** – **Type**, **Patch**, **Channel**, **Number**, **Value**; blank matches anything. **Capture** fills them from the next MIDI message.
- **Fader on change** – fires when the **Fader** changes, at most **Rate (Hz)** times per second.

An orange outline means a keyboard or gamepad action uses the same key or button: one press does both.

## Settings

**OSC message** – the address, then the arguments. Integers send as `i`, decimals as `f`, anything else as a string; quote arguments with spaces. Click a placeholder pill to edit it.

A placeholder is `[source:N.transform]`. `:N` picks a marker (or fader number), `:cN` the marker controller N drives; without either, the Default marker or fader is used.

| Source | Value |
|---|---|
| `[x]` `[y]` `[z]` | Marker position in metres |
| `[markerid]` | Marker ID |
| `[fader]` `[markerfader]` | Virtual fader / the marker's own fader, 0.0–1.0 |
| `[value]` `[velocity]` `[note]` | MIDI value, velocity, note |

| Transform | Effect |
|---|---|
| `.inv` | Invert a position or fader |
| `.frac` | Position as grid fraction (±1); `[z.frac]` needs **Grid → Maximum Height** |
| `.pct` | Fader as 0–100 |
| `.int:min-max` / `.scale:min-max` | Fader as an integer / decimal range |

## Diagnostics

A red dot on a collapsed row means it can never fire; the reason is next to the name.

- **Live status** – connection state, packets per second, last error.
- **Preview** – the message as it would be sent now, without sending.
- **Send test packet** – sends one packet, even when disabled.

## Managing transmitters

- **Template** and **+ New transmitter** – start from a template or an empty one.
- **Save** / **Discard** – changes apply live but are lost on reload unless saved.
- **Save as template…** / **Manage templates…** – keep, apply, export or import templates.
- **Duplicate** / **Delete** – copy below, or remove.
