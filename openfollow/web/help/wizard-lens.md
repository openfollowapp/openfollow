# Lens

A wide lens makes straight lines look curved. The Lens step measures how much, so the overlay on the Operator Screen curves in the same way.

## The camera's current calibration

When the camera already has a lens calibration, the Lens step first shows its values and asks what to do with it. Until you choose, the rest of the step and **Next** are hidden. **Save lens calibration** under the choices saves the camera's current calibration on this station under a name, as a backup before you change it. It goes once you choose. **Keep the current calibration** uses it as it is. It is the right choice when the camera, the lens and the zoom have not changed, and the picture and its controls are then hidden. **Clear it and measure the lens again** sets the lens to no correction and opens the picture for new lines. **Load another lens calibration** opens the same list as **Load lens calibration**. The loaded calibration is used as it is, and only its notices stay on the page. You can choose again until you leave the step: **Keep** puts the camera's own calibration back, and lines drawn after **Clear** come back with the next **Clear**, which measures them again from no correction.

## Suggestions

With the experimental **Lens line suggestions** feature on (General tab, Features), the station finds edges in the snapshot and shows them as dashed suggested lines. Each suggested line has a bubble that points at it. The check mark uses the line, the cross removes it. Only the bubble does: a click on the dashed line itself starts or ends a line of your own, as anywhere else on the picture. When the pointer is on a bubble, its line is highlighted. Use only lines that are straight on the stage. A curtain, a cloth or a cable is curved. Two edges that only look joined are two separate lines. A suggested line that you use becomes a normal line, the same as a line you draw yourself. **Refresh Image** takes a new snapshot and suggests the lines again.

## Lines

Each line you draw must be straight in reality. The calculation makes all lines straight at the same time. Lines near the edges and corners of the picture help the most.

When you click or drag a point, it stays selected and the loupe stays on it. The arrow keys move it by one pixel. With **Shift**, they move it by ten. **Tab** also selects a point: each line is one stop, and **Tab** selects its first point. The first stop is the line you drew or changed last; from there **Tab** goes on through the following lines and then the ones before it. While a point is selected, **Tab** selects the next point of the same line and **Shift**+**Tab** the previous one, round and round: after the last point comes the first. **Enter** ends the selection; then **Tab** moves on to the next line, the suggestion bubbles when shown, and the buttons below the picture, and **Shift**+**Tab** moves back past the line. **Enter**, **Esc** or a click anywhere else ends the selection. After **Enter** or **Esc** the point keeps the focus, so **Tab** goes on from there. A middle point that found no edge starts switched off. Moving a middle point by hand, by dragging it or with the arrow keys, switches it on: a point placed by hand marks the edge. A click on a line selects the whole line. **Delete** or **Backspace** removes the selected line. A new line is selected at once, so the key also removes a line you just added.

## Rating

The rating has five steps. It shows how exactly the lines measure the lens. When the lines stay near the middle of the picture, the edges are not measured. They are measured once lines reach out toward the corners on two sides of the picture; one long line can do that. Without that, a result that would be Good or Excellent is shown as Fair, and the result says that it might already be good in the middle. That can be enough when you only need the middle of the stage. A **Not usable** result is shown but not applied, and so is any result while a line does not fit the others. A line that does not fit glows in the warning colour, and its number is marked with **!**. **Corner correction** is the extra correction near the corners of the picture. Lines that reach toward the corners measure it. Without them, the result shows an estimate, marked **(estimated)**: what the other lines show of it, drawn toward 0 only as far as they leave it open, so it can still be large. The estimate is applied with Barrel / fisheye. **Lens centre** is the point that the lens bends the picture around. On many cameras it is a little off the centre of the picture. The Lens step does not measure it: type it under **User input**, or load a calibration that has one. Barrel / fisheye and Corner correction are measured around it.

## User input

The values can also be typed in. **Lens centre** is in pixels of the picture, measured from the centre of the picture. A negative value under **Right of the picture centre** is to the left of it, a negative value under **Above the picture centre** is below it.

## Load and save

**Load lens calibration** sets the lens from a calibration saved on this station, or from an exported `.ofsettings` file. Only the lens values of the file are used. Use it when the picture has no straight edge to draw a line on. The calibration must come from the same camera and lens at the same zoom. Loading removes the lines on the picture. You can still change the values under **User input** afterwards.

**Save lens calibration** saves the current values on this station under a name, so you can load them later for this camera and lens. The size of the snapshot is saved with them. The values are relative to the diagonal of the picture, so they also fit another resolution of the same picture shape, but not another shape or zoom. The list says when a calibration was measured on a picture of another shape. If the name already exists, the saved calibration is replaced.

## Full screen

The button in the top right corner of the picture shows the picture on the whole screen. A bar above it shows the next thing to do, a warning when a line does not fit, **Delete line**, **?** (how to use full screen, with the keyboard shortcuts) and **Exit full screen**. Everything works the same as on the page. **Esc** ends full screen. It also cancels a selection, an open menu or a line you have started.
