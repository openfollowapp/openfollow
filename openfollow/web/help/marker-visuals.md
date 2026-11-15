# Marker Visuals

How each marker appears on the Operator Screen. These settings apply to every marker this station renders and have no effect on PSN, OSC or any other output.

## Style

**Marker Style** picks how a marker is drawn. The other style's settings are kept for when you switch back.

- **Crosshair** (default) – the ball, crosshair, Z line and ground circle, each switched and sized on its own.
- **Cone** – a ring on the stage plane at the marker's ground position, a smaller ring at its Z, and the edges joining them, in the marker's colour. The Body, Crosshair, Z Line and Ground Circle settings are ignored.

The selected marker is drawn heavier, and a marker this station only views is drawn dimmer. The mouse grab area is the ring on the stage plane.

## Cone

- **Base Diameter** – the ring on the stage plane, in metres. Default `0.6`.
- **Top Diameter** – the ring at the marker's Z, in metres. Default `0.3`. `0` gives a point.
- **Line Thickness (px)** – 1–10. Default `2`.
- **Cone fill** – filled in the marker's colour, or a wireframe. Default on.
- **Fill Opacity (0–1)** – `0` transparent, `1` opaque. Default `0.4`.
- **Shading** – lights the filled side from the left so the cone reads as solid. Default on.

## Body

- **Ball** – shows or hides the ball, a semi-transparent sphere at the marker's 3D position. The other elements stay active independently.
- **Ball Size** – radius in metres, scaled with perspective. Default `0.15`.
- **Opacity (0–1)** – `0` transparent, `1` opaque. Default `0.3`.

## Crosshair

A cross at the marker's projected screen position, for precise aiming.

- **Crosshair** – shows or hides it.
- **Crosshair Size** – arm length in metres. Default `0.3`.
- **Crosshair Thickness (px)** – 1–10. Default `2`.
- **Crosshair Color** – click the swatch to pick a greyscale tone. Default white (`#ffffff`).

The per-marker colour is set in **Marker Control & Visibility**, not here.

## Z Line

The line between the ground position and the ball. It gets longer as you raise the marker and is gone on the stage plane.

- **Z Line** – shows or hides it.
- **Z Line Thickness (px)** – 1–20. Default `2`.

## Ground Circle

A circle on the stage plane at the marker's ground position.

- **Ground circle** – shows or hides it.
- **Circle Size** – radius in metres. Default `0.3`.
- **Circle fill** – a solid disc or an outline ring.

## Z Display

**Z readout** – which height the marker card shows. **Show Z relative to the Reference Point** shows Z as it is sent. **Show Z relative to the grid** subtracts the grid's **Z Offset**, so a marker on the stage floor reads 0.

## Saving

**Save** writes the settings to disk. Visuals apply immediately, but changes revert on reload unless you save.
