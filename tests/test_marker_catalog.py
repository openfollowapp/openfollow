# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for the shared marker catalog (id/name/color).

Covers the LWW merge semantics, tombstones, TOML round-trip, and the
``id >= 1`` invariant the catalog enforces project-wide.
"""

from __future__ import annotations

import pytest

from openfollow.marker_catalog.catalog import (
    MarkerCatalog,
    MarkerEntry,
    load_catalog,
    save_catalog,
)

pytestmark = pytest.mark.unit


class TestMarkerEntry:
    def test_rejects_zero_id(self) -> None:
        with pytest.raises(ValueError):
            MarkerEntry(id=0, name="X", color="#ff0000", updated_at=0.0)

    def test_rejects_negative_id(self) -> None:
        with pytest.raises(ValueError):
            MarkerEntry(id=-3, name="X", color="#ff0000", updated_at=0.0)

    def test_rejects_bool_id(self) -> None:
        with pytest.raises(ValueError):
            MarkerEntry(id=True, name="X", color="#ff0000", updated_at=0.0)  # type: ignore[arg-type]

    def test_coerces_invalid_color_to_white(self) -> None:
        e = MarkerEntry(id=1, name="X", color="red", updated_at=0.0)
        assert e.color == "#ffffff"

    def test_lowercases_color(self) -> None:
        e = MarkerEntry(id=1, name="X", color="#ABCDEF", updated_at=0.0)
        assert e.color == "#abcdef"

    def test_coerces_bad_timestamp(self) -> None:
        e = MarkerEntry(id=1, name="X", color="#ff0000", updated_at="bogus")  # type: ignore[arg-type]
        assert e.updated_at == 0.0

    def test_coerces_non_string_name_to_empty(self) -> None:
        e = MarkerEntry(id=1, name=123, color="#ff0000", updated_at=0.0)  # type: ignore[arg-type]
        assert e.name == ""

    def test_non_finite_updated_at_normalised_to_zero(self) -> None:
        """``updated_at`` drives LWW merge via ``remote.updated_at <=
        existing.updated_at`` in ``MarkerCatalog.merge_entry``. NaN
        poisons that comparison (``nan <= x`` is always ``False``),
        so a NaN-stamped entry would never win and never lose,
        deadlocking the merge. ``inf`` would let any finite later
        write lose forever. Both collapse to ``0.0`` here – same
        fallback as a non-numeric ``updated_at`` – so the merge has a
        finite total order to work with. Defense-in-depth: the loader
        and the sync receiver already filter bad timestamps at their
        boundaries, but ``MarkerEntry`` is the dataclass shared by both
        paths."""
        e = MarkerEntry(
            id=1,
            name="X",
            color="#ff0000",
            updated_at=float("nan"),
        )
        assert e.updated_at == 0.0
        e = MarkerEntry(
            id=1,
            name="X",
            color="#ff0000",
            updated_at=float("inf"),
        )
        assert e.updated_at == 0.0
        e = MarkerEntry(
            id=1,
            name="X",
            color="#ff0000",
            updated_at=float("-inf"),
        )
        assert e.updated_at == 0.0
        # Finite timestamps survive unchanged.
        e = MarkerEntry(
            id=1,
            name="X",
            color="#ff0000",
            updated_at=1234.5,
        )
        assert e.updated_at == 1234.5

    def test_strict_bool_tombstone_rejects_truthy_strings(self) -> None:
        """Defense-in-depth. ``bool("false")`` is
        ``True`` in Python, so a previous ``bool(self.tombstone)``
        cast in ``__post_init__`` would have re-introduced the same
        trap the TOML loader and the sync receiver's
        ``_entry_from_dict`` already guard against. A future caller
        that forgets to sanitise (test fixture, programmatic
        construction, future loader change) therefore can't smuggle
        in a string-truthy tombstone here – anything that isn't a
        real ``bool`` collapses to ``False``."""
        e = MarkerEntry(
            id=1,
            name="X",
            color="#ff0000",
            updated_at=0.0,
            tombstone="false",  # type: ignore[arg-type]
        )
        assert e.tombstone is False
        e = MarkerEntry(
            id=1,
            name="X",
            color="#ff0000",
            updated_at=0.0,
            tombstone=1,  # type: ignore[arg-type]
        )
        assert e.tombstone is False
        # Real bool still works.
        e = MarkerEntry(
            id=1,
            name="X",
            color="#ff0000",
            updated_at=0.0,
            tombstone=True,
        )
        assert e.tombstone is True


class TestMarkerCatalog:
    def test_get_skips_tombstones(self) -> None:
        cat = MarkerCatalog()
        cat.upsert(1, "Spot 1", "#ff0000")
        assert cat.get(1) is not None
        cat.delete(1)
        assert cat.get(1) is None

    def test_get_any_returns_tombstone(self) -> None:
        cat = MarkerCatalog()
        cat.upsert(1, "Spot 1", "#ff0000")
        cat.delete(1)
        tomb = cat.get_any(1)
        assert tomb is not None
        assert tomb.tombstone is True

    def test_live_entries_excludes_tombstones_and_sorts(self) -> None:
        cat = MarkerCatalog()
        cat.upsert(3, "C", "#0000ff")
        cat.upsert(1, "A", "#ff0000")
        cat.upsert(2, "B", "#00ff00")
        cat.delete(2)
        live = cat.live_entries()
        assert [e.id for e in live] == [1, 3]

    def test_upsert_rejects_id_zero(self) -> None:
        cat = MarkerCatalog()
        with pytest.raises(ValueError):
            cat.upsert(0, "X", "#ff0000")

    def test_upsert_clears_tombstone(self) -> None:
        cat = MarkerCatalog()
        cat.upsert(1, "Spot 1", "#ff0000")
        cat.delete(1)
        cat.upsert(1, "Spot 1 reborn", "#00ff00")
        assert cat.get(1) is not None
        assert cat.get(1).name == "Spot 1 reborn"

    def test_delete_unknown_id_is_noop(self) -> None:
        cat = MarkerCatalog()
        assert cat.delete(42) is None

    def test_merge_newer_wins(self) -> None:
        cat = MarkerCatalog()
        cat.upsert(1, "Old", "#ff0000")  # version 1
        applied = cat.merge_entry(
            MarkerEntry(id=1, name="New", color="#00ff00", updated_at=2.0, version=2),
        )
        assert applied is True
        assert cat.get(1).name == "New"

    def test_merge_older_loses(self) -> None:
        cat = MarkerCatalog()
        cat.upsert(1, "Newer", "#00ff00", updated_at=5.0)
        applied = cat.merge_entry(
            MarkerEntry(id=1, name="Older", color="#ff0000", updated_at=1.0),
        )
        assert applied is False
        assert cat.get(1).name == "Newer"

    def test_merge_equal_timestamp_keeps_incumbent(self) -> None:
        cat = MarkerCatalog()
        cat.upsert(1, "Incumbent", "#00ff00", updated_at=3.0)
        applied = cat.merge_entry(
            MarkerEntry(id=1, name="Challenger", color="#ff0000", updated_at=3.0),
        )
        assert applied is False
        assert cat.get(1).name == "Incumbent"

    def test_merge_new_id_is_applied(self) -> None:
        cat = MarkerCatalog()
        applied = cat.merge_entry(
            MarkerEntry(id=7, name="New", color="#ff0000", updated_at=1.0),
        )
        assert applied is True
        assert cat.get(7).name == "New"

    def test_merge_tombstone_overrides_live(self) -> None:
        cat = MarkerCatalog()
        cat.upsert(1, "Live", "#ff0000")  # version 1
        tomb = MarkerEntry(
            id=1,
            name="Live",
            color="#ff0000",
            updated_at=2.0,
            tombstone=True,
            version=2,
        )
        cat.merge_entry(tomb)
        assert cat.get(1) is None

    def test_next_free_id_skips_tombstones(self) -> None:
        cat = MarkerCatalog()
        cat.upsert(1, "A", "#ff0000")
        cat.upsert(2, "B", "#00ff00")
        cat.delete(2)
        # Deleted ids stay reserved so a re-add doesn't reuse a number
        # with different historical meaning.
        assert cat.next_free_id() == 3

    def test_next_free_id_finds_gap(self) -> None:
        cat = MarkerCatalog()
        cat.upsert(3, "C", "#0000ff")
        assert cat.next_free_id() == 1

    def test_len_excludes_tombstones(self) -> None:
        cat = MarkerCatalog()
        cat.upsert(1, "A", "#ff0000")
        cat.upsert(2, "B", "#00ff00")
        cat.delete(2)
        assert len(cat) == 1


class TestTomlRoundtrip:
    def test_roundtrip_including_tombstones(self, tmp_path) -> None:
        path = str(tmp_path / "markers.toml")
        cat = MarkerCatalog()
        cat.upsert(1, "Spot 1", "#ff0000", updated_at=10.0)
        cat.upsert(2, "Spot 2", "#00ff00", updated_at=20.0)
        cat.delete(2)
        save_catalog(cat, path)
        reloaded = load_catalog(path)
        assert reloaded.get(1) is not None
        assert reloaded.get(1).name == "Spot 1"
        # Tombstone survives the round-trip and continues to block live reads.
        assert reloaded.get(2) is None
        assert reloaded.get_any(2) is not None
        assert reloaded.get_any(2).tombstone is True

    def test_load_missing_file_returns_empty(self, tmp_path) -> None:
        cat = load_catalog(str(tmp_path / "does-not-exist.toml"))
        assert len(cat) == 0

    def test_load_corrupt_toml_returns_empty_with_log(self, tmp_path, caplog) -> None:
        """Parse error on the TOML body itself falls through to an empty
        catalog – better than crashing the app on a hand-edited file."""
        path = tmp_path / "markers.toml"
        path.write_text("this is not valid toml [[[", encoding="utf-8")
        with caplog.at_level("ERROR"):
            cat = load_catalog(str(path))
        assert len(cat) == 0
        assert any("Failed to parse" in r.message for r in caplog.records)

    def test_load_marker_key_not_array_warns(self, tmp_path, caplog) -> None:
        """``[marker]`` (table) instead of ``[[marker]]`` (array of tables)
        is a hand-edit error – warn and return empty rather than crash."""
        path = tmp_path / "markers.toml"
        path.write_text(
            '[marker]\nid = 1\nname = "oops"\n',
            encoding="utf-8",
        )
        with caplog.at_level("WARNING"):
            cat = load_catalog(str(path))
        assert len(cat) == 0
        assert any("'marker' must be array-of-tables" in r.message for r in caplog.records)

    def test_load_skips_non_dict_entries(self, tmp_path) -> None:
        """Strings/numbers in the marker array (e.g. a hand-edit error
        that swapped ``[[marker]]`` for inline values) are skipped."""
        path = tmp_path / "markers.toml"
        path.write_text(
            'marker = ["oops", 42]\n',
            encoding="utf-8",
        )
        cat = load_catalog(str(path))
        assert len(cat) == 0

    def test_load_rejects_bool_id_explicitly(self, tmp_path, caplog) -> None:
        """``int(True)`` is ``1`` – a hand-edited ``id = true`` in
        ``markers.toml`` would silently clobber the real marker 1
        entry. The loader rejects bool ids explicitly before the
        ``int()`` cast and warns about the dropped entry."""
        path = tmp_path / "markers.toml"
        path.write_text(
            "[[marker]]\n"
            "id = true\n"
            'name = "oops"\n'
            'color = "#ff0000"\n'
            "updated_at = 1.0\n"
            "\n"
            "[[marker]]\n"
            "id = 1\n"
            'name = "real"\n'
            'color = "#00ff00"\n'
            "updated_at = 2.0\n",
            encoding="utf-8",
        )
        with caplog.at_level("WARNING"):
            cat = load_catalog(str(path))
        # Real id=1 survives untouched; bool entry dropped with a warning.
        assert cat.get(1) is not None
        assert cat.get(1).name == "real"
        assert any("bool id" in r.message for r in caplog.records)

    def test_load_string_tombstone_does_not_trigger_delete(self, tmp_path) -> None:
        """``bool("false")`` is ``True`` in Python, so a hand-edited
        ``markers.toml`` with ``tombstone = "false"`` (a string) would
        silently mark the entry deleted under the previous
        ``bool(...)`` coercion. The loader now requires a real boolean
        and defaults to ``False`` for everything else, so the entry
        comes back live."""
        path = tmp_path / "markers.toml"
        path.write_text(
            "[[marker]]\n"
            "id = 1\n"
            'name = "Live entry"\n'
            'color = "#ff0000"\n'
            "updated_at = 1.0\n"
            'tombstone = "false"\n',  # string, not bool
            encoding="utf-8",
        )
        cat = load_catalog(str(path))
        live = cat.get(1)
        assert live is not None
        assert live.name == "Live entry"

    def test_load_non_string_name_normalised_to_empty(self, tmp_path) -> None:
        """A hand-edited TOML with a non-string ``name`` (e.g. an int)
        should not persist a surprising ``"123"`` / ``"None"``
        representation. The loader hands the raw value through to
        ``MarkerEntry.__post_init__`` which normalises non-strings
        to ``""`` rather than ``str()``-coercing them at the loader
        boundary."""
        path = tmp_path / "markers.toml"
        path.write_text(
            '[[marker]]\nid = 1\nname = 123\ncolor = "#ff0000"\nupdated_at = 1.0\n',
            encoding="utf-8",
        )
        cat = load_catalog(str(path))
        entry = cat.get(1)
        assert entry is not None
        assert entry.name == ""

    def test_load_skips_non_int_id(self, tmp_path) -> None:
        """An ``id`` field that can't be coerced to int is skipped."""
        path = tmp_path / "markers.toml"
        path.write_text(
            '[[marker]]\nid = "not-a-number"\nname = "X"\ncolor = "#ff0000"\nupdated_at = 1.0\n',
            encoding="utf-8",
        )
        cat = load_catalog(str(path))
        assert len(cat) == 0

    def test_load_invalid_color_doesnt_raise(self, tmp_path) -> None:
        """``MarkerEntry`` coerces an invalid color to ``#ffffff`` rather
        than raising, so the entry still loads with the default color
        (the catalog never silently drops a valid-id entry on a soft
        field error)."""
        path = tmp_path / "markers.toml"
        path.write_text(
            '[[marker]]\nid = 1\nname = "X"\ncolor = "not-a-hex"\nupdated_at = 1.0\n',
            encoding="utf-8",
        )
        cat = load_catalog(str(path))
        assert cat.get(1) is not None
        assert cat.get(1).color == "#ffffff"

    def test_save_cleanup_on_write_failure(self, tmp_path, monkeypatch) -> None:
        """If the atomic-rename ``os.replace`` raises, the tempfile is
        unlinked so we don't leak ``markers.toml.*.tmp`` next to the
        target."""
        import os as os_module

        path = tmp_path / "markers.toml"
        cat = MarkerCatalog()
        cat.upsert(1, "X", "#ff0000", updated_at=1.0)

        def boom(*_a, **_kw):
            raise OSError("simulated rename failure")

        monkeypatch.setattr(os_module, "replace", boom)
        with pytest.raises(OSError, match="simulated"):
            save_catalog(cat, str(path))
        # No leftover .tmp files in the target directory.
        leftover = [p for p in tmp_path.iterdir() if p.name.startswith("markers.toml.")]
        assert leftover == []

    def test_load_drops_entry_when_construction_raises(self, tmp_path, caplog) -> None:
        """``load_catalog`` pre-casts ``updated_at`` via ``float(...)``
        before constructing ``MarkerEntry`` (see ``catalog.py``'s
        loader). A value like ``updated_at = "bogus"`` therefore
        raises ``ValueError`` from the pre-cast itself, inside the
        ``try`` around the ``MarkerEntry(...)`` call. The entry is
        dropped with a warning so the rest of the catalog still
        loads."""
        path = tmp_path / "markers.toml"
        path.write_text(
            '[[marker]]\nid = 1\nname = "X"\ncolor = "#ff0000"\nupdated_at = "not-a-float"\n',
            encoding="utf-8",
        )
        with caplog.at_level("WARNING"):
            cat = load_catalog(str(path))
        assert len(cat) == 0
        assert any("dropping invalid entry" in r.message for r in caplog.records)

    def test_save_cleanup_handles_unlink_failure(self, tmp_path, monkeypatch) -> None:
        """When tempfile cleanup itself errors, ``save_catalog`` still
        re-raises the original write failure rather than masking it with
        the cleanup error."""
        import os as os_module

        path = tmp_path / "markers.toml"
        cat = MarkerCatalog()
        cat.upsert(1, "X", "#ff0000", updated_at=1.0)

        def boom_replace(*_a, **_kw):
            raise OSError("rename boom")

        def boom_unlink(*_a, **_kw):
            raise OSError("unlink boom")

        monkeypatch.setattr(os_module, "replace", boom_replace)
        monkeypatch.setattr(os_module, "unlink", boom_unlink)
        with pytest.raises(OSError, match="rename boom"):
            save_catalog(cat, str(path))


