"""Video cadence of the GL Operator Screen: frames on a refresh-locked cadence."""

from __future__ import annotations

import random

import pytest

from openfollow.video.gl_cadence import MAX_PTS_STEP_US, MAX_QUEUED, SETTLE_FRAMES, Cadence, CadenceReport

pytestmark = pytest.mark.unit

REFRESH = 16_667
FRAME = 33_333
# Arrivals around a refresh boundary, the worst case for showing frames as they arrive.
ON_A_REFRESH = 3 * REFRESH


def _play(
    cadence: Cadence[int],
    frames: list[tuple[int | None, int]],
    refreshes: int,
    start: int = 0,
    missed: frozenset[int] = frozenset(),
    refresh_jitter: int = 0,
) -> list[int | None]:
    """Push each (pts, arrival) frame once its arrival passed, pick once per refresh; what each refresh picked.

    ``missed`` refreshes pick nothing, as when the display skips one.
    """
    rng = random.Random(3)
    picked: list[int | None] = []
    pending = sorted(enumerate(frames), key=lambda f: f[1][1])
    for k in range(refreshes):
        now = start + k * REFRESH + rng.randint(-refresh_jitter, refresh_jitter)
        while pending and pending[0][1][1] <= now:
            index, (pts, arrival) = pending.pop(0)
            cadence.push(index, pts, arrival)
        picked.append(None if k in missed else cadence.pick(now, REFRESH))
    return picked


def _delays(picked: list[int | None], frames: list[tuple[int | None, int]]) -> dict[int, int]:
    """Each shown frame's refresh time minus its timestamp."""
    return {f: k * REFRESH - (frames[f][0] or 0) for k, f in enumerate(picked) if f is not None}


def _stream(count: int, latency: int, jitter: int = 0, seed: int = 1) -> list[tuple[int | None, int]]:
    rng = random.Random(seed)
    return [(i * FRAME, i * FRAME + latency + rng.randint(-jitter, jitter)) for i in range(count)]


def test_a_jittery_30_fps_feed_shows_every_frame_for_two_refreshes() -> None:
    cadence: Cadence[int] = Cadence()
    picked = _play(cadence, _stream(300, latency=ON_A_REFRESH, jitter=6_000), refreshes=610)

    report = cadence.report()
    assert [f for f in picked if f is not None] == list(range(300))
    assert report.refreshes == {2: 299}
    assert report.skipped == report.late == 0


def test_unlocked_the_same_feed_shows_the_newest_frame_and_loses_the_cadence() -> None:
    cadence: Cadence[int] = Cadence(locked=False)
    _play(cadence, _stream(300, latency=ON_A_REFRESH, jitter=6_000), refreshes=610)

    report = cadence.report()
    assert report.share(2) < 0.9
    assert report.wait_ms_p99 <= REFRESH / 1000


def test_the_first_frame_shows_one_refresh_after_it_anchors_the_cadence() -> None:
    cadence: Cadence[int] = Cadence()
    cadence.push(7, 1_000_000, 50)

    assert cadence.pick(100, REFRESH) is None
    assert cadence.pick(100 + REFRESH, REFRESH) == 7
    assert cadence.report().wait_ms_p50 == (100 + REFRESH - 50) / 1000


