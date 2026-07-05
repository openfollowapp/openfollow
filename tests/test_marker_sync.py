# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the multicast catalog sync service.

Loops over the network are exercised hermetically by feeding bytes
through ``_parse_beacon`` / ``_build_beacon`` directly. Full socket
plumbing has its own integration coverage via the live LAN test.
"""

from __future__ import annotations

import errno
import json
import socket as _socket
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

import openfollow.marker_catalog.sync as marker_sync
from openfollow.marker_catalog.catalog import MarkerCatalog, MarkerEntry
from openfollow.marker_catalog.sync import (
    CATALOG_MCAST_GROUP,
    CATALOG_PORT,
    DELTA_DEBOUNCE,
    HEARTBEAT_INTERVAL,
    MarkerCatalogSync,
    PeerSelection,
    _build_beacon,
    _coerce_id_list,
    _entry_from_dict,
    _parse_beacon,
)

pytestmark = pytest.mark.unit


def _make_sync(catalog: MarkerCatalog | None = None, **kwargs):
    return MarkerCatalogSync(
        catalog if catalog is not None else MarkerCatalog(),
        station_id="station-A",
        station_name_provider=kwargs.get("station_name_provider", lambda: "OpenFollow X"),
        selection_provider=kwargs.get("selection_provider", lambda: ([], [])),
        on_change=kwargs.get("on_change"),
    )


class TestConstants:
    def test_port_is_50506(self) -> None:
        assert CATALOG_PORT == 50506

    def test_group_matches_discovery(self) -> None:
        # Same multicast group as web.discovery; we pick a different port
        # to keep the protocols cleanly separable.
        assert CATALOG_MCAST_GROUP == "239.255.50.50"

    def test_heartbeat_interval_is_five_seconds(self) -> None:
        assert HEARTBEAT_INTERVAL == 5.0

    def test_delta_debounce_is_three_hundred_ms(self) -> None:
        assert DELTA_DEBOUNCE == 0.3


class TestBeaconWireFormat:
    def test_roundtrip(self) -> None:
        entries = [
            MarkerEntry(id=1, name="Spot", color="#ff0000", updated_at=1.0),
            MarkerEntry(id=2, name="Lead", color="#00ff00", updated_at=2.0, tombstone=True),
        ]
        data = _build_beacon(
            kind="heartbeat",
            station_id="abc",
            station_name="OpenFollow bright-fox",
            controlled_ids=[1],
            viewer_ids=[1, 2],
            entries=entries,
        )
        parsed = _parse_beacon(data)
        assert parsed is not None
        assert parsed["type"] == "openfollow-markers"
        assert parsed["station_id"] == "abc"
        assert parsed["station_name"] == "OpenFollow bright-fox"
        assert parsed["controlled_ids"] == [1]
        assert parsed["viewer_ids"] == [1, 2]
        raw_entries = parsed["entries"]
        assert isinstance(raw_entries, list)
        e1 = _entry_from_dict(raw_entries[0])
        e2 = _entry_from_dict(raw_entries[1])
        assert e1 is not None
        assert e2 is not None
        assert e1.id == 1 and not e1.tombstone
        assert e2.id == 2 and e2.tombstone

    def test_rejects_non_json(self) -> None:
        assert _parse_beacon(b"\xff\xfe not json") is None

    def test_rejects_non_dict_top_level(self) -> None:
        assert _parse_beacon(json.dumps([1, 2]).encode("utf-8")) is None

    def test_rejects_unknown_type(self) -> None:
        payload = {"type": "openfollow", "station_id": "x"}
        assert _parse_beacon(json.dumps(payload).encode("utf-8")) is None

    def test_rejects_oversized_packet(self) -> None:
        data = b"x" * (60 * 1024 + 1)
        assert _parse_beacon(data) is None


class TestIdCoercion:
    def test_drops_bools_and_negatives_and_zero(self) -> None:
        assert _coerce_id_list([1, 2, 0, -3, True, False, "x"]) == [1, 2]

    def test_non_list_returns_empty(self) -> None:
        assert _coerce_id_list("not a list") == []


class TestRequestDelta:
    def test_queues_ids(self) -> None:
        sync = _make_sync()
        sync.request_delta([3, 7])
        assert sync._pending_delta_ids == {3, 7}
        assert sync._delta_due_at is not None

    def test_drops_invalid_ids(self) -> None:
        sync = _make_sync()
        sync.request_delta([0, -1, 5])
        assert sync._pending_delta_ids == {5}

    def test_empty_list_is_noop(self) -> None:
        sync = _make_sync()
        sync.request_delta([])
        assert sync._pending_delta_ids == set()
        assert sync._delta_due_at is None

    def test_consume_pending_drains(self) -> None:
        sync = _make_sync()
        sync.request_delta([3, 7])
        drained = sync._consume_pending_ids()
        assert drained == {3, 7}
        # Subsequent consume sees an empty set.
        assert sync._consume_pending_ids() == set()


class TestPacketHandling:
    def test_self_packets_are_ignored(self) -> None:
        cat = MarkerCatalog()
        sync = _make_sync(cat)
        data = _build_beacon(
            kind="heartbeat",
            station_id="station-A",  # same as sync's station_id
            station_name="X",
            controlled_ids=[],
            viewer_ids=[],
            entries=[MarkerEntry(id=1, name="Spot", color="#ff0000", updated_at=1.0)],
        )
        sync._handle_packet(data)
        # Catalog stayed empty; peer list stayed empty.
        assert len(cat) == 0
        assert sync.get_peer_selections() == []

    def test_remote_packet_merges_and_records_peer(self) -> None:
        cat = MarkerCatalog()
        changes: list[list[int]] = []
        sync = _make_sync(cat, on_change=changes.append)
        data = _build_beacon(
            kind="heartbeat",
            station_id="station-B",
            station_name="OpenFollow bright-fox",
            controlled_ids=[1],
            viewer_ids=[1, 2],
            entries=[
                MarkerEntry(id=1, name="Spot", color="#ff0000", updated_at=10.0),
            ],
        )
        sync._handle_packet(data)
        assert cat.get(1) is not None
        assert cat.get(1).name == "Spot"
        peers = sync.get_peer_selections()
        assert len(peers) == 1
        assert peers[0].station_id == "station-B"
        assert peers[0].station_name == "OpenFollow bright-fox"
        assert peers[0].controlled_ids == [1]
        assert peers[0].viewer_ids == [1, 2]
        assert changes == [[1]]

    def test_no_change_callback_when_merge_returns_false(self) -> None:
        cat = MarkerCatalog()
        cat.upsert(1, "Newer", "#00ff00", updated_at=100.0)
        changes: list[list[int]] = []
        sync = _make_sync(cat, on_change=changes.append)
        data = _build_beacon(
            kind="heartbeat",
            station_id="station-B",
            station_name="Y",
            controlled_ids=[],
            viewer_ids=[],
            entries=[
                MarkerEntry(id=1, name="Older", color="#ff0000", updated_at=1.0),
            ],
        )
        sync._handle_packet(data)
        # Peer entry was still recorded for UI, but on_change wasn't invoked.
        assert sync.get_peer_selections()[0].station_id == "station-B"
        assert changes == []

    def test_malformed_packet_silently_ignored(self) -> None:
        sync = _make_sync()
        sync._handle_packet(b"junk")  # must not raise


class TestPeerTableHardening:
    """#542 – bound the untrusted-keyed peer-selection table + sanitize text."""

    @staticmethod
    def _beacon(station_id: str, station_name: str = "X", entries=None) -> bytes:
        return _build_beacon(
            kind="heartbeat",
            station_id=station_id,
            station_name=station_name,
            controlled_ids=[],
            viewer_ids=[],
            entries=entries or [],
        )

    def test_station_id_length_is_capped(self) -> None:
        sync = _make_sync()
        sync._handle_packet(self._beacon("z" * 300))
        peers = sync.get_peer_selections()
        assert len(peers) == 1
        assert peers[0].station_id == "z" * marker_sync._STATION_ID_MAX_LEN

    def test_station_id_control_chars_stripped(self) -> None:
        sync = _make_sync()
        sync._handle_packet(self._beacon("ok\x07id\x1b"))
        assert sync.get_peer_selections()[0].station_id == "okid"

    def test_station_id_all_control_chars_is_rejected(self) -> None:
        cat = MarkerCatalog()
        sync = _make_sync(cat)
        entry = MarkerEntry(id=1, name="Spot", color="#ff0000", updated_at=1.0)
        sync._handle_packet(self._beacon("\x00\x07\x1b", entries=[entry]))
        # Empty after sanitizing → no peer recorded and no catalog merge.
        assert sync.get_peer_selections() == []
        assert len(cat) == 0

    def test_station_name_sanitized_and_capped(self) -> None:
        sync = _make_sync()
        sync._handle_packet(self._beacon("station-B", station_name="bad\x1bname" + "y" * 300))
        name = sync.get_peer_selections()[0].station_name
        assert "\x1b" not in name
        assert name.startswith("badname")
        assert len(name) == marker_sync._STATION_NAME_MAX_LEN

    def test_non_string_station_name_becomes_empty(self) -> None:
        sync = _make_sync()
        raw = json.dumps(
            {
                "type": "openfollow-markers",
                "kind": "heartbeat",
                "station_id": "station-B",
                "station_name": 123,  # non-string from a crafted peer
                "controlled_ids": [],
                "viewer_ids": [],
                "entries": [],
            }
        ).encode("utf-8")
        sync._handle_packet(raw)
        assert sync.get_peer_selections()[0].station_name == ""

    def test_peer_table_is_capped_under_flood(self) -> None:
        sync = _make_sync()
        for i in range(marker_sync.MAX_PEER_SELECTIONS):
            sync._handle_packet(self._beacon(f"s-{i}"))
        assert len(sync.get_peer_selections()) == marker_sync.MAX_PEER_SELECTIONS
        # Further distinct stations are refused; repeated refusals also exercise
        # the throttled cap-log path.
        for i in range(3):
            sync._handle_packet(self._beacon(f"flood-{i}"))
        peers = sync.get_peer_selections()
        assert len(peers) == marker_sync.MAX_PEER_SELECTIONS
        assert all(not p.station_id.startswith("flood-") for p in peers)

    def test_cap_prunes_stale_before_refusing(self) -> None:
        sync = _make_sync()
        old = time.time() - (marker_sync.PEER_TIMEOUT + 100)
        with sync._peer_lock:
            for i in range(marker_sync.MAX_PEER_SELECTIONS):
                sync._peer_selections[f"stale-{i}"] = PeerSelection(
                    station_id=f"stale-{i}", station_name="", last_seen=old
                )
        sync._handle_packet(self._beacon("fresh"))
        assert [p.station_id for p in sync.get_peer_selections()] == ["fresh"]

    def test_entries_still_merge_when_peer_refused(self) -> None:
        cat = MarkerCatalog()
        sync = _make_sync(cat)
        for i in range(marker_sync.MAX_PEER_SELECTIONS):
            sync._handle_packet(self._beacon(f"s-{i}"))
        # Table full of live peers: the new station is refused for UI display,
        # but its catalog entries still merge (the two concerns are decoupled).
        entry = MarkerEntry(id=7, name="Spot7", color="#abcdef", updated_at=5.0)
        sync._handle_packet(self._beacon("overflow", entries=[entry]))
        assert cat.get(7) is not None
        assert all(p.station_id != "overflow" for p in sync.get_peer_selections())

    def test_first_cap_warning_fires_at_low_monotonic(self, monkeypatch, caplog) -> None:
        # #606 review: _peer_cap_log_ts starts at -inf so the first cap warning
        # fires even when time.monotonic() starts below _PEER_CAP_LOG_INTERVAL at
        # boot (a 0.0 init would suppress it for ~30s during a startup flood).
        import logging

        sync = _make_sync()
        monkeypatch.setattr(marker_sync.time, "monotonic", lambda: 5.0)
        with caplog.at_level(logging.WARNING, logger="openfollow.marker_catalog.sync"):
            sync._note_peer_cap()
        assert any("peer-selection table full" in r.message for r in caplog.records)


