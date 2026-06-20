# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the marker catalog + selection HTTP endpoints.

Spins up a real :class:`ConfigWebServer` and feeds requests through
``urllib`` – same pattern as ``test_web_osc_bindings.py``. The catalog
is wired through a provider callback so each test can configure its
own catalog without sharing state across tests.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

import openfollow.web.discovery as discovery_module
from openfollow.configuration import load_config
from openfollow.marker_catalog import MarkerCatalog
from openfollow.marker_catalog.sync import PeerSelection
from openfollow.web.server import ConfigWebServer

pytestmark = pytest.mark.integration


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


class _FakeSync:
    """Stand-in for MarkerCatalogSync – records delta requests and serves peers."""

    def __init__(self) -> None:
        self.delta_requests: list[list[int]] = []
        self.peers: list[PeerSelection] = []

    def request_delta(self, ids: list[int]) -> None:
        self.delta_requests.append(list(ids))

    def get_peer_selections(self) -> list[PeerSelection]:
        return list(self.peers)


@pytest.fixture
def live_server(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery_module.BeaconSender, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconSender, "stop", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "stop", lambda self: None)

    port = _find_free_tcp_port()
    config_path = tmp_path / "config.toml"
    catalog = MarkerCatalog()
    sync = _FakeSync()
    server = ConfigWebServer(
        config_path=str(config_path),
        host="127.0.0.1",
        port=port,
        system_name="OpenFollow test",
        marker_catalog_provider=lambda: catalog,
        marker_catalog_sync_provider=lambda: sync,
    )
    server.start()
    assert _wait_for_port(port)
    base = f"http://127.0.0.1:{port}"
    yield server, base, str(config_path), catalog, sync
    server.stop()


def _request(base: str, path: str, method: str = "GET", body: dict | None = None):
    url = base + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


