"""Compatibility shims for GStreamer Python binding differences."""

from __future__ import annotations

from typing import Any

# Name-mangled attribute on GStreamer 1.26.2's StructureWrapper that holds the
# real Gst.Structure.
_WRAPPED_STRUCTURE_ATTR = "_StructureWrapper__structure"


def unwrap_gst_structure(structure: Any) -> Any:
    """Return a ``Gst.Structure`` that exposes the typed getters.

    GStreamer 1.26.2 hands structures from ``Gst.Caps.get_structure`` and
    ``Gst.Device.get_properties`` back wrapped in a ``StructureWrapper`` that
    doesn't forward the typed getters (``get_value`` / ``get_fraction`` /
    ``get_string``). The real structure lives on a name-mangled attribute; reach
    through to it when the getters are missing. Every other binding returns a
    structure that already has them, so pass it through unchanged.
    """
    if structure is None or hasattr(structure, "get_value"):
        return structure
    return getattr(structure, _WRAPPED_STRUCTURE_ATTR, structure)