class TestEntryFromDict:
    def test_tombstone_must_be_real_bool(self) -> None:
        """``bool("false")`` is ``True`` in Python, so accepting any
        truthy value would let a malformed/hostile peer smuggle in a
        delete via ``tombstone: "false"``. Defense: require an actual
        JSON boolean and default to False otherwise."""
        entry = _entry_from_dict(
            {
                "id": 5,
                "name": "X",
                "color": "#ff0000",
                "updated_at": 1.0,
                "tombstone": "false",  # non-bool – must not be coerced to True
            }
        )
        assert entry is not None
        assert entry.tombstone is False

    def test_tombstone_int_one_is_not_treated_as_true(self) -> None:
        entry = _entry_from_dict(
            {
                "id": 5,
                "name": "X",
                "color": "#ff0000",
                "updated_at": 1.0,
                "tombstone": 1,
            }
        )
        assert entry is not None
        assert entry.tombstone is False

    def test_real_bool_true_is_accepted(self) -> None:
        entry = _entry_from_dict(
            {
                "id": 5,
                "name": "X",
                "color": "#ff0000",
                "updated_at": 1.0,
                "tombstone": True,
            }
        )
        assert entry is not None
        assert entry.tombstone is True

    def test_non_dict_returns_none(self) -> None:
        assert _entry_from_dict("not a dict") is None  # type: ignore[arg-type]

    def test_value_error_returns_none(self) -> None:
        # id="abc" raises ValueError inside int() – _entry_from_dict
        # catches it and returns None.
        assert _entry_from_dict({"id": "abc"}) is None

    def test_bool_id_rejected(self) -> None:
        """``bool`` is an ``int`` subclass – ``int(True)`` is ``1``. A
        malformed or hostile peer beacon carrying ``"id": true`` would
        otherwise deserialise as ``id=1`` and overwrite the real
        marker 1 entry on LWW merge. Same trap class as
        ``load_catalog``'s id coercion guards."""
        assert (
            _entry_from_dict(
                {
                    "id": True,
                    "name": "x",
                    "color": "#ff0000",
                    "updated_at": 1.0,
                }
            )
            is None
        )
        assert (
            _entry_from_dict(
                {
                    "id": False,
                    "name": "x",
                    "color": "#ff0000",
                    "updated_at": 1.0,
                }
            )
            is None
        )

    def test_non_string_name_normalised_to_empty(self) -> None:
        """``_entry_from_dict`` hands the raw ``name`` through to
        ``MarkerEntry.__post_init__`` rather than ``str()``-coercing
        at the loader boundary, so a malformed peer payload with
        ``name: 123`` lands as ``""`` instead of the surprising
        ``"123"``."""
        entry = _entry_from_dict(
            {
                "id": 5,
                "name": 123,
                "color": "#ff0000",
                "updated_at": 1.0,
            }
        )
        assert entry is not None
        assert entry.name == ""


