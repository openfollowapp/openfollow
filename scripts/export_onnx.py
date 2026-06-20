#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Export an Ultralytics YOLO .pt model to ONNX."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "model",
        nargs="?",
        default="yolov8n.pt",
        help="Input .pt model path (default: yolov8n.pt)",
    )
    parser.add_argument(
        "--imgsz",
        type=int,
        default=320,
        help="Square export image size (default: 320)",
    )
    parser.add_argument(
        "--opset",
        type=int,
        default=17,
        help="ONNX opset version (default: 17)",
    )
    parser.add_argument(
        "--dynamic",
        action="store_true",
        help="Enable dynamic input shapes (default: false)",
    )
    parser.add_argument(
        "--no-simplify",
        action="store_true",
        help="Disable ONNX graph simplification",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Move the exported .onnx into this directory (created if needed)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    from ultralytics import YOLO  # type: ignore[import-untyped]

    model = YOLO(args.model)
    exported = model.export(
        format="onnx",
        imgsz=args.imgsz,
        opset=args.opset,
        simplify=not args.no_simplify,
        dynamic=args.dynamic,
    )
    if args.output_dir:
        dest_dir = Path(args.output_dir).expanduser()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / Path(exported).name
        shutil.move(str(exported), str(dest))
        exported = str(dest)
    print(exported)


if __name__ == "__main__":
    main()
