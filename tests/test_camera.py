# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for ``openfollow.scene.camera``: PSN-to-pygfx coordinate
conversion helpers and the Camera class (construction, FOV clamping,
move/rotate, and config round-trip)."""

from __future__ import annotations

import numpy as np
import pytest

from openfollow.configuration import CameraConfig
from openfollow.scene.camera import Camera, psn_to_pygfx, psn_to_pygfx_array

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Coordinate conversion helpers
# ---------------------------------------------------------------------------


class TestPsnToPygfx:
    def test_basic_conversion(self) -> None:
        # PSN: X=stage left, Y=upstage, Z=up
        # pygfx: X=right, Y=up, Z=towards viewer
        result = psn_to_pygfx(1.0, 2.0, 3.0)
        assert result == (1.0, 3.0, -2.0)

    def test_origin(self) -> None:
        assert psn_to_pygfx(0.0, 0.0, 0.0) == (0.0, 0.0, 0.0)

    def test_negative_values(self) -> None:
        result = psn_to_pygfx(-5.0, -3.0, -1.0)
        assert result == (-5.0, -1.0, 3.0)


class TestPsnToPygfxArray:
    def test_converts_nx3_array(self) -> None:
        pts = np.array(
            [
                [1.0, 2.0, 3.0],
                [4.0, 5.0, 6.0],
            ]
        )
        out = psn_to_pygfx_array(pts)
        np.testing.assert_array_equal(out[:, 0], [1.0, 4.0])
        np.testing.assert_array_equal(out[:, 1], [3.0, 6.0])
        np.testing.assert_array_equal(out[:, 2], [-2.0, -5.0])

    def test_single_point(self) -> None:
        pts = np.array([[10.0, 20.0, 30.0]])
        out = psn_to_pygfx_array(pts)
        assert out.shape == (1, 3)
        np.testing.assert_array_almost_equal(out[0], [10.0, 30.0, -20.0])

    def test_integer_input_returns_float64_without_truncation(self) -> None:
        # An int input array must not make the output inherit int dtype:
        # downstream projection math would then truncate world coordinates.
        pts = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.int64)
        out = psn_to_pygfx_array(pts)
        assert out.dtype == np.float64
        np.testing.assert_array_equal(out[:, 1], [3.0, 6.0])
        np.testing.assert_array_equal(out[:, 2], [-2.0, -5.0])


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------


class TestCamera:
    def test_default_construction(self) -> None:
        cam = Camera()
        assert cam._pos == [0.0, -10.0, 5.0]
        assert cam.pitch == -20.0
        assert cam.yaw == 0.0
        assert cam.roll == 0.0
        assert cam.fov == 60.0

    def test_custom_construction(self) -> None:
        cam = Camera(position=(1, 2, 3), pitch=10, yaw=20, roll=5, fov=90)
        assert cam._pos == [1, 2, 3]
        assert cam.pitch == 10
        assert cam.fov == 90

    def test_fov_clamped_on_construction(self) -> None:
        # Canonical [1, 179] band (CameraConfig/solver), not the old [10, 170].
        assert Camera(fov=0.5).fov == 1.0
        assert Camera(fov=200.0).fov == 179.0
        assert Camera(fov=175.0).fov == 175.0  # in-band: must not narrow to 170

    def test_from_config(self) -> None:
        cfg = CameraConfig(pos_x=1, pos_y=2, pos_z=3, pitch=15, yaw=30, roll=5, fov=80)
        cam = Camera.from_config(cfg)
        assert cam._pos == [1, 2, 3]
        assert cam.pitch == 15
        assert cam.yaw == 30
        assert cam.roll == 5
        assert cam.fov == 80

    def test_to_config_roundtrip(self) -> None:
        cam = Camera(position=(5, 6, 7), pitch=-10, yaw=45, roll=2, fov=75)
        cfg = cam.to_config()
        assert cfg.pos_x == 5
        assert cfg.pos_y == 6
        assert cfg.pos_z == 7
        assert cfg.pitch == -10
        assert cfg.yaw == 45
        assert cfg.roll == 2
        assert cfg.fov == 75

    def test_move(self) -> None:
        cam = Camera(position=(0, 0, 0))
        cam.move(1.0, 2.0, 3.0)
        assert cam._pos == [1.0, 2.0, 3.0]
        cam.move(-0.5, -0.5, -0.5)
        assert cam._pos == [0.5, 1.5, 2.5]

    def test_rotate_clamps_pitch(self) -> None:
        cam = Camera(pitch=85)
        cam.rotate(10, 0)
        assert cam.pitch == 89.0  # clamped at 89

        cam2 = Camera(pitch=-85)
        cam2.rotate(-10, 0)
        assert cam2.pitch == -89.0  # clamped at -89

    def test_rotate_yaw_unrestricted(self) -> None:
        cam = Camera(yaw=350)
        cam.rotate(0, 20)
        assert cam.yaw == 370

    def test_rotate_roll(self) -> None:
        cam = Camera(roll=0)
        cam.rotate(0, 0, 15)
        assert cam.roll == 15

    def test_set_fov_clamps(self) -> None:
        cam = Camera()
        cam.set_fov(0.5)
        assert cam.fov == 1.0
        cam.set_fov(200)
        assert cam.fov == 179.0
        cam.set_fov(5)
        assert cam.fov == 5.0  # in-band: must not narrow to 10
        cam.set_fov(90)
        assert cam.fov == 90.0

    def test_apply_config(self) -> None:
        cam = Camera()
        cfg = CameraConfig(pos_x=10, pos_y=20, pos_z=30, pitch=5, yaw=15, roll=3, fov=100)
        cam.apply_config(cfg)
        assert cam._pos == [10, 20, 30]
        assert cam.pitch == 5
        assert cam.yaw == 15
        assert cam.roll == 3
        assert cam.fov == 100

    def test_apply_config_preserves_in_band_fov(self) -> None:
        # CameraConfig already clamps to [1, 179]; apply_config must not
        # re-narrow an in-band value (the old [10, 170] clamp did).
        cam = Camera()
        cam.apply_config(CameraConfig(fov=175.0))
        assert cam.fov == 175.0
