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
                    #   or the Stage / Concert / Festival / Grey defaults from media_store.py – always available
```

The **Media Gallery** (`MediaGalleryInput`, `input_id` `testpattern`) plays a device-local image / looping VP8 clip / bundled default selected via `testpattern_selected_media`. Its library lives in [`video/media_store.py`](../../openfollow/video/media_store.py) (storage resolver, id rules, hard cap, magic-byte + `GstDiscoverer` validation, thumbnails); the web management routes (`/video-input/testpattern/*` in [`web/routes.py`](../../openfollow/web/routes.py)) handle list / upload / capture / select / download / delete. The selection is device-local (web-only via `ConfigField.device_editable=False`; stripped on export, preserved on import) and silently falls back to Stage when unresolvable. The **Concert** default (`default:concert`, `concert_default.jpg`) is a concert stage seen from the upstage back truss through a 2.8 mm M12 lens (Barrel / fisheye -0.37, Corner correction -0.17): a photorealistic rendering of the scene `scripts/gen_concert_default.py` draws, every line where the drawing puts it, so the calibration the script writes holds for it (the script writes its own drawing only to a path it is given); the help drawer lists the camera it was drawn with, so a station set to it draws its grid on the deck seams. The **Festival** default (`default:festival`, `festival_default.jpg`) is a festival stage seen from the front truss, rendered by `scripts/gen_festival_default.py` through a 2.8 mm lens (Barrel / fisheye -0.31, Corner correction -0.01) about a lens centre 28 px right of and 16 px above the picture's centre, which a station is set to by hand; the help drawer lists its camera. Without its file a bundled picture is not offered, and a selection of it falls back to Stage. Each bundled picture keeps the calibration it was drawn with beside it (`stage_default.toml`, `concert_default.toml`, `festival_default.toml`: `[camera]` and `[grid]` in `config.toml` form, the lens centre only where the picture has one), written by its generator through `scripts/gallery_calibration.py`; nothing reads it at runtime, and `tests/test_gallery_calibration.py` checks that it loads unchanged and puts the grid on the picture's tape. Clips loop **seamlessly** via a SEGMENT seek: `on_bus_async_done` arms the loop once (a flushing `FLUSH | SEGMENT` seek), so the clip posts `SEGMENT_DONE` instead of `EOS` at the end, and `on_bus_segment_done` queues the next pass with a non-flushing seek – the sink never stops, so there is no end-of-clip freeze and the stall watchdog never trips. `GstPipeline` aggregates every sink's segment-done into ONE `SEGMENT_DONE` posted by the pipeline itself (`message.src` is the pipeline, not the sink), so the receiver forwards it unfiltered – do NOT re-add a per-sink (`shared_videosink`) src filter, it drops the aggregated message and the clip stalls. `on_bus_eos` stays a fallback that re-arms the loop (a stray EOS is not a disconnect); do NOT loop by a flushing EOS seek: it freezes the clip at its end and flashes "No Signal". WebP image support relies on `gstreamer1.0-plugins-bad` (webpdec/webpenc, via `libgstwebp`); it is treated as always-present and pinned as a dedicated dependency by [`tests/test_system_dependencies.py`](../../tests/test_system_dependencies.py) so a pipeline refactor can't silently drop it.

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
- `frame_meter_element_name` → the instance name of the leaky queue that first holds whole frames, metered on its sink pad against the source limits (see [Source limits](#source-limits-videosource_limitspy)); `None` for an input that plays only media within them
- `source_element_name` → the **instance name** of the element that first produces bytes from the source (`"rtspsrc"`, `"udpsrc"`, `"media_source"`, …), or `None` for an input with no such boundary. The receiver probes it for the first buffer → `DATA_ARRIVING`. `TestSourceElementDeclaration` in [`tests/test_video_input_plugins.py`](../../tests/test_video_input_plugins.py) builds each plugin's pipeline and asserts the name resolves, so a rename inside `create_pipeline` can't silently detach the probe
- `source_kind` → `SourceKind.REMOTE` (dials a host: rtsp, srt), `NAMED` (finds a source by name: ndi), `LISTENER` (waits at an address: rtp) or `LOCAL` (hardware or a file on this box). It picks the **wording** of the sentence and the action, so an NDI source is not told to check an address and port it does not have, and a USB camera is not told to check a sender. Defaults to `LOCAL`, which names no address, port or sender - a plugin that forgets is vague, never wrong
- `on_bus_error(error)` → optional; the failed attempt's `BusError`, handed over in `_schedule_reconnect` before the next attempt is built. RTSP reads it to change the transport the retry asks for
- `observe_progress(pipeline, report)` → optional; report protocol progress only that plugin can see. RTSP wires `rtspsrc::on-sdp` → `TRANSPORT_UP` + `STREAM_DESCRIBED`. The receiver keeps the furthest phase reported, so a duplicate or out-of-order report is harmless

## Receiver (`video/receiver.py`) – generic orchestrator
Delegates protocol-specific work to the active plugin. Retains shared infrastructure:
- `_build_overlay_tail()` – links the plugin tail directly to `shared_videosink` (no `cairooverlay`; the HUD is drawn apart from the pipeline – see [hud](hud.md#hud-rendering-decoupled-from-buffer-flow))
- `_create_placeholder_pipeline()` – black-frame "No Signal" fallback
- Shared sink management (detach/reattach across pipeline switches, `_prepare_sink`). The shared sink is `gtksink`, or with the GL display the display's own sink bin, whose widget is the GL area (see [hud](hud.md#the-gl-display)): the receiver calls `release_frames` before every NULL transition, `accept_frames` in `_prepare_sink`, and falls back to `gtksink` through `drop_display`
- Connection timeout, reconnection scheduling (driven by plugin's `ReconnectPolicy`)
- First-frame / caps detection via downstream pad probes (`_on_pad_event` for the caps event, `_on_sink_buffer` → `_handle_video_connected` on the first buffer)
- Display-frame count: `_create_shared_sink` puts a buffer probe on the new shared sink's `sink` pad (`gtksink`'s, or the ghost pad of the GL display's sink bin) that tells the `set_display_frame_listener` listener of every frame, the placeholder's included, for the Operator Screen metrics (see [hud](hud.md#what-the-operator-screen-costs-apistats--hud)). It only counts, never drives drawing. The listener belongs to the receiver, so the platform sink `drop_display` builds is counted too
- Source-byte observation: `_attach_source_probe()` puts a one-shot buffer probe on the plugin's `source_element_name`. Attached **per pipeline** (the source element is rebuilt on every reconnect), unlike the sink probes which attach once for the shared sink's lifetime. A source with no static `src` pad (`rtspsrc`) is followed via `pad-added`. The same first buffer records `source_format` from that pad's caps (a pixel format for raw video, else the media type), read from the caps **string** because the structure getters are what GStreamer 1.26.2 broke; the sink probe sits after `videoconvert` and never sees it. It is logged once per connection and published as `video.source_format`
- Source discovery scheduling (calls plugin's `discover_sources()`)
- Source selection state management (generic, checks `InputCapabilities.has_source_selection`)
- A lost source comes back by itself: when an input with a picker and discovery gives up, it clears its source field but keeps the name in `_awaited_source`. Discovery keeps running, with the picker open or closed with BACK (which keeps the verdict), and each pass is handed to the main loop (`_on_awaited_pass`): the first that lists the name reconnects to it. A source that sent no video since it was set (announced but unreachable, or a camera that is listed but will not start) is retried only after `_LISTED_RETRY_PASSES` listed passes, or on the first listing after a pass that missed it, so the picker stays usable between tries. Picking another source (`set_source`), a new input or config (`swap_input`), `release_source` and `stop` end the wait; a pass queued before that is dropped. NDI, USB camera, Pi Camera and the macOS camera share this path; a Pi Camera or macOS camera left on the default camera (empty name) has no name to wait for.
- `source_name`, read every frame for the Operator Screen, keeps the plugin's `get_source_label` until the input, its config or the pipeline changes; `create_pipeline` drops it, since an input resolves what it plays as it builds (the Media Gallery its selection, on disk)

## NDI plugin (`video/inputs/ndi.py`)
```
ndisrc → ndisrcdemux → ndi_video_queue (leaky) → videoconvert → shared_videosink
```
- Source discovery via ctypes → libndi SDK
- Source selection overlay activated by N key or controller back button
- Forces pipeline latency to 0 on ASYNC_DONE
- Reconnect: max 1 attempt, then fallback to source selection; the source is reconnected once discovery announces it again

## SRT plugin (`video/inputs/srt.py`)
```
srtsrc → pre_queue → decodebin → post_queue → videoconvert → shared_videosink
```
- `srtsrc`: `mode=caller`, `wait-for-connection=True`, `latency=125ms`, **`auto-reconnect=False`** (left on, it retries internally and the failure never reaches the bus)
- `pre_queue` (RTP's too, `configure_pre_decode_queue`) is leaky and bounded by **time**, 300 ms, never by a buffer count: its buffers are packet-sized, so a count holds milliseconds and a decoder hiccup drops compressed data, which breaks the picture into blocks until the next keyframe. A byte bound (8 MiB) holds it where buffer timestamps cannot (none, or a step back). It fills only while the decoder is behind, and the picture then runs up to 300 ms late until the decoder catches up, since the sink does not sync.
- `srt_passphrase`, when set, drives the `passphrase` property and the URL's own `?passphrase=` is stripped first, so one field answers "which key is this stream encrypted with". Blank leaves the URL path untouched
- Hardware decoder priority boosting (V4L2 > avdec > openh264)
- HEVC stays off the Raspberry Pi hardware decoder (`v4l2slh265dec`, ranked out on every Pi): when a reference picture is missing (packet loss, a stream joined between keyframes) `rpi_hevc_dec` rejects the slice parameters, and stopping the pipeline after that error crashes the process (raspberrypi/linux #7612; still so on kernel 6.18.56 with GStreamer 1.26.2-3+rpt4). Kernels before 6.18.49 can instead block `VIDIOC_STREAMOFF` in D state until a reboot (raspberrypi/linux #7609). Only a Raspberry Pi (`/proc/device-tree/model`) is guarded: another board's stateless HEVC decoder is a different driver. `avdec_h265` decodes 1080p30 for about 0.6 of a Pi 5 core and conceals lost references. The rank change is process-wide and is applied before each SRT, RTSP and RTP decodebin (`inputs/_hevc_guard.py`); no other input decodes HEVC (NDI decodes in its SDK, USB decodes at most MJPEG, the Pi Camera is raw, the Media Gallery plays VP8 only)
- Preserves decoder latency on ASYNC_DONE (do NOT force 0)
- `pad-added` on decodebin: **do NOT filter by pad name** (uses `src_0`, not `video_0`)
- Reconnect: 3 retries with 8s first-frame timeout, then no-signal placeholder fallback

## RTP plugin (`video/inputs/rtp.py`)
```
udpsrc → rtpjitterbuffer → pre_queue → decodebin → post_queue → videoconvert → shared_videosink
```
- `rtpjitterbuffer`: `latency` 0, `drop-on-latency`; left out when the element is missing. `latency` 0 leaves its queue unbounded, so the bounded `pre_queue` (as on SRT) sits **after** it: a decoder stall fills `pre_queue`, which drops the oldest, and never piles up in the jitterbuffer to come out late. The jitterbuffer pushes from its own thread, so `udpsrc` keeps reading the socket through a stall

## RTSP plugin (`video/inputs/rtsp.py`)
```
rtspsrc → decodebin → post_queue (leaky) → videoconvert → shared_videosink
```
- `protocols` allows TCP + UDP + multicast, **except for a name dialled over IPv6 and a host whose attempt failed on the address family, which get TCP interleaved only**. `rtspsrc` opens a stream's UDP ports for the address type of the server's session description (`c=IN IP4` / `IP6`, IPv4 when there is none), not for the family the control connection reached the server on, so a server reached over IPv6 that describes IPv4 (or nothing) gets IPv4 ports, and the RTCP `udpsink` then refuses its address: `gst-resource-error-quark:13`, `Invalid address family` in the debug string, posted as a pipeline error after `on-sdp`. Verified against 1.28.7 on loopback with a stub server, for a name and an IPv6 literal alike; the 1.26 source picks the family the same way (`gst_rtspsrc_do_stream_connection`)
  - **Up front:** `_pin.dials_ipv6` asks the bounded resolver (`_RESOLVE_WAIT_S`) and reads the family of its first answer, the one the system's address selection (RFC 6724, against its own routes) puts first and the connection dials, so a dual-stack camera reached over IPv4 keeps UDP and multicast; a name still resolving keeps every transport this attempt and the next attempt reads the answer. An address literal is not looked up
  - **After the fact:** the connection can land on the other family anyway (the first address refused or unreachable, the next one dialled; an IPv6 literal whose server describes IPv4). `on_bus_error` reads `Invalid address family` in the failed attempt's debug string and keeps that host on TCP interleaved for every later attempt and heal of this input; a changed URL host, or an input rebuilt by a saved change to the source's own settings or type, tries every transport again
- `tcp-timeout` 10 s so the element gives up before the connection timeout, `latency` 0, `drop-on-latency`, `buffer-mode` none; only the video track is linked into `decodebin`, since an NVR may list its audio track first

## USB Camera plugin (`video/inputs/v4l2.py`)
```
v4l2src → [src_caps] → post_queue → [jpegdec] → [videoscale] → videorate → capsfilter → videoconvert → shared_videosink
```
- `src_caps` pins a mode from the camera's own list (`VIDIOC_ENUM_FMT`, `_ENUM_FRAMESIZES`, `_ENUM_FRAMEINTERVALS`), never a fixed size: a capture card lists only the incoming signal's resolution. A UVC camera may list raw modes its USB link cannot carry; those frames arrive cut short and read as mostly green.
- `_choose_mode` puts the size first: the render size, or for native the largest size listed. Among the modes covering it, it takes a raw mode the link carries, else MJPEG, at the fastest rate up to the requested one; `videorate` repeats frames up to the request. With nothing covering the size it takes the largest of the modes at the fastest rate up to the requested one. `jpegdec` decodes MJPEG 1080p30 for about 0.43 of a Pi 5 core; the cost grows with the picture's area, so native size on a 4K MJPEG camera costs several times that.
- The link budget is the isochronous rate for the USB device's sysfs `speed`, less the 12-byte UVC header in every packet: 24.3 MB/s on USB 2.0 (three 1012-byte payloads per microframe). A webcam's raw 848x480 at 30 fps needs 24.4 MB/s and arrives cut short. A device off USB has no limit.
- A list that cannot be read in full (an ioctl error, sizes or rates given as ranges) or holds no mode the station can play leaves the source open to negotiate its own mode. An attempt whose pinned mode failed before showing a picture, while the camera's node is still there, leaves that device's next attempt open once (`on_bus_error`, with a buffer probe on `convert` marking the picture). A pin that showed a picture, or an unplugged camera, keeps its pin.

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
box and the web boxes (Live Statistics, the Video Source panel and the Setup
Wizard's Video Source step, the last two one partial) carry the **sentence plus
the action**, and never the
element's own wording - every renderer uses `failure_text or error_message`, so
the raw text appears only when there is no classification to replace it. It
stays in `/api/stats` and the diagnostics bundle, which is what support reads
against.

**GStreamer does not report everything the taxonomy can express.** Against 1.26, a
bad RTSP path comes back as
`gst-resource-error-quark:13` with `SDP contains no streams`, not
`RESOURCE_NOT_FOUND`. Check what the element actually emits before adding a
mapping - a code that looks obvious from the enum may never be sent. RTSP's
UDP address-family mismatch is that same code with `Invalid address family` in
the debug string; it reads by the phase like an open failure (after `on-sdp`,
`NO_DATA`), and the retry runs over TCP interleaved (see the RTSP plugin).

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
negotiation. `v4l2.py` pins a mode from the camera's own list instead (see
[USB Camera](#usb-camera-plugin-videoinputsv4l2py)).
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

## Source limits (`video/source_limits.py`)
A station is sized for Full HD at 30 fps. `over_limits(width, height, fps)` is the **one** rule, called by the Media Gallery's upload check (`validate_video_probe`) and by the live meter, so the two cannot disagree: the longer side at most `MAX_LONG_SIDE` (1920), the shorter at most `MAX_SHORT_SIDE` (1080), so a 1080x1920 portrait passes and a 2560x1080 ultrawide does not; the rate at most `MAX_FPS` + `FPS_TOLERANCE` (30.5, which absorbs a declared 30000/1001). A zero, negative or NaN value is unknown and exceeds nothing (a variable-rate clip declares 0 fps); a non-number raises `TypeError`.

The live feed is judged from the frames that **arrive**, never from caps a source declares. Each plugin names in `frame_meter_element_name` the leaky queue that first holds whole frames (`post_queue`, NDI's `ndi_video_queue`); `_attach_frame_meter` puts a CAPS event probe and a buffer probe on its **sink** pad, so a frame is counted before that queue can drop it. The size is always the source's own. For RTSP, SRT and RTP the queue sits after `decodebin`, so the rate is what reaches the decoder's output: a decoder that falls behind back-pressures the jitterbuffers and the pre-decode queue, which drop upstream of any parser, and a fast source can then read below the limit. The Media Gallery declares none: it plays only media within the limits. The placeholder is never metered.
- `FrameMeter` is per attempt: `create_pipeline` makes a new one and `_reset_video_flow_state` drops it, and `source_limits` reads nothing while the placeholder is up, so a reconnect measures afresh, a source change clears the verdict, and a late probe of an old pipeline writes only to its own orphaned meter. The probes run on the streaming thread; per frame they bump a counter and read the clock. Once per `SLOT_S` (1 s) the meter closes a slot and replaces `reading` (an immutable `SourceReading`) whole, which the frame loop and the web thread read without a lock.
- The rate is frames over the time between the frames that closed the first and last of `WINDOW_SLOTS` (5) slots, so only those two frames' arrival jitter reaches it: about 80 ms of it moves a 30 fps reading by 0.5. Nothing is judged until the window spans; a verdict changes only after `CONFIRM_READINGS` (3) readings in a row disagree with it, each with different end frames, so a steady 30 fps feed never reads as 31 and the row does not flap. `reading.fps` is the rate an over verdict was reached at, held while it lasts (the row and the box show it, and the box's `hx-preserve` id is a hash of its text), else the latest reading within the limit. A USB camera is metered in the mode it is read in, ahead of `jpegdec` / `videoscale` / `videorate`, which can be past its Render resolution and FPS (`_choose_mode` takes what the camera lists; a capture card lists its incoming signal), so the `LOCAL` step names the camera or the signal into the capture card as well as the settings. The size is judged from the first frame after its caps.
- Surfaces: the HUD's `_status_flags["video_limits"]` error row (`limits_row`: `Video 3840×2160 > Full HD, 60 fps > 30 fps`), `/api/stats` `video.limits` (`limits_snapshot`: `exceeded` holds the `OverLimit` ids, a wire interface like `video.failure`), the box on the Video Source section and Statistics → Video (`limits_sentence` plus `limits_action`, which names the station's own resolution and FPS settings for a `LOCAL` camera and the camera or encoder otherwise), and the bundle's `source limits` line. The video keeps playing; this reports, it does not refuse.

## Snapshot provider (`video/preview.py`)
`SnapshotProvider` captures on-demand full-resolution JPEG frames for the setup wizard. It connects to a `tee` → `valve` → `queue` → `videoconvert` → `jpegenc` → `appsink` branch in the receiver pipeline (no downscale). The valve sits **ahead of** the queue in this branch and the preview's: closed, it drops frames on the tee's thread, so an idle branch wakes no streaming thread. The snapshot is pulled lazily – no background polling. The last captured frame is cached so repeated requests don't block on GStreamer. Wired via `full_snapshot_provider` callback on `ConfigWebServer`.
- `get_snapshot()` **serialises the blocking `try_pull_sample` → extract under a dedicated `_pull_lock`** so concurrent HTTP requests can't race on the single appsink. The `_valve` / `_jpeg_bytes` references are guarded by a separate short-lived `_state_lock` (also taken by `set_valve`), which is **not** held across the ~500 ms pull – so a source-swap / pipeline rebuild's `set_valve` never blocks on an in-flight encode. Keep the pull serialised, but don't move it under `_state_lock`.

## Connection lifecycle
- `play()` → `_start_connection_timeout()` (uses plugin's `connection_timeout`)
- On error: `_schedule_reconnect()` with exponential backoff (plugin's `ReconnectPolicy`)
- `_is_placeholder_pipeline`: True when showing "No Signal" placeholder
- `STATE_CHANGED` check: sink name is `"shared_videosink"` (not `"videosink"`); connected status is set on first frame/caps, not on PLAYING alone

## GStreamer SRT gotchas
- `set_latency(0)` only works after ASYNC_DONE, not at pipeline creation
- `set_state(PLAYING)` returning ASYNC is normal for SRT caller mode
