# Lens

A wide lens makes straight lines look curved. The Lens step measures how much, so the overlay on the Operator Screen curves in the same way.

## Suggestions

The station finds edges in the snapshot and shows them as dashed suggested lines. Each suggested line has a bubble that points at it. The check mark uses the line, the cross removes it. When the pointer is on a bubble, its line is highlighted. Use only lines that are straight on the stage. A curtain, a cloth or a cable is curved. Two edges that only look joined are two separate lines. A suggested line that you use becomes a normal line, the same as a line you draw yourself. **Refresh Image** takes a new snapshot and suggests the lines again.

## Lines

Each line you draw must be straight in reality. The calculation makes all lines straight at the same time. Lines near the edges and corners of the picture help the most.

When you click or drag a point, it stays selected and the loupe stays on it. The arrow keys move it by one pixel. **Tab** also selects a point. When a point is selected, **Tab** selects the next point of the same line and **Shift**+**Tab** the previous one. After the last point of a line, **Tab** moves on: to the next line, the suggestion bubbles and the buttons below the picture. Before the first point, **Shift**+**Tab** moves back the same way. **Enter**, **Esc** or a click anywhere else ends the selection. After **Enter** or **Esc** the point keeps the focus, so **Tab** goes on from there. **Point off** or **Space** switches a selected middle point off or on. A middle point that found no edge starts switched off. A click on a line selects the whole line. **Delete** or **Backspace** removes the selected line. A new line is selected at once, so the key also removes a line you just added.

## Rating

The rating has five steps. It shows how exactly the lines measure the lens. A **Not usable** result is shown but not applied. **Corner correction** is the extra correction near the corners of the picture. It is only measured when a line reaches a corner of the picture.

## Load and save

**Load lens calibration** sets the lens from a calibration saved on this station, or from an exported `.ofsettings` file. Only the lens values of the file are used. Use it when the picture has no straight edge to draw a line on. The calibration must come from the same camera and lens at the same zoom. Loading removes the lines on the picture. You can still change the values afterwards under **Adjust by hand (optional)**.

**Save lens calibration** saves the current values on this station under a name, so you can load them later for this camera and lens. The size of the snapshot is saved with them. The values are relative to the diagonal of the picture, so they also fit another resolution of the same picture shape, but not another shape or zoom. The list says when a calibration was measured on a picture of another shape. If the name already exists, the saved calibration is replaced.

## Full screen

The button in the top right corner of the picture shows the picture on the whole screen. A bar above it shows the next thing to do, a warning when a line does not fit, **Point off**, **Delete line**, **?** (how to use full screen, with the keyboard shortcuts) and **Exit full screen**. Everything works the same as on the page. **Esc** ends full screen. It also cancels a selection, an open menu or a line you have started.
