# Camera Calibration (Simple)

## The rectangle

The four corners of a measured rectangle. Its width, depth, centre and height also become the grid the Operator Screen draws.

**Point of origin** is the 0/0/0 every position this station sends is measured from, so it must match the origin of the console or tracking system. By default it sits midway between DSL and DSR. With **Custom**, enter where the rectangle's centre is, measured from the origin: X towards stage left, Y upstage, Z up. The origin is drawn on the picture as 0/0/0; check it lands on the console's origin mark.

Once its size is entered, the rectangle is drawn so each corner starts on the right side of the picture: as a camera at the position entered in the **Camera Position** box would see it, else one at the location chosen in **Where is your camera located?**, else one in front of the stage. It is drawn at most half the picture's width and height, so every corner starts clear of the frame's edge. Drag every corner onto its tape mark: the camera solves only when all four have been moved.

The blue hint names the next thing to do and sits above the part it is about: the location, the rectangle, the point of origin, or the picture.

**Optional:** before dragging the corners, measure the two diagonals between opposite marks. When both match the value under the rectangle, the marks are right and form a true rectangle of the entered size. It cannot be checked afterwards: four corners always fit the picture exactly, so a wrong mark does not show in the solve. Skip it for a rectangle you know is true, such as the edges of stage decks.

Drag each corner onto its tape mark. The loupe shows the picture at 4× while you drag. A clicked or dragged corner stays selected, with the loupe on it, and the arrow keys nudge it a pixel at a time (**Shift** for ten); **Tab** selects one too. **Enter** places the corner where it is, also when it started on its mark; **Esc** or a click anywhere else ends the selection without that.

The **Camera Position** box under the location is optional. It does not change the solve, it checks it: when the solved camera is further from it than 1 m, or a tenth of the camera's distance to the points, whichever is larger, the caution says so. That catches mistakes that leave every corner fitting exactly, such as a skipped lens step or a wrong rectangle size. X counts toward stage left, Y upstage and Z up.

## Units

**Switch units** changes the station's unit system, the same setting as in General. Every length in the Setup Wizard is typed and shown in it; switching converts the values already entered. In Imperial a length reads as feet and inches (`26' 3"`, `26 ft 3 in`), a bare number as feet, and the stored metric value shows below each field.

## Refresh Image

Takes a new snapshot, for when someone stood on a mark or the stage changed. The pins stay where they are; a snapshot of another resolution moves them with the picture.

## Something might be wrong

The solve still applies, unless a point is more than 20 px off or behind the camera: then **Apply & Finish** stays off until it is fixed. The caution lists what looks implausible or went unchecked: a camera far from the entered position, an unusual field of view, a camera below the floor or looking up, a point that disagrees with the others.

It also says when the lens was not measured: nothing was measured, loaded or fine-tuned in this run of the Setup Wizard, and the station's saved **Barrel / fisheye** and **Edge fit** are both 0. Four corners fit exactly whatever the lens, so the corners look right while the camera behind them is off. A camera without visible barrel distortion needs no lens measurement.

When the station already had a lens and nothing was measured in this run, a note says the station's lens is used. It is not a caution: the lens is the one measured before.

## Full screen

The button in the picture's top right corner shows it on the whole screen, with one line above it: the next thing to do, the largest pin error once the camera solves, **?** for how to use it and its keyboard shortcuts, and **Exit full screen**. Everything works there as on the page. **Esc** ends full screen and cancels an active selection, an open menu or a started line.

## Switching to Advanced

All four corners become known points, so you can add points to them. Placed corners keep their pins. A corner gets the X, Y and Z the rectangle gives it; a value the rectangle does not give yet, such as Y without a depth, stays empty for you to enter.
