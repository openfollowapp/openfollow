# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Integration tests for the MIDI page (Input tab).

The page has two sub-sections (Devices / Virtual Faders), each with
its own form. These tests drive the live server through
the route layer so failures point at HTTP/template plumbing rather
than at the in-process apply functions.

Provider hooks (``midi_discovered_devices_provider`` /
``midi_fader_values_provider`` / ``marker_fader_values_provider``)
default to ``None`` on the basic fixture so the page renders
empty-state cleanly without a real MIDI substrate. A separate
fixture wires fakes for tests that exercise the live-poll
endpoints or the populated Devices table.
"""

from __future__ import annotations

import contextlib
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import pytest

import openfollow.web.discovery as discovery_module
from openfollow.configuration import (
    MidiConfig,
    MidiPatch,
    VirtualFaderConfig,
    VirtualFadersConfig,
    load_config,
    save_config,
)
from openfollow.web.server import ConfigWebServer

pytestmark = pytest.mark.integration


def _find_free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_for_port(
    port: int,
    host: str = "127.0.0.1",
    timeout: float = 5.0,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.1):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def _get(base: str, path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


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


@pytest.fixture()
def live_server(tmp_path, monkeypatch):
    """Plain fixture – no MIDI substrate wired. The page renders
    empty-state for both Devices and the live-value poll."""
    monkeypatch.setattr(
        discovery_module.BeaconSender,
        "start",
        lambda self: None,
    )
    monkeypatch.setattr(
        discovery_module.BeaconSender,
        "stop",
        lambda self: None,
    )
    monkeypatch.setattr(
        discovery_module.BeaconReceiver,
        "start",
        lambda self: None,
    )
    monkeypatch.setattr(
        discovery_module.BeaconReceiver,
        "stop",
        lambda self: None,
    )
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


@contextlib.contextmanager
def _live_server_with_midi_providers(
    tmp_path,
    monkeypatch,
    *,
    discovered: list[dict[str, Any]] | None = None,
    fader_values: list[dict[str, Any]] | None = None,
    marker_fader_values: list[dict[str, Any]] | None = None,
    capture_state: dict[str, Any] | None = None,
):
    """Fixture variant that wires fake provider callables for device discovery.
    Context-managed so the listening socket and server thread are always
    torn down, even on test failure. The context-manager pattern prevents
    server-per-call leaks that would cascade test failures through port reuse.
    """
    monkeypatch.setattr(
        discovery_module.BeaconSender,
        "start",
        lambda self: None,
    )
    monkeypatch.setattr(
        discovery_module.BeaconSender,
        "stop",
        lambda self: None,
    )
    monkeypatch.setattr(
        discovery_module.BeaconReceiver,
        "start",
        lambda self: None,
    )
    monkeypatch.setattr(
        discovery_module.BeaconReceiver,
        "stop",
        lambda self: None,
    )
    port = _find_free_tcp_port()
    config_path = tmp_path / "config.toml"

    def _devices() -> list[dict[str, Any]]:
        return list(discovered or [])

    def _values() -> list[dict[str, Any]]:
        return list(fader_values or [])

    def _marker_values() -> list[dict[str, Any]]:
        return list(marker_fader_values or [])

    # Single-shot capture broker (shared by the OSC trigger Capture and
    # the Fader Learn flows). Records the row ids it was armed with so
    # Fader Learn tests can assert the ``fader:<idx>`` namespacing.
    capture_arm_calls: list[str] = []

    def _capture_arm(row_id: str = "") -> None:
        capture_arm_calls.append(row_id)

    def _capture_poll(row_id: str = "") -> dict[str, Any]:
        return dict(capture_state or {"status": "idle"})

    server = ConfigWebServer(
        config_path=str(config_path),
        host="127.0.0.1",
        port=port,
        system_name="TestSystem",
        midi_discovered_devices_provider=_devices,
        midi_fader_values_provider=_values,
        marker_fader_values_provider=_marker_values,
        midi_capture_arm=_capture_arm,
        midi_capture_poll=_capture_poll,
    )
    # Stash the arm-call counter on the server object so route
    # tests can assert on it without exposing a global.
    server._test_capture_arm_calls = capture_arm_calls  # type: ignore[attr-defined]
    server.start()
    try:
        assert _wait_for_port(port)
        yield server, f"http://127.0.0.1:{port}", str(config_path)
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# Section render
# ---------------------------------------------------------------------------


class TestMidiSectionRender:
    def test_renders_with_no_patches(self, live_server) -> None:
        """Empty-state path: the MIDI Patches list shows the
        no-patches-yet banner with an "Add new MIDI Patch" button;
        the Virtual Faders strip row always renders eight strips."""
        _, base, _ = live_server
        status, body = _get(base, "/section/midi")
        assert status == 200
        assert "MIDI" in body
        # Patch list empty state shows no-patches banner with Add button always visible.
        assert "MIDI Patches" in body
        assert "Add new MIDI Patch" in body
        assert "No MIDI patches yet" in body
        # Eight virtual fader strips always render – each has a
        # uniquely-id'd track / fill / handle / value triplet so the
        # OOB poll snippets find their targets.
        for n in range(1, 9):
            assert f'data-fader-idx="{n}"' in body
            assert f'id="midi-fader-strip-fill-{n}"' in body
        # The detail panel is rendered for fader 1 on first paint.
        assert 'id="midi-fader-detail-panel"' in body
        assert "Fader 1 settings" in body

    def test_fader_strip_tint_reflects_color(self, live_server) -> None:
        """An editable fader strip is tinted with the operator's colour
        (hex + 25% alpha) via ``--fader-tint``; the black default reads
        as "no colour chosen" and renders ``transparent`` so an
        unconfigured strip keeps its prior untinted look (no dark wash)."""
        _, base, cfg_path = live_server
        cfg = load_config(cfg_path)
        cfg.virtual_faders = VirtualFadersConfig(
            faders=[
                VirtualFaderConfig(name="Tinted", color="#ff0000"),
                VirtualFaderConfig(name="Plain"),  # default #000000
            ]
        )
        save_config(cfg, cfg_path)
        status, body = _get(base, "/section/midi")
        assert status == 200
        # Custom colour → hex tint at ~25% alpha.
        assert "--fader-tint:#ff000040" in body
        # Default black → transparent, never a #00000040 wash.
        assert "--fader-tint:transparent" in body
        assert "#00000040" not in body

    def test_renders_device_options_when_provider_lists_them(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        with _live_server_with_midi_providers(
            tmp_path,
            monkeypatch,
            discovered=[
                {
                    "identifier": "port:MIDI Mix|MIDI Mix",
                    "port_name": "MIDI Mix",
                    "product": "MIDI Mix",
                    "serial": None,
                },
                {
                    "identifier": "serial:ABC123",
                    "port_name": "X-Touch",
                    "product": "X-Touch Mini",
                    "serial": "ABC123",
                },
            ],
        ) as (_, base, cfg_path):
            # Seed one patch so the row (and therefore the Device
            # <select> populated from discovered_devices) renders.
            cfg = load_config(cfg_path)
            cfg.midi = MidiConfig(patches=[MidiPatch(id=1)])
            save_config(cfg, cfg_path)
            status, body = _get(base, "/section/midi")
            assert status == 200
            # Both discovered devices appear as <option> entries in
            # the patch row's Device select, keyed by identifier.
            assert 'name="device"' in body
            assert 'value="port:MIDI Mix|MIDI Mix"' in body
            assert 'value="serial:ABC123"' in body
            assert "MIDI Mix" in body
            assert "X-Touch" in body
            # The serial annotation is appended to the option label.
            assert "ABC123" in body
            # With a patch present the no-patches banner is gone.
            assert "No MIDI patches yet" not in body


# ---------------------------------------------------------------------------
# MIDI Patches – CRUD operations
# ---------------------------------------------------------------------------


class TestSavePatches:
    def test_add_appends_blank_patch(self, live_server) -> None:
        """``Add new MIDI Patch`` appends a fresh, device-less patch
        with the next free sequential id (>= 1)."""
        _, base, cfg_path = live_server
        status, _ = _post_form(base, "/section/midi/patches/add", {})
        assert status == 200
        cfg = load_config(cfg_path)
        assert len(cfg.midi.patches) == 1
        assert cfg.midi.patches[0].id == 1
        assert cfg.midi.patches[0].alias == ""
        assert cfg.midi.patches[0].port_name == ""

    def test_save_assigns_alias_and_device_to_patch(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        """Add a patch, then save it with an alias + a device
        identifier. The handler resolves the identifier to the
        connected device's port_name / product / serial and persists
        them on the patch. Survives a reload."""
        with _live_server_with_midi_providers(
            tmp_path,
            monkeypatch,
            discovered=[
                {
                    "identifier": "port:MIDI Mix|MIDI Mix",
                    "port_name": "MIDI Mix",
                    "product": "MIDI Mix",
                    "serial": None,
                },
            ],
        ) as (_, base, cfg_path):
            # Create patch 1.
            status, _ = _post_form(base, "/section/midi/patches/add", {})
            assert status == 200
            # Assign alias + device identifier to it.
            status, _ = _post_form(
                base,
                "/section/midi/patches/1",
                {
                    "alias": "Workspace 1",
                    "device": "port:MIDI Mix|MIDI Mix",
                },
            )
            assert status == 200
            cfg = load_config(cfg_path)
            assert len(cfg.midi.patches) == 1
            patch = cfg.midi.patches[0]
            assert patch.id == 1
            assert patch.alias == "Workspace 1"
            assert patch.port_name == "MIDI Mix"
            assert patch.product == "MIDI Mix"

    def test_save_with_no_device_clears_device_binding(
        self,
        live_server,
    ) -> None:
        """Selecting the ``– none –`` device option (empty identifier)
        leaves the patch unassigned: its alias persists but no
        port_name / product / serial are bound, so no listener port
        is opened on the next apply pass."""
        _, base, cfg_path = live_server
        # Seed a patch that already has a device bound so we can
        # confirm saving with a blank device clears the binding.
        cfg = load_config(cfg_path)
        cfg.midi = MidiConfig(
            patches=[
                MidiPatch(
                    id=1,
                    alias="OldAlias",
                    port_name="MIDI Mix",
                    product="MIDI Mix",
                ),
            ]
        )
        save_config(cfg, cfg_path)
        status, _ = _post_form(
            base,
            "/section/midi/patches/1",
            {
                "alias": "Renamed",
                "device": "",
            },
        )
        assert status == 200
        cfg = load_config(cfg_path)
        assert len(cfg.midi.patches) == 1
        patch = cfg.midi.patches[0]
        assert patch.alias == "Renamed"
        assert patch.port_name == ""
        assert patch.product == ""
        assert patch.serial == ""

    def test_unplugged_device_binding_preserved_on_save(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        """An unplugged device patch preserves its binding when re-saved:
        the handler keeps existing port_name / product / serial instead of
        clearing them when the device isn't in the connected list."""
        with _live_server_with_midi_providers(
            tmp_path,
            monkeypatch,
            discovered=[
                {
                    "identifier": "port:MIDI Mix|MIDI Mix",
                    "port_name": "MIDI Mix",
                    "product": "MIDI Mix",
                    "serial": None,
                },
            ],
        ) as (_, base, cfg_path):
            cfg = load_config(cfg_path)
            cfg.midi = MidiConfig(
                patches=[
                    # Bound to "port:Unplugged|Unplugged" – not in the
                    # discovered list, i.e. currently disconnected.
                    MidiPatch(
                        id=1,
                        alias="StagedKeys",
                        port_name="Unplugged",
                        product="Unplugged",
                    ),
                ]
            )
            save_config(cfg, cfg_path)
            # Re-save the row: the disconnected device's own
            # identifier is re-submitted (the dropdown's preserved
            # option), so the binding must survive.
            status, _ = _post_form(
                base,
                "/section/midi/patches/1",
                {
                    "alias": "StagedKeys",
                    "device": "port:Unplugged|Unplugged",
                },
            )
            assert status == 200
            cfg = load_config(cfg_path)
            assert len(cfg.midi.patches) == 1
            patch = cfg.midi.patches[0]
            assert patch.alias == "StagedKeys"
            assert patch.port_name == "Unplugged"
            assert patch.product == "Unplugged"

    def test_save_with_unconnected_device_identifier_clears_binding(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        """Choosing a device whose identifier isn't among the
        currently-connected ports (and isn't the patch's own
        identifier) clears the binding: the resolver scans the
        connected devices, finds no match, and returns empty fields.
        Distinct from the disconnected-preserve case, where the
        re-submitted identifier equals the patch's own."""
        with _live_server_with_midi_providers(
            tmp_path,
            monkeypatch,
            discovered=[
                {
                    "identifier": "port:MIDI Mix|MIDI Mix",
                    "port_name": "MIDI Mix",
                    "product": "MIDI Mix",
                    "serial": None,
                },
            ],
        ) as (_, base, cfg_path):
            cfg = load_config(cfg_path)
            cfg.midi = MidiConfig(
                patches=[
                    MidiPatch(
                        id=1,
                        alias="Keys",
                        port_name="MIDI Mix",
                        product="MIDI Mix",
                    ),
                ]
            )
            save_config(cfg, cfg_path)
            # Submit a non-empty identifier that is neither the patch's
            # own ("port:MIDI Mix|MIDI Mix") nor a connected device, so
            # the resolver loops the connected list, matches nothing,
            # and clears the binding.
            status, _ = _post_form(
                base,
                "/section/midi/patches/1",
                {
                    "alias": "Keys",
                    "device": "port:Ghost|Ghost",
                },
            )
            assert status == 200
            cfg = load_config(cfg_path)
            patch = cfg.midi.patches[0]
            assert patch.alias == "Keys"
            assert patch.port_name == ""
            assert patch.product == ""
            assert patch.serial == ""

    def test_delete_removes_patch_by_id(self, live_server) -> None:
        """``Delete`` removes the patch with the matching id and
        leaves the others intact."""
        _, base, cfg_path = live_server
        cfg = load_config(cfg_path)
        cfg.midi = MidiConfig(
            patches=[
                MidiPatch(id=1, alias="A"),
                MidiPatch(id=2, alias="B"),
                MidiPatch(id=3, alias="C"),
            ]
        )
        save_config(cfg, cfg_path)
        status, _ = _post_form(base, "/section/midi/patches/2/delete", {})
        assert status == 200
        cfg = load_config(cfg_path)
        assert [(p.id, p.alias) for p in cfg.midi.patches] == [
            (1, "A"),
            (3, "C"),
        ]

    def test_save_unknown_patch_id_is_no_op(self, live_server) -> None:
        """Saving an id with no matching patch (e.g. a concurrent
        delete) quietly renders the section without modifying the
        config."""
        _, base, cfg_path = live_server
        cfg = load_config(cfg_path)
        cfg.midi = MidiConfig(patches=[MidiPatch(id=1, alias="A")])
        save_config(cfg, cfg_path)
        status, _ = _post_form(
            base,
            "/section/midi/patches/99",
            {
                "alias": "Should not appear",
                "device": "",
            },
        )
        assert status == 200
        cfg = load_config(cfg_path)
        assert [(p.id, p.alias) for p in cfg.midi.patches] == [(1, "A")]


# ---------------------------------------------------------------------------
# Virtual Faders save
# ---------------------------------------------------------------------------


class TestSaveFaders:
    def _post_default_eight_rows(
        self,
        base: str,
        **overrides: Any,
    ) -> tuple[int, str]:
        """Helper: post a save with eight well-formed rows. Tests
        override only the fields they're asserting on by passing
        per-field lists in ``overrides``."""
        defaults: dict[str, Any] = {
            "fader_name": [""] * 8,
            "fader_default": ["0"] * 8,
            "fader_source_kind": [""] * 8,
            "fader_source_patch": ["0"] * 8,
            "fader_source_midi_type": ["control_change"] * 8,
            "fader_source_midi_channel": ["0"] * 8,
            "fader_source_midi_number": ["0"] * 8,
        }
        defaults.update(overrides)
        return _post_form(base, "/section/midi/faders", defaults)

    def test_save_coerces_stale_gamepad_source_kind(
        self,
        live_server,
    ) -> None:
        """All eight faders are uniformly MIDI / unmapped now (the
        former 'fader 1 = gamepad' allocation rule is gone). MIDI is
        legal on any fader including fader 1; a stale ``gamepad`` kind
        coerces to ``""`` via ``VirtualFaderConfig.__post_init__``."""
        _, base, cfg_path = live_server
        # MIDI on fader 1 is now legal; gamepad on fader 3 coerces away.
        kinds = ["midi", "midi", "gamepad"] + [""] * 5
        status, _ = self._post_default_eight_rows(
            base,
            fader_source_kind=kinds,
        )
        assert status == 200
        cfg = load_config(cfg_path)
        assert cfg.virtual_faders.faders[0].source_kind == "midi"
        assert cfg.virtual_faders.faders[1].source_kind == "midi"
        assert cfg.virtual_faders.faders[2].source_kind == ""

    def test_save_replaces_full_fader_list(self, live_server) -> None:
        _, base, cfg_path = live_server
        status, _ = self._post_default_eight_rows(
            base,
            fader_name=["Master", "Aux 1", "", "", "", "", "", ""],
            fader_default=["0.5", "0.25", "0", "0", "0", "0", "0", "0"],
            fader_show=["1", "3"],  # checkbox group
        )
        assert status == 200
        cfg = load_config(cfg_path)
        assert cfg.virtual_faders.faders[0].name == "Master"
        assert cfg.virtual_faders.faders[0].default_value == 0.5
        assert cfg.virtual_faders.faders[0].show_on_display is True
        assert cfg.virtual_faders.faders[1].name == "Aux 1"
        assert cfg.virtual_faders.faders[1].show_on_display is False
        assert cfg.virtual_faders.faders[2].show_on_display is True

    def test_save_round_trips_per_fader_color(self, live_server) -> None:
        """The batch save threads each fader's colour through the
        parallel ``fader_color`` array, so a full-list replace persists
        operator-assigned colours instead of resetting them to default."""
        _, base, cfg_path = live_server
        colors = ["#ff0000", "#00ff00"] + ["#000000"] * 6
        status, _ = self._post_default_eight_rows(base, fader_color=colors)
        assert status == 200
        cfg = load_config(cfg_path)
        assert cfg.virtual_faders.faders[0].color == "#ff0000"
        assert cfg.virtual_faders.faders[1].color == "#00ff00"
        assert cfg.virtual_faders.faders[2].color == "#000000"

    def test_save_without_color_array_defaults_to_black(
        self,
        live_server,
    ) -> None:
        """A batch POST that omits ``fader_color`` entirely (the default
        helper) coerces each fader to the ``#000000`` default rather than
        crashing on the missing parallel array."""
        _, base, cfg_path = live_server
        status, _ = self._post_default_eight_rows(base)
        assert status == 200
        cfg = load_config(cfg_path)
        assert all(f.color == "#000000" for f in cfg.virtual_faders.faders)

    def test_save_coerces_bad_color_to_default(self, live_server) -> None:
        """A crafted colour that isn't a ``#rrggbb`` hex coerces back to
        the default via ``VirtualFaderConfig.__post_init__`` – a crafted
        batch POST can't persist garbage."""
        _, base, cfg_path = live_server
        colors = ["not-a-color"] + ["#000000"] * 7
        status, _ = self._post_default_eight_rows(base, fader_color=colors)
        assert status == 200
        cfg = load_config(cfg_path)
        assert cfg.virtual_faders.faders[0].color == "#000000"

    def test_per_fader_save_only_touches_target(self, live_server) -> None:
        _, base, cfg_path = live_server
        # Seed two faders with distinct names so we can detect
        # collateral damage on the un-targeted one.
        cfg = load_config(cfg_path)
        cfg.virtual_faders = VirtualFadersConfig(
            faders=[
                VirtualFaderConfig(name="One", default_value=0.1),
                VirtualFaderConfig(name="Two", default_value=0.2),
            ]
        )
        save_config(cfg, cfg_path)
        # Save fader 2 with new values; fader 1 must stay as "One".
        status, _ = _post_form(
            base,
            "/section/midi/faders/2",
            {
                "name": "Updated Two",
                "default_value": "0.66",
                "show_on_display": "on",
                "source_kind": "",
                "source_patch": "0",
                "source_midi_type": "control_change",
                "source_midi_channel": "0",
                "source_midi_number": "0",
            },
        )
        assert status == 200
        cfg = load_config(cfg_path)
        # Fader 1 untouched.
        assert cfg.virtual_faders.faders[0].name == "One"
        assert cfg.virtual_faders.faders[0].default_value == 0.1
        # Fader 2 reflects the save.
        assert cfg.virtual_faders.faders[1].name == "Updated Two"
        assert cfg.virtual_faders.faders[1].default_value == 0.66
        assert cfg.virtual_faders.faders[1].show_on_display is True

    def test_per_fader_save_round_trips_color(self, live_server) -> None:
        """The per-fader save persists the operator-assigned strip colour
        posted by the detail form's hidden ``color`` input."""
        _, base, cfg_path = live_server
        status, _ = _post_form(
            base,
            "/section/midi/faders/1",
            {
                "name": "Tinted",
                "default_value": "0",
                "source_kind": "",
                "source_patch": "0",
                "source_midi_type": "control_change",
                "source_midi_channel": "0",
                "source_midi_number": "0",
                "color": "#abcdef",
            },
        )
        assert status == 200
        cfg = load_config(cfg_path)
        assert cfg.virtual_faders.faders[0].color == "#abcdef"

    def test_per_fader_save_accepts_midi_on_fader_one(
        self,
        live_server,
    ) -> None:
        """Fader 1 is a normal MIDI fader now (the gamepad no longer
        owns it), so a per-fader save with ``source_kind="midi"`` on
        fader 1 persists intact."""
        _, base, cfg_path = live_server
        status, _ = _post_form(
            base,
            "/section/midi/faders/1",
            {
                "name": "Master",
                "default_value": "0",
                "source_kind": "midi",
                "source_patch": "0",
                "source_midi_type": "control_change",
                "source_midi_channel": "0",
                "source_midi_number": "7",
            },
        )
        assert status == 200
        cfg = load_config(cfg_path)
        assert cfg.virtual_faders.faders[0].source_kind == "midi"
        assert cfg.virtual_faders.faders[0].source_midi_number == 7

    def test_per_fader_save_normalises_kind_for_fader_two(
        self,
        live_server,
    ) -> None:
        """Inverse direction: ``source_kind="gamepad"`` on a non-
        gamepad slot (fader 2..8) is reset to ``""``."""
        _, base, cfg_path = live_server
        status, _ = _post_form(
            base,
            "/section/midi/faders/2",
            {
                "name": "Crafted",
                "default_value": "0",
                "source_kind": "gamepad",
                "source_patch": "0",
                "source_midi_type": "control_change",
                "source_midi_channel": "0",
                "source_midi_number": "0",
            },
        )
        assert status == 200
        cfg = load_config(cfg_path)
        assert cfg.virtual_faders.faders[1].source_kind == ""

    def test_per_fader_save_out_of_range_index_renders_without_change(
        self,
        live_server,
    ) -> None:
        _, base, cfg_path = live_server
        cfg = load_config(cfg_path)
        original_name = cfg.virtual_faders.faders[0].name
        status, _ = _post_form(
            base,
            "/section/midi/faders/99",
            {
                "name": "Should not appear",
            },
        )
        assert status == 200
        cfg = load_config(cfg_path)
        assert cfg.virtual_faders.faders[0].name == original_name


class TestFaderDetailGet:
    """``GET /section/midi/faders/<idx>/detail`` returns the
    editable detail-form partial for one fader. The mixer-style UI
    swaps it into the page when the operator clicks a strip."""

    def test_renders_form_for_fader(self, live_server) -> None:
        _, base, cfg_path = live_server
        cfg = load_config(cfg_path)
        cfg.virtual_faders = VirtualFadersConfig(
            faders=[
                VirtualFaderConfig(name="Master", default_value=0.5),
            ]
        )
        save_config(cfg, cfg_path)
        status, body = _get(base, "/section/midi/faders/1/detail")
        assert status == 200
        # The form posts to the per-fader endpoint, so a save
        # round-trips through the new per-fader save path.
        assert 'hx-post="/section/midi/faders/1"' in body
        # Operator's name is pre-filled.
        assert 'value="Master"' in body
        # Default-value input pre-filled.
        assert 'value="0.5"' in body

    def test_fader_1_shows_midi_source_fields(self, live_server) -> None:
        """Fader 1 is a normal MIDI fader now (the gamepad no longer
        owns it), so its detail form renders the MIDI source dropdown +
        sub-fields just like faders 2..8 – no gamepad notice."""
        _, base, _ = live_server
        status, body = _get(base, "/section/midi/faders/1/detail")
        assert status == 200
        assert "driven by the gamepad stick" not in body
        assert "data-midi-source-kind-select" in body
        assert "data-midi-source-detail" in body

    def test_fader_2_shows_midi_source_fields(self, live_server) -> None:
        """Faders 2..8 accept MIDI sources, so the detail form
        renders the source dropdown + sub-fields."""
        _, base, _ = live_server
        status, body = _get(base, "/section/midi/faders/2/detail")
        assert status == 200
        assert "data-midi-source-kind-select" in body
        assert "data-midi-source-detail" in body

    def test_out_of_range_falls_back_to_fader_1(
        self,
        live_server,
    ) -> None:
        _, base, _ = live_server
        status, body = _get(base, "/section/midi/faders/99/detail")
        assert status == 200
        assert 'hx-post="/section/midi/faders/1"' in body

    def test_save_round_trips_midi_source_fields_per_fader(
        self,
        live_server,
    ) -> None:
        """Per-fader save round-trips the MIDI source fields. The
        mixer-style UI's per-fader endpoint reads the patch-scoped
        source fields (``source_patch`` is a patch-id int, plus
        ``source_midi_type`` / etc.) so the dispatcher's downstream
        logic resolves the patch by id."""
        _, base, cfg_path = live_server
        # Seed a patch so the fader's source can reference it by id.
        cfg = load_config(cfg_path)
        cfg.midi = MidiConfig(patches=[MidiPatch(id=1, alias="Workspace 1")])
        save_config(cfg, cfg_path)
        status, _ = _post_form(
            base,
            "/section/midi/faders/2",
            {
                "name": "Master",
                "default_value": "0.5",
                "source_kind": "midi",
                "source_patch": "1",
                "source_midi_type": "control_change",
                "source_midi_channel": "3",
                "source_midi_number": "7",
            },
        )
        assert status == 200
        cfg = load_config(cfg_path)
        f2 = cfg.virtual_faders.faders[1]
        assert f2.source_kind == "midi"
        assert f2.source_patch == 1
        assert f2.source_midi_type == "control_change"
        assert f2.source_midi_channel == 3
        assert f2.source_midi_number == 7


# ---------------------------------------------------------------------------
# Fader Learn – arm / poll capture flow
# ---------------------------------------------------------------------------


class TestFaderLearn:
    def test_detail_renders_learn_button_for_midi_fader(
        self,
        live_server,
    ) -> None:
        """Faders 2..8 expose a Learn button + status slot inside the
        MIDI source detail block, with OOB-target ids on each source
        field."""
        _, base, _ = live_server
        status, body = _get(base, "/section/midi/faders/2/detail")
        assert status == 200
        assert 'id="midi-fader-learn-status-2"' in body
        assert 'hx-post="/section/midi/faders/2/learn/arm"' in body
        assert 'id="midi-fader-source-patch-2"' in body
        assert 'id="midi-fader-source-type-2"' in body
        assert 'id="midi-fader-source-channel-2"' in body
        assert 'id="midi-fader-source-number-2"' in body

    def test_arm_with_broker_renders_listening_and_namespaced_row(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        """Arming a fader's Learn returns the ``armed`` partial (poll
        driver present) and arms the shared broker under the
        ``fader:<idx>`` row id."""
        with _live_server_with_midi_providers(
            tmp_path,
            monkeypatch,
        ) as (server, base, _):
            status, body = _post_form(
                base,
                "/section/midi/faders/2/learn/arm",
                {},
            )
            assert status == 200
            assert "Listening for MIDI" in body
            assert "/section/midi/faders/2/learn/poll" in body
            assert server._test_capture_arm_calls == ["fader:2"]

    def test_arm_without_broker_reports_unavailable(
        self,
        live_server,
    ) -> None:
        """No MIDI substrate wired → the Learn flow shows the
        unavailable banner instead of a stack trace."""
        _, base, _ = live_server
        status, body = _post_form(
            base,
            "/section/midi/faders/2/learn/arm",
            {},
        )
        assert status == 200
        assert "MIDI subsystem not running" in body

    def test_poll_captured_emits_oob_fragments(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        """A captured event re-renders the fader's source fields via
        OOB swaps with the matched message pre-filled."""
        with _live_server_with_midi_providers(
            tmp_path,
            monkeypatch,
            capture_state={
                "status": "captured",
                "patch_id": 0,
                "type": "control_change",
                "channel": 5,
                "number": 7,
                "value": 100,
            },
        ) as (_, base, _cfg):
            status, body = _get(
                base,
                "/section/midi/faders/2/learn/poll",
            )
            assert status == 200
            assert "Captured" in body
            # OOB fragments target the fader's source fields by id.
            assert 'id="midi-fader-source-type-2"' in body
            assert 'hx-swap-oob="true"' in body
            # Channel + CC number land in their inputs.
            assert 'value="5"' in body
            assert 'value="7"' in body

    def test_poll_captured_unsupported_type_round_trips(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        """A captured type that isn't a valid fader source (operator hits a
        pad → ``note_on``) surfaces an explicit ``(unsupported)`` option that
        is selected, so the value round-trips instead of the <select>
        silently defaulting to the first option (control_change)."""
        with _live_server_with_midi_providers(
            tmp_path,
            monkeypatch,
            capture_state={
                "status": "captured",
                "patch_id": 0,
                "type": "note_on",
                "channel": 1,
                "number": 60,
                "value": 127,
            },
        ) as (_, base, _cfg):
            status, body = _get(
                base,
                "/section/midi/faders/2/learn/poll",
            )
            assert status == 200
            assert "Note On (unsupported)" in body
            assert '<option value="note_on" selected>' in body
            # The real types are still offered (unselected) so the operator
            # can correct the mapping.
            assert 'value="control_change"' in body

    def test_fader_1_has_learn(self, live_server) -> None:
        """Fader 1 is a normal MIDI fader now, so its Learn endpoints
        resolve (200) and the detail form includes the Learn button –
        same as faders 2..8."""
        _, base, _ = live_server
        arm_status, _ = _post_form(
            base,
            "/section/midi/faders/1/learn/arm",
            {},
        )
        poll_status, _ = _get(base, "/section/midi/faders/1/learn/poll")
        assert arm_status == 200
        assert poll_status == 200
        _, detail = _get(base, "/section/midi/faders/1/detail")
        assert "learn/arm" in detail

    def test_out_of_range_fader_learn_404s(self, live_server) -> None:
        _, base, _ = live_server
        arm_status, _ = _post_form(
            base,
            "/section/midi/faders/99/learn/arm",
            {},
        )
        poll_status, _ = _get(base, "/section/midi/faders/99/learn/poll")
        assert arm_status == 404
        assert poll_status == 404


# ---------------------------------------------------------------------------
# Live-value poll endpoint
# ---------------------------------------------------------------------------


class TestFaderValuesPoll:
    def test_returns_oob_swap_snippets(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        with _live_server_with_midi_providers(
            tmp_path,
            monkeypatch,
            fader_values=[
                {
                    "index": 1,
                    "name": "Master",
                    "value": 0.42,
                    "picked_up": True,
                    "show_on_display": True,
                },
                {
                    "index": 2,
                    "name": "Aux",
                    "value": 0.17,
                    "picked_up": False,
                    "show_on_display": False,
                },
            ],
        ) as (_, base, _cfg):
            status, body = _get(base, "/section/midi/faders/values")
            assert status == 200
        # The mixer-style UI uses three OOB swaps per strip: the
        # fill bar's height, the handle's bottom position, and the
        # numeric value text. Each is id-keyed so HTMX targets the
        # specific element without disturbing the click handler /
        # selection state on the strip wrapper.
        assert 'id="midi-fader-strip-fill-1"' in body
        assert 'style="height:42%"' in body
        assert 'id="midi-fader-strip-handle-1"' in body
        assert 'style="bottom:42%"' in body
        assert 'id="midi-fader-strip-value-1"' in body
        assert "0.42" in body
        # Fader 2 is not picked up – value carries the inline
        # annotation so the operator can see at a glance why their
        # hardware fader isn't moving the virtual one yet.
        assert 'id="midi-fader-strip-value-2"' in body
        assert "0.17" in body
        assert "not picked up" in body
        # Three OOB swaps per fader × two faders = six total.
        assert body.count('hx-swap-oob="true"') == 6

    def test_returns_empty_body_when_no_provider_wired(
        self,
        live_server,
    ) -> None:
        """The default ``live_server`` doesn't wire a fader-values
        provider – the route returns an empty body so the existing
        cells stay unchanged. Pre-fix this would have 500'd."""
        _, base, _ = live_server
        status, body = _get(base, "/section/midi/faders/values")
        assert status == 200
        assert body.strip() == ""


# ---------------------------------------------------------------------------
# Marker Faders – read-only per-controlled-marker visualization
# ---------------------------------------------------------------------------


class TestMarkerFaderSectionRender:
    def test_empty_state_when_no_controlled_markers(
        self,
        live_server,
    ) -> None:
        """With no marker-fader provider wired (and no controlled
        markers), the Marker Faders group renders its empty-state note
        rather than any strips."""
        _, base, _ = live_server
        status, body = _get(base, "/section/midi")
        assert status == 200
        assert "Marker Faders" in body
        assert "No controlled markers yet" in body
        # No strip targets exist in the empty state.
        assert 'id="marker-fader-strip-fill-' not in body

    def test_renders_one_strip_per_controlled_marker(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        """Each provider entry renders a read-only strip with id-keyed
        fill / handle / value targets so the 100 ms poll can update it.
        The strip label falls back to ``M<id>`` when the marker has no
        catalog name, and uses the catalog name when present."""
        with _live_server_with_midi_providers(
            tmp_path,
            monkeypatch,
            marker_fader_values=[
                {"marker_id": 3, "name": "", "value": 0.25},
                {"marker_id": 7, "name": "Diva", "value": 0.8},
            ],
        ) as (_, base, _cfg):
            status, body = _get(base, "/section/midi")
            assert status == 200
            assert "Marker Faders" in body
            assert "No controlled markers yet" not in body
            # Strip targets keyed by marker id (not a 1..8 index).
            assert 'id="marker-fader-strip-fill-3"' in body
            assert 'id="marker-fader-strip-handle-3"' in body
            assert 'id="marker-fader-strip-value-3"' in body
            assert 'id="marker-fader-strip-fill-7"' in body
            # Name fallback for the unnamed marker, catalog name for 7.
            assert "M3" in body
            assert "Diva" in body
            # Read-only modifier (no click handler like Virtual Faders).
            assert "midi-fader-strip--readonly" in body
            assert 'data-marker-id="3"' in body
            # Initial fill reflects the seed value (25%).
            assert 'style="height:25%"' in body


class TestMarkerFaderValuesPoll:
    def test_returns_oob_swap_snippets(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        """The live-poll endpoint returns three OOB swaps per marker
        (fill height, handle bottom, numeric value), id-keyed by marker
        id. No 'not picked up' annotation – marker faders are gamepad-
        driven with no MIDI pickup gate."""
        with _live_server_with_midi_providers(
            tmp_path,
            monkeypatch,
            marker_fader_values=[
                {"marker_id": 3, "name": "", "value": 0.42},
                {"marker_id": 7, "name": "Diva", "value": 0.1},
            ],
        ) as (_, base, _cfg):
            status, body = _get(
                base,
                "/section/midi/marker-faders/values",
            )
            assert status == 200
        assert 'id="marker-fader-strip-fill-3"' in body
        assert 'style="height:42%"' in body
        assert 'id="marker-fader-strip-handle-3"' in body
        assert 'style="bottom:42%"' in body
        assert 'id="marker-fader-strip-value-3"' in body
        assert "0.42" in body
        assert 'id="marker-fader-strip-value-7"' in body
        assert "0.10" in body
        assert "not picked up" not in body
        # Three OOB swaps per marker × two markers = six total.
        assert body.count('hx-swap-oob="true"') == 6

    def test_returns_empty_body_when_no_provider_wired(
        self,
        live_server,
    ) -> None:
        """The default ``live_server`` doesn't wire a marker-fader
        values provider – the route returns an empty body so the
        existing cells stay unchanged rather than 500'ing."""
        _, base, _ = live_server
        status, body = _get(base, "/section/midi/marker-faders/values")
        assert status == 200
        assert body.strip() == ""
