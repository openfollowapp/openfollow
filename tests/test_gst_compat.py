"""Tests for the GStreamer binding compatibility shims."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openfollow.video.gst_compat import unwrap_gst_structure

pytestmark = pytest.mark.unit


def test_unwrap_passes_through_structure_with_typed_getters() -> None:
    """A binding that exposes get_value is returned untouched."""
    structure = SimpleNamespace(get_value=lambda key: {"width": 1280}.get(key))
    assert unwrap_gst_structure(structure) is structure


def test_unwrap_none_returns_none() -> None:
    assert unwrap_gst_structure(None) is None


def test_unwrap_reaches_through_structure_wrapper() -> None:
    """GStreamer 1.26.2 wraps the structure and drops the typed getters; the
    real structure on the name-mangled attribute is returned instead."""
    real = SimpleNamespace(get_value=lambda key: {"width": 800, "height": 600}.get(key))
    # Mimic StructureWrapper: no get_value, real structure under the mangled name.
    wrapper = SimpleNamespace(**{"_StructureWrapper__structure": real})
    assert not hasattr(wrapper, "get_value")

    unwrapped = unwrap_gst_structure(wrapper)

    assert unwrapped is real
    assert unwrapped.get_value("width") == 800


def test_unwrap_returns_object_unchanged_when_no_wrapped_attr() -> None:
    """An unknown object lacking both get_value and the wrapped attribute is
    returned as-is rather than raising."""
    obj = SimpleNamespace(other=1)
    assert unwrap_gst_structure(obj) is obj
