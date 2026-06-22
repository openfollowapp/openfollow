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
    color: var(--ok);
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
  .wizard-action-required {
    margin-bottom: 12px;
    padding: 10px 12px;
    border-radius: 0.8rem;
    border: 1px solid rgba(255,188,0,0.35);
    background: rgba(255,188,0,0.13);
    color: #ffe1a2;
    font-weight: 600;
    font-size: 0.88rem;
  }
  .wizard-tip {
    color: var(--muted);
    font-size: 0.82rem;
    font-style: italic;
    margin-top: 0.5rem;
  }
  .wizard-field-error {
    color: var(--danger);
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
  .wizard-preview-container.valid { border-color: rgba(125,229,159,0.6); }
  .wizard-preview-container.invalid { border-color: rgba(255,140,140,0.6); }
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
  .wizard-status.ok { color: var(--ok); }
  .wizard-status.error { color: var(--danger); }
  @media (max-width: 680px) {
    .wizard-step-btn { padding: 0.35rem 0.5rem; font-size: 0.72rem; }
  }
</style>

<div class="wizard-header">
  <a href="/">&larr; {{_("Back to Settings")}}</a>
  <h2>{{_("Setup Wizard")}}</h2>
</div>

<nav class="wizard-steps" aria-label="Setup wizard steps">
  <button type="button" class="wizard-step-btn" data-step="0" onclick="wizardGo(0)">1. {{_("Preparation")}}</button>
  <button type="button" class="wizard-step-btn" data-step="1" onclick="wizardGo(1)">2. {{_("Grid Setup")}}</button>
  <button type="button" class="wizard-step-btn" data-step="2" onclick="wizardGo(2)">3. {{_("Video Source")}}</button>
  <button type="button" class="wizard-step-btn" data-step="3" onclick="wizardGo(3)">4. {{_("Camera Position")}}</button>
  <button type="button" class="wizard-step-btn" data-step="4" onclick="wizardGo(4)">5. {{_("Reference Mapping")}}</button>
  <button type="button" class="wizard-step-btn" data-step="5" onclick="wizardGo(5)">6. {{_("Corner Pinning")}}</button>
  <button type="button" class="wizard-step-btn" data-step="6" onclick="wizardGo(6)">7. {{_("Review")}}</button>
</nav>

<!-- Step 1: Preparation -->
<div class="wizard-content" id="wizard-step-0">
  <div class="section">
    <div class="section-head">
      <h2>{{_("Preparation")}}</h2>
      <span class="section-note">{{_("Understand the setup and prepare the physical space")}}</span>
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
      <line id="pp-x-line" stroke="rgba(255,140,140,0.4)" stroke-width="1" stroke-dasharray="4,3" style="display:none"/>
      <text id="pp-x-label" fill="rgba(255,140,140,0.6)" font-size="9" font-weight="600" style="display:none"></text>
      <line id="pp-y-line" stroke="rgba(125,229,159,0.4)" stroke-width="1" stroke-dasharray="4,3"/>
      <text id="pp-y-label" fill="rgba(125,229,159,0.6)" font-size="9" font-weight="600"></text>
      <!-- Z axis line (camera floor to camera) -->
      <line id="pp-z-line" stroke="rgba(159,201,255,0.5)" stroke-width="1.5" stroke-dasharray="4,3"/>
      <text id="pp-z-label" fill="rgba(159,201,255,0.6)" font-size="9" font-weight="600" style="display:none"></text>
      <circle id="pp-floor-dot" r="3" fill="none" stroke="rgba(159,201,255,0.5)" stroke-width="1" style="display:none"/>
      <!-- Camera body -->
      <polygon id="pp-cam-body" fill="rgba(255,255,255,0.12)" stroke="rgba(255,255,255,0.4)" stroke-width="1"/>
      <polygon id="pp-cam-lens" fill="rgba(255,255,255,0.2)" stroke="rgba(255,255,255,0.5)" stroke-width="1"/>
      <text id="pp-cam-label" fill="rgba(247,245,233,0.68)" font-size="10" font-weight="600">Camera</text>
      <!-- DS / US labels -->
      <text id="pp-ds-label" fill="rgba(247,245,233,0.3)" font-size="9" font-weight="600" text-anchor="middle">DOWNSTAGE</text>
      <text id="pp-us-label" fill="rgba(247,245,233,0.3)" font-size="9" font-weight="600" text-anchor="middle">UPSTAGE</text>
      <!-- PSN axis indicator -->
      <defs>
        <marker id="ppArrowR" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6" fill="rgba(255,140,140,0.8)"/></marker>
        <marker id="ppArrowG" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6" fill="rgba(125,229,159,0.8)"/></marker>
        <marker id="ppArrowB" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6" fill="rgba(159,201,255,0.8)"/></marker>
      </defs>
      <line id="pp-axis-x" stroke="rgba(255,140,140,0.8)" stroke-width="1.5" marker-end="url(#ppArrowR)"/>
      <text id="pp-axis-x-label" fill="rgba(255,140,140,0.8)" font-size="10" font-weight="600">X+</text>
      <line id="pp-axis-y" stroke="rgba(125,229,159,0.8)" stroke-width="1.5" marker-end="url(#ppArrowG)"/>
      <text id="pp-axis-y-label" fill="rgba(125,229,159,0.8)" font-size="10" font-weight="600">Y+</text>
      <line id="pp-axis-z" stroke="rgba(159,201,255,0.8)" stroke-width="1.5" marker-end="url(#ppArrowB)"/>
      <text id="pp-axis-z-label" fill="rgba(159,201,255,0.8)" font-size="10" font-weight="600">Z+</text>
    </svg>

    <div class="wizard-help">
      <strong>{{_("Reference Point:")}}</strong> {{_("All measurements in this wizard are relative to a single Reference Point. This should be the same reference point for all show control systems that you plan to connect to OpenFollow. If your venue has a defined origin point, you should use that. If you do not have an existing reference point, we recommend the")}} <strong>{{_("center of the stage front edge")}}</strong> {{_("as the default.")}}
    </div>
    <div class="wizard-help">
      <strong>{{_("The Grid:")}}</strong> {{_("The grid area must be a")}} <strong>{{_("right-angled rectangle")}}</strong> {{_("as large as possible that lies flat on the main performance level. All four corners must be")}} <strong>{{_("clearly visible in the camera feed")}}</strong>. {{_("Do not worry, you can follow performers outside this grid, as long as they are in the area visible to the camera.")}}
    </div>
    <div class="wizard-action-required">
      {{_("Before proceeding, physically mark the Reference Point and the four grid corners on the stage with visible markers, so they are easily visible on camera (tape, gaffer marks, etc.).")}}
    </div>
    <p class="wizard-tip">{{_("Take your time with accurate markings - the precision of the final calibration depends on them.")}}</p>

    <div class="wizard-nav">
      <span class="spacer"></span>
      <button type="button" class="save-btn" onclick="wizardGo(1)">{{_("Next")}}</button>
    </div>
  </div>
</div>

<!-- Step 2: Grid Setup -->
<div class="wizard-content" id="wizard-step-1">
  <div class="section">
    <div class="section-head">
      <h2>{{_("Grid Setup")}}</h2>
      <span class="section-note">{{_("Dimensions and position of the rectangular tracking area")}}</span>
    </div>

    <svg id="grid-setup-svg" class="wizard-illustration" viewBox="0 0 580 400" style="max-width:580px;" xmlns="http://www.w3.org/2000/svg">
      <!-- Stage floor outline -->
      <rect id="gs-stage" x="40" y="30" width="400" height="250" rx="4" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="1" stroke-dasharray="4,4"/>
      <!-- Grid rectangle (dynamic) -->
      <g id="gs-grid-lines"></g>
      <rect id="gs-grid" rx="2" fill="rgba(255,188,0,0.06)" stroke="rgba(255,188,0,0.5)" stroke-width="1.5"/>
      <!-- Diagonal line (dynamic) -->
      <line id="gs-diag" stroke="rgba(159,201,255,0.4)" stroke-width="1" stroke-dasharray="6,4"/>
      <text id="gs-diag-text" fill="rgba(159,201,255,0.7)" font-size="10" font-weight="600" text-anchor="middle"></text>
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
      <line id="gs-off-x" stroke="rgba(255,140,140,0.6)" stroke-width="1.5" marker-end="url(#arrowR)"/>
      <text id="gs-off-x-label" fill="rgba(255,140,140,0.8)" font-size="9" font-weight="600"></text>
      <line id="gs-off-y" stroke="rgba(125,229,159,0.6)" stroke-width="1.5" marker-end="url(#arrowG)"/>
      <text id="gs-off-y-label" fill="rgba(125,229,159,0.8)" font-size="9" font-weight="600"></text>
      <!-- Z offset label (shown when z_offset != 0) -->
      <text id="gs-off-z-label" fill="rgba(159,201,255,0.8)" font-size="9" font-weight="600"></text>
      <!-- Downstage / Upstage labels -->
      <text id="gs-ds-label" fill="rgba(247,245,233,0.35)" font-size="9" font-weight="600" text-anchor="middle">DOWNSTAGE</text>
      <text id="gs-us-label" fill="rgba(247,245,233,0.35)" font-size="9" font-weight="600" text-anchor="middle">UPSTAGE</text>
      <defs>
        <marker id="arrowR" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6" fill="rgba(255,140,140,0.8)"/></marker>
        <marker id="arrowG" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6" fill="rgba(125,229,159,0.8)"/></marker>
      </defs>
    </svg>

    <div class="group">
      <div class="group-title">{{_("Dimensions")}}</div>
      <p class="wizard-help">{{_("Enter the width and depth of the rectangular tracking area you marked on stage.")}}</p>
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
          <span class="param-label">{{_("Diagonal")}}</span>
          <span class="param-value" id="grid-diagonal">-</span>
        </div>
      </div>
      <p class="wizard-tip">{{_("Verify your grid is rectangular: measure both diagonals on stage - they must be equal. The expected diagonal is shown above.")}}</p>
    </div>

    <div class="group">
      <div class="group-title">{{_("Alignment")}}</div>
      <p class="wizard-help">{{_("Define where the Reference Point sits within the grid. By default, it is at the center of the front edge (X Offset = 0, Y Offset = depth / 2).")}}</p>
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

    <p class="wizard-tip">{{_("Measure between the marks you placed on stage. Precision matters - even 10cm off will affect tracking accuracy. Spacing controls the visual grid density (does not affect calibration). If your Reference Point is 1m left of the grid center: set X Offset = -1.")}}</p>

    <div style="margin-top:0.72rem;display:flex;gap:0.5rem;flex-wrap:wrap;">
      <button type="button" class="secondary" onclick="resetGridToDefaults()">{{_("Load Defaults")}}</button>
      <button type="button" class="secondary" onclick="restoreGridToLast()">{{_("Restore Last")}}</button>
    </div>

    <div class="wizard-nav">
      <button type="button" class="secondary" onclick="wizardGo(0)">{{_("Back")}}</button>
      <button type="button" class="save-btn" onclick="wizardGo(2)">{{_("Next")}}</button>
    </div>
  </div>
</div>

<!-- Step 3: Video Source -->
<div class="wizard-content" id="wizard-step-2">
  <div class="section">
    <div class="section-head">
      <h2>{{_("Video Source")}}</h2>
      <span class="section-note">{{_("Select and configure the camera input")}}</span>
    </div>

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
      {{_("Choose the video input that matches your camera setup. The change applies live - the new pipeline starts within ~1 s of saving.")}}
    </p>

    <div id="wizard-video-saved" style="display:none;margin-bottom:0.72rem;padding:10px 12px;border-radius:0.8rem;border:1px solid rgba(125,229,159,0.4);background:rgba(125,229,159,0.1);color:var(--ok);font-weight:600;font-size:0.88rem;"></div>

    <div class="wizard-nav">
      <button type="button" class="secondary" onclick="wizardGo(1)">{{_("Back")}}</button>
      <span class="spacer"></span>
      <button type="button" class="secondary" onclick="saveWizardVideoSource()">{{_("Save")}}</button>
      <button type="button" class="save-btn" onclick="wizardGo(3)">{{_("Next")}}</button>
    </div>
  </div>
</div>

<!-- Step 4: Camera Extrinsics -->
<div class="wizard-content" id="wizard-step-3">
  <div class="section">
    <div class="section-head">
      <h2>{{_("Camera Position")}}</h2>
      <span class="section-note">{{_("Physical camera position and orientation relative to the Reference Point")}}</span>
    </div>

    <svg id="cam-pos-svg" class="wizard-illustration" viewBox="0 0 580 400" style="max-width:725px;" xmlns="http://www.w3.org/2000/svg">
      <!-- Grid quad (isometric) -->
      <polygon id="cp-grid" fill="rgba(255,188,0,0.06)" stroke="rgba(255,188,0,0.5)" stroke-width="1.5"/>
      <g id="cp-grid-lines"></g>
      <!-- Corner labels -->
      <text id="cp-label-dsl" fill="rgba(247,245,233,0.5)" font-size="9" font-weight="600">DSL</text>
      <text id="cp-label-dsr" fill="rgba(247,245,233,0.5)" font-size="9" font-weight="600">DSR</text>
      <text id="cp-label-usr" fill="rgba(247,245,233,0.5)" font-size="9" font-weight="600">USR</text>
      <text id="cp-label-usl" fill="rgba(247,245,233,0.5)" font-size="9" font-weight="600">USL</text>
      <!-- Grid Z offset indicator (ref ground to grid plane) -->
      <line id="cp-gz-line" stroke="rgba(159,201,255,0.5)" stroke-width="1" stroke-dasharray="4,3"/>
      <text id="cp-gz-label" fill="rgba(159,201,255,0.8)" font-size="9" font-weight="600"></text>
      <circle id="cp-gz-dot" r="3" fill="rgba(159,201,255,0.4)"/>
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
      <line id="cp-x-line" stroke="rgba(255,140,140,0.5)" stroke-width="1.5"/>
      <text id="cp-x-label" fill="rgba(255,140,140,0.8)" font-size="9" font-weight="600"></text>
      <!-- Y measurement line (ref+X to cam floor) -->
      <line id="cp-y-line" stroke="rgba(125,229,159,0.5)" stroke-width="1.5"/>
      <text id="cp-y-label" fill="rgba(125,229,159,0.8)" font-size="9" font-weight="600"></text>
      <!-- Vertical pole (Z height) -->
      <line id="cp-z-line" stroke="rgba(159,201,255,0.5)" stroke-width="1" stroke-dasharray="4,3"/>
      <text id="cp-z-label" fill="rgba(159,201,255,0.8)" font-size="9" font-weight="600"></text>
      <!-- Floor projection dot -->
      <circle id="cp-floor-dot" r="3" fill="rgba(159,201,255,0.4)"/>
      <!-- Dashed line from camera to ref -->
      <line id="cp-sight-line" stroke="rgba(255,255,255,0.15)" stroke-width="1" stroke-dasharray="4,4"/>
      <!-- FOV cone lines -->
      <line id="cp-fov-left" stroke="rgba(255,188,0,0.3)" stroke-width="1" stroke-dasharray="3,3"/>
      <line id="cp-fov-right" stroke="rgba(255,188,0,0.3)" stroke-width="1" stroke-dasharray="3,3"/>
      <line id="cp-fov-line" stroke="rgba(255,255,255,0.35)" stroke-width="1"/>
      <text id="cp-fov-label" fill="rgba(255,188,0,0.6)" font-size="9" font-weight="600" text-anchor="middle"></text>
      <!-- Axis labels -->
      <text id="cp-ds-label" fill="rgba(247,245,233,0.3)" font-size="9" font-weight="600" text-anchor="middle">DOWNSTAGE</text>
      <text id="cp-us-label" fill="rgba(247,245,233,0.3)" font-size="9" font-weight="600" text-anchor="middle">UPSTAGE</text>
    </svg>

    <div class="group">
      <div class="group-title">{{_("Position")}}</div>
      <p class="wizard-help">{{_("Enter the camera physical position relative to the Reference Point, in")}} {{_len}}. {{_("These use PSN theatrical coordinates: X = stage left, Y = upstage, Z = up.")}}</p>
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
      <div class="group-title">{{_("Orientation")}}</div>
      <p class="wizard-help">{{_("Enter the camera angle. If the camera points straight at the stage, start with Pitch approx -30 degrees (looking down), Yaw = 0 degrees, Roll = 0 degrees.")}}</p>
      <div class="row">
        <div class="field">
          <label for="cam_pitch">Pitch <span style="font-weight:400;text-transform:none;letter-spacing:0;">(down &minus;)</span></label>
          <input type="number" step="0.1" min="-90" max="-0.5" id="cam_pitch" value="{{config.camera.pitch}}" oninput="onCamInputChanged()">
          <div id="cam-pitch-error" class="wizard-field-error" style="display:none;">{{_("Pitch must be between -0.5 degrees and -90 degrees")}}</div>
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
      <div class="group-title">{{_("Lens")}}</div>
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
      <p class="wizard-tip">{{_("Enter the horizontal FOV from your camera datasheet, or pick your sensor size and focal length and we will compute it.")}}</p>
    </div>

    <div style="margin-top:0.72rem;display:flex;gap:0.5rem;flex-wrap:wrap;">
      <button type="button" class="secondary" onclick="resetCameraToDefaults()">{{_("Load Defaults")}}</button>
      <button type="button" class="secondary" onclick="restoreCameraToLast()">{{_("Restore Last")}}</button>
    </div>

    <div class="wizard-nav">
      <button type="button" class="secondary" onclick="wizardGo(2)">{{_("Back")}}</button>
      <button type="button" class="save-btn" onclick="wizardGo(4)">{{_("Next")}}</button>
    </div>
  </div>
</div>

<!-- Step 5: Coarse Calibration (Reference Mapping) -->
<div class="wizard-content" id="wizard-step-4">
  <div class="section">
    <div class="section-head">
      <h2>{{_("Reference Mapping")}}</h2>
      <span class="section-note">{{_("Drag the crosshair to the physical Reference Point mark")}}</span>
    </div>

    <div id="coarse-container" class="wizard-preview-container" style="display:none;">
      <img id="coarse-image" alt="Camera snapshot">
      <svg id="coarse-overlay" class="wizard-overlay" xmlns="http://www.w3.org/2000/svg">
        <polygon id="coarse-quad" fill="rgba(255,188,0,0.06)" stroke="rgba(255,188,0,0.5)" stroke-width="2" points="0,0"/>
        <g id="coarse-zoff"></g>
        <g id="coarse-corners"></g>
        <g id="coarse-ref"></g>
      </svg>
    </div>
    <div id="coarse-no-feed" class="wizard-no-feed" style="display:none;">{{_("No video feed available. Configure a video source in Step 3, then return here and press")}} <strong>{{_("Refresh Image")}}</strong>.</div>

    <div id="coarse-status" class="wizard-status" style="display:none;"></div>

    <div style="margin-top:0.72rem;display:flex;gap:0.5rem;flex-wrap:wrap;">
      <button type="button" class="secondary" onclick="loadSnapshot()">{{_("Refresh Image")}}</button>
      <button type="button" class="secondary" onclick="resetCoarseCalibration()">{{_("Reset")}}</button>
    </div>

    <p class="wizard-help" style="margin-top:0.72rem;">
      {{_("Drag the crosshair marker to the physical Reference Point mark visible on the stage. All corners move together with the reference point to roughly align the overlay with your stage.")}}
    </p>
    <p class="wizard-tip">{{_("Zoom in on your browser (Ctrl/Cmd + scroll) for more precision. You can also click the crosshair and use arrow keys to nudge it precisely (hold Shift for larger steps).")}}</p>

    <div class="wizard-nav">
      <button type="button" class="secondary" onclick="wizardGo(3)">{{_("Back")}}</button>
      <button type="button" class="save-btn" onclick="wizardGo(5)">{{_("Next")}}</button>
    </div>
  </div>
</div>

<!-- Step 6: Fine Calibration (Corner Pinning) -->
<div class="wizard-content" id="wizard-step-5">
  <div class="section">
    <div class="section-head">
      <h2>{{_("Corner Pinning")}}</h2>
      <span class="section-note">{{_("Drag each corner to its physical stage mark for precise calibration")}}</span>
    </div>

    <div id="fine-container" class="wizard-preview-container" style="display:none;">
      <!-- Full-image view (default). -->
      <div id="fine-full-view">
        <img id="fine-image" alt="Camera snapshot">
        <svg id="fine-overlay" class="wizard-overlay" xmlns="http://www.w3.org/2000/svg">
          <polygon id="fine-quad" fill="rgba(255,188,0,0.06)" stroke="rgba(255,188,0,0.5)" stroke-width="2" points="0,0"/>
          <g id="fine-corners"></g>
        </svg>
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
    <div id="fine-no-feed" class="wizard-no-feed" style="display:none;">{{_("No video feed available. Configure a video source in Step 3, then return here and press")}} <strong>{{_("Refresh Image")}}</strong>.</div>

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
      <button type="button" class="secondary" onclick="loadSnapshot()">{{_("Refresh Image")}}</button>
      <button type="button" class="secondary" onclick="resetCornerPinning()">{{_("Reset Corners")}}</button>
      <!-- Fine adjust toggle. Disabled until snapshot loads.
           Label flips between "Fine adjust" and "Show full image". -->
      <button type="button" class="secondary" id="fine-zoom-toggle"
              onclick="toggleFineZoomMode()" disabled
              title="Load a snapshot first">{{_("Fine adjust")}}</button>
    </div>

    <p class="wizard-help" style="margin-top:0.72rem;">
      {{_("Drag each corner marker to its physical mark on the stage. The labels are stage positions: DSL/USL are stage left, DSR/USR are stage right (D = downstage/front, U = upstage/back). With a front-of-house camera you see the audience view, so stage left is on the right of the image (audience right) and stage right is on the left (audience left).")}}
    </p>
    <p class="wizard-tip">{{_("Start with the two downstage corners (front), then adjust the upstage corners (back). If a corner turns red, the shape is invalid. For pixel-precise placement, click")}} <strong>{{_("Fine adjust")}}</strong> {{_("to switch to a 4x-zoomed per-corner view.")}}</p>
    <p>{{_("If your corners are invalid, three things could be wrong: The position of your camera relative to the reference point, your marked rectangle has not the same size as the grid, or the corners of your rectangle are not 90 degrees.")}}</p>
    <p>{{_("Click a corner or use Tab to select it, then use arrow keys to nudge (hold Shift for larger steps).")}}</p>

    <div class="wizard-nav">
      <button type="button" class="secondary" onclick="wizardGo(4)">{{_("Back")}}</button>
      <button type="button" class="save-btn" onclick="wizardGo(6)">{{_("Next")}}</button>
    </div>
  </div>
</div>

<!-- Step 7: Review & Apply -->
<div class="wizard-content" id="wizard-step-6">
  <div class="section">
    <div class="section-head">
      <h2>{{_("Review")}}</h2>
      <span class="section-note">{{_("Verify all values before applying")}}</span>
    </div>

    <div id="review-container" class="wizard-preview-container" style="display:none;">
      <img id="review-image" alt="Camera snapshot">
      <svg id="review-overlay" class="wizard-overlay" xmlns="http://www.w3.org/2000/svg">
        <polygon id="review-quad" fill="rgba(255,188,0,0.06)" stroke="rgba(125,229,159,0.6)" stroke-width="2" points="0,0"/>
        <g id="review-zoff"></g>
        <g id="review-corners"></g>
        <g id="review-ref"></g>
      </svg>
    </div>
    <div id="review-no-feed" class="wizard-no-feed" style="display:none;">{{_("No video feed available. Values below are still valid, but the visual review is unavailable.")}}</div>

    <div id="review-status" class="wizard-status" style="display:none;"></div>

    <div class="group">
      <div class="group-title">{{_("Camera")}}</div>
      <div class="wizard-solved-params" id="review-camera-params">
        <div class="wizard-solved-param"><span class="param-label">Pos X</span><span class="param-value" id="review-cam-pos-x">-</span></div>
        <div class="wizard-solved-param"><span class="param-label">Pos Y</span><span class="param-value" id="review-cam-pos-y">-</span></div>
        <div class="wizard-solved-param"><span class="param-label">Pos Z</span><span class="param-value" id="review-cam-pos-z">-</span></div>
        <div class="wizard-solved-param"><span class="param-label">Pitch</span><span class="param-value" id="review-cam-pitch">-</span></div>
        <div class="wizard-solved-param"><span class="param-label">Yaw</span><span class="param-value" id="review-cam-yaw">-</span></div>
        <div class="wizard-solved-param"><span class="param-label">Roll</span><span class="param-value" id="review-cam-roll">-</span></div>
        <div class="wizard-solved-param"><span class="param-label">FOV</span><span class="param-value" id="review-cam-fov">-</span></div>
      </div>
    </div>

    <div class="group">
      <div class="group-title">{{_("Grid")}}</div>
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
      <button type="button" class="secondary" onclick="wizardGo(5)">{{_("Back")}}</button>
      <span class="spacer"></span>
      <button type="button" class="btn-danger" onclick="discardAndLeave()">{{_("Discard & Leave")}}</button>
      <button type="button" class="save-btn" onclick="applyAndFinish()" id="btn-apply-finish">{{_("Apply & Finish")}}</button>
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
  var solvedCamera = null;
  var originalFov = parseFloat(document.getElementById('cam_fov').value);

  // Camera state before coarse calibration (for reset)
  var preCoarseCamera = null;

  // Camera state before the first solve of the current corner-pinning
  // session. ``solveFromCorners`` writes the solved camera back into
  // the camera form fields on success – without a snapshot, ``Reset
  // Corners`` would re-project from those overwritten values and the
  // corners would visibly stay where the operator dragged them. Reset
  // restores from this snapshot AND clears it so the next solve takes
  // a fresh snapshot for the next session.
  var preCornerPinningCamera = null;

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

  function saveToSession() {
    try {
      var state = getState();
      state._step = currentStep;
      state._solvedCamera = solvedCamera;
      state._originalFov = originalFov;
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
      if (state._solvedCamera) solvedCamera = state._solvedCamera;
      if (state._originalFov) originalFov = state._originalFov;
      if (typeof state._step === 'number') {
        currentStep = state._step;
        return true;
      }
      return false;
    } catch(e) { return false; }
  }

  // ---------------------------------------------------------------
  // Step Navigation
  // ---------------------------------------------------------------
  window.wizardGo = function(step) {
    saveToSession();
    currentStep = step;
    document.querySelectorAll('.wizard-content').forEach(function(el, i) {
      el.classList.toggle('active', i === step);
    });
    document.querySelectorAll('.wizard-step-btn').forEach(function(btn) {
      var s = parseInt(btn.dataset.step);
      btn.classList.toggle('active', s === step);
      btn.setAttribute('aria-current', s === step ? 'step' : 'false');
      if (s < step) btn.classList.add('completed');
    });
    // On entering step 4+ load the snapshot and project
    if (step >= 4) {
      loadSnapshot();
    }
    // On entering preparation step, redraw the illustration
    if (step === 0) {
      updatePrepIllustration();
    }
    // On entering grid setup step, redraw the illustration
    if (step === 1) {
      updateGridIllustration();
    }
    // On entering camera position step, redraw the illustration
    if (step === 3) {
      updateCamIllustration();
    }
    // On entering coarse calibration step, save camera state for reset
    if (step === 4) {
      preCoarseCamera = getState().camera;
    }
    // On entering review step, populate summary
    if (step === 6) {
      populateReview();
    }
    saveToSession();
  };

  window.updateWizardState = function() {
    saveToSession();
  };

  // ---------------------------------------------------------------
  // Preparation Illustration (Step 1 – isometric overview)
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
  // Grid Illustration (Step 4 – dynamic SVG)
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
  // Camera Position Illustration (Step 4 – isometric dynamic SVG)
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
    var rad = Math.PI / 180;
    var pitchR = pitch * rad;
    var yawR = yaw * rad;
    // 3D look direction (absolute angles)
    var lookX = Math.cos(pitchR) * Math.sin(yawR);
    var lookY = Math.cos(pitchR) * Math.cos(yawR);
    var lookZ = Math.sin(pitchR);

    // FOV cone: project a horizontal line at a distance where it intersects grid plane
    // Distance from camera to grid plane (z=goz) along look direction
    var floorDist = ((cz - goz) > 0 && lookZ < 0) ? -(cz - goz) / lookZ : 10;
    var fovHalfRad = (fov / 2) * rad;
    var fovHalfWidth = floorDist * Math.tan(fovHalfRad);
    // FOV target point on floor
    var fovTargetX = cx + lookX * floorDist;
    var fovTargetY = cy + lookY * floorDist;
    // Perpendicular direction on floor (for FOV width)
    var lookFloorLen = Math.sqrt(lookX * lookX + lookY * lookY) || 1;
    var perpX = -lookY / lookFloorLen;
    var perpY = lookX / lookFloorLen;
    var fovLeftX = fovTargetX + perpX * fovHalfWidth;
    var fovLeftY = fovTargetY + perpY * fovHalfWidth;
    var fovRightX = fovTargetX - perpX * fovHalfWidth;
    var fovRightY = fovTargetY - perpY * fovHalfWidth;

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
    screenPts.push(isoProject(fovLeftX, fovLeftY, goz));
    screenPts.push(isoProject(fovRightX, fovRightY, goz));
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

    // FOV cone lines and horizontal width line on grid plane
    var fovLSvg = toSvg(fovLeftX, fovLeftY, goz);
    var fovRSvg = toSvg(fovRightX, fovRightY, goz);
    document.getElementById('cp-fov-left').setAttribute('x1', camTop[0]);
    document.getElementById('cp-fov-left').setAttribute('y1', camTop[1]);
    document.getElementById('cp-fov-left').setAttribute('x2', fovLSvg[0]);
    document.getElementById('cp-fov-left').setAttribute('y2', fovLSvg[1]);
    document.getElementById('cp-fov-right').setAttribute('x1', camTop[0]);
    document.getElementById('cp-fov-right').setAttribute('y1', camTop[1]);
    document.getElementById('cp-fov-right').setAttribute('x2', fovRSvg[0]);
    document.getElementById('cp-fov-right').setAttribute('y2', fovRSvg[1]);
    // Horizontal FOV width line at floor level
    document.getElementById('cp-fov-line').setAttribute('x1', fovLSvg[0]);
    document.getElementById('cp-fov-line').setAttribute('y1', fovLSvg[1]);
    document.getElementById('cp-fov-line').setAttribute('x2', fovRSvg[0]);
    document.getElementById('cp-fov-line').setAttribute('y2', fovRSvg[1]);
    // FOV label – left of the lens
    var fovLabel = document.getElementById('cp-fov-label');
    fovLabel.setAttribute('x', camTop[0] - camSize - 6);
    fovLabel.setAttribute('y', camTop[1] + 4);
    fovLabel.setAttribute('text-anchor', 'end');
    fovLabel.textContent = 'FOV ' + fov.toFixed(0) + '\u00b0';

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

  window.loadSnapshot = function() {
    fetch('/api/video/snapshot/full').then(function(r) {
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
        showSnapshotOnCurrentStep(nextUrl);
        projectAndOverlay();
        revokeSnapshotUrl(previousUrl);
      };
      img.onerror = function() {
        revokeSnapshotUrl(nextUrl);
        showNoFeed();
      };
      img.src = nextUrl;
    }).catch(function() {
      showNoFeed();
    });
  };

  function setPreviewVisibility(visible) {
    var pairs = [
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
        toggle.title = '{{_("Load a snapshot first")}}';
      }
      setFineZoomMode(false);
    } else {
      updateFineZoomToggleEnabled();
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
    toggle.title = ready ? '' : '{{_("Load a snapshot first")}}';
  }

  function showNoFeed() {
    setPreviewVisibility(false);
  }

  function showSnapshotOnCurrentStep(url) {
    document.getElementById('coarse-image').src = url;
    document.getElementById('fine-image').src = url;
    document.getElementById('review-image').src = url;
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
  var PROJECTION_STATUS_STEPS = {
    4: { status: 'coarse-status', container: 'coarse-container' },
    5: { status: 'fine-status', container: 'fine-container' },
    6: { status: 'review-status', container: 'review-container' },
  };

  function currentProjectionEls() {
    return PROJECTION_STATUS_STEPS[currentStep] || PROJECTION_STATUS_STEPS[5];
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

    // Quad points
    var quadPts = c.DSL[0]+','+c.DSL[1]+' '+c.DSR[0]+','+c.DSR[1]+' '+c.USR[0]+','+c.USR[1]+' '+c.USL[0]+','+c.USL[1];
    ['coarse-quad', 'fine-quad', 'review-quad'].forEach(function(id) {
      document.getElementById(id).setAttribute('points', quadPts);
    });

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
    g.innerHTML = '';
    var names = CORNER_NAMES;
    var keys = ['DSL', 'DSR', 'USR', 'USL'];
    for (var i = 0; i < 4; i++) {
      var pos = corners[keys[i]];
      var group = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      group.setAttribute('transform', 'translate('+pos[0]+','+pos[1]+')');

      if (draggable) {
        group.classList.add('handle');
        group.setAttribute('tabindex', '0');
        group.dataset.corner = keys[i];
        group.dataset.idx = i;
        // Enlarged hit area
        var hitArea = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        hitArea.setAttribute('x', '-22');
        hitArea.setAttribute('y', '-22');
        hitArea.setAttribute('width', '44');
        hitArea.setAttribute('height', '44');
        hitArea.setAttribute('fill', 'transparent');
        group.appendChild(hitArea);
        // Focus ring
        var ring = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        ring.setAttribute('r', '14');
        ring.classList.add('handle-ring');
        group.appendChild(ring);
      }

      var circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      circle.setAttribute('r', '7');
      circle.setAttribute('fill', 'rgba(255,188,0,0.8)');
      circle.setAttribute('stroke', '#ffbc00');
      circle.setAttribute('stroke-width', '1.5');
      group.appendChild(circle);

      var label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      label.setAttribute('x', '12');
      label.setAttribute('y', '4');
      label.setAttribute('fill', 'rgba(247,245,233,0.8)');
      label.setAttribute('font-size', '11');
      label.setAttribute('font-weight', '600');
      label.textContent = names[i];
      group.appendChild(label);

      g.appendChild(group);
    }

    if (draggable) {
      setupCornerDragging(g);
    }
  }

  function renderRefMarker(containerId, pos, draggable) {
    var g = document.getElementById(containerId);
    g.innerHTML = '';
    var group = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    group.setAttribute('transform', 'translate('+pos[0]+','+pos[1]+')');

    if (draggable) {
      group.classList.add('handle');
      group.setAttribute('tabindex', '0');
      group.dataset.refHandle = '1';
      // Enlarged hit area
      var hitArea = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      hitArea.setAttribute('x', '-22');
      hitArea.setAttribute('y', '-22');
      hitArea.setAttribute('width', '44');
      hitArea.setAttribute('height', '44');
      hitArea.setAttribute('fill', 'transparent');
      group.appendChild(hitArea);
      // Focus ring
      var ring = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      ring.setAttribute('r', '16');
      ring.classList.add('handle-ring');
      group.appendChild(ring);
    }

    // Crosshair
    var l1 = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    l1.setAttribute('x1', '-10'); l1.setAttribute('y1', '0');
    l1.setAttribute('x2', '10'); l1.setAttribute('y2', '0');
    l1.setAttribute('stroke', '#ffbc00'); l1.setAttribute('stroke-width', '2');
    group.appendChild(l1);
    var l2 = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    l2.setAttribute('x1', '0'); l2.setAttribute('y1', '-10');
    l2.setAttribute('x2', '0'); l2.setAttribute('y2', '10');
    l2.setAttribute('stroke', '#ffbc00'); l2.setAttribute('stroke-width', '2');
    group.appendChild(l2);
    var c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    c.setAttribute('r', '8');
    c.setAttribute('fill', 'none');
    c.setAttribute('stroke', '#ffbc00');
    c.setAttribute('stroke-width', '1.5');
    group.appendChild(c);

    g.appendChild(group);

    if (draggable) {
      setupRefDragging(group);
    }
  }

  // ---------------------------------------------------------------
  // Reference Point Dragging (Step 6 - Coarse Calibration)
  // ---------------------------------------------------------------
  function setupRefDragging(handle) {
    var svg = document.getElementById('coarse-overlay');
    var dragging = false;
    var startScreen = null;
    var startPos = null;
    var startCornerPositions = null;

    function getPos() {
      var t = handle.getAttribute('transform');
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
      e.preventDefault();
      dragging = true;
      var cx = e.touches ? e.touches[0].clientX : e.clientX;
      var cy = e.touches ? e.touches[0].clientY : e.clientY;
      startScreen = svgPoint(cx, cy);
      startPos = getPos();
      startCornerPositions = {};
      CORNER_NAMES.forEach(function(k) {
        startCornerPositions[k] = cornerPositions[k].slice();
      });
      handle.style.cursor = 'grabbing';
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
      handle.setAttribute('transform', 'translate('+nx+','+ny+')');
      // Move all corner markers and quad along with the ref point
      var corners = document.getElementById('coarse-corners').querySelectorAll('g');
      var keys = CORNER_NAMES;
      var quadParts = [];
      corners.forEach(function(g, i) {
        var cp = startCornerPositions[keys[i]];
        var cnx = cp[0] + dx;
        var cny = cp[1] + dy;
        g.setAttribute('transform', 'translate('+cnx+','+cny+')');
        quadParts.push(cnx+','+cny);
      });
      document.getElementById('coarse-quad').setAttribute('points', quadParts.join(' '));
      // Move z-offset group along with everything else
      document.getElementById('coarse-zoff').setAttribute('transform', 'translate('+dx+','+dy+')');
    }

    function onUp() {
      if (!dragging) return;
      dragging = false;
      handle.style.cursor = '';
      document.getElementById('coarse-zoff').removeAttribute('transform');
      applyCoarseOffset(startPos, getPos(), startCornerPositions);
    }

    handle.addEventListener('mousedown', onDown);
    handle.addEventListener('touchstart', onDown, { passive: false });
    window.addEventListener('mousemove', onMove);
    window.addEventListener('touchmove', onMove, { passive: false });
    window.addEventListener('mouseup', onUp);
    window.addEventListener('touchend', onUp);

    // Keyboard arrow support
    handle.addEventListener('keydown', function(e) {
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
        // Visually move corners along with the ref point
        var dx = pos[0] - startPos[0];
        var dy = pos[1] - startPos[1];
        var corners = document.getElementById('coarse-corners').querySelectorAll('g');
        var quadParts = [];
        corners.forEach(function(g, i) {
          var cp = startCornerPositions[CORNER_NAMES[i]];
          var cnx = cp[0] + dx;
          var cny = cp[1] + dy;
          g.setAttribute('transform', 'translate('+cnx+','+cny+')');
          quadParts.push(cnx+','+cny);
        });
        document.getElementById('coarse-quad').setAttribute('points', quadParts.join(' '));
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
    // Initialize startPos for keyboard on focus
    handle.addEventListener('focus', function() {
      startPos = getPos();
      startCornerPositions = {};
      CORNER_NAMES.forEach(function(k) {
        startCornerPositions[k] = cornerPositions[k].slice();
      });
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

    var state = getState();
    var g = state.grid;
    var hw = g.width / 2, hd = g.depth / 2;
    var ox = g.x_offset, oy = g.y_offset, oz = g.z_offset;
    // Stage convention: +X is stage left, so DSL/USL are at +hw and DSR/USR
    // at -hw. Each row pairs with the same-named screen corner below.
    var worldCorners = [
      [ox + hw, oy - hd, oz],
      [ox - hw, oy - hd, oz],
      [ox - hw, oy + hd, oz],
      [ox + hw, oy + hd, oz],
    ];
    var screenCorners = [
      cornerPositions.DSL,
      cornerPositions.DSR,
      cornerPositions.USR,
      cornerPositions.USL,
    ];

    fetch('/api/wizard/solve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        world_corners: worldCorners,
        screen_corners: screenCorners,
        image_width: imageWidth,
        image_height: imageHeight,
      }),
    }).then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.error) return;
      // Back-propagate solved camera to form fields
      wizWriteLen('cam_pos_x', data.camera.pos_x);
      wizWriteLen('cam_pos_y', data.camera.pos_y);
      wizWriteLen('cam_pos_z', data.camera.pos_z);
      document.getElementById('cam_pitch').value = data.camera.pitch;
      document.getElementById('cam_yaw').value = data.camera.yaw;
      document.getElementById('cam_roll').value = data.camera.roll;
      document.getElementById('cam_fov').value = data.camera.fov;
      saveToSession();
      projectAndOverlay();
    });
  }

  window.resetCoarseCalibration = function() {
    if (preCoarseCamera) {
      wizWriteLen('cam_pos_x', preCoarseCamera.pos_x);
      wizWriteLen('cam_pos_y', preCoarseCamera.pos_y);
      wizWriteLen('cam_pos_z', preCoarseCamera.pos_z);
      document.getElementById('cam_pitch').value = preCoarseCamera.pitch;
      document.getElementById('cam_yaw').value = preCoarseCamera.yaw;
      document.getElementById('cam_roll').value = preCoarseCamera.roll;
      document.getElementById('cam_fov').value = preCoarseCamera.fov;
    }
    saveToSession();
    projectAndOverlay();
  };

  // ---------------------------------------------------------------
  // Corner Dragging (Step 7 - Fine Calibration)
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
      updateFineQuad();
      // Update fine-zoom boxes as operator drags in full view.
      // Moved corner's box + neighbours' edges stay consistent.
      refreshFineZoomNeighbourhood(dragging.corner);
    }

    window.addEventListener('mouseup', function() {
      if (!dragging) return;
      dragging.handle.style.cursor = '';
      dragging = null;
      solveFromCorners();
    });
    window.addEventListener('touchend', function() {
      if (!dragging) return;
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
    var pts = cornerPositions;
    var quadPts = pts.DSL[0]+','+pts.DSL[1]+' '+pts.DSR[0]+','+pts.DSR[1]+' '+pts.USR[0]+','+pts.USR[1]+' '+pts.USL[0]+','+pts.USL[1];
    document.getElementById('fine-quad').setAttribute('points', quadPts);
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
      var n = cornerPositions[neighbour];
      var line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('x1', here[0]);
      line.setAttribute('y1', here[1]);
      line.setAttribute('x2', n[0]);
      line.setAttribute('y2', n[1]);
      line.setAttribute('stroke', 'rgba(255,188,0,0.7)');
      line.setAttribute('stroke-width', '2');
      line.setAttribute('vector-effect', 'non-scaling-stroke');
      edgesG.appendChild(line);
    });

    // Centre marker – same visual style as the full-view handle but
    // sized in CSS pixels (non-scaling) so 4× zoom doesn't bloat it.
    var markerG = svg.querySelector('[data-fine-zoom-marker]');
    markerG.innerHTML = '';
    var dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    dot.setAttribute('cx', here[0]);
    dot.setAttribute('cy', here[1]);
    dot.setAttribute('r', '7');
    dot.setAttribute('fill', 'rgba(255,188,0,0.85)');
    dot.setAttribute('stroke', '#ffbc00');
    dot.setAttribute('stroke-width', '1.5');
    dot.setAttribute('vector-effect', 'non-scaling-stroke');
    markerG.appendChild(dot);
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
      toggle.textContent = fineZoomMode ? '{{_("Show full image")}}' : '{{_("Fine adjust")}}';
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
    if (preCornerPinningCamera === null) {
      preCornerPinningCamera = getState().camera;
    }

    var state = getState();
    var g = state.grid;
    var hw = g.width / 2, hd = g.depth / 2;
    var ox = g.x_offset, oy = g.y_offset, oz = g.z_offset;
    // Stage convention: +X is stage left, so DSL/USL are at +hw and DSR/USR
    // at -hw. Each row pairs with the same-named screen corner below.
    var worldCorners = [
      [ox + hw, oy - hd, oz],
      [ox - hw, oy - hd, oz],
      [ox - hw, oy + hd, oz],
      [ox + hw, oy + hd, oz],
    ];

    var screenCorners = [
      cornerPositions.DSL,
      cornerPositions.DSR,
      cornerPositions.USR,
      cornerPositions.USL,
    ];

    fetch('/api/wizard/solve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        world_corners: worldCorners,
        screen_corners: screenCorners,
        image_width: imageWidth,
        image_height: imageHeight,
      }),
    }).then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.error) {
        showSolveStatus(data.error, false);
        return;
      }

      solvedCamera = data.camera;
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

      // Back-propagate to camera form fields
      wizWriteLen('cam_pos_x', data.camera.pos_x);
      wizWriteLen('cam_pos_y', data.camera.pos_y);
      wizWriteLen('cam_pos_z', data.camera.pos_z);
      document.getElementById('cam_pitch').value = data.camera.pitch;
      document.getElementById('cam_yaw').value = data.camera.yaw;
      document.getElementById('cam_roll').value = data.camera.roll;
      document.getElementById('cam_fov').value = data.camera.fov;
      saveToSession();
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
    solvedCamera = null;
    document.getElementById('fine-status').style.display = 'none';
    document.getElementById('fine-solved-params').style.display = 'none';
    document.getElementById('fine-container').classList.remove('valid', 'invalid');
    // Restore the camera form from the pre-solve snapshot so the
    // re-projection produces corners at the operator's pre-pinning
    // positions (not the dragged-to ones, since solveFromCorners
    // writes its result back into the form on every successful
    // solve). Without this, Reset would re-project from the post-
    // solve camera and the corners would visibly stay where they
    // were – which is what the operator originally reported as
    // "reset is broken after a corner is moved". Clearing the
    // snapshot lets the next solve session start fresh.
    if (preCornerPinningCamera) {
      wizWriteLen('cam_pos_x', preCornerPinningCamera.pos_x);
      wizWriteLen('cam_pos_y', preCornerPinningCamera.pos_y);
      wizWriteLen('cam_pos_z', preCornerPinningCamera.pos_z);
      document.getElementById('cam_pitch').value = preCornerPinningCamera.pitch;
      document.getElementById('cam_yaw').value = preCornerPinningCamera.yaw;
      document.getElementById('cam_roll').value = preCornerPinningCamera.roll;
      document.getElementById('cam_fov').value = preCornerPinningCamera.fov;
      preCornerPinningCamera = null;
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
    var cam = solvedCamera || state.camera;
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

    // Re-project with final values for the review overlay
    if (imageWidth && imageHeight) {
      fetch('/api/wizard/project', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          camera: cam,
          grid: g,
          image_width: imageWidth,
          image_height: imageHeight,
        }),
      }).then(function(r) { return r.json(); })
      .then(function(data) {
        var vb = '0 0 ' + imageWidth + ' ' + imageHeight;
        document.getElementById('review-overlay').setAttribute('viewBox', vb);
        var c = data.corners;
        var quadPts = c.DSL[0]+','+c.DSL[1]+' '+c.DSR[0]+','+c.DSR[1]+' '+c.USR[0]+','+c.USR[1]+' '+c.USL[0]+','+c.USL[1];
        document.getElementById('review-quad').setAttribute('points', quadPts);
        renderCornerMarkers('review-corners', c, false);
        renderRefMarker('review-ref', data.reference, false);
        var zg = document.getElementById('review-zoff');
        zg.innerHTML = '';
        if (data.reference_elevated && data.z_offset) {
          renderZOffsetLine(zg, data.reference, data.reference_elevated, data.z_offset);
        }
      });
    }
  }

  window.applyAndFinish = function() {
    var badLen = invalidLengthFields();
    if (badLen.length) {
      alert('These fields aren\'t valid lengths – fix them before finishing: ' + badLen.join(', '));
      return;
    }
    var state = getState();
    var camData = solvedCamera || state.camera;
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
    ]).then(function(responses) {
      var labeled = [
        { name: 'camera', response: responses[0] },
        { name: 'grid', response: responses[1] },
      ];
      return Promise.all(labeled.map(function(entry) {
        if (entry.response.ok) return null;
        return entry.response.text().then(function(body) {
          var detail = body ? ': ' + body : '';
          throw new Error('Failed to save ' + entry.name + ' configuration (' + entry.response.status + ')' + detail);
        });
      }));
    }).then(function() {
      try { sessionStorage.removeItem(STORAGE_KEY); } catch(e) {}
      window.location.href = '/';
    }).catch(function(err) {
      var message = err && err.message ? err.message : 'Failed to save configuration. Please try again.';
      alert(message);
    });
  };

  window.discardAndLeave = function() {
    try { sessionStorage.removeItem(STORAGE_KEY); } catch(e) {}
    window.location.href = '/';
  };

  // ---------------------------------------------------------------
  // Video Source (Step 2)
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

    fetch('/section/video_source', { method: 'POST', body: formData })
    .then(function(r) {
      if (!r.ok) throw new Error('Save failed');
      var msgEl = document.getElementById('wizard-video-saved');
      msgEl.textContent = 'Video source saved.';
      msgEl.style.display = 'block';
    }).catch(function() {
      alert('Failed to save video source. Please try again.');
    });
  };

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
  preCoarseCamera = getState().camera;
  updatePrepIllustration();
  updateGridIllustration();
  updateCamIllustration();
  // Bind drag handlers on the four fine-zoom boxes once
  // at page load. The SVG elements outlive every re-render (we only
  // mutate their inner <g> children), so a one-time wiring is enough
  // and cheaper than re-attaching per render.
  setupFineZoomDragging();
  wizardGo(restored ? currentStep : 0);
})();
</script>
