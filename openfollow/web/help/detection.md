# Person Detection

> **Experimental** – do not rely on detection for show-critical paths.

YOLO-based person detection finds people in the camera frame and can steer your markers. It does not run efficiently on a Raspberry Pi 5 today and works best tracking one actor, not crowds.

## Tracking

**Tracking** is the master switch. **AI Assisted** and **Fully Automatic** turn detection on; **Off** turns it off.

- **Off** – no detection.
- **AI Assisted** – default. Each controlled marker has a solid **manual anchor** you steer, and a dim **AI-corrected output** (the position sent on PSN and used for zones). The output glides onto the detected person nearest the anchor, or back to the anchor when nobody is in range. Park the anchor near a person to choose who.
- **Fully Automatic** – drives one marker. Picks the largest visible person and sticks to them, holding through brief occlusions, until they stay gone past the **Grace period**.

- **Follow marker** – *(Fully Automatic)* which marker detection drives: **Currently selected (controller)** or a pinned marker ID.
- **Track** – **Head (top of person)** or **Feet (floor position)** gives the marker position.

**Advanced motion**

- **Smoothing (0–1)** – smooths the position; in AI Assisted also the glide speed. Lower is smoother and laggier. Default `0.15`.
- **Prediction** – lookahead for fast movers, 0–20; `0` is off. Default `8.0`.
- **Grace period (ms)** – how long the marker holds its last position after detection is lost, 0–10 000. Default `500`.

**Assisted Tracking** *(AI Assisted)*

- **Assist radius (m)** – how close a detection must be to the anchor to be picked up. Default `1.0`.
- **Anchor pull (0–1)** – where the output sits when a person is in range: `1` is exactly on the person, lower blends back toward the anchor. Default `0.5`.

## Detection Model

**Quality** tiers: **Fastest** (right for a Raspberry Pi), **Fast**, **Balanced** (workstation default), **Accurate**, **Most Accurate**. Higher tiers need more compute. Accurate and Most Accurate are grayed out on a Pi until fetched.

**Advanced models** downloads other models, selects your own, deletes models and shows free storage space.

## Sensitivity & Overlay

- **Detection sensitivity (0–1)** – score needed to start a track. Lower finds more people and more false positives. Default `0.2`.
- **Detection rate (FPS)** – 1, 2, 5, 10, 15 or 30. Higher uses more CPU. Default 15.
- **Maximum people** – detections kept per frame, 1–50. Default `10`.
- **Boxes** / **Labels** – draw a box and a confidence score per person.
- **Box color** – a box attached to a marker uses that marker's color. Default `#808080`.
- **Box thickness (px)** – 1–10. Default `2`.

Each box saves on its own.
