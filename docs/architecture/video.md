# Video pipeline

Covers `video/` (receiver, inputs, failure taxonomy, preview), the Pi Camera setup in `privilege/camera_config.py`. The rules that apply everywhere are in [CLAUDE.md](../../CLAUDE.md).

## Plugin architecture (`video/inputs/`)
Each video protocol is a self-contained file in `video/inputs/` that subclasses `VideoInputBase`:

```
video/inputs/
    _base.py        # ABC: VideoInputBase, ConfigField, InputCapabilities, ReconnectPolicy
    __init__.py     # Auto-discovery registry (pkgutil) + is_available() filter
    ndi.py          # NDI plugin (pipeline, ctypes discovery, web UI)
    srt.py          # SRT plugin (pipeline, decoder config, web UI)
    rtsp.py         # RTSP plugin (rtspsrc, auto-codec, multi-transport)
    rtp.py          # RTP plugin (udpsrc, manual codec selection)
    picam.py        # Raspberry Pi camera plugin (libcamerasrc, CSI/MIPI); caps pin format=I420
    v4l2.py         # USB camera / capture card plugin (v4l2src, UVC, Linux-only)
    avf.py          # USB camera / capture card plugin (avfvideosrc, macOS-only)
    testpattern.py  # Media Gallery source (id "testpattern"): plays a stored image / VP8 clip
                    #   or the Stage / Grey defaults from media_store.py – always available
```

The **Media Gallery** (`MediaGalleryInput`, `input_id` `testpattern`) plays a device-local image / looping VP8 clip / bundled default selected via `testpattern_selected_media`. Its library lives in [`video/media_store.py`](../../openfollow/video/media_store.py) (storage resolver, id rules, hard cap, magic-byte + `GstDiscoverer` validation, thumbnails); the web management routes (`/video-input/testpattern/*` in [`web/routes.py`](../../openfollow/web/routes.py)) handle list / upload / capture / select / download / delete. The selection is device-local (web-only via `ConfigField.device_editable=False`; stripped on export, preserved on import) and silently falls back to Stage when unresolvable. Clips loop **seamlessly** via a SEGMENT seek: `on_bus_async_done` arms the loop once (a flushing `FLUSH | SEGMENT` seek), so the clip posts `SEGMENT_DONE` instead of `EOS` at the end, and `on_bus_segment_done` queues the next pass with a non-flushing seek – the sink never stops, so there is no end-of-clip freeze and the stall watchdog never trips. `GstPipeline` aggregates every sink's segment-done into ONE `SEGMENT_DONE` posted by the pipeline itself (`message.src` is the pipeline, not the sink), so the receiver forwards it unfiltered – do NOT re-add a per-sink (`shared_videosink`) src filter, it drops the aggregated message and the clip stalls. `on_bus_eos` stays a fallback that re-arms the loop (a stray EOS is not a disconnect); do NOT loop by a flushing EOS seek: it freezes the clip at its end and flashes "No Signal". WebP image support relies on `gstreamer1.0-plugins-bad` (webpdec/webpenc, via `libgstwebp`); it is treated as always-present and pinned as a dedicated dependency by [`tests/test_system_dependencies.py`](../../tests/test_system_dependencies.py) so a pipeline refactor can't silently drop it.

**Adding a new protocol:** create `video/inputs/<name>.py` with a `VideoInputBase` subclass. No other files need changes – the registry auto-discovers it, the web UI renders its fields, the receiver delegates to it.

**Platform availability:** plugins declare `is_available() → (bool, reason)` so the picker can hide backends that won't run on the current host (e.g. `v4l2src` is Linux-only, `avfvideosrc` is macOS-only). `get_registry()` returns every discovered plugin; `get_available_registry()` / `get_available_input_ids()` filter by `is_available()` – UI pickers and the wizard use the filtered view, while discovery-contract tests use the full registry. Plugins must still register on every platform; `is_available()` only gates display, not import.

