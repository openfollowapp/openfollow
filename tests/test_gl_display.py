# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The GL display's plain parts: switches, plane layout, letterbox, sprites, colour terms and log lines."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import cairo
import pytest

from openfollow.runtime.overlay_layers import Sprite
from openfollow.video import gl_display
from openfollow.video.gl_cadence import CadenceReport
from openfollow.video.gl_display import Plane, i420_planes, letterbox, sprite_quad, yuv_to_rgb

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("env", "flag", "on"),
    [("1", False, True), ("", True, True), ("", False, False), ("0", False, False), ("yes", False, False)],
    ids=["env", "flag-file", "neither", "env-0", "env-not-1"],
)
def test_the_switch_is_the_environment_or_the_flag_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: str, flag: bool, on: bool
) -> None:
    monkeypatch.setenv(gl_display.ENV_SWITCH, env)
    flag_file = tmp_path / "flag"
    if flag:
        flag_file.touch()
    monkeypatch.setattr(gl_display, "FLAG_FILE", str(flag_file))
    assert gl_display.gl_display_requested() is on


def test_i420_planes_halve_the_chroma_rounding_up() -> None:
    assert i420_planes(1921, 1081, (0, 100, 200), (1984, 992, 992)) == [
        Plane(0, 1984, 1921, 1081),
        Plane(100, 992, 961, 541),
        Plane(200, 992, 961, 541),
    ]


@pytest.mark.parametrize(
    ("frame", "area", "box"),
    [
        ((1920, 1080), (1920, 1080), (-1.0, -1.0, 1.0, 1.0)),
        ((1280, 720), (1920, 1080), (-1.0, -1.0, 1.0, 1.0)),
        ((1440, 1080), (1920, 1080), (-0.75, -1.0, 0.75, 1.0)),
        ((1920, 1080), (1920, 1440), (-1.0, -0.75, 1.0, 0.75)),
        ((0, 1080), (1920, 1080), (-1.0, -1.0, 1.0, 1.0)),
        ((1920, 1080), (0, 0), (-1.0, -1.0, 1.0, 1.0)),
    ],
    ids=["same", "scaled-up", "pillarbox", "letterbox", "no-frame", "no-area"],
)
def test_a_frame_keeps_its_aspect(
    frame: tuple[int, int], area: tuple[int, int], box: tuple[float, float, float, float]
) -> None:
    assert letterbox(*frame, *area) == pytest.approx(box)


# Published BT.709 / BT.601 YUV to RGB terms (V->R, U->G, V->G, U->B), limited and full range.
_BT709_LIMITED = (1.7927, 0.2132, 0.5329, 2.1124)
_BT709_FULL = (1.5748, 0.1873, 0.4681, 1.8556)
_BT601_LIMITED = (1.5960, 0.3918, 0.8130, 2.0172)
_BT601_FULL = (1.4020, 0.3441, 0.7141, 1.7720)
_BT2020_LIMITED = (1.6787, 0.1873, 0.6504, 2.1418)


@pytest.mark.parametrize(
    ("height", "matrix", "terms"),
    [
        (1080, "bt709", _BT709_LIMITED),
        (480, "bt709", _BT709_LIMITED),
        (1080, "bt601", _BT601_LIMITED),
        (1080, "bt2020", _BT2020_LIMITED),
        (1080, "", _BT709_LIMITED),
        (720, "unknown", _BT709_LIMITED),
        (576, "", _BT601_LIMITED),
        (577, "", _BT709_LIMITED),
    ],
    ids=["bt709", "bt709-sd", "bt601", "bt2020", "hd-unnamed", "hd-unknown", "sd-unnamed", "above-sd-unnamed"],
)
def test_the_colour_matrix_follows_the_caps_else_the_frame_size(
    height: int, matrix: str, terms: tuple[float, ...]
) -> None:
    assert yuv_to_rgb(height, matrix).m == pytest.approx(terms, abs=1e-4)


