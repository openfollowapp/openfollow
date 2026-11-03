# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""Diagnostics bundle section E11: GPUs, connectors, the compositor's layout, kanshi and the session log."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from openfollow.wayland_outputs import OutputHead, OutputMode, WaylandError
from openfollow.web import diagnostics as diag
from openfollow.web import diagnostics_display as dd

pytestmark = pytest.mark.unit

_ENV = {"WAYLAND_DISPLAY": "wayland-0", "XDG_RUNTIME_DIR": "/run/user/1000"}
_1080 = OutputMode(1920, 1080, 60000, True)
_PANEL = OutputMode(2560, 1600, 165000, True)


def _write(node: Path, **attrs: str) -> None:
    node.mkdir(parents=True, exist_ok=True)
    for name, value in attrs.items():
        (node / name).write_text(value)


def _card(root: Path, name: str, driver: str, **device: str) -> None:
    dev = root / "devices" / f"{name}-dev"
    _write(dev, **device)
    drivers = root / "drivers" / driver
    drivers.mkdir(parents=True, exist_ok=True)
    (dev / "driver").symlink_to(drivers)
    (root / "drm").mkdir(exist_ok=True)
    (root / "drm" / name).mkdir()
    (root / "drm" / name / "device").symlink_to(dev)


def _laptop(tmp_path: Path) -> Path:
    """Two GPUs: the panel on the integrated one, HDMI on the discrete one."""
    _card(tmp_path, "card0", "i915", vendor="0x8086", device="0x46a6", boot_vga="1")
    _card(tmp_path, "card1", "amdgpu", vendor="0x1002", device="0x7480", boot_vga="0")
    drm = tmp_path / "drm"
    _write(drm / "card0-eDP-1", status="connected", enabled="disabled", dpms="Off", modes="2560x1600\n2560x1600\n")
    _write(drm / "card0-DP-1", status="disconnected", enabled="disabled", dpms="Off", modes="")
    _write(
        drm / "card1-HDMI-A-1",
        status="connected",
        enabled="enabled",
        dpms="On",
        modes="\n".join(
            ["3840x2160", "1920x1080", "1920x1080", "1600x900", "1280x720", "1024x768", "800x600", "720x480"]
        ),
    )
    (drm / "renderD128").mkdir()
    (drm / "version").write_text("drm 1.1.0")
    return drm


def _head(name: str, *, enabled: bool = True, mode: OutputMode | None = _1080, **kw: object) -> OutputHead:
    fields: dict[str, object] = {
        "name": name,
        "description": "",
        "make": "",
        "model": "",
        "enabled": enabled,
        "current_mode": mode if enabled else None,
        "position": (0, 0),
        "transform": 0,
        "scale": 1.0,
        "modes": (mode,) if mode else (),
    }
    fields.update(kw)
    return OutputHead(**fields)  # type: ignore[arg-type]


def _reader(heads: list[OutputHead]) -> Callable[[Path, float], list[OutputHead]]:
    return lambda _path, _timeout: heads


# ---------------------------------------------------------------------------
# GPUs and connectors
# ---------------------------------------------------------------------------


def test_drm_names_each_gpu_and_every_connector(tmp_path: Path) -> None:
    rows, connected = dd.collect_drm(_laptop(tmp_path))
    assert rows[:3] == [
        "  GPUs:",
        "    card0     i915 (PCI 8086:46a6, boot display)",
        "    card1     amdgpu (PCI 1002:7480)",
    ]
    assert "    DP-1        card0  disconnected, disabled, dpms Off; modes none" in rows
    assert "    eDP-1       card0  connected, disabled, dpms Off; modes 2560x1600" in rows
    assert (
        "    HDMI-A-1    card1  connected, enabled, dpms On; "
        "modes 3840x2160, 1920x1080, 1600x900, 1280x720, 1024x768, 800x600 (+1 more)"
    ) in rows
    assert not any("renderD128" in row or "version" in row for row in rows)
    assert connected == ["eDP-1", "HDMI-A-1"]


def test_a_platform_gpu_is_named_by_its_device(tmp_path: Path) -> None:
    _card(tmp_path, "card1", "vc4-drm")
    rows, _ = dd.collect_drm(tmp_path / "drm")
    assert rows[1] == "    card1     vc4-drm (card1-dev)"


