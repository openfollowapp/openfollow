# Marker outputs

Covers `psn/`, `otp/`, `rttrpm/`, the OSC transmitter, `packet_chunking.py`. The rules that apply everywhere are in [CLAUDE.md](../../CLAUDE.md).

## The one canonical frame

**X = stage left, Y = upstage (away from audience), Z = up.**

`marker.pos` is **PSN-absolute world coordinates** everywhere. Every touchpoint on the marker-position pipeline reads and writes that one frame – no site translates by `grid.{x,y}_offset`:

| Site | Frame |
|---|---|
| `psn/receiver.py` (`apply_remote` from incoming PSN packet) | PSN-absolute (raw) |
| `psn/server.py` (`marker.to_psn_marker()` outbound) | PSN-absolute (verbatim) |
| `input/mouse.py` (`unproject_to_plane` → `set_pos`) | PSN-absolute (direct) |
| `runtime/services_detection_pin.py` (pin target) | PSN-absolute (direct) |
| `services._collect_marker_positions` (zone-engine input) | PSN-absolute (verbatim) |
| `TriggerZoneConfig.vertices` (stored in config) | PSN-absolute |
| `runtime/overlay_draw_scene.draw_marker` (renderer) | PSN-absolute (no offset adjust) |
| `runtime/overlay_draw_zones.draw_zones` (renderer) | PSN-absolute |
| `scene/solver.unproject_to_plane` output / `project_points` input | PSN-absolute |

`grid.{x,y}_offset` is **display-positioning metadata** for the grid rectangle itself – used by the web setup wizard / `scene/solver.py` to build the four grid corners in world space, and by the web zone editor to centre its viewport on the grid origin. It is **never** added to or subtracted from `marker.pos`, a detection world-point, or a zone vertex.

**Do not add offset arithmetic to any marker-position flow.** The shape to watch for is "subtract offset on write, add offset on read": a hidden convention that works only at zero offset. The regression suite in `tests/test_coordinate_system_invariants.py` parametrises nine invariants across three offset configurations (zero, positive, mixed-sign), so an offset at any of these sites fails across every non-zero case.

## Marker freshness (`is_marker_stale`, shared by every output)

