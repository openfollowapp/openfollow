# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Checks for scripts/hw_validation/osc_marker_paths.py: wire encoding that a
real OSC receiver accepts, path geometry, and the marker spec parser."""

from __future__ import annotations

import importlib.util
import inspect
import itertools
import math
import random
import sys
from pathlib import Path
from types import ModuleType

import pytest
from pythonosc.osc_message import OscMessage

pytestmark = pytest.mark.unit


def _load() -> ModuleType:
    source = inspect.getsourcefile(_load)
    assert source, "Could not resolve current test source path"
    script = Path(source).resolve().parents[1] / "scripts" / "hw_validation" / "osc_marker_paths.py"
    spec = importlib.util.spec_from_file_location("osc_marker_paths", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: @dataclass resolves the module's postponed
    # annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


drive = _load()


# --------------------------------------------------------------------------- #
# Wire encoding – parsed by the same library the app listens with
# --------------------------------------------------------------------------- #


class TestOscEncoding:
    @pytest.mark.parametrize(
        "address",
        [
            "/marker/1",  # 9 bytes – unaligned
            "/marker/301",  # 11 bytes – terminator lands on the boundary
            "/marker/3010",  # 12 bytes – already a multiple of 4
            "/marker/301/x",
        ],
    )
    def test_address_lengths_round_trip_through_a_real_parser(self, address: str) -> None:
        """A string whose length is already a multiple of 4 still needs exactly
        one terminator plus padding – an extra word is read as the type tag and
        the message is silently dropped."""
        raw = drive.osc_message(address, 1.5, -2.25, 0.75)

        parsed = OscMessage(raw)

        assert parsed.address == address
        assert [round(v, 3) for v in parsed.params] == [1.5, -2.25, 0.75]

    def test_every_encoded_message_is_four_byte_aligned(self) -> None:
        assert len(drive.osc_message("/marker/12", 0.0, 0.0, 0.0)) % 4 == 0

    def test_single_argument_message_round_trips(self) -> None:
        parsed = OscMessage(drive.osc_message("/marker/7/z", 2.5))

        assert parsed.address == "/marker/7/z"
        assert [round(v, 3) for v in parsed.params] == [2.5]

    def test_axis_mode_sends_one_message_per_axis(self) -> None:
        payloads = drive.messages_for(4, (1.0, 2.0, 3.0), axis_mode="axis")

        parsed = [OscMessage(p) for p in payloads]
        assert [m.address for m in parsed] == ["/marker/4/x", "/marker/4/y", "/marker/4/z"]
        assert [round(m.params[0], 3) for m in parsed] == [1.0, 2.0, 3.0]

    def test_triple_mode_sends_one_message_with_three_arguments(self) -> None:
        payloads = drive.messages_for(4, (1.0, 2.0, 3.0), axis_mode="triple")

        assert len(payloads) == 1
        parsed = OscMessage(payloads[0])
        assert parsed.address == "/marker/4"
        assert [round(v, 3) for v in parsed.params] == [1.0, 2.0, 3.0]


# --------------------------------------------------------------------------- #
# Path geometry
# --------------------------------------------------------------------------- #


def _params(**overrides: float) -> object:
    base = {"centre_x": 0.0, "centre_y": 0.0, "size": 3.0, "period_s": 8.0}
    base.update(overrides)
    return drive.PathParams(**base)


class TestPathGeometry:
    def test_circle_holds_its_radius_all_the_way_round(self) -> None:
        path = drive.CirclePath(_params())

        radii = [math.dist((0.0, 0.0), path.plane(t)) for t in [0.0, 1.0, 2.5, 4.0, 6.3, 8.0]]

        assert all(r == pytest.approx(3.0) for r in radii)

    def test_circle_honours_a_shifted_centre(self) -> None:
        path = drive.CirclePath(_params(centre_x=10.0, centre_y=-4.0))

        for t in (0.0, 2.0, 5.5):
            assert math.dist((10.0, -4.0), path.plane(t)) == pytest.approx(3.0)

    def test_figure_eight_returns_to_the_centre_mid_lap(self) -> None:
        """The crossing is what makes it a figure eight rather than a loop."""
        path = drive.Figure8Path(_params())

        x, y = path.plane(4.0)  # half a lap

        assert (x, y) == (pytest.approx(0.0, abs=1e-9), pytest.approx(0.0, abs=1e-9))

    def test_line_sweeps_the_full_span_and_reverses_at_each_end(self) -> None:
        path = drive.LinePath(_params())

        xs = [path.plane(t)[0] for t in [i * 0.05 for i in range(240)]]  # 1.5 laps

        assert min(xs) == pytest.approx(-3.0, abs=0.05)
        assert max(xs) == pytest.approx(3.0, abs=0.05)
        assert all(abs(x) <= 3.0 + 1e-9 for x in xs)
        deltas = [b - a for a, b in itertools.pairwise(xs)]
        flips = sum(1 for a, b in itertools.pairwise(deltas) if a * b < 0)
        assert flips == 2, "expected one reversal at each end of the sweep"

    def test_line_stays_on_its_centre_axis(self) -> None:
        path = drive.LinePath(_params(centre_y=2.0))

        assert {round(path.plane(t)[1], 6) for t in (0.0, 1.0, 3.0, 7.0)} == {2.0}

    def test_square_stays_on_its_perimeter(self) -> None:
        path = drive.SquarePath(_params())

        for t in [i * 0.05 for i in range(160)]:
            x, y = path.plane(t)
            assert max(abs(x), abs(y)) == pytest.approx(3.0), f"left the perimeter at t={t}"

    def test_spiral_grows_from_the_centre_to_the_full_size(self) -> None:
        path = drive.SpiralPath(_params())

        radii = [math.dist((0.0, 0.0), path.plane(t)) for t in (0.0, 2.0, 4.0, 6.0)]

        assert radii == sorted(radii)
        assert radii[0] == pytest.approx(0.0)
        assert math.dist((0.0, 0.0), path.plane(7.99)) == pytest.approx(3.0, abs=0.02)

    def test_static_path_never_moves(self) -> None:
        path = drive.StaticPath(_params(centre_x=1.0, centre_y=2.0))

        assert {path.plane(t) for t in (0.0, 3.0, 11.0)} == {(1.0, 2.0)}

    def test_random_walk_stays_inside_its_box_and_is_seed_reproducible(self) -> None:
        def walk(seed: int) -> list[tuple[float, float]]:
            path = drive.RandomWalkPath(_params(), rng=random.Random(seed))
            return [path.plane(t) for t in [i * 0.1 for i in range(200)]]

        first, again, different = walk(7), walk(7), walk(8)

        assert first == again, "a seeded run must be reproducible"
        assert first != different
        assert all(abs(x) <= 3.0 + 1e-9 and abs(y) <= 3.0 + 1e-9 for x, y in first)


class TestHeight:
    def test_equal_bounds_hold_a_constant_height(self) -> None:
        params = _params(z_low=1.6, z_high=1.6)

        assert {params.height(t, 0.0) for t in (0.0, 1.0, 4.0)} == {1.6}

    def test_a_range_bobs_within_its_bounds_and_uses_both_halves(self) -> None:
        params = _params(z_low=0.5, z_high=2.5, z_period_s=4.0)

        heights = [params.height(t, 0.0) for t in [i * 0.1 for i in range(40)]]

        assert min(heights) == pytest.approx(0.5, abs=0.01)
        assert max(heights) == pytest.approx(2.5, abs=0.01)

    def test_sample_combines_the_plane_and_the_height(self) -> None:
        path = drive.CirclePath(_params(z_low=2.0, z_high=2.0))

        x, y, z = path.sample(0.0)

        assert (x, y) == path.plane(0.0)
        assert z == 2.0


# --------------------------------------------------------------------------- #
# Marker spec
# --------------------------------------------------------------------------- #


class TestMarkerSpec:
    def test_bare_ids_take_the_default_path(self) -> None:
        assert drive.parse_marker_spec("1,2", "line") == [(1, "line"), (2, "line")]

    def test_per_group_paths_override_the_default(self) -> None:
        assert drive.parse_marker_spec("1:circle,2", "line") == [(1, "circle"), (2, "line")]

    def test_ranges_are_inclusive(self) -> None:
        assert drive.parse_marker_spec("301-304:square", "circle") == [
            (301, "square"),
            (302, "square"),
            (303, "square"),
            (304, "square"),
        ]

    def test_whitespace_and_empty_tokens_are_tolerated(self) -> None:
        assert drive.parse_marker_spec(" 1 , , 2:circle ", "line") == [(1, "line"), (2, "circle")]

    @pytest.mark.parametrize(
        ("spec", "fragment"),
        [
            ("1:orbit", "unknown path"),
            ("abc", "must be an integer"),
            ("5-2", "runs backwards"),
            ("", "no markers"),
            ("   ", "no markers"),
        ],
    )
    def test_bad_input_names_the_offending_token(self, spec: str, fragment: str) -> None:
        with pytest.raises(ValueError, match=fragment):
            drive.parse_marker_spec(spec, "circle")

    def test_every_registered_path_is_selectable_by_name(self) -> None:
        for name in drive.PATHS:
            assert drive.parse_marker_spec(f"1:{name}", "circle") == [(1, name)]
            assert drive.build_path(name, _params(), 0.0, random.Random(0)).sample(1.0)


class TestPhaseSpread:
    def test_markers_sharing_a_path_are_spread_around_the_period(self) -> None:
        spread = drive.assign_phases([(1, "circle"), (2, "circle"), (3, "circle"), (4, "circle")])

        assert [phase for _id, _path, phase in spread] == [0.0, 0.25, 0.5, 0.75]

    def test_each_path_is_spread_independently(self) -> None:
        spread = drive.assign_phases([(1, "circle"), (2, "line"), (3, "circle")])

        assert spread == [(1, "circle", 0.0), (2, "line", 0.0), (3, "circle", 0.5)]

    def test_spread_can_be_disabled(self) -> None:
        spread = drive.assign_phases([(1, "circle"), (2, "circle")], spread=False)

        assert [phase for _id, _path, phase in spread] == [0.0, 0.0]

    def test_spread_markers_do_not_occupy_the_same_point(self) -> None:
        spread = drive.assign_phases([(1, "circle"), (2, "circle")])
        points = {drive.build_path(path, _params(), phase).plane(0.0) for _id, path, phase in spread}

        assert len(points) == 2


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


class TestCli:
    def test_dry_run_sends_no_packets(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
        def explode(*_a: object, **_kw: object) -> None:
            raise AssertionError("--dry-run must not open a socket")

        monkeypatch.setattr(drive.socket, "socket", explode)

        assert drive.main(["--markers", "1-2", "--duration", "0.05", "--rate", "20", "--dry-run"]) == 0
        assert "DRY RUN" in capsys.readouterr().out

    def test_run_sends_one_datagram_per_marker_per_tick(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sent: list[tuple[bytes, tuple[str, int]]] = []

        class _FakeSocket:
            def __init__(self, *_a: object, **_kw: object) -> None:
                pass

            def sendto(self, payload: bytes, dest: tuple[str, int]) -> None:
                sent.append((payload, dest))

            def close(self) -> None:
                pass

        monkeypatch.setattr(drive.socket, "socket", _FakeSocket)

        rc = drive.main(
            ["--host", "10.0.0.9", "--markers", "301-303", "--duration", "0.2", "--rate", "10", "--period", "4"]
        )

        assert rc == 0
        assert sent, "expected datagrams"
        assert {dest for _p, dest in sent} == {("10.0.0.9", 8765)}
        addresses = {OscMessage(p).address for p, _d in sent}
        assert addresses == {"/marker/301", "/marker/302", "/marker/303"}
        assert len(sent) % 3 == 0, "each tick must cover every marker"

    def test_park_sends_a_final_centre_position(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sent: list[bytes] = []

        class _FakeSocket:
            def __init__(self, *_a: object, **_kw: object) -> None:
                pass

            def sendto(self, payload: bytes, _dest: tuple[str, int]) -> None:
                sent.append(payload)

            def close(self) -> None:
                pass

        monkeypatch.setattr(drive.socket, "socket", _FakeSocket)

        drive.main(
            [
                "--markers", "1",
                "--duration", "0.1", "--rate", "10",
                "--centre", "2,3", "--z-range", "1.2,1.2",
                "--park",
            ]
        )  # fmt: skip

        final = OscMessage(sent[-1])
        assert [round(v, 3) for v in final.params] == [2.0, 3.0, 1.2]

    @pytest.mark.parametrize(
        "argv",
        [
            ["--rate", "0"],
            ["--rate", "-1"],
            ["--period", "0"],
            ["--z-period", "0"],
        ],
    )
    def test_non_positive_timing_is_rejected(self, argv: list[str], capsys: pytest.CaptureFixture) -> None:
        assert drive.main([*argv, "--dry-run", "--duration", "0.01"]) == 2
        assert "must be positive" in capsys.readouterr().err

    def test_bad_marker_spec_exits_two(self, capsys: pytest.CaptureFixture) -> None:
        assert drive.main(["--markers", "nope", "--dry-run"]) == 2
        assert "must be an integer" in capsys.readouterr().err

    @pytest.mark.parametrize("value", ["1", "1,2,3", "a,b"])
    def test_pair_arguments_reject_malformed_input(self, value: str) -> None:
        with pytest.raises(SystemExit):
            drive.build_parser().parse_args(["--centre", value])