def test_a_gpu_without_a_driver_says_so(tmp_path: Path) -> None:
    dev = tmp_path / "dev"
    dev.mkdir()
    (tmp_path / "drm" / "card0").mkdir(parents=True)
    (tmp_path / "drm" / "card0" / "device").symlink_to(dev)
    _write(tmp_path / "drm" / "card0-HDMI-A-1")
    rows, connected = dd.collect_drm(tmp_path / "drm")
    assert rows[1] == "    card0     no driver (dev)"
    assert "    HDMI-A-1    card0  status unreadable, ?, dpms ?; modes none" in rows
    assert connected == []


def test_no_drm_device_is_said(tmp_path: Path) -> None:
    (tmp_path / "drm").mkdir()
    rows, connected = dd.collect_drm(tmp_path / "drm")
    assert rows == ["  GPUs:", "    [none: the kernel lists no DRM device]", "", "  Connectors (kernel):", "    [none]"]
    assert connected == []


def test_an_unreadable_drm_class_is_said_not_read_as_no_display(tmp_path: Path) -> None:
    rows, connected = dd.collect_drm(tmp_path / "absent")
    assert rows == [f"  GPUs                         [unavailable: {tmp_path / 'absent'}: No such file or directory]"]
    assert connected == []


# ---------------------------------------------------------------------------
# Compositor layout
# ---------------------------------------------------------------------------


def test_a_connected_display_the_compositor_left_out_is_named() -> None:
    heads = [
        _head("HDMI-A-1", make="Acme", model="Show 27"),
        _head("eDP-1", enabled=False, mode=_PANEL, description="Built-in panel"),
    ]
    rows = dd.collect_layout(["eDP-1", "HDMI-A-1"], environ=_ENV, reader=_reader(heads))
    assert rows == [
        "",
        "  Compositor layout (wlr-output-management):",
        "    HDMI-A-1    enabled, 1920x1080@60.00 Hz, at 0,0, scale 1.00  [Acme Show 27]",
        "    eDP-1       disabled, offers preferred 2560x1600@165.00 Hz  [Built-in panel]",
        "    Arrangement                one output",
        "    Connected, not shown       eDP-1",
    ]


@pytest.mark.parametrize(
    ("heads", "arrangement"),
    [
        ([], "no output enabled: nothing is on screen"),
        ([_head("DP-1", enabled=False)], "no output enabled: nothing is on screen"),
        ([_head("DP-1"), _head("DP-2")], "2 outputs mirrored (all at one position)"),
        (
            [_head("DP-1"), _head("DP-2", position=(1920, 0))],
            "2 outputs extended (side by side: each shows part of the window)",
        ),
    ],
    ids=["none", "all-disabled", "mirrored", "extended"],
)
def test_the_arrangement_tells_mirrored_from_extended(heads: list[OutputHead], arrangement: str) -> None:
    rows = dd.collect_layout([], environ=_ENV, reader=_reader(heads))
    assert f"    Arrangement                {arrangement}" in rows
    assert not any("Connected, not shown" in row for row in rows)


def test_no_head_is_said() -> None:
    rows = dd.collect_layout([], environ=_ENV, reader=_reader([]))
    assert "    [none: the compositor lists no output]" in rows


@pytest.mark.parametrize(
    ("head", "row"),
    [
        (
            _head("DP-1", transform=1, scale=1.5, position=(1920, 0)),
            "    DP-1        enabled, 1920x1080@60.00 Hz, at 1920,0, scale 1.50, transform 1",
        ),
        (_head("DP-1", current_mode=None), "    DP-1        enabled, no current mode, at 0,0, scale 1.00"),
        (_head("", enabled=False, mode=None), "    (unnamed)   disabled, offers 0 modes"),
        (
            _head("DP-1", enabled=False, modes=(OutputMode(1024, 768, 60000, False),)),
            "    DP-1        disabled, offers 1 modes",
        ),
    ],
    ids=["transform", "no-current-mode", "unnamed", "no-preferred"],
)
def test_each_head_row(head: OutputHead, row: str) -> None:
    assert dd.collect_layout([], environ=_ENV, reader=_reader([head]))[2] == row


