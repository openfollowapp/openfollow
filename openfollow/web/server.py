# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Threaded web server for the configuration UI with peer discovery."""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from collections.abc import Callable
from socketserver import ThreadingMixIn
from typing import TYPE_CHECKING, Any
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer

if TYPE_CHECKING:
    from openfollow.logging_setup import RingBufferLogHandler
    from openfollow.network.adapter import ApplyResult

from bottle import TEMPLATE_PATH, Bottle

import openfollow
from openfollow.net_utils import get_local_ipv4_addresses, get_primary_local_ipv4
from openfollow.services import WebCommandQueue
from openfollow.web.discovery import BeaconReceiver, BeaconSender, PeerInfo

logger = logging.getLogger(__name__)

# Add templates directory to Bottle's search path.
_WEB_DIR = os.path.dirname(__file__)
TEMPLATE_PATH.insert(0, os.path.join(_WEB_DIR, "templates"))


_REQUEST_MAX_CONCURRENT = 32  # semaphore cap for concurrent request handling
_request_semaphore = threading.BoundedSemaphore(_REQUEST_MAX_CONCURRENT)

# Cumulative 503 count from the concurrency cap. The semaphore is module-level
# (shared across every server instance), so its rejection counter is too. Guarded
# by a lock since ``process_request_thread`` runs on per-request worker threads.
_request_rejections_lock = threading.Lock()
_request_semaphore_rejections = 0

# Unprivileged fallback port chain. The configured ``web_port`` defaults to
# 80, which needs root on Linux; if the operator runs the service
# unprivileged (typical Pi deployment) the primary bind fails. We try each
# fallback in order and use the first that binds, so the UI is reachable on
# a known port even when the primary fails. The fallback that matches the
# configured port (if any) is skipped – no point binding the same port
# twice.
_FALLBACK_PORTS: tuple[int, ...] = (8080, 2010)

# Min seconds between live local-IP re-resolutions; the refresh runs on request
# paths and the resolver enumerates interfaces, so an IP change is picked up
# within this window rather than re-resolving on every request.
_LOCAL_IP_REFRESH_TTL = 5.0

_REQUEST_BUSY_BODY = b"Server busy; retry"
_REQUEST_BUSY_RESPONSE = (
    b"HTTP/1.1 503 Service Unavailable\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"Content-Length: " + str(len(_REQUEST_BUSY_BODY)).encode("ascii") + b"\r\n"
    b"Connection: close\r\n\r\n" + _REQUEST_BUSY_BODY
)


class _QuietHandler(WSGIRequestHandler):
    def log_request(self, *args: object, **kwargs: object) -> None:
        pass


class _ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    # Per-request threads don't block the UI during slow handlers
    # (e.g. synchronous peer broadcasts). daemon_threads=True so
    # Python exits cleanly even if a handler is still in flight.
    daemon_threads = True

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        # ThreadingMixIn has already spawned this worker thread. Gate
        # actual request handling on a global bounded semaphore so a
        # flood of concurrent slow requests cannot pile up real work.
        # Rejected threads send a short 503 and terminate within a few
        # ms; the total number of threads doing WSGI work at once is
        # capped at ``_REQUEST_MAX_CONCURRENT``.
        #
        # Capture the semaphore reference once so acquire and release
        # always target the same object – tests monkeypatch the module
        # attribute, and a mid-flight rebind would otherwise split an
        # acquire from its release (over-releasing the new semaphore
        # and under-releasing the old one).
        sem = _request_semaphore
        if not sem.acquire(blocking=False):
            global _request_semaphore_rejections
            with _request_rejections_lock:
                _request_semaphore_rejections += 1
            try:
                request.sendall(_REQUEST_BUSY_RESPONSE)
            except OSError:
                pass
            try:
                self.shutdown_request(request)
            except Exception:
                pass
            logger.warning(
                "Config web UI rejected a request from %s: concurrent handler cap %d reached.",
                client_address,
                _REQUEST_MAX_CONCURRENT,
            )
            return
        try:
            super().process_request_thread(request, client_address)
        finally:
            sem.release()