class TestSendPacket:
    def _sync(self, **kwargs):
        return MarkerCatalogSync(
            MarkerCatalog(),
            station_id="station-A",
            station_name_provider=kwargs.get("station_name_provider", lambda: "OpenFollow X"),
            selection_provider=kwargs.get("selection_provider", lambda: ([1, 2], [3])),
        )

    def test_normal_path_calls_sendto(self) -> None:
        sync = self._sync()
        sock = MagicMock()
        sync._send_packet(sock, kind="heartbeat", entries=[])
        assert sock.sendto.call_count == 1
        data, addr = sock.sendto.call_args[0]
        assert addr == (CATALOG_MCAST_GROUP, CATALOG_PORT)
        decoded = _parse_beacon(data)
        assert decoded is not None
        assert decoded["station_id"] == "station-A"

    def test_station_name_provider_raises_falls_back_to_empty(self) -> None:
        def bad_provider() -> str:
            raise RuntimeError("provider boom")

        sync = self._sync(station_name_provider=bad_provider)
        sock = MagicMock()
        sync._send_packet(sock, kind="heartbeat", entries=[])
        data, _ = sock.sendto.call_args[0]
        decoded = _parse_beacon(data)
        assert decoded is not None
        assert decoded["station_name"] == ""

    def test_selection_provider_raises_falls_back_to_empty(self) -> None:
        def bad_provider():
            raise RuntimeError("provider boom")

        sync = self._sync(selection_provider=bad_provider)
        sock = MagicMock()
        sync._send_packet(sock, kind="heartbeat", entries=[])
        data, _ = sock.sendto.call_args[0]
        decoded = _parse_beacon(data)
        assert decoded is not None
        assert decoded["controlled_ids"] == []
        assert decoded["viewer_ids"] == []

    def test_oversized_payload_falls_back_to_selection_only(self) -> None:
        """An oversized entries payload triggers a selection-only beacon
        (no entries) instead of a byte-truncated invalid-JSON packet
        that every peer would reject."""
        sync = self._sync()
        # Build a synthetic entry set large enough to exceed _MAX_TX_PACKET
        # (60 KiB) on the wire – each entry name is ~600 bytes.
        big_entries = [MarkerEntry(id=i, name="x" * 600, color="#ff0000", updated_at=0.0) for i in range(1, 200)]
        sock = MagicMock()
        sync._send_packet(sock, kind="heartbeat", entries=big_entries)
        assert sock.sendto.call_count == 1
        data, _ = sock.sendto.call_args[0]
        decoded = _parse_beacon(data)
        assert decoded is not None
        # Fallback drops entries but keeps selection / station_id.
        assert decoded["entries"] == []
        assert decoded["station_id"] == "station-A"
        assert decoded["controlled_ids"] == [1, 2]

    def test_oversized_fallback_also_too_large_drops_beacon(self) -> None:
        """Pathological case: even the empty-entries shell is bigger
        than the cap (e.g. astronomically long station name). Drop the
        beacon entirely rather than wire-poison peers with a truncated
        invalid packet."""
        sync = self._sync(station_name_provider=lambda: "x" * 70000)
        sock = MagicMock()
        # Entries don't matter – even the empty shell is oversize.
        sync._send_packet(sock, kind="heartbeat", entries=[])
        assert sock.sendto.call_count == 0


