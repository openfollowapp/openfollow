"""The calibration stored next to each Media Gallery default picture: the camera and grid it was drawn with."""

from __future__ import annotations

import tomllib
from dataclasses import fields
from pathlib import Path

import numpy as np
import pytest

from openfollow.configuration import CameraConfig, GridConfig, load_config
from openfollow.scene.solver import apply_overlay_distortion, project_points
from openfollow.video import media_store as ms

pytestmark = pytest.mark.unit

PICTURES = [
    pytest.param(ms.STAGE_ASSET_SVG, id="stage"),
    pytest.param(ms.CONCERT_ASSET_JPG, id="concert"),
    pytest.param(ms.FESTIVAL_ASSET_JPG, id="festival"),
]
TAPED = [
    pytest.param(ms.CONCERT_ASSET_JPG, id="concert"),
    pytest.param(ms.FESTIVAL_ASSET_JPG, id="festival"),
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
    lens = (cam["lens_k1"], cam["lens_k2"], cam.get("lens_cx", 0.0), cam.get("lens_cy", 0.0))
    px = apply_overlay_distortion(project_points(params, marks, W, H), W, H, *lens)
    img = cv2.imread(str(picture), cv2.IMREAD_GRAYSCALE).astype(float)
    for x, y in px:
        xi, yi = round(x), round(y)
        assert img[yi - 2 : yi + 3, xi - 2 : xi + 3].max() >= 200, (x, y)


HELP = Path(ms.__file__).resolve().parents[2] / "docs" / "help-reference" / "video_source.md"


def _help_camera_table(title: str) -> dict[str, str]:
    """The camera table under a picture's paragraph in the Video Source help reference."""
    text = HELP.read_text()
    block = text[text.index(f"**{title}** is ") :]
    rows = {}
    for line in block.splitlines()[1:]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.strip().startswith("|") and len(cells) == 2 and cells[0] not in ("Camera setting", "---"):
            rows[cells[0]] = cells[1]
        elif rows and not line.strip().startswith("|"):
            break
    return rows


@pytest.mark.parametrize(("title", "picture"), [("Concert", ms.CONCERT_ASSET_JPG), ("Festival", ms.FESTIVAL_ASSET_JPG)])
def test_the_help_reference_lists_the_camera_a_picture_was_drawn_with(title: str, picture: Path) -> None:
    cam = _calibration(picture)["camera"]
    rows = _help_camera_table(title)

    def number(text: str) -> float:
        return float(text.split()[0].rstrip("°"))

    assert [float(v) for v in rows["Position X / Y / Z"].removesuffix(" m").split(" / ")] == [
        cam["pos_x"],
        cam["pos_y"],
        cam["pos_z"],
    ]
    assert number(rows["Pitch"]) == cam["pitch"]
    assert number(rows["Yaw"]) == cam["yaw"]
    assert number(rows["Roll"]) == cam["roll"]
    assert number(rows["Horizontal Field of View"]) == cam["fov"]
    assert number(rows["Barrel / fisheye"]) == cam["lens_k1"]
    assert number(rows["Corner correction"]) == cam["lens_k2"]
    half_diag = 0.5 * float(np.hypot(W, H))
    right = number(rows.get("Lens centre, right of the picture centre", "0 px"))
    above = number(rows.get("Lens centre, above the picture centre", "0 px"))
    assert (right, above) == (round(cam.get("lens_cx", 0.0) * half_diag), round(-cam.get("lens_cy", 0.0) * half_diag))
