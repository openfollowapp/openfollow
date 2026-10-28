# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""``/api/wizard/lens/opencv``: an OpenCV calibration file converted for the snapshot."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest

import openfollow.web.discovery as discovery_module
from openfollow.lens_model import lens_warp_is_valid
from openfollow.scene.opencv_lens import MAX_FILE_BYTES
from openfollow.web.server import ConfigWebServer
from tests._opencv_files import opencv_xml
from tests._ports import live_on_free_port

pytestmark = pytest.mark.integration

_ROUTE = "/api/wizard/lens/opencv"
_SNAP = {"image_width": 1920, "image_height": 1080}


@pytest.fixture(scope="module")
def base(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    with pytest.MonkeyPatch.context() as mp:
        for cls in (discovery_module.BeaconSender, discovery_module.BeaconReceiver):
            mp.setattr(cls, "start", lambda self: None)
            mp.setattr(cls, "stop", lambda self: None)
        config_path = tmp_path_factory.mktemp("station") / "config.toml"
        with live_on_free_port(
            lambda port: ConfigWebServer(config_path=str(config_path), host="127.0.0.1", port=port)
        ) as (_server, url):
            yield url


def _send(base: str, payload: Any, *, raw: bytes | None = None) -> tuple[int, Any]:
    req = urllib.request.Request(
        f"{base}{_ROUTE}",
        data=raw if raw is not None else json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def test_a_calibration_file_comes_back_as_a_lens_for_the_snapshot(base: str) -> None:
    status, data = _send(base, {"xml": opencv_xml(1500.0, (989.5, 539.5)), **_SNAP})
    assert status == 200
    assert lens_warp_is_valid(data["k1"], data["k2"], data["cx"], data["cy"])
    assert data["cx"] > 0.0 and data["k1"] < 0.0
    assert (data["calibration_width"], data["calibration_height"], data["size_from_file"]) == (1920, 1080, True)
    assert (data["fisheye"], data["coverage"]) == (False, 1.0)
    assert data["rms_px"] < 0.5 and data["corner_px"] is not None


def test_a_fisheye_file_says_so(base: str) -> None:
    status, data = _send(base, {"xml": opencv_xml(1300.0, dist=[-0.04, 0.008, -0.004, 0.0005], fisheye="1"), **_SNAP})
    assert (status, data["fisheye"]) == (200, True)


def test_a_file_without_its_size_asks_for_one_then_takes_it(base: str) -> None:
    text = opencv_xml(3000.0, (1919.5, 1079.5), size=None)
    status, data = _send(base, {"xml": text, **_SNAP})
    assert (status, data["needs_size"]) == (422, True)
    assert "does not say which picture size" in data["error"]
    status, data = _send(base, {"xml": text, **_SNAP, "calibration_width": 3840, "calibration_height": 2160})
    assert status == 200
    assert (data["calibration_width"], data["calibration_height"], data["size_from_file"]) == (3840, 2160, False)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("%YAML:1.0\ncameraMatrix: !!opencv-matrix\n", "Only XML calibration files can be read."),
        (
            opencv_xml(size=(1440, 1080), c=(719.5, 539.5)),
            "calibrated on a 1440 × 1080 picture, this one is 1920 × 1080",
        ),
        (opencv_xml(dist=[-0.1, 0.02, 0.0, 0.0]), "does not say whether it is a fisheye calibration"),
        ("<opencv_storage>\ud800</opencv_storage>", "not a readable XML file"),
        (opencv_xml(1.7e308, (479.5, 269.5), size=(960, 540)), "camera matrix is out of range"),
    ],
    ids=["yaml", "other shape", "four bare values", "lone surrogate", "overflowing matrix"],
)
def test_a_file_that_gives_no_lens_is_refused_with_the_reason(base: str, text: str, reason: str) -> None:
    status, data = _send(base, {"xml": text, **_SNAP})
    assert status == 422 and reason in data["error"] and "needs_size" not in data


@pytest.mark.parametrize(
    "payload",
    [
        [],
        "xml",
        {**_SNAP},
        {"xml": None, **_SNAP},
        {"xml": 3, **_SNAP},
        {"xml": True, **_SNAP},
        {"xml": opencv_xml()},
        {"xml": opencv_xml(), "image_width": "abc", "image_height": 1080},
        {"xml": opencv_xml(), "image_width": None, "image_height": 1080},
        {"xml": opencv_xml(), "image_width": True, "image_height": 1080},
        {"xml": opencv_xml(), "image_width": 0, "image_height": 1080},
        {"xml": "x" * (MAX_FILE_BYTES + 1), **_SNAP},
    ],
    ids=[
        "list",
        "string",
        "no xml",
        "xml null",
        "xml number",
        "xml bool",
        "no snapshot size",
        "width text",
        "width null",
        "width bool",
        "width zero",
        "oversized",
    ],
)
def test_a_malformed_body_is_a_400(base: str, payload: Any) -> None:
    assert _send(base, payload)[0] == 400


@pytest.mark.parametrize(
    "size",
    [
        {"calibration_width": 1920},
        {"calibration_width": 0, "calibration_height": 1080},
        {"calibration_width": 99999, "calibration_height": 1080},
        {"calibration_width": 1920.5, "calibration_height": 1080},
        {"calibration_width": "1920", "calibration_height": 1080},
        {"calibration_width": True, "calibration_height": 1080},
        {"calibration_width": None, "calibration_height": 1080},
    ],
    ids=["one side", "zero", "huge", "fraction", "text", "bool", "null"],
)
def test_a_malformed_calibration_size_is_a_400(base: str, size: dict[str, Any]) -> None:
    assert _send(base, {"xml": opencv_xml(size=None), **_SNAP, **size})[0] == 400


@pytest.mark.parametrize("raw", [b"{", b"null", b"\xff\xfe"], ids=["broken", "null", "not utf-8"])
def test_a_body_that_is_no_json_is_a_400(base: str, raw: bytes) -> None:
    assert _send(base, None, raw=raw)[0] == 400
