# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Bottle routes for configuration web UI: HTML pages, JSON API, config helpers."""

from __future__ import annotations

import collections
import copy
import hashlib
import hmac
import html as html_mod
import importlib.util
import ipaddress
import json
import logging
import math
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, MutableSequence, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar
from urllib.parse import urlsplit

from bottle import Bottle, HTTPResponse, abort, redirect, request, response, static_file, template

import openfollow

# Bottle 0.13.4 decodes URL-form bodies as Latin-1; browsers POST UTF-8.
# Side-effect import rebinds ``bottle.urlunquote`` to UTF-8 so non-ASCII
# form values round-trip. Bottle resolves ``urlunquote`` lazily per call,
# so the rebind only needs to land at module-load time.
import openfollow.web._bottle_charset_fix  # noqa: F401

if TYPE_CHECKING:
    from openfollow.video.inputs._base import VideoInputBase
    from openfollow.web.discovery import PeerInfo
    from openfollow.web.server import ConfigWebServer

from openfollow.configuration import (
    DEFAULT_UPDATE_SERVICE_NAME,
    MARKER_TOKEN_ALL,
    MOUSE3D_AXES,
    MOUSE3D_BUTTON_FIELDS,
    VALID_BUTTON_NAMES,
    AppConfig,
    OscDestinationConfig,
    OscTransmitterConfig,
    TriggerZoneConfig,
    _coerce_marker_tokens,
    config_write_lock,
    load_config,
    save_config,
)

# Module-level so handler closures resolve ``save_catalog`` from this
# namespace at call time (tests monkeypatch it for persist-failure paths).
from openfollow.marker_catalog import save_catalog
from openfollow.net_utils import get_local_ipv4_addresses
from openfollow.network.adapter import Ipv4Config, Ipv4Method
from openfollow.network.validate import parse_prefix, validate_apply
from openfollow.templates import (
    TEMPLATE_FILE_SUFFIX,
    TemplateValidationError,
)
from openfollow.templates import (
    VALID_TYPES as TEMPLATE_VALID_TYPES,
)
from openfollow.templates.bootstrap import seed_system_templates
from openfollow.templates.loader import (
    LoadedTemplate,
    find_template,
    list_templates,
    list_templates_by_type,
)
from openfollow.templates.writer import (
    TemplateWriteError,
    delete_user_template,
    write_user_template,
)
from openfollow.units import UnitSystem, parse_length, parse_speed
from openfollow.web import diagnostics, peer_auth
from openfollow.web._md import render_help_markdown
from openfollow.web.login_throttle import LoginThrottle

logger = logging.getLogger(__name__)


@dataclass
class _UserTemplateView:
    """Per-row shape for the user-templates dropdown in the OSC bindings partial.

    ``select_value`` is the stable key the dropdown emits. For user
    templates it's ``file:<filename>``: file-based templates don't enforce
    id uniqueness across files (copying/syncing can duplicate ids), so
    ``file:<filename>`` (filesystem-unique within ``user/``) identifies one
    row regardless of envelope id."""

    id: str
    name: str
    address: str
    args: list[str]
    select_value: str = ""


def _templates_root(server: ConfigWebServer) -> Path:
    """Resolve the on-disk templates folder.

    Lives next to ``config.toml`` so the operator's templates travel
    with their config when they back up / clone an install. The folder
    is created lazily by the writer and the bootstrap; the loader
    tolerates a missing folder (returns an empty list).
    """
    return Path(server.config_path).parent / "templates"


def _is_safe_template_filename(filename: str) -> bool:
    """Reject filenames that aren't ``<type>.<slug>.openfollowtemplate`` shape.

    Defends against path traversal in route-facing inputs independent of
    Bottle's single-segment routing.
    """
    if not filename or "/" in filename or "\\" in filename:
        return False
    # Reject NUL and any control char (< 0x20): a future caller that passes the
    # name straight to open() would otherwise hit an uncaught ValueError on NUL.
    if any(ord(c) < 0x20 for c in filename):
        return False
    if filename in (".", "..") or filename.startswith("."):
        return False
    if not filename.endswith(TEMPLATE_FILE_SUFFIX):
        return False
    # Reject ``..`` substring; the legal single-dot separators
    # (``osc_output.my-cue.openfollowtemplate``) still pass.
    return ".." not in filename


# Serializes read-modify-write transactions against the on-disk config.
# Process-wide lock; threaded WSGI handlers and the marker-catalog sync
# receiver's selection prune mutually exclude. On-screen-menu writers do
# NOT take this lock. Each mutating handler wraps its load→save in it.
_config_write_lock = config_write_lock


# ---------------------------------------------------------------------------
# Config Section Helpers
# ---------------------------------------------------------------------------

VALID_SECTIONS = {
    "general",
    "psn",
    "video_source",
    "camera",
    "grid",
    "movement",
    "marker",
    "controller",
    "gamepad",
    "keyboard",
    "mouse",
    "mouse3d",
    "osc",
    "operator_messages",
    "detection",
    "otp_output",
    "rttrpm_output",
    "trigger_zones",
    "osc_bindings",
    "osc_destinations",
}
_WEB_STATIC_DIR = Path(__file__).with_name("static")


def _compute_asset_version() -> str:
    """Content fingerprint of the bundled static dir.

    A short hash over every static file's path + bytes, so the token changes
    whenever any asset changes (a dev edit picked up on the next server
    start, a release that ships new bytes) and stays put otherwise. Falls
    back to the build identity when the dir holds no files (a non-standard
    install), so the token is never empty.
    """
    digest = hashlib.sha256()
    files = sorted(p for p in _WEB_STATIC_DIR.rglob("*") if p.is_file())
    for path in files:
        digest.update(path.relative_to(_WEB_STATIC_DIR).as_posix().encode())
        digest.update(path.read_bytes())
    if files:
        return digest.hexdigest()[:12]
    return openfollow.__commit__ or openfollow.__version__


@lru_cache(maxsize=1)
def asset_version() -> str:
    """Cache-bust token for bundled static assets, computed once per process.

    Referenced by ``base.tpl`` as ``?v=<token>`` on every asset URL; paired
    with the ``immutable`` cache headers on the ``/assets`` route so a new
    build always refetches while an unchanged one stays cached.
    """
    return _compute_asset_version()