class TestCatalogEndpoints:
    def test_get_empty_catalog(self, live_server) -> None:
        _, base, _, _, _ = live_server
        status, body = _request(base, "/api/markers/catalog")
        assert status == 200
        data = json.loads(body)
        assert data["entries"] == []
        assert data["this_station"]["station_name"]
        assert data["peer_selections"] == []
        assert data["next_free_id"] == 1

    def test_put_creates_entry_and_requests_delta(self, live_server) -> None:
        _, base, _, catalog, sync = live_server
        status, body = _request(
            base,
            "/api/markers/catalog/1",
            method="PUT",
            body={"name": "Spot 1", "color": "#ff0000"},
        )
        assert status == 200
        assert catalog.get(1) is not None
        assert catalog.get(1).name == "Spot 1"
        assert sync.delta_requests == [[1]]

    def test_put_id_zero_rejected(self, live_server) -> None:
        _, base, _, _, _ = live_server
        status, _ = _request(
            base,
            "/api/markers/catalog/0",
            method="PUT",
            body={"name": "X", "color": "#ff0000"},
        )
        assert status == 400

    def test_delete_tombstones(self, live_server) -> None:
        _, base, _, catalog, _ = live_server
        catalog.upsert(1, "Spot", "#ff0000")
        status, _ = _request(base, "/api/markers/catalog/1", method="DELETE")
        assert status == 200
        assert catalog.get(1) is None
        assert catalog.get_any(1) is not None
        assert catalog.get_any(1).tombstone is True

    def test_delete_unknown_id_returns_404(self, live_server) -> None:
        _, base, _, _, _ = live_server
        status, _ = _request(base, "/api/markers/catalog/99", method="DELETE")
        assert status == 404

    def test_delete_prunes_id_from_selection(self, live_server) -> None:
        _, base, cfg_path, catalog, _ = live_server
        catalog.upsert(1, "Keep", "#ff0000")
        catalog.upsert(2, "Drop", "#00ff00")
        status, _ = _request(
            base,
            "/api/markers/selection",
            method="POST",
            body={"controlled_ids": [1, 2], "viewer_ids": [2]},
        )
        assert status == 200
        status, _ = _request(base, "/api/markers/catalog/2", method="DELETE")
        assert status == 200
        cfg = load_config(cfg_path)
        # Deleted id pruned from both selection lists; the survivor stays.
        assert cfg.controlled_marker_ids == [1]
        assert cfg.viewer_marker_ids == []
        assert catalog.get(2) is None
        assert catalog.get(1) is not None

    def test_delete_succeeds_when_selection_persist_fails(
        self,
        live_server,
        monkeypatch,
    ) -> None:
        """The selection prune is best-effort: if ``save_config`` fails
        (read-only fs / disk full), the delete still returns 200 – the
        catalog tombstone is the authoritative delete and the startup
        prune reconciles the selection on the next restart."""
        _, base, _, catalog, _ = live_server
        catalog.upsert(1, "Spot", "#ff0000")
        # Seed the selection while writes still work, then break them.
        status, _ = _request(
            base,
            "/api/markers/selection",
            method="POST",
            body={"controlled_ids": [1], "viewer_ids": []},
        )
        assert status == 200

        def boom(*_a, **_k):
            raise OSError("read-only fs")

        monkeypatch.setattr("openfollow.web.routes.save_config", boom)
        status, _ = _request(base, "/api/markers/catalog/1", method="DELETE")
        assert status == 200
        assert catalog.get(1) is None
        assert catalog.get_any(1).tombstone is True

    def test_put_rolls_back_in_memory_on_persist_failure(
        self,
        live_server,
        monkeypatch,
    ) -> None:
        """If ``save_catalog`` raises (disk full, fs read-only, permission
        denied), the handler returns 500. Without rollback the
        in-memory ``upsert`` already mutated state, so the UI / poll
        responses would show the change despite the operator seeing
        the error – and the value would never make it to peers via
        ``request_delta`` either. Roll back the in-memory entry so the
        catalog stays consistent with the on-disk snapshot the
        operator's last successful save left behind."""
        from openfollow.web import routes as routes_module

        _, base, _, catalog, sync = live_server
        catalog.upsert(1, "Original", "#ff0000")

        def boom(*_a, **_kw):
            raise OSError("disk full")

        monkeypatch.setattr(routes_module, "save_catalog", boom)
        status, _ = _request(
            base,
            "/api/markers/catalog/1",
            method="PUT",
            body={"name": "Changed", "color": "#00ff00"},
        )
        assert status == 500
        # In-memory entry restored to its pre-upsert state.
        entry = catalog.get(1)
        assert entry is not None
        assert entry.name == "Original"
        assert entry.color == "#ff0000"
        # No delta beacon sent for a write that never persisted.
        assert sync.delta_requests == []

    def test_put_rolls_back_create_on_persist_failure(
        self,
        live_server,
        monkeypatch,
    ) -> None:
        """Same rollback as above, fresh-create variant: the entry
        didn't exist before, so rollback means removing it entirely
        (not restoring a non-existent previous version)."""
        from openfollow.web import routes as routes_module

        _, base, _, catalog, _ = live_server

        def boom(*_a, **_kw):
            raise OSError("disk full")

        monkeypatch.setattr(routes_module, "save_catalog", boom)
        status, _ = _request(
            base,
            "/api/markers/catalog/7",
            method="PUT",
            body={"name": "New", "color": "#0000ff"},
        )
        assert status == 500
        # The would-be-new entry is gone – neither live nor
        # tombstoned. ``get_any`` covers both.
        assert catalog.get_any(7) is None

    def test_put_skips_rollback_when_peer_merges_during_save(
        self,
        live_server,
        monkeypatch,
    ) -> None:
        import time

        from openfollow.marker_catalog import MarkerEntry
        from openfollow.web import routes as routes_module

        _, base, _, catalog, _ = live_server
        catalog.upsert(1, "Original", "#ff0000")

        def boom_with_peer_merge(catalog_arg, _path):
            # Mid-save peer race: a newer remote entry merges into the
            # catalog before save_catalog raises. ``catalog_arg`` is
            # the same MarkerCatalog instance the route handler passed.
            peer = MarkerEntry(
                id=1,
                name="From peer",
                color="#0000ff",
                updated_at=time.time() + 10.0,
                tombstone=False,
            )
            catalog_arg.merge_entry(peer)
            raise OSError("disk full")

        monkeypatch.setattr(routes_module, "save_catalog", boom_with_peer_merge)
        status, _ = _request(
            base,
            "/api/markers/catalog/1",
            method="PUT",
            body={"name": "Local change", "color": "#00ff00"},
        )
        assert status == 500
        # Rollback was SKIPPED – peer's newer state still in place.
        entry = catalog.get(1)
        assert entry is not None
        assert entry.name == "From peer"
        assert entry.color == "#0000ff"

    def test_delete_rolls_back_in_memory_on_persist_failure(
        self,
        live_server,
        monkeypatch,
    ) -> None:
        """Same rollback shape for DELETE: if ``save_catalog`` fails
        after ``catalog.delete()`` has already installed the tombstone
        in memory, restore the live entry so the UI / poll responses
        don't show the marker disappeared. Without the rollback the
        operator sees the marker gone while the disk snapshot still
        has it."""
        from openfollow.web import routes as routes_module

        _, base, _, catalog, sync = live_server
        catalog.upsert(1, "Spot", "#ff0000")

        def boom(*_a, **_kw):
            raise OSError("disk full")

        monkeypatch.setattr(routes_module, "save_catalog", boom)
        status, _ = _request(base, "/api/markers/catalog/1", method="DELETE")
        assert status == 500
        # Live entry restored; no tombstone left over.
        entry = catalog.get(1)
        assert entry is not None
        assert entry.name == "Spot"
        assert entry.tombstone is False
        # No delta beacon for a delete that never persisted.
        assert sync.delta_requests == []

    def test_delete_skips_rollback_when_peer_merges_during_save(
        self,
        live_server,
        monkeypatch,
    ) -> None:
        import time

        from openfollow.marker_catalog import MarkerEntry
        from openfollow.web import routes as routes_module

        _, base, _, catalog, _ = live_server
        catalog.upsert(1, "Spot", "#ff0000")

        def boom_with_peer_merge(catalog_arg, _path):
            # Peer broadcasts a newer live entry while our save_catalog
            # is in flight. ``merge_entry`` replaces our local tombstone
            # because the remote ``updated_at`` is strictly greater.
            peer = MarkerEntry(
                id=1,
                name="Revived by peer",
                color="#0000ff",
                updated_at=time.time() + 10.0,
                tombstone=False,
            )
            catalog_arg.merge_entry(peer)
            raise OSError("disk full")

        monkeypatch.setattr(routes_module, "save_catalog", boom_with_peer_merge)
        status, _ = _request(base, "/api/markers/catalog/1", method="DELETE")
        assert status == 500
        # Rollback skipped: the peer's live entry survives, not our
        # would-be-tombstoned-then-restored "Spot".
        entry = catalog.get(1)
        assert entry is not None
        assert entry.name == "Revived by peer"
        assert entry.tombstone is False


