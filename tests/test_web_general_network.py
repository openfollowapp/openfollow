# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the General tab structure.

The General tab hosts three foldable sub-sections:

* ``Station Settings`` (fold key ``general-station``, default-expanded)
  – one box combining the Display-units radios, the Station name
  (``psn_system_name``), and the Web Access PIN (``web_pin``).
* ``Network Settings`` (fold key ``general-network-interface``) – the
  lazy-loaded Pi network interface status/edit region.
* ``Software Update`` (fold key ``general-software-update``,
  default-collapsed).

Pinned contracts:

* Station name + PIN share one form (``general-network-section``) that
  saves via ``/section/general``; the Display-units form
  (``general-display-section``) live-applies via ``/settings/unit-system``.
* Marker Assignment is gone from this tab (moved to Marker).
"""

from __future__ import annotations

import pytest
from bottle import template

from openfollow.configuration import AppConfig
from openfollow.web import server as _server_module  # noqa: F401

pytestmark = pytest.mark.unit


def _render_general(**overrides):
    """Render ``partials/general`` with the minimum required context.

    ``network_state`` defaults to ``None`` so legacy callers that
    didn't pass it still render cleanly (the partial uses
    ``defined()`` to guard the include). ``current_version`` is
    required context: the Software Update section displays the
    installed version from it."""
    cfg = AppConfig()
    ctx = {
        "config": cfg,
        "saved": False,
        "restarting": False,
        "local_ips": ["127.0.0.1"],
        "update_status": {"state": "idle", "message": "", "error": ""},
        "network_state": None,
        "current_version": "0.2.3",
        # The real route sets this from ``sys.platform``; default to the
        # Pi/Linux case (section shown) so the existing structure tests hold.
        "update_supported": True,
    }
    ctx.update(overrides)
    return template("partials/general", **ctx)


class TestGeneralStructure:
    def test_renders_foldable_subsections(self) -> None:
        body = _render_general()
        assert 'data-fold-key="general-station"' in body
        assert 'data-fold-key="general-network-interface"' in body
        assert 'data-fold-key="general-software-update"' in body

    def test_software_update_subsection_is_default_collapsed(self) -> None:
        body = _render_general()
        # The fold default is encoded in ``data-fold-default``;
        # ``collapsed`` means the inner content is hidden until
        # the operator expands it.
        assert 'data-fold-default="collapsed"' in body

    def test_station_settings_is_default_expanded(self) -> None:
        body = _render_general()
        assert 'data-fold-key="general-station"' in body
        assert 'data-fold-default="expanded"' in body

    def test_web_pin_input_is_in_station_settings(self) -> None:
        body = _render_general()
        # The PIN input now lives in the Station Settings box; presence
        # alone is enough to confirm the restructure didn't drop it.
        assert 'name="web_pin"' in body
        assert "Station Settings" in body

    def test_station_name_input_is_in_station_settings(self) -> None:
        # The Station name (``psn_system_name``) moved out of the PSN
        # Output section into Station Settings, where it's editable.
        body = _render_general()
        assert 'name="psn_system_name"' in body
        assert 'id="general-psn-system-name"' in body
        assert "Station name" in body

    def test_unit_system_select_is_in_station_settings(self) -> None:
        # The Display-units form lives inside the Station Settings box and
        # keeps its own id so it can swap itself on a live unit change.
        # The control is a dropdown (``<select name="unit_system">``).
        body = _render_general()
        assert 'id="general-display-section"' in body
        assert '<select id="general-unit-system" name="unit_system">' in body
        assert 'value="imperial"' in body

    def test_software_update_shows_current_version(self) -> None:
        """The update section must display the installed version so the
        operator can see what's running before clicking Check & Install."""
        body = _render_general(current_version="0.2.3rc6")
        assert "v0.2.3rc6" in body

    def test_software_update_has_check_and_install_button(self) -> None:
        """The new UI is a single button that posts to the deb-update route."""
        body = _render_general()
        assert "/section/general/deb-update" in body
        # Old git-based fields must be gone.
        assert 'name="update_source_url"' not in body
        assert 'name="update_repo_branch"' not in body
        assert "/section/general/update-from-public" not in body

    def test_software_update_hidden_when_unsupported(self) -> None:
        """On hosts without the .deb installer (macOS) the whole Software
        Update section and its inline JS are absent."""
        body = _render_general(update_supported=False)
        assert "general-software-update" not in body
        assert "/section/general/deb-update" not in body
        assert "/section/general/deb-upload" not in body
        assert "openfollowCheckUpdate" not in body
        assert "openfollowUploadUpdate" not in body
        # The rest of the tab is unaffected.
        assert 'data-fold-key="general-station"' in body
        assert 'data-fold-key="general-network-interface"' in body

    def test_software_update_shown_when_supported(self) -> None:
        """On the Pi/Linux case the section and its handlers render."""
        body = _render_general(update_supported=True)
        assert 'data-fold-key="general-software-update"' in body
        assert "openfollowCheckUpdate" in body

    def test_marker_assignment_is_gone(self) -> None:
        body = _render_general()
        assert 'name="controlled_marker_ids"' not in body
        assert 'name="viewer_marker_ids"' not in body


