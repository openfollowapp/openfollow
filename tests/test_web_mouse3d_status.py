# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The 3D Mouse section's status block: a box per fault, nothing when all is well."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

import pytest
from bottle import template

from openfollow.configuration import AppConfig
from openfollow.input.mouse3d_status import DeviceState, Mouse3DDeviceStatus, Mouse3DStatus, status_key
from openfollow.web import server as _server_module  # noqa: F401 - registers tpl path

pytestmark = pytest.mark.unit


def _device(state: DeviceState, name: str = "SpaceNavigator") -> dict[str, Any]:
    return Mouse3DDeviceStatus(
        path="/dev/hidraw2",
        product_name=name,
        vendor_id=0x046D,
        product_id=0xC626,
        port_key=None,
        state=state,
    ).to_dict()


def _block(**overrides: Any) -> dict[str, Any]:
    block = Mouse3DStatus(enabled=True, supported=True, scanned=True).to_dict()
    block.update(overrides)
    return block


def _render(block: dict[str, Any]) -> str:
    return template("partials/mouse3d_status", mouse3d_status=block)


def _boxes(html: str) -> list[str]:
    return re.findall(r'<div class="notice[^"]*".*?</div>\s*</div>', html, re.S)


@pytest.mark.parametrize(
    ("block", "text"),
    [
        (_block(backend="not_installed"), "3D Mouse support is not installed."),
        (_block(backend="could_not_start"), "3D Mouse support could not start, so no 3D Mouse can be read."),
        (
            _block(devices=[_device(DeviceState.NO_PROFILE)]),
            "SpaceNavigator (046d:c626) is not supported by this version of OpenFollow.",
        ),
        (
            _block(devices=[_device(DeviceState.NOT_PERMITTED)]),
            "SpaceNavigator (046d:c626) was found, but OpenFollow is not allowed to open it.",
        ),
        (
            _block(devices=[_device(DeviceState.OPEN_FAILED)]),
            "SpaceNavigator (046d:c626) was found, but could not be opened.",
        ),
    ],
    ids=["not-installed", "could-not-start", "no-profile", "not-permitted", "open-failed"],
)
def test_a_fault_is_an_error_box_that_announces_itself(block: dict[str, Any], text: str) -> None:
    (box,) = _boxes(_render(block))
    assert box.startswith('<div class="notice error" role="alert" aria-live="assertive" aria-atomic="true">')
    assert f"<div>{text}</div>" in box
    assert '<div class="notice-sub">' in box  # every fault carries its one step


def test_macos_is_an_info_box_that_does_not_interrupt() -> None:
    (box,) = _boxes(_render(_block(enabled=False, supported=False)))
    assert box.startswith('<div class="notice" role="status" aria-live="polite" aria-atomic="true">')
    assert "<div>3D Mouse input is not supported on macOS by this version of OpenFollow.</div>" in box
    assert "notice-sub" not in box
    assert 'role="alert"' not in box


def test_nothing_connected_is_plain_text_not_a_fault() -> None:
    html = _render(_block())
    assert '<p class="m3d-empty">No 3D Mouse connected.</p>' in html
    assert "notice" not in html
    assert "role=" not in html
    assert "aria-live" not in html


@pytest.mark.parametrize(
    "block",
    [
        _block(enabled=False),
        _block(scanned=False),
        _block(devices=[_device(DeviceState.OPEN)]),
        _block(devices=[_device(DeviceState.OPENING)]),
    ],
    ids=["disabled", "first-scan-pending", "working", "opening"],
)
def test_all_is_well_shows_nothing(block: dict[str, Any]) -> None:
    html = _render(block)
    assert "notice" not in html
    assert "m3d-empty" not in html


def test_the_poll_carries_the_key_of_what_it_shows() -> None:
    block = _block(devices=[_device(DeviceState.NO_PROFILE)])
    html = _render(block)
    assert f'hx-get="/section/mouse3d/status?key={status_key(block)}"' in html
    assert 'hx-swap="outerHTML"' in html
    # Polls only while the section is open on the visible tab.
    assert "is-collapsed" in html and "tab-content" in html


def test_a_devices_own_product_string_is_escaped() -> None:
    html = _render(_block(devices=[_device(DeviceState.NO_PROFILE, name="<b>Puck</b>")]))
    assert "<b>Puck</b>" not in html
    assert "&lt;b&gt;Puck&lt;/b&gt;" in html


def test_the_section_renders_the_block_above_its_form() -> None:
    html = template(
        "partials/mouse3d", config=AppConfig(), mouse3d_status=_block(devices=[_device(DeviceState.NO_PROFILE)])
    )
    assert html.index('id="mouse3d-status"') < html.index('name="enabled"')
    assert "is not supported by this version of OpenFollow." in html


class _EffectiveTarget(HTMLParser):
    """The ``hx-target`` htmx resolves for the element polling ``path``: its own, else inherited."""

    _VOID = {"br", "hr", "img", "input", "link", "meta"}

    def __init__(self, path: str) -> None:
        super().__init__()
        self._path = path
        self._targets: list[str | None] = []
        self.target: str | None = None
        self.found = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        own = a.get("hx-target")
        if (a.get("hx-get") or "").startswith(self._path) and not self.found:
            self.found = True
            inherited = next((t for t in reversed(self._targets) if t is not None), None)
            self.target = own if own is not None else inherited
        if tag not in self._VOID:
            self._targets.append(own)

    def handle_endtag(self, tag: str) -> None:
        if tag not in self._VOID and self._targets:
            self._targets.pop()


def test_the_poll_swaps_only_itself_inside_the_sections_form() -> None:
    """The form targets the whole section for Save; a poll that inherited that
    target would replace the section with the status block."""
    html = template(
        "partials/mouse3d", config=AppConfig(), mouse3d_status=_block(devices=[_device(DeviceState.NO_PROFILE)])
    )
    parser = _EffectiveTarget("/section/mouse3d/status")
    parser.feed(html)
    assert parser.found
    assert parser.target == "this"
