# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Property-based tests for configuration coercion + round-trip.

The ``_coerce_*`` helpers are the repo's validation contract: hand-edited TOML
or a crafted web POST flows through them, and the promise is "normalise to a
finite, in-range, correctly-typed value – never raise, never leak inf/nan".
These tests assert that promise across arbitrary input (nan, inf, huge ints,
strings, None, bools, lists). A separate test asserts ``save_config`` →
``load_config`` is a fixed point on an already-normalised config.

Conventions mirror ``test_configuration.py``.
"""

from __future__ import annotations

import math
import os
import re
import string
import tempfile

import pytest
from hypothesis import given
from hypothesis import strategies as st

from openfollow.configuration import (
    AppConfig,
    CameraConfig,
    DetectionConfig,
    GridConfig,
    MarkerConfig,
    _coerce_bool,
    _coerce_choice,
    _coerce_float,
    _coerce_hex_color,
    _coerce_int,
    _coerce_optional_float,
    _coerce_optional_marker_id,
    _coerce_str,
    load_config,
    save_config,
)

pytestmark = pytest.mark.unit

# Arbitrary, deliberately-hostile input: covers the bad-type, non-finite, and
# overflow paths every ``_coerce_*`` helper must survive.
_ANY = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.integers(min_value=10**200, max_value=10**500),  # float() overflows
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=20),
    st.lists(st.integers(), max_size=3),
)


def _finite(lo: float, hi: float) -> st.SearchStrategy[float]:
    return st.floats(min_value=lo, max_value=hi, allow_nan=False, allow_infinity=False)


@st.composite
def _float_bounds(draw: st.DrawFn) -> tuple[float, float, float]:
    """An ordered ``(lo, hi, default)`` with ``lo <= default <= hi``."""
    lo = draw(_finite(-1e6, 1e6))
    hi = draw(_finite(lo, 1e6))
    default = draw(_finite(lo, hi))
    return lo, hi, default


@st.composite
def _int_bounds(draw: st.DrawFn) -> tuple[int, int, int]:
    lo = draw(st.integers(-(10**6), 10**6))
    hi = draw(st.integers(lo, 10**6))
    default = draw(st.integers(lo, hi))
    return lo, hi, default


# --- _coerce_float -----------------------------------------------------------


@given(value=_ANY, bounds=_float_bounds())
def test_coerce_float_is_finite_and_in_range(value: object, bounds: tuple[float, float, float]) -> None:
    lo, hi, default = bounds
    out = _coerce_float(value, default, lo=lo, hi=hi)
    assert isinstance(out, float)
    assert math.isfinite(out)
    assert lo <= out <= hi


@given(value=_ANY, default=_finite(-1e9, 1e9))
def test_coerce_float_without_bounds_is_always_finite(value: object, default: float) -> None:
    out = _coerce_float(value, default)
    assert isinstance(out, float)
    assert math.isfinite(out)


# --- _coerce_int -------------------------------------------------------------


@given(value=_ANY, bounds=_int_bounds())
def test_coerce_int_is_pure_int_and_in_range(value: object, bounds: tuple[int, int, int]) -> None:
    lo, hi, default = bounds
    out = _coerce_int(value, default, lo=lo, hi=hi)
    assert type(out) is int  # not bool (an int subclass)
    assert lo <= out <= hi


@given(value=st.booleans(), bounds=_int_bounds())
def test_coerce_int_rejects_bool_input(value: bool, bounds: tuple[int, int, int]) -> None:
    lo, hi, default = bounds
    assert _coerce_int(value, default, lo=lo, hi=hi) == default


# --- _coerce_optional_float --------------------------------------------------


@given(value=_ANY, bounds=_float_bounds())
def test_coerce_optional_float_is_none_or_finite_in_range(
    value: object,
    bounds: tuple[float, float, float],
) -> None:
    lo, hi, default = bounds  # non-None default
    out = _coerce_optional_float(value, default, lo=lo, hi=hi)
    assert out is None or (isinstance(out, float) and math.isfinite(out) and lo <= out <= hi)


@given(value=_ANY)
def test_coerce_optional_float_with_none_default_never_leaks_nonfinite(value: object) -> None:
    out = _coerce_optional_float(value, None)
    assert out is None or (isinstance(out, float) and math.isfinite(out))


# --- _coerce_optional_marker_id ----------------------------------------------


@given(value=_ANY)
def test_coerce_optional_marker_id_is_none_or_nonnegative_int(value: object) -> None:
    out = _coerce_optional_marker_id(value)
    assert out is None or (type(out) is int and out >= 0)


# --- _coerce_choice / _coerce_str / _coerce_bool / _coerce_hex_color ---------


@given(
    value=_ANY,
    choices=st.lists(st.text(min_size=1, max_size=6), min_size=1, max_size=5, unique=True),
    pick=st.integers(min_value=0, max_value=4),
)
def test_coerce_choice_result_is_always_a_choice(
    value: object,
    choices: list[str],
    pick: int,
) -> None:
    choices_t = tuple(choices)
    default = choices_t[pick % len(choices_t)]
    assert _coerce_choice(value, choices_t, default) in choices_t


@given(value=_ANY, default=st.text(max_size=10))
def test_coerce_str_always_returns_str(value: object, default: str) -> None:
    assert isinstance(_coerce_str(value, default), str)


@given(value=_ANY, default=st.booleans())
def test_coerce_bool_always_returns_bool(value: object, default: bool) -> None:
    assert type(_coerce_bool(value, default)) is bool


@given(value=_ANY, default=st.sampled_from(["#000000", "#ffffff", "#ff8000"]))
def test_coerce_hex_color_is_always_lowercase_rrggbb(value: object, default: str) -> None:
    out = _coerce_hex_color(value, default)
    assert re.fullmatch(r"#[0-9a-f]{6}", out) is not None


# --- save_config ∘ load_config is a fixed point ------------------------------

# Plain ASCII names round-trip through TOML without strip/escape surprises and
# stay non-empty (an empty ``psn_system_name`` is normalised to the default).
_NAMES = st.text(alphabet=string.ascii_letters + string.digits, min_size=1, max_size=12)

# Marker ids: positive (id 0 is the reserved "ignored" sentinel that
# ``__post_init__`` strips) and unique (it dedupes), so a generated list is
# already normalised and round-trips as an identity rather than a normalisation.
_MARKER_IDS = st.lists(st.integers(min_value=1, max_value=64), min_size=0, max_size=5, unique=True)


@st.composite
def _normalised_configs(draw: st.DrawFn) -> AppConfig:
    """A valid, already-normalised ``AppConfig`` with random overrides.

    Covers the serialisation-fragile fields specifically – the marker-id lists
    and ``marker_move_speeds`` (int keys → TOML string keys → back) – plus
    several sub-config trees. ``marker_move_speeds`` keys are kept ⊆
    ``controlled_marker_ids`` and values non-negative, so ``save_config``'s
    prune/normalise step is a no-op and the round-trip is a true identity.
    """
    controlled = draw(_MARKER_IDS)
    speeds = {mid: draw(_finite(0.0, 10.0)) for mid in controlled if draw(st.booleans())}
    return AppConfig(
        psn_system_name=draw(_NAMES),
        window_width=draw(st.integers(min_value=1, max_value=7680)),
        window_height=draw(st.integers(min_value=1, max_value=4320)),
        stall_timeout=draw(_finite(0.0, 60.0)),
        heal_interval=draw(_finite(0.0, 60.0)),
        web_port=draw(st.integers(min_value=1, max_value=65535)),
        controlled_marker_ids=list(controlled),
        viewer_marker_ids=draw(_MARKER_IDS),
        marker_move_speeds=speeds,
        grid=GridConfig(
            width=draw(_finite(0.1, 100.0)),
            depth=draw(_finite(0.1, 100.0)),
            spacing=draw(_finite(0.1, 50.0)),
            thickness=draw(st.integers(min_value=1, max_value=20)),
            transparency=draw(_finite(0.0, 1.0)),
            origin_visible=draw(st.booleans()),
        ),
        camera=CameraConfig(
            pos_x=draw(_finite(-50.0, 50.0)),
            pos_y=draw(_finite(-50.0, 50.0)),
            pos_z=draw(_finite(0.0, 30.0)),
            pitch=draw(_finite(-89.0, 89.0)),
            fov=draw(_finite(1.0, 179.0)),
        ),
        marker=MarkerConfig(
            min_speed=draw(_finite(0.0, 5.0)),
            max_speed=draw(_finite(5.0, 20.0)),
            ball_size=draw(_finite(0.0, 5.0)),
            transparency=draw(_finite(0.0, 1.0)),
        ),
        detection=DetectionConfig(
            confidence=draw(_finite(0.0, 1.0)),
            interval_ms=draw(st.integers(min_value=1, max_value=9999)),
            max_persons=draw(st.integers(min_value=1, max_value=50)),
            smoothing=draw(_finite(0.0, 1.0)),
        ),
    )


@given(cfg=_normalised_configs())
def test_save_then_load_is_identity(cfg: AppConfig) -> None:
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "config.toml")
        save_config(cfg, path)
        loaded = load_config(path)
    assert loaded == cfg
