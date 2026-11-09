# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""What a first run starts with: the macOS app's bundled seed, else the defaults."""

from __future__ import annotations

import logging
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from openfollow.configuration import AppConfig
from openfollow.first_run import SEED_CONFIG_NAME, bundled_seed, first_run_config

pytestmark = pytest.mark.unit

_MACOS_SEED = Path(__file__).resolve().parents[1] / "packaging" / "macos" / SEED_CONFIG_NAME


@pytest.fixture
def bundle(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    return tmp_path


def test_outside_the_bundle_a_first_run_has_the_defaults(monkeypatch) -> None:
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert bundled_seed() is None
    assert asdict(first_run_config()) == asdict(AppConfig())


def test_a_bundle_without_a_seed_has_the_defaults(bundle) -> None:
    assert bundled_seed() is None
    assert asdict(first_run_config()) == asdict(AppConfig())


def test_the_macos_bundle_starts_from_its_seed(bundle) -> None:
    shutil.copyfile(_MACOS_SEED, bundle / SEED_CONFIG_NAME)

    cfg = first_run_config()

    assert bundled_seed() == bundle / SEED_CONFIG_NAME
    assert cfg.video_source_type == "testpattern"
    assert cfg.detection.enabled is True
    assert cfg.detection.model == "yolo26m.onnx"
    assert cfg.web_port == 8080


@pytest.mark.parametrize("text", ["", "not = [toml", 'video_source_type = "testpattern"\n[detection\n'])
def test_an_unreadable_seed_falls_back_to_the_defaults(bundle, caplog, text: str) -> None:
    (bundle / SEED_CONFIG_NAME).write_text(text, encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="openfollow.first_run"):
        cfg = first_run_config()

    assert asdict(cfg) == asdict(AppConfig())
    assert "First-run settings unreadable" in caplog.text


def test_each_call_returns_a_config_of_its_own(bundle) -> None:
    shutil.copyfile(_MACOS_SEED, bundle / SEED_CONFIG_NAME)

    first = first_run_config()
    first.detection.enabled = False

    assert first_run_config().detection.enabled is True
