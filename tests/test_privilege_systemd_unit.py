# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the systemd unit self-install path."""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any

import pytest

from openfollow.privilege.broker import PrivilegeError
from openfollow.privilege.capabilities import (
    DEVICE_INSTALL_SYSTEMD_UNIT,
    DEVICE_SYSTEMD_ANALYZE,
    SERVICE_DAEMON_RELOAD,
    SERVICE_ENABLE,
)
from openfollow.privilege.systemd_unit import (
    _resolve_poetry_bin,
    install_unit,
    is_unit_installed,
    render_service_unit,
    stop_and_status,
)
from tests._fake_broker import FakeBroker, make_failure

pytestmark = pytest.mark.unit


class TestRenderServiceUnit:
    def test_includes_user_uid_and_paths(self) -> None:
        text = render_service_unit(
            user="bob",
            uid=1001,
            repo_dir=Path("/home/bob/openfollow"),
            poetry_bin=Path("/home/bob/.local/bin/poetry"),
        )
        assert "User=bob" in text
        assert "WorkingDirectory=/home/bob/openfollow" in text
        assert "user@1001.service" in text
        assert "/home/bob/.local/bin/poetry" in text
        assert "ExecStart=/usr/bin/cage" in text
        assert "[Install]" in text
        assert "WantedBy=multi-user.target" in text

    def test_service_name_defaults_to_openfollow(self) -> None:
        text = render_service_unit(
            user="bob",
            uid=1001,
            repo_dir=Path("/x"),
            poetry_bin=Path("/y"),
        )
        assert "SyslogIdentifier=openfollow" in text

    def test_service_name_override_propagates(self) -> None:
        text = render_service_unit(
            user="bob",
            uid=1001,
            repo_dir=Path("/x"),
            poetry_bin=Path("/y"),
            service_name="my-openfollow",
        )
        assert "SyslogIdentifier=my-openfollow" in text


class TestRenderServiceUnitValidation:
    """Every interpolated value lands in a root-installed unit, so the
    inputs are gated the same way ``render_drop_in`` gates its user."""

    @pytest.mark.parametrize(
        "bad_user",
        [
            "root\nExecStartPre=/bin/sh -c payload",  # newline injects a directive
            "Bob",  # uppercase rejected (mirrors the sudoers user check)
            "bad user",  # space
            "x" * 33,  # over length cap
            "",  # empty
        ],
    )
    def test_rejects_unsafe_user(self, bad_user: str) -> None:
        with pytest.raises(ValueError, match="unsafe user"):
            render_service_unit(user=bad_user, uid=1001, repo_dir=Path("/x"), poetry_bin=Path("/y"))

    @pytest.mark.parametrize(
        "override",
        [
            {"repo_dir": Path("/x\nExecStartPre=/bin/sh -c payload")},
            {"poetry_bin": Path("/y\npayload")},
            {"service_name": "openfollow\nExecStartPre=evil"},
        ],
    )
    def test_rejects_control_chars(self, override: dict[str, Any]) -> None:
        kwargs: dict[str, Any] = {
            "user": "bob",
            "uid": 1001,
            "repo_dir": Path("/x"),
            "poetry_bin": Path("/y"),
        }
        kwargs.update(override)
        with pytest.raises(ValueError, match="control character"):
            render_service_unit(**kwargs)

    def _exec_command_words(self, unit_text: str) -> list[str]:
        """Re-derive the command argv ``/bin/sh -c`` would execute.

        ``ExecStart=/usr/bin/cage -- /bin/sh -c '<arg>'`` – systemd splits
        the directive into argv, then ``/bin/sh -c`` re-tokenises ``<arg>``.
        Walking that two-stage split is how we observe a quoting breakout:
        if a value escaped the single-quoted ``-c`` argument it would show
        up as extra command words instead of one literal token.
        """
        prefix = "ExecStart=/usr/bin/cage -- /bin/sh -c "
        line = next(line for line in unit_text.splitlines() if line.startswith(prefix))
        sh_c_arg = shlex.split(line[len(prefix) :])[0]
        return shlex.split(sh_c_arg)

    @pytest.mark.parametrize(
        "override",
        [
            # Single quote: ExecStart ``/bin/sh -c '...'`` breakout.
            {"poetry_bin": Path("/tmp/p' ; touch /tmp/pwned ; '")},
            # Quote in a bare systemd directive (WorkingDirectory / SyslogIdentifier).
            {"repo_dir": Path("/home/'bob'/openfollow")},
            {"service_name": "open'follow"},
            {"poetry_bin": Path('/tmp/"quoted"')},
        ],
    )
    def test_rejects_quote_chars(self, override: dict[str, Any]) -> None:
        kwargs: dict[str, Any] = {
            "user": "bob",
            "uid": 1001,
            "repo_dir": Path("/x"),
            "poetry_bin": Path("/y"),
        }
        kwargs.update(override)
        with pytest.raises(ValueError, match="quote character"):
            render_service_unit(**kwargs)

    @pytest.mark.parametrize(
        "poetry_bin",
        [
            "/tmp/p;touch /tmp/pwned",  # command separator
            "/tmp/p&touch /tmp/pwned",  # background / chain
            "/tmp/p|tee /tmp/pwned",  # pipe
            "/tmp/p$(touch /tmp/pwned)",  # command substitution
            "/tmp/p`touch /tmp/pwned`",  # backtick substitution
            "/tmp/p>/tmp/pwned",  # redirection
            "/home/my dir/poetry",  # whitespace splits the unquoted command
            "/tmp/p*",  # glob expansion
        ],
    )
    def test_rejects_shell_metacharacters_in_poetry_bin(self, poetry_bin: str) -> None:
        # poetry_bin lands unquoted in the ``/bin/sh -c`` command, so a bare
        # metacharacter (no quote needed) would inject or split it. Guards the
        # gap that quote-rejection alone leaves open.
        with pytest.raises(ValueError, match="shell metacharacter"):
            render_service_unit(
                user="bob",
                uid=1001,
                repo_dir=Path("/x"),
                poetry_bin=Path(poetry_bin),
            )

    def test_normal_poetry_bin_rendered_verbatim(self) -> None:
        # Quoting must not corrupt an ordinary path (no needless escaping).
        text = render_service_unit(
            user="bob",
            uid=1001,
            repo_dir=Path("/x"),
            poetry_bin=Path("/home/bob/.local/bin/poetry"),
        )
        words = self._exec_command_words(text)
        # Foreground app (no `exec`), then tear kanshi down preserving the app's
        # exit code. poetry_bin stays a single verbatim token – a quote breakout
        # would split it or inject extra words.
        assert words == [
            "kanshi",
            "&",
            "/home/bob/.local/bin/poetry",
            "run",
            "python",
            "-m",
            "openfollow.main;",
            "rc=$?;",
            "kill",
            "$!",
            "2>/dev/null;",
            "exit",
            "$rc",
        ]


