# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Station Network: every other station's row opens that station's web UI in a new window."""

from __future__ import annotations

import time

import pytest
from bottle import template

from openfollow.web import server as _server_module  # noqa: F401 - registers tpl path
from openfollow.web.discovery import PeerInfo

pytestmark = pytest.mark.unit

_LOCAL = PeerInfo(name="Stage Left", ip="192.0.2.10", web_port=80, version="0.4.3", last_seen=time.time())


def _render(*peers: PeerInfo) -> str:
    return template("partials/overview_peers", local=_LOCAL, peers=list(peers))


def _peer(name: str = "Stage Right", ip: str = "192.0.2.11", port: int = 80, *, online: bool = True) -> PeerInfo:
    return PeerInfo(name=name, ip=ip, web_port=port, version="0.4.3", last_seen=time.time() if online else 0.0)


@pytest.mark.parametrize(("online", "state"), [(True, "online"), (False, "offline")])
def test_another_station_opens_in_a_new_window(online: bool, state: str) -> None:
    html = _render(_peer(online=online))
    assert (
        f'<a class="peer-item {state}" href="http://192.0.2.11:80/" target="_blank" rel="noopener noreferrer">' in html
    )


def test_this_station_is_not_a_link() -> None:
    html = _render(_peer())
    assert '<div class="peer-item local">' in html
    assert "http://192.0.2.10" not in html


def test_the_link_uses_the_port_the_station_advertises() -> None:
    assert 'href="http://192.0.2.11:8080/"' in _render(_peer(port=8080))


def test_an_ipv6_address_is_bracketed_in_the_link() -> None:
    assert 'href="http://[2001:db8::5]:80/"' in _render(_peer(ip="2001:db8::5"))


def test_a_station_name_is_escaped() -> None:
    html = _render(_peer(name='<img src=x onerror="alert(1)">'))
    assert "<img" not in html
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in html


def test_a_screen_reader_hears_that_the_link_opens_a_new_window() -> None:
    html = _render(_peer())
    assert 'Stage Right<span class="visually-hidden"> (opens in a new window)</span>' in html
