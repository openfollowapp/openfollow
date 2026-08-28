# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Integration tests for the diagnostics routes.

Spins up a real ``ConfigWebServer`` on a free localhost port (with
beacon I/O stubbed) and drives the four diagnostics endpoints over
HTTP. The unit-level collector logic is covered by
``tests/test_web_diagnostics.py``; this file asserts on the wiring
(auth gate, response shapes, headers, redactions reaching the wire,
the private-IP allowlist on the peer probe).
"""

from __future__ import annotations

import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator

import pytest

import openfollow.web.discovery as discovery_module
from openfollow.configuration import load_config, save_config
from openfollow.logging_setup import setup_logging
from openfollow.web.discovery import PeerInfo
from openfollow.web.server import ConfigWebServer
from tests._ports import live_on_free_port

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clear_probe_log_source_cache():
    """The TTL-cached ``journalctl`` reachability probe in
    ``diagnostics.probe_log_source`` survives across tests by
    design (a 60 s TTL is the right behaviour for the 5 s HTMX
    poll). Wipe between tests so a previous test's
    monkeypatched ``shutil.which`` / ``_run`` answers don't leak
    via the cache. (Without this, on a CI runner where
    ``journalctl`` is on PATH, an earlier "reachable=True" cache
    entry can short-circuit a later ``journalctl missing`` test.)"""
    from openfollow.web import diagnostics

    diagnostics._probe_log_source_cache.clear()
    yield
    diagnostics._probe_log_source_cache.clear()


@pytest.fixture(autouse=True)
def _stub_host_subprocesses(monkeypatch):
    """Stub the module's single subprocess boundary so no diagnostics
    route shells out to the host.

    The bundle spawns nine probes on macOS (``git``, ``system_profiler``,
    ``du`` ...) whose caps sum to 50 s, and more on a Pi where
    ``journalctl`` is real - against the 5 s client budget in ``_get``.
    Collector behaviour is covered by ``tests/test_web_diagnostics.py``;
    this file asserts on the wiring, so real host output buys nothing here
    but a latency flake. A test needing a specific answer patches over
    this one."""
    from openfollow.web import diagnostics

    monkeypatch.setattr(
        diagnostics,
        "_run",
        lambda cmd, **_kw: (-1, f"[unavailable: {cmd[0] if cmd else '(empty)'} not found]"),
    )


# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------


@pytest.fixture()
def live_server(tmp_path, monkeypatch) -> Iterator[tuple[ConfigWebServer, str, str]]:
    """Live ``ConfigWebServer`` with beacon I/O + log ring wired."""
    monkeypatch.setattr(
        discovery_module.BeaconSender,
        "start",
        lambda self: None,
    )
    monkeypatch.setattr(
        discovery_module.BeaconSender,
        "stop",
        lambda self: None,
    )
    monkeypatch.setattr(
        discovery_module.BeaconReceiver,
        "start",
        lambda self: None,
    )
    monkeypatch.setattr(
        discovery_module.BeaconReceiver,
        "stop",
        lambda self: None,
    )

    # Snapshot + restore the root logger so ``setup_logging`` doesn't
    # leak into sibling test files (matches the pattern in
    # ``tests/test_logging_setup.py``).
    import logging

    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level

    ring = setup_logging(ring_capacity=64)
    config_path = tmp_path / "config.toml"
    try:
        with live_on_free_port(
            lambda port: ConfigWebServer(
                config_path=str(config_path),
                host="127.0.0.1",
                port=port,
                system_name="TestSystem",
                log_ring=ring,
            )
        ) as (server, base):
            yield server, base, str(config_path)
    finally:
        root.setLevel(original_level)
        for h in list(root.handlers):
            if h not in original_handlers:
                root.removeHandler(h)
        for h in original_handlers:
            if h not in root.handlers:
                root.addHandler(h)


def _get(
    base: str,
    path: str,
    *,
    follow_redirects: bool = True,
) -> tuple[int, str, dict[str, str]]:
    """``follow_redirects=False`` is the auth-gate testing knob: the
    auth hook 302s an unauthenticated GET to ``/login``; without
    this opt-out urllib follows that redirect and the assertion
    sees the login page's 200 instead of the protected route's 302."""
    if follow_redirects:
        try:
            with urllib.request.urlopen(f"{base}{path}", timeout=5) as r:
                return r.status, r.read().decode(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(), {}

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):  # noqa: ARG002
            return None

    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(f"{base}{path}", timeout=5) as r:
            return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), {}


def _get_with_htmx(
    base: str,
    path: str,
) -> tuple[int, str, dict[str, str]]:
    """GET with the ``HX-Request: true`` header that real htmx
    sends. Used to drive the log-tail endpoint's HTML-escape
    branch."""
    req = urllib.request.Request(
        f"{base}{path}",
        headers={"HX-Request": "true"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), {}


def _post(base: str, path: str, data: dict | None = None) -> tuple[int, str]:
    body = urllib.parse.urlencode(data or {}).encode()
    req = urllib.request.Request(
        f"{base}{path}",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


# ---------------------------------------------------------------------------
# /section/diagnostics – inline summary cards
# ---------------------------------------------------------------------------


def test_section_diagnostics_renders_card_grid(live_server) -> None:
    _, base, _ = live_server
    status, body, _ = _get(base, "/section/diagnostics")
    assert status == 200
    # Each card title appears in the polled fragment.
    for title in ("Web server", "Beacon sender", "Beacon receiver", "Logs"):
        assert title in body
    # Flicker fix: the 5s poll returns ONLY the live cards – not the static
    # section shell (head + bundle / probe / log-tail tools). Rebuilding those
    # every tick is what caused the visible flash. The tools are asserted on
    # the full-page render in ``test_index_page_includes_diagnostics_section``.
    assert "Bundle" not in body
    assert "/api/diagnostics/bundle" not in body


def test_section_diagnostics_warns_when_journalctl_missing(
    live_server,
    tmp_path,
    monkeypatch,
) -> None:
    """Default config has ``update_service_name = "openfollow"`` so the
    section *would* prefer journalctl. On a host without journalctl in
    PATH (typical dev / macOS) ``probe_log_source`` reports the
    fallback label and the warning banner renders."""
    _, base, _ = live_server
    from openfollow.web import diagnostics

    # Force the missing-binary fallback regardless of host. ``probe_log_source``
    # consults ``shutil.which`` rather than spawning journalctl every poll.
    monkeypatch.setattr(diagnostics.shutil, "which", lambda _name: None)
    _, body, _ = _get(base, "/section/diagnostics")
    assert "journalctl is unavailable" in body


def test_section_diagnostics_warns_when_service_name_blank(
    live_server,
    tmp_path,
) -> None:
    """When the operator hasn't set ``update_service_name``, the
    section short-circuits journalctl and uses the ring; the
    warning explains why."""
    _, base, cfg_path = live_server
    cfg = load_config(cfg_path)
    cfg.update_service_name = ""
    save_config(cfg, cfg_path)
    _, body, _ = _get(base, "/section/diagnostics")
    assert "No journald service name configured" in body


def test_section_diagnostics_warns_when_no_log_source_at_all(
    live_server,
    tmp_path,
    monkeypatch,
) -> None:
    server, base, _ = live_server
    from openfollow.web import diagnostics

    monkeypatch.setattr(diagnostics.shutil, "which", lambda _name: None)
    # The fixture constructs ``ConfigWebServer`` with a default
    # ring; null it out for this test only.
    monkeypatch.setattr(server, "_log_ring", None)
    _, body, _ = _get(base, "/section/diagnostics")
    assert "No log source is available" in body


def test_section_diagnostics_no_warning_when_journalctl_works(
    live_server,
    tmp_path,
    monkeypatch,
) -> None:
    _, base, cfg_path = live_server
    cfg = load_config(cfg_path)
    cfg.update_service_name = "openfollow"
    save_config(cfg, cfg_path)
    from openfollow.web import diagnostics

    # Wipe the TTL cache so the previous test's "unreachable"
    # answer doesn't leak through.
    diagnostics._probe_log_source_cache.clear()
    monkeypatch.setattr(
        diagnostics.shutil,
        "which",
        lambda name: "/usr/bin/journalctl" if name == "journalctl" else None,
    )
    monkeypatch.setattr(diagnostics, "_run", lambda *a, **kw: (0, "ok"))
    _, body, _ = _get(base, "/section/diagnostics")
    assert "journalctl is unavailable" not in body
    assert "No journald service name configured" not in body


# ---------------------------------------------------------------------------
# /api/diagnostics/bundle – full text bundle download
# ---------------------------------------------------------------------------


def test_api_diagnostics_bundle_returns_text_attachment(
    live_server,
    monkeypatch,
    tmp_path,
) -> None:
    """The bundle response carries plain text + a Content-Disposition
    attachment header so curl / wget grab the file under the right
    name."""
    _, base, _ = live_server
    # Redirect the on-disk writer to a tmp dir so the test doesn't
    # touch ``/var/log`` or the operator's home dir.
    from openfollow.web import diagnostics

    monkeypatch.setattr(
        diagnostics,
        "default_disk_root",
        lambda: tmp_path / "bundles",
    )
    status, body, headers = _get(base, "/api/diagnostics/bundle")
    assert status == 200
    assert headers.get("Content-Type", "").startswith("text/plain")
    disposition = headers.get("Content-Disposition", "")
    assert "attachment" in disposition
    assert "openfollow-diagnostics-TestSystem-" in disposition
    # Release version + architecture make the download self-identifying.
    import openfollow

    assert disposition.endswith(
        f'-{diagnostics._sanitise_name(openfollow.__version__)}-{diagnostics._platform_arch()}.txt"'
    )
    assert f"version: {openfollow.__version__}" in body
    assert f"platform: {diagnostics._platform_label()}" in body
    # Body has every section header from the bundle.
    for header in ("=== A. Service / port ===", "=== E1. Runtime / versions ==="):
        assert header in body
    # On-disk copy was written.
    written = list((tmp_path / "bundles").iterdir())
    assert len(written) == 1


def test_api_diagnostics_bundle_sizes_configured_storage_path(
    live_server,
    monkeypatch,
    tmp_path,
) -> None:
    """An absolute ``detection.storage_path`` is threaded into the
    storage-breakdown section so the model store's footprint shows up
    alongside the SD card."""
    _, base, cfg_path = live_server
    from openfollow.web import diagnostics

    monkeypatch.setattr(
        diagnostics,
        "default_disk_root",
        lambda: tmp_path / "bundles",
    )
    # Stub du so the bundle's storage breakdown doesn't size the host's
    # caches – this test only checks that the configured path is threaded in.
    monkeypatch.setattr(diagnostics, "_du_kib", lambda *a, **kw: 4096)
    store = tmp_path / "models"
    store.mkdir()
    cfg = load_config(cfg_path)
    cfg.detection.storage_path = str(store)
    save_config(cfg, cfg_path)
    _, body, _ = _get(base, "/api/diagnostics/bundle")
    assert f"configured storage ({store})" in body


def test_api_diagnostics_bundle_sizes_autodetected_nvme_storage(
    live_server,
    monkeypatch,
    tmp_path,
) -> None:
    """A blank ``storage_path`` on an NVMe unit threads the auto-detected SSD
    store into the breakdown, so models aren't mislabelled as SD-card."""
    _, base, cfg_path = live_server
    from openfollow.video import detection as detection_mod
    from openfollow.web import diagnostics

    monkeypatch.setattr(diagnostics, "default_disk_root", lambda: tmp_path / "bundles")
    monkeypatch.setattr(diagnostics, "_du_kib", lambda *a, **kw: 4096)
    nvme_root = tmp_path / "nvme"
    storage = nvme_root / "openfollow" / "yolo"
    storage.mkdir(parents=True)
    monkeypatch.setattr(detection_mod, "_NVME_MOUNTPOINT", str(nvme_root))
    monkeypatch.setattr(detection_mod, "_NVME_DETECTION_STORAGE", str(storage))
    monkeypatch.setattr(os.path, "ismount", lambda p: p == str(nvme_root))
    cfg = load_config(cfg_path)
    cfg.detection.storage_path = ""  # blank -> runtime auto-detects the NVMe
    save_config(cfg, cfg_path)
    _, body, _ = _get(base, "/api/diagnostics/bundle")
    assert f"configured storage ({storage})" in body


