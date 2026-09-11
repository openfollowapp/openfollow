# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for :mod:`openfollow.runtime.state_maps`.

The lazy-create / prune pair behind every per-marker state map the frame loop
carries (pin states, assist anchors, velocity estimates).
"""

from __future__ import annotations

import pytest

from openfollow.runtime.state_maps import get_or_create, prune_to_keep

pytestmark = pytest.mark.unit


def test_get_or_create_builds_once_and_returns_the_same_object() -> None:
    made: list[list[int]] = []

    def _factory() -> list[int]:
        made.append([])
        return made[-1]

    mapping: dict[int, list[int]] = {}
    first = get_or_create(mapping, 7, _factory)
    first.append(1)
    again = get_or_create(mapping, 7, _factory)

    assert again is first
    assert again == [1]
    assert len(made) == 1


def test_get_or_create_keeps_a_stored_falsy_value() -> None:
    """State that happens to be empty is still state: it must not be rebuilt."""
    mapping: dict[str, list[int]] = {"a": []}
    stored = mapping["a"]
    assert get_or_create(mapping, "a", lambda: [9]) is stored


def test_prune_drops_only_the_keys_outside_keep() -> None:
    mapping = {1: "a", 2: "b", 3: "c"}
    kept = mapping[2]
    prune_to_keep(mapping, {2, 4})
    assert mapping == {2: "b"}
    assert mapping[2] is kept


def test_prune_with_an_empty_keep_clears_the_map() -> None:
    mapping = {1: "a", 2: "b"}
    prune_to_keep(mapping, set())
    assert mapping == {}


def test_prune_leaves_a_fully_kept_map_untouched() -> None:
    mapping = {1: "a", 2: "b"}
    prune_to_keep(mapping, {1, 2, 3})
    assert mapping == {1: "a", 2: "b"}
