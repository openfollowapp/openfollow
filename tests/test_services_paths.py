# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""What the runtime tells the path editor: marker positions with height, the engine's state, recording."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openfollow.configuration import AppConfig, PathConfig, PathPointConfig, PathsConfig, save_config
from openfollow.paths.engine import PathEngine
from openfollow.services import AppRuntimeServices

pytestmark = pytest.mark.unit


class _Markers:
    def __init__(self, positions: dict[int, tuple[float, float, float]], online: set[int] | None = None) -> None:
        self._markers = {m: SimpleNamespace(pos=p) for m, p in positions.items()}
        self._online = online if online is not None else set(positions)

    def get_marker(self, marker_id: int) -> SimpleNamespace | None:
        return self._markers.get(marker_id)

    def is_marker_online(self, marker_id: int) -> bool:
        return marker_id in self._online


def _services(tmp_path, *, server=None, receiver=None, controlled=()) -> AppRuntimeServices:  # noqa: ANN001
    cfg = AppConfig(psn_system_name="X")
    cfg.ui.show_experimental_features = True
    cfg.paths = PathsConfig(
        paths=[PathConfig(name="cross", active=True, points=[PathPointConfig(0, 0, 0), PathPointConfig(4, 0, 0)])]
    )
    config_path = str(tmp_path / "config.toml")
    save_config(cfg, config_path)
    engine = PathEngine()
    engine.reload(cfg.paths, enabled=True)
    app = SimpleNamespace(
        _config=cfg,
        _config_path=config_path,
        _server=server,
        _psn_receiver=receiver,
        _controlled_ids=list(controlled),
        _paths=engine,
    )
    return AppRuntimeServices(app)


class TestMarkerPosition:
    def test_a_controlled_marker_is_read_from_this_station_with_its_height(self, tmp_path) -> None:  # noqa: ANN001
        svc = _services(tmp_path, server=_Markers({2: (1, 2, 1.6)}), receiver=_Markers({}), controlled=[2])
        assert svc._marker_position(2) == (1.0, 2.0, 1.6)

    def test_a_remote_marker_counts_only_while_it_is_online(self, tmp_path) -> None:  # noqa: ANN001
        receiver = _Markers({5: (3, 4, 0)}, online={5})
        svc = _services(tmp_path, server=_Markers({}), receiver=receiver)
        assert svc._marker_position(5) == (3.0, 4.0, 0.0)
        receiver._online.clear()
        assert svc._marker_position(5) is None

    @pytest.mark.parametrize("controlled", [[2], []])
    def test_nothing_without_the_marker_or_its_source(self, tmp_path, controlled) -> None:  # noqa: ANN001
        assert _services(tmp_path, controlled=controlled)._marker_position(2) is None
        assert _services(tmp_path, server=_Markers({}), controlled=[2])._marker_position(2) is None


def test_the_path_state_names_the_recording_and_the_locks(tmp_path) -> None:  # noqa: ANN001
    svc = _services(tmp_path)
    engine = svc._app._paths
    engine.press(7, (1.0, 1.0, 0.0))
    engine.record("cross", svc._app._config.paths)
    assert svc._path_state() == {"recording": "cross", "locks": {7: "cross"}}


def test_recording_reads_the_paths_from_the_file_the_web_ui_just_wrote(tmp_path) -> None:  # noqa: ANN001
    svc = _services(tmp_path)
    cfg = AppConfig(psn_system_name="X")
    cfg.paths = PathsConfig(paths=[PathConfig(name="cross"), PathConfig(name="fresh")])
    save_config(cfg, svc._app._config_path)
    assert svc._record_path("fresh") is True
    assert svc._app._paths.recording == "fresh"
    assert svc._record_path("nope") is False
    assert svc._record_path(None) is True
    assert svc._app._paths.recording is None


@pytest.mark.parametrize("content", [b"", b"not [[ toml", b"grid = 5\n"], ids=["empty", "unparseable", "not-a-config"])
def test_recording_a_broken_file_records_the_running_paths(tmp_path, content: bytes) -> None:  # noqa: ANN001
    svc = _services(tmp_path)
    with open(svc._app._config_path, "wb") as f:
        f.write(content)
    assert svc._record_path("cross") is True
    assert svc._app._paths.recording == "cross"


def test_a_rename_the_web_ui_is_saving_keeps_the_markers_on_the_path(tmp_path) -> None:  # noqa: ANN001
    svc = _services(tmp_path)
    engine = svc._app._paths
    engine.press(7, (1.0, 0.2, 0.0))
    svc._stage_path_rename("cross", "ds")
    renamed = PathsConfig(
        paths=[PathConfig(name="ds", active=True, points=[PathPointConfig(0, 0, 0), PathPointConfig(4, 0, 0)])]
    )
    engine.reload(renamed, enabled=True)
    assert engine.locked_path(7) == "ds"


def test_the_path_state_names_a_path_by_a_rename_saved_before_the_reload(tmp_path) -> None:  # noqa: ANN001
    svc = _services(tmp_path)
    engine = svc._app._paths
    engine.press(7, (1.0, 0.2, 0.0))
    engine.record("cross", svc._app._config.paths)
    svc._stage_path_rename("cross", "ds")
    assert svc._path_state() == {"recording": "ds", "locks": {7: "ds"}}