class TestHandleSendError:
    def _sync(self) -> MarkerCatalogSync:
        return MarkerCatalogSync(
            MarkerCatalog(),
            station_id="station-A",
            station_name_provider=lambda: "X",
            selection_provider=lambda: ([], []),
        )

    def test_transient_errno_returns_none_and_closes_sock(self) -> None:
        sync = self._sync()
        sock = MagicMock()
        exc = OSError(errno.ENETDOWN, "network down")
        result = sync._handle_send_error(sock, exc)
        assert result is None
        assert sock.close.call_count == 1

    def test_close_failure_during_transient_is_swallowed(self) -> None:
        sync = self._sync()
        sock = MagicMock()
        sock.close.side_effect = OSError("already closed")
        exc = OSError(errno.ENETDOWN, "network down")
        # Must not raise – _handle_send_error swallows close errors.
        result = sync._handle_send_error(sock, exc)
        assert result is None

    def test_permanent_errno_keeps_sock(self) -> None:
        sync = self._sync()
        sock = MagicMock()
        exc = OSError(errno.EACCES, "denied")
        result = sync._handle_send_error(sock, exc)
        assert result is sock
        assert sock.close.call_count == 0


class TestSendLoopOneIteration:
    """Drive ``_send_loop`` for a single iteration via mocked sockets +
    a pre-fired stop event, so the threaded loop's normal-path / error
    branches stay reachable in unit tests."""

    def _sync(self, **kwargs) -> MarkerCatalogSync:
        return MarkerCatalogSync(
            MarkerCatalog(),
            station_id="station-A",
            station_name_provider=kwargs.get("station_name_provider", lambda: "X"),
            selection_provider=kwargs.get("selection_provider", lambda: ([], [])),
        )

    def test_open_tx_socket_failure_continues_until_stop(self) -> None:
        sync = self._sync()
        # First call: raise. Then set stop.
        call_count = [0]

        def open_tx_or_stop():
            call_count[0] += 1
            sync._stop_event.set()
            raise RuntimeError("simulated open failure")

        with patch.object(sync, "_open_tx_socket", side_effect=open_tx_or_stop):
            sync._send_loop()
        assert call_count[0] == 1

    def test_heartbeat_path_sends_packet_then_stops(self) -> None:
        sync = self._sync()
        sock = MagicMock()
        sent_data: list[bytes] = []

        def capture_sendto(data, _addr):
            sent_data.append(data)
            sync._stop_event.set()

        sock.sendto.side_effect = capture_sendto
        with patch.object(sync, "_open_tx_socket", return_value=sock):
            sync._send_loop()
        assert len(sent_data) == 1
        # Heartbeat covers ``next_heartbeat`` branch + final ``sock.close()``.
        assert sock.close.call_count == 1

    def test_delta_path_sends_filtered_entries(self) -> None:
        cat = MarkerCatalog()
        cat.upsert(1, "Spot 1", "#ff0000", updated_at=1.0)
        cat.upsert(2, "Spot 2", "#00ff00", updated_at=2.0)
        sync = MarkerCatalogSync(
            cat,
            station_id="station-A",
            station_name_provider=lambda: "X",
            selection_provider=lambda: ([], []),
        )
        sync.request_delta([1])
        sync._delta_due_at = time.monotonic() - 1  # force "due"
        sock = MagicMock()

        def capture(data, _addr):
            sync._stop_event.set()

        sock.sendto.side_effect = capture
        with patch.object(sync, "_open_tx_socket", return_value=sock):
            sync._send_loop()
        # Delta + heartbeat both sent in the same iteration.
        assert sock.sendto.call_count >= 1
        first_data = sock.sendto.call_args_list[0][0][0]
        decoded = _parse_beacon(first_data)
        assert decoded is not None
        entries = decoded.get("entries", [])
        ids = [e["id"] for e in entries] if isinstance(entries, list) else []
        # Delta beacon should contain only id=1.
        assert ids == [1] or 1 in ids

    def test_delta_with_no_matching_entries_skips_send(self) -> None:
        """request_delta queued an id that isn't in the catalog – the
        send loop notices the empty filter and skips that send (but
        still does the heartbeat below)."""
        sync = self._sync()
        sync.request_delta([99])
        sync._delta_due_at = time.monotonic() - 1
        sock = MagicMock()

        def capture(data, _addr):
            sync._stop_event.set()

        sock.sendto.side_effect = capture
        with patch.object(sync, "_open_tx_socket", return_value=sock):
            sync._send_loop()
        # Heartbeat fires; delta was a no-op because no matching entries.
        assert sock.sendto.call_count == 1

    def test_delta_send_error_resets_socket(self) -> None:
        """Delta send raises OSError on a transient errno; the loop
        rebuilds the socket on the next iteration. Distinct from the
        heartbeat-error path above – both arms have to be reached."""
        cat = MarkerCatalog()
        cat.upsert(1, "Spot", "#ff0000", updated_at=1.0)
        sync = MarkerCatalogSync(
            cat,
            station_id="station-A",
            station_name_provider=lambda: "X",
            selection_provider=lambda: ([], []),
        )
        sync.request_delta([1])
        sync._delta_due_at = time.monotonic() - 1

        sock1 = MagicMock()
        sock2 = MagicMock()
        send_calls = [0]

        def first_sendto(_data, _addr):
            send_calls[0] += 1
            # First send is the delta – fail transiently.
            raise OSError(errno.ENETDOWN, "down")

        def second_sendto(_data, _addr):
            sync._stop_event.set()

        sock1.sendto.side_effect = first_sendto
        sock2.sendto.side_effect = second_sendto
        sockets = iter([sock1, sock2])
        # Zero the post-error backoff so the loop reaches the rebuilt
        # socket immediately instead of sleeping the real interval.
        with (
            patch.object(marker_sync, "_SEND_ERROR_BACKOFF", 0.0),
            patch.object(sync, "_open_tx_socket", side_effect=lambda: next(sockets)),
        ):
            sync._send_loop()
        assert sock1.sendto.call_count == 1
        assert sock2.sendto.call_count == 1

    def test_close_on_exit_swallows_oserror(self) -> None:
        sync = self._sync()
        sock = MagicMock()
        sock.close.side_effect = OSError("close boom")

        def stop_immediately(_data, _addr):
            sync._stop_event.set()

        sock.sendto.side_effect = stop_immediately
        with patch.object(sync, "_open_tx_socket", return_value=sock):
            # Must not propagate the close error.
            sync._send_loop()
        sock.close.assert_called_once()

    def test_open_tx_socket_no_iface_skips_multicast_if(self) -> None:
        """With no ``iface_ip`` configured, the ``IP_MULTICAST_IF``
        ``setsockopt`` block is skipped – exercising the False arm of
        the ``if self._iface_ip:`` branch."""
        sync = MarkerCatalogSync(
            MarkerCatalog(),
            station_id="station-A",
            station_name_provider=lambda: "X",
            selection_provider=lambda: ([], []),
            iface_ip="",
        )
        sock = MagicMock()
        with patch.object(_socket, "socket", return_value=sock):
            result = sync._open_tx_socket()
        assert result is sock
        # Only IP_MULTICAST_TTL was set; IP_MULTICAST_IF was not.
        opts_called = [c.args[1] for c in sock.setsockopt.call_args_list]
        assert _socket.IP_MULTICAST_TTL in opts_called
        assert _socket.IP_MULTICAST_IF not in opts_called

    def test_two_iterations_reuses_sock_and_skips_heartbeat(self) -> None:
        """Drive two iterations: the first opens a socket + fires the
        heartbeat (sets ``next_heartbeat`` 5 s in the future), and the
        second reuses the sock + skips the heartbeat (``now <
        next_heartbeat``). Covers ``248->256`` and ``275->285`` branches."""
        sync = self._sync()
        sock = MagicMock()
        iteration_count = [0]
        original_wait = sync._stop_event.wait

        def short_wait(timeout):
            iteration_count[0] += 1
            if iteration_count[0] >= 2:
                sync._stop_event.set()
            # Tiny wait so we don't actually sleep 5 s in the test.
            return original_wait(0.001)

        sync._stop_event.wait = short_wait  # type: ignore[method-assign]
        open_count = [0]

        def open_once():
            open_count[0] += 1
            return sock

        with patch.object(sync, "_open_tx_socket", side_effect=open_once):
            sync._send_loop()
        # _open_tx_socket called only once across both iterations.
        assert open_count[0] == 1
        # Heartbeat fired exactly once (second iteration skipped).
        assert sock.sendto.call_count == 1

    def test_delta_consume_drains_to_empty_skips_send(self) -> None:
        sync = self._sync()
        sync._delta_due_at = time.monotonic() - 1  # force "due"
        sync._pending_delta_ids = set()  # already drained
        sock = MagicMock()

        def capture(_data, _addr):
            sync._stop_event.set()

        sock.sendto.side_effect = capture
        with patch.object(sync, "_open_tx_socket", return_value=sock):
            sync._send_loop()
        # Only the heartbeat fired; the delta path was skipped because
        # consume returned an empty set.
        assert sock.sendto.call_count == 1

    def test_delta_with_pending_keeps_wake_short(self) -> None:
        sync = self._sync()
        # Queue a delta but place its due-time in the future, so it's
        # NOT due this iteration; the wait-until min() branch fires.
        sync._pending_delta_ids = {1}
        sync._delta_due_at = time.monotonic() + 0.01
        sock = MagicMock()

        def capture(_data, _addr):
            sync._stop_event.set()

        sock.sendto.side_effect = capture
        with patch.object(sync, "_open_tx_socket", return_value=sock):
            sync._send_loop()
        # Heartbeat fired and the loop exited cleanly.
        assert sock.sendto.call_count == 1

    def test_send_error_resets_socket(self) -> None:
        sync = self._sync()
        sock1 = MagicMock()
        sock2 = MagicMock()
        # First sendto raises a transient error → handler returns None.
        sock1.sendto.side_effect = OSError(errno.ENETDOWN, "down")

        def capture(data, _addr):
            sync._stop_event.set()

        sock2.sendto.side_effect = capture
        sockets = iter([sock1, sock2])
        with (
            patch.object(marker_sync, "_SEND_ERROR_BACKOFF", 0.0),
            patch.object(sync, "_open_tx_socket", side_effect=lambda: next(sockets)),
        ):
            sync._send_loop()
        # Both sockets used: first failed, second sent successfully.
        assert sock1.sendto.call_count == 1
        assert sock2.sendto.call_count == 1

    def test_persistent_send_error_backs_off_instead_of_spinning(self) -> None:
        """Regression: a send that fails on every attempt (dead/stale
        iface, unreachable net) must NOT busy-loop. Before the fix the
        heartbeat error path ``continue``d without advancing
        ``next_heartbeat``, so the loop re-opened the socket and retried
        as fast as the CPU allowed – hundreds of cycles/sec, flooding
        journald and starving the box (RTSP decode missed its deadline).
        Now each persistent failure backs off by ``_SEND_ERROR_BACKOFF``."""
        sync = self._sync()
        sock = MagicMock()

        waits: list[float | None] = []
        real_event = sync._stop_event

        def fake_wait(timeout: float | None = None) -> bool:
            waits.append(timeout)
            # Stop after the first backoff so the loop can't run forever
            # (and so a regression that never calls wait would instead
            # trip the sendto safety-stop below and fail the count check).
            real_event.set()
            return True

        send_calls = [0]

        def guarded_sendto(_data, _addr):  # noqa: ANN001
            send_calls[0] += 1
            if send_calls[0] > 20:  # safety: never hang on regression
                real_event.set()
                return None
            raise OSError(errno.ENETUNREACH, "unreachable")

        sock.sendto.side_effect = guarded_sendto

        with (
            patch.object(sync, "_open_tx_socket", return_value=sock),
            patch.object(sync._stop_event, "wait", side_effect=fake_wait),
        ):
            sync._send_loop()

        # Exactly one send attempt before the loop backed off – not a
        # tight retry storm.
        assert sock.sendto.call_count == 1
        # The backoff used the dedicated interval.
        assert marker_sync._SEND_ERROR_BACKOFF in waits


