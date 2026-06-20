# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the in-app button detection wizard.

The wizard walks a user through pressing every labelled input on their
gamepad and records the raw joystick button / hat / axis index that
fires for each one.  These tests drive the state machine through a
fake pygame joystick+controller pair so the suite stays hermetic.

Scenarios covered (one parametrize or function each):

 - `_read_hat_directions` resolves all 4 cardinal hat deflections.
 - `__post_init__` snapshots initial-held buttons and hat directions
   so stale holdovers don't immediately register as step inputs,
   snapshots axis baselines + SDL2 trigger baselines, and seeds the
   controller name.
 - `current_label` / `is_done` / `get_state` reflect the step cursor.
 - A plain button press on a non-trigger step records and advances.
 - A hat direction press on a D-Pad step records the hat sentinel.
 - A button held when the wizard starts is ignored; releasing and
   re-pressing lets it register on a later step.
 - A raw index already assigned to an earlier label cannot bind a
   second label (prevents RB bleed-through).
 - The post-step cooldown (`_STEP_COOLDOWN_S`) blocks polls until the
   clock has advanced.
 - Trigger step with clear axis deflection records the AXIS sentinel
   and flips the wizard into `_waiting_for_release` until axes settle.
 - Trigger step with a digital button press defers commit for
   `_TRIGGER_BUTTON_DEFER_S`; the axis can supersede, or the defer
   deadline commits the button.
 - Trigger step does NOT accept a button while an axis is still
   rising above settle threshold (would otherwise lose LT/RT info).
 - `_waiting_for_release` re-captures baselines after settle.
 - `cancel()` marks the wizard done without applying.
 - `build_detection_map` emits SDL2-logical `map_*` from the per-step
   SDL2-logical capture (identity when SDL2 maps the pad correctly, a
   real correction otherwise); skips trigger + un-detected steps.
 - `build_raw_index_map` returns every detection including negative
   sentinels.
 - `should_swap_triggers` honours (in order): SDL2 probe, physical
   axis match against LT/RT_AXIS_INDICES, both-axis sentinel
   comparison.  Both-digital case returns False (indices are
   arbitrary and cannot signal a swap).