`build_marker_visual_state` rewrites every **controlled** marker each frame (an unconditional `set_speed` carrying the marker's velocity estimate, `(0, 0, 0)` when it is at rest), and every `Marker` data write stamps `timestamp`. That stamp is therefore a per-marker "the frame loop ran" signal, which is what the four output protocols use to avoid transmitting a frozen position as if it were live – each runs its own send thread, so none of them stops when the frame loop does.

`marker_age_s(marker, *, now_us=None)` / `is_marker_stale(marker, *, now_us=None)` / `MARKER_STALE_AFTER_S` (1.0 s ≈ 60 missed frames) live in [`psn/marker.py`](../../openfollow/psn/marker.py). A remote marker (sender's epoch) and a never-written one both report `inf`, so anything unaged fails safe as stale – and `inf` is not `int()`-able, which the OTP sampled-timestamp path has to handle. The reference is the **marker's own clock** (`Marker.clock_now_us`), never the module function: `PsnServer.add_marker` passes its `clock=` down, so a server on an injected clock would otherwise age every marker against an unrelated epoch and ship `status=0.0` for a freshly written position. Pass one `now_us` (from that same clock) when ageing several markers for one packet.

| Output | What staleness does on the wire |
|---|---|
| PSN | `to_psn_marker(stale=True)` publishes `PSN_DATA_TRACKER_STATUS = 0.0` for that packet only. It does **not** mutate `Marker._status` – recovery must restore the real validity, and the detection-derived status (see "Tracker status" below) must not be clobbered |
| OTP | The Point Layer's `sampled_timestamp_us` is `timestamp_us - marker_age_us` (E1.59 §9.6: when the Producer read *that Point*), clamped at 0. A frozen point stops ageing while the Transform Layer's timestamp runs on |
| RTTrPM | No validity field exists, so absence is the idiom: stale trackables are filtered out of the packet, and an all-stale set sends nothing so the receiver's own timeout fires |
| OSC transmitter | A stale marker skips with the reason in that row's ring buffer. Explicit `[x:N]` / controller `[x:cN]` refs go through `RenderContext.marker_stale_resolver` (neither sets `needs_default_marker`), which raises `RenderError(..., hint="position is stale")` so the skip is distinguishable from an unregistered marker. Constant / hotkey / MIDI rows are unaffected |

**Do not gate the per-frame `set_speed` on movement.** The value may be zero; the write never skips. A deliberately still marker would otherwise go stale and drop off the wire on all four protocols. `tests/test_output_staleness.py` pins the wire behaviour; `tests/test_marker_freshness.py` pins the rule.

## Frame loop stall watchdog

`check_frame_loop_stall` (on the housekeeping timeout, because a stalled loop can't report itself) flags `app._frame_stalled` after `_FRAME_STALL_AFTER_S` (**derived from `MARKER_STALE_AFTER_S`** so the indicator and the wire can't disagree) and logs one line per episode on each edge, timed from the last completed frame so the figure is the whole outage. It reads `app._last_frame_completed`, stamped as the **last line of `animate`** – a frame that raises or hangs part-way must not advance the liveness clock, or a loop failing every tick reads as healthy while every output goes stale. `get_runtime_stats_snapshot` overlays `playback.stalled` + `playback.seconds_since_last_frame` at **read** time. The Statistics → Device `Frame clock` chip is driven by the **age**, not the flag alone: the watchdog shares the main loop with the frame clock, so a block inside one callback stops both and leaves the flag False. It compares against `playback.stale_after_s` (published from `MARKER_STALE_AFTER_S`), never a literal, so the chip can't disagree with what the outputs are doing.

## Tracker status (`PSN_DATA_TRACKER_STATUS`)

`Marker.status` is the tracker's validity and is read only by PSN (OTP / RTTrPM / OSC have their own freshness idioms above). A marker under manual or assist control carries `1.0`: `_stamp_locked` promotes an untouched marker on its first data write, and an explicit `set_status` stands, **0.0 included** – `_status` is `None` until either happens, so an explicit invalid is never mistaken for "nothing written yet" and promoted back by the next per-frame `set_speed`. Only the Fully Automatic pin writes it, through `detection_status(score, threshold, age_s=, grace_s=)` in [`runtime/services_detection_pin.py`](../../openfollow/runtime/services_detection_pin.py): the configured confidence reads 0.5, a perfect score 1.0, a low-band recovery match continues the same line below 0.5; a coasting track (`DetectionBox.age_s`) decays linearly to 0.0 across `PersonDetector.coast_s`; nobody tracked, no feed, or a pin point that does not unproject is 0.0. **A box has one age**, `_coast_age_s`: seconds since its last match, less the detector's own step period, floored at 0 – so a box one step overdue is fresh and a match on every step never reads as coasting between steps. `_track` stamps it on a lost track and `tracked_detection` recomputes it live for every box it hands out, sticky or re-acquired, so a detector that stops stepping ages its last box too and a re-acquire from frozen results is not a fresh sighting. The step period (`_step_allowance_s`) is the longest of the last `_STEP_HISTORY` gaps, floored at `interval_ms` (only the pull timeout: inference time sets the cadence); each gap is **clamped to `_MAX_DT_REL` nominal steps** before it is recorded, because a pull timeout, an inference error or a backend rebuild never steps the tracker and the first step after one spans the whole outage. `coast_s` is how long a box stays handed out past its due step: the grace period, **never under one step period**, so a step longer than the recent ones (which always overruns an allowance that *is* the longest recent step) is overdue, not lost, and a zero grace does not release the pin on cadence jitter. The pin fades across that same window, so 0.0 lands exactly where the box stops being handed out, and `_track` retains a lost track for `allowance + coast_s`, the span it is handed out for, so the tracker never drops it mid-fade. Position and status land in **one** `Marker.set_pos(..., status=)` write: the sender reads both under one lock, so two writes would let a packet pair the new position with the previous frame's status. The value is raw per frame, no smoothing, and both the threshold (`PersonDetector.confidence_threshold`) and the window are the detector's, not the config's, because the worker drains config changes on its own cadence. `_release_status` restores 1.0 on every path that stops driving a marker, all of them through `_prune_pin_states`: detection off, another marker pinned, marker left `controlled_marker_ids`, and a `pin_mode` switch (`app._detection_pin_mode` remembers the mode the states were built under, and a change prunes them all before either mode runs, so the other mode's smoothing and status never reach its first frame, feed or no feed); plus the replace path when there is no detector or the detector never loaded a backend (`available` is False: it tracks nobody, ever, and the operator drives the marker). Both key on `DetectionPinState.status`, which also feeds `tracking.pin_status` in `/api/stats` (a wire interface) so support can set the pin's view beside what the wire carries. Staleness outranks the stored value on the wire. Help text lives in `web/help/psn.md`.

## Datagram size (`packet_chunking.MAX_DATAGRAM_BYTES`, shared by every output)

Every marker-carrying output grows its datagram with the marker count, and **1472 bytes** (a 1500 B Ethernet MTU less the 20 B IPv4 and 8 B UDP headers) is the ceiling for all of them. That is the *fragmentation* threshold, which bites before any protocol's own packet limit and degrades silently – a quiet bench LAN reassembles the fragments, a loaded show network drops one and loses the whole datagram. Budget against 1472, never 1500.

`chunk_to_datagrams(items, encoded_size, budget)` in [`packet_chunking.py`](../../openfollow/packet_chunking.py) is the one splitter: it measures through the caller's own encoder (so a protocol field added later moves the split instead of silently pushing the datagram over), keeps order, loses nothing, and emits an item that can never fit alone rather than dropping it or spinning. Each protocol then applies its own grouping mechanism:

| Output | How one marker set spans datagrams | Crossed at |
|---|---|---|
| PSN data / info | Packets of one frame: one `frame_id` and one header timestamp repeated across them, `frame_packet_count` = the chunk count. Build the header **once per frame** – a per-packet id reassembles nothing. Capped at 255 packets (`frame_packet_count` is a uint8) | 14 / 85 markers |
| OTP transform / name advertisement | Pages of one folio (E1.59 §6.7-6.9): shared Folio Number, `page` 0..`last_page`. Everything outside the split list repeats verbatim on every page, the Transform Layer's **Full Point Set** flag included – it describes the folio, so a page contradicting its siblings describes no coherent point set | 32 / 36 markers |
| RTTrPM | Independent packets, each with its own `pkt_id`. RTTrPM groups nothing across packets, so no reassembly is involved | 35 markers |

`PsnReceiver` needs no reassembly buffer: it reads 65535 and applies each packet's trackers independently, so a peer's split frame accumulates naturally. The `MAX_OTP_MESSAGE_OCTETS` (§6.3.1) and RTTrPM `_MAX_MODULES` / `_MAX_PACKET_BYTES` checks stay as structural backstops behind the split – they guard what the wire format can *express*, which is a different question from what the network can carry.

`tests/test_output_packet_splitting.py` pins the cross-protocol invariant (no stream emits a datagram past 1472 at any marker count) plus each protocol's grouping; `scripts/hw_validation/output_datagram_size_probe.py` re-checks it on the DUT against the deployed send paths.

## PsnServer (`psn/server.py`)
- Sends PSN multicast at `data_fps` (default 60 Hz)
- `add_marker(id, name)` / `remove_marker(id)` / `get_marker(id)`
- `marker.set_pos(x, y, z)`, `marker.set_speed(vx, vy, vz)`
- **Speed encoding:** every frame in `build_marker_visual_state`, each controlled marker's speed is its estimated **velocity vector** in m/s, PSN-absolute frame ([`runtime/marker_velocity.py`](../../openfollow/runtime/marker_velocity.py): position delta over the **real** elapsed seconds – `animate` hands `update_marker_visuals` the unclamped `elapsed`, not the clamped motion `dt`, because a clamped divisor inflates the rate of a slow frame – EMA-smoothed at alpha 0.3 per nominal frame, frame-rate independent via `ema_factor`). Two bounds, neither of which classifies motion: the sample rate is **clamped** to `_MAX_REPORTED_SPEED_MPS` (20 m/s), so a repositioning (reset, OSC snap) can't put an unbounded rate on the wire while a fast mouse drag still reports as moving; and the smoothed output snaps to exactly `(0, 0, 0)` below `_STILL_SPEED_MPS`, so a marker that comes to rest reads as stopped rather than asymptotically slow. The HUD card of a controlled marker shows the configured move speed (what R / T and the bumpers adjust), not this vector; viewer cards show `‖speed‖` of the received marker.

## PsnReceiver (`psn/receiver.py`)
- Receives PSN multicast in background thread
- `ignore_ids`: controlled_marker_ids – prevents loopback overwriting own markers
- `_last_seen[tid]`: monotonic timestamp of last received packet
- `_last_pos[tid]`: previous position for speed derivation
- `_wire_speed_sender[tid]`: the source address last seen publishing a non-zero speed for that tracker (dropped on TTL eviction). PSN has no server id, so several stations on one group can send the same tracker id – keying the trust by id alone would let a zero-only station's packets be stored verbatim because a different station earned the trust
- **Speed logic (per packet):**
  1. Any non-zero `t.speed` records this datagram's sender as the tracker's wire-speed source
  2. The packet carries a speed **and** comes from that sender: store the vector verbatim, zeros included (the sender says it is still)
  3. Otherwise – no speed chunk (it is optional, and holding the last one would freeze a stale vector forever), or a different sender – derive from `delta_pos/dt` inside the `0.001 < dt < 1.0` window, only when actually moving (preserves last known speed when stationary)
- **Received markers carry the sender's own `timestamp` / `status`.** Position, speed, and both fields land in one `Marker.apply_remote(...)` write, which never stamps the local clock – the wire values are the sender's, in *its* epoch, and are not comparable to `psn_timestamp_usec()` or to another sender's. Such a marker is built `remote=True` and reports `is_remote`; don't re-broadcast its timestamp as ours. Local freshness is `is_marker_online` (arrival), never the tracker timestamp
- `is_marker_online(tid, timeout=2.0)`: returns True if packet received within 2s
- `rebind(source_ip, source_iface=)` moves the receive socket to another interface (see [network](network.md#multicast-sockets-select-an-interface-by-index))
