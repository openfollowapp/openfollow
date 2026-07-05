# Person Detection

> **Experimental** – accuracy, model defaults, and the configuration surface may change between releases. Do not rely on detection for show-critical paths.

Experimental YOLO-based person detection that finds people in the camera frame and can automatically steer your markers. It ships ready to run: the quality-tier models are pre-installed and detection starts as soon as you pick a tracking mode.

> **Performance.** Person detection is compute-heavy and does **not** run efficiently on a Raspberry Pi 5 today – a more powerful workstation is recommended for it. It is also not suited to very crowded scenes (many overlapping people). Where it shines is tracking an individual actor: it reacts faster than a human operator and can sharpen the marker's position once you have parked the assist anchor on the right person.

## Dependencies

On the macOS app everything needed is bundled. On a Raspberry Pi the inference backend (onnxruntime + opencv) is an optional install:

```
bash /usr/share/openfollow/install-detection.sh
```

It checks for free space first, uses the NVMe when one is present, installs everything into the app venv, and restarts the service. Re-run it any time; it's idempotent. See https://openfollow.app/docs/detection-install.html.

If the section shows a red "Detection needs extra components" banner, the backend isn't installed yet – run that command, then reload.

## Tracking

The Tracking control is the master on/off for the whole feature. Choosing **AI Assisted** or **Fully Automatic** turns person detection on automatically; **Off** turns it off.

- **Off** – no detection runs; no CPU cost.
- **AI Assisted** – the default. Detection refines **all of your controlled markers** at once. For each marker there are two things on screen: a **manual anchor** (the solid marker, with its card, that you steer with keyboard / gamepad / mouse) and the **AI-corrected output** (a dim crosshair + ground ring – the position actually broadcast on PSN and used for zones). The output continuously *glides* – it never snaps – onto the detected person **nearest your anchor** (within **Assist radius**), or back toward the anchor when nobody is in range. You choose *who* each marker follows by parking its anchor near them; the AI supplies the precise, jitter-free position. Because the anchor is independent, you can move it away at any time and the output follows. This works for every operator's marker simultaneously.
- **Fully Automatic** – hands-off, for a single marker. Detection picks the largest visible person, then sticks to *that* person frame to frame (tracked across frames), holding through brief occlusions. If their track is briefly lost or re-numbered it re-locks onto the person nearest where it last followed them; only once they're gone longer than the **Grace period** (or at a cold start) does it fall back to picking the largest. **Follow marker** chooses which marker it drives.

Under both modes the detector runs a **tracking-by-detection** pipeline: each detection is bound to a persistent track with a stable identity, and a motion model predicts every track forward using the real time elapsed between frames (so a dropped frame or a fast mover doesn't throw it off). That matching is what lets a person who is briefly occluded, or who steps into shadow and dims, keep the same track instead of being dropped and re-numbered on reacquisition.

- **Follow marker** – *(Fully Automatic only)* which marker detection drives. **Currently selected (controller)** follows whichever marker the operator has selected; a specific marker ID pins detection to that marker regardless of controller selection.
- **Track** – which part of the bounding box gives the marker position: **Head (top of person)** or **Feet (floor position)**. Choose based on whether show geometry references head height or floor position.
- **Smoothing (0–1)** – exponential smoothing on the pinned marker position. In **AI Assisted** mode this is also the **glide speed**: how fast each output marker eases toward its person (or back to its anchor). Lower is smoother / laggier; higher is more responsive (never instant – the output is always smoothed, never snapped). `0.1`–`0.2` feels natural. Default `0.15`.
- **Prediction** – velocity lookahead multiplier that extrapolates the marker's trajectory to compensate for detection lag on fast movers, 0–20. `0` disables prediction. The lookahead and smoothing are frame-rate-independent, so the feel is the same on a fast workstation and a slower Pi. Default `8.0`.
- **Grace period (ms)** – how long the pinned marker holds its last position after detection is lost before it stops updating, 0–10 000 ms. Prevents snapping away on a single missed frame. Default `500`.
- **Assist radius (m)** – *(AI Assisted only)* how close a detection must be, in meters, to a marker's anchor to be picked up. Detections outside this radius are ignored, so the output never jumps to a different performer across the stage. Larger forgives looser anchor aim; smaller demands you keep the anchor tight on the subject. Each marker always follows whichever in-range detection is **nearest** its anchor. Default `1.0`. Depends on accurate camera calibration (run the setup wizard).
- **Anchor pull (clip strength) (0–1)** – *(AI Assisted only)* where the output sits **when a person is in range**. `1` clips it exactly onto the detected person (the anchor only chooses *who*); lower values blend the output back toward the anchor. This sets the target only – the glide to that target is governed by **Smoothing** above. Default `0.5`.

**Smoothing**, **Prediction**, and **Grace period** live under **Advanced motion** in the Tracking box; **Follow marker** (Fully Automatic only) and **Track** stay visible.

## Detection Model

Detection quality is a simple choice of tier, from fastest to most accurate. Higher tiers detect people more reliably (especially small, distant, or silhouetted subjects under stage lighting) but need more compute.

- **Fastest** – lowest compute; the right choice on a Raspberry Pi.
- **Fast** – light, quick on modest hardware.
- **Balanced** – good accuracy and speed; the default on a workstation.
- **Accurate** – sharper; needs a workstation.
- **Most Accurate** – best accuracy, heaviest compute.

All five tiers come pre-installed on the macOS app. On a Raspberry Pi the Fastest / Fast / Balanced tiers ship in the image; the heavier Accurate / Most Accurate tiers (which a Pi can't run well anyway) appear grayed out and can be fetched from **Advanced models** on a workstation.

**Advanced models** (collapsed by default) is for everything beyond the tiers: downloading other cataloged models, selecting a model you downloaded yourself, deleting models to free space, and a readout of free / total space on the storage disk. Storage is automatic – model files live in a `models/` folder on the NVMe (`/mnt/nvme/openfollow/yolo`) when a drive is mounted, otherwise a local `yolo` folder under the application working directory. The path is specific to this device, so it is never written into a config export and never overwritten by an import or peer broadcast. (An advanced operator can override it with `detection.storage_path` in `config.toml`.)

## Sensitivity & Overlay

How sensitive detection is, and what the overlay draws.

- **Detection sensitivity (0–1)** – the score a detection must reach to start a new track or be treated as a confident sighting. Lower catches more people but more false positives; higher is stricter. Default `0.2`. Detections that dip just below this threshold are not thrown away: the tracker still uses them to hold an already-tracked person, so an actor stepping into shadow (and dimming the detection) keeps their identity instead of being dropped.
- **Detection rate (FPS)** – how often the model runs: 1, 2, 5, 10, 15, or 30 fps. Higher is more responsive but uses more CPU. Default 15 fps.
- **Maximum people** – maximum detections kept per frame (highest-confidence first). 1–50. Default `10`. Reducing this limits CPU when many people are in view.
- **Show boxes** – draw a bounding rectangle around each detected person. On by default.
- **Show labels** – draw a text label (confidence score) inside or above each box. On by default.
- **Box color** – color of the boxes. Click the swatch to open the color picker. Default gray (`#808080`). A box currently attached to a marker is drawn in **that marker's** color instead, so you can see which detection is driving each followspot.
- **Box thickness (px)** – line weight for the boxes, 1–10 pixels. Default `2`.

**Save** – each box saves on its own. Settings take effect immediately; saving persists them across restarts.
