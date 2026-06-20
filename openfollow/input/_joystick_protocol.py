# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Type-only Protocol shim for pygame's joystick API.

pygame's stubs treat ``pygame.joystick.Joystick`` as a function, not a class.
Under ``mypy --strict``, this breaks type annotations and masks attribute errors.

We define Protocols for the subset of the Joystick and SDL2 Controller APIs
we actually use, allowing proper type-checking at annotation sites.

Add a method here when the codebase needs to call a method not yet listed.
The Protocol is structural – adding methods is backwards-compatible; forgetting
one surfaces the same ``attr-defined`` error mypy already emits.
"""

from __future__ import annotations

from typing import Protocol


class JoystickProtocol(Protocol):
    """The subset of ``pygame.joystick.Joystick`` we depend on.

    Method signatures mirror the pygame docs; return types are the
    runtime values pygame produces, not its weak-stub declarations.

    Method bodies are ``...`` – Protocol classes only describe the
    interface, the runtime calls go to the concrete pygame object
    (or the test fixture). The ``# pragma: no cover`` per method
    excludes these stub bodies from coverage.
    """

    def get_axis(self, axis: int) -> float: ...  # pragma: no cover
    def get_button(self, button: int) -> int: ...  # pragma: no cover
    def get_guid(self) -> str: ...  # pragma: no cover
    def get_hat(self, hat: int) -> tuple[int, int]: ...  # pragma: no cover
    def get_name(self) -> str: ...  # pragma: no cover
    def get_numaxes(self) -> int: ...  # pragma: no cover
    def get_numbuttons(self) -> int: ...  # pragma: no cover
    def get_numhats(self) -> int: ...  # pragma: no cover
    def quit(self) -> None: ...  # pragma: no cover


class ControllerProtocol(Protocol):
    """The subset of ``pygame._sdl2.controller.Controller`` we depend on.

    Same upstream-stub gap as ``JoystickProtocol`` – pygame's SDL2
    Controller wrapper is unstubbed under ``--strict``. We use it
    alongside the raw Joystick API as a higher-level fallback that
    normalises trigger axes, so the two Protocols co-exist in the
    input pipeline.

    Note the deliberate type asymmetry vs. ``JoystickProtocol``:
    SDL2 ``Controller.get_axis`` returns a signed 16-bit integer in
    [-32768, 32767], whereas ``Joystick.get_axis`` returns a
    pre-normalised float in [-1.0, 1.0]. ``GamepadHandler.
    _normalize_controller_axis`` divides by 32768.0 to bridge the two.
    """

    def get_axis(self, axis: int) -> int: ...  # pragma: no cover
    def get_button(self, button: int) -> int: ...  # pragma: no cover
    def quit(self) -> None: ...  # pragma: no cover