def test_a_frame_shows_at_the_refresh_at_its_due_time() -> None:
    cadence: Cadence[int] = Cadence()
    cadence.push(7, 1_000_000, 50)
    cadence.pick(100, REFRESH)

    assert cadence.pick(100 + REFRESH // 2, REFRESH) == 7


def test_waits_report_their_median_and_99th_percentile() -> None:
    cadence: Cadence[int] = Cadence(locked=False)
    for k in range(100):
        cadence.push(k, None, k * REFRESH - k * 100)
        cadence.pick(k * REFRESH, REFRESH)

    report = cadence.report()
    assert (report.wait_ms_p50, report.wait_ms_p99) == (5.0, 9.9)


def test_a_latency_step_costs_one_late_frame_then_the_cadence_holds() -> None:
    later = _stream(300, latency=30_000, jitter=5_000, seed=2)[150:]
    frames = _stream(150, latency=10_000) + later
    cadence: Cadence[int] = Cadence()
    picked = _play(cadence, frames, refreshes=610)

    report = cadence.report()
    assert [f for f in picked if f is not None] == list(range(300))
    assert report.late <= 2
    assert report.refreshes[2] >= 296


def test_a_latency_jump_of_several_refreshes_costs_one_late_frame() -> None:
    frames = _stream(100, latency=10_000) + _stream(200, latency=60_000)[100:]
    cadence: Cadence[int] = Cadence()
    picked = _play(cadence, frames, refreshes=610)

    report = cadence.report()
    assert [f for f in picked if f is not None] == list(range(200))
    assert report.late == 1


def test_the_settle_move_keeps_due_times_between_refreshes() -> None:
    count = SETTLE_FRAMES + 100
    frames = _stream(20, latency=30_000) + [(i * FRAME, i * FRAME + 10_000) for i in range(20, count)]
    cadence: Cadence[int] = Cadence()
    _play(cadence, frames, refreshes=2 * count + 10, refresh_jitter=3_000)

    assert cadence.report().refreshes == {2: count - 2, 1: 1}


def test_a_late_frame_starts_the_settle_run_over() -> None:
    # The first frame anchors the cadence; the rest arrive a refresh early, but one.
    spike = SETTLE_FRAMES - 1
    latency = {0: 40_000, spike: 60_000}
    frames = [(i * FRAME, i * FRAME + latency.get(i, 30_000)) for i in range(SETTLE_FRAMES + 150)]
    cadence: Cadence[int] = Cadence()
    delays = _delays(_play(cadence, frames, refreshes=2 * len(frames) + 10), frames)

    assert cadence.report().late == 1
    assert delays[spike + 100] == pytest.approx(delays[spike + 1], abs=1_000)
    assert delays[spike + 1] == pytest.approx(delays[spike - 1] + REFRESH, abs=1_000)


def test_a_latency_drop_lowers_the_wait_after_the_settle_run() -> None:
    count = SETTLE_FRAMES + 100
    frames = _stream(20, latency=30_000) + [(i * FRAME, i * FRAME + 10_000) for i in range(20, count)]
    cadence: Cadence[int] = Cadence()
    picked = _play(cadence, frames, refreshes=2 * count + 10)
    wait = {f: k * REFRESH - frames[f][1] for k, f in enumerate(picked) if f is not None}

    assert wait[count - 1] < wait[30] - REFRESH // 2
    assert all(w >= 0 for w in wait.values())


def test_an_occasional_slow_frame_keeps_the_wait_where_it_is() -> None:
    count = SETTLE_FRAMES + 100
    frames = [(i * FRAME, i * FRAME + (30_000 if i < 20 or i % 50 == 0 else 10_000)) for i in range(count)]
    cadence: Cadence[int] = Cadence()
    picked = _play(cadence, frames, refreshes=2 * count + 10)
    wait = {f: k * REFRESH - frames[f][1] for k, f in enumerate(picked) if f is not None}

    assert cadence.report().late == 0
    assert wait[count - 1] == pytest.approx(wait[31], abs=1_000)


def test_frames_without_timestamps_show_as_they_arrive() -> None:
    cadence: Cadence[int] = Cadence()
    cadence.push(1, None, 0)
    cadence.push(2, None, 10)

    assert cadence.pick(20, REFRESH) == 2
    assert cadence.report().skipped == 1


def test_a_timestamp_jump_re_anchors_instead_of_waiting_for_it() -> None:
    cadence: Cadence[int] = Cadence()
    _play(cadence, _stream(10, latency=5_000), refreshes=22)
    restart = 10 * FRAME + 5_000
    cadence.push(99, 9 * FRAME + 3 * MAX_PTS_STEP_US // 2, restart)

    assert cadence.pick(restart + REFRESH, REFRESH) is None
    assert cadence.pick(restart + 2 * REFRESH, REFRESH) == 99


def test_a_timestamp_step_drops_the_frames_queued_before_it() -> None:
    cadence: Cadence[int] = Cadence()
    cadence.push(0, 0, 0)
    cadence.pick(0, REFRESH)
    cadence.pick(REFRESH, REFRESH)
    cadence.push(1, FRAME, FRAME)
    cadence.pick(2 * REFRESH, REFRESH)
    cadence.push(99, FRAME - 5 * MAX_PTS_STEP_US, FRAME + 100)

    assert [cadence.pick(k * REFRESH, REFRESH) for k in (3, 4)] == [None, 99]
    assert cadence.report().skipped == 1


def test_the_queue_holds_at_most_max_queued_frames() -> None:
    cadence: Cadence[int] = Cadence()
    for i in range(MAX_QUEUED + 3):
        cadence.push(i, i * FRAME, i * FRAME)

    assert cadence.report().skipped == 3


def test_a_missed_refresh_counts_toward_the_time_on_screen() -> None:
    cadence: Cadence[int] = Cadence()
    _play(cadence, _stream(20, latency=5_000), refreshes=42, missed=frozenset({22}))

    assert cadence.report().refreshes == {2: 17, 3: 1, 1: 1}


def test_a_second_render_at_one_refresh_shows_nothing_new_and_counts_nothing() -> None:
    cadence: Cadence[int] = Cadence(locked=False)
    cadence.push(1, None, 0)
    shown = [cadence.pick(0, REFRESH)]
    cadence.push(2, None, 5)
    shown.append(cadence.pick(0, REFRESH))
    shown.append(cadence.pick(REFRESH, REFRESH))
    cadence.push(3, None, REFRESH + 5)
    shown.append(cadence.pick(2 * REFRESH, REFRESH))

    assert shown == [1, None, 2, 3]
    assert cadence.report().refreshes == {1: 2}


def test_a_stalled_refresh_drops_the_oldest_and_re_anchors_on_the_newest() -> None:
    cadence: Cadence[int] = Cadence()
    cadence.push(0, 0, 0)
    cadence.pick(0, REFRESH)
    newest = MAX_QUEUED + 1
    for i in range(1, newest + 1):
        cadence.push(i, i * FRAME, i * FRAME)
    now = newest * FRAME + 1_000

    assert cadence.pick(now, REFRESH) == newest - 1
    assert cadence.pick(now + REFRESH, REFRESH) == newest
    assert cadence.report().skipped == newest - 1


def test_timestamps_running_ahead_of_the_clock_re_anchor_instead_of_freezing() -> None:
    frames = [(i * 2 * FRAME, i * FRAME + 5_000) for i in range(100)]
    cadence: Cadence[int] = Cadence()
    picked = _play(cadence, frames, refreshes=200)

    assert max(f for f in picked if f is not None) >= 90


def test_a_report_starts_the_next_period() -> None:
    cadence: Cadence[int] = Cadence()
    _play(cadence, _stream(20, latency=5_000), refreshes=42)
    first = cadence.report()

    assert first.shown == 19
    assert cadence.report() == CadenceReport(refreshes={}, skipped=0, late=0, wait_ms_p50=0.0, wait_ms_p99=0.0)


@pytest.mark.parametrize(
    ("refreshes", "count", "share"),
    [({2: 3, 3: 1}, 2, 0.75), ({2: 3, 3: 1}, 1, 0.0), ({}, 2, 0.0)],
    ids=["two", "absent", "nothing-shown"],
)
def test_share_of_frames_by_refreshes_on_screen(refreshes: dict[int, int], count: int, share: float) -> None:
    report = CadenceReport(refreshes=refreshes, skipped=0, late=0, wait_ms_p50=0.0, wait_ms_p99=0.0)

    assert report.share(count) == share
