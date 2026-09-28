# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Pi camera in config.txt: parsing, rewriting, the state the page shows, and the root CLI."""

from __future__ import annotations

import ast
import runpy
import stat
import sys
from pathlib import Path

import pytest

import openfollow.privilege.camera_config as cc
from openfollow.privilege.camera_config import (
    AUTOMATIC,
    BLOCK_BEGIN,
    BLOCK_END,
    TAKEN_OVER,
    Camera,
    MalformedBlockError,
    camera_label,
    camera_lines,
    configured_camera,
    is_raspberry_pi,
    main,
    parse_token,
    read_camera_setup,
    rewrite_config,
    sensor_label,
)

pytestmark = pytest.mark.unit

_APPLIANCE = """# stock Pi OS lines
camera_auto_detect=1
dtoverlay=vc4-kms-v3d

[all]
# --- OpenFollow appliance ---
dtoverlay=disable-wifi
"""
OV = Camera("ov5647", "cam0")
IMX = Camera("imx708_wide", "cam1")


class TestTokens:
    @pytest.mark.parametrize(
        ("token", "camera"),
        [(AUTOMATIC, None), ("ov5647,cam0", OV), ("imx708_wide,cam1", IMX), ("arducam-64mp,cam0", None)],
    )
    def test_valid(self, token: str, camera: Camera | None) -> None:
        parsed = parse_token(token)
        if token.startswith("arducam"):
            assert parsed == Camera("arducam-64mp", "cam0")
        else:
            assert parsed == camera

    @pytest.mark.parametrize(
        "token",
        [
            "",
            "ov5647",
            "ov5647,cam2",
            "ov5647,CAM0",
            "OV5647,cam0",
            "vc4-kms-v3d,cam0",
            "../imx219,cam0",
            "imx219 cam0",
            "imx219,cam0,rotation=180",
            "automatic,cam0",
            "imx219,cam0\n",
        ],
    )
    def test_invalid(self, token: str) -> None:
        with pytest.raises(ValueError, match="not a camera choice"):
            parse_token(token)

    def test_token_round_trips(self) -> None:
        assert parse_token(OV.token()) == OV

    @pytest.mark.parametrize(
        ("token", "label"),
        [
            ("ov5647,cam0", "Camera Module 1 (ov5647) on CAM/DISP 0"),
            ("imx708,cam1", "Camera Module 3 (imx708) on CAM/DISP 1"),
            ("imx290,cam0", "imx290 on CAM/DISP 0"),
            ("imx219,", "Camera Module 2 (imx219) on the default connector"),
        ],
    )
    def test_label_reads_as_the_board_prints_it(self, token: str, label: str) -> None:
        assert camera_label(token) == label

    @pytest.mark.parametrize(
        ("sensor", "label"),
        [
            ("imx477", "HQ Camera (imx477)"),
            ("imx296", "Global Shutter Camera (imx296)"),
            ("imx500", "AI Camera (imx500)"),
            ("imx500-pi5", "AI Camera for Pi 5 (imx500-pi5)"),
            # libcamera names a variant after the sensor.
            ("imx708_wide_noir", "Camera Module 3 (imx708_wide_noir)"),
            ("arducam-pivariety", "arducam-pivariety"),
            ("", ""),
        ],
    )
    def test_sensor_label_names_the_raspberry_pi_module(self, sensor: str, label: str) -> None:
        assert sensor_label(sensor) == label


