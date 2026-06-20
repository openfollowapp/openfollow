# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Coverage for OpenFollowApp's marker catalog wiring.

These helpers are short bound methods on the app – too heavy to
instantiate the full app for, so we drive them via a SimpleNamespace
``self`` and unbound-method invocation. Covers:

- ``_bootstrap_station_identity``: seed UUID + name, persist failure
- ``_marker_catalog_path``: absolute vs relative resolution
- ``_load_or_seed_marker_catalog``: seed-from-selection, save failure
- ``_init_marker_catalog_sync._on_change``: rename routing + save failure
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from openfollow.app import OpenFollowApp
from openfollow.configuration import AppConfig
from openfollow.marker_catalog.catalog import MarkerCatalog

pytestmark = pytest.mark.unit


def _bind(fake, *names):
    """Bind the listed ``OpenFollowApp`` methods onto ``fake`` so the
    methods can call each other via ``self.X()`` during the test."""
    for name in names:
        method = getattr(OpenFollowApp, name)
        setattr(fake, name, types.MethodType(method, fake))


class TestBootstrapStationIdentity:
    def test_seeds_station_id_and_name_when_blank(self, tmp_path, monkeypatch) -> None:
        cfg = AppConfig(station_id="", psn_system_name="")
        config_path = str(tmp_path / "config.toml")
        save_calls: list = []

        def fake_save(_cfg, path):
            save_calls.append(path)

        monkeypatch.setattr("openfollow.app.save_config", fake_save)
        fake = types.SimpleNamespace(_config=cfg, _config_path=config_path)
        OpenFollowApp._bootstrap_station_identity(fake)  # type: ignore[arg-type]
        assert cfg.station_id != ""
        assert cfg.psn_system_name.startswith("OpenFollow ")
        assert save_calls == [config_path]

    def test_overrides_legacy_openfollow_name(self, tmp_path, monkeypatch) -> None:
        """The literal historical default ``"OpenFollow"`` is treated as
        unset so a freshly-upgraded fleet picks up unique names rather
        than every station rendering with the same string."""
        cfg = AppConfig(station_id="abc123", psn_system_name="OpenFollow")
        monkeypatch.setattr("openfollow.app.save_config", lambda *_a, **_k: None)
        fake = types.SimpleNamespace(_config=cfg, _config_path=str(tmp_path / "config.toml"))
        OpenFollowApp._bootstrap_station_identity(fake)  # type: ignore[arg-type]
        assert cfg.psn_system_name != "OpenFollow"
        assert cfg.psn_system_name.startswith("OpenFollow ")

    def test_keeps_operator_chosen_name(self, tmp_path, monkeypatch) -> None:
        cfg = AppConfig(station_id="abc", psn_system_name="Stage Left")
        saved: list = []
        monkeypatch.setattr(
            "openfollow.app.save_config",
            lambda *_a, **_k: saved.append(1),
        )
        fake = types.SimpleNamespace(_config=cfg, _config_path=str(tmp_path / "c.toml"))
        OpenFollowApp._bootstrap_station_identity(fake)  # type: ignore[arg-type]
        # No save: station_id was set and psn_system_name is operator-chosen.
        assert saved == []
        assert cfg.psn_system_name == "Stage Left"

    def test_oserror_on_save_is_logged_not_raised(self, tmp_path, monkeypatch) -> None:
        cfg = AppConfig(station_id="", psn_system_name="")
        config_path = str(tmp_path / "config.toml")

        def boom(*_a, **_kw):
            raise OSError("permission denied")

        monkeypatch.setattr("openfollow.app.save_config", boom)
        fake = types.SimpleNamespace(_config=cfg, _config_path=config_path)
        OpenFollowApp._bootstrap_station_identity(fake)  # type: ignore[arg-type]
        # No raise – the failure is logged.


