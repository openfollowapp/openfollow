# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Route + parser coverage for the OSC bindings UI.

Routes are integration-tested against a live :class:`ConfigWebServer`.
The form parsers (``_parse_osc_message`` / ``_parse_trigger_subtable``
/ ``_apply_osc_binding_fields``) are unit-tested directly so each
branch is reachable without a live HTTP round-trip.
"""

from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

import openfollow.web.discovery as discovery_module
from openfollow.configuration import (
    AppConfig,
    ControllerButtonTrigger,
    FaderOnChangeTrigger,
    HotkeyTrigger,
    MidiConfig,
    MidiMessageTrigger,
    MidiPatch,
    OscDestinationConfig,
    OscTransmitterConfig,
    StreamTrigger,
    VirtualFaderConfig,
    VirtualFadersConfig,
    load_config,
    save_config,
)
from openfollow.web.routes import (
    _apply_osc_binding_fields,
    _effective_default_marker_id,
    _midi_patches_for_form,
    _osc_binding_marker_display,
    _parse_osc_message,
    _parse_trigger_subtable,
    _row_unresolved_placeholders,
    _virtual_fader_names_for_form,
)
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
    # Seed a controlled marker; id 0 is reserved as "ignored" project-wide.
    config_path.write_text("controlled_marker_ids = [1]\n", encoding="utf-8")
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


def _live_server_with_providers(tmp_path, monkeypatch, **providers):
    """Spin up a server with custom diagnostics providers.

    Returns ``(server, base_url, config_path)``.
    """
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
        **providers,
    )
    server.start()
    assert _wait_for_port(port)
    return server, f"http://127.0.0.1:{port}", str(config_path)


def _get(base: str, path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:  # pragma: no cover – exercised by 404 tests
        return e.code, e.read().decode()
    except Exception as e:  # pragma: no cover – fallback
        return 0, str(e)


def _post_form(base: str, path: str, data: dict) -> tuple[int, str]:
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


# ---------------------------------------------------------------------------
# Section render
# ---------------------------------------------------------------------------


def test_section_renders_empty_state(live_server) -> None:
    _, base, _ = live_server
    status, body = _get(base, "/section/osc_bindings")
    assert status == 200
    assert "OSC Transmitters" in body
    assert "No transmitters configured" in body


def test_section_renders_existing_rows(live_server) -> None:
    _, base, cfg_path = live_server
    cfg = load_config(cfg_path)
    cfg.osc_destinations.destinations.append(
        OscDestinationConfig(id="d1", name="Console", host="10.0.0.1", port=8001),
    )
    cfg.osc_transmitters.transmitters.append(
        OscTransmitterConfig(name="Stage 1", destination_id="d1"),
    )
    save_config(cfg, cfg_path)
    status, body = _get(base, "/section/osc_bindings")
    assert status == 200
    assert "Stage 1" in body
    # The destination's address rides inside the <option> label, not below it.
    assert "<option" in body and "Console – udp://10.0.0.1:8001</option>" in body


def test_section_focus_query_reopens_row(live_server) -> None:
    """``GET /section/osc_bindings?focus=<id>`` re-renders the section with
    the named row expanded."""
    _, base, cfg_path = live_server
    cfg = load_config(cfg_path)
    cfg.osc_transmitters.transmitters.append(
        OscTransmitterConfig(name="A", destination_id="d1"),
    )
    cfg.osc_transmitters.transmitters.append(
        OscTransmitterConfig(name="B", destination_id="d2"),
    )
    save_config(cfg, cfg_path)
    target_id = cfg.osc_transmitters.transmitters[1].id
    other_id = cfg.osc_transmitters.transmitters[0].id
    status, body = _get(base, f"/section/osc_bindings?focus={target_id}")
    assert status == 200
    # Focused row's ``<details>`` carries ``open``; the sibling doesn't.
    target_marker = f'data-row-id="{target_id}"'
    target_idx = body.find(target_marker)
    assert target_idx != -1
    target_tag_end = body.find(">", target_idx)
    assert " open" in body[target_idx:target_tag_end]
    other_marker = f'data-row-id="{other_id}"'
    other_idx = body.find(other_marker)
    assert other_idx != -1
    other_tag_end = body.find(">", other_idx)
    assert " open" not in body[other_idx:other_tag_end]


def test_section_renders_per_row_discard_button(live_server) -> None:
    """Each row exposes a Discard button that GETs the section back so
    unsaved edits are dropped. Disabled by default – the JS dirty gate
    enables it once the operator changes any field. ``hx-target`` +
    ``hx-select`` scope the swap to this row only so unsaved edits in
    other open rows aren't wiped."""
    _, base, cfg_path = live_server
    cfg = load_config(cfg_path)
    cfg.osc_transmitters.transmitters.append(
        OscTransmitterConfig(name="Stage 1", destination_id="d1"),
    )
    save_config(cfg, cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    status, body = _get(base, "/section/osc_bindings")
    assert status == 200
    assert "data-discard-btn" in body
    assert f"/section/osc_bindings?focus={row_id}" in body
    assert "hx-confirm" in body
    # Target + select must reference the same row's <details> so other
    # open rows stay untouched in the DOM.
    row_selector = f'details.osc-binding-row[data-row-id="{row_id}"]'
    assert f"hx-target='{row_selector}'" in body
    assert f"hx-select='{row_selector}'" in body


# ---------------------------------------------------------------------------
# CRUD round-trips
# ---------------------------------------------------------------------------


def test_add_creates_row(live_server) -> None:
    _, base, cfg_path = live_server
    status, body = _post_form(base, "/section/osc_bindings/add", {})
    assert status == 200
    cfg = load_config(cfg_path)
    assert len(cfg.osc_transmitters.transmitters) == 1
    row = cfg.osc_transmitters.transmitters[0]
    assert row.name == "New transmitter"
    assert row.enabled is False
    # ``focus_id`` opens the row in the rendered partial.
    assert f'data-row-id="{row.id}"' in body
    assert "open" in body


def test_save_updates_basics(live_server) -> None:
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    status, _ = _post_form(
        base,
        f"/section/osc_binding/{row_id}",
        {
            "enabled": "on",
            "name": "Edited",
            "destination_id": "dest-7",
            "markers": "2",
            "trigger.type": "stream",
            "trigger.rate_hz": "60",
        },
    )
    assert status == 200
    cfg = load_config(cfg_path)
    row = cfg.osc_transmitters.transmitters[0]
    assert row.enabled is True
    assert row.name == "Edited"
    assert row.destination_id == "dest-7"
    assert row.markers == ["2"]
    assert isinstance(row.trigger, StreamTrigger)
    assert row.trigger.rate_hz == 60


def test_save_round_trips_non_ascii_form_bytes(live_server) -> None:
    """Bottle 0.13.4 hardcodes its URL-form decoder to Latin-1, which
    corrupts UTF-8 form bodies. Without the ``_bottle_charset_fix`` shim a
    non-breaking space (U+00A0) round-trips as runaway U+00C3/U+00C2
    mojibake on each save -> render -> re-save.

    The NBSP is placed inside the quoted arg on purpose: an unquoted NBSP
    is a token separator (the ``contenteditable`` editor inserts U+00A0
    around placeholder pills, so it must split, not glue), and quoting is
    the one place it survives verbatim -- the byte-level UTF-8 round-trip
    this test guards."""
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    nbsp = "\xa0"
    msg = f'/cmd "FaderMaster{nbsp}Executor 201 At 50 Fade 2" [ix:99]'
    status, _ = _post_form(
        base,
        f"/section/osc_binding/{row_id}",
        {
            "name": "T",
            "host": "1.2.3.4",
            "port": "8000",
            "protocol": "udp",
            "markers": "1",
            "trigger.type": "stream",
            "trigger.rate_hz": "30",
            "osc_message": msg,
        },
    )
    assert status == 200
    cfg = load_config(cfg_path)
    row = cfg.osc_transmitters.transmitters[0]
    # Args must not carry the U+00C2/U+00C3 bytes a Latin-1 form-decode
    # introduces -- the bytes round-trip as UTF-8.
    assert row.args, "expected at least one arg after save"
    for arg in row.args:
        assert "Â" not in arg, f"unexpected U+00C2 in arg {arg!r} -- Latin-1 form-decode regressed"
        assert "Ã" not in arg, f"unexpected U+00C3 in arg {arg!r} -- Latin-1 form-decode regressed"
    # NBSP round-trips as U+00A0 -- proof the form decode treated the
    # bytes as UTF-8, not Latin-1.
    assert any("\xa0" in arg for arg in row.args), f"expected U+00A0 NBSP to round-trip, got {row.args!r}"


def test_save_partial_form_keeps_unsent_fields(live_server) -> None:
    """A partial form (e.g. just toggling enabled off) must not blank
    out fields the form didn't carry. Covers the branch in
    ``_apply_osc_binding_fields`` that only updates fields present in
    the data dict."""
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    _post_form(
        base,
        f"/section/osc_binding/{row_id}",
        {
            "enabled": "on",
            "name": "Stage",
            "destination_id": "dest-3",
            "markers": "1",
            "trigger.type": "stream",
            "trigger.rate_hz": "30",
        },
    )
    # Post with no fields – enabled missing → False, the rest stay.
    _post_form(base, f"/section/osc_binding/{row_id}", {})
    cfg = load_config(cfg_path)
    row = cfg.osc_transmitters.transmitters[0]
    assert row.enabled is False
    assert row.name == "Stage"
    assert row.destination_id == "dest-3"


def test_save_404_for_unknown_id(live_server) -> None:
    _, base, _ = live_server
    status, _ = _post_form(base, "/section/osc_binding/does-not-exist", {})
    assert status == 404


def test_save_with_hotkey_trigger_persists_modifiers(live_server) -> None:
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    # Multi-value modifiers must survive Bottle's MultiDict.getall path.
    _post_form(
        base,
        f"/section/osc_binding/{row_id}",
        {
            "trigger.type": "hotkey",
            "trigger.key": "r",
            "trigger.modifiers": ["ctrl", "shift"],
            "trigger.edge": "press",
        },
    )
    cfg = load_config(cfg_path)
    row = cfg.osc_transmitters.transmitters[0]
    assert isinstance(row.trigger, HotkeyTrigger)
    assert row.trigger.key == "r"
    assert set(row.trigger.modifiers) == {"ctrl", "shift"}
    assert row.trigger.edge == "press"


def test_save_with_controller_button_trigger(live_server) -> None:
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    _post_form(
        base,
        f"/section/osc_binding/{row_id}",
        {
            "trigger.type": "controller_button",
            "trigger.button": "A",
            "trigger.edge": "release",
        },
    )
    cfg = load_config(cfg_path)
    row = cfg.osc_transmitters.transmitters[0]
    assert isinstance(row.trigger, ControllerButtonTrigger)
    assert row.trigger.button == "A"
    assert row.trigger.edge == "release"


def test_save_with_marker_fader_on_change_trigger(live_server) -> None:
    # The Fader-on-Change dropdown posts a single prefixed
    # ``trigger.fader_source`` value; ``marker:N`` selects a marker fader.
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    _post_form(
        base,
        f"/section/osc_binding/{row_id}",
        {
            "name": "Spot",
            "host": "10.0.0.1",
            "port": "9000",
            "protocol": "udp",
            "markers": "1",
            "osc_message": "/spot/[markerfader]",
            "trigger.type": "fader_on_change",
            "trigger.fader_source": "marker:1",
            "trigger.rate_hz": "30",
        },
    )
    row = load_config(cfg_path).osc_transmitters.transmitters[0]
    assert isinstance(row.trigger, FaderOnChangeTrigger)
    assert row.trigger.marker_id == 1
    assert row.trigger.fader == 1  # default/ignored for a marker source
    assert row.trigger.rate_hz == 30


def test_save_with_indexed_fader_on_change_trigger(live_server) -> None:
    # ``index:N`` selects the indexed source; marker_id stays 0.
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    _post_form(
        base,
        f"/section/osc_binding/{row_id}",
        {
            "name": "Lvl",
            "host": "10.0.0.1",
            "port": "9000",
            "protocol": "udp",
            "markers": "1",
            "osc_message": "/lvl/[fader:3]",
            "trigger.type": "fader_on_change",
            "trigger.fader_source": "index:3",
            "trigger.rate_hz": "30",
        },
    )
    row = load_config(cfg_path).osc_transmitters.transmitters[0]
    assert isinstance(row.trigger, FaderOnChangeTrigger)
    assert row.trigger.fader == 3
    assert row.trigger.marker_id == 0


def test_trigger_form_lists_and_selects_marker_fader(
    tmp_path,
    monkeypatch,
) -> None:
    # The Fader-on-Change dropdown lists controlled-marker faders (from
    # the live snapshot) in their own optgroup beside the indexed faders,
    # and pre-selects the row's stored marker source.
    server, base, cfg_path = _live_server_with_providers(
        tmp_path,
        monkeypatch,
        marker_fader_values_provider=lambda: [
            {"marker_id": 1, "name": "Diva", "value": 0.0},
        ],
    )
    try:
        _post_form(base, "/section/osc_bindings/add", {})
        row_id = load_config(cfg_path).osc_transmitters.transmitters[0].id
        _post_form(
            base,
            f"/section/osc_binding/{row_id}",
            {
                "name": "Spot",
                "host": "10.0.0.1",
                "port": "9000",
                "protocol": "udp",
                "markers": "1",
                "osc_message": "/spot/[markerfader]",
                "trigger.type": "fader_on_change",
                "trigger.fader_source": "marker:1",
                "trigger.rate_hz": "30",
            },
        )
        status, body = _get(
            base,
            f"/section/osc_binding/{row_id}/trigger_form?trigger.type=fader_on_change",
        )
        assert status == 200
        assert 'name="trigger.fader_source"' in body
        assert 'value="index:1"' in body
        assert "Marker faders" in body
        # Marker option uses the live label and is pre-selected.
        assert 'value="marker:1" selected' in body
        assert "Diva" in body
    finally:
        server.stop()


def test_duplicate_clones_row(live_server) -> None:
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    status, body = _post_form(
        base,
        f"/section/osc_binding/{row_id}/duplicate",
        {},
    )
    assert status == 200
    cfg = load_config(cfg_path)
    assert len(cfg.osc_transmitters.transmitters) == 2
    assert cfg.osc_transmitters.transmitters[1].id != row_id
    assert cfg.osc_transmitters.transmitters[1].name == "New transmitter (copy)"


def test_duplicate_404_for_unknown_id(live_server) -> None:
    _, base, _ = live_server
    status, _ = _post_form(base, "/section/osc_binding/no-such/duplicate", {})
    assert status == 404


def test_delete_removes_row(live_server) -> None:
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    status, _ = _post_form(base, f"/section/osc_binding/{row_id}/delete", {})
    assert status == 200
    cfg = load_config(cfg_path)
    assert cfg.osc_transmitters.transmitters == []


def test_delete_unknown_id_is_noop(live_server) -> None:
    _, base, _ = live_server
    status, body = _post_form(
        base,
        "/section/osc_binding/no-such/delete",
        {},
    )
    assert status == 200
    assert "OSC Transmitters" in body  # partial still renders cleanly


def test_move_up_swaps_with_predecessor(live_server) -> None:
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    first_id = cfg.osc_transmitters.transmitters[0].id
    second_id = cfg.osc_transmitters.transmitters[1].id
    _post_form(base, f"/section/osc_binding/{second_id}/move", {"direction": "up"})
    cfg = load_config(cfg_path)
    assert cfg.osc_transmitters.transmitters[0].id == second_id
    assert cfg.osc_transmitters.transmitters[1].id == first_id


def test_move_down_swaps_with_successor(live_server) -> None:
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    first_id = cfg.osc_transmitters.transmitters[0].id
    second_id = cfg.osc_transmitters.transmitters[1].id
    _post_form(base, f"/section/osc_binding/{first_id}/move", {"direction": "down"})
    cfg = load_config(cfg_path)
    assert cfg.osc_transmitters.transmitters[0].id == second_id
    assert cfg.osc_transmitters.transmitters[1].id == first_id


def test_move_top_up_is_noop(live_server) -> None:
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    only_id = cfg.osc_transmitters.transmitters[0].id
    _post_form(base, f"/section/osc_binding/{only_id}/move", {"direction": "up"})
    cfg = load_config(cfg_path)
    assert cfg.osc_transmitters.transmitters[0].id == only_id


def test_move_bottom_down_is_noop(live_server) -> None:
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    only_id = cfg.osc_transmitters.transmitters[0].id
    _post_form(base, f"/section/osc_binding/{only_id}/move", {"direction": "down"})
    cfg = load_config(cfg_path)
    assert cfg.osc_transmitters.transmitters[0].id == only_id


def test_move_with_unknown_direction_is_noop(live_server) -> None:
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    first_id = cfg.osc_transmitters.transmitters[0].id
    _post_form(base, f"/section/osc_binding/{first_id}/move", {"direction": "sideways"})
    cfg = load_config(cfg_path)
    assert cfg.osc_transmitters.transmitters[0].id == first_id


def test_move_404_for_unknown_id(live_server) -> None:
    _, base, _ = live_server
    status, _ = _post_form(
        base,
        "/section/osc_binding/no-such/move",
        {"direction": "up"},
    )
    assert status == 404


# ---------------------------------------------------------------------------
# Bulk reorder: the drag-handle UI POSTs the complete row ordering as a
# comma-separated id list.
# ---------------------------------------------------------------------------


def _add_three_rows(base: str, cfg_path: str) -> tuple[str, str, str]:
    for _ in range(3):
        _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    return (
        cfg.osc_transmitters.transmitters[0].id,
        cfg.osc_transmitters.transmitters[1].id,
        cfg.osc_transmitters.transmitters[2].id,
    )


def test_reorder_applies_full_ordering(live_server) -> None:
    """Three-row reorder: ``[A, B, C]`` → ``[C, A, B]``."""
    _, base, cfg_path = live_server
    a, b, c = _add_three_rows(base, cfg_path)
    status, body = _post_form(
        base,
        "/section/osc_bindings/reorder",
        {"order": f"{c},{a},{b}"},
    )
    assert status == 200
    cfg = load_config(cfg_path)
    assert [r.id for r in cfg.osc_transmitters.transmitters] == [c, a, b]


def test_reorder_drops_unknown_ids_keeps_missing_rows(live_server) -> None:
    """Stale tab posts a list with a phantom id and omits one real row.
    The phantom is dropped; the omitted row gets appended to the end so
    no data is lost."""
    _, base, cfg_path = live_server
    a, b, c = _add_three_rows(base, cfg_path)
    status, _ = _post_form(
        base,
        "/section/osc_bindings/reorder",
        {"order": f"{c},ghost-id,{a}"},
    )
    assert status == 200
    cfg = load_config(cfg_path)
    # ``c, a`` are honoured in order; ``b`` was missing so it's
    # appended; ``ghost-id`` is dropped.
    assert [r.id for r in cfg.osc_transmitters.transmitters] == [c, a, b]


def test_reorder_empty_order_is_noop(live_server) -> None:
    """Empty / whitespace-only ``order`` field – no-op (don't lose
    rows to a buggy drag interaction)."""
    _, base, cfg_path = live_server
    a, b, c = _add_three_rows(base, cfg_path)
    status, body = _post_form(base, "/section/osc_bindings/reorder", {"order": "  "})
    assert status == 200
    cfg = load_config(cfg_path)
    assert [r.id for r in cfg.osc_transmitters.transmitters] == [a, b, c]


def test_reorder_with_no_change_is_idempotent(live_server) -> None:
    """Posting the existing order is a no-op (no save_config call,
    same observable result)."""
    _, base, cfg_path = live_server
    a, b, c = _add_three_rows(base, cfg_path)
    status, body = _post_form(
        base,
        "/section/osc_bindings/reorder",
        {"order": f"{a},{b},{c}"},
    )
    assert status == 200
    cfg = load_config(cfg_path)
    assert [r.id for r in cfg.osc_transmitters.transmitters] == [a, b, c]


def test_reorder_dedups_repeated_ids(live_server) -> None:
    """A buggy client that sends the same id twice gets the second
    occurrence ignored – the row appears exactly once in its first
    listed position."""
    _, base, cfg_path = live_server
    a, b, c = _add_three_rows(base, cfg_path)
    status, _ = _post_form(
        base,
        "/section/osc_bindings/reorder",
        {"order": f"{a},{a},{c},{b}"},
    )
    assert status == 200
    cfg = load_config(cfg_path)
    assert [r.id for r in cfg.osc_transmitters.transmitters] == [a, c, b]


# ---------------------------------------------------------------------------
# Combined ``osc_message`` field: address + args travel as one
# whitespace-delimited string.
# ---------------------------------------------------------------------------


def test_save_with_osc_message_splits_address_and_args(live_server) -> None:
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    _post_form(
        base,
        f"/section/osc_binding/{row_id}",
        {
            "osc_message": "/eos/[markerid]/pos [x] [y] [z]",
        },
    )
    cfg = load_config(cfg_path)
    row = cfg.osc_transmitters.transmitters[0]
    assert row.address == "/eos/[markerid]/pos"
    assert row.args == ["[x]", "[y]", "[z]"]


def test_save_with_empty_osc_message_clears_address_and_args(live_server) -> None:
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row = cfg.osc_transmitters.transmitters[0]
    row.address = "/seed"
    row.args = ["[x]"]
    save_config(cfg, cfg_path)
    _post_form(base, f"/section/osc_binding/{row.id}", {"osc_message": ""})
    cfg = load_config(cfg_path)
    row = cfg.osc_transmitters.transmitters[0]
    assert row.address == ""
    assert row.args == []


def test_save_without_osc_message_field_keeps_existing(live_server) -> None:
    """A partial save (e.g. just toggling enabled) must not blank the
    operator's tuned message – covered by the ``None`` arm of
    ``_parse_osc_message``."""
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row = cfg.osc_transmitters.transmitters[0]
    row.address = "/seed"
    row.args = ["[x]", "[y]"]
    save_config(cfg, cfg_path)
    _post_form(base, f"/section/osc_binding/{row.id}", {})
    cfg = load_config(cfg_path)
    row = cfg.osc_transmitters.transmitters[0]
    assert row.address == "/seed"
    assert row.args == ["[x]", "[y]"]


def test_save_collapses_whitespace_runs_in_osc_message(live_server) -> None:
    """Multiple spaces between tokens collapse to one – pasted /
    multi-line input doesn't leave empty arg slots that the renderer
    would emit as literal empty strings."""
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    _post_form(
        base,
        f"/section/osc_binding/{row_id}",
        {
            "osc_message": "  /a/b   [x]    [y]   ",
        },
    )
    cfg = load_config(cfg_path)
    row = cfg.osc_transmitters.transmitters[0]
    assert row.address == "/a/b"
    assert row.args == ["[x]", "[y]"]


# ---------------------------------------------------------------------------
# Optional default marker + Enabled gating + red pills
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_row_unresolved_placeholders_collapses_across_address_and_args() -> None:
    """The row-level helper aggregates unresolved tokens from the
    address + every arg, with duplicates collapsed across the whole
    row. The pill JS treats them as one dependency per token."""
    row = OscTransmitterConfig(
        markers=[],
        address="/eos/[markerid]/go",
        args=["[x]", "[x]", "[y:7]"],
    )
    out = _row_unresolved_placeholders(row, frozenset({0, 1}))
    assert out == ("[markerid]", "[x]", "[y:7]")


@pytest.mark.unit
def test_row_unresolved_placeholders_empty_when_all_resolve() -> None:
    row = OscTransmitterConfig(
        markers=["1"],
        address="/eos/[markerid]/go",
        args=["[x:1]"],
    )
    out = _row_unresolved_placeholders(row, frozenset({0, 1}))
    assert out == ()


@pytest.mark.unit
def test_row_unresolved_placeholders_empty_for_literal_only_row() -> None:
    """Literal-only rows have no dependencies – they fire even when
    no marker is registered, and the UX must not flag them as
    unresolved either."""
    row = OscTransmitterConfig(
        markers=[],
        address="/cue/go",
        args=["My Cue"],
    )
    out = _row_unresolved_placeholders(row, frozenset())
    assert out == ()


class _FakeCatalog:
    """Minimal marker-name lookup for the display-helper unit tests."""

    def __init__(self, names: dict[int, str]) -> None:
        self._names = names

    def get(self, marker_id: int):  # noqa: ANN201 – duck-typed entry
        name = self._names.get(marker_id)
        if name is None:
            return None
        return type("_Entry", (), {"name": name})()


@pytest.mark.unit
def test_effective_default_marker_id_resolution() -> None:
    """The unresolved-pill heuristic: a usable default marker exists when a
    numeric id is controlled, or when a dynamic token (``all`` / ``cN``)
    is named and the station controls at least one marker."""
    # Numeric token in the registry → that id.
    assert _effective_default_marker_id(["7"], frozenset({7})) == 7
    # Numeric token not in the registry → no usable default.
    assert _effective_default_marker_id(["7"], frozenset({1})) is None
    # ``all`` / ``cN`` are dynamic → lowest registered id stands in.
    assert _effective_default_marker_id(["all"], frozenset({5, 2})) == 2
    assert _effective_default_marker_id(["c1"], frozenset({3})) == 3
    # Dynamic token but nothing controlled → no usable default.
    assert _effective_default_marker_id(["all"], frozenset()) is None
    # No markers named → no usable default.
    assert _effective_default_marker_id([], frozenset({1})) is None


@pytest.mark.unit
def test_marker_display_multi_marker_all_nested() -> None:
    """A row with >1 markers nests every marker (primary included) as a chip
    – no header badge – so they read uniformly."""
    from openfollow.configuration import OscTransmittersConfig

    cfg = AppConfig(
        controlled_marker_ids=[1, 3, 7],
        osc_transmitters=OscTransmittersConfig(
            transmitters=[OscTransmitterConfig(id="r1", markers=["1", "3", "7"])],
        ),
    )
    catalog = _FakeCatalog({1: "Marker 1", 3: "Sänger", 7: "Gitarre"})
    display = _osc_binding_marker_display(cfg, catalog)
    assert display["r1"]["header"] is None
    assert display["r1"]["nested"] == [
        {"label": "Marker 1 (1)", "controlled": True},
        {"label": "Sänger (3)", "controlled": True},
        {"label": "Gitarre (7)", "controlled": True},
    ]
    assert display["r1"]["markers_unusable"] is False


@pytest.mark.unit
def test_marker_display_single_marker_uses_header() -> None:
    """A single marker shows in the header badge with no nested list; an
    uncontrolled single marker sets ``markers_unusable`` True (red dot)."""
    from openfollow.configuration import OscTransmittersConfig

    cfg = AppConfig(
        controlled_marker_ids=[1],
        osc_transmitters=OscTransmittersConfig(
            transmitters=[
                OscTransmitterConfig(id="ok", markers=["1"]),
                OscTransmitterConfig(id="bad", markers=["5"]),
            ],
        ),
    )
    display = _osc_binding_marker_display(cfg, _FakeCatalog({1: "Lead"}))
    assert display["ok"] == {"header": "Lead (1)", "nested": [], "markers_unusable": False}
    assert display["bad"] == {"header": "Marker 5 (5)", "nested": [], "markers_unusable": True}


@pytest.mark.unit
def test_marker_display_multi_marker_flags_uncontrolled_chip() -> None:
    """In a multi-marker row each chip carries its own controlled flag; at least one is
    controlled so ``markers_unusable`` is False (yellow dot)."""
    from openfollow.configuration import OscTransmittersConfig

    cfg = AppConfig(
        controlled_marker_ids=[1],
        osc_transmitters=OscTransmittersConfig(
            transmitters=[OscTransmitterConfig(id="r1", markers=["1", "5"])],
        ),
    )
    display = _osc_binding_marker_display(cfg, _FakeCatalog({}))
    assert display["r1"]["header"] is None
    assert display["r1"]["nested"] == [
        {"label": "Marker 1 (1)", "controlled": True},
        {"label": "Marker 5 (5)", "controlled": False},
    ]
    assert display["r1"]["markers_unusable"] is False


@pytest.mark.unit
def test_marker_display_all_token_nests_controlled() -> None:
    from openfollow.configuration import OscTransmittersConfig

    cfg = AppConfig(
        controlled_marker_ids=[5, 2],
        osc_transmitters=OscTransmittersConfig(
            transmitters=[OscTransmitterConfig(id="r1", markers=["all"])],
        ),
    )
    catalog = _FakeCatalog({2: "Diva"})
    display = _osc_binding_marker_display(cfg, catalog)
    assert display["r1"]["header"] is None
    # Controlled markers, id-sorted; missing catalog name falls back to "Marker N".
    assert display["r1"]["nested"] == [
        {"label": "Diva (2)", "controlled": True},
        {"label": "Marker 5 (5)", "controlled": True},
    ]
    assert display["r1"]["markers_unusable"] is False


@pytest.mark.unit
def test_marker_display_controller_alias_and_empty() -> None:
    from openfollow.configuration import OscTransmittersConfig

    cfg = AppConfig(
        controlled_marker_ids=[1],
        osc_transmitters=OscTransmittersConfig(
            transmitters=[
                OscTransmitterConfig(id="alias", markers=["1", "c2"]),
                OscTransmitterConfig(id="empty", markers=[]),
            ],
        ),
    )
    catalog = _FakeCatalog({1: "Lead"})
    display = _osc_binding_marker_display(cfg, catalog)
    # >1 markers → both nested (alias always resolves to a controlled marker).
    assert display["alias"]["header"] is None
    assert display["alias"]["nested"] == [
        {"label": "Lead (1)", "controlled": True},
        {"label": "Controller c2", "controlled": True},
    ]
    assert display["alias"]["markers_unusable"] is False
    # No markers → nothing, dot not reddened by markers.
    assert display["empty"] == {"header": None, "nested": [], "markers_unusable": False}


def test_save_with_empty_marker_id_persists_as_none(live_server) -> None:
    """An operator who clears the ``Default marker`` input posts an
    empty string. The ``_as_optional_int`` parser collapses empty →
    ``None`` so the field reflects "no default marker chosen"."""
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    _post_form(
        base,
        f"/section/osc_binding/{row_id}",
        {
            "markers": "",
        },
    )
    cfg = load_config(cfg_path)
    assert cfg.osc_transmitters.transmitters[0].markers == []


def test_save_with_explicit_marker_id_persists_as_int(
    live_server,
) -> None:
    """An operator who deliberately picks a marker id must round-trip
    as that ``int`` – distinct from "unset" (``None``). Marker id 0
    is reserved as "ignored" on the PSN wire, so this test uses ``1``."""
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    _post_form(
        base,
        f"/section/osc_binding/{row_id}",
        {
            "markers": "1",
        },
    )
    cfg = load_config(cfg_path)
    assert cfg.osc_transmitters.transmitters[0].markers == ["1"]


def test_save_coerces_enabled_false_when_default_marker_unresolved(
    live_server,
) -> None:
    """A row that uses ``[x]`` but has no default marker configured
    must POST as ``enabled=False`` even when the form ticks it on. The
    Save isn't blocked – the operator can fix and resave – but the
    runtime won't fire packets that would skip for "no default marker
    configured" anyway."""
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    _post_form(
        base,
        f"/section/osc_binding/{row_id}",
        {
            "enabled": "on",
            "markers": "",
            "osc_message": "/eos/1/go [x]",
        },
    )
    cfg = load_config(cfg_path)
    row = cfg.osc_transmitters.transmitters[0]
    assert row.markers == []
    assert row.enabled is False
    assert row.address == "/eos/1/go"
    assert row.args == ["[x]"]


def test_save_coerces_enabled_false_for_unregistered_explicit_marker(
    live_server,
) -> None:
    """Explicit ``[x:markerN]`` for a marker not in
    ``controlled_marker_ids`` is also unresolved – the row coerces
    to disabled even when the operator's default marker is fine."""
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    # The live_server fixture seeds ``controlled_marker_ids=[1]`` only.
    row_id = cfg.osc_transmitters.transmitters[0].id
    _post_form(
        base,
        f"/section/osc_binding/{row_id}",
        {
            "enabled": "on",
            "markers": "1",  # default is registered, so [x] alone would be fine
            "osc_message": "/multi [x:9]",  # marker 9 not registered
        },
    )
    cfg = load_config(cfg_path)
    row = cfg.osc_transmitters.transmitters[0]
    assert row.markers == ["1"]
    assert row.enabled is False


def test_save_keeps_enabled_true_when_all_placeholders_resolve(
    live_server,
) -> None:
    """Sanity check on the gate: a row whose every placeholder
    dependency is satisfied keeps ``enabled=True``."""
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    _post_form(
        base,
        f"/section/osc_binding/{row_id}",
        {
            "enabled": "on",
            # The live_server fixture seeds marker 1 in ``controlled_marker_ids``.
            "markers": "1",
            "osc_message": "/eos/[markerid]/pos [x] [y]",
        },
    )
    cfg = load_config(cfg_path)
    assert cfg.osc_transmitters.transmitters[0].enabled is True


def test_save_keeps_enabled_true_for_literal_only_row_without_marker(
    live_server,
) -> None:
    """A row whose templates have zero placeholders has no
    dependencies – Enabled stays on regardless of ``marker_id``.
    Mirrors the runtime gate where literal-only rows fire even with
    no marker configured."""
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    _post_form(
        base,
        f"/section/osc_binding/{row_id}",
        {
            "enabled": "on",
            "markers": "",
            "osc_message": "/cue/go MyCue",
        },
    )
    cfg = load_config(cfg_path)
    row = cfg.osc_transmitters.transmitters[0]
    assert row.markers == []
    assert row.enabled is True


def test_section_render_marks_unresolved_pill_in_dataset(live_server) -> None:
    """The partial exposes each row's unresolved-placeholder set as a
    JSON-encoded ``data-osc-unresolved-placeholders`` attribute on the
    editor div. The pill JS reads it on render and applies
    ``data-unresolved="true"`` to matching pills, so the initial page
    load shows the right state."""
    _, base, cfg_path = live_server
    cfg = load_config(cfg_path)
    cfg.osc_transmitters.transmitters.append(
        OscTransmitterConfig(
            id="r-uns",
            name="Unset",
            markers=[],
            address="/eos/1/go",
            args=["[x]"],
        ),
    )
    save_config(cfg, cfg_path)
    status, body = _get(base, "/section/osc_bindings")
    assert status == 200
    # JSON-encoded list with ``[x]`` as the unresolved token.
    assert (
        'data-osc-unresolved-placeholders="[\\u0022[x]\\u0022]"' in body
        or 'data-osc-unresolved-placeholders="[&quot;[x]&quot;]"' in body
        or '[\\"[x]\\"]' in body
        or '["[x]"]' in body
    )
    # The Enabled checkbox carries the unresolved-flag marker as a
    # custom ``data-osc-unresolved`` attribute, not ``aria-invalid``:
    # ``aria-invalid`` would trip the shared form Save-gate
    # (``refreshFormGate`` disables every Save button). The row must
    # stay Saveable, with ``enabled`` coerced to ``False`` until the
    # dependency resolves.
    assert 'data-osc-unresolved="true"' in body


def test_stream_trigger_form_includes_mode_dropdown(live_server) -> None:
    """The Stream trigger panel renders a mode dropdown (Send always /
    Send only on change) plus a threshold input. The threshold is
    server-side ``hidden`` when mode is ``always`` so the initial render
    matches the JS toggle state without a flash."""
    _, base, cfg_path = live_server
    cfg = load_config(cfg_path)
    cfg.osc_transmitters.transmitters.append(
        OscTransmitterConfig(id="r1", name="X"),
    )
    save_config(cfg, cfg_path)
    status, body = _get(base, "/section/osc_binding/r1/trigger_form")
    assert status == 200
    assert 'name="trigger.mode"' in body
    assert "Send always" in body
    assert "Send only on change" in body
    assert 'name="trigger.min_change_m"' in body
    assert "any axis" in body.lower() or "default marker" in body.lower()
    # Initial mode "always" → threshold wrap is hidden server-side.
    assert "data-osc-stream-min-change-wrap" in body
    assert "hidden" in body


def test_diagnostics_tab_renders_three_structured_panels(live_server) -> None:
    """The Diagnostics tab is a stack of three labelled panels (Live
    status / Preview / Test send). Each panel carries a
    ``data-osc-diag-*`` hook the JS in ``base.tpl`` keys off so a
    refactor that drops the wire-up fails CI loudly."""
    _, base, cfg_path = live_server
    cfg = load_config(cfg_path)
    cfg.osc_transmitters.transmitters.append(
        OscTransmitterConfig(id="r1", name="X"),
    )
    save_config(cfg, cfg_path)
    status, body = _get(base, "/section/osc_bindings")
    assert status == 200
    # All three panels exist and are scoped to the row id.
    assert 'data-osc-diag-panel="r1"' in body
    assert 'data-osc-diag-status-body="r1"' in body
    assert 'data-osc-diag-preview-body="r1"' in body
    assert 'data-osc-diag-test-body="r1"' in body
    # Each action button declares its target via the data API the
    # click handler in ``base.tpl`` reads.
    assert 'data-osc-diag-refresh="status"' in body
    assert 'data-osc-diag-action="preview"' in body
    assert 'data-osc-diag-action="test"' in body
    assert "Live status" in body
    assert "Send test packet" in body
    assert "Status updates while the runtime is active" not in body


def test_section_render_includes_save_as_template_button(live_server) -> None:
    """Every OSC row carries a per-row "Save as template…" button. The
    click handler in ``base.tpl`` keys off ``data-osc-save-template-btn``
    + ``data-row-id`` so it can pull the row's name + osc_message from
    the form without re-deriving the row id from a parent ``<details>``."""
    _, base, cfg_path = live_server
    cfg = load_config(cfg_path)
    cfg.osc_transmitters.transmitters.append(
        OscTransmitterConfig(id="r1", name="X"),
    )
    save_config(cfg, cfg_path)
    status, body = _get(base, "/section/osc_bindings")
    assert status == 200
    assert "data-osc-save-template-btn" in body
    assert 'data-row-id="r1"' in body
    # The button opts into the dirty-state gate so editing the row's
    # form disables it until the row is saved. Templates capture
    # what's on disk; without the gate the pre-edit state gets
    # templated.
    assert "data-template-save" in body
    # Dep selector points to the row's own form so editing other rows
    # doesn't bleed into this row's gate.
    assert "data-template-deps='form.osc-binding-form[data-row-id=\"r1\"]'" in body
    # The form opts in via ``data-template-form``; the input/change
    # listener walks up to it to find the dirty scope.
    assert 'data-template-form="1"' in body


def test_section_render_no_unresolved_for_clean_row(live_server) -> None:
    """A row whose deps all resolve renders an empty unresolved list
    and the Enabled checkbox stays without ``data-osc-unresolved``."""
    _, base, cfg_path = live_server
    cfg = load_config(cfg_path)
    cfg.osc_transmitters.transmitters.append(
        OscTransmitterConfig(
            id="r-ok",
            name="Clean",
            markers=["1"],
            address="/eos/[markerid]/pos",
            args=["[x]"],
        ),
    )
    save_config(cfg, cfg_path)
    status, body = _get(base, "/section/osc_bindings")
    assert status == 200
    assert 'data-osc-unresolved-placeholders="[]"' in body
    # No aria-invalid on the Enabled checkbox of this row. The body
    # could contain aria-invalid for some other reason on another row,
    # so we don't assert globally – instead, we sanity-check the row's
    # placeholder set is empty.


def test_section_render_passes_registered_marker_ids_to_editor(
    live_server,
) -> None:
    """The partial exposes ``data-osc-registered-marker-ids`` so the
    JS can re-evaluate dependencies as the operator edits the
    ``Default marker`` field without a server round-trip."""
    _, base, cfg_path = live_server
    cfg = load_config(cfg_path)
    cfg.controlled_marker_ids = [1, 2, 5]
    cfg.osc_transmitters.transmitters.append(
        OscTransmitterConfig(id="r1", name="X"),
    )
    save_config(cfg, cfg_path)
    status, body = _get(base, "/section/osc_bindings")
    assert status == 200
    assert "data-osc-registered-marker-ids" in body
    # Don't pin the exact JSON shape – Bottle's HTML escape can vary.
    assert "[1, 2, 5]" in body or "[1,2,5]" in body


def _summary_dot_state(body: str, row_id: str) -> str:
    """Return the summary (header) status-dot state for a row: the dot class
    inside the row's ``<summary>``, scoped so nested-chip dots don't leak in."""
    row = re.search(
        r'data-row-id="' + re.escape(row_id) + r'".*?<summary[^>]*>(.*?)</summary>',
        body,
        re.S,
    )
    assert row is not None, f"row {row_id} not found"
    dot = re.search(r"osc-binding-enabled-dot (on|off|invalid)", row.group(1))
    assert dot is not None, "no status dot in summary"
    return dot.group(1)


def test_section_render_uncontrolled_secondary_is_red_chip_not_dot(live_server) -> None:
    """A multi-marker row with a valid primary keeps a yellow (``on``) summary
    dot; the uncontrolled secondary reddens only its own nested chip."""
    _, base, cfg_path = live_server
    cfg = load_config(cfg_path)
    cfg.controlled_marker_ids = [1]
    cfg.osc_destinations.destinations.append(OscDestinationConfig(id="d1", name="C", host="10.0.0.1", port=8001))
    cfg.osc_transmitters.transmitters.append(
        # Primary 1 controlled, secondary 5 not.
        OscTransmitterConfig(id="r1", name="X", enabled=True, markers=["1", "5"], destination_id="d1"),
    )
    save_config(cfg, cfg_path)
    status, body = _get(base, "/section/osc_bindings")
    assert status == 200
    # Summary dot stays yellow – at least one marker is controlled.
    assert _summary_dot_state(body, "r1") == "on"
    # The uncontrolled secondary shows a red chip.
    assert "osc-binding-nested-row is-invalid" in body


def test_section_render_no_controlled_marker_reddens_dot(live_server) -> None:
    """A row that names markers but controls none of them can't send any, so
    its summary dot is red."""
    _, base, cfg_path = live_server
    cfg = load_config(cfg_path)
    cfg.controlled_marker_ids = [1]
    cfg.osc_destinations.destinations.append(OscDestinationConfig(id="d1", name="C", host="10.0.0.1", port=8001))
    cfg.osc_transmitters.transmitters.append(
        OscTransmitterConfig(id="r1", name="X", markers=["5"], destination_id="d1"),
    )
    save_config(cfg, cfg_path)
    status, body = _get(base, "/section/osc_bindings")
    assert status == 200
    assert _summary_dot_state(body, "r1") == "invalid"


def test_section_render_invalid_message_reddens_dot(live_server) -> None:
    """An unresolvable placeholder in the address/args (``[x:9]`` for an
    unregistered marker) reddens the summary dot even when destination and
    markers are fine."""
    _, base, cfg_path = live_server
    cfg = load_config(cfg_path)
    cfg.controlled_marker_ids = [1]
    cfg.osc_destinations.destinations.append(OscDestinationConfig(id="d1", name="C", host="10.0.0.1", port=8001))
    cfg.osc_transmitters.transmitters.append(
        OscTransmitterConfig(id="r1", name="X", markers=["1"], destination_id="d1", address="/eos", args=["[x:9]"]),
    )
    save_config(cfg, cfg_path)
    status, body = _get(base, "/section/osc_bindings")
    assert status == 200
    assert _summary_dot_state(body, "r1") == "invalid"


def test_section_render_marks_missing_destination_red(live_server) -> None:
    """A transmitter with no destination can never fire, so its summary dot
    is red even when its (single) marker is controlled."""
    _, base, cfg_path = live_server
    cfg = load_config(cfg_path)
    cfg.controlled_marker_ids = [1]
    cfg.osc_transmitters.transmitters.append(
        OscTransmitterConfig(id="r1", name="X", markers=["1"]),  # no destination_id
    )
    save_config(cfg, cfg_path)
    status, body = _get(base, "/section/osc_bindings")
    assert status == 200
    assert _summary_dot_state(body, "r1") == "invalid"


def test_section_render_no_invalid_dot_when_valid(live_server) -> None:
    """A row with a valid destination whose every named id is controlled
    renders no ``invalid`` marker dot anywhere – the summary dot reflects
    only enabled/disabled."""
    _, base, cfg_path = live_server
    cfg = load_config(cfg_path)
    cfg.controlled_marker_ids = [1, 5]
    cfg.osc_destinations.destinations.append(OscDestinationConfig(id="d1", name="C", host="10.0.0.1", port=8001))
    cfg.osc_transmitters.transmitters.append(
        OscTransmitterConfig(id="r1", name="X", markers=["1", "5"], destination_id="d1"),
    )
    save_config(cfg, cfg_path)
    status, body = _get(base, "/section/osc_bindings")
    assert status == 200
    assert "osc-binding-enabled-dot invalid" not in body
    assert "osc-binding-nested-row is-invalid" not in body


def test_save_persists_explicit_markerN_placeholder(live_server) -> None:
    """``[name:markerN]`` references a specific marker rather than the
    row's default. The parser treats it as just another
    whitespace-delimited token; the renderer resolves it at
    send-time."""
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    _post_form(
        base,
        f"/section/osc_binding/{row_id}",
        {
            "osc_message": "/multi [x:2] [y:2] [z:2]",
        },
    )
    cfg = load_config(cfg_path)
    row = cfg.osc_transmitters.transmitters[0]
    assert row.address == "/multi"
    assert row.args == ["[x:2]", "[y:2]", "[z:2]"]


def test_save_with_quoted_arg_preserves_whitespace(live_server) -> None:
    """A quoted string argument arrives at the receiver as a single
    arg, not one arg per whitespace-delimited fragment with literal
    quote characters glued onto the ends."""
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    _post_form(
        base,
        f"/section/osc_binding/{row_id}",
        {
            "osc_message": '/cmd "Fadermaster Executor 202 At 100 Fade 1"',
        },
    )
    cfg = load_config(cfg_path)
    row = cfg.osc_transmitters.transmitters[0]
    assert row.address == "/cmd"
    assert row.args == ["Fadermaster Executor 202 At 100 Fade 1"]


def test_save_with_unclosed_quote_preserves_existing_values(live_server) -> None:
    """A programmatic POST with an unclosed quote is rejected server-side
    (400) before any field is applied, so the row's previous values survive
    intact rather than being half-applied."""
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row = cfg.osc_transmitters.transmitters[0]
    row.address = "/seed"
    row.args = ["[x]", "1.5"]
    save_config(cfg, cfg_path)
    status, _ = _post_form(
        base,
        f"/section/osc_binding/{row.id}",
        {
            "osc_message": '/cue "unclosed',
        },
    )
    assert status == 400
    cfg = load_config(cfg_path)
    row = cfg.osc_transmitters.transmitters[0]
    assert row.address == "/seed"
    assert row.args == ["[x]", "1.5"]


def test_parse_message_preserves_current_on_unclosed_quote() -> None:
    """Unclosed quote → ``tokenize_osc_message`` raises ``ValueError``; the
    wrapper keeps the row's current address/args rather than corrupting them.
    Defensive: the save routes validate and reject before reaching here."""
    out = _parse_osc_message({"osc_message": '/cue "unclosed'}, "/seed", ["[x]", "1.5"])
    assert out == ("/seed", ["[x]", "1.5"])


def test_round_trip_quoted_arg_renders_and_re_parses_unchanged(live_server) -> None:
    """After saving an arg containing whitespace, the section partial
    must render the field with the quote re-applied so the next Save
    (without changing anything) re-tokenises to the same args.
    Otherwise the round-trip mangles the row across page loads.

    The save handler returns the section partial with ``focus_id`` set
    to the just-saved row, so the expanded form (including the hidden
    ``osc_message`` mirror input) is in the response body.
    """
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    status, body = _post_form(
        base,
        f"/section/osc_binding/{row_id}",
        {
            "osc_message": '/cmd "Go Cue 1" 1.5',
        },
    )
    assert status == 200
    # Hidden ``osc_message`` mirror carries the joined value the next
    # Save will re-tokenise. Quotes must be re-applied around any arg
    # that contains whitespace, otherwise the next Save would split
    # ``Go Cue 1`` into three separate args.
    assert f'id="osc-message-{row_id}-hidden"' in body
    assert "/cmd &quot;Go Cue 1&quot; 1.5" in body  # HTML-escaped form
    # Round-trip: re-tokenise the freeform string the field would
    # post on the next Save, prove it produces the original args.
    from openfollow.osc.parser import (
        join_osc_message,
        tokenize_osc_message,
    )

    cfg = load_config(cfg_path)
    saved_row = cfg.osc_transmitters.transmitters[0]
    rendered = join_osc_message(saved_row.address, saved_row.args)
    address, args = tokenize_osc_message(rendered)
    assert address == saved_row.address
    assert args == saved_row.args


# ---------------------------------------------------------------------------
# Section-level template picker: template choice happens at row-creation
# time, not per-row.
# ---------------------------------------------------------------------------


def test_add_with_builtin_template_populates_address_and_args(live_server) -> None:
    # Bundled "system" templates are sourced from disk, addressed via the
    # same ``file:<filename>`` selector user templates use; the apply path
    # resolves either kind through ``find_template``.
    _, base, cfg_path = live_server
    _post_form(
        base,
        "/section/osc_bindings/add",
        {"template_id": "file:osc_output.etc-eos.openfollowtemplate"},
    )
    cfg = load_config(cfg_path)
    row = cfg.osc_transmitters.transmitters[0]
    assert row.name == "ETC Eos"
    assert row.address.startswith("/eos/")
    assert row.args  # non-empty


@pytest.mark.parametrize(
    "template_filename,template_name",
    [
        ("osc_output.etc-eos.openfollowtemplate", "ETC Eos"),
        # Filename stays ``adm-osc`` while the display name carries the
        # 2D/3D suffix, so existing rows whose template_id references this
        # file keep resolving.
        ("osc_output.adm-osc.openfollowtemplate", "ADM-OSC 2D"),
        ("osc_output.adm-osc-3d.openfollowtemplate", "ADM-OSC 3D"),
        ("osc_output.dnb-absolute.openfollowtemplate", "d&b absolute"),
    ],
)
def test_add_with_builtin_template_pre_fills_stream_30hz_trigger(
    live_server,
    template_filename: str,
    template_name: str,
) -> None:
    """Applying a bundled-system position-output template lands the
    row's ``trigger`` as ``Stream @ 30 Hz``, typed by
    ``OscTransmitterConfig.__post_init__`` from the template's payload.
    System templates are bundled as ``.openfollowtemplate`` files, so the
    trigger is read from the JSON payload; the files spell it out
    explicitly so the apply doesn't depend on the row config's default
    trigger matching what the templates want.
    """
    _, base, cfg_path = live_server
    _post_form(
        base,
        "/section/osc_bindings/add",
        {"template_id": f"file:{template_filename}"},
    )
    cfg = load_config(cfg_path)
    row = cfg.osc_transmitters.transmitters[0]
    assert row.name == template_name
    assert isinstance(row.trigger, StreamTrigger)
    assert row.trigger.rate_hz == 30
    # The ``rate_hz`` mirror on the row must match for readers that
    # haven't migrated to ``trigger.rate_hz``.
    assert row.rate_hz == 30


def test_initial_page_render_with_osc_row_includes_framing_dropdown(live_server) -> None:
    """The index include of ``partials/osc_bindings.tpl`` must pass
    ``valid_framings``. Without it, a config with at least one OSC row
    raised ``NameError: name 'valid_framings' is not defined`` on the
    initial page render – the HTMX partial route had the context but
    ``index()`` did not. Invisible to other tests because the
    live_server fixture starts with zero rows, so the framing-dropdown
    loop never executed."""
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    status, body = _get(base, "/")
    assert status == 200
    # ``<option value="slip">`` confirms the loop over valid_framings ran.
    assert 'value="slip"' in body
    assert 'value="length_prefix"' in body


def test_initial_page_render_shows_nested_markers(live_server) -> None:
    """A multi-marker row's nested chips must render on the first index page
    load, not only after a save – ``index()`` builds the same marker context
    the section render does."""
    _, base, cfg_path = live_server
    cfg = load_config(cfg_path)
    cfg.controlled_marker_ids = [1, 3]
    cfg.osc_transmitters.transmitters.append(
        OscTransmitterConfig(id="r1", name="X", markers=["1", "3"]),
    )
    save_config(cfg, cfg_path)
    status, body = _get(base, "/")
    assert status == 200
    assert 'class="osc-binding-nested"' in body
    assert "Marker 1 (1)" in body
    assert "Marker 3 (3)" in body


def test_add_with_unknown_template_id_falls_back_to_blank_row(live_server) -> None:
    """A stale dropdown selection (template deleted in another tab)
    yields a blank row rather than a row with a confusing identity."""
    _, base, cfg_path = live_server
    _post_form(
        base,
        "/section/osc_bindings/add",
        {"template_id": "ghost-template"},
    )
    cfg = load_config(cfg_path)
    row = cfg.osc_transmitters.transmitters[0]
    assert row.name == "New transmitter"
    assert row.address == ""
    assert row.args == []


def test_initial_page_render_includes_disk_user_templates(live_server) -> None:
    """``index.tpl`` wires ``osc_user_templates`` through the index
    render so disk-loaded user templates are in the dropdown from the
    first page load, not only after an HTMX section refresh."""
    from pathlib import Path

    from openfollow.templates.writer import write_user_template

    _, base, cfg_path = live_server
    write_user_template(
        Path(cfg_path).parent / "templates",
        "osc_output",
        "First Load",
        {"address": "/x", "args": []},
    )
    status, body = _get(base, "/")
    assert status == 200
    assert "First Load" in body


def test_add_with_file_template_copies_extended_fields(live_server) -> None:
    """Dropdown apply via ``add_osc_binding`` must restore
    destination_id/rate/trigger from a file template, not just
    ``name``/``address``/``args``; otherwise applying a saved template
    silently drops the saved destination + trigger.

    Dropdown values for user templates are ``file:<filename>`` rather
    than the envelope id, so the add request keys on the
    filesystem-unique filename.
    """
    from pathlib import Path

    from openfollow.templates.writer import write_user_template

    _, base, cfg_path = live_server
    written = write_user_template(
        Path(cfg_path).parent / "templates",
        "osc_output",
        "Stage Preset",
        {
            "name": "ETC Stage Left",
            "destination_id": "dest-5",
            "address": "/eos/go",
            "args": ["[x]", "[y]"],
            "rate_hz": 60,
            "trigger": {"kind": "stream", "rate_hz": 60, "mode": "on_change", "min_change_m": 0.1},
        },
        template_id="stage-preset-id",
    )
    _post_form(
        base,
        "/section/osc_bindings/add",
        {"template_id": f"file:{written.name}"},
    )
    cfg = load_config(cfg_path)
    row = cfg.osc_transmitters.transmitters[-1]
    assert row.name == "ETC Stage Left"
    assert row.destination_id == "dest-5"
    assert row.address == "/eos/go"
    assert row.args == ["[x]", "[y]"]
    assert row.rate_hz == 60
    from openfollow.configuration import StreamTrigger

    assert isinstance(row.trigger, StreamTrigger)
    assert row.trigger.mode == "on_change"
    assert row.trigger.min_change_m == 0.1


def test_add_with_custom_template_populates_from_saved_template(
    live_server,
) -> None:
    """Two saved templates; the second is the match. Exercises both the
    no-match and match branches of the lookup iteration.

    User templates live as ``.openfollowtemplate`` files under
    ``<config-dir>/templates/user/``; the writer seeds the test files so
    the disk shape matches what ``add_osc_binding`` reads."""
    from pathlib import Path

    from openfollow.templates.writer import write_user_template

    _, base, cfg_path = live_server
    templates_root = Path(cfg_path).parent / "templates"
    write_user_template(
        templates_root,
        "osc_output",
        "Other",
        {"address": "/other", "args": ["[z]"]},
        template_id="other-tpl",
    )
    written_my = write_user_template(
        templates_root,
        "osc_output",
        "My Template",
        {"address": "/my/[markerid]", "args": ["[x]", "[y]"]},
        template_id="my-tpl",
    )
    _post_form(
        base,
        "/section/osc_bindings/add",
        {"template_id": f"file:{written_my.name}"},
    )
    cfg = load_config(cfg_path)
    row = cfg.osc_transmitters.transmitters[-1]
    assert row.name == "My Template"
    assert row.address == "/my/[markerid]"
    assert row.args == ["[x]", "[y]"]


def test_add_with_empty_template_id_yields_blank_row(live_server) -> None:
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {"template_id": ""})
    cfg = load_config(cfg_path)
    row = cfg.osc_transmitters.transmitters[0]
    assert row.name == "New transmitter"
    assert row.address == ""
    assert row.args == []


