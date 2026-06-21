# -*- mode: python ; coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""PyInstaller spec for the self-contained macOS .app bundle.

Invoked by ``packaging/macos/build-dmg.sh`` (which generates the icon + default
model into ``_build/`` first). Bundles the GTK / GStreamer / GObject stack plus
the detection (onnxruntime + opencv) and export (ultralytics + torch) toolchains
so a clean Mac needs nothing pre-installed.

The torch / ultralytics collection is the fragile part; this is the file to
iterate on when the post-build self-check (``OPENFOLLOW_SELFCHECK=1``) or a real
export fails.
"""

import os
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_submodules,
    copy_metadata,
)

REPO = Path(SPECPATH).resolve().parents[1]  # packaging/macos -> repo root  # noqa: F821
MACOS = REPO / "packaging" / "macos"
BUILD = MACOS / "_build"

with open(REPO / "pyproject.toml", "rb") as _f:
    VERSION = tomllib.load(_f)["project"]["version"]

datas = []
binaries = []

# GI / GStreamer / GTK are reached through gi.repository.*; naming them as hidden
# imports triggers the contributed hooks that pull their typelibs and (for Gst)
# the plugin dylibs into the bundle.
hiddenimports = [
    "gi",
    "gi.repository.GLib",
    "gi.repository.GObject",
    "gi.repository.Gio",
    "gi.repository.Gtk",
    "gi.repository.Gdk",
    "gi.repository.GdkPixbuf",
    "gi.repository.Gst",
    "gi.repository.GstApp",
    "gi.repository.GstVideo",
    "gi.repository.Rsvg",
    "cairo",
    "export_onnx",  # scripts/export_onnx.py, re-exec target for in-app export
]


def _add(triple):
    d, b, h = triple
    datas.extend(d)
    binaries.extend(b)
    hiddenimports.extend(h)


# Python bindings whose native deps we want fully relocated.
for pkg in ("gi", "cairo"):
    _add(collect_all(pkg))

# Detection inference backend (cheap, always bundled).
for pkg in ("onnxruntime",):
    try:
        _add(collect_all(pkg))
    except Exception:  # noqa: BLE001
        pass

# opencv / numpy: rely on the contributed hooks via hiddenimports to avoid the
# heavy over-collection collect_all('cv2') is prone to.
hiddenimports += ["cv2", "numpy"]

# Export toolchain. torch is huge and has a maintained hook, so lean on the hook
# (hiddenimport) and only hand-collect ultralytics' data + submodules and the
# onnx exporters ultralytics would otherwise try to pip-install at runtime.
hiddenimports += ["torch", "torchvision", "onnx", "onnxslim"]
try:
    datas += collect_data_files("ultralytics")
    hiddenimports += ["ultralytics", *collect_submodules("ultralytics")]
except Exception:  # noqa: BLE001
    pass

# Project metadata so openfollow.__version__ resolves via importlib.metadata.
try:
    datas += copy_metadata("openfollow")
except Exception:  # noqa: BLE001
    pass

# openfollow package data: web templates / static / help markdown / templates.
datas += collect_data_files("openfollow")

# Collect the whole openfollow package. Two runtime mechanisms import submodules
# in ways PyInstaller's static analysis can't see, so a static-import-only freeze
# silently drops them:
#   - video input plugins are discovered by walking the package
#     (pkgutil.iter_modules + importlib) - only avf / v4l2 are statically
#     imported elsewhere, so the rest (testpattern / rtsp / srt / ndi / rtp /
#     picam) would be missing and the registry can't resolve them;
#   - bottle templates run `% from openfollow.<mod> import ...` at render time
#     (labels, palette, units, osc.parser, osc.template, ...), so a missing
#     submodule turns the web UI into a 500 on first request.
hiddenimports += collect_submodules("openfollow")

# mido loads its MIDI backend (python-rtmidi) by dynamic import string, invisible
# to the static analysis; without these the MIDI input discovery raises
# ModuleNotFoundError and MIDI control is unavailable.
hiddenimports += ["rtmidi", *collect_submodules("mido.backends")]

# Bundle the first-run config seed and (when present) the default model.
datas += [(str(MACOS / "config.seed.toml"), ".")]
_default_model = BUILD / "models" / "yolov8n.onnx"
if _default_model.is_file():
    datas += [(str(_default_model), "models")]

a = Analysis(  # noqa: F821
    [str(MACOS / "launcher.py")],
    pathex=[str(REPO / "scripts"), str(REPO)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(MACOS / "runtime_hook.py")],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# PyInstaller dedups shared libs by basename, so an older libglib vendored by the
# cv2 / pygame wheels can win and shadow the newer Homebrew libglib the GObject
# stack needs (missing symbols like g_string_copy). build-dmg.sh prunes those
# vendored copies before the freeze so libglib resolves to Homebrew (relocated by
# PyInstaller). Guard against a regression where a vendored copy slips back in:
# every collected libglib must come from a Homebrew source, never a wheel's
# .dylibs dir.
for _name in ("libglib-2.0.0.dylib", "libharfbuzz.0.dylib"):
    _ents = [b for b in a.binaries if os.path.basename(b[0]) == _name]
    print(f"[openfollow.spec] {_name} sources: {[b[1] for b in _ents]}")
    if not _ents:
        raise SystemExit(f"openfollow.spec: no {_name} collected at all")
    if any("/.dylibs/" in (b[1] or "") for b in _ents):
        raise SystemExit(f"openfollow.spec: a vendored {_name} slipped in; prune it in build-dmg.sh")

# The gi Gst hook collects EVERY plugin from the (very heavy) Homebrew gstreamer
# install - 270+ plugins, incl. gst-plugins-rs ML/cloud ones (whisper, demucs,
# deepgram, ...) that embed a Python runtime. GStreamer dlopen/dlcloses every
# plugin during its startup registry scan, and one of those Python-embedding
# plugins' dlclose runs a matplotlib static destructor with the GIL released ->
# fatal abort in Gst.init(). OpenFollow only uses standard C plugins for video
# receive / display / detection, so keep an allowlist and drop the rest. This
# also cuts the plugin payload from ~160 MB to ~20 MB.
_GST_PLUGIN_ALLOW = frozenset(
    {
        # core / dataflow
        "libgstcoreelements.dylib", "libgstcoretracers.dylib", "libgstapp.dylib",
        "libgstplayback.dylib", "libgsttypefindfunctions.dylib", "libgstautodetect.dylib",
        "libgstpbtypes.dylib", "libgstswitchbin.dylib", "libgstdownloadbuffer.dylib",
        # video convert / scale / test / rate / filter / still-image freeze
        "libgstvideoconvertscale.dylib", "libgstvideoconvert.dylib", "libgstvideoscale.dylib",
        "libgstvideotestsrc.dylib", "libgstvideorate.dylib", "libgstvideofilter.dylib",
        "libgstimagefreeze.dylib",
        "libgstvideofiltersbad.dylib", "libgstvideocrop.dylib", "libgstvideobox.dylib",
        "libgstdeinterlace.dylib", "libgstcompositor.dylib", "libgstoverlaycomposition.dylib",
        "libgstcodecalpha.dylib", "libgstcodectimestamper.dylib",
        # display sink + macOS capture/decode
        "libgstgtk.dylib", "libgstapplemedia.dylib", "libgstosxaudio.dylib",
        # codecs / parsers / containers
        "libgstlibav.dylib", "libgstvideoparsersbad.dylib", "libgstaudioparsers.dylib",
        "libgstjpeg.dylib", "libgstpng.dylib", "libgstpnm.dylib",
        "libgstisomp4.dylib", "libgstmatroska.dylib", "libgstmpegtsdemux.dylib",
        "libgstmpegtsmux.dylib", "libgstmpegpsdemux.dylib", "libgstid3demux.dylib",
        "libgstid3tag.dylib", "libgsticydemux.dylib", "libgsttagdemux.dylib",
        "libgstapetag.dylib", "libgstxingmux.dylib", "libgstmultipart.dylib",
        "libgstmultifile.dylib", "libgstrawparse.dylib", "libgstencoding.dylib",
        "libgstsubparse.dylib", "libgstclosedcaption.dylib",
        # network: rtp / rtsp / udp / srt / tcp
        # NDI (libgstndi) is deliberately excluded - it pulls the proprietary NDI
        # SDK (libndi) into the bundle. See the libndi prune below.
        "libgstrtp.dylib", "libgstrtpmanager.dylib", "libgstrtsp.dylib",
        "libgstsdpelem.dylib", "libgstsrtp.dylib", "libgstsrt.dylib",
        "libgstudp.dylib", "libgsttcp.dylib", "libgstdtls.dylib",
        "libgstrtponvif.dylib",
        # audio (passthrough for SRT / RTSP)
        "libgstaudioconvert.dylib", "libgstaudioresample.dylib", "libgstaudiomixer.dylib",
        "libgstaudiofx.dylib", "libgstvolume.dylib", "libgstaudiotestsrc.dylib",
        "libgstinterleave.dylib", "libgstequalizer.dylib",
    }
)


def _is_gst_plugin(dest):
    base = os.path.basename(dest)
    # Plugins are libgst<name>.dylib under the plugin dir; the core shared libs
    # are libgst<name>-1.0.0.dylib in Frameworks/ root (left untouched).
    return "gst_plugins" in dest and base.startswith("libgst") and base.endswith(".dylib") and "-1.0.0" not in base


_gst_before = sum(1 for b in a.binaries if _is_gst_plugin(b[0]))
a.binaries = [b for b in a.binaries if not (_is_gst_plugin(b[0]) and os.path.basename(b[0]) not in _GST_PLUGIN_ALLOW)]
_gst_kept = sorted(os.path.basename(b[0]) for b in a.binaries if _is_gst_plugin(b[0]))
print(f"[openfollow.spec] gst plugins: {_gst_before} -> {len(_gst_kept)} kept")
_critical_gst = (
    "libgstgtk.dylib",
    "libgstapplemedia.dylib",
    "libgstvideoconvertscale.dylib",
    "libgstvideotestsrc.dylib",
    "libgstimagefreeze.dylib",
)
for _critical in _critical_gst:
    if _critical not in _gst_kept:
        raise SystemExit(f"openfollow.spec: critical gst plugin {_critical} was not collected/kept")

# NDI is intentionally not shipped on macOS: libndi is the proprietary NDI SDK,
# collected only as a dependency of the now-dropped libgstndi plugin. Drop it
# (the gst-plugin filter above doesn't, since libndi sits at Frameworks/ root and
# isn't a libgst* plugin). Without the ndisrc element NdiInput.is_available() is
# False, so the source picker hides NDI. Assert nothing NDI survives so a future
# transitive re-add fails the build instead of silently re-shipping the SDK.
_NDI_BLOCKED = ("libndi.dylib", "libgstndi.dylib")
a.binaries = [b for b in a.binaries if os.path.basename(b[0]) not in _NDI_BLOCKED]
a.datas = [d for d in a.datas if os.path.basename(d[0]) not in _NDI_BLOCKED]
_ndi_left = sorted({os.path.basename(x[0]) for x in (*a.binaries, *a.datas)} & set(_NDI_BLOCKED))
print(f"[openfollow.spec] NDI dylibs after prune: {_ndi_left or 'none'}")
if _ndi_left:
    raise SystemExit(f"openfollow.spec: NDI SDK still bundled ({', '.join(_ndi_left)}); prune it")

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OpenFollow",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="OpenFollow",
)

_icon = BUILD / "OpenFollow.icns"

app = BUNDLE(  # noqa: F821
    coll,
    name="OpenFollow.app",
    icon=str(_icon) if _icon.is_file() else None,
    bundle_identifier="app.openfollow.desktop",
    version=VERSION,
    info_plist={
        "CFBundleName": "OpenFollow",
        "CFBundleDisplayName": "OpenFollow",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
        "LSApplicationCategoryType": "public.app-category.video",
        "NSCameraUsageDescription": "OpenFollow uses connected cameras as a video source for tracking.",
        "NSMicrophoneUsageDescription": "OpenFollow may receive audio alongside NDI / AV video sources.",
        "NSLocalNetworkUsageDescription": (
            "OpenFollow discovers peers and sends PSN tracking data over your local network."
        ),
        "NSBonjourServices": ["_http._tcp"],
    },
)
