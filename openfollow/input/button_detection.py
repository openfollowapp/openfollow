# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""In-app button detection wizard for mapping raw joystick buttons."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from openfollow.input._joystick_protocol import JoystickProtocol
from openfollow.input.gamepad import (
    BUTTON_NAME_TO_ID,
    CONTROLLER_AXIS_TRIGGERLEFT,
    CONTROLLER_AXIS_TRIGGERRIGHT,
    HAT_DOWN,
    HAT_LEFT,
    HAT_RIGHT,
    HAT_UP,
    LT_AXIS_INDICES,
    RT_AXIS_INDICES,
)
from openfollow.runtime.overlay_state import ButtonDetectionState

if TYPE_CHECKING:
    from openfollow.app import OpenFollowApp

logger = logging.getLogger(__name__)

# Ordered sequence of inputs the wizard asks the user to press.
WIZARD_STEPS: tuple[str, ...] = (
    "A",
    "B",
    "X",
    "Y",
    "LB",
    "RB",
    "LT",
    "RT",
    "BACK",
    "START",
    "DPAD_UP",
    "DPAD_DOWN",
    "DPAD_LEFT",
    "DPAD_RIGHT",
)

# Steps that are detected via analog trigger axes rather than buttons.
_TRIGGER_STEPS: frozenset[str] = frozenset({"LT", "RT"})

# Sentinel range for axis detections: AXIS_BASE - axis_index.
AXIS_BASE = -100

# Map hat sentinel -> standard BUTTON_NAME_TO_ID label.
_HAT_TO_LABEL: dict[int, str] = {
    HAT_UP: "DPAD_UP",
    HAT_DOWN: "DPAD_DOWN",
    HAT_LEFT: "DPAD_LEFT",
    HAT_RIGHT: "DPAD_RIGHT",
}

# Positive SDL2 logical button ids (negative sentinels exclude LT/RT).
_PROBE_LOGICAL_IDS: tuple[int, ...] = tuple(sorted({v for v in BUTTON_NAME_TO_ID.values() if v >= 0}))
_LOGICAL_ID_TO_NAME: dict[int, str] = {v: k for k, v in BUTTON_NAME_TO_ID.items() if v >= 0}

# Minimum deflection from baseline to count as a trigger pull.
_TRIGGER_THRESHOLD = 0.4


def _normalize_sdl2_trigger(raw: int) -> float:
    """Scale a raw SDL2 trigger axis (signed 16-bit) to [0, 1], matching the runtime."""
    return max(0.0, min(1.0, float(raw) / 32768.0))


# Cooldown between wizard steps to prevent re-reading released buttons.
_STEP_COOLDOWN_S = 0.4

# Grace period so wizard start press doesn't flow into step 1.
_WIZARD_START_GRACE_S = 0.5

# Delay trigger button commit to allow analog axis to catch up first.
_TRIGGER_BUTTON_DEFER_S = 0.3


def _read_hat_directions(joystick: JoystickProtocol) -> set[int]:
    """Return the set of hat sentinel IDs currently active."""
    active: set[int] = set()
    for hat_idx in range(joystick.get_numhats()):
        hx, hy = joystick.get_hat(hat_idx)
        if hy > 0:
            active.add(HAT_UP)
        if hy < 0:
            active.add(HAT_DOWN)
        if hx < 0:
            active.add(HAT_LEFT)
        if hx > 0:
            active.add(HAT_RIGHT)
    return active