class TestRecvLoopOneIteration:
    """Drive ``_recv_loop`` for a single iteration with mocked sockets."""

    def _sync(self) -> MarkerCatalogSync:
        return MarkerCatalogSync(
            MarkerCatalog(),
            station_id="station-A",
            station_name_provider=lambda: "X",
            selection_provider=lambda: ([], []),
        )

    def test_bind_failure_returns_immediately(self) -> None:
        sync = self._sync()
        bad_sock = MagicMock()
        bad_sock.bind.side_effect = OSError("port busy")
        with patch.object(_socket, "socket", return_value=bad_sock):
            sync._recv_loop()
        bad_sock.close.assert_called_once()

    def test_reuseport_attribute_error_swallowed(self) -> None:
        sync = self._sync()
        sock = MagicMock()
        # Make setsockopt fail with AttributeError on the SO_REUSEPORT call only.

        def setsockopt(level, opt, val):
            if opt == _socket.SO_REUSEPORT:
                raise AttributeError("not available")

        sock.setsockopt.side_effect = setsockopt
        # bind succeeds (default MagicMock); set stop so recv loop exits.
        sync._stop_event.set()
        with patch.object(_socket, "socket", return_value=sock):
            sync._recv_loop()
        sock.close.assert_called_once()

    def test_fallback_join_on_iface_failure(self) -> None:
        """When IP_ADD_MEMBERSHIP fails on the bound iface (e.g. iface
        IP not present), the loop retries with the wildcard. Both
        failing also short-circuits the loop and closes the socket."""
        sync = MarkerCatalogSync(
            MarkerCatalog(),
            station_id="station-A",
            station_name_provider=lambda: "X",
            selection_provider=lambda: ([], []),
            iface_ip="10.0.0.5",
        )
        sock = MagicMock()
        membership_calls: list[bytes] = []

        def setsockopt(level, opt, val):
            if opt == _socket.IP_ADD_MEMBERSHIP:
                membership_calls.append(val)
                raise OSError("iface gone")

        sock.setsockopt.side_effect = setsockopt
        with patch.object(_socket, "socket", return_value=sock):
            sync._recv_loop()
        # Two IP_ADD_MEMBERSHIP attempts (iface, then 0.0.0.0 fallback).
        assert len(membership_calls) == 2
        sock.close.assert_called_once()

    def test_recv_timeout_continues_until_stop(self) -> None:
        sync = self._sync()
        sock = MagicMock()
        recv_count = [0]

        def recv_or_stop(_size):
            recv_count[0] += 1
            if recv_count[0] == 1:
                raise TimeoutError()
            if recv_count[0] == 2:
                sync._stop_event.set()
                raise OSError("misc")
            return (b"junk", ("127.0.0.1", 12345))  # pragma: no cover

        sock.recvfrom.side_effect = recv_or_stop
        with patch.object(_socket, "socket", return_value=sock):
            sync._recv_loop()
        sock.close.assert_called_once()

    def test_received_packet_dispatches_to_handle(self) -> None:
        sync = self._sync()
        sock = MagicMock()
        data = _build_beacon(
            kind="heartbeat",
            station_id="station-B",
            station_name="Peer",
            controlled_ids=[1],
            viewer_ids=[],
            entries=[],
        )
        calls = [0]

        def recv_or_stop(_size):
            calls[0] += 1
            if calls[0] == 1:
                return (data, ("239.255.50.50", 50506))
            sync._stop_event.set()
            raise TimeoutError()

        sock.recvfrom.side_effect = recv_or_stop
        with patch.object(_socket, "socket", return_value=sock):
            sync._recv_loop()
        # The peer beacon registered in the peer table.
        peers = sync.get_peer_selections()
        assert any(p.station_id == "station-B" for p in peers)