class TestTomlRoundtripContinued:
    def test_load_drops_invalid_ids_with_warning(self, tmp_path, caplog) -> None:
        path = tmp_path / "markers.toml"
        path.write_text(
            "[[marker]]\n"
            "id = 0\n"
            'name = "reserved"\n'
            'color = "#ff0000"\n'
            "updated_at = 1.0\n"
            "tombstone = false\n"
            "\n"
            "[[marker]]\n"
            "id = 5\n"
            'name = "good"\n'
            'color = "#00ff00"\n'
            "updated_at = 1.0\n"
            "tombstone = false\n",
            encoding="utf-8",
        )
        with caplog.at_level("WARNING"):
            cat = load_catalog(str(path))
        assert cat.get(5) is not None
        assert cat.get_any(0) is None
        assert any("id=0" in r.message or "id=" in r.message for r in caplog.records)


class TestMergeEntryTombstoneGuard:
    """merge_entry must not materialise a tombstone for an id it has
    never seen – mirrors delete()'s unbounded-growth guard."""

    def test_unknown_id_tombstone_is_dropped(self) -> None:
        from openfollow.marker_catalog.catalog import MarkerCatalog, MarkerEntry

        cat = MarkerCatalog()
        tomb = MarkerEntry(id=999, name="", color="#ffffff", updated_at=100.0, tombstone=True)
        changed = cat.merge_entry(tomb)
        assert changed is False
        assert cat.get(999) is None
        assert 999 not in {e.id for e in cat.all_entries()}

    def test_unknown_id_live_entry_is_stored(self) -> None:
        from openfollow.marker_catalog.catalog import MarkerCatalog, MarkerEntry

        cat = MarkerCatalog()
        live = MarkerEntry(id=5, name="Spot", color="#ff0000", updated_at=100.0, tombstone=False)
        assert cat.merge_entry(live) is True
        assert cat.get(5) is not None

    def test_known_id_can_still_be_tombstoned_via_merge(self) -> None:
        from openfollow.marker_catalog.catalog import MarkerCatalog, MarkerEntry

        cat = MarkerCatalog()
        cat.merge_entry(MarkerEntry(id=5, name="Spot", color="#ff0000", updated_at=100.0))
        tomb = MarkerEntry(id=5, name="Spot", color="#ff0000", updated_at=200.0, tombstone=True)
        assert cat.merge_entry(tomb) is True
        got = cat.get_any(5)
        assert got is not None and got.tombstone is True


