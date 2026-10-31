# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""Which platforms are supported, and when the warning shows."""

from __future__ import annotations

from pathlib import Path

import pytest

from openfollow import platform_support
from openfollow.configuration import AppConfig
from openfollow.platform_support import PlatformSupport, detect

pytestmark = pytest.mark.unit

DEBIAN_13 = 'PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\nNAME="Debian GNU/Linux"\nVERSION_ID="13"\nID=debian\n'


def _detect(tmp_path: Path, *, model: bytes | None = None, os_release: str | None = None) -> PlatformSupport:
    model_path = tmp_path / "model"
    release_path = tmp_path / "os-release"
    if model is not None:
        model_path.write_bytes(model)
    if os_release is not None:
        release_path.write_text(os_release, encoding="utf-8")
    return detect("linux", model_paths=(model_path,), os_release_paths=(release_path,))


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (b"Raspberry Pi 5 Model B Rev 1.0\x00", PlatformSupport(True, "Raspberry Pi 5 Model B")),
        (b"Raspberry Pi Compute Module 5 Rev 1.0\x00", PlatformSupport(True, "Raspberry Pi Compute Module 5")),
        (b"Raspberry Pi 500 Rev 1.0\x00", PlatformSupport(True, "Raspberry Pi 500")),
        (b"Raspberry Pi 6 Model B Rev 1.0\x00", PlatformSupport(True, "Raspberry Pi 6 Model B")),
        (b"Raspberry Pi 4 Model B Rev 1.4\x00", PlatformSupport(False, "Raspberry Pi 4 Model B")),
        (b"Raspberry Pi Compute Module 4 Rev 1.0\x00", PlatformSupport(False, "Raspberry Pi Compute Module 4")),
        (b"Raspberry Pi 400 Rev 1.0\x00", PlatformSupport(False, "Raspberry Pi 400")),
        (b"Raspberry Pi 3 Model B Plus Rev 1.3\x00", PlatformSupport(False, "Raspberry Pi 3 Model B Plus")),
        (b"Raspberry Pi Zero 2 W Rev 1.0\x00", PlatformSupport(False, "Raspberry Pi Zero 2 W")),
        (b"Raspberry Pi Model B Rev 2\x00", PlatformSupport(False, "Raspberry Pi Model B")),
    ],
    ids=["pi5", "cm5", "pi500", "pi6", "pi4", "cm4", "pi400", "pi3", "zero2", "pi1"],
)
def test_a_pi_is_supported_from_generation_5(tmp_path: Path, model: bytes, expected: PlatformSupport) -> None:
    assert _detect(tmp_path, model=model, os_release=DEBIAN_13) == expected


def test_an_older_pi_stays_unsupported_on_debian_13(tmp_path: Path) -> None:
    assert _detect(tmp_path, model=b"Raspberry Pi 4 Model B Rev 1.4\x00", os_release=DEBIAN_13).supported is False


@pytest.mark.parametrize(
    ("os_release", "expected"),
    [
        (DEBIAN_13, PlatformSupport(True, "Debian GNU/Linux 13 (trixie)")),
        (
            'PRETTY_NAME="Debian GNU/Linux 14 (forky)"\nVERSION_ID="14"\nID=debian\n',
            PlatformSupport(True, "Debian GNU/Linux 14 (forky)"),
        ),
        ('PRETTY_NAME="Debian GNU/Linux forky/sid"\nID=debian\n', PlatformSupport(True, "Debian GNU/Linux forky/sid")),
        (
            'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nVERSION_ID="12"\nID=debian\n',
            PlatformSupport(False, "Debian GNU/Linux 12 (bookworm)"),
        ),
        ('PRETTY_NAME="Debian broken"\nVERSION_ID="abc"\nID=debian\n', PlatformSupport(False, "Debian broken")),
        (
            'PRETTY_NAME="Ubuntu 25.04"\nVERSION_ID="25.04"\nID=ubuntu\nID_LIKE=debian\n',
            PlatformSupport(True, "Ubuntu 25.04"),
        ),
        (
            'PRETTY_NAME="Linux Mint 22"\nID=linuxmint\nID_LIKE="ubuntu debian"\n',
            PlatformSupport(True, "Linux Mint 22"),
        ),
        (
            'PRETTY_NAME="Fedora Linux 42 (Workstation Edition)"\nVERSION_ID=42\nID=fedora\n',
            PlatformSupport(False, "Fedora Linux 42 (Workstation Edition)"),
        ),
        ("NAME=Arch Linux\nID=arch\n", PlatformSupport(False, "Arch Linux")),
        ("# a comment\nno separator\nID_LIKE=\n", PlatformSupport(False, "Linux")),
        ("", PlatformSupport(False, "Linux")),
    ],
    ids=[
        "debian13",
        "debian14",
        "sid",
        "debian12",
        "bad-version",
        "ubuntu",
        "mint",
        "fedora",
        "arch",
        "no-id",
        "empty",
    ],
)
def test_other_hardware_is_supported_on_debian_13_and_its_derivatives(
    tmp_path: Path, os_release: str, expected: PlatformSupport
) -> None:
    assert _detect(tmp_path, os_release=os_release) == expected


def test_no_os_release_is_an_unsupported_linux(tmp_path: Path) -> None:
    assert _detect(tmp_path) == PlatformSupport(False, "Linux")


def test_the_second_os_release_is_read_when_the_first_is_missing(tmp_path: Path) -> None:
    fallback = tmp_path / "usr-lib-os-release"
    fallback.write_text(DEBIAN_13, encoding="utf-8")
    support = detect("linux", model_paths=(tmp_path / "absent",), os_release_paths=(tmp_path / "absent", fallback))
    assert support.supported is True


def test_an_unreadable_model_falls_back_to_the_os(tmp_path: Path) -> None:
    unreadable = tmp_path / "model"
    unreadable.mkdir()
    release = tmp_path / "os-release"
    release.write_text(DEBIAN_13, encoding="utf-8")
    assert detect("linux", model_paths=(unreadable,), os_release_paths=(release,)).supported is True


@pytest.mark.parametrize(
    ("platform", "name"),
    [("darwin", "macOS"), ("win32", "Windows"), ("freebsd14", "freebsd14")],
)
def test_every_other_os_is_unsupported(platform: str, name: str) -> None:
    assert detect(platform) == PlatformSupport(False, name)


def test_the_title_names_the_platform() -> None:
    assert PlatformSupport(False, "Windows").title == "Unsupported platform: Windows"


@pytest.mark.parametrize(
    ("support", "developer_mode", "shown"),
    [
        (PlatformSupport(False, "macOS"), False, True),
        (PlatformSupport(False, "macOS"), True, False),
        (PlatformSupport(True, "Raspberry Pi 5"), False, False),
    ],
    ids=["unsupported", "developer-mode", "supported"],
)
def test_developer_mode_hides_the_warning(
    monkeypatch: pytest.MonkeyPatch, support: PlatformSupport, developer_mode: bool, shown: bool
) -> None:
    monkeypatch.setattr(platform_support, "current", lambda: support)
    cfg = AppConfig()
    cfg.ui.developer_mode = developer_mode
    assert platform_support.warning_shown(cfg) is shown
    assert platform_support.stats_entry(cfg) == {"supported": support.supported, "name": support.name, "warning": shown}