class TestGeneralNetworkInterfaceRegion:
    """Network Interface region lazy-loads unified status/edit form,
    kept outside Web Access form to avoid form nesting."""

    def test_lazy_loads_the_status_view(self) -> None:
        body = _render_general()
        assert 'id="network-interface"' in body
        assert 'hx-get="/section/network/status"' in body
        assert 'hx-trigger="load"' in body

    def test_network_interface_is_its_own_subsection(self) -> None:
        body = _render_general()
        assert 'data-fold-key="general-network-interface"' in body
        assert "Network Settings" in body

    def test_no_inline_network_state_table(self) -> None:
        """The old inline read-only table + its 5 s poll moved into the status
        view; general.tpl no longer embeds them."""
        body = _render_general()
        assert 'id="general-network-state"' not in body
        assert "/section/general/network_state" not in body


class TestUpdateSupportedFlag:
    """``_build_general_template_data`` derives ``update_supported`` from the
    host platform: the .deb installer is Pi/Linux-only."""

    @staticmethod
    def _build(monkeypatch: pytest.MonkeyPatch, platform: str) -> dict:
        from types import SimpleNamespace

        from openfollow.web import routes

        monkeypatch.setattr(routes.sys, "platform", platform)
        monkeypatch.setattr(routes, "_get_local_ips", lambda: ["127.0.0.1"])
        server = SimpleNamespace(
            get_update_status=lambda: {"state": "idle", "message": "", "error": ""},
            get_network_state=lambda: None,
            get_update_available=lambda: "",
        )
        return routes._build_general_template_data(server, AppConfig())

    @pytest.mark.parametrize(
        ("platform", "expected"),
        [
            ("linux", True),
            ("linux2", True),  # older sys.platform spelling
            ("darwin", False),
            ("win32", False),  # only the Linux .deb installer is supported
        ],
    )
    def test_update_supported_follows_platform(
        self, monkeypatch: pytest.MonkeyPatch, platform: str, expected: bool
    ) -> None:
        data = self._build(monkeypatch, platform)
        assert data["update_supported"] is expected


def _update_script_function(name: str) -> str:
    """Body of ``window.<name>`` in the rendered Software Update script."""
    import re

    match = re.search(
        rf"window\.{name} = (?:async )?function \([^)]*\) \{{(.*?)\n\}};",
        _render_general(update_supported=True),
        re.DOTALL,
    )
    assert match is not None, f"window.{name} not found in partials/general"
    return match.group(1)


