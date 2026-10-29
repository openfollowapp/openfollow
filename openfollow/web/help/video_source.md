# Video Source

Where OpenFollow receives its video feed. Saving rebuilds the pipeline within about a second; markers and PSN keep running. A red box names what went wrong with the last attempt.

Video above Full HD or 30 fps gets a red box and an error row; it keeps playing, but lags.

## Source Type

The protocol or device; the fields follow it. RTSP, SRT and RTP use the interface from **Network Interface Assignment**.

### RTSP

Most IP cameras and HDMI/SDI-to-IP encoders.

- **RTSP URL** – the full address, e.g. `rtsp://192.0.2.20:554/stream1`. Best results: 1080p or 720p, 25–30 fps, H.264, 4–8 Mbps CBR, one keyframe a second.
- **Username (optional)** / **Password (optional)** – the camera's login. Type the password as it is; `@` and `:` are fine here. These override credentials in the URL.

H.265 decodes in software on a Raspberry Pi, about 0.6 of a Pi 5 core at 1080p30.

### SRT

Long-haul or lossy networks and internet bridges.

- **SRT URL** – `srt://0.0.0.0:5000` to listen, `srt://203.0.113.10:5000` to connect.
- **Passphrase (optional)** – the encryption key, when the sender encrypts. Overrides a passphrase in the URL.

### RTP

Local pipelines you generate yourself, such as FFmpeg or a hardware encoder.

- **RTP URL** – e.g. `rtp://0.0.0.0:5004`, or a `224.x–239.x` multicast address.
- **Encoding** – `H264`, `H265` or `MP2T`, matching the sender.

### USB Camera

UVC webcams and USB capture cards.

- **Device** – the camera. **Scan** looks for cameras again.
- **Render resolution** – `2160p`, `1080p` (default) or `720p` scale the picture down for lower CPU; `Native size` keeps the largest size the camera offers.
- **FPS** – default 30.

A camera with a different shape from the render size, such as 4:3 at `1080p`, keeps its shape with black bars. A capture card reads the incoming signal's resolution.

### Pi Camera

Camera modules and HDMI/SDI-to-CSI adapters on the ribbon-cable port.

- **Camera** – the camera in use; with more than one found, the one to stream.
- **Width** / **Height** (default 1920×1080) and **FPS** (default 30) – the stream.
- **Change** – names the **Camera module** and **Connector**. **Automatic** leaves it to the Pi, which never recognises a camera on a Compute Module. A camera the station booted with changes at the next restart.

### Media Gallery

Stored pictures and clips, for working without a camera.

Click a tile to make it the source. **Stage**, **Concert**, **Festival** and **Grey** cannot be deleted. **Upload** takes JPEG/PNG/WebP images and **VP8 WebM** clips (Full HD, ≤30 fps, ≤60 s); other codecs are refused. Every other source shows **Capture frame**, which saves the current frame into the gallery.

**Stage** shows a 10 × 6 m grid; **Concert** (from the upstage back truss) and **Festival** (from the front truss) a taped 12 × 6 m grid. In all three the Reference Point is the middle of the grid's downstage edge.

## Connection recovery

- **Stall Timeout (s)** – a feed that delivers no frame for this long is rebuilt. Default `3.0`; `0` disables it.
- **Heal Interval (s)** – how often a network feed showing "No Signal" is retried. Default `5.0`; `0` disables it.
- **Preview** – a snapshot of the input, every two seconds.

## Camera Latency

**Measure latency** times the round trip through the camera, which must point at the Operator Screen. The screen flashes black and white and shows no video or markers meanwhile, which can trigger seizures: never during a show. Green below 120 ms, amber to 250 ms, red above.
