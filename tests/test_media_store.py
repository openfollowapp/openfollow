# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for the Media Gallery store.

The GStreamer seams (``_render_jpeg``, ``_probe_video``, ``_webp_supported``)
are monkeypatched, so the storage layout, id rules, hard caps, format allowlist,
and video-limit checks are all exercised without a live pipeline.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from openfollow.video import media_store as ms

pytestmark = pytest.mark.unit

# Leading bytes for each container the sniffer recognises (or rejects).
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 12
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
_WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 4
_WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 12
_GIF = b"GIF89a" + b"\x00" * 10


@pytest.fixture
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the store at a temp dir and default WebP support off."""
    media_dir = tmp_path / "media"
    monkeypatch.setattr(ms, "resolve_media_storage_path", lambda: media_dir)
    monkeypatch.setattr(ms, "_webp_supported", lambda: False)
    return media_dir


def _fake_render(source: Path, *, max_dim: int) -> bytes:
    return f"jpeg-{max_dim}".encode()


def _staged(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _valid_probe(**overrides: object) -> ms.VideoProbe:
    base = {"codec": "vp8", "width": 1280, "height": 720, "fps": 30.0, "duration_s": 10.0}
    base.update(overrides)
    return ms.VideoProbe(**base)  # type: ignore[arg-type]


# -- storage path -------------------------------------------------------------


def test_storage_path_uses_nvme_when_mounted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ms.os.path, "ismount", lambda p: p == ms._NVME_MOUNTPOINT)
    assert ms.resolve_media_storage_path() == Path(ms._NVME_MEDIA_STORAGE)


def test_storage_path_falls_back_to_workdir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ms.os.path, "ismount", lambda p: False)
    assert ms.resolve_media_storage_path().name == "media"


# -- id rules -----------------------------------------------------------------


@pytest.mark.parametrize(
    "media_id",
    [ms.DEFAULT_STAGE_ID, ms.DEFAULT_GREY_ID, "0123456789abcdef"],
)
def test_valid_ids(media_id: str) -> None:
    assert ms.is_valid_id(media_id)


@pytest.mark.parametrize(
    "media_id",
    [
        "..",
        "../etc/passwd",
        "a/b",
        "abc",
        "0123456789ABCDEF",  # uppercase not generated
        "0123456789abcde",  # 15 chars
        "0123456789abcdef0",  # 17 chars
        "",
        "default:bogus",
    ],
)
def test_invalid_ids(media_id: str) -> None:
    assert not ms.is_valid_id(media_id)


def test_defaults_are_read_only() -> None:
    assert ms.is_read_only(ms.DEFAULT_STAGE_ID)
    assert ms.is_read_only(ms.DEFAULT_GREY_ID)
    assert not ms.is_read_only("0123456789abcdef")


# -- listing / resolution -----------------------------------------------------


def test_list_media_defaults_only_when_empty(storage: Path) -> None:
    items = ms.list_media()
    assert [i.media_id for i in items] == [ms.DEFAULT_STAGE_ID, ms.DEFAULT_GREY_ID]
    assert all(i.read_only for i in items)


def test_resolve_defaults(storage: Path) -> None:
    stage = ms.resolve(ms.DEFAULT_STAGE_ID)
    grey = ms.resolve(ms.DEFAULT_GREY_ID)
    assert stage is not None and stage.kind == "stage" and stage.path is not None
    assert grey is not None and grey.kind == "grey" and grey.path is None


def test_resolve_unknown_and_invalid_return_none(storage: Path) -> None:
    assert ms.resolve("0123456789abcdef") is None  # well-formed but absent
    assert ms.resolve("../secret") is None  # traversal rejected by id rule


# -- image upload -------------------------------------------------------------


def test_save_uploaded_image_normalises_and_thumbnails(
    storage: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ms, "_render_jpeg", _fake_render)
    item = ms.save_uploaded_image(_staged(tmp_path, "u.png", _PNG))

    assert item.kind == "image" and not item.read_only
    main = storage / f"{item.media_id}.jpg"
    thumb = storage / f"{item.media_id}.thumb.jpg"
    assert main.read_bytes() == f"jpeg-{ms.IMAGE_MAX_DIM}".encode()  # normalised, not the raw upload
    assert thumb.read_bytes() == f"jpeg-{ms.THUMB_MAX_DIM}".encode()
    assert item in [i for i in ms.list_media() if not i.read_only] or ms.resolve(item.media_id) is not None


def test_save_uploaded_image_accepts_webp_only_when_supported(
    storage: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ms, "_render_jpeg", _fake_render)
    with pytest.raises(ms.MediaStoreError, match="Unsupported image format"):
        ms.save_uploaded_image(_staged(tmp_path, "a.webp", _WEBP))

    monkeypatch.setattr(ms, "_webp_supported", lambda: True)
    assert ms.save_uploaded_image(_staged(tmp_path, "b.webp", _WEBP)).kind == "image"


@pytest.mark.parametrize("data", [_GIF, _WEBM, b"not-an-image"])
def test_save_uploaded_image_rejects_disallowed_formats(storage: Path, tmp_path: Path, data: bytes) -> None:
    with pytest.raises(ms.MediaStoreError, match="Unsupported image format"):
        ms.save_uploaded_image(_staged(tmp_path, "x.bin", data))


def test_save_uploaded_image_rejects_oversize(storage: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ms, "MAX_IMAGE_UPLOAD_BYTES", 8)
    with pytest.raises(ms.MediaStoreError, match="Image too large"):
        ms.save_uploaded_image(_staged(tmp_path, "big.png", _PNG + b"x" * 32))


def test_save_uploaded_image_propagates_decode_failure(
    storage: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(source: Path, *, max_dim: int) -> bytes:
        raise ms.MediaStoreError("Could not decode media.")

    monkeypatch.setattr(ms, "_render_jpeg", boom)
    with pytest.raises(ms.MediaStoreError, match="decode"):
        ms.save_uploaded_image(_staged(tmp_path, "u.png", _PNG))


# -- capture ------------------------------------------------------------------


def test_save_captured_frame_stores_verbatim(storage: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ms, "_render_jpeg", _fake_render)
    payload = _JPEG + b"native-resolution-frame"
    item = ms.save_captured_frame(payload)

    assert item.kind == "image"
    # Capture keeps original resolution: the main file is the raw frame, untouched.
    assert (storage / f"{item.media_id}.jpg").read_bytes() == payload
    assert (storage / f"{item.media_id}.thumb.jpg").exists()


def test_save_captured_frame_rejects_non_jpeg(storage: Path) -> None:
    with pytest.raises(ms.MediaStoreError, match="not a JPEG"):
        ms.save_captured_frame(_PNG)


# -- video upload -------------------------------------------------------------


def test_save_uploaded_video_stores_verbatim(storage: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ms, "_probe_video", lambda src: _valid_probe())
    monkeypatch.setattr(ms, "_render_jpeg", _fake_render)
    staged = _staged(tmp_path, "clip.webm", _WEBM + b"vp8-payload")
    item = ms.save_uploaded_video(staged)

    assert item.kind == "video"
    assert (storage / f"{item.media_id}.webm").read_bytes() == _WEBM + b"vp8-payload"
    assert not staged.exists()  # moved into the store
    assert (storage / f"{item.media_id}.thumb.jpg").exists()


def test_save_uploaded_video_rejects_non_webm_container(storage: Path, tmp_path: Path) -> None:
    with pytest.raises(ms.MediaStoreError, match="Unsupported video container"):
        ms.save_uploaded_video(_staged(tmp_path, "clip.webm", _JPEG))


def test_save_uploaded_video_rejects_oversize(storage: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ms, "MAX_VIDEO_UPLOAD_BYTES", 8)
    with pytest.raises(ms.MediaStoreError, match="Video too large"):
        ms.save_uploaded_video(_staged(tmp_path, "clip.webm", _WEBM + b"x" * 32))


# -- video limit rules --------------------------------------------------------


def test_validate_video_probe_accepts_within_limits() -> None:
    assert ms.validate_video_probe(_valid_probe(fps=30.4, duration_s=60.4)) is None


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"codec": "h264"}, "must be VP8"),
        ({"codec": ""}, "unknown"),
        ({"width": 3840, "height": 2160}, "resolution"),
        ({"width": 1921}, "resolution"),
        ({"fps": 60.0}, "frame rate"),
        ({"duration_s": 90.0}, "limit is 60"),
    ],
)
def test_validate_video_probe_rejects(overrides: dict[str, object], match: str) -> None:
    with pytest.raises(ms.MediaStoreError, match=match):
        ms.validate_video_probe(_valid_probe(**overrides))


def test_save_uploaded_video_rejects_bad_codec(storage: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ms, "_probe_video", lambda src: _valid_probe(codec="h264"))
    with pytest.raises(ms.MediaStoreError, match="must be VP8"):
        ms.save_uploaded_video(_staged(tmp_path, "clip.webm", _WEBM))


# -- capacity -----------------------------------------------------------------


def test_capacity_blocks_on_item_count(storage: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ms, "_render_jpeg", _fake_render)
    monkeypatch.setattr(ms, "MAX_ITEMS", 1)
    ms.save_uploaded_image(_staged(tmp_path, "one.png", _PNG))
    with pytest.raises(ms.MediaStoreError, match="full"):
        ms.save_uploaded_image(_staged(tmp_path, "two.png", _PNG))


def test_capacity_blocks_on_total_bytes(storage: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ms, "_render_jpeg", _fake_render)
    monkeypatch.setattr(ms, "MAX_TOTAL_BYTES", 4)  # smaller than any normalised payload
    with pytest.raises(ms.MediaStoreError, match="storage limit"):
        ms.save_uploaded_image(_staged(tmp_path, "one.png", _PNG))


# -- delete -------------------------------------------------------------------


def test_delete_removes_file_and_thumbnail(storage: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ms, "_render_jpeg", _fake_render)
    item = ms.save_uploaded_image(_staged(tmp_path, "u.png", _PNG))
    ms.delete(item.media_id)
    assert not (storage / f"{item.media_id}.jpg").exists()
    assert not (storage / f"{item.media_id}.thumb.jpg").exists()
    assert ms.resolve(item.media_id) is None


def test_delete_refuses_defaults(storage: Path) -> None:
    with pytest.raises(ms.MediaStoreError, match="cannot be deleted"):
        ms.delete(ms.DEFAULT_STAGE_ID)


def test_delete_unknown_raises(storage: Path) -> None:
    with pytest.raises(ms.MediaStoreError, match="not found"):
        ms.delete("0123456789abcdef")


# -- download / thumb paths ---------------------------------------------------


def test_download_path_excludes_defaults(storage: Path) -> None:
    assert ms.download_path(ms.DEFAULT_STAGE_ID) is None
    assert ms.download_path(ms.DEFAULT_GREY_ID) is None


def test_download_and_thumb_paths_for_user_media(
    storage: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ms, "_render_jpeg", _fake_render)
    item = ms.save_uploaded_image(_staged(tmp_path, "u.png", _PNG))
    assert ms.download_path(item.media_id) == storage / f"{item.media_id}.jpg"
    assert ms.thumb_path(item.media_id) == storage / f"{item.media_id}.thumb.jpg"
    assert ms.thumb_path(ms.DEFAULT_STAGE_ID) is None


# -- listing order ------------------------------------------------------------


def test_list_media_orders_user_files_newest_first(
    storage: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ms, "_render_jpeg", _fake_render)
    first = ms.save_uploaded_image(_staged(tmp_path, "a.png", _PNG))
    second = ms.save_uploaded_image(_staged(tmp_path, "b.png", _PNG))
    os.utime(storage / f"{first.media_id}.jpg", (1000, 1000))
    os.utime(storage / f"{second.media_id}.jpg", (2000, 2000))

    user_ids = [i.media_id for i in ms.list_media() if not i.read_only]
    assert user_ids == [second.media_id, first.media_id]


# -- stage asset fallback -----------------------------------------------------


def test_stage_asset_path_prefers_jpg_then_svg_then_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    jpg = tmp_path / "s.jpg"
    svg = tmp_path / "s.svg"
    monkeypatch.setattr(ms, "_STAGE_ASSET_JPG", jpg)
    monkeypatch.setattr(ms, "_STAGE_ASSET_SVG", svg)

    assert ms._stage_asset_path() is None
    svg.write_bytes(b"x")
    assert ms._stage_asset_path() == svg
    jpg.write_bytes(b"x")
    assert ms._stage_asset_path() == jpg


# -- id collision -------------------------------------------------------------


def test_new_user_id_retries_on_collision(storage: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "aaaaaaaaaaaaaaaa.jpg").write_bytes(b"x")
    ids = iter(["aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"])
    monkeypatch.setattr(ms.secrets, "token_hex", lambda n: next(ids))
    assert ms._new_user_id(storage) == "bbbbbbbbbbbbbbbb"


# -- best-effort thumbnail ----------------------------------------------------


def test_thumbnail_failure_does_not_orphan_main_file(storage: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(source: Path, *, max_dim: int) -> bytes:
        raise ms.MediaStoreError("thumb render failed")

    monkeypatch.setattr(ms, "_render_jpeg", boom)
    item = ms.save_captured_frame(_JPEG + b"frame")  # only renders the thumbnail

    assert (storage / f"{item.media_id}.jpg").exists()  # main survives
    assert not (storage / f"{item.media_id}.thumb.jpg").exists()  # thumb skipped, not orphaned


# -- format sniffer -----------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (_JPEG, "jpeg"),
        (_PNG, "png"),
        (_WEBP, "webp"),
        (_WEBM, "webm"),
        (_GIF, None),
        (b"BM" + b"\x00" * 14, None),  # BMP
        (b"", None),
    ],
)
def test_sniff_format(data: bytes, expected: str | None) -> None:
    assert ms._sniff_format(data) == expected
