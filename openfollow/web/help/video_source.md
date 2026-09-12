# Video Source

Where OpenFollow receives its video feed – every detection, overlay, and position output depends on it. Saving rebuilds the pipeline within about a second; markers and PSN output keep running during the rebuild.

## Source Type

Pick the protocol or device for your setup; the fields below update to match.

- **RTSP** – most IP cameras and HDMI/SDI-to-IP encoders; codec auto-negotiated.
- **SRT** – long-haul or lossy networks and internet bridges.
- **RTP** – tightly controlled local pipelines you generate yourself (FFmpeg, hardware encoder).
- **USB Camera** – UVC webcams and USB capture cards.
- **Pi Camera** – CSI camera modules and HDMI/SDI-to-CSI adapters on the ribbon-cable port.
- **Media Gallery** – play a stored still image or short looping clip, or capture a frame from a live source; ships with a Stage scene and a Grey card for bench-testing calibration and detection without a camera.

## Settings (by Source Type)

- **RTSP URL** – the full `rtsp://` address, e.g. `rtsp://192.168.0.182:554/stream1`. Best results: 1080p/720p, 25–30 fps, H.264, ~4–8 Mbps CBR, 1 keyframe/s.
- **Username** / **Password** – the camera's login, for the many IP cameras that refuse an unauthenticated stream. A camera that needs one and does not get one never connects at all, so if the Video panel on the Overview tab reports `Disconnected` against a URL that plays in VLC, this is the first thing to check. Type the password as it is; no URL-encoding, and characters like `@` or `:` are fine here where they would break a URL. Leave both blank if the camera needs no login, or if you would rather keep credentials in the URL as `rtsp://user:pass@host/path` – that still works, but when these fields are filled in they win and any login in the URL is ignored.
- **SRT URL** – `srt://0.0.0.0:5000` to listen (listener mode) or `srt://203.0.113.10:5000` to connect (caller mode); a bare `host:port` also works.
- **Passphrase** – the SRT stream's encryption key, when the sender encrypts. Same precedence as the RTSP login: filling it in overrides a `?passphrase=` carried in the URL.
- **RTP URL** + **Encoding** – receive address, e.g. `rtp://0.0.0.0:5004` (unicast) or a `224.x–239.x` multicast address; set **Encoding** (`H264`, `H265`, `MP2T`) to match the sender.
- **USB Camera** – pick the **Device** (Scan to re-enumerate), then choose a **Render resolution** and **FPS** (default 30). The camera is read at its own native resolution and scaled to the render size: pick a preset (`2160p` / `1080p` / `720p`, default `1080p`) to downscale for lower CPU, or `Native size` to keep the device's full resolution. This works with any input, including capture cards that only output the incoming signal's resolution (e.g. a 4K HDMI feed) – those failed to connect when a fixed size was forced.
- **Pi Camera** – pick the **Camera** (Scan to re-enumerate; leave blank to auto-detect a single CSI camera), then set **Width** / **Height** (default 1920×1080) and **FPS** (default 30).
- **Media Gallery** – a device-local library of images and looping clips, managed entirely from the browser (the on-display menu can select this source but not change its content). Click a tile to make it the source; the selected tile has an amber border. The bundled **Stage** and **Grey** items can't be deleted; per-tile buttons download or delete your own media. **Upload** accepts JPEG/PNG/WebP images (normalised to ≤1080p JPEG) and **VP8 WebM** clips (≤1080p, ≤30 fps, ≤60 s, audio ignored); other codecs are rejected. Every other source shows a **Capture frame** button that saves the current clean frame into the gallery at its native resolution. A selection that no longer resolves (deleted media, an imported config from another station) falls back to Stage.

  Clips are not transcoded on-device – prepare a conforming VP8 WebM on a workstation, e.g.:

  ```
  ffmpeg -i in.mp4 \
    -vf "scale='min(1920,iw)':'min(1080,ih)':force_original_aspect_ratio=decrease,fps=30" \
    -t 60 -an -c:v libvpx -b:v 6M -crf 10 out.webm
  ```

## Connection recovery

Two safeguards keep a network feed alive without operator intervention. Both apply only to network inputs (RTSP, SRT, RTP) – USB, Pi Camera, and Media Gallery can't stall on the wire – and both take effect immediately on save. They cover two different failure modes:

- **Stall Timeout (s)** – the *silent-stall* watchdog, for a stream that is still "connected" but has quietly stopped delivering frames with no error to signal it. This is common on UDP, multicast, and some RTSP cameras: the last frame just freezes on screen and nothing recovers on its own. If no new frame arrives within this many seconds, OpenFollow tears the pipeline down and reconnects. Default `3.0`. Raise it if a healthy source has long legitimate gaps (very low frame rate, sparse keyframes) and is being reconnected unnecessarily; lower it to react to freezes faster. `0` disables the watchdog.
- **Heal Interval (s)** – the *self-healing* re-probe, for a feed that is already down and showing the "No Signal" placeholder (source unplugged, encoder rebooting, network blip). OpenFollow retries the URL this often until the source returns, so the picture comes back by itself with no one re-saving. Default `5.0`. Shorter intervals recover faster but probe the network more often; `0` disables auto-recovery, and the feed then stays on the placeholder until you save the section again.

> To force an immediate reconnect at any time, save the section unchanged – it rebuilds the pipeline on the spot.

**Show Preview** – an inline snapshot of the current feed, refreshed every two seconds while the section is expanded.

**Save** – write and apply the settings; the pipeline restarts with the new source within about a second.
