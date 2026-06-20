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
