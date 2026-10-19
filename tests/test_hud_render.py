# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The HUD drawn on its own thread, paced by the display: a refresh starts a picture, a later one shows it."""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import cairo
import numpy as np
import pytest

from openfollow.runtime.overlay_layers import Layers
from openfollow.video.hud_render import HudRenderThread
from tests._fake_cairo import FakeCairo

pytestmark = pytest.mark.unit

WAIT_S = 5.0
RED = (255, 0, 0, 255)
CLEAR = (0, 0, 0, 0)


@dataclass(eq=False)
class _Scene:
    """The state handed to the fake renderer: a red 10 x 10 square at ``at``, or nothing."""

    square: bool = True
    at: tuple[int, int] = (5, 5)


@dataclass
class _Drawn:
    state: Any
    width: int
    height: int
    scale: tuple[float, float]
    into: cairo.Surface


REFRESH_S = 1 / 60


class _Clock:
    """A clock the test moves; the render thread reading it marks a finished picture."""

    def __init__(self, refresh_s: float = REFRESH_S) -> None:
        self.refresh_s = refresh_s
        self.now = self.refreshed_at = 100.0
        self.finished = threading.Semaphore(0)

    def __call__(self) -> float:
        # Read before releasing: the test moves the clock to the next refresh as soon as it is released.
        now = self.now
        if threading.current_thread().name == "HudRender":
            self.finished.release()
        return now


class _Renderer:
    """Draws the scene it is handed and records each picture."""

    def __init__(self, clock: _Clock | None = None) -> None:
        self.state: Any = _Scene()
        self.drawn: list[_Drawn] = []
        self.started = threading.Event()
        self.gate: threading.Event | None = None
        self.raise_next = False
        self.clock = clock
        self.draw_s = 0.0

    def draw(self, cr: Any, width: int, height: int, state: Any) -> None:
        gate = self.gate
        self.started.set()  # after reading the gate, so a test may swap it once this is set
        if gate is not None:
            gate.wait(WAIT_S)
        if self.clock is not None:
            self.clock.now += self.draw_s
        if self.raise_next:
            self.raise_next = False
            raise RuntimeError("boom")
        target = cr.get_target()
        self.drawn.append(_Drawn(state, width, height, target.get_device_scale(), target))
        if state.square:
            cr.set_source_rgba(1.0, 0.0, 0.0, 1.0)
            cr.rectangle(*state.at, 10, 10)
            cr.fill()


@pytest.fixture
def renderer() -> _Renderer:
    return _Renderer()


@pytest.fixture
def hud(renderer: _Renderer) -> Iterator[HudRenderThread]:
    thread = HudRenderThread(renderer)
    thread.start()
    yield thread
    if renderer.gate is not None:
        renderer.gate.set()
    thread.stop()


@pytest.fixture
def logged() -> Iterator[list[logging.LogRecord]]:
    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Collect()
    log = logging.getLogger("openfollow.video.hud_render")
    log.addHandler(handler)
    yield records
    log.removeHandler(handler)


def _until(check: Callable[[], Any], what: str) -> None:
    """Poll until ``check`` holds; only to synchronise with the thread."""
    for _ in range(int(WAIT_S / 0.002)):
        if check():
            return
        threading.Event().wait(0.002)
    raise AssertionError(f"timed out waiting for {what}")


def _refresh_until(hud: HudRenderThread, check: Callable[[], Any], what: str) -> None:
    """Refresh as the display does until ``check`` holds."""

    def refreshed() -> Any:
        hud.tick()
        return check()

    _until(refreshed, what)


def _show(hud: HudRenderThread) -> None:
    """Refresh until one shows a finished picture; that refresh also starts the next."""
    _until(hud.tick, "a refresh that shows a picture")


