#!/usr/bin/python3 -I
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The CSI camera a Raspberry Pi's boot configuration names, and loading it live.

A Compute Module never detects a camera by itself, and a regular Pi only
detects Raspberry Pi's own modules, so ``config.txt`` has to name the sensor
and connector (``dtoverlay=ov5647,cam0``). The service reads that file as
itself; it changes it only through the root helper
(``/usr/share/openfollow/camera-setup``), which runs :func:`main`.

That helper is a root-owned copy of this very file, run by the system
interpreter in isolated mode, so root never imports code from the venv, which
the service user may be able to write. Keep this module standard-library only.

The kernel can load a camera overlay while running (``dtoverlay``), so a
camera set up where none was loaded at boot starts without a reboot. One
loaded at boot cannot be unloaded while running: changing it waits for a
restart.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "AUTOMATIC",
    "CAMERA_TOKEN_RE",
    "CHOICE_TOKEN_RE",
    "Camera",
    "CameraSetupState",
    "HELPER_PATH",
    "MODULE_NAMES",
    "camera_label",
    "camera_lines",
    "configured_camera",
    "is_raspberry_pi",
    "main",
    "parse_token",
    "read_camera_setup",
    "rewrite_config",
    "sensor_label",
]

CONFIG_PATH = Path("/boot/firmware/config.txt")
OVERLAYS_DIR = Path("/boot/firmware/overlays")
MODEL_PATH = Path("/proc/device-tree/model")
HELPER_PATH = Path("/usr/share/openfollow/camera-setup")
DTOVERLAY = "/usr/bin/dtoverlay"
# tmpfs: whatever the helper records here describes this boot only.
RUN_DIR = Path("/run/openfollow-camera")
LIVE_FILE = RUN_DIR / "camera-live"
BOOT_FILE = RUN_DIR / "camera-boot"

AUTOMATIC = "automatic"
BLOCK_BEGIN = "# --- OpenFollow camera ---"
BLOCK_END = "# --- end OpenFollow camera ---"
# Prefix for a hand-written camera line OpenFollow's block replaced.
TAKEN_OVER = "# Replaced by the OpenFollow camera setup: "

# A camera overlay, by file name. The helper re-checks the file exists too.
# Plain groups only: sudoers matches these as POSIX regexes.
_SENSOR = r"(imx|ov|arducam|irs)[a-z0-9_-]*"
CAMERA_TOKEN_RE = rf"^{_SENSOR},cam[01]$"
CHOICE_TOKEN_RE = rf"^({AUTOMATIC}|{_SENSOR},cam[01])$"
_SENSOR_RE = re.compile(rf"^{_SENSOR}$")
_DTOVERLAY_LINE = re.compile(r"^\s*dtoverlay\s*=\s*([^\s#]+)")


@dataclass(frozen=True)
class Camera:
    """One camera overlay: its sensor and connector (``""`` for the board's default)."""

    sensor: str
    port: str

    def token(self) -> str:
        return f"{self.sensor},{self.port}"


@dataclass(frozen=True)
class CameraSetupState:
    """What the page shows about the station's camera configuration."""

    # False: no setup on this station, and ``reason`` says why.
    available: bool
    reason: str = ""
    sensors: tuple[str, ...] = ()
    # What config.txt names now, and whether OpenFollow's block names it.
    configured: Camera | None = None
    managed: bool = False
    # Loaded now: from boot, and live by OpenFollow since.
    active: tuple[Camera, ...] = ()
    live: Camera | None = None
    # config.txt names something other than what is loaded: a restart applies it.
    pending: bool = False


# Sensor overlay -> Raspberry Pi product, in the order the camera list offers them.
MODULE_NAMES = {
    "imx708": "Camera Module 3",
    "imx219": "Camera Module 2",
    "ov5647": "Camera Module 1",
    "imx477": "HQ Camera",
    "imx296": "Global Shutter Camera",
    "imx500": "AI Camera",
    "imx500-pi5": "AI Camera for Pi 5",
}