Each plugin declares:
- `config_fields()` → config fields stored in config.toml
- `capabilities()` → feature flags (discovery, selection hotkey, latency handling)
- `reconnect_policy()` → max attempts, backoff, timeout, fallback behavior
- `create_pipeline()` → GStreamer element chain
- `web_ui_html()` → HTML fragment for the web settings form
- `web_routes()` → additional HTTP endpoints (e.g. `/video-input/ndi/sources`)
- `is_available()` → `(bool, reason)`; defaults to `(True, "")`. Override when the plugin needs an OS or GStreamer element that isn't universally present (e.g. `v4l2src` on Linux, `avfvideosrc` on macOS, the `ndisrc` plugin from gst-plugin-ndi).
- `source_element_name` → the **instance name** of the element that first produces bytes from the source (`"rtspsrc"`, `"udpsrc"`, `"media_source"`, …), or `None` for an input with no such boundary. The receiver probes it for the first buffer → `DATA_ARRIVING`. `TestSourceElementDeclaration` in [`tests/test_video_input_plugins.py`](../../tests/test_video_input_plugins.py) builds each plugin's pipeline and asserts the name resolves, so a rename inside `create_pipeline` can't silently detach the probe
- `source_kind` → `SourceKind.REMOTE` (dials a host: rtsp, srt), `NAMED` (finds a source by name: ndi), `LISTENER` (waits at an address: rtp) or `LOCAL` (hardware or a file on this box). It picks the **wording** of the sentence and the action, so an NDI source is not told to check an address and port it does not have, and a USB camera is not told to check a sender. Defaults to `LOCAL`, which names no address, port or sender - a plugin that forgets is vague, never wrong
- `observe_progress(pipeline, report)` → optional; report protocol progress only that plugin can see. RTSP wires `rtspsrc::on-sdp` → `TRANSPORT_UP` + `STREAM_DESCRIBED`. The receiver keeps the furthest phase reported, so a duplicate or out-of-order report is harmless