"""

from __future__ import annotations

import pytest

from openfollow.input import button_detection as bd
from openfollow.input.button_detection import (
    _HAT_TO_LABEL,
    AXIS_BASE,
    WIZARD_STEPS,
    ButtonDetectionWizard,
    _read_hat_directions,
)
from openfollow.input.gamepad import (
    BUTTON_NAME_TO_ID,
    CONTROLLER_AXIS_TRIGGERLEFT,
    CONTROLLER_AXIS_TRIGGERRIGHT,
    HAT_DOWN,
    HAT_LEFT,
    HAT_RIGHT,
    HAT_UP,
)

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class FakeJoystick:
    """Minimal pygame.joystick.Joystick replacement.

    Exposes only the surface the wizard touches.  State is mutated
    directly by tests between ``poll()`` calls.
    """

    def __init__(
        self,
        *,
        num_buttons: int = 12,
        num_hats: int = 1,
        num_axes: int = 6,
        name: str = "FakePad",
        guid: str = "fakeguid",
    ) -> None:
        self._buttons = [False] * num_buttons
        self._hats = [(0, 0)] * num_hats
        self._axes = [0.0] * num_axes
        self._name = name
        self._guid = guid

    def get_numbuttons(self) -> int:
        return len(self._buttons)

    def get_button(self, idx: int) -> bool:
        return self._buttons[idx]

    def get_numhats(self) -> int:
        return len(self._hats)

    def get_hat(self, idx: int) -> tuple[int, int]:
        return self._hats[idx]

    def get_numaxes(self) -> int:
        return len(self._axes)

    def get_axis(self, idx: int) -> float:
        return self._axes[idx]

    def get_name(self) -> str:
        return self._name

    def get_guid(self) -> str:
        return self._guid

    # Test helpers
    def press(self, btn: int) -> None:
        self._buttons[btn] = True

    def release(self, btn: int) -> None:
        self._buttons[btn] = False

    def set_hat(self, idx: int, value: tuple[int, int]) -> None:
        self._hats[idx] = value

    def set_axis(self, idx: int, value: float) -> None:
        self._axes[idx] = value


class FakeController:
    """Minimal SDL2 GameController replacement.

    ``get_axis`` returns a raw signed 16-bit int like real SDL2; ``set_trigger``
    takes a [0, 1] fraction and stores the equivalent raw value so the wizard's
    normalization is exercised the way the runtime's is.
    """

    def __init__(self) -> None:
        self._axes: dict[int, int] = {
            CONTROLLER_AXIS_TRIGGERLEFT: 0,
            CONTROLLER_AXIS_TRIGGERRIGHT: 0,
        }
        self._buttons: dict[int, bool] = {}

    def get_axis(self, axis: int) -> int:
        return self._axes[axis]

    def set_trigger(self, axis: int, value: float) -> None:
        self._axes[axis] = int(value * 32767)

    def set_trigger_raw(self, axis: int, raw: int) -> None:
        self._axes[axis] = raw

    def get_button(self, btn: int) -> bool:
        return self._buttons.get(btn, False)

    def press(self, btn: int) -> None:
        self._buttons[btn] = True

    def release(self, btn: int) -> None:
        self._buttons[btn] = False


class FakeGamepadHandler:
    def __init__(
        self,
        joystick: FakeJoystick | None = None,
        controller: FakeController | None | object = "default",
    ) -> None:
        self.joysticks: dict[int, FakeJoystick] = {0: joystick} if joystick is not None else {}
        if controller == "default":
            controller = FakeController()
        self.controllers: dict[int, object | None] = {0: controller} if controller is not None else {}
        self.pump_calls = 0

    def _pump_events(self) -> None:
        self.pump_calls += 1


class FakeInputManager:
    def __init__(self, handler: FakeGamepadHandler) -> None:
        self.gamepad_handler = handler


class FakeApp:
    def __init__(self, handler: FakeGamepadHandler) -> None:
        self._input_manager = FakeInputManager(handler)


class FakeClock:
    def __init__(self, start: float = 100.0) -> None:
        self.now = start

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def __call__(self) -> float:
        return self.now


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(bd.time, "monotonic", c)
    return c


def _make_wizard(
    joystick: FakeJoystick | None = None,
    *,
    controller: FakeController | None | object = "default",
    clock: FakeClock | None = None,
) -> tuple[ButtonDetectionWizard, FakeJoystick, FakeGamepadHandler]:
    """Build a wizard.  Pass ``clock`` to advance past the start-up grace
    and arm the wizard, so subsequent ``poll()`` calls behave as if the
    grace period had elapsed and the initial-held snapshot were empty.
    """
    joystick = joystick or FakeJoystick()
    handler = FakeGamepadHandler(joystick, controller=controller)
    wizard = ButtonDetectionWizard(app=FakeApp(handler))
    if clock is not None:
        clock.advance(bd._WIZARD_START_GRACE_S + 0.001)
        wizard._capture_initial_held(handler)
        wizard._armed = True
    return wizard, joystick, handler


# --------------------------------------------------------------------------- #
# Module-level helpers and constants
# --------------------------------------------------------------------------- #


def test_wizard_steps_cover_every_remappable_button_label() -> None:
    remappable = set(BUTTON_NAME_TO_ID.keys())
    assert remappable.issubset(set(WIZARD_STEPS))
    # Wizard contains exactly the remappable labels defined by BUTTON_NAME_TO_ID.
    assert set(WIZARD_STEPS) == remappable


def test_hat_to_label_covers_all_four_hat_sentinels() -> None:
    assert _HAT_TO_LABEL == {
        HAT_UP: "DPAD_UP",
        HAT_DOWN: "DPAD_DOWN",
        HAT_LEFT: "DPAD_LEFT",
        HAT_RIGHT: "DPAD_RIGHT",
    }


@pytest.mark.parametrize(
    "hat_value,expected",
    [
        ((0, 1), {HAT_UP}),
        ((0, -1), {HAT_DOWN}),
        ((-1, 0), {HAT_LEFT}),
        ((1, 0), {HAT_RIGHT}),
        ((1, 1), {HAT_UP, HAT_RIGHT}),
        ((-1, -1), {HAT_DOWN, HAT_LEFT}),
        ((0, 0), set()),
    ],
)
def test_read_hat_directions_resolves_every_cardinal(hat_value, expected) -> None:
    joystick = FakeJoystick(num_hats=1)
    joystick.set_hat(0, hat_value)

    assert _read_hat_directions(joystick) == expected


def test_read_hat_directions_merges_multiple_hats() -> None:
    joystick = FakeJoystick(num_hats=2)
    joystick.set_hat(0, (1, 0))  # RIGHT
    joystick.set_hat(1, (0, 1))  # UP

    assert _read_hat_directions(joystick) == {HAT_RIGHT, HAT_UP}


# --------------------------------------------------------------------------- #
# __post_init__
# --------------------------------------------------------------------------- #


def test_post_init_ignores_buttons_and_hats_held_at_wizard_start(clock) -> None:
    """Inputs (button or hat) held at the end of the grace don't register.

    ``_make_wizard(..., clock=clock)`` advances past the start-up grace
    and snapshots the still-held button + hat into ``_initial_held``.
    On the first poll, neither records as step ``A``.  Only a fresh,
    never-held button finally advances the first step.  If the hat
    snapshot were skipped, the held HAT_UP would have bled through as
    ``results["A"] = HAT_UP``.
    """
    joy = FakeJoystick()
    joy.press(3)  # button held when wizard begins
    joy.set_hat(0, (0, 1))  # HAT_UP held when wizard begins

    wizard, _, _ = _make_wizard(joy, clock=clock)

    wizard.poll()
    assert wizard.results == {}

    # A different, never-held button records step A.
    joy.press(7)
    wizard.poll()
    assert wizard.results == {"A": 7}


def test_poll_ignores_all_input_during_startup_grace(clock) -> None:
    """For the first ``_WIZARD_START_GRACE_S`` seconds, every press is ignored.

    Reproduces the on-screen-menu UX bug: the user pressed A to confirm
    "Button Detection", and on the very next frame their press would
    otherwise be auto-recorded as step 1 (A).  The grace forces a brief
    "Press A" prompt window before any input is consumed.
    """
    joy = FakeJoystick()
    # Skip the auto-arm – we want to verify the grace gate itself.
    handler = FakeGamepadHandler(joy)
    wizard = ButtonDetectionWizard(app=FakeApp(handler))

    # Press during the grace window – must be ignored.
    joy.press(0)
    wizard.poll()
    assert wizard.results == {}

    # Still inside grace.
    clock.advance(bd._WIZARD_START_GRACE_S * 0.5)
    wizard.poll()
    assert wizard.results == {}

    # Past the grace.  The still-held button lands in ``_initial_held``
    # on this first post-grace poll and is therefore filtered.
    clock.advance(bd._WIZARD_START_GRACE_S + 0.01)
    wizard.poll()
    assert wizard.results == {}

    # Release and re-press: now the prune logic clears it from
    # ``_initial_held`` and the next press registers as step A.
    joy.release(0)
    wizard.poll()
    joy.press(0)
    wizard.poll()
    assert wizard.results == {"A": 0}


def test_post_init_snapshots_axis_baselines_for_deflection_detection(clock) -> None:
    joy = FakeJoystick()
    joy.set_axis(4, 0.5)  # non-zero rest captured as axis 4 baseline
    wizard, _, _ = _make_wizard(joy, clock=clock)

    _advance_to_trigger_step(wizard, joy, clock, "LT")
    clock.advance(bd._STEP_COOLDOWN_S + 0.1)

    # At rest – delta from the 0.5 baseline is 0, no detection.
    wizard.poll()
    assert "LT" not in wizard.results

    # Small wobble (|0.6 - 0.5| = 0.1 < 0.4 threshold) – still no detection.
    joy.set_axis(4, 0.6)
    wizard.poll()
    assert "LT" not in wizard.results

    # Real deflection relative to baseline (|0.95 - 0.5| = 0.45 > 0.4):
    # records LT on axis 4.
    joy.set_axis(4, 0.95)
    wizard.poll()
    assert wizard.results["LT"] == AXIS_BASE - 4


def test_post_init_without_sdl2_controller_leaves_baseline_none(clock) -> None:
    wizard, _, _ = _make_wizard(controller=None)

    assert wizard._sdl2_trigger_baselines is None


def test_identity_captured_from_pad_on_first_record(clock) -> None:
    joy = FakeJoystick(name="Special Pro", guid="pro-guid")
    wizard, _, _ = _make_wizard(joy, clock=clock)
    # Not captured until a pad records (the lock point), not at __post_init__.
    assert wizard.controller_name == ""
    joy.press(0)
    wizard.poll()
    assert wizard.controller_name == "Special Pro"
    assert wizard.controller_guid == "pro-guid"


def test_identity_guid_failure_degrades_to_empty(clock) -> None:
    class FailingGuidJoystick(FakeJoystick):
        def get_guid(self) -> str:
            raise RuntimeError("guid not ready")

    joy = FailingGuidJoystick(name="Has Name")
    wizard, _, _ = _make_wizard(joy, clock=clock)
    joy.press(0)
    wizard.poll()
    # Name still captured; guid failure degrades to empty without raising.
    assert wizard.controller_name == "Has Name"
    assert wizard.controller_guid == ""


def test_identity_comes_from_recording_pad_not_first_enumerated(clock) -> None:
    """The saved map must name the pad that actually recorded, not whichever
    pad enumerated first (the #314-family attribution root)."""
    joy_a = FakeJoystick(name="First Pad", guid="first-guid")
    joy_b = FakeJoystick(name="Second Pad", guid="second-guid")
    handler = FakeGamepadHandler(joy_a)
    handler.joysticks[1] = joy_b
    handler.controllers[1] = FakeController()
    wizard = ButtonDetectionWizard(app=FakeApp(handler))
    clock.advance(bd._WIZARD_START_GRACE_S + 0.001)
    wizard._capture_initial_held(handler)
    wizard._armed = True

    # Press only on the SECOND pad – it wins the lock and names the map.
    joy_b.press(0)
    wizard.poll()

    assert wizard._active_idx == 1
    assert wizard.controller_name == "Second Pad"
    assert wizard.controller_guid == "second-guid"


def test_identity_name_failure_degrades_to_empty(clock) -> None:
    class FailingNameJoystick(FakeJoystick):
        def get_name(self) -> str:
            raise RuntimeError("driver hiccup")

    joy = FailingNameJoystick()
    wizard, _, _ = _make_wizard(joy, clock=clock)
    joy.press(0)
    wizard.poll()
    assert wizard.controller_name == ""


def test_post_init_tolerates_sdl2_axis_read_failure(clock) -> None:
    class FailingController(FakeController):
        def get_axis(self, axis):
            raise RuntimeError("axis not ready")

    wizard, _, _ = _make_wizard(controller=FailingController())

    assert wizard._sdl2_trigger_baselines is None


# --------------------------------------------------------------------------- #
# current_label / is_done / get_state
# --------------------------------------------------------------------------- #


def test_current_label_follows_step_cursor(clock) -> None:
    wizard, _, _ = _make_wizard()

    assert wizard.current_label == WIZARD_STEPS[0]
    wizard._step = len(WIZARD_STEPS)
    assert wizard.current_label == ""


def test_get_state_reflects_progress(clock) -> None:
    wizard, _, _ = _make_wizard()

    wizard.results["A"] = 0

    snapshot = wizard.get_state()
    assert snapshot.active is True
    assert snapshot.current_label == WIZARD_STEPS[0]
    assert snapshot.step == 0
    assert snapshot.total_steps == len(WIZARD_STEPS)
    assert snapshot.completed == {"A": 0}


# --------------------------------------------------------------------------- #
# poll: regular button + hat flow
# --------------------------------------------------------------------------- #


def test_poll_records_plain_button_press_on_non_trigger_step(clock) -> None:
    joy = FakeJoystick()
    wizard, _, handler = _make_wizard(joy, clock=clock)

    joy.press(2)
    wizard.poll()

    assert handler.pump_calls == 1
    assert wizard.results == {"A": 2}
    assert wizard.current_label == "B"


def test_poll_skips_button_that_was_held_at_wizard_start(clock) -> None:
    """Button held when the wizard started doesn't fire on the first poll.

    While still held, ``_initial_held`` filters the press; a different,
    never-held button is free to register at the same step.
    """
    joy = FakeJoystick()
    joy.press(5)
    wizard, _, _ = _make_wizard(joy, clock=clock)

    wizard.poll()
    assert wizard.results == {}

    # A different (never-held) button is still free to register.
    joy.press(7)
    wizard.poll()

    assert wizard.results == {"A": 7}


def test_poll_records_entry_button_after_release_and_repress(clock) -> None:
    """Button held at wizard start is recordable after release-then-press.

    Reproduces the on-screen-menu bug: A confirms "Button Detection" and
    is still held when the wizard initialises, so it lands in
    ``_initial_held``.  Without the per-poll prune of released ids, the
    user's labelled press of A never registers because ``_initial_held``
    is only rebuilt inside ``_record``.
    """
    joy = FakeJoystick()
    joy.press(1)  # "A" held when wizard inits
    wizard, _, _ = _make_wizard(joy, clock=clock)

    # First poll: still held – filtered.
    wizard.poll()
    assert wizard.results == {}

    # User releases the button.
    joy.release(1)
    wizard.poll()
    assert wizard.results == {}

    # User presses the same button as the labelled step input.
    joy.press(1)
    wizard.poll()

    assert wizard.results == {"A": 1}
    assert wizard.current_label == "B"


def test_poll_records_entry_hat_after_release_and_repress(clock) -> None:
    """Hat direction held at wizard start is recordable after release-then-repress.

    Same shape as the button case but for D-Pad sentinels – proves the
    prune logic covers both branches of ``_initial_held``.
    """
    joy = FakeJoystick()
    joy.set_hat(0, (0, 1))  # HAT_UP held when wizard inits
    wizard, _, _ = _make_wizard(joy, clock=clock)

    # Fast-forward to DPAD_UP step.
    for idx, label in enumerate(WIZARD_STEPS):
        if label == "DPAD_UP":
            wizard._step = idx
            break
    clock.advance(1.0)

    # Still held – filtered.
    wizard.poll()
    assert wizard.results == {}

    # Release, then re-engage the same direction.
    joy.set_hat(0, (0, 0))
    wizard.poll()
    joy.set_hat(0, (0, 1))
    wizard.poll()

    assert wizard.results == {"DPAD_UP": HAT_UP}


def test_poll_records_hat_direction_on_dpad_step(clock) -> None:
    joy = FakeJoystick()
    wizard, _, _ = _make_wizard(joy, clock=clock)

    # Fast-forward to DPAD_UP step by scripting earlier detections.
    for idx, label in enumerate(WIZARD_STEPS):
        if label == "DPAD_UP":
            wizard._step = idx
            break

    joy.set_hat(0, (0, 1))  # UP
    clock.advance(1.0)  # past any cooldown
    wizard.poll()

    assert wizard.results["DPAD_UP"] == HAT_UP


def test_poll_does_not_rebind_a_raw_index_already_in_use(clock) -> None:
    joy = FakeJoystick()
    wizard, _, _ = _make_wizard(joy, clock=clock)

    # A -> button 2
    joy.press(2)
    wizard.poll()
    joy.release(2)
    clock.advance(1.0)

    # Next step B: button 2 is pressed again – must NOT rebind.
    joy.press(2)
    wizard.poll()

    assert wizard.results == {"A": 2}
    assert wizard.current_label == "B"


def test_poll_honours_step_cooldown(clock) -> None:
    joy = FakeJoystick()
    wizard, _, _ = _make_wizard(joy, clock=clock)

    joy.press(0)
    wizard.poll()  # records A
    assert wizard.current_label == "B"
    joy.release(0)

    # Immediately press a new button – cooldown should suppress the poll.
    joy.press(1)
    wizard.poll()

    assert "B" not in wizard.results
    clock.advance(bd._STEP_COOLDOWN_S + 0.01)
    wizard.poll()

    assert wizard.results["B"] == 1


def test_poll_is_noop_when_done(clock) -> None:
    wizard, _, handler = _make_wizard()

    wizard.cancel()
    wizard.poll()

    assert handler.pump_calls == 0


# --------------------------------------------------------------------------- #
# poll: trigger step (axis, digital fallback, deferred commit)
# --------------------------------------------------------------------------- #


def _advance_to_trigger_step(
    wizard: ButtonDetectionWizard,
    joy: FakeJoystick,
    clock: FakeClock,
    label: str = "LT",
) -> None:
    """Drive the wizard through earlier steps so ``current_label`` is LT/RT.

    Uses a pool of unique button indices so no index is reused across
    steps (which the 'used_id' guard would otherwise reject).
    """
    lt_index = WIZARD_STEPS.index(label)
    for idx in range(lt_index):
        btn = idx
        joy.press(btn)
        clock.advance(bd._STEP_COOLDOWN_S + 0.1)
        wizard.poll()
        joy.release(btn)


def test_poll_trigger_step_records_axis_sentinel_when_axis_deflects(clock) -> None:
    joy = FakeJoystick()
    wizard, _, _ = _make_wizard(joy, clock=clock)

    _advance_to_trigger_step(wizard, joy, clock, "LT")

    # Deflect axis 4 (LT_AXIS_INDICES includes 4) past the threshold.
    joy.set_axis(4, 0.9)
    clock.advance(1.0)
    wizard.poll()

    assert wizard.results["LT"] == AXIS_BASE - 4
    # LT is recorded; cursor advances to RT.
    assert wizard.current_label == "RT"

    # While the trigger axis is still deflected, the wizard must not
    # accept any further input for the next step.
    clock.advance(bd._STEP_COOLDOWN_S + 0.1)
    wizard.poll()
    assert "RT" not in wizard.results


def test_poll_trigger_step_digital_fallback_commits_after_defer_deadline(clock) -> None:
    joy = FakeJoystick()
    wizard, _, _ = _make_wizard(joy, clock=clock)

    _advance_to_trigger_step(wizard, joy, clock, "LT")

    # All axes at rest, so _axes_settled is True and the button path is taken.
    clock.advance(1.0)
    joy.press(6)
    wizard.poll()

    # Button press alone must not record LT before the defer deadline –
    # an analog axis might still catch up and supersede it.
    assert "LT" not in wizard.results

    # Release and advance past the defer deadline – axis never caught up.
    joy.release(6)
    clock.advance(bd._TRIGGER_BUTTON_DEFER_S + 0.1)
    wizard.poll()

    assert wizard.results["LT"] == 6


def test_poll_trigger_step_axis_supersedes_pending_button(clock) -> None:
    joy = FakeJoystick()
    wizard, _, _ = _make_wizard(joy, clock=clock)

    _advance_to_trigger_step(wizard, joy, clock, "LT")

    clock.advance(1.0)
    joy.press(6)
    wizard.poll()
    # Button alone hasn't been recorded yet (still within the defer window).
    assert "LT" not in wizard.results

    # Axis catches up before the defer deadline.
    joy.set_axis(4, 0.9)
    wizard.poll()

    assert wizard.results["LT"] == AXIS_BASE - 4


def test_poll_trigger_step_defers_button_while_axis_still_rising(clock) -> None:
    joy = FakeJoystick()
    wizard, _, _ = _make_wizard(joy, clock=clock)

    _advance_to_trigger_step(wizard, joy, clock, "LT")

    # Axis partially deflected (below detection threshold 0.4 but above
    # settle threshold 0.2).
    joy.set_axis(4, 0.3)
    clock.advance(1.0)
    joy.press(6)
    wizard.poll()

    # The still-rising trigger must not commit yet: axis is below the
    # 0.4 detection threshold AND the wizard must remain on the same step
    # until the trigger has clearly settled or crossed the detection threshold.
    assert "LT" not in wizard.results
    assert wizard.current_label == "LT"


def test_waiting_for_release_blocks_then_resumes_once_settled(clock) -> None:
    joy = FakeJoystick()
    wizard, _, _ = _make_wizard(joy, clock=clock)

    _advance_to_trigger_step(wizard, joy, clock, "LT")

    joy.set_axis(4, 0.9)
    clock.advance(1.0)
    wizard.poll()
    assert wizard.results["LT"] == AXIS_BASE - 4
    assert wizard.current_label == "RT"

    # Even after the post-step cooldown, keeping LT deflected must not let
    # the next step record any input yet.
    clock.advance(bd._STEP_COOLDOWN_S + 0.1)
    wizard.poll()
    assert "RT" not in wizard.results
    assert wizard.current_label == "RT"

    # Release back near baseline; after settle + cooldown, the wizard should
    # still be on RT and ready to accept RT normally.
    joy.set_axis(4, 0.05)
    clock.advance(bd._STEP_COOLDOWN_S + 0.1)
    wizard.poll()
    assert "RT" not in wizard.results
    assert wizard.current_label == "RT"

    # With LT settled, RT's axis must now record cleanly – proving the
    # wizard resumed normal detection without stale state blocking the next
    # trigger step.
    joy.set_axis(5, 0.9)
    wizard.poll()
    assert wizard.results["RT"] == AXIS_BASE - 5


def test_multi_pad_prune_released_safe_across_mismatched_button_counts(clock) -> None:
    """``_initial_held`` must be per-joystick so a button index valid on
    the larger pad never reaches the smaller pad's ``get_button`` (raises
    ``pygame.error: Invalid joystick button``).

    Stress the wizard by holding a button at start that only exists on
    Pad B (15-button GameSir layout); Pad A (11-button Generic) must
    still poll without crashing.
    """

    class StrictJoystick(FakeJoystick):
        """Raises like real pygame for out-of-range button reads."""

        def get_button(self, idx: int) -> bool:
            if idx < 0 or idx >= len(self._buttons):
                raise IndexError("Invalid joystick button")
            return self._buttons[idx]

    pad_a = StrictJoystick(num_buttons=11, name="Pad A")
    pad_b = StrictJoystick(num_buttons=15, name="Pad B")
    pad_b.press(13)  # Held at wizard start; index out of range for Pad A.
    handler = FakeGamepadHandler(pad_a)
    handler.joysticks[1] = pad_b
    handler.controllers[1] = FakeController()
    wizard = ButtonDetectionWizard(app=FakeApp(handler))

    clock.advance(bd._WIZARD_START_GRACE_S + 0.001)
    # Must not raise: per-joystick ``_initial_held`` keeps pad_b's
    # button 13 out of pad_a's prune iteration.
    wizard.poll()

    # First labelled step (A) accepts a fresh press on pad_a – proving
    # pad_b's held button 13 didn't poison pad_a's filter or break the
    # iteration.
    pad_a.press(0)
    clock.advance(bd._STEP_COOLDOWN_S + 0.1)
    wizard.poll()
    assert wizard.results.get("A") == 0


def test_multi_pad_trigger_step_does_not_hang_on_other_pads_rest_state(clock) -> None:
    """Two pads with different trigger-axis layouts must not deadlock the
    wizard's ``_waiting_for_release`` gate.

    Pad A rests with axis 2 = -1 and axis 5 = -1 (Generic X-Box layout
    on Linux); Pad B rests with axis 4 = -1 and axis 5 = -1 (GameSir).
    Pre-fix, ``_axis_baselines`` was a single shared dict that got
    overwritten per joystick, so after Pad A recorded LT and refreshed
    the baseline from Pad A's rest, Pad B's still-resting axes 4/5 sat
    1.0 away from the shared baseline – ``_axes_settled(padB)``
    returned False forever and the wizard could never leave the
    ``_waiting_for_release`` state to advance to RT.
    """
    pad_a = FakeJoystick(name="Pad A")
    pad_a.set_axis(2, -1.0)
    pad_a.set_axis(5, -1.0)
    pad_b = FakeJoystick(name="Pad B")
    pad_b.set_axis(4, -1.0)
    pad_b.set_axis(5, -1.0)
    handler = FakeGamepadHandler(pad_a)
    handler.joysticks[1] = pad_b
    handler.controllers[1] = FakeController()
    wizard = ButtonDetectionWizard(app=FakeApp(handler))
    clock.advance(bd._WIZARD_START_GRACE_S + 0.001)
    wizard._capture_initial_held(handler)
    wizard._armed = True

    _advance_to_trigger_step(wizard, pad_a, clock, "LT")

    # Pad A's LT deflects raw axis 2 from its -1 rest. Pad B sits idle
    # at its own (different) rest values throughout.
    pad_a.set_axis(2, 1.0)
    clock.advance(1.0)
    wizard.poll()
    assert wizard.results["LT"] == AXIS_BASE - 2
    assert wizard.current_label == "RT"

    # Release LT back to rest; the wizard must clear
    # ``_waiting_for_release`` and not be blocked by Pad B's idle axes.
    pad_a.set_axis(2, -1.0)
    clock.advance(bd._STEP_COOLDOWN_S + 0.1)
    wizard.poll()
    assert wizard.current_label == "RT"

    # Press RT on Pad A – should record cleanly without the multi-pad
    # deadlock.
    pad_a.set_axis(5, 1.0)
    wizard.poll()
    assert wizard.results["RT"] == AXIS_BASE - 5


# --------------------------------------------------------------------------- #
# cancel / completion
# --------------------------------------------------------------------------- #


def test_cancel_marks_wizard_done(clock) -> None:
    wizard, _, _ = _make_wizard()

    wizard.cancel()

    assert wizard.is_done is True


def test_final_step_sets_done_flag(clock) -> None:
    joy = FakeJoystick()
    wizard, _, _ = _make_wizard(joy, clock=clock)

    hat_for = {
        "DPAD_UP": (0, 1),
        "DPAD_DOWN": (0, -1),
        "DPAD_LEFT": (-1, 0),
        "DPAD_RIGHT": (1, 0),
    }

    for step_idx, label in enumerate(WIZARD_STEPS):
        clock.advance(bd._STEP_COOLDOWN_S + 1.0)
        if label == "LT":
            joy.set_axis(4, 0.9)
            wizard.poll()  # records LT via axis; sets _waiting_for_release
            joy.set_axis(4, 0.0)
            clock.advance(bd._STEP_COOLDOWN_S + 1.0)
            wizard.poll()  # settle poll clears _waiting_for_release
        elif label == "RT":
            joy.set_axis(5, 0.9)
            wizard.poll()  # records RT via axis; sets _waiting_for_release
            joy.set_axis(5, 0.0)
            clock.advance(bd._STEP_COOLDOWN_S + 1.0)
            wizard.poll()  # settle poll clears _waiting_for_release
        elif label in hat_for:
            joy.set_hat(0, hat_for[label])
            wizard.poll()
            joy.set_hat(0, (0, 0))
        else:
            btn = step_idx  # always < num_buttons (12)
            joy.press(btn)
            wizard.poll()
            joy.release(btn)

    assert wizard.is_done is True
    assert wizard.current_label == ""


# --------------------------------------------------------------------------- #
# build_detection_map / build_raw_index_map
# --------------------------------------------------------------------------- #


def test_build_detection_map_records_rebound_labels() -> None:
    # SDL2 reports physical-A pressing logical B (a genuinely wrong SDL2
    # mapping) -> map_a must correct it to "B". The raw index in results
    # only gates "step completed" now; the logical id drives the name.
    wizard = ButtonDetectionWizard.__new__(ButtonDetectionWizard)
    wizard.results = {"A": 0, "X": 0}
    wizard.sdl_results = {
        "A": BUTTON_NAME_TO_ID["B"],  # SDL2 logical B fired for the A step
        "X": BUTTON_NAME_TO_ID["X"],  # matches default
    }

    mapping = wizard.build_detection_map()

    assert mapping["map_a"] == "B"
    assert mapping["map_x"] == "X"


def test_build_detection_map_sdl_recognized_pad_is_identity() -> None:
    # The common case: SDL2 maps the pad correctly, so every captured
    # logical button matches its label -> identity map_* (no remap).
    wizard = ButtonDetectionWizard.__new__(ButtonDetectionWizard)
    labels = ("A", "B", "X", "Y", "LB", "RB", "BACK", "START")
    wizard.results = dict.fromkeys(labels, 0)
    wizard.sdl_results = {lbl: BUTTON_NAME_TO_ID[lbl] for lbl in labels}

    mapping = wizard.build_detection_map()

    assert mapping["map_lb"] == "LB"
    assert mapping["map_rb"] == "RB"
    assert all(mapping[f"map_{lbl.lower()}"] == lbl for lbl in labels)


def test_build_detection_map_resolves_dpad_from_sdl_logical() -> None:
    wizard = ButtonDetectionWizard.__new__(ButtonDetectionWizard)
    wizard.results = {"DPAD_UP": HAT_UP, "DPAD_RIGHT": HAT_RIGHT}
    wizard.sdl_results = {
        "DPAD_UP": BUTTON_NAME_TO_ID["DPAD_UP"],
        "DPAD_RIGHT": BUTTON_NAME_TO_ID["DPAD_RIGHT"],
    }

    mapping = wizard.build_detection_map()

    assert mapping["map_dpad_up"] == "DPAD_UP"
    assert mapping["map_dpad_right"] == "DPAD_RIGHT"


def test_build_detection_map_identity_without_sdl_capture() -> None:
    # Raw-only pad (no SDL2 controller, so nothing captured): completed
    # steps fall back to identity; map_* isn't consulted on the raw path.
    wizard = ButtonDetectionWizard.__new__(ButtonDetectionWizard)
    wizard.results = {"A": 4, "LB": 6}  # raw indices, no sdl_results
    wizard.sdl_results = {}

    mapping = wizard.build_detection_map()

    assert mapping == {"map_a": "A", "map_lb": "LB"}


def test_build_detection_map_skips_triggers_and_undetected() -> None:
    wizard = ButtonDetectionWizard.__new__(ButtonDetectionWizard)
    wizard.results = {
        "A": BUTTON_NAME_TO_ID["A"],
        "LT": AXIS_BASE - 4,  # trigger must be filtered out
        # "RT" intentionally missing – must be filtered out
    }
    wizard.sdl_results = {"A": BUTTON_NAME_TO_ID["A"]}

    mapping = wizard.build_detection_map()

    assert "map_lt" not in mapping
    assert "map_rt" not in mapping
    assert mapping == {"map_a": "A"}


def test_build_detection_map_falls_back_to_label_for_unknown_logical_id() -> None:
    wizard = ButtonDetectionWizard.__new__(ButtonDetectionWizard)
    wizard.results = {"A": 0}
    wizard.sdl_results = {"A": 999}  # not a known SDL2 logical id

    mapping = wizard.build_detection_map()

    assert mapping["map_a"] == "A"


def test_poll_captures_sdl_logical_button_at_record(clock) -> None:
    """With an SDL2 controller present, recording the "A" step captures
    both the raw index (``results``) and the SDL2 logical button
    (``sdl_results``) – so a wrong SDL2 mapping is correctly recorded for
    ``map_*`` instead of being inferred (wrongly) from the raw index."""
    joy = FakeJoystick()
    ctrl = FakeController()
    wizard, joy, handler = _make_wizard(joy, controller=ctrl, clock=clock)

    # Physical A press: raw button 2 on this pad, which SDL2 happens to
    # map to logical BACK (a genuinely wrong mapping we want corrected).
    joy.press(2)
    ctrl.press(BUTTON_NAME_TO_ID["BACK"])
    wizard.poll()

    assert wizard.results["A"] == 2
    assert wizard.sdl_results["A"] == BUTTON_NAME_TO_ID["BACK"]
    assert wizard.build_detection_map()["map_a"] == "BACK"


def test_poll_no_sdl_capture_without_controller(clock) -> None:
    """Raw-only pad (no SDL2 controller): nothing is captured into
    ``sdl_results`` and ``map_*`` falls back to identity."""
    joy = FakeJoystick()
    wizard, joy, handler = _make_wizard(joy, controller=None, clock=clock)

    joy.press(2)
    wizard.poll()

    assert wizard.results["A"] == 2
    assert "A" not in wizard.sdl_results
    assert wizard.build_detection_map()["map_a"] == "A"


def test_capture_sdl_logical_tolerates_controller_without_get_button(clock) -> None:

    class NoButtonController(FakeController):
        def get_button(self, btn: int) -> bool:
            raise RuntimeError("no button surface")

    joy = FakeJoystick()
    wizard, joy, handler = _make_wizard(joy, controller=NoButtonController(), clock=clock)

    joy.press(2)
    wizard.poll()

    assert wizard.results["A"] == 2
    assert "A" not in wizard.sdl_results
    assert wizard.build_detection_map()["map_a"] == "A"


def test_build_raw_index_map_preserves_every_detection() -> None:
    wizard = ButtonDetectionWizard.__new__(ButtonDetectionWizard)
    wizard.results = {
        "A": 2,
        "LT": AXIS_BASE - 4,
        "DPAD_DOWN": HAT_DOWN,
        "X": 3,
    }

    raw = wizard.build_raw_index_map()

    assert raw == {"A": 2, "LT": AXIS_BASE - 4, "DPAD_DOWN": HAT_DOWN, "X": 3}


# --------------------------------------------------------------------------- #
# should_swap_triggers
# --------------------------------------------------------------------------- #


def _bare_wizard() -> ButtonDetectionWizard:
    wizard = ButtonDetectionWizard.__new__(ButtonDetectionWizard)
    wizard.results = {}
    wizard._sdl2_probe_results = {}
    wizard._raw_axis_probe_results = {}
    return wizard


def test_should_swap_triggers_sdl2_swap_detected() -> None:
    wizard = _bare_wizard()
    wizard._sdl2_probe_results = {"LT": "RIGHT", "RT": "LEFT"}

    assert wizard.should_swap_triggers() is True


def test_should_swap_triggers_sdl2_normal_wiring() -> None:
    wizard = _bare_wizard()
    wizard._sdl2_probe_results = {"LT": "LEFT", "RT": "RIGHT"}

    assert wizard.should_swap_triggers() is False


def test_should_swap_triggers_axis_comparison_reversed_order() -> None:
    wizard = _bare_wizard()
    # LT detected on axis 5, RT on axis 4 → reversed.
    wizard.results = {"LT": AXIS_BASE - 5, "RT": AXIS_BASE - 4}

    assert wizard.should_swap_triggers() is True


def test_should_swap_triggers_axis_comparison_normal_order() -> None:
    wizard = _bare_wizard()
    wizard.results = {"LT": AXIS_BASE - 4, "RT": AXIS_BASE - 5}

    assert wizard.should_swap_triggers() is False


def test_should_swap_triggers_partial_signal_lt_on_rt_axis() -> None:
    """RT was only detected digitally, but LT axis landed on the RT axis set."""
    wizard = _bare_wizard()
    # LT raw-axis probe landed on axis 5, which lives in RT_AXIS_INDICES (5, 7).
    wizard._raw_axis_probe_results = {"LT": 5}
    wizard.results = {"RT": 0}  # digital button index – cannot be an axis

    assert wizard.should_swap_triggers() is True


def test_should_swap_triggers_partial_signal_rt_on_lt_axis() -> None:
    wizard = _bare_wizard()
    wizard._raw_axis_probe_results = {"RT": 4}  # lives in LT_AXIS_INDICES
    wizard.results = {"LT": 0}

    assert wizard.should_swap_triggers() is True


def test_should_swap_triggers_both_digital_returns_false() -> None:
    """HID button indices are arbitrary – they cannot signal swap."""
    wizard = _bare_wizard()
    wizard.results = {"LT": 7, "RT": 6}  # 7 > 6 but both digital

    assert wizard.should_swap_triggers() is False


def test_should_swap_triggers_missing_detections_returns_false() -> None:
    wizard = _bare_wizard()

    assert wizard.should_swap_triggers() is False


def test_should_swap_triggers_only_lt_detected_returns_false() -> None:
    wizard = _bare_wizard()
    wizard.results = {"LT": AXIS_BASE - 4}

    assert wizard.should_swap_triggers() is False


def test_should_swap_triggers_both_axis_sentinels_reversed() -> None:
    """Axis-sentinel comparison returns True when LT resolves higher than RT."""
    wizard = _bare_wizard()
    # Both triggers are stored as axis sentinels, so `_axis_for` resolves
    # them to concrete axis indices.  LT maps to axis 5 and RT maps to
    # axis 4, so the normal axis-comparison branch treats them as swapped.
    wizard.results = {"LT": AXIS_BASE - 5, "RT": AXIS_BASE - 4}

    assert wizard.should_swap_triggers() is True


def test_should_swap_triggers_both_on_same_unmapped_axis_returns_false() -> None:
    wizard = _bare_wizard()
    # Axis 2 isn't in LT_AXIS_INDICES (4, 6) or RT_AXIS_INDICES (5, 7),
    # so the partial-signal branches at 526/532 don't short-circuit.
    wizard.results = {"LT": AXIS_BASE - 2, "RT": AXIS_BASE - 2}

    assert wizard.should_swap_triggers() is False


def test_should_swap_triggers_mixed_axis_and_digital_returns_false() -> None:
    """LT is an axis sentinel, RT is digital – fall-through returns False."""
    wizard = _bare_wizard()
    wizard.results = {"LT": AXIS_BASE - 4, "RT": 3}

    # `lt_axis` resolves (via `_axis_for`) to 4, `rt_axis` is None.
    # Neither the full axis-comparison nor the partial-signal check
    # fires (axis 4 is in LT_AXIS_INDICES, not RT's).  Both-digital
    # guard doesn't apply.  Final fall-through returns False.
    assert wizard.should_swap_triggers() is False


# --------------------------------------------------------------------------- #
# SDL2 / raw-axis probe side effects on _record
# --------------------------------------------------------------------------- #


def test_record_feeds_should_swap_triggers_with_normal_wiring(clock) -> None:
    joy = FakeJoystick()
    ctrl = FakeController()
    wizard, _, _ = _make_wizard(joy, controller=ctrl, clock=clock)

    # ---- LT: SDL2 LEFT + raw axis 4 deflect together --------------------
    _advance_to_trigger_step(wizard, joy, clock, "LT")

    ctrl.set_trigger(CONTROLLER_AXIS_TRIGGERLEFT, 0.9)
    joy.set_axis(4, 0.9)
    clock.advance(1.0)
    wizard.poll()

    # Release so _waiting_for_release clears before the next step.
    ctrl.set_trigger(CONTROLLER_AXIS_TRIGGERLEFT, 0.0)
    joy.set_axis(4, 0.0)
    clock.advance(bd._STEP_COOLDOWN_S + 0.1)
    wizard.poll()
    assert wizard.current_label == "RT"

    # ---- RT: SDL2 RIGHT + raw axis 5 deflect together -------------------
    ctrl.set_trigger(CONTROLLER_AXIS_TRIGGERRIGHT, 0.9)
    joy.set_axis(5, 0.9)
    clock.advance(1.0)
    wizard.poll()

    # Observable outcome: LT landed on axis 4 / SDL2 LEFT, RT on
    # axis 5 / SDL2 RIGHT – that's normal wiring, so no swap.
    assert wizard.results["LT"] == AXIS_BASE - 4
    assert wizard.results["RT"] == AXIS_BASE - 5
    assert wizard.should_swap_triggers() is False


def test_record_skips_sdl2_probe_when_both_sides_below_threshold(clock) -> None:
    joy = FakeJoystick()
    wizard, _, _ = _make_wizard(joy, clock=clock)

    _advance_to_trigger_step(wizard, joy, clock, "LT")

    # Record LT via the raw joystick axis while SDL2 trigger deltas stay
    # below the probe threshold.
    joy.set_axis(4, 0.9)
    clock.advance(1.0)
    wizard.poll()

    # Record RT the same way, then assert the observable outcome: with
    # SDL2 inconclusive, swap detection falls back to the raw-axis result.
    joy.set_axis(4, 0.0)
    joy.set_axis(5, 0.9)
    clock.advance(1.0)
    wizard.poll()

    assert wizard.should_swap_triggers() is False


def test_sdl2_trigger_raw_noise_stays_below_threshold(clock) -> None:
    """A few raw 16-bit units of SDL2 trigger jitter scale below
    _TRIGGER_THRESHOLD, so noise on the undriven trigger can't fabricate an
    SDL2 probe result (and thus a spurious swap)."""
    joy = FakeJoystick()
    ctrl = FakeController()
    wizard, _, _ = _make_wizard(joy, controller=ctrl, clock=clock)

    _advance_to_trigger_step(wizard, joy, clock, "LT")

    # LT driven by the raw joy axis; SDL2 left trigger only jitters by a
    # handful of raw units (sensor noise), which must not satisfy the probe.
    ctrl.set_trigger_raw(CONTROLLER_AXIS_TRIGGERLEFT, 60)
    joy.set_axis(4, 0.9)
    clock.advance(1.0)
    wizard.poll()

    assert wizard.results["LT"] == AXIS_BASE - 4
    assert "LT" not in wizard._sdl2_probe_results


def test_record_skips_raw_axis_probe_when_all_below_relaxed_threshold(clock) -> None:
    """With no meaningful axis deflection, digital-button fallback records
    both triggers and swap detection reports no swap – proving the raw-axis
    probe contributes nothing when deflection never crossed the threshold.
    """
    joy = FakeJoystick()
    wizard, _, _ = _make_wizard(joy, clock=clock)

    _advance_to_trigger_step(wizard, joy, clock, "LT")

    # LT via digital button; axis stays at rest throughout.
    clock.advance(1.0)
    joy.press(6)
    wizard.poll()  # defer button commit
    clock.advance(bd._TRIGGER_BUTTON_DEFER_S + 0.1)
    wizard.poll()  # deadline expires, button commits
    joy.release(6)

    assert wizard.results["LT"] == 6
    assert wizard.current_label == "RT"

    # RT via digital button; axis still at rest.
    clock.advance(bd._STEP_COOLDOWN_S + 0.1)
    joy.press(7)
    wizard.poll()  # defer
    clock.advance(bd._TRIGGER_BUTTON_DEFER_S + 0.1)
    wizard.poll()  # commit

    assert wizard.results["RT"] == 7
    # Both recorded via digital fallback with no axis info – swap detection
    # has nothing to act on and reports no swap.
    assert wizard.should_swap_triggers() is False


# --------------------------------------------------------------------------- #
# _probe_sdl2_triggers / _probe_raw_axes edge cases
# --------------------------------------------------------------------------- #


def test_trigger_poll_noop_when_no_baseline(clock) -> None:
    wizard, joy, _ = _make_wizard(controller=None)

    _advance_to_trigger_step(wizard, joy, clock, "LT")

    clock.advance(1.0)
    wizard.poll()

    assert "LT" not in wizard.results
    assert wizard.should_swap_triggers() is False


def test_probe_sdl2_triggers_returns_early_when_no_baseline() -> None:
    """Direct-call coverage for the ``baselines is None`` early-exit in
    ``_probe_sdl2_triggers``. ``poll()`` doesn't reach this function
    before the trigger step activates, so the test exercises it
    directly to pin the early-return branch."""
    wizard, _, _ = _make_wizard(controller=None)
    wizard._sdl2_trigger_baselines = None
    # Must not raise, must not touch ``self.app._input_manager`` (the
    # assert on the line below would fire otherwise). Returns ``None``.
    assert wizard._probe_sdl2_triggers(0) is None


def test_trigger_poll_tolerates_none_controller_on_active_pad(clock) -> None:
    """The locked pad has no SDL2 controller entry: the probe must tolerate it
    (no crash, no foreign-pad read) and detection falls back to raw axes."""
    joy = FakeJoystick()
    handler = FakeGamepadHandler(joy, controller=None)
    handler.controllers = {0: None}
    wizard = ButtonDetectionWizard(app=FakeApp(handler))
    clock.advance(bd._WIZARD_START_GRACE_S + 0.001)
    wizard._capture_initial_held(handler)
    wizard._armed = True

    _advance_to_trigger_step(wizard, joy, clock, "LT")
    # Force a non-None baseline *after* the lock (which would otherwise reset it
    # to None for a None controller) so the probe reaches the ctrl-None guard.
    wizard._sdl2_trigger_baselines = (0.0, 0.0)
    joy.set_axis(4, 0.9)  # LT on raw axis 4, no SDL2 wrapper
    clock.advance(1.0)
    wizard.poll()

    assert wizard.results["LT"] == AXIS_BASE - 4


def test_capture_sdl_logical_skips_when_controller_lacks_get_button(clock) -> None:
    """A controller wrapper with no ``get_button`` at all (vs one that raises)
    is skipped cleanly – identity fallback, no probe."""

    class NoGetButton:
        def get_axis(self, axis):  # noqa: ANN001
            return 0.0

    joy = FakeJoystick()
    wizard, joy, _ = _make_wizard(joy, controller=NoGetButton(), clock=clock)

    joy.press(2)
    wizard.poll()

    assert wizard.results["A"] == 2
    assert "A" not in wizard.sdl_results


def test_pad_lock_ignores_input_from_other_pads(clock) -> None:
    """Once a pad records its first press, presses on a *different* pad are
    ignored for the rest of the run (no interleaving into one mapping)."""
    joy_a = FakeJoystick(name="Pad A")
    joy_b = FakeJoystick(name="Pad B")
    handler = FakeGamepadHandler(joy_a)
    handler.joysticks[1] = joy_b
    handler.controllers[1] = FakeController()
    wizard = ButtonDetectionWizard(app=FakeApp(handler))
    clock.advance(bd._WIZARD_START_GRACE_S + 0.001)
    wizard._capture_initial_held(handler)
    wizard._armed = True

    # Pad A records "A" and locks.
    joy_a.press(0)
    wizard.poll()
    assert wizard._active_idx == 0
    assert wizard.results == {"A": 0}

    # Pad B presses a fresh button at the next step – it must be ignored.
    joy_a.release(0)
    clock.advance(bd._STEP_COOLDOWN_S + 0.1)
    joy_b.press(5)
    wizard.poll()
    assert wizard.current_label == "B"  # not advanced by the foreign pad
    assert "B" not in wizard.results


def test_trigger_swap_detected_via_sdl2_on_active_pad(clock) -> None:
    """A reversed trigger wiring is detected via the SDL2 probe read from the
    *locked* pad's own controller (not 'the first non-None one')."""
    joy = FakeJoystick()
    ctrl = FakeController()
    wizard, _, _ = _make_wizard(joy, controller=ctrl, clock=clock)

    _advance_to_trigger_step(wizard, joy, clock, "LT")  # locks to pad 0

    # Physical LT: joy axis 4 deflects AND SDL2 reports RIGHT (reversed).
    ctrl.set_trigger(CONTROLLER_AXIS_TRIGGERRIGHT, 0.8)
    joy.set_axis(4, 0.9)
    clock.advance(1.0)
    wizard.poll()
    assert wizard.results["LT"] == AXIS_BASE - 4

    # Settle, then RT: joy axis 5 AND SDL2 reports LEFT.
    ctrl.set_trigger(CONTROLLER_AXIS_TRIGGERRIGHT, 0.0)
    joy.set_axis(4, 0.0)
    clock.advance(bd._STEP_COOLDOWN_S + 0.1)
    wizard.poll()
    ctrl.set_trigger(CONTROLLER_AXIS_TRIGGERLEFT, 0.8)
    joy.set_axis(5, 0.9)
    clock.advance(1.0)
    wizard.poll()

    assert wizard.results["RT"] == AXIS_BASE - 5
    assert wizard.should_swap_triggers() is True


def test_trigger_poll_tolerates_axis_read_failure(clock) -> None:
    class FailingController(FakeController):
        def get_axis(self, axis):
            raise RuntimeError("axis unavailable at runtime")

    joy = FakeJoystick()
    wizard, _, _ = _make_wizard(joy, controller=FailingController(), clock=clock)

    _advance_to_trigger_step(wizard, joy, clock, "LT")
    # Lock + __post_init__ both reset baselines to None (get_axis failed);
    # force a non-None baseline *after* the lock to exercise the probe's
    # in-loop read-failure except path.
    wizard._sdl2_trigger_baselines = (0.0, 0.0)

    clock.advance(1.0)
    joy.press(6)
    wizard.poll()  # defer button commit
    clock.advance(bd._TRIGGER_BUTTON_DEFER_S + 0.1)
    wizard.poll()  # deadline expires, button commits

    assert wizard.results["LT"] == 6
    assert wizard.should_swap_triggers() is False
