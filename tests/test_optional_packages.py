# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""Which optional packages a station lacks, read from what it can do."""

from __future__ import annotations

import pytest

from openfollow import optional_packages
from openfollow.optional_packages import NETWORK_MANAGER, WEBKIT

pytestmark = pytest.mark.unit

# Taken at import, before conftest replaces them for every test.
_REAL_HAS_COMMAND = optional_packages._has_command
_REAL_WEBKIT_AVAILABLE = optional_packages._webkit_available


def _has(*present: str):  # noqa: ANN202
    return lambda name: name in present


@pytest.mark.parametrize(
    ("platform", "writable", "present", "expected"),
    [
        ("linux", False, (), True),
        ("linux", False, ("nmcli",), False),
        ("linux", True, (), False),
        ("darwin", False, (), False),
    ],
    ids=["ifupdown-host", "nm-installed", "dhcpcd-writes", "macos"],
)
def test_network_manager_is_missing_only_where_it_limits_the_station(
    platform: str, writable: bool, present: tuple[str, ...], expected: bool
) -> None:
    assert (
        optional_packages.network_manager_missing(writable=writable, platform=platform, has_command=_has(*present))
        is expected
    )


@pytest.mark.parametrize(
    ("writable", "present", "webkit", "expected"),
    [
        (False, (), False, [NETWORK_MANAGER, WEBKIT]),
        (False, ("nmcli",), False, [WEBKIT]),
        (False, (), True, [NETWORK_MANAGER]),
        (True, (), True, []),
    ],
    ids=["both", "webkit-only", "nm-only", "none"],
)
def test_missing_lists_each_lacking_package_once(
    writable: bool, present: tuple[str, ...], webkit: bool, expected: list[object]
) -> None:
    found = optional_packages.missing(
        network_writable=writable, platform="linux", has_command=_has(*present), webkit_available=lambda: webkit
    )
    assert found == expected


def test_nothing_is_missing_off_linux() -> None:
    def never() -> bool:
        raise AssertionError("probed off Linux")

    assert optional_packages.missing(network_writable=False, platform="darwin", webkit_available=never) == []


def test_by_default_the_host_is_probed(monkeypatch: pytest.MonkeyPatch) -> None:
    from openfollow.runtime import webkit_browser

    monkeypatch.setattr(optional_packages, "_has_command", _REAL_HAS_COMMAND)
    monkeypatch.setattr(optional_packages, "_webkit_available", _REAL_WEBKIT_AVAILABLE)
    monkeypatch.setattr(optional_packages.shutil, "which", lambda name: None)
    monkeypatch.setattr(webkit_browser, "AVAILABLE", False)
    assert optional_packages.missing(network_writable=False, platform="linux") == [NETWORK_MANAGER, WEBKIT]


def test_a_host_with_both_lacks_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    from openfollow.runtime import webkit_browser

    monkeypatch.setattr(optional_packages, "_has_command", _REAL_HAS_COMMAND)
    monkeypatch.setattr(optional_packages, "_webkit_available", _REAL_WEBKIT_AVAILABLE)
    monkeypatch.setattr(optional_packages.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(webkit_browser, "AVAILABLE", True)
    assert optional_packages.missing(network_writable=False, platform="linux") == []
