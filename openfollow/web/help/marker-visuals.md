# Marker Visuals

Controls how each marker appears on the Operator Screen – size, transparency, and which elements are drawn over the live camera feed. These settings apply to every marker this station renders and have no effect on PSN, OSC, or any other output protocol.

## Body

The ball is the primary visual: a semi-transparent sphere drawn at the marker's 3D position in the scene.

- **Show Ball** – enable or disable the ball. When unchecked the ball is hidden; all other elements (crosshair, drop line, ground circle) remain active independently.
- **Ball Size** – radius of the ball in metres. Because it is sized in world units, `0.15` m appears roughly head-sized at a performer's head height. Default: `0.15`.
- **Opacity (0–1)** – `0` is fully transparent; `1` is fully opaque; in-between values let the stage image show through. Default: `0.3`.

## Crosshair

A 2D cross pinned to the marker's projected screen position – the precise aiming point for centring on a specific target.

- **Show Crosshair** – enable or disable the crosshair.
- **Crosshair Size** – arm length of the cross, in metres. Default: `0.3`.
- **Crosshair Thickness (px)** – line weight in pixels (1–10). Default: `2`.
- **Crosshair Color** – click the swatch to open the picker and choose a colour for the crosshair lines. The palette is greyscale tones so the crosshair stays legible over any stage image. Default: white (`#ffffff`).

> Per-marker colour (the ball and other filled elements) is set in **Marker Control & Visibility → Shared catalog**, not here. The Crosshair Color field applies to the crosshair lines only.

## Z Display

**Z from Stage Level** – when checked, the height readout shown near the marker displays Z relative to the stage plane rather than absolute world Z. Most operators prefer this: a performer standing on the floor reads roughly 1.7 m rather than the absolute coordinate from the Reference Point.

## Drop Line

A thin vertical line from the marker straight down to the stage plane. Useful when the marker is elevated – it keeps the plan-view position readable at height.

- **Drop Line** – enable or disable the drop line.
- **Drop Line Thickness (px)** – line weight in pixels (1–20). Default: `2`.

## Ground Circle

A circle drawn on the stage plane directly beneath the marker, giving an additional floor-projection cue.

- **Ground Circle** – enable or disable the ground circle.
- **Circle Size** – radius of the circle in metres. Default: `0.3`.
- **Filled** – when checked the circle is a solid disc; when unchecked it is an outline ring.

## Saving

**Save** – write the current visual settings to disk. Visuals apply immediately, but changes revert on reload unless you save.
