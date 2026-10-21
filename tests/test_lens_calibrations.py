# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Lens calibrations saved on the station, one JSON file each beside config.toml."""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

import pytest

from openfollow.web import lens_calibrations as lc

pytestmark = pytest.mark.unit

DAY = datetime.date(2026, 10, 21)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(lc, "_reported", set())
    return lc.calibrations_dir(tmp_path / "config.toml")


def _files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.json"))


def test_the_store_sits_beside_the_config_file(tmp_path: Path) -> None:
    assert lc.calibrations_dir(tmp_path / "config.toml") == tmp_path / "lens-calibrations"


def test_a_station_without_saved_calibrations_lists_none(store: Path) -> None:
    assert not store.exists()
    assert lc.list_calibrations(store) == []


def test_a_saved_calibration_lists_with_the_version_that_wrote_it(store: Path) -> None:
    saved = lc.save_calibration(store, "  Zoom wide  ", -0.2, 0.03, "0.6.0", DAY)
    assert saved == lc.LensCalibration("Zoom wide", -0.2, 0.03, "2026-10-21", "0.6.0")
    assert lc.list_calibrations(store) == [saved]
    (path,) = _files(store)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "name": "Zoom wide",
        "k1": -0.2,
        "k2": 0.03,
        "saved": "2026-10-21",
        "openfollow_version": "0.6.0",
    }
    assert [p.name for p in store.iterdir()] == [path.name]  # no temporary file left behind


def test_saving_a_name_again_ignoring_case_replaces_it(store: Path) -> None:
    lc.save_calibration(store, "Zoom Wide", -0.2, 0.03, "0.6.0", DAY)
    lc.save_calibration(store, "zoom wide", -0.1, 0.01, "0.6.1", DAY + datetime.timedelta(days=1))
    assert lc.list_calibrations(store) == [lc.LensCalibration("zoom wide", -0.1, 0.01, "2026-10-22", "0.6.1")]
    assert len(_files(store)) == 1


def test_replacing_removes_the_old_file_under_any_file_name(store: Path) -> None:
    store.mkdir(parents=True)
    old = store / "copied-by-hand.json"
    old.write_text(json.dumps({"name": "ZOOM WIDE", "k1": -0.2, "k2": 0.03, "saved": "2026-01-01"}), "utf-8")
    lc.save_calibration(store, "Zoom wide", -0.1, 0.01, "0.6.0", DAY)
    assert not old.exists()
    assert [c.name for c in lc.list_calibrations(store)] == ["Zoom wide"]


def test_saving_another_name_keeps_the_first(store: Path) -> None:
    lc.save_calibration(store, "Zoom wide", -0.2, 0.03, "0.6.0", DAY)
    lc.save_calibration(store, "Zoom tele", -0.05, 0.0, "0.6.0", DAY)
    assert sorted(c.name for c in lc.list_calibrations(store)) == ["Zoom tele", "Zoom wide"]


@pytest.mark.parametrize("name", ["../x", "a/b", ".hidden", "..", "/etc/passwd", "C:\\lens", "../../config.toml"])
def test_a_name_like_a_path_still_writes_inside_the_store(name: str, store: Path, tmp_path: Path) -> None:
    lc.save_calibration(store, name, -0.2, 0.03, "0.6.0", DAY)
    (path,) = _files(store)
    assert path.parent == store
    assert not path.name.startswith(".")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["lens-calibrations"]
    assert [c.name for c in lc.list_calibrations(store)] == [name]


@pytest.mark.parametrize(
    ("name", "problem"),
    [
        ("", "Enter a name."),
        ("   ", "Enter a name."),
        (None, "Enter a name."),
        (42, "Enter a name."),
        (True, "Enter a name."),
        ("x" * 61, "A name has at most 60 characters."),
        ("Zoom\nwide", "A name cannot hold control characters."),
        ("Zoom\x00", "A name cannot hold control characters."),
        ("Zoom\x7f", "A name cannot hold control characters."),
    ],
)
def test_a_name_that_cannot_name_a_calibration_is_refused(name: object, problem: str, store: Path) -> None:
    assert lc.name_problem(name) == problem
    with pytest.raises(ValueError, match=problem):
        lc.save_calibration(store, name, -0.2, 0.03, "0.6.0", DAY)  # type: ignore[arg-type]
    assert not store.exists() or _files(store) == []


@pytest.mark.parametrize("name", ["x" * 60, f"  {'x' * 60}  ", "Zoom wide 18-55 mm", "Zoom \u00e9tendu"])
def test_a_name_of_up_to_60_characters_is_kept(name: str) -> None:
    assert lc.name_problem(name) == ""


