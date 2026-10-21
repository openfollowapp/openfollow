# Camera Calibration (Simple)

## The rectangle

Simple uses the four corners of a measured rectangle. Its width, depth, centre and height also become the grid that the Operator Screen draws.

The four questions open in order: each one is greyed out until the one before it is answered. A rectangle that is already entered counts as the first question answered.

**Point of origin** is the 0/0/0 point. Every position this station sends is measured from it, so it must be the same as the origin of the console or tracking system. **Default** puts it in the middle between DSL and DSR. With **Custom**, enter the position of the rectangle's centre, measured from the origin: X towards stage left, Y upstage, Z up. The picture shows the origin as 0/0/0. Check that it is on the console's origin mark.

When you enter its size, the rectangle is drawn on the picture so that each corner starts on the correct side. It is drawn as a camera at the position in the **Camera Position** box would see it. Without a position, it is drawn as a camera at the location from **1. Where is your camera?** would see it. If no location is chosen, it is drawn as a camera in front of the stage would see it. It is at most half as wide and half as high as the picture, so no corner starts at the edge of the picture. Drag every corner onto its tape mark. The camera is calculated only when all four corners have been placed, and **Next** stays off until then.

The blue hint shows the next thing to do. It is placed above the part of the page it is about: the location, the rectangle, the point of origin or the picture.

**Optional:** before you drag the corners, measure the two diagonals between opposite tape marks. If both are the same as the value under the rectangle, the marks are correct and form a true rectangle of the entered size. You cannot check this later: four corners always fit the picture exactly, so a wrong mark does not show in the result. Skip this for a rectangle you know is true, such as the edges of stage decks.

Drag each corner onto its tape mark. While you drag, the loupe shows the picture 4× larger. When you click or drag a corner, it stays selected and the loupe stays on it. The arrow keys move it by one pixel. **Tab** also selects a corner. **Enter** places the corner where it is. This also works for a corner that already starts on its mark. **Esc** or a click anywhere else ends the selection without placing the corner. **Reset Corners** appears when a corner has been moved, and puts every corner back on the drawn rectangle.

The **Camera Position** box under the location is optional. It does not change the result, it only checks it. The warning **Something might be wrong** appears when the calculated camera is further from this position than 1 m or a tenth of the camera's distance to the points, whichever is larger. This finds mistakes where every corner still fits exactly, such as a skipped lens step or a wrong rectangle size. X counts towards stage left, Y upstage and Z up.

## Units

**Switch units** changes the unit system of the station. It is the same setting as in General. You type and see every length in the Setup Wizard in these units. Switching converts the values you already entered. In Imperial, type a length in feet and inches (`26' 3"` or `26 ft 3 in`). A number without a unit counts as feet. The stored metric value is shown below each field.

## Refresh Image

Takes a new snapshot, for example when someone stood on a mark or the stage changed. The corners stay where they are. If the new snapshot has another resolution, the corners move with the picture.

## Something might be wrong

You can still save the result, unless a point is more than 20 px off or behind the camera: then **Save and finish** stays off until it is corrected. The warning lists what looks wrong or could not be checked: a camera far from the entered position, an unusual field of view, a camera below the floor or pointing upwards, or a point that disagrees with the others.

It also tells you when the lens was not measured: nothing was measured, loaded or adjusted by hand in this run of the Setup Wizard, and the station's saved **Barrel / fisheye** and **Corner correction** are both 0. Four corners always fit exactly, whatever the lens is. So the corners look correct, but the calculated camera is wrong. A camera without visible barrel distortion does not need a lens measurement.

When the station already had a lens and nothing was measured in this run, a note says that the station's lens is used. This is not a warning: the lens is the one measured before.

## Full screen

The button in the top right corner of the picture shows the picture on the whole screen. A bar above it shows the next thing to do, the largest error when the camera is calculated, **?** (how to use full screen, with the keyboard shortcuts) and **Exit full screen**. Everything works the same as on the page. **Esc** ends full screen. It also cancels a selection or an open menu.

## Switching to Advanced

The corners become known points, and their places on the picture become pins. You can then add more points. A corner gets the X, Y and Z that the rectangle gives it. A value the rectangle does not give yet, such as Y without a depth, stays empty for you to enter.