class TestMarkerEntryVersion:
    @pytest.mark.parametrize("bad", [True, False, -1, "5", 1.5, None, [1]])
    def test_bad_version_coerced_to_zero(self, bad: object) -> None:
        entry = MarkerEntry(id=1, name="X", color="#ff0000", updated_at=0.0, version=bad)
        assert entry.version == 0

    def test_valid_version_kept(self) -> None:
        entry = MarkerEntry(id=1, name="X", color="#ff0000", updated_at=0.0, version=7)
        assert entry.version == 7

    @pytest.mark.parametrize("bad", [123, None, [], 1.5])
    def test_non_string_origin_coerced_to_empty(self, bad: object) -> None:
        entry = MarkerEntry(id=1, name="X", color="#ff0000", updated_at=0.0, origin=bad)
        assert entry.origin == ""

    def test_version_capped_at_max(self) -> None:
        # A hostile/buggy peer can't push version past the TOML 64-bit range
        # (which would write spec-violating markers.toml or poison the clock).
        entry = MarkerEntry(id=1, name="X", color="#ff0000", updated_at=0.0, version=2**63)
        assert entry.version == 2**63 - 1

    def test_origin_sanitised_and_length_capped(self) -> None:
        raw = "st\x00\n\x1bX" + "y" * 300
        entry = MarkerEntry(id=1, name="X", color="#ff0000", updated_at=0.0, origin=raw)
        assert "\x00" not in entry.origin and "\n" not in entry.origin
        assert entry.origin.startswith("stXy")
        assert len(entry.origin) <= 128