def _script_safe_json(obj: Any) -> str:
    """``json.dumps`` escaped for embedding as raw JS inside a ``<script>``.

    ``json.dumps`` does not escape ``<``, ``>`` or ``&``, so a user-controlled
    string (e.g. an OSC destination name containing ``</script>``) embedded via
    ``{{!...}}`` could close the script element and inject markup. Escaping
    those to ``\\uXXXX`` keeps the value a valid JS string while making the
    breakout impossible; the U+2028/U+2029 line separators are escaped too
    since they terminate JS string literals on older engines.
    """
    return (
        json.dumps(obj, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def osc_destinations_client_list(config: AppConfig) -> list[dict[str, Any]]:
    """Client-facing OSC destination list (id + label + endpoint).

    The single source of truth for the zone editor's destination dropdown,
    served both at initial render (``osc_destinations_script_json``) and on the
    ``/api/zones`` poll so the dropdown follows add/rename/delete without a full
    page reload.
    """
    return [
        {
            "id": d.id,
            "name": d.name,
            "host": d.host,
            "port": d.port,
            "protocol": d.protocol,
            "framing": d.framing,
        }
        for d in config.osc_destinations.destinations
    ]


def osc_destinations_script_json(config: AppConfig) -> str:
    """OSC destinations as ``<script>``-safe JSON for the zone editor's initial render."""
    return _script_safe_json(osc_destinations_client_list(config))


# Help docs served as rendered HTML by ``/help/<id>.html``. A help id is a
# lowercase slug (no dots/slashes/``..``) so it can't escape ``_WEB_HELP_DIR``.
_WEB_HELP_DIR = Path(__file__).with_name("help")
_HELP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SERVICE_NAME_RE = re.compile(r"^[A-Za-z0-9_.@-]+$")
_GITHUB_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SECTION_CONFIG_ATTRS = {
    "camera": "camera",
    "grid": "grid",
    "movement": "marker",
    "marker": "marker",
    "controller": "controller",
    "gamepad": "controller",
    "keyboard": "controller",
    "mouse": "controller",
    "mouse3d": "mouse3d",
    "osc": "osc",
    "operator_messages": "operator_messages",
    "detection": "detection",
    "otp_output": "otp_output",
    "rttrpm_output": "rttrpm_output",
    "trigger_zones": "trigger_zones",
}


# Cap on an uploaded .deb (the bundled-venv package is ~100s of MB).
_MAX_UPLOAD_BYTES = 512 * 1024 * 1024


def _stream_to_file(src: Any, dest_path: str, total: int, *, chunk: int = 1024 * 1024) -> None:
    """Stream exactly ``total`` bytes from ``src`` (the WSGI input) to ``dest_path``.

    Reading the raw body directly avoids Bottle's multipart ``disk_limit``.
    Bounded by ``total`` so a read never runs past the request body.
    """
    remaining = total
    # Owner-only perms (0600), matching the hardened GitHub-download path –
    # the staged .deb must not be world-readable in shared /tmp.
    fd = os.open(dest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as out:
        while remaining > 0:
            data = src.read(min(chunk, remaining))
            if not data:
                break
            out.write(data)
            remaining -= len(data)
    if remaining > 0:
        raise RuntimeError("Upload truncated – the connection closed before all data arrived.")


def _discard_staged(staged_path: str | None) -> None:
    """Best-effort removal of a staged upload bundle + its extract dir after a failure."""
    if not staged_path:
        return
    # Reuse the downloader's cleanup so the two paths can't drift (it also drops a
    # stray ``.part``, a harmless no-op for the upload path).
    from openfollow.runtime.deb_update import _remove_staged

    _remove_staged(staged_path)


def _get_local_ips() -> list[str]:
    """Return sorted list of non-loopback local IPv4 addresses."""
    return sorted(ip for ip in get_local_ipv4_addresses() if ip and not ip.startswith("127.") and ip != "localhost")


def _is_on_device_request() -> bool:
    """True when the request originated from the embedded WebView.

    The on-device WebKit overlay loads ``http://127.0.0.1:<port>/``, so a
    loopback ``remote_addr`` distinguishes it from LAN clients (which hit
    the external IP). Covers IPv4/IPv6 loopback and the ``localhost`` literal.
    """
    remote = (request.remote_addr or "").strip()
    return remote in ("127.0.0.1", "::1", "localhost")


# CSRF / DNS-rebind defence. State-changing methods only –
# safe methods carry no side effects.
_SAFE_HTTP_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})


def _allowed_request_hosts() -> set[str]:
    """Host names this device legitimately answers to: loopback, its own LAN
    IPs, and its (mDNS) hostname. A state-changing request whose ``Origin``
    names anything else is a cross-origin / DNS-rebound forgery."""
    hosts = {"127.0.0.1", "::1", "localhost"}
    hosts.update(_get_local_ips())
    host = socket.gethostname().strip().lower()
    if host:
        hosts.add(host)
        hosts.add(f"{host}.local")
    return hosts


def _request_origin_host() -> str | None:
    """Lower-cased hostname from the request's ``Origin`` (preferred) or
    ``Referer`` header, or ``None`` when neither is present/parseable. Port
    and IPv6 brackets are stripped by ``urlsplit().hostname``."""
    raw = (request.headers.get("Origin") or request.headers.get("Referer") or "").strip()
    if not raw or raw == "null":
        return None
    try:
        return urlsplit(raw).hostname
    except ValueError:
        return None


def _cancel_button_label(cfg: AppConfig) -> str:
    """Return the gamepad button label bound to cancel, for the footer hint.

    Mirrors ``controller.btn_menu_cancel`` (default ``"B"``); falls back to
    ``"Cancel"`` when the binding is cleared so the hint stays non-empty.
    """
    return (cfg.controller.btn_menu_cancel or "").strip() or "Cancel"


def _get_detection_missing_deps(config: AppConfig | None = None) -> list[str]:
    """Return missing pip packages for person detection (empty if all present).

    Reports ``opencv-python`` and ``onnxruntime`` when absent. ``config`` is
    accepted but not required.
    """
    try:
        from openfollow.video.detection import check_detection_dependencies

        detection_cfg = config.detection if config is not None else None
        return check_detection_dependencies(detection_cfg)
    except ModuleNotFoundError as exc:
        # Map known module to pip name (cv2 → opencv-python); else surface
        # the actual module name so the banner names the right package.
        logger.info(
            "Detection dependency probe failed (missing module %s)",
            exc.name,
            exc_info=True,
        )
        if exc.name == "cv2":
            return ["opencv-python"]
        return [exc.name or "detection dependencies unavailable"]
    except Exception:
        # Unexpected failure – don't misattribute to opencv-python; log
        # and surface a generic message.
        logger.warning(
            "Detection dependency probe failed unexpectedly",
            exc_info=True,
        )
        return ["detection dependencies unavailable"]


# Each detection extra's presence indicator: the package whose import signals
# the extra is installed. ``onnxruntime`` defines the (only) detection backend;
# ``opencv-python`` is a shared dependency, not a reliable gate. ``ultralytics``
# is the optional model-export toolchain (pulls torch; workstation-only). Both
# are installed via ``install-detection.sh``; these gate model select + export.
_DETECTION_EXTRA_INDICATOR: dict[str, str] = {
    "detection": "onnxruntime",
    "export": "ultralytics",
}


def _get_detection_extras_status() -> dict[str, bool]:
    """Map each detection extra to whether its primary backend is installed.

    Gates the model dropdown (``detection``) and the Download action
    (``export``). ``find_spec`` is cheap (no import) so it runs uncached.
    """
    return {extra: importlib.util.find_spec(pkg) is not None for extra, pkg in _DETECTION_EXTRA_INDICATOR.items()}


# Friendly ``(value, label)`` names for the well-known YOLO lineup. All
# models are ONNX. The catalogue only supplies labels and ordering – whether
# an entry is *selectable* is decided per-render by what's actually on disk
# (see ``_available_models``).
_DETECTION_MODEL_CATALOGUE: tuple[tuple[str, str], ...] = (
    ("yolov8n.onnx", "YOLOv8 Nano ONNX"),
    ("yolov8s.onnx", "YOLOv8 Small ONNX"),
    ("yolov8m.onnx", "YOLOv8 Medium ONNX"),
    ("yolov8l.onnx", "YOLOv8 Large ONNX"),
    ("yolov8x.onnx", "YOLOv8 XLarge ONNX"),
    ("yolo11n.onnx", "YOLO11 Nano ONNX"),
    ("yolo11s.onnx", "YOLO11 Small ONNX"),
    ("yolo11m.onnx", "YOLO11 Medium ONNX"),
    ("yolo11l.onnx", "YOLO11 Large ONNX"),
    ("yolo11x.onnx", "YOLO11 XLarge ONNX"),
    ("yolo12n.onnx", "YOLO12 Nano ONNX"),
    ("yolo12s.onnx", "YOLO12 Small ONNX"),
    ("yolo12m.onnx", "YOLO12 Medium ONNX"),
    ("yolo12l.onnx", "YOLO12 Large ONNX"),
    ("yolo12x.onnx", "YOLO12 XLarge ONNX"),
    ("yolo26n.onnx", "YOLO26 Nano ONNX"),
    ("yolo26s.onnx", "YOLO26 Small ONNX"),
    ("yolo26m.onnx", "YOLO26 Medium ONNX"),
    ("yolo26l.onnx", "YOLO26 Large ONNX"),
    ("yolo26x.onnx", "YOLO26 XLarge ONNX"),
)


def _resolved_models_dir(storage_path: str) -> Path:
    """The ``<storage>/models`` directory the runtime loads from.

    Storage always resolves to an absolute path (NVMe when mounted, else a
    ``yolo`` folder under the working dir – see ``resolve_detection_storage_path``),
    so this is always a concrete path even on a unit without an NVMe.
    """
    from openfollow.video.detection import resolve_detection_storage_path

    return Path(resolve_detection_storage_path(storage_path)).expanduser() / "models"


def _discover_storage_models(storage_path: str) -> set[str]:
    """Return ``.onnx`` filenames present in ``<storage>/models``.

    An absent / empty / unreadable directory yields an empty set, so every
    catalogue entry renders unavailable (nothing on disk to load).
    """
    models_dir = _resolved_models_dir(storage_path)
    try:
        return {p.name for p in models_dir.iterdir() if p.is_file() and p.suffix.lower() == ".onnx"}
    except OSError:
        return set()


def _detection_installed_models(storage_path: str) -> list[dict[str, Any]]:
    """``{name, size_bytes, size_h}`` for each ``.onnx`` in ``<storage>/models``.

    Drives the deletable "Installed models" list, sorted by filename. A missing
    or unreadable directory yields an empty list.
    """
    models_dir = _resolved_models_dir(storage_path)
    out: list[dict[str, Any]] = []
    try:
        entries = sorted(models_dir.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return out
    for p in entries:
        if p.is_file() and p.suffix.lower() == ".onnx":
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            out.append({"name": p.name, "size_bytes": size, "size_h": f"{size / (1024**2):.1f} MiB"})
    return out


def _format_gib(num_bytes: int | None) -> str:
    """Human GiB string for a byte count, or ``"?"`` when unknown."""
    if num_bytes is None:
        return "?"
    return f"{num_bytes / (1024**3):.1f} GiB"


def _detection_storage_info(storage_path: str) -> dict[str, Any]:
    """Resolved models path + free / total space on its filesystem.

    Walks to the nearest existing ancestor so a not-yet-created storage dir
    still reports its filesystem's space (no directory is created here – render
    stays side-effect-free). ``free_bytes`` / ``total_bytes`` are ``None`` when
    the filesystem can't be stat'd; ``free_h`` / ``total_h`` are the matching
    human strings for the template.
    """
    import shutil

    models_dir = _resolved_models_dir(storage_path)
    probe = models_dir
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free_bytes: int | None = None
    total_bytes: int | None = None
    try:
        usage = shutil.disk_usage(str(probe))
        free_bytes, total_bytes = usage.free, usage.total
    except OSError:
        pass
    return {
        "path": str(models_dir),
        "free_bytes": free_bytes,
        "total_bytes": total_bytes,
        "free_h": _format_gib(free_bytes),
        "total_h": _format_gib(total_bytes),
    }


def _available_models(
    extras: dict[str, bool],
    saved_value: str,
    *,
    storage_path: str = "",
) -> list[tuple[str, str, bool]]:
    """Return ``(value, label, available)`` for every selectable model.

    The catalogue supplies friendly labels for the known YOLO lineup; any
    other ``.onnx`` file present in ``<storage_path>/models`` is surfaced by
    filename. ``available`` is True only when the ``detection`` (onnxruntime)
    extra is installed AND the file is on disk – a model that isn't present
    can't be loaded, so it renders disabled with an "(unavailable)" suffix.
    A ``saved_value`` that is neither catalogued nor on disk is preserved as a
    leading entry so saving the form doesn't overwrite a hand-edited value.
    """
    onnx_installed = extras.get("detection", False)
    on_disk = _discover_storage_models(storage_path)

    def available(value: str) -> bool:
        return onnx_installed and value in on_disk

    results: list[tuple[str, str, bool]] = []
    seen: set[str] = set()

    catalogue_values = {value for value, _ in _DETECTION_MODEL_CATALOGUE}
    if saved_value and saved_value not in catalogue_values and saved_value not in on_disk:
        results.append((saved_value, saved_value, available(saved_value)))
        seen.add(saved_value)

    for value, label in _DETECTION_MODEL_CATALOGUE:
        results.append((value, label, available(value)))
        seen.add(value)

    if on_disk:
        for value in sorted(on_disk):
            if value not in seen:
                results.append((value, value, available(value)))
                seen.add(value)

    return results


# Export subprocess stdout/stderr drains through a bounded deque so a long
# run can't OOM the web process and the OS pipe buffer can't fill and deadlock
# the child. Full log remains in journalctl.
_SUBPROCESS_TAIL_LINES = 40

# Trailing lines in the condensed warning log on non-zero exit. Kept small to
# fit one ``logger.warning`` line; the full tail is attached to the status slot
# for the UI's ``<pre>`` block.
_INSTALL_TAIL_VISIBLE_LINES = 4

# Model export downloads the YOLO weights then runs the torch→ONNX export, so
# it gets generous headroom.
_EXPORT_JOB_TIMEOUT_S = 1800

# Opset bounds for the model-export action. 7 is the floor the YOLO exporters
# accept; the upper bound tracks onnxruntime's supported range with headroom.
_EXPORT_OPSET_MIN = 7
_EXPORT_OPSET_MAX = 22
_EXPORT_OPSET_DEFAULT = 17


def _catalogue_model_values() -> frozenset[str]:
    """The ``.onnx`` filenames the export action will accept (allowlist)."""
    return frozenset(value for value, _label in _DETECTION_MODEL_CATALOGUE)


def _export_tools_available() -> bool:
    """True when the optional ``export`` extra (ultralytics) is importable."""
    return importlib.util.find_spec("ultralytics") is not None


# The model-export script shipped in the .deb / image (build-deb.sh installs it).
# Lets export work on a built appliance, not only a source checkout.
_PACKAGED_EXPORT_SCRIPT = Path("/usr/share/openfollow/scripts/export_onnx.py")


def _detection_export_script() -> Path | None:
    """Path to ``export_onnx.py``, or ``None`` if it isn't reachable.

    Resolves from a source checkout (``<repo>/scripts/export_onnx.py``, found via
    a ``pyproject.toml`` ancestor walk) or the packaged appliance location
    (``/usr/share/openfollow/scripts/export_onnx.py``).
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file():
            candidate = parent / "scripts" / "export_onnx.py"
            if candidate.is_file():
                return candidate
            break
    if _PACKAGED_EXPORT_SCRIPT.is_file():
        return _PACKAGED_EXPORT_SCRIPT
    return None


def _coerce_export_imgsz(raw: str, default: int) -> int:
    """Coerce + snap the export image size to a multiple of 32 in [160, 1280].

    Mirrors ``DetectionConfig.inference_size`` so an exported model's input size
    matches what the detector will feed it.
    """
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        value = default
    value = max(160, min(1280, value))
    return max(160, (value // 32) * 32)


def _coerce_export_opset(raw: str) -> int:
    """Coerce + clamp the ONNX opset to the supported export range."""
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return _EXPORT_OPSET_DEFAULT
    return max(_EXPORT_OPSET_MIN, min(_EXPORT_OPSET_MAX, value))


def _run_package_command(
    argv: list[str],
    *,
    timeout: int,
) -> tuple[int, str]:
    """Run ``argv`` with combined stdout/stderr drained into a bounded tail.

    Returns ``(returncode, tail_text)``. Raises ``subprocess.TimeoutExpired``
    on timeout (the child is killed first). Raises ``OSError`` if the
    executable can't be launched.
    """
    tail: collections.deque[str] = collections.deque(maxlen=_SUBPROCESS_TAIL_LINES)
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    def _drain() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            tail.append(line.rstrip("\n"))

    drainer = threading.Thread(target=_drain, daemon=True, name="pkg-cmd-drain")
    drainer.start()
    child_wedged = False
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        # pragma: no cover – wedged-child cleanup: SIGKILL-ignored
        # subprocess is a kernel-level pathology that doesn't reproduce
        # under pytest's process control.
        except subprocess.TimeoutExpired:  # pragma: no cover
            child_wedged = True
            if proc.stdout is not None:
                try:
                    proc.stdout.close()
                except OSError:
                    pass
            raise
        raise
    finally:
        if child_wedged:
            # Best-effort cleanup: the child is unreachable, so we cap the
            # join rather than block a request thread indefinitely. The
            # re-raised TimeoutExpired propagates regardless of drain state.
            drainer.join(timeout=1)
        else:
            # Join unbounded: the drainer only lives as long as
            # ``proc.stdout`` is open, which closes when the child exits
            # (via normal wait or the successful kill path). Joining with a
            # timeout here could race the deque read below and surface
            # "deque mutated during iteration".
            drainer.join()
    return rc, "\n".join(tail)


def _build_input_template_data(cfg: AppConfig) -> dict[str, Any]:
    """Build template variables for plugin-driven video source UI.

    Hides plugins whose ``is_available()`` returns False so picker dropdowns
    only show inputs that can actually run on the current host (e.g. the V4L2
    "USB Camera" plugin is hidden on macOS, where AVFoundation is offered
    instead).
    """
    from openfollow.video.inputs import get_available_registry

    registry = get_available_registry()
    available_inputs = [(k, v.display_name) for k, v in sorted(registry.items())]
    input_html_fragments: dict[str, str] = {}
    for iid, cls in sorted(registry.items()):
        values = cls.get_config_field_values(cfg)
        input_html_fragments[iid] = cls.web_ui_html(values)
    return {
        "available_inputs": available_inputs,
        "input_html_fragments": input_html_fragments,
    }


def _build_general_template_data(
    server: ConfigWebServer,
    cfg: AppConfig,
    *,
    saved: bool = False,
    restarting: bool = False,
    update_feedback: str = "",
) -> dict[str, Any]:
    """Build shared template context for the General/Network section.

    ``network_state`` feeds the Network read-only interface block, which
    has its own 5s HTMX poll against ``/section/general/network_state`` so
    the surrounding form fields aren't clobbered while the operator types.
    """
    data: dict[str, Any] = {
        "config": cfg,
        "saved": saved,
        "restarting": restarting,
        "local_ips": _get_local_ips(),
        "update_status": server.get_update_status(),
        "network_state": server.get_network_state(),
        "current_version": openfollow.__version__,
    }
    if update_feedback:
        data["update_feedback"] = update_feedback
    return data


def get_section_data(cfg: AppConfig, section: str) -> dict[str, Any] | None:
    """Return a serializable section view of config, or None if unknown."""
    if section == "video_source":
        result: dict[str, Any] = {
            "video_source_type": cfg.video_source_type,
            "stall_timeout": cfg.stall_timeout,
            "heal_interval": cfg.heal_interval,
        }
        from openfollow.video.inputs import get_registry

        for _iid, cls in get_registry().items():
            result.update(cls.get_config_field_values(cfg))
        return result
    if section == "psn":
        return {
            "psn_system_name": cfg.psn_system_name,
            "psn_mcast_ip": cfg.psn_mcast_ip,
            # Pin the source interface by name (eth0/wlan0).
            "psn_source_iface": cfg.psn_source_iface,
        }
    if section == "general":
        return {
            "psn_system_name": cfg.psn_system_name,
            "psn_mcast_ip": cfg.psn_mcast_ip,
            "psn_source_iface": cfg.psn_source_iface,
            "web_port": cfg.web_port,
            "web_pin": cfg.web_pin,
            "update_service_name": cfg.update_service_name,
        }
    section_attr = _SECTION_CONFIG_ATTRS.get(section)
    if section_attr is None:
        return None
    payload = asdict(getattr(cfg, section_attr))
    # Per-marker control/view selection is owned by the inline catalog UI
    # (POSTs to ``/api/markers/selection``); this payload carries visuals only.
    return payload


# Device-specific fields that must NEVER cross machines via peer-broadcast
# or full-config-import. ``psn_source_iface`` pins the local NIC and only
# makes sense for this device. Stripped at both ends (broadcaster-forward
# and peer-receive) so an out-of-date peer can't poison this device.
_DEVICE_LOCAL_FIELDS_BY_SECTION: dict[str, frozenset[str]] = {
    "psn": frozenset({"psn_source_iface"}),
    # ``web_pin`` (login credential) and ``web_port`` (local bind) are
    # device-local: a peer push / import must never rewrite this station's PIN
    # or listen port. Stripped at both broadcaster-forward and peer-receive.
    "general": frozenset({"psn_source_iface", "web_pin", "web_port"}),
    # The OTP source interface pins THIS device's NIC by name – like
    # ``psn_source_iface``, it must not cross machines via broadcast/import.
    "otp_output": frozenset({"source_iface"}),
    # ``storage_path`` is an absolute filesystem path on THIS device (NVMe
    # mount or a local working dir). A path from another machine is invalid
    # here – it must never cross via broadcast/import. Blank means auto-resolve.
    "detection": frozenset({"storage_path"}),
}


def strip_device_local_fields(
    section: str,
    data: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a copy of ``data`` with device-local fields removed.

    Idempotent: when ``section`` has no device-local fields registered
    (or none of them are present in ``data``) the result is a shallow
    copy of ``data`` so callers can always assume a fresh dict.
    """
    drop = _DEVICE_LOCAL_FIELDS_BY_SECTION.get(section, frozenset())
    return {k: v for k, v in data.items() if k not in drop}


def apply_section_data(cfg: AppConfig, section: str, data: Mapping[str, Any]) -> bool:
    """Apply section updates in-place. Returns False for unknown sections."""
    if section == "video_source":
        if "video_source_type" in data:
            from openfollow.video.inputs import get_available_input_ids

            v = _as_str(
                data["video_source_type"],
                cfg.video_source_type,
            )
            if v in get_available_input_ids():
                cfg.video_source_type = v
        # Match AppConfig.__post_init__'s coercion (finite, >= 0) here so the
        # web-save path follows the same rules as a TOML load – apply_section_data
        # mutates cfg in place and never re-runs __post_init__, and _as_float
        # alone would let a crafted inf/nan through and break int(timeout*1000).
        if "stall_timeout" in data:
            cfg.stall_timeout = _coerce_timer(data["stall_timeout"], cfg.stall_timeout)
        if "heal_interval" in data:
            cfg.heal_interval = _coerce_timer(data["heal_interval"], cfg.heal_interval)
        from openfollow.video.inputs import get_input_class

        input_cls = get_input_class(cfg.video_source_type)
        if input_cls is not None:
            input_cls.apply_config_fields(cfg, dict(data))
        return True

    if section == "psn":
        if "psn_system_name" in data:
            cfg.psn_system_name = _as_str(data["psn_system_name"], cfg.psn_system_name)
        if "psn_mcast_ip" in data:
            cfg.psn_mcast_ip = _as_str(data["psn_mcast_ip"], cfg.psn_mcast_ip)
        # Strip mirrors ``AppConfig.__post_init__`` so a saved ``" eth0 "``
        # doesn't desync the stored config from the runtime.
        if "psn_source_iface" in data:
            cfg.psn_source_iface = _as_str(
                data["psn_source_iface"],
                cfg.psn_source_iface,
            ).strip()
        return True

    if section == "general":
        if "psn_system_name" in data:
            cfg.psn_system_name = _as_str(data["psn_system_name"], cfg.psn_system_name)
        if "psn_mcast_ip" in data:
            cfg.psn_mcast_ip = _as_str(data["psn_mcast_ip"], cfg.psn_mcast_ip)
        if "psn_source_iface" in data:
            cfg.psn_source_iface = _as_str(
                data["psn_source_iface"],
                cfg.psn_source_iface,
            ).strip()
        # Enforce the declared bounds server-side: the FieldRules for these
        # are client-side only, so a crafted POST (or JS-off form) would
        # otherwise persist a non-digit PIN or an out-of-range port that the
        # next load silently clamps/keeps. Reject out-of-contract values.
        if "web_port" in data:
            port = _as_int(data["web_port"], cfg.web_port)
            if 1 <= port <= 65535:
                cfg.web_port = port
        if "web_pin" in data:
            pin = _as_str(data["web_pin"], cfg.web_pin).strip()
            if _is_valid_web_pin(pin):
                cfg.web_pin = pin
        if "update_github_repo" in data:
            repo = _as_str(data["update_github_repo"], cfg.update_github_repo).strip()
            if _is_valid_github_repo(repo):
                cfg.update_github_repo = repo
        if "update_service_name" in data:
            service_name = _as_str(data["update_service_name"], cfg.update_service_name).strip()
            if service_name and _is_valid_service_name(service_name):
                cfg.update_service_name = service_name
        # ``controlled_marker_ids`` + ``viewer_marker_ids`` are handled by
        # the ``marker`` section; the web UI no longer POSTs them here.
        return True

    section_attr = _SECTION_CONFIG_ATTRS.get(section)
    if section_attr is None:
        return False

    if section == "detection":
        _apply_parsed_updates(cfg.detection, data, _DETECTION_FIELD_PARSERS)
        if "inference_size" in data:
            cfg.detection.inference_size = max(
                160,
                _as_int(data["inference_size"], cfg.detection.inference_size),
            )
        if "pin_point" in data:
            val = _as_str(data["pin_point"], cfg.detection.pin_point)
            if val in ("top", "bottom"):
                cfg.detection.pin_point = val
        cfg.detection.__post_init__()
        return True

    if section == "trigger_zones":
        # Global settings only – zones list CRUD happens via /api/zones
        tz = cfg.trigger_zones
        _apply_parsed_updates(tz, data, _TRIGGER_ZONES_FIELD_PARSERS)
        tz.__post_init__()
        return True

    parser_map = _SECTION_FIELD_PARSERS.get(section)
    # pragma: no cover – defensively unreachable: every section in
    # ``_SECTION_CONFIG_ATTRS`` either returns early above (zones) or has
    # a corresponding entry in ``_SECTION_FIELD_PARSERS``.
    if parser_map is None:  # pragma: no cover
        return False
    _apply_parsed_updates(getattr(cfg, section_attr), data, parser_map)
    # Re-run the dataclass ``__post_init__`` after a web-form save so the
    # same coercion/clamping rules that protect hand-edited config.toml also
    # protect crafted POSTs. See "Validation contract" in CLAUDE.md.
    if section in ("controller", "gamepad", "keyboard", "mouse"):
        cfg.controller.__post_init__()
    elif section == "mouse3d":
        cfg.mouse3d.__post_init__()
    elif section == "grid":
        cfg.grid.__post_init__()
    elif section == "camera":
        cfg.camera.__post_init__()
    elif section in ("marker", "movement"):
        cfg.marker.__post_init__()
    elif section == "osc":
        cfg.osc.__post_init__()
    elif section == "operator_messages":
        cfg.operator_messages.__post_init__()
    elif section == "otp_output":
        cfg.otp_output.__post_init__()
    # pragma: no branch – exhaustive elif chain over the section keys;
    # the False-arm falls through to ``return True`` below.
    elif section == "rttrpm_output":  # pragma: no branch
        cfg.rttrpm_output.__post_init__()
    return True


def _as_str(value: Any, default: str) -> str:
    if value is None:
        return default
    return str(value)


def _as_float(value: Any, default: float) -> float:
    # OverflowError: ``float(10**5000)`` (huge JSON int reaching a float field
    # via /api/config/<section>) raises. Without this catch the request 500s
    # before ``__post_init__`` gets a chance to normalise.
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _coerce_timer(value: Any, default: float) -> float:
    """Coerce a recovery-timer field to a finite, non-negative float.

    Mirrors ``AppConfig.__post_init__``'s ``_coerce_float(..., lo=0.0)`` so the
    web-save path enforces the same contract as a TOML load (``apply_section_data``
    mutates cfg in place and never re-runs ``__post_init__``). Rejects non-finite
    inputs (``inf``/``nan``) to ``default`` – they'd otherwise pass ``float()``
    and later crash ``int(timeout * 1000)`` in the watchdog/heal timers.
    """
    out = _as_float(value, default)
    if not math.isfinite(out):
        return default
    return max(0.0, out)


def _as_float_or_zero(value: Any, default: float) -> float:
    """Variant of ``_as_float`` where **empty** input means "unset/disable"
    (collapse to ``0.0``) rather than "preserve current".

    Used by ``grid.max_height``, whose ``0.0`` sentinel disables ``[fz]`` /
    ``[ifz]`` rendering. Disambiguation:

    - ``None`` / empty / whitespace-only string → ``0.0`` (intentional clear).
    - Valid finite number → that number.
    - Anything else (non-numeric, ``inf`` / ``nan``, oversized) → ``default``,
      matching the "bad input → preserve current" contract so a crafted
      payload can't disable every binding by collapsing to ``0.0``.
    """
    if value is None:
        return 0.0
    if isinstance(value, str) and not value.strip():
        return 0.0
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(out):
        return default
    return out


def _as_optional_float(value: Any, default: float | None) -> float | None:
    """Parse to float; explicit None or blank string clears to None.

    When the field key is absent from the payload, ``_apply_parsed_updates``
    skips it entirely, so "preserve existing" is handled there, not here.
    """
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


# Per-section form fields carrying a physical length/speed (need
# imperial→metric parsing in imperial mode). Enumerated, NOT "every float
# field": px thicknesses, transparencies, FOV, framerates stay unitless.
_SECTION_LENGTH_FIELDS: dict[str, frozenset[str]] = {
    "camera": frozenset({"pos_x", "pos_y", "pos_z"}),
    "grid": frozenset(
        {
            "width",
            "depth",
            "spacing",
            "x_offset",
            "y_offset",
            "z_offset",
            "origin_length",
            "max_height",
        }
    ),
    "marker": frozenset({"ball_size", "crosshair_size", "ground_circle_size"}),
    "movement": frozenset({"default_pos_x", "default_pos_y", "default_pos_z"}),
    "trigger_zones": frozenset({"hysteresis"}),
}
_SECTION_SPEED_FIELDS: dict[str, frozenset[str]] = {
    "movement": frozenset({"min_speed", "max_speed", "move_speed"}),
}


def _unit_field_kind(section: str, field_name: str) -> str | None:
    """Return ``"length"`` / ``"speed"`` if *field_name* in *section* is a
    physical measurement (so it needs unit parsing), else ``None``."""
    if field_name in _SECTION_LENGTH_FIELDS.get(section, frozenset()):
        return "length"
    if field_name in _SECTION_SPEED_FIELDS.get(section, frozenset()):
        return "speed"
    return None


def _coerce_unit_input(
    section: str,
    field_name: str,
    raw: Any,
    unit_system: UnitSystem,
) -> tuple[Any, str | None]:
    """Convert an operator-typed length/speed value to a canonical metric
    numeric string so the existing metric parsers (``_as_float`` etc.)
    and validation bounds work unchanged.

    Returns ``(value, error)``. In metric mode, for non-unit fields, or
    for empty input, returns ``(raw, None)`` untouched. In imperial mode
    a parse failure returns ``(raw, <human error>)`` so the caller can
    surface it inline.
    """
    kind = _unit_field_kind(section, field_name)
    if kind is None or unit_system is UnitSystem.METRIC:
        return raw, None
    if not isinstance(raw, str) or not raw.strip():
        return raw, None
    try:
        meters = parse_length(raw, unit_system) if kind == "length" else parse_speed(raw, unit_system)
    except ValueError:
        if kind == "length":
            return raw, "Enter a length, e.g. 5 ft 6 in, 5'6\", or 1.5 m."
        return raw, "Enter a speed, e.g. 4.92 ft/s or 1.5 m/s."
    return repr(meters), None


def _mouse_bool_fields() -> tuple[str, ...]:
    """Bool checkboxes the mouse form submits.

    The scroll-wheel checkboxes are not rendered on macOS (the wheel can't be
    polled there), so they must be excluded from the save – ``_save_section_from_form``
    coerces any ``bool_fields`` entry missing from the POST to ``False``, which
    would otherwise clobber the stored ``mouse_wheel_*`` values on every macOS save.
    Mirrors the ``_is_macos`` branch in ``partials/mouse.tpl``.
    """
    fields = ["mouse_enabled", "mouse_double_click_reset"]
    if sys.platform != "darwin":
        fields += ["mouse_wheel_z_enabled", "mouse_wheel_invert"]
    return tuple(fields)


def _normalize_unit_fields(
    section: str,
    form_data: dict[str, Any],
    unit_system: UnitSystem,
) -> None:
    """In imperial mode, rewrite length/speed string fields of *form_data*
    in place to their metric numeric-string form. Unparseable values are
    left as-is; the section-POST path then silently preserves the field's
    current value (``_as_float`` / dataclass coercion falls back on a bad
    parse). The inline parse error is surfaced separately by the blur
    validator, not by the save."""
    if unit_system is UnitSystem.METRIC:
        return
    fields = _SECTION_LENGTH_FIELDS.get(section, frozenset()) | _SECTION_SPEED_FIELDS.get(section, frozenset())
    for field_name in fields:
        if field_name not in form_data:
            continue
        value, error = _coerce_unit_input(
            section,
            field_name,
            form_data[field_name],
            unit_system,
        )
        if error is None:
            form_data[field_name] = value


def _as_int(value: Any, default: int) -> int:
    # OverflowError: JSON ``1e400`` decodes to ``float('inf')`` and
    # ``int(float('inf'))`` raises. Fall back to default rather than 500.
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _as_positive_int(value: Any, default: int) -> int:
    """Like ``_as_int`` but clamps to ``>= 1``. For fields that divide by the value."""
    result = _as_int(value, default)
    return result if result >= 1 else 1


def _as_button_index(value: Any, default: int) -> int:
    """Parse a device button index where blank means "unbound" (-1).

    A cleared field (``None`` / empty / whitespace) maps to the ``-1`` unbound
    sentinel regardless of the prior value, so clearing a binding actually
    unbinds it. Non-numeric junk falls back to ``default`` (the validate path
    passes a sentinel here to surface a type error). For 3D Mouse button binds.
    """
    if value is None:
        return -1
    if isinstance(value, str) and not value.strip():
        return -1
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _as_optional_int(value: Any, default: int | None) -> int | None:
    """Like :func:`_as_int` but treats empty / whitespace-only / ``None``
    input as the unset state.

    For :class:`int | None` fields (today only ``OscTransmitterConfig.
    marker_id``). Distinguishing "cleared" (``None``) from "typed 0"
    (``0``) is load-bearing: the runtime and editor treat them differently.

    ``bool`` is an ``int`` subclass, so a crafted ``marker_id=true`` would
    otherwise save marker ``1``; ``True`` / ``False`` collapse to ``None``
    to match ``OscTransmitterConfig.__post_init__``.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _as_bool(value: Any, default: bool) -> bool:
    """Accept real bools or recognised string forms; anything else → ``default``.

    Mirror of ``configuration._coerce_bool`` so the JSON-API parser path
    follows the same "wrong-type input → default" contract that
    ``GridConfig.__post_init__`` (and every other ``_coerce_*`` helper)
    enforces. A loose ``bool(value)`` fallback would let a payload like
    ``{"origin_visible": 42}`` coerce to ``True`` and then pass through
    ``__post_init__`` unchallenged – the dataclass can't fall back to
    the declared default once the parser has already converted the
    type error away.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _as_int_list(value: Any, default: list[int]) -> list[int]:
    if value is None:
        return default
    if isinstance(value, str):
        if not value.strip():
            return default
        try:
            return [int(x.strip()) for x in value.split(",") if x.strip()]
        except ValueError:
            return default
    if isinstance(value, (list, tuple)):
        try:
            return [int(x) for x in value]
        except (TypeError, ValueError):
            return default
    return default


def _as_ip_list(value: Any, default: list[str]) -> list[str]:
    """Parse a list of IP addresses.

    Accepts either a comma-separated form-field string or a JSON list.
    Semantics are careful around the allow-all sentinel because empty
    list means "allow all" in ``OscInputHandler`` – the parser must not
    fail open on a user typo:

    - Explicit empty (``""`` or ``[]``) → return ``[]`` (caller intends
      to clear the allowlist).
    - Submitted value is non-empty but every entry is an invalid IP →
      return the caller-supplied ``default`` unchanged. Returning ``[]``
      here would silently disable the filter, turning a single typo
      (``192.168.1.999``) into "accept from any host".
    - Submitted value has at least one valid entry → return that valid
      subset (invalid entries are dropped silently).
    - Non-string, non-list input → return ``default``.
    """
    raw: list[str]
    if isinstance(value, str):
        if not value.strip():
            return []
        raw = [x.strip() for x in value.split(",") if x.strip()]
    elif isinstance(value, (list, tuple)):
        if not value:
            return []
        raw = [str(x).strip() for x in value if str(x).strip()]
    else:
        return default

    # ``raw`` may still be empty here (e.g., value was a whitespace-only
    # CSV like "   ,  ,"). Treat that as "explicit empty" – the user
    # clearly tried to type something but it all whitespaced out.
    if not raw:
        return []

    cleaned: list[str] = []
    for entry in raw:
        try:
            ipaddress.ip_address(entry)
        except ValueError:
            continue
        cleaned.append(entry)
    # Fail closed: a non-empty submission that yields zero valid IPs
    # looks like a typo, not "clear the filter". Preserving ``default``
    # keeps whatever the operator had configured.
    if not cleaned:
        return default
    return cleaned


_FieldParser = Callable[[Any, Any], Any]

_SECTION_FIELD_PARSERS: dict[str, dict[str, _FieldParser]] = {
    "camera": {
        "pos_x": _as_float,
        "pos_y": _as_float,
        "pos_z": _as_float,
        "pitch": _as_float,
        "yaw": _as_float,
        "roll": _as_float,
        "fov": _as_float,
        "sensor_width_mm": _as_optional_float,
        "focal_length_mm": _as_optional_float,
        "lens_k1": _as_float,
        "lens_k2": _as_float,
    },
    "grid": {
        "visible": _as_bool,
        "width": _as_float,
        "depth": _as_float,
        "spacing": _as_float,
        "x_offset": _as_float,
        "y_offset": _as_float,
        "z_offset": _as_float,
        # Empty ``max_height`` clears to 0 (unset), not preserve-current;
        # see ``_as_float_or_zero``.
        "max_height": _as_float_or_zero,
        "color": _as_str,
        "thickness": _as_positive_int,
        "transparency": _as_float,
        "origin_visible": _as_bool,
        "origin_length": _as_float,
        "origin_thickness": _as_int,
    },
    "movement": {
        "min_speed": _as_float,
        "max_speed": _as_float,
        "move_speed": _as_float,
        "default_pos_x": _as_float,
        "default_pos_y": _as_float,
        "default_pos_z": _as_float,
    },
    "marker": {
        "ball_visible": _as_bool,
        "ball_size": _as_float,
        "transparency": _as_float,
        "crosshair_visible": _as_bool,
        "crosshair_size": _as_float,
        "crosshair_color": _as_str,
        "crosshair_thickness": _as_int,
        "drop_line": _as_bool,
        "drop_line_thickness": _as_int,
        "ground_circle": _as_bool,
        "ground_circle_size": _as_float,
        "ground_circle_filled": _as_bool,
        "z_display_from_stage": _as_bool,
    },
    "controller": {
        "enabled": _as_bool,
        "keyboard_enabled": _as_bool,
        "mouse_enabled": _as_bool,
        "mouse_hysteresis_px": _as_int,
        "mouse_smoothing": _as_float,
        "mouse_max_y": _as_float,
        "mouse_wheel_z_enabled": _as_bool,
        "mouse_wheel_invert": _as_bool,
        "mouse_wheel_z_step": _as_float,
        "mouse_double_click_reset": _as_bool,
        "deadzone": _as_float,
        "invert_y": _as_bool,
        "curve": _as_str,
        "btn_reset": _as_str,
        "btn_source_select": _as_str,
        "btn_toggle_help": _as_str,
        "btn_speed_down": _as_str,
        "btn_speed_up": _as_str,
        "btn_move_z_down": _as_str,
        "btn_move_z_up": _as_str,
        "btn_toggle_zones": _as_str,
        "btn_next_marker": _as_str,
        "btn_prev_marker": _as_str,
        "btn_settings": _as_str,
        "btn_menu_confirm": _as_str,
        "btn_menu_cancel": _as_str,
        "btn_clear_messages": _as_str,
        "move_xy_stick": _as_str,
        "marker_fader_stick": _as_str,
        "marker_fader_max_speed_s": _as_float,
        "map_a": _as_str,
        "map_b": _as_str,
        "map_x": _as_str,
        "map_y": _as_str,
        "map_back": _as_str,
        "map_start": _as_str,
        "map_lb": _as_str,
        "map_rb": _as_str,
        "map_dpad_up": _as_str,
        "map_dpad_down": _as_str,
        "map_dpad_left": _as_str,
        "map_dpad_right": _as_str,
        "key_move_layout": _as_str,
        "key_move_z_up": _as_str,
        "key_move_z_down": _as_str,
        "key_reset": _as_str,
        "key_toggle_help": _as_str,
        "key_toggle_zones": _as_str,
        "key_speed_down": _as_str,
        "key_speed_up": _as_str,
        "key_next_marker": _as_str,
        "key_prev_marker": _as_str,
        "key_settings": _as_str,
        "key_clear_messages": _as_str,
    },
    "osc": {
        "enabled": _as_bool,
        "port": _as_int,
        "allowed_sender_ips": _as_ip_list,
        "multicast_group": _as_str,
    },
    "operator_messages": {
        "enabled": _as_bool,
        "position": _as_str,
        "max_visible": _as_int,
        "route_by_marker": _as_bool,
        "scale": _as_float,
    },
    "otp_output": {
        "enabled": _as_bool,
        "port": _as_int,
        "source_iface": _as_str,
        "system_number": _as_int,
        "priority": _as_int,
    },
    "rttrpm_output": {
        "enabled": _as_bool,
        "host": _as_str,
        "port": _as_int,
        "fps": _as_positive_int,
        "context": _as_int,
    },
}

# Gamepad / keyboard / mouse are slices of the same ControllerConfig; they
# share the "controller" parser map and rely on _apply_parsed_updates
# ignoring fields that aren't present in the submitted form.
_SECTION_FIELD_PARSERS["gamepad"] = _SECTION_FIELD_PARSERS["controller"]
_SECTION_FIELD_PARSERS["keyboard"] = _SECTION_FIELD_PARSERS["controller"]
_SECTION_FIELD_PARSERS["mouse"] = _SECTION_FIELD_PARSERS["controller"]

# 3D Mouse: built from the shared axis / button constants so the parser map
# can't drift from the dataclass fields. Mirrored by FIELD_RULES["mouse3d"].
_mouse3d_parsers: dict[str, _FieldParser] = {
    "enabled": _as_bool,
    "deadzone": _as_float,
    "curve": _as_str,
}
for _axis in MOUSE3D_AXES:
    _mouse3d_parsers[f"map_{_axis}"] = _as_str
    _mouse3d_parsers[f"sens_{_axis}"] = _as_float
    _mouse3d_parsers[f"invert_{_axis}"] = _as_bool
for _btn in MOUSE3D_BUTTON_FIELDS:
    _mouse3d_parsers[_btn] = _as_button_index
_SECTION_FIELD_PARSERS["mouse3d"] = _mouse3d_parsers


_TRIGGER_ZONES_FIELD_PARSERS: dict[str, _FieldParser] = {
    "enabled": _as_bool,
    "show_overlay": _as_bool,
    "eval_fps": _as_int,
    "debounce_ms": _as_int,
    "hysteresis": _as_float,
}


_ZONE_FIELD_PARSERS: dict[str, _FieldParser] = {
    "name": _as_str,
    "color": _as_str,
    "trigger_source": _as_str,
    "osc_address_first_entry": _as_str,
    "osc_address_additional_entry": _as_str,
    "osc_address_partial_exit": _as_str,
    "osc_address_final_exit": _as_str,
    "destination_id": _as_str,
    "enabled": _as_bool,
}


def _parse_vertices(value: Any, current: list[list[float]]) -> list[list[float]]:
    """Accept a list of [x, y] pairs and return cleaned float pairs.

    Falls back to ``current`` when ``value`` is not a list. This mirrors the
    other zone field parsers (they keep the existing value on a type mismatch
    rather than destructively blanking it) and prevents a malformed client
    payload – e.g. ``{"vertices": null}`` – from silently wiping a polygon.
    """
    if not isinstance(value, list):
        return current
    cleaned: list[list[float]] = []
    for v in value:
        if isinstance(v, (list, tuple)) and len(v) >= 2:
            try:
                cleaned.append([float(v[0]), float(v[1])])
            except (TypeError, ValueError):
                continue
    return cleaned


def _parse_triggered_by(value: Any, current: list[int]) -> list[int]:
    """Per-zone marker filter. Coerce a JSON list of int-like values to
    ``list[int]``; fall back to ``current`` on a non-list shape.

    Accepts ``[0, 1, 5]`` or string ints ``["0", "1"]``. Empty list means
    "clear the filter"; ``null``/scalar/dict means "leave it alone".
    """
    if not isinstance(value, list):
        return current
    cleaned: list[int] = []
    for entry in value:
        try:
            cleaned.append(int(entry))
        except (TypeError, ValueError):
            continue
    return cleaned


def _apply_zone_fields(zone: TriggerZoneConfig, data: Mapping[str, Any]) -> None:
    """Apply a subset of fields from ``data`` to ``zone`` (no validation beyond types)."""
    for field_name, parser in _ZONE_FIELD_PARSERS.items():
        if field_name in data:
            current = getattr(zone, field_name)
            setattr(zone, field_name, parser(data[field_name], current))
    if "vertices" in data:
        zone.vertices = _parse_vertices(data["vertices"], zone.vertices)
    # ``triggered_by`` is outside ``_ZONE_FIELD_PARSERS`` (its parser takes
    # a list, not a scalar). Absent key means "leave as is" so a partial
    # PUT can toggle ``enabled`` without clearing the filter.
    if "triggered_by" in data:
        zone.triggered_by = _parse_triggered_by(data["triggered_by"], zone.triggered_by)
    zone.__post_init__()


# Per-row OSC binding form parsers, top-level fields only. The trigger
# sub-table is parsed by :func:`_parse_trigger_subtable`; address + args
# arrive in a combined ``osc_message`` field (first whitespace token is
# the address, the rest are args).
_OSC_BINDING_FIELD_PARSERS: dict[str, _FieldParser] = {
    "enabled": _as_bool,
    "name": _as_str,
    # Connection is chosen via a shared OSC destination; empty = no
    # destination selected (the row skips sending).
    "destination_id": _as_str,
    # ``markers`` is a comma-separated list of marker ids / ``cN`` aliases /
    # ``all``; empty means "no default marker". ``_coerce_marker_tokens``
    # vets + canonicalises (``__post_init__`` re-runs it on save).
    "markers": lambda value, _default: _coerce_marker_tokens(value),
    # Default virtual fader: empty → ``None``, 1..8 preserved, junk collapses
    # to ``None`` in ``__post_init__``.
    "default_fader": _as_optional_int,
}


# Per-destination form parsers, mirroring the dataclass coercion.
_OSC_DESTINATION_FIELD_PARSERS: dict[str, _FieldParser] = {
    "name": _as_str,
    "host": _as_str,
    "port": _as_int,
    "protocol": _as_str,
    "framing": _as_str,
}


def _parse_osc_message(
    data: Mapping[str, Any],
    current_address: str,
    current_args: list[str],
) -> tuple[str, list[str]] | None:
    """Pull the combined ``osc_message`` form field into address + args.

    Web-form wrapper around :func:`openfollow.osc.parser.tokenize_osc_message`
    so OSC Output and Trigger Zones share one quote-aware tokeniser.

    Returns ``None`` when the form lacks an ``osc_message`` key, signalling
    "leave address + args alone" so a partial save (e.g. toggling
    ``enabled``) doesn't blank the message. Otherwise returns
    ``(address, args)`` (first token is the address; quoted strings stay
    single args: ``/cue/go "My Cue" 1.5`` → ``("/cue/go", ["My Cue", "1.5"])``).

    On unclosed-quote (``ValueError``), preserve ``current_address`` /
    ``current_args`` rather than corrupt the row. The blur validator gates
    Save, so reaching this at save-time means a POST bypassed validation.
    """
    if "osc_message" not in data:
        return None
    raw = _as_str(data["osc_message"], "").strip()
    if not raw:
        return ("", [])
    from openfollow.osc.parser import tokenize_osc_message

    try:
        return tokenize_osc_message(raw)
    except ValueError:
        return (current_address, list(current_args))


def _parse_trigger_subtable(
    data: Mapping[str, Any],
    current_kind: str,
) -> dict[str, Any]:
    """Pull the flat ``trigger.*`` form keys into the dict-shape that
    :func:`openfollow.configuration._trigger_from_dict` accepts.

    Strips the ``trigger.`` prefix into a sub-dict. The wire discriminator
    is ``trigger.type``; the dataclass field is ``kind``, so this emits
    ``"kind": <value>`` regardless of wire spelling. ``modifiers`` is a
    checkbox group (repeat-key in Bottle's ``MultiDict``), so callers pass
    a ``getall``-style accumulator. Falls back to ``current_kind`` when the
    form omits the discriminator (partial save). ``trigger.kind`` is read
    as a backwards-compatible alias for ``trigger.type``.
    """
    raw_kind = data.get("trigger.type", data.get("trigger.kind", current_kind))
    kind = _as_str(raw_kind, current_kind)
    out: dict[str, Any] = {"kind": kind}
    if kind == "stream":
        if "trigger.rate_hz" in data:
            out["rate_hz"] = _as_int(data["trigger.rate_hz"], 30)
        # send-always vs send-on-change mode + per-axis minimum-change
        # threshold (m). Both optional; omitting keeps the existing value.
        if "trigger.mode" in data:
            out["mode"] = _as_str(data["trigger.mode"], "always")
        if "trigger.min_change_m" in data:
            out["min_change_m"] = _as_float(
                data["trigger.min_change_m"],
                0.05,
            )
    elif kind == "hotkey":
        if "trigger.key" in data:
            out["key"] = _as_str(data["trigger.key"], "")
        # ``modifiers`` may be passed as a list (Bottle MultiDict
        # ``getall``) or a single-string fallback for tests using a
        # plain dict. Both shapes are accepted by the configuration
        # layer's ``HotkeyTrigger.__post_init__``.
        if "trigger.modifiers" in data:
            mods = data["trigger.modifiers"]
            if isinstance(mods, list):
                out["modifiers"] = mods
            else:
                out["modifiers"] = [mods] if mods else []
        if "trigger.edge" in data:
            out["edge"] = _as_str(data["trigger.edge"], "press")
    elif kind == "controller_button":
        if "trigger.button" in data:
            out["button"] = _as_str(data["trigger.button"], "")
        if "trigger.edge" in data:
            out["edge"] = _as_str(data["trigger.edge"], "press")
    elif kind == "midi_message":
        # The MIDI message type is ``trigger.midi_type`` (NOT ``trigger.type``,
        # which is the kind discriminator). Channel/number/value use the
        # "empty means Any" convention via ``_as_optional_int`` (``int | None``);
        # patch id is an int where ``0`` means "any patch".
        if "trigger.midi_type" in data:
            out["type"] = _as_str(data["trigger.midi_type"], "note_on")
        if "trigger.patch_id" in data:
            out["patch_id"] = _as_int(data["trigger.patch_id"], 0)
        if "trigger.midi_channel" in data:
            out["channel"] = _as_optional_int(
                data["trigger.midi_channel"],
                default=None,
            )
        if "trigger.midi_number" in data:
            out["number"] = _as_optional_int(
                data["trigger.midi_number"],
                default=None,
            )
        if "trigger.midi_value" in data:
            out["value"] = _as_optional_int(
                data["trigger.midi_value"],
                default=None,
            )
    elif kind == "fader_on_change":
        # ``rate_hz`` reuses the Stream wire key; ``_snap_to_valid_rate``
        # handles junk values.
        #
        # The change source is one combined dropdown,
        # ``trigger.fader_source``, whose option values are prefixed:
        # ``index:<n>`` (indexed virtual fader) or ``marker:<id>`` (a
        # per-controlled-marker gamepad fader). Pack ONLY the chosen
        # discriminator; the apply path rebuilds via ``_trigger_from_dict``,
        # so the other field falls back to its default (``marker_id`` → 0,
        # ``fader`` → 1). The bare ``trigger.fader`` key is read as a
        # back-compat fallback.
        source = data.get("trigger.fader_source")
        if isinstance(source, str) and source.startswith("marker:"):
            out["marker_id"] = _as_int(source[len("marker:") :], 0)
        elif isinstance(source, str) and source.startswith("index:"):
            out["fader"] = _as_int(source[len("index:") :], 1)
        elif "trigger.fader" in data:
            out["fader"] = _as_int(data["trigger.fader"], 1)
        if "trigger.rate_hz" in data:
            out["rate_hz"] = _as_int(data["trigger.rate_hz"], 30)
    return out


def _effective_default_marker_id(
    markers: list[str],
    registered: frozenset[int],
) -> int | None:
    """Pick a representative default-marker id for the unresolved-pill
    heuristic from a row's ``markers`` tokens.

    A bare ``[x]`` resolves at runtime when the row names *any* usable
    default marker. ``all`` / ``cN`` are dynamic – treated as resolvable
    whenever the station controls at least one marker (the controller
    drives a controlled marker; ``all`` enumerates them). Numeric tokens
    count only when controlled. Returns the lowest candidate id, or
    ``None`` when nothing usable is named (so ``[x]`` shows unresolved)."""
    candidates: set[int] = set()
    has_dynamic = False
    for token in markers:
        if token == MARKER_TOKEN_ALL or token.startswith("c"):
            has_dynamic = True
            continue
        mid = int(token)
        if mid in registered:
            candidates.add(mid)
    if has_dynamic and registered:
        candidates.update(registered)
    return min(candidates) if candidates else None


def _osc_binding_marker_label(token: str, catalog: Any) -> str:
    """Human label for one resolved ``markers`` token, shown in the row
    summary badge + the nested secondary chips.

    Numeric ids render as ``"<catalog name> (<id>)"`` (falling back to
    ``"Marker <id>"``); controller aliases render as ``"Controller cN"``."""
    if token.startswith("c"):
        return f"Controller {token}"
    mid = int(token)
    entry = catalog.get(mid) if catalog is not None else None
    name = (entry.name.strip() if entry is not None and entry.name else "") or f"Marker {mid}"
    return f"{name} ({mid})"


def _osc_binding_marker_entry(
    token: str,
    catalog: Any,
    controlled_set: set[int],
) -> dict[str, Any]:
    """One marker chip: its label plus whether this station controls it.

    Controller aliases (``cN``) resolve to a controlled marker at runtime, so
    they're always reported controlled; only an explicit numeric id can be
    uncontrolled (its send is dropped at runtime → the chip is marked)."""
    controlled = True if token.startswith("c") else int(token) in controlled_set
    return {"label": _osc_binding_marker_label(token, catalog), "controlled": controlled}


def _osc_binding_marker_display(
    cfg: AppConfig,
    catalog: Any,
) -> dict[str, dict[str, Any]]:
    """Per-row marker display data for the bindings partial.

    Each entry is ``{"header": <str|None>, "nested": [<chip>, …],
    "markers_unusable": <bool>}`` where a chip is ``{"label",
    "controlled"}``:

    - **0 markers** → no header, no nested.
    - **1 marker** → shown inline in the header badge (no nested list);
      there's no "primary vs secondary" to confuse.
    - **>1 markers** → *every* marker (primary included) renders as a nested
      chip so they read uniformly; no header badge.

    ``all`` expands to one chip per controlled marker. ``markers_unusable``
    is ``True`` when the row names markers but this station controls none of
    them (it can't send any) – the template reddens the row's status dot
    from it, and each nested chip carries its own flag."""
    controlled = sorted(cfg.controlled_marker_ids)
    controlled_set = set(controlled)
    out: dict[str, dict[str, Any]] = {}
    for row in cfg.osc_transmitters.transmitters:
        markers = row.markers
        if markers == [MARKER_TOKEN_ALL]:
            chips = [{"label": _osc_binding_marker_label(str(mid), catalog), "controlled": True} for mid in controlled]
        else:
            chips = [_osc_binding_marker_entry(t, catalog, controlled_set) for t in markers]
        out[row.id] = {
            "header": chips[0]["label"] if len(chips) == 1 else None,
            "nested": chips if len(chips) > 1 else [],
            "markers_unusable": bool(chips) and not any(c["controlled"] for c in chips),
        }
    return out


def _osc_binding_unresolved_blur_error(
    query: Mapping[str, Any],
    cfg: AppConfig,
) -> str | None:
    """Surface inline blur errors for unresolved placeholders on the
    ``osc_binding`` form.

    A default-marker placeholder needs ``marker_id`` set AND registered;
    an explicit ``[x:markerN]`` needs ``N`` registered. Reads message +
    marker_id from the form's ``hx-vals`` payload (``query``) and the
    registry from ``cfg.controlled_marker_ids``. ``None`` means no inline
    error to surface.
    """
    # Reuse ``_parse_osc_message`` so blur tokenisation tracks the Save
    # path 1:1. It returns ``None`` only when ``osc_message`` is missing;
    # treat that as an empty message (no inline error).
    parsed = _parse_osc_message(query, "", [])
    if parsed is None:
        return None
    address, args = parsed
    if not address and not args:
        return None
    registered = frozenset(cfg.controlled_marker_ids)
    markers = _coerce_marker_tokens(query.get("markers", ""))
    marker_id = _effective_default_marker_id(markers, registered)
    from openfollow.osc.template import (
        compile_template,
        token_has_explicit_index,
        unresolved_placeholders,
    )

    seen: set[str] = set()
    unresolved: list[str] = []
    for tpl in (address, *args):
        for token in unresolved_placeholders(
            compile_template(tpl),
            default_marker_id=marker_id,
            registered_marker_ids=registered,
            grid_max_height=cfg.grid.max_height,
        ):
            if token not in seen:
                unresolved.append(token)
                seen.add(token)
    if not unresolved:
        return None
    # Split into "needs default" vs "explicit-target missing" so the
    # message names the actionable fix per category. ``unresolved`` is
    # already the operator-facing token form (``"[x]"`` / ``"[x:7]"``).
    # Classify via the grammar, not a ``:`` sniff – a transform can carry
    # a colon. Per-token validity (malformed / non-controlled entries) is
    # surfaced by the field-level ``markers`` validator; this arm only flags
    # the cross-field "you use [x] but name no usable default marker".
    # Every unresolved token is either explicit-index or not, so the two
    # lists partition the (non-empty) ``unresolved`` set: at least one is
    # non-empty here, so ``parts`` below is never empty.
    default_tokens = [t for t in unresolved if not token_has_explicit_index(t)]
    explicit_tokens = [t for t in unresolved if token_has_explicit_index(t)]
    parts: list[str] = []
    if default_tokens:
        parts.append(
            f"{', '.join(default_tokens)} needs a default marker. Set 'Default markers' to a controlled id, "
            "a controller alias (c1, c2, …), or 'all'."
        )
    if explicit_tokens:
        parts.append(f"{', '.join(explicit_tokens)} references a marker that isn't registered.")
    return " ".join(parts)


def _virtual_fader_names_for_form(cfg: AppConfig) -> list[tuple[int, str]]:
    """List of ``(index, display_name)`` pairs for the OSC binding form's
    fader dropdowns.

    Display name falls back to ``"Fader N"`` when unset.
    ``VirtualFadersConfig.__post_init__`` pads/trims to
    :data:`VIRTUAL_FADER_COUNT`, so a plain ``enumerate`` yields exactly
    that many rows without a bounds check.
    """
    return [(idx, fader.name.strip() or f"Fader {idx}") for idx, fader in enumerate(cfg.virtual_faders.faders, start=1)]


def _marker_fader_names_for_form(
    server: ConfigWebServer,
) -> list[tuple[int, str]]:
    """List of ``(marker_id, display_name)`` pairs for the OSC binding
    form's Fader-on-Change dropdown – the per-controlled-marker gamepad
    faders, shown beside the eight indexed faders.

    Uses ``server.marker_fader_values()`` so labels match the MIDI page.
    Name falls back to ``"Marker N"`` with no catalog entry. Empty when no
    markers are controlled or no substrate is wired.
    """
    return [
        (entry["marker_id"], entry["name"] or f"Marker {entry['marker_id']}") for entry in server.marker_fader_values()
    ]


def _midi_patches_for_form(cfg: AppConfig) -> list[dict[str, Any]]:
    """MIDI patches for the source/trigger dropdowns.

    Returns ``[{"id": <int>, "label": "<id> – <alias or port name>"}]`` in the
    operator's MIDI page order, so a patch is selectable by id even before
    it's named. The dropdown's value is the patch id; ``0`` is "any patch".
    """
    return [{"id": patch.id, "label": patch.label} for patch in cfg.midi.patches if patch.id >= 1]


def _osc_binding_form_sources(cfg: AppConfig) -> dict[str, Any]:
    """Bundled fader / device-alias lists for the OSC binding trigger forms.

    Hoisted out of ``index.tpl`` so the template doesn't import private
    helpers. The same dict feeds the section refresh path so initial render
    and HTMX re-renders see identical data.
    """
    return {
        "virtual_fader_names": _virtual_fader_names_for_form(cfg),
        "midi_patches": _midi_patches_for_form(cfg),
    }


def _render_midi_capture_status(
    cfg: AppConfig,
    row_id: str,
    poll: dict[str, Any],
) -> Any:
    """Render the MIDI Learn capture status partial for the given row.

    The captured branch needs the full ``valid_midi_types`` list and
    device aliases so the OOB-swap fragments re-render the trigger form's
    selects with the matched option pre-selected; a flat
    ``<option value="X" selected>`` would clobber the rest of the dropdown.
    """
    from openfollow.configuration import VALID_MIDI_MESSAGE_TYPES

    return template(
        "partials/osc_midi_capture_status",
        row_id=row_id,
        poll=poll,
        valid_midi_types=VALID_MIDI_MESSAGE_TYPES,
        midi_patches=_midi_patches_for_form(cfg),
    )


def _render_midi_fader_capture_status(
    cfg: AppConfig,
    fader_index: int,
    poll: dict[str, Any],
) -> Any:
    """Render the Fader Learn capture status partial.

    Sibling to :func:`_render_midi_capture_status` for the fader detail
    form's Learn button. The captured branch needs ``midi_patches`` and
    ``valid_fader_midi_types`` so the OOB-swap fragments can re-render the
    fader's Patch / Type selects with the matched option pre-selected.
    """
    from openfollow.configuration import VALID_FADER_MIDI_TYPES

    return template(
        "partials/midi_fader_capture_status",
        fader_index=fader_index,
        poll=poll,
        valid_fader_midi_types=VALID_FADER_MIDI_TYPES,
        midi_patches=_midi_patches_for_form(cfg),
    )


def _row_unresolved_placeholders(
    row: OscTransmitterConfig,
    registered_marker_ids: frozenset[int],
    *,
    grid_max_height: float = 0.0,
) -> tuple[str, ...]:
    """Bracketed placeholder tokens in ``row``'s address + args that can't
    resolve given the row's ``markers`` and the registered-marker registry.

    The web bindings partial uses this to:

    - Render unresolved pills with ``data-unresolved="true"``.
    - Mark the ``Enabled`` checkbox ``data-osc-unresolved="true"`` so the
      POST handler coerces ``enabled=False`` until the operator resolves
      the dependency. A custom attribute (not ``aria-invalid``) is used so
      the shared ``refreshFormGate`` doesn't disable Save on first render
      (it keys off ``aria-invalid``), keeping the "Save with enabled=False"
      workflow available.

    Order is address-then-args appearance, duplicates collapsed across the
    row; see :func:`openfollow.osc.template.unresolved_placeholders`.
    """
    from openfollow.osc.template import (
        compile_template,
        unresolved_placeholders,
    )

    effective_marker_id = _effective_default_marker_id(row.markers, registered_marker_ids)
    out: list[str] = []
    seen: set[str] = set()
    for tpl in (row.address, *row.args):
        for token in unresolved_placeholders(
            compile_template(tpl),
            default_marker_id=effective_marker_id,
            registered_marker_ids=registered_marker_ids,
            grid_max_height=grid_max_height,
        ):
            if token not in seen:
                out.append(token)
                seen.add(token)
    return tuple(out)


def _apply_osc_binding_fields(
    row: OscTransmitterConfig,
    data: Mapping[str, Any],
    *,
    registered_marker_ids: frozenset[int] | None = None,
    grid_max_height: float = 0.0,
) -> None:
    """Apply a posted form to one OSC binding row. Mirrors
    :func:`_apply_zone_fields` – type-coerces top-level fields, parses the
    trigger sub-table separately, then re-runs ``__post_init__``.

    When ``registered_marker_ids`` is supplied, coerces ``row.enabled =
    False`` if the row's templates reference any unresolved placeholder,
    so the row stays inert at the transport layer until the dependency
    resolves. Callers without the registry pass ``None`` to skip the gate.
    """
    for field_name, parser in _OSC_BINDING_FIELD_PARSERS.items():
        if field_name in data:
            current = getattr(row, field_name)
            setattr(row, field_name, parser(data[field_name], current))
    parsed_message = _parse_osc_message(data, row.address, row.args)
    if parsed_message is not None:
        row.address, row.args = parsed_message
    # ``trigger.*`` keys present → re-parse the whole trigger sub-table.
    if any(k.startswith("trigger.") for k in data):
        current_kind = getattr(row.trigger, "kind", "stream")
        row.trigger = _parse_trigger_subtable(data, current_kind)
    row.__post_init__()
    if registered_marker_ids is not None and row.enabled:
        if _row_unresolved_placeholders(
            row,
            registered_marker_ids,
            grid_max_height=grid_max_height,
        ):
            row.enabled = False


def _apply_osc_destination_fields(
    dest: OscDestinationConfig,
    data: Mapping[str, Any],
) -> None:
    """Type-coerce posted fields onto a destination, then re-run
    ``__post_init__`` so bounds / choices match a TOML load."""
    for field_name, parser in _OSC_DESTINATION_FIELD_PARSERS.items():
        if field_name in data:
            current = getattr(dest, field_name)
            setattr(dest, field_name, parser(data[field_name], current))
    dest.__post_init__()


_DETECTION_FIELD_PARSERS: dict[str, _FieldParser] = {
    "enabled": _as_bool,
    "model": _as_str,
    "storage_path": lambda value, default: _as_str(value, default).strip(),
    "preprocess_clahe": _as_bool,
    "confidence": _as_float,
    "interval_ms": _as_int,
    "show_boxes": _as_bool,
    "show_labels": _as_bool,
    "box_color": _as_str,
    "box_thickness": _as_int,
    "max_persons": _as_int,
    "pin_marker": _as_bool,
    "pin_marker_id": _as_int,
    "smoothing": _as_float,
    "prediction": _as_float,
    "grace_period_ms": _as_int,
    "pin_mode": _as_str,
    "assist_radius_m": _as_float,
    "assist_strength": _as_float,
}


def _apply_parsed_updates(
    target: Any,
    data: Mapping[str, Any],
    parser_map: Mapping[str, _FieldParser],
) -> None:
    for field_name, parser in parser_map.items():
        if field_name not in data:
            continue
        current = getattr(target, field_name)
        setattr(target, field_name, parser(data[field_name], current))


def _is_valid_github_repo(value: str) -> bool:
    """Accept an ``owner/repo`` GitHub repository slug."""
    return bool(value and _GITHUB_REPO_RE.fullmatch(value.strip()))


def _is_valid_service_name(value: str) -> bool:
    candidate = value.strip()
    # Reject a leading ``-``: the value is appended to ``systemctl restart``
    # and a leading-dash token would otherwise be parsed as an option.
    if not candidate or candidate.startswith("-"):
        return False
    return bool(_SERVICE_NAME_RE.fullmatch(candidate))


def _is_valid_web_pin(value: str) -> bool:
    """Empty (auth disabled) or 1-32 ASCII digits – mirrors the ``web_pin``
    FieldRule so a crafted POST can't persist a non-digit / over-length PIN."""
    return value == "" or (value.isascii() and value.isdigit() and len(value) <= 32)


def _config_dict_redacted(cfg: AppConfig) -> dict[str, Any]:
    """``asdict(cfg)`` with device-local fields removed, so they never leave the
    device via export / ``/api/config`` / peer broadcast.

    ``web_pin`` is the login credential – stripped so it never travels in
    cleartext. ``detection.storage_path`` is an absolute path that only makes
    sense on this host (a path from another machine would be unwritable here),
    so it is stripped too. On import an absent value keeps the current one
    (``_apply_import_data`` restores both), so the round-trip can't clear them.
    """
    d = asdict(cfg)
    d.pop("web_pin", None)
    # ``detection`` is always present (asdict of a dataclass field); drop the
    # host-local storage path so it never travels to another machine.
    d["detection"].pop("storage_path", None)
    return d


# Sections that travel through the config export/import file (so a
# ``destination_id`` and its target move together) but are NEVER real-time
# shared between stations: each station keeps its own OSC routing + zones.
_BROADCAST_EXCLUDED_SECTIONS = frozenset(
    {"osc_destinations", "osc_transmitters", "trigger_zones"},
)


def _strip_broadcast_excluded(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a full-config dict without the sections that must
    not be real-time-shared to peers (they still export/import via file)."""
    return {k: v for k, v in data.items() if k not in _BROADCAST_EXCLUDED_SECTIONS}


_T = TypeVar("_T")


def _find_by_id(items: Sequence[_T], item_id: str) -> tuple[int, _T] | None:
    """Index-and-item lookup by ``.id`` for the id-keyed config lists (OSC
    transmitters, OSC destinations). ``None`` when nothing matches, so a route
    can 404 on stale UI state (a row deleted in another tab) without crashing.
    """
    for idx, item in enumerate(items):
        if getattr(item, "id", None) == item_id:
            return idx, item
    return None


def _swap_for_direction(items: MutableSequence[Any], idx: int, direction: str) -> bool:
    """Swap ``items[idx]`` one step ``"up"`` / ``"down"`` in place. No-op at
    the list edge or for an unknown direction. Returns ``True`` only when a
    swap actually happened, so the caller persists + flags ``saved`` on a real
    move rather than on a button-mash against the boundary."""
    target = idx
    if direction == "up" and idx > 0:
        target = idx - 1
    elif direction == "down" and idx < len(items) - 1:
        target = idx + 1
    if target == idx:
        return False
    items[idx], items[target] = items[target], items[idx]
    return True


def _apply_import_data(
    current_cfg: AppConfig,
    data: dict[str, Any],
    *,
    skip_restart_sections: bool = False,
) -> AppConfig:
    """Build a new config from import data, preserving the device-specific
    network pin.

    *skip_restart_sections* survives in the API for backwards compatibility
    but no longer gates anything: every section is live-reloadable.

    ``psn_source_iface`` is preserved across imports – importing a config
    from another box must NOT clobber this device's chosen interface.
    """
    cfg = copy.deepcopy(current_cfg)
    original_iface = cfg.psn_source_iface
    # ``web_pin`` (login credential) and ``web_port`` (local bind) are
    # device-local – an imported config must not rewrite this station's PIN
    # or listen port. Captured here and restored after the section applies.
    original_pin = cfg.web_pin
    original_port = cfg.web_port
    # ``detection.storage_path`` is an absolute path on THIS host – a path from
    # the exporting machine would be unwritable here. Keep the device's own.
    original_storage_path = cfg.detection.storage_path

    # General section (top-level scalar fields)
    apply_section_data(cfg, "general", data)

    # Sections that are always live-reloadable.
    for section in (
        "camera",
        "grid",
        "controller",
        "osc",
        "otp_output",
        "rttrpm_output",
        "detection",
    ):
        if section in data and isinstance(data[section], dict):
            apply_section_data(cfg, section, data[section])
    apply_section_data(cfg, "video_source", data)

    # OSC transmitters + destinations: rebuild the lists wholesale so the
    # references (a transmitter/zone ``destination_id``) and their targets
    # travel together through the config file. (They are excluded from
    # real-time peer broadcast – see ``_strip_broadcast_excluded``.)
    from openfollow.configuration import (
        OscDestinationsConfig,
        OscTransmittersConfig,
    )

    if "osc_transmitters" in data and isinstance(data["osc_transmitters"], dict):
        rows = data["osc_transmitters"].get("transmitters")
        if isinstance(rows, list):
            cfg.osc_transmitters = OscTransmittersConfig(transmitters=rows)
    if "osc_destinations" in data and isinstance(data["osc_destinations"], dict):
        dests = data["osc_destinations"].get("destinations")
        if isinstance(dests, list):
            cfg.osc_destinations = OscDestinationsConfig(destinations=dests)

    # Trigger zones: import global settings + zones list atomically
    if "trigger_zones" in data and isinstance(data["trigger_zones"], dict):
        tz_data = dict(data["trigger_zones"])
        raw_zones = tz_data.pop("zones", None)
        apply_section_data(cfg, "trigger_zones", tz_data)
        if isinstance(raw_zones, list):
            new_zones: list[TriggerZoneConfig] = []
            for item in raw_zones:
                if not isinstance(item, dict):
                    continue
                zone = TriggerZoneConfig()
                _apply_zone_fields(zone, item)
                new_zones.append(zone)
            cfg.trigger_zones.zones = new_zones

    # Marker config feeds both "marker" and "movement" parser maps
    if "marker" in data and isinstance(data["marker"], dict):
        apply_section_data(cfg, "marker", data["marker"])
        apply_section_data(cfg, "movement", data["marker"])

    # UI preferences (unit system + experimental-features visibility gate).
    # Not device-local, so they round-trip on import. ``ui`` is not a routable
    # /section/<name> form (it is saved via /settings/units + /settings/experimental),
    # so it has no apply_section_data branch – apply it inline here, then re-run
    # __post_init__ so a crafted/legacy payload is coerced like a TOML load.
    if "ui" in data and isinstance(data["ui"], dict):
        ui_data = data["ui"]
        # Assign the raw imported values and let __post_init__ coerce, so a
        # present-but-invalid field falls back to the *declared* default
        # (metric / False) exactly like a TOML load – not to this device's
        # current value. Absent fields are skipped, preserving the current.
        if "unit_system" in ui_data:
            cfg.ui.unit_system = ui_data["unit_system"]
        if "show_experimental_features" in ui_data:
            cfg.ui.show_experimental_features = ui_data["show_experimental_features"]
        cfg.ui.__post_init__()

    # Fields not covered by any section
    if "window_width" in data:
        cfg.window_width = _as_int(data["window_width"], cfg.window_width)
    if "window_height" in data:
        cfg.window_height = _as_int(data["window_height"], cfg.window_height)

    # Restore device-local fields (network pin, login PIN, listen port,
    # detection storage path).
    cfg.psn_source_iface = original_iface
    cfg.web_pin = original_pin
    cfg.web_port = original_port
    cfg.detection.storage_path = original_storage_path
    return cfg


def _import_needs_restart(old: AppConfig, new: AppConfig) -> list[str]:
    """Return human-readable reasons the imported config requires a restart.

    No config-detectable diff triggers a restart today (every section
    applies live). Kept as a placeholder: if a section regains a
    restart-only path, add the diff check here and have
    ``api_import_config`` consult this helper again.
    """
    return []


def _load_json_body() -> Any:
    """Parse the request body as JSON.

    Returns the parsed value (dict, list, str, number, bool) or ``None`` on
    malformed/empty input. Callers are responsible for checking the type of
    the returned value – most routes expect a JSON object and must also
    reject non-dict inputs with HTTP 400.
    """
    try:
        data = json.loads(request.body.read().decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        # Non-UTF-8 bytes raise UnicodeDecodeError before json.loads runs;
        # treat it as a malformed body (400) rather than an uncaught 500.
        response.status = 400
        return None
    # JSON literal `null` parses to Python None without raising; treat it as
    # an invalid body and let callers' existing ``if data is None`` branches
    # surface the error with the correct 400 status.
    if data is None:
        response.status = 400
    return data


# The wizard solves the pinhole pose; lens distortion (lens_k1/lens_k2) is kept
# separate from this vector because the DLT solve is pinhole. The wizard applies
# the distortion warp around the pinhole projection instead (see the project /
# unproject / solve endpoints), so the corner-pinning overlay bows to match a
# fisheye snapshot and the solve undistorts the pinned corners first.
_WIZARD_CAMERA_FIELDS = ("pos_x", "pos_y", "pos_z", "pitch", "yaw", "roll", "fov")


def _wizard_lens_coeffs(cam: Any) -> tuple[float, float]:
    """Extract clamped lens-distortion coefficients from a wizard camera dict.

    Absent or unparseable values fall back to ``0.0`` (pinhole), so an older
    client that posts no coefficients keeps the previous behaviour. Bounds mirror
    ``CameraConfig.__post_init__``.
    """

    def _clamp(value: Any, lo: float, hi: float) -> float:
        try:
            f = float(value)
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(f):
            return 0.0
        return max(lo, min(hi, f))

    if not isinstance(cam, dict):
        return 0.0, 0.0
    return _clamp(cam.get("lens_k1", 0.0), -0.4, 0.4), _clamp(cam.get("lens_k2", 0.0), -0.2, 0.2)


def _wizard_camera_params(cam: Any) -> Any:
    """Coerce a wizard camera dict into the np.float64 parameter vector.

    Raises ``KeyError``/``TypeError``/``ValueError`` on bad input so the
    endpoints can convert any failure into a uniform HTTP 400 response.
    Imported lazily to keep numpy off the import path of routes that
    don't use it.
    """
    import numpy as np

    if not isinstance(cam, dict):
        raise TypeError("camera must be an object")
    params = np.array(
        [float(cam[k]) for k in _WIZARD_CAMERA_FIELDS],
        dtype=np.float64,
    )
    # Reject a degenerate fov here so the endpoint returns 400, not a 500 from
    # the divide-by-zero in the solver. Bounds mirror CameraConfig.__post_init__.
    fov = float(params[6])
    if not 1.0 <= fov <= 179.0:
        raise ValueError(f"fov must be within [1, 179] degrees, got {fov}")
    return params


def _require_wizard_canvas(img_w: float, img_h: float) -> None:
    """Reject a degenerate canvas before the solver divides by it.

    Raises ``ValueError`` so the wizard endpoints surface a 400 instead of a
    500 from ``canvas_w / canvas_h`` (or the focal-length divide).
    """
    # math.isfinite rejects NaN/Inf, which a bare ``<= 0`` lets through
    # (``NaN <= 0`` is False) – json.loads accepts NaN/Infinity, so a crafted
    # body could otherwise feed non-finite dims into the solver and emit a
    # non-standard-JSON "NaN" response instead of a clean 400.
    if not math.isfinite(img_w) or not math.isfinite(img_h) or img_w <= 0.0 or img_h <= 0.0:
        raise ValueError(f"image_width and image_height must be finite values > 0, got {img_w}x{img_h}")


def _repo_root_for_diagnostics() -> Path | None:
    """Return the OpenFollow repo root if this process is running from
    a git checkout, else ``None``. Used by the diagnostics bundle's
    runtime-versions section to surface the HEAD sha + branch +
    dirty flag – empty when the operator is running an installed
    package with no .git nearby."""
    candidate = Path(__file__).resolve().parent.parent.parent
    return candidate if (candidate / ".git").exists() else None


# Repo-root in a checkout; copied to /usr/share/openfollow by the .deb so the
# on-device browser reads these offline.
_SHARE_DOC_DIR = Path("/usr/share/openfollow")


def _bundled_doc_path(name: str) -> Path | None:
    """Path to a bundled repo-root doc (LICENSE / THIRD_PARTY_NOTICES.md), or None."""
    for candidate in (Path(__file__).resolve().parents[2] / name, _SHARE_DOC_DIR / name):
        if candidate.is_file():
            return candidate
    return None


def _license_file_path() -> Path | None:
    """Path to the bundled full-text ``LICENSE`` if present, else ``None``."""
    return _bundled_doc_path("LICENSE")


def _read_license_text() -> str | None:
    """Verbatim ``LICENSE`` text for inline /about display, or ``None``."""
    path = _license_file_path()
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _rendered_doc_html(name: str) -> str | None:
    """Rendered HTML of a bundled markdown doc, or ``None``."""
    path = _bundled_doc_path(name)
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return render_help_markdown(text)


def _third_party_notices_html() -> str | None:
    return _rendered_doc_html("THIRD_PARTY_NOTICES.md")


def _written_offer_html() -> str | None:
    return _rendered_doc_html("WRITTEN_OFFER.md")


def _build_diagnostics_providers(
    server: ConfigWebServer,
    cfg: AppConfig,
) -> diagnostics.DiagnosticsProviders:
    """Wire ``ConfigWebServer`` accessors into ``DiagnosticsProviders``
    callables. Each closes over ``server`` / ``cfg`` so providers read
    fresh state even if config is hot-reloaded between bundle generations.

    ``gamepad_names`` derives from the already-wired
    ``gamepad_runtime_provider`` rather than a dedicated hook (its snapshot
    dicts carry each pad's ``name``). ``worker_thread_tracebacks`` stays
    ``None`` and degrades to a "[not applicable]" sentinel.
    """
    # ``gamepad_names`` reuses the gamepad runtime snapshot. ``None`` when
    # unwired keeps ``render_usb_table``'s "subsystem not available" footer
    # meaningful (vs. an empty-but-present list).
    gamepad_runtime = server.gamepad_runtime_provider
    gamepad_names: Callable[[], list[str]] | None
    if gamepad_runtime is None:
        gamepad_names = None
    else:
        runtime = gamepad_runtime

        def _gamepad_names() -> list[str]:
            # Drop empty/whitespace names for parity with the
            # midi_port_names / camera_names providers.
            return [name for g in (runtime() or []) if (name := g.get("name", "").strip())]

        gamepad_names = _gamepad_names
    return diagnostics.DiagnosticsProviders(
        web_port_configured=lambda: cfg.web_port,
        web_port_display=lambda: server.display_port,
        process_uptime_s=lambda: _format_uptime(server.process_uptime_s),
        process_pid=os.getpid,
        beacon_sender_health=lambda: {
            "alive": server.beacon_sender.is_alive,
            "consecutive_errors": server.beacon_sender.consecutive_errors,
            "send_count": server.beacon_sender.send_count,
            "last_send_age_s": _monotonic_age(
                server.beacon_sender.last_send_ts,
            ),
        },
        beacon_receiver_health=lambda: {
            "alive": server.beacon_receiver.is_alive,
            "packets_received": server.beacon_receiver.packets_received,
            "last_recv_age_s": _monotonic_age(
                server.beacon_receiver.last_recv_ts,
            ),
        },
        known_peers=lambda: [
            {
                "name": p.name,
                "ip": p.ip,
                "web_port": p.web_port,
                "last_seen_age_s": f"{max(0.0, time.time() - p.last_seen):.1f}",
            }
            for p in server.get_peers()
        ],
        iface_ip=lambda: server.local_ip,
        config_redacted_toml=lambda: diagnostics.redact_web_pin(
            _config_to_toml(cfg),
        ),
        request_semaphore_rejections=(lambda: server.request_semaphore_rejections),
        # Per-capability privilege state in the bundle.
        privilege_states=server.get_privilege_capability_states,
        # Live SDL gamepad snapshot (backend / GUID / calibration-match).
        # ``None`` when input isn't wired → bundle renders "[not applicable]".
        gamepad_runtime=server.gamepad_runtime_provider,
        # Recent I/O. ``None`` when unwired → bundle renders the
        # "(no events recorded)" / "[not applicable]" sentinels.
        recent_osc_sends=server.recent_osc_sends_provider,
        osc_multicast_status=server.osc_listener_status_provider,
        recent_midi_events=server.recent_midi_events_provider,
        # USB-visibility cross-reference indices.
        midi_port_names=server.midi_port_names_provider,
        gamepad_names=gamepad_names,
        camera_names=server.camera_names_provider,
    )


def _monotonic_age(ts: float) -> str:
    """Render the age of a monotonic timestamp as ``"N.Ns"`` or
    ``"never"`` for the 0.0 sentinel that the beacon-health
    properties use before the first event."""
    if ts <= 0.0:
        return "never"
    return f"{max(0.0, time.monotonic() - ts):.1f}s"


def _config_to_toml(cfg: AppConfig) -> str:
    """Serialise ``cfg`` to TOML for the bundle's "effective config" section.

    Shares ``save_config``'s serialisation via ``config_to_toml_dict`` so
    the output is paste-back-compatible with ``config.toml`` and int-keyed
    dicts like ``marker_move_speeds`` are stringified rather than crashing
    the dump."""
    import tomli_w

    from openfollow.configuration import config_to_toml_dict

    return tomli_w.dumps(config_to_toml_dict(cfg))


def _build_diagnostics_cards(
    server: ConfigWebServer,
    cfg: AppConfig,
) -> tuple[dict[str, Any], str]:
    """Build the inline summary cards + the journalctl warning
    banner. Separate from the bundle collector because the cards
    are HTMX-polled every 5 s and don't need the heavyweight
    sections (USB enumeration, full TOML render)."""
    sender = server.beacon_sender
    receiver = server.beacon_receiver
    peers = server.get_peers()
    # The card panel polls every 5 s. ``probe_log_source`` runs a real
    # ``journalctl -u <service> -n 0`` reachability probe (agreeing with
    # ``collect_log_tail`` on hosts where the binary is on PATH but returns
    # non-zero) and TTL-caches for 60 s, so the poll path averages 1
    # subprocess per minute. The bundle/log-tail endpoint still reads live.
    log_source_label = diagnostics.probe_log_source(
        cfg.update_service_name or None,
        ring=server.log_ring,
    )
    if log_source_label == "journalctl":
        log_chip = "ok"
        log_unavailable_warning = ""
        log_source_note = "Reading from systemd journal."
    elif "no log source available" in log_source_label:
        # Server constructed without a ``log_ring``: log-tail / bundle
        # download would surface ``[unavailable: ring buffer not
        # initialised]`` from ``collect_log_tail``. Surface that now on the
        # cards so the operator isn't promised a fallback that doesn't exist.
        log_chip = "off"
        log_unavailable_warning = (
            "No log source is available on this server. "
            "journalctl is unreachable and the in-memory ring "
            "wasn't initialised – diagnostics bundles will not "
            "include a log tail. Wire ``setup_logging``'s "
            "``RingBufferLogHandler`` into ``ConfigWebServer`` "
            "(see issue #179)."
        )
        log_source_note = "No log source."
    elif "no journald" in log_source_label:
        log_chip = "warn"
        log_unavailable_warning = (
            "No journald service name configured – "
            "the bundle will use the in-memory log buffer "
            "(only covers ~the last hour, lost on restart). "
            "Set ``update_service_name`` to enable journalctl."
        )
        log_source_note = "Using in-memory ring buffer."
    else:
        log_chip = "warn"
        log_unavailable_warning = (
            "journalctl is unavailable on this host. The bundle "
            "uses an in-memory log buffer, which only covers "
            "~the last hour and is lost on restart. For full "
            "historical logs, run the service as root or add "
            "the operator to the systemd-journal group."
        )
        log_source_note = "Falling back to in-memory ring buffer."

    cards: dict[str, Any] = {
        "web_port_configured": cfg.web_port,
        "web_port_display": server.display_port,
        "web_port_match": cfg.web_port == server.display_port,
        "uptime_human": _format_uptime(server.process_uptime_s),
        "sender_status": "running" if sender.is_alive else "stopped",
        "sender_chip": _alive_chip(sender.is_alive, sender.consecutive_errors),
        "sender_last_send": _monotonic_age(sender.last_send_ts),
        "sender_errors": sender.consecutive_errors,
        "sender_send_count": sender.send_count,
        "receiver_status": "running" if receiver.is_alive else "stopped",
        "receiver_chip": _alive_chip(receiver.is_alive, 0),
        "receiver_last_recv": _monotonic_age(receiver.last_recv_ts),
        "receiver_packet_count": receiver.packets_received,
        "peer_count": len(peers),
        "log_source_label": log_source_label.split(" (")[0],
        "log_source_note": log_source_note,
        "log_chip": log_chip,
    }
    return cards, log_unavailable_warning


def _alive_chip(is_alive: bool, errors: int) -> str:
    """Map a thread's alive flag + error counter to a chip class.
    Stopped → off; alive with errors → warn; alive + clean → ok."""
    if not is_alive:
        return "off"
    return "warn" if errors > 0 else "ok"


def _format_uptime(seconds: float) -> str:
    """Render ``time.monotonic()`` deltas as ``Hh Mm`` / ``Mm`` /
    ``Ns`` so the card stays readable from boot through multi-day
    uptime."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    hours, mins = divmod(minutes, 60)
    if hours == 0:
        return f"{mins}m"
    return f"{hours}h {mins}m"


def _probe_peer(ip: str, port: int, name: str, *, expected_port: int) -> dict[str, Any]:
    """One HTTP-HEAD probe to a peer's advertised web port (2 s timeout).
    Caller is responsible for the private-IP allowlist check; this helper
    assumes ``ip`` has already been vetted. The beacon-advertised ``port`` is
    untrusted, so it is checked against the expected-port allowlist here."""
    if not _is_allowed_peer_port(port, expected_port):
        return {
            "name": name,
            "ip": ip,
            "port": port,
            "ok": False,
            "status": 0,
            "ms": 0.0,
            "error": "unexpected port refused",
        }
    url = f"http://{ip}:{port}/"
    started = time.monotonic()
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=2.0) as resp:  # nosec B310
            elapsed_ms = (time.monotonic() - started) * 1000.0
            return {
                "name": name,
                "ip": ip,
                "port": port,
                "ok": resp.status < 500,
                "status": resp.status,
                "ms": elapsed_ms,
                "error": "",
            }
    except urllib.error.HTTPError as exc:
        return {
            "name": name,
            "ip": ip,
            "port": port,
            "ok": exc.code < 500,
            "status": exc.code,
            "ms": (time.monotonic() - started) * 1000.0,
            "error": "",
        }
    except Exception as exc:  # noqa: BLE001
        # ``URLError`` (refused / unreachable / DNS), ``TimeoutError``,
        # or anything else – surface a single line so the operator
        # sees the failure mode without parsing a stack trace.
        return {
            "name": name,
            "ip": ip,
            "port": port,
            "ok": False,
            "status": 0,
            "ms": (time.monotonic() - started) * 1000.0,
            "error": _short_probe_error(exc),
        }


def _short_probe_error(exc: BaseException) -> str:
    """Render a peer-probe exception as a one-liner suitable for the
    inline result row. Strips the stdlib's verbose wrapper text so
    "[Errno 61] Connection refused" reads as "Connection refused"."""
    msg = str(exc) or exc.__class__.__name__
    if "] " in msg:
        msg = msg.split("] ", 1)[1]
    return msg.split("\n", 1)[0][:120]


def _is_private_peer_ip(ip: str) -> bool:
    """Return True iff ``ip`` is safe to target for peer broadcast.

    OpenFollow peer sync is LAN-only by design: peers are discovered via
    link-local UDP multicast. Refusing non-private destinations closes an
    SSRF vector where a crafted beacon advertises an attacker-chosen
    (public or internal-service) target, which the broadcast helpers
    would otherwise POST to – leaking auth credentials and hitting
    arbitrary services inside the trust boundary.

    ``ipaddress.IPv4Address.is_private`` already covers RFC 1918, loopback,
    and link-local ranges, which matches the set of addresses a legitimate
    peer can plausibly have. Loopback and link-local (169.254/16, a real
    no-DHCP LAN class) stay allowed by design – loopback peers are used in
    tests. ``0.0.0.0`` / ``::`` are rejected (never a real peer; dial
    localhost on many stacks), and IPv4-mapped IPv6 is evaluated on its
    embedded IPv4 so a mapped public address can't slip past the check.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if addr.is_unspecified:
        return False
    return addr.is_private


# Outbound peer connections may only target a plausible OpenFollow web port.
# A homogeneous fleet serves on the same configured ``web_port``; the two
# conventional HTTP ports cover mixed defaults. The beacon-advertised port is
# otherwise attacker-controlled (any 1..65535) – a port-only SSRF that would
# steer the probe/broadcast helpers (and any signed config body) at an
# arbitrary service on the beaconing private host.
_DEFAULT_PEER_PORTS: frozenset[int] = frozenset({80, 8080})


def _is_allowed_peer_port(port: int, expected_port: int) -> bool:
    """True iff ``port`` is one a real OpenFollow peer plausibly serves on:
    this device's configured ``web_port`` or a conventional default."""
    return port == expected_port or port in _DEFAULT_PEER_PORTS


def _peer_auth_headers(
    pin: str,
    method: str,
    path: str,
    body: bytes,
) -> dict[str, str]:
    """Return HMAC auth headers for a peer request.

    Empty when ``pin`` is empty so unsecured deployments still work.
    """
    if not pin:
        return {}
    timestamp, signature = peer_auth.sign(pin, method, path, body)
    return {
        peer_auth.TIMESTAMP_HEADER: str(timestamp),
        peer_auth.SIGNATURE_HEADER: signature,
    }


def _send_config_to_peer(
    ip: str, port: int, section: str, data: dict[str, Any], pin: str = "", *, expected_port: int
) -> bool:
    """Send config update to a remote peer."""
    if not _is_private_peer_ip(ip):
        logger.warning("Refusing peer broadcast to non-private IP: %s", ip)
        return False
    if not _is_allowed_peer_port(port, expected_port):
        logger.warning("Refusing peer broadcast to unexpected port: %s:%d", ip, port)
        return False
    path = f"/api/config/{section}"
    body = json.dumps(data).encode("utf-8")
    url = f"http://{ip}:{port}{path}"
    try:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        headers.update(_peer_auth_headers(pin, "POST", path, body))
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310
            return bool(resp.status == 200)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return False


def _send_config_import_to_peer(ip: str, port: int, data: dict[str, Any], pin: str = "", *, expected_port: int) -> bool:
    """Send full config to a remote peer via the import endpoint."""
    if not _is_private_peer_ip(ip):
        logger.warning("Refusing peer broadcast to non-private IP: %s", ip)
        return False
    if not _is_allowed_peer_port(port, expected_port):
        logger.warning("Refusing peer broadcast to unexpected port: %s:%d", ip, port)
        return False
    path = "/api/config/import?skip_restart=1"
    body = json.dumps(data).encode("utf-8")
    url = f"http://{ip}:{port}{path}"
    try:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        headers.update(_peer_auth_headers(pin, "POST", path, body))
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
            return bool(resp.status == 200)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return False


# Per-peer HTTP timeouts inside _send_config_to_peer (5 s) and
# _send_config_import_to_peer (10 s) already cap each send. We add a small
# safety margin for pool/wait overhead and cap the overall broadcast so the
# request thread is never held open indefinitely.
_BROADCAST_SECTION_TIMEOUT_S = 7.0
_BROADCAST_IMPORT_TIMEOUT_S = 12.0

# Peer list is populated from unauthenticated multicast beacons; cap concurrent
# sends so a flood of beaconed peers can't spawn an unbounded number of
# threads. LAN fleets are realistically <10 devices; 16 is ample.
_BROADCAST_MAX_WORKERS = 16

# Hard cap on peers scheduled per broadcast. Peers come from unauthenticated
# multicast beacons and BeaconReceiver._peers is unbounded – without this cap
# a crafted beacon flood could force each broadcast to allocate an arbitrarily
# large number of worker threads and pending sends (memory/CPU DoS). Any
# peers beyond the cap are reported as failed without a send attempt.
_BROADCAST_MAX_PEERS = 64

# Global worker-slot gate across all in-flight broadcasts. We acquire
# a slot BEFORE spawning each worker thread, so the total number of
# live broadcast threads across the process is bounded by
# ``_BROADCAST_MAX_WORKERS`` regardless of how many broadcasts are in
# flight concurrently. Per-broadcast spawn-then-block would still
# accumulate hundreds of daemon threads under rapid repeated
# broadcasts; this arrangement does not.
_broadcast_semaphore = threading.BoundedSemaphore(_BROADCAST_MAX_WORKERS)


def _broadcast_to_peers(
    peers: list[PeerInfo],
    send: Callable[[PeerInfo], bool],
    overall_timeout: float,
) -> list[dict[str, Any]]:
    """Fan out ``send`` across ``peers`` in parallel, collect results.

    A module-level bounded semaphore (``_broadcast_semaphore``) is
    acquired BEFORE spawning each daemon worker thread, so the total
    live thread count across all concurrent broadcasts is capped at
    ``_BROADCAST_MAX_WORKERS`` – rapid repeated broadcasts cannot
    accumulate hundreds of blocked threads.

    Peers that don't complete within ``overall_timeout`` are reported
    ``success=False`` and the request thread returns immediately – any
    still-running worker keeps going in the background until the inner
    per-peer HTTP timeout fires (see ``_send_config_to_peer`` /
    ``_send_config_import_to_peer``), but daemon threads never delay
    interpreter shutdown.

    Peer lists longer than ``_BROADCAST_MAX_PEERS`` are truncated; the
    excess peers are reported ``success=False`` without a send attempt.
    Peers whose worker slot could not be acquired before
    ``overall_timeout`` expired are likewise reported ``success=False``
    without a send attempt.

    Order of the returned list matches the input ``peers`` list so the
    UI rendering is stable.
    """
    if not peers:
        return []

    if len(peers) > _BROADCAST_MAX_PEERS:
        scheduled = peers[:_BROADCAST_MAX_PEERS]
        skipped = peers[_BROADCAST_MAX_PEERS:]
        logger.warning(
            "Peer broadcast: %d peers exceeded the %d-per-broadcast cap; "
            "extra peers reported as failed without a send attempt.",
            len(skipped),
            _BROADCAST_MAX_PEERS,
        )
    else:
        scheduled = peers
        skipped = []

    results_by_key: dict[str, dict[str, Any]] = {}
    results_lock = threading.Lock()
    pending = threading.Semaphore(0)

    def _record(peer: PeerInfo, ok: bool) -> None:
        with results_lock:
            results_by_key[f"{peer.ip}:{peer.web_port}"] = {
                "name": peer.name,
                "ip": peer.ip,
                "success": ok,
            }

    def _run(peer: PeerInfo) -> None:
        try:
            try:
                ok = bool(send(peer))
            except Exception:
                logger.exception("Peer broadcast to %s raised", peer.ip)
                ok = False
            _record(peer, ok)
        finally:
            _broadcast_semaphore.release()
            pending.release()

    deadline = time.monotonic() + overall_timeout
    spawned = 0
    capacity_exhausted = False
    for peer in scheduled:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not _broadcast_semaphore.acquire(timeout=remaining):
            # Either the overall timeout expired or no worker slot came
            # free in time. Fall through to the missing-result fill-in
            # below, which records these as failures.
            capacity_exhausted = True
            break
        # If Thread() or start() raises (e.g. RuntimeError: can't start
        # new thread under resource pressure), _run never executes and
        # would leak the semaphore slot permanently – shrinking the
        # global worker pool on each failure. Release on any spawn
        # failure and record the peer as failed.
        try:
            t = threading.Thread(
                target=_run,
                args=(peer,),
                name=f"peer-broadcast-{peer.ip}",
                daemon=True,
            )
            t.start()
        except Exception:
            _broadcast_semaphore.release()
            logger.exception(
                "Peer broadcast: failed to spawn worker for %s; peer marked failed",
                peer.ip,
            )
            _record(peer, False)
            continue
        spawned += 1

    if capacity_exhausted:
        logger.warning(
            "Peer broadcast: worker capacity exhausted or %.1fs overall "
            "timeout hit before all peers could be dispatched; remaining "
            "peers marked failed.",
            overall_timeout,
        )

    for _ in range(spawned):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not pending.acquire(timeout=remaining):
            logger.warning(
                "Peer broadcast exceeded %.1fs overall timeout; slow peers marked failed.",
                overall_timeout,
            )
            break

    with results_lock:
        for peer in scheduled:
            key = f"{peer.ip}:{peer.web_port}"
            if key not in results_by_key:
                results_by_key[key] = {
                    "name": peer.name,
                    "ip": peer.ip,
                    "success": False,
                }
    for peer in skipped:
        _record(peer, False)

    return [results_by_key[f"{p.ip}:{p.web_port}"] for p in peers]


# ---------------------------------------------------------------------------
# Route Setup
# ---------------------------------------------------------------------------


_AUTH_COOKIE = "_openfollow_auth"


_marker_catalog_lock = threading.Lock()


def _catalog_entry_json(entry: Any) -> dict[str, Any]:
    return {
        "id": entry.id,
        "name": entry.name,
        "color": entry.color,
        "updated_at": entry.updated_at,
    }


def _resolve_marker_catalog_path(config_path: str, cfg_path_field: str) -> str:
    raw = cfg_path_field or "markers.toml"
    p = Path(raw)
    if p.is_absolute():
        return str(p)
    return str(Path(config_path).parent / p)


# Request-scoped config cache key. The ``before_request`` hook in
# ``setup_routes`` parses ``config.toml`` once per request and stashes the
# result here on ``request.environ``. Lifting the key + accessor to module
# scope lets route groups registered *outside* ``setup_routes`` – such as
# ``_register_marker_catalog_routes`` below – reuse that same per-request
# parse instead of hitting disk again.
_ENV_CONFIG_KEY = "openfollow.loaded_config"


def _port_suffix(netloc: str) -> str:
    """The ``:port`` suffix of a URL netloc, or ``""`` when no explicit port.

    Handles bracketed IPv6 host literals: a bare ``[fe80::1]`` has no port (the
    trailing hextet must not be read as one) – only ``[fe80::1]:8080`` does.
    """
    if netloc.startswith("["):
        _, sep, port = netloc.rpartition("]:")
        return f":{port}" if sep else ""
    _, sep, port = netloc.rpartition(":")
    return f":{port}" if sep else ""


def _request_config(server: ConfigWebServer) -> AppConfig:
    """Return the config parsed once for the current request.

    Reads the ``before_request``-populated ``request.environ`` cache and
    falls back to a fresh ``load_config`` (caching it on the environ) when
    the hook hasn't run – e.g. a handler exercised directly in a unit test.
    """
    cached = request.environ.get(_ENV_CONFIG_KEY)
    if cached is not None:
        return cached  # type: ignore[no-any-return]
    cfg = load_config(server.config_path)
    request.environ[_ENV_CONFIG_KEY] = cfg
    return cfg


def _register_marker_catalog_routes(
    app: Bottle,
    server: ConfigWebServer,
) -> None:
    """Register the shared catalog + per-station selection endpoints."""

    @app.get("/api/markers/catalog")
    def api_get_marker_catalog() -> Any:
        """JSON snapshot of the catalog + each peer's broadcast selection."""
        response.content_type = "application/json"
        catalog = server.get_marker_catalog()
        # Reuse the per-request config the ``before_request`` hook already
        # parsed (stashed on ``request.environ``) rather than re-reading
        # ``config.toml`` from disk a second time for this request.
        cfg = _request_config(server)
        if catalog is None:
            return json.dumps(
                {
                    "entries": [],
                    "peer_selections": [],
                    "this_station": {
                        "station_id": cfg.station_id,
                        "station_name": cfg.psn_system_name,
                        "controlled_ids": list(cfg.controlled_marker_ids),
                        "viewer_ids": list(cfg.viewer_marker_ids),
                    },
                }
            )
        entries = [_catalog_entry_json(e) for e in catalog.live_entries()]
        sync = server.get_marker_catalog_sync()
        peer_selections: list[dict[str, Any]] = []
        if sync is not None:
            for peer in sync.get_peer_selections():
                peer_selections.append(
                    {
                        "station_id": peer.station_id,
                        "station_name": peer.station_name,
                        "controlled_ids": list(peer.controlled_ids),
                        "viewer_ids": list(peer.viewer_ids),
                    }
                )
        return json.dumps(
            {
                "entries": entries,
                "peer_selections": peer_selections,
                "this_station": {
                    "station_id": cfg.station_id,
                    "station_name": cfg.psn_system_name,
                    "controlled_ids": list(cfg.controlled_marker_ids),
                    "viewer_ids": list(cfg.viewer_marker_ids),
                },
                "next_free_id": catalog.next_free_id(),
            }
        )

    @app.put("/api/markers/catalog/<marker_id:int>")
    def api_put_marker_catalog_entry(marker_id: int) -> Any:
        """Create or update a catalog entry."""
        response.content_type = "application/json"
        if marker_id < 1:
            response.status = 400
            return json.dumps({"error": "marker id must be >= 1"})
        data = _load_json_body()
        if not isinstance(data, dict):
            response.status = 400
            return json.dumps({"error": "Invalid JSON body"})
        name = data.get("name", "")
        color = data.get("color", "#ffffff")
        if not isinstance(name, str):
            name = ""
        if not isinstance(color, str):
            color = "#ffffff"
        catalog = server.get_marker_catalog()
        if catalog is None:
            response.status = 503
            return json.dumps({"error": "catalog unavailable"})
        cfg = load_config(server.config_path)
        with _marker_catalog_lock:
            # Snapshot the prior entry (tombstones included) so we can
            # roll back the in-memory upsert if persistence fails –
            # otherwise a 500 leaves the catalog showing the change in
            # the UI / poll responses even though the operator was
            # told the write failed, and the value never gets
            # ``request_delta``'d to peers.
            prev = catalog.get_any(marker_id)
            entry = catalog.upsert(marker_id, name, color, origin=cfg.station_id)
            try:
                save_catalog(
                    catalog,
                    _resolve_marker_catalog_path(
                        server.config_path,
                        cfg.markers_catalog_path,
                    ),
                )
            except OSError as exc:
                # LWW-aware rollback: only restore the prior entry if
                # our local upsert is still the current in-memory
                # state. The sync receiver thread can call
                # ``catalog.merge_entry`` while ``save_catalog`` is
                # blocked on the (failing) disk write; if a peer's
                # newer LWW write landed in between, that newer state
                # has to win – restoring ``prev`` would clobber it.
                # ``catalog.upsert`` returns the freshly-constructed
                # entry object; ``merge_entry`` replaces the dict
                # value (rather than mutating in place), so identity
                # equality is the exact check we want.
                if catalog.get_any(marker_id) is entry:
                    catalog.restore_entry(marker_id, prev)
                logger.exception("Failed to persist catalog: %s", exc)
                response.status = 500
                return json.dumps({"error": "could not persist catalog"})
        sync = server.get_marker_catalog_sync()
        if sync is not None:
            sync.request_delta([marker_id])
        return json.dumps({"success": True, "entry": _catalog_entry_json(entry)})

    @app.delete("/api/markers/catalog/<marker_id:int>")
    def api_delete_marker_catalog_entry(marker_id: int) -> Any:
        """Tombstone a catalog entry and drop it from this station's
        control/view selection.

        Pruning the selection (not just tombstoning the catalog) is what
        makes the live marker vanish immediately: writing ``config.toml``
        bumps its mtime, and the animate loop's config hot-reload then
        runs ``apply_runtime_config_changes``, which removes the PSN
        marker and unregisters it from the receiver / OTP / RTTrPM. Skip
        the prune and the marker keeps broadcasting until the next
        restart.
        """
        response.content_type = "application/json"
        if marker_id < 1:
            response.status = 400
            return json.dumps({"error": "marker id must be >= 1"})
        catalog = server.get_marker_catalog()
        if catalog is None:
            response.status = 503
            return json.dumps({"error": "catalog unavailable"})
        cfg = load_config(server.config_path)
        with _marker_catalog_lock:
            # Snapshot the pre-tombstone entry so we can restore it
            # if persistence fails – without the rollback the UI / poll
            # responses would show the entry deleted even though the
            # operator was told the delete failed, and the tombstone
            # never reaches peers via ``request_delta``.
            prev = catalog.get_any(marker_id)
            tomb = catalog.delete(marker_id, origin=cfg.station_id)
            if tomb is None:
                response.status = 404
                return json.dumps({"error": "unknown marker id"})
            try:
                save_catalog(
                    catalog,
                    _resolve_marker_catalog_path(
                        server.config_path,
                        cfg.markers_catalog_path,
                    ),
                )
            except OSError as exc:
                # LWW-aware rollback – see the matching comment in the
                # PUT handler above. A peer's newer ``merge_entry`` can
                # land while ``save_catalog`` is blocked on the failing
                # disk write; restoring ``prev`` unconditionally would
                # clobber that peer's newer state.
                if catalog.get_any(marker_id) is tomb:
                    catalog.restore_entry(marker_id, prev)
                logger.exception("Failed to persist catalog: %s", exc)
                response.status = 500
                return json.dumps({"error": "could not persist catalog"})
        # Drop the deleted id from this station's selection and persist.
        # The config mtime bump drives the animate loop's hot-reload,
        # which tears down the live PSN marker via
        # ``apply_runtime_config_changes``. Best-effort: the tombstone
        # above is the authoritative delete, so a failed config write
        # still leaves the marker gone from the catalog UI, and the
        # startup selection-prune reconciles it on the next restart.
        with _config_write_lock:
            fresh = load_config(server.config_path)
            new_controlled = [t for t in fresh.controlled_marker_ids if t != marker_id]
            new_viewer = [t for t in fresh.viewer_marker_ids if t != marker_id]
            if new_controlled != fresh.controlled_marker_ids or new_viewer != fresh.viewer_marker_ids:
                fresh.controlled_marker_ids = new_controlled
                fresh.viewer_marker_ids = new_viewer
                try:
                    save_config(fresh, server.config_path)
                except OSError:
                    logger.exception(
                        "Marker %s tombstoned but failed to prune it from "
                        "the selection in %s; live marker will persist "
                        "until the next restart reconciles it",
                        marker_id,
                        server.config_path,
                    )
        sync = server.get_marker_catalog_sync()
        if sync is not None:
            sync.request_delta([marker_id])
        return json.dumps({"success": True})

    @app.post("/api/markers/selection")
    def api_post_marker_selection() -> Any:
        """Persist this station's per-marker control/view selection."""
        response.content_type = "application/json"
        data = _load_json_body()
        if not isinstance(data, dict):
            response.status = 400
            return json.dumps({"error": "Invalid JSON body"})
        catalog = server.get_marker_catalog()
        if catalog is None:
            response.status = 503
            return json.dumps({"error": "catalog unavailable"})

        def _coerce(name: str) -> list[int] | None:
            raw = data.get(name, [])
            if not isinstance(raw, list):
                return None
            # De-duplicate while preserving input order. The downstream
            # HUD state builder iterates ``app._viewer_ids`` /
            # ``app._controlled_ids`` directly (see
            # ``services_marker_visuals.build_marker_visual_state``), so
            # a duplicate id in the persisted list would render two
            # marker cards for the same id – confusing the operator and
            # doubling the PSN/RTTrPM register-marker calls on hot
            # reload. The catalog UI's checkbox model can't produce
            # duplicates today, but a hand-crafted JSON POST can, so
            # we normalise at the persistence boundary.
            seen: set[int] = set()
            out: list[int] = []
            for v in raw:
                if isinstance(v, bool) or not isinstance(v, int):
                    return None
                if v < 1:
                    return None
                if catalog.get(v) is None:
                    return None
                if v in seen:
                    continue
                seen.add(v)
                out.append(v)
            return out

        controlled = _coerce("controlled_ids")
        viewer = _coerce("viewer_ids")
        if controlled is None or viewer is None:
            response.status = 400
            return json.dumps({"error": "invalid id list"})

        with _config_write_lock:
            cfg = load_config(server.config_path)
            cfg.controlled_marker_ids = controlled
            cfg.viewer_marker_ids = viewer
            save_config(cfg, server.config_path)
        return json.dumps({"success": True})


def _register_input_plugin_routes(
    app: Bottle,
    server: ConfigWebServer,
) -> None:
    """Register HTTP routes declared by video input plugins."""
    from openfollow.video.inputs import get_registry

    for _iid, cls in get_registry().items():
        routes = cls.web_routes()
        if not routes:
            continue
        instance = cls()
        for route in routes:
            handler = instance.get_web_route_handler(
                route.handler_name,
            )
            # pragma: no cover – every plugin-declared route's
            # ``handler_name`` resolves to a real method; the None arm
            # only fires for a malformed third-party plugin.
            if handler is None:  # pragma: no cover
                continue

            def _make_handler(
                h: Callable[..., Any],
                input_cls: type[VideoInputBase],
            ) -> Callable[[], Any]:
                def _handler() -> Any:
                    cfg = load_config(server.config_path)
                    config = input_cls.get_config_field_values(
                        cfg,
                    )
                    return h(config)

                return _handler

            app.route(
                route.path,
                method=route.method,
                callback=_make_handler(handler, cls),
            )


def setup_routes(app: Bottle, server: ConfigWebServer) -> None:
    """Register all routes on the Bottle app."""

    # Mirror bundled system templates into the operator's templates folder
    # on every start. Idempotent overwrite enforces the "system templates
    # are immutable" contract against direct filesystem tampering; the
    # bootstrap creates the destination folder on demand.
    try:
        seed_system_templates(_templates_root(server))
    except OSError as exc:  # pragma: no cover – defensive fallback for read-only mounts / perm errors
        # Filesystem-level failure (read-only mount, permission
        # error). Log and continue – the loader can still surface
        # operator-saved templates from ``user/`` if that subfolder
        # is writable, and a missing ``system/`` is a valid state
        # (the UI will show only user templates instead).
        logger.warning(
            "Could not seed system templates into %s: %s",
            _templates_root(server),
            exc,
        )

    # -- PIN authentication ---------------------------------------------------

    # One throttle per server: shared by ``/login`` (form) and the peer-auth
    # signature path so an attacker can't sidestep the lockout by alternating
    # vectors. Per-IP keyed.
    login_throttle = LoginThrottle()
    # Rejects a captured signed peer request replayed within the timestamp
    # window (the signature alone is valid + recent but single-use here).
    peer_replay_cache = peer_auth.ReplayCache()

    def _abort_if_locked(remote: str) -> None:
        """Raise 429 + ``Retry-After`` when ``remote`` is currently locked out.

        ``bottle.abort`` builds a fresh ``HTTPError`` and discards anything
        previously set on the thread-local ``response`` object, so the
        ``Retry-After`` header has to ride on the raised response itself.
        """
        remaining = login_throttle.remaining_lockout(remote)
        if remaining > 0.0:
            # ``ceil`` so a partial-second remainder rounds up to the next
            # whole second (HTTP Retry-After is integer-valued), and
            # ``max(1, …)`` so we never advertise "retry now" when we're
            # actually still locked. ``int(remaining) + 1`` would overshoot
            # by a whole second on integer remainders (e.g. cap == 30.0).
            retry_after = max(1, math.ceil(remaining))
            raise HTTPResponse(
                body="Too many failed authentication attempts",
                status=429,
                headers={"Retry-After": str(retry_after)},
            )

    def _begin_or_abort(remote: str) -> None:
        """Atomically reserve a single credential guess for ``remote`` or raise
        429. Serializes concurrent guesses for one IP so a burst of parallel
        requests can't each slip a guess through the window between the lockout
        check and the failure record. Must be followed by exactly one
        ``record_success`` / ``record_failure``.
        """
        wait = login_throttle.begin_attempt(remote)
        if wait > 0.0:
            retry_after = max(1, math.ceil(wait))
            raise HTTPResponse(
                body="Too many authentication attempts",
                status=429,
                headers={"Retry-After": str(retry_after)},
            )

    # Request-scoped config cache. ``_check_auth`` reads ``web_pin`` on every
    # request and the blur-validation handlers parse the same TOML again;
    # stashing the parsed config on ``request.environ`` collapses both to one
    # disk hit per request (the blur endpoint fires rapidly while typing).
    # Thin wrapper over module-level ``_request_config``.
    def _request_scoped_config() -> AppConfig:
        return _request_config(server)

    @app.hook("before_request")
    def _check_auth() -> Any:
        pin = _request_scoped_config().web_pin
        if not pin:
            return

        path = request.path
        if (
            path == "/login"
            or path.startswith("/assets/")
            or path == "/section/statistics"
            # About / license pages are AGPLv3 §5(d) "Appropriate Legal
            # Notices" reachable pre-auth; they expose no privileged state.
            or path == "/about"
            or path == "/about/license.txt"
        ):
            return
        # The global HTMX modal poll in ``base.tpl`` fires every 3 s,
        # including from ``/login``. Without this exemption the
        # unauthenticated branch below would ``HX-Redirect: /login`` the
        # poll, looping reload→poll→reload. The route gates on the auth
        # cookie and returns an empty partial when unauthenticated.
        if path == "/system/privilege/password/modal":
            return

        # Peer-to-peer (HMAC-signed) request. Replaces the legacy
        # X-Auth-Pin header that leaked the PIN over plain HTTP.
        signature = request.headers.get(peer_auth.SIGNATURE_HEADER, "")
        timestamp = request.headers.get(peer_auth.TIMESTAMP_HEADER, "")
        if signature and timestamp:
            remote = request.remote_addr or ""
            # Reject locked-out IPs *before* the body read + HMAC compute,
            # so a flood of bogus signatures can't keep us hashing.
            _abort_if_locked(remote)
            # Guard the pre-auth body read: ``peer_auth.verify`` must
            # hash the full body before the signature proves the sender,
            # so an unauthenticated client could otherwise force an
            # unbounded spool. Reject anything that either (a) does not
            # declare a Content-Length at all – bottle would read into a
            # tempfile of unpredictable size – or (b) declares more
            # than ``MAX_SIGNED_BODY_SIZE``.
            content_length = request.content_length
            if content_length is None or content_length > peer_auth.MAX_SIGNED_BODY_SIZE:
                abort(413, "Signed request body too large or unspecified")
            # Read one extra byte so a client that lies about its
            # Content-Length is also caught. Bottle will only have
            # buffered up to the declared Content-Length, so this read
            # is naturally bounded.
            body = request.body.read(peer_auth.MAX_SIGNED_BODY_SIZE + 1)
            # pragma: no cover – defence against a peer lying about
            # Content-Length (body longer than declared). Bottle only
            # buffers up to Content-Length so this read is naturally bounded;
            # reproducing the lie needs a hand-crafted WSGI test client.
            if len(body) > peer_auth.MAX_SIGNED_BODY_SIZE:  # pragma: no cover
                abort(413, "Signed request body exceeded declared Content-Length")
            # Rewind so downstream handlers (json.loads, request.forms,
            # etc.) still see the full body.
            request.body.seek(0)
            qs = request.environ.get("QUERY_STRING", "")
            signed_path = path + (f"?{qs}" if qs else "")
            # Reserve the guess atomically so concurrent signature checks for
            # this IP serialize to one per window (the early lockout check
            # above is a cheap pre-filter; this is the race-free gate).
            _begin_or_abort(remote)
            if peer_auth.verify(
                pin,
                request.method,
                signed_path,
                body,
                timestamp,
                signature,
            ):
                # Valid + recent, but reject a replay of the same signature
                # within the window (counts as a failure to escalate the
                # throttle against a replay flood).
                if not peer_replay_cache.check_and_record(signature):
                    login_throttle.record_failure(remote)
                    abort(401, "Replayed peer request")
                login_throttle.record_success(remote)
                return
            # Signature present but invalid: fail closed rather than
            # falling through to cookie auth so a broken peer can't
            # accidentally rely on stale cookies.
            login_throttle.record_failure(remote)
            abort(401, "Invalid peer signature")

        # CSRF / DNS-rebind defence. ``SameSite=Strict`` cookies
        # are keyed on *site*, so a hostname an attacker rebinds to the device
        # IP reads as same-site and the browser still attaches the auth cookie.
        # When a browser sends an ``Origin``/``Referer`` on a state-changing
        # request, require its host to be one of this device's own addresses;
        # a forged request from an attacker page carries that page's foreign
        # Origin → reject. An absent header (server-to-server / non-browser
        # client) is allowed – the rebind threat is browser-driven, where the
        # header is always present. Runs before the cookie check so a valid
        # cookie riding a rebind is still rejected.
        if request.method not in _SAFE_HTTP_METHODS:
            origin_host = _request_origin_host()
            if origin_host is not None and origin_host not in _allowed_request_hosts():
                abort(403, "Cross-origin request refused")

        if request.get_cookie(_AUTH_COOKIE, secret=pin) == "ok":
            return

        if path.startswith("/api/"):
            abort(401, "Authentication required")
        elif request.headers.get("HX-Request"):
            raise HTTPResponse(body="", headers={"HX-Redirect": "/login"})
        else:
            redirect("/login")

    @app.get("/login")
    def login_page() -> Any:
        cfg = _request_scoped_config()
        if not cfg.web_pin:
            redirect("/")
        return template(
            "login",
            error=False,
            stats=server.get_runtime_stats(),
            on_device=_is_on_device_request(),
            cancel_button=_cancel_button_label(cfg),
        )

    @app.post("/login")
    def login_submit() -> Any:
        cfg = load_config(server.config_path)
        pin = cfg.web_pin
        if not pin:
            redirect("/")

        remote = request.remote_addr or ""
        # Reserve a single guess atomically: refuse while locked out (short-
        # circuits any timing leak from compare_digest) AND serialize
        # concurrent submissions for this IP to one guess per window.
        _begin_or_abort(remote)

        entered = request.forms.get("pin", "")
        if hmac.compare_digest(entered, pin):
            login_throttle.record_success(remote)
            resp = HTTPResponse(status=303, headers={"Location": "/"})
            # ``SameSite=Strict`` blocks the browser from attaching this
            # cookie to any cross-site request, including top-level
            # navigations. That alone defeats most CSRF vectors against
            # the state-changing endpoints without needing a separate
            # token layer. OpenFollow is LAN-only so the reduced
            # cross-site ergonomics (e.g., following a link into the
            # admin UI from a different origin) don't matter.
            resp.set_cookie(
                _AUTH_COOKIE,
                "ok",
                secret=pin,
                httponly=True,
                path="/",
                samesite="strict",
            )
            raise resp

        login_throttle.record_failure(remote)
        return template(
            "login",
            error=True,
            stats=server.get_runtime_stats(),
            on_device=_is_on_device_request(),
            cancel_button=_cancel_button_label(cfg),
        )

    @app.post("/logout")
    def logout() -> Any:
        resp = HTTPResponse(status=303, headers={"Location": "/login"})
        resp.delete_cookie(_AUTH_COOKIE, path="/")
        raise resp

    # -- Helpers --------------------------------------------------------------

    def _render_general(
        cfg: AppConfig,
        *,
        saved: bool = False,
        restarting: bool = False,
        update_feedback: str = "",
    ) -> Any:
        """Render the General section with shared context."""
        return template(
            "partials/general",
            **_build_general_template_data(
                server,
                cfg,
                saved=saved,
                restarting=restarting,
                update_feedback=update_feedback,
            ),
        )

    def _render_video_source(
        cfg: AppConfig,
        *,
        saved: bool = False,
    ) -> Any:
        """Render the Video Source section."""
        data: dict[str, Any] = {
            "config": cfg,
            "saved": saved,
        }
        data.update(_build_input_template_data(cfg))
        return template("partials/video_source", **data)

    def _save_section_from_form(section: str, *, bool_fields: tuple[str, ...] = ()) -> AppConfig:
        form_data = dict(request.forms)
        for field_name in bool_fields:
            form_data[field_name] = field_name in request.forms
        with _config_write_lock:
            cfg = load_config(server.config_path)
            # In imperial mode, length/speed fields arrive as imperial
            # strings ("5 ft 6 in"). Rewrite to canonical metric before the
            # metric-only section parsers run, so storage stays metric.
            _normalize_unit_fields(
                section,
                form_data,
                UnitSystem(cfg.ui.unit_system),
            )
            apply_section_data(cfg, section, form_data)
            save_config(cfg, server.config_path)
        return cfg

    @app.get("/assets/<filename:path>")
    def get_asset(filename: str) -> Any:
        """Serve bundled static web assets.

        Assets referenced from ``base.tpl`` carry a ``?v=<build>`` cache-bust
        token, so a versioned request is safe to cache hard ("immutable") –
        a new build changes the URL. A request without the token (a direct
        hit or an unversioned reference) must revalidate so it can never
        pin a stale copy.
        """
        result = static_file(filename, root=str(_WEB_STATIC_DIR))
        # Only cache successful bodies; never pin a 404/403 (an HTTPError),
        # which would otherwise let a transient miss stick for a year.
        if isinstance(result, HTTPResponse) and result.status_code in (200, 206, 304):
            if request.query.get("v"):
                result.set_header("Cache-Control", "public, max-age=31536000, immutable")
            else:
                result.set_header("Cache-Control", "no-cache")
        return result

    @app.get("/help/<doc_id>.html")
    def get_help_doc(doc_id: str) -> Any:
        """Render a bundled help Markdown doc to an HTML fragment.

        ``doc_id`` maps 1:1 to ``openfollow/web/help/<doc_id>.md`` and is
        the ``data-help`` value the section "?" button carries. Guarded by
        a slug allow-list and a resolved-path containment check so a regex
        slip can't read a file outside the help directory.
        """
        if not _HELP_ID_RE.fullmatch(doc_id):
            abort(404)
        md_path = (_WEB_HELP_DIR / f"{doc_id}.md").resolve()
        if _WEB_HELP_DIR.resolve() not in md_path.parents or not md_path.is_file():
            abort(404)
        response.content_type = "text/html; charset=utf-8"
        return render_help_markdown(md_path.read_text(encoding="utf-8"))

    @app.get("/about")
    def about_page() -> Any:
        """About / license page (AGPLv3 §5(d) "Appropriate Legal Notices").

        Public (pre-auth), so it must expose no config state. ``config`` is
        deliberately NOT passed: ``base.tpl`` guards every ``config`` use with
        ``defined('config')``, so the page degrades cleanly and no config can
        leak as templates evolve. Only the derived cancel-button label is
        passed (for the on-device close-browser hint)."""
        cfg = _request_scoped_config()
        return template(
            "about",
            license_text=_read_license_text(),
            third_party_html=_third_party_notices_html(),
            written_offer_html=_written_offer_html(),
            on_device=_is_on_device_request(),
            cancel_button=_cancel_button_label(cfg),
        )

    @app.get("/about/license.txt")
    def about_license_txt() -> Any:
        """Serve the bundled AGPLv3 text as plain text (the License tab's
        "Open as plain text" link), or redirect to gnu.org when unbundled."""
        license_path = _license_file_path()
        if license_path is not None:
            return static_file(
                license_path.name,
                root=str(license_path.parent),
                mimetype="text/plain",
            )
        redirect("https://www.gnu.org/licenses/agpl-3.0.txt")

    # --- HTML Page Routes ---

    def _osc_dropdown_templates() -> tuple[
        list[_UserTemplateView],
        list[_UserTemplateView],
    ]:
        """Scan the OSC-output templates once and partition into
        ``(user, system)`` dropdown views.

        Both the initial page render and the section partial render need both
        lists; one scan does a single glob + JSON parse instead of two. The
        initial page load must feed the partial both lists, or the partial's
        ``defined()`` fallback collapses to empty until an HTMX refresh.
        """
        # ``list_templates_by_type`` already drops failed loads (entries whose
        # envelope didn't decode), so every entry here has a non-None template.
        entries = list_templates_by_type(
            _templates_root(server),
            "osc_output",
        )
        user: list[_UserTemplateView] = []
        system: list[_UserTemplateView] = []
        for entry in entries:
            tmpl = entry.template
            # ``list_templates_by_type`` drops failed loads, so ``tmpl`` is
            # always present here; the assert just narrows it for mypy.
            assert tmpl is not None  # pragma: no branch
            view = _UserTemplateView(
                id=tmpl.id,
                name=tmpl.name,
                address=tmpl.payload.get("address", ""),
                args=list(tmpl.payload.get("args", [])),
                # ``file:<filename>`` is the stable lookup key – see
                # the dataclass docstring for why id alone isn't safe.
                select_value=f"file:{entry.filename}",
            )
            (system if entry.is_system else user).append(view)
        return user, system

    @app.get("/")
    def index() -> Any:
        """Main configuration page."""
        config = _request_scoped_config()
        peers = server.get_peers()
        local = server.get_local_peer_info()
        # ``index.tpl`` includes the detection partial directly (no HTMX
        # kick), so the initial render must supply the same context as
        # ``/section/detection`` or the dropdowns collapse to "(unavailable)"
        # fallbacks even when extras are installed.
        extras = _get_detection_extras_status()
        # Diagnostics card data for the initial render (same shape the 5 s
        # poll returns), so the section reads filled-in values immediately.
        diag_cards, diag_warning = _build_diagnostics_cards(server, config)
        osc_user_templates, osc_system_templates = _osc_dropdown_templates()
        return template(
            "index",
            config=config,
            peers=peers,
            local=local,
            network_state=server.get_network_state(),
            stats=server.get_runtime_stats(),
            local_ips=_get_local_ips(),
            update_status=server.get_update_status(),
            # index.tpl includes the General partial directly, so the initial
            # render must supply the Software Update section's version label.
            current_version=openfollow.__version__,
            button_names=sorted(VALID_BUTTON_NAMES),
            detection_missing=_get_detection_missing_deps(config),
            detection_extras_installed=extras,
            detection_install=server.get_detection_install_status(),
            detection_available_models=_available_models(
                extras,
                config.detection.model,
                storage_path=config.detection.storage_path,
            ),
            detection_installed_models=_detection_installed_models(config.detection.storage_path),
            detection_storage_info=_detection_storage_info(config.detection.storage_path),
            osc_user_templates=osc_user_templates,
            osc_system_templates=osc_system_templates,
            # Marker chips / unresolved pills / status dots must render at
            # first paint, not only after a save – same context the section
            # render builds.
            **_osc_bindings_marker_context(config),
            # MIDI Devices table needs the live discovered-devices list so
            # it renders connected ports at first paint, not after the first
            # HTMX refresh.
            midi_discovered_devices=server.midi_discovered_devices(),
            # Per-controlled-marker fader snapshot so the MIDI page's
            # read-only Marker Faders strips render populated at first paint.
            marker_fader_values=server.marker_fader_values(),
            # Fader / device-alias names so the OSC bindings partial pre-fills
            # its dropdowns at first render.
            **_osc_binding_form_sources(config),
            # Per-controlled-marker faders for the unified Fader-on-Change
            # source dropdown (names from the live marker catalog, not config).
            marker_fader_names=_marker_fader_names_for_form(server),
            diagnostics_cards=diag_cards,
            diagnostics_log_warning=diag_warning,
            on_device=_is_on_device_request(),
            cancel_button=_cancel_button_label(config),
            psn_source_advisory=server.get_psn_source_advisory(),
            **_build_input_template_data(config),
        )

    @app.get("/section/overview")
    def get_overview() -> Any:
        """Get the server network overview partial.

        Overview is strictly read-only peer discovery; the network-interface
        block lives on the General tab.
        """
        peers = server.get_peers()
        local = server.get_local_peer_info()
        # Poll returns only the peer rows – the section shell is rendered once
        # at page load (partials/overview) and never re-swapped, so it can't
        # flicker. See partials/overview.tpl / overview_peers.tpl.
        return template(
            "partials/overview_peers",
            peers=peers,
            local=local,
        )

    @app.get("/section/statistics")
    def get_statistics() -> Any:
        """Get the live runtime statistics partial."""
        return template("partials/statistics", stats=server.get_runtime_stats())

    @app.get("/section/general/network_state")
    def get_general_network_state() -> Any:
        """Render JUST the read-only Network Interface block.

        Polled every 5s from inside the Network sub-section of the
        General tab so the surrounding form fields (Web PIN etc.) are
        not refreshed mid-typing – refreshing the full General section
        would clobber any half-entered PIN input.
        """
        return template(
            "partials/network_state",
            network_state=server.get_network_state(),
        )

    # ----------------------------------------------------------------------
    # Web write path for Pi network settings. Mirrors the on-screen flow
    # (``runtime/app_modes_network.py``): same ``validate_apply`` + adapter
    # ``apply_ipv4`` / ``renew_lease`` backend, surfaced as an editable form.
    # Gated by ``web_pin`` session auth like every other config section.
    # ----------------------------------------------------------------------

    _NETWORK_METHODS = ("dhcp", "dhcp_manual", "static")

    def _network_method_value(raw: str) -> str:
        return raw if raw in _NETWORK_METHODS else "dhcp"

    def _build_network_form_context(
        *,
        iface: str | None = None,
        method: str | None = None,
        overrides: dict[str, Any] | None = None,
        banner: dict[str, str] | None = None,
        editable: bool = False,
    ) -> dict[str, Any]:
        """Assemble the ``partials/network`` context from the adapter's raw
        snapshot. ``editable`` selects the view (disabled fields) vs. the edit
        form; ``method`` / ``overrides`` apply the operator's selection +
        submitted input (so a validation error keeps what they typed)."""
        cfg = server.get_network_config(iface)
        if not cfg or not cfg.get("interfaces"):
            return {
                "available": False,
                "writable": bool(cfg.get("writable")) if cfg else False,
                "editable": editable,
                "banner": banner,
            }
        net: dict[str, Any] = {
            "available": True,
            "editable": editable,
            "writable": cfg.get("writable", False),
            "backend": cfg.get("backend"),
            "interfaces": cfg.get("interfaces", []),
            "active_interface": cfg.get("active_interface", ""),
            "method": _network_method_value(cfg.get("method", "dhcp")),
            "address": cfg.get("address", ""),
            "prefix": cfg.get("prefix"),
            "subnet_mask": cfg.get("subnet_mask", ""),
            "router": cfg.get("router", ""),
            "dns": list(cfg.get("dns", [])),
            "lease_display": cfg.get("lease_display"),
            "banner": banner,
        }
        if method is not None:
            net["method"] = _network_method_value(method)
        if overrides:
            net.update(overrides)
        return net

    def _resolve_network_iface(iface: str) -> str | None:
        """Validate the posted interface against the adapter's live list,
        defaulting to the active interface. A privileged adapter write must
        never receive a raw/forged/empty POST value – the render path already
        sanitises the same way (``iface if iface in names else …``). Returns
        ``None`` when no interface is available (no adapter / read-only-empty)."""
        cfg = server.get_network_config(iface or None)
        if not cfg or not cfg.get("interfaces"):
            return None
        return cfg.get("active_interface") or None

    def _network_redirect_url(address: str) -> str:
        """Build a same-scheme/same-port URL on ``address`` so a static /
        manual-address apply can reload the UI at the new IP."""
        parts = request.urlparts
        return f"{parts.scheme}://{address}{_port_suffix(parts.netloc)}/"

    def _parse_network_form() -> tuple[str, str, dict[str, Any]]:
        """Pull (iface, method_value, fields) off the posted form. ``fields``
        carries the raw operator input – re-rendered verbatim on error."""
        forms = request.forms
        iface = (forms.get("iface") or "").strip()
        method = _network_method_value((forms.get("method") or "").strip())
        address = (forms.get("address") or "").strip()
        subnet_mask = (forms.get("subnet_mask") or "").strip()
        router = (forms.get("router") or "").strip()
        dns: list[str] = []
        for key in ("dns1", "dns2", "dns3"):
            value = (forms.get(key) or "").strip()
            if value:
                dns.append(value)
        return (
            iface,
            method,
            {
                "address": address,
                "subnet_mask": subnet_mask,
                "router": router,
                "dns": dns,
            },
        )

    @app.get("/section/network/status")
    def get_network_status() -> Any:
        """Read-only view (disabled fields + 'Switch to edit view'). The
        default; also where Cancel and a successful apply/renew return to."""
        return template(
            "partials/network",
            net=_build_network_form_context(editable=False),
        )

    @app.get("/section/network/edit")
    def get_network_edit() -> Any:
        """The editable form – reached from the view's 'Change' button."""
        return template(
            "partials/network",
            net=_build_network_form_context(editable=True),
        )

    @app.post("/section/network")
    def post_network_section() -> Any:
        """Re-render the edit form on interface / method change (no apply) so
        only the fields relevant to the chosen method are shown."""
        iface = (request.forms.get("iface") or "").strip() or None
        method = (request.forms.get("method") or "").strip() or None
        return template(
            "partials/network",
            net=_build_network_form_context(
                iface=iface,
                method=method,
                editable=True,
            ),
        )

    @app.post("/section/network/apply")
    def network_apply() -> Any:
        iface, method_value, fields = _parse_network_form()
        resolved = _resolve_network_iface(iface)
        if resolved is None:
            return template(
                "partials/network",
                net=_build_network_form_context(
                    editable=True,
                    banner={"kind": "error", "text": "Network interface not available on this host."},
                ),
            )
        iface = resolved
        method = Ipv4Method(method_value)
        address = fields["address"] or None
        # Router + prefix are operator-controlled (and validated) ONLY for a
        # static config. For dhcp_manual they come from the DHCP lease, and
        # validate_apply intentionally doesn't check them for that method – so
        # accepting them off the static path would let a forged POST inject an
        # out-of-subnet gateway / wrong prefix that nm_adapter honours over the
        # lease. Drop them unless the method is static. (The subnet field is a
        # dotted mask in the UI; parse_prefix takes a mask or bare CIDR → 0..32.)
        if method == Ipv4Method.STATIC:
            router = fields["router"] or None
            prefix = parse_prefix(fields["subnet_mask"])
        else:
            router = None
            prefix = None
        overrides = {
            "active_interface": iface,
            "method": method_value,
            "address": fields["address"],
            "subnet_mask": fields["subnet_mask"],
            "router": fields["router"],
            "dns": fields["dns"],
        }
        # A static config needs a valid mask; surface it in mask wording rather
        # than letting validate_apply report a missing/out-of-range "prefix".
        if method == Ipv4Method.STATIC and prefix is None:
            _mask_err = "Subnet must be a valid IPv4 netmask (255.255.255.0) or prefix length (e.g. /24)."
            return template(
                "partials/network",
                net=_build_network_form_context(
                    iface=iface,
                    overrides=overrides,
                    editable=True,
                    banner={"kind": "error", "text": _mask_err},
                ),
            )
        errors = validate_apply(method, address, prefix, router, fields["dns"])
        if errors:
            return template(
                "partials/network",
                net=_build_network_form_context(
                    iface=iface,
                    overrides=overrides,
                    editable=True,
                    banner={"kind": "error", "text": errors[0]},
                ),
            )
        config = Ipv4Config(
            method=method,
            address=address,
            prefix=prefix,
            router=router,
            dns=tuple(fields["dns"]),
        )
        result = server.apply_network(iface, config)
        if not result.ok:
            return template(
                "partials/network",
                net=_build_network_form_context(
                    iface=iface,
                    overrides=overrides,
                    editable=True,
                    banner={"kind": "error", "text": f"Apply failed: {result.message}"},
                ),
            )
        # An operator-set address (static / manual) is reachable at the new
        # IP, so reload the UI there – but only on a clean apply. On partial
        # failures, fall through to the banner view so the warnings aren't
        # lost behind the redirect's empty body. DHCP has no known address
        # and likewise falls through to the read-only view.
        if (
            method in (Ipv4Method.STATIC, Ipv4Method.DHCP_WITH_MANUAL_ADDRESS)
            and address
            and not result.partial_failures
        ):
            response.set_header("HX-Redirect", _network_redirect_url(address))
            return ""
        text = "Network settings applied."
        if result.partial_failures:
            text += " Warnings: " + "; ".join(result.partial_failures)
            # A static / manual apply changed the address but we're keeping the
            # operator on this (now possibly stale-IP) page to show the warning;
            # point them at the new address to reconnect.
            if method in (Ipv4Method.STATIC, Ipv4Method.DHCP_WITH_MANUAL_ADDRESS) and address:
                text += f" Reconnect at {address} if this page stops responding."
        return template(
            "partials/network",
            net=_build_network_form_context(
                iface=iface,
                editable=False,
                banner={"kind": "ok", "text": text},
            ),
        )

    @app.post("/section/network/renew")
    def network_renew() -> Any:
        iface = _resolve_network_iface((request.forms.get("iface") or "").strip())
        if iface is None:
            return template(
                "partials/network",
                net=_build_network_form_context(
                    editable=False,
                    banner={"kind": "error", "text": "Network interface not available on this host."},
                ),
            )
        result = server.renew_network(iface)
        if result.ok:
            text = "DHCP lease renewed."
            if result.partial_failures:
                text += " Warnings: " + "; ".join(result.partial_failures)
            banner = {"kind": "ok", "text": text}
        else:
            banner = {"kind": "error", "text": f"Renew failed: {result.message}"}
        return template(
            "partials/network",
            net=_build_network_form_context(
                iface=iface,
                editable=False,
                banner=banner,
            ),
        )

    # ----------------------------------------------------------------------
    # Diagnostics routes. Four endpoints, all PIN-gated through
    # ``_check_auth``. ``/section/diagnostics`` is the inline summary cards;
    # the three ``/api/diagnostics/...`` endpoints serve the on-demand
    # bundle / log tail / peer probe. Provider wiring lives in
    # ``_build_diagnostics_providers`` so the closure captures the
    # request-scoped config without coupling the server class to the
    # diagnostics import surface.
    # ----------------------------------------------------------------------

    @app.get("/section/diagnostics")
    def get_diagnostics() -> Any:
        cfg = _request_scoped_config()
        cards, warning = _build_diagnostics_cards(server, cfg)
        # Poll returns only the live cards – the section shell + bundle/log
        # tools are rendered once at page load (partials/diagnostics) and never
        # re-swapped, so they can't flicker. See partials/diagnostics_cards.tpl.
        return template(
            "partials/diagnostics_cards",
            cards=cards,
            log_unavailable_warning=warning,
        )

    @app.get("/api/diagnostics/bundle")
    def api_diagnostics_bundle() -> Any:
        """Build the full bundle, write a copy to disk, return as text
        download. Filename: ``openfollow-diagnostics-<system>-<ts>.txt``."""
        cfg = _request_scoped_config()
        # Size the operator's configured detection model store alongside
        # the SD card so the storage breakdown shows where models live
        # (NVMe vs. an SD-card fallback). Expand ``~`` first – the
        # detection runtime resolves storage_path with ``expanduser()``
        # and requires it absolute *after* expansion, so a ``~/...`` value
        # is valid and must be included. A still-relative value resolves
        # against the checkout, already covered by repo_root.
        from openfollow.video.detection import resolve_detection_storage_path

        extra_storage: list[Path] = []
        # Resolve the same effective path the runtime uses, so a blank field on
        # an NVMe unit reports the SSD store rather than mislabelling models as
        # living on the SD card.
        storage_path = resolve_detection_storage_path(cfg.detection.storage_path)
        expanded = Path(storage_path).expanduser()
        if expanded.is_absolute():
            extra_storage.append(expanded)
        bundle = diagnostics.collect_bundle(
            providers=_build_diagnostics_providers(server, cfg),
            log_ring=server.log_ring,
            update_service_name=cfg.update_service_name or None,
            repo_root=_repo_root_for_diagnostics(),
            extra_storage_paths=extra_storage or None,
        )
        text = diagnostics.format_bundle(bundle)
        # Best-effort on-disk copy. Failure (read-only fs, no perms)
        # logs but does not break the download – operators on locked-
        # down deployments still get the bundle through the browser.
        diagnostics.write_bundle_to_disk(
            text,
            system_name=server.system_name,
        )
        # Reuse the disk writer's filename helper so ``system_name`` lands
        # sanitised – an operator-configurable value can otherwise inject
        # double-quotes / newlines into the ``Content-Disposition`` header.
        fname = diagnostics.bundle_filename(
            server.system_name,
            datetime.now(timezone.utc),
        )
        response.content_type = "text/plain; charset=utf-8"
        response.headers["Content-Disposition"] = f'attachment; filename="{fname}"'
        return text

    @app.get("/api/diagnostics/log-tail")
    def api_diagnostics_log_tail() -> Any:
        """Return the last N log lines as plain text. ``n`` is parsed as an
        int and clamped to ``[1, 2000]`` (the upper cap matches the ring's
        default size)."""
        cfg = _request_scoped_config()
        raw_n = _as_str(request.query.get("n", "100"), "100")
        try:
            n = max(1, min(2000, int(raw_n)))
        except (TypeError, ValueError):
            n = 100
        src, lines = diagnostics.collect_log_tail(
            server.log_ring,
            update_service_name=cfg.update_service_name or None,
            last_n=n,
        )
        # Redact signatures from log content before serving – same
        # always-on stripping the bundle uses.
        redacted = [diagnostics.redact_signatures(ln) for ln in lines]
        body = f"[source: {src}]\n" + "\n".join(redacted)
        # XSS guard for the HTMX path: the partial swaps this into a
        # ``<pre>`` via ``hx-swap="innerHTML"``, so ``<…>`` in log content
        # would parse as HTML. Escape for HTMX; preserve raw text for direct
        # curl / wget so CLI consumers see the actual characters.
        if request.headers.get("HX-Request") == "true":
            response.content_type = "text/html; charset=utf-8"
            return html_mod.escape(body)
        response.content_type = "text/plain; charset=utf-8"
        return body

    @app.post("/api/diagnostics/test-peers")
    def api_diagnostics_test_peers() -> Any:
        """HTTP-HEAD probe to every known peer's advertised port.
        Refuses non-private IPs (``_is_private_peer_ip`` allowlist)
        – same SSRF gate the peer-broadcast helpers use."""
        results: list[dict[str, Any]] = []
        # A legit peer serves on the port this device serves on (homogeneous
        # fleet); the beacon-advertised port is otherwise untrusted.
        expected_port = server.display_port
        for peer in server.get_peers():
            if not _is_private_peer_ip(peer.ip):
                results.append(
                    {
                        "name": peer.name,
                        "ip": peer.ip,
                        "port": peer.web_port,
                        "ok": False,
                        "status": 0,
                        "ms": 0,
                        "error": "non-private IP refused",
                    }
                )
                continue
            results.append(_probe_peer(peer.ip, peer.web_port, peer.name, expected_port=expected_port))
        # Compact HTML fragment for the results div. Every interpolated field
        # is HTML-escaped: ``peer.name`` comes from beacon broadcasts and
        # ``error`` carries probe exception text – neither is HTML-safe.
        # Numeric fields escape too, guarding future shape changes.
        esc = html_mod.escape
        rows: list[str] = [
            "<table class='grid-table'><thead><tr><th>Peer</th><th>Address</th><th>Result</th></tr></thead><tbody>"
        ]
        for r in results:
            if r["ok"]:
                # Format ``ms`` then escape. Today ``ms`` is a float so escape
                # is a no-op, but a future string shape ("12.4ms (timeout)")
                # would otherwise sneak HTML through unfiltered.
                ms_text = esc(f"{r['ms']:.0f}")
                cell = f"<span class='stat-chip ok'>{esc(str(r['status']))} OK in {ms_text} ms</span>"
            else:
                # ``_probe_peer`` returns ``ok=False`` for any non-2xx,
                # including 5xx with an empty ``error``. Prefer the status
                # code when non-zero so a reachable peer returning 503 isn't
                # mislabelled "unreachable".
                if r.get("error"):
                    detail = str(r["error"])
                elif r.get("status"):
                    detail = f"HTTP {r['status']}"
                else:
                    detail = "unreachable"
                cell = f"<span class='stat-chip off'>{esc(detail)}</span>"
            rows.append(
                f"<tr><td>{esc(str(r['name']))}</td>"
                f"<td>{esc(str(r['ip']))}:{esc(str(r['port']))}</td>"
                f"<td>{cell}</td></tr>"
            )
        if not results:
            rows.append(
                "<tr><td colspan='3'>"
                "<span class='field-note'>No peers known yet – "
                "wait for discovery or check beacon health above.</span>"
                "</td></tr>"
            )
        rows.append("</tbody></table>")
        return "".join(rows)

    @app.get("/section/<name>")
    def get_section(name: str) -> Any:
        """Get a single section partial."""
        if name not in VALID_SECTIONS:
            abort(404, "Unknown section")
        config = _request_scoped_config()
        extra: dict[str, Any] = {}
        if name == "general":
            # Delegate to ``_render_general`` so network_state,
            # update_status and local_ips are populated consistently
            # with the form-POST render path.
            return _render_general(config)
        elif name == "psn":
            extra["local_ips"] = _get_local_ips()
            extra["psn_source_advisory"] = server.get_psn_source_advisory()
        elif name == "video_source":
            extra.update(_build_input_template_data(config))
        elif name in ("controller", "gamepad"):
            extra["button_names"] = sorted(VALID_BUTTON_NAMES)
            extra["detection_started"] = server.is_button_detection_active()
        # ``detection`` is intentionally NOT handled here – it has a
        # dedicated ``@app.get("/section/detection")`` route that
        # builds the extras-status / install-job state needed by the
        # template. Bottle dispatches the explicit route before this
        # wildcard, so a ``name == "detection"`` request never reaches
        # this code path.
        template_name = "partials/gamepad" if name == "controller" else f"partials/{name}"
        return template(template_name, config=config, **extra)

    @app.post("/section/video_source")
    def update_video_source() -> Any:
        """Update video source settings."""
        form_data = dict(request.forms)
        with _config_write_lock:
            cfg = load_config(server.config_path)
            apply_section_data(cfg, "video_source", form_data)
            save_config(cfg, server.config_path)

        if request.query.get("restart") == "1":
            server.request_restart()
            return _render_general(
                cfg,
                saved=True,
                restarting=True,
            )

        return _render_video_source(cfg, saved=True)

    @app.post("/section/general")
    def update_general() -> Any:
        """Update general settings."""
        form_data = dict(request.forms)
        with _config_write_lock:
            cfg = load_config(server.config_path)
            apply_section_data(cfg, "general", form_data)
            save_config(cfg, server.config_path)

        if request.query.get("restart") == "1":
            update_state = server.get_update_status().get("state", "")
            if update_state in {"queued", "running", "restarting"}:
                return _render_general(
                    cfg,
                    saved=True,
                    update_feedback="Update is currently running. Restart is blocked until it finishes.",
                )
            server.request_restart()
            return _render_general(cfg, saved=True, restarting=True)

        return _render_general(cfg, saved=True)

    def _persist_ui_change(mutate: Callable[[AppConfig], None]) -> AppConfig:
        """Load, mutate, coerce, and save the config under the write lock,
        returning the saved config. Loads fresh inside the lock; ``mutate``
        sets the [ui] fields the route owns; ``UiConfig.__post_init__``
        coerces raw form values."""
        with _config_write_lock:
            cfg = load_config(server.config_path)
            mutate(cfg)
            cfg.ui.__post_init__()  # coerce raw [ui] form values
            save_config(cfg, server.config_path)
        return cfg

    @app.post("/settings/unit-system")
    def update_unit_system() -> Any:
        """Flip the installation-wide display unit system.

        Persists ``[ui] unit_system`` and re-renders the General section. The
        model stays metric; only display + form-input parsing change. Other
        sections pick it up on their next render. An unrecognised value is
        coerced to ``"metric"`` by ``UiConfig.__post_init__``."""
        choice = _as_str(request.forms.get("unit_system", ""), "").strip().lower()
        cfg = _persist_ui_change(lambda c: setattr(c.ui, "unit_system", choice))
        return _render_general(cfg, saved=True)

    @app.post("/settings/experimental")
    def update_experimental() -> Any:
        """Persist ``[ui] show_experimental_features``. Turning it off also
        disables ``detection.enabled`` (still experimental); the config-reload
        watcher applies that at runtime. The body-class show/hide flip is done
        client-side. Responds empty (hx-swap="none")."""
        show = _as_bool(request.forms.get("show_experimental_features"), False)

        def _mutate(cfg: AppConfig) -> None:
            cfg.ui.show_experimental_features = show
            if not show:
                cfg.detection.enabled = False

        _persist_ui_change(_mutate)
        return ""

    @app.post("/section/general/deb-update")
    def deb_update_general() -> Any:
        """Check GitHub Releases for a newer .deb and install it.

        Returns JSON so the General-tab script knows whether the install was
        actually queued – a rejected request (another update in flight) must
        not leave the operator polling a locked progress modal.
        """
        cfg = load_config(server.config_path)
        response.content_type = "application/json"
        service_name = cfg.update_service_name
        if not _is_valid_service_name(service_name):
            service_name = DEFAULT_UPDATE_SERVICE_NAME
        if not server.request_deb_update(service_name=service_name):
            return json.dumps({"ok": False, "error": "An update is already running. Please wait for it to finish."})
        return json.dumps({"ok": True})

    @app.get("/section/general/deb-update/check")
    def deb_update_check() -> Any:
        """Check GitHub for a newer release (synchronous, read-only).

        Returns JSON the General-tab modal uses to decide whether to
        offer the install. Does NOT download or install – that's the
        POST route above, triggered only after the operator confirms.
        """
        from openfollow.runtime.deb_update import check_for_update

        cfg = load_config(server.config_path)
        response.content_type = "application/json"
        try:
            info = check_for_update(cfg.update_github_repo, openfollow.__version__)
            return json.dumps({"ok": True, **info})
        except Exception as exc:  # surface any network/API error as feedback
            return json.dumps({"ok": False, "error": str(exc)})

    @app.post("/section/general/deb-upload")
    def deb_upload_general() -> Any:
        """Install an operator-uploaded update bundle (no internet).

        Streams the ``.ofupdate`` bundle to ``/tmp/openfollow-update-*`` so the
        existing privilege-broker sudoers rule applies, verifies its signature +
        checksum and that the inner package is an ``openfollow`` build for this
        architecture, then queues the detached installer. No GitHub version gate
        – the operator chose the file, so downgrades are allowed. The worker
        re-verifies before installing.
        """
        from openfollow.privilege.capabilities import DEB_UPDATE_TMP_PREFIX
        from openfollow.runtime.deb_update import (
            _deb_arch,
            _is_newer,
            validate_uploaded_deb,
            verify_and_extract_bundle,
        )

        response.content_type = "application/json"

        # The file is the raw request body (not multipart, to dodge Bottle's
        # multipart size limit); the filename rides along as a query param.
        filename = (request.query.get("filename") or "").strip()
        if not filename:
            return json.dumps({"ok": False, "error": "No file selected."})
        if not filename.lower().endswith(".ofupdate"):
            return json.dumps({"ok": False, "error": "Unsupported file type. Upload an .ofupdate release bundle."})

        total = request.content_length or 0
        if total <= 0:
            return json.dumps({"ok": False, "error": "Empty upload."})
        if total > _MAX_UPLOAD_BYTES:
            return json.dumps({"ok": False, "error": f"File too large (max {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB)."})

        cfg = load_config(server.config_path)
        service_name = cfg.update_service_name
        if not _is_valid_service_name(service_name):
            service_name = DEFAULT_UPDATE_SERVICE_NAME

        staged_path = f"/tmp/{DEB_UPDATE_TMP_PREFIX}{secrets.token_hex(8)}.ofupdate"  # nosec B108
        try:
            _stream_to_file(request.environ["wsgi.input"], staged_path, total)
            deb_path = verify_and_extract_bundle(staged_path, staged_path + ".d")
            fields = validate_uploaded_deb(deb_path, _deb_arch())
            if not server.request_local_update(service_name, deb_path=staged_path):
                _discard_staged(staged_path)
                return json.dumps({"ok": False, "error": "An update is already running. Please wait for it to finish."})
            version = fields.get("Version", "?")
            # The control Version is the Debian form (e.g. ``0.2.5~rc8``); drop
            # the pre-release tilde so packaging.Version can parse it for the
            # upgrade/downgrade comparison.
            return json.dumps(
                {
                    "ok": True,
                    "version": version,
                    "current": openfollow.__version__,
                    "downgrade": not _is_newer(version.replace("~", ""), openfollow.__version__),
                }
            )
        except Exception as exc:  # validation / stream failure
            _discard_staged(staged_path)
            return json.dumps({"ok": False, "error": str(exc)})

    @app.post("/section/psn")
    def update_psn() -> Any:
        """Update PSN output settings."""
        form_data = dict(request.forms)
        with _config_write_lock:
            cfg = load_config(server.config_path)
            apply_section_data(cfg, "psn", form_data)
            save_config(cfg, server.config_path)

        if request.query.get("restart") == "1":
            server.request_restart()

        return template(
            "partials/psn",
            config=cfg,
            saved=True,
            local_ips=_get_local_ips(),
            psn_source_advisory=server.get_psn_source_advisory(),
        )

    @app.post("/section/camera")
    def update_camera() -> Any:
        """Update camera settings."""
        cfg = _save_section_from_form("camera")
        return template("partials/camera", config=cfg, saved=True)

    @app.post("/section/grid")
    def update_grid() -> Any:
        """Update grid settings."""
        cfg = _save_section_from_form("grid", bool_fields=("visible", "origin_visible"))
        return template("partials/grid", config=cfg, saved=True)

    @app.post("/section/movement")
    def update_movement() -> Any:
        """Update movement speed settings."""
        cfg = _save_section_from_form("movement")
        return template("partials/movement", config=cfg, saved=True)

    @app.post("/section/marker")
    def update_marker() -> Any:
        """Update marker visual settings."""
        cfg = _save_section_from_form(
            "marker",
            bool_fields=(
                "ball_visible",
                "crosshair_visible",
                "drop_line",
                "ground_circle",
                "ground_circle_filled",
                "z_display_from_stage",
            ),
        )
        return template("partials/marker", config=cfg, saved=True)

    @app.post("/section/gamepad")
    def update_gamepad() -> Any:
        """Update gamepad (controller) settings."""
        cfg = _save_section_from_form(
            "gamepad",
            bool_fields=("enabled", "invert_y", "swap_triggers"),
        )
        return template(
            "partials/gamepad",
            config=cfg,
            saved=True,
            button_names=sorted(VALID_BUTTON_NAMES),
        )

    @app.post("/section/keyboard")
    def update_keyboard() -> Any:
        """Update keyboard settings."""
        cfg = _save_section_from_form(
            "keyboard",
            bool_fields=("keyboard_enabled",),
        )
        return template("partials/keyboard", config=cfg, saved=True)

    @app.post("/section/mouse")
    def update_mouse() -> Any:
        """Update mouse settings."""
        cfg = _save_section_from_form("mouse", bool_fields=_mouse_bool_fields())
        return template("partials/mouse", config=cfg, saved=True)

    @app.post("/section/mouse3d")
    def update_mouse3d() -> Any:
        """Update 3D Mouse settings."""
        bool_fields = ("enabled", *(f"invert_{axis}" for axis in MOUSE3D_AXES))
        cfg = _save_section_from_form("mouse3d", bool_fields=bool_fields)
        return template("partials/mouse3d", config=cfg, saved=True)

    @app.get("/section/mouse3d/detect")
    def detect_mouse3d_button() -> Any:
        """Watch for a 3D Mouse button press, for the inline Detect bind widget.

        The operator clicks a field's Detect button, then presses the device
        button to bind; the handler polls briefly (working whether or not the
        feature is enabled). Returns JSON ``{"button": <int|null>}`` which the
        ``detect-input.js`` widget writes into the field.
        """
        response.content_type = "application/json"
        return json.dumps({"button": server.latest_mouse3d_button()})

    @app.post("/section/gamepad/detect-buttons")
    def start_button_detection() -> Any:
        """Trigger the in-app button detection wizard."""
        server.request_button_detection()
        cfg = load_config(server.config_path)
        return template(
            "partials/gamepad",
            config=cfg,
            saved=False,
            button_names=sorted(VALID_BUTTON_NAMES),
            detection_started=True,
        )

    @app.post("/section/gamepad/cancel-button-detection")
    def cancel_button_detection() -> Any:
        """Cancel an in-progress button detection wizard.

        Queues the cancel for the main loop, which calls
        ``exit_button_detection``. The cancel is async (drained on the next
        main-loop tick), so ``detection_started`` may still read "running"
        for one poll until the flag clears."""
        server.cancel_button_detection()
        cfg = load_config(server.config_path)
        return template(
            "partials/gamepad",
            config=cfg,
            saved=False,
            button_names=sorted(VALID_BUTTON_NAMES),
            detection_started=server.is_button_detection_active(),
        )

    @app.post("/section/osc")
    def update_osc() -> Any:
        """Update OSC settings."""
        cfg = _save_section_from_form("osc", bool_fields=("enabled",))
        return template("partials/osc", config=cfg, saved=True)

    @app.post("/section/operator_messages")
    def update_operator_messages() -> Any:
        """Update the OSC operator-message overlay settings."""
        cfg = _save_section_from_form("operator_messages", bool_fields=("enabled", "route_by_marker"))
        return template("partials/operator_messages", config=cfg, saved=True)

    # ------------------------------------------------------------------
    # MIDI page (Devices / Virtual Faders) inside the Input tab. Each
    # sub-section is its own form so saves are independent; the helper
    # below renders the whole page partial with both forms populated from
    # the current config + live substrate state.
    # ------------------------------------------------------------------

    def _render_midi_section(
        cfg: AppConfig,
        *,
        saved: bool = False,
        selected_fader: int = 1,
    ) -> Any:
        """Render the MIDI page partial. Hoisted so save handlers can
        return the same shape as the initial-page render – the
        Devices table needs the live ``discover()`` result to label
        which configured aliases match a connected port.

        ``selected_fader`` controls which strip is pre-selected on render.
        The per-fader save handler passes the just-saved fader so a save
        doesn't snap selection back to fader 1.
        """
        from openfollow.configuration import VALID_FADER_MIDI_TYPES

        return template(
            "partials/midi",
            config=cfg,
            saved=saved,
            selected_fader=selected_fader,
            discovered_devices=server.midi_discovered_devices(),
            midi_patches=_midi_patches_for_form(cfg),
            valid_fader_midi_types=VALID_FADER_MIDI_TYPES,
            marker_fader_values=server.marker_fader_values(),
        )

    @app.get("/section/midi")
    def get_midi_section() -> Any:
        """Render the full MIDI page partial. Used by the initial
        page load (via ``index.tpl``) and by direct HTMX refreshes."""
        cfg = load_config(server.config_path)
        return _render_midi_section(cfg)

    def _midi_patch_device_fields(identifier: str) -> dict[str, str]:
        """Resolve a chosen device ``identifier`` (from the patch row's
        Device dropdown) to its port_name / product / serial. Empty /
        unknown identifier → an unassigned patch (no device)."""
        if identifier:
            for dev in server.midi_discovered_devices():
                if dev.get("identifier") == identifier:
                    return {
                        "port_name": _as_str(dev.get("port_name"), ""),
                        "product": _as_str(dev.get("product"), ""),
                        "serial": _as_str(dev.get("serial"), ""),
                    }
        return {"port_name": "", "product": "", "serial": ""}

    @app.post("/section/midi/patches/add")
    def add_midi_patch() -> Any:
        """Append a fresh, device-less MIDI patch with the next free id.
        The operator then assigns an alias + device on its row."""
        from openfollow.configuration import MidiPatch

        with _config_write_lock:
            cfg = load_config(server.config_path)
            cfg.midi.patches.append(MidiPatch(id=cfg.midi.next_patch_id()))
            save_config(cfg, server.config_path)
        return _render_midi_section(cfg, saved=True)

    @app.post("/section/midi/patches/<patch_id:int>")
    def save_midi_patch(patch_id: int) -> Any:
        """Save one patch's alias + device assignment. The substrate's
        ``apply_config`` opens / closes the listener port on the next
        hot-reload tick. No-op when the id is unknown (concurrent delete)."""
        with _config_write_lock:
            cfg = load_config(server.config_path)
            patch = cfg.midi.patch_by_id(patch_id)
            if patch is not None:
                patch.alias = _as_str(request.forms.get("alias", ""), "").strip()
                chosen = _as_str(request.forms.get("device", ""), "")
                # Preserve a binding whose device is currently disconnected:
                # its dropdown option re-submits the patch's own identifier,
                # which won't be among the connected devices. Only re-resolve
                # (or clear) when the operator picked a *different* option.
                if chosen and chosen == patch.identifier:
                    pass
                else:
                    dev = _midi_patch_device_fields(chosen)
                    patch.port_name = dev["port_name"]
                    patch.product = dev["product"]
                    patch.serial = dev["serial"]
                cfg.midi.__post_init__()
                save_config(cfg, server.config_path)
        return _render_midi_section(cfg, saved=True)

    @app.post("/section/midi/patches/<patch_id:int>/delete")
    def delete_midi_patch(patch_id: int) -> Any:
        """Remove a patch by id. Bindings/faders that referenced it fall
        back to their "(missing)" dropdown option until re-pointed."""
        with _config_write_lock:
            cfg = load_config(server.config_path)
            cfg.midi.patches = [p for p in cfg.midi.patches if p.id != patch_id]
            save_config(cfg, server.config_path)
        return _render_midi_section(cfg, saved=True)

    @app.post("/section/midi/faders")
    def save_midi_faders() -> Any:
        """Save virtual fader configurations. The form posts eight
        parallel-array rows in fader order; the loop builds a fresh
        :class:`VirtualFaderConfig` per row and replaces the list.

        All eight faders are uniform (MIDI / unmapped) – the former
        "fader 1 = gamepad" allocation rule is gone (the gamepad drives
        per-marker faders, not a fixed index). ``source_kind`` is coerced
        to a valid choice by ``VirtualFaderConfig.__post_init__``, so a
        crafted POST can't persist an out-of-band kind.
        """
        from openfollow.configuration import (
            VIRTUAL_FADER_COUNT,
            VirtualFaderConfig,
            VirtualFadersConfig,
        )

        names = request.forms.getall("fader_name")
        defaults = request.forms.getall("fader_default")
        kinds = request.forms.getall("fader_source_kind")
        patches = request.forms.getall("fader_source_patch")
        midi_types = request.forms.getall("fader_source_midi_type")
        midi_channels = request.forms.getall("fader_source_midi_channel")
        midi_numbers = request.forms.getall("fader_source_midi_number")
        # Operator-assigned strip colour (parallel array, same as the
        # per-fader route). Threaded through so a batch save doesn't reset
        # every fader's colour to the default; ``__post_init__`` coerces a
        # bad hex back to the default so a crafted POST can't persist garbage.
        colors = request.forms.getall("fader_color")
        # Show flag is a checkbox group – the form field is named
        # ``fader_show`` with the fader's index as the value, so
        # ``getall`` returns only the indices the operator ticked.
        shown_indices = {_as_int(v, 0) for v in request.forms.getall("fader_show")}
        new_faders: list[VirtualFaderConfig] = []
        for i in range(VIRTUAL_FADER_COUNT):
            # Defensive: pad missing array entries with empty
            # strings so a hand-edited POST (some rows missing) still
            # produces a well-formed list. The dataclass
            # ``__post_init__`` clamps every field.
            posted_kind = _as_str(kinds[i], "") if i < len(kinds) else ""
            new_faders.append(
                VirtualFaderConfig(
                    name=names[i] if i < len(names) else "",
                    default_value=_as_float(
                        defaults[i] if i < len(defaults) else "0",
                        0.0,
                    ),
                    show_on_display=(i + 1) in shown_indices,
                    source_kind=posted_kind,
                    source_patch=(_as_int(patches[i], 0) if i < len(patches) else 0),
                    source_midi_type=(
                        _as_str(midi_types[i], "control_change") if i < len(midi_types) else "control_change"
                    ),
                    source_midi_channel=_as_int(
                        midi_channels[i] if i < len(midi_channels) else "0",
                        0,
                    ),
                    source_midi_number=_as_int(
                        midi_numbers[i] if i < len(midi_numbers) else "0",
                        0,
                    ),
                    color=_as_str(colors[i], "#000000") if i < len(colors) else "#000000",
                )
            )
        with _config_write_lock:
            cfg = load_config(server.config_path)
            cfg.virtual_faders = VirtualFadersConfig(faders=new_faders)
            save_config(cfg, server.config_path)
        return _render_midi_section(cfg, saved=True)

    @app.get("/section/midi/faders/<idx:int>/detail")
    def get_midi_fader_detail(idx: int) -> Any:
        """Render the editable detail panel for one virtual fader.

        Swapped into the detail slot when the operator clicks a strip.
        Index out of range falls back to fader 1 so a stale link can't
        404 the page."""
        from openfollow.configuration import (
            VALID_FADER_MIDI_TYPES,
            VIRTUAL_FADER_COUNT,
        )

        if not 1 <= idx <= VIRTUAL_FADER_COUNT:
            idx = 1
        cfg = load_config(server.config_path)
        fader = cfg.virtual_faders.faders[idx - 1]
        return template(
            "partials/midi_fader_detail",
            config=cfg,
            fader_index=idx,
            fader=fader,
            midi_patches=_midi_patches_for_form(cfg),
            valid_fader_midi_types=VALID_FADER_MIDI_TYPES,
        )

    @app.post("/section/midi/faders/<idx:int>")
    def save_midi_fader_one(idx: int) -> Any:
        """Save a single fader. Other faders are untouched – this is
        the per-fader analogue of the legacy
        ``POST /section/midi/faders`` batch endpoint that the
        old wide-table UI used. The mixer-style UI saves one fader
        at a time so the round-trip is small even with all eight
        configured."""
        from openfollow.configuration import (
            VIRTUAL_FADER_COUNT,
            VirtualFaderConfig,
            VirtualFadersConfig,
        )

        if not 1 <= idx <= VIRTUAL_FADER_COUNT:
            return _render_midi_section(
                load_config(server.config_path),
                saved=False,
            )
        # All faders are uniform (MIDI / unmapped); ``source_kind`` is
        # coerced to a valid choice by ``VirtualFaderConfig.__post_init__``.
        posted_kind = _as_str(request.forms.get("source_kind", ""), "")
        with _config_write_lock:
            cfg = load_config(server.config_path)
            existing = list(cfg.virtual_faders.faders)
            existing[idx - 1] = VirtualFaderConfig(
                name=_as_str(request.forms.get("name", ""), ""),
                default_value=_as_float(
                    request.forms.get("default_value", "0"),
                    0.0,
                ),
                show_on_display=("show_on_display" in request.forms),
                source_kind=posted_kind,
                source_patch=_as_int(
                    request.forms.get("source_patch", "0"),
                    0,
                ),
                source_midi_type=_as_str(
                    request.forms.get("source_midi_type", "control_change"),
                    "control_change",
                ),
                source_midi_channel=_as_int(
                    request.forms.get("source_midi_channel", "0"),
                    0,
                ),
                source_midi_number=_as_int(
                    request.forms.get("source_midi_number", "0"),
                    0,
                ),
                # Operator-assigned strip colour; ``__post_init__`` coerces
                # a bad hex back to the default so a crafted POST can't
                # persist garbage.
                color=_as_str(request.forms.get("color", "#000000"), "#000000"),
            )
            cfg.virtual_faders = VirtualFadersConfig(faders=existing)
            save_config(cfg, server.config_path)
        # Pass the just-saved fader so the strip stays selected after the
        # section re-renders (the partial otherwise defaults to fader 1).
        return _render_midi_section(cfg, saved=True, selected_fader=idx)

    def _fader_learn_row_id(idx: int) -> str:
        """Capture-broker row id for a fader's Learn flow. Namespaced
        ``fader:<idx>`` so it shares the single-slot broker with the OSC
        trigger Capture flow without colliding with an OSC row's UUID –
        arming one cancels the other, same single-slot contract."""
        return f"fader:{idx}"

    @app.post("/section/midi/faders/<idx:int>/learn/arm")
    def arm_midi_fader_learn(idx: int) -> Any:
        """Arm a MIDI Learn capture for fader ``idx``.

        Learn applies to 1..VIRTUAL_FADER_COUNT; an out-of-range index 404s
        rather than silently arming an unusable capture."""
        from openfollow.configuration import VIRTUAL_FADER_COUNT

        if not 1 <= idx <= VIRTUAL_FADER_COUNT:
            abort(404, "No MIDI Learn for this fader")
            return ""  # pragma: no cover - abort() raises first
        poll = server.midi_capture_arm(_fader_learn_row_id(idx))
        # Reuse the per-request parse the ``before_request`` hook already
        # stashed rather than re-reading config.toml – this arms off the
        # 250 ms poll loop, so a second disk read per request adds up.
        return _render_midi_fader_capture_status(
            _request_scoped_config(),
            idx,
            poll,
        )

    @app.get("/section/midi/faders/<idx:int>/learn/poll")
    def poll_midi_fader_learn(idx: int) -> Any:
        """Poll the Fader Learn capture state for fader ``idx``. The
        arm response kicked off the 250 ms poll loop; on ``captured``
        the partial emits OOB fragments that fill the fader's source
        fields and the loop stops (terminal state omits the driver)."""
        from openfollow.configuration import VIRTUAL_FADER_COUNT

        if not 1 <= idx <= VIRTUAL_FADER_COUNT:
            abort(404, "No MIDI Learn for this fader")
            return ""  # pragma: no cover - abort() raises first
        poll = server.midi_capture_poll(_fader_learn_row_id(idx))
        # Fires every 250 ms while listening – read the cached per-request
        # config instead of hitting disk on each poll.
        return _render_midi_fader_capture_status(
            _request_scoped_config(),
            idx,
            poll,
        )

    @app.get("/section/midi/faders/values")
    def get_midi_fader_values() -> Any:
        """Live-value poll endpoint. Returns a flat list of
        ``hx-swap-oob`` ``<span>`` snippets – HTMX replaces each
        per-row live cell from one round-trip. ``midi_fader_values``
        is empty when no substrate is wired (test contexts), in
        which case the response is an empty body and the browser's
        existing values stay unchanged."""
        return template(
            "partials/midi_fader_values",
            fader_values=server.midi_fader_values(),
        )

    @app.get("/section/midi/marker-faders/values")
    def get_marker_fader_values() -> Any:
        """Live-value poll endpoint for the read-only Marker Faders viz.
        Returns a flat list of ``hx-swap-oob`` snippets (one per
        controlled marker) so HTMX refreshes each gamepad-driven strip's
        bar + value from one round-trip. ``marker_fader_values`` is empty
        when no substrate is wired (test contexts) → empty body, leaving
        the browser's existing values unchanged."""
        return template(
            "partials/midi_marker_fader_values",
            marker_fader_values=server.marker_fader_values(),
        )

    @app.post("/section/detection/models")
    def update_detection_models() -> Any:
        """Save the Models & Dependencies box (active model + storage path)."""
        cfg = _save_section_from_form("detection")
        return _render_detection(cfg, saved_section="models")

    @app.post("/section/detection/inference")
    def update_detection_inference() -> Any:
        """Save the Detection & Display box (inference + overlay settings)."""
        cfg = _save_section_from_form(
            "detection",
            bool_fields=("enabled", "preprocess_clahe", "show_boxes", "show_labels"),
        )
        return _render_detection(cfg, saved_section="inference")

    @app.post("/section/detection/tracking")
    def update_detection_tracking() -> Any:
        """Save the Tracking box (mode, pin target, smoothing, assist)."""
        cfg = _save_section_from_form("detection", bool_fields=("pin_marker",))
        return _render_detection(cfg, saved_section="tracking")

    @app.get("/section/detection")
    def get_detection_partial() -> Any:
        """Re-render the detection partial.

        Used by the HTMX polling loop while a background install or
        uninstall is in progress (detection_install.state ==
        'running') and by any client-side flow that wants to refresh
        the section without saving.

        Auto-clears the install-status slot after rendering a
        terminal (success / error) state – the polling div has done
        its job once the operator sees the final banner, and we
        don't want it lingering on the next re-render.
        """
        cfg = _request_scoped_config()
        return _render_detection(cfg, dismiss_terminal_install_status=True)

    def _render_detection(
        cfg: AppConfig,
        *,
        saved_section: str = "",
        install_feedback: str = "",
        install_error: bool = False,
        dismiss_terminal_install_status: bool = False,
    ) -> Any:
        # Snapshot the install-job status under the queue lock.
        # ``dismiss_terminal_install_status`` is set ONLY by the
        # polling endpoint – the kickoff handlers ``_kickoff_install``
        # / form save must not auto-clear because the worker may have
        # raced to terminal between the kickoff's
        # ``try_claim_detection_install`` and this snapshot, and
        # clearing would wipe the success/error banner before the
        # polling div ever shows it.
        install_status = server.get_detection_install_status()
        if dismiss_terminal_install_status and install_status.get("state") in {"success", "error"}:
            server.set_detection_install_status(
                state="idle",
                message="",
                tail="",
            )
        extras = _get_detection_extras_status()
        return template(
            "partials/detection",
            config=cfg,
            saved_section=saved_section,
            detection_missing=_get_detection_missing_deps(cfg),
            detection_extras_installed=extras,
            detection_install=install_status,
            detection_available_models=_available_models(
                extras, cfg.detection.model, storage_path=cfg.detection.storage_path
            ),
            detection_installed_models=_detection_installed_models(cfg.detection.storage_path),
            detection_storage_info=_detection_storage_info(cfg.detection.storage_path),
            install_feedback=install_feedback,
            install_error=install_error,
        )

    def _run_detection_export_job(*, model: str, argv: list[str], timeout: int) -> None:
        """Worker body for the background YOLO→ONNX export.

        Runs ``argv`` via ``_run_package_command``, captures its tail, and
        publishes a terminal ``state="success"`` / ``"error"`` to the status
        slot the polling UI reads. Every code path leaves a renderable state so
        an exception can't strand the slot in ``running`` and block the lock.
        """
        try:
            rc, tail_text = _run_package_command(argv, timeout=timeout)
        except subprocess.TimeoutExpired:
            server.set_detection_install_status(
                state="error",
                message=f"Exporting `{model}` timed out. Retry from a terminal to see progress.",
            )
            return
        except OSError as exc:
            server.set_detection_install_status(state="error", message=f"Failed to launch the export: {exc}")
            return
        except Exception:
            logger.error("Unexpected error while exporting detection model %s", model, exc_info=True)
            server.set_detection_install_status(
                state="error",
                message=f"Exporting `{model}` failed due to an unexpected error. Check logs for details.",
            )
            return
        if rc != 0:
            tail = tail_text.strip().splitlines()[-_INSTALL_TAIL_VISIBLE_LINES:]
            detail = " / ".join(tail) if tail else "no output"
            logger.warning("model export %s failed (rc=%s): %s", model, rc, detail)
            server.set_detection_install_status(
                state="error",
                message=f"Exporting `{model}` failed (exit {rc}).",
                tail=tail_text,
            )
            return
        logger.info("Detection model export %s succeeded", model)
        server.set_detection_install_status(state="success", message=f"Exported `{model}`.", tail=tail_text)

    def _delete_detection_model() -> Any:
        """Delete a downloaded ``.onnx`` from ``<storage>/models``.

        The filename must be a bare basename present in the on-disk set, so a
        crafted ``../`` traversal or unknown name can't reach outside the
        models directory.
        """
        cfg = load_config(server.config_path)
        name = (request.forms.get("model", "") or "").strip()
        on_disk = _discover_storage_models(cfg.detection.storage_path)
        if not name or name != Path(name).name or name not in on_disk:
            return _render_detection(
                cfg,
                install_feedback=f"Cannot delete unknown model: {name or '(empty)'}.",
                install_error=True,
            )
        target = _resolved_models_dir(cfg.detection.storage_path) / name
        try:
            target.unlink()
        except OSError as exc:
            return _render_detection(cfg, install_feedback=f"Could not delete `{name}`: {exc}.", install_error=True)
        logger.info("Deleted detection model %s", name)
        return _render_detection(cfg, install_feedback=f"Deleted `{name}`.")

    @app.post("/section/detection/models/delete")
    def delete_detection_model() -> Any:
        """Delete a downloaded model file from the storage models folder."""
        return _delete_detection_model()

    def _kickoff_detection_export() -> Any:
        """Validate + start a background YOLO→ONNX export, using the status slot
        so it can't race another export.

        Export is an explicit, operator-initiated action that needs an uplink
        and the model-export tools (ultralytics + onnx, installed by
        ``install-detection.sh``). It writes ``<storage>/models/<model>``.
        """
        cfg = load_config(server.config_path)
        # ``export_model`` (not ``model``) so the export dropdown can't clobber
        # the active-model selector when the whole form is saved.
        model = (request.forms.get("export_model", "") or "").strip()
        if model not in _catalogue_model_values():
            return _render_detection(cfg, install_feedback=f"Unknown model: {model or '(empty)'}.", install_error=True)
        if not _export_tools_available():
            return _render_detection(
                cfg,
                install_feedback="Install the model export tools first: run install-detection.sh.",
                install_error=True,
            )
        export_script = _detection_export_script()
        if export_script is None:
            return _render_detection(
                cfg,
                install_feedback="Export script not found. Reinstall the package or run from a source checkout.",
                install_error=True,
            )
        models_dir = _resolved_models_dir(cfg.detection.storage_path)
        imgsz = _coerce_export_imgsz(request.forms.get("imgsz", ""), cfg.detection.inference_size)
        opset = _coerce_export_opset(request.forms.get("opset", ""))
        pt_model = model[: -len(".onnx")] + ".pt" if model.endswith(".onnx") else model + ".pt"

        if not server.try_claim_detection_install(
            action="export",
            extra=model,
            message=f"Exporting `{model}` (imgsz {imgsz}, opset {opset})...",
        ):
            return _render_detection(cfg, install_feedback="An export is already in progress.", install_error=True)

        argv = [
            sys.executable,
            str(export_script),
            pt_model,
            "--imgsz",
            str(imgsz),
            "--opset",
            str(opset),
            "--output-dir",
            str(models_dir),
        ]
        worker = threading.Thread(
            target=_run_detection_export_job,
            kwargs={"model": model, "argv": argv, "timeout": _EXPORT_JOB_TIMEOUT_S},
            daemon=True,
            name=f"detection-export-{model}",
        )
        try:
            worker.start()
        except Exception:
            logger.exception("Failed to spawn detection export worker for %s", model)
            server.set_detection_install_status(
                state="error",
                message=f"Exporting `{model}` failed: could not start worker thread.",
            )
            return _render_detection(
                cfg,
                install_feedback="Could not start the export worker. Try again or export from a terminal.",
                install_error=True,
            )
        return _render_detection(cfg)

    @app.post("/section/detection/export")
    def export_detection_model() -> Any:
        """Download + export a catalogued YOLO model to ONNX on a worker thread.

        Writes ``<storage>/models/<model>`` and reports progress through the
        status slot the section polls. Gated on the export tools being installed
        and the export script being present.
        """
        return _kickoff_detection_export()

    @app.post("/section/otp_output")
    def update_otp_output() -> Any:
        """Update OTP output settings."""
        cfg = _save_section_from_form("otp_output", bool_fields=("enabled",))
        return template("partials/otp_output", config=cfg, saved=True)

    @app.post("/section/rttrpm_output")
    def update_rttrpm_output() -> Any:
        """Update RTTrPM output settings."""
        cfg = _save_section_from_form("rttrpm_output", bool_fields=("enabled",))
        return template("partials/rttrpm_output", config=cfg, saved=True)

    @app.post("/section/trigger_zones")
    def update_trigger_zones() -> Any:
        """Update global trigger-zone settings (zones CRUD handled separately)."""
        cfg = _save_section_from_form(
            "trigger_zones",
            bool_fields=("enabled", "show_overlay"),
        )
        return template("partials/trigger_zones", config=cfg, saved=True)

    # -- OSC bindings CRUD ---------------------------------------------
    #
    # Each binding is a row in ``cfg.osc_transmitters.transmitters`` keyed
    # on the row's stable ``id``. Mutating routes share one shape: load →
    # mutate → save under ``_config_write_lock``, then re-render the partial.
    # The full bindings list is re-rendered on every change so row order
    # (the move route depends on it) stays consistent and a stale form on a
    # different row can't ship an out-of-date trigger sub-form.

    def _osc_bindings_marker_context(cfg: AppConfig) -> dict[str, Any]:
        """Marker-aware template vars for the OSC bindings partial: the
        registered-id list, each row's unresolved-placeholder tokens, and the
        per-row marker display (header / nested chips / dot state).

        Shared by the section render and the index render so the nested
        chips, unresolved pills, and status dots appear at first paint, not
        only after the operator saves a row."""
        registered = frozenset(cfg.controlled_marker_ids)
        return {
            "registered_marker_ids": sorted(registered),
            "unresolved_by_row": {
                row.id: _row_unresolved_placeholders(row, registered, grid_max_height=cfg.grid.max_height)
                for row in cfg.osc_transmitters.transmitters
            },
            "marker_display_by_row": _osc_binding_marker_display(cfg, server.get_marker_catalog()),
        }

    def _render_osc_bindings_section(
        cfg: AppConfig,
        *,
        saved: bool = False,
        focus_id: str = "",
    ) -> Any:
        """Helper used by every CRUD branch: pick up the latest config,
        attach the read-only choice tuples that drive the dropdowns, and
        render the bindings partial. ``focus_id`` carries through so the
        client can re-open the row that just changed.

        Each row carries a precomputed ``unresolved_by_row[row.id]`` tuple
        of bracketed placeholder tokens whose dependencies aren't satisfied.
        The partial renders unresolved pills as ``data-unresolved="true"``
        and marks the row's Enabled checkbox ``data-osc-unresolved="true"``
        (a custom attribute, not ``aria-invalid``, so the shared Save-gate
        doesn't disable Save on a row intentionally Saveable with ``enabled``
        coerced to ``False``). The full registered marker id list rides
        through so the client-side pill JS can re-evaluate dependencies
        without a round-trip.
        """
        from openfollow.configuration import (
            VALID_KEY_NAMES,
            VALID_MIDI_MESSAGE_TYPES,
            VALID_OSC_TRANSMITTER_RATES,
            VALID_TRIGGER_EDGES,
            VALID_TRIGGER_KINDS,
            VALID_TRIGGER_MODIFIERS,
        )
        from openfollow.osc.template import PLACEHOLDERS

        # Form-source data for the trigger forms + ``default_fader`` field.
        # Computed here (config already loaded) so the per-row partial
        # render and the trigger-swap render see identical lists.
        virtual_fader_names = _virtual_fader_names_for_form(cfg)
        midi_patches = _midi_patches_for_form(cfg)
        # User templates live as ``.openfollowtemplate`` files under
        # ``<config-dir>/templates/user/``; system templates ship under
        # ``templates/system/``. Both dropdown lists come from one scan so
        # the partial's ``defined()`` fallback never collapses on first render.
        user_templates, system_templates = _osc_dropdown_templates()
        return template(
            "partials/osc_bindings",
            config=cfg,
            saved=saved,
            focus_id=focus_id,
            valid_rates=VALID_OSC_TRANSMITTER_RATES,
            valid_kinds=VALID_TRIGGER_KINDS,
            valid_edges=VALID_TRIGGER_EDGES,
            valid_modifiers=VALID_TRIGGER_MODIFIERS,
            valid_keys=sorted(VALID_KEY_NAMES),
            valid_buttons=sorted(VALID_BUTTON_NAMES),
            builtin_templates=system_templates,
            user_templates=user_templates,
            placeholders=sorted(PLACEHOLDERS),
            **_osc_bindings_marker_context(cfg),
            valid_midi_types=VALID_MIDI_MESSAGE_TYPES,
            virtual_fader_names=virtual_fader_names,
            marker_fader_names=_marker_fader_names_for_form(server),
            midi_patches=midi_patches,
        )

    def _find_osc_binding(
        cfg: AppConfig,
        row_id: str,
    ) -> tuple[int, OscTransmitterConfig] | None:
        """Locate a row + its index by id; ``None`` when missing. Used
        to short-circuit a 404 on stale UI state (operator deletes a row
        in tab A while tab B still has it open) without crashing the
        request."""
        return _find_by_id(cfg.osc_transmitters.transmitters, row_id)

    @app.get("/section/osc_bindings")
    def get_osc_bindings_section() -> Any:
        """Render the OSC-bindings partial (list + collapsed-row editor).

        ``?focus=<row_id>`` re-opens the named row by adding the ``open``
        attribute on the matching ``<details>``. Used by the per-row Discard
        button to keep the operator's place after a revert re-render.
        """
        cfg = _request_scoped_config()
        # ``.strip()`` so a ``focus`` value with trailing whitespace still
        # matches a row id (otherwise the Discard re-render skips the row).
        focus_id = _as_str(request.query.get("focus", ""), "").strip()
        return _render_osc_bindings_section(cfg, focus_id=focus_id)

    @app.post("/section/osc_bindings/add")
    def add_osc_binding() -> Any:
        """Append a fresh binding. The new row defaults to ``enabled=False``
        and a Stream trigger at 30 Hz.

        Templates are picked at the section level, so the form may carry a
        ``template_id``. When set, the new row's address + args + name are
        populated from the chosen template; empty/unknown yields a blank row.
        """
        template_id = _as_str(request.forms.get("template_id", ""), "").strip()
        with _config_write_lock:
            cfg = load_config(server.config_path)
            row = OscTransmitterConfig(name="New transmitter")
            if template_id:
                # Both system and user templates flow through one
                # ``file:<filename>`` lookup. Dropdown values for user
                # templates are ``file:<filename>`` (filesystem-unique), not
                # the envelope id – ids aren't unique across files, so a
                # duplicate-id template would otherwise be unselectable.
                #
                # Copy every persistable field the template carries (host /
                # port / protocol / rate_hz / trigger), not just name /
                # address / args, so applying restores full row state.
                # ``enabled`` and ``markers`` are NOT copied (apply lands
                # rows inert; default markers are per-binding).
                entry = None
                if template_id.startswith("file:"):
                    bare = template_id[len("file:") :]
                    if _is_safe_template_filename(bare):
                        entry = find_template(_templates_root(server), bare)
                if entry is not None and entry.template is not None:
                    payload = entry.template.payload
                    # Payload ``name`` (the row's own name at save time) takes
                    # precedence over the envelope name (the operator-typed
                    # save name); they can differ, and row identity is wanted
                    # here. Falls back to the envelope name when the payload
                    # carries none.
                    row.name = payload.get("name", entry.template.name)
                    row.address = payload.get("address", "")
                    row.args = list(payload.get("args", []))
                    for opt_field in ("destination_id", "rate_hz"):
                        if opt_field in payload:
                            setattr(row, opt_field, payload[opt_field])
                    if "trigger" in payload:
                        # Pass a copy so a later mutation on
                        # the row can't bleed back into the
                        # cached template payload.
                        row.trigger = dict(payload["trigger"])
            row.__post_init__()
            cfg.osc_transmitters.transmitters.append(row)
            save_config(cfg, server.config_path)
        return _render_osc_bindings_section(cfg, saved=True, focus_id=row.id)

    @app.post("/section/osc_binding/<row_id>")
    def save_osc_binding(row_id: str) -> Any:
        """Apply a posted form to one binding. The row's ``id`` is the
        URL key, not a form field – that prevents a forged form from
        re-targeting another row's slot."""
        # ``request.forms`` is a Bottle ``MultiDict``; ``trigger.modifiers``
        # may have multiple entries (one per checked box). Build a plain
        # dict first, then promote the multi-value field by hand so
        # parsers downstream see a list rather than the last value only.
        form_data: dict[str, Any] = dict(request.forms)
        if "trigger.modifiers" in request.forms:
            form_data["trigger.modifiers"] = request.forms.getall("trigger.modifiers")
        # Bottle's MultiDict drops checkbox-fields entirely when
        # unchecked; ``enabled`` arrives as ``"on"`` when checked. The
        # explicit miss → False conversion is needed because the form
        # parser default-keeps the current value otherwise (preserving
        # the row's enable state across a partial save).
        form_data["enabled"] = "enabled" in request.forms
        # Re-run the message validator server-side: the leading-slash and
        # unclosed-quote checks are a client-side blur gate only, so a
        # programmatic POST (or a browser that skipped the gate) could otherwise
        # persist a non-conformant address, or silently keep the old message
        # while saving every other field. Mirrors the osc-templates save route.
        osc_message = _as_str(form_data.get("osc_message", ""), "").strip()
        if osc_message:
            from openfollow.web.validation import validate

            message_error = validate("osc_binding", "osc_message", osc_message)
            if message_error:
                response.status = 400
                return message_error
        with _config_write_lock:
            cfg = load_config(server.config_path)
            found = _find_osc_binding(cfg, row_id)
            if found is None:
                abort(404, "Unknown binding")
                return ""  # pragma: no cover – abort() raises before we reach here
            _, row = found
            # Pass the registered-marker registry so
            # ``_apply_osc_binding_fields`` can coerce ``enabled=False`` when
            # the row's templates reference an unresolved placeholder; the
            # next render's red-pill surfaces why. Save isn't gated.
            _apply_osc_binding_fields(
                row,
                form_data,
                registered_marker_ids=frozenset(cfg.controlled_marker_ids),
                grid_max_height=cfg.grid.max_height,
            )
            save_config(cfg, server.config_path)
        return _render_osc_bindings_section(cfg, saved=True, focus_id=row_id)

    @app.post("/section/osc_binding/<row_id>/duplicate")
    def duplicate_osc_binding(row_id: str) -> Any:
        """Clone the row immediately after the original. The clone
        keeps ``enabled`` as-is so an operator duplicating a working
        row gets a working row out of the box; only the ``id`` and
        ``name`` are changed (name suffixed with ``" (copy)"`` so the
        list view tells the two apart)."""
        with _config_write_lock:
            cfg = load_config(server.config_path)
            found = _find_osc_binding(cfg, row_id)
            if found is None:
                abort(404, "Unknown binding")
                return ""  # pragma: no cover – abort() raises before we reach here
            idx, row = found
            clone = copy.deepcopy(row)
            clone.id = ""  # force a fresh uuid in __post_init__
            clone.name = (row.name or "Binding") + " (copy)"
            clone.__post_init__()
            cfg.osc_transmitters.transmitters.insert(idx + 1, clone)
            save_config(cfg, server.config_path)
        return _render_osc_bindings_section(cfg, saved=True, focus_id=clone.id)

    @app.post("/section/osc_binding/<row_id>/delete")
    def delete_osc_binding(row_id: str) -> Any:
        """Remove a row by id. No-op (with a saved=True render) if the
        row was already gone – keeps the UI stable in the face of
        concurrent edits across tabs."""
        with _config_write_lock:
            cfg = load_config(server.config_path)
            found = _find_osc_binding(cfg, row_id)
            if found is not None:
                idx, _ = found
                del cfg.osc_transmitters.transmitters[idx]
                save_config(cfg, server.config_path)
        return _render_osc_bindings_section(cfg, saved=True)

    @app.post("/section/osc_binding/<row_id>/move")
    def move_osc_binding(row_id: str) -> Any:
        """Re-order a row. ``direction`` is ``up`` or ``down``; out-of-
        bounds moves (top row up, bottom row down) are no-ops so the
        operator can mash the button without the server complaining.

        Kept as a stable JSON-API surface (an external client / future
        keyboard-shortcut path can still fire single-step swaps) even
        though the web UI's drag-handle path uses the bulk
        :func:`reorder_osc_bindings` endpoint instead.
        """
        direction = _as_str(request.forms.get("direction", ""), "")
        with _config_write_lock:
            cfg = load_config(server.config_path)
            found = _find_osc_binding(cfg, row_id)
            if found is None:
                abort(404, "Unknown binding")
                return ""  # pragma: no cover – abort() raises before we reach here
            idx, _ = found
            moved = _swap_for_direction(cfg.osc_transmitters.transmitters, idx, direction)
            if moved:
                save_config(cfg, server.config_path)
        return _render_osc_bindings_section(cfg, saved=moved, focus_id=row_id)

    @app.post("/section/osc_bindings/reorder")
    def reorder_osc_bindings() -> Any:
        """Apply a complete row ordering produced by the drag-handle UI.

        The form carries an ``order`` field – a comma-separated list of
        row ids in the new desired order. The server reorders
        ``cfg.osc_transmitters.transmitters`` accordingly and re-renders
        the section.

        Defensive contract: any id in ``order`` that doesn't match an
        existing row is dropped (a stale tab might POST a list with a
        deleted-elsewhere id). Any current row whose id is *missing*
        from ``order`` is appended to the end of the new ordering so an
        out-of-sync client can't silently delete rows by omitting
        them. Empty / malformed ``order`` is a no-op so the operator
        doesn't lose work to a buggy drag interaction.
        """
        raw_order = _as_str(request.forms.get("order", ""), "")
        wanted_ids = [s for s in (s.strip() for s in raw_order.split(",")) if s]
        # ``saved`` mirrors the pre-existing behaviour: an empty /
        # malformed ``order`` is a no-op and reports back without the
        # success-flash; a non-empty ``order`` always reports back with
        # the flash, even if the resulting permutation matches what was
        # already on disk.
        saved = bool(wanted_ids)
        with _config_write_lock:
            cfg = load_config(server.config_path)
            transmitters = cfg.osc_transmitters.transmitters
            current = {row.id: row for row in transmitters}
            if wanted_ids:
                new_order: list[OscTransmitterConfig] = []
                seen: set[str] = set()
                for rid in wanted_ids:
                    row = current.get(rid)
                    if row is not None and rid not in seen:
                        new_order.append(row)
                        seen.add(rid)
                # Append any rows that were missing from the wanted
                # list so we never lose data to a stale form post.
                for row in transmitters:
                    if row.id not in seen:
                        new_order.append(row)
                if [r.id for r in new_order] != [r.id for r in transmitters]:
                    cfg.osc_transmitters.transmitters = new_order
                    save_config(cfg, server.config_path)
        # Template rendering happens outside the write lock – rendering
        # the bindings section walks every row + its choice tuples and
        # would otherwise block any concurrent CRUD writer for the
        # render duration.
        return _render_osc_bindings_section(cfg, saved=saved)

    # -- OSC destinations CRUD -----------------------------------------
    #
    # Shared connection profiles in ``cfg.osc_destinations.destinations``,
    # keyed on a stable ``id``. Transmitters and zones reference a profile
    # by id; editing one repoints every consumer live. Same load → mutate →
    # save → full-re-render shape as the bindings CRUD above.

    def _render_osc_destinations_section(
        cfg: AppConfig,
        *,
        saved: bool = False,
        focus_id: str = "",
    ) -> Any:
        from openfollow.configuration import (
            VALID_OSC_FRAMINGS,
            VALID_OSC_TRANSMITTER_PROTOCOLS,
        )

        return template(
            "partials/osc_destinations",
            config=cfg,
            saved=saved,
            focus_id=focus_id,
            valid_protocols=VALID_OSC_TRANSMITTER_PROTOCOLS,
            valid_framings=VALID_OSC_FRAMINGS,
        )

    def _find_osc_destination(
        cfg: AppConfig,
        dest_id: str,
    ) -> tuple[int, OscDestinationConfig] | None:
        return _find_by_id(cfg.osc_destinations.destinations, dest_id)

    @app.get("/section/osc_destinations")
    def get_osc_destinations_section() -> Any:
        cfg = _request_scoped_config()
        focus_id = _as_str(request.query.get("focus", ""), "").strip()
        return _render_osc_destinations_section(cfg, focus_id=focus_id)

    @app.post("/section/osc_destinations/add")
    def add_osc_destination() -> Any:
        with _config_write_lock:
            cfg = load_config(server.config_path)
            dest = OscDestinationConfig(name="New destination")
            cfg.osc_destinations.destinations.append(dest)
            save_config(cfg, server.config_path)
        return _render_osc_destinations_section(cfg, saved=True, focus_id=dest.id)

    @app.post("/section/osc_destination/<dest_id>")
    def save_osc_destination(dest_id: str) -> Any:
        form_data: dict[str, Any] = dict(request.forms)
        with _config_write_lock:
            cfg = load_config(server.config_path)
            found = _find_osc_destination(cfg, dest_id)
            if found is None:
                abort(404, "Unknown destination")
                return ""  # pragma: no cover – abort() raises first
            _, dest = found
            _apply_osc_destination_fields(dest, form_data)
            save_config(cfg, server.config_path)
        return _render_osc_destinations_section(cfg, saved=True, focus_id=dest_id)

    @app.post("/section/osc_destination/<dest_id>/duplicate")
    def duplicate_osc_destination(dest_id: str) -> Any:
        with _config_write_lock:
            cfg = load_config(server.config_path)
            found = _find_osc_destination(cfg, dest_id)
            if found is None:
                abort(404, "Unknown destination")
                return ""  # pragma: no cover – abort() raises first
            idx, dest = found
            clone = copy.deepcopy(dest)
            clone.id = ""  # force a fresh uuid in __post_init__
            clone.name = (dest.name or "Destination") + " (copy)"
            clone.__post_init__()
            cfg.osc_destinations.destinations.insert(idx + 1, clone)
            save_config(cfg, server.config_path)
        return _render_osc_destinations_section(cfg, saved=True, focus_id=clone.id)

    @app.post("/section/osc_destination/<dest_id>/delete")
    def delete_osc_destination(dest_id: str) -> Any:
        with _config_write_lock:
            cfg = load_config(server.config_path)
            found = _find_osc_destination(cfg, dest_id)
            if found is not None:
                idx, _ = found
                del cfg.osc_destinations.destinations[idx]
                save_config(cfg, server.config_path)
        return _render_osc_destinations_section(cfg, saved=True)

    @app.post("/section/osc_destination/<dest_id>/move")
    def move_osc_destination(dest_id: str) -> Any:
        direction = _as_str(request.forms.get("direction", ""), "")
        with _config_write_lock:
            cfg = load_config(server.config_path)
            found = _find_osc_destination(cfg, dest_id)
            if found is None:
                abort(404, "Unknown destination")
                return ""  # pragma: no cover – abort() raises first
            idx, _ = found
            moved = _swap_for_direction(cfg.osc_destinations.destinations, idx, direction)
            if moved:
                save_config(cfg, server.config_path)
        return _render_osc_destinations_section(cfg, saved=moved, focus_id=dest_id)

    @app.post("/section/osc_destinations/reorder")
    def reorder_osc_destinations() -> Any:
        """Apply a complete destination ordering from the drag-handle UI.

        Mirrors :func:`reorder_osc_bindings`: ``order`` is a comma-separated
        list of destination ids in the new order. Unknown ids are dropped; any
        current destination missing from ``order`` is appended so a stale client
        can't delete by omission. Empty / malformed ``order`` is a no-op (no
        success flash). The per-id ``/move`` route stays as a stable JSON-API
        surface for single-step swaps.
        """
        raw_order = _as_str(request.forms.get("order", ""), "")
        wanted_ids = [s for s in (s.strip() for s in raw_order.split(",")) if s]
        saved = bool(wanted_ids)
        with _config_write_lock:
            cfg = load_config(server.config_path)
            dests = cfg.osc_destinations.destinations
            current = {d.id: d for d in dests}
            if wanted_ids:
                new_order: list[OscDestinationConfig] = []
                seen: set[str] = set()
                for did in wanted_ids:
                    dest = current.get(did)
                    if dest is not None and did not in seen:
                        new_order.append(dest)
                        seen.add(did)
                # Append any destinations missing from the wanted list so a
                # stale form post can't silently drop them.
                for dest in dests:
                    if dest.id not in seen:
                        new_order.append(dest)
                if [d.id for d in new_order] != [d.id for d in dests]:
                    cfg.osc_destinations.destinations = new_order
                    save_config(cfg, server.config_path)
        return _render_osc_destinations_section(cfg, saved=saved)

    @app.get("/section/osc_binding/<row_id>/trigger_form")
    def get_osc_binding_trigger_form(row_id: str) -> Any:
        """Return the trigger-specific input fields for the chosen
        trigger type. The dropdown's ``hx-get`` + ``hx-include="this"``
        sends the selection as ``?trigger.type=<value>``; we accept
        the legacy ``?kind=`` and ``?trigger.kind=`` spellings too so
        existing API clients don't break. Sticks with the row's
        currently-stored values where they apply (so flipping the
        type off-and-on doesn't wipe partial entry); falls back to
        per-kind defaults otherwise."""
        from openfollow.configuration import VALID_TRIGGER_KINDS

        # Accept the new wire name first; fall back to the legacy
        # spellings ``trigger.kind`` and ``kind`` for older callers.
        kind = _as_str(
            request.query.get("trigger.type")
            or request.query.get("trigger.kind")
            or request.query.get("kind", "stream"),
            "stream",
        )
        if kind not in VALID_TRIGGER_KINDS:
            kind = "stream"
        cfg = _request_scoped_config()
        found = _find_osc_binding(cfg, row_id)
        if found is None:
            abort(404, "Unknown binding")
            return ""  # pragma: no cover – abort() raises before we reach here
        _, row = found
        from openfollow.configuration import (
            VALID_KEY_NAMES,
            VALID_MIDI_MESSAGE_TYPES,
            VALID_OSC_TRANSMITTER_RATES,
            VALID_TRIGGER_EDGES,
            VALID_TRIGGER_MODIFIERS,
        )

        return template(
            "partials/osc_binding_trigger_form",
            row=row,
            kind=kind,
            valid_rates=VALID_OSC_TRANSMITTER_RATES,
            valid_edges=VALID_TRIGGER_EDGES,
            valid_modifiers=VALID_TRIGGER_MODIFIERS,
            valid_keys=sorted(VALID_KEY_NAMES),
            valid_buttons=sorted(VALID_BUTTON_NAMES),
            # Trigger forms need the MIDI message types + fader / device
            # aliases; ``defined()`` fallbacks keep older callers safe.
            valid_midi_types=VALID_MIDI_MESSAGE_TYPES,
            virtual_fader_names=_virtual_fader_names_for_form(cfg),
            marker_fader_names=_marker_fader_names_for_form(server),
            midi_patches=_midi_patches_for_form(cfg),
        )

    # -- OSC bindings diagnostics + templates --------------------------
    #
    # All diagnostics endpoints degrade gracefully when no manager is
    # attached (returning ``{"available": False, ...}``) so the section
    # renders cleanly during boot, mid-restart, and in unit tests where
    # only the route layer is exercised.

    @app.get("/api/osc_binding/<row_id>/status")
    def api_osc_binding_status(row_id: str) -> Any:
        """Return per-row health: pps, last_error, healthy, recent
        ring-buffer entries. ``available=False`` signals the manager
        isn't attached yet (boot/restart/test) – the UI shows a
        placeholder rather than fake green."""
        response.content_type = "application/json"
        cfg = _request_scoped_config()
        if _find_osc_binding(cfg, row_id) is None:
            response.status = 404
            return json.dumps({"available": False, "error": "Unknown binding"})
        status = server.get_osc_binding_status(row_id)
        if status is None:
            # Row is in config (checked above), so a missing live status means
            # either the manager hasn't picked it up yet (saved, awaiting the
            # ~1 Hz re-apply) or no manager is attached (boot/restart). ``pending``
            # splits the two so the UI says "saved – not yet serviced" vs "detached".
            return json.dumps({"available": False, "pending": server.is_osc_manager_attached()})
        # Defensive: provider returns a dict, but we guarantee the
        # ``available`` flag is populated so the UI never has to handle
        # a partial shape.
        out = {"available": True}
        out.update(status)
        return json.dumps(out)

    @app.post("/api/osc_binding/<row_id>/test")
    def api_osc_binding_test(row_id: str) -> Any:
        """Force a one-shot send for ``row_id``. Bypasses the row's
        ``enabled`` flag (so a disabled row can still be probed before
        flipping it on) but obeys ``OscService.send`` failure modes,
        which feed back into the ring buffer. Returns the manager's
        report dict (``sent`` / ``error`` / ``address`` / ``args``)."""
        response.content_type = "application/json"
        cfg = load_config(server.config_path)
        if _find_osc_binding(cfg, row_id) is None:
            response.status = 404
            return json.dumps({"available": False, "error": "Unknown binding"})
        result = server.trigger_osc_binding_test(row_id)
        if not result:
            # ``pending`` = manager attached but this row isn't serviced yet.
            return json.dumps({"available": False, "pending": server.is_osc_manager_attached()})
        out = {"available": True}
        out.update(result)
        return json.dumps(out)

    # MIDI Learn capture for the OSC binding form's Capture button. Arm is
    # POST (state-changing); poll is GET (read-only) so HTMX's ``every
    # 250ms`` trigger doesn't double-fire arm. Both return JSON the form's
    # HTMX swap handler reads to populate fields or render waiting/timeout.
    @app.post("/api/osc/midi/learn/arm")
    def api_osc_midi_learn_arm() -> Any:
        """Arm a MIDI Learn capture. Idempotent – a second click
        cancels the previous arm and starts a new one (operator
        wants to re-capture). Returns ``{"status": "armed"}`` on
        success, ``{"status": "unavailable"}`` when no MIDI
        subsystem is wired (test contexts / pre-init boot)."""
        response.content_type = "application/json"
        return json.dumps(server.midi_capture_arm())

    @app.get("/api/osc/midi/learn/poll")
    def api_osc_midi_learn_poll() -> Any:
        """Return the broker's current state. The form polls this
        every 250 ms while the operator is in the Capture flow.
        Possible shapes:

        - ``{"status": "idle"}`` – no arm pending.
        - ``{"status": "waiting", "elapsed_s": <float>}`` – armed,
          no event yet.
        - ``{"status": "captured", "patch_id": ..., "type": ...,
          "channel": <int>, "number": <int|null>, "value": <int>}``
          – first event landed; the broker drops back to idle.
        - ``{"status": "timeout"}`` – armed, no event before the
          window elapsed; the broker drops back to idle.
        - ``{"status": "unavailable"}`` – no MIDI subsystem
          wired (test / pre-init).
        """
        response.content_type = "application/json"
        return json.dumps(server.midi_capture_poll())

    # The trigger form's Capture button drives a pure-HTMX flow against
    # these section routes (the JSON ``/api`` ones above stay for external
    # clients). ``arm`` returns the waiting partial, which includes a 250 ms
    # poll driver fetching ``poll`` until the broker classifies an event.
    #
    # ``row_id`` is a path parameter (not a form field) so the captured
    # partial's OOB-swap fragments target the right form-field ids – two
    # simultaneous Capture flows on different rows can't clobber each other.
    @app.post("/section/osc/midi/learn/arm/<row_id>")
    def section_osc_midi_learn_arm(row_id: str) -> Any:
        cfg = load_config(server.config_path)
        if _find_osc_binding(cfg, row_id) is None:
            abort(404, "Unknown binding")
            return ""  # pragma: no cover - abort() raises before we reach here
        poll = server.midi_capture_arm(row_id)
        return _render_midi_capture_status(cfg, row_id, poll)

    @app.get("/section/osc/midi/learn/poll/<row_id>")
    def section_osc_midi_learn_poll(row_id: str) -> Any:
        cfg = load_config(server.config_path)
        if _find_osc_binding(cfg, row_id) is None:
            abort(404, "Unknown binding")
            return ""  # pragma: no cover - abort() raises before we reach here
        poll = server.midi_capture_poll(row_id)
        return _render_midi_capture_status(cfg, row_id, poll)

    @app.get("/api/osc_binding/<row_id>/preview")
    def api_osc_binding_preview(row_id: str) -> Any:
        """Return the rendered address + args for ``row_id`` against
        current marker state. Lets the operator see exactly what would
        ship without actually transmitting anything."""
        response.content_type = "application/json"
        cfg = _request_scoped_config()
        if _find_osc_binding(cfg, row_id) is None:
            response.status = 404
            return json.dumps({"available": False, "error": "Unknown binding"})
        preview = server.get_osc_binding_preview(row_id)
        if preview is None:
            # ``pending`` = manager attached but this row isn't serviced yet.
            return json.dumps({"available": False, "pending": server.is_osc_manager_attached()})
        out = {"available": True}
        out.update(preview)
        return json.dumps(out)

    @app.post("/section/osc_templates")
    def save_osc_template() -> Any:
        """Persist a custom template the operator built up in a row.

        Required form fields ``name`` and ``osc_message``; the latter uses
        the same combined-field shape as the per-row save. Writes a
        ``.openfollowtemplate`` file under ``<config-dir>/templates/user/``
        via :func:`write_user_template`.

        Delegates to :func:`openfollow.osc.parser.tokenize_osc_message` so
        the per-row save and this template save share one tokeniser (a plain
        ``split()`` would mangle a quoted arg). Also reuses the per-row
        :class:`FieldRule` validators via
        :func:`openfollow.web.validation.validate` so the same ``max_len``
        caps (``name`` ≤ 64, ``osc_message`` ≤ 2048) and unclosed-quote /
        leading-slash checks gate the persisted file, preventing a
        programmatic POST from writing an oversized name/message.
        """
        from openfollow.osc.parser import tokenize_osc_message
        from openfollow.web.validation import validate

        form_data: dict[str, Any] = dict(request.forms)
        name = _as_str(form_data.get("name", ""), "").strip()
        message = _as_str(form_data.get("osc_message", ""), "").strip()
        if not name or not message:
            response.status = 400
            return template(
                "partials/osc_template_save_result",
                ok=False,
                error="Name and OSC message are required.",
            )
        # Reuse the per-row blur validators. ``validate(...)`` returns
        # the human-error string the FIELD_RULES would surface (for
        # ``name`` → max_len 64; for ``osc_message`` → unclosed quote,
        # max_len 2048, address-starts-with-``/``). Empty inputs short-
        # circuit to ``None`` inside the validator, which is why the
        # explicit-empty check above runs first.
        for field_name, value in (("name", name), ("osc_message", message)):
            err = validate("osc_binding", field_name, value)
            if err:
                response.status = 400
                return template(
                    "partials/osc_template_save_result",
                    ok=False,
                    error=err,
                )
        # ``validate`` already exercised the same ``tokenize_osc_message``
        # via ``_validate_osc_message``, so this re-tokenise can't raise
        # ``ValueError``. Defensive guard for future drift; cheap.
        address, args = tokenize_osc_message(message)
        try:
            write_user_template(
                _templates_root(server),
                "osc_output",
                name,
                {"address": address, "args": args},
            )
        # pragma: no cover branch – defensive; the validator above already
        # caught bad-input shapes, so the writer can't raise in normal flow.
        except (
            TemplateWriteError,
            TemplateValidationError,
        ) as exc:  # pragma: no cover
            response.status = 400
            return template(
                "partials/osc_template_save_result",
                ok=False,
                error=str(exc),
            )
        return template(
            "partials/osc_template_save_result",
            ok=True,
            error="",
        )

    @app.post("/section/osc_binding/<row_id>/save_as_template")
    def save_osc_binding_as_template(row_id: str) -> Any:
        """Capture an OSC binding row's full live form state as a
        ``.openfollowtemplate`` file under ``templates/user/``.

        Mirrors the per-row Save POST shape (same form fields) plus a
        ``template_name`` field, so every form parser the row's Save uses
        runs here too via ``_apply_osc_binding_fields`` (no duplicated
        parsing in JS). Captured payload: name / host / port / protocol /
        address / args / rate_hz / trigger. ``enabled`` and ``marker_id``
        are omitted so applying a template lands the row inert with no
        default marker.

        Returns the bindings-section partial so the dropdown refreshes in
        the same response cycle as the click.
        """
        form_data: dict[str, Any] = dict(request.forms)
        # Bottle's MultiDict drops checkbox-fields entirely when
        # unchecked; the ``trigger.modifiers`` checkbox group needs
        # the same multi-value lift the row's Save handler uses.
        if "trigger.modifiers" in request.forms:
            form_data["trigger.modifiers"] = request.forms.getall(
                "trigger.modifiers",
            )
        template_name = _as_str(
            form_data.get("template_name", ""),
            "",
        ).strip()
        if not template_name:
            response.status = 400
            return template(
                "partials/osc_template_save_result",
                ok=False,
                error="Template name is required.",
            )
        # Validate the message server-side (leading-slash / unclosed-quote) before
        # snapshotting, so a programmatic POST can't capture a non-conformant
        # address into a saved template. The empty-address gate below is a
        # secondary check; the leading-slash rule needs this validator.
        osc_message = _as_str(form_data.get("osc_message", ""), "").strip()
        if osc_message:
            from openfollow.web.validation import validate

            message_error = validate("osc_binding", "osc_message", osc_message)
            if message_error:
                response.status = 400
                return template(
                    "partials/osc_template_save_result",
                    ok=False,
                    error=message_error,
                )
        # Build a transient row from the form so every field gets
        # the same coercion + validation it would on a normal Save.
        # Doesn't touch the live config – the row is discarded after
        # we snapshot its persistable fields into the template.
        transient = OscTransmitterConfig()
        _apply_osc_binding_fields(transient, form_data)
        if not transient.address:
            response.status = 400
            return template(
                "partials/osc_template_save_result",
                ok=False,
                error="OSC address is required.",
            )
        # Build the template payload: every persistable field except
        # ``id`` (per-row uuid), ``enabled`` (apply-time False), and
        # ``markers`` (per-binding operator choice). ``template_id``
        # is also dropped – it's row metadata pointing at the *source*
        # template, not part of the saved-template content.
        trigger_dict = asdict(transient.trigger) if transient.trigger is not None else {"kind": "stream", "rate_hz": 30}
        payload: dict[str, Any] = {
            "name": transient.name,
            "destination_id": transient.destination_id,
            "address": transient.address,
            "args": list(transient.args),
            "rate_hz": transient.rate_hz,
            "trigger": trigger_dict,
        }
        try:
            write_user_template(
                _templates_root(server),
                "osc_output",
                template_name,
                payload,
            )
        # pragma: no cover branch – defensive; ``transient.address`` is
        # non-empty per the gate above and every field is already coerced by
        # ``__post_init__`` to a writer-acceptable shape.
        except (
            TemplateWriteError,
            TemplateValidationError,
        ) as exc:  # pragma: no cover
            response.status = 400
            return template(
                "partials/osc_template_save_result",
                ok=False,
                error=str(exc),
            )
        # Return the bindings partial so the dropdown refreshes with
        # the freshly-saved template entry. Same shape as the row's
        # Save response, so the JS button can reuse the section
        # ``hx-swap`` plumbing without a one-off content-type
        # negotiation.
        cfg = load_config(server.config_path)
        return _render_osc_bindings_section(cfg, saved=True)

    # ---- File-based template system ---------------------------------------
    #
    # Routes for listing, saving, applying, and deleting templates of the
    # three supported types (``osc_output`` / ``camera_grid`` / ``zones``).
    # The on-disk layer is :mod:`openfollow.templates`; these routes are thin
    # HTTP adapters over its loader / writer / schema functions so the
    # validation rules (envelope shape, payload validation, slug + conflict
    # numbering, path-traversal refusal) live in one place.

    def _filename_or_400(filename: str) -> bool:
        """Reject a filename that fails :func:`_is_safe_template_filename`
        with a 400 response. Returns ``True`` when the filename is safe
        (caller can proceed); ``False`` when the response was already
        set by this helper."""
        if not _is_safe_template_filename(filename):
            response.status = 400
            return False
        return True

    def _entry_to_dict(entry: LoadedTemplate) -> dict[str, Any]:
        """Serialise a :class:`LoadedTemplate` for the JSON list endpoint.

        Both successful and failed loads are returned in the same
        flat shape – ``error`` is empty on success, populated on
        failure – so the UI can render both kinds of entries from the
        same client-side loop."""
        out: dict[str, Any] = {
            "filename": entry.filename,
            "is_system": entry.is_system,
            "error": entry.error,
        }
        if entry.template is not None:
            out["type"] = entry.template.type
            out["id"] = entry.template.id
            out["name"] = entry.template.name
        else:
            # Failed-load entries don't have a usable type / id /
            # name; surface empty strings so the UI's null-handling
            # is uniform across the list.
            out["type"] = ""
            out["id"] = ""
            out["name"] = ""
        return out

    @app.get("/api/templates")
    def api_list_templates() -> Any:
        """List ``.openfollowtemplate`` files filtered by ``?type=<type>``.

        ``?type`` is required – the dropdowns are per-section and
        always know which type they're showing; surfacing the global
        list to the client would only invite client-side filtering
        bugs. Returns the merged system + user list with stable
        ordering (sort key inside the loader).

        Failed loads (malformed JSON, envelope/payload failure) are
        surfaced with ``error`` populated and ``type`` empty so the dropdown
        can render a disabled "(unreadable: ...)" entry. Successfully loaded
        entries of other types are filtered out so the dropdown stays scoped.
        """
        response.content_type = "application/json"
        template_type = request.query.get("type", "")
        if template_type not in TEMPLATE_VALID_TYPES:
            response.status = 400
            return json.dumps(
                {
                    "error": (f"unknown type {template_type!r} (expected one of {', '.join(TEMPLATE_VALID_TYPES)})"),
                }
            )
        # Filename convention ``<type>.<slug>.openfollowtemplate``. Scope
        # unreadable files to the requested type by prefix so a broken
        # ``osc_output`` file doesn't surface in the zones / camera_grid
        # choosers. Files lacking the prefix (hand-renamed) are dropped from
        # every type's view – the loader can't classify them.
        type_prefix = template_type + "."
        entries = [
            entry
            for entry in list_templates(_templates_root(server))
            if (entry.template is None and entry.filename.startswith(type_prefix))
            or (entry.template is not None and entry.template.type == template_type)
        ]
        return json.dumps(
            {
                "templates": [_entry_to_dict(e) for e in entries],
            }
        )

    @app.post("/api/templates/<template_type>/save")
    def api_save_template(template_type: str) -> Any:
        """Save the operator's current section state as a user template.

        Body shape depends on ``template_type``:

        - ``osc_output``: ``{"name": str, "payload": {"address": str,
          "args": [str]}}`` – the operator's row-level shape, mirrored
          from the HTMX modal so the message they typed becomes the
          saved template verbatim.
        - ``camera_grid``: ``{"name": str}`` – the server reads the
          current ``camera`` + ``grid`` sections from disk so the
          template captures whatever the operator just saved (no body
          payload needed; "what you see in the form" matches what's in
          ``config.toml`` after the section's own POST).
        - ``zones``: ``{"name": str}`` – same pattern; the server
          reads the current ``trigger_zones`` section.

        Both implicit-read variants run inside the config-write lock
        so a concurrent ``/section/...`` POST can't tear the captured
        snapshot."""
        response.content_type = "application/json"
        if template_type not in TEMPLATE_VALID_TYPES:
            response.status = 400
            return json.dumps({"error": f"unknown type {template_type!r}"})
        data = _load_json_body()
        if data is None:
            return json.dumps({"error": "Invalid JSON"})
        if not isinstance(data, dict):
            response.status = 400
            return json.dumps({"error": "Expected a JSON object"})
        name = _as_str(data.get("name", ""), "").strip()
        if not name:
            response.status = 400
            return json.dumps({"error": "name is required"})
        if template_type == "osc_output":
            payload_raw = data.get("payload", {})
            if not isinstance(payload_raw, dict):
                response.status = 400
                return json.dumps(
                    {
                        "error": "payload must be an object for osc_output",
                    }
                )
            # Pass the request payload through unchanged so the
            # schema validator's "missing address" / "non-str address"
            # checks fire as designed. Filling in defaults here would
            # silently turn a missing-address request into a saved
            # template with ``address=""``.
            payload = dict(payload_raw)
        elif template_type == "camera_grid":
            with _config_write_lock:
                cfg = load_config(server.config_path)
                payload = {
                    "camera": asdict(cfg.camera),
                    "grid": asdict(cfg.grid),
                }
        else:  # zones – the only remaining option per the early gate
            with _config_write_lock:
                cfg = load_config(server.config_path)
                payload = asdict(cfg.trigger_zones)
        try:
            written_path = write_user_template(
                _templates_root(server),
                template_type,
                name,
                payload,
            )
        except (TemplateValidationError, TemplateWriteError) as exc:
            response.status = 400
            return json.dumps({"error": str(exc)})
        # Re-read the just-written file via the loader so the response
        # carries the canonical envelope (id, disambiguated name, etc.)
        # rather than re-deriving them from the request input.
        entry = find_template(_templates_root(server), written_path.name)
        if entry is None or entry.template is None:  # pragma: no cover – race
            return json.dumps({"ok": True, "filename": written_path.name})
        return json.dumps(
            {
                "ok": True,
                "filename": entry.filename,
                "id": entry.template.id,
                "name": entry.template.name,
            }
        )

    @app.post("/api/templates/<filename>/apply")
    def api_apply_template(filename: str) -> Any:
        """Apply a named template to the live config.

        - ``osc_output`` creates a new row pre-populated from the
          template; returns ``{"ok": true, "row_id": "..."}`` so the
          caller can scroll to / open the new row.
        - ``camera_grid`` replaces the current ``camera`` + ``grid``
          sections (whichever the payload carries – partial templates
          are honoured; the absent half is left alone).
        - ``zones`` replaces the entire ``trigger_zones`` section.

        ``camera_grid`` and ``zones`` require ``?confirm=1`` because
        they overwrite a whole section. The caller's UI should show a
        confirm dialog before adding the parameter; the server gate
        is defence-in-depth so a forged request can't side-effect
        without intent."""
        response.content_type = "application/json"
        if not _filename_or_400(filename):
            return json.dumps({"error": "invalid filename"})
        entry = find_template(_templates_root(server), filename)
        if entry is None:
            response.status = 404
            return json.dumps({"error": "template not found"})
        if entry.template is None:
            # Loader couldn't decode the file (malformed JSON,
            # envelope failure, payload failure). Surface the
            # operator-facing reason verbatim so the UI can show
            # what's wrong without a second round-trip.
            response.status = 400
            return json.dumps({"error": entry.error})
        tpl = entry.template
        confirm = request.query.get("confirm", "") == "1"
        if tpl.type in ("camera_grid", "zones") and not confirm:
            response.status = 400
            return json.dumps(
                {
                    "error": (
                        f"applying a {tpl.type} template overwrites the current section; pass ?confirm=1 to proceed"
                    ),
                }
            )
        if tpl.type == "osc_output":
            # Restore every persistable row field the template carries.
            # ``enabled`` and ``markers`` are ignored even if a template
            # carries them – apply always lands the row inert with no default
            # marker. ``name`` falls back to the envelope name so a minimal
            # ``{address, args}`` template still gets a readable label.
            with _config_write_lock:
                cfg = load_config(server.config_path)
                kwargs: dict[str, Any] = {
                    "name": tpl.payload.get("name", tpl.name),
                    "address": tpl.payload.get("address", ""),
                    "args": list(tpl.payload.get("args", [])),
                }
                for opt_field in ("destination_id", "rate_hz"):
                    if opt_field in tpl.payload:
                        kwargs[opt_field] = tpl.payload[opt_field]
                if "trigger" in tpl.payload:
                    # ``OscTransmitterConfig.__post_init__`` accepts a
                    # dict for ``trigger`` and runs it through
                    # ``_trigger_from_dict`` so the kind discriminator
                    # picks the right typed trigger. Pass a copy so a
                    # later mutation on the row can't bleed back into
                    # the cached template payload.
                    kwargs["trigger"] = dict(tpl.payload["trigger"])
                row = OscTransmitterConfig(**kwargs)
                # Defence in depth: even if a malformed template
                # bypassed validation and carries enabled/markers
                # fields, we still strip them after construction.
                row.enabled = False
                row.markers = []
                cfg.osc_transmitters.transmitters.append(row)
                save_config(cfg, server.config_path)
            return json.dumps({"ok": True, "row_id": row.id})
        if tpl.type == "camera_grid":
            # The schema requires BOTH halves, so this apply path needs no
            # conditional checks – a partial template is rejected by the
            # loader and would have returned 404 / 400 above.
            from openfollow.configuration import CameraConfig, GridConfig

            with _config_write_lock:
                cfg = load_config(server.config_path)
                cfg.camera = CameraConfig(**tpl.payload["camera"])
                cfg.grid = GridConfig(**tpl.payload["grid"])
                save_config(cfg, server.config_path)
            return json.dumps({"ok": True})
        # tpl.type == "zones" – the only remaining option per the
        # early gate against ``VALID_TYPES``.
        from openfollow.configuration import TriggerZonesConfig

        with _config_write_lock:
            cfg = load_config(server.config_path)
            cfg.trigger_zones = TriggerZonesConfig(**tpl.payload)
            save_config(cfg, server.config_path)
        return json.dumps({"ok": True})

    @app.delete("/api/templates/<filename>")
    def api_delete_template(filename: str) -> Any:
        """Delete a user template. Refuses system files with HTTP 403.

        The route layer enforces the system-folder ban; the writer's
        ``delete_user_template`` is also gated against path traversal
        as defence-in-depth, so a future direct caller (e.g. an
        import script) can't bypass the check by sending a crafted
        ``../system/foo`` filename."""
        response.content_type = "application/json"
        if not _filename_or_400(filename):
            return json.dumps({"error": "invalid filename"})
        entry = find_template(_templates_root(server), filename)
        if entry is None:
            response.status = 404
            return json.dumps({"error": "template not found"})
        if entry.is_system:
            response.status = 403
            return json.dumps(
                {
                    "error": "system templates cannot be deleted",
                }
            )
        try:
            removed = delete_user_template(
                _templates_root(server),
                filename,
            )
        # pragma: no cover branch – defensive; ``find_template`` already
        # validated the file lands inside the user folder, so the writer's
        # path-traversal refusal is unreachable here.
        except TemplateWriteError as exc:  # pragma: no cover
            response.status = 400
            return json.dumps({"error": str(exc)})
        if not removed:  # pragma: no cover – caught by find_template above
            response.status = 404
            return json.dumps({"error": "template not found"})
        return json.dumps({"ok": True})

    @app.get("/api/validate/<section>/<field_name>")
    def api_validate(section: str, field_name: str) -> Any:
        """On-blur per-field validation.

        HTMX targets a sibling ``<span class="field-error">`` and swaps in
        either an empty body (valid), a ``<span class="field-error-msg">``
        (invalid – flips ``aria-invalid`` and gates Save), or a
        ``<span class="field-note-msg">`` (advisory – does not gate).
        """
        from openfollow.web.validation import FIELD_RULES, needs_cfg, note, validate

        response.content_type = "text/html; charset=utf-8"
        rules = FIELD_RULES.get(section)
        if rules is None or field_name not in rules:
            abort(404, "Unknown field")
            return ""  # pragma: no cover – abort() raises before we reach here
        raw = request.query.get(field_name, "")
        # In imperial mode a length/speed field is typed as an imperial
        # string ("5 ft 6 in"). Convert to canonical metric before the
        # bounds check so valid imperial input isn't flagged "Must be a
        # number" and ``lo``/``hi`` are compared in metres.
        if _unit_field_kind(section, field_name) is not None:
            unit_system = UnitSystem(_request_scoped_config().ui.unit_system)
            raw, unit_err = _coerce_unit_input(
                section,
                field_name,
                raw,
                unit_system,
            )
            if unit_err is not None:
                return (
                    f'<span class="field-error-msg" role="alert" '
                    f'aria-live="assertive">'
                    f"{html_mod.escape(unit_err, quote=True)}</span>"
                )
        # Only the URL-allowlist check reads the TOML config; skip the
        # per-keystroke parse for every other rule (see
        # ``validation.needs_cfg``). The request-scoped cache reuses the
        # parse from ``_check_auth``'s PIN read so blur validation doesn't
        # duplicate disk I/O.
        cfg = _request_scoped_config() if needs_cfg(rules[field_name]) else None
        err = validate(section, field_name, raw, cfg=cfg)
        if err is not None:
            # Errors get an assertive live region so screen readers
            # interrupt. ``role`` / ``aria-live`` ride on the swapped-in span
            # only – the parent ``span.field-error`` carries no live-region
            # role, so advisory notes (the ``role="status"`` branch below)
            # aren't announced as alerts.
            return (
                f'<span class="field-error-msg" role="alert" aria-live="assertive">'
                f"{html_mod.escape(err, quote=True)}</span>"
            )
        # Cross-subsystem conflict check for OSC binding triggers: an input
        # claimed by the controller / keyboard config can't be re-assigned to
        # an OSC binding (and vice versa). Same-owner OSC rows on the same
        # input do NOT conflict (multiple bindings can listen and fire
        # independently); ``conflicts_for(..., owner=X)`` excludes ``X``.
        # Strip ``raw`` before the guard and lookup: ``validate()`` treats
        # whitespace-only input as empty, and the registry holds no padded
        # ids, so ``trigger.key=   `` would otherwise produce a bogus check.
        stripped = raw.strip() if isinstance(raw, str) else raw
        if (
            section == "osc_binding"
            and stripped
            and field_name
            in (
                "trigger.key",
                "trigger.button",
            )
        ):
            kind = "key" if field_name == "trigger.key" else "controller_button"
            owner = "osc:hotkey" if field_name == "trigger.key" else "osc:controller_button"
            owners = server.get_osc_binding_conflicts(kind, stripped, owner)
            if owners:
                # Hard error: surfaces as ``<span class="field-error-msg">``
                # so the ``aria-invalid`` plumbing and Save-gate kick in.
                msg = (
                    f"Already claimed by {', '.join(owners)}. Pick a different {'key' if kind == 'key' else 'button'}."
                )
                return (
                    f'<span class="field-error-msg" role="alert" aria-live="assertive">'
                    f"{html_mod.escape(msg, quote=True)}</span>"
                )
        # Cross-field unresolved-placeholder check for OSC bindings, at blur
        # time on the message field – surfaces the same condition the partial
        # paints as red pills. Reads sibling ``markers`` from the form and
        # the registered-marker registry from the live config.
        if section == "osc_binding" and field_name in ("osc_message", "markers"):
            err_msg = _osc_binding_unresolved_blur_error(
                request.query,
                _request_scoped_config(),
            )
            if err_msg is not None:
                # Unresolved-placeholder rows are intentionally save-able (the
                # POST handler coerces ``enabled=False`` until deps resolve).
                # Use ``field-warn-msg`` (not ``field-error-msg``) so the
                # shared ``refreshFormGate`` doesn't flip ``aria-invalid`` and
                # disable Save.
                return (
                    f'<span class="field-warn-msg" role="status" aria-live="polite">'
                    f"{html_mod.escape(err_msg, quote=True)}</span>"
                )
        # ``request.query`` is a MultiDict; flatten siblings into a plain
        # dict so ``note()`` can read cross-field context (e.g. ``min_speed``
        # for the ``max_speed`` advisory) without leaking the field being
        # validated as its own context entry.
        context = {k: request.query.get(k, "") for k in request.query if k != field_name}
        advisory = note(section, field_name, raw, context=context)
        if advisory is not None:
            # Notes are polite: queued behind the user's current reading
            # context rather than interrupting. ``role="status"`` is the
            # WAI-ARIA alias for an ``aria-live="polite"`` region.
            return (
                f'<span class="field-note-msg" role="status" aria-live="polite">'
                f"{html_mod.escape(advisory, quote=True)}</span>"
            )
        return ""

    @app.get("/network/interfaces/by_name")
    def network_interfaces_by_name() -> Any:
        """Return iface-keyed network interface options.

        Option ``value`` is the iface name (the stored value); the
        label is ``<iface> – <current IPv4>`` so the operator can
        still tell at a glance which network each one is on. A
        configured iface that's currently down is appended with
        ``(not available)`` so the operator sees their selection
        instead of having it silently drop out of the list.

        Shared by every interface picker (PSN, OTP). ``?current=<iface>``
        selects that iface in the rendered list; without it the route
        defaults to ``psn_source_iface`` so the PSN picker keeps working
        with a plain ``hx-get``.
        """
        from openfollow.net_utils import list_iface_ipv4

        cfg = _request_scoped_config()
        # ``?current=`` present (even empty) overrides; absent → PSN default. An
        # empty OTP pin must stay empty (auto-detect), not fall back to PSN's.
        current = request.query.current if "current" in request.query else cfg.psn_source_iface
        ifaces = list_iface_ipv4()
        options = ['<option value="">-- Auto-detect --</option>']
        names = {name for name, _ in ifaces}
        for name, ip in ifaces:
            selected = "selected" if name == current else ""
            label = html_mod.escape(f"{name} – {ip}", quote=True)
            options.append(f'<option value="{html_mod.escape(name, quote=True)}" {selected}>{label}</option>')
        if current and current not in names:
            safe = html_mod.escape(current, quote=True)
            options.append(f'<option value="{safe}" selected>{safe} (not available)</option>')
        return "\n".join(options)

    # NDI source discovery is served by the plugin's own
    # ``/video-input/ndi/sources`` route (NdiInput.handle_discover_sources),
    # consistent with every other input plugin. The legacy hardcoded
    # ``/ndi/sources`` route was removed to converge on one implementation.

    # --- JSON API Routes ---

    @app.get("/api/info")
    def api_info() -> Any:
        """Return server info."""
        response.content_type = "application/json"
        return json.dumps(
            {
                "name": server.system_name,
                "ip": server.local_ip,
                "port": server.port,
                "version": openfollow.__version__,
            }
        )

    @app.get("/api/stats")
    def api_stats() -> Any:
        """Return live runtime statistics."""
        response.content_type = "application/json"
        return json.dumps(server.get_runtime_stats())

    @app.get("/api/video/snapshot")
    def api_video_snapshot() -> Any:
        """Return latest JPEG video preview snapshot."""
        jpeg = server.get_preview_snapshot()
        if jpeg is None:
            abort(503, "No preview available")
        response.content_type = "image/jpeg"
        response.set_header("Cache-Control", "no-store")
        return jpeg

    @app.get("/api/update-status")
    def api_update_status() -> Any:
        """Return state of the web-triggered update workflow."""
        response.content_type = "application/json"
        return json.dumps(server.get_update_status())

    # ----- privilege password prompt --------------------------------
    #
    # The broker parks any subsystem that needs ``sudo`` without a
    # NOPASSWD grant on the WebCommandQueue's privilege-password slot.
    # This web modal is the sole consumer – operators always elevate
    # through the browser.

    @app.get("/system/privilege/password/modal")
    def system_privilege_password_modal() -> Any:
        """HTMX partial: render the privilege-password modal or an
        empty div when no prompt is in flight.

        ``base.tpl`` polls this every 3 s. When ``pending`` is set, the
        modal renders with the operator-facing reason; when not, the
        partial is empty (which removes any stale modal from the DOM).

        Auth: ``_check_auth`` exempts this path from the global PIN
        gate so the unauthenticated ``/login`` page can poll without
        triggering a redirect loop. We re-check auth locally and return
        the empty partial when unauthenticated – pending-prompt state
        is privileged info and shouldn't leak to a logged-out browser.
        """
        cfg = _request_scoped_config()
        if cfg.web_pin and request.get_cookie(_AUTH_COOKIE, secret=cfg.web_pin) != "ok":
            return template("partials/privilege_password", pending=None)
        pending = server.pending_privilege_password_request()
        return template(
            "partials/privilege_password",
            pending=pending,
        )

    @app.post("/system/privilege/password")
    def system_privilege_password_submit() -> Any:
        """Hand the operator-typed device password to the broker.

        Guarded by ``pending_privilege_password_request`` – an
        un-prompted submission is a no-op so a stale password can't
        sit in the queue waiting for the next worker. Empty submit is
        treated as cancel so the worker wakes immediately rather than
        waiting out the timeout.
        """
        pending = server.pending_privilege_password_request()
        if pending is not None:
            password = request.forms.get("password", "")
            if password:
                server.submit_privilege_password(password)
            else:
                server.cancel_privilege_password()
        return template("partials/privilege_password", pending=None)

    @app.post("/system/privilege/password/cancel")
    def system_privilege_password_cancel() -> Any:
        """Operator dismissed the modal. Wake the parked worker so it
        surfaces a clean cancellation."""
        if server.pending_privilege_password_request() is not None:
            server.cancel_privilege_password()
        return template("partials/privilege_password", pending=None)

    @app.post("/api/restart")
    def api_restart() -> Any:
        """Trigger an application restart."""
        response.content_type = "application/json"
        server.request_restart()
        return json.dumps({"success": True})

    @app.get("/api/peers")
    def api_peers() -> Any:
        """Return list of discovered peers."""
        response.content_type = "application/json"
        peers = server.get_peers()
        local = server.get_local_peer_info()
        return json.dumps(
            {
                "local": {
                    "name": local.name,
                    "ip": local.ip,
                    "port": local.web_port,
                    "version": local.version,
                },
                "peers": [
                    {
                        "name": p.name,
                        "ip": p.ip,
                        "port": p.web_port,
                        "version": p.version,
                        "online": p.is_online,
                    }
                    for p in peers
                ],
            }
        )

    @app.get("/api/config")
    def api_get_config() -> Any:
        """Return full config as JSON."""
        response.content_type = "application/json"
        cfg = _request_scoped_config()
        return json.dumps(_config_dict_redacted(cfg))

    # --- Config Export / Import (before wildcard <section> routes) ---

    @app.get("/api/config/export")
    def api_export_config() -> Any:
        """Export the full config as a downloadable ``.openfollowsettings`` file.

        The payload is still JSON (content-type ``application/json``); only
        the download filename uses the custom extension so exported settings
        are recognisable and round-trip through the import picker, which
        filters on ``.openfollowsettings``. The filename is just the
        sanitised system name plus the extension – no ``openfollow-``
        prefix, since "openfollow" already lives in the extension.
        ``psn_system_name`` is guaranteed non-empty (``AppConfig``
        post-init falls back to the default) and the sanitiser maps each
        disallowed character to ``-`` rather than dropping it, so
        ``safe_name`` is never empty (no bare ``.openfollowsettings``).
        """
        cfg = _request_scoped_config()
        safe_name = re.sub(r"[^A-Za-z0-9_-]", "-", cfg.psn_system_name)
        response.content_type = "application/json"
        response.set_header(
            "Content-Disposition",
            f'attachment; filename="{safe_name}.openfollowsettings"',
        )
        return json.dumps(_config_dict_redacted(cfg), indent=2)

    @app.post("/api/config/import")
    def api_import_config() -> Any:
        """Import a config from JSON, preserving the device IP address.

        Every section in the imported payload applies live via the
        hot-reload path. The ``confirm_restart`` / ``skip_restart`` query
        params are still accepted (older peers send them) but no longer
        change the outcome. If a future section regains a restart-only path,
        this endpoint must consult ``_import_needs_restart`` before reporting
        ``needs_restart`` again.
        """
        response.content_type = "application/json"

        data = _load_json_body()
        if data is None:
            return json.dumps({"error": "Invalid JSON"})
        if not isinstance(data, dict):
            response.status = 400
            return json.dumps({"error": "Expected a JSON object"})

        with _config_write_lock:
            current = load_config(server.config_path)
            full_cfg = _apply_import_data(current, data)
            save_config(full_cfg, server.config_path)
            return json.dumps({"success": True, "needs_restart": False})

    @app.post("/api/config/broadcast-all")
    def api_broadcast_all() -> Any:
        """Broadcast full config to all peers via their import endpoint."""
        response.content_type = "application/json"
        cfg = load_config(server.config_path)
        # web_pin/web_port are device-local and stripped on receive; also drop
        # the PIN here so the credential never travels in the broadcast body
        # (peer auth still carries it in the signed header via ``pin=``).
        # OSC destinations / transmitters / zones export via file but are never
        # real-time shared – strip them from the broadcast body.
        cfg_data = _strip_broadcast_excluded(_config_dict_redacted(cfg))
        peers = server.get_peers()
        pin = cfg.web_pin
        results = _broadcast_to_peers(
            peers,
            lambda peer: _send_config_import_to_peer(
                peer.ip,
                peer.web_port,
                cfg_data,
                pin=pin,
                expected_port=server.display_port,
            ),
            overall_timeout=_BROADCAST_IMPORT_TIMEOUT_S,
        )
        return json.dumps(
            {
                "success": True,
                "peer_results": results,
            }
        )

    # --- Per-section JSON API (wildcard routes) ---

    @app.get("/api/config/<section>")
    def api_get_section(section: str) -> Any:
        """Return a config section as JSON."""
        response.content_type = "application/json"
        cfg = _request_scoped_config()
        data = get_section_data(cfg, section)
        if data is None:
            response.status = 404
            return json.dumps({"error": "Unknown section"})
        return json.dumps(data)

    @app.post("/api/config/<section>")
    def api_update_section(section: str) -> Any:
        """Update a config section from JSON.

        Strips device-local fields before apply: this endpoint receives peer
        broadcasts (and any external API client), neither of which should
        overwrite this device's ``psn_source_iface`` pin. The local
        ``/section/psn`` form save uses a different route.
        """
        response.content_type = "application/json"

        data = _load_json_body()
        if data is None:
            return json.dumps({"error": "Invalid JSON"})

        with _config_write_lock:
            cfg = load_config(server.config_path)
            scrubbed = strip_device_local_fields(section, data)
            if not apply_section_data(cfg, section, scrubbed):
                response.status = 404
                return json.dumps({"error": "Unknown section"})

            save_config(cfg, server.config_path)
        return json.dumps({"success": True})

    @app.post("/api/config/<section>/broadcast")
    def api_broadcast_section(section: str) -> Any:
        """Broadcast a config section to all peers.

        The peer-forward payload is scrubbed of device-local fields
        (``psn_source_iface``) so one box's interface pin is never copied to
        other devices. Local-apply uses the UNSCRUBBED payload (the
        broadcaster IS saving their own PSN form). The peer-receive endpoint
        also filters as defence-in-depth against out-of-date senders.
        """
        response.content_type = "application/json"

        # OSC destinations / transmitters / zones travel via the config file
        # only – never real-time to peers. Refuse a crafted section-broadcast.
        if section in _BROADCAST_EXCLUDED_SECTIONS:
            response.status = 403
            return json.dumps({"error": "Section is not shareable between stations"})

        data = _load_json_body()
        if data is None:
            return json.dumps({"error": "Invalid JSON"})

        with _config_write_lock:
            cfg = load_config(server.config_path)
            if not apply_section_data(cfg, section, data):
                response.status = 404
                return json.dumps({"error": "Unknown section"})
            save_config(cfg, server.config_path)

        peers = server.get_peers()
        pin = cfg.web_pin
        peer_data = strip_device_local_fields(section, data)
        results = _broadcast_to_peers(
            peers,
            lambda peer: _send_config_to_peer(
                peer.ip,
                peer.web_port,
                section,
                peer_data,
                pin=pin,
                expected_port=server.display_port,
            ),
            overall_timeout=_BROADCAST_SECTION_TIMEOUT_S,
        )

        return json.dumps(
            {
                "success": True,
                "local_updated": True,
                "peer_results": results,
            }
        )

    # -- Trigger zone CRUD API ----------------------------------------

    @app.get("/api/zones")
    def api_list_zones() -> Any:
        """Return zones + live occupancy + marker positions for the editor."""
        response.content_type = "application/json"
        cfg = _request_scoped_config()
        tz = cfg.trigger_zones
        states = {idx: (occ, count) for idx, occ, count in server.get_zone_states()}
        zones_out = []
        for idx, z in enumerate(tz.zones):
            occ, count = states.get(idx, (False, 0))
            # Per-zone live state. The provider hook is None when the engine
            # isn't running yet (headless tests, startup race) – fall back to
            # a zero-state dict so the panel always has predictable shape.
            diag = (
                server.get_zone_diagnostics(idx)
                if hasattr(
                    server,
                    "get_zone_diagnostics",
                )
                else None
            )
            if diag is None:
                diag = {
                    "is_occupied": occ,
                    "count": count,
                    "occupants": [],
                    "last_event_time": 0.0,
                    "last_event_address": "",
                }
            zones_out.append(
                {
                    "index": idx,
                    "name": z.name,
                    "color": z.color,
                    "trigger_source": z.trigger_source,
                    "triggered_by": list(z.triggered_by),
                    "vertices": z.vertices,
                    "osc_address_first_entry": z.osc_address_first_entry,
                    "osc_address_additional_entry": z.osc_address_additional_entry,
                    "osc_address_partial_exit": z.osc_address_partial_exit,
                    "osc_address_final_exit": z.osc_address_final_exit,
                    "destination_id": z.destination_id,
                    "enabled": z.enabled,
                    "is_occupied": occ,
                    "occupant_count": count,
                    "diagnostics": diag,
                }
            )
        return json.dumps(
            {
                "globals": {
                    "enabled": tz.enabled,
                    "show_overlay": tz.show_overlay,
                    "eval_fps": tz.eval_fps,
                    "debounce_ms": tz.debounce_ms,
                    "hysteresis": tz.hysteresis,
                },
                "grid": {
                    "width": cfg.grid.width,
                    "depth": cfg.grid.depth,
                    "spacing": cfg.grid.spacing,
                    "x_offset": cfg.grid.x_offset,
                    "y_offset": cfg.grid.y_offset,
                },
                "zones": zones_out,
                "markers": [{"id": tid, "x": x, "y": y} for tid, x, y in server.get_marker_positions()],
                # Shared destinations travel with the poll so the editor's
                # dropdown follows add/rename/delete without a full reload.
                "destinations": osc_destinations_client_list(cfg),
            }
        )

    @app.post("/api/zones")
    def api_create_zone() -> Any:
        """Append a new zone from JSON body."""
        response.content_type = "application/json"
        data = _load_json_body()
        if data is None:
            return json.dumps({"error": "Invalid JSON"})
        if not isinstance(data, dict):
            response.status = 400
            return json.dumps({"error": "Expected a JSON object"})
        with _config_write_lock:
            cfg = load_config(server.config_path)
            zone = TriggerZoneConfig()
            _apply_zone_fields(zone, data)
            cfg.trigger_zones.zones.append(zone)
            save_config(cfg, server.config_path)
        return json.dumps({"success": True, "index": len(cfg.trigger_zones.zones) - 1})

    @app.put("/api/zones/<index:int>")
    def api_update_zone(index: int) -> Any:
        """Replace fields on zone at ``index`` from JSON body."""
        response.content_type = "application/json"
        data = _load_json_body()
        if data is None:
            return json.dumps({"error": "Invalid JSON"})
        if not isinstance(data, dict):
            response.status = 400
            return json.dumps({"error": "Expected a JSON object"})
        with _config_write_lock:
            cfg = load_config(server.config_path)
            zones = cfg.trigger_zones.zones
            if index < 0 or index >= len(zones):
                response.status = 404
                return json.dumps({"error": "Zone index out of range"})
            _apply_zone_fields(zones[index], data)
            save_config(cfg, server.config_path)
        return json.dumps({"success": True})

    # Clone a zone in place: the clone is appended at the end of the list
    # and its ``name`` gets a ``" (copy)"`` suffix.
    @app.post("/api/zones/<index:int>/duplicate")
    def api_duplicate_zone(index: int) -> Any:
        response.content_type = "application/json"
        with _config_write_lock:
            cfg = load_config(server.config_path)
            zones = cfg.trigger_zones.zones
            if index < 0 or index >= len(zones):
                response.status = 404
                return json.dumps({"error": "Zone index out of range"})
            src = zones[index]
            clone = TriggerZoneConfig(
                name=(src.name + " (copy)") if src.name else "(copy)",
                vertices=[list(v) for v in src.vertices],
                color=src.color,
                trigger_source=src.trigger_source,
                triggered_by=list(src.triggered_by),
                osc_address_first_entry=src.osc_address_first_entry,
                osc_address_additional_entry=src.osc_address_additional_entry,
                osc_address_partial_exit=src.osc_address_partial_exit,
                osc_address_final_exit=src.osc_address_final_exit,
                destination_id=src.destination_id,
                enabled=src.enabled,
            )
            zones.append(clone)
            save_config(cfg, server.config_path)
        return json.dumps({"success": True, "index": len(zones) - 1})

    # Test send. ``which`` picks one of the four zone OSC fields and
    # forwards it through the shared ``OscService.send`` path the engine
    # uses for real transitions. Empty / unconfigured fields return a
    # ``skipped`` response (mirrors the engine's "no address ⇒ no send").
    _ZONE_TEST_FIELDS = {
        "first": "osc_address_first_entry",
        "additional": "osc_address_additional_entry",
        "partial": "osc_address_partial_exit",
        "final": "osc_address_final_exit",
    }

    @app.post("/api/zones/<index:int>/test_send")
    def api_zone_test_send(index: int) -> Any:
        response.content_type = "application/json"
        which = (request.query.get("which", "") or "").strip()
        if which not in _ZONE_TEST_FIELDS:
            response.status = 400
            return json.dumps(
                {
                    "error": "which must be one of: first, additional, partial, final",
                }
            )
        result = server.trigger_zone_test_send(index, which)
        if not result:
            response.status = 503
            return json.dumps(
                {
                    "error": "Zone engine not running – cannot send test message",
                }
            )
        # The provider returns ``{"error": "..."}`` for out-of-range index
        # and unclosed-quote input; both need a 4xx, not 200. Out-of-range
        # index → 404; any other ``error`` is a payload-shape problem → 400.
        # ``success`` and ``skipped`` stay 200.
        if "error" in result:
            err = result["error"]
            if "out of range" in err.lower():
                response.status = 404
            else:
                response.status = 400
        return json.dumps(result)

    @app.delete("/api/zones/<index:int>")
    def api_delete_zone(index: int) -> Any:
        """Remove zone at ``index``."""
        response.content_type = "application/json"
        with _config_write_lock:
            cfg = load_config(server.config_path)
            zones = cfg.trigger_zones.zones
            if index < 0 or index >= len(zones):
                response.status = 404
                return json.dumps({"error": "Zone index out of range"})
            del zones[index]
            save_config(cfg, server.config_path)
        return json.dumps({"success": True})

    # -- Wizard routes -------------------------------------------------

    @app.get("/wizard")
    def wizard_page() -> Any:
        """Setup wizard page."""
        config = _request_scoped_config()
        input_data = _build_input_template_data(config)
        return template("wizard", config=config, **input_data)

    @app.get("/api/video/snapshot/full")
    def api_video_snapshot_full() -> Any:
        """Return a full-resolution JPEG snapshot for the wizard."""
        jpeg = server.get_full_snapshot()
        if jpeg is None:
            abort(503, "No snapshot available")
        response.content_type = "image/jpeg"
        response.set_header("Cache-Control", "no-store")
        return jpeg

    @app.post("/api/wizard/project")
    def api_wizard_project() -> Any:
        """Project grid corners + reference point to screen coordinates."""
        response.content_type = "application/json"
        data = _load_json_body()
        if data is None:
            return json.dumps({"error": "Invalid JSON"})

        try:
            cam = data["camera"]
            grid = data["grid"]
            img_w = float(data["image_width"])
            img_h = float(data["image_height"])
            _require_wizard_canvas(img_w, img_h)
            params = _wizard_camera_params(cam)
            w = float(grid["width"])
            d = float(grid["depth"])
            ox = float(grid.get("x_offset") or 0)
            oy = float(grid.get("y_offset") or 0)
            oz = float(grid.get("z_offset") or 0)
        except (KeyError, TypeError, ValueError) as exc:
            response.status = 400
            return json.dumps({"error": str(exc)})

        import numpy as np

        from openfollow.scene.solver import apply_overlay_distortion, project_points

        k1, k2 = _wizard_lens_coeffs(cam)
        hw, hd = w / 2.0, d / 2.0

        # PSN +X is stage left, so the stage-left corners (DSL, USL) sit at
        # +hw and the stage-right corners (DSR, USR) at -hw. On a front-of-house
        # camera that places stage left on the right of the image (audience
        # right) and stage right on the left (audience left).
        corners_psn = np.array(
            [
                [ox + hw, oy - hd, oz],  # DSL (downstage stage-left)
                [ox - hw, oy - hd, oz],  # DSR (downstage stage-right)
                [ox - hw, oy + hd, oz],  # USR (upstage stage-right)
                [ox + hw, oy + hd, oz],  # USL (upstage stage-left)
            ],
            dtype=np.float64,
        )

        # Reference point at ground level (where the physical mark is)
        ref_psn = np.array([[0, 0, 0]], dtype=np.float64)

        # Bow the projected overlay to match a fisheye / wide-angle snapshot so
        # the corner-pinning preview lines up with the distorted video.
        screen_corners = apply_overlay_distortion(
            project_points(params, corners_psn, img_w, img_h), img_w, img_h, k1, k2
        )
        screen_ref = apply_overlay_distortion(project_points(params, ref_psn, img_w, img_h), img_w, img_h, k1, k2)
        projected = [screen_corners, screen_ref]

        result = {
            "corners": {
                "DSL": screen_corners[0].tolist(),
                "DSR": screen_corners[1].tolist(),
                "USR": screen_corners[2].tolist(),
                "USL": screen_corners[3].tolist(),
            },
            "reference": screen_ref[0].tolist(),
        }

        # Bowed boundary outline so the wizard quad curves exactly like the HUD
        # grid (which subdivides each straight world edge). Same subdivision
        # count as the renderer keeps the two curves identical. Best-effort: a
        # behind-camera edge point omits the outline and the client falls back to
        # the straight corner quad.
        from openfollow.runtime.overlay_draw_scene import _DISTORTION_SUBDIVISIONS

        n_sub = _DISTORTION_SUBDIVISIONS if (k1 or k2) else 1
        loop = np.vstack([corners_psn, corners_psn[0:1]])  # close the quad
        edge_ts = np.linspace(0.0, 1.0, n_sub + 1)[:-1]  # drop shared join vertex
        outline_world = np.array(
            [loop[i] + t * (loop[i + 1] - loop[i]) for i in range(4) for t in edge_ts],
            dtype=np.float64,
        )
        outline_screen = apply_overlay_distortion(
            project_points(params, outline_world, img_w, img_h), img_w, img_h, k1, k2
        )
        if np.isfinite(outline_screen).all():
            result["outline"] = outline_screen.tolist()

        if abs(oz) > 1e-6:
            # Elevated point at grid height (top of z-offset line)
            ref_elevated = np.array([[0, 0, oz]], dtype=np.float64)
            screen_elevated = apply_overlay_distortion(
                project_points(params, ref_elevated, img_w, img_h), img_w, img_h, k1, k2
            )
            projected.append(screen_elevated)
            result["reference_elevated"] = screen_elevated[0].tolist()
            result["z_offset"] = oz

        # A grid corner at or behind the camera plane projects to NaN/Inf. The
        # default json.dumps emits literal `NaN` tokens – invalid JSON the
        # browser's JSON.parse rejects, which silently drops the wizard overlay
        # (no corners, no error). Fail loudly with actionable guidance instead.
        if not all(np.isfinite(arr).all() for arr in projected):
            response.status = 400
            return json.dumps(
                {
                    "error": (
                        "Camera cannot see all grid corners with these parameters. "
                        "Adjust the camera position and angle in Step 4 (Camera Position)."
                    )
                }
            )

        return json.dumps(result)

    @app.post("/api/wizard/unproject")
    def api_wizard_unproject() -> Any:
        """Unproject screen points to a world plane (for coarse calibration)."""
        response.content_type = "application/json"
        data = _load_json_body()
        if data is None:
            return json.dumps({"error": "Invalid JSON"})

        import numpy as np

        from openfollow.scene.solver import invert_overlay_distortion, unproject_to_plane

        try:
            cam = data["camera"]
            screen_points = data["screen_points"]
            img_w = float(data["image_width"])
            img_h = float(data["image_height"])
            _require_wizard_canvas(img_w, img_h)
            plane_z = float(data.get("plane_z", 0.0))
            params = _wizard_camera_params(cam)
            k1, k2 = _wizard_lens_coeffs(cam)
            if not isinstance(screen_points, list) or not screen_points:
                raise ValueError("screen_points must be a non-empty list")
            for p in screen_points:
                if not isinstance(p, (list, tuple)) or len(p) != 2:
                    raise ValueError("each screen_point must be [x, y]")
            # Coerce coordinates *inside* the try/except so non-numeric values
            # (e.g. ["a", "b"]) surface as 400, not 500 from the np.array below.
            pts = np.array(
                [[float(p[0]), float(p[1])] for p in screen_points],
                dtype=np.float64,
            )
        except (KeyError, TypeError, ValueError) as exc:
            response.status = 400
            return json.dumps({"error": str(exc)})

        # The clicked points sit on the distorted snapshot; undistort them to the
        # pinhole frame before unprojecting (identity when no distortion is set).
        pts = invert_overlay_distortion(pts, img_w, img_h, k1, k2)
        world = unproject_to_plane(params, pts, img_w, img_h, plane_z)

        if len(pts) == 2 and not np.any(np.isnan(world)):
            delta = world[1] - world[0]
            return json.dumps(
                {
                    "world_points": world.tolist(),
                    "delta": {"x": float(delta[0]), "y": float(delta[1])},
                }
            )

        return json.dumps({"world_points": world.tolist()})

    @app.post("/api/wizard/solve")
    def api_wizard_solve() -> Any:
        """Run DLT camera solve from 4 corner correspondences."""
        response.content_type = "application/json"
        data = _load_json_body()
        if data is None:
            return json.dumps({"error": "Invalid JSON"})

        try:
            world_corners = data["world_corners"]
            screen_corners = data["screen_corners"]
            img_w = float(data["image_width"])
            img_h = float(data["image_height"])
            _require_wizard_canvas(img_w, img_h)
            if not isinstance(world_corners, list) or len(world_corners) != 4:
                raise ValueError("world_corners must be a list of exactly 4 points")
            if not isinstance(screen_corners, list) or len(screen_corners) != 4:
                raise ValueError("screen_corners must be a list of exactly 4 points")
            for p in world_corners:
                if not isinstance(p, (list, tuple)) or len(p) != 3:
                    raise ValueError("each world_corner must be [x, y, z]")
                # Coerce + reject non-finite here so numpy's solve can't 500 later.
                if not all(math.isfinite(float(c)) for c in p):
                    raise ValueError("world_corner coords must be finite numbers")
            for p in screen_corners:
                if not isinstance(p, (list, tuple)) or len(p) != 2:
                    raise ValueError("each screen_corner must be [x, y]")
                if not all(math.isfinite(float(c)) for c in p):
                    raise ValueError("screen_corner coords must be finite numbers")
        except (KeyError, TypeError, ValueError) as exc:
            response.status = 400
            return json.dumps({"error": str(exc)})

        import numpy as np

        from openfollow.scene.solver import (
            apply_overlay_distortion,
            invert_overlay_distortion,
            project_points,
            solve_camera_dlt,
        )

        k1, k2 = _wizard_lens_coeffs(data.get("camera"))

        # The corners were pinned on the distorted snapshot. Undistort them to the
        # pinhole frame so the pinhole DLT solve isn't biased by the lens curve.
        screen_arr = np.array(screen_corners, dtype=np.float64)
        screen_undistorted = invert_overlay_distortion(screen_arr, img_w, img_h, k1, k2)

        world_tuples = [tuple(p) for p in world_corners]
        screen_tuples = [(float(p[0]), float(p[1])) for p in screen_undistorted]

        result = solve_camera_dlt(world_tuples, screen_tuples, img_w, img_h)
        if result is None:
            response.status = 422
            return json.dumps({"error": "Invalid perspective – adjust corners"})

        # Reproject corners with solved camera for snapping
        params = np.array(
            [
                result.pos_x,
                result.pos_y,
                result.pos_z,
                result.pitch,
                result.yaw,
                result.roll,
                result.fov,
            ],
            dtype=np.float64,
        )
        world_arr = np.array(world_corners, dtype=np.float64)
        # Re-distort the reprojected corners back into the snapshot frame so the
        # snap feedback lands where the operator pinned (on the distorted video).
        reprojected = apply_overlay_distortion(project_points(params, world_arr, img_w, img_h), img_w, img_h, k1, k2)

        return json.dumps(
            {
                "camera": {
                    "pos_x": result.pos_x,
                    "pos_y": result.pos_y,
                    "pos_z": result.pos_z,
                    "pitch": result.pitch,
                    "yaw": result.yaw,
                    "roll": result.roll,
                    "fov": result.fov,
                },
                "reprojected_corners": reprojected.tolist(),
            }
        )

    # -- Marker catalog (shared id/name/color + per-station selection) ----

    _register_marker_catalog_routes(app, server)

    # -- Video input plugin routes ----------------------------------

    _register_input_plugin_routes(app, server)