def test_api_diagnostics_bundle_skips_relative_storage_path(
    live_server,
    monkeypatch,
    tmp_path,
) -> None:
    _, base, cfg_path = live_server
    from openfollow.web import diagnostics

    monkeypatch.setattr(
        diagnostics,
        "default_disk_root",
        lambda: tmp_path / "bundles",
    )
    monkeypatch.setattr(diagnostics, "_du_kib", lambda *a, **kw: 4096)
    cfg = load_config(cfg_path)
    cfg.detection.storage_path = "models/yolo"  # relative, not absolute
    save_config(cfg, cfg_path)
    status, body, _ = _get(base, "/api/diagnostics/bundle")
    assert status == 200
    assert "configured storage (models/yolo)" not in body


def test_api_diagnostics_bundle_sanitises_filename_in_disposition(
    live_server,
    monkeypatch,
    tmp_path,
) -> None:
    server, base, _ = live_server
    from openfollow.web import diagnostics

    monkeypatch.setattr(
        diagnostics,
        "default_disk_root",
        lambda: tmp_path / "bundles",
    )
    server._system_name = 'evil"\nname/with spaces'
    _, _, headers = _get(base, "/api/diagnostics/bundle")
    disposition = headers.get("Content-Disposition", "")
    # No raw double-quote or newline / slash escaped through.
    assert '"\n' not in disposition
    assert "/" not in disposition.split('filename="', 1)[1]
    # The fallback sanitised form is what landed.
    assert "evil_" in disposition or "name_with_spaces" in disposition