def _painted(hud: HudRenderThread, width: int = 64, height: int = 36, scale: float = 1.0) -> cairo.ImageSurface:
    """What a GTK draw of a fresh canvas shows."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, math.ceil(width * scale), math.ceil(height * scale))
    surface.set_device_scale(scale, scale)
    hud.paint(cairo.Context(surface), width, height)
    return surface


def _pixel(surface: cairo.ImageSurface, x: int, y: int) -> tuple[int, int, int, int]:
    surface.flush()
    h, stride = surface.get_height(), surface.get_stride()
    data = np.ndarray((h, stride // 4, 4), np.uint8, surface.get_data())
    b, g, r, a = (int(v) for v in data[y, x])
    return r, g, b, a


def _hud_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "HudRender" and t.is_alive()]


def test_nothing_is_drawn_until_a_refresh_and_the_first_paint_shows_nothing(hud, renderer) -> None:
    cr = FakeCairo()
    hud.paint(cr, 64, 36)
    assert ("paint",) not in cr.calls
    assert not renderer.started.wait(0.2)


def test_a_refresh_starts_a_picture_of_the_state_current_at_that_refresh(hud, renderer) -> None:
    hud.paint(FakeCairo(), 64, 36)
    renderer.state = at_refresh = _Scene()
    hud.tick()
    renderer.state = _Scene()  # the frame loop moves on before the thread draws
    _until(lambda: renderer.drawn, "a picture")
    assert [(d.state, d.width, d.height, d.scale) for d in renderer.drawn] == [(at_refresh, 64, 36, (1.0, 1.0))]


def test_a_refresh_before_any_paint_starts_nothing(hud, renderer, logged) -> None:
    assert hud.tick() is False
    assert not renderer.started.wait(0.2)
    hud.paint(FakeCairo(), 64, 36)
    hud.tick()
    _until(lambda: renderer.drawn, "the first paint's refresh to start a picture")
    assert logged == [], "the early refresh asked the thread for a picture it had no canvas for"


def test_the_refresh_after_a_picture_finished_redraws_once(hud, renderer) -> None:
    hud.paint(FakeCairo(), 64, 36)
    hud.tick()
    _until(lambda: renderer.drawn, "the first picture")
    renderer.gate = threading.Event()  # hold the picture the showing refresh starts
    _show(hud)
    assert hud.tick() is False, "nothing new until the next picture finishes"


def test_the_paint_after_that_shows_the_finished_picture(hud, renderer) -> None:
    hud.paint(FakeCairo(), 64, 36)
    hud.tick()
    _show(hud)
    surface = _painted(hud)
    assert _pixel(surface, 8, 8) == RED
    assert _pixel(surface, 30, 30) == CLEAR


def test_a_finished_picture_waits_for_the_refresh_that_shows_it(hud, renderer) -> None:
    hud.paint(FakeCairo(), 64, 36)
    hud.tick()
    renderer.state = _Scene(at=(40, 22))
    _show(hud)  # shows the square at (5, 5) and starts the one at (40, 22)
    _until(lambda: len(renderer.drawn) == 2, "the second picture")
    # A repaint no refresh asked for, as a video frame under the HUD causes, shows the picture on screen.
    for _ in range(40):
        surface = _painted(hud)
        assert (_pixel(surface, 8, 8), _pixel(surface, 45, 27)) == (RED, CLEAR)
        threading.Event().wait(0.005)
    _show(hud)
    surface = _painted(hud)
    assert (_pixel(surface, 8, 8), _pixel(surface, 45, 27)) == (CLEAR, RED)


def test_the_picture_on_screen_is_never_drawn_into(hud, renderer) -> None:
    hud.paint(FakeCairo(), 64, 36)
    hud.tick()
    _show(hud)
    _until(lambda: len(renderer.drawn) == 2, "the second picture")
    renderer.gate = threading.Event()
    renderer.started.clear()
    _show(hud)  # shows the second picture; the third is started and held mid-draw
    assert renderer.started.wait(WAIT_S)
    assert _pixel(_painted(hud), 8, 8) == RED


def test_a_hidpi_screen_gets_a_picture_at_its_own_scale(hud, renderer) -> None:
    _painted(hud, scale=2.0)
    hud.tick()
    _show(hud)
    assert renderer.drawn[0].scale == (2.0, 2.0)
    surface = _painted(hud, scale=2.0)
    # The square at (5, 5)-(15, 15) in canvas units lands at (10, 10)-(30, 30) in device pixels.
    assert _pixel(surface, 28, 28) == RED
    assert _pixel(surface, 32, 32) == CLEAR


def test_a_refresh_while_a_picture_is_drawn_starts_nothing_more(hud, renderer) -> None:
    renderer.gate = threading.Event()
    states = [_Scene() for _ in range(3)]
    renderer.state = states[0]
    hud.paint(FakeCairo(), 64, 36)
    hud.tick()
    assert renderer.started.wait(WAIT_S)
    renderer.state = states[1]
    assert hud.tick() is False  # still drawing: no redraw, no second picture
    renderer.state = states[2]
    renderer.gate.set()
    _show(hud)
    _until(lambda: len(renderer.drawn) == 2, "the next picture")
    assert [d.state for d in renderer.drawn] == [states[0], states[2]]


def test_a_draw_that_fails_is_logged_and_the_next_refresh_tries_again(hud, renderer, logged) -> None:
    renderer.raise_next = True
    hud.paint(FakeCairo(), 64, 36)
    hud.tick()
    _until(lambda: logged, "the failure to be logged")
    assert "Unhandled exception in HUD render" in logged[0].getMessage()
    assert renderer.drawn == []
    _refresh_until(hud, lambda: renderer.drawn, "a picture after the failed one")


def test_a_draw_that_fails_leaves_the_picture_on_screen(hud, renderer, logged) -> None:
    hud.paint(FakeCairo(), 64, 36)
    hud.tick()
    _show(hud)
    _until(lambda: len(renderer.drawn) == 2, "the second picture")
    renderer.raise_next = True
    _show(hud)  # shows the second picture and starts a third, which fails
    _until(lambda: logged, "the failure to be logged")
    renderer.gate = threading.Event()
    renderer.started.clear()
    _refresh_until(hud, renderer.started.is_set, "a picture after the failed one")
    assert _pixel(_painted(hud), 8, 8) == RED


def test_a_picture_drawn_again_starts_empty(hud, renderer) -> None:
    """Two pictures take turns, so the third is drawn into the first one's picture."""
    hud.paint(FakeCairo(), 64, 36)
    hud.tick()
    _show(hud)  # shows the first square and starts a second
    renderer.state = _Scene(square=False)
    _show(hud)  # starts the empty third
    _show(hud)
    assert renderer.drawn[2].into == renderer.drawn[0].into != renderer.drawn[1].into
    assert _pixel(_painted(hud), 8, 8) == CLEAR


