# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""The snapshot size on ``/api/wizard/lens/calibrations``: optional, both sides or neither, strictly read."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import openfollow.web.discovery as discovery_module
from openfollow.web.server import ConfigWebServer
from tests._ports import live_on_free_port

pytestmark = pytest.mark.integration

_ROUTE = "/api/wizard/lens/calibrations"
_LENS = {"name": "Zoom wide", "k1": -0.2, "k2": 0.03, "cx": 0.0, "cy": 0.0}


@pytest.fixture(scope="module")
def station(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[ConfigWebServer, str]]:
    with pytest.MonkeyPatch.context() as mp:
        for cls in (discovery_module.BeaconSender, discovery_module.BeaconReceiver):
            mp.setattr(cls, "start", lambda self: None)
            mp.setattr(cls, "stop", lambda self: None)
        config_path = tmp_path_factory.mktemp("station") / "config.toml"
        with live_on_free_port(
            lambda port: ConfigWebServer(config_path=str(config_path), host="127.0.0.1", port=port)
        ) as (server, base):
            yield server, base


@pytest.fixture
def base(station: tuple[ConfigWebServer, str]) -> str:
    return station[1]


@pytest.fixture
def lens_dir(station: tuple[ConfigWebServer, str]) -> Path:
    directory = Path(station[0].config_path).parent / "lens-calibrations"
    for path in directory.glob("*.json") if directory.is_dir() else ():
        path.unlink()
    return directory


def _send(base: str, payload: Any = None) -> tuple[int, Any]:
    req = urllib.request.Request(
        f"{base}{_ROUTE}",
        data=None if payload is None else json.dumps(payload).encode(),
        method="GET" if payload is None else "POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def test_a_lens_saved_with_its_snapshot_size_lists_with_it(base: str, lens_dir: Path) -> None:
    status, data = _send(base, {**_LENS, "width": 1920, "height": 1080})
    assert status == 200
    assert (data["saved"]["width"], data["saved"]["height"]) == (1920, 1080)
    (path,) = lens_dir.glob("*.json")
    assert json.loads(path.read_text(encoding="utf-8")) == data["saved"]
    assert _send(base) == (200, {"calibrations": [data["saved"]]})


def test_a_lens_saved_before_any_snapshot_has_no_size(base: str, lens_dir: Path) -> None:
    status, data = _send(base, _LENS)
    assert status == 200
    assert (data["saved"]["width"], data["saved"]["height"]) == (None, None)
    _, listed = _send(base)
    assert [(c["width"], c["height"]) for c in listed["calibrations"]] == [(None, None)]


def _refused(base: str, lens_dir: Path, size: dict) -> None:
    status, data = _send(base, {**_LENS, **size})
    assert status == 400
    assert data["error"] == "The picture size must be two whole numbers from 1 to 16384."
    assert not lens_dir.is_dir() or list(lens_dir.glob("*.json")) == []


@pytest.mark.parametrize("size", [{"width": 1920}, {"height": 1080}], ids=["width only", "height only"])
def test_one_side_alone_is_refused(base: str, lens_dir: Path, size: dict) -> None:
    _refused(base, lens_dir, size)


@pytest.mark.parametrize("bad", [None, "abc", "1920", True, 1920.5, [1920]])
@pytest.mark.parametrize("side", ["width", "height"])
def test_a_side_of_the_wrong_type_is_refused(base: str, lens_dir: Path, side: str, bad: object) -> None:
    _refused(base, lens_dir, {"width": 1920, "height": 1080, side: bad})


@pytest.mark.parametrize("bad", [0, -1080, 16385, 10**400])
@pytest.mark.parametrize("side", ["width", "height"])
def test_a_side_out_of_range_is_refused(base: str, lens_dir: Path, side: str, bad: int) -> None:
    _refused(base, lens_dir, {"width": 1920, "height": 1080, side: bad})


def test_the_largest_snapshot_side_is_kept(base: str, lens_dir: Path) -> None:
    status, data = _send(base, {**_LENS, "width": 16384, "height": 16384})
    assert status == 200
    assert (data["saved"]["width"], data["saved"]["height"]) == (16384, 16384)


def test_a_stored_file_without_a_size_lists_with_none(base: str, lens_dir: Path) -> None:
    lens_dir.mkdir(exist_ok=True)
    body = {
        "name": "Old",
        "k1": -0.1,
        "k2": 0.0,
        "cx": 0.0,
        "cy": 0.0,
        "saved": "2026-01-02",
        "openfollow_version": "0.5.0",
    }
    (lens_dir / "old.json").write_text(json.dumps(body), encoding="utf-8")
    (lens_dir / "odd.json").write_text(json.dumps({**body, "name": "Odd", "width": "wide", "height": 0}), "utf-8")
    status, data = _send(base)
    assert status == 200
    assert sorted((c["name"], c["width"], c["height"]) for c in data["calibrations"]) == [
        ("Odd", None, None),
        ("Old", None, None),
    ]