class ConfigWebServer:
    """Threaded web server for configuration UI with peer discovery."""

    def __init__(
        self,
        config_path: str,
        host: str = "0.0.0.0",
        port: int = 80,
        system_name: str = "OpenFollow",
        command_queue: WebCommandQueue | None = None,
        local_ip: str = "",
        local_ip_provider: Callable[[], str] | None = None,
        runtime_stats_provider: Callable[[], dict[str, Any]] | None = None,
        preview_snapshot_provider: Callable[[], bytes | None] | None = None,
        zone_state_provider: Callable[[], list[tuple[int, bool, int]]] | None = None,
        zone_diagnostics_provider: (Callable[[int], dict[str, Any] | None] | None) = None,
        zone_test_send: (Callable[[int, str], dict[str, Any]] | None) = None,
        marker_positions_provider: Callable[[], list[tuple[int, float, float]]] | None = None,
        # Live per-marker move speeds (R/T + gamepad bumpers). Device-local and
        # runtime-authoritative; the section-save path overlays them onto the fresh
        # disk load so a save can't clobber a speed the operator just ramped.
        marker_move_speeds_provider: Callable[[], dict[int, float]] | None = None,
        full_snapshot_provider: Callable[[], bytes | None] | None = None,
        osc_binding_status_provider: Callable[[str], dict[str, Any] | None] | None = None,
        osc_binding_preview_provider: Callable[[str], dict[str, Any] | None] | None = None,
        osc_binding_test_send: Callable[[str], dict[str, Any]] | None = None,
        osc_binding_conflicts_provider: (Callable[[str, str, str], list[str]] | None) = None,
        osc_manager_attached_provider: Callable[[], bool] | None = None,
        # MIDI Learn broker callables. The arm/poll split keeps each HTTP
        # call short (the broker's poll never blocks, unlike
        # ``MidiSubsystem.arm_capture``). Both default to ``None`` so the
        # existing test fleet that spins up a stand-alone web server
        # doesn't have to provide a substrate.
        midi_capture_arm: Callable[[str], None] | None = None,
        midi_capture_poll: Callable[[str], dict[str, Any]] | None = None,
        # MIDI page providers; return plain dict/list shapes for route layer.
        midi_discovered_devices_provider: (Callable[[], list[dict[str, Any]]] | None) = None,
        midi_fader_values_provider: (Callable[[], list[dict[str, Any]]] | None) = None,
        # Per-controlled-marker fader snapshot for the MIDI page's
        # read-only "Marker Faders" live viz (one entry per controlled
        # marker: id, name, 0..1 value).
        marker_fader_values_provider: (Callable[[], list[dict[str, Any]]] | None) = None,
        # Diagnostics log-tail fallback for non-systemd hosts; optional for tests.
        log_ring: RingBufferLogHandler | None = None,
        # Read-only host-network snapshot for Overview; optional for tests.
        network_state_provider: Callable[[], dict[str, Any] | None] | None = None,
        # Web write path: config snapshot + apply/renew handlers; optional for tests.
        network_config_provider: (Callable[[str | None], dict[str, Any] | None] | None) = None,
        network_apply_handler: Callable[[str, Any], ApplyResult] | None = None,
        network_renew_handler: Callable[[str], ApplyResult] | None = None,
        # Privilege capability snapshot for the diagnostics bundle; optional for tests.
        privilege_states_provider: Callable[[], dict[str, str]] | None = None,
        # Shared marker catalog (id/name/color) + multicast sync.
        # The inline catalog UI in the Markers & Zones tab (rendered
        # via the ``/api/markers/catalog`` poll) and the ``/api/markers/*``
        # endpoints both read the catalog from these providers – they're
        # called lazily (lambda) so the catalog can be created after
        # the server. The standalone ``/markers`` page was retired in
        # favour of the inline UI.
        marker_catalog_provider: Callable[[], Any] | None = None,
        marker_catalog_sync_provider: Callable[[], Any] | None = None,
        # Live per-controller SDL snapshot for the diagnostics bundle's
        # gamepad section (backend / GUID / calibration-match). Optional so
        # the server still constructs in tests that don't wire input.
        gamepad_runtime_provider: Callable[[], list[dict[str, Any]]] | None = None,
        # Diagnostics I/O providers: recent OSC/MIDI events + USB-visibility cross-reference.
        recent_osc_sends_provider: (Callable[[], list[dict[str, Any]]] | None) = None,
        osc_listener_status_provider: (Callable[[], dict[str, Any]] | None) = None,
        recent_midi_events_provider: (Callable[[], list[dict[str, Any]]] | None) = None,
        midi_port_names_provider: Callable[[], list[str]] | None = None,
        camera_names_provider: Callable[[], list[str]] | None = None,
        # Startup PSN-source advisory when pinned iface unavailable; optional for tests.
        psn_source_advisory_provider: (Callable[[], dict[str, str]] | None) = None,
    ) -> None:
        self._config_path = os.path.abspath(config_path)
        self._host = host
        self._port = port
        self._system_name = system_name
        if local_ip and local_ip in get_local_ipv4_addresses():
            self._local_ip = local_ip
        else:
            if local_ip:
                logger.warning("Configured source IP %s is not a local interface, using auto-detected IP.", local_ip)
            self._local_ip = get_primary_local_ipv4(default="127.0.0.1")
        # Live re-resolver so a runtime IP change (static → DHCP, new lease)
        # is picked up without a restart. The lock guards the cached
        # ``_local_ip`` + beacon-interface repoint against concurrent overview
        # requests; the throttle timestamp keeps request-path refreshes cheap.
        self._local_ip_provider = local_ip_provider
        self._local_ip_lock = threading.Lock()
        self._local_ip_refresh_ts = 0.0  # monotonic; throttles _refresh_local_ip
        self._command_queue = command_queue or WebCommandQueue()
        self._runtime_stats_provider = runtime_stats_provider
        self._preview_snapshot_provider = preview_snapshot_provider
        self._zone_state_provider = zone_state_provider
        self._zone_diagnostics_provider = zone_diagnostics_provider
        self._zone_test_send = zone_test_send
        self._marker_positions_provider = marker_positions_provider
        self._marker_move_speeds_provider = marker_move_speeds_provider
        self._full_snapshot_provider = full_snapshot_provider
        self._osc_binding_status_provider = osc_binding_status_provider
        self._osc_binding_preview_provider = osc_binding_preview_provider
        self._osc_binding_test_send = osc_binding_test_send
        self._osc_binding_conflicts_provider = osc_binding_conflicts_provider
        self._osc_manager_attached_provider = osc_manager_attached_provider
        self._midi_capture_arm = midi_capture_arm
        self._midi_capture_poll = midi_capture_poll
        self._midi_discovered_devices_provider = midi_discovered_devices_provider
        self._midi_fader_values_provider = midi_fader_values_provider
        self._marker_fader_values_provider = marker_fader_values_provider
        # log_ring used by diagnostics when journalctl unavailable.
        self._log_ring = log_ring
        self._network_state_provider = network_state_provider
        self._network_config_provider = network_config_provider
        self._network_apply_handler = network_apply_handler
        self._network_renew_handler = network_renew_handler
        self._psn_source_advisory_provider = psn_source_advisory_provider
        self._privilege_states_provider = privilege_states_provider
        self._marker_catalog_provider = marker_catalog_provider
        self._marker_catalog_sync_provider = marker_catalog_sync_provider
        # Public so ``_build_diagnostics_providers`` can pass it straight
        # through; ``None`` when unwired keeps the bundle's "not wired" vs
        # "no controllers connected" distinction meaningful.
        self.gamepad_runtime_provider = gamepad_runtime_provider
        # Public so diagnostics builders pass directly to DiagnosticsProviders.
        self.recent_osc_sends_provider = recent_osc_sends_provider
        self.osc_listener_status_provider = osc_listener_status_provider
        self.recent_midi_events_provider = recent_midi_events_provider
        self.midi_port_names_provider = midi_port_names_provider
        self.camera_names_provider = camera_names_provider
        self._started_at = time.monotonic()
        self._app = Bottle()
        # Guards the HTTP-server slot assignment in ``_run`` against ``stop()``.
        # Without it, ``stop()`` could run in the window after ``start()`` sets
        # the thread but before ``_run`` populates the slot, miss the freshly
        # bound server, and leave it squatting the port.
        self._lifecycle_lock = threading.Lock()
        self._stopping = False
        self._thread: threading.Thread | None = None
        self._http_server: Any = None
        self._fallback_thread: threading.Thread | None = None
        self._fallback_http_server: Any = None
        # ``_fallback_port`` is the port the fallback thread is serving on.
        # Truth for "is the server up?" is the ``_http_server`` /
        # ``_fallback_http_server`` slots, which ``_run`` populates after
        # ``make_server`` succeeds and clears in its finally block – so
        # ``display_port`` automatically tracks bind/crash/restart cycles
        # without a separate flag.
        self._fallback_port: int | None = None
        # Loopback listener: when ``host`` pins a specific (non-loopback) IP,
        # the server ALSO serves 127.0.0.1 so the on-screen embedded browser
        # (which always targets loopback) keeps working. Idle when host is
        # ``0.0.0.0`` / a loopback address (already covers loopback).
        self._loopback_thread: threading.Thread | None = None
        self._loopback_http_server: Any = None

        # Peer discovery
        self._beacon_sender = BeaconSender(
            name=system_name,
            web_port=port,
            version=openfollow.__version__,
            iface_ip=self._local_ip if self._local_ip != "127.0.0.1" else "",
        )
        self._beacon_receiver = BeaconReceiver(
            on_peer_discovered=self._on_peer_discovered,
            iface_ip=self._local_ip if self._local_ip != "127.0.0.1" else "",
        )
        self._beacon_receiver.set_local_port(port)

        # Import routes and bind to this app
        from openfollow.web import routes

        routes.setup_routes(self._app, self)

    @property
    def config_path(self) -> str:
        return self._config_path

    def get_marker_catalog(self) -> Any:
        """Return the shared :class:`MarkerCatalog`, or ``None``."""
        if self._marker_catalog_provider is None:
            return None
        return self._marker_catalog_provider()

    def get_marker_catalog_sync(self) -> Any:
        """Return the running :class:`MarkerCatalogSync`, or ``None``."""
        if self._marker_catalog_sync_provider is None:
            return None
        return self._marker_catalog_sync_provider()

    @property
    def local_ip(self) -> str:
        return self._local_ip

    @property
    def port(self) -> int:
        return self._port

    @property
    def system_name(self) -> str:
        return self._system_name

    @property
    def log_ring(self) -> RingBufferLogHandler | None:
        """In-memory log ring; diagnostics falls back when journalctl unavailable."""
        return self._log_ring

    def update_system_name(self, name: str) -> None:
        """Update beacon name when config changes."""
        self._system_name = name
        self._beacon_sender.update_name(name)

    def get_peers(self) -> list[PeerInfo]:
        """Get list of discovered peers."""
        return self._beacon_receiver.get_peers()

    def get_privilege_capability_states(self) -> dict[str, str]:
        """Snapshot of every privilege capability's state; empty dict when unwired."""
        if self._privilege_states_provider is None:
            return {}
        try:
            return dict(self._privilege_states_provider())
        except Exception:  # noqa: BLE001
            logger.exception("Privilege states provider raised")
            return {}

    def get_network_state(self) -> dict[str, Any] | None:
        """Read-only host-network snapshot; None when unwired."""
        if self._network_state_provider is None:
            return None
        try:
            return self._network_state_provider()
        except Exception:  # noqa: BLE001
            logger.exception("Network state provider raised")
            return None

    def get_network_config(self, iface: str | None = None) -> dict[str, Any] | None:
        """Raw editable network-config snapshot; iface=None picks active."""
        if self._network_config_provider is None:
            return None
        try:
            return self._network_config_provider(iface)
        except Exception:  # noqa: BLE001
            logger.exception("Network config provider raised")
            return None

    def apply_network(self, iface: str, config: Any) -> ApplyResult:
        """Apply IPv4 config to iface; always returns ApplyResult."""
        from openfollow.network.adapter import ApplyResult

        if self._network_apply_handler is None:
            return ApplyResult(ok=False, message="Network writes are not available on this build.")
        try:
            return self._network_apply_handler(iface, config)
        except Exception as exc:  # noqa: BLE001
            logger.exception("network_apply handler raised")
            return ApplyResult(ok=False, message=str(exc))

    def renew_network(self, iface: str) -> ApplyResult:
        """Renew DHCP lease on iface; always returns ApplyResult."""
        from openfollow.network.adapter import ApplyResult

        if self._network_renew_handler is None:
            return ApplyResult(ok=False, message="Network writes are not available on this build.")
        try:
            return self._network_renew_handler(iface)
        except Exception as exc:  # noqa: BLE001
            logger.exception("network_renew handler raised")
            return ApplyResult(ok=False, message=str(exc))

    def get_psn_source_advisory(self) -> dict[str, str]:
        """Startup advisory when pinned PSN source iface unavailable; returns status/banner/resolved_ip."""
        empty = {"status": "", "banner": "", "resolved_ip": ""}
        if self._psn_source_advisory_provider is None:
            return empty
        try:
            return self._psn_source_advisory_provider()
        except Exception:  # noqa: BLE001
            logger.exception("PSN source advisory provider raised")
            return empty

    def _refresh_local_ip(self) -> None:
        """Re-resolve this host's primary IP and adopt it if it changed.

        The IP captured at startup goes stale when the operator switches the
        interface from static to DHCP (or a lease hands back a new address)
        without restarting. Peers already track us by the beacon's UDP source
        address, so this keeps our own self-row and the beacon's send/receive
        interface in step with them. An unresolved (offline / loopback) result
        is ignored so the last known good IP is never downgraded to blank.

        Throttled to ``_LOCAL_IP_REFRESH_TTL``: this runs on request paths
        (overview poll, /api/info, /api/peers) and the provider does interface
        enumeration, so it must not re-resolve on every hit.
        """
        if self._local_ip_provider is None:
            return
        now = time.monotonic()
        with self._local_ip_lock:
            if now - self._local_ip_refresh_ts < _LOCAL_IP_REFRESH_TTL:
                return
            self._local_ip_refresh_ts = now
        try:
            candidate = self._local_ip_provider()
        except Exception:  # noqa: BLE001
            logger.exception("local_ip provider raised")
            return
        if not candidate or candidate.startswith("127."):
            return
        with self._local_ip_lock:
            if candidate == self._local_ip:
                return
            self._local_ip = candidate
            # Repoint beacons under the lock so IP + interface stay consistent
            # under concurrent refreshes (update_iface_ip never blocks).
            self._beacon_sender.update_iface_ip(candidate)
            self._beacon_receiver.update_iface_ip(candidate)
        logger.info("Local IP changed to %s; beacon interface repointed.", candidate)

    def get_local_peer_info(self) -> PeerInfo:
        """Get info about this server as a PeerInfo object."""
        self._refresh_local_ip()
        return PeerInfo(
            name=self._system_name,
            ip=self._local_ip,
            web_port=self._port,
            version=openfollow.__version__,
            last_seen=time.time(),
        )

    def request_restart(self) -> None:
        """Signal that app restart was requested."""
        self._command_queue.request_restart()

    def request_button_detection(self) -> None:
        """Signal that button detection wizard was requested."""
        self._command_queue.request_button_detection()

    def cancel_button_detection(self) -> None:
        """Signal that the in-progress wizard should be cancelled.
        Drained on the main loop, which calls ``exit_button_detection`` –
        the only thread that may touch the wizard / input state."""
        self._command_queue.request_button_detection_cancel()

    def set_button_detection_active(self, active: bool) -> None:
        """Mark the wizard as running (or done) so the web UI can reflect it."""
        self._command_queue.set_button_detection_active(active)

    def is_button_detection_active(self) -> bool:
        """Return True while the wizard is running on the app display."""
        return self._command_queue.is_button_detection_active()

    def check_restart_requested(self) -> bool:
        """Check and clear restart flag. Called by app main loop."""
        return self._command_queue.consume_restart_requested()

    def request_deb_update(self, service_name: str) -> bool:
        """Queue a web-triggered GitHub-release .deb update command."""
        return self._command_queue.request_deb_update(service_name)

    def request_local_update(self, service_name: str, *, deb_path: str | None = None) -> bool:
        """Queue an offline install of an operator-uploaded .deb."""
        return self._command_queue.request_local_update(service_name, deb_path=deb_path)

    def set_update_status(self, state: str, message: str = "", error: str = "") -> None:
        """Update web-visible status of the update workflow."""
        self._command_queue.set_update_status(state=state, message=message, error=error)

    def get_update_status(self) -> dict[str, str]:
        """Get current web-visible status of the update workflow."""
        return self._command_queue.get_update_status()

    def get_update_available(self) -> str:
        """Newest release the background online-sync found ("" if up to date)."""
        return self._command_queue.get_update_available()

    def pending_privilege_password_request(self) -> dict[str, str] | None:
        """Return the active privilege-password prompt or None."""
        return self._command_queue.pending_privilege_password_request()

    def submit_privilege_password(self, password: str) -> None:
        """Forward an operator-typed device password to the parked privilege worker."""
        self._command_queue.submit_privilege_password(password)

    def cancel_privilege_password(self) -> None:
        """Operator dismissed the privilege-password modal – wake the
        worker so it can surface a clean cancellation error."""
        self._command_queue.cancel_privilege_password()

    def try_claim_detection_install(
        self,
        *,
        action: str,
        extra: str,
        message: str = "",
    ) -> bool:
        """Atomically transition detection-install state idle → running."""
        return self._command_queue.try_claim_detection_install(
            action=action,
            extra=extra,
            message=message,
        )

    def set_detection_install_status(
        self,
        *,
        state: str,
        message: str = "",
        tail: str = "",
        extra: str | None = None,
        action: str | None = None,
    ) -> None:
        """Publish the terminal state of a detection install/uninstall job."""
        self._command_queue.set_detection_install_status(
            state=state,
            message=message,
            tail=tail,
            extra=extra,
            action=action,
        )

    def get_detection_install_status(self) -> dict[str, str]:
        """Get the detection install/uninstall job status."""
        return self._command_queue.get_detection_install_status()

    def get_runtime_stats(self) -> dict[str, Any]:
        """Return latest runtime telemetry snapshot for web routes."""
        if self._runtime_stats_provider is None:
            return {}
        return self._runtime_stats_provider()

    def get_preview_snapshot(self) -> bytes | None:
        """Return latest JPEG preview snapshot, or None."""
        if self._preview_snapshot_provider is None:
            return None
        return self._preview_snapshot_provider()

    def get_zone_states(self) -> list[tuple[int, bool, int]]:
        """Return current zone occupancy as [(zone_index, is_occupied, count), ...]."""
        if self._zone_state_provider is None:
            return []
        return self._zone_state_provider()

    # Per-zone Diagnostics tab; optional providers fall back to "manager not running" shape.
    def get_zone_diagnostics(self, index: int) -> dict[str, Any] | None:
        """Return per-zone diagnostics (occupants, last fire) or ``None``
        when no engine is attached or the index is out of range."""
        if self._zone_diagnostics_provider is None:
            return None
        return self._zone_diagnostics_provider(index)

    def trigger_zone_test_send(self, index: int, which: str) -> dict[str, Any]:
        """Force a one-shot send for the zone at ``index`` on the
        ``which`` field (``"first"`` / ``"additional"`` / ``"partial"``
        / ``"final"``). Empty dict signals "test endpoint unavailable"
        so the caller can surface a friendly notice instead of crashing."""
        if self._zone_test_send is None:
            return {}
        return self._zone_test_send(index, which)

    def get_marker_positions(self) -> list[tuple[int, float, float]]:
        """Return current marker XY positions in PSN-absolute world coordinates."""
        if self._marker_positions_provider is None:
            return []
        return self._marker_positions_provider()

    def get_marker_move_speeds(self) -> dict[int, float]:
        """Return live per-marker move speeds; empty dict when unwired.

        The provider (wired in ``services``) hands back a copy, so a section save
        can overlay these onto its fresh disk load without touching the live dict.
        """
        if self._marker_move_speeds_provider is None:
            return {}
        return self._marker_move_speeds_provider()

    def get_full_snapshot(self) -> bytes | None:
        """Return a full-resolution JPEG snapshot, or None."""
        if self._full_snapshot_provider is None:
            return None
        return self._full_snapshot_provider()

    # OSC-binding diagnostics surface; optional providers fall back to "manager not running" shape.
    def is_osc_manager_attached(self) -> bool:
        """Whether the live OSC transmitter manager is up. Lets the diagnostics
        routes tell "row saved but the manager hasn't picked it up yet" apart
        from "no manager attached at all" (boot / restart)."""
        if self._osc_manager_attached_provider is None:
            return False
        return self._osc_manager_attached_provider()

    def get_osc_binding_status(self, row_id: str) -> dict[str, Any] | None:
        """Return per-row diagnostics (pps, last_error, healthy,
        ring_buffer) or ``None`` when no manager is attached or the row
        is unknown."""
        if self._osc_binding_status_provider is None:
            return None
        return self._osc_binding_status_provider(row_id)

    def get_osc_binding_preview(self, row_id: str) -> dict[str, Any] | None:
        """Return a rendered preview (resolved address + args + skip
        reason) for ``row_id`` against current marker state, or
        ``None`` when no manager is attached / the row is unknown."""
        if self._osc_binding_preview_provider is None:
            return None
        return self._osc_binding_preview_provider(row_id)

    def trigger_osc_binding_test(self, row_id: str) -> dict[str, Any]:
        """Force a one-shot send for ``row_id`` and return the result
        dict. Empty dict signals "test endpoint unavailable" so the
        caller can surface a friendly notice instead of crashing."""
        if self._osc_binding_test_send is None:
            return {}
        return self._osc_binding_test_send(row_id)

    # MIDI Learn capture surface for the OSC binding form's Capture
    # button. Both methods return a sentinel ``{"status": "unavailable"}`` /
    # no-op when no broker is wired so the route layer can respond cleanly
    # during boot or in unit tests that didn't spin up a substrate.
    def midi_capture_arm(self, row_id: str = "") -> dict[str, Any]:
        """Arm a fresh MIDI Learn capture. Returns a status dict
        the route layer turns into JSON. ``unavailable`` covers
        the "no MIDI subsystem wired" case (e.g. headless test
        contexts) so the form renders a meaningful banner instead
        of a stack trace.

        ``row_id`` scopes the capture to the calling row so two
        concurrent rows can each get their own ``cancelled`` /
        ``captured`` signal back."""
        if self._midi_capture_arm is None:
            return {"status": "unavailable"}
        self._midi_capture_arm(row_id)
        return {"status": "armed"}

    def midi_capture_poll(self, row_id: str = "") -> dict[str, Any]:
        """Read the broker's current state. Returns the broker's
        ``poll`` shape verbatim, or ``{"status": "unavailable"}``
        when no broker is wired."""
        if self._midi_capture_poll is None:
            return {"status": "unavailable"}
        return self._midi_capture_poll(row_id)

    # MIDI page reads (return empty lists when no substrate wired).
    def midi_discovered_devices(self) -> list[dict[str, Any]]:
        """List of currently-connected MIDI devices. Each entry is
        ``{"identifier": str, "port_name": str, "product": str,
        "serial": str | None}`` – plain dict shapes so the template
        + JSON callers don't have to know the substrate's
        :class:`DiscoveredDevice` dataclass."""
        if self._midi_discovered_devices_provider is None:
            return []
        return self._midi_discovered_devices_provider()

    def midi_fader_values(self) -> list[dict[str, Any]]:
        """Snapshot of the eight virtual faders for the live-poll
        endpoint. Each entry is ``{"index": int, "name": str,
        "value": float, "picked_up": bool, "show_on_display":
        bool}``. Empty list when no substrate is wired (test
        contexts) so the poll endpoint returns ``[]`` rather than
        500."""
        if self._midi_fader_values_provider is None:
            return []
        return self._midi_fader_values_provider()

    def marker_fader_values(self) -> list[dict[str, Any]]:
        """Snapshot of the per-controlled-marker gamepad faders for the
        MIDI page's read-only "Marker Faders" live viz. Each entry is
        ``{"marker_id": int, "name": str, "value": float}`` (one per
        controlled marker). Empty list when no substrate is wired (test
        contexts) so the poll endpoint returns ``[]`` rather than 500."""
        if self._marker_fader_values_provider is None:
            return []
        return self._marker_fader_values_provider()

    def get_osc_binding_conflicts(
        self,
        kind: str,
        identifier: str,
        owner: str,
    ) -> list[str]:
        """Conflict probe for the on-blur validator. ``kind`` is one of
        ``"key"`` / ``"controller_button"`` etc., matching
        :data:`InputBinding.kind`; ``owner`` excludes self-conflicts.
        Empty list when no registry is wired (treat as "no conflicts
        known" for unit-test contexts)."""
        if self._osc_binding_conflicts_provider is None:
            return []
        return self._osc_binding_conflicts_provider(kind, identifier, owner)

    @staticmethod
    def _can_bind(host: str, port: int) -> bool:
        """Return True if an IPv4 TCP socket can bind to host:port."""
        bind_host = "" if host in {"", "0.0.0.0"} else host
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((bind_host, port))
            except OSError:
                return False
        return True

    def start(self) -> None:
        """Start web server and beacon threads.

        Tries the configured (primary) port and then walks ``_FALLBACK_PORTS``
        looking for the first that binds. The primary may fail (e.g. port 80
        on an unprivileged Pi); when it does, the fallback takes over.  A
        hard error is logged only when neither primary nor any fallback can
        bind.

        Idempotent: re-entering while either the primary or the fallback
        thread is alive is a no-op. The fallback-only branch must guard on
        ``_fallback_thread`` too, otherwise a primary-failed restart would
        re-probe and log a spurious hard error.
        """
        if self._thread is not None or self._fallback_thread is not None:
            return

        # Clear the stop signal from any prior ``stop()`` so the freshly
        # launched workers don't immediately self-shutdown.
        self._stopping = False

        primary_ok = self._can_bind(self._host, self._port)

        # Pick the first fallback port that differs from the primary and
        # binds. Skipping the matching port avoids redundantly trying to
        # bind the same address twice. Reset before the search so a prior
        # ``stop()`` + ``start()`` cycle doesn't leave a stale port.
        self._fallback_port = None
        fallback_port: int | None = None
        for candidate in _FALLBACK_PORTS:
            if candidate == self._port:
                continue
            if self._can_bind(self._host, candidate):
                fallback_port = candidate
                break

        if not primary_ok and fallback_port is None:
            # Mirror the candidate-loop's behaviour: if the operator
            # configured the primary port to one of the fallback entries,
            # that entry was never actually probed – listing it would be
            # misleading.
            tried = [p for p in _FALLBACK_PORTS if p != self._port]
            logger.error(
                "Cannot start config web UI: %s:%d unavailable and no fallback port (%s) is free.",
                self._host,
                self._port,
                ", ".join(str(p) for p in tried),
            )
            return

        # Start beacon discovery (only once at least one HTTP socket will bind).
        self._beacon_sender.start()
        self._beacon_receiver.start()

        if primary_ok:
            self._thread = threading.Thread(
                target=self._run,
                args=(self._host, self._port, "primary"),
                daemon=True,
                name="ConfigWebServer",
            )
            self._thread.start()
            logger.info(
                "Starting config web UI at http://%s:%d",
                self._local_ip,
                self._port,
            )
        else:
            logger.warning(
                "Primary web UI port %d unavailable; serving on fallback port %d only.",
                self._port,
                fallback_port,
            )

        if fallback_port is not None:
            self._fallback_port = fallback_port
            self._fallback_thread = threading.Thread(
                target=self._run,
                args=(self._host, fallback_port, "fallback"),
                daemon=True,
                name="ConfigWebServerFallback",
            )
            self._fallback_thread.start()
            if primary_ok:
                logger.info(
                    "Config web UI also reachable at http://%s:%d (fallback)",
                    self._local_ip,
                    fallback_port,
                )
            else:
                logger.info(
                    "Config web UI reachable at http://%s:%d",
                    self._local_ip,
                    fallback_port,
                )

        # When the external listener is pinned to a specific (non-loopback)
        # IP, also serve 127.0.0.1 so the on-screen embedded browser (always
        # loopback) still reaches the UI. Serves the live display port; a
        # loopback-bind failure must not take down the pinned listener.
        loopback_port = self._port if primary_ok else fallback_port
        if self._needs_loopback_listener() and loopback_port is not None and self._can_bind("127.0.0.1", loopback_port):
            self._loopback_thread = threading.Thread(
                target=self._run,
                args=("127.0.0.1", loopback_port, "loopback"),
                daemon=True,
                name="ConfigWebServerLoopback",
            )
            self._loopback_thread.start()
            logger.info("Config web UI also reachable at http://127.0.0.1:%d (loopback)", loopback_port)

    def _needs_loopback_listener(self) -> bool:
        """True when the external bind pins a specific non-loopback IP, so a
        separate 127.0.0.1 listener is needed for the on-screen browser.
        ``0.0.0.0`` and any 127.x / ::1 address already cover loopback."""
        host = self._host
        return bool(host) and host != "0.0.0.0" and not host.startswith("127.") and host != "::1"

    @property
    def display_port(self) -> int:
        """Port that the operator should type in their browser.

        Derived from the live HTTP server slots so the value automatically
        tracks bind success, mid-run crashes, and stop+restart cycles –
        no separate "did the primary bind?" flag to keep in sync.

        - Primary slot live: configured port (the URL the operator set).
        - Primary down, fallback live: the fallback port (what actually
          reaches the UI).
        - Neither live (server never started, or fully stopped): falls
          back to the configured port so the HUD value is never blank.
        """
        if self._http_server is not None:
            return self._port
        if self._fallback_http_server is not None and self._fallback_port is not None:
            return self._fallback_port
        return self._port

    # -- Diagnostics accessors -----------------------------------------------
    # Read-only views into runtime state for the bundle. The collector
    # in ``openfollow.web.diagnostics`` consumes these via
    # ``DiagnosticsProviders`` callables built in the route handler.

    @property
    def beacon_sender(self) -> BeaconSender:
        """The :class:`BeaconSender` instance. Exposed so the bundle's
        sender-health subsection reads straight from the substrate
        rather than via a duplicated dict."""
        return self._beacon_sender

    @property
    def beacon_receiver(self) -> BeaconReceiver:
        """The :class:`BeaconReceiver` instance."""
        return self._beacon_receiver

    @property
    def process_uptime_s(self) -> float:
        """Seconds since this server instance was constructed.
        Tracks the instance, not the process – a restart of the web
        server within the same Python process resets the clock,
        which matches what the operator wants to see when they're
        debugging a hot reload."""
        return time.monotonic() - self._started_at

    @property
    def request_semaphore_rejections(self) -> int:
        """Cumulative count of 503s from the concurrent-handler cap."""
        with _request_rejections_lock:
            return _request_semaphore_rejections

    def _run(self, host: str, port: int, slot: str) -> None:
        """Run the WSGI server on ``host:port`` (called in a thread).

        ``slot`` is one of ``"primary"`` / ``"fallback"`` / ``"loopback"`` and
        selects which ``_http_server`` / thread slot this listener owns.
        """
        from wsgiref.simple_server import make_server

        try:
            srv = make_server(
                host,
                port,
                self._app,
                server_class=_ThreadingWSGIServer,
                handler_class=_QuietHandler,
            )
            # Publish the slot under the lifecycle lock so a concurrent
            # ``stop()`` either sees the server here (and shuts it down) or
            # has already flagged ``_stopping`` – in which case we shut down
            # before ``serve_forever`` so the listener can't squat the port.
            with self._lifecycle_lock:
                if slot == "fallback":
                    self._fallback_http_server = srv
                elif slot == "loopback":
                    self._loopback_http_server = srv
                else:
                    self._http_server = srv
                stopping = self._stopping
            if stopping:
                # ``stop()`` already ran and missed this slot. Don't enter
                # ``serve_forever`` – release the port directly. (Calling
                # ``shutdown()`` here would deadlock: it waits on a
                # ``serve_forever`` loop that never starts.)
                srv.server_close()
                return
            srv.serve_forever()
        except OSError as exc:
            logger.error("Config web UI failed on %s:%d: %s", host, port, exc)
        except Exception:
            logger.exception("Config web UI thread crashed.")
        finally:
            if slot == "fallback":
                # Clear ``_fallback_http_server`` first: ``display_port``
                # reads it as the liveness signal and would otherwise look
                # at a server that's already shutting down.
                self._fallback_http_server = None
                self._fallback_port = None
                self._fallback_thread = None
            elif slot == "loopback":
                self._loopback_http_server = None
                self._loopback_thread = None
            else:
                self._http_server = None
                self._thread = None

    def stop(self) -> None:
        """Stop the web server and beacons."""
        self._beacon_sender.stop()
        self._beacon_receiver.stop()
        # Flag the stop and snapshot the slots under the lifecycle lock. This
        # pairs with ``_run``: either a worker has already published its slot
        # (snapshot below shuts it down) or it hasn't yet and will see
        # ``_stopping`` and self-close instead of squatting the port.
        with self._lifecycle_lock:
            self._stopping = True
            servers = (self._http_server, self._fallback_http_server, self._loopback_http_server)
        for srv in servers:
            if srv is not None:
                srv.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        if self._fallback_thread is not None:
            self._fallback_thread.join(timeout=3.0)
            self._fallback_thread = None
        if self._loopback_thread is not None:
            self._loopback_thread.join(timeout=3.0)
            self._loopback_thread = None

    def _on_peer_discovered(self, peer: PeerInfo) -> None:
        """Called when a new peer is discovered."""
        logger.info("Discovered peer: %s at %s", peer.name, peer.address)
