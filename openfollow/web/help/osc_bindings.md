# OSC Transmitters

Configure outbound OSC messages for lighting consoles, media servers, audio engines, and show-control software.

Each transmitter collapses to a one-line summary (name, trigger type, destination). Expand it for four tabs: **Basics**, **Trigger**, **Settings**, **Diagnostics**.

## Basics

Identity, destination, and transport.

- **Enabled** – master toggle for this transmitter; disabled transmitters do not transmit. If a placeholder's dependency isn't satisfied (e.g. no Default marker for a bare `[x]`), the toggle turns red and the transmitter is forced off until resolved.
- **Name** – a label for your own reference. Appears in the Diagnostics tab and the Overview live counters.
- **Default marker** – marker ID that bare position placeholders (`[x]`, `[y]`, `[markerid]`, etc.) resolve against when no `:N` index is given. Leave blank if every placeholder names its marker explicitly; the default must be an existing marker (IDs start at 1).
- **Default fader** – virtual fader that bare fader placeholders (`[fader]`, including transform forms like `[fader.pct]`) resolve against. Leave as `(none)` if you use only explicit `[fader:N]` references or have no fader placeholders.
- **Destination** – the shared OSC connection (host, port, transport) this transmitter sends to, picked from the list you maintain under **OSC Destinations**. A transmitter with no destination selected sends nothing. Edit a destination's IP once and every transmitter and zone pointing at it repoints live – no per-row editing, no restart. The read-only line under the dropdown shows the destination's resolved `host:port`.

## Trigger

When the transmitter sends. Pick one **Trigger type** from the dropdown; the fields below update to match.

- **Stream** – sends continuously at the chosen **Rate (Hz)** (1, 5, 10, 20, 30, or 60 Hz). Set **Send** to:
  - *Send always* – fire every tick regardless of movement.
  - *Send only on change* – fire only when the transmitter's Default marker has moved at least **Min change (m)** along any axis since the last send. The gate always watches the Default marker, even if the body references other markers via `[x:N]`. Transmitters with no Default marker fire every tick.

- **Hotkey** – fires on a keyboard combination. Set the **Key**, any **Modifiers** (Ctrl, Shift, Alt, Cmd), and the **Edge** (Press or Release). Movement keys are reserved and cannot be assigned.

- **Controller button** – fires on a gamepad button edge. Set the **Button** (A, B, X, Y, bumpers, D-Pad, Start, Back, etc.) and the **Edge** (Press or Release). Multiple transmitters can share a button and fire independently.

- **MIDI message** – fires when an incoming MIDI event matches the pattern. All match fields default to "any" – leave one blank to match anything.
  - **Type** – Note On, Note Off, Control Change, Program Change, Key Pressure, or Channel Pressure.
  - **Patch** – the MIDI patch to match. `(any)` matches all. Configure patches under Input → MIDI.
  - **Channel** – 1–16, or `Any`.
  - **Number** – note / CC / program number (0–127), or blank for any.
  - **Value** – 0–127, or blank for any. Note On with value 0 is treated as Note Off per the MIDI spec.
  - **Capture** – click and play the control to auto-fill all fields from the next incoming MIDI message within a 10-second window.

- **Fader on change** – fires whenever a fader's value changes, throttled to the chosen **Rate (Hz)**, which caps how often a fast-moving fader generates wire traffic. Pick the **Fader** source from the combined dropdown, listing all eight virtual faders and any per-marker marker faders.

## Settings

The OSC message this transmitter sends – an address and arguments with optional placeholders.

**OSC message** – the full message in one field. The first whitespace-separated token is the OSC address; everything after is arguments. Recognised placeholders appear as labelled pills as you type; click a pill to edit it (e.g. add a `:N` index targeting a specific marker). Use the buttons below the field to insert a placeholder at the cursor.

Argument types are inferred from the literal: an integer (e.g. `12`) sends as `i`; a float (e.g. `1.0`) as `f`; anything else as a string `s`. Placeholders carry their own types (`[markerid]` → `i`; position and fader placeholders → `f`). Arguments containing spaces must be quoted: `"My Cue"` is one string argument, not two tokens.

**Placeholder grammar.** Every placeholder follows one shape:

```
[ source (:N) (.transform) (.transform) … ]
```

- **`source`** – the value to send (below).
- **`:N`** – optional index: a marker ID for position / `markerid` / `markerfader` sources, or a fader number for `fader`. Omit it to resolve against the transmitter's Default marker / Default fader. Event sources take no index. A `:cN` form is a *controller reference* (below).
- **`.transform`** – optional chain of filters, applied left to right.

**Sources:**

