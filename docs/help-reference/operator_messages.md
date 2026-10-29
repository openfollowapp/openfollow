# Operator Messages

A station can show short text messages on the **Operator Screen** – typically the operator's next cue – pushed in over OSC by a console or show-control system. They share the same receiver as **OSC Input**, so **Listen for OSC messages** must be ticked there for any message to arrive.

## Settings

- **Operator messages** – master toggle. When off, incoming operator messages are ignored and nothing is drawn on the Operator Screen. Default: off.
- **Placement** – where the stack of message cards appears: **Bottom-center** or **Top-center**.
- **Max visible** – how many cards show at once, 1–20. Default: `5`. Any cards beyond this collapse into a single "+N more" line so the overlay never overruns the screen.
- **Routing** – with **Only the marker's station** (default), a marker-keyed message (`markerId` ≥ 1) shows only on the station controlling that marker. **Every station** ignores `markerId`. Broadcast (`markerId` 0) always reaches every station either way.
- **Scale** – uniformly enlarges the message cards and their text: `1×`, `1.25×`, `1.5×`, `1.75×`, or `2×`. Default: `1×`. Card width is still capped at 60% of the screen, so very long lines wrap rather than overrun at the larger sizes.

## Sending a message

Send to the address with up to four positional args (OSC type tags `ssif`):

```
/message  <message:s>  <info:s>  <markerId:i>  <seconds:f>
```

- **message** – OSC **string** (`s`). The main line of text.
- **info** – OSC **string** (`s`). An optional second line (e.g. a cue number). Defaults to empty.
- **markerId** – OSC **int** (`i`). Routing target (see below). Defaults to `0`.
- **seconds** – OSC **float** (`f`). Auto-dismiss after this many seconds. `0` keeps the card until it is cleared. Defaults to `0`.

Trailing arguments are optional and fill in left to right; a numeric argument that can't be read drops the whole message. The numbers are read leniently – `markerId`/`seconds` may also arrive as a numeric string (`"3"`, `"8.0"`) or the other numeric type – but `i` / `f` are the clean types.

## Routing by marker

- **`0` – broadcast.** Shown on every station. Broadcast cards have no title bar.
- **`1` or higher – marker-routed.** Shown only on the station currently controlling that marker; every other station drops it. The card gets a solid title bar in the marker's colour with the marker name on it.

This lets one sender address "whoever is on Marker 3" without knowing which physical station that is. Set **Routing** to **Every station** to ignore this.

## Clearing

```
/message/clear            clears all messages
/message/clear  <id>      clears messages for marker <id>
```

You can also bind a key or gamepad button under the **Input** tab to clear the messages on this station.

## Transport

Messages travel over the OSC receiver configured in **OSC Input** – unicast straight to a station, subnet broadcast, or the **Multicast group** set there reach all stations at once.
