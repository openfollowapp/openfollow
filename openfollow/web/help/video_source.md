# Video Source

Where OpenFollow receives its video feed – every detection, overlay, and position output depends on it. Saving rebuilds the pipeline within about a second; markers and PSN output keep running during the rebuild.

When the last attempt failed, a red box at the top of the section names what went wrong – nothing answered, the login was rejected, no video reached this station, and so on – and below it the one thing to try. It is the same box **Live Statistics → Video** and the Setup Wizard's Video Source step show, and it refreshes on its own, so a failure that starts while this page is open appears without reloading. **Live Statistics → Video** lists what each reading means.

## Source Type

Pick the protocol or device for your setup; the fields below update to match.

- **RTSP** – most IP cameras and HDMI/SDI-to-IP encoders; codec auto-negotiated.
- **SRT** – long-haul or lossy networks and internet bridges.
- **RTP** – tightly controlled local pipelines you generate yourself (FFmpeg, hardware encoder).
- **USB Camera** – UVC webcams and USB capture cards.
- **Pi Camera** – CSI camera modules and HDMI/SDI-to-CSI adapters on the ribbon-cable port.
- **Media Gallery** – play a stored still image or short looping clip, or capture a frame from a live source; ships with a Stage scene, an Arena, a Concert, a Festival and a Grey card for bench-testing calibration, lens correction and detection without a camera.

Which network interface SRT, RTSP and RTP use is set under **Network Interface Assignment**, in the Video input row. In Simple network mode they use the station interface.

## Settings (by Source Type)

