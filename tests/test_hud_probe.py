# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""scripts/hw_validation/hud_probe.py: /proc reading, the hud averages and the command line."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hw_validation" / "hud_probe.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("hud_probe", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["hud_probe"] = module
    spec.loader.exec_module(module)
    return module


probe = _load()


def _stat(ticks_user: int, ticks_sys: int, name: str = "python") -> str:
    # pid (comm) state ... utime is field 14, stime 15 (1-based).
    rest = ["S"] + ["0"] * 10 + [str(ticks_user), str(ticks_sys)] + ["0"] * 5
    return f"123 ({name}) " + " ".join(rest)


def _proc(root: Path, pid: int, total: int, threads: dict[int, tuple[str, int]]) -> None:
    (root / str(pid) / "task").mkdir(parents=True, exist_ok=True)
    (root / str(pid) / "stat").write_text(_stat(total, 0))
    for tid, (name, ticks) in threads.items():
        task = root / str(pid) / "task" / str(tid)
        task.mkdir(exist_ok=True)
        (task / "stat").write_text(_stat(ticks, 0, name))
        (task / "comm").write_text(name + "\n")


# --------------------------------------------------------------------------- #
# /proc
# --------------------------------------------------------------------------- #


def test_cpu_ticks_add_user_and_system_time_whatever_the_name_holds() -> None:
    assert probe.cpu_ticks(_stat(7, 5, name="a (strange) name")) == 12


@pytest.mark.parametrize("content", [None, "garbled", "1 (x) S 1 2"])
def test_an_unreadable_stat_reads_as_nothing(tmp_path: Path, content: str | None) -> None:
    path = tmp_path / "stat"
    if content is not None:
        path.write_text(content)
    assert probe.read_ticks(path) is None


def test_threads_are_read_with_their_names_skipping_one_that_ended(tmp_path: Path) -> None:
    _proc(tmp_path, 10, 0, {10: ("python", 5), 11: ("HudRender", 3)})
    gone = tmp_path / "10" / "task" / "12"
    gone.mkdir()
    no_name = tmp_path / "10" / "task" / "13"
    no_name.mkdir()
    (no_name / "stat").write_text(_stat(1, 0))
    assert probe.thread_ticks(10, tmp_path) == {10: ("python", 5), 11: ("HudRender", 3), 13: ("?", 1)}
    assert probe.thread_ticks(99, tmp_path) == {}


def test_thread_shares_name_the_main_and_render_threads_and_add_up_the_rest_by_name() -> None:
    before = {1: ("python", 0), 2: ("python", 0), 3: ("display_queue", 10)}
    after = {1: ("python", 50), 2: ("python", 30), 3: ("display_queue", 30), 4: ("python", 20)}
    shares = probe.thread_shares(before, after, 1.0, 100, main=1, render=2)
    assert shares == {"main": 50.0, "hud_render": 30.0, "display_queue": 20.0, "python": 20.0}


def _pgrep(monkeypatch: pytest.MonkeyPatch, stdout: str) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=stdout)

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    return calls


def test_the_app_is_the_python_process_not_the_compositor_carrying_its_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The session's cage and its shell carry "python -m openfollow.main" in their own command lines.
    for pid, name in (("822", "cage"), ("824", "sh"), ("830", "python3.13")):
        (tmp_path / pid).mkdir()
        (tmp_path / pid / "comm").write_text(name + "\n")
    calls = _pgrep(monkeypatch, "822\n824\n999\n830\n")
    assert probe.find_pid(proc=tmp_path) == 830
    assert calls == [["pgrep", "-f", probe.APP_PATTERN]]


def test_no_python_process_means_no_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "822").mkdir()
    (tmp_path / "822" / "comm").write_text("cage\n")
    _pgrep(monkeypatch, "822\n")
    assert probe.find_pid(proc=tmp_path) is None


@pytest.mark.parametrize(("stdout", "expected"), [("4321\n99\n", 4321), ("", None)])
def test_the_compositor_is_found_by_name(monkeypatch: pytest.MonkeyPatch, stdout: str, expected: int | None) -> None:
    calls = _pgrep(monkeypatch, stdout)
    assert probe.find_compositor() == expected
    assert calls == [["pgrep", "-x", "cage"]]


# --------------------------------------------------------------------------- #
# /api/stats
# --------------------------------------------------------------------------- #


class _Opener:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.requests: list[Any] = []

    def open(self, request: Any, timeout: float) -> io.BytesIO:
        self.requests.append(request)
        return io.BytesIO(json.dumps(self.payload).encode())


@pytest.mark.parametrize(("pin", "logins"), [("0000", 1), ("", 0)])
def test_the_stats_reader_logs_in_only_with_a_pin(monkeypatch: pytest.MonkeyPatch, pin: str, logins: int) -> None:
    opener = _Opener({"hud": {"picture_ms": 5.0}})
    monkeypatch.setattr(probe.urllib.request, "build_opener", lambda *handlers: opener)
    read = probe.stats_reader("http://127.0.0.1", pin)
    assert read() == {"hud": {"picture_ms": 5.0}}
    posted = [r for r in opener.requests if not isinstance(r, str)]
    assert len(posted) == logins
    if logins:
        assert posted[0].full_url == "http://127.0.0.1/login"
        assert posted[0].data == b"pin=0000"
    assert opener.requests[-1] == "http://127.0.0.1/api/stats"


