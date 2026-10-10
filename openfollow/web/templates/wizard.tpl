% rebase('base.tpl')
% from openfollow.units import UnitSystem, format_length, metric_echo, unit_suffix_length
% _us = UnitSystem(config.ui.unit_system)
% _imp = _us is UnitSystem.IMPERIAL
% _len = unit_suffix_length(_us)

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
  .wizard-illustration {
    display: block;
    width: 100%;
    max-width: 480px;
    margin: 0 auto 1rem;
  }
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
  /* Dimmed when user edited HFOV directly – helper no longer matches FOV. */
  .lens-helper-stale,
  .lens-helper-stale label,
  .lens-helper-stale input,
  .lens-helper-stale select {
    opacity: 0.55;
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
  .wizard-preview-container.valid { border-color: var(--success-line); }
  .wizard-preview-container.invalid { border-color: var(--error-line); }
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
  }
  .wizard-overlay .handle:active { cursor: grabbing; }
  /* Fine-adjust mode: 2×2 grid of 4× zoom windows (one per corner).
     Layout: USL/USR/DSL/DSR. Aspect-ratio set inline from snapshot.
     Four boxes together cover same footprint as full image. */
  .fine-zoom-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    grid-template-rows: 1fr 1fr;
    gap: 4px;
    width: 100%;
    background: var(--bg-deep);
  }
  .fine-zoom-box {
    position: relative;
    background: #000;
    overflow: hidden;
    min-width: 0;
    min-height: 0;
  }
  .fine-zoom-svg {
    display: block;
    width: 100%;
    height: 100%;
    cursor: grab;
    touch-action: none;
  }
  .fine-zoom-svg:active { cursor: grabbing; }
  .fine-zoom-corner-label {
    position: absolute;
    top: 4px;
    left: 6px;
    padding: 1px 6px;
    border-radius: 4px;
    background: rgba(0, 0, 0, 0.55);
    color: rgba(247, 245, 233, 0.9);
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    pointer-events: none;
  }
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
  /* Lens step: traced lines, their points and the curve the fitted lens predicts. */
  .lens-hit { pointer-events: all; cursor: crosshair; }
  .lens-chord { fill: none; stroke: var(--accent); stroke-width: 1.5; stroke-opacity: 0.7; }
  .lens-curve { fill: none; stroke: var(--success-line); stroke-width: 2; stroke-dasharray: 6 4; }
  .lens-line.misfit .lens-curve, .lens-line.misfit .lens-chord { stroke: var(--caution-line); }
  .lens-line.selected .lens-chord { stroke-opacity: 1; stroke-width: 2.5; }
  .lens-point .dot, .wizard-point .dot { fill: var(--accent); stroke: var(--accent); stroke-width: 1.5; }
  .lens-point.off .dot { fill: none; }
  .lens-point.unsnapped .dot { stroke-dasharray: 2 2; }
  .lens-point.selected .dot { stroke-width: 3; }
  .lens-pending { fill: none; stroke: var(--accent); stroke-width: 2; stroke-dasharray: 3 3; }
  .lens-label { fill: var(--text); font-size: 11px; font-weight: 600; pointer-events: none; }
  /* The grid's own lines inside the quad, as the Operator Screen draws them. */
  .wizard-grid-line { fill: none; stroke: var(--accent); stroke-opacity: 0.4; stroke-width: 1; vector-effect: non-scaling-stroke; pointer-events: none; }
  #review-grid .wizard-grid-line { stroke: var(--success-mark); stroke-opacity: 0.5; }
  .wizard-loupe {
    position: absolute; width: 120px; height: 120px; border-radius: 50%; overflow: hidden;
    border: 2px solid var(--accent); background: var(--bg-deep); pointer-events: none; display: none;
  }
  .wizard-loupe canvas { width: 100%; height: 100%; display: block; }
  .lens-result-main { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
  /* Suggested edges: faint and dashed until tapped, with a wide invisible stroke to tap. */
  .lens-candidate-line { fill: none; stroke: var(--accent); stroke-width: 2.5; stroke-opacity: 0.6; stroke-dasharray: 8 6; pointer-events: none; }
  .lens-candidate-hit { fill: none; stroke: transparent; stroke-width: 24; pointer-events: stroke; cursor: pointer; }
  .lens-candidate:hover .lens-candidate-line { stroke-opacity: 1; }
  .lens-edges { position: absolute; top: 0; left: 0; width: 100%; height: 100%; display: none; pointer-events: none; }
  .lens-dev { display: inline-flex; align-items: center; gap: 0.35rem; color: var(--muted); font-size: 0.85rem; }
  /* Graded meter (docs/STATUS_LANGUAGE.md): five segments, filled in the level's line colour. */
  .wizard-meter { display: inline-flex; gap: 3px; vertical-align: middle; }
  .wizard-meter-seg { width: 14px; height: 8px; border-radius: 2px; background: var(--border-soft); }
  .wizard-meter[data-level="caution"] .wizard-meter-seg.filled { background: var(--caution-line); }
  .wizard-meter[data-level="info"] .wizard-meter-seg.filled { background: var(--info-line); }
  .wizard-meter[data-level="success"] .wizard-meter-seg.filled { background: var(--success-line); }
  @media (max-width: 680px) {
    .wizard-step-btn { padding: 0.35rem 0.5rem; font-size: 0.72rem; }
  }
</style>

<div class="wizard-header">
  <a href="/">&larr; Back to Settings</a>
  <h2>Setup Wizard</h2>
</div>

% # The Lens step ships behind the experimental-features toggle: without it the
% # wizard leaves the step out and keeps seven steps. The order is published to
% # the page script as WIZ (key -> index), so no button or script carries a
% # step number.
% _show_lens = bool(config.ui.show_experimental_features)
% _steps = [('prep', 'Preparation'), ('grid', 'Grid Setup'), ('video', 'Video Source')]
% if _show_lens:
%   _steps.append(('lens', 'Lens'))
% end
% _steps += [('camera', 'Camera Position'), ('ref', 'Reference Mapping'), ('corners', 'Corner Pinning'), ('review', 'Review')]
% _keys_js = ', '.join("'" + k + "'" for k, _ in _steps)
<script>
  window.WIZ = {
% for _i, (_key, _label) in enumerate(_steps):
    {{_key}}: {{_i}},
% end
  };
  window.WIZ_STEPS = [{{!_keys_js}}];
</script>
<nav class="wizard-steps" aria-label="Setup wizard steps">
% for _i, (_key, _label) in enumerate(_steps):
  <button type="button" class="wizard-step-btn{{' experimental-feature' if _key == 'lens' else ''}}" data-step="{{_i}}" onclick="wizardGo({{_i}})">{{_i + 1}}. {{_label}}</button>
% end
</nav>

<!-- Preparation -->
<div class="wizard-content" id="wizard-step-prep">
  <div class="section">
    <div class="section-head">
      <h2>Preparation</h2>
      <span class="section-note">Understand the setup and prepare the physical space</span>
    </div>

    <svg id="prep-svg" class="wizard-illustration" viewBox="0 0 580 400" style="max-width:725px;" xmlns="http://www.w3.org/2000/svg">
      <!-- Stage area (transparent white, larger than grid) -->
      <polygon id="pp-stage" fill="rgba(255,255,255,0.03)" stroke="rgba(255,255,255,0.1)" stroke-width="1" stroke-dasharray="4,4"/>
      <!-- Grid quad -->
      <polygon id="pp-grid" fill="rgba(255,188,0,0.06)" stroke="rgba(255,188,0,0.5)" stroke-width="1.5"/>
      <g id="pp-grid-lines"></g>
      <!-- Corner labels -->
      <text id="pp-label-dsl" fill="rgba(247,245,233,0.5)" font-size="9" font-weight="600">DSL</text>
      <text id="pp-label-dsr" fill="rgba(247,245,233,0.5)" font-size="9" font-weight="600">DSR</text>
      <text id="pp-label-usr" fill="rgba(247,245,233,0.5)" font-size="9" font-weight="600">USR</text>
      <text id="pp-label-usl" fill="rgba(247,245,233,0.5)" font-size="9" font-weight="600">USL</text>
      <!-- Reference point (ground level) -->
      <g id="pp-ref">
        <circle r="5" fill="none" stroke="#ffbc00" stroke-width="1.5"/>
        <line x1="-5" y1="0" x2="5" y2="0" stroke="#ffbc00" stroke-width="1.5"/>
        <line x1="0" y1="-5" x2="0" y2="5" stroke="#ffbc00" stroke-width="1.5"/>
      </g>
      <text id="pp-ref-label" fill="#ffbc00" font-size="9" font-weight="700">REF</text>
      <!-- Grid Z offset indicator -->
      <line id="pp-gz-line" stroke="#ffbc00" stroke-width="1" stroke-dasharray="4,3" opacity="0.6" style="display:none"/>
      <circle id="pp-gz-dot" r="3" fill="none" stroke="#ffbc00" stroke-width="1" opacity="0.6" style="display:none"/>
      <text id="pp-gz-label" fill="#ffbc00" font-size="9" font-weight="600" opacity="0.7" style="display:none"></text>
      <!-- FOV cone -->
      <line id="pp-sight-line" stroke="rgba(255,255,255,0.1)" stroke-width="1" stroke-dasharray="4,4"/>
      <line id="pp-fov-left" stroke="rgba(255,255,255,0.15)" stroke-width="1" stroke-dasharray="3,3"/>
      <line id="pp-fov-right" stroke="rgba(255,255,255,0.15)" stroke-width="1" stroke-dasharray="3,3"/>
      <line id="pp-fov-line" stroke="rgba(255,255,255,0.2)" stroke-width="1"/>
      <text id="pp-fov-label" fill="rgba(247,245,233,0.4)" font-size="9" font-weight="600" text-anchor="middle"></text>
      <!-- Corner-to-camera sight lines -->
      <line id="pp-sight-0" stroke="rgba(255,255,255,0.12)" stroke-width="1" stroke-dasharray="4,4"/>
      <line id="pp-sight-1" stroke="rgba(255,255,255,0.12)" stroke-width="1" stroke-dasharray="4,4"/>
      <line id="pp-sight-2" stroke="rgba(255,255,255,0.12)" stroke-width="1" stroke-dasharray="4,4"/>
      <line id="pp-sight-3" stroke="rgba(255,255,255,0.12)" stroke-width="1" stroke-dasharray="4,4"/>
      <!-- Measurement lines -->
      <line id="pp-x-line" stroke="rgba(244,72,72,0.4)" stroke-width="1" stroke-dasharray="4,3" style="display:none"/>
      <text id="pp-x-label" fill="#f44848" font-size="9" font-weight="600" style="display:none"></text>
      <line id="pp-y-line" stroke="rgba(92,201,140,0.4)" stroke-width="1" stroke-dasharray="4,3"/>
      <text id="pp-y-label" fill="#5cc98c" font-size="9" font-weight="600"></text>
      <!-- Z axis line (camera floor to camera) -->
      <line id="pp-z-line" stroke="rgba(79,141,217,0.5)" stroke-width="1.5" stroke-dasharray="4,3"/>
      <text id="pp-z-label" fill="#4f8dd9" font-size="9" font-weight="600" style="display:none"></text>
      <circle id="pp-floor-dot" r="3" fill="none" stroke="rgba(79,141,217,0.5)" stroke-width="1" style="display:none"/>
      <!-- Camera body -->
      <polygon id="pp-cam-body" fill="rgba(255,255,255,0.12)" stroke="rgba(255,255,255,0.4)" stroke-width="1"/>
      <polygon id="pp-cam-lens" fill="rgba(255,255,255,0.2)" stroke="rgba(255,255,255,0.5)" stroke-width="1"/>
      <text id="pp-cam-label" fill="rgba(247,245,233,0.68)" font-size="10" font-weight="600">Camera</text>
      <!-- DS / US labels -->
      <text id="pp-ds-label" fill="rgba(247,245,233,0.3)" font-size="9" font-weight="600" text-anchor="middle">DOWNSTAGE</text>
      <text id="pp-us-label" fill="rgba(247,245,233,0.3)" font-size="9" font-weight="600" text-anchor="middle">UPSTAGE</text>
      <!-- PSN axis indicator -->
      <defs>
        <marker id="ppArrowR" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6" fill="rgba(244,72,72,0.8)"/></marker>
        <marker id="ppArrowG" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6" fill="rgba(92,201,140,0.8)"/></marker>
        <marker id="ppArrowB" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6" fill="rgba(79,141,217,0.8)"/></marker>
      </defs>
      <line id="pp-axis-x" stroke="rgba(244,72,72,0.8)" stroke-width="1.5" marker-end="url(#ppArrowR)"/>
      <text id="pp-axis-x-label" fill="#f44848" font-size="10" font-weight="600">X+</text>
      <line id="pp-axis-y" stroke="rgba(92,201,140,0.8)" stroke-width="1.5" marker-end="url(#ppArrowG)"/>
      <text id="pp-axis-y-label" fill="#5cc98c" font-size="10" font-weight="600">Y+</text>
      <line id="pp-axis-z" stroke="rgba(79,141,217,0.8)" stroke-width="1.5" marker-end="url(#ppArrowB)"/>
      <text id="pp-axis-z-label" fill="#4f8dd9" font-size="10" font-weight="600">Z+</text>
    </svg>

    <div class="wizard-help">
      <strong>Reference Point:</strong> All measurements in this wizard are relative to a single Reference Point. This should be the same reference point for all show control systems that you plan to connect to OpenFollow. If your venue has a defined origin point, you should use that. If you don't have an existing reference point, we recommend the <strong>center of the stage's front edge</strong> as the default.
    </div>
    <div class="wizard-help">
      <strong>The Grid:</strong> The grid area must be a <strong>right-angled rectangle</strong> as large as possible that lies flat on the main performance level. All four corners must be <strong>clearly visible in the camera feed</strong>. Don't worry, you can follow performers outside this grid, as long as they are in the area visible to the camera.
    </div>
    <div class="wizard-action-required">
      Before proceeding, physically mark the Reference Point and the four grid corners on the stage with visible markers, so they are easily visible on camera (tape, gaffer marks, etc.).
    </div>
    <p class="wizard-tip">Take your time with accurate markings &ndash; the precision of the final calibration depends on them.</p>

    <div class="wizard-nav">
      <span class="spacer"></span>
      <button type="button" class="save-btn" onclick="wizardNext()">Next</button>
    </div>
  </div>
</div>

<!-- Grid Setup -->
<div class="wizard-content" id="wizard-step-grid">
  <div class="section">
    <div class="section-head">
      <h2>Grid Setup</h2>
      <span class="section-note">Dimensions and position of the rectangular tracking area</span>
    </div>

    <div class="group" id="wizard-units">
      <div class="row">
        <div class="field">
          % include('partials/unit_system_select.tpl', select_id='wizard-unit-system', unit_system=_us.value)
        </div>
      </div>
    </div>

    <svg id="grid-setup-svg" class="wizard-illustration" viewBox="0 0 580 400" style="max-width:580px;" xmlns="http://www.w3.org/2000/svg">
      <!-- Stage floor outline -->
      <rect id="gs-stage" x="40" y="30" width="400" height="250" rx="4" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="1" stroke-dasharray="4,4"/>
      <!-- Grid rectangle (dynamic) -->
      <g id="gs-grid-lines"></g>
      <rect id="gs-grid" rx="2" fill="rgba(255,188,0,0.06)" stroke="rgba(255,188,0,0.5)" stroke-width="1.5"/>
      <!-- Diagonal line (dynamic) -->
      <line id="gs-diag" stroke="rgba(79,141,217,0.4)" stroke-width="1" stroke-dasharray="6,4"/>
      <text id="gs-diag-text" fill="#4f8dd9" font-size="10" font-weight="600" text-anchor="middle"></text>
      <!-- Corner labels -->
      <text id="gs-label-dsl" fill="rgba(247,245,233,0.68)" font-size="10" font-weight="600">DSL</text>
      <text id="gs-label-dsr" fill="rgba(247,245,233,0.68)" font-size="10" font-weight="600">DSR</text>
      <text id="gs-label-usr" fill="rgba(247,245,233,0.68)" font-size="10" font-weight="600">USR</text>
      <text id="gs-label-usl" fill="rgba(247,245,233,0.68)" font-size="10" font-weight="600">USL</text>
      <!-- Width dimension line -->
      <line id="gs-dim-w" stroke="rgba(247,245,233,0.5)" stroke-width="1"/>
      <line id="gs-dim-w-l" stroke="rgba(247,245,233,0.5)" stroke-width="1"/>
      <line id="gs-dim-w-r" stroke="rgba(247,245,233,0.5)" stroke-width="1"/>
      <text id="gs-dim-w-text" fill="var(--text)" font-size="11" font-weight="600" text-anchor="middle"></text>
      <!-- Depth dimension line -->
      <line id="gs-dim-d" stroke="rgba(247,245,233,0.5)" stroke-width="1"/>
      <line id="gs-dim-d-t" stroke="rgba(247,245,233,0.5)" stroke-width="1"/>
      <line id="gs-dim-d-b" stroke="rgba(247,245,233,0.5)" stroke-width="1"/>
      <text id="gs-dim-d-text" fill="var(--text)" font-size="11" font-weight="600" text-anchor="start"></text>
      <!-- Reference point crosshair (dynamic) -->
      <g id="gs-ref">
        <circle r="6" fill="none" stroke="#ffbc00" stroke-width="1.5"/>
        <line x1="-6" y1="0" x2="6" y2="0" stroke="#ffbc00" stroke-width="1.5"/>
        <line x1="0" y1="-6" x2="0" y2="6" stroke="#ffbc00" stroke-width="1.5"/>
      </g>
      <text id="gs-ref-label" fill="#ffbc00" font-size="9" font-weight="700">REF</text>
      <!-- Offset arrows (only shown when offset != 0) -->
      <line id="gs-off-x" stroke="rgba(244,72,72,0.6)" stroke-width="1.5" marker-end="url(#arrowR)"/>
      <text id="gs-off-x-label" fill="#f44848" font-size="9" font-weight="600"></text>
      <line id="gs-off-y" stroke="rgba(92,201,140,0.6)" stroke-width="1.5" marker-end="url(#arrowG)"/>
      <text id="gs-off-y-label" fill="#5cc98c" font-size="9" font-weight="600"></text>
      <!-- Z offset label (shown when z_offset != 0) -->
      <text id="gs-off-z-label" fill="#4f8dd9" font-size="9" font-weight="600"></text>
      <!-- Downstage / Upstage labels -->
      <text id="gs-ds-label" fill="rgba(247,245,233,0.35)" font-size="9" font-weight="600" text-anchor="middle">DOWNSTAGE</text>
      <text id="gs-us-label" fill="rgba(247,245,233,0.35)" font-size="9" font-weight="600" text-anchor="middle">UPSTAGE</text>
      <defs>
        <marker id="arrowR" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6" fill="rgba(244,72,72,0.8)"/></marker>
        <marker id="arrowG" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6" fill="rgba(92,201,140,0.8)"/></marker>
      </defs>
    </svg>

    <div class="group">
      <div class="group-title">Dimensions</div>
      <p class="wizard-help">Enter the width and depth of the rectangular tracking area you marked on stage.</p>
      <div class="row">
        <div class="field">
          <label for="grid_width">Width ({{_len}})</label>
          <input type="{{'text' if _imp else 'number'}}"{{!'' if _imp else ' step="0.1" min="0.1"'}} id="grid_width" value="{{format_length(config.grid.width, _us) if _imp else config.grid.width}}" oninput="onGridInputChanged()">
          % if _imp:
          <small class="metric-echo" id="grid_width-echo">Stored: {{metric_echo(config.grid.width)}}</small>
          % end
        </div>
        <div class="field">
          <label for="grid_depth">Depth ({{_len}})</label>
          <input type="{{'text' if _imp else 'number'}}"{{!'' if _imp else ' step="0.1" min="0.1"'}} id="grid_depth" value="{{format_length(config.grid.depth, _us) if _imp else config.grid.depth}}" oninput="onGridInputChanged()">
          % if _imp:
          <small class="metric-echo" id="grid_depth-echo">Stored: {{metric_echo(config.grid.depth)}}</small>
          % end
        </div>
      </div>
      <div class="row">
        <div class="field">
          <label for="grid_z_offset">Z Offset ({{_len}})</label>
          <input type="{{'text' if _imp else 'number'}}"{{!'' if _imp else ' step="0.01"'}} id="grid_z_offset" value="{{format_length(config.grid.z_offset, _us) if _imp else config.grid.z_offset}}" oninput="onGridInputChanged()">
          % if _imp:
          <small class="metric-echo" id="grid_z_offset-echo">Stored: {{metric_echo(config.grid.z_offset)}}</small>
          % end
        </div>
        <div class="field">
          <label for="grid_spacing">Spacing ({{_len}})</label>
          <input type="{{'text' if _imp else 'number'}}"{{!'' if _imp else ' step="0.1" min="0.1"'}} id="grid_spacing" value="{{format_length(config.grid.spacing, _us) if _imp else config.grid.spacing}}" oninput="onGridInputChanged()">
          % if _imp:
          <small class="metric-echo" id="grid_spacing-echo">Stored: {{metric_echo(config.grid.spacing)}}</small>
          % end
        </div>
      </div>
      <div class="wizard-solved-params" style="margin-top:0.5rem;">
        <div class="wizard-solved-param">
          <span class="param-label">Diagonal</span>
          <span class="param-value" id="grid-diagonal">-</span>
        </div>
      </div>
      <p class="wizard-tip">Verify your grid is rectangular: measure both diagonals on stage &ndash; they must be equal. The expected diagonal is shown above.</p>
    </div>

    <div class="group">
      <div class="group-title">Alignment</div>
      <p class="wizard-help">Define where the Reference Point sits within the grid. By default, it's at the <strong>center of the front edge</strong> (X&nbsp;Offset&nbsp;=&nbsp;0, Y&nbsp;Offset&nbsp;=&nbsp;depth&nbsp;/&nbsp;2).</p>
      <div class="row">
        <div class="field">
          <label for="grid_x_offset">X Offset ({{_len}}) <span style="font-weight:400;text-transform:none;letter-spacing:0;">(stage left +)</span></label>
          <input type="{{'text' if _imp else 'number'}}"{{!'' if _imp else ' step="0.01"'}} id="grid_x_offset" value="{{format_length(config.grid.x_offset, _us) if _imp else config.grid.x_offset}}" oninput="onGridInputChanged()">
          % if _imp:
          <small class="metric-echo" id="grid_x_offset-echo">Stored: {{metric_echo(config.grid.x_offset)}}</small>
          % end
        </div>
        <div class="field">
          <label for="grid_y_offset">Y Offset ({{_len}}) <span style="font-weight:400;text-transform:none;letter-spacing:0;">(upstage +)</span></label>
          <input type="{{'text' if _imp else 'number'}}"{{!'' if _imp else ' step="0.01"'}} id="grid_y_offset" value="{{format_length(config.grid.y_offset, _us) if _imp else config.grid.y_offset}}" oninput="onGridInputChanged()">
          % if _imp:
          <small class="metric-echo" id="grid_y_offset-echo">Stored: {{metric_echo(config.grid.y_offset)}}</small>
          % end
        </div>
      </div>
    </div>

    <p class="wizard-tip">Measure between the marks you placed on stage. Precision matters &ndash; even 10cm off will affect tracking accuracy. Spacing controls the visual grid density (does not affect calibration). If your Reference Point is 1m left of the grid center: set X Offset = &minus;1.</p>

    <div style="margin-top:0.72rem;display:flex;gap:0.5rem;flex-wrap:wrap;">
      <button type="button" class="secondary" onclick="resetGridToDefaults()">Load Defaults</button>
      <button type="button" class="secondary" onclick="restoreGridToLast()">Restore Last</button>
    </div>

    <div class="wizard-nav">
      <button type="button" class="secondary" onclick="wizardPrev()">Back</button>
      <button type="button" class="save-btn" onclick="wizardNext()">Next</button>
    </div>
  </div>
</div>

<!-- Video Source -->
<div class="wizard-content" id="wizard-step-video">
  <div class="section">
    <div class="section-head">
      <h2>Video Source</h2>
      <span class="section-note">Select and configure the camera input</span>
    </div>

    % include('partials/video_source_failure.tpl')

    <div class="group">
      <div class="row">
        <div class="field">
          <label>Source Type</label>
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
      Choose the video input that matches your camera setup. The change applies live – the new pipeline starts within ~1 s of saving.
    </p>

    <div id="wizard-video-saved" class="notice success" role="status" style="display:none"></div>

    <div class="wizard-nav">
      <button type="button" class="secondary" onclick="wizardPrev()">Back</button>
      <span class="spacer"></span>
      <button type="button" class="secondary" onclick="saveWizardVideoSource().catch(wizardVideoSaveFailed)">Save</button>
      <button type="button" class="save-btn" onclick="saveWizardVideoSource().then(function(){ wizardNext(); }).catch(wizardVideoSaveFailed)">Save &amp; Next</button>
    </div>
  </div>
</div>

% if _show_lens:
<!-- Lens: the distortion from lines that are straight in reality (experimental) -->
<div class="wizard-content experimental-feature" id="wizard-step-lens">
  <div class="section">
    <div class="section-head">
      <h2>Lens <span class="badge-experimental">Experimental</span></h2>
      <span class="section-note">Measure the lens from edges that are straight in reality</span>
    </div>

    <div id="lens-container" class="wizard-preview-container" style="display:none;">
      <img id="lens-image" alt="Camera snapshot">
      <canvas id="lens-edges" class="lens-edges" aria-hidden="true"></canvas>
      <svg id="lens-overlay" class="wizard-overlay" xmlns="http://www.w3.org/2000/svg">
        <rect id="lens-hit" class="lens-hit" x="0" y="0" width="100%" height="100%" fill="transparent"/>
        <g id="lens-candidates"></g>
        <g id="lens-lines"></g>
        <g id="lens-pending"></g>
      </svg>
      <div class="wizard-loupe" aria-hidden="true"><canvas width="240" height="240"></canvas></div>
    </div>
    <div id="lens-no-feed" class="wizard-no-feed" style="display:none;">No video feed available. Configure a video source in the Video Source step, then return here and press <strong>Refresh Image</strong>.</div>

    <div id="lens-notice" class="notice" role="status" style="display:none;"></div>
    <div id="lens-status" class="wizard-status" style="display:none;"></div>
    <div id="lens-misfit" class="notice warning" style="display:none;"></div>

    <div style="margin-top:0.72rem;display:flex;gap:0.5rem;flex-wrap:wrap;">
      <button type="button" class="secondary" onclick="loadSnapshot()">Refresh Image</button>
      <button type="button" class="secondary" id="lens-toggle-point" onclick="lensToggleSelectedPoint()" disabled>Point off</button>
      <button type="button" class="secondary" id="lens-delete-line" onclick="lensDeleteSelectedLine()" disabled>Delete line</button>
      <button type="button" class="secondary" id="lens-clear" onclick="lensClearLines()" disabled>Clear lines</button>
% if config.ui.developer_mode:
      <label class="lens-dev"><input type="checkbox" id="lens-show-edges" onchange="lensToggleEdgeView(this.checked)"> Show edges</label>
% end
    </div>

    <div id="lens-result" class="notice" style="display:none;margin-top:0.72rem;">
      <div class="lens-result-main">
        <span class="wizard-meter" aria-hidden="true"><span class="wizard-meter-seg"></span><span class="wizard-meter-seg"></span><span class="wizard-meter-seg"></span><span class="wizard-meter-seg"></span><span class="wizard-meter-seg"></span></span>
        <span id="lens-result-text"></span>
      </div>
      <div class="notice-sub" id="lens-result-hint"></div>
    </div>

    <div class="group" style="margin-top:0.72rem;">
      <div class="group-title">Fine-tune</div>
      <div class="row">
        <div class="field" style="flex:1;min-width:200px;">
          <label for="wiz_lens_k1">Barrel / fisheye</label>
          <div style="display:flex;gap:0.5rem;align-items:center;">
            <input type="range" id="wiz_lens_k1_range" min="-0.6" max="0.6" step="0.005"
                   value="{{config.camera.lens_k1}}" style="flex:1;" oninput="onWizardLensInput('k1', 'range')"
                   aria-label="Barrel / fisheye slider">
            <input type="number" id="wiz_lens_k1" step="0.005"
                   value="{{config.camera.lens_k1}}" style="width:6rem;" oninput="onWizardLensInput('k1', 'number')">
          </div>
        </div>
        <div class="field" style="flex:1;min-width:200px;">
          <label for="wiz_lens_k2">Edge fit</label>
          <div style="display:flex;gap:0.5rem;align-items:center;">
            <input type="range" id="wiz_lens_k2_range" min="-0.4" max="0.4" step="0.005"
                   value="{{config.camera.lens_k2}}" style="flex:1;" oninput="onWizardLensInput('k2', 'range')"
                   aria-label="Edge fit slider">
            <input type="number" id="wiz_lens_k2" step="0.005"
                   value="{{config.camera.lens_k2}}" style="width:6rem;" oninput="onWizardLensInput('k2', 'number')">
          </div>
        </div>
      </div>
      <div id="wiz-lens-error" class="wizard-field-error" style="display:none;">This pair folds the overlay inside the frame. Bring either value closer to 0.</div>
    </div>

    <p class="wizard-help" style="margin-top:0.72rem;">
      <strong>Tap a dashed suggestion</strong> that is straight in reality, or click the start and the end of a straight edge yourself: a stage edge, a tape line, a truss. Drag a point to correct it. Lines near the picture's edges and corners tell the most.
    </p>
    <p class="wizard-tip"><strong>Arrow keys</strong> nudge the selected point (<strong>Shift</strong> for bigger steps), <strong>Space</strong> switches a middle point off or on, <strong>Delete</strong> removes its line, <strong>Esc</strong> cancels a started line. Skipping this step keeps the current values.</p>

    <div class="wizard-nav">
      <button type="button" class="secondary" onclick="wizardPrev()">Back</button>
      <button type="button" class="save-btn" onclick="wizardNext()">Next</button>
    </div>
  </div>
</div>
% else:
% # Without the Lens step the stored pair rides along so Apply keeps it.
<input type="hidden" id="wiz_lens_k1" value="{{config.camera.lens_k1}}">
<input type="hidden" id="wiz_lens_k2" value="{{config.camera.lens_k2}}">
% end

<!-- Camera Position -->
<div class="wizard-content" id="wizard-step-camera">
  <div class="section">
    <div class="section-head">
      <h2>Camera Position</h2>
      <span class="section-note">Physical camera position and orientation relative to the Reference Point</span>
    </div>

    <svg id="cam-pos-svg" class="wizard-illustration" viewBox="0 0 580 400" style="max-width:725px;" xmlns="http://www.w3.org/2000/svg">
      <!-- Grid quad (isometric) -->
      <polygon id="cp-grid" fill="rgba(255,188,0,0.06)" stroke="rgba(255,188,0,0.5)" stroke-width="1.5"/>
      <g id="cp-grid-lines"></g>
      <!-- Field-of-view ground footprint on the grid plane -->
      <polygon id="cp-fov-area" fill="rgba(255,188,0,0.08)" stroke="rgba(255,188,0,0.4)" stroke-width="1" stroke-dasharray="3,3"/>
      <!-- Corner labels -->
      <text id="cp-label-dsl" fill="rgba(247,245,233,0.5)" font-size="9" font-weight="600">DSL</text>
      <text id="cp-label-dsr" fill="rgba(247,245,233,0.5)" font-size="9" font-weight="600">DSR</text>
      <text id="cp-label-usr" fill="rgba(247,245,233,0.5)" font-size="9" font-weight="600">USR</text>
      <text id="cp-label-usl" fill="rgba(247,245,233,0.5)" font-size="9" font-weight="600">USL</text>
      <!-- Grid Z offset indicator (ref ground to grid plane) -->
      <line id="cp-gz-line" stroke="rgba(79,141,217,0.5)" stroke-width="1" stroke-dasharray="4,3"/>
      <text id="cp-gz-label" fill="#4f8dd9" font-size="9" font-weight="600"></text>
      <circle id="cp-gz-dot" r="3" fill="rgba(79,141,217,0.4)"/>
      <!-- Reference point on grid plane -->
      <g id="cp-ref">
        <circle r="5" fill="none" stroke="#ffbc00" stroke-width="1.5"/>
        <line x1="-5" y1="0" x2="5" y2="0" stroke="#ffbc00" stroke-width="1.5"/>
        <line x1="0" y1="-5" x2="0" y2="5" stroke="#ffbc00" stroke-width="1.5"/>
      </g>
      <text id="cp-ref-label" fill="#ffbc00" font-size="9" font-weight="700">REF</text>
      <!-- Camera body (box: lens square + body length) -->
      <polygon id="cp-cam-body" fill="rgba(255,255,255,0.12)" stroke="rgba(255,255,255,0.4)" stroke-width="1"/>
      <polygon id="cp-cam-lens" fill="rgba(255,255,255,0.2)" stroke="rgba(255,255,255,0.5)" stroke-width="1"/>
      <text id="cp-cam-label" fill="rgba(247,245,233,0.68)" font-size="10" font-weight="600">Camera</text>
      <!-- X measurement line (ref to cam floor X) -->
      <line id="cp-x-line" stroke="rgba(244,72,72,0.5)" stroke-width="1.5"/>
      <text id="cp-x-label" fill="#f44848" font-size="9" font-weight="600"></text>
      <!-- Y measurement line (ref+X to cam floor) -->
      <line id="cp-y-line" stroke="rgba(92,201,140,0.5)" stroke-width="1.5"/>
      <text id="cp-y-label" fill="#5cc98c" font-size="9" font-weight="600"></text>
      <!-- Vertical pole (Z height) -->
      <line id="cp-z-line" stroke="rgba(79,141,217,0.5)" stroke-width="1" stroke-dasharray="4,3"/>
      <text id="cp-z-label" fill="#4f8dd9" font-size="9" font-weight="600"></text>
      <!-- Floor projection dot -->
      <circle id="cp-floor-dot" r="3" fill="rgba(79,141,217,0.4)"/>
      <!-- Dashed line from camera to ref -->
      <line id="cp-sight-line" stroke="rgba(255,255,255,0.15)" stroke-width="1" stroke-dasharray="4,4"/>
      <!-- FOV frustum edges (lens to ground footprint corners) -->
      <line id="cp-fov-bl" stroke="rgba(255,188,0,0.3)" stroke-width="1" stroke-dasharray="3,3"/>
      <line id="cp-fov-br" stroke="rgba(255,188,0,0.3)" stroke-width="1" stroke-dasharray="3,3"/>
      <line id="cp-fov-tl" stroke="rgba(255,188,0,0.22)" stroke-width="1" stroke-dasharray="3,3"/>
      <line id="cp-fov-tr" stroke="rgba(255,188,0,0.22)" stroke-width="1" stroke-dasharray="3,3"/>
      <text id="cp-fov-label" fill="rgba(255,188,0,0.6)" font-size="9" font-weight="600" text-anchor="middle"></text>
      <!-- Axis labels -->
      <text id="cp-ds-label" fill="rgba(247,245,233,0.3)" font-size="9" font-weight="600" text-anchor="middle">DOWNSTAGE</text>
      <text id="cp-us-label" fill="rgba(247,245,233,0.3)" font-size="9" font-weight="600" text-anchor="middle">UPSTAGE</text>
    </svg>

    <div class="group">
      <div class="group-title">Position</div>
      <p class="wizard-help">Enter the camera's physical position relative to the Reference Point, in {{_len}}. These use PSN theatrical coordinates: X = stage left, Y = upstage, Z = up.</p>
      <div class="row">
        <div class="field">
          <label for="cam_pos_x">Pos X ({{_len}}) <span style="font-weight:400;text-transform:none;letter-spacing:0;">(stage left +)</span></label>
          <input type="{{'text' if _imp else 'number'}}"{{!'' if _imp else ' step="0.01"'}} id="cam_pos_x" value="{{format_length(config.camera.pos_x, _us) if _imp else config.camera.pos_x}}" oninput="onCamInputChanged()">
          % if _imp:
          <small class="metric-echo" id="cam_pos_x-echo">Stored: {{metric_echo(config.camera.pos_x)}}</small>
          % end
        </div>
        <div class="field">
          <label for="cam_pos_y">Pos Y ({{_len}}) <span style="font-weight:400;text-transform:none;letter-spacing:0;">(upstage +)</span></label>
          <input type="{{'text' if _imp else 'number'}}"{{!'' if _imp else ' step="0.01"'}} id="cam_pos_y" value="{{format_length(config.camera.pos_y, _us) if _imp else config.camera.pos_y}}" oninput="onCamInputChanged()">
          % if _imp:
          <small class="metric-echo" id="cam_pos_y-echo">Stored: {{metric_echo(config.camera.pos_y)}}</small>
          % end
        </div>
        <div class="field">
          <label for="cam_pos_z">Pos Z ({{_len}}) <span style="font-weight:400;text-transform:none;letter-spacing:0;">(height)</span></label>
          <input type="{{'text' if _imp else 'number'}}"{{!'' if _imp else ' step="0.01"'}} id="cam_pos_z" value="{{format_length(config.camera.pos_z, _us) if _imp else config.camera.pos_z}}" oninput="onCamInputChanged()">
          % if _imp:
          <small class="metric-echo" id="cam_pos_z-echo">Stored: {{metric_echo(config.camera.pos_z)}}</small>
          % end
        </div>
      </div>
    </div>
    <div class="group">
      <div class="group-title">Orientation</div>
      <p class="wizard-help">Enter the camera's angle: Pitch tilts down, Yaw is the direction it faces (0&deg; looks upstage, 180&deg; looks back at the house).</p>
      <div class="row">
        <div class="field">
          <label for="cam_pitch">Pitch <span style="font-weight:400;text-transform:none;letter-spacing:0;">(down &minus;)</span></label>
          <input type="number" step="0.1" min="-90" max="-0.5" id="cam_pitch" value="{{config.camera.pitch}}" oninput="onCamInputChanged()">
          <div id="cam-pitch-error" class="wizard-field-error" style="display:none;">Pitch must be between &minus;0.5&deg; and &minus;90&deg;</div>
        </div>
        <div class="field">
          <label for="cam_yaw">Yaw <span style="font-weight:400;text-transform:none;letter-spacing:0;">(left &minus;)</span></label>
          <input type="number" step="0.1" id="cam_yaw" value="{{config.camera.yaw}}" oninput="onCamInputChanged()">
        </div>
        <div class="field">
          <label for="cam_roll">Roll</label>
          <input type="number" step="0.1" id="cam_roll" value="{{config.camera.roll}}" oninput="onCamInputChanged()">
        </div>
      </div>
    </div>
    <div class="group">
      <div class="group-title">Lens</div>
      <div class="row">
        <div class="field">
          <label for="cam_fov">Horizontal Field of View (&deg;)</label>
          <input type="number" step="0.1" id="cam_fov" value="{{config.camera.fov}}" oninput="onHfovEdited()">
        </div>
        <div class="field" id="cam_sensor_field">
          <label for="cam_sensor">Sensor Size</label>
          <select id="cam_sensor" onchange="onSensorOrFocalChanged()"></select>
        </div>
        <div class="field" id="cam_sensor_custom_field" style="display:none;">
          <label for="cam_sensor_custom">Sensor Width (mm)</label>
          <input type="number" step="0.01" min="0" id="cam_sensor_custom" oninput="onSensorOrFocalChanged()">
        </div>
        <div class="field">
          <label for="cam_focal">Focal Length (mm)</label>
          <input type="number" step="0.1" min="0" id="cam_focal"
                 value="{{config.camera.focal_length_mm if config.camera.focal_length_mm is not None else ''}}"
                 oninput="onSensorOrFocalChanged()">
        </div>
        <input type="hidden" id="cam_sensor_width_initial"
               value="{{config.camera.sensor_width_mm if config.camera.sensor_width_mm is not None else ''}}">
      </div>
      <p class="wizard-tip">Enter the horizontal FOV from your camera datasheet, or pick your sensor size and focal length and we'll compute it.</p>
    </div>

    <div style="margin-top:0.72rem;display:flex;gap:0.5rem;flex-wrap:wrap;">
      <button type="button" class="secondary" onclick="resetCameraToDefaults()">Load Defaults</button>
      <button type="button" class="secondary" onclick="restoreCameraToLast()">Restore Last</button>
    </div>

    <div class="wizard-nav">
      <button type="button" class="secondary" onclick="wizardPrev()">Back</button>
      <button type="button" class="save-btn" onclick="wizardNext()">Next</button>
    </div>
  </div>
</div>

<!-- Reference Mapping (coarse calibration) -->
<div class="wizard-content" id="wizard-step-ref">
  <div class="section">
    <div class="section-head">
      <h2>Reference Mapping</h2>
      <span class="section-note">Drag the marker to the physical Reference Point mark</span>
    </div>

    <div id="coarse-container" class="wizard-preview-container" style="display:none;">
      <div id="coarse-full-view">
        <img id="coarse-image" alt="Camera snapshot">
        <svg id="coarse-overlay" class="wizard-overlay" xmlns="http://www.w3.org/2000/svg">
          <polygon id="coarse-quad" fill="rgba(255,188,0,0.06)" stroke="rgba(255,188,0,0.5)" stroke-width="2" points="0,0"/>
          <g id="coarse-zoff"></g>
          <g id="coarse-corners"></g>
          <g id="coarse-ref"></g>
        </svg>
      </div>
      % # Fine-adjust view (hidden until toggled): one 4× crop of the snapshot
      % # centred on the Reference Point, dragged like a Corner Pinning box.
      <div id="coarse-zoom-view" class="fine-zoom-box" style="display:none;">
        <svg id="coarse-zoom-svg" class="fine-zoom-svg" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid slice" role="group" aria-label="Fine adjust the Reference Point" tabindex="0">
          <image data-fine-zoom-image x="0" y="0"/>
          <polygon id="coarse-zoom-quad" fill="rgba(255,188,0,0.06)" stroke="rgba(255,188,0,0.5)" stroke-width="2" vector-effect="non-scaling-stroke" points="0,0"/>
          <g id="coarse-zoom-ref"></g>
        </svg>
      </div>
      <div class="wizard-loupe" aria-hidden="true"><canvas width="240" height="240"></canvas></div>
    </div>
    <div id="coarse-no-feed" class="wizard-no-feed" style="display:none;">No video feed available. Configure a video source in the Video Source step, then return here and press <strong>Refresh Image</strong>.</div>

    <div id="coarse-status" class="wizard-status" style="display:none;"></div>

    <div style="margin-top:0.72rem;display:flex;gap:0.5rem;flex-wrap:wrap;">
      <button type="button" class="secondary" onclick="loadSnapshot()">Refresh Image</button>
      <button type="button" class="secondary" onclick="resetCoarseCalibration()">Reset</button>
      <button type="button" class="secondary" id="coarse-zoom-toggle" onclick="toggleCoarseZoomMode()" disabled>Fine adjust</button>
    </div>

    <p class="wizard-help" style="margin-top:0.72rem;">
      <strong>Drag the marker</strong> to the physical Reference Point mark visible on the stage. All corners move together with the reference point to roughly align the overlay with your stage.
    </p>
    <p class="wizard-tip">For pixel-precise placement, click <strong>Fine adjust</strong> to switch to a 4×-zoomed view of the marker. You can also click the marker and use <strong>arrow keys</strong> to nudge it precisely (hold <strong>Shift</strong> for larger steps).</p>

    <div class="wizard-nav">
      <button type="button" class="secondary" onclick="wizardPrev()">Back</button>
      <button type="button" class="save-btn" onclick="wizardNext()">Next</button>
    </div>
  </div>
</div>

<!-- Corner Pinning (fine calibration) -->
<div class="wizard-content" id="wizard-step-corners">
  <div class="section">
    <div class="section-head">
      <h2>Corner Pinning</h2>
      <span class="section-note">Drag each corner to its physical stage mark for precise calibration</span>
    </div>

    <div id="fine-container" class="wizard-preview-container" style="display:none;">
      <!-- Full-image view (default). -->
      <div id="fine-full-view">
        <img id="fine-image" alt="Camera snapshot">
        <svg id="fine-overlay" class="wizard-overlay" xmlns="http://www.w3.org/2000/svg">
          <polygon id="fine-quad" fill="rgba(255,188,0,0.06)" stroke="rgba(255,188,0,0.5)" stroke-width="2" points="0,0"/>
          <g id="fine-grid"></g>
          <g id="fine-corners"></g>
        </svg>
        <div class="wizard-loupe" aria-hidden="true"><canvas width="240" height="240"></canvas></div>
      </div>
      % # Fine-adjust 4-box view (hidden until toggled). Each box:
      % # 4×-zoomed crop of snapshot, centred on corner. data-corner
      % # hooks drag handlers. SVG groups: -edges (partial polygon to
      % # neighbors), -marker (centre handle operator drags).
      % # aria-label for screen readers (semantic stage position names).
      % # Box order mirrors a front-of-house image: stage left on the right,
      % # so the top row is USR, USL and the bottom row DSR, DSL.
      <div id="fine-zoom-view" style="display:none;">
        <div class="fine-zoom-grid" id="fine-zoom-grid">
          <div class="fine-zoom-box" data-corner="USR">
            <svg class="fine-zoom-svg" data-corner="USR" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid slice" role="group" aria-label="Fine adjust upstage-right corner">
              <image data-fine-zoom-image x="0" y="0"/>
              <g data-fine-zoom-edges></g>
              <g data-fine-zoom-marker></g>
            </svg>
            <span class="fine-zoom-corner-label">USR</span>
          </div>
          <div class="fine-zoom-box" data-corner="USL">
            <svg class="fine-zoom-svg" data-corner="USL" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid slice" role="group" aria-label="Fine adjust upstage-left corner">
              <image data-fine-zoom-image x="0" y="0"/>
              <g data-fine-zoom-edges></g>
              <g data-fine-zoom-marker></g>
            </svg>
            <span class="fine-zoom-corner-label">USL</span>
          </div>
          <div class="fine-zoom-box" data-corner="DSR">
            <svg class="fine-zoom-svg" data-corner="DSR" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid slice" role="group" aria-label="Fine adjust downstage-right corner">
              <image data-fine-zoom-image x="0" y="0"/>
              <g data-fine-zoom-edges></g>
              <g data-fine-zoom-marker></g>
            </svg>
            <span class="fine-zoom-corner-label">DSR</span>
          </div>
          <div class="fine-zoom-box" data-corner="DSL">
            <svg class="fine-zoom-svg" data-corner="DSL" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid slice" role="group" aria-label="Fine adjust downstage-left corner">
              <image data-fine-zoom-image x="0" y="0"/>
              <g data-fine-zoom-edges></g>
              <g data-fine-zoom-marker></g>
            </svg>
            <span class="fine-zoom-corner-label">DSL</span>
          </div>
        </div>
      </div>
    </div>
    <div id="fine-no-feed" class="wizard-no-feed" style="display:none;">No video feed available. Configure a video source in the Video Source step, then return here and press <strong>Refresh Image</strong>.</div>

    <div id="fine-status" class="wizard-status" style="display:none;"></div>

    <div id="fine-solved-params" class="wizard-solved-params" style="display:none;">
      <div class="wizard-solved-param"><span class="param-label">Pos X</span><span class="param-value" id="solved-pos-x">-</span></div>
      <div class="wizard-solved-param"><span class="param-label">Pos Y</span><span class="param-value" id="solved-pos-y">-</span></div>
      <div class="wizard-solved-param"><span class="param-label">Pos Z</span><span class="param-value" id="solved-pos-z">-</span></div>
      <div class="wizard-solved-param"><span class="param-label">Pitch</span><span class="param-value" id="solved-pitch">-</span></div>
      <div class="wizard-solved-param"><span class="param-label">Yaw</span><span class="param-value" id="solved-yaw">-</span></div>
      <div class="wizard-solved-param"><span class="param-label">Roll</span><span class="param-value" id="solved-roll">-</span></div>
      <div class="wizard-solved-param"><span class="param-label">FOV</span><span class="param-value" id="solved-fov">-</span></div>
    </div>

    <div style="margin-top:0.72rem;display:flex;gap:0.5rem;flex-wrap:wrap;">
      <button type="button" class="secondary" onclick="loadSnapshot()">Refresh Image</button>
      <button type="button" class="secondary" onclick="resetCornerPinning()">Reset Corners</button>
      <!-- Fine adjust toggle. Disabled until snapshot loads.
           Label flips between "Fine adjust" and "Show full image". -->
      <button type="button" class="secondary" id="fine-zoom-toggle"
              onclick="toggleFineZoomMode()" disabled
              title="Load a snapshot first">Fine adjust</button>
    </div>

    <p class="wizard-help" style="margin-top:0.72rem;">
      <strong>Drag each corner marker</strong> to its physical mark on the stage. The grid's lines follow, spaced as the Operator Screen draws them. The labels are stage positions: <strong>DSL</strong>/<strong>USL</strong> are stage left, <strong>DSR</strong>/<strong>USR</strong> are stage right (D = downstage/front, U = upstage/back). With a front-of-house camera you see the audience's view, so stage left is on the right of the image (audience right) and stage right is on the left (audience left).
    </p>
    <p class="wizard-tip">Start with the two downstage corners (front), then adjust the upstage corners (back). If a corner turns red, the shape is invalid. For pixel-precise placement, click <strong>Fine adjust</strong> to switch to a 4×-zoomed per-corner view.</p>
    <p>If your corners are invalid, three things could be wrong: The position of your camera relative to the reference point, your marked rectangle has not the same size as the grid, or the corners of your rectangle are not 90 degrees.</p>
    <p>Click a corner or use <strong>Tab</strong> to select it, then use <strong>arrow keys</strong> to nudge (hold <strong>Shift</strong> for larger steps).</p>

    <div class="wizard-nav">
      <button type="button" class="secondary" onclick="wizardPrev()">Back</button>
      <button type="button" class="save-btn" onclick="wizardNext()">Next</button>
    </div>
  </div>
</div>

<!-- Review & Apply -->
<div class="wizard-content" id="wizard-step-review">
  <div class="section">
    <div class="section-head">
      <h2>Review</h2>
      <span class="section-note">Verify all values before applying</span>
    </div>

    <div id="review-container" class="wizard-preview-container" style="display:none;">
      <img id="review-image" alt="Camera snapshot">
      <svg id="review-overlay" class="wizard-overlay" xmlns="http://www.w3.org/2000/svg">
        <polygon id="review-quad" fill="rgba(255,188,0,0.06)" stroke="rgba(92,201,140,0.8)" stroke-width="2" points="0,0"/>
        <g id="review-grid"></g>
        <g id="review-zoff"></g>
        <g id="review-corners"></g>
        <g id="review-ref"></g>
      </svg>
    </div>
    <div id="review-no-feed" class="wizard-no-feed" style="display:none;">No video feed available. Values below are still valid, but the visual review is unavailable.</div>

    <div id="review-status" class="wizard-status" style="display:none;"></div>
% if _show_lens:
    <div id="review-lens-caution" class="notice warning" style="display:none;">Lens changed &ndash; redo Corner Pinning so the position matches.</div>
% end

    <div class="group">
      <div class="group-title">Camera</div>
      <div class="wizard-solved-params" id="review-camera-params">
        <div class="wizard-solved-param"><span class="param-label">Pos X</span><span class="param-value" id="review-cam-pos-x">-</span></div>
        <div class="wizard-solved-param"><span class="param-label">Pos Y</span><span class="param-value" id="review-cam-pos-y">-</span></div>
        <div class="wizard-solved-param"><span class="param-label">Pos Z</span><span class="param-value" id="review-cam-pos-z">-</span></div>
        <div class="wizard-solved-param"><span class="param-label">Pitch</span><span class="param-value" id="review-cam-pitch">-</span></div>
        <div class="wizard-solved-param"><span class="param-label">Yaw</span><span class="param-value" id="review-cam-yaw">-</span></div>
        <div class="wizard-solved-param"><span class="param-label">Roll</span><span class="param-value" id="review-cam-roll">-</span></div>
        <div class="wizard-solved-param"><span class="param-label">FOV</span><span class="param-value" id="review-cam-fov">-</span></div>
% if _show_lens:
        <div class="wizard-solved-param"><span class="param-label">Barrel / fisheye</span><span class="param-value" id="review-lens-k1">-</span></div>
        <div class="wizard-solved-param"><span class="param-label">Edge fit</span><span class="param-value" id="review-lens-k2">-</span></div>
        <div class="wizard-solved-param"><span class="param-label">Lens coverage</span><span class="param-value" id="review-lens-rating"><span class="wizard-meter" aria-hidden="true"><span class="wizard-meter-seg"></span><span class="wizard-meter-seg"></span><span class="wizard-meter-seg"></span><span class="wizard-meter-seg"></span><span class="wizard-meter-seg"></span></span> <span id="review-lens-rating-text">-</span></span></div>
% end
      </div>
    </div>

    <div class="group">
      <div class="group-title">Grid</div>
      <div class="wizard-solved-params" id="review-grid-params">
        <div class="wizard-solved-param"><span class="param-label">Width</span><span class="param-value" id="review-grid-width">-</span></div>
        <div class="wizard-solved-param"><span class="param-label">Depth</span><span class="param-value" id="review-grid-depth">-</span></div>
        <div class="wizard-solved-param"><span class="param-label">Spacing</span><span class="param-value" id="review-grid-spacing">-</span></div>
        <div class="wizard-solved-param"><span class="param-label">X Offset</span><span class="param-value" id="review-grid-x-offset">-</span></div>
        <div class="wizard-solved-param"><span class="param-label">Y Offset</span><span class="param-value" id="review-grid-y-offset">-</span></div>
        <div class="wizard-solved-param"><span class="param-label">Z Offset</span><span class="param-value" id="review-grid-z-offset">-</span></div>
      </div>
    </div>

    <div class="wizard-nav">
      <button type="button" class="secondary" onclick="wizardPrev()">Back</button>
      <span class="spacer"></span>
      <button type="button" class="btn-danger" onclick="discardAndLeave()">Discard &amp; Leave</button>
      <button type="button" class="save-btn" onclick="applyAndFinish()" id="btn-apply-finish">Apply &amp; Finish</button>
    </div>
  </div>
</div>

<script>
(function() {
  'use strict';

  // #154: shared length formatter/parser (seeded from config.ui.unit_system
  // in base.tpl). All wizard inputs hold ft/in text in imperial; values flow
  // through wizReadLen/wizWriteLen so the model + API stay in METRES.
  var WUNIT = window.OpenFollow.units;

  // ---------------------------------------------------------------
  // Wizard State
  // ---------------------------------------------------------------
  var STORAGE_KEY = 'psnfs:wizard-state';
  var currentStep = 0;
  var snapshotUrl = null;
  var imageWidth = 0;
  var imageHeight = 0;
  var originalFov = parseFloat(document.getElementById('cam_fov').value);

  // The pose (``snapshotPose``) before Reference Mapping, for its Reset.
  var preCoarsePose = null;

  // The pose before the first solve of the current corner-pinning session.
  // Every solve writes its camera into the form, so without it ``Reset
  // Corners`` would re-project from the solved camera and the corners would
  // stay where the operator dragged them. Reset restores it AND clears it so
  // the next solve takes a fresh one for the next session.
  var preCornerPinningPose = null;
  // Every solve counts here; an answer to an older one is dropped.
  var poseSeq = 0;

  // Corner screen positions for fine calibration
  var cornerPositions = { DSL: null, DSR: null, USR: null, USL: null };
  var CORNER_NAMES = ['DSL', 'DSR', 'USR', 'USL'];

  // Debounce timer for keyboard arrow moves
  var arrowDebounceTimer = null;

  // Server-loaded values (captured before session restore, for "Restore Last").
  // Stored in METRES (lengths via wizReadLen) so the setters below can render
  // them in whatever unit is active; angles are kept verbatim (degrees).
  var lastGridValues = {
    width: wizReadLen('grid_width'),
    depth: wizReadLen('grid_depth'),
    spacing: wizReadLen('grid_spacing'),
    x_offset: wizReadLen('grid_x_offset'),
    y_offset: wizReadLen('grid_y_offset'),
    z_offset: wizReadLen('grid_z_offset'),
  };
  var lastCameraValues = {
    pos_x: wizReadLen('cam_pos_x'),
    pos_y: wizReadLen('cam_pos_y'),
    pos_z: wizReadLen('cam_pos_z'),
    pitch: document.getElementById('cam_pitch').value,
    yaw: document.getElementById('cam_yaw').value,
    roll: document.getElementById('cam_roll').value,
    fov: document.getElementById('cam_fov').value,
  };

  // Defaults (from dataclass definitions)
  var GRID_DEFAULTS = { width: 10, depth: 6, spacing: 1, x_offset: 0, y_offset: 3, z_offset: 0 };
  var CAMERA_DEFAULTS = { pos_x: 0, pos_y: -11, pos_z: 6, pitch: -22, yaw: 0, roll: 0, fov: 60 };

  // Length input <-> metres helpers (WUNIT defined at the top of the IIFE).
  // wizReadLen returns METRES (NaN if unparseable); wizWriteLen takes METRES
  // and renders in the active unit + refreshes the "Stored:" echo.
  function wizReadLen(id) {
    var el = document.getElementById(id);
    if (!el) return NaN;
    return WUNIT.isImperial() ? WUNIT.parseLength(el.value) : parseFloat(el.value);
  }
  function wizUpdateEcho(id, meters) {
    var e = document.getElementById(id + '-echo');
    if (e && isFinite(meters)) e.textContent = 'Stored: ' + WUNIT.metricEcho(meters);
  }
  function wizWriteLen(id, meters) {
    var el = document.getElementById(id);
    if (!el) return;
    el.value = WUNIT.isImperial() ? WUNIT.formatLength(meters) : meters;
    wizUpdateEcho(id, meters);
  }

  // Length input ids whose "Stored: X.XXX m" echoes must track live typing.
  var GRID_LEN_IDS = ['grid_width', 'grid_depth', 'grid_spacing',
                      'grid_x_offset', 'grid_y_offset', 'grid_z_offset'];
  var CAM_LEN_IDS = ['cam_pos_x', 'cam_pos_y', 'cam_pos_z'];
  // Re-derive each echo from the current input on every edit. wizUpdateEcho
  // is a no-op on a partial/invalid parse (NaN), so the echo holds its last
  // valid value while the operator is mid-type rather than going stale.
  function refreshLengthEchoes(ids) {
    ids.forEach(function(id) { wizUpdateEcho(id, wizReadLen(id)); });
  }

  var LEN_FIELD_LABELS = {
    grid_width: 'Width', grid_depth: 'Depth', grid_spacing: 'Spacing',
    grid_x_offset: 'X Offset', grid_y_offset: 'Y Offset', grid_z_offset: 'Z Offset',
    cam_pos_x: 'Pos X', cam_pos_y: 'Pos Y', cam_pos_z: 'Pos Z',
  };
  // Grid dimensions that must be a positive length – in imperial these are
  // free-text (no HTML min), so a typed 0 / negative would otherwise sail
  // through into the solve geometry (width/2, divide-by-spacing) and Apply.
  var POSITIVE_LEN_IDS = ['grid_width', 'grid_depth', 'grid_spacing'];
  // Length inputs whose value would reach project / solve / Apply degenerate.
  // Dimensions (width/depth/spacing) must parse to a positive number – empty,
  // unparseable, or <= 0 are all invalid. Offsets / camera pos only need to
  // parse when non-empty (0 and negatives are legitimate there; empty stays
  // lenient -> 0, the long-standing default).
  function invalidLengthFields() {
    var bad = [];
    GRID_LEN_IDS.concat(CAM_LEN_IDS).forEach(function(id) {
      var el = document.getElementById(id);
      if (!el) return;
      var empty = el.value.trim() === '';
      var v = wizReadLen(id);
      var invalid = POSITIVE_LEN_IDS.indexOf(id) !== -1
        ? (empty || !isFinite(v) || v <= 0)
        : (!empty && !isFinite(v));
      if (invalid) bad.push(LEN_FIELD_LABELS[id] || id);
    });
    return bad;
  }

  function getState() {
    return {
      camera: {
        pos_x: wizReadLen('cam_pos_x'),
        pos_y: wizReadLen('cam_pos_y'),
        pos_z: wizReadLen('cam_pos_z'),
        pitch: parseFloat(document.getElementById('cam_pitch').value),
        yaw: parseFloat(document.getElementById('cam_yaw').value),
        roll: parseFloat(document.getElementById('cam_roll').value),
        fov: parseFloat(document.getElementById('cam_fov').value),
        lens_k1: wizReadLensCoeff('wiz_lens_k1'),
        lens_k2: wizReadLensCoeff('wiz_lens_k2'),
      },
      lens: {
        sensor_id: (document.getElementById('cam_sensor') || {}).value || '',
        sensor_custom: (document.getElementById('cam_sensor_custom') || {}).value || '',
        focal: (document.getElementById('cam_focal') || {}).value || '',
      },
      grid: {
        width: wizReadLen('grid_width') || 0,
        depth: wizReadLen('grid_depth') || 0,
        x_offset: wizReadLen('grid_x_offset') || 0,
        y_offset: wizReadLen('grid_y_offset') || 0,
        z_offset: wizReadLen('grid_z_offset') || 0,
        spacing: wizReadLen('grid_spacing') || 0,
      }
    };
  }

  // Lens coefficients: the one bound on the pair is that the warp must not fold
  // inside the frame (mirrors openfollow.lens_model.lens_warp_is_valid).
  function wizLensFoldRadius(k1, k2) {
    if (k2 === 0) return k1 < 0 ? Math.sqrt(-1 / (3 * k1)) : Infinity;
    var disc = 9 * k1 * k1 - 20 * k2;
    if (disc < 0) return Infinity;
    var sq = Math.sqrt(disc);
    var sLo = (-3 * k1 - sq) / (10 * k2), sHi = (-3 * k1 + sq) / (10 * k2);
    var s = Infinity;
    if (sLo > 0) s = sLo;
    if (sHi > 0 && sHi < s) s = sHi;
    return Math.sqrt(s);
  }
  function wizLensIsValid(k1, k2) {
    return isFinite(k1) && isFinite(k2) && wizLensFoldRadius(k1, k2) > 1;
  }
  function wizLensPairValid() {
    return wizLensIsValid(wizReadLensCoeff('wiz_lens_k1'), wizReadLensCoeff('wiz_lens_k2'));
  }

  function wizReadLensCoeff(id) {
    var el = document.getElementById(id);
    if (!el) return 0;
    var v = parseFloat(el.value);
    return isFinite(v) ? v : 0;
  }

  function wizWriteLensCoeff(id, value) {
    var num = document.getElementById(id);
    var range = document.getElementById(id + '_range');
    if (num) num.value = value;
    if (!range) return;
    // A value past the slider's span widens it, so touching the slider never pulls the value back.
    var v = parseFloat(value);
    if (isFinite(v)) {
      range.min = Math.min(parseFloat(range.min), v);
      range.max = Math.max(parseFloat(range.max), v);
    }
    range.value = value;
  }

  // A slider or number edit on the Lens step: mirror the pair, flag a folding
  // pair, redraw the predicted curves and the projected grid, and persist.
  window.onWizardLensInput = function(which, source) {
    var num = document.getElementById('wiz_lens_' + which);
    var range = document.getElementById('wiz_lens_' + which + '_range');
    if (num && range) {
      if (source === 'range') num.value = range.value; else wizWriteLensCoeff('wiz_lens_' + which, num.value);
    }
    var err = document.getElementById('wiz-lens-error');
    if (err) err.style.display = wizLensPairValid() ? 'none' : 'block';
    lensRecomputeCurves();
    renderLens();
    onLensCoeffChanged();
    projectAndOverlay();
  };

  function saveToSession() {
    try {
      var state = getState();
      state._stepKey = WIZ_STEPS[currentStep];
      state._originalFov = originalFov;
      state._lensLines = lensLines;
      state._lensImage = lensImageSize;
      state._lensFit = lensFit;
      state._pinnedCorners = pinnedCorners;
      state._lensSolvedWith = lensSolvedWith;
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch(e) {}
  }

  function restoreFromSession() {
    try {
      var raw = sessionStorage.getItem(STORAGE_KEY);
      if (!raw) return false;
      var state = JSON.parse(raw);
      if (state.camera) {
        wizWriteLen('cam_pos_x', state.camera.pos_x);
        wizWriteLen('cam_pos_y', state.camera.pos_y);
        wizWriteLen('cam_pos_z', state.camera.pos_z);
        document.getElementById('cam_pitch').value = state.camera.pitch;
        document.getElementById('cam_yaw').value = state.camera.yaw;
        document.getElementById('cam_roll').value = state.camera.roll;
        document.getElementById('cam_fov').value = state.camera.fov;
        if (state.camera.lens_k1 !== undefined) wizWriteLensCoeff('wiz_lens_k1', state.camera.lens_k1);
        if (state.camera.lens_k2 !== undefined) wizWriteLensCoeff('wiz_lens_k2', state.camera.lens_k2);
      }
      if (state.grid) {
        wizWriteLen('grid_width', state.grid.width);
        wizWriteLen('grid_depth', state.grid.depth);
        wizWriteLen('grid_x_offset', state.grid.x_offset);
        wizWriteLen('grid_y_offset', state.grid.y_offset);
        wizWriteLen('grid_z_offset', state.grid.z_offset);
        wizWriteLen('grid_spacing', state.grid.spacing);
      }
      if (state.lens) {
        var sel = document.getElementById('cam_sensor');
        if (sel && state.lens.sensor_id !== undefined) sel.value = state.lens.sensor_id;
        if (state.lens.sensor_custom !== undefined) {
          document.getElementById('cam_sensor_custom').value = state.lens.sensor_custom;
        }
        if (sel && sel.value === 'custom') {
          document.getElementById('cam_sensor_custom_field').style.display = '';
        }
        if (state.lens.focal !== undefined) {
          document.getElementById('cam_focal').value = state.lens.focal;
        }
      }
      if (state._originalFov) originalFov = state._originalFov;
      // Lens state only in the shape this page draws: a session saved by
      // another version must not stop the wizard from opening.
      if (Array.isArray(state._lensLines)) lensLines = state._lensLines.filter(lensRestoredLine);
      if (lensRestoredPair(state._lensImage)) lensImageSize = state._lensImage;
      if (state._lensFit && typeof state._lensFit.rating === 'string' && isFinite(state._lensFit.k1)
          && isFinite(state._lensFit.k2)) lensFit = state._lensFit;
      if (state._pinnedCorners && lensRestoredPair(state._pinnedCorners.size)
          && Array.isArray(state._pinnedCorners.corners) && state._pinnedCorners.corners.length === 4
          && state._pinnedCorners.corners.every(lensRestoredPair)) pinnedCorners = state._pinnedCorners;
      if (state._lensSolvedWith && isFinite(state._lensSolvedWith.k1) && isFinite(state._lensSolvedWith.k2)) {
        lensSolvedWith = state._lensSolvedWith;
      }
      // A step this page leaves out (the Lens step switched off) starts at Preparation, values kept.
      if (typeof state._stepKey === 'string' && WIZ[state._stepKey] !== undefined) currentStep = WIZ[state._stepKey];
      return true;
    } catch(e) { return false; }
  }

  // ---------------------------------------------------------------
  // Step Navigation
  // ---------------------------------------------------------------
  // The first step that needs the snapshot: the Lens step when it is shown.
  var WIZ_FIRST_SNAPSHOT = WIZ.lens !== undefined ? WIZ.lens : WIZ.ref;

  window.wizardGo = function(step) {
    saveToSession();
    if (!(step >= 0 && step < WIZ_STEPS.length)) step = 0;
    currentStep = step;
    var key = WIZ_STEPS[step];
    document.querySelectorAll('.wizard-content').forEach(function(el) {
      el.classList.toggle('active', el.id === 'wizard-step-' + key);
    });
    document.querySelectorAll('.wizard-step-btn').forEach(function(btn) {
      var s = parseInt(btn.dataset.step);
      btn.classList.toggle('active', s === step);
      btn.setAttribute('aria-current', s === step ? 'step' : 'false');
      if (s < step) btn.classList.add('completed');
    });
    if (step >= WIZ_FIRST_SNAPSHOT) {
      loadSnapshot();
    }
    if (key === 'prep') {
      updatePrepIllustration();
    }
    if (key === 'grid') {
      updateGridIllustration();
    }
    if (key === 'camera') {
      updateCamIllustration();
    }
    // Entering Reference Mapping saves the camera state for its Reset.
    if (key === 'ref') {
      preCoarsePose = snapshotPose();
    }
    if (key === 'review') {
      populateReview();
    }
    saveToSession();
  };
  window.wizardNext = function() { wizardGo(Math.min(currentStep + 1, WIZ_STEPS.length - 1)); };
  window.wizardPrev = function() { wizardGo(Math.max(currentStep - 1, 0)); };

  window.updateWizardState = function() {
    saveToSession();
  };

  // ---------------------------------------------------------------
  // Preparation illustration (isometric overview)
  // ---------------------------------------------------------------
  function updatePrepIllustration() {
    // Use default values from steps 2 and 4
    var gw = GRID_DEFAULTS.width;
    var gd = GRID_DEFAULTS.depth;
    var sp = GRID_DEFAULTS.spacing;
    var gox = GRID_DEFAULTS.x_offset;
    var goy = GRID_DEFAULTS.y_offset;
    var goz = GRID_DEFAULTS.z_offset;
    var cx = CAMERA_DEFAULTS.pos_x;
    var cy = CAMERA_DEFAULTS.pos_y;
    var cz = CAMERA_DEFAULTS.pos_z;

    // Isometric projection (same as camera step)
    var isoXx = 0.7, isoXy = 0.35;
    var isoYx = -0.7, isoYy = 0.35;
    var isoZy = -0.9;
    var svgW = 580, svgH = 400;

    function isoProject(wx, wy, wz) {
      return [wx * isoXx + wy * isoYx, wx * isoXy + wy * isoYy + (wz || 0) * isoZy];
    }

    // Grid corners using offsets (same as step 4)
    var hw = gw / 2, hd = gd / 2;
    var gridCorners = [
      [gox - hw, goy - hd],
      [gox + hw, goy - hd],
      [gox + hw, goy + hd],
      [gox - hw, goy + hd],
    ];

    // Stage area: 20% wider and deeper than grid
    var stageHW = gw * 1.2 / 2, stageHD = gd * 1.2 / 2;
    var stageCorners = [
      [gox - stageHW, goy - stageHD],
      [gox + stageHW, goy - stageHD],
      [gox + stageHW, goy + stageHD],
      [gox - stageHW, goy + stageHD],
    ];

    // Bounding box for auto-scale (stage + camera + ref)
    var screenPts = [];
    for (var i = 0; i < stageCorners.length; i++) {
      screenPts.push(isoProject(stageCorners[i][0], stageCorners[i][1], goz));
    }
    screenPts.push(isoProject(0, 0, 0));
    if (Math.abs(goz) > 0.01) screenPts.push(isoProject(0, 0, goz));
    screenPts.push(isoProject(cx, cy, 0));
    screenPts.push(isoProject(cx, cy, cz));

    var minSx = Infinity, maxSx = -Infinity, minSy = Infinity, maxSy = -Infinity;
    for (var j = 0; j < screenPts.length; j++) {
      if (screenPts[j][0] < minSx) minSx = screenPts[j][0];
      if (screenPts[j][0] > maxSx) maxSx = screenPts[j][0];
      if (screenPts[j][1] < minSy) minSy = screenPts[j][1];
      if (screenPts[j][1] > maxSy) maxSy = screenPts[j][1];
    }
    var screenBW = maxSx - minSx || 1;
    var screenBH = maxSy - minSy || 1;
    var margin = 50;
    var scale = Math.min((svgW - 2 * margin) / screenBW, (svgH - 2 * margin) / screenBH);
    var offsetX = svgW / 2 - (minSx + maxSx) / 2 * scale;
    var offsetY = svgH / 2 - (minSy + maxSy) / 2 * scale;

    function toSvg(wx, wy, wz) {
      var p = isoProject(wx, wy, wz);
      return [p[0] * scale + offsetX, p[1] * scale + offsetY];
    }

    // Stage quad
    var sc = [];
    for (var si = 0; si < 4; si++) sc.push(toSvg(stageCorners[si][0], stageCorners[si][1], goz));
    document.getElementById('pp-stage').setAttribute('points',
      sc[0][0]+','+sc[0][1]+' '+sc[1][0]+','+sc[1][1]+' '+sc[2][0]+','+sc[2][1]+' '+sc[3][0]+','+sc[3][1]);

    // Grid quad (at z=goz)
    var gc = [];
    for (var k = 0; k < 4; k++) gc.push(toSvg(gridCorners[k][0], gridCorners[k][1], goz));
    document.getElementById('pp-grid').setAttribute('points',
      gc[0][0]+','+gc[0][1]+' '+gc[1][0]+','+gc[1][1]+' '+gc[2][0]+','+gc[2][1]+' '+gc[3][0]+','+gc[3][1]);

    // Grid spacing lines
    var linesG = document.getElementById('pp-grid-lines');
    linesG.innerHTML = '';
    var lineColor = 'rgba(255,188,0,0.08)';
    if (sp > 0) {
      for (var xi = sp; xi < gw; xi += sp) {
        var wx = gox - hw + xi;
        var p1 = toSvg(wx, goy - hd, goz), p2 = toSvg(wx, goy + hd, goz);
        var vl = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        vl.setAttribute('x1', p1[0]); vl.setAttribute('y1', p1[1]);
        vl.setAttribute('x2', p2[0]); vl.setAttribute('y2', p2[1]);
        vl.setAttribute('stroke', lineColor); vl.setAttribute('stroke-width', '0.5');
        linesG.appendChild(vl);
      }
      for (var yi = sp; yi < gd; yi += sp) {
        var wy = goy - hd + yi;
        var q1 = toSvg(gox - hw, wy, goz), q2 = toSvg(gox + hw, wy, goz);
        var hl = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        hl.setAttribute('x1', q1[0]); hl.setAttribute('y1', q1[1]);
        hl.setAttribute('x2', q2[0]); hl.setAttribute('y2', q2[1]);
        hl.setAttribute('stroke', lineColor); hl.setAttribute('stroke-width', '0.5');
        linesG.appendChild(hl);
      }
    }

    // Corner labels
    var cornerIds = ['pp-label-dsl', 'pp-label-dsr', 'pp-label-usr', 'pp-label-usl'];
    var labelOffsets = [[-4, 14], [4, 14], [4, -6], [-4, -6]];
    for (var ci = 0; ci < 4; ci++) {
      var lbl = document.getElementById(cornerIds[ci]);
      lbl.setAttribute('x', gc[ci][0] + labelOffsets[ci][0]);
      lbl.setAttribute('y', gc[ci][1] + labelOffsets[ci][1]);
    }

    // Reference point at ground level (0,0,0)
    var refSvg = toSvg(0, 0, 0);
    document.getElementById('pp-ref').setAttribute('transform', 'translate('+refSvg[0]+','+refSvg[1]+')');
    document.getElementById('pp-ref-label').setAttribute('x', refSvg[0] - 30);
    document.getElementById('pp-ref-label').setAttribute('y', refSvg[1] + 4);

    // Grid Z offset line: from ref at ground (0,0,0) up to grid plane (0,0,goz)
    var gzLine = document.getElementById('pp-gz-line');
    var gzLabel = document.getElementById('pp-gz-label');
    var gzDot = document.getElementById('pp-gz-dot');
    if (Math.abs(goz) > 0.01) {
      var elevatedRef = toSvg(0, 0, goz);
      gzLine.setAttribute('x1', refSvg[0]); gzLine.setAttribute('y1', refSvg[1]);
      gzLine.setAttribute('x2', elevatedRef[0]); gzLine.setAttribute('y2', elevatedRef[1]);
      gzLine.style.display = '';
      gzLabel.setAttribute('x', elevatedRef[0] - 8);
      gzLabel.setAttribute('y', (refSvg[1] + elevatedRef[1]) / 2 + 3);
      gzLabel.setAttribute('text-anchor', 'end');
      gzLabel.textContent = 'Z: ' + WUNIT.formatLength(goz);
      gzLabel.style.display = '';
      gzDot.setAttribute('cx', elevatedRef[0]); gzDot.setAttribute('cy', elevatedRef[1]);
      gzDot.style.display = '';
    } else {
      gzLine.style.display = 'none';
      gzLabel.style.display = 'none';
      gzDot.style.display = 'none';
    }

    // Camera floor projection and top position
    var camFloor = toSvg(cx, cy, 0);
    var camPos = toSvg(cx, cy, cz);

    // Y axis line: ref (0,0,0) to camera floor (cx, cy, 0)
    document.getElementById('pp-y-line').setAttribute('x1', refSvg[0]);
    document.getElementById('pp-y-line').setAttribute('y1', refSvg[1]);
    document.getElementById('pp-y-line').setAttribute('x2', camFloor[0]);
    document.getElementById('pp-y-line').setAttribute('y2', camFloor[1]);
    // Z axis line: camera floor (cx, cy, 0) to camera (cx, cy, cz)
    document.getElementById('pp-z-line').setAttribute('x1', camFloor[0]);
    document.getElementById('pp-z-line').setAttribute('y1', camFloor[1]);
    document.getElementById('pp-z-line').setAttribute('x2', camPos[0]);
    document.getElementById('pp-z-line').setAttribute('y2', camPos[1]);

    // Camera body oriented toward grid center
    var gridCenterX = gox, gridCenterY = goy;
    var lookEndSvg = toSvg(cx + (gridCenterX - cx) * 0.3, cy + (gridCenterY - cy) * 0.3, cz + (goz - cz) * 0.3);
    var dx2 = lookEndSvg[0] - camPos[0];
    var dy2 = lookEndSvg[1] - camPos[1];
    var dist2 = Math.sqrt(dx2 * dx2 + dy2 * dy2) || 1;
    var ux = dx2 / dist2, uy = dy2 / dist2;
    var px = -uy, py = ux;
    var camSize = 10, bodyLen = camSize * 6;
    var lf1 = [camPos[0] + px * camSize, camPos[1] + py * camSize];
    var lf2 = [camPos[0] - px * camSize, camPos[1] - py * camSize];
    var bodyNarrow = 0.7;
    var nb1 = [lf1[0] - ux * bodyLen + px * camSize * (bodyNarrow - 1), lf1[1] - uy * bodyLen + py * camSize * (bodyNarrow - 1)];
    var nb2 = [lf2[0] - ux * bodyLen - px * camSize * (bodyNarrow - 1), lf2[1] - uy * bodyLen - py * camSize * (bodyNarrow - 1)];
    document.getElementById('pp-cam-body').setAttribute('points',
      lf1[0]+','+lf1[1]+' '+nb1[0]+','+nb1[1]+' '+nb2[0]+','+nb2[1]+' '+lf2[0]+','+lf2[1]);
    var lensInset = camSize * 0.7;
    var li1 = [camPos[0] + px * lensInset, camPos[1] + py * lensInset];
    var li2 = [camPos[0] - px * lensInset, camPos[1] - py * lensInset];
    var li3 = [li2[0] + ux * 2, li2[1] + uy * 2];
    var li4 = [li1[0] + ux * 2, li1[1] + uy * 2];
    document.getElementById('pp-cam-lens').setAttribute('points',
      li1[0]+','+li1[1]+' '+li2[0]+','+li2[1]+' '+li3[0]+','+li3[1]+' '+li4[0]+','+li4[1]);

    // Camera label
    var camCenterX = (lf1[0] + lf2[0] + nb1[0] + nb2[0]) / 4;
    var camCenterY = (lf1[1] + lf2[1] + nb1[1] + nb2[1]) / 4;
    var camLabelX = Math.max(30, Math.min(svgW - 30, camCenterX));
    var camLabelY = Math.max(14, Math.min(svgH - 6, camCenterY - 14));
    var camLabel = document.getElementById('pp-cam-label');
    camLabel.setAttribute('x', camLabelX);
    camLabel.setAttribute('y', camLabelY);
    camLabel.setAttribute('text-anchor', 'middle');

    // Dotted lines from each stage (outer box) corner to camera lens center
    var lensCenterX = (li1[0] + li2[0] + li3[0] + li4[0]) / 4;
    var lensCenterY = (li1[1] + li2[1] + li3[1] + li4[1]) / 4;
    for (var vi = 0; vi < 4; vi++) {
      var sl = document.getElementById('pp-sight-' + vi);
      sl.setAttribute('x1', sc[vi][0]); sl.setAttribute('y1', sc[vi][1]);
      sl.setAttribute('x2', lensCenterX); sl.setAttribute('y2', lensCenterY);
    }

    // Hide unused elements (FOV, measurement labels, floor dot – kept simple for prep overview)
    ['pp-sight-line', 'pp-fov-left', 'pp-fov-right', 'pp-fov-line'].forEach(function(id) {
      document.getElementById(id).style.display = 'none';
    });
    document.getElementById('pp-fov-label').style.display = 'none';
    document.getElementById('pp-x-line').style.display = 'none';
    document.getElementById('pp-x-label').style.display = 'none';
    document.getElementById('pp-y-label').style.display = 'none';
    document.getElementById('pp-z-label').style.display = 'none';
    document.getElementById('pp-floor-dot').style.display = 'none';

    // DS / US labels
    var dsPos = toSvg(gox, goy - hd - 1.5, goz);
    var usPos = toSvg(gox, goy + hd + 1.5, goz);
    document.getElementById('pp-ds-label').setAttribute('x', dsPos[0] + 40);
    document.getElementById('pp-ds-label').setAttribute('y', dsPos[1]);
    document.getElementById('pp-us-label').setAttribute('x', usPos[0]);
    document.getElementById('pp-us-label').setAttribute('y', usPos[1]);

    // PSN axis indicator (bottom-right corner)
    var stageBottomY = Math.max(sc[0][1], sc[1][1], sc[2][1], sc[3][1]);
    var axOriginX = svgW - 40, axOriginY = stageBottomY;
    var axLen = 45;
    var axX = [axOriginX - axLen * 0.7, axOriginY - axLen * 0.35];
    var axY = [axOriginX - axLen * 0.7, axOriginY + axLen * 0.35];
    var axZ = [axOriginX, axOriginY - axLen * 0.9];
    document.getElementById('pp-axis-x').setAttribute('x1', axOriginX);
    document.getElementById('pp-axis-x').setAttribute('y1', axOriginY);
    document.getElementById('pp-axis-x').setAttribute('x2', axX[0]);
    document.getElementById('pp-axis-x').setAttribute('y2', axX[1]);
    document.getElementById('pp-axis-x-label').setAttribute('x', axX[0] - 12);
    document.getElementById('pp-axis-x-label').setAttribute('y', axX[1] - 2);
    document.getElementById('pp-axis-y').setAttribute('x1', axOriginX);
    document.getElementById('pp-axis-y').setAttribute('y1', axOriginY);
    document.getElementById('pp-axis-y').setAttribute('x2', axY[0]);
    document.getElementById('pp-axis-y').setAttribute('y2', axY[1]);
    document.getElementById('pp-axis-y-label').setAttribute('x', axY[0] - 12);
    document.getElementById('pp-axis-y-label').setAttribute('y', axY[1] + 12);
    document.getElementById('pp-axis-z').setAttribute('x1', axOriginX);
    document.getElementById('pp-axis-z').setAttribute('y1', axOriginY);
    document.getElementById('pp-axis-z').setAttribute('x2', axZ[0]);
    document.getElementById('pp-axis-z').setAttribute('y2', axZ[1]);
    document.getElementById('pp-axis-z-label').setAttribute('x', axZ[0] - 12);
    document.getElementById('pp-axis-z-label').setAttribute('y', axZ[1] - 2);
  }

  // ---------------------------------------------------------------
  // Grid illustration (dynamic SVG)
  // ---------------------------------------------------------------
  window.onGridInputChanged = function() {
    saveToSession();
    updateGridIllustration();
    refreshLengthEchoes(GRID_LEN_IDS);
  };

  function setGridValues(vals) {
    // ``vals`` is in METRES (GRID_DEFAULTS or lastGridValues); render per unit.
    wizWriteLen('grid_width', vals.width);
    wizWriteLen('grid_depth', vals.depth);
    wizWriteLen('grid_spacing', vals.spacing);
    wizWriteLen('grid_x_offset', vals.x_offset);
    wizWriteLen('grid_y_offset', vals.y_offset);
    wizWriteLen('grid_z_offset', vals.z_offset);
    onGridInputChanged();
  }

  window.resetGridToDefaults = function() {
    setGridValues(GRID_DEFAULTS);
  };

  window.restoreGridToLast = function() {
    setGridValues(lastGridValues);
  };

  // ft/in text is rounded to 0.01 in, so a length read from it carries a residue
  // (7.5 m reads back as 7.500112 m); leaving imperial snaps the saved lengths
  // to the millimetre so the metric fields don't show it.
  function snapStoredLengthsToMm() {
    try {
      var state = JSON.parse(sessionStorage.getItem(STORAGE_KEY));
      var mm = function(v) { return Math.round(v * 1000) / 1000; };
      ['pos_x', 'pos_y', 'pos_z'].forEach(function(k) { state.camera[k] = mm(state.camera[k]); });
      Object.keys(state.grid).forEach(function(k) { state.grid[k] = mm(state.grid[k]); });
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch(e) {}
  }

  // Length fields are rendered per unit, so a switch reloads into this step;
  // the session holds the values in metres and restores them in the new unit.
  document.getElementById('wizard-unit-system').addEventListener('change', function() {
    var select = this;
    var saveError = window.OpenFollow.saveError;
    var box = document.querySelector('#wizard-step-grid .section');
    var near = document.getElementById('wizard-units');
    function keepCurrent(info) {
      select.value = WUNIT.isImperial() ? 'imperial' : 'metric';
      saveError.show(box, info, 'Not changed.', near);
    }
    saveToSession();
    if (WUNIT.isImperial()) snapStoredLengthsToMm();
    var body = new FormData();
    body.append('unit_system', select.value);
    fetch('/settings/unit-system', { method: 'POST', body: body })
      .then(async function(r) {
        if (!r.ok) return keepCurrent(await saveError.fromResponse(r));
        window.location.reload();
      })
      .catch(function() { keepCurrent(saveError.UNREACHABLE); });
  });

  function updateGridIllustration() {
    var w = wizReadLen('grid_width') || 1;
    var d = wizReadLen('grid_depth') || 1;
    var sp = wizReadLen('grid_spacing') || 1;
    var ox = wizReadLen('grid_x_offset') || 0;
    var oy = wizReadLen('grid_y_offset') || 0;
    var oz = wizReadLen('grid_z_offset') || 0;

    // SVG coordinate system: viewBox is 580×400
    var svgW = 580, svgH = 400;
    var margin = 40;
    var dimSpace = 30; // space for dimension lines + labels

    // Available drawing area (excluding margins and dimension space)
    var drawW = svgW - 2 * margin - dimSpace;
    var drawH = svgH - 2 * margin - dimSpace;

    // Compute bounding box of everything that needs to be visible:
    // the grid rectangle + reference point + offset arrows
    // Reference is at world (0,0), grid center is at (ox, oy)
    // Grid spans from (ox-w/2, oy-d/2) to (ox+w/2, oy+d/2)
    var hw = w / 2, hd = d / 2;
    var worldLeft = Math.min(0, ox - hw) - 0.2;
    var worldRight = Math.max(0, ox + hw) + 0.2;
    var worldBottom = Math.min(0, oy - hd) - 0.2; // downstage (min Y)
    var worldTop = Math.max(0, oy + hd) + 0.2;    // upstage (max Y)
    var worldW = worldRight - worldLeft;
    var worldH = worldTop - worldBottom;

    // Scale to fit bounding box in draw area
    var scale = Math.min(drawW / worldW, drawH / worldH);

    // Center the bounding box in the drawing area
    var centerSvgX = margin + drawW / 2;
    var centerSvgY = margin + drawH / 2;
    var worldCenterX = (worldLeft + worldRight) / 2;
    var worldCenterY = (worldBottom + worldTop) / 2;

    // World→SVG transform: X→right, Y→up (SVG Y is inverted)
    function toSvgX(wx) { return centerSvgX + (wx - worldCenterX) * scale; }
    function toSvgY(wy) { return centerSvgY - (wy - worldCenterY) * scale; }

    // Reference point in SVG coords
    var refSvgX = toSvgX(0);
    var refSvgY = toSvgY(0);

    // Grid rectangle in SVG coords
    var gx = toSvgX(ox - hw);
    var gy = toSvgY(oy + hd); // top edge = max Y → min SVG Y
    var gw = w * scale;
    var gh = d * scale;
    var gridCx = toSvgX(ox);
    var gridCy = toSvgY(oy);
    var dsY = toSvgY(oy - hd); // downstage edge (bottom in SVG)

    // Update grid rectangle
    var rect = document.getElementById('gs-grid');
    rect.setAttribute('x', gx); rect.setAttribute('y', gy);
    rect.setAttribute('width', gw); rect.setAttribute('height', gh);

    // Grid lines
    var linesG = document.getElementById('gs-grid-lines');
    linesG.innerHTML = '';
    if (sp > 0 && w > 0 && d > 0) {
      var lineColor = 'rgba(255,188,0,0.1)';
      // Vertical lines
      for (var xi = sp; xi < w; xi += sp) {
        var lx = gx + xi * scale;
        var vl = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        vl.setAttribute('x1', lx); vl.setAttribute('y1', gy);
        vl.setAttribute('x2', lx); vl.setAttribute('y2', gy + gh);
        vl.setAttribute('stroke', lineColor); vl.setAttribute('stroke-width', '0.5');
        linesG.appendChild(vl);
      }
      // Horizontal lines
      for (var yi = sp; yi < d; yi += sp) {
        var ly = gy + yi * scale;
        var hl = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        hl.setAttribute('x1', gx); hl.setAttribute('y1', ly);
        hl.setAttribute('x2', gx + gw); hl.setAttribute('y2', ly);
        hl.setAttribute('stroke', lineColor); hl.setAttribute('stroke-width', '0.5');
        linesG.appendChild(hl);
      }
    }

    // Corner labels
    var pad = 8;
    var setLabel = function(id, x, y) {
      var el = document.getElementById(id);
      el.setAttribute('x', x); el.setAttribute('y', y);
    };
    // +X is stage left, drawn on the right, so DSL/USL sit at the right edge
    // and DSR/USR at the left edge (audience view: stage left = audience right).
    setLabel('gs-label-dsr', gx - pad, dsY + 14);
    setLabel('gs-label-dsl', gx + gw + pad - 20, dsY + 14);
    setLabel('gs-label-usr', gx - pad, gy - 4);
    setLabel('gs-label-usl', gx + gw + pad - 20, gy - 4);

    // Width dimension (below grid)
    var dimY = dsY + 22;
    var dimWLine = document.getElementById('gs-dim-w');
    dimWLine.setAttribute('x1', gx); dimWLine.setAttribute('y1', dimY);
    dimWLine.setAttribute('x2', gx + gw); dimWLine.setAttribute('y2', dimY);
    var dimWL = document.getElementById('gs-dim-w-l');
    dimWL.setAttribute('x1', gx); dimWL.setAttribute('y1', dimY - 4);
    dimWL.setAttribute('x2', gx); dimWL.setAttribute('y2', dimY + 4);
    var dimWR = document.getElementById('gs-dim-w-r');
    dimWR.setAttribute('x1', gx + gw); dimWR.setAttribute('y1', dimY - 4);
    dimWR.setAttribute('x2', gx + gw); dimWR.setAttribute('y2', dimY + 4);
    var dimWText = document.getElementById('gs-dim-w-text');
    dimWText.setAttribute('x', gridCx); dimWText.setAttribute('y', dimY + 14);
    dimWText.textContent = WUNIT.formatLength(w);

    // Depth dimension (right of grid)
    var dimX = gx + gw + 18;
    var dimDLine = document.getElementById('gs-dim-d');
    dimDLine.setAttribute('x1', dimX); dimDLine.setAttribute('y1', gy);
    dimDLine.setAttribute('x2', dimX); dimDLine.setAttribute('y2', gy + gh);
    var dimDT = document.getElementById('gs-dim-d-t');
    dimDT.setAttribute('x1', dimX - 4); dimDT.setAttribute('y1', gy);
    dimDT.setAttribute('x2', dimX + 4); dimDT.setAttribute('y2', gy);
    var dimDB = document.getElementById('gs-dim-d-b');
    dimDB.setAttribute('x1', dimX - 4); dimDB.setAttribute('y1', gy + gh);
    dimDB.setAttribute('x2', dimX + 4); dimDB.setAttribute('y2', gy + gh);
    var dimDText = document.getElementById('gs-dim-d-text');
    dimDText.setAttribute('x', dimX + 8); dimDText.setAttribute('y', gridCy + 4);
    dimDText.textContent = WUNIT.formatLength(d);

    // Reference point
    document.getElementById('gs-ref').setAttribute('transform', 'translate(' + refSvgX + ',' + refSvgY + ')');
    document.getElementById('gs-ref-label').setAttribute('x', refSvgX + 10);
    document.getElementById('gs-ref-label').setAttribute('y', refSvgY - 2);

    // Offset arrows: from ref point toward grid center (only when offset != 0)
    var offThreshold = 0.01;
    var offXLine = document.getElementById('gs-off-x');
    var offXLabel = document.getElementById('gs-off-x-label');
    if (Math.abs(ox) > offThreshold) {
      offXLine.setAttribute('x1', refSvgX); offXLine.setAttribute('y1', refSvgY);
      offXLine.setAttribute('x2', gridCx); offXLine.setAttribute('y2', refSvgY);
      offXLine.style.display = '';
      offXLabel.setAttribute('x', (refSvgX + gridCx) / 2);
      offXLabel.setAttribute('y', refSvgY + 14);
      offXLabel.textContent = 'X: ' + WUNIT.formatLength(ox);
      offXLabel.style.display = '';
    } else {
      offXLine.style.display = 'none';
      offXLabel.style.display = 'none';
    }

    var offYLine = document.getElementById('gs-off-y');
    var offYLabel = document.getElementById('gs-off-y-label');
    if (Math.abs(oy) > offThreshold) {
      offYLine.setAttribute('x1', refSvgX); offYLine.setAttribute('y1', refSvgY);
      offYLine.setAttribute('x2', refSvgX); offYLine.setAttribute('y2', gridCy);
      offYLine.style.display = '';
      offYLabel.setAttribute('x', refSvgX + 8);
      offYLabel.setAttribute('y', (refSvgY + gridCy) / 2 + 4);
      offYLabel.textContent = 'Y: ' + WUNIT.formatLength(oy);
      offYLabel.style.display = '';
    } else {
      offYLine.style.display = 'none';
      offYLabel.style.display = 'none';
    }

    // Z offset label (2D view: show as text near ref)
    var offZLabel = document.getElementById('gs-off-z-label');
    if (Math.abs(oz) > offThreshold) {
      offZLabel.setAttribute('x', refSvgX + 10);
      offZLabel.setAttribute('y', refSvgY + 14);
      offZLabel.textContent = 'Z: ' + WUNIT.formatLength(oz);
      offZLabel.style.display = '';
    } else {
      offZLabel.style.display = 'none';
    }

    // Diagonal (DSR→USL)
    var diag = Math.sqrt(w * w + d * d);
    var diagLine = document.getElementById('gs-diag');
    diagLine.setAttribute('x1', gx); diagLine.setAttribute('y1', dsY);
    diagLine.setAttribute('x2', gx + gw); diagLine.setAttribute('y2', gy);
    var diagText = document.getElementById('gs-diag-text');
    diagText.setAttribute('x', (gx + gx + gw) / 2 + 12);
    diagText.setAttribute('y', (dsY + gy) / 2);
    diagText.textContent = WUNIT.formatLength(diag);

    // Update diagonal readout below the form
    document.getElementById('grid-diagonal').textContent = WUNIT.formatLength(diag);

    // Downstage / Upstage labels
    document.getElementById('gs-ds-label').setAttribute('x', refSvgX);
    document.getElementById('gs-ds-label').setAttribute('y', svgH - 5);
    document.getElementById('gs-us-label').setAttribute('x', refSvgX);
    document.getElementById('gs-us-label').setAttribute('y', 15);
  }

  // ---------------------------------------------------------------
  // Lens Helper (Sensor Size + Focal Length → Horizontal FOV)
  // ---------------------------------------------------------------
  // Horizontal sensor-width (mm) for each preset. Matches datasheet convention.
  var SENSOR_SIZES = [
    { id: '1/4',    label: '1/4" (3.6 mm)',              width_mm: 3.6  },
    { id: '1/3',    label: '1/3" (4.8 mm)',              width_mm: 4.8  },
    { id: '1/2.8',  label: '1/2.8" (5.37 mm)',           width_mm: 5.37 },
    { id: '1/2.5',  label: '1/2.5" (5.76 mm)',           width_mm: 5.76 },
    { id: '1/2',    label: '1/2" (6.4 mm)',              width_mm: 6.4  },
    { id: '1/1.8',  label: '1/1.8" (7.18 mm)',           width_mm: 7.18 },
    { id: '1/1.7',  label: '1/1.7" (7.6 mm)',            width_mm: 7.6  },
    { id: '2/3',    label: '2/3" (8.8 mm)',              width_mm: 8.8  },
    { id: '1',      label: '1" (13.2 mm)',               width_mm: 13.2 },
    { id: 'mft',    label: 'Micro Four Thirds (17.3 mm)', width_mm: 17.3 },
    { id: 'apsc',   label: 'APS-C (23.6 mm)',            width_mm: 23.6 },
    { id: 'super35',label: 'Super 35 (24.89 mm)',        width_mm: 24.89 },
    { id: 'ff',     label: 'Full Frame 35 mm (36 mm)',   width_mm: 36   },
    { id: 'custom', label: 'Custom…',                    width_mm: null },
    { id: '',       label: '– choose –',                 width_mm: null },
  ];

  function populateSensorDropdown() {
    var sel = document.getElementById('cam_sensor');
    if (!sel || sel.options.length > 0) return;
    SENSOR_SIZES.forEach(function(s) {
      var opt = document.createElement('option');
      opt.value = s.id;
      opt.textContent = s.label;
      sel.appendChild(opt);
    });
    sel.value = '';  // default: no selection
  }

  function getSensorWidthMm() {
    var sel = document.getElementById('cam_sensor');
    if (!sel) return null;
    if (sel.value === 'custom') {
      var custom = parseFloat(document.getElementById('cam_sensor_custom').value);
      return isFinite(custom) && custom > 0 ? custom : null;
    }
    var entry = SENSOR_SIZES.find(function(s) { return s.id === sel.value; });
    return entry && entry.width_mm ? entry.width_mm : null;
  }

  function hfovFromSensor(sensorWidthMm, focalLengthMm) {
    if (!sensorWidthMm || !focalLengthMm) return null;
    var rad = 2 * Math.atan(sensorWidthMm / (2 * focalLengthMm));
    return rad * 180 / Math.PI;
  }

  function setLensHelperStale(stale) {
    var ids = ['cam_sensor_field', 'cam_sensor_custom_field'];
    ids.push('cam_focal');
    ids.forEach(function(id) {
      var el = document.getElementById(id);
      if (el) {
        if (stale) el.classList.add('lens-helper-stale');
        else el.classList.remove('lens-helper-stale');
      }
    });
  }

  window.onSensorOrFocalChanged = function() {
    var sel = document.getElementById('cam_sensor');
    var customField = document.getElementById('cam_sensor_custom_field');
    if (sel && customField) {
      customField.style.display = sel.value === 'custom' ? '' : 'none';
    }
    var sw = getSensorWidthMm();
    var fl = parseFloat(document.getElementById('cam_focal').value);
    var hfov = hfovFromSensor(sw, fl);
    if (hfov !== null && isFinite(hfov)) {
      document.getElementById('cam_fov').value = hfov.toFixed(2);
      setLensHelperStale(false);
      onCamInputChanged();
    }
  };

  window.onHfovEdited = function() {
    // User typed HFOV directly – dim the helper fields to flag they no longer
    // reflect the current FOV, but keep their values so the user can re-couple
    // by editing them again.
    setLensHelperStale(true);
    onCamInputChanged();
  };

  // ---------------------------------------------------------------
  // Camera Position illustration (isometric dynamic SVG)
  // ---------------------------------------------------------------
  window.onCamInputChanged = function() {
    var pitch = parseFloat(document.getElementById('cam_pitch').value);
    var errEl = document.getElementById('cam-pitch-error');
    if (isNaN(pitch) || pitch > -0.5 || pitch < -90) {
      errEl.style.display = 'block';
    } else {
      errEl.style.display = 'none';
    }
    saveToSession();
    updateCamIllustration();
    refreshLengthEchoes(CAM_LEN_IDS);
  };

  function setCameraValues(vals) {
    // pos_* are METRES (CAMERA_DEFAULTS or lastCameraValues); angles verbatim.
    wizWriteLen('cam_pos_x', vals.pos_x);
    wizWriteLen('cam_pos_y', vals.pos_y);
    wizWriteLen('cam_pos_z', vals.pos_z);
    document.getElementById('cam_pitch').value = vals.pitch;
    document.getElementById('cam_yaw').value = vals.yaw;
    document.getElementById('cam_roll').value = vals.roll;
    document.getElementById('cam_fov').value = vals.fov;
    setLensHelperStale(true);  // preset FOV no longer matches helper fields
    onCamInputChanged();
  }

  window.resetCameraToDefaults = function() {
    setCameraValues(CAMERA_DEFAULTS);
  };

  window.restoreCameraToLast = function() {
    setCameraValues(lastCameraValues);
  };

  function updateCamIllustration() {
    // Read grid values
    var gw = wizReadLen('grid_width') || 6;
    var gd = wizReadLen('grid_depth') || 4;
    var gox = wizReadLen('grid_x_offset') || 0;
    var goy = wizReadLen('grid_y_offset') || 0;
    var goz = wizReadLen('grid_z_offset') || 0;

    // Read camera values
    var cx = wizReadLen('cam_pos_x') || 0;
    var cy = wizReadLen('cam_pos_y') || 0;
    var cz = wizReadLen('cam_pos_z') || 0;
    var pitch = parseFloat(document.getElementById('cam_pitch').value) || 0;
    var yaw = parseFloat(document.getElementById('cam_yaw').value) || 0;
    var fov = parseFloat(document.getElementById('cam_fov').value) || 60;

    // Isometric projection: world (X=right, Y=upstage, Z=up)
    // Upstage = top-left on screen
    var isoXx = 0.7, isoXy = 0.35;
    var isoYx = -0.7, isoYy = 0.35;
    var isoZy = -0.9;

    var svgW = 580, svgH = 400;
    var hw = gw / 2, hd = gd / 2;
    // Stage convention: +X is stage left, so DSL/USL are at +hw and DSR/USR
    // at -hw (order: DSL, DSR, USR, USL), matching the other wizard steps.
    var gridCorners = [
      [gox + hw, goy - hd],
      [gox - hw, goy - hd],
      [gox - hw, goy + hd],
      [gox + hw, goy + hd],
    ];

    function isoProject(wx, wy, wz) {
      return [
        wx * isoXx + wy * isoYx,
        wx * isoXy + wy * isoYy + (wz || 0) * isoZy
      ];
    }

    // Compute camera viewing direction from pitch/yaw (in degrees)
    // Pitch: 0 = horizontal, -45 = 45° down, -90 = straight down
    // Yaw: 0 = looking along +Y (upstage), negative = left
    // This camera model must stay in step with scene/solver.py, or the preview
    // stops describing the overlay. Pinned by TestWizardIllustrationAgreement
    // in tests/test_solver.py - edit both together.
    var rad = Math.PI / 180;
    var pitchR = pitch * rad;
    var yawR = yaw * rad;
    // 3D look direction (absolute angles)
    var lookX = Math.cos(pitchR) * Math.sin(yawR);
    var lookY = Math.cos(pitchR) * Math.cos(yawR);
    var lookZ = Math.sin(pitchR);

    // Sight line: where the look direction hits the grid plane (z=goz).
    var floorDist = ((cz - goz) > 0 && lookZ < 0) ? -(cz - goz) / lookZ : 10;
    var fovTargetX = cx + lookX * floorDist;
    var fovTargetY = cy + lookY * floorDist;

    // Field-of-view footprint on the grid plane. Assume a 16:9 frame, so the
    // vertical FOV follows from the horizontal one. Build a camera frame from
    // the look direction L, a horizontal right vector R, and an in-image up U,
    // then intersect the four corner rays with the grid plane.
    var vfov = (2 * Math.atan(Math.tan((fov / 2) * rad) * 9 / 16)) / rad;
    var th = Math.tan((fov / 2) * rad);
    var tv = Math.tan((vfov / 2) * rad);
    var Rv = [Math.cos(yawR), -Math.sin(yawR), 0];
    var Uv = [-Math.sin(yawR) * Math.sin(pitchR), -Math.cos(yawR) * Math.sin(pitchR), Math.cos(pitchR)];
    var capGround = Math.max(gw, gd) * 2 + Math.abs(cx) + Math.abs(cy) + 4;
    function cornerDir(sx, sy) {
      var dx = lookX + sx * th * Rv[0] + sy * tv * Uv[0];
      var dy = lookY + sx * th * Rv[1] + sy * tv * Uv[1];
      var dz = lookZ + sx * th * Rv[2] + sy * tv * Uv[2];
      var len = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;
      return [dx / len, dy / len, dz / len];
    }
    function floorHit(D) {
      if (D[2] < -1e-3) {
        var t = (goz - cz) / D[2];
        if (t > 0) {
          var hx = cx + D[0] * t, hy = cy + D[1] * t;
          if (Math.sqrt((hx - cx) * (hx - cx) + (hy - cy) * (hy - cy)) <= capGround) return [hx, hy];
        }
      }
      // Ray points at/above the horizon or past the cap: clamp onto the plane
      // at the max ground reach so the footprint stays bounded.
      var hlen = Math.sqrt(D[0] * D[0] + D[1] * D[1]) || 1;
      return [cx + (D[0] / hlen) * capGround, cy + (D[1] / hlen) * capGround];
    }
    var fovBL = floorHit(cornerDir(-1, -1));
    var fovBR = floorHit(cornerDir(1, -1));
    var fovTR = floorHit(cornerDir(1, 1));
    var fovTL = floorHit(cornerDir(-1, 1));

    // Collect all world points for auto-scale bounding
    var screenPts = [];
    // Grid corners at z=goz
    for (var gi = 0; gi < gridCorners.length; gi++) {
      screenPts.push(isoProject(gridCorners[gi][0], gridCorners[gi][1], goz));
    }
    // Ref at z=goz, ground ref at z=0 (if goz != 0), camera floor, FOV
    screenPts.push(isoProject(0, 0, goz));
    if (Math.abs(goz) > 0.01) screenPts.push(isoProject(0, 0, 0));
    screenPts.push(isoProject(cx, cy, 0));
    // Anchor the auto-scale to the grid, camera, centre sightline and the near
    // coverage edge. The far coverage edge can run far upstage at shallow
    // angles, so leave it out of the bounds and let it clip at the frame.
    screenPts.push(isoProject(fovTargetX, fovTargetY, goz));
    screenPts.push(isoProject(fovBL[0], fovBL[1], goz));
    screenPts.push(isoProject(fovBR[0], fovBR[1], goz));
    screenPts.push(isoProject(cx, cy, cz));

    var minSx = Infinity, maxSx = -Infinity, minSy = Infinity, maxSy = -Infinity;
    for (var j = 0; j < screenPts.length; j++) {
      if (screenPts[j][0] < minSx) minSx = screenPts[j][0];
      if (screenPts[j][0] > maxSx) maxSx = screenPts[j][0];
      if (screenPts[j][1] < minSy) minSy = screenPts[j][1];
      if (screenPts[j][1] > maxSy) maxSy = screenPts[j][1];
    }

    var screenBW = maxSx - minSx || 1;
    var screenBH = maxSy - minSy || 1;
    var margin = 50;
    var scale = Math.min((svgW - 2 * margin) / screenBW, (svgH - 2 * margin) / screenBH);
    var offsetX = svgW / 2 - (minSx + maxSx) / 2 * scale;
    var offsetY = svgH / 2 - (minSy + maxSy) / 2 * scale;

    function toSvg(wx, wy, wz) {
      var p = isoProject(wx, wy, wz);
      return [p[0] * scale + offsetX, p[1] * scale + offsetY];
    }

    // Grid quad (at z=goz)
    var gc = [];
    for (var k = 0; k < 4; k++) {
      gc.push(toSvg(gridCorners[k][0], gridCorners[k][1], goz));
    }
    document.getElementById('cp-grid').setAttribute('points',
      gc[0][0]+','+gc[0][1]+' '+gc[1][0]+','+gc[1][1]+' '+gc[2][0]+','+gc[2][1]+' '+gc[3][0]+','+gc[3][1]);

    // Grid spacing lines
    var sp = wizReadLen('grid_spacing') || 1;
    var linesG = document.getElementById('cp-grid-lines');
    linesG.innerHTML = '';
    var lineColor = 'rgba(255,188,0,0.08)';
    if (sp > 0) {
      for (var xi = sp; xi < gw; xi += sp) {
        var wx = gox - hw + xi;
        var p1 = toSvg(wx, goy - hd, goz);
        var p2 = toSvg(wx, goy + hd, goz);
        var vl = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        vl.setAttribute('x1', p1[0]); vl.setAttribute('y1', p1[1]);
        vl.setAttribute('x2', p2[0]); vl.setAttribute('y2', p2[1]);
        vl.setAttribute('stroke', lineColor); vl.setAttribute('stroke-width', '0.5');
        linesG.appendChild(vl);
      }
      for (var yi = sp; yi < gd; yi += sp) {
        var wy2 = goy - hd + yi;
        var q1 = toSvg(gox - hw, wy2, goz);
        var q2 = toSvg(gox + hw, wy2, goz);
        var hl = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        hl.setAttribute('x1', q1[0]); hl.setAttribute('y1', q1[1]);
        hl.setAttribute('x2', q2[0]); hl.setAttribute('y2', q2[1]);
        hl.setAttribute('stroke', lineColor); hl.setAttribute('stroke-width', '0.5');
        linesG.appendChild(hl);
      }
    }

    // Corner labels – hidden in camera step (shown in other steps)
    ['cp-label-dsl', 'cp-label-dsr', 'cp-label-usr', 'cp-label-usl'].forEach(function(id) {
      document.getElementById(id).style.display = 'none';
    });

    // Reference point at ground level (where the physical mark is)
    var refSvg = toSvg(0, 0, 0);
    document.getElementById('cp-ref').setAttribute('transform', 'translate('+refSvg[0]+','+refSvg[1]+')');
    var refLabel = document.getElementById('cp-ref-label');
    refLabel.setAttribute('x', refSvg[0] + 10);
    refLabel.setAttribute('y', refSvg[1] + 4);
    refLabel.setAttribute('text-anchor', 'start');

    // Grid Z offset line: from ref at ground (0,0,0) up to grid plane (0,0,goz)
    var gzLine = document.getElementById('cp-gz-line');
    var gzLabel = document.getElementById('cp-gz-label');
    var gzDot = document.getElementById('cp-gz-dot');
    if (Math.abs(goz) > 0.01) {
      var elevatedRef = toSvg(0, 0, goz);
      gzLine.setAttribute('x1', refSvg[0]); gzLine.setAttribute('y1', refSvg[1]);
      gzLine.setAttribute('x2', elevatedRef[0]); gzLine.setAttribute('y2', elevatedRef[1]);
      gzLine.style.display = '';
      // Place z-offset label above the elevated dot
      gzLabel.setAttribute('x', elevatedRef[0]);
      gzLabel.setAttribute('y', elevatedRef[1] - 8);
      gzLabel.setAttribute('text-anchor', 'middle');
      gzLabel.textContent = 'Z: ' + WUNIT.formatLength(goz);
      gzLabel.style.display = '';
      gzDot.setAttribute('cx', elevatedRef[0]); gzDot.setAttribute('cy', elevatedRef[1]);
      gzDot.style.display = '';
    } else {
      gzLine.style.display = 'none';
      gzLabel.style.display = 'none';
      gzDot.style.display = 'none';
    }

    // Camera floor projection and top position
    var camFloor = toSvg(cx, cy, 0);
    var camTop = toSvg(cx, cy, cz);

    // X measurement line: from ref (0,0,0) along X to (cx,0,0)
    var xLine = document.getElementById('cp-x-line');
    var xLabel = document.getElementById('cp-x-label');
    if (Math.abs(cx) > 0.01) {
      var xEnd = toSvg(cx, 0, 0);
      xLine.setAttribute('x1', refSvg[0]); xLine.setAttribute('y1', refSvg[1]);
      xLine.setAttribute('x2', xEnd[0]); xLine.setAttribute('y2', xEnd[1]);
      xLine.style.display = '';
      xLabel.setAttribute('x', (refSvg[0] + xEnd[0]) / 2);
      xLabel.setAttribute('y', (refSvg[1] + xEnd[1]) / 2 + 14);
      xLabel.setAttribute('text-anchor', 'middle');
      xLabel.textContent = 'X: ' + WUNIT.formatLength(cx);
      xLabel.style.display = '';
    } else {
      xLine.style.display = 'none'; xLabel.style.display = 'none';
    }

    // Y measurement line: from (cx,0) to (cx,cy)
    var yLine = document.getElementById('cp-y-line');
    var yLabel = document.getElementById('cp-y-label');
    if (Math.abs(cy) > 0.01) {
      var yStart = toSvg(cx, 0, 0);
      yLine.setAttribute('x1', yStart[0]); yLine.setAttribute('y1', yStart[1]);
      yLine.setAttribute('x2', camFloor[0]); yLine.setAttribute('y2', camFloor[1]);
      yLine.style.display = '';
      // Place Y label to the right of the line
      yLabel.setAttribute('x', (yStart[0] + camFloor[0]) / 2 + 10);
      yLabel.setAttribute('y', (yStart[1] + camFloor[1]) / 2);
      yLabel.textContent = 'Y: ' + WUNIT.formatLength(cy);
      yLabel.style.display = '';
    } else {
      yLine.style.display = 'none'; yLabel.style.display = 'none';
    }

    // Z pole (only show when Z > 0)
    var zLine = document.getElementById('cp-z-line');
    var zLabel = document.getElementById('cp-z-label');
    var floorDot = document.getElementById('cp-floor-dot');
    if (Math.abs(cz) > 0.01) {
      zLine.setAttribute('x1', camFloor[0]); zLine.setAttribute('y1', camFloor[1]);
      zLine.setAttribute('x2', camTop[0]); zLine.setAttribute('y2', camTop[1]);
      zLine.style.display = '';
      // Place Z label to the right of the pole
      zLabel.setAttribute('x', camFloor[0] + 10);
      zLabel.setAttribute('y', (camFloor[1] + camTop[1]) / 2 + 3);
      zLabel.textContent = 'Z: ' + WUNIT.formatLength(cz);
      zLabel.style.display = '';
      floorDot.setAttribute('cx', camFloor[0]); floorDot.setAttribute('cy', camFloor[1]);
      floorDot.style.display = '';
    } else {
      zLine.style.display = 'none'; zLabel.style.display = 'none';
      floorDot.style.display = 'none';
    }

    // Sight line from camera to FOV target center (where look direction hits grid plane)
    var fovTargetSvg = toSvg(fovTargetX, fovTargetY, goz);
    var sightLine = document.getElementById('cp-sight-line');
    sightLine.setAttribute('x1', camTop[0]); sightLine.setAttribute('y1', camTop[1]);
    sightLine.setAttribute('x2', fovTargetSvg[0]); sightLine.setAttribute('y2', fovTargetSvg[1]);

    // Camera body: oriented along the 3D look direction projected to screen
    // Use the look direction to orient the camera symbol
    var lookEndSvg = toSvg(cx + lookX * 2, cy + lookY * 2, cz + lookZ * 2);
    var dx2 = lookEndSvg[0] - camTop[0];
    var dy2 = lookEndSvg[1] - camTop[1];
    var dist2 = Math.sqrt(dx2 * dx2 + dy2 * dy2) || 1;
    var ux = dx2 / dist2;
    var uy = dy2 / dist2;
    var px = -uy;
    var py = ux;

    var camSize = 10;
    var bodyLen = camSize * 6;

    // Lens front face
    var lf1 = [camTop[0] + px * camSize, camTop[1] + py * camSize];
    var lf2 = [camTop[0] - px * camSize, camTop[1] - py * camSize];
    // Body back (narrower)
    var bodyNarrow = 0.7;
    var nb1 = [lf1[0] - ux * bodyLen + px * camSize * (bodyNarrow - 1), lf1[1] - uy * bodyLen + py * camSize * (bodyNarrow - 1)];
    var nb2 = [lf2[0] - ux * bodyLen - px * camSize * (bodyNarrow - 1), lf2[1] - uy * bodyLen - py * camSize * (bodyNarrow - 1)];
    document.getElementById('cp-cam-body').setAttribute('points',
      lf1[0]+','+lf1[1]+' '+nb1[0]+','+nb1[1]+' '+nb2[0]+','+nb2[1]+' '+lf2[0]+','+lf2[1]);

    // Lens face
    var lensInset = camSize * 0.7;
    var li1 = [camTop[0] + px * lensInset, camTop[1] + py * lensInset];
    var li2 = [camTop[0] - px * lensInset, camTop[1] - py * lensInset];
    var li3 = [li2[0] + ux * 2, li2[1] + uy * 2];
    var li4 = [li1[0] + ux * 2, li1[1] + uy * 2];
    document.getElementById('cp-cam-lens').setAttribute('points',
      li1[0]+','+li1[1]+' '+li2[0]+','+li2[1]+' '+li3[0]+','+li3[1]+' '+li4[0]+','+li4[1]);

    // Camera label – position above the camera body center, clamped inside SVG
    var camLabel = document.getElementById('cp-cam-label');
    var camCenterX = (lf1[0] + lf2[0] + nb1[0] + nb2[0]) / 4;
    var camCenterY = (lf1[1] + lf2[1] + nb1[1] + nb2[1]) / 4;
    var camLabelX = Math.max(30, Math.min(svgW - 30, camCenterX));
    var camLabelY = Math.max(14, Math.min(svgH - 6, camCenterY - 14));
    camLabel.setAttribute('x', camLabelX);
    camLabel.setAttribute('y', camLabelY);
    camLabel.setAttribute('text-anchor', 'middle');

    // FOV ground footprint: shaded quad + the four frustum edges from the lens
    var aBL = toSvg(fovBL[0], fovBL[1], goz);
    var aBR = toSvg(fovBR[0], fovBR[1], goz);
    var aTR = toSvg(fovTR[0], fovTR[1], goz);
    var aTL = toSvg(fovTL[0], fovTL[1], goz);
    document.getElementById('cp-fov-area').setAttribute('points',
      aBL[0]+','+aBL[1]+' '+aBR[0]+','+aBR[1]+' '+aTR[0]+','+aTR[1]+' '+aTL[0]+','+aTL[1]);
    function setFovEdge(id, p) {
      var el = document.getElementById(id);
      el.setAttribute('x1', camTop[0]); el.setAttribute('y1', camTop[1]);
      el.setAttribute('x2', p[0]); el.setAttribute('y2', p[1]);
    }
    setFovEdge('cp-fov-bl', aBL);
    setFovEdge('cp-fov-br', aBR);
    setFovEdge('cp-fov-tl', aTL);
    setFovEdge('cp-fov-tr', aTR);
    // FOV label – left of the lens
    var fovLabel = document.getElementById('cp-fov-label');
    fovLabel.setAttribute('x', camTop[0] - camSize - 6);
    fovLabel.setAttribute('y', camTop[1] + 4);
    fovLabel.setAttribute('text-anchor', 'end');
    fovLabel.textContent = 'FOV ' + fov.toFixed(0) + '\u00b0H \u00b7 ' + vfov.toFixed(0) + '\u00b0V';

    // Downstage label near the downstage stage-right (DSR) corner; upstage
    // label centered on the upstage edge.
    var dsLabelPos = toSvg(gox - hw, goy - hd, goz + 1.0);
    document.getElementById('cp-ds-label').setAttribute('x', dsLabelPos[0] + 6);
    document.getElementById('cp-ds-label').setAttribute('y', dsLabelPos[1]);
    document.getElementById('cp-ds-label').setAttribute('text-anchor', 'start');
    var usPos = toSvg(gox, goy + hd + 1.5, goz);
    document.getElementById('cp-us-label').setAttribute('x', usPos[0]);
    document.getElementById('cp-us-label').setAttribute('y', usPos[1]);
  }

  // ---------------------------------------------------------------
  // Snapshot Loading
  // ---------------------------------------------------------------
  function revokeSnapshotUrl(url) {
    if (url) {
      try { URL.revokeObjectURL(url); } catch (e) {}
    }
  }

  window.loadSnapshot = function(attempt) {
    attempt = attempt || 0;
    fetch('/api/video/snapshot/full?t=' + Date.now()).then(function(r) {
      if (!r.ok) throw new Error('No feed');
      return r.blob();
    }).then(function(blob) {
      var previousUrl = snapshotUrl;
      var nextUrl = URL.createObjectURL(blob);
      var img = new Image();
      img.onload = function() {
        imageWidth = img.naturalWidth;
        imageHeight = img.naturalHeight;
        snapshotUrl = nextUrl;
        showSnapshotOnCurrentStep(nextUrl, img);
        projectAndOverlay();
        revokeSnapshotUrl(previousUrl);
      };
      img.onerror = function() {
        revokeSnapshotUrl(nextUrl);
        showNoFeed();
      };
      img.src = nextUrl;
    }).catch(function() {
      // A just-changed source may still be connecting – retry briefly so the
      // new feed appears on its own, without a manual Refresh Image.
      if (attempt < 6) {
        setTimeout(function() { loadSnapshot(attempt + 1); }, 700);
      } else {
        showNoFeed();
      }
    });
  };

  function setPreviewVisibility(visible) {
    var pairs = [
      ['lens-container', 'lens-no-feed'],
      ['coarse-container', 'coarse-no-feed'],
      ['fine-container', 'fine-no-feed'],
      ['review-container', 'review-no-feed'],
    ];
    pairs.forEach(function(pair) {
      var container = document.getElementById(pair[0]);
      var placeholder = document.getElementById(pair[1]);
      if (container) container.style.display = visible ? 'block' : 'none';
      if (placeholder) placeholder.style.display = visible ? 'none' : 'block';
    });
    // Toggle disabled until snapshot loaded AND projection populated
    // cornerPositions. Without gate, operator could toggle in async
    // window and trigger null-corner errors. Snapshot-loss force-exits
    // zoom mode so next reload doesn't return to stale layout.
    if (!visible) {
      var toggle = document.getElementById('fine-zoom-toggle');
      if (toggle) {
        toggle.disabled = true;
        toggle.title = 'Load a snapshot first';
      }
      setFineZoomMode(false);
      document.getElementById('coarse-zoom-toggle').disabled = true;
      setCoarseZoomMode(false);
    } else {
      updateFineZoomToggleEnabled();
      updateCoarseZoomToggleEnabled();
    }
  }

  // Centralises the toggle's enabled/disabled state. Called whenever
  // anything that affects ``fineZoomReady()`` changes – snapshot
  // load/clear, projection completion, reset.
  function updateFineZoomToggleEnabled() {
    var toggle = document.getElementById('fine-zoom-toggle');
    if (!toggle) return;
    var ready = fineZoomReady();
    toggle.disabled = !ready;
    toggle.title = ready ? '' : 'Load a snapshot first';
  }

  function showNoFeed() {
    setPreviewVisibility(false);
  }

  function showSnapshotOnCurrentStep(url, img) {
    document.getElementById('coarse-image').src = url;
    document.getElementById('fine-image').src = url;
    document.getElementById('review-image').src = url;
    snapshotCanvas = document.createElement('canvas');
    snapshotCanvas.width = imageWidth;
    snapshotCanvas.height = imageHeight;
    snapshotCanvas.getContext('2d').drawImage(img, 0, 0);
    var lensImage = document.getElementById('lens-image');
    if (lensImage) {
      lensImage.src = url;
      lensOnSnapshot();
    }
    // Each fine-zoom box renders snapshot through own viewBox crop.
    // Update all <image> elements so Refresh Image lands in zoom too.
    var zoomImages = document.querySelectorAll('[data-fine-zoom-image]');
    for (var i = 0; i < zoomImages.length; i++) {
      zoomImages[i].setAttribute('href', url);
    }
    // Clear cached zoom-box viewBoxes – they're sized in image-pixel
    // space and a new snapshot may have different natural dimensions
    // than the previous one. The next ``renderAllFineZoomBoxes`` will
    // re-init from the current ``imageWidth`` / ``imageHeight`` and
    // the freshly-projected corner positions.
    fineZoomViewBoxes = { DSL: null, DSR: null, USR: null, USL: null };
    coarseZoomViewBox = null;
    // Set grid aspect-ratio as soon as image dimensions known
    // (avoids race where toggle happens before projection populates
    // cornerPositions). Per-box viewBox init still waits for ready().
    if (imageWidth > 0 && imageHeight > 0) {
      var grid = document.getElementById('fine-zoom-grid');
      if (grid) grid.style.aspectRatio = imageWidth + ' / ' + imageHeight;
    }
    setPreviewVisibility(true);
  }

  // ---------------------------------------------------------------
  // Projection
  // ---------------------------------------------------------------
  function projectAndOverlay() {
    if (!imageWidth || !imageHeight) return;
    // Don't overlay against a half-typed length (it would coerce to 0 m and
    // throw the projection off); just wait for a valid value.
    if (invalidLengthFields().length) return;
    var state = getState();
    fetch('/api/wizard/project', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        camera: state.camera,
        grid: state.grid,
        image_width: imageWidth,
        image_height: imageHeight,
      }),
    }).then(function(r) {
      return r.json().then(function(data) { return { ok: r.ok, data: data }; });
    })
    .then(function(res) {
      if (!res.ok || (res.data && res.data.error)) {
        // Don't silently drop the overlay – a corner behind the camera makes
        // the server reject the projection. Tell the operator why no corners
        // appeared instead of leaving a blank image.
        showProjectionError((res.data && res.data.error) || 'Could not project the grid onto the image.');
        return;
      }
      clearProjectionError();
      updateAllOverlays(res.data);
    })
    .catch(function(err) {
      showProjectionError('Could not project the grid onto the image.');
      if (typeof console !== 'undefined' && console.warn) console.warn('projectAndOverlay failed:', err);
    });
  }

  // The projected overlay is rendered on Reference Mapping, Corner Pinning,
  // and Review. A failed projection must surface on whichever of those the
  // operator is looking at – not only Corner Pinning – otherwise the overlay
  // silently vanishes on the other two with no explanation.
  var PROJECTION_STATUS_STEPS = {};
  PROJECTION_STATUS_STEPS[WIZ.ref] = { status: 'coarse-status', container: 'coarse-container' };
  PROJECTION_STATUS_STEPS[WIZ.corners] = { status: 'fine-status', container: 'fine-container' };
  PROJECTION_STATUS_STEPS[WIZ.review] = { status: 'review-status', container: 'review-container' };

  function currentProjectionEls() {
    return PROJECTION_STATUS_STEPS[currentStep] || PROJECTION_STATUS_STEPS[WIZ.corners];
  }

  function showProjectionError(msg) {
    var ids = currentProjectionEls();
    var el = document.getElementById(ids.status);
    if (el) {
      el.style.display = 'block';
      el.textContent = msg;
      el.className = 'wizard-status error';
    }
    var cont = document.getElementById(ids.container);
    if (cont) { cont.classList.add('invalid'); cont.classList.remove('valid'); }
  }

  function clearProjectionError() {
    var ids = currentProjectionEls();
    var el = document.getElementById(ids.status);
    // Only clear a projection error – leave a solve result message intact.
    if (el && el.classList.contains('error')) el.style.display = 'none';
    var cont = document.getElementById(ids.container);
    if (cont) cont.classList.remove('invalid');
  }

  function updateAllOverlays(data) {
    var c = data.corners;
    var ref = data.reference;

    // Set viewBox on all overlays
    var vb = '0 0 ' + imageWidth + ' ' + imageHeight;
    ['coarse-overlay', 'fine-overlay', 'review-overlay'].forEach(function(id) {
      document.getElementById(id).setAttribute('viewBox', vb);
    });

    // Quad points. Prefer the server's bowed boundary outline so the preview
    // curves exactly like the rendered HUD grid; fall back to a straight
    // 4-corner quad when distortion is off or an edge is behind the camera.
    var quadPts;
    if (data.outline && data.outline.length) {
      quadPts = data.outline.map(function(p) { return p[0] + ',' + p[1]; }).join(' ');
    } else {
      quadPts = c.DSL[0]+','+c.DSL[1]+' '+c.DSR[0]+','+c.DSR[1]+' '+c.USR[0]+','+c.USR[1]+' '+c.USL[0]+','+c.USL[1];
    }
    ['coarse-quad', 'fine-quad', 'review-quad'].forEach(function(id) {
      document.getElementById(id).setAttribute('points', quadPts);
    });

    renderGridLines('fine-grid', c);
    renderGridLines('review-grid', c);

    // Corner markers
    renderCornerMarkers('coarse-corners', c, false);
    renderCornerMarkers('fine-corners', c, true);
    renderCornerMarkers('review-corners', c, false);

    // Reference marker
    renderRefMarker('coarse-ref', ref, true);
    renderRefMarker('review-ref', ref, false);

    // Z-offset indicator line (ground → elevated ref)
    ['coarse-zoff', 'review-zoff'].forEach(function(id) {
      var g = document.getElementById(id);
      g.innerHTML = '';
      if (data.reference_elevated && data.z_offset) {
        renderZOffsetLine(g, ref, data.reference_elevated, data.z_offset);
      }
    });

    // Update corner positions for fine calibration
    cornerPositions.DSL = c.DSL.slice();
    cornerPositions.DSR = c.DSR.slice();
    cornerPositions.USR = c.USR.slice();
    cornerPositions.USL = c.USL.slice();

    // Bowed edges for the fine-zoom view, only when the server supplied a bowed
    // outline (no edge behind the camera) so the zoom boxes match the straight
    // main quad in the behind-camera fallback instead of curving on their own.
    fineBowedEdges = (data.outline && data.outline.length) ? buildBowedEdges() : null;

    // Refresh fine-zoom boxes AFTER cornerPositions updated so boxes
    // render new positions. Try/catch so zoom code failures don't break
    // overlay flow.
    try {
      renderAllFineZoomBoxes();
    } catch (err) {
      if (typeof console !== 'undefined' && console.warn) console.warn('fine-zoom render failed:', err);
    }
    // Enable Fine-adjust toggle now that cornerPositions populated.
    updateFineZoomToggleEnabled();
    recenterCoarseZoomIfNeeded();
    renderCoarseZoom();
    updateCoarseZoomToggleEnabled();
  }

  function renderZOffsetLine(container, groundPos, elevatedPos, zOffset) {
    // Dashed line from ground to elevated ref point
    var line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('x1', groundPos[0]);
    line.setAttribute('y1', groundPos[1]);
    line.setAttribute('x2', elevatedPos[0]);
    line.setAttribute('y2', elevatedPos[1]);
    line.setAttribute('stroke', '#ffbc00');
    line.setAttribute('stroke-width', '1.5');
    line.setAttribute('stroke-dasharray', '6,4');
    line.setAttribute('opacity', '0.7');
    container.appendChild(line);

    // Small circle at grid elevation
    var dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    dot.setAttribute('cx', elevatedPos[0]);
    dot.setAttribute('cy', elevatedPos[1]);
    dot.setAttribute('r', '4');
    dot.setAttribute('fill', 'none');
    dot.setAttribute('stroke', '#ffbc00');
    dot.setAttribute('stroke-width', '1.5');
    dot.setAttribute('opacity', '0.7');
    container.appendChild(dot);

    // Label
    var midX = (groundPos[0] + elevatedPos[0]) / 2;
    var midY = (groundPos[1] + elevatedPos[1]) / 2;
    var text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    text.setAttribute('x', midX + 10);
    text.setAttribute('y', midY);
    text.setAttribute('fill', '#ffbc00');
    text.setAttribute('font-size', '13');
    text.setAttribute('font-weight', '600');
    text.setAttribute('dominant-baseline', 'middle');
    text.setAttribute('opacity', '0.8');
    text.textContent = 'Z ' + WUNIT.formatLength(zOffset);
    container.appendChild(text);
  }

  function renderCornerMarkers(containerId, corners, draggable) {
    var g = document.getElementById(containerId);
    var focused = g.contains(document.activeElement) ? document.activeElement.dataset.corner : null;
    g.innerHTML = '';
    var u = overlayUnit();
    var centre = [0, 0];
    CORNER_NAMES.forEach(function(k) {
      centre[0] += corners[k][0] / 4;
      centre[1] += corners[k][1] / 4;
    });
    CORNER_NAMES.forEach(function(name, i) {
      var pos = corners[name];
      var group;
      // Only Corner Pinning drags a corner; elsewhere the grid's own corner is the mark.
      if (draggable) {
        group = pointHandle(pos, 7);
        group.dataset.corner = name;
        group.dataset.idx = i;
      } else {
        group = svgEl('g', { transform: 'translate('+pos[0]+','+pos[1]+')' });
      }

      // Diagonally outward from the grid's centre, so the name never sits on
      // the grid whichever way the camera looks at it.
      var sx = pos[0] < centre[0] ? -1 : 1;
      var sy = pos[1] < centre[1] ? -1 : 1;
      var label = svgEl('text', {
        x: sx * 1.6*u, y: sy * 1.6*u,
        fill: 'rgba(247,245,233,0.95)', stroke: '#000', 'stroke-width': 0.3*u, 'paint-order': 'stroke',
        'font-size': 1.3*u, 'font-weight': 700, 'text-anchor': sx < 0 ? 'end' : 'start',
        'dominant-baseline': 'central', 'pointer-events': 'none',
      });
      label.textContent = name;
      group.appendChild(label);

      g.appendChild(group);
      if (name === focused) group.focus({ preventScroll: true });
    });
  }

  function svgEl(tag, attrs) {
    var el = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.keys(attrs || {}).forEach(function(k) { el.setAttribute(k, attrs[k]); });
    return el;
  }

  // One hundredth of the frame. The overlay's viewBox is the snapshot's own
  // resolution, so a size fixed in viewBox units shrinks on a larger frame.
  function overlayUnit() {
    return Math.max(imageWidth, imageHeight, 1) / 100;
  }

  function renderRefMarker(containerId, pos, draggable) {
    var g = document.getElementById(containerId);
    var hadFocus = g.contains(document.activeElement);
    g.innerHTML = '';
    if (draggable) {
      var handle = pointHandle(pos, 7);
      handle.dataset.refHandle = '1';
      g.appendChild(handle);
      // Drawn again under a keyboard nudge: keep the focus so the next arrow moves it too.
      if (hadFocus) handle.focus({ preventScroll: true });
    } else {
      g.appendChild(pointMarker(pos));
    }
  }

  // The Lens step's point, used for every point the wizard drags: the dot, a
  // focus ring and the grab area around it.
  function pointHandle(pos, r) {
    var g = svgEl('g', { class: 'handle wizard-point', tabindex: '0', transform: 'translate(' + pos[0] + ',' + pos[1] + ')' });
    g.appendChild(svgEl('rect', { x: -22, y: -22, width: 44, height: 44, fill: 'transparent' }));
    g.appendChild(svgEl('circle', { r: 14, class: 'handle-ring' }));
    g.appendChild(svgEl('circle', { r: r, class: 'dot' }));
    return g;
  }

  // The same dot where nothing drags it: the Review step, the Fine adjust views.
  function pointMarker(pos, attrs) {
    var g = svgEl('g', { class: 'wizard-point', transform: 'translate(' + pos[0] + ',' + pos[1] + ')' });
    g.appendChild(svgEl('circle', Object.assign({ r: 7, class: 'dot' }, attrs)));
    return g;
  }

  // ---- loupe ----
  // The snapshot at full resolution, for the loupe and the Lens step.
  var snapshotCanvas = null;
  var LOUPE_ZOOM = 4;

  // A 4x look at the snapshot under a point being dragged, beside the finger so
  // it never hides what it shows.
  function showLoupe(containerId, overlayId, pos) {
    var container = document.getElementById(containerId);
    var loupe = container && container.querySelector('.wizard-loupe');
    if (!loupe || !snapshotCanvas) return;
    var canvas = loupe.querySelector('canvas'), ctx = canvas.getContext('2d');
    var src = canvas.width / LOUPE_ZOOM / 2;
    ctx.imageSmoothingEnabled = false;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(snapshotCanvas, pos[0] - src / 2, pos[1] - src / 2, src, src, 0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = '#ffbc00';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(canvas.width / 2, 0); ctx.lineTo(canvas.width / 2, canvas.height);
    ctx.moveTo(0, canvas.height / 2); ctx.lineTo(canvas.width, canvas.height / 2);
    ctx.stroke();
    var overlay = document.getElementById(overlayId);
    var box = overlay.getBoundingClientRect(), cont = container.getBoundingClientRect();
    var sx = box.left - cont.left + pos[0] / imageWidth * box.width;
    var sy = box.top - cont.top + pos[1] / imageHeight * box.height;
    var size = loupe.offsetWidth || 120;
    // Offset from the finger: above and to the right, flipped when it would leave the image.
    var lx = sx + 30, ly = sy - size - 30;
    if (lx + size > cont.width) lx = sx - size - 30;
    if (ly < 0) ly = sy + 30;
    loupe.style.left = Math.max(0, lx) + 'px';
    loupe.style.top = Math.max(0, ly) + 'px';
    loupe.style.display = 'block';
  }

  function hideLoupe(containerId) {
    var loupe = document.querySelector('#' + containerId + ' .wizard-loupe');
    if (loupe) loupe.style.display = 'none';
  }

  // ---------------------------------------------------------------
  // Reference Point dragging (Reference Mapping)
  // ---------------------------------------------------------------
  // Bound once to the Reference Point's group: the handle in it is drawn again
  // on every projection, so each event finds it afresh.
  function setupRefDragging(container) {
    var svg = document.getElementById('coarse-overlay');
    var dragging = false;
    var startScreen = null;
    var startPos = null;
    var startCornerPositions = null;

    function refHandle() { return container.querySelector('[data-ref-handle]'); }

    function getPos() {
      var handle = refHandle();
      var t = handle ? handle.getAttribute('transform') : '';
      var m = t.match(/translate\(([\d.e+-]+),([\d.e+-]+)\)/);
      return m ? [parseFloat(m[1]), parseFloat(m[2])] : [0,0];
    }

    function svgPoint(clientX, clientY) {
      var pt = svg.createSVGPoint();
      pt.x = clientX;
      pt.y = clientY;
      var ctm = svg.getScreenCTM().inverse();
      var svgP = pt.matrixTransform(ctm);
      return [svgP.x, svgP.y];
    }

    function onDown(e) {
      if (!e.target.closest('[data-ref-handle]')) return;
      e.preventDefault();
      dragging = true;
      var cx = e.touches ? e.touches[0].clientX : e.clientX;
      var cy = e.touches ? e.touches[0].clientY : e.clientY;
      startScreen = svgPoint(cx, cy);
      startPos = getPos();
      showLoupe('coarse-container', 'coarse-overlay', startPos);
      startCornerPositions = {};
      CORNER_NAMES.forEach(function(k) {
        startCornerPositions[k] = cornerPositions[k].slice();
      });
      refHandle().style.cursor = 'grabbing';
    }

    function onMove(e) {
      if (!dragging) return;
      e.preventDefault();
      var cx = e.touches ? e.touches[0].clientX : e.clientX;
      var cy = e.touches ? e.touches[0].clientY : e.clientY;
      var cur = svgPoint(cx, cy);
      var dx = cur[0] - startScreen[0];
      var dy = cur[1] - startScreen[1];
      var nx = startPos[0] + dx;
      var ny = startPos[1] + dy;
      refHandle().setAttribute('transform', 'translate('+nx+','+ny+')');
      showLoupe('coarse-container', 'coarse-overlay', [nx, ny]);
      shiftCoarseCorners(dx, dy, startCornerPositions);
      // Move z-offset group along with everything else
      document.getElementById('coarse-zoff').setAttribute('transform', 'translate('+dx+','+dy+')');
    }

    function onUp() {
      if (!dragging) return;
      dragging = false;
      hideLoupe('coarse-container');
      refHandle().style.cursor = '';
      document.getElementById('coarse-zoff').removeAttribute('transform');
      applyCoarseOffset(startPos, getPos(), startCornerPositions);
    }

    container.addEventListener('mousedown', onDown);
    container.addEventListener('touchstart', onDown, { passive: false });
    window.addEventListener('mousemove', onMove);
    window.addEventListener('touchmove', onMove, { passive: false });
    window.addEventListener('mouseup', onUp);
    window.addEventListener('touchend', onUp);

    // Keyboard arrow support
    container.addEventListener('keydown', function(e) {
      var handle = e.target.closest('[data-ref-handle]');
      if (!handle) return;
      var step = e.shiftKey ? 10 : 1;
      var pos = getPos();
      var moved = false;
      if (e.key === 'ArrowLeft') { pos[0] -= step; moved = true; }
      else if (e.key === 'ArrowRight') { pos[0] += step; moved = true; }
      else if (e.key === 'ArrowUp') { pos[1] -= step; moved = true; }
      else if (e.key === 'ArrowDown') { pos[1] += step; moved = true; }
      if (moved) {
        e.preventDefault();
        if (!startPos) {
          startPos = getPos();
          startCornerPositions = {};
          CORNER_NAMES.forEach(function(k) {
            startCornerPositions[k] = cornerPositions[k].slice();
          });
        }
        handle.setAttribute('transform', 'translate('+pos[0]+','+pos[1]+')');
        shiftCoarseCorners(pos[0] - startPos[0], pos[1] - startPos[1], startCornerPositions);
        clearTimeout(arrowDebounceTimer);
        var sp = startPos.slice();
        var scp = {};
        CORNER_NAMES.forEach(function(k) { scp[k] = startCornerPositions[k].slice(); });
        arrowDebounceTimer = setTimeout(function() {
          applyCoarseOffset(sp, getPos(), scp);
          startPos = null;
          startCornerPositions = null;
        }, 300);
      }
    });
    // Initialize startPos for keyboard on focus, the focus kept across a redraw included
    container.addEventListener('focusin', function(e) {
      if (!e.target.closest('[data-ref-handle]')) return;
      startPos = getPos();
      startCornerPositions = {};
      CORNER_NAMES.forEach(function(k) {
        startCornerPositions[k] = cornerPositions[k].slice();
      });
    });
  }

  // Move the full-view corners and grid outline with the Reference Point while
  // it is dragged; the solve that follows redraws them from the new camera.
  function shiftCoarseCorners(dx, dy, startCorners) {
    var quadParts = [];
    document.getElementById('coarse-corners').querySelectorAll('g').forEach(function(g, i) {
      var cp = startCorners[CORNER_NAMES[i]];
      g.setAttribute('transform', 'translate(' + (cp[0] + dx) + ',' + (cp[1] + dy) + ')');
      quadParts.push((cp[0] + dx) + ',' + (cp[1] + dy));
    });
    document.getElementById('coarse-quad').setAttribute('points', quadParts.join(' '));
  }

  // ---------------------------------------------------------------
  // Reference Point fine-adjust view
  // ---------------------------------------------------------------
  // One crop of the snapshot, FINE_ZOOM_FACTOR× zoomed around the Reference
  // Point, dragged and nudged like a Corner Pinning box. The full-view handle
  // stays the position's source of truth.
  var coarseZoomMode = false;
  var coarseZoomViewBox = null;  // [x, y, w, h] in image pixels; null re-centres

  function coarseRefPos() {
    var handle = document.querySelector('#coarse-ref .handle');
    var m = handle && handle.getAttribute('transform').match(/translate\(([\d.e+-]+),([\d.e+-]+)\)/);
    return m ? [parseFloat(m[1]), parseFloat(m[2])] : null;
  }

  function coarseZoomReady() {
    return fineZoomReady() && !!coarseRefPos();
  }

  function updateCoarseZoomToggleEnabled() {
    document.getElementById('coarse-zoom-toggle').disabled = !coarseZoomReady();
  }

  // Re-centre the crop once the point sits in its outer fifth, as the corner boxes do.
  function recenterCoarseZoomIfNeeded() {
    var vb = coarseZoomViewBox, pos = coarseRefPos();
    if (!vb || !pos) return;
    var nx = (pos[0] - vb[0]) / vb[2], ny = (pos[1] - vb[1]) / vb[3];
    if (nx < 0.2 || nx > 0.8 || ny < 0.2 || ny > 0.8) coarseZoomViewBox = null;
  }

  function renderCoarseZoom() {
    if (!coarseZoomMode || !coarseZoomReady()) return;
    var pos = coarseRefPos();
    if (!coarseZoomViewBox) {
      var w = imageWidth / FINE_ZOOM_FACTOR, h = imageHeight / FINE_ZOOM_FACTOR;
      coarseZoomViewBox = [pos[0] - w / 2, pos[1] - h / 2, w, h];
    }
    var svg = document.getElementById('coarse-zoom-svg');
    svg.setAttribute('viewBox', coarseZoomViewBox.join(' '));
    var image = svg.querySelector('[data-fine-zoom-image]');
    image.setAttribute('width', imageWidth);
    image.setAttribute('height', imageHeight);
    document.getElementById('coarse-zoom-quad').setAttribute('points', document.getElementById('coarse-quad').getAttribute('points'));
    var g = document.getElementById('coarse-zoom-ref');
    g.innerHTML = '';
    g.appendChild(pointMarker(pos, { 'vector-effect': 'non-scaling-stroke' }));
  }

  function setCoarseZoomMode(on) {
    if (on && !coarseZoomReady()) return;
    coarseZoomMode = !!on;
    var zoomView = document.getElementById('coarse-zoom-view');
    document.getElementById('coarse-full-view').style.display = coarseZoomMode ? 'none' : '';
    zoomView.style.display = coarseZoomMode ? '' : 'none';
    document.getElementById('coarse-zoom-toggle').textContent = coarseZoomMode ? 'Show full image' : 'Fine adjust';
    if (coarseZoomMode) {
      zoomView.style.aspectRatio = imageWidth + ' / ' + imageHeight;
      coarseZoomViewBox = null;
      renderCoarseZoom();
    }
  }

  window.toggleCoarseZoomMode = function() {
    setCoarseZoomMode(!coarseZoomMode);
  };

  function setupCoarseZoomDragging() {
    var svg = document.getElementById('coarse-zoom-svg');
    var activePointerId = null;
    var startPos = null;
    var startCorners = null;

    function pointFromEvent(e) {
      var ctm = svg.getScreenCTM();
      if (!ctm) return null;
      var pt = svg.createSVGPoint();
      pt.x = e.clientX;
      pt.y = e.clientY;
      var p = pt.matrixTransform(ctm.inverse());
      return [p.x, p.y];
    }

    // A pointer drag that starts while a nudge is still pending carries on from
    // the nudge's start, so the solve gets the whole move.
    function begin() {
      clearTimeout(arrowDebounceTimer);
      if (startPos) return;
      startPos = coarseRefPos();
      startCorners = {};
      CORNER_NAMES.forEach(function(k) { startCorners[k] = cornerPositions[k].slice(); });
    }

    function moveTo(p) {
      document.querySelector('#coarse-ref .handle').setAttribute('transform', 'translate(' + p[0] + ',' + p[1] + ')');
      shiftCoarseCorners(p[0] - startPos[0], p[1] - startPos[1], startCorners);
      renderCoarseZoom();
    }

    function finish() {
      var from = startPos, corners = startCorners;
      startPos = null;
      startCorners = null;
      applyCoarseOffset(from, coarseRefPos(), corners);
    }

    // As in the corner boxes, the crop stays put during a drag and re-centres
    // on release: moving it mid-drag would change the pointer mapping.
    function endDrag(e) {
      if (activePointerId === null || e.pointerId !== activePointerId) return;
      activePointerId = null;
      recenterCoarseZoomIfNeeded();
      renderCoarseZoom();
      finish();
    }

    svg.addEventListener('pointerdown', function(e) {
      if (!coarseZoomReady()) return;
      e.preventDefault();
      activePointerId = e.pointerId;
      try {
        svg.setPointerCapture(e.pointerId);
      } catch (_err) {
        // Capture can be refused; the drag still works while over the view.
      }
      begin();
      var p = pointFromEvent(e);
      if (p) moveTo(p);
    });
    svg.addEventListener('pointermove', function(e) {
      if (e.pointerId !== activePointerId) return;
      e.preventDefault();
      var p = pointFromEvent(e);
      if (p) moveTo(p);
    });
    svg.addEventListener('pointerup', endDrag);
    svg.addEventListener('pointercancel', endDrag);

    svg.addEventListener('keydown', function(e) {
      if (!coarseZoomReady() || activePointerId !== null) return;
      var step = e.shiftKey ? 10 : 1;
      var d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
      if (!d) return;
      e.preventDefault();
      begin();
      var pos = coarseRefPos();
      moveTo([pos[0] + d[0], pos[1] + d[1]]);
      recenterCoarseZoomIfNeeded();
      renderCoarseZoom();
      arrowDebounceTimer = setTimeout(finish, 300);
    });
  }

  function applyCoarseOffset(oldScreenPos, newScreenPos, savedCornerPositions) {
    var dx = newScreenPos[0] - oldScreenPos[0];
    var dy = newScreenPos[1] - oldScreenPos[1];
    // Translate all corner positions by the same screen delta
    CORNER_NAMES.forEach(function(k) {
      cornerPositions[k] = [
        savedCornerPositions[k][0] + dx,
        savedCornerPositions[k][1] + dy,
      ];
    });

    if (!isConvex(cornerPositions) || quadArea(cornerPositions) < 100) {
      return;
    }

    var screenCorners = [
      cornerPositions.DSL,
      cornerPositions.DSR,
      cornerPositions.USR,
      cornerPositions.USL,
    ];

    var seq = ++poseSeq;
    var body = wizardSolveBody(screenCorners);
    fetch('/api/wizard/solve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(function(r) { return r.json(); })
    .then(function(data) {
      if (seq !== poseSeq || data.error) return;
      applySolvedPose(data.camera, screenCorners, body);
      projectAndOverlay();
    });
  }

  // The grid's four corners in world space. Stage convention: +X is stage
  // left, so DSL/USL are at +hw and DSR/USR at -hw, in the order DSL, DSR,
  // USR, USL that every screen-corner list follows.
  function wizardWorldCorners() {
    var g = getState().grid;
    var hw = g.width / 2, hd = g.depth / 2;
    var ox = g.x_offset, oy = g.y_offset, oz = g.z_offset;
    return [
      [ox + hw, oy - hd, oz],
      [ox - hw, oy - hd, oz],
      [ox - hw, oy + hd, oz],
      [ox + hw, oy + hd, oz],
    ];
  }

  function wizardSolveBody(screenCorners) {
    return {
      world_corners: wizardWorldCorners(),
      screen_corners: screenCorners,
      image_width: imageWidth,
      image_height: imageHeight,
      camera: { lens_k1: wizReadLensCoeff('wiz_lens_k1'), lens_k2: wizReadLensCoeff('wiz_lens_k2') },
    };
  }

  // Back-propagate a solved camera into the form fields.
  function wizardApplySolvedCamera(cam) {
    wizWriteLen('cam_pos_x', cam.pos_x);
    wizWriteLen('cam_pos_y', cam.pos_y);
    wizWriteLen('cam_pos_z', cam.pos_z);
    document.getElementById('cam_pitch').value = cam.pitch;
    document.getElementById('cam_yaw').value = cam.yaw;
    document.getElementById('cam_roll').value = cam.roll;
    document.getElementById('cam_fov').value = cam.fov;
  }

  // Every solve lands here. The form holds the pose Review shows and Apply
  // saves; the pins and the lens pair the request undistorted them with are
  // kept, with the snapshot size, so a later lens change solves again from them.
  function applySolvedPose(camera, screenCorners, body) {
    wizardApplySolvedCamera(camera);
    pinnedCorners = {
      size: [body.image_width, body.image_height],
      corners: screenCorners.map(function(p) { return [p[0], p[1]]; }),
    };
    lensSolvedWith = { k1: body.camera.lens_k1, k2: body.camera.lens_k2 };
    if (document.getElementById('fine-solved-params').style.display !== 'none') showSolvedParams(camera);
    saveToSession();
    if (currentStep === WIZ.review) populateReview();
  }

  // The pose in the form, with the lens pair and pins it was solved from: what a Reset puts back.
  function snapshotPose() {
    return { camera: getState().camera, lensSolvedWith: lensSolvedWith, pins: pinnedCorners };
  }
  function restorePose(pose) {
    poseSeq++;
    wizardApplySolvedCamera(pose.camera);
    lensSolvedWith = pose.lensSolvedWith;
    pinnedCorners = pose.pins;
  }

  window.resetCoarseCalibration = function() {
    if (preCoarsePose) restorePose(preCoarsePose);
    saveToSession();
    projectAndOverlay();
  };

  // ---------------------------------------------------------------
  // Corner dragging (Corner Pinning)
  // ---------------------------------------------------------------
  function setupCornerDragging(container) {
    var svg = document.getElementById('fine-overlay');
    var dragging = null;

    function svgPoint(clientX, clientY) {
      var pt = svg.createSVGPoint();
      pt.x = clientX;
      pt.y = clientY;
      var ctm = svg.getScreenCTM().inverse();
      var svgP = pt.matrixTransform(ctm);
      return [svgP.x, svgP.y];
    }

    container.addEventListener('mousedown', function(e) {
      var handle = e.target.closest('.handle');
      if (!handle) return;
      startCornerDrag(handle, e);
    });
    container.addEventListener('touchstart', function(e) {
      var handle = e.target.closest('.handle');
      if (!handle) return;
      startCornerDrag(handle, e);
    }, { passive: false });

    function startCornerDrag(handle, e) {
      e.preventDefault();
      var cornerName = handle.dataset.corner;
      var cx = e.touches ? e.touches[0].clientX : e.clientX;
      var cy = e.touches ? e.touches[0].clientY : e.clientY;
      var startSvg = svgPoint(cx, cy);
      var startPos = cornerPositions[cornerName].slice();
      dragging = { handle: handle, corner: cornerName, startSvg: startSvg, startPos: startPos };
      handle.style.cursor = 'grabbing';
      showLoupe('fine-container', 'fine-overlay', startPos);
    }

    window.addEventListener('mousemove', function(e) {
      if (!dragging) return;
      e.preventDefault();
      moveCorner(e);
    });
    window.addEventListener('touchmove', function(e) {
      if (!dragging) return;
      e.preventDefault();
      moveCorner(e);
    }, { passive: false });

    function moveCorner(e) {
      var cx = e.touches ? e.touches[0].clientX : e.clientX;
      var cy = e.touches ? e.touches[0].clientY : e.clientY;
      var cur = svgPoint(cx, cy);
      var dx = cur[0] - dragging.startSvg[0];
      var dy = cur[1] - dragging.startSvg[1];
      var nx = dragging.startPos[0] + dx;
      var ny = dragging.startPos[1] + dy;
      cornerPositions[dragging.corner] = [nx, ny];
      dragging.handle.setAttribute('transform', 'translate('+nx+','+ny+')');
      showLoupe('fine-container', 'fine-overlay', [nx, ny]);
      updateFineQuad();
      // Update fine-zoom boxes as operator drags in full view.
      // Moved corner's box + neighbours' edges stay consistent.
      refreshFineZoomNeighbourhood(dragging.corner);
    }

    window.addEventListener('mouseup', function() {
      if (!dragging) return;
      hideLoupe('fine-container');
      dragging.handle.style.cursor = '';
      dragging = null;
      solveFromCorners();
    });
    window.addEventListener('touchend', function() {
      if (!dragging) return;
      hideLoupe('fine-container');
      dragging.handle.style.cursor = '';
      dragging = null;
      solveFromCorners();
    });

    // Keyboard arrow support for corners
    container.addEventListener('keydown', function(e) {
      var handle = e.target.closest('.handle');
      if (!handle || !handle.dataset.corner) return;
      var step = e.shiftKey ? 10 : 1;
      var cn = handle.dataset.corner;
      var pos = cornerPositions[cn];
      var moved = false;
      if (e.key === 'ArrowLeft') { pos[0] -= step; moved = true; }
      else if (e.key === 'ArrowRight') { pos[0] += step; moved = true; }
      else if (e.key === 'ArrowUp') { pos[1] -= step; moved = true; }
      else if (e.key === 'ArrowDown') { pos[1] += step; moved = true; }
      if (moved) {
        e.preventDefault();
        handle.setAttribute('transform', 'translate('+pos[0]+','+pos[1]+')');
        updateFineQuad();
        // Keep zoom view in sync with arrow-key nudges.
        // Same neighbourhood refresh as drag handler.
        refreshFineZoomNeighbourhood(cn);
        clearTimeout(arrowDebounceTimer);
        arrowDebounceTimer = setTimeout(function() {
          solveFromCorners();
        }, 300);
      }
    });
  }

  function updateFineQuad() {
    // Refresh the bowed boundary shape from the moved pins, but only when bowing
    // is already active for this projection. Keeps the curve live during a drag
    // without re-enabling it in the behind-camera fallback where the full quad is
    // straight.
    if (fineBowedEdges) fineBowedEdges = buildBowedEdges();
    var pts = cornerPositions;
    var quadPts;
    if (fineBowedEdges) {
      quadPts = bowedOutlinePoints(fineBowedEdges);
    } else {
      quadPts = pts.DSL[0]+','+pts.DSL[1]+' '+pts.DSR[0]+','+pts.DSR[1]+' '+pts.USR[0]+','+pts.USR[1]+' '+pts.USL[0]+','+pts.USL[1];
    }
    document.getElementById('fine-quad').setAttribute('points', quadPts);
    renderGridLines('fine-grid', cornerPositions);
  }

  // ---------------------------------------------------------------
  // Fine-adjust 4-box zoom view
  // ---------------------------------------------------------------
  // The four boxes share the source snapshot (same image element ``href``)
  // but each crops to a 4× zoomed window centred on its corner via the
  // SVG ``viewBox`` attribute. The viewBox is in image-pixel space (same
  // as the full ``fine-overlay``), so drag math via ``getScreenCTM().inverse()``
  // yields image pixels directly – no per-box pixel translation needed.
  // ``cornerPositions`` stays the single source of truth; rendering and
  // drag handlers read/write it the same way as the full view.
  var FINE_ZOOM_FACTOR = 4;
  // Each corner's pair of neighbours along the rectangle (DSL↔DSR↔USR↔USL).
  // Drives the polygon-edge rendering inside each box.
  var FINE_NEIGHBOURS = {
    DSL: ['DSR', 'USL'],
    DSR: ['DSL', 'USR'],
    USR: ['USL', 'DSR'],
    USL: ['USR', 'DSL'],
  };

  // Bowed boundary-edge polylines keyed 'A>B', so the fine-zoom view curves like
  // the full overlay / HUD. Rebuilt from the current pins by ``buildBowedEdges``
  // on every projection and on every manual corner move (see updateFineQuad), so
  // a dragged corner keeps its curve. null when distortion is off → straight.
  var fineBowedEdges = null;

  // Radial overlay-distortion mirror of scene/solver.py. Used to bow the
  // corner-pinning boundary purely from the pin positions, so the preview curves
  // exactly like the rendered HUD and stays correct live while a corner is
  // dragged (no server round-trip needed).
  var DISTORTION_SUBDIVISIONS = 12;
  var DISTORTION_INVERT_ITERS = 12;
  var DISTORTION_INVERT_R_CAP = 4;

  function wizApplyDistortion(pt, k1, k2) {
    if (k1 === 0 && k2 === 0) return [pt[0], pt[1]];
    var cx = imageWidth / 2, cy = imageHeight / 2;
    var halfDiag = 0.5 * Math.sqrt(imageWidth * imageWidth + imageHeight * imageHeight);
    var dx = (pt[0] - cx) / halfDiag;
    var dy = (pt[1] - cy) / halfDiag;
    var r2 = dx * dx + dy * dy;
    var f = 1 + k1 * r2 + k2 * r2 * r2;
    return [cx + dx * f * halfDiag, cy + dy * f * halfDiag];
  }

  // Solve r_u * f(r_u) = r_d by bracketed Newton steps, as the server does
  // (scene.solver.invert_normalised_radius); a radius the warp never reaches
  // lands on the fold radius.
  function wizInvertRadius(rd, k1, k2) {
    var rMax = Math.min(wizLensFoldRadius(k1, k2), DISTORTION_INVERT_R_CAP);
    var lo = 0, hi = rMax, r = Math.min(rd, rMax);
    for (var i = 0; i < DISTORTION_INVERT_ITERS; i++) {
      var r2 = r * r;
      var h = r * (1 + k1 * r2 + k2 * r2 * r2) - rd;
      var slope = 1 + 3 * k1 * r2 + 5 * k2 * r2 * r2;
      if (h < 0) lo = r; else if (h > 0) hi = r;
      var cand = slope > 1e-12 ? r - h / slope : r;
      r = (cand < lo || cand > hi || slope <= 1e-12) ? 0.5 * (lo + hi) : cand;
    }
    return r;
  }

  function wizInvertDistortion(pt, k1, k2) {
    if (k1 === 0 && k2 === 0) return [pt[0], pt[1]];
    var cx = imageWidth / 2, cy = imageHeight / 2;
    var halfDiag = 0.5 * Math.sqrt(imageWidth * imageWidth + imageHeight * imageHeight);
    var dx = (pt[0] - cx) / halfDiag;
    var dy = (pt[1] - cy) / halfDiag;
    var rd = Math.sqrt(dx * dx + dy * dy);
    var scale = rd > 0 ? wizInvertRadius(rd, k1, k2) / rd : 1;
    return [cx + dx * scale * halfDiag, cy + dy * scale * halfDiag];
  }

  // Bowed boundary edge between two corner pins, in screen space. A grid edge is
  // a straight WORLD line, so under pinhole it is a straight segment in the
  // undistorted frame: invert-distort both pins, walk that straight segment, and
  // re-distort each step. The chord set traces the same curve the HUD draws and
  // passes through both handles – computed only from the pins, so it stays valid
  // while a corner is dragged.
  function wizBowEdge(p0, p1, k1, k2) {
    var u0 = wizInvertDistortion(p0, k1, k2);
    var u1 = wizInvertDistortion(p1, k1, k2);
    var n = DISTORTION_SUBDIVISIONS;
    var poly = [];
    for (var i = 0; i <= n; i++) {
      var t = i / n;
      poly.push(wizApplyDistortion([u0[0] + t * (u1[0] - u0[0]), u0[1] + t * (u1[1] - u0[1])], k1, k2));
    }
    // Pin the endpoints exactly to the handles (guards sub-pixel inverse drift).
    poly[0] = [p0[0], p0[1]];
    poly[n] = [p1[0], p1[1]];
    return poly;
  }

  // All four bowed boundary edges keyed 'A>B' (+ reverse), from the current pins
  // and lens coefficients. null when distortion is off or the corners aren't
  // ready, so callers fall back to straight edges.
  function buildBowedEdges() {
    if (!fineZoomReady()) return null;
    var k1 = wizReadLensCoeff('wiz_lens_k1');
    var k2 = wizReadLensCoeff('wiz_lens_k2');
    if ((k1 === 0 && k2 === 0) || !wizLensIsValid(k1, k2)) return null;
    var edges = {};
    for (var k = 0; k < 4; k++) {
      var a = CORNER_NAMES[k];
      var b = CORNER_NAMES[(k + 1) % 4];
      var poly = wizBowEdge(cornerPositions[a], cornerPositions[b], k1, k2);
      edges[a + '>' + b] = poly;
      edges[b + '>' + a] = poly.slice().reverse();
    }
    return edges;
  }

  // Flatten the four bowed edges (CORNER_NAMES order) into an SVG points string
  // for the closed boundary polygon, dropping each edge's duplicate end corner.
  function bowedOutlinePoints(edges) {
    var pts = [];
    for (var k = 0; k < 4; k++) {
      var poly = edges[CORNER_NAMES[k] + '>' + CORNER_NAMES[(k + 1) % 4]];
      for (var j = 0; j < poly.length - 1; j++) pts.push(poly[j][0] + ',' + poly[j][1]);
    }
    return pts.join(' ');
  }

  // Mirrors runtime/overlay_draw_scene.grid_line_count: lines spread evenly
  // over each axis, both edges included.
  var GRID_MAX_LINES_PER_AXIS = 200;

  function wizGridLineCount(length, spacing) {
    return Math.min(Math.max(Math.floor(length / spacing) + 1, 2), GRID_MAX_LINES_PER_AXIS);
  }

  // The projective map of the unit square onto a quad:
  // (0,0) -> p0, (1,0) -> p1, (1,1) -> p2, (0,1) -> p3.
  function wizSquareToQuad(p0, p1, p2, p3) {
    var dx1 = p1[0] - p2[0], dx2 = p3[0] - p2[0], sx = p0[0] - p1[0] + p2[0] - p3[0];
    var dy1 = p1[1] - p2[1], dy2 = p3[1] - p2[1], sy = p0[1] - p1[1] + p2[1] - p3[1];
    var den = dx1 * dy2 - dx2 * dy1;
    var g = (sx * dy2 - dx2 * sy) / den;
    var h = (dx1 * sy - sx * dy1) / den;
    var a = p1[0] - p0[0] + g * p1[0], b = p3[0] - p0[0] + h * p3[0];
    var d = p1[1] - p0[1] + g * p1[1], e = p3[1] - p0[1] + h * p3[1];
    return function(s, t) {
      var w = g * s + h * t + 1;
      return [(a * s + b * t + p0[0]) / w, (d * s + e * t + p0[1]) / w];
    };
  }

  // The grid's inner lines in screen space, from its four corners. A plane
  // projects through a pinhole by a homography, so the undistorted corners fix
  // every point of it; each line is then bowed by the lens like the HUD's.
  // Built from the corners alone, so it follows a corner while it is dragged.
  function wizGridLines(corners, grid, k1, k2) {
    if (!(grid.spacing > 0) || !(grid.width > 0) || !(grid.depth > 0)) return [];
    if (!CORNER_NAMES.every(function(name) { return corners[name]; })) return [];
    if (!wizLensIsValid(k1, k2)) { k1 = 0; k2 = 0; }
    var u = {};
    CORNER_NAMES.forEach(function(name) { u[name] = wizInvertDistortion(corners[name], k1, k2); });
    if (!isConvex(u)) return [];
    // s runs across the width from DSR, t up the depth from downstage.
    var map = wizSquareToQuad(u.DSR, u.DSL, u.USL, u.USR);
    var chords = (k1 || k2) ? DISTORTION_SUBDIVISIONS : 1;
    var lines = [];
    function addLine(s0, t0, s1, t1) {
      var line = [];
      for (var i = 0; i <= chords; i++) {
        var f = i / chords;
        line.push(wizApplyDistortion(map(s0 + f * (s1 - s0), t0 + f * (t1 - t0)), k1, k2));
      }
      lines.push(line);
    }
    var across = wizGridLineCount(grid.depth, grid.spacing);
    var along = wizGridLineCount(grid.width, grid.spacing);
    // The outermost lines are the quad's own outline.
    for (var i = 1; i < across - 1; i++) addLine(0, i / (across - 1), 1, i / (across - 1));
    for (var j = 1; j < along - 1; j++) addLine(j / (along - 1), 0, j / (along - 1), 1);
    return lines;
  }

  function renderGridLines(groupId, corners) {
    var group = document.getElementById(groupId);
    group.innerHTML = '';
    var lines = wizGridLines(corners, getState().grid, wizReadLensCoeff('wiz_lens_k1'), wizReadLensCoeff('wiz_lens_k2'));
    var d = lines.map(function(line) {
      return 'M' + line.map(function(p) { return p[0] + ' ' + p[1]; }).join(' L');
    }).join(' ');
    if (d) group.appendChild(svgEl('path', { d: d, class: 'wizard-grid-line' }));
  }
  // Per-corner viewBox state. Each entry is [vbX, vbY, vbW, vbH] in
  // image-pixel coordinates. Lazily populated when zoom mode is first
  // activated for a snapshot – so we don't carry stale state across
  // snapshot reloads.
  var fineZoomViewBoxes = { DSL: null, DSR: null, USR: null, USL: null };
  var fineZoomMode = false;

  function fineZoomReady() {
    return imageWidth > 0 && imageHeight > 0
      && cornerPositions.DSL && cornerPositions.DSR
      && cornerPositions.USR && cornerPositions.USL;
  }

  function _initFineZoomViewBox(corner) {
    var pos = cornerPositions[corner];
    var vbW = imageWidth / (FINE_ZOOM_FACTOR * 2);
    var vbH = imageHeight / (FINE_ZOOM_FACTOR * 2);
    fineZoomViewBoxes[corner] = [
      pos[0] - vbW / 2,
      pos[1] - vbH / 2,
      vbW,
      vbH,
    ];
  }

  function renderFineZoomBox(corner) {
    if (!fineZoomReady()) return;
    if (!fineZoomViewBoxes[corner]) {
      _initFineZoomViewBox(corner);
    }
    var vb = fineZoomViewBoxes[corner];
    var box = document.querySelector('.fine-zoom-box[data-corner="' + corner + '"]');
    if (!box) return;
    var svg = box.querySelector('svg');
    svg.setAttribute('viewBox', vb[0] + ' ' + vb[1] + ' ' + vb[2] + ' ' + vb[3]);

    // Source image fills the full image-pixel coordinate plane; the
    // viewBox is the crop window. Set width/height every render so a
    // post-load resize of the snapshot still renders correctly.
    var imageEl = svg.querySelector('[data-fine-zoom-image]');
    imageEl.setAttribute('width', imageWidth);
    imageEl.setAttribute('height', imageHeight);

    // Edges from this corner to its two neighbours. ``vector-effect``
    // keeps stroke widths constant in CSS pixels regardless of zoom.
    var edgesG = svg.querySelector('[data-fine-zoom-edges]');
    edgesG.innerHTML = '';
    var here = cornerPositions[corner];
    FINE_NEIGHBOURS[corner].forEach(function(neighbour) {
      var bowed = fineBowedEdges && fineBowedEdges[corner + '>' + neighbour];
      var el;
      if (bowed && bowed.length > 2) {
        // Curved boundary edge, matching the full overlay / rendered HUD.
        el = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
        el.setAttribute('points', bowed.map(function(p) { return p[0] + ',' + p[1]; }).join(' '));
        el.setAttribute('fill', 'none');
      } else {
        // Straight edge to the neighbour pin (no distortion, or mid-drag).
        var n = cornerPositions[neighbour];
        el = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        el.setAttribute('x1', here[0]);
        el.setAttribute('y1', here[1]);
        el.setAttribute('x2', n[0]);
        el.setAttribute('y2', n[1]);
      }
      el.setAttribute('stroke', 'rgba(255,188,0,0.7)');
      el.setAttribute('stroke-width', '2');
      el.setAttribute('vector-effect', 'non-scaling-stroke');
      edgesG.appendChild(el);
    });

    var markerG = svg.querySelector('[data-fine-zoom-marker]');
    markerG.innerHTML = '';
    markerG.appendChild(pointMarker(here, { 'vector-effect': 'non-scaling-stroke' }));
  }

  function renderAllFineZoomBoxes() {
    if (!fineZoomReady()) return;
    ['USL', 'USR', 'DSL', 'DSR'].forEach(renderFineZoomBox);
  }

  // Refresh the moved corner's box + its two neighbours' boxes (whose
  // edges to this corner have moved). Used after both full-view drags
  // and zoom-view drags so the views stay coherent regardless of which
  // one the operator interacts with.
  function refreshFineZoomNeighbourhood(corner) {
    if (!fineZoomReady()) return;
    renderFineZoomBox(corner);
    FINE_NEIGHBOURS[corner].forEach(renderFineZoomBox);
  }

  // Re-centre rule (drag-release only): if the corner sits within
  // 1/5 of any box edge – or completely outside the box – shift the
  // viewBox so the corner returns to box centre. We deliberately do
  // NOT run this mid-drag: shifting the viewBox during a drag changes
  // how subsequent cursor screen positions map to image-pixel
  // coordinates via ``getScreenCTM().inverse()``, and the corner
  // would accelerate away as each recentre compounds the next mapping
  // delta. Returns true when a shift happened so the caller knows to
  // re-render.
  function recenterFineZoomBoxIfNeeded(corner) {
    var vb = fineZoomViewBoxes[corner];
    if (!vb) return false;
    var pos = cornerPositions[corner];
    var nx = (pos[0] - vb[0]) / vb[2];
    var ny = (pos[1] - vb[1]) / vb[3];
    if (nx < 0.2 || nx > 0.8 || ny < 0.2 || ny > 0.8) {
      fineZoomViewBoxes[corner] = [
        pos[0] - vb[2] / 2,
        pos[1] - vb[3] / 2,
        vb[2],
        vb[3],
      ];
      return true;
    }
    return false;
  }

  // Pointer + touch drag wiring per zoom box. Bound once at page load;
  // event delegation off each SVG keeps re-renders cheap (we never
  // remove/replace the SVG element itself, only its inner groups).
  function setupFineZoomDragging() {
    var boxes = document.querySelectorAll('.fine-zoom-box');
    for (var i = 0; i < boxes.length; i++) {
      _attachFineZoomBoxHandlers(boxes[i]);
    }
  }

  function _attachFineZoomBoxHandlers(box) {
    var svg = box.querySelector('svg');
    var corner = box.dataset.corner;
    var activePointerId = null;

    function svgPoint(clientX, clientY) {
      var pt = svg.createSVGPoint();
      pt.x = clientX;
      pt.y = clientY;
      var ctm = svg.getScreenCTM();
      if (!ctm) return null;
      var p = pt.matrixTransform(ctm.inverse());
      return [p.x, p.y];
    }

    function setCornerFromEvent(e) {
      // Defence-in-depth: pointer events
      // shouldn't fire on the SVG before ``fineZoomReady()`` is true
      // because the toggle gate now blocks zoom-mode entry until then.
      // Bail anyway in case the operator finds another path here so
      // ``updateFineQuad`` doesn't dereference a null sibling
      // ``cornerPositions`` entry.
      if (!fineZoomReady()) return;
      var p = svgPoint(e.clientX, e.clientY);
      if (!p) return;
      cornerPositions[corner] = [p[0], p[1]];
      // Update the moved corner's full-view marker too – the operator
      // may flip back to the full view at any time.
      var fullHandle = document.querySelector('#fine-corners .handle[data-corner="' + corner + '"]');
      if (fullHandle) {
        fullHandle.setAttribute('transform', 'translate(' + p[0] + ',' + p[1] + ')');
      }
      updateFineQuad();
      // Deliberately do NOT recenter mid-drag. Recentring shifts the
      // viewBox, which changes how subsequent screen-pixel positions
      // map to image-pixel positions via ``getScreenCTM().inverse()``
      // – once the corner crosses the 1/5 boundary, every following
      // pointermove would land further out than the actual cursor
      // delta warrants, and the corner flies off uncontrollably. The
      // recentre runs once at drag-release in ``endDrag`` instead.
      // Within a single drag the box stays fixed; if the cursor
      // leaves the visible box the corner simply renders outside the
      // SVG viewport (clipped), and the release-time recentre brings
      // it back into view.
      refreshFineZoomNeighbourhood(corner);
    }

    function endDrag(e) {
      // Ignore pointerup/pointercancel events from a different pointer
      // – relevant for multi-touch. Without
      // this, lifting a stray secondary finger would prematurely end
      // the active drag.
      if (activePointerId === null) return;
      if (e && e.pointerId !== undefined && e.pointerId !== activePointerId) return;
      activePointerId = null;
      // Recentre on release if the corner ended in the outer 1/5
      // ring (or completely outside the box) so the operator's next
      // adjustment lands on a visible target. Re-render the affected
      // box + neighbours so the new viewBox + clipped edges paint.
      if (recenterFineZoomBoxIfNeeded(corner)) {
        refreshFineZoomNeighbourhood(corner);
      }
      solveFromCorners();
    }

    // Pointer Events + setPointerCapture – handles mouse / touch /
    // pen uniformly, and capture means pointermove keeps firing on
    // the SVG even when the cursor leaves the box. Replaces the four
    // pairs of window-level mousemove/mouseup + touchmove/touchend
    // listeners (one per box, total 16 globals) the previous
    // implementation used.
    svg.addEventListener('pointerdown', function(e) {
      e.preventDefault();
      activePointerId = e.pointerId;
      try {
        svg.setPointerCapture(e.pointerId);
      } catch (_err) {
        // Some older browsers / synthetic events may reject capture;
        // the drag still works without it (the SVG will receive
        // pointermove while the cursor is over it).
      }
      setCornerFromEvent(e);
    });
    svg.addEventListener('pointermove', function(e) {
      if (e.pointerId !== activePointerId) return;
      e.preventDefault();
      setCornerFromEvent(e);
    });
    svg.addEventListener('pointerup', endDrag);
    svg.addEventListener('pointercancel', endDrag);

    // Hiding the full view in
    // zoom mode removes its focusable ``.handle`` elements, which
    // means Tab + arrow-key nudging stops working. Wire equivalent
    // keyboard handling on the zoom SVG so the operator keeps their
    // pixel-precise nudge workflow in either view. Same step semantics
    // (1px / 10px with Shift) and same debounced solve as the full
    // view's keyboard handler.
    svg.setAttribute('tabindex', '0');
    svg.addEventListener('keydown', function(e) {
      // Same readiness gate as pointer path. Toggle disabled until ready.
      // true, but a focused SVG could still receive keydown if the
      // operator tabbed in via some other path.
      if (!fineZoomReady()) return;
      var step = e.shiftKey ? 10 : 1;
      var pos = cornerPositions[corner];
      var moved = false;
      if (e.key === 'ArrowLeft') { pos[0] -= step; moved = true; }
      else if (e.key === 'ArrowRight') { pos[0] += step; moved = true; }
      else if (e.key === 'ArrowUp') { pos[1] -= step; moved = true; }
      else if (e.key === 'ArrowDown') { pos[1] += step; moved = true; }
      if (!moved) return;
      e.preventDefault();
      // Mirror to the full-view handle so a flip-back shows the
      // same position.
      var fullHandle = document.querySelector('#fine-corners .handle[data-corner="' + corner + '"]');
      if (fullHandle) {
        fullHandle.setAttribute('transform', 'translate(' + pos[0] + ',' + pos[1] + ')');
      }
      updateFineQuad();
      // Re-centre on each keypress is cheap and feels right for
      // arrow-key nudging – unlike pointer drag, there's no
      // accelerating-mapping feedback loop because the cursor
      // doesn't drive the position.
      if (recenterFineZoomBoxIfNeeded(corner)) {
        refreshFineZoomNeighbourhood(corner);
      } else {
        renderFineZoomBox(corner);
        FINE_NEIGHBOURS[corner].forEach(renderFineZoomBox);
      }
      clearTimeout(arrowDebounceTimer);
      arrowDebounceTimer = setTimeout(function() {
        solveFromCorners();
      }, 300);
    });
  }

  function setFineZoomMode(on) {
    var fullView = document.getElementById('fine-full-view');
    var zoomView = document.getElementById('fine-zoom-view');
    var toggle = document.getElementById('fine-zoom-toggle');
    if (!fullView || !zoomView) return;
    // Refuse to enter zoom mode until the source image dimensions and
    // every corner position are known. The toggle is gated on the
    // same readiness check, but
    // ``toggleFineZoomMode`` is exposed on ``window`` and could be
    // invoked programmatically – this defends that path too.
    if (on && !fineZoomReady()) return;
    fineZoomMode = !!on;
    fullView.style.display = fineZoomMode ? 'none' : '';
    zoomView.style.display = fineZoomMode ? '' : 'none';
    if (toggle) {
      toggle.textContent = fineZoomMode ? 'Show full image' : 'Fine adjust';
    }
    if (fineZoomMode && fineZoomReady()) {
      // Match the four-box grid's overall aspect ratio to the source
      // image so the per-box crops stay non-distorted.
      var grid = document.getElementById('fine-zoom-grid');
      if (grid) grid.style.aspectRatio = imageWidth + ' / ' + imageHeight;
      // Reset every viewBox so toggling-in re-centres on the operator's
      // current corner positions rather than carrying a stale crop from
      // before the last full-view drag.
      ['USL', 'USR', 'DSL', 'DSR'].forEach(_initFineZoomViewBox);
      renderAllFineZoomBoxes();
    }
  }

  window.toggleFineZoomMode = function() {
    setFineZoomMode(!fineZoomMode);
  };

  function isConvex(pts) {
    var p = [pts.DSL, pts.DSR, pts.USR, pts.USL];
    var signs = [];
    for (var i = 0; i < 4; i++) {
      var ax = p[i][0], ay = p[i][1];
      var bx = p[(i+1)%4][0], by = p[(i+1)%4][1];
      var cx = p[(i+2)%4][0], cy = p[(i+2)%4][1];
      signs.push((bx-ax)*(cy-by) - (by-ay)*(cx-bx));
    }
    return signs.every(function(s){return s>0;}) || signs.every(function(s){return s<0;});
  }

  function quadArea(pts) {
    var p = [pts.DSL, pts.DSR, pts.USR, pts.USL];
    var area = 0;
    for (var i = 0; i < 4; i++) {
      var j = (i+1)%4;
      area += p[i][0]*p[j][1] - p[j][0]*p[i][1];
    }
    return Math.abs(area)/2;
  }

  function solveFromCorners() {
    if (!isConvex(cornerPositions) || quadArea(cornerPositions) < 100) {
      showSolveStatus('Invalid perspective \u2013 adjust corners', false);
      return;
    }
    var badLen = invalidLengthFields();
    if (badLen.length) {
      showSolveStatus('Fix invalid length field(s) first: ' + badLen.join(', '), false);
      return;
    }

    // Snapshot the camera form before the first solve of this
    // corner-pinning session so Reset Corners can revert the form
    // values that solveFromCorners is about to overwrite. Only takes
    // a snapshot when none exists \u2013 re-entry to the step keeps the
    // original snapshot until Reset clears it.
    if (preCornerPinningPose === null) {
      preCornerPinningPose = snapshotPose();
    }

    var screenCorners = [
      cornerPositions.DSL,
      cornerPositions.DSR,
      cornerPositions.USR,
      cornerPositions.USL,
    ];

    var seq = ++poseSeq;
    var body = wizardSolveBody(screenCorners);
    fetch('/api/wizard/solve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(function(r) { return r.json(); })
    .then(function(data) {
      if (seq !== poseSeq) return;
      if (data.error) {
        showSolveStatus(data.error, false);
        return;
      }

      showSolveStatus('Calibration valid', true);
      showSolvedParams(data.camera);

      // Snap corners to reprojected positions
      var rp = data.reprojected_corners;
      var keys = CORNER_NAMES;
      for (var i = 0; i < 4; i++) {
        cornerPositions[keys[i]] = rp[i];
      }
      // Update handle positions
      var handles = document.getElementById('fine-corners').querySelectorAll('.handle');
      handles.forEach(function(h) {
        var cn = h.dataset.corner;
        var p = cornerPositions[cn];
        h.setAttribute('transform', 'translate('+p[0]+','+p[1]+')');
      });
      updateFineQuad();
      // Solve snapped corners to reprojected positions –
      // refresh the zoom boxes so their markers + edges paint at the
      // post-snap positions instead of the pre-snap drag-end ones.
      try {
        renderAllFineZoomBoxes();
      } catch (err) {
        if (typeof console !== 'undefined' && console.warn) console.warn('fine-zoom render failed:', err);
      }

      applySolvedPose(data.camera, screenCorners, body);
    });
  }

  function showSolveStatus(msg, ok) {
    var el = document.getElementById('fine-status');
    el.style.display = 'block';
    el.textContent = msg;
    el.className = 'wizard-status ' + (ok ? 'ok' : 'error');
    var cont = document.getElementById('fine-container');
    cont.classList.toggle('valid', ok);
    cont.classList.toggle('invalid', !ok);
  }

  function showSolvedParams(cam) {
    document.getElementById('fine-solved-params').style.display = 'grid';
    document.getElementById('solved-pos-x').textContent = WUNIT.formatLength(cam.pos_x);
    document.getElementById('solved-pos-y').textContent = WUNIT.formatLength(cam.pos_y);
    document.getElementById('solved-pos-z').textContent = WUNIT.formatLength(cam.pos_z);
    document.getElementById('solved-pitch').textContent = cam.pitch.toFixed(1) + '\u00b0';
    document.getElementById('solved-yaw').textContent = cam.yaw.toFixed(1) + '\u00b0';
    document.getElementById('solved-roll').textContent = cam.roll.toFixed(1) + '\u00b0';
    var fovText = cam.fov.toFixed(1) + '\u00b0';
    if (Math.abs(cam.fov - originalFov) > 0.5) {
      fovText += ' (was ' + originalFov.toFixed(1) + '\u00b0)';
    }
    document.getElementById('solved-fov').textContent = fovText;
  }

  window.resetCornerPinning = function() {
    document.getElementById('fine-status').style.display = 'none';
    document.getElementById('fine-solved-params').style.display = 'none';
    document.getElementById('fine-container').classList.remove('valid', 'invalid');
    // Restore the pose from before this session's first solve, with the lens
    // and pins it was solved from, so the re-projection puts the corners back
    // where the operator started, not where they were dragged to. Clearing the
    // snapshot lets the next solve session start fresh.
    if (preCornerPinningPose) {
      restorePose(preCornerPinningPose);
      preCornerPinningPose = null;
      saveToSession();
    }
    // Clear cached zoom-box viewBoxes so the next
    // ``renderAllFineZoomBoxes`` re-centres each box on the freshly
    // reset corner position. Without this, a reset while the operator
    // is in zoom mode would keep showing the box framed around the
    // pre-reset corner location and the new marker could land off-
    // screen relative to that stale viewBox.
    fineZoomViewBoxes = { DSL: null, DSR: null, USR: null, USL: null };
    projectAndOverlay();
  };

  // ---------------------------------------------------------------
  // Finish
  // ---------------------------------------------------------------
  // ---------------------------------------------------------------
  // Review Step
  // ---------------------------------------------------------------
  function populateReview() {
    var state = getState();
    var cam = state.camera;
    var g = state.grid;

    document.getElementById('review-cam-pos-x').textContent = WUNIT.formatLength(Number(cam.pos_x));
    document.getElementById('review-cam-pos-y').textContent = WUNIT.formatLength(Number(cam.pos_y));
    document.getElementById('review-cam-pos-z').textContent = WUNIT.formatLength(Number(cam.pos_z));
    document.getElementById('review-cam-pitch').textContent = Number(cam.pitch).toFixed(1) + '\u00b0';
    document.getElementById('review-cam-yaw').textContent = Number(cam.yaw).toFixed(1) + '\u00b0';
    document.getElementById('review-cam-roll').textContent = Number(cam.roll).toFixed(1) + '\u00b0';
    var fovText = Number(cam.fov).toFixed(1) + '\u00b0';
    if (Math.abs(cam.fov - originalFov) > 0.5) {
      fovText += ' (was ' + originalFov.toFixed(1) + '\u00b0)';
    }
    document.getElementById('review-cam-fov').textContent = fovText;

    document.getElementById('review-grid-width').textContent = WUNIT.formatLength(Number(g.width));
    document.getElementById('review-grid-depth').textContent = WUNIT.formatLength(Number(g.depth));
    document.getElementById('review-grid-spacing').textContent = WUNIT.formatLength(Number(g.spacing));
    document.getElementById('review-grid-x-offset').textContent = WUNIT.formatLength(Number(g.x_offset));
    document.getElementById('review-grid-y-offset').textContent = WUNIT.formatLength(Number(g.y_offset));
    document.getElementById('review-grid-z-offset').textContent = WUNIT.formatLength(Number(g.z_offset));
    populateReviewLens(state.camera);

    // The review overlay (viewBox, bowed quad, corners, ref, z-offset) is drawn
    // by ``loadSnapshot`` -> ``projectAndOverlay`` -> ``updateAllOverlays`` on
    // entering this step, using the live form values (which already carry the
    // solved camera + lens coefficients) and the server's bowed boundary
    // outline. A second projection here would race that one and briefly paint a
    // straight, lens-free quad – so populateReview only fills the text summary.
  }

  window.applyAndFinish = function() {
    var saveError = window.OpenFollow.saveError;
    var reviewBox = document.querySelector('#wizard-step-review .section');
    var badLen = invalidLengthFields();
    if (badLen.length) {
      saveError.show(reviewBox, {
        error: 'These fields are not valid lengths: ' + badLen.join(', ') + '.',
        action: 'Fix them before finishing.',
      }, 'Not applied.');
      return;
    }
    if (!wizLensPairValid()) {
      saveError.show(reviewBox, {
        error: 'The lens pair folds the overlay inside the frame.',
        action: 'Bring either lens value closer to 0 on the Lens step before finishing.',
      }, 'Not applied.');
      return;
    }
    var state = getState();
    var camData = state.camera;
    var gridData = state.grid;
    var lensData = state.lens || {};

    // Use JSON API to avoid bool_fields issues with form POST
    var headers = { 'Content-Type': 'application/json' };

    // Resolve sensor width: preset width, or custom entry, or null.
    var sensorWidth = null;
    var focalLength = null;
    if (lensData.sensor_id === 'custom') {
      var cw = parseFloat(lensData.sensor_custom);
      sensorWidth = isFinite(cw) && cw > 0 ? cw : null;
    } else if (lensData.sensor_id) {
      var entry = SENSOR_SIZES.find(function(s) { return s.id === lensData.sensor_id; });
      if (entry && entry.width_mm) sensorWidth = entry.width_mm;
    }
    var fl = parseFloat(lensData.focal);
    if (isFinite(fl) && fl > 0) focalLength = fl;

    var cameraPayload = {
      pos_x: camData.pos_x, pos_y: camData.pos_y, pos_z: camData.pos_z,
      pitch: camData.pitch, yaw: camData.yaw, roll: camData.roll, fov: camData.fov,
      sensor_width_mm: sensorWidth,
      focal_length_mm: focalLength,
      // The DLT solve is pinhole and doesn't return distortion; carry the
      // Lens step's pair through from the wizard state.
      lens_k1: state.camera.lens_k1,
      lens_k2: state.camera.lens_k2,
    };

    Promise.all([
      fetch('/api/config/camera', {
        method: 'POST', headers: headers,
        body: JSON.stringify(cameraPayload),
      }),
      fetch('/api/config/grid', {
        method: 'POST', headers: headers,
        body: JSON.stringify({
          width: gridData.width, depth: gridData.depth, spacing: gridData.spacing,
          x_offset: gridData.x_offset, y_offset: gridData.y_offset, z_offset: gridData.z_offset,
        }),
      }),
    ]).then(async function(responses) {
      var labeled = [
        { name: 'camera', response: responses[0] },
        { name: 'grid', response: responses[1] },
      ];
      for (var i = 0; i < labeled.length; i++) {
        var entry = labeled[i];
        if (!entry.response.ok) {
          saveError.show(reviewBox, await saveError.fromResponse(entry.response),
            'The ' + entry.name + ' settings were not applied.');
          return;
        }
      }
      try { sessionStorage.removeItem(STORAGE_KEY); } catch(e) {}
      window.location.href = '/';
    }).catch(function() {
      saveError.show(reviewBox, saveError.UNREACHABLE, 'Not applied.');
    });
  };

  window.discardAndLeave = function() {
    try { sessionStorage.removeItem(STORAGE_KEY); } catch(e) {}
    window.location.href = '/';
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
  // Lens (experimental): the distortion from lines that are straight in reality
  // ---------------------------------------------------------------
  // A line keeps its endpoints p0 / p1 (image px) and its three middle points
  // in the line's own frame as (t, off): t along the chord, off along its
  // perpendicular. Moving an endpoint keeps every middle point's offset; a
  // middle point only ever moves along the perpendicular.
  var LENS_T = [0.25, 0.5, 0.75];
  var LENS_FIT_DEBOUNCE_MS = 300;
  var LENS_MIN_LINE_PX = 20;
  // Mirror lens_fit._MIN_LINE_SPAN_PX: a line spanning less carries no direction to fit.
  var LENS_MIN_SPAN_PX = 10;
  // CSS pixels a finger may wander before a tap counts as a drag.
  var LENS_TAP_SLOP_PX = 6;
  var lensFitSeq = 0;        // every fit request counts; an answer to an older one is dropped
  var LENS_RATING_LEVEL = { low: 'caution', medium: 'caution', okay: 'info', good: 'success', excellent: 'success' };
  var LENS_RATING_SEGMENTS = { low: 1, medium: 2, okay: 3, good: 4, excellent: 5 };
  var LENS_RATING_LABEL = { low: 'Low', medium: 'Medium', okay: 'Okay', good: 'Good', excellent: 'Excellent' };
  var LENS_MISFIT_TEXT = "This line doesn't fit the others – is it really straight?";
  var LENS_DEV = {{'true' if config.ui.developer_mode else 'false'}};
  var lensLines = [];        // [{p0, p1, mids: [{t, off, on}], snapped: [bool x5], curve, rms, misfit, candidate}]
  var lensCandidates = [];   // suggested edges of the current snapshot: [{points, samples, used}]
  var lensImageSize = null;  // [w, h] the lines were traced on
  var lensPending = null;    // first click of a line being traced
  var lensSelected = null;   // {line, point} with point 0..4
  var lensFit = null;        // the last fit response
  var lensCanvas = null;     // the snapshot at full resolution, for the luma band
  var lensFitTimer = null;
  var lensReSolveTimer = null;
  var lensDrag = null;
  var pinnedCorners = null;  // {size, corners} of the last solve: the snapshot size and the four pins
  var lensSolvedWith = { k1: wizReadLensCoeff('wiz_lens_k1'), k2: wizReadLensCoeff('wiz_lens_k2') };

  function lensEnabled() { return !!document.getElementById('lens-container'); }

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
    var f = lensFrame(line), m = line.mids[j - 1];
    var bx = line.p0[0] + m.t * f.dx, by = line.p0[1] + m.t * f.dy;
    m.off = (pos[0] - bx) * f.nx + (pos[1] - by) * f.ny;
  }
  function lensSetMidsFromPositions(line, positions) {
    var f = lensFrame(line);
    for (var j = 1; j <= 3; j++) {
      var m = line.mids[j - 1], rx = positions[j][0] - line.p0[0], ry = positions[j][1] - line.p0[1];
      m.t = (rx * f.dx + ry * f.dy) / (f.len * f.len);
      m.off = rx * f.nx + ry * f.ny;
    }
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
    if (!lensEnabled()) return;
    document.getElementById('lens-overlay').setAttribute('viewBox', '0 0 ' + imageWidth + ' ' + imageHeight);
    lensCanvas = snapshotCanvas;
    if (lensImageSize && (lensImageSize[0] !== imageWidth || lensImageSize[1] !== imageHeight) && lensLines.length) {
      lensFitSeq++;
      lensLines = [];
      lensFit = null;
      lensSelected = null;
      lensPending = null;
      lensShowNotice('The snapshot resolution changed, so the traced lines were cleared.');
    }
    lensImageSize = [imageWidth, imageHeight];
    lensCandidates = [];
    renderLens();
    renderLensResult();
    saveToSession();
    // The suggestions are for this step: a snapshot loaded for another step is not sent.
    if (currentStep === WIZ.lens) lensRequestEdges();
  }
  function lensShowNotice(text) {
    var el = document.getElementById('lens-notice');
    if (!el) return;
    el.textContent = text || '';
    el.style.display = text ? '' : 'none';
  }
  function lensShowStatus(msg, ok) {
    var el = document.getElementById('lens-status');
    if (!el) return;
    el.style.display = msg ? 'block' : 'none';
    el.textContent = msg || '';
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
    lensSelected = { line: lensLines.length - 1, point: 4 };
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
    if (!lensCanvas) return;
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
      lensCandidates = res.data.candidates.map(function(c) { return { points: c.points, samples: c.samples, used: false }; });
      lensLines.forEach(function(line) { line.candidate = lensCandidateOf(line); });
      if (res.data.edges) lensPaintEdges(res.data.edges);
      renderLens();
    }).catch(function() {});
  }
  // The suggestion a line was taken from, by its ends, so it is offered again when the line goes.
  function lensCandidateOf(line) {
    for (var i = 0; i < lensCandidates.length; i++) {
      var s = lensCandidates[i].samples;
      var same = (Math.hypot(s[0][0] - line.p0[0], s[0][1] - line.p0[1]) < 20 && Math.hypot(s[4][0] - line.p1[0], s[4][1] - line.p1[1]) < 20)
        || (Math.hypot(s[4][0] - line.p0[0], s[4][1] - line.p0[1]) < 20 && Math.hypot(s[0][0] - line.p1[0], s[0][1] - line.p1[1]) < 20);
      if (same) { lensCandidates[i].used = true; return i; }
    }
    return undefined;
  }
  function lensAddCandidate(i) {
    var cand = lensCandidates[i];
    if (!cand || cand.used) return;
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
    lensSelected = { line: lensLines.length - 1, point: 4 };
    renderLens();
    lensSnapLine(line);
  }
  function lensReleaseCandidate(line) {
    if (line.candidate !== undefined && lensCandidates[line.candidate]) lensCandidates[line.candidate].used = false;
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
  function lensSnapLine(line) {
    if (!lensCanvas) { lensChanged(); return; }
    var band = lensBand(line.p0, line.p1);
    if (!band) { lensChanged(); return; }
    var sent = JSON.stringify([line.p0, line.p1, line.mids]);
    fetch('/api/wizard/lens/snap', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_width: imageWidth, image_height: imageHeight, p0: line.p0, p1: line.p1, band: band }),
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
  function lensHandle(line, i, j) {
    var g = pointHandle(lensPointPos(line, j), j === 0 || j === 4 ? 7 : 6);
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
    lensCandidates.forEach(function(cand, i) {
      if (cand.used) return;
      var group = svgEl('g');
      group.setAttribute('class', 'lens-candidate');
      group.dataset.candidate = i;
      var hit = svgEl('polyline');
      hit.setAttribute('class', 'lens-candidate-hit');
      hit.setAttribute('points', lensPointsAttr(cand.points));
      group.appendChild(hit);
      var line = svgEl('polyline');
      line.setAttribute('class', 'lens-candidate-line');
      line.setAttribute('points', lensPointsAttr(cand.points));
      group.appendChild(line);
      g.appendChild(group);
    });
  }
  function renderLens() {
    if (!lensEnabled()) return;
    renderLensCandidates();
    var g = document.getElementById('lens-lines');
    // Rebuilding the handles drops a focused one, which would leave the keys dead after a click.
    var hadFocus = g.contains(document.activeElement);
    g.innerHTML = '';
    lensLines.forEach(function(line, i) {
      var group = svgEl('g');
      group.setAttribute('class', 'lens-line' + (line.misfit ? ' misfit' : '') + (lensSelected && lensSelected.line === i ? ' selected' : ''));
      group.dataset.line = i;
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
      var label = svgEl('text');
      label.setAttribute('class', 'lens-label');
      label.setAttribute('x', line.p0[0] + 10);
      label.setAttribute('y', line.p0[1] - 10);
      label.textContent = String(i + 1);
      group.appendChild(label);
      for (var j = 0; j < 5; j++) group.appendChild(lensHandle(line, i, j));
      g.appendChild(group);
    });
    var pending = document.getElementById('lens-pending');
    pending.innerHTML = '';
    if (lensPending) {
      var c = svgEl('circle');
      c.setAttribute('class', 'lens-pending');
      c.setAttribute('cx', lensPending[0]);
      c.setAttribute('cy', lensPending[1]);
      c.setAttribute('r', '7');
      pending.appendChild(c);
    }
    renderLensToolbar();
    renderLensMisfit();
    if (hadFocus) lensFocusSelected();
  }
  function lensFocusSelected() {
    if (!lensSelected) return;
    var h = document.querySelector('.lens-point[data-line="' + lensSelected.line + '"][data-point="' + lensSelected.point + '"]');
    if (h) h.focus({ preventScroll: true });
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
    group.querySelector('.lens-chord').setAttribute('points', lensPointsAttr(lensActivePoints(line)));
    var label = group.querySelector('.lens-label');
    label.setAttribute('x', line.p0[0] + 10);
    label.setAttribute('y', line.p0[1] - 10);
  }
  function renderLensToolbar() {
    var toggle = document.getElementById('lens-toggle-point');
    var del = document.getElementById('lens-delete-line');
    var clear = document.getElementById('lens-clear');
    if (!toggle) return;
    var mid = lensSelected && lensSelected.point > 0 && lensSelected.point < 4 ? lensLines[lensSelected.line] : null;
    toggle.disabled = !mid;
    toggle.textContent = mid && !mid.mids[lensSelected.point - 1].on ? 'Point on' : 'Point off';
    del.disabled = !lensSelected;
    clear.disabled = !lensLines.length;
  }
  function renderLensMisfit() {
    var el = document.getElementById('lens-misfit');
    if (!el) return;
    var bad = [];
    lensLines.forEach(function(line, i) { if (line.misfit) bad.push(i + 1); });
    el.style.display = bad.length ? '' : 'none';
    el.textContent = bad.length ? 'Line ' + bad.join(', ') + ': ' + LENS_MISFIT_TEXT : '';
  }
  function renderLensMeter(meter, rating) {
    if (!meter) return;
    meter.dataset.level = LENS_RATING_LEVEL[rating] || 'caution';
    var n = LENS_RATING_SEGMENTS[rating] || 0;
    meter.querySelectorAll('.wizard-meter-seg').forEach(function(seg, i) { seg.classList.toggle('filled', i < n); });
  }
  function renderLensResult() {
    var box = document.getElementById('lens-result');
    if (!box) return;
    if (!lensFit) { box.style.display = 'none'; return; }
    var level = LENS_RATING_LEVEL[lensFit.rating] || 'caution';
    box.style.display = '';
    box.className = 'notice' + (level === 'caution' ? ' warning' : level === 'success' ? ' success' : '');
    renderLensMeter(box.querySelector('.wizard-meter'), lensFit.rating);
    var text = 'Coverage ' + (LENS_RATING_LABEL[lensFit.rating] || lensFit.rating).toLowerCase();
    if (lensFitApplies(lensFit)) {
      text += ' · Barrel / fisheye ' + Number(lensFit.k1).toFixed(3)
        + ' · Edge fit ' + (lensFit.k2_fitted ? Number(lensFit.k2).toFixed(3) : 'not measured');
    } else {
      text += ' · Not applied until the coverage improves';
    }
    document.getElementById('lens-result-text').textContent = text;
    document.getElementById('lens-result-hint').textContent = lensFit.hint || '';
  }

  function lensFitApplies(fit) { return fit.rating !== 'low'; }
  // Whether the pair is the one the fit wrote (it writes four decimals).
  function lensPairIsTheFits(k1, k2) {
    return !!lensFit && lensFitApplies(lensFit)
      && Math.abs(k1 - Number(Number(lensFit.k1).toFixed(4))) < 1e-9
      && Math.abs(k2 - Number(Number(lensFit.k2).toFixed(4))) < 1e-9;
  }

  // ---- edits ----
  function lensMovePoint(line, j, pos) {
    if (j === 0) line.p0 = pos;
    else if (j === 4) line.p1 = pos;
    else lensSetMidFromPos(line, j, pos);
  }
  function lensChanged() {
    renderLens();
    saveToSession();
    scheduleLensFit();
  }
  window.lensToggleSelectedPoint = function() {
    if (!lensSelected || lensSelected.point === 0 || lensSelected.point === 4) return;
    var m = lensLines[lensSelected.line].mids[lensSelected.point - 1];
    m.on = !m.on;
    lensChanged();
  };
  window.lensDeleteSelectedLine = function() {
    if (!lensSelected) return;
    lensReleaseCandidate(lensLines[lensSelected.line]);
    lensLines.splice(lensSelected.line, 1);
    lensSelected = null;
    lensChanged();
  };
  window.lensClearLines = function() {
    lensLines.forEach(lensReleaseCandidate);
    lensLines = [];
    lensSelected = null;
    lensPending = null;
    lensShowNotice('');
    lensChanged();
  };

  // ---- fit ----
  function scheduleLensFit() {
    clearTimeout(lensFitTimer);
    lensFitTimer = setTimeout(runLensFit, LENS_FIT_DEBOUNCE_MS);
  }
  function runLensFit() {
    var seq = ++lensFitSeq;
    var lines = lensLines.filter(lensLineCounts);
    lensLines.forEach(function(l) { l.curve = null; l.rms = null; l.misfit = false; });
    if (!lines.length) {
      lensFit = null;
      lensShowStatus('', true);
      renderLensResult();
      renderLens();
      saveToSession();
      return;
    }
    fetch('/api/wizard/lens/fit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        image_width: imageWidth,
        image_height: imageHeight,
        lines: lines.map(function(l) { return { points: lensActivePoints(l) }; }),
      }),
    }).then(function(r) {
      return r.json().then(function(data) { return { ok: r.ok, data: data }; });
    }).then(function(res) {
      if (seq !== lensFitSeq) return;
      if (!res.ok || !res.data || res.data.error) {
        lensShowStatus((res.data && res.data.error) || 'Could not fit the lens.', false);
        return;
      }
      lensShowStatus('', true);
      lensFit = res.data;
      lines.forEach(function(l, i) {
        var r = res.data.lines[i];
        l.curve = r.curve;
        l.rms = r.rms_px;
        l.misfit = !!r.misfit;
      });
      // A Low fit says what the lines cannot tell: it is shown, and the pair stays as it was.
      if (lensFitApplies(lensFit)) {
        wizWriteLensCoeff('wiz_lens_k1', Number(res.data.k1).toFixed(4));
        wizWriteLensCoeff('wiz_lens_k2', Number(res.data.k2).toFixed(4));
        var err = document.getElementById('wiz-lens-error');
        if (err) err.style.display = 'none';
      }
      renderLensResult();
      renderLens();
      if (lensFitApplies(lensFit)) onLensCoeffChanged();
      else saveToSession();
    }).catch(function() {
      if (seq === lensFitSeq) lensShowStatus('Could not fit the lens.', false);
    });
  }

  // The curve a lens pair predicts for a line: the straight line through the
  // undistorted active points, bowed again. Drawn live while a slider moves.
  function lensPredictCurve(line, k1, k2) {
    var pts = lensActivePoints(line);
    if (pts.length < 2 || !wizLensIsValid(k1, k2)) return null;
    var u = pts.map(function(p) { return wizInvertDistortion(p, k1, k2); });
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
      out.push(wizApplyDistortion([cx + t * dx, cy + t * dy], k1, k2));
    }
    return out;
  }
  function lensRecomputeCurves() {
    if (!lensEnabled()) return;
    var k1 = wizReadLensCoeff('wiz_lens_k1'), k2 = wizReadLensCoeff('wiz_lens_k2');
    lensLines.forEach(function(line) { line.curve = lensLineCounts(line) ? lensPredictCurve(line, k1, k2) : null; });
  }

  // A new lens pair, from the fit or a slider: solve the pose again from the
  // pins when the session still has them; otherwise Review says to re-pin.
  function onLensCoeffChanged() {
    saveToSession();
    if (!pinnedCorners || !wizLensPairValid()) return;
    clearTimeout(lensReSolveTimer);
    lensReSolveTimer = setTimeout(solveFromPinnedCorners, LENS_FIT_DEBOUNCE_MS);
  }
  // Pins taken on a snapshot of another size are in other pixels: they solve nothing.
  function solveFromPinnedCorners() {
    var pins = pinnedCorners;
    if (!pins || pins.size[0] !== imageWidth || pins.size[1] !== imageHeight) return;
    var seq = ++poseSeq;
    var body = wizardSolveBody(pins.corners);
    fetch('/api/wizard/solve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(function(r) { return r.json(); })
    .then(function(data) {
      if (seq !== poseSeq || data.error) return;
      applySolvedPose(data.camera, pins.corners, body);
      if (currentStep !== WIZ.lens) projectAndOverlay();
    }).catch(function() {});
  }
  // The pose was solved through another lens pair: a solve from the pins that
  // failed, is still running or had no pins to start from leaves Review's caution up.
  function lensChangedSinceSolve() {
    var k1 = wizReadLensCoeff('wiz_lens_k1'), k2 = wizReadLensCoeff('wiz_lens_k2');
    return Math.abs(k1 - lensSolvedWith.k1) > 1e-9 || Math.abs(k2 - lensSolvedWith.k2) > 1e-9;
  }
  function populateReviewLens(cam) {
    var k1El = document.getElementById('review-lens-k1');
    if (!k1El) return;
    k1El.textContent = Number(cam.lens_k1).toFixed(3);
    document.getElementById('review-lens-k2').textContent = Number(cam.lens_k2).toFixed(3);
    var meter = document.querySelector('#review-lens-rating .wizard-meter');
    var text = document.getElementById('review-lens-rating-text');
    // The rating describes the fit's own pair, not one fine-tuned away from it.
    if (lensPairIsTheFits(Number(cam.lens_k1), Number(cam.lens_k2))) {
      renderLensMeter(meter, lensFit.rating);
      meter.style.display = '';
      text.textContent = LENS_RATING_LABEL[lensFit.rating] || lensFit.rating;
    } else {
      meter.style.display = 'none';
      text.textContent = lensFit && lensFitApplies(lensFit) ? 'Fine-tuned' : 'Not measured';
    }
    document.getElementById('review-lens-caution').style.display = lensChangedSinceSolve() ? '' : 'none';
  }

  // ---- pointer + keyboard ----
  function lensInit() {
    if (!lensEnabled()) return;
    var overlay = document.getElementById('lens-overlay');
    overlay.addEventListener('pointerdown', function(e) {
      var handle = e.target.closest ? e.target.closest('.lens-point') : null;
      var candidate = e.target.closest ? e.target.closest('.lens-candidate') : null;
      var pt = lensSvgPoint(e.clientX, e.clientY);
      if (candidate) {
        e.preventDefault();
        lensAddCandidate(+candidate.dataset.candidate);
      } else if (handle) {
        e.preventDefault();
        lensSelected = { line: +handle.dataset.line, point: +handle.dataset.point };
        lensDrag = { line: lensLines[lensSelected.line], point: lensSelected.point, client: [e.clientX, e.clientY], moved: false };
        overlay.setPointerCapture(e.pointerId);
        renderLens();
        lensFocusSelected();
        showLoupe('lens-container', 'lens-overlay', lensPointPos(lensDrag.line, lensDrag.point));
      } else if (e.target.id === 'lens-hit') {
        e.preventDefault();
        lensDrag = { trace: true, client: [e.clientX, e.clientY], moved: false };
        overlay.setPointerCapture(e.pointerId);
      }
    });
    overlay.addEventListener('pointermove', function(e) {
      if (!lensDrag) return;
      var pt = lensSvgPoint(e.clientX, e.clientY);
      if (Math.hypot(e.clientX - lensDrag.client[0], e.clientY - lensDrag.client[1]) > LENS_TAP_SLOP_PX) lensDrag.moved = true;
      if (lensDrag.trace) return;
      e.preventDefault();
      lensMovePoint(lensDrag.line, lensDrag.point, lensClamp(pt));
      lensUpdateLineGeometry(lensLines.indexOf(lensDrag.line));
      showLoupe('lens-container', 'lens-overlay', lensPointPos(lensDrag.line, lensDrag.point));
    });
    function endDrag(e) {
      if (!lensDrag) return;
      var drag = lensDrag;
      lensDrag = null;
      hideLoupe('lens-container');
      try { overlay.releasePointerCapture(e.pointerId); } catch (err) {}
      if (drag.trace) {
        if (!drag.moved && e.type !== 'pointercancel') lensTraceClick(lensSvgPoint(e.clientX, e.clientY));
        return;
      }
      if (drag.moved) lensChanged(); else renderLens();
    }
    overlay.addEventListener('pointerup', endDrag);
    overlay.addEventListener('pointercancel', endDrag);
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
      else if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); lensDeleteSelectedLine(); return; }
      else if (e.key === ' ') { e.preventDefault(); lensToggleSelectedPoint(); return; }
      else return;
      e.preventDefault();
      var pos = lensPointPos(line, j);
      if (j === 0 || j === 4) {
        lensMovePoint(line, j, lensClamp([pos[0] + dx, pos[1] + dy]));
      } else {
        // A middle point only moves across the line: keep the nudge's perpendicular part.
        var f = lensFrame(line);
        line.mids[j - 1].off += dx * f.nx + dy * f.ny;
      }
      lensUpdateLineGeometry(i);
      saveToSession();
      scheduleLensFit();
    });
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape' && lensPending && currentStep === WIZ.lens) {
        lensPending = null;
        renderLens();
      }
    });
    var err = document.getElementById('wiz-lens-error');
    if (err) err.style.display = wizLensPairValid() ? 'none' : 'block';
    renderLens();
    renderLensResult();
  }

  // ---------------------------------------------------------------
  // Init
  // ---------------------------------------------------------------
  populateSensorDropdown();
  (function initLensHelper() {
    var sel = document.getElementById('cam_sensor');
    var initialSw = parseFloat(document.getElementById('cam_sensor_width_initial').value);
    if (sel && isFinite(initialSw) && initialSw > 0) {
      // Pick the preset matching the stored width (within 0.05mm), else 'custom'.
      var match = SENSOR_SIZES.find(function(s) {
        return s.width_mm && Math.abs(s.width_mm - initialSw) < 0.05;
      });
      if (match) {
        sel.value = match.id;
      } else {
        sel.value = 'custom';
        document.getElementById('cam_sensor_custom').value = initialSw;
        document.getElementById('cam_sensor_custom_field').style.display = '';
      }
    }
    // If no preset+focal → helper is already stale against the server-injected
    // FOV. Don't dim on load; let the user opt in by editing the helper.
  })();
  var restored = restoreFromSession();
  // Default Y Offset to half the depth so REF sits at center of downstage edge
  if (!restored) {
    var yOff = wizReadLen('grid_y_offset') || 0;
    if (yOff === 0) {
      var initDepth = wizReadLen('grid_depth') || 0;
      if (initDepth > 0) {
        wizWriteLen('grid_y_offset', initDepth / 2);
      }
    }
  }
  preCoarsePose = snapshotPose();
  updatePrepIllustration();
  updateGridIllustration();
  updateCamIllustration();
  // Bind every drag handler once at page load. The SVG groups outlive every
  // re-render (only their children are drawn again), and a handler bound per
  // render would act once for every projection so far.
  setupFineZoomDragging();
  setupCoarseZoomDragging();
  setupRefDragging(document.getElementById('coarse-ref'));
  setupCornerDragging(document.getElementById('fine-corners'));
  lensInit();
  wizardGo(restored ? currentStep : 0);
})();
</script>