def test_a_larger_canvas_gets_a_picture_that_covers_it(hud, renderer) -> None:
    hud.paint(FakeCairo(), 20, 12)
    hud.tick()
    _show(hud)
    _show(hud)  # two small pictures exist
    renderer.state = _Scene(at=(40, 22))  # beyond the earlier canvas
    hud.paint(FakeCairo(), 64, 36)
    _show(hud)
    _show(hud)
    assert (renderer.drawn[3].width, renderer.drawn[3].height) == (64, 36)
    assert _pixel(_painted(hud), 45, 27) == RED


def test_a_new_screen_scale_gets_a_picture_at_that_scale(hud, renderer) -> None:
    hud.paint(FakeCairo(), 64, 36)
    hud.tick()
    _show(hud)
    _show(hud)  # two pictures exist at scale 1
    _painted(hud, 32, 18, scale=2.0)  # the same 64 x 36 pixels, as 32 x 18 at scale 2
    _show(hud)
    _show(hud)
    assert renderer.drawn[3].scale == (2.0, 2.0), "drawn at the old scale, the picture would be stretched and blurred"
    assert _pixel(_painted(hud, 32, 18, scale=2.0), 28, 28) == RED


def test_stop_ends_the_thread_and_nothing_is_drawn_after(renderer) -> None:
    before = set(_hud_threads())
    hud = HudRenderThread(renderer)
    hud.start()
    hud.start()  # a second start is a no-op
    assert len(set(_hud_threads()) - before) == 1
    hud.stop()
    assert set(_hud_threads()) - before == set()
    hud.paint(FakeCairo(), 64, 36)
    hud.tick()
    assert not renderer.started.wait(0.2)


