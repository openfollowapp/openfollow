# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Contract tests for every ``VideoInputBase`` subclass.

Each built-in input plugin must honour the base-class contract regardless
of which backend it wraps.  These tests are parametrized over the live
plugin registry so that dropping a new file into ``openfollow/video/inputs``
is automatically covered.
"""

from __future__ import annotations

import gc

import pytest

from openfollow.configuration import AppConfig
from openfollow.video.inputs import get_registry
from openfollow.video.inputs._base import (
    ConfigField,
    InputCapabilities,
    ReconnectPolicy,
    VideoInputBase,
    WebRoute,
)

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# Plugin discovery
# --------------------------------------------------------------------------- #


def _plugin_params() -> list[pytest.param]:
    return [pytest.param(cls, id=cls.input_id) for cls in sorted(get_registry().values(), key=lambda c: c.input_id)]


# --------------------------------------------------------------------------- #
# Class-level metadata
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("plugin", _plugin_params())
class TestPluginMetadata:
    def test_input_id_is_nonempty(self, plugin: type[VideoInputBase]) -> None:
        assert plugin.input_id, f"{plugin.__name__} has empty input_id"

    def test_display_name_is_nonempty(self, plugin: type[VideoInputBase]) -> None:
        assert plugin.display_name, f"{plugin.__name__} has empty display_name"

    def test_registered_under_input_id(self, plugin: type[VideoInputBase]) -> None:
        assert get_registry()[plugin.input_id] is plugin


# --------------------------------------------------------------------------- #
# Config fields
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("plugin", _plugin_params())
class TestConfigFields:
    def test_returns_list_of_config_fields(self, plugin: type[VideoInputBase]) -> None:
        fields = plugin.config_fields()
        assert isinstance(fields, list)
        assert fields, f"{plugin.__name__} declared no config fields"
        for field in fields:
            assert isinstance(field, ConfigField)

    def test_every_field_exists_on_appconfig(self, plugin: type[VideoInputBase]) -> None:
        cfg = AppConfig()
        for field in plugin.config_fields():
            assert hasattr(cfg, field.name), (
                f"{plugin.__name__} declares config field {field.name!r} not present on AppConfig"
            )

    def test_defaults_match_declared_type(self, plugin: type[VideoInputBase]) -> None:
        for field in plugin.config_fields():
            if field.default is None:
                continue
            if field.type is int:
                assert isinstance(field.default, int)
            elif field.type is float:
                assert isinstance(field.default, (int, float))
            elif field.type is bool:
                assert isinstance(field.default, bool)
            elif field.type is str:
                assert isinstance(field.default, str)

    def test_labels_are_human_readable(self, plugin: type[VideoInputBase]) -> None:
        for field in plugin.config_fields():
            assert field.label
            assert field.label.strip() == field.label


# --------------------------------------------------------------------------- #
# Capabilities + reconnect policy
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("plugin", _plugin_params())
class TestCapabilities:
    def test_returns_input_capabilities(self, plugin: type[VideoInputBase]) -> None:
        caps = plugin.capabilities()
        assert isinstance(caps, InputCapabilities)

    def test_source_selection_without_discovery_is_disallowed(self, plugin: type[VideoInputBase]) -> None:
        """Source-selection overlay is driven by discovery – the combination
        ``has_source_selection=True`` + ``has_source_discovery=False`` would
        open the picker over an empty list and wedge the UI."""
        caps = plugin.capabilities()
        if caps.has_source_selection:
            assert caps.has_source_discovery, f"{plugin.__name__} selects sources but has no discovery"


@pytest.mark.parametrize("plugin", _plugin_params())
class TestReconnectPolicy:
    def test_returns_reconnect_policy(self, plugin: type[VideoInputBase]) -> None:
        policy = plugin.reconnect_policy()
        assert isinstance(policy, ReconnectPolicy)

    def test_backoff_values_are_sane(self, plugin: type[VideoInputBase]) -> None:
        policy = plugin.reconnect_policy()
        assert policy.min_delay >= 0
        assert policy.max_delay >= policy.min_delay
        assert policy.backoff_multiplier >= 1.0
        assert policy.connection_timeout >= 0


# --------------------------------------------------------------------------- #
# Web UI
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("plugin", _plugin_params())
class TestWebUI:
    def test_returns_html_string(self, plugin: type[VideoInputBase]) -> None:
        html = plugin.web_ui_html({})
        assert isinstance(html, str)
        assert html.strip(), f"{plugin.__name__} produced empty HTML"

    def test_references_every_config_field(self, plugin: type[VideoInputBase]) -> None:
        """Each plugin's form must expose every field it persists."""
        values = {f.name: f.default for f in plugin.config_fields()}
        html = plugin.web_ui_html(values)
        for field in plugin.config_fields():
            assert field.name in html, f"{plugin.__name__}.web_ui_html omits field {field.name!r}"

    def test_escapes_malicious_string_values(self, plugin: type[VideoInputBase]) -> None:
        payload = '<script>alert("xss")</script>'
        values = {f.name: payload if f.type is str else f.default for f in plugin.config_fields()}
        html = plugin.web_ui_html(values)
        assert "<script>alert" not in html, f"{plugin.__name__} failed to escape user-controlled value"

    def test_web_routes_returns_list(self, plugin: type[VideoInputBase]) -> None:
        routes = plugin.web_routes()
        assert isinstance(routes, list)
        for route in routes:
            assert isinstance(route, WebRoute)
            assert route.method in {"GET", "POST"}
            assert route.path.startswith("/")

    def test_web_route_handlers_resolve(self, plugin: type[VideoInputBase]) -> None:
        instance = plugin()
        for route in plugin.web_routes():
            handler = instance.get_web_route_handler(route.handler_name)
            assert callable(handler), (
                f"{plugin.__name__} declared route {route.path} with missing handler {route.handler_name!r}"
            )