def test_add_with_unknown_template_id_yields_blank_row(live_server) -> None:
    """A non-empty ``template_id`` that doesn't match a builtin and
    doesn't start with ``file:`` (e.g. a stale envelope id from a
    browser-cached form) falls through to a blank row rather than
    crashing."""
    _, base, cfg_path = live_server
    _post_form(
        base,
        "/section/osc_bindings/add",
        {"template_id": "some-stale-envelope-id"},
    )
    cfg = load_config(cfg_path)
    row = cfg.osc_transmitters.transmitters[0]
    assert row.name == "New transmitter"
    assert row.address == ""
    assert row.args == []


def test_add_with_unsafe_file_template_id_yields_blank_row(live_server) -> None:
    """A ``file:`` prefix with a path-traversal payload must not reach
    ``find_template`` – the basename safety check refuses the value and
    the route falls through to a blank row, so a crafted POST can't
    escape the templates folder via the dropdown."""
    _, base, cfg_path = live_server
    _post_form(
        base,
        "/section/osc_bindings/add",
        {"template_id": "file:../etc/passwd.openfollowtemplate"},
    )
    cfg = load_config(cfg_path)
    row = cfg.osc_transmitters.transmitters[0]
    assert row.name == "New transmitter"
    assert row.address == ""


# ---------------------------------------------------------------------------
# Trigger sub-form swap
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind,marker",
    [
        ("stream", "Rate (Hz)"),
        ("hotkey", "Modifiers"),
        ("controller_button", "Button"),
        # Each marker string is unique to its trigger form so the
        # assertion catches a future swap of one form for another.
        ("midi_message", "Capture"),
        ("fader_on_change", "Throttles fader-driven sends"),
    ],
)
def test_trigger_form_renders_per_kind(live_server, kind: str, marker: str) -> None:
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    status, body = _get(base, f"/section/osc_binding/{row_id}/trigger_form?kind={kind}")
    assert status == 200
    assert marker in body


