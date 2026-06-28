# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Shared hermetic GStreamer fake for video-input plugin tests.

Each plugin's ``create_pipeline`` method does ``from gi.repository import Gst``
at call time, so test code patches ``gi.repository.Gst`` with the
:func:`make_fake_gst` return value for the duration of a test.

The fake records element properties, link order, static/request-pad
lookups, and registered signal callbacks so tests can assert on both
the topology of the built pipeline and the runtime ``pad-added`` /
``element-added`` callbacks the plugins wire up.
"""

from __future__ import annotations

from typing import Any


class FakePad:
    """Recording Gst pad – knows its name, whether it's been linked to, and
    returns a preset link-return code."""

    def __init__(self, name: str = "pad", *, link_returns: str = "ok") -> None:
        self._name = name
        self._link_returns = link_returns
        self.linked_to: FakePad | None = None
        self._linked_into = False

    def get_name(self) -> str:
        return self._name

    def is_linked(self) -> bool:
        return self.linked_to is not None or self._linked_into

    def link(self, other: FakePad) -> str:
        self.linked_to = other
        if isinstance(other, FakePad):
            other._linked_into = True
        return self._link_returns

    def get_current_caps(self) -> FakeCaps:
        return FakeCaps(f"video/{self._name}")

    def query_caps(self, _filter: Any) -> FakeCaps:
        return FakeCaps(f"video/{self._name}")


class FakeCaps:
    def __init__(self, value: str) -> None:
        self._value = value

    def to_string(self) -> str:
        return self._value


class FakeElement:
    """Recording Gst element – tests inspect ``properties``, ``links``, and
    ``signals`` after ``create_pipeline`` runs."""

    def __init__(self, name: str, *, link_ok: bool = True) -> None:
        self.name = name
        self.properties: dict[str, object] = {}
        self.links: list[FakeElement] = []
        self.signals: list[tuple[str, Any]] = []
        self._static_pads: dict[str, FakePad] = {}
        self._link_ok = link_ok

    def get_name(self) -> str:
        return self.name

    def set_property(self, key: str, value: object) -> None:
        self.properties[key] = value

    def link(self, other: FakeElement) -> bool:
        self.links.append(other)
        return self._link_ok

    def get_static_pad(self, name: str) -> FakePad:
        return self._static_pads.setdefault(name, FakePad(name))

    def request_pad_simple(self, template: str) -> FakePad:
        return FakePad(template)

    def get_request_pad(self, template: str) -> FakePad:
        return FakePad(template)

    def connect(self, signal: str, callback: Any) -> None:
        self.signals.append((signal, callback))

    def sync_state_with_parent(self) -> None:
        self.properties["synced"] = True

    def get_factory(self) -> Any:
        return self.properties.get("_factory")


class FakePipeline:
    def __init__(self, name: str) -> None:
        self.name = name
        self.elements: list[FakeElement] = []
        self.latency_values: list[int] = []
        self.seeks: list[tuple[Any, int, int]] = []
        self.seek_ok = True

    def add(self, element: FakeElement) -> None:
        self.elements.append(element)

    def set_latency(self, value: int) -> None:
        self.latency_values.append(value)

    def seek_simple(self, fmt: Any, flags: int, position: int) -> bool:
        self.seeks.append((fmt, flags, position))
        return self.seek_ok

    def get_by_name(self, name: str) -> FakeElement | None:
        for elem in self.elements:
            if elem.name == name:
                return elem
        return None


def make_fake_gst(
    *,
    missing_elements: set[str] | None = None,
    known_factories: dict[str, Any] | None = None,
    link_fail_kinds: set[str] | None = None,
):
    """Return a FakeGst class wired to the given overrides.

    Arguments:
        missing_elements: element ``kind`` values (the first arg to
            ``Gst.ElementFactory.make``) that the factory should return
            ``None`` for.  Use to test ``RuntimeError`` branches when a
            GStreamer plugin is unavailable.
        known_factories: maps a factory name (``Gst.ElementFactory.find``
            arg) to a recording factory object.  ``None`` means "not
            installed".
        link_fail_kinds: element ``kind`` values whose ``link()`` method
            should return ``False`` rather than ``True``.  Lets tests
            target individual ``raise RuntimeError("Failed to link ...")``
            arms in plugin pipelines.
    """

    missing = set(missing_elements or set())
    factories = dict(known_factories or {})
    link_fail = set(link_fail_kinds or set())

    class _Rank:
        MARGINAL = 64
        SECONDARY = 128
        PRIMARY = 256

    class FakeFactory:
        def __init__(self, name: str) -> None:
            self.name = name
            self.ranks: list[int] = []

        def set_rank(self, value: int) -> None:
            self.ranks.append(value)

        def get_name(self) -> str:
            return self.name

        def get_metadata(self, key: str) -> str:
            return "Video/Decoder" if key == "klass" else ""

    class FakeElementFactory:
        @staticmethod
        def make(kind: str, name: str) -> FakeElement | None:
            if kind in missing:
                return None
            return FakeElement(name, link_ok=kind not in link_fail)

        @staticmethod
        def find(name: str) -> Any:
            return factories.get(name)

    class FakePipelineNs:
        @staticmethod
        def new(name: str) -> FakePipeline:
            return FakePipeline(name)

    class FakeCapsNs:
        @staticmethod
        def from_string(value: str) -> FakeCaps:
            return FakeCaps(value)

    class FakePadLinkReturn:
        OK = "ok"
        NOFORMAT = "noformat"
        REFUSED = "refused"

    class FakeState:
        PLAYING = "playing"

    class FakeFormat:
        TIME = "time"

    class FakeSeekFlags:
        FLUSH = 1
        KEY_UNIT = 32

    class FakeMessageType:
        ASYNC_DONE = "async-done"
        ERROR = "error"
        EOS = "eos"
        STATE_CHANGED = "state-changed"

    class FakeGst:
        Pipeline = FakePipelineNs
        ElementFactory = FakeElementFactory
        Caps = FakeCapsNs
        PadLinkReturn = FakePadLinkReturn
        State = FakeState
        Format = FakeFormat
        SeekFlags = FakeSeekFlags
        MessageType = FakeMessageType
        Rank = _Rank

        @staticmethod
        def init(_args: Any) -> None:
            return None

    # Expose the factory-tracking collection so tests can reach into it
    # after the fact (e.g. to verify decoder-rank updates).
    FakeGst.factories = factories  # type: ignore[attr-defined]
    return FakeGst
