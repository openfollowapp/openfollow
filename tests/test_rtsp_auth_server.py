# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Checks for scripts/hw_validation/rtsp_auth_server.py.

The bench tool that makes the RTSP credential path testable at all - no camera
here demands a login. Its own failure modes are quiet ones: an encoder that
isn't installed only surfaces when the first client triggers media
construction, and an auth block that silently grants access would let the
credential test pass while proving nothing.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

pytestmark = pytest.mark.unit


def _load() -> ModuleType:
    source = inspect.getsourcefile(_load)
    assert source, "Could not resolve current test source path"
    script = Path(source).resolve().parents[1] / "scripts" / "hw_validation" / "rtsp_auth_server.py"
    spec = importlib.util.spec_from_file_location("rtsp_auth_server", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


srv = _load()


class TestEncoderSelection:
    """``x264enc`` is ``gst-plugins-ugly``, which the appliance does not ship.

    Hard-coding it produced a server that started cleanly and then failed when
    a client actually connected - the worst shape for a diagnostic tool.
    """

    def test_prefers_the_first_installed_encoder_in_order(self) -> None:
        assert srv.pick_encoder(lambda name: True) == srv._ENCODER_PREFERENCE[0]

    def test_falls_through_to_what_is_actually_installed(self) -> None:
        assert srv.pick_encoder(lambda name: name == "avenc_h264") == "avenc_h264"

    def test_reports_nothing_usable_rather_than_building_a_broken_pipeline(self) -> None:
        assert srv.pick_encoder(lambda name: False) is None

    def test_an_explicit_choice_is_honoured(self) -> None:
        assert srv.pick_encoder(lambda name: True, preferred="openh264enc") == "openh264enc"

    def test_an_explicit_choice_that_is_missing_is_refused_not_substituted(self) -> None:
        """Silently swapping in another encoder would hide the operator's
        mistake and change what the bench run is testing."""
        assert srv.pick_encoder(lambda name: name == "x264enc", preferred="openh264enc") is None

    def test_the_appliance_plugin_sets_are_covered(self) -> None:
        """gst-libav and gst-plugins-bad are what a station actually has."""
        assert "avenc_h264" in srv._ENCODER_PREFERENCE
        assert "openh264enc" in srv._ENCODER_PREFERENCE


class TestArgumentParsing:
    def test_defaults_describe_a_credentialed_stream(self) -> None:
        args = srv.build_parser().parse_args([])
        assert args.port == srv.DEFAULT_PORT
        assert args.path == srv.DEFAULT_PATH
        assert args.user == srv.DEFAULT_USER
        assert args.password == srv.DEFAULT_PASSWORD
        assert args.no_auth is False

    def test_a_password_full_of_url_hostile_characters_survives_parsing(self) -> None:
        """The reason the fields exist: ``@``, ``:`` and ``/`` cannot go into a
        URL unencoded, so the bench password must be able to contain them."""
        args = srv.build_parser().parse_args(["--password", "p@ss:word/1"])
        assert args.password == "p@ss:word/1"

    def test_auth_can_be_disabled_for_a_control_run(self) -> None:
        assert srv.build_parser().parse_args(["--no-auth"]).no_auth is True


class _FakeMountPoints:
    def __init__(self) -> None:
        self.added: list[tuple[str, Any]] = []

    def add_factory(self, path: str, factory: Any) -> None:
        self.added.append((path, factory))


class _FakeFactory:
    def __init__(self) -> None:
        self.launch: str | None = None
        self.shared: bool | None = None
        self.permissions: Any = None

    def set_launch(self, launch: str) -> None:
        self.launch = launch

    def set_shared(self, shared: bool) -> None:
        self.shared = shared

    def set_permissions(self, permissions: Any) -> None:
        self.permissions = permissions


class _FakeAuth:
    def __init__(self) -> None:
        self.basics: list[tuple[str, Any]] = []

    def add_basic(self, basic: str, token: Any) -> None:
        self.basics.append((basic, token))

    @staticmethod
    def make_basic(user: str, password: str) -> str:
        return f"basic:{user}:{password}"


class _FakePermissions:
    def __init__(self) -> None:
        self.grants: list[tuple[str, str, bool]] = []

    def add_permission_for_role(self, role: str, permission: str, allowed: bool) -> None:
        self.grants.append((role, permission, allowed))


class _FakeServer:
    attach_result = 1

    def __init__(self) -> None:
        self.service: str | None = None
        self.auth: Any = None
        self.mounts = _FakeMountPoints()

    def set_service(self, service: str) -> None:
        self.service = service

    def set_auth(self, auth: Any) -> None:
        self.auth = auth

    def get_mount_points(self) -> _FakeMountPoints:
        return self.mounts

    def attach(self, context: Any) -> int:
        return type(self).attach_result


def _install_fake_gi(monkeypatch: pytest.MonkeyPatch, *, installed: set[str], attach: int = 1) -> _FakeServer:
    """Stand in for the GStreamer stack so the wiring can be asserted."""
    server = _FakeServer()
    type(server).attach_result = attach

    gst = SimpleNamespace(
        init=lambda argv: None,
        ElementFactory=SimpleNamespace(find=lambda name: object() if name in installed else None),
    )
    rtsp = SimpleNamespace(
        RTSPServer=lambda: server,
        RTSPMediaFactory=_FakeFactory,
        RTSPAuth=_FakeAuth,
        RTSPToken=lambda: SimpleNamespace(set_string=lambda key, value: None),
        RTSPPermissions=_FakePermissions,
    )

    class _Loop:
        def run(self) -> None:
            raise KeyboardInterrupt  # return from main() without blocking

    repository = SimpleNamespace(Gst=gst, GstRtspServer=rtsp, GLib=SimpleNamespace(MainLoop=_Loop))
    monkeypatch.setitem(sys.modules, "gi", SimpleNamespace(require_version=lambda *a: None, repository=repository))
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    return server


class TestServerSetup:
    def test_auth_is_wired_and_the_factory_is_gated_on_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without an explicit permission grant the factory stays world-readable,
        so an unauthenticated client would be served and the credential test
        would pass while proving nothing."""
        server = _install_fake_gi(monkeypatch, installed={"x264enc"})
        assert srv.main(["--user", "operator", "--password", "p@ss:word/1"]) == 0

        assert server.auth is not None
        assert [basic for basic, _token in server.auth.basics] == ["basic:operator:p@ss:word/1"]
        path, factory = server.mounts.added[0]
        assert path == srv.DEFAULT_PATH
        grants = {(role, perm) for role, perm, allowed in factory.permissions.grants if allowed}
        assert grants == {("operator", "media.factory.access"), ("operator", "media.factory.construct")}

    def test_no_auth_leaves_the_factory_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = _install_fake_gi(monkeypatch, installed={"x264enc"})
        assert srv.main(["--no-auth"]) == 0
        assert server.auth is None
        _path, factory = server.mounts.added[0]
        assert factory.permissions is None

    def test_the_selected_encoder_reaches_the_pipeline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = _install_fake_gi(monkeypatch, installed={"avenc_h264"})
        assert srv.main([]) == 0
        _path, factory = server.mounts.added[0]
        assert "avenc_h264" in factory.launch
        assert "x264enc" not in factory.launch
        assert "rtph264pay name=pay0" in factory.launch

    def test_missing_encoder_exits_nonzero_instead_of_serving(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = _install_fake_gi(monkeypatch, installed=set())
        assert srv.main([]) == 1
        assert server.mounts.added == []

    def test_a_taken_port_exits_nonzero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_gi(monkeypatch, installed={"x264enc"}, attach=0)
        assert srv.main([]) == 1

    def test_list_encoders_reports_without_starting_a_server(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = _install_fake_gi(monkeypatch, installed={"avenc_h264"})
        assert srv.main(["--list-encoders"]) == 0
        assert server.mounts.added == []
