# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Template envelope and per-type payload validation."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

TEMPLATE_VERSION: int = 1  # bumped only for breaking format changes
TEMPLATE_FILE_SUFFIX: str = ".openfollowtemplate"  # filename suffix for loader glob
VALID_TYPES: tuple[str, ...] = (
    "osc_output",
    "camera_grid",
    "zones",
)  # fixed set of template types


class TemplateValidationError(ValueError):
    """Raised when a template envelope or payload fails validation."""


def _new_uuid_hex() -> str:
    """Mint a fresh hex-only UUID for missing/empty id fields."""
    return uuid.uuid4().hex


@dataclass
class OpenFollowTemplate:
    """One operator-saved or system-bundled template.

    Payload is validated on construction. Folder determines is_system
    (folder is source of truth, not JSON tag).
    """

    version: int = TEMPLATE_VERSION
    type: str = ""
    id: str = ""
    name: str = ""
    is_system: bool = False
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Strict envelope validation; no auto-fixup.
        if not isinstance(self.version, int) or isinstance(self.version, bool):
            raise TemplateValidationError(
                f"version must be int, got {type(self.version).__name__}",
            )
        if self.version != TEMPLATE_VERSION:
            raise TemplateValidationError(
                f"unsupported version {self.version!r} (this build understands version {TEMPLATE_VERSION})",
            )
        if not isinstance(self.type, str) or self.type not in VALID_TYPES:
            raise TemplateValidationError(
                f"unknown type {self.type!r} (expected one of {', '.join(VALID_TYPES)})",
            )
        if not isinstance(self.id, str) or not self.id.strip():
            # Mint id for older/hand-edited files; next save writes it back.
            self.id = _new_uuid_hex()
        else:
            self.id = self.id.strip()
        if not isinstance(self.name, str):
            raise TemplateValidationError(
                f"name must be str, got {type(self.name).__name__}",
            )
        self.name = self.name.strip()
        if not self.name:
            raise TemplateValidationError("name must not be empty")
        if not isinstance(self.is_system, bool):
            raise TemplateValidationError(
                f"is_system must be bool, got {type(self.is_system).__name__}",
            )
        if not isinstance(self.payload, dict):
            raise TemplateValidationError(
                f"payload must be object, got {type(self.payload).__name__}",
            )
        # Per-type payload validation via config dataclass.
        validate_payload(self.type, self.payload)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to on-disk JSON shape with canonical field order."""
        return {
            "version": self.version,
            "type": self.type,
            "id": self.id,
            "name": self.name,
            "is_system": self.is_system,
            "payload": dict(self.payload),
        }


def _validate_osc_output_trigger(trigger: Any) -> None:
    """Validate trigger kind and per-kind fields at load time."""
    from openfollow.configuration import (
        VALID_OSC_STREAM_MODES,
        VALID_TRIGGER_EDGES,
        VALID_TRIGGER_KINDS,
        VALID_TRIGGER_MODIFIERS,
    )

    if not isinstance(trigger, dict):
        raise TemplateValidationError(
            f"osc_output payload 'trigger' must be object, got {type(trigger).__name__}",
        )
    kind = trigger.get("kind")
    if not isinstance(kind, str):
        raise TemplateValidationError(
            f"osc_output payload 'trigger.kind' must be a string (got {type(kind).__name__})",
        )
    # Only selectable kinds; derived from the one source of truth so this
    # can't drift from the trigger union again.
    if kind not in VALID_TRIGGER_KINDS:
        raise TemplateValidationError(
            f"osc_output payload 'trigger.kind' must be one of {', '.join(VALID_TRIGGER_KINDS)} (got {kind!r})",
        )
    # Reject unknown keys; silently dropped at apply would lose operator intent.
    allowed_trigger_keys: dict[str, frozenset[str]] = {
        "stream": frozenset({"kind", "rate_hz", "mode", "min_change_m"}),
        "hotkey": frozenset({"kind", "key", "modifiers", "edge"}),
        "controller_button": frozenset({"kind", "button", "edge"}),
        "midi_message": frozenset({"kind", "patch_id", "type", "channel", "number", "value"}),
        "fader_on_change": frozenset({"kind", "fader", "rate_hz", "marker_id"}),
    }
    extra_keys = set(trigger) - allowed_trigger_keys[kind]
    if extra_keys:
        raise TemplateValidationError(
            f"osc_output payload 'trigger' has unknown key(s) for kind {kind!r}: {', '.join(sorted(extra_keys))}",
        )
    if kind == "stream":
        if "rate_hz" in trigger:
            from openfollow.configuration import VALID_OSC_TRANSMITTER_RATES

            rate = trigger["rate_hz"]
            if not isinstance(rate, int) or isinstance(rate, bool):
                raise TemplateValidationError(
                    f"osc_output payload 'trigger.rate_hz' must be int (got {type(rate).__name__})",
                )
            # Row config snaps an out-of-set rate to the nearest valid one.
            # Reject up front so the template doesn't auto-mutate on apply.
            if rate not in VALID_OSC_TRANSMITTER_RATES:
                raise TemplateValidationError(
                    f"osc_output payload 'trigger.rate_hz' must be one of "
                    f"{', '.join(str(r) for r in VALID_OSC_TRANSMITTER_RATES)} "
                    f"(got {rate})",
                )
        if "mode" in trigger and trigger["mode"] not in VALID_OSC_STREAM_MODES:
            raise TemplateValidationError(
                f"osc_output payload 'trigger.mode' must be one of "
                f"{', '.join(VALID_OSC_STREAM_MODES)} (got {trigger['mode']!r})",
            )
        if "min_change_m" in trigger:
            mc = trigger["min_change_m"]
            if isinstance(mc, bool) or not isinstance(mc, (int, float)):
                raise TemplateValidationError(
                    f"osc_output payload 'trigger.min_change_m' must be a number (got {type(mc).__name__})",
                )
            # Reject negative; config would silently clamp to 0.
            if mc < 0:
                raise TemplateValidationError(
                    f"osc_output payload 'trigger.min_change_m' must be >= 0 (got {mc})",
                )
        return
    if kind == "hotkey":
        key = trigger.get("key", "")
        if not isinstance(key, str):
            raise TemplateValidationError(
                f"osc_output payload 'trigger.key' must be a string (got {type(key).__name__})",
            )
        if "modifiers" in trigger:
            mods = trigger["modifiers"]
            if not isinstance(mods, (list, tuple)):
                raise TemplateValidationError(
                    f"osc_output payload 'trigger.modifiers' must be a list (got {type(mods).__name__})",
                )
            for i, m in enumerate(mods):
                if not isinstance(m, str) or m not in VALID_TRIGGER_MODIFIERS:
                    raise TemplateValidationError(
                        f"osc_output payload 'trigger.modifiers[{i}]' "
                        f"must be one of {', '.join(VALID_TRIGGER_MODIFIERS)} "
                        f"(got {m!r})",
                    )
        if "edge" in trigger and trigger["edge"] not in VALID_TRIGGER_EDGES:
            raise TemplateValidationError(
                f"osc_output payload 'trigger.edge' must be one of "
                f"{', '.join(VALID_TRIGGER_EDGES)} (got {trigger['edge']!r})",
            )
        return
    if kind in ("midi_message", "fader_on_change"):
        # Reject any field the trigger dataclass would coerce/clamp/snap on
        # apply – a saved template must round-trip unchanged.
        from dataclasses import asdict

        from openfollow.configuration import _trigger_from_dict

        canonical = asdict(_trigger_from_dict(trigger))
        for tkey, tval in trigger.items():
            expected = canonical.get(tkey)
            if tkey != "kind" and (expected != tval or type(expected) is not type(tval)):
                raise TemplateValidationError(
                    f"osc_output payload 'trigger.{tkey}' is invalid for kind {kind!r} (would become {expected!r})",
                )
        return
    # kind == "controller_button" – the only remaining option.
    button = trigger.get("button", "")
    if not isinstance(button, str):
        raise TemplateValidationError(
            f"osc_output payload 'trigger.button' must be a string (got {type(button).__name__})",
        )
    if "edge" in trigger and trigger["edge"] not in VALID_TRIGGER_EDGES:
        raise TemplateValidationError(
            f"osc_output payload 'trigger.edge' must be one of "
            f"{', '.join(VALID_TRIGGER_EDGES)} (got {trigger['edge']!r})",
        )


def validate_payload(template_type: str, payload: Mapping[str, Any]) -> None:
    """Run the per-type payload validator. Raises
    :class:`TemplateValidationError` on any shape failure; returns
    ``None`` on success.

    Each branch reuses the existing :mod:`openfollow.configuration`
    dataclass for that surface so the validation rules don't drift
    from what ``load_config`` accepts. We instantiate the dataclass
    *only* for its ``__post_init__`` side effect – the result is
    discarded, the original payload dict survives untouched.
    """
    # Lazy import to keep this module import-cheap (the loader is
    # called at startup before most of the config layer wakes up) and
    # to side-step a future circular import if ``configuration`` ever
    # learns about templates.
    from openfollow.configuration import (
        CameraConfig,
        GridConfig,
        TriggerZonesConfig,
    )

    if template_type == "osc_output":
        # Payload carries full operator-tunable shape.
        # enabled and marker_id NOT in schema (per-binding, not reusable).
        # Reject unknown keys; silent drops at apply would lose intent.
        allowed_payload_keys = frozenset(
            {
                "address",
                "args",
                "name",
                "host",
                "port",
                "protocol",
                "rate_hz",
                "trigger",
            }
        )
        extra_keys = set(payload) - allowed_payload_keys
        if extra_keys:
            raise TemplateValidationError(
                f"osc_output payload has unknown key(s): {', '.join(sorted(extra_keys))}",
            )
        if "address" not in payload:
            raise TemplateValidationError(
                "osc_output payload missing required key 'address'",
            )
        if not isinstance(payload["address"], str):
            raise TemplateValidationError(
                f"osc_output payload 'address' must be str, got {type(payload['address']).__name__}",
            )
        # Addresses must start with /; empty is allowed (not yet configured).
        if payload["address"] and not payload["address"].startswith("/"):
            raise TemplateValidationError(
                f"osc_output payload 'address' must start with '/' (got {payload['address']!r})",
            )
        args = payload.get("args", [])
        if not isinstance(args, list):
            raise TemplateValidationError(
                f"osc_output payload 'args' must be list, got {type(args).__name__}",
            )
        for i, arg in enumerate(args):
            if not isinstance(arg, str):
                raise TemplateValidationError(
                    f"osc_output payload 'args[{i}]' must be str, got {type(arg).__name__}",
                )
        # Optional fields: validate bounds to catch silent coercions at apply.
        from openfollow.configuration import (
            VALID_OSC_TRANSMITTER_PROTOCOLS,
            VALID_OSC_TRANSMITTER_RATES,
        )

        for field_name in ("name", "host"):
            if field_name in payload and not isinstance(payload[field_name], str):
                raise TemplateValidationError(
                    f"osc_output payload {field_name!r} must be str, got {type(payload[field_name]).__name__}",
                )
        if "protocol" in payload:
            protocol = payload["protocol"]
            if not isinstance(protocol, str):
                raise TemplateValidationError(
                    f"osc_output payload 'protocol' must be str, got {type(protocol).__name__}",
                )
            if protocol not in VALID_OSC_TRANSMITTER_PROTOCOLS:
                raise TemplateValidationError(
                    f"osc_output payload 'protocol' must be one of "
                    f"{', '.join(VALID_OSC_TRANSMITTER_PROTOCOLS)} "
                    f"(got {protocol!r})",
                )
        if "port" in payload:
            port = payload["port"]
            if not isinstance(port, int) or isinstance(port, bool):
                # bool is int subclass; reject to avoid port=true → 1.
                raise TemplateValidationError(
                    f"osc_output payload 'port' must be int, got {type(port).__name__}",
                )
            if not 1 <= port <= 65535:
                raise TemplateValidationError(
                    f"osc_output payload 'port' must be in 1..65535 (got {port})",
                )
        if "rate_hz" in payload:
            rate = payload["rate_hz"]
            if not isinstance(rate, int) or isinstance(rate, bool):
                raise TemplateValidationError(
                    f"osc_output payload 'rate_hz' must be int, got {type(rate).__name__}",
                )
            if rate not in VALID_OSC_TRANSMITTER_RATES:
                # Reject; config would snap to nearest (losing intent).
                raise TemplateValidationError(
                    f"osc_output payload 'rate_hz' must be one of "
                    f"{', '.join(str(r) for r in VALID_OSC_TRANSMITTER_RATES)} "
                    f"(got {rate})",
                )
        if "trigger" in payload:
            _validate_osc_output_trigger(payload["trigger"])
        return

    if template_type == "camera_grid":
        # Requires both camera and grid keys (partial apply would desync).
        if "camera" not in payload or "grid" not in payload:
            missing = sorted(
                {"camera", "grid"} - set(payload),
            )
            raise TemplateValidationError(
                f"camera_grid payload must contain BOTH 'camera' and 'grid' (missing: {', '.join(missing)})",
            )
        camera_dict = payload["camera"]
        if not isinstance(camera_dict, dict):
            raise TemplateValidationError(
                f"camera_grid payload 'camera' must be object, got {type(camera_dict).__name__}",
            )
        try:
            CameraConfig(**camera_dict)
        except TypeError as exc:
            raise TemplateValidationError(
                f"camera_grid payload 'camera' invalid: {exc}",
            ) from exc
        grid_dict = payload["grid"]
        if not isinstance(grid_dict, dict):
            raise TemplateValidationError(
                f"camera_grid payload 'grid' must be object, got {type(grid_dict).__name__}",
            )
        try:
            GridConfig(**grid_dict)
        except TypeError as exc:
            raise TemplateValidationError(
                f"camera_grid payload 'grid' invalid: {exc}",
            ) from exc
        return

    if template_type == "zones":
        # Requires zones array to prevent destructive factory-reset on apply.
        if not isinstance(payload, dict) or "zones" not in payload:
            raise TemplateValidationError(
                "zones payload must include a 'zones' array (the full "
                "trigger_zones section snapshot); empty payloads would "
                "wipe all configured zones on apply",
            )
        # ``TriggerZonesConfig.__post_init__`` would keep a non-dict zone
        # entry verbatim, persisting a bare str/int that the zone engine then
        # dereferences (``zone.vertices``) → AttributeError on the eval
        # thread. Require a list of objects here (the other per-type
        # validators already coerce every element through a dataclass).
        zones = payload["zones"]
        if not isinstance(zones, list):
            raise TemplateValidationError(
                f"zones payload 'zones' must be a list, got {type(zones).__name__}",
            )
        for i, z in enumerate(zones):
            if not isinstance(z, dict):
                raise TemplateValidationError(
                    f"zones payload 'zones[{i}]' must be object, got {type(z).__name__}",
                )
        try:
            TriggerZonesConfig(**payload)
        except TypeError as exc:
            raise TemplateValidationError(
                f"zones payload invalid: {exc}",
            ) from exc
        return

    # Defensive: unreachable if gates are in sync.
    raise TemplateValidationError(  # pragma: no cover
        f"no payload validator for type {template_type!r}",
    )