def test_api_diagnostics_bundle_marks_empty_web_pin(
    live_server,
    monkeypatch,
    tmp_path,
) -> None:
    """A blank PIN renders as ``"(empty)"`` in the bundle so the
    bundle reader can tell the operator never set one. (The
    ``"***"`` redaction path for a *set* PIN is unit-tested in
    ``tests/test_web_diagnostics.py`` – we can't reproduce it in an
    integration test without authenticating to clear the auth
    gate, which would defeat the test setup.)"""
    _, base, _ = live_server
    from openfollow.web import diagnostics

    monkeypatch.setattr(
        diagnostics,
        "default_disk_root",
        lambda: tmp_path / "bundles",
    )
    _, body, _ = _get(base, "/api/diagnostics/bundle")
    assert 'web_pin = "(empty)"' in body


def test_api_diagnostics_bundle_writer_failure_does_not_break_download(
    live_server,
    monkeypatch,
    tmp_path,
) -> None:
    """Operator on a read-only fs still gets the bundle through the
    browser even though the on-disk copy fails."""
    _, base, _ = live_server
    from openfollow.web import diagnostics

    monkeypatch.setattr(
        diagnostics,
        "write_bundle_to_disk",
        lambda *a, **kw: None,
    )
    status, body, _ = _get(base, "/api/diagnostics/bundle")
    assert status == 200
    assert "openfollow diagnostics bundle" in body


