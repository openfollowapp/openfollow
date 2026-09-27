v0.4.4
## Marker Control & Visibility is one table

Each marker in the shared catalog has a **This Station** control: **View & Control**, **View** or **Hide**. It replaces the separate selection table below the catalog and saves as soon as you click.

A marker this station controls is now always shown. If a marker was controlled but hidden before this update, its card now appears on the Operator Screen and it counts in the trigger zones.

## Pi Camera on any Raspberry Pi

A camera on a Compute Module, or a sensor the Pi does not recognise by itself, is now set up from the browser: in **Video Source → Pi Camera**, press **Change** beside the camera and choose the camera module and the connector it is plugged into. It starts without a restart, and when no camera is found the choice opens by itself. Raspberry Pi's camera modules are listed by name, a camera that is already streaming is recognised, and a camera asked for a size or frame rate it cannot deliver says so instead of showing No Signal.
