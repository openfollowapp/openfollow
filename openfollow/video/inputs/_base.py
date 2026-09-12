# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Base class and declaration types for video input plugins.

``VideoInputBase`` is the ABC each protocol plugin subclasses; the dataclasses
(``ConfigField``, ``WebRoute``, ``ReconnectPolicy``, ``InputCapabilities``)
declare its config fields, routes, reconnection behaviour, and feature flags.
Also provides a shared helper for positive-int coercion; URI credential
handling lives in :mod:`openfollow.uri_redaction`.
"""

from __future__ import annotations

import html
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


def coerce_positive_int(value: Any, default: int) -> int:
    """Return ``value`` as an int ``>= 1``, else ``default``.

    Camera ``width`` / ``height`` / ``framerate`` are interpolated straight
    into a caps string; a ``0``/negative/non-int from a hand-edited config or
    crafted POST would otherwise produce caps that never negotiate and wedge
    the receiver in a reconnect loop. ``configuration.py`` doesn't clamp these
    plugin fields, so the pipeline builder guards them defensively.
    """
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if n >= 1 else default


@dataclass
class ConfigField:
    """A config field that this input needs stored in config.toml."""

    name: str  # e.g. "ndi_source_name"
    type: type  # str, int, float, bool
    default: Any  # default value
    label: str  # human-readable label for web UI
    choices: tuple[tuple[str, str], ...] = field(default_factory=tuple)  # enum-style choices for list picker
    device_editable: bool = True  # False = web-only field; the on-device URL editor / list picker skip it
    # False = keep edge whitespace on save. Credentials only: it can be part of
    # the secret and is invisible in a password field, so trimming one fails
    # authentication with nothing on screen to explain why.
    strip: bool = True


@dataclass
class WebRoute:
    """An additional HTTP endpoint this input exposes."""

    method: str  # "GET" or "POST"
    path: str  # e.g. "/video-input/ndi/sources"
    handler_name: str  # method name on the VideoInputBase subclass


@dataclass
class ReconnectPolicy:
    """Reconnection behaviour for this input type."""

    max_attempts: int = 0  # 0 = infinite
    min_delay: float = 0.5  # seconds
    max_delay: float = 3.0  # seconds
    backoff_multiplier: float = 1.5
    connection_timeout: float = 8.0  # seconds; 0 = no timeout
    fallback_to_selection: bool = False
    heal_interval: float = 0.0  # background probe interval for URL-based inputs
    stall_timeout: float = 0.0  # watchdog timeout for silent stream stalls


@dataclass
class InputCapabilities:
    """Feature flags for a video input type."""

    has_source_discovery: bool = False
    has_source_selection: bool = False
    discovery_interval: float = 5.0  # seconds between scans
    selection_title: str = "SELECT SOURCE"
    hotkey: str = ""  # keyboard shortcut (e.g. "n")
    hotkey_label: str = ""  # HUD label (e.g. "N=NDI")
    force_zero_latency: bool = False  # force pipeline latency to 0 on ASYNC_DONE


class VideoInputBase(ABC):
    """Abstract base for video input plugins.

    Subclasses **must** implement the abstract methods below. Override
    the optional hooks to customise latency handling, source discovery,
    web routes, etc.
    """

    input_id: str = ""  # unique ID, e.g. "ndi"
    display_name: str = ""  # human-readable, e.g. "NDI"

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.input_id == "" and not getattr(cls, "__abstractmethods__", None):
            raise TypeError(f"{cls.__name__} must define input_id")

    # -- Must implement -------------------------------------------------------

    @classmethod
    @abstractmethod
    def config_fields(cls) -> list[ConfigField]:
        """Declare config fields this input needs in config.toml."""
        ...

    @classmethod
    @abstractmethod
    def capabilities(cls) -> InputCapabilities:
        """Declare capabilities and feature flags."""
        ...

    @classmethod
    @abstractmethod
    def reconnect_policy(cls) -> ReconnectPolicy:
        """Declare reconnection behaviour."""
        ...

    @abstractmethod
    def create_pipeline(
        self,
        config: dict[str, Any],
        sink: Any,
        build_overlay_tail: Callable[..., Any],
        prepare_sink: Callable[..., Any],
    ) -> Any:
        """Build the GStreamer pipeline for this input type.

        Args:
            config: dict of this input's config field values.
            sink: the pre-created shared gtksink element (use only via
                *prepare_sink* which detaches it from any prior pipeline).
            build_overlay_tail: ``(pipeline, last_videoconvert, sink) -> None``
                – appends the Cairo overlay chain and links to *sink*.
            prepare_sink: ``() -> sink`` – detaches and returns the shared
                sink, ready to be added to a new pipeline.

        Returns:
            A ``Gst.Pipeline`` ready for ``set_state(PLAYING)``.

        Error path:
            The base provides no teardown for a failed build, so the
            implementation MUST release everything it acquired before it
            raises – NULL-transition and unref any elements it created or
            ``add``ed, and close any fd / socket / device handle – and MUST
            NOT return a partially-built pipeline. The receiver
            (``VideoReceiver.create_pipeline``) only catches the exception
            and falls back to a placeholder; it never receives, and so
            cannot tear down, a half-built pipeline. Today's subclasses
            satisfy this implicitly: they create only NULL-state elements
            (no fd/socket opens before ``READY``), so an aborted build
            leaks nothing GC can't reclaim. A subclass that opens a real
            handle during the build must clean it up itself.
        """
        ...

    @classmethod
    @abstractmethod
    def web_ui_html(cls, config: dict[str, Any]) -> str:
        """Return an HTML fragment for this input's settings in the web UI.

        The fragment is inserted into the general-settings form when this
        input type is selected in the Source Type dropdown.
        """
        ...

    # -- Optional hooks -------------------------------------------------------

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        """Return (available, reason) for whether this input can be used.

        Override to check for required system dependencies (e.g. GStreamer
        plugins, shared libraries).  The default assumes always available.
        """
        return True, ""

    def on_bus_async_done(self, pipeline: Any) -> None:  # noqa: B027 – intentional optional hook
        """Called on ``ASYNC_DONE`` bus message.  Override for latency tuning."""

    def on_bus_eos(self, pipeline: Any) -> bool:
        """Called on ``EOS``. Return ``True`` if the input handled it (e.g. a
        looping clip seeking back to the start) so the receiver does NOT treat
        end-of-stream as a disconnect. The default reports it unhandled."""
        return False

    def on_bus_segment_done(self, pipeline: Any) -> bool:
        """Called on ``SEGMENT_DONE`` (only fired for an input that armed a
        segment seek). Return ``True`` if the input handled it by queuing the
        next iteration of a seamless loop. The default reports it unhandled."""
        return False

    def on_caps_changed(self, width: int, height: int) -> None:  # noqa: B027 – intentional optional hook
        """Called when video resolution is detected from caps-changed."""

    @classmethod
    def discover_sources(cls, timeout: float = 2.0) -> list[str]:
        """Discover available sources (for inputs with source discovery)."""
        return []

    @classmethod
    def web_routes(cls) -> list[WebRoute]:
        """Declare additional HTTP endpoints this input needs."""
        return []

    def get_web_route_handler(self, name: str) -> Callable[..., Any] | None:
        """Return the handler callable for a declared web route."""
        return getattr(self, name, None)

    @classmethod
    def get_source_label(cls, config: dict[str, Any]) -> str:
        """Return a human-readable label for the current source."""
        return cls.display_name

    @classmethod
    def apply_config_fields(cls, cfg: Any, form_data: dict[str, Any]) -> None:
        """Apply web-form data to an ``AppConfig`` object.

        The default implementation handles simple top-level fields.
        """
        for f in cls.config_fields():
            if f.name in form_data:
                val = form_data[f.name]
                if f.type is str:
                    if val is None:
                        setattr(cfg, f.name, f.default)
                    else:
                        text = str(val)
                        setattr(cfg, f.name, text.strip() if f.strip else text)
                elif f.type is int:
                    # ``OverflowError`` too: a crafted POST of ``1e400`` decodes
                    # to ``float('inf')`` and ``int(inf)`` raises it (not
                    # ValueError) – without this a malformed value 500s the
                    # request instead of being dropped to the prior value.
                    try:
                        setattr(cfg, f.name, int(val))
                    except (TypeError, ValueError, OverflowError):
                        pass
                elif f.type is float:
                    try:
                        setattr(cfg, f.name, float(val))
                    except (TypeError, ValueError, OverflowError):
                        pass

    @classmethod
    def get_config_field_values(cls, cfg: Any) -> dict[str, Any]:
        """Extract this input's config values from an ``AppConfig``."""
        result: dict[str, Any] = {}
        for f in cls.config_fields():
            result[f.name] = getattr(cfg, f.name, f.default)
        return result

    @classmethod
    def config_changed(cls, old_cfg: Any, new_cfg: Any) -> bool:
        """Return ``True`` if config fields relevant to this input changed."""
        for f in cls.config_fields():
            if getattr(old_cfg, f.name, f.default) != getattr(new_cfg, f.name, f.default):
                return True
        return False

    def cleanup(self) -> None:  # noqa: B027 – intentional optional hook
        """Called when the input is being destroyed. Clean up threads etc."""

    # -- Helpers --------------------------------------------------------------

    @staticmethod
    def _esc(value: Any) -> str:
        """HTML-escape a value for safe insertion into a template.

        Only ``None`` collapses to an empty string; a falsy-but-meaningful
        value (``0`` / ``0.0`` / ``False``) escapes to its text so a
        numeric field rendered through this helper isn't silently dropped.
        """
        return "" if value is None else html.escape(str(value))