@pytest.mark.parametrize(
    ("matrix", "color_range", "y_black", "y_scale", "terms"),
    [
        ("bt709", "16-235", 16 / 255, 255 / 219, _BT709_LIMITED),
        ("bt709", "unknown", 16 / 255, 255 / 219, _BT709_LIMITED),
        ("bt709", "0-255", 0.0, 1.0, _BT709_FULL),
        ("bt601", "0-255", 0.0, 1.0, _BT601_FULL),
    ],
    ids=["limited", "unknown-is-limited", "full-709", "full-601"],
)
def test_a_full_range_frame_keeps_its_black_and_white(
    matrix: str, color_range: str, y_black: float, y_scale: float, terms: tuple[float, ...]
) -> None:
    colour = yuv_to_rgb(1080, matrix, color_range)
    assert (colour.y_black, colour.y_scale) == pytest.approx((y_black, y_scale))
    assert colour.m == pytest.approx(terms, abs=1e-4)


def test_a_buffer_must_reach_the_end_of_its_furthest_plane() -> None:
    planes = [Plane(4096, 2048, 1920, 1080), Plane(0, 2048, 960, 2)]

    assert gl_display.planes_end(planes) == 4096 + 2048 * 1080


_CAPS_LAYOUT = SimpleNamespace(offset=[0, 2_073_600, 2_592_000, 0], stride=[1920, 960, 960, 0])


@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        (None, ((0, 2_073_600, 2_592_000), (1920, 960, 960))),
        (
            SimpleNamespace(offset=[0, 2_088_960, 2_611_200, 0], stride=[1984, 992, 992, 0]),
            ((0, 2_088_960, 2_611_200), (1984, 992, 992)),
        ),
    ],
    ids=["caps-layout", "padded-meta"],
)
def test_a_frame_is_laid_out_by_its_video_meta_else_by_its_caps(
    meta: SimpleNamespace | None, expected: tuple[tuple[int, ...], tuple[int, ...]]
) -> None:
    assert gl_display.frame_layout(meta, _CAPS_LAYOUT, 3) == expected


@pytest.mark.parametrize(
    ("x", "y", "size", "scale", "expected"),
    [
        (0, 0, (100, 50), (1.0, 1.0), (-1.0, 0.8, -0.8, 1.0)),
        (900, 450, (100, 50), (1.0, 1.0), (0.8, -1.0, 1.0, -0.8)),
        (450, 225, (100, 50), (2.0, 2.0), (0.8, -1.0, 1.0, -0.8)),
    ],
    ids=["top-left", "bottom-right", "hidpi"],
)
def test_a_sprite_lands_where_its_corner_and_size_put_it_in_clip_space(
    x: int, y: int, size: tuple[int, int], scale: tuple[float, float], expected: tuple[float, ...]
) -> None:
    sprite = Sprite(cairo.ImageSurface(cairo.FORMAT_ARGB32, *size), x, y, kept=False)

    assert sprite_quad(sprite, scale, 1000, 500) == pytest.approx(expected)


class _FakeGl:
    """Texture names in creation order, with what each was filled from and which were deleted."""

    def __init__(self) -> None:
        self.made = 0
        self.uploads: list[tuple[int, cairo.ImageSurface]] = []
        self.deleted: list[int] = []

    def new(self) -> int:
        self.made += 1
        return self.made

    def upload(self, tex: int, picture: cairo.ImageSurface) -> None:
        self.uploads.append((tex, picture))

    def delete(self, textures: list[int]) -> None:
        self.deleted.extend(textures)


def _sprite(*, kept: bool, picture: cairo.ImageSurface | None = None) -> Sprite:
    return Sprite(picture or cairo.ImageSurface(cairo.FORMAT_ARGB32, 4, 4), 0, 0, kept=kept)


def _textures() -> tuple[gl_display.SpriteTextures, _FakeGl]:
    gl = _FakeGl()
    return gl_display.SpriteTextures(gl.new, gl.upload, gl.delete), gl