class TestHandlePacketEdges:
    def test_missing_station_id_silently_ignored(self) -> None:
        sync = MarkerCatalogSync(
            MarkerCatalog(),
            station_id="station-A",
            station_name_provider=lambda: "X",
            selection_provider=lambda: ([], []),
        )
        data = json.dumps({"type": "openfollow-markers"}).encode("utf-8")
        sync._handle_packet(data)
        assert sync.get_peer_selections() == []

    def test_non_string_station_name_coerced_empty(self) -> None:
        sync = MarkerCatalogSync(
            MarkerCatalog(),
            station_id="station-A",
            station_name_provider=lambda: "X",
            selection_provider=lambda: ([], []),
        )
        # Hand-craft a beacon with a non-string station_name.
        payload = {
            "type": "openfollow-markers",
            "station_id": "station-B",
            "station_name": 42,  # not a string
            "controlled_ids": [],
            "viewer_ids": [],
            "entries": [],
        }
        sync._handle_packet(json.dumps(payload).encode("utf-8"))
        peers = sync.get_peer_selections()
        assert len(peers) == 1
        assert peers[0].station_name == ""

    def test_entries_not_list_recorded_peer_but_no_merge(self) -> None:
        cat = MarkerCatalog()
        sync = MarkerCatalogSync(
            cat,
            station_id="station-A",
            station_name_provider=lambda: "X",
            selection_provider=lambda: ([], []),
        )
        payload = {
            "type": "openfollow-markers",
            "station_id": "station-B",
            "station_name": "Peer",
            "controlled_ids": [],
            "viewer_ids": [],
            "entries": "not a list",  # malformed
        }
        sync._handle_packet(json.dumps(payload).encode("utf-8"))
        peers = sync.get_peer_selections()
        assert len(peers) == 1
        assert len(cat) == 0

    def test_malformed_entry_in_list_skipped(self) -> None:
        cat = MarkerCatalog()
        sync = MarkerCatalogSync(
            cat,
            station_id="station-A",
            station_name_provider=lambda: "X",
            selection_provider=lambda: ([], []),
        )
        payload = {
            "type": "openfollow-markers",
            "station_id": "station-B",
            "station_name": "Peer",
            "controlled_ids": [],
            "viewer_ids": [],
            # Mix of valid + invalid: the string entry returns None
            # from _entry_from_dict, the dict entry merges.
            "entries": [
                "not-a-dict",
                {
                    "id": 5,
                    "name": "X",
                    "color": "#ff0000",
                    "updated_at": 1.0,
                    "tombstone": False,
                },
            ],
        }
        sync._handle_packet(json.dumps(payload).encode("utf-8"))
        assert cat.get(5) is not None

    def test_on_change_callback_exception_swallowed(self) -> None:
        cat = MarkerCatalog()

        def boom(_ids: list[int]) -> None:
            raise RuntimeError("callback boom")

        sync = MarkerCatalogSync(
            cat,
            station_id="station-A",
            station_name_provider=lambda: "X",
            selection_provider=lambda: ([], []),
            on_change=boom,
        )
        data = _build_beacon(
            kind="heartbeat",
            station_id="station-B",
            station_name="Peer",
            controlled_ids=[],
            viewer_ids=[],
            entries=[MarkerEntry(id=1, name="X", color="#ff0000", updated_at=1.0)],
        )
        # Must not propagate the callback error.
        sync._handle_packet(data)
        assert cat.get(1) is not None


