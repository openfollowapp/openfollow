# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the inline help system: ``render_help_markdown`` renderer with
raw-HTML escaping, ``data-help`` section markup with a backing-doc packaging
guard, and the ``/help/<id>.html`` route (slug allow-list, 404s, content type).
"""

from __future__ import annotations

import re
import socket
import time
import urllib.error
import urllib.request

import pytest
from bottle import template

import openfollow.web.discovery as discovery_module
from openfollow.configuration import AppConfig
from openfollow.web import server as _server_module  # noqa: F401 – registers tpl path
from openfollow.web._md import render_help_markdown
from openfollow.web.routes import _WEB_HELP_DIR
from openfollow.web.server import ConfigWebServer

_TEMPLATES_DIR = _WEB_HELP_DIR.parent / "templates"


# ---------------------------------------------------------------------------
# Renderer (unit)
# ---------------------------------------------------------------------------


class TestRenderHelpMarkdown:
    pytestmark = pytest.mark.unit

    def test_renders_heading(self) -> None:
        assert "<h1>" in render_help_markdown("# Camera")

    def test_renders_list(self) -> None:
        html = render_help_markdown("- one\n- two")
        assert "<ul>" in html and "<li>" in html

    def test_renders_bold(self) -> None:
        assert "<strong>" in render_help_markdown("**required**")

    def test_renders_table(self) -> None:
        md = "| A | B |\n| - | - |\n| 1 | 2 |"
        assert "<table>" in render_help_markdown(md)

    def test_escapes_raw_html(self) -> None:
        # Fragment is injected via innerHTML, so raw HTML must be escaped, not
        # passed through as live markup.
        html = render_help_markdown("<script>alert(1)</script>")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_handles_empty_input(self) -> None:
        assert render_help_markdown("").strip() == ""


# ---------------------------------------------------------------------------
# Section markup + packaging (unit)
# ---------------------------------------------------------------------------


class TestSectionHelpAttributes:
    pytestmark = pytest.mark.unit

    def test_camera_section_opts_in(self) -> None:
        body = template("partials/camera", config=AppConfig(), saved=False)
        assert 'data-help="camera"' in body

    def test_grid_section_opts_in(self) -> None:
        body = template("partials/grid", config=AppConfig(), saved=False)
        assert 'data-help="grid"' in body

    def test_excluded_section_has_no_marker(self) -> None:
        # send_config.tpl is not rendered (include commented out in index.tpl),
        # so it carries no data-help marker and needs no doc.
        raw = (_TEMPLATES_DIR / "partials" / "send_config.tpl").read_text()
        assert "data-help=" not in raw


class TestHelpDocPackaging:
    """Every ``data-help`` id referenced in a template has a backing doc."""

    pytestmark = pytest.mark.unit

    def test_every_referenced_help_id_has_a_doc(self) -> None:
        referenced: set[str] = set()
        for tpl in _TEMPLATES_DIR.rglob("*.tpl"):
            referenced.update(re.findall(r'data-help="([a-z0-9_-]+)"', tpl.read_text()))
        assert referenced, "expected at least one data-help reference (camera, grid)"
        missing = {doc_id for doc_id in referenced if not (_WEB_HELP_DIR / f"{doc_id}.md").is_file()}
        assert not missing, f"data-help ids without a help/<id>.md doc: {sorted(missing)}"


class TestHelpDocNoHtmlEntities:
    """Docs rendered with ``escape=True`` must not use HTML character entities.

    ``render_help_markdown`` escapes ``&`` so a named/numeric entity like
    ``&ndash;`` reaches the browser as the literal text ``&ndash;`` instead of
    an en-dash. The fix is to write the Unicode character (``–``) directly.
    """

    pytestmark = pytest.mark.unit

    # ``&name;`` / ``&#123;`` / ``&#x1F;`` – the trailing ``;`` keeps a bare
    # prose ampersand from matching.
    _ENTITY_RE = re.compile(r"&(#[0-9]+|#x[0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]*);")

    def _mistune_rendered_docs(self) -> list:
        repo_root = _WEB_HELP_DIR.parents[2]
        return sorted(_WEB_HELP_DIR.glob("*.md")) + [repo_root / "THIRD_PARTY_NOTICES.md"]

    def test_no_entities_in_rendered_docs(self) -> None:
        offenders: dict[str, list[str]] = {}
        for doc in self._mistune_rendered_docs():
            if not doc.is_file():
                continue
            hits = self._ENTITY_RE.findall(doc.read_text(encoding="utf-8"))
            if hits:
                offenders[doc.name] = hits
        assert not offenders, (
            "HTML entities in escape-rendered docs render as literal text; "
            f"use the Unicode character instead: {offenders}"
        )


class TestHelpDocImageAssets:
    """Every image a help doc references is a bundled local asset, served by
    the ``/assets/<path>`` route. Guards the offline contract – no CDN image,
    no broken icon on a show LAN – and catches a renamed/missing SVG.
    """

    pytestmark = pytest.mark.unit

    _IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")

    def test_help_images_are_bundled_local_assets(self) -> None:
        from openfollow.web.routes import _WEB_STATIC_DIR

        offenders: dict[str, list[str]] = {}
        for doc in sorted(_WEB_HELP_DIR.glob("*.md")):
            for src in self._IMG_RE.findall(doc.read_text(encoding="utf-8")):
                if not src.startswith("/assets/"):
                    offenders.setdefault(doc.name, []).append(f"non-local: {src}")
                elif not (_WEB_STATIC_DIR / src[len("/assets/") :]).is_file():
                    offenders.setdefault(doc.name, []).append(f"missing: {src}")
        assert not offenders, f"help image refs must be bundled under web/static: {offenders}"


# ---------------------------------------------------------------------------
# Route (integration – live server)
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


def _get(base: str, path: str) -> tuple[int, str, str]:
    """GET ``path``; return ``(status, body, content_type)``."""
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=5) as r:
            return r.status, r.read().decode(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), e.headers.get("Content-Type", "")


@pytest.fixture()
def live_server(tmp_path, monkeypatch):
    """ConfigWebServer on a free localhost port; beacon I/O stubbed out."""
    monkeypatch.setattr(discovery_module.BeaconSender, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconSender, "stop", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "stop", lambda self: None)

    port = _find_free_tcp_port()
    server = ConfigWebServer(
        config_path=str(tmp_path / "config.toml"),
        host="127.0.0.1",
        port=port,
        system_name="TestSystem",
    )
    server.start()
    assert _wait_for_port(port), f"Web server did not start within 5 s on port {port}"
    yield f"http://127.0.0.1:{port}"
    server.stop()


class TestHelpRoute:
    pytestmark = pytest.mark.integration

    def test_renders_bundled_doc(self, live_server) -> None:
        status, body, ctype = _get(live_server, "/help/camera.html")
        assert status == 200
        assert "text/html" in ctype
        assert "<h1>Camera</h1>" in body

    def test_underscore_id_doc(self, live_server) -> None:
        # Slug allow-list permits underscores (e.g. video_source).
        status, body, ctype = _get(live_server, "/help/video_source.html")
        assert status == 200
        assert "text/html" in ctype

    def test_missing_doc_404(self, live_server) -> None:
        status, _, _ = _get(live_server, "/help/doesnotexist.html")
        assert status == 404

    def test_malformed_id_404(self, live_server) -> None:
        # Uppercase fails the slug allow-list before any filesystem access.
        status, _, _ = _get(live_server, "/help/Bad.html")
        assert status == 404

    def test_dotted_id_404(self, live_server) -> None:
        # Dotted id (path-traversal payload) is rejected by the slug regex.
        status, _, _ = _get(live_server, "/help/a.b.html")
        assert status == 404