def test_trigger_form_unknown_kind_falls_back_to_stream(live_server) -> None:
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    status, body = _get(base, f"/section/osc_binding/{row_id}/trigger_form?kind=bogus")
    assert status == 200
    assert "Rate (Hz)" in body


def test_trigger_form_404_for_unknown_row(live_server) -> None:
    _, base, _ = live_server
    status, _ = _get(base, "/section/osc_binding/no-such/trigger_form?kind=stream")
    assert status == 404


def test_trigger_form_accepts_trigger_type_query_param(live_server) -> None:
    """The dropdown's wire name is ``trigger.type``; HTMX's
    ``hx-include="this"`` sends ``?trigger.type=<value>``. The route
    must read it under that name (legacy ``?kind=`` and
    ``?trigger.kind=`` still work for older callers)."""
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    status, body = _get(
        base,
        f"/section/osc_binding/{row_id}/trigger_form?trigger.type=hotkey",
    )
    assert status == 200
    assert "Modifiers" in body


def test_trigger_form_accepts_legacy_trigger_kind_query_param(live_server) -> None:
    """Backwards compat: a stale tab might post ``?trigger.kind=``;
    the route must still resolve it correctly."""
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    status, body = _get(
        base,
        f"/section/osc_binding/{row_id}/trigger_form?trigger.kind=controller_button",
    )
    assert status == 200
    assert "Button" in body


