# Plan: camera latency measurement

Operators see up to a second of delay on the Operator Screen and can't tell
whether the camera, the stream or the station causes it. This plan adds a
**Measure latency** action to the Video Source page: the operator points the
camera at the station's own Operator Screen, the station flashes the screen
black and white, watches for each change in the decoded video and reports the
round trip.

Both candidate methods (flash and timecode bars) were compared in a browser
simulation with no camera. Flash won: it reads the picture as one average
brightness, so it needs no framing, survives blur, low detail, stage light and
auto exposure, and has no wrap-around limit. Timecode bars were faster and saw
dropped frames, but failed first under poor framing or focus and read a wrong,
plausible number past the counter's wrap.

## Decisions

| Topic | Decision |
|---|---|
| Method | Full-screen black / white flashes on the Operator Screen. The station times each change of the average brightness of the decoded frames. |
| Entry point | A **Measure latency** button on the Video Source page; **Stop** while a run is going. No HUD menu entry. |
| Guard | None: no confirm, no gating on live outputs. One click starts the run. |
| Availability | Disabled (visible, with a status row naming the reason) when no display is attached, the source is the Media Gallery, or no video is decoded yet. |
| On screen | Plain black or white over video and HUD, nothing else. Progress shows in the web UI only. |
| Run | 1 s black, then 5 flashes of 150 ms white, gaps drawn from 300 to 900 ms. Each change waits until it is seen before the next. Both edges are timed: 10 samples. |
| Detection | An edge is a jump in mean luma against the minimum (rise) or maximum (fall) of the previous 3 frames. Threshold 7 % of full scale, then 35 % of the contrast seen on the first flash, never below 4 %. Slow auto-exposure drift never crosses it. |
| Clock | One monotonic clock (`GLib.get_monotonic_time()`, µs) for both stamps: the refresh that put the flash on screen and the arrival of the decoded frame at the display sink. |
| Timeout | No edge within 3 s ⇒ the run stops with a plain reason ("No flash seen in the video within 3 s. Point the camera at the Operator Screen."). |
| Result | Median in ms and the spread (min to max), with a status level: success below 120 ms, caution below 250 ms, error above. The help drawer explains that the monitor's own lag is part of the number. |
| Kept | The last **successful** result (median, spread, time, source type) is published in runtime stats and shown in the diagnostics bundle. Failed runs are shown, never stored. |
| Feature toggle | None. The probe costs nothing unless a run is active. |

## Work items

### Probe (`runtime/latency_probe.py`)
- [ ] `LatencyProbe`: pure state machine, clock injected. States `idle`, `settle`, `gap`, `wait_rise`, `hold`, `wait_fall`, `done`. Owns the screen level the display must show, the edge detector and the result.
- [ ] `feed(arrival_us, mean_luma)` is called from the GStreamer streaming thread and only appends to a lock-protected deque; `step(now_us)` runs on the frame loop, drains it and advances the state machine.
- [ ] `presented(level, refresh_us)`: the display reports the refresh that first showed a new level; edge times are measured from that stamp, never from the request.
- [ ] Result type: `median_ms`, `min_ms`, `max_ms`, `samples`, `finished_at`, `source_type`; failure type with a reason enum (`no_rise`, `no_fall`, `stopped`, `source_changed`).
- [ ] A live source swap, a pipeline rebuild or the feed leaving `DECODING` stops the run with `source_changed`.
- [ ] Tests (`tests/test_latency_probe.py`), all on a fake clock with synthetic luma traces: a clean run gives the injected delay within one frame; auto-exposure drift between flashes never triggers an edge; a rise or fall spread over two frames (long shutter) still detects; no rise within 3 s and no fall within 3 s each fail with their reason; stop mid-run; source change mid-run; median and spread over 10 samples; an edge from before `presented` is never paired with the new level.