class TestStartStop:
    def test_double_start_is_idempotent(self) -> None:
        sync = MarkerCatalogSync(
            MarkerCatalog(),
            station_id="station-A",
            station_name_provider=lambda: "X",
            selection_provider=lambda: ([], []),
        )
        # Pre-set a sentinel thread so start() short-circuits.
        sync._send_thread = threading.Thread(target=lambda: None)
        sync.start()  # must not start a new thread
        # Clean up
        sync._send_thread = None

    def test_start_then_stop_launches_and_joins_threads(self) -> None:
        """Drive the full start/stop lifecycle with a fast stop_event so
        the daemon threads exit promptly. Covers ``start()``'s thread
        creation and ``stop()``'s join branches."""
        sync = MarkerCatalogSync(
            MarkerCatalog(),
            station_id="station-A",
            station_name_provider=lambda: "X",
            selection_provider=lambda: ([], []),
        )

        # Stub out the actual socket work so the threads exit fast.
        def quick_send_loop() -> None:
            sync._stop_event.wait(0.01)

        def quick_recv_loop() -> None:
            sync._stop_event.wait(0.01)

        with (
            patch.object(sync, "_send_loop", side_effect=quick_send_loop),
            patch.object(sync, "_recv_loop", side_effect=quick_recv_loop),
        ):
            sync.start()
            assert sync._send_thread is not None
            assert sync._recv_thread is not None
            sync.stop()
        assert sync._send_thread is None
        assert sync._recv_thread is None

    def test_stop_without_start_is_safe(self) -> None:
        sync = MarkerCatalogSync(
            MarkerCatalog(),
            station_id="station-A",
            station_name_provider=lambda: "X",
            selection_provider=lambda: ([], []),
        )
        # Must not raise – neither thread was ever started.
        sync.stop()