# ---------------------------------------------------------------------------
# Diagnostics – provider absent (default) and provider attached
# ---------------------------------------------------------------------------


def test_status_returns_available_false_without_provider(live_server) -> None:
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    status, body = _get(base, f"/api/osc_binding/{row_id}/status")
    assert status == 200
    payload = json.loads(body)
    assert payload == {"available": False, "pending": False}


def test_status_404_for_unknown_row(live_server) -> None:
    _, base, _ = live_server
    status, body = _get(base, "/api/osc_binding/no-such/status")
    assert status == 404
    assert json.loads(body)["available"] is False


def test_status_returns_provider_payload(tmp_path, monkeypatch) -> None:
    server, base, cfg_path = _live_server_with_providers(
        tmp_path,
        monkeypatch,
        osc_binding_status_provider=lambda rid: {
            "pps": 30.0,
            "last_error": None,
            "healthy": True,
            "ring_buffer": [],
        },
    )
    try:
        _post_form(base, "/section/osc_bindings/add", {})
        cfg = load_config(cfg_path)
        row_id = cfg.osc_transmitters.transmitters[0].id
        status, body = _get(base, f"/api/osc_binding/{row_id}/status")
        assert status == 200
        payload = json.loads(body)
        assert payload["available"] is True
        assert payload["pps"] == 30.0
        assert payload["healthy"] is True
    finally:
        server.stop()


