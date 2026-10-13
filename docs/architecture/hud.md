# Operator Screen (HUD)

Covers `video/overlay.py`, `runtime/overlay_*`, `window.py`, the on-screen menus. The rules that apply everywhere are in [CLAUDE.md](../../CLAUDE.md).

## HUD rendering (decoupled from buffer flow)
The HUD is **not** in the GStreamer chain. The video sink (`gtksink`) is wrapped in a `Gtk.Overlay`; a `Gtk.DrawingArea` is layered on top via `Gtk.Overlay.add_overlay()` with `set_overlay_pass_through(True)` so it doesn't intercept input.
- The DrawingArea's `draw` signal calls `CairoOverlayRenderer.draw(cr, w, h)`.
- A `Gtk.Widget.add_tick_callback` queues a redraw every display vsync (~60–120 Hz), independent of source buffer rate, so a 0-fps NDI source does not freeze the HUD.
- First-frame / caps detection is a downstream pad probe in the receiver (see [video](video.md#receiver-videoreceiverpy--generic-orchestrator)).
- `CairoOverlayRenderer.measured_fps()` reports HUD redraw rate (display tick), **not** source buffer rate. Use `receiver.source_framerate` (parsed from caps) for actual source FPS.
- **Don't re-couple the HUD to buffer flow** (e.g. by reintroducing `cairooverlay` or a videorate-driven redraw).

## HUD layout
- **Top-left:** the help panel (`key_toggle_help`), or its key hint while closed. While any menu screen is open, the menus' one key list instead (`build_help_sections(mode="menus")`), the same on every screen: `CairoOverlayRenderer.draw` draws it after `_draw_menu_screen`, never a screen itself, and no screen names its own keys. The Button Detection Wizard is not a menu here (every pad button is the input it records) and names only `Esc` itself
- **Top-right:** system stats (`draw_system_stats`) and the status badge (`overlay_status_badge.py`, one row per `_status_flags` entry), drawn last so modals sit on top
- **Bottom-left:** the info panel (`IP Address:` + `Video Source:`) and the virtual fader stack
- **Right side:** one marker card per viewer marker, each carrying its controller badge (see [input](input.md#marker-card--controller-binding)); an assist-mode ghost has no card

## Cone markers

`MarkerConfig.marker_style` is `crosshair` or `cone`. `cone` draws a truncated cone between the stage plane and the marker's Z, below the stage included, from `cone_base_diameter` / `cone_top_diameter` (m) and `cone_thickness` (px), filled at `cone_opacity` when `cone_filled` (the side under a left-lit gradient when `cone_shaded`), and ignores the ball / crosshair / Z line / ground circle fields; the silhouette edges come from `scene/solver.cone_silhouette_angles` in closed form and are inserted into the rings (`cone_ring_angles`), with the convex hull `ring_silhouette_indices` only where the camera looks into the cone or part of it is behind the camera; the renderer keeps each cone as a picture (`ConeCache`) and draws it again only when an input it is drawn from changes, and the mouse grab area is the base ring built from half of `cone_base_diameter`.

Trigger zones are kept as one picture too (`runtime/overlay_draw_zones.ZoneCache`); what it is keyed on, how it is sized and why its labels are grayscale are in the [zones guide](zones.md#overlay-rendering-runtimeservices_marker_visualspy).

## Floor height and the ground grid

Every element that stands on the floor under a marker (the Z line, the ground circle, the cone base, the assist ghost's ring) asks `overlay_draw_scene.floor_z_at(state, x, y)` for its height; nothing else reads `grid_config[5]` for a marker's floor. `floor_z_at` returns the ground grid's `z_offset` when it is active and `(x, y)` is inside its polygon (`zones.geometry.point_in_polygon`, even-odd, XY only, whatever the marker's own Z), else the main grid's `z_offset`. The `ConeCache` key carries that height, so a cone redraws when its floor changes.

The ground grid (`GroundGridConfig`, experimental) is one polygon of up to 64 PSN-absolute `[x, y]` corners at an absolute `z_offset`. `enabled` with at least three corners makes it active (the floor redirect); `visible` additionally draws it. `sync_ground_grid_config` copies it into `OverlayState.ground_grid_*` every tick. `draw_ground_grid` runs right after `draw_grid`: the main grid's lines (aligned to the main grid rectangle's corner, at its spacing, capped like the main grid) clipped to the polygon by `scene/ground_grid.grid_segments_in_polygon`, plus the outline, at the ground grid's height and in the main grid's colour, thickness and opacity, through `_draw_world_lines` so the lens bows them. The main grid still draws across the polygon. Mouse placement, the detection pin, the OSC height placeholders and the marker card stay on the main grid.
