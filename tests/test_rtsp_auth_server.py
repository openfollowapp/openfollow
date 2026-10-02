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

import gc
import importlib.util
import inspect
import re
import sys
import weakref
from collections.abc import Collection
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

_FICTITIOUS_ENCODER = "benchonlyh264enc"  # no GStreamer element has this name


def _usable(name: str) -> Any:
    return srv.EncoderSettings({})


class TestEncoderSelection:
    """``x264enc`` is ``gst-plugins-ugly``, which the appliance does not ship.

    Hard-coding it produced a server that started cleanly and then failed when
    a client actually connected - the worst shape for a diagnostic tool.
    """

    def test_prefers_the_first_usable_encoder_in_order(self) -> None:
        name, _settings = srv.pick_encoder(None, set(srv._ENCODER_PREFERENCE), lambda name: True, _usable)
        assert name == srv._ENCODER_PREFERENCE[0]

    def test_falls_through_to_what_is_actually_installed(self) -> None:
        """``openh264enc`` is the only H.264 encoder a station has."""
        name, _settings = srv.pick_encoder(None, {"openh264enc"}, lambda name: True, _usable)
        assert name == "openh264enc"

    def test_an_explicit_choice_is_honoured(self) -> None:
        h264 = set(srv._ENCODER_PREFERENCE)
        name, _settings = srv.pick_encoder("openh264enc", h264, lambda name: True, _usable)
        assert name == "openh264enc"

    def test_an_explicit_choice_that_is_missing_is_refused_not_substituted(self) -> None:
        """Silently swapping in another encoder would hide the operator's
        mistake and change what the bench run is testing."""
        with pytest.raises(srv.EncoderError):
            srv.pick_encoder("openh264enc", {"x264enc"}, lambda name: name == "x264enc", _usable)


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


class _FakeElement:
    def __init__(self, properties: Collection[str]) -> None:
        self.properties = frozenset(properties)

    def find_property(self, name: str) -> object | None:
        if not isinstance(name, str):
            raise TypeError("Argument 1 does not allow None as a value")  # as PyGObject does
        return object() if name in self.properties else None


class _FakeElementFactory:
    def __init__(self, name: str) -> None:
        self.name = name

    def get_name(self) -> str:
        return self.name


class _FakeServer:
    attach_result = 1

    def __init__(self) -> None:
        self.service: str | None = None
        self.auth: Any = None
        self.mounts = _FakeMountPoints()
        self.elements: list[weakref.ref[_FakeElement]] = []
        self.elements_alive_while_serving: int | None = None

    def set_service(self, service: str) -> None:
        self.service = service

    def set_auth(self, auth: Any) -> None:
        self.auth = auth

    def get_mount_points(self) -> _FakeMountPoints:
        return self.mounts

    def attach(self, context: Any) -> int:
        return type(self).attach_result


