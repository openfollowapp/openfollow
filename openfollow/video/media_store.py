# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Device-local media store for the Media Gallery video source.

Holds operator-uploaded still images and short looping clips, plus the two
read-only bundled defaults (Stage, Grey). This module is the single source of
truth for the storage layout, id rules, hard caps, and upload validation shared
by the gallery plugin (``video/inputs/testpattern.py``) and the web routes.

The storage directory is device-local *by construction* (resolved per host,
never a config field), so nothing here ever crosses machines.

Heavy media operations (decode, scale, encode, probe) run through GStreamer
behind the ``_render_jpeg`` / ``_probe_video`` / ``_webp_supported`` seams.
Everything security-relevant – the format allowlist, the byte-size caps, the
id / traversal rules, and the codec / dimension / fps / duration checks – is
pure Python so it is fully exercised without a live pipeline.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# -- Reserved defaults --------------------------------------------------------

DEFAULT_STAGE_ID = "default:stage"
DEFAULT_GREY_ID = "default:grey"
# Ordered for display: defaults always render first in the grid.
DEFAULT_IDS: tuple[str, ...] = (DEFAULT_STAGE_ID, DEFAULT_GREY_ID)
# The AppConfig default for ``testpattern_selected_media``.
DEFAULT_SELECTED_MEDIA = DEFAULT_STAGE_ID

# -- Storage location ---------------------------------------------------------

# Mirrors ``resolve_detection_storage_path``: the NVMe location when the drive
# is mounted, otherwise a folder under the working directory. Device-local by
# construction, so it is never stored in config and never exported.
_NVME_MOUNTPOINT = "/mnt/nvme"
_NVME_MEDIA_STORAGE = "/mnt/nvme/openfollow/media"
_LOCAL_STORAGE_DIRNAME = "media"

_ASSET_DIR = Path(__file__).parent / "inputs" / "assets"
_STAGE_ASSET_JPG = _ASSET_DIR / "stage_default.jpg"
_STAGE_ASSET_SVG = _ASSET_DIR / "stage_default.svg"

# -- Hard caps ----------------------------------------------------------------

# Gallery total budget – bundled defaults are excluded from both counts. Sized
# to stay comfortable on an SD-card-only Pi while holding a useful library.
MAX_ITEMS = 100
MAX_TOTAL_BYTES = 1024 * 1024 * 1024  # 1 GiB

# Per-upload ceilings. Images are normalised down on store, so the upload cap
# only bounds the transient spool; the clip cap is the issue's ~64 MB budget.
MAX_IMAGE_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_VIDEO_UPLOAD_BYTES = 64 * 1024 * 1024

# -- Image normalisation ------------------------------------------------------

IMAGE_MAX_DIM = 1920  # longest side for normalised uploads
THUMB_MAX_DIM = 320  # longest side for grid thumbnails
JPEG_QUALITY = 85

# -- Video limits -------------------------------------------------------------

VIDEO_CODEC = "vp8"
VIDEO_MAX_WIDTH = 1920
VIDEO_MAX_HEIGHT = 1080
VIDEO_MAX_FPS = 30
VIDEO_MAX_DURATION_S = 60
# Tolerances absorb container rounding (e.g. 30000/1001 fps, 60.04 s duration).
_FPS_TOLERANCE = 0.5
_DURATION_TOLERANCE_S = 0.5

# -- Id / file conventions ----------------------------------------------------

# User media lands as ``<id>.<ext>`` with the thumbnail as ``<id>.thumb.jpg``.
_USER_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_IMAGE_EXT = "jpg"
_VIDEO_EXT = "webm"
_THUMB_SUFFIX = ".thumb.jpg"
_USER_FILE_RE = re.compile(r"^(?P<id>[0-9a-f]{16})\.(?P<ext>jpg|webm)$")


