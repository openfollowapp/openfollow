# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""GitHub Releases-based update installer.

Implements the on-demand "Check & Install Latest" flow:

1. Query the GitHub Releases API for the latest published release.
2. Compare the remote version with the currently-installed version.
3. If newer: download the single signed update bundle (``.ofupdate``) for this
   architecture, verify its signature + checksum, extract the inner ``.deb``,
   and install it via the detached root installer.
4. Restart the service explicitly – the postinst calls ``systemctl start``
   which is a no-op on a running service, so the explicit restart here is what
   brings the new version up.

An update bundle is a plain tar carrying three members: the ``.deb``, a
``SHA256SUMS`` line for it, and ``SHA256SUMS.sig`` (an openssl RSA signature
over ``SHA256SUMS``). The on-device public key (shipped in the package) verifies
the signature; the checksum proves the package is complete and unmodified. Both
checks are fail-closed and run before the root installer – the same bundle and
the same checks cover the online download and the operator upload paths.

The update worker runs on a daemon thread started by
:func:`openfollow.runtime.app_commands.check_update_request`.
"""

from __future__ import annotations

import glob
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess  # nosec B404
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from packaging.version import InvalidVersion, Version

from openfollow.privilege.broker import PrivilegeError
from openfollow.privilege.capabilities import (
    DEB_UPDATE_TMP_PREFIX,
    PACKAGE_SELF_UPDATE,
    SELF_UPDATE_SCRIPT,
    SELF_UPDATE_UNIT,
)

if TYPE_CHECKING:
    from openfollow.app import OpenFollowApp

logger = logging.getLogger(__name__)

# The list endpoint (newest-first) is used instead of /releases/latest because
# /releases/latest EXCLUDES pre-releases and drafts – and this project ships its
# updates as pre-releases. We take the newest published (non-draft) release.
_GITHUB_API_RELEASES = "https://api.github.com/repos/{repo}/releases?per_page=20"
_DOWNLOAD_TIMEOUT_S = 300
_INSTALL_TIMEOUT_S = 300

# Single signed update artifact. One file carries the .deb + checksum + signature.
_BUNDLE_SUFFIX = ".ofupdate"

# On-device release-signing public key, shipped in the package (see pyproject
# include). Verifies a bundle before it can be installed as root.
_RELEASE_PUBKEY = Path(__file__).with_name("release-pubkey.pem")

# Hard ceiling on a downloaded bundle. /tmp is tmpfs (RAM) on the Pi, so an
# oversized or run-away download must be aborted rather than fill memory.
_MAX_BUNDLE_BYTES = 1024 * 1024 * 1024

# Asset downloads must be HTTPS to GitHub (defence in depth – the URL comes from
# the API response, but the result is installed as root).
_ALLOWED_DOWNLOAD_HOSTS = ("github.com", "objects.githubusercontent.com")


def run_deb_update(app: OpenFollowApp, request: dict[str, str]) -> None:
    """Execute a GitHub-release update from a worker thread."""
    import openfollow

    repo = app._config.update_github_repo.strip()

    def set_status(state: str, message: str = "", error: str = "") -> None:
        app._web_commands.set_update_status(state, message=message, error=error)

    staged: str | None = None
    try:
        _cleanup_temp_debs()  # clear any stale staging from a prior failed run
        set_status("running", "Checking for latest release…")
        include_prereleases = app._config.update_include_prereleases or _is_prerelease_version(openfollow.__version__)
        release = _fetch_latest_release(repo, include_prereleases=include_prereleases)

        tag: str = release.get("tag_name", "")
        latest_str = tag.removeprefix("v")
        current_str = openfollow.__version__

        if not _is_newer(latest_str, current_str):
            set_status("idle", f"Already up to date (v{current_str}).")
            return

        arch = _deb_arch()
        assets: list[dict[str, Any]] = release.get("assets", [])
        bundle_url = _find_bundle_asset(assets, arch)

        # Sanitise the version label for the staging path so an unusual tag
        # (a '/' in the name, spaces, …) can't escape the staging prefix,
        # break os.open, or miss the sudoers/apply-update.sh wildcard.
        safe_label = re.sub(r"[^A-Za-z0-9._+-]", "-", latest_str) or "update"
        bundle_path = f"/tmp/{DEB_UPDATE_TMP_PREFIX}{safe_label}_{arch}{_BUNDLE_SUFFIX}"  # nosec B108
        staged = bundle_path

        set_status("running", f"Downloading version {latest_str}…")
        _download_bundle(bundle_url, bundle_path, set_status=set_status)

        set_status("running", f"Verifying version {latest_str}…")
        deb_path = verify_and_extract_bundle(bundle_path, bundle_path + ".d")
        # Defence in depth: the same package-identity + architecture check the
        # offline-upload path runs.
        validate_uploaded_deb(deb_path, arch)

        _free_bundle_file(bundle_path)  # free tmpfs before the install runs
        _apply_update(app, deb_path, version_label=latest_str, set_status=set_status)
    except Exception as exc:
        logger.error("Deb update failed: %s", exc)
        set_status("failed", "Update failed.", error=str(exc))
        # Only this run's staging – a broad sweep could in principle touch a
        # file a detached installer is still using.
        if staged is not None:
            _remove_staged(staged)


def run_local_update(app: OpenFollowApp, request: dict[str, str]) -> None:
    """Install an operator-uploaded update bundle from a worker thread.

    The web route has already staged the ``.ofupdate`` bundle at ``deb_path``
    under ``/tmp/openfollow-update-*``. Unlike :func:`run_deb_update` this path
    does no GitHub fetch and no version gate – the operator chose the file, so
    downgrades / reinstalls are allowed. The signature + checksum are still
    verified, so only a bundle signed with the release key installs.
    """
    bundle_path = request.get("deb_path", "").strip()

    def set_status(state: str, message: str = "", error: str = "") -> None:
        app._web_commands.set_update_status(state, message=message, error=error)

    try:
        if not bundle_path:
            raise RuntimeError("No uploaded package to install.")
        set_status("running", "Verifying update…")
        deb_path = verify_and_extract_bundle(bundle_path, bundle_path + ".d")
        validate_uploaded_deb(deb_path)
        version_label = read_deb_control(deb_path).get("Version", "?")
        _free_bundle_file(bundle_path)
        _apply_update(app, deb_path, version_label=version_label, set_status=set_status)
    except Exception as exc:
        logger.error("Local update failed: %s", exc)
        set_status("failed", "Update failed.", error=str(exc))
        if bundle_path:
            _remove_staged(bundle_path)


def _apply_update(
    app: OpenFollowApp,
    spec: str,
    *,
    version_label: str,
    set_status: Callable[..., None],
) -> None:
    """Launch the detached installer for a staged ``.deb`` ``spec``.

    ``spec`` is a staged ``/tmp/openfollow-update-*`` path. The install runs in a
    transient systemd unit (``systemd-run``) so it is DETACHED from
    ``openfollow.service`` – the package's prerm stops that service mid-install,
    which would kill an in-process ``apt-get`` and leave the package
    half-configured. The wrapper script installs, restarts the service onto the
    new version, and removes the spec; this call returns as soon as the unit is
    started.

    On success the staged ``spec`` is left in place for the (now-running)
    detached installer to consume – the caller must NOT clean it up.
    """
    broker = app._runtime_services.privilege_broker
    set_status("running", f"Installing version {version_label}…")
    try:
        broker.run(
            PACKAGE_SELF_UPDATE,
            [
                "/usr/bin/systemd-run",
                "--collect",
                f"--unit={SELF_UPDATE_UNIT}",
                SELF_UPDATE_SCRIPT,
                spec,
            ],
            cwd="/",
            timeout=_INSTALL_TIMEOUT_S,
            reason=f"Install OpenFollow v{version_label}",
        )
    except PrivilegeError as exc:
        raise RuntimeError(
            f"Package install failed. Run 'Apply Permissions' on the Device page, then try again. Detail: {exc}"
        ) from exc

    # The detached unit now installs and restarts the service; this process will
    # be stopped shortly when the package's prerm cycles the unit. The new
    # version clears the status on startup.
    set_status("restarting", f"Installing version {version_label} – the device will restart automatically…")


def verify_and_extract_bundle(
    bundle_path: str,
    staging_dir: str,
    pubkey_path: str | os.PathLike[str] = _RELEASE_PUBKEY,
) -> str:
    """Verify a signed update bundle and extract the inner ``.deb``.

    Extracts the three expected members into ``staging_dir`` (rejecting any
    unsafe member name so a crafted bundle can't escape the dir), verifies the
    RSA signature over ``SHA256SUMS`` with ``pubkey_path``, then verifies the
    ``.deb``'s SHA-256 against ``SHA256SUMS``. Returns the inner ``.deb`` path.
    Raises ``RuntimeError`` (fail-closed) on any missing/invalid member.
    """
    os.makedirs(staging_dir, exist_ok=True)
    deb_name: str | None = None
    seen: set[str] = set()
    try:
        with tarfile.open(bundle_path, "r:") as tar:
            for member in tar.getmembers():
                name = member.name
                if not _is_safe_member_name(name):
                    raise RuntimeError(f"Update bundle contains an unsafe path: {name!r}")
                if not member.isfile():
                    continue
                if name in ("SHA256SUMS", "SHA256SUMS.sig"):
                    tar.extract(member, staging_dir)  # nosec B202 – name validated above
                    seen.add(name)
                elif name.startswith("openfollow_") and name.endswith(".deb"):
                    tar.extract(member, staging_dir)  # nosec B202 – name validated above
                    deb_name = name
    except tarfile.TarError as exc:
        raise RuntimeError(f"Update bundle is not a readable archive: {exc}") from exc

    if deb_name is None or "SHA256SUMS" not in seen or "SHA256SUMS.sig" not in seen:
        raise RuntimeError("Update bundle is missing a required member (.deb, SHA256SUMS, SHA256SUMS.sig).")

    sums_path = os.path.join(staging_dir, "SHA256SUMS")
    sig_path = os.path.join(staging_dir, "SHA256SUMS.sig")
    deb_path = os.path.join(staging_dir, deb_name)

    _verify_signature(sums_path, sig_path, pubkey_path)
    _verify_bundle_checksum(sums_path, deb_path, deb_name)
    return deb_path


def _is_safe_member_name(name: str) -> bool:
    """True if ``name`` is a plain basename (no path separators, no traversal)."""
    return bool(name) and name not in (".", "..") and os.path.basename(name) == name


def _verify_signature(
    file_path: str,
    sig_path: str,
    pubkey_path: str | os.PathLike[str] = _RELEASE_PUBKEY,
) -> None:
    """Verify ``file_path`` against ``sig_path`` with the release public key.

    Uses ``openssl dgst -sha256 -verify``. Raises ``RuntimeError`` (fail-closed)
    on a bad signature, a missing key, or a missing ``openssl``.
    """
    if not os.path.isfile(pubkey_path):
        raise RuntimeError("Release signing key not found – cannot verify the update.")
    try:
        proc = subprocess.run(  # nosec B603 B607
            ["openssl", "dgst", "-sha256", "-verify", str(pubkey_path), "-signature", sig_path, file_path],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Cannot verify update signature: {exc}") from exc
    if proc.returncode != 0 or "Verified OK" not in (proc.stdout or ""):
        raise RuntimeError("Update signature is invalid – refusing to install an unsigned or tampered package.")


def _verify_bundle_checksum(sums_path: str, deb_path: str, deb_name: str) -> None:
    """Check ``deb_path``'s SHA-256 against the ``deb_name`` entry in ``sums_path``."""
    want: str | None = None
    with open(sums_path, encoding="utf-8", errors="strict") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) >= 2 and parts[1].lstrip("*") == deb_name:
                want = parts[0].lower()
                break
    if not want:
        raise RuntimeError("Update bundle checksum manifest has no entry for the package.")

    digest = hashlib.sha256()
    with open(deb_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != want:
        raise RuntimeError("Update package failed its checksum – the file is corrupt or incomplete.")


def read_deb_control(path: str) -> dict[str, str]:
    """Return selected control fields of a .deb via ``dpkg-deb -f``.

    Raises ``RuntimeError`` if the file is not a readable Debian package –
    the route surfaces that message to the operator.
    """
    try:
        proc = subprocess.run(  # nosec B603 B607
            ["dpkg-deb", "-f", path, "Package", "Version", "Architecture"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Cannot read package metadata: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or "not a Debian package"
        raise RuntimeError(f"Invalid .deb file: {detail}")
    fields: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def validate_uploaded_deb(path: str, expected_arch: str | None = None) -> dict[str, str]:
    """Confirm ``path`` is an ``openfollow`` .deb for this device's architecture.

    Returns the parsed control fields on success; raises ``RuntimeError`` with
    an operator-facing message otherwise.
    """
    expected_arch = expected_arch or _deb_arch()
    fields = read_deb_control(path)
    package = fields.get("Package", "")
    arch = fields.get("Architecture", "")
    if package != "openfollow":
        raise RuntimeError(f"Uploaded package is '{package or 'unknown'}', expected 'openfollow'.")
    if arch not in (expected_arch, "all"):
        raise RuntimeError(f"Package architecture '{arch}' does not match this device ({expected_arch}).")
    return fields


def check_for_update(repo: str, current_version: str, *, include_prereleases: bool = False) -> dict[str, Any]:
    """Query GitHub and report whether a newer release is available.

    Returns ``{"latest": str, "current": str, "available": bool}``.
    Raises ``RuntimeError`` on any network / API error (the caller
    surfaces the message to the operator). This is the synchronous
    "check" half of the flow – it does NOT download or install.

    ``include_prereleases`` offers pre-release builds; left off, only stable
    releases are considered – except when the running build is itself a
    pre-release, which always tracks pre-releases so it isn't stranded.
    """
    effective_prereleases = include_prereleases or _is_prerelease_version(current_version)
    release = _fetch_latest_release(repo.strip(), include_prereleases=effective_prereleases)
    latest = str(release.get("tag_name", "")).lstrip("v")
    return {
        "latest": latest,
        "current": current_version,
        "available": _is_newer(latest, current_version),
    }


def _fetch_latest_release(repo: str, *, include_prereleases: bool = False) -> dict[str, Any]:
    """Return the newest published release from the GitHub API.

    Uses the list endpoint (newest-first) and picks the first non-draft entry.
    Pre-releases are skipped unless ``include_prereleases`` is set, so stable
    builds are offered by default and pre-releases are strictly opt-in.
    """
    url = _GITHUB_API_RELEASES.format(repo=repo)
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "openfollow-updater"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"GitHub API returned HTTP {exc.code} for {repo!r}. Check update_github_repo in config."
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach GitHub API: {exc.reason}") from exc

    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError(f"Unexpected GitHub API response (not JSON): {exc}") from exc

    if not isinstance(data, list):
        raise RuntimeError(f"GitHub API returned a {type(data).__name__}, expected a list of releases.")

    for release in data:
        if not isinstance(release, dict) or release.get("draft") or "tag_name" not in release:
            continue
        if release.get("prerelease") and not include_prereleases:
            continue
        return release
    raise RuntimeError(f"No published releases found for {repo!r}.")


def _find_bundle_asset(assets: list[dict[str, Any]], arch: str) -> str:
    """Return the browser_download_url for the signed update bundle.

    Matches the release-CI naming ``openfollow_<version>_<arch>.ofupdate``,
    preferring the device architecture and falling back to an
    arch-independent ``_all.ofupdate``.
    """
    for suffix in (f"_{arch}{_BUNDLE_SUFFIX}", f"_all{_BUNDLE_SUFFIX}"):
        for asset in assets:
            name: str = asset.get("name", "")
            url: str = asset.get("browser_download_url", "")
            if name.startswith("openfollow_") and name.endswith(suffix) and url:
                return url
    raise RuntimeError(
        f"No openfollow_{arch}{_BUNDLE_SUFFIX} asset found in the latest release. "
        f"Assets available: {[a.get('name', '') for a in assets]}"
    )


def _assert_download_url(url: str) -> None:
    """Require an HTTPS GitHub URL before fetching (the result is run as root)."""
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not (host in _ALLOWED_DOWNLOAD_HOSTS or host.endswith(".githubusercontent.com")):
        raise RuntimeError(f"Refusing to download update from an untrusted URL: {url!r}")


def _download_bundle(
    url: str,
    dest_path: str,
    *,
    set_status: Callable[..., None],
) -> None:
    """Download ``url`` to ``dest_path``, enforcing host, size and completeness."""
    _assert_download_url(url)
    part_path = dest_path + ".part"
    try:
        os.unlink(part_path)
    except OSError:
        pass

    req = urllib.request.Request(url, headers={"User-Agent": "openfollow-updater"})
    try:
        with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT_S) as resp:  # nosec B310
            expected: int | None = None
            total_raw = resp.headers.get("Content-Length")
            if total_raw:
                try:
                    expected = int(total_raw)
                except ValueError:
                    expected = None
            if expected is not None and expected > _MAX_BUNDLE_BYTES:
                raise RuntimeError("Update is larger than the maximum allowed size.")
            total_mb = expected / 1_048_576 if expected else None

            fd = os.open(part_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                downloaded = 0
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if downloaded > _MAX_BUNDLE_BYTES:
                        raise RuntimeError("Update exceeded the maximum allowed size.")
                    if total_mb:
                        set_status("running", f"Downloading… {downloaded / 1_048_576:.1f} / {total_mb:.1f} MB")
            # A server/CDN that closes mid-stream returns an empty read with no
            # error – reject the short file rather than promote a truncated one.
            if expected is not None and downloaded != expected:
                raise RuntimeError(f"Download incomplete – got {downloaded} of {expected} bytes.")
    except urllib.error.URLError as exc:
        _safe_unlink(part_path)
        raise RuntimeError(f"Download failed: {exc.reason}") from exc
    except Exception:
        _safe_unlink(part_path)
        raise

    os.replace(part_path, dest_path)


def _deb_arch() -> str:
    """Map ``platform.machine()`` to the Debian architecture label."""
    machine = platform.machine()
    _map = {
        "aarch64": "arm64",
        "x86_64": "amd64",
        "armv7l": "armhf",
        "i686": "i386",
    }
    return _map.get(machine, machine)


def _is_newer(latest: str, current: str) -> bool:
    """Return True when ``latest`` is strictly newer than ``current``."""
    try:
        return Version(latest) > Version(current)
    except InvalidVersion:
        logger.warning("Cannot compare versions %r vs %r – assuming update needed.", latest, current)
        return latest != current


def _is_prerelease_version(version: str) -> bool:
    """True when *version* is a PEP 440 pre-release (``rc``/``a``/``b``/``dev``).

    A device already on a pre-release build is on the pre-release track, so the
    update check offers pre-releases to it regardless of the opt-in flag –
    otherwise an ``rcN`` device would be stranded (the project ships updates as
    pre-releases). Returns False for a final release or an unparseable version.
    """
    try:
        return Version(version).is_prerelease
    except InvalidVersion:
        return False


def _safe_unlink(path: str) -> None:
    """Remove a single file, ignoring a missing target."""
    try:
        os.unlink(path)
    except OSError:
        pass


def _free_bundle_file(bundle_path: str) -> None:
    """Drop the downloaded bundle once its .deb is extracted (frees tmpfs)."""
    _safe_unlink(bundle_path)
    _safe_unlink(bundle_path + ".part")


def _remove_staged(path: str) -> None:
    """Remove a staged bundle artifact (the file, its ``.part`` and extract dir)."""
    _safe_unlink(path)
    _safe_unlink(path + ".part")
    staging_dir = path + ".d"
    if os.path.isdir(staging_dir):
        shutil.rmtree(staging_dir, ignore_errors=True)


def _cleanup_temp_debs() -> None:
    """Remove leftover /tmp/openfollow-update-* staging artifacts (best-effort).

    Covers the online download's bundle/``.part`` files, plain-upload bundles,
    and the extracted staging directories.
    """
    for path in glob.glob(f"/tmp/{DEB_UPDATE_TMP_PREFIX}*"):  # nosec B108
        try:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.unlink(path)
        except OSError:
            pass