def test_stopping_a_thread_never_started_is_harmless(renderer) -> None:
    before = set(_hud_threads())
    HudRenderThread(renderer).stop()
    assert set(_hud_threads()) == before


@pytest.fixture
def clock(request: pytest.FixtureRequest) -> _Clock:
    """A display refreshing at 60 Hz, or at the rate the test is parametrised with."""
    return _Clock(1 / getattr(request, "param", 60))


@pytest.fixture
def paced(clock: _Clock) -> Iterator[tuple[HudRenderThread, _Renderer]]:
    renderer = _Renderer(clock)
    thread = HudRenderThread(renderer, clock=clock)
    thread.start()
    thread.paint(FakeCairo(), 64, 36)
    yield thread, renderer
    if renderer.gate is not None:
        renderer.gate.set()
    thread.stop()


def _refresh(hud: HudRenderThread, clock: _Clock) -> bool:
    """The next refresh, on the display's own schedule whatever the drawing took."""
    clock.refreshed_at += clock.refresh_s
    clock.now = clock.refreshed_at
    return hud.tick()


def _finished(clock: _Clock) -> None:
    assert clock.finished.acquire(timeout=WAIT_S), "the picture never finished"


def _count_to_show(hud: HudRenderThread, clock: _Clock) -> int:
    """Count the refreshes until one shows a picture."""
    for count in range(1, 60):
        if _refresh(hud, clock):
            return count
    raise AssertionError("the finished picture was never shown")


def _refreshes_to_show(hud: HudRenderThread, clock: _Clock) -> int:
    """Wait for the picture being drawn, then count the refreshes until one shows it."""
    _finished(clock)
    return _count_to_show(hud, clock)


def _miss_one_refresh(hud: HudRenderThread, clock: _Clock, renderer: _Renderer, refreshes: int) -> None:
    """Show a picture, then hold the next one in the drawing for ``refreshes`` refreshes before it finishes."""
    _finished(clock)
    renderer.gate = threading.Event()
    _count_to_show(hud, clock)  # shows the picture before and starts the held one
    for _ in range(refreshes):
        assert _refresh(hud, clock) is False
    renderer.gate.set()
    renderer.gate = None
    assert _refreshes_to_show(hud, clock) == 1


def _miss_in_a_row(hud: HudRenderThread, clock: _Clock, renderer: _Renderer, refreshes: int, count: int) -> None:
    """Show a picture, then hold each of the next ``count`` in the drawing for ``refreshes`` refreshes."""
    _finished(clock)
    renderer.started.clear()
    renderer.gate = threading.Event()
    _count_to_show(hud, clock)  # shows the picture before and starts the first held one
    for left in reversed(range(count)):
        assert renderer.started.wait(WAIT_S)
        renderer.started.clear()
        for _ in range(refreshes):
            assert _refresh(hud, clock) is False
        # The refresh that shows this one starts the next, held by the gate set here.
        gate, renderer.gate = renderer.gate, threading.Event() if left else None
        gate.set()
        assert _refreshes_to_show(hud, clock) == 1


def _lengthen(hud: HudRenderThread, clock: _Clock, renderer: _Renderer, refreshes: int) -> None:
    """Three pictures in a row that each need ``refreshes + 1`` refreshes."""
    for _ in range(3):
        _miss_one_refresh(hud, clock, renderer, refreshes)


