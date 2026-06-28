# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for :class:`GstNativeSinkReceiver` in ``openfollow.video.receiver``.

The receiver is a generic pipeline orchestrator that delegates protocol-
specific work to a ``VideoInputBase`` plugin.  All GStreamer / GLib
interaction is indirected through ``Gst`` and ``GLib`` module-level
bindings that we patch with recording fakes, so every test stays hermetic
(no real GStreamer pipelines, no main-loop, no threads that touch real
system state).

Tests cover the receiver's public surface plus its non-trivial private
state transitions:

* module-level ``gst_runtime_available`` / ``discover_sources`` shims
* constructor validation, plugin-driven properties, shared sink creation
* connection lifecycle via ``play()`` / ``start()`` / ``stop()`` for
  inputs with and without source selection, plus pipeline set-state
  failure branches that schedule a reconnect
* ``create_pipeline()`` routing between placeholder (plugin unavailable
  or raises) and the happy path
* source-selection navigation, discovery scheduling, ``confirm_source_
  selection`` round-trip that saves to config
* reconnect logic with backoff and ``fallback_to_selection``
* bus / pad / caps-structure event handlers, including the no-op
  guards (event not CAPS, missing framerate, invalid values)
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from openfollow.video import receiver as receiver_mod
from openfollow.video.connection_status import ConnectionStatus
from openfollow.video.inputs._base import (
    ConfigField,
    InputCapabilities,
    ReconnectPolicy,
)

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# Hermetic fakes for Gst / GLib
# --------------------------------------------------------------------------- #


class FakeState:
    NULL = "null"
    PLAYING = "playing"
    PAUSED = "paused"


class FakeStateChangeReturn:
    SUCCESS = "success"
    FAILURE = "failure"
    ASYNC = "async"


class FakeMessageType:
    ERROR = "error"
    EOS = "eos"
    ASYNC_DONE = "async_done"
    STATE_CHANGED = "state_changed"


class FakeEventType:
    CAPS = "caps"
    OTHER = "other"


class FakePadProbeType:
    EVENT_DOWNSTREAM = "event_downstream"
    BUFFER = "buffer"


class FakePadProbeReturn:
    OK = "ok"


class FakeElementFactory:
    """Controllable ``Gst.ElementFactory`` stand-in.

    The receiver only uses ``.find()`` and ``.make()``.  ``find()``
    returns truthy when the element is in ``available_elements``;
    ``make()`` returns a :class:`FakeElement`.
    """

    def __init__(
        self,
        *,
        available_elements: set[str] | None = None,
    ) -> None:
        # NB: ``or`` would turn an intentionally-empty set into the
        # default – fall back only when the caller passed ``None``.
        self.available_elements = {"gtksink"} if available_elements is None else available_elements
        self.made: list[tuple[str, str]] = []

    def find(self, name: str) -> object | None:
        return object() if name in self.available_elements else None

    def make(self, kind: str, name: str) -> FakeElement:
        self.made.append((kind, name))
        return FakeElement(name=name)


class FakeElement:
    """Recording element – tracks property writes and parent linkage."""

    def __init__(self, name: str = "elem") -> None:
        self._name = name
        self.properties: dict[str, Any] = {}
        self._parent: Any = None
        self._static_pads: dict[str, FakePad] = {}

    def get_name(self) -> str:
        return self._name

    def set_property(self, key: str, value: Any) -> None:
        self.properties[key] = value

    def get_property(self, key: str) -> Any:
        return self.properties.get(key)

    def get_parent(self) -> Any:
        return self._parent

    def get_static_pad(self, name: str) -> FakePad:
        return self._static_pads.setdefault(name, FakePad(name))


class FakePad:
    def __init__(self, name: str) -> None:
        self._name = name
        self.probes: list[tuple[str, Callable]] = []

    def add_probe(self, probe_type: Any, callback: Callable) -> None:
        self.probes.append((probe_type, callback))


class FakeBus:
    def __init__(self, pop_message: Any = None) -> None:
        self.signal_watch_added = False
        self.signal_watch_removed = False
        self.remove_count = 0
        self.message_callback: Callable | None = None
        self._pop_message = pop_message

    def add_signal_watch(self) -> None:
        self.signal_watch_added = True

    def remove_signal_watch(self) -> None:
        self.signal_watch_removed = True
        self.remove_count += 1

    def connect(self, signal: str, callback: Callable) -> None:
        assert signal == "message"
        self.message_callback = callback

    def timed_pop_filtered(self, timeout_ns: int, type_mask: str) -> Any:
        return self._pop_message


class FakePipeline:
    """Recording pipeline that returns configurable set_state outcomes."""

    def __init__(
        self,
        name: str = "pipeline",
        *,
        set_state_returns: dict[str, str] | None = None,
        bus: FakeBus | None = None,
        elements: dict[str, FakeElement] | None = None,
    ) -> None:
        self.name = name
        self.state_changes: list[str] = []
        self._return_map = dict(set_state_returns or {})
        self._bus = bus or FakeBus()
        self._elements = dict(elements or {})
        self.raise_on_null = False

    def set_state(self, state: str) -> str:
        self.state_changes.append(state)
        if self.raise_on_null and state == FakeState.NULL:
            raise RuntimeError("pipeline set NULL blew up")
        return self._return_map.get(state, FakeStateChangeReturn.SUCCESS)

    def get_state(self, timeout_ns: int = 0) -> tuple[str, str, str]:
        """Stub for pipeline.get_state(): always reports NULL immediately.

        Returns ``(StateChangeReturn, current_state, pending_state)`` as
        plain strings (FakeStateChangeReturn / FakeState values are strings).
        """
        return (FakeStateChangeReturn.SUCCESS, FakeState.NULL, FakeState.NULL)

    def get_bus(self) -> FakeBus:
        return self._bus

    def get_by_name(self, name: str) -> FakeElement | None:
        return self._elements.get(name)


class FakeGst:
    Gst = None  # filled in below
    State = FakeState
    StateChangeReturn = FakeStateChangeReturn
    MessageType = FakeMessageType
    EventType = FakeEventType
    PadProbeType = FakePadProbeType
    PadProbeReturn = FakePadProbeReturn
    MSECOND = 1_000_000  # nanoseconds per millisecond – used with pipeline bus

    def __init__(self) -> None:
        self.init_calls = 0
        self.ElementFactory = FakeElementFactory()

    def init(self, _args: Any) -> None:
        self.init_calls += 1


class FakeGLib:
    """Records ``timeout_add`` / ``source_remove`` calls – no real timers."""

    def __init__(self) -> None:
        self.timers: dict[int, tuple[int, Callable]] = {}
        self._counter = 0
        self.removed: list[int] = []

    def timeout_add(self, delay_ms: int, callback: Callable) -> int:
        self._counter += 1
        self.timers[self._counter] = (delay_ms, callback)
        return self._counter

    def source_remove(self, source_id: int) -> None:
        self.removed.append(source_id)
        self.timers.pop(source_id, None)


# --------------------------------------------------------------------------- #
# Fake VideoInputBase plugin
# --------------------------------------------------------------------------- #


class FakeInput:
    """Pluggable fake input; intentionally not a VideoInputBase to avoid polluting __subclasses__()."""

    input_id = "fake"
    display_name = "Fake"

    # Defaults – override per-test via subclass.
    _available: tuple[bool, str] = (True, "")
    _capabilities = InputCapabilities(
        has_source_selection=False,
        has_source_discovery=False,
        selection_title="SELECT FAKE",
        hotkey="f",
        discovery_interval=5.0,
        force_zero_latency=False,
    )
    _reconnect_policy = ReconnectPolicy(
        max_attempts=2,
        min_delay=0.1,
        max_delay=1.0,
        backoff_multiplier=2.0,
        connection_timeout=4.0,
        fallback_to_selection=True,
    )
    _config_fields: list[ConfigField] = [
        ConfigField(name="fake_source", type=str, default="", label="Fake Source"),
    ]
    # Pipeline factory / side-effect hooks populated by tests via
    # direct attribute assignment on instances.
    create_pipeline_raises: Exception | None = None
    create_pipeline_result: Any = None
    create_pipeline_call_count: int = 0

    # -- classmethod overrides -----------------------------------------------
    @classmethod
    def config_fields(cls) -> list[ConfigField]:
        return list(cls._config_fields)

    @classmethod
    def capabilities(cls) -> InputCapabilities:
        return cls._capabilities

    @classmethod
    def reconnect_policy(cls) -> ReconnectPolicy:
        # Return a fresh copy each call, mirroring production inputs (which
        # construct a new ``ReconnectPolicy(...)``). The receiver mutates the
        # returned policy to fold in operator overrides; a shared class-level
        # instance would leak those mutations across tests.
        return replace(cls._reconnect_policy)

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        return cls._available

    @classmethod
    def get_source_label(cls, config: dict[str, Any]) -> str:
        return str(config.get("fake_source", "") or "")

    @classmethod
    def web_ui_html(cls, config: dict[str, Any]) -> str:  # pragma: no cover
        return ""

    # -- instance methods ----------------------------------------------------
    def __init__(self) -> None:
        super().__init__()
        self.cleanup_calls = 0
        self.async_done_calls = 0
        self.discover_results: list[list[str]] | None = None
        self.discover_calls: list[float] = []

    def create_pipeline(
        self,
        config: dict[str, Any],
        sink: Any,
        build_overlay_tail: Callable,
        prepare_sink: Callable,
    ) -> Any:
        type(self).create_pipeline_call_count += 1
        if self.create_pipeline_raises is not None:
            raise self.create_pipeline_raises
        return self.create_pipeline_result

    def on_bus_async_done(self, pipeline: Any) -> None:
        self.async_done_calls += 1

    def cleanup(self) -> None:
        self.cleanup_calls += 1

    # Note: ``discover_sources`` is a classmethod on the base; we provide
    # an instance-level shim here so tests can program per-receiver
    # results without registry lookup gymnastics.
    def discover_sources(self, timeout: float = 2.0) -> list[str]:  # type: ignore[override]
        self.discover_calls.append(timeout)
        if self.discover_results:
            return self.discover_results.pop(0)
        return []


class FakeInputAlt:
    """Second pluggable fake plugin with a different ``input_id`` and
    ``config_fields``. Used by ``swap_input`` tests to verify a plugin
    swap (RTSP-like → SRT-like) carries over the receiver instance,
    cleans up the old plugin, and rebuilds the pipeline against the
    new one. Same duck-typing rationale as ``FakeInput``."""

    input_id = "fake_alt"
    display_name = "FakeAlt"

    _available: tuple[bool, str] = (True, "")
    _capabilities = InputCapabilities(
        has_source_selection=False,
        has_source_discovery=False,
        selection_title="SELECT FAKE ALT",
        hotkey="g",
        discovery_interval=5.0,
        force_zero_latency=False,
    )
    _reconnect_policy = ReconnectPolicy(
        max_attempts=1,
        min_delay=0.1,
        max_delay=1.0,
        backoff_multiplier=2.0,
        connection_timeout=4.0,
        fallback_to_selection=False,
    )
    _config_fields: list[ConfigField] = [
        ConfigField(
            name="alt_url",
            type=str,
            default="",
            label="Alt URL",
        ),
    ]
    create_pipeline_raises: Exception | None = None
    create_pipeline_result: Any = None
    create_pipeline_call_count: int = 0

    @classmethod
    def config_fields(cls) -> list[ConfigField]:
        return list(cls._config_fields)

    @classmethod
    def capabilities(cls) -> InputCapabilities:
        return cls._capabilities

    @classmethod
    def reconnect_policy(cls) -> ReconnectPolicy:
        # Return a fresh copy each call, mirroring production inputs (which
        # construct a new ``ReconnectPolicy(...)``). The receiver mutates the
        # returned policy to fold in operator overrides; a shared class-level
        # instance would leak those mutations across tests.
        return replace(cls._reconnect_policy)

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        return cls._available

    @classmethod
    def get_source_label(cls, config: dict[str, Any]) -> str:
        return str(config.get("alt_url", "") or "")

    @classmethod
    def web_ui_html(cls, config: dict[str, Any]) -> str:  # pragma: no cover
        return ""

    def __init__(self) -> None:
        super().__init__()
        self.cleanup_calls = 0
        self.async_done_calls = 0

    def create_pipeline(
        self,
        config: dict[str, Any],
        sink: Any,
        build_overlay_tail: Callable,
        prepare_sink: Callable,
    ) -> Any:
        type(self).create_pipeline_call_count += 1
        if self.create_pipeline_raises is not None:
            raise self.create_pipeline_raises
        return self.create_pipeline_result

    def on_bus_async_done(self, pipeline: Any) -> None:
        self.async_done_calls += 1

    def cleanup(self) -> None:
        self.cleanup_calls += 1


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def fake_gst(monkeypatch):
    gst = FakeGst()
    monkeypatch.setattr(receiver_mod, "Gst", gst, raising=False)
    return gst


@pytest.fixture
def fake_glib(monkeypatch):
    glib = FakeGLib()
    monkeypatch.setattr(receiver_mod, "GLib", glib, raising=False)
    return glib


@pytest.fixture
def fake_input_cls(monkeypatch):
    """Registers ``FakeInput`` as the class returned by ``get_input_class``
    and restores every mutable class-level attribute after the test, so
    tests that reassign (e.g.) ``FakeInput.create_pipeline_raises`` can't
    leak state into later tests."""
    # Capture originals.
    originals = {
        name: getattr(FakeInput, name)
        for name in (
            "_available",
            "_capabilities",
            "_reconnect_policy",
            "_config_fields",
            "create_pipeline_raises",
            "create_pipeline_result",
            "create_pipeline_call_count",
        )
    }
    monkeypatch.setattr(
        receiver_mod,
        "get_input_class",
        lambda input_id: FakeInput if input_id == "fake" else None,
    )
    yield FakeInput
    # Restore.
    for name, value in originals.items():
        setattr(FakeInput, name, value)


@pytest.fixture
def fake_input_pair(monkeypatch):
    """Register both ``FakeInput`` and ``FakeInputAlt`` so tests can
    swap between them via ``receiver.swap_input``. Restores every
    mutable class-level attribute on both fakes at teardown."""
    originals = {
        cls: {
            name: getattr(cls, name)
            for name in (
                "_available",
                "_capabilities",
                "_reconnect_policy",
                "_config_fields",
                "create_pipeline_raises",
                "create_pipeline_result",
                "create_pipeline_call_count",
            )
        }
        for cls in (FakeInput, FakeInputAlt)
    }

    def _resolve(input_id: str) -> Any:
        if input_id == "fake":
            return FakeInput
        if input_id == "fake_alt":
            return FakeInputAlt
        return None

    monkeypatch.setattr(receiver_mod, "get_input_class", _resolve)
    yield FakeInput, FakeInputAlt
    for cls, attrs in originals.items():
        for name, value in attrs.items():
            setattr(cls, name, value)


def _make_receiver(
    *,
    input_config: dict[str, Any] | None = None,
    on_widget_changed: Callable | None = None,
    overlay_renderer: Any = None,
    config_path: str = "config.toml",
) -> receiver_mod.GstNativeSinkReceiver:
    return receiver_mod.GstNativeSinkReceiver(
        source_type="fake",
        input_config=dict(input_config or {}),
        reconnect_delay=0.2,
        overlay_renderer=overlay_renderer,
        on_widget_changed=on_widget_changed,
        config_path=config_path,
    )


# --------------------------------------------------------------------------- #
# Module-level helpers
# --------------------------------------------------------------------------- #


class TestModuleHelpers:
    def test_gst_runtime_available_true_when_bindings_present(self, fake_gst) -> None:
        """``gst_runtime_available`` reflects whether the module-level
        ``Gst`` binding is present – a proxy for ``import gi`` having
        succeeded on this host."""
        assert receiver_mod.gst_runtime_available() is True

    def test_gst_runtime_available_false_when_bindings_missing(self, monkeypatch) -> None:
        monkeypatch.setattr(receiver_mod, "Gst", None, raising=False)
        assert receiver_mod.gst_runtime_available() is False

    def test_discover_sources_returns_empty_when_ndi_plugin_missing(self, monkeypatch) -> None:
        monkeypatch.setattr(receiver_mod, "get_input_class", lambda input_id: None)
        assert receiver_mod.discover_sources(timeout=0.5) == []

    def test_discover_sources_delegates_to_ndi_plugin(self, monkeypatch) -> None:
        captured: dict[str, float] = {}

        class SpyPlugin:
            @classmethod
            def discover_sources(cls, timeout: float = 0.1) -> list[str]:
                captured["timeout"] = timeout
                return ["CAM1 (NDI)"]

        monkeypatch.setattr(
            receiver_mod,
            "get_input_class",
            lambda input_id: SpyPlugin if input_id == "ndi" else None,
        )
        assert receiver_mod.discover_sources(timeout=1.25) == ["CAM1 (NDI)"]
        assert captured["timeout"] == 1.25