### Frame luma hook (`video/receiver.py`, `video/gl_display.py`)
- [ ] Pass the buffer to the probe from the existing display-sink probe while a run is active; return immediately otherwise. Placeholder frames are ignored, as `_on_sink_buffer` does.
- [ ] `mean_luma(buffer, caps)`: subsampled mean of the Y plane for I420 (the GL path), of a weighted RGB sum for the packed RGB formats gtksink negotiates. Reads every 8th pixel of every 8th row.
- [ ] Tests: I420 and RGBx / BGRx buffers of known value; odd sizes and strides; an unsupported format returns `None` and the run fails with a reason instead of hanging; no call into the probe while idle.

### Flash on the Operator Screen (`video/gl_display.py`, `video/overlay.py`, `video/hud_render.py`)
- [ ] GL path: while the probe asks for a level, `GlDisplay._draw` clears to solid black or white after video and HUD, and reports the refresh time of that draw to `probe.presented`.
- [ ] Cairo fallback: a first branch in `CairoOverlayRenderer.draw` fills the canvas from a new `OverlayState` field; `HudRenderThread.tick` reports `presented` when the picture carrying the new level goes on screen, so the HUD pacing delay is not counted.
- [ ] `OverlayState` field set in `build_marker_visual_state` from the probe.
- [ ] Tests: `tests/test_gl_display.py` (fill drawn over video and HUD, `presented` stamped with the refresh time, nothing drawn while idle); `tests/test_video_overlay.py` (fill branch first, nothing else drawn); `tests/test_hud_render.py` (`presented` reported when the picture is shown, not when it is rendered); `tests/test_services_marker_visuals.py` (probe level reaches the overlay state).

### Web (`video_source` section)
- [ ] Routes: `POST /section/video_source/latency` (start), `POST /section/video_source/latency/stop`, `GET /section/video_source/latency` (status partial). Start and stop go through the `WebCommandQueue`; the GTK side drains them in `housekeeping`.
- [ ] `partials/video_source_latency.tpl`: the button row and status box. Hidden for the Media Gallery by `ofVideoSourceToggle`; disabled with a reason row for no display or no decoded video. Progress is an info box with `.modal-spinner`; the result box uses the level from the table above; a failure is an error box with the reason and one next step. The poll is operator-started (`every 1s [!document.hidden], poll-shown from:body`) and stops when the run ends. No `role="alert"` on the polled box.
- [ ] Availability read from runtime stats: `hud` present, `video.phase` is `DECODING`, `video.source_type` not the gallery.
- [ ] Tests: `tests/test_web_server.py` (start, stop, status for each state; start while unavailable answers 409 with the reason); `tests/test_web_poll_gating.py` and `tests/test_short_lived_polls_js.py` pass; `tests/test_status_language.py` passes; `tests/test_web_templates.py` (row hidden for the gallery, disabled for no display and no video).

### Diagnostics (`web/diagnostics.py`)
- [ ] Publish the last successful result in runtime stats; render it in A3 (runtime state) under the video block: median, spread, sample count, age and source type, or "not measured".
- [ ] Tests in `tests/test_web_diagnostics.py`: a stored result renders; none renders "not measured"; a failed run leaves the previous success in place.

### Docs and copy
- [ ] `openfollow/web/help/video_source.md`: a **Measure latency** paragraph: point the camera at the Operator Screen, the screen flashes for about 15 s, what the number includes (camera, encode, network, buffering, decode, station monitor), what the levels mean, what to try when no flash is seen.
- [ ] `docs/architecture/video.md`: the probe, the luma hook, the one-clock rule.
- [ ] `docs/architecture/diagnostics.md`: the A3 row.

### Verify
- [ ] `make ci-remote` green.
- [ ] On a Pi with a USB camera and an RTSP camera pointed at the Operator Screen: the result is stable within one frame across three runs, and an added SRT latency of 500 ms shows up as +500 ms.
- [ ] On macOS with the Cairo fallback: a run completes and matches the GL path within one refresh.

## Out of scope

- A timecode or continuous latency monitor during a show; dropped-frame counting.
- Flashing a browser tab for stations with no display.
- Splitting the number into camera, network and station shares.
- Storing a history of results.