def sensor_label(sensor: str) -> str:
    """``"imx708"`` -> ``"Camera Module 3 (imx708)"``; libcamera's ``imx708_wide`` keeps its suffix."""
    name = MODULE_NAMES.get(sensor) or MODULE_NAMES.get(sensor.split("_", 1)[0])
    return f"{name} ({sensor})" if name else sensor


def camera_label(token: str) -> str:
    """``"ov5647,cam0"`` -> ``"Camera Module 1 (ov5647) on CAM/DISP 0"``, as the board prints the connector."""
    sensor, _, port = token.partition(",")
    where = f"CAM/DISP {port[-1]}" if port in ("cam0", "cam1") else "the default connector"
    return f"{sensor_label(sensor)} on {where}"


def parse_token(token: str) -> Camera | None:
    """``"ov5647,cam0"`` -> a camera, ``"automatic"`` -> None; anything else raises ValueError."""
    if not re.fullmatch(CHOICE_TOKEN_RE, token):
        raise ValueError(f"not a camera choice: {token!r}")
    if token == AUTOMATIC:
        return None
    sensor, port = token.split(",", 1)
    return Camera(sensor, port)


def _camera_from_line(line: str) -> Camera | None:
    match = _DTOVERLAY_LINE.match(line)
    if match is None:
        return None
    name, *params = match.group(1).split(",")
    if not _SENSOR_RE.fullmatch(name):
        return None
    port = next((p for p in params if p in ("cam0", "cam1")), "")
    return Camera(name, port)


def _split_block(lines: list[str]) -> tuple[list[str], list[str]]:
    """``config.txt`` lines outside OpenFollow's block, and the lines inside it."""
    outside: list[str] = []
    inside: list[str] = []
    in_block = False
    for line in lines:
        stripped = line.strip()
        if stripped == BLOCK_BEGIN:
            in_block = True
            continue
        if stripped == BLOCK_END:
            in_block = False
            continue
        (inside if in_block else outside).append(line)
    return outside, inside


def camera_lines(text: str) -> list[Camera]:
    """Every active camera ``dtoverlay`` line, in file order."""
    cameras = (_camera_from_line(line) for line in text.splitlines())
    return [camera for camera in cameras if camera is not None]


def configured_camera(text: str) -> tuple[Camera | None, bool]:
    """The camera ``config.txt`` names, and whether OpenFollow's block names it."""
    outside, inside = _split_block(text.splitlines())
    managed = camera_lines("\n".join(inside))
    if managed:
        return managed[-1], True
    by_hand = camera_lines("\n".join(outside))
    return (by_hand[-1] if by_hand else None), False


def rewrite_config(text: str, camera: Camera | None) -> str:
    """``config.txt`` naming *camera*, or leaving it to auto-detect when None.

    OpenFollow's block replaces the previous one; a hand-written camera line is
    commented out with a marker, so a connector never carries two sensors. The
    block turns auto-detect off (the last setting wins), which removing the
    block restores.
    """
    outside, _inside = _split_block(text.splitlines())
    kept = [TAKEN_OVER + line.strip() if _camera_from_line(line) else line for line in outside]
    while kept and not kept[-1].strip():
        kept.pop()
    if camera is not None:
        # ``[all]`` inside the markers, so removing the block leaves no trace.
        kept += ["", BLOCK_BEGIN, "[all]", "camera_auto_detect=0", f"dtoverlay={camera.token()}", BLOCK_END]
    return "\n".join(kept) + "\n"


def is_raspberry_pi(model_path: Path = MODEL_PATH) -> bool:
    try:
        return model_path.read_bytes().startswith(b"Raspberry Pi")
    except OSError:
        return False