def test_hud_windows_are_averaged_once_each_with_refresh_counts_added_up() -> None:
    first = {"picture_ms": 4.0, "picture_refreshes": {"2": 30, "3": 1}, "render_thread_id": 7, "note": "x"}
    second = {"picture_ms": 6.0, "picture_refreshes": {"10": 1, "2": 29}, "render_thread_id": 7, "note": "y"}
    # The same window read twice counts once; a sample without figures is skipped.
    out = probe.average_hud([None, first, first, second, {}])
    assert out == {
        "windows": 2,
        "picture_ms": 5.0,
        "picture_refreshes": {"2": 59, "3": 1, "10": 1},
        "render_thread_id": 7,
    }


def test_no_hud_windows_average_to_nothing() -> None:
    assert probe.average_hud([None, {}]) is None


@pytest.mark.parametrize(("counts", "expected"), [({"2": 3, "3": 1}, 0.75), ({}, None)])
def test_on_time_is_the_share_held_exactly_the_interval(counts: dict[str, int], expected: float | None) -> None:
    assert probe.on_time(counts, 2) == expected


# --------------------------------------------------------------------------- #
# A run and the command line
# --------------------------------------------------------------------------- #


class _Time:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def test_a_run_reports_process_compositor_threads_and_the_hud(tmp_path: Path) -> None:
    _proc(tmp_path, 10, 100, {10: ("python", 40), 11: ("python", 20)})
    _proc(tmp_path, 20, 50, {})
    hud = {"picture_ms": 8.0, "interval_refreshes": 2, "picture_refreshes": {"2": 9, "3": 1}, "render_thread_id": 11}
    clock = _Time()

    def read_stats() -> dict[str, Any]:
        if clock.t >= 1.0:
            # The station spent CPU meanwhile.
            _proc(tmp_path, 10, 300, {10: ("python", 140), 11: ("python", 120)})
            _proc(tmp_path, 20, 70, {})
        return {"hud": hud}

    result = probe.measure(10, 20, read_stats, 2.0, hz=100, proc=tmp_path, sleep=clock.sleep, now=clock.now)
    assert result["process_pct"] == pytest.approx(100.0)
    assert result["compositor_pct"] == pytest.approx(10.0)
    assert result["threads"] == {"main": 50.0, "hud_render": 50.0}
    assert result["hud"]["picture_ms"] == 8.0
    assert result["pictures_on_time"] == 0.9


def test_a_run_without_a_compositor_or_hud_reports_none_for_them(tmp_path: Path) -> None:
    _proc(tmp_path, 10, 100, {10: ("python", 40)})
    clock = _Time()
    result = probe.measure(10, None, lambda: {}, 1.0, hz=100, proc=tmp_path, sleep=clock.sleep, now=clock.now)
    assert result["compositor_pct"] is None
    assert result["hud"] is None
    assert "pictures_on_time" not in result


@pytest.mark.parametrize("argv", [["--window", "0"], ["--runs", "0"]])
def test_a_bad_window_or_run_count_is_refused(argv: list[str]) -> None:
    assert probe.main(argv) == 2


def test_no_openfollow_process_is_reported(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(probe, "find_pid", lambda pattern=probe.APP_PATTERN: None)
    assert probe.main([]) == 2
    assert "no OpenFollow process" in capsys.readouterr().err


def _run_main(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], hud: dict[str, Any] | None
) -> tuple[int, list[tuple[Any, ...]]]:
    seen: list[tuple[Any, ...]] = []
    monkeypatch.setattr(probe, "find_compositor", lambda: 20)

    def reader(url: str, pin: str) -> Any:
        seen.append(("reader", url, pin))
        return lambda: {}

    def run(pid: int, compositor: int | None, read_stats: Any, window: float, *, hz: int) -> dict[str, Any]:
        seen.append(("run", pid, compositor, window))
        return {"hud": hud}

    return probe.main(argv, reader=reader, run=run), seen


def test_each_run_prints_one_line_and_appends_it_to_the_out_file(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    out = tmp_path / "runs.jsonl"
    argv = ["--pid", "10", "--runs", "2", "--window", "5", "--label", "before", "--pin", "0303", "--out", str(out)]
    code, seen = _run_main(monkeypatch, argv, {"picture_ms": 5.0})
    assert code == 0
    assert seen == [("reader", "http://127.0.0.1", "0303"), ("run", 10, 20, 5.0), ("run", 10, 20, 5.0)]
    printed = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert printed == [{"label": "before", "hud": {"picture_ms": 5.0}}] * 2
    assert [json.loads(line) for line in out.read_text().splitlines()] == printed


def test_a_station_whose_operator_screen_reports_nothing_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _ = _run_main(monkeypatch, ["--pid", "10"], None)
    assert code == 2
    assert "reported nothing" in capsys.readouterr().err
