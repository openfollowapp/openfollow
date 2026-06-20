# Camera

Where the real camera sits in the venue and which lens it uses. OpenFollow uses these to project tracked points into stage coordinates, so they must match the physical rig.

The easiest way to set them is the **Setup Wizard** (the Open Setup Wizard button on the Camera & Grid tab), which solves position, orientation, and field of view from four marked grid corners. The fields here are for direct edits and fine-tuning.

Positions are in **metres**, relative to the **Reference Point** – the single physical point on stage that is the (0, 0, 0) of your show (see Core Concepts). Orientation is in **degrees**.

## Position (X, Y, Z)

- **Position X** – stage left positive, stage right negative. `0` is on the centre line.
- **Position Y** – upstage positive, downstage (towards the audience) negative. A camera out in the house has a negative Y.
- **Position Z** – height of the lens above the stage floor.

## Orientation (Pitch, Yaw, Roll)

- **Pitch** – tilt up or down. Negative looks down at the stage; a front-of-house camera is typically around −20°.
- **Yaw** – pan left or right. `0` looks straight upstage.
- **Roll** – rotation around the lens axis. Leave at `0` unless the camera is physically canted.

## Lens

OpenFollow only needs the **horizontal** field of view; the sensor and focal-length fields are an optional way to work it out.

- **Horizontal Field of View** – the angular width the camera sees, in degrees. Pull this from the camera datasheet.
- **Sensor Size** + **Focal Length** – pick your sensor format (or *Custom…* with a width in mm) and enter the focal length; OpenFollow computes the field of view from the geometry. If you then edit the field of view by hand, these dim to show they're no longer authoritative – your manual value wins.

## Saving & sharing

- **Save** – make the current values durable. Camera and Grid apply live as you type but revert on reload unless you Save.
- **Apply to all stations** – broadcast Camera and Grid to every OpenFollow station on the network. Use it when several operators share one physical camera.
- **Save as template… / Load template…** – store Camera and Grid together as a portable file and recall a full venue setup later.