def test_a_kept_picture_is_uploaded_once_for_as_long_as_lists_show_it() -> None:
    textures, gl = _textures()
    grid = _sprite(kept=True)

    textures.update([grid, _sprite(kept=False)])
    textures.update([grid, _sprite(kept=False)])

    assert [tex for tex, picture in gl.uploads if picture is grid.picture] == [1]
    assert textures.drawn[0] == 1
    assert gl.deleted == []


def test_a_kept_picture_no_list_shows_has_its_texture_deleted() -> None:
    textures, gl = _textures()
    textures.update([_sprite(kept=True), _sprite(kept=True)])

    textures.update([_sprite(kept=False)])

    assert gl.deleted == [1, 2]
    assert textures.drawn == [3]


def test_drawn_sprites_reuse_the_last_lists_textures_and_never_a_kept_one() -> None:
    textures, gl = _textures()
    grid = _sprite(kept=True)
    textures.update([_sprite(kept=False), grid, _sprite(kept=False)])

    gl.uploads.clear()
    sprites = [grid, *(_sprite(kept=False) for _ in range(3))]

    textures.update(sprites)

    assert textures.drawn[0] == 2
    assert sorted(textures.drawn[1:]) == [1, 3, 4]
    assert gl.uploads == [(tex, s.picture) for tex, s in zip(textures.drawn[1:], sprites[1:], strict=True)]
    assert gl.deleted == []


def test_textures_a_shorter_list_leaves_over_are_kept_for_the_next() -> None:
    textures, gl = _textures()
    textures.update([_sprite(kept=False) for _ in range(3)])
    textures.update([_sprite(kept=False)])

    textures.update([_sprite(kept=False) for _ in range(3)])

    assert sorted(textures.drawn) == [1, 2, 3]
    assert gl.made == 3


def test_a_kept_picture_listed_twice_shares_one_texture() -> None:
    textures, gl = _textures()
    scrim = _sprite(kept=True)

    textures.update([scrim, _sprite(kept=False), scrim])

    assert textures.drawn == [1, 2, 1]
    assert gl.made == 2


def test_every_texture_held_is_named_once_for_the_context_to_free() -> None:
    textures, _gl = _textures()
    scrim = _sprite(kept=True)
    textures.update([scrim, *(_sprite(kept=False) for _ in range(3)), scrim])
    textures.update([scrim, _sprite(kept=False)])

    assert sorted(textures.held()) == [1, 2, 3, 4]


def test_durations_read_as_count_median_and_99th_percentile() -> None:
    assert gl_display.describe_durations("render", (600, 1.234, 5.0)) == "render 600, p50 1.23 ms p99 5.00 ms"


@pytest.mark.parametrize(
    ("pacing", "line"),
    [
        ((300, 3, 2), "300 pictures, 99.0 % on time, held 2 refreshes"),
        ((0, 0, 1), "0 pictures, 0.0 % on time, held 1 refreshes"),
    ],
    ids=["shown", "none-shown"],
)
def test_hud_pacing_reads_as_pictures_share_on_time_and_refreshes_held(pacing: tuple[int, int, int], line: str) -> None:
    assert gl_display.describe_pacing(*pacing) == line


def test_the_video_cadence_reads_as_frames_shown_skipped_late_waits_and_spread() -> None:
    report = CadenceReport(
        refreshes={3: 1, 2: 3},
        skipped=4,
        late=5,
        wait_ms_p50=1.25,
        wait_ms_p99=9.0,
        spread_ms_p50=2.0,
        spread_ms_p98=7.5,
    )

    assert gl_display.describe_cadence(report) == (
        "4 shown, 75.0 % for two refreshes, refreshes {2: 3, 3: 1}, 4 skipped, 5 late,"
        " wait p50 1.2 ms p99 9.0 ms, arrival spread p50 2.0 ms p98 7.5 ms"
    )