def test_a_lone_slow_picture_is_shown_late_and_changes_nothing(paced, clock) -> None:
    hud, renderer = paced
    hud.tick()
    assert _refreshes_to_show(hud, clock) == 1
    _miss_one_refresh(hud, clock, renderer, refreshes=2)  # needed three refreshes
    assert [_refreshes_to_show(hud, clock) for _ in range(5)] == [1, 1, 1, 1, 1]


def test_pictures_that_keep_missing_their_refresh_hold_the_later_ones_as_long(paced, clock) -> None:
    hud, renderer = paced
    hud.tick()
    _refreshes_to_show(hud, clock)
    _lengthen(hud, clock, renderer, refreshes=2)  # each needed three refreshes
    # Later pictures finish at once, and still each reaches the screen three refreshes after it was sampled.
    assert [_refreshes_to_show(hud, clock) for _ in range(5)] == [3, 3, 3, 3, 3]


def test_one_very_slow_picture_among_repeated_misses_does_not_set_the_interval(paced, clock) -> None:
    hud, renderer = paced
    hud.tick()
    _refreshes_to_show(hud, clock)
    _miss_one_refresh(hud, clock, renderer, refreshes=1)
    _miss_one_refresh(hud, clock, renderer, refreshes=3)
    _miss_one_refresh(hud, clock, renderer, refreshes=1)
    assert [_refreshes_to_show(hud, clock) for _ in range(5)] == [2, 2, 2, 2, 2]


# Lengthened from 1, so no shorter than 2 until a steady run there.
@pytest.mark.parametrize(("draw_refreshes", "fits"), [(0.0, 2), (0.1, 2), (1.5, 3), (2.5, 4)])
def test_two_seconds_of_quick_pictures_take_the_least_interval_they_all_fit_above_the_one_that_failed(
    paced, clock, draw_refreshes, fits
) -> None:
    hud, renderer = paced
    hud.tick()
    _refreshes_to_show(hud, clock)
    _lengthen(hud, clock, renderer, refreshes=4)
    renderer.draw_s = draw_refreshes * clock.refresh_s
    held = [_refreshes_to_show(hud, clock) for _ in range(60)]
    assert held == [5] * 24 + [fits] * 36


def test_the_interval_after_a_stall_comes_back_after_four_seconds_of_quick_pictures(paced, clock) -> None:
    hud, renderer = paced
    hud.tick()
    _refreshes_to_show(hud, clock)
    renderer.draw_s = 0.5 - 29 * clock.refresh_s
    _lengthen(hud, clock, renderer, refreshes=29)  # a 1.5 s stall: three pictures of half a second each
    renderer.draw_s = 0.005
    held = [_refreshes_to_show(hud, clock) for _ in range(70)]
    assert held == [30] * 4 + [2] * 60 + [1] * 6


def test_pictures_that_keep_missing_a_long_interval_lengthen_it_again(paced, clock) -> None:
    hud, renderer = paced
    hud.tick()
    _refreshes_to_show(hud, clock)
    _lengthen(hud, clock, renderer, refreshes=30)
    _miss_in_a_row(hud, clock, renderer, refreshes=39, count=3)  # at 31, each needed forty refreshes
    assert [_refreshes_to_show(hud, clock) for _ in range(3)] == [40, 40, 40]


def test_three_misses_within_sixty_refreshes_lengthen_the_interval(paced, clock) -> None:
    hud, renderer = paced
    hud.tick()
    _refreshes_to_show(hud, clock)
    renderer.draw_s = 0.1 * clock.refresh_s
    for _ in range(2):
        _miss_one_refresh(hud, clock, renderer, refreshes=1)
        assert [_refreshes_to_show(hud, clock) for _ in range(26)] == [1] * 26
    _miss_one_refresh(hud, clock, renderer, refreshes=1)  # the third miss, 57 pictures after the first
    assert [_refreshes_to_show(hud, clock) for _ in range(5)] == [2] * 5


