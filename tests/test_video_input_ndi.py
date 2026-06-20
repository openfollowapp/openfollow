# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the NDI video input plugin.

Exercises:

* the NDI SDK library-path discovery helpers,
* ``discover_sources`` against a fake ``ctypes`` NDI library,
* the HTMX source-dropdown handler,
* ``create_pipeline`` against a fake ``Gst`` module (lazy import inside
  ``create_pipeline``),
* the availability probe with and without the GStreamer ``ndisrc`` element.
"""

from __future__ import annotations

import ctypes
import logging
from typing import Any
from unittest.mock import patch

import pytest

from openfollow.video.inputs import ndi as ndi_module
from openfollow.video.inputs.ndi import NdiInput
from tests._fake_gst import FakeElement, FakePad, FakePipeline, make_fake_gst

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# Fake GStreamer fixture – wraps shared ``tests._fake_gst`` helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def fake_gst(monkeypatch: pytest.MonkeyPatch):
    """Patch ``gi.repository.Gst`` with the shared recording fake."""
    # Make sure the real gi.repository module attribute exists so patch works.
    from gi.repository import Gst  # noqa: F401

    fake = make_fake_gst()
    with patch("gi.repository.Gst", fake):
        yield fake


# --------------------------------------------------------------------------- #
# Library path discovery
# --------------------------------------------------------------------------- #


class TestFindNdiLibrary:
    def test_env_var_hit_wins_over_default_dirs(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        lib = tmp_path / "libndi.dylib"
        lib.write_bytes(b"")
        monkeypatch.setenv("NDI_RUNTIME_DIR_V6", str(tmp_path))
        monkeypatch.delenv("NDI_RUNTIME_DIR_V5", raising=False)
        assert ndi_module._find_ndi_library() == str(lib)

    def test_v5_env_var_checked_when_v6_absent(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        lib = tmp_path / "libndi.so"
        lib.write_bytes(b"")
        monkeypatch.delenv("NDI_RUNTIME_DIR_V6", raising=False)
        monkeypatch.setenv("NDI_RUNTIME_DIR_V5", str(tmp_path))
        assert ndi_module._find_ndi_library() == str(lib)

    def test_env_var_set_but_no_sdk_file_falls_through_to_default_dirs(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When ``NDI_RUNTIME_DIR_*`` points at a directory with NO
        SDK files, the inner search loop falls through and the next
        env var (or the default-dirs / ctypes fallback) is tried.
        Covers the inner-for-loop fall-through partial branch."""
        # tmp_path is empty → no libndi.* files present.
        monkeypatch.setenv("NDI_RUNTIME_DIR_V6", str(tmp_path))
        monkeypatch.delenv("NDI_RUNTIME_DIR_V5", raising=False)
        monkeypatch.setattr(ndi_module, "_NDI_LIB_DIRS", ())
        monkeypatch.setattr(
            ndi_module.ctypes.util,
            "find_library",
            lambda _name: "/opt/fallback/libndi.so",
        )
        # The V6 env var is set but tmp_path has no SDK file → inner
        # for completes, outer continues to V5 (also absent), then
        # falls through to the default-dirs and finally the ctypes
        # fallback.
        assert ndi_module._find_ndi_library() == "/opt/fallback/libndi.so"

    def test_falls_back_to_ctypes_util_when_no_sdk_files_exist(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NDI_RUNTIME_DIR_V6", raising=False)
        monkeypatch.delenv("NDI_RUNTIME_DIR_V5", raising=False)
        # Point the fallback search dirs at a location without the SDK.
        monkeypatch.setattr(ndi_module, "_NDI_LIB_DIRS", ())
        monkeypatch.setattr(ndi_module.ctypes.util, "find_library", lambda _name: "/opt/fake/libndi.so")
        assert ndi_module._find_ndi_library() == "/opt/fake/libndi.so"


# --------------------------------------------------------------------------- #
# is_available
# --------------------------------------------------------------------------- #


class TestIsAvailable:
    def test_plugin_missing_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from gi.repository import Gst  # noqa: F401

        class FakeFactory:
            @staticmethod
            def find(_name: str) -> Any:
                return None

        class FakeGst:
            ElementFactory = FakeFactory

            @staticmethod
            def init(_args: Any) -> None:
                return None

        with patch("gi.repository.Gst", FakeGst):
            available, reason = NdiInput.is_available()

        assert available is False
        assert "gst-plugin-ndi" in reason

    def test_plugin_present_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from gi.repository import Gst  # noqa: F401

        class FakeFactory:
            @staticmethod
            def find(_name: str) -> object:
                return object()

        class FakeGst:
            ElementFactory = FakeFactory

            @staticmethod
            def init(_args: Any) -> None:
                return None

        with patch("gi.repository.Gst", FakeGst):
            available, reason = NdiInput.is_available()

        assert available is True
        assert reason == ""

    def test_gst_import_failure_returns_reason(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class ExplodingGst:
            class ElementFactory:
                @staticmethod
                def find(_name: str) -> Any:
                    raise RuntimeError("no GStreamer")

            @staticmethod
            def init(_args: Any) -> None:
                raise RuntimeError("no GStreamer")

        with patch("gi.repository.Gst", ExplodingGst):
            available, reason = NdiInput.is_available()

        assert available is False
        assert "GStreamer" in reason


# --------------------------------------------------------------------------- #
# Web UI + source selection handler
# --------------------------------------------------------------------------- #


class TestWebUI:
    def test_html_contains_source_dropdown_and_scan_button(self) -> None:
        html = NdiInput.web_ui_html({"ndi_source_name": "Studio A"})
        assert "ndi_source_name" in html
        assert "Studio A" in html
        assert "refresh-ndi" in html
        # Points at the plugin's own route, not a legacy hardcoded one.
        assert 'hx-get="/video-input/ndi/sources"' in html

    def test_html_renders_unavailable_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(NdiInput, "is_available", classmethod(lambda cls: (False, "missing lib")))
        html = NdiInput.web_ui_html({"ndi_source_name": ""})
        assert "NDI not available" in html
        assert "missing lib" in html

    def test_empty_source_prompts_loading_placeholder(self) -> None:
        html = NdiInput.web_ui_html({"ndi_source_name": ""})
        assert "-- Loading... --" in html


class TestDiscoverSourcesHandler:
    def test_returns_no_sources_option_when_scan_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(NdiInput, "discover_sources", classmethod(lambda cls, timeout: []))
        html = NdiInput().handle_discover_sources({"ndi_source_name": ""})
        assert "-- No sources found --" in html

    def test_marks_full_name_match_selected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            NdiInput,
            "discover_sources",
            classmethod(lambda cls, timeout: ["Studio A", "Studio B"]),
        )
        html = NdiInput().handle_discover_sources({"ndi_source_name": "Studio A"})
        assert '<option value="Studio A" selected>Studio A</option>' in html
        assert '<option value="Studio B">Studio B</option>' in html

    def test_substring_config_does_not_mark_option_selected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A configured name that is only a substring of a discovered source
        must NOT be highlighted – only an exact match selects an option."""
        monkeypatch.setattr(
            NdiInput,
            "discover_sources",
            classmethod(lambda cls, timeout: ["HOST (Studio A)"]),
        )
        html = NdiInput().handle_discover_sources({"ndi_source_name": "Studio A"})
        assert "selected" not in html

    def test_substring_source_does_not_steal_selection_from_exact_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Exactly one option is selected when a longer source shares the
        configured name as a prefix – the browser must not switch to it."""
        monkeypatch.setattr(
            NdiInput,
            "discover_sources",
            classmethod(lambda cls, timeout: ["Studio", "Studio Backup"]),
        )
        html = NdiInput().handle_discover_sources({"ndi_source_name": "Studio"})
        assert '<option value="Studio" selected>Studio</option>' in html
        assert '<option value="Studio Backup">Studio Backup</option>' in html
        assert html.count("selected") == 1

    def test_escapes_source_names(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            NdiInput,
            "discover_sources",
            classmethod(lambda cls, timeout: ['<script>alert("x")</script>']),
        )
        html = NdiInput().handle_discover_sources({"ndi_source_name": ""})
        assert "<script>alert" not in html
        assert "&lt;script&gt;" in html


# --------------------------------------------------------------------------- #
# discover_sources via fake ctypes NDI library
# --------------------------------------------------------------------------- #


class _FakeNdiSource:
    _fields_ = [
        ("p_ndi_name", ctypes.c_char_p),
        ("p_url_address", ctypes.c_char_p),
    ]

    def __init__(self, name: bytes) -> None:
        self.p_ndi_name = name


class _CallableAttr:
    def __init__(self, fn):
        self._fn = fn
        self.restype: Any = None
        self.argtypes: list[Any] = []

    def __call__(self, *args, **kwargs):
        return self._fn(*args, **kwargs)


class _FakeCtypesNdi:
    def __init__(
        self,
        sources: list[bytes],
        *,
        init_returns: bool = True,
        has_initialize: bool = True,
        finder_handle: int = 1,
    ) -> None:
        self._sources = [_FakeNdiSource(s) for s in sources]
        self.wait_calls: list[int] = []
        self.destroy_calls: int = 0
        self._finder_handle = finder_handle

        if has_initialize:
            self.NDIlib_initialize = _CallableAttr(lambda: init_returns)

        self.NDIlib_find_create_v2 = _CallableAttr(lambda _opts: self._finder_handle)
        self.NDIlib_find_wait_for_sources = _CallableAttr(self._wait)
        self.NDIlib_find_get_current_sources = _CallableAttr(self._get)
        self.NDIlib_find_destroy = _CallableAttr(self._destroy)

    def _wait(self, _finder: Any, timeout_ms: int) -> bool:
        self.wait_calls.append(timeout_ms)
        return True

    def _get(self, _finder: Any, num_ptr: Any):
        num_ptr._obj.value = len(self._sources)
        sources = self._sources

        class _Arr:
            def __getitem__(self_inner, idx: int):  # noqa: N805
                return sources[idx]

        return _Arr()

    def _destroy(self, _finder: Any) -> None:
        self.destroy_calls += 1


class TestDiscoverSources:
    def test_no_library_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ndi_module, "_find_ndi_library", lambda: None)
        assert NdiInput.discover_sources() == []

    def test_library_load_failure_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ndi_module, "_find_ndi_library", lambda: "/fake/libndi.so")

        class _BrokenLoader:
            @staticmethod
            def LoadLibrary(_path: str) -> Any:
                raise OSError("cannot load")

        monkeypatch.setattr(ctypes, "cdll", _BrokenLoader)
        assert NdiInput.discover_sources() == []

    def test_initialize_returning_false_yields_no_sources(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeCtypesNdi([b"Studio A"], init_returns=False)
        monkeypatch.setattr(ndi_module, "_find_ndi_library", lambda: "/fake/libndi.so")
        monkeypatch.setattr(ctypes, "cdll", type("L", (), {"LoadLibrary": staticmethod(lambda _p: fake)}))
        assert NdiInput.discover_sources() == []

    def test_find_create_failure_returns_empty_and_skips_destroy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeCtypesNdi([b"Studio A"], finder_handle=0)
        monkeypatch.setattr(ndi_module, "_find_ndi_library", lambda: "/fake/libndi.so")
        monkeypatch.setattr(ctypes, "cdll", type("L", (), {"LoadLibrary": staticmethod(lambda _p: fake)}))
        assert NdiInput.discover_sources() == []
        assert fake.destroy_calls == 0

    def test_returns_decoded_source_names(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeCtypesNdi([b"HOST (Studio A)", b"HOST (Studio B)"])
        monkeypatch.setattr(ndi_module, "_find_ndi_library", lambda: "/fake/libndi.so")
        monkeypatch.setattr(ctypes, "cdll", type("L", (), {"LoadLibrary": staticmethod(lambda _p: fake)}))
        result = NdiInput.discover_sources(timeout=0.25)
        assert result == ["HOST (Studio A)", "HOST (Studio B)"]
        assert fake.wait_calls == [250]  # seconds → ms
        assert fake.destroy_calls == 1

    def test_older_sdk_without_initialize_still_scans(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeCtypesNdi([b"Studio X"], has_initialize=False)
        monkeypatch.setattr(ndi_module, "_find_ndi_library", lambda: "/fake/libndi.so")
        monkeypatch.setattr(ctypes, "cdll", type("L", (), {"LoadLibrary": staticmethod(lambda _p: fake)}))
        assert NdiInput.discover_sources() == ["Studio X"]


# --------------------------------------------------------------------------- #
# create_pipeline with fake Gst
# --------------------------------------------------------------------------- #


class _OverlayCapture:
    """Collect ``build_overlay_tail`` invocations for assertion."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, Any, Any]] = []

    def __call__(self, pipeline: Any, head: Any, sink: Any) -> None:
        self.calls.append((pipeline, head, sink))


class TestCreatePipeline:
    def test_builds_ndi_chain_and_records_latency(self, fake_gst) -> None:
        overlay = _OverlayCapture()
        sink = FakeElement("shared_videosink")
        pipeline = NdiInput().create_pipeline(
            config={"ndi_source_name": "Studio A"},
            sink=sink,
            build_overlay_tail=overlay,
            prepare_sink=lambda: sink,
        )

        assert isinstance(pipeline, FakePipeline)
        # Source configured
        ndisrc = pipeline.get_by_name("ndisrc")
        assert ndisrc is not None
        assert ndisrc.properties["ndi-name"] == "Studio A"

        # Leaky queue wired with the expected size caps
        queue = pipeline.get_by_name("ndi_video_queue")
        assert queue is not None
        assert queue.properties["leaky"] == 2
        assert queue.properties["max-size-buffers"] == 3

        # Overlay tail hook was handed the videoconvert tail and sink
        assert len(overlay.calls) == 1
        _pipe, head, tail_sink = overlay.calls[0]
        assert head.name == "convert"
        assert tail_sink is sink

        # Pipeline latency forced to 0 for zero-latency NDI
        assert pipeline.latency_values == [0]

        # demux.pad-added callback exists and is exercised below
        demux = pipeline.get_by_name("demux")
        assert demux is not None
        assert any(sig == "pad-added" for sig, _cb in demux.signals)

    def test_raises_when_ndisrc_element_missing(self) -> None:
        from gi.repository import Gst  # noqa: F401

        fake = make_fake_gst(missing_elements={"ndisrc"})
        with patch("gi.repository.Gst", fake):
            with pytest.raises(RuntimeError, match="NDI GStreamer plugin not available"):
                NdiInput().create_pipeline(
                    config={},
                    sink=FakeElement("shared_videosink"),
                    build_overlay_tail=_OverlayCapture(),
                    prepare_sink=lambda: FakeElement("shared_videosink"),
                )

    def test_raises_when_demux_element_missing(self) -> None:
        from gi.repository import Gst  # noqa: F401

        fake = make_fake_gst(missing_elements={"ndisrcdemux"})
        with patch("gi.repository.Gst", fake):
            with pytest.raises(RuntimeError, match="NDI demux element not available"):
                NdiInput().create_pipeline(
                    config={},
                    sink=FakeElement("shared_videosink"),
                    build_overlay_tail=_OverlayCapture(),
                    prepare_sink=lambda: FakeElement("shared_videosink"),
                )

    def test_raises_when_prepare_sink_returns_none(self, fake_gst) -> None:
        with pytest.raises(RuntimeError, match="No video sink available"):
            NdiInput().create_pipeline(
                config={},
                sink=FakeElement("shared_videosink"),
                build_overlay_tail=_OverlayCapture(),
                prepare_sink=lambda: None,
            )

    def test_pad_added_video_links_into_leaky_queue(self, fake_gst) -> None:
        overlay = _OverlayCapture()
        sink = FakeElement("shared_videosink")
        pipeline = NdiInput().create_pipeline(
            config={},
            sink=sink,
            build_overlay_tail=overlay,
            prepare_sink=lambda: sink,
        )
        demux = pipeline.get_by_name("demux")
        assert demux is not None
        cb = next(cb for sig, cb in demux.signals if sig == "pad-added")
        video_pad = FakePad("video_0")
        cb(demux, video_pad)
        queue_sink = pipeline.get_by_name("ndi_video_queue").get_static_pad("sink")
        assert video_pad.linked_to is queue_sink

    def test_raises_when_video_queue_element_missing(self) -> None:
        """Failure to create the video queue surfaces a precise error
        rather than letting the next link silently NPE."""
        from gi.repository import Gst  # noqa: F401

        fake = make_fake_gst(missing_elements={"queue"})
        with patch("gi.repository.Gst", fake):
            with pytest.raises(RuntimeError, match="video queue"):
                NdiInput().create_pipeline(
                    config={},
                    sink=FakeElement("shared_videosink"),
                    build_overlay_tail=_OverlayCapture(),
                    prepare_sink=lambda: FakeElement("shared_videosink"),
                )

    def test_raises_when_convert_element_missing(self) -> None:
        from gi.repository import Gst  # noqa: F401

        fake = make_fake_gst(missing_elements={"videoconvert"})
        with patch("gi.repository.Gst", fake):
            with pytest.raises(RuntimeError, match="videoconvert GStreamer element not found"):
                NdiInput().create_pipeline(
                    config={},
                    sink=FakeElement("shared_videosink"),
                    build_overlay_tail=_OverlayCapture(),
                    prepare_sink=lambda: FakeElement("shared_videosink"),
                )

    def test_raises_when_ndisrc_to_demux_link_fails(self) -> None:
        from gi.repository import Gst  # noqa: F401

        fake = make_fake_gst(link_fail_kinds={"ndisrc"})
        with patch("gi.repository.Gst", fake):
            with pytest.raises(RuntimeError, match="ndisrc"):
                NdiInput().create_pipeline(
                    config={},
                    sink=FakeElement("shared_videosink"),
                    build_overlay_tail=_OverlayCapture(),
                    prepare_sink=lambda: FakeElement("shared_videosink"),
                )

    def test_raises_when_video_queue_to_convert_link_fails(self) -> None:
        from gi.repository import Gst  # noqa: F401

        # Only one ``queue`` is created up front (the video queue).
        # Audio queue is created lazily inside the pad-added callback.
        fake = make_fake_gst(link_fail_kinds={"queue"})
        with patch("gi.repository.Gst", fake):
            with pytest.raises(RuntimeError, match="ndi_video_queue"):
                NdiInput().create_pipeline(
                    config={},
                    sink=FakeElement("shared_videosink"),
                    build_overlay_tail=_OverlayCapture(),
                    prepare_sink=lambda: FakeElement("shared_videosink"),
                )

    def test_pad_added_video_skips_when_queue_sink_already_linked(self, fake_gst) -> None:
        """If the demux fires a second video pad after the first has
        linked, the callback drops it silently – covers 179→exit."""
        overlay = _OverlayCapture()
        sink = FakeElement("shared_videosink")
        pipeline = NdiInput().create_pipeline(
            config={},
            sink=sink,
            build_overlay_tail=overlay,
            prepare_sink=lambda: sink,
        )
        demux = pipeline.get_by_name("demux")
        cb = next(cb for sig, cb in demux.signals if sig == "pad-added")
        # Pre-link the queue sink pad.
        queue_sink = pipeline.get_by_name("ndi_video_queue").get_static_pad("sink")
        FakePad("existing").link(queue_sink)
        pad = FakePad("video_extra")
        cb(demux, pad)
        assert pad.linked_to is None

    def test_pad_added_video_logs_error_on_link_failure(self, fake_gst) -> None:
        """When the demux fires a video pad whose link returns non-OK,
        the error log fires – covers line 186."""
        overlay = _OverlayCapture()
        sink = FakeElement("shared_videosink")
        pipeline = NdiInput().create_pipeline(
            config={},
            sink=sink,
            build_overlay_tail=overlay,
            prepare_sink=lambda: sink,
        )
        demux = pipeline.get_by_name("demux")
        cb = next(cb for sig, cb in demux.signals if sig == "pad-added")
        pad = FakePad("video_0", link_returns="some_error")
        cb(demux, pad)  # must not raise

    def test_pad_added_unknown_prefix_falls_through(self, fake_gst) -> None:
        """A pad whose name doesn't start with ``video`` or ``audio``
        falls through both arms silently – covers 191→exit."""
        overlay = _OverlayCapture()
        sink = FakeElement("shared_videosink")
        pipeline = NdiInput().create_pipeline(
            config={},
            sink=sink,
            build_overlay_tail=overlay,
            prepare_sink=lambda: sink,
        )
        demux = pipeline.get_by_name("demux")
        cb = next(cb for sig, cb in demux.signals if sig == "pad-added")
        pad = FakePad("metadata_0")
        cb(demux, pad)
        # Neither queue nor fakesink is added for unknown pads.
        assert pipeline.get_by_name("ndi_audio_queue") is None
        assert pipeline.get_by_name("audio_fakesink") is None

    def test_pad_added_audio_returns_when_factory_yields_none(self) -> None:
        """If the audio queue or fakesink can't be created, the audio
        pad is dropped silently rather than crashing."""
        from gi.repository import Gst  # noqa: F401

        # Initial pipeline build creates the video queue from "queue"
        # successfully – but we want the *second* "queue" make() (the
        # audio queue) to return None. Use a counter to switch behaviour
        # mid-test.
        fake = make_fake_gst()
        original_make = fake.ElementFactory.make
        queue_count = {"n": 0}

        def _make(kind: str, name: str):
            if kind == "queue":
                queue_count["n"] += 1
                if queue_count["n"] >= 2:
                    return None
            return original_make(kind, name)

        fake.ElementFactory.make = staticmethod(_make)  # type: ignore[assignment]
        overlay = _OverlayCapture()
        sink = FakeElement("shared_videosink")
        with patch("gi.repository.Gst", fake):
            pipeline = NdiInput().create_pipeline(
                config={},
                sink=sink,
                build_overlay_tail=overlay,
                prepare_sink=lambda: sink,
            )
            demux = pipeline.get_by_name("demux")
            cb = next(cb for sig, cb in demux.signals if sig == "pad-added")
            audio_pad = FakePad("audio_0")
            cb(demux, audio_pad)  # must not raise
            # No audio queue was added because make() returned None.
            assert pipeline.get_by_name("ndi_audio_queue") is None

    def test_pad_added_audio_logs_error_when_queue_to_fakesink_link_fails(self) -> None:
        """Audio path: queue.link(fakesink) returning False fires the
        error log and returns without touching pad.link – covers 206-207."""
        from gi.repository import Gst  # noqa: F401

        fake = make_fake_gst()
        original_make = fake.ElementFactory.make
        queue_count = {"n": 0}

        def _make(kind: str, name: str):
            elem = original_make(kind, name)
            # Second ``queue`` is the audio queue – make its link fail.
            if kind == "queue":
                queue_count["n"] += 1
                if queue_count["n"] >= 2 and elem is not None:
                    elem._link_ok = False
            return elem

        fake.ElementFactory.make = staticmethod(_make)  # type: ignore[assignment]
        overlay = _OverlayCapture()
        sink = FakeElement("shared_videosink")
        with patch("gi.repository.Gst", fake):
            pipeline = NdiInput().create_pipeline(
                config={},
                sink=sink,
                build_overlay_tail=overlay,
                prepare_sink=lambda: sink,
            )
            demux = pipeline.get_by_name("demux")
            cb = next(cb for sig, cb in demux.signals if sig == "pad-added")
            audio_pad = FakePad("audio_0")
            cb(demux, audio_pad)
            # Audio queue exists but the link failed – so fakesink's
            # sink pad is never reached.
            assert pipeline.get_by_name("ndi_audio_queue") is not None
            assert audio_pad.linked_to is None

    def test_pad_added_audio_adds_fakesink_branch(self, fake_gst) -> None:
        overlay = _OverlayCapture()
        sink = FakeElement("shared_videosink")
        pipeline = NdiInput().create_pipeline(
            config={},
            sink=sink,
            build_overlay_tail=overlay,
            prepare_sink=lambda: sink,
        )
        demux = pipeline.get_by_name("demux")
        cb = next(cb for sig, cb in demux.signals if sig == "pad-added")
        audio_pad = FakePad("audio_0")
        cb(demux, audio_pad)

        audio_queue = pipeline.get_by_name("ndi_audio_queue")
        fakesink = pipeline.get_by_name("audio_fakesink")
        assert audio_queue is not None
        assert fakesink is not None
        assert fakesink.properties["sync"] is False
        assert fakesink.properties["silent"] is True

    def test_pad_added_audio_logs_warning_when_pad_link_fails(self, fake_gst, caplog) -> None:
        """A failed audio-pad link is logged (parity with the video branch)
        rather than silently swallowed."""
        overlay = _OverlayCapture()
        sink = FakeElement("shared_videosink")
        pipeline = NdiInput().create_pipeline(
            config={},
            sink=sink,
            build_overlay_tail=overlay,
            prepare_sink=lambda: sink,
        )
        demux = pipeline.get_by_name("demux")
        cb = next(cb for sig, cb in demux.signals if sig == "pad-added")
        pad = FakePad("audio_0", link_returns="some_error")
        with caplog.at_level("WARNING", logger="openfollow.video.inputs.ndi"):
            cb(demux, pad)  # must not raise
        assert any("ndi_audio_queue" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# Lifecycle hook
# --------------------------------------------------------------------------- #


class TestOnBusAsyncDone:
    def test_forces_pipeline_latency_to_zero(self) -> None:
        pipeline = FakePipeline("ndi")
        NdiInput().on_bus_async_done(pipeline)
        assert pipeline.latency_values == [0]


# --------------------------------------------------------------------------- #
# Config helpers
# --------------------------------------------------------------------------- #


class TestGetSourceLabel:
    def test_returns_configured_source_name(self) -> None:
        assert NdiInput.get_source_label({"ndi_source_name": "HOST (Studio)"}) == ("HOST (Studio)")

    def test_missing_config_returns_empty_string(self) -> None:
        assert NdiInput.get_source_label({}) == ""


# --------------------------------------------------------------------------- #
# Sanity: we're not leaking logger warnings between tests
# --------------------------------------------------------------------------- #


def test_logger_is_module_scoped() -> None:
    assert isinstance(ndi_module.logger, logging.Logger)
    assert ndi_module.logger.name == "openfollow.video.inputs.ndi"