# ---------------------------------------------------------------------------
# /api/diagnostics/log-tail
# ---------------------------------------------------------------------------


def test_api_diagnostics_log_tail_returns_ring_contents(live_server) -> None:
    _, base, _ = live_server
    # journalctl is stubbed unavailable, so the route reads the ring; the
    # journalctl-success path is covered in ``tests/test_web_diagnostics``.
    # Write a log line and confirm the ring picks it up + the route
    # serves it. ``server.log_ring`` is the same handle ``setup_logging``
    # returned and the route reads from.
    import logging

    logging.getLogger("openfollow.test.tail").info("synthetic log entry abc")
    status, body, _ = _get(base, "/api/diagnostics/log-tail?n=200")
    assert status == 200
    assert "synthetic log entry abc" in body
    assert body.startswith("[source: in-memory ring buffer")


def test_api_diagnostics_log_tail_caps_n_at_2000(live_server) -> None:
    """Operator-tunable ``n`` honours the registry cap; an over-large
    request is clamped, not rejected – the bundle is more useful with
    a clamp than with a 400."""
    _, base, _ = live_server
    status, body, _ = _get(base, "/api/diagnostics/log-tail?n=99999")
    assert status == 200
    assert body  # any text – clamp didn't 500


def test_api_diagnostics_log_tail_handles_invalid_n(live_server) -> None:
    """Garbage ``n`` falls back to the 100-line default rather than
    500-ing."""
    _, base, _ = live_server
    status, _, _ = _get(base, "/api/diagnostics/log-tail?n=abc")
    assert status == 200


def test_api_diagnostics_log_tail_redacts_signatures(live_server) -> None:
    _, base, _ = live_server
    import logging

    logging.getLogger("openfollow.test.redact").info(
        "outgoing X-Auth-Signature: deadbeefcafebabe handled",
    )
    status, body, _ = _get(base, "/api/diagnostics/log-tail?n=200")
    assert status == 200
    assert "deadbeefcafebabe" not in body
    assert "X-Auth-Signature: ***" in body


