# Review

**Save and finish** saves the camera, the lens and the points. In Simple mode it also saves the rectangle as the grid. The green grid is the one the Operator Screen will draw.

**Leave without saving** keeps the station's camera, lens and grid as they were. Either way, the Setup Wizard returns to the General tab.

Every point is saved, so **Save and finish** stays off while a point is not complete, such as a known point without a value or a triangulated point without a pin. Review names each of these points and what it needs. **Points** and **Pinned** count what **Save and finish** saves.

**Camera height** is listed when the camera looks almost straight down or a height was entered: the height the calculation kept, or **Not entered**.

## Floor height

Advanced only. **Floor height** is the grid's Z Offset, measured from the point of origin: the floor that a marker's Z line, ground circle, cone base and assist ring are drawn down to, and the plane a mouse drag moves a marker on. Simple saves its rectangle's height there; Advanced has no rectangle, so Review asks for it. Width, depth, spacing and the X and Y offsets stay as set on the Grid tab.

The field starts at a suggestion from the points: the points whose heights lie within 5 cm of each other form a group, and the largest group gives its mean height. The note under the field names those points. When no two points share a height, or two separate groups are the same size, the suggestion is 0. When the grid is at another height now, for example from an earlier Simple calibration, the note names that height too, so you can see what changes. A value you type stays while the points stay the same; when changed points suggest another floor, the field takes the new suggestion. The green grid on the picture follows the value as you type, and Camera Calibration draws the grid at the same height.
