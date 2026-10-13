% import json
% from openfollow.units import UnitSystem, format_length, metric_echo, unit_suffix_length
% _us = UnitSystem(config.ui.unit_system)
% _imp = _us is UnitSystem.IMPERIAL
% _len = unit_suffix_length(_us)
% _gg = config.ground_grid
<form id="ground-grid-section" class="section experimental-feature {{'saved' if defined('saved') and saved else ''}}" data-fold-key="ground_grid" data-help="ground_grid"
      hx-post="/section/ground_grid" hx-target="#ground-grid-section" hx-swap="outerHTML" hx-trigger="submit">
    <div class="section-head">
        <h2>Ground Grid <span class="badge-experimental">Experimental</span></h2>
        <span class="section-note">A second floor at its own height</span>
    </div>

    <div class="group">
        <div class="row">
            <div class="field checkbox-field">
                <label for="ground-grid-enabled">Ground grid</label>
                <div class="checkbox-wrap checkbox-field inline">
                    <input type="checkbox" id="ground-grid-enabled" name="enabled" {{'checked' if _gg.enabled else ''}}>
                    <label for="ground-grid-enabled">Drop markers inside it onto its height</label>
                </div>
            </div>
            <div class="field checkbox-field">
                <label for="ground-grid-visible">Display</label>
                <div class="checkbox-wrap checkbox-field inline">
                    <input type="checkbox" id="ground-grid-visible" name="visible" {{'checked' if _gg.visible else ''}}>
                    <label for="ground-grid-visible">Show it on the Operator Screen</label>
                </div>
            </div>
            <div class="field">
                <label for="ground-grid-z-offset">Height ({{_len}})</label>
                <input id="ground-grid-z-offset" type="{{'text' if _imp else 'number'}}" name="z_offset" value="{{format_length(_gg.z_offset, _us) if _imp else _gg.z_offset}}" step="any"
                       hx-get="/api/validate/ground_grid/z_offset" hx-trigger="blur changed delay:200ms"
                       hx-target="#ground-grid-z-offset-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="ground-grid-z-offset-error" aria-invalid="false">
                <span id="ground-grid-z-offset-error" class="field-error"></span>
                % if _imp:
                <small class="metric-echo">Stored: {{metric_echo(_gg.z_offset)}}</small>
                % end
            </div>
        </div>
    </div>

    <div class="group">
        <h3 class="group-title">Area</h3>
        <div class="gg-plan-wrap" style="position:relative;border:1px solid var(--border,#444);border-radius:6px;background:#111;overflow:hidden;width:100%;">
            <canvas id="ground-grid-canvas" width="1280" height="720" style="display:block;width:100%;height:auto;background:#111;cursor:crosshair;"></canvas>
        </div>
        <div style="display:flex;gap:0.5rem;margin-top:0.5rem;flex-wrap:wrap;">
            <button type="button" id="ground-grid-draw-btn" class="secondary">Draw polygon</button>
            <button type="button" id="ground-grid-finish-btn" class="secondary" disabled>Finish polygon</button>
            <button type="button" id="ground-grid-cancel-btn" class="secondary" disabled>Cancel drawing</button>
            <button type="button" id="ground-grid-clear-btn" class="danger">Clear polygon</button>
            <span id="ground-grid-status" class="section-note"></span>
        </div>
    </div>

    <input type="hidden" id="ground-grid-vertices" name="vertices" value="{{json.dumps(_gg.vertices)}}">

    <div class="actions">
        <button type="submit" class="save-btn">Save</button>
        <button type="button" class="broadcast-btn" onclick="broadcastSection('ground_grid', this.form)">Apply to all stations</button>
    </div>

<script>
(function() {
    // Runs again on every save (the form is swapped): the plan binds the new canvas.
    var canvas = document.getElementById('ground-grid-canvas');
    var field = document.getElementById('ground-grid-vertices');
    if (!canvas || !field) return;
    var statusEl = document.getElementById('ground-grid-status');
    var drawBtn = document.getElementById('ground-grid-draw-btn');
    var finishBtn = document.getElementById('ground-grid-finish-btn');
    var cancelBtn = document.getElementById('ground-grid-cancel-btn');
    var clearBtn = document.getElementById('ground-grid-clear-btn');
    var verts = [];
    try { verts = JSON.parse(field.value) || []; } catch (e) { verts = []; }
    var changed = false;

    function store() {
        field.value = JSON.stringify(verts);
        changed = true;
    }

    var plan = new window.OpenFollow.PolygonPlan(canvas, {
        polygons: function() { return [verts]; },
        editable: function() { return verts.length ? verts : null; },
        drawPolygons: function(p) {
            if (verts.length < 2) return;
            var ctx = p.ctx;
            p.tracePolygon(verts);
            ctx.fillStyle = 'rgba(119,204,255,0.15)';
            if (verts.length >= 3) ctx.fill();
            ctx.strokeStyle = 'rgba(119,204,255,0.9)';
            ctx.lineWidth = 2;
            ctx.stroke();
            p.drawHandles(verts, '#7cf');
        },
        onDragEnd: store,
        onDraftClose: function(drawn) { verts = drawn; store(); },
        onRender: function() {
            var drawing = !!plan.draft;
            finishBtn.disabled = !(drawing && plan.draft.vertices.length >= 3);
            cancelBtn.disabled = !drawing;
            drawBtn.disabled = drawing;
            clearBtn.disabled = drawing || verts.length === 0;
            if (drawing) {
                statusEl.textContent = 'Drawing: ' + plan.draft.vertices.length + ' point(s). Click the first point or double-click to close.';
            } else if (changed) {
                statusEl.textContent = 'Polygon changed. Save to keep it.';
            } else {
                statusEl.textContent = verts.length >= 3 ? '' : 'No polygon yet.';
            }
        },
    });

    drawBtn.addEventListener('click', function() { plan.startDraft(); });
    finishBtn.addEventListener('click', function() { plan.finishDraft(); });
    cancelBtn.addEventListener('click', function() { plan.cancelDraft(); });
    clearBtn.addEventListener('click', function() { verts = []; store(); plan.render(); });

    // Grid and live markers from the zones endpoint, while the plan is on screen.
    function visible() {
        return !document.hidden && document.body.contains(canvas) && canvas.getClientRects().length > 0;
    }
    function refresh() {
        if (!visible()) return;
        fetch('/api/zones', {cache: 'no-store'})
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(data) {
                if (!data) return;
                if (data.grid) plan.grid = data.grid;
                plan.markers = data.markers || [];
                plan.render();
            })
            .catch(function() {});
    }
    plan.grid = {{!json.dumps({k: getattr(config.grid, k) for k in ("width", "depth", "spacing", "x_offset", "y_offset")})}};
    plan.render();
    refresh();
    if (window.__groundGridTimer) clearInterval(window.__groundGridTimer);
    window.__groundGridTimer = setInterval(function() {
        if (!document.body.contains(canvas)) {
            clearInterval(window.__groundGridTimer);
            window.__groundGridTimer = null;
            return;
        }
        refresh();
    }, 1000);
    document.addEventListener('visibilitychange', refresh);
})();
</script>
</form>