def test_a_report_is_due_every_period_after_the_first_refresh_starts_one() -> None:
    clock = gl_display.ReportClock()
    period = gl_display.REPORT_EVERY_US
    start = 7_000_000_000

    due = [clock.due(now) for now in (0, start, start + period - 1, start + period, start + period + 1)]
    # A display that stopped for a while reports once when it renders again, then a period later.
    due += [clock.due(now) for now in (start + 5 * period, start + 6 * period - 1, start + 6 * period)]

    assert due == [False, False, False, True, False, True, False, True]


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (None, "arrival"),
        ("", "arrival"),
        ("locked\n", "locked"),
        ("capped", "capped"),
        ("smooth", "arrival"),
        (b"\xffcapped", "arrival"),
    ],
    ids=["no-file", "empty", "locked", "capped", "unknown", "not-utf-8"],
)
def test_the_flag_file_picks_how_video_frames_meet_the_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str | bytes | None, expected: str
) -> None:
    flag_file = tmp_path / "cadence"
    if isinstance(content, bytes):
        flag_file.write_bytes(content)
    elif content is not None:
        flag_file.write_text(content)
    monkeypatch.setattr(gl_display, "CADENCE_FLAG", str(flag_file))
    # 30 fps picked at every 60 Hz refresh; a decoder stall releases the last three of every twelve at once.
    frames = [(i * 33_333, (i // 12 * 12 + 11 if i % 12 >= 9 else i) * 33_333 + 10_000) for i in range(300)]
    cadence = gl_display.cadence_for_mode()
    pending = 0
    for k in range(610):
        now = k * 16_667
        while pending < len(frames) and frames[pending][1] <= now:
            cadence.push(pending, *frames[pending])
            pending += 1
        cadence.pick(now, 16_667)

    report = cadence.report()
    waits = {
        "arrival": report.wait_ms_p50 < 10,
        "locked": report.wait_ms_p50 > 60,
        "capped": 15 <= report.wait_ms_p50 <= 40,
    }
    assert waits[expected], (expected, report)


_NONE = 2**64 - 1
_GST = SimpleNamespace(CLOCK_TIME_NONE=_NONE, Format=SimpleNamespace(TIME="time", BYTES="bytes"))


class _Segment:
    """A segment starting at ``start`` ns; no pts and a pts before it have no running time, as in GStreamer."""

    def __init__(self, start: int, base: int = 0, fmt: str = "time") -> None:
        self.format = fmt
        self._start = start
        self._base = base

    def to_running_time(self, fmt: str, pts: int) -> int:
        assert fmt == self.format, "GStreamer logs a critical for a format other than the segment's"
        return _NONE if pts == _NONE or pts < self._start else pts - self._start + self._base


def test_a_frames_timestamp_is_its_running_time_in_microseconds() -> None:
    # A looping clip's second pass: pts start over at the segment start, the running time runs on from the base.
    assert gl_display.running_time_us(_GST, _Segment(start=0, base=10_000_000_000), 40_000_999) == 10_040_000


@pytest.mark.parametrize(
    ("segment", "pts"),
    [
        (_Segment(start=0), _NONE),
        (None, 40_000_000),
        (_Segment(start=0, fmt="bytes"), 40_000_000),
        (_Segment(start=50_000_000), 40_000_000),
    ],
    ids=["no-pts", "no-segment", "segment-in-bytes", "before-the-segment"],
)
def test_a_frame_without_a_running_time_has_no_timestamp(segment: _Segment | None, pts: int) -> None:
    assert gl_display.running_time_us(_GST, segment, pts) is None


@pytest.mark.parametrize(
    ("interval", "expected"),
    [(20_000, 20_000), (0, gl_display.DEFAULT_REFRESH_US)],
    ids=["50-hz", "unknown"],
)
def test_a_render_knows_its_refresh_time_and_interval_from_the_frame_clock(interval: int, expected: int) -> None:
    clock = SimpleNamespace(get_frame_time=lambda: 5_000_000, get_refresh_info=lambda base: (interval, base + 1))

    assert gl_display.refresh_of(clock) == (5_000_000, expected)
