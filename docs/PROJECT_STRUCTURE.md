# Project Structure

```
openfollow/              # Single unified package
├── __init__.py          # Exports OpenFollowApp
├── main.py              # CLI entry point
├── app.py               # Main application class + event loop
├── configuration.py     # Config model, TOML I/O, hot-reload logic
├── services.py          # Runtime service orchestration
├── system_stats.py      # Runtime system stats collector
├── runtime_metrics.py   # Frame metrics + overlay-state pooling helpers
├── net_utils.py         # Local IP/network helpers
├── window.py            # GTK native sink window wrapper
├── psn/                 # PSN protocol subpackage
│   ├── marker.py       # Marker state
│   ├── server.py        # PSN sender
│   └── receiver.py      # PSN listener
├── scene/               # Camera model + calibration math
│   ├── camera.py
│   └── solver.py        # DLT camera solve (used by the web wizard)
├── video/               # Video pipeline + overlay
│   ├── receiver.py      # Generic GStreamer orchestrator
│   ├── overlay.py       # Cairo HUD renderer
│   ├── detection.py     # Optional person detection
│   ├── tracking.py      # ByteTrack tracker (Kalman + two-stage association)
│   ├── connection_status.py
│   └── inputs/          # Pluggable input protocols (ndi/srt/rtsp/rtp/picam)
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
- Camera/calibration → `scene/camera.py`, `scene/solver.py` (calibration is the web `/wizard`)

**Coordinates (PSN):** X=stage left, Y=upstage, Z=up

**Code quality:** PEP8 enforced via `ruff check` (line-length=120; bundles bugbear/isort/pyupgrade/comprehensions); style locked by `ruff format` (`make format` to apply, `ruff format --check` gates `make ci`)
