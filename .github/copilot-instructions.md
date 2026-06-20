# OpenFollow - Copilot Instructions

See `docs/PROJECT_STRUCTURE.md` for layout.

## Overview

Live video overlay + 3D marker visualization + PSN protocol output for show-control systems.

**Run:** `poetry run openfollow`

## Architecture

**Package:** Single `openfollow/` with subpackages (`psn/`, `scene/`, `video/`, `input/`, `web/`)

**Key modules:**
- `app.py` – Main application, lifecycle
- `configuration.py` – Config (TOML I/O, hot-reload)
- `services.py` – Runtime service orchestration
- `runtime_metrics.py` – Frame metrics + pooling helpers
- `psn/server.py`, `psn/receiver.py` – PSN protocol
- `scene/camera.py`, `scene/solver.py` – Camera/calibration
- `video/receiver.py` – GStreamer pipeline orchestration

**Coordinates (PSN):** X=stage left, Y=upstage, Z=up (pygfx uses different internal coords, conversion handled)

**Marker flow:** Input → `Marker` → `PsnServer` (60 FPS) → network. `PsnReceiver` ignores controlled IDs.

## Code Conventions

**Imports (absolute only):**
```python
from openfollow.psn import Marker, PsnServer, PsnReceiver
from openfollow.configuration import load_config, AppConfig
from openfollow.services import AppRuntimeServices
```

**Standards:**
- PEP8 enforced (`ruff check`, line-length=120)
- Type hints required (`list[int]`, `| None`)
- Threading: `threading.Lock` for shared state
- Config: TOML via `load_config()`, hot-reload enabled

## Workflow

Per `CLAUDE.md`: Plan first (3+ steps), verify before done, minimal impact, track in todos, capture lessons.
