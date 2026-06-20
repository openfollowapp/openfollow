# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for video input plugin discovery, URI redaction, and the receiver bus/state/pipeline helpers."""

from __future__ import annotations

import logging

import pytest

from openfollow.runtime.receiver_bus import ReceiverBusHandler
from openfollow.runtime.receiver_pipeline import ReceiverPipelineAssembler
from openfollow.runtime.receiver_state import ReceiverStateMachine
from openfollow.video.inputs import get_registry
from openfollow.video.inputs._base import ReconnectPolicy, redact_uri
from openfollow.video.inputs.ndi import NdiInput
from openfollow.video.inputs.rtp import _parse_rtp_url
from openfollow.video.inputs.rtsp import RtspInput
from openfollow.video.inputs.srt import SrtInput, _resolve_srt_uri

pytestmark = pytest.mark.unit


def test_redact_uri_strips_inline_credentials() -> None:
    assert redact_uri("rtsp://user:pass@cam.local:554/h264") == "rtsp://cam.local:554/h264"


def test_redact_uri_masks_srt_query_secrets_keeps_host() -> None:
    out = redact_uri("srt://host:5000?streamid=r=0&passphrase=secret&latency=20")
    assert "secret" not in out
    assert "r=0" not in out
    assert "host:5000" in out
    assert "latency=20" in out


def test_redact_uri_passes_through_credential_free_and_schemeless() -> None:
    assert redact_uri("rtsp://cam:554/stream") == "rtsp://cam:554/stream"  # no creds
    assert redact_uri("not-a-uri") == "not-a-uri"  # no scheme
    assert redact_uri("file:///x") == "file:///x"  # no netloc
    assert redact_uri("host:554/path") == "host:554/path"  # bare host:port, no userinfo


def test_redact_uri_strips_credentials_from_schemeless_shorthand() -> None:
    # urlsplit reads the userinfo as a bogus scheme + empty netloc; a plain
    # pass-through would have logged/displayed the password verbatim.
    assert redact_uri("user:pass@192.168.0.1/stream") == "192.168.0.1/stream"
    assert redact_uri("admin:hunter2@cam.local:554") == "cam.local:554"


def test_get_source_label_redacts_rtsp_credentials() -> None:
    label = RtspInput.get_source_label({"rtsp_url": "rtsp://admin:hunter2@cam.local:554/s"})
    assert "hunter2" not in label
    assert "cam.local:554/s" in label


def test_resolve_srt_uri_accepts_shorthand_and_full_uri() -> None:
    assert _resolve_srt_uri("") == "srt://0.0.0.0:5000"
    assert _resolve_srt_uri("192.168.0.5:1600") == "srt://192.168.0.5:1600"
    assert _resolve_srt_uri("srt://10.0.0.1:5000?streamid=r=0") == "srt://10.0.0.1:5000?streamid=r=0"


def test_parse_rtp_url_detects_multicast_and_defaults() -> None:
    assert _parse_rtp_url("") == ("0.0.0.0", 5004, False)
    assert _parse_rtp_url("rtp://232.255.255.255:4000") == ("232.255.255.255", 4000, True)
    assert _parse_rtp_url("10.0.0.5:5006") == ("10.0.0.5", 5006, False)


def test_input_registry_contains_builtin_plugins() -> None:
    # Use ``get_registry`` (all discovered plugins) rather than
    # ``get_available_input_ids`` (filtered by ``is_available()``).
    # This is a discovery/registration test – the registry must hold
    # every built-in plugin even on hosts where some report
    # unavailable (e.g. NDI shared lib missing on Linux CI, V4L2 on
    # macOS, AVFoundation on Linux).
    registry = get_registry()
    plugin_ids = set(registry.keys())
    assert {"ndi", "srt", "rtsp", "rtp", "picam"}.issubset(plugin_ids)

    assert registry["ndi"].display_name == "NDI®"
    assert registry["srt"].display_name == "SRT"


