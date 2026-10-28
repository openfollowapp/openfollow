% rebase('base.tpl')
% from openfollow.units import UnitSystem, unit_suffix_length
% _us = UnitSystem(config.ui.unit_system)
% _imp = _us is UnitSystem.IMPERIAL
% _len = unit_suffix_length(_us)
% _len_type = 'text' if _imp else 'number'
% _len_step = '' if _imp else ' step="0.01"'
% import json
% from openfollow.configuration import MAX_CALIBRATION_MARKS
% from openfollow.lens_model import C_BOUND
% _cam = config.camera
% _lens_k1, _lens_k2, _lens_cx, _lens_cy = _cam.lens_k1, _cam.lens_k2, _cam.lens_cx, _cam.lens_cy
% _suggest_on = config.ui.feature_on('lens_suggestions')
% _g = config.grid
% _saved = {
%   'mode': config.camera.calibration_mode,
%   'marks': [{'id': m.id, 'name': m.name, 'kind': m.kind, 'x': m.x, 'y': m.y, 'z': m.z, 'from_a': m.from_a, 'from_b': m.from_b, 'distance_a': m.distance_a, 'distance_b': m.distance_b, 'pin': m.pin, 'position': m.position} for m in config.camera.calibration_marks],
%   'grid': {'width': _g.width, 'depth': _g.depth, 'spacing': _g.spacing, 'x_offset': _g.x_offset, 'y_offset': _g.y_offset, 'z_offset': _g.z_offset},
%   'fov': config.camera.fov,
%   'height': config.camera.calibration_height,
%   'lens_rating': config.camera.calibration_lens_rating,
%   'lens': [_lens_k1, _lens_k2, _lens_cx, _lens_cy],
%   'max_marks': MAX_CALIBRATION_MARKS,
% }

