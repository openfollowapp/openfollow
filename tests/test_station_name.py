# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the deterministic station-name derivation."""

from __future__ import annotations

import re
import uuid

import pytest

from openfollow.marker_catalog.station_name import (
    derive_station_name,
    station_name_to_hostname,
)

pytestmark = pytest.mark.unit

_HOSTNAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

_NAME_RE = re.compile(r"^OpenFollow [a-z]+-[a-z]+$")


def test_returns_canonical_form() -> None:
    name = derive_station_name(uuid.uuid4().hex)
    assert _NAME_RE.match(name), name


def test_deterministic_for_same_input() -> None:
    sid = "0123456789abcdef0123456789abcdef"
    assert derive_station_name(sid) == derive_station_name(sid)


def test_different_uuids_usually_differ() -> None:
    names = {derive_station_name(uuid.uuid4().hex) for _ in range(50)}
    # At least some variation across 50 random UUIDs; not a strict
    # bijection (the namespace is ~2500 combinations) but more than one.
    assert len(names) > 1


def test_accepts_dashed_uuid() -> None:
    sid = str(uuid.UUID("12345678-1234-5678-1234-567812345678"))
    assert _NAME_RE.match(derive_station_name(sid))


def test_falls_back_to_openfollow_on_blank() -> None:
    assert derive_station_name("") == "OpenFollow"


def test_falls_back_to_openfollow_on_non_string() -> None:
    assert derive_station_name(None) == "OpenFollow"  # type: ignore[arg-type]


def test_falls_back_to_openfollow_on_non_hex() -> None:
    assert derive_station_name("not-hex-at-all-zzz") == "OpenFollow"


def test_dashed_and_undashed_uuids_collapse_to_same_name() -> None:
    with_dashes = derive_station_name("01234567-89ab-cdef-0123-456789abcdef")
    without = derive_station_name("0123456789abcdef0123456789abcdef")
    assert with_dashes == without


@pytest.mark.parametrize(
    ("station_name", "expected"),
    [
        ("OpenFollow noble-bear", "openfollow-noble-bear"),
        ("OpenFollow", "openfollow"),
        ("  Booth 12 (Stage Left)!  ", "booth-12-stage-left"),
        ("UPPER_case__mix", "upper-case-mix"),
        ("---trim---", "trim"),
        ("", ""),
        ("***", ""),
    ],
)
def test_station_name_to_hostname_slugifies(station_name: str, expected: str) -> None:
    assert station_name_to_hostname(station_name) == expected


def test_station_name_to_hostname_is_a_valid_dns_label() -> None:
    slug = station_name_to_hostname(derive_station_name(uuid.uuid4().hex))
    assert _HOSTNAME_RE.match(slug), slug
    assert len(slug) <= 63


def test_station_name_to_hostname_caps_at_63_chars() -> None:
    slug = station_name_to_hostname("x" * 200)
    assert len(slug) == 63
    assert not slug.endswith("-")


def test_station_name_to_hostname_non_string_is_empty() -> None:
    assert station_name_to_hostname(None) == ""  # type: ignore[arg-type]
