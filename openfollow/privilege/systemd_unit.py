# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Self-install systemd unit: render, verify, install, enable (no --now)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from openfollow.privilege.broker import PrivilegeBroker, PrivilegeError
from openfollow.privilege.capabilities import (
    DEVICE_INSTALL_SYSTEMD_UNIT,
    DEVICE_SYSTEMD_ANALYZE,
    SERVICE_DAEMON_RELOAD,
    SERVICE_ENABLE,
    SYSTEMD_UNIT_TMP_PREFIX,
)
from openfollow.privilege.drop_in import _is_safe_user, current_device_user

logger = logging.getLogger(__name__)


DEFAULT_SERVICE_NAME = "openfollow"
SYSTEM_UNIT_DIR = Path("/etc/systemd/system")

# Control characters (newline / CR / tab / NUL / …) rejected before a value
# reaches the root-installed unit body. ``current_device_user``'s ``.strip()``
# only trims surrounding whitespace, so an embedded newline survives and would
# inject an extra root-run directive (e.g. a second ``ExecStartPre=``);
# ``systemd-analyze verify`` doesn't catch a syntactically-valid injected line.
_CONTROL_CHARS = frozenset(chr(c) for c in range(0x20)) | {chr(0x7F)}


def _reject_control_chars(label: str, value: str) -> None:
    if any(ch in _CONTROL_CHARS for ch in value):
        raise ValueError(f"Refusing to render systemd unit: {label} contains a control character")


# A quote breaks out of the ExecStart ``/bin/sh -c '...'`` quoting (and
# systemd's own quoting of the bare directives), injecting extra argv.
_QUOTE_CHARS = frozenset("'\"")


def _reject_quote_chars(label: str, value: str) -> None:
    if any(ch in _QUOTE_CHARS for ch in value):
        raise ValueError(f"Refusing to render systemd unit: {label} contains a quote character")


# ``poetry_bin`` lands *unquoted* inside the ExecStart ``/bin/sh -c`` command
# (systemd strips the surrounding single quotes before the shell sees the
# string), so a shell metacharacter – whitespace, ``;`` ``&`` ``|`` ``$``
# backtick, a glob, … – would split the command or inject another. Rejecting
# quotes alone doesn't close this: the shell never sees a quote to break out of.
_SHELL_METACHARACTERS = frozenset(" ;&|<>()$`*?[]{}~#!\\")


def _reject_shell_metacharacters(label: str, value: str) -> None:
    if any(ch in _SHELL_METACHARACTERS for ch in value):
        raise ValueError(f"Refusing to render systemd unit: {label} contains a shell metacharacter")


def render_service_unit(
    *,
    user: str,
    uid: int,
    repo_dir: Path,
    poetry_bin: Path,
    service_name: str = DEFAULT_SERVICE_NAME,
) -> str:
    """Build systemd unit content (absolute paths, no Ansible defaults).

    Values land in a root:root unit: ``user`` must pass :func:`_is_safe_user`;
    ``repo_dir`` / ``poetry_bin`` / ``service_name`` may carry no control or
    quote character. ``poetry_bin`` is additionally rejected for any shell
    metacharacter – it lands unquoted in the ``/bin/sh -c`` command (systemd
    strips the surrounding quotes), so quote-rejection alone wouldn't stop a
    ``;`` / ``$`` / whitespace injection.
    """
    if not _is_safe_user(user):
        raise ValueError(f"Refusing to render systemd unit for unsafe user name: {user!r}")
    for label, value in (("repo_dir", str(repo_dir)), ("poetry_bin", str(poetry_bin)), ("service_name", service_name)):
        _reject_control_chars(label, value)
        _reject_quote_chars(label, value)
    _reject_shell_metacharacters("poetry_bin", str(poetry_bin))
    # Foreground the app (no `exec`), then tear kanshi down, preserving the
    # app's exit code so the kill can't mask a crash. Built outside the unit
    # template to keep the ExecStart line within the line-length limit.
    cage_session = f'kanshi & {poetry_bin} run python -m openfollow.main; rc=$?; kill "$!" 2>/dev/null; exit $rc'
    return f"""[Unit]
Description=OpenFollow (NDI + 3D tracker overlay)
After=network.target seatd.service user@{uid}.service systemd-udev-settle.service
Wants=network.target user@{uid}.service systemd-udev-settle.service

[Service]
Type=simple
User={user}
WorkingDirectory={repo_dir}
PermissionsStartOnly=true
ExecStartPre=/bin/bash -c 'until [ -d /run/user/{uid} ]; do sleep 0.5; done'
ExecStartPre=/bin/sleep 2
ExecStart=/usr/bin/cage -- /bin/sh -c '{cage_session}'
Restart=always
RestartSec=5
TimeoutStartSec=60
TimeoutStopSec=15
Environment=XDG_RUNTIME_DIR=/run/user/{uid}
Environment=WLR_BACKENDS=drm,libinput
Environment=WLR_RENDERER=gles2
Environment=GDK_BACKEND=wayland
Environment=GST_PLUGIN_FEATURE_RANK=v4l2slh264dec:257,v4l2slh265dec:257,v4l2h264dec:257,v4l2h265dec:257,openh264dec:1
StandardOutput=journal
StandardError=journal
SyslogIdentifier={service_name}

[Install]
WantedBy=multi-user.target
"""


