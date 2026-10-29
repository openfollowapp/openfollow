# Video Source

Where OpenFollow receives its video feed. Saving rebuilds the pipeline within about a second; markers and PSN keep running. A red box names what went wrong with the last attempt.

Video above Full HD or 30 fps gets a red box and an error row; it keeps playing, but lags.

## Source Type

The protocol or device; the fields follow it. RTSP, SRT and RTP use the interface from **Network Interface Assignment**.

- **RTSP URL** – the full `rtsp://` address. **Username (optional)** / **Password (optional)** override the URL's credentials.
- **SRT URL** – `srt://0.0.0.0:5000` to listen, `srt://203.0.113.10:5000` to connect. **Passphrase (optional)** is the encryption key.
- **RTP URL** + **Encoding** – e.g. `rtp://0.0.0.0:5004` or a `224.x–239.x` multicast address. Set **Encoding** (`H264`, `H265`, `MP2T`) to the sender's.
- **USB Camera** – pick the **Device**, **Render resolution** (default `1080p`) and **FPS** (default 30).
- **Pi Camera** – **Camera** shows the camera in use; **Width** / **Height** (default 1920×1080) and **FPS** (default 30) set the stream. **Change** names the **Camera module** and **Connector**; a camera the station booted with changes at the next restart.
- **Media Gallery** – click a tile to make it the source. **Stage**, **Concert**, **Festival** and **Grey** cannot be deleted. **Upload** takes JPEG/PNG/WebP images and **VP8 WebM** clips (Full HD, ≤30 fps, ≤60 s). **Capture frame** saves the current frame.

  **Stage** shows a 10 × 6 m grid; **Concert** (from the upstage back truss) and **Festival** (from the front truss) a taped 12 × 6 m grid. In all three the Reference Point is the middle of the grid's downstage edge.

## Connection recovery

- **Stall Timeout (s)** – a feed that delivers no frame for this long is rebuilt. Default `3.0`; `0` disables it.
- **Heal Interval (s)** – how often a network feed showing "No Signal" is retried. Default `5.0`; `0` disables it.
- **Preview** – a snapshot of the input, every two seconds.

## Camera Latency

**Measure latency** times the round trip through the camera, which must point at the Operator Screen. The screen flashes black and white and shows no video or markers meanwhile, which can trigger seizures: never during a show. Green below 120 ms, amber to 250 ms, red above.