def _install_fake_gi(
    monkeypatch: pytest.MonkeyPatch,
    *,
    installed: Collection[str],
    h264: Collection[str] | None = None,
    properties: Collection[str] = (),
    uncreatable: Collection[str] = (),
    attach: int = 1,
) -> _FakeServer:
    """Stand in for the GStreamer stack so the wiring can be asserted.

    Every *installed* element is an H.264 encoder unless *h264* says otherwise,
    and every one that can be created exposes *properties*.
    """
    server = _FakeServer()
    type(server).attach_result = attach
    h264_names = set(installed if h264 is None else h264)

    def make(name: str, element_name: str | None) -> _FakeElement | None:
        if name not in installed or name in uncreatable:
            return None
        element = _FakeElement(properties)
        server.elements.append(weakref.ref(element))
        return element

    def list_filter(factories: list[_FakeElementFactory], *args: Any) -> list[_FakeElementFactory]:
        return [factory for factory in factories if factory.get_name() in h264_names]

    gst = SimpleNamespace(
        init=lambda argv: None,
        ELEMENT_FACTORY_TYPE_VIDEO_ENCODER=object(),
        Rank=SimpleNamespace(NONE=0),
        PadDirection=SimpleNamespace(SRC=object()),
        Caps=SimpleNamespace(from_string=lambda caps: caps),
        ElementFactory=SimpleNamespace(
            find=lambda name: _FakeElementFactory(name) if name in installed else None,
            make=make,
            list_get_elements=lambda kind, rank: [_FakeElementFactory(name) for name in installed],
            list_filter=list_filter,
        ),
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
            gc.collect()
            server.elements_alive_while_serving = sum(ref() is not None for ref in server.elements)
            raise KeyboardInterrupt  # return from main() without blocking

    repository = SimpleNamespace(Gst=gst, GstRtspServer=rtsp, GLib=SimpleNamespace(MainLoop=_Loop))
    monkeypatch.setitem(sys.modules, "gi", SimpleNamespace(require_version=lambda *a: None, repository=repository))
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    return server


def _launch(server: _FakeServer) -> str:
    _path, factory = server.mounts.added[0]
    assert isinstance(factory.launch, str)
    return factory.launch


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
        server = _install_fake_gi(monkeypatch, installed={"openh264enc"})
        assert srv.main([]) == 0
        launch = _launch(server)
        assert "openh264enc" in launch
        assert "x264enc" not in launch
        assert "rtph264pay name=pay0" in launch

    def test_no_probe_element_is_held_while_serving(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The media factory builds its own encoder per pipeline; the probe's
        copy has no use once the settings are known."""
        server = _install_fake_gi(monkeypatch, installed={"openh264enc"}, properties={"gop-size"})
        assert srv.main([]) == 0
        assert server.elements, "the encoder was never probed"
        assert server.elements_alive_while_serving == 0

    def test_a_taken_port_exits_nonzero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_gi(monkeypatch, installed={"x264enc"}, attach=0)
        assert srv.main([]) == 1


class TestEncoderSettings:
    """A property the encoder lacks fails the media pipeline, and every client
    gets a 503: openh264enc, the only encoder on a Pi, has no key-int-max."""

    @pytest.mark.parametrize("keyframe", ["key-int-max", "max-keyframe-interval", "gop-size"])
    def test_the_keyframe_interval_uses_the_property_the_encoder_has(
        self, monkeypatch: pytest.MonkeyPatch, keyframe: str
    ) -> None:
        server = _install_fake_gi(monkeypatch, installed={"vtenc_h264_hw"}, properties={keyframe})
        assert srv.main(["--encoder", "vtenc_h264_hw", "--fps", "25"]) == 0
        assert f" {keyframe}=25 " in _launch(server)

    @pytest.mark.parametrize(
        ("properties", "expected"),
        [
            ({"key-int-max", "gop-size"}, "key-int-max"),
            ({"max-keyframe-interval", "gop-size"}, "max-keyframe-interval"),
            ({"key-int-max", "max-keyframe-interval", "gop-size"}, "key-int-max"),
        ],
    )
    def test_only_the_first_keyframe_property_is_set(
        self, monkeypatch: pytest.MonkeyPatch, properties: set[str], expected: str
    ) -> None:
        """Two keyframe limits on one element would fight over the interval."""
        server = _install_fake_gi(monkeypatch, installed={_FICTITIOUS_ENCODER}, properties=properties)
        assert srv.main(["--encoder", _FICTITIOUS_ENCODER, "--fps", "25"]) == 0
        launch = _launch(server)
        assert re.findall(r" (key-int-max|max-keyframe-interval|gop-size)=", launch) == [expected]

    def test_an_encoder_with_no_known_keyframe_property_still_serves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Any installed H.264 encoder can be forced, including one this script
        has no settings for; it runs on its own keyframe default."""
        server = _install_fake_gi(monkeypatch, installed={_FICTITIOUS_ENCODER}, properties={"bitrate"})
        assert srv.main(["--encoder", _FICTITIOUS_ENCODER]) == 0
        launch = _launch(server)
        assert _FICTITIOUS_ENCODER in launch
        assert not any(f" {name}=" in launch for name in ("key-int-max", "max-keyframe-interval", "gop-size"))

    @pytest.mark.parametrize("encoder", ["vtenc_h264", "vtenc_h264_hw", "avenc_h264_videotoolbox", _FICTITIOUS_ENCODER])
    def test_low_latency_switches_follow_the_property_not_the_name(
        self, monkeypatch: pytest.MonkeyPatch, encoder: str
    ) -> None:
        """Forcing ``vtenc_h264_hw`` must not lose what ``vtenc_h264`` gets."""
        server = _install_fake_gi(monkeypatch, installed={encoder}, properties={"realtime", "allow-frame-reordering"})
        assert srv.main(["--encoder", encoder]) == 0
        launch = _launch(server)
        assert " realtime=true " in launch
        assert " allow-frame-reordering=false " in launch

    # Units from each element's own property description: x264enc "kbit/sec",
    # vtenc "kbps", avenc_h264_videotoolbox "bits/s", openh264enc "bits per second".
    @pytest.mark.parametrize(
        ("encoder", "bits_per_unit"),
        [
            ("x264enc", 1000),
            ("vtenc_h264", 1000),
            ("vtenc_h264_hw", 1000),
            ("avenc_h264_videotoolbox", 1),
            ("openh264enc", 1),
        ],
    )
    def test_the_bitrate_is_about_4_mbit_s_in_the_elements_own_unit(
        self, monkeypatch: pytest.MonkeyPatch, encoder: str, bits_per_unit: int
    ) -> None:
        """openh264enc's default target is 128 kbit/s, far below what a 720p30 camera sends."""
        server = _install_fake_gi(monkeypatch, installed={encoder}, properties={"bitrate"})
        assert srv.main(["--encoder", encoder]) == 0
        bitrate = re.search(r" bitrate=(\d+) ", _launch(server))
        assert bitrate is not None
        assert 3_000_000 <= int(bitrate[1]) * bits_per_unit <= 5_000_000

    def test_a_setting_the_encoder_lacks_is_left_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = _install_fake_gi(monkeypatch, installed={"x264enc"}, properties={"key-int-max", "tune"})
        assert srv.main([]) == 0
        launch = _launch(server)
        assert " tune=zerolatency " in launch
        assert "speed-preset=" not in launch
        assert "realtime=" not in launch


class TestBanner:
    """What the encoder actually runs with decides what the bench run shows."""

    def test_the_applied_settings_are_named(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _install_fake_gi(monkeypatch, installed={"openh264enc"}, properties={"gop-size", "bitrate"})
        assert srv.main(["--fps", "25"]) == 0
        out = capsys.readouterr().out
        line = next(line for line in out.splitlines() if "openh264enc" in line)
        assert "bitrate=4000000" in line
        assert "gop-size=25" in line
        assert "not set" not in out

    def test_requested_settings_the_encoder_lacks_are_named(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _install_fake_gi(monkeypatch, installed={"x264enc"}, properties={"tune"})
        assert srv.main([]) == 0
        line = next(line for line in capsys.readouterr().out.splitlines() if "not set" in line)
        assert "speed-preset" in line
        assert "bitrate" in line
        assert "keyframe interval" in line
        assert "tune" not in line


class TestEncoderErrors:
    """Each way of having no encoder gets its own remedy."""

    def test_a_preferred_encoder_that_cannot_be_created_is_passed_over(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = _install_fake_gi(monkeypatch, installed={"x264enc", "openh264enc"}, uncreatable={"x264enc"})
        assert srv.main([]) == 0
        launch = _launch(server)
        assert " openh264enc " in launch
        assert "x264enc" not in launch

    @pytest.mark.parametrize(
        ("installed", "uncreatable"),
        [(set(), set()), ({"openh264enc"}, {"openh264enc"})],
        ids=["nothing-installed", "nothing-creatable"],
    )
    def test_nothing_usable_exits_nonzero_and_names_the_package(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        installed: set[str],
        uncreatable: set[str],
    ) -> None:
        server = _install_fake_gi(monkeypatch, installed=installed, uncreatable=uncreatable)
        assert srv.main([]) == 1
        assert server.mounts.added == []
        assert "gstreamer1.0-plugins-bad" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("forced", "installed", "h264", "uncreatable", "reason"),
        [
            ("openh264dec", set(), set(), set(), "no element of that name is installed"),
            ("x265enc", {"x265enc"}, set(), set(), "not an H.264 video encoder"),
            ("vtenc_h264_hw", {"vtenc_h264_hw"}, None, {"vtenc_h264_hw"}, "could not create it"),
        ],
        ids=["typo", "not-h264", "cannot-be-created"],
    )
    def test_a_forced_encoder_that_cannot_serve_says_why(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        forced: str,
        installed: set[str],
        h264: set[str] | None,
        uncreatable: set[str],
        reason: str,
    ) -> None:
        """Every client would otherwise get a 503 on caps negotiation after a
        startup that printed "serving"."""
        server = _install_fake_gi(
            monkeypatch, installed=installed | {"openh264enc"}, h264=h264, uncreatable=uncreatable
        )
        assert srv.main(["--encoder", forced]) == 1
        assert server.mounts.added == []
        err = capsys.readouterr().err
        assert err.count("\n") == 1
        assert forced in err
        assert reason in err
        assert "gstreamer1.0-plugins-bad" not in err


def _report(out: str) -> dict[str, str]:
    return dict(line.split(maxsplit=1) for line in out.splitlines())


class TestListEncoders:
    def test_reports_without_starting_a_server(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = _install_fake_gi(monkeypatch, installed={"openh264enc"})
        assert srv.main(["--list-encoders"]) == 0
        assert server.mounts.added == []

    def test_every_installed_h264_encoder_is_listed_with_its_state(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``--encoder`` takes any of them, so the list must show them all."""
        _install_fake_gi(
            monkeypatch,
            installed={"x264enc", "openh264enc", "vtenc_h264_hw", "jpegenc"},
            h264={"x264enc", "openh264enc", "vtenc_h264_hw"},
            uncreatable={"x264enc"},
        )
        assert srv.main(["--list-encoders"]) == 0
        assert _report(capsys.readouterr().out) == {
            "x264enc": "preferred, cannot be created",
            "vtenc_h264": "preferred, not installed",
            "openh264enc": "preferred, usable, auto-selected",
            "vtenc_h264_hw": "usable",
        }

    @pytest.mark.parametrize(
        ("installed", "uncreatable"),
        [
            ({"x264enc", "vtenc_h264", "openh264enc"}, set()),
            ({"x264enc", "vtenc_h264", "openh264enc"}, {"x264enc"}),
            ({"x264enc", "openh264enc"}, {"x264enc"}),
            ({"openh264enc", "vtenc_h264_hw"}, set()),
        ],
    )
    def test_the_marked_encoder_is_the_one_served(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        installed: set[str],
        uncreatable: set[str],
    ) -> None:
        server = _install_fake_gi(monkeypatch, installed=installed, uncreatable=uncreatable)
        assert srv.main(["--list-encoders"]) == 0
        marked = [name for name, notes in _report(capsys.readouterr().out).items() if "auto-selected" in notes]
        assert srv.main([]) == 0
        assert len(marked) == 1
        assert f" {marked[0]} " in _launch(server)


def _real_gst() -> Any:
    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
    except (ImportError, ValueError):
        pytest.skip("PyGObject / GStreamer is not importable")
    Gst.init(None)
    return Gst


def _installed_preferred(gst: Any) -> list[str]:
    names = [name for name in srv._ENCODER_PREFERENCE if gst.ElementFactory.find(name) is not None]
    if not names:
        pytest.skip("no preferred H.264 encoder is installed")
    return names


class TestRealGStreamer:
    """What an element actually has comes from the registry, not the fakes."""

    def test_installed_preferred_encoders_are_recognised_as_h264(self) -> None:
        gst = _real_gst()
        assert set(_installed_preferred(gst)) <= set(srv.h264_encoders(gst))

    def test_elements_that_do_not_output_h264_are_not_offered(self) -> None:
        gst = _real_gst()
        others = ("jpegenc", "vp8enc", "x265enc", "avdec_h264", "openh264dec")
        installed = {name for name in others if gst.ElementFactory.find(name) is not None}
        if not installed:
            pytest.skip("none of the non-H.264 reference elements is installed")
        assert not installed & set(srv.h264_encoders(gst))

    def test_each_preferred_encoder_gets_a_keyframe_interval_it_accepts(self) -> None:
        gst = _real_gst()
        for name in _installed_preferred(gst):
            settings = srv.probe_encoder(gst, name, 25)
            assert settings is not None, f"{name} could not be created"
            keyframes = [value for key, value in settings.applied.items() if key in srv._KEYFRAME_PROPERTIES]
            assert keyframes == ["25"], name
            gst.parse_launch(f"{name} {settings}")  # GLib.Error on a property or value the element rejects
