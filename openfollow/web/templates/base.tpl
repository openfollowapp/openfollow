<!DOCTYPE html>
<html lang="en">
<head>
 <meta charset="UTF-8">
 <meta name="viewport" content="width=device-width, initial-scale=1.0">
 <title>{{config.psn_system_name if defined('config') and config else 'OpenFollow'}} – Configuration</title>
 %# Cache-bust every bundled asset with a content fingerprint of the static
 %# dir so an app update always serves fresh JS/CSS. Without this, a browser
 %# keeps the previous release's cached script on the offline LAN (no
 %# Cache-Control), and the UI silently runs stale code. The token changes
 %# whenever any asset's bytes change (picked up on the next server start).
 % from openfollow.web.routes import asset_version
 % _asset_v = asset_version()
 <link rel="icon" type="image/svg+xml" href="/assets/icon.svg?v={{_asset_v}}">
 <script src="/assets/htmx.min.js?v={{_asset_v}}"></script>
 %# Embed canonical palette inline before any partial scripts to avoid
 %# first-paint flash; enables window.OpenFollow.nextUnusedColor in
 %# marker.tpl / zone_editor.tpl (must sync with openfollow.palette).
 % from openfollow.palette import web_palette_json
 <script>window.OPENFOLLOW_PALETTE = {{!web_palette_json()}};</script>
 %# Seed active unit system before units.js loads for shared formatter/parser
 %# (window.OpenFollow.units); used by zone-editor canvas and setup wizard
 %# unit. Guarded like the title above: login.tpl rebases base without config.
 % if defined('config') and config:
 <script>window.OPENFOLLOW_UNIT_SYSTEM = "{{config.ui.unit_system}}";</script>
 % end
 <script src="/assets/js/color-picker.js?v={{_asset_v}}"></script>
 <script src="/assets/js/units.js?v={{_asset_v}}"></script>
 <style>
 :root {
 color-scheme: dark;
 --bg-base: #07130d;
 --bg-deep: #03100a;
 --bg-soft: #0f2118;
 --surface: rgba(255, 255, 255, 0.04);
 --surface-strong: rgba(255, 255, 255, 0.07);
 --border: rgba(255, 255, 255, 0.12);
 --border-soft: rgba(255, 255, 255, 0.08);
 --text: #f7f5e9;
 --muted: rgba(247, 245, 233, 0.68);
 --accent: #ffbc00;
 --accent-soft: rgba(255, 188, 0, 0.12);
 --ok: #7de59f;
 --danger: #ff8c8c;
 /* Button system tokens (web UI cleanup). Colour encodes
 importance, not action type: primary = the one commit action
 (Save/Update/Restart all map here), secondary = alternatives,
 danger = destructive, neutral = low-emphasis/peer. */
 --btn-radius: 999px;
 --btn-pad-y: 0.62rem;
 --btn-pad-x: 1rem;
 --btn-pad-y-sm: 0.34rem;
 --btn-pad-x-sm: 0.8rem;
 --btn-font: 0.86rem;
 --btn-font-sm: 0.78rem;
 --btn-weight: 700;
 --btn-ink-dark: #07130d;
 --btn-primary-bg: var(--accent);
 --btn-neutral-bg: rgba(255, 255, 255, 0.12);
 --btn-secondary-border: rgba(255, 188, 0, 0.42);
 --btn-danger-border: rgba(255, 140, 140, 0.42);
 --btn-transition: transform 0.17s ease, filter 0.17s ease;
 /* width of the inline help drawer. While the drawer is
 open this same size is applied as <body> padding-right, so the
 content column shrinks/shifts left instead of being covered. */
 --help-drawer-w: min(420px, 92vw);
 }

 * { box-sizing: border-box; }
 body {
 margin: 0;
 min-height: 100vh;
 padding: 20px 0;
 font: 400 14px/1.6 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
 color: var(--text);
 /* Fade is keyed to the viewport (100vh), not page height, so it looks
 identical on every section regardless of length. It sits at the top of
 the page and scrolls away; the matching base colour fills below it. */
 background-color: #06140d;
 background-image:
 radial-gradient(ellipse 90% 50% at 50% -5%, rgba(255, 188, 0, 0.12), transparent 65%),
 linear-gradient(180deg, var(--bg-deep) 0%, var(--bg-base) 65%, #06140d 100%);
 background-repeat: no-repeat;
 background-position: top center;
 background-size: 100% 100vh;
 }
 h1 {
 margin: 0;
 font-size: clamp(1.9rem, 4vw, 2.7rem);
 line-height: 1.08;
 letter-spacing: -0.02em;
 color: var(--text);
 }
 h2 {
 margin: 0;
 font-size: 1.16rem;
 color: var(--text);
 letter-spacing: -0.01em;
 }
 .hero-logo-link {
 display: inline-block;
 border-radius: 0.6rem;
 transition: opacity 0.15s ease;
 }
 .hero-logo-link:hover { opacity: 0.85; }
 .hero-logo-link:focus-visible {
 outline: 2px solid var(--accent);
 outline-offset: 4px;
 }
 .hero-logo {
 display: block;
 width: min(360px, 72vw);
 height: auto;
 margin: 0 0 0.5rem;
 }
 .subtitle {
 margin: 0.6rem 0 0;
 max-width: 52rem;
 color: var(--muted);
 font-size: 0.99rem;
 }
 .container {
 width: min(1120px, calc(100% - 2rem));
 margin: 0 auto;
 transition: width 0.28s ease;
 }
 /* while the help drawer is open, pad <body> on the right by
 the drawer width. The drawer is position:fixed and overlays that
 padding; the centred .container shrinks into the remaining space, so
 the content shifts left and is never covered. On narrow screens the
 drawer overlays full-width instead (see the max-width rule below). */
 body { transition: padding-right 0.28s ease; }
 body.help-open { padding-right: var(--help-drawer-w); }
 @media (prefers-reduced-motion: reduce) {
 body, .container, .help-drawer { transition: none; }
 }
 .hero-panel {
 position: relative;
 margin-bottom: 1rem;
 padding: 1.4rem 1.5rem 1.5rem;
 border-radius: 1.2rem;
 border: 1px solid var(--border-soft);
 background: linear-gradient(180deg, rgba(255, 255, 255, 0.045) 0%, rgba(255, 255, 255, 0.02) 100%);
 overflow: hidden;
 }
 .hero-panel::before {
 content: "";
 position: absolute;
 inset: 0;
 pointer-events: none;
 background-image: radial-gradient(circle, rgba(255, 188, 0, 0.08) 1px, transparent 1px);
 background-size: 32px 32px;
 mask-image: radial-gradient(ellipse 90% 85% at 50% 18%, black 25%, transparent 78%);
 -webkit-mask-image: radial-gradient(ellipse 90% 85% at 50% 18%, black 25%, transparent 78%);
 }
 .section {
 margin-bottom: 1rem;
 padding: 1.2rem;
 border-radius: 1.1rem;
 border: 1px solid var(--border-soft);
 background: var(--surface);
 backdrop-filter: blur(2px);
 }
 .section.is-collapsed > :not(.section-head) { display: none; }
 .section.saved, .save-flash.saved { animation: flash-green .55s; }
 @keyframes flash-green {
 0% { box-shadow: 0 0 0 2px rgba(125, 229, 159, 0.75); }
 100% { box-shadow: none; }
 }
 .section-head {
 display: flex;
 align-items: center;
 gap: 8px;
 flex-wrap: wrap;
 padding-bottom: 10px;
 border-bottom: 1px solid var(--border-soft);
 margin-bottom: 14px;
 }
 .section-head h2 { margin-right: 0; }
 .section-note {
 color: var(--muted);
 font-size: 0.84rem;
 }
 .section.is-collapsed .section-head {
 border-bottom: 0;
 padding-bottom: 0;
 margin-bottom: 0;
 }
 .section-toggle {
 padding: 0.38rem 0.7rem;
 border-radius: 999px;
 border: 1px solid rgba(255, 188, 0, 0.35);
 background: transparent;
 color: var(--text);
 font-size: 0.75rem;
 letter-spacing: 0.03em;
 font-weight: 600;
 cursor: pointer;
 }
 .section-toggle:hover {
 border-color: rgba(255, 188, 0, 0.55);
 background: rgba(255, 188, 0, 0.1);
 }
 /* right-aligned cluster holding the help "?" and the
 collapse toggle (both JS-injected into .section-head). */
 .section-actions {
 margin-left: auto;
 display: flex;
 align-items: center;
 gap: 8px;
 }
 .section-help-btn {
 width: 1.6rem;
 height: 1.6rem;
 flex: 0 0 auto;
 border-radius: 999px;
 border: 1px solid rgba(255, 188, 0, 0.35);
 background: transparent;
 color: var(--text);
 font-weight: 700;
 font-size: 0.86rem;
 line-height: 1;
 cursor: pointer;
 display: inline-flex;
 align-items: center;
 justify-content: center;
 }
 .section-help-btn:hover {
 border-color: rgba(255, 188, 0, 0.55);
 background: rgba(255, 188, 0, 0.1);
 }
 /* the help drawer itself. position:fixed so its scroll is
 independent of the page; slid off-screen by default and revealed by
 ``body.help-open``. Kept below #modal-root (z-index:1000). */
 .help-drawer {
 position: fixed;
 top: 0;
 right: 0;
 width: var(--help-drawer-w);
 height: 100vh;
 height: 100dvh;
 display: flex;
 flex-direction: column;
 background: var(--bg-soft);
 border-left: 1px solid var(--border-soft);
 box-shadow: -14px 0 36px rgba(0, 0, 0, 0.38);
 transform: translateX(100%);
 transition: transform 0.28s ease;
 z-index: 900;
 overflow: hidden;
 }
 body.help-open .help-drawer { transform: translateX(0); }
 .help-drawer-head {
 flex: 0 0 auto;
 display: flex;
 align-items: center;
 justify-content: space-between;
 gap: 8px;
 padding: 0.9rem 1.2rem;
 border-bottom: 1px solid var(--border-soft);
 }
 .help-drawer-head h2 { margin: 0; font-size: 1.05rem; }
 .help-drawer-close {
 flex: 0 0 auto;
 display: inline-flex;
 align-items: center;
 justify-content: center;
 width: 2rem;
 height: 2rem;
 padding: 0;
 border-radius: 999px;
 border: 1px solid var(--border-soft);
 background: transparent;
 color: var(--text);
 font-size: 1.25rem;
 line-height: 1;
 cursor: pointer;
 }
 .help-drawer-close:hover { background: rgba(255, 255, 255, 0.08); }
 .help-drawer-body {
 flex: 1 1 auto;
 overflow-y: auto;
 padding: 1rem 1.2rem 2.2rem;
 line-height: 1.55;
 color: var(--text);
 }
 .help-loading { color: var(--muted); }
 .help-drawer-body h1 { font-size: 1.2rem; margin: 0 0 0.6rem; }
 .help-drawer-body h2 { font-size: 1.02rem; margin: 1.3rem 0 0.4rem; }
 .help-drawer-body h3 { font-size: 0.92rem; margin: 1rem 0 0.3rem; }
 .help-drawer-body p,
 .help-drawer-body ul,
 .help-drawer-body ol { margin: 0 0 0.7rem; }
 .help-drawer-body li { margin: 0.2rem 0; }
 .help-drawer-body code {
 background: rgba(255, 255, 255, 0.08);
 padding: 0.1em 0.35em;
 border-radius: 4px;
 font-size: 0.85em;
 }
 .help-drawer-body a { color: var(--accent); }
 .help-drawer-body blockquote {
 margin: 0.7rem 0;
 padding: 0.3rem 0.85rem;
 border-left: 3px solid var(--accent);
 color: var(--muted);
 }
 .help-drawer-body table {
 border-collapse: collapse;
 margin: 0.6rem 0;
 font-size: 0.85rem;
 }
 .help-drawer-body th,
 .help-drawer-body td {
 border: 1px solid var(--border-soft);
 padding: 0.3rem 0.5rem;
 text-align: left;
 }
 /* narrow viewports: overlay full-width instead of shrinking
 the already single-column content (would crush the forms). Declared
 after the global body.help-open rule so it wins. */
 @media (max-width: 760px) {
 :root { --help-drawer-w: 100vw; }
 body.help-open { padding-right: 0; }
 }
 .group {
 margin-bottom: 0.9rem;
 padding-bottom: 0.95rem;
 border-bottom: 1px solid rgba(255, 255, 255, 0.06);
 }
 .group:last-child { margin-bottom: 0; padding-bottom: 0; border-bottom: 0; }
 /* Keep the divider even when the group is the last child of its
 form – used to separate the two forms stacked inside the Station
 Settings box (Display units | Station name + PIN). Same
 specificity as .group:last-child, declared after it so it wins. */
 .group.group--divider { margin-bottom: 0.9rem; padding-bottom: 0.95rem; border-bottom: 1px solid rgba(255, 255, 255, 0.06); }
 .group-title {
 margin: 0 0 0.62rem;
 color: rgba(247, 245, 233, 0.8);
 font-size: 0.78rem;
 font-weight: 700;
 letter-spacing: 0.11em;
 text-transform: uppercase;
 }
 /* Detection form: a compact intro header above three collapsible section
 boxes – the same component (.section + fold toggle) as the Output
 PSN/OTP/RTTrPM boxes, so the long form reads as a few labelled panels. */
 .detection-intro { margin-bottom: 1rem; }
 .detection-intro h2 { margin: 0 0 0.25rem; }
 /* Keep at most two inputs side by side inside detection groups so dense rows
 don't crowd; collapse to one column on narrow screens. */
 .detection-cards .row--pair { display: grid; grid-template-columns: 1fr 1fr; gap: 0.9rem; align-items: start; }
 .detection-cards .row--pair > .field { min-width: 0; }
 .detection-cards .row--toggles { display: flex; flex-wrap: wrap; gap: 1.6rem; }
 @media (max-width: 640px) { .detection-cards .row--pair { grid-template-columns: 1fr; } }
 /* Assisted-tracking sub-section: divider above it, hidden in Fully Automatic mode. */
 .detection-cards .group--assist { margin-top: 0.9rem; padding-top: 0.95rem; border-top: 1px solid rgba(255, 255, 255, 0.06); }
 /* Segmented two-option toggle (Tracking Mode): equal-width cells. */
 .seg-toggle { display: grid; grid-template-columns: 1fr 1fr; gap: 4px; max-width: 30rem; padding: 4px; border: 1px solid var(--border-soft); border-radius: 0.7rem; background: rgba(0, 0, 0, 0.22); }
 .seg-toggle .seg-option { margin: 0; display: flex; cursor: pointer; }
 .seg-toggle .seg-option input { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }
 .seg-toggle .seg-option > span { flex: 1; display: flex; flex-direction: column; gap: 1px; padding: 0.4rem 0.95rem; border-radius: 0.5rem; color: var(--muted); text-align: center; transition: background 0.12s, color 0.12s; }
 .seg-toggle .seg-option > span strong { font-size: 0.9rem; font-weight: 700; }
 .seg-toggle .seg-option > span small { font-size: 0.72rem; font-weight: 400; opacity: 0.8; }
 .seg-toggle .seg-option input:checked + span { background: var(--accent); color: #1a1205; }
 .seg-toggle .seg-option input:focus-visible + span { outline: 2px solid var(--accent); outline-offset: 2px; }
 .stats-columns {
 display: grid;
 gap: 0.78rem;
 grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
 }
 .stat-panel {
 border: 1px solid rgba(255, 255, 255, 0.1);
 border-radius: 0.95rem;
 background: rgba(255, 255, 255, 0.02);
 padding: 0.84rem 0.9rem;
 }
 .stat-panel-head {
 display: flex;
 justify-content: space-between;
 align-items: center;
 gap: 0.4rem;
 margin-bottom: 0.5rem;
 }
 .stat-panel-title {
 margin: 0;
 color: var(--text);
 font-size: 0.98rem;
 letter-spacing: -0.01em;
 font-weight: 700;
 }
 .stat-chip {
 display: inline-flex;
 align-items: center;
 border-radius: 999px;
 padding: 0.14rem 0.55rem;
 font-size: 0.68rem;
 letter-spacing: 0.06em;
 text-transform: uppercase;
 font-weight: 700;
 border: 1px solid rgba(255, 255, 255, 0.18);
 color: rgba(247, 245, 233, 0.88);
 background: rgba(255, 255, 255, 0.05);
 white-space: nowrap;
 }
 .stat-chip.ok {
 color: #c8ffd8;
 border-color: rgba(125, 229, 159, 0.4);
 background: rgba(125, 229, 159, 0.14);
 }
 .stat-chip.warn {
 color: #ffe7ae;
 border-color: rgba(255, 188, 0, 0.35);
 background: rgba(255, 188, 0, 0.14);
 }
 .stat-chip.off {
 color: #ffd7d7;
 border-color: rgba(255, 140, 140, 0.35);
 background: rgba(255, 140, 140, 0.13);
 }
 .metric-list {
 margin: 0;
 padding: 0;
 display: flex;
 flex-direction: column;
 gap: 0.28rem;
 }
 .metric-list dt,
 .metric-list dd {
 margin: 0;
 }
 .metric-row {
 display: flex;
 justify-content: space-between;
 align-items: baseline;
 gap: 0.75rem;
 padding: 0.35rem 0;
 border-bottom: 1px solid rgba(255, 255, 255, 0.06);
 }
 .metric-row:last-child {
 border-bottom: 0;
 padding-bottom: 0;
 }
 .metric-label {
 color: var(--muted);
 font-size: 0.74rem;
 letter-spacing: 0.06em;
 text-transform: uppercase;
 font-weight: 600;
 }
 .metric-value {
 color: var(--text);
 font-size: 0.96rem;
 font-weight: 650;
 text-align: right;
 word-break: break-word;
 }
 .metric-value.strong {
 font-size: 1.12rem;
 letter-spacing: -0.01em;
 }
 .stat-help {
 margin: 0 0 0.7rem;
 color: var(--muted);
 font-size: 0.82rem;
 }
 .inline-advanced {
 margin-top: 0.55rem;
 padding-top: 0.55rem;
 border-top: 1px solid rgba(255, 255, 255, 0.08);
 }
 .inline-advanced > summary {
 cursor: pointer;
 color: var(--muted);
 font-size: 0.76rem;
 letter-spacing: 0.06em;
 text-transform: uppercase;
 font-weight: 700;
 list-style: none;
 }
 .inline-advanced > summary::-webkit-details-marker {
 display: none;
 }
 .inline-advanced > summary::before {
 content: "+ ";
 }
 .inline-advanced[open] > summary::before {
 content: "- ";
 }
 .inline-advanced-content {
 margin-top: 0.45rem;
 }
 .checkbox-field.inline {
 justify-content: flex-start;
 flex-direction: row;
 align-items: center;
 gap: 8px;
 min-width: 100%;
 }
 .checkbox-field.inline label {
 margin: 0;
 cursor: pointer;
 text-transform: none;
 letter-spacing: 0;
 font-size: 0.86rem;
 font-weight: 600;
 color: var(--text);
 }
 .video-preview {
 width: 100%;
 max-width: 640px;
 border-radius: 0.75rem;
 border: 1px solid var(--border);
 background: var(--bg-deep);
 display: none;
 }
 .video-preview-hint {
 color: var(--muted);
 font-size: 0.84rem;
 font-style: italic;
 }
 .gallery-toolbar { margin: 0.5rem 0 0.25rem; }
 .gallery-upload-btn { cursor: pointer; }
 .gallery-grid {
 display: grid;
 grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
 gap: 0.5rem;
 margin-top: 0.5rem;
 }
 .gallery-grid.is-loading { opacity: 0.5; pointer-events: none; }
 .gallery-error {
 grid-column: 1 / -1;
 color: var(--danger);
 font-size: 0.84rem;
 padding: 0.35rem 0.5rem;
 border: 1px solid var(--danger);
 border-radius: 0.4rem;
 }
 .gallery-tile {
 position: relative;
 border: 2px solid transparent;
 border-radius: 0.5rem;
 overflow: hidden;
 }
 .gallery-tile.selected { border-color: var(--accent); }
 .gallery-select {
 display: block;
 width: 100%;
 padding: 0;
 border: 0;
 background: var(--bg-deep);
 cursor: pointer;
 color: var(--text);
 }
 .gallery-thumb {
 display: block;
 width: 100%;
 aspect-ratio: 16 / 9;
 object-fit: cover;
 background: var(--bg-soft);
 }
 .gallery-grey { background: #808080; }
 .gallery-actions {
 position: absolute;
 top: 4px;
 right: 4px;
 display: flex;
 gap: 0.25rem;
 opacity: 0;
 transition: opacity 0.15s;
 }
 .gallery-tile:hover .gallery-actions,
 .gallery-tile:focus-within .gallery-actions { opacity: 1; }
 .gallery-dl, .gallery-del {
 display: inline-flex;
 align-items: center;
 justify-content: center;
 width: 22px;
 height: 22px;
 border-radius: 50%;
 border: 0;
 background: rgba(3, 16, 10, 0.85);
 color: var(--text);
 font-size: 0.9rem;
 line-height: 1;
 text-decoration: none;
 cursor: pointer;
 }
 .gallery-del { color: var(--danger); }
 .top-bar-actions {
 display: flex;
 justify-content: flex-end;
 gap: 0.5rem;
 margin-bottom: 0.5rem;
 }
 .top-bar-actions form { margin: 0; }
 .station-name-pill {
 display: inline-block;
 padding: 0.18rem 0.6rem;
 border-radius: 999px;
 background: rgba(255,255,255,0.08);
 color: var(--text);
 font-size: 0.85rem;
 font-weight: 600;
 letter-spacing: 0.02em;
 margin-bottom: 0.5rem;
 }
 /* When pinned in the hero-panel's top-right corner: absolute pos,
 drop the trailing margin, and bump the z-index so the panel's
 ::before dot-grid doesn't sit on top of the text. */
 .hero-station-pill {
 position: absolute;
 top: 0.9rem;
 right: 1.1rem;
 margin-bottom: 0;
 z-index: 1;
 }
 .wizard-launch {
 display: flex;
 align-items: center;
 flex-wrap: wrap;
 gap: 0.5rem;
 margin-bottom: 1rem;
 }
 .row { display: flex; flex-wrap: wrap; gap: 0.72rem; margin-bottom: 0.72rem; }
 .row:last-child { margin-bottom: 0; }
 /* The per-source fragment wrapper isn't the section's visual end – more rows
 (Stall Timeout, …) follow in the same group – so keep its last row's normal
 inter-row gap that .row:last-child would otherwise zero. */
 [data-input-type] > .row:last-child { margin-bottom: 0.72rem; }
 .field { display: flex; flex-direction: column; flex: 1; min-width: 130px; }
 .field.wide { flex: 2; min-width: 230px; }
 .fields-grid {
 display: grid;
 grid-template-columns: repeat(3, minmax(0, 1fr));
 gap: 0.72rem;
 margin-bottom: 0.72rem;
 }
 .fields-grid:last-child { margin-bottom: 0; }
 .fields-grid .field { min-width: 0; flex: initial; }
 .fields-grid .field.span-2 { grid-column: span 2; }
 .fields-grid .field.span-3 { grid-column: span 3; }
 label {
 margin-bottom: 4px;
 color: var(--muted);
 font-size: 0.72rem;
 text-transform: uppercase;
 letter-spacing: 0.08em;
 font-weight: 600;
 }
 input, select {
 width: 100%;
 border-radius: 0.75rem;
 border: 1px solid var(--border);
 background: rgba(255, 255, 255, 0.03);
 color: var(--text);
 padding: 0.62rem 0.72rem;
 font-size: 0.93rem;
 }
 /* Strip the platform-native select chrome (macOS double-arrow,
 Windows blue indicator) so selects render with the same border /
 padding / radius as the surrounding text inputs. Replace with a
 single chevron drawn via an inline SVG background – no extra
 network requests, no asset path. ``padding-right`` reserves
 room for the chevron so long option labels don't overlap it. */
 select {
 appearance: none;
 -webkit-appearance: none;
 -moz-appearance: none;
 padding-right: 2.2rem;
 background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8' fill='none'><path d='M1 1.5L6 6.5L11 1.5' stroke='%23f7f5e9' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round' opacity='0.7'/></svg>");
 background-repeat: no-repeat;
 background-position: right 0.85rem center;
 background-size: 12px 8px;
 }
 select::-ms-expand { display: none; }
 /* The native <select> dropdown popup does NOT inherit the select's
 dark background – Firefox renders the option list on the OS-default
 white surface, leaving our light --text colour near-invisible
 (contrastless options). Pin an explicit dark background + light text
 on options/optgroups so the popup stays legible across browsers. */
 option, optgroup {
 background-color: var(--bg-deep);
 color: var(--text);
 }
 input:focus, select:focus {
 outline: 2px solid rgba(255, 188, 0, 0.32);
 outline-offset: 1px;
 border-color: rgba(255, 188, 0, 0.42);
 }
 /* Pair an input/select with an inline action button on the same
 row (e.g. the network-interface picker's Scan button). Without
 this the parent ``.field`` is ``flex-direction: column`` and the
 button drops onto its own line. ``align-items: stretch`` makes
 the button match the input's height; ``flex: 1`` on the select
 keeps the field elastic. */
 .input-with-button {
 display: flex;
 gap: 0.5rem;
 align-items: stretch;
 }
 .input-with-button > select,
 .input-with-button > input {
 flex: 1;
 min-width: 0;
 }
 .input-with-button > button {
 flex-shrink: 0;
 margin-bottom: 0;
 }
 input[type="color"] { height: 39px; padding: 2px; cursor: pointer; }
 .checkbox-field { justify-content: flex-start; min-width: 180px; }
 .checkbox-wrap { height: 39px; display: flex; align-items: center; }
 input[type="checkbox"] {
 width: 18px;
 height: 18px;
 margin: 0;
 cursor: pointer;
 accent-color: var(--accent);
 }
 .actions { display: flex; flex-wrap: wrap; gap: 0.64rem; margin-top: 0.5rem; }
 /* One token-driven base; the variant classes below only swap
 colour. Legacy role names (.save-btn/.update-btn/.restart-btn/
 .ghost-btn/.broadcast-btn) are kept as aliases so existing
 templates need no churn. */
 button, a.btn-link {
 display: inline-block;
 border: 0;
 border-radius: var(--btn-radius);
 padding: var(--btn-pad-y) var(--btn-pad-x);
 font-size: var(--btn-font);
 color: var(--btn-ink-dark);
 font-weight: var(--btn-weight);
 cursor: pointer;
 text-align: center;
 text-decoration: none;
 line-height: 1.2;
 transition: var(--btn-transition);
 }
 button:hover, a.btn-link:hover { transform: translateY(-1px); filter: brightness(1.03); }
 button:disabled, .btn-disabled {
 opacity: 0.5;
 cursor: not-allowed;
 transform: none;
 filter: none;
 }
 /* Primary – the one commit action in a context. Save/Update/Restart
 all map here (colour signals importance, not which action). */
 .btn-primary, .save-btn, .update-btn, .restart-btn {
 background: var(--btn-primary-bg);
 color: var(--btn-ink-dark);
 }
 /* Secondary – alternatives (Cancel, Back, Scan, Reset, Load) and
 the "Apply to all stations" peer push (.broadcast-btn): it's a
 real action, so it gets the actionable gold outline rather than
 the faint neutral fill that reads as disabled. */
 .btn-secondary, button.secondary, .button-secondary, .broadcast-btn {
 background: transparent;
 border: 1px solid var(--btn-secondary-border);
 color: var(--text);
 font-weight: 600;
 }
 /* Danger – destructive (Delete, Discard). */
 .btn-danger, button.danger {
 background: transparent;
 border: 1px solid var(--btn-danger-border);
 color: var(--danger);
 font-weight: 600;
 }
 /* Neutral – low-emphasis dismiss actions (e.g. the network-edit
 Cancel via .ghost-btn). */
 .btn-neutral, .ghost-btn {
 background: var(--btn-neutral-bg);
 color: var(--text);
 }
 /* Full-width within its container (e.g. the login Unlock button). */
 .btn-block { width: 100%; }
 /* Network form: compact label:value grid matching the read-only status table. */
 .network-grid {
 display: grid;
 grid-template-columns: minmax(7rem, 9rem) 1fr;
 gap: 0.4rem 0.9rem;
 align-items: center;
 margin: 0;
 }
 .network-grid > label {
 color: var(--muted);
 font-size: 0.8rem;
 }
 .network-grid input, .network-grid select {
 margin: 0;
 max-width: 18rem;
 }
 .network-grid-value {
 color: rgba(247, 245, 233, 0.95);
 font-variant-numeric: tabular-nums;
 }
 /* Disabled fields in the read-only view read as a clean status display
 rather than greyed-out broken inputs. */
 .network-config select:disabled, .network-config input:disabled {
 opacity: 1;
 color: var(--text);
 -webkit-text-fill-color: var(--text);
 background: rgba(247, 245, 233, 0.03);
 cursor: default;
 }
 .btn-sm,
 button.small, a.btn-link.small, .save-btn.small, .restart-btn.small, .secondary.small {
 padding: var(--btn-pad-y-sm) var(--btn-pad-x-sm);
 font-size: var(--btn-font-sm);
 }
 /* Touch spacing: gap between *adjacent* small buttons (e.g. Save /
 Delete in a row) so they aren't easy to mis-tap on a touchscreen.
 Scoped to the sibling pair so a standalone small button (e.g. the
 header Logout, a Reset-to-Defaults) keeps its original margins. */
 .small + .small { margin-left: 0.5rem; }
 .field-note {
 margin-top: 4px;
 color: var(--muted);
 font-size: 0.78rem;
 line-height: 1.35;
 }
 /* Sibling span stays empty for valid input; only -msg span gets
 swapped in by HTMX so empty content collapses naturally. */
 .field-error {
 display: block;
 min-height: 0;
 margin-top: 3px;
 font-size: 0.78rem;
 line-height: 1.3;
 }
 .field-error-msg {
 color: #ff8a8a;
 font-weight: 600;
 }
 /* ``field-warn-msg``: soft-fail channel, visually prominent but leaves Save usable.
 Keys off ``.field-error-msg`` and ``aria-invalid``. Used by OSC binding validators. */
 .field-warn-msg {
 color: #ff8a8a;
 font-weight: 600;
 }
 .field-note-msg {
 color: #9fc9ff;
 }
 /* Virtual Faders: eight vertical fader strips (horizontal layout);
 clicking a strip selects it for editing in the detail panel below. */
 .midi-fader-strips {
 display: flex;
 gap: 0.6rem;
 justify-content: center;
 align-items: stretch;
 flex-wrap: wrap;
 padding: 1rem 0.5rem;
 background: rgba(0, 0, 0, 0.18);
 border: 1px solid rgba(255, 255, 255, 0.08);
 border-radius: 10px;
 margin-bottom: 1rem;
 }
 .midi-fader-strip {
 display: flex;
 flex-direction: column;
 align-items: center;
 gap: 0.35rem;
 width: 64px;
 padding: 0.6rem 0.35rem;
 /* Per-fader colour tint: defaults to transparent, with selection
 rules having higher specificity for focus feedback. */
 background: var(--fader-tint, transparent);
 border: 1px solid transparent;
 border-radius: 8px;
 color: var(--text);
 cursor: pointer;
 font: inherit;
 transition: background 0.1s, border-color 0.1s;
 }
 .midi-fader-strip:hover { background: rgba(255, 255, 255, 0.05); }
 .midi-fader-strip[data-selected="true"] {
 /* Selection = outline only. No gold background wash (it was too much
 gold and hid the border against a tinted strip); the crisp accent
 border alone marks the selected strip over its colour tint. */
 border-color: var(--accent);
 }
 .midi-fader-strip-num {
 font-size: 0.65rem;
 letter-spacing: 0.08em;
 color: rgba(247, 245, 233, 0.55);
 text-transform: uppercase;
 }
 .midi-fader-strip-track {
 position: relative;
 width: 10px;
 height: 140px;
 background: rgba(0, 0, 0, 0.45);
 border: 1px solid rgba(255, 255, 255, 0.08);
 border-radius: 5px;
 overflow: visible;
 }
 .midi-fader-strip-fill {
 position: absolute;
 bottom: 0;
 left: 0;
 right: 0;
 background: linear-gradient(to top, #ffbc00 0%, #ffe680 100%);
 border-radius: 5px;
 transition: height 0.08s linear;
 }
 .midi-fader-strip-handle {
 position: absolute;
 width: 26px;
 height: 6px;
 left: -8px;
 background: var(--text, #f7f5e9);
 border: 1px solid var(--accent, #ffbc00);
 border-radius: 2px;
 box-shadow: 0 1px 3px rgba(0, 0, 0, 0.4);
 transform: translateY(50%);
 transition: bottom 0.08s linear;
 }
 .midi-fader-strip-name {
 font-size: 0.78rem;
 font-weight: 600;
 max-width: 56px;
 overflow: hidden;
 text-overflow: ellipsis;
 white-space: nowrap;
 text-align: center;
 }
 .midi-fader-strip-value {
 font-size: 0.72rem;
 color: rgba(247, 245, 233, 0.7);
 font-variant-numeric: tabular-nums;
 text-align: center;
 }
 .midi-fader-strip-not-picked-up {
 display: block;
 font-size: 0.62rem;
 color: var(--accent, #ffbc00);
 font-style: italic;
 line-height: 1;
 margin-top: 2px;
 }
 /* Read-only marker-fader strips: gamepad-driven, not clickable. */
 .midi-fader-strip--readonly { cursor: default; }
 .midi-fader-strip--readonly:hover { background: transparent; }
 .midi-fader-detail-subtitle {
 font-weight: 400;
 color: rgba(247, 245, 233, 0.55);
 font-size: 0.85em;
 }
 /* Fader detail form: scoped to .midi-fader-detail for the editor. */
 .midi-fader-detail .mf-source-row { align-items: flex-start; }
 .midi-fader-detail .mf-kind { flex: 0 1 180px; }
 .midi-fader-detail .mf-patch { flex: 2 1 220px; }
 .midi-fader-detail .mf-type { flex: 1.3 1 160px; }
 .midi-fader-detail .mf-channel { flex: 0 1 150px; }
 .midi-fader-detail .mf-cc { flex: 0 1 180px; }
 .midi-fader-detail .mf-learn { flex: 1 1 240px; }
 /* Learn slot: button sized to its label (not the full column); helper
 note / status pills beneath. The class sits on the persistent
 wrapper so it survives the capture-status partial's innerHTML
 re-renders during the Learn flow. */
 .midi-fader-detail .mf-learn-status {
 display: flex;
 flex-direction: column;
 gap: 0.4rem;
 }
 .midi-fader-detail .mf-learn-status > button,
 .midi-fader-detail .mf-learn-status > .status-pill { align-self: flex-start; }
 .midi-fader-detail .mf-learn-status .field-note { margin-top: 0; }
 /* Identity row: name grows; default value / colour stay compact so the
 row reads evenly with the colour swatch added. */
 .midi-fader-detail .mf-ident-row { align-items: flex-start; }
 .midi-fader-detail .mf-name { flex: 2 1 240px; }
 .midi-fader-detail .mf-default { flex: 0 1 150px; }
 .midi-fader-detail .mf-color-field { flex: 0 0 auto; min-width: 0; }
 .midi-fader-detail .mf-color-field .color-swatch-trigger { margin-top: 0.2rem; }
 /* Standard "visually hidden but exposed to AT" pattern: hide
 content visually while exposing it to assistive technology. */
 .visually-hidden {
 position: absolute;
 width: 1px;
 height: 1px;
 padding: 0;
 margin: -1px;
 overflow: hidden;
 clip: rect(0, 0, 0, 0);
 white-space: nowrap;
 border: 0;
 }
 input[aria-invalid="true"],
 select[aria-invalid="true"],
 textarea[aria-invalid="true"],
 /* Enabled checkbox: ``data-osc-unresolved="true"`` marker allows Save
 when deps unresolved; mirrors danger styling for visual consistency. */
 input[data-osc-unresolved="true"],
 /* OSC message editor: ``contenteditable`` div needs explicit invalid styling
 to match standard input controls. Ensures visual consistency when validation fails. */
 .osc-message-editor[aria-invalid="true"] {
 border-color: #ff5c5c;
 box-shadow: 0 0 0 1px rgba(255, 92, 92, 0.35);
 }
 .restart-notice {
 margin-bottom: 12px;
 padding: 10px 12px;
 border-radius: 0.8rem;
 border: 1px solid rgba(159, 201, 255, 0.35);
 background: rgba(159, 201, 255, 0.11);
 color: #d5e9ff;
 font-weight: 600;
 }
 /* Generic notice banner – same shape as ``.restart-notice`` /
 ``.update-notice`` but reusable outside those flows. Default
 variant is informational blue; ``.warning`` matches the yellow
 palette of ``.stat-chip.warn``; ``.error`` matches the red
 danger palette. Use this for inline messages that aren't tied
 to a specific feature's notice block. */
 .notice {
 margin: 0 0 12px;
 padding: 10px 12px;
 border-radius: 0.8rem;
 border: 1px solid rgba(159, 201, 255, 0.35);
 background: rgba(159, 201, 255, 0.11);
 color: #d5e9ff;
 font-weight: 600;
 line-height: 1.35;
 }
 .notice.warning {
 border-color: rgba(255, 188, 0, 0.35);
 background: rgba(255, 188, 0, 0.13);
 color: #ffe1a2;
 }
 .notice.error {
 border-color: rgba(255, 140, 140, 0.35);
 background: rgba(255, 140, 140, 0.13);
 color: #ffd7d7;
 }
 .update-notice {
 margin-bottom: 12px;
 padding: 10px 12px;
 border-radius: 0.8rem;
 border: 1px solid rgba(159, 201, 255, 0.35);
 background: rgba(159, 201, 255, 0.11);
 color: #d5e9ff;
 font-weight: 600;
 }
 .update-notice.updating {
 border-color: rgba(255, 188, 0, 0.35);
 background: rgba(255, 188, 0, 0.13);
 color: #ffe1a2;
 }
 .update-notice.error {
 border-color: rgba(255, 140, 140, 0.35);
 background: rgba(255, 140, 140, 0.13);
 color: #ffd7d7;
 }
 /* Awaiting-password prompt: roomier padding and explicit vertical
 rhythm between the message, the password input and the action
 row, since the operator is making a security-sensitive
 decision and the default cramped spacing made the controls
 look like decoration. */
 .update-notice.awaiting-password {
 padding: 14px 16px;
 }
 .update-notice.awaiting-password p {
 margin: 0 0 12px;
 }
 .update-notice.awaiting-password input[type="password"] {
 margin-bottom: 12px;
 }
 .update-notice.awaiting-password .actions {
 margin-top: 0;
 }
 /* Screen-reader-only label: kept in the DOM for assistive tech
 but pulled out of the visual / layout flow. Used by the
 awaiting-password prompt where the placeholder already names
 the field. Standard "visually hidden" pattern. */
 .visually-hidden {
 position: absolute;
 width: 1px;
 height: 1px;
 margin: -1px;
 padding: 0;
 overflow: hidden;
 clip: rect(0, 0, 0, 0);
 white-space: nowrap;
 border: 0;
 }
 /* On-device footer (loopback requests only): hint operators that
 a gamepad button can dismiss the embedded WebView overlay.
 Hidden by default – only ``index`` / ``login`` pass
 ``on_device=True`` when the request came from 127.0.0.1, which
 is how the WebKitGTK overlay loads the page. */
 .on-device-footer {
 margin-top: 18px;
 padding: 10px 14px;
 border-radius: 0.8rem;
 border: 1px solid rgba(255, 255, 255, 0.08);
 background: rgba(255, 255, 255, 0.04);
 color: var(--muted);
 font-size: 0.82rem;
 display: flex;
 gap: 0.8rem;
 flex-wrap: wrap;
 }
 .on-device-footer kbd {
 display: inline-block;
 padding: 1px 8px;
 margin-right: 4px;
 border-radius: 0.4rem;
 border: 1px solid rgba(255, 255, 255, 0.18);
 background: rgba(255, 255, 255, 0.08);
 color: var(--text);
 font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
 font-size: 0.78rem;
 font-weight: 700;
 }
 /* Persistent license/version line (AGPLv3 §5(d) "Appropriate Legal
 Notices"). Rendered on every page – including the pre-auth login
 – so the running program always surfaces its name, version and
 license, plus a route to the full notice. Kept deliberately
 minimal: the copyright, no-warranty text and source/license
 links live on the dedicated ``/about`` page so this line stays
 clean beside the link. */
 .license-footer {
 margin-top: 18px;
 padding: 12px 4px 4px;
 border-top: 1px solid var(--border-soft);
 color: var(--muted);
 font-size: 0.78rem;
 line-height: 1.5;
 text-align: center;
 }
 .license-footer a {
 color: var(--accent);
 text-decoration: none;
 font-weight: 600;
 }
 .license-footer a:hover { text-decoration: underline; }
 .license-footer .sep { opacity: 0.5; margin: 0 0.4rem; }
 /* Full verbatim license text on the /about page. Scrollable so the
 ~660-line AGPLv3 doesn't dominate the page, monospace + preserved
 wrapping so the FSF formatting stays intact. */
 .license-text {
 margin: 0;
 color: var(--muted);
 font-family: inherit;
 font-size: 0.84rem;
 line-height: 1.6;
 white-space: pre-wrap;
 word-break: break-word;
 }
 .peers-list {
 background: rgba(255, 255, 255, 0.03);
 border: 1px solid rgba(255, 255, 255, 0.06);
 border-radius: 0.8rem;
 padding: 9px;
 }
 .peer-item {
 display: flex;
 align-items: center;
 gap: 8px;
 padding: 8px 10px;
 border-radius: 0.65rem;
 margin-bottom: 5px;
 background: rgba(255, 255, 255, 0.02);
 }
 .peer-item.local { border: 1px solid rgba(255, 188, 0, 0.4); background: rgba(255, 188, 0, 0.05); }
 .peer-item.online { border: 1px solid rgba(125, 229, 159, 0.35); }
 .peer-item.offline { border: 1px solid rgba(255, 140, 140, 0.22); opacity: 0.75; }
 .peer-status { color: var(--ok); font-size: 1.1rem; }
 .peer-item.offline .peer-status { color: var(--danger); }
 .peer-name { flex: 1; font-weight: 600; }
 .peer-name em { color: var(--muted); font-style: italic; font-weight: 400; }
 .peer-address { color: var(--muted); font-size: 0.84rem; }
 .no-peers { color: var(--muted); padding: 14px; text-align: center; font-style: italic; }
 .ndi-row { align-items: flex-end; }
 .htmx-request { opacity: 0.83; }
 /* Metric echo: shows canonical value under length/speed input when imperial display is active. */
 .metric-echo { display: block; margin-top: 2px; color: var(--muted); font-size: 0.8rem; }
 /* htmx adds ``.htmx-request`` to any element with an in-flight
 fetch – including polling roots like the marker catalog,
 overview, detection, and general status blocks. For a 1.5–5 s
 poll cadence that's an opacity dip 12–40 times per minute and
 reads as constant flicker on the whole section. Suppress the
 dip on any element whose ``hx-trigger`` includes ``every``;
 click-driven requests (Save buttons etc.) keep the feedback. */
 [hx-trigger*="every"].htmx-request { opacity: 1; }

 /* Circle-swatch colour picker: replaces native ``<input type="color">``
 for consistent palette-aware UI across all colour fields. */
 .color-swatch-trigger {
 width: 1.8rem;
 height: 1.8rem;
 padding: 0;
 border-radius: 50%;
 border: 2px solid var(--border);
 cursor: pointer;
 vertical-align: middle;
 transition: box-shadow 0.12s ease, border-color 0.12s ease;
 }
 .color-swatch-trigger:hover,
 .color-swatch-trigger:focus-visible {
 border-color: var(--accent);
 outline: none;
 box-shadow: 0 0 0 2px var(--accent-soft);
 }
 .color-picker-popover {
 z-index: 1000;
 padding: 10px;
 border-radius: 10px;
 background: var(--bg-base);
 border: 1px solid var(--border);
 box-shadow: 0 6px 24px rgba(0, 0, 0, 0.5);
 display: flex;
 flex-direction: column;
 gap: 8px;
 }
 .color-picker-grid {
 display: grid;
 grid-template-columns: repeat(5, 1.6rem);
 gap: 6px;
 }
 .color-picker-greys {
 display: grid;
 grid-template-columns: repeat(5, 1.6rem);
 gap: 6px;
 padding-top: 6px;
 border-top: 1px solid var(--border-soft);
 }
 .color-picker-swatch {
 width: 1.6rem;
 height: 1.6rem;
 padding: 0;
 border-radius: 50%;
 border: 2px solid transparent;
 cursor: pointer;
 transition: transform 0.08s ease;
 }
 .color-picker-swatch:hover { transform: scale(1.12); }
 .color-picker-swatch.selected {
 border-color: var(--accent);
 box-shadow: 0 0 0 2px var(--accent-soft);
 }
 .color-picker-swatch:focus-visible {
 outline: none;
 border-color: var(--accent);
 }
 /* Mirror the 5-column swatch grid above so the hash sits under
 column 1, the hex input spans columns 2–4, and the preview
 circle lands directly below the rightmost swatch column. Keeps
 the popover compact and visually aligned without a second
 layout system. */
 .color-picker-hex {
 display: grid;
 grid-template-columns: repeat(5, 1.6rem);
 gap: 6px;
 align-items: center;
 padding-top: 6px;
 border-top: 1px solid var(--border-soft);
 }
 .color-picker-hash {
 color: var(--muted);
 font-family: ui-monospace, monospace;
 text-align: center;
 grid-column: 1;
 }
 .color-picker-hex input[type="text"] {
 grid-column: 2 / 5;
 min-width: 0;
 padding: 4px 6px;
 background: var(--surface);
 color: var(--text);
 border: 1px solid var(--border);
 border-radius: 4px;
 font-family: ui-monospace, monospace;
 text-transform: uppercase;
 }
 .color-picker-preview {
 width: 1.6rem;
 height: 1.6rem;
 border-radius: 50%;
 border: 1px solid var(--border);
 grid-column: 5;
 }
 .toast {
 position: fixed;
 right: 16px;
 bottom: 16px;
 border: 1px solid rgba(255, 188, 0, 0.4);
 background: rgba(7, 19, 13, 0.95);
 color: var(--accent);
 padding: 10px 16px;
 border-radius: 999px;
 opacity: 0;
 transition: opacity 0.3s;
 font-weight: 600;
 }
 .toast.show { opacity: 1; }
 /* Tab navigation */
 .tab-bar {
 display: flex;
 gap: 0.25rem;
 margin-bottom: 1rem;
 padding: 0.35rem;
 border-radius: 1rem;
 background: var(--surface);
 border: 1px solid var(--border-soft);
 overflow-x: auto;
 -webkit-overflow-scrolling: touch;
 scrollbar-width: none;
 }
 .tab-bar::-webkit-scrollbar { display: none; }
 .tab-btn {
 flex: 1;
 min-width: fit-content;
 padding: 0.55rem 1rem;
 border-radius: 0.75rem;
 border: 1px solid transparent;
 background: transparent;
 color: var(--muted);
 font-size: 0.84rem;
 font-weight: 600;
 letter-spacing: 0.01em;
 white-space: nowrap;
 cursor: pointer;
 transition: all 0.15s ease;
 }
 .tab-btn:hover {
 color: var(--text);
 background: rgba(255, 255, 255, 0.04);
 transform: none;
 filter: none;
 }
 .tab-btn.active {
 color: var(--accent);
 background: var(--accent-soft);
 border-color: rgba(255, 188, 0, 0.35);
 }
 .tab-content { display: none; }
 .tab-content.active { display: block; }
 /* Per-row tab bar inside OSC binding editor. Distinct classes from page-level tabs. */
 .row-tab-bar {
 display: flex;
 gap: 0.25rem;
 padding: 0.25rem;
 border-radius: 0.6rem;
 background: rgba(255, 255, 255, 0.03);
 border: 1px solid var(--border-soft);
 margin-bottom: 0.6rem;
 }
 .row-tab-btn {
 flex: 1;
 padding: 0.4rem 0.7rem;
 border-radius: 0.45rem;
 border: 1px solid transparent;
 background: transparent;
 color: var(--muted);
 font-size: 0.8rem;
 font-weight: 600;
 cursor: pointer;
 }
 .row-tab-btn.active {
 color: var(--accent);
 background: var(--accent-soft);
 border-color: rgba(255, 188, 0, 0.35);
 }
 .row-tab-panel { display: none; }
 .row-tab-panel.active { display: block; }
 .osc-binding-row, .osc-destination-row { margin-bottom: 0.6rem; border: 1px solid var(--border-soft); border-radius: 0.6rem; padding: 0.5rem 0.8rem; background: var(--surface); }
 .osc-binding-summary, .osc-destination-summary { display: flex; gap: 0.6rem; align-items: center; cursor: pointer; padding-bottom: 0.2rem; }
 /* Breathing room between the summary and the editor body when the
 row is expanded (``<details open>``). When collapsed the
 ``.osc-binding-form`` is ``display: none`` so the margin is a
 no-op. */
 .osc-binding-row[open] > .osc-binding-form,
 .osc-destination-row[open] > .osc-destination-form { margin-top: 0.9rem; }
 /* Drag handle: visible click target inside the collapsed-row
 summary that the operator grabs to reorder rows. The actual
 drag-and-drop wiring lives in the document-level JS at the
 bottom of this file. ``cursor: grab`` flips to ``grabbing``
 while the row is mid-drag. */
 .osc-binding-drag-handle, .osc-destination-drag-handle {
 cursor: grab;
 color: var(--muted);
 font-family: ui-monospace, monospace;
 font-size: 0.95rem;
 padding: 0 0.25rem;
 user-select: none;
 letter-spacing: -0.1em;
 }
 .osc-binding-drag-handle:hover, .osc-destination-drag-handle:hover { color: var(--text); }
 .osc-binding-drag-handle:active, .osc-destination-drag-handle:active { cursor: grabbing; }
 .osc-binding-row.dragging, .osc-destination-row.dragging { opacity: 0.55; }
 .osc-binding-row.drop-target, .osc-destination-row.drop-target { outline: 2px dashed var(--accent); outline-offset: 2px; }
 .osc-binding-enabled-dot { width: 0.55rem; height: 0.55rem; border-radius: 999px; background: var(--muted); flex: none; }
 .osc-binding-enabled-dot.on { background: var(--accent); }
 .osc-binding-enabled-dot.invalid { background: var(--danger); }
 .osc-binding-kind-badge, .osc-destination-proto-badge { font-size: 0.7rem; padding: 0.1rem 0.4rem; border-radius: 0.4rem; background: rgba(255,255,255,0.05); color: var(--muted); }
 .osc-binding-marker-badge { font-size: 0.7rem; padding: 0.1rem 0.4rem; border-radius: 0.4rem; background: rgba(255,255,255,0.05); color: var(--muted); }
 .osc-binding-target { color: var(--muted); font-size: 0.8rem; margin-left: auto; }
 /* Secondary markers nested under a fanned-out transmitter row: read-only
 chips sharing the parent's destination / message / trigger. */
 /* Negative top pulls the chips up under their parent row (the row's
    0.6rem bottom margin otherwise floats them); the bottom margin keeps a
    clear gap before the next transmitter so they don't touch. */
 .osc-binding-nested { display: flex; flex-direction: column; gap: 0.4rem; margin: -0.2rem 0 0.7rem 1.4rem; }
 .osc-binding-nested-row { display: flex; align-items: center; gap: 0.5rem; font-size: 0.85rem; color: var(--muted); padding: 0.45rem 0.8rem; border: 1px solid var(--border-soft); border-radius: 0.5rem; background: rgba(255,255,255,0.02); }
 /* The red dot alone marks an uncontrolled marker – no red border. */
 .osc-binding-nested-row.is-invalid { color: var(--danger); }
 /* Destination collapsed summary: host:port sits right after the name; the
 protocol badge anchors to the right. */
 .osc-destination-addr { color: var(--muted); font-size: 0.8rem; }
 .osc-destination-proto-badge { margin-left: auto; }
 .args-list { display: flex; flex-direction: column; gap: 0.3rem; margin-bottom: 0.4rem; }
 .arg-pill-input { font-family: ui-monospace, monospace; }
 .args-buttons { display: flex; gap: 0.3rem; }
 /* Legacy ``.diag-pre``: still used by the basic raw-JSON drop in
 a few places. The newer rule below (with line-height + better
 padding) is in the diagnostics-tab block – when both selectors
 match, the later rule wins. */
 /* Operator-feedback follow-up: redesigned OSC bindings
 Diagnostics tab. Three stacked panels (Live status / Preview /
 Test send), each in a ``.stat-panel`` card with a labelled
 header + dedicated body area. Replaces the cramped
 row-of-buttons-+-raw-JSON layout that used to dominate the
 tab. ``.diag-grid`` stacks the panels vertically with
 breathing room; the body areas hold the structured renderer
 output (address / args table for Preview, key-value rows for
 Live status, etc.). */
 /* Shared diagnostics-tab styles. Used by OSC Output Diagnostics
 (Live status / Preview / Test send) AND Zone Editor Diagnostics
 (Live state / Test send) – same visual language so an operator
 moving between the two surfaces gets a consistent layout. The
 ``osc-diag-*`` data attributes on OSC binding rows still scope
 the JS-side per-row hooks; only the CSS class names are shared. */
 .diag-grid {
 display: grid;
 gap: 0.55rem;
 }
 .diag-card .stat-panel-head {
 align-items: center;
 gap: 0.5rem;
 margin-bottom: 0.35rem;
 }
 .diag-card .stat-help {
 margin: 0 0 0.45rem;
 font-size: 0.78rem;
 color: var(--muted);
 line-height: 1.35;
 }
 .diag-card.stat-panel {
 padding: 0.65rem 0.75rem;
 }
 .diag-action {
 appearance: none;
 border: 1px solid var(--border);
 background: transparent;
 color: var(--text);
 font: inherit;
 font-size: 0.78rem;
 padding: 0.26rem 0.65rem;
 border-radius: 0.4rem;
 cursor: pointer;
 white-space: nowrap;
 }
 .diag-action:hover { background: rgba(255, 255, 255, 0.05); }
 .diag-action.primary {
 background: var(--accent);
 color: #07130d;
 border-color: transparent;
 font-weight: 600;
 }
 .diag-actions {
 display: flex;
 gap: 0.35rem;
 flex-wrap: wrap;
 }
 .diag-body {
 display: flex;
 flex-direction: column;
 gap: 0.3rem;
 line-height: 1.4;
 }
 .diag-row {
 display: grid;
 grid-template-columns: 6rem 1fr;
 gap: 0.55rem;
 align-items: baseline;
 padding: 0.18rem 0;
 }
 .diag-row dt {
 margin: 0;
 color: var(--muted);
 font-size: 0.72rem;
 letter-spacing: 0.06em;
 text-transform: uppercase;
 font-weight: 600;
 }
 .diag-row dd {
 margin: 0;
 font-family: ui-monospace, monospace;
 font-size: 0.84rem;
 color: var(--text);
 word-break: break-all;
 }
 .diag-args {
 display: flex;
 flex-wrap: wrap;
 gap: 0.28rem;
 }
 .diag-arg {
 font-family: ui-monospace, monospace;
 font-size: 0.78rem;
 padding: 0.1rem 0.45rem;
 border-radius: 0.35rem;
 background: rgba(255, 255, 255, 0.05);
 border: 1px solid var(--border-soft);
 }
 .diag-empty {
 color: var(--muted);
 font-style: italic;
 font-size: 0.82rem;
 }
 .diag-pre {
 background: rgba(0, 0, 0, 0.22);
 border: 1px solid var(--border-soft);
 padding: 0.45rem 0.55rem;
 border-radius: 0.4rem;
 font-family: ui-monospace, monospace;
 font-size: 0.74rem;
 line-height: 1.35;
 max-height: 10rem;
 overflow: auto;
 margin: 0.3rem 0 0;
 white-space: pre-wrap;
 word-break: break-word;
 }
 .diag-error {
 color: #ff8a8a;
 background: rgba(255, 140, 140, 0.1);
 border: 1px solid rgba(255, 140, 140, 0.3);
 padding: 0.5rem 0.7rem;
 border-radius: 0.5rem;
 font-size: 0.85rem;
 }
 .diag-status-pill {
 display: inline-flex;
 align-items: center;
 gap: 0.3rem;
 padding: 0.14rem 0.6rem;
 border-radius: 999px;
 font-size: 0.72rem;
 letter-spacing: 0.06em;
 text-transform: uppercase;
 font-weight: 700;
 }
 .diag-status-pill.healthy {
 color: #c8ffd8;
 border: 1px solid rgba(125, 229, 159, 0.4);
 background: rgba(125, 229, 159, 0.14);
 }
 .diag-status-pill.unhealthy {
 color: #ffd7d7;
 border: 1px solid rgba(255, 140, 140, 0.4);
 background: rgba(255, 140, 140, 0.13);
 }
 .diag-status-pill.unknown {
 color: var(--muted);
 border: 1px solid var(--border);
 background: rgba(255, 255, 255, 0.03);
 }
 .diag-events {
 list-style: none;
 margin: 0.3rem 0 0;
 padding: 0;
 display: flex;
 flex-direction: column;
 gap: 0.25rem;
 max-height: 12rem;
 overflow-y: auto;
 }
 .diag-event {
 display: grid;
 grid-template-columns: 4.2rem 4rem 1fr;
 gap: 0.5rem;
 align-items: baseline;
 font-family: ui-monospace, monospace;
 font-size: 0.78rem;
 padding: 0.25rem 0.45rem;
 border-radius: 0.35rem;
 background: rgba(0, 0, 0, 0.18);
 }
 .diag-event-status {
 font-weight: 700;
 letter-spacing: 0.04em;
 text-transform: uppercase;
 font-size: 0.7rem;
 }
 .diag-event-status.sent { color: #c8ffd8; }
 .diag-event-status.skipped { color: #ffd7a8; }
 .diag-event-detail { color: var(--muted); word-break: break-all; }
 .diag-raw {
 margin-top: 0.6rem;
 font-size: 0.78rem;
 }
 .diag-raw > summary {
 cursor: pointer;
 color: var(--muted);
 letter-spacing: 0.04em;
 text-transform: uppercase;
 font-size: 0.7rem;
 padding: 0.15rem 0;
 }
 .diag-raw > summary:hover { color: var(--text); }
 .diag-raw[open] > summary { color: var(--text); margin-bottom: 0.3rem; }
 .placeholder-help { font-size: 0.78rem; color: var(--muted); }
 /* Combined OSC message editor: monospace input with chip row below for quick-insert access. */
 .osc-message-input { font-family: ui-monospace, monospace; width: 100%; }
 /* Pills in OSC message editor: ``[placeholder]`` patterns rendered as
 ``contenteditable=false`` spans for atomic deletion; hidden ``<input>`` mirrors serialization. */
 .osc-message-editor {
 font-family: ui-monospace, monospace;
 width: 100%;
 min-height: 2.4rem;
 padding: 0.45rem 0.6rem;
 border-radius: 0.45rem;
 border: 1px solid var(--border-soft);
 background: var(--surface);
 color: var(--text);
 white-space: nowrap;
 overflow-x: auto;
 cursor: text;
 line-height: 1.6;
 }
 .osc-message-editor:focus {
 outline: none;
 border-color: var(--accent);
 box-shadow: 0 0 0 2px var(--accent-soft);
 }
 .osc-message-editor:empty::before {
 content: attr(data-osc-message-placeholder);
 color: var(--muted);
 pointer-events: none;
 }
 .osc-pill {
 display: inline-block;
 font-family: ui-monospace, monospace;
 font-size: 0.85rem;
 padding: 0.05rem 0.4rem;
 margin: 0 0.1rem;
 border-radius: 0.35rem;
 background: var(--accent-soft);
 color: var(--accent);
 border: 1px solid rgba(255, 188, 0, 0.35);
 white-space: nowrap;
 vertical-align: baseline;
 user-select: all;
 cursor: text;
 }
 .osc-pill:hover { border-color: var(--accent); }
 /* pill rendering for unresolved placeholders.
 The ``data-unresolved="true"`` flag is set by the pill JS when
 the placeholder's dependency isn't satisfied ([x] without a
 configured default marker, or [x:N] when N isn't
 registered). The danger palette mirrors aria-invalid styling
 on inputs so the operator sees the same "this needs
 attention" cue across the form. */
 .osc-pill[data-unresolved="true"] {
 background: rgba(220, 60, 60, 0.14);
 color: #ff9b9b;
 border-color: rgba(220, 60, 60, 0.55);
 }
 .osc-pill[data-unresolved="true"]:hover {
 border-color: #ff7070;
 }
 /* Pill rendered for a bracketed token whose name isn't a
 recognised placeholder (``[xyz]`` / ``[bogus]``). The server-side
 compiler treats these as literal text, but the editor used to
 render them as a normal accent-coloured pill – visually
 indistinguishable from a real placeholder. The dashed warning
 palette signals "this looks like a placeholder but it's actually
 literal text". ``[data-unresolved="true"]`` (red, dependency
 missing) takes precedence when both apply, since the dependency
 hint is the more actionable error. */
 .osc-pill[data-invalid="true"] {
 background: rgba(255, 178, 102, 0.1);
 color: #ffc187;
 border: 1px dashed rgba(255, 178, 102, 0.55);
 }
 .osc-pill[data-invalid="true"]:hover {
 border-color: #ffb066;
 }
 .osc-pill[data-invalid="true"][data-unresolved="true"] {
 background: rgba(220, 60, 60, 0.14);
 color: #ff9b9b;
 border: 1px dashed rgba(220, 60, 60, 0.55);
 }
 .placeholder-buttons { flex-wrap: wrap; gap: 0.25rem; }
 .placeholder-chip {
 font-family: ui-monospace, monospace;
 font-size: 0.78rem;
 padding: 0.18rem 0.45rem;
 border-radius: 0.35rem;
 border: 1px solid var(--border-soft);
 background: rgba(255, 255, 255, 0.04);
 color: var(--accent);
 cursor: pointer;
 }
 .placeholder-chip:hover { background: var(--accent-soft); }
 .inline-label { color: var(--muted); font-size: 0.8rem; align-self: center; }
 .osc-bindings-toolbar { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; }
 .badge-experimental {
 display: inline-block;
 padding: 0.1rem 0.45rem;
 border-radius: 999px;
 font-size: 0.62rem;
 font-weight: 700;
 letter-spacing: 0.06em;
 text-transform: uppercase;
 background: rgba(255, 188, 0, 0.14);
 border: 1px solid rgba(255, 188, 0, 0.35);
 color: #ffe7ae;
 vertical-align: middle;
 margin-left: 0.3rem;
 }
 /* Hide experimental sections unless <body> carries .show-experimental. */
 body:not(.show-experimental) .experimental-feature { display: none !important; }
 @media (max-width: 680px) {
 body { padding: 12px 0; }
 .hero-panel { padding: 1rem; margin-bottom: 0.8rem; }
 .section { padding: 1rem; }
 .field, .field.wide, .checkbox-field { min-width: 100%; }
 .fields-grid { grid-template-columns: 1fr; }
 .fields-grid .field.span-2, .fields-grid .field.span-3 { grid-column: auto; }
 .actions button { width: 100%; }
 .peer-item { flex-wrap: wrap; }
 .peer-address { width: 100%; }
 .tab-btn { padding: 0.45rem 0.7rem; font-size: 0.78rem; }
 .network-state-grid { grid-template-columns: 1fr; }
 }
 /* ---- Network state read-only block -----------
 Two-column grid (label/value) inside each .group for DNS lists.
 Collapses to single column under responsive breakpoint. */
 .network-state-card { margin-top: 0.4rem; }
 .network-state-banner {
 margin-bottom: 0.8rem;
 padding: 0.5rem 0.7rem;
 border-left: 3px solid rgba(247, 245, 233, 0.32);
 background: rgba(247, 245, 233, 0.04);
 color: rgba(247, 245, 233, 0.7);
 font-size: 0.82rem;
 }
 .network-state-grid {
 display: grid;
 grid-template-columns: minmax(7rem, 9rem) 1fr;
 gap: 0.35rem 0.9rem;
 margin: 0;
 }
 .network-state-grid dt {
 color: var(--muted);
 font-size: 0.78rem;
 align-self: center;
 }
 .network-state-grid dd {
 margin: 0;
 color: rgba(247, 245, 233, 0.95);
 font-variant-numeric: tabular-nums;
 align-self: center;
 word-break: break-word;
 }
 .network-state-card .group { padding-bottom: 0.6rem; margin-bottom: 0.6rem; }
 .network-state-card .group:last-child { padding-bottom: 0; margin-bottom: 0; }
 .network-state-card .muted { color: var(--muted); margin: 0; }
 /* Network result banner and disconnect warning. */
 .network-banner {
 margin-bottom: 0.8rem;
 padding: 0.5rem 0.7rem;
 border-left: 3px solid var(--accent);
 background: var(--accent-soft);
 font-size: 0.85rem;
 }
 .network-banner-ok { border-left-color: var(--ok); background: rgba(125, 229, 159, 0.1); }
 .network-banner-error { border-left-color: var(--danger); background: rgba(255, 140, 140, 0.1); }
 .network-warning {
 margin: 0.6rem 0;
 padding: 0.45rem 0.7rem;
 border-left: 3px solid var(--accent);
 background: var(--accent-soft);
 color: var(--muted);
 font-size: 0.8rem;
 }
 /* Mode bar at the top of the network form: names the current mode and,
 in view mode, offers a text-link switch (not a button) so the unlock
 action doesn't read as a Save. */
 .net-mode-bar {
 display: flex;
 flex-wrap: wrap;
 align-items: center;
 gap: 0.4rem 0.7rem;
 margin-bottom: 0.8rem;
 padding: 0.5rem 0.7rem;
 border-left: 3px solid var(--border);
 background: rgba(247, 245, 233, 0.04);
 font-size: 0.82rem;
 }
 .net-mode-bar.edit { border-left-color: var(--accent); background: var(--accent-soft); }
 .net-mode-text { color: var(--muted); }
 .net-mode-pill {
 display: inline-flex;
 align-items: center;
 padding: 0.14rem 0.6rem;
 border-radius: 999px;
 font-size: 0.72rem;
 letter-spacing: 0.06em;
 text-transform: uppercase;
 font-weight: 700;
 white-space: nowrap;
 }
 .net-mode-pill.view, .net-mode-pill.readonly {
 color: var(--muted);
 border: 1px solid var(--border);
 background: rgba(247, 245, 233, 0.05);
 }
 .net-mode-pill.edit {
 color: #ffe6a8;
 border: 1px solid var(--btn-secondary-border);
 background: var(--accent-soft);
 }
 /* Small gold-outline button (no fill, via .secondary .small), right-aligned. */
 .net-mode-switch { margin-left: auto; }
 /* Nested foldable sub-sections: tighter padding with subtle left
 border for visual nesting. Hit target inherits from .section-toggle. */
 .section.subsection {
 padding: 0.9rem 1rem;
 margin: 0.6rem 0;
 border-left: 3px solid rgba(247, 245, 233, 0.12);
 background: rgba(247, 245, 233, 0.02);
 }
 .section.subsection > .section-head h2 { font-size: 1.05rem; }
 /* ---- Modal component -----------------------------------
 Reusable overlay for template flows. Single #modal-root in body;
 only one open at a time. Hidden attribute prevents pointer events. */
 .modal-backdrop {
 position: fixed;
 inset: 0;
 background: rgba(0, 0, 0, 0.55);
 backdrop-filter: blur(2px);
 z-index: 1000;
 display: flex;
 align-items: center;
 justify-content: center;
 padding: 20px;
 }
 .modal-backdrop[hidden] { display: none; }
 .modal-card {
 width: min(560px, 100%);
 max-height: calc(100vh - 40px);
 display: flex;
 flex-direction: column;
 background: var(--bg-soft);
 border: 1px solid var(--border);
 border-radius: 1rem;
 box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
 overflow: hidden;
 }
 .modal-header {
 display: flex;
 align-items: center;
 gap: 0.75rem;
 padding: 1rem 1.2rem;
 border-bottom: 1px solid var(--border-soft);
 }
 .modal-title {
 margin: 0;
 flex: 1;
 font-size: 1.05rem;
 letter-spacing: -0.01em;
 color: var(--text);
 }
 .modal-close {
 appearance: none;
 border: 0;
 background: transparent;
 color: var(--muted);
 font-size: 1.4rem;
 line-height: 1;
 padding: 0.2rem 0.55rem;
 border-radius: 999px;
 cursor: pointer;
 }
 .modal-close:hover { background: rgba(255, 255, 255, 0.06); color: var(--text); }
 .modal-body {
 padding: 1rem 1.2rem;
 color: var(--text);
 overflow-y: auto;
 flex: 1;
 }
 .modal-body p { margin: 0 0 0.7rem; line-height: 1.45; }
 .modal-body label { display: block; font-size: 0.82rem; color: var(--muted); margin-bottom: 0.3rem; }
 .modal-body input[type="text"] {
 width: 100%;
 padding: 0.55rem 0.7rem;
 border-radius: 0.6rem;
 border: 1px solid var(--border);
 background: var(--bg-deep);
 color: var(--text);
 font: inherit;
 }
 .modal-footer {
 display: flex;
 gap: 0.55rem;
 justify-content: flex-end;
 padding: 0.85rem 1.2rem;
 border-top: 1px solid var(--border-soft);
 }
 /* Modal footer buttons reuse the global button system: a bare
 button reads as secondary; ``.primary`` and ``.danger`` map to
 the shared variants so modal and inline buttons match. */
 .modal-footer button {
 background: transparent;
 border: 1px solid var(--btn-secondary-border);
 color: var(--text);
 font-weight: 600;
 }
 .modal-footer button.primary {
 background: var(--btn-primary-bg);
 color: var(--btn-ink-dark);
 border-color: transparent;
 }
 .modal-footer button.danger {
 background: transparent;
 color: var(--danger);
 border-color: var(--btn-danger-border);
 }
 /* Progress row: spinner + status text, used by the locked update modal. */
 .modal-progress { display: flex; align-items: center; gap: 0.7rem; }
 .modal-spinner {
 flex: none;
 width: 1.2rem;
 height: 1.2rem;
 border: 2px solid rgba(255, 188, 0, 0.25);
 border-top-color: var(--accent);
 border-radius: 50%;
 animation: modal-spin 0.7s linear infinite;
 }
 @keyframes modal-spin { to { transform: rotate(360deg); } }
 @media (prefers-reduced-motion: reduce) {
 .modal-spinner { animation-duration: 2s; }
 }
 .modal-list {
 list-style: none;
 margin: 0;
 padding: 0;
 display: flex;
 flex-direction: column;
 gap: 0.4rem;
 }
 .modal-list-item {
 display: flex;
 align-items: center;
 gap: 0.6rem;
 padding: 0.6rem 0.75rem;
 border-radius: 0.55rem;
 border: 1px solid var(--border-soft);
 background: rgba(255, 255, 255, 0.03);
 }
 .modal-list-item:hover { border-color: var(--border); }
 /* Disabled chooser rows: muted styling for legibility; Apply button carries ``disabled`` attr. */
 .modal-list-item-disabled {
 opacity: 0.55;
 border-style: dashed;
 }
 .modal-list-item-disabled .modal-list-item-name {
 font-weight: 500;
 font-style: italic;
 }
 .modal-list-item-apply:disabled {
 opacity: 0.45;
 cursor: not-allowed;
 }
 .modal-list-item-name { flex: 1; font-weight: 600; }
 .modal-list-item-badge {
 font-size: 0.66rem;
 letter-spacing: 0.06em;
 text-transform: uppercase;
 padding: 0.12rem 0.45rem;
 border-radius: 999px;
 border: 1px solid rgba(255, 255, 255, 0.18);
 color: var(--muted);
 }
 .modal-list-item-badge.system {
 color: #c8ffd8;
 border-color: rgba(125, 229, 159, 0.4);
 background: rgba(125, 229, 159, 0.12);
 }
 .modal-list-item-apply,
 .modal-list-item-delete {
 appearance: none;
 border: 0;
 background: transparent;
 color: var(--text);
 font: inherit;
 font-size: 0.82rem;
 padding: 0.25rem 0.55rem;
 border-radius: 0.4rem;
 cursor: pointer;
 }
 .modal-list-item-apply:hover { background: rgba(255, 188, 0, 0.16); }
 .modal-list-item-delete { color: var(--danger); }
 .modal-list-item-delete:hover { background: rgba(255, 140, 140, 0.14); }
 .modal-empty {
 color: var(--muted);
 font-style: italic;
 text-align: center;
 padding: 1.5rem 0.5rem;
 }
 .modal-error {
 color: #ff8a8a;
 background: rgba(255, 140, 140, 0.1);
 border: 1px solid rgba(255, 140, 140, 0.3);
 padding: 0.5rem 0.7rem;
 border-radius: 0.5rem;
 margin-bottom: 0.7rem;
 font-size: 0.85rem;
 }
 </style>
</head>
<body class="{{'show-experimental' if defined('config') and config and config.ui.show_experimental_features else ''}}">
 <div class="container">
 <header class="hero-panel">
 <a class="hero-logo-link" href="/" aria-label="OpenFollow – back to overview">
 <img class="hero-logo" src="/assets/openfollow.svg?v={{_asset_v}}" alt="OpenFollow logo">
 </a>
 % if defined('config') and config:
 <div class="station-name-pill hero-station-pill" title="Station name (psn_system_name) – set on the General tab">
 {{config.psn_system_name}}
 </div>
 % end
 </header>
 {{!base}}
 % if defined('on_device') and on_device:
 <footer class="on-device-footer">
 %# Render the operator's actual ``btn_menu_cancel`` mapping, not
 %# a hardcoded "B" – Settings → Button Detection can remap this
 %# to any of A/B/X/Y/etc., and a stale hint here would tell
 %# operators to press a button that no longer dismisses the
 %# overlay.
 <span><kbd>{{cancel_button or 'Cancel'}}</kbd>Close embedded browser</span>
 </footer>
 % end
 %# AGPLv3 §5(d) Appropriate Legal Notices. __version__ comes from
 %# __init__.py so the footer tracks running code, not install metadata.
 % from openfollow import __commit__, __version__
 <footer class="license-footer" role="contentinfo">
 OpenFollow v{{__version__}}{{ ' (' + __commit__ + ')' if __commit__ else '' }}
 <span class="sep">·</span>
 © 2026 The OpenFollow Project
 <span class="sep">·</span>
 <a href="/about">About &amp; license</a>
 </footer>
 </div>
 <div id="toast" class="toast"></div>
 <!-- Single ``#modal-root`` shared by all modals via helper functions.
 Helpers populate ``.modal-card`` and toggle ``hidden`` on backdrop. -->
 <div id="modal-root" class="modal-backdrop" hidden role="dialog" aria-modal="true" aria-labelledby="modal-title">
 <div class="modal-card">
 <div class="modal-header">
 <h2 class="modal-title" id="modal-title"></h2>
 <button type="button" class="modal-close" data-modal-close aria-label="Close">&times;</button>
 </div>
 <div class="modal-body" id="modal-body"></div>
 <div class="modal-footer" id="modal-footer"></div>
 </div>
 </div>
 <!-- inline help drawer. Non-modal side panel: slides in from
 the right (CSS ``body.help-open``), shifts the content column left via
 <body> padding rather than covering it, and scrolls independently. The
 body is filled by ``openHelp()`` with the server-rendered ``/help/<id>``
 fragment. ``inert`` + ``aria-hidden`` keep it out of the tab order and
 the a11y tree while it's closed / off-screen. -->
 <aside id="help-drawer" class="help-drawer" role="dialog" aria-modal="false"
 aria-labelledby="help-drawer-title" aria-hidden="true" inert>
 <div class="help-drawer-head">
 <h2 id="help-drawer-title">Help</h2>
 <button type="button" class="help-drawer-close" data-help-close
 aria-label="Close help">&times;</button>
 </div>
 <div class="help-drawer-body" id="help-drawer-body" tabindex="-1"></div>
 </aside>
 <script>
 // Schedule client-side reload for static/manual address apply (single-NIC only).
 // On multi-NIC, HX-Redirect returns and unloads this page first, cancelling the timer.
 function netScheduleReload(el) {
 const form = el.closest('form');
 if (!form) return;
 const method = (form.querySelector('[name=method]') || {}).value;
 const addr = (form.querySelector('[name=address]') || {}).value;
 if (!((method === 'static' || method === 'dhcp_manual') && addr)) return;
 const port = location.port ? (':' + location.port) : '';
 const url = location.protocol + '//' + addr + port + '/';
 const timer = setTimeout(function () { window.location.href = url; }, 6000);
 // The blind reload is ONLY for the single-NIC case where the apply tears
 // down this very connection and no response can return. If the server
 // actually answers – a validation error, an adapter failure, or a
 // multi-NIC success with HX-Redirect – a response *was* received, so
 // cancel the timer and let htmx handle the swap/redirect. status === 0
 // means the connection was severed (no response): leave it armed.
 const cancel = function (evt) {
 const d = evt.detail || {};
 if (d.elt !== form) return; // ignore bubbled child requests
 if (d.xhr && d.xhr.status !== 0) {
 clearTimeout(timer);
 }
 form.removeEventListener('htmx:afterRequest', cancel);
 };
 form.addEventListener('htmx:afterRequest', cancel);
 }

 const sectionFoldStoragePrefix = 'psnfs:section:';
 const advancedFoldStoragePrefix = 'psnfs:advanced:';
 function getSectionFoldStorageKey(key) {
 return `${sectionFoldStoragePrefix}${key}`;
 }
 function getAdvancedFoldStorageKey(key) {
 return `${advancedFoldStoragePrefix}${key}`;
 }
 function readStorage(key) {
 try {
 return localStorage.getItem(key);
 } catch (_) {
 return null;
 }
 }
 function writeStorage(key, value) {
 try {
 localStorage.setItem(key, value);
 } catch (_) {
 // Ignore storage errors and keep UI functional.
 }
 }
 function setSectionCollapsed(section, button, collapsed) {
 section.classList.toggle('is-collapsed', collapsed);
 button.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
 // Only rewrite the label when it actually changes. Assigning
 // ``textContent`` destroys + recreates the button's text node, and
 // since this runs on every ``htmx:afterSwap`` (sub-second pollers
 // fire it constantly), an unconditional assignment churned the text
 // node ~1.5x/s – a click landing ON the text was lost because its
 // target node was replaced mid-gesture, while clicks on the padding
 // (over the persistent button element) still worked.
 const label = collapsed ? 'Expand' : 'Collapse';
 if (button.textContent !== label) button.textContent = label;
 }
 function initializeSectionFolding(root) {
 const scope = root || document;
 const sections = [];
 if (scope.matches && scope.matches('.section[data-fold-key]')) {
 sections.push(scope);
 }
 scope.querySelectorAll('.section[data-fold-key]').forEach((section) => sections.push(section));
 sections.forEach((section) => {
 const head = section.querySelector('.section-head');
 if (!head) return;
 const key = section.dataset.foldKey;
 if (!key) return;

 // cluster the help "?" and the collapse toggle in a
 // right-aligned actions wrapper. Both buttons are injected (not in
 // the markup) and this function re-runs after every HTMX swap, so
 // each step is written to be idempotent: a re-run finds the existing
 // nodes and no-ops, never duplicating a button.
 let actions = head.querySelector('.section-actions');
 if (!actions) {
 actions = document.createElement('div');
 actions.className = 'section-actions';
 head.appendChild(actions);
 }
 const helpId = section.dataset.help;
 if (helpId && !actions.querySelector('.section-help-btn')) {
 const help = document.createElement('button');
 help.type = 'button';
 help.className = 'section-help-btn';
 help.setAttribute('aria-label', 'Open help for this section');
 help.dataset.helpOpen = helpId;
 help.textContent = '?';
 actions.appendChild(help); // help first → it sits left of Collapse
 }
 let toggle = actions.querySelector('.section-toggle');
 // Already initialised (this runs on every htmx:afterSwap, which
 // sub-second pollers fire constantly) – leave the existing toggle
 // entirely untouched. Re-running setSectionCollapsed here churned
 // the button's text node and ate clicks landing on the text.
 if (toggle && toggle.dataset.bound === '1') return;
 if (!toggle) {
 toggle = document.createElement('button');
 toggle.type = 'button';
 toggle.className = 'section-toggle';
 actions.appendChild(toggle);
 }

 const defaultCollapsed = section.dataset.foldDefault
 ? section.dataset.foldDefault === 'collapsed'
 : true;
 const stored = readStorage(getSectionFoldStorageKey(key));
 const collapsed = stored === null ? defaultCollapsed : stored === 'collapsed';
 setSectionCollapsed(section, toggle, collapsed);
 toggle.dataset.bound = '1';

 toggle.addEventListener('click', () => {
 const nextCollapsed = !section.classList.contains('is-collapsed');
 setSectionCollapsed(section, toggle, nextCollapsed);
 writeStorage(
 getSectionFoldStorageKey(key),
 nextCollapsed ? 'collapsed' : 'expanded'
 );
 });
 });
 }
 function initializeInlineAdvanced(root) {
 const scope = root || document;
 const detailsNodes = [];
 if (scope.matches && scope.matches('details.inline-advanced[data-adv-key]')) {
 detailsNodes.push(scope);
 }
 scope.querySelectorAll('details.inline-advanced[data-adv-key]')
 .forEach((node) => detailsNodes.push(node));
 detailsNodes.forEach((node) => {
 const key = node.dataset.advKey;
 if (!key) return;
 const stored = readStorage(getAdvancedFoldStorageKey(key));
 if (stored === 'open') node.open = true;
 else if (stored === 'closed') node.open = false;
 if (node.dataset.bound === '1') return;
 node.dataset.bound = '1';
 node.addEventListener('toggle', () => {
 writeStorage(getAdvancedFoldStorageKey(key), node.open ? 'open' : 'closed');
 });
 });
 }
 function showToast(message) {
 const toast = document.getElementById('toast');
 if (!toast) return;
 toast.textContent = message;
 toast.classList.add('show');
 setTimeout(() => toast.classList.remove('show'), 2000);
 }
 // ---- Help drawer () --------------------------------------
 //
 // Non-modal side panel showing the server-rendered help doc for a
 // section. Opened by the injected ``.section-help-btn`` ("?") and the
 // close button via *event delegation* on the body, so HTMX section
 // swaps (which replace the buttons) never strand a listener. Content is
 // fetched from ``/help/<id>.html`` and injected as a trusted, same-origin
 // fragment (the route renders first-party Markdown with raw HTML escaped).
 let _helpLastFocus = null;
 function openHelp(docId, opener) {
 const drawer = document.getElementById('help-drawer');
 const body = document.getElementById('help-drawer-body');
 if (!drawer || !body || !docId) return;
 _helpLastFocus = opener || document.activeElement;
 drawer.removeAttribute('inert');
 drawer.setAttribute('aria-hidden', 'false');
 document.body.classList.add('help-open');
 body.innerHTML = '<p class="help-loading">Loading…</p>';
 fetch('/help/' + encodeURIComponent(docId) + '.html')
 .then((res) => {
 if (!res.ok) throw new Error('status ' + res.status);
 return res.text();
 })
 .then((htmlText) => { body.innerHTML = htmlText; })
 .catch(() => {
 body.innerHTML =
 '<p class="help-loading">Help for this section isn\'t available.</p>';
 })
 .finally(() => { requestAnimationFrame(() => body.focus()); });
 }
 function closeHelp() {
 const drawer = document.getElementById('help-drawer');
 if (!drawer || !document.body.classList.contains('help-open')) return;
 document.body.classList.remove('help-open');
 drawer.setAttribute('aria-hidden', 'true');
 // Re-assert ``inert`` only once the slide-out has played, so the panel
 // animates off-screen before it leaves the a11y tree / tab order.
 drawer.addEventListener('transitionend', () => {
 if (!document.body.classList.contains('help-open')) {
 drawer.setAttribute('inert', '');
 }
 }, { once: true });
 const last = _helpLastFocus;
 _helpLastFocus = null;
 if (last && typeof last.focus === 'function') last.focus();
 }
 document.body.addEventListener('click', (event) => {
 const openBtn = event.target.closest('[data-help-open]');
 if (openBtn) { openHelp(openBtn.dataset.helpOpen, openBtn); return; }
 if (event.target.closest('[data-help-close]')) closeHelp();
 });
 document.addEventListener('keydown', (event) => {
 if (event.key !== 'Escape') return;
 if (!document.body.classList.contains('help-open')) return;
 // A modal sits above the drawer and owns ESC while open (its handler
 // returns early when ``#modal-root`` is hidden); defer to it so one
 // ESC doesn't close both.
 const modal = document.getElementById('modal-root');
 if (modal && !modal.hidden) return;
 closeHelp();
 });
 // ---- Modal helpers () ------------------------------------
 //
 // Single ``#modal-root`` reused by every dialog. The helpers
 // serialise opens (closing any current modal before opening a new
 // one) so we never paint two stacked overlays. ESC and clicks on
 // the backdrop close. Focus moves to the first focusable element
 // inside the modal on open and returns to the previously-focused
 // element on close.
 let _modalCloseHandler = null;
 let _modalLastFocus = null;
 // When false, ESC / backdrop / × can't close – locks a modal during install.
 // ``closeModal()`` stays unguarded so ``openModal`` can still swap it.
 let _modalDismissable = true;
 function closeModal() {
 const root = document.getElementById('modal-root');
 if (!root) return;
 root.hidden = true;
 _modalDismissable = true;
 const closeBtn = root.querySelector('.modal-close');
 if (closeBtn) closeBtn.hidden = false;
 const handler = _modalCloseHandler;
 _modalCloseHandler = null;
 // Restore focus before the consumer sees the close so a
 // post-close action that re-renders DOM doesn't strand focus
 // on a removed element.
 const lastFocus = _modalLastFocus;
 _modalLastFocus = null;
 if (lastFocus && typeof lastFocus.focus === 'function') {
 lastFocus.focus();
 }
 if (handler) handler();
 }
 function openModal(opts) {
 // ``opts`` keys: ``title`` (string), ``bodyHTML`` (string OR
 // DocumentFragment), ``footerButtons`` (array of {label, kind,
 // onClick} – ``kind`` ∈ "primary" | "danger" | "default"; the
 // helper wires Cancel automatically). ``onClose`` fires after
 // the modal closes for any reason (Cancel, ESC, backdrop, the
 // close button, or a footer button that calls ``closeModal``).
 // Returns nothing – caller wires its own confirm logic via
 // ``footerButtons.onClick``. Helper wrappers
 // (``modalPrompt``, ``modalConfirm``) provide the
 // promise-shaped API for common patterns.
 if (_modalCloseHandler) closeModal();
 _modalLastFocus = document.activeElement;
 const root = document.getElementById('modal-root');
 const title = document.getElementById('modal-title');
 const body = document.getElementById('modal-body');
 const footer = document.getElementById('modal-footer');
 if (!root || !title || !body || !footer) return;
 // ``dismissable`` defaults true; false hides × and locks ESC/backdrop.
 _modalDismissable = opts.dismissable !== false;
 const closeBtn = root.querySelector('.modal-close');
 if (closeBtn) closeBtn.hidden = !_modalDismissable;
 title.textContent = opts.title || '';
 // Wipe previous content. ``replaceChildren`` is the modern
 // single-call API and avoids the double-set ``innerHTML = ''``
 // followed by ``append``.
 body.replaceChildren();
 footer.replaceChildren();
 if (typeof opts.bodyHTML === 'string') {
 body.innerHTML = opts.bodyHTML;
 } else if (opts.bodyHTML instanceof Node) {
 body.appendChild(opts.bodyHTML);
 }
 const buttons = Array.isArray(opts.footerButtons) ? opts.footerButtons : [];
 buttons.forEach((spec) => {
 const btn = document.createElement('button');
 btn.type = 'button';
 btn.textContent = spec.label || '';
 if (spec.kind === 'primary') btn.classList.add('primary');
 else if (spec.kind === 'danger') btn.classList.add('danger');
 if (typeof spec.onClick === 'function') {
 btn.addEventListener('click', () => spec.onClick(btn));
 }
 footer.appendChild(btn);
 });
 _modalCloseHandler = typeof opts.onClose === 'function' ? opts.onClose : null;
 root.hidden = false;
 // Focus the first focusable inside body, or the first footer
 // button if the body has none. Without this the modal is
 // keyboard-orphaned for screen-reader / keyboard-only users.
 const focusable = body.querySelector('input, textarea, select, button, [tabindex]')
 || footer.querySelector('button');
 if (focusable && typeof focusable.focus === 'function') {
 // Defer one frame so the modal is in the layout tree before
 // we move focus – Safari otherwise no-ops on
 // immediately-shown elements.
 requestAnimationFrame(() => focusable.focus());
 }
 }
 document.addEventListener('keydown', (event) => {
 const root = document.getElementById('modal-root');
 if (!root || root.hidden) return;
 if (event.key === 'Escape') {
 event.preventDefault();
 if (_modalDismissable) closeModal();  // locked modals swallow ESC
 return;
 }
 // Trap Tab inside dialog: wrap to first/last focusable element
 // so keyboard/screen-reader users can't escape to controls behind backdrop.
 if (event.key !== 'Tab') return;
 // Build the focusable list lazily on each Tab keystroke – the
 // modal body is dynamic (chooser re-renders, list deletes,
 // etc.), so caching would go stale. The cost is negligible
 // (modals are small) and the correctness gain is large.
 const focusables = root.querySelectorAll(
 'button:not([disabled]), [href], input:not([disabled]),'
 + ' select:not([disabled]), textarea:not([disabled]),'
 + ' [tabindex]:not([tabindex="-1"])',
 );
 if (focusables.length === 0) return;
 const first = focusables[0];
 const last = focusables[focusables.length - 1];
 const active = document.activeElement;
 if (event.shiftKey && active === first) {
 event.preventDefault();
 last.focus();
 } else if (!event.shiftKey && active === last) {
 event.preventDefault();
 first.focus();
 } else if (active && !root.contains(active)) {
 // Focus drifted outside the modal somehow (e.g. JS shifted
 // it to ``document.body``); pull it back to the first
 // focusable rather than letting Tab leak to background
 // controls.
 event.preventDefault();
 first.focus();
 }
 });
 document.addEventListener('click', (event) => {
 if (!_modalDismissable) return;  // locked: ignore × and backdrop
 if (event.target.matches('[data-modal-close]')) {
 closeModal();
 return;
 }
 // Backdrop click (target is the root itself, not a child of
 // ``.modal-card``).
 const root = document.getElementById('modal-root');
 if (root && !root.hidden && event.target === root) {
 closeModal();
 }
 });
 // Promise wrappers for the common cases. ``modalPrompt`` resolves
 // with the entered string (trimmed) or ``null`` on cancel;
 // ``modalConfirm`` resolves with ``true`` on confirm, ``false``
 // otherwise. The async API keeps caller code linear (``const name
 // = await modalPrompt(...); if (!name) return;``).
 function modalPrompt(opts) {
 return new Promise((resolve) => {
 let resolved = false;
 const safeResolve = (value) => {
 if (resolved) return;
 resolved = true;
 resolve(value);
 };
 const placeholder = opts.placeholder || '';
 const initial = opts.initial || '';
 const inputId = 'modal-prompt-input-' + Date.now();
 const helpHTML = opts.help
 ? '<p class="modal-help">' + escapeHTML(opts.help) + '</p>'
 : '';
 openModal({
 title: opts.title || 'Enter a value',
 bodyHTML:
 helpHTML
 + '<label for="' + inputId + '">' + escapeHTML(opts.label || 'Value') + '</label>'
 + '<input id="' + inputId + '" type="text" placeholder="' + escapeHTML(placeholder)
 + '" value="' + escapeHTML(initial) + '">',
 footerButtons: [
 { label: opts.cancelLabel || 'Cancel', onClick: () => closeModal() },
 { label: opts.confirmLabel || 'Save', kind: 'primary', onClick: () => {
 const input = document.getElementById(inputId);
 const value = (input && input.value || '').trim();
 if (!value) {
 input.focus();
 return;
 }
 safeResolve(value);
 closeModal();
 } },
 ],
 onClose: () => safeResolve(null),
 });
 // Submit on Enter inside the prompt input.
 const input = document.getElementById(inputId);
 if (input) {
 input.addEventListener('keydown', (event) => {
 if (event.key === 'Enter') {
 event.preventDefault();
 const value = input.value.trim();
 if (value) {
 safeResolve(value);
 closeModal();
 }
 }
 });
 }
 });
 }
 function modalConfirm(opts) {
 return new Promise((resolve) => {
 let resolved = false;
 const safeResolve = (value) => {
 if (resolved) return;
 resolved = true;
 resolve(value);
 };
 openModal({
 title: opts.title || 'Confirm',
 bodyHTML: '<p>' + escapeHTML(opts.message || 'Are you sure?') + '</p>',
 footerButtons: [
 { label: opts.cancelLabel || 'Cancel', onClick: () => closeModal() },
 {
 label: opts.confirmLabel || 'OK',
 kind: opts.danger ? 'danger' : 'primary',
 onClick: () => { safeResolve(true); closeModal(); },
 },
 ],
 onClose: () => safeResolve(false),
 });
 });
 }
 // ``escapeHTML`` keeps operator-typed names from being interpreted
 // as markup when interpolated into the modal body. Used by the
 // template chooser + every modalPrompt label / placeholder.
 function escapeHTML(value) {
 const div = document.createElement('div');
 div.textContent = value == null ? '' : String(value);
 return div.innerHTML;
 }
 // ``modalChooseTemplate`` opens a list-style modal showing every
 // ``.openfollowtemplate`` of a given type. Clicking a row applies
 // it (after a confirm dialog when ``opts.applyConfirmMessage`` is
 // set – used for camera_grid + zones, which wholesale-replace the
 // section). The caller's ``onApplied(filename)`` fires after a
 // successful apply so the section can refresh itself. Delete
 // buttons (user templates only) call DELETE then refresh the
 // list inline.
 async function modalChooseTemplate(opts) {
 const type = opts.type;
 const applyConfirm = !!opts.applyConfirmMessage;
 // Build the list-rendering helper as a closure so the delete /
 // apply buttons can re-fetch + re-render without leaving the
 // modal open in a stale state.
 async function loadAndRender() {
 const body = document.getElementById('modal-body');
 if (!body) return;
 body.innerHTML = '<p class="modal-empty">Loading…</p>';
 let payload;
 try {
 const res = await fetch('/api/templates?type=' + encodeURIComponent(type), { cache: 'no-store' });
 if (!res.ok) throw new Error('HTTP ' + res.status);
 payload = await res.json();
 } catch (err) {
 body.innerHTML = '<div class="modal-error">Could not load templates: '
 + escapeHTML(err.message || 'unknown error') + '</div>';
 return;
 }
 const items = (payload && Array.isArray(payload.templates)) ? payload.templates : [];
 if (items.length === 0) {
 body.innerHTML = '<p class="modal-empty">No templates yet.</p>';
 return;
 }
 const list = document.createElement('ul');
 list.className = 'modal-list';
 items.forEach((tpl) => {
 const li = document.createElement('li');
 li.className = 'modal-list-item';
 // Unreadable files (decode errors) rendered as disabled rows with error reason.
 // Apply is greyed out; Delete still allowed for cleanup.
 if (tpl.error) {
 const label = '(unreadable: ' + tpl.error + ') '
 + escapeHTML(tpl.filename);
 li.classList.add('modal-list-item-disabled');
 li.innerHTML = ''
 + '<span class="modal-list-item-name">' + escapeHTML(label) + '</span>'
 + (tpl.is_system
 ? '<span class="modal-list-item-badge system">system</span>'
 : '<span class="modal-list-item-badge">user</span>');
 const apply = document.createElement('button');
 apply.type = 'button';
 apply.className = 'modal-list-item-apply';
 apply.textContent = 'Apply';
 apply.disabled = true;
 apply.title = 'Cannot apply an unreadable template';
 li.appendChild(apply);
 if (!tpl.is_system) {
 const del = document.createElement('button');
 del.type = 'button';
 del.className = 'modal-list-item-delete';
 del.textContent = 'Delete';
 // Delete by filename – the loader couldn't decode the
 // envelope, but the route's DELETE only needs the
 // filename which we still have.
 del.addEventListener('click', () => onDeleteClick(tpl));
 li.appendChild(del);
 }
 list.appendChild(li);
 return;
 }
 li.innerHTML = ''
 + '<span class="modal-list-item-name">' + escapeHTML(tpl.name) + '</span>'
 + (tpl.is_system
 ? '<span class="modal-list-item-badge system">system</span>'
 : '<span class="modal-list-item-badge">user</span>');
 const apply = document.createElement('button');
 apply.type = 'button';
 apply.className = 'modal-list-item-apply';
 apply.textContent = 'Apply';
 apply.addEventListener('click', () => onApplyClick(tpl));
 li.appendChild(apply);
 if (!tpl.is_system) {
 const del = document.createElement('button');
 del.type = 'button';
 del.className = 'modal-list-item-delete';
 del.textContent = 'Delete';
 del.addEventListener('click', () => onDeleteClick(tpl));
 li.appendChild(del);
 }
 list.appendChild(li);
 });
 body.replaceChildren(list);
 }
 async function onApplyClick(tpl) {
 if (applyConfirm) {
 const ok = await modalConfirm({
 title: 'Replace section?',
 message: opts.applyConfirmMessage,
 confirmLabel: 'Replace',
 danger: true,
 });
 if (!ok) {
 // The confirm closed the chooser; reopen it (or simply
 // bail – the operator already saw the list and chose
 // "no", so leaving it closed is fine).
 return;
 }
 }
 const url = '/api/templates/'
 + encodeURIComponent(tpl.filename)
 + '/apply' + (applyConfirm ? '?confirm=1' : '');
 try {
 const res = await fetch(url, { method: 'POST' });
 if (!res.ok) {
 const text = await res.text();
 let msg = 'apply failed';
 try { msg = JSON.parse(text).error || msg; } catch (_) {}
 showToast(msg);
 return;
 }
 closeModal();
 showToast('Applied ' + tpl.name);
 if (typeof opts.onApplied === 'function') opts.onApplied(tpl);
 } catch (err) {
 showToast('Apply failed: ' + (err.message || 'unknown error'));
 }
 }
 async function onDeleteClick(tpl) {
 const ok = await modalConfirm({
 title: 'Delete template?',
 message: 'Delete "' + tpl.name + '"? This cannot be undone.',
 confirmLabel: 'Delete',
 danger: true,
 });
 if (!ok) return;
 try {
 const res = await fetch('/api/templates/' + encodeURIComponent(tpl.filename), { method: 'DELETE' });
 if (!res.ok) {
 const text = await res.text();
 let msg = 'delete failed';
 try { msg = JSON.parse(text).error || msg; } catch (_) {}
 showToast(msg);
 return;
 }
 showToast('Deleted ' + tpl.name);
 // Re-render the list so the deleted entry vanishes
 // without re-opening the chooser.
 openTemplateChooser();
 } catch (err) {
 showToast('Delete failed: ' + (err.message || 'unknown error'));
 }
 }
 function openTemplateChooser() {
 openModal({
 title: opts.title || 'Load template',
 bodyHTML: '<p class="modal-empty">Loading…</p>',
 footerButtons: [
 { label: 'Close', onClick: () => closeModal() },
 ],
 });
 loadAndRender();
 }
 openTemplateChooser();
 }
 // Camera + Grid share one ``camera_grid`` template type because
 // they're typically tuned together (e.g. an indoor venue rig).
 // Save captures both sections; load reloads the page so both
 // sections re-render with the new state. Reload (rather than a
 // pair of HTMX section refreshes) is intentional – the operator's
 // current section folds may be stale after an apply, and a full
 // reload keeps focus / scroll consistent.
 window.cameraGridSaveTemplate = function() {
 window.saveCurrentSectionAsTemplate({
 type: 'camera_grid',
 title: 'Save camera + grid as template',
 placeholder: 'e.g. Indoor venue rig',
 });
 };
 window.cameraGridLoadTemplate = function() {
 window.modalChooseTemplate({
 type: 'camera_grid',
 title: 'Load camera + grid template',
 applyConfirmMessage:
 'Loading a camera + grid template replaces the current camera'
 + ' and grid settings. Continue?',
 onApplied: function() { window.location.reload(); },
 });
 };
 // ``saveCurrentSectionAsTemplate`` is the per-section "Save as
 // template…" entry point. ``opts.type`` is the template type
 // (``camera_grid`` / ``zones``) – the server reads the current
 // section snapshot from disk so no body payload is needed beyond
 // the operator-supplied name. ``onSaved`` fires on success so the
 // caller can refresh / toast.
 async function saveCurrentSectionAsTemplate(opts) {
 const name = await modalPrompt({
 title: opts.title || 'Save as template',
 label: 'Template name',
 placeholder: opts.placeholder || 'e.g. Indoor rig',
 confirmLabel: 'Save',
 });
 if (!name) return;
 try {
 const res = await fetch('/api/templates/' + encodeURIComponent(opts.type) + '/save', {
 method: 'POST',
 headers: { 'Content-Type': 'application/json' },
 body: JSON.stringify({ name }),
 });
 if (!res.ok) {
 const text = await res.text();
 let msg = 'save failed';
 try { msg = JSON.parse(text).error || msg; } catch (_) {}
 showToast(msg);
 return;
 }
 const payload = await res.json();
 showToast('Saved ' + payload.name);
 if (typeof opts.onSaved === 'function') opts.onSaved(payload);
 } catch (err) {
 showToast('Save failed: ' + (err.message || 'unknown error'));
 }
 }
 // Parse OSC diagnostic JSON response with fallback for non-JSON/HTTP errors.
 // Prevents diagnostics wedging when server returns error pages or invalid JSON.
 function renderDiagJson(event, targetId) {
 const target = document.getElementById(targetId);
 if (!target) return;
 const xhr = event && event.detail && event.detail.xhr;
 const status = xhr ? xhr.status : 0;
 const body = xhr ? xhr.responseText : '';
 try {
 target.textContent = JSON.stringify(JSON.parse(body), null, 2);
 } catch (err) {
 target.textContent =
 '[non-JSON response, status ' + status + ']\n\n' + (body || '(empty body)');
 }
 }
 // ---- OSC bindings Diagnostics tab (operator-feedback follow-up) ----
 //
 // Three panels: Live status / Preview / Test send. Each one gets
 // a structured renderer below; the action buttons in the partial
 // dispatch via vanilla ``fetch`` (rather than HTMX swap) so the
 // structured renderer is the single writer to each body.
 //
 // Tab-activation hook auto-loads the Live status panel the first
 // time the operator opens the Diagnostics tab on a row, so the
 // pane isn't an empty "Loading…" stub waiting for a click. Re-
 // opening the tab triggers another fetch – operators expect
 // "switching to the diagnostics tab shows current state".
 //
 // Helpers re-use ``escapeHTML`` from the modal section.
 function _oscDiagArgsHTML(args) {
 if (!Array.isArray(args) || args.length === 0) {
 return '<span class="diag-empty">(no args)</span>';
 }
 return '<div class="diag-args">'
 + args.map((a) => '<span class="diag-arg">'
 + escapeHTML(typeof a === 'string' ? a : JSON.stringify(a))
 + '</span>').join('')
 + '</div>';
 }
 function _oscDiagErrorHTML(reason) {
 return '<div class="diag-error">' + escapeHTML(reason) + '</div>';
 }
 function _oscDiagRawHTML(parsed) {
 // Collapsed ``<details>`` with the raw response below the
 // structured render. Operators see the formatted view by
 // default; support can ask "expand Raw JSON and copy" without
 // a second round-trip. Falls back to the raw text when the
 // body wasn't JSON (server mid-restart, HTML 500 page) so the
 // exact bytes are still pasteable.
 let payload = '';
 if (parsed && parsed.data !== undefined && parsed.data !== null) {
 try {
 payload = JSON.stringify(parsed.data, null, 2);
 } catch (_err) {
 payload = String(parsed.data);
 }
 } else if (parsed && typeof parsed.raw === 'string' && parsed.raw) {
 payload = parsed.raw;
 } else {
 payload = '(empty response)';
 }
 const status = (parsed && typeof parsed.status === 'number') ? parsed.status : 0;
 return '<details class="diag-raw">'
 + '<summary>Raw JSON (HTTP ' + status + ')</summary>'
 + '<pre class="diag-pre">' + escapeHTML(payload) + '</pre>'
 + '</details>';
 }
 function _oscDiagPlainResponse(xhr) {
 // Common parse with try/catch fallback – same robustness
 // contract as the original ``renderDiagJson`` (a misbehaving /
 // mid-restart server returning HTML or a non-2xx body must not
 // wedge the diagnostics surface).
 const status = xhr ? xhr.status : 0;
 const body = xhr ? xhr.responseText : '';
 try {
 return { ok: status >= 200 && status < 300, data: JSON.parse(body), status };
 } catch (_err) {
 return { ok: false, data: null, status, raw: body };
 }
 }
 function renderOscDiagPreview(rowId, parsed) {
 const body = document.querySelector(
 '[data-osc-diag-preview-body="' + rowId + '"]',
 );
 if (!body) return;
 const raw = _oscDiagRawHTML(parsed);
 if (!parsed.ok || !parsed.data) {
 body.innerHTML = _oscDiagErrorHTML(
 'Preview failed (HTTP ' + parsed.status + ')'
 + (parsed.raw ? ': ' + parsed.raw.slice(0, 200) : ''),
 ) + raw;
 return;
 }
 const d = parsed.data;
 if (d.available === false) {
 const msg = d.pending
 ? 'Saved – not yet serviced by the live manager. It applies within ~1s; restart OpenFollow if this persists.'
 : 'Runtime not attached – start OpenFollow to see a live preview.';
 body.innerHTML = '<p class="diag-empty">' + msg + '</p>' + raw;
 return;
 }
 if (d.error) {
 body.innerHTML = _oscDiagErrorHTML(d.error) + raw;
 return;
 }
 const address = typeof d.address === 'string' ? d.address : '';
 body.innerHTML = ''
 + '<dl class="diag-row"><dt>Address</dt><dd>' + escapeHTML(address || '(empty)') + '</dd></dl>'
 + '<dl class="diag-row"><dt>Args</dt><dd>' + _oscDiagArgsHTML(d.args) + '</dd></dl>'
 + raw;
 }
 function renderOscDiagTest(rowId, parsed) {
 const body = document.querySelector(
 '[data-osc-diag-test-body="' + rowId + '"]',
 );
 if (!body) return;
 const raw = _oscDiagRawHTML(parsed);
 if (!parsed.ok || !parsed.data) {
 body.innerHTML = _oscDiagErrorHTML(
 'Test send failed (HTTP ' + parsed.status + ')'
 + (parsed.raw ? ': ' + parsed.raw.slice(0, 200) : ''),
 ) + raw;
 return;
 }
 const d = parsed.data;
 if (d.available === false) {
 const msg = d.pending
 ? 'Saved – not yet serviced by the live manager. It applies within ~1s; restart OpenFollow if this persists.'
 : 'Runtime not attached – start OpenFollow to send a test packet.';
 body.innerHTML = '<p class="diag-empty">' + msg + '</p>' + raw;
 return;
 }
 const sentPill = d.sent === true
 ? '<span class="diag-status-pill healthy">Sent</span>'
 : '<span class="diag-status-pill unhealthy">Skipped</span>';
 const rows = [
 '<dl class="diag-row"><dt>Status</dt><dd>' + sentPill + '</dd></dl>',
 ];
 if (d.address) {
 rows.push(
 '<dl class="diag-row"><dt>Address</dt><dd>' + escapeHTML(d.address) + '</dd></dl>',
 );
 }
 if (d.args !== undefined) {
 rows.push(
 '<dl class="diag-row"><dt>Args</dt><dd>' + _oscDiagArgsHTML(d.args) + '</dd></dl>',
 );
 }
 if (d.error) {
 rows.push(
 '<dl class="diag-row"><dt>Error</dt><dd><span class="diag-empty">'
 + escapeHTML(d.error) + '</span></dd></dl>',
 );
 }
 body.innerHTML = rows.join('') + raw;
 }
 function _oscDiagFormatAge(seconds) {
 if (typeof seconds !== 'number' || seconds < 0) return 'just now';
 if (seconds < 1) return '<1s ago';
 const r = Math.round(seconds);
 if (r < 60) return r + 's ago';
 const m = Math.floor(r / 60);
 const s = r - m * 60;
 return m + 'm ' + s + 's ago';
 }
 function renderOscDiagStatus(rowId, parsed) {
 const body = document.querySelector(
 '[data-osc-diag-status-body="' + rowId + '"]',
 );
 if (!body) return;
 const raw = _oscDiagRawHTML(parsed);
 if (!parsed.ok || !parsed.data) {
 body.innerHTML = _oscDiagErrorHTML(
 'Status fetch failed (HTTP ' + parsed.status + ')'
 + (parsed.raw ? ': ' + parsed.raw.slice(0, 200) : ''),
 ) + raw;
 return;
 }
 const d = parsed.data;
 if (d.available === false) {
 const pillText = d.pending ? 'Awaiting live apply' : 'Runtime detached';
 const note = d.pending
 ? 'Saved – not yet serviced by the live manager. It applies within ~1s; restart OpenFollow if this persists.'
 : 'Live counters appear when OpenFollow is running.';
 body.innerHTML = ''
 + '<dl class="diag-row"><dt>State</dt><dd>'
 + '<span class="diag-status-pill unknown">' + pillText + '</span>'
 + '</dd></dl>'
 + '<p class="diag-empty" style="margin-top:0.5rem;">'
 + note + '</p>'
 + raw;
 return;
 }
 const healthClass = d.healthy === true ? 'healthy' : 'unhealthy';
 const healthText = d.healthy === true ? 'Healthy' : 'Unhealthy';
 const ppsText = (typeof d.pps === 'number') ? d.pps.toFixed(1) + ' /s' : '–';
 const lastError = d.last_error || '';
 const rows = [
 '<dl class="diag-row"><dt>State</dt><dd>'
 + '<span class="diag-status-pill ' + healthClass + '">'
 + escapeHTML(healthText) + '</span></dd></dl>',
 '<dl class="diag-row"><dt>Send rate</dt><dd>' + escapeHTML(ppsText) + '</dd></dl>',
 ];
 if (lastError) {
 rows.push(
 '<dl class="diag-row"><dt>Last error</dt><dd>'
 + '<span class="diag-empty">' + escapeHTML(lastError) + '</span></dd></dl>',
 );
 }
 const buf = Array.isArray(d.ring_buffer) ? d.ring_buffer.slice(-5).reverse() : [];
 if (buf.length > 0) {
 const events = buf.map((e) => {
 const status = e.status || '';
 const cls = status === 'sent' ? 'sent' : 'skipped';
 const detail = status === 'sent'
 ? (e.address || '') + (Array.isArray(e.args) && e.args.length
 ? ' [' + e.args.map(String).join(', ') + ']' : '')
 : (e.error || '');
 const age = _oscDiagFormatAge(e.age_seconds);
 return '<li class="diag-event">'
 + '<span class="diag-event-detail">' + escapeHTML(age) + '</span>'
 + '<span class="diag-event-status ' + cls + '">' + escapeHTML(status) + '</span>'
 + '<span class="diag-event-detail">' + escapeHTML(detail) + '</span>'
 + '</li>';
 }).join('');
 rows.push(
 '<dl class="diag-row"><dt>Recent events</dt><dd>'
 + '<ul class="diag-events">' + events + '</ul>'
 + '</dd></dl>',
 );
 }
 body.innerHTML = rows.join('') + raw;
 }
 async function _oscDiagFetch(url, opts) {
 try {
 const res = await fetch(url, opts || {});
 const status = res.status;
 const text = await res.text();
 try {
 return { ok: res.ok, data: JSON.parse(text), status };
 } catch (_err) {
 return { ok: false, data: null, status, raw: text };
 }
 } catch (err) {
 return { ok: false, data: null, status: 0, raw: err.message || String(err) };
 }
 }
 async function loadOscDiagStatus(rowId) {
 const parsed = await _oscDiagFetch('/api/osc_binding/' + encodeURIComponent(rowId) + '/status');
 renderOscDiagStatus(rowId, parsed);
 }
 async function loadOscDiagPreview(rowId) {
 const parsed = await _oscDiagFetch('/api/osc_binding/' + encodeURIComponent(rowId) + '/preview');
 renderOscDiagPreview(rowId, parsed);
 }
 async function runOscDiagTest(rowId) {
 const parsed = await _oscDiagFetch(
 '/api/osc_binding/' + encodeURIComponent(rowId) + '/test',
 { method: 'POST' },
 );
 renderOscDiagTest(rowId, parsed);
 }
 document.addEventListener('click', (event) => {
 const refresh = event.target.closest('[data-osc-diag-refresh]');
 if (refresh) {
 loadOscDiagStatus(refresh.dataset.rowId);
 return;
 }
 const action = event.target.closest('[data-osc-diag-action]');
 if (!action) return;
 const rowId = action.dataset.rowId;
 if (!rowId) return;
 if (action.dataset.oscDiagAction === 'preview') {
 loadOscDiagPreview(rowId);
 } else if (action.dataset.oscDiagAction === 'test') {
 runOscDiagTest(rowId);
 }
 });
 // ---- "Save as template" dirty-state gate ---------------------------
 //
 // Operators expect "Save as template" to capture what they see on
 // screen – but the server-side save reads the section's snapshot
 // from ``config.toml``, so a form with unsaved changes would
 // silently capture the on-disk state and lose whatever the
 // operator typed since the last Save. Disable every "Save as
 // template" button while its watched scope has unsaved changes
 // so the only way to template the current view is to commit the
 // form first.
 //
 // Mark-up contract:
 // - A form / container that holds saveable state declares
 // ``data-template-form="1"`` on its outer element. The
 // listeners below add ``data-dirty="1"`` to that element on
 // any ``input`` / ``change`` from a descendant.
 // - A "Save as template" button declares ``data-template-save``
 // plus ``data-template-deps="<CSS selector>"``. The button is
 // disabled when ANY element matching that selector carries
 // ``data-dirty="1"``.
 // - HTMX swaps render fresh forms (no ``data-dirty`` attribute),
 // so re-evaluating after every swap naturally clears the
 // gated buttons.
 // - Helpers ``markFormDirty(node)`` / ``markFormClean(node)`` are
 // exposed on ``window`` so non-form callers (the zone editor's
 // per-zone Save flow, the canvas drawing-in-progress state)
 // can drive the same gate without triggering an ``input``
 // event.
 function _updateTemplateSaveButtons() {
 // Save-as-template: disabled WHEN dirty (templates can't
 // capture unsaved values).
 document.querySelectorAll('[data-template-save]').forEach((btn) => {
 const sel = btn.dataset.templateDeps;
 if (!sel) return;
 let dirty = false;
 try {
 const matches = document.querySelectorAll(sel);
 matches.forEach((el) => {
 if (el.dataset && el.dataset.dirty === '1') dirty = true;
 });
 } catch (_err) {
 // Bad selector – fail open (button stays enabled) rather
 // than wedge the entire UI on a typo'd ``data-template-deps``.
 return;
 }
 btn.disabled = dirty;
 btn.title = dirty
 ? 'Save the section first – templates capture what\'s on disk, not the unsaved form values.'
 : '';
 });
 // Discard: inverted polarity – disabled when clean (nothing to revert).
 // Uses same ``data-template-deps`` selector mechanism.
 document.querySelectorAll('[data-discard-btn]').forEach((btn) => {
 const sel = btn.dataset.templateDeps;
 if (!sel) return;
 let dirty = false;
 try {
 const matches = document.querySelectorAll(sel);
 matches.forEach((el) => {
 if (el.dataset && el.dataset.dirty === '1') dirty = true;
 });
 } catch (_err) {
 return;
 }
 btn.disabled = !dirty;
 btn.title = dirty ? '' : 'No unsaved changes.';
 });
 }
 function markFormDirty(node) {
 if (!node || !node.dataset) return;
 if (node.dataset.dirty === '1') return;
 node.dataset.dirty = '1';
 _updateTemplateSaveButtons();
 }
 function markFormClean(node) {
 if (!node || !node.dataset) return;
 if (node.dataset.dirty !== '1') return;
 delete node.dataset.dirty;
 _updateTemplateSaveButtons();
 }
 window.markFormDirty = markFormDirty;
 window.markFormClean = markFormClean;
 window.updateTemplateSaveButtons = _updateTemplateSaveButtons;
 function _onTemplateFormFieldChange(event) {
 const form = event.target.closest('[data-template-form]');
 if (!form) return;
 markFormDirty(form);
 }
 document.addEventListener('input', _onTemplateFormFieldChange);
 document.addEventListener('change', _onTemplateFormFieldChange);
 // After any HTMX swap, freshly-rendered forms come in clean
 // (no ``data-dirty`` attribute). Re-evaluate so newly-rendered
 // Save-as-template buttons reflect current dirty state – and
 // the swapped-out form's old dirty flag is gone with the DOM.
 document.body.addEventListener('htmx:afterSwap', () => {
 _updateTemplateSaveButtons();
 });
 // Initial evaluation on page load – covers any inline state set
 // before the listeners attached.
 if (document.readyState === 'loading') {
 document.addEventListener('DOMContentLoaded', _updateTemplateSaveButtons);
 } else {
 _updateTemplateSaveButtons();
 }
 function broadcastSection(section, form) {
 const formData = new FormData(form);
 const checkboxNames = new Set(
 Array.from(form.querySelectorAll('input[type="checkbox"]')).map(cb => cb.name)
 );
 const data = {};
 for (const [key, value] of formData.entries()) {
 if (checkboxNames.has(key)) data[key] = true;
 else if (!Number.isNaN(Number(value)) && value !== '') data[key] = Number(value);
 else data[key] = value;
 }
 checkboxNames.forEach((key) => {
 if (!(key in data)) data[key] = false;
 });
 fetch(`/api/config/${section}/broadcast`, {
 method: 'POST',
 headers: { 'Content-Type': 'application/json' },
 body: JSON.stringify(data),
 }).then((res) => res.json()).then((result) => {
 const failed = result.peer_results.filter((peer) => !peer.success);
 showToast(failed.length === 0
 ? `Saved and applied to ${result.peer_results.length} server(s)`
 : `Saved and applied to ${result.peer_results.length - failed.length}/${result.peer_results.length} servers`);
 }).catch(() => showToast('Broadcast failed'));
 }
 // Disable Save/Broadcast buttons when form has aria-invalid inputs.
 // Scoped to actual form-submit / broadcast controls only.
 function refreshFormGate(form) {
 if (!form) return;
 const hasError = form.querySelector('[aria-invalid="true"]') !== null;
 form.querySelectorAll(
 'button[type="submit"].save-btn,'
 + ' button.broadcast-btn[onclick*="broadcastSection"]'
 ).forEach((b) => {
 b.disabled = hasError;
 });
 // Also gate a submit button placed outside the form via ``form=`` (layout).
 if (form.id) {
 document.querySelectorAll(
 'button[type="submit"][form="' + form.id + '"].save-btn'
 ).forEach((b) => { b.disabled = hasError; });
 }
 }
 document.body.addEventListener('htmx:afterSwap', (e) => {
 if (e.detail.target && e.detail.target.classList && e.detail.target.classList.contains('saved')) {
 showToast('Saved');
 setTimeout(() => e.detail.target.classList.remove('saved'), 500);
 }
 // Validation swap: the target is a sibling ``<span class="field-error">``
 // whose inner content is either empty (valid), an error span (invalid),
 // or a note span (advisory). Flip ``aria-invalid`` on the input and
 // recompute the per-form gate. Notes do not gate Save.
 const target = e.detail.target;
 if (target && target.classList && target.classList.contains('field-error')) {
 const input = document.querySelector('[aria-describedby="' + target.id + '"]');
 if (input) {
 const hasError = target.querySelector('.field-error-msg') !== null;
 input.setAttribute('aria-invalid', hasError ? 'true' : 'false');
 refreshFormGate(input.closest('form'));
 }
 }
 // Section re-renders (after a successful save) reset every input to
 // ``aria-invalid="false"`` already; just re-run the gate to clear
 // any stale ``disabled`` state on the freshly-swapped buttons.
 if (target && target.tagName === 'FORM') {
 refreshFormGate(target);
 }
 // Some sections (e.g. Person Detection) swap a container holding several
 // ``form.section`` panels; re-gate each so freshly-rendered Save buttons
 // start un-disabled.
 if (target && target.querySelectorAll) {
 target.querySelectorAll('form.section').forEach(refreshFormGate);
 }
 initializeSectionFolding(document);
 initializeInlineAdvanced(document);
 });
 // Periodic pollers (Live Statistics 1s, Diagnostics / Server overview 5s)
 // replace whole DOM subtrees. That destroys Firefox's scroll-anchor node,
 // so the viewport snaps toward the top on every poll tick. Re-pin the
 // scroll position for swaps NOT triggered by a user gesture (polls +
 // ``load``); user-initiated swaps (Save / click) still scroll naturally.
 // ``beforeSwap`` -> DOM swap -> ``afterSwap`` run synchronously within one
 // request, so a single saved slot can't be clobbered by an overlapping poll.
 (function () {
 let saved = null;
 function isAutomatic(evt) {
 const cfg = evt.detail && evt.detail.requestConfig;
 return !cfg || !cfg.triggeringEvent;  // poll / load carry no user event
 }
 document.body.addEventListener('htmx:beforeSwap', (evt) => {
 // Only capture when a swap will actually happen. An error / no-swap
 // response fires beforeSwap but never afterSwap, which would otherwise
 // leave a stale position for a later poll's afterSwap to restore to.
 saved = isAutomatic(evt) && evt.detail.shouldSwap
 ? { x: window.scrollX, y: window.scrollY }
 : null;
 });
 document.body.addEventListener('htmx:afterSwap', () => {
 if (saved && (window.scrollX !== saved.x || window.scrollY !== saved.y)) {
 window.scrollTo(saved.x, saved.y);
 }
 saved = null;
 });
 })();
 document.addEventListener('DOMContentLoaded', () => {
 document.querySelectorAll('form.section').forEach(refreshFormGate);
 });
 function resetButtonMappingDefaults(container) {
 const defaults = {
 btn_reset: 'X', btn_toggle_help: 'Y',
 btn_settings: 'BACK',
 btn_speed_down: 'LB', btn_speed_up: 'RB',
 btn_move_z_down: 'LT', btn_move_z_up: 'RT',
 btn_toggle_zones: 'B',
 btn_next_marker: 'DPAD_RIGHT', btn_prev_marker: 'DPAD_LEFT',
 move_xy_stick: 'left',
 btn_menu_confirm: 'A', btn_menu_cancel: 'B',
 };
 container.querySelectorAll('select').forEach(sel => {
 const def = defaults[sel.name];
 if (def !== undefined) sel.value = def;
 });
 }
 function resetKeyboardMappingDefaults(container) {
 const defaults = {
 key_move_layout: 'wasd',
 key_move_z_up: 'q', key_move_z_down: 'e',
 key_reset: 'x', key_toggle_help: 'h',
 key_toggle_zones: 'z',
 key_speed_down: 'r', key_speed_up: 't',
 key_settings: 'm',
 key_next_marker: 'Tab', key_prev_marker: '',
 };
 container.querySelectorAll('select, input[type="text"]').forEach(el => {
 const def = defaults[el.name];
 if (def !== undefined) el.value = def;
 });
 }
 // Restart button (lives in the Diagnostics partial). Defined here
 // alongside the ``#top-restart-notice`` banner so the partial
 // itself stays JS-free (per its module header), and so the
 // notice element is null-guarded for the case where the
 // diagnostics partial is fetched standalone via
 // ``hx-get="/section/diagnostics"`` and the index-level banner
 // isn't in the DOM. Triggers ``/api/restart`` (which returns
 // immediately) then polls ``/api/info`` every 2 s until the
 // server comes back, at which point it reloads the page.
 function confirmRestartApp() {
 if (!confirm('Restart the application?')) return;
 fetch('/api/restart', {method: 'POST'}).then(function() {
 var n = document.getElementById('top-restart-notice');
 if (n) n.style.display = 'block';
 var p = setInterval(function() {
 fetch('/api/info').then(function(r) {
 if (r.ok) { clearInterval(p); window.location.reload(); }
 }).catch(function() {});
 }, 2000);
 });
 }
 // Toggle the <body> gate class. When turning off, uncheck the detection
 // Enabled box to mirror the server-side cascade; the selector must match
 // the route's cascade fields.
 function onExperimentalToggle(cb) {
 document.body.classList.toggle('show-experimental', cb.checked);
 if (cb.checked) return;
 document.querySelectorAll(
 '#detection-section input[name="enabled"]'
 ).forEach(function (el) { el.checked = false; });
 }
 function switchTab(tabId) {
 // help docs are section-specific; dismiss the drawer when
 // the operator moves to another tab so stale help can't linger.
 closeHelp();
 document.querySelectorAll('.tab-btn').forEach(btn => {
 btn.classList.toggle('active', btn.dataset.tab === tabId);
 });
 document.querySelectorAll('.tab-content').forEach(el => {
 el.classList.toggle('active', el.id === 'tab-' + tabId);
 });
 writeStorage('psnfs:active-tab:' + location.pathname, tabId);
 }
 function initTabs() {
 var stored = readStorage('psnfs:active-tab:' + location.pathname);
 var first = document.querySelector('.tab-btn');
 var defaultTab = first ? first.dataset.tab : 'overview';
 var tabId = stored || defaultTab;
 // Fall back to the default tab if the stored tab is missing or is a
 // hidden experimental tab.
 var el = document.getElementById('tab-' + tabId);
 if (!el
 || (el.classList.contains('experimental-feature')
 && !document.body.classList.contains('show-experimental'))) {
 tabId = defaultTab;
 }
 document.querySelectorAll('.tab-btn').forEach(btn => {
 btn.addEventListener('click', () => switchTab(btn.dataset.tab));
 });
 switchTab(tabId);
 }
 // Per-row tab switcher for OSC bindings editor. Event delegation ensures
 // HTMX-swapped rows inherit behavior without re-binding.
 function switchRowTab(btn) {
 const target = btn.dataset.rowTab;
 if (!target) return;
 const bar = btn.closest('.row-tab-bar');
 // Scope-fallback chain: .osc-binding-form → <details> → [data-row-tabs-scope].
 const form = btn.closest('.osc-binding-form')
 || btn.closest('details')
 || btn.closest('[data-row-tabs-scope]');
 if (!bar || !form) return;
 // Keep ``aria-selected`` in sync with ``.active`` so the
 // ``role="tablist"`` / ``role="tab"`` markup actually reports
 // the active tab to screen readers.
 bar.querySelectorAll('.row-tab-btn').forEach(b => {
 const isActive = b === btn;
 b.classList.toggle('active', isActive);
 b.setAttribute('aria-selected', isActive ? 'true' : 'false');
 });
 let activatedDiagPanel = null;
 form.querySelectorAll('.row-tab-panel').forEach(panel => {
 const becameActive = panel.id === 'row-tab-' + target;
 panel.classList.toggle('active', becameActive);
 if (becameActive && panel.dataset.oscDiagPanel) {
 activatedDiagPanel = panel;
 }
 });
 // Auto-fetch the Live status panel whenever the operator
 // switches to the Diagnostics tab. The panel ships a
 // "Loading…" stub server-side; without this hook the operator
 // would have to click the Refresh button to see anything.
 // Re-clicking the Diagnostics tab also re-fetches – operators
 // expect the tab to reflect "now", not "last time it was
 // opened".
 if (activatedDiagPanel
 && typeof window.loadOscDiagStatus === 'function') {
 window.loadOscDiagStatus(activatedDiagPanel.dataset.oscDiagPanel);
 }
 }
 document.addEventListener('click', (event) => {
 const btn = event.target.closest('.row-tab-btn');
 if (btn) switchRowTab(btn);
 });
 // combined OSC message editor.
 //
 // The visible field is a ``<div contenteditable>``; ``[name]``
 // placeholders are rendered as inline ``.osc-pill`` spans
 // (``contenteditable=false`` so they delete as a unit). A sibling
 // hidden input mirrors the plain-text serialization. The chip
 // bar below the editor inserts pills at the caret.
 //
 // Pills are re-rendered after blur and after every chip insertion;
 // *during* typing we leave the textContent alone so the operator's
 // caret position and IME composition aren't disturbed.
 // Extract each ``[ ... ]`` run up to its FIRST ``]`` – matching the
 // server's positional scan (compile_template: ``[`` then ``s.find(']')``).
 // ``[^\]]*`` (not the pill char-class) so a stray inner ``[`` doesn't
 // let the global scan re-match a valid-looking substring: ``[fo[x]``
 // is one literal candidate here, exactly as the server sends it, rather
 // than a green ``[x]`` pill the wire never produces.
 // Recognised-vs-literal is still decided by ``oscIsRecognised`` below.
 const OSC_PILL_RE = /\[[^\]]*\]/g;
 function oscEditorPlainText(editor) {
 // Walk the editor's children and concatenate. Pills carry their
 // placeholder text in ``textContent`` so a simple ``textContent``
 // read on the editor returns the plain-text serialization –
 // exactly what the server-side parser splits on whitespace.
 //
 // Non-breaking spaces -> ASCII space: ``contenteditable`` browsers
 // routinely insert U+00A0 (and other Unicode ``Zs`` separators)
 // when you press space next to an inline ``contenteditable=false``
 // element, i.e. right beside a placeholder pill. The server
 // tokeniser (``parser._UNICODE_WHITESPACE``) treats these as
 // separators too, so this is defence-in-depth: it keeps the hidden
 // field plain so the editor's own pill-splitting and any non-shlex
 // consumer see real spaces, not relying on the server to special-case.
 //
 // Adjacent-pill separation: insert a space between any two
 // ``[...]`` tokens that touch with no character between them
 // (``[ix:99][abc]`` → ``[ix:99] [abc]``). Without this the wire
 // tokeniser treats the run as a single arg, which is almost
 // never the operator's intent – in OSC, args are discrete
 // values, not concatenated strings. ``oscEditorRenderPills``
 // also inserts a space text node between adjacent rendered
 // pills for visual clarity; this regex is the wire-form
 // safety net for the input-event sync path that fires before
 // a re-render runs.
 return (editor.textContent || '')
 .replace(/[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]/g, ' ')
 .replace(/\](\[)/g, '] $1');
 }
 function oscEditorSyncHidden(editor) {
 const id = editor.dataset.oscMessageEditor;
 if (!id) return;
 const hidden = document.getElementById('osc-message-' + id + '-hidden');
 if (hidden) hidden.value = oscEditorPlainText(editor);
 }
 // Parse editor's data attributes to get "unresolved-placeholders" set for pill rendering.
 // Source: server-supplied attr at first render, then derived from marker IDs + editor text.
 // JSON failures degrade gracefully to prevent wedging the editor.
 function oscEditorParseJsonAttr(editor, attr, fallback) {
 const raw = editor.dataset[attr];
 if (!raw) return fallback;
 try {
 const v = JSON.parse(raw);
 return Array.isArray(v) ? v : fallback;
 } catch (_e) {
 return fallback;
 }
 }
 function oscEditorUnresolvedSet(editor) {
 return new Set(
 oscEditorParseJsonAttr(editor, 'oscUnresolvedPlaceholders', []),
 );
 }
 function oscEditorRenderPills(editor) {
 // Snapshot the current plain text, blow away the children, and
 // rebuild as a sequence of text nodes + pill spans. Caret
 // restoration is best-effort: we move the caret to the end of
 // the editor (the operator just blurred away or used a chip,
 // so this matches expectation).
 //
 // Pill validation: each ``[token]`` is checked against the
 // recognised-placeholder set. Tokens whose name isn't a
 // registered placeholder (``[xyz]`` / ``[bogus]``) get
 // ``data-invalid="true"`` so CSS paints them with the dashed
 // warning palette. The server-side ``compile_template`` treats
 // these as literal text – the marker is purely UI: "this looks
 // like a placeholder but the runtime will send it verbatim".
 //
 // Adjacent-pill auto-separation: when two ``[token]`` matches
 // are touching with no character between them (``[ix:99][abc]``),
 // insert a single space text node between the rendered pills so
 // ``shlex.split`` tokenises them as separate args. Without this
 // the merged ``[ix:99][abc]`` lands as a single arg in the
 // hidden input – confusing behaviour the operator can't easily
 // see (the pills look distinct but the wire form is glued).
 const text = oscEditorPlainText(editor);
 const unresolved = oscEditorUnresolvedSet(editor);
 const placeholderNames = (editor.dataset.oscPlaceholderNames
 ? new Set(oscEditorParseJsonAttr(editor, 'oscPlaceholderNames', []))
 : OSC_PLACEHOLDER_NAMES_FALLBACK);
 while (editor.firstChild) editor.removeChild(editor.firstChild);
 let lastIndex = 0;
 let match;
 OSC_PILL_RE.lastIndex = 0;
 while ((match = OSC_PILL_RE.exec(text)) !== null) {
 if (match.index > lastIndex) {
 editor.appendChild(document.createTextNode(text.slice(lastIndex, match.index)));
 } else if (match.index === lastIndex && lastIndex > 0) {
 // Two matches touching – separate them so the wire form
 // doesn't glue them into one arg.
 editor.appendChild(document.createTextNode(' '));
 }
 const pill = document.createElement('span');
 pill.className = 'osc-pill';
 pill.contentEditable = 'false';
 pill.textContent = match[0];
 // Validate the pill against the #402 grammar
 // ``[source(:index)(.transform)*]``. ``oscIsRecognised`` mirrors
 // the server-side ``_slot_from_name`` (positions accept
 // ``.inv`` / ``.frac``; ``fader`` / ``markerfader`` accept
 // ``.inv`` / ``.pct`` / ``.int:min-max`` / ``.scale:min-max``;
 // ``markerid`` an optional ``:N`` only; event sources bare).
 const isRecognised = oscIsRecognised(match[0]);
 if (!isRecognised) {
 pill.dataset.invalid = 'true';
 pill.title = 'Not a recognised placeholder – '
 + match[0] + ' will be sent as literal text. '
 + 'Sources: ' + Array.from(placeholderNames).sort().join(', ')
 + '. Add :N for a specific marker/fader, and chain '
 + '.transform (.inv .frac .pct .int:min-max .scale:min-max) '
 + '– e.g. [x.frac], [fader.pct], [markerfader:3.int:0-100].';
 }
 if (unresolved.has(match[0])) {
 pill.dataset.unresolved = 'true';
 // ): the tooltip used to suggest both
 // remediations every time, but only one applies per token.
 // ``[x]`` (default-marker slot) is fixed by setting the
 // row's Default marker; ``[x:N]`` (explicit-marker
 // slot) is fixed by registering marker N (or pointing the
 // reference at a registered id). Match an index ``:N`` that
 // sits immediately after the source name (before any
 // ``.transform``) – not a bare ``/:\d/``, which would also
 // fire on a transform range bound like ``.scale:0-1`` (mirrors
 // the server's ``ref_index is not None``).
 const isExplicit = /^\[[a-z]+:\d/.test(match[0]);
 const remediation = isExplicit
 ? 'register that marker or change :N to a'
 + ' registered id'
 : 'set the row\'s Default marker to a registered id';
 pill.title = 'Unresolved: ' + match[0]
 + ' – ' + remediation + '. Click to edit.';
 } else if (isRecognised) {
 pill.title = "Click to edit";
 }
 editor.appendChild(pill);
 lastIndex = match.index + match[0].length;
 }
 if (lastIndex < text.length) {
 editor.appendChild(document.createTextNode(text.slice(lastIndex)));
 }
 }
 // the unified placeholder grammar
 // [ source (:index)? (.transform)* ]
 // recognised here as five source-family regexes that mirror the
 // server's ``_slot_from_name`` (``openfollow/osc/template.py``).
 // Positions accept ``.inv`` / ``.frac``; ``fader`` / ``markerfader``
 // accept ``.inv`` / ``.pct`` / ``.int:min-max`` / ``.scale:min-max``;
 // ``markerid`` takes an optional index and no transform; the event
 // sources are bare. The index is a 1-based marker/fader number
 // (``[1-9]\d*`` – no ``0``, no leading zero, so ``:007`` can't diverge
 // from the canonical ``:7``) or, on the marker-keyed sources only, a
 // 1-based controller reference (``:cN``, no ``c0``).
 //
 // The chain is ORDER-CONSTRAINED, mirroring the server's parse-loop
 // guards (no duplicate transform; at most one range transform; no
 // domain-assuming transform after one that left the native domain).
 // Positions: at most one ``.inv`` and one ``.frac``, either order
 // (``-v`` is domain-agnostic). Faders: ``.inv`` (still 0..1), then
 // ``.pct`` (→ 0..100), then ONE ``.int``/``.scale`` (clamps) – each
 // optional, in that fixed order. So ``[fader.inv.pct]`` (→ 50) is
 // recognised but ``[fader.pct.inv]`` (→ 1.0-50) is literal.
 //
 // The parity gate ``tests/test_web_osc_placeholder_recognition.py``
 // extracts these literals and replays them against the server, so the
 // two can't drift – keep them in lockstep when the grammar changes.
 const OSC_POSITION_RE = /^\[(?:x|y|z)(?::(?:c[1-9]\d*|[1-9]\d*))?(?:\.inv(?:\.frac)?|\.frac(?:\.inv)?)?\]$/;
 const OSC_MARKERID_RE = /^\[markerid(?::(?:c[1-9]\d*|[1-9]\d*))?\]$/;
 const OSC_FADER_RE = /^\[fader(?::[1-9]\d*)?(?:\.inv)?(?:\.pct)?(?:\.(?:int|scale):-?\d+(?:\.\d+)?--?\d+(?:\.\d+)?)?\]$/;
 const OSC_MARKERFADER_RE = /^\[markerfader(?::(?:c[1-9]\d*|[1-9]\d*))?(?:\.inv)?(?:\.pct)?(?:\.(?:int|scale):-?\d+(?:\.\d+)?--?\d+(?:\.\d+)?)?\]$/;
 const OSC_EVENT_RE = /^\[(?:value|velocity|note)\]$/;
 function oscIsRecognised(token) {
 return OSC_POSITION_RE.test(token)
 || OSC_MARKERID_RE.test(token)
 || OSC_FADER_RE.test(token)
 || OSC_MARKERFADER_RE.test(token)
 || OSC_EVENT_RE.test(token);
 }
 // The recognised placeholder *sources* – used only for the
 // "not recognised" tooltip's source list. The osc-bindings partial
 // ships the authoritative list per-row in
 // ``data-osc-placeholder-names`` (server is the single source of
 // truth); this fallback is for editors mounted without the partial.
 // Keep it in step with ``PLACEHOLDERS``.
 const OSC_PLACEHOLDER_NAMES_FALLBACK = new Set([
 'x', 'y', 'z', 'markerid', 'fader', 'markerfader',
 'value', 'velocity', 'note',
 ]);
 // client-side mirror of Python's
 // ``unresolved_placeholders``. Rebuilds the unresolved-set from the
 // editor's current text + the live marker registry + the row's
 // ``markers`` so the operator sees red pills update as they edit,
 // without a server round-trip. Only position + ``markerid`` slots
 // surface here (mirrors the server's ``_EDIT_TIME_SOURCES``); fader /
 // ``markerfader`` slots surface as runtime skips. This parse regex
 // captures source / index / transform-chain so ``z.frac`` (which
 // needs ``GridConfig.max_height`` > 0) can be detected. The index group
 // stays digit-only (``:(\d+)``) so a ``:cN`` controller reference –
 // recognised but unknowable at edit time – fails this match and falls
 // through to a runtime skip, mirroring the server excluding
 // ``controller_index`` slots from ``unresolved_placeholders``.
 const OSC_POSITION_PARSE_RE = /^\[(x|y|z|markerid)(?::(\d+))?((?:\.(?:inv|frac))*)\]$/;
 function oscEditorRecomputeUnresolved(editor) {
 const registered = new Set(
 oscEditorParseJsonAttr(editor, 'oscRegisteredMarkerIds', [])
 .map(v => Number(v)),
 );
 // Mirror of Python's ``_effective_default_marker_id``: a bare ``[x]``
 // resolves when the row names any usable default marker. ``all`` / ``cN``
 // are dynamic – treated as resolvable whenever at least one marker is
 // controlled; a numeric token counts only when controlled.
 const markerTokens = (editor.dataset.oscRowMarkers || '')
 .split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
 let hasDefaultMarker = false;
 for (const tok of markerTokens) {
 if (tok === 'all' || /^c[1-9][0-9]*$/.test(tok)) {
 if (registered.size > 0) { hasDefaultMarker = true; break; }
 } else if (/^[0-9]+$/.test(tok) && registered.has(Number(tok))) {
 hasDefaultMarker = true; break;
 }
 }
 const gridMaxHeightRaw = (editor.dataset.oscGridMaxHeight || '').trim();
 const gridMaxHeight = gridMaxHeightRaw === ''
 ? 0
 : Number(gridMaxHeightRaw);
 const gridUnset = !(gridMaxHeight > 0);
 const text = oscEditorPlainText(editor);
 const out = [];
 const seen = new Set();
 let match;
 OSC_PILL_RE.lastIndex = 0;
 while ((match = OSC_PILL_RE.exec(text)) !== null) {
 const token = match[0];
 if (seen.has(token)) continue;
 // Only consider recognised tokens (the server compiles only
 // those to slots), and of those only position / markerid slots.
 if (!oscIsRecognised(token)) continue;
 const parsed = OSC_POSITION_PARSE_RE.exec(token);
 if (!parsed) continue;
 const source = parsed[1];
 const index = parsed[2]; // undefined when bare
 const chain = parsed[3] || '';
 const isZFrac = source === 'z' && chain.indexOf('.frac') !== -1;
 let unresolved = false;
 if (index === undefined) {
 unresolved = !hasDefaultMarker;
 if (!unresolved && isZFrac && gridUnset) {
 unresolved = true;
 }
 } else if (source !== 'markerid') {
 // ``[markerid:N]`` substitutes ``N`` directly – never a miss.
 const id = Number(index);
 unresolved = !registered.has(id);
 if (!unresolved && isZFrac && gridUnset) {
 unresolved = true;
 }
 }
 if (unresolved) {
 out.push(token);
 seen.add(token);
 }
 }
 editor.dataset.oscUnresolvedPlaceholders = JSON.stringify(out);
 }
 // Update the row's Enabled-checkbox unresolved-flag to match
 // the just-recomputed unresolved set. Mirrors the server-side
 // initial render so the operator's edits land on the same UX
 // signal between saves.
 //
 // Use custom ``data-osc-unresolved`` instead of ``aria-invalid`` to avoid
 // blocking Save when deps unresolved. CSS styling matches; gate ignores the attr.
 //
 // Also clears the partner field's stale ``field-warn-msg`` /
 // aria-invalid when the just-edited side resolves the dependency,
 // so the operator doesn't have to re-blur the partner to see the
 // warning go away.
 function oscEditorSyncEnabledUnresolved(editor) {
 const rowId = editor.dataset.oscMessageEditor;
 if (!rowId) return;
 // The Enabled checkbox is the only ``input[name="enabled"]``
 // inside the same form as this editor – scope to the closest
 // form so a future second-Enabled checkbox doesn't get
 // collateral updates.
 const form = editor.closest('form');
 if (!form) return;
 const enabled = form.querySelector('input[name="enabled"]');
 const unresolved = oscEditorParseJsonAttr(
 editor, 'oscUnresolvedPlaceholders', [],
 );
 if (enabled) {
 if (unresolved.length > 0) {
 enabled.setAttribute('data-osc-unresolved', 'true');
 } else {
 enabled.removeAttribute('data-osc-unresolved');
 }
 // Mirror unresolved state into ``aria-describedby`` span for screen-reader announcement.
 // ``aria-live="polite"`` makes changes announce automatically.
 const helpId = enabled.getAttribute('aria-describedby') || '';
 const helpSpan = helpId ? document.getElementById(helpId) : null;
 if (helpSpan) {
 helpSpan.textContent = unresolved.length > 0
 ? 'Will save disabled: this row uses placeholder values'
 + ' that are not resolved yet (no default marker, or an'
 + ' explicit marker reference targets an unregistered'
 + ' marker).'
 : '';
 }
 }
 // ): the cross-field warn-msg the server
 // emitted at last blur is stale the moment either field changes –
 // even if the row is *still* unresolved, the dependency may have
 // changed shape (``[x]`` → ``[x:9]`` swaps "missing default
 // marker" for "marker 9 not registered"), and the partner field
 // would otherwise keep describing the wrong problem. Clear partner
 // warnings unconditionally on every recompute; the next blur on
 // either field will re-emit a fresh warn-msg from the server-side
 // validator if one still applies.
 const messageError = form.querySelector(
 '[id$="-error"][id^="osc-message-"]',
 );
 const markerInput = form.querySelector('input[name="markers"]');
 const markerError = markerInput
 ? document.getElementById(
 markerInput.getAttribute('aria-describedby') || '',
 )
 : null;
 [messageError, markerError].forEach((slot) => {
 if (!slot) return;
 const warn = slot.querySelector('.field-warn-msg');
 if (warn) {
 slot.innerHTML = '';
 const owner = document.querySelector(
 '[aria-describedby="' + slot.id + '"]',
 );
 if (owner) {
 owner.setAttribute('aria-invalid', 'false');
 refreshFormGate(owner.closest('form'));
 }
 }
 });
 }
 function oscEditorInit(root) {
 root.querySelectorAll('[data-osc-message-editor]').forEach(editor => {
 oscEditorRenderPills(editor);
 oscEditorSyncHidden(editor);
 });
 }
 // Re-render pills after every HTMX swap (the section partial is
 // returned wholesale from CRUD routes, so the editor DOM is fresh).
 document.body.addEventListener('htmx:afterSwap', (event) => {
 oscEditorInit(event.target);
 });
 document.addEventListener('input', (event) => {
 const editor = event.target.closest('[data-osc-message-editor]');
 if (!editor) return;
 oscEditorSyncHidden(editor);
 });
 document.addEventListener('blur', (event) => {
 const editor = event.target.closest('[data-osc-message-editor]');
 if (!editor) return;
 oscEditorRecomputeUnresolved(editor);
 oscEditorRenderPills(editor);
 oscEditorSyncHidden(editor);
 oscEditorSyncEnabledUnresolved(editor);
 // Fire the HTMX validation we wired with ``hx-trigger="osc-validate"``.
 window.htmx.trigger(editor, 'osc-validate');
 }, true); // capture: ``blur`` doesn't bubble.
 // Re-evaluate unresolved pills as operator edits the Default markers field.
 // Mirrors Python's ``_row_unresolved_placeholders`` for real-time pill state.
 document.addEventListener('input', (event) => {
 const input = event.target.closest('input[name="markers"]');
 if (!input) return;
 const form = input.closest('form');
 if (!form) return;
 const editor = form.querySelector('[data-osc-message-editor]');
 if (!editor) return;
 editor.dataset.oscRowMarkers = (input.value || '').trim();
 oscEditorRecomputeUnresolved(editor);
 oscEditorRenderPills(editor);
 oscEditorSyncEnabledUnresolved(editor);
 });
 // Single-line: prevent Enter from inserting a newline (the field is
 // semantically one OSC message). Paste handler strips newlines so
 // multi-line paste from a doc still lands as a flat string.
 document.addEventListener('keydown', (event) => {
 const editor = event.target.closest('[data-osc-message-editor]');
 if (!editor) return;
 if (event.key === 'Enter') event.preventDefault();
 });
 // ``document.execCommand`` is deprecated and not guaranteed to
 // keep working across browsers. Insert via the Range/Selection
 // API, mirroring the chip-insertion path above so paste behaves
 // the same as a placeholder insertion.
 function oscEditorInsertText(editor, text) {
 editor.focus();
 const sel = window.getSelection();
 if (!sel) return;
 let range;
 if (sel.rangeCount > 0 && editor.contains(sel.focusNode)) {
 range = sel.getRangeAt(0);
 } else {
 range = document.createRange();
 range.selectNodeContents(editor);
 range.collapse(false);
 }
 range.deleteContents();
 const textNode = document.createTextNode(text);
 range.insertNode(textNode);
 const newRange = document.createRange();
 newRange.setStartAfter(textNode);
 newRange.collapse(true);
 sel.removeAllRanges();
 sel.addRange(newRange);
 }
 document.addEventListener('paste', (event) => {
 const editor = event.target.closest('[data-osc-message-editor]');
 if (!editor) return;
 event.preventDefault();
 const text = (event.clipboardData || window.clipboardData).getData('text');
 const flat = text.replace(/\s+/g, ' ');
 oscEditorInsertText(editor, flat);
 oscEditorSyncHidden(editor);
 // Same as the chip-insertion path – programmatic DOM
 // mutations don't reliably fire the ``input`` event the
 // dirty-state gate listens on. Without this dispatch the
 // Save-as-template / Discard buttons would stay stuck in
 // Dispatch input event so dirty-state gate updates without typing.
 editor.dispatchEvent(new Event('input', { bubbles: true }));
 });
 // Issue – pills are ``contenteditable=false``
 // so the operator can't edit them inline. A single click unwraps
 // a pill back into plain text so the operator can extend it
 // (e.g. turn ``[x]`` into ``[x:2]``); the next blur re-runs
 // the pill renderer and re-wraps as a single pill. Caret lands
 // just before the closing ``]`` so typing immediately edits
 // inside the brackets – the common case for adding the
 // ``:N`` suffix.
 document.addEventListener('click', (event) => {
 const pill = event.target.closest('.osc-pill');
 if (!pill) return;
 const editor = pill.closest('[data-osc-message-editor]');
 if (!editor) return;
 event.preventDefault();
 const text = pill.textContent;
 const textNode = document.createTextNode(text);
 pill.parentNode.replaceChild(textNode, pill);
 const closing = text.lastIndexOf(']');
 const caretPos = closing >= 0 ? closing : text.length;
 // Same null-guard as the chip-insertion + paste paths – some
 // contexts (detached iframes, headless test runners) return
 // ``null`` from ``getSelection()`` and we'd throw on the
 // subsequent ``removeAllRanges`` call.
 const sel = window.getSelection();
 if (sel) {
 const range = document.createRange();
 range.setStart(textNode, caretPos);
 range.collapse(true);
 sel.removeAllRanges();
 sel.addRange(range);
 }
 editor.focus();
 oscEditorSyncHidden(editor);
 // Dispatch input event: unwrapping mutates DOM directly, so dirty-state gate needs the event.
 editor.dispatchEvent(new Event('input', { bubbles: true }));
 });
 // "Save as template…" button captures the whole row form to endpoint.
 // Template carries all operator-tunable fields; ``enabled`` and ``markers`` dropped server-side.
 //
 // We use ``FormData`` to gather the live form state (matches
 // what the row's normal Save POST would send), then append the
 // operator-typed ``template_name`` and submit as URL-encoded
 // form data. Reusing the row's form means we don't have to
 // duplicate trigger / message parsing in JS – the same
 // ``_apply_osc_binding_fields`` server helper that powers the
 // row's Save handles the parse on the template-save side too.
 document.addEventListener('click', async (event) => {
 const btn = event.target.closest('[data-osc-save-template-btn]');
 if (!btn) return;
 event.preventDefault();
 const rowId = btn.dataset.rowId;
 if (!rowId) return;
 const form = btn.closest('form');
 if (!form) return;
 const nameInput = form.querySelector('input[name="name"]');
 const messageInput = form.querySelector('input[name="osc_message"]');
 const rowName = (nameInput && nameInput.value || '').trim();
 const message = (messageInput && messageInput.value || '').trim();
 if (!message) {
 showToast('OSC message is empty – fill it in before saving as a template');
 return;
 }
 // Pre-fill the modal with the row's own name so the operator
 // doesn't have to retype "ETC stage" if that's already the row
 // name. They can still change it.
 const tplName = await modalPrompt({
 title: 'Save row as template',
 label: 'Template name',
 placeholder: 'e.g. ETC stage',
 initial: rowName,
 confirmLabel: 'Save',
 });
 if (!tplName) return;
 // Snapshot the live form. ``FormData`` includes every named
 // input the row's normal Save would send; appending
 // ``template_name`` is the only extra the endpoint needs.
 // ``enabled`` arrives as ``"on"`` when the checkbox is ticked
 // and is omitted entirely when not – the server-side endpoint
 // simply ignores it for template payload purposes.
 const fd = new FormData(form);
 fd.set('template_name', tplName);
 const body = new URLSearchParams();
 // ``URLSearchParams`` from a ``FormData`` keeps multi-value
 // fields (``trigger.modifiers`` checkbox group) correctly –
 // each value lands as a repeat key, which is what the
 // server-side ``request.forms.getall`` reader expects.
 fd.forEach((value, key) => body.append(key, value));
 try {
 const res = await fetch(
 '/section/osc_binding/' + encodeURIComponent(rowId) + '/save_as_template',
 {
 method: 'POST',
 headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
 body: body.toString(),
 },
 );
 if (!res.ok) {
 const text = await res.text();
 // The endpoint returns the bindings partial on success
 // and a small error partial on failure; surface a toast
 // either way without trying to parse JSON.
 showToast(text.length < 200 ? text : 'Save failed (HTTP ' + res.status + ')');
 return;
 }
 showToast('Saved template "' + tplName + '"');
 // The endpoint returns the bindings-section partial in its
 // body – swap it in directly so the dropdown updates with
 // the new entry on the same response cycle as the click.
 const html = await res.text();
 const target = document.getElementById('osc-bindings-section');
 if (target && html) {
 // htmx 1.x doesn't expose a public ``swap()`` method (added
 // in 2.x). We own the outerHTML swap, then call into the
 // two public hooks that DO exist:
 //
 // * ``htmx.process(elt)`` rescans ``hx-*`` attributes on
 // the freshly-inserted DOM so they wire up just like
 // content swapped via a normal htmx response.
 // * ``htmx.trigger(elt, 'htmx:afterSwap')`` fires the
 // event that downstream listeners (placeholder pills,
 // trigger select re-init, …) already key off of, so
 // they re-render against the new section.
 target.outerHTML = html;
 const newTarget = document.getElementById('osc-bindings-section');
 if (newTarget && window.htmx) {
 if (window.htmx.process) window.htmx.process(newTarget);
 if (window.htmx.trigger) window.htmx.trigger(newTarget, 'htmx:afterSwap');
 }
 }
 } catch (err) {
 showToast('Save failed: ' + (err.message || 'unknown error'));
 }
 });
 // Placeholder-chip insertion: works for both ``<input>`` and
 // ``contenteditable`` targets so the editor migration didn't
 // regress JS-off operators / future plain-text use.
 //
 // Caret preservation: clicking a ``<button>`` shifts focus to
 // the button by default, which collapses the contenteditable's
 // selection to its start – every chip click then inserts at
 // position 0 instead of where the operator's cursor was. Track
 // the editor's last range on every selectionchange + the editor's
 // own input/keyup events so we can restore it when a chip fires.
 // Also ``preventDefault`` on the chip's mousedown so the focus
 // doesn't shift in the first place; the saved range is the
 // safety net for browsers / IME states where mousedown ordering
 // varies.
 const oscEditorLastRange = new WeakMap();
 function rememberRange(editor) {
 const sel = window.getSelection();
 if (!sel || !sel.rangeCount) return;
 const r = sel.getRangeAt(0);
 if (editor.contains(r.startContainer) && editor.contains(r.endContainer)) {
 oscEditorLastRange.set(editor, r.cloneRange());
 }
 }
 document.addEventListener('selectionchange', () => {
 const sel = window.getSelection();
 if (!sel || !sel.rangeCount) return;
 const node = sel.focusNode;
 if (!node) return;
 const editor = (node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement)
 ?.closest('[data-osc-message-editor]');
 if (editor) rememberRange(editor);
 });
 document.addEventListener('mousedown', (event) => {
 const chip = event.target.closest('.placeholder-chip');
 if (!chip) return;
 // Snapshot the targeted editor's caret BEFORE the focus shifts
 // to the chip button – and prevent the focus shift itself so
 // the editor's selection survives the click.
 const targetSelector = chip.dataset.oscTarget;
 if (targetSelector) {
 const target = document.querySelector(targetSelector);
 if (target && target.matches('[data-osc-message-editor]')) {
 rememberRange(target);
 }
 }
 event.preventDefault();
 });
 document.addEventListener('click', (event) => {
 const chip = event.target.closest('.placeholder-chip');
 if (!chip) return;
 const targetSelector = chip.dataset.oscTarget;
 const payload = chip.dataset.oscPlaceholder;
 if (!targetSelector || !payload) return;
 const target = document.querySelector(targetSelector);
 if (!target) return;
 if (target.matches('[data-osc-message-editor]')) {
 // Contenteditable path: insert a real pill span at the caret.
 target.focus();
 // ``window.getSelection()`` can return ``null`` (e.g. in
 // detached iframes or some headless contexts). Bail rather
 // than calling ``removeAllRanges`` on null.
 const sel = window.getSelection();
 if (!sel) return;
 let range;
 const saved = oscEditorLastRange.get(target);
 if (saved && target.contains(saved.startContainer) && target.contains(saved.endContainer)) {
 // Use the saved caret position from before the chip was
 // clicked. ``getSelection`` after ``target.focus()`` would
 // collapse to position 0 in most browsers; the saved range
 // preserves the operator's actual edit point.
 range = saved.cloneRange();
 range.deleteContents();
 } else if (sel.rangeCount && target.contains(sel.focusNode)) {
 range = sel.getRangeAt(0);
 range.deleteContents();
 } else {
 // Last-resort fallback: end of editor (the chip click
 // wasn't preceded by a known caret).
 range = document.createRange();
 range.selectNodeContents(target);
 range.collapse(false);
 }
 const pill = document.createElement('span');
 pill.className = 'osc-pill';
 pill.contentEditable = 'false';
 pill.title = "Click to edit";
 pill.textContent = payload;
 // Pad with a space before/after when adjacent to non-whitespace
 // so chips don't fuse with neighbouring tokens.
 const before = range.startContainer.nodeType === Node.TEXT_NODE
 ? range.startContainer.textContent.slice(0, range.startOffset)
 : '';
 if (before.length > 0 && !/\s$/.test(before)) {
 range.insertNode(document.createTextNode(' '));
 range.collapse(false);
 }
 range.insertNode(pill);
 // Trailing space + caret after it.
 const trailing = document.createTextNode(' ');
 pill.parentNode.insertBefore(trailing, pill.nextSibling);
 const newRange = document.createRange();
 newRange.setStartAfter(trailing);
 newRange.collapse(true);
 sel.removeAllRanges();
 sel.addRange(newRange);
 oscEditorSyncHidden(target);
 // Programmatic DOM mutations don't reliably fire the
 // ``input`` event the dirty-state gate listens on, so
 // dispatch one explicitly. Without this, inserting a
 // placeholder chip leaves Save-as-template / Discard
 // buttons stuck in their previous state.
 // Dispatch input event for dirty-state gate.
 target.dispatchEvent(new Event('input', { bubbles: true }));
 return;
 }
 // ``<input>`` path (tests + JS-off contexts may still use a
 // plain text input).
 const start = target.selectionStart != null ? target.selectionStart : target.value.length;
 const end = target.selectionEnd != null ? target.selectionEnd : target.value.length;
 const before = target.value.slice(0, start);
 const after = target.value.slice(end);
 const leftPad = before.length > 0 && !/\s$/.test(before) ? ' ' : '';
 const rightPad = after.length > 0 && !/^\s/.test(after) ? ' ' : '';
 const inserted = leftPad + payload + rightPad;
 target.value = before + inserted + after;
 const cursor = start + inserted.length;
 target.focus();
 target.setSelectionRange(cursor, cursor);
 target.dispatchEvent(new Event('input', { bubbles: true }));
 });
 // Drag-and-drop reordering for OSC output rows; replaces ↑/↓ buttons.
 // Event delegation on document so HTMX-swapped sections inherit behavior.
 // Immediately update collapsed row's trigger-type badge when operator changes dropdown.
 // Without this, badge only refreshes on Save, creating mismatch with selection.
 document.addEventListener('change', (event) => {
 const sel = event.target.closest('[data-osc-trigger-type-select]');
 if (!sel) return;
 const row = sel.closest('.osc-binding-row');
 if (!row) return;
 row.dataset.triggerKind = sel.value;
 const badge = row.querySelector('.osc-binding-kind-badge');
 if (badge) badge.textContent = sel.value;
 });
 // Show/hide per-row Framing field based on Protocol (TCP only).
 // Element stays in DOM so form posts the value (read by TCP-render only).
 document.addEventListener('change', (event) => {
 const sel = event.target.closest('[data-osc-protocol-select]');
 if (!sel) return;
 const row = sel.closest('.osc-binding-row');
 if (!row) return;
 const framingField = row.querySelector('[data-osc-framing-field]');
 if (!framingField) return;
 framingField.style.display = (sel.value === 'tcp') ? '' : 'none';
 });
 // Operator-feedback follow-up: hide / show the ``min_change`` input
 // based on the Stream trigger's mode select. The wrapper carries
 // ``hidden`` server-side when the row's mode is ``always`` so the
 // initial render is correct without JS; this listener handles
 // subsequent toggles by the operator. Scoped to the closest form
 // so a future second mode-select on the page doesn't collateral-
 // toggle every wrapper at once.
 document.addEventListener('change', (event) => {
 const sel = event.target.closest('[data-osc-stream-mode-select]');
 if (!sel) return;
 const form = sel.closest('form');
 if (!form) return;
 const wrap = form.querySelector('[data-osc-stream-min-change-wrap]');
 if (!wrap) return;
 if (sel.value === 'on_change') {
 wrap.removeAttribute('hidden');
 } else {
 wrap.setAttribute('hidden', '');
 }
 });
 // MIDI fader detail panel: "Source kind" select toggles MIDI sub-fields visibility.
 // Scoped to closest form to avoid collateral toggles of other faders.
 document.addEventListener('change', (event) => {
 const sel = event.target.closest('[data-midi-source-kind-select]');
 if (!sel) return;
 const form = sel.closest('form');
 if (!form) return;
 // Toggle MIDI sub-fields and the Learn row:
 // both carry a ``data-midi-source-detail*`` marker so a fader with
 // no MIDI source doesn't show the inputs or the Learn button.
 const rows = form.querySelectorAll(
 '[data-midi-source-detail], [data-midi-source-detail-learn]');
 const display = (sel.value === 'midi') ? '' : 'none';
 rows.forEach((row) => { row.style.display = display; });
 });
 // Detection: reveal the "Assisted tracking" sub-block only when the
 // Tracking Mode toggle is set to AI Assisted. The server renders the block
 // with ``hidden`` when the saved mode is Fully Automatic, so the first paint
 // is correct without JS; this handles subsequent operator toggles. Scoped to
 // the closest form so a future second toggle can't collateral-toggle it.
 document.addEventListener('change', (event) => {
 const radio = event.target.closest('input[name="pin_mode"]');
 if (!radio) return;
 const form = radio.closest('form');
 if (!form) return;
 const checked = form.querySelector('input[name="pin_mode"]:checked');
 const show = checked !== null && checked.value === 'assist';
 form.querySelectorAll('[data-assist-only]').forEach((el) => {
 if (show) { el.removeAttribute('hidden'); } else { el.setAttribute('hidden', ''); }
 });
 });
 // Generic drag-reorder shared by the OSC Transmitters + OSC Destinations
 // row lists: same ⋮⋮ handle on both. Each row carries the bulk-reorder
 // endpoint + swap target in ``data-reorder-url`` / ``data-reorder-target``,
 // so one set of listeners drives both lists.
 const OSC_REORDER_HANDLE_SEL = '.osc-binding-drag-handle, .osc-destination-drag-handle';
 const OSC_REORDER_ROW_SEL = '.osc-binding-row, .osc-destination-row';
 let oscDragSourceId = null;
 document.addEventListener('dragstart', (event) => {
 const handle = event.target.closest(OSC_REORDER_HANDLE_SEL);
 if (!handle) return;
 const row = handle.closest(OSC_REORDER_ROW_SEL);
 if (!row) return;
 oscDragSourceId = row.dataset.rowId;
 row.classList.add('dragging');
 // ``effectAllowed = "move"`` + a payload satisfies Firefox's
 // drag-init contract; the actual id transfer happens via the
 // ``oscDragSourceId`` closure (more reliable than dataTransfer
 // across drag-leave race conditions on some browsers).
 if (event.dataTransfer) {
 event.dataTransfer.effectAllowed = 'move';
 try { event.dataTransfer.setData('text/plain', oscDragSourceId); } catch (_) {}
 }
 });
 document.addEventListener('dragend', (event) => {
 const handle = event.target.closest(OSC_REORDER_HANDLE_SEL);
 const row = handle ? handle.closest(OSC_REORDER_ROW_SEL) : null;
 if (row) row.classList.remove('dragging');
 document.querySelectorAll('.drop-target').forEach(el => {
 el.classList.remove('drop-target');
 });
 oscDragSourceId = null;
 });
 document.addEventListener('dragover', (event) => {
 if (!oscDragSourceId) return;
 const targetRow = event.target.closest(OSC_REORDER_ROW_SEL);
 if (!targetRow) return;
 event.preventDefault();
 if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
 document.querySelectorAll('.drop-target').forEach(el => {
 if (el !== targetRow) el.classList.remove('drop-target');
 });
 if (targetRow.dataset.rowId !== oscDragSourceId) {
 targetRow.classList.add('drop-target');
 }
 });
 document.addEventListener('drop', (event) => {
 if (!oscDragSourceId) return;
 const targetRow = event.target.closest(OSC_REORDER_ROW_SEL);
 if (!targetRow) return;
 event.preventDefault();
 const list = targetRow.parentElement;
 if (!list) return;
 // Don't cross lists: a drag started in one list only reorders within it.
 const sourceRow = list.querySelector('[data-row-id="' + oscDragSourceId + '"]');
 if (!sourceRow || sourceRow === targetRow) {
 oscDragSourceId = null;
 return;
 }
 // Insert source *before* target when dropping in the upper half
 // of the target row, *after* otherwise – feels natural for a
 // vertical list (mirrors how QLab's cue list reorders).
 const rect = targetRow.getBoundingClientRect();
 const after = (event.clientY - rect.top) > rect.height / 2;
 list.insertBefore(sourceRow, after ? targetRow.nextSibling : targetRow);
 // Build the new ordering and POST it to the row's own reorder
 // endpoint. HTMX is already loaded so ``htmx.ajax`` keeps the
 // re-render flow identical to every other CRUD round-trip.
 const url = targetRow.dataset.reorderUrl;
 const targetId = targetRow.dataset.reorderTarget;
 if (url && targetId) {
 const order = Array.from(list.querySelectorAll(OSC_REORDER_ROW_SEL))
 .map(el => el.dataset.rowId)
 .join(',');
 window.htmx.ajax('POST', url, {
 target: '#' + targetId,
 swap: 'outerHTML',
 values: { order },
 });
 }
 oscDragSourceId = null;
 });
 document.addEventListener('DOMContentLoaded', () => {
 initializeSectionFolding(document);
 initializeInlineAdvanced(document);
 initTabs();
 oscEditorInit(document);
 });
 </script>
 %# Privilege-password modal. Surfaces whenever a privileged subsystem
 %# (e.g. a network apply) parks a password prompt on the broker. Global so
 %# it works on every page, not just where the prompting action lives; polls
 %# every 3s so a fresh prompt appears without a reload and clears when done.
 %# ``hx-disinherit`` stops the response from inheriting any ancestor swap.
 %# The route is auth-exempt (it returns an empty partial when logged out),
 %# so the poll on /login can't loop into a redirect.
 <div hx-disinherit="hx-select hx-target">
     <div id="privilege-password-modal"
          hx-get="/system/privilege/password/modal"
          hx-trigger="load, every 3s"
          hx-target="this"
          hx-swap="innerHTML"></div>
 </div>
</body>
</html>