class TestIsUnitInstalled:
    def test_returns_true_when_file_exists(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.systemd_unit.SYSTEM_UNIT_DIR",
            tmp_path,
        )
        (tmp_path / "openfollow.service").write_text("")
        assert is_unit_installed() is True

    def test_returns_false_when_file_absent(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.systemd_unit.SYSTEM_UNIT_DIR",
            tmp_path,
        )
        assert is_unit_installed() is False


class TestInstallUnit:
    def test_validates_install_reloads_enables(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.systemd_unit.SYSTEM_UNIT_DIR",
            tmp_path,
        )
        monkeypatch.setenv("USER", "tester")
        monkeypatch.setattr(
            "openfollow.privilege.systemd_unit._resolve_poetry_bin",
            lambda: Path("/usr/local/bin/poetry"),
        )
        broker = FakeBroker()
        install_unit(broker)
        capabilities = [c.capability for c in broker.calls]
        # Install uses the narrow DEVICE_INSTALL_SYSTEMD_UNIT capability that pins tmp and destination.
        assert capabilities == [
            DEVICE_SYSTEMD_ANALYZE,
            DEVICE_INSTALL_SYSTEMD_UNIT,
            SERVICE_DAEMON_RELOAD,
            SERVICE_ENABLE,
        ]
        # Last call's last arg is the service name (no ``--now`` here –
        # the running foreground app must not be stomped by an auto-start).
        assert broker.calls[-1].argv[-1] == "openfollow"
        assert "--now" not in broker.calls[-1].argv

    def test_validation_failure_does_not_install(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.systemd_unit.SYSTEM_UNIT_DIR",
            tmp_path,
        )
        monkeypatch.setenv("USER", "tester")
        broker = FakeBroker()
        broker.exceptions = [make_failure("bad unit")]
        with pytest.raises(PrivilegeError, match="validation failed"):
            install_unit(broker)
        # Only the validate call happened – install / reload / enable never ran.
        capabilities = [c.capability for c in broker.calls]
        assert capabilities == [DEVICE_SYSTEMD_ANALYZE]

    def test_enable_skipped_when_flag_false(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.systemd_unit.SYSTEM_UNIT_DIR",
            tmp_path,
        )
        monkeypatch.setenv("USER", "tester")
        broker = FakeBroker()
        install_unit(broker, enable=False)
        capabilities = [c.capability for c in broker.calls]
        assert SERVICE_ENABLE not in capabilities

    def test_explicit_args_override_defaults(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.systemd_unit.SYSTEM_UNIT_DIR",
            tmp_path,
        )
        broker = FakeBroker()
        install_unit(
            broker,
            service_name="my-custom",
            user="alice",
            repo_dir=Path("/srv/openfollow"),
            poetry_bin=Path("/srv/poetry"),
        )
        # The install call's target path encodes the service name –
        # confirms the render used our ``service_name=`` override.
        target = broker.calls[1].argv[-1]
        assert target == str(tmp_path / "my-custom.service")

    def test_temp_file_cleaned_up(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.systemd_unit.SYSTEM_UNIT_DIR",
            tmp_path,
        )
        monkeypatch.setenv("USER", "tester")
        broker = FakeBroker()
        install_unit(broker)
        assert not Path(broker.calls[0].argv[-1]).exists()

    def test_staged_unit_keeps_0600_not_world_readable(self, tmp_path, monkeypatch) -> None:
        """The staged unit keeps mkstemp's 0600 in /tmp – ``install -m 0644``
        sets the destination mode, so the temp file is never widened to
        world-readable before the root move."""
        monkeypatch.setattr(
            "openfollow.privilege.systemd_unit.SYSTEM_UNIT_DIR",
            tmp_path,
        )
        monkeypatch.setenv("USER", "tester")

        class _ModeCapturingBroker:
            """Delegates to a real FakeBroker but stats the staged unit
            while it still exists (during the systemd-analyze call)."""

            def __init__(self) -> None:
                self._inner = FakeBroker()
                self.staged_modes: list[int] = []

            def run(self, capability: Any, argv: list[str], **kwargs: Any) -> Any:
                if "systemd-analyze" in argv[0]:
                    self.staged_modes.append(os.stat(argv[-1]).st_mode & 0o777)
                return self._inner.run(capability, argv, **kwargs)

        broker = _ModeCapturingBroker()
        install_unit(broker)  # type: ignore[arg-type]
        assert broker.staged_modes == [0o600]


