# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Operator Screen's latency warning card, held up by Measure dialogs, on a fake clock."""

from __future__ import annotations

import pytest

from openfollow.runtime import latency_warning as lw
from openfollow.runtime.latency_warning import LatencyWarning

pytestmark = pytest.mark.unit


def _warning() -> tuple[LatencyWarning, list[float]]:
    clock = [100.0]
    return LatencyWarning(clock=lambda: clock[0]), clock


def test_an_open_dialog_shows_the_card_until_it_goes_quiet() -> None:
    warning, clock = _warning()
    assert not warning.showing()
    warning.open()
    clock[0] += lw.STALE_S
    assert warning.showing()
    clock[0] += 0.1
    assert not warning.showing()


def test_a_renewal_keeps_the_card_up() -> None:
    warning, clock = _warning()
    token = warning.open()
    for _ in range(5):
        clock[0] += lw.STALE_S - 0.5
        warning.renew(token)
    assert warning.showing()


def test_closing_takes_the_card_down_at_once() -> None:
    warning, _clock = _warning()
    warning.close(warning.open())
    assert not warning.showing()


def test_the_card_stays_while_any_dialog_holds_it() -> None:
    warning, clock = _warning()
    first = warning.open()
    clock[0] += 1.0
    second = warning.open()
    warning.close(first)
    assert warning.showing()
    clock[0] += lw.STALE_S
    assert warning.showing()
    warning.close(second)
    assert not warning.showing()


def test_a_dialog_back_from_a_hidden_tab_joins_again() -> None:
    warning, clock = _warning()
    token = warning.open()
    clock[0] += lw.STALE_S + 1.0
    assert not warning.showing()
    warning.renew(token)
    assert warning.showing()


def test_a_flood_of_dialogs_keeps_the_newest_few() -> None:
    warning, clock = _warning()
    warning.open()
    newer = []
    for _ in range(lw.MAX_SESSIONS):
        clock[0] += 0.001
        newer.append(warning.open())
    for token in newer[:-1]:
        warning.close(token)
    assert warning.showing()
    warning.close(newer[-1])
    # The oldest was dropped to make room, so nothing holds the card now.
    assert not warning.showing()


def test_card_showing_reads_the_web_commands_of_the_app() -> None:
    from types import SimpleNamespace

    warning, _clock = _warning()
    app = SimpleNamespace(_web_commands=SimpleNamespace(latency_warning=warning))
    assert not lw.card_showing(app)
    warning.open()
    assert lw.card_showing(app)
    assert not lw.card_showing(SimpleNamespace())