def test_outside_a_wayland_session_the_layout_is_not_read() -> None:
    def never(_path: Path, _timeout: float) -> list[OutputHead]:
        raise AssertionError("read outside a session")

    assert dd.collect_layout(["HDMI-A-1"], environ={}, reader=never)[-1] == (
        "    [not read: not running in a Wayland session]"
    )


def test_a_failed_read_is_said() -> None:
    def fails(path: Path, timeout: float) -> list[OutputHead]:
        assert path == Path("/run/user/1000/wayland-0")
        assert timeout == 1.5
        raise WaylandError("the compositor does not offer wlr-output-management")

    rows = dd.collect_layout(["HDMI-A-1"], environ=_ENV, timeout_s=1.5, reader=fails)
    assert rows[-1] == "    [unavailable: the compositor does not offer wlr-output-management]"


# ---------------------------------------------------------------------------
# Journal: kanshi and the session log
# ---------------------------------------------------------------------------


def _fake_run(monkeypatch: pytest.MonkeyPatch, answers: dict[str, tuple[int, str]]) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(cmd: list[str], *, timeout_s: float = 0.0, stdout_on_error: bool = False) -> tuple[int, str]:
        calls.append(cmd)
        key = " ".join(cmd[:2])
        return answers[key]

    monkeypatch.setattr(diag, "_run", run)
    return calls


def test_the_journal_read_is_this_boot_of_the_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_run(monkeypatch, {"journalctl -u": (0, "a\nb")})
    assert dd.read_display_journal("openfollow", 3.0) == ["a", "b"]
    (cmd,) = calls
    assert cmd[:4] == ["journalctl", "-u", "openfollow", "-b"]
    assert "--grep" in cmd


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ((1, ""), []),
        ((-1, "[unavailable: journalctl not found]"), "[unavailable: journalctl not found]"),
        ((1, "Failed to get journal access"), "[unavailable: Failed to get journal access]"),
    ],
    ids=["nothing-matched", "missing", "refused"],
)
def test_the_journal_read_says_why_it_is_empty(
    monkeypatch: pytest.MonkeyPatch, answer: tuple[int, str], expected: list[str] | str
) -> None:
    _fake_run(monkeypatch, {"journalctl -u": answer})
    assert dd.read_display_journal("openfollow", 3.0) == expected


def test_no_unit_means_no_journal_read() -> None:
    assert dd.read_display_journal(None, 3.0) == "[not read: no journald service name configured]"


def _j(message: str, stamp: str = "Nov 03 10:00:01") -> str:
    return f"{stamp} station openfollow[812]: {message}"


@pytest.mark.parametrize(
    ("lines", "verdict"),
    [
        (
            [_j("applying profile 'single-1'"), _j("configuration for profile 'single-1' applied", "Nov 03 10:00:02")],
            "profile 'single-1' applied (Nov 03 10:00:02)",
        ),
        (
            [_j("applying profile 'dual'"), _j("failed to apply configuration for profile 'dual'")],
            "profile 'dual' failed to apply (Nov 03 10:00:01)",
        ),
        (
            [_j("no profile matched")],
            "no profile matched: the compositor arranges the outputs itself (Nov 03 10:00:01)",
        ),
        (
            [_j("configuration for profile 'dual' cancelled, retrying")],
            "profile 'dual' cancelled, retrying (Nov 03 10:00:01)",
        ),
        (
            [_j("applying profile 'dual'"), _j("applying profile output 'HDMI-A-1' on connected head 'HDMI-A-1'")],
            "applying profile 'dual', no result logged (Nov 03 10:00:01)",
        ),
        (
            [_j("applying profile 'dual'"), _j("output 'HDMI-A-2' doesn't support mode '1920x1080@0.000000Hz'")],
            "applying profile 'dual', no result logged (Nov 03 10:00:01)",
        ),
        (
            ["compositor doesn't support wlr-output-management-unstable-v1"],
            "compositor doesn't support wlr-output-management-unstable-v1",
        ),
        ([], "nothing logged this boot"),
    ],
    ids=["applied", "failed", "no-match", "cancelled", "pending", "pending-mode", "no-protocol", "silent"],
)
def test_kanshi_reports_its_last_verdict(lines: list[str], verdict: str) -> None:
    assert dd.kanshi_rows(lines)[0] == f"  {'kanshi':<29}{verdict}"


