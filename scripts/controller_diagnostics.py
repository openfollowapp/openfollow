#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Inspect connected controllers via pygame and run rumble / LED diagnostics."""

from __future__ import annotations

import argparse
import time
from typing import Any

import pygame


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect connected controllers and run basic diagnostics.")
    parser.add_argument("--device", type=int, default=0, help="Controller index to test (default: 0)")
    parser.add_argument(
        "--test-rumble",
        action="store_true",
        help="Run a short rumble test on the selected controller",
    )
    parser.add_argument(
        "--test-led",
        nargs=3,
        type=int,
        metavar=("R", "G", "B"),
        help="Set controller LED color (0-255 each) on the selected controller",
    )
    return parser.parse_args()


def _list_joysticks() -> list[Any]:
    pygame.init()
    pygame.joystick.init()
    joysticks: list[Any] = []
    for idx in range(pygame.joystick.get_count()):
        js = pygame.joystick.Joystick(idx)
        js.init()
        joysticks.append(js)
    return joysticks


def _print_joysticks(joysticks: list[Any]) -> None:
    if not joysticks:
        print("No controllers detected.")
        return
    print(f"Detected {len(joysticks)} controller(s):")
    for idx, js in enumerate(joysticks):
        print(
            f"  [{idx}] {js.get_name()} "
            f"(axes={js.get_numaxes()}, buttons={js.get_numbuttons()}, hats={js.get_numhats()})"
        )


def _run_rumble_test(joystick: Any) -> int:
    if not hasattr(joystick, "rumble"):
        print("This controller does not support rumble via pygame.")
        return 1
    if not joystick.rumble(0.8, 0.8, 400):
        print("Rumble test failed to start.")
        return 1
    time.sleep(0.5)
    print("Rumble test completed.")
    return 0


def _clamp_rgb(values: tuple[int, int, int]) -> tuple[int, int, int]:
    return tuple(max(0, min(255, channel)) for channel in values)


def _run_led_test(joystick: Any, rgb: tuple[int, int, int]) -> int:
    if not hasattr(joystick, "set_led"):
        print("This controller does not support LED control via pygame.")
        return 1
    joystick.set_led(*rgb)
    print(f"LED set to RGB{rgb}.")
    return 0


def main() -> int:
    args = _parse_args()
    joysticks: list[Any] = []
    try:
        joysticks = _list_joysticks()
        _print_joysticks(joysticks)
        if not joysticks:
            return 1

        if args.device < 0 or args.device >= len(joysticks):
            print(f"Invalid --device index {args.device}.")
            return 2

        joystick = joysticks[args.device]
        if args.test_rumble:
            return _run_rumble_test(joystick)
        if args.test_led is not None:
            rgb = _clamp_rgb(tuple(args.test_led))
            return _run_led_test(joystick, rgb)
        return 0
    finally:
        if joysticks:
            pygame.joystick.quit()
        pygame.quit()


if __name__ == "__main__":
    raise SystemExit(main())