- **RTSP URL** – the full `rtsp://` address, e.g. `rtsp://192.168.0.182:554/stream1`. Best results: 1080p/720p, 25–30 fps, H.264, ~4–8 Mbps CBR, 1 keyframe/s.
- **Username (optional)** / **Password (optional)** – the camera's login, where one is required. Type the password as it is: no URL-encoding, and `@` or `:` are fine here where they would break a URL. Credentials in the URL (`rtsp://user:pass@host/path`) still work, but these fields override them.
- **SRT URL** – `srt://0.0.0.0:5000` to listen (listener mode) or `srt://203.0.113.10:5000` to connect (caller mode); a bare `host:port` also works.
- **Passphrase (optional)** – the SRT stream's encryption key, when the sender encrypts. Same precedence as the RTSP login: filling it in overrides a `?passphrase=` carried in the URL.
- **RTP URL** + **Encoding** – receive address, e.g. `rtp://0.0.0.0:5004` (unicast) or a `224.x–239.x` multicast address; set **Encoding** (`H264`, `H265`, `MP2T`) to match the sender.
- **H.265 (HEVC)** over RTSP, SRT or RTP decodes in software on a Raspberry Pi: the Pi's hardware HEVC decoder can take the station down when a stream loses packets or is joined between keyframes. At 1080p30 that takes about 0.6 of a Pi 5 core.
- **USB Camera** – pick the **Device** (Scan to re-enumerate), then choose a **Render resolution** and **FPS** (default 30). The station reads the camera in a mode from the camera's own list that reaches the render size and FPS: its uncompressed mode where the camera's USB connection carries it, otherwise its compressed (MJPEG) mode, which the station decodes (about 0.4 of a Pi 5 core at 1080p30). The picture is then scaled to the render size: a preset (`2160p` / `1080p` / `720p`, default `1080p`) downscales for lower CPU, `Native size` keeps the camera's largest size. Capture cards that only output the incoming signal's resolution (e.g. a 4K HDMI feed) list just that, and are read at it.
- **Pi Camera** – the **Camera** row shows the camera the station uses: its Raspberry Pi module name and sensor (*Camera Module 3 (imx708)*), the connector it is named on (CAM/DISP 0 or 1, as printed on the board), or *found automatically* when the Pi recognised it by itself; *set in config.txt* marks a camera named there by hand. With more than one camera found, the row is a choice of which one to stream, saved with the section. **Width** / **Height** (default 1920×1080) and **FPS** (default 30) set the stream.
- **Change** (Pi Camera) – names the **Camera module** and **Connector** in the Pi's boot configuration. **Automatic** leaves it to the Pi, which recognises Raspberry Pi camera modules on a regular Pi but never on a Compute Module. Raspberry Pi's own modules are listed first by name, other sensors below. A camera named while none was loaded at boot starts at once and is checked again a few seconds later; one the station booted with changes at the next restart, shown as **Takes effect after the next restart** beside what runs now, with a **Restart now** button. A camera line added to `config.txt` by hand is replaced (kept, commented out) when another camera is applied here. **No camera found on this station** opens this choice by itself.
- **Media Gallery** – a device-local library of images and looping clips, managed entirely from the browser (the on-display menu can select this source but not change its content). Click a tile to make it the source; the selected tile has an amber border. The bundled **Stage**, **Arena**, **Concert**, **Festival** and **Grey** items can't be deleted; per-tile buttons download or delete your own media. **Upload** accepts JPEG/PNG/WebP images (normalised to ≤1080p JPEG) and **VP8 WebM** clips (≤1080p, ≤30 fps, ≤60 s, audio ignored); other codecs are rejected. Every other source shows a **Capture frame** button that saves the current clean frame into the gallery at its native resolution. A selection that no longer resolves (deleted media, an imported config from another station) falls back to Stage.

  **Arena** is an arena seen from a truss above the stage, through a lens that bows straight lines the way a wide lens does. It was drawn with Barrel / fisheye `-0.15` and Corner correction `-0.03`; the Lens step may name other values that bend the picture the same way to within a few pixels. The tape on the stage marks a 10 × 1 m grid with the Reference Point at the middle of its downstage edge. The four pink marks on mic stands in the aisles sit at stage level, for distance points in the Setup Wizard's Advanced Camera Calibration:

  | Mark | From DSL | From DSR |
  |---|---|---|
  | Centre aisle, front | 11.449 m | 11.449 m |
  | Cross aisle, stage left | 24.781 m | 27.461 m |
  | Cross aisle, stage right | 27.461 m | 24.781 m |
  | Centre aisle, back | 31.697 m | 31.697 m |

  **Concert** is a concert stage seen from a camera on the upstage back truss, looking down over the stage into the crowd through a wide barrel lens. It was drawn with this camera, which puts the grid on the deck seams. The tape on the stage marks a 12 × 6 m grid with the Reference Point at the middle of its downstage edge; the stage decks are 1 m across and 2 m deep.

  | Camera setting | Value |
  |---|---|
  | Position X / Y / Z | 0 / 13 / 12 m |
  | Pitch | -48° |
  | Yaw | 180° |
  | Roll | 0° |
  | Horizontal Field of View | 70° |
  | Barrel / fisheye | -0.2 |
  | Corner correction | -0.07 |

  **Festival** is a festival stage seen from a camera on the front truss over the crowd barrier, looking steeply down onto the stage through a 2.8 mm lens whose centre sits off the centre of the picture. It was drawn with this camera, which puts the grid on the tape; its lens centre is typed in, under Lens centre on the Camera tab or under User input in the Lens step. The tape on the stage marks a 12 × 6 m grid with the Reference Point at the middle of its downstage edge; the stage decks are 2 m across and 1 m deep.

  | Camera setting | Value |
  |---|---|
  | Position X / Y / Z | 0 / -4 / 9.3 m |
  | Pitch | -42° |
  | Yaw | 0° |
  | Roll | 0° |
  | Horizontal Field of View | 84.2° |
  | Barrel / fisheye | -0.31 |
  | Corner correction | -0.01 |
  | Lens centre, right of the picture centre | 28 px |
  | Lens centre, above the picture centre | 16 px |

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

NDI and the cameras (USB, Pi Camera, macOS) recover differently. When their source is lost, the station shows the source picker and keeps looking for that source every few seconds. Once it is announced again, video returns to it by itself, whether the picker is still open or was closed with BACK. A source that is announced but sent no video is tried again after about half a minute, or as soon as it disappears and is announced again. Picking another source in the picker ends the wait. A Pi Camera or macOS camera left on the default camera has no name to look for and is not reconnected this way.

> To force an immediate reconnect at any time, save the section unchanged – it rebuilds the pipeline on the spot.

**Preview** – an inline snapshot of the current video input, refreshed every two seconds while the section is open and in view.

**Save** – write and apply the settings; the pipeline restarts with the new source within about a second.
