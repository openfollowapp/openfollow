# Camera Calibration (Simple)

The four steps open in order: each one is greyed out until the one before it is done.

## The rectangle

Simple uses the four corners of a measured rectangle. Its width, depth, centre and height also become the grid that the Operator Screen draws.

**Point of origin** is the 0/0/0 point. Every position this station sends is measured from it, so it must be the origin of the console or tracking system. **Default** puts it in the middle between DSL and DSR. With **Custom**, enter the rectangle's centre measured from the origin: X towards stage left, Y upstage, Z up.

Once the size is entered, the rectangle is drawn on the picture as a camera at the **Camera Position** would see it. Without a position it uses the location from **1. Where is your camera?**, and with no location a camera in front of the stage. Drag every corner onto its tape mark. **Next** stays off until all four are placed.

While you drag, the loupe shows the picture 4× larger. A clicked corner stays selected: the arrow keys move it by one pixel, **Tab** selects the next corner, **Enter** places it where it is, **Esc** or a click elsewhere ends the selection without placing it. **Reset Corners** puts every corner back on the drawn rectangle.

**Camera Position** is optional. It does not change the result, it only checks it. **Something might be wrong** appears when the calculated camera is further from it than 1 m or a tenth of the camera's distance to the points, whichever is larger.

## Camera height

From almost straight above, camera height and field of view cannot be told apart. If the camera looks down steeper than −82.5°, a blue box asks for the exact height of the camera's lens, as Z from the point of origin. A missing height is not a warning.

## Units

**Switch units** changes the station's unit system, the same setting as in General. In Imperial, type feet and inches (`26' 3"` or `26 ft 3 in`); a number without a unit counts as feet. The stored metric value is shown below each field.

## Something might be wrong

You can still save, unless a point is more than 20 px off or behind the camera: then **Save and finish** stays off. It lists: a camera far from the entered position, an unusual field of view, a camera below the floor or pointing upwards, or a point that disagrees with the others.

It also appears when the lens was not measured: nothing was measured, loaded or typed in under **User input** in this run, and the saved **Barrel / fisheye** and **Corner correction** are both 0. The corners then look correct, but the calculated camera is wrong. A camera without visible barrel distortion needs no lens measurement.

## Switching to Advanced

The corners become known points, and their places on the picture become pins. You can then add more points.