class TestReadingConfig:
    def test_only_camera_overlays_count(self) -> None:
        text = "dtoverlay=vc4-kms-v3d\ndtoverlay=disable-bt\ndtoverlay=ov5647,cam0\n#dtoverlay=imx219,cam1\n"
        assert camera_lines(text) == [OV]

    def test_extra_parameters_and_a_missing_port(self) -> None:
        text = "dtoverlay=imx708,cam1,rotation=180\n  dtoverlay = imx219\n"
        assert camera_lines(text) == [Camera("imx708", "cam1"), Camera("imx219", "")]

    def test_a_hand_written_line(self) -> None:
        assert configured_camera(_APPLIANCE + "dtoverlay=ov5647,cam0\n") == (OV, False)

    def test_openfollows_block_wins_over_a_hand_written_line(self) -> None:
        text = rewrite_config(_APPLIANCE, IMX) + "dtoverlay=ov5647,cam0\n"
        assert configured_camera(text) == (IMX, True)

    def test_nothing_named(self) -> None:
        assert configured_camera(_APPLIANCE) == (None, False)


class TestRewriting:
    def test_a_camera_gets_a_block_under_all_with_auto_detect_off(self) -> None:
        out = rewrite_config(_APPLIANCE, OV)
        assert out.startswith(_APPLIANCE.rstrip("\n"))
        assert out.endswith(f"\n\n{BLOCK_BEGIN}\n[all]\ncamera_auto_detect=0\ndtoverlay=ov5647,cam0\n{BLOCK_END}\n")
        # Stock lines outside the block are untouched, auto-detect included.
        assert "\ncamera_auto_detect=1\n" in out

    def test_changing_replaces_the_block(self) -> None:
        out = rewrite_config(rewrite_config(_APPLIANCE, OV), IMX)
        assert out.count(BLOCK_BEGIN) == 1
        assert camera_lines(out) == [IMX]

    def test_automatic_removes_the_block(self) -> None:
        assert rewrite_config(rewrite_config(_APPLIANCE, OV), None) == _APPLIANCE

    def test_a_hand_written_line_is_taken_over_not_doubled(self) -> None:
        out = rewrite_config(_APPLIANCE + "dtoverlay=ov5647,cam0\n", IMX)
        assert f"\n{TAKEN_OVER}dtoverlay=ov5647,cam0\n" in out
        assert camera_lines(out) == [IMX]

    def test_it_is_idempotent(self) -> None:
        once = rewrite_config(_APPLIANCE, OV)
        assert rewrite_config(once, OV) == once

    def test_an_empty_file(self) -> None:
        assert camera_lines(rewrite_config("", OV)) == [OV]


# Markers that don't pair up. An unclosed block would claim every later line.
_UNPAIRED = [
    f"{BLOCK_BEGIN}\ndtoverlay=ov5647,cam0\n[pi5]\ndtoverlay=vc4-kms-v3d\n",
    f"dtoverlay=vc4-kms-v3d\n{BLOCK_END}\n",
    f"{BLOCK_BEGIN}\n{BLOCK_BEGIN}\ndtoverlay=ov5647,cam0\n{BLOCK_END}\n",
    f"{BLOCK_BEGIN}\ndtoverlay=ov5647,cam0\n{BLOCK_END}\n{BLOCK_END}\n",
]
_UNPAIRED_IDS = ["unclosed", "end-without-begin", "begin-twice", "end-twice"]


class TestMalformedBlock:
    @pytest.mark.parametrize("block", _UNPAIRED, ids=_UNPAIRED_IDS)
    def test_rewriting_refuses(self, block: str) -> None:
        with pytest.raises(MalformedBlockError):
            rewrite_config(_APPLIANCE + block, IMX)

    def test_two_complete_blocks_become_one(self) -> None:
        text = rewrite_config(_APPLIANCE, OV) + f"{BLOCK_BEGIN}\ndtoverlay=imx219,cam1\n{BLOCK_END}\n"
        out = rewrite_config(text, IMX)
        assert out.count(BLOCK_BEGIN) == 1
        assert camera_lines(out) == [IMX]


