# Person Detection

> **Experimental** – accuracy, model defaults, and the configuration surface may change between releases. Do not rely on detection for show-critical paths.

Experimental YOLO-based person detection that finds people in the camera frame and can drive zone events or automatically steer a marker. Opt-in: detection stays disabled until the required packages are installed and **Enabled** is on.

> **Performance.** Person detection is compute-heavy and does **not** run efficiently on a Raspberry Pi 5 today – a more powerful workstation is recommended for it. It is also not suited to very crowded scenes (many overlapping people). Where it shines is tracking an individual actor: it reacts faster than a human operator and can sharpen the marker's position once you have parked the assist anchor on the right person.

## Dependencies

Detection needs onnxruntime + opencv-python (the inference backend) and, for **Download Model**, the export tools (ultralytics + onnx). Install them all with one command:

```
bash /usr/share/openfollow/install-detection.sh
```

It checks for free space first, uses the NVMe when one is present, installs everything into the app venv, and restarts the service. Re-run it any time; it's idempotent. See https://openfollow.app/docs/detection-install.html.

If the section shows a red "Detection needs extra components" banner, the backend isn't installed yet – run that command, then reload.

## Model

- **Model** – the YOLO model file the detector loads (for example `yolov8n.onnx`). Only models actually present in the storage `models/` folder are listed and selectable; a model that isn't on disk can't load, so it appears under **Download Model** instead. When no models are installed yet, the picker shows a "download one below" prompt and your saved selection is kept untouched until a model is present.
- **Models disk** – under the model picker, a one-line readout shows the free / total space on the filesystem that holds the models folder, plus the resolved path. Use it to judge how many models you can keep before the disk fills.
- **Installed models** – every `.onnx` in the storage folder, with its size and a **Delete** button to remove ones you no longer need. Deleting the active model keeps your selection saved but unlistable until another model is on disk.

Storage is automatic: model files live in a `models/` folder under the NVMe (`/mnt/nvme/openfollow/yolo`) when a drive is mounted, otherwise a local `yolo` folder under the application working directory. There is nothing to configure. (An advanced operator can still override the location by setting `detection.storage_path` to an absolute path in `config.toml`.) Because the path is specific to this device, it is never written into a config export and never overwritten by an import or peer broadcast – each station keeps its own storage location.

The catalogue covers the YOLOv8, YOLO11, YOLO12, and YOLO26 families in every size (`n`/`s`/`m`/`l`/`x`). On a CPU-only station such as a Raspberry Pi, prefer a **nano** model – `yolo26n.onnx` is built for edge inference and is a good default; `yolov8n.onnx` is the safe fallback. On a workstation with a GPU, a larger attention-based model such as `yolo12l.onnx` trades speed for accuracy on difficult, silhouetted stage lighting. Use **Download Model** to fetch and convert any catalogued model to ONNX (needs an internet connection; slow on a Pi); set the **Export image size** to match the inference size you intend to run.

## Detection

Core inference settings.

- **Enabled** – master on/off switch. Off by default, with no CPU cost unless enabled.
- **Confidence (0–1)** – the score a detection must reach to start a new track or be treated as a confident sighting. Lower catches more people but more false positives; higher is stricter. Default `0.2`. Detections that dip just below this threshold are not thrown away: the tracker still uses them to hold an already-tracked person, so an actor stepping into shadow (and dimming the detection) keeps their identity instead of being dropped – they just can't spawn a brand-new track on their own.
- **Detection Rate (FPS)** – how often the model runs: 1, 2, 5, 10, 15, or 30 fps. Higher is more responsive but uses more CPU. Default 15 fps.
- **Inference Size** – square resolution (pixels) frames are fed to the model at: 320 (faster), 416, 512, or 640. Larger improves accuracy on distant or small subjects at a compute cost. **Auto-detected from the model**: when the selected `.onnx` has a fixed input size (the usual case for an export), the detector uses that size automatically, so this field only takes effect for a model exported with a dynamic input size. Default `640`.
- **CLAHE Preprocess** – applies contrast-limited adaptive histogram equalisation before inference. Useful in unevenly-lit venues where parts of the stage are much brighter or darker. On by default.
- **Max Persons** – maximum detections kept per frame (highest-confidence first). 1–50. Default `10`. Reducing this limits CPU when many people are in view.

