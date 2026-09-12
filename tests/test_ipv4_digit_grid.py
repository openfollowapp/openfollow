"""Digit-grid model behind gamepad entry of an IPv4 value.

The behaviour these pin is "an operator with no keyboard can set a static
address", so they are written against reachability rather than the
representation: how many presses a digit is away, that a half-typed value
survives reaching for the d-pad, and that what the grid produces is something
the address parsers accept.
"""

from __future__ import annotations

import pytest

from openfollow.runtime import ipv4_digit_grid as grid

pytestmark = pytest.mark.unit


class TestReadingAValueIntoTheGrid:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("192.168.1.5", "192168001005"),
            ("255.255.255.0", "255255255000"),
            ("10.0.0.5", "010000000005"),
            ("0.0.0.0", "000000000000"),
        ],
    )
    def test_an_address_keeps_its_octets(self, value: str, expected: str) -> None:
        assert grid.to_grid(value) == expected

    @pytest.mark.parametrize("value", ["", "192.168.", "not-an-address", "/24", "192.168.1.5.6.7"])
    def test_an_unusable_value_degrades_to_zeros_rather_than_raising(self, value: str) -> None:
        """The editor is pre-filled from live config that may be blank, and the
        operator can reach for the d-pad mid-type."""
        digits = grid.to_grid(value)
        assert len(digits) == grid.DIGIT_SLOTS
        assert digits.isdigit()


class TestAnyDigitIsAtMostFivePresses:
    """The reason this model exists at all.

    Settings-overlay input is edge-triggered, so a per-octet increment would put
    255 up to 255 presses away and need held-state key repeat to be usable.
    """

    @pytest.mark.parametrize("start", list(range(10)))
    @pytest.mark.parametrize("target", list(range(10)))
    def test_every_digit_is_reachable_within_five_presses(self, start: int, target: int) -> None:
        best: int | None = None
        for direction in (1, -1):
            current = str(start) + "0" * (grid.DIGIT_SLOTS - 1)
            for presses in range(11):
                if current[0] == str(target):
                    best = presses if best is None else min(best, presses)
                    break
                current = grid.bump_digit(current, 0, direction)
        assert best is not None, f"{target} unreachable from {start}"
        assert best <= 5, f"{target} is {best} presses from {start}; the grid exists to keep this under six"

    def test_the_digit_wraps_rather_than_sticking_at_the_ends(self) -> None:
        assert grid.bump_digit("900000000000", 0, 1)[0] == "0"
        assert grid.bump_digit("000000000000", 0, -1)[0] == "9"

    def test_only_the_cursor_digit_moves(self) -> None:
        assert grid.bump_digit("192168001005", 4, 1) == "192178001005"

    @pytest.mark.parametrize("index", [-1, grid.DIGIT_SLOTS, 99])
    def test_an_out_of_range_cursor_changes_nothing(self, index: int) -> None:
        assert grid.bump_digit("192168001005", index, 1) == "192168001005"


class TestTheCursorStopsAtTheEnds:
    def test_it_walks_the_slots(self) -> None:
        assert grid.move_cursor(0, 1) == 1
        assert grid.move_cursor(5, -1) == 4

    def test_it_clamps_rather_than_wrapping(self) -> None:
        """Running off the last digit onto the first reads as the value having
        changed when it has not."""
        assert grid.move_cursor(0, -1) == 0
        assert grid.move_cursor(grid.DIGIT_SLOTS - 1, 1) == grid.DIGIT_SLOTS - 1


class TestWhatTheGridHandsBack:
    def test_it_pads_so_the_cursor_and_the_character_stay_aligned(self) -> None:
        assert grid.from_grid("192168001005") == "192.168.001.005"

    def test_committing_drops_the_padding_the_parsers_reject(self) -> None:
        """``ipaddress`` rejects leading zeros outright, so a grid-edited value
        would fail validation with its padding still on."""
        assert grid.strip_padding("192.168.001.005") == "192.168.1.5"
        assert grid.strip_padding("000.000.000.000") == "0.0.0.0"

    @pytest.mark.parametrize("value", ["/24", "24", "", "255.255.255", "not.an.address.here"])
    def test_a_value_that_is_not_a_dotted_quad_passes_through_untouched(self, value: str) -> None:
        """The subnet field also accepts a bare prefix length."""
        assert grid.strip_padding(value) == value

    def test_a_grid_edit_round_trips_into_something_a_parser_accepts(self) -> None:
        from openfollow.network.validate import parse_ipv4

        edited = grid.from_grid(grid.bump_digit(grid.to_grid("10.0.0.5"), 0, 1))
        assert parse_ipv4(grid.strip_padding(edited)) == "110.0.0.5"


class TestWhereTheCursorSitsOnScreen:
    """The renderer draws the cursor by character offset, so the mapping from
    digit slot to character is what puts the marker under the digit the
    operator is actually about to change."""

    @pytest.mark.parametrize(
        ("slot", "char"),
        [(0, 0), (2, 2), (3, 4), (5, 6), (6, 8), (8, 10), (9, 12), (11, 14)],
    )
    def test_the_offset_steps_over_each_dot(self, slot: int, char: int) -> None:
        assert grid.caret_offset(slot) == char

    def test_every_slot_lands_on_a_digit_not_a_dot(self) -> None:
        """One drift per octet crossed is the failure this prevents, and it
        only shows on the later octets - the ones an operator edits most."""
        rendered = grid.from_grid("192168001005")
        for slot in range(grid.DIGIT_SLOTS):
            assert rendered[grid.caret_offset(slot)].isdigit()

    def test_the_offset_names_the_digit_the_bump_will_change(self) -> None:
        rendered = grid.from_grid("192168001005")
        for slot in (0, 4, 7, 11):
            bumped = grid.from_grid(grid.bump_digit(grid.to_grid(rendered), slot, 1))
            changed = [i for i, (a, b) in enumerate(zip(rendered, bumped, strict=True)) if a != b]
            assert changed == [grid.caret_offset(slot)]

    @pytest.mark.parametrize("slot", [-5, -1, grid.DIGIT_SLOTS, 99])
    def test_an_out_of_range_slot_clamps_into_the_value(self, slot: int) -> None:
        rendered = grid.from_grid("192168001005")
        assert 0 <= grid.caret_offset(slot) < len(rendered)

    @pytest.mark.parametrize("value", ["192.168.001.005", "000.000.000.000"])
    def test_a_padded_quad_is_grid_form(self, value: str) -> None:
        assert grid.is_grid_form(value) is True

    @pytest.mark.parametrize("value", ["192.168.1.5", "24", "", "255.255.255", "19a.168.001.005"])
    def test_anything_else_is_not(self, value: str) -> None:
        """A freely typed value has no fixed slot-to-character mapping, so the
        renderer must fall back to the end-of-string caret rather than
        underline an arbitrary character."""
        assert grid.is_grid_form(value) is False