## Receiver (`video/receiver.py`) – generic orchestrator
Delegates protocol-specific work to the active plugin. Retains shared infrastructure:
- `_build_overlay_tail()` – links the plugin tail directly to `shared_videosink` (no `cairooverlay`; HUD is drawn on a `Gtk.DrawingArea` above the gtksink widget – see [hud](hud.md#hud-rendering-decoupled-from-buffer-flow))
- `_create_placeholder_pipeline()` – black-frame "No Signal" fallback
- Shared gtksink management (detach/reattach across pipeline switches)
- Connection timeout, reconnection scheduling (driven by plugin's `ReconnectPolicy`)
- First-frame / caps detection via downstream pad probes (`_on_pad_event` for the caps event, `_on_sink_buffer` → `_handle_video_connected` on the first buffer)
- Source-byte observation: `_attach_source_probe()` puts a one-shot buffer probe on the plugin's `source_element_name`. Attached **per pipeline** (the source element is rebuilt on every reconnect), unlike the sink probes which attach once for the shared sink's lifetime. A source with no static `src` pad (`rtspsrc`) is followed via `pad-added`. The same first buffer records `source_format` from that pad's caps (a pixel format for raw video, else the media type), read from the caps **string** because the structure getters are what GStreamer 1.26.2 broke; the sink probe sits after `videoconvert` and never sees it. It is logged once per connection and published as `video.source_format`
- Source discovery scheduling (calls plugin's `discover_sources()`)
- Source selection state management (generic, checks `InputCapabilities.has_source_selection`)

## NDI plugin (`video/inputs/ndi.py`)
```
ndisrc → ndisrcdemux → ndi_video_queue (leaky) → videoconvert → shared_videosink
```
- Source discovery via ctypes → libndi SDK
- Source selection overlay activated by N key or controller back button
- Forces pipeline latency to 0 on ASYNC_DONE
- Reconnect: max 1 attempt, then fallback to source selection

## SRT plugin (`video/inputs/srt.py`)
```
srtsrc → pre_queue → decodebin → post_queue → videoconvert → shared_videosink
```
- `srtsrc`: `mode=caller`, `wait-for-connection=True`, `latency=125ms`, **`auto-reconnect=False`** (left on, it retries internally and the failure never reaches the bus)
- `srt_passphrase`, when set, drives the `passphrase` property and the URL's own `?passphrase=` is stripped first, so one field answers "which key is this stream encrypted with". Blank leaves the URL path untouched
- Hardware decoder priority boosting (V4L2 > avdec > openh264)
- Preserves decoder latency on ASYNC_DONE (do NOT force 0)
- `pad-added` on decodebin: **do NOT filter by pad name** (uses `src_0`, not `video_0`)
- Reconnect: 3 retries with 8s first-frame timeout, then no-signal placeholder fallback

## Stream credentials (RTSP / SRT)
`rtsp_user` / `rtsp_password` / `srt_passphrase` are rendered as a login block under each plugin's URL (password inputs) and drive the element properties directly. The URL's own credential is **stripped before** `location` / `uri` is handed over – `rtspsrc` tries URL userinfo first and only then falls back to `user-id` / `user-pw`, so leaving it in would let a stale URL credential outrank the form. Blank fields leave the existing URL-userinfo path working untouched.

`ConfigField(strip=False)` marks a credential so the web-save path keeps its edge whitespace, where every other string field is trimmed: the whitespace can be part of the secret and is invisible in a password field, so trimming it fails authentication with nothing on screen to explain why.

Every surface that shows, logs or exports a stream URL redacts it first: see [diagnostics](diagnostics.md#credential-redaction-uri_redactionpy).

## Video failure taxonomy (`video/failure.py`)

Four outcomes that would otherwise all read as the same "no video received after 8s":
**nothing ever answered**, **it answered but sent no media**, **media arrives
but never decodes**, and **it was flowing and stopped**. Each sends the
operator to different equipment.

`ConnectionPhase` (`STARTING` → `TRANSPORT_UP` → `STREAM_DESCRIBED` →
`DATA_ARRIVING` → `DECODING`) records how far an attempt got. `DATA_ARRIVING`
is the load-bearing one (bytes out of the source element, which every protocol
has), and `ReceiverStateMachine.note_phase` keeps the **furthest** reached
because the probes fire from different threads and arrive out of order.

`classify_failure(phase, domain, code, message, debug)` names the failure, and
the phase is what decides an ambiguous error: the same
`GstResourceError.OPEN_READ` is `UNREACHABLE` before any bytes arrive and
`STALLED` after them. **`debug` is not optional detail** - the refusal marker
and the empty-SDP marker both live in GStreamer's debug string, not its message,
so a caller that drops it discards the evidence the mapping depends on. A known
domain carrying a code with no rule (the generic `FAILED = 1`) stays `UNKNOWN`
rather than landing in a neighbouring bucket. The one exception is observed on
hardware: a caps negotiation failure arrives as `gst-stream-error-quark:1` with
`reason not-negotiated` in the debug string, and **before any video** that is
`UNSUPPORTED_MODE` (the source cannot deliver the format, size or frame rate
asked for); after video it stays `STALLED`.

**Classify before `_reset_video_flow_state`.** `_schedule_reconnect` clears the
phase it classifies from, so the verdict is computed at the top of that method;
computing it afterwards reads every failure as a cold start.

**`ReceiverStateMachine.phase` is the furthest the *feed* ever reached, not the
attempt.** It survives `reset_video_flow` (which clears only per-attempt state
like `video_flow_detected`) and is cleared solely by `forget_video_history` on a
source change. A retry knows nothing, so classifying or publishing from a
per-attempt phase redescribes every dropped feed as one that was never
reachable.

There is deliberately **one** such concept: a separate "had video" latch beside
a per-attempt phase would disagree with it the moment a retry landed.

**Only `DECODING` is evidence video arrived** - nothing but `mark_frame_received`
sets it. `DATA_ARRIVING` is bytes out of the source element, which a feed
carrying an undecodable payload produces just as readily, so it means "it
answered", not "it worked". Because only RTSP reports `TRANSPORT_UP` /
`STREAM_DESCRIBED`, `DATA_ARRIVING` is also the only route to `NO_DATA` for
every other input.

One module owns the enum and both text maps so the five surfaces that render
them cannot drift. `VideoFailure` values are a **wire interface** –
`/api/stats` publishes `video.failure` for support tooling, so renaming a
member breaks a consumer's matching.

Sentences state **only what this station observed**, never what the far end
did. "sent no video" would assert the camera sent nothing when it may have been
sending into a blocked path; a stall is "stopped arriving", not
"stopped sending". Sentences otherwise **describe the observation and stop**,
and `failure_action` carries
**one short next step** beside them - two fields, never blurred into one, so a
reader quoting the observation into a support thread does not carry our advice
with it. Anything longer than a line belongs in the website docs, which an
operator on a stage cannot open. The pipeline's own wording ("Could not open
resource for reading and writing.") is **not** shown to an operator: it reads as
a second, unrelated fault and nothing can be done with it. It stays in
`/api/stats` and the diagnostics bundle, and stands in for the sentence only
where there is no classification at all. `where` must already be redacted – it reaches the HUD and
`/section/statistics`. `UNKNOWN` renders **no** sentence on any surface: it
would sit above the element's own wording and contradict it.

**The phase is published inside `_StatusSnapshot`, with the verdict it
explains.** Publishing it separately from `failure` would let a reader pair one
generation's phase with another's verdict.

**Surfaces:** the top-right status badge carries the *chip* only
(`_status_flags["video_failure"]`, ~40 characters per row). The Settings error
box and both web boxes carry the **sentence plus the action**, and never the
element's own wording - every renderer uses `failure_text or error_message`, so
the raw text appears only when there is no classification to replace it. It
stays in `/api/stats` and the diagnostics bundle, which is what support reads
against.

**GStreamer does not report everything the taxonomy can express.** Against 1.26, a
bad RTSP path comes back as
`gst-resource-error-quark:13` with `SDP contains no streams`, not
`RESOURCE_NOT_FOUND`. Check what the element actually emits before adding a
mapping - a code that looks obvious from the enum may never be sent.

**`INTERFACE_DOWN` and `WRONG_INTERFACE` never come from the bus.** Only the
video input pin's preflight produces them, before anything is dialled, so
`classify_failure` has no rule for either. A refused build builds nothing, not
even the placeholder, and retries on the input's own backoff; the placeholder
appears only once the reconnects run out, as for any failure. `error_message`
carries the interfaces the sentence cannot name.

**`REFUSED` has no observed producer.** `rtspsrc` reports a refused connection
as `Failed to connect. (Generic error)`, the errno discarded inside GStreamer's
RTSP stack, confirmed against a socket probe raising `ECONNREFUSED` for the same
endpoint. `srtsrc` posts a real error once internal retry is off (below), but
reports every cause identically. The remaining inputs are a listener, a
discovery-by-name protocol and local devices, none of which can be refused. The
member ships unreachable and its text match is dead.

**The element must give up before our watchdog does**, or the pipeline is torn
down before it can say why and the failure is classified from the phase alone.
The defaults do not: `rtspsrc.tcp-timeout` is 20 s and `srtsrc.auto-reconnect`
retries forever. **The budget each is measured against is per plugin**, not one
number - RTSP allows 15 s and sets `tcp-timeout` to 10 s inside it; SRT allows
8 s and turns internal retry off, which is what puts an error on the bus at
all. **`rtspsrc.timeout` is deliberately left alone**: it is the live
UDP-to-TCP fallback trigger, armed for the whole session, so lowering it
downgrades a working feed to TCP-interleaved on any brief gap. `tcp-timeout` is
likewise not the connect deadline but the wait for each RTSP response, so it
stays generous enough for a busy NVR to answer DESCRIBE. **`udpsrc.timeout` is
left alone too**: the stall watchdog already covers socket silence and honours
the operator's `stall_timeout` including its `0 = off`, and a second reporter at
the same window only races it for the message. Read what a property does before
setting it.
`TestTheElementGivesUpFirst` keys on `source_element_name` and `SourceKind`, so
a networked plugin absent from its map fails rather than passing silently.

**SRT reports *that* it failed, never *why*.** With `auto-reconnect` off a real
error reaches the bus in ~3 s instead of nothing at all, but an unresponsive
listener, a wrong passphrase, a closed port and an unroutable host are all
`gst-resource-error-quark:9` with `Connection timeout (16)`. That is libsrt's
design at the caller - telling one from another for the passphrase case would be
an oracle - not a gap in the taxonomy. Do not add a mapping that pretends
otherwise.

## Pi Camera

**The Pi Camera's capsfilter pins `format=I420`.** Left open, libcamerasrc takes
the lowest-sorting fourcc it offers (raw Bayer or greyscale), not the camera's
YUV420 default: on a CM5 with libcamera 0.7.1 that negotiated a raw stream and
failed outright. Colorimetry is **not** pinned: a value the ISP adjusts can fail
negotiation. `v4l2.py` is a different mechanism (driver-preference order) and
is left open deliberately.
Cameras are listed through GStreamer's libcamera device provider (the
`gstreamer1.0-libcamera` package the pipeline needs), which shares
libcamerasrc's camera manager and so lists a camera that is already streaming.
`rpicam-apps` is not a dependency. A start that `set_state(PLAYING)` refuses
(no camera present) is classified from the element's bus error in both `play()`
branches, so a missing Pi Camera reads as not found, not unknown.

**Camera setup** (Video Source → Pi Camera) names the sensor and connector in
`config.txt` (a Compute Module never auto-detects its camera).
`privilege/camera_config.py` owns the parsing, the state read and the root CLI:
the `.deb` installs a copy of that **stdlib-only** file as
`/usr/share/openfollow/camera-setup`, run by `python3 -I`, so root never imports
from the venv (keep it stdlib-only). It rewrites an `# --- OpenFollow camera ---`
block (with `camera_auto_detect=0`) under `[all]`, comments out hand-written
camera lines, and loads/unloads the overlay live with `dtoverlay`. Markers that
don't pair up (`MalformedBlockError`) leave the file untouched and the setup
unavailable, since an unclosed block would claim every later line;
`/run/openfollow-camera` records what it loaded and the boot-time lines, so the
page can tell "running" from "takes effect after the next restart". A
boot-loaded overlay cannot be unloaded live. **Never unload under a streaming
pipeline**: the device vanishes under `libcamerasrc`, which then never reaches
NULL, and every later swap refuses. `apply_camera`'s `release` callback first
has the main loop stop the Pi Camera pipeline (`receiver.release_source()`, via
`WebCommandQueue.release_camera`); unanswered, the change waits for a restart
instead. `release_camera` drops a rebuild an earlier change left pending, or
the same housekeeping pass would restart the camera about to be unloaded. The block (`partials/camera_setup.tpl`, loaded into the Pi Camera
fragment) owns the **Camera** row: what libcamera detects plus what config.txt
names, with Raspberry Pi module names (`MODULE_NAMES`). It renders
`picam_camera_name` only when there is a choice (2+ cameras, or a saved one
that is gone), opens the setup by itself when no camera is found, and after a
live Apply re-checks after 3 s rather than reporting a success it has not seen.

## Placeholder pipeline vs source state
The "No Signal" placeholder is a black `videotestsrc` pinned at 1920x1080 @ 30 that feeds the **shared** sink, and both sink probes are attached once for that sink's lifetime – so its caps reach the same writer the real source uses. `ReceiverStateMachine.set_resolution` / `set_source_framerate` / `set_source_format` therefore refuse while `is_placeholder_pipeline`, mirroring `mark_frame_received`, and `_create_placeholder_pipeline` calls `clear_source_caps()` rather than writing its own geometry in. **Do not publish placeholder caps as source state**: `video.resolution` / `source_fps` are what the Statistics panel reports as the feed's own, and what `update_video` shapes the window from – a source that has never delivered a frame would otherwise present as a working 1080p feed and pin the window to 16:9 for the session.

`update_video` applies the aspect-ratio hint whenever the real resolution changes (tracked in `app._video_aspect`), **not** once per session: the HUD projects across the canvas while calibration is solved against the input, so a window left at a previous source's aspect ratio slides the overlay off the video with nothing in the UI to explain it. `app._video_logged` stays a one-shot latch for its log line only.

## Snapshot provider (`video/preview.py`)
`SnapshotProvider` captures on-demand full-resolution JPEG frames for the setup wizard. It connects to a `tee` → `queue` → `videoconvert` → `jpegenc` → `appsink` branch in the receiver pipeline (no downscale). The snapshot is pulled lazily – no background polling. The last captured frame is cached so repeated requests don't block on GStreamer. Wired via `full_snapshot_provider` callback on `ConfigWebServer`.
- `get_snapshot()` **serialises the blocking `try_pull_sample` → extract under a dedicated `_pull_lock`** so concurrent HTTP requests can't race on the single appsink. The `_valve` / `_jpeg_bytes` references are guarded by a separate short-lived `_state_lock` (also taken by `set_valve`), which is **not** held across the ~500 ms pull – so a source-swap / pipeline rebuild's `set_valve` never blocks on an in-flight encode. Keep the pull serialised, but don't move it under `_state_lock`.

## Connection lifecycle
- `play()` → `_start_connection_timeout()` (uses plugin's `connection_timeout`)
- On error: `_schedule_reconnect()` with exponential backoff (plugin's `ReconnectPolicy`)
- `_is_placeholder_pipeline`: True when showing "No Signal" placeholder
- `STATE_CHANGED` check: sink name is `"shared_videosink"` (not `"videosink"`); connected status is set on first frame/caps, not on PLAYING alone

## GStreamer SRT gotchas
- `set_latency(0)` only works after ASYNC_DONE, not at pipeline creation
- `set_state(PLAYING)` returning ASYNC is normal for SRT caller mode
