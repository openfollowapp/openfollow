# Camera Calibration (Advanced)

## The camera

**Where is your camera?** comes first. An estimate within about 2 m per axis is enough. The result is never pulled towards this position.

## Points

Up to 12 points. **Known** points have an X/Y/Z that you type. Two known points far apart, such as the DSL and DSR corners, give about as good a result as four. Two close together give a result two to three times worse. A known point outside the picture still counts. **Triangulated points** are found from their distances to two other points, plus their height. Two distances allow two places, so a triangulated point must be pinned. **Add triangulated point** needs two known points.

## Placing points

A click on the picture opens **Which point is here?** with the points not pinned yet. Choose the point at that spot. **New known point** and **New triangulated point** turn the menu into fields for a new point. **Place point** adds it, pinned where you clicked.

A click on a pin opens its menu. The arrow keys move the pin by one pixel, **Enter** closes the menu. **Remove pin** keeps the point so you can pin it again. **Delete point** removes it. **×** or **Esc** closes the menu without a change. Edit every point in the list under the picture; its **Add known point** button is for a point outside the picture.

## Calculation

The Setup Wizard needs the camera position, two known points and four pinned points. Of the grid, Advanced changes only its height: **Floor height** on Review.

## Camera height

From almost straight above, camera height and field of view cannot be told apart. If the camera looks down steeper than −82.5°, a blue box asks for the exact height of the camera's lens, as Z from the point of origin. A missing height is not a warning.

## Units

**Switch units** changes the station's unit system, the same setting as in General. In Imperial, type feet and inches (`26' 3"` or `26 ft 3 in`); a number without a unit counts as feet. The stored metric value is shown below each field.

## Refresh Image

Takes a new snapshot; the pins stay.

## Distances to check

The distances between every pair of points, from the positions you typed and the calculated ones. Measure the same distance between the real marks on the stage. If it differs, a value is wrong. They check the shape, not its place: a wrong origin or swapped stage sides give the same distances.

## Something might be wrong

You can still save, unless a point is more than 20 px off or behind the camera: then **Save and finish** stays off. It lists: a camera far from the entered position, an unusual field of view, a camera below the floor or pointing upwards, a camera that puts the far floor more than twice as far or as near as the floor points alone do, a point that disagrees with the others, or a triangulated point whose distances disagree with the picture.

## Switching to Simple

Clears all points and their pins. Simple starts again with the rectangle.