class TestSelectionEndpoint:
    def test_persists_selection(self, live_server) -> None:
        _, base, cfg_path, catalog, _ = live_server
        catalog.upsert(1, "A", "#ff0000")
        catalog.upsert(2, "B", "#00ff00")
        status, _ = _request(
            base,
            "/api/markers/selection",
            method="POST",
            body={"controlled_ids": [1, 2], "viewer_ids": [1]},
        )
        assert status == 200
        cfg = load_config(cfg_path)
        assert cfg.controlled_marker_ids == [1, 2]
        assert cfg.viewer_marker_ids == [1]

    def test_unknown_id_rejected(self, live_server) -> None:
        _, base, _, catalog, _ = live_server
        catalog.upsert(1, "A", "#ff0000")
        status, _ = _request(
            base,
            "/api/markers/selection",
            method="POST",
            body={"controlled_ids": [99], "viewer_ids": []},
        )
        assert status == 400

    def test_zero_id_rejected(self, live_server) -> None:
        _, base, _, _, _ = live_server
        status, _ = _request(
            base,
            "/api/markers/selection",
            method="POST",
            body={"controlled_ids": [0], "viewer_ids": []},
        )
        assert status == 400

    def test_duplicates_collapsed_before_persist(self, live_server) -> None:
        _, base, cfg_path, catalog, _ = live_server
        catalog.upsert(1, "A", "#ff0000")
        catalog.upsert(2, "B", "#00ff00")
        status, _ = _request(
            base,
            "/api/markers/selection",
            method="POST",
            body={"controlled_ids": [1, 1, 2, 1], "viewer_ids": [2, 2]},
        )
        assert status == 200
        cfg = load_config(cfg_path)
        assert cfg.controlled_marker_ids == [1, 2]
        assert cfg.viewer_marker_ids == [2]


class TestCatalogPolling:
    def test_peer_selections_surface_in_payload(self, live_server) -> None:
        _, base, _, catalog, sync = live_server
        catalog.upsert(1, "A", "#ff0000")
        sync.peers.append(
            PeerSelection(
                station_id="peer-x",
                station_name="OpenFollow bright-fox",
                controlled_ids=[1],
                viewer_ids=[1],
                last_seen=time.time(),
            )
        )
        status, body = _request(base, "/api/markers/catalog")
        assert status == 200
        data = json.loads(body)
        peers = data["peer_selections"]
        assert len(peers) == 1
        assert peers[0]["station_id"] == "peer-x"
        assert peers[0]["controlled_ids"] == [1]