class TestResolvePoetryBin:
    def test_returns_path_from_which_when_available(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.systemd_unit.shutil.which",
            lambda name: "/opt/poetry/bin/poetry",
        )
        assert _resolve_poetry_bin() == Path("/opt/poetry/bin/poetry")

    def test_falls_back_to_user_install_path(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.systemd_unit.shutil.which",
            lambda name: None,
        )
        result = _resolve_poetry_bin()
        assert ".local/bin/poetry" in str(result)


class TestStopAndStatus:
    def test_active_returns_true_and_label(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.systemd_unit.shutil.which",
            lambda name: "/usr/bin/systemctl",
        )
        import subprocess as sp

        monkeypatch.setattr(
            "openfollow.privilege.systemd_unit.subprocess.run",
            lambda *a, **kw: sp.CompletedProcess([], 0, "active\n", ""),
        )
        active, label = stop_and_status("openfollow")
        assert active is True
        assert label == "active"

    def test_inactive_returns_false(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.systemd_unit.shutil.which",
            lambda name: "/usr/bin/systemctl",
        )
        import subprocess as sp

        monkeypatch.setattr(
            "openfollow.privilege.systemd_unit.subprocess.run",
            lambda *a, **kw: sp.CompletedProcess([], 3, "inactive\n", ""),
        )
        active, label = stop_and_status("openfollow")
        assert active is False
        assert label == "inactive"

    def test_missing_systemctl_reports_so(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.systemd_unit.shutil.which",
            lambda name: None,
        )
        active, label = stop_and_status()
        assert active is False
        assert "not available" in label

    def test_subprocess_error_returns_false(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "openfollow.privilege.systemd_unit.shutil.which",
            lambda name: "/usr/bin/systemctl",
        )
        import subprocess as sp

        def _boom(*a, **kw):
            raise sp.SubprocessError("boom")

        monkeypatch.setattr(
            "openfollow.privilege.systemd_unit.subprocess.run",
            _boom,
        )
        active, label = stop_and_status()
        assert active is False
        assert "boom" in label
