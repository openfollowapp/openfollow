# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Features list against a live ``ConfigWebServer``: the Station Settings
Save (``/section/general``) saves the list and writes nothing else, the list is
never section-broadcast, imports and pushes carry it (an older
station's ``show_experimental_features`` included), and the page renders one
``feature-<key>`` body class per feature that is on."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

import pytest
from bottle import html_escape

import openfollow.web.discovery as discovery_module
from openfollow.configuration import DEFAULT_SHOWN_FEATURES, FEATURE_KEYS, PathConfig, load_config, save_config
from openfollow.web.labels import FEATURE_LABELS
from openfollow.web.server import ConfigWebServer
from tests._ports import live_on_free_port

pytestmark = pytest.mark.integration


@pytest.fixture()
def live_server(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    """ConfigWebServer on a free localhost port; beacon I/O stubbed."""
    for cls in (discovery_module.BeaconSender, discovery_module.BeaconReceiver):
        monkeypatch.setattr(cls, "start", lambda self: None)
        monkeypatch.setattr(cls, "stop", lambda self: None)
    with live_on_free_port(
        lambda port: ConfigWebServer(
            config_path=str(tmp_path / "config.toml"),
            host="127.0.0.1",
            port=port,
            system_name="TestSystem",
        )
    ) as (server, base):
        yield server, base


def _get(base: str, path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _post(base: str, path: str, data: bytes, content_type: str) -> tuple[int, dict[str, str], str]:
    req = urllib.request.Request(f"{base}{path}", data=data, headers={"Content-Type": content_type}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read().decode()


def _save_features(base: str, *on: str, marker: bool = True) -> tuple[int, dict[str, str]]:
    # An unchecked checkbox submits no field; the marker says the table was on the page.
    fields = dict.fromkeys(on, "on") | ({"features_submitted": "1"} if marker else {})
    status, headers, _ = _post(
        base, "/section/general", urllib.parse.urlencode(fields).encode(), "application/x-www-form-urlencoded"
    )
    return status, headers


def _post_json(base: str, path: str, body: object) -> int:
    return _post(base, path, json.dumps(body).encode(), "application/json")[0]


class TestSave:
    def test_a_fresh_station_shows_the_default_features(self, live_server) -> None:
        server, _ = live_server
        assert load_config(server.config_path).ui.shown_features == list(DEFAULT_SHOWN_FEATURES)

    def test_the_form_saves_exactly_the_ticked_features(self, live_server) -> None:
        server, base = live_server
        assert _save_features(base, "paths", "midi")[0] == 200
        assert load_config(server.config_path).ui.shown_features == ["midi", "paths"]
        assert _save_features(base)[0] == 200
        assert load_config(server.config_path).ui.shown_features == []

    def test_a_save_without_the_table_keeps_the_list(self, live_server) -> None:
        server, base = live_server
        assert _save_features(base, "paths", marker=False)[0] == 200
        assert load_config(server.config_path).ui.shown_features == list(DEFAULT_SHOWN_FEATURES)

    def test_there_is_no_route_that_applies_a_box_on_its_own(self, live_server) -> None:
        _, base = live_server
        status, _, _ = _post(base, "/section/features", b"paths=on", "application/x-www-form-urlencoded")
        assert status in (404, 405)

    def test_the_list_is_never_section_broadcast(self, live_server) -> None:
        _, base = live_server
        assert _post_json(base, "/api/config/features/broadcast", {"paths": True}) == 403

    def test_unknown_fields_are_ignored(self, live_server) -> None:
        server, base = live_server
        assert _save_features(base, "zones", "warp_drive")[0] == 200
        assert load_config(server.config_path).ui.shown_features == ["zones"]

    def test_turning_features_off_writes_nothing_else(self, live_server) -> None:
        server, base = live_server
        cfg = load_config(server.config_path)
        cfg.ui.shown_features = list(FEATURE_KEYS)
        cfg.detection.enabled = True
        cfg.rttrpm_output.enabled = True
        cfg.ground_grid.enabled = True
        cfg.ground_grid.vertices = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]
        cfg.touch_remote.enabled = True
        cfg.paths.paths = [PathConfig(name="cross", active=True)]
        save_config(cfg, server.config_path)

        assert _save_features(base)[0] == 200

        out = load_config(server.config_path)
        assert out.ui.shown_features == []
        assert (out.detection.enabled, out.rttrpm_output.enabled, out.ground_grid.enabled) == (True, True, True)
        assert out.touch_remote.enabled is True
        assert [(p.name, p.active) for p in out.paths.paths] == [("cross", True)]

    @pytest.mark.parametrize(("remote_on", "refresh"), [(True, True), (False, False)])
    def test_the_page_reloads_only_when_the_remote_launcher_changes(self, live_server, remote_on, refresh) -> None:  # noqa: ANN001
        server, base = live_server
        cfg = load_config(server.config_path)
        cfg.touch_remote.enabled = remote_on
        save_config(cfg, server.config_path)
        _, headers = _save_features(base, "touch_remote")
        assert (headers.get("hx-refresh") == "true") is refresh


class TestTransfer:
    @pytest.mark.parametrize(
        ("ui", "expected"),
        [
            ({"shown_features": ["paths", "zones", "nope"]}, ["zones", "paths"]),
            ({"show_experimental_features": True}, list(FEATURE_KEYS)),
            ({"show_experimental_features": False}, None),
            ({"show_experimental_features": True, "shown_features": ["midi"]}, ["midi"]),
            ({"shown_features": "zones"}, list(DEFAULT_SHOWN_FEATURES)),
        ],
    )
    def test_an_import_carries_the_list(self, live_server, ui, expected) -> None:  # noqa: ANN001
        server, base = live_server
        cfg = load_config(server.config_path)
        cfg.ui.shown_features = ["paths"]
        save_config(cfg, server.config_path)
        assert _post_json(base, "/api/config/import", {"ui": ui}) == 200
        out = load_config(server.config_path).ui.shown_features
        # A false old flag says nothing about the list: the station keeps its own.
        assert out == (["paths"] if expected is None else expected)

    def test_an_import_writes_nothing_the_list_gates(self, live_server) -> None:
        server, base = live_server
        cfg = load_config(server.config_path)
        cfg.ui.shown_features = list(FEATURE_KEYS)
        cfg.paths.paths = [PathConfig(name="cross", active=True)]
        save_config(cfg, server.config_path)
        body = {"ui": {"shown_features": []}, "paths": {"paths": [{"name": "cross", "active": True}]}}
        assert _post_json(base, "/api/config/import", body) == 200
        assert [(p.name, p.active) for p in load_config(server.config_path).paths.paths] == [("cross", True)]

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            ({"paths": True, "zones": False}, ["midi", "lens_distortion", "psn_input", "paths"]),
            ({"detection": "on", "midi": "off"}, ["zones", "lens_distortion", "psn_input", "detection"]),
            ({"warp_drive": True}, list(DEFAULT_SHOWN_FEATURES)),
        ],
    )
    def test_the_section_api_sets_only_the_keys_it_names(self, live_server, body, expected) -> None:  # noqa: ANN001
        server, base = live_server
        assert _post_json(base, "/api/config/features", body) == 200
        assert load_config(server.config_path).ui.shown_features == expected


class TestPage:
    def test_the_body_names_every_feature_that_is_on(self, live_server) -> None:
        _, base = live_server
        _save_features(base, "zones", "paths")
        _, body = _get(base, "/")
        assert re.search(r'<body class="feature-zones feature-paths">', body)

    def test_every_feature_has_its_hide_rule(self, live_server) -> None:
        _, base = live_server
        _, body = _get(base, "/")
        for key in FEATURE_KEYS:
            assert f'body:not(.feature-{key}) [data-feature="{key}"] {{ display: none !important; }}' in body

    def test_each_box_shows_its_feature_and_only_experiments_carry_the_badge(self, live_server) -> None:
        _, base = live_server
        _save_features(base, "detection")
        _, body = _get(base, "/")
        form = body[body.index('<div id="features-section"') :]
        form = form[: form.index("</table>")]
        assert re.search(r'name="detection"[^>]*\s+checked', form)
        assert not re.search(r'name="zones"[^>]*\s+checked', form)
        assert form.count('class="badge-experimental"') == 6

    def test_the_table_has_one_row_per_feature_with_its_title_and_description(self, live_server) -> None:
        _, base = live_server
        _, body = _get(base, "/")
        form = body[body.index('<div id="features-section"') :]
        form = form[: form.index("</table>")]
        assert '<input type="hidden" name="features_submitted" value="1" form="general-network-section">' in form
        assert "broadcastSection" not in form
        rows = re.findall(r"<tr>\s*<td class=\"feature-check\">(.*?)</tr>", form, re.S)
        assert len(rows) == len(FEATURE_KEYS)
        for key, row in zip(FEATURE_KEYS, rows, strict=True):
            box_id = "features-" + key.replace("_", "-")
            label = FEATURE_LABELS[key]
            assert f'id="{box_id}" name="{key}" form="general-network-section"' in row
            stored = "1" if key in DEFAULT_SHOWN_FEATURES else "0"
            assert f'aria-describedby="{box_id}-description" data-saved="{stored}"' in row
            assert f'data-title="{html_escape(label.title)}" data-off="{html_escape(label.when_off)}"' in row
            assert f'<label for="{box_id}">{html_escape(label.title)}</label>' in row
            description = html_escape(label.description)
            assert f'<td id="{box_id}-description" class="feature-description">{description}</td>' in row


class TestWizard:
    def test_lens_off_drops_the_lens_step_and_starts_pinhole(self, live_server) -> None:
        server, base = live_server
        cfg = load_config(server.config_path)
        cfg.camera.lens_k1, cfg.camera.lens_k2 = -0.2, 0.05
        cfg.ui.shown_features = []
        save_config(cfg, server.config_path)
        _, body = _get(base, "/wizard")
        assert "window.WIZ_STEPS = ['prep', 'grid', 'video', 'camera'," in body
        assert 'id="wiz_lens_k1" step="0.005"\n                   value="0.0"' in body
        assert "var LENS_ENABLED = false;" in body

    def test_lens_on_keeps_the_step_and_the_saved_pair(self, live_server) -> None:
        server, base = live_server
        cfg = load_config(server.config_path)
        cfg.camera.lens_k1 = -0.2
        save_config(cfg, server.config_path)
        _, body = _get(base, "/wizard")
        assert "'video', 'lens', 'camera'" in body
        assert 'value="-0.2"' in body
        assert "var LENS_ENABLED = true;" in body


class TestTouchRemote:
    @pytest.mark.parametrize(("features", "launcher"), [((), False), (("touch_remote",), True)])
    def test_the_launcher_needs_the_feature_and_the_switch(self, live_server, features, launcher) -> None:  # noqa: ANN001
        server, base = live_server
        cfg = load_config(server.config_path)
        cfg.touch_remote.enabled = True
        cfg.ui.shown_features = list(features)
        save_config(cfg, server.config_path)
        assert ("/assets/js/touch-remote.js" in _get(base, "/")[1]) is launcher

    def test_the_remote_refuses_while_its_feature_is_off(self, live_server) -> None:
        server, base = live_server
        cfg = load_config(server.config_path)
        cfg.touch_remote.enabled = True
        cfg.ui.shown_features = []
        save_config(cfg, server.config_path)
        assert _get(base, "/api/remote/state")[0] == 403
