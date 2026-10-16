"""The calibration stored next to each Media Gallery default picture: the camera and grid it was drawn with."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import numpy as np
import pytest
import tomllib

from openfollow.configuration import CameraConfig, GridConfig, load_config
from openfollow.scene.solver import apply_overlay_distortion, project_points
from openfollow.video import media_store as ms

pytestmark = pytest.mark.unit

PICTURES = [
    pytest.param(ms.STAGE_ASSET_SVG, id="stage"),
    pytest.param(ms.ARENA_ASSET_JPG, id="arena"),
    pytest.param(ms.CONCERT_ASSET_JPG, id="concert"),
]
TAPED = [
    pytest.param(ms.ARENA_ASSET_JPG, id="arena"),
    pytest.param(ms.CONCERT_ASSET_JPG, id="concert"),
]
W, H = 1920.0, 1080.0


def _calibration(picture: Path) -> dict:
    return tomllib.loads(picture.with_suffix(".toml").read_text())


@pytest.mark.parametrize("picture", PICTURES)
def test_a_station_config_takes_the_calibration_unchanged(picture: Path, tmp_path: Path) -> None:
    cal = _calibration(picture)
    assert set(cal) == {"camera", "grid"}
    path = tmp_path / "config.toml"
    path.write_text(picture.with_suffix(".toml").read_text())
    cfg = load_config(str(path))
    for key, value in cal["camera"].items():
        assert getattr(cfg.camera, key) == value, key
    for key, value in cal["grid"].items():
        assert getattr(cfg.grid, key) == value, key


def test_the_stage_calibration_is_the_config_defaults_it_is_drawn_from() -> None:
    cal = _calibration(ms.STAGE_ASSET_SVG)
    camera, grid = CameraConfig(), GridConfig()
    assert cal["camera"] == {key: getattr(camera, key) for key in cal["camera"]}
    assert cal["grid"] == {key: getattr(grid, key) for key in cal["grid"]}
    assert set(cal["camera"]) <= {f.name for f in fields(CameraConfig)}
    assert set(cal["grid"]) <= {f.name for f in fields(GridConfig)}


@pytest.mark.parametrize("picture", TAPED)
def test_the_calibration_puts_the_grid_on_the_tape(picture: Path) -> None:
    """Grid corners and the Reference Point, projected with the stored camera and lens, land on white tape."""
    cv2 = pytest.importorskip("cv2", reason="decoding the picture needs the detection extra")
    cal = _calibration(picture)
    cam, grid = cal["camera"], cal["grid"]
    params = np.array([cam[k] for k in ("pos_x", "pos_y", "pos_z", "pitch", "yaw", "roll", "fov")])
    x0, y0 = grid["x_offset"] - grid["width"] / 2, grid["y_offset"] - grid["depth"] / 2
    x1, y1 = x0 + grid["width"], y0 + grid["depth"]
    marks = np.array([(x1, y0, 0.0), (x0, y0, 0.0), (x0, y1, 0.0), (x1, y1, 0.0), (0.0, 0.0, 0.0)])
    px = apply_overlay_distortion(project_points(params, marks, W, H), W, H, cam["lens_k1"], cam["lens_k2"])
    img = cv2.imread(str(picture), cv2.IMREAD_GRAYSCALE).astype(float)
    for x, y in px:
        xi, yi = round(x), round(y)
        assert img[yi - 2 : yi + 3, xi - 2 : xi + 3].max() >= 200, (x, y)
