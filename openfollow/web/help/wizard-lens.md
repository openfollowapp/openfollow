# Lens

A wide lens makes straight lines look curved. This step measures how much, so the overlay curves the same way.

## The camera's current calibration

If the camera already has a lens calibration, the step first shows it. **Next** is hidden until you choose.

- **Keep the current calibration** – use it.
- **Clear it and measure the lens again** – no correction; the picture opens for new lines.
- **Load another lens calibration** – opens the **Load lens calibration** list.
- **Save lens calibration** – backs up the current calibration on this station under a name.

You can choose again until you leave the step.

## Lines

Each line you draw must be straight in reality. Lines near edges and corners help most.

A clicked point stays selected. The arrow keys move it by one pixel, with **Shift** by ten. **Tab** also selects a point: each line is one stop, and **Tab** selects its first point. While a point is selected, **Tab** selects the next point of the same line and **Shift**+**Tab** the previous one, round and round: after the last point comes the first. **Enter** ends the selection; then **Tab** moves on to the next line, and **Shift**+**Tab** moves back past the line. **Esc** or a click elsewhere also ends it. After **Enter** or **Esc** the point keeps the focus, so **Tab** goes on from there.

A middle point that found no edge starts switched off; moving it switches it on. Click a line to select it; **Delete** or **Backspace** removes it.

## Suggestions

With the experimental **Lens line suggestions** feature on (General tab, Features), edges show as dashed suggested lines. The check mark in a line's bubble uses it, the cross removes it. Use only lines that are straight on the stage; a curtain, cloth or cable is curved. **Refresh Image** suggests again.

## Rating

Five steps show how exactly the lines measure the lens. A **Not usable** result is shown but not applied, and neither is any result while a line does not fit the others. Such a line glows in the warning colour and its number is marked with **!**.

**Corner correction** is the extra correction near the corners. Without lines reaching toward the corners, the result is an estimate, marked **(estimated)**, and a Good or Excellent result is shown as Fair.

## User input

**Lens centre** is the point the lens bends the picture around, in pixels from the picture centre. The step does not measure it; type it here or load a calibration that has one. A negative **Right of the picture centre** is left of it, a negative **Above the picture centre** is below it.

## Load and save

**Load lens calibration** takes a calibration saved on this station, an exported `.ofsettings` file or an OpenCV calibration file (`.xml`). It must come from the same camera and lens at the same zoom. Loading removes the lines on the picture. A calibration of another picture shape is refused.

**Save lens calibration** stores the current values under a name; an existing name is replaced.
