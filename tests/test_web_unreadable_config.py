# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The web UI on a station whose ``config.toml`` is empty or does not read.

Every write reads the file whole first, so it is refused with a reason the
save-feedback line shows and leaves the file, its backup and the settings
archives as they are. An Import may replace a file that does not read. A page
is drawn from the file as it last read whole, else from the backup.
"""

from __future__ import annotations

import ast
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import pytest

import openfollow.web.discovery as discovery_module
import openfollow.web.routes as routes_module
from openfollow.configuration import AppConfig, load_config, save_config
from openfollow.marker_catalog.catalog import MarkerCatalog
from openfollow.privilege.settings_backup import web_backup_dir
from openfollow.web.server import ConfigWebServer
from tests._ports import live_on_free_port

pytestmark = pytest.mark.integration

_EMPTY_ERROR = "The settings file on this station is empty"
_EMPTY_ACTION = "Restart OpenFollow to restore the settings from the latest backup copy."
_UNREADABLE_ERROR = "The settings file on this station could not be read"
_UNREADABLE_ACTION = "Import a settings file or a backup copy to replace it."

_BROKEN = pytest.mark.parametrize(
    "content", [b"", b"  \n\t\n", b"not [[ valid toml"], ids=["empty", "whitespace", "unparseable"]
)

_GRID = {"width": 12.0}
_ZONE = {"name": "Z", "vertices": [[0, 0], [1, 0], [1, 1]]}
_MASK = {"vertices": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9]]}

# Every route that writes config.toml, with a request that reaches its read. Keyed by the route's
# own pattern, so a new writing route fails the completeness test below until it is listed here.
_WRITES: dict[tuple[str, str], tuple[str, str, Any]] = {
    ("POST", "/api/config/<section>"): ("/api/config/grid", "json", _GRID),
    ("POST", "/api/config/<section>/broadcast"): ("/api/config/grid/broadcast", "json", _GRID),
    ("POST", "/api/config/import"): ("/api/config/import?from=Other%20Station", "json", {"grid": _GRID}),
    ("POST", "/api/config/reset"): ("/api/config/reset", "json", {}),
    ("POST", "/api/detection/masks"): ("/api/detection/masks", "json", _MASK),
    ("PUT", "/api/detection/masks/<index:int>"): ("/api/detection/masks/0", "json", _MASK),
    ("DELETE", "/api/detection/masks/<index:int>"): ("/api/detection/masks/0", "json", None),
    ("POST", "/api/detection/masks/enabled"): ("/api/detection/masks/enabled", "json", {"enabled": True}),
    ("DELETE", "/api/markers/catalog/<marker_id:int>"): ("/api/markers/catalog/1", "json", None),
    ("POST", "/api/markers/selection"): ("/api/markers/selection", "json", {"controlled_ids": [1], "viewer_ids": [1]}),
    ("POST", "/api/paths"): ("/api/paths", "json", {"name": "P"}),
    ("PUT", "/api/paths/<name>"): ("/api/paths/P", "json", {"name": "Q"}),
    ("DELETE", "/api/paths/<name>"): ("/api/paths/P", "json", None),
    ("POST", "/api/paths/<name>/active"): ("/api/paths/P/active", "json", {"active": True}),
    ("POST", "/api/templates/<filename>/apply"): ("/api/templates/osc_output.etc-eos.oftemplate/apply", "json", {}),
    ("POST", "/api/zones"): ("/api/zones", "json", _ZONE),
    ("PUT", "/api/zones/<index:int>"): ("/api/zones/0", "json", _ZONE),
    ("DELETE", "/api/zones/<index:int>"): ("/api/zones/0", "json", None),
    ("POST", "/api/zones/<index:int>/duplicate"): ("/api/zones/0/duplicate", "json", {}),
    ("POST", "/section/camera"): ("/section/camera", "form", {"fov": "60"}),
    ("POST", "/section/detection/inference"): ("/section/detection/inference", "form", {"confidence": "0.5"}),
    ("POST", "/section/detection/models"): ("/section/detection/models", "form", {}),
    ("POST", "/section/detection/tracking"): ("/section/detection/tracking", "form", {"tracking_mode": "off"}),
    ("POST", "/section/gamepad"): ("/section/gamepad", "form", {}),
    ("POST", "/section/general"): ("/section/general", "form", {"psn_system_name": "Changed"}),
    ("POST", "/section/grid"): ("/section/grid", "form", {"width": "12"}),
    ("POST", "/section/ground_grid"): ("/section/ground_grid", "form", {}),
    ("POST", "/section/interface_assignment"): ("/section/interface_assignment", "form", {}),
    ("POST", "/section/keyboard"): ("/section/keyboard", "form", {}),
    ("POST", "/section/marker"): ("/section/marker", "form", {}),
    ("POST", "/section/midi/faders"): ("/section/midi/faders", "form", {}),
    ("POST", "/section/midi/faders/<idx:int>"): ("/section/midi/faders/1", "form", {}),
    ("POST", "/section/midi/patches/<patch_id:int>"): ("/section/midi/patches/1", "form", {}),
    ("POST", "/section/midi/patches/<patch_id:int>/delete"): ("/section/midi/patches/1/delete", "form", {}),
    ("POST", "/section/midi/patches/add"): ("/section/midi/patches/add", "form", {}),
    ("POST", "/section/mouse"): ("/section/mouse", "form", {}),
    ("POST", "/section/mouse3d"): ("/section/mouse3d", "form", {}),
    ("POST", "/section/movement"): ("/section/movement", "form", {}),
    ("POST", "/section/network/label"): ("/section/network/label", "form", {"iface": "eth0", "label": "Stage"}),
    ("POST", "/section/network/label/forget"): ("/section/network/label/forget", "form", {"iface": "eth0"}),
    ("POST", "/section/network/mode"): ("/section/network/mode", "form", {"network_mode": "advanced"}),
    ("POST", "/section/network/station"): ("/section/network/station", "form", {"psn_source_iface": ""}),
    ("POST", "/section/operator_messages"): ("/section/operator_messages", "form", {}),
    ("POST", "/section/osc"): ("/section/osc", "form", {}),
    ("POST", "/section/osc_binding/<row_id>"): ("/section/osc_binding/row1", "form", {}),
    ("POST", "/section/osc_binding/<row_id>/delete"): ("/section/osc_binding/row1/delete", "form", {}),
    ("POST", "/section/osc_binding/<row_id>/duplicate"): ("/section/osc_binding/row1/duplicate", "form", {}),
    ("POST", "/section/osc_binding/<row_id>/move"): ("/section/osc_binding/row1/move", "form", {"direction": "up"}),
    ("POST", "/section/osc_bindings/add"): ("/section/osc_bindings/add", "form", {}),
    ("POST", "/section/osc_bindings/reorder"): ("/section/osc_bindings/reorder", "form", {"order": "row1"}),
    ("POST", "/section/osc_destination/<dest_id>"): ("/section/osc_destination/d1", "form", {}),
    ("POST", "/section/osc_destination/<dest_id>/delete"): ("/section/osc_destination/d1/delete", "form", {}),
    ("POST", "/section/osc_destination/<dest_id>/duplicate"): ("/section/osc_destination/d1/duplicate", "form", {}),
    ("POST", "/section/osc_destination/<dest_id>/move"): (
        "/section/osc_destination/d1/move",
        "form",
        {"direction": "up"},
    ),
    ("POST", "/section/osc_destinations/add"): ("/section/osc_destinations/add", "form", {}),
    ("POST", "/section/osc_destinations/reorder"): ("/section/osc_destinations/reorder", "form", {"order": "d1"}),
    ("POST", "/section/otp_output"): ("/section/otp_output", "form", {}),
    ("POST", "/section/paths"): ("/section/paths", "form", {}),
    ("POST", "/section/psn"): ("/section/psn", "form", {}),
    ("POST", "/section/rttrpm_output"): ("/section/rttrpm_output", "form", {}),
    ("POST", "/section/trigger_zones"): ("/section/trigger_zones", "form", {}),
    ("POST", "/section/video_source"): ("/section/video_source", "form", {"video_source_type": "testpattern"}),
    ("POST", "/settings/experimental"): ("/settings/experimental", "form", {"show_experimental_features": "on"}),
    ("POST", "/settings/unit-system"): ("/settings/unit-system", "form", {"unit_system": "imperial"}),
    ("POST", "/video-input/testpattern/select"): (
        "/video-input/testpattern/select",
        "form",
        {"media_id": "default:grey"},
    ),
}

# Writes that read the file to hand it on, or that land in more than one way.
_EXTRA = [
    ("POST", "/api/config/grid?from=Other%20Station", "json", _GRID),
    ("POST", "/api/config/broadcast-all", "json", {}),
    ("POST", "/api/templates/camera_grid/save", "json", {"name": "Rig"}),
    ("POST", "/api/templates/paths/save", "json", {"name": "Route"}),
    ("POST", "/api/templates/zones/save", "json", {"name": "Areas"}),
    ("GET", "/api/config/export", "json", None),
]
_REQUESTS = [(method, *_WRITES[(method, pattern)]) for method, pattern in _WRITES] + _EXTRA
_IDS = [f"{method} {path}" for method, path, _, _ in _REQUESTS]


def _writing_routes() -> set[tuple[str, str]]:
    """``(method, pattern)`` of every route in ``routes.py`` whose handler writes the config."""
    source = Path(routes_module.__file__).read_text(encoding="utf-8")
    found = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef):
            continue
        body = ast.unparse(node)
        if not any(call in body for call in ("save_config(", "_save_section_from_form(", "_persist_ui_change(")):
            continue
        for deco in node.decorator_list:
            if (
                isinstance(deco, ast.Call)
                and isinstance(deco.func, ast.Attribute)
                and isinstance(deco.func.value, ast.Name)
                and deco.func.value.id == "app"
            ):
                pattern = eval(ast.unparse(deco.args[0]), vars(routes_module))  # noqa: S307 - our own source
                found.add((deco.func.attr.upper(), pattern))
    return found


def test_every_route_that_writes_the_config_is_in_the_matrix() -> None:
    found = _writing_routes()
    assert len(found) >= 60, "the scan no longer finds the writing routes"
    assert found == set(_WRITES)


def _request(base: str, method: str, path: str, kind: str, body: Any) -> tuple[int, str]:
    if body is None:
        data, headers = None, {}
    elif kind == "json":
        data, headers = json.dumps(body).encode(), {"Content-Type": "application/json"}
    else:
        data, headers = urllib.parse.urlencode(body).encode(), {"Content-Type": "application/x-www-form-urlencoded"}
    req = urllib.request.Request(f"{base}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
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


def _archives(config_path: Path) -> list[Path]:
    folder = web_backup_dir(config_path)
    return sorted(folder.iterdir()) if folder.is_dir() else []


@pytest.fixture()
def station(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    """A live web server over ``config.toml``, which a test breaks before it requests."""
    for cls in (discovery_module.BeaconSender, discovery_module.BeaconReceiver):
        monkeypatch.setattr(cls, "start", lambda self: None)
        monkeypatch.setattr(cls, "stop", lambda self: None)
    config_path = tmp_path / "config.toml"
    for name in ("Backup Name", "Latest Name"):  # the second save leaves the first as the backup
        save_config(AppConfig(psn_system_name=name, station_id="abc123", controlled_marker_ids=[1]), str(config_path))
    catalog = MarkerCatalog()
    catalog.upsert(1, "One", "#ffffff", origin="abc123")
    with live_on_free_port(
        lambda port: ConfigWebServer(
            config_path=str(config_path),
            host="127.0.0.1",
            port=port,
            system_name="T",
            marker_catalog_provider=lambda: catalog,
            network_interfaces_provider=lambda: [{"name": "eth0"}],
        )
    ) as (server, base):
        yield config_path, base


@pytest.fixture()
def repaired_before_any_write(station, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN001, ANN201
    """The file is repaired just before a route writes it: a route that read the backup would write it over."""
    config_path, _ = station
    whole = config_path.read_bytes()
    real = routes_module.save_config

    def _repaired_then_saved(cfg: AppConfig, path: str, **kwargs: Any) -> None:
        Path(path).write_bytes(whole)
        real(cfg, path, **kwargs)

    monkeypatch.setattr(routes_module, "save_config", _repaired_then_saved)
    return station


@_BROKEN
@pytest.mark.parametrize(("method", "path", "kind", "body"), _REQUESTS, ids=_IDS)
def test_a_write_over_a_broken_file_is_refused_at_its_read_and_leaves_everything(
    repaired_before_any_write, content: bytes, method: str, path: str, kind: str, body: Any
) -> None:
    config_path, base = repaired_before_any_write
    backup = config_path.with_name("config.toml.bak").read_bytes()
    config_path.write_bytes(content)

    status, text = _request(base, method, path, kind, body)

    if path.startswith("/api/markers/catalog/"):
        assert status == 200  # the tombstone stands; only the selection is left as it is
    else:
        assert status == 409, text
        answer = json.loads(text)
        empty = not content.strip()
        assert answer["error"] == (_EMPTY_ERROR if empty else _UNREADABLE_ERROR)
        assert answer["action"] == (_EMPTY_ACTION if empty else _UNREADABLE_ACTION)
    assert config_path.read_bytes() == content
    assert config_path.with_name("config.toml.bak").read_bytes() == backup
    assert _archives(config_path) == []


def test_an_import_replaces_a_file_that_does_not_read_and_keeps_the_station_s_identity(station) -> None:
    config_path, base = station
    # The backup is the save before, without the PIN: the identity comes from the last whole read.
    save_config(AppConfig(psn_system_name="Latest Name", station_id="abc123", web_pin="4321"), str(config_path))
    _request(base, "GET", "/login", "json", None)
    backup = config_path.with_name("config.toml.bak").read_bytes()
    config_path.write_bytes(b"not [[ valid toml")

    cookie = _login(base, "4321")
    status, text = _authed(
        base, "POST", "/api/config/import", {"grid": _GRID, "web_pin": "", "station_id": "x"}, cookie
    )

    assert status == 200, text
    saved = load_config(str(config_path), strict=True)
    assert (saved.grid.width, saved.web_pin, saved.station_id) == (12.0, "4321", "abc123")
    assert config_path.with_name("config.toml.bak").read_bytes() == backup
    archived = _archives(config_path)
    assert len(archived) == 1, "the file it replaced is kept in a settings archive"


def _login(base: str, pin: str) -> str:
    req = urllib.request.Request(
        f"{base}/login",
        data=urllib.parse.urlencode({"pin": pin}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_a, **_kw):  # noqa: ANN002, ANN003, ANN202
            return None

    try:
        urllib.request.build_opener(_NoRedirect).open(req, timeout=5)
    except urllib.error.HTTPError as e:
        return str(e.headers["Set-Cookie"]).split(";", 1)[0]
    raise AssertionError("no login redirect")


def _authed(base: str, method: str, path: str, body: Any, cookie: str) -> tuple[int, str]:
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Cookie": cookie},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


@pytest.mark.parametrize("content", [b"", b"  \n"], ids=["empty", "whitespace"])
def test_an_import_does_not_replace_an_empty_file(station, content: bytes) -> None:
    config_path, base = station
    config_path.write_bytes(content)

    status, text = _request(base, "POST", "/api/config/import", "json", {"grid": _GRID})

    assert (status, json.loads(text)["action"]) == (409, _EMPTY_ACTION)
    assert config_path.read_bytes() == content
    assert _archives(config_path) == []


def test_a_save_over_a_missing_file_starts_from_the_backup(station) -> None:
    config_path, base = station
    config_path.unlink()

    status, _ = _request(base, "POST", "/api/config/grid", "json", _GRID)

    assert status == 200
    saved = load_config(str(config_path), strict=True)
    assert (saved.psn_system_name, saved.grid.width) == ("Backup Name", 12.0)


@_BROKEN
def test_a_page_over_a_file_that_broke_is_drawn_from_its_last_whole_read(station, content: bytes) -> None:
    config_path, base = station
    assert _get(base, "/section/general")[0] == 200
    config_path.write_bytes(content)

    status, page, _ = _get(base, "/section/general")

    assert status == 200
    assert 'value="Latest Name"' in page
    assert config_path.read_bytes() == content


@_BROKEN
def test_the_pin_of_the_last_whole_read_still_guards_a_station_whose_file_broke(station, content: bytes) -> None:
    config_path, base = station
    save_config(AppConfig(psn_system_name="Latest Name", web_pin="4321"), str(config_path))
    assert _get(base, "/")[0] == 303
    config_path.write_bytes(content)

    status, _, headers = _get(base, "/")

    assert (status, headers.get("Location", "").endswith("/login")) == (303, True)


@_BROKEN
def test_the_login_asks_for_the_pin_of_the_last_whole_read(station, content: bytes) -> None:
    config_path, base = station
    save_config(AppConfig(psn_system_name="Latest Name", web_pin="4321"), str(config_path))  # the backup has none
    assert _get(base, "/")[0] == 303
    config_path.write_bytes(content)

    assert _login(base, "4321").startswith("_openfollow_auth=")
    status, page = _request(base, "POST", "/login", "form", {"pin": "0000"})
    assert (status, "Incorrect PIN" in page) == (200, True)