| Source | Value |
|---|---|
| `[x]` `[y]` `[z]` | Marker position in metres (X = stage left, Y = upstage, Z = up) |
| `[markerid]` | The resolved marker's ID as an integer |
| `[fader]` | Default virtual-fader value, normalised 0.0–1.0 |
| `[markerfader]` | The resolved marker's own fader value, 0.0–1.0 |
| `[value]` | Live MIDI event value (0–127). MIDI-message transmitters only. |
| `[velocity]` | Note velocity (0–127). Note On / Note Off transmitters only. |
| `[note]` | MIDI note number (0–127). Note On / Note Off transmitters only. |

**Index – target a specific marker or fader.** Append `:N` to override the transmitter default: `[x:2]` sends marker 2's X (works on `x` / `y` / `z` / `markerid` / `markerfader`), `[fader:3]` reads virtual fader 3. The transmitter sends from that target regardless of the Default marker / Default fader.

**Controller reference – `:cN`.** Append `:cN` (1-based: `c1` = first controller) to address *the marker that controller `N` is currently driving*, resolved live as the operator moves. Use it to tell a console which fixture an operator is following without hard-coding a marker ID: `[markerid:c1]` sends that marker's ID, `[markerfader:c1.int:0-100]` its fader as 0–100, `[x:c1]` / `[y:c1]` / `[z:c1]` its position. Valid only on the marker-keyed sources (not `[fader]`, not event sources); `c0` is not a controller and renders as literal text. With a single controller connected, `cN` follows the currently selected marker; with several, it's that controller's fixed marker. If the controller is disconnected or drives no marker, the transmitter skips that send (shown under Diagnostics) – it never depends on the transmitter's Default marker.

**Transforms – chain with `.`, applied left to right:**

| Transform | Effect | Allowed on |
|---|---|---|
| `.inv` | Invert – negate a position, or `1 − v` a 0.0–1.0 fader | positions, faders |
| `.frac` | Normalise to ±1 by the grid extent | positions |
| `.pct` | Multiply by 100 (0.0–1.0 → 0–100) | faders |
| `.int:min-max` | Scale to an **integer** range, inclusive (`min > max` inverts) | faders |
| `.scale:min-max` | Scale to a **float** range; bounds may be decimal and negative | faders |

Positions (`[x]` `[y]` `[z]`) accept `.inv` / `.frac`. Faders (`[fader]` `[markerfader]`) accept `.inv` / `.pct` / `.int` / `.scale`. `[markerid]` and the event sources take no transform. `[z.frac]` (fractional height) additionally requires **Grid → Maximum Height** to be set.

**Examples:**

| Placeholder | Sends |
|---|---|
| `[x.frac]` | X as a grid fraction, −1.0 … 1.0 |
| `[z.frac.inv]` | Inverted fractional height |
| `[fader.pct]` | Default fader as 0–100 |
| `[fader:3.int:0-255]` | Fader 3 scaled to a 0–255 integer |
| `[markerfader.scale:-60-12]` | Marker fader mapped to −60 … +12 (e.g. dB) |
| `[x:2]` | Marker 2's X position |
| `[markerid:c1]` | ID of the marker controller 1 is currently driving |
| `[markerfader:c1.int:0-100]` | Controller 1's marker's fader, as 0–100 |

> Placeholders whose dependencies are not yet met are shown in red. The transmitter won't enable until you set the required Default marker / Default fader, or rewrite the placeholders to use explicit references.

## Diagnostics

Read-only panels showing what the transmitter is doing right now.

- **Live status** – connection state (UDP ready, or TCP connected / connecting / backing off), packets per second, and the last error. Click the refresh button to update.
- **Preview** – renders the message that would be sent right now from current marker data, without putting anything on the wire. Click **Refresh** to re-render.
- **Test send** – click **Send test packet** to force one packet to the destination. Works even if the transmitter is disabled, so you can verify destination and message before enabling. The result appears immediately.

## Adding and managing transmitters

**Template** dropdown + **+ New transmitter** – choose a template and click the button to add a transmitter pre-filled with its address and arguments. Leave the dropdown on *empty* to start with a blank transmitter. Drag the ⋮⋮ handle on the left of a collapsed transmitter to reorder – order is cosmetic, transmitters evaluate independently.

## Saving & sharing

- **Save** – write the transmitter's current settings to disk. Changes apply immediately but are lost on reload unless saved.
- **Discard** – revert the editor to the last saved state. Requires confirmation. Other open transmitters are unaffected.
- **Save as template…** – save the transmitter's name and message as a reusable template. It appears in the **Template** dropdown under *Custom Templates*.
- **Duplicate** – copy the transmitter and insert the duplicate immediately below. Useful for a near-identical transmitter for a second marker.
- **Delete** – permanently remove the transmitter. Requires confirmation.
