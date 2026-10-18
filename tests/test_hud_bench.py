# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""scripts/hw_validation/hud_bench.py: timing, the A/B verdict and the worker protocol."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hw_validation" / "hud_bench.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("hud_bench", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered first: @dataclass resolves annotations through sys.modules.
    sys.modules["hud_bench"] = module
    spec.loader.exec_module(module)
    return module


bench = _load()


@pytest.fixture(autouse=True)
def _restore_import_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """``main`` and ``worker`` put a checkout on ``sys.path`` and register the scenes module."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    sys.modules.pop("_hud_scenes", None)
    yield
    sys.modules.pop("_hud_scenes", None)


class _Clock:
    """Advances by ``step`` seconds on each read."""

    def __init__(self, step: float) -> None:
        self.t = 0.0
        self.step = step

    def __call__(self) -> float:
        self.t += self.step
        return self.t


class _FakeCairo:
    OPERATOR_CLEAR = "clear"

    class Context:
        def __init__(self, surface: Any) -> None:
            self.surface = surface

        def set_operator(self, op: Any) -> None:
            pass

        def paint(self) -> None:
            self.surface.append("clear")


class _FakeWorker:
    """Answers each run with the next of ``ms``; records what it was asked."""

    def __init__(
        self,
        name: str,
        ms: list[float],
        log: list[tuple[str, str, bool]],
        fail: bool = False,
        errors: bool = False,
        dropped: list[str] | None = None,
    ) -> None:
        self.name = name
        self.ms = list(ms)
        self.log = log
        self.fail = fail
        self.errors = ["Cairo overlay draw error: boom"] if errors else []
        self.dropped = dropped or []
        self.closed = False

    def run(self, scene: str, frames: int, split: bool = False) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError(f"the worker for {self.name} stopped")
        self.log.append((self.name, scene, split))
        return {
            "ms": self.ms.pop(0) if self.ms else 1.0,
            "split": {"markers": 0.5} if split else {},
            "errors": self.errors,
            "dropped": self.dropped,
        }

    def close(self) -> None:
        self.closed = True


# --------------------------------------------------------------------------- #
# Timing
# --------------------------------------------------------------------------- #


def test_a_round_is_cpu_ms_per_picture_with_the_clear_counted() -> None:
    drawn: list[Any] = []
    surface: list[str] = []
    frames = [(1920, 1080, "a"), (1920, 1080, "b")]
    ms = bench.time_round(lambda cr, w, h, s: drawn.append(s), surface, frames, _Clock(0.002), _FakeCairo)
    # Two clock reads per frame, 2 ms apart.
    assert ms == pytest.approx(2.0)
    assert drawn == ["a", "b"]
    assert surface == ["clear", "clear"]


def test_the_split_wraps_the_passes_and_puts_them_back() -> None:
    import openfollow.runtime.overlay_draw_hud as hud

    original = hud.draw_system_stats
    split = bench.Split(_Clock(0.001))
    split.install()
    try:
        assert hud.draw_system_stats is not original
    finally:
        split.remove()
    assert hud.draw_system_stats is original


def test_the_split_times_a_wrapped_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    import openfollow.runtime.overlay_draw_hud as hud

    monkeypatch.setattr(bench, "SPLIT_PASSES", (("openfollow.runtime.overlay_draw_hud", "draw_system_stats", "stats"),))
    monkeypatch.setattr(hud, "draw_system_stats", lambda x: x * 2)
    split = bench.Split(_Clock(0.001))
    split.install()
    try:
        wrapped: Any = hud.draw_system_stats
        assert wrapped(21) == 42
    finally:
        split.remove()
    assert split.ms == {"stats": pytest.approx(1.0)}


def test_the_split_skips_a_pass_the_checkout_does_not_have(monkeypatch: pytest.MonkeyPatch) -> None:
    import openfollow.runtime.overlay_draw_hud as hud

    original = hud.draw_system_stats
    monkeypatch.setattr(
        bench,
        "SPLIT_PASSES",
        (
            ("openfollow.video.overlay", "NoSuchClass.draw", "gone"),
            ("openfollow.runtime.overlay_draw_hud", "draw_system_stats", "stats"),
        ),
    )
    split = bench.Split(_Clock(0.001))
    split.install()
    try:
        assert hud.draw_system_stats is not original
    finally:
        split.remove()
    assert hud.draw_system_stats is original


# --------------------------------------------------------------------------- #
# Measuring and judging
# --------------------------------------------------------------------------- #


def test_rounds_alternate_which_side_goes_first_and_the_warm_up_is_not_counted() -> None:
    log: list[tuple[str, str, bool]] = []
    head = _FakeWorker("head", [9.0, 1.0, 2.0, 3.0], log)
    base = _FakeWorker("base", [9.0, 1.0, 2.0, 3.0], log)
    (result,) = bench.measure(["full"], 10, 3, head, base, split=False)
    assert [name for name, _, _ in log] == ["head", "base", "head", "base", "base", "head", "head", "base"]
    assert result.head_ms == [1.0, 2.0, 3.0]
    assert result.base_ms == [1.0, 2.0, 3.0]


def test_split_runs_once_more_on_this_checkout_only() -> None:
    log: list[tuple[str, str, bool]] = []
    head = _FakeWorker("head", [], log)
    (result,) = bench.measure(["light"], 10, 1, head, None, split=True)
    assert log[-1] == ("head", "light", True)
    assert result.split == {"markers": 0.5}


@pytest.mark.parametrize(
    ("head", "base", "expected"),
    [([1.15], [1.0], 0), ([1.16], [1.0], 1), ([0.5], [1.0], 0)],
)
def test_the_verdict_fails_only_beyond_the_allowed_slowdown(
    head: list[float], base: list[float], expected: int
) -> None:
    result = bench.SceneResult("full", head_ms=head, base_ms=base)
    assert bench.verdict([result], 0.15) == expected


def test_the_report_names_the_scene_fields_the_base_lacks() -> None:
    log: list[tuple[str, str, bool]] = []
    head = _FakeWorker("head", [], log)
    base = _FakeWorker("base", [], log, dropped=["OverlayState.show_paths"])
    (result,) = bench.measure(["full"], 10, 1, head, base, split=False)
    assert result.base_lacks == ["OverlayState.show_paths"]
    assert bench.report([result], 0.15)[1].split() == ["base", "lacks", "OverlayState.show_paths"]


def test_a_frame_the_renderer_could_not_draw_stops_the_run() -> None:
    log: list[tuple[str, str, bool]] = []
    head = _FakeWorker("head", [], log)
    base = _FakeWorker("base", [], log, errors=True)
    with pytest.raises(RuntimeError, match="draw error: boom"):
        bench.measure(["light"], 10, 1, head, base, split=False)


def test_one_slower_scene_fails_the_comparison() -> None:
    results = [
        bench.SceneResult("light", head_ms=[1.0], base_ms=[1.0]),
        bench.SceneResult("full", head_ms=[2.0], base_ms=[1.0]),
    ]
    assert bench.verdict(results, 0.15) == 1


def test_without_a_base_nothing_is_judged() -> None:
    result = bench.SceneResult("full", head_ms=[3.0, 1.0, 2.0])
    assert result.head == 2.0
    assert result.base is None
    assert result.slowdown is None
    assert bench.verdict([result], 0.15) == 0


def test_the_report_flags_a_slower_scene_and_lists_the_split() -> None:
    results = [
        bench.SceneResult("full", head_ms=[2.0], base_ms=[1.0], split={"markers": 0.5, "zones": 0.25}),
        bench.SceneResult("light", head_ms=[1.0]),
    ]
    lines = bench.report(results, 0.15)
    assert "SLOWER" in lines[0] and "+100.0%" in lines[0]
    assert lines[1].split() == ["markers", "0.50", "ms"]
    assert lines[2].split() == ["zones", "0.25", "ms"]
    assert lines[3].startswith("light") and "base" not in lines[3]


def test_the_report_survives_a_base_that_took_no_time() -> None:
    (line,) = bench.report([bench.SceneResult("full", head_ms=[1.0], base_ms=[0.0])], 0.15)
    assert line.startswith("full") and "SLOWER" not in line


# --------------------------------------------------------------------------- #
# The command line
# --------------------------------------------------------------------------- #


def _main(
    argv: list[str], ms: dict[str, list[float]] | None = None, fail: str = "", no_start: str = ""
) -> tuple[int, list[Any]]:
    workers: list[Any] = []
    log: list[tuple[str, str, bool]] = []

    def make(root: Path) -> Any:
        name = "head" if root == bench.REPO else "base"
        if name == no_start:
            raise FileNotFoundError(f"the worker for {name} did not start")
        worker = _FakeWorker(name, (ms or {}).get(name, []), log, fail=name == fail)
        workers.append(worker)
        return worker

    return bench.main(argv, make_worker=make), workers


def test_an_unknown_scene_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    code, workers = _main(["--scenes", "full,nope"])
    assert code == 2
    assert workers == []
    assert "nope" in capsys.readouterr().err


@pytest.mark.parametrize("arg", [["--frames", "0"], ["--rounds", "0"]])
def test_zero_frames_or_rounds_are_refused(arg: list[str]) -> None:
    assert _main(arg)[0] == 2


@pytest.mark.parametrize("value", ["-0.1", "nan"])
def test_a_negative_or_nan_slowdown_is_refused(value: str, capsys: pytest.CaptureFixture[str]) -> None:
    code, workers = _main(["--max-slowdown", value])
    assert code == 2
    assert workers == []
    assert "--max-slowdown" in capsys.readouterr().err


def test_a_compare_directory_without_the_package_is_refused(tmp_path: Path) -> None:
    assert _main(["--compare", str(tmp_path)])[0] == 2


def test_a_side_that_stops_is_reported_and_both_workers_are_closed(tmp_path: Path) -> None:
    (tmp_path / "openfollow").mkdir()
    code, workers = _main(["--scenes", "light", "--compare", str(tmp_path)], fail="base")
    assert code == 2
    assert all(w.closed for w in workers)


def test_a_side_that_does_not_start_closes_the_other(tmp_path: Path) -> None:
    (tmp_path / "openfollow").mkdir()
    code, workers = _main(["--scenes", "light", "--compare", str(tmp_path)], no_start="base")
    assert code == 2
    assert [w.closed for w in workers] == [True]


def test_a_worker_of_this_checkout_that_does_not_start_is_reported(capsys: pytest.CaptureFixture[str]) -> None:
    code, workers = _main(["--scenes", "light"], no_start="head")
    assert code == 2
    assert workers == []
    assert "did not start" in capsys.readouterr().err


def test_a_slower_checkout_fails_the_comparison(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "openfollow").mkdir()
    code, _ = _main(
        ["--scenes", "light", "--rounds", "1", "--compare", str(tmp_path)],
        ms={"head": [0.0, 2.0], "base": [0.0, 1.0]},
    )
    assert code == 1
    assert "SLOWER" in capsys.readouterr().out


def test_without_compare_the_run_succeeds_and_json_has_one_line_per_scene(capsys: pytest.CaptureFixture[str]) -> None:
    code, _ = _main(["--scenes", "light,full", "--rounds", "1", "--json"])
    assert code == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [r["scene"] for r in rows] == ["light", "full"]
    assert all(r["base_ms"] is None for r in rows)


def test_every_scene_runs_when_none_is_named() -> None:
    _, workers = _main(["--rounds", "1"])
    scenes = {scene for _, scene, _ in workers[0].log}
    assert scenes == set(bench.load_scenes().SCENES)


def test_a_worker_answers_each_request_with_one_line() -> None:
    stdin = io.StringIO(
        json.dumps({"scene": "light", "frames": 2})
        + "\n"
        + json.dumps({"scene": "light", "frames": 2, "split": True})
        + "\n"
    )
    stdout = io.StringIO()
    assert bench.worker(bench.REPO, stdin=stdin, stdout=stdout, clock=_Clock(0.001)) == 0
    plain, split = (json.loads(line) for line in stdout.getvalue().splitlines())
    # Two clock reads per frame, 1 ms apart.
    assert plain == {"ms": pytest.approx(1.0), "split": {}, "errors": [], "dropped": []}
    assert "markers" in split["split"]


def test_a_worker_names_only_the_fields_the_scene_itself_sets(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests._hud_scenes import overlay_state_without

    older = overlay_state_without("show_paths")
    load = bench.load_scenes

    def load_against_older() -> Any:
        scenes = load()
        scenes.OverlayState = older
        return scenes

    monkeypatch.setattr(bench, "load_scenes", load_against_older)
    stdin = io.StringIO("".join(json.dumps({"scene": s, "frames": 1}) + "\n" for s in ("full", "light", "full")))
    stdout = io.StringIO()
    bench.worker(bench.REPO, stdin=stdin, stdout=stdout, clock=_Clock(0.001))
    dropped = [json.loads(line)["dropped"] for line in stdout.getvalue().splitlines()]
    assert dropped == [["OverlayState.show_paths"], [], ["OverlayState.show_paths"]]


def test_every_split_pass_is_timed_in_some_scene() -> None:
    stdin = io.StringIO("".join(json.dumps({"scene": s, "frames": 1, "split": True}) + "\n" for s in ("full", "menu")))
    stdout = io.StringIO()
    bench.worker(bench.REPO, stdin=stdin, stdout=stdout, clock=_Clock(0.001))
    timed = {label for line in stdout.getvalue().splitlines() for label in json.loads(line)["split"]}
    assert timed == {label for _module, _path, label in bench.SPLIT_PASSES}


def test_a_worker_reports_a_frame_the_renderer_could_not_draw(monkeypatch: pytest.MonkeyPatch) -> None:
    import openfollow.video.overlay as overlay

    def broken(*_args: Any) -> None:
        raise ValueError("boom")

    monkeypatch.setattr(overlay, "draw_paths_pass", broken)
    stdout = io.StringIO()
    bench.worker(
        bench.REPO,
        stdin=io.StringIO(json.dumps({"scene": "light", "frames": 1}) + "\n"),
        stdout=stdout,
        clock=_Clock(0.001),
    )
    assert json.loads(stdout.getvalue())["errors"] == ["Cairo overlay draw error: boom"]


class _DeadPipe:
    """A pipe whose reader has exited."""

    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        raise BrokenPipeError

    def close(self) -> None:
        raise BrokenPipeError


class _Proc:
    def __init__(self, stdin: Any, stdout: str) -> None:
        self.stdin = stdin
        self.stdout = io.StringIO(stdout)
        self.waited = False

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        return 0


def _worker_with(proc: _Proc) -> Any:
    worker = bench.Worker.__new__(bench.Worker)
    worker.root = Path("/base")
    worker.proc = proc
    return worker


@pytest.mark.parametrize(
    ("stdin", "stdout"),
    [(_DeadPipe(), ""), (io.StringIO(), ""), (io.StringIO(), "Traceback (most recent call last):\n")],
    ids=["broken-pipe", "eof", "not-json"],
)
def test_a_worker_that_stopped_or_garbled_its_answer_is_a_runtime_error(stdin: Any, stdout: str) -> None:
    with pytest.raises(RuntimeError, match="/base"):
        _worker_with(_Proc(stdin, stdout)).run("light", 1)


def test_closing_a_worker_whose_pipe_is_gone_still_waits_for_it() -> None:
    proc = _Proc(_DeadPipe(), "")
    _worker_with(proc).close()
    assert proc.waited


class _HungProc(_Proc):
    """A worker that does not exit until killed."""

    def __init__(self) -> None:
        super().__init__(io.StringIO(), "")
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        if timeout is not None and not self.killed:
            raise subprocess.TimeoutExpired("worker", timeout)
        return super().wait(timeout)

    def kill(self) -> None:
        self.killed = True


def test_closing_a_worker_that_does_not_exit_kills_it() -> None:
    proc = _HungProc()
    _worker_with(proc).close()
    assert proc.killed and proc.waited


def test_the_worker_flag_runs_a_worker_for_that_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    roots: list[Path] = []

    def worker(root: Path) -> int:
        roots.append(root)
        return 0

    monkeypatch.setattr(bench, "worker", worker)
    assert bench.main(["--worker", "/base"]) == 0
    assert roots == [Path("/base")]


def test_a_worker_process_answers_a_round_over_its_pipes() -> None:
    worker = bench.Worker(bench.REPO)
    try:
        reply = worker.run("light", 1)
    finally:
        worker.close()
    assert reply["errors"] == [] and reply["dropped"] == [] and reply["split"] == {}
    assert isinstance(reply["ms"], float)
    assert worker.proc.returncode == 0
