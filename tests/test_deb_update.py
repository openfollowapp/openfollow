# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the GitHub-releases update installer + signed update bundle."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import urllib.error
from http.client import HTTPMessage
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from openfollow.runtime.deb_update import (
    _assert_download_url,
    _deb_arch,
    _download_bundle,
    _fetch_latest_release,
    _find_bundle_asset,
    _is_newer,
    _is_prerelease_version,
    _remove_staged,
    _verify_bundle_checksum,
    _verify_signature,
    check_for_update,
    read_deb_control,
    run_deb_update,
    run_local_update,
    validate_uploaded_deb,
    verify_and_extract_bundle,
)

pytestmark = pytest.mark.unit

_OPENSSL = shutil.which("openssl")
requires_openssl = pytest.mark.skipif(_OPENSSL is None, reason="openssl not available")


# ---------------------------------------------------------------------------
# Signed-bundle test helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rsa_keypair(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """An ephemeral RSA keypair (2048-bit for test speed), generated once per module.

    The tests only read priv/pub (to sign bundles into their own tmp dirs), so a
    single shared keypair is safe and skips a per-test ``openssl genpkey``.
    """
    tmp_path = tmp_path_factory.mktemp("rsa_keypair")
    priv = tmp_path / "priv.pem"
    pub = tmp_path / "pub.pem"
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(priv)],
        check=True,
        capture_output=True,
    )
    subprocess.run(["openssl", "pkey", "-in", str(priv), "-pubout", "-out", str(pub)], check=True, capture_output=True)
    return priv, pub


def _sign(priv: Path, data: bytes) -> bytes:
    """Detached openssl RSA-SHA256 signature over ``data``."""
    proc = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", str(priv)],
        input=data,
        capture_output=True,
        check=True,
    )
    return proc.stdout


def _sha256sums(deb_name: str, deb_bytes: bytes) -> bytes:
    return f"{hashlib.sha256(deb_bytes).hexdigest()}  {deb_name}\n".encode()


def _write_tar(path: Path, members: list[tuple[str, bytes]]) -> None:
    with tarfile.open(path, "w:") as tar:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def _build_bundle(
    tmp_path: Any,
    priv: Path,
    *,
    deb_name: str = "openfollow_0.2.4_arm64.deb",
    deb_bytes: bytes = b"the-package-bytes",
    sums: bytes | None = None,
    sig: bytes | None = None,
    omit: str | None = None,
    extra: tuple[str, bytes] | None = None,
) -> Path:
    """Build a (by default valid) signed bundle; kwargs craft failure cases."""
    sums_bytes = sums if sums is not None else _sha256sums(deb_name, deb_bytes)
    sig_bytes = sig if sig is not None else _sign(priv, sums_bytes)
    members = [
        (deb_name, deb_bytes),
        ("SHA256SUMS", sums_bytes),
        ("SHA256SUMS.sig", sig_bytes),
    ]
    members = [m for m in members if m[0] != omit]
    if extra is not None:
        members.append(extra)
    bundle = tmp_path / "openfollow_0.2.4_arm64.ofupdate"
    _write_tar(bundle, members)
    return bundle


# ---------------------------------------------------------------------------
# _is_newer
# ---------------------------------------------------------------------------


class TestIsNewer:
    def test_newer_patch_returns_true(self) -> None:
        assert _is_newer("0.2.4", "0.2.3") is True

    def test_same_version_returns_false(self) -> None:
        assert _is_newer("0.2.3", "0.2.3") is False

    def test_older_returns_false(self) -> None:
        assert _is_newer("0.2.2", "0.2.3") is False

    def test_pre_release_older_than_release(self) -> None:
        assert _is_newer("0.2.3", "0.2.3rc6") is True

    def test_pre_release_to_pre_release(self) -> None:
        assert _is_newer("0.2.3rc7", "0.2.3rc6") is True

    def test_invalid_version_string_falls_back_to_inequality(self) -> None:
        result = _is_newer("not-a-version", "0.2.3")
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# _deb_arch
# ---------------------------------------------------------------------------


class TestDebArch:
    def test_aarch64_maps_to_arm64(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("platform.machine", lambda: "aarch64")
        assert _deb_arch() == "arm64"

    def test_x86_64_maps_to_amd64(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("platform.machine", lambda: "x86_64")
        assert _deb_arch() == "amd64"

    def test_unknown_machine_passes_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("platform.machine", lambda: "riscv64")
        assert _deb_arch() == "riscv64"


# ---------------------------------------------------------------------------
# _find_bundle_asset
# ---------------------------------------------------------------------------


class TestFindBundleAsset:
    def _assets(self, names: list[str]) -> list[dict]:
        return [{"name": n, "browser_download_url": f"https://github.com/example/{n}"} for n in names]

    def test_finds_arm64_bundle(self) -> None:
        assets = self._assets(["openfollow_0.2.4_arm64.ofupdate", "checksums.txt"])
        url = _find_bundle_asset(assets, "arm64")
        assert url.endswith("openfollow_0.2.4_arm64.ofupdate")

    def test_raises_when_no_matching_asset(self) -> None:
        assets = self._assets(["openfollow_0.2.4_amd64.ofupdate"])
        with pytest.raises(RuntimeError, match="arm64"):
            _find_bundle_asset(assets, "arm64")

    def test_raises_when_only_raw_deb_present(self) -> None:
        # A release with only the raw .deb (no signed bundle) is not installable.
        assets = self._assets(["openfollow_0.2.4_arm64.deb"])
        with pytest.raises(RuntimeError):
            _find_bundle_asset(assets, "arm64")

    def test_raises_on_empty_assets(self) -> None:
        with pytest.raises(RuntimeError):
            _find_bundle_asset([], "arm64")

    def test_ignores_assets_with_missing_url(self) -> None:
        assets = [{"name": "openfollow_0.2.4_arm64.ofupdate", "browser_download_url": ""}]
        with pytest.raises(RuntimeError):
            _find_bundle_asset(assets, "arm64")

    def test_falls_back_to_all_bundle(self) -> None:
        assets = self._assets(["openfollow_0.2.4_all.ofupdate", "checksums.txt"])
        url = _find_bundle_asset(assets, "arm64")
        assert url.endswith("openfollow_0.2.4_all.ofupdate")

    def test_prefers_arch_over_all(self) -> None:
        assets = self._assets(["openfollow_0.2.4_all.ofupdate", "openfollow_0.2.4_arm64.ofupdate"])
        url = _find_bundle_asset(assets, "arm64")
        assert url.endswith("_arm64.ofupdate")


# ---------------------------------------------------------------------------
# _assert_download_url
# ---------------------------------------------------------------------------


class TestAssertDownloadUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/owner/repo/releases/download/v1/x.ofupdate",
            "https://objects.githubusercontent.com/abc/x",
            "https://release-assets.githubusercontent.com/abc/x",
        ],
    )
    def test_allows_https_github(self, url: str) -> None:
        _assert_download_url(url)  # no raise

    @pytest.mark.parametrize(
        "url",
        [
            "http://github.com/owner/repo/x.ofupdate",  # not https
            "https://evil.com/x.ofupdate",  # wrong host
            "https://github.com.evil.com/x",  # suffix-spoof host
            "ftp://github.com/x",  # wrong scheme
        ],
    )
    def test_rejects_untrusted(self, url: str) -> None:
        with pytest.raises(RuntimeError, match="untrusted URL"):
            _assert_download_url(url)


# ---------------------------------------------------------------------------
# _fetch_latest_release
# ---------------------------------------------------------------------------


def _make_response(body: bytes, status: int = 200) -> Any:
    """Build a minimal urllib response-like object."""
    resp = MagicMock()
    resp.read.return_value = body
    resp.headers = HTTPMessage()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestFetchLatestRelease:
    def test_skips_prereleases_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Newest entry is a pre-release; stable-only default returns the
        # newest *stable* release below it.
        payload = [
            {"tag_name": "v0.2.4", "assets": [], "draft": False, "prerelease": True},
            {"tag_name": "v0.2.3", "assets": [], "draft": False, "prerelease": False},
        ]
        resp = _make_response(json.dumps(payload).encode())
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: resp)
        result = _fetch_latest_release("owner/repo")
        assert result["tag_name"] == "v0.2.3"

    def test_includes_prereleases_when_opted_in(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = [
            {"tag_name": "v0.2.4", "assets": [], "draft": False, "prerelease": True},
            {"tag_name": "v0.2.3", "assets": [], "draft": False, "prerelease": False},
        ]
        resp = _make_response(json.dumps(payload).encode())
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: resp)
        result = _fetch_latest_release("owner/repo", include_prereleases=True)
        assert result["tag_name"] == "v0.2.4"

    def test_returns_newest_stable_when_all_stable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = [
            {"tag_name": "v0.2.4", "assets": [], "draft": False, "prerelease": False},
            {"tag_name": "v0.2.3", "assets": [], "draft": False, "prerelease": False},
        ]
        resp = _make_response(json.dumps(payload).encode())
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: resp)
        assert _fetch_latest_release("owner/repo")["tag_name"] == "v0.2.4"

    def test_raises_when_only_prereleases_and_stable_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = [{"tag_name": "v0.3.0rc1", "assets": [], "draft": False, "prerelease": True}]
        resp = _make_response(json.dumps(payload).encode())
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: resp)
        with pytest.raises(RuntimeError, match="No published releases"):
            _fetch_latest_release("owner/repo")

    def test_skips_drafts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = [
            {"tag_name": "v0.3.0-draft", "assets": [], "draft": True},
            {"tag_name": "v0.2.4", "assets": [], "draft": False},
        ]
        resp = _make_response(json.dumps(payload).encode())
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: resp)
        result = _fetch_latest_release("owner/repo")
        assert result["tag_name"] == "v0.2.4"

    def test_raises_on_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(*a: Any, **kw: Any) -> None:
            raise urllib.error.HTTPError(url="u", code=404, msg="Not Found", hdrs=HTTPMessage(), fp=None)

        monkeypatch.setattr("urllib.request.urlopen", _raise)
        with pytest.raises(RuntimeError, match="404"):
            _fetch_latest_release("owner/repo")

    def test_raises_on_url_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(*a: Any, **kw: Any) -> None:
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr("urllib.request.urlopen", _raise)
        with pytest.raises(RuntimeError, match="connection refused"):
            _fetch_latest_release("owner/repo")

    def test_raises_on_invalid_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        resp = _make_response(b"not json")
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: resp)
        with pytest.raises(RuntimeError, match="JSON"):
            _fetch_latest_release("owner/repo")

    def test_raises_when_not_a_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        resp = _make_response(json.dumps({"message": "Not Found"}).encode())
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: resp)
        with pytest.raises(RuntimeError, match="expected a list"):
            _fetch_latest_release("owner/repo")

    def test_raises_when_no_published_releases(self, monkeypatch: pytest.MonkeyPatch) -> None:
        resp = _make_response(json.dumps([{"draft": True, "tag_name": "v9"}]).encode())
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: resp)
        with pytest.raises(RuntimeError, match="No published releases"):
            _fetch_latest_release("owner/repo")


class TestCheckForUpdate:
    def test_reports_available_when_newer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "openfollow.runtime.deb_update._fetch_latest_release",
            lambda repo, **kw: {"tag_name": "v0.2.4", "assets": []},
        )
        info = check_for_update("owner/repo", "0.2.3")
        assert info == {"latest": "0.2.4", "current": "0.2.3", "available": True}

    def test_reports_not_available_when_same(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "openfollow.runtime.deb_update._fetch_latest_release",
            lambda repo, **kw: {"tag_name": "v0.2.3", "assets": []},
        )
        info = check_for_update("owner/repo", "0.2.3")
        assert info["available"] is False

    def test_strips_leading_v_from_tag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "openfollow.runtime.deb_update._fetch_latest_release",
            lambda repo, **kw: {"tag_name": "v1.0.0", "assets": []},
        )
        assert check_for_update("owner/repo", "0.9.0")["latest"] == "1.0.0"

    def test_forwards_include_prereleases_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def _fake(repo: str, **kw: Any) -> dict[str, Any]:
            captured.update(kw)
            return {"tag_name": "v1.0.0", "assets": []}

        monkeypatch.setattr("openfollow.runtime.deb_update._fetch_latest_release", _fake)
        check_for_update("owner/repo", "0.9.0", include_prereleases=True)
        assert captured == {"include_prereleases": True}

    def test_propagates_fetch_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(repo: str, **kw: Any) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr("openfollow.runtime.deb_update._fetch_latest_release", _raise)
        with pytest.raises(RuntimeError, match="boom"):
            check_for_update("owner/repo", "0.1.0")

    def test_prerelease_current_auto_includes_prereleases(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A device on an ``rcN`` build tracks pre-releases even with the flag
        # OFF, so it isn't stranded when the project ships updates as rcs.
        captured: dict[str, Any] = {}

        def _fake(repo: str, **kw: Any) -> dict[str, Any]:
            captured.update(kw)
            return {"tag_name": "v0.3.1rc3", "assets": []}

        monkeypatch.setattr("openfollow.runtime.deb_update._fetch_latest_release", _fake)
        info = check_for_update("owner/repo", "0.3.1rc2", include_prereleases=False)
        assert captured == {"include_prereleases": True}
        assert info["available"] is True  # rc3 > rc2

    def test_stable_current_stays_stable_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def _fake(repo: str, **kw: Any) -> dict[str, Any]:
            captured.update(kw)
            return {"tag_name": "v0.3.0", "assets": []}

        monkeypatch.setattr("openfollow.runtime.deb_update._fetch_latest_release", _fake)
        check_for_update("owner/repo", "0.3.1", include_prereleases=False)
        assert captured == {"include_prereleases": False}


class TestIsPrereleaseVersion:
    @pytest.mark.parametrize(
        "version,expected",
        [
            ("0.3.1rc2", True),
            ("0.3.1-rc2", True),  # normalised by packaging
            ("0.4.0a1", True),
            ("0.4.0b2", True),
            ("0.4.0.dev1", True),
            ("0.3.1", False),  # final – the bug case: an rc image stamped bare
            ("0.3.0", False),
            ("not-a-version", False),
            ("0.0.0+unknown", False),  # the dev fallback
        ],
    )
    def test_is_prerelease_version(self, version: str, expected: bool) -> None:
        assert _is_prerelease_version(version) is expected


# ---------------------------------------------------------------------------
# _download_bundle
# ---------------------------------------------------------------------------

_OK_URL = "https://objects.githubusercontent.com/openfollow_0.2.4_arm64.ofupdate"


def _download_response(data_chunks: list[bytes], headers: dict[str, str]) -> Any:
    resp = MagicMock()
    resp.headers = headers
    resp.read.side_effect = data_chunks
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestDownloadBundle:
    def test_writes_file_atomically(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        data = b"fake bundle content"
        resp = _download_response([data, b""], {"Content-Length": str(len(data))})
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: resp)

        dest = tmp_path / "openfollow-update-0.2.4_arm64.ofupdate"
        messages: list[str] = []
        _download_bundle(_OK_URL, str(dest), set_status=lambda state, msg="": messages.append(msg))
        assert dest.read_bytes() == data
        assert not (tmp_path / "openfollow-update-0.2.4_arm64.ofupdate.part").exists()

    def test_downloads_without_content_length(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        data = b"chunk-without-length"
        resp = _download_response([data, b""], {})  # no Content-Length
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: resp)

        dest = tmp_path / "out.ofupdate"
        messages: list[str] = []
        _download_bundle(_OK_URL, str(dest), set_status=lambda state, msg="": messages.append(msg))
        assert dest.read_bytes() == data
        assert messages == []  # no size known -> no progress lines

    def test_invalid_content_length_skips_progress(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        data = b"bundle content"
        resp = _download_response([data, b""], {"Content-Length": "not-a-number"})
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: resp)

        dest = tmp_path / "out.ofupdate"
        messages: list[str] = []
        _download_bundle(_OK_URL, str(dest), set_status=lambda state, msg="": messages.append(msg))
        assert dest.read_bytes() == data
        assert messages == []

    def test_truncated_download_raises_and_leaves_no_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        # Content-Length advertises 100 bytes; the stream ends early.
        resp = _download_response([b"short", b""], {"Content-Length": "100"})
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: resp)

        dest = tmp_path / "out.ofupdate"
        with pytest.raises(RuntimeError, match="incomplete"):
            _download_bundle(_OK_URL, str(dest), set_status=lambda state, msg="": None)
        assert not dest.exists()
        assert not (tmp_path / "out.ofupdate.part").exists()

    def test_oversized_content_length_rejected(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        import openfollow.runtime.deb_update as mod

        monkeypatch.setattr(mod, "_MAX_BUNDLE_BYTES", 10)
        resp = _download_response([b"x" * 50, b""], {"Content-Length": "100"})
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: resp)

        dest = tmp_path / "out.ofupdate"
        with pytest.raises(RuntimeError, match="larger than the maximum"):
            _download_bundle(_OK_URL, str(dest), set_status=lambda state, msg="": None)
        assert not dest.exists()

    def test_runaway_stream_aborted_at_cap(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        import openfollow.runtime.deb_update as mod

        monkeypatch.setattr(mod, "_MAX_BUNDLE_BYTES", 10)
        # No Content-Length, but the body blows past the cap.
        resp = _download_response([b"x" * 50, b""], {})
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: resp)

        dest = tmp_path / "out.ofupdate"
        with pytest.raises(RuntimeError, match="exceeded the maximum"):
            _download_bundle(_OK_URL, str(dest), set_status=lambda state, msg="": None)
        assert not dest.exists()

    def test_rejects_untrusted_url_before_fetch(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        called = {"n": 0}

        def _spy(*a: Any, **kw: Any) -> None:
            called["n"] += 1

        monkeypatch.setattr("urllib.request.urlopen", _spy)
        with pytest.raises(RuntimeError, match="untrusted URL"):
            _download_bundle("https://evil.com/x.ofupdate", str(tmp_path / "o"), set_status=lambda *a, **k: None)
        assert called["n"] == 0  # never reached the network

    def test_raises_on_url_error(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        def _raise(*a: Any, **kw: Any) -> None:
            raise urllib.error.URLError("timeout")

        monkeypatch.setattr("urllib.request.urlopen", _raise)
        with pytest.raises(RuntimeError, match="Download failed"):
            _download_bundle(_OK_URL, str(tmp_path / "out.ofupdate"), set_status=lambda state, msg="": None)


# ---------------------------------------------------------------------------
# _verify_signature
# ---------------------------------------------------------------------------


@requires_openssl
class TestVerifySignature:
    def test_valid_signature_passes(self, rsa_keypair: tuple[Path, Path], tmp_path: Any) -> None:
        priv, pub = rsa_keypair
        f = tmp_path / "data"
        f.write_bytes(b"payload")
        sig = tmp_path / "data.sig"
        sig.write_bytes(_sign(priv, b"payload"))
        _verify_signature(str(f), str(sig), pub)  # no raise

    def test_tampered_data_raises(self, rsa_keypair: tuple[Path, Path], tmp_path: Any) -> None:
        priv, pub = rsa_keypair
        f = tmp_path / "data"
        f.write_bytes(b"payload-TAMPERED")
        sig = tmp_path / "data.sig"
        sig.write_bytes(_sign(priv, b"payload"))
        with pytest.raises(RuntimeError, match="signature is invalid"):
            _verify_signature(str(f), str(sig), pub)

    def test_missing_key_raises(self, tmp_path: Any) -> None:
        f = tmp_path / "data"
        f.write_bytes(b"x")
        sig = tmp_path / "data.sig"
        sig.write_bytes(b"x")
        with pytest.raises(RuntimeError, match="signing key not found"):
            _verify_signature(str(f), str(sig), tmp_path / "missing-key.pem")


# ---------------------------------------------------------------------------
# verify_and_extract_bundle
# ---------------------------------------------------------------------------


@requires_openssl
class TestVerifyAndExtractBundle:
    def test_valid_bundle_returns_inner_deb(self, rsa_keypair: tuple[Path, Path], tmp_path: Any) -> None:
        priv, pub = rsa_keypair
        bundle = _build_bundle(tmp_path, priv, deb_bytes=b"PKG")
        deb_path = verify_and_extract_bundle(str(bundle), str(tmp_path / "stage"), pub)
        assert Path(deb_path).name == "openfollow_0.2.4_arm64.deb"
        assert Path(deb_path).read_bytes() == b"PKG"

    def test_tampered_deb_fails_checksum(self, rsa_keypair: tuple[Path, Path], tmp_path: Any) -> None:
        priv, pub = rsa_keypair
        # SHA256SUMS (signed) is for the original bytes; the shipped .deb differs.
        good_sums = _sha256sums("openfollow_0.2.4_arm64.deb", b"PKG")
        bundle = _build_bundle(tmp_path, priv, deb_bytes=b"PKG-EVIL", sums=good_sums, sig=_sign(priv, good_sums))
        with pytest.raises(RuntimeError, match="checksum"):
            verify_and_extract_bundle(str(bundle), str(tmp_path / "stage"), pub)

    def test_bad_signature_rejected(self, rsa_keypair: tuple[Path, Path], tmp_path: Any) -> None:
        priv, pub = rsa_keypair
        bundle = _build_bundle(tmp_path, priv, sig=b"not-a-real-signature")
        with pytest.raises(RuntimeError, match="signature is invalid"):
            verify_and_extract_bundle(str(bundle), str(tmp_path / "stage"), pub)

    def test_signature_from_other_key_rejected(self, rsa_keypair: tuple[Path, Path], tmp_path: Any) -> None:
        priv, pub = rsa_keypair
        # Sign with a DIFFERENT key than the one we verify against.
        other = tmp_path / "other.pem"
        subprocess.run(
            ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(other)],
            check=True,
            capture_output=True,
        )
        sums = _sha256sums("openfollow_0.2.4_arm64.deb", b"the-package-bytes")
        bundle = _build_bundle(tmp_path, priv, sums=sums, sig=_sign(other, sums))
        with pytest.raises(RuntimeError, match="signature is invalid"):
            verify_and_extract_bundle(str(bundle), str(tmp_path / "stage"), pub)

    @pytest.mark.parametrize("omit", ["SHA256SUMS", "SHA256SUMS.sig"])
    def test_missing_member_rejected(self, rsa_keypair: tuple[Path, Path], tmp_path: Any, omit: str) -> None:
        priv, pub = rsa_keypair
        bundle = _build_bundle(tmp_path, priv, omit=omit)
        with pytest.raises(RuntimeError, match="missing a required member"):
            verify_and_extract_bundle(str(bundle), str(tmp_path / "stage"), pub)

    def test_missing_deb_rejected(self, rsa_keypair: tuple[Path, Path], tmp_path: Any) -> None:
        priv, pub = rsa_keypair
        bundle = _build_bundle(tmp_path, priv, omit="openfollow_0.2.4_arm64.deb")
        with pytest.raises(RuntimeError, match="missing a required member"):
            verify_and_extract_bundle(str(bundle), str(tmp_path / "stage"), pub)

    def test_path_traversal_member_rejected(self, rsa_keypair: tuple[Path, Path], tmp_path: Any) -> None:
        priv, _pub = rsa_keypair
        bundle = tmp_path / "trav.ofupdate"
        _write_tar(bundle, [("../escape", b"x")])
        outside = tmp_path / "escape"
        with pytest.raises(RuntimeError, match="unsafe path"):
            verify_and_extract_bundle(str(bundle), str(tmp_path / "stage"), tmp_path / "pub.pem")
        assert not outside.exists()

    def test_not_a_tar_rejected(self, tmp_path: Any) -> None:
        bundle = tmp_path / "junk.ofupdate"
        bundle.write_bytes(b"this is not a tar archive at all")
        with pytest.raises(RuntimeError, match="not a readable archive"):
            verify_and_extract_bundle(str(bundle), str(tmp_path / "stage"), tmp_path / "pub.pem")

    def test_extra_members_are_ignored(self, rsa_keypair: tuple[Path, Path], tmp_path: Any) -> None:
        # A safe-named stray file and a directory member are skipped; the three
        # required members still verify.
        priv, pub = rsa_keypair
        deb_bytes = b"PKG"
        sums = _sha256sums("openfollow_0.2.4_arm64.deb", deb_bytes)
        bundle = tmp_path / "openfollow_0.2.4_arm64.ofupdate"
        with tarfile.open(bundle, "w:") as tar:
            for name, data in [
                ("openfollow_0.2.4_arm64.deb", deb_bytes),
                ("SHA256SUMS", sums),
                ("SHA256SUMS.sig", _sign(priv, sums)),
                ("NOTES.txt", b"ignore me"),  # safe non-matching file
            ]:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            d = tarfile.TarInfo("extradir")  # non-file member
            d.type = tarfile.DIRTYPE
            tar.addfile(d)

        deb_path = verify_and_extract_bundle(str(bundle), str(tmp_path / "stage"), pub)
        assert Path(deb_path).read_bytes() == deb_bytes


class TestVerifySignatureMocked:
    def test_missing_openssl_raises(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        import openfollow.runtime.deb_update as mod

        f = tmp_path / "data"
        f.write_bytes(b"x")
        sig = tmp_path / "data.sig"
        sig.write_bytes(b"x")
        key = tmp_path / "pub.pem"
        key.write_bytes(b"-----BEGIN PUBLIC KEY-----\n")  # exists -> passes the key check

        def _boom(*a: Any, **kw: Any) -> None:
            raise FileNotFoundError("openssl")

        monkeypatch.setattr(mod.subprocess, "run", _boom)
        with pytest.raises(RuntimeError, match="Cannot verify update signature"):
            _verify_signature(str(f), str(sig), key)


class TestVerifyBundleChecksum:
    def test_manifest_without_entry_rejected(self, tmp_path: Any) -> None:
        deb = tmp_path / "openfollow_0.2.4_arm64.deb"
        deb.write_bytes(b"PKG")
        sums = tmp_path / "SHA256SUMS"
        # A blank line and an entry for a different file – neither matches.
        sums.write_text("\ndeadbeef  some-other-file.deb\n")
        with pytest.raises(RuntimeError, match="no entry for the package"):
            _verify_bundle_checksum(str(sums), str(deb), "openfollow_0.2.4_arm64.deb")


# ---------------------------------------------------------------------------
# run_deb_update – integration-level (all I/O mocked)
# ---------------------------------------------------------------------------


def _make_app(
    *,
    current_version: str = "0.2.3",
    github_repo: str = "owner/repo",
) -> Any:
    """Build a minimal app-shaped object for the worker."""
    statuses: list[tuple[str, str]] = []

    def set_update_status(state: str, message: str = "", error: str = "") -> None:
        statuses.append((state, message))

    commands = SimpleNamespace(
        set_update_status=set_update_status,
        get_update_status=lambda: {"state": "idle", "message": "", "error": ""},
    )
    config = SimpleNamespace(
        update_github_repo=github_repo,
        update_service_name="openfollow",
        update_include_prereleases=False,
    )
    broker = MagicMock()
    broker.run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")

    app = SimpleNamespace(
        _web_commands=commands,
        _config=config,
        _runtime_services=SimpleNamespace(privilege_broker=broker),
        _statuses=statuses,
    )
    return app


_BUNDLE_ASSETS = [
    {
        "name": "openfollow_0.2.4_arm64.ofupdate",
        "browser_download_url": "https://github.com/o/r/releases/download/v0.2.4/openfollow_0.2.4_arm64.ofupdate",
    }
]


def _patch_online_io(monkeypatch: pytest.MonkeyPatch, *, inner_deb: str) -> None:
    """Stub the network + bundle steps so run_deb_update exercises state flow."""
    monkeypatch.setattr("openfollow.__version__", "0.2.3")
    monkeypatch.setattr(
        "openfollow.runtime.deb_update._fetch_latest_release",
        lambda repo, **kw: {"tag_name": "v0.2.4", "assets": _BUNDLE_ASSETS},
    )
    monkeypatch.setattr("platform.machine", lambda: "aarch64")
    monkeypatch.setattr("openfollow.runtime.deb_update._download_bundle", lambda url, path, set_status: None)
    monkeypatch.setattr("openfollow.runtime.deb_update.verify_and_extract_bundle", lambda bundle, staging: inner_deb)
    monkeypatch.setattr("openfollow.runtime.deb_update._cleanup_temp_debs", lambda: None)


class TestRunDebUpdate:
    def test_already_up_to_date_sets_idle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "openfollow.runtime.deb_update._fetch_latest_release",
            lambda repo, **kw: {"tag_name": "v0.2.3", "assets": []},
        )
        monkeypatch.setattr("openfollow.runtime.deb_update._cleanup_temp_debs", lambda: None)
        import openfollow.runtime.deb_update as mod

        monkeypatch.setattr(mod, "_is_newer", lambda latest, current: False)

        app = _make_app(current_version="0.2.3")
        run_deb_update(app, {"kind": "deb", "service_name": "openfollow"})

        last_state, last_msg = app._statuses[-1]
        assert last_state == "idle"
        assert "up to date" in last_msg.lower()

    def test_happy_path_progresses_through_states(self, monkeypatch: pytest.MonkeyPatch) -> None:
        inner = "/tmp/openfollow-update-0.2.4_arm64.ofupdate.d/openfollow_0.2.4_arm64.deb"
        _patch_online_io(monkeypatch, inner_deb=inner)
        monkeypatch.setattr(
            "openfollow.runtime.deb_update.validate_uploaded_deb",
            lambda path, arch: {"Package": "openfollow", "Version": "0.2.4", "Architecture": arch},
        )

        app = _make_app(current_version="0.2.3")
        run_deb_update(app, {"kind": "deb", "service_name": "openfollow"})

        states = [s for s, _ in app._statuses]
        assert "running" in states
        assert "restarting" in states
        broker = app._runtime_services.privilege_broker
        assert broker.run.call_count == 1
        argv = broker.run.call_args_list[0].args[1]
        assert argv[0] == "/usr/bin/systemd-run"
        assert argv[-2] == "/usr/share/openfollow/apply-update.sh"
        # The install spec is the extracted inner .deb, not the bundle.
        assert argv[-1] == inner

    def test_api_error_sets_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "openfollow.runtime.deb_update._fetch_latest_release",
            lambda repo, **kw: (_ for _ in ()).throw(RuntimeError("HTTP 404")),
        )
        monkeypatch.setattr("openfollow.runtime.deb_update._cleanup_temp_debs", lambda: None)

        app = _make_app()
        run_deb_update(app, {"kind": "deb", "service_name": "openfollow"})
        assert app._statuses[-1][0] == "failed"

    def test_unverifiable_bundle_sets_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A bundle that fails signature/checksum verification must never install.
        _patch_online_io(monkeypatch, inner_deb="/unused")
        monkeypatch.setattr(
            "openfollow.runtime.deb_update.verify_and_extract_bundle",
            lambda bundle, staging: (_ for _ in ()).throw(RuntimeError("Update signature is invalid")),
        )
        app = _make_app(current_version="0.2.3")
        run_deb_update(app, {"kind": "deb", "service_name": "openfollow"})
        assert app._statuses[-1][0] == "failed"
        assert app._runtime_services.privilege_broker.run.call_count == 0

    def test_install_privilege_error_sets_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openfollow.privilege.broker import PrivilegeError

        _patch_online_io(monkeypatch, inner_deb="/tmp/openfollow-update-x.ofupdate.d/openfollow_0.2.4_arm64.deb")
        monkeypatch.setattr(
            "openfollow.runtime.deb_update.validate_uploaded_deb",
            lambda path, arch: {"Package": "openfollow", "Version": "0.2.4", "Architecture": arch},
        )

        app = _make_app(current_version="0.2.3")
        app._runtime_services.privilege_broker.run.side_effect = PrivilegeError("no sudoers rule")
        run_deb_update(app, {"kind": "deb", "service_name": "openfollow"})
        assert app._statuses[-1][0] == "failed"

    def test_invalid_inner_deb_sets_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A verified bundle whose inner package fails the package/arch check must
        # NOT be handed to the root installer.
        _patch_online_io(monkeypatch, inner_deb="/tmp/openfollow-update-x.ofupdate.d/openfollow_0.2.4_arm64.deb")

        def _reject(path: str, arch: str) -> dict[str, str]:
            raise RuntimeError("Uploaded package is 'evil', expected 'openfollow'.")

        monkeypatch.setattr("openfollow.runtime.deb_update.validate_uploaded_deb", _reject)

        app = _make_app(current_version="0.2.3")
        run_deb_update(app, {"kind": "deb", "service_name": "openfollow"})
        assert app._statuses[-1][0] == "failed"
        assert app._runtime_services.privilege_broker.run.call_count == 0


# ---------------------------------------------------------------------------
# read_deb_control
# ---------------------------------------------------------------------------


class TestReadDebControl:
    def test_parses_labeled_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import openfollow.runtime.deb_update as mod

        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda *a, **kw: SimpleNamespace(
                returncode=0,
                stdout="Package: openfollow\nVersion: 0.2.4\nArchitecture: arm64\n\n",
                stderr="",
            ),
        )
        fields = read_deb_control("/tmp/openfollow-update-x.deb")
        assert fields == {"Package": "openfollow", "Version": "0.2.4", "Architecture": "arm64"}

    def test_nonzero_returncode_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import openfollow.runtime.deb_update as mod

        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda *a, **kw: SimpleNamespace(returncode=1, stdout="", stderr="not a Debian archive"),
        )
        with pytest.raises(RuntimeError, match="Invalid .deb file: not a Debian archive"):
            read_deb_control("/tmp/openfollow-update-x.deb")

    def test_missing_dpkg_deb_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import openfollow.runtime.deb_update as mod

        def _boom(*a: Any, **kw: Any) -> None:
            raise FileNotFoundError("dpkg-deb")

        monkeypatch.setattr(mod.subprocess, "run", _boom)
        with pytest.raises(RuntimeError, match="Cannot read package metadata"):
            read_deb_control("/tmp/openfollow-update-x.deb")


# ---------------------------------------------------------------------------
# validate_uploaded_deb
# ---------------------------------------------------------------------------


class TestValidateUploadedDeb:
    def test_accepts_openfollow_matching_arch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "openfollow.runtime.deb_update.read_deb_control",
            lambda path: {"Package": "openfollow", "Version": "0.2.4", "Architecture": "arm64"},
        )
        fields = validate_uploaded_deb("/tmp/openfollow-update-x.deb", "arm64")
        assert fields["Version"] == "0.2.4"

    def test_rejects_wrong_package(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "openfollow.runtime.deb_update.read_deb_control",
            lambda path: {"Package": "vlc", "Version": "1.0", "Architecture": "arm64"},
        )
        with pytest.raises(RuntimeError, match="expected 'openfollow'"):
            validate_uploaded_deb("/tmp/openfollow-update-x.deb", "arm64")

    def test_rejects_wrong_arch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "openfollow.runtime.deb_update.read_deb_control",
            lambda path: {"Package": "openfollow", "Version": "0.2.4", "Architecture": "amd64"},
        )
        with pytest.raises(RuntimeError, match="does not match this device"):
            validate_uploaded_deb("/tmp/openfollow-update-x.deb", "arm64")

    def test_accepts_arch_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "openfollow.runtime.deb_update.read_deb_control",
            lambda path: {"Package": "openfollow", "Version": "0.2.4", "Architecture": "all"},
        )
        validate_uploaded_deb("/tmp/openfollow-update-x.deb", "arm64")


# ---------------------------------------------------------------------------
# run_local_update – offline upload install (all I/O mocked)
# ---------------------------------------------------------------------------


class TestRunLocalUpdate:
    def test_verified_bundle_installs_without_version_gate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Older version than installed – must STILL install (downgrade allowed).
        inner = "/tmp/openfollow-update-abc.ofupdate.d/openfollow_0.1.0_arm64.deb"
        monkeypatch.setattr("openfollow.runtime.deb_update.verify_and_extract_bundle", lambda bundle, staging: inner)
        monkeypatch.setattr(
            "openfollow.runtime.deb_update.validate_uploaded_deb",
            lambda path: {"Package": "openfollow", "Version": "0.1.0", "Architecture": "arm64"},
        )
        monkeypatch.setattr(
            "openfollow.runtime.deb_update.read_deb_control",
            lambda path: {"Package": "openfollow", "Version": "0.1.0", "Architecture": "arm64"},
        )

        app = _make_app(current_version="0.2.3")
        run_local_update(
            app,
            {"kind": "deb-local", "service_name": "openfollow", "deb_path": "/tmp/openfollow-update-abc.ofupdate"},
        )

        states = [s for s, _ in app._statuses]
        assert states[-1] == "restarting"
        broker = app._runtime_services.privilege_broker
        assert broker.run.call_count == 1
        argv = broker.run.call_args_list[0].args[1]
        assert argv[0] == "/usr/bin/systemd-run"
        assert argv[-2] == "/usr/share/openfollow/apply-update.sh"
        assert argv[-1] == inner

    def test_missing_payload_sets_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _make_app()
        run_local_update(app, {"kind": "deb-local", "service_name": "openfollow"})
        assert app._statuses[-1][0] == "failed"

    def test_unverifiable_bundle_sets_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "openfollow.runtime.deb_update.verify_and_extract_bundle",
            lambda bundle, staging: (_ for _ in ()).throw(RuntimeError("Update signature is invalid")),
        )
        app = _make_app()
        run_local_update(
            app,
            {"kind": "deb-local", "service_name": "openfollow", "deb_path": "/tmp/openfollow-update-x.ofupdate"},
        )
        assert app._statuses[-1][0] == "failed"
        assert app._runtime_services.privilege_broker.run.call_count == 0


# ---------------------------------------------------------------------------
# _cleanup_temp_debs
# ---------------------------------------------------------------------------


class TestCleanupTempDebs:
    def test_removes_bundles_parts_and_extract_dirs(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        """The single prefix glob removes finished bundles, interrupted .part
        files, and extracted staging directories."""
        import openfollow.runtime.deb_update as mod

        bundle = tmp_path / "openfollow-update-0.2.4_arm64.ofupdate"
        part = tmp_path / "openfollow-update-0.2.4_arm64.ofupdate.part"
        extract = tmp_path / "openfollow-update-0.2.4_arm64.ofupdate.d"
        bundle.write_bytes(b"x")
        part.write_bytes(b"y")
        extract.mkdir()
        (extract / "inner.deb").write_bytes(b"z")

        monkeypatch.setattr(mod.glob, "glob", lambda pattern: [str(bundle), str(part), str(extract)])
        mod._cleanup_temp_debs()

        assert not bundle.exists()
        assert not part.exists()
        assert not extract.exists()

    def test_ignores_unlink_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A file that vanishes (or can't be removed) between glob and unlink
        must not propagate – cleanup is best-effort."""
        import openfollow.runtime.deb_update as mod

        monkeypatch.setattr(mod.glob, "glob", lambda pattern: ["/tmp/openfollow-update-x.ofupdate"])

        def boom(_path: str) -> None:
            raise OSError("gone")

        monkeypatch.setattr(mod.os, "unlink", boom)
        mod._cleanup_temp_debs()  # must not raise


# ---------------------------------------------------------------------------
# _remove_staged
# ---------------------------------------------------------------------------


class TestRemoveStaged:
    def test_removes_bundle_part_and_extract_dir(self, tmp_path: Any) -> None:
        bundle = tmp_path / "openfollow-update-x.ofupdate"
        part = tmp_path / "openfollow-update-x.ofupdate.part"
        extract = tmp_path / "openfollow-update-x.ofupdate.d"
        bundle.write_bytes(b"a")
        part.write_bytes(b"b")
        extract.mkdir()
        (extract / "openfollow_0_arm64.deb").write_bytes(b"c")

        _remove_staged(str(bundle))
        assert not bundle.exists()
        assert not part.exists()
        assert not extract.exists()

    def test_tolerates_missing(self, tmp_path: Any) -> None:
        _remove_staged(str(tmp_path / "openfollow-update-nope.ofupdate"))