def test_api_diagnostics_log_tail_escapes_html_for_htmx_consumer(live_server) -> None:
    """The diagnostics partial swaps the log-tail response into a
    ``<pre>`` via ``hx-swap="innerHTML"``. Content must be HTML-escaped
    to prevent XSS from user-influenced log lines."""
    _, base, _ = live_server
    import logging

    logging.getLogger("openfollow.test.xss").info(
        "<img src=x onerror=alert(1)>",
    )
    status, body, headers = _get_with_htmx(base, "/api/diagnostics/log-tail?n=200")
    assert status == 200
    assert headers.get("Content-Type", "").startswith("text/html")
    # Raw payload escaped; entity form present.
    assert "<img src=x" not in body
    assert "&lt;img src=x" in body


def test_api_diagnostics_log_tail_returns_raw_text_for_curl(live_server) -> None:
    _, base, _ = live_server
    import logging

    logging.getLogger("openfollow.test.curl").info("plain <ok> message")
    status, body, headers = _get(base, "/api/diagnostics/log-tail?n=200")
    assert status == 200
    assert headers.get("Content-Type", "").startswith("text/plain")
    assert "<ok>" in body
    assert "&lt;ok&gt;" not in body


# ---------------------------------------------------------------------------
# Hermetic contract – no diagnostics route shells out under test
# ---------------------------------------------------------------------------


def test_diagnostics_routes_spawn_no_subprocess(
    live_server,
    monkeypatch,
    tmp_path,
) -> None:
    """Every host probe in the collector goes through ``diagnostics._run``,
    which the autouse stub replaces. This pins that: a collector added later
    that reaches for ``subprocess`` directly re-couples these tests to host
    latency, which is what made the bundle test flake against its 5 s client
    budget."""
    _, base, _ = live_server
    from openfollow.web import diagnostics

    monkeypatch.setattr(
        diagnostics,
        "default_disk_root",
        lambda: tmp_path / "bundles",
    )

    class _Boom:
        @staticmethod
        def run(*args, **kwargs):
            raise AssertionError(f"diagnostics route spawned a subprocess: {args!r}")

    monkeypatch.setattr(diagnostics, "subprocess", _Boom)
    for path in ("/api/diagnostics/bundle", "/api/diagnostics/log-tail?n=200"):
        status, body, _ = _get(base, path)
        assert status == 200, f"{path} returned {status}"
        assert "spawned a subprocess" not in body


# ---------------------------------------------------------------------------
# /api/diagnostics/test-peers
# ---------------------------------------------------------------------------


def test_api_test_peers_renders_empty_state_with_no_peers(live_server) -> None:
    _, base, _ = live_server
    status, body = _post(base, "/api/diagnostics/test-peers", {})
    assert status == 200
    assert "No peers known yet" in body


def test_api_test_peers_refuses_non_private_ip(live_server) -> None:
    """SSRF gate – same allowlist the peer-broadcast helpers use."""
    server, base, _ = live_server
    # Inject a fake peer with a public IP. We stash one PeerInfo
    # directly into the receiver's peer dict to mimic what
    # discovery would have done.
    server.beacon_receiver._peers["8.8.8.8:80"] = PeerInfo(
        name="public",
        ip="8.8.8.8",
        web_port=80,
        version="0.1.0",
        last_seen=time.time(),
    )
    status, body = _post(base, "/api/diagnostics/test-peers", {})
    assert status == 200
    assert "non-private IP refused" in body


def test_api_test_peers_escapes_peer_name_in_html(live_server) -> None:
    server, base, _ = live_server
    server.beacon_receiver._peers["10.1.1.1:80"] = PeerInfo(
        name="<img src=x onerror=alert(1)>",
        ip="10.1.1.1",
        web_port=80,
        version="0.1.0",
        last_seen=time.time(),
    )
    status, body = _post(base, "/api/diagnostics/test-peers", {})
    assert status == 200
    # Raw script payload never reaches the response – escaped form
    # does. The literal ``<img src=x`` substring must not appear.
    assert "<img src=x" not in body
    assert "&lt;img src=x" in body


def test_api_test_peers_renders_status_code_for_5xx(
    live_server,
    monkeypatch,
) -> None:
    """A peer that responds with ``HTTP 503`` is reachable, just erroring.
    The UI must surface the status code rather than misreporting "unreachable"."""
    server, base, _ = live_server
    server.beacon_receiver._peers["10.1.1.50:80"] = PeerInfo(
        name="busy-peer",
        ip="10.1.1.50",
        web_port=80,
        version="0.1.0",
        last_seen=time.time(),
    )
    from openfollow.web import routes

    monkeypatch.setattr(
        routes,
        "_probe_peer",
        lambda ip, port, name, **_kw: {  # noqa: ARG005
            "name": name,
            "ip": ip,
            "port": port,
            "ok": False,
            "status": 503,
            "ms": 12,
            "error": "",
        },
    )
    status, body = _post(base, "/api/diagnostics/test-peers", {})
    assert status == 200
    assert "HTTP 503" in body
    # The misleading "unreachable" fallback isn't used here.
    assert ">unreachable<" not in body