class TestLogicalClockConflictResolution:
    """The clock-skew bug: a fresh local edit must win over a stale remote even
    when the remote carries a larger (clock-ahead) wall-clock ``updated_at``."""

    def test_local_edit_beats_stale_remote_with_higher_wallclock(self) -> None:
        cat = MarkerCatalog()
        # A clock-ahead peer's heartbeat arrives first: stale name, far-future
        # wall stamp, some logical version.
        cat.merge_entry(
            MarkerEntry(id=1, name="OldName", color="#ffffff", updated_at=1_000_000.0, version=5),
        )
        # Operator renames locally now; our clock is well behind the peer's.
        cat.upsert(1, "NewName", "#ffffff", updated_at=10.0)
        assert cat.get(1).name == "NewName"
        # The peer keeps re-broadcasting its stale entry; it must NOT revert us.
        reverted = cat.merge_entry(
            MarkerEntry(id=1, name="OldName", color="#ffffff", updated_at=1_000_000.0, version=5),
        )
        assert reverted is False
        assert cat.get(1).name == "NewName"

    def test_upsert_assigns_monotonic_versions(self) -> None:
        cat = MarkerCatalog()
        assert cat.upsert(1, "A", "#ffffff").version == 1
        assert cat.upsert(2, "B", "#ffffff").version == 2
        assert cat.upsert(1, "A2", "#ffffff").version == 3

    def test_upsert_records_origin(self) -> None:
        cat = MarkerCatalog()
        assert cat.upsert(1, "A", "#ffffff", origin="station-x").origin == "station-x"

    def test_delete_records_origin_and_bumps_version(self) -> None:
        cat = MarkerCatalog()
        cat.upsert(1, "A", "#ffffff")  # version 1
        tomb = cat.delete(1, origin="station-y")
        assert tomb is not None
        assert tomb.origin == "station-y"
        assert tomb.version == 2

    def test_local_edit_outranks_seen_peer_version(self) -> None:
        cat = MarkerCatalog()
        cat.merge_entry(MarkerEntry(id=2, name="Peer", color="#ffffff", updated_at=0.0, version=42))
        # Next local edit must out-rank the highest version seen from any peer.
        assert cat.upsert(1, "Local", "#ffffff").version == 43

    def test_ignored_unknown_tombstone_still_advances_clock(self) -> None:
        """An unknown-id tombstone is ignored for memory, but its version MUST
        still advance the logical clock. Otherwise a later local create of that
        id gets a lower version and the peer's re-broadcast tombstone out-ranks
        and silently deletes it. Guards the clock bump that precedes the
        unknown-tombstone early-return in merge_entry."""
        cat = MarkerCatalog()
        # Peer deleted id 5 long ago at a high version; we never held it.
        ignored = MarkerEntry(id=5, name="Gone", color="#ffffff", updated_at=0.0, version=10, tombstone=True)
        assert cat.merge_entry(ignored) is False
        assert cat.get_any(5) is None  # ignored, not materialised
        # A local create must out-rank what we've already seen.
        created = cat.upsert(5, "MyMarker", "#ffffff")
        assert created.version > 10
        # The peer keeps re-broadcasting its stale tombstone; it must not win.
        restale = MarkerEntry(id=5, name="Gone", color="#ffffff", updated_at=9e9, version=10, tombstone=True)
        assert cat.merge_entry(restale) is False
        assert cat.get(5) is not None
        assert cat.get(5).name == "MyMarker"

    def test_higher_version_wins_regardless_of_wallclock(self) -> None:
        cat = MarkerCatalog()
        cat.merge_entry(MarkerEntry(id=1, name="V2", color="#ffffff", updated_at=1.0, version=2))
        # Lower version loses even with a much larger updated_at.
        assert cat.merge_entry(MarkerEntry(id=1, name="V1", color="#ffffff", updated_at=9e9, version=1)) is False
        assert cat.get(1).name == "V2"

    def test_same_version_falls_back_to_wallclock_then_origin(self) -> None:
        cat = MarkerCatalog()
        cat.merge_entry(MarkerEntry(id=1, name="early", color="#ffffff", updated_at=1.0, version=3, origin="a"))
        # Same version, later wall stamp -> wins.
        later = MarkerEntry(id=1, name="late", color="#ffffff", updated_at=2.0, version=3, origin="a")
        assert cat.merge_entry(later) is True
        # Same version and wall stamp, higher origin -> wins (deterministic tiebreak).
        higher_origin = MarkerEntry(id=1, name="z-origin", color="#ffffff", updated_at=2.0, version=3, origin="z")
        assert cat.merge_entry(higher_origin) is True
        assert cat.get(1).name == "z-origin"