<style>
  .wizard-header {
    display: flex;
    align-items: center;
    gap: 1rem;
    margin-bottom: 1rem;
    flex-wrap: wrap;
  }
  .wizard-header a {
    color: var(--muted);
    text-decoration: none;
    font-size: 0.88rem;
    font-weight: 600;
  }
  .wizard-header a:hover { color: var(--text); }
  .wizard-steps {
    display: flex;
    gap: 0.25rem;
    padding: 0.35rem;
    border-radius: 1rem;
    background: var(--surface);
    border: 1px solid var(--border-soft);
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: none;
    margin-bottom: 1rem;
  }
  .wizard-steps::-webkit-scrollbar { display: none; }
  .wizard-step-btn {
    flex: 1;
    min-width: fit-content;
    padding: 0.45rem 0.7rem;
    border-radius: 0.75rem;
    border: 1px solid transparent;
    background: transparent;
    color: var(--muted);
    font-size: 0.78rem;
    font-weight: 600;
    white-space: nowrap;
    cursor: pointer;
    transition: all 0.15s ease;
  }
  .wizard-step-btn:hover {
    color: var(--text);
    background: rgba(255,255,255,0.04);
    transform: none; filter: none;
  }
  .wizard-step-btn.active {
    color: var(--accent);
    background: var(--accent-soft);
    border-color: rgba(255,188,0,0.35);
  }
  .wizard-step-btn.completed {
    color: var(--success-text);
  }
  .wizard-content { display: none; }
  .wizard-content.active { display: block; }
  .wizard-nav {
    display: flex;
    justify-content: space-between;
    gap: 0.72rem;
    margin-top: 1.2rem;
  }
  .wizard-nav .spacer { flex: 1; }
  .wizard-help {
    color: var(--muted);
    font-size: 0.88rem;
    line-height: 1.6;
    margin-bottom: 0.72rem;
  }
  .wizard-help strong { color: var(--text); }
  .wizard-tip {
    color: var(--muted);
    font-size: 0.82rem;
    font-style: italic;
    margin-top: 0.5rem;
  }
  .wizard-field-error {
    color: var(--error-text);
    font-weight: 600;
    font-size: 0.8rem;
    margin-top: 0.25rem;
  }
  .wizard-preview-container {
    position: relative;
    width: 100%;
    margin-bottom: 1rem;
    background: var(--bg-deep);
    border-radius: 0.75rem;
    border: 1px solid var(--border);
    overflow: hidden;
    transition: border-color 0.3s;
  }
  .wizard-preview-container img {
    display: block;
    width: 100%;
    height: auto;
  }
  .wizard-overlay {
    position: absolute;
    top: 0; left: 0;
    width: 100%;
    height: 100%;
    pointer-events: none;
    touch-action: none;
  }
  /* Inset and rounded as the picture's container, which clips anything outside it. */
  .wizard-overlay:focus { outline: none; }
  .wizard-overlay:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; border-radius: inherit; }
  /* A step's heading takes the focus only so a step change is announced and Tab starts there. */
  .wizard-content h2[tabindex="-1"]:focus { outline: none; }
  .wizard-overlay .handle {
    pointer-events: all;
    cursor: grab;
    outline: none;
  }
  .wizard-overlay .handle:focus {
    outline: none;
  }
  .wizard-overlay .handle:focus .handle-ring {
    display: block;
  }
  .handle-ring {
    display: none;
    stroke: var(--accent);
    stroke-width: 2;
    fill: none;
    vector-effect: non-scaling-stroke;
  }
  .wizard-overlay .handle:active { cursor: grabbing; }
  .wizard-no-feed {
    padding: 2rem;
    text-align: center;
  }
  .wizard-solved-params {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 0.5rem;
    margin-top: 0.72rem;
    padding: 0.84rem;
    border-radius: 0.75rem;
    background: var(--surface);
    border: 1px solid var(--border-soft);
  }
  .wizard-solved-param {
    display: flex;
    flex-direction: column;
  }
  .wizard-solved-param .param-label {
    color: var(--muted);
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 600;
  }
  .wizard-solved-param .param-value {
    color: var(--text);
    font-size: 0.96rem;
    font-weight: 650;
  }
  .wizard-status {
    font-size: 0.88rem;
    font-weight: 600;
    margin-top: 0.5rem;
  }
  .wizard-status.ok { color: var(--success-text); }
  .wizard-status.ok::before {
    content: ""; display: inline-block; width: 1.1em; height: 1.1em; margin-right: 0.35em; vertical-align: -0.2em;
    background: var(--success-sign-green) no-repeat center / contain;
  }
  .wizard-status.error { color: var(--error-text); }
  .wizard-status.waiting { color: var(--muted); }
  /* Lens step: traced lines, their points and the curve the fitted lens predicts. */
  .lens-hit { pointer-events: all; cursor: crosshair; }
  /* Strokes in CSS pixels: the viewBox is the snapshot's own resolution, which a phone shows at a fifth. */
  .lens-chord, .lens-curve, .lens-halo, .lens-pending, .lens-candidate-line,
  .lens-point .dot, .wizard-point .dot { vector-effect: non-scaling-stroke; }
  .lens-chord { fill: none; stroke: var(--accent); stroke-width: 1.5; stroke-opacity: 0.7; }
  .lens-curve { fill: none; stroke: var(--success-line); stroke-width: 2; stroke-dasharray: 6 4; }
  .lens-line.misfit .lens-curve, .lens-line.misfit .lens-chord { stroke: var(--caution-line); }
  .lens-line.selected .lens-chord { stroke: var(--text); stroke-opacity: 1; stroke-width: 3; }
  /* A line that does not fit glows behind its chord, and its number becomes a caution pill. */
  .lens-halo { fill: none; stroke: none; stroke-width: 16; stroke-linecap: round; stroke-linejoin: round; pointer-events: none; }
  .lens-line.misfit .lens-halo { stroke: var(--caution-line); stroke-opacity: 0.4; }
  /* A traced line is selected by a click anywhere along it. */
  .lens-line-hit { fill: none; stroke: transparent; stroke-width: 22; pointer-events: stroke; cursor: pointer; }
  .lens-point .dot, .wizard-point .dot { fill: var(--accent); stroke: var(--accent); stroke-width: 1.5; }
  .lens-point.off .dot { fill: none; }
  .lens-point.unsnapped .dot { stroke-dasharray: 2 2; }
  .lens-point.selected .dot { stroke-width: 3; }
  .lens-pending { fill: none; stroke: var(--accent); stroke-width: 2; stroke-dasharray: 3 3; }
  .lens-label { fill: var(--text); font-weight: 600; pointer-events: none; }
  .lens-tag { pointer-events: none; }
  .lens-tag rect { fill: var(--caution-line); }
  .lens-tag text { fill: var(--bg-deep); font-weight: 700; }
  .wizard-grid-line { fill: none; stroke: var(--accent); stroke-opacity: 0.4; stroke-width: 1; vector-effect: non-scaling-stroke; pointer-events: none; }
  #review-grid .wizard-grid-line { stroke: var(--success-mark); stroke-opacity: 0.5; }
  .wizard-loupe {
    position: absolute; width: 120px; height: 120px; border-radius: 50%; overflow: hidden;
    border: 2px solid var(--accent); background: var(--bg-deep); pointer-events: none; display: none;
  }
  .wizard-loupe canvas { width: 100%; height: 100%; display: block; }
  .lens-result-main { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
  /* Suggested edges: faint and dashed; only their bubbles take them, a press on one draws as anywhere else. */
  .lens-candidate-line { fill: none; stroke: var(--accent); stroke-width: 2.5; stroke-opacity: 0.6; stroke-dasharray: 8 6; pointer-events: none; }
  .lens-candidate.hot .lens-candidate-line { stroke-opacity: 1; stroke-width: 5; filter: drop-shadow(0 0 3px var(--bg-deep)); }
  #lens-candidates.has-hot .lens-candidate:not(.hot) .lens-candidate-line { stroke-opacity: 0.2; }
  /* One use / reject bubble per suggestion, pointing at its line; in CSS pixels over the picture. */
  .lens-tails, .lens-bubbles { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
  .lens-tail { fill: var(--bg-soft); stroke: var(--border); stroke-width: 1; stroke-linejoin: round; filter: drop-shadow(0 5px 8px rgba(0, 0, 0, 0.45)); }
  .lens-tail.hot { stroke: var(--accent); }
  /* The outline is drawn in the SVG layer below; this only holds the two buttons over it. */
  .lens-bubble { position: absolute; width: 72px; height: 38px; box-sizing: border-box; display: flex; gap: 4px; padding: 4px; pointer-events: auto; }
  .lens-bubble button { width: 30px; height: 30px; min-height: 0; padding: 0; border: 0; border-radius: 50%; background: transparent; display: grid; place-items: center; cursor: pointer; color: var(--muted); }
  .lens-bubble svg { width: 26px; height: 26px; display: block; }
  .lens-bubble .ring { fill: none; stroke: currentColor; stroke-width: 1.8; }
  .lens-bubble .mark { fill: none; stroke: currentColor; stroke-width: 2.2; stroke-linecap: round; stroke-linejoin: round; }
  .lens-bubble .lb-use { color: var(--success-text); }
  .lens-bubble .lb-use:hover .ring, .lens-bubble .lb-use:focus-visible .ring { fill: var(--success-chip); }
  .lens-bubble .lb-reject:hover, .lens-bubble .lb-reject:focus-visible { color: var(--error-text); }
  .lens-bubble .lb-reject:hover .ring, .lens-bubble .lb-reject:focus-visible .ring { fill: var(--error-chip); }
  .lens-bubble button:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
  /* The app's buttons lift by 1 px on hover; these must not move in any state, in any browser. */
  .lens-bubble button, .lens-bubble button:hover, .lens-bubble button:active, .lens-bubble button:focus, .lens-bubble button:focus-visible { transform: none !important; filter: none !important; transition: color 120ms ease; }
  .lens-edges { position: absolute; top: 0; left: 0; width: 100%; height: 100%; display: none; pointer-events: none; }
  .lens-dev { margin-top: 0.72rem; }
  /* Graded meter (docs/STATUS_LANGUAGE.md): five segments, filled in the level's line colour. */
  .wizard-meter { display: inline-flex; gap: 3px; vertical-align: middle; }
  .wizard-meter-seg { width: 14px; height: 8px; border-radius: 2px; background: var(--border-soft); }
  .wizard-meter[data-level="caution"] .wizard-meter-seg.filled { background: var(--caution-line); }
  .wizard-meter[data-level="info"] .wizard-meter-seg.filled { background: var(--info-line); }
  .wizard-meter[data-level="success"] .wizard-meter-seg.filled { background: var(--success-line); }
  /* Load / Save lens calibration dialogs: a radio card inside a dialog keeps its card look. */
  .modal-body .tier-option { display: flex; margin: 0; font-size: inherit; color: inherit; text-transform: none; letter-spacing: normal; font-weight: inherit; }
  .modal-body .tier-list { max-width: none; max-height: 16rem; overflow-y: auto; padding: 4px; margin: -4px; }
  .lens-dlg-title { font-size: 0.68rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); margin: 0.2rem 0 0.45rem; }
  .lens-dlg-sep { height: 1px; background: var(--border-soft); margin: 1rem 0 0.8rem; }
  #ll-size { margin: 0 0 0.8rem; }
  .ll-size-row { display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap; }
  .ll-size-row input { width: 6.5rem; }
  /* Camera Calibration: pins, the drawn rectangle, the origin and the distance lines. */
  .pin-handle { cursor: grab; pointer-events: all; outline: none; }
  .pin-handle:active { cursor: grabbing; }
  .pin-dot { fill: var(--accent); fill-opacity: 0.25; stroke: var(--accent); stroke-width: 2; vector-effect: non-scaling-stroke; }
  .pin-handle.active .pin-dot, .pin-handle:focus .pin-dot { stroke-width: 3.5; fill-opacity: 0.5; }
  .pin-handle.flag .pin-dot { stroke: var(--caution-line); }
  .pin-handle.unmoved .pin-dot { stroke-dasharray: 5 4; fill-opacity: 0.08; }
  .pin-cross { stroke: var(--text); stroke-width: 1; vector-effect: non-scaling-stroke; pointer-events: none; }
  .pin-label { fill: var(--text); stroke: var(--bg-deep); font-weight: 700; paint-order: stroke; pointer-events: none; }
  .pin-pending { fill: none; stroke: var(--accent); stroke-width: 2; stroke-dasharray: 4 3; vector-effect: non-scaling-stroke; pointer-events: none; }
  .pin-pending-cross { fill: none; stroke: var(--accent); stroke-width: 2; vector-effect: non-scaling-stroke; pointer-events: none; }
  .reproj { fill: none; stroke: var(--success-mark); stroke-width: 1.5; vector-effect: non-scaling-stroke; pointer-events: none; }
  .origin-mark { fill: none; stroke: var(--info-text); stroke-width: 2.5; vector-effect: non-scaling-stroke; pointer-events: none; }
  .origin-label { fill: var(--info-text); stroke: var(--bg-deep); font-weight: 700; paint-order: stroke; pointer-events: none; }
  .start-quad { fill: var(--accent); fill-opacity: 0.06; stroke: var(--accent); stroke-opacity: 0.5; stroke-width: 2; stroke-dasharray: 8 6; vector-effect: non-scaling-stroke; pointer-events: none; }
  .dist-line { fill: none; stroke: var(--info-text); stroke-width: 2; stroke-dasharray: 8 5; vector-effect: non-scaling-stroke; pointer-events: none; }
  .dist-line.on { stroke: var(--text); stroke-width: 3; stroke-dasharray: none; }
  .dist-label { fill: var(--text); stroke: var(--bg-deep); font-weight: 700; paint-order: stroke; pointer-events: none; }
  .cal-hit { pointer-events: all; cursor: crosshair; }
  .cal-hit.idle { cursor: default; }
  .dist-wrap { overflow-x: auto; }
  .dist-table { font-variant-numeric: tabular-nums; }
  .dist-table th { text-align: left; font-size: 0.68rem; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); font-weight: 600; padding: 0.3rem 0.5rem; }
  .dist-table tr.pair { cursor: pointer; }
  .dist-table tr.pair:hover td, .dist-table tr.pair.on td { background: var(--surface-strong); }
  .dist-table td.num { text-align: right; font-weight: 700; }
  .calib-row { border-top: 1px solid var(--border-soft); padding: 0.6rem 0; }
  .calib-row .row { margin: 0.5rem 0 0; }
  .calib-row-head { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
  .calib-row-head strong { font-size: 0.95rem; }
  .calib-fit { color: var(--muted); }
  .calib-reason { color: var(--error-text); margin-top: 0.35rem; }
  /* User input: the lens centre's two fields under the sliders, under one name. */
  .lens-centre { margin-top: 0.9rem; }
  .lens-centre-title { margin-bottom: 0.45rem; color: var(--text); font-size: 0.85rem; font-weight: 600; }
  .corner-xyz { margin-left: auto; font-variant-numeric: tabular-nums; color: var(--text); font-size: 0.88rem; }
  .caution-list { margin: 0.3rem 0 0; padding-left: 1.1rem; }
  .caution-text { color: var(--caution-text); }
  /* Full strength in a tile, and dark ink on a checked tile's accent fill. */
  .tier-option > span small.caution-text { opacity: 1; }
  .tier-option input:checked + span .caution-text { color: inherit; }
  .mode-field { margin-bottom: 0.9rem; }
  .lens-current { margin: 0.9rem 0; }
  .lens-current-values { margin: 0.2rem 0 0.6rem; color: var(--muted); }
  /* The choices are buttons, without a form label's capitals: primary until one is chosen, then the chosen one
     stays filled and the others turn secondary. */
  .lens-current .tier-option { text-transform: none; letter-spacing: normal; }
  .lens-current .tier-option > span { border-color: var(--btn-secondary-border); }
  .lens-current.asking .tier-option > span { background: var(--btn-primary-bg); color: var(--btn-ink-dark); border-color: var(--btn-primary-bg); }
  .axis-note { font-weight: 400; text-transform: none; letter-spacing: 0; }
  #step-calib.mode-simple .only-advanced, #step-calib.mode-advanced .only-simple { display: none; }
  #wiz-hint, #lens-hint { margin: 0.2rem 0 0.9rem; }
  /* Simple: a step is greyed out and cannot be used until the step before it is done. */
  .step-locked { opacity: 0.4; filter: grayscale(0.6); transition: opacity 150ms; }
  @media (prefers-reduced-motion: reduce) { .step-locked { transition: none; } }
  /* The menu a click on the picture opens in Advanced. */
  .pin-menu { position: absolute; z-index: 5; min-width: 13rem; max-width: 18rem; max-height: calc(100% - 16px); overflow-y: auto; overscroll-behavior: contain; padding: 0.35rem; background: var(--bg-soft); border: 1px solid var(--border); border-radius: 0.6rem; box-shadow: 0 10px 28px rgba(0, 0, 0, 0.45); line-height: 1.3; }
  .pin-menu:focus { outline: none; }
  .pin-menu-head { display: flex; align-items: center; justify-content: space-between; gap: 0.4rem; }
  .pin-menu-title { font-size: 0.68rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); padding: 0.3rem 0.5rem; }
  .pin-menu .pm-close { width: 1.7rem; height: 1.7rem; padding: 0; border-radius: 0.45rem; background: transparent; color: var(--muted); font-size: 1.1rem; line-height: 1; }
  .pin-menu .pm-close:hover, .pin-menu .pm-close:focus-visible { background: var(--surface-strong); color: var(--text); outline: none; transform: none; }
  .pin-menu .pm-item { display: flex; width: 100%; justify-content: space-between; align-items: center; gap: 0.6rem; padding: 0.4rem 0.5rem; border-radius: 0.45rem; background: transparent; color: var(--text); font-weight: 600; font-size: 0.86rem; text-align: left; }
  .pin-menu .pm-item:hover, .pin-menu .pm-item:focus-visible { background: var(--surface-strong); outline: none; transform: none; }
  .pin-menu .pm-item.danger { color: var(--error-text); }
  .pin-menu .kind { font-size: 0.72rem; font-weight: 500; color: var(--muted); }
  .pin-menu hr { border: 0; border-top: 1px solid var(--border-soft); margin: 0.3rem 0; }
  .pin-menu.form { width: 18rem; max-width: calc(100% - 16px); }
  /* On a picture under 480 x 360 CSS px: a panel under the picture, in full screen a sheet at the shell's bottom. */
  .pin-menu.as-panel { position: static; width: auto; min-width: 0; max-width: none; max-height: none; overflow: visible; margin-top: 0.5rem; box-shadow: none; }
  .pin-menu.as-sheet { position: absolute; left: 0; right: 0; bottom: 0; z-index: 7; max-height: 50%; overflow-y: auto; margin: 0; border-width: 1px 0 0; border-radius: 0.8rem 0.8rem 0 0; padding-bottom: calc(0.35rem + env(safe-area-inset-bottom)); }
  .pm-form { padding: 0 0.5rem 0.4rem; }
  .pm-form .field { min-width: 0; margin-bottom: 0.5rem; }
  .pm-form label { font-size: 0.66rem; }
  .pm-form input, .pm-form select { padding: 0.42rem 0.6rem; font-size: 0.88rem; border-radius: 0.55rem; }
  .pm-pair { display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem; }
  .pm-actions { display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 0.2rem; }
  /* The full-screen picture with its one-line toolbar. */
  .fs-btn { position: absolute; top: 8px; right: 8px; z-index: 4; width: 2.2rem; height: 2.2rem; padding: 0; display: flex; align-items: center; justify-content: center; border-radius: 0.5rem; background: color-mix(in srgb, var(--bg-deep) 72%, transparent); border: 1px solid var(--border); color: var(--text); }
  .fs-btn:hover, .fs-btn:focus-visible { background: var(--bg-soft); border-color: var(--accent); transform: none; }
  .fs-btn svg { width: 1.15rem; height: 1.15rem; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
  .fs-shell { position: fixed; inset: 0; z-index: 50; display: flex; flex-direction: column; background: var(--bg-deep); }
  .fs-shell[hidden], .wizard-solved-params[hidden], .metric-echo[hidden], .tier-list[hidden], button[hidden] { display: none; }
  .fs-bar { display: flex; align-items: center; gap: 0.75rem; min-height: 2.6rem; padding: 0.35rem 0.6rem 0.35rem 0.9rem; background: var(--bg-soft); border-bottom: 1px solid var(--border); font-size: 0.86rem; line-height: 1.3; }
  .fs-title { font-size: 0.68rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); white-space: nowrap; }
  .fs-hint { flex: 1; min-width: 0; color: var(--text); font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .fs-status { color: var(--muted); white-space: nowrap; font-variant-numeric: tabular-nums; }
  .fs-status.warn { color: var(--caution-text); }
  .fs-actions { display: flex; gap: 0.4rem; }
  .fs-stage { flex: 1; min-height: 0; display: flex; align-items: center; justify-content: center; overflow: hidden; }
  .fs-stage .wizard-preview-container { margin: 0; border: 0; border-radius: 0; flex: none; }
  .fs-stage .fs-btn { display: none; }
  .fs-help { position: absolute; top: 3.2rem; right: 0.6rem; z-index: 6; width: min(24rem, calc(100% - 1.2rem)); max-height: calc(100% - 4rem); overflow-y: auto; padding: 0.4rem 0.9rem 0.8rem; background: var(--bg-soft); border: 1px solid var(--border); border-radius: 0.6rem; box-shadow: 0 10px 28px rgba(0, 0, 0, 0.45); font-size: 0.86rem; line-height: 1.4; }
  .fs-help[hidden] { display: none; }
  .fs-help-head { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; }
  .fs-help-head h3 { margin: 0.35rem 0; font-size: 0.95rem; }
  .fs-help p { margin: 0 0 0.6rem; color: var(--muted); }
  .fs-help p strong { color: var(--text); }
  .fs-keys td { padding: 0.3rem 0; border-top: 1px solid var(--border-soft); border-bottom: 0; vertical-align: top; }
  .fs-keys td:first-child { white-space: nowrap; padding-right: 0.75rem; }
  .fs-keys kbd { display: inline-block; min-width: 1.4rem; padding: 0 0.35rem; border: 1px solid var(--border); border-bottom-width: 2px; border-radius: 0.35rem; background: var(--bg-deep); font: inherit; font-size: 0.8rem; text-align: center; }
  /* A disclosure that is a group's whole content takes the group's own frame. */
  .group > .inline-advanced:first-child { margin-top: 0; padding-top: 0; border-top: 0; }
  @media (max-width: 680px) {
    .wizard-step-btn { padding: 0.35rem 0.5rem; font-size: 0.72rem; }
  }
  @media (max-width: 640px) { .fs-title, .fs-status { display: none; } }
</style>

<div class="wizard-header">
  <a href="/">&larr; Back to Settings</a>
  <h2>Setup Wizard</h2>
</div>

% # The order is published to the page script as WIZ (key -> index), so no
% # button or script carries a step number.
% _steps = [('video', 'Video Source'), ('lens', 'Lens'), ('calib', 'Camera Calibration'), ('review', 'Review')]
% _keys_js = ', '.join("'" + k + "'" for k, _ in _steps)
<script>
  window.WIZ = {
% for _i, (_key, _label) in enumerate(_steps):
    {{_key}}: {{_i}},
% end
  };
  window.WIZ_STEPS = [{{!_keys_js}}];
</script>
<nav class="wizard-steps" aria-label="Setup Wizard steps">
% for _i, (_key, _label) in enumerate(_steps):
  <button type="button" class="wizard-step-btn" data-step="{{_i}}" onclick="wizardGo({{_i}})">{{_i + 1}}. {{_label}}</button>
% end
</nav>

%# The four-corner icon of the full-screen button on every picture.
% _fs_icon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5"/></svg>'

<!-- Video Source -->
<div class="wizard-content" id="wizard-step-video">
  <div class="section">
    <div class="section-head">
      <h2 tabindex="-1">Video Source</h2>
      <span class="section-note">Select and configure the camera input</span>
      <div class="section-actions"><button type="button" class="section-help-btn" data-help-open="wizard-video" aria-label="Open help for this section">?</button></div>
    </div>

    % include('partials/video_source_failure.tpl')

    <div class="group">
      <div class="row">
        <div class="field">
          <label for="wizard-video-source-type">Source Type</label>
          <select id="wizard-video-source-type"
                  onchange="document.querySelectorAll('[data-wizard-input-type]').forEach(function(el){el.style.display=el.dataset.wizardInputType===this.value?'':'none';}.bind(this));">
            % for iid, iname in available_inputs:
            <option value="{{iid}}" {{'selected' if config.video_source_type == iid else ''}}>{{iname}}</option>
            % end
          </select>
        </div>
      </div>
      % for iid, html_fragment in input_html_fragments.items():
      <div data-wizard-input-type="{{iid}}" style="display:{{'none' if config.video_source_type != iid else ''}}">
        {{!html_fragment}}
      </div>
      % end
    </div>

    <p class="wizard-help">
      Choose the video input that matches your camera setup. The change is applied at once. The new video input starts about 1 s after you save.
    </p>

    <div id="wizard-video-saved" class="notice success" role="status" style="display:none"></div>

    <div class="wizard-nav">
      <span class="spacer"></span>
      <button type="button" class="secondary" onclick="saveWizardVideoSource().catch(wizardVideoSaveFailed)">Save</button>
      <button type="button" class="save-btn" onclick="saveWizardVideoSource().then(function(){ wizardNext(); }).catch(wizardVideoSaveFailed)">Save &amp; Next</button>
    </div>
  </div>
</div>

<!-- Lens: the distortion from lines that are straight in reality -->
<div class="wizard-content" id="wizard-step-lens">
  <div class="section">
    <div class="section-head">
      <h2 tabindex="-1">Lens</h2>
      <span class="section-note">Measure the lens from edges that are straight in reality</span>
      <div class="section-actions"><button type="button" class="section-help-btn" data-help-open="wizard-lens" aria-label="Open help for this section">?</button></div>
    </div>

    <p class="wizard-help" id="lens-intro">Most camera lenses bend the picture: edges that are straight on stage look curved, most of all near the edges of the picture. This step measures how much your lens bends it, so that the grid and the markers sit exactly on the stage everywhere in the picture. Do it once for each camera and lens, and again after you change the zoom.</p>
    <div class="lens-current" id="lens-current" hidden>
      <div class="lens-dlg-title" id="lens-current-q">This camera has a lens calibration</div>
      <p class="lens-current-values" id="lens-current-values"></p>
      <div class="tier-list" role="radiogroup" aria-labelledby="lens-current-q">
        <label class="tier-option"><input type="radio" name="lens_current" value="keep"><span><strong>Keep the current calibration</strong><small>Choose this if the camera, the lens and the zoom have not changed.</small></span></label>
        <label class="tier-option"><input type="radio" name="lens_current" value="clear"><span><strong>Clear it and measure the lens again</strong><small>Trace new lines to calibrate the lens.</small></span></label>
        <label class="tier-option"><input type="radio" name="lens_current" value="load"><span><strong>Load another lens calibration</strong><small>Saved on this station or in a settings file.</small></span></label>
      </div>
      <div id="lens-current-backup">
        <button type="button" class="secondary" style="margin-top:0.72rem;" onclick="lensSaveCurrent()">Save lens calibration&hellip;</button>
        <div id="lens-current-saved" class="notice success" role="status" style="display:none;margin-top:0.72rem;"></div>
      </div>
    </div>
    <div id="lens-notices">
    <div class="notice" id="lens-hint" role="status" aria-live="polite" hidden></div>
    <div id="lens-result" class="notice" role="status" style="display:none;margin-top:0.5rem;">
      <div class="lens-result-main">
        <span class="wizard-meter" aria-hidden="true"><span class="wizard-meter-seg"></span><span class="wizard-meter-seg"></span><span class="wizard-meter-seg"></span><span class="wizard-meter-seg"></span><span class="wizard-meter-seg"></span></span>
        <span id="lens-result-text"></span>
      </div>
      <div class="notice-sub" id="lens-result-hint"></div>
    </div>
    </div>
    <div id="lens-work">
    <div id="lens-container" class="wizard-preview-container" style="display:none;">
      <img id="lens-image" alt="">
      <canvas id="lens-edges" class="lens-edges" aria-hidden="true"></canvas>
      <svg id="lens-overlay" class="wizard-overlay" tabindex="-1" aria-label="Camera snapshot" xmlns="http://www.w3.org/2000/svg">
        <rect id="lens-hit" class="lens-hit" x="0" y="0" width="100%" height="100%" fill="transparent"/>
        <g id="lens-candidates"></g>
        <g id="lens-lines"></g>
        <g id="lens-pending"></g>
      </svg>
      <svg id="lens-tails" class="lens-tails" aria-hidden="true"></svg>
      <div id="lens-bubbles" class="lens-bubbles"></div>
      <div class="wizard-loupe" aria-hidden="true"><canvas width="240" height="240"></canvas></div>
      <button type="button" class="fs-btn" data-fs="lens" aria-label="Full screen">{{!_fs_icon}}</button>
    </div>
    <div id="lens-no-feed" class="wizard-no-feed" style="display:none;">No video feed available. Configure a video source in the Video Source step, then return here and press <strong>Refresh Image</strong>.</div>

    <div id="lens-notice" class="notice" role="status" style="display:none;"></div>
    <div id="lens-status" class="wizard-status" role="alert" style="display:none;"></div>

    <div style="margin-top:0.72rem;display:flex;gap:0.5rem;flex-wrap:wrap;">
      <button type="button" class="secondary" onclick="lensRefreshImage()">Refresh Image</button>
      <button type="button" class="secondary" id="lens-delete-line" onclick="lensDeleteSelectedLine()" disabled>Delete line</button>
      <button type="button" class="secondary" id="lens-clear" onclick="lensClearLines()" disabled>Clear lines</button>
      <button type="button" class="secondary" onclick="lensLoadDialog()">Load lens calibration&hellip;</button>
      <button type="button" class="secondary" id="lens-save" onclick="lensSaveDialog()" disabled>Save lens calibration&hellip;</button>
    </div>
    <div id="lens-saved" class="notice success" role="status" style="display:none;margin-top:0.72rem;"></div>
% if config.ui.developer_mode and _suggest_on:
    <div class="field checkbox-field lens-dev">
      <label for="lens-show-edges">Edge map</label>
      <div class="checkbox-wrap checkbox-field inline">
        <input type="checkbox" id="lens-show-edges" onchange="lensToggleEdgeView(this.checked)">
        <label for="lens-show-edges">Show edges</label>
      </div>
    </div>
% end

    <div class="group" style="margin-top:0.72rem;">
      <details class="inline-advanced" id="lens-adjust">
        <summary>User input (optional)</summary>
        <div class="inline-advanced-content">
          <div class="row">
            <div class="field" style="flex:1;min-width:200px;">
              <label for="wiz_lens_k1">Barrel / fisheye</label>
              <div style="display:flex;gap:0.5rem;align-items:center;">
                <input type="range" id="wiz_lens_k1_range" min="-1" max="0.5" step="0.005"
                       value="{{_lens_k1}}" style="flex:1;" oninput="onWizardLensInput('k1', 'range')"
                       aria-label="Barrel / fisheye slider">
                <input type="number" id="wiz_lens_k1" step="any"
                       value="{{_lens_k1}}" style="width:6rem;" oninput="onWizardLensInput('k1', 'number')">
              </div>
            </div>
            <div class="field" style="flex:1;min-width:200px;">
              <label for="wiz_lens_k2">Corner correction</label>
              <div style="display:flex;gap:0.5rem;align-items:center;">
                <input type="range" id="wiz_lens_k2_range" min="-0.8" max="0.3" step="0.005"
                       value="{{_lens_k2}}" style="flex:1;" oninput="onWizardLensInput('k2', 'range')"
                       aria-label="Corner correction slider">
                <input type="number" id="wiz_lens_k2" step="any"
                       value="{{_lens_k2}}" style="width:6rem;" oninput="onWizardLensInput('k2', 'number')">
              </div>
            </div>
          </div>
          <div id="wiz-lens-error" class="wizard-field-error" role="alert" style="display:none;">With these two values, the overlay cannot follow the picture out to its corners. Set one of the values closer to 0.</div>
          <div class="lens-centre" role="group" aria-labelledby="wiz-lens-centre-title">
            <div class="lens-centre-title" id="wiz-lens-centre-title">Lens centre</div>
            <div class="row">
              <div class="field" style="flex:1;min-width:200px;">
                <label for="wiz_lens_cx">Right of the picture centre (px)</label>
                <input type="number" id="wiz_lens_cx" step="1" value="0" style="width:6rem;" aria-describedby="wiz-lens-centre-error"
                       oninput="onWizardLensCentreInput(this)" onchange="onWizardLensCentreSettled()" onblur="onWizardLensCentreSettled()">
              </div>
              <div class="field" style="flex:1;min-width:200px;">
                <label for="wiz_lens_cy">Above the picture centre (px)</label>
                <input type="number" id="wiz_lens_cy" step="1" value="0" style="width:6rem;" aria-describedby="wiz-lens-centre-error"
                       oninput="onWizardLensCentreInput(this)" onchange="onWizardLensCentreSettled()" onblur="onWizardLensCentreSettled()">
              </div>
            </div>
          </div>
          <div id="wiz-lens-centre-error" class="wizard-field-error" role="alert" style="display:none;"></div>
        </div>
      </details>
    </div>

    <p class="wizard-tip">Click a point or press <strong>Tab</strong> to select it. The loupe stays on it, and the <strong>arrow keys</strong> move it by one pixel. While a point is selected, <strong>Tab</strong> goes round the points of its line. Press <strong>Enter</strong> when the crosshair is in the right place; then <strong>Tab</strong> moves on to the next line. <strong>Esc</strong> or a click somewhere else ends the selection. <strong>Delete</strong> or <strong>Backspace</strong> removes the selected line. <strong>Esc</strong> cancels a line you have started.</p>
    </div>

    <div class="wizard-nav">
      <button type="button" class="secondary" onclick="wizardPrev()">Back</button>
      <button type="button" class="save-btn" id="lens-next" onclick="wizardNext()">Next</button>
    </div>
  </div>
</div>

<!-- Camera Calibration -->
<div class="wizard-content" id="wizard-step-calib">
  <div class="section" id="step-calib">
    <div class="section-head">
      <h2 tabindex="-1">Camera Calibration</h2>
      <span class="section-note"><span class="only-simple">Drag four corners onto their tape marks</span><span class="only-advanced">Place measured points on the picture</span></span>
      <div class="section-actions"><button type="button" class="secondary small" id="unit-switch">Switch units to {{'Metric' if _imp else 'Imperial'}}</button><button type="button" class="section-help-btn" id="calib-help-btn" data-help-open="wizard-calibration-simple" aria-label="Open help for this section">?</button></div>
    </div>

    <div class="mode-field">
      <label id="cal-mode-label">Calibration</label>
      <div class="seg-toggle" role="radiogroup" aria-labelledby="cal-mode-label">
        <label class="seg-option">
          <input type="radio" name="cal_mode" value="simple" checked>
          <span><strong>Simple</strong><small>Four corners of a rectangle</small></span>
        </label>
        <label class="seg-option">
          <input type="radio" name="cal_mode" value="advanced">
          <span><strong>Advanced</strong><small>Known points and triangulated points</small></span>
        </label>
      </div>
    </div>

    <!-- Simple -->
    <div id="cal-simple">
      <div id="hint-slot-sloc"></div>
      <div class="simple-step" id="sstep-1">
        <div class="group">
          <p class="wizard-help" id="simple-loc-q"><strong>1. Where is your camera?</strong></p>
          <div class="row">
            <div class="field wide"><select id="simple-loc" aria-labelledby="simple-loc-q"></select></div>
          </div>
          <details class="inline-advanced" id="simple-cam-box">
            <summary>Optional: enter a rough camera position to check the result</summary>
            <div class="inline-advanced-content" id="cam-slot-simple"></div>
          </details>
        </div>
      </div>
      <div id="hint-slot-rect"></div>
      <div class="simple-step" id="sstep-2">
        <p class="wizard-help" id="rect-q"><strong>2. What is the size of your rectangle?</strong></p>
        <p class="wizard-help">Mark the four corners of the <strong>largest rectangle</strong> that fits on the floor of the performance area. The camera must see all four corners. Only the corners need a mark, such as a tape cross. Enter the size of the rectangle.</p>
        <div class="group">
          <div class="row" role="group" aria-labelledby="rect-q">
            <div class="field"><label for="rect_w">Width (<span class="u-len">{{_len}}</span>)</label><input type="{{_len_type}}"{{!_len_step}} min="0" id="rect_w" data-len><small class="metric-echo" data-echo="rect_w" hidden></small></div>
            <div class="field"><label for="rect_d">Depth (<span class="u-len">{{_len}}</span>)</label><input type="{{_len_type}}"{{!_len_step}} min="0" id="rect_d" data-len><small class="metric-echo" data-echo="rect_d" hidden></small></div>
          </div>
          <p class="wizard-help" id="rect-diag"></p>
        </div>
      </div>
      <div id="hint-slot-origin-q"></div>
      <div class="simple-step" id="sstep-3">
      <p class="wizard-help" id="origin-q"><strong>3. Where is the point of origin?</strong></p>
      <div class="group">
        <div class="row">
          <div class="field wide">
            <label for="rect_origin">Point of origin for all position data (0/0/0)</label>
            <select id="rect_origin">
              <option value="" selected>Choose…</option>
              <option value="dsc">Default: centred between DSL and DSR</option>
              <option value="custom">Custom</option>
            </select>
          </div>
        </div>
        <div id="rect-custom" hidden>
          <div id="hint-slot-origin"></div><p class="wizard-help" id="rect-custom-q"><strong>Where is the centre of the rectangle, measured from the point of origin?</strong></p>
          <div class="row" role="group" aria-labelledby="rect-custom-q">
            <div class="field"><label for="rect_cx">X (<span class="u-len">{{_len}}</span>) <span class="axis-note">(stage left +)</span></label><input type="{{_len_type}}"{{!_len_step}} id="rect_cx" data-len data-ph="0"><small class="metric-echo" data-echo="rect_cx" hidden></small></div>
            <div class="field"><label for="rect_cy">Y (<span class="u-len">{{_len}}</span>) <span class="axis-note">(upstage +)</span></label><input type="{{_len_type}}"{{!_len_step}} id="rect_cy" data-len data-ph="0"><small class="metric-echo" data-echo="rect_cy" hidden></small></div>
            <div class="field"><label for="rect_z">Z (<span class="u-len">{{_len}}</span>) <span class="axis-note">(up +)</span></label><input type="{{_len_type}}"{{!_len_step}} id="rect_z" data-len data-ph="0"><small class="metric-echo" data-echo="rect_z" hidden></small></div>
          </div>
        </div>
      </div>
      </div>
    </div>

    <!-- Advanced -->
    <div id="cal-advanced" hidden>
      <div id="hint-slot-cam"><div class="notice" id="wiz-hint" role="status" aria-live="polite"></div></div>
      <div id="cam-slot-adv-top">
        <div class="group" id="cal-camera">
          <div class="group-title" id="cam-title">Camera Position</div>
          <p class="wizard-help" id="cam-intro"></p>
          <div class="row">
            <div class="field">
              <label for="cam_pos_x">Pos X (<span class="u-len">{{_len}}</span>) <span class="axis-note">(stage left +)</span></label>
              <input type="{{_len_type}}"{{!_len_step}} id="cam_pos_x" data-len data-ph="0">
              <small class="metric-echo" data-echo="cam_pos_x" hidden></small>
            </div>
            <div class="field">
              <label for="cam_pos_y">Pos Y (<span class="u-len">{{_len}}</span>) <span class="axis-note">(upstage +)</span></label>
              <input type="{{_len_type}}"{{!_len_step}} id="cam_pos_y" data-len data-ph="0">
              <small class="metric-echo" data-echo="cam_pos_y" hidden></small>
            </div>
            <div class="field">
              <label for="cam_pos_z">Pos Z (<span class="u-len">{{_len}}</span>) <span class="axis-note">(height)</span></label>
              <input type="{{_len_type}}"{{!_len_step}} id="cam_pos_z" data-len data-ph="0">
              <small class="metric-echo" data-echo="cam_pos_z" hidden></small>
            </div>
          </div>
          <div id="cam-error" class="wizard-field-error" role="alert" hidden></div>
        </div>
      </div>
    </div>

    <p class="wizard-help only-simple" id="corners-q"><strong>4. Place the corners on the picture</strong></p>
    <div id="hint-slot-image"></div>
    <div id="cal-container" class="wizard-preview-container" style="display:none;">
      <img id="cal-image" alt="">
      <svg id="cal-overlay" class="wizard-overlay" tabindex="-1" aria-label="Camera snapshot" xmlns="http://www.w3.org/2000/svg">
        <rect id="cal-hit" class="cal-hit" x="0" y="0" width="100%" height="100%" fill="transparent"/>
        <g id="cal-grid"></g>
        <g id="cal-dist"></g>
        <g id="cal-pins"></g>
      </svg>
      <div class="wizard-loupe" aria-hidden="true"><canvas width="240" height="240"></canvas></div>
      <div class="pin-menu" id="pin-menu" role="group" aria-label="Which point is here?" tabindex="-1" hidden></div>
      <button type="button" class="fs-btn" data-fs="cal" aria-label="Full screen">{{!_fs_icon}}</button>
    </div>
    <div id="pin-menu-slot"></div>
    <div id="cal-no-feed" class="wizard-no-feed" style="display:none;">No video feed available. Configure a video source in the Video Source step, then return here and press <strong>Refresh Image</strong>.</div>
    <div style="display:flex;gap:0.5rem;flex-wrap:wrap;">
      <button type="button" class="secondary" id="cal-refresh" onclick="loadSnapshot(0, true)">Refresh Image</button>
      <button type="button" class="secondary" id="cal-reset" onclick="calibResetPins()"><span class="only-simple">Reset Corners</span><span class="only-advanced">Reset Pins</span></button>
    </div>
    <div id="adv-need" class="wizard-status waiting" role="status" hidden style="margin-top:0.72rem;"></div>
    <div id="cal-refreshed" class="notice success" role="status" hidden style="margin-top:0.72rem;">New snapshot loaded. The <span class="only-simple">corners stay</span><span class="only-advanced">pins stay</span> where they are.</div>
    <p class="wizard-tip only-simple">Drag each corner onto its tape mark. The loupe shows the picture 4× larger. Click a corner or press <strong>Tab</strong> to select it. The loupe stays on it, and the <strong>arrow keys</strong> move it by one pixel. Press <strong>Enter</strong> when the crosshair is in the right place. <strong>Esc</strong> or a click somewhere else ends the selection.</p>
    <p class="wizard-tip only-advanced">Click the picture where a point is, and choose the point from the menu. Or create a <strong>New known point</strong> or a <strong>New triangulated point</strong> there. Click a pin to remove the pin or delete the point. While a menu is open, the loupe stays on the spot, and the <strong>arrow keys</strong> move the spot by one pixel. Drag a pin to correct it.</p>

    <div id="cal-advanced-points" hidden>
      <div class="group">
        <div class="group-title">Points</div>
        <div id="adv-rows"></div>
        <div style="display:flex;gap:0.5rem;flex-wrap:wrap;margin-top:0.6rem;">
          <button type="button" class="secondary" id="adv-add-known" onclick="calibAddPoint('known')">Add known point</button>
          <button type="button" class="secondary" id="adv-add-tri" onclick="calibAddPoint('triangulated')" hidden>Add triangulated point</button>
        </div>
      </div>
    </div>
    <div id="cal-simple-corners">
      <div class="group">
        <div class="group-title">Corners</div>
        <div id="corner-rows"></div>
      </div>
    </div>
    <div id="cal-status" class="wizard-status" role="status" hidden></div>
    <div id="cal-caution" class="notice warning" role="status" hidden style="margin-top:0.72rem;"></div>
    <div class="group" id="cal-height" hidden style="margin-top:0.72rem;">
      <div class="notice" id="cal-height-ask" role="status">Enter the exact height of the camera's lens<div class="notice-sub">The camera looks almost straight down – its height and its field of view cannot be told apart.</div></div>
      <div class="row">
        <div class="field">
          <label for="cam_height">Camera height (<span class="u-len">{{_len}}</span>) <span class="axis-note">(Z from the point of origin)</span></label>
          <input type="{{_len_type}}"{{!_len_step}} id="cam_height" data-len aria-describedby="cam-height-error" aria-invalid="false">
          <small class="metric-echo" data-echo="cam_height" hidden></small>
          <div id="cam-height-error" class="wizard-field-error" hidden></div>
        </div>
      </div>
      <div class="notice" id="cal-height-note" role="status" hidden></div>
    </div>
    <div id="cal-lens-note" class="notice" role="status" hidden style="margin-top:0.72rem;">Using the station's lens. It was not measured in this run.</div>
    <div id="cal-solved" class="wizard-solved-params" hidden></div>

    <div class="group" id="cal-dist-group" hidden style="margin-top:0.9rem;">
      <div class="group-title">Distances to check</div>
      <p class="wizard-help">Measure these distances between the marks on the stage. If a distance is different, a typed or measured value is wrong. The distances from a triangulated point to the two points it is measured from are not listed.</p>
      <div class="dist-wrap"><table class="data-table dist-table" id="cal-dist-table"></table></div>
    </div>

    <div class="wizard-nav">
      <button type="button" class="secondary" onclick="wizardPrev()">Back</button>
      <button type="button" class="save-btn" id="cal-next" onclick="calibNext()">Next</button>
    </div>
  </div>
</div>

<!-- Review -->
<div class="wizard-content" id="wizard-step-review">
  <div class="section">
    <div class="section-head">
      <h2 tabindex="-1">Review</h2>
      <span class="section-note">Check all values before saving</span>
      <div class="section-actions"><button type="button" class="section-help-btn" data-help-open="wizard-review" aria-label="Open help for this section">?</button></div>
    </div>

    <div id="review-container" class="wizard-preview-container" style="display:none;">
      <img id="review-image" alt="">
      <svg id="review-overlay" class="wizard-overlay" role="img" aria-label="Camera snapshot" xmlns="http://www.w3.org/2000/svg">
        <g id="review-grid"></g>
        <g id="review-pins"></g>
      </svg>
      <button type="button" class="fs-btn" data-fs="review" aria-label="Full screen">{{!_fs_icon}}</button>
    </div>
    <div id="review-no-feed" class="wizard-no-feed" style="display:none;">No video feed available, so the visual review is unavailable. The camera is calculated from a snapshot: configure a video source in the Video Source step, then return here.</div>

    <div id="review-status" class="notice error" role="alert" hidden></div>
    <div id="review-incomplete" class="notice error" role="alert" hidden></div>
    <div id="review-caution" class="notice warning" role="status" hidden></div>
    <div id="review-lens-note" class="notice" role="status" hidden>Using the station's lens. It was not measured in this run.</div>
    <div class="group">
      <div class="group-title">Camera</div>
      <div class="wizard-solved-params" id="review-camera"></div>
    </div>
    <div class="group">
      <div class="group-title">Lens</div>
      <div class="wizard-solved-params" id="review-lens"></div>
    </div>
    <div class="group">
      <div class="group-title">Calibration</div>
      <div class="wizard-solved-params" id="review-calib"></div>
    </div>
    <div class="group" id="review-dist-group" hidden>
      <div class="group-title">Distances to check</div>
      <div class="dist-wrap"><table class="data-table dist-table" id="review-dist-table"></table></div>
    </div>
    <div class="group">
      <div class="group-title">Grid</div>
      <div class="wizard-solved-params" id="review-grid-params"></div>
    </div>

    <div class="wizard-nav">
      <button type="button" class="secondary" onclick="wizardPrev()">Back</button>
      <span class="spacer"></span>
      <button type="button" class="btn-danger" onclick="discardAndLeave()">Leave without saving</button>
      <button type="button" class="save-btn" onclick="applyAndFinish()" id="btn-apply-finish">Save and finish</button>
    </div>
  </div>
</div>

<div id="fs-shell" class="fs-shell" hidden role="dialog" aria-modal="true" aria-labelledby="fs-title">
  <div class="fs-bar">
    <span class="fs-title" id="fs-title"></span>
    <span class="fs-hint" id="fs-hint" aria-live="polite"></span>
    <span class="fs-status" id="fs-status"></span>
    <span class="fs-actions" id="fs-actions"></span>
    <button type="button" class="section-help-btn" id="fs-help-btn" aria-label="How to use full screen" aria-expanded="false" aria-controls="fs-help">?</button>
    <button type="button" class="secondary small" id="fs-exit">Exit full screen</button>
  </div>
  <div class="fs-help" id="fs-help" role="dialog" aria-modal="false" aria-labelledby="fs-help-title" hidden></div>
  <div class="fs-stage" id="fs-stage"></div>
</div>

<script>
  window.WIZ_SAVED = {{!json.dumps(_saved)}};
</script>

<script>
(function() {
  'use strict';

  var WUNIT = window.OpenFollow.units;
  var SAVED = window.WIZ_SAVED;
  // Names this page to the solve route, which stops a fit once the page asks for a newer one.
  var WIZ_SOLVE_PAGE = Math.random().toString(36).slice(2, 12) || 'page';
  var STORAGE_KEY = 'openfollow:wizard';
  var currentStep = 0;
  var snapshotUrl = null;
  var imageWidth = 0;
  var imageHeight = 0;

  // ---------------------------------------------------------------
  // Lengths: fields hold text in the active unit system, the model holds metres.
  // ---------------------------------------------------------------
  // ft / in text is rounded to 0.01 in, so text the page wrote reads back as the metres it was written from.
  var exactLen = {};
  function len(text) {
    var t = String(text == null ? '' : text).trim();
    if (WUNIT.isImperial() && Object.prototype.hasOwnProperty.call(exactLen, t)) return exactLen[t];
    return WUNIT.parseLength(t);
  }
  function lenField(m) {
    if (!WUNIT.isImperial()) return String(Math.round(m * 1000) / 1000);
    var t = WUNIT.formatLength(m);
    exactLen[t] = m;
    return t;
  }
  // Signs the rounded value, so a value just below zero reads 0.0, not −0.0.
  function fmt(v, d) { var a = Math.abs(v).toFixed(d); return (v < 0 && Number(a) !== 0 ? '−' : '') + a; }
  function lenText(m, d) { return WUNIT.isImperial() ? (m < 0 ? '−' : '') + WUNIT.formatLength(Math.abs(m)) : fmt(m, d) + ' m'; }
  function lenRough(m) { return WUNIT.isImperial() ? (m / 0.3048).toFixed(1) + ' ft' : m.toFixed(1) + ' m'; }
  function lenSmall(m) { return WUNIT.isImperial() ? (m / 0.0254).toFixed(1) + ' in' : Math.round(m * 100) + ' cm'; }
  function lenLimit() { return WUNIT.isImperial() ? '656 ft' : '200 m'; }
  function lenSuffix() { return WUNIT.unitSuffixLength(); }
  function esc(s) { return String(s).replace(/[&<>"]/g, function(c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function $(id) { return document.getElementById(id); }
  // A live region is rewritten only when its words change, so a redraw is not read out again; `force` rewrites it anyway.
  function setLive(el, html, force) { if (force || el.liveHtml !== html) { el.liveHtml = html; el.innerHTML = html; } }
  // Hidden, a region forgets its words: shown again with the same words, it is read out again.
  function showLive(el, html, on) { el.hidden = !on; if (on) setLive(el, html); else el.liveHtml = null; }

  // The lens: Barrel / fisheye and Corner correction in their fields, the lens centre in S.lensCentre (half-diagonals
  // from the picture centre, x right, y down), shown in whole pixels of the snapshot. Mirrors openfollow.lens_model.
  var LENS_C_BOUND = {{C_BOUND}};
  // Smallest positive root of 1 + b s + a s^2, Infinity when none.
  function wizLensRoot(a, b) {
    if (a === 0) return b < 0 ? -1 / b : Infinity;
    var disc = b * b - 4 * a;
    if (disc < 0) return Infinity;
    // The q form keeps both roots exact when a is tiny against b.
    var q = -0.5 * (b + (b < 0 ? -1 : 1) * Math.sqrt(disc));
    var s = Infinity;
    [q / a, 1 / q].forEach(function(r) { if (r > 0 && r < s) s = r; });
    return s;
  }
  // Screen radius about the lens centre where the map stops rising: its pole or its fold.
  function wizLensLimitRadius(k1, k2) { return Math.sqrt(Math.min(wizLensRoot(k2, k1), wizLensRoot(-3 * k2, -k1))); }
  // Mirror lens_model.lens_warp_is_valid: the corner farthest from the lens centre is at most 1 + |c| from it.
  function wizLensIsValid(k1, k2, cx, cy) {
    if (![k1, k2, cx, cy].every(function(v) { return typeof v === 'number' && isFinite(v); })) return false;
    if (Math.max(Math.abs(k1), Math.abs(k2)) > 1e3 || Math.abs(cx) > LENS_C_BOUND || Math.abs(cy) > LENS_C_BOUND) return false;
    return wizLensLimitRadius(k1, k2) > 1 + Math.hypot(cx, cy);
  }
  // A fitted pair as the fields show it: four decimals, unless rounding tips a pair fitted against the limit over it.
  function wizLensShownPair(k1, k2, cx, cy) {
    var r1 = k1.toFixed(4), r2 = k2.toFixed(4);
    return wizLensIsValid(Number(r1), Number(r2), cx, cy) ? [r1, r2] : [String(k1), String(k2)];
  }
  // [k1, k2, cx, cy] in use.
  function wizLens() { return [wizReadLensCoeff('wiz_lens_k1'), wizReadLensCoeff('wiz_lens_k2'), S.lensCentre[0], S.lensCentre[1]]; }
  // False while the centre fields show a centre not in use, so nothing saves a centre the fields do not show.
  function wizLensValid() { var l = wizLens(); return !S.lensCentreBad && wizLensIsValid(l[0], l[1], l[2], l[3]); }

  function wizReadLensCoeff(id) {
    var el = document.getElementById(id);
    if (!el) return 0;
    var v = parseFloat(el.value);
    return isFinite(v) ? v : 0;
  }

  function wizWriteLensCoeff(id, value) {
    var num = document.getElementById(id);
    if (num) num.value = value;
    wizSyncLensRange(id, value);
  }
  // The slider follows a value that reads as a number; one past its span widens it, so touching the slider never
  // pulls the value back.
  function wizSyncLensRange(id, value) {
    var range = document.getElementById(id + '_range'), v = parseFloat(value);
    if (!range || !isFinite(v)) return;
    range.min = Math.min(parseFloat(range.min), v);
    range.max = Math.max(parseFloat(range.max), v);
    range.value = v;
  }

  // The lens centre in pixels of a w x h picture: right of and above its centre, in whole pixels within the fields'
  // bound, which a centre at the bound can round past.
  function wizHalfDiag(w, h) { return 0.5 * Math.sqrt(w * w + h * h); }
  function wizLensCentrePxMax(w, h) { return Math.floor(LENS_C_BOUND * wizHalfDiag(w, h)); }
  function wizLensCentrePx(c, w, h) {
    var d = wizHalfDiag(w, h), max = wizLensCentrePxMax(w, h);
    var px = function(v) { return Math.max(-max, Math.min(max, Math.round(v * d))) || 0; };
    return [px(c[0]), px(-c[1])];
  }
  // "42 px right, 18 px up"; an axis that rounds to 0 px is left out, a centre at 0 (or no picture size) says nothing.
  function wizLensCentreText(c, w, h) {
    if (!(w > 0 && h > 0)) return '';
    var px = wizLensCentrePx(c, w, h), parts = [];
    if (px[0]) parts.push(Math.abs(px[0]) + ' px ' + (px[0] > 0 ? 'right' : 'left'));
    if (px[1]) parts.push(Math.abs(px[1]) + ' px ' + (px[1] > 0 ? 'up' : 'down'));
    return parts.join(', ');
  }
  // A centre field's text as whole pixels within max, else NaN.
  function wizLensCentreField(t, max) {
    var s = String(t).trim(), v = s === '' ? NaN : Number(s);
    return Number.isInteger(v) && Math.abs(v) <= max ? v : NaN;
  }
  // The two pixel fields' text as a centre in half-diagonals; null when either is not whole pixels within the bound.
  function wizLensCentreFromPx(right, above, w, h) {
    var max = wizLensCentrePxMax(w, h), d = wizHalfDiag(w, h);
    var x = wizLensCentreField(right, max), y = wizLensCentreField(above, max);
    if (isNaN(x) || isNaN(y)) return null;
    return [x / d, -y / d];
  }
  // The pixel fields show S.lensCentre in the snapshot's pixels; without a snapshot they are off.
  function wizShowLensCentre() {
    var on = imageWidth > 0 && imageHeight > 0, px = on ? wizLensCentrePx(S.lensCentre, imageWidth, imageHeight) : null;
    var max = on ? wizLensCentrePxMax(imageWidth, imageHeight) : 0;
    ['wiz_lens_cx', 'wiz_lens_cy'].forEach(function(id, i) {
      var el = $(id);
      if (!el) return;
      el.disabled = !on;
      el.placeholder = on ? '' : 'No video';
      el.min = on ? -max : '';
      el.max = on ? max : '';
      el.value = on ? px[i] : '';
    });
    S.lensCentreBad = false;
  }
  function wizSetLensCentre(c) { S.lensCentre = [Number(c[0]) || 0, Number(c[1]) || 0]; wizShowLensCentre(); }

  // The pair's error while the pair alone cannot follow the picture out to its corners, else the centre's;
  // either opens User input, so the error is never folded away.
  var LENS_CENTRE_LIMIT_ERROR = 'With this lens centre, the overlay cannot follow the picture out to its corners. Set the lens centre closer to 0.';
  function wizLensCentreRangeError(max) { return 'Enter a whole number from −' + max + ' to ' + max + '.'; }
  // The centre field being typed in, whose range error waits until it is left or committed.
  var wizLensCentreTyping = null;
  // What keeps the lens from being saved: 'pair' (the pair alone), 'range' (a centre field without whole pixels in
  // range, each such field in `off`), 'centre' (the centre stops the lens short of the corners), or ''.
  function wizLensProblem() {
    var l = wizLens(), max = wizLensCentrePxMax(imageWidth, imageHeight);
    var off = ['wiz_lens_cx', 'wiz_lens_cy'].map(function(id) { var el = $(id); return S.lensCentreBad && !!el && isNaN(wizLensCentreField(el.value, max)); });
    var kind = !wizLensIsValid(l[0], l[1], 0, 0) ? 'pair' : off[0] || off[1] ? 'range'
      : S.lensCentreBad || !wizLensIsValid(l[0], l[1], l[2], l[3]) ? 'centre' : '';
    return { kind: kind, off: off, max: max };
  }
  // A range error marks the field that holds no whole pixels in range; the limit error marks both.
  function wizShowLensLimit() {
    var p = wizLensProblem(), pairBad = p.kind === 'pair', max = p.max, ids = ['wiz_lens_cx', 'wiz_lens_cy'];
    var offRange = p.off.map(function(off, i) { return off && wizLensCentreTyping !== ids[i]; });
    var range = offRange[0] || offRange[1];
    var centreBad = p.kind === 'range' ? range : p.kind === 'centre';
    var err = $('wiz-lens-error'), cerr = $('wiz-lens-centre-error');
    if (err) err.style.display = pairBad ? 'block' : 'none';
    if (cerr) {
      cerr.style.display = centreBad ? 'block' : 'none';
      var text = !centreBad ? '' : range ? wizLensCentreRangeError(max) : LENS_CENTRE_LIMIT_ERROR;
      // Rewritten only when it changes, so the alert is not read out again on every keystroke.
      if (cerr.textContent !== text) cerr.textContent = text;
    }
    ids.forEach(function(id, i) {
      var el = $(id);
      if (el) el.setAttribute('aria-invalid', centreBad && (!range || offRange[i]) ? 'true' : 'false');
    });
    if (pairBad || centreBad) $('lens-adjust').open = true;
  }
  // Take the centre fields' centre unless they hold no whole pixels in range or it is the centre that stops the
  // lens short of the corners; the last centre then stays, flagged. True when taken.
  function wizTakeLensCentre() {
    var c = wizLensCentreFromPx($('wiz_lens_cx').value, $('wiz_lens_cy').value, imageWidth, imageHeight), l = wizLens();
    var taken = !!c && (!wizLensIsValid(l[0], l[1], 0, 0) || wizLensIsValid(l[0], l[1], c[0], c[1]));
    S.lensCentreBad = !taken;
    if (taken) S.lensCentre = c;
    return taken;
  }
  // A new lens from the sliders, the number fields or the centre fields: flag a lens that cannot follow the picture,
  // redraw the predicted curves, and persist.
  function wizLensEdited() {
    wizShowLensLimit();
    lensRecomputeCurves();
    S.lensTouched = true;
    renderLens();
    renderLensResult();
    onLensCoeffChanged();
  }
  window.onWizardLensInput = function(which, source) {
    var num = document.getElementById('wiz_lens_' + which);
    var range = document.getElementById('wiz_lens_' + which + '_range');
    if (num && range) {
      // The number field is never written while it is typed in: a part-typed "-0." reads as empty.
      if (source === 'range') num.value = range.value; else wizSyncLensRange('wiz_lens_' + which, num.value);
    }
    // A centre the last pair could not take may suit this one.
    if (S.lensCentreBad) wizTakeLensCentre();
    wizLensEdited();
  };
  window.onWizardLensCentreInput = function(el) {
    wizLensCentreTyping = el ? el.id : null;
    var taken = wizTakeLensCentre();
    wizLensEdited();
    // The fit holds the centre fixed, so traced lines are measured again about the new one.
    if (taken && lensLines.some(lensLineCounts)) scheduleLensFit();
  };
  window.onWizardLensCentreSettled = function() {
    wizLensCentreTyping = null;
    wizShowLensLimit();
  };

  // The overlay warp and its inverse (mirror scene/solver apply_overlay_distortion / invert_overlay_distortion).
  // Pinhole radius to screen radius about the lens centre (distort_normalised_radius): closed form for k2 = 0, else
  // bracketed Newton on h(r) = r_u (1 + k1 r^2 + k2 r^4) - r; a pinhole radius past a fold lands on the fold radius.
  function wizDistortRadius(ru, k1, k2) {
    var sPole = wizLensRoot(k2, k1), sFold = wizLensRoot(-3 * k2, -k1), sLim = Math.min(sPole, sFold), lim = Math.sqrt(sLim);
    if (sFold < sPole) ru = Math.min(ru, lim / (1 + k1 * sLim + k2 * sLim * sLim));
    var r = 2 * ru / (1 + Math.sqrt(Math.max(1 - 4 * k1 * ru * ru, 0)));
    if (k2 === 0) return r;
    var lo = 0, hi = lim;
    r = Math.min(Math.max(r, 0), lim);
    for (var i = 0; i < 80; i++) {
      var s = r * r, h = ru * ((k2 * s + k1) * s + 1) - r, step = h / (ru * r * (4 * k2 * s + 2 * k1) - 1);
      if (h > 0) lo = r; else if (h < 0) hi = r;
      var cand = r - step, moved = cand >= lo && cand <= hi ? cand : 0.5 * (lo + hi), settled = Math.abs(moved - r) <= 1e-13;
      r = moved;
      if (settled) break;
    }
    return r;
  }
  function wizApplyDistortion(pt, k1, k2, cx, cy) {
    if (k1 === 0 && k2 === 0 && cx === 0 && cy === 0) return [pt[0], pt[1]];
    var fx = imageWidth / 2, fy = imageHeight / 2, d = wizHalfDiag(imageWidth, imageHeight);
    var ox = fx + cx * d, oy = fy + cy * d, dx = pt[0] - fx, dy = pt[1] - fy;
    if (k1 === 0 && k2 === 0) return [pt[0] + (ox - fx), pt[1] + (oy - fy)];
    var ru = Math.hypot(dx, dy) / d;
    // The slope at the lens centre is 1, so the centre's own scale is 1.
    var scale = ru > 0 ? wizDistortRadius(ru, k1, k2) / ru : 1;
    return [ox + dx * scale, oy + dy * scale];
  }
  // Closed form; a point at or past the limit radius has no single pinhole point (NaN), which a valid lens keeps off the frame.
  function wizInvertDistortion(pt, k1, k2, cx, cy) {
    if (k1 === 0 && k2 === 0 && cx === 0 && cy === 0) return [pt[0], pt[1]];
    var fx = imageWidth / 2, fy = imageHeight / 2, d = wizHalfDiag(imageWidth, imageHeight);
    var dx = pt[0] - (fx + cx * d), dy = pt[1] - (fy + cy * d), s = (dx * dx + dy * dy) / (d * d), lim = wizLensLimitRadius(k1, k2);
    if (!(s < lim * lim)) return [NaN, NaN];
    var scale = 1 / ((k2 * s + k1) * s + 1);
    return [fx + dx * scale, fy + dy * scale];
  }

  // ---------------------------------------------------------------
  // Projection: mirrors scene/solver.project_points, so the page draws what the station solved.
  // ---------------------------------------------------------------
  var RAD = Math.PI / 180;
  function wizRotation(pitch, yaw, roll) {
    var a = pitch * RAD, b = -yaw * RAD, c = roll * RAD;
    var ca = Math.cos(a), sa = Math.sin(a), cb = Math.cos(b), sb = Math.sin(b), cc = Math.cos(c), sc = Math.sin(c);
    return [[cb * cc + sb * sa * sc, -cb * sc + sb * sa * cc, sb * ca], [ca * sc, ca * cc, -sa], [-sb * cc + cb * sa * sc, sb * sc + cb * sa * cc, cb * ca]];
  }
  // [x, y, z, pitch, yaw, roll, fov] seeing world point P on a w x h pinhole picture; null behind the camera.
  function wizProject(p, P, w, h) {
    if (!(p[6] > 1 && p[6] < 179)) return null;
    var R = wizRotation(p[3], p[4], p[5]);
    var t0 = P[0] - p[0], t1 = P[2] - p[2], t2 = -P[1] + p[1];
    var v0 = R[0][0] * t0 + R[1][0] * t1 + R[2][0] * t2;
    var v1 = R[0][1] * t0 + R[1][1] * t1 + R[2][1] * t2;
    var v2 = R[0][2] * t0 + R[1][2] * t1 + R[2][2] * t2;
    if (v2 >= -1e-9) return null;
    var aspect = w / h, vf = 2 * Math.atan(Math.tan(p[6] * RAD / 2) / aspect), f = 1 / Math.tan(vf / 2);
    return [((f / aspect) * (v0 / -v2) + 1) / 2 * w, (1 - f * (v1 / -v2)) / 2 * h];
  }
  function camParams(c) { return [c.pos_x, c.pos_y, c.pos_z, c.pitch, c.yaw, c.roll, c.fov]; }
  function wizLookAt(pos, target) {
    var dx = target[0] - pos[0], dy = target[1] - pos[1], dz = target[2] - pos[2];
    return { yaw: Math.atan2(dx, dy) / RAD, pitch: Math.atan2(dz, Math.hypot(dx, dy)) / RAD };
  }
  // On the snapshot as shown: through the camera, then bowed by the lens pair in use.
  function projectShown(cam, P) {
    var q = wizProject(camParams(cam), P, imageWidth, imageHeight);
    var l = wizLens();
    return q && wizApplyDistortion(q, l[0], l[1], l[2], l[3]);
  }
  function worldPolyline(cam, A, B, n) {
    var out = [];
    for (var i = 0; i <= n; i++) {
      var t = i / n, q = projectShown(cam, [A[0] + (B[0] - A[0]) * t, A[1] + (B[1] - A[1]) * t, A[2] + (B[2] - A[2]) * t]);
      if (q) out.push(q);
    }
    return out;
  }
  function gridLines(cam, g) {
    var out = [], x0 = g.cx - g.w / 2, x1 = g.cx + g.w / 2, y0 = g.cy - g.d / 2, y1 = g.cy + g.d / 2;
    var nx = Math.max(1, Math.round(g.w / g.spacing)), ny = Math.max(1, Math.round(g.d / g.spacing));
    for (var i = 0; i <= nx; i++) { var x = x0 + (x1 - x0) * i / nx; out.push(worldPolyline(cam, [x, y0, g.z], [x, y1, g.z], 24)); }
    for (var j = 0; j <= ny; j++) { var y = y0 + (y1 - y0) * j / ny; out.push(worldPolyline(cam, [x0, y, g.z], [x1, y, g.z], 32)); }
    return out;
  }
  function poly(pts) { return pts.filter(Boolean).map(function(p) { return p[0].toFixed(1) + ',' + p[1].toFixed(1); }).join(' '); }

  // Plane-to-picture homography from four points: the 0/0/0 mark before the camera solves.
  function solveLinear(A, b) {
    var n = b.length, M = A.map(function(r, i) { return r.concat([b[i]]); });
    for (var c = 0; c < n; c++) {
      var piv = c;
      for (var r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[piv][c])) piv = r;
      if (Math.abs(M[piv][c]) < 1e-14) return null;
      var tmp = M[c]; M[c] = M[piv]; M[piv] = tmp;
      for (var r2 = 0; r2 < n; r2++) {
        if (r2 === c) continue;
        var fct = M[r2][c] / M[c][c];
        if (fct === 0) continue;
        for (var k = c; k <= n; k++) M[r2][k] -= fct * M[c][k];
      }
    }
    return M.map(function(r, i) { return r[n] / r[i]; });
  }
  function wizHomographyPoint(plane, screen, P) {
    var A = [], b = [];
    for (var i = 0; i < 4; i++) {
      var X = plane[i][0], Y = plane[i][1], u = screen[i][0], v = screen[i][1];
      A.push([X, Y, 1, 0, 0, 0, -u * X, -u * Y]); b.push(u);
      A.push([0, 0, 0, X, Y, 1, -v * X, -v * Y]); b.push(v);
    }
    var h = solveLinear(A, b);
    if (!h) return null;
    var w = h[6] * P[0] + h[7] * P[1] + 1;
    if (Math.abs(w) < 1e-12) return null;
    return [(h[0] * P[0] + h[1] * P[1] + h[2]) / w, (h[3] * P[0] + h[4] * P[1] + h[5]) / w];
  }

  // ---------------------------------------------------------------
  // Camera Calibration state
  // ---------------------------------------------------------------
  var CORNERS = ['DSL', 'DSR', 'USR', 'USL'];
  var CORNER_TITLES = { DSL: 'Downstage left', DSR: 'Downstage right', USR: 'Upstage right', USL: 'Upstage left' };
  var CAM_PRESETS = [
    ['foh', 'Front of House (balcony railing / ceiling)', [0, -20, 8]],
    ['front', 'Front Truss', [0, -2, 8]],
    ['back', 'Back Truss', [0, 9, 7]],
    ['sl', 'Stage Left', [10, 4, 6]],
    ['sr', 'Stage Right', [-10, 4, 6]]
  ];
  var PIN_PROBLEM = 'Pin it on the picture. Its two distances allow two places, mirrored across the line between its two points.';
  var DISTANCE_SIGMA = 0.04, MEET_TOL = 3 * Math.SQRT2 * DISTANCE_SIGMA, FLAG_PX = 5, MARK_REACH_M = 10000;
  var S = {
    mode: 'simple', simpleLoc: '',
    rect: { w: '', d: '', origin: '', cx: '', cy: '', z: '' },
    cornerPins: { DSL: null, DSR: null, USR: null, USL: null },
    cornerMoved: { DSL: false, DSR: false, USR: false, USL: false },
    points: [], nextId: 1,
    pinSize: null,  // the snapshot size the pins are in; null while they are fractions of it
    cam: null, camPartial: false, height: null,
    solve: null, selPin: null, selCorner: null, pinMenu: null, pair: 0,
    lensTouched: false, lensLoaded: null, lensChoice: '',
    lensCentre: [SAVED.lens[2], SAVED.lens[3]], lensCentreBad: false
  };
  var solveSeq = 0, solveTimer = null, solvePending = false;

  // The point of origin is an active choice: until it is made, the rectangle has no centre.
  function rectVals() {
    var w = len(S.rect.w), d = len(S.rect.d);
    if (S.rect.origin === 'custom') return { w: w, d: d, cx: len(S.rect.cx), cy: len(S.rect.cy), z: len(S.rect.z) };
    if (S.rect.origin === 'dsc') return { w: w, d: d, cx: 0, cy: d / 2, z: 0 };
    return { w: w, d: d, cx: NaN, cy: NaN, z: NaN };
  }
  function andList(l) { return l.length > 1 ? l.slice(0, -1).join(', ') + ' and ' + l[l.length - 1] : l[0]; }
  function rectSized(r) { return r.w > 0 && r.d > 0 && r.w <= 200 && r.d <= 200; }
  // Names exactly what is missing, in the order the steps ask for it; the Simple hint shows it.
  function rectProblem() {
    var r = rectVals();
    var size = [['width', r.w], ['depth', r.d]].filter(function(q) { return isNaN(q[1]); }).map(function(q) { return q[0]; });
    if (size.length) return 'Enter the rectangle\'s ' + andList(size) + '.';
    if (!rectSized(r)) return 'Width and depth must be between 0 and ' + lenLimit() + '.';
    if (!S.rect.origin) return 'Choose the point of origin.';
    var at = [['X', r.cx], ['Y', r.cy], ['Z', r.z]].filter(function(q) { return isNaN(q[1]); }).map(function(q) { return q[0]; });
    if (at.length) return 'Enter the ' + andList(at) + ' of the rectangle\'s centre, measured from the point of origin.';
    return '';
  }
  function cornerXYZ(name, r) {
    var hw = r.w / 2, hd = r.d / 2;
    return { DSL: [r.cx + hw, r.cy - hd, r.z], DSR: [r.cx - hw, r.cy - hd, r.z], USR: [r.cx - hw, r.cy + hd, r.z], USL: [r.cx + hw, r.cy + hd, r.z] }[name];
  }
  // The marks both modes solve: Simple's corners have ids 1 to 4 in CORNERS order. ``need`` words a
  // problem for Review's list of incomplete points.
  function currentMarks() {
    if (S.mode === 'simple') {
      var ok = !rectProblem(), r = rectVals();
      return CORNERS.map(function(c, i) {
        return { id: i + 1, name: c, kind: 'known', pos: ok ? cornerXYZ(c, r) : null, pin: S.cornerMoved[c] ? S.cornerPins[c] : null, problem: ok ? '' : 'rect' };
      });
    }
    return S.points.map(function(p) {
      var m = { id: p.id, name: p.name || ('P' + p.id), kind: p.kind, pin: p.pin, problem: '' };
      if (p.kind === 'known') {
        var x = len(p.x), y = len(p.y), z = len(p.z), missing = [['X', x], ['Y', y], ['Z', z]].filter(function(q) { return isNaN(q[1]); }).map(function(q) { return q[0]; });
        if (missing.length) { m.problem = 'Enter its X, Y and Z.'; m.need = 'its ' + andList(missing); }
        else if ([x, y, z].some(function(v) { return Math.abs(v) > MARK_REACH_M; })) { m.problem = 'Enter X, Y and Z within ' + lenRough(MARK_REACH_M) + ' of the point of origin.'; m.need = 'X, Y and Z within ' + lenRough(MARK_REACH_M) + ' of the point of origin'; }
        else m.pos = [x, y, z];
      } else {
        m.a = p.a; m.b = p.b; m.da = len(p.da); m.db = len(p.db); m.z = p.z === '' ? 0 : len(p.z);
        if (!m.a || !m.b) { m.problem = 'Choose the two points its distances are measured from.'; m.need = 'the two points its distances are measured from'; }
        else if (m.a === m.b) { m.problem = 'Choose two different points.'; m.need = 'two different points to measure from'; }
        else if (!(m.da > 0 && m.da <= 200) || !(m.db > 0 && m.db <= 200)) { m.problem = 'Enter both distances, between 0 and ' + lenLimit() + '.'; m.need = 'both distances, between 0 and ' + lenLimit(); }
        else if (isNaN(m.z) || Math.abs(m.z) > MARK_REACH_M) { m.problem = 'Enter its Z.'; m.need = 'its Z'; }
        else if (!m.pin) { m.problem = PIN_PROBLEM; m.need = 'a pin'; }
      }
      return m;
    });
  }
  // Triangulated points after the points they are measured from; a loop or a broken one leaves a point out.
  function orderMarks(marks) {
    var byId = {}, order = [], state = {};
    marks.forEach(function(m) { byId[m.id] = m; });
    function visit(m, depth) {
      if (state[m.id] === 2) return !m.problem;
      if (state[m.id] === 1 || depth > 40) { m.problem = m.problem || 'It is measured from itself, through other points.'; return false; }
      state[m.id] = 1;
      var ok = !m.problem;
      if (ok && m.kind === 'triangulated') {
        [m.a, m.b].forEach(function(aid) {
          var an = byId[aid];
          if (!an) { m.problem = 'One of its points was deleted.'; ok = false; return; }
          if (!visit(an, depth + 1)) { if (ok) m.problem = m.problem || ('It is measured from ' + an.name + ', which cannot be placed.'); ok = false; }
        });
      }
      state[m.id] = 2;
      if (ok) order.push(m);
      return ok;
    }
    marks.forEach(function(m) { visit(m, 0); });
    return order;
  }
  function hintPos() { var c = S.cam; return c ? [c.x, c.y, c.z] : null; }

  // ---------------------------------------------------------------
  // Triangulated points placed from their measurements alone, before the camera solves
  // ---------------------------------------------------------------
  // The two places at height z at the measured distances from A and B: c1 on the right of A to B seen from above.
  function wizTrilaterate(A, dA, B, dB, z) {
    var ra2 = dA * dA - Math.pow(z - A[2], 2), rb2 = dB * dB - Math.pow(z - B[2], 2);
    if (ra2 < 0 || rb2 < 0) return { reason: 'A distance is shorter than the height difference between the points.' };
    var ra = Math.sqrt(ra2), rb = Math.sqrt(rb2), dx = B[0] - A[0], dy = B[1] - A[1], d = Math.hypot(dx, dy);
    if (d < 0.05) return { reason: 'Its two points are at the same place.' };
    var miss = 0, aa, hh;
    if (Math.abs(ra - rb) > d) { miss = Math.abs(ra - rb) - d; if (miss > MEET_TOL) return { reason: 'The difference between the two distances is larger than the distance between its two points.' }; }
    else if (ra + rb < d) { miss = d - ra - rb; if (miss > MEET_TOL) return { reason: 'The two distances together are shorter than the distance between its two points.' }; }
    if (miss > 0) { hh = 0; aa = ra + rb < d ? ra + miss / 2 : ra > rb ? ra - miss / 2 : -(ra + miss / 2); }
    else { aa = (ra * ra - rb * rb + d * d) / (2 * d); hh = Math.sqrt(Math.max(0, ra * ra - aa * aa)); }
    var mx = A[0] + aa * dx / d, my = A[1] + aa * dy / d;
    return { pos: [mx + hh * dy / d, my - hh * dx / d, z], alt: [mx - hh * dy / d, my + hh * dx / d, z] };
  }
  // Of the two places, the pin's side of the line between its two points' pins picks one: seen from a camera
  // above the floor, the picture (y down) shows the floor mirrored, so the sides swap. Left out until both
  // points and the point are pinned.
  function triPlaced(marks) {
    var byId = {}, pos = {};
    marks.forEach(function(m) { byId[m.id] = m; });
    orderMarks(marks).forEach(function(m) {
      if (m.kind === 'known') { pos[m.id] = m.pos; return; }
      var A = pos[m.a], B = pos[m.b]; if (!A || !B) return;
      var t = wizTrilaterate(A, m.da, B, m.db, m.z); if (t.reason) return;
      var c1 = t.pos, c2 = t.alt;
      if (Math.hypot(c1[0] - c2[0], c1[1] - c2[1]) < 0.01) { pos[m.id] = c1; return; }
      var pa = byId[m.a].pin, pb = byId[m.b].pin, pp = m.pin; if (!pa || !pb || !pp) return;
      var si = (pb[0] - pa[0]) * (pp[1] - pa[1]) - (pb[1] - pa[1]) * (pp[0] - pa[0]);
      var sw = (B[0] - A[0]) * (c1[1] - A[1]) - (B[1] - A[1]) * (c1[0] - A[0]);
      if (Math.abs(si) < 1) return;
      pos[m.id] = (si < 0) === (sw > 0) ? c1 : c2;
    });
    return pos;
  }

  // ---------------------------------------------------------------
  // Solve: the station fits the camera; the page says what is missing first
  // ---------------------------------------------------------------
  function solveState() {
    var marks = currentMarks(), usable = orderMarks(marks);
    var pinned = usable.filter(function(m) { return m.pin; });
    var need = [];
    var knownN = marks.filter(function(m) { return m.kind === 'known' && !m.problem; }).length;
    if (S.mode === 'simple') {
      if (rectProblem()) return { marks: marks, waiting: rectProblem() + ' Then the rectangle is drawn on the picture.' };
      var left = CORNERS.filter(function(c) { return !S.cornerMoved[c]; }).length;
      if (left) return { marks: marks, waiting: 'Drag each corner onto its tape mark. The camera is calculated when all four corners are moved. ' + left + ' left.' };
    } else {
      if (!S.cam) need.push('the camera position');
      if (knownN < 2) need.push((2 - knownN) + ' more known point' + (2 - knownN === 1 ? '' : 's'));
    }
    if (pinned.length < 4) need.push((4 - pinned.length) + ' more pinned point' + (4 - pinned.length === 1 ? '' : 's'));
    if (need.length) return { marks: marks, need: need };
    return { marks: marks, usable: usable };
  }
  function solveBody(usable, seq) {
    return {
      image_width: imageWidth, image_height: imageHeight, mode: S.mode, hint: hintPos(), saved_fov: SAVED.fov, camera_height: S.height,
      camera: { lens_k1: wizReadLensCoeff('wiz_lens_k1'), lens_k2: wizReadLensCoeff('wiz_lens_k2'), lens_cx: S.lensCentre[0], lens_cy: S.lensCentre[1] },
      solve_page: WIZ_SOLVE_PAGE, solve_seq: seq,
      marks: usable.map(function(m) {
        var out = { id: m.id, kind: m.kind, pin: m.pin ? [m.pin[0], m.pin[1]] : null };
        if (m.kind === 'known') { out.x = m.pos[0]; out.y = m.pos[1]; out.z = m.pos[2]; }
        else { out.from_a = +m.a; out.from_b = +m.b; out.distance_a = m.da; out.distance_b = m.db; out.z = m.z; }
        return out;
      })
    };
  }
  // An answer still in flight is for the state before this change: it is dropped.
  function solveSoon() { clearTimeout(solveTimer); solveSeq++; renderCalibOverlay(); solveTimer = setTimeout(runSolve, 40); }
  // Every solve counts here; an answer to an older one is dropped, and the station stops working on it.
  function runSolve(then) {
    clearTimeout(solveTimer);
    var seq = ++solveSeq, st = solveState();
    if (!st.usable || !imageWidth) {
      S.solve = st.usable ? { marks: st.marks, waiting: 'The camera is calculated when the snapshot has loaded.' } : st;
      solvePending = false;
      afterSolve(then);
      return;
    }
    // Until this answer lands, Save and finish would save the previous one.
    solvePending = true;
    $('btn-apply-finish').disabled = true;
    var body = solveBody(st.usable, seq);
    fetch('/api/wizard/solve', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(function(r) {
      return r.json().then(function(data) { return { status: r.status, data: data }; });
    }).then(function(res) {
      if (seq !== solveSeq || res.status === 409) return;
      solvePending = false;
      S.solve = solveFromAnswer(st.marks, res.status, res.data);
      S.solve.withHeight = body.camera_height != null;
      afterSolve(then);
    }).catch(function() {
      if (seq !== solveSeq) return;
      solvePending = false;
      S.solve = { marks: st.marks, error: 'The station did not answer. Check the connection, then move a pin to calculate again.' };
      afterSolve(then);
    });
  }
  function solveFromAnswer(marks, status, data) {
    var byId = {}, sent = marks.filter(function(m) { return m.pin && !m.problem; }).length;
    marks.forEach(function(m) { byId[m.id] = m; });
    (data.marks || []).forEach(function(a) { if (a.reason && byId[a.id] && !byId[a.id].problem) byId[a.id].problem = a.reason; });
    if (status === 422 && Array.isArray(data.need)) return { marks: marks, need: data.need };
    if (status === 400) return { marks: marks, error: 'The station refused these points. Check their values.' };
    if (status !== 200 || !data.camera) {
      var noFit = 'No camera fits these points. Check the pins and the measurements.';
      return { marks: marks, error: S.mode === 'simple' && (!data.error || data.error === noFit) ? 'No camera fits these corners. Check the corners and the measurements.' : data.error || noFit };
    }
    var positions = {}, res = {};
    data.marks.forEach(function(a) {
      if (a.position) positions[a.id] = a.position;
      res[a.id] = { px: a.error, at: a.seen, dist: a.distance_error };
    });
    // A pin the camera puts behind itself has no error to measure, and is as wrong as one far off.
    var behind = marks.filter(function(m) { return m.pin && !m.problem && res[m.id] && res[m.id].px == null; }).map(function(m) { return { kind: 'behind', id: m.id }; });
    var behindIds = {};
    behind.forEach(function(b) { behindIds[b.id] = true; });
    return { marks: marks, cam: data.camera, steep: data.steep === true, positions: positions, res: res, worst: data.largest_error, pinned: sent, behind: behind.length, behindIds: behindIds,
      reasons: wordReasons((data.reasons || []).concat(behind), marks) };
  }
  function afterSolve(then) {
    if (S.mode === 'simple') renderCornerRows(); else updateAdvChips();
    renderReq();
    renderCalibResult();
    renderCalibOverlay();
    renderFsBar();
    if (typeof then === 'function') then();
  }

  // ---------------------------------------------------------------
  // Something might be wrong: the station's reasons in this page's units
  // ---------------------------------------------------------------
  // Names the axes that carry the gap: one axis off points at the origin or a row of pins, not the lens.
  function offsetReason(d, off, tol, perAxis) {
    var words = [['stage left', 'stage right'], ['upstage', 'downstage'], ['above', 'below']], parts = [];
    for (var i = 0; i < 3; i++) if (Math.abs(d[i]) >= 0.25 * off) parts.push(lenRough(Math.abs(d[i])) + ' ' + words[i][d[i] > 0 ? 0 : 1]);
    var last = parts.length && !/above|below/.test(parts[parts.length - 1]);
    var list = parts.length > 1 ? parts.slice(0, -1).join(', ') + ' and ' + parts[parts.length - 1] : parts[0];
    var total = parts.length === 1 && parts[0].indexOf(lenRough(off)) === 0 ? '' : lenRough(off) + ' in total, ';
    var text = 'The calculated camera is ' + list + (last ? ' of' : '') + ' the camera position you entered (' + total + 'limit ' + lenRough(tol) + (perAxis ? ' per axis' : '') + ').';
    if (S.mode === 'simple' && S.rect.origin === 'custom') text += ' Was the camera measured from the same point of origin as the rectangle\'s centre?';
    return text;
  }
  function wordReasons(reasons, marks) {
    var names = {}, out = [];
    marks.forEach(function(m) { names[m.id] = m.name; });
    if (S.mode === 'simple' && !lensMeasured()) out.push('The lens was not measured. Four corners fit with any lens, so a missing lens measurement does not show in their errors. Measure the lens in the Lens step, unless straight lines in the picture already look straight.');
    (reasons || []).forEach(function(r) {
      if (r.kind === 'offset') out.push(offsetReason(r.delta, r.off, r.allowed, r.per_axis));
      else if (r.kind === 'fov') out.push('The calculated field of view, ' + r.fov.toFixed(1) + '°, is outside the usual range for cameras.');
      else if (r.kind === 'below') out.push('The calculated camera is below the floor.');
      else if (r.kind === 'up') out.push('The calculated camera points upwards.');
      else if (r.kind === 'reach') out.push('The calculated camera puts the far floor more than twice as far, or as near, as the floor points alone do.');
      else if (r.kind === 'pixel') out.push(names[r.id] + ' disagrees with the other points by ' + r.px.toFixed(1) + ' px.');
      else if (r.kind === 'behind') out.push(names[r.id] + ' lies behind the calculated camera.');
      else if (r.kind === 'distance') out.push(names[r.id] + '\'s distances disagree with the picture by ' + lenSmall(r.m) + '.');
    });
    return out;
  }
  function cautionHtml(s) {
    return 'Something might be wrong<div class="notice-sub"><ul class="caution-list">' + s.reasons.map(function(r) { return '<li>' + esc(r) + '</li>'; }).join('') + '</ul>' +
      (applyBlocked(s) ? 'Save and finish stays off while a point is more than ' + APPLY_MAX_PX + ' px off or behind the camera.' : 'You can still save the camera.') +
      ' Check the measurements, then ' + (S.mode === 'simple' ? 'place the corners again.' : 'pin the points again.') + '</div>';
  }
  // As 0.4.3 refused such a solve, a pin this far off keeps Save and finish off.
  var APPLY_MAX_PX = 20;
  function applyBlocked(s) { return !!(s && s.cam && (s.worst > APPLY_MAX_PX || s.behind > 0)); }
  function pinBehind(s, id) { return !!(s && s.cam && s.behindIds && s.behindIds[id]); }
  function pinFlagged(s, id) { var r = s && s.res && s.res[id]; return !!(r && r.px > FLAG_PX) || pinBehind(s, id); }
  // Save and finish writes every point, so a point with a problem keeps it off; Simple's rectangle problem leaves no solve to save.
  function marksIncomplete(s) { return !!(s && s.marks && s.marks.some(function(m) { return m.problem; })); }
  function incompleteLines(s) {
    return (s && s.marks || []).filter(function(m) { return m.problem && m.problem !== 'rect'; }).map(function(m) {
      return m.need ? m.name + ' needs ' + m.need + '.' : m.name + ': ' + m.problem;
    });
  }
  function incompleteHtml(lines) {
    return 'Some points are not complete.<div class="notice-sub"><ul class="caution-list">' + lines.map(function(l) { return '<li>' + esc(l) + '</li>'; }).join('') + '</ul>Complete or delete them in Camera Calibration.</div>';
  }
  // Simple keeps the station's pair without measuring one: said, but not a caution.
  function stationLensOnly() {
    return S.mode === 'simple' && !S.lensTouched && lensMeasured();
  }

  // ---------------------------------------------------------------
  // Picture helpers: snapshot, handles, loupe
  // ---------------------------------------------------------------
  function revokeSnapshotUrl(url) {
    if (url) {
      try { URL.revokeObjectURL(url); } catch (e) {}
    }
  }

  // ``notice``: say so on Camera Calibration, where the pins stay where they are.
  var snapshotSeq = 0;
  // Only the newest request lands: an older answer or retry arriving late is dropped.
  window.loadSnapshot = function(attempt, notice, seq) {
    attempt = attempt || 0;
    if (!attempt) seq = ++snapshotSeq;
    fetch('/api/video/snapshot/full?t=' + Date.now()).then(function(r) {
      if (!r.ok) throw new Error('No feed');
      return r.blob();
    }).then(function(blob) {
      if (seq !== snapshotSeq) return;
      var previousUrl = snapshotUrl;
      var nextUrl = URL.createObjectURL(blob);
      var img = new Image();
      img.onload = function() {
        if (seq !== snapshotSeq) { revokeSnapshotUrl(nextUrl); return; }
        imageWidth = img.naturalWidth;
        imageHeight = img.naturalHeight;
        snapshotUrl = nextUrl;
        showSnapshotOnCurrentStep(nextUrl, img);
        revokeSnapshotUrl(previousUrl);
        if (notice) showRefreshed();
      };
      img.onerror = function() {
        revokeSnapshotUrl(nextUrl);
        if (seq === snapshotSeq) showNoFeed();
      };
      img.src = nextUrl;
    }).catch(function() {
      // A just-changed source may still be connecting – retry briefly so the
      // new feed appears on its own, without a manual Refresh Image.
      if (seq !== snapshotSeq) return;
      if (attempt < 6) {
        setTimeout(function() { loadSnapshot(attempt + 1, notice, seq); }, 700);
      } else {
        showNoFeed();
      }
    });
  };

  function setPreviewVisibility(visible) {
    [['lens-container', 'lens-no-feed'], ['cal-container', 'cal-no-feed'], ['review-container', 'review-no-feed']].forEach(function(pair) {
      $(pair[0]).style.display = visible ? 'block' : 'none';
      $(pair[1]).style.display = visible ? 'none' : 'block';
    });
    $('lens-hint').hidden = !visible;
    if (!visible) $('lens-hint').liveHtml = null;
  }
  function showNoFeed() { setPreviewVisibility(false); }
  function showSnapshotOnCurrentStep(url, img) {
    ['lens-image', 'cal-image', 'review-image'].forEach(function(id) { $(id).src = url; });
    snapshotCanvas = document.createElement('canvas');
    snapshotCanvas.width = imageWidth;
    snapshotCanvas.height = imageHeight;
    snapshotCanvas.getContext('2d').drawImage(img, 0, 0);
    setPreviewVisibility(true);
    lensOnSnapshot();
    pinsToSnapshot();
    if (S.mode === 'simple') placeStartQuad();
    renderCalibOverlay();
    if (currentStep === WIZ.calib) runSolve();
    if (currentStep === WIZ.review) runSolve(renderReview);
    if (FS) fsLayout();
  }
  var refreshedTimer = null;
  // Written each time it shows, so every new snapshot is read out, one taken while the notice still shows too.
  function showRefreshed() {
    var el = $('cal-refreshed');
    el.hidden = false; setLive(el, el.innerHTML, true);
    clearTimeout(refreshedTimer);
    refreshedTimer = setTimeout(function() { showLive(el, '', false); }, 4000);
  }

  function svgEl(tag, attrs) {
    var el = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.keys(attrs || {}).forEach(function(k) { el.setAttribute(k, attrs[k]); });
    return el;
  }

  // Snapshot pixels per CSS pixel in the full previews, which all span the
  // wizard's column. The viewBox is the snapshot's own resolution, so a size
  // meant for a finger or an eye is this many viewBox units per CSS pixel; a
  // hidden preview measures nothing, so the one showing is asked.
  function overlayPx() {
    var overlays = document.querySelectorAll('svg.wizard-overlay');
    for (var i = 0; i < overlays.length; i++) {
      var width = overlays[i].getBoundingClientRect().width;
      if (width > 0 && imageWidth > 0) return imageWidth / width;
    }
    return 1;
  }

  // The Lens step's point, used for every point the wizard drags: the dot, a
  // focus ring and a 44 px grab area around it, sized in CSS pixels by ``px``
  // (``overlayPx``) so a phone's finger finds it.
  function pointHandle(pos, r, px) {
    var g = svgEl('g', { class: 'handle wizard-point', tabindex: '0', transform: 'translate(' + pos[0] + ',' + pos[1] + ')' });
    g.appendChild(svgEl('rect', { x: -22 * px, y: -22 * px, width: 44 * px, height: 44 * px, fill: 'transparent' }));
    g.appendChild(svgEl('circle', { r: 9 * px, class: 'handle-ring' }));
    g.appendChild(svgEl('circle', { r: r * px, class: 'dot' }));
    return g;
  }

  // The snapshot at full resolution, for the loupe and the Lens step.
  var snapshotCanvas = null;
  // The loupe sits beside the point: close to a mouse pointer, a finger's width away from a touch.
  var pointerIsMouse = true;
  // CSS pixels a press may move and still be a click, on every picture: a finger or a pen wanders more than a mouse.
  function tapSlop(pointerType) { return pointerType === 'mouse' ? 3 : 8; }
  function showLoupe(containerId, overlayId, pos, avoid) {
    var cont = $(containerId), loupe = cont && cont.querySelector('.wizard-loupe');
    if (!loupe || !snapshotCanvas || !imageWidth) return;
    var rect = cont.getBoundingClientRect(), s = rect.width / imageWidth;
    var at = wizLoupeSpot(pos[0] * s, pos[1] * s, 120, pointerIsMouse ? 12 : 30, rect.width, rect.height, avoid ? {
      l: avoid.offsetLeft, t: avoid.offsetTop, r: avoid.offsetLeft + avoid.offsetWidth, b: avoid.offsetTop + avoid.offsetHeight
    } : null);
    loupe.style.left = at[0] + 'px'; loupe.style.top = at[1] + 'px'; loupe.style.display = 'block';
    var cv = loupe.querySelector('canvas'), ctx = cv.getContext('2d'), src = 120 / (4 * s);
    ctx.imageSmoothingEnabled = false;
    ctx.clearRect(0, 0, cv.width, cv.height);
    ctx.drawImage(snapshotCanvas, pos[0] - src / 2, pos[1] - src / 2, src, src, 0, 0, cv.width, cv.height);
    // Yellow on a wider black line: readable on a white floor and on a dark one.
    var c = cv.width / 2, arm = cv.width * 28 / 240;
    ctx.beginPath(); ctx.moveTo(c, c - arm); ctx.lineTo(c, c + arm); ctx.moveTo(c - arm, c); ctx.lineTo(c + arm, c);
    var tokens = getComputedStyle(document.documentElement);
    ctx.strokeStyle = tokens.getPropertyValue('--bg-deep').trim() || 'black'; ctx.lineWidth = 6; ctx.stroke();
    ctx.strokeStyle = tokens.getPropertyValue('--accent').trim() || 'orange'; ctx.lineWidth = 2; ctx.stroke();
  }
  // The round loupe's top left, its edge ``gap`` from the point (sx, sy): up-right first, then the other
  // corners; a spot that leaves the picture or covers ``avoid`` ({l, t, r, b}) is skipped.
  function wizLoupeSpot(sx, sy, size, gap, width, height, avoid) {
    var rad = size / 2, k = (rad + gap) / Math.SQRT2;
    var spots = [[1, -1], [-1, -1], [-1, 1], [1, 1]].map(function(d) { return [sx + d[0] * k - rad, sy + d[1] * k - rad]; });
    var inside = function(q) { return q[0] >= 0 && q[1] >= 0 && q[0] + size <= width && q[1] + size <= height; };
    var clear = function(q) { return !avoid || q[0] + size <= avoid.l || q[0] >= avoid.r || q[1] + size <= avoid.t || q[1] >= avoid.b; };
    var at = spots.filter(function(q) { return inside(q) && clear(q); })[0] || spots.filter(inside)[0] || spots[0];
    return [Math.max(0, Math.min(at[0], width - size)), Math.max(0, Math.min(at[1], height - size))];
  }
  function hideLoupe(containerId) {
    var loupe = document.querySelector('#' + containerId + ' .wizard-loupe');
    if (loupe) loupe.style.display = 'none';
  }
  function svgPoint(svg, e) {
    var pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;
    var p = pt.matrixTransform(svg.getScreenCTM().inverse());
    return [Math.max(0, Math.min(imageWidth - 1, p.x)), Math.max(0, Math.min(imageHeight - 1, p.y))];
  }

  // ---------------------------------------------------------------
  // Lens: the distortion from lines that are straight in reality
  // ---------------------------------------------------------------
  // A line keeps its endpoints p0 / p1 (image px) and its three middle points
  // in the line's own frame as (t, off): t along the chord, off along its
  // perpendicular. Moving an endpoint keeps every middle point's (t, off); a
  // middle point goes wherever it is moved.
  var LENS_T = [0.25, 0.5, 0.75];
  var LENS_FIT_DEBOUNCE_MS = 300;
  var LENS_MIN_LINE_PX = 20;
  // Mirror lens_fit._MIN_LINE_SPAN_PX: a line spanning less carries no direction to fit.
  var LENS_MIN_SPAN_PX = 10;
  var lensFitSeq = 0;        // every fit request counts; an answer to an older one is dropped
  var lensFitAsk = null;     // the lensFitSeq of the fit due or sent and not yet answered
  var LENS_RATING_LEVEL = { low: 'caution', medium: 'caution', okay: 'info', good: 'success', excellent: 'success' };
  var LENS_RATING_SEGMENTS = { low: 1, medium: 2, okay: 3, good: 4, excellent: 5 };
  var LENS_RATING_LABEL = { low: 'Not usable', medium: 'Poor', okay: 'Fair', good: 'Good', excellent: 'Excellent' };
  // A fit rated Good or better whose lines stay near the middle of the picture: shown as Fair, since its edges are not measured.
  var LENS_MIDDLE = {
    rating: ', but might already be good in the middle',
    result: 'The lines stay near the middle of the picture, so its edges are not measured. That is fine if you only need the middle. To measure the full image, add lines near the edges.',
    hint: 'The result may already be good in the middle.',
    hintSub: 'If possible, add lines near the edges of the picture to measure them too. Or press Next.'
  };
  // An answer without the field (a session saved before it) counts as measuring the edges.
  function lensMiddleOnly(fit) { return !!fit && (fit.rating === 'good' || fit.rating === 'excellent') && fit.edges_measured === false; }
  function lensShownRating(fit) { return lensMiddleOnly(fit) ? 'okay' : fit.rating; }
  var LENS_DEV = {{'true' if config.ui.developer_mode else 'false'}};
  var LENS_SUGGEST = {{'true' if _suggest_on else 'false'}};
  var lensLines = [];        // [{p0, p1, mids: [{t, off, on}], snapped: [bool x5], curve, rms, misfit, candidate}]
  var lensCandidates = [];   // suggested edges of the current snapshot: [{points, samples, polarity, used, rejected}]
  var lensRejected = [];     // the ends of every rejected suggestion, until Refresh Image: [[x, y], [x, y]]
  var lensHot = -1;          // the suggestion pointed at, by its bubble
  var lensFocusAfter = null; // after a choice by keyboard: {i, cls} of the bubble button that had the focus
  var lensHintText = '';
  var lensHintLines = null;  // [heading, sub] the lines and the last answer gave, kept while a refit is awaited
  var lensRefocus = false;   // a redraw is giving the focus back: no new selection
  var lensImageSize = null;  // [w, h] the lines were traced on
  var lensPending = null;    // first click of a line being traced
  var lensSelected = null;   // {line, point} with point 0..4
  var lensLast = null;       // the line drawn or changed last: Tab enters the picture there
  var lensFit = null;        // the last fit response
  var lensCanvas = null;     // the snapshot at full resolution, for the luma band
  var lensFitTimer = null;
  var lensReSolveTimer = null;
  var lensDrag = null;
  var lensHold = false;      // the selected point keeps the focus and the loupe until Enter, Esc or a press elsewhere

  function lensFrame(line) {
    var dx = line.p1[0] - line.p0[0], dy = line.p1[1] - line.p0[1];
    var len = Math.sqrt(dx * dx + dy * dy) || 1;
    return { dx: dx, dy: dy, len: len, nx: -dy / len, ny: dx / len };
  }
  function lensPointPos(line, j) {
    if (j === 0) return [line.p0[0], line.p0[1]];
    if (j === 4) return [line.p1[0], line.p1[1]];
    var m = line.mids[j - 1], f = lensFrame(line);
    return [line.p0[0] + m.t * f.dx + m.off * f.nx, line.p0[1] + m.t * f.dy + m.off * f.ny];
  }
  function lensPointIsOn(line, j) { return j === 0 || j === 4 || line.mids[j - 1].on; }
  function lensActivePoints(line) {
    var pts = [];
    for (var j = 0; j < 5; j++) if (lensPointIsOn(line, j)) pts.push(lensPointPos(line, j));
    return pts;
  }
  // A line counts while its ends and at least one middle point remain, spanning enough to have a direction.
  function lensLineCounts(line) {
    var pts = lensActivePoints(line);
    if (pts.length < 3) return false;
    var xs = pts.map(function(p) { return p[0]; }), ys = pts.map(function(p) { return p[1]; });
    var span = Math.max(Math.max.apply(null, xs) - Math.min.apply(null, xs), Math.max.apply(null, ys) - Math.min.apply(null, ys));
    return span >= LENS_MIN_SPAN_PX;
  }
  function lensRestoredPair(p) { return Array.isArray(p) && p.length === 2 && isFinite(p[0]) && isFinite(p[1]); }
  function lensRestoredLine(line) {
    return !!line && lensRestoredPair(line.p0) && lensRestoredPair(line.p1) && Array.isArray(line.mids)
      && line.mids.length === 3 && Array.isArray(line.snapped) && line.snapped.length === 5;
  }
  function lensSetMidFromPos(line, j, pos) {
    var f = lensFrame(line), m = line.mids[j - 1], rx = pos[0] - line.p0[0], ry = pos[1] - line.p0[1];
    m.t = (rx * f.dx + ry * f.dy) / (f.len * f.len);
    m.off = rx * f.nx + ry * f.ny;
  }
  function lensSetMidsFromPositions(line, positions) {
    for (var j = 1; j <= 3; j++) lensSetMidFromPos(line, j, positions[j]);
  }
  function lensClamp(pt) {
    return [Math.max(0, Math.min(imageWidth - 1, pt[0])), Math.max(0, Math.min(imageHeight - 1, pt[1]))];
  }
  function lensSvgPoint(clientX, clientY) {
    var svg = document.getElementById('lens-overlay');
    var pt = svg.createSVGPoint();
    pt.x = clientX; pt.y = clientY;
    var p = pt.matrixTransform(svg.getScreenCTM().inverse());
    return [p.x, p.y];
  }

  // ---- snapshot ----
  function lensOnSnapshot() {
    document.getElementById('lens-overlay').setAttribute('viewBox', '0 0 ' + imageWidth + ' ' + imageHeight);
    // The current calibration's centre is named in pixels of the picture.
    renderLensCurrent();
    lensCanvas = snapshotCanvas;
    var sameSize = !!lensImageSize && lensImageSize[0] === imageWidth && lensImageSize[1] === imageHeight;
    if (lensImageSize && (lensImageSize[0] !== imageWidth || lensImageSize[1] !== imageHeight) && lensLines.length) {
      lensFitSeq++;
      lensLines = [];
      lensFit = null;
      lensSelected = null;
      lensPending = null;
      lensShowNotice('The snapshot resolution changed, so the lines on the picture were removed.');
    }
    if (lensImageSize && (lensImageSize[0] !== imageWidth || lensImageSize[1] !== imageHeight)) lensRejected = [];
    lensImageSize = [imageWidth, imageHeight];
    lensCandidates = [];
    // The centre fields count in this snapshot's pixels; a flagged centre keeps its text and flag while they still do.
    if (!(sameSize && S.lensCentreBad)) wizShowLensCentre();
    wizShowLensLimit();
    renderLens();
    renderLensResult();
    saveToSession();
    // The suggestions are for this step: a snapshot loaded for another step is not sent.
    if (currentStep === WIZ.lens) lensRequestEdges();
  }
  function lensShowNotice(text) {
    var el = document.getElementById('lens-notice');
    if (!el) return;
    setLive(el, esc(text || ''));
    el.style.display = text ? '' : 'none';
  }
  function lensShowStatus(msg, ok) {
    var el = document.getElementById('lens-status');
    if (!el) return;
    el.style.display = msg ? 'block' : 'none';
    setLive(el, esc(msg || ''));
    el.className = 'wizard-status ' + (ok ? 'ok' : 'error');
  }

  // ---- tracing ----
  function lensTraceClick(pt) {
    pt = lensClamp(pt);
    if (!lensPending) {
      lensPending = pt;
      lensSelected = null;
      renderLens();
      return;
    }
    var dx = pt[0] - lensPending[0], dy = pt[1] - lensPending[1];
    if (Math.sqrt(dx * dx + dy * dy) < LENS_MIN_LINE_PX) return;
    var line = {
      p0: lensPending, p1: pt,
      mids: LENS_T.map(function(t) { return { t: t, off: 0, on: true }; }),
      snapped: [false, false, false, false, false],
      curve: null, rms: null, misfit: false,
    };
    lensPending = null;
    lensLines.push(line);
    lensLast = line;
    lensSelected = { line: lensLines.length - 1, point: null };
    lensShowNotice('');
    renderLens();
    lensSnapLine(line);
  }

  // ---- suggested edges ----
  // Mirror edge_chains.edge_map_scale: the snapshot is scaled under the map width.
  function lensEdgeScale() { return Math.max(1, Math.min(16, Math.ceil(imageWidth / 1280))); }
  function lensEdgeSize() {
    var scale = lensEdgeScale();
    return { scale: scale, width: Math.ceil(imageWidth / scale), height: Math.ceil(imageHeight / scale) };
  }
  function lensBase64(bytes) {
    var bin = '';
    for (var j = 0; j < bytes.length; j += 8192) bin += String.fromCharCode.apply(null, bytes.subarray(j, j + 8192));
    return btoa(bin);
  }
  // The luma of the RGBA pixels w x h, as means of scale x scale blocks; the last
  // row and column repeat to fill a block. A canvas scaling down by three or more
  // samples a few pixels per block and drops one-pixel seams between them.
  function lensScaledLuma(rgba, w, h, scale) {
    var sw = Math.ceil(w / scale), sh = Math.ceil(h / scale), out = new Uint8Array(sw * sh);
    for (var by = 0; by < sh; by++) {
      for (var bx = 0; bx < sw; bx++) {
        var sum = 0;
        for (var dy = 0; dy < scale; dy++) {
          var row = Math.min(by * scale + dy, h - 1) * w;
          for (var dx = 0; dx < scale; dx++) {
            var k = 4 * (row + Math.min(bx * scale + dx, w - 1));
            sum += (rgba[k] * 299 + rgba[k + 1] * 587 + rgba[k + 2] * 114) / 1000;
          }
        }
        out[by * sw + bx] = Math.round(sum / (scale * scale));
      }
    }
    return out;
  }
  function lensRequestEdges() {
    if (!LENS_SUGGEST || !lensCanvas) return;
    var size = lensEdgeSize();
    var rgba = lensCanvas.getContext('2d').getImageData(0, 0, imageWidth, imageHeight).data;
    var luma = lensScaledLuma(rgba, imageWidth, imageHeight, size.scale);
    var snapshot = lensCanvas;
    fetch('/api/wizard/lens/edges', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        image_width: imageWidth, image_height: imageHeight, scale: size.scale,
        width: size.width, height: size.height, data: lensBase64(luma), with_map: LENS_DEV,
      }),
    }).then(function(r) {
      return r.json().then(function(data) { return { ok: r.ok, data: data }; });
    }).then(function(res) {
      if (snapshot !== lensCanvas || !res.ok || !Array.isArray(res.data.candidates)) return;
      lensCandidates = res.data.candidates.map(function(c) {
        return {
          points: c.points, samples: c.samples, polarity: c.polarity === 1 || c.polarity === -1 ? c.polarity : 0,
          used: false, rejected: lensRejected.some(function(r) { return lensSameEnds(c.samples, r[0], r[1]); }),
        };
      });
      lensLines.forEach(function(line) { line.candidate = lensCandidateOf(line); });
      if (res.data.edges) lensPaintEdges(res.data.edges);
      renderLens();
    }).catch(function() {});
  }
  // Whether a suggestion's five samples run between the two ends, either way round.
  function lensSameEnds(s, a, b) {
    return (Math.hypot(s[0][0] - a[0], s[0][1] - a[1]) < 20 && Math.hypot(s[4][0] - b[0], s[4][1] - b[1]) < 20)
      || (Math.hypot(s[4][0] - a[0], s[4][1] - a[1]) < 20 && Math.hypot(s[0][0] - b[0], s[0][1] - b[1]) < 20);
  }
  // The suggestion a line was taken from, by its ends, so it is offered again when the line goes.
  function lensCandidateOf(line) {
    for (var i = 0; i < lensCandidates.length; i++) {
      if (lensSameEnds(lensCandidates[i].samples, line.p0, line.p1)) { lensCandidates[i].used = true; return i; }
    }
    return undefined;
  }
  function lensAddCandidate(i) {
    var cand = lensCandidates[i];
    if (!cand || cand.used || cand.rejected) return;
    lensHotCand(-1);
    var pts = cand.samples;
    var line = {
      p0: pts[0], p1: pts[4],
      mids: LENS_T.map(function(t) { return { t: t, off: 0, on: true }; }),
      snapped: [false, false, false, false, false],
      curve: null, rms: null, misfit: false, candidate: i,
    };
    lensSetMidsFromPositions(line, pts);
    cand.used = true;
    lensPending = null;
    lensLines.push(line);
    lensLast = line;
    lensSelected = { line: lensLines.length - 1, point: null };
    renderLens();
    // Snapped onto the suggestion's own edge; one bowed past its band keeps its samples, as a refused snap would.
    if (lensNearFits(pts)) lensSnapLine(line, { points: pts, polarity: cand.polarity });
    else lensChanged();
  }
  function lensReleaseCandidate(line) {
    if (line.candidate !== undefined && lensCandidates[line.candidate]) lensCandidates[line.candidate].used = false;
  }
  // A rejected suggestion leaves the picture and the fit until Refresh Image suggests the lines again.
  function lensRejectCandidate(i) {
    var cand = lensCandidates[i];
    if (!cand || cand.used || cand.rejected) return;
    lensHotCand(-1);
    cand.rejected = true;
    lensRejected.push([cand.samples[0], cand.samples[4]]);
    renderLens();
    saveToSession();
  }
  // The Lens step's Refresh Image: a new snapshot, and every suggestion offered again.
  window.lensRefreshImage = function() {
    lensRejected = [];
    lensCandidates.forEach(function(c) { c.rejected = false; });
    saveToSession();
    loadSnapshot();
  };
  function lensOpenCandidates() {
    var out = [];
    lensCandidates.forEach(function(c, i) { if (!c.used && !c.rejected) out.push(i); });
    return out;
  }
  // The developer's view: every thin edge the suggestions were picked from, over the snapshot.
  function lensPaintEdges(edges) {
    var canvas = document.getElementById('lens-edges');
    if (!canvas) return;
    var bin = atob(edges.data), n = edges.width * edges.height;
    if (bin.length !== n) return;
    canvas.width = edges.width; canvas.height = edges.height;
    var ctx = canvas.getContext('2d'), image = ctx.createImageData(edges.width, edges.height), px = image.data;
    for (var i = 0, k = 0; i < n; i++, k += 4) {
      var v = bin.charCodeAt(i);
      px[k] = px[k + 1] = px[k + 2] = v; px[k + 3] = 255;
    }
    ctx.putImageData(image, 0, 0);
  }
  window.lensToggleEdgeView = function(show) {
    var canvas = document.getElementById('lens-edges');
    if (canvas) canvas.style.display = show ? 'block' : 'none';
  };

  // Mirror edge_snap.band_half_size / band_step: the band grows with the snapshot.
  function lensBandHalf() { return Math.max(48, Math.min(256, Math.round(imageWidth / 10))); }
  function lensBandStep() { return Math.max(1, Math.min(8, Math.round(imageWidth / 480))); }
  // Mirror edge_snap.point_window, rounding up where it rounds half to even.
  function lensPointWindow() { return Math.max(4, Math.round(imageWidth / 480)); }
  // Mirror snap_line_to_edges: a suggestion's samples lie along its chord, inside the band the station searches.
  function lensNearFits(s) {
    var dx = s[4][0] - s[0][0], dy = s[4][1] - s[0][1], len = Math.hypot(dx, dy);
    if (!(len >= 1)) return false;
    var tx = dx / len, ty = dy / len, reach = lensBandHalf() - lensPointWindow() - 1;
    return s.every(function(p) {
      var rx = p[0] - s[0][0], ry = p[1] - s[0][1], f = (rx * tx + ry * ty) / len;
      return f >= -0.01 && f <= 1.01 && Math.abs(ry * tx - rx * ty) <= reach;
    });
  }
  // The luma along the chord p0 -> p1, rectified so the chord runs along the middle
  // row: one column per step pixels of the chord, one row per pixel across it.
  function lensBand(p0, p1) {
    var dx = p1[0] - p0[0], dy = p1[1] - p0[1], len = Math.hypot(dx, dy);
    if (!(len >= 1)) return null;
    var step = lensBandStep(), half = lensBandHalf();
    var tx = dx / len, ty = dy / len, nx = -ty, ny = tx;
    var cols = Math.ceil(len / step) + 1, rows = 2 * half + 1;
    var c = document.createElement('canvas');
    c.width = cols; c.height = rows;
    var ctx = c.getContext('2d');
    ctx.imageSmoothingEnabled = true;
    // image (x, y) -> band (u, v): u along the chord in columns, v across it in rows. A
    // pixel's centre sits at +0.5 in canvas coordinates, in the image and in the band:
    // both halves together put each cell's centre on the pixel position the server models.
    var cx = p0[0] + 0.5, cy = p0[1] + 0.5;
    ctx.setTransform(tx / step, nx, ty / step, ny, 0.5 - (cx * tx + cy * ty) / step, 0.5 - (cx * nx + cy * ny) + half);
    ctx.drawImage(lensCanvas, 0, 0);
    var rgba = ctx.getImageData(0, 0, cols, rows).data;
    var luma = new Uint8Array(cols * rows);
    for (var i = 0, k = 0; i < luma.length; i++, k += 4) {
      luma[i] = Math.round((rgba[k] * 299 + rgba[k + 1] * 587 + rgba[k + 2] * 114) / 1000);
    }
    return { step: step, half: half, cols: cols, rows: rows, data: lensBase64(luma) };
  }
  // near ({points, polarity}) only for a suggestion just taken: its points stay on that suggestion's edge.
  function lensSnapLine(line, near) {
    if (!lensCanvas) { lensChanged(); return; }
    var band = lensBand(line.p0, line.p1);
    if (!band) { lensChanged(); return; }
    var sent = JSON.stringify([line.p0, line.p1, line.mids]);
    var body = { image_width: imageWidth, image_height: imageHeight, p0: line.p0, p1: line.p1, band: band };
    if (near) body.near = near;
    fetch('/api/wizard/lens/snap', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(function(r) {
      return r.json().then(function(data) { return { ok: r.ok, data: data }; });
    }).then(function(res) {
      // A line deleted or moved since is the operator's, not the answer's.
      if (lensLines.indexOf(line) === -1 || JSON.stringify([line.p0, line.p1, line.mids]) !== sent) return;
      if (res.ok && res.data.points && res.data.points.length === 5) {
        var pts = res.data.points.map(function(p) { return [p.x, p.y]; });
        line.p0 = pts[0];
        line.p1 = pts[4];
        lensSetMidsFromPositions(line, pts);
        line.snapped = res.data.points.map(function(p) { return !!p.snapped; });
        // A middle point that found no edge sits on the fitted curve only: it starts switched off.
        for (var j = 1; j <= 3; j++) line.mids[j - 1].on = line.snapped[j];
      }
      lensChanged();
    }).catch(function() { lensChanged(); });
  }

  // ---- rendering ----
  function lensHandle(line, i, j, px) {
    var g = pointHandle(lensPointPos(line, j), j === 0 || j === 4 ? 4 : 3.5, px);
    // Each line is one Tab stop, its first point.
    g.setAttribute('tabindex', j ? '-1' : '0');
    g.setAttribute('aria-label', 'Line ' + (i + 1) + (j === 0 ? ', start' : j === 4 ? ', end' : ', middle point ' + j + (lensPointIsOn(line, j) ? '' : ' (off)')));
    var cls = 'handle lens-point';
    if (!lensPointIsOn(line, j)) cls += ' off';
    if (!line.snapped[j]) cls += ' unsnapped';
    if (lensSelected && lensSelected.line === i && lensSelected.point === j) cls += ' selected';
    g.setAttribute('class', cls);
    g.dataset.line = i;
    g.dataset.point = j;
    return g;
  }
  function lensPointsAttr(pts) { return pts.map(function(p) { return p[0] + ',' + p[1]; }).join(' '); }
  function renderLensCandidates() {
    var g = document.getElementById('lens-candidates');
    g.innerHTML = '';
    if (lensHot >= 0 && lensOpenCandidates().indexOf(lensHot) < 0) lensHot = -1;
    g.classList.toggle('has-hot', lensHot >= 0);
    lensCandidates.forEach(function(cand, i) {
      if (cand.used || cand.rejected) return;
      var group = svgEl('g');
      group.setAttribute('class', 'lens-candidate' + (lensHot === i ? ' hot' : ''));
      group.dataset.candidate = i;
      var line = svgEl('polyline');
      line.setAttribute('class', 'lens-candidate-line');
      line.setAttribute('points', lensPointsAttr(cand.points));
      group.appendChild(line);
      g.appendChild(group);
    });
  }
  // ---- suggestion bubbles ----
  // A bubble with its pointer: 72 x 38 px, 8 px corners, an equilateral pointer as high as the gap (18 px) with a
  // 3 px rounded tip. CSS pixels over the picture.
  var LENS_BUBBLE = { w: 72, h: 38, r: 8, gap: 18, tip: 3, half: 18 / Math.sqrt(3) };
  // The point a fraction t along a polyline's length, with the unit tangent there and the whole length.
  function lensPolyAt(p, t) {
    var acc = [0];
    for (var i = 1; i < p.length; i++) acc.push(acc[i - 1] + Math.hypot(p[i][0] - p[i - 1][0], p[i][1] - p[i - 1][1]));
    var target = t * acc[acc.length - 1];
    for (var k = 1; k < p.length; k++) {
      if (acc[k] >= target) {
        var f = (target - acc[k - 1]) / Math.max(acc[k] - acc[k - 1], 1e-9), dx = p[k][0] - p[k - 1][0], dy = p[k][1] - p[k - 1][1], n = Math.hypot(dx, dy) || 1;
        return { x: p[k - 1][0] + dx * f, y: p[k - 1][1] + dy * f, tx: dx / n, ty: dy / n, len: acc[acc.length - 1] };
      }
    }
    return { x: p[p.length - 1][0], y: p[p.length - 1][1], tx: 1, ty: 0, len: acc[acc.length - 1] };
  }
  // Bubble and pointer as one closed path: the bubble's top left (x, y), the side the pointer leaves from
  // (the side of the line the bubble is on: 'top' points down), and where along that side it sits.
  function lensBubbleOutline(x, y, side, at) {
    var B = LENS_BUBBLE, w = B.w, h = B.h, r = B.r, hb = B.half, f = function(px, py) { return px.toFixed(1) + ' ' + py.toFixed(1); };
    function notch(b1, apex, b2) {
      var cut = B.tip * Math.sqrt(3) / Math.hypot(apex[0] - b1[0], apex[1] - b1[1]);
      var p1 = [apex[0] + (b1[0] - apex[0]) * cut, apex[1] + (b1[1] - apex[1]) * cut], p2 = [apex[0] + (b2[0] - apex[0]) * cut, apex[1] + (b2[1] - apex[1]) * cut];
      return ' L' + f(b1[0], b1[1]) + ' L' + f(p1[0], p1[1]) + ' A' + B.tip + ' ' + B.tip + ' 0 0 1 ' + f(p2[0], p2[1]) + ' L' + f(b2[0], b2[1]);
    }
    var d = 'M' + f(x + r, y);
    if (side === 'bottom') d += notch([at - hb, y], [at, y - B.gap], [at + hb, y]);
    d += ' L' + f(x + w - r, y) + ' A' + r + ' ' + r + ' 0 0 1 ' + f(x + w, y + r);
    if (side === 'left') d += notch([x + w, at - hb], [x + w + B.gap, at], [x + w, at + hb]);
    d += ' L' + f(x + w, y + h - r) + ' A' + r + ' ' + r + ' 0 0 1 ' + f(x + w - r, y + h);
    if (side === 'top') d += notch([at + hb, y + h], [at, y + h + B.gap], [at - hb, y + h]);
    d += ' L' + f(x + r, y + h) + ' A' + r + ' ' + r + ' 0 0 1 ' + f(x, y + h - r);
    if (side === 'right') d += notch([x, at + hb], [x - B.gap, at], [x, at - hb]);
    return d + ' L' + f(x, y + r) + ' A' + r + ' ' + r + ' 0 0 1 ' + f(x + r, y) + ' Z';
  }
  // Where each open suggestion's bubble goes, as tooltip and map-popup libraries place theirs: anchored off the
  // line's middle, on a side (above or below a flat line, beside a steep one), slid along it with the pointer kept
  // on the flat edge, flipped when there is no room. Never over its own line; clear of the other suggestions, the
  // frame's edge and the bubbles already placed, longest line first. ``lines``: screen polylines by index.
  function lensPlaceBubbles(lines, open, width, height) {
    var B = LENS_BUBBLE, pad = B.r + B.half, placed = [], out = [];
    function hits(line, b) {
      var n = 0;
      for (var k = 1; k < line.length; k++) {
        var steps = Math.max(1, Math.ceil(Math.hypot(line[k][0] - line[k - 1][0], line[k][1] - line[k - 1][1]) / 4));
        for (var s = 0; s <= steps; s++) {
          var px = line[k - 1][0] + (line[k][0] - line[k - 1][0]) * s / steps, py = line[k - 1][1] + (line[k][1] - line[k - 1][1]) * s / steps;
          if (px > b.l && px < b.r && py > b.t && py < b.b) n++;
        }
      }
      return n;
    }
    open.slice().sort(function(a, b) { return lensPolyAt(lines[b], 1).len - lensPolyAt(lines[a], 1).len; }).forEach(function(i) {
      var best = null, bestScore = Infinity;
      [0.38, 0.62, 0.3, 0.7, 0.22, 0.78, 0.15, 0.85].forEach(function(t, rank) {
        var a = lensPolyAt(lines[i], t), flat = Math.abs(a.tx) >= Math.abs(a.ty);
        var sides = flat ? ['top', 'bottom', 'left', 'right'] : ['left', 'right', 'top', 'bottom'];
        sides.forEach(function(side, k) {
          // The bubble's edge a pointer's height and 1 px from the anchor: the rounded tip stops 4 px short of it.
          var edge = B.gap + 1, opts = [];
          if (side === 'top' || side === 'bottom') {
            var y = side === 'top' ? a.y - edge - B.h : a.y + edge;
            [B.w / 2, pad, B.w - pad].forEach(function(off, si) { opts.push({ x: a.x - off, y: y, at: a.x, shift: si }); });
          } else {
            opts.push({ x: side === 'left' ? a.x - edge - B.w : a.x + edge, y: a.y - B.h / 2, at: a.y, shift: 0 });
          }
          opts.forEach(function(o) {
            var b = { l: o.x, r: o.x + B.w, t: o.y, b: o.y + B.h }, grown = { l: b.l - 4, r: b.r + 4, t: b.t - 4, b: b.b + 4 };
            var score = rank * 2 + (k >= 2 ? 12 : k) + o.shift * 3;
            if (b.l < 6 || b.t < 6 || b.r > width - 6 || b.b > height - 6) score += 100;
            if (hits(lines[i], grown)) score += 200;
            open.forEach(function(j) { if (j !== i && hits(lines[j], b)) score += 15; });
            placed.forEach(function(q) {
              var ox = Math.min(b.r, q.r) - Math.max(b.l, q.l), oy = Math.min(b.b, q.b) - Math.max(b.t, q.t);
              if (ox > -6 && oy > -6) score += 50;
              else if (ox > -30 && oy > -30) score += 6;
            });
            if (score < bestScore) { bestScore = score; best = { i: i, x: o.x, y: o.y, at: o.at, side: side, box: b }; }
          });
        });
      });
      placed.push(best.box);
      out.push(best);
    });
    return out;
  }
  var LENS_CHECK_SVG = '<svg viewBox="0 0 26 26" aria-hidden="true"><circle class="ring" cx="13" cy="13" r="11"/><path class="mark" d="M8.2 13.4 L11.6 16.7 L17.8 9.6"/></svg>';
  var LENS_CROSS_SVG = '<svg viewBox="0 0 26 26" aria-hidden="true"><circle class="ring" cx="13" cy="13" r="11"/><path class="mark" d="M9.3 9.3 L16.7 16.7 M16.7 9.3 L9.3 16.7"/></svg>';
  function renderLensBubbles() {
    var cont = $('lens-container'), box = $('lens-bubbles'), shapes = $('lens-tails'), r = cont.getBoundingClientRect();
    // Rebuilding drops a focused button: the same button of the same suggestion takes the focus back.
    var had = box.contains(document.activeElement) && document.activeElement.closest('[data-candidate]');
    var keep = lensFocusAfter || (had ? { same: +had.dataset.candidate, cls: document.activeElement.classList.contains('lb-use') ? 'lb-use' : 'lb-reject' } : null);
    lensFocusAfter = null;
    box.innerHTML = ''; shapes.innerHTML = '';
    if (!r.width || !r.height || !imageWidth) return;
    shapes.setAttribute('viewBox', '0 0 ' + r.width + ' ' + r.height);
    var sx = r.width / imageWidth, sy = r.height / imageHeight, open = lensOpenCandidates();
    var lines = lensCandidates.map(function(c) { return c.points.map(function(q) { return [q[0] * sx, q[1] * sy]; }); });
    shapes.classList.toggle('has-hot', lensHot >= 0);
    // Placed longest line first, entered in suggestion order: Tab and the focus after a choice go the same way.
    lensPlaceBubbles(lines, open, r.width, r.height).sort(function(a, b) { return a.i - b.i; }).forEach(function(b) {
      var hot = lensHot === b.i ? ' hot' : '';
      shapes.insertAdjacentHTML('beforeend', '<path class="lens-tail' + hot + '" data-candidate="' + b.i + '" d="' + lensBubbleOutline(b.x, b.y, b.side, b.at) + '"/>');
      box.insertAdjacentHTML('beforeend', '<div class="lens-bubble' + hot + '" role="group" aria-label="Suggested line ' + (b.i + 1) + '" data-candidate="' + b.i + '" style="left:' + b.x.toFixed(1) + 'px;top:' + b.y.toFixed(1) + 'px;">' +
        '<button type="button" class="lb-use" aria-label="Use this line" title="Use this line">' + LENS_CHECK_SVG + '</button>' +
        '<button type="button" class="lb-reject" aria-label="Reject this line" title="Reject this line">' + LENS_CROSS_SVG + '</button></div>');
    });
    var to = lensFocusTarget(open, keep);
    var btn = to != null && box.querySelector('[data-candidate="' + to + '"] .' + keep.cls);
    if (btn) btn.focus({ preventScroll: true });
    else if (keep && keep.same == null) lensFocusLineOrPicture(keep.cls === 'lb-use');
  }
  // The last suggestion decided by keyboard: the focus goes to the start of the line just used, else to the picture.
  function lensFocusLineOrPicture(used) {
    var h = used && lensSelected && document.querySelector('#lens-lines .lens-point[data-line="' + lensSelected.line + '"][data-point="0"]');
    lensFocusEl(h || $('lens-overlay'));
  }
  // The suggestion whose bubble takes the focus back: the same one after a redraw, else after a choice the next
  // open one, round to the first.
  function lensFocusTarget(open, keep) {
    if (!keep) return null;
    if (keep.same != null) return open.indexOf(keep.same) >= 0 ? keep.same : null;
    var to = open.filter(function(i) { return i > keep.i; })[0];
    return to != null ? to : open.length ? open[0] : null;
  }
  // Pointing at or focusing a bubble lights up that line and its pointer and dims the others.
  function lensHotCand(i) {
    if (lensHot === i) return;
    lensHot = i;
    $('lens-candidates').classList.toggle('has-hot', i >= 0);
    $('lens-tails').classList.toggle('has-hot', i >= 0);
    document.querySelectorAll('#lens-container [data-candidate]').forEach(function(e) { e.classList.toggle('hot', +e.dataset.candidate === i); });
  }

  // ---- the hint: always the one next thing to do, above the picture ----
  // A press keeps the hint's words until its click: a box changing height moves the picture and the controls,
  // and a tap's click lands on whatever is under the finger by then. A mouse's click goes where it pressed.
  var lensPress = false, lensPressTimer = null;
  function lensPressEnd() {
    clearTimeout(lensPressTimer);
    if (!lensPress) return;
    lensPress = false; renderLensHint(); renderFsBar();
  }
  // The 1-based numbers drawn on the picture of the lines that do not fit, and "Line 4 does not fit the others".
  function lensMisfitLines() { return lensLines.reduce(function(n, l, i) { return l.misfit ? n.concat(i + 1) : n; }, []); }
  function lensMisfitText(n) {
    return n.length > 1 ? 'Lines ' + n.slice(0, -1).join(', ') + ' and ' + n[n.length - 1] + ' do not fit the others' : 'Line ' + n[0] + ' does not fit the others';
  }
  // What the lines and the fit say. The misfit marks stay with their lines; the rest of the old fit no longer
  // matches the lines once they change, so its words stay until the refit answers.
  function lensLinesHint() {
    var fit = lensFit, misfit = lensMisfitLines(), many = misfit.length > 1;
    var applied = fit && lensFitApplies(fit), hint = fit && fit.hint || '', cov = hint ? ' ' + hint : '';
    var agree = /^The result may already be good\. /;
    if (misfit.length) return [lensMisfitText(misfit) + '.', (many ? 'They are' : 'It is') + ' marked in the warning colour. Move ' + (many ? 'their' : 'its') + ' points onto the edge, or delete ' + (many ? 'them if they are' : 'it if it is') + ' not straight in reality.'];
    if (lensHintLines && lensFitAsk === lensFitSeq) return lensHintLines;
    if (lensMiddleOnly(fit)) return [LENS_MIDDLE.hint, LENS_MIDDLE.hintSub];
    if (applied && (fit.rating === 'good' || fit.rating === 'excellent')) return ['The lens is measured.', /^If possible/.test(hint) ? 'Press Next. ' + hint : 'Press Next.'];
    if (applied && agree.test(hint)) return ['The result may already be good.', hint.replace(agree, '') + ' Or press Next.'];
    if (applied) return ['Improve the lines, or add more.', 'Select a point and move it exactly onto the edge. Lines near the edges and corners of the picture help the most.' + cov];
    return ['Draw more straight lines.', 'Click the start and the end of a straight edge. Then move its points exactly onto the edge.' + cov];
  }
  function renderLensHint() {
    if (lensPress) return;
    var open = lensOpenCandidates().length, lines = lensLines.length, h, sub;
    lensHintLines = lines ? lensLinesHint() : null;
    if (lensPending) { h = 'Click the other end of the straight edge.'; sub = 'Esc cancels the line.'; }
    else if (lensHold && lensSelected && lensSelected.point != null) { h = 'Move the point exactly onto the edge.'; sub = 'Drag it, or use the arrow keys while the loupe shows the edge. Press Enter when it is in the right place.'; }
    else if (open) { h = 'Check the suggested lines.'; sub = 'Confirm each of the lines that is straight in reality by pressing ✔. Reject the others, such as a curtain, a cloth or a cable, by pressing ✖. ' + open + ' left to check. Or load a saved lens calibration.'; }
    else if (!lines && S.lensLoaded) { h = 'A lens calibration is loaded.'; sub = lensHasCurrent() ? 'Press Next, or choose Clear it and measure the lens again.' : 'Press Next, or draw straight lines to measure the lens again.'; }
    else if (!lines) { h = 'Draw straight lines on the picture.'; sub = 'Click the start and the end of an edge that is straight in reality, such as a stage edge, a tape line or a truss. Then move its points exactly onto the edge. Or load a saved lens calibration.'; }
    else { h = lensHintLines[0]; sub = lensHintLines[1]; }
    // A started line keeps the box's height, so the picture stays put between the line's two taps.
    var box = $('lens-hint');
    box.style.minHeight = lensPending ? box.style.minHeight || box.offsetHeight + 'px' : '';
    setLive(box, esc(h) + '<div class="notice-sub">' + esc(sub) + '</div>');
    lensHintText = h;
  }

  function renderLens() {
    renderLensCandidates();
    var g = document.getElementById('lens-lines');
    // Rebuilding the handles drops a focused one, which would leave the keys dead after a click.
    var hadFocus = g.contains(document.activeElement);
    var px = overlayPx();
    g.innerHTML = '';
    var groups = lensLines.map(function(line, i) {
      var group = svgEl('g');
      group.setAttribute('class', 'lens-line' + (line.misfit ? ' misfit' : '') + (lensSelected && lensSelected.line === i ? ' selected' : ''));
      group.dataset.line = i;
      var halo = svgEl('polyline');
      halo.setAttribute('class', 'lens-halo');
      halo.setAttribute('points', lensPointsAttr(lensActivePoints(line)));
      group.appendChild(halo);
      if (line.curve && line.curve.length) {
        var curve = svgEl('polyline');
        curve.setAttribute('class', 'lens-curve');
        curve.setAttribute('points', lensPointsAttr(line.curve));
        group.appendChild(curve);
      }
      var chord = svgEl('polyline');
      chord.setAttribute('class', 'lens-chord');
      chord.setAttribute('points', lensPointsAttr(lensActivePoints(line)));
      group.appendChild(chord);
      group.appendChild(line.misfit ? lensTag(line, i, px) : lensLabel(line, i, px));
      // A click anywhere along the line selects it.
      var hit = svgEl('polyline');
      hit.setAttribute('class', 'lens-line-hit');
      hit.setAttribute('points', lensPointsAttr(lensActivePoints(line)));
      hit.dataset.lineHit = i;
      group.appendChild(hit);
      for (var j = 0; j < 5; j++) group.appendChild(lensHandle(line, i, j, px));
      return group;
    });
    // Tab enters at the line drawn or changed last, goes on in number order and wraps round.
    var k = lensLines.indexOf(lensLast);
    (k > 0 ? groups.slice(k).concat(groups.slice(0, k)) : groups).forEach(function(group) { g.appendChild(group); });
    renderLensBubbles();
    var pending = document.getElementById('lens-pending');
    pending.innerHTML = '';
    if (lensPending) {
      var c = svgEl('circle');
      c.setAttribute('class', 'lens-pending');
      c.setAttribute('cx', lensPending[0]);
      c.setAttribute('cy', lensPending[1]);
      c.setAttribute('r', 5 * px);
      pending.appendChild(c);
    }
    renderLensToolbar();
    renderLensHint();
    if (hadFocus) lensFocusSelected();
    if (lensHold) holdLensPoint();
    renderFsBar();
  }
  function lensLabel(line, i, px) {
    var label = svgEl('text', { class: 'lens-label', x: line.p0[0] + 8 * px, y: line.p0[1] - 8 * px, 'font-size': 11 * px });
    label.textContent = String(i + 1);
    return label;
  }
  // A pill above the line's start, in CSS pixels; a drag moves it by its transform.
  function lensTag(line, i, px) {
    var tag = svgEl('g', { class: 'lens-tag', transform: 'translate(' + line.p0[0] + ',' + line.p0[1] + ')' });
    tag.appendChild(svgEl('rect', { x: 6 * px, y: -24 * px, width: 30 * px, height: 18 * px, rx: 9 * px }));
    var text = svgEl('text', { x: 21 * px, y: -10.5 * px, 'font-size': 12 * px, 'text-anchor': 'middle' });
    text.textContent = '! ' + (i + 1);
    tag.appendChild(text);
    return tag;
  }
  // A clicked or dragged point stays selected: it keeps the focus, so the arrow keys move it, and the loupe
  // stays on it until Enter, Esc, or a press or focus anywhere else.
  function holdLensPoint() {
    var sel = lensSelected, line = sel && sel.point != null && lensLines[sel.line];
    var el = line && document.querySelector('#lens-lines .lens-point[data-line="' + sel.line + '"][data-point="' + sel.point + '"]');
    if (!el) { dropLensPoint(); return; }
    if (document.activeElement !== el) lensFocusEl(el);
    showLoupe('lens-container', 'lens-overlay', lensPointPos(line, sel.point));
  }
  function dropLensPoint() { if (!lensHold) return; lensHold = false; hideLoupe('lens-container'); renderLensHint(); renderFsBar(); }
  // A selected line without a point keeps the focus on its start.
  function lensFocusSelected() {
    if (!lensSelected) return;
    var h = document.querySelector('#lens-lines .lens-point[data-line="' + lensSelected.line + '"][data-point="' + (lensSelected.point == null ? 0 : lensSelected.point) + '"]');
    if (h) lensFocusEl(h);
  }
  function lensFocusEl(el) {
    lensRefocus = true;
    try { el.focus({ preventScroll: true }); } finally { lensRefocus = false; }
  }
  // Move the handles and chord of one line without rebuilding the DOM, so a
  // drag keeps its focus and pointer capture.
  function lensUpdateLineGeometry(i) {
    var line = lensLines[i];
    var group = document.querySelector('#lens-lines .lens-line[data-line="' + i + '"]');
    if (!group) return;
    group.querySelectorAll('.lens-point').forEach(function(h) {
      var pos = lensPointPos(line, +h.dataset.point);
      h.setAttribute('transform', 'translate(' + pos[0] + ',' + pos[1] + ')');
    });
    var pts = lensPointsAttr(lensActivePoints(line));
    ['.lens-halo', '.lens-chord', '.lens-line-hit'].forEach(function(sel) { group.querySelector(sel).setAttribute('points', pts); });
    var label = group.querySelector('.lens-label'), tag = group.querySelector('.lens-tag');
    if (label) { label.setAttribute('x', line.p0[0] + 8 * overlayPx()); label.setAttribute('y', line.p0[1] - 8 * overlayPx()); }
    if (tag) tag.setAttribute('transform', 'translate(' + line.p0[0] + ',' + line.p0[1] + ')');
  }
  function renderLensToolbar() {
    var del = document.getElementById('lens-delete-line');
    var clear = document.getElementById('lens-clear');
    if (!del) return;
    del.disabled = !lensSelected;
    clear.disabled = !lensLines.length;
    $('lens-save').disabled = !(lensMeasured() && wizLensValid());
  }
  function renderLensMeter(meter, rating, level) {
    if (!meter) return;
    meter.dataset.level = level || LENS_RATING_LEVEL[rating] || 'caution';
    var n = LENS_RATING_SEGMENTS[rating] || 0;
    meter.querySelectorAll('.wizard-meter-seg').forEach(function(seg, i) { seg.classList.toggle('filled', i < n); });
  }
  function renderLensResult() {
    var box = document.getElementById('lens-result');
    if (!box) return;
    var meter = box.querySelector('.wizard-meter');
    // A loaded calibration, until lines are traced again.
    if (!lensFit && S.lensLoaded && !lensLines.length) {
      var l = wizLens(), ld = S.lensLoaded;
      var tuned = !lensSame(l, [ld.k1, ld.k2, ld.cx, ld.cy]);
      box.style.display = ''; box.className = 'notice'; meter.style.display = 'none';
      setLive($('lens-result-text'), esc('Loaded \u201c' + ld.name + '\u201d \u2013 barrel / fisheye ' + fmt(l[0], 3) + ', corner correction ' + fmt(l[1], 3) + lensCentreSuffix([l[2], l[3]])));
      setLive($('lens-result-hint'), esc((tuned ? 'Changed by user input after loading. ' : '') + S.lensLoaded.src + '.' + (ld.kind === 'opencv' ? ' ' + lensOpencvAccuracy(ld) : '')));
      return;
    }
    meter.style.display = '';
    // One line is not fitted: the lens is measured from two.
    if (!lensFit && lensLines.length) {
      box.style.display = ''; box.className = 'notice';
      renderLensMeter(meter, '');
      meter.dataset.level = '';
      setLive($('lens-result-text'), 'Draw at least two lines.');
      setLive($('lens-result-hint'), '');
      return;
    }
    if (!lensFit) { box.style.display = 'none'; $('lens-result-text').liveHtml = $('lens-result-hint').liveHtml = null; return; }
    // A result that is not applied is a caution, whatever its rating.
    var shown = lensShownRating(lensFit), level = lensFitApplies(lensFit) ? LENS_RATING_LEVEL[shown] || 'caution' : 'caution';
    box.style.display = '';
    box.className = 'notice' + (level === 'caution' ? ' warning' : level === 'success' ? ' success' : '');
    renderLensMeter(box.querySelector('.wizard-meter'), shown, level);
    var middle = lensMiddleOnly(lensFit);
    var text = (LENS_RATING_LABEL[shown] || shown) + (middle ? LENS_MIDDLE.rating : '') + ' \u2013 barrel / fisheye ' + fmt(Number(lensFit.k1), 3)
      + ', corner correction ' + fmt(Number(lensFit.k2), 3) + (lensFit.k2_fitted ? '' : ' (estimated)') + lensCentreSuffix(lensFit.centre)
      + (lensFitApplies(lensFit) ? '' : ' \u2013 not applied');
    setLive($('lens-result-text'), esc(text));
    // The coverage hint is the hint box's alone.
    setLive($('lens-result-hint'), esc((lensHandTuned() ? 'Changed by user input after the calculation. ' : '') +
      (middle ? LENS_MIDDLE.result : lensFitApplies(lensFit) ? '' : lensFitMisfit(lensFit) ? 'It is applied once the line that does not fit is corrected or deleted.' : 'More lines near the edges give a better rating.')));
  }

  function lensFitMisfit(fit) { return (fit.lines || []).some(function(l) { return !!(l && l.misfit); }); }
  // A Not usable fit, or one with a line that does not fit the others, is shown and not applied: the lens stays as it was.
  function lensFitApplies(fit) { return fit.rating !== 'low' && !lensFitMisfit(fit); }
  // Whether the lens was changed under User input since the fit: away from the fit's pair, or from the one a fit not applied kept.
  function lensHandTuned() {
    if (!lensFit) return false;
    if (lensFitApplies(lensFit)) return !lensIsTheFits(wizLens());
    return !!lensFit.kept && !lensSame(wizLens(), lensFit.kept);
  }
  function lensSame(a, b) { return a.every(function(v, i) { return Math.abs(v - Number(b[i])) < 1e-9; }); }
  // Whether the lens [k1, k2, cx, cy] is the one the fit wrote: its pair about the centre it was sent.
  function lensIsTheFits(l) {
    if (!lensFit || !lensFitApplies(lensFit)) return false;
    var c = lensFit.centre, shown = wizLensShownPair(Number(lensFit.k1), Number(lensFit.k2), c[0], c[1]);
    return lensSame(l, [shown[0], shown[1], c[0], c[1]]);
  }
  // ", lens centre 13 px left, 9 px up" in the snapshot's pixels; nothing for a centre at 0.
  function lensCentreSuffix(c) { var t = wizLensCentreText(c, imageWidth, imageHeight); return t ? ', lens centre ' + t : ''; }

  // ---- edits ----
  // A middle point placed by hand is on the edge: it switches on. Returns whether it did, for a redraw.
  function lensMovePoint(line, j, pos) {
    lensLast = line;
    if (j === 0) line.p0 = pos;
    else if (j === 4) line.p1 = pos;
    else {
      lensSetMidFromPos(line, j, pos);
      if (!line.mids[j - 1].on) { line.mids[j - 1].on = true; return true; }
    }
    return false;
  }
  function lensChanged() {
    scheduleLensFit();
    renderLens();
    saveToSession();
  }
  window.lensDeleteSelectedLine = function() {
    if (!lensSelected) return;
    dropLensPoint();
    var hadFocus = document.getElementById('lens-lines').contains(document.activeElement);
    lensReleaseCandidate(lensLines[lensSelected.line]);
    lensLines.splice(lensSelected.line, 1);
    // Nothing stays selected, so a second Delete removes nothing; the keys stay on the picture.
    lensSelected = null;
    lensChanged();
    if (hadFocus) document.getElementById('lens-overlay').focus({ preventScroll: true });
  };
  window.lensClearLines = function() {
    dropLensPoint();
    lensLines.forEach(lensReleaseCandidate);
    lensLines = [];
    lensSelected = null;
    lensPending = null;
    lensShowNotice('');
    lensChanged();
  };

  // ---- fit ----
  function scheduleLensFit() {
    lensFitAsk = ++lensFitSeq;
    clearTimeout(lensFitTimer);
    lensFitTimer = setTimeout(runLensFit, LENS_FIT_DEBOUNCE_MS);
  }
  // A fit not applied keeps the lens in the fields, and the rating that lens had.
  function lensTakeFit(fit, centre) {
    fit.centre = centre;
    if (!lensFitApplies(fit)) { fit.kept = wizLens(); fit.keptRating = lensRatingRecord(fit.kept); }
    lensFit = fit;
  }
  function runLensFit() {
    var seq = ++lensFitSeq;
    var lines = lensLines.filter(lensLineCounts), centre = S.lensCentre.slice();
    // A line keeps its misfit mark until the answer, so a redraw before it still shows which line does not fit.
    lensLines.forEach(function(l) { l.curve = null; l.rms = null; });
    if (lines.length < 2) {
      lensFit = null;
      lensClearMisfit();
      lensShowStatus('', true);
      renderLensResult();
      renderLens();
      saveToSession();
      return;
    }
    lensFitAsk = seq;
    fetch('/api/wizard/lens/fit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        image_width: imageWidth,
        image_height: imageHeight,
        // The fit measures the pair about the lens centre in use; it never measures the centre.
        cx: centre[0],
        cy: centre[1],
        lines: lines.map(function(l) { return { points: lensActivePoints(l) }; }),
      }),
    }).then(function(r) {
      return r.json().then(function(data) { return { ok: r.ok, data: data }; });
    }).then(function(res) {
      if (seq !== lensFitSeq) return;
      lensFitAsk = null;
      if (!res.ok || !res.data || res.data.error) {
        lensFitFailed((res.data && res.data.error) || 'Could not fit the lens.');
        return;
      }
      lensShowStatus('', true);
      lensTakeFit(res.data, centre);
      lensClearMisfit();
      lines.forEach(function(l, i) {
        var r = res.data.lines[i];
        l.curve = r.curve;
        l.rms = r.rms_px;
        l.misfit = !!r.misfit;
      });
      // A Not usable fit says what the lines cannot tell: it is shown, and the pair stays as it was.
      if (lensFitApplies(lensFit)) {
        S.lensTouched = true;
        S.lensLoaded = null;
        var shown = wizLensShownPair(Number(res.data.k1), Number(res.data.k2), centre[0], centre[1]);
        wizWriteLensCoeff('wiz_lens_k1', shown[0]);
        wizWriteLensCoeff('wiz_lens_k2', shown[1]);
        // A centre the last pair could not take may suit this one; the lines are then measured about it.
        if (S.lensCentreBad && wizTakeLensCentre() && lines.length) scheduleLensFit();
        wizShowLensLimit();
      }
      renderLensResult();
      renderLens();
      if (lensFitApplies(lensFit)) onLensCoeffChanged();
      else saveToSession();
    }).catch(function() {
      if (seq !== lensFitSeq) return;
      lensFitAsk = null;
      lensFitFailed('Could not fit the lens.');
    });
  }
  function lensClearMisfit() { lensLines.forEach(function(l) { l.misfit = false; }); }
  // The lines have changed since the last fit, so nothing of it stays on screen.
  function lensFitFailed(message) {
    lensFit = null;
    lensClearMisfit();
    renderLensResult();
    renderLens();
    saveToSession();
    lensShowStatus(message, false);
  }

  // The curve a lens pair predicts for a line: the straight line through the
  // undistorted active points, bowed again. Drawn live while a slider moves.
  function lensPredictCurve(line, k1, k2, lcx, lcy) {
    var pts = lensActivePoints(line);
    if (pts.length < 2 || !wizLensIsValid(k1, k2, lcx, lcy)) return null;
    var u = pts.map(function(p) { return wizInvertDistortion(p, k1, k2, lcx, lcy); });
    var cx = 0, cy = 0;
    u.forEach(function(p) { cx += p[0]; cy += p[1]; });
    cx /= u.length; cy /= u.length;
    var sxx = 0, sxy = 0, syy = 0;
    u.forEach(function(p) { var x = p[0] - cx, y = p[1] - cy; sxx += x * x; sxy += x * y; syy += y * y; });
    var theta = 0.5 * Math.atan2(2 * sxy, sxx - syy);
    var dx = Math.cos(theta), dy = Math.sin(theta);
    var tMin = Infinity, tMax = -Infinity;
    u.forEach(function(p) { var t = (p[0] - cx) * dx + (p[1] - cy) * dy; tMin = Math.min(tMin, t); tMax = Math.max(tMax, t); });
    var out = [];
    for (var i = 0; i < 24; i++) {
      var t = tMin + (tMax - tMin) * i / 23;
      out.push(wizApplyDistortion([cx + t * dx, cy + t * dy], k1, k2, lcx, lcy));
    }
    return out;
  }
  function lensRecomputeCurves() {
    var l = wizLens();
    lensLines.forEach(function(line) { line.curve = lensLineCounts(line) ? lensPredictCurve(line, l[0], l[1], l[2], l[3]) : null; });
  }

  // A new lens pair, from the fit, a load or a slider: Camera Calibration solves with it the next time it is shown.
  function onLensCoeffChanged() {
    saveToSession();
    renderLensToolbar();
  }

  // ---- pointer + keyboard ----
  // With a point held, Tab and Shift+Tab go round the points of its line until Enter ends the hold; then Tab
  // moves on to the next line and Shift+Tab back past this one.
  function lensTabKey(e) {
    if (e.key !== 'Tab' || currentStep !== WIZ.lens) return;
    if (lensHold && lensSelected && lensSelected.point != null && lensLines[lensSelected.line]) {
      e.preventDefault(); e.stopPropagation();
      lensSelected = { line: lensSelected.line, point: (lensSelected.point + (e.shiftKey ? 4 : 1)) % 5 };
      renderLens();
      return;
    }
    var h = e.shiftKey && e.target && e.target.closest ? e.target.closest('#lens-lines .lens-point') : null;
    if (!h || +h.dataset.point === 0) return;
    // From the line's own Tab stop, focused without holding it, the browser's Shift+Tab leaves the line.
    var first = document.querySelector('#lens-lines .lens-point[data-line="' + h.dataset.line + '"][data-point="0"]');
    if (first) lensFocusEl(first);
  }
  // Enter or Esc ends a hold; the point keeps the focus and its selection, so the next Tab moves on from there.
  function lensEndHold() { dropLensPoint(); renderLens(); }
  // A point reached with Tab is selected and held, as one clicked: the loupe shows it, and Tab steps along its line.
  function lensPointFocusIn(e) {
    var h = e.target.closest ? e.target.closest('.lens-point') : null;
    if (!h || lensRefocus || lensDrag) return;
    var line = +h.dataset.line, point = +h.dataset.point;
    if (lensHold && lensSelected && lensSelected.line === line && lensSelected.point === point) return;
    lensSelected = { line: line, point: point };
    lensHold = true;
    renderLens();
  }
  // A focus that held a point redrew the points: its event still reaches here from the old, detached handle.
  function lensFocusElsewhere(e) {
    if (lensHold && e.target.isConnected && !e.target.closest('#lens-lines .lens-point')) dropLensPoint();
  }
  function lensInit() {
    var overlay = document.getElementById('lens-overlay'), traceTap = null;
    document.addEventListener('pointerdown', function() { clearTimeout(lensPressTimer); lensPress = true; }, true);
    // A tap that ends in no click (a drag) lets the hint go shortly after.
    document.addEventListener('pointerup', function(e) {
      if (e.pointerType === 'mouse') lensPressEnd(); else { clearTimeout(lensPressTimer); lensPressTimer = setTimeout(lensPressEnd, 300); }
    });
    ['click', 'pointercancel', 'contextmenu'].forEach(function(t) { document.addEventListener(t, lensPressEnd); });
    overlay.addEventListener('pointerdown', function(e) {
      traceTap = null;
      var handle = e.target.closest ? e.target.closest('.lens-point') : null;
      if (handle) {
        e.preventDefault();
        lensHold = false;
        lensSelected = { line: +handle.dataset.line, point: +handle.dataset.point };
        lensDrag = { line: lensLines[lensSelected.line], point: lensSelected.point, client: [e.clientX, e.clientY], slop: tapSlop(e.pointerType), moved: false };
        overlay.setPointerCapture(e.pointerId);
        renderLens();
        lensFocusSelected();
        showLoupe('lens-container', 'lens-overlay', lensPointPos(lensDrag.line, lensDrag.point));
      } else if (e.target.closest && e.target.closest('[data-line-hit]') && !lensPending) {
        e.preventDefault();
        lensHold = false;
        lensSelected = { line: +e.target.closest('[data-line-hit]').dataset.lineHit, point: null };
        renderLens();
      } else if (e.target.id === 'lens-hit' || (e.target.closest && e.target.closest('[data-line-hit]'))) {
        // With a line started, a click on a line ends it there.
        e.preventDefault();
        lensDrag = { trace: true, client: [e.clientX, e.clientY], slop: tapSlop(e.pointerType), moved: false };
        overlay.setPointerCapture(e.pointerId);
      }
    });
    overlay.addEventListener('pointermove', function(e) {
      if (!lensDrag) return;
      var pt = lensSvgPoint(e.clientX, e.clientY);
      var far = Math.hypot(e.clientX - lensDrag.client[0], e.clientY - lensDrag.client[1]);
      if (lensDrag.trace) { if (far >= lensDrag.slop) lensDrag.moved = true; return; }
      // A press that stays within the tap slop is a click: it selects the point and moves nothing.
      if (!lensDrag.moved && far < lensDrag.slop) return;
      lensDrag.moved = true;
      e.preventDefault();
      var switched = lensMovePoint(lensDrag.line, lensDrag.point, lensClamp(pt));
      lensUpdateLineGeometry(lensLines.indexOf(lensDrag.line));
      if (switched) renderLens();
      showLoupe('lens-container', 'lens-overlay', lensPointPos(lensDrag.line, lensDrag.point));
    });
    function endDrag(e) {
      if (!lensDrag) return;
      var drag = lensDrag;
      lensDrag = null;
      hideLoupe('lens-container');
      try { overlay.releasePointerCapture(e.pointerId); } catch (err) {}
      if (drag.trace) {
        if (!drag.moved && e.type !== 'pointercancel') traceTap = drag;
        return;
      }
      lensHold = true;
      if (drag.moved) lensChanged(); else renderLens();
    }
    overlay.addEventListener('pointerup', endDrag);
    overlay.addEventListener('pointercancel', endDrag);
    // A line starts and ends on the click, as on the Calibration picture: the hint above the picture changes,
    // and a picture moved before the click would hand the click to the control then under the finger.
    overlay.addEventListener('click', function(e) {
      var t = traceTap; traceTap = null;
      if (t && Math.hypot(e.clientX - t.client[0], e.clientY - t.client[1]) < t.slop) lensTraceClick(lensSvgPoint(e.clientX, e.clientY));
    });
    // On the HTML container: Chrome makes an SVG element with a focus listener a Tab stop of its own.
    $('lens-container').addEventListener('focusin', lensPointFocusIn);
    overlay.addEventListener('keydown', function(e) {
      var handle = e.target.closest ? e.target.closest('.lens-point') : null;
      if (!handle) return;
      var i = +handle.dataset.line, j = +handle.dataset.point, line = lensLines[i];
      if (!line) return;
      lensSelected = { line: i, point: j };
      var step = e.shiftKey ? 10 : 1, dx = 0, dy = 0;
      if (e.key === 'ArrowLeft') dx = -step;
      else if (e.key === 'ArrowRight') dx = step;
      else if (e.key === 'ArrowUp') dy = -step;
      else if (e.key === 'ArrowDown') dy = step;
      else if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault();
        if (lensPending) { lensPending = null; renderLens(); } else lensDeleteSelectedLine();
        return;
      }
      else return;
      e.preventDefault();
      var pos = lensPointPos(line, j);
      var switched = lensMovePoint(line, j, lensClamp([pos[0] + dx, pos[1] + dy]));
      lensUpdateLineGeometry(i);
      lensHold = true;
      if (switched) renderLens();
      showLoupe('lens-container', 'lens-overlay', lensPointPos(line, j));
      saveToSession();
      scheduleLensFit();
    });
    // Delete or Backspace remove the selected line wherever the focus is, unless a field is being typed in;
    // a started line is cancelled first. Enter confirms a held point; the arrows reach it after its focus fell.
    document.addEventListener('keydown', function(e) {
      if (currentStep !== WIZ.lens || !$('modal-root').hidden) return;
      var t = e.target.nodeType === 1 ? e.target : document.body;
      // A target the redraw detached was a handle whose own key handler already ran.
      if (!t.isConnected || t.matches('input, select, textarea') || t.closest('#lens-lines .lens-point')) return;
      if (e.key === 'Delete' || e.key === 'Backspace') {
        if (!lensSelected && !lensPending) return;
        e.preventDefault();
        if (lensPending) { lensPending = null; renderLens(); } else lensDeleteSelectedLine();
        return;
      }
      if (!lensHold) return;
      if (e.key === 'Enter') { e.preventDefault(); lensEndHold(); return; }
      var step = e.shiftKey ? 10 : 1, d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
      if (!d || !lensSelected || lensSelected.point == null) return;
      e.preventDefault();
      var line = lensLines[lensSelected.line], j = lensSelected.point, pos = lensPointPos(line, j);
      lensMovePoint(line, j, lensClamp([pos[0] + d[0], pos[1] + d[1]]));
      lensChanged();
    });
    document.addEventListener('keydown', function(e) {
      if (e.key !== 'Enter' || !lensHold || currentStep !== WIZ.lens) return;
      var t = e.target.closest ? e.target.closest('#lens-lines .lens-point') : null;
      if (!t) return;
      e.preventDefault(); lensEndHold();
    });
    document.addEventListener('keydown', lensTabKey, true);
    document.addEventListener('pointerdown', function(e) { if (lensHold && !e.target.closest('#lens-lines .lens-point')) dropLensPoint(); }, true);
    document.addEventListener('focusin', lensFocusElsewhere);
    // The bubbles: the check uses its suggestion, the cross rejects it; pointing at one lights up its line.
    var bubbles = $('lens-bubbles');
    bubbles.addEventListener('click', function(e) {
      var b = e.target.closest('button'), g = b && b.closest('[data-candidate]'); if (!g) return;
      var i = +g.dataset.candidate, use = b.classList.contains('lb-use');
      if (e.detail === 0) lensFocusAfter = { i: i, cls: use ? 'lb-use' : 'lb-reject' };
      if (use) lensAddCandidate(i); else lensRejectCandidate(i);
    });
    bubbles.addEventListener('mouseover', function(e) { var g = e.target.closest('[data-candidate]'); lensHotCand(g ? +g.dataset.candidate : -1); });
    bubbles.addEventListener('mouseleave', function() { lensHotCand(-1); });
    bubbles.addEventListener('focusin', function(e) { var g = e.target.closest('[data-candidate]'); if (g) lensHotCand(+g.dataset.candidate); });
    bubbles.addEventListener('focusout', function(e) { if (!e.relatedTarget || !bubbles.contains(e.relatedTarget)) lensHotCand(-1); });
    // The bubbles are placed in CSS pixels of the picture as shown, known once it has its size.
    $('lens-image').addEventListener('load', renderLensBubbles);
    wizShowLensCentre();
    wizShowLensLimit();
    renderLens();
    renderLensResult();
  }

  // ---------------------------------------------------------------
  // Lens calibrations saved on this station, and a settings file's lens values
  // ---------------------------------------------------------------
  // Whether the lens has values worth trusting: measured, loaded or typed under User input in this run, or a pair the station already had.
  function lensMeasured() {
    return S.lensTouched || (lensHasCurrent() && S.lensChoice !== 'clear');
  }
  function lensHasCurrent() { return SAVED.lens.some(function(v) { return v !== 0; }); }
  // A camera with a lens calibration asks first what to do with it; until then the rest of the step and Next are
  // hidden. Kept or loaded, the calibration is used as it is: a loaded one shows its notices, and only Clear brings
  // the picture and its controls back.
  function renderLensCurrent() {
    var cur = lensHasCurrent(), ask = cur && !S.lensChoice;
    $('lens-current').hidden = !cur;
    $('lens-current').classList.toggle('asking', ask);
    // The backup is for before the calibration changes: it goes with the first choice.
    $('lens-current-backup').hidden = !ask;
    if (cur) {
      $('lens-current-values').textContent = lensVals(SAVED.lens[0], SAVED.lens[1], SAVED.lens[2], SAVED.lens[3], imageWidth, imageHeight) + '.';
      document.querySelectorAll('input[name="lens_current"]').forEach(function(r) { r.checked = r.value === S.lensChoice; });
    }
    $('lens-notices').hidden = cur && S.lensChoice !== 'clear' && S.lensChoice !== 'load';
    $('lens-work').hidden = cur && S.lensChoice !== 'clear';
    $('lens-next').hidden = ask;
  }
  function lensCurrentInit() {
    document.querySelectorAll('input[name="lens_current"]').forEach(function(r) { r.addEventListener('change', function() { chooseLensCurrent(r.value); }); });
    renderLensCurrent();
  }
  function lensWriteAll(l) {
    wizWriteLensCoeff('wiz_lens_k1', l[0]); wizWriteLensCoeff('wiz_lens_k2', l[1]);
    wizSetLensCentre([l[2], l[3]]); wizShowLensLimit();
  }
  // Keep puts the camera's own calibration back, also after a load; Clear starts from no lens and measures the
  // lines still on the picture again. Load keeps the choice it had until a calibration is loaded.
  function chooseLensCurrent(v) {
    if (v === 'load') {
      lensLoadDialog(function() { S.lensChoice = 'load'; renderLensCurrent(); saveToSession(); });
      renderLensCurrent();
      return;
    }
    // Nothing is measured until the lines' fit answers.
    S.lensChoice = v; S.lensLoaded = null; S.lensTouched = false;
    dropLensPoint(); lensSelected = null;
    // A fit still on its way must not replace the lens chosen here.
    lensFitSeq++;
    lensWriteAll(v === 'keep' ? SAVED.lens : [0, 0, 0, 0]);
    renderLensCurrent(); renderLens(); renderLensResult(); onLensCoeffChanged();
    if (v === 'clear' && lensLines.length) lensChanged();
  }
  // A lens as its list line names it, the centre in pixels of a w x h picture.
  function lensVals(k1, k2, cx, cy, w, h) {
    var t = wizLensCentreText([cx, cy], w, h);
    return 'Barrel / fisheye ' + fmt(k1, 3) + ' · Corner correction ' + fmt(k2, 3) + (t ? ' · Lens centre ' + t : '');
  }
  function lensSize(w, h) { return w + ' \u00d7 ' + h; }
  function lensSized(c) { return c.width > 0 && c.height > 0; }
  // The pair is relative to the half-diagonal, so it carries over to another size of the same picture shape only.
  function lensShapeWarning(c) {
    if (!lensSized(c) || !imageWidth || !imageHeight) return '';
    var here = imageWidth / imageHeight;
    if (Math.abs(c.width / c.height - here) <= 0.01 * here) return '';
    return 'Measured on a ' + lensSize(c.width, c.height) + ' picture, this one is ' + lensSize(imageWidth, imageHeight) + '. The values only carry over at the same picture shape.';
  }
  // Only camera.lens_k1 / lens_k2 / lens_cx / lens_cy of an exported settings file are read; a missing centre is 0,
  // as the station reads it, and a lens the station would clamp or pull back is refused.
  function lensFromSettings(text) {
    var cfg;
    try { cfg = JSON.parse(text); } catch (e) { return { error: 'This is not an OpenFollow settings file.' }; }
    var cam = cfg && typeof cfg === 'object' ? cfg.camera : null;
    if (!cam || typeof cam !== 'object') return { error: 'This is not an OpenFollow settings file.' };
    var k1 = cam.lens_k1, k2 = cam.lens_k2;
    var cx = cam.lens_cx === undefined ? 0 : cam.lens_cx, cy = cam.lens_cy === undefined ? 0 : cam.lens_cy;
    var numbers = [k1, k2, cx, cy].every(function(v) { return typeof v === 'number' && isFinite(v); });
    if (!numbers) return { error: 'This settings file has no lens calibration.' };
    if (!wizLensIsValid(k1, k2, cx, cy)) return { error: 'With the lens values of this file, the overlay cannot follow the picture out to its corners.' };
    return { k1: k1, k2: k2, cx: cx, cy: cy };
  }
  // How closely the converted lens follows the file: typical error, and the corners' when the file reaches them.
  function lensOpencvAccuracy(c) {
    var text = 'Typical error ' + fmt(c.rms_px, 1) + ' px';
    if (c.corner_px !== null && c.corner_px !== undefined) return text + ', corners ' + fmt(c.corner_px, 1) + ' px.';
    return text + '. The file describes ' + Math.floor(100 * c.coverage) + ' % of the picture, not its corners.';
  }
  // The station converts an OpenCV calibration for this snapshot: ``{ fit }``, ``{ needsSize }`` or ``{ error }``.
  function lensFromOpencv(text, size) {
    var saveError = window.OpenFollow.saveError;
    if (!imageWidth || !imageHeight) return Promise.resolve({ error: 'An OpenCV calibration is converted for the picture. Load it once the picture is shown.' });
    var body = { xml: text, image_width: imageWidth, image_height: imageHeight };
    if (size) { body.calibration_width = size[0]; body.calibration_height = size[1]; }
    return fetch('/api/wizard/lens/opencv', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(function(r) {
      return r.json().then(function(data) {
        if (r.ok) return { fit: data };
        return data && data.needs_size ? { needsSize: true, error: data.error } : { error: (data && data.error) || saveError.UNREACHABLE.error };
      }, function() { return { error: saveError.UNREACHABLE.error }; });
    }, function() { return { error: saveError.UNREACHABLE.error }; });
  }
  function lensOption(i, name, sub, checked, warn) {
    return '<label class="tier-option"><input type="radio" name="lens_src" value="' + i + '"' + (checked ? ' checked' : '') + '><span><strong>' + esc(name) + '</strong><small>' + esc(sub) + '</small>' +
      (warn ? '<small class="caution-text">' + esc(warn) + '</small>' : '') + '</span></label>';
  }
  function lensApplyLoaded(c, src) {
    lensLines.forEach(lensReleaseCandidate);
    lensLines = []; lensSelected = null; lensPending = null; lensFit = null; lensFitSeq++;
    dropLensPoint();
    var cx = Number(c.cx), cy = Number(c.cy), shown = wizLensShownPair(Number(c.k1), Number(c.k2), cx, cy);
    wizWriteLensCoeff('wiz_lens_k1', shown[0]);
    wizWriteLensCoeff('wiz_lens_k2', shown[1]);
    wizSetLensCentre([cx, cy]);
    wizShowLensLimit();
    S.lensTouched = true;
    S.lensLoaded = { name: c.name, src: src, k1: Number(shown[0]), k2: Number(shown[1]), cx: cx, cy: cy };
    if (c.kind === 'opencv') Object.assign(S.lensLoaded, { kind: 'opencv', rms_px: c.rms_px, corner_px: c.corner_px, coverage: c.coverage });
    $('lens-saved').style.display = 'none';
    lensShowNotice('');
    renderLens(); renderLensResult(); onLensCoeffChanged();
  }
  // The saved list, or why it could not be read: ``{ list }`` or ``{ error }`` (the station's reason, '' without one).
  function lensSavedList() {
    var saveError = window.OpenFollow.saveError;
    return fetch('/api/wizard/lens/calibrations').then(function(r) {
      if (!r.ok) return saveError.fromResponse(r).then(function(info) { return { error: info.error || '' }; });
      return r.json().then(function(data) {
        return data && Array.isArray(data.calibrations) ? { list: data.calibrations } : { error: '' };
      }, function() { return { error: '' }; });
    }, function() { return { error: saveError.UNREACHABLE.error }; });
  }
  function lensListFailed(reason, saving) {
    var sub = [reason, saving ? 'If a calibration with this name is saved on this station, saving replaces it.' : ''].filter(Boolean).join(' ');
    return '<div class="notice error" id="lens-list-error" role="alert">The saved lens calibrations could not be read.' + (sub ? '<div class="notice-sub">' + esc(sub) + '</div>' : '') + '</div>';
  }
  window.lensLoadDialog = function(loaded) {
    // ``seq`` numbers every file choice and conversion; only the latest one's answer lands.
    var fromFile = null, saved = [], failed = null, pendingXml = null, seq = 0;
    function render() {
      var lines = lensLines;
      return '<div id="ll-body"><div class="lens-dlg-title">Saved on this station</div>' +
        (failed !== null ? lensListFailed(failed, false) : saved.length ? '<div class="tier-list" role="radiogroup" aria-label="Saved lens calibrations">' + saved.map(function(c, i) { return lensOption(i, c.name, lensVals(c.k1, c.k2, c.cx, c.cy, imageWidth || c.width, imageHeight || c.height) + ' · ' + c.saved + (lensSized(c) ? ' · ' + lensSize(c.width, c.height) : ''), false, lensShapeWarning(c)); }).join('') + '</div>'
          : '<p>No lens calibrations are saved on this station.</p>') +
        '<div class="lens-dlg-sep"></div><div class="lens-dlg-title">From a file</div>' +
        '<p>Only the lens values of an exported <code>.ofsettings</code> file or of an OpenCV calibration (<code>.xml</code>) are loaded.</p>' +
        '<div class="tier-list" id="ll-file-opt" hidden></div>' +
        '<div id="ll-size" hidden><label for="ll-size-w">Calibrated picture size</label>' +
        '<div class="ll-size-row"><input type="number" id="ll-size-w" min="1" step="1" aria-label="Width"> \u00d7 <input type="number" id="ll-size-h" min="1" step="1" aria-label="Height"> ' +
        '<button type="button" class="secondary" id="ll-size-btn">Convert</button></div></div>' +
        '<button type="button" class="secondary" id="ll-file-btn">Choose file&hellip;</button>' +
        '<input type="file" id="ll-file" accept=".ofsettings,.openfollowsettings,.xml" hidden>' +
        '<div class="wizard-field-error" id="ll-file-error" role="alert" hidden></div>' +
        (lines.length ? '<p style="margin-top:0.8rem;">Loading removes the ' + lines.length + ' line' + (lines.length === 1 ? '' : 's') + ' on the picture.</p>' : '') + '</div>';
    }
    function open() {
      openModal({
        title: 'Load Lens Calibration',
        bodyHTML: render(),
        footerButtons: [
          { label: 'Cancel', onClick: function() { closeModal(); } },
          { label: 'Load', kind: 'primary', onClick: function() {
            var v = (document.querySelector('input[name="lens_src"]:checked') || {}).value;
            var c = v === 'file' ? fromFile : saved[+v];
            if (!c) return;
            closeModal();
            lensApplyLoaded(c, v !== 'file' ? 'Saved on this station on ' + c.saved : c.kind === 'opencv' ? 'From the OpenCV calibration file ' + c.file : 'From the settings file ' + c.file);
            if (typeof loaded === 'function') loaded();
          } }
        ],
      });
      var ok = $('modal-footer').lastChild;
      function sync() { ok.disabled = !document.querySelector('input[name="lens_src"]:checked'); }
      sync();
      $('ll-body').onchange = function(e) {
        if (e.target.id !== 'll-file') { sync(); return; }
        var f = e.target.files[0]; if (!f) return;
        var rd = new FileReader();
        rd.onload = function() {
          var text = String(rd.result);
          seq++; fromFile = null; pendingXml = null;
          $('ll-file-opt').hidden = true; $('ll-file-opt').innerHTML = ''; $('ll-size').hidden = true; sync();
          // An XML file is an OpenCV calibration, converted by the station; anything else a settings file.
          if (/\.xml$/i.test(f.name) || /^\s*</.test(text)) { pendingXml = { text: text, file: f.name }; convert(null); }
          else show(lensFromSettings(text), f.name, null);
          e.target.value = '';
        };
        rd.readAsText(f);
      };
      function show(res, file, opencv) {
        var err = $('ll-file-error');
        err.hidden = !res.error; err.textContent = res.error || '';
        if (res.error) return;
        fromFile = { name: file.replace(/\.(ofsettings|openfollowsettings|xml)$/i, ''), file: file, k1: res.k1, k2: res.k2, cx: res.cx, cy: res.cy };
        if (opencv) Object.assign(fromFile, { kind: 'opencv', rms_px: res.rms_px, corner_px: res.corner_px, coverage: res.coverage });
        var box = $('ll-file-opt'); box.hidden = false;
        box.innerHTML = lensOption('file', file, lensVals(res.k1, res.k2, res.cx, res.cy, imageWidth, imageHeight) + (opencv ? ' · ' + lensOpencvAccuracy(res) : ''), true);
        $('ll-file-btn').textContent = 'Choose another file…';
        sync();
      }
      function convert(size) {
        var mine = ++seq, file = pendingXml.file;
        lensFromOpencv(pendingXml.text, size).then(function(got) {
          if (mine !== seq) return;
          var asking = !!got.needsSize || (!!size && !!got.error);
          $('ll-size').hidden = !asking;
          if (got.needsSize && !size) { $('ll-size-w').value = imageWidth; $('ll-size-h').value = imageHeight; }
          show(got.fit ? got.fit : { error: got.error }, file, true);
        });
      }
      $('ll-size-btn').onclick = function() {
        var w = Number($('ll-size-w').value), h = Number($('ll-size-h').value);
        if (!pendingXml) return;
        if (!(Number.isInteger(w) && Number.isInteger(h) && w > 0 && h > 0)) { show({ error: 'Enter the calibrated picture size as two whole numbers.' }, pendingXml.file, true); return; }
        convert([w, h]);
      };
      $('ll-file-btn').onclick = function() { $('ll-file').click(); };
    }
    lensSavedList().then(function(got) { saved = got.list || []; failed = got.list ? null : got.error; open(); });
  };
  // The dialog opens once the saved list is in, so the same-name warning is never missing; a list that
  // could not be read says so, and that saving may replace a calibration of that name.
  // Under the question, Save backs up the camera's own calibration, whatever the fields show by then.
  window.lensSaveCurrent = function() { lensSaveDialog(SAVED.lens, 'lens-current-saved'); };
  window.lensSaveDialog = function(current, noteId) {
    if (!current && (!lensMeasured() || !wizLensValid())) return;
    var l = current || wizLens(), saved = [], failed = null, saving = false;
    function open() {
      openModal({
        title: 'Save Lens Calibration',
        bodyHTML: '<label for="ls-name">Calibration name</label>' +
          '<input type="text" id="ls-name" maxlength="60" placeholder="Main stage – FOH wide" autocomplete="off" data-1p-ignore data-lpignore="true">' +
          '<p style="margin-top:0.7rem;">' + esc(lensVals(l[0], l[1], l[2], l[3], imageWidth, imageHeight)) + '</p>' +
          (failed !== null ? lensListFailed(failed, true) : '') +
          '<div class="notice warning" id="ls-exists" role="status" hidden>A calibration with this name is saved on this station. Saving replaces it.</div>',
        footerButtons: [
          { label: 'Cancel', onClick: function() { closeModal(); } },
          { label: 'Save', kind: 'primary', onClick: function(btn) {
            var name = $('ls-name').value.trim(); if (!name || saving) return;
            saving = true; btn.disabled = true;
            var saveError = window.OpenFollow.saveError, card = document.querySelector('#modal-root .modal-card');
            fetch('/api/wizard/lens/calibrations', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(Object.assign({ name: name, k1: l[0], k2: l[1], cx: l[2], cy: l[3] }, imageWidth && imageHeight ? { width: imageWidth, height: imageHeight } : {})),
            }).then(async function(r) {
              saving = false;
              if (!r.ok) { btn.disabled = false; saveError.show(card, await saveError.fromResponse(r), 'Not saved.'); return; }
              closeModal();
              var note = $(noteId || 'lens-saved');
              note.textContent = 'Saved “' + name + '” on this station.';
              note.style.display = '';
            }).catch(function() { saving = false; btn.disabled = false; saveError.show(card, saveError.UNREACHABLE, 'Not saved.'); });
          } }
        ],
      });
      var ok = $('modal-footer').lastChild, inp = $('ls-name');
      function sync() {
        var n = inp.value.trim().toLowerCase();
        ok.disabled = !n || saving;
        $('ls-exists').hidden = !saved.some(function(c) { return c.name.toLowerCase() === n; });
      }
      sync();
      inp.oninput = sync;
      inp.onkeydown = function(e) { if (e.key === 'Enter' && !ok.disabled) { e.preventDefault(); ok.click(); } };
    }
    lensSavedList().then(function(got) { saved = got.list || []; failed = got.list ? null : got.error; open(); });
  };

  // ---------------------------------------------------------------
  // Camera Calibration: the panels
  // ---------------------------------------------------------------
  function camUnusable(el) { var v = len(el.value); return isNaN(v) || Math.abs(v) > MARK_REACH_M; }
  function readCam() {
    var x = len($('cam_pos_x').value), y = len($('cam_pos_y').value), z = len($('cam_pos_z').value);
    S.cam = camFields().some(camUnusable) ? null : { x: x, y: y, z: z };
    S.camPartial = !S.cam && ['x', 'y', 'z'].some(function(k) { return $('cam_pos_' + k).value.trim() !== ''; });
  }
  // A typed height that cannot be used says why under the field; an empty one is no error. A number field
  // holding text it cannot read reports an empty value and badInput.
  function readHeight() {
    var el = $('cam_height'), typed = el.value.trim() !== '' || !!(el.validity && el.validity.badInput), v = len(el.value);
    var problem = !typed ? '' : isNaN(v) ? 'Enter the height of the camera\'s lens.'
      : Math.abs(v) > MARK_REACH_M ? 'Enter a height within ' + lenRough(MARK_REACH_M) + ' of the point of origin.' : '';
    S.height = typed && !problem ? v : null;
    $('cam-height-error').textContent = problem;
    $('cam-height-error').hidden = !problem;
    $('cam_height').setAttribute('aria-invalid', problem ? 'true' : 'false');
    refreshEchoes();
  }
  window.calibNext = function() {
    if ($('cal-next').disabled) return;
    readCam();
    var partial = S.camPartial;
    showCamError(partial);
    if (partial && S.mode === 'simple') $('simple-cam-box').open = true;
    if (partial) {
      renderReq();
      camFields().filter(camUnusable)[0].focus({ preventScroll: true });
      $('cal-camera').scrollIntoView({ block: 'center', behavior: 'smooth' });
      return;
    }
    wizardNext();
  };
  function camFields() { return ['x', 'y', 'z'].map(function(k) { return $('cam_pos_' + k); }); }
  // The error describes the three fields while it shows; the ones without a usable value are invalid.
  function showCamError(on) {
    $('cam-error').hidden = !on;
    camFields().forEach(function(el) {
      if (on) el.setAttribute('aria-describedby', 'cam-error'); else el.removeAttribute('aria-describedby');
      el.setAttribute('aria-invalid', on && camUnusable(el) ? 'true' : 'false');
    });
  }
  // The hint names a partly filled camera once Next showed the error or the focus left the fields, then until the
  // camera is complete or empty: the hint never moves while a value is typed.
  var camNamed = false;
  function camPartialNamed() {
    if (!S.camPartial) camNamed = false;
    else if (!$('cam-error').hidden || camFields().indexOf(document.activeElement) < 0) camNamed = true;
    return camNamed;
  }
  function camChanged() {
    readCam();
    showCamError(!$('cam-error').hidden && S.camPartial);
    if (S.mode === 'simple') placeStartQuad();
    renderReq(); solveSoon(); saveToSession();
  }
  function renderSimpleLoc() {
    $('simple-loc').innerHTML = '<option value="">Choose…</option>' + CAM_PRESETS.map(function(p) {
      return '<option value="' + p[0] + '"' + (S.simpleLoc === p[0] ? ' selected' : '') + '>' + esc(p[1]) + '</option>';
    }).join('');
  }
  // A field showing an error echoes nothing: the value is not stored.
  function refreshEchoes() {
    document.querySelectorAll('#wizard-step-calib [data-echo]').forEach(function(e) {
      var el = $(e.dataset.echo), m = el ? len(el.value) : NaN;
      e.hidden = !WUNIT.isImperial() || isNaN(m) || el.getAttribute('aria-invalid') === 'true';
      if (!e.hidden) e.textContent = 'Stored: ' + WUNIT.metricEcho(m);
    });
  }
  function applyUnitMarkup() {
    var imp = WUNIT.isImperial();
    document.querySelectorAll('.u-len').forEach(function(el) { el.textContent = lenSuffix(); });
    document.querySelectorAll('#wizard-step-calib input[data-len]').forEach(function(el) {
      el.type = imp ? 'text' : 'number';
      if (imp) el.removeAttribute('step'); else el.step = '0.01';
      if (el.dataset.ph != null) el.placeholder = lenField(Number(el.dataset.ph));
    });
    $('unit-switch').textContent = 'Switch units to ' + (imp ? 'Metric' : 'Imperial');
    refreshEchoes();
  }
  // The station's unit system, as in General; every length already typed is converted, not cleared.
  function setUnitSystem(sys) {
    var cam = ['x', 'y', 'z'].map(function(k) { return len($('cam_pos_' + k).value); });
    var height = len($('cam_height').value);
    var rect = {}; ['w', 'd', 'cx', 'cy', 'z'].forEach(function(k) { rect[k] = len(S.rect[k]); });
    var pts = S.points.map(function(p) { var o = {}; ['x', 'y', 'z', 'da', 'db'].forEach(function(k) { o[k] = p[k] == null ? NaN : len(p[k]); }); return o; });
    WUNIT.setSystem(sys);
    exactLen = {};
    // Retype the inputs first: a number input drops a ft / in value.
    applyUnitMarkup();
    ['x', 'y', 'z'].forEach(function(k, i) { if (!isNaN(cam[i])) $('cam_pos_' + k).value = lenField(cam[i]); });
    if (!isNaN(height)) $('cam_height').value = lenField(height);
    ['w', 'd', 'cx', 'cy', 'z'].forEach(function(k) { if (!isNaN(rect[k])) { S.rect[k] = lenField(rect[k]); $('rect_' + k).value = S.rect[k]; } });
    S.points.forEach(function(p, i) { ['x', 'y', 'z', 'da', 'db'].forEach(function(k) { if (!isNaN(pts[i][k])) p[k] = lenField(pts[i][k]); }); });
    refreshEchoes();
    readCam(); readHeight();
    renderCalibPanels(); solveSoon(); saveToSession();
  }
  // One Camera Position group: in Simple, optional, inside the box under the location question; in
  // Advanced, required, and the first thing asked.
  function renderCamGroup() {
    var simple = S.mode === 'simple';
    var slot = $(simple ? 'cam-slot-simple' : 'cam-slot-adv-top');
    if ($('cal-camera').parentNode !== slot) slot.appendChild($('cal-camera'));
    if (simple && (S.cam || S.camPartial)) $('simple-cam-box').open = true;
    $('cam-title').hidden = simple;
    $('cam-intro').innerHTML = simple
      ? 'Estimate the position of the camera\'s lens, measured from the point of origin, in <span class="u-len">' + esc(lenSuffix()) + '</span>: X = stage left, Y = upstage, Z = up. An estimate within about ' + esc(lenRough(1)) + ' is good enough. The calculated camera is checked against this position. This finds a skipped lens step or a wrong rectangle size.'
      : '<strong>Where is your camera?</strong> Enter the position of its lens, measured from the point of origin, in <span class="u-len">' + esc(lenSuffix()) + '</span>: X = stage left, Y = upstage, Z = up. An estimate within about ' + esc(lenRough(2)) + ' per axis is good enough.';
    $('cam-error').textContent = simple ? 'Enter all three values, or leave all three empty.' : 'Enter all three values.';
  }
  function renderCalibPanels() {
    if (S.mode !== 'advanced') closePinMenu();
    renderCamGroup();
    $('cal-simple').hidden = S.mode !== 'simple';
    $('cal-simple-corners').hidden = S.mode !== 'simple';
    $('cal-advanced').hidden = S.mode !== 'advanced';
    $('cal-advanced-points').hidden = S.mode !== 'advanced';
    $('step-calib').classList.toggle('mode-simple', S.mode === 'simple');
    $('step-calib').classList.toggle('mode-advanced', S.mode === 'advanced');
    var helpId = 'wizard-calibration-' + S.mode;
    var helpBtn = $('calib-help-btn');
    // An open drawer showing the other mode's help follows the switch.
    if (helpBtn.dataset.helpOpen !== helpId && document.body.classList.contains('help-open') && calibHelpOpen) openHelp(helpId, helpBtn);
    helpBtn.dataset.helpOpen = helpId;
    dropCorner();
    document.querySelectorAll('input[name="cal_mode"]').forEach(function(r) { r.checked = r.value === S.mode; });
    if (S.mode === 'simple') renderCornerRows(); else renderAdvRows();
  }
  var calibHelpOpen = false;

  function chipFor(m, solve) {
    if (m.problem && m.problem !== 'rect' && !(m.kind === 'triangulated' && !m.pin)) return '<span class="stat-chip off">Not placed</span>';
    if (!m.pin) return '<span class="stat-chip">Not pinned</span>';
    if (pinBehind(solve, m.id)) return '<span class="stat-chip warn">Behind the camera</span>';
    var r = solve && solve.res && solve.res[m.id];
    if (!r || r.px == null) return '<span class="stat-chip">Pinned</span>';
    var bad = r.px > FLAG_PX || (r.dist != null && r.dist > 3 * DISTANCE_SIGMA);
    return '<span class="stat-chip ' + (bad ? 'warn' : 'ok') + '">' + r.px.toFixed(1) + ' px' + (r.dist != null ? ' · ' + lenSmall(r.dist) : '') + '</span>';
  }
  function renderCornerRows() {
    var solve = S.solve, marks = currentMarks();
    $('corner-rows').innerHTML = CORNERS.map(function(c, i) {
      var chip = !S.cornerPins[c] ? '<span class="stat-chip">Waiting for size</span>' : !S.cornerMoved[c] ? '<span class="stat-chip">Not placed</span>' : chipFor(marks[i], solve).replace('>Pinned<', '>Placed<');
      var P = rectProblem() ? null : cornerXYZ(c, rectVals());
      var xyz = P ? 'X ' + lenText(P[0], 2) + ' · Y ' + lenText(P[1], 2) + ' · Z ' + lenText(P[2], 2) : 'X – · Y – · Z –';
      return '<div class="calib-row"><div class="calib-row-head"><strong>' + c + '</strong><span class="calib-fit">' + CORNER_TITLES[c] + '</span>' + chip +
        '<span class="corner-xyz">' + xyz + '</span></div></div>';
    }).join('');
  }
  var AXIS_NOTE = { x: ' <span class="axis-note">(stage left +)</span>', y: ' <span class="axis-note">(upstage +)</span>', z: ' <span class="axis-note">(height)</span>' };
  function lenInput(id, f, pid, val, ph) {
    var imp = WUNIT.isImperial(), m = len(val);
    return '<input type="' + (imp ? 'text' : 'number') + '"' + (imp ? '' : ' step="0.01"') + ' id="' + id + '" data-f="' + f + '" data-id="' + pid + '" data-len value="' + esc(val) + '"' + (ph != null ? ' placeholder="' + esc(lenField(ph)) + '"' : '') + '>' +
      (imp ? '<small class="metric-echo" data-echo="' + id + '"' + (isNaN(m) ? ' hidden' : '') + '>Stored: ' + (isNaN(m) ? '' : WUNIT.metricEcho(m)) + '</small>' : '');
  }
  // The point itself and the one chosen for the other distance are left out.
  function pointOptions(self, sel, other) {
    return '<option value="">Choose…</option>' + S.points.filter(function(p) { return p.id !== self && String(p.id) !== String(other); }).map(function(p) {
      return '<option value="' + p.id + '"' + (String(sel) === String(p.id) ? ' selected' : '') + '>' + esc(p.name || ('P' + p.id)) + '</option>';
    }).join('');
  }
  // A triangulated point's position: as the solve placed it, else from its measurements; dashes until either.
  function triXYZ(id, pre) {
    var P = S.solve && S.solve.cam && S.solve.positions && S.solve.positions[id] || pre[id];
    return P ? 'X ' + lenText(P[0], 2) + ' · Y ' + lenText(P[1], 2) + ' · Z ' + lenText(P[2], 2) : 'X – · Y – · Z –';
  }
  function renderAdvRows() {
    var solve = S.solve, marks = currentMarks(), byId = {}, pre = triPlaced(currentMarks());
    marks.forEach(function(m) { byId[m.id] = m; });
    if (solve && solve.marks) solve.marks.forEach(function(m) { if (byId[m.id]) byId[m.id].problem = m.problem; });
    // Rebuilding the rows drops the focused field: put the focus back on the field of that id.
    var focused = document.activeElement && $('adv-rows').contains(document.activeElement) ? document.activeElement.id : '';
    $('adv-rows').innerHTML = S.points.length ? S.points.map(function(p) {
      var m = byId[p.id], id = p.id, name = esc(p.name || ('P' + id));
      var head = '<div class="calib-row-head"><strong id="pn-' + id + '">' + name + '</strong><span class="stat-chip">' + (p.kind === 'known' ? 'Known point' : 'Triangulated point') + '</span><span id="chip-' + id + '">' + chipFor(m, solve) + '</span>' +
        (p.kind === 'known' ? '' : '<span class="corner-xyz" id="xyz-' + id + '">' + triXYZ(id, pre) + '</span>') +
        '<span style="margin-left:' + (p.kind === 'known' ? 'auto' : '0.6rem') + ';display:flex;gap:0.4rem;flex-wrap:wrap;">' +
        (p.pin ? '<button type="button" class="secondary small" data-unpin="' + id + '" aria-label="Remove pin of ' + name + '">Remove pin</button>' : '') +
        '<button type="button" class="secondary small" data-del="' + id + '" aria-label="Delete ' + name + '">Delete</button></span></div>';
      var f = '<div class="row"><div class="field"><label for="pl-' + id + '">Point</label><input type="text" id="pl-' + id + '" maxlength="60" autocomplete="off" data-1p-ignore data-lpignore="true" data-f="name" data-id="' + id + '" value="' + esc(p.name) + '"></div>';
      if (p.kind === 'known') {
        f += ['x', 'y', 'z'].map(function(k) { return '<div class="field"><label for="p' + k + '-' + id + '">' + k.toUpperCase() + ' (' + esc(lenSuffix()) + ')' + AXIS_NOTE[k] + '</label>' + lenInput('p' + k + '-' + id, k, id, p[k]) + '</div>'; }).join('');
        f += '</div>';
      } else {
        f += '<div class="field"><label for="pz-' + id + '">Z (' + esc(lenSuffix()) + ')' + AXIS_NOTE.z + '</label>' + lenInput('pz-' + id, 'z', id, p.z, 0) + '</div></div>';
        f += '<div class="row"><div class="field"><label for="pa-' + id + '">Measured from</label><select id="pa-' + id + '" data-f="a" data-id="' + id + '">' + pointOptions(id, p.a, p.b) + '</select></div>' +
          '<div class="field"><label for="pda-' + id + '">Distance (' + esc(lenSuffix()) + ')</label>' + lenInput('pda-' + id, 'da', id, p.da) + '</div>' +
          '<div class="field"><label for="pb-' + id + '">Measured from</label><select id="pb-' + id + '" data-f="b" data-id="' + id + '">' + pointOptions(id, p.b, p.a) + '</select></div>' +
          '<div class="field"><label for="pdb-' + id + '">Distance (' + esc(lenSuffix()) + ')</label>' + lenInput('pdb-' + id, 'db', id, p.db) + '</div></div>';
      }
      var reason = m && m.problem && m.problem !== 'rect' ? '<div class="calib-reason" id="reason-' + id + '">' + esc(m.problem) + '</div>' : '<div class="calib-reason" id="reason-' + id + '" hidden></div>';
      return '<div class="calib-row" role="group" aria-labelledby="pn-' + id + '" data-row="' + id + '">' + head + f + reason + '</div>';
    }).join('') : '<p class="wizard-help" style="margin:0.5rem 0;">No points yet.</p>';
    $('adv-add-known').disabled = S.points.length >= SAVED.max_marks;
    if (focused && $(focused)) $(focused).focus({ preventScroll: true });
    renderReq();
  }
  function updateAdvChips() {
    var solve = S.solve; if (!solve || !solve.marks) return;
    var pre = triPlaced(currentMarks());
    solve.marks.forEach(function(m) {
      var chip = $('chip-' + m.id), reason = $('reason-' + m.id), xyz = $('xyz-' + m.id);
      if (chip) chip.innerHTML = chipFor(m, solve);
      if (xyz) xyz.textContent = triXYZ(m.id, pre);
      if (reason) { var show = m.problem && m.problem !== 'rect'; reason.hidden = !show; reason.textContent = show ? m.problem : ''; }
    });
  }

  // The hint box: always the one next thing to do, above the part of the page it is about.
  var hintText = '';
  function showHint(area, h, sub) {
    var slot = $('hint-slot-' + area), moved = $('wiz-hint').parentNode !== slot;
    // Moved above another part of the page, it is read out again.
    if (moved) slot.appendChild($('wiz-hint'));
    setLive($('wiz-hint'), esc(h) + '<div class="notice-sub">' + esc(sub) + '</div>', moved);
    hintText = h;
    renderFsBar();
  }
  // Simple's four steps open in order: the location, the rectangle's size, the point of origin, the corners. A
  // rectangle already entered (a reopened Setup Wizard) counts as the first step done.
  function lockSimpleSteps() {
    var simple = S.mode === 'simple', rv = rectVals(), anyRect = ['w', 'd'].some(function(k) { return S.rect[k] !== ''; });
    var sized = rectSized(rv);
    var done1 = !!S.simpleLoc || anyRect, done2 = done1 && sized, done3 = done2 && !rectProblem();
    [['sstep-2', !done1], ['sstep-3', !done2], ['corners-q', !done3], ['cal-container', !done3], ['cal-simple-corners', !done3]].forEach(function(e) {
      var el = $(e[0]), locked = simple && e[1];
      el.classList.toggle('step-locked', locked);
      el.inert = locked;
    });
    document.querySelectorAll('#step-calib .wizard-tip.only-simple').forEach(function(el) { el.classList.toggle('step-locked', simple && !done3); });
    $('cal-refresh').disabled = simple && !done3;
    // Reset Corners only on an open step 4, once a corner has been moved.
    $('cal-reset').hidden = simple && (!done3 || !CORNERS.some(function(c) { return S.cornerMoved[c]; }));
    // Simple's Next waits for an open step 4 and every corner placed: dragged, moved with the keys, or confirmed with Enter.
    $('cal-next').disabled = simple && (!done3 || !CORNERS.every(function(c) { return S.cornerMoved[c]; }));
  }
  function renderSimpleReq() {
    var rv0 = rectVals(), diagOk = !isNaN(rv0.w) && !isNaN(rv0.d) && rv0.w > 0 && rv0.d > 0;
    // Before both sizes are in, the two diagonals only need to match; after, they must also be this long.
    $('rect-diag').innerHTML = '<strong>Optional:</strong> measure the two diagonals between opposite tape marks. ' + (diagOk
      ? 'If both are ' + esc(lenText(Math.hypot(rv0.w, rv0.d), 2)) + ', the marks are correct and form a true rectangle.'
      : 'If both are the same, you have a true rectangle.') + ' Skip this for a rectangle you know is true, such as the edges of stage decks.';
    var sized = !rectProblem(), anyRect = ['w', 'd'].some(function(k) { return S.rect[k] !== ''; });
    var todo = CORNERS.filter(function(c) { return !S.cornerMoved[c]; }), s = S.solve;
    if (!S.simpleLoc && !anyRect) showHint('sloc', 'Choose where your camera is located.', 'The location decides on which side of the picture each corner of the rectangle starts.');
    else if (camPartialNamed()) showHint('sloc', 'Enter all three values of the camera position, or leave all three empty.', 'An estimate within about ' + lenRough(1) + ' is good enough.');
    else if (!sized) {
      var rv = rectVals(), inRange = rectSized(rv), atCentre = inRange && S.rect.origin === 'custom' && [rv.cx, rv.cy, rv.z].some(isNaN);
      if (inRange && !S.rect.origin) showHint('origin-q', 'Choose the point of origin.', 'All position data that the station sends is measured from this point (0/0/0). It must match the point of origin of your console or tracking system.');
      else showHint(atCentre ? 'origin' : 'rect', anyRect ? rectProblem() : 'Enter the size of the rectangle you marked on stage.', 'Only the corners need a mark, such as a tape cross. The Setup Wizard then draws the rectangle on the picture.');
    }
    else if (todo.length) showHint('image', 'Drag ' + todo[0] + ' onto its tape mark.', CORNER_TITLES[todo[0]] + '. ' + (4 - todo.length) + ' of 4 corners are on their marks. Click a corner to select it and move it with the arrow keys. Press Enter when the crosshair is in the right place.');
    else if (s && s.cam) showHint('image', 'Check the result.', 'The grid on the picture should match the tape marks. Then press Next.');
    else if (s && s.withHeight) showHint('image', 'The camera cannot be calculated at this camera height.', 'Check the camera height, the size of the rectangle, and that each corner is on its tape mark.');
    else showHint('image', 'The camera cannot be calculated from these corners.', 'Check the size of the rectangle, and check that each corner is on its tape mark.');
  }
  function renderReq() {
    lockSimpleSteps();
    if (S.mode === 'simple') { renderSimpleReq(); return; }
    var marks = currentMarks(), solved = !!(S.solve && S.solve.cam);
    var known = marks.filter(function(m) { return m.kind === 'known' && !m.problem; });
    var tri = marks.filter(function(m) { return m.kind === 'triangulated' && (!m.problem || m.problem === PIN_PROBLEM); });
    var points = known.concat(tri), pinned = points.filter(function(m) { return m.pin; }).length;
    $('adv-add-tri').hidden = known.length < 2;
    $('adv-add-tri').disabled = S.points.length >= SAVED.max_marks;
    // A triangulated point always needs its pin; a known point only until four points are pinned.
    var toPin = points.filter(function(m) { return !m.pin && (m.kind === 'triangulated' || pinned < 4); })[0];
    var h, sub, area = 'image';
    if (!S.cam) { h = camPartialNamed() ? 'Enter all three values of the camera position.' : 'Enter the rough camera position first.'; sub = 'An estimate within about ' + lenRough(2) + ' per axis is good enough.'; area = 'cam'; }
    else if (known.length < 2) { h = 'Enter two known points that are far apart, such as the DSL and DSR corners.'; sub = 'Click where one of them is on the picture, choose New known point and type its X/Y/Z. ' + known.length + ' of 2 entered.'; }
    else if (points.length < 4) {
      h = points.length === 3 ? 'Enter one more known point or triangulated point.' : 'Enter two more known points or triangulated points.';
      sub = 'Click where it is on the picture and choose New known point or New triangulated point.';
    }
    else if (toPin) { h = 'Pin ' + toPin.name + ' on the picture.'; sub = 'Click where it is on the picture, then choose it from the menu. ' + pinned + ' of at least 4 pinned.'; }
    else if (solved) { h = 'Check the result.'; sub = 'Check the grid on the picture and the Distances to check. Then press Next. More known points or triangulated points give a better result.'; }
    else if (S.solve && S.solve.withHeight) { h = 'The camera cannot be calculated at this camera height.'; sub = 'Check the camera height, the pins and the measurements.'; }
    else { h = 'The camera cannot be calculated from these points.'; sub = 'Check the pins and the measurements.'; }
    showHint(area, h, sub);
  }

  // Every pair of placed points but the ones a triangulated point is measured from (Advanced only).
  function pairsFor(solve) {
    if (!solve || !solve.cam || S.mode === 'simple') return [];
    var marks = solve.marks.filter(function(m) { return solve.positions[m.id]; }), out = [], measured = {};
    marks.forEach(function(m) { if (m.kind === 'triangulated') { measured[m.id + '|' + m.a] = measured[m.a + '|' + m.id] = true; measured[m.id + '|' + m.b] = measured[m.b + '|' + m.id] = true; } });
    for (var i = 0; i < marks.length; i++) for (var j = i + 1; j < marks.length; j++) {
      var a = marks[i], b = marks[j];
      if (measured[a.id + '|' + b.id]) continue;
      out.push({ a: a.id, b: b.id, label: a.name + ' – ' + b.name, A: solve.positions[a.id], B: solve.positions[b.id] });
    }
    return out;
  }
  function dist3(A, B) { return Math.hypot(A[0] - B[0], A[1] - B[1], A[2] - B[2]); }
  function distTable(pairs, interactive) {
    return '<thead><tr><th>Points</th><th style="text-align:right">Distance</th></tr></thead><tbody>' + pairs.map(function(p, i) {
      return '<tr class="' + (interactive ? 'pair' : '') + (interactive && i === S.pair ? ' on' : '') + '"' + (interactive ? ' data-pair="' + i + '" tabindex="0" aria-current="' + (i === S.pair) + '"' : '') + '><td>' + esc(p.label) + '</td><td class="num">' + lenText(dist3(p.A, p.B), 2) + '</td></tr>';
    }).join('') + '</tbody>';
  }
  function solvedParamsHtml(cam) {
    return [['Pos X', lenText(cam.pos_x, 2)], ['Pos Y', lenText(cam.pos_y, 2)], ['Pos Z', lenText(cam.pos_z, 2)], ['Pitch', fmt(cam.pitch, 1) + '°'], ['Yaw', fmt(Number(cam.yaw.toFixed(1)) === -180 ? 180 : cam.yaw, 1) + '°'], ['Roll', fmt(cam.roll, 1) + '°'], ['FOV', fmt(cam.fov, 1) + '°']].map(function(p) {
      return '<div class="wizard-solved-param"><span class="param-label">' + p[0] + '</span><span class="param-value">' + p[1] + '</span></div>';
    }).join('');
  }
  // Set by a steep camera; cleared only by a camera solved without a height that is not steep.
  var heightAsked = false;
  function renderCalibResult() {
    var s = S.solve, st = $('cal-status');
    if (!s) { showLive(st, '', false); return; }
    var need = s.need && esc('To calculate the camera, add ' + s.need.join(' and ') + '.');
    // Advanced says what the solve still needs right under the picture, where the points are placed.
    showLive($('adv-need'), need, !!(S.mode === 'advanced' && need));
    if (s.waiting) { st.className = 'wizard-status waiting'; showLive(st, esc(s.waiting), true); }
    else if (need) { st.className = 'wizard-status waiting'; showLive(st, need, S.mode !== 'advanced'); }
    else if (s.error) { st.className = 'wizard-status error'; showLive(st, esc(s.error), true); }
    // A point that keeps Save and finish off makes the result an error, not a success.
    else { st.className = 'wizard-status ' + (applyBlocked(s) ? 'error' : 'ok'); showLive(st, esc('Calculated from ' + s.pinned + (S.mode === 'simple' ? ' corners' : ' pinned points') + ', largest error ' + s.worst.toFixed(1) + ' px.'), true); }
    var ok = !!s.cam;
    $('cal-solved').hidden = !ok;
    if (ok) $('cal-solved').innerHTML = solvedParamsHtml(s.cam) + '<div class="wizard-solved-param"><span class="param-label">Lens</span><span class="param-value"' +
      (lensMeasured() ? '>' + fmt(wizReadLensCoeff('wiz_lens_k1'), 3) + ' / ' + fmt(wizReadLensCoeff('wiz_lens_k2'), 3) : ' class="caution-text">Not measured') + '</span></div>';
    showLive($('cal-caution'), ok && s.reasons.length && cautionHtml(s), !!(ok && s.reasons.length));
    $('cal-lens-note').hidden = !(ok && stationLensOnly());
    if (ok && s.steep) heightAsked = true;
    else if (ok && !s.withHeight) heightAsked = false;
    renderHeightGroup();
    var pairs = pairsFor(s);
    if (S.pair >= pairs.length) S.pair = 0;
    $('cal-dist-group').hidden = !pairs.length;
    $('cal-dist-table').innerHTML = distTable(pairs, true);
    $('cal-hit').classList.toggle('idle', S.mode !== 'advanced');
  }
  // Asked while the camera looks almost straight down; the field stays while it holds text or the focus.
  function renderHeightGroup() {
    var hf = $('cam_height');
    $('cal-height').hidden = !(heightAsked || hf.value.trim() !== '' || !!(hf.validity && hf.validity.badInput) || hf === document.activeElement);
    $('cal-height-ask').hidden = !(heightAsked && S.height == null);
    showLive($('cal-height-note'), S.height != null && esc('The camera height is fixed at ' + lenText(S.height, 2) + '.'), S.height != null);
  }
  function calibGrid() {
    var g = SAVED.grid;
    if (S.mode === 'simple' && !rectProblem()) return Object.assign({ spacing: g.spacing }, rectVals());
    return { w: g.width, d: g.depth, cx: g.x_offset, cy: g.y_offset, z: g.z_offset, spacing: g.spacing };
  }
  function overlayUnit(svg) { var w = svg.getBoundingClientRect().width; return w > 0 && imageWidth ? imageWidth / w : 1; }
  function originPixel(solve) {
    if (solve && solve.cam) return projectShown(solve.cam, [0, 0, 0]);
    if (S.mode !== 'simple' || rectProblem() || !CORNERS.every(function(c) { return S.cornerPins[c]; })) return null;
    var r = rectVals(), l = wizLens();
    var q = wizHomographyPoint(CORNERS.map(function(c) { var P = cornerXYZ(c, r); return [P[0], P[1]]; }),
      CORNERS.map(function(c) { return wizInvertDistortion(S.cornerPins[c], l[0], l[1], l[2], l[3]); }), [0, 0]);
    return q && wizApplyDistortion(q, l[0], l[1], l[2], l[3]);
  }
  function originMarkup(q, u) {
    if (!q || q[0] < 0 || q[0] > imageWidth || q[1] < 0 || q[1] > imageHeight) return '';
    return '<g><circle class="origin-mark" cx="' + q[0] + '" cy="' + q[1] + '" r="' + (7 * u) + '"/>' +
      '<line class="origin-mark" x1="' + (q[0] - 14 * u) + '" y1="' + q[1] + '" x2="' + (q[0] + 14 * u) + '" y2="' + q[1] + '"/>' +
      '<line class="origin-mark" x1="' + q[0] + '" y1="' + (q[1] - 14 * u) + '" x2="' + q[0] + '" y2="' + (q[1] + 14 * u) + '"/>' +
      '<text class="origin-label" x="' + (q[0] + 12 * u) + '" y="' + (q[1] + 22 * u) + '" font-size="' + (13 * u) + '" stroke-width="' + (4 * u) + '">0/0/0</text></g>';
  }
  // A pin's name, above and right of it; one that would leave the top goes below, one that would run off the
  // picture or under the full-screen button (the top right 46 CSS px, not shown in full screen) goes left.
  function pinLabel(q, name, off, u) {
    var below = q[1] - (off[1] + 11) * u < 0, top = below ? q[1] + off[1] * u : q[1] - (off[1] + 11) * u;
    var right = q[0] + (off[0] + 9 * String(name).length) * u;
    var under = right > imageWidth || (!FS && right > imageWidth - 46 * u && top < 46 * u);
    return '<text class="pin-label" x="' + (q[0] + (under ? -off[0] : off[0]) * u) + '" y="' + (top + 11 * u) + '"' + (under ? ' text-anchor="end"' : '') +
      ' font-size="' + (14 * u) + '" stroke-width="' + (4 * u) + '">' + esc(name) + '</text>';
  }
  function renderCalibOverlay() {
    var svg = $('cal-overlay'); if (!imageWidth) return;
    svg.setAttribute('viewBox', '0 0 ' + imageWidth + ' ' + imageHeight);
    var u = overlayUnit(svg), s = S.solve, marks = currentMarks();
    $('cal-grid').innerHTML = s && s.cam ? gridLines(s.cam, calibGrid()).map(function(l) { return '<polyline class="wizard-grid-line" points="' + poly(l) + '"/>'; }).join('') : '';
    var d = '';
    pairsFor(s).forEach(function(p, i) {
      if (i !== S.pair) return;
      var line = worldPolyline(s.cam, p.A, p.B, 32), mid = line[Math.floor(line.length / 2)];
      d += '<polyline class="dist-line on" points="' + poly(line) + '"/>';
      if (mid) d += '<text class="dist-label" x="' + mid[0] + '" y="' + (mid[1] - 10 * u) + '" text-anchor="middle" font-size="' + (14 * u) + '" stroke-width="' + (4 * u) + '">' + esc(lenText(dist3(p.A, p.B), 2)) + '</text>';
    });
    $('cal-dist').innerHTML = d;
    var quad = '';
    if (S.mode === 'simple') {
      marks = marks.map(function(m) { return Object.assign({}, m, { pin: S.cornerPins[m.name], unmoved: !S.cornerMoved[m.name] }); });
      if (!(s && s.cam) && CORNERS.every(function(c) { return S.cornerPins[c]; })) quad = '<polygon class="start-quad" points="' + poly(CORNERS.map(function(c) { return S.cornerPins[c]; })) + '"/>';
    }
    var key = function(m) { return S.mode === 'simple' ? m.name : m.id; };
    // Rebuilding the pins drops a focused one; it gets the focus back, so Tab moves on from there.
    var a = document.activeElement, focused = a && a.closest && a.closest('#cal-pins .pin-handle');
    var keep = focused ? focused.getAttribute('data-mark') : null;
    $('cal-pins').innerHTML = quad + originMarkup(originPixel(s), u) + marks.map(function(m) {
      if (!m.pin) return '';
      var r = s && s.res && s.res[m.id], at = r && r.at, flag = pinFlagged(s, m.id);
      var rep = at ? '<circle class="reproj" cx="' + at[0] + '" cy="' + at[1] + '" r="' + (3 * u) + '"/>' : '';
      return '<g class="pin-handle' + (S.selPin === key(m) ? ' active' : '') + (flag ? ' flag' : '') + (m.unmoved ? ' unmoved' : '') + '" tabindex="0" role="button" data-mark="' + esc(key(m)) + '" aria-label="' + esc(m.name) + ' pin">' +
        '<circle class="pin-dot" cx="' + m.pin[0] + '" cy="' + m.pin[1] + '" r="' + (9 * u) + '"/>' +
        '<line class="pin-cross" x1="' + (m.pin[0] - 5 * u) + '" y1="' + m.pin[1] + '" x2="' + (m.pin[0] + 5 * u) + '" y2="' + m.pin[1] + '"/><line class="pin-cross" x1="' + m.pin[0] + '" y1="' + (m.pin[1] - 5 * u) + '" x2="' + m.pin[0] + '" y2="' + (m.pin[1] + 5 * u) + '"/>' +
        '<circle cx="' + m.pin[0] + '" cy="' + m.pin[1] + '" r="' + (18 * u) + '" fill="transparent"/>' + rep +
        pinLabel(m.pin, m.name, [13, 12], u) + '</g>';
    }).join('') + (S.pinMenu && !S.pinMenu.pin ? pendingMark(S.pinMenu.p, u) : '');
    if (S.selCorner != null) holdCorner();
    else if (keep != null) {
      var el = document.querySelector('#cal-pins [data-mark="' + keep + '"]');
      calRefocus = true;
      try { if (el) el.focus({ preventScroll: true }); } finally { calRefocus = false; }
    }
  }

  // ---------------------------------------------------------------
  // Simple: the drawn rectangle and its corners
  // ---------------------------------------------------------------
  // The rectangle as the starting camera sees it: a camera at the entered position, else at the chosen
  // location, else in front of the stage, looking at the rectangle's centre, so each corner starts on the
  // side of the picture where it is; fitted into the frame and bent by the lens.
  function startQuad() {
    if (rectProblem() || !imageWidth) return null;
    var r = rectVals(), centre = [r.cx, r.cy, r.z], cams = [];
    var loc = CAM_PRESETS.filter(function(q) { return q[0] === S.simpleLoc; })[0];
    if (S.cam) cams.push([S.cam.x, S.cam.y, S.cam.z]);
    if (loc) cams.push(loc[2]);
    cams.push([r.cx, r.cy - r.d / 2 - 15, r.z + 8]);
    for (var i = 0; i < cams.length; i++) {
      var la = wizLookAt(cams[i], centre), p = [cams[i][0], cams[i][1], cams[i][2], la.pitch, la.yaw, 0, SAVED.fov];
      var q = CORNERS.map(function(c) { return wizProject(p, cornerXYZ(c, r), imageWidth, imageHeight); });
      if (q.every(Boolean)) {
        var l = wizLens();
        return wizFitQuad(q, imageWidth, imageHeight).map(function(pt) { return wizApplyDistortion(pt, l[0], l[1], l[2], l[3]); });
      }
    }
    return null;
  }
  // Never more than half the picture's width and height, kept inside the frame around where it projects.
  function wizFitQuad(q, w, h) {
    var xs = q.map(function(p) { return p[0]; }), ys = q.map(function(p) { return p[1]; });
    var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs), y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
    var qw = Math.max(x1 - x0, 1), qh = Math.max(y1 - y0, 1), k = Math.min(1, w * 0.5 / qw, h * 0.5 / qh);
    if (qw < w * 0.2 && qh < h * 0.2) k = Math.min(w * 0.5 / qw, h * 0.5 / qh);
    var hw = qw * k / 2, hh = qh * k / 2;
    var mx = Math.max(hw + w * 0.05, Math.min(w - hw - w * 0.05, (x0 + x1) / 2));
    var my = Math.max(hh + h * 0.05, Math.min(h - hh - h * 0.05, (y0 + y1) / 2));
    return q.map(function(p) { return [mx + (p[0] - (x0 + x1) / 2) * k, my + (p[1] - (y0 + y1) / 2) * k]; });
  }
  function placeStartQuad() {
    var q = startQuad();
    CORNERS.forEach(function(c, i) { if (!S.cornerMoved[c]) S.cornerPins[c] = q ? q[i] : null; });
  }
  function setPin(id, p) {
    if (S.mode === 'simple') { S.cornerPins[id] = p; S.cornerMoved[id] = true; }
    else S.points.forEach(function(q) { if (q.id === id) q.pin = p; });
  }
  function getPin(id) {
    if (S.mode === 'simple') return S.cornerPins[id];
    var q = S.points.filter(function(x) { return x.id === id; })[0]; return q && q.pin;
  }
  function clearCorners() {
    S.cornerPins = { DSL: null, DSR: null, USR: null, USL: null };
    S.cornerMoved = { DSL: false, DSR: false, USR: false, USL: false };
    placeStartQuad();
  }
  // A clicked or dragged corner stays selected: it keeps the focus, so the arrow keys move it, and the loupe
  // stays on it until something else is pressed or focused.
  function selectCorner(id) { S.selCorner = id; S.selPin = id; renderCalibOverlay(); }
  function holdCorner() {
    var el = document.querySelector('#cal-pins [data-mark="' + S.selCorner + '"]'), q = getPin(S.selCorner);
    if (!el || !q) { S.selCorner = null; return; }
    if (document.activeElement !== el) el.focus({ preventScroll: true });
    showLoupe('cal-container', 'cal-overlay', q);
  }
  // Ending the hold ends the selection, as Enter places only a selected corner; the corner keeps the focus.
  function dropCorner() {
    if (S.selCorner == null) return;
    var c = S.selCorner, el = document.querySelector('#cal-pins [data-mark="' + c + '"]');
    S.selCorner = null; if (S.selPin === c) S.selPin = null;
    if (el) el.classList.remove('active');
    hideLoupe('cal-container');
  }
  var calRefocus = false;
  // Enter places the selected corner where it is; the corner keeps the focus, so the next Tab moves on from it.
  function cornerEnterKey(e) {
    if (e.key !== 'Enter' || S.selCorner == null) return;
    e.preventDefault();
    var c = S.selCorner; dropCorner();
    if (!S.cornerMoved[c]) { S.cornerMoved[c] = true; solveSoon(); saveToSession(); }
  }

  // ---------------------------------------------------------------
  // Advanced: a click on the picture asks which unpinned point goes there, or makes a new point there
  // ---------------------------------------------------------------
  // The spot of an open menu; a panel away from the picture adds a crosshair, as no menu points at it.
  function pendingMark(p, u) {
    var x = p[0], y = p[1], ring = '<circle class="pin-pending" cx="' + x + '" cy="' + y + '" r="' + (9 * u) + '"/>';
    if (!$('pin-menu').classList.contains('as-panel')) return ring;
    var a = 4 * u, b = 14 * u;
    return ring + '<path class="pin-pending-cross" d="M' + (x - b) + ' ' + y + 'H' + (x - a) + 'M' + (x + a) + ' ' + y + 'H' + (x + b) +
      'M' + x + ' ' + (y - b) + 'V' + (y - a) + 'M' + x + ' ' + (y + a) + 'V' + (y + b) + '"/>';
  }
  function pinMenuHead(title) {
    $('pin-menu').setAttribute('aria-label', title);
    return '<div class="pin-menu-head"><div class="pin-menu-title">' + esc(title) + '</div><button type="button" class="pm-close" data-pm="close" aria-label="Close">&times;</button></div>';
  }
  // A gesture is one press and its click, or one key from down to up. The menu ignores the clicks of the
  // gesture that opened it: opened under a finger, it would take that tap's click.
  var gestureSeq = 0, menuGesture = -1, pressSeq = -1;
  function openPinMenu(p) {
    S.pinMenu = { p: p, form: null, pin: null }; S.selPin = null; menuGesture = gestureSeq;
    var knownN = currentMarks().filter(function(m) { return m.kind === 'known' && !m.problem; }).length;
    var open = S.points.filter(function(q) { return !q.pin; }), full = S.points.length >= SAVED.max_marks;
    var m = $('pin-menu');
    m.classList.remove('form');
    m.innerHTML = pinMenuHead('Which point is here?') + open.map(function(q) {
      return '<button type="button" class="pm-item" data-place="' + q.id + '"><span>' + esc(q.name || ('P' + q.id)) + '</span><span class="kind">' + (q.kind === 'known' ? 'Known point' : 'Triangulated point') + '</span></button>';
    }).join('') + (open.length ? '<hr>' : '') +
      '<button type="button" class="pm-item" data-place="new-known"' + (full ? ' disabled' : '') + '><span>New known point</span><span class="kind">X/Y/Z</span></button>' +
      '<button type="button" class="pm-item" data-place="new-tri"' + (knownN < 2 || full ? ' disabled' : '') + '><span>New triangulated point</span>' + (knownN < 2 ? '<span class="kind">Needs two known points</span>' : '') + '</button>';
    m.hidden = false;
    positionPinMenu(true);
    renderCalibOverlay();
    setTimeout(function() { var first = m.querySelector('.pm-item:not(:disabled)'); if (first) first.focus({ preventScroll: true }); });
  }
  // A click on a pin: fix a misclick by removing the pin or deleting the point.
  function openPointMenu(id) {
    var q = S.points.filter(function(x) { return x.id === id; })[0]; if (!q || !q.pin) return;
    S.pinMenu = { p: q.pin, form: null, pin: id }; S.selPin = id; menuGesture = gestureSeq;
    var m = $('pin-menu');
    m.classList.remove('form');
    m.innerHTML = pinMenuHead((q.name || ('P' + id)) + (q.kind === 'known' ? ' · Known point' : ' · Triangulated point')) +
      '<button type="button" class="pm-item" data-pm="unpin"><span>Remove pin</span><span class="kind">Keeps the point</span></button>' +
      '<button type="button" class="pm-item danger" data-pm="delete"><span>Delete point</span></button>';
    m.hidden = false;
    positionPinMenu(true);
    renderCalibOverlay();
    // The menu takes the focus, not Remove pin: Enter then keeps a pin the arrow keys moved.
    setTimeout(function() { m.focus({ preventScroll: true }); });
  }
  // With the menu open, the loupe stays on its spot; arrow keys move the spot (or the pin) a pixel at a time.
  function menuLoupe() {
    var m = $('pin-menu');
    if (S.pinMenu) showLoupe('cal-container', 'cal-overlay', S.pinMenu.p, m.classList.contains('as-panel') ? null : m);
  }
  function nudgeMenuSpot(d) {
    var q = [Math.max(0, Math.min(imageWidth - 1, S.pinMenu.p[0] + d[0])), Math.max(0, Math.min(imageHeight - 1, S.pinMenu.p[1] + d[1]))];
    S.pinMenu.p = q;
    if (S.pinMenu.pin) { setPin(S.pinMenu.pin, q); solveSoon(); saveToSession(); }
    renderCalibOverlay(); menuLoupe();
  }
  // The popup needs a picture of at least 480 x 360 CSS px; on a smaller one (a phone) the menu is a panel.
  function pinMenuAsPanel(rect) { return rect.width < 480 || rect.height < 360; }
  // The one menu moves between the picture, the panel slot under it and, in full screen, the shell's bottom.
  // ``reveal`` scrolls the menu into view: on opening, and when it changes content (a panel also when it moves).
  function positionPinMenu(reveal) {
    var m = $('pin-menu'), p = S.pinMenu.p, cont = $('cal-container');
    var rect = cont.getBoundingClientRect(), panel = pinMenuAsPanel(rect), home = !panel ? cont : FS ? $('fs-shell') : $('pin-menu-slot');
    var moved = m.parentNode !== home;
    if (moved) {
      // Moving an element drops its focus.
      var a = m.contains(document.activeElement) ? document.activeElement : null;
      if (panel) home.appendChild(m); else cont.insertBefore(m, cont.querySelector('.fs-btn'));
      if (a) a.focus({ preventScroll: true });
    }
    m.classList.toggle('as-panel', panel); m.classList.toggle('as-sheet', panel && !!FS);
    if (FS && (panel ? m.offsetHeight : 0) !== FS.pad) { fsLayout(); return; }
    if (moved) renderCalibOverlay();
    if (panel) {
      m.style.left = ''; m.style.top = '';
      if ((reveal || moved) && !FS) m.scrollIntoView({ block: 'nearest' });
      menuLoupe();
      return;
    }
    var k = rect.width / imageWidth, x = p[0] * k + 12, y = p[1] * k + 12;
    if (x + m.offsetWidth > rect.width - 8) x = p[0] * k - m.offsetWidth - 12;
    if (y + m.offsetHeight > rect.height - 8) y = Math.max(8, p[1] * k - m.offsetHeight - 12);
    m.style.left = Math.max(8, x) + 'px'; m.style.top = y + 'px';
    if (reveal && !FS) m.scrollIntoView({ block: 'nearest' });
    menuLoupe();
  }
  function pinMenuForm(kind) {
    S.pinMenu.form = kind;
    var m = $('pin-menu'), name = 'P' + S.nextId, u = esc(lenSuffix()), f;
    if (kind === 'known') {
      f = ['x', 'y', 'z'].map(function(k) {
        return '<div class="field"><label for="pm-' + k + '">' + k.toUpperCase() + ' (' + u + ')' + AXIS_NOTE[k] + '</label>' + lenInput('pm-' + k, k, 'new', k === 'z' ? lenField(0) : '') + '</div>';
      }).join('');
    } else {
      f = ['a', 'b'].map(function(k) {
        return '<div class="pm-pair"><div class="field"><label for="pm-' + k + '">Measured from</label><select id="pm-' + k + '">' + pointOptions(null, '') + '</select></div>' +
          '<div class="field"><label for="pm-d' + k + '">Distance (' + u + ')</label>' + lenInput('pm-d' + k, 'd' + k, 'new', '') + '</div></div>';
      }).join('') + '<div class="field"><label for="pm-z">Z (' + u + ')' + AXIS_NOTE.z + '</label>' + lenInput('pm-z', 'z', 'new', '', 0) + '</div>';
    }
    m.classList.add('form');
    m.innerHTML = pinMenuHead(kind === 'known' ? 'New known point' : 'New triangulated point') +
      '<form class="pm-form" novalidate><div class="field"><label for="pm-point">Point</label><input type="text" id="pm-point" maxlength="60" autocomplete="off" data-1p-ignore data-lpignore="true" value="' + esc(name) + '"></div>' + f +
      '<div class="pm-actions"><button type="button" class="secondary small" data-pm="back">Back</button><button type="submit" class="btn-primary small" id="pm-ok" disabled>Place point</button></div></form>';
    positionPinMenu(true);
    pinMenuCheck();
    var n = $('pm-point'); n.focus({ preventScroll: true }); n.select();
  }
  function pinMenuVals() {
    var v = function(id) { var el = $(id); return el ? el.value : ''; };
    return { name: v('pm-point').trim(), x: v('pm-x'), y: v('pm-y'), z: v('pm-z'), a: v('pm-a'), b: v('pm-b'), da: v('pm-da'), db: v('pm-db') };
  }
  // A field's change event may come after its menu closed.
  function pinMenuCheck(e) {
    if (!S.pinMenu) return false;
    var v = pinMenuVals(), ok;
    if (e && (e.target.id === 'pm-a' || e.target.id === 'pm-b')) {
      var o = e.target.id === 'pm-a' ? 'b' : 'a';
      $('pm-' + o).innerHTML = pointOptions(null, v[o], e.target.value);
    }
    if (S.pinMenu.form === 'known') ok = [v.x, v.y, v.z].every(function(t) { return !isNaN(len(t)); });
    else ok = v.a !== '' && v.b !== '' && v.a !== v.b && len(v.da) > 0 && len(v.db) > 0 && (v.z === '' || !isNaN(len(v.z)));
    $('pm-ok').disabled = !ok;
    refreshEchoes();
    return ok;
  }
  function placeNewFromMenu() {
    if (!pinMenuCheck()) return;
    var v = pinMenuVals(), p = S.pinMenu.p, id = S.nextId++, name = v.name || ('P' + id);
    S.points.push(S.pinMenu.form === 'known'
      ? { id: id, name: name, kind: 'known', x: v.x, y: v.y, z: v.z, pin: p }
      : { id: id, name: name, kind: 'triangulated', a: +v.a, b: +v.b, da: v.da, db: v.db, z: v.z, pin: p });
    S.selPin = id; closePinMenu(id);
    renderAdvRows(); solveSoon(); saveToSession();
  }
  // With `back` (a pin id, or null for none), a focus in the menu goes to that pin, or to the picture without it.
  // A pin's menu closed without giving the focus back leaves its pin unselected. `held` leaves the picture as
  // drawn: a press closing the menu must not rebuild the pin under it, as a detached pin gets no click.
  function closePinMenu(back, held) {
    if (!S.pinMenu) return;
    var had = back !== undefined && $('pin-menu').contains(document.activeElement);
    if (S.pinMenu.pin != null && !had) S.selPin = null;
    S.pinMenu = null; $('pin-menu').hidden = true;
    hideLoupe('cal-container');
    if (held) return;
    if (FS && FS.pad) fsLayout();
    renderCalibOverlay();
    if (had) focusCalPin(back);
  }
  function focusCalPin(id) {
    var h = id != null && document.querySelector('#cal-pins [data-mark="' + id + '"]');
    (h || $('cal-overlay')).focus({ preventScroll: true });
  }
  function placeFromMenu(what) {
    if (what === 'new-known' || what === 'new-tri') { pinMenuForm(what === 'new-known' ? 'known' : 'tri'); return; }
    setPin(+what, S.pinMenu.p); S.selPin = +what; closePinMenu(+what);
    renderAdvRows(); solveSoon(); saveToSession();
  }
  function deletePoint(id) {
    // A point measured from it keeps the id, so its row says "One of its points was deleted."
    S.points = S.points.filter(function(q) { return q.id !== id; });
  }
  window.calibAddPoint = function(kind) {
    if (S.points.length >= SAVED.max_marks) return;
    var id = S.nextId++;
    S.points.push(kind === 'known' ? { id: id, name: 'P' + id, kind: 'known', x: '', y: '', z: lenField(0), pin: null } : { id: id, name: 'P' + id, kind: 'triangulated', a: '', b: '', da: '', db: '', z: '', pin: null });
    renderAdvRows(); solveSoon(); saveToSession();
    var el = $('pl-' + id); if (el) el.focus();
  };
  window.calibResetPins = function() {
    // Simple hides the button with no corner moved: its focus goes to the picture.
    var had = S.mode === 'simple' && document.activeElement === $('cal-reset');
    if (S.mode === 'simple') clearCorners();
    else { S.points.forEach(function(p) { p.pin = null; }); renderAdvRows(); }
    S.selPin = null; solveSoon(); saveToSession();
    if (had) $('cal-overlay').focus({ preventScroll: true });
  };
  function switchMode(to) {
    if (to === S.mode) return;
    if (to === 'advanced') {
      // Every corner becomes a known point; an axis the rectangle does not give yet stays empty for its row to ask.
      var r = rectVals(), has = [r.w > 0 && r.w <= 200 && !isNaN(r.cx), r.d > 0 && r.d <= 200 && !isNaN(r.cy), !isNaN(r.z)];
      S.nextId = 1;
      S.points = CORNERS.map(function(c) {
        var P = cornerXYZ(c, r), v = function(i) { return has[i] ? lenField(P[i]) : ''; };
        return { id: S.nextId++, name: c, kind: 'known', x: v(0), y: v(1), z: v(2), pin: S.cornerMoved[c] ? S.cornerPins[c] : null };
      });
      S.mode = 'advanced'; S.selPin = null; S.pair = 0; S.solve = null;
      renderCalibPanels(); solveSoon(); saveToSession();
      return;
    }
    if (!S.points.length) { S.mode = 'simple'; S.solve = null; clearCorners(); renderCalibPanels(); solveSoon(); saveToSession(); return; }
    document.querySelectorAll('input[name="cal_mode"]').forEach(function(x) { x.checked = x.value === 'advanced'; });
    openModal({
      title: 'Switch to Simple?',
      bodyHTML: '<p>Simple uses the four corners of a rectangle. Switching clears all ' + S.points.length + ' points and their pins.</p>',
      footerButtons: [
        { label: 'Cancel', onClick: function() { closeModal(); } },
        { label: 'Clear points and switch', kind: 'danger', onClick: function() {
          closeModal();
          S.points = []; S.mode = 'simple'; clearCorners(); S.selPin = null; S.pair = 0; S.solve = null;
          renderCalibPanels(); solveSoon(); saveToSession();
        } }
      ],
    });
  }

  // ---------------------------------------------------------------
  // Dragging on the picture
  // ---------------------------------------------------------------
  function bindDrag(svgId, container, hitSel, opts) {
    var svg = $(svgId), drag = null, press = null, pinTap = null;
    function far(e, t) { return Math.hypot(e.clientX - t.x, e.clientY - t.y) >= t.slop; }
    svg.addEventListener('pointerdown', function(e) {
      var h = e.target.closest('.pin-handle');
      var p = svgPoint(svg, e);
      press = null; pinTap = null;
      if (h) {
        e.preventDefault();
        drag = opts.start(h, p, tapSlop(e.pointerType));
        if (drag) { svg.setPointerCapture(e.pointerId); showLoupe(container, svgId, drag.pos()); }
        return;
      }
      if (e.target.closest(hitSel) || e.target.matches(hitSel)) press = { p: p, x: e.clientX, y: e.clientY, slop: tapSlop(e.pointerType), moved: false };
    });
    // A press on the picture or a pin acts on its click: a menu opened by the press or the release would sit
    // under a finger and take the tap's click. A press that moved is a drag, not a click.
    svg.addEventListener('click', function(e) {
      var t = press, a = pinTap; press = null; pinTap = null;
      if (a) a();
      else if (t && !t.moved && !far(e, t)) opts.tap(t.p, e);
    });
    svg.addEventListener('pointermove', function(e) {
      var p = svgPoint(svg, e);
      if (press && far(e, press)) press.moved = true;
      if (drag) { drag.move(p); showLoupe(container, svgId, drag.pos()); return; }
      if (opts.hover) opts.hover(p);
    });
    // A released press's end may hand back what its click does.
    function end(e) { if (!drag) return; hideLoupe(container); var d = drag, up = e.type === 'pointerup'; drag = null; var a = d.end(up); pinTap = up && a || null; }
    svg.addEventListener('pointerup', end);
    svg.addEventListener('pointercancel', end);
    svg.addEventListener('pointerleave', function() { if (!drag && opts.leave) opts.leave(); });
    svg.addEventListener('keydown', function(e) {
      var h = e.target.closest('.pin-handle'); if (!h) return;
      var step = e.shiftKey ? 10 : 1, d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
      if (d) { e.preventDefault(); opts.nudge(h, d); }
      else if (e.key === 'Enter' && opts.enter && opts.enter(h)) e.preventDefault();
    });
  }
  function calibInit() {
    renderSimpleLoc();
    $('simple-loc').addEventListener('change', function(e) { S.simpleLoc = e.target.value; clearCorners(); S.selPin = null; renderReq(); solveSoon(); saveToSession(); });
    ['cam_pos_x', 'cam_pos_y', 'cam_pos_z'].forEach(function(id) { $(id).addEventListener('input', camChanged); });
    $('cam_height').addEventListener('input', function() { readHeight(); renderCalibResult(); solveSoon(); saveToSession(); });
    // An empty field the camera does not ask for goes once the focus leaves it; only the field's group is
    // drawn, as a rebuilt control would lose the focus Tab is moving to it.
    document.addEventListener('focusout', function(e) {
      if (e.target === $('cam_height')) renderHeightGroup();
      if (camFields().indexOf(e.target) >= 0 && camFields().indexOf(e.relatedTarget) < 0) renderReq();
    });
    $('unit-switch').addEventListener('click', function() {
      var to = WUNIT.isImperial() ? 'metric' : 'imperial', box = document.querySelector('#wizard-step-calib .section');
      var body = new FormData(); body.append('unit_system', to);
      fetch('/settings/unit-system', { method: 'POST', body: body }).then(async function(r) {
        if (!r.ok) { window.OpenFollow.saveError.show(box, await window.OpenFollow.saveError.fromResponse(r), 'Not changed.', $('unit-switch')); return; }
        window.OpenFollow.saveError.clear(box);
        setUnitSystem(to);
      }).catch(function() { window.OpenFollow.saveError.show(box, window.OpenFollow.saveError.UNREACHABLE, 'Not changed.', $('unit-switch')); });
    });
    $('calib-help-btn').addEventListener('click', function() { calibHelpOpen = true; });
    document.querySelectorAll('[data-help-open]').forEach(function(b) { if (b.id !== 'calib-help-btn') b.addEventListener('click', function() { calibHelpOpen = false; }); });
    document.addEventListener('input', function(e) { if (e.target.matches && e.target.matches('#wizard-step-calib input[data-len]')) refreshEchoes(); });
    document.querySelectorAll('input[name="cal_mode"]').forEach(function(r) { r.addEventListener('change', function() { switchMode(r.value); }); });
    ['w', 'd', 'cx', 'cy', 'z'].forEach(function(k) { $('rect_' + k).addEventListener('input', function() { S.rect[k] = $('rect_' + k).value; placeStartQuad(); solveSoon(); renderReq(); saveToSession(); }); });
    $('rect_origin').addEventListener('change', function() {
      S.rect.origin = $('rect_origin').value;
      $('rect-custom').hidden = S.rect.origin !== 'custom';
      // Custom starts from the default, so switching alone moves nothing.
      if (S.rect.origin === 'custom' && S.rect.cx === '' && S.rect.cy === '' && S.rect.z === '') {
        var d = len(S.rect.d);
        S.rect.cx = lenField(0); S.rect.cy = isNaN(d) ? '' : lenField(d / 2); S.rect.z = lenField(0);
        ['cx', 'cy', 'z'].forEach(function(k) { $('rect_' + k).value = S.rect[k]; });
        refreshEchoes();
      }
      placeStartQuad(); solveSoon(); renderReq(); saveToSession();
    });
    $('adv-rows').addEventListener('click', function(e) {
      var u = e.target.closest('[data-unpin]'), d = e.target.closest('[data-del]');
      if (u) { setPin(+u.dataset.unpin, null); renderAdvRows(); solveSoon(); saveToSession(); $('adv-rows').querySelector('[data-del="' + u.dataset.unpin + '"]').focus(); }
      if (d) {
        // The focus goes to the next row, else the one before, else to adding a point.
        var i = S.points.map(function(p) { return p.id; }).indexOf(+d.dataset.del);
        deletePoint(+d.dataset.del); renderAdvRows(); solveSoon(); saveToSession();
        var to = S.points[i] || S.points[i - 1], row = to && $('adv-rows').querySelector('[data-row="' + to.id + '"]');
        (row ? row.querySelector('button, input, select') : $('adv-add-known')).focus();
      }
    });
    $('adv-rows').addEventListener('input', function(e) {
      var t = e.target, id = +t.dataset.id, f = t.dataset.f; if (!f) return;
      S.points.forEach(function(p) { if (p.id === id) p[f] = (f === 'a' || f === 'b') ? (t.value === '' ? '' : +t.value) : t.value; });
      if (f === 'a' || f === 'b') renderAdvRows();
      solveSoon(); saveToSession();
    });
    $('adv-rows').addEventListener('change', function(e) { if (e.target.dataset.f === 'name') { renderAdvRows(); renderCalibOverlay(); } });
    bindDrag('cal-overlay', 'cal-container', '#cal-hit', {
      start: function(h, p, tap) {
        var id = S.mode === 'simple' ? h.dataset.mark : +h.dataset.mark, stale = !!S.pinMenu;
        closePinMenu(undefined, true); S.selPin = id;
        // A press on a pin that does not move it is a click: Simple selects the corner, Advanced opens the pin's menu on the click.
        var moved = false, slop = tap * imageWidth / $('cal-container').getBoundingClientRect().width;
        return {
          pos: function() { return getPin(id); },
          move: function(q) { if (!moved && Math.hypot(q[0] - p[0], q[1] - p[1]) < slop) return; moved = true; setPin(id, q); renderCalibOverlay(); },
          end: function(released) {
            if (S.mode === 'simple') { S.selCorner = id; if (moved) { solveSoon(); saveToSession(); } else selectCorner(id); return; }
            if (!moved && released) return function() { openPointMenu(id); };
            // The menu the press closed leaves the picture now; the click's own menu draws it otherwise.
            if (stale) { if (FS && FS.pad) fsLayout(); renderCalibOverlay(); }
            if (moved) { solveSoon(); saveToSession(); focusCalPin(id); }
          }
        };
      },
      tap: function(p, e) {
        // A double-click's second click repeats its first; the menu that one opened may have scrolled the page under it.
        if (e.detail > 1) return;
        // While a new point is being typed, a click on the picture moves where it goes.
        if (S.pinMenu && S.pinMenu.form) { S.pinMenu.p = p; positionPinMenu(); renderCalibOverlay(); return; }
        if (S.mode === 'advanced') openPinMenu(p);
      },
      // Advanced: the loupe follows the pointer over the picture, not only while dragging.
      hover: function(p) { if (S.mode === 'advanced' && !S.pinMenu) showLoupe('cal-container', 'cal-overlay', p); },
      leave: function() { if (!S.pinMenu && S.selCorner == null) hideLoupe('cal-container'); },
      enter: function(h) { if (S.mode !== 'advanced') return false; openPointMenu(+h.dataset.mark); return true; },
      nudge: function(h, d) {
        var id = S.mode === 'simple' ? h.dataset.mark : +h.dataset.mark, q = getPin(id); S.selPin = id;
        if (S.mode === 'simple') S.selCorner = id;
        setPin(id, [Math.max(0, Math.min(imageWidth - 1, q[0] + d[0])), Math.max(0, Math.min(imageHeight - 1, q[1] + d[1]))]);
        solveSoon(); saveToSession();
        var el = document.querySelector('#cal-pins [data-mark="' + id + '"]'); if (el) el.focus();
      }
    });
    document.addEventListener('pointerdown', function(e) { pointerIsMouse = e.pointerType === 'mouse'; }, true);
    // A press elsewhere may leave the focus on nothing, which no focusin reports. A panel stays open through a
    // press (a swipe), and so does its pin's selection; closing it drops that.
    document.addEventListener('pointerdown', function(e) {
      if (e.target.closest('#cal-pins .pin-handle, #pin-menu') || (S.pinMenu && $('pin-menu').classList.contains('as-panel'))) return;
      dropCorner();
      if (S.selPin != null) { S.selPin = null; renderCalibOverlay(); }
    }, true);
    // A focus that selected a corner redrew the pins: its event still reaches here from the old, detached handle.
    document.addEventListener('focusin', function(e) { if (S.selCorner != null && e.target.isConnected && !e.target.closest('#cal-pins .pin-handle')) dropCorner(); });
    // A pin shows as selected only while the focus is on a pin or in its menu.
    document.addEventListener('focusin', function(e) { if (S.selPin != null && e.target.isConnected && !e.target.closest('#cal-pins .pin-handle, #pin-menu')) { S.selPin = null; renderCalibOverlay(); } });
    // A corner reached with Tab is selected, as one clicked; a redraw giving the focus back selects nothing.
    // On the HTML container: Chrome makes an SVG element with a focus listener a Tab stop of its own.
    $('cal-container').addEventListener('focusin', function(e) {
      var h = e.target.closest('#cal-pins .pin-handle');
      if (S.mode === 'simple' && h && !calRefocus && S.selCorner !== h.dataset.mark) selectCorner(h.dataset.mark);
    });
    // A menu left by Tab closes, so the arrow keys never move a spot nobody is looking at. A press's focus leaves
    // it to the press: closed by the focus, a panel would move the pressed control from under the tap.
    document.addEventListener('focusin', function(e) {
      if (S.pinMenu && pressSeq !== gestureSeq && !e.target.closest('#pin-menu') && !e.target.closest('#cal-overlay')) closePinMenu();
    });
    document.addEventListener('keydown', cornerEnterKey);
    // A held key's repeats belong to its press.
    document.addEventListener('pointerdown', function() { pressSeq = ++gestureSeq; }, true);
    document.addEventListener('keydown', function(e) { if (!e.repeat) gestureSeq++; }, true);
    document.addEventListener('keyup', function() { gestureSeq++; }, true);
    document.addEventListener('click', function() { gestureSeq++; });
    $('pin-menu').addEventListener('click', function(e) {
      if (gestureSeq === menuGesture) return;
      var b = e.target.closest('[data-place]'); if (b && !b.disabled) placeFromMenu(b.dataset.place);
      var pm = e.target.closest('[data-pm]'), act = pm && pm.dataset.pm;
      if (act === 'back') openPinMenu(S.pinMenu.p);
      else if (act === 'close') closePinMenu(S.pinMenu.pin);
      else if (act === 'unpin' || act === 'delete') {
        var id = S.pinMenu.pin;
        if (act === 'unpin') setPin(id, null); else deletePoint(id);
        S.selPin = null; closePinMenu(id); renderAdvRows(); solveSoon(); saveToSession();
      }
    });
    // Arrow keys move the menu's spot unless a field has the focus (a number field's arrows change its value).
    document.addEventListener('keydown', function(e) {
      if (!S.pinMenu) return;
      if (e.key === 'Enter' && e.target === $('pin-menu') && S.pinMenu.pin != null) { e.preventDefault(); closePinMenu(S.pinMenu.pin); return; }
      var step = e.shiftKey ? 10 : 1, d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
      var t = e.target.nodeType === 1 ? e.target : document.body;
      if (!d || t.matches('input, select, textarea') || t.closest('.pin-handle')) return;
      e.preventDefault(); nudgeMenuSpot(d);
    });
    $('pin-menu').addEventListener('input', pinMenuCheck);
    $('pin-menu').addEventListener('change', pinMenuCheck);
    $('pin-menu').addEventListener('submit', function(e) { e.preventDefault(); placeNewFromMenu(); });
    // A press anywhere but the menu or the picture closes it; a click on the picture opens it there instead.
    // A panel waits for the click, so a swipe that scrolls the page keeps it, and closes once the click's own
    // target has acted; where the click began is read first, as a rebuilt menu item is detached by then.
    function outsideMenu(e, panel) {
      return S.pinMenu && $('pin-menu').classList.contains('as-panel') === panel && !e.target.closest('#pin-menu') && !e.target.closest('#cal-overlay');
    }
    var clickOutside = false;
    document.addEventListener('pointerdown', function(e) { if (outsideMenu(e, false)) closePinMenu(); });
    document.addEventListener('click', function(e) { clickOutside = !!outsideMenu(e, true); }, true);
    document.addEventListener('click', function() { if (clickOutside) { clickOutside = false; closePinMenu(); } });
    // Choosing a row only moves the highlight: a rebuilt table would drop the focus.
    function choosePair(r) {
      S.pair = +r.dataset.pair;
      $('cal-dist-table').querySelectorAll('[data-pair]').forEach(function(t) { t.classList.toggle('on', t === r); t.setAttribute('aria-current', t === r ? 'true' : 'false'); });
      renderCalibOverlay();
    }
    $('cal-dist-table').addEventListener('click', function(e) { var r = e.target.closest('[data-pair]'); if (r) choosePair(r); });
    $('cal-dist-table').addEventListener('keydown', function(e) { var r = e.target.closest('[data-pair]'); if (r && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); choosePair(r); } });
  }

  // ---------------------------------------------------------------
  // Review
  // ---------------------------------------------------------------
  function paramRows(rows) {
    return rows.map(function(p) { return '<div class="wizard-solved-param"><span class="param-label">' + p[0] + '</span><span class="param-value">' + p[1] + '</span></div>'; }).join('');
  }
  function renderReview() {
    var s = S.solve || {}, svg = $('review-overlay'), u = overlayUnit(svg);
    var ok = !!s.cam;
    showLive($('review-status'), !ok && 'The camera is not calculated yet.<div class="notice-sub">' + esc(s.need ? 'To calculate the camera, add ' + s.need.join(' and ') + '. Go back to Camera Calibration to finish it.' : s.waiting ? s.waiting : s.error ? s.error : 'Pin the points in Camera Calibration.') + '</div>', !ok);
    var incomplete = incompleteLines(s);
    showLive($('review-incomplete'), incomplete.length && incompleteHtml(incomplete), !!incomplete.length);
    $('btn-apply-finish').disabled = !ok || solvePending || applyBlocked(s) || marksIncomplete(s);
    showLive($('review-caution'), ok && s.reasons.length && cautionHtml(s), !!(ok && s.reasons.length));
    $('review-lens-note').hidden = !(ok && stationLensOnly());
    var grid = calibGrid();
    if (imageWidth) svg.setAttribute('viewBox', '0 0 ' + imageWidth + ' ' + imageHeight);
    $('review-grid').innerHTML = ok && imageWidth ? gridLines(s.cam, grid).map(function(l) { return '<polyline class="wizard-grid-line" points="' + poly(l) + '"/>'; }).join('') : '';
    $('review-pins').innerHTML = ok && imageWidth ? originMarkup(originPixel(s), u) + s.marks.map(function(m) {
      var P = s.positions[m.id]; if (!P) return '';
      var q = projectShown(s.cam, P); if (!q) return '';
      return '<circle class="reproj" cx="' + q[0] + '" cy="' + q[1] + '" r="' + (5 * u) + '"/>' + pinLabel(q, m.name, [9, 9], u);
    }).join('') : '';
    $('review-camera').innerHTML = ok ? solvedParamsHtml(s.cam) : paramRows([['Camera', '–']]);
    var l = wizLens(), centre = wizLensCentreText([l[2], l[3]], imageWidth, imageHeight);
    $('review-lens').innerHTML = paramRows([['Barrel / fisheye', fmt(l[0], 3)], ['Corner correction', fmt(l[1], 3)]]
      .concat(centre ? [['Lens centre', esc(centre)]] : []).concat([['Lens rating', reviewLensCoverage(l)]]));
    var marks = s.marks || [], pinnedN = marks.filter(function(m) { return m.pin; }).length;
    $('review-calib').innerHTML = paramRows([['Mode', S.mode === 'simple' ? 'Simple' : 'Advanced'], ['Points', String(marks.length)], ['Pinned', String(pinnedN)], ['Largest error (px)', ok ? s.worst.toFixed(1) : '–'],
      ['Your camera position', S.cam ? esc(lenText(S.cam.x, 2) + ' / ' + lenText(S.cam.y, 2) + ' / ' + lenText(S.cam.z, 2)) : 'Not entered']].concat(S.height != null || (ok && s.steep)
        ? [['Camera height', S.height != null ? esc('Fixed at ' + lenText(S.height, 2)) : 'Not entered']] : []).concat(S.mode === 'simple'
        ? [['Camera location', esc((CAM_PRESETS.filter(function(q) { return q[0] === S.simpleLoc; })[0] || [0, 'Not chosen'])[1])]] : []));
    var pairs = pairsFor(s);
    $('review-dist-group').hidden = !pairs.length;
    $('review-dist-table').innerHTML = distTable(pairs, false);
    $('review-grid-params').innerHTML = paramRows([['Width', lenText(grid.w, 2)], ['Depth', lenText(grid.d, 2)], ['Spacing', lenText(grid.spacing, 2)], ['X Offset', lenText(grid.cx, 2)], ['Y Offset', lenText(grid.cy, 2)], ['Z Offset', lenText(grid.z, 2)], ['Grid', S.mode === 'simple' ? 'From the rectangle' : 'Kept']]);
    renderFsBar();
  }
  // A kept calibration first; the rating while the pair is the fit's own, User input once typed over; a loaded calibration by its name; else whether values were typed.
  function reviewLensCoverage(l) {
    if (lensHasCurrent() && S.lensChoice === 'keep') return 'Current calibration, kept';
    var applies = lensFit && lensFitApplies(lensFit);
    if (lensIsTheFits(l) || (lensFit && !applies && !lensHandTuned())) {
      var shown = lensShownRating(lensFit), level = applies ? LENS_RATING_LEVEL[shown] || 'caution' : 'caution', n = LENS_RATING_SEGMENTS[shown] || 0;
      return '<span class="wizard-meter" data-level="' + level + '" aria-hidden="true">' + [0, 1, 2, 3, 4].map(function(i) { return '<span class="wizard-meter-seg' + (i < n ? ' filled' : '') + '"></span>'; }).join('') + '</span> ' + (LENS_RATING_LABEL[shown] || '') + (applies ? '' : ', not applied');
    }
    if (lensFit) return 'User input';
    if (S.lensLoaded) {
      var ld = S.lensLoaded, same = lensSame(l, [ld.k1, ld.k2, ld.cx, ld.cy]);
      return 'Loaded: ' + esc(S.lensLoaded.name) + (same ? '' : ', changed by user input');
    }
    if (S.lensTouched) return 'User input';
    return lensMeasured() ? 'The station\'s lens' : 'Not measured';
  }
  // The lens rating Save and finish records: the fit's while the lens is the fit's, else that of the lens in use.
  var LENS_RATING_RECORD = { medium: 'poor', okay: 'fair', good: 'good', excellent: 'excellent' };
  function lensRatingRecord(l) {
    if (lensHasCurrent() && S.lensChoice === 'keep') return SAVED.lens_rating;
    if (lensIsTheFits(l)) return LENS_RATING_RECORD[lensShownRating(lensFit)] || '';
    if (lensFit) return lensFitApplies(lensFit) || lensHandTuned() ? 'user_input' : lensFit.keptRating || '';
    if (S.lensLoaded) return lensSame(l, [S.lensLoaded.k1, S.lensLoaded.k2, S.lensLoaded.cx, S.lensLoaded.cy]) ? (S.lensLoaded.kind === 'opencv' ? 'opencv' : 'loaded') : 'user_input';
    if (S.lensTouched) return 'user_input';
    return lensMeasured() ? SAVED.lens_rating : '';
  }
  // Now by this browser's clock, as ISO 8601 with its UTC offset.
  function localIsoNow() {
    var d = new Date(), off = -d.getTimezoneOffset(), p = function(n) { return String(Math.floor(Math.abs(n))).padStart(2, '0'); };
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + 'T' + p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds())
      + (off < 0 ? '-' : '+') + p(off / 60) + ':' + p(off % 60);
  }
  // Why Save and finish refuses a lens that cannot be saved, named by what is wrong with it.
  function wizLensSaveError(p) {
    if (p.kind === 'range') return { error: 'A lens centre field does not hold a whole number from −' + p.max + ' to ' + p.max + '.', action: 'Correct it in the Lens step before you finish.' };
    if (p.kind === 'centre') return { error: 'With this lens centre, the overlay cannot follow the picture out to its corners.', action: 'Set the lens centre closer to 0 in the Lens step before you finish.' };
    return { error: 'With these lens values, the overlay cannot follow the picture out to its corners.', action: 'Set one of the lens values closer to 0 in the Lens step before you finish.' };
  }
  // Save and finish writes Simple's rectangle as the grid first: a grid write that moves the corners unpins the saved corners.
  window.applyAndFinish = function() {
    var saveError = window.OpenFollow.saveError, reviewBox = document.querySelector('#wizard-step-review .section');
    var s = S.solve;
    if (!s || !s.cam || solvePending || applyBlocked(s) || marksIncomplete(s)) return;
    if (!wizLensValid()) {
      saveError.show(reviewBox, wizLensSaveError(wizLensProblem()), 'Not saved.');
      return;
    }
    var headers = { 'Content-Type': 'application/json' }, frac = function(p) { return p ? [p[0] / imageWidth, p[1] / imageHeight] : []; };
    var marks = s.marks.map(function(m) {
      var out = { id: m.id, name: m.name, kind: m.kind, pin: frac(m.pin) };
      if (m.kind === 'known') { out.x = m.pos[0]; out.y = m.pos[1]; out.z = m.pos[2]; }
      else { out.from_a = +m.a; out.from_b = +m.b; out.distance_a = m.da; out.distance_b = m.db; out.z = m.z; out.position = s.positions[m.id] || []; }
      return out;
    });
    var camera = { pos_x: s.cam.pos_x, pos_y: s.cam.pos_y, pos_z: s.cam.pos_z, pitch: s.cam.pitch, yaw: s.cam.yaw, roll: s.cam.roll, fov: s.cam.fov,
      calibration_mode: S.mode, calibration_marks: marks, calibration_height: S.height,
      calibration_lens_rating: lensRatingRecord(wizLens()), calibration_time: localIsoNow(),
      lens_k1: wizReadLensCoeff('wiz_lens_k1'), lens_k2: wizReadLensCoeff('wiz_lens_k2'), lens_cx: S.lensCentre[0], lens_cy: S.lensCentre[1] };
    var g = calibGrid();
    var gridWrite = S.mode === 'simple'
      ? fetch('/api/config/grid', { method: 'POST', headers: headers, body: JSON.stringify({ width: g.w, depth: g.d, x_offset: g.cx, y_offset: g.cy, z_offset: g.z }) })
      : Promise.resolve(null);
    gridWrite.then(async function(grid) {
      if (grid && !grid.ok) { saveError.show(reviewBox, await saveError.fromResponse(grid), 'The grid settings were not saved.'); return; }
      var cam = await fetch('/api/config/camera', { method: 'POST', headers: headers, body: JSON.stringify(camera) });
      if (!cam.ok) { saveError.show(reviewBox, await saveError.fromResponse(cam), 'The camera settings were not saved.'); return; }
      try { sessionStorage.removeItem(STORAGE_KEY); } catch (e) {}
      window.location.href = '/';
    }).catch(function() { saveError.show(reviewBox, saveError.UNREACHABLE, 'Not saved.'); });
  };
  window.discardAndLeave = function() {
    openModal({
      title: 'Leave without saving?',
      bodyHTML: '<p>None of your changes in the Setup Wizard are saved. The station keeps its current camera, lens and grid.</p>',
      footerButtons: [
        { label: 'Cancel', onClick: function() { closeModal(); } },
        { label: 'Leave without saving', kind: 'danger', onClick: function() {
          closeModal();
          try { sessionStorage.removeItem(STORAGE_KEY); } catch (e) {}
          window.location.href = '/';
        } }
      ],
    });
  };

  // ---------------------------------------------------------------
  // Video Source
  // ---------------------------------------------------------------
  window.saveWizardVideoSource = function() {
    var formData = new FormData();
    formData.append('video_source_type', document.getElementById('wizard-video-source-type').value);
    // Collect plugin-specific fields from the visible input panel
    var activeType = document.getElementById('wizard-video-source-type').value;
    var activePanel = document.querySelector('[data-wizard-input-type="' + activeType + '"]');
    if (activePanel) {
      activePanel.querySelectorAll('input, select, textarea').forEach(function(el) {
        if (el.name) {
          if (el.type === 'checkbox') {
            formData.append(el.name, el.checked ? 'on' : '');
          } else {
            formData.append(el.name, el.value);
          }
        }
      });
    }

    // Returns the fetch promise so callers can chain navigation AFTER the save
    // resolves (Save & Next must not advance while the old source is still
    // active). Rejects on a failed save so the caller's .catch can surface it
    // without advancing.
    var saveError = window.OpenFollow.saveError;
    var videoBox = document.querySelector('#wizard-step-video .section');
    return fetch('/section/video_source', { method: 'POST', body: formData })
    .then(async function(r) {
      if (!r.ok) {
        saveError.show(videoBox, await saveError.fromResponse(r));
        throw new Error('Save failed');
      }
      saveError.clear(videoBox);
      var msgEl = document.getElementById('wizard-video-saved');
      msgEl.textContent = 'Video source saved.';
      msgEl.style.display = 'block';
    });
  };

  // A refused save has already said why on the step; this ends the chain and
  // covers a request that got no answer at all.
  window.wizardVideoSaveFailed = function(err) {
    if (err && err.message === 'Save failed') return;
    window.OpenFollow.saveError.show(
      document.querySelector('#wizard-step-video .section'), window.OpenFollow.saveError.UNREACHABLE);
  };

  // ---------------------------------------------------------------
  // Full screen: the step's picture in a shell with a one-line toolbar, and back on exit. Where the
  // browser refuses full screen (an iPhone, a framed page), the shell fills the window.
  // ---------------------------------------------------------------
  var FS = null;
  function fsContainer(key) { return $(key + '-container'); }
  function openFull(key) {
    if (FS) return;
    closeHelp();
    // The placeholder keeps the picture's room and the page comes back where it was, read before scroll
    // anchoring follows the picture into the shell.
    var x = window.scrollX, y = window.scrollY, cont = fsContainer(key), ph = document.createElement('div'), cs = getComputedStyle(cont);
    ph.style.height = cont.getBoundingClientRect().height + 'px'; ph.style.margin = cs.margin;
    cont.parentNode.insertBefore(ph, cont);
    dropCorner(); dropLensPoint();
    $('fs-stage').appendChild(cont);
    FS = { key: key, cont: cont, ph: ph, pad: 0, x: x, y: y };
    $('fs-shell').hidden = false; document.body.style.overflow = 'hidden';
    var sh = $('fs-shell');
    if (sh.requestFullscreen) { try { var pr = sh.requestFullscreen(); if (pr && pr.catch) pr.catch(function() {}); } catch (e) { /* refused: the window-filling shell stays */ } }
    fsLayout();
    $('fs-exit').focus({ preventScroll: true });
  }
  function closeFull() {
    if (!FS) return;
    toggleFsHelp(false);
    // Leaving cancels whatever is active on the picture.
    closePinMenu(); dropCorner(); dropLensPoint(); lensSelected = null; lensPending = null;
    var f = FS; FS = null;
    f.ph.parentNode.insertBefore(f.cont, f.ph); f.ph.parentNode.removeChild(f.ph);
    f.cont.style.width = '';
    $('fs-shell').hidden = true; document.body.style.overflow = ''; $('fs-hint').liveHtml = null;
    fsRender(f.key);
    // The page outside the full-screen element takes no focus until the browser has left full screen.
    var b = f.cont.querySelector('.fs-btn'), back = function() {
      if (FS) return;
      window.scrollTo(f.x, f.y);
      if (b) b.focus({ preventScroll: true });
    };
    if (document.fullscreenElement && document.exitFullscreen) {
      try { var pr = document.exitFullscreen(); if (pr && pr.then) pr.then(back, back); else back(); } catch (e) { back(); }
    } else back();
  }
  // The picture keeps its aspect ratio: as wide as the stage allows without being taller than it.
  // An open menu sheet takes the stage's bottom, so the picture fits above it.
  function fsLayout() {
    if (!FS || !imageWidth) return;
    var m = $('pin-menu'), stage = $('fs-stage');
    FS.pad = S.pinMenu && m.classList.contains('as-sheet') ? m.offsetHeight : 0;
    stage.style.paddingBottom = FS.pad ? FS.pad + 'px' : '';
    var st = stage.getBoundingClientRect();
    FS.cont.style.width = Math.floor(Math.min(st.width, (st.height - FS.pad) * imageWidth / imageHeight)) + 'px';
    fsRender(FS.key); renderFsBar();
  }
  function fsRender(key) {
    if (key === 'lens') renderLens();
    else if (key === 'cal') { renderCalibOverlay(); if (S.pinMenu) positionPinMenu(); }
    else if (key === 'review') renderReview();
  }
  function fsKeys(rows) {
    return '<table class="data-table fs-keys"><tbody>' + rows.map(function(k) {
      return '<tr><td>' + (typeof k[0] === 'string' ? k[0] : k[0].map(function(x) { return '<kbd>' + x + '</kbd>'; }).join(' ')) + '</td><td>' + k[1] + '</td></tr>';
    }).join('') + '</tbody></table>';
  }
  var ARROWS = ['&larr;', '&rarr;', '&uarr;', '&darr;'];
  var FS_ESC = [['Esc'], 'End full screen (also cancels the selection)'];
  // How this picture works, and its keys. The panel stays open while the picture is used.
  function fsHelpHtml() {
    var k = FS.key, intro, rows;
    if (k === 'lens') {
      intro = (lensOpenCandidates().length ? '<strong>Check each dashed suggested line</strong>: the check mark uses it, the cross removes it. Or click' : 'Click') + ' the start and the end of a straight edge. Click a line to select it. Drag a point to correct it.';
      rows = [[['Tab'], 'Next suggestion bubble or line. With a point selected: the next point on its line, after the last the first again'], ['<kbd>Shift</kbd> + <kbd>Tab</kbd>', 'The previous point on its line, before the first the last again'],
        [ARROWS, 'Move the selected point 1&nbsp;px, with Shift 10&nbsp;px'], [['Enter'], 'Confirm the point and end the selection'],
        ['<kbd>Delete</kbd> or <kbd>Backspace</kbd>', 'Remove the selected line, or cancel a line you started'], FS_ESC];
    } else if (k === 'cal' && S.mode === 'simple') {
      intro = '<strong>Drag each corner</strong> onto its tape mark. The loupe shows the picture 4&times; larger. Click a corner to select it.';
      rows = [[['Tab'], 'Select the next corner'], [ARROWS, 'Move the selected corner 1&nbsp;px'], [['Enter'], 'Place the corner where the crosshair is'], FS_ESC];
    } else if (k === 'cal') {
      intro = '<strong>Click the picture</strong> where a point is, and choose the point from the menu. Or create a <strong>New known point</strong> or a <strong>New triangulated point</strong> there. Click a pin to remove the pin or delete the point. Drag a pin to correct it.';
      rows = [[ARROWS, 'Menu open: move the spot 1&nbsp;px. Pin selected: move the pin 1&nbsp;px'], [['Tab'], 'Next menu item, field or pin'], [['Enter'], 'Keep the moved pin, choose the menu item, or place the new point'], FS_ESC];
    } else {
      intro = 'The picture shows the calculated grid, the points and 0/0/0, as the Operator Screen will draw them. You cannot change anything here.';
      rows = [[['Esc'], 'End full screen']];
    }
    rows.push([['?'], 'Show or hide this help']);
    return '<div class="fs-help-head"><h3 id="fs-help-title">Full screen</h3><button type="button" class="modal-close" data-fs-help-close aria-label="Close help">&times;</button></div>' +
      '<p>' + intro + '</p>' + fsKeys(rows);
  }
  function toggleFsHelp(open) {
    var panel = $('fs-help');
    if (open == null) open = panel.hidden;
    if (open && FS) panel.innerHTML = fsHelpHtml();
    panel.hidden = !open;
    $('fs-help-btn').setAttribute('aria-expanded', String(open));
  }
  function renderFsBar() {
    if (!FS) return;
    var title, hint = '', status = '', warn = false, actions = [];
    if (FS.key === 'lens') {
      title = 'Lens';
      hint = lensHintText;
      var misfit = lensMisfitLines();
      if (misfit.length) { status = lensMisfitText(misfit); warn = true; }
      actions = [{ act: 'delete-line', label: 'Delete line', off: !lensSelected }];
    } else if (FS.key === 'cal') {
      title = 'Camera Calibration · ' + (S.mode === 'simple' ? 'Simple' : 'Advanced');
      hint = hintText;
      var s = S.solve;
      if (s && s.cam) { status = 'Largest error ' + s.worst.toFixed(1) + ' px'; if (s.reasons.length) { status += ' · Something might be wrong'; warn = true; } }
    } else {
      title = 'Review';
      hint = S.solve && S.solve.cam ? 'The green grid is the one the Operator Screen will draw.' : 'The camera is not calculated yet.';
    }
    $('fs-title').textContent = title;
    setLive($('fs-hint'), esc(hint));
    $('fs-status').textContent = status; $('fs-status').classList.toggle('warn', warn);
    fsActions(actions);
  }
  // The buttons are patched, not rebuilt: a button replaced between its press and release gets no click, and a
  // focused one drops the focus. A focused button that is switched off hands the focus to the picture.
  function fsActions(list) {
    var bar = $('fs-actions'), acts = list.map(function(a) { return a.act; }).join(' ');
    if (bar.dataset.acts !== acts) {
      bar.innerHTML = list.map(function(a) { return '<button type="button" class="secondary small" data-fs-act="' + a.act + '"></button>'; }).join('');
      bar.dataset.acts = acts;
    }
    var btns = bar.querySelectorAll('[data-fs-act]');
    list.forEach(function(a, i) {
      var b = btns[i], lost = a.off && document.activeElement === b;
      if (b.textContent !== a.label) b.textContent = a.label;
      b.disabled = a.off;
      if (lost) $(FS.key + '-overlay').focus({ preventScroll: true });
    });
  }
  function fsInit() {
    document.querySelectorAll('[data-fs]').forEach(function(b) { b.addEventListener('click', function() { openFull(b.dataset.fs); }); });
    $('fs-exit').addEventListener('click', closeFull);
    $('fs-help-btn').addEventListener('click', function() { toggleFsHelp(); });
    $('fs-help').addEventListener('click', function(e) { if (e.target.closest('[data-fs-help-close]')) { toggleFsHelp(false); $('fs-help-btn').focus(); } });
    $('fs-actions').addEventListener('click', function(e) {
      var b = e.target.closest('[data-fs-act]'); if (b && b.dataset.fsAct === 'delete-line') lensDeleteSelectedLine();
    });
    // Tab stays inside the full-screen view: the page behind it is not reachable.
    $('fs-shell').addEventListener('keydown', function(e) {
      if (e.key !== 'Tab') return;
      var all = Array.prototype.filter.call($('fs-shell').querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'), function(el) { return el.offsetParent !== null; });
      if (!all.length) return;
      var first = all[0], last = all[all.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });
    // ? toggles the help, unless a field is being typed in.
    document.addEventListener('keydown', function(e) {
      if (!FS || e.key !== '?') return;
      var t = e.target.nodeType === 1 ? e.target : document.body;
      if (t.matches('input, select, textarea')) return;
      e.preventDefault(); toggleFsHelp();
    });
    // The browser's own exit (Esc, a gesture) leaves the shell too.
    document.addEventListener('fullscreenchange', function() { if (!document.fullscreenElement && FS) closeFull(); else fsLayout(); });
  }

  // ---------------------------------------------------------------
  // Session: survives an accidental reload; pins are kept as fractions of the snapshot
  // ---------------------------------------------------------------
  function saveToSession() {
    try {
      var keepPins = S.pinSize === null;  // still fractions: the snapshot has not loaded
      // Pixels of the snapshot the pins are in, which a new snapshot's size may not be yet.
      var frac = function(p) { return p && !keepPins ? [p[0] / S.pinSize[0], p[1] / S.pinSize[1]] : null; };
      var corners = {};
      CORNERS.forEach(function(c) { corners[c] = keepPins ? S.cornerPins[c] : frac(S.cornerPins[c]); });
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
        stepKey: WIZ_STEPS[currentStep],
        lensLines: lensLines, lensLast: lensLines.indexOf(lensLast), lensImage: lensImageSize, lensFit: lensFit, lensRejected: lensRejected,
        k1: wizReadLensCoeff('wiz_lens_k1'), k2: wizReadLensCoeff('wiz_lens_k2'), lensCentre: S.lensCentre,
        mode: S.mode, simpleLoc: S.simpleLoc, rect: S.rect, cornerPins: corners, cornerMoved: S.cornerMoved,
        points: S.points.map(function(p) { return Object.assign({}, p, { pin: keepPins ? p.pin : frac(p.pin) }); }), nextId: S.nextId,
        cam: ['x', 'y', 'z'].map(function(k) { return $('cam_pos_' + k).value; }), height: $('cam_height').value, units: WUNIT.isImperial() ? 'imperial' : 'metric',
        lensTouched: S.lensTouched, lensLoaded: S.lensLoaded, lensChoice: S.lensChoice
      }));
    } catch (e) {}
  }
  function pairOk(p) { return Array.isArray(p) && p.length === 2 && isFinite(p[0]) && isFinite(p[1]); }
  // A pin is stored as fractions of the snapshot: anything outside it is not one this page wrote.
  function fracPinOk(p) { return pairOk(p) && p[0] >= 0 && p[0] <= 1 && p[1] >= 0 && p[1] <= 1; }
  // Only state in the shape this page draws: a session saved by another version must not stop the wizard from opening.
  function restoreFromSession() {
    var raw;
    try { raw = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || 'null'); } catch (e) { return false; }
    if (!raw || typeof raw !== 'object') return false;
    try {
      // Lengths were typed in the units the session was saved in; another unit system starts the calibration afresh.
      var sameUnits = raw.units === (WUNIT.isImperial() ? 'imperial' : 'metric');
      if (Array.isArray(raw.lensLines)) {
        lensLines = raw.lensLines.filter(lensRestoredLine);
        var last = Number.isInteger(raw.lensLast) ? raw.lensLines[raw.lensLast] : null;
        lensLast = lensLines.indexOf(last) >= 0 ? last : null;
      }
      if (lensRestoredPair(raw.lensImage)) lensImageSize = raw.lensImage;
      if (Array.isArray(raw.lensRejected)) lensRejected = raw.lensRejected.filter(function(r) { return Array.isArray(r) && r.length === 2 && lensRestoredPair(r[0]) && lensRestoredPair(r[1]); });
      if (raw.lensFit && typeof raw.lensFit.rating === 'string' && isFinite(raw.lensFit.k1) && isFinite(raw.lensFit.k2) && pairOk(raw.lensFit.centre)) lensFit = raw.lensFit;
      if (isFinite(raw.k1) && isFinite(raw.k2)) { wizWriteLensCoeff('wiz_lens_k1', raw.k1); wizWriteLensCoeff('wiz_lens_k2', raw.k2); }
      if (pairOk(raw.lensCentre)) S.lensCentre = raw.lensCentre.slice();
      S.lensTouched = raw.lensTouched === true;
      if (['keep', 'clear', 'load'].indexOf(raw.lensChoice) >= 0 && lensHasCurrent()) S.lensChoice = raw.lensChoice;
      if (raw.lensLoaded && typeof raw.lensLoaded.name === 'string' && [raw.lensLoaded.k1, raw.lensLoaded.k2, raw.lensLoaded.cx, raw.lensLoaded.cy].every(isFinite)) S.lensLoaded = raw.lensLoaded;
      if (sameUnits && (raw.mode === 'simple' || raw.mode === 'advanced')) {
        S.mode = raw.mode;
        S.simpleLoc = CAM_PRESETS.some(function(p) { return p[0] === raw.simpleLoc; }) ? raw.simpleLoc : '';
        if (raw.rect && typeof raw.rect === 'object') ['w', 'd', 'cx', 'cy', 'z'].forEach(function(k) { if (typeof raw.rect[k] === 'string') S.rect[k] = raw.rect[k]; });
        if (raw.rect && (raw.rect.origin === '' || raw.rect.origin === 'dsc' || raw.rect.origin === 'custom')) S.rect.origin = raw.rect.origin;
        CORNERS.forEach(function(c) {
          S.cornerPins[c] = raw.cornerPins && fracPinOk(raw.cornerPins[c]) ? raw.cornerPins[c] : null;
          S.cornerMoved[c] = !!(raw.cornerMoved && raw.cornerMoved[c] && S.cornerPins[c]);
        });
        if (Array.isArray(raw.points)) S.points = raw.points.filter(function(p) { return p && isFinite(p.id) && (p.kind === 'known' || p.kind === 'triangulated'); }).map(function(p) {
          return Object.assign({}, p, { pin: fracPinOk(p.pin) ? p.pin : null });
        });
        S.nextId = Math.max(isFinite(raw.nextId) ? raw.nextId : 1, 1 + Math.max.apply(null, [0].concat(S.points.map(function(p) { return p.id; }))));
        if (Array.isArray(raw.cam)) ['x', 'y', 'z'].forEach(function(k, i) { if (typeof raw.cam[i] === 'string') $('cam_pos_' + k).value = raw.cam[i]; });
        if (typeof raw.height === 'string') $('cam_height').value = raw.height;
      }
      if (typeof raw.stepKey === 'string' && WIZ[raw.stepKey] !== undefined) currentStep = WIZ[raw.stepKey];
      return true;
    } catch (e) { return false; }
  }
  // The station's last calibration, when there is one: Simple's rectangle comes from the grid it wrote.
  function loadSaved() {
    var marks = SAVED.marks || [];
    if (!marks.length) return;
    S.mode = SAVED.mode === 'advanced' ? 'advanced' : 'simple';
    if (typeof SAVED.height === 'number') $('cam_height').value = lenField(SAVED.height);
    if (S.mode === 'simple') {
      var g = SAVED.grid;
      S.rect.w = lenField(g.width); S.rect.d = lenField(g.depth);
      var centred = Math.abs(g.x_offset) < 1e-6 && Math.abs(g.y_offset - g.depth / 2) < 1e-6 && Math.abs(g.z_offset) < 1e-6;
      S.rect.origin = centred ? 'dsc' : 'custom';
      if (!centred) { S.rect.cx = lenField(g.x_offset); S.rect.cy = lenField(g.y_offset); S.rect.z = lenField(g.z_offset); }
      marks.forEach(function(m) {
        var c = CORNERS[m.id - 1];
        if (c && m.pin.length === 2) { S.cornerPins[c] = m.pin; S.cornerMoved[c] = true; }
      });
    } else {
      S.points = marks.map(function(m) {
        return m.kind === 'known'
          ? { id: m.id, name: m.name, kind: 'known', x: lenField(m.x), y: lenField(m.y), z: lenField(m.z), pin: m.pin.length === 2 ? m.pin : null }
          : { id: m.id, name: m.name, kind: 'triangulated', a: m.from_a, b: m.from_b, da: lenField(m.distance_a), db: lenField(m.distance_b), z: lenField(m.z), pin: m.pin.length === 2 ? m.pin : null };
      });
      S.nextId = 1 + Math.max.apply(null, [0].concat(S.points.map(function(p) { return p.id; })));
    }
  }
  // Pins held as fractions (from the station or a session) become snapshot pixels once its size is known;
  // pins in pixels follow a snapshot of another size.
  function pinsToSnapshot() {
    var from = S.pinSize, w = imageWidth, h = imageHeight;
    var map = function(p) { return !p ? p : from ? [p[0] * w / from[0], p[1] * h / from[1]] : [p[0] * w, p[1] * h]; };
    if (from && from[0] === w && from[1] === h) return;
    CORNERS.forEach(function(c) { if (S.cornerMoved[c]) S.cornerPins[c] = map(S.cornerPins[c]); });
    S.points.forEach(function(p) { p.pin = map(p.pin); });
    S.pinSize = [w, h];
  }

  // ---------------------------------------------------------------
  // Step navigation
  // ---------------------------------------------------------------
  window.wizardGo = function(step, atLoad) {
    closePinMenu(); dropCorner(); dropLensPoint();
    saveToSession();
    if (!(step >= 0 && step < WIZ_STEPS.length)) step = 0;
    currentStep = step;
    var key = WIZ_STEPS[step], to = $('wizard-step-' + key), a = document.activeElement;
    // A focus on the page's body, or in the step being left, goes to the new step's heading.
    var lost = !atLoad && (!a || a === document.body || !!(a.closest && a.closest('.wizard-content') && !to.contains(a)));
    document.querySelectorAll('.wizard-content').forEach(function(el) { el.classList.toggle('active', el === to); });
    // Its regions are read out as the step shows them.
    to.querySelectorAll('[role="status"], [role="alert"], [aria-live]').forEach(function(el) { el.liveHtml = null; });
    document.querySelectorAll('.wizard-step-btn').forEach(function(btn) {
      var s = parseInt(btn.dataset.step);
      btn.classList.toggle('active', s === step);
      btn.setAttribute('aria-current', s === step ? 'step' : 'false');
      if (s < step) btn.classList.add('completed');
    });
    if (key !== 'video') loadSnapshot();
    if (key === 'calib') { if (S.mode === 'simple') placeStartQuad(); renderCalibPanels(); runSolve(); }
    if (key === 'review') runSolve(renderReview);
    saveToSession();
    if (lost) to.querySelector('h2').focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };
  window.wizardNext = function() { wizardGo(Math.min(currentStep + 1, WIZ_STEPS.length - 1)); };
  window.wizardPrev = function() { wizardGo(Math.max(currentStep - 1, 0)); };

  // Esc ends what is active, one thing at a time: full screen first, where the browser takes Esc itself.
  document.addEventListener('keydown', function(e) {
    if (e.key !== 'Escape') return;
    var modal = $('modal-root');
    if (modal && !modal.hidden) return;
    if (FS) { closeFull(); return; }
    // An open help drawer takes this Esc (base.tpl closes it).
    if (document.body.classList.contains('help-open')) return;
    if (S.pinMenu) closePinMenu(S.pinMenu.pin);
    else if (S.selCorner != null) dropCorner();
    else if (lensHold) lensEndHold();
    else if (lensPending && currentStep === WIZ.lens) { lensPending = null; renderLens(); }
  });

  // ---------------------------------------------------------------
  // Init
  // ---------------------------------------------------------------
  loadSaved();
  var restored = restoreFromSession();
  applyUnitMarkup();
  ['w', 'd', 'cx', 'cy', 'z'].forEach(function(k) { $('rect_' + k).value = S.rect[k]; });
  $('rect_origin').value = S.rect.origin;
  $('rect-custom').hidden = S.rect.origin !== 'custom';
  refreshEchoes();
  readCam(); readHeight();
  calibInit();
  fsInit();
  lensInit();
  lensCurrentInit();
  renderCalibPanels();
  // Handles and labels are sized in CSS pixels of the preview's width at render time.
  var resizeTimer = null;
  window.addEventListener('resize', function() {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function() {
      if (!imageWidth) return;
      if (FS) { fsLayout(); return; }
      renderLens(); renderCalibOverlay(); if (S.pinMenu) positionPinMenu(); if (currentStep === WIZ.review) renderReview();
    }, 200);
  });
  wizardGo(restored ? currentStep : 0, true);

})();
</script>