def test_api_test_peers_renders_unreachable_when_neither_error_nor_status(
    live_server,
    monkeypatch,
) -> None:
    """The "unreachable" fallback applies when the probe couldn't talk to the peer at all."""
    server, base, _ = live_server
    server.beacon_receiver._peers["10.1.1.51:80"] = PeerInfo(
        name="silent-peer",
        ip="10.1.1.51",
        web_port=80,
        version="0.1.0",
        last_seen=time.time(),
    )
    from openfollow.web import routes

    monkeypatch.setattr(
        routes,
        "_probe_peer",
        lambda ip, port, name, **_kw: {  # noqa: ARG005
            "name": name,
            "ip": ip,
            "port": port,
            "ok": False,
            "status": 0,
            "ms": 0,
            "error": "",
        },
    )
    status, body = _post(base, "/api/diagnostics/test-peers", {})
    assert status == 200
    assert ">unreachable<" in body


def test_api_test_peers_probes_private_peer(live_server) -> None:
    """A peer at a private IP is probed (we point it at the live
    server's own port, so the HEAD request actually completes)."""
    server, base, _ = live_server
    addr = base.split("://")[1]
    host, port_str = addr.split(":")
    server.beacon_receiver._peers[f"{host}:{port_str}"] = PeerInfo(
        name="self-probe",
        ip="127.0.0.1",
        web_port=int(port_str),
        version="0.1.0",
        last_seen=time.time(),
    )
    status, body = _post(base, "/api/diagnostics/test-peers", {})
    assert status == 200
    assert "self-probe" in body
    # 127.0.0.1 is private → the row got probed (not the SSRF refusal).
    assert "non-private IP refused" not in body


# ---------------------------------------------------------------------------
# Initial render of ``/`` includes the diagnostics partial
# ---------------------------------------------------------------------------


def test_index_page_includes_diagnostics_section(live_server) -> None:
    _, base, _ = live_server
    status, body, _ = _get(base, "/")
    assert status == 200
    assert "Diagnostics" in body
    # Flicker fix: only the live cards region polls, so the section shell (head
    # + tools) renders once and never re-swaps. The poll target is the inner
    # ``#diagnostics-live``, not the whole section.
    assert 'id="diagnostics-live"' in body
    assert 'hx-get="/section/diagnostics"' in body
    assert 'id="diagnostics-section-wrap"' not in body
    # The static bundle/probe/log-tail tools live in the shell, rendered once.
    assert "/api/diagnostics/bundle" in body
    assert "/api/diagnostics/test-peers" in body
    assert "/api/diagnostics/log-tail" in body
    # Both the diagnostics and overview polls are gated to skip while their
    # section is collapsed (same closest()-based trigger filter on each).
    assert body.count("closest('.section').classList.contains('is-collapsed')") >= 2


# ---------------------------------------------------------------------------
# PIN auth – every diagnostics route is gated.
# ---------------------------------------------------------------------------


def test_diagnostics_routes_require_pin_when_set(
    live_server,
    tmp_path,
) -> None:
    server, base, cfg_path = live_server
    cfg = load_config(cfg_path)
    cfg.web_pin = "1234"
    save_config(cfg, cfg_path)
    try:
        for path in (
            "/section/diagnostics",
            "/api/diagnostics/bundle",
            "/api/diagnostics/log-tail",
        ):
            status, _, _ = _get(base, path, follow_redirects=False)
            # The auth hook redirects to ``/login`` (302/303) for HTML
            # GETs without a session, or 401s an API call.
            assert status in (302, 303, 401), f"{path} not pin-gated (got {status})"
        # ``/api/diagnostics/test-peers`` is the POST route that returns
        # operator-visible data and performs outbound HTTP requests.
        post_status, _ = _post(base, "/api/diagnostics/test-peers", {})
        assert post_status in (302, 303, 401), f"/api/diagnostics/test-peers not pin-gated (got {post_status})"
    finally:
        # Reset PIN so the fixture's teardown isn't fighting auth.
        cfg.web_pin = ""
        save_config(cfg, cfg_path)