def _station(tmp_path: Path, config: str = _APPLIANCE, *, pi: bool = True, helper: bool = True) -> dict[str, Path]:
    paths = {
        "config_path": tmp_path / "config.txt",
        "overlays_dir": tmp_path / "overlays",
        "model_path": tmp_path / "model",
        "helper_path": tmp_path / "camera-setup",
        "live_file": tmp_path / "run" / "camera-live",
        "boot_file": tmp_path / "run" / "camera-boot",
    }
    paths["config_path"].write_text(config)
    paths["overlays_dir"].mkdir()
    for name in ("ov5647", "imx708_wide", "imx219", "vc4-kms-v3d", "disable-wifi"):
        (paths["overlays_dir"] / f"{name}.dtbo").write_bytes(b"")
    (paths["overlays_dir"] / "README").write_text("")
    paths["model_path"].write_bytes(b"Raspberry Pi Compute Module 5 Rev 1.0\x00" if pi else b"Generic x86\x00")
    if helper:
        paths["helper_path"].write_text("")
    return paths


class TestStateRead:
    def test_not_a_pi(self, tmp_path: Path) -> None:
        state = read_camera_setup(**_station(tmp_path, pi=False))
        assert (state.available, state.reason) == (False, "Camera setup is for Raspberry Pi stations.")

    def test_no_boot_configuration(self, tmp_path: Path) -> None:
        paths = _station(tmp_path)
        paths["config_path"].unlink()
        state = read_camera_setup(**paths)
        assert (state.available, state.reason) == (False, "This station has no Raspberry Pi boot configuration.")

    def test_no_helper_installed(self, tmp_path: Path) -> None:
        state = read_camera_setup(**_station(tmp_path, helper=False))
        assert (state.available, state.reason) == (False, "Camera setup is not installed on this station.")

    @pytest.mark.parametrize("block", _UNPAIRED, ids=_UNPAIRED_IDS)
    def test_unpaired_markers_offer_no_setup(self, tmp_path: Path, block: str) -> None:
        state = read_camera_setup(**_station(tmp_path, _APPLIANCE + block))
        assert (state.available, state.reason) == (False, str(MalformedBlockError()))

    def test_lists_only_camera_overlays(self, tmp_path: Path) -> None:
        assert read_camera_setup(**_station(tmp_path)).sensors == ("imx219", "imx708_wide", "ov5647")

    def test_no_overlays_directory_leaves_only_automatic(self, tmp_path: Path) -> None:
        paths = _station(tmp_path)
        for entry in paths["overlays_dir"].iterdir():
            entry.unlink()
        paths["overlays_dir"].rmdir()
        state = read_camera_setup(**paths)
        assert (state.available, state.sensors) == (True, ())

    def test_a_camera_named_at_boot_is_running(self, tmp_path: Path) -> None:
        state = read_camera_setup(**_station(tmp_path, rewrite_config(_APPLIANCE, OV)))
        assert (state.configured, state.managed, state.active, state.live, state.pending) == (
            OV,
            True,
            (OV,),
            None,
            False,
        )

    def test_a_hand_written_camera(self, tmp_path: Path) -> None:
        state = read_camera_setup(**_station(tmp_path, _APPLIANCE + "dtoverlay=ov5647,cam0\n"))
        assert (state.configured, state.managed, state.pending) == (OV, False, False)

    def test_a_camera_started_live_is_running_not_pending(self, tmp_path: Path) -> None:
        paths = _station(tmp_path, rewrite_config(_APPLIANCE, OV))
        paths["boot_file"].parent.mkdir()
        paths["boot_file"].write_text("\n")  # nothing named at boot
        paths["live_file"].write_text("ov5647,cam0\n")
        state = read_camera_setup(**paths)
        assert (state.active, state.live, state.pending) == ((OV,), OV, False)

    def test_changing_a_boot_camera_waits_for_a_restart(self, tmp_path: Path) -> None:
        paths = _station(tmp_path, rewrite_config(_APPLIANCE, IMX))
        paths["boot_file"].parent.mkdir()
        paths["boot_file"].write_text("dtoverlay=ov5647,cam0\n")
        state = read_camera_setup(**paths)
        assert (state.configured, state.active, state.pending) == (IMX, (OV,), True)

    def test_back_to_automatic_with_a_boot_camera_waits_too(self, tmp_path: Path) -> None:
        paths = _station(tmp_path)
        paths["boot_file"].parent.mkdir()
        paths["boot_file"].write_text("dtoverlay=ov5647,cam0\n")
        assert read_camera_setup(**paths).pending is True

    def test_a_garbled_live_record_is_ignored(self, tmp_path: Path) -> None:
        paths = _station(tmp_path)
        paths["live_file"].parent.mkdir()
        paths["live_file"].write_text("rm -rf /\n")
        assert read_camera_setup(**paths).live is None


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"Raspberry Pi 5 Model B Rev 1.1\x00", True),
        (b"Raspberry Pi Compute Module 5\x00", True),
        (b"ROCK 5B\x00", False),
    ],
)
def test_is_raspberry_pi(tmp_path: Path, content: bytes, expected: bool) -> None:
    model = tmp_path / "model"
    model.write_bytes(content)
    assert is_raspberry_pi(model) is expected