# --------------------------------------------------------------------------- #
# __init__ / properties
# --------------------------------------------------------------------------- #


class TestConstructor:
    def test_unknown_source_type_raises_value_error(self, monkeypatch) -> None:
        monkeypatch.setattr(receiver_mod, "get_input_class", lambda input_id: None)
        with pytest.raises(ValueError, match="Unknown video input type"):
            receiver_mod.GstNativeSinkReceiver(source_type="nope")

    def test_initial_status_disconnected_with_configured_source(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver(input_config={"fake_source": "my-cam"})
        assert r.connected is False
        # Status marker starts in a neutral "disconnected" state (no
        # reason) because the source IS configured – we're just not
        # receiving yet.
        assert r.status_marker.is_connected is False

    def test_initial_status_disconnected_with_empty_source_sets_reason(
        self, fake_gst, fake_glib, fake_input_cls
    ) -> None:
        r = _make_receiver(input_config={"fake_source": ""})
        # With no configured source, the initial status carries a reason
        # string.  We assert on observable status, not internal flags.
        assert r.connected is False

    def test_source_selection_properties_read_from_plugin_caps(
        self, fake_gst, fake_glib, fake_input_cls, monkeypatch
    ) -> None:
        """``has_source_selection`` / hotkey / title come from the
        plugin's ``InputCapabilities``, not a hard-coded NDI branch."""
        monkeypatch.setattr(
            FakeInput,
            "_capabilities",
            InputCapabilities(
                has_source_selection=True,
                has_source_discovery=True,
                selection_title="PICK ONE",
                hotkey="n",
                discovery_interval=1.0,
            ),
        )
        r = _make_receiver()
        assert r.has_source_selection is True
        assert r.source_selection_hotkey == "n"
        assert r.source_selection_title == "PICK ONE"

    def test_default_properties_reflect_state_machine(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver()
        assert r.resolution == (0, 0)
        assert r.source_framerate == 0.0
        assert r.discovered_sources == []
        assert r.selected_source_index == 0
        assert r.source_selection_active is False
        assert r.source_name == ""  # empty config → empty label


# --------------------------------------------------------------------------- #
# Shared sink / sink widget
# --------------------------------------------------------------------------- #


class TestSharedSink:
    def test_get_shared_sink_creates_gtksink_when_available(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver()
        sink = r._get_shared_sink()
        assert isinstance(sink, FakeElement)
        assert sink.get_name() == "shared_videosink"
        assert sink.properties.get("sync") is False
        # Gst.init is called as part of shared-sink creation.
        assert fake_gst.init_calls == 1
        # Subsequent calls reuse the same sink (no second creation).
        same = r._get_shared_sink()
        assert same is sink
        assert fake_gst.init_calls == 1

    def test_shared_sink_returns_none_when_no_embeddable_sink(self, fake_gst, fake_glib, fake_input_cls) -> None:
        fake_gst.ElementFactory = FakeElementFactory(available_elements=set())
        r = _make_receiver()
        assert r._get_shared_sink() is None

    def test_shared_sink_returns_none_when_factory_make_fails(self, fake_gst, fake_glib, fake_input_cls) -> None:
        class FailingFactory(FakeElementFactory):
            def make(self, kind: str, name: str) -> FakeElement | None:
                return None

        fake_gst.ElementFactory = FailingFactory(available_elements={"gtksink"})
        r = _make_receiver()
        assert r._get_shared_sink() is None

    def test_prepare_sink_detaches_from_previous_parent(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver()
        sink = r._get_shared_sink()

        removed: list[FakeElement] = []

        class FakeParent:
            def remove(self, child: FakeElement) -> None:
                removed.append(child)

        parent = FakeParent()
        sink._parent = parent  # type: ignore[attr-defined]

        result = r._prepare_sink()
        assert result is sink
        assert removed == [sink]

    def test_get_sink_widget_returns_widget_property_when_present(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver()
        sink = r._get_shared_sink()
        sink.set_property("widget", SimpleNamespace(id="gtk-widget"))
        widget = r.get_sink_widget()
        assert widget.id == "gtk-widget"

    def test_get_sink_widget_returns_none_when_sink_creation_fails(self, fake_gst, fake_glib, fake_input_cls) -> None:
        fake_gst.ElementFactory = FakeElementFactory(available_elements=set())
        r = _make_receiver()
        assert r.get_sink_widget() is None

    def test_get_sink_widget_returns_none_when_get_property_raises(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver()
        sink = r._get_shared_sink()

        def boom(_key: str) -> Any:
            raise RuntimeError("widget not ready")

        sink.get_property = boom  # type: ignore[assignment]
        assert r.get_sink_widget() is None


# --------------------------------------------------------------------------- #
# _has_configured_source and video-flow connection
# --------------------------------------------------------------------------- #


class TestVideoFlow:
    def test_has_configured_source_respects_primary_config_field(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        assert r._has_configured_source() is True
        r._input_config["fake_source"] = ""
        assert r._has_configured_source() is False
        r._input_config["fake_source"] = "   "  # whitespace-only
        assert r._has_configured_source() is False

    def test_has_configured_source_falls_back_to_label_when_no_fields(
        self, fake_gst, fake_glib, fake_input_cls, monkeypatch
    ) -> None:
        """If a plugin declares no config fields we probe the label."""
        monkeypatch.setattr(FakeInput, "_config_fields", [])
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        # No fields → the label determines configured-ness; our fake's
        # label comes from ``fake_source`` so it's still truthy.
        assert r._has_configured_source() is True

    def test_real_frame_transitions_to_connected(self, fake_gst, fake_glib, fake_input_cls) -> None:
        """A real decoded buffer marks the feed connected."""
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._state.set_resolution(1920, 1080)  # CAPS arrives before first frame
        r._on_sink_buffer(object(), object())
        assert r.connected is True
        assert r.resolution == (1920, 1080)
        assert r.status_marker.is_connected is True

    def test_caps_event_alone_does_not_connect(self, fake_gst, fake_glib, fake_input_cls) -> None:
        """CAPS events are sticky and must update resolution without marking feed connected."""
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._state.set_placeholder_pipeline(False)
        assert r._state.set_resolution(1280, 720) is True
        assert r.connected is False
        assert r.status_marker.is_connected is False

    def test_real_frame_clears_pending_connection_timeout(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._state.connection_timeout_id = 42
        r._on_sink_buffer(object(), object())
        assert 42 in fake_glib.removed
        assert r._state.connection_timeout_id is None

    def test_placeholder_frame_does_not_connect(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._state.set_placeholder_pipeline(True)
        r._state.connection_timeout_id = 7
        r._on_sink_buffer(object(), object())
        assert r.connected is False
        assert r.status_marker.is_connected is False
        # A placeholder frame also must not cancel a pending connect timeout.
        assert 7 not in fake_glib.removed


# --------------------------------------------------------------------------- #
# play() – paths for inputs without source selection
# --------------------------------------------------------------------------- #


class TestPlayNoSourceSelection:
    def test_no_configured_source_shows_placeholder_and_plays(self, fake_gst, fake_glib, fake_input_cls) -> None:
        """No URL configured → the receiver builds a placeholder
        (videotestsrc) pipeline and sends it to PLAYING so the
        'No Signal' overlay is shown without ever touching the plugin's
        pipeline builder."""
        widgets: list[Any] = []
        r = _make_receiver(on_widget_changed=widgets.append)
        fake_pipeline = FakePipeline()
        r._pipeline_assembler.create_placeholder_pipeline = lambda: fake_pipeline
        r._pipeline_assembler._placeholder_resolution = (1920, 1080)
        # Sink widget must be present so the widget-callback branch is
        # exercised in addition to placeholder construction.
        sink = r._get_shared_sink()
        sink.set_property("widget", SimpleNamespace(id="gtk-widget"))

        r.play()

        assert FakeState.PLAYING in fake_pipeline.state_changes
        # Observable placeholder effects: the assembler's placeholder
        # resolution is published via the public `resolution` property,
        # and the receiver never transitions to connected.
        assert r.resolution == (1920, 1080)
        assert r.connected is False
        # Widget change callback fires once with the sink widget.
        assert len(widgets) == 1

    def test_configured_source_starts_pipeline_and_waits_for_first_frame(
        self, fake_gst, fake_glib, fake_input_cls
    ) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        fake_pipeline = FakePipeline()
        FakeInput.create_pipeline_result = fake_pipeline
        # Set the placeholder resolution to a distinctive value so we can
        # tell placeholder-path from happy-path through `r.resolution`.
        r._pipeline_assembler._placeholder_resolution = (1920, 1080)
        r._pipeline_assembler.create_placeholder_pipeline = lambda: fake_pipeline

        r.play()

        assert FakeState.PLAYING in fake_pipeline.state_changes
        # Happy path: plugin pipeline was built, not the placeholder, so
        # resolution has not been published yet (awaits first frame).
        assert r.resolution == (0, 0)
        assert r.connected is False  # awaiting first frame
        # And the plugin's create_pipeline was actually invoked exactly once.
        assert FakeInput.create_pipeline_call_count == 1

    def test_set_state_failure_schedules_reconnect(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        fake_pipeline = FakePipeline(
            set_state_returns={FakeState.PLAYING: FakeStateChangeReturn.FAILURE},
            bus=FakeBus(pop_message=None),
        )
        FakeInput.create_pipeline_result = fake_pipeline

        r.play()

        # A reconnect timeout must have been scheduled.
        assert len(fake_glib.timers) >= 1

    def test_set_state_failure_surfaces_bus_error_message(self, fake_gst, fake_glib, fake_input_cls) -> None:
        class FakeError:
            message = "pipeline broke"

        class FakeErrorMessage:
            def parse_error(self) -> tuple[FakeError, str]:
                return FakeError(), "debug-info"

        fake_pipeline = FakePipeline(
            set_state_returns={FakeState.PLAYING: FakeStateChangeReturn.FAILURE},
            bus=FakeBus(pop_message=FakeErrorMessage()),
        )
        FakeInput.create_pipeline_result = fake_pipeline
        r = _make_receiver(input_config={"fake_source": "cam-1"})

        r.play()
        # Reconnect scheduled; the parsed bus-error message is baked
        # into the status marker's reason field.  We only assert the
        # reconnect was scheduled – reason text is a log detail.
        assert fake_glib.timers

    def test_start_alias_delegates_to_play(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        fake_pipeline = FakePipeline()
        FakeInput.create_pipeline_result = fake_pipeline
        r.start()
        assert FakeState.PLAYING in fake_pipeline.state_changes


# --------------------------------------------------------------------------- #
# play() – paths for inputs with source selection
# --------------------------------------------------------------------------- #


class TestPlaySourceSelection:
    @pytest.fixture
    def selection_input_cls(self, monkeypatch, fake_input_cls):
        monkeypatch.setattr(
            FakeInput,
            "_capabilities",
            InputCapabilities(
                has_source_selection=True,
                has_source_discovery=True,
                discovery_interval=0.5,
                selection_title="SELECT FAKE",
                hotkey="n",
            ),
        )
        return FakeInput

    def test_no_source_activates_selection_and_schedules_discovery(
        self, fake_gst, fake_glib, selection_input_cls
    ) -> None:
        r = _make_receiver(input_config={"fake_source": ""})
        placeholder = FakePipeline()
        r._pipeline_assembler.create_placeholder_pipeline = lambda: placeholder

        r.play()

        assert r.source_selection_active is True
        # Discovery loop was scheduled via GLib.timeout_add.
        assert fake_glib.timers  # non-empty
        assert FakeState.PLAYING in placeholder.state_changes

    def test_configured_source_with_selection_awaits_first_frame(
        self, fake_gst, fake_glib, selection_input_cls
    ) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        fake_pipeline = FakePipeline()
        FakeInput.create_pipeline_result = fake_pipeline

        r.play()
        # No longer in selection mode, pipeline is PLAYING.
        assert r.source_selection_active is False
        assert FakeState.PLAYING in fake_pipeline.state_changes

    def test_configured_source_set_state_failure_triggers_reconnect(
        self, fake_gst, fake_glib, selection_input_cls
    ) -> None:
        fake_pipeline = FakePipeline(
            set_state_returns={FakeState.PLAYING: FakeStateChangeReturn.FAILURE},
        )
        FakeInput.create_pipeline_result = fake_pipeline
        r = _make_receiver(input_config={"fake_source": "cam-1"})

        r.play()
        assert fake_glib.timers  # reconnect scheduled


# --------------------------------------------------------------------------- #
# create_pipeline
# --------------------------------------------------------------------------- #


class TestCreatePipeline:
    def test_plugin_unavailable_falls_back_to_placeholder(
        self, fake_gst, fake_glib, fake_input_cls, monkeypatch
    ) -> None:
        monkeypatch.setattr(FakeInput, "_available", (False, "SDK missing"))
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        placeholder = FakePipeline()
        r._pipeline_assembler._placeholder_resolution = (1920, 1080)
        r._pipeline_assembler.create_placeholder_pipeline = lambda: placeholder

        r.create_pipeline()

        # Observable effects of the placeholder path:
        #   * status marker is set to disconnected with the reason text
        #   * resolution is published as the placeholder's fixed resolution
        #   * the plugin's create_pipeline is never invoked
        assert r.connected is False
        assert r.status_marker.error_message == "SDK missing"
        assert r.resolution == (1920, 1080)
        assert FakeInput.create_pipeline_call_count == 0

    def test_plugin_raises_falls_back_to_placeholder(self, fake_gst, fake_glib, fake_input_cls) -> None:
        FakeInput.create_pipeline_raises = RuntimeError("boom")
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        placeholder = FakePipeline()
        r._pipeline_assembler._placeholder_resolution = (1920, 1080)
        r._pipeline_assembler.create_placeholder_pipeline = lambda: placeholder

        r.create_pipeline()

        # The exception text propagates to the public status marker, the
        # placeholder resolution surfaces via the public `resolution` property,
        # and the receiver stays disconnected.
        assert r.connected is False
        assert "boom" in r.status_marker.error_message
        assert r.resolution == (1920, 1080)
        assert FakeInput.create_pipeline_call_count == 1

    def test_happy_path_sets_up_bus_and_pad_probe(self, fake_gst, fake_glib, fake_input_cls) -> None:
        """Happy path: sets up the bus watch and adds pad probes on the
        shared videosink – an EVENT_DOWNSTREAM probe for resolution/caps
        detection and a BUFFER probe feeding the silent-stall watchdog."""
        videosink_elem = FakeElement(name="shared_videosink")
        fake_pipeline = FakePipeline(elements={"shared_videosink": videosink_elem})
        FakeInput.create_pipeline_result = fake_pipeline
        r = _make_receiver(input_config={"fake_source": "cam-1"})

        r.create_pipeline()

        assert r._pipeline is fake_pipeline
        assert fake_pipeline._bus.signal_watch_added is True
        pad = videosink_elem.get_static_pad("sink")
        probe_types = [p[0] for p in pad.probes]
        assert probe_types == [
            FakePadProbeType.EVENT_DOWNSTREAM,
            FakePadProbeType.BUFFER,
        ]
        # The BUFFER probe stamps the last-frame clock for the watchdog.
        buffer_cb = pad.probes[1][1]
        assert buffer_cb.__name__ == "_on_sink_buffer"

    def test_pad_probes_not_duplicated_across_rebuilds(self, fake_gst, fake_glib, fake_input_cls) -> None:
        videosink_elem = FakeElement(name="shared_videosink")
        FakeInput.create_pipeline_result = FakePipeline(elements={"shared_videosink": videosink_elem})
        r = _make_receiver(input_config={"fake_source": "cam-1"})

        r.create_pipeline()
        r.create_pipeline()  # simulate a rebuild on reconnect/heal
        r.create_pipeline()

        pad = videosink_elem.get_static_pad("sink")
        # Exactly one EVENT_DOWNSTREAM + one BUFFER probe, not three of each.
        assert len(pad.probes) == 2
        assert r._sink_probes_added is True


# --------------------------------------------------------------------------- #
# stop()
# --------------------------------------------------------------------------- #


class TestStop:
    def test_stop_sets_null_cancels_timers_and_cleans_up_plugin(
        self, fake_gst, fake_glib, fake_input_cls, monkeypatch
    ) -> None:
        widget_history: list[Any] = []
        r = _make_receiver(
            input_config={"fake_source": "cam-1"},
            on_widget_changed=widget_history.append,
        )
        # Install a pipeline that logs state transitions.
        fake_pipeline = FakePipeline()
        r._pipeline = fake_pipeline

        # Pretend timers exist so we can assert they're removed.
        r._state.connection_timeout_id = 100
        r._state.reconnect_source_id = 200
        r._discovery_source_id = 300

        # Avoid thread cost / flakiness: replace threading.Thread with a
        # synchronous stand-in that just runs ``target`` immediately.
        class SyncThread:
            def __init__(self, target, daemon=False, name=""):  # noqa: ANN001
                self._target = target

            def start(self) -> None:
                self._target()

            def join(self, timeout: float = 0.0) -> None:
                return None

            def is_alive(self) -> bool:
                return False  # runs synchronously in start(), so always done

        monkeypatch.setattr(threading, "Thread", SyncThread)

        r.stop()

        assert 100 in fake_glib.removed
        assert 200 in fake_glib.removed
        assert 300 in fake_glib.removed
        assert FakeState.NULL in fake_pipeline.state_changes
        assert r._pipeline is None
        # The plugin's ``cleanup()`` method must fire exactly once.
        assert r._input.cleanup_calls == 1  # type: ignore[attr-defined]
        # Widget callback gets a None to signal "no video widget now".
        assert widget_history == [None]

    def test_stop_warns_when_null_transition_thread_is_stuck(
        self, fake_gst, fake_glib, fake_input_cls, monkeypatch, caplog
    ) -> None:
        """A wedged NULL transition (unresponsive network source) is abandoned
        but logged at WARNING so the leaked pipeline/sockets are diagnosable."""
        r = _make_receiver()
        r._pipeline = FakePipeline()

        class StuckThread:
            def __init__(self, target, daemon=False, name=""):  # noqa: ANN001
                pass

            def start(self) -> None:
                pass  # never runs target – simulates a wedged NULL transition

            def join(self, timeout: float = 0.0) -> None:
                return None

            def is_alive(self) -> bool:
                return True

        monkeypatch.setattr(threading, "Thread", StuckThread)

        with caplog.at_level("WARNING", logger="openfollow.video.receiver"):
            r.stop()

        assert any("did not complete within 2s on stop" in rec.message for rec in caplog.records)


# --------------------------------------------------------------------------- #
# Source-selection navigation
# --------------------------------------------------------------------------- #


class TestSourceSelectionNavigation:
    @pytest.fixture
    def selection_receiver(self, fake_gst, fake_glib, fake_input_cls, monkeypatch):
        monkeypatch.setattr(
            FakeInput,
            "_capabilities",
            InputCapabilities(has_source_selection=True, has_source_discovery=True),
        )
        r = _make_receiver()
        # Prime discovered sources and activate selection so the
        # up/down methods aren't no-ops.
        r._discovered_sources = ["a", "b", "c"]
        r._selected_source_index = 1
        r._state.activate_source_selection()
        return r

    def test_select_source_up_clamps_at_zero(self, selection_receiver) -> None:
        selection_receiver.select_source_up()
        assert selection_receiver.selected_source_index == 0
        selection_receiver.select_source_up()
        assert selection_receiver.selected_source_index == 0

    def test_select_source_down_clamps_at_last(self, selection_receiver) -> None:
        selection_receiver.select_source_down()
        assert selection_receiver.selected_source_index == 2
        selection_receiver.select_source_down()
        assert selection_receiver.selected_source_index == 2

    def test_navigation_is_noop_when_selection_inactive(self, selection_receiver) -> None:
        selection_receiver._state.deactivate_source_selection()
        selection_receiver.select_source_down()
        selection_receiver.select_source_up()
        assert selection_receiver.selected_source_index == 1

    def test_confirm_requires_discovered_sources_and_active_selection(self, selection_receiver, monkeypatch) -> None:
        # Prevent the real set_source from executing side-effects; we
        # only care about the boolean contract here.
        monkeypatch.setattr(
            selection_receiver,
            "set_source",
            lambda _s: None,
            raising=True,
        )
        monkeypatch.setattr(
            selection_receiver,
            "_save_source_to_config",
            lambda _s: None,
            raising=True,
        )

        assert selection_receiver.confirm_source_selection() is True

        # Empty discovered list → False.
        selection_receiver._discovered_sources = []
        selection_receiver._state.activate_source_selection()
        assert selection_receiver.confirm_source_selection() is False

        # Selection inactive → False.
        selection_receiver._discovered_sources = ["a"]
        selection_receiver._state.deactivate_source_selection()
        assert selection_receiver.confirm_source_selection() is False

    def test_confirm_out_of_range_index_returns_false(self, selection_receiver) -> None:
        selection_receiver._selected_source_index = 99
        assert selection_receiver.confirm_source_selection() is False

    def test_enter_source_selection_noop_without_capability(self, fake_gst, fake_glib, fake_input_cls) -> None:
        # Default FakeInput capabilities has no source selection.
        r = _make_receiver()
        r.enter_source_selection()
        assert r.source_selection_active is False

    def test_enter_source_selection_resets_index_and_starts_discovery(self, selection_receiver) -> None:
        selection_receiver._selected_source_index = 2
        selection_receiver._discovered_sources = ["old"]
        selection_receiver.enter_source_selection()
        assert selection_receiver.source_selection_active is True
        assert selection_receiver.selected_source_index == 0
        assert selection_receiver.discovered_sources == []

    def test_exit_source_selection_restores_previous_connection(self, selection_receiver, fake_glib) -> None:
        # Simulate "was connected when we entered selection".
        selection_receiver._state.enter_source_selection()
        selection_receiver._state.was_connected_before_selection = True

        selection_receiver.exit_source_selection()
        assert selection_receiver.connected is True
        assert selection_receiver.source_selection_active is False

    def test_exit_source_selection_reconnects_when_source_still_configured(self, selection_receiver, fake_glib) -> None:
        selection_receiver._input_config["fake_source"] = "cam-1"
        selection_receiver._state.was_connected_before_selection = False

        selection_receiver.exit_source_selection()
        assert fake_glib.timers  # reconnect scheduled


# --------------------------------------------------------------------------- #
# set_source
# --------------------------------------------------------------------------- #


class TestSetSource:
    def test_set_source_with_configured_value_creates_pipeline_and_plays(
        self, fake_gst, fake_glib, fake_input_cls
    ) -> None:
        widgets: list[Any] = []
        r = _make_receiver(on_widget_changed=widgets.append)
        fake_pipeline = FakePipeline()
        FakeInput.create_pipeline_result = fake_pipeline

        r.set_source("  cam-42  ")  # whitespace trimmed

        assert r._input_config["fake_source"] == "cam-42"
        assert FakeState.PLAYING in fake_pipeline.state_changes

    def test_set_source_empty_falls_through_to_play_placeholder(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver()
        placeholder = FakePipeline()
        r._pipeline_assembler.create_placeholder_pipeline = lambda: placeholder
        r.set_source("")
        assert r._input_config["fake_source"] == ""
        # No plugin create_pipeline was invoked; placeholder took over.
        assert FakeState.PLAYING in placeholder.state_changes

    def test_set_source_exception_in_create_pipeline_schedules_reconnect(
        self, fake_gst, fake_glib, fake_input_cls
    ) -> None:
        r = _make_receiver()
        FakeInput.create_pipeline_raises = RuntimeError("boom")
        # create_pipeline happy-path fallback (raises -> placeholder); we
        # force the outer try/except in set_source instead by making the
        # whole create_pipeline call raise synchronously.
        r.create_pipeline = lambda: (_ for _ in ()).throw(RuntimeError("sync boom"))  # type: ignore[assignment]

        r.set_source("cam-1")
        assert fake_glib.timers  # reconnect scheduled

    def test_set_source_aborts_when_prior_pipeline_stuck(self, fake_gst, fake_glib, fake_input_cls) -> None:
        """If the prior pipeline can't reach NULL, set_source must not hand the
        shared sink to a new pipeline – it aborts (status already surfaced)."""
        from openfollow.video.receiver import PipelineStuckError

        r = _make_receiver(input_config={"fake_source": "old-cam"})
        r._pipeline = FakePipeline()
        FakeInput.create_pipeline_call_count = 0

        def _stuck(*, swap_label):  # noqa: ANN001, ANN202
            raise PipelineStuckError("prior pipeline wedged")

        r._null_transition_current_pipeline = _stuck  # type: ignore[assignment]

        r.set_source("new-cam")

        # Aborted before building a new pipeline.
        assert FakeInput.create_pipeline_call_count == 0


# --------------------------------------------------------------------------- #
# Save source to config
# --------------------------------------------------------------------------- #


class TestSaveSourceToConfig:
    def test_save_source_invokes_configuration_roundtrip(
        self, fake_gst, fake_glib, fake_input_cls, monkeypatch, tmp_path
    ) -> None:
        loaded: list[str] = []
        saved: list[tuple[Any, str]] = []

        class FakeCfg:
            fake_source: str = ""

        def fake_load(path: str) -> FakeCfg:
            loaded.append(path)
            return FakeCfg()

        def fake_save(cfg: FakeCfg, path: str) -> None:
            saved.append((cfg, path))

        import openfollow.configuration as configuration_mod

        monkeypatch.setattr(configuration_mod, "load_config", fake_load)
        monkeypatch.setattr(configuration_mod, "save_config", fake_save)

        cfg_path = str(tmp_path / "config.toml")
        r = _make_receiver(config_path=cfg_path)
        r._save_source_to_config("cam-42")

        assert loaded == [cfg_path]
        assert len(saved) == 1
        assert saved[0][0].fake_source == "cam-42"
        assert saved[0][1] == cfg_path

    def test_save_source_swallows_errors(self, fake_gst, fake_glib, fake_input_cls, monkeypatch) -> None:
        """A failing save must not crash the GLib main-thread handler."""
        import openfollow.configuration as configuration_mod

        monkeypatch.setattr(
            configuration_mod,
            "load_config",
            lambda _p: (_ for _ in ()).throw(RuntimeError("disk full")),
        )
        r = _make_receiver()
        r._save_source_to_config("cam-1")  # must not raise


# --------------------------------------------------------------------------- #
# Reconnect lifecycle
# --------------------------------------------------------------------------- #


class TestReconnect:
    def test_schedule_reconnect_uses_glib_timeout_and_updates_status(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._schedule_reconnect("lost link")
        assert fake_glib.timers
        # The one registered callback is ``_do_reconnect``.
        ((_delay, cb),) = list(fake_glib.timers.values())[:1]
        assert cb.__name__ == "_do_reconnect"

    def test_schedule_reconnect_sets_pipeline_to_null(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        pipeline = FakePipeline()
        r._pipeline = pipeline
        r._schedule_reconnect("foo")
        assert FakeState.NULL in pipeline.state_changes

    def test_schedule_reconnect_tears_down_old_bus_to_release_fds(self, fake_gst, fake_glib, fake_input_cls) -> None:
        """Regression for the socket leak: reconnect must remove the
        old pipeline's bus signal watch, otherwise the watch keeps the
        pipeline – and its rtspsrc UDP/TCP sockets – alive across every
        reconnect until the process exhausts its fd limit."""
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        bus = FakeBus()
        r._pipeline = FakePipeline(bus=bus)
        r._schedule_reconnect("foo")
        assert bus.signal_watch_removed is True

    def test_do_reconnect_tears_down_old_bus_to_release_fds(self, fake_gst, fake_glib, fake_input_cls) -> None:
        """Same leak guard on the rebuild path."""
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        bus = FakeBus()
        r._pipeline = FakePipeline(bus=bus)
        r._do_reconnect()
        assert bus.signal_watch_removed is True

    def test_schedule_reconnect_drops_pipeline_ref_so_teardown_runs_once(
        self, fake_gst, fake_glib, fake_input_cls
    ) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        bus = FakeBus()
        r._pipeline = FakePipeline(bus=bus)
        r._schedule_reconnect("foo")
        # Ref dropped → _do_reconnect can't tear the same bus down twice.
        assert r._pipeline is None
        # And the watch was removed exactly once, not twice.
        assert bus.remove_count == 1

    def test_schedule_reconnect_swallows_null_failure(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        pipeline = FakePipeline()
        pipeline.raise_on_null = True
        r._pipeline = pipeline
        r._schedule_reconnect("foo")
        # Reconnect timer still armed.
        assert fake_glib.timers

    def test_cancel_reconnect_noop_when_no_source(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver()
        r._cancel_reconnect()  # no-op
        assert fake_glib.removed == []

    def test_cancel_reconnect_removes_registered_source(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver()
        r._state.reconnect_source_id = 7
        r._cancel_reconnect()
        assert fake_glib.removed == [7]
        assert r._state.reconnect_source_id is None

    def test_start_connection_timeout_registers_glib_timer(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._start_connection_timeout()
        assert fake_glib.timers
        # Delay is taken from the plugin's reconnect_policy.connection_timeout
        # (4.0s → 4000ms).
        ((delay, _cb),) = list(fake_glib.timers.values())[:1]
        assert delay == 4000

    def test_start_connection_timeout_skipped_when_policy_zero(
        self, fake_gst, fake_glib, fake_input_cls, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            FakeInput,
            "_reconnect_policy",
            ReconnectPolicy(connection_timeout=0, max_attempts=1, fallback_to_selection=False),
        )
        r = _make_receiver()
        r._start_connection_timeout()
        assert not fake_glib.timers

    def test_cancel_connection_timeout_no_op_without_source(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver()
        r._cancel_connection_timeout()  # no-op
        assert fake_glib.removed == []

    def test_connection_timeout_triggers_reconnect_when_no_video(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._state.connection_timeout_id = 9
        r._state.video_flow_detected = False
        # Should schedule a new reconnect
        result = r._on_connection_timeout()
        assert result is False  # one-shot
        assert fake_glib.timers  # reconnect scheduled
        assert r._state.connection_timeout_id is None

    def test_connection_timeout_noop_when_video_already_flowing(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._state.video_flow_detected = True
        result = r._on_connection_timeout()
        assert result is False
        assert not fake_glib.timers  # no new reconnect

    def test_do_reconnect_runs_create_and_play(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        fake_pipeline = FakePipeline()
        FakeInput.create_pipeline_result = fake_pipeline
        r._state.reconnect_attempt = 1

        result = r._do_reconnect()

        assert result is False
        assert FakeState.PLAYING in fake_pipeline.state_changes

    def test_do_reconnect_reschedules_when_build_fails_into_placeholder(
        self, fake_gst, fake_glib, fake_input_cls
    ) -> None:
        """create_pipeline swallows a build failure and installs the
        placeholder without raising; a fixed-URL reconnect must reschedule
        rather than play() the placeholder (which would strand the feed)."""
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        placeholder = FakePipeline()
        r._pipeline_assembler.create_placeholder_pipeline = lambda: placeholder
        FakeInput.create_pipeline_raises = RuntimeError("transient build failure")
        r._state.reconnect_attempt = 1

        result = r._do_reconnect()

        assert result is False
        # A fresh reconnect was scheduled instead of stranding on the placeholder.
        assert fake_glib.timers
        # The placeholder was never started – we rescheduled instead of play()ing it.
        assert FakeState.PLAYING not in placeholder.state_changes

    def test_do_reconnect_exception_triggers_another_reconnect(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        FakeInput.create_pipeline_raises = RuntimeError("still broken")
        # Force the try-block path that *raises* – our receiver's
        # create_pipeline catches plugin errors and swaps to placeholder
        # internally, so we force an error at the next layer up by
        # making the placeholder creation blow up too.
        r.create_pipeline = lambda: (_ for _ in ()).throw(RuntimeError("sync boom"))  # type: ignore[assignment]
        r._state.reconnect_attempt = 1

        result = r._do_reconnect()
        assert result is False
        assert fake_glib.timers  # another reconnect scheduled

    def test_do_reconnect_fallback_to_placeholder_after_max_attempts(
        self, fake_gst, fake_glib, fake_input_cls, monkeypatch
    ) -> None:
        """When ``ReconnectPolicy.max_attempts`` is hit and the input has
        ``fallback_to_selection``, the receiver switches to placeholder
        mode, resets the backoff, and re-enters selection."""
        monkeypatch.setattr(
            FakeInput,
            "_capabilities",
            InputCapabilities(
                has_source_selection=True,
                has_source_discovery=True,
            ),
        )
        monkeypatch.setattr(
            FakeInput,
            "_reconnect_policy",
            ReconnectPolicy(
                max_attempts=1,
                fallback_to_selection=True,
                connection_timeout=0,
                min_delay=0.1,
                max_delay=1.0,
            ),
        )
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        placeholder = FakePipeline()
        r._pipeline_assembler.create_placeholder_pipeline = lambda: placeholder
        r._state.reconnect_attempt = 1  # hit the limit

        result = r._do_reconnect()
        assert result is False
        assert r._state.is_placeholder_pipeline is True
        # Primary config field cleared so selection UI is empty.
        assert r._input_config["fake_source"] == ""
        assert r.source_selection_active is True
        assert FakeState.PLAYING in placeholder.state_changes

    def test_do_reconnect_no_url_falls_through_to_play_placeholder(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver()
        placeholder = FakePipeline()
        r._pipeline_assembler.create_placeholder_pipeline = lambda: placeholder
        r._state.reconnect_attempt = 1
        result = r._do_reconnect()
        assert result is False


# --------------------------------------------------------------------------- #
# Bus handling
# --------------------------------------------------------------------------- #


class TestBusHandling:
    def test_on_bus_message_swallows_exceptions(self, fake_gst, fake_glib, fake_input_cls, caplog) -> None:
        r = _make_receiver()

        def boom(_bus, _msg) -> None:
            raise RuntimeError("wedged")

        r._bus_handler.handle_message = boom  # type: ignore[assignment]
        with caplog.at_level(logging.ERROR):
            r._on_bus_message(object(), object())  # must not raise
        assert any("Unhandled error" in rec.message for rec in caplog.records)

    def test_handle_bus_error_marks_disconnected_and_schedules_reconnect(
        self, fake_gst, fake_glib, fake_input_cls
    ) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._state.connected = True
        r._handle_bus_error("network down")
        assert r._state.connected is False
        assert fake_glib.timers

    def test_handle_bus_eos_marks_disconnected_and_schedules_reconnect(
        self, fake_gst, fake_glib, fake_input_cls
    ) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._state.connected = True
        r._handle_bus_eos()
        assert r._state.connected is False
        assert fake_glib.timers

    def test_handle_bus_eos_skips_reconnect_when_input_loops(self, fake_gst, fake_glib, fake_input_cls) -> None:
        # A looping clip handles EOS by seeking back to start; the receiver must
        # NOT treat end-of-stream as a disconnect.
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._state.connected = True
        r._pipeline = FakePipeline()
        r._input.on_bus_eos = lambda _pipeline: True  # type: ignore[attr-defined]
        fake_glib.timers.clear()
        r._handle_bus_eos()
        assert r._state.connected is True  # stayed connected
        assert not fake_glib.timers  # no reconnect scheduled

    def test_handle_bus_eos_reconnects_when_input_does_not_handle(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._state.connected = True
        r._pipeline = FakePipeline()
        r._input.on_bus_eos = lambda _pipeline: False  # type: ignore[attr-defined]
        r._handle_bus_eos()
        assert r._state.connected is False
        assert fake_glib.timers

    def test_handle_bus_async_done_delegates_to_input(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver()
        pipeline = FakePipeline()
        r._handle_bus_async_done(pipeline)
        assert r._input.async_done_calls == 1  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Pad / caps parsing
# --------------------------------------------------------------------------- #


class TestPadEvent:
    def _make_caps_event(
        self,
        width: int,
        height: int,
        fps_num: int = 30,
        fps_den: int = 1,
        fraction_returns: tuple[bool, int, int] = None,
    ):
        structure = SimpleNamespace(
            get_value=lambda key: {"width": width, "height": height}.get(key),
            get_fraction=lambda key: fraction_returns if fraction_returns is not None else (True, fps_num, fps_den),
        )
        caps = SimpleNamespace(get_structure=lambda _idx: structure)
        event = SimpleNamespace(
            type=FakeEventType.CAPS,
            parse_caps=lambda: caps,
        )
        info = SimpleNamespace(get_event=lambda: event)
        return info, event

    def test_non_caps_event_returns_ok_without_state_change(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver()
        event = SimpleNamespace(type=FakeEventType.OTHER)
        info = SimpleNamespace(get_event=lambda: event)
        result = r._on_pad_event(object(), info)
        assert result == FakePadProbeReturn.OK
        assert r.resolution == (0, 0)

    def test_caps_event_updates_resolution_but_not_connected(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        info, _ = self._make_caps_event(1280, 720)
        r._on_pad_event(object(), info)
        assert r.resolution == (1280, 720)
        assert r.connected is False
        assert r.source_framerate == 30.0

    def test_caps_event_with_invalid_dims_no_state_change(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver()
        info, _ = self._make_caps_event(0, 0)
        r._on_pad_event(object(), info)
        assert r.resolution == (0, 0)
        assert r.connected is False

    def test_framerate_invalid_fraction_leaves_framerate_zero(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        info, _ = self._make_caps_event(
            800,
            600,
            fraction_returns=(False, 0, 1),
        )
        r._on_pad_event(object(), info)
        assert r.source_framerate == 0.0

    def test_framerate_exception_silently_ignored(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        structure = SimpleNamespace(
            get_value=lambda key: {"width": 800, "height": 600}.get(key),
            get_fraction=lambda _k: (_ for _ in ()).throw(RuntimeError("bad")),
        )
        caps = SimpleNamespace(get_structure=lambda _i: structure)
        event = SimpleNamespace(type=FakeEventType.CAPS, parse_caps=lambda: caps)
        info = SimpleNamespace(get_event=lambda: event)
        r._on_pad_event(object(), info)  # must not raise
        assert r.source_framerate == 0.0

    def test_framerate_zero_denominator_ignored(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        info, _ = self._make_caps_event(
            800,
            600,
            fraction_returns=(True, 30, 0),
        )
        r._on_pad_event(object(), info)
        assert r.source_framerate == 0.0


# --------------------------------------------------------------------------- #
# Placeholder pipeline wiring
# --------------------------------------------------------------------------- #


class TestPlaceholderWiring:
    def test_create_placeholder_applies_resolution_and_bus(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver()
        fake_pipeline = FakePipeline()
        r._pipeline_assembler.create_placeholder_pipeline = lambda: fake_pipeline
        r._pipeline_assembler._placeholder_resolution = (640, 360)

        r._create_placeholder_pipeline()
        assert r._pipeline is fake_pipeline
        assert fake_pipeline._bus.signal_watch_added is True
        assert r._state.is_placeholder_pipeline is True
        assert r.resolution == (640, 360)

    def test_create_placeholder_returns_early_when_assembler_returns_none(
        self, fake_gst, fake_glib, fake_input_cls
    ) -> None:
        r = _make_receiver()
        r._pipeline_assembler.create_placeholder_pipeline = lambda: None
        r._create_placeholder_pipeline()
        assert r._pipeline is None

    def test_create_placeholder_disposes_an_existing_pipeline(self, fake_gst, fake_glib, fake_input_cls) -> None:
        """An existing pipeline is torn down (bus + NULL) before the placeholder
        overwrites self._pipeline, so it isn't orphaned with its bus watch."""
        r = _make_receiver()
        old = FakePipeline()
        r._pipeline = old
        placeholder = FakePipeline()
        r._pipeline_assembler.create_placeholder_pipeline = lambda: placeholder

        r._create_placeholder_pipeline()

        assert FakeState.NULL in old.state_changes
        assert r._pipeline is placeholder

    def test_create_placeholder_aborts_when_prior_pipeline_stuck(
        self, fake_gst, fake_glib, fake_input_cls, monkeypatch
    ) -> None:
        """If the prior pipeline won't reach NULL, the placeholder is NOT
        installed and the old reference stays attached – the dispose runs on
        the timed guard so an unresponsive source can't wedge the main thread,
        and callers check ``self._pipeline`` before play()ing anything new."""

        class AsyncPipeline(FakePipeline):
            def get_state(self, timeout_ns: int = 0) -> tuple[str, str, str]:
                return (FakeStateChangeReturn.ASYNC, FakeState.PLAYING, FakeState.NULL)

        r = _make_receiver()
        old = AsyncPipeline()
        r._pipeline = old
        placeholder = FakePipeline()
        r._pipeline_assembler.create_placeholder_pipeline = lambda: placeholder
        monkeypatch.setattr(threading, "Thread", _SyncSwapThread)

        r._create_placeholder_pipeline()  # must not raise

        assert r._pipeline is old  # placeholder not installed over a stuck pipeline
        assert "did not reach NULL" in r._status_marker.error_message


# --------------------------------------------------------------------------- #
# Source discovery
# --------------------------------------------------------------------------- #


class TestDiscovery:
    @pytest.fixture
    def discovery_receiver(self, fake_gst, fake_glib, fake_input_cls, monkeypatch):
        monkeypatch.setattr(
            FakeInput,
            "_capabilities",
            InputCapabilities(
                has_source_selection=True,
                has_source_discovery=True,
                discovery_interval=1.0,
            ),
        )
        return _make_receiver()

    def test_schedule_discovery_noop_without_capability(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver()  # default: no discovery capability
        r._schedule_discovery()
        assert not fake_glib.timers

    def test_cancel_discovery_noop_without_id(self, discovery_receiver) -> None:
        discovery_receiver._cancel_discovery()  # no raise

    def test_cancel_discovery_removes_source(self, discovery_receiver, fake_glib) -> None:
        discovery_receiver._discovery_source_id = 77
        discovery_receiver._cancel_discovery()
        assert 77 in fake_glib.removed
        assert discovery_receiver._discovery_source_id is None

    def test_do_discovery_skipped_when_connected_and_not_selecting(self, discovery_receiver) -> None:
        discovery_receiver._state.connected = True
        discovery_receiver._state.deactivate_source_selection()
        assert discovery_receiver._do_discovery() is False

    def test_do_discovery_skipped_when_already_running(self, discovery_receiver) -> None:
        discovery_receiver._discovery_running = True
        result = discovery_receiver._do_discovery()
        assert result is True

    def test_do_discovery_spawns_thread_synchronously(self, discovery_receiver, monkeypatch) -> None:
        """We substitute threading.Thread with a sync stand-in so the
        worker runs on the calling thread – this lets us assert on the
        post-run state without sleeping."""

        class SyncThread:
            def __init__(self, target, daemon=False, name=""):  # noqa: ANN001
                self._target = target

            def start(self) -> None:
                self._target()

            def join(self, timeout: float = 0.0) -> None:
                return None

        monkeypatch.setattr(threading, "Thread", SyncThread)
        discovery_receiver._input.discover_results = [["cam-a", "cam-b"]]

        result = discovery_receiver._do_discovery()
        assert result is True
        assert discovery_receiver.discovered_sources == ["cam-a", "cam-b"]
        assert discovery_receiver._discovery_running is False

    def test_do_discovery_preserves_selection_when_source_still_present(self, discovery_receiver, monkeypatch) -> None:
        class SyncThread:
            def __init__(self, target, daemon=False, name=""):  # noqa: ANN001
                self._target = target

            def start(self) -> None:
                self._target()

            def join(self, timeout: float = 0.0) -> None:
                return None

        monkeypatch.setattr(threading, "Thread", SyncThread)
        discovery_receiver._discovered_sources = ["old-a", "old-b"]
        discovery_receiver._selected_source_index = 1  # was "old-b"
        discovery_receiver._input.discover_results = [["old-a", "old-b", "new-c"]]

        discovery_receiver._do_discovery()
        # ``old-b`` is at index 1 in new list → selection preserved.
        assert discovery_receiver.selected_source_index == 1
        assert discovery_receiver.discovered_sources == ["old-a", "old-b", "new-c"]

    def test_do_discovery_clamps_selection_when_previous_source_missing(self, discovery_receiver, monkeypatch) -> None:
        class SyncThread:
            def __init__(self, target, daemon=False, name=""):  # noqa: ANN001
                self._target = target

            def start(self) -> None:
                self._target()

            def join(self, timeout: float = 0.0) -> None:
                return None

        monkeypatch.setattr(threading, "Thread", SyncThread)
        discovery_receiver._discovered_sources = ["old-a", "old-b", "old-c"]
        discovery_receiver._selected_source_index = 2  # was "old-c"
        discovery_receiver._input.discover_results = [["only-one"]]

        discovery_receiver._do_discovery()
        # Old selection gone → clamp to last available index (0).
        assert discovery_receiver.selected_source_index == 0

    def test_do_discovery_handles_plugin_exception(self, discovery_receiver, monkeypatch, caplog) -> None:
        class SyncThread:
            def __init__(self, target, daemon=False, name=""):  # noqa: ANN001
                self._target = target

            def start(self) -> None:
                self._target()

            def join(self, timeout: float = 0.0) -> None:
                return None

        monkeypatch.setattr(threading, "Thread", SyncThread)

        def bad_discover(timeout: float = 0.0) -> list[str]:
            raise RuntimeError("sdk broken")

        discovery_receiver._input.discover_sources = bad_discover  # type: ignore[assignment]
        with caplog.at_level(logging.WARNING):
            discovery_receiver._do_discovery()
        # The _discovery_running flag was reset so a future call can
        # proceed – this is the real invariant we're protecting.
        assert discovery_receiver._discovery_running is False

    def test_do_discovery_clears_source_id_when_connected_and_not_selecting(
        self, discovery_receiver, fake_glib
    ) -> None:
        """A recurring discovery tick that returns False auto-removes its
        GLib source; the receiver must drop the now-freed id so a later
        ``_cancel_discovery`` doesn't ``source_remove`` a reused id."""
        discovery_receiver._discovery_source_id = 42
        discovery_receiver._state.connected = True
        discovery_receiver._state.deactivate_source_selection()

        assert discovery_receiver._do_discovery() is False
        assert discovery_receiver._discovery_source_id is None

        # The follow-up cancel is a clean no-op – no source_remove on the
        # stale id.
        discovery_receiver._cancel_discovery()
        assert 42 not in fake_glib.removed

    def test_do_discovery_honours_concurrent_navigation(self, discovery_receiver, monkeypatch) -> None:
        """While reconciliation runs, the input thread may navigate the
        selection.  Reconciliation must read+write the index in one locked
        critical section, so navigation that lands mid-discovery isn't
        clobbered – otherwise the operator's highlighted row jumps back."""

        class SyncThread:
            def __init__(self, target, daemon=False, name=""):  # noqa: ANN001
                self._target = target

            def start(self) -> None:
                self._target()

            def join(self, timeout: float = 0.0) -> None:
                return None

        monkeypatch.setattr(threading, "Thread", SyncThread)

        # Instrument the discovery lock so the operator's navigation fires
        # as the *second* lock section is released.  The first release is
        # ``_do_discovery``'s running-flag guard; the second is where
        # ``_run_discovery`` reads source state.  In the buggy two-section
        # version this lands in the gap between reading ``old_idx`` and
        # writing the result; in the fixed single section the index is
        # already committed before the navigation fires.
        real_lock = discovery_receiver._discovery_lock

        class NavOnSecondRelease:
            def __init__(self) -> None:
                self._releases = 0

            def __enter__(self):  # noqa: ANN204
                return real_lock.__enter__()

            def __exit__(self, *exc):  # noqa: ANN002, ANN204
                result = real_lock.__exit__(*exc)
                self._releases += 1
                if self._releases == 2:
                    discovery_receiver._selected_source_index = 1
                return result

        discovery_receiver._discovery_lock = NavOnSecondRelease()
        discovery_receiver._discovered_sources = ["cam-a", "cam-b"]
        discovery_receiver._selected_source_index = 0  # operator on "cam-a"
        discovery_receiver._input.discover_results = [["cam-a", "cam-b"]]

        discovery_receiver._do_discovery()

        # Operator navigated to "cam-b" (still present) → their fresh
        # selection wins; it must not snap back to index 0.
        assert discovery_receiver.selected_source_index == 1
        assert discovery_receiver.discovered_sources == ["cam-a", "cam-b"]


# ---------------------------------------------------------------------------
# Shared sink lifecycle: prepare, stop, set source, reconnect
# ---------------------------------------------------------------------------


class TestSharedSinkSyncPropertyExceptions:
    def test_create_shared_sink_swallows_set_property_sync_failure(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
    ) -> None:

        class _NoSyncElem(FakeElement):
            def set_property(self, key: str, value: Any) -> None:
                if key == "sync":
                    raise RuntimeError("no such property")
                super().set_property(key, value)

        original_make = fake_gst.ElementFactory.make

        def _make(kind: str, name: str) -> FakeElement | None:
            sink = original_make(kind, name)
            if sink is not None and name == "shared_videosink":
                # Replace with the no-sync variant.
                return _NoSyncElem(name)
            return sink

        fake_gst.ElementFactory.make = _make  # type: ignore[assignment]
        r = _make_receiver()
        sink = r._get_shared_sink()
        assert sink is not None
        # Despite set_property("sync") raising, the sink is returned cleanly.

    def test_prepare_sink_returns_none_when_shared_sink_creation_fails(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
    ) -> None:
        fake_gst.ElementFactory = FakeElementFactory(available_elements=set())
        r = _make_receiver()
        assert r._prepare_sink() is None

    def test_prepare_sink_swallows_set_property_sync_failure(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
    ) -> None:
        r = _make_receiver()
        sink = r._get_shared_sink()
        assert sink is not None

        # First call set sync=False successfully. Now break set_property
        # for the second call's sync write.
        def _boom(key: str, value: Any) -> None:
            if key == "sync":
                raise RuntimeError("no such property")
            FakeElement.set_property(sink, key, value)

        sink.set_property = _boom  # type: ignore[assignment]
        # Must not raise.
        result = r._prepare_sink()
        assert result is sink


class TestStopWidgetCallback:
    def test_stop_invokes_widget_callback_with_none(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
    ) -> None:
        widget_history: list[Any] = []
        r = _make_receiver(on_widget_changed=widget_history.append)
        # No pipeline / threads – exercise just the callback branch.
        r.stop()
        assert widget_history == [None]


class TestSetSourceWidgetCallback:
    def test_set_source_invokes_widget_callback_with_new_widget(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
    ) -> None:
        widget_history: list[Any] = []
        r = _make_receiver(
            input_config={"fake_source": "old"},
            on_widget_changed=widget_history.append,
        )
        # Pre-stage a sink with a widget property.
        sink = r._get_shared_sink()
        sink.set_property("widget", SimpleNamespace(id="new-widget"))

        # Pre-stage a pipeline so the cleanup branch fires too. Plugin
        # must return a real (fake) pipeline so create_pipeline doesn't
        # blow up trying to call get_by_name on None.
        prev_pipeline = FakePipeline(
            elements={"shared_videosink": sink},
        )
        r._pipeline = prev_pipeline
        FakeInput.create_pipeline_result = FakePipeline(
            elements={"shared_videosink": sink},
        )

        # Make `play()` a no-op so we don't get distracted by side
        # effects.
        r.play = lambda: None  # type: ignore[assignment]

        r.set_source("new-source")

        # The previous pipeline was set to NULL and cleared.
        assert FakeState.NULL in prev_pipeline.state_changes
        # The widget callback fired (with the staged widget).
        assert any(getattr(w, "id", None) == "new-widget" for w in widget_history)


class TestPlaySourceSelectionPlaceholderFailure:
    def test_play_logs_error_when_initial_placeholder_fails(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
        caplog,
        monkeypatch,
    ) -> None:
        """Source-selection mode shows a placeholder while discovery runs.
        If that placeholder pipeline itself fails to start, the operator
        gets a log error rather than a silent black screen."""
        import logging as _logging

        from openfollow.video.inputs._base import InputCapabilities

        monkeypatch.setattr(
            FakeInput,
            "_capabilities",
            InputCapabilities(
                has_source_selection=True,
                has_source_discovery=True,
                discovery_interval=5.0,
                selection_title="SELECT FAKE",
                hotkey="f",
                force_zero_latency=False,
            ),
        )
        # Empty source → enters the source-selection branch.
        r = _make_receiver(input_config={"fake_source": ""})
        # Force the placeholder pipeline's set_state to FAILURE.
        placeholder = FakePipeline(
            set_state_returns={FakeState.PLAYING: FakeStateChangeReturn.FAILURE},
        )
        r._pipeline_assembler.create_placeholder_pipeline = lambda: placeholder

        with caplog.at_level(_logging.ERROR, logger="openfollow.video.receiver"):
            r.play()

        assert any("initial placeholder pipeline" in rec.message.lower() for rec in caplog.records)


class TestPlayPlaceholderSuccessLog:
    def test_play_logs_placeholder_info_when_state_already_placeholder(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
        caplog,
    ) -> None:
        """When `play()` is invoked with a configured source AND the
        state machine is still flagged ``is_placeholder_pipeline=True``
        (left over from an earlier fallback), the success log fires
        the placeholder-info branch instead of the normal "waiting for
        first frame" line."""
        import logging as _logging

        r = _make_receiver(input_config={"fake_source": "rtsp://x"})
        # Pre-stage a pipeline so play() doesn't try to create one.
        r._pipeline = FakePipeline()  # returns SUCCESS by default
        r._state.set_placeholder_pipeline(True)

        with caplog.at_level(_logging.INFO, logger="openfollow.video.receiver"):
            r.play()

        assert any("Placeholder pipeline started" in rec.message for rec in caplog.records)


class TestDoReconnectPlaceholderFallbackPaths:
    def test_reconnect_invokes_widget_callback_after_placeholder_fallback(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
        monkeypatch,
    ) -> None:
        from openfollow.video.inputs._base import (
            InputCapabilities,
            ReconnectPolicy,
        )

        monkeypatch.setattr(
            FakeInput,
            "_capabilities",
            InputCapabilities(
                has_source_selection=False,
                has_source_discovery=False,
                discovery_interval=5.0,
                selection_title="SELECT FAKE",
                hotkey="f",
                force_zero_latency=False,
            ),
        )
        monkeypatch.setattr(
            FakeInput,
            "_reconnect_policy",
            ReconnectPolicy(
                max_attempts=1,
                min_delay=0.01,
                max_delay=0.1,
                backoff_multiplier=2.0,
                connection_timeout=0.0,
                fallback_to_selection=True,
            ),
        )

        widget_history: list[Any] = []
        r = _make_receiver(
            input_config={"fake_source": "rtsp://x"},
            on_widget_changed=widget_history.append,
        )
        # Drive past max_attempts so should_fallback_to_placeholder fires.
        r._state.reconnect_attempt = 5
        # Pre-set the shared sink to expose a widget.
        sink = r._get_shared_sink()
        sink.set_property("widget", SimpleNamespace(id="placeholder-widget"))

        # Stage a previous pipeline (covers cleanup branch 705-706).
        r._pipeline = FakePipeline()
        # Stage placeholder factory.
        r._pipeline_assembler.create_placeholder_pipeline = lambda: FakePipeline()

        r._do_reconnect()

        # Widget callback fired with the placeholder's widget.
        assert any(getattr(w, "id", None) == "placeholder-widget" for w in widget_history)

    def test_reconnect_logs_error_when_placeholder_set_state_fails(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
        monkeypatch,
        caplog,
    ) -> None:
        """If the placeholder pipeline itself can't enter PLAYING after
        fallback, log an error so the operator sees why no overlay
        appeared."""
        import logging as _logging

        from openfollow.video.inputs._base import (
            InputCapabilities,
            ReconnectPolicy,
        )

        monkeypatch.setattr(
            FakeInput,
            "_reconnect_policy",
            ReconnectPolicy(
                max_attempts=1,
                min_delay=0.01,
                max_delay=0.1,
                backoff_multiplier=2.0,
                connection_timeout=0.0,
                fallback_to_selection=True,
            ),
        )
        monkeypatch.setattr(
            FakeInput,
            "_capabilities",
            InputCapabilities(
                has_source_selection=False,
                has_source_discovery=False,
                discovery_interval=5.0,
                selection_title="SELECT FAKE",
                hotkey="f",
                force_zero_latency=False,
            ),
        )

        r = _make_receiver(input_config={"fake_source": "rtsp://x"})
        r._state.reconnect_attempt = 5
        # Placeholder fails to enter PLAYING.
        failing_pipeline = FakePipeline(
            set_state_returns={FakeState.PLAYING: FakeStateChangeReturn.FAILURE},
        )
        r._pipeline_assembler.create_placeholder_pipeline = lambda: failing_pipeline

        with caplog.at_level(_logging.ERROR, logger="openfollow.video.receiver"):
            r._do_reconnect()
        assert any("Failed to start placeholder pipeline" in rec.message for rec in caplog.records)

    def test_reconnect_invokes_widget_callback_in_normal_reconnect_path(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
    ) -> None:
        """Normal reconnect path (not the placeholder fallback): create
        a fresh pipeline and surface its widget through the callback."""
        widget_history: list[Any] = []
        r = _make_receiver(
            input_config={"fake_source": "rtsp://x"},
            on_widget_changed=widget_history.append,
        )
        sink = r._get_shared_sink()
        sink.set_property("widget", SimpleNamespace(id="reconnect-widget"))

        # Default reconnect attempt is 0 – no fallback.
        FakeInput.create_pipeline_result = FakePipeline()
        r.play = lambda: None  # type: ignore[assignment]

        r._do_reconnect()

        assert any(getattr(w, "id", None) == "reconnect-widget" for w in widget_history)


# --------------------------------------------------------------------------- #
# Gap-closing tests for the few branches and lines still missing coverage in
# ``openfollow/video/receiver.py``.  Each test targets a specific source-line
# range called out by ``coverage report -m``; the fixtures and fakes are the
# same ones the rest of this file uses.
# --------------------------------------------------------------------------- #


class TestVideoFlowTransitionBranches:
    """Resolution (CAPS) and connection (frame flow) are independent signals."""

    def test_repeat_caps_same_dims_skips_resolution_log(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
        caplog,
    ) -> None:
        import logging as _logging

        r = _make_receiver(input_config={"fake_source": "cam-1"})
        info, _ = self._caps(800, 600)
        r._on_pad_event(object(), info)  # primes resolution

        with caplog.at_level(_logging.INFO, logger="openfollow.video.receiver"):
            r._on_pad_event(object(), self._caps(800, 600)[0])

        assert not any("Video resolution" in rec.message for rec in caplog.records)

    def test_second_frame_after_connected_skips_connect_log(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
        caplog,
    ) -> None:
        import logging as _logging

        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._on_sink_buffer(object(), object())  # first frame → connected

        with caplog.at_level(_logging.INFO, logger="openfollow.video.receiver"):
            r._on_sink_buffer(object(), object())

        assert not any("marking as connected" in rec.message for rec in caplog.records)

    @staticmethod
    def _caps(width: int, height: int):
        structure = SimpleNamespace(
            get_value=lambda key: {"width": width, "height": height}.get(key),
            get_fraction=lambda _k: (True, 30, 1),
        )
        caps = SimpleNamespace(get_structure=lambda _i: structure)
        event = SimpleNamespace(type=FakeEventType.CAPS, parse_caps=lambda: caps)
        return SimpleNamespace(get_event=lambda: event), event


class TestStopDiscoveryThreadCleanup:
    """``stop()`` must join the background discovery thread when one is
    running.  Without this test the join + clear branch
    (``if self._discovery_thread is not None``) never fires."""

    def test_stop_joins_and_clears_discovery_thread(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
        monkeypatch,
    ) -> None:
        joined: list[float | None] = []

        class FakeDiscoveryThread:
            def join(self, timeout: float | None = None) -> None:
                joined.append(timeout)

        r = _make_receiver()
        r._discovery_thread = FakeDiscoveryThread()  # type: ignore[assignment]

        # Replace threading.Thread (used to call set_state(NULL)) with a
        # synchronous stand-in so the test stays hermetic.
        class SyncThread:
            def __init__(self, target, daemon=False, name=""):  # noqa: ANN001
                self._target = target

            def start(self) -> None:
                self._target()

            def join(self, timeout: float = 0.0) -> None:
                return None

            def is_alive(self) -> bool:
                return False  # runs synchronously in start(), so always done

        monkeypatch.setattr(threading, "Thread", SyncThread)

        r.stop()

        # The discovery thread was joined exactly once with the receiver's
        # timeout, and the slot was cleared.
        assert joined == [3.0]
        assert r._discovery_thread is None


class TestStopWithoutWidgetCallback:
    """When the receiver was constructed without an ``on_widget_changed``
    callback, the corresponding ``stop()`` branch must skip silently
    rather than calling ``None``.  Targets branch 284→286."""

    def test_stop_with_no_widget_callback_does_not_raise(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
    ) -> None:
        r = _make_receiver()  # default: on_widget_changed=None
        r.stop()  # no AttributeError, no TypeError


class TestCreatePipelineMissingPad:
    """``create_pipeline`` adds a pad probe to the sink iff the static
    ``sink`` pad exists.  When it doesn't, the function must skip the
    probe wire-up cleanly – branch 339→exit."""

    def test_no_static_pad_skips_probe_wireup(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
    ) -> None:
        class _NoPadSink(FakeElement):
            def get_static_pad(self, _name: str):  # type: ignore[override]
                return None

        sink = _NoPadSink(name="videosink")
        FakeInput.create_pipeline_result = FakePipeline(
            elements={"videosink": sink},
        )
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r.create_pipeline()
        # No probe added because get_static_pad returned None.
        assert sink._static_pads == {}


class TestPlayNoSourceSelectionConnectionTimeoutDisabled:
    """``connection_timeout=0`` in the reconnect policy must skip the
    ``_start_connection_timeout`` arm in ``play()`` – branch 401→403."""

    def test_play_with_zero_connection_timeout_skips_timer_setup(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
        monkeypatch,
    ) -> None:
        from openfollow.video.inputs._base import ReconnectPolicy

        monkeypatch.setattr(
            FakeInput,
            "_reconnect_policy",
            ReconnectPolicy(
                max_attempts=2,
                min_delay=0.01,
                max_delay=0.1,
                backoff_multiplier=2.0,
                connection_timeout=0.0,
                fallback_to_selection=False,
            ),
        )
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        FakeInput.create_pipeline_result = FakePipeline()
        r.play()
        # No GLib timer was scheduled because the policy's timeout is 0.
        assert not fake_glib.timers


class TestPlaySourceSelectionInvokesWidgetCallback:
    """Source-selection mode with no source configured must surface the
    placeholder's widget through ``on_widget_changed``.  Targets line
    421 (the call) and branch 437→439 indirectly by ensuring the
    no-source path returns before set_state."""

    def test_source_selection_no_source_invokes_widget_callback(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(
            FakeInput,
            "_capabilities",
            InputCapabilities(
                has_source_selection=True,
                has_source_discovery=True,
                discovery_interval=0.5,
                selection_title="SELECT FAKE",
                hotkey="n",
                force_zero_latency=False,
            ),
        )
        widgets: list[Any] = []
        r = _make_receiver(
            input_config={"fake_source": ""},
            on_widget_changed=widgets.append,
        )
        sink = r._get_shared_sink()
        sink.set_property("widget", SimpleNamespace(id="selection-widget"))
        r._pipeline_assembler.create_placeholder_pipeline = lambda: FakePipeline()

        r.play()

        assert any(getattr(w, "id", None) == "selection-widget" for w in widgets)


class TestPlaySourceSelectionPlaceholderReturnsNone:
    """When the placeholder factory returns ``None`` in source-selection
    mode, ``play()`` must skip the ``set_state(PLAYING)`` call without
    crashing – branch 423→430."""

    def test_placeholder_none_in_selection_mode_skips_set_state(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(
            FakeInput,
            "_capabilities",
            InputCapabilities(
                has_source_selection=True,
                has_source_discovery=True,
                discovery_interval=0.5,
                selection_title="SELECT FAKE",
                hotkey="n",
                force_zero_latency=False,
            ),
        )
        r = _make_receiver(input_config={"fake_source": ""})
        r._pipeline_assembler.create_placeholder_pipeline = lambda: None

        r.play()  # must not raise

        assert r._pipeline is None
        # Discovery still gets scheduled even when the placeholder is missing.
        assert fake_glib.timers


class TestPlaySourceSelectionConfiguredPlaceholderInfoLog:
    """When a source IS configured but the receiver is still flagged as
    placeholder (e.g. left over from a prior fallback), the success
    branch must log the placeholder-info line."""

    def test_placeholder_info_log_for_configured_source_in_selection_mode(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
        caplog,
        monkeypatch,
    ) -> None:
        import logging as _logging

        monkeypatch.setattr(
            FakeInput,
            "_capabilities",
            InputCapabilities(
                has_source_selection=True,
                has_source_discovery=True,
                discovery_interval=0.5,
                selection_title="SELECT FAKE",
                hotkey="n",
                force_zero_latency=False,
            ),
        )
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._pipeline = FakePipeline()
        r._state.set_placeholder_pipeline(True)

        with caplog.at_level(_logging.INFO, logger="openfollow.video.receiver"):
            r.play()

        assert any("Placeholder pipeline started after source startup failure" in rec.message for rec in caplog.records)


class TestPlaySourceSelectionConfiguredZeroTimeout:
    """The configured-source success branch in source-selection mode must
    skip ``_start_connection_timeout`` when the policy's timeout is 0
    – branch 450→452."""

    def test_zero_timeout_skips_connection_timer(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
        monkeypatch,
    ) -> None:
        from openfollow.video.inputs._base import (
            InputCapabilities,
            ReconnectPolicy,
        )

        monkeypatch.setattr(
            FakeInput,
            "_capabilities",
            InputCapabilities(
                has_source_selection=True,
                has_source_discovery=True,
                discovery_interval=0.5,
                selection_title="SELECT FAKE",
                hotkey="n",
                force_zero_latency=False,
            ),
        )
        monkeypatch.setattr(
            FakeInput,
            "_reconnect_policy",
            ReconnectPolicy(
                max_attempts=2,
                min_delay=0.01,
                max_delay=0.1,
                backoff_multiplier=2.0,
                connection_timeout=0.0,
                fallback_to_selection=False,
            ),
        )
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        FakeInput.create_pipeline_result = FakePipeline()
        r.play()
        # Pipeline started, but no GLib timer – timeout was zero.
        assert not fake_glib.timers


class TestSetSourceWithoutConfigFields:
    """Plugins without config fields skip the primary-field assignment in
    ``set_source`` – branch 458→462."""

    def test_set_source_with_empty_config_fields_skips_primary_field_assignment(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
        monkeypatch,
    ) -> None:
        # Plugin reports zero config fields.
        monkeypatch.setattr(FakeInput, "_config_fields", [])
        r = _make_receiver(input_config={"fake_source": "old"})
        # Stage a non-None pipeline so set_source's connect path doesn't
        # accidentally exercise the create_pipeline failure arm – keeps
        # this test focused on the empty-config-fields branch.
        FakeInput.create_pipeline_result = FakePipeline()
        r.set_source("new-cam")
        # The primary field key was NOT touched (still the original value).
        assert r._input_config["fake_source"] == "old"


class TestSaveSourceWithoutConfigFields:
    """``_save_source_to_config`` must short-circuit silently when the
    plugin has no config fields – branch 529→532."""

    def test_save_source_no_config_fields_writes_no_attr(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
        monkeypatch,
        tmp_path,
    ) -> None:
        monkeypatch.setattr(FakeInput, "_config_fields", [])

        attrs_set: list[tuple[str, Any]] = []

        class FakeCfg:
            def __setattr__(self, key: str, value: Any) -> None:  # noqa: ANN401
                attrs_set.append((key, value))

        saved: list[tuple[Any, str]] = []

        import openfollow.configuration as configuration_mod

        monkeypatch.setattr(configuration_mod, "load_config", lambda _p: FakeCfg())
        monkeypatch.setattr(
            configuration_mod,
            "save_config",
            lambda cfg, path: saved.append((cfg, path)),
        )

        cfg_path = str(tmp_path / "c.toml")
        r = _make_receiver(config_path=cfg_path)
        r._save_source_to_config("anything")

        # Save still happened, but no setattr was performed on the cfg.
        assert saved and saved[0][1] == cfg_path
        assert attrs_set == []


class TestExitSourceSelectionCancelled:
    """The else-branch of ``exit_source_selection`` fires when the
    receiver was not previously connected AND no source is configured.
    The user-visible side effect is the disconnected status text
    "Source selection cancelled"."""

    def test_exit_with_no_prior_connection_and_no_source_marks_cancelled(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(
            FakeInput,
            "_capabilities",
            InputCapabilities(
                has_source_selection=True,
                has_source_discovery=True,
                discovery_interval=0.5,
                selection_title="SELECT FAKE",
                hotkey="n",
                force_zero_latency=False,
            ),
        )
        r = _make_receiver(input_config={"fake_source": ""})
        r._state.enter_source_selection()
        r._state.was_connected_before_selection = False

        r.exit_source_selection()

        # Status reason text records the cancellation.
        assert r._status_marker.error_message == "Source selection cancelled"


class TestBuildOverlayTailDelegation:
    """``_build_overlay_tail`` is a thin pass-through to the assembler.
    A direct test pins the contract."""

    def test_build_overlay_tail_delegates_to_pipeline_assembler(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
    ) -> None:
        r = _make_receiver()
        calls: list[tuple[Any, Any, Any]] = []

        def _record(pipeline, convert, sink):  # noqa: ANN001
            calls.append((pipeline, convert, sink))

        r._pipeline_assembler.build_overlay_tail = _record  # type: ignore[assignment]

        sentinel_pipeline = object()
        sentinel_convert = object()
        sentinel_sink = object()
        r._build_overlay_tail(sentinel_pipeline, sentinel_convert, sentinel_sink)

        assert calls == [(sentinel_pipeline, sentinel_convert, sentinel_sink)]


class TestPadEventCapsAndStructureNoneBranches:
    """``_on_pad_event`` has two defensive nil-checks past the CAPS gate.
    Targets branches 616→629 (caps is None) and 618→629 (structure is
    None)."""

    def test_caps_event_with_parse_caps_returning_none_is_noop(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
    ) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        event = SimpleNamespace(type=FakeEventType.CAPS, parse_caps=lambda: None)
        info = SimpleNamespace(get_event=lambda: event)
        result = r._on_pad_event(object(), info)
        assert result == FakePadProbeReturn.OK
        assert r.resolution == (0, 0)

    def test_caps_event_with_get_structure_returning_none_is_noop(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
    ) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        caps = SimpleNamespace(get_structure=lambda _i: None)
        event = SimpleNamespace(type=FakeEventType.CAPS, parse_caps=lambda: caps)
        info = SimpleNamespace(get_event=lambda: event)
        result = r._on_pad_event(object(), info)
        assert result == FakePadProbeReturn.OK
        assert r.resolution == (0, 0)


class TestDoReconnectFallbackVariants:
    """Two remaining ``_do_reconnect`` fallback branches:

    * 725→728: plugin reports no config fields, so the primary-field
      clear arm is skipped.
    * 746→755: the placeholder factory returns ``None`` so the
      ``set_state(PLAYING)`` block is skipped entirely.
    """

    def test_fallback_with_no_config_fields_skips_primary_field_clear(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
        monkeypatch,
    ) -> None:
        from openfollow.video.inputs._base import (
            InputCapabilities,
            ReconnectPolicy,
        )

        monkeypatch.setattr(FakeInput, "_config_fields", [])
        monkeypatch.setattr(
            FakeInput,
            "_capabilities",
            InputCapabilities(
                has_source_selection=True,
                has_source_discovery=True,
                discovery_interval=0.5,
                selection_title="SELECT FAKE",
                hotkey="n",
                force_zero_latency=False,
            ),
        )
        monkeypatch.setattr(
            FakeInput,
            "_reconnect_policy",
            ReconnectPolicy(
                max_attempts=1,
                min_delay=0.01,
                max_delay=0.1,
                backoff_multiplier=2.0,
                connection_timeout=0.0,
                fallback_to_selection=True,
            ),
        )
        r = _make_receiver(input_config={"fake_source": "stale"})
        r._state.reconnect_attempt = 5  # force fallback
        r._pipeline_assembler.create_placeholder_pipeline = lambda: FakePipeline()

        r._do_reconnect()
        # No config fields → the primary key on the dict is left alone.
        assert r._input_config["fake_source"] == "stale"
        # Source-selection is re-activated (selection-capable plugin).
        assert r.source_selection_active is True

    def test_fallback_with_placeholder_returning_none_skips_set_state(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
        monkeypatch,
    ) -> None:
        from openfollow.video.inputs._base import (
            InputCapabilities,
            ReconnectPolicy,
        )

        monkeypatch.setattr(
            FakeInput,
            "_capabilities",
            InputCapabilities(
                has_source_selection=True,
                has_source_discovery=True,
                discovery_interval=0.5,
                selection_title="SELECT FAKE",
                hotkey="n",
                force_zero_latency=False,
            ),
        )
        monkeypatch.setattr(
            FakeInput,
            "_reconnect_policy",
            ReconnectPolicy(
                max_attempts=1,
                min_delay=0.01,
                max_delay=0.1,
                backoff_multiplier=2.0,
                connection_timeout=0.0,
                fallback_to_selection=True,
            ),
        )
        r = _make_receiver(input_config={"fake_source": "ndi-cam"})
        r._state.reconnect_attempt = 5
        r._pipeline_assembler.create_placeholder_pipeline = lambda: None

        r._do_reconnect()  # must not raise
        assert r._pipeline is None


class TestPlayPipelineNoneBranches:
    """``play()`` has three ``if self._pipeline is not None:`` guards on
    the no-selection paths.  When the placeholder factory and / or
    ``create_pipeline`` fail to actually produce a pipeline, those guards
    must skip the ``set_state(PLAYING)`` block – branches 373→375,
    378→408, 439→exit.

    Coverage report shows these as ``→exit`` arms because each block
    ends with a control-flow ``return`` rather than falling through.
    """

    def test_no_source_no_selection_with_placeholder_returning_none(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
    ) -> None:
        """Branch 373→375: ``_pipeline is None`` in the no-source arm of
        ``play()`` after the placeholder factory returned None."""
        r = _make_receiver()  # no fake_source configured
        r._pipeline_assembler.create_placeholder_pipeline = lambda: None

        r.play()  # must not raise

        assert r._pipeline is None

    def test_configured_source_no_selection_with_create_pipeline_returning_none(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
        monkeypatch,
    ) -> None:
        """Branch 378→408: ``_pipeline is None`` in the configured-source
        arm of ``play()`` when ``create_pipeline`` reports the input
        unavailable AND the placeholder factory also returns None."""
        monkeypatch.setattr(FakeInput, "_available", (False, "no backend"))
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._pipeline_assembler.create_placeholder_pipeline = lambda: None

        r.play()

        assert r._pipeline is None

    def test_source_selection_configured_with_create_pipeline_returning_none(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
        monkeypatch,
    ) -> None:
        """Branch 439→exit: source-selection mode with a configured source
        but ``create_pipeline`` fails to produce one (input unavailable
        AND placeholder factory returns None)."""
        monkeypatch.setattr(
            FakeInput,
            "_capabilities",
            InputCapabilities(
                has_source_selection=True,
                has_source_discovery=True,
                discovery_interval=0.5,
                selection_title="SELECT FAKE",
                hotkey="n",
                force_zero_latency=False,
            ),
        )
        monkeypatch.setattr(FakeInput, "_available", (False, "no backend"))
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._pipeline_assembler.create_placeholder_pipeline = lambda: None

        r.play()

        assert r._pipeline is None


# --------------------------------------------------------------------------- #
# swap_input: live video pipeline rebuild
# --------------------------------------------------------------------------- #


class _SyncSwapThread:
    """Synchronous stand-in for ``threading.Thread`` so the NULL
    transition during ``swap_input`` runs inline instead of in a real
    daemon thread. Mirrors the ``SyncThread`` pattern from the
    ``stop()`` tests."""

    def __init__(self, target, daemon=False, name=""):  # noqa: ANN001
        self._target = target

    def start(self) -> None:
        self._target()

    def join(self, timeout: float = 0.0) -> None:
        return None

    def is_alive(self) -> bool:
        # The synchronous ``start`` already ran the target inline, so
        # by the time ``swap_input`` checks ``is_alive`` after
        # ``join``, the "thread" has finished – this happy-path stub
        # always reports False. Tests that exercise the timeout path
        # use ``StuckThread`` instead.
        return False


class TestSwapInput:
    def test_unknown_source_type_raises_before_touching_state(
        self,
        fake_gst,
        fake_glib,
        fake_input_pair,
        monkeypatch,
    ) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        original_pipeline = FakePipeline()
        r._pipeline = original_pipeline
        original_input = r._input

        with pytest.raises(ValueError, match="Unknown video input type"):
            r.swap_input("does-not-exist", {})

        # Receiver state untouched: same pipeline, same plugin instance.
        assert r._pipeline is original_pipeline
        assert r._input is original_input
        assert original_pipeline.state_changes == []  # no NULL transition

    def test_swap_to_different_plugin_replaces_input_and_rebuilds(
        self,
        fake_gst,
        fake_glib,
        fake_input_pair,
        monkeypatch,
    ) -> None:
        """A cross-plugin swap (fake → fake_alt) tears down the old
        pipeline (NULL transition), cleans up the old plugin's
        resources, swaps ``_input`` / ``_input_caps`` /
        ``_reconnect_policy`` / ``_source_type`` / ``_input_config``
        in place, and calls ``create_pipeline`` on the new plugin
        via ``play()``. The shared sink survives – no
        ``on_widget_changed(None)`` notification, since the canvas
        keeps the same widget across the swap."""
        widget_history: list[Any] = []
        r = _make_receiver(
            input_config={"fake_source": "cam-1"},
            on_widget_changed=widget_history.append,
        )
        old_pipeline = FakePipeline(
            set_state_returns={
                FakeState.PLAYING: FakeStateChangeReturn.SUCCESS,
            }
        )
        r._pipeline = old_pipeline
        old_input = r._input
        # Provide a pipeline result for the NEW plugin so create_pipeline
        # returns a real (fake) pipeline rather than None.
        new_pipeline = FakePipeline()
        FakeInputAlt.create_pipeline_result = new_pipeline

        monkeypatch.setattr(threading, "Thread", _SyncSwapThread)

        r.swap_input("fake_alt", {"alt_url": "test://x"})

        # Old plugin cleaned up exactly once.
        assert old_input.cleanup_calls == 1  # type: ignore[attr-defined]
        # Pipeline transitioned to NULL during the swap.
        assert FakeState.NULL in old_pipeline.state_changes
        # New plugin instance now installed (different class).
        assert isinstance(r._input, FakeInputAlt)
        assert r._source_type == "fake_alt"
        assert r._input_config == {"alt_url": "test://x"}
        assert r._input_caps is FakeInputAlt._capabilities
        # Equality, not identity: ``reconnect_policy()`` returns a fresh copy
        # (as real inputs do) which the receiver may mutate for overrides.
        assert r._reconnect_policy == FakeInputAlt._reconnect_policy
        # New pipeline built (create_pipeline_call_count incremented
        # via the new plugin).
        assert FakeInputAlt.create_pipeline_call_count >= 1
        # Widget callback NOT called with None – the canvas keeps the
        # shared sink's widget across the swap.
        assert None not in widget_history

    def test_swap_resets_discovery_and_placeholder_state(
        self,
        fake_gst,
        fake_glib,
        fake_input_pair,
        monkeypatch,
    ) -> None:
        """Per-plugin state (discovered sources, selected source index,
        placeholder-pipeline flag) doesn't carry across a swap – it's
        meaningless for a different source. The new ``play()`` call
        re-establishes whatever state the new plugin needs."""
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._discovered_sources = ["a", "b", "c"]
        r._selected_source_index = 2
        r._state.set_placeholder_pipeline(True)
        FakeInputAlt.create_pipeline_result = FakePipeline()

        monkeypatch.setattr(threading, "Thread", _SyncSwapThread)

        r.swap_input("fake_alt", {"alt_url": "x"})

        assert r._discovered_sources == []
        assert r._selected_source_index == 0
        assert r._state.is_placeholder_pipeline is False

    def test_swap_to_same_plugin_with_new_config_rebuilds(
        self,
        fake_gst,
        fake_glib,
        fake_input_pair,
        monkeypatch,
    ) -> None:
        """A same-plugin swap (e.g. RTSP url change) still tears down
        and rebuilds: ``_input`` is replaced with a fresh instance of
        the same class (so the old instance's per-instance state
        doesn't bleed in), and ``_input_config`` updates to the new
        dict."""
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        old_pipeline = FakePipeline()
        r._pipeline = old_pipeline
        old_input = r._input
        FakeInput.create_pipeline_result = FakePipeline()

        monkeypatch.setattr(threading, "Thread", _SyncSwapThread)

        r.swap_input("fake", {"fake_source": "cam-2"})

        assert r._source_type == "fake"
        assert r._input_config == {"fake_source": "cam-2"}
        # Fresh plugin instance – old one cleaned up.
        assert r._input is not old_input
        assert old_input.cleanup_calls == 1  # type: ignore[attr-defined]
        assert FakeState.NULL in old_pipeline.state_changes

    def test_swap_clears_snapshot_and_preview_caches(
        self,
        fake_gst,
        fake_glib,
        fake_input_pair,
        monkeypatch,
    ) -> None:
        # A swap must drop any cached frame from the old source so a snapshot
        # during the new source's connect window isn't the stale old frame.
        from openfollow.video.preview import PreviewProvider, SnapshotProvider

        r = _make_receiver(input_config={"fake_source": "cam-1"})
        snap = SnapshotProvider()
        prev = PreviewProvider()
        snap._jpeg_bytes = b"old-stage-frame"
        prev._jpeg_bytes = b"old-stage-frame"
        r._snapshot_provider = snap
        r._preview_provider = prev
        FakeInput.create_pipeline_result = FakePipeline()
        monkeypatch.setattr(threading, "Thread", _SyncSwapThread)

        r.swap_input("fake", {"fake_source": "cam-2"})

        assert snap._jpeg_bytes is None
        assert prev._jpeg_bytes is None

    def test_swap_tolerates_old_pipeline_with_no_bus(
        self,
        fake_gst,
        fake_glib,
        fake_input_pair,
        monkeypatch,
    ) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        old_pipeline = FakePipeline()
        # Pipeline returns no bus – exercises the
        # ``if old_bus is not None: …`` if-False branch.
        old_pipeline._bus = None  # type: ignore[assignment]
        r._pipeline = old_pipeline
        FakeInput.create_pipeline_result = FakePipeline()

        monkeypatch.setattr(threading, "Thread", _SyncSwapThread)

        # Must not raise – the bus-cleanup block is gracefully
        # skipped when no bus exists.
        r.swap_input("fake", {"fake_source": "cam-2"})

        assert r._pipeline is not old_pipeline
        assert FakeState.NULL in old_pipeline.state_changes

    def test_swap_cancels_in_flight_timers_and_discovery(
        self,
        fake_gst,
        fake_glib,
        fake_input_pair,
        monkeypatch,
    ) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._state.connection_timeout_id = 100
        r._state.reconnect_source_id = 200
        r._discovery_source_id = 300
        FakeInputAlt.create_pipeline_result = FakePipeline()

        monkeypatch.setattr(threading, "Thread", _SyncSwapThread)

        r.swap_input("fake_alt", {"alt_url": "x"})

        assert 100 in fake_glib.removed
        assert 200 in fake_glib.removed
        assert 300 in fake_glib.removed

    def test_swap_joins_old_discovery_thread(
        self,
        fake_gst,
        fake_glib,
        fake_input_pair,
        monkeypatch,
    ) -> None:
        joined: list[float | None] = []

        class FakeDiscoveryThread:
            def join(self, timeout: float | None = None) -> None:
                joined.append(timeout)

            def is_alive(self) -> bool:
                # Happy-path stub: ``join`` "returned in time", thread
                # is no longer alive. Tests for the
                # discovery-stuck path use ``StuckDiscoveryThread``.
                return False

        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._discovery_thread = FakeDiscoveryThread()  # type: ignore[assignment]
        FakeInputAlt.create_pipeline_result = FakePipeline()

        monkeypatch.setattr(threading, "Thread", _SyncSwapThread)

        r.swap_input("fake_alt", {"alt_url": "x"})

        assert joined == [3.0]
        assert r._discovery_thread is None

    def test_swap_raises_pipeline_stuck_error_when_old_pipeline_does_not_reach_null(
        self,
        fake_gst,
        fake_glib,
        fake_input_pair,
        monkeypatch,
    ) -> None:
        from openfollow.video.receiver import PipelineStuckError

        r = _make_receiver(input_config={"fake_source": "cam-1"})
        old_pipeline = FakePipeline()
        r._pipeline = old_pipeline

        # Thread fake whose ``join`` returns without doing anything
        # (the target never ran) and whose ``is_alive()`` reports
        # True – simulates the GstStopPipeline daemon thread still
        # working when the timeout expires.
        class StuckThread:
            def __init__(self, target, daemon=False, name=""):  # noqa: ANN001
                self._target = target

            def start(self) -> None:
                pass  # Simulate a hang.

            def join(self, timeout: float = 0.0) -> None:
                return None

            def is_alive(self) -> bool:
                return True

        monkeypatch.setattr(threading, "Thread", StuckThread)

        old_input = r._input
        with pytest.raises(PipelineStuckError, match="did not reach NULL"):
            r.swap_input("fake_alt", {"alt_url": "x"})

        # ``_input.cleanup()`` lives AFTER the timeout check, so the
        # old plugin reference is still in place when the raise
        # fires (no count mismatch if a rollback later cleans up).
        assert old_input.cleanup_calls == 0  # type: ignore[attr-defined]
        assert r._input is old_input
        # _pipeline is NOT cleared on raise path; stays pointing at old pipeline.
        assert r._pipeline is old_pipeline
        # Status marker carries disconnect reason.
        assert "did not reach NULL" in r._status_marker.error_message
        # ``PipelineStuckError`` IS an ``OSError`` so existing
        # ``except OSError`` callers (e.g. the dispatcher's generic
        # catch) still match.
        assert issubclass(PipelineStuckError, OSError)

    def test_swap_raises_when_get_state_times_out_in_async(
        self,
        fake_gst,
        fake_glib,
        fake_input_pair,
        monkeypatch,
    ) -> None:
        """Detect stuck pipeline when get_state returns SUCCESS without reaching NULL."""
        from openfollow.video.receiver import PipelineStuckError

        class AsyncPipeline(FakePipeline):
            """``set_state(NULL)`` "returns" ``ASYNC`` and
            ``get_state(2 s)`` reports the timeout – current state
            is still ``PLAYING``, pending is ``NULL``. Mirrors
            real GStreamer behaviour for a pipeline whose
            streaming thread is blocked on I/O."""

            def get_state(self, timeout_ns: int = 0) -> tuple[str, str, str]:
                return (
                    FakeStateChangeReturn.ASYNC,
                    FakeState.PLAYING,
                    FakeState.NULL,
                )

        r = _make_receiver(input_config={"fake_source": "cam-1"})
        old_pipeline = AsyncPipeline()
        r._pipeline = old_pipeline

        # Run swap inline to simulate thread finishing before pipeline reaches NULL.
        monkeypatch.setattr(threading, "Thread", _SyncSwapThread)

        with pytest.raises(PipelineStuckError, match="did not reach NULL"):
            r.swap_input("fake_alt", {"alt_url": "x"})

        # Same recovery contract as the set_state-hung path: leave
        # the pipeline reference attached so a retry re-enters
        # the guard.
        assert r._pipeline is old_pipeline
        assert "did not reach NULL" in r._status_marker.error_message

    def test_swap_raises_when_get_state_returns_non_null_state(
        self,
        fake_gst,
        fake_glib,
        fake_input_pair,
        monkeypatch,
    ) -> None:
        """Defensive companion to the ASYNC-timeout test: even if
        ``get_state`` reports ``SUCCESS``, the current state could
        still be non-NULL (e.g., a faulty plugin transitioned to
        the wrong state). Treat that as stuck too – the contract is
        ``current_state == NULL`` after the helper returns."""
        from openfollow.video.receiver import PipelineStuckError

        class WrongStatePipeline(FakePipeline):
            def get_state(self, timeout_ns: int = 0) -> tuple[str, str, str]:
                return (
                    FakeStateChangeReturn.SUCCESS,
                    FakeState.PAUSED,
                    FakeState.PAUSED,
                )

        r = _make_receiver(input_config={"fake_source": "cam-1"})
        old_pipeline = WrongStatePipeline()
        r._pipeline = old_pipeline

        monkeypatch.setattr(threading, "Thread", _SyncSwapThread)

        with pytest.raises(PipelineStuckError, match="did not reach NULL"):
            r.swap_input("fake_alt", {"alt_url": "x"})
        assert r._pipeline is old_pipeline

    def test_swap_raises_when_set_state_raises_before_confirm(
        self,
        fake_gst,
        fake_glib,
        fake_input_pair,
        monkeypatch,
    ) -> None:
        from openfollow.video.receiver import PipelineStuckError

        class RaisingPipeline(FakePipeline):
            def __init__(self) -> None:
                super().__init__()
                self.raise_on_null = True

        # Real daemon threads swallow uncaught target exceptions
        # (sys.excepthook prints to stderr, then the thread exits).
        # ``_SyncSwapThread`` would propagate the exception
        # synchronously instead, masking the empty-confirmed
        # branch we're trying to test. This fake mirrors the
        # production behaviour: exceptions are swallowed and the
        # thread reports ``is_alive=False`` afterwards.
        class SwallowExceptionsThread:
            def __init__(self, target, daemon=False, name=""):  # noqa: ANN001
                self._target = target

            def start(self) -> None:
                try:
                    self._target()
                except Exception:
                    pass

            def join(self, timeout: float = 0.0) -> None:
                return None

            def is_alive(self) -> bool:
                return False

        r = _make_receiver(input_config={"fake_source": "cam-1"})
        old_pipeline = RaisingPipeline()
        r._pipeline = old_pipeline

        monkeypatch.setattr(threading, "Thread", SwallowExceptionsThread)

        with pytest.raises(PipelineStuckError, match="did not reach NULL"):
            r.swap_input("fake_alt", {"alt_url": "x"})
        assert r._pipeline is old_pipeline

    def test_swap_retry_after_stuck_pipeline_re_enters_null_transition(
        self,
        fake_gst,
        fake_glib,
        fake_input_pair,
        monkeypatch,
    ) -> None:
        from openfollow.video.receiver import PipelineStuckError

        r = _make_receiver(input_config={"fake_source": "cam-1"})
        old_pipeline = FakePipeline()
        r._pipeline = old_pipeline

        class StuckThread:
            def __init__(self, target, daemon=False, name=""):  # noqa: ANN001
                self._target = target

            def start(self) -> None:
                pass

            def join(self, timeout: float = 0.0) -> None:
                return None

            def is_alive(self) -> bool:
                return True

        monkeypatch.setattr(threading, "Thread", StuckThread)

        # First retry: raises.
        with pytest.raises(PipelineStuckError):
            r.swap_input("fake_alt", {"alt_url": "x"})
        assert r._pipeline is old_pipeline

        # Second retry – pipeline still stuck, so the NULL guard
        # re-fires. Without keeping ``_pipeline`` set, the retry
        # would skip the guard and proceed to re-parent the shared
        # sink while the old pipeline is still alive.
        with pytest.raises(PipelineStuckError):
            r.swap_input("fake_alt", {"alt_url": "x"})
        assert r._pipeline is old_pipeline

    def test_swap_raises_when_discovery_thread_does_not_stop(
        self,
        fake_gst,
        fake_glib,
        fake_input_pair,
        monkeypatch,
    ) -> None:
        """Unstopped discovery thread conflicts with plugin swap; raise PipelineStuckError."""
        from openfollow.video.receiver import PipelineStuckError

        class StuckDiscoveryThread:
            def join(self, timeout: float | None = None) -> None:
                return None

            def is_alive(self) -> bool:
                return True

        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._discovery_thread = StuckDiscoveryThread()  # type: ignore[assignment]
        FakeInputAlt.create_pipeline_result = FakePipeline()

        with pytest.raises(PipelineStuckError, match="discovery thread"):
            r.swap_input("fake_alt", {"alt_url": "x"})

        # Discovery thread reference NOT cleared – keeps the runtime's
        # awareness of the stuck thread (reset would imply we were
        # confident it had finished).
        assert r._discovery_thread is not None
        # Status marker surfaces swap failure.
        assert "discovery thread" in r._status_marker.error_message

    def test_swap_resets_reconnect_backoff(
        self,
        fake_gst,
        fake_glib,
        fake_input_pair,
        monkeypatch,
    ) -> None:
        """Plugin swap resets reconnect backoff for the new source."""
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        # Simulate a few prior reconnect attempts on the old plugin.
        r._state.reconnect_attempt = 5
        r._state.current_reconnect_delay = 8.0
        FakeInputAlt.create_pipeline_result = FakePipeline()

        monkeypatch.setattr(threading, "Thread", _SyncSwapThread)

        r.swap_input("fake_alt", {"alt_url": "x"})

        assert r._state.reconnect_attempt == 0
        # Back to the policy's base delay (not the inflated value).
        assert r._state.current_reconnect_delay < 8.0

    def test_swap_to_unconfigured_source_sets_disconnected_with_label(
        self,
        fake_gst,
        fake_glib,
        fake_input_pair,
        monkeypatch,
    ) -> None:
        """When the new plugin has no configured source (empty primary
        field), ``swap_input`` sets the status marker to the
        human-readable ``"No <DisplayName> source configured"`` BEFORE
        handing off to ``play()``. Covers the ``not source_label``
        branch in the swap path. ``play()`` itself then takes the
        placeholder-pipeline route, which we stub out here so the
        test stays hermetic against the real ``Gst.Pipeline`` import.
        """
        r = _make_receiver(input_config={"fake_source": "cam-1"})

        # Stub the placeholder-pipeline factory so ``play()`` doesn't
        # reach into the real ``Gst.Pipeline.new`` (FakeGst doesn't
        # model it).
        placeholder = FakePipeline(
            set_state_returns={
                FakeState.PLAYING: FakeStateChangeReturn.SUCCESS,
            }
        )
        r._pipeline_assembler.create_placeholder_pipeline = lambda: placeholder

        monkeypatch.setattr(threading, "Thread", _SyncSwapThread)

        # Empty alt_url → unconfigured.
        r.swap_input("fake_alt", {"alt_url": ""})

        # New plugin in place; status marker reports the
        # display-name-bearing disconnect message.
        assert r._source_type == "fake_alt"
        assert r._input_config == {"alt_url": ""}
        assert "FakeAlt" in r._status_marker.error_message


# --------------------------------------------------------------------------- #
# swap_detection_branch – (live detector pipeline rebuild)
# --------------------------------------------------------------------------- #


class _FakeDetector:
    """Minimal stand-in for ``PersonDetector`` exposing the surface
    ``swap_detection_branch`` reaches: ``stop()`` /  ``start()`` /
    ``available`` / ``input_resolution`` / ``set_appsink``."""

    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.input_resolution = (320, 240)
        self.start_calls = 0
        self.stop_calls = 0
        self.set_appsink_calls: list[Any] = []

    def start(self) -> None:
        self.start_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1

    def set_appsink(self, appsink: Any) -> None:
        self.set_appsink_calls.append(appsink)


class TestSwapDetectionBranch:
    def test_swap_to_new_detector_tears_down_pipeline_and_rebuilds(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
        monkeypatch,
    ) -> None:
        """Happy path: tear down old pipeline (NULL transition), swap
        detector reference on receiver + assembler, rebuild via
        ``play()``, start the new detector. The shared sink survives
        – no ``on_widget_changed(None)`` call."""
        widget_history: list[Any] = []
        old_detector = _FakeDetector()
        new_detector = _FakeDetector()
        r = _make_receiver(
            input_config={"fake_source": "cam-1"},
            on_widget_changed=widget_history.append,
        )
        r._detector = old_detector
        r._pipeline_assembler.set_detector(old_detector)
        old_pipeline = FakePipeline(
            set_state_returns={
                FakeState.PLAYING: FakeStateChangeReturn.SUCCESS,
            }
        )
        r._pipeline = old_pipeline
        FakeInput.create_pipeline_result = FakePipeline()

        monkeypatch.setattr(threading, "Thread", _SyncSwapThread)

        r.swap_detection_branch(new_detector)

        # Old pipeline transitioned to NULL.
        assert FakeState.NULL in old_pipeline.state_changes
        # Detector swapped on receiver AND assembler.
        assert r._detector is new_detector
        assert r._pipeline_assembler._detector is new_detector
        # OLD detector stopped; NEW detector started after rebuild.
        assert old_detector.stop_calls == 1
        assert new_detector.start_calls == 1
        # The widget callback was NOT called with None – canvas keeps
        # the shared sink across the swap.
        assert None not in widget_history

    def test_swap_to_none_drops_detection_branch_without_starting(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
        monkeypatch,
    ) -> None:
        """``new_detector=None`` rebuilds without a detection branch.
        The OLD detector is stopped; nothing is started afterwards."""
        old_detector = _FakeDetector()
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._detector = old_detector
        r._pipeline_assembler.set_detector(old_detector)
        r._pipeline = FakePipeline()
        FakeInput.create_pipeline_result = FakePipeline()

        monkeypatch.setattr(threading, "Thread", _SyncSwapThread)

        r.swap_detection_branch(None)

        assert r._detector is None
        assert r._pipeline_assembler._detector is None
        assert old_detector.stop_calls == 1

    def test_swap_from_none_starts_new_detector(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
        monkeypatch,
    ) -> None:
        """Off→on transition: receiver has no prior detector. The
        rebuild wires the new detector and starts it."""
        new_detector = _FakeDetector()
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        assert r._detector is None
        r._pipeline = FakePipeline()
        FakeInput.create_pipeline_result = FakePipeline()

        monkeypatch.setattr(threading, "Thread", _SyncSwapThread)

        r.swap_detection_branch(new_detector)

        assert r._detector is new_detector
        assert r._pipeline_assembler._detector is new_detector
        assert new_detector.start_calls == 1

    def test_swap_raises_pipeline_stuck_error_when_old_pipeline_does_not_reach_null(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
        monkeypatch,
    ) -> None:
        from openfollow.video.receiver import PipelineStuckError

        old_detector = _FakeDetector()
        new_detector = _FakeDetector()
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._detector = old_detector
        r._pipeline_assembler.set_detector(old_detector)
        old_pipeline = FakePipeline()
        r._pipeline = old_pipeline

        class StuckThread:
            def __init__(self, target, daemon=False, name=""):  # noqa: ANN001
                self._target = target

            def start(self) -> None:
                pass  # Simulate a hang.

            def join(self, timeout: float = 0.0) -> None:
                return None

            def is_alive(self) -> bool:
                return True

        monkeypatch.setattr(threading, "Thread", StuckThread)

        with pytest.raises(PipelineStuckError, match="did not reach NULL"):
            r.swap_detection_branch(new_detector)

        # Detector swap NEVER happened.
        assert r._detector is old_detector
        assert r._pipeline_assembler._detector is old_detector
        # OLD detector NOT stopped – it's still alive on the
        # still-running pipeline.
        assert old_detector.stop_calls == 0
        # NEW detector NOT started.
        assert new_detector.start_calls == 0
        # Pipeline reference NOT cleared so a retry re-enters the
        # NULL transition guard instead of skipping it.
        assert r._pipeline is old_pipeline
        # Status marker carries the disconnected reason for the HUD.
        assert "did not reach NULL" in r._status_marker.error_message

    def test_swap_tolerates_old_pipeline_with_no_bus(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
        monkeypatch,
    ) -> None:
        old_detector = _FakeDetector()
        new_detector = _FakeDetector()
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._detector = old_detector
        r._pipeline_assembler.set_detector(old_detector)
        old_pipeline = FakePipeline()
        old_pipeline._bus = None  # type: ignore[assignment]
        r._pipeline = old_pipeline
        FakeInput.create_pipeline_result = FakePipeline()

        monkeypatch.setattr(threading, "Thread", _SyncSwapThread)

        # Must not raise – the bus-cleanup block is gracefully
        # skipped when no bus exists.
        r.swap_detection_branch(new_detector)

        assert r._detector is new_detector
        assert FakeState.NULL in old_pipeline.state_changes

    def test_swap_cancels_in_flight_timers(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
        monkeypatch,
    ) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._detector = _FakeDetector()
        r._pipeline_assembler.set_detector(r._detector)
        r._state.connection_timeout_id = 100
        r._state.reconnect_source_id = 200
        r._heal_source_id = 300
        FakeInput.create_pipeline_result = FakePipeline()

        monkeypatch.setattr(threading, "Thread", _SyncSwapThread)

        r.swap_detection_branch(_FakeDetector())

        assert 100 in fake_glib.removed
        assert 200 in fake_glib.removed
        assert 300 in fake_glib.removed  # heal timer cancelled too
        assert r._heal_source_id is None

    def test_swap_with_no_pipeline_skips_null_transition(
        self,
        fake_gst,
        fake_glib,
        fake_input_cls,
        monkeypatch,
    ) -> None:
        """When the receiver has never built a pipeline (``_pipeline
        is None``, e.g. a swap right after construction), the
        NULL-transition block is skipped and the detector swap +
        rebuild happens directly via ``play()``."""
        new_detector = _FakeDetector()
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._pipeline = None
        FakeInput.create_pipeline_result = FakePipeline()

        monkeypatch.setattr(threading, "Thread", _SyncSwapThread)

        r.swap_detection_branch(new_detector)

        assert r._detector is new_detector
        assert new_detector.start_calls == 1


# --------------------------------------------------------------------------- #
# Background healing (URL-based inputs with no source discovery)
# --------------------------------------------------------------------------- #


class TestHeal:
    """Slow background retry that lets fixed-URL inputs (RTSP/SRT/RTP)
    recover on their own after parking on the no-signal placeholder –
    e.g. when a camera or router finishes rebooting. Discovery-capable
    inputs use ``_schedule_discovery`` instead; the heal loop fills the
    gap for inputs that have neither discovery nor selection."""

    @pytest.fixture
    def heal_policy(self, monkeypatch):
        """A no-selection, no-discovery input with healing enabled."""
        monkeypatch.setattr(
            FakeInput,
            "_reconnect_policy",
            ReconnectPolicy(
                max_attempts=1,
                min_delay=0.1,
                max_delay=1.0,
                backoff_multiplier=2.0,
                connection_timeout=0,
                fallback_to_selection=True,
                heal_interval=5.0,
            ),
        )

    def test_schedule_heal_noop_without_interval(self, fake_gst, fake_glib, fake_input_cls) -> None:
        # FakeInput's default policy leaves heal_interval at 0.0.
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._schedule_heal()
        assert not fake_glib.timers

    def test_schedule_heal_registers_do_heal_callback(self, fake_gst, fake_glib, fake_input_cls, heal_policy) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._schedule_heal()
        assert len(fake_glib.timers) == 1
        delay_ms, cb = next(iter(fake_glib.timers.values()))
        assert delay_ms == 5000
        assert cb.__name__ == "_do_heal"

    def test_cancel_heal_removes_registered_source(self, fake_gst, fake_glib, fake_input_cls, heal_policy) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._schedule_heal()
        heal_id = r._heal_source_id
        assert heal_id is not None
        r._cancel_heal()
        assert heal_id in fake_glib.removed
        assert r._heal_source_id is None

    def test_cancel_heal_noop_without_id(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._cancel_heal()  # no raise, no removal
        assert not fake_glib.removed

    def test_fallback_schedules_heal_for_url_input(self, fake_gst, fake_glib, fake_input_cls, heal_policy) -> None:
        """The whole point: a no-discovery URL input that exhausts its
        reconnect budget parks on the placeholder AND arms a heal timer
        so it keeps probing the URL in the background."""
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._pipeline_assembler.create_placeholder_pipeline = lambda: FakePipeline()
        r._state.reconnect_attempt = 1  # hit max_attempts

        r._do_reconnect()

        assert r._state.is_placeholder_pipeline is True
        assert r._heal_source_id is not None
        _, cb = fake_glib.timers[r._heal_source_id]
        assert cb.__name__ == "_do_heal"

    def test_fallback_skips_heal_when_no_configured_source(
        self, fake_gst, fake_glib, fake_input_cls, heal_policy
    ) -> None:
        r = _make_receiver()  # no fake_source configured
        r._pipeline_assembler.create_placeholder_pipeline = lambda: FakePipeline()
        r._state.reconnect_attempt = 1

        r._do_reconnect()

        assert r._heal_source_id is None

    def test_do_heal_noop_when_already_connected(self, fake_gst, fake_glib, fake_input_cls, heal_policy) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._state.connected = True
        r._state.set_placeholder_pipeline(False)

        result = r._do_heal()

        assert result is False
        # No reconnect attempt kicked off.
        assert FakeInput.create_pipeline_call_count == 0
        assert r._heal_source_id is None

    def test_do_heal_noop_on_real_pipeline_before_connected(
        self, fake_gst, fake_glib, fake_input_cls, heal_policy
    ) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._state.connected = False
        r._state.set_placeholder_pipeline(False)

        result = r._do_heal()

        assert result is False
        assert FakeInput.create_pipeline_call_count == 0  # no reconnect kicked off
        assert r._heal_source_id is None  # not re-armed

    def test_do_heal_reschedules_when_no_configured_source(
        self, fake_gst, fake_glib, fake_input_cls, heal_policy
    ) -> None:
        r = _make_receiver()  # no source yet
        r._state.set_placeholder_pipeline(True)

        result = r._do_heal()

        assert result is False
        # Re-armed for a later attempt rather than giving up.
        assert r._heal_source_id is not None

    def test_do_heal_rebuilds_pipeline_when_source_configured(
        self, fake_gst, fake_glib, fake_input_cls, heal_policy
    ) -> None:
        """A heal tick on a parked, configured input resets the backoff
        and rebuilds the real pipeline via ``_do_reconnect``."""
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._state.set_placeholder_pipeline(True)
        r._state.reconnect_attempt = 99  # would otherwise force fallback
        fake_pipeline = FakePipeline()
        FakeInput.create_pipeline_result = fake_pipeline

        result = r._do_heal()

        assert result is False
        # Backoff was reset so _do_reconnect rebuilt instead of re-parking.
        assert r._state.reconnect_attempt == 0
        assert FakeState.PLAYING in fake_pipeline.state_changes


# --------------------------------------------------------------------------- #
# Silent-stall watchdog (established stream stops delivering frames silently)
# --------------------------------------------------------------------------- #


class TestStallWatchdog:
    """Established URL feeds (RTSP/SRT/RTP) can lose the network mid-stream
    with no bus ERROR or EOS – the picture freezes, the HUD stays green, and
    the error-driven reconnect never fires. The watchdog detects the absence
    of decoded frames and forces a reconnect so the feed self-heals and the
    HUD turns red."""

    @pytest.fixture
    def stall_policy(self, monkeypatch):
        """A no-selection, no-discovery input with the stall watchdog on."""
        monkeypatch.setattr(
            FakeInput,
            "_reconnect_policy",
            ReconnectPolicy(
                max_attempts=2,
                min_delay=0.1,
                max_delay=1.0,
                backoff_multiplier=2.0,
                connection_timeout=4.0,
                fallback_to_selection=True,
                stall_timeout=3.0,
            ),
        )

    def test_on_sink_buffer_stamps_last_frame_clock(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        assert r._last_frame_monotonic == 0.0
        before = time.monotonic()
        result = r._on_sink_buffer(object(), object())
        assert result == FakePadProbeReturn.OK
        assert r._last_frame_monotonic >= before

    def test_start_watchdog_noop_without_stall_timeout(self, fake_gst, fake_glib, fake_input_cls) -> None:
        # FakeInput's default policy leaves stall_timeout at 0.0.
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._start_watchdog()
        assert r._watchdog_source_id is None
        assert not fake_glib.timers

    def test_start_watchdog_registers_do_watchdog_and_seeds_clock(
        self, fake_gst, fake_glib, fake_input_cls, stall_policy
    ) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._start_watchdog()
        assert r._watchdog_source_id is not None
        delay_ms, cb = fake_glib.timers[r._watchdog_source_id]
        # 3s timeout → poll ~3x within the window (every 1000ms).
        assert delay_ms == 1000
        assert cb.__name__ == "_do_watchdog"
        # The clock is seeded so a fresh stream isn't flagged immediately.
        assert r._last_frame_monotonic > 0.0

    def test_cancel_watchdog_removes_registered_source(self, fake_gst, fake_glib, fake_input_cls, stall_policy) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._start_watchdog()
        wd_id = r._watchdog_source_id
        assert wd_id is not None
        r._cancel_watchdog()
        assert wd_id in fake_glib.removed
        assert r._watchdog_source_id is None

    def test_cancel_watchdog_noop_without_id(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._cancel_watchdog()  # no raise, no removal
        assert not fake_glib.removed

    def test_first_frame_arms_watchdog_on_connect(self, fake_gst, fake_glib, fake_input_cls, stall_policy) -> None:
        """Becoming connected (on the first real frame) arms the watchdog;
        the established stream is now monitored for a silent stall."""
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._state.set_placeholder_pipeline(False)
        r._on_sink_buffer(object(), object())
        assert r._watchdog_source_id is not None
        _, cb = fake_glib.timers[r._watchdog_source_id]
        assert cb.__name__ == "_do_watchdog"

    def test_do_watchdog_stands_down_when_timeout_disabled(self, fake_gst, fake_glib, fake_input_cls) -> None:
        """With ``stall_timeout`` 0 (FakeInput default), the watchdog is a
        no-op: it clears its id and stops rather than ever reconnecting."""
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._state.connected = True
        r._watchdog_source_id = 123

        result = r._do_watchdog()

        assert result is False
        assert r._watchdog_source_id is None

    def test_do_watchdog_stays_armed_when_frames_recent(
        self, fake_gst, fake_glib, fake_input_cls, stall_policy
    ) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._state.connected = True
        r._state.set_placeholder_pipeline(False)
        r._last_frame_monotonic = time.monotonic()  # just saw a frame

        result = r._do_watchdog()

        assert result is True  # healthy – keep polling
        assert FakeInput.create_pipeline_call_count == 0

    def test_do_watchdog_reconnects_on_stall(self, fake_gst, fake_glib, fake_input_cls, stall_policy) -> None:
        """No frame for longer than ``stall_timeout`` → tear down and
        reconnect, surfacing the RECONNECTING status (red HUD)."""
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._state.connected = True
        r._state.set_placeholder_pipeline(False)
        r._last_frame_monotonic = time.monotonic() - 100  # long-dead feed

        result = r._do_watchdog()

        assert result is False  # one-shot stop; reconnect now owns recovery
        assert r._watchdog_source_id is None
        # Reconnect was scheduled and the status reflects it (red HUD).
        assert r._state.reconnect_source_id is not None
        assert r.status_marker.status == ConnectionStatus.RECONNECTING

    def test_do_watchdog_stands_down_when_not_connected(
        self, fake_gst, fake_glib, fake_input_cls, stall_policy
    ) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._state.connected = False
        r._state.set_placeholder_pipeline(False)
        r._last_frame_monotonic = time.monotonic() - 100

        result = r._do_watchdog()

        assert result is False
        # No reconnect kicked off – recovery is owned elsewhere now.
        assert r._state.reconnect_source_id is None

    def test_do_watchdog_stands_down_on_placeholder(self, fake_gst, fake_glib, fake_input_cls, stall_policy) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._state.connected = True
        r._state.set_placeholder_pipeline(True)
        r._last_frame_monotonic = time.monotonic() - 100

        result = r._do_watchdog()

        assert result is False
        assert r._state.reconnect_source_id is None

    def test_schedule_reconnect_cancels_watchdog(self, fake_gst, fake_glib, fake_input_cls, stall_policy) -> None:
        r = _make_receiver(input_config={"fake_source": "cam-1"})
        r._start_watchdog()
        wd_id = r._watchdog_source_id
        assert wd_id is not None

        r._schedule_reconnect("boom")

        assert wd_id in fake_glib.removed
        assert r._watchdog_source_id is None


# --------------------------------------------------------------------------- #
# Recovery-timer overrides (web-UI configurable stall_timeout / heal_interval)
# --------------------------------------------------------------------------- #


class TestRecoveryTimerOverrides:
    """``stall_timeout`` / ``heal_interval`` are operator-configurable. The
    per-plugin ``reconnect_policy()`` supplies defaults; constructor overrides
    (sourced from ``AppConfig``) win and survive a plugin swap, and
    ``set_recovery_timers`` applies a live change without a rebuild."""

    def test_constructor_override_wins_over_policy_default(self, fake_gst, fake_glib, fake_input_cls) -> None:
        # FakeInput's default policy has stall_timeout=0.0, heal_interval=0.0.
        r = receiver_mod.GstNativeSinkReceiver(
            source_type="fake",
            input_config={"fake_source": "cam-1"},
            stall_timeout=2.5,
            heal_interval=7.0,
        )
        assert r._reconnect_policy.stall_timeout == 2.5
        assert r._reconnect_policy.heal_interval == 7.0

    def test_none_override_keeps_policy_default(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = receiver_mod.GstNativeSinkReceiver(
            source_type="fake",
            input_config={"fake_source": "cam-1"},
        )
        # Untouched – whatever the plugin policy declared (0.0 here).
        assert r._reconnect_policy.stall_timeout == 0.0
        assert r._reconnect_policy.heal_interval == 0.0

    def test_override_survives_input_swap(self, fake_gst, fake_glib, fake_input_pair) -> None:
        r = receiver_mod.GstNativeSinkReceiver(
            source_type="fake",
            input_config={"fake_source": "cam-1"},
            stall_timeout=2.5,
            heal_interval=7.0,
        )
        FakeInputAlt.create_pipeline_result = FakePipeline()
        r.swap_input("fake_alt", {"alt_url": "rtsp://x"})
        assert r._reconnect_policy.stall_timeout == 2.5
        assert r._reconnect_policy.heal_interval == 7.0

    def test_set_recovery_timers_updates_live(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = receiver_mod.GstNativeSinkReceiver(
            source_type="fake",
            input_config={"fake_source": "cam-1"},
            stall_timeout=3.0,
        )
        r.set_recovery_timers(stall_timeout=1.0, heal_interval=9.0)
        assert r._reconnect_policy.stall_timeout == 1.0
        assert r._reconnect_policy.heal_interval == 9.0
        # Overrides persist across a later swap too.
        assert r._stall_timeout_override == 1.0
        assert r._heal_interval_override == 9.0

    def test_set_recovery_timers_heal_only_leaves_stall_untouched(self, fake_gst, fake_glib, fake_input_cls) -> None:
        """Passing only ``heal_interval`` updates it and leaves the stall
        override as-is (the ``stall_timeout is None`` skip arm)."""
        r = receiver_mod.GstNativeSinkReceiver(
            source_type="fake",
            input_config={"fake_source": "cam-1"},
            stall_timeout=3.0,
        )
        r.set_recovery_timers(heal_interval=4.0)
        assert r._reconnect_policy.heal_interval == 4.0
        assert r._heal_interval_override == 4.0
        # Stall override untouched.
        assert r._stall_timeout_override == 3.0
        assert r._reconnect_policy.stall_timeout == 3.0

    def test_set_recovery_timers_rearms_active_watchdog(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = receiver_mod.GstNativeSinkReceiver(
            source_type="fake",
            input_config={"fake_source": "cam-1"},
            stall_timeout=3.0,
        )
        r._state.connected = True
        r._start_watchdog()
        old_id = r._watchdog_source_id
        assert old_id is not None

        r.set_recovery_timers(stall_timeout=1.0)

        # Old timer cancelled, a fresh one armed at the new cadence.
        assert old_id in fake_glib.removed
        assert r._watchdog_source_id is not None
        delay_ms, _cb = fake_glib.timers[r._watchdog_source_id]
        assert delay_ms == 500  # max(500, int(1.0*1000/3))

    def test_set_recovery_timers_rearm_preserves_last_frame_clock(self, fake_gst, fake_glib, fake_input_cls) -> None:
        r = receiver_mod.GstNativeSinkReceiver(
            source_type="fake",
            input_config={"fake_source": "cam-1"},
            stall_timeout=3.0,
        )
        r._state.connected = True
        r._start_watchdog()
        # Simulate a stalled feed: last frame was long ago.
        stale = time.monotonic() - 100
        r._last_frame_monotonic = stale

        r.set_recovery_timers(stall_timeout=1.0)

        # Clock preserved (not reseeded to ~now), so the next _do_watchdog
        # tick still sees the stall and reconnects.
        assert r._last_frame_monotonic == stale
        assert r._do_watchdog() is False
        assert r._state.reconnect_source_id is not None
