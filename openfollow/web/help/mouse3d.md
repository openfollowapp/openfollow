# 3D Mouse Input

Steer the selected marker with a 6DOF 3D Mouse – a spring-centred puck. Off by default; turn it on with **Steer markers with a 3D Mouse**.

It shares one numbering with gamepads, ordered by USB socket; its marker carries a controller badge (`C1`, `C2`, …).

- **On its own** it drives the selected marker, like the keyboard and mouse. The marker-cycle buttons change which marker it steers.
- **Alongside other controllers** it is pinned to its own marker; the marker-cycle buttons go quiet.

## Status

A box at the top appears only when something keeps a 3D Mouse from working, and says what to do.

## Axis mapping

Each of the six motions has its own row:

| | Motion | Default |
|---|---|---|
| ![Cap pushed left and right](/assets/help/mouse3d-pan-x.svg) | **Push left / right** | Move X |
| ![Cap pushed forward and back](/assets/help/mouse3d-pan-y.svg) | **Push forward / back** | Move Y |
| ![Cap pulled up and pushed down](/assets/help/mouse3d-lift.svg) | **Pull up / push down** | Move Z (height) |
| ![Cap tilted forward and back](/assets/help/mouse3d-pitch.svg) | **Tilt forward / back** | off |
| ![Cap twisted](/assets/help/mouse3d-yaw.svg) | **Twist / spin** | Speed |
| ![Cap tilted left and right](/assets/help/mouse3d-roll.svg) | **Tilt left / right** | off |

- **Target** – **x / y / z** moves the marker along that axis; **speed** ramps its move-speed while held; **fader** raises or lowers its fader (0–1); **none** ignores the axis.
- **Sensitivity** – per-axis multiplier, 0–10. `1` moves at the marker's configured move-speed at full deflection.
- **Deadzone** – per-axis dead band near centre, 0–1. Raise it if the marker drifts at rest.
- **Direction** – **Normal** or **Inverted**.

To follow the camera's view, use **Control frame** under Marker Movement instead of inverting axes.

**Response curve** – how deflection maps to speed, for all axes: **linear**, **logarithmic** (fine near centre), **quadratic** (finer still), **s-law** (smooth ease-in and ease-out).

## Buttons

Each action binds to a button **index**; blank unbinds it. **Detect** – click it, then press the button on the device.

An index another action already uses moves over; the field it left turns red, since that action does nothing until it gets a button.

Actions: **Reset marker**, **Next / Previous marker**, **Speed up / Speed down**, **Toggle help**, **Toggle zones**, and the experimental **Pathfinder** and **Toggle paths**.

**Save** – changes apply immediately, but are lost on restart unless you save.