def test_status_provider_returning_none_collapses_to_unavailable(tmp_path, monkeypatch) -> None:
    server, base, cfg_path = _live_server_with_providers(
        tmp_path,
        monkeypatch,
        osc_binding_status_provider=lambda rid: None,
    )
    try:
        _post_form(base, "/section/osc_bindings/add", {})
        cfg = load_config(cfg_path)
        row_id = cfg.osc_transmitters.transmitters[0].id
        status, body = _get(base, f"/api/osc_binding/{row_id}/status")
        assert status == 200
        assert json.loads(body) == {"available": False, "pending": False}
    finally:
        server.stop()


def test_test_send_returns_available_false_without_provider(live_server) -> None:
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    status, body = _post_form(base, f"/api/osc_binding/{row_id}/test", {})
    assert status == 200
    assert json.loads(body) == {"available": False, "pending": False}


def test_test_send_404_for_unknown_row(live_server) -> None:
    _, base, _ = live_server
    status, body = _post_form(base, "/api/osc_binding/no-such/test", {})
    assert status == 404
    assert json.loads(body)["available"] is False


def test_test_send_returns_provider_payload(tmp_path, monkeypatch) -> None:
    server, base, cfg_path = _live_server_with_providers(
        tmp_path,
        monkeypatch,
        osc_binding_test_send=lambda rid: {
            "sent": True,
            "address": "/x",
            "args": [1, 2],
        },
    )
    try:
        _post_form(base, "/section/osc_bindings/add", {})
        cfg = load_config(cfg_path)
        row_id = cfg.osc_transmitters.transmitters[0].id
        status, body = _post_form(base, f"/api/osc_binding/{row_id}/test", {})
        assert status == 200
        payload = json.loads(body)
        assert payload["available"] is True
        assert payload["sent"] is True
    finally:
        server.stop()


def test_preview_returns_available_false_without_provider(live_server) -> None:
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    cfg = load_config(cfg_path)
    row_id = cfg.osc_transmitters.transmitters[0].id
    status, body = _get(base, f"/api/osc_binding/{row_id}/preview")
    assert status == 200
    assert json.loads(body) == {"available": False, "pending": False}


def test_preview_404_for_unknown_row(live_server) -> None:
    _, base, _ = live_server
    status, body = _get(base, "/api/osc_binding/no-such/preview")
    assert status == 404
    assert json.loads(body)["available"] is False


def test_preview_returns_provider_payload(tmp_path, monkeypatch) -> None:
    server, base, cfg_path = _live_server_with_providers(
        tmp_path,
        monkeypatch,
        osc_binding_preview_provider=lambda rid: {
            "address": "/eos/set/patch/1/augment3d/position",
            "args": [1.0, 2.0, 0.0, 0, 0, 0],
            "skipped": False,
        },
    )
    try:
        _post_form(base, "/section/osc_bindings/add", {})
        cfg = load_config(cfg_path)
        row_id = cfg.osc_transmitters.transmitters[0].id
        status, body = _get(base, f"/api/osc_binding/{row_id}/preview")
        assert status == 200
        payload = json.loads(body)
        assert payload["available"] is True
        assert payload["address"].startswith("/eos/")
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# Save-as-template
# ---------------------------------------------------------------------------


def test_save_template_persists(live_server) -> None:
    """The HTMX ``POST /section/osc_templates`` route writes a
    ``.openfollowtemplate`` file under ``<config-dir>/templates/user/``.
    The save response (HTMX partial) shape matches what the modal
    expects."""
    from pathlib import Path

    from openfollow.templates.loader import list_templates_by_type

    _, base, cfg_path = live_server
    status, body = _post_form(
        base,
        "/section/osc_templates",
        {
            "name": "Custom",
            "osc_message": "/custom/[markerid]/pos [x] [y]",
        },
    )
    assert status == 200
    assert "saved" in body.lower()
    templates_root = Path(cfg_path).parent / "templates"
    user_entries = [e for e in list_templates_by_type(templates_root, "osc_output") if not e.is_system]
    assert len(user_entries) == 1
    saved = user_entries[0].template
    assert saved is not None
    assert saved.name == "Custom"
    assert saved.payload["address"] == "/custom/[markerid]/pos"
    assert saved.payload["args"] == ["[x]", "[y]"]


def test_save_template_rejects_missing_name(live_server) -> None:
    _, base, _ = live_server
    status, body = _post_form(
        base,
        "/section/osc_templates",
        {
            "name": "",
            "osc_message": "/foo",
        },
    )
    assert status == 400
    assert "Name and OSC message" in body


def test_save_template_rejects_missing_message(live_server) -> None:
    _, base, _ = live_server
    status, body = _post_form(
        base,
        "/section/osc_templates",
        {
            "name": "X",
            "osc_message": "",
        },
    )
    assert status == 400
    assert "Name and OSC message" in body


def test_save_template_rejects_address_without_slash(live_server) -> None:
    _, base, _ = live_server
    status, body = _post_form(
        base,
        "/section/osc_templates",
        {
            "name": "X",
            "osc_message": "no-slash [x]",
        },
    )
    assert status == 400
    assert "must start with" in body


def test_save_template_preserves_quoted_arg(live_server) -> None:
    """``save_osc_template`` must use the same quote-aware tokeniser as
    the per-row save path, otherwise a template with a quoted arg
    (``/cmd \"Go Cue 1\" 1.5``) mangles the arg into separate tokens
    and round-trips wrong when the template is applied.

    The template lands as a ``.openfollowtemplate`` file under
    ``<config-dir>/templates/user/``."""
    from pathlib import Path

    from openfollow.templates.loader import list_templates_by_type

    _, base, cfg_path = live_server
    status, _ = _post_form(
        base,
        "/section/osc_templates",
        {
            "name": "MA Cue",
            "osc_message": '/cmd "Go Cue 1 Fade 2"',
        },
    )
    assert status == 200
    templates_root = Path(cfg_path).parent / "templates"
    user_entries = [e for e in list_templates_by_type(templates_root, "osc_output") if not e.is_system]
    assert len(user_entries) == 1
    saved = user_entries[0].template
    assert saved is not None
    assert saved.payload["address"] == "/cmd"
    assert saved.payload["args"] == ["Go Cue 1 Fade 2"]


def test_save_template_rejects_unclosed_quote(live_server) -> None:
    """The unclosed-quote error path mirrors the per-row save
    endpoint – same wording, same 400."""
    _, base, _ = live_server
    status, body = _post_form(
        base,
        "/section/osc_templates",
        {
            "name": "X",
            "osc_message": '/cmd "unclosed',
        },
    )
    assert status == 400
    assert "Unclosed quote" in body


def test_save_template_rejects_name_over_max_len(live_server) -> None:
    """The route reuses the per-row blur validators, so the same
    ``max_len=64`` cap the on-blur ``name`` field enforces also gates
    the save-as-template endpoint. Without it a programmatic POST could
    persist an arbitrarily large ``name`` to disk, bypassing the blur
    validation."""
    _, base, _ = live_server
    long_name = "x" * 65  # one over the cap
    status, body = _post_form(
        base,
        "/section/osc_templates",
        {
            "name": long_name,
            "osc_message": "/cmd",
        },
    )
    assert status == 400
    assert "64" in body  # error message names the bound


def test_save_template_rejects_message_over_max_len(live_server) -> None:
    """Mirror of the ``name`` cap test for the ``osc_message`` field's
    ``max_len=2048`` rule.

    Use a string that's long but doesn't trigger the unclosed-quote
    path – otherwise this tests the wrong error branch."""
    _, base, _ = live_server
    long_message = "/cmd " + ("a" * 2050)  # well over 2048 chars
    status, body = _post_form(
        base,
        "/section/osc_templates",
        {
            "name": "Long",
            "osc_message": long_message,
        },
    )
    assert status == 400
    assert "2048" in body