def test_a_slow_picture_every_twentieth_settles_on_one_interval(paced, clock) -> None:
    hud, renderer = paced
    hud.tick()
    _refreshes_to_show(hud, clock)
    renderer.draw_s = 0.3 * clock.refresh_s
    held = []
    for _ in range(30):
        held += [_refreshes_to_show(hud, clock) for _ in range(18)]
        _miss_one_refresh(hud, clock, renderer, refreshes=7)  # one quick picture, then one held eight refreshes
    intervals = [n for i, n in enumerate(held) if i == 0 or n != held[i - 1]]
    assert intervals == [1, 8, 2], "it settles where the slow picture misses alone"
    assert held[-300:] == [2] * 300


def test_a_picture_missing_once_a_second_does_not_lengthen_the_interval(paced, clock) -> None:
    hud, renderer = paced
    hud.tick()
    _refreshes_to_show(hud, clock)
    _lengthen(hud, clock, renderer, refreshes=2)
    renderer.draw_s = 0.1 * clock.refresh_s
    for _ in range(6):
        _miss_one_refresh(hud, clock, renderer, refreshes=3)  # one quick picture, then one held four refreshes
        assert [_refreshes_to_show(hud, clock) for _ in range(18)] == [3] * 18


def test_slow_pictures_far_apart_never_lengthen_the_interval(paced, clock) -> None:
    hud, renderer = paced
    hud.tick()
    _refreshes_to_show(hud, clock)
    for _ in range(4):
        _miss_one_refresh(hud, clock, renderer, refreshes=1)
        assert [_refreshes_to_show(hud, clock) for _ in range(30)] == [1] * 30


@pytest.mark.parametrize("clock", [30, 60, 120], indirect=True, ids=["30Hz", "60Hz", "120Hz"])
def test_pictures_well_inside_a_shorter_interval_bring_it_back_down(paced, clock) -> None:
    hud, renderer = paced
    hud.tick()
    _refreshes_to_show(hud, clock)
    _lengthen(hud, clock, renderer, refreshes=1)
    renderer.draw_s = 0.6 * clock.refresh_s
    held = [_refreshes_to_show(hud, clock) for _ in range(80)]
    assert held == [2] * 60 + [1] * 20, "two seconds of quick pictures are a steady load, less is not"


@pytest.mark.parametrize("clock", [30, 60, 120], indirect=True, ids=["30Hz", "60Hz", "120Hz"])
def test_pictures_that_need_most_of_a_shorter_interval_keep_the_longer_one(paced, clock) -> None:
    hud, renderer = paced
    hud.tick()
    _refreshes_to_show(hud, clock)
    _lengthen(hud, clock, renderer, refreshes=1)
    renderer.draw_s = 0.75 * clock.refresh_s  # fits one refresh, but with too little room for the next slow one
    assert [_refreshes_to_show(hud, clock) for _ in range(300)] == [2] * 300


def test_one_slow_picture_starts_the_steady_run_again(paced, clock) -> None:
    hud, renderer = paced
    hud.tick()
    _refreshes_to_show(hud, clock)
    _lengthen(hud, clock, renderer, refreshes=1)
    renderer.draw_s = 0.1 * clock.refresh_s
    held = [_refreshes_to_show(hud, clock) for _ in range(59)]
    renderer.draw_s = 0.9 * clock.refresh_s  # shown on time, with too little room for a shorter interval
    held.append(_refreshes_to_show(hud, clock))
    renderer.draw_s = 0.1 * clock.refresh_s
    held += [_refreshes_to_show(hud, clock) for _ in range(70)]
    assert held == [2] * 120 + [1] * 10


