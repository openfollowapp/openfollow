# Camera Calibration (Advanced)

## The camera

**Where is your camera?** comes first: the solve starts from a camera there, looking at the points, so an estimate within roughly 2 m per axis is enough. The solve never bends toward it. When the solved camera is further from it than 2.5 m on an axis, or a tenth of the camera's distance to the points, whichever is larger, the caution says so.

## Points

Up to 12 points. **Known** points have a typed X/Y/Z. Two of them far apart, such as the DSL and DSR corners, solve about as well as four; two close together solve two to three times worse. A known point outside the picture still counts as a point to measure from. **Triangulated** points are measured by their distances to two other points plus their height. Two distances leave two possible places, mirrored across the line between the two points, so a triangulated point must be pinned. Its X/Y/Z shows in its row as soon as it and its two points are pinned, before the camera solves; the solve then refines it. **Add triangulated point** appears once two known points exist.

## Placing points

Over the picture, the loupe follows the pointer. A click on the picture opens a menu of the points not pinned yet; choose the one that is there. **New point** turns the menu into its name and X/Y/Z; **New triangulated point** into its name, the distances to two other points and its height. **Place point** adds it, pinned where you clicked. While the menu is open the loupe stays on its spot, and the arrow keys move the spot a pixel at a time (**Shift** for ten); while it shows a new point, a click on the picture moves the spot too. A click on a pin opens its menu: **Remove pin** keeps the point to pin again, **Delete point** removes it. **×** or **Esc** closes the menu without a change. The points list under the picture holds every point to edit; its **Add known point** is for a point outside the picture.

## Solving

The solve needs the camera position, two known points and four pinned points; more points give a better result. The hint names the next thing to do and sits above the part it is about: Camera Position, or the picture. Advanced never changes the grid.

## Units

**Switch units** changes the station's unit system, the same setting as in General. Every length in the Setup Wizard is typed and shown in it; switching converts the values already entered. In Imperial a length reads as feet and inches (`26' 3"`, `26 ft 3 in`), a bare number as feet, and the stored metric value shows below each field.

## Refresh Image

Takes a new snapshot, for when someone stood on a mark or the stage changed. The pins stay where they are; a snapshot of another resolution moves them with the picture.

## Distances to check

Computed from the typed and solved positions, for every pair of points except the ones a triangulated point was measured from. A distance measured between the real marks that disagrees points at a wrong value. They check the shape of the points, not where it sits: a wrong origin or swapped stage sides gives the same distances.

## Something might be wrong

The solve still applies, unless a point is more than 20 px off or behind the camera: then **Apply & Finish** stays off until it is fixed. The caution lists what looks implausible or went unchecked: a camera far from the entered position, an unusual field of view, a camera below the floor or looking up, a camera that puts the far floor more than twice as far or as near as the floor points alone do, a point that disagrees with the others, a triangulated point whose distances disagree with the picture.

## Full screen

The button in the picture's top right corner shows it on the whole screen, with one line above it: the next thing to do, the largest pin error once the camera solves, **?** for how to use it and its keyboard shortcuts, and **Exit full screen**. Everything works there as on the page. **Esc** ends full screen and cancels an active selection, an open menu or a started line.

## Switching to Simple

Clears every point and its pin; Simple starts again from the rectangle.