## Display

What the detection overlay draws on the Operator Screen.

- **Show Boxes** – draw a bounding rectangle around each detected person. On by default.
- **Show Labels** – draw a text label (confidence score) inside or above each box. On by default.
- **Box Color** – colour of the boxes. Click the swatch to open the colour picker. Default grey (`#808080`). The box currently attached to a marker is drawn in **that marker's** colour instead, so you can see which detection is driving the marker.
- **Box Thickness (px)** – line weight for the boxes, 1–10 pixels. Default `2`.

## Tracking

Whether detected bounding boxes steer a marker, and how that motion is filtered.

Under both modes the detector runs a **tracking-by-detection** pipeline: each detection is bound to a persistent track with a stable identity, and a motion model predicts every track forward between frames. That two-stage matching is what lets a person who is briefly occluded, or who steps into shadow and dims, keep the same track instead of being dropped and re-numbered on reacquisition. The **Grace Period** below sets how long a lost track is held before it is released.

- **Tracking Mode** – how detection drives the marker:
  - **Fully Automatic** – hands-off. Detection picks the largest visible person, then sticks to *that* person frame to frame (tracked across frames), holding through brief occlusions until they're gone longer than the **Grace Period**, at which point it re-picks the largest. On a multi-performer stage that initial pick is whoever is biggest/closest to the camera, not necessarily the right person.
  - **AI Assisted** – two-marker hybrid tracking, and the default. There are two markers on screen: a **manual anchor** (the solid marker, with its card, that you steer freely with keyboard / gamepad / mouse) and the **AI-corrected output marker** (a dim crosshair + ground ring – the position actually broadcast on PSN and used for zones). The output marker continuously *glides* – it never snaps – onto the detected person **nearest your manual anchor** (within **Assist Radius**), or back toward the anchor when nobody is in range. You choose *who* to follow by parking the anchor near them; the AI supplies the precise, jitter-free position. Because the anchor is independent, you can move it away at any time and the output follows – no more being trapped on a performer. The **Assisted Tracking** fields below appear only in this mode.
- **Pin Marker** – when enabled, detection writes to a marker, moving it automatically. Disable for zone events without marker steering.
- **Pin To Marker** – which marker to drive. **Currently selected (controller)** follows whichever marker the operator has selected; a specific marker ID pins detection to that marker regardless of controller selection.
- **Pin Point** – which part of the bounding box gives the marker position: **Top (Head)** or **Bottom (Feet)**. Choose based on whether show geometry references head height or floor position.
- **Smoothing / glide (0–1)** – exponential smoothing on the pinned marker position. In **AI Assisted** mode this is also the **glide speed**: how fast the output marker eases toward the person (or back to your anchor). Lower is smoother / laggier; higher is more responsive (never instant – the output is always smoothed, never snapped). `0.1`–`0.2` feels natural. Default `0.15`.
- **Prediction** – velocity lookahead multiplier that extrapolates the marker's trajectory to compensate for detection lag on fast movers, 0–20. `0` disables prediction. Default `8.0`.
- **Grace Period (ms)** – how long the pinned marker holds its last position after detection is lost before it stops updating, 0–10 000 ms. Prevents snapping away on a single missed frame. Default `500`.
- **Assist Radius (m)** – *(AI Assisted mode only)* how close a detection must be, in metres, to your manual anchor to be picked up. Detections outside this radius are ignored, so the output marker never jumps to a different performer across the stage. Larger forgives looser anchor aim; smaller demands you keep the anchor tight on the subject. The output always follows whichever in-range detection is **nearest** your anchor – never the biggest or a previously-chosen one – so nudging the anchor toward a different person hands the AI straight over to them. Default `1.0`. Depends on accurate camera calibration (run the setup wizard).
- **Clip strength (0–1)** – *(AI Assisted mode only)* where the output marker sits **when a person is in range**. `1` clips it exactly onto the detected person (the manual anchor only chooses *who*); lower values blend the output back toward your anchor, so the manual position also influences where the output sits. This sets the target only – the glide to that target is governed by **Smoothing / glide** above, so the output is always smoothed and never snaps. Default `0.5`.

**Save** – writes all Person Detection settings to the station's configuration file. Settings take effect immediately; saving persists them across restarts.
