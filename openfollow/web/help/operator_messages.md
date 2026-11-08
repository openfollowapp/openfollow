# Operator Messages

A station can show short text messages on the **Operator Screen**, typically the operator's next cue, pushed in over OSC by a console or show-control system. **Listen for OSC messages** must be ticked under **OSC Input** for any message to arrive.

## Settings

- **Operator messages** – master toggle. When off, incoming messages are ignored. Default: off.
- **Placement** – the message cards at **Bottom-center** or **Top-center**.
- **Max visible** – cards shown at once, 1–20. Default `5`. The rest collapse into a "+N more" line.
- **Routing** – with **Only the marker's station** (default), a message with a `markerId` of 1 or higher shows only on the station controlling that marker. **Every station** ignores `markerId`. Broadcast (`markerId` 0) always reaches every station.
- **Scale** – enlarges the cards and their text: `1×` to `2×`. Default `1×`. Long lines wrap at 60% of the screen width.

## Sending a message

```
/message  <message:s>  <info:s>  <markerId:i>  <seconds:f>
```

- **message** – the main line of text.
- **info** – an optional second line, such as a cue number. Default empty.
- **markerId** – routing target. `0` is broadcast, shown on every station without a title bar. `1` or higher shows only on the station controlling that marker, with a title bar in the marker's colour and its name. Default `0`.
- **seconds** – auto-dismiss after this many seconds. `0` keeps the card until it is cleared. Default `0`.

Trailing arguments are optional and fill in left to right. A numeric argument that can't be read drops the whole message.

## Clearing

```
/message/clear            clears all messages
/message/clear  <id>      clears messages for marker <id>
```

A key or gamepad button bound under the **Input** tab also clears the messages on this station.

Messages arrive over the receiver set in **OSC Input**.