def test_kanshi_lists_the_modes_its_last_profile_could_not_set() -> None:
    lines = [
        _j("applying profile 'single-1'"),
        _j("output 'HDMI-A-1' doesn't support mode '1280x1024@60.000000Hz'"),
        _j("configuration for profile 'single-1' applied"),
        _j("applying profile 'dual'"),
        _j("output 'HDMI-A-2' doesn't support mode '1920x1080@0.000000Hz'"),
        _j("failed to apply configuration for profile 'dual'"),
    ]
    assert dd.kanshi_rows(lines) == [
        f"  {'kanshi':<29}profile 'dual' failed to apply (Nov 03 10:00:01)",
        "    output 'HDMI-A-2' doesn't support mode '1920x1080@0.000000Hz'",
    ]


def test_the_session_log_keeps_the_graphics_stack_and_drops_the_app() -> None:
    lines = [
        _j("2026-11-03 10:00:00 [INFO] openfollow.services: OSC output mode changed"),
        _j("00:00:00.412 [ERROR] [backend/drm/drm.c:812] connector eDP-1: Failed to commit"),
        _j("00:00:00.413 [ERROR] [render/egl.c:208] command: eglQueryDeviceStringEXT, error: EGL_BAD_PARAMETER"),
        _j("00:00:00.500 [ERROR] [types/output/swapchain.c:96] Failed to test swapchain"),
        _j("no profile matched"),
    ]
    assert dd.session_log_rows(lines) == [
        "",
        "  Session log (this boot: Cage / wlroots / kanshi, outputs and modes):",
        f"    {lines[1]}",
        f"    {lines[3]}",
        f"    {lines[4]}",
    ]


def test_the_station_name_never_lets_a_wlroots_line_through() -> None:
    line = "Nov 03 10:00:01 output-mode-1 openfollow[812]: [ERROR] [render/egl.c:208] eglQueryDeviceStringEXT"
    assert dd.session_log_rows([line])[-1] == "    [none]"


def test_a_reconnect_cycle_is_collapsed() -> None:
    cycle = [_j(f"[ERROR] [backend/drm/drm.c:1] connector DP-{i}: mode failed") for i in range(3)]
    rows = dd.session_log_rows(cycle * 6)
    assert 2 + 3 < len(rows) < 2 + 18
    assert sum("connector DP-0" in row for row in rows) == 1


def test_the_session_log_keeps_the_newest_lines_redacted() -> None:
    distinct = [_j(f"00:00:00.000 [ERROR] [backend/drm/drm.c:{n}] crtc {n} gone") for n in range(50)]
    secret = _j("00:00:00.000 [ERROR] [x.c:1] output from rtsp://admin:hunter2@cam/s")
    rows = dd.session_log_rows([*distinct, secret])
    assert rows[2] == "    [... 11 earlier line(s) not shown]"
    assert len(rows) == 3 + 40
    assert rows[3].endswith("[backend/drm/drm.c:11] crtc 11 gone")
    assert not any("hunter2" in row for row in rows)
    assert rows[-1].endswith("output from rtsp://cam/s")


def test_an_empty_session_log_says_none() -> None:
    assert dd.session_log_rows([_j("2026 [INFO] openfollow.x: output")])[-1] == "    [none]"


# ---------------------------------------------------------------------------
# Display manager
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("show", "row"),
    [
        (
            (0, "Id=gdm.service\nLoadState=loaded\nActiveState=active"),
            "gdm.service, running: a desktop holds the display",
        ),
        ((0, "Id=lightdm.service\nLoadState=loaded\nActiveState=inactive"), "lightdm.service, enabled, inactive"),
        ((0, "Id=display-manager.service\nLoadState=not-found\nActiveState=inactive"), "none enabled"),
        ((-1, "[unavailable: systemctl timed out after 2.0s]"), "[unavailable: systemctl timed out after 2.0s]"),
        ((1, "Failed to connect to bus"), "[unavailable: Failed to connect to bus]"),
    ],
    ids=["running", "enabled-stopped", "none", "timeout", "no-bus"],
)
def test_the_display_manager_is_named(monkeypatch: pytest.MonkeyPatch, show: tuple[int, str], row: str) -> None:
    _fake_run(monkeypatch, {"systemctl get-default": (0, "graphical.target\n"), "systemctl show": show})
    assert dd.collect_display_manager() == [
        "",
        f"  {'Default boot target':<29}graphical.target",
        f"  {'Display manager':<29}{row}",
    ]