# --------------------------------------------------------------------------- #
# Config round-tripping
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("plugin", _plugin_params())
class TestConfigRoundTrip:
    def test_get_values_from_default_appconfig(self, plugin: type[VideoInputBase]) -> None:
        cfg = AppConfig()
        values = plugin.get_config_field_values(cfg)
        assert set(values.keys()) == {f.name for f in plugin.config_fields()}

    def test_apply_config_fields_roundtrip_through_web_form(self, plugin: type[VideoInputBase]) -> None:
        cfg = AppConfig()
        form: dict[str, object] = {}
        for field in plugin.config_fields():
            if field.type is str:
                form[field.name] = "roundtrip-value"
            elif field.type is int:
                form[field.name] = 123
            elif field.type is float:
                form[field.name] = 1.5

        plugin.apply_config_fields(cfg, form)

        for field in plugin.config_fields():
            if field.name not in form:
                continue
            if field.type is str:
                assert getattr(cfg, field.name) == "roundtrip-value"
            elif field.type is int:
                assert getattr(cfg, field.name) == 123
            elif field.type is float:
                assert getattr(cfg, field.name) == pytest.approx(1.5)

    def test_apply_config_fields_ignores_unparseable_numbers(self, plugin: type[VideoInputBase]) -> None:
        cfg = AppConfig()
        before = plugin.get_config_field_values(cfg)
        form = {f.name: "not-a-number" for f in plugin.config_fields() if f.type in (int, float)}
        if not form:
            pytest.skip("Plugin has no numeric fields")
        plugin.apply_config_fields(cfg, form)
        after = plugin.get_config_field_values(cfg)
        for name in form:
            assert after[name] == before[name]

    def test_config_changed_false_for_identical_configs(self, plugin: type[VideoInputBase]) -> None:
        cfg = AppConfig()
        assert plugin.config_changed(cfg, cfg) is False

    def test_config_changed_detects_per_field_mutation(self, plugin: type[VideoInputBase]) -> None:
        for field in plugin.config_fields():
            old = AppConfig()
            new = AppConfig()
            if field.type is str:
                setattr(new, field.name, "different-value")
            elif field.type is int:
                setattr(new, field.name, int(field.default or 0) + 7)
            elif field.type is float:
                setattr(new, field.name, float(field.default or 0) + 0.5)
            elif field.type is bool:
                setattr(new, field.name, not bool(field.default))
            else:
                continue
            assert plugin.config_changed(old, new) is True, (
                f"{plugin.__name__}.config_changed missed mutation of {field.name}"
            )


# --------------------------------------------------------------------------- #
# Source-label + lifecycle hooks
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("plugin", _plugin_params())
class TestSourceLabel:
    def test_label_from_defaults_is_string(self, plugin: type[VideoInputBase]) -> None:
        # NDI's default source name is empty so an empty label is legitimate
        # – we only assert that the return contract (str) holds.
        values = {f.name: f.default for f in plugin.config_fields()}
        label = plugin.get_source_label(values)
        assert isinstance(label, str)

    def test_label_with_populated_config_is_nonempty(self, plugin: type[VideoInputBase]) -> None:
        values: dict[str, object] = {}
        for field in plugin.config_fields():
            if field.type is str:
                values[field.name] = "my-source"
            else:
                values[field.name] = field.default
        label = plugin.get_source_label(values)
        assert label.strip()


@pytest.mark.parametrize("plugin", _plugin_params())
class TestOptionalHooks:
    def test_is_available_returns_tuple(self, plugin: type[VideoInputBase]) -> None:
        available, reason = plugin.is_available()
        assert isinstance(available, bool)
        assert isinstance(reason, str)

    def test_discover_sources_returns_list(self, plugin: type[VideoInputBase]) -> None:
        if plugin.capabilities().has_source_discovery:
            pytest.skip(
                "Plugin-provided source discovery may touch real system "
                "boundaries (subprocess, /dev, NDI ctypes); per-plugin "
                "tests cover those cases hermetically."
            )
        assert isinstance(plugin.discover_sources(timeout=0.0), list)

    def test_cleanup_and_lifecycle_hooks_are_no_op_by_default(self, plugin: type[VideoInputBase]) -> None:
        instance = plugin()
        # None of the optional hooks should raise for a plain dummy value.
        instance.on_caps_changed(1920, 1080)
        instance.cleanup()