def test_is_raspberry_pi_without_a_device_tree(tmp_path: Path) -> None:
    assert is_raspberry_pi(tmp_path / "missing") is False


def _fake_dtoverlay(tmp_path: Path, *, exit_code: int = 0) -> tuple[str, Path]:
    log = tmp_path / "dtoverlay.log"
    script = tmp_path / "dtoverlay"
    script.write_text(f'#!/bin/sh\necho "$@" >> {log}\necho "dtoverlay said no" >&2\nexit {exit_code}\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script), log


def _cli(paths: dict[str, Path], dtoverlay: str = "/bin/false") -> dict[str, object]:
    return {
        "config_path": paths["config_path"],
        "overlays_dir": paths["overlays_dir"],
        "live_file": paths["live_file"],
        "boot_file": paths["boot_file"],
        "dtoverlay": dtoverlay,
    }


class TestRootHelper:
    @pytest.mark.parametrize(
        "argv",
        [
            [],
            ["write"],
            ["write", "a", "b"],
            ["delete", "ov5647,cam0"],
            ["write", "../etc,cam0"],
            ["load", AUTOMATIC],
            ["unload", AUTOMATIC],
            ["load", "imx477,cam0"],
        ],
        ids=[
            "no-args",
            "one-arg",
            "three-args",
            "unknown-command",
            "bad-token",
            "load-auto",
            "unload-auto",
            "no-such-overlay",
        ],
    )
    def test_refuses_bad_arguments(self, tmp_path: Path, argv: list[str], capsys) -> None:
        paths = _station(tmp_path)
        assert main(argv, **_cli(paths)) == 2
        assert paths["config_path"].read_text() == _APPLIANCE
        assert capsys.readouterr().err

    def test_write_keeps_the_boot_state_once_and_a_backup(self, tmp_path: Path) -> None:
        paths = _station(tmp_path, _APPLIANCE + "dtoverlay=ov5647,cam0\n")
        assert main(["write", "imx708_wide,cam1"], **_cli(paths)) == 0
        assert configured_camera(paths["config_path"].read_text()) == (IMX, True)
        assert paths["boot_file"].read_text() == "dtoverlay=ov5647,cam0\n"
        assert (tmp_path / "config.txt.openfollow-bak").read_text() == _APPLIANCE + "dtoverlay=ov5647,cam0\n"
        assert main(["write", AUTOMATIC], **_cli(paths)) == 0
        assert paths["boot_file"].read_text() == "dtoverlay=ov5647,cam0\n"  # still what the station booted with
        assert configured_camera(paths["config_path"].read_text()) == (None, False)

    @pytest.mark.parametrize("block", _UNPAIRED, ids=_UNPAIRED_IDS)
    def test_unpaired_markers_write_nothing(self, tmp_path: Path, block: str, capsys) -> None:  # noqa: ANN001
        paths = _station(tmp_path, _APPLIANCE + block)
        assert main(["write", "imx708_wide,cam1"], **_cli(paths)) == 1
        assert paths["config_path"].read_text() == _APPLIANCE + block
        assert not (tmp_path / "config.txt.openfollow-bak").exists()
        assert not paths["boot_file"].exists()
        assert "missing or extra marker" in capsys.readouterr().err

    def test_a_failed_write_leaves_config_txt_whole(self, tmp_path: Path, monkeypatch, capsys) -> None:  # noqa: ANN001
        paths = _station(tmp_path)

        def _disk_full(_fd: int) -> None:
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(cc.os, "fsync", _disk_full)
        assert main(["write", "ov5647,cam0"], **_cli(paths)) == 1
        assert paths["config_path"].read_text() == _APPLIANCE
        assert not list(tmp_path.glob(".config.txt.*"))
        assert capsys.readouterr().err == f"could not write {paths['config_path']}: No space left on device\n"

    def test_load_starts_the_camera_and_records_it(self, tmp_path: Path) -> None:
        paths = _station(tmp_path)
        dtoverlay, log = _fake_dtoverlay(tmp_path)
        assert main(["load", "ov5647,cam0"], **_cli(paths, dtoverlay)) == 0
        assert log.read_text() == "ov5647 cam0\n"
        assert paths["live_file"].read_text() == "ov5647,cam0\n"

    def test_a_failed_load_records_nothing(self, tmp_path: Path, capsys) -> None:
        paths = _station(tmp_path)
        dtoverlay, _log = _fake_dtoverlay(tmp_path, exit_code=1)
        assert main(["load", "ov5647,cam0"], **_cli(paths, dtoverlay)) == 1
        assert not paths["live_file"].exists()
        assert "dtoverlay said no" in capsys.readouterr().err

    def test_a_missing_dtoverlay_is_reported(self, tmp_path: Path, capsys) -> None:
        paths = _station(tmp_path)
        assert main(["load", "ov5647,cam0"], **_cli(paths, str(tmp_path / "nope"))) == 1
        assert "dtoverlay failed" in capsys.readouterr().err

    def test_unload_only_what_it_loaded(self, tmp_path: Path) -> None:
        paths = _station(tmp_path)
        dtoverlay, log = _fake_dtoverlay(tmp_path)
        assert main(["unload", "ov5647,cam0"], **_cli(paths, dtoverlay)) == 1
        assert not log.exists()
        main(["load", "ov5647,cam0"], **_cli(paths, dtoverlay))
        assert main(["unload", "ov5647,cam0"], **_cli(paths, dtoverlay)) == 0
        assert log.read_text() == "ov5647 cam0\n-r ov5647\n"
        assert not paths["live_file"].exists()

    def test_runs_as_the_installed_helper(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        """The .deb installs this very file as the helper; run it that way."""
        monkeypatch.setattr(sys, "argv", ["camera-setup"])
        with pytest.raises(SystemExit) as exit_info:
            runpy.run_path(cc.__file__, run_name="__main__")
        assert exit_info.value.code == 2
        assert "usage: camera-setup" in capsys.readouterr().err


def test_the_helper_file_is_standard_library_only() -> None:
    """Root runs this file with the system interpreter, never code from the venv."""
    source = Path(cc.__file__).read_text()
    assert source.startswith("#!/usr/bin/python3 -I\n")
    tree = ast.parse(source)
    imported = {
        (node.module if isinstance(node, ast.ImportFrom) else alias.name).split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [node])
    }
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}


def test_the_package_installs_it_as_the_helper() -> None:
    build = (Path(cc.__file__).resolve().parents[2] / "packaging" / "build-deb.sh").read_text()
    assert 'install -m 0755 "$REPO_ROOT/openfollow/privilege/camera_config.py"   "$SHARE/camera-setup"' in build