class TestOpenTxSocket:
    def test_iface_ip_failure_logs_and_still_returns_socket(self) -> None:
        sync = MarkerCatalogSync(
            MarkerCatalog(),
            station_id="station-A",
            station_name_provider=lambda: "X",
            selection_provider=lambda: ([], []),
            iface_ip="10.0.0.5",
        )
        sock = MagicMock()

        def setsockopt(level, opt, _val):
            if opt == _socket.IP_MULTICAST_IF:
                raise OSError("iface gone")

        sock.setsockopt.side_effect = setsockopt
        with patch.object(_socket, "socket", return_value=sock):
            result = sync._open_tx_socket()
        # Open returns the socket even though IP_MULTICAST_IF failed –
        # send loop falls back to all-interfaces routing.
        assert result is sock


class TestPeerExpiry:
    def test_stale_peers_are_dropped(self) -> None:
        sync = _make_sync()
        sync._peer_selections["x"] = PeerSelection(
            station_id="x",
            station_name="Stale",
            controlled_ids=[],
            viewer_ids=[],
            last_seen=time.time() - 1000,
        )
        sync._peer_selections["y"] = PeerSelection(
            station_id="y",
            station_name="Fresh",
            controlled_ids=[],
            viewer_ids=[],
            last_seen=time.time(),
        )
        peers = sync.get_peer_selections()
        ids = [p.station_id for p in peers]
        assert "y" in ids
        assert "x" not in ids


class TestEntryFromDictIdStrictness:
    """_entry_from_dict must require a real int id from peers – coercing
    str/float ids lets a malformed payload collide with a valid marker."""

    def test_string_id_rejected(self) -> None:
        from openfollow.marker_catalog.sync import _entry_from_dict

        assert _entry_from_dict({"id": "5", "name": "x", "updated_at": 1.0}) is None

    def test_float_id_rejected(self) -> None:
        from openfollow.marker_catalog.sync import _entry_from_dict

        assert _entry_from_dict({"id": 5.9, "name": "x", "updated_at": 1.0}) is None

    def test_bool_id_rejected(self) -> None:
        from openfollow.marker_catalog.sync import _entry_from_dict

        assert _entry_from_dict({"id": True, "name": "x", "updated_at": 1.0}) is None

    def test_real_int_id_accepted(self) -> None:
        from openfollow.marker_catalog.sync import _entry_from_dict

        entry = _entry_from_dict({"id": 5, "name": "x", "color": "#ff0000", "updated_at": 1.0})
        assert entry is not None and entry.id == 5


def test_entry_from_dict_non_numeric_updated_at_returns_none() -> None:
    """A valid int id passes the strict guard, but a non-numeric
    updated_at makes float() raise → caught → None (construction except)."""
    from openfollow.marker_catalog.sync import _entry_from_dict

    assert _entry_from_dict({"id": 5, "updated_at": "soon"}) is None


# --------------------------------------------------------------------------- #
# Logical-clock fields on the wire (version / origin)
# --------------------------------------------------------------------------- #


def test_entry_dict_round_trips_version_and_origin() -> None:
    from openfollow.marker_catalog.sync import _entry_from_dict, _entry_to_dict

    entry = MarkerEntry(id=1, name="A", color="#ffffff", updated_at=2.0, version=7, origin="st-9")
    raw = _entry_to_dict(entry)
    assert raw["version"] == 7
    assert raw["origin"] == "st-9"
    back = _entry_from_dict(raw)
    assert back is not None
    assert back.version == 7
    assert back.origin == "st-9"


def test_entry_from_dict_defaults_missing_version_and_origin() -> None:
    """An old-format peer omits version/origin -> 0 / "" (sorts below any edit)."""
    from openfollow.marker_catalog.sync import _entry_from_dict

    back = _entry_from_dict({"id": 1, "name": "A", "color": "#ffffff", "updated_at": 1.0})
    assert back is not None
    assert back.version == 0
    assert back.origin == ""


@pytest.mark.parametrize("bad", [True, False, "5", 1.5, None, [1]])
def test_entry_from_dict_bad_version_coerced_to_zero(bad: object) -> None:
    from openfollow.marker_catalog.sync import _entry_from_dict

    back = _entry_from_dict({"id": 1, "name": "A", "color": "#ffffff", "version": bad})
    assert back is not None
    assert back.version == 0


def test_entry_from_dict_sanitizes_origin() -> None:
    from openfollow.marker_catalog.sync import _entry_from_dict

    back = _entry_from_dict({"id": 1, "name": "A", "color": "#ffffff", "origin": "st\x00\n\x1bX"})
    assert back is not None
    # Control chars (NUL, LF, ESC) dropped; printable remainder kept.
    assert back.origin == "stX"


def test_entry_from_dict_caps_huge_version() -> None:
    """An unbounded version from an untrusted peer is clamped to the 64-bit cap
    so it can't poison the clock or produce spec-violating markers.toml."""
    from openfollow.marker_catalog.sync import _entry_from_dict

    back = _entry_from_dict({"id": 1, "name": "A", "color": "#ffffff", "version": 10**30})
    assert back is not None
    assert back.version == 2**63 - 1