class MediaStoreError(Exception):
    """A media operation failed in a way the operator should see.

    ``message`` is safe to surface verbatim in the web UI – it names the
    specific limit or format problem and never leaks a filesystem path.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class MediaItem:
    """A gallery entry – a bundled default or a stored user file."""

    media_id: str
    kind: str  # "image" | "video" | "grey" | "stage"
    read_only: bool
    path: Path | None  # bundled-synthetic (grey) has no backing file
    size_bytes: int
    label: str


@dataclass(frozen=True)
class VideoProbe:
    """Result of probing an uploaded clip (one video stream)."""

    codec: str
    width: int
    height: int
    fps: float
    duration_s: float


# -- Storage path -------------------------------------------------------------


def resolve_media_storage_path() -> Path:
    """Return the device-local media directory (does not create it).

    NVMe location when ``/mnt/nvme`` is a real mountpoint, otherwise a ``media``
    folder under the current working directory. Idempotent and host-specific.
    """
    if os.path.ismount(_NVME_MOUNTPOINT):
        return Path(_NVME_MEDIA_STORAGE)
    return Path.cwd() / _LOCAL_STORAGE_DIRNAME


def _ensure_storage_dir() -> Path:
    path = resolve_media_storage_path()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _stage_asset_path() -> Path | None:
    """Backing file for the Stage default (JPG preferred, SVG fallback)."""
    if _STAGE_ASSET_JPG.exists():
        return _STAGE_ASSET_JPG
    if _STAGE_ASSET_SVG.exists():
        return _STAGE_ASSET_SVG
    return None


# -- Id rules -----------------------------------------------------------------


def is_default(media_id: str) -> bool:
    """True for a reserved bundled-default id."""
    return media_id in DEFAULT_IDS


def is_read_only(media_id: str) -> bool:
    """True when the id may not be deleted, downloaded, or overwritten."""
    return is_default(media_id)


def is_valid_id(media_id: str) -> bool:
    """True for a reserved default or a well-formed user id.

    Rejects traversal (``..``, slashes) and any non-canonical token – the only
    user ids that exist are the 16-hex names this module generates.
    """
    return is_default(media_id) or bool(_USER_ID_RE.match(media_id))


def _new_user_id(storage: Path) -> str:
    """A fresh 16-hex id with no existing file collision."""
    while True:
        candidate = secrets.token_hex(8)
        if not any((storage / f"{candidate}.{ext}").exists() for ext in (_IMAGE_EXT, _VIDEO_EXT)):
            return candidate


# -- Listing / resolution -----------------------------------------------------


def _default_item(media_id: str) -> MediaItem:
    if media_id == DEFAULT_GREY_ID:
        return MediaItem(DEFAULT_GREY_ID, "grey", True, None, 0, "Grey")
    asset = _stage_asset_path()
    size = asset.stat().st_size if asset is not None else 0
    return MediaItem(DEFAULT_STAGE_ID, "stage", True, asset, size, "Stage")


def _user_item(path: Path) -> MediaItem:
    match = _USER_FILE_RE.match(path.name)
    if match is None:  # pragma: no cover - callers pass only regex-matched paths
        raise MediaStoreError("Not a gallery media file.")
    ext = match.group("ext")
    kind = "video" if ext == _VIDEO_EXT else "image"
    return MediaItem(match.group("id"), kind, False, path, path.stat().st_size, match.group("id"))


def _user_media_files(storage: Path) -> list[Path]:
    if not storage.is_dir():
        return []
    files = [f for f in storage.iterdir() if f.is_file() and _USER_FILE_RE.match(f.name)]
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return files


def list_media() -> list[MediaItem]:
    """Every gallery entry: the two defaults first, then user media newest-first."""
    items = [_default_item(mid) for mid in DEFAULT_IDS]
    items.extend(_user_item(f) for f in _user_media_files(resolve_media_storage_path()))
    return items


def resolve(media_id: str) -> MediaItem | None:
    """Look up an id. Returns ``None`` when it cannot be resolved.

    A ``None`` result is the caller's cue to fall back to the Stage default –
    this is what makes a hand-edited / deleted selection degrade silently.
    """
    if not is_valid_id(media_id):
        return None
    if is_default(media_id):
        return _default_item(media_id)
    storage = resolve_media_storage_path()
    for ext in (_IMAGE_EXT, _VIDEO_EXT):
        path = storage / f"{media_id}.{ext}"
        if path.is_file():
            return _user_item(path)
    return None


def thumb_path(media_id: str) -> Path | None:
    """Stored thumbnail path for a user media id, if present."""
    if not _USER_ID_RE.match(media_id):
        return None
    path = resolve_media_storage_path() / f"{media_id}{_THUMB_SUFFIX}"
    return path if path.is_file() else None


def download_path(media_id: str) -> Path | None:
    """Path served by the download route. ``None`` for defaults (not downloadable)."""
    item = resolve(media_id)
    if item is None or item.read_only or item.path is None:
        return None
    return item.path


# -- Capacity -----------------------------------------------------------------


def _current_usage(storage: Path) -> tuple[int, int]:
    """(item count, total bytes) of user media – thumbnails and defaults excluded."""
    files = _user_media_files(storage)
    return len(files), sum(f.stat().st_size for f in files)


def _enforce_capacity(storage: Path, incoming_bytes: int) -> None:
    count, total = _current_usage(storage)
    if count + 1 > MAX_ITEMS:
        raise MediaStoreError(f"Gallery is full ({MAX_ITEMS} items). Delete some media first.")
    if total + incoming_bytes > MAX_TOTAL_BYTES:
        limit_mb = MAX_TOTAL_BYTES // (1024 * 1024)
        raise MediaStoreError(f"Gallery storage limit reached ({limit_mb} MB). Delete some media first.")


# -- Format sniffing ----------------------------------------------------------


def _sniff_format(header: bytes) -> str | None:
    """Identify a media container from its leading bytes.

    Only the allowlisted formats are recognised; everything else (GIF, BMP,
    TIFF, SVG, ...) returns ``None`` and is rejected. Never trust the upload's
    extension or Content-Type.
    """
    if header[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if header[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp"
    if header[:4] == b"\x1a\x45\xdf\xa3":  # EBML header (Matroska / WebM)
        return "webm"
    return None


def _read_header(path: Path, size: int = 16) -> bytes:
    with path.open("rb") as fh:
        return fh.read(size)


# -- Video validation rules (pure) --------------------------------------------


def validate_video_probe(probe: VideoProbe) -> None:
    """Raise ``MediaStoreError`` if a probed clip violates the gallery limits."""
    if probe.codec != VIDEO_CODEC:
        got = probe.codec or "unknown"
        raise MediaStoreError(f"Video must be VP8 in WebM (got {got}). See the help drawer for an ffmpeg recipe.")
    if probe.width > VIDEO_MAX_WIDTH or probe.height > VIDEO_MAX_HEIGHT:
        raise MediaStoreError(
            f"Video resolution {probe.width}x{probe.height} exceeds the {VIDEO_MAX_WIDTH}x{VIDEO_MAX_HEIGHT} limit."
        )
    if probe.fps > VIDEO_MAX_FPS + _FPS_TOLERANCE:
        raise MediaStoreError(f"Video frame rate {probe.fps:.0f} fps exceeds the {VIDEO_MAX_FPS} fps limit.")
    if probe.duration_s > VIDEO_MAX_DURATION_S + _DURATION_TOLERANCE_S:
        raise MediaStoreError(f"Video is {probe.duration_s:.0f} s; the limit is {VIDEO_MAX_DURATION_S} s.")


# -- Save paths ---------------------------------------------------------------


def _write_atomic(dest: Path, data: bytes) -> None:
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def _write_thumb(storage: Path, media_id: str, source: Path) -> None:
    """Render and store the grid thumbnail for a just-saved media file.

    A thumbnail failure must not orphan the main file, so it is best-effort:
    the tile simply renders without a thumbnail until a later refresh.
    """
    try:
        data = _render_jpeg(source, max_dim=THUMB_MAX_DIM)
    except MediaStoreError:
        logger.warning("Thumbnail render failed for %s", media_id)
        return
    _write_atomic(storage / f"{media_id}{_THUMB_SUFFIX}", data)


def save_uploaded_image(staged: Path) -> MediaItem:
    """Validate, normalise, and store an uploaded still image.

    ``staged`` is the raw upload on disk (the caller spools and discards it).
    On success the image is decoded, scaled to ``IMAGE_MAX_DIM``, and re-encoded
    to JPEG; the original staged file is left for the caller to remove.
    """
    size = staged.stat().st_size
    if size > MAX_IMAGE_UPLOAD_BYTES:
        limit_mb = MAX_IMAGE_UPLOAD_BYTES // (1024 * 1024)
        raise MediaStoreError(f"Image too large (max {limit_mb} MB).")
    fmt = _sniff_format(_read_header(staged))
    allowed = {"jpeg", "png"} | ({"webp"} if _webp_supported() else set())
    if fmt not in allowed:
        raise MediaStoreError("Unsupported image format. Use JPEG, PNG, or WebP.")

    storage = _ensure_storage_dir()
    normalised = _render_jpeg(staged, max_dim=IMAGE_MAX_DIM)
    _enforce_capacity(storage, len(normalised))
    media_id = _new_user_id(storage)
    dest = storage / f"{media_id}.{_IMAGE_EXT}"
    _write_atomic(dest, normalised)
    _write_thumb(storage, media_id, dest)
    return _user_item(dest)


def save_captured_frame(jpeg_bytes: bytes) -> MediaItem:
    """Store a clean frame captured from a live source, verbatim.

    Capture keeps the source's native resolution (no downscale); the bytes are
    already JPEG (from the snapshot provider). Only a thumbnail is derived.
    """
    if _sniff_format(jpeg_bytes[:16]) != "jpeg":
        raise MediaStoreError("Captured frame is not a JPEG.")
    storage = _ensure_storage_dir()
    _enforce_capacity(storage, len(jpeg_bytes))
    media_id = _new_user_id(storage)
    dest = storage / f"{media_id}.{_IMAGE_EXT}"
    _write_atomic(dest, jpeg_bytes)
    _write_thumb(storage, media_id, dest)
    return _user_item(dest)


def save_uploaded_video(staged: Path) -> MediaItem:
    """Validate and store an uploaded VP8/WebM clip verbatim (no transcode).

    ``staged`` is moved into the store on success, so it must live on a path the
    caller no longer owns afterwards.
    """
    size = staged.stat().st_size
    if size > MAX_VIDEO_UPLOAD_BYTES:
        limit_mb = MAX_VIDEO_UPLOAD_BYTES // (1024 * 1024)
        raise MediaStoreError(f"Video too large (max {limit_mb} MB).")
    if _sniff_format(_read_header(staged)) != "webm":
        raise MediaStoreError("Unsupported video container. Use WebM (VP8).")

    validate_video_probe(_probe_video(staged))

    storage = _ensure_storage_dir()
    _enforce_capacity(storage, size)
    media_id = _new_user_id(storage)
    dest = storage / f"{media_id}.{_VIDEO_EXT}"
    shutil.move(str(staged), str(dest))
    _write_thumb(storage, media_id, dest)
    return _user_item(dest)


def delete(media_id: str) -> None:
    """Remove a user media file and its thumbnail. Refuses defaults / unknowns."""
    if is_read_only(media_id):
        raise MediaStoreError("Default media cannot be deleted.")
    item = resolve(media_id)
    if item is None or item.path is None:
        raise MediaStoreError("Media not found.")
    item.path.unlink(missing_ok=True)
    (resolve_media_storage_path() / f"{media_id}{_THUMB_SUFFIX}").unlink(missing_ok=True)


# -- GStreamer seams ----------------------------------------------------------
#
# These wrap the only GStreamer-dependent work. Tests monkeypatch them; the real
# pipelines below are verified on-device. Each raises ``MediaStoreError`` on
# failure so callers handle media problems uniformly.


def _webp_supported() -> bool:  # pragma: no cover - GStreamer, verified on-device
    """Whether this host can decode WebP (the ``webpdec`` element is present)."""
    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        return Gst.ElementFactory.find("webpdec") is not None
    except Exception:
        return False


def _render_jpeg(source: Path, *, max_dim: int) -> bytes:  # pragma: no cover - GStreamer, verified on-device
    """Decode the first frame of an image/video file to a scaled JPEG.

    Aspect ratio is preserved; the longest side is capped at ``max_dim`` and the
    frame is never upscaled (the capsfilter range lets ``videoscale`` keep a
    smaller source as-is). Used for image normalisation and for image/video
    thumbnails alike – ``decodebin`` handles both.
    """
    pipeline = None
    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)

        pipeline = Gst.parse_launch(
            "filesrc name=src ! decodebin ! videoconvert ! videoscale ! "
            f"video/x-raw,width=(int)[1,{max_dim}],height=(int)[1,{max_dim}],pixel-aspect-ratio=(fraction)1/1 ! "
            "jpegenc ! appsink name=sink"
        )
        pipeline.get_by_name("src").set_property("location", str(source))
        sink = pipeline.get_by_name("sink")

        pipeline.set_state(Gst.State.PAUSED)
        pipeline.get_state(5 * Gst.SECOND)  # block until preroll / error
        sample = sink.emit("pull-preroll")
        if sample is None:
            raise MediaStoreError("Could not decode media.")
        buf = sample.get_buffer()
        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            raise MediaStoreError("Could not read decoded frame.")
        try:
            return bytes(mapinfo.data)
        finally:
            buf.unmap(mapinfo)
    except MediaStoreError:
        raise
    except Exception as exc:
        raise MediaStoreError("Could not decode media.") from exc
    finally:
        if pipeline is not None:
            try:
                pipeline.set_state(Gst.State.NULL)
            except Exception:
                pass


def _probe_video(source: Path) -> VideoProbe:  # pragma: no cover - GStreamer, verified on-device
    """Read codec / dimensions / fps / duration from a clip via GstDiscoverer."""
    try:
        import gi

        gi.require_version("Gst", "1.0")
        gi.require_version("GstPbutils", "1.0")
        from gi.repository import Gst, GstPbutils

        Gst.init(None)
        discoverer = GstPbutils.Discoverer.new(5 * Gst.SECOND)
        info = discoverer.discover_uri(source.absolute().as_uri())
        streams = info.get_video_streams()
        if not streams:
            raise MediaStoreError("No video stream found in clip.")
        stream = streams[0]
        struct = stream.get_caps().get_structure(0)
        codec = struct.get_name().removeprefix("video/x-")
        denom = stream.get_framerate_denom() or 1
        fps = stream.get_framerate_num() / denom
        duration_s = info.get_duration() / Gst.SECOND
        return VideoProbe(codec, stream.get_width(), stream.get_height(), fps, duration_s)
    except MediaStoreError:
        raise
    except Exception as exc:
        raise MediaStoreError("Could not read video metadata.") from exc
