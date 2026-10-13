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
        <div class="seg-toggle plan-view-switch" id="ground-grid-view-switch" role="radiogroup" aria-label="Ground grid view">
            <label class="seg-option">
                <input type="radio" name="ground_grid_view" value="plan" checked>
                <span><strong>Top-down</strong></span>
            </label>
            <label class="seg-option">
                <input type="radio" name="ground_grid_view" value="camera">
                <span><strong>Camera</strong></span>
            </label>
        </div>
        <div class="pv-stage" id="ground-grid-picture" hidden>
            <img alt="Camera picture with the ground grid">
            <svg viewBox="0 0 1280 720" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg"></svg>
        </div>
        <div class="pv-nofeed" id="ground-grid-picture-nofeed" hidden>No video picture.</div>
        <div class="gg-plan-wrap" id="ground-grid-plan-wrap" style="position:relative;border:1px solid var(--border,#444);border-radius:6px;background:#111;overflow:hidden;width:100%;">
            <canvas id="ground-grid-canvas" width="1280" height="720" style="display:block;width:100%;height:auto;background:#111;cursor:crosshair;"></canvas>
        </div>
        <div style="display:flex;gap:0.5rem;margin-top:0.5rem;flex-wrap:wrap;">
            <button type="button" id="ground-grid-draw-btn" class="secondary">Draw polygon</button>
            <button type="button" id="ground-grid-finish-btn" class="secondary" disabled>Finish polygon</button>
            <button type="button" id="ground-grid-cancel-btn" class="secondary" disabled>Cancel drawing</button>
            <button type="button" id="ground-grid-clear-btn" class="danger">Clear polygon</button>
            <button type="button" id="ground-grid-refresh-btn" class="secondary" hidden>Refresh Image</button>
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
            drawBtn.disabled = drawing || mode === 'camera';
            clearBtn.disabled = drawing || verts.length === 0;
            if (drawing) {
                statusEl.textContent = 'Drawing: ' + plan.draft.vertices.length + ' point(s). Click the first point or double-click to close.';
            } else if (changed) {
                statusEl.textContent = 'Polygon changed. Save to keep it.';
            } else {
                statusEl.textContent = verts.length >= 3 ? '' : 'No polygon yet.';
            }
            projectGround();
        },
    });

    // -- camera view: the ground grid at the Height typed in, as the saved camera sees it ----
    var form = document.getElementById('ground-grid-section');
    var pictureEl = document.getElementById('ground-grid-picture');
    var nofeedEl = document.getElementById('ground-grid-picture-nofeed');
    var planWrap = document.getElementById('ground-grid-plan-wrap');
    var refreshBtn = document.getElementById('ground-grid-refresh-btn');
    var heightInput = document.getElementById('ground-grid-z-offset');
    var savedZ = {{_gg.z_offset}};
    var pv = new window.OpenFollow.PictureView({
        stage: pictureEl, img: pictureEl.querySelector('img'), svg: pictureEl.querySelector('svg'), nofeed: nofeedEl,
        onSize: function() { viewKey = ''; projectGround(); },
    });
    var mode = 'plan', view = null, viewKey = '', viewSeq = 0;

    function height() {
        var units = window.OpenFollow.units;
        var v = units.isImperial() ? units.parseLength(heightInput.value) : parseFloat(heightInput.value);
        return isFinite(v) ? v : savedZ;
    }

    function projectGround() {
        if (mode !== 'camera') return;
        var z = height();
        var key = JSON.stringify([verts, z, pv.w, pv.h]);
        if (key === viewKey) return;
        viewKey = key;
        var asked = ++viewSeq;
        window.OpenFollow.stationApi('POST', '/api/ground_grid/view', { w: pv.w, h: pv.h, vertices: verts, z: z })
            .then(function(json) {
                if (asked !== viewSeq) return;
                view = json;
                renderPicture();
            }, function() { viewKey = ''; });
    }

    function renderPicture() {
        pv.clear();
        if (!view) return;
        view.lines.forEach(function(points) {
            pv.polylines(points).forEach(function(run) {
                pv.svg.appendChild(pv.el('polyline', { points: run, 'class': 'pv-grid-line', 'vector-effect': 'non-scaling-stroke' }));
            });
        });
        pv.polylines(view.outline).forEach(function(run) {
            pv.svg.appendChild(pv.el('polyline', { points: run, 'class': 'pv-outline', 'vector-effect': 'non-scaling-stroke' }));
        });
        view.corners.forEach(function(pt, i) {
            if (pt) pv.svg.appendChild(pv.handle(pt, 'pv-handle', function() { return grab(i); }));
        });
    }

    // A corner dropped in the picture lands on the ground grid's own plane.
    function grab(i) {
        if (!verts[i]) return null;
        var z = height();
        return function(at) {
            pv.onStage(at, z).then(function(w) {
                if (!verts[i]) return;
                verts[i] = [w.x, w.y];
                store();
                plan.render();
            }, function(info) {
                window.OpenFollow.saveError.show(form, info, 'Not moved.');
            });
        };
    }

    function applyMode(next) {
        mode = next;
        var camera = mode === 'camera';
        planWrap.hidden = camera;
        refreshBtn.hidden = !camera;
        if (camera) {
            if (plan.draft) plan.cancelDraft();
            viewKey = '';
            pv.load();
        } else {
            pictureEl.hidden = true;
            nofeedEl.hidden = true;
        }
        plan.render();
    }
    refreshBtn.addEventListener('click', function() { pv.load(); });
    heightInput.addEventListener('input', projectGround);

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
    window.OpenFollow.viewSwitch(document.getElementById('ground-grid-view-switch'), 'ground_grid', applyMode);
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