class TestLogicalClockPersistence:
    def test_save_load_round_trips_version_and_origin(self, tmp_path) -> None:
        cat = MarkerCatalog()
        cat.upsert(1, "A", "#ffffff", origin="st-1")  # version 1
        cat.upsert(1, "A2", "#ffffff", origin="st-1")  # version 2
        path = str(tmp_path / "markers.toml")
        save_catalog(cat, path)
        loaded = load_catalog(path)
        entry = loaded.get(1)
        assert entry.version == 2
        assert entry.origin == "st-1"

    def test_load_resumes_lamport_above_persisted_max(self, tmp_path) -> None:
        cat = MarkerCatalog()
        cat.upsert(1, "A", "#ffffff")  # version 1
        cat.upsert(1, "A2", "#ffffff")  # version 2
        path = str(tmp_path / "markers.toml")
        save_catalog(cat, path)
        loaded = load_catalog(path)
        # A post-restart edit must keep climbing, not restart at 1.
        assert loaded.upsert(1, "A3", "#ffffff").version == 3

    def test_load_back_compat_missing_version_and_origin(self, tmp_path) -> None:
        path = tmp_path / "markers.toml"
        path.write_text(
            '[[marker]]\nid = 1\nname = "Old"\ncolor = "#ffffff"\nupdated_at = 5.0\ntombstone = false\n',
            encoding="utf-8",
        )
        cat = load_catalog(str(path))
        entry = cat.get(1)
        assert entry is not None
        assert entry.version == 0
        assert entry.origin == ""

    def test_load_sanitises_origin_and_caps_version(self, tmp_path) -> None:
        # A hand-edited markers.toml must go through the same origin sanitise +
        # version cap as the wire path, not load verbatim.
        path = tmp_path / "markers.toml"
        path.write_text(
            '[[marker]]\nid = 1\nname = "X"\ncolor = "#ffffff"\nupdated_at = 1.0\n'
            'origin = "ok\\u0007bad"\nversion = 99999999999999999999\n',
            encoding="utf-8",
        )
        cat = load_catalog(str(path))
        entry = cat.get_any(1)
        assert entry is not None
        assert entry.origin == "okbad"  # BEL stripped
        assert entry.version == 2**63 - 1  # clamped to the 64-bit cap
