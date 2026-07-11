# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Integration tests for the file-based template HTTP API.

Routes are exercised against a live :class:`ConfigWebServer`.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import pytest

import openfollow.web.discovery as discovery_module
from openfollow.configuration import (
    TriggerZoneConfig,
    load_config,
    save_config,
)
from openfollow.templates import TEMPLATE_VERSION
from openfollow.templates.loader import list_templates_by_type
from openfollow.templates.writer import write_user_template
from openfollow.web.server import ConfigWebServer

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Live-server fixture
# ---------------------------------------------------------------------------


def _find_free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, host: str = "127.0.0.1", timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.1):
                return True
        except OSError:
            time.sleep(0.05)
    return False


@pytest.fixture()
def live_server(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery_module.BeaconSender, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconSender, "stop", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "stop", lambda self: None)

    port = _find_free_tcp_port()
    config_path = tmp_path / "config.toml"
    server = ConfigWebServer(
        config_path=str(config_path),
        host="127.0.0.1",
        port=port,
        system_name="TestSystem",
    )
    server.start()
    assert _wait_for_port(port)
    yield server, f"http://127.0.0.1:{port}", str(config_path)
    server.stop()


def _get(base: str, path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _post_json(base: str, path: str, data: dict[str, Any]) -> tuple[int, str]:
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{base}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _post_form(base: str, path: str, data: dict[str, Any]) -> tuple[int, str]:
    body = urllib.parse.urlencode(data, doseq=True).encode()
    req = urllib.request.Request(
        f"{base}{path}",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _post_empty(base: str, path: str) -> tuple[int, str]:
    """POST with no body (``apply`` carries no body)."""
    req = urllib.request.Request(f"{base}{path}", data=b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _delete(base: str, path: str) -> tuple[int, str]:
    req = urllib.request.Request(f"{base}{path}", method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _get_raw(base: str, path: str) -> tuple[int, bytes, dict[str, str]]:
    """GET returning (status, raw bytes, headers) – for the export download."""
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=5) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def _post_raw(
    base: str,
    path: str,
    body: bytes,
    content_type: str = "application/json",
) -> tuple[int, str]:
    """POST a raw byte body (the import route reads ``wsgi.input`` directly)."""
    req = urllib.request.Request(
        f"{base}{path}",
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _osc_envelope_bytes(name: str = "Imported", **env: Any) -> bytes:
    """A valid ``osc_output`` template serialised for upload."""
    base: dict[str, Any] = {
        "version": 1,
        "type": "osc_output",
        "name": name,
        "payload": {"address": "/cue/[markerid]", "args": ["[x]", "[y]"]},
    }
    base.update(env)
    return json.dumps(base).encode()


def _templates_root(cfg_path: str) -> Path:
    return Path(cfg_path).parent / "templates"


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


class TestBootstrap:
    def test_seeded_on_start(self, live_server) -> None:
        _, _, cfg_path = live_server
        sysdir = _templates_root(cfg_path) / "system"
        assert sysdir.is_dir()
        files = sorted(p.name for p in sysdir.iterdir() if p.is_file())
        assert files == [
            "osc_output.adm-osc-3d.oftemplate",
            "osc_output.adm-osc.oftemplate",
            "osc_output.dnb-absolute.oftemplate",
            "osc_output.etc-eos.oftemplate",
        ]


# ---------------------------------------------------------------------------
# GET /api/templates
# ---------------------------------------------------------------------------


class TestList:
    def test_lists_system_templates_for_osc_output(self, live_server) -> None:
        _, base, _ = live_server
        status, body = _get(base, "/api/templates?type=osc_output")
        assert status == 200
        payload = json.loads(body)
        names = sorted(t["name"] for t in payload["templates"])
        assert names == ["ADM-OSC 2D", "ADM-OSC 3D", "ETC Eos", "d&b absolute"]
        for entry in payload["templates"]:
            assert entry["is_system"] is True
            assert entry["type"] == "osc_output"
            assert entry["error"] == ""

    def test_lists_user_templates_alongside_system(self, live_server) -> None:
        _, base, cfg_path = live_server
        write_user_template(
            _templates_root(cfg_path),
            "osc_output",
            "Mine",
            {"address": "/mine", "args": []},
        )
        status, body = _get(base, "/api/templates?type=osc_output")
        assert status == 200
        names = sorted(t["name"] for t in json.loads(body)["templates"])
        assert "Mine" in names
        assert len(names) == 5

    def test_filters_by_type(self, live_server) -> None:
        _, base, cfg_path = live_server
        # Zones payload needs an explicit ``zones`` array; the writer
        # rejects an empty payload (apply would wipe the section).
        write_user_template(
            _templates_root(cfg_path),
            "zones",
            "Zones A",
            {"zones": []},
        )
        status, body = _get(base, "/api/templates?type=zones")
        assert status == 200
        items = json.loads(body)["templates"]
        assert [t["name"] for t in items] == ["Zones A"]
        status, body = _get(base, "/api/templates?type=osc_output")
        assert "Zones A" not in body

    def test_missing_type_rejected(self, live_server) -> None:
        _, base, _ = live_server
        status, body = _get(base, "/api/templates")
        assert status == 400
        assert "unknown type" in json.loads(body)["error"]

    def test_unknown_type_rejected(self, live_server) -> None:
        _, base, _ = live_server
        status, body = _get(base, "/api/templates?type=bogus")
        assert status == 400
        assert "bogus" in json.loads(body)["error"]

    def test_unreadable_file_surfaces_with_error(self, live_server) -> None:
        # The list endpoint surfaces malformed files as error entries
        # so the UI can render them disabled.
        _, base, cfg_path = live_server
        userdir = _templates_root(cfg_path) / "user"
        userdir.mkdir(parents=True, exist_ok=True)
        (userdir / "osc_output.broken.oftemplate").write_text("{not json")
        status, body = _get(base, "/api/templates?type=osc_output")
        assert status == 200
        items = json.loads(body)["templates"]
        broken = [t for t in items if t["filename"].endswith("broken.oftemplate")]
        assert len(broken) == 1
        assert broken[0]["error"]
        # Failed-load entries report empty type / id / name.
        assert broken[0]["type"] == ""
        assert broken[0]["id"] == ""
        assert broken[0]["name"] == ""

    def test_unreadable_file_scoped_to_filename_type_prefix(self, live_server) -> None:
        _, base, cfg_path = live_server
        userdir = _templates_root(cfg_path) / "user"
        userdir.mkdir(parents=True, exist_ok=True)
        (userdir / "osc_output.broken.oftemplate").write_text("{not json")
        status, body = _get(base, "/api/templates?type=osc_output")
        assert status == 200
        names = [t["filename"] for t in json.loads(body)["templates"]]
        assert "osc_output.broken.oftemplate" in names
        # Scoped to the filename's ``<type>`` prefix; absent from others.
        for other in ("zones", "camera_grid"):
            status, body = _get(base, f"/api/templates?type={other}")
            assert status == 200
            names = [t["filename"] for t in json.loads(body)["templates"]]
            assert "osc_output.broken.oftemplate" not in names

    def test_unreadable_file_without_type_prefix_dropped(self, live_server) -> None:
        # A file not matching ``<type>.<slug>.oftemplate`` can't
        # be classified by the loader and is dropped from every chooser.
        _, base, cfg_path = live_server
        userdir = _templates_root(cfg_path) / "user"
        userdir.mkdir(parents=True, exist_ok=True)
        (userdir / "stray.oftemplate").write_text("{not json")
        for tt in ("osc_output", "zones", "camera_grid"):
            status, body = _get(base, f"/api/templates?type={tt}")
            assert status == 200
            names = [t["filename"] for t in json.loads(body)["templates"]]
            assert "stray.oftemplate" not in names


# ---------------------------------------------------------------------------
# POST /api/templates/<type>/save
# ---------------------------------------------------------------------------


class TestSaveOscOutput:
    def test_writes_file_and_returns_metadata(self, live_server) -> None:
        _, base, cfg_path = live_server
        status, body = _post_json(
            base,
            "/api/templates/osc_output/save",
            {
                "name": "My Cue",
                "payload": {"address": "/cue/[markerid]", "args": ["[x]"]},
            },
        )
        assert status == 200
        payload = json.loads(body)
        assert payload["ok"] is True
        assert payload["filename"] == "osc_output.my-cue.oftemplate"
        assert payload["name"] == "My Cue"
        assert payload["id"] and len(payload["id"]) == 32
        path = _templates_root(cfg_path) / "user" / payload["filename"]
        assert path.is_file()

    def test_conflict_numbering_visible(self, live_server) -> None:
        _, base, _ = live_server
        _post_json(
            base,
            "/api/templates/osc_output/save",
            {
                "name": "Cue",
                "payload": {"address": "/x", "args": []},
            },
        )
        status, body = _post_json(
            base,
            "/api/templates/osc_output/save",
            {
                "name": "Cue",
                "payload": {"address": "/x", "args": []},
            },
        )
        assert status == 200
        payload = json.loads(body)
        assert payload["filename"] == "osc_output.cue-1.oftemplate"
        assert payload["name"] == "Cue (1)"

    def test_missing_name_rejected(self, live_server) -> None:
        _, base, _ = live_server
        status, _ = _post_json(
            base,
            "/api/templates/osc_output/save",
            {
                "payload": {"address": "/x", "args": []},
            },
        )
        assert status == 400

    def test_invalid_payload_rejected(self, live_server) -> None:
        _, base, _ = live_server
        # Missing address triggers per-type validator failure.
        status, body = _post_json(
            base,
            "/api/templates/osc_output/save",
            {
                "name": "Bad",
                "payload": {"args": []},
            },
        )
        assert status == 400
        assert "address" in json.loads(body)["error"]

    def test_non_dict_body_rejected(self, live_server) -> None:
        _, base, _ = live_server
        # Empty array is valid JSON but not an object; route returns 400.
        req = urllib.request.Request(
            f"{base}/api/templates/osc_output/save",
            data=b"[]",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                status = r.status
                body = r.read().decode()
        except urllib.error.HTTPError as e:
            status = e.code
            body = e.read().decode()
        assert status == 400
        assert "Expected a JSON object" in body

    def test_invalid_json_rejected(self, live_server) -> None:
        _, base, _ = live_server
        req = urllib.request.Request(
            f"{base}/api/templates/osc_output/save",
            data=b"{not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                status = r.status
                body = r.read().decode()
        except urllib.error.HTTPError as e:
            status = e.code
            body = e.read().decode()
        assert status == 400
        assert "Invalid JSON" in body

    def test_unknown_type_rejected(self, live_server) -> None:
        _, base, _ = live_server
        status, body = _post_json(
            base,
            "/api/templates/bogus/save",
            {
                "name": "x",
            },
        )
        assert status == 400
        assert "unknown type" in json.loads(body)["error"]

    def test_payload_must_be_object_for_osc_output(self, live_server) -> None:
        _, base, _ = live_server
        status, body = _post_json(
            base,
            "/api/templates/osc_output/save",
            {
                "name": "x",
                "payload": "string-not-object",
            },
        )
        assert status == 400
        assert "must be an object" in json.loads(body)["error"]


class TestSaveCameraGrid:
    def test_reads_current_camera_grid_from_config(self, live_server) -> None:
        _, base, cfg_path = live_server
        cfg = load_config(cfg_path)
        cfg.camera.pos_x = 1.5
        cfg.grid.width = 12.0
        save_config(cfg, cfg_path)
        status, body = _post_json(
            base,
            "/api/templates/camera_grid/save",
            {
                "name": "Indoor Rig",
            },
        )
        assert status == 200
        payload = json.loads(body)
        # Saved file carries the ``camera`` and ``grid`` snapshots verbatim.
        from openfollow.templates.loader import find_template

        entry = find_template(_templates_root(cfg_path), payload["filename"])
        assert entry is not None and entry.template is not None
        assert entry.template.payload["camera"]["pos_x"] == 1.5
        assert entry.template.payload["grid"]["width"] == 12.0


class TestSaveZones:
    def test_reads_current_zones_section_from_config(self, live_server) -> None:
        _, base, cfg_path = live_server
        cfg = load_config(cfg_path)
        cfg.trigger_zones.enabled = True
        cfg.trigger_zones.zones.append(
            TriggerZoneConfig(name="Stage", vertices=[[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]]),
        )
        save_config(cfg, cfg_path)
        status, body = _post_json(
            base,
            "/api/templates/zones/save",
            {
                "name": "Studio A",
            },
        )
        assert status == 200
        payload = json.loads(body)
        from openfollow.templates.loader import find_template

        entry = find_template(_templates_root(cfg_path), payload["filename"])
        assert entry is not None and entry.template is not None
        assert entry.template.payload["enabled"] is True
        assert len(entry.template.payload["zones"]) == 1
        assert entry.template.payload["zones"][0]["name"] == "Stage"


# ---------------------------------------------------------------------------
# POST /api/templates/<filename>/apply
# ---------------------------------------------------------------------------


class TestApplyOscOutput:
    def test_creates_new_row_from_template(self, live_server) -> None:
        _, base, cfg_path = live_server
        status, body = _post_empty(
            base,
            "/api/templates/osc_output.adm-osc.oftemplate/apply",
        )
        assert status == 200
        payload = json.loads(body)
        assert payload["ok"] is True
        assert payload["row_id"]
        cfg = load_config(cfg_path)
        rows = cfg.osc_transmitters.transmitters
        assert len(rows) == 1
        assert rows[0].name == "ADM-OSC 2D"
        assert rows[0].address == "/adm/obj/[markerid]/xyz"
        assert rows[0].args == ["[x.frac]", "[y.frac]", "0"]
        assert rows[0].id == payload["row_id"]

    def test_apply_restores_extended_fields(self, live_server) -> None:
        # Apply restores name / host / port / protocol / address / args /
        # rate_hz / trigger; ``enabled`` and ``markers`` are forced to
        # apply-time defaults regardless of payload.
        _, base, cfg_path = live_server
        write_user_template(
            _templates_root(cfg_path),
            "osc_output",
            "ETC Stage",
            {
                "name": "ETC Stage Left",
                "destination_id": "dest-5",
                "address": "/eos/go",
                "args": ["[x]", "[y]"],
                "rate_hz": 60,
                "trigger": {"kind": "stream", "rate_hz": 60},
            },
        )
        status, body = _post_empty(
            base,
            "/api/templates/osc_output.etc-stage.oftemplate/apply",
        )
        assert status == 200
        cfg = load_config(cfg_path)
        rows = cfg.osc_transmitters.transmitters
        assert len(rows) == 1
        row = rows[0]
        assert row.name == "ETC Stage Left"
        assert row.destination_id == "dest-5"
        assert row.address == "/eos/go"
        assert row.args == ["[x]", "[y]"]
        assert row.rate_hz == 60
        from openfollow.configuration import StreamTrigger

        assert isinstance(row.trigger, StreamTrigger)
        assert row.trigger.rate_hz == 60

    def test_apply_forces_enabled_false_for_legitimate_template(
        self,
        live_server,
    ) -> None:
        # The strict schema refuses ``enabled`` / ``markers`` keys at
        # load time, so apply never sees them. This exercises the apply
        # contract for a legitimate template: force ``enabled=False`` and
        # empty ``markers``.
        _, base, cfg_path = live_server
        write_user_template(
            _templates_root(cfg_path),
            "osc_output",
            "Hot",
            {"address": "/x"},
        )
        status, _ = _post_empty(
            base,
            "/api/templates/osc_output.hot.oftemplate/apply",
        )
        assert status == 200
        cfg = load_config(cfg_path)
        row = cfg.osc_transmitters.transmitters[0]
        assert row.enabled is False
        assert row.markers == []

    def test_strict_schema_refuses_enabled_key(self) -> None:
        # ``enabled`` / ``markers`` are excluded from the schema (apply
        # forces both; they belong to the binding, not the template).
        # The writer's strict-schema gate refuses them before any write.
        from openfollow.templates.schema import TemplateValidationError

        with pytest.raises(TemplateValidationError, match="unknown key"):
            write_user_template(
                # Writer raises before touching the filesystem.
                Path("/tmp"),  # noqa: S108 – never reached
                "osc_output",
                "Hot",
                {"address": "/x", "enabled": True, "markers": ["7"]},
            )


class TestSaveOscBindingAsTemplate:
    """``POST /section/osc_binding/<row_id>/save_as_template`` captures
    the row's live form state into a template payload, reusing the row's
    Save form parsers for trigger / message / protocol coercion."""

    def test_captures_extended_fields_from_form(self, live_server) -> None:
        _, base, cfg_path = live_server
        # The row only anchors the URL; the endpoint builds a transient
        # row from the form body and writes only the template.
        _post_form(base, "/section/osc_bindings/add", {})
        cfg = load_config(cfg_path)
        row_id = cfg.osc_transmitters.transmitters[0].id
        status, body = _post_form(
            base,
            f"/section/osc_binding/{row_id}/save_as_template",
            {
                "template_name": "Stage Cue",
                "name": "Stage Cue (live)",
                "destination_id": "dest-99",
                "markers": "0",
                "trigger.type": "stream",
                "trigger.rate_hz": "60",
                "osc_message": "/cue/[markerid]/go [x]",
            },
        )
        assert status == 200
        assert "OSC Transmitters" in body
        from openfollow.templates.loader import find_template

        entry = find_template(
            _templates_root(cfg_path),
            "osc_output.stage-cue.oftemplate",
        )
        assert entry is not None and entry.template is not None
        p = entry.template.payload
        assert p["name"] == "Stage Cue (live)"
        assert p["destination_id"] == "dest-99"
        assert p["address"] == "/cue/[markerid]/go"
        assert p["args"] == ["[x]"]
        assert p["rate_hz"] == 60
        assert p["trigger"]["kind"] == "stream"
        assert p["trigger"]["rate_hz"] == 60
        # ``enabled`` / ``markers`` are dropped even though the form
        # supplied markers.
        assert "enabled" not in p
        assert "markers" not in p

    def test_rejects_missing_template_name(self, live_server) -> None:
        _, base, cfg_path = live_server
        _post_form(base, "/section/osc_bindings/add", {})
        cfg = load_config(cfg_path)
        row_id = cfg.osc_transmitters.transmitters[0].id
        status, body = _post_form(
            base,
            f"/section/osc_binding/{row_id}/save_as_template",
            {"osc_message": "/x"},
        )
        assert status == 400
        assert "template name" in body.lower()

    def test_rejects_missing_address(self, live_server) -> None:
        _, base, cfg_path = live_server
        _post_form(base, "/section/osc_bindings/add", {})
        cfg = load_config(cfg_path)
        row_id = cfg.osc_transmitters.transmitters[0].id
        status, body = _post_form(
            base,
            f"/section/osc_binding/{row_id}/save_as_template",
            {"template_name": "X", "osc_message": ""},
        )
        assert status == 400
        assert "address" in body.lower()

    def test_captures_stream_mode_and_min_change(self, live_server) -> None:
        # ``StreamTrigger.mode`` and the per-axis threshold round-trip
        # through save → apply, recreating the send-throttle on a fresh row.
        _, base, cfg_path = live_server
        _post_form(base, "/section/osc_bindings/add", {})
        cfg = load_config(cfg_path)
        row_id = cfg.osc_transmitters.transmitters[0].id
        status, _ = _post_form(
            base,
            f"/section/osc_binding/{row_id}/save_as_template",
            {
                "template_name": "OnChangeStage",
                "name": "On-change stage",
                "destination_id": "dest-1",
                "trigger.type": "stream",
                "trigger.rate_hz": "60",
                "trigger.mode": "on_change",
                "trigger.min_change_m": "0.1",
                "osc_message": "/cue/[markerid] [x]",
            },
        )
        assert status == 200
        from openfollow.templates.loader import find_template

        entry = find_template(
            _templates_root(cfg_path),
            "osc_output.onchangestage.oftemplate",
        )
        assert entry is not None and entry.template is not None
        trigger = entry.template.payload["trigger"]
        assert trigger["mode"] == "on_change"
        assert trigger["min_change_m"] == 0.1
        _post_empty(
            base,
            "/api/templates/osc_output.onchangestage.oftemplate/apply",
        )
        cfg = load_config(cfg_path)
        from openfollow.configuration import StreamTrigger

        applied = cfg.osc_transmitters.transmitters[-1]
        assert isinstance(applied.trigger, StreamTrigger)
        assert applied.trigger.mode == "on_change"
        assert applied.trigger.min_change_m == 0.1

    def test_captures_hotkey_trigger_with_modifiers(self, live_server) -> None:
        # ``trigger.modifiers`` is a checkbox group, so the route must
        # lift all values from Bottle's MultiDict (not just the first)
        # for the modifier set to round-trip through the template.
        _, base, cfg_path = live_server
        _post_form(base, "/section/osc_bindings/add", {})
        cfg = load_config(cfg_path)
        row_id = cfg.osc_transmitters.transmitters[0].id
        status, _ = _post_form(
            base,
            f"/section/osc_binding/{row_id}/save_as_template",
            {
                "template_name": "Hotkey",
                "name": "Stage cue",
                "trigger.type": "hotkey",
                "trigger.key": "Space",
                "trigger.modifiers": ["ctrl", "shift"],
                "trigger.edge": "press",
                "osc_message": "/cue/go",
            },
        )
        assert status == 200
        from openfollow.templates.loader import find_template

        entry = find_template(
            _templates_root(cfg_path),
            "osc_output.hotkey.oftemplate",
        )
        assert entry is not None and entry.template is not None
        trigger = entry.template.payload["trigger"]
        assert trigger["kind"] == "hotkey"
        assert trigger["key"] == "Space"
        assert sorted(trigger["modifiers"]) == ["ctrl", "shift"]

    def test_save_followed_by_apply_round_trips_full_row(self, live_server) -> None:
        _, base, cfg_path = live_server
        _post_form(base, "/section/osc_bindings/add", {})
        cfg = load_config(cfg_path)
        row_id = cfg.osc_transmitters.transmitters[0].id
        _post_form(
            base,
            f"/section/osc_binding/{row_id}/save_as_template",
            {
                "template_name": "RoundTrip",
                "name": "Source row",
                "destination_id": "dest-7",
                "trigger.type": "stream",
                "trigger.rate_hz": "20",
                "osc_message": "/round/[markerid] [x]",
            },
        )
        status, _ = _post_empty(
            base,
            "/api/templates/osc_output.roundtrip.oftemplate/apply",
        )
        assert status == 200
        cfg = load_config(cfg_path)
        applied = cfg.osc_transmitters.transmitters[-1]  # the apply row
        assert applied.name == "Source row"
        assert applied.destination_id == "dest-7"
        assert applied.address == "/round/[markerid]"
        assert applied.args == ["[x]"]
        assert applied.rate_hz == 20
        assert applied.enabled is False  # forced
        assert applied.markers == []  # forced


class TestApplyCameraGrid:
    def test_replaces_camera_and_grid_with_confirm(self, live_server) -> None:
        _, base, cfg_path = live_server
        write_user_template(
            _templates_root(cfg_path),
            "camera_grid",
            "Indoor",
            {"camera": {"pos_x": 2.5}, "grid": {"width": 20.0}},
        )
        status, body = _post_empty(
            base,
            "/api/templates/camera_grid.indoor.oftemplate/apply?confirm=1",
        )
        assert status == 200
        assert json.loads(body)["ok"] is True
        cfg = load_config(cfg_path)
        assert cfg.camera.pos_x == 2.5
        assert cfg.grid.width == 20.0

    def test_partial_camera_only_template_rejected_at_write_time(
        self,
        live_server,
    ) -> None:
        _, _, cfg_path = live_server
        from openfollow.templates.schema import TemplateValidationError

        with pytest.raises(TemplateValidationError, match="must contain BOTH"):
            write_user_template(
                _templates_root(cfg_path),
                "camera_grid",
                "CameraOnly",
                {"camera": {"pos_x": 9.9}},
            )

    def test_partial_grid_only_template_rejected_at_write_time(
        self,
        live_server,
    ) -> None:
        _, _, cfg_path = live_server
        from openfollow.templates.schema import TemplateValidationError

        with pytest.raises(TemplateValidationError, match="must contain BOTH"):
            write_user_template(
                _templates_root(cfg_path),
                "camera_grid",
                "GridOnly",
                {"grid": {"width": 30.0}},
            )

    def test_requires_confirm(self, live_server) -> None:
        _, base, cfg_path = live_server
        write_user_template(
            _templates_root(cfg_path),
            "camera_grid",
            "Indoor",
            {"camera": {}, "grid": {}},
        )
        status, body = _post_empty(
            base,
            "/api/templates/camera_grid.indoor.oftemplate/apply",
        )
        assert status == 400
        assert "?confirm=1" in json.loads(body)["error"]


class TestApplyZones:
    def test_replaces_trigger_zones_with_confirm(self, live_server) -> None:
        _, base, cfg_path = live_server
        write_user_template(
            _templates_root(cfg_path),
            "zones",
            "Studio",
            {
                "enabled": True,
                "zones": [
                    {
                        "name": "Center",
                        "vertices": [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]],
                    }
                ],
            },
        )
        status, body = _post_empty(
            base,
            "/api/templates/zones.studio.oftemplate/apply?confirm=1",
        )
        assert status == 200
        assert json.loads(body)["ok"] is True
        cfg = load_config(cfg_path)
        assert cfg.trigger_zones.enabled is True
        assert len(cfg.trigger_zones.zones) == 1
        assert cfg.trigger_zones.zones[0].name == "Center"

    def test_requires_confirm(self, live_server) -> None:
        _, base, cfg_path = live_server
        write_user_template(
            _templates_root(cfg_path),
            "zones",
            "X",
            {"zones": []},
        )
        status, _ = _post_empty(
            base,
            "/api/templates/zones.x.oftemplate/apply",
        )
        assert status == 400


class TestApplyEdgeCases:
    def test_unknown_filename_returns_404(self, live_server) -> None:
        _, base, _ = live_server
        status, _ = _post_empty(
            base,
            "/api/templates/osc_output.nope.oftemplate/apply",
        )
        assert status == 404

    def test_unsafe_filename_returns_400(self, live_server) -> None:
        _, base, _ = live_server
        # Dotfile names are rejected by the safety check.
        status, _ = _post_empty(
            base,
            "/api/templates/.oftemplate/apply",
        )
        assert status == 400

    def test_filename_with_dotdot_rejected(self, live_server) -> None:
        # Path-traversal guard: Bottle strips ``/``, but a ``..`` between
        # dots could otherwise escape the templates folder.
        _, base, _ = live_server
        status, _ = _post_empty(
            base,
            "/api/templates/osc..output.oftemplate/apply",
        )
        assert status == 400

    def test_filename_with_backslash_rejected(self, live_server) -> None:
        # URL-encode the ``\`` so it reaches the handler intact; the
        # filename-safety gate refuses it.
        _, base, _ = live_server
        status, _ = _post_empty(
            base,
            "/api/templates/osc%5Coutput.oftemplate/apply",
        )
        assert status == 400

    def test_malformed_template_surfaces_loader_error(self, live_server) -> None:
        _, base, cfg_path = live_server
        # Malformed file written directly into ``user/`` to hit the
        # loader's error path.
        bad = _templates_root(cfg_path) / "user"
        bad.mkdir(parents=True, exist_ok=True)
        (bad / "osc_output.broken.oftemplate").write_text("{not json")
        status, body = _post_empty(
            base,
            "/api/templates/osc_output.broken.oftemplate/apply",
        )
        assert status == 400
        assert "invalid JSON" in json.loads(body)["error"]


# ---------------------------------------------------------------------------
# DELETE /api/templates/<filename>
# ---------------------------------------------------------------------------


class TestDelete:
    def test_deletes_user_template(self, live_server) -> None:
        _, base, cfg_path = live_server
        path = write_user_template(
            _templates_root(cfg_path),
            "osc_output",
            "Mine",
            {"address": "/mine", "args": []},
        )
        status, body = _delete(base, f"/api/templates/{path.name}")
        assert status == 200
        assert json.loads(body)["ok"] is True
        assert not path.exists()

    def test_system_template_returns_403(self, live_server) -> None:
        _, base, cfg_path = live_server
        sysdir = _templates_root(cfg_path) / "system"
        target = next(sysdir.glob("*.oftemplate"))
        status, body = _delete(base, f"/api/templates/{target.name}")
        assert status == 403
        assert "system" in json.loads(body)["error"].lower()
        # File untouched.
        assert target.is_file()

    def test_unknown_filename_returns_404(self, live_server) -> None:
        _, base, _ = live_server
        status, _ = _delete(
            base,
            "/api/templates/osc_output.nope.oftemplate",
        )
        assert status == 404

    def test_unsafe_filename_returns_400(self, live_server) -> None:
        _, base, _ = live_server
        # Names without the ``.oftemplate`` suffix fail the gate.
        status, _ = _delete(base, "/api/templates/foo.json")
        assert status == 400


# ---------------------------------------------------------------------------
# POST /section/osc_templates
# ---------------------------------------------------------------------------


class TestSectionOscTemplatesRewrite:
    def test_save_modal_lands_on_disk(self, live_server) -> None:
        _, base, cfg_path = live_server
        body = urllib.parse.urlencode(
            {
                "name": "Modal Save",
                "osc_message": "/cue/go [x]",
            }
        ).encode()
        req = urllib.request.Request(
            f"{base}/section/osc_templates",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200
            html = r.read().decode()
            assert "saved" in html.lower()
        entries = list_templates_by_type(_templates_root(cfg_path), "osc_output")
        user = [e for e in entries if not e.is_system]
        assert len(user) == 1
        assert user[0].template is not None
        assert user[0].template.name == "Modal Save"

    def test_save_modal_invalid_address_rejected(self, live_server) -> None:
        _, base, _ = live_server
        body = urllib.parse.urlencode(
            {
                "name": "x",
                "osc_message": "no-leading-slash",
            }
        ).encode()
        req = urllib.request.Request(
            f"{base}/section/osc_templates",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
        assert status == 400


# ---------------------------------------------------------------------------
# Section render embeds disk-loaded user templates in the dropdown
# ---------------------------------------------------------------------------


class TestSectionRenderUsesDiskTemplates:
    def test_user_template_appears_in_section(self, live_server) -> None:
        _, base, cfg_path = live_server
        write_user_template(
            _templates_root(cfg_path),
            "osc_output",
            "From Disk",
            {"address": "/x", "args": []},
        )
        status, body = _get(base, "/section/osc_bindings")
        assert status == 200
        # User templates appear under the "Custom Templates" optgroup.
        assert "Custom Templates" in body
        assert "From Disk" in body

    def test_add_resolves_user_template_filename_from_disk(self, live_server) -> None:
        # User-template dropdown values are ``file:<filename>``
        # (filesystem-unique) rather than the envelope id (not unique
        # across files); the add route dispatches on the ``file:`` prefix
        # via ``find_template``.
        _, base, cfg_path = live_server
        path = write_user_template(
            _templates_root(cfg_path),
            "osc_output",
            "DiskTpl",
            {"address": "/disk/[markerid]", "args": ["[x]"]},
            template_id="disk-tpl-id",
        )
        assert path.is_file()
        body = urllib.parse.urlencode(
            {"template_id": f"file:{path.name}"},
        ).encode()
        req = urllib.request.Request(
            f"{base}/section/osc_bindings/add",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200
        cfg = load_config(cfg_path)
        rows = cfg.osc_transmitters.transmitters
        assert len(rows) == 1
        assert rows[0].name == "DiskTpl"
        assert rows[0].address == "/disk/[markerid]"
        assert rows[0].args == ["[x]"]

    def test_add_resolves_two_user_templates_with_same_envelope_id(
        self,
        live_server,
    ) -> None:
        # Envelope ids aren't unique-enforced (file copies / synced
        # installs collide), but filenames are. Both templates must stay
        # selectable; an id-keyed lookup would hide the second.
        _, base, cfg_path = live_server
        root = _templates_root(cfg_path)
        path_a = write_user_template(
            root,
            "osc_output",
            "TplA",
            {"address": "/a", "args": ["[x]"]},
            template_id="dup-id",
        )
        path_b = write_user_template(
            root,
            "osc_output",
            "TplB",
            {"address": "/b", "args": ["[y]"]},
            template_id="dup-id",  # same envelope id, different file
        )
        assert path_a.name != path_b.name  # writer's filename suffix
        # Apply B by filename must reach B's payload, not A's.
        body = urllib.parse.urlencode(
            {"template_id": f"file:{path_b.name}"},
        ).encode()
        req = urllib.request.Request(
            f"{base}/section/osc_bindings/add",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200
        cfg = load_config(cfg_path)
        rows = cfg.osc_transmitters.transmitters
        assert len(rows) == 1
        assert rows[0].address == "/b"


class TestSaveAsTemplateDirtyGate:
    """Every "Save as template" button opts into the dirty-state gate so
    editing a form disables it until the change is committed; templates
    capture disk state, so saving mid-edit would template the pre-edit
    value.

    These assert the rendered HTML markup hooks only; the disable-on-input
    behaviour itself lives in JS in ``base.tpl``.
    """

    def test_camera_form_marked_as_template_form(self, live_server) -> None:
        """Camera ``<form>`` declares ``data-template-form`` so
        ``input`` / ``change`` events reach the gate's scope."""
        _, base, _ = live_server
        status, body = _get(base, "/")
        assert status == 200
        # Substring match: Bottle template whitespace varies across releases.
        assert 'id="camera-section"' in body
        assert 'data-template-form="1"' in body

    def test_grid_form_marked_as_template_form(self, live_server) -> None:
        _, base, _ = live_server
        status, body = _get(base, "/")
        assert status == 200
        assert 'id="grid-section"' in body
        # One per opted-in form: camera + grid + osc-row + zones >= 4.
        assert body.count('data-template-form="1"') >= 4

    def test_camera_save_as_template_button_has_dirty_deps(
        self,
        live_server,
    ) -> None:
        """Save-as-template button declares ``data-template-save`` and a
        ``data-template-deps`` selector watching both camera and grid;
        the template captures the pair, so editing either gates it."""
        _, base, _ = live_server
        status, body = _get(base, "/")
        assert status == 200
        assert "data-template-save" in body
        assert 'data-template-deps="#camera-section, #grid-section"' in body

    def test_zone_editor_marked_as_template_form(self, live_server) -> None:
        _, base, _ = live_server
        status, body = _get(base, "/")
        assert status == 200
        assert 'id="zone-editor-section"' in body
        assert 'id="trigger-zones-section"' in body
        # Gates on both zone-editor-section (per-zone detail / drawing) and
        # trigger-zones-section (zone defaults).
        assert 'data-template-deps="#zone-editor-section, #trigger-zones-section"' in body
        assert 'id="trigger-zones-section"' in body
        assert body.count('data-template-form="1"') >= 5


# ---------------------------------------------------------------------------
# GET /api/templates/<filename>/export
# ---------------------------------------------------------------------------


class TestExport:
    def test_export_user_template_round_trips(self, live_server) -> None:
        _, base, _ = live_server
        status, body = _post_json(
            base,
            "/api/templates/osc_output/save",
            {"name": "Export Me", "payload": {"address": "/a/[markerid]", "args": ["[x]"]}},
        )
        assert status == 200, body
        filename = json.loads(body)["filename"]

        status, raw, headers = _get_raw(base, f"/api/templates/{filename}/export")
        assert status == 200
        # Download, not inline; canonical extension on the offered name.
        disp = headers.get("Content-Disposition", "")
        assert "attachment" in disp
        assert filename in disp
        assert disp.rstrip('"').endswith(".oftemplate")
        # The body is a complete, re-importable envelope.
        env = json.loads(raw.decode())
        assert env["type"] == "osc_output"
        assert env["name"] == "Export Me"
        assert env["payload"]["address"] == "/a/[markerid]"

    def test_export_system_template(self, live_server) -> None:
        _, base, _ = live_server
        status, raw, headers = _get_raw(base, "/api/templates/osc_output.adm-osc.oftemplate/export")
        assert status == 200
        assert "attachment" in headers.get("Content-Disposition", "")
        assert json.loads(raw.decode())["type"] == "osc_output"

    def test_export_unknown_filename_404(self, live_server) -> None:
        _, base, _ = live_server
        status, _, _ = _get_raw(base, "/api/templates/osc_output.nope.oftemplate/export")
        assert status == 404

    def test_export_unreadable_file_400(self, live_server) -> None:
        _, base, cfg_path = live_server
        userdir = _templates_root(cfg_path) / "user"
        userdir.mkdir(parents=True, exist_ok=True)
        (userdir / "osc_output.broken.oftemplate").write_text("{not json")
        status, raw, _ = _get_raw(base, "/api/templates/osc_output.broken.oftemplate/export")
        assert status == 400
        assert "error" in json.loads(raw.decode())

    def test_export_invalid_filename_400(self, live_server) -> None:
        _, base, _ = live_server
        # No template suffix → rejected before any disk lookup.
        status, _, _ = _get_raw(base, "/api/templates/stray.txt/export")
        assert status == 400

    def test_export_sanitizes_unsafe_filename_in_header(self, live_server) -> None:
        # The filename gate admits a double quote, so a hand-placed file could
        # otherwise break the quoted Content-Disposition value. The offered
        # name is sanitised to [A-Za-z0-9._-] (the quote becomes '-').
        _, base, cfg_path = live_server
        userdir = _templates_root(cfg_path) / "user"
        userdir.mkdir(parents=True, exist_ok=True)
        (userdir / 'osc_output.a"b.oftemplate').write_text(
            json.dumps({"version": 1, "type": "osc_output", "name": "Quoted", "payload": {"address": "/q", "args": []}})
        )
        status, raw, headers = _get_raw(base, "/api/templates/osc_output.a%22b.oftemplate/export")
        assert status == 200
        assert headers.get("Content-Disposition") == 'attachment; filename="osc_output.a-b.oftemplate"'
        assert json.loads(raw.decode())["name"] == "Quoted"

    def test_export_legacy_file_offered_as_canonical(self, live_server) -> None:
        _, base, cfg_path = live_server
        userdir = _templates_root(cfg_path) / "user"
        userdir.mkdir(parents=True, exist_ok=True)
        # A file saved by an older build still exports – under the new name.
        legacy = userdir / "osc_output.old.openfollowtemplate"
        legacy.write_text(
            json.dumps(
                {
                    "version": 1,
                    "type": "osc_output",
                    "name": "Old One",
                    "payload": {"address": "/o", "args": []},
                }
            )
        )
        status, raw, headers = _get_raw(base, "/api/templates/osc_output.old.openfollowtemplate/export")
        assert status == 200
        assert "osc_output.old.oftemplate" in headers.get("Content-Disposition", "")
        assert json.loads(raw.decode())["name"] == "Old One"


# ---------------------------------------------------------------------------
# POST /api/templates/import
# ---------------------------------------------------------------------------


class TestImport:
    def test_import_happy_lands_and_lists(self, live_server) -> None:
        _, base, _ = live_server
        status, body = _post_raw(
            base,
            "/api/templates/import?filename=share.oftemplate",
            _osc_envelope_bytes(name="Shared Cue"),
        )
        assert status == 200, body
        data = json.loads(body)
        assert data["ok"] is True
        assert data["type"] == "osc_output"
        assert data["filename"].endswith(".oftemplate")
        # Shows up in the type-scoped list as a user template.
        status, body = _get(base, "/api/templates?type=osc_output")
        names = [t["name"] for t in json.loads(body)["templates"]]
        assert "Shared Cue" in names

    def test_import_bad_json_400(self, live_server) -> None:
        _, base, _ = live_server
        status, body = _post_raw(base, "/api/templates/import?filename=x.oftemplate", b"{not json")
        assert status == 400
        assert "valid template file" in json.loads(body)["error"].lower()

    def test_import_non_object_400(self, live_server) -> None:
        _, base, _ = live_server
        status, body = _post_raw(base, "/api/templates/import?filename=x.oftemplate", b"[1, 2, 3]")
        assert status == 400
        assert "JSON object" in json.loads(body)["error"]

    def test_import_deeply_nested_json_400_not_500(self, live_server) -> None:
        # Deeply-nested JSON (well under the size cap) exhausts the decoder's
        # recursion; it must be rejected as bad input (400), not crash to 500.
        _, base, _ = live_server
        bomb = ("[" * 100_000 + "]" * 100_000).encode()
        status, body = _post_raw(base, "/api/templates/import?filename=x.oftemplate", bomb)
        assert status == 400
        assert "nesting too deep" in json.loads(body)["error"].lower()

    def test_import_utf8_bom_accepted(self, live_server) -> None:
        # A BOM-prefixed (EF BB BF) but otherwise valid template imports
        # cleanly instead of being rejected over the invisible prefix.
        _, base, _ = live_server
        body = b"\xef\xbb\xbf" + _osc_envelope_bytes(name="Bommed")
        status, resp = _post_raw(base, "/api/templates/import?filename=x.oftemplate", body)
        assert status == 200, resp
        assert json.loads(resp)["ok"] is True

    def test_import_bad_payload_400(self, live_server) -> None:
        _, base, _ = live_server
        bad = json.dumps({"version": 1, "type": "osc_output", "name": "Bad", "payload": {"args": []}}).encode()
        status, body = _post_raw(base, "/api/templates/import?filename=x.oftemplate", bad)
        assert status == 400
        assert "address" in json.loads(body)["error"]

    def test_import_newer_format_version_400(self, live_server) -> None:
        _, base, _ = live_server
        status, body = _post_raw(
            base,
            "/api/templates/import?filename=x.oftemplate",
            _osc_envelope_bytes(version=TEMPLATE_VERSION + 1),
        )
        assert status == 400
        assert "unsupported version" in json.loads(body)["error"]

    def test_import_payload_error_includes_version_skew(self, live_server) -> None:
        _, base, _ = live_server
        # Valid format version, but a trigger kind this build doesn't know,
        # authored by a far-newer OpenFollow → the rejection names the skew.
        payload = {"address": "/a", "args": [], "trigger": {"kind": "warp_drive"}}
        body_bytes = _osc_envelope_bytes(name="Future", app_version="999.0.0", payload=payload)
        status, body = _post_raw(base, "/api/templates/import?filename=x.oftemplate", body_bytes)
        assert status == 400
        err = json.loads(body)["error"]
        assert "trigger.kind" in err
        assert "999.0.0" in err
        assert "update OpenFollow" in err

    def test_import_error_unparseable_app_version_no_skew(self, live_server) -> None:
        # A non-PEP440 app_version can't be compared, so no skew note is
        # appended – the raw validation error stands on its own.
        _, base, _ = live_server
        body_bytes = _osc_envelope_bytes(name="Junk", app_version="not-a-version", payload={"args": []})
        status, body = _post_raw(base, "/api/templates/import?filename=x.oftemplate", body_bytes)
        assert status == 400
        err = json.loads(body)["error"]
        assert "address" in err
        assert "created by OpenFollow" not in err

    def test_import_error_older_app_version_no_skew(self, live_server) -> None:
        # An older (valid) app_version is not a forward-incompatibility, so the
        # rejection is left unannotated.
        _, base, _ = live_server
        body_bytes = _osc_envelope_bytes(name="Old", app_version="0.0.1", payload={"args": []})
        status, body = _post_raw(base, "/api/templates/import?filename=x.oftemplate", body_bytes)
        assert status == 400
        err = json.loads(body)["error"]
        assert "address" in err
        assert "created by OpenFollow" not in err

    def test_import_oversize_413(self, live_server, monkeypatch) -> None:
        import openfollow.web.routes as routes_mod

        # Shrink the cap so a tiny over-limit body triggers the refusal. The
        # route rejects on Content-Length before reading, so a *large* body
        # would break the client's in-flight send (the server never drains
        # it); a few dozen bytes fit the socket buffer and send cleanly.
        monkeypatch.setattr(routes_mod, "_MAX_TEMPLATE_UPLOAD_BYTES", 16)
        _, base, _ = live_server
        status, body = _post_raw(base, "/api/templates/import?filename=x.oftemplate", b"x" * 64)
        assert status == 413
        assert "too large" in json.loads(body)["error"].lower()

    def test_import_bad_extension_400(self, live_server) -> None:
        _, base, _ = live_server
        status, body = _post_raw(
            base,
            "/api/templates/import?filename=evil.txt",
            _osc_envelope_bytes(),
        )
        assert status == 400
        assert "Unsupported file type" in json.loads(body)["error"]

    def test_import_empty_400(self, live_server) -> None:
        _, base, _ = live_server
        status, body = _post_raw(base, "/api/templates/import?filename=x.oftemplate", b"")
        assert status == 400
        assert "Empty" in json.loads(body)["error"]

    def test_import_legacy_extension_accepted(self, live_server) -> None:
        _, base, _ = live_server
        # An export from an older build (legacy name) imports and is rewritten
        # under the canonical suffix.
        status, body = _post_raw(
            base,
            "/api/templates/import?filename=legacy.openfollowtemplate",
            _osc_envelope_bytes(name="From Legacy"),
        )
        assert status == 200, body
        assert json.loads(body)["filename"].endswith(".oftemplate")

    def test_import_preserves_app_version(self, live_server) -> None:
        _, base, cfg_path = live_server
        status, body = _post_raw(
            base,
            "/api/templates/import?filename=x.oftemplate",
            _osc_envelope_bytes(name="Provenance", app_version="0.0.7"),
        )
        assert status == 200, body
        filename = json.loads(body)["filename"]
        on_disk = json.loads((_templates_root(cfg_path) / "user" / filename).read_text())
        assert on_disk["app_version"] == "0.0.7"

    def test_round_trip_export_delete_reimport(self, live_server) -> None:
        """The acceptance criterion: export → delete → import → listed again."""
        _, base, _ = live_server
        status, body = _post_json(
            base,
            "/api/templates/osc_output/save",
            {"name": "Round Trip", "payload": {"address": "/rt/[markerid]", "args": ["[x]"]}},
        )
        assert status == 200, body
        filename = json.loads(body)["filename"]

        # Export the bytes.
        status, raw, _ = _get_raw(base, f"/api/templates/{filename}/export")
        assert status == 200

        # Delete it from disk.
        status, _ = _delete(base, f"/api/templates/{filename}")
        assert status == 200
        status, body = _get(base, "/api/templates?type=osc_output")
        assert "Round Trip" not in [t["name"] for t in json.loads(body)["templates"]]

        # Re-import the exact exported bytes.
        status, body = _post_raw(base, "/api/templates/import?filename=rt.oftemplate", raw)
        assert status == 200, body
        status, body = _get(base, "/api/templates?type=osc_output")
        assert "Round Trip" in [t["name"] for t in json.loads(body)["templates"]]