# ---------------------------------------------------------------------------
# Form parser unit tests – exercise every branch
# ---------------------------------------------------------------------------


class _ParserTests:
    """Grouped under a class only for readability."""


def test_parse_message_returns_none_when_field_missing() -> None:
    """No ``osc_message`` key → caller must leave the row's address +
    args alone (covers the partial-save path)."""
    assert _parse_osc_message({"name": "x"}, "/seed", ["[x]"]) is None


def test_parse_message_splits_address_and_args() -> None:
    out = _parse_osc_message(
        {"osc_message": "/eos/[markerid]/pos [x] [y] [z]"},
        "",
        [],
    )
    assert out == ("/eos/[markerid]/pos", ["[x]", "[y]", "[z]"])


def test_parse_message_empty_input_clears_both() -> None:
    out = _parse_osc_message({"osc_message": ""}, "/seed", ["[x]"])
    assert out == ("", [])


def test_parse_message_collapses_whitespace() -> None:
    out = _parse_osc_message(
        {"osc_message": "  /a    [x]   [y]  "},
        "",
        [],
    )
    assert out == ("/a", ["[x]", "[y]"])


def test_parse_message_address_only_no_args() -> None:
    out = _parse_osc_message({"osc_message": "/just/address"}, "", [])
    assert out == ("/just/address", [])


def test_parse_trigger_subtable_stream() -> None:
    d = _parse_trigger_subtable({"trigger.type": "stream", "trigger.rate_hz": "60"}, "stream")
    assert d == {"kind": "stream", "rate_hz": 60}


def test_parse_trigger_subtable_stream_default_rate_when_missing() -> None:
    d = _parse_trigger_subtable({"trigger.type": "stream"}, "stream")
    assert d == {"kind": "stream"}


def test_parse_trigger_subtable_stream_with_mode_and_min_change() -> None:
    # ``trigger.mode`` + ``trigger.min_change_m`` round-trip as the
    # ``mode`` and ``min_change_m`` fields ``StreamTrigger`` reads.
    d = _parse_trigger_subtable(
        {
            "trigger.type": "stream",
            "trigger.rate_hz": "30",
            "trigger.mode": "on_change",
            "trigger.min_change_m": "0.1",
        },
        "stream",
    )
    assert d == {
        "kind": "stream",
        "rate_hz": 30,
        "mode": "on_change",
        "min_change_m": 0.1,
    }


def test_parse_trigger_subtable_stream_omits_mode_keys_when_absent() -> None:
    # A partial save (e.g. just changing the rate) should leave the
    # mode + threshold alone server-side. Parser only emits the keys
    # that were explicitly in the form.
    d = _parse_trigger_subtable(
        {"trigger.type": "stream", "trigger.rate_hz": "30"},
        "stream",
    )
    assert "mode" not in d
    assert "min_change_m" not in d


def test_parse_trigger_subtable_hotkey_with_modifiers_list() -> None:
    d = _parse_trigger_subtable(
        {
            "trigger.type": "hotkey",
            "trigger.key": "r",
            "trigger.modifiers": ["ctrl", "shift"],
            "trigger.edge": "press",
        },
        "stream",
    )
    assert d == {
        "kind": "hotkey",
        "key": "r",
        "modifiers": ["ctrl", "shift"],
        "edge": "press",
    }


def test_parse_trigger_subtable_hotkey_with_single_modifier_string() -> None:
    d = _parse_trigger_subtable(
        {
            "trigger.type": "hotkey",
            "trigger.key": "r",
            "trigger.modifiers": "ctrl",
        },
        "stream",
    )
    assert d["modifiers"] == ["ctrl"]


def test_parse_trigger_subtable_hotkey_empty_modifiers() -> None:
    d = _parse_trigger_subtable(
        {
            "trigger.type": "hotkey",
            "trigger.modifiers": "",
        },
        "stream",
    )
    assert d["modifiers"] == []


def test_parse_trigger_subtable_controller_button() -> None:
    d = _parse_trigger_subtable(
        {
            "trigger.type": "controller_button",
            "trigger.button": "A",
            "trigger.edge": "release",
        },
        "stream",
    )
    assert d == {"kind": "controller_button", "button": "A", "edge": "release"}


def test_parse_trigger_subtable_falls_back_to_current_kind() -> None:
    """No ``trigger.type`` in form → use the row's existing kind."""
    d = _parse_trigger_subtable({"trigger.rate_hz": "10"}, "stream")
    assert d["kind"] == "stream"
    assert d["rate_hz"] == 10


def test_parse_trigger_subtable_accepts_legacy_trigger_kind_wire_name() -> None:
    """A stale form posting under the old ``trigger.kind`` wire name
    still resolves correctly. New code emits ``trigger.type``; both are
    accepted at the parser layer."""
    d = _parse_trigger_subtable({"trigger.kind": "hotkey"}, "stream")
    assert d == {"kind": "hotkey"}


def test_parse_trigger_subtable_prefers_trigger_type_over_legacy_kind() -> None:
    """When both spellings are posted (very stale tab + new tab race),
    the new ``trigger.type`` value wins."""
    d = _parse_trigger_subtable(
        {"trigger.type": "hotkey", "trigger.kind": "controller_button"},
        "stream",
    )
    assert d["kind"] == "hotkey"


def test_parse_trigger_subtable_phase_b_kind_is_returned_verbatim() -> None:
    """MIDI/fader kinds pass through; the dataclass post_init resolves
    them via ``_trigger_from_dict`` rather than the parser."""
    d = _parse_trigger_subtable({"trigger.type": "midi_message"}, "stream")
    assert d == {"kind": "midi_message"}


def test_apply_osc_binding_fields_top_level_and_trigger() -> None:
    row = OscTransmitterConfig()
    _apply_osc_binding_fields(
        row,
        {
            "enabled": "on",
            "name": "X",
            "destination_id": "dest-9",
            "markers": "3",
            "osc_message": "/foo [x] [y]",
            "trigger.type": "stream",
            "trigger.rate_hz": "60",
        },
    )
    assert row.name == "X"
    assert row.enabled is True
    assert row.destination_id == "dest-9"
    assert row.markers == ["3"]
    assert row.address == "/foo"
    assert row.args == ["[x]", "[y]"]
    assert isinstance(row.trigger, StreamTrigger)
    assert row.trigger.rate_hz == 60


def test_apply_osc_binding_fields_without_osc_message_keeps_address_and_args() -> None:
    """A partial save without ``osc_message`` leaves the row's address
    + args untouched."""
    row = OscTransmitterConfig(address="/keep", args=["[x]"])
    _apply_osc_binding_fields(row, {"name": "renamed"})
    assert row.address == "/keep"
    assert row.args == ["[x]"]


def test_apply_osc_binding_fields_skips_missing_fields() -> None:
    row = OscTransmitterConfig(name="seed", destination_id="seed-dest")
    _apply_osc_binding_fields(row, {"markers": "3"})
    assert row.name == "seed"
    assert row.destination_id == "seed-dest"
    assert row.markers == ["3"]


def test_apply_osc_binding_fields_no_trigger_keys_keeps_trigger() -> None:
    row = OscTransmitterConfig(trigger=HotkeyTrigger(key="r"))
    _apply_osc_binding_fields(row, {"name": "no-trigger-keys"})
    assert isinstance(row.trigger, HotkeyTrigger)
    assert row.trigger.key == "r"


def test_parse_trigger_subtable_hotkey_without_modifiers_or_edge() -> None:
    """Hotkey arm where neither ``trigger.modifiers`` nor
    ``trigger.edge`` are in the form data – covers the
    branch-not-taken path through both ``if`` checks."""
    d = _parse_trigger_subtable(
        {
            "trigger.type": "hotkey",
            "trigger.key": "r",
        },
        "stream",
    )
    assert d == {"kind": "hotkey", "key": "r"}


def test_parse_trigger_subtable_controller_button_without_button() -> None:
    """Controller-button arm where ``trigger.button`` is missing –
    covers the branch-not-taken path through that ``if`` check."""
    d = _parse_trigger_subtable(
        {
            "trigger.type": "controller_button",
            "trigger.edge": "press",
        },
        "stream",
    )
    assert d == {"kind": "controller_button", "edge": "press"}


def test_parse_trigger_subtable_controller_button_without_edge() -> None:
    """Controller-button arm where ``trigger.edge`` is missing –
    covers the branch-not-taken path through that ``if`` check."""
    d = _parse_trigger_subtable(
        {
            "trigger.type": "controller_button",
            "trigger.button": "A",
        },
        "stream",
    )
    assert d == {"kind": "controller_button", "button": "A"}


# ---------------------------------------------------------------------------
# ConfigWebServer diagnostics provider hooks – direct unit tests.
# Routes don't consume the conflicts provider, so test the accessor
# directly to keep coverage on web/server.py.
# ---------------------------------------------------------------------------


def test_get_osc_binding_conflicts_no_provider(tmp_path) -> None:
    server = ConfigWebServer(
        config_path=str(tmp_path / "config.toml"),
        host="127.0.0.1",
        port=1,
        system_name="t",
    )
    assert server.get_osc_binding_conflicts("key", "w", "osc:hotkey") == []


def test_get_osc_binding_conflicts_with_provider(tmp_path) -> None:
    captured: list[tuple[str, str, str]] = []

    def fake_conflicts(kind: str, identifier: str, owner: str) -> list[str]:
        captured.append((kind, identifier, owner))
        return ["system:movement"]

    server = ConfigWebServer(
        config_path=str(tmp_path / "config.toml"),
        host="127.0.0.1",
        port=1,
        system_name="t",
        osc_binding_conflicts_provider=fake_conflicts,
    )
    assert server.get_osc_binding_conflicts("key", "w", "osc:hotkey") == [
        "system:movement",
    ]
    assert captured == [("key", "w", "osc:hotkey")]


# ---------------------------------------------------------------------------
# Conflict-warning surface: /api/validate/osc_binding/trigger.{key,button}
# consults the ConflictRegistry through the server's provider hook.
# ---------------------------------------------------------------------------


def test_validate_trigger_key_no_conflict_returns_empty(
    tmp_path,
    monkeypatch,
) -> None:
    """No conflict-provider attached → blur validation passes silently."""
    server, base, _ = _live_server_with_providers(tmp_path, monkeypatch)
    try:
        status, body = _get(base, "/api/validate/osc_binding/trigger.key?trigger.key=q")
        assert status == 200
        assert body == ""
    finally:
        server.stop()


def test_validate_trigger_key_with_system_movement_conflict_renders_error(
    tmp_path,
    monkeypatch,
) -> None:
    """A blur-validation request for a key claimed by ``system:movement``
    surfaces a hard error the operator sees inline."""
    server, base, _ = _live_server_with_providers(
        tmp_path,
        monkeypatch,
        osc_binding_conflicts_provider=lambda kind, ident, owner: (
            ["system:movement"] if kind == "key" and ident == "w" else []
        ),
    )
    try:
        status, body = _get(base, "/api/validate/osc_binding/trigger.key?trigger.key=w")
        assert status == 200
        assert "field-error-msg" in body
        assert "system:movement" in body
        assert "Pick a different key" in body
    finally:
        server.stop()


def test_validate_trigger_button_with_conflict_renders_error(
    tmp_path,
    monkeypatch,
) -> None:
    server, base, _ = _live_server_with_providers(
        tmp_path,
        monkeypatch,
        osc_binding_conflicts_provider=lambda kind, ident, owner: (
            ["system:movement"] if kind == "controller_button" and ident == "A" else []
        ),
    )
    try:
        status, body = _get(
            base,
            "/api/validate/osc_binding/trigger.button?trigger.button=A",
        )
        assert status == 200
        assert "field-error-msg" in body
        assert "Pick a different button" in body
    finally:
        server.stop()


def test_validate_trigger_key_empty_value_skips_conflict_check(
    tmp_path,
    monkeypatch,
) -> None:
    """An empty key – operator hasn't picked one yet – must not surface
    a phantom conflict. Covers the ``raw and ...`` guard in the
    conflict-check arm of ``api_validate``."""
    captured: list[tuple[str, str, str]] = []

    def fake_conflicts(kind: str, ident: str, owner: str) -> list[str]:
        captured.append((kind, ident, owner))
        return ["system:movement"]  # would conflict if called

    server, base, _ = _live_server_with_providers(
        tmp_path,
        monkeypatch,
        osc_binding_conflicts_provider=fake_conflicts,
    )
    try:
        status, body = _get(base, "/api/validate/osc_binding/trigger.key?trigger.key=")
        assert status == 200
        assert body == ""
        # Provider not consulted because the value was empty.
        assert captured == []
    finally:
        server.stop()


def test_validate_trigger_key_no_provider_returns_empty(live_server) -> None:
    """The default fixture has no conflicts provider; an otherwise-valid
    key value still validates clean (registry returns ``[]``)."""
    _, base, _ = live_server
    status, body = _get(base, "/api/validate/osc_binding/trigger.key?trigger.key=q")
    assert status == 200
    assert body == ""


def test_validate_trigger_key_whitespace_only_skips_conflict_check(
    tmp_path,
    monkeypatch,
) -> None:
    """A whitespace-only value (``trigger.key=   ``) is treated as empty
    by the field-rule layer (no field error), so the conflict-check
    arm must also skip – otherwise the provider is called with a
    nonsense ``"   "`` identifier and may surface a bogus conflict.
    Covers the ``raw.strip()`` guard."""
    captured: list[tuple[str, str, str]] = []

    def fake_conflicts(kind: str, ident: str, owner: str) -> list[str]:
        captured.append((kind, ident, owner))
        return ["system:movement"]

    server, base, _ = _live_server_with_providers(
        tmp_path,
        monkeypatch,
        osc_binding_conflicts_provider=fake_conflicts,
    )
    try:
        # ``%20`` × 3 → ``"   "`` once URL-decoded by the framework.
        status, body = _get(
            base,
            "/api/validate/osc_binding/trigger.key?trigger.key=%20%20%20",
        )
        assert status == 200
        assert body == ""
        # Provider not consulted – the strip() guard kicked in.
        assert captured == []
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# Cross-field unresolved-placeholder blur validator. The osc_message +
# marker_id fields share the dependency, so blur on either surfaces the
# same inline error.
# ---------------------------------------------------------------------------


