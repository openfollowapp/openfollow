# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""The Operator Screen's latency warning card, held up by Measure dialogs, on a fake clock."""

from __future__ import annotations

import pytest

from openfollow.runtime import latency_warning as lw
from openfollow.runtime.latency_warning import LatencyWarning

pytestmark = pytest.mark.unit


def _shown(warning: LatencyWarning) -> str:
    """A dialog showing its warnings: a token, renewed once."""
    token = warning.token()
    warning.renew(token)
    return token


def _warning() -> tuple[LatencyWarning, list[float]]:
    clock = [100.0]
    return LatencyWarning(clock=lambda: clock[0]), clock


def test_an_open_dialog_shows_the_card_until_it_goes_quiet() -> None:
    warning, clock = _warning()
    assert not warning.showing()
    _shown(warning)
    clock[0] += lw.STALE_S
    assert warning.showing()
    clock[0] += 0.1
    assert not warning.showing()


def test_a_renewal_keeps_the_card_up() -> None:
    warning, clock = _warning()
    token = _shown(warning)
    for _ in range(5):
        clock[0] += lw.STALE_S - 0.5
        warning.renew(token)
    assert warning.showing()


def test_closing_takes_the_card_down_at_once() -> None:
    warning, _clock = _warning()
    warning.close(_shown(warning))
    assert not warning.showing()


def test_a_token_is_only_a_name_until_a_renewal_shows_it() -> None:
    warning, _clock = _warning()
    warning.token()
    assert not warning.showing()


def test_a_closed_dialog_never_comes_back() -> None:
    warning, clock = _warning()
    token = _shown(warning)
    warning.close(token)
    # A renewal still on its way, now and after the session would have gone stale.
    warning.renew(token)
    clock[0] += lw.STALE_S + 1.0
    warning.renew(token)
    assert not warning.showing()


def test_only_the_newest_closed_tokens_are_remembered() -> None:
    warning, _clock = _warning()
    first = _shown(warning)
    warning.close(first)
    for _ in range(lw.MAX_CLOSED):
        warning.close(warning.token())
    warning.renew(first)
    assert warning.showing()


def test_the_card_stays_while_any_dialog_holds_it() -> None:
    warning, clock = _warning()
    first = _shown(warning)
    clock[0] += 1.0
    second = _shown(warning)
    warning.close(first)
    assert warning.showing()
    clock[0] += lw.STALE_S
    assert warning.showing()
    warning.close(second)
    assert not warning.showing()


def test_a_dialog_back_from_a_hidden_tab_joins_again() -> None:
    warning, clock = _warning()
    token = _shown(warning)
    clock[0] += lw.STALE_S + 1.0
    assert not warning.showing()
    warning.renew(token)
    assert warning.showing()


def test_a_flood_of_dialogs_keeps_the_newest_few() -> None:
    warning, clock = _warning()
    _shown(warning)
    newer = []
    for _ in range(lw.MAX_SESSIONS):
        clock[0] += 0.001
        newer.append(_shown(warning))
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
    _shown(warning)
    assert lw.card_showing(app)
    assert not lw.card_showing(SimpleNamespace())
