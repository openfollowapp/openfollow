# Spike: an OpenGL Operator Screen inside OpenFollow

Branch `spike/gl-operator-screen`, on top of every phase 2 PR. Not merged: it
answers whether to build it, against the go criteria in the issue (#440).
Switched on per run with `OPENFOLLOW_GL_DISPLAY=1`; without it the app is
unchanged.

Each step is built, reviewed and fixed (at least three rounds), measured on the
Pi 5 test station in the full scene (1080p30 H.264 over RTSP, grid, ground
grid, three paths, two zones, lens calibration, two markers moving over OSC),
and reported on the tracking issue.

## Steps

- [ ] **1. GL display.** A `Gtk.GLArea` replaces the gtksink widget and the HUD
  drawing area. The receiver ends in an `appsink` (I420); each new frame's
  planes go up as textures and a shader converts them. The HUD render
  thread's picture goes up as one texture when it changes and is blended over
  the video. Every display refresh renders once.
- [ ] **2. Video cadence.** Frames are shown on a cadence locked to the display
  refresh (every second refresh for 30 fps on 60 Hz), paced by their
  timestamps, at most one refresh of extra delay. Measured: share of frames
  shown for exactly two refreshes.
- [ ] **3. Layers and GL markers.** The HUD's kept pictures (grid and ground
  grid, zones, paths, panels) go up as their own textures, only when they
  change; marker cards as small textures; markers (ball, crosshair, Z line,
  ground circle) drawn by GL, projection and lens bending in the vertex
  shader. The full-canvas HUD picture and its upload go away.
- [ ] **4. No CPU copy of the video.** Decoded frames reach a texture through
  GStreamer's GL support in the context GTK shares, without a CPU copy;
  measured against step 1's upload.
- [ ] **5. HEVC.** The same clip as HEVC through the Pi 5's hardware decoder,
  against H.264 in software.
- [ ] **6. macOS.** The same path through GTK3's GL on macOS, at least as good as
  today, or its own track.
- [ ] **7. Decision.** All go criteria measured in the running app against
  0.5.0~rc2 and phase 2; a go / no-go report on the tracking issue.

## Go criteria (from the issue)

On a Pi 5, 1080p30 at 60 Hz, full scene, inside the running app:
- HUD at 60 fps, at least 99 % of pictures on time;
- at least 98 % of video frames shown for exactly two refreshes, at most 17 ms
  extra delay;
- whole-Pi CPU at most half of 0.5.0~rc2 on the same scene;
- no late picture on a zone change or a menu opening;
- macOS at least as good as today.