@pytest.mark.parametrize(
    ("answer", "row"),
    [
        ((-1, "[unavailable: systemctl not found]"), "[unavailable: systemctl not found]"),
        ((1, "boom"), "[unavailable: boom]"),
    ],
    ids=["missing", "failed"],
)
def test_an_unreadable_default_target_is_said(
    monkeypatch: pytest.MonkeyPatch, answer: tuple[int, str], row: str
) -> None:
    _fake_run(monkeypatch, {"systemctl get-default": answer, "systemctl show": (0, "LoadState=not-found")})
    assert dd.collect_display_manager()[1] == f"  {'Default boot target':<29}{row}"


# ---------------------------------------------------------------------------
# The section
# ---------------------------------------------------------------------------


def test_off_linux_the_section_says_it_does_not_apply() -> None:
    assert dd.collect_displays(unit="openfollow", budget_s=5.0, platform_name="darwin") == [
        "  [not applicable: displays are read on Linux only]"
    ]


def test_the_section_reads_every_part(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_run(
        monkeypatch,
        {
            "journalctl -u": (0, _j("no profile matched")),
            "systemctl get-default": (0, "multi-user.target"),
            "systemctl show": (0, "LoadState=not-found"),
        },
    )
    rows = dd.collect_displays(
        unit="openfollow",
        budget_s=5.0,
        drm_root=_laptop(tmp_path),
        environ=_ENV,
        platform_name="linux",
        layout_reader=_reader([_head("HDMI-A-1"), _head("eDP-1", enabled=False, mode=_PANEL)]),
    )
    text = "\n".join(rows)
    assert "    card1     amdgpu (PCI 1002:7480)" in rows
    assert "    Connected, not shown       eDP-1" in rows
    assert f"  {'kanshi':<29}no profile matched" in text
    assert f"    {_j('no profile matched')}" in rows
    assert rows[-2:] == [f"  {'Default boot target':<29}multi-user.target", f"  {'Display manager':<29}none enabled"]


def test_an_unreadable_journal_is_said_in_both_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_run(monkeypatch, {"systemctl get-default": (0, "x"), "systemctl show": (0, "")})
    rows = dd.collect_displays(unit=None, budget_s=5.0, platform_name="linux", environ={})
    reason = "[not read: no journald service name configured]"
    assert f"  {'kanshi':<29}{reason}" in rows
    assert f"    {reason}" in rows


def test_by_default_the_section_reads_the_hosts_drm_class(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(dd, "_DRM_ROOT", _laptop(tmp_path))
    _fake_run(monkeypatch, {"systemctl get-default": (0, "x"), "systemctl show": (0, "")})
    rows = dd.collect_displays(unit=None, budget_s=5.0, platform_name="linux", environ={})
    assert "    card0     i915 (PCI 8086:46a6, boot display)" in rows


def test_the_bundle_places_displays_after_the_3d_mouse() -> None:
    labels = [label for label, _attr in diag._BUNDLE_SECTIONS]
    assert labels.index("E11. Displays") == labels.index("E10. 3D Mouse") + 1


def test_the_bundle_hands_the_section_its_unit_and_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str | None, float]] = []

    def fake(*, unit: str | None, budget_s: float) -> list[str]:
        seen.append((unit, budget_s))
        return ["  displays"]

    monkeypatch.setattr(dd, "collect_displays", fake)
    bundle = diag.collect_bundle(update_service_name="openfollow")
    assert bundle.e11_displays == ["  displays"]
    ((unit, budget),) = seen
    assert unit == "openfollow"
    assert 0 < budget <= diag._DISPLAY_SECTION_BUDGET_S
