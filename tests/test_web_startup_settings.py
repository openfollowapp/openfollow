# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""System tests for the General tab's start-at-boot switch.

Real HTTP against a live server. The host's systemd state is supplied by the
same provider pair the runtime wires, so these tests exercise the route,
template and error copy without touching ``systemctl``.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.parse
import urllib.request

import pytest

import openfollow.web.discovery as discovery_module
from openfollow.configuration import AppConfig, save_config
from openfollow.web.server import ConfigWebServer
from tests._ports import live_on_free_port

pytestmark = pytest.mark.integration


class _Host:
    """Records what the routes ask of the host and answers with canned state."""

    def __init__(self, *, available: bool = True, enabled: bool = True, reason: str = "") -> None:
        self.state = {"available": available, "enabled": enabled, "reason": reason}
        self.reads: list[str] = []
        self.writes: list[tuple[str, bool]] = []
        self.result: dict | None = None

    def read(self, service_name: str) -> dict:
        self.reads.append(service_name)
        return dict(self.state)

    def write(self, service_name: str, enabled: bool) -> dict:
        self.writes.append((service_name, enabled))
        if self.result is not None:
            return dict(self.result)
        self.state["enabled"] = enabled
        return {"ok": True, "error": "", **self.state}


def _serve(tmp_path, monkeypatch, host: _Host | None, *, cfg: AppConfig | None = None):
    monkeypatch.setattr(discovery_module.BeaconSender, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconSender, "stop", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "stop", lambda self: None)
    config_path = tmp_path / "config.toml"
    save_config(cfg or AppConfig(), str(config_path))
    return live_on_free_port(
        lambda port: ConfigWebServer(
            config_path=str(config_path),
            host="127.0.0.1",
            port=port,
            system_name="TestSystem",
            autostart_state_provider=host.read if host else None,
            autostart_apply_handler=host.write if host else None,
        )
    )