def test_validate_osc_message_blurs_clean_when_default_marker_set(
    live_server,
) -> None:
    """A row whose default-marker placeholder is satisfied by a
    registered ``marker_id`` blurs without an inline error. Default
    config registers marker 0."""
    _, base, _ = live_server
    status, body = _get(
        base,
        "/api/validate/osc_binding/osc_message?osc_message=%2Feos+%5Bx%5D&markers=1",
    )
    assert status == 200
    assert body == ""


def test_validate_osc_message_surfaces_missing_default_marker(
    live_server,
) -> None:
    """A row with ``[x]`` and an empty ``marker_id`` blurs with an
    inline error naming the unresolved token + the actionable fix."""
    _, base, _ = live_server
    status, body = _get(
        base,
        "/api/validate/osc_binding/osc_message?osc_message=%2Feos+%5Bx%5D&markers=",
    )
    assert status == 200
    assert "[x]" in body
    assert "default marker" in body.lower()
    # Cross-field unresolved-blur renders as a soft warn-msg (not
    # field-error-msg) so the form's Save-gate doesn't disable Save –
    # the row stays Saveable with ``enabled`` coerced to ``False`` until
    # the dep resolves.
    assert 'class="field-warn-msg"' in body
    assert 'class="field-error-msg"' not in body


def test_validate_osc_message_surfaces_unregistered_explicit_marker(
    live_server,
) -> None:
    """``[x:9]`` for an unregistered marker blurs with an
    inline error naming the unresolved explicit token."""
    _, base, _ = live_server
    status, body = _get(
        base,
        "/api/validate/osc_binding/osc_message?osc_message=%2Feos+%5Bx%3A9%5D&markers=1",
    )
    assert status == 200
    assert "[x:9]" in body
    assert "isn" in body  # "isn't registered"


def test_validate_markers_surfaces_message_dependency_error(
    live_server,
) -> None:
    """Symmetric: the ``marker_id`` blur endpoint also surfaces the
    cross-field error so an operator who clears the input gets
    inline feedback even before they touch the message field."""
    _, base, _ = live_server
    status, body = _get(
        base,
        "/api/validate/osc_binding/markers?markers=&osc_message=%2Feos+%5Bx%5D",
    )
    assert status == 200
    assert "[x]" in body
    assert "default marker" in body.lower()


def test_validate_osc_message_with_explicit_markerid_no_error(
    live_server,
) -> None:
    """``[markerid:markerN]`` resolves to the literal id without a
    resolver call – must not flag as unresolved even when N isn't
    registered."""
    _, base, _ = live_server
    status, body = _get(
        base,
        "/api/validate/osc_binding/osc_message?osc_message=%2Feos%2F%5Bmarkerid%3A9%5D%2Fgo&markers=1",
    )
    assert status == 200
    assert body == ""


def test_validate_osc_message_blurs_clean_for_literal_only_message(
    live_server,
) -> None:
    """A literal-only message (``/cue/go MyCue``) has no placeholder
    dependencies; blur passes regardless of ``marker_id``."""
    _, base, _ = live_server
    status, body = _get(
        base,
        "/api/validate/osc_binding/osc_message?osc_message=%2Fcue%2Fgo+MyCue&markers=",
    )
    assert status == 200
    assert body == ""


def test_validate_markers_blurs_clean_when_no_message_present(
    live_server,
) -> None:
    """The ``marker_id`` endpoint also runs the cross-field check,
    but a missing / empty ``osc_message`` sibling means there's
    nothing to compare against – early-out without a spurious
    error. Exercises the no-address-no-args short-circuit."""
    _, base, _ = live_server
    status, body = _get(
        base,
        "/api/validate/osc_binding/markers?markers=&osc_message=",
    )
    assert status == 200
    assert body == ""


def test_validate_markers_blurs_clean_when_message_key_omitted(
    live_server,
) -> None:
    """If a blur sends only ``marker_id`` (no ``osc_message`` key), the
    cross-field check returns ``None`` via the ``_parse_osc_message``
    "key missing" branch and the route falls through to the no-error
    response."""
    _, base, _ = live_server
    status, body = _get(
        base,
        "/api/validate/osc_binding/markers?markers=5",
    )
    assert status == 200
    assert body == ""


def test_validate_markers_flags_malformed_token(live_server) -> None:
    """A malformed ``markers`` entry (junk / negative / bad alias) blurs
    with a hard field-error naming the offending entry. The runtime drops
    such tokens, but the operator gets feedback on the typo."""
    _, base, _ = live_server
    for bad in ("bogus", "-1", "c0"):
        status, body = _get(
            base,
            "/api/validate/osc_binding/markers?markers=1," + bad,
        )
        assert status == 200, f"bad input: {bad!r}"
        assert 'class="field-error-msg"' in body, f"expected error for {bad!r}, got {body!r}"
        assert bad in body


def test_validate_markers_allows_non_controlled_id(live_server) -> None:
    """A syntactically-valid id this station doesn't control is ignored at
    send time, not flagged – the markers field blurs clean. The fixture
    controls only marker 1."""
    _, base, _ = live_server
    status, body = _get(
        base,
        "/api/validate/osc_binding/markers?markers=5",
    )
    assert status == 200
    assert 'class="field-error-msg"' not in body


def test_validate_osc_message_keeps_explicit_warn_regardless_of_markers(
    live_server,
) -> None:
    """An explicit ``[x:9]`` is independent of the ``markers`` field, so its
    "references a marker that isn't registered" warning surfaces even when
    ``markers`` carries malformed tokens."""
    _, base, _ = live_server
    status, body = _get(
        base,
        "/api/validate/osc_binding/osc_message?osc_message=%2Feos+%5Bx%3A9%5D&markers=bogus",
    )
    assert status == 200
    assert "[x:9]" in body
    assert "isn" in body  # "isn't registered"


def test_validate_osc_message_collapses_duplicate_unresolved_tokens(
    live_server,
) -> None:
    """A row that uses ``[x]`` three times surfaces the token once in
    the inline error (mirrors the duplicate-collapse rule in
    :func:`unresolved_placeholders`). Exercises the
    ``token not in seen`` branch in
    ``_osc_binding_unresolved_blur_error``."""
    _, base, _ = live_server
    status, body = _get(
        base,
        "/api/validate/osc_binding/osc_message?osc_message=%2Feos+%5Bx%5D+%5Bx%5D+%5Bx%5D&markers=",
    )
    assert status == 200
    assert body.count("[x]") == 1


# ---------------------------------------------------------------------------
# MIDI / fader-on-change trigger forms
# ---------------------------------------------------------------------------


class TestPhaseBTriggerFormParsing:
    """``_parse_trigger_subtable`` packs the MIDI / fader-on-change form
    fields into the dict shape :class:`MidiMessageTrigger` /
    :class:`FaderOnChangeTrigger` accept on construction. These tests
    drive the parser directly so failures point at the wire→config
    translation rather than at HTTP / template plumbing."""

    def test_midi_message_packs_full_field_set(self) -> None:
        """``trigger.midi_type`` is the wire key (``trigger.type`` is
        the kind discriminator). ``midi_channel`` / ``midi_number`` /
        ``midi_value`` accept blank-as-``None`` for the wildcard
        semantic; ``patch_id`` is an int with ``0`` meaning "any
        patch"."""
        out = _parse_trigger_subtable(
            {
                "trigger.type": "midi_message",
                "trigger.midi_type": "control_change",
                "trigger.patch_id": "2",
                "trigger.midi_channel": "3",
                "trigger.midi_number": "7",
                "trigger.midi_value": "64",
            },
            current_kind="stream",
        )
        assert out == {
            "kind": "midi_message",
            "type": "control_change",
            "patch_id": 2,
            "channel": 3,
            "number": 7,
            "value": 64,
        }

    def test_midi_message_blank_inputs_become_wildcards(self) -> None:
        """Empty inputs collapse to ``None`` so a row authored "any
        channel, any number" matches every event for that type.
        ``patch_id`` blank / unset collapses to ``0`` (any patch)."""
        out = _parse_trigger_subtable(
            {
                "trigger.type": "midi_message",
                "trigger.midi_type": "control_change",
                "trigger.patch_id": "",
                "trigger.midi_channel": "",
                "trigger.midi_number": "",
                "trigger.midi_value": "",
            },
            current_kind="stream",
        )
        assert out["channel"] is None
        assert out["number"] is None
        assert out["value"] is None
        assert out["patch_id"] == 0

    def test_fader_on_change_packs_fader_and_rate(self) -> None:
        out = _parse_trigger_subtable(
            {
                "trigger.type": "fader_on_change",
                "trigger.fader": "3",
                "trigger.rate_hz": "60",
            },
            current_kind="stream",
        )
        assert out == {
            "kind": "fader_on_change",
            "fader": 3,
            "rate_hz": 60,
        }

    def test_fader_on_change_omits_fields_on_partial_save(self) -> None:
        """Partial saves (e.g. just toggling Enabled in a different
        tab) post the kind but no trigger sub-fields. The parser
        must not invent defaults – the row's existing values are
        preserved by the apply path's omission semantics."""
        out = _parse_trigger_subtable(
            {"trigger.type": "fader_on_change"},
            current_kind="stream",
        )
        assert out == {"kind": "fader_on_change"}

    def test_fader_on_change_omits_rate_when_only_fader_posted(
        self,
    ) -> None:
        """Field-by-field partial save: only ``trigger.fader``
        present, no ``trigger.rate_hz``. The parser packs only what
        was sent so the apply path preserves the existing rate."""
        out = _parse_trigger_subtable(
            {"trigger.type": "fader_on_change", "trigger.fader": "5"},
            current_kind="stream",
        )
        assert out == {"kind": "fader_on_change", "fader": 5}

    def test_unknown_kind_falls_through_every_branch(self) -> None:
        """A wire ``trigger.type`` that matches no known kind falls
        through the entire if/elif chain. ``_trigger_from_dict`` has its
        own fallback to ``Stream``, so the parser doesn't validate here –
        it passes the value through and lets the canonical validator
        decide."""
        out = _parse_trigger_subtable(
            {"trigger.type": "bogus_unknown_kind"},
            current_kind="stream",
        )
        assert out == {"kind": "bogus_unknown_kind"}


class TestPhaseBTriggerSaveRoundTrip:
    """Saving a MIDI / fader-on-change trigger through the form must
    produce the correct typed trigger on the row, with values that
    round-trip through TOML."""

    def test_save_midi_message_trigger_round_trips(self, live_server) -> None:
        _, base, cfg_path = live_server
        _post_form(base, "/section/osc_bindings/add", {})
        cfg = load_config(cfg_path)
        row_id = cfg.osc_transmitters.transmitters[0].id
        status, _ = _post_form(
            base,
            f"/section/osc_binding/{row_id}",
            {
                "enabled": "on",
                "name": "MIDI Cue",
                "host": "127.0.0.1",
                "port": "9000",
                "protocol": "udp",
                "markers": "",
                "default_fader": "",
                "osc_message": "/cue/[value]",
                "trigger.type": "midi_message",
                "trigger.midi_type": "control_change",
                "trigger.patch_id": "2",
                "trigger.midi_channel": "1",
                "trigger.midi_number": "7",
                "trigger.midi_value": "",
            },
        )
        assert status == 200
        cfg = load_config(cfg_path)
        row = cfg.osc_transmitters.transmitters[0]
        assert isinstance(row.trigger, MidiMessageTrigger)
        assert row.trigger.type == "control_change"
        assert row.trigger.patch_id == 2
        assert row.trigger.channel == 1
        assert row.trigger.number == 7
        assert row.trigger.value is None  # blank wildcard

    def test_save_fader_on_change_trigger_round_trips(self, live_server) -> None:
        _, base, cfg_path = live_server
        _post_form(base, "/section/osc_bindings/add", {})
        cfg = load_config(cfg_path)
        row_id = cfg.osc_transmitters.transmitters[0].id
        status, _ = _post_form(
            base,
            f"/section/osc_binding/{row_id}",
            {
                "enabled": "on",
                "name": "Master Level",
                "host": "127.0.0.1",
                "port": "9000",
                "protocol": "udp",
                "markers": "",
                "default_fader": "1",
                "osc_message": "/level/[fader]",
                "trigger.type": "fader_on_change",
                "trigger.fader": "1",
                "trigger.rate_hz": "30",
            },
        )
        assert status == 200
        cfg = load_config(cfg_path)
        row = cfg.osc_transmitters.transmitters[0]
        assert isinstance(row.trigger, FaderOnChangeTrigger)
        assert row.trigger.fader == 1
        assert row.trigger.rate_hz == 30
        assert row.default_fader == 1


class TestDefaultFaderField:
    """The ``default_fader`` form field mirrors the ``marker_id``
    pattern – empty input → ``None``, valid 1..8 preserved."""

    def test_apply_default_fader_from_form(self) -> None:
        row = OscTransmitterConfig(id="r1", destination_id="d1")
        _apply_osc_binding_fields(row, {"default_fader": "3"})
        assert row.default_fader == 3

    def test_blank_default_fader_collapses_to_none(self) -> None:
        row = OscTransmitterConfig(
            id="r1",
            destination_id="d1",
            default_fader=3,
        )
        _apply_osc_binding_fields(row, {"default_fader": ""})
        assert row.default_fader is None

    def test_out_of_range_default_fader_collapses_to_none(self) -> None:
        """``OscTransmitterConfig.__post_init__`` clamps via
        ``_coerce_optional_int`` so a ``default_fader=9`` lands on
        ``None`` instead of silently routing to fader 8 (valid range
        1..8)."""
        row = OscTransmitterConfig(id="r1", destination_id="d1")
        _apply_osc_binding_fields(row, {"default_fader": "99"})
        assert row.default_fader is None