class TestMarkersPageRetired:
    def test_standalone_route_returns_404(self, live_server) -> None:
        """The standalone ``/markers`` page was retired in favour of
        the inline catalog renderer inside the Markers & Zones tab on
        ``/``. Pinned so a regression can't quietly resurrect it."""
        _, base, _, _, _ = live_server
        status, _ = _request(base, "/markers")
        assert status == 404


@pytest.fixture
def live_server_no_providers(tmp_path, monkeypatch):
    """Server with marker_catalog/sync providers absent – exercises None branches."""
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
        system_name="OpenFollow test",
    )
    server.start()
    assert _wait_for_port(port)
    base = f"http://127.0.0.1:{port}"
    yield server, base, str(config_path)
    server.stop()


class TestCatalogUnavailable:
    """Cover the catalog=None / sync=None branches in routes.py."""

    def test_get_returns_empty_when_catalog_absent(self, live_server_no_providers) -> None:
        _, base, _ = live_server_no_providers
        status, body = _request(base, "/api/markers/catalog")
        assert status == 200
        data = json.loads(body)
        assert data["entries"] == []
        assert data["peer_selections"] == []
        assert "next_free_id" not in data

    def test_put_503_when_catalog_absent(self, live_server_no_providers) -> None:
        _, base, _ = live_server_no_providers
        status, body = _request(
            base,
            "/api/markers/catalog/1",
            method="PUT",
            body={"name": "X", "color": "#ffffff"},
        )
        assert status == 503
        assert "catalog unavailable" in body

    def test_delete_503_when_catalog_absent(self, live_server_no_providers) -> None:
        _, base, _ = live_server_no_providers
        status, body = _request(base, "/api/markers/catalog/1", method="DELETE")
        assert status == 503
        assert "catalog unavailable" in body

    def test_selection_503_when_catalog_absent(self, live_server_no_providers) -> None:
        _, base, _ = live_server_no_providers
        status, body = _request(
            base,
            "/api/markers/selection",
            method="POST",
            body={"controlled_ids": [], "viewer_ids": []},
        )
        assert status == 503


@pytest.fixture
def live_server_no_sync(tmp_path, monkeypatch):
    """Server with catalog provider but no sync provider – exercises sync=None branches."""
    monkeypatch.setattr(discovery_module.BeaconSender, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconSender, "stop", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "stop", lambda self: None)

    port = _find_free_tcp_port()
    config_path = tmp_path / "config.toml"
    catalog = MarkerCatalog()
    server = ConfigWebServer(
        config_path=str(config_path),
        host="127.0.0.1",
        port=port,
        system_name="OpenFollow test",
        marker_catalog_provider=lambda: catalog,
    )
    server.start()
    assert _wait_for_port(port)
    base = f"http://127.0.0.1:{port}"
    yield server, base, str(config_path), catalog
    server.stop()


class TestSyncUnavailable:
    """Sync=None branches in GET/PUT/DELETE."""

    def test_get_omits_peer_selections_when_sync_absent(self, live_server_no_sync) -> None:
        _, base, _, catalog = live_server_no_sync
        catalog.upsert(1, "A", "#ff0000")
        status, body = _request(base, "/api/markers/catalog")
        assert status == 200
        data = json.loads(body)
        assert data["peer_selections"] == []
        assert len(data["entries"]) == 1

    def test_put_skips_delta_request_when_sync_absent(self, live_server_no_sync) -> None:
        _, base, _, catalog = live_server_no_sync
        status, _ = _request(
            base,
            "/api/markers/catalog/2",
            method="PUT",
            body={"name": "B", "color": "#00ff00"},
        )
        assert status == 200
        assert catalog.get(2) is not None

    def test_delete_skips_delta_request_when_sync_absent(self, live_server_no_sync) -> None:
        _, base, _, catalog = live_server_no_sync
        catalog.upsert(3, "C", "#0000ff")
        status, _ = _request(base, "/api/markers/catalog/3", method="DELETE")
        assert status == 200
        assert catalog.get(3) is None