class TestSoftwareUpdateProgressModal:
    """Once the operator confirms an install, the page stays locked and says
    which step is running until the device restarts."""

    def test_offline_install_is_one_control(self) -> None:
        # Picking the bundle is the whole action: the button opens the picker
        # and the choice itself starts the install.
        body = _render_general(update_supported=True)
        assert "document.getElementById('general-update-file').click()" in body
        assert 'onchange="openfollowUploadUpdate(this)"' in body
        assert "Choose Update &amp; Install" in body
        assert "modalConfirm" not in _update_script_function("openfollowUploadUpdate")

    def test_upload_locks_the_page_before_the_bundle_is_sent(self) -> None:
        body = _update_script_function("openfollowUploadUpdate")
        chosen = body.index("if (!file) return;")
        locked = body.index("openfollowUpdateProgress('Uploading update…')")
        sent = body.index("xhr.send(file)")
        assert chosen < locked < sent

    def test_the_same_file_can_be_chosen_again_after_a_failure(self) -> None:
        # A file input only fires change when its value changes.
        body = _update_script_function("openfollowUploadUpdate")
        assert body.index("input.value = '';") < body.index("xhr.send(file)")

    def test_upload_reports_progress_then_verification(self) -> None:
        body = _update_script_function("openfollowUploadUpdate")
        assert "xhr.upload.onprogress" in body
        assert "openfollowUpdateProgress('Uploading update… ' + Math.floor(" in body
        # The device verifies the bundle after the last byte arrives and before it answers.
        uploaded = body[body.index("xhr.upload.onload = () => {") : body.index("xhr.onload = () => {")]
        assert "openfollowUpdateProgress('Verifying update…')" in uploaded

    def test_check_and_install_locks_the_page_before_starting(self) -> None:
        body = _update_script_function("openfollowCheckUpdate")
        confirmed = body.index("if (!confirmed) return;")
        locked = body.index("openfollowUpdateProgress('Starting update…')")
        started = body.index("openfollowUpdateJSON('/section/general/deb-update', {")
        assert confirmed < locked < started

    def test_poll_shows_every_step_message(self) -> None:
        # Download, verify and install all report as one state, so a state
        # guard would freeze the first message on screen.
        body = _update_script_function("openfollowPollUpdate")
        tail = body[body.rindex("sawProgress = true;") :]
        assert "showUpdating(st.message" in tail
        assert "if (" not in tail[: tail.index("showUpdating(st.message")]

    def test_progress_modal_is_locked_and_rewrites_its_status_line(self) -> None:
        body = _update_script_function("openfollowUpdateProgress")
        assert "dismissable: false" in body
        assert 'id="update-progress-msg" role="status" aria-live="polite"' in body
        # An open modal gets its line rewritten rather than being rebuilt.
        assert body.index("line.textContent = msg;") < body.index("openModal(")
        # The modal has no button, so the line is what takes focus.
        assert 'aria-live="polite" tabindex="-1"' in body

    def test_an_aborted_or_timed_out_upload_releases_the_lock(self) -> None:
        # Neither fires onload, so without these the locked dialog never closes.
        body = _update_script_function("openfollowUploadUpdate")
        assert "xhr.onerror = xhr.onabort = xhr.ontimeout = () => finish({\n        ok: false," in body

    def test_a_settled_upload_cannot_reopen_the_lock(self) -> None:
        # A progress event queued before the result would otherwise lock the
        # page again over the closable error.
        body = _update_script_function("openfollowUploadUpdate")
        settle = body[body.index("const finish = (result) => {") :]
        settle = settle[: settle.index("};")]
        assert settle.index("xhr.upload.onprogress = xhr.upload.onload = null;") < settle.index("resolve(result);")
        promise = body[body.index("new Promise((resolve) => {") : body.index("xhr.send(file);")]
        assert promise.count("resolve(") == 1

    def test_every_request_behind_the_lock_gives_up(self) -> None:
        # A request that never settles would leave the page inert for good.
        helper = _update_script_function("openfollowUpdateJSON")
        assert "setTimeout(() => ctrl.abort(), ms)" in helper
        # The body read is bounded too, not just the headers.
        assert helper.index(".then((resp) => resp.json())") < helper.index(".finally(() => clearTimeout(timer))")
        check = _update_script_function("openfollowCheckUpdate")
        after_lock = check[check.index("openfollowUpdateProgress('Starting update…')") :]
        assert "openfollowUpdateJSON('/section/general/deb-update', {" in after_lock
        assert "fetch(" not in after_lock
        poll = _update_script_function("openfollowPollUpdate")
        assert "openfollowUpdateJSON('/api/update-status'" in poll
        assert "fetch(" not in poll

    def test_a_stalled_upload_releases_the_lock(self) -> None:
        # Bytes that stop moving, or no answer once the bundle is in, abort the request.
        body = _update_script_function("openfollowUploadUpdate")
        assert "watchdog = setTimeout(() => { stalled = true; xhr.abort(); }, ms);" in body
        assert "arm(30000);\n      xhr.send(file);" in body
        progress = body[body.index("xhr.upload.onprogress = (ev) => {") : body.index("xhr.upload.onload = () => {")]
        # Re-armed only by bytes that moved, not by any progress event.
        assert progress.index("if (ev.loaded > sent) {") < progress.index("arm(30000);")
        uploaded = body[body.index("xhr.upload.onload = () => {") : body.index("xhr.onload = () => {")]
        assert "arm(120000);" in uploaded

    def test_an_answer_that_is_not_an_object_takes_the_error_path(self) -> None:
        # JSON ``null`` parses fine; reading ``.ok`` / ``.state`` off it outside
        # a ``try`` would leave the lock stuck.
        guard = _update_script_function("openfollowUpdateObject")
        assert "if (!data || typeof data !== 'object' || Array.isArray(data)) throw" in guard
        helper = _update_script_function("openfollowUpdateJSON")
        assert helper.index(".then(openfollowUpdateObject)") < helper.index(".finally(")
        upload = _update_script_function("openfollowUploadUpdate")
        assert "finish(openfollowUpdateObject(JSON.parse(xhr.responseText)));" in upload

    def test_only_a_dropped_connection_counts_as_a_restart(self) -> None:
        # A timed-out poll followed by "idle" must not read as a finished update.
        body = _update_script_function("openfollowPollUpdate")
        assert "const dropped = err instanceof TypeError;" in body
        assert "if (dropped) sawProgress = true;" in body
        assert "sawProgress = true;  //" not in body

    def test_a_job_that_stays_queued_is_not_progress(self) -> None:
        # Otherwise a job that never starts waits out the 15-minute backstop,
        # and a later "idle" reads as a finished update.
        body = _update_script_function("openfollowPollUpdate")
        queued = body.index("if (st.state === 'queued') {")
        assert queued < body.rindex("sawProgress = true;")
        branch = body[queued : body.index("continue;", queued)]
        assert "++idleWaits >= MAX_IDLE" in branch

    def test_poll_gives_up_on_elapsed_time_not_poll_count(self) -> None:
        # Each poll can wait out its own timeout, so a count would stretch the limits.
        body = _update_script_function("openfollowPollUpdate")
        assert "performance.now() - unreachableSince >= UNREACHABLE_MS" in body
        assert "performance.now() - beganAt >= GIVE_UP_MS" in body
        assert "Date.now()" not in body


