# Gamepad Input

Configure how a connected USB gamepad drives markers: stick sensitivity and response, button assignments, and how to teach OpenFollow your controller's physical layout.

## General

- **Enabled** – master toggle for gamepad input. Turn off to ignore the controller while keeping its configuration intact.
- **Invert Y/Z Mapping** – flips the forward/back sense of the movement stick. Enable for flight-sim style, where pushing up moves the marker downstage.
- **Axis Deadzone (0–1)** – the fraction of stick travel treated as zero. Default `0.15`. Raise it if the marker creeps when the stick is released; lower it for tighter response on a well-centred stick.
- **Response Curve** – how stick deflection translates to marker velocity:
  - `Linear` – velocity scales proportionally with deflection.
  - `Logarithmic` – finer movement at small deflections, faster at large ones. The default and usually best for live tracking.
  - `Quadratic` – like Logarithmic but with a steeper ramp-up.
  - `S-Law` – slow at both ends, fast through the middle; suits very dynamic shows.

## Button Detection Map

Use this panel when your controller's layout doesn't match the standard Xbox reference. The wizard runs a guided sequence on the Operator Screen, prompting you to press each button in turn and recording the raw index behind every label.

- **Start Button Detection Wizard** – launches the wizard on the Operator Screen. A "Wizard running on app display" notice appears here while it's active; the table refreshes when it finishes, showing the detected controller as *Mapped with …*. Ignored while the Operator Screen is showing a menu or other modal – close it on the device first.
- **Cancel wizard** – stops a running wizard from the web UI. Use this if the wizard has taken exclusive input on the Operator Screen and you can't reach the device keyboard.

The detection table shows each physical button (A, B, X, Y, LB, RB, Back, Start, D-Pad directions), the logical name OpenFollow assigned it, and the raw hardware ID. LT and RT trigger state (normal or swapped) is shown beneath the table.

> Re-run the wizard if you swap to a different physical unit or change the controller's mode. A GUID mismatch is reported in the diagnostics bundle under Gamepad controllers.

## Button Mapping

Assign functions to physical buttons. Every selector offers the same set of detected buttons; set a field to `–` to leave that action unbound. **Reset to Defaults** restores the factory assignment for all buttons at once, without affecting detection results or stick settings.

### Normal Mode

Controls active during show operation.

- **Reset Marker** – snaps the active marker to its configured default position. Default: `X`.
- **Toggle Help** – shows or hides the help overlay on the Operator Screen. Default: `Y`.
- **Toggle Zone Overlay** – shows or hides the zone overlay on the Operator Screen. Default: `B`.
- **Settings Menu** – opens the on-screen Settings menu. Default: `Back`.
- **Move X/Y** – which stick drives the marker's horizontal position: `Left Stick` or `Right Stick`. Default: `Left Stick`. The deadzone and response curve above apply to the chosen stick.
- **Marker fader stick** – which stick Y axis continuously drives the fader value of the marker this controller currently controls: `Left Stick Y`, `Right Stick Y`, or `– (unused)`. The same deadzone and response curve apply. See Hardware Inputs → Marker Faders to send this value via OSC.
- **Marker fader speed (s)** – seconds a full stick deflection takes to sweep the fader end to end. Range 0.05–60 s. Default: `1.0`. Lower values give faster response; higher values give finer resolution over slow sweeps.
- **Speed −** / **Speed +** – step tracking speed down or up through the configured range. Defaults: `LB` / `RB`.
- **Move Z−** / **Move Z+** – lower or raise the marker's height. Defaults: `LT` / `RT`.
- **Next Marker** / **Prev Marker** – cycle which marker this controller drives. Defaults: `D-Pad Right` / `D-Pad Left`. These are suppressed automatically when more than one controller is connected – gamepads and 3D mice share one numbering – since each controller is pinned to its own marker by plug order.

### Menu Navigation

Button assignments shared by the on-screen Settings menu and source / interface selection.

- **Confirm** – accepts the highlighted option. Default: `A`.
- **Cancel** – dismisses the menu without applying changes. Default: `B`.

## Saving

- **Save** – writes all gamepad settings to the station's configuration. Changes take effect immediately but are not persisted until you save.
