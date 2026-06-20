# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Derive a human-memorable station name from a UUID-shaped station id.

``derive_station_name`` maps a ``station_id`` UUID to a distinct
adjective+animal default like ``"OpenFollow brave-otter"`` so peers are
distinguishable in lists; deterministic so a reboot yields the same name.
``station_name_to_hostname`` slugifies a name into a single DNS label for
mDNS advertisement.
"""

from __future__ import annotations

import re

_ADJECTIVES: tuple[str, ...] = (
    "amber",
    "azure",
    "bold",
    "brave",
    "bright",
    "brisk",
    "calm",
    "clever",
    "cosmic",
    "crisp",
    "dapper",
    "daring",
    "deep",
    "eager",
    "epic",
    "fair",
    "fancy",
    "fast",
    "fierce",
    "fresh",
    "gentle",
    "happy",
    "jolly",
    "kind",
    "lively",
    "loyal",
    "lucky",
    "merry",
    "mighty",
    "noble",
    "plucky",
    "proud",
    "quick",
    "quiet",
    "regal",
    "rosy",
    "royal",
    "rustic",
    "sharp",
    "silver",
    "sleek",
    "stout",
    "sunny",
    "swift",
    "true",
    "vivid",
    "warm",
    "wild",
    "wise",
    "zesty",
)

_ANIMALS: tuple[str, ...] = (
    "badger",
    "bear",
    "beaver",
    "bison",
    "camel",
    "chimpanzee",
    "cobra",
    "crocodile",
    "deer",
    "dolphin",
    "eagle",
    "elephant",
    "falcon",
    "flamingo",
    "fox",
    "gecko",
    "giraffe",
    "goose",
    "gorilla",
    "groundhog",
    "hawk",
    "hippo",
    "jackal",
    "kangaroo",
    "koala",
    "lemur",
    "leopard",
    "lion",
    "lynx",
    "marlin",
    "moose",
    "orca",
    "otter",
    "owl",
    "panda",
    "panther",
    "penguin",
    "raven",
    "rhino",
    "robin",
    "seal",
    "shark",
    "sparrow",
    "swan",
    "tiger",
    "viper",
    "walrus",
    "weasel",
    "wolf",
    "zebra",
)


def derive_station_name(station_id: str) -> str:
    """Map ``station_id`` (UUID hex, with or without dashes) to a name.

    Returns ``"OpenFollow <adjective>-<animal>"``. Falls back to
    ``"OpenFollow"`` when the id can't be parsed as hex.
    """
    if not isinstance(station_id, str) or not station_id:
        return "OpenFollow"
    cleaned = station_id.replace("-", "")
    try:
        h = int(cleaned, 16)
    except ValueError:
        return "OpenFollow"
    adj = _ADJECTIVES[h % len(_ADJECTIVES)]
    animal = _ANIMALS[(h // len(_ADJECTIVES)) % len(_ANIMALS)]
    return f"OpenFollow {adj}-{animal}"


_HOSTNAME_NON_LABEL = re.compile(r"[^a-z0-9]+")


def station_name_to_hostname(name: str) -> str:
    """Slugify a station name into a single DNS hostname label.

    ``"OpenFollow noble-bear"`` → ``"openfollow-noble-bear"``. Lower-cases,
    collapses any run of non-``[a-z0-9]`` into a single ``-``, trims leading/
    trailing ``-``, and caps at the 63-char DNS label limit. Returns ``""`` when
    nothing usable is left (the caller then leaves the hostname untouched).

    The result is what the device advertises over mDNS (avahi) so an operator
    can reach the unit at ``<slug>.local`` instead of chasing its DHCP address.
    """
    if not isinstance(name, str):
        return ""
    slug = _HOSTNAME_NON_LABEL.sub("-", name.strip().lower()).strip("-")
    return slug[:63].strip("-")