def _sensors(overlays_dir: Path) -> tuple[str, ...]:
    try:
        names = {entry.name.removesuffix(".dtbo") for entry in overlays_dir.iterdir() if entry.suffix == ".dtbo"}
    except OSError:
        return ()
    return tuple(sorted(name for name in names if _SENSOR_RE.fullmatch(name)))


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def read_camera_setup(
    *,
    config_path: Path = CONFIG_PATH,
    overlays_dir: Path = OVERLAYS_DIR,
    model_path: Path = MODEL_PATH,
    helper_path: Path = HELPER_PATH,
    live_file: Path = LIVE_FILE,
    boot_file: Path = BOOT_FILE,
) -> CameraSetupState:
    """The station's camera configuration, read without privileges."""
    if not is_raspberry_pi(model_path):
        return CameraSetupState(available=False, reason="Camera setup is for Raspberry Pi stations.")
    text = _read(config_path)
    if text is None:
        return CameraSetupState(available=False, reason="This station has no Raspberry Pi boot configuration.")
    if not helper_path.exists():
        return CameraSetupState(available=False, reason="Camera setup is not installed on this station.")
    configured, managed = configured_camera(text)
    # Before its first change in a boot the helper keeps config.txt's camera
    # lines as they were at boot; until then the file itself is that state.
    boot_text = _read(boot_file)
    boot = camera_lines(text if boot_text is None else boot_text)
    live_token = (_read(live_file) or "").strip()
    live = parse_token(live_token) if re.fullmatch(CAMERA_TOKEN_RE, live_token) else None
    active = tuple(boot) + ((live,) if live is not None and live not in boot else ())
    wanted = (configured,) if configured is not None else ()
    return CameraSetupState(
        available=True,
        sensors=_sensors(overlays_dir),
        configured=configured,
        managed=managed,
        active=active,
        live=live,
        pending=set(wanted) != set(active),
    )


# -- the root helper ----------------------------------------------------------


def _write_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".config.txt.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _record(path: Path, text: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if text is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(text, encoding="utf-8")


def main(
    argv: list[str],
    *,
    config_path: Path = CONFIG_PATH,
    overlays_dir: Path = OVERLAYS_DIR,
    live_file: Path = LIVE_FILE,
    boot_file: Path = BOOT_FILE,
    dtoverlay: str = DTOVERLAY,
) -> int:
    """``camera-setup write|load|unload <token>``, run as root through sudo.

    The sudoers rule already bounds the token; it is checked again here, with
    the overlay file's existence, because this is the process that writes boot
    configuration.
    """
    if len(argv) != 2 or argv[0] not in ("write", "load", "unload"):
        print("usage: camera-setup write|load|unload <sensor,camN|automatic>", file=sys.stderr)
        return 2
    command, token = argv
    try:
        camera = parse_token(token)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    if camera is not None and not (overlays_dir / f"{camera.sensor}.dtbo").is_file():
        print(f"no overlay named {camera.sensor} on this station", file=sys.stderr)
        return 2
    if command == "write":
        try:
            text = config_path.read_text(encoding="utf-8")
            if not boot_file.exists():
                _record(boot_file, "".join(line + "\n" for line in text.splitlines() if _camera_from_line(line)))
            config_path.with_name(config_path.name + ".openfollow-bak").write_text(text, encoding="utf-8")
            _write_atomic(config_path, rewrite_config(text, camera))
        except OSError as exc:
            print(f"could not write {config_path}: {exc.strerror or exc}", file=sys.stderr)
            return 1
        return 0
    if camera is None:
        print(f"{command} needs a sensor and connector", file=sys.stderr)
        return 2
    if command == "load":
        argv_run = [dtoverlay, camera.sensor, camera.port]
    elif (_read(live_file) or "").strip() != camera.token():
        print(f"{camera.token()} was not loaded live by OpenFollow", file=sys.stderr)
        return 1
    else:
        argv_run = [dtoverlay, "-r", camera.sensor]
    try:
        subprocess.run(argv_run, check=True, timeout=30, capture_output=True, text=True)  # noqa: S603
    except (subprocess.SubprocessError, OSError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        print(f"dtoverlay failed: {detail.strip()}", file=sys.stderr)
        return 1
    _record(live_file, camera.token() + "\n" if command == "load" else None)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
