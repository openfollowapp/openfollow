# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann, Michel Honold, and Vinzenz Schultz
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""The Overview tab's section lists the stations and never renders the Network
configuration block, which lives in the General tab's "Network" sub-section.
The Network-block render contract is tested in :mod:`tests.test_web_general_network`.
"""

from __future__ import annotations

import pytest
from bottle import template

from openfollow.web import server as _server_module  # noqa: F401 – registers tpl path
from openfollow.web.discovery import PeerInfo
from tests._status_boxes import CARDS

pytestmark = pytest.mark.unit


def _render_overview():
    return template(
        "partials/diagnostics",
        stats={},
        cards=CARDS,
        station_down=False,
        peers=[],
        local=PeerInfo(
            name="local",
            ip="127.0.0.1",
            web_port=80,
            version="t",
            last_seen=0.0,
        ),
    )


class TestOverviewNoLongerRendersNetworkBlock:
    def test_no_network_heading(self) -> None:
        body = _render_overview()
        assert '<h3 class="stat-panel-title">Station Network</h3>' in body
        assert "<h2>Network</h2>" not in body

    def test_no_subnet_or_interface_fields_rendered(self) -> None:
        body = _render_overview()
        for needle in ("Subnet mask", "IP address", "Interface", "Method", "Backend"):
            assert needle not in body, f"Overview should not render the Network block – found {needle!r}"

    def test_no_post_mutation_routes_for_network(self) -> None:
        """Web UI is read-only – no mutation routes for /network.
        Checks both decorator-style (``@app.post``) and dynamic-style
        (``app.route(..., method='POST')``) registrations."""
        import inspect
        import re
        from pathlib import Path

        from openfollow.web import routes as _routes

        routes_path = Path(inspect.getsourcefile(_routes) or "")
        routes_src = routes_path.read_text()
        # Match @app.post("/network..."), @app.put, @app.delete, @app.patch
        decorator_re = re.compile(
            r"@app\.(post|put|delete|patch)\(\s*['\"]/network",
            re.IGNORECASE,
        )
        decorator_matches = decorator_re.findall(routes_src)
        # Match app.route('/network…', method='POST'|'PUT'|'DELETE'|'PATCH')
        # The ``method=`` kwarg may appear before or after the path –
        # accept any argument order, but pin the verb set to mutators.
        dynamic_re = re.compile(
            r"app\.route\(\s*['\"]/network[^)]*method\s*=\s*['\"](POST|PUT|DELETE|PATCH)['\"]",
            re.IGNORECASE,
        )
        dynamic_matches = dynamic_re.findall(routes_src)
        # Also catch the reversed-arg form: ``route(method='POST', '/network…')``.
        dynamic_reverse_re = re.compile(
            r"app\.route\([^)]*method\s*=\s*['\"](POST|PUT|DELETE|PATCH)['\"][^)]*['\"]/network",
            re.IGNORECASE,
        )
        dynamic_reverse_matches = dynamic_reverse_re.findall(routes_src)
        matches = decorator_matches + dynamic_matches + dynamic_reverse_matches
        assert matches == [], f"Unexpected mutation routes for /network: {matches}"