def is_unit_installed(service_name: str = DEFAULT_SERVICE_NAME) -> bool:
    """Return True when ``/etc/systemd/system/<name>.service`` exists.

    Doesn't check ``systemctl cat`` – a plain file presence check
    matches what the Privileges-page probe needs, and avoids spawning
    a subprocess on every render of the page.
    """
    return (SYSTEM_UNIT_DIR / f"{service_name}.service").exists()


def install_unit(
    broker: PrivilegeBroker,
    *,
    service_name: str = DEFAULT_SERVICE_NAME,
    user: str | None = None,
    repo_dir: Path | None = None,
    poetry_bin: Path | None = None,
    enable: bool = True,
) -> None:
    """Render + validate + install the unit, then ``daemon-reload`` +
    ``enable`` (without ``--now``).

    All arguments default to the running process's view of the world:

    * ``user`` – :func:`current_device_user`.
    * ``repo_dir`` – :func:`os.getcwd`.
    * ``poetry_bin`` – :func:`shutil.which` first, else the canonical
      ``~/.local/bin/poetry``.

    Raises :class:`PrivilegeError` for any failure; on success the unit
    is on disk, ``daemon-reload`` has run, and the unit is enabled but
    **not started** – the operator restarts the foreground app at
    their convenience.
    """
    resolved_user = user or current_device_user()
    resolved_repo = repo_dir or Path(os.getcwd())
    resolved_poetry = poetry_bin or _resolve_poetry_bin()
    uid = os.getuid()

    content = render_service_unit(
        user=resolved_user,
        uid=uid,
        repo_dir=resolved_repo,
        poetry_bin=resolved_poetry,
        service_name=service_name,
    )

    # Temp prefix matches ``SYSTEMD_UNIT_TMP_PREFIX`` so the staged
    # path is covered by the ``DEVICE_INSTALL_SYSTEMD_UNIT`` sudoers
    # rule's ``/tmp/openfollow-*.service`` glob.
    fd, temp_name = tempfile.mkstemp(
        prefix=f"{SYSTEMD_UNIT_TMP_PREFIX}{service_name}-",
        suffix=".service",
        dir="/tmp",  # nosec B108
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        # Keep mkstemp's 0600: root reads it for verify/install regardless
        # of mode, and ``install -m 0644`` sets the destination mode – no
        # need to widen the staged file in /tmp first (mirrors drop_in.py).

        try:
            broker.run(
                DEVICE_SYSTEMD_ANALYZE,
                ["/usr/bin/systemd-analyze", "verify", str(temp_path)],
                reason="Validate the rendered systemd unit",
                timeout=15,
            )
        except PrivilegeError as exc:
            raise PrivilegeError(f"systemd unit validation failed: {exc}. Unit not installed.") from exc

        broker.run(
            DEVICE_INSTALL_SYSTEMD_UNIT,
            [
                "/usr/bin/install",
                "-m",
                "0644",
                "-o",
                "root",
                "-g",
                "root",
                str(temp_path),
                str(SYSTEM_UNIT_DIR / f"{service_name}.service"),
            ],
            reason="Install the validated systemd unit",
            timeout=10,
        )
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover – unusual /tmp ACL
            logger.warning("Could not remove temp unit file %s: %s", temp_path, exc)

    # daemon-reload picks up the new unit; enable adds the WantedBy
    # symlink so it autostarts on the next boot. We deliberately
    # don't pass ``--now`` – the foreground app is already running
    # under ``poetry run``, and starting the unit would race two
    # copies for the framebuffer (and the GStreamer pipeline / video
    # device locks).
    broker.run(
        SERVICE_DAEMON_RELOAD,
        ["/usr/bin/systemctl", "daemon-reload"],
        reason="Reload systemd unit definitions",
        timeout=15,
    )
    if enable:
        broker.run(
            SERVICE_ENABLE,
            ["/usr/bin/systemctl", "enable", service_name],
            reason=f"Enable {service_name}.service at boot",
            timeout=15,
        )


def _resolve_poetry_bin() -> Path:
    """Find ``poetry``. Prefer PATH, else the canonical user-install
    location used by the Ansible playbook (``~/.local/bin/poetry``)."""
    found = shutil.which("poetry")
    if found:
        return Path(found)
    return Path(os.path.expanduser("~/.local/bin/poetry"))


def stop_and_status(
    service_name: str = DEFAULT_SERVICE_NAME,
) -> tuple[bool, str]:
    """Return ``(is_active, status_text)`` for ``service_name``.

    Used by the Privileges page to warn the operator that they have a
    foreground app running under a different launcher than the one
    the unit would start. Read-only – no sudo needed.
    """
    if shutil.which("systemctl") is None:
        return (False, "systemctl not available")
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return (False, f"systemctl is-active failed: {exc}")
    return (proc.returncode == 0, proc.stdout.strip() or proc.stderr.strip())