class TestPhaseBFormSourceHelpers:
    """The form-source helpers that pull operator-defined names /
    aliases out of the config for the trigger dropdowns."""

    def test_virtual_fader_names_uses_custom_when_set(self) -> None:
        from openfollow.configuration import AppConfig

        cfg = AppConfig(
            virtual_faders=VirtualFadersConfig(
                faders=[
                    VirtualFaderConfig(name="Master"),
                    VirtualFaderConfig(name=""),  # blank → fallback label
                    VirtualFaderConfig(name="Aux 1"),
                ]
            ),
        )
        names = _virtual_fader_names_for_form(cfg)
        assert names[0] == (1, "Master")
        assert names[1] == (2, "Fader 2")
        assert names[2] == (3, "Aux 1")
        # Padding fills out to eight entries with default labels.
        assert names[7] == (8, "Fader 8")

    def test_midi_patches_returns_id_label_dicts(self) -> None:
        """``_midi_patches_for_form`` returns ``{"id", "label"}`` dicts
        in MIDI page order. ``MidiConfig`` assigns sequential ids on
        load, so each patch is selectable by id even before it's named;
        the label falls through alias → port name → ``Patch <id>``."""
        from openfollow.configuration import AppConfig

        cfg = AppConfig(
            midi=MidiConfig(
                patches=[
                    MidiPatch(alias="Workspace 1"),
                    MidiPatch(alias=""),  # unnamed – still selectable by id
                    MidiPatch(alias="Workspace 2"),
                ]
            ),
        )
        patches = _midi_patches_for_form(cfg)
        # ``MidiConfig.__post_init__`` assigns ids 1, 2, 3.
        assert patches == [
            {"id": 1, "label": "1 – Workspace 1"},
            {"id": 2, "label": "2 – Patch 2"},
            {"id": 3, "label": "3 – Workspace 2"},
        ]


# ---------------------------------------------------------------------------
# MIDI Learn capture endpoints
# ---------------------------------------------------------------------------


class TestMidiCaptureRoutes:
    """The arm + poll endpoints expose the broker's state to the
    OSC binding form's Capture button. Without a broker wired the
    endpoints return ``{"status": "unavailable"}`` so the form can
    render a meaningful banner instead of a stack trace."""

    def test_arm_unavailable_when_no_broker(self, live_server) -> None:
        """Default ``live_server`` fixture spins a server without
        the MIDI broker – the route must report unavailable rather
        than 500."""
        _, base, _ = live_server
        status, body = _post_form(base, "/api/osc/midi/learn/arm", {})
        assert status == 200
        assert json.loads(body) == {"status": "unavailable"}

    def test_poll_unavailable_when_no_broker(self, live_server) -> None:
        _, base, _ = live_server
        status, body = _get(base, "/api/osc/midi/learn/poll")
        assert status == 200
        assert json.loads(body) == {"status": "unavailable"}

    def test_arm_invokes_broker_when_wired(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        """A wired broker's ``arm`` is called once per POST. The
        provider closure keeps the broker out of the route layer's
        API."""
        arm_calls: list[str] = []

        def fake_arm(row_id: str) -> None:
            arm_calls.append(row_id)

        def fake_poll(row_id: str) -> dict:
            return {"status": "idle"}

        _, base, _ = _live_server_with_providers(
            tmp_path,
            monkeypatch,
            midi_capture_arm=fake_arm,
            midi_capture_poll=fake_poll,
        )
        status, body = _post_form(base, "/api/osc/midi/learn/arm", {})
        assert status == 200
        assert json.loads(body) == {"status": "armed"}
        assert len(arm_calls) == 1

    def test_poll_returns_broker_state(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        """The poll endpoint passes the broker's dict through
        verbatim. The form's HTMX target reads the same shape the
        broker emits – captured / waiting / timeout / idle / cancelled
        – so adding a new state needs no route changes."""
        captured_event = {
            "status": "captured",
            "patch_id": 1,
            "type": "control_change",
            "channel": 1,
            "number": 7,
            "value": 42,
        }

        def fake_poll(row_id: str) -> dict:
            return captured_event

        _, base, _ = _live_server_with_providers(
            tmp_path,
            monkeypatch,
            midi_capture_arm=lambda _row: None,
            midi_capture_poll=fake_poll,
        )
        status, body = _get(base, "/api/osc/midi/learn/poll")
        assert status == 200
        assert json.loads(body) == captured_event


class TestMidiCaptureSectionRoutes:
    """The pure-HTMX Capture button on the OSC binding trigger form
    drives ``/section/osc/midi/learn/{arm,poll}/<row_id>``. These
    return HTML partials, not JSON – a section route per row keeps the
    OOB-swap fragments (which target ``osc-midi-...-<row_id>`` ids on
    the trigger form) row-scoped so two rows can't clobber each other.
    """

    def _add_row(self, base: str, cfg_path: str) -> str:
        _post_form(base, "/section/osc_bindings/add", {})
        cfg = load_config(cfg_path)
        return cfg.osc_transmitters.transmitters[0].id

    def test_arm_returns_listening_partial(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        arm_calls: list[str] = []

        def fake_arm(row_id: str) -> None:
            arm_calls.append(row_id)

        def fake_poll(row_id: str) -> dict:
            return {"status": "armed"}

        _, base, cfg_path = _live_server_with_providers(
            tmp_path,
            monkeypatch,
            midi_capture_arm=fake_arm,
            midi_capture_poll=fake_poll,
        )
        row_id = self._add_row(base, cfg_path)
        status, body = _post_form(
            base,
            f"/section/osc/midi/learn/arm/{row_id}",
            {},
        )
        assert status == 200
        assert arm_calls == [row_id]
        # Listening banner + the 250 ms HTMX poll driver pointing at
        # the matching poll endpoint for this row.
        assert "Listening" in body
        assert f"/section/osc/midi/learn/poll/{row_id}" in body
        assert "every 250ms" in body

    def test_arm_unknown_row_404(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        _, base, _ = _live_server_with_providers(
            tmp_path,
            monkeypatch,
            midi_capture_arm=lambda _row: None,
            midi_capture_poll=lambda _row: {"status": "idle"},
        )
        status, _ = _post_form(
            base,
            "/section/osc/midi/learn/arm/does-not-exist",
            {},
        )
        assert status == 404

    def test_poll_unknown_row_404(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        _, base, _ = _live_server_with_providers(
            tmp_path,
            monkeypatch,
            midi_capture_arm=lambda _row: None,
            midi_capture_poll=lambda _row: {"status": "idle"},
        )
        status, _ = _get(
            base,
            "/section/osc/midi/learn/poll/does-not-exist",
        )
        assert status == 404

    def test_poll_captured_emits_oob_fragments(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        """Captured state must emit one ``hx-swap-oob`` fragment per
        trigger-form field so the operator's row picks up the
        classified MIDI event without a full form re-render."""
        captured = {
            "status": "captured",
            "patch_id": 0,
            "type": "control_change",
            "channel": 1,
            "number": 7,
            "value": 42,
        }

        _, base, cfg_path = _live_server_with_providers(
            tmp_path,
            monkeypatch,
            midi_capture_arm=lambda _row: None,
            midi_capture_poll=lambda _row: captured,
        )
        row_id = self._add_row(base, cfg_path)
        status, body = _get(
            base,
            f"/section/osc/midi/learn/poll/{row_id}",
        )
        assert status == 200
        # Each form field is OOB-targeted by id; the captured
        # values are pre-selected on the new fragments.
        assert f'id="osc-midi-type-{row_id}"' in body
        assert f'id="osc-midi-patch-{row_id}"' in body
        assert f'id="osc-midi-channel-{row_id}"' in body
        assert f'id="osc-midi-number-{row_id}"' in body
        assert f'id="osc-midi-value-{row_id}"' in body
        assert 'hx-swap-oob="true"' in body
        # Polling driver is gone – captured is a terminal state for
        # the partial; the operator clicks Capture again to re-arm.
        assert "every 250ms" not in body

    def test_poll_timeout_renders_retry(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        _, base, cfg_path = _live_server_with_providers(
            tmp_path,
            monkeypatch,
            midi_capture_arm=lambda _row: None,
            midi_capture_poll=lambda _row: {"status": "timeout"},
        )
        row_id = self._add_row(base, cfg_path)
        status, body = _get(
            base,
            f"/section/osc/midi/learn/poll/{row_id}",
        )
        assert status == 200
        assert "Timed out" in body
        assert "Retry" in body
        # Retry re-arms the same row's broker through the section
        # arm endpoint – keeps the operator on a single click.
        assert f"/section/osc/midi/learn/arm/{row_id}" in body

    def test_poll_unavailable_when_no_broker(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        """Without a broker wired the partial reports the subsystem
        is offline rather than 500'ing or rendering a useless
        Listening banner that would never resolve."""
        _, base, cfg_path = _live_server_with_providers(
            tmp_path,
            monkeypatch,
        )
        row_id = self._add_row(base, cfg_path)
        status, body = _get(
            base,
            f"/section/osc/midi/learn/poll/{row_id}",
        )
        assert status == 200
        assert "MIDI subsystem not running" in body


# OSC template dropdowns share one scan per render
# ---------------------------------------------------------------------------


def test_osc_dropdowns_scan_templates_once_per_render(
    live_server,
    monkeypatch,
) -> None:
    """The user + system OSC dropdowns are built from a single template
    scan per render – one ``list_templates_by_type`` call, not one per
    list (which would re-glob ``system/`` + ``user/`` and re-parse every
    ``.openfollowtemplate`` file twice)."""
    import openfollow.web.routes as routes_mod

    scans: list[str] = []
    real = routes_mod.list_templates_by_type

    def _counting(root, template_type):
        scans.append(template_type)
        return real(root, template_type)

    monkeypatch.setattr(routes_mod, "list_templates_by_type", _counting)
    _, base, _ = live_server

    # Full page render.
    scans.clear()
    status, _ = _get(base, "/")
    assert status == 200
    assert scans.count("osc_output") == 1

    # Section partial render – the path every CRUD op re-renders through.
    scans.clear()
    status, _ = _get(base, "/section/osc_bindings")
    assert status == 200
    assert scans.count("osc_output") == 1


# ---------------------------------------------------------------------------
# Server-side osc_message validation (leading-slash / unclosed-quote)
# ---------------------------------------------------------------------------


def test_save_with_valid_message_persists(live_server) -> None:
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    row_id = load_config(cfg_path).osc_transmitters.transmitters[0].id
    status, _ = _post_form(base, f"/section/osc_binding/{row_id}", {"osc_message": "/cue/go 1.5"})
    assert status == 200
    row = load_config(cfg_path).osc_transmitters.transmitters[0]
    assert row.address == "/cue/go"
    assert row.args == ["1.5"]


def test_save_rejects_address_without_leading_slash(live_server) -> None:
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    row_id = load_config(cfg_path).osc_transmitters.transmitters[0].id
    status, _ = _post_form(
        base,
        f"/section/osc_binding/{row_id}",
        {"name": "Bad", "osc_message": "cue/go 1.0"},  # no leading slash
    )
    assert status == 400
    # Nothing persisted – the whole save was rejected before apply.
    row = load_config(cfg_path).osc_transmitters.transmitters[0]
    assert row.address != "cue/go"
    assert row.name == "New transmitter"


def test_save_as_template_rejects_address_without_leading_slash(live_server) -> None:
    _, base, cfg_path = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    row_id = load_config(cfg_path).osc_transmitters.transmitters[0].id
    status, _ = _post_form(
        base,
        f"/section/osc_binding/{row_id}/save_as_template",
        {"template_name": "T", "osc_message": "cue/go 1.0"},
    )
    assert status == 400


# ---------------------------------------------------------------------------
# Runtime-detached: pending vs no-manager in the diagnostics routes
# ---------------------------------------------------------------------------


def _attached_no_row_server(tmp_path, monkeypatch):
    return _live_server_with_providers(
        tmp_path,
        monkeypatch,
        osc_binding_status_provider=lambda rid: None,
        osc_binding_preview_provider=lambda rid: None,
        osc_binding_test_send=lambda rid: {},
        osc_manager_attached_provider=lambda: True,
    )


def test_status_pending_when_manager_attached_but_row_unserviced(tmp_path, monkeypatch) -> None:
    server, base, cfg_path = _attached_no_row_server(tmp_path, monkeypatch)
    try:
        _post_form(base, "/section/osc_bindings/add", {})
        row_id = load_config(cfg_path).osc_transmitters.transmitters[0].id
        status, body = _get(base, f"/api/osc_binding/{row_id}/status")
        assert status == 200
        assert json.loads(body) == {"available": False, "pending": True}
    finally:
        server.stop()


def test_preview_pending_when_manager_attached_but_row_unserviced(tmp_path, monkeypatch) -> None:
    server, base, cfg_path = _attached_no_row_server(tmp_path, monkeypatch)
    try:
        _post_form(base, "/section/osc_bindings/add", {})
        row_id = load_config(cfg_path).osc_transmitters.transmitters[0].id
        status, body = _get(base, f"/api/osc_binding/{row_id}/preview")
        assert status == 200
        assert json.loads(body) == {"available": False, "pending": True}
    finally:
        server.stop()


def test_test_send_pending_when_manager_attached_but_row_unserviced(tmp_path, monkeypatch) -> None:
    server, base, cfg_path = _attached_no_row_server(tmp_path, monkeypatch)
    try:
        _post_form(base, "/section/osc_bindings/add", {})
        row_id = load_config(cfg_path).osc_transmitters.transmitters[0].id
        status, body = _post_form(base, f"/api/osc_binding/{row_id}/test", {})
        assert status == 200
        assert json.loads(body) == {"available": False, "pending": True}
    finally:
        server.stop()
