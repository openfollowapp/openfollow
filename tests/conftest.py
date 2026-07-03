# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Shared pytest fixtures and collection hooks.

Registers Hypothesis profiles (``dev`` / ``ci``), keeps each test hermetic by
snapshotting ``os.environ`` and pinning the MIDI backend absent, and defaults
any test collected without a suite marker to ``unit`` so it can't silently
vanish from both ``-m unit`` and ``-m "integration or smoke"`` selections.
"""

import os
import warnings
from collections.abc import Iterator
from pathlib import Path

import pytest
from hypothesis import settings

# Hypothesis profiles. Registered at import time so they exist before any
# ``@given`` test is collected. ``deadline=None`` disables the per-example
# timing check – it flakes on shared CI runners under GC /
# scheduling jitter – and ``max_examples`` bounds wall time instead. The
# default ``dev`` profile stays randomized so local runs keep surfacing new
# cases, and ``print_blob=True`` emits a ``@reproduce_failure(...)`` seed on
# failure so a maintainer can pin it. CI loads the ``ci`` profile (see
# .github/workflows/ci.yml) which adds ``derandomize=True`` for byte-for-byte
# reproducible runs without committing an example database. Select via the
# ``HYPOTHESIS_PROFILE`` env var.
settings.register_profile("dev", max_examples=200, deadline=None, print_blob=True)
settings.register_profile("ci", parent=settings.get_profile("dev"), derandomize=True)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))

# Cap native thread pools to one thread per worker. Each xdist worker's OpenCV
# pool otherwise grows to the full core count on first use, so N workers fan out
# to N x cores threads and freeze an interactive desktop. macOS OpenCV uses GCD
# and ignores the OMP_/BLAS env vars, so setNumThreads is the only lever (the
# Makefile caps BLAS for the OpenBLAS Linux/Pi target). See test_thread_headroom.
try:
    import cv2

    cv2.setNumThreads(1)
except Exception:  # cv2 is an optional extra, absent in minimal envs
    pass
try:
    import torch

    torch.set_num_threads(1)
except Exception:  # torch is an optional extra, absent in minimal envs
    pass

# Suite markers declared in pyproject.toml [tool.pytest.ini_options]. A
# collected test that carries none of these is defaulted to ``unit`` (with a
# warning) by pytest_collection_modifyitems below, so it can't silently vanish.
_SUITE_MARKERS = frozenset({"unit", "integration", "smoke", "smoke_e2e", "hardware"})


class UnmarkedTestWarning(UserWarning):
    """A test was collected without a suite marker and defaulted to ``unit``."""


@pytest.fixture(autouse=True)
def _restore_os_environ() -> Iterator[None]:
    """Snapshot ``os.environ`` and restore it after every test.

    Some production code legitimately mutates the process environment directly
    rather than through ``monkeypatch`` – e.g. ``GamepadHandler.__init__`` does
    ``os.environ.setdefault("SDL_AUDIODRIVER", "dummy")`` so SDL never opens the
    ALSA device. ``monkeypatch`` only unwinds the changes it made itself,
    so without this such a write would leak into every later test in the worker
    (a hidden ordering dependency). Restoring the original mapping keeps each
    test hermetic. Cheap: a dict copy plus an equality check, and the restore
    branch only runs when a test actually changed the environment.

    Compare ``dict(os.environ)`` (not ``os.environ``) against the snapshot:
    ``os._Environ`` doesn't guarantee value-wise ``__eq__`` against a plain dict.
    """
    snapshot = dict(os.environ)
    yield
    if dict(os.environ) != snapshot:
        for key in [k for k in os.environ if k not in snapshot]:
            del os.environ[key]
        for key, val in snapshot.items():
            if os.environ.get(key) != val:
                os.environ[key] = val


@pytest.fixture(autouse=True)
def _no_real_midi_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the MIDI backend absent by default in every test.

    ``mido`` / ``python-rtmidi`` are base deps, so
    ``openfollow.input.midi`` imports a real ``mido`` / ``python-rtmidi`` at
    module load. Calling into it – e.g. ``mido.get_input_names()`` inside
    ``MidiSubsystem.discover()`` – enumerates ALSA on the headless Linux CI
    runner, where rtmidi can **segfault the test worker**: an uncatchable
    native crash, not a Python exception the ``discover()`` try/except can
    swallow. Tests must never touch the real backend, so pin module-level
    ``_mido`` to ``None`` (the "no backend wired" path the discovery tests
    already assume) as the default. Tests that exercise real MIDI behaviour
    override it with the in-process ``_FakeMido`` via their ``fake_mido``
    fixture, which is set up after this autouse default and therefore wins.
    """
    monkeypatch.setattr("openfollow.input.midi._mido", None, raising=False)
    # Pin the paired import-error string too. ``import mido`` succeeds in CI,
    # The real module value is ``None``; a fixed sentinel allows tests to verify error messages.
    monkeypatch.setattr(
        "openfollow.input.midi._MIDO_IMPORT_ERROR",
        "ImportError: no MIDI backend (test default)",
        raising=False,
    )


@pytest.fixture
def temp_config_path(tmp_path: Path) -> Path:
    return tmp_path / "config.toml"


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Default any unmarked test to ``unit`` so it can never silently vanish.

    ``make test`` runs two disjoint selections – ``test-unit`` (``-m unit``)
    and ``test-integration`` (``-m "integration or smoke"``) – and there is no
    path-based auto-marking. A test carrying none of ``unit`` / ``integration``
    / ``smoke`` / ``hardware`` matches *neither* selection: it is silently
    deselected by both, never runs in CI, and drops from coverage – surfacing
    as confusing sub-100% failure blamed on unrelated module.

    ``tryfirst`` so this runs before pytest's own ``-m``/``-k`` deselection:
    the ``unit`` marker we add here makes the test survive ``-m unit`` and run
    + count for coverage. A warning names the offenders so the author can pick
    the right lane (a slow live-server test belongs in ``integration``, not
    ``unit``). Auto-marking rather than ``raise``-ing because raising from this
    hook is reported as an ``INTERNALERROR`` under ``pytest-xdist`` (CI runs
    ``-n logical``); auto-mark keeps the run clean while still preventing the
    silent-coverage-loss this guards against.
    """
    unmarked = [item for item in items if not _SUITE_MARKERS.intersection(m.name for m in item.iter_markers())]
    for item in unmarked:
        item.add_marker(pytest.mark.unit)
    if unmarked:
        listing = "\n  ".join(item.nodeid for item in unmarked)
        warnings.warn(
            "Defaulted to `unit` – these tests carry no suite marker "
            "(unit / integration / smoke / hardware):\n  "
            f"{listing}\n"
            "Add e.g. `pytestmark = pytest.mark.unit` near the top of the file "
            "to silence this and pick the right lane.",
            UnmarkedTestWarning,
            stacklevel=1,
        )