def _base_modal_source() -> str:
    from pathlib import Path

    import openfollow

    base = Path(openfollow.__file__).resolve().parent / "web" / "templates" / "base.tpl"
    src = base.read_text(encoding="utf-8")
    return src[src.index(" let _modalCloseHandler = null;") : src.index(" function modalConfirm(opts) {")]


class TestLockedModalBlocksThePage:
    """The backdrop stops the mouse; a locked modal must stop the keyboard too."""

    def test_a_locked_modal_makes_the_page_behind_it_inert(self) -> None:
        src = _base_modal_source()
        assert "_lockModalBackground(!_modalDismissable);" in src
        helper = src[src.index("function _lockModalBackground(lock) {") : src.index(" function closeModal() {")]
        assert "sib.setAttribute('inert', '');" in helper

    def test_closing_releases_only_what_the_modal_made_inert(self) -> None:
        # The closed help drawer carries its own inert, which must survive.
        src = _base_modal_source()
        helper = src[src.index("function _lockModalBackground(lock) {") : src.index(" function closeModal() {")]
        assert "_modalInerted.forEach((el) => {" in helper
        # A drawer that finished closing while the modal was up keeps its inert.
        assert "if (el.getAttribute('aria-hidden') !== 'true') el.removeAttribute('inert');" in helper
        assert "!sib.hasAttribute('inert')" in helper
        close = src[src.index(" function closeModal() {") : src.index(" function openModal(opts) {")]
        # An inert element can't take focus, so release first.
        assert close.index("_lockModalBackground(false);") < close.index("lastFocus.focus();")

    def test_a_modal_replacing_an_open_one_keeps_the_original_focus_target(self) -> None:
        src = _base_modal_source()
        assert "if (root.hidden) _modalLastFocus = document.activeElement;" in src