def test_discover_inputs_swallows_module_import_errors(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import importlib
    import logging as _logging

    from openfollow.video import inputs as inputs_pkg

    # Force a fresh discovery pass: clear the once-only flag AND empty
    # ``_registry`` so the post-discovery assertion can't be satisfied
    # by leftover state from an earlier test (monkeypatch restores both
    # at teardown).
    monkeypatch.setattr(inputs_pkg, "_discovered", False)
    monkeypatch.setattr(inputs_pkg, "_registry", {})

    real_import = importlib.import_module

    def _import(name: str, *args, **kwargs):
        if name.endswith(".srt"):
            raise ImportError("simulated plugin failure")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(inputs_pkg.importlib, "import_module", _import)

    with caplog.at_level(_logging.WARNING, logger="openfollow.video.inputs"):
        inputs_pkg._discover_inputs()

    # Broken plugin was logged.
    assert any("Failed to load video input plugin: srt" in rec.message for rec in caplog.records)
    # Discovery continued past the failure and re-populated ``_registry``
    # with the un-broken siblings – proves the ``except Exception`` arm
    # really does ``continue`` instead of bailing out. (``_registry`` was
    # explicitly cleared above, so this can only pass if the loop ran.)
    registry = inputs_pkg.get_registry()
    assert "ndi" in registry

    # Second pass with ``_registry`` already populated – exercises the
    # ``cls.input_id not in _registry`` skip arm (no double-registration).
    monkeypatch.setattr(inputs_pkg, "_discovered", False)
    snapshot = dict(inputs_pkg._registry)
    inputs_pkg._discover_inputs()
    assert dict(inputs_pkg._registry) == snapshot


def test_no_test_only_video_input_subclasses_leak() -> None:
    """Regression guard against test-only VideoInputBase subclasses leaking.

    The plugin registry iterates ``VideoInputBase.__subclasses__()`` at
    discovery time, and that set isn't restricted to modules under
    ``openfollow/video/inputs/``. Any test that subclasses
    ``VideoInputBase`` (even briefly, in a test body) ends up
    permanently in ``__subclasses__()`` for the rest of the process –
    so the next test that resets ``_discovered`` and re-runs discovery
    picks it up too. That creates test-order coupling and pollutes
    ``/api/config`` video_source fields in any test that exercises
    discovery against the live registry.

    This test enforces the rule: ALL ``VideoInputBase`` subclasses
    visible to the running process must come from the production
    plugin modules under ``openfollow/video/inputs/``.
    """
    from openfollow.video.inputs._base import VideoInputBase

    leaks: list[str] = []
    for cls in VideoInputBase.__subclasses__():
        module = cls.__module__ or ""
        if not module.startswith("openfollow.video.inputs"):
            leaks.append(f"{module}.{cls.__name__} (input_id={cls.input_id!r})")

    assert not leaks, (
        "Test-only VideoInputBase subclasses leaking into "
        "__subclasses__() – see for the fix pattern "
        "(use a duck-typed namespace and invoke methods via __func__):\n  " + "\n  ".join(leaks)
    )


def test_ndi_availability_probe_never_raises() -> None:
    available, reason = NdiInput.is_available()
    assert isinstance(available, bool)
    assert isinstance(reason, str)


def test_srt_reconnect_policy_has_timeout_and_placeholder_fallback() -> None:
    policy = SrtInput.reconnect_policy()
    assert policy.max_attempts == 3
    assert policy.connection_timeout == 8.0
    assert policy.fallback_to_selection is True


def test_receiver_state_machine_applies_backoff_and_fallback_threshold() -> None:
    state = ReceiverStateMachine(reconnect_delay=1.0)
    policy = ReconnectPolicy(
        max_attempts=3,
        max_delay=4.0,
        backoff_multiplier=2.0,
        fallback_to_selection=True,
    )

    assert state.build_reconnect_schedule(policy).delay_ms == 1000
    assert state.build_reconnect_schedule(policy).delay_ms == 2000
    third = state.build_reconnect_schedule(policy)
    assert third.attempt == 3
    assert third.delay_ms == 4000
    assert state.should_fallback_to_placeholder(policy) is True


def test_receiver_state_machine_marks_video_flow_without_placeholder_connect() -> None:
    state = ReceiverStateMachine(reconnect_delay=1.0)

    # Resolution is tracked from CAPS; connection from real frame flow.
    assert state.set_resolution(1280, 720) is True
    assert state.resolution == (1280, 720)
    assert state.mark_frame_received() is True  # first real frame → connected
    assert state.connected is True
    assert state.mark_frame_received() is False  # already connected, no retrigger

    state.reset_video_flow()
    state.set_placeholder_pipeline(True)
    # Placeholder frames must not mark connected or set video_flow_detected.
    assert state.mark_frame_received() is False
    assert state.connected is False
    assert state.video_flow_detected is False


def test_receiver_bus_handler_dispatches_core_message_types() -> None:
    class FakeGst:
        class MessageType:
            ASYNC_DONE = "ASYNC_DONE"
            ERROR = "ERROR"
            EOS = "EOS"
            STATE_CHANGED = "STATE_CHANGED"

        class State:
            PLAYING = "PLAYING"

    class FakeSource:
        def __init__(self, name: str) -> None:
            self._name = name

        def get_name(self) -> str:
            return self._name

    class FakeError:
        def __init__(self, message: str) -> None:
            self.message = message

    class FakeMessage:
        def __init__(self, msg_type: str, src_name: str = "shared_videosink") -> None:
            self.type = msg_type
            self.src = FakeSource(src_name)

        @staticmethod
        def parse_error():
            return FakeError("boom"), "debug"

        @staticmethod
        def parse_state_changed():
            return None, FakeGst.State.PLAYING, None

    async_calls: list[object] = []
    errors: list[str] = []
    eos_calls: list[bool] = []
    pipeline = object()

    handler = ReceiverBusHandler(
        gst=FakeGst,
        logger=logging.getLogger(__name__),
        get_pipeline=lambda: pipeline,
        on_async_done=async_calls.append,
        on_error=errors.append,
        on_eos=lambda: eos_calls.append(True),
        is_placeholder_pipeline=lambda: False,
        get_input_display_name=lambda: "NDI",
    )

    handler.handle_message(None, FakeMessage(FakeGst.MessageType.ASYNC_DONE))
    handler.handle_message(None, FakeMessage(FakeGst.MessageType.ERROR))
    handler.handle_message(None, FakeMessage(FakeGst.MessageType.EOS))
    handler.handle_message(None, FakeMessage(FakeGst.MessageType.STATE_CHANGED))

    assert async_calls == [pipeline]
    assert errors == ["boom"]
    assert eos_calls == [True]


def test_receiver_bus_handler_setup_bus_skips_when_pipeline_none() -> None:
    """Calling ``setup_bus(None, ...)`` is a no-op so the receiver can
    publish bus subscriptions before the pipeline is built without
    blowing up."""

    class FakeGst:
        class MessageType:
            ASYNC_DONE = "ASYNC_DONE"

    handler = ReceiverBusHandler(
        gst=FakeGst,
        logger=logging.getLogger(__name__),
        get_pipeline=lambda: None,
        on_async_done=lambda _p: None,
        on_error=lambda _m: None,
        on_eos=lambda: None,
        is_placeholder_pipeline=lambda: False,
        get_input_display_name=lambda: "NDI",
    )
    # Must not raise.
    handler.setup_bus(None, lambda *args: None)


def _make_bus_handler() -> ReceiverBusHandler:
    class FakeGst:
        class MessageType:
            ASYNC_DONE = "ASYNC_DONE"

    return ReceiverBusHandler(
        gst=FakeGst,
        logger=logging.getLogger(__name__),
        get_pipeline=lambda: None,
        on_async_done=lambda _p: None,
        on_error=lambda _m: None,
        on_eos=lambda: None,
        is_placeholder_pipeline=lambda: False,
        get_input_display_name=lambda: "RTSP",
    )


def test_teardown_bus_removes_signal_watch() -> None:
    removed: list[bool] = []

    class FakeBus:
        def remove_signal_watch(self) -> None:
            removed.append(True)

    class FakePipeline:
        def get_bus(self) -> FakeBus:
            return FakeBus()

    _make_bus_handler().teardown_bus(FakePipeline())
    assert removed == [True]


def test_teardown_bus_skips_when_pipeline_or_bus_none() -> None:
    handler = _make_bus_handler()
    handler.teardown_bus(None)  # no pipeline → no-op, no raise

    class NoBusPipeline:
        def get_bus(self):  # noqa: ANN201
            return None

    handler.teardown_bus(NoBusPipeline())  # no bus → no-op, no raise


def test_teardown_bus_swallows_remove_failure() -> None:

    class FakeBus:
        def remove_signal_watch(self) -> None:
            raise RuntimeError("bus already disposed")

    class FakePipeline:
        def get_bus(self) -> FakeBus:
            return FakeBus()

    _make_bus_handler().teardown_bus(FakePipeline())  # must not raise


def test_setup_then_teardown_disconnects_message_handler() -> None:
    """teardown_bus disconnects the connect("message") handler before removing
    the watch – symmetric attach/detach so no GSource + closure leaks."""
    events: list[str] = []

    class FakeBus:
        def add_signal_watch(self) -> None:
            events.append("watch")

        def connect(self, sig: str, _cb) -> int:  # noqa: ANN001
            events.append(f"connect:{sig}")
            return 77

        def disconnect(self, hid: int) -> None:
            events.append(f"disconnect:{hid}")

        def remove_signal_watch(self) -> None:
            events.append("unwatch")

    fake_bus = FakeBus()

    class FakePipeline:
        def get_bus(self) -> FakeBus:
            return fake_bus

    handler = _make_bus_handler()
    pipe = FakePipeline()
    handler.setup_bus(pipe, lambda *_a: None)
    handler.teardown_bus(pipe)
    assert "connect:message" in events
    # Disconnect happens, with the stored id, before remove_signal_watch.
    assert events.index("disconnect:77") < events.index("unwatch")


def test_teardown_bus_swallows_disconnect_failure() -> None:
    class FakeBus:
        def add_signal_watch(self) -> None:
            pass

        def connect(self, _sig: str, _cb) -> int:  # noqa: ANN001
            return 5

        def disconnect(self, _hid: int) -> None:
            raise RuntimeError("handler already gone")

        def remove_signal_watch(self) -> None:
            pass

    fake_bus = FakeBus()

    class FakePipeline:
        def get_bus(self) -> FakeBus:
            return fake_bus

    handler = _make_bus_handler()
    pipe = FakePipeline()
    handler.setup_bus(pipe, lambda *_a: None)
    handler.teardown_bus(pipe)  # disconnect raises → swallowed, must not raise


def test_receiver_bus_handler_async_done_with_no_pipeline_skips_callback() -> None:

    class FakeGst:
        class MessageType:
            ASYNC_DONE = "ASYNC_DONE"
            ERROR = "ERROR"
            EOS = "EOS"
            STATE_CHANGED = "STATE_CHANGED"

    class FakeMessage:
        type = FakeGst.MessageType.ASYNC_DONE

    invoked: list[object] = []
    handler = ReceiverBusHandler(
        gst=FakeGst,
        logger=logging.getLogger(__name__),
        get_pipeline=lambda: None,
        on_async_done=invoked.append,
        on_error=lambda _m: None,
        on_eos=lambda: None,
        is_placeholder_pipeline=lambda: False,
        get_input_display_name=lambda: "NDI",
    )
    handler.handle_message(None, FakeMessage())
    assert invoked == []  # callback skipped because pipeline is None


def test_receiver_bus_handler_unknown_message_type_is_ignored() -> None:
    """An unrecognised message type falls through every if/elif and
    exits silently."""

    class FakeGst:
        class MessageType:
            ASYNC_DONE = "ASYNC_DONE"
            ERROR = "ERROR"
            EOS = "EOS"
            STATE_CHANGED = "STATE_CHANGED"

    class FakeMessage:
        type = "DURATION_CHANGED"  # not handled

    handler = ReceiverBusHandler(
        gst=FakeGst,
        logger=logging.getLogger(__name__),
        get_pipeline=lambda: object(),
        on_async_done=lambda _p: None,
        on_error=lambda _m: None,
        on_eos=lambda: None,
        is_placeholder_pipeline=lambda: False,
        get_input_display_name=lambda: "NDI",
    )
    handler.handle_message(None, FakeMessage())  # must not raise


def test_receiver_bus_handler_state_changed_branches() -> None:
    """``_handle_state_changed`` has four exits: not-a-videosink source,
    state ≠ PLAYING, placeholder-pipeline-PLAYING, and the default
    "awaiting first frame" log."""

    class FakeGst:
        class MessageType:
            ASYNC_DONE = "ASYNC_DONE"
            ERROR = "ERROR"
            EOS = "EOS"
            STATE_CHANGED = "STATE_CHANGED"

        class State:
            PLAYING = "PLAYING"
            PAUSED = "PAUSED"

    class FakeSource:
        def __init__(self, name: str) -> None:
            self._name = name

        def get_name(self) -> str:
            return self._name

    class FakeMessage:
        def __init__(self, src_name: str, new_state: str = FakeGst.State.PLAYING) -> None:
            self.type = FakeGst.MessageType.STATE_CHANGED
            self.src = FakeSource(src_name)
            self._new_state = new_state

        def parse_state_changed(self):
            return None, self._new_state, None

    is_placeholder = [False]
    handler = ReceiverBusHandler(
        gst=FakeGst,
        logger=logging.getLogger(__name__),
        get_pipeline=lambda: object(),
        on_async_done=lambda _p: None,
        on_error=lambda _m: None,
        on_eos=lambda: None,
        is_placeholder_pipeline=lambda: is_placeholder[0],
        get_input_display_name=lambda: "NDI",
    )

    # Non-videosink source.
    handler.handle_message(None, FakeMessage("decodebin"))

    # videosink, but new_state is PAUSED.
    handler.handle_message(None, FakeMessage("videosink", FakeGst.State.PAUSED))

    # placeholder pipeline reaches PLAYING.
    is_placeholder[0] = True
    handler.handle_message(None, FakeMessage("shared_videosink"))

    # Default "awaiting first frame" path: real (non-placeholder)
    # videosink reaches PLAYING.
    is_placeholder[0] = False
    handler.handle_message(None, FakeMessage("videosink"))


def test_receiver_pipeline_assembler_builds_placeholder_pipeline() -> None:
    class FakePad:
        @staticmethod
        def link(other) -> str:
            return "ok"

    class FakeElement:
        def __init__(self, name: str) -> None:
            self.name = name
            self.properties: dict[str, object] = {}
            self.links: list[FakeElement] = []
            self.signals: list[tuple[str, object]] = []

        def set_property(self, key: str, value: object) -> None:
            self.properties[key] = value

        def link(self, other: FakeElement) -> bool:
            self.links.append(other)
            return True

        @staticmethod
        def get_request_pad(name: str) -> FakePad:
            return FakePad()

        @staticmethod
        def get_static_pad(name: str) -> FakePad:
            return FakePad()

        def connect(self, signal_name: str, callback: object) -> None:
            self.signals.append((signal_name, callback))

    class FakePipeline:
        def __init__(self) -> None:
            self.elements: list[FakeElement] = []

        def add(self, element: FakeElement) -> None:
            self.elements.append(element)

        def get_by_name(self, name: str) -> FakeElement | None:
            for element in self.elements:
                if element.name == name:
                    return element
            return None

    class FakeGst:
        class Pipeline:
            @staticmethod
            def new(name: str) -> FakePipeline:
                return FakePipeline()

        class ElementFactory:
            @staticmethod
            def make(kind: str, name: str) -> FakeElement:
                return FakeElement(name)

        class Caps:
            @staticmethod
            def from_string(value: str) -> str:
                return value

        class PadLinkReturn:
            OK = "ok"

    sink = FakeElement("shared_videosink")
    assembler = ReceiverPipelineAssembler(
        gst=FakeGst,
        logger=logging.getLogger(__name__),
        overlay_renderer=object(),
        detector=None,
        prepare_sink=lambda: sink,
    )

    pipeline = assembler.create_placeholder_pipeline()
    assert pipeline is not None
    # HUD lives on Gtk.DrawingArea above gtksink – no cairooverlay in pipeline.
    assert pipeline.get_by_name("cairo_overlay") is None

    videotestsrc = pipeline.get_by_name("videotestsrc")
    assert videotestsrc is not None
    assert videotestsrc.properties["pattern"] == 2
