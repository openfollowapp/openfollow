# Gamepad Input

## General

- **Gamepad** – master toggle. Off ignores the controller and keeps its configuration.
- **Stick Y axis** – forward/back sense of the movement stick. **Push up to move down the screen** is flight-sim style.
- **Axis Deadzone (0–1)** – stick travel treated as zero. Default `0.15`. Raise it if the marker creeps at rest.
- **Response Curve** – how stick travel maps to speed. `Logarithmic` (default) is finer at small deflections.
- **Button Names** – the pad family whose button names the station shows. Only the names change; every binding stays on its button.

## Button Names

| Standard | Xbox | PlayStation | Nintendo |
|---|---|---|---|
| A | A | ![](button:cross) Cross | A |
| B | B | ![](button:circle) Circle | B |
| X | X | ![](button:square) Square | X |
| Y | Y | ![](button:triangle) Triangle | Y |
| Left Bumper (LB) | LB | L1 | L |
| Right Bumper (RB) | RB | R1 | R |
| Left Trigger (LT) | LT | L2 | ZL |
| Right Trigger (RT) | RT | R2 | ZR |
| Back | ![](button:view) View | Create | Minus (-) |
| Start | ![](button:menu) Menu | Options | Plus (+) |

## Button Detection Map

For a pad whose layout does not match the Xbox reference: **Start Button Detection Wizard** asks you on the Operator Screen to press each button. **Button map is for another model** on a Controller Slots row means this pad needs it run again.

## Button Mapping

`–` leaves an action unbound. **Reset to Defaults** restores all assignments.

Each button drives one action. Choosing a button another action uses moves it: the field you changed says in blue where it came from, and the field it left turns red and does nothing until it gets a button. Move X/Y and the marker fader stick cannot share a stick; that holds **Save**.

### Normal Mode

- **Reset Marker** – active marker to its default position. Default `X`.
- **Toggle Help** – Operator Screen help overlay. Default `Y`.
- **Settings Menu** – opens the Settings menu; in any menu it closes them all. Default `Back`.
- **Move X/Y** – `Left Stick` or `Right Stick`. Default `Left Stick`.
- **Marker fader stick** – which stick Y axis drives the marker's fader value, or `– (unused)`.
- **Marker fader speed (s)** – full-deflection sweep time, 0.05–60. Default `1.0`.
- **Speed −** / **Speed +** – tracking speed down / up. Defaults `LB` / `RB`.
- **Move Z−** / **Move Z+** – lower / raise the marker. Defaults `LT` / `RT`.
- **Next Marker** / **Prev Marker** – cycle the marker this controller drives. Defaults `D-Pad Right` / `D-Pad Left`. Suppressed with more than one controller connected.
- **Clear Messages**, **Toggle Zone Overlay**, **Toggle Paths** *(experimental)*, **Pathfinder** *(experimental)* – unbound by default.

### Menu Navigation

Read only while a menu is open, so they may share a button with Normal Mode.

- **Confirm** – accepts the highlighted option. Default `A`.
- **Cancel** – closes the menu. Default `B`.
