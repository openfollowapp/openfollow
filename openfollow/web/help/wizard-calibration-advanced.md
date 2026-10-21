# Camera Calibration (Advanced)

## The camera

**Where is your camera?** comes first. The calculation starts from a camera at this position, looking at the points, so an estimate within about 2 m per axis is enough. The result is never pulled towards this position. The warning **Something might be wrong** appears when the calculated camera is further from it than 2.5 m on one axis or a tenth of the camera's distance to the points, whichever is larger.

## Points

Up to 12 points. **Known** points have an X/Y/Z that you type. Two known points far apart, such as the DSL and DSR corners, give about as good a result as four. Two known points close together give a result two to three times worse. A known point outside the picture still counts: other points can be measured from it. **Distance points** are found from their distances to two other points, plus their height. Two distances allow two possible places, mirrored across the line between the two points. So a distance point must be pinned. Its X/Y/Z is shown in its row as soon as it and its two points are pinned, before the camera is calculated. The calculation then makes it more exact. **Add distance point** appears when two known points exist.

## Placing points

Over the picture, the loupe follows the pointer. A click on the picture opens **Which point is here?** with the points that are not pinned yet. Choose the point that is at that spot. **New known point** changes the menu into fields for its name and X/Y/Z. **New distance point** changes it into fields for its name, the distances to two other points and its height. **Place point** adds the point, pinned where you clicked. While the menu is open, the loupe stays on the spot, and the arrow keys move the spot by one pixel. While the menu shows a new point, a click on the picture also moves the spot. A click on a pin opens its menu. **Remove pin** keeps the point, so you can pin it again. **Delete point** removes the point. **×** or **Esc** closes the menu without a change. You can edit every point in the list under the picture. Its **Add known point** button is for a point outside the picture.

## Calculation

To calculate the camera, the Setup Wizard needs the camera position, two known points and four pinned points. More points give a better result. The blue hint shows the next thing to do. It is placed above the part of the page it is about: Camera Position or the picture. Advanced never changes the grid.

## Camera height

From almost straight above, the camera's height and its field of view cannot be told apart: a higher camera with a narrower lens sees flat points the same way as a lower camera with a wider lens. A calculated camera that looks down steeper than -82.5° can be metres too high or too low, without a larger error on any point. So then a blue box asks for the exact height of the camera's lens, measured as Z from the point of origin. The calculation then keeps the camera at this height, and a note shows the height. A missing height is not a warning, and **Save and finish** stays on without it. The height is saved with the calibration, so the Setup Wizard starts from it the next time.

## Units

**Switch units** changes the unit system of the station. It is the same setting as in General. You type and see every length in the Setup Wizard in these units. Switching converts the values you already entered. In Imperial, type a length in feet and inches (`26' 3"` or `26 ft 3 in`). A number without a unit counts as feet. The stored metric value is shown below each field.

## Refresh Image

Takes a new snapshot, for example when someone stood on a mark or the stage changed. The pins stay where they are. If the new snapshot has another resolution, the pins move with the picture.

## Distances to check

Calculated from the positions you typed and the calculated positions, for every pair of points. The distances from a distance point to the two points it is measured from are not listed. Measure the same distance between the real marks on the stage. If it is different, a value is wrong. The distances check the shape of the points, not where the shape is: a wrong origin or swapped stage sides give the same distances.

## Something might be wrong

You can still save the result, unless a point is more than 20 px off or behind the camera: then **Save and finish** stays off until it is corrected. The warning lists what looks wrong or could not be checked: a camera far from the entered position, an unusual field of view, a camera below the floor or pointing upwards, a camera that puts the far floor more than twice as far or as near as the floor points alone do, a point that disagrees with the others, or a distance point whose distances disagree with the picture.

## Full screen

The button in the top right corner of the picture shows the picture on the whole screen. A bar above it shows the next thing to do, the largest error when the camera is calculated, **?** (how to use full screen, with the keyboard shortcuts) and **Exit full screen**. Everything works the same as on the page. **Esc** ends full screen. It also cancels a selection or an open menu.

## Switching to Simple

Clears all points and their pins. Simple starts again with the rectangle.