@dataclass
class ButtonDetectionWizard:
    """Drives an interactive button-by-button detection flow.

    The wizard steps through ``WIZARD_STEPS``, asking the user to press
    each labelled button.  For every press it records which raw joystick
    button index fired (``results``, for the raw-joystick fallback path)
    and – when an SDL2 GameController wrapper is present – which SDL2
    logical button fired (``sdl_results``, for the SDL2 ``map_*`` remap).
    When all steps are complete (or the user cancels) the caller can read
    these and apply the mapping.

    Hat-based D-Pads are detected via ``joystick.get_hat()`` and stored
    using negative sentinel values (HAT_UP, HAT_DOWN, ...).

    Analog triggers (LT/RT) are detected via axis deflection and stored
    using ``AXIS_BASE - axis_index`` sentinels.
    """

    app: OpenFollowApp = field(repr=False)
    _step: int = 0
    # label -> raw button index (or hat/axis sentinel) that fired. Drives
    # ``build_raw_index_map`` (the raw-joystick fallback path) and trigger
    # swap detection.
    results: dict[str, int] = field(default_factory=dict)
    # label -> SDL2 *logical* button id that was pressed when this step
    # fired, captured only when an SDL2 GameController wrapper is present.
    # Drives ``build_detection_map`` (the SDL2 ``map_*`` remap). Kept
    # separate from ``results`` because the two backends number buttons
    # differently: feeding a raw index into the SDL2-logical ``map_*``
    # (the old bug) mis-maps any pad whose raw order != SDL2 logical order.
    sdl_results: dict[str, int] = field(default_factory=dict)
    # Name of the joystick the wizard is running against.
    controller_name: str = ""
    # SDL GUID of that joystick – persisted alongside the name so the
    # runtime can later detect a connected-vs-calibrated mismatch.
    controller_guid: str = ""
    # Tracks which raw buttons/hats were held when the wizard started,
    # keyed by pygame controller index.  Per-joystick because button
    # indices are not portable across pads: a button id valid on a
    # 15-button GameSir is out-of-range on an 11-button Generic, and a
    # shared set would either hit ``pygame.error: Invalid joystick
    # button`` when the smaller pad called ``get_button`` on a foreign
    # entry, or lose semantic correctness when one pad's release
    # silently pruned the other pad's still-held filter.
    _initial_held: dict[int, set[int]] = field(default_factory=dict)
    # Baseline axis values captured before each trigger step.
    # Outer key = pygame controller index; inner key = axis idx.
    # Per-joystick because two pads with different physical layouts
    # (e.g. LT on raw axis 2 vs raw axis 4) rest at different values –
    # a shared baseline left one pad permanently "deflected" relative
    # to the other's rest, so ``_axes_settled`` never cleared and the
    # wizard hung after the first trigger step.
    _axis_baselines: dict[int, dict[int, float]] = field(default_factory=dict)
    # SDL2 logical trigger axis baselines: (left, right).  Only present
    # when the joystick also has an SDL2 Controller wrapper.  Used to
    # detect SDL2-level trigger swap independently from raw buttons.
    _sdl2_trigger_baselines: tuple[float, float] | None = None
    # Max |delta| observed on each SDL2 trigger axis during the current
    # trigger step: {"LEFT": float, "RIGHT": float}.
    _sdl2_probe_current: dict[str, float] = field(default_factory=dict)
    # Resolved SDL2 deflection per trigger label: {"LT": "LEFT"|"RIGHT"}.
    _sdl2_probe_results: dict[str, str] = field(default_factory=dict)
    # Max |delta| per raw axis during the current trigger step.
    _raw_axis_probe_current: dict[int, float] = field(default_factory=dict)
    # Raw axis index that moved most during each trigger step.
    _raw_axis_probe_results: dict[str, int] = field(default_factory=dict)
    # When True, wait for all axes to settle near baseline before
    # accepting the next input.  Set after a trigger axis is detected
    # so the released trigger doesn't immediately fire the next step.
    _waiting_for_release: bool = False
    # Monotonic timestamp before which poll() ignores all input.
    _step_ready_at: float = 0.0
    # True once the start-up grace period has expired and ``_initial_held``
    # has been snapshotted from the live controller state.
    _armed: bool = False
    # At trigger steps, buffer the first button-fallback press and hold
    # off on recording it for a short window so an analog trigger axis
    # has a chance to catch up and supersede the button.  Only active
    # between the press and the deadline.
    _pending_trigger_btn: int | None = None
    _pending_trigger_btn_deadline: float = 0.0
    # Pad lock: once any pad records its first press the wizard binds to that
    # controller index and ignores every other connected pad for the rest of
    # the run. Without this, two live pads (or two operators) could interleave
    # presses into one mapping, and the SDL2 trigger probe / name / GUID would
    # be attributed to the wrong physical controller (#314 family).
    _active_idx: int | None = None
    _done: bool = False

    def __post_init__(self) -> None:
        # _input_manager is guaranteed non-None by the caller; assert for strict typing.
        assert self.app._input_manager is not None
        handler = self.app._input_manager.gamepad_handler
        for idx, joystick in handler.joysticks.items():
            self._capture_axis_baselines(idx, joystick)
        # ``controller_name`` / ``controller_guid`` are captured from the pad
        # that records the first press (``_lock_to_pad``), NOT the
        # first-enumerated one, so the saved map's ``mapped_controller_guid``
        # always names the controller actually mapped.
        # Block input during start-up grace so entry press can't flow into step 1.
        self._step_ready_at = time.monotonic() + _WIZARD_START_GRACE_S
        logger.info(
            "Button detection wizard started (%d connected, %d steps).",
            len(handler.joysticks),
            len(WIZARD_STEPS),
        )

    def _capture_axis_baselines(
        self,
        idx: int,
        joystick: JoystickProtocol,
    ) -> None:
        """Snapshot all axis rest positions for the given controller."""
        self._axis_baselines[idx] = {i: joystick.get_axis(i) for i in range(joystick.get_numaxes())}
        self._capture_sdl2_trigger_baselines(idx)

    def _capture_sdl2_trigger_baselines(self, idx: int) -> None:
        """Snapshot the SDL2 logical trigger rest positions for pad ``idx``.

        Reads only that pad's controller wrapper – never "the first one" –
        so a two-pad bench measures the delta against the right rest position.
        """
        assert self.app._input_manager is not None  # see __post_init__
        handler = self.app._input_manager.gamepad_handler
        ctrl = handler.controllers.get(idx)
        if ctrl is None:
            self._sdl2_trigger_baselines = None
            return
        try:
            left = _normalize_sdl2_trigger(ctrl.get_axis(CONTROLLER_AXIS_TRIGGERLEFT))
            right = _normalize_sdl2_trigger(ctrl.get_axis(CONTROLLER_AXIS_TRIGGERRIGHT))
            self._sdl2_trigger_baselines = (left, right)
        except Exception:
            logger.debug("SDL2 trigger baseline read failed for pad %d", idx, exc_info=True)
            self._sdl2_trigger_baselines = None

    def _capture_sdl_logical(self, idx: int, label: str) -> None:
        """Record which SDL2 *logical* button is pressed for this step.

        Called at record time (the physical button is still held) when an
        SDL2 GameController wrapper exists for this pad. Probes the logical
        buttons the app can map and stores the first one reported pressed.
        That is the authoritative answer to "which SDL2 logical button does
        this physical button drive", which is exactly what ``map_*`` needs –
        unlike the raw joystick index, which SDL2's ``get_button`` numbers
        differently. With no SDL2 controller (raw-only pad) nothing is
        recorded and ``build_detection_map`` falls back to identity.
        """
        assert self.app._input_manager is not None  # see __post_init__
        handler = self.app._input_manager.gamepad_handler
        ctrl = handler.controllers.get(idx)
        if ctrl is None:
            return
        if not hasattr(ctrl, "get_button"):
            # Wrapper without get_button (test stub / raw-only pad): nothing to
            # probe – build_detection_map falls back to identity.
            return
        for logical_id in _PROBE_LOGICAL_IDS:
            try:
                if ctrl.get_button(logical_id):
                    self.sdl_results[label] = logical_id
                    return
            except Exception:
                # Transient per-id read error – skip just this id and keep
                # probing the rest, so one bad read doesn't drop a real map_*
                # correction (the whole-probe abort was the bug).
                logger.debug("SDL2 logical read failed for pad %d id %d", idx, logical_id, exc_info=True)
                continue

    def _probe_raw_axes(
        self,
        idx: int,
        joystick: JoystickProtocol,
    ) -> None:
        """Track max |delta| on every raw axis during a trigger step.

        Used so we can still identify which physical axis the user's
        trigger drives even when a digital button fires before the axis
        crosses the detection threshold.  A relaxed threshold is applied
        at commit time so a partial deflection still counts.
        """
        baseline = self._axis_baselines.get(idx, {})
        for axis_idx in range(joystick.get_numaxes()):
            b = baseline.get(axis_idx, 0.0)
            delta = abs(joystick.get_axis(axis_idx) - b)
            if delta > self._raw_axis_probe_current.get(axis_idx, 0.0):
                self._raw_axis_probe_current[axis_idx] = delta

    def _probe_sdl2_triggers(self, idx: int) -> None:
        """Track max |delta| on pad ``idx``'s SDL2 triggers during a step.

        Samples only the active pad's controller, so a pull on the pad being
        mapped is measured (not pad 0's), and a stray deflection on a different
        pad can't fabricate or suppress a swap.
        """
        if self._sdl2_trigger_baselines is None:
            return
        assert self.app._input_manager is not None  # see __post_init__
        handler = self.app._input_manager.gamepad_handler
        ctrl = handler.controllers.get(idx)
        if ctrl is None:
            return
        base_l, base_r = self._sdl2_trigger_baselines
        try:
            dl = abs(_normalize_sdl2_trigger(ctrl.get_axis(CONTROLLER_AXIS_TRIGGERLEFT)) - base_l)
            dr = abs(_normalize_sdl2_trigger(ctrl.get_axis(CONTROLLER_AXIS_TRIGGERRIGHT)) - base_r)
        except Exception:
            logger.debug("SDL2 trigger probe read failed for pad %d", idx, exc_info=True)
            return
        if dl > self._sdl2_probe_current.get("LEFT", 0.0):
            self._sdl2_probe_current["LEFT"] = dl
        if dr > self._sdl2_probe_current.get("RIGHT", 0.0):
            self._sdl2_probe_current["RIGHT"] = dr

    @property
    def current_label(self) -> str:
        if self._step < len(WIZARD_STEPS):
            return WIZARD_STEPS[self._step]
        return ""

    @property
    def is_done(self) -> bool:
        return self._done

    def get_state(self) -> ButtonDetectionState:
        """Build the overlay snapshot for the renderer."""
        return ButtonDetectionState(
            active=True,
            current_label=self.current_label,
            step=self._step,
            total_steps=len(WIZARD_STEPS),
            completed=dict(self.results),
        )

    def _snapshot_held(self, joystick: JoystickProtocol) -> set[int]:
        """Snapshot all currently-held buttons and hat directions."""
        held: set[int] = set()
        for b in range(joystick.get_numbuttons()):
            if joystick.get_button(b):
                held.add(b)
        held |= _read_hat_directions(joystick)
        return held

    def _capture_initial_held(self, handler: object) -> None:
        """Snapshot held buttons/hats across every connected joystick.

        Called at the end of the start-up grace period.  Anything still
        held at this point – typically the entry button if the user
        hasn't released it yet – is filtered out by ``_initial_held``
        until ``_prune_released`` observes it released.  Stored
        per-joystick so a button index valid on one pad isn't fed to
        another pad's ``get_button`` (raises ``pygame.error: Invalid
        joystick button`` when out of range).
        """
        joysticks = handler.joysticks  # type: ignore[attr-defined]
        self._initial_held = {idx: self._snapshot_held(j) for idx, j in joysticks.items()}

    def _prune_released(
        self,
        idx: int,
        joystick: JoystickProtocol,
    ) -> None:
        """Drop ids from ``_initial_held`` once they're observed released.

        Without this, a button held when the wizard starts (typically the
        same A that confirmed the "Button Detection" menu entry) stays
        permanently in ``_initial_held`` and the user's first labelled
        press is silently ignored, since ``_initial_held`` is otherwise
        only refreshed inside ``_record()``.
        """
        active_hats = _read_hat_directions(joystick)
        still_held: set[int] = set()
        for entry in self._initial_held.get(idx, set()):
            if entry < 0:
                if entry in active_hats:
                    still_held.add(entry)
            elif joystick.get_button(entry):
                still_held.add(entry)
        self._initial_held[idx] = still_held

    def poll(self) -> None:
        """Call once per frame.  Reads raw joystick buttons/hats/axes and advances."""
        if self._done:
            return
        if time.monotonic() < self._step_ready_at:
            return
        assert self.app._input_manager is not None  # see __post_init__
        handler = self.app._input_manager.gamepad_handler
        handler._pump_events()

        # First poll after the start-up grace: snapshot what's still held
        # so the entry press is filtered if the user is still holding it.
        if not self._armed:
            self._capture_initial_held(handler)
            self._armed = True

        for idx, joystick in handler.joysticks.items():
            # Pad lock: once a pad has recorded its first press, ignore every
            # other connected pad for the rest of the run so two pads can't
            # interleave into one mapping (and so the trigger probe / waiting
            # state below only ever concern the locked pad).
            if self._active_idx is not None and idx != self._active_idx:
                continue

            # After a trigger detection, wait for all axes to settle
            # before accepting any new input.
            if self._waiting_for_release:
                if self._axes_settled(idx, joystick):
                    self._waiting_for_release = False
                    # Re-capture clean baselines now that everything is at rest
                    self._capture_axis_baselines(idx, joystick)
                else:
                    return

            # Release entries from _initial_held as soon as the user lets
            # go of them, so the next press is detectable.
            self._prune_released(idx, joystick)

            is_trigger_step = self.current_label in _TRIGGER_STEPS

            if is_trigger_step:
                # Sample SDL2 logical trigger axes so we can detect
                # SDL2-level swap even when the raw joystick axes don't
                # respond to the trigger press.
                self._probe_sdl2_triggers(idx)
                # Track max raw-axis deflection for swap detection on
                # controllers where the button fires before the axis
                # crosses the detection threshold.
                self._probe_raw_axes(idx, joystick)
                # For trigger steps, detect axis deflection
                axis_id = self._poll_trigger_axis(idx, joystick)
                if axis_id is not None:
                    # Axis wins – cancel any pending button deferral.
                    self._pending_trigger_btn = None
                    sentinel = AXIS_BASE - axis_id
                    self._record(idx, sentinel, joystick)
                    return
                # If a button has been deferred at this step, keep
                # probing axes until the deadline – if the axis catches
                # up it supersedes the button above.
                if self._pending_trigger_btn is not None:
                    # pragma: no branch – the False arm (deadline not
                    # reached, return without recording) is reachable
                    # via the existing defer-then-supersede tests; the
                    # True arm (deadline expired, commit) is covered
                    # by ``test_poll_trigger_step_digital_fallback...``.
                    if time.monotonic() >= self._pending_trigger_btn_deadline:  # pragma: no branch
                        pending = self._pending_trigger_btn
                        self._pending_trigger_btn = None
                        self._record(idx, pending, joystick)
                    return
                # Digital button fallback (some controllers expose
                # triggers as buttons). But wait until all axes are
                # at rest first: many controllers fire the digital
                # trigger press before the analog axis crosses the
                # 0.4 detection threshold. Recording the button in
                # that window loses the axis info that
                # should_swap_triggers() needs to detect reversed
                # LT/RT wiring.
                if not self._axes_settled(idx, joystick):
                    return

            # Already-assigned raw button/hat ids – never record the
            # same physical input twice. Without this, a lingering
            # held button from a previous step (e.g. slow release of
            # RB) can bleed into the next step's detection. Trigger
            # axes are excluded: some controllers legitimately share
            # one axis between LT and RT.
            used_ids = {v for v in self.results.values() if v > AXIS_BASE}

            # Check regular buttons
            held_for_pad = self._initial_held.get(idx, set())
            for btn in range(joystick.get_numbuttons()):
                if joystick.get_button(btn) and btn not in held_for_pad:
                    # pragma: no cover – defensive guard: in normal
                    # flow ``_initial_held`` is re-snapshotted inside
                    # ``_record()`` while the recording button is still
                    # held, so a previously-bound button never reaches
                    # the ``btn not in _initial_held`` arm. The
                    # ``used_ids`` filter is a belt-and-braces guard
                    # against future changes that decouple the snapshot
                    # from the record point.
                    if btn in used_ids:  # pragma: no cover
                        continue
                    if is_trigger_step:
                        # Defer: start the grace window so a slow analog
                        # axis has a chance to supersede this button.
                        self._pending_trigger_btn = btn
                        self._pending_trigger_btn_deadline = time.monotonic() + _TRIGGER_BUTTON_DEFER_S
                        return
                    self._record(idx, btn, joystick)
                    return

            # Check hat (D-Pad) directions
            for hat_id in _read_hat_directions(joystick):
                if hat_id not in held_for_pad:
                    # pragma: no cover – same reasoning as the button
                    # used_ids guard above; ``_initial_held`` is
                    # re-snapshotted on record while the hat direction
                    # is still active, so the same direction never
                    # reaches the used_ids arm in normal flow.
                    if hat_id in used_ids:  # pragma: no cover
                        continue
                    self._record(idx, hat_id, joystick)
                    return

    def _poll_trigger_axis(
        self,
        idx: int,
        joystick: JoystickProtocol,
    ) -> int | None:
        """Check if any axis has moved significantly from its baseline.

        Returns the axis index with the largest absolute deflection
        above the threshold, or None.  Using abs() catches triggers
        that deflect in the negative direction, and picking the
        largest avoids false positives from minor stick noise.
        """
        baseline = self._axis_baselines.get(idx, {})
        best_idx: int | None = None
        best_delta = _TRIGGER_THRESHOLD
        for axis_idx in range(joystick.get_numaxes()):
            b = baseline.get(axis_idx, 0.0)
            current = joystick.get_axis(axis_idx)
            delta = abs(current - b)
            if delta > best_delta:
                best_delta = delta
                best_idx = axis_idx
        return best_idx

    def _axes_settled(self, idx: int, joystick: JoystickProtocol) -> bool:
        """Return True when all axes are close to their initial baselines.

        Used after a trigger detection to wait for the user to fully
        release the trigger before the next step accepts input.
        """
        baseline = self._axis_baselines.get(idx, {})
        settle_threshold = _TRIGGER_THRESHOLD * 0.5
        for axis_idx in range(joystick.get_numaxes()):
            b = baseline.get(axis_idx, 0.0)
            current = joystick.get_axis(axis_idx)
            if abs(current - b) > settle_threshold:
                return False
        return True

    def _record(
        self,
        idx: int,
        raw_id: int,
        joystick: JoystickProtocol,
    ) -> None:
        """Record a detected input and advance to the next step."""
        # Bind to this pad on the first record and capture its identity, so the
        # rest of the run ignores other pads and the saved map names the
        # controller that was actually pressed.
        if self._active_idx is None:
            self._lock_to_pad(idx, joystick)
        label = self.current_label
        logger.info("Button detection: pad %d recorded %s", idx, label)
        self.results[label] = raw_id
        # Capture the SDL2 logical button this physical press drives (button
        # still held here), for a correct SDL2-logical map_*. Triggers are
        # excluded from map_* anyway, so only do it for the others.
        if label not in _TRIGGER_STEPS:
            self._capture_sdl_logical(idx, label)
        self._pending_trigger_btn = None
        if raw_id <= AXIS_BASE:
            axis_idx = AXIS_BASE - raw_id
            logger.info("Button detection: %s -> axis %d", label, axis_idx)
        elif raw_id < 0:
            hat_label = _HAT_TO_LABEL.get(raw_id, "?")
            logger.info("Button detection: %s -> hat %s", label, hat_label)
        else:
            logger.info("Button detection: %s -> raw btn %d", label, raw_id)
        # For trigger steps, always wait for axes to return to baseline,
        # even when the detection came from a digital button. Some
        # controllers fire the trigger button before the analog axis
        # crosses threshold; without this hold, the still-rising axis
        # would spill into the next step and falsely register RT from
        # the same LT press.
        if label in _TRIGGER_STEPS:
            self._waiting_for_release = True
            left_max = self._sdl2_probe_current.get("LEFT", 0.0)
            right_max = self._sdl2_probe_current.get("RIGHT", 0.0)
            if max(left_max, right_max) >= _TRIGGER_THRESHOLD:
                self._sdl2_probe_results[label] = "LEFT" if left_max > right_max else "RIGHT"
                logger.info(
                    "Button detection: %s SDL2 probe -> %s (L=%.2f R=%.2f)",
                    label,
                    self._sdl2_probe_results[label],
                    left_max,
                    right_max,
                )
            self._sdl2_probe_current = {}
            if self._raw_axis_probe_current:
                # ``dict.get`` is overloaded (one-arg vs two-arg with
                # default), which mypy can't disambiguate as a ``key=``
                # callable for ``max``. A lambda over ``__getitem__``
                # is the same lookup but a single concrete callable.
                probes = self._raw_axis_probe_current
                best_axis = max(probes, key=lambda axis_idx: probes[axis_idx])
                best_delta = self._raw_axis_probe_current[best_axis]
                # Relaxed threshold – even a partial deflection is enough
                # to identify WHICH axis belongs to this trigger.
                # pragma: no branch – the False arm only fires when the
                # raw-axis probe captured a deflection but it was below
                # the relaxed 0.5×_TRIGGER_THRESHOLD floor, which the
                # existing trigger-step tests don't reproduce; the
                # truthy arm is covered by the digital-fallback tests.
                if best_delta >= _TRIGGER_THRESHOLD * 0.5:  # pragma: no branch
                    self._raw_axis_probe_results[label] = best_axis
                    logger.info(
                        "Button detection: %s raw-axis probe -> axis %d (|Δ|=%.2f)",
                        label,
                        best_axis,
                        best_delta,
                    )
            self._raw_axis_probe_current = {}
        self._step += 1
        self._step_ready_at = time.monotonic() + _STEP_COOLDOWN_S
        # Wait for ALL buttons/hats released before next step
        self._initial_held[idx] = self._snapshot_held(joystick)
        if self._step >= len(WIZARD_STEPS):
            self._done = True
            self._waiting_for_release = False
            logger.info("Button detection complete: %s", self.results)

    def _lock_to_pad(self, idx: int, joystick: JoystickProtocol) -> None:
        """Bind the wizard to ``idx`` and capture that pad's name / GUID.

        Re-snapshots the pad's SDL2 trigger baseline too: ``__post_init__``
        left it as whichever pad enumerated last, but the swap probe must
        measure against the locked pad's rest position.
        """
        self._active_idx = idx
        try:
            self.controller_name = joystick.get_name()
        except Exception:
            logger.debug("controller name read failed for pad %d", idx, exc_info=True)
            self.controller_name = ""
        try:
            self.controller_guid = joystick.get_guid()
        except Exception:
            logger.debug("controller GUID read failed for pad %d", idx, exc_info=True)
            self.controller_guid = ""
        self._capture_sdl2_trigger_baselines(idx)
        logger.info(
            "Button detection locked to pad %d (%r).",
            idx,
            self.controller_name,
        )

    def cancel(self) -> None:
        """Cancel the wizard without applying."""
        self._done = True
        logger.info("Button detection wizard cancelled.")

    def build_detection_map(self) -> dict[str, str]:
        """Convert detections into SDL2-logical ``map_*`` config values.

        ``map_*`` is consulted only on the SDL2 GameController path, where
        the app reads SDL2 *logical* buttons. So each field records the
        SDL2 logical button that the physical button drives, captured live
        in ``sdl_results`` during detection:

        - SDL2 maps the pad correctly (the common case) -> the logical
          button matches the label -> identity (no remap).
        - SDL2's mapping is genuinely wrong -> the logical button differs
          -> ``map_<label>`` records the real one, correcting it.
        - No SDL2 controller (raw-only pad), or no logical button was
          reported -> fall back to identity; the raw path uses
          ``button_raw_indices`` instead, so ``map_*`` is moot there.

        Only completed (non-trigger) steps get an entry; triggers are
        handled separately via ``should_swap_triggers``.

        Returns a dict like ``{"map_a": "A", "map_lb": "BACK"}``.
        """
        detect_map: dict[str, str] = {}
        for label in WIZARD_STEPS:
            if label in _TRIGGER_STEPS:
                continue  # Triggers handled by should_swap_triggers
            if label not in self.results:
                continue  # step not completed – leave its map_* untouched
            field_name = f"map_{label.lower()}"
            sdl_id = self.sdl_results.get(label)
            detect_map[field_name] = _LOGICAL_ID_TO_NAME.get(sdl_id, label) if sdl_id is not None else label
        return detect_map

    def build_raw_index_map(self) -> dict[str, int]:
        """Return a label -> raw hardware index map for all detected inputs.

        This preserves the actual raw joystick indices so the remap
        can work correctly even when SDL2 IDs don't match the hardware.

        Hat sentinel values (negative) are included for D-Pad directions
        so that ``_get_button`` can route them to ``joystick.get_hat()``.
        Trigger axis sentinels (``<= AXIS_BASE``) are included for LT/RT
        so they can be displayed in the web UI; ``_button_remap`` build
        skips labels it doesn't recognize, and LT/RT reads already route
        through ``_get_trigger_as_button`` ahead of the remap lookup.
        """
        raw_map: dict[str, int] = {}
        for label in WIZARD_STEPS:
            raw_idx = self.results.get(label)
            if raw_idx is None:
                continue
            raw_map[label] = raw_idx
        return raw_map

    def should_swap_triggers(self) -> bool:
        """Return True if the detected LT/RT inputs are in reversed order.

        The runtime Z-axis code on raw joysticks reads LT_AXIS_INDICES
        and RT_AXIS_INDICES – so swap is defined as "what the wizard
        learned about the physical trigger-to-axis wiring doesn't match
        that hardcoded expectation."  Signals we trust, in order:

        1. SDL2 logical-trigger probe (authoritative when the controller
           is an SDL2 GameController at runtime).
        2. Whichever physical axis each trigger actually drives – either
           because the wizard recorded it directly or the raw-axis probe
           captured it.  An axis landing in the *other* trigger's
           expected set is a swap regardless of how the other trigger
           was detected.
        3. Digital-button index comparison as a last resort.
        """
        lt_sdl2 = self._sdl2_probe_results.get("LT")
        rt_sdl2 = self._sdl2_probe_results.get("RT")
        if lt_sdl2 and rt_sdl2:
            swapped = lt_sdl2 == "RIGHT" and rt_sdl2 == "LEFT"
            if swapped:
                logger.info(
                    "Trigger swap detected: physical LT drives SDL2 RIGHT axis",
                )
            return swapped

        def _axis_for(label: str) -> int | None:
            recorded = self.results.get(label)
            if recorded is not None and recorded <= AXIS_BASE:
                return AXIS_BASE - recorded
            return self._raw_axis_probe_results.get(label)

        lt_axis = _axis_for("LT")
        rt_axis = _axis_for("RT")

        if lt_axis is not None and rt_axis is not None and lt_axis != rt_axis:
            swapped = lt_axis > rt_axis
            if swapped:
                logger.info(
                    "Trigger swap detected: LT axis=%d, RT axis=%d (reversed order)",
                    lt_axis,
                    rt_axis,
                )
            return swapped

        # Partial signal: one trigger's axis landed in the other's set.
        if lt_axis is not None and lt_axis in RT_AXIS_INDICES and lt_axis not in LT_AXIS_INDICES:
            logger.info(
                "Trigger swap detected: LT on axis %d, which is in RT_AXIS_INDICES",
                lt_axis,
            )
            return True
        if rt_axis is not None and rt_axis in LT_AXIS_INDICES and rt_axis not in RT_AXIS_INDICES:
            logger.info(
                "Trigger swap detected: RT on axis %d, which is in LT_AXIS_INDICES",
                rt_axis,
            )
            return True

        lt_raw = self.results.get("LT")
        rt_raw = self.results.get("RT")
        if lt_raw is None or rt_raw is None:
            return False
        # Both axis sentinels – only reachable when both LT and RT
        # resolve to the SAME axis (shared-axis controllers); the
        # earlier ``lt_axis != rt_axis`` branch above catches every
        # different-axis case before we get here.
        if lt_raw <= AXIS_BASE and rt_raw <= AXIS_BASE:
            lt_axis = AXIS_BASE - lt_raw
            rt_axis = AXIS_BASE - rt_raw
            swapped = lt_axis > rt_axis
            # pragma: no cover – same-axis case forces lt_axis == rt_axis,
            # so swapped is always False at this point. The log is a
            # safety net for a code path that can no longer fire after
            # the upstream different-axis short-circuit at line 516.
            if swapped:  # pragma: no cover
                logger.info(
                    "Trigger swap detected: LT on axis %d, RT on axis %d",
                    lt_axis,
                    rt_axis,
                )
            return swapped
        # Both digital buttons: HID button indices are arbitrary and do not
        # reflect physical trigger position, so they cannot tell us about a
        # swap. Assume no swap and rely on the SDL2/axis paths above when
        # either trigger produces an axis signal.
        if lt_raw >= 0 and rt_raw >= 0:
            logger.debug(
                "Trigger swap inconclusive: both triggers digital buttons (LT=%d, RT=%d); assuming no swap",
                lt_raw,
                rt_raw,
            )
            return False
        return False