# --------------------------------------------------------------------------- #
# Base-class behaviour (not plugin-specific)
# --------------------------------------------------------------------------- #


class TestVideoInputBaseContract:
    def test_subclass_without_input_id_raises(self) -> None:
        with pytest.raises(TypeError, match="must define input_id"):

            class _BadPlugin(VideoInputBase):  # noqa: D401 – local fixture
                """Intentionally missing input_id."""

                @classmethod
                def config_fields(cls):
                    return []

                @classmethod
                def capabilities(cls):
                    return InputCapabilities()

                @classmethod
                def reconnect_policy(cls):
                    return ReconnectPolicy()

                def create_pipeline(self, config, sink, build_overlay_tail, prepare_sink):
                    return None

                @classmethod
                def web_ui_html(cls, config):
                    return ""

        # Force GC to clean up the half-built subclass from the exception
        # traceback; prevents it from lingering in __subclasses__() and
        # affecting other tests on the same worker process.
        gc.collect()

    def test_esc_helper_escapes_and_handles_falsy(self) -> None:
        assert VideoInputBase._esc("<b>&") == "&lt;b&gt;&amp;"
        assert VideoInputBase._esc("") == ""
        assert VideoInputBase._esc(None) == ""
        # Falsy-but-meaningful values escape to their text – only None is dropped.
        assert VideoInputBase._esc(0) == "0"
        assert VideoInputBase._esc(0.0) == "0.0"
        assert VideoInputBase._esc(False) == "False"

    def test_get_web_route_handler_returns_none_for_missing_method(self) -> None:
        # Plain object – NOT a ``VideoInputBase`` subclass – to avoid
        # leaking into ``VideoInputBase.__subclasses__()`` and from
        # there into ``_registry`` on any later test that resets
        # ``_discovered``. The method body is just
        # ``getattr(self, name, None)``, so any object works as ``self``.
        class _Stub:
            pass

        handler = VideoInputBase.get_web_route_handler(_Stub(), "does_not_exist")
        assert handler is None

    def test_default_get_source_label_returns_display_name(self) -> None:
        # Plain class – NOT a ``VideoInputBase`` subclass – see comment
        # on ``test_get_web_route_handler_returns_none_for_missing_method``.
        # ``get_source_label`` is a classmethod returning ``cls.display_name``;
        # invoke its underlying function with this duck-typed namespace.
        class _Stub:
            display_name = "Stubby"

        label = VideoInputBase.get_source_label.__func__(_Stub, {})
        assert label == "Stubby"


class TestApplyConfigFieldsFloatBranch:
    """No real plugin currently declares a float-type config field, so the
    base-class ``apply_config_fields`` float arm needs synthetic coverage.
    The non-float arms are exercised by every plugin's roundtrip test."""

    def _make_float_namespace(self) -> type:
        # Plain class – NOT a ``VideoInputBase`` subclass – so it can't
        # leak into the plugin registry via
        # ``VideoInputBase.__subclasses__()`` after a later test that
        # resets ``_discovered``. The base class's ``__init_subclass__``
        # rejects ``input_id = ""`` for concrete subclasses, and we
        # need ``apply_config_fields``'s logic intact, so we invoke its
        # underlying function directly with this duck-typed namespace.
        class _FloatNS:
            @classmethod
            def config_fields(cls):
                # Two fields so the post-float-assignment ``continue``
                # back to the loop head is exercised. Field zero is bool
                # – a type ``apply_config_fields`` doesn't handle, which
                # makes the ``elif f.type is float`` evaluate False on
                # the first iteration and drop into the next loop step
                # (covers the 185→175 partial branch).
                return [
                    ConfigField("debug_logging", bool, False, "Debug"),
                    ConfigField("v4l2_framerate", float, 30.0, "Frame rate"),
                    ConfigField("v4l2_device", str, "/dev/video0", "Device"),
                ]

        return _FloatNS

    @staticmethod
    def _apply(cls_namespace, cfg, form):
        # ``apply_config_fields`` is a ``@classmethod`` on
        # ``VideoInputBase``; ``__func__`` reaches its underlying
        # function so we can invoke it with an arbitrary ``cls``
        # without subclassing the abstract base.
        return VideoInputBase.apply_config_fields.__func__(cls_namespace, cfg, form)

    def test_float_value_is_assigned(self) -> None:
        ns = self._make_float_namespace()
        cfg = AppConfig()
        # All fields present so the loop iterates: bool (skipped by all
        # if/elif arms → 185→175), float (assigned), str (assigned).
        self._apply(
            ns,
            cfg,
            {
                "debug_logging": True,
                "v4l2_framerate": "59.94",
                "v4l2_device": "/dev/video1",
            },
        )
        assert cfg.v4l2_framerate == pytest.approx(59.94)
        assert cfg.v4l2_device == "/dev/video1"

    def test_unparseable_float_is_swallowed(self) -> None:
        ns = self._make_float_namespace()
        cfg = AppConfig()
        before = cfg.v4l2_framerate
        self._apply(ns, cfg, {"v4l2_framerate": "definitely-not-a-float"})
        # The except (TypeError, ValueError): pass arm fires.
        assert cfg.v4l2_framerate == before
