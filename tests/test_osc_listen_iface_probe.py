# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Checks for scripts/hw_validation/osc_listen_iface_probe.py: the verdict comes
from the configured pin, never from the kernel state the probe is checking."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit


def _load() -> ModuleType:
    source = inspect.getsourcefile(_load)
    assert source, "Could not resolve current test source path"
    script = Path(source).resolve().parents[1] / "scripts" / "hw_validation" / "osc_listen_iface_probe.py"
    spec = importlib.util.spec_from_file_location("osc_listen_iface_probe", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _load()

_ADDRESSES = {"eth0": "192.0.2.10", "eth1": "198.51.100.10"}


# --------------------------------------------------------------------------- #
# configured_osc
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("toml", "expected"),
    [
        ('psn_source_iface = "eth0"\n[osc]\nmulticast_group = "239.20.20.20"\nlisten_iface = "eth1"\n', "eth1"),
        ('psn_source_iface = "eth0"\n[osc]\nmulticast_group = "239.20.20.20"\nlisten_iface = ""\n', "eth0"),
        ('psn_source_iface = ""\n[osc]\nmulticast_group = "239.20.20.20"\n', ""),
    ],
    ids=["own-pin", "follows-station", "unpinned"],
)
def test_configured_osc_resolves_the_pin_like_the_station(tmp_path: Path, toml: str, expected: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(toml, encoding="utf-8")
    assert probe.configured_osc(str(path)) == ("239.20.20.20", expected)


@pytest.mark.parametrize("content", [None, "osc = [unclosed\n"], ids=["missing", "malformed"])
def test_configured_osc_reads_nothing_from_an_unusable_file(tmp_path: Path, content: str | None) -> None:
    path = tmp_path / "config.toml"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert probe.configured_osc(str(path)) == ("", "")


# --------------------------------------------------------------------------- #
# membership_failures / delivery_expected
# --------------------------------------------------------------------------- #


def test_a_join_on_the_wrong_adapter_fails() -> None:
    """The case a kernel-derived expectation passes: pinned to eth0, joined on
    eth1, and multicast via eth1 arriving exactly as eth1's membership predicts."""
    assert probe.membership_failures("eth0", _ADDRESSES, ["eth1"])
    assert probe.delivery_expected("eth1", "eth0", ["eth1"]) is False
    assert probe.delivery_expected("eth0", "eth0", ["eth1"]) is True


@pytest.mark.parametrize(
    ("pin", "holders", "ok"),
    [
        ("eth0", ["eth0"], True),
        ("eth0", ["eth0", "eth1"], False),
        ("eth0", [], False),
        ("eth2", [], True),
        ("eth2", ["eth0"], False),
        ("", ["eth1"], True),
        ("", ["eth0", "eth1"], False),
        ("", [], False),
    ],
    ids=[
        "on-the-pin",
        "also-elsewhere",
        "missing",
        "pin-down-holds-nothing",
        "pin-down-fell-back",
        "unpinned-one",
        "unpinned-two",
        "unpinned-none",
    ],
)
def test_membership_is_judged_against_the_pin(pin: str, holders: list[str], ok: bool) -> None:
    assert (probe.membership_failures(pin, _ADDRESSES, holders) == []) is ok


def test_unpinned_delivery_follows_the_kernel() -> None:
    """With no pin the routing table's choice is the only reference there is."""
    assert probe.delivery_expected("eth1", "", ["eth1"]) is True
    assert probe.delivery_expected("eth0", "", ["eth1"]) is False
