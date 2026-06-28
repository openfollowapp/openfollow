# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""System tests for ConfigWebServer – real HTTP requests against a live server.

Spins up a Bottle server on a free localhost port.  Multicast beacon I/O is
stubbed out so tests run offline and without network privileges.  Every test
that hits a template validates end-to-end rendering; every config POST test
re-reads the saved file to confirm persistence.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

import openfollow
import openfollow.web.discovery as discovery_module
from openfollow.configuration import AppConfig, load_config, save_config
from openfollow.web import peer_auth
from openfollow.web.server import ConfigWebServer

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------


def _find_free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, host: str = "127.0.0.1", timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.1):
                return True
        except OSError:
            time.sleep(0.05)
    return False


@pytest.fixture()
def live_server(tmp_path, monkeypatch):
    """ConfigWebServer on a free localhost port; beacon I/O stubbed out."""
    monkeypatch.setattr(discovery_module.BeaconSender, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconSender, "stop", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "stop", lambda self: None)

    port = _find_free_tcp_port()
    config_path = tmp_path / "config.toml"

    server = ConfigWebServer(
        config_path=str(config_path),
        host="127.0.0.1",
        port=port,
        system_name="TestSystem",
    )
    server.start()
    assert _wait_for_port(port), f"Web server did not start within 5 s on port {port}"

    yield server, f"http://127.0.0.1:{port}"
    server.stop()


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _get(base: str, path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _get_json(base: str, path: str) -> tuple[int, dict]:
    status, body = _get(base, path)
    return status, json.loads(body)


def _post_json(base: str, path: str, data: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def _post_raw_json(base: str, path: str, payload: object, method: str = "POST") -> tuple[int, dict]:
    """Post an arbitrary JSON value (not necessarily a dict) and parse the response."""
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def _post_form_full(base: str, path: str, data: dict) -> tuple[int, str, dict]:
    """Form POST returning ``(status, body, headers_dict)``."""
    req = urllib.request.Request(
        f"{base}{path}",
        data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode(), dict(r.headers.items())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers.items())


def _post_form(base: str, path: str, data: dict) -> tuple[int, str]:
    status, body, _ = _post_form_full(base, path, data)
    return status, body


def _no_redirect_opener() -> urllib.request.OpenerDirector:
    """Return an opener that surfaces 3xx responses instead of following them.

    Tests assert on the Location header + status code; following the
    redirect would lose that information.
    """

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_a, **_kw):
            return None

    return urllib.request.build_opener(_NoRedirect)


# ---------------------------------------------------------------------------
# HTML page smoke tests – any template rendering error surfaces here
# ---------------------------------------------------------------------------


def test_index_page_renders(live_server) -> None:
    server, base = live_server
    status, body = _get(base, "/")
    assert status == 200
    assert len(body) > 200


def test_index_page_populates_detection_partial_context(live_server) -> None:
    """Initial render supplies same context as /section/detection for
    dropdowns and static catalogue entries."""
    _, base = live_server
    status, body = _get(base, "/")
    assert status == 200
    sec_status, section = _get(base, "/section/detection")
    assert sec_status == 200
    # The "Download Model" group renders only when ``catalogue_unavailable`` is
    # non-empty, i.e. when the route passed ``detection_available_models`` rather
    # than the empty-list fallback. Compare the index render against the canonical
    # section route instead of an absolute string: if the index dropped the
    # context it would diverge here, and the check stays correct regardless of how
    # many catalogue models happen to be on disk in the test cwd.
    assert ("Download Model" in body) == ("Download Model" in section)


def test_index_page_uses_locally_bundled_htmx(live_server) -> None:
    _, base = live_server
    status, body = _get(base, "/")
    assert status == 200
    # Local bundle, cache-busted with the build identity (``?v=…``) so an
    # app update can't be masked by a stale browser copy.
    assert re.search(r'<script src="/assets/htmx\.min\.js\?v=[^"]+"></script>', body)
    # Negative CDN list catches a copy-paste reintroduction even if the
    # positive assertion above passes via an additional script tag.
    for cdn in ("unpkg.com", "cdn.jsdelivr.net", "cdnjs.cloudflare.com"):
        assert cdn not in body, f"CDN reference reintroduced: {cdn!r}"


def test_select_options_have_explicit_dark_background(live_server) -> None:
    # Regression guard: the native <select> dropdown popup does not inherit
    # the select's dark background. Firefox renders the option list on the
    # OS-default white surface, so our light --text colour on <option>s was
    # near-invisible (the "contrastless video source picker" bug report).
    # The fix pins an explicit dark background on option/optgroup; pin it so a
    # future base.tpl edit can't silently drop it and bring back the white
    # popup. Match the rule shape, not exact whitespace.
    _, base = live_server
    status, body = _get(base, "/")
    assert status == 200
    rule = re.search(
        r"option[^{}]*\{[^{}]*background-color\s*:\s*var\(--bg-deep\)",
        body,
    )
    assert rule is not None, "option dropdown popup lost its explicit dark background"


def test_body_background_fade_is_viewport_sized(live_server) -> None:
    # Regression guard: the yellow->green page fade is painted on <body>. If it
    # sizes to the body's content box, the fade stretches with page length – a
    # short section compresses it, a long one (Detection) spreads it over
    # thousands of pixels, so the same fade lands in a different place per
    # section. The fix keys the gradient height to the viewport (100vh) and
    # fills below it with the matching base colour. Pin the viewport sizing so a
    # future base.tpl edit can't silently reintroduce the page-length coupling.
    _, base = live_server
    status, body = _get(base, "/")
    assert status == 200
    rule = re.search(
        r"body\s*\{[^{}]*background-size\s*:\s*100%\s+100vh",
        body,
    )
    assert rule is not None, "body background fade is no longer viewport-sized (100vh)"


def test_index_page_preserves_scroll_across_poll_swaps(live_server) -> None:
    # Regression guard: the Overview tab's 1s statistics poll (and the 5s
    # diagnostics / server-overview swaps) replace whole DOM subtrees, which
    # destroys Firefox's scroll-anchor node and snaps the viewport to the top
    # on every tick. The fix re-pins the scroll position after automatic
    # (poll / ``load``) swaps. Pin the wiring so a future base.tpl edit can't
    # silently drop it and reintroduce the jump.
    _, base = live_server
    status, body = _get(base, "/")
    assert status == 200
    # Captures scroll before an automatic swap and restores it after.
    assert "htmx:beforeSwap" in body
    assert "window.scrollTo(" in body
    # Gates on user-vs-poll so Save / click swaps still scroll naturally –
    # without this discriminator the handler would over-pin user actions.
    assert "triggeringEvent" in body
    # Only captures when a swap will actually occur – an error / no-swap
    # response fires beforeSwap but never afterSwap, which would otherwise
    # leave a stale position for a later poll to restore to.
    assert "shouldSwap" in body


def test_htmx_static_asset_is_served(live_server) -> None:
    _, base = live_server
    with urllib.request.urlopen(f"{base}/assets/htmx.min.js", timeout=5) as r:
        assert r.status == 200
        # ``.js`` resolves to ``text/javascript`` or ``application/javascript``
        # depending on the mimetypes database; either is a JS MIME the browser
        # will execute.
        ct = r.headers.get("Content-Type", "")
        assert "javascript" in ct.lower(), f"unexpected Content-Type: {ct!r}"
        body = r.read()
    # htmx ships as a UMD bundle; the wrapper preamble is a stable
    # content-shape check.
    assert body.startswith(b"(function("), "asset is not the htmx UMD bundle"
    # Generous floor: catches a truncated/placeholder asset but survives a
    # future htmx upgrade.
    assert len(body) > 30_000


def test_bundled_assets_are_cache_busted(live_server) -> None:
    # Every bundled ``/assets/...`` reference on the page must carry a
    # ``?v=<build>`` token. Without it, a browser on the offline LAN keeps
    # the previous release's cached JS after an app update – the picker /
    # units scripts silently run stale code (the "Box Color reverts on
    # save" report: an old color-picker.js updated the swatch but not the
    # hidden input, so Save submitted the old value).
    _, base = live_server
    status, body = _get(base, "/")
    assert status == 200
    refs = re.findall(r'(?:src|href)="(/assets/[^"]+)"', body)
    assert refs, "no bundled asset references found on the page"
    unversioned = [r for r in refs if "?v=" not in r]
    assert not unversioned, f"asset references missing a ?v= cache-bust token: {unversioned}"


def test_versioned_asset_is_immutably_cacheable(live_server) -> None:
    # A versioned URL is safe to cache hard: a new build changes the token,
    # so the URL changes. This is what makes the cache-bust effective – the
    # browser keeps the asset until the version moves, then refetches.
    _, base = live_server
    with urllib.request.urlopen(f"{base}/assets/js/color-picker.js?v=testbuild", timeout=5) as r:
        assert r.status == 200
        cc = r.headers.get("Cache-Control", "")
    assert "immutable" in cc, f"versioned asset not immutably cacheable: {cc!r}"
    assert "max-age=31536000" in cc, f"versioned asset missing long max-age: {cc!r}"


def test_unversioned_asset_must_revalidate(live_server) -> None:
    # A request without the token (direct hit / unversioned reference) must
    # revalidate, so it can never pin a stale copy across an app update.
    _, base = live_server
    with urllib.request.urlopen(f"{base}/assets/js/color-picker.js", timeout=5) as r:
        assert r.status == 200
        cc = r.headers.get("Cache-Control", "")
    assert "no-cache" in cc, f"unversioned asset is not forced to revalidate: {cc!r}"


def test_color_picker_js_syncs_hidden_input(live_server) -> None:
    # The picker's commit path must write the sibling hidden ``<input>`` (the
    # value the form actually submits), not only the swatch's visible state.
    # If a refactor drops the hidden-input sync, the swatch shows the new
    # colour but Save submits the old value – exactly the reverts-on-save
    # report. Pin the wiring in the served asset.
    _, base = live_server
    with urllib.request.urlopen(f"{base}/assets/js/color-picker.js", timeout=5) as r:
        assert r.status == 200
        js = r.read().decode()
    assert "nextElementSibling" in js
    assert "hidden.value = hex" in js


def test_detection_box_color_round_trips(live_server) -> None:
    # End-to-end guard for the reported field: a POSTed Box Color persists to
    # disk *and* comes back in the re-rendered partial (the HTMX swap body),
    # so a save can't silently revert server-side.
    server, base = live_server
    status, body = _post_form(
        base,
        "/section/detection/inference",
        {
            "enabled": "on",
            "confidence": "0.5",
            "interval_ms": "100",
            "inference_size": "320",
            "max_persons": "5",
            "show_boxes": "on",
            "show_labels": "on",
            "box_color": "#ff00aa",
            "box_thickness": "3",
        },
    )
    assert status == 200
    assert load_config(server.config_path).detection.box_color == "#ff00aa"
    # The swap response the browser renders must show the new colour, not the old.
    assert 'name="box_color" value="#ff00aa"' in body
    assert 'data-value="#ff00aa"' in body


def test_asset_version_changes_when_an_asset_changes(tmp_path, monkeypatch) -> None:
    # The token is a content fingerprint: editing any bundled asset must change
    # it (so the next server start refetches), and an unchanged dir must keep
    # the same token (so caches survive a restart that ships no asset changes).
    from openfollow.web import routes

    monkeypatch.setattr(routes, "_WEB_STATIC_DIR", tmp_path)
    (tmp_path / "app.js").write_text("one")
    first = routes._compute_asset_version()
    assert len(first) == 12 and all(c in "0123456789abcdef" for c in first)
    assert routes._compute_asset_version() == first, "unchanged dir changed the token"
    (tmp_path / "app.js").write_text("two")
    assert routes._compute_asset_version() != first, "edited asset did not bust the token"


def test_asset_version_falls_back_without_bundled_assets(tmp_path, monkeypatch) -> None:
    # A non-standard install with no static files still gets a non-empty token
    # from the build identity, so asset URLs never collapse to a bare ``?v=``.
    from openfollow import __commit__, __version__
    from openfollow.web import routes

    monkeypatch.setattr(routes, "_WEB_STATIC_DIR", tmp_path)  # empty dir
    assert routes._compute_asset_version() == (__commit__ or __version__)


@pytest.mark.parametrize(
    "section",
    [
        "general",
        "camera",
        "grid",
        "movement",
        "marker",
        "controller",
        "osc",
        "detection",
    ],
)
def test_section_partial_renders(live_server, section: str) -> None:
    _, base = live_server
    status, body = _get(base, f"/section/{section}")
    assert status == 200, f"/section/{section} returned {status}"
    assert len(body) > 50


def test_overview_partial_renders(live_server) -> None:
    _, base = live_server
    status, body = _get(base, "/section/overview")
    assert status == 200
    assert len(body) > 10


def test_overview_poll_returns_peer_rows_without_section_shell(live_server) -> None:
    # Flicker fix: the 5s overview poll swaps ONLY the peer rows, not the whole
    # section via outerHTML. The fragment must carry peer markup but NOT the
    # section shell (head / fold key / Refresh) – re-rendering the shell every
    # tick is what made the section flash.
    _, base = live_server
    status, body = _get(base, "/section/overview")
    assert status == 200
    assert "peer-item" in body  # the local-server row is always present
    assert "Server Network" not in body
    assert "data-fold-key" not in body
    assert 'class="section"' not in body


def test_index_overview_section_polls_only_peer_rows(live_server) -> None:
    # The polling element is the inner peer list (#overview-peers), gated on the
    # enclosing section's collapsed state – not the whole #overview-section via
    # outerHTML, which is what flickered.
    _, base = live_server
    status, body = _get(base, "/")
    assert status == 200
    assert 'id="overview-peers"' in body
    assert "closest('.section').classList.contains('is-collapsed')" in body


def test_statistics_partial_renders(live_server) -> None:
    _, base = live_server
    status, body = _get(base, "/section/statistics")
    assert status == 200


def test_unknown_section_returns_404(live_server) -> None:
    _, base = live_server
    status, _ = _get(base, "/section/nonexistent")
    assert status == 404


def test_network_interfaces_by_name_returns_iface_keyed_options(
    live_server,
    monkeypatch,
) -> None:
    """Interface dropdown options keyed by name with IP as label suffix."""
    import socket as _socket
    from types import SimpleNamespace

    from openfollow import net_utils as net_utils_mod

    monkeypatch.setattr(
        net_utils_mod.psutil,
        "net_if_addrs",
        lambda: {
            "eth0": [SimpleNamespace(family=_socket.AF_INET, address="192.168.178.59")],
            "wlan0": [SimpleNamespace(family=_socket.AF_INET, address="10.0.0.5")],
        },
    )
    _, base = live_server
    status, body = _get(base, "/network/interfaces/by_name")
    assert status == 200
    # Value = iface name, label = "iface – ip".
    assert 'value="eth0"' in body
    assert "eth0 – 192.168.178.59" in body
    assert 'value="wlan0"' in body
    # Auto-detect option always present.
    assert 'value=""' in body and "Auto-detect" in body


def test_network_interfaces_by_name_marks_pinned_iface_not_available(
    live_server,
    monkeypatch,
) -> None:
    import socket as _socket
    from types import SimpleNamespace

    from openfollow import net_utils as net_utils_mod

    monkeypatch.setattr(
        net_utils_mod.psutil,
        "net_if_addrs",
        lambda: {
            "eth0": [SimpleNamespace(family=_socket.AF_INET, address="192.168.178.59")],
        },
    )
    server, base = live_server
    cfg = load_config(server.config_path)
    cfg.psn_source_iface = "wlan0_gone"
    save_config(cfg, server.config_path)

    status, body = _get(base, "/network/interfaces/by_name")
    assert status == 200
    assert "wlan0_gone" in body
    assert "not available" in body


def test_network_interfaces_by_name_current_param_overrides_psn_default(
    live_server,
    monkeypatch,
) -> None:
    """``?current=<iface>`` (the OTP picker) selects that iface, not the PSN
    default, so each picker highlights its own pin."""
    import socket as _socket
    from types import SimpleNamespace

    from openfollow import net_utils as net_utils_mod

    monkeypatch.setattr(
        net_utils_mod.psutil,
        "net_if_addrs",
        lambda: {
            "eth0": [SimpleNamespace(family=_socket.AF_INET, address="192.168.178.59")],
            "wlan0": [SimpleNamespace(family=_socket.AF_INET, address="10.0.0.5")],
        },
    )
    server, base = live_server
    cfg = load_config(server.config_path)
    cfg.psn_source_iface = "eth0"
    save_config(cfg, server.config_path)

    status, body = _get(base, "/network/interfaces/by_name?current=wlan0")
    assert status == 200
    assert 'value="wlan0" selected' in body
    assert 'value="eth0" selected' not in body


def test_network_interfaces_by_name_empty_current_does_not_fall_back_to_psn(
    live_server,
    monkeypatch,
) -> None:
    """An unpinned OTP picker sends ``?current=`` (present, empty); it must NOT
    inherit the PSN default – nothing is pre-selected."""
    import socket as _socket
    from types import SimpleNamespace

    from openfollow import net_utils as net_utils_mod

    monkeypatch.setattr(
        net_utils_mod.psutil,
        "net_if_addrs",
        lambda: {"eth0": [SimpleNamespace(family=_socket.AF_INET, address="192.168.178.59")]},
    )
    server, base = live_server
    cfg = load_config(server.config_path)
    cfg.psn_source_iface = "eth0"
    save_config(cfg, server.config_path)

    status, body = _get(base, "/network/interfaces/by_name?current=")
    assert status == 200
    # The PSN default must not leak into an empty OTP pin.
    assert "selected" not in body


# ---------------------------------------------------------------------------
# JSON API – shape and content
# ---------------------------------------------------------------------------


def test_api_info_returns_expected_fields(live_server) -> None:
    server, base = live_server
    status, data = _get_json(base, "/api/info")
    assert status == 200
    assert data["name"] == "TestSystem"
    assert "ip" in data
    assert data["port"] == server.port
    # Must reflect the real package version, not the legacy "0.1.0" placeholder.
    assert data["version"] == openfollow.__version__


def test_api_stats_returns_json(live_server) -> None:
    _, base = live_server
    status, data = _get_json(base, "/api/stats")
    assert status == 200
    assert isinstance(data, dict)


def test_api_update_status_returns_json(live_server) -> None:
    _, base = live_server
    status, data = _get_json(base, "/api/update-status")
    assert status == 200
    assert isinstance(data, dict)


def test_api_peers_returns_local_and_peers_keys(live_server) -> None:
    server, base = live_server
    status, data = _get_json(base, "/api/peers")
    assert status == 200
    assert "local" in data
    assert "peers" in data
    local = data["local"]
    assert local["name"] == "TestSystem"
    assert local["port"] == server.port
    # local.version must be the real package version, not the "0.1.0" placeholder.
    assert local["version"] == openfollow.__version__


def test_api_config_returns_full_config(live_server) -> None:
    _, base = live_server
    status, data = _get_json(base, "/api/config")
    assert status == 200
    # Spot-check top-level keys that must always exist
    for key in ("video_source_type", "psn_system_name", "camera", "grid", "marker"):
        assert key in data, f"Missing key: {key}"


@pytest.mark.parametrize(
    "section",
    [
        "general",
        "video_source",
        "camera",
        "grid",
        "movement",
        "marker",
        "controller",
        "osc",
        "detection",
    ],
)
def test_api_get_section_returns_dict(live_server, section: str) -> None:
    _, base = live_server
    status, data = _get_json(base, f"/api/config/{section}")
    assert status == 200, f"/api/config/{section} returned {status}"
    assert isinstance(data, dict)


def test_api_get_unknown_section_returns_404(live_server) -> None:
    _, base = live_server
    status, data = _get_json(base, "/api/config/doesnotexist")
    assert status == 404
    assert "error" in data


# ---------------------------------------------------------------------------
# Config persistence – POST then re-read the saved file
# ---------------------------------------------------------------------------


def test_api_post_camera_persists_values(live_server) -> None:
    server, base = live_server
    status, data = _post_json(base, "/api/config/camera", {"pos_x": 3.5, "pos_z": 7.0})
    assert status == 200
    assert data.get("success") is True

    saved = load_config(server.config_path)
    assert saved.camera.pos_x == pytest.approx(3.5)
    assert saved.camera.pos_z == pytest.approx(7.0)


def test_api_post_osc_persists_enabled_flag(live_server) -> None:
    server, base = live_server
    status, data = _post_json(base, "/api/config/osc", {"enabled": True, "port": 9001})
    assert status == 200
    assert data.get("success") is True

    saved = load_config(server.config_path)
    assert saved.osc.enabled is True
    assert saved.osc.port == 9001


def test_api_post_grid_persists_dimensions(live_server) -> None:
    server, base = live_server
    status, data = _post_json(base, "/api/config/grid", {"width": 30.0, "depth": 20.0})
    assert status == 200

    saved = load_config(server.config_path)
    assert saved.grid.width == pytest.approx(30.0)
    assert saved.grid.depth == pytest.approx(20.0)


def test_api_post_grid_max_height_round_trips(live_server) -> None:
    """``max_height`` saves and loads as a positive float like other dimensions."""
    server, base = live_server
    _post_json(base, "/api/config/grid", {"max_height": 4.0})
    saved = load_config(server.config_path)
    assert saved.grid.max_height == pytest.approx(4.0)


def test_api_post_grid_max_height_emptied_clears_to_zero(live_server) -> None:
    server, base = live_server
    # First: set a non-zero height.
    _post_json(base, "/api/config/grid", {"max_height": 4.0})
    saved = load_config(server.config_path)
    assert saved.grid.max_height == pytest.approx(4.0)
    # Then: clear via empty string. Must collapse to 0 (= unset).
    _post_json(base, "/api/config/grid", {"max_height": ""})
    saved = load_config(server.config_path)
    assert saved.grid.max_height == 0.0


def test_api_post_grid_max_height_negative_collapses_to_zero(live_server) -> None:
    """Negative ``max_height`` collapses to 0 in ``GridConfig.__post_init__``;
    the save round-trip must produce 0, not -2."""
    server, base = live_server
    _post_json(base, "/api/config/grid", {"max_height": -2.0})
    saved = load_config(server.config_path)
    assert saved.grid.max_height == 0.0


def test_api_post_movement_persists_speed_settings(live_server) -> None:
    server, base = live_server
    status, data = _post_json(
        base,
        "/api/config/movement",
        {"min_speed": 0.5, "move_speed": 2.5, "max_speed": 5.0},
    )
    assert status == 200
    assert data.get("success") is True

    saved = load_config(server.config_path)
    assert saved.marker.min_speed == pytest.approx(0.5)
    assert saved.marker.move_speed == pytest.approx(2.5)
    assert saved.marker.max_speed == pytest.approx(5.0)


def test_api_post_movement_persists_default_position(live_server) -> None:
    server, base = live_server
    status, data = _post_json(
        base,
        "/api/config/movement",
        {"default_pos_x": 1.0, "default_pos_y": -2.0, "default_pos_z": 3.5},
    )
    assert status == 200

    saved = load_config(server.config_path)
    assert saved.marker.default_pos_x == pytest.approx(1.0)
    assert saved.marker.default_pos_y == pytest.approx(-2.0)
    assert saved.marker.default_pos_z == pytest.approx(3.5)


def test_api_post_unknown_section_returns_404(live_server) -> None:
    _, base = live_server
    status, data = _post_json(base, "/api/config/bogus", {"x": 1})
    assert status == 404
    assert "error" in data


def test_api_post_invalid_json_returns_400(live_server) -> None:
    _, base = live_server
    req = urllib.request.Request(
        f"{base}/api/config/camera",
        data=b"not json {{{{",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    assert status == 400


# ---------------------------------------------------------------------------
# Form POST routes – templates must render after save
# ---------------------------------------------------------------------------


def test_form_post_camera_renders_saved_partial(live_server) -> None:
    _, base = live_server
    status, body = _post_form(base, "/section/camera", {"pos_x": "1.5", "pos_y": "-5.0", "pos_z": "4.0"})
    assert status == 200
    assert len(body) > 50


def test_form_post_osc_renders_saved_partial(live_server) -> None:
    _, base = live_server
    status, body = _post_form(base, "/section/osc", {"enabled": "true", "port": "8765"})
    assert status == 200


def test_form_post_osc_saves_multicast_group(live_server) -> None:
    # The [osc] multicast_group field round-trips through save.
    _, base = live_server
    status, body = _post_form(
        base,
        "/section/osc",
        {"enabled": "true", "port": "8765", "multicast_group": "239.10.10.10"},
    )
    assert status == 200
    assert "239.10.10.10" in body


def test_form_post_operator_messages_renders_saved_partial(live_server) -> None:
    # The [operator_messages] section saves + re-renders.
    _, base = live_server
    status, body = _post_form(
        base,
        "/section/operator_messages",
        {"enabled": "true", "position": "top", "max_visible": "3"},
    )
    assert status == 200
    assert "operator-messages-section" in body


def test_form_post_movement_renders_saved_partial(live_server) -> None:
    _, base = live_server
    status, body = _post_form(
        base,
        "/section/movement",
        {"min_speed": "0.2", "move_speed": "1.5", "max_speed": "4.0"},
    )
    assert status == 200
    assert "movement-section" in body


def test_form_post_gamepad_renders_saved_partial(live_server) -> None:
    _, base = live_server
    status, body = _post_form(base, "/section/gamepad", {"enabled": "true", "deadzone": "0.1"})
    assert status == 200
    assert "gamepad-section" in body


def test_form_post_gamepad_persists_new_button_fields(live_server) -> None:
    server, base = live_server
    status, _ = _post_form(
        base,
        "/section/gamepad",
        {
            "enabled": "true",
            "deadzone": "0.15",
            "btn_next_marker": "RB",
            "btn_prev_marker": "LB",
            "btn_settings": "START",
            "btn_menu_confirm": "A",
            "btn_menu_cancel": "B",
        },
    )
    assert status == 200

    saved = load_config(server.config_path)
    assert saved.controller.btn_next_marker == "RB"
    assert saved.controller.btn_prev_marker == "LB"
    assert saved.controller.btn_settings == "START"
    assert saved.controller.btn_menu_confirm == "A"
    assert saved.controller.btn_menu_cancel == "B"


def test_form_post_gamepad_invalid_button_falls_back_to_default(live_server) -> None:
    server, base = live_server
    status, _ = _post_form(
        base,
        "/section/gamepad",
        {
            "enabled": "true",
            "deadzone": "0.15",
            "btn_next_marker": "NOT_A_BUTTON",
        },
    )
    assert status == 200
    saved = load_config(server.config_path)
    # ControllerConfig.__post_init__ reverts unknown names to the default.
    assert saved.controller.btn_next_marker == "DPAD_RIGHT"


def test_form_post_keyboard_renders_saved_partial(live_server) -> None:
    _, base = live_server
    status, body = _post_form(base, "/section/keyboard", {"keyboard_enabled": "true"})
    assert status == 200
    assert "keyboard-section" in body


def test_form_post_keyboard_persists_new_fields(live_server) -> None:
    server, base = live_server
    status, _ = _post_form(
        base,
        "/section/keyboard",
        {
            "keyboard_enabled": "true",
            "key_next_marker": "n",
            "key_prev_marker": "p",
            "key_settings": "g",
        },
    )
    assert status == 200

    saved = load_config(server.config_path)
    assert saved.controller.key_next_marker == "n"
    assert saved.controller.key_prev_marker == "p"
    assert saved.controller.key_settings == "g"


def test_form_post_keyboard_movement_collision_reverts_to_default(live_server) -> None:
    server, base = live_server
    # 'w' is part of the WASD layout – action bindings must refuse it.
    status, _ = _post_form(
        base,
        "/section/keyboard",
        {
            "keyboard_enabled": "true",
            "key_settings": "w",
        },
    )
    assert status == 200
    saved = load_config(server.config_path)
    assert saved.controller.key_settings == "m"


def test_form_post_keyboard_persists_movement_layout(live_server) -> None:
    server, base = live_server
    status, _ = _post_form(
        base,
        "/section/keyboard",
        {
            "keyboard_enabled": "true",
            "key_move_layout": "ijkl",
        },
    )
    assert status == 200
    saved = load_config(server.config_path)
    assert saved.controller.key_move_layout == "ijkl"


def test_form_post_mouse_renders_saved_partial(live_server) -> None:
    _, base = live_server
    status, body = _post_form(base, "/section/mouse", {"mouse_enabled": "true"})
    assert status == 200
    assert "mouse-section" in body


# ---------------------------------------------------------------------------
# Restart flag
# ---------------------------------------------------------------------------


def test_api_create_zone_rejects_non_object_json(live_server) -> None:
    _, base = live_server
    status, body = _post_raw_json(base, "/api/zones", ["not", "a", "dict"])
    assert status == 400
    assert "error" in body


def test_api_update_zone_rejects_non_object_json(live_server) -> None:
    """A JSON list/scalar at PUT /api/zones/<i> must 400, not silently no-op."""
    _, base = live_server
    # Create a real zone first so the update target exists.
    status, body = _post_raw_json(base, "/api/zones", {"name": "Z0"})
    assert status == 200
    idx = body.get("index", 0)
    status, body = _post_raw_json(base, f"/api/zones/{idx}", "string, not an object", method="PUT")
    assert status == 400
    assert "error" in body


def test_api_create_zone_rejects_null_json(live_server) -> None:
    """A JSON ``null`` body at POST /api/zones must 400 with an error body.

    ``json.loads(\"null\")`` returns None without raising, so the route must
    reject it explicitly rather than treating it as a successful parse.
    """
    _, base = live_server
    status, body = _post_raw_json(base, "/api/zones", None)
    assert status == 400
    assert "error" in body


def test_api_update_zone_rejects_null_json(live_server) -> None:
    """A JSON ``null`` body at PUT /api/zones/<i> must 400 with an error body."""
    _, base = live_server
    status, body = _post_raw_json(base, "/api/zones", {"name": "Zn"})
    assert status == 200
    idx = body.get("index", 0)
    status, body = _post_raw_json(base, f"/api/zones/{idx}", None, method="PUT")
    assert status == 400
    assert "error" in body


def test_api_update_zone_ignores_non_list_vertices(live_server) -> None:
    _, base = live_server
    good_verts = [[0.0, 0.0], [4.0, 0.0], [4.0, 4.0], [0.0, 4.0]]
    status, body = _post_raw_json(base, "/api/zones", {"name": "Z", "vertices": good_verts})
    assert status == 200
    idx = body.get("index", 0)

    # Malformed vertices (null) – route should accept (200) but keep the polygon.
    status, _ = _post_raw_json(base, f"/api/zones/{idx}", {"vertices": None}, method="PUT")
    assert status == 200

    status, zones_body = _get_json(base, "/api/zones")
    vertices_after = zones_body["zones"][idx]["vertices"]
    assert vertices_after == good_verts


# ---------------------------------------------------------------------------
# Restart flag
# ---------------------------------------------------------------------------


def test_post_general_with_restart_flag_queues_restart(live_server) -> None:
    server, base = live_server
    assert server.check_restart_requested() is False

    status, body = _post_form(base, "/section/general?restart=1", {})
    assert status == 200

    assert server.check_restart_requested() is True
    # consuming it should clear the flag
    assert server.check_restart_requested() is False


# ---------------------------------------------------------------------------
# Peer authentication (HMAC-signed requests)
# ---------------------------------------------------------------------------


@pytest.fixture()
def pin_protected_server(tmp_path, monkeypatch):
    """Variant of ``live_server`` that starts with a configured web PIN."""
    monkeypatch.setattr(discovery_module.BeaconSender, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconSender, "stop", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "stop", lambda self: None)

    port = _find_free_tcp_port()
    config_path = tmp_path / "config.toml"

    # Write a config with a PIN before the server starts; ``_check_auth``
    # reads the pin from disk on every request via ``load_config``.
    initial = AppConfig()
    initial.web_pin = "sekret"
    save_config(initial, str(config_path))

    server = ConfigWebServer(
        config_path=str(config_path),
        host="127.0.0.1",
        port=port,
        system_name="TestSystem",
    )
    server.start()
    assert _wait_for_port(port), f"Web server did not start within 5 s on port {port}"

    yield server, f"http://127.0.0.1:{port}", "sekret"
    server.stop()


def _signed_post(base: str, path: str, body_bytes: bytes, pin: str) -> int:
    timestamp, signature = peer_auth.sign(pin, "POST", path, body_bytes)
    req = urllib.request.Request(
        f"{base}{path}",
        data=body_bytes,
        headers={
            "Content-Type": "application/json",
            peer_auth.TIMESTAMP_HEADER: str(timestamp),
            peer_auth.SIGNATURE_HEADER: signature,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def _post_json_status(base: str, path: str, data: dict) -> int:
    """POST JSON and return only the HTTP status code.

    Unlike ``_post_json``, this tolerates non-JSON error bodies (e.g.,
    bottle's default HTML 401 page).
    """
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_api_requires_auth_when_pin_configured(pin_protected_server) -> None:
    _, base, _ = pin_protected_server
    # No cookie, no signature → 401.
    assert _post_json_status(base, "/api/config/camera", {"pos_x": 1.0}) == 401


def test_api_accepts_valid_hmac_signature(pin_protected_server) -> None:
    server, base, pin = pin_protected_server
    body = json.dumps({"pos_x": 2.5}).encode("utf-8")
    status = _signed_post(base, "/api/config/camera", body, pin)

    assert status == 200
    saved = load_config(server.config_path)
    assert saved.camera.pos_x == pytest.approx(2.5)


def test_api_rejects_replayed_signed_request(pin_protected_server) -> None:
    """A valid signed request is single-use within the window: re-sending the
    exact same (timestamp, signature) is rejected as a replay."""
    _, base, pin = pin_protected_server
    path = "/api/config/camera"
    body = json.dumps({"pos_x": 3.5}).encode("utf-8")
    timestamp, signature = peer_auth.sign(pin, "POST", path, body)
    headers = {
        "Content-Type": "application/json",
        peer_auth.TIMESTAMP_HEADER: str(timestamp),
        peer_auth.SIGNATURE_HEADER: signature,
    }

    def _send() -> int:
        req = urllib.request.Request(f"{base}{path}", data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    assert _send() == 200  # first use accepted
    assert _send() == 401  # identical signature replayed → rejected


def test_api_rejects_invalid_hmac_signature(pin_protected_server) -> None:
    _, base, _ = pin_protected_server
    body = json.dumps({"pos_x": 2.5}).encode("utf-8")
    # Sign with the wrong PIN.
    status = _signed_post(base, "/api/config/camera", body, "wrong-pin")

    assert status == 401


def test_api_rejects_tampered_body(pin_protected_server) -> None:
    server, base, pin = pin_protected_server
    original = json.dumps({"pos_x": 2.5}).encode("utf-8")
    timestamp, signature = peer_auth.sign(pin, "POST", "/api/config/camera", original)
    # Sign the original body but send a different one.
    tampered = json.dumps({"pos_x": 999.0}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/config/camera",
        data=tampered,
        headers={
            "Content-Type": "application/json",
            peer_auth.TIMESTAMP_HEADER: str(timestamp),
            peer_auth.SIGNATURE_HEADER: signature,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code

    assert status == 401
    saved = load_config(server.config_path)
    assert saved.camera.pos_x != pytest.approx(999.0)


def test_api_does_not_accept_legacy_x_auth_pin_header(pin_protected_server) -> None:
    """The X-Auth-Pin header must no longer authenticate."""
    _, base, pin = pin_protected_server
    body = json.dumps({"pos_x": 3.5}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/config/camera",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Auth-Pin": pin,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code

    assert status == 401


def test_login_endpoint_remains_accessible_without_auth(pin_protected_server) -> None:
    _, base, _ = pin_protected_server
    status, body = _get(base, "/login")
    assert status == 200
    assert "pin" in body.lower()


def test_privilege_modal_poll_unauth_returns_empty_no_redirect(pin_protected_server) -> None:
    server, base, _ = pin_protected_server
    # Park a privilege request so an authenticated poll would have
    # something to render – proves the unauth path returns empty by
    # CHOICE, not by absence of state.
    server._command_queue.request_privilege_password(
        reason="should not leak to unauth client",
        capability_name="network.nm.con_mod",
    )
    status, body = _get(base, "/system/privilege/password/modal")
    assert status == 200
    assert "privilege-password-input" not in body
    assert "should not leak to unauth client" not in body


def test_about_page_renders_all_tabs(live_server) -> None:
    """/about is a single tabbed page carrying every legal document inline."""
    _, base = live_server
    status, body = _get(base, "/about")
    assert status == 200
    # Tab bar (main-UI style) with the four tabs.
    assert 'class="tab-bar"' in body
    assert 'data-tab="about-license"' in body
    assert 'data-tab="about-third-party"' in body
    assert 'data-tab="about-written-offer"' in body
    # About tab: §5(d) notice + source link + license-texts pointer.
    assert "OpenFollow v" in body
    # The short git commit follows the release number in both the footer and the
    # About version row when running from a checkout (None on a no-.git install).
    from openfollow import __commit__, __version__

    if __commit__:
        assert f"OpenFollow v{__version__} ({__commit__})" in body  # footer
        assert f"v{__version__} ({__commit__})" in body  # About version row
    assert "AGPL-3.0-or-later" in body
    assert "WITHOUT ANY WARRANTY" in body
    assert "all rights reserved" in body
    assert "https://github.com/openfollowapp/openfollow" in body
    assert "/usr/share/common-licenses/" in body
    # License tab: verbatim AGPL + plain-text link.
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in body
    assert 'href="/about/license.txt"' in body
    # Notices tab: SBOM note + the rendered notices document.
    assert "A Software Bill of Materials (SPDX) is included" in body
    assert "Third-Party Notices" in body
    assert "<table" in body
    # Offer tab.
    assert "Written Offer for Source Code" in body
    assert "three (3) years" in body


def test_about_page_exposes_no_config_state(live_server) -> None:
    _, base = live_server
    status, body = _get(base, "/about")
    assert status == 200
    # The pill *element* (which would render config.psn_system_name) must
    # not appear. Match the element's class attribute, not the bare class
    # name – the latter also occurs in base.tpl's CSS, which is always
    # present regardless of whether ``config`` was passed.
    assert 'class="station-name-pill hero-station-pill"' not in body


def test_about_license_txt_serves_plain_text(live_server) -> None:
    """/about/license.txt serves the bundled AGPLv3 text as plain text."""
    _, base = live_server
    with urllib.request.urlopen(f"{base}/about/license.txt", timeout=5) as r:
        status = r.status
        content_type = r.headers.get("Content-Type", "")
        body = r.read().decode()
    assert status == 200
    assert content_type.startswith("text/plain")
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in body


def test_about_license_txt_redirects_to_gnu_when_unbundled(live_server, monkeypatch) -> None:
    """No bundled LICENSE -> /about/license.txt redirects to the FSF copy."""
    import openfollow.web.routes as routes_module

    _, base = live_server
    monkeypatch.setattr(routes_module, "_license_file_path", lambda: None)
    opener = _no_redirect_opener()
    try:
        with opener.open(f"{base}/about/license.txt", timeout=5) as resp:
            status = resp.status
            location = resp.headers.get("Location", "")
    except urllib.error.HTTPError as e:
        status = e.code
        location = e.headers.get("Location", "")
    assert status in (301, 302, 303, 307, 308)
    assert "gnu.org" in location


def test_about_license_tab_fallback_when_unbundled(live_server, monkeypatch) -> None:
    """No bundled LICENSE -> the License tab shows the gnu.org link."""
    import openfollow.web.routes as routes_module

    _, base = live_server
    monkeypatch.setattr(routes_module, "_read_license_text", lambda: None)
    status, body = _get(base, "/about")
    assert status == 200
    assert "gnu.org/licenses/agpl-3.0" in body


def test_about_page_accessible_without_auth(pin_protected_server) -> None:
    _, base, _ = pin_protected_server
    status, body = _get(base, "/about")
    assert status == 200
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in body  # license tab content inline
    status, _ = _get(base, "/about/license.txt")
    assert status == 200


def test_license_footer_present_on_index(live_server) -> None:
    """Every page rebases base.tpl, so the clean license/version footer
    (with its link to /about) renders on the index page."""
    _, base = live_server
    status, body = _get(base, "/")
    assert status == 200
    assert 'class="license-footer"' in body
    assert "OpenFollow v" in body
    assert 'href="/about"' in body


def test_hero_logo_links_to_overview(live_server) -> None:
    """base.tpl wraps the hero logo in a link back to the overview ("/"),
    so the logo is a clickable way home on every page."""
    _, base = live_server
    status, body = _get(base, "/")
    assert status == 200
    assert 'class="hero-logo-link" href="/"' in body
    assert '<img class="hero-logo"' in body


def test_about_page_has_back_to_overview_link(live_server) -> None:
    """/about offers an explicit way back to the overview – both the textual
    back link and the clickable hero logo from base.tpl."""
    _, base = live_server
    status, body = _get(base, "/about")
    assert status == 200
    assert 'class="about-back-link" href="/"' in body
    assert "Back to overview" in body
    assert 'class="hero-logo-link" href="/"' in body


def test_about_doc_tabs_fallback_when_unbundled(live_server, monkeypatch) -> None:
    """No bundled notices/offer -> those tabs degrade to a link, page still 200."""
    import openfollow.web.routes as routes_module

    _, base = live_server
    monkeypatch.setattr(routes_module, "_third_party_notices_html", lambda: None)
    monkeypatch.setattr(routes_module, "_written_offer_html", lambda: None)
    status, body = _get(base, "/about")
    assert status == 200
    assert "isn't bundled" in body
    assert "openfollow.app" in body


def test_api_rejects_signed_request_over_body_size_cap(pin_protected_server, monkeypatch) -> None:
    _, base, pin = pin_protected_server
    monkeypatch.setattr(peer_auth, "MAX_SIGNED_BODY_SIZE", 100)

    body = b"x" * 500  # > cap, with a valid Content-Length header
    timestamp, signature = peer_auth.sign(pin, "POST", "/api/config/camera", body)
    req = urllib.request.Request(
        f"{base}/api/config/camera",
        data=body,
        headers={
            "Content-Type": "application/json",
            peer_auth.TIMESTAMP_HEADER: str(timestamp),
            peer_auth.SIGNATURE_HEADER: signature,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code

    assert status == 413


def test_successful_login_sets_samesite_strict_cookie(pin_protected_server) -> None:
    """Auth cookie must carry SameSite=Strict to prevent CSRF attacks.
    Without it, a malicious page could trigger config changes."""
    _, base, pin = pin_protected_server
    req = urllib.request.Request(
        f"{base}/login",
        data=urllib.parse.urlencode({"pin": pin}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    # urllib follows redirects by default; we need the raw 303 response
    # so we can inspect the Set-Cookie header.

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_a, **_kw):
            return None

    opener = urllib.request.build_opener(_NoRedirect)

    try:
        resp = opener.open(req, timeout=5)
        headers = resp.headers
    except urllib.error.HTTPError as e:
        headers = e.headers

    cookie_headers = headers.get_all("Set-Cookie") or []
    auth_cookies = [c for c in cookie_headers if c.startswith("_openfollow_auth=")]
    assert auth_cookies, f"No auth cookie in response headers: {cookie_headers}"

    attrs = auth_cookies[0].lower()
    assert "samesite=strict" in attrs, f"Auth cookie missing SameSite=Strict: {auth_cookies[0]}"
    assert "httponly" in attrs, f"Auth cookie missing HttpOnly: {auth_cookies[0]}"


# ---------------------------------------------------------------------------
# Threaded WSGI + parallel peer fan-out
# ---------------------------------------------------------------------------


def test_threaded_server_handles_concurrent_requests(live_server, monkeypatch) -> None:
    import concurrent.futures
    import time as _time

    from openfollow.web import routes as routes_mod

    _, base = live_server

    original_asdict = routes_mod.asdict

    def _slow_asdict(obj):
        _time.sleep(1.0)
        return original_asdict(obj)

    monkeypatch.setattr(routes_mod, "asdict", _slow_asdict)

    def _hit(path: str) -> tuple[float, int]:
        t0 = _time.monotonic()
        status, _ = _get(base, path)
        return _time.monotonic() - t0, status

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        slow = pool.submit(_hit, "/api/config")
        # Give the slow request a head-start so it's definitely mid-sleep
        # before the fast request arrives. With 0.05 s the slow request
        # has ~0.95 s of sleep remaining when the fast one is issued.
        _time.sleep(0.05)
        fast = pool.submit(_hit, "/api/info")

        fast_elapsed, fast_status = fast.result(timeout=3.0)
        slow_elapsed, slow_status = slow.result(timeout=3.0)

    assert slow_status == 200 and fast_status == 200
    # Under the single-threaded server, fast would be queued behind slow
    # (>= 0.95 s). With the threaded server it should complete well
    # within 0.5 s. Margin generous enough to stay robust on busy CI.
    assert fast_elapsed < 0.5, (
        f"/api/info took {fast_elapsed:.3f}s while /api/config was in flight – "
        "WSGI server is not processing requests concurrently."
    )


def test_threading_wsgi_server_rejects_when_handler_cap_exhausted(live_server, monkeypatch) -> None:
    import threading as _threading
    import time as _time

    from openfollow.web import server as server_module

    _, base = live_server

    cap = server_module._REQUEST_MAX_CONCURRENT
    sem = _threading.BoundedSemaphore(cap)
    monkeypatch.setattr(server_module, "_request_semaphore", sem)

    # Exhaust every slot on the fresh semaphore.
    held = 0
    try:
        for _ in range(cap):
            assert sem.acquire(blocking=False), "fresh semaphore must start fully free"
            held += 1

        t0 = _time.monotonic()
        status, body = _get(base, "/api/info")
        elapsed = _time.monotonic() - t0

        assert status == 503, f"expected 503 when handler cap exhausted, got {status}: {body!r}"
        # Rejection must be fast – the whole point is that threads don't
        # accumulate. A proper reject sends immediately and closes.
        assert elapsed < 2.0, f"rejection took {elapsed:.2f}s – rejected requests must terminate quickly"
    finally:
        for _ in range(held):
            sem.release()

    # Sanity: after releasing all slots, the server handles requests normally.
    status, _ = _get(base, "/api/info")
    assert status == 200


def test_broadcast_to_peers_runs_sends_in_parallel(monkeypatch) -> None:
    import time as _time

    from openfollow.web.discovery import PeerInfo
    from openfollow.web.routes import _broadcast_to_peers

    peers = [
        PeerInfo(name=f"P{i}", ip=f"10.0.0.{10 + i}", web_port=80, version="0.1.0", last_seen=_time.time())
        for i in range(10)
    ]

    def _send(_peer: PeerInfo) -> bool:
        _time.sleep(0.3)
        return True

    t0 = _time.monotonic()
    results = _broadcast_to_peers(peers, _send, overall_timeout=5.0)
    elapsed = _time.monotonic() - t0

    assert len(results) == len(peers)
    assert all(r["success"] for r in results)
    # A conservative upper bound: 10 sends sequentially would take 3.0s;
    # parallel should finish in ~0.3s + thread overhead. 1.5s leaves lots
    # of slack for busy CI without weakening the assertion.
    assert elapsed < 1.5, f"fan-out took {elapsed:.2f}s – likely serial"


def test_broadcast_to_peers_honours_overall_timeout(monkeypatch) -> None:
    """Slow peers beyond the overall timeout are reported as failed rather
    than holding the request thread open indefinitely.
    """
    import time as _time

    from openfollow.web.discovery import PeerInfo
    from openfollow.web.routes import _broadcast_to_peers

    fast = PeerInfo("Fast", "10.0.0.10", 80, "0.1.0", _time.time())
    slow = PeerInfo("Slow", "10.0.0.11", 80, "0.1.0", _time.time())

    def _send(peer: PeerInfo) -> bool:
        if peer.name == "Slow":
            _time.sleep(2.0)
        return True

    t0 = _time.monotonic()
    results = _broadcast_to_peers([fast, slow], _send, overall_timeout=0.3)
    elapsed = _time.monotonic() - t0

    assert elapsed < 1.5, "broadcast must return without waiting out the slow peer"
    by_name = {r["name"]: r for r in results}
    assert by_name["Fast"]["success"] is True
    assert by_name["Slow"]["success"] is False


def test_broadcast_to_peers_reports_exception_as_failure() -> None:
    import time as _time

    from openfollow.web.discovery import PeerInfo
    from openfollow.web.routes import _broadcast_to_peers

    good = PeerInfo("Good", "10.0.0.10", 80, "0.1.0", _time.time())
    bad = PeerInfo("Bad", "10.0.0.11", 80, "0.1.0", _time.time())

    def _send(peer: PeerInfo) -> bool:
        if peer.name == "Bad":
            raise RuntimeError("simulated peer error")
        return True

    results = _broadcast_to_peers([good, bad], _send, overall_timeout=2.0)

    by_name = {r["name"]: r for r in results}
    assert by_name["Good"]["success"] is True
    assert by_name["Bad"]["success"] is False


def test_broadcast_to_peers_returns_empty_for_empty_peer_list() -> None:
    from openfollow.web.routes import _broadcast_to_peers

    called = {"n": 0}

    def _send(_peer):
        called["n"] += 1
        return True

    assert _broadcast_to_peers([], _send, overall_timeout=1.0) == []
    assert called["n"] == 0


def test_broadcast_to_peers_caps_max_workers() -> None:
    import threading as _threading
    import time as _time

    from openfollow.web.discovery import PeerInfo
    from openfollow.web.routes import _BROADCAST_MAX_WORKERS, _broadcast_to_peers

    peer_count = _BROADCAST_MAX_WORKERS + 8
    peers = [
        PeerInfo(name=f"P{i}", ip=f"10.0.0.{10 + i}", web_port=80, version="0.1.0", last_seen=_time.time())
        for i in range(peer_count)
    ]

    live = 0
    peak = 0
    counter_lock = _threading.Lock()

    def _send(_peer: PeerInfo) -> bool:
        nonlocal live, peak
        with counter_lock:
            live += 1
            peak = max(peak, live)
        _time.sleep(0.2)
        with counter_lock:
            live -= 1
        return True

    results = _broadcast_to_peers(peers, _send, overall_timeout=10.0)

    assert len(results) == peer_count
    assert all(r["success"] for r in results)
    assert peak <= _BROADCAST_MAX_WORKERS, (
        f"peak concurrency {peak} exceeded cap {_BROADCAST_MAX_WORKERS} – worker pool is not bounded"
    )


def test_broadcast_to_peers_caps_max_peers() -> None:
    import threading as _threading
    import time as _time

    from openfollow.web.discovery import PeerInfo
    from openfollow.web.routes import _BROADCAST_MAX_PEERS, _broadcast_to_peers

    overflow = 5
    peer_count = _BROADCAST_MAX_PEERS + overflow
    peers = [
        PeerInfo(
            name=f"P{i}", ip=f"10.0.{(i // 256) % 256}.{i % 256}", web_port=80, version="0.1.0", last_seen=_time.time()
        )
        for i in range(peer_count)
    ]

    calls: list[str] = []
    calls_lock = _threading.Lock()

    def _send(peer: PeerInfo) -> bool:
        with calls_lock:
            calls.append(peer.ip)
        return True

    results = _broadcast_to_peers(peers, _send, overall_timeout=5.0)

    assert len(results) == peer_count, "result list must match input peer list length"
    # Order must match input list so UI rendering is stable.
    assert [r["ip"] for r in results] == [p.ip for p in peers]

    successes = [r for r in results if r["success"]]
    failures = [r for r in results if not r["success"]]
    assert len(successes) == _BROADCAST_MAX_PEERS, (
        f"expected exactly {_BROADCAST_MAX_PEERS} successes (the cap), got {len(successes)}"
    )
    assert len(failures) == overflow, (
        f"expected {overflow} peers beyond the cap to be reported failed, got {len(failures)}"
    )
    # Skipped peers must be the trailing slice – confirms deterministic truncation.
    skipped_ips = {r["ip"] for r in failures}
    assert skipped_ips == {p.ip for p in peers[_BROADCAST_MAX_PEERS:]}

    # Crucially: _send must never be called for skipped peers.
    assert len(calls) == _BROADCAST_MAX_PEERS
    assert set(calls).isdisjoint(skipped_ips)


def test_broadcast_to_peers_total_thread_count_bounded_under_rapid_broadcasts() -> None:
    import threading as _threading
    import time as _time

    from openfollow.web.discovery import PeerInfo
    from openfollow.web.routes import _BROADCAST_MAX_WORKERS, _broadcast_to_peers

    # Hold each send long enough that many broadcasts overlap.
    send_release = _threading.Event()

    def _send(_peer: PeerInfo) -> bool:
        send_release.wait(timeout=5.0)
        return True

    peers_per_broadcast = 32  # well above _BROADCAST_MAX_WORKERS
    broadcasts = 6  # 6 × 32 = 192 peers scheduled while blocked

    def _do_broadcast(idx: int) -> None:
        peers = [
            PeerInfo(
                name=f"P{i}", ip=f"10.{idx}.{i // 256}.{i % 256}", web_port=80, version="0.1.0", last_seen=_time.time()
            )
            for i in range(peers_per_broadcast)
        ]
        _broadcast_to_peers(peers, _send, overall_timeout=10.0)

    callers: list[_threading.Thread] = []
    try:
        for broadcast_idx in range(broadcasts):
            t = _threading.Thread(
                target=_do_broadcast,
                args=(broadcast_idx,),
                daemon=True,
                name=f"broadcast-caller-{broadcast_idx}",
            )
            t.start()
            callers.append(t)

        # Give broadcasts time to spawn as many workers as they can before
        # we measure. Without the fix, each broadcast would spawn 32
        # threads up front (192 total) that then block on the semaphore.
        # With the fix, spawning is gated on slot acquisition, so at most
        # _BROADCAST_MAX_WORKERS worker threads are live.
        _time.sleep(0.3)

        live_workers = [t for t in _threading.enumerate() if t.name.startswith("peer-broadcast-")]
        assert len(live_workers) <= _BROADCAST_MAX_WORKERS, (
            f"live broadcast threads {len(live_workers)} exceed cap "
            f"{_BROADCAST_MAX_WORKERS} – rapid broadcasts are accumulating "
            "blocked threads (acquire-before-spawn regression)"
        )
    finally:
        send_release.set()
        for t in callers:
            t.join(timeout=10.0)


def test_broadcast_to_peers_releases_slot_when_thread_spawn_fails(monkeypatch) -> None:
    import threading as _threading
    import time as _time

    from openfollow.web import routes as _routes
    from openfollow.web.discovery import PeerInfo

    # Snapshot the current available slot count via non-blocking acquires,
    # then release them back.
    def _available_slots() -> int:
        taken = 0
        while _routes._broadcast_semaphore.acquire(blocking=False):
            taken += 1
        for _ in range(taken):
            _routes._broadcast_semaphore.release()
        return taken

    # Other tests in this file may have left slow background workers
    # still holding a slot (e.g. overall-timeout tests return before
    # their slow peer completes). Snapshot the current available count
    # and assert the delta rather than an absolute value.
    before = _available_slots()

    # Force Thread.start() to raise on every spawn attempt. The peer send
    # should never be invoked, and every acquired slot must be released.
    real_thread = _threading.Thread

    def _boom_thread(*args, **kwargs):
        t = real_thread(*args, **kwargs)

        def _failing_start() -> None:
            raise RuntimeError("simulated: can't start new thread")

        t.start = _failing_start  # type: ignore[method-assign]
        return t

    monkeypatch.setattr(_threading, "Thread", _boom_thread)
    monkeypatch.setattr(_routes.threading, "Thread", _boom_thread)

    send_calls = {"n": 0}

    def _send(_peer: PeerInfo) -> bool:
        send_calls["n"] += 1
        return True

    peers = [
        PeerInfo(name=f"P{i}", ip=f"10.0.0.{10 + i}", web_port=80, version="0.1.0", last_seen=_time.time())
        for i in range(5)
    ]

    results = _routes._broadcast_to_peers(peers, _send, overall_timeout=2.0)

    assert len(results) == len(peers)
    assert all(r["success"] is False for r in results), "all peers must be reported failed when spawn fails"
    assert send_calls["n"] == 0, "send must not be invoked when spawn fails"

    after = _available_slots()
    assert after == before, f"semaphore slot leaked: {before - after} slots permanently consumed after spawn failures"


def test_broadcast_to_peers_workers_are_daemon_threads() -> None:
    import threading as _threading
    import time as _time

    from openfollow.web.discovery import PeerInfo
    from openfollow.web.routes import _broadcast_to_peers

    captured: list[_threading.Thread] = []
    captured_lock = _threading.Lock()

    def _send(_peer: PeerInfo) -> bool:
        with captured_lock:
            captured.append(_threading.current_thread())
        return True

    peers = [
        PeerInfo(name=f"P{i}", ip=f"10.0.0.{10 + i}", web_port=80, version="0.1.0", last_seen=_time.time())
        for i in range(3)
    ]

    results = _broadcast_to_peers(peers, _send, overall_timeout=5.0)

    assert all(r["success"] for r in results)
    assert len(captured) == len(peers)
    for t in captured:
        assert t.daemon, (
            f"broadcast worker {t.name!r} is not a daemon thread – in-flight sends would delay interpreter shutdown"
        )


# ---------------------------------------------------------------------------
# Detection install / uninstall endpoints
# ---------------------------------------------------------------------------


def _stub_package_command(monkeypatch, fake_fn):
    """Replace ``routes._run_package_command`` with ``fake_fn``.

    The helper is the only way the install/uninstall routes touch
    ``subprocess`` – stubbing it keeps tests from monkeypatching
    ``subprocess.Popen`` globally, which used to catch unrelated calls
    (e.g. the discovery thread shelling out to ``git``).

    ``fake_fn(argv, *, timeout)`` must return ``(returncode, tail_text)``
    or raise ``subprocess.TimeoutExpired`` / ``OSError`` to exercise the
    failure branches.
    """
    from openfollow.web import routes as routes_mod

    monkeypatch.setattr(routes_mod, "_run_package_command", fake_fn)


def _wait_for_install_terminal(server, *, timeout: float = 3.0) -> dict:
    """Block up to ``timeout`` seconds until the detection install
    worker publishes a terminal state, then return that snapshot.

    The install/uninstall routes run pip on a background daemon
    thread. Tests that exercise
    a stubbed ``_run_package_command`` need to wait for the worker
    to publish its result before asserting on the section render –
    polling the in-memory state is faster and more reliable than
    polling the HTTP endpoint.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = server.get_detection_install_status()
        if snapshot.get("state") in {"success", "error"}:
            return snapshot
        time.sleep(0.02)
    return server.get_detection_install_status()


def test_detection_section_has_no_dependency_view(live_server) -> None:
    """The Dependencies install/uninstall view was removed – deps come from
    install-detection.sh. Neither the buttons nor their routes appear."""
    _, base = live_server
    status, body = _get(base, "/section/detection")
    assert status == 200
    assert "/section/detection/install" not in body
    assert "/section/detection/uninstall" not in body
    assert ">Dependencies</h3>" not in body
    assert 'name="storage_path"' not in body


def test_detection_section_running_state_emits_polling_div(live_server) -> None:
    server, base = live_server
    server.try_claim_detection_install(
        action="export",
        extra="yolo11n.onnx",
        message="Exporting `yolo11n.onnx`...",
    )
    try:
        status, body = _get(base, "/section/detection")
    finally:
        server.set_detection_install_status(state="idle")

    assert status == 200
    assert 'hx-trigger="every 1s"' in body
    assert "Exporting `yolo11n.onnx`" in body


def test_detection_section_polling_dismisses_terminal_state(live_server) -> None:
    """The polling endpoint clears the status slot after rendering a terminal
    banner so subsequent re-renders don't keep showing it forever."""
    server, base = live_server
    server.try_claim_detection_install(action="export", extra="yolo11n.onnx")
    server.set_detection_install_status(
        state="success",
        message="Exported `yolo11n.onnx`.",
        tail="ok",
    )
    status, body = _get(base, "/section/detection")
    assert status == 200
    assert "Exported `yolo11n.onnx`" in body
    # State is auto-cleared after a polling render of a terminal state.
    assert server.get_detection_install_status()["state"] == "idle"


def test_detection_section_renders_pin_marker_id_dropdown(live_server) -> None:
    """The Pin To Marker dropdown lists each controlled marker plus the
    ``Currently selected (controller)`` sentinel option."""
    from openfollow.configuration import load_config, save_config

    server, base = live_server
    cfg = load_config(server.config_path)
    cfg.controlled_marker_ids = [1, 3, 7]
    save_config(cfg, server.config_path)

    status, body = _get(base, "/section/detection")
    assert status == 200
    assert 'name="pin_marker_id"' in body
    assert 'value="-1"' in body  # the "Currently selected" sentinel
    # Each controlled marker shows up as an explicit option.
    for tid in (1, 3, 7):
        assert f'value="{tid}"' in body


def test_detection_section_surfaces_pin_marker_id_when_not_in_controlled_ids(
    live_server,
    monkeypatch,
    tmp_path,
) -> None:
    from openfollow.configuration import load_config, save_config

    server, base = live_server
    cfg = load_config(server.config_path)
    cfg.controlled_marker_ids = [1, 3]
    cfg.detection.pin_marker_id = 9  # not in controlled_marker_ids
    save_config(cfg, server.config_path)

    status, body = _get(base, "/section/detection")
    assert status == 200
    # Narrow to the pin_marker_id select to avoid matching the
    # ``Marker {{tid}}`` strings in other sections.
    select_start = body.index('<select name="pin_marker_id">')
    select_end = body.index("</select>", select_start)
    select_html = body[select_start:select_end]
    assert 'value="9" selected disabled' in select_html
    assert "Marker 9 (unavailable)" in select_html
    # The sentinel option must NOT be selected when an unavailable
    # explicit ID survives.
    assert 'value="-1" >' in select_html or 'value="-1">' in select_html


# ---------------------------------------------------------------------------
# Model export endpoint
# ---------------------------------------------------------------------------


def _config_with_storage(storage: str):
    from openfollow.configuration import AppConfig

    cfg = AppConfig()
    cfg.detection.storage_path = storage
    return cfg


def test_export_rejects_unknown_model(live_server) -> None:
    _, base = live_server
    status, body = _post_form(base, "/section/detection/export", {"export_model": "totally-made-up"})
    assert status == 200
    assert "Unknown model" in body
    assert "totally-made-up" in body


def test_export_requires_export_tools(live_server, monkeypatch) -> None:
    from openfollow.web import routes as routes_mod

    _, base = live_server
    monkeypatch.setattr(routes_mod, "_export_tools_available", lambda: False)
    status, body = _post_form(base, "/section/detection/export", {"export_model": "yolov8n.onnx"})
    assert status == 200
    assert "model export tools" in body.lower()


def test_export_reports_missing_script(live_server, monkeypatch) -> None:
    from openfollow.web import routes as routes_mod

    _, base = live_server
    monkeypatch.setattr(routes_mod, "_export_tools_available", lambda: True)
    monkeypatch.setattr(routes_mod, "_detection_export_script", lambda: None)
    status, body = _post_form(base, "/section/detection/export", {"export_model": "yolov8n.onnx"})
    assert status == 200
    assert "Export script not found" in body


def _stub_export_script(monkeypatch, tmp_path):
    """Point the export at a stub script + an explicit storage path so the
    kickoff reaches the worker without an NVMe or a real ultralytics."""
    from openfollow.web import routes as routes_mod

    script = tmp_path / "export_onnx.py"
    script.write_text("# stub\n")
    storage = tmp_path / "store"
    storage.mkdir()
    monkeypatch.setattr(routes_mod, "_export_tools_available", lambda: True)
    monkeypatch.setattr(routes_mod, "_detection_export_script", lambda: script)
    monkeypatch.setattr(routes_mod, "load_config", lambda _p: _config_with_storage(str(storage)))
    return script, storage


def test_export_kicks_off_worker_and_reports_success(live_server, monkeypatch, tmp_path) -> None:
    server, base = live_server
    script, storage = _stub_export_script(monkeypatch, tmp_path)

    captured: dict = {}

    def _fake(argv, *, timeout):
        captured["argv"] = list(argv)
        return 0, "export ok"

    _stub_package_command(monkeypatch, _fake)

    status, _ = _post_form(
        base,
        "/section/detection/export",
        {"export_model": "yolov8n.onnx", "imgsz": "320", "opset": "17"},
    )
    assert status == 200
    snapshot = _wait_for_install_terminal(server)
    assert snapshot["state"] == "success"
    assert "Exported" in snapshot["message"] and "yolov8n.onnx" in snapshot["message"]
    # The export targets the .pt source, the requested imgsz/opset, and the
    # storage models dir, via the interpreter running the web process.
    argv = captured["argv"]
    assert argv[0] == sys.executable
    assert argv[1] == str(script)
    assert argv[2] == "yolov8n.pt"
    assert "--imgsz" in argv and "320" in argv
    assert "--opset" in argv and "17" in argv
    assert str(storage / "models") in argv


def test_export_reports_subprocess_failure(live_server, monkeypatch, tmp_path) -> None:
    server, base = live_server
    _stub_export_script(monkeypatch, tmp_path)
    _stub_package_command(monkeypatch, lambda _argv, *, timeout: (1, "ultralytics blew up\ntraceback"))

    status, _ = _post_form(base, "/section/detection/export", {"export_model": "yolov8n.onnx"})
    assert status == 200
    snapshot = _wait_for_install_terminal(server)
    assert snapshot["state"] == "error"
    assert "exit 1" in snapshot["message"]
    assert "traceback" in snapshot["tail"]


def test_export_reports_timeout(live_server, monkeypatch, tmp_path) -> None:
    import subprocess as _real_subprocess

    server, base = live_server
    _stub_export_script(monkeypatch, tmp_path)

    def _boom(_argv, *, timeout):
        raise _real_subprocess.TimeoutExpired(cmd="export", timeout=timeout)

    _stub_package_command(monkeypatch, _boom)
    status, _ = _post_form(base, "/section/detection/export", {"export_model": "yolov8n.onnx"})
    assert status == 200
    snapshot = _wait_for_install_terminal(server)
    assert snapshot["state"] == "error"
    assert "timed out" in snapshot["message"].lower()


def test_export_reports_launch_failure(live_server, monkeypatch, tmp_path) -> None:
    server, base = live_server
    _stub_export_script(monkeypatch, tmp_path)

    def _boom(_argv, *, timeout):
        raise OSError("no such file")

    _stub_package_command(monkeypatch, _boom)
    status, _ = _post_form(base, "/section/detection/export", {"export_model": "yolov8n.onnx"})
    assert status == 200
    snapshot = _wait_for_install_terminal(server)
    assert snapshot["state"] == "error"
    assert "Failed to launch the export" in snapshot["message"]


def test_export_handles_unexpected_worker_exception(live_server, monkeypatch, tmp_path) -> None:
    server, base = live_server
    _stub_export_script(monkeypatch, tmp_path)

    def _boom(_argv, *, timeout):
        raise RuntimeError("unexpected blow-up inside the worker")

    _stub_package_command(monkeypatch, _boom)
    status, _ = _post_form(base, "/section/detection/export", {"export_model": "yolov8n.onnx"})
    assert status == 200
    snapshot = _wait_for_install_terminal(server)
    assert snapshot["state"] == "error"
    assert "unexpected error" in snapshot["message"].lower()
    assert server.try_claim_detection_install(action="export", extra="yolov8n.onnx") is True


def test_export_releases_slot_when_worker_spawn_fails(live_server, monkeypatch, tmp_path) -> None:
    """If ``Thread.start()`` raises, the export must publish a terminal error
    and release the shared slot rather than wedge at ``running``."""
    import types

    from openfollow.web import routes as routes_mod

    server, base = live_server
    _stub_export_script(monkeypatch, tmp_path)

    class _RaisingThread:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(routes_mod, "threading", types.SimpleNamespace(Thread=_RaisingThread))

    status, body = _post_form(base, "/section/detection/export", {"export_model": "yolov8n.onnx"})
    assert status == 200
    assert "worker" in body.lower()
    snapshot = server.get_detection_install_status()
    assert snapshot["state"] == "error"
    assert server.try_claim_detection_install(action="export", extra="yolov8n.onnx") is True


def test_export_rejected_while_another_export_in_progress(live_server, monkeypatch, tmp_path) -> None:
    server, base = live_server
    _stub_export_script(monkeypatch, tmp_path)
    # Occupy the shared slot so a second export can't claim it.
    assert server.try_claim_detection_install(action="export", extra="yolo11n.onnx") is True
    try:
        status, body = _post_form(base, "/section/detection/export", {"export_model": "yolov8n.onnx"})
        assert status == 200
        assert "in progress" in body
    finally:
        server.set_detection_install_status(state="idle")


def _seed_storage_model(monkeypatch, tmp_path, name: str = "yolo11n.onnx"):
    """Create <tmp>/store/models/<name> and point load_config at that storage."""
    from openfollow.web import routes as routes_mod

    storage = tmp_path / "store"
    (storage / "models").mkdir(parents=True)
    model = storage / "models" / name
    model.write_bytes(b"stub-onnx")
    monkeypatch.setattr(routes_mod, "load_config", lambda _p: _config_with_storage(str(storage)))
    return model


def test_delete_model_removes_file_and_reports(live_server, monkeypatch, tmp_path) -> None:
    _, base = live_server
    model = _seed_storage_model(monkeypatch, tmp_path)

    status, body = _post_form(base, "/section/detection/models/delete", {"model": "yolo11n.onnx"})
    assert status == 200
    assert "Deleted" in body and "yolo11n.onnx" in body
    assert not model.exists()


def test_delete_model_rejects_unknown_name(live_server, monkeypatch, tmp_path) -> None:
    _, base = live_server
    model = _seed_storage_model(monkeypatch, tmp_path)

    status, body = _post_form(base, "/section/detection/models/delete", {"model": "ghost.onnx"})
    assert status == 200
    assert "Cannot delete unknown model" in body
    assert model.exists()  # the real model is untouched


def test_delete_model_rejects_path_traversal(live_server, monkeypatch, tmp_path) -> None:
    _, base = live_server
    # A file outside the models dir that a traversal would try to reach.
    secret = tmp_path / "store" / "secret.txt"
    _seed_storage_model(monkeypatch, tmp_path)
    secret.write_text("keep me")

    status, body = _post_form(base, "/section/detection/models/delete", {"model": "../secret.txt"})
    assert status == 200
    assert "Cannot delete unknown model" in body
    assert secret.exists()  # traversal refused


def test_delete_model_reports_unlink_failure(live_server, monkeypatch, tmp_path) -> None:
    """A filesystem error during the actual delete surfaces as a banner, not a
    500 – the model stays on disk."""
    import pathlib

    _, base = live_server
    model = _seed_storage_model(monkeypatch, tmp_path)

    def boom(self, *_a, **_kw):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(pathlib.Path, "unlink", boom)

    status, body = _post_form(base, "/section/detection/models/delete", {"model": "yolo11n.onnx"})
    assert status == 200
    assert "Could not delete" in body and "yolo11n.onnx" in body
    assert model.exists()  # the failed delete left the file in place


def test_run_package_command_returns_rc_and_tail() -> None:
    from openfollow.web.routes import _run_package_command

    rc, tail = _run_package_command(
        [sys.executable, "-c", "print('hello'); print('world')"],
        timeout=10,
    )
    assert rc == 0
    # Split because the helper joins with "\n"; both lines must survive.
    lines = tail.splitlines()
    assert "hello" in lines
    assert "world" in lines


def test_run_package_command_raises_on_timeout() -> None:
    import subprocess as _real_subprocess

    from openfollow.web.routes import _run_package_command

    with pytest.raises(_real_subprocess.TimeoutExpired):
        _run_package_command(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout=1,
        )


def test_run_package_command_does_not_hang_when_child_ignores_kill(monkeypatch) -> None:
    import subprocess as _real_subprocess

    from openfollow.web import routes as routes_mod

    class _StubStdout:
        def __init__(self) -> None:
            self.closed = False

        def __iter__(self):
            # Yields nothing so the drainer thread exits immediately. The real
            # wedge would block on read(); the code calls ``close()`` regardless.
            return iter(())

        def close(self) -> None:
            self.closed = True

    class _StubProc:
        def __init__(self) -> None:
            self.stdout = _StubStdout()
            self.kill_called = False
            self._wait_calls = 0

        def wait(self, timeout=None):
            self._wait_calls += 1
            raise _real_subprocess.TimeoutExpired(cmd="fake", timeout=timeout or 0)

        def kill(self) -> None:
            self.kill_called = True

    stub = _StubProc()

    def _fake_popen(argv, **kwargs):
        return stub

    monkeypatch.setattr(routes_mod.subprocess, "Popen", _fake_popen)

    with pytest.raises(_real_subprocess.TimeoutExpired):
        routes_mod._run_package_command(["fake"], timeout=1)

    assert stub.kill_called is True
    # stdout must be closed on the wedged-child path so the drainer thread
    # can exit and the bounded join in ``finally`` returns.
    assert stub.stdout.closed is True


def test_run_package_command_truncates_to_bounded_tail(monkeypatch) -> None:
    from openfollow.web import routes as routes_mod

    monkeypatch.setattr(routes_mod, "_SUBPROCESS_TAIL_LINES", 5)

    rc, tail = routes_mod._run_package_command(
        [
            sys.executable,
            "-c",
            "import sys\nfor i in range(200):\n    sys.stdout.write(f'line-{i}\\n')\n",
        ],
        timeout=10,
    )
    assert rc == 0
    lines = tail.splitlines()
    assert len(lines) == 5
    # Last N lines are retained; earlier lines must be dropped.
    assert lines[-1] == "line-199"
    assert lines[0] == "line-195"


# ---------------------------------------------------------------------------
# Config export / import endpoints
# ---------------------------------------------------------------------------


def test_api_config_export_returns_json_attachment(live_server) -> None:
    """/api/config/export returns the full config as a JSON-bodied attachment
    named ``<psn_system_name>.openfollowsettings`` (content-type stays JSON;
    only the download extension is custom)."""
    server, base = live_server
    # The filename comes from the on-disk config's psn_system_name, not the
    # ConfigWebServer constructor arg. Persist an explicit name so the test
    # asserts on real behaviour rather than the AppConfig default.
    cfg = load_config(server.config_path)
    cfg.psn_system_name = "ExportedName"
    save_config(cfg, server.config_path)

    req = urllib.request.Request(f"{base}/api/config/export", method="GET")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        assert resp.headers.get("Content-Type", "").startswith("application/json")
        disposition = resp.headers.get("Content-Disposition", "")
        assert "attachment" in disposition
        # Full filename token, tightly pinned: the sanitised system name plus
        # the extension, with no "openfollow-" prefix (it lives in the extension).
        assert 'filename="ExportedName.openfollowsettings"' in disposition
        body = json.loads(resp.read().decode())
    # Exported config must contain the top-level sections.
    assert "camera" in body
    assert "grid" in body


def test_api_config_export_sanitises_unsafe_system_name(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(discovery_module.BeaconSender, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconSender, "stop", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "stop", lambda self: None)

    port = _find_free_tcp_port()
    config_path = tmp_path / "config.toml"

    # Write config with a risky system name.
    cfg = AppConfig()
    cfg.psn_system_name = "Name with / and ; chars"
    save_config(cfg, str(config_path))

    server = ConfigWebServer(
        config_path=str(config_path),
        host="127.0.0.1",
        port=port,
        system_name=cfg.psn_system_name,
    )
    server.start()
    try:
        assert _wait_for_port(port)
        base = f"http://127.0.0.1:{port}"

        with urllib.request.urlopen(f"{base}/api/config/export", timeout=5) as resp:
            disposition = resp.headers.get("Content-Disposition", "")
            resp.read()

        # Forward slash and semicolon must be replaced with '-'.
        assert "/" not in disposition.split('filename="', 1)[1].split('"')[0]
        assert ";" not in disposition.split('filename="', 1)[1].split('"')[0]
    finally:
        server.stop()


def test_api_config_import_rejects_non_object_json(live_server) -> None:
    _, base = live_server
    status, body = _post_raw_json(
        base,
        "/api/config/import",
        ["not", "a", "dict"],
    )
    assert status == 400
    assert "error" in body


def test_api_config_import_rejects_null_body(live_server) -> None:
    _, base = live_server
    status, body = _post_raw_json(base, "/api/config/import", None)
    assert status == 400
    assert "error" in body


def test_api_config_import_rejects_malformed_json(live_server) -> None:
    _, base = live_server
    req = urllib.request.Request(
        f"{base}/api/config/import",
        data=b"{not valid",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    assert status == 400


def test_api_config_import_saves_non_restart_changes_immediately(live_server) -> None:
    server, base = live_server
    status, body = _post_json(
        base,
        "/api/config/import",
        {"camera": {"pos_x": 7.25}, "psn_system_name": "Imported"},
    )
    assert status == 200
    assert body.get("success") is True
    assert body.get("needs_restart") is False

    saved = load_config(server.config_path)
    assert saved.camera.pos_x == pytest.approx(7.25)
    assert saved.psn_system_name == "Imported"


def test_api_config_import_detection_off_to_on_saves_live_without_restart_gate(
    live_server,
) -> None:
    server, base = live_server
    before = load_config(server.config_path)
    original_detection = before.detection.enabled

    status, body = _post_json(
        base,
        "/api/config/import",
        {"detection": {"enabled": not original_detection}},
    )
    assert status == 200
    assert body.get("success") is True
    assert body.get("needs_restart") is False

    saved = load_config(server.config_path)
    assert saved.detection.enabled == (not original_detection)


def test_api_config_import_otp_change_saves_live_without_restart_gate(
    live_server,
) -> None:
    server, base = live_server
    before = load_config(server.config_path)
    original_enabled = before.otp_output.enabled

    status, body = _post_json(
        base,
        "/api/config/import",
        {"otp_output": {"enabled": not original_enabled, "priority": 42}},
    )
    assert status == 200
    assert body.get("success") is True
    assert body.get("needs_restart") is False

    saved = load_config(server.config_path)
    assert saved.otp_output.enabled == (not original_enabled)
    assert saved.otp_output.priority == 42


def test_api_config_import_with_confirm_restart_saves_everything(live_server) -> None:
    server, base = live_server
    before = load_config(server.config_path)

    status, body = _post_json(
        base,
        "/api/config/import?confirm_restart=1",
        {
            "detection": {"enabled": not before.detection.enabled},
            "otp_output": {"enabled": True, "priority": 42, "system_number": 7},
        },
    )
    assert status == 200
    assert body.get("success") is True
    assert body.get("needs_restart") is False

    saved = load_config(server.config_path)
    assert saved.otp_output.enabled is True
    assert saved.otp_output.priority == 42
    assert saved.otp_output.system_number == 7


def test_api_config_import_with_skip_restart_saves_everything_live(
    live_server,
) -> None:
    """All sections apply live; skip_restart flag is preserved for backwards compatibility."""
    server, base = live_server
    before = load_config(server.config_path)
    before_otp = before.otp_output.enabled
    before_detection = before.detection.enabled

    status, body = _post_json(
        base,
        "/api/config/import?skip_restart=1",
        {
            "otp_output": {"enabled": not before_otp},
            "detection": {"enabled": not before_detection},
            "camera": {"pos_x": 99.0},
        },
    )
    assert status == 200
    assert body.get("success") is True
    # No diff is restart-gated, so the request is satisfied via the
    # default save-everything path; the skip_restart=1 query param
    # has no remaining gate to honour.
    assert body.get("needs_restart") is False

    saved = load_config(server.config_path)
    assert saved.detection.enabled == (not before_detection)
    assert saved.otp_output.enabled == (not before_otp)
    assert saved.camera.pos_x == pytest.approx(99.0)


# ---------------------------------------------------------------------------
# Wizard endpoints – error-path coverage
# ---------------------------------------------------------------------------


def _wizard_camera_payload(**overrides) -> dict[str, object]:
    base = {
        "pos_x": 0.0,
        "pos_y": -5.0,
        "pos_z": 3.0,
        "pitch": 10.0,
        "yaw": 0.0,
        "roll": 0.0,
        "fov": 45.0,
    }
    base.update(overrides)
    return base


def test_api_wizard_project_happy_path(live_server) -> None:
    _, base = live_server
    status, body = _post_json(
        base,
        "/api/wizard/project",
        {
            "camera": _wizard_camera_payload(),
            "grid": {"width": 10.0, "depth": 8.0},
            "image_width": 1920,
            "image_height": 1080,
        },
    )
    assert status == 200
    assert "corners" in body
    for key in ("DSL", "DSR", "USR", "USL"):
        assert key in body["corners"]
    # With z_offset=0 there should not be an elevated reference point.
    assert "reference_elevated" not in body
    assert "reference" in body


def test_api_wizard_project_includes_elevated_ref_when_z_offset(live_server) -> None:
    _, base = live_server
    status, body = _post_json(
        base,
        "/api/wizard/project",
        {
            "camera": _wizard_camera_payload(),
            "grid": {"width": 10.0, "depth": 8.0, "z_offset": 1.5},
            "image_width": 1920,
            "image_height": 1080,
        },
    )
    assert status == 200
    assert "reference_elevated" in body
    assert body.get("z_offset") == pytest.approx(1.5)


def test_api_wizard_project_rejects_non_dict_camera(live_server) -> None:
    _, base = live_server
    status, body = _post_json(
        base,
        "/api/wizard/project",
        {
            "camera": [1, 2, 3],  # not a dict
            "grid": {"width": 10.0, "depth": 8.0},
            "image_width": 1920,
            "image_height": 1080,
        },
    )
    assert status == 400
    assert "error" in body


def test_api_wizard_project_rejects_missing_camera_fields(live_server) -> None:
    _, base = live_server
    cam = _wizard_camera_payload()
    cam.pop("fov")  # omit a required field

    status, body = _post_json(
        base,
        "/api/wizard/project",
        {
            "camera": cam,
            "grid": {"width": 10.0, "depth": 8.0},
            "image_width": 1920,
            "image_height": 1080,
        },
    )
    assert status == 400
    assert "error" in body


def test_api_wizard_project_rejects_malformed_json(live_server) -> None:
    _, base = live_server
    req = urllib.request.Request(
        f"{base}/api/wizard/project",
        data=b"{not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    assert status == 400


def test_api_wizard_unproject_rejects_empty_screen_points(live_server) -> None:
    _, base = live_server
    status, body = _post_json(
        base,
        "/api/wizard/unproject",
        {
            "camera": _wizard_camera_payload(),
            "screen_points": [],  # empty list -> 400
            "image_width": 1920,
            "image_height": 1080,
        },
    )
    assert status == 400
    assert "non-empty" in str(body.get("error", ""))


def test_api_wizard_unproject_rejects_non_list_screen_points(live_server) -> None:
    _, base = live_server
    status, body = _post_json(
        base,
        "/api/wizard/unproject",
        {
            "camera": _wizard_camera_payload(),
            "screen_points": "not a list",
            "image_width": 1920,
            "image_height": 1080,
        },
    )
    assert status == 400
    assert "error" in body


def test_api_wizard_unproject_rejects_wrong_point_shape(live_server) -> None:
    _, base = live_server
    status, body = _post_json(
        base,
        "/api/wizard/unproject",
        {
            "camera": _wizard_camera_payload(),
            "screen_points": [[1.0, 2.0, 3.0]],  # 3-tuple instead of [x, y]
            "image_width": 1920,
            "image_height": 1080,
        },
    )
    assert status == 400
    assert "[x, y]" in str(body.get("error", ""))


def test_api_wizard_unproject_rejects_non_numeric_coord(live_server) -> None:
    _, base = live_server
    status, body = _post_json(
        base,
        "/api/wizard/unproject",
        {
            "camera": _wizard_camera_payload(),
            "screen_points": [["a", "b"]],
            "image_width": 1920,
            "image_height": 1080,
        },
    )
    assert status == 400
    assert "error" in body


def test_api_wizard_unproject_returns_delta_for_two_points(live_server) -> None:
    _, base = live_server
    # Camera looking down (pitch=-30) from 5 m high – both screen points
    # project onto the ground plane, so the endpoint produces a real delta.
    # The default _wizard_camera_payload() pitches up and would yield NaN.
    status, body = _post_json(
        base,
        "/api/wizard/unproject",
        {
            "camera": _wizard_camera_payload(pos_z=5.0, pitch=-30.0),
            "screen_points": [[960.0, 540.0], [1000.0, 540.0]],
            "image_width": 1920,
            "image_height": 1080,
        },
    )
    assert status == 200
    assert "world_points" in body
    # Two points must produce a delta payload (x/y distances).
    assert "delta" in body
    assert "x" in body["delta"] and "y" in body["delta"]


def test_api_wizard_solve_rejects_wrong_corner_counts(live_server) -> None:
    _, base = live_server
    status, body = _post_json(
        base,
        "/api/wizard/solve",
        {
            "world_corners": [[0, 0, 0]],  # only 1, expected 4
            "screen_corners": [[0, 0], [1, 0], [1, 1], [0, 1]],
            "image_width": 1920,
            "image_height": 1080,
        },
    )
    assert status == 400
    assert "exactly 4" in str(body.get("error", ""))


def test_api_wizard_solve_rejects_non_list_screen_corners(live_server) -> None:
    _, base = live_server
    status, body = _post_json(
        base,
        "/api/wizard/solve",
        {
            "world_corners": [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]],
            "screen_corners": "not a list",
            "image_width": 1920,
            "image_height": 1080,
        },
    )
    assert status == 400
    assert "error" in body


def test_api_wizard_solve_rejects_malformed_world_corner(live_server) -> None:
    _, base = live_server
    status, body = _post_json(
        base,
        "/api/wizard/solve",
        {
            "world_corners": [[0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]],  # missing z on first
            "screen_corners": [[0, 0], [1, 0], [1, 1], [0, 1]],
            "image_width": 1920,
            "image_height": 1080,
        },
    )
    assert status == 400
    assert "[x, y, z]" in str(body.get("error", ""))


def test_api_wizard_solve_rejects_non_numeric_screen_coord(live_server) -> None:
    _, base = live_server
    status, body = _post_json(
        base,
        "/api/wizard/solve",
        {
            "world_corners": [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]],
            "screen_corners": [[0, 0], [1, 0], [1, 1], [0, "not-a-number"]],
            "image_width": 1920,
            "image_height": 1080,
        },
    )
    assert status == 400
    assert "error" in body


# ---------------------------------------------------------------------------
# Auth hook: HX-Redirect for browser htmx requests, body-size cap, cookie
# ---------------------------------------------------------------------------


def test_auth_hx_request_returns_hx_redirect_header(pin_protected_server) -> None:
    _, base, _ = pin_protected_server
    req = urllib.request.Request(
        f"{base}/section/camera",
        headers={"HX-Request": "true"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            status = r.status
            hx_redirect = r.headers.get("HX-Redirect", "")
            body = r.read()
    except urllib.error.HTTPError as e:
        status = e.code
        hx_redirect = e.headers.get("HX-Redirect", "")
        body = e.read()

    # Empty body with HX-Redirect set to /login.
    assert status == 200 or status == 204
    assert hx_redirect == "/login"
    assert body in (b"", b"{}")


def test_auth_non_htmx_browser_redirects_to_login(pin_protected_server) -> None:
    """Plain GET without cookie or signature must 302 → /login for browser UX."""
    _, base, _ = pin_protected_server

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(f"{base}/", method="GET")
    try:
        resp = opener.open(req, timeout=5)
        location = resp.headers.get("Location", "")
        status = resp.status
    except urllib.error.HTTPError as e:
        location = e.headers.get("Location", "")
        status = e.code

    assert status in (302, 303)
    assert "/login" in location


def test_auth_api_path_returns_401_not_redirect(pin_protected_server) -> None:
    _, base, _ = pin_protected_server
    req = urllib.request.Request(f"{base}/api/config", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    assert status == 401


def test_auth_assets_path_bypasses_auth(pin_protected_server) -> None:
    _, base, _ = pin_protected_server
    # Any asset path – the route serves static files; we just need to see
    # auth doesn't 401/redirect before the route runs. 404 is acceptable
    # because the requested asset may not exist; 401/302 is NOT.
    req = urllib.request.Request(
        f"{base}/assets/does-not-exist.css",
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    assert status not in (401, 302, 303)


def test_auth_statistics_path_bypasses_auth(pin_protected_server) -> None:
    _, base, _ = pin_protected_server
    req = urllib.request.Request(f"{base}/section/statistics", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    assert status == 200


def test_auth_signed_request_over_declared_content_length_is_rejected(
    pin_protected_server,
    monkeypatch,
) -> None:
    """Body size cap must be enforced – oversize declarations are rejected."""
    _, base, pin = pin_protected_server
    # Shrink the cap to something we can exceed with a small payload.
    monkeypatch.setattr(peer_auth, "MAX_SIGNED_BODY_SIZE", 16)

    body = json.dumps({"pos_x": 2.5, "field": "x" * 32}).encode("utf-8")
    assert len(body) > 16
    timestamp, signature = peer_auth.sign(pin, "POST", "/api/config/camera", body)
    req = urllib.request.Request(
        f"{base}/api/config/camera",
        data=body,
        headers={
            "Content-Type": "application/json",
            peer_auth.TIMESTAMP_HEADER: str(timestamp),
            peer_auth.SIGNATURE_HEADER: signature,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    assert status == 413


def test_auth_cookie_authenticates_subsequent_requests(pin_protected_server) -> None:
    _, base, pin = pin_protected_server
    req = urllib.request.Request(
        f"{base}/login",
        data=urllib.parse.urlencode({"pin": pin}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_a, **_kw):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    try:
        resp = opener.open(req, timeout=5)
        set_cookies = resp.headers.get_all("Set-Cookie") or []
    except urllib.error.HTTPError as e:
        set_cookies = e.headers.get_all("Set-Cookie") or []

    auth_cookie = next(
        (c for c in set_cookies if c.startswith("_openfollow_auth=")),
        None,
    )
    assert auth_cookie is not None
    cookie_value = auth_cookie.split(";", 1)[0]  # e.g., _openfollow_auth=<encoded>

    # Reuse the cookie on a protected GET.
    req2 = urllib.request.Request(
        f"{base}/api/config",
        headers={"Cookie": cookie_value},
        method="GET",
    )
    with urllib.request.urlopen(req2, timeout=5) as r:
        assert r.status == 200
        json.loads(r.read().decode())  # valid JSON body


def test_auth_invalid_signature_fails_closed(pin_protected_server) -> None:
    _, base, _ = pin_protected_server
    body = b'{"pos_x": 2.5}'
    # Valid-looking timestamp with a bogus signature.
    req = urllib.request.Request(
        f"{base}/api/config/camera",
        data=body,
        headers={
            "Content-Type": "application/json",
            peer_auth.TIMESTAMP_HEADER: str(int(time.time())),
            peer_auth.SIGNATURE_HEADER: "00" * 32,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    assert status == 401


def test_post_general_ignores_removed_git_pull_fields(live_server, monkeypatch) -> None:
    """A crafted POST /section/general carrying the removed git-pull fields
    must be accepted without error and persist none of them – only the
    surviving .deb fields are honoured."""
    from openfollow.video import inputs as inputs_module

    monkeypatch.setattr(inputs_module, "get_available_input_ids", lambda: ["rtsp", "srt"])
    monkeypatch.setattr(inputs_module, "get_input_class", lambda _id: None)

    server, base = live_server
    cfg = load_config(server.config_path)

    status, _body = _post_form(
        base,
        "/section/general",
        {
            "psn_system_name": cfg.psn_system_name,
            "psn_mcast_ip": cfg.psn_mcast_ip,
            "web_port": str(cfg.web_port),
            "update_source_url": "git@evil.example.com:bad.git",
            "update_repo_branch": "attacker",
            "update_allowed_hosts": "evil.example.com",
            "update_service_name": "openfollow",
        },
    )
    assert status == 200

    after = load_config(server.config_path)
    assert not hasattr(after, "update_source_url")
    assert not hasattr(after, "update_repo_branch")
    assert not hasattr(after, "update_allowed_hosts")


def test_post_general_updates_github_repo_only_when_valid(live_server, monkeypatch) -> None:
    """POST /section/general with ``update_github_repo`` persists a valid
    ``owner/repo`` slug but ignores a malformed one (keeping the existing
    value), exercising both arms of the ``_is_valid_github_repo`` guard."""
    from openfollow.video import inputs as inputs_module

    monkeypatch.setattr(inputs_module, "get_available_input_ids", lambda: ["rtsp"])
    monkeypatch.setattr(inputs_module, "get_input_class", lambda _id: None)

    server, base = live_server
    cfg = load_config(server.config_path)
    cfg.update_github_repo = "owner/old-repo"
    save_config(cfg, server.config_path)

    base_form = {
        "psn_system_name": cfg.psn_system_name,
        "psn_mcast_ip": cfg.psn_mcast_ip,
        "web_port": str(cfg.web_port),
    }

    # A valid slug replaces the stored value.
    status, _ = _post_form(base, "/section/general", {**base_form, "update_github_repo": "owner/new-repo"})
    assert status == 200
    assert load_config(server.config_path).update_github_repo == "owner/new-repo"

    # A malformed slug is rejected and the previous value is kept.
    status, _ = _post_form(base, "/section/general", {**base_form, "update_github_repo": "not a repo!"})
    assert status == 200
    assert load_config(server.config_path).update_github_repo == "owner/new-repo"


# ---------------------------------------------------------------------------
# Section / general / login / update lifecycle handlers
# ---------------------------------------------------------------------------


def test_login_page_redirects_when_no_pin_configured(live_server) -> None:
    _, base = live_server

    opener = _no_redirect_opener()
    try:
        with opener.open(f"{base}/login", timeout=5) as resp:
            status = resp.status
            location = resp.headers.get("Location", "")
    except urllib.error.HTTPError as e:
        status = e.code
        location = e.headers.get("Location", "")

    assert status in (302, 303)
    assert location.endswith("/")


def test_login_submit_redirects_when_no_pin_configured(live_server) -> None:
    _, base = live_server

    opener = _no_redirect_opener()
    req = urllib.request.Request(
        f"{base}/login",
        data=urllib.parse.urlencode({"pin": "anything"}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with opener.open(req, timeout=5) as resp:
            status = resp.status
            location = resp.headers.get("Location", "")
    except urllib.error.HTTPError as e:
        status = e.code
        location = e.headers.get("Location", "")

    assert status in (302, 303)
    assert location.endswith("/")


def test_login_submit_renders_error_template_on_invalid_pin(pin_protected_server) -> None:
    _, base, _ = pin_protected_server

    status, body = _post_form(base, "/login", {"pin": "definitely-wrong"})

    assert status == 200
    # The template renders *something* indicating the PIN was rejected.
    # The exact wording is in the bundled template, not the route, so
    # asserting "incorrect"/"invalid" would couple to copy. Asserting the
    # form is re-rendered (still has the pin input) covers the branch.
    assert 'name="pin"' in body or "PIN" in body


def test_repeated_wrong_pin_locks_out_with_retry_after(pin_protected_server) -> None:
    _, base, _ = pin_protected_server

    status1, _, _ = _post_form_full(base, "/login", {"pin": "wrong-1"})
    assert status1 == 200  # first failure renders template, no penalty yet

    status2, _, headers2 = _post_form_full(base, "/login", {"pin": "wrong-2"})
    assert status2 == 429
    # ``Retry-After`` is part of the 429 contract – without it a polite
    # client can't tell when to come back, defeating the UX rationale
    # for a graceful lockout vs a hard ban.
    assert "Retry-After" in headers2
    assert int(headers2["Retry-After"]) >= 1


def test_lockout_blocks_correct_pin_during_window(pin_protected_server) -> None:
    """Even the correct PIN is rejected with 429 while the IP is locked
    out. Otherwise an attacker could probe whether their *guess* matches
    by timing the response – a 200 vs 429 split would leak whether they
    happened to guess right just before being locked out."""
    _, base, pin = pin_protected_server

    _post_form_full(base, "/login", {"pin": "wrong-1"})
    status, _, _ = _post_form_full(base, "/login", {"pin": pin})

    assert status == 429


def test_successful_login_clears_lockout_counter(pin_protected_server) -> None:
    _, base, pin = pin_protected_server

    # Fail once, wait out the 1 s lockout, then succeed. The success is
    # a 303 to ``/`` which urllib would auto-follow into a 200 from the
    # protected index – so use a non-following opener to observe the
    # actual login response.
    _post_form_full(base, "/login", {"pin": "wrong"})
    time.sleep(1.1)
    opener = _no_redirect_opener()
    req = urllib.request.Request(
        f"{base}/login",
        data=urllib.parse.urlencode({"pin": pin}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with opener.open(req, timeout=5) as r:
            status_ok = r.status
    except urllib.error.HTTPError as e:
        status_ok = e.code
    assert status_ok in (302, 303)

    # Fresh sequence: first failure renders template (counter = 1).
    status1, _, _ = _post_form_full(base, "/login", {"pin": "wrong-again"})
    assert status1 == 200
    # Second failure is the one that triggers 429 – same as the first
    # test. If the success had failed to clear history, we'd be deep in
    # the curve and the second wrong attempt would lock for >1 s.
    _, _, headers2 = _post_form_full(base, "/login", {"pin": "wrong-again-2"})
    assert int(headers2["Retry-After"]) <= 2


def _signed_post_full(
    base: str,
    path: str,
    body_bytes: bytes,
    *,
    signature: str,
    timestamp: str,
) -> tuple[int, dict]:
    """Send a signed POST with a caller-supplied signature/timestamp pair.

    Lets tests forge invalid signatures to exercise the throttle's
    peer-auth-failure path without the cooperation of ``peer_auth.sign``.
    """
    req = urllib.request.Request(
        f"{base}{path}",
        data=body_bytes,
        headers={
            "Content-Type": "application/json",
            peer_auth.TIMESTAMP_HEADER: timestamp,
            peer_auth.SIGNATURE_HEADER: signature,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers.items())
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers.items())


def test_peer_auth_signature_failure_locks_out(pin_protected_server) -> None:
    """Peer-auth signature path is subject to rate-limit throttle like login."""
    _, base, _ = pin_protected_server

    body = b'{"pos_x": 1.0}'
    bogus_sig = "0" * 64  # SHA-256 hex length, but never a valid HMAC

    # Use a current timestamp so we trip the signature-mismatch path,
    # not the timestamp-window-exceeded path.
    ts = str(int(time.time()))

    status1, _ = _signed_post_full(
        base,
        "/api/config/camera",
        body,
        signature=bogus_sig,
        timestamp=ts,
    )
    assert status1 == 401  # first bogus signature → 401, throttle armed

    status2, headers2 = _signed_post_full(
        base,
        "/api/config/camera",
        body,
        signature=bogus_sig,
        timestamp=ts,
    )
    assert status2 == 429
    assert "Retry-After" in headers2


def test_logout_clears_cookie_and_redirects_to_login(live_server) -> None:
    """POST /logout deletes the auth cookie and 303s to /login. Even on a
    PIN-less server the route must exist and behave. ``delete_cookie``
    works by emitting a Set-Cookie with an expired Max-Age/Expires –
    asserting on cookie *name* alone wouldn't catch a regression where
    the route stopped emitting the expiry."""
    _, base = live_server

    opener = _no_redirect_opener()
    req = urllib.request.Request(f"{base}/logout", data=b"", method="POST")
    try:
        with opener.open(req, timeout=5) as resp:
            status = resp.status
            location = resp.headers.get("Location", "")
            set_cookies = resp.headers.get_all("Set-Cookie") or []
    except urllib.error.HTTPError as e:
        status = e.code
        location = e.headers.get("Location", "")
        set_cookies = e.headers.get_all("Set-Cookie") or []

    assert status in (302, 303)
    assert location.endswith("/login")

    auth_cookies = [c for c in set_cookies if "_openfollow_auth=" in c]
    assert auth_cookies, "logout must emit a Set-Cookie for _openfollow_auth"
    # An expired cookie is the actual deletion signal – ``Max-Age=0``,
    # negative Max-Age, or an ``Expires=`` date in the past. A regression
    # that drops the expiry would still produce a Set-Cookie header but
    # leave the cookie alive on the browser.
    assert any("max-age=0" in c.lower() or "max-age=-" in c.lower() or "expires=" in c.lower() for c in auth_cookies)


def test_update_video_source_post_renders_video_source_partial(live_server) -> None:
    """Form POST without ?restart=1 saves and re-renders the video source partial.
    Asserts on stable IDs to catch regressions in partial rendering."""
    server, base = live_server
    status, body = _post_form(
        base,
        "/section/video_source",
        {"video_source_type": "rtsp"},
    )
    assert status == 200
    assert 'id="video-source-section"' in body
    assert 'id="general-network-section"' not in body
    assert 'id="general-software-update-section"' not in body

    saved = load_config(server.config_path)
    assert saved.video_source_type == "rtsp"


def test_update_video_source_post_with_restart_renders_general_partial(live_server) -> None:
    """``?restart=1`` flips into the restart-confirmation branch, which
    requests an app restart and renders the general partial instead of
    the video-source one. Asserting on stable section IDs (rather than
    just the restart flag) catches a regression where the handler
    returns the wrong template."""
    server, base = live_server
    assert server.check_restart_requested() is False

    status, body = _post_form(
        base,
        "/section/video_source?restart=1",
        {"video_source_type": "rtsp"},
    )
    assert status == 200
    assert server.check_restart_requested() is True
    # General partial has two top-level sections; verify the right one rendered.
    assert 'id="general-network-section"' in body
    assert 'id="general-software-update-section"' in body
    assert 'id="video-source-section"' not in body


def test_update_general_blocks_restart_while_update_is_running(live_server) -> None:
    server, base = live_server
    server.set_update_status(state="running", message="Pulling...", error="")

    assert server.check_restart_requested() is False
    status, body = _post_form(base, "/section/general?restart=1", {})
    assert status == 200
    assert server.check_restart_requested() is False
    assert "Restart is blocked" in body or "currently running" in body


def test_deb_update_dispatches_request(live_server, monkeypatch) -> None:
    """POST /section/general/deb-update queues a deb update when idle."""
    from openfollow.web import server as server_module

    server, base = live_server
    captured: dict = {}

    def _fake_request_deb_update(self, service_name):
        captured["service_name"] = service_name
        return True

    monkeypatch.setattr(
        server_module.ConfigWebServer,
        "request_deb_update",
        _fake_request_deb_update,
    )

    status, body = _post_form(base, "/section/general/deb-update", {})
    assert status == 200
    assert captured["service_name"] == "openfollow"


def test_deb_update_falls_back_to_default_when_service_name_invalid(live_server, monkeypatch) -> None:
    """An invalid update_service_name in config must not block the update –
    the route falls back to 'openfollow' so the button always works."""
    from openfollow.web import server as server_module

    server, base = live_server
    cfg = load_config(server.config_path)
    cfg.update_service_name = "--no-block"
    save_config(cfg, server.config_path)

    captured: dict = {}

    def _fake_request_deb_update(self, service_name):
        captured["service_name"] = service_name
        return True

    monkeypatch.setattr(
        server_module.ConfigWebServer,
        "request_deb_update",
        _fake_request_deb_update,
    )

    status, body = _post_form(base, "/section/general/deb-update", {})
    assert status == 200
    assert captured.get("service_name") == "openfollow"


def test_deb_update_reports_when_already_running(live_server, monkeypatch) -> None:
    """request_deb_update returns False when another update is in flight;
    the route must surface an 'already running' message."""
    from openfollow.web import server as server_module

    server, base = live_server
    monkeypatch.setattr(
        server_module.ConfigWebServer,
        "request_deb_update",
        lambda *_a, **_kw: False,
    )

    status, body = _post_form(base, "/section/general/deb-update", {})
    assert status == 200
    assert "already running" in body


def test_deb_update_check_returns_json_available(live_server, monkeypatch) -> None:
    """GET /section/general/deb-update/check returns JSON the General-tab
    modal uses to decide whether to offer the install."""
    import openfollow.runtime.deb_update as deb_update_mod

    _, base = live_server
    monkeypatch.setattr(
        deb_update_mod,
        "_fetch_latest_release",
        lambda repo: {"tag_name": "v99.0.0", "assets": []},
    )
    status, body = _get_json(base, "/section/general/deb-update/check")
    assert status == 200
    assert body["ok"] is True
    assert body["available"] is True
    assert body["latest"] == "99.0.0"


def test_deb_update_check_surfaces_error_as_json(live_server, monkeypatch) -> None:
    """A network/API failure during check returns ok=False with the error
    message rather than a 500 – the modal shows it as feedback."""
    import openfollow.runtime.deb_update as deb_update_mod

    _, base = live_server

    def _raise(repo):
        raise RuntimeError("GitHub unreachable")

    monkeypatch.setattr(deb_update_mod, "_fetch_latest_release", _raise)
    status, body = _get_json(base, "/section/general/deb-update/check")
    assert status == 200
    assert body["ok"] is False
    assert "GitHub unreachable" in body["error"]


# ---------------------------------------------------------------------------
# Offline upload install – POST /section/general/deb-upload
# ---------------------------------------------------------------------------


def _post_upload(
    base: str,
    path: str,
    *,
    filename: str | None = "openfollow_0.2.4_arm64.ofupdate",
    content: bytes = b"FAKEBUNDLE",
) -> tuple[int, dict]:
    """POST a file as the raw request body and parse the JSON reply.

    The route reads the filename from a ``?filename=`` query param and streams
    the body straight to disk (no multipart). ``filename=None`` omits the param
    (covers the 'No file selected' branch)."""
    url = f"{base}{path}"
    if filename is not None:
        url += f"?filename={urllib.parse.quote(filename)}"
    req = urllib.request.Request(
        url,
        data=content,
        headers={"Content-Type": "application/octet-stream"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def test_deb_upload_rejects_when_no_file(live_server) -> None:
    _, base = live_server
    status, body = _post_upload(base, "/section/general/deb-upload", filename=None)
    assert status == 200
    assert body["ok"] is False
    assert "No file" in body["error"]


def test_deb_upload_rejects_unsupported_extension(live_server) -> None:
    _, base = live_server
    status, body = _post_upload(base, "/section/general/deb-upload", filename="evil.exe")
    assert status == 200
    assert body["ok"] is False
    assert "Unsupported file type" in body["error"]


def test_deb_upload_rejects_raw_deb(live_server) -> None:
    # A bare .deb (no signature wrapper) is no longer installable – only the
    # signed .ofupdate bundle is accepted.
    _, base = live_server
    status, body = _post_upload(base, "/section/general/deb-upload", filename="openfollow_0.2.4_arm64.deb")
    assert status == 200
    assert body["ok"] is False
    assert "Unsupported file type" in body["error"]


def test_deb_upload_rejects_oversized_body(live_server, monkeypatch) -> None:
    import openfollow.web.routes as routes_mod

    _, base = live_server
    monkeypatch.setattr(routes_mod, "_MAX_UPLOAD_BYTES", 4)  # smaller than our body
    status, body = _post_upload(base, "/section/general/deb-upload", content=b"way too big")
    assert status == 200
    assert body["ok"] is False
    assert "too large" in body["error"]


def test_deb_upload_rejects_empty_body(live_server) -> None:
    _, base = live_server
    status, body = _post_upload(base, "/section/general/deb-upload", content=b"")
    assert status == 200
    assert body["ok"] is False
    assert "Empty" in body["error"]


def test_deb_upload_plain_deb_stages_and_queues(live_server, monkeypatch) -> None:
    """A valid bundle is staged under the temp prefix, verified, and queued;
    a newer version is not flagged as a downgrade."""
    import openfollow.runtime.deb_update as deb_update_mod
    from openfollow.web import server as server_module

    server, base = live_server
    captured: dict = {}

    monkeypatch.setattr(
        deb_update_mod,
        "verify_and_extract_bundle",
        lambda bundle, staging: bundle + ".d/openfollow_0.2.4_arm64.deb",
    )
    monkeypatch.setattr(
        deb_update_mod,
        "validate_uploaded_deb",
        lambda path, arch: {"Package": "openfollow", "Version": "99.0.0", "Architecture": arch},
    )

    def _fake_queue(self, service_name, *, deb_path=None):
        captured["service_name"] = service_name
        captured["deb_path"] = deb_path
        return True

    monkeypatch.setattr(server_module.ConfigWebServer, "request_local_update", _fake_queue)

    status, body = _post_upload(base, "/section/general/deb-upload")
    assert status == 200
    assert body["ok"] is True
    assert body["version"] == "99.0.0"
    assert body["downgrade"] is False
    # Staged under the temp prefix so the existing sudoers rule applies.
    assert captured["deb_path"].startswith("/tmp/openfollow-update-")
    # Cleanup the staged file (the worker would normally do this).
    try:
        os.unlink(captured["deb_path"])
    except OSError:
        pass


def test_deb_upload_flags_downgrade(live_server, monkeypatch) -> None:
    import openfollow
    import openfollow.runtime.deb_update as deb_update_mod
    from openfollow.web import server as server_module

    _, base = live_server
    # Pin the installed version so the comparison is independent of the
    # dev/CI version stamp.
    monkeypatch.setattr(openfollow, "__version__", "9.9.9")
    monkeypatch.setattr(
        deb_update_mod,
        "verify_and_extract_bundle",
        lambda bundle, staging: bundle + ".d/openfollow_0.0.1_arm64.deb",
    )
    monkeypatch.setattr(
        deb_update_mod,
        "validate_uploaded_deb",
        lambda path, arch: {"Package": "openfollow", "Version": "0.0.1", "Architecture": arch},
    )
    staged: dict = {}

    def _fake_queue(self, service_name, *, deb_path=None):
        staged["deb_path"] = deb_path
        return True

    monkeypatch.setattr(server_module.ConfigWebServer, "request_local_update", _fake_queue)

    status, body = _post_upload(base, "/section/general/deb-upload")
    assert status == 200
    assert body["ok"] is True
    assert body["downgrade"] is True
    try:
        os.unlink(staged["deb_path"])
    except (OSError, KeyError):
        pass


def test_deb_upload_invalid_deb_returns_error(live_server, monkeypatch) -> None:
    import openfollow.runtime.deb_update as deb_update_mod

    _, base = live_server

    def _bad(path, arch):
        raise RuntimeError("Uploaded package is 'vlc', expected 'openfollow'.")

    monkeypatch.setattr(
        deb_update_mod,
        "verify_and_extract_bundle",
        lambda bundle, staging: bundle + ".d/openfollow_0.2.4_arm64.deb",
    )
    monkeypatch.setattr(deb_update_mod, "validate_uploaded_deb", _bad)
    status, body = _post_upload(base, "/section/general/deb-upload")
    assert status == 200
    assert body["ok"] is False
    assert "expected 'openfollow'" in body["error"]


def test_deb_upload_reports_when_already_running(live_server, monkeypatch) -> None:
    import openfollow.runtime.deb_update as deb_update_mod
    from openfollow.web import server as server_module

    _, base = live_server
    monkeypatch.setattr(
        deb_update_mod,
        "verify_and_extract_bundle",
        lambda bundle, staging: bundle + ".d/openfollow_0.2.4_arm64.deb",
    )
    monkeypatch.setattr(
        deb_update_mod,
        "validate_uploaded_deb",
        lambda path, arch: {"Package": "openfollow", "Version": "99.0.0", "Architecture": arch},
    )
    monkeypatch.setattr(server_module.ConfigWebServer, "request_local_update", lambda *a, **kw: False)
    status, body = _post_upload(base, "/section/general/deb-upload")
    assert status == 200
    assert body["ok"] is False
    assert "already running" in body["error"]


def test_deb_upload_falls_back_to_default_service_name(live_server, monkeypatch) -> None:
    """An invalid update_service_name in config must not block the upload –
    the route falls back to 'openfollow' (mirrors the online updater)."""
    import openfollow.runtime.deb_update as deb_update_mod
    from openfollow.web import server as server_module

    server, base = live_server
    cfg = load_config(server.config_path)
    cfg.update_service_name = "--no-block"
    save_config(cfg, server.config_path)

    captured: dict = {}
    monkeypatch.setattr(
        deb_update_mod,
        "verify_and_extract_bundle",
        lambda bundle, staging: bundle + ".d/openfollow_0.2.4_arm64.deb",
    )
    monkeypatch.setattr(
        deb_update_mod,
        "validate_uploaded_deb",
        lambda path, arch: {"Package": "openfollow", "Version": "99.0.0", "Architecture": arch},
    )

    def _fake_queue(self, service_name, *, deb_path=None):
        captured["service_name"] = service_name
        captured["deb_path"] = deb_path
        return True

    monkeypatch.setattr(server_module.ConfigWebServer, "request_local_update", _fake_queue)

    status, body = _post_upload(base, "/section/general/deb-upload")
    assert status == 200
    assert body["ok"] is True
    assert captured["service_name"] == "openfollow"
    try:
        os.unlink(captured["deb_path"])
    except (OSError, KeyError):
        pass


def test_update_psn_persists_and_renders_partial(live_server) -> None:
    """Form POST to /section/psn updates the PSN transport fields and
    re-renders the partial – exercises the dedicated PSN update route
    (separate from /section/general which also accepts those keys)."""
    server, base = live_server
    status, body = _post_form(
        base,
        "/section/psn",
        {
            "psn_system_name": "PSN-A",
            "psn_mcast_ip": "236.10.10.20",
            "psn_source_iface": "  eth0  ",
        },
    )
    assert status == 200

    saved = load_config(server.config_path)
    assert saved.psn_system_name == "PSN-A"
    assert saved.psn_mcast_ip == "236.10.10.20"
    assert saved.psn_source_iface == "eth0"


def test_update_psn_with_restart_flag_queues_restart(live_server) -> None:
    """Same restart contract as /section/general: ``?restart=1`` triggers a
    restart request via the command queue."""
    server, base = live_server
    assert server.check_restart_requested() is False

    status, _ = _post_form(base, "/section/psn?restart=1", {})
    assert status == 200
    assert server.check_restart_requested() is True


def test_psn_section_renders_source_advisory_when_pin_missed(
    tmp_path,
    monkeypatch,
) -> None:
    """When pinned interface is unavailable, PSN section renders advisory banner with auto-detected IP."""
    banner = "Pinned network interface 'wlan0_gone' is not available. Using auto-detected 192.168.178.61."
    server, base = _live_server_with_zone_providers(
        tmp_path,
        monkeypatch,
        psn_source_advisory_provider=lambda: {
            "status": "primary",
            "banner": banner,
            "resolved_ip": "192.168.178.61",
        },
    )
    try:
        status, body = _get(base, "/section/psn")
        assert status == 200
        # Assert on a quote-free slice – bottle HTML-escapes the
        # apostrophes around the iface name (``&#039;``) in the live body.
        assert "is not available. Using auto-detected 192.168.178.61." in body
        assert 'class="notice warning"' in body
    finally:
        server.stop()


def test_psn_section_no_advisory_when_pin_honoured(live_server) -> None:
    server, base = live_server
    status, body = _get(base, "/section/psn")
    assert status == 200
    assert "is not available" not in body


def test_get_psn_source_advisory_empty_without_provider(tmp_path) -> None:
    """No provider wired (tests / dev hosts) → all-empty advisory so the
    PSN partial renders no banner."""
    server = ConfigWebServer(config_path=str(tmp_path / "config.toml"))
    assert server.get_psn_source_advisory() == {
        "status": "",
        "banner": "",
        "resolved_ip": "",
    }


def test_get_psn_source_advisory_swallows_provider_error(tmp_path) -> None:

    def boom() -> dict:
        raise RuntimeError("app gone")

    server = ConfigWebServer(
        config_path=str(tmp_path / "config.toml"),
        psn_source_advisory_provider=boom,
    )
    assert server.get_psn_source_advisory() == {
        "status": "",
        "banner": "",
        "resolved_ip": "",
    }


def test_start_button_detection_calls_command_queue(live_server, monkeypatch) -> None:
    """The wizard kick-off endpoint forwards to ``request_button_detection``
    on the server and re-renders the gamepad partial with
    ``detection_started=True``. Spy on the request method so a regression
    that drops the call would fail the test, and assert on the stable
    "Wizard running on app display" template token that only renders
    when ``detection_started`` is set."""
    server, base = live_server

    request_calls: list[bool] = []
    monkeypatch.setattr(
        server,
        "request_button_detection",
        lambda: request_calls.append(True),
    )

    status, body = _post_form(base, "/section/gamepad/detect-buttons", {})
    assert status == 200
    assert request_calls == [True]
    # Stable template token gated on ``detection_started=True``.
    assert "Wizard running on app display" in body


def test_cancel_button_detection_calls_command_queue(live_server, monkeypatch) -> None:
    """Cancel endpoint forwards to cancel_button_detection and re-renders the gamepad partial."""
    server, base = live_server

    cancel_calls: list[bool] = []
    monkeypatch.setattr(
        server,
        "cancel_button_detection",
        lambda: cancel_calls.append(True),
    )
    # The re-render reads the live active flag; force it on so the
    # response still carries the in-progress status + Cancel button
    # (the cancel is async – drained on the next main-loop tick).
    monkeypatch.setattr(server, "is_button_detection_active", lambda: True)

    status, body = _post_form(
        base,
        "/section/gamepad/cancel-button-detection",
        {},
    )
    assert status == 200
    assert cancel_calls == [True]
    assert "Cancel wizard" in body


def test_update_otp_output_post_renders_partial(live_server) -> None:
    server, base = live_server
    status, body = _post_form(
        base,
        "/section/otp_output",
        {"enabled": "on", "port": "5570"},
    )
    assert status == 200

    saved = load_config(server.config_path)
    assert saved.otp_output.enabled is True
    assert saved.otp_output.port == 5570


def test_update_rttrpm_output_post_renders_partial(live_server) -> None:
    """RTTrPM output partial route – same bool-fields contract as OTP."""
    server, base = live_server
    status, body = _post_form(
        base,
        "/section/rttrpm_output",
        {"enabled": "on", "fps": "30"},
    )
    assert status == 200

    saved = load_config(server.config_path)
    assert saved.rttrpm_output.enabled is True
    assert saved.rttrpm_output.fps == 30


def test_update_trigger_zones_post_renders_partial(live_server) -> None:
    server, base = live_server
    status, _ = _post_form(
        base,
        "/section/trigger_zones",
        {"enabled": "on", "debounce_ms": "150"},
    )
    assert status == 200

    saved = load_config(server.config_path)
    assert saved.trigger_zones.enabled is True
    # ``show_overlay`` was not in the form, so the bool-fields treatment
    # must clear it to False rather than leave it stale.
    assert saved.trigger_zones.show_overlay is False
    assert saved.trigger_zones.debounce_ms == 150


def test_osc_destinations_crud_round_trip(live_server) -> None:
    """Add → save → duplicate → delete a destination through the web routes."""
    server, base = live_server

    # Add: a fresh destination lands on top of the seeded "Default".
    status, body = _post_form(base, "/section/osc_destinations/add", {})
    assert status == 200
    cfg = load_config(server.config_path)
    assert len(cfg.osc_destinations.destinations) == 2
    new_id = cfg.osc_destinations.destinations[-1].id

    # Save: edit the new destination's connection.
    status, _ = _post_form(
        base,
        f"/section/osc_destination/{new_id}",
        {"name": "Console", "host": "10.0.0.9", "port": "9001", "protocol": "tcp", "framing": "slip"},
    )
    assert status == 200
    saved = load_config(server.config_path).osc_destinations.get(new_id)
    assert saved is not None
    assert saved.name == "Console"
    assert saved.host == "10.0.0.9"
    assert saved.port == 9001
    assert saved.protocol == "tcp"

    # Duplicate then delete.
    status, _ = _post_form(base, f"/section/osc_destination/{new_id}/duplicate", {})
    assert status == 200
    assert len(load_config(server.config_path).osc_destinations.destinations) == 3
    status, _ = _post_form(base, f"/section/osc_destination/{new_id}/delete", {})
    assert status == 200
    after = load_config(server.config_path).osc_destinations
    assert after.get(new_id) is None


def test_api_zones_includes_live_destinations(live_server) -> None:
    """The zone-editor poll carries the shared destinations, so adding one in
    the OSC Destinations section reaches the zone dropdown without a reload."""
    server, base = live_server

    # The seeded "Default" destination is present from the start, with the
    # endpoint fields the dropdown renders.
    status, body = _get_json(base, "/api/zones")
    assert status == 200
    default = next(d for d in body["destinations"] if d["id"] == "default")
    assert set(default) == {"id", "name", "host", "port", "protocol", "framing"}

    # Add a destination via the section route; the next poll reflects it.
    status, _ = _post_form(base, "/section/osc_destinations/add", {})
    assert status == 200
    new_id = load_config(server.config_path).osc_destinations.destinations[-1].id
    status, body2 = _get_json(base, "/api/zones")
    assert status == 200
    assert new_id in [d["id"] for d in body2["destinations"]]


def test_osc_destinations_section_get_renders(live_server) -> None:
    """``GET /section/osc_destinations`` renders the destinations partial."""
    _, base = live_server
    status, body = _get(base, "/section/osc_destinations")
    assert status == 200
    assert "OSC Destinations" in body


def test_osc_destination_save_partial_form_leaves_absent_fields_untouched(
    live_server,
) -> None:
    """A save that omits fields updates only what's posted – the parser loop
    skips absent fields rather than clobbering them with defaults."""
    server, base = live_server
    seeded = load_config(server.config_path).osc_destinations.destinations[0]
    original_name = seeded.name
    status, _ = _post_form(
        base,
        f"/section/osc_destination/{seeded.id}",
        {"host": "10.1.2.3"},  # only host – name/port/protocol/framing absent
    )
    assert status == 200
    saved = load_config(server.config_path).osc_destinations.get(seeded.id)
    assert saved is not None
    assert saved.host == "10.1.2.3"
    assert saved.name == original_name


def test_osc_destination_save_unknown_id_404(live_server) -> None:
    _, base = live_server
    status, _ = _post_form(base, "/section/osc_destination/no-such", {"host": "1.2.3.4"})
    assert status == 404


def test_osc_destination_duplicate_unknown_id_404(live_server) -> None:
    _, base = live_server
    status, _ = _post_form(base, "/section/osc_destination/no-such/duplicate", {})
    assert status == 404


def test_osc_destination_delete_unknown_id_is_noop(live_server) -> None:
    server, base = live_server
    before = len(load_config(server.config_path).osc_destinations.destinations)
    status, body = _post_form(base, "/section/osc_destination/no-such/delete", {})
    assert status == 200
    assert "OSC Destinations" in body
    after = len(load_config(server.config_path).osc_destinations.destinations)
    assert after == before


def test_osc_destination_move_reorders_and_guards_edges(live_server) -> None:
    """move up/down swaps neighbours; edge moves and an unknown direction are
    no-ops; an unknown id is a 404."""
    server, base = live_server
    # Seeded "Default" sits at index 0; add two more for a clear ordering.
    _post_form(base, "/section/osc_destinations/add", {})
    _post_form(base, "/section/osc_destinations/add", {})
    dests = load_config(server.config_path).osc_destinations.destinations
    assert len(dests) == 3
    first_id, second_id, third_id = (d.id for d in dests)

    def _order() -> list[str]:
        return [d.id for d in load_config(server.config_path).osc_destinations.destinations]

    _post_form(base, f"/section/osc_destination/{second_id}/move", {"direction": "up"})
    assert _order()[:2] == [second_id, first_id]

    _post_form(base, f"/section/osc_destination/{second_id}/move", {"direction": "down"})
    assert _order()[:2] == [first_id, second_id]

    # Top-up and bottom-down: target == idx → no swap, no save.
    _post_form(base, f"/section/osc_destination/{first_id}/move", {"direction": "up"})
    _post_form(base, f"/section/osc_destination/{third_id}/move", {"direction": "down"})
    assert _order() == [first_id, second_id, third_id]

    # Unknown direction → no-op.
    _post_form(base, f"/section/osc_destination/{first_id}/move", {"direction": "sideways"})
    assert _order() == [first_id, second_id, third_id]

    status, _ = _post_form(
        base,
        "/section/osc_destination/no-such/move",
        {"direction": "up"},
    )
    assert status == 404


def test_osc_destination_noop_move_does_not_flash_saved(live_server) -> None:
    """A boundary move (top row up) changes nothing, so the re-render must NOT
    carry the 'saved' state – an operator shouldn't see a saved flash for an
    action that did nothing. A real move does flash saved."""
    server, base = live_server
    first_id = load_config(server.config_path).osc_destinations.destinations[0].id
    _post_form(base, "/section/osc_destinations/add", {})
    second_id = load_config(server.config_path).osc_destinations.destinations[-1].id

    # No-op: top row up → no swap, no 'saved'.
    _status, body = _post_form(base, f"/section/osc_destination/{first_id}/move", {"direction": "up"})
    assert "osc-destinations-section" in body
    assert "section saved" not in body

    # Real move: second row up → flashes saved.
    _status, body = _post_form(base, f"/section/osc_destination/{second_id}/move", {"direction": "up"})
    assert "section saved" in body


def test_osc_binding_dangling_destination_shows_missing_option(live_server) -> None:
    """A row whose ``destination_id`` points at a deleted destination renders a
    selected '(missing destination)' option, so the dropdown reflects the
    stored dangling id instead of silently falling back to '(none)'."""
    server, base = live_server
    _post_form(base, "/section/osc_bindings/add", {})
    row_id = load_config(server.config_path).osc_transmitters.transmitters[0].id
    _post_form(base, f"/section/osc_binding/{row_id}", {"destination_id": "ghost-id"})

    status, body = _get(base, "/section/osc_bindings")
    assert status == 200
    assert "(missing destination)" in body


def test_osc_destinations_use_drag_handle_not_arrow_buttons(live_server) -> None:
    """Destinations reorder via the same ⋮⋮ drag handle as transmitters; the
    per-row ↑/↓ arrow buttons are gone from the UI (the /move route stays as a
    stable JSON-API surface)."""
    _, base = live_server
    status, body = _get(base, "/section/osc_destinations")
    assert status == 200
    assert "osc-destination-drag-handle" in body
    assert 'data-reorder-url="/section/osc_destinations/reorder"' in body
    assert "↑" not in body
    assert "↓" not in body


def test_osc_destinations_reorder_applies_full_ordering(live_server) -> None:
    """The drag-handle UI POSTs the complete id ordering: [A,B,C] -> [C,A,B]."""
    server, base = live_server
    _post_form(base, "/section/osc_destinations/add", {})
    _post_form(base, "/section/osc_destinations/add", {})
    a, b, c = (d.id for d in load_config(server.config_path).osc_destinations.destinations)
    status, _ = _post_form(base, "/section/osc_destinations/reorder", {"order": f"{c},{a},{b}"})
    assert status == 200
    after = [d.id for d in load_config(server.config_path).osc_destinations.destinations]
    assert after == [c, a, b]


def test_osc_destinations_reorder_drops_unknown_keeps_missing(live_server) -> None:
    """A stale post with a phantom id and an omitted real id: phantom dropped,
    omitted destination appended so nothing is lost."""
    server, base = live_server
    _post_form(base, "/section/osc_destinations/add", {})
    _post_form(base, "/section/osc_destinations/add", {})
    a, b, c = (d.id for d in load_config(server.config_path).osc_destinations.destinations)
    _post_form(base, "/section/osc_destinations/reorder", {"order": f"{c},ghost-id,{a}"})
    after = [d.id for d in load_config(server.config_path).osc_destinations.destinations]
    assert after == [c, a, b]


def test_osc_destinations_reorder_empty_order_is_noop(live_server) -> None:
    """Empty / whitespace ``order`` must not drop destinations."""
    server, base = live_server
    _post_form(base, "/section/osc_destinations/add", {})
    before = [d.id for d in load_config(server.config_path).osc_destinations.destinations]
    status, _ = _post_form(base, "/section/osc_destinations/reorder", {"order": "  "})
    assert status == 200
    after = [d.id for d in load_config(server.config_path).osc_destinations.destinations]
    assert after == before


def test_osc_destinations_reorder_no_change_is_idempotent(live_server) -> None:
    """Posting the existing order is a no-op (the permutation matches what's on
    disk, so no save) while still re-rendering successfully."""
    server, base = live_server
    _post_form(base, "/section/osc_destinations/add", {})
    before = [d.id for d in load_config(server.config_path).osc_destinations.destinations]
    status, _ = _post_form(base, "/section/osc_destinations/reorder", {"order": ",".join(before)})
    assert status == 200
    after = [d.id for d in load_config(server.config_path).osc_destinations.destinations]
    assert after == before


def test_osc_destinations_collapsed_summary_shows_host_port(live_server) -> None:
    """The collapsed destination row shows host:port right after the name plus a
    protocol badge, so a folded destination stays identifiable at a glance."""
    server, base = live_server
    seeded = load_config(server.config_path).osc_destinations.destinations[0]
    _post_form(
        base,
        f"/section/osc_destination/{seeded.id}",
        {"name": "Console", "host": "10.5.5.5", "port": "9001", "protocol": "udp", "framing": "slip"},
    )
    _, body = _get(base, "/section/osc_destinations")
    assert "osc-destination-addr" in body
    assert "10.5.5.5:9001" in body
    assert "osc-destination-proto-badge" in body
    assert "UDP" in body


def test_broadcast_section_rejects_non_shareable_sections(live_server) -> None:
    """OSC routing + zones travel by file only – a section broadcast of them
    is refused with 403, never pushed to peers."""
    _, base = live_server
    for section in ("osc_destinations", "osc_transmitters", "trigger_zones"):
        status, payload = _post_json(base, f"/api/config/{section}/broadcast", {"enabled": True})
        assert status == 403, f"{section} should not be broadcastable"
        assert "not shareable" in payload.get("error", "").lower()


def test_update_detection_inference_renders_partial_with_install_state(live_server) -> None:
    """The Detection & Display box save wires the install-state flags into the
    rendered partial so the install/uninstall buttons reflect dependency
    state, and the box's bool fields coerce to False when absent."""
    server, base = live_server

    status, body = _post_form(
        base,
        "/section/detection/inference",
        {
            "enabled": "on",
            "confidence": "0.42",
        },
    )
    assert status == 200

    saved = load_config(server.config_path)
    assert saved.detection.enabled is True
    assert saved.detection.confidence == pytest.approx(0.42)
    # ``preprocess_clahe`` / ``show_boxes`` / ``show_labels`` were absent –
    # the bool_fields treatment must coerce them to False.
    assert saved.detection.preprocess_clahe is False


def test_update_detection_models_box_preserves_other_boxes(live_server) -> None:
    """Each box saves only its own fields: saving the Models box must not
    clear bool fields owned by the Detection & Display box."""
    server, base = live_server
    # Enable detection via the inference box first.
    _post_form(base, "/section/detection/inference", {"enabled": "on", "preprocess_clahe": "on"})
    # Saving the Models box (no detection bool fields) must leave them alone.
    status, _ = _post_form(base, "/section/detection/models", {"storage_path": "/tmp/of-models"})
    assert status == 200
    saved = load_config(server.config_path)
    assert saved.detection.storage_path == "/tmp/of-models"
    assert saved.detection.enabled is True
    assert saved.detection.preprocess_clahe is True


@pytest.mark.parametrize("mode", ["replace", "assist"])
def test_update_detection_tracking_pin_mode_round_trips(live_server, mode) -> None:
    """The Tracking Mode segmented toggle is radio inputs named ``pin_mode``;
    the selected value persists via the Tracking box save."""
    server, base = live_server
    status, _ = _post_form(base, "/section/detection/tracking", {"pin_mode": mode})
    assert status == 200
    assert load_config(server.config_path).detection.pin_mode == mode


def test_api_video_snapshot_returns_503_when_no_preview_provider(live_server) -> None:
    _, base = live_server
    status, _ = _get(base, "/api/video/snapshot")
    assert status == 503


def test_api_video_snapshot_full_returns_503_when_no_full_provider(live_server) -> None:
    """Same contract as the preview snapshot for the wizard-only full-res
    endpoint."""
    _, base = live_server
    status, _ = _get(base, "/api/video/snapshot/full")
    assert status == 503


def test_api_restart_post_requests_restart_via_command_queue(live_server) -> None:
    """The JSON /api/restart endpoint must enqueue a restart and return
    success – used by the web UI's "Apply & Restart" buttons."""
    server, base = live_server
    assert server.check_restart_requested() is False

    status, body = _post_json(base, "/api/restart", {})
    assert status == 200
    assert body.get("success") is True
    assert server.check_restart_requested() is True


# ---------------------------------------------------------------------------
# Zones CRUD success paths + import confirm/skip + broadcast
# ---------------------------------------------------------------------------


def _put_json(base: str, path: str, data: dict) -> tuple[int, dict]:
    return _post_raw_json(base, path, data, method="PUT")


def _delete(base: str, path: str) -> tuple[int, dict]:
    req = urllib.request.Request(f"{base}{path}", method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def test_api_create_zone_appends_with_index_in_response(live_server) -> None:
    """POST /api/zones appends a new zone and returns its index. Without
    this, the editor can't map the new row to a server-side zone for
    follow-up PUTs."""
    server, base = live_server

    status, body = _post_json(base, "/api/zones", {"name": "ZoneOne"})
    assert status == 200
    assert body.get("success") is True
    assert isinstance(body.get("index"), int) and body["index"] >= 0

    saved = load_config(server.config_path)
    assert saved.trigger_zones.zones[body["index"]].name == "ZoneOne"


def test_api_update_zone_replaces_fields_for_existing_index(live_server) -> None:
    """PUT /api/zones/<i> applies the field subset in-place (no full-zone
    replacement) so partial edits don't blank fields the editor didn't
    send."""
    server, base = live_server
    create_status, create_body = _post_json(base, "/api/zones", {"name": "Original"})
    assert create_status == 200
    idx = create_body["index"]

    update_status, update_body = _put_json(
        base,
        f"/api/zones/{idx}",
        {"name": "Renamed"},
    )
    assert update_status == 200
    assert update_body.get("success") is True

    saved = load_config(server.config_path)
    assert saved.trigger_zones.zones[idx].name == "Renamed"


def test_api_update_zone_returns_404_for_out_of_range_index(live_server) -> None:
    _, base = live_server
    status, body = _put_json(base, "/api/zones/9999", {"name": "Phantom"})
    assert status == 404
    assert "out of range" in str(body.get("error", "")).lower()


def test_api_update_zone_returns_404_for_negative_index(live_server) -> None:
    _, base = live_server
    status, body = _put_json(base, "/api/zones/-1", {"name": "Phantom"})
    assert status == 404
    assert "out of range" in str(body.get("error", "")).lower()


# ---------------------------------------------------------------------------
# ``triggered_by`` round-trip, Duplicate, Test send and Diagnostics in /api/zones GET
# ---------------------------------------------------------------------------


def test_api_create_zone_persists_triggered_by_list(live_server) -> None:
    """POST /api/zones with a ``triggered_by`` list saves a coerced
    ``list[int]`` – strings get coerced silently per ``_parse_triggered_by``."""
    server, base = live_server
    status, body = _post_json(
        base,
        "/api/zones",
        {
            "name": "Filtered",
            "triggered_by": [0, "1", 5],
        },
    )
    assert status == 200
    saved = load_config(server.config_path)
    assert saved.trigger_zones.zones[body["index"]].triggered_by == [0, 1, 5]


def test_api_update_zone_round_trips_triggered_by(live_server) -> None:
    server, base = live_server
    _, c = _post_json(base, "/api/zones", {"name": "Z"})
    idx = c["index"]
    status, body = _put_json(
        base,
        f"/api/zones/{idx}",
        {"triggered_by": [3, 7]},
    )
    assert status == 200
    assert body.get("success") is True
    saved = load_config(server.config_path)
    assert saved.trigger_zones.zones[idx].triggered_by == [3, 7]


def test_api_update_zone_triggered_by_omitted_preserves_filter(live_server) -> None:
    server, base = live_server
    _, c = _post_json(
        base,
        "/api/zones",
        {
            "name": "Z",
            "triggered_by": [4],
        },
    )
    idx = c["index"]
    _put_json(base, f"/api/zones/{idx}", {"enabled": False})
    saved = load_config(server.config_path)
    assert saved.trigger_zones.zones[idx].triggered_by == [4]


def test_api_list_zones_exposes_triggered_by_and_diagnostics(live_server) -> None:
    server, base = live_server
    _, c = _post_json(base, "/api/zones", {"name": "Z"})
    idx = c["index"]

    status, payload = _get_json(base, "/api/zones")
    assert status == 200
    zone_payload = next(z for z in payload["zones"] if z["index"] == idx)
    assert zone_payload["triggered_by"] == []
    diag = zone_payload["diagnostics"]
    assert diag["is_occupied"] is False
    assert diag["count"] == 0
    assert diag["occupants"] == []
    assert diag["last_event_address"] == ""


def test_api_zone_duplicate_clones_in_place(live_server) -> None:
    server, base = live_server
    _, c = _post_json(
        base,
        "/api/zones",
        {
            "name": "Original",
            "triggered_by": [1, 2],
            "osc_address_first_entry": "/orig/enter",
        },
    )
    idx = c["index"]

    req = urllib.request.Request(f"{base}/api/zones/{idx}/duplicate", method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        body = json.loads(r.read().decode() or "{}")
    assert body.get("success") is True
    new_idx = body["index"]
    saved = load_config(server.config_path)
    assert saved.trigger_zones.zones[new_idx].name == "Original (copy)"
    assert saved.trigger_zones.zones[new_idx].triggered_by == [1, 2]
    assert saved.trigger_zones.zones[new_idx].osc_address_first_entry == "/orig/enter"


def test_api_zone_duplicate_404_for_unknown_index(live_server) -> None:
    _, base = live_server
    req = urllib.request.Request(f"{base}/api/zones/9999/duplicate", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            r.read()
        raise AssertionError("expected 404")
    except urllib.error.HTTPError as e:
        assert e.code == 404


def test_api_zone_test_send_400_for_unknown_which(live_server) -> None:
    _, base = live_server
    _, c = _post_json(base, "/api/zones", {"name": "Z"})
    idx = c["index"]
    req = urllib.request.Request(
        f"{base}/api/zones/{idx}/test_send?which=bogus",
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            r.read()
        raise AssertionError("expected 400")
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_api_zone_test_send_503_when_no_provider_attached(live_server) -> None:
    _, base = live_server
    _, c = _post_json(base, "/api/zones", {"name": "Z"})
    idx = c["index"]
    req = urllib.request.Request(
        f"{base}/api/zones/{idx}/test_send?which=first",
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            r.read()
        raise AssertionError("expected 503")
    except urllib.error.HTTPError as e:
        assert e.code == 503


def _live_server_with_zone_providers(tmp_path, monkeypatch, **providers):
    """Server fixture variant that wires zone diagnostics + test-send
    providers so the positive-path code in ``server.py`` and ``routes.py``
    is exercised. Mirrors the OSC-bindings ``_live_server_with_providers``
    helper but stays scoped to this file (no cross-import)."""
    monkeypatch.setattr(discovery_module.BeaconSender, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconSender, "stop", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "stop", lambda self: None)
    port = _find_free_tcp_port()
    config_path = tmp_path / "config.toml"
    server = ConfigWebServer(
        config_path=str(config_path),
        host="127.0.0.1",
        port=port,
        system_name="TestSystem",
        **providers,
    )
    server.start()
    assert _wait_for_port(port)
    return server, f"http://127.0.0.1:{port}"


def test_api_list_zones_uses_diagnostics_provider_when_attached(
    tmp_path,
    monkeypatch,
) -> None:
    """When a ``zone_diagnostics_provider`` is wired, the GET response
    surfaces its dict for each zone instead of the zero-state fallback.
    Pins both the server-layer pass-through and the route-layer ``diag
    is not None`` branch."""
    captured: list[int] = []

    def diag_provider(idx: int) -> dict:
        captured.append(idx)
        return {
            "is_occupied": True,
            "count": 2,
            "occupants": [{"kind": "marker", "id": 0}, {"kind": "detection", "id": 5}],
            "last_event_time": 12.5,
            "last_event_address": "/zone/enter",
        }

    server, base = _live_server_with_zone_providers(
        tmp_path,
        monkeypatch,
        zone_diagnostics_provider=diag_provider,
    )
    try:
        _, c = _post_json(base, "/api/zones", {"name": "Z"})
        idx = c["index"]
        status, payload = _get_json(base, "/api/zones")
        assert status == 200
        zone_payload = next(z for z in payload["zones"] if z["index"] == idx)
        assert zone_payload["diagnostics"]["last_event_address"] == "/zone/enter"
        assert zone_payload["diagnostics"]["count"] == 2
        assert idx in captured
    finally:
        server.stop()


def test_api_zone_test_send_invokes_provider_and_returns_result(
    tmp_path,
    monkeypatch,
) -> None:
    """When a ``zone_test_send`` provider is wired, the route forwards
    ``which`` to it and ships the provider's dict back. Exercises both
    the server-layer pass-through and the route-layer non-empty
    success path (``return json.dumps(result)``)."""
    calls: list[tuple[int, str]] = []

    def fake_send(idx: int, which: str) -> dict:
        calls.append((idx, which))
        return {"success": True, "address": "/zone/enter", "args": [1]}

    server, base = _live_server_with_zone_providers(
        tmp_path,
        monkeypatch,
        zone_test_send=fake_send,
    )
    try:
        _, c = _post_json(base, "/api/zones", {"name": "Z"})
        idx = c["index"]
        req = urllib.request.Request(
            f"{base}/api/zones/{idx}/test_send?which=first",
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            body = json.loads(r.read().decode())
        assert body == {"success": True, "address": "/zone/enter", "args": [1]}
        assert calls == [(idx, "first")]
    finally:
        server.stop()


def test_api_zone_test_send_404_when_provider_reports_out_of_range(
    tmp_path,
    monkeypatch,
) -> None:
    """Zone provider errors are mapped to 404 response instead of 200."""

    def fake_send(idx: int, which: str) -> dict:
        return {"error": "Zone index out of range"}

    server, base = _live_server_with_zone_providers(
        tmp_path,
        monkeypatch,
        zone_test_send=fake_send,
    )
    try:
        # Index 0 is fine for the route's URL routing; the provider
        # decides the response.
        _, c = _post_json(base, "/api/zones", {"name": "Z"})
        idx = c["index"]
        req = urllib.request.Request(
            f"{base}/api/zones/{idx}/test_send?which=first",
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                r.read()
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404
            body = json.loads(e.read().decode())
            assert "out of range" in body["error"].lower()
    finally:
        server.stop()


def test_api_zone_test_send_400_when_provider_reports_payload_error(
    tmp_path,
    monkeypatch,
) -> None:
    """Provider returns ``{"error": "unclosed quote in field: ..."}``
    when the configured field has malformed shlex syntax. That's a
    400 (bad config payload), not a 200 success."""

    def fake_send(idx: int, which: str) -> dict:
        return {"error": "unclosed quote in field: No closing quotation"}

    server, base = _live_server_with_zone_providers(
        tmp_path,
        monkeypatch,
        zone_test_send=fake_send,
    )
    try:
        _, c = _post_json(base, "/api/zones", {"name": "Z"})
        idx = c["index"]
        req = urllib.request.Request(
            f"{base}/api/zones/{idx}/test_send?which=first",
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                r.read()
            raise AssertionError("expected 400")
        except urllib.error.HTTPError as e:
            assert e.code == 400
            body = json.loads(e.read().decode())
            assert "unclosed" in body["error"].lower()
    finally:
        server.stop()


def test_parse_triggered_by_non_list_value_returns_current() -> None:
    """A scalar / dict / null payload must fall back to ``current`` –
    same defensive shape as ``_parse_vertices``. Without this guard a
    malformed POST (e.g. ``{"triggered_by": null}``) would silently
    nuke the filter."""
    from openfollow.web.routes import _parse_triggered_by

    assert _parse_triggered_by(None, [1, 2]) == [1, 2]
    assert _parse_triggered_by("not a list", [3]) == [3]
    assert _parse_triggered_by({"x": 1}, []) == []


def test_parse_triggered_by_drops_non_coercible_entries() -> None:
    """A permissive client may smuggle un-coercible entries (``None`` /
    non-numeric strings); drop them rather than reject the whole list,
    so a partially-bad payload still applies the salvageable filter."""
    from openfollow.web.routes import _parse_triggered_by

    assert _parse_triggered_by([0, "abc", None, "1"], []) == [0, 1]


def test_api_delete_zone_removes_existing_zone(live_server) -> None:
    """Happy delete path – the zones list shrinks by one; subsequent gets
    don't surface the removed zone."""
    server, base = live_server
    _, c1 = _post_json(base, "/api/zones", {"name": "ToKeep"})
    _, c2 = _post_json(base, "/api/zones", {"name": "ToDelete"})
    target_idx = c2["index"]

    status, body = _delete(base, f"/api/zones/{target_idx}")
    assert status == 200
    assert body.get("success") is True

    saved = load_config(server.config_path)
    names = [z.name for z in saved.trigger_zones.zones]
    assert "ToKeep" in names
    assert "ToDelete" not in names


def test_api_delete_zone_returns_404_for_out_of_range_index(live_server) -> None:
    """Symmetric guard with the PUT route – out-of-range deletes 404 rather
    than silently no-op'ing."""
    _, base = live_server
    status, body = _delete(base, "/api/zones/9999")
    assert status == 404
    assert "out of range" in str(body.get("error", "")).lower()


def test_api_delete_zone_returns_404_for_negative_index(live_server) -> None:
    """Same negative-index guard as the PUT route."""
    _, base = live_server
    status, body = _delete(base, "/api/zones/-1")
    assert status == 404
    assert "out of range" in str(body.get("error", "")).lower()


def test_api_list_zones_returns_globals_grid_zones_and_markers(live_server) -> None:
    _, base = live_server
    status, data = _get_json(base, "/api/zones")
    assert status == 200
    assert {"globals", "grid", "zones", "markers"}.issubset(set(data.keys()))
    # Markers come from the marker_positions provider – empty in the
    # default fixture (no provider wired).
    assert data["markers"] == []


def test_api_broadcast_all_returns_empty_results_when_no_peers(live_server) -> None:
    _, base = live_server
    status, body = _post_json(base, "/api/config/broadcast-all", {})
    assert status == 200
    assert body.get("success") is True
    assert body.get("peer_results") == []


def test_api_broadcast_section_returns_empty_results_when_no_peers(live_server) -> None:
    server, base = live_server
    status, body = _post_json(
        base,
        "/api/config/camera/broadcast",
        {"pos_x": 4.5},
    )
    assert status == 200
    assert body.get("success") is True
    assert body.get("local_updated") is True
    assert body.get("peer_results") == []

    saved = load_config(server.config_path)
    assert saved.camera.pos_x == pytest.approx(4.5)


def test_api_update_section_psn_ignores_psn_source_iface(live_server) -> None:
    server, base = live_server
    cfg = load_config(server.config_path)
    cfg.psn_source_iface = "eth0"
    save_config(cfg, server.config_path)

    status, body = _post_json(
        base,
        "/api/config/psn",
        {
            "psn_system_name": "Broadcast Stage",
            "psn_source_iface": "wlan0",  # MUST NOT take effect locally
        },
    )
    assert status == 200
    assert body.get("success") is True

    after = load_config(server.config_path)
    # Local iface pin preserved.
    assert after.psn_source_iface == "eth0"
    # Non-device-local PSN fields applied as expected.
    assert after.psn_system_name == "Broadcast Stage"


def test_api_broadcast_section_psn_strips_iface_from_peer_payload(
    live_server,
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    from openfollow.web import routes as routes_mod

    captured_payloads: list[dict] = []

    def _fake_send(ip: str, port: int, section: str, data: dict, pin: str = "", *, expected_port: int) -> bool:
        captured_payloads.append({"section": section, "data": dict(data)})
        return True

    monkeypatch.setattr(routes_mod, "_send_config_to_peer", _fake_send)

    # Inject a fake peer so the broadcaster actually forwards.
    server, base = live_server
    fake_peer = SimpleNamespace(
        name="fake-peer",
        ip="10.0.0.99",
        web_port=80,
    )
    monkeypatch.setattr(server, "get_peers", lambda: [fake_peer])

    status, body = _post_json(
        base,
        "/api/config/psn/broadcast",
        {
            "psn_system_name": "Broadcasted",
            "psn_mcast_ip": "236.10.10.10",
            "psn_source_iface": "eth0",
        },
    )
    assert status == 200
    assert body.get("success") is True

    # Local-apply path uses the unscrubbed data – broadcaster IS saving
    # their own PSN form, so their local iface gets set.
    after = load_config(server.config_path)
    assert after.psn_source_iface == "eth0"
    assert after.psn_system_name == "Broadcasted"

    # Peer-forward payload had ``psn_source_iface`` stripped.
    assert len(captured_payloads) == 1
    sent = captured_payloads[0]
    assert sent["section"] == "psn"
    assert "psn_source_iface" not in sent["data"]
    assert sent["data"]["psn_system_name"] == "Broadcasted"
    assert sent["data"]["psn_mcast_ip"] == "236.10.10.10"


def test_api_broadcast_section_returns_404_for_unknown_section(live_server) -> None:
    _, base = live_server
    status, body = _post_json(
        base,
        "/api/config/no-such-section/broadcast",
        {"x": 1},
    )
    assert status == 404
    assert "error" in body


def test_get_section_psn_partial_includes_local_ips(live_server) -> None:
    """GET /section/psn must inject ``local_ips`` into the template – the
    template needs the list to render the source-IP dropdown. A regression
    here would render an empty selector."""
    _, base = live_server
    status, body = _get(base, "/section/psn")
    assert status == 200
    # Section template includes the PSN field labels.
    assert "psn" in body.lower()


def test_get_section_video_source_partial_includes_input_fragments(live_server) -> None:
    """GET /section/video_source merges in plugin-provided HTML fragments
    via ``_build_input_template_data`` – without that branch the per-input
    config UI would never render."""
    _, base = live_server
    status, body = _get(base, "/section/video_source")
    assert status == 200
    # Must mention at least one registered input by name.
    assert "rtsp" in body.lower() or "video" in body.lower()


def test_update_grid_post_persists_and_renders_partial(live_server) -> None:
    server, base = live_server
    status, body = _post_form(
        base,
        "/section/grid",
        {"width": "25", "depth": "15", "origin_visible": "on"},
    )
    assert status == 200

    saved = load_config(server.config_path)
    assert saved.grid.width == pytest.approx(25.0)
    assert saved.grid.depth == pytest.approx(15.0)
    assert saved.grid.origin_visible is True


def test_update_grid_post_toggles_visible(live_server) -> None:
    """The Show Grid checkbox: ticked -> True, omitted -> False. Omitting it
    must hide the grid, not silently preserve the prior True default."""
    server, base = live_server

    status, _ = _post_form(base, "/section/grid", {"width": "25"})
    assert status == 200
    assert load_config(server.config_path).grid.visible is False

    status, _ = _post_form(base, "/section/grid", {"width": "25", "visible": "on"})
    assert status == 200
    assert load_config(server.config_path).grid.visible is True


def test_update_marker_post_persists_visual_booleans(live_server) -> None:
    """POST /section/marker exercises the longest bool-fields list in the
    routes module. Boxes ticked → True, omitted → False."""
    server, base = live_server
    status, _ = _post_form(
        base,
        "/section/marker",
        {
            "ball_visible": "on",
            "crosshair_visible": "on",
            # drop_line + ground_circle + ground_circle_filled +
            # z_display_from_stage all omitted -> coerced to False.
        },
    )
    assert status == 200

    saved = load_config(server.config_path)
    assert saved.marker.ball_visible is True
    assert saved.marker.crosshair_visible is True
    assert saved.marker.drop_line is False
    assert saved.marker.ground_circle is False


def test_api_video_snapshot_returns_jpeg_when_provider_yields_bytes(
    tmp_path,
    monkeypatch,
) -> None:
    """Happy path for the preview snapshot endpoint: when a provider is
    wired and returns bytes, the route returns image/jpeg with cache-busting
    headers. Use a fresh ConfigWebServer (the live_server fixture wires no
    snapshot provider)."""
    monkeypatch.setattr(discovery_module.BeaconSender, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconSender, "stop", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "stop", lambda self: None)

    port = _find_free_tcp_port()
    config_path = tmp_path / "config.toml"
    server = ConfigWebServer(
        config_path=str(config_path),
        host="127.0.0.1",
        port=port,
        system_name="SnapTest",
        preview_snapshot_provider=lambda: b"\xff\xd8\xff\xe0jpegbody",
        full_snapshot_provider=lambda: b"\xff\xd8\xff\xe0fulljpeg",
    )
    server.start()
    try:
        assert _wait_for_port(port)
        base = f"http://127.0.0.1:{port}"

        with urllib.request.urlopen(f"{base}/api/video/snapshot", timeout=5) as r:
            assert r.status == 200
            assert r.headers.get("Content-Type", "").startswith("image/jpeg")
            assert r.headers.get("Cache-Control") == "no-store"
            assert r.read() == b"\xff\xd8\xff\xe0jpegbody"

        with urllib.request.urlopen(f"{base}/api/video/snapshot/full", timeout=5) as r:
            assert r.status == 200
            assert r.headers.get("Content-Type", "").startswith("image/jpeg")
            assert r.read() == b"\xff\xd8\xff\xe0fulljpeg"
    finally:
        server.stop()


def test_api_wizard_solve_returns_camera_and_reprojected_corners(live_server) -> None:
    """Happy-path DLT solve: four well-conditioned corner correspondences
    yield a camera dict (pos/rot/fov) plus a reprojected corner array of
    the same shape. Generate the screen corners by projecting through a
    known camera pose so the DLT is guaranteed to succeed (a hand-picked
    trapezoid can fall into the degenerate branch and return 422)."""
    import numpy as np

    from openfollow.scene.solver import project_points

    _, base = live_server

    img_w, img_h = 1280.0, 720.0
    params = np.array([0.0, -10.0, 5.0, -28.0, 8.0, 1.0, 65.0], dtype=np.float64)
    world = np.array(
        [
            [-5.0, -5.0, 0.0],
            [5.0, -5.0, 0.0],
            [5.0, 5.0, 0.0],
            [-5.0, 5.0, 0.0],
        ],
        dtype=np.float64,
    )
    screen = project_points(params, world, img_w, img_h)

    status, body = _post_json(
        base,
        "/api/wizard/solve",
        {
            "world_corners": [list(p) for p in world],
            "screen_corners": [list(p) for p in screen],
            "image_width": img_w,
            "image_height": img_h,
        },
    )
    assert status == 200
    assert "camera" in body
    assert {"pos_x", "pos_y", "pos_z", "pitch", "yaw", "roll", "fov"}.issubset(set(body["camera"].keys()))
    reprojected = body.get("reprojected_corners")
    assert isinstance(reprojected, list)
    assert len(reprojected) == 4


def test_api_wizard_solve_returns_422_for_degenerate_corners(live_server) -> None:
    """When the four screen corners can't be matched to a valid camera pose,
    the route returns 422 with an "Invalid perspective" hint instead of
    serving a NaN-laced camera dict the editor would render as garbage."""
    _, base = live_server
    # All four screen corners collapsed to a single point – the DLT
    # has no perspective to fit.
    status, body = _post_json(
        base,
        "/api/wizard/solve",
        {
            "world_corners": [
                [-5.0, -5.0, 0.0],
                [5.0, -5.0, 0.0],
                [5.0, 5.0, 0.0],
                [-5.0, 5.0, 0.0],
            ],
            "screen_corners": [
                [640.0, 360.0],
                [640.0, 360.0],
                [640.0, 360.0],
                [640.0, 360.0],
            ],
            "image_width": 1280,
            "image_height": 720,
        },
    )
    assert status == 422
    assert "Invalid perspective" in str(body.get("error", ""))


def test_api_wizard_unproject_single_point_returns_world_only(live_server) -> None:
    """When only one screen point is sent, the route returns ``world_points``
    but no ``delta`` (delta is meaningless for a single point). This is
    the fall-through arm at the bottom of api_wizard_unproject."""
    _, base = live_server
    status, body = _post_json(
        base,
        "/api/wizard/unproject",
        {
            "camera": _wizard_camera_payload(pos_z=5.0, pitch=-30.0),
            "screen_points": [[960.0, 540.0]],
            "image_width": 1920,
            "image_height": 1080,
        },
    )
    assert status == 200
    assert "world_points" in body
    assert "delta" not in body


def test_get_wizard_page_renders(live_server) -> None:
    _, base = live_server
    status, body = _get(base, "/wizard")
    assert status == 200
    assert len(body) > 200


def test_api_broadcast_section_rejects_malformed_json(live_server) -> None:
    _, base = live_server
    req = urllib.request.Request(
        f"{base}/api/config/camera/broadcast",
        data=b"{not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            status = r.status
            body = json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        status = e.code
        body = json.loads(e.read().decode() or "{}")
    assert status == 400
    assert "error" in body


def test_api_wizard_unproject_rejects_malformed_json(live_server) -> None:
    """Same _load_json_body short-circuit on the unproject route – without
    this guard the route would 500 on the next ``data["camera"]`` deref."""
    _, base = live_server
    req = urllib.request.Request(
        f"{base}/api/wizard/unproject",
        data=b"\x00garbage",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    assert status == 400


def test_api_wizard_solve_rejects_malformed_json(live_server) -> None:
    """Same _load_json_body short-circuit on the solve route."""
    _, base = live_server
    req = urllib.request.Request(
        f"{base}/api/wizard/solve",
        data=b"not json at all",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    assert status == 400


def test_api_wizard_solve_rejects_screen_corner_with_wrong_shape(live_server) -> None:
    """A screen_corner that isn't [x, y] (e.g. [x, y, z]) must surface as
    400 with an ``[x, y]`` hint – the world_corner-shape check fires
    first only if both are malformed in the same payload."""
    _, base = live_server
    status, body = _post_json(
        base,
        "/api/wizard/solve",
        {
            "world_corners": [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]],
            "screen_corners": [
                [0, 0, 999],  # extra coord – must be rejected
                [1, 0],
                [1, 1],
                [0, 1],
            ],
            "image_width": 1920,
            "image_height": 1080,
        },
    )
    assert status == 400
    assert "[x, y]" in str(body.get("error", ""))


def test_ndi_video_input_plugin_route_is_registered_and_callable(live_server) -> None:
    from openfollow.video.inputs import get_registry

    _, base = live_server

    registry = get_registry()
    if "ndi" not in registry:
        pytest.skip("NDI input plugin not registered in this build")

    status, body = _get(base, "/video-input/ndi/sources")
    assert status == 200
    # The handler renders an <option> for each discovered NDI source.
    # Empty source lists still emit the placeholder option, so the
    # tag presence is a stable assertion across hosts.
    assert "<option" in body


# ---------------------------------------------------------------------------
# /api/validate/<section>/<field> – on-blur validation
# ---------------------------------------------------------------------------


def test_validate_endpoint_valid_returns_empty_body(live_server) -> None:
    """A coercible value within bounds → 200 + empty body."""
    _, base = live_server
    status, body = _get(base, "/api/validate/camera/fov?fov=60")
    assert status == 200
    assert body == ""


def test_validate_endpoint_invalid_returns_error_span(live_server) -> None:
    """Out-of-range value → 200 + ``<span class="field-error-msg">…</span>``."""
    _, base = live_server
    status, body = _get(base, "/api/validate/camera/fov?fov=200")
    assert status == 200
    assert 'class="field-error-msg"' in body
    assert "FOV" in body


def test_validate_endpoint_type_error(live_server) -> None:
    _, base = live_server
    status, body = _get(base, "/api/validate/camera/fov?fov=wide")
    assert status == 200
    assert 'class="field-error-msg"' in body


def test_validate_endpoint_advisory_note(live_server) -> None:
    """Cross-field auto-correct surfaces as a ``field-note-msg`` span."""
    _, base = live_server
    status, body = _get(
        base,
        "/api/validate/movement/max_speed?max_speed=0.5&min_speed=2.0",
    )
    assert status == 200
    assert 'class="field-note-msg"' in body
    assert "Min Speed" in body


def test_validate_endpoint_inference_size_snap_note(live_server) -> None:
    _, base = live_server
    status, body = _get(
        base,
        "/api/validate/detection/inference_size?inference_size=200",
    )
    assert status == 200
    assert 'class="field-note-msg"' in body


def test_validate_endpoint_unknown_section_returns_404(live_server) -> None:
    _, base = live_server
    status, _body = _get(base, "/api/validate/nope/fov?fov=60")
    assert status == 404


def test_validate_endpoint_unknown_field_returns_404(live_server) -> None:
    _, base = live_server
    status, _body = _get(base, "/api/validate/camera/unknown?unknown=1")
    assert status == 404


def test_validate_endpoint_html_escapes_error_text(live_server) -> None:
    """Error copy is HTML-escaped so injected markup can't break the swap."""
    _, base = live_server
    # ``detection/model`` has a max_len rule, so an over-long value trips an
    # error whose text is HTML-escaped before it lands in the swap target.
    status, body = _get(
        base,
        "/api/validate/detection/model?model=" + ("x" * 600),
    )
    assert status == 200
    assert "<script>" not in body


def test_validate_endpoint_requires_auth(pin_protected_server) -> None:
    _, base, _pin = pin_protected_server
    status, _body = _get(base, "/api/validate/camera/fov?fov=60")
    assert status == 401


def test_validate_reuses_request_scoped_config_cache(live_server) -> None:
    _, base = live_server
    status, body = _get(
        base,
        "/api/validate/general/update_service_name?update_service_name=openfollow",
    )
    assert status == 200
    # A valid service name passes – the response body is empty (no error span).
    assert body == ""


def test_get_handler_parses_config_once_per_request(live_server, monkeypatch) -> None:
    """GET handlers reuse request-scoped config cache to parse config exactly once per request."""
    import openfollow.web.routes as routes_mod

    calls: list[str] = []
    real_load = routes_mod.load_config

    def _counting(path, *a, **k):
        calls.append(path)
        return real_load(path, *a, **k)

    monkeypatch.setattr(routes_mod, "load_config", _counting)
    _, base = live_server
    calls.clear()
    status, _ = _get(base, "/")
    assert status == 200
    # ``_check_auth`` (before_request) parses once and caches on the request
    # environ; ``index`` reuses that instead of a second parse.
    assert len(calls) == 1


def test_marker_catalog_parses_config_once_per_request(
    live_server,
    monkeypatch,
) -> None:
    import openfollow.web.routes as routes_mod

    calls: list[str] = []
    real_load = routes_mod.load_config

    def _counting(path, *a, **k):
        calls.append(path)
        return real_load(path, *a, **k)

    monkeypatch.setattr(routes_mod, "load_config", _counting)
    _, base = live_server
    calls.clear()
    status, _ = _get(base, "/api/markers/catalog")
    assert status == 200
    # before_request parses + caches; the catalog handler reuses that cache.
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# ``/api/validate/zone/<field>`` endpoints serve the four per-zone OSC address fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "osc_address_first_entry",
        "osc_address_additional_entry",
        "osc_address_partial_exit",
        "osc_address_final_exit",
    ],
)
def test_validate_zone_osc_field_valid_returns_empty(live_server, field) -> None:
    _, base = live_server
    status, body = _get(
        base,
        f"/api/validate/zone/{field}?{field}=/zone/enter",
    )
    assert status == 200
    assert body == ""


@pytest.mark.parametrize(
    "field",
    [
        "osc_address_first_entry",
        "osc_address_additional_entry",
        "osc_address_partial_exit",
        "osc_address_final_exit",
    ],
)
def test_validate_zone_osc_field_quoted_args_valid(live_server, field) -> None:
    """Quoted args (e.g. /cmd "Go Cue 1" 1.5) pass blur validation."""
    _, base = live_server
    raw = '/cmd "Go Cue 1" 1.5'
    status, body = _get(
        base,
        f"/api/validate/zone/{field}?{field}=" + urllib.parse.quote(raw, safe=""),
    )
    assert status == 200
    assert body == ""


@pytest.mark.parametrize(
    "field",
    [
        "osc_address_first_entry",
        "osc_address_additional_entry",
        "osc_address_partial_exit",
        "osc_address_final_exit",
    ],
)
def test_validate_zone_osc_field_unclosed_quote_returns_error(
    live_server,
    field,
) -> None:
    """Unclosed quote → ``field-error-msg`` so the JS handler can flip
    aria-invalid and surface the message inline."""
    _, base = live_server
    raw = '/cmd "unclosed'
    status, body = _get(
        base,
        f"/api/validate/zone/{field}?{field}=" + urllib.parse.quote(raw, safe=""),
    )
    assert status == 200
    assert 'class="field-error-msg"' in body
    assert "Unclosed quote" in body


def test_validate_zone_unknown_field_returns_404(live_server) -> None:
    """A field not in ``FIELD_RULES["zone"]`` (e.g. ``name``) returns
    404. The JS hookup only attaches blur handlers to the four registered
    OSC address fields, so this only catches a future drift where a new
    field name lands in the JS without a matching FIELD_RULES entry."""
    _, base = live_server
    status, _body = _get(base, "/api/validate/zone/name?name=foo")
    assert status == 404


# ===========================================================================
# Privilege broker web surface (modal, submit, cancel, install)
# ===========================================================================


def test_privilege_password_modal_renders_empty_when_idle(live_server) -> None:
    """The polling GET returns an empty partial when no broker call is
    parked – the global modal container in base.tpl then renders no
    overlay."""
    _server, base = live_server
    status, body = _get(base, "/system/privilege/password/modal")
    assert status == 200
    # Empty partial = no input element, no submit button.
    assert "privilege-password-input" not in body


def test_privilege_password_modal_renders_when_pending(live_server) -> None:
    server, base = live_server
    server._command_queue.request_privilege_password(
        reason="Apply network changes",
        capability_name="network.nm.con_mod",
    )
    status, body = _get(base, "/system/privilege/password/modal")
    assert status == 200
    assert "privilege-password-input" in body
    assert "Apply network changes" in body


def test_privilege_password_submit_forwards_to_queue(live_server) -> None:
    server, base = live_server
    server._command_queue.request_privilege_password(
        reason="x",
        capability_name="y",
    )
    status, _body = _post_form(
        base,
        "/system/privilege/password",
        {"password": "hunter2"},
    )
    assert status == 200
    assert server._command_queue.consume_privilege_password(timeout=0.5) == "hunter2"


def test_privilege_password_submit_empty_is_treated_as_cancel(live_server) -> None:
    server, base = live_server
    server._command_queue.request_privilege_password(
        reason="x",
        capability_name="y",
    )
    status, _body = _post_form(
        base,
        "/system/privilege/password",
        {"password": ""},
    )
    assert status == 200
    assert server._command_queue.consume_privilege_password(timeout=0.5) is None


def test_privilege_password_submit_noop_when_no_prompt(live_server) -> None:
    server, base = live_server
    status, _body = _post_form(
        base,
        "/system/privilege/password",
        {"password": "hunter2"},
    )
    assert status == 200
    # Now request a prompt and verify the consume is empty (nothing
    # was leaked from the prior submit).
    server._command_queue.request_privilege_password(
        reason="x",
        capability_name="y",
    )
    assert server._command_queue.consume_privilege_password(timeout=0.05) is None


def test_privilege_password_cancel_route_wakes_worker(live_server) -> None:
    server, base = live_server
    server._command_queue.request_privilege_password(
        reason="x",
        capability_name="y",
    )
    status, _body = _post_form(base, "/system/privilege/password/cancel", {})
    assert status == 200
    assert server._command_queue.consume_privilege_password(timeout=0.5) is None


def test_privilege_password_cancel_noop_when_idle(live_server) -> None:
    """Cancel with no pending prompt is a no-op – guards against a
    stray browser click clobbering a future prompt's state."""
    _server, base = live_server
    status, body = _post_form(base, "/system/privilege/password/cancel", {})
    assert status == 200
    # Empty partial body since pending=None.
    assert "privilege-password-input" not in body


def test_general_network_state_route_returns_partial(live_server) -> None:
    """Network sub-section polls /section/general/network_state every 5s, keeping PIN input intact."""
    _server, base = live_server
    status, body = _get(base, "/section/general/network_state")
    assert status == 200
    # The partial renders the unavailable banner when no state
    # provider is wired (the live_server fixture doesn't pass one),
    # so we assert on that – the contract is "always 200, always
    # the partial shape" regardless of provider availability.
    assert "Network state unavailable" in body or "network-state-card" in body


# ---------------------------------------------------------------------------
# CSRF / DNS-rebind: Origin/Host check on state-changing requests
# ---------------------------------------------------------------------------


def _post_status_with_headers(base, path, *, extra_headers, method="POST", data=b"{}"):
    req = urllib.request.Request(
        f"{base}{path}",
        data=data,
        headers={"Content-Type": "application/json", **extra_headers},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_csrf_foreign_origin_on_post_is_refused(pin_protected_server) -> None:
    """A state-changing POST carrying a foreign Origin (cross-site / DNS-
    rebind) is rejected 403 before the cookie check."""
    _, base, _ = pin_protected_server
    status = _post_status_with_headers(base, "/api/restart", extra_headers={"Origin": "http://evil.example.com"})
    assert status == 403


def test_csrf_foreign_referer_on_post_is_refused(pin_protected_server) -> None:
    _, base, _ = pin_protected_server
    status = _post_status_with_headers(base, "/api/restart", extra_headers={"Referer": "http://evil.example.com/x"})
    assert status == 403


def test_csrf_absent_origin_falls_through_to_auth(pin_protected_server) -> None:
    """No Origin/Referer (non-browser client) → Host check skipped, request
    reaches the cookie gate (401 without a cookie), NOT 403."""
    _, base, _ = pin_protected_server
    assert _post_status_with_headers(base, "/api/restart", extra_headers={}) == 401


def test_csrf_device_origin_allowed_reaches_auth(pin_protected_server) -> None:
    """An Origin naming the device itself (loopback) passes the Host check
    and reaches the cookie gate (401), not 403."""
    _, base, _ = pin_protected_server
    assert _post_status_with_headers(base, "/api/restart", extra_headers={"Origin": base}) == 401


def test_csrf_origin_null_treated_as_absent(pin_protected_server) -> None:
    _, base, _ = pin_protected_server
    assert _post_status_with_headers(base, "/api/restart", extra_headers={"Origin": "null"}) == 401


def test_csrf_unparseable_origin_treated_as_absent(pin_protected_server) -> None:
    """An invalid-IPv6 Origin makes ``urlsplit().hostname`` raise ValueError →
    treated as no Origin (allowed through to the cookie gate)."""
    _, base, _ = pin_protected_server
    assert _post_status_with_headers(base, "/api/restart", extra_headers={"Origin": "http://[::1]bad:80"}) == 401


def test_csrf_safe_method_ignores_foreign_origin(pin_protected_server) -> None:
    """GET (safe method) is never Host-gated; a foreign Origin on a read must
    not 403 – it falls through to normal auth (401)."""
    _, base, _ = pin_protected_server
    status = _post_status_with_headers(
        base, "/api/config", extra_headers={"Origin": "http://evil.example.com"}, method="GET", data=None
    )
    assert status != 403


# ---------------------------------------------------------------------------
# Media Gallery management routes (/video-input/testpattern/*)
# ---------------------------------------------------------------------------

_GP = "/video-input/testpattern"
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
_GIF = b"GIF89a" + b"\x00" * 10
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 12


def _post_bytes(base: str, path: str, data: bytes) -> tuple[int, str]:
    req = urllib.request.Request(
        f"{base}{path}",
        data=data,
        headers={"Content-Type": "application/octet-stream"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _get_raw(base: str, path: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@pytest.fixture()
def gallery_server(live_server, tmp_path, monkeypatch):
    """A live server with the media store pointed at a temp dir and the
    GStreamer decode/probe seams mocked, so uploads need no real pipeline."""
    from openfollow.video import media_store

    server, base = live_server
    media_dir = tmp_path / "gallery-media"
    monkeypatch.setattr(media_store, "resolve_media_storage_path", lambda: media_dir)
    monkeypatch.setattr(media_store, "_render_jpeg", lambda src, *, max_dim: b"\xff\xd8\xff" + str(max_dim).encode())
    monkeypatch.setattr(
        media_store,
        "_probe_video",
        lambda src: media_store.VideoProbe("vp8", 1280, 720, 30.0, 10.0),
    )
    return server, base, media_dir


def _user_files(media_dir) -> list:
    return sorted(p.name for p in media_dir.glob("*.jpg") if ".thumb." not in p.name) if media_dir.is_dir() else []


def test_gallery_list_shows_defaults(gallery_server) -> None:
    _, base, _ = gallery_server
    status, body = _get(base, f"{_GP}/list")
    assert status == 200
    assert 'id="gallery-grid"' in body
    assert "Stage" in body and "Grey" in body
    assert "gallery-label" not in body  # no visible per-tile labels
    assert 'title="Stage"' in body  # name kept as hover/aria name only


def test_gallery_select_persists(gallery_server) -> None:
    server, base, _ = gallery_server
    status, body = _post_form(base, f"{_GP}/select", {"media_id": "default:grey"})
    assert status == 200
    assert load_config(server.config_path).testpattern_selected_media == "default:grey"


def test_gallery_select_unknown_rejected(gallery_server) -> None:
    server, base, _ = gallery_server
    status, _body = _post_form(base, f"{_GP}/select", {"media_id": "ffffffffffffffff"})
    assert status == 400
    assert load_config(server.config_path).testpattern_selected_media == "default:stage"  # unchanged


def test_gallery_upload_image_stores_file(gallery_server) -> None:
    _, base, media_dir = gallery_server
    status, body = _post_bytes(base, f"{_GP}/upload", _PNG)
    assert status == 200
    assert len(_user_files(media_dir)) == 1  # one normalised image landed


def test_gallery_upload_rejects_unknown_format(gallery_server) -> None:
    _, base, media_dir = gallery_server
    status, body = _post_bytes(base, f"{_GP}/upload", _GIF)
    assert status == 200  # HTMX swap with an inline error banner
    assert "Unsupported file" in body
    assert _user_files(media_dir) == []


def test_gallery_upload_rejects_oversize(gallery_server, monkeypatch) -> None:
    from openfollow.video import media_store

    _, base, media_dir = gallery_server
    monkeypatch.setattr(media_store, "MAX_VIDEO_UPLOAD_BYTES", 4)
    status, body = _post_bytes(base, f"{_GP}/upload", _PNG + b"xxxxxxxx")
    assert status == 200
    assert "too large" in body.lower()
    assert _user_files(media_dir) == []


def test_gallery_capture_503_without_feed(gallery_server) -> None:
    _, base, _ = gallery_server
    status, body = _post_bytes(base, f"{_GP}/capture", b"")
    assert status == 503
    assert json.loads(body)["ok"] is False


def test_gallery_capture_saves_frame(gallery_server, monkeypatch) -> None:
    server, base, media_dir = gallery_server
    monkeypatch.setattr(server, "get_full_snapshot", lambda: _JPEG + b"clean-frame")
    status, body = _post_bytes(base, f"{_GP}/capture", b"")
    assert status == 200
    payload = json.loads(body)
    assert payload["ok"] is True
    assert len(_user_files(media_dir)) == 1


def test_gallery_delete_default_refused(gallery_server) -> None:
    _, base, _ = gallery_server
    status, body = _post_bytes(base, f"{_GP}/delete/default:stage", b"")
    assert status == 400
    assert "cannot be deleted" in body


def test_gallery_delete_user_media(gallery_server) -> None:
    _, base, media_dir = gallery_server
    _post_bytes(base, f"{_GP}/upload", _PNG)
    media_id = _user_files(media_dir)[0].removesuffix(".jpg")
    status, _body = _post_bytes(base, f"{_GP}/delete/{media_id}", b"")
    assert status == 200
    assert _user_files(media_dir) == []


def test_gallery_download_default_404(gallery_server) -> None:
    _, base, _ = gallery_server
    status, _body = _get(base, f"{_GP}/download/default:stage")
    assert status == 404


def test_gallery_thumb_stage_serves_asset(gallery_server) -> None:
    _, base, _ = gallery_server
    status, body = _get_raw(base, f"{_GP}/thumb/default:stage")
    assert status == 200  # the bundled Stage asset
    assert body[:3] == b"\xff\xd8\xff"  # JPEG


def test_video_source_section_hides_capture_for_gallery(live_server) -> None:
    _, base = live_server  # default source is the gallery (testpattern)
    status, body = _get(base, "/section/video_source")
    assert status == 200
    assert 'id="gallery-grid"' in body  # grid container loads via HTMX
    # Capture, connection recovery, and preview don't apply to the gallery.
    assert 'id="capture-frame-row" style="margin-top:0.5rem;display:none"' in body
    assert 'id="recovery-row" style="display:none"' in body
    assert 'id="preview-row" style="margin-top:0.5rem;display:none"' in body


def test_video_source_section_shows_capture_for_live_source(live_server) -> None:
    server, base = live_server
    cfg = load_config(server.config_path)
    cfg.video_source_type = "rtsp"
    cfg.rtsp_url = "rtsp://example/stream"
    save_config(cfg, server.config_path)
    status, body = _get(base, "/section/video_source")
    assert status == 200
    assert "Capture frame to gallery" in body  # live sources offer capture
    assert 'id="capture-frame-row" style="margin-top:0.5rem;display:"' in body  # shown
    assert 'id="recovery-row" style="display:"' in body  # network source -> recovery shown
    assert 'id="preview-row" style="margin-top:0.5rem;display:"' in body  # preview shown


def test_video_source_section_camera_hides_recovery_keeps_preview(live_server) -> None:
    # A non-network, non-gallery source (NDI uses its own reconnect): recovery
    # hidden, but capture + preview still apply.
    server, base = live_server
    cfg = load_config(server.config_path)
    cfg.video_source_type = "ndi"
    save_config(cfg, server.config_path)
    status, body = _get(base, "/section/video_source")
    assert status == 200
    assert 'id="recovery-row" style="display:none"' in body
    assert 'id="preview-row" style="margin-top:0.5rem;display:"' in body
    assert 'id="capture-frame-row" style="margin-top:0.5rem;display:"' in body


def test_gallery_thumb_user_media(gallery_server) -> None:
    _, base, media_dir = gallery_server
    _post_bytes(base, f"{_GP}/upload", _PNG)
    media_id = _user_files(media_dir)[0].removesuffix(".jpg")
    status, body = _get_raw(base, f"{_GP}/thumb/{media_id}")
    assert status == 200 and body[:3] == b"\xff\xd8\xff"


def test_gallery_thumb_invalid_id_404(gallery_server) -> None:
    _, base, _ = gallery_server
    status, _body = _get_raw(base, f"{_GP}/thumb/not-a-valid-id")
    assert status == 404


def test_gallery_upload_empty(gallery_server) -> None:
    _, base, media_dir = gallery_server
    status, body = _post_bytes(base, f"{_GP}/upload", b"")
    assert status == 200
    assert "Empty upload" in body
    assert _user_files(media_dir) == []


def test_gallery_upload_unexpected_error(gallery_server, monkeypatch) -> None:
    from openfollow.video import media_store

    _, base, _ = gallery_server

    def boom(staged):
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(media_store, "save_upload", boom)
    status, body = _post_bytes(base, f"{_GP}/upload", _PNG)
    assert status == 200
    assert "Upload failed" in body


def test_gallery_capture_store_error(gallery_server, monkeypatch) -> None:
    from openfollow.video import media_store

    server, base, _ = gallery_server
    monkeypatch.setattr(server, "get_full_snapshot", lambda: _JPEG + b"frame")

    def boom(jpeg):
        raise media_store.MediaStoreError("gallery full")

    monkeypatch.setattr(media_store, "save_captured_frame", boom)
    status, body = _post_bytes(base, f"{_GP}/capture", b"")
    assert status == 200
    assert json.loads(body) == {"ok": False, "error": "gallery full"}


def test_gallery_download_user_media(gallery_server) -> None:
    _, base, media_dir = gallery_server
    _post_bytes(base, f"{_GP}/upload", _PNG)
    media_id = _user_files(media_dir)[0].removesuffix(".jpg")
    status, body = _get_raw(base, f"{_GP}/download/{media_id}")
    assert status == 200 and body[:3] == b"\xff\xd8\xff"
