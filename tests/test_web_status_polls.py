# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Status polls answer 204 while nothing they show has changed.

A 204 leaves the page untouched, so a ``role="alert"`` box is inserted, and
announced, once per change rather than once per poll.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

import pytest

import openfollow.web.discovery as discovery_module
from openfollow.input.mouse3d_status import DeviceState, Mouse3DDeviceStatus, Mouse3DStatus, status_key
from openfollow.web.live_alerts import statistics_alerts
from openfollow.web.server import ConfigWebServer
from tests._ports import live_on_free_port

pytestmark = pytest.mark.integration

_NOT_SUPPORTED = "SpaceNavigator (046d:c626) is not supported by this version of OpenFollow."


def _mouse3d_block(state: DeviceState = DeviceState.NO_PROFILE) -> dict:
    return Mouse3DStatus(
        enabled=True,
        supported=True,
        scanned=True,
        devices=(
            Mouse3DDeviceStatus(
                path="/dev/hidraw2",
                product_name="SpaceNavigator",
                vendor_id=0x046D,
                product_id=0xC626,
                port_key=None,
                state=state,
            ),
        ),
    ).to_dict()


@pytest.fixture()
def status_server(tmp_path, monkeypatch):
    """A live server whose runtime stats the test edits in place."""
    monkeypatch.setattr(discovery_module.BeaconSender, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconSender, "stop", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "stop", lambda self: None)
    stats: dict = {
        "mouse3d": _mouse3d_block(),
        "controllers": {"items": [{"controller_index": 0, "state": "missing", "name": "GameSir"}]},
    }
    with live_on_free_port(
        lambda port: ConfigWebServer(
            config_path=str(tmp_path / "config.toml"),
            host="127.0.0.1",
            port=port,
            system_name="TestSystem",
            runtime_stats_provider=lambda: stats,
        )
    ) as (server, base):
        yield base, stats


def _request(base: str, path: str, data: dict | None = None) -> tuple[int, str]:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(f"{base}{path}", data=body, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_the_3d_mouse_poll_answers_204_while_unchanged(status_server) -> None:
    base, stats = status_server
    key = status_key(stats["mouse3d"])
    assert _request(base, f"/section/mouse3d/status?key={key}") == (204, "")


def test_the_3d_mouse_poll_swaps_in_a_change(status_server) -> None:
    base, stats = status_server
    old_key = status_key(stats["mouse3d"])
    stats["mouse3d"] = _mouse3d_block(DeviceState.NOT_PERMITTED)
    status, body = _request(base, f"/section/mouse3d/status?key={old_key}")
    assert status == 200
    assert "was found, but OpenFollow is not allowed to open it." in body
    assert f"key={status_key(stats['mouse3d'])}" in body


def test_a_first_poll_without_a_key_gets_the_block(status_server) -> None:
    base, _ = status_server
    status, body = _request(base, "/section/mouse3d/status")
    assert status == 200
    assert _NOT_SUPPORTED in body


@pytest.mark.parametrize(
    ("path", "form"),
    [("/", None), ("/section/mouse3d", None), ("/section/mouse3d", {"curve": "linear"})],
    ids=["page", "section", "save"],
)
def test_every_render_of_the_section_carries_the_status(status_server, path: str, form: dict | None) -> None:
    base, _ = status_server
    status, body = _request(base, path, form)
    assert status == 200
    assert 'id="mouse3d-status"' in body
    assert _NOT_SUPPORTED in body


def test_the_statistics_announcer_answers_204_while_unchanged(status_server) -> None:
    base, stats = status_server
    key = statistics_alerts(stats).key()
    assert _request(base, f"/section/statistics/alerts?key={key}") == (204, "")
    stats["controllers"]["items"].append({"controller_index": 1, "state": "missing", "name": "8BitDo"})
    status, body = _request(base, f"/section/statistics/alerts?key={key}")
    assert status == 200
    assert "C2 missing · 8BitDo" in body


class _Ancestors(HTMLParser):
    """Ids of the elements enclosing the one with ``target`` as its id."""

    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self, target: str) -> None:
        super().__init__()
        self._target = target
        self._stack: list[str] = []
        self.found: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._VOID:
            return
        elt_id = dict(attrs).get("id") or ""
        if elt_id == self._target and self.found is None:
            self.found = list(self._stack)
        self._stack.append(elt_id)

    def handle_endtag(self, tag: str) -> None:
        if tag not in self._VOID and self._stack:
            self._stack.pop()


def test_the_announcer_sits_outside_the_panel_it_speaks_for(status_server) -> None:
    base, _ = status_server
    _, page = _request(base, "/")
    parser = _Ancestors("statistics-alerts")
    parser.feed(page)
    assert parser.found is not None
    # The panel's own 1 s swap would re-insert it, and re-announce it, every second.
    assert "statistics-section-content" not in parser.found
    assert "statistics-section" in parser.found
    announcer = page.index('id="statistics-alerts"')
    assert "C1 missing · GameSir" in page[announcer : page.index("</div>", announcer)]


def test_the_announcer_swaps_only_itself(status_server) -> None:
    base, _ = status_server
    _, page = _request(base, "/")
    start = page.index('<div id="statistics-alerts"')
    tag = page[start : page.index(">", start)]
    assert 'hx-target="this"' in tag
