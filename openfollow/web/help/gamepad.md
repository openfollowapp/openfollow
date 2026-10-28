# Gamepad Input

Configure how a connected USB gamepad drives markers: stick sensitivity and response, button assignments, and how to teach OpenFollow your controller's physical layout.

## General

- **Gamepad** – master toggle for gamepad input. Turn off to ignore the controller while keeping its configuration intact.
- **Stick Y axis** – the forward/back sense of the movement stick, and nothing else. **Push up to move down the screen** is flight-sim style. Left/right and height are unaffected: height comes from the triggers, not the stick.

    > This is a personal stick-feel preference, separate from **Control frame** under Markers & Zones → Marker Movement, which sets which way every input device steers relative to the camera. Set the frame there, and the stick's feel here.
- **Axis Deadzone (0–1)** – the fraction of stick travel treated as zero. Default `0.15`. Raise it if the marker creeps when the stick is released; lower it for tighter response on a well-centred stick.
- **Response Curve** – how stick deflection translates to marker velocity:
  - `Linear` – velocity scales proportionally with deflection.
  - `Logarithmic` – finer movement at small deflections, faster at large ones. The default and usually best for live tracking.
  - `Quadratic` – like Logarithmic but with a steeper ramp-up.
  - `S-Law` – slow at both ends, fast through the middle; suits very dynamic shows.
- **Button Names** – the pad family whose button names the station shows: on the Operator Screen (its help, key hints and the Button Detection Wizard), in this section's selectors, detection table and notes, in the controller-button trigger of OSC Transmitters, and in the on-device footer. `Standard`, the default, shows the generic names (A, B, Back, Start, Left Bumper, shortened to LB on the Operator Screen); `Xbox`, `PlayStation` and `Nintendo` show what those pads print (see Button Names below). Only the names change: every binding stays on its button. Like the Button Detection Map, it describes this station's pads: a settings file import restores it, and a push from another station leaves it as it is.

## Button Detection Map

Use this panel when your controller's layout doesn't match the standard Xbox reference. The wizard runs a guided sequence on the Operator Screen, prompting you to press each button in turn and recording the raw index behind every label.

- **Start Button Detection Wizard** – launches the wizard on the Operator Screen. A "Wizard running on app display" notice appears here while it's active; the table refreshes when it finishes, showing the detected controller as *Mapped with …*. Ignored while the Operator Screen is showing a menu or other modal – close it on the device first.
- **Cancel wizard** – stops a running wizard from the web UI. Use this if the wizard has taken exclusive input on the Operator Screen and you can't reach the device keyboard.

The detection table shows each physical button (the face buttons, bumpers, Back, Start and D-Pad directions, named by **Button Names**), the logical name OpenFollow assigned it, and the raw hardware ID. The triggers' state (normal or swapped) is shown beneath the table, under their names in the chosen **Button Names** family (LT / RT, L2 / R2 or ZL / ZR).

> Re-run the wizard if you swap to a different model or change the controller's mode; another pad of the same model shares the mapping. A pad of another model shows **Button map is for another model** on its Controller Slots row, and the diagnostics bundle reports the GUID mismatch under Gamepad controllers.

## Button Names

The same buttons carry different names on different pads. **Button Names** picks the family whose names the station shows; this table lists every family. Symbols stand for the PlayStation face buttons and the Xbox View and Menu buttons, except in drop-down lists, which show the word. The D-Pad is named the same in every family.

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

The Nintendo names assume the pad reports its face buttons by their printed letters, which is how such pads report them unless reconfigured.

## Button Mapping

Assign functions to physical buttons. Every selector offers the same set of detected buttons; set a field to `–` to leave that action unbound. **Reset to Defaults** restores the default assignment for all buttons at once, without affecting detection results.

Each button drives one action. Choosing a button another action already uses moves it: the field you changed says in blue where it came from, and the field it left empties and turns red, since that action does nothing until it gets a button again. Once the button moves on, the red field only names the button it lost. The notes clear on **Save**.

Move X/Y and the marker fader stick never share a stick. A fader stick on the stick Move X/Y uses is an error that holds **Save**; moving Move X/Y onto the fader's stick empties the fader field, in red.

### Normal Mode

Controls active during show operation.

- **Reset Marker** – snaps the active marker to its configured default position. Default: `X`.
- **Toggle Help** – shows or hides the help overlay on the Operator Screen. Default: `Y`.
- **Settings Menu** – opens the on-screen Settings menu; pressed in any menu, it closes them all. Default: `Back`.
- **Move X/Y** – which stick drives the marker's horizontal position: `Left Stick` or `Right Stick`. Default: `Left Stick`. The deadzone and response curve above apply to the chosen stick.
- **Marker fader stick** – which stick Y axis continuously drives the fader value of the marker this controller currently controls: `Left Stick Y`, `Right Stick Y`, or `– (unused)`. The same deadzone and response curve apply. See Hardware Inputs → Marker Faders to send this value via OSC.
- **Marker fader speed (s)** – seconds a full stick deflection takes to sweep the fader end to end. Range 0.05–60 s. Default: `1.0`. Lower values give faster response; higher values give finer resolution over slow sweeps.
- **Speed −** / **Speed +** – step tracking speed down or up through the configured range. Defaults: `LB` / `RB`.
- **Move Z−** / **Move Z+** – lower or raise the marker's height. Defaults: `LT` / `RT`.
- **Next Marker** / **Prev Marker** – cycle which marker this controller drives. Defaults: `D-Pad Right` / `D-Pad Left`. These are suppressed automatically when more than one controller is connected – gamepads and 3D mice share one numbering – since each controller is pinned to its own marker by the USB socket it is plugged into (see Controller Slots).
- **Clear Messages** – clears every operator-message card. Default: unbound.
- **Toggle Zone Overlay** – shows or hides the zone overlay on the Operator Screen. Default: unbound.
- **Toggle Paths** *(experimental)* – shows or hides paths on the Operator Screen. Default: unbound.
- **Pathfinder** *(experimental)* – slides the marker this controller drives onto the nearest active path, where it stays until pressed again or moved past an end of the path that releases it; while a path is recording, adds the marker's position to it instead. Default: unbound.

### Menu Navigation

Button assignments shared by the on-screen Settings menu and source / interface selection. They are only read while a menu is open, so they may share a button with a Normal Mode action. D-Pad Up and Down always move the menu highlight, so Confirm and Cancel don't offer them. While a menu is open, the Operator Screen lists every menu key and button in its top-left corner, with this station's assignments.

- **Confirm** – accepts the highlighted option. Default: `A`.
- **Cancel** – dismisses the menu without applying changes. Default: `B`.

## Saving

- **Save** – writes all gamepad settings to the station's configuration. Changes take effect immediately but are not persisted until you save.