def _get(base: str, path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _post_form(base: str, path: str, data: dict) -> tuple[int, str]:
    req = urllib.request.Request(
        f"{base}{path}",
        data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _cgroup(tmp_path, unit: str):
    """A ``/proc/self/cgroup`` naming ``unit`` as the leaf."""
    path = tmp_path / "cgroup"
    path.write_text(f"0::/system.slice/{unit}\n", encoding="utf-8")
    return path


_ADVANCED_SUMMARY = "<summary>Advanced Settings</summary>"


def _advanced_opening_tag(body: str) -> str:
    """The ``<details …>`` tag that opens the Advanced Settings disclosure."""
    summary_at = body.index(_ADVANCED_SUMMARY)
    open_at = body.rindex("<details", 0, summary_at)
    return body[open_at : body.index(">", open_at) + 1]


def _advanced_group(body: str) -> str | None:
    """The disclosure's markup, or ``None`` when the page carries none."""
    if _ADVANCED_SUMMARY not in body:
        return None
    summary_at = body.index(_ADVANCED_SUMMARY)
    return body[summary_at : body.index("</details>", summary_at)]


class TestRenderingTheSwitch:
    @pytest.mark.parametrize("enabled", [True, False])
    def test_switch_shows_what_the_host_reports(self, tmp_path, monkeypatch, enabled: bool) -> None:
        host = _Host(enabled=enabled)
        with _serve(tmp_path, monkeypatch, host) as (_server, base):
            status, body = _get(base, "/section/general/startup")
        assert status == 200
        assert 'name="autostart"' in body
        assert ("checked" in body) is enabled

    def test_a_host_with_nothing_to_switch_explains_instead_of_offering_one(self, tmp_path, monkeypatch) -> None:
        host = _Host(available=False, reason="This service is masked on this host.")
        with _serve(tmp_path, monkeypatch, host) as (_server, base):
            status, body = _get(base, "/section/general/startup")
        assert status == 200
        assert 'name="autostart"' not in body
        assert "masked on this host" in body

    def test_the_read_names_the_unit_this_process_runs_under(self, tmp_path, monkeypatch) -> None:
        host = _Host()
        monkeypatch.setattr("openfollow.privilege.autostart._CGROUP_PATH", _cgroup(tmp_path, "station-app.service"))
        with _serve(tmp_path, monkeypatch, host) as (_server, base):
            _get(base, "/section/general/startup")
        assert host.reads == ["station-app.service"]

    def test_outside_systemd_the_read_falls_back_to_the_default(self, tmp_path, monkeypatch) -> None:
        host = _Host()
        monkeypatch.setattr("openfollow.privilege.autostart._CGROUP_PATH", tmp_path / "absent")
        with _serve(tmp_path, monkeypatch, host) as (_server, base):
            _get(base, "/section/general/startup")
        assert host.reads == ["openfollow"]

    @pytest.mark.parametrize("configured", ["station-app", "sshd", "-rf"])
    def test_the_configured_update_name_never_reaches_the_switch(self, tmp_path, monkeypatch, configured: str) -> None:
        """``update_service_name`` is writable over the web and ``service.enable``
        is granted as ``systemctl enable *``. Taking the switch's target from it
        would make any syntactically valid unit enablable at boot by whoever can
        reach the page."""
        host = _Host()
        monkeypatch.setattr("openfollow.privilege.autostart._CGROUP_PATH", _cgroup(tmp_path, "openfollow.service"))
        cfg = AppConfig()
        cfg.update_service_name = configured
        with _serve(tmp_path, monkeypatch, host, cfg=cfg) as (_server, base):
            _get(base, "/section/general/startup")
            _post_form(base, "/section/general/startup", {"autostart": "on"})
        assert host.reads == ["openfollow.service"]
        assert host.writes == [("openfollow.service", True)]

    def test_a_build_without_the_provider_says_so_rather_than_failing(self, tmp_path, monkeypatch) -> None:
        with _serve(tmp_path, monkeypatch, None) as (_server, base):
            status, body = _get(base, "/section/general/startup")
        assert status == 200
        assert 'name="autostart"' not in body
        assert "cannot be changed on this build" in body


class TestSwitchingIt:
    def test_turning_it_on(self, tmp_path, monkeypatch) -> None:
        host = _Host(enabled=False)
        with _serve(tmp_path, monkeypatch, host) as (_server, base):
            status, body = _post_form(base, "/section/general/startup", {"autostart": "on"})
        assert status == 200
        assert host.writes == [("openfollow", True)]
        assert "will start automatically at boot" in body
        assert "checked" in body

    def test_an_unchecked_box_sends_no_field_and_turns_it_off(self, tmp_path, monkeypatch) -> None:
        """A cleared checkbox is absent from the form, which is the off signal."""
        host = _Host(enabled=True)
        with _serve(tmp_path, monkeypatch, host) as (_server, base):
            status, body = _post_form(base, "/section/general/startup", {})
        assert status == 200
        assert host.writes == [("openfollow", False)]
        assert "will not start at boot" in body
        assert "checked" not in body

    def test_a_refused_change_shows_the_reason_and_keeps_the_host_state(self, tmp_path, monkeypatch) -> None:
        """The switch snaps back to what the host holds, not what was asked for.

        Leaving it where the operator put it would report a station as no longer
        starting at boot when it still does.
        """
        host = _Host(enabled=True)
        host.result = {
            "ok": False,
            "error": "Cancelled - the setting was not changed.",
            "available": True,
            "enabled": True,
            "reason": "",
        }
        with _serve(tmp_path, monkeypatch, host) as (_server, base):
            status, body = _post_form(base, "/section/general/startup", {})
        assert status == 200
        assert "Cancelled - the setting was not changed." in body
        assert 'role="alert"' in body
        assert "checked" in body

    def test_a_failure_with_no_message_still_says_something(self, tmp_path, monkeypatch) -> None:
        host = _Host(enabled=True)
        host.result = {"ok": False, "error": "", "available": True, "enabled": True, "reason": ""}
        with _serve(tmp_path, monkeypatch, host) as (_server, base):
            _status, body = _post_form(base, "/section/general/startup", {})
        assert "could not be changed" in body

    def test_a_build_without_the_handler_answers_a_banner_not_a_crash(self, tmp_path, monkeypatch) -> None:
        with _serve(tmp_path, monkeypatch, None) as (_server, base):
            status, body = _post_form(base, "/section/general/startup", {"autostart": "on"})
        assert status == 200
        assert "cannot be changed on this build" in body


class TestWhereItAppears:
    """The switch lives in Station Settings' Advanced Settings disclosure."""

    def test_general_page_loads_the_switch_lazily_on_a_systemd_host(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        host = _Host()
        with _serve(tmp_path, monkeypatch, host) as (_server, base):
            status, body = _get(base, "/section/general")
        assert status == 200
        assert 'id="startup-settings"' in body
        assert "window.loadAutostart()" in body
        # Lazy: the General render itself must not query the host.
        assert host.reads == []

    @pytest.mark.parametrize("path", ["/", "/section/general"])
    def test_the_switch_is_on_the_full_page_as_well_as_the_section_partial(
        self, tmp_path, monkeypatch, path: str
    ) -> None:
        """The landing page builds its own context and includes the General
        partial directly, so a gate flag supplied only to the section reload
        renders the switch on a tab switch and nowhere on first paint."""
        monkeypatch.setattr(sys, "platform", "linux")
        with _serve(tmp_path, monkeypatch, _Host()) as (_server, base):
            status, body = _get(base, path)
        assert status == 200
        assert _advanced_group(body) is not None
        assert 'id="startup-settings"' in body
        assert "window.loadAutostart()" in body

    def test_general_page_omits_the_switch_where_there_is_no_systemd(self, tmp_path, monkeypatch) -> None:
        """The disclosure stays - it still holds the experimental opt-in."""
        monkeypatch.setattr(sys, "platform", "darwin")
        with _serve(tmp_path, monkeypatch, _Host()) as (_server, base):
            for path in ("/", "/section/general"):
                status, body = _get(base, path)
                assert status == 200
                assert "/section/general/startup" not in body
                assert _advanced_group(body) is not None

    @pytest.mark.parametrize("path", ["/", "/section/general"])
    def test_the_disclosure_never_remembers_being_open(self, tmp_path, monkeypatch, path: str) -> None:
        """``data-adv-key`` is what persists a disclosure's open state, and an
        ``open`` attribute is what renders it expanded. This one must come up
        closed on every load, so it carries neither."""
        monkeypatch.setattr(sys, "platform", "linux")
        with _serve(tmp_path, monkeypatch, _Host()) as (_server, base):
            _status, body = _get(base, path)
        opening_tag = _advanced_opening_tag(body)
        assert "data-adv-key" not in opening_tag
        assert "open" not in opening_tag

    def test_both_advanced_controls_are_inside_the_disclosure(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        with _serve(tmp_path, monkeypatch, _Host()) as (_server, base):
            _status, body = _get(base, "/")
        group = _advanced_group(body)
        assert group is not None
        assert 'id="startup-settings"' in group
        assert 'name="show_experimental_features"' in group


def _no_redirect_opener() -> urllib.request.OpenerDirector:
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_a, **_kw):
            return None

    return urllib.request.build_opener(_NoRedirect)


class TestAuth:
    @pytest.mark.parametrize("method", ["GET", "POST"])
    def test_the_switch_is_behind_the_web_pin(self, tmp_path, monkeypatch, method: str) -> None:
        """Neither reading nor flipping the switch is reachable without the PIN."""
        host = _Host()
        cfg = AppConfig()
        cfg.web_pin = "4242"
        opener = _no_redirect_opener()
        with _serve(tmp_path, monkeypatch, host, cfg=cfg) as (_server, base):
            url = f"{base}/section/general/startup"
            if method == "GET":
                req = urllib.request.Request(url)
            else:
                req = urllib.request.Request(
                    url,
                    data=urllib.parse.urlencode({"autostart": "on"}).encode(),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )
            with pytest.raises(urllib.error.HTTPError) as excinfo:
                opener.open(req, timeout=5)
        assert excinfo.value.code in (302, 303, 401, 403)
        assert host.writes == []
        assert host.reads == []


class TestProviderFailures:
    """A raising provider must degrade to a readable state, never a 500."""

    def _server(self, tmp_path, **kwargs) -> ConfigWebServer:
        return ConfigWebServer(config_path=str(tmp_path / "config.toml"), **kwargs)

    def test_a_raising_read_reports_unreadable(self, tmp_path) -> None:
        def _boom(_name):
            raise RuntimeError("systemctl exploded")

        server = self._server(tmp_path, autostart_state_provider=_boom)
        assert server.get_autostart("openfollow") == {
            "available": False,
            "enabled": False,
            "reason": "Could not read whether this service starts at boot.",
        }

    def test_a_raising_write_reports_the_error_with_the_full_shape(self, tmp_path) -> None:
        def _boom(_name, _enabled):
            raise RuntimeError("systemctl exploded")

        server = self._server(tmp_path, autostart_apply_handler=_boom)
        result = server.apply_autostart("openfollow", True)
        assert result["ok"] is False
        # A written sentence, not the exception text: this branch is a bug in
        # the handler, and raw Python (plus whatever host detail it carries) is
        # not something an operator can act on.
        assert result["error"] == "The setting could not be changed."
        assert "systemctl exploded" not in result["error"]
        assert result["available"] is False
        assert result["enabled"] is False

    def test_an_unwired_build_says_so_on_both_paths(self, tmp_path) -> None:
        server = self._server(tmp_path)
        assert server.get_autostart("openfollow")["available"] is False
        result = server.apply_autostart("openfollow", True)
        assert result == {
            "ok": False,
            "error": "Autostart cannot be changed on this build.",
            "available": False,
            "enabled": False,
            "reason": "Autostart cannot be changed on this build.",
        }


class TestOnlyOneWriterPaintsTheSwitch:
    """The region had two writers: this script and an ``hx-trigger="load"`` on
    the region itself. A read issued before a write could land after it and
    repaint the switch with the state it had just left."""

    def test_the_region_carries_no_htmx_load_of_its_own(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        with _serve(tmp_path, monkeypatch, _Host()) as (_server, base):
            _status, body = _get(base, "/section/general")
        region_at = body.index('id="startup-settings"')
        region_tag = body[body.rindex("<div", 0, region_at) : body.index(">", region_at) + 1]
        assert "hx-get" not in region_tag, region_tag
        assert "hx-trigger" not in region_tag, region_tag

    def test_every_render_is_sequence_guarded(self, tmp_path, monkeypatch) -> None:
        """Both the initial read and the write bump the same counter, and the
        one shared applier drops a reply that is no longer the newest."""
        monkeypatch.setattr(sys, "platform", "linux")
        with _serve(tmp_path, monkeypatch, _Host()) as (_server, base):
            _status, body = _get(base, "/section/general")
        assert "if (seq !== window._autostartSeq) return;" in body
        assert body.count("++window._autostartSeq") == 2
        # The region is written in exactly one place.
        assert body.count("region.innerHTML = html") == 1
        assert "getElementById('startup-settings').innerHTML" not in body


class TestAdvancedDisclosureSeparation:
    """One rule above the disclosure, not two.

    ``.inline-advanced`` draws its own top border, so a ``group--divider`` on
    the group right above it put a second rule a few pixels away.
    """

    def test_the_group_above_the_disclosure_draws_no_rule_of_its_own(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        with _serve(tmp_path, monkeypatch, _Host()) as (_server, base):
            _status, body = _get(base, "/section/general")
        disclosure_at = body.index('<details class="inline-advanced">')
        preceding = body[:disclosure_at]
        last_group = preceding[preceding.rindex("<div class=") :]
        assert "group--divider" not in last_group, last_group[:200]

    def test_a_following_action_row_is_not_flush_against_the_disclosure(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        with _serve(tmp_path, monkeypatch, _Host()) as (_server, base):
            _status, body = _get(base, "/")
        assert ".inline-advanced + .actions" in body