@pytest.mark.parametrize("late", [0, 1], ids=["first-refresh", "showing-refresh"])
def test_one_late_refresh_does_not_stretch_the_interval_measured(paced, clock, late) -> None:
    hud, renderer = paced
    hud.tick()
    _refreshes_to_show(hud, clock)
    _lengthen(hud, clock, renderer, refreshes=1)
    renderer.draw_s = 0.75 * clock.refresh_s
    held = [_refreshes_to_show(hud, clock) for _ in range(70)]
    _finished(clock)
    for _ in range(late):
        assert _refresh(hud, clock) is False
    clock.refreshed_at += 3 * clock.refresh_s  # the display skips three refreshes
    held.append(late + _count_to_show(hud, clock))
    held += [_refreshes_to_show(hud, clock) for _ in range(60)]
    assert held == [2] * 131


def test_a_canvas_set_without_a_paint_gets_pictures(hud, renderer) -> None:
    """A GL display gives the canvas directly; the GTK draw never runs."""
    hud.set_canvas(64, 36, (2.0, 2.0))
    hud.tick()
    _until(lambda: renderer.drawn, "a picture")
    assert [(d.width, d.height, d.scale) for d in renderer.drawn] == [(64, 36, (2.0, 2.0))]


def test_each_picture_put_on_screen_has_a_new_serial(hud, renderer) -> None:
    """The two pictures take turns, so the same surface returns with new content and a new serial."""
    assert hud.shown_picture() == (None, 0)
    hud.set_canvas(64, 36, (1.0, 1.0))
    hud.tick()
    seen = []
    for _ in range(3):
        _show(hud)
        seen.append(hud.shown_picture())
    surfaces, serials = zip(*seen, strict=True)
    assert serials == (1, 2, 3)
    assert surfaces[0] is surfaces[2] and surfaces[0] is not surfaces[1]


def test_a_layered_hud_hands_over_sprites_and_pastes_nothing_itself(renderer) -> None:
    hud = HudRenderThread(renderer, layered=True)
    hud.start()
    try:
        hud.set_canvas(64, 36, (1.0, 1.0))
        hud.tick()
        _show(hud)
        layers, serial = hud.shown_picture()
        assert isinstance(layers, Layers) and serial == 1
        assert [(s.x, s.y, s.kept) for s in layers.sprites] == [(4, 4, False)]
        assert _pixel(_painted(hud), 10, 10) == CLEAR
    finally:
        hud.stop()


def test_the_pacing_report_counts_pictures_and_the_late_ones_since_the_last(paced, clock) -> None:
    hud, renderer = paced
    hud.tick()
    assert _refreshes_to_show(hud, clock) == 1
    _miss_one_refresh(hud, clock, renderer, refreshes=2)

    assert hud.pacing_report() == (3, 1, 1)
    assert hud.pacing_report() == (0, 0, 1)


class _FontOptionsRecorder(_Renderer):
    def __init__(self) -> None:
        super().__init__()
        self.options: list[cairo.FontOptions] = []

    def draw(self, cr: Any, width: int, height: int, state: Any) -> None:
        self.options.append(cr.get_font_options())
        super().draw(cr, width, height, state)


def test_a_layered_hud_lays_text_out_as_on_a_canvas_sized_picture() -> None:
    renderer = _FontOptionsRecorder()
    hud = HudRenderThread(renderer, layered=True)
    hud.start()
    try:
        hud.set_canvas(64, 36, (1.0, 1.0))
        hud.tick()
        _show(hud)
    finally:
        hud.stop()

    assert renderer.options[0] == cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1).get_font_options()


def test_a_layered_hud_set_back_draws_canvas_sized_pictures(renderer) -> None:
    hud = HudRenderThread(renderer, layered=True)
    hud.start()
    try:
        hud.set_canvas(64, 36, (1.0, 1.0))
        hud.tick()
        _show(hud)
        hud.set_layered(False)
        _until(lambda: hud.tick() and isinstance(hud.shown_picture()[0], cairo.ImageSurface), "a canvas-sized picture")
        assert _pixel(_painted(hud), 10, 10) == RED
    finally:
        hud.stop()
