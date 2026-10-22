# Project Structure

```
openfollow/              # Single unified package
├── __init__.py          # Exports OpenFollowApp
├── main.py              # CLI entry point
├── app.py               # Main application class + event loop
├── configuration.py     # Config model, TOML I/O, hot-reload logic
├── services.py          # Runtime service orchestration
├── system_stats.py      # Runtime system stats collector
├── runtime_metrics.py   # Frame metrics
├── net_utils.py         # Local IP/network helpers
├── packet_chunking.py   # MTU-budgeted datagram split shared by every output
├── paths/               # Paths (the experimental `paths` feature)
│   ├── geometry.py      # A path's curve: straight runs, rounded corners
│   ├── network.py       # Junctions between active paths, walking a locked marker
│   └── engine.py        # Locked markers: pull, slide, switch, record
├── lens_model.py        # The division lens model's validity rule and pull-back (stdlib-only; config, web and solver read it)
├── keyboard_layout.py   # Kiosk session keyboard layout resolver (stdlib-only; also the .deb helper)
├── window.py            # GTK native sink window wrapper
├── psn/                 # PSN protocol subpackage
│   ├── clock.py         # Shared microsecond time base (headers + trackers)
│   ├── marker.py       # Marker state
│   ├── server.py        # PSN sender
│   └── receiver.py      # PSN listener
├── scene/               # Camera model + calibration math
│   ├── camera.py
│   ├── ground_grid.py   # Grid lines clipped to the ground-grid polygon (HUD + web)
│   ├── solver.py        # Projection, homography, DLT camera solve + the division-model overlay warp about the lens centre, both ways
│   ├── calibration_points.py # The Setup Wizard's camera solve from known and triangulated marks
│   ├── lens_fit.py      # k1 / k2 from traced straight lines (the Setup Wizard's Lens step)
│   ├── edge_snap.py     # Snap a traced line's points onto a brightness edge
│   └── edge_chains.py   # Edge suggestions: line-like edges found in a scaled snapshot
├── video/               # Video pipeline + overlay
│   ├── receiver.py      # Generic GStreamer orchestrator
│   ├── overlay.py       # Cairo HUD renderer
│   ├── hud_render.py    # HUD drawn on its own thread, paced by the display
│   ├── gl_display.py    # GL Operator Screen: video and HUD in one GL area, the checks it runs behind
│   ├── gl_cadence.py    # When GL video frames meet the refresh, and what that did to them
│   ├── detection.py     # Optional person detection
│   ├── tracking.py      # ByteTrack tracker (Kalman + two-stage association)
│   ├── media_store.py   # Media Gallery library (images/clips, storage, validation)
│   ├── connection_status.py
│   └── inputs/          # Pluggable input protocols (ndi/srt/rtsp/rtp/picam/testpattern)
├── input/               # Keyboard/gamepad/OSC input handling
│   ├── keyboard.py
│   ├── gamepad.py
│   ├── osc.py
│   └── input_manager.py
└── web/                 # Bottle web UI + APIs + peer discovery
    ├── server.py
    ├── routes.py
    ├── discovery.py
    ├── peer_auth.py     # HMAC-signed peer request auth (no PIN on the wire)
    ├── lens_calibrations.py # Lens calibrations saved on this station, one JSON file each
    ├── static/js/       # polygon-plan.js (top-down plan), picture-view.js (camera picture), ...
    └── templates/
```

## Quick Reference

**Run:** `poetry run openfollow`

**Imports:**
```python
from openfollow.psn import Marker, PsnServer, PsnReceiver
from openfollow.configuration import load_config, AppConfig
from openfollow.services import AppRuntimeServices
```

**Common changes:**
- Overlay/HUD rendering → `video/overlay.py`
- Config → `configuration.py`, `config.toml`
- PSN protocol → `psn/server.py`, `psn/receiver.py`
- Web API → `web/routes.py`
- Camera/calibration → `scene/camera.py`, `scene/solver.py`, `scene/calibration_points.py` (calibration is the web `/wizard`)
- Paths → `paths/`, `runtime/paths_runtime.py`, `osc/paths.py`, `runtime/overlay_draw_paths.py`
- Ground grid → `scene/ground_grid.py`, `runtime/overlay_draw_scene.py` (`floor_z_at`, `draw_ground_grid`, `GridCache`)

**Coordinates (PSN):** X=stage left, Y=upstage, Z=up

**Code quality:** PEP8 enforced via `ruff check` (line-length=120; bundles bugbear/isort/pyupgrade/comprehensions); style locked by `ruff format` (`make format` to apply, `ruff format --check` gates `make ci`)