class TestBadJsonBodies:
    def test_put_invalid_json(self, live_server) -> None:
        _, base, _, _, _ = live_server
        url = base + "/api/markers/catalog/1"
        req = urllib.request.Request(
            url,
            data=b"[1,2,3]",  # list, not dict
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status, body = resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            status, body = exc.code, exc.read().decode("utf-8")
        assert status == 400
        assert "Invalid JSON body" in body

    def test_put_coerces_non_string_name_and_color(self, live_server) -> None:
        _, base, _, catalog, _ = live_server
        status, _ = _request(
            base,
            "/api/markers/catalog/5",
            method="PUT",
            body={"name": 123, "color": ["not", "a", "string"]},
        )
        assert status == 200
        entry = catalog.get(5)
        assert entry is not None
        assert entry.name == ""
        assert entry.color == "#ffffff"

    def test_selection_invalid_json_body(self, live_server) -> None:
        _, base, _, _, _ = live_server
        url = base + "/api/markers/selection"
        req = urllib.request.Request(
            url,
            data=b'"a string, not a dict"',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        assert status == 400

    def test_selection_non_list_field_rejected(self, live_server) -> None:
        _, base, _, catalog, _ = live_server
        catalog.upsert(1, "A", "#ff0000")
        status, body = _request(
            base,
            "/api/markers/selection",
            method="POST",
            body={"controlled_ids": "not-a-list", "viewer_ids": []},
        )
        assert status == 400
        assert "invalid id list" in body

    def test_selection_bool_value_rejected(self, live_server) -> None:
        _, base, _, catalog, _ = live_server
        catalog.upsert(1, "A", "#ff0000")
        status, body = _request(
            base,
            "/api/markers/selection",
            method="POST",
            body={"controlled_ids": [True], "viewer_ids": []},
        )
        assert status == 400
        assert "invalid id list" in body


class TestDeleteIdValidation:
    def test_delete_id_zero_rejected(self, live_server) -> None:
        _, base, _, _, _ = live_server
        status, body = _request(base, "/api/markers/catalog/0", method="DELETE")
        assert status == 400
        assert "marker id must be >= 1" in body


@pytest.fixture
def live_server_bad_catalog_path(tmp_path, monkeypatch):
    """Server whose config points markers_catalog_path at a non-existent dir."""
    monkeypatch.setattr(discovery_module.BeaconSender, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconSender, "stop", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "stop", lambda self: None)

    from openfollow.configuration import load_config, save_config

    config_path = tmp_path / "config.toml"
    cfg = load_config(str(config_path))
    cfg.markers_catalog_path = str(tmp_path / "does-not-exist-dir" / "markers.toml")
    save_config(cfg, str(config_path))

    port = _find_free_tcp_port()
    catalog = MarkerCatalog()
    sync = _FakeSync()
    server = ConfigWebServer(
        config_path=str(config_path),
        host="127.0.0.1",
        port=port,
        system_name="OpenFollow test",
        marker_catalog_provider=lambda: catalog,
        marker_catalog_sync_provider=lambda: sync,
    )
    server.start()
    assert _wait_for_port(port)
    base = f"http://127.0.0.1:{port}"
    yield server, base, str(config_path), catalog, sync
    server.stop()


class TestCatalogPersistenceFailure:
    """OSError branches in PUT/DELETE when save_catalog raises."""

    def test_put_returns_500_on_disk_error(self, live_server_bad_catalog_path) -> None:
        _, base, _, _, _ = live_server_bad_catalog_path
        status, body = _request(
            base,
            "/api/markers/catalog/7",
            method="PUT",
            body={"name": "Z", "color": "#ffffff"},
        )
        assert status == 500
        assert "could not persist catalog" in body

    def test_delete_returns_500_on_disk_error(self, live_server_bad_catalog_path) -> None:
        _, base, _, catalog, _ = live_server_bad_catalog_path
        catalog.upsert(8, "Z", "#ffffff")
        status, body = _request(base, "/api/markers/catalog/8", method="DELETE")
        assert status == 500
        assert "could not persist catalog" in body


class TestResolveMarkerCatalogPath:
    def test_absolute_path_passes_through(self, tmp_path) -> None:
        from openfollow.web.routes import _resolve_marker_catalog_path

        abs_path = str(tmp_path / "markers.toml")
        out = _resolve_marker_catalog_path("/etc/openfollow/config.toml", abs_path)
        assert out == abs_path

    def test_relative_path_joins_to_config_parent(self) -> None:
        from openfollow.web.routes import _resolve_marker_catalog_path

        out = _resolve_marker_catalog_path("/etc/openfollow/config.toml", "markers.toml")
        assert out == "/etc/openfollow/markers.toml"
