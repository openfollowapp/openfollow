# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The web UI on a station whose ``config.toml`` is empty or does not read.

A save is refused with a reason the save-feedback line shows, and leaves the
file and its backup as they are; a page is drawn from the backup, the way an
unreadable file always was, so the PIN in it still guards the station.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

import openfollow.web.discovery as discovery_module
from openfollow.configuration import AppConfig, load_config, save_config
from openfollow.web.server import ConfigWebServer
from tests._ports import live_on_free_port

pytestmark = pytest.mark.integration

_BROKEN = pytest.mark.parametrize(
    "content", [b"", b"  \n\t\n", b"not [[ valid toml"], ids=["empty", "whitespace", "unparseable"]
)

# One write of each kind the web UI makes: section forms, the JSON API and its
# Broadcast, whole-config import and reset, and the list editors.
_SAVES = [
    ("POST", "/section/grid", "form", {"width": "12"}),
    ("POST", "/section/general", "form", {"psn_system_name": "Changed"}),
    ("POST", "/section/video_source", "form", {"video_source_type": "testpattern"}),
    ("POST", "/settings/unit-system", "form", {"unit_system": "imperial"}),
    ("POST", "/section/osc_destinations/add", "form", {}),
    ("POST", "/section/midi/patches/add", "form", {}),
    ("POST", "/api/config/grid", "json", {"width": 12.0}),
    ("POST", "/api/config/grid?from=Other%20Station", "json", {"width": 12.0}),
    ("POST", "/api/config/grid/broadcast", "json", {"width": 12.0}),
    ("POST", "/api/config/import", "json", {"grid": {"width": 12.0}}),
    ("POST", "/api/config/reset", "json", {}),
    ("POST", "/api/zones", "json", {"name": "Z"}),
    ("POST", "/api/paths", "json", {"name": "P"}),
    ("POST", "/api/detection/masks/enabled", "json", {"enabled": True}),
]


def _request(base: str, method: str, path: str, kind: str, body: dict) -> tuple[int, str]:
    if kind == "json":
        data, ctype = json.dumps(body).encode(), "application/json"
    else:
        data, ctype = urllib.parse.urlencode(body).encode(), "application/x-www-form-urlencoded"
    req = urllib.request.Request(f"{base}{path}", data=data, headers={"Content-Type": ctype}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _get(base: str, path: str) -> tuple[int, str, dict[str, str]]:
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_a, **_kw):  # noqa: ANN002, ANN003, ANN202
            return None

    try:
        with urllib.request.build_opener(_NoRedirect).open(f"{base}{path}", timeout=5) as r:
            return r.status, r.read().decode(), dict(r.headers.items())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers.items())


@pytest.fixture()
def station(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    """A live web server over ``config.toml``, which a test breaks before it requests."""
    for cls in (discovery_module.BeaconSender, discovery_module.BeaconReceiver):
        monkeypatch.setattr(cls, "start", lambda self: None)
        monkeypatch.setattr(cls, "stop", lambda self: None)
    config_path = tmp_path / "config.toml"
    for _ in range(2):  # the second save leaves the first as the backup
        save_config(AppConfig(psn_system_name="Backup Name", controlled_marker_ids=[3]), str(config_path))
    with live_on_free_port(
        lambda port: ConfigWebServer(config_path=str(config_path), host="127.0.0.1", port=port, system_name="T")
    ) as (server, base):
        yield config_path, base


@_BROKEN
@pytest.mark.parametrize(("method", "path", "kind", "body"), _SAVES, ids=[f"{m} {p}" for m, p, _, _ in _SAVES])
def test_a_save_over_a_broken_file_is_refused_and_leaves_it(
    station, content: bytes, method: str, path: str, kind: str, body: dict
) -> None:
    config_path, base = station
    backup = config_path.with_name("config.toml.bak").read_bytes()
    config_path.write_bytes(content)

    status, text = _request(base, method, path, kind, body)

    assert status == 409
    answer = json.loads(text)
    said = "is empty" if not content.strip() else "could not be read"
    assert answer["error"] == f"The settings file config.toml on this station {said}"
    assert answer["action"] == "Restore it from config.toml.bak, then save again."
    assert config_path.read_bytes() == content
    assert config_path.with_name("config.toml.bak").read_bytes() == backup


def test_a_save_over_a_missing_file_starts_from_the_backup(station) -> None:
    config_path, base = station
    config_path.unlink()

    status, _ = _request(base, "POST", "/api/config/grid", "json", {"width": 12.0})

    assert status == 200
    saved = load_config(str(config_path), strict=True)
    assert (saved.psn_system_name, saved.grid.width) == ("Backup Name", 12.0)


@_BROKEN
def test_a_page_over_a_broken_file_is_drawn_from_the_backup(station, content: bytes) -> None:
    config_path, base = station
    config_path.write_bytes(content)

    status, page, _ = _get(base, "/section/general")

    assert status == 200
    assert 'value="Backup Name"' in page
    assert config_path.read_bytes() == content


@_BROKEN
def test_the_pin_in_the_backup_still_guards_a_station_whose_file_is_broken(station, content: bytes) -> None:
    config_path, base = station
    for _ in range(2):
        save_config(AppConfig(psn_system_name="Backup Name", web_pin="4321"), str(config_path))
    config_path.write_bytes(content)

    status, _, headers = _get(base, "/")

    assert (status, headers.get("Location", "").endswith("/login")) == (303, True)