class TestMarkerCatalogPath:
    def test_absolute_path_returned_verbatim(self, tmp_path) -> None:
        cfg = AppConfig(markers_catalog_path=str(tmp_path / "absolute.toml"))
        fake = types.SimpleNamespace(_config=cfg, _config_path="/etc/openfollow/config.toml")
        out = OpenFollowApp._marker_catalog_path(fake)  # type: ignore[arg-type]
        assert out == str(tmp_path / "absolute.toml")

    def test_relative_path_resolved_against_config_dir(self) -> None:
        cfg = AppConfig(markers_catalog_path="markers.toml")
        fake = types.SimpleNamespace(_config=cfg, _config_path="/etc/openfollow/config.toml")
        out = OpenFollowApp._marker_catalog_path(fake)  # type: ignore[arg-type]
        assert out == str(Path("/etc/openfollow") / "markers.toml")

    def test_empty_path_defaults_to_markers_toml(self) -> None:
        cfg = AppConfig(markers_catalog_path="")
        fake = types.SimpleNamespace(_config=cfg, _config_path="/etc/openfollow/config.toml")
        out = OpenFollowApp._marker_catalog_path(fake)  # type: ignore[arg-type]
        assert out.endswith("markers.toml")


class TestLoadOrSeedMarkerCatalog:
    def test_seeds_from_selection_when_catalog_empty(self, tmp_path) -> None:
        cfg = AppConfig(
            controlled_marker_ids=[1, 2],
            viewer_marker_ids=[3],
            markers_catalog_path=str(tmp_path / "markers.toml"),
        )
        fake = types.SimpleNamespace(
            _config=cfg,
            _config_path=str(tmp_path / "config.toml"),
        )
        _bind(fake, "_marker_catalog_path")
        catalog = OpenFollowApp._load_or_seed_marker_catalog(fake)  # type: ignore[arg-type]
        assert catalog.get(1) is not None
        assert catalog.get(2) is not None
        assert catalog.get(3) is not None
        # Seeded catalog is persisted so a restart preserves the names.
        assert (tmp_path / "markers.toml").exists()

    def test_skips_invalid_ids_in_selection(self, tmp_path) -> None:
        """Defense-in-depth: a selection list with ``0`` or negative
        entries does NOT crash the seed loop (``MarkerEntry.__init__``
        would raise) – we filter ``< 1`` first."""
        cfg = AppConfig(
            controlled_marker_ids=[0, 1],
            viewer_marker_ids=[-3, 2],
            markers_catalog_path=str(tmp_path / "markers.toml"),
        )
        fake = types.SimpleNamespace(
            _config=cfg,
            _config_path=str(tmp_path / "config.toml"),
        )
        _bind(fake, "_marker_catalog_path")
        catalog = OpenFollowApp._load_or_seed_marker_catalog(fake)  # type: ignore[arg-type]
        assert catalog.get(1) is not None
        assert catalog.get(2) is not None
        assert catalog.get(0) is None

    def test_existing_catalog_not_overwritten(self, tmp_path) -> None:
        """When the catalog file already exists with an entry, the
        seeder doesn't replace it – the seed loop only fills gaps."""
        from openfollow.marker_catalog.catalog import save_catalog as _save

        catalog_path = tmp_path / "markers.toml"
        pre = MarkerCatalog()
        pre.upsert(1, "OperatorName", "#abc123", updated_at=100.0)
        _save(pre, str(catalog_path))

        cfg = AppConfig(
            controlled_marker_ids=[1, 2],
            markers_catalog_path=str(catalog_path),
        )
        fake = types.SimpleNamespace(
            _config=cfg,
            _config_path=str(tmp_path / "config.toml"),
        )
        _bind(fake, "_marker_catalog_path")
        catalog = OpenFollowApp._load_or_seed_marker_catalog(fake)  # type: ignore[arg-type]
        # Existing entry preserved.
        assert catalog.get(1).name == "OperatorName"
        # Missing entry seeded.
        assert catalog.get(2) is not None
        assert catalog.get(2).name == "Marker 2"

    def test_save_failure_during_seed_is_logged(self, tmp_path, monkeypatch) -> None:
        cfg = AppConfig(
            controlled_marker_ids=[1],
            markers_catalog_path=str(tmp_path / "markers.toml"),
        )
        fake = types.SimpleNamespace(
            _config=cfg,
            _config_path=str(tmp_path / "config.toml"),
        )
        _bind(fake, "_marker_catalog_path")

        def boom(*_a, **_kw):
            raise OSError("disk full")

        monkeypatch.setattr("openfollow.app.save_catalog", boom)
        # Must not raise – failure is logged.
        catalog = OpenFollowApp._load_or_seed_marker_catalog(fake)  # type: ignore[arg-type]
        assert catalog.get(1) is not None

    def test_tombstoned_selection_id_not_resurrected_and_pruned(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        """Regression: create a marker, delete it (tombstone), restart.

        Before the fix the seeder consulted ``catalog.get`` (which hides
        tombstones) and re-``upsert``ed the deleted id as a live entry,
        so the marker reappeared after restart. Now seeding consults
        ``get_any`` and the tombstoned id is also pruned out of the
        per-station selection.
        """
        from openfollow.marker_catalog.catalog import save_catalog as _save

        catalog_path = tmp_path / "markers.toml"
        pre = MarkerCatalog()
        pre.upsert(1, "Live", "#abc123", updated_at=100.0)
        pre.upsert(2, "Doomed", "#222222", updated_at=100.0)
        pre.delete(2)  # tombstone marker 2
        _save(pre, str(catalog_path))

        cfg = AppConfig(
            controlled_marker_ids=[1, 2],
            viewer_marker_ids=[2],
            markers_catalog_path=str(catalog_path),
        )
        saved: list = []
        monkeypatch.setattr(
            "openfollow.app.save_config",
            lambda c, p: saved.append((list(c.controlled_marker_ids), list(c.viewer_marker_ids), p)),
        )
        fake = types.SimpleNamespace(
            _config=cfg,
            _config_path=str(tmp_path / "config.toml"),
        )
        _bind(fake, "_marker_catalog_path")
        catalog = OpenFollowApp._load_or_seed_marker_catalog(fake)  # type: ignore[arg-type]

        # Tombstone respected: marker 2 stays deleted (not resurrected),
        # marker 1 untouched.
        assert catalog.get(2) is None
        assert catalog.get_any(2) is not None and catalog.get_any(2).tombstone
        assert catalog.get(1) is not None
        # Selection pruned of the deleted id, and persisted exactly once.
        assert cfg.controlled_marker_ids == [1]
        assert cfg.viewer_marker_ids == []
        assert saved == [([1], [], str(tmp_path / "config.toml"))]

    def test_no_config_write_when_no_tombstones_to_prune(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        """The selection-prune save only fires when something is dropped;
        a clean selection leaves ``config.toml`` untouched."""
        cfg = AppConfig(
            controlled_marker_ids=[1, 2],
            markers_catalog_path=str(tmp_path / "markers.toml"),
        )
        saved: list = []
        monkeypatch.setattr(
            "openfollow.app.save_config",
            lambda *_a, **_k: saved.append(1),
        )
        fake = types.SimpleNamespace(
            _config=cfg,
            _config_path=str(tmp_path / "config.toml"),
        )
        _bind(fake, "_marker_catalog_path")
        OpenFollowApp._load_or_seed_marker_catalog(fake)  # type: ignore[arg-type]
        assert saved == []

    def test_prune_save_failure_during_load_is_logged(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        """If persisting the pruned selection fails, the catalog still
        loads (failure logged, not raised). The marker stays tombstoned
        and the in-memory selection is still pruned; the next boot
        retries the on-disk write."""
        from openfollow.marker_catalog.catalog import save_catalog as _save

        catalog_path = tmp_path / "markers.toml"
        pre = MarkerCatalog()
        pre.upsert(1, "Live", "#abc123", updated_at=100.0)
        pre.upsert(2, "Doomed", "#222222", updated_at=100.0)
        pre.delete(2)
        _save(pre, str(catalog_path))

        cfg = AppConfig(
            controlled_marker_ids=[1, 2],
            markers_catalog_path=str(catalog_path),
        )
        fake = types.SimpleNamespace(
            _config=cfg,
            _config_path=str(tmp_path / "config.toml"),
        )
        _bind(fake, "_marker_catalog_path")

        def boom(*_a, **_kw):
            raise OSError("disk full")

        monkeypatch.setattr("openfollow.app.save_config", boom)
        catalog = OpenFollowApp._load_or_seed_marker_catalog(fake)  # type: ignore[arg-type]
        assert catalog.get(2) is None  # tombstone respected
        assert cfg.controlled_marker_ids == [1]  # in-memory prune still applied

    def test_seed_uses_palette_auto_pick_order(self, tmp_path) -> None:
        """Seeded entries pick their colour from
        ``openfollow.palette.AUTO_PICK_ORDER`` by id-modulo. The
        user-editable palette field was retired. Palette module is the
        single source of truth."""
        from openfollow.palette import AUTO_PICK_ORDER

        cfg = AppConfig(
            controlled_marker_ids=[1],
            markers_catalog_path=str(tmp_path / "markers.toml"),
        )
        fake = types.SimpleNamespace(
            _config=cfg,
            _config_path=str(tmp_path / "config.toml"),
        )
        _bind(fake, "_marker_catalog_path")
        catalog = OpenFollowApp._load_or_seed_marker_catalog(fake)  # type: ignore[arg-type]
        # ``MarkerEntry`` normalizes colour to lowercase on store
        # (``_coerce_hex_color``); the palette ships canonical
        # uppercase. Assert against the stored form.
        assert catalog.get(1).color == AUTO_PICK_ORDER[1 % len(AUTO_PICK_ORDER)].lower()


class TestInitMarkerCatalogSyncOnChange:
    """Drive the ``_on_change`` closure that wires sync receiver updates
    back into the running PSN server + the on-disk catalog."""

    def _build_fake(self, tmp_path, *, server=None, controlled=None):
        cfg = AppConfig(
            controlled_marker_ids=controlled or [1, 2],
            markers_catalog_path=str(tmp_path / "markers.toml"),
            station_id="station-A",
        )
        catalog = MarkerCatalog()
        catalog.upsert(1, "Renamed", "#ff0000", updated_at=10.0)
        catalog.upsert(2, "AlsoRenamed", "#00ff00", updated_at=10.0)
        # _init_marker_catalog_sync reads iface IP via _resolved_source_ip.
        # Stub to stable value to avoid host network dependency.
        runtime_services = types.SimpleNamespace(
            _resolved_source_ip=lambda: "10.0.0.5",
        )
        fake = types.SimpleNamespace(
            _config=cfg,
            _config_path=str(tmp_path / "config.toml"),
            _marker_catalog=catalog,
            _server=server,
            _controlled_ids=controlled or [1, 2],
            _viewer_ids=[],
            _marker_catalog_sync=None,
            _runtime_services=runtime_services,
        )
        _bind(fake, "_marker_catalog_path", "_prune_selection_ids")
        return fake

    def _capture_on_change(self, fake, monkeypatch):
        """Invoke ``_init_marker_catalog_sync`` and return the captured
        ``_on_change`` closure without actually starting any threads."""
        captured: dict = {}

        class FakeSync:
            def __init__(self, *args, **kwargs):
                captured["on_change"] = kwargs.get("on_change")
                captured["selection_provider"] = kwargs.get("selection_provider")
                captured["station_name_provider"] = kwargs.get("station_name_provider")

            def start(self):
                pass

        monkeypatch.setattr(
            "openfollow.marker_catalog.sync.MarkerCatalogSync",
            FakeSync,
        )
        OpenFollowApp._init_marker_catalog_sync(fake)  # type: ignore[arg-type]
        return captured

    def test_on_change_renames_controlled_marker(self, tmp_path, monkeypatch) -> None:
        renames: list = []

        class FakeServer:
            def update_marker_name(self, mid, name):
                renames.append((mid, name))

        fake = self._build_fake(tmp_path, server=FakeServer(), controlled=[1])
        captured = self._capture_on_change(fake, monkeypatch)
        captured["on_change"]([1])
        assert renames == [(1, "Renamed")]

    def test_on_change_skips_uncontrolled_markers(self, tmp_path, monkeypatch) -> None:
        renames: list = []

        class FakeServer:
            def update_marker_name(self, mid, name):
                renames.append((mid, name))

        fake = self._build_fake(tmp_path, server=FakeServer(), controlled=[1])
        captured = self._capture_on_change(fake, monkeypatch)
        captured["on_change"]([2])  # 2 not in controlled_ids
        assert renames == []

    def test_on_change_skips_when_server_is_none(self, tmp_path, monkeypatch) -> None:
        fake = self._build_fake(tmp_path, server=None)
        captured = self._capture_on_change(fake, monkeypatch)
        # Must not raise even though the server isn't up yet.
        captured["on_change"]([1])

    def test_on_change_skips_when_entry_missing_or_unnamed(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        renames: list = []

        class FakeServer:
            def update_marker_name(self, mid, name):
                renames.append((mid, name))

        fake = self._build_fake(tmp_path, server=FakeServer(), controlled=[5, 6])
        # Make 5 a tombstone (entry comes back as None from .get) and 6
        # have an empty name (skipped).
        fake._marker_catalog.upsert(5, "", "#0000ff", updated_at=1.0)
        fake._marker_catalog.delete(5)
        fake._marker_catalog.upsert(6, "", "#ff00ff", updated_at=1.0)
        captured = self._capture_on_change(fake, monkeypatch)
        captured["on_change"]([5, 6])
        assert renames == []

    def test_on_change_prunes_selection_when_peer_deletes(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        """A peer's delete (tombstone) for a marker this station was
        controlling/viewing is pruned out of our on-disk selection, so
        the animate-loop config hot-reload tears down the live PSN
        marker here too – no restart needed."""
        from openfollow.configuration import load_config, save_config

        class FakeServer:
            def update_marker_name(self, mid, name):
                pass

        fake = self._build_fake(tmp_path, server=FakeServer(), controlled=[1, 2])
        fake._viewer_ids = [2]
        # Persist a real config carrying the selection on disk – the
        # prune reads disk, not the in-memory _config.
        fake._config.controlled_marker_ids = [1, 2]
        fake._config.viewer_marker_ids = [2]
        save_config(fake._config, fake._config_path)
        # Peer tombstones marker 2.
        fake._marker_catalog.delete(2)
        captured = self._capture_on_change(fake, monkeypatch)
        captured["on_change"]([2])
        cfg = load_config(fake._config_path)
        assert cfg.controlled_marker_ids == [1]
        assert cfg.viewer_marker_ids == []

    def test_on_change_no_config_write_for_undriven_peer_delete(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        """A peer deletes a marker this station never controlled/viewed:
        the catalog still tombstones (handled by the merge), but our
        selection is untouched, so no config write happens."""
        from openfollow.configuration import save_config

        class FakeServer:
            def update_marker_name(self, mid, name):
                pass

        fake = self._build_fake(tmp_path, server=FakeServer(), controlled=[1])
        fake._viewer_ids = []
        fake._config.controlled_marker_ids = [1]
        fake._config.viewer_marker_ids = []
        save_config(fake._config, fake._config_path)
        # Catalog knows marker 9, then a peer tombstones it; we never
        # drove it.
        fake._marker_catalog.upsert(9, "Theirs", "#abcdef", updated_at=1.0)
        fake._marker_catalog.delete(9)
        saves: list = []
        monkeypatch.setattr(
            "openfollow.app.save_config",
            lambda *_a, **_k: saves.append(1),
        )
        captured = self._capture_on_change(fake, monkeypatch)
        captured["on_change"]([9])
        assert saves == []

    def test_on_change_ignores_fully_unknown_changed_id(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:

        class FakeServer:
            def update_marker_name(self, mid, name):
                raise AssertionError("must not rename an unknown id")

        fake = self._build_fake(tmp_path, server=FakeServer(), controlled=[1])
        saves: list = []
        monkeypatch.setattr(
            "openfollow.app.save_config",
            lambda *_a, **_k: saves.append(1),
        )
        captured = self._capture_on_change(fake, monkeypatch)
        captured["on_change"]([99])  # 99 absent from the catalog entirely
        assert saves == []

    def test_on_change_save_failure_logged_not_raised(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        class FakeServer:
            def update_marker_name(self, mid, name):
                pass

        fake = self._build_fake(tmp_path, server=FakeServer(), controlled=[1])

        def boom(*_a, **_kw):
            raise OSError("disk full")

        captured = self._capture_on_change(fake, monkeypatch)
        monkeypatch.setattr("openfollow.app.save_catalog", boom)
        # Must not raise.
        captured["on_change"]([1])

    def test_selection_provider_returns_lists(self, tmp_path, monkeypatch) -> None:
        fake = self._build_fake(tmp_path, server=None, controlled=[1, 2])
        fake._viewer_ids = [3]
        captured = self._capture_on_change(fake, monkeypatch)
        ctrl, view = captured["selection_provider"]()
        assert ctrl == [1, 2]
        assert view == [3]
        # Returned copies, not aliases – mutating the result doesn't
        # corrupt app state.
        ctrl.append(99)
        assert fake._controlled_ids == [1, 2]


class TestPruneSelectionIds:
    """Direct coverage for ``_prune_selection_ids`` (called from the sync
    receiver thread when a peer's delete tombstones a marker)."""

    def test_save_failure_is_logged_not_raised(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        from openfollow.configuration import save_config

        cfg = AppConfig(controlled_marker_ids=[1, 2], viewer_marker_ids=[2])
        config_path = str(tmp_path / "config.toml")
        save_config(cfg, config_path)  # real selection on disk
        fake = types.SimpleNamespace(_config=cfg, _config_path=config_path)

        def boom(*_a, **_kw):
            raise OSError("read-only fs")

        monkeypatch.setattr("openfollow.app.save_config", boom)
        # Must not raise even though persisting the prune fails.
        OpenFollowApp._prune_selection_ids(fake, [2])  # type: ignore[arg-type]


class TestInitMarkerCatalogSyncIfaceResolution:
    """Regression for one-way-multicast bug: without resolving iface IP,
    kernel picks arbitrary outbound. Now uses _resolved_source_ip."""

    def _build_fake(self, tmp_path, *, resolved_ip: str = "10.0.0.42"):
        cfg = AppConfig(
            controlled_marker_ids=[1],
            markers_catalog_path=str(tmp_path / "markers.toml"),
            station_id="station-A",
        )
        catalog = MarkerCatalog()
        runtime_services = types.SimpleNamespace(
            _resolved_source_ip=lambda: resolved_ip,
        )
        fake = types.SimpleNamespace(
            _config=cfg,
            _config_path=str(tmp_path / "config.toml"),
            _marker_catalog=catalog,
            _server=None,
            _controlled_ids=[1],
            _viewer_ids=[],
            _marker_catalog_sync=None,
            _runtime_services=runtime_services,
        )
        _bind(fake, "_marker_catalog_path")
        return fake

    def _capture_kwargs(self, fake, monkeypatch):
        captured: dict = {}

        class FakeSync:
            def __init__(self, *args, **kwargs):
                captured.update(kwargs)

            def start(self):
                pass

        monkeypatch.setattr(
            "openfollow.marker_catalog.sync.MarkerCatalogSync",
            FakeSync,
        )
        OpenFollowApp._init_marker_catalog_sync(fake)  # type: ignore[arg-type]
        return captured

    def test_uses_runtime_services_resolved_source_ip(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        fake = self._build_fake(tmp_path, resolved_ip="10.0.0.42")
        captured = self._capture_kwargs(fake, monkeypatch)
        assert captured["iface_ip"] == "10.0.0.42"

    def test_explicit_psn_source_ip_resolves_through_shared_helper(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        """When the operator pinned an IP, ``_resolved_source_ip``
        returns it directly so the sync's bind matches the server's."""
        fake = self._build_fake(tmp_path, resolved_ip="192.168.0.50")
        captured = self._capture_kwargs(fake, monkeypatch)
        assert captured["iface_ip"] == "192.168.0.50"
