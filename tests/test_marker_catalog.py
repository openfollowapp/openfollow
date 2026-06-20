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
        cat.upsert(1, "Old", "#ff0000", updated_at=1.0)
        applied = cat.merge_entry(
            MarkerEntry(id=1, name="New", color="#00ff00", updated_at=2.0),
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
        cat.upsert(1, "Live", "#ff0000", updated_at=1.0)
        tomb = MarkerEntry(
            id=1,
            name="Live",
            color="#ff0000",
            updated_at=2.0,
            tombstone=True,
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