@pytest.mark.parametrize(
    ("k1", "k2", "problem"),
    [
        (None, 0.0, "The lens values must be numbers."),
        ("-0.2", 0.0, "The lens values must be numbers."),
        (True, 0.0, "The lens values must be numbers."),
        (0.0, False, "The lens values must be numbers."),
        (float("nan"), 0.0, "The lens values must be numbers."),
        (0.0, float("inf"), "The lens values must be numbers."),
        (10**400, 0.0, "The lens values must be numbers."),
        (-0.4, 0.0, "These lens values fold the overlay inside the frame."),
    ],
)
def test_a_pair_that_is_no_lens_is_refused(k1: object, k2: object, problem: str, store: Path) -> None:
    assert lc.pair_problem(k1, k2) == problem
    with pytest.raises(ValueError):
        lc.save_calibration(store, "Zoom wide", k1, k2, "0.6.0", DAY)  # type: ignore[arg-type]
    assert not store.exists() or _files(store) == []


@pytest.mark.parametrize(("k1", "k2"), [(0, 0), (-0.2, 0.03), (0.1, -0.02)])
def test_a_lens_pair_is_kept(k1: float, k2: float) -> None:
    assert lc.pair_problem(k1, k2) == ""


_UNREADABLE = {
    "broken json": "{not json",
    "a list": "[1, 2]",
    "no name": json.dumps({"k1": -0.2, "k2": 0.03}),
    "no k2": json.dumps({"name": "Zoom", "k1": -0.2}),
    "k1 text": json.dumps({"name": "Zoom", "k1": "abc", "k2": 0.03}),
    "k1 bool": json.dumps({"name": "Zoom", "k1": True, "k2": 0.03}),
    "k1 huge": '{"name": "Zoom", "k1": 1' + "0" * 400 + ', "k2": 0.03}',
    "k1 beyond a float": '{"name": "Zoom", "k1": 1e400, "k2": 0.03}',
    "folding pair": json.dumps({"name": "Zoom", "k1": -0.4, "k2": 0.0}),
    "empty name": json.dumps({"name": " ", "k1": -0.2, "k2": 0.03}),
    "name too long": json.dumps({"name": "x" * 61, "k1": -0.2, "k2": 0.03}),
    "name null": json.dumps({"name": None, "k1": -0.2, "k2": 0.03}),
    "name a number": json.dumps({"name": 7, "k1": -0.2, "k2": 0.03}),
    "saved a number": json.dumps({"name": "Zoom", "k1": -0.2, "k2": 0.03, "saved": 20261021}),
    "version a list": json.dumps({"name": "Zoom", "k1": -0.2, "k2": 0.03, "openfollow_version": ["0.6"]}),
}


@pytest.mark.parametrize("body", list(_UNREADABLE.values()), ids=list(_UNREADABLE))
def test_a_file_that_does_not_read_is_left_out_with_one_warning(body: str, store: Path, caplog) -> None:
    store.mkdir(parents=True)
    (store / "hand-edited.json").write_text(body, encoding="utf-8")
    kept = lc.save_calibration(store, "Kept", -0.2, 0.03, "0.6.0", DAY)
    with caplog.at_level(logging.WARNING, logger="openfollow.web.lens_calibrations"):
        assert lc.list_calibrations(store) == [kept]
        assert lc.list_calibrations(store) == [kept]
    assert [r.getMessage() for r in caplog.records] == [
        "lens calibration hand-edited.json does not read as one; left out"
    ]
    assert (store / "hand-edited.json").exists()  # never deleted


def test_each_unreadable_file_is_reported_once(store: Path, caplog) -> None:
    store.mkdir(parents=True)
    for name in ("a.json", "b.json"):
        (store / name).write_text("{", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="openfollow.web.lens_calibrations"):
        for _ in range(3):
            assert lc.list_calibrations(store) == []
    assert sorted(r.getMessage() for r in caplog.records) == [
        "lens calibration a.json does not read as one; left out",
        "lens calibration b.json does not read as one; left out",
    ]


def test_a_file_without_a_date_or_version_still_lists(store: Path) -> None:
    store.mkdir(parents=True)
    (store / "old.json").write_text(json.dumps({"name": "Old", "k1": -0.1, "k2": 0}), encoding="utf-8")
    assert lc.list_calibrations(store) == [lc.LensCalibration("Old", -0.1, 0.0, "", "")]


def test_there_is_no_limit_on_the_number_saved(store: Path) -> None:
    for n in range(60):
        lc.save_calibration(store, f"Lens {n:02d}", -0.01 * (n % 10), 0.0, "0.6.0", DAY)
    listed = lc.list_calibrations(store)
    assert len(listed) == 60
    assert len(_files(store)) == 60


def test_the_list_is_newest_saved_first_then_by_name_ignoring_case(store: Path) -> None:
    for name, day in (("Beta", 1), ("alpha", 1), ("gamma", 3), ("Delta", 2), ("alpha 2", 3)):
        lc.save_calibration(store, name, -0.1, 0.0, "0.6.0", datetime.date(2026, 10, day))
    assert [c.name for c in lc.list_calibrations(store)] == ["alpha 2", "gamma", "Delta", "alpha", "Beta"]
