# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Paths on the frame loop: Pathfinder presses, recording, path commands and the status row."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any

from openfollow.configuration import MAX_PATH_POINTS, AppConfig, PathPointConfig, save_config_change
from openfollow.osc.paths import MarkerPath, PathActive, PathCommand
from openfollow.paths.engine import Press

logger = logging.getLogger(__name__)

# Key of the Operator Screen status row shown while a path records.
RECORDING_FLAG = "path_recording"


def reload_paths(app: Any) -> None:
    """Rebuild the engine from the running config."""
    app._paths.reload(app._config.paths, enabled=app._config.ui.show_experimental_features)


def release(app: Any, marker_id: int | None) -> None:
    """Release ``marker_id`` from its path, if the app runs paths at all."""
    engine = getattr(app, "_paths", None)
    if engine is not None and marker_id is not None:
        engine.release(marker_id)


def pathfinder(app: Any, marker_id: int, marker: Any) -> None:
    """A Pathfinder press from the input driving ``marker_id``, whose steered marker is ``marker``."""
    result = app._paths.press(marker_id, marker.pos)
    if result is Press.RECORD:
        _record_point(app, marker)
    elif result is Press.LOCKED:
        logger.info("Marker %d locked onto path %s", marker_id, app._paths.locked_path(marker_id))
    elif result is Press.RELEASED:
        logger.info("Marker %d released from its path", marker_id)


def _record_point(app: Any, marker: Any) -> None:
    """Add ``marker``'s position to the recorded path, in the running config and in the file, each as it has it.

    The file can hold a path the running config does not have yet: one just made on the web.
    """
    name = app._paths.recording or ""
    x, y, z = marker.pos
    full: list[str] = []

    def add_point(cfg: AppConfig) -> None:
        path = cfg.paths.find(name)
        if path is None:
            return
        if len(path.points) >= MAX_PATH_POINTS:
            full.append(path.name)
            return
        path.points.append(PathPointConfig(x, y, z))

    _write(app, add_point)
    if full:
        logger.warning("Path %s already has %d points; not adding another", full[0], MAX_PATH_POINTS)
    # A rebuild from a running config without the path would stop recording it; the reload watcher brings it.
    if app._config.paths.find(name) is not None:
        reload_paths(app)


def toggle_paths(app: Any) -> None:
    """Show or hide paths on the Operator Screen, and remember it; nothing while paths are off."""
    if not app._paths.enabled:
        return
    show = not app._config.paths.show_paths
    _write(app, lambda cfg: setattr(cfg.paths, "show_paths", show))


def apply_commands(app: Any, commands: Iterable[PathCommand], controlled: Iterable[int], marker_of: Any) -> None:
    """Apply OSC path commands; ``marker_of(id)`` resolves the marker input steers."""
    if not app._paths.enabled:
        return
    controlled_ids = set(controlled)
    for command in commands:
        if isinstance(command, PathActive):
            _set_active(app, command.name, command.active)
        elif isinstance(command, MarkerPath) and command.marker_id in controlled_ids:
            if command.name is None:
                app._paths.release(command.marker_id)
                continue
            marker = marker_of(command.marker_id)
            if marker is not None and not app._paths.lock_onto(command.marker_id, command.name, marker.pos):
                logger.info("OSC: no active path %r to lock marker %d onto", command.name, command.marker_id)


def _set_active(app: Any, name: str, active: bool) -> None:
    path = app._config.paths.find(name)
    if path is None:
        logger.info("OSC: no path called %r", name)
        return
    if path.active != active:
        _write(app, lambda cfg: _switch(cfg, path.name, active))
        reload_paths(app)


def _switch(cfg: AppConfig, name: str, active: bool) -> None:
    path = cfg.paths.find(name)
    if path is not None:
        path.active = active


def recording_row(app: Any) -> tuple[str, str] | None:
    """The blue status row while a path records, else ``None``."""
    name = app._paths.recording
    path = app._config.paths.find(name) if name is not None else None
    if path is None:
        return None
    count = len(path.points)
    return "info", f"Recording path {path.name}: {count} point{'' if count == 1 else 's'}"


def _write(app: Any, change: Callable[[AppConfig], None]) -> None:
    """Make ``change`` in the running config and in the config file as it is now.

    Only the change is written, so a web save the reload watcher has not applied yet is kept, and
    the file is not marked as loaded, so the watcher still applies it.
    """
    try:
        save_config_change(app._config_path, app._config, change)
    except Exception:
        logger.exception("Failed to save the paths change.")
