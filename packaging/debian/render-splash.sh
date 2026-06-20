#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Pre-render the boot splash PNG: the OpenFollow logo centered (~70% width) on a
# black WxH canvas. Primary path uses gi Rsvg + pycairo (present on the build
# host via gir1.2-rsvg-2.0 + python3-cairo); falls back to a solid near-black
# canvas so the package still builds when those are unavailable.
#
# Usage: render-splash.sh <logo.svg> <out.png> <width> <height>
set -euo pipefail
SVG="$1"; OUT="$2"; W="$3"; H="$4"

python3 - "$SVG" "$OUT" "$W" "$H" <<'PY'
import struct
import sys
import zlib

svg, out, W, H = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])


def render_with_rsvg() -> bool:
    try:
        import gi
        gi.require_version("Rsvg", "2.0")
        from gi.repository import Rsvg
        import cairo
    except Exception:
        return False
    handle = Rsvg.Handle.new_from_file(svg)
    dim = handle.get_dimensions()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    ctx = cairo.Context(surface)
    ctx.set_source_rgb(0, 0, 0)
    ctx.paint()
    scale = (0.70 * W) / dim.width
    ctx.translate((W - dim.width * scale) / 2.0, (H - dim.height * scale) / 2.0)
    ctx.scale(scale, scale)
    handle.render_cairo(ctx)
    surface.write_to_png(out)
    return True


def write_solid_png() -> None:
    """Minimal solid near-black RGB PNG, no third-party libs."""
    rgb = bytes((8, 8, 8))
    row = b"\x00" + rgb * W  # filter byte 0 (None) + pixels
    raw = row * H

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)  # 8-bit RGB
    with open(out, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", ihdr))
        fh.write(chunk(b"IDAT", zlib.compress(raw, 9)))
        fh.write(chunk(b"IEND", b""))


if render_with_rsvg():
    print("[render-splash] rendered logo splash via Rsvg")
else:
    write_solid_png()
    print("[render-splash] WARN: Rsvg/cairo unavailable; wrote solid black splash")
PY
